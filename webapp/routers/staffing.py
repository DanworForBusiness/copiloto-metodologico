"""
staffing.py — Operations / Staffing HTTP.

Rutas canónicas (batch Shared Workspace Shell + Operaciones Structure):
`GET /operaciones/staffing/asignaciones` (shell autenticado, sin Graph) y
`POST /operaciones/staffing/asignaciones/data` (recibe `graph_token`
explícito del browser, delega en `StaffingService`, devuelve un
fragmento HTML para HTMX). El nombre refleja la jerarquía real: workspace
Operaciones → área Staffing → capacidad Asignaciones.

`POST /operaciones/staffing/asignaciones/historial` (Staffing Director
Priority V2, ver HANDOFF) es la ÚNICA ruta nueva del batch — carga lazy del
historial Inactivo, JSON (no HTML), ver docstring de `staffing_historial`
para el motivo. Route surface 82→83, documentado explícitamente.

`POST /operaciones/staffing/asignaciones/ausencias` (Staffing Approved
Absence Overlay, ver HANDOFF — generalización del Vacation Timeline
Overlay original: reemplazo one-for-one de `.../vacaciones`, endpoint
interno sin contrato externo, `include_in_schema=False`, sin alias de
compatibilidad — route surface se mantiene 84), MISMO patrón que
`staffing_historial`: JSON, carga LAZY e INDEPENDIENTE de `staffing_data`
(las asignaciones renderizan primero; el overlay de ausencias llega
después, sin bloquear nada), mismo `graph_token` explícito por form, misma
gate de sesión/autorización. Ausencias es ADITIVO/presentacional — una
falla acá nunca degrada Asignaciones (ver `staffing_ausencias`).

`GET /operations/staffing` y `POST /operations/staffing/data` (nombres
históricos en inglés, de Batch 3 de aceleración) quedan como alias de
COMPATIBILIDAD — SIEMPRE 307 a la ruta canónica correspondiente, method y
body preservados (307, no 302/303). Nunca vuelven a implementar la lógica
de la página/fragmento — un solo punto de verdad evita que ambos
diverjan.

El token Graph delegado lo resuelve la AUTORIDAD compartida del Hub
(`dwGraphToken`, webapp/static/hub_auth.js) — el cliente ya no lee el
storage de credenciales, y el fragmento htmx sólo DECLARA que necesita
Graph (`data-dw-graph-token`): el parámetro lo inyecta el hook transversal
tras esperar a la autoridad;
este router NUNCA lo posee, lo persiste ni lo loguea. `GET` no requiere
`graph_token` ni llama a Graph — el token del browser no está disponible en
un GET server-side normal (arquitectura preservada de Batch 1: el
transporte compartido no posee tokens).

Clasificación: OPERATIONS / STAFFING HTTP. NOT PLATFORM / SHARED. NO
importa `webapp.main`, no construye URLs de Graph, no posee el mapeo de
campos, no posee transporte Graph, no realiza cálculos de negocio — todo
eso vive en `StaffingService`/`StaffingRepository`/`core.microsoft365`
(Batch 1/2). Consume `webapp.operations_access.is_operations_authorized`
(allowlist explícita `OPERATIONS_ALLOWED_USERS` del workspace Operations,
separada de `webapp.access` — ver Operations Workspace Access Gate en
HANDOFF).

El deep-link no autenticado entra por `webapp.workspaces.platform_login_redirect`
(apunta a `/platform`, alias de compatibilidad que a su vez redirige a
`/onedanwor` — ver Batch A) — NO por `webapp.nav.login_redirect`
(Copilot-específico): Operaciones es un workspace distinto del Copiloto.

`_require_operations_session` distingue PÁGINA de ACCIÓN al armar `next`:
`staffing_page` (GET) no pasa `next_path` — `request.url.path` YA es la
página, corresponde tal cual. `staffing_data` (POST) pasa
`next_path="/operaciones/staffing/asignaciones"` EXPLÍCITO — sin sesión,
`next` debe apuntar a la PÁGINA, nunca al endpoint de datos que disparó
el redirect (el `graph_token` del POST original se descarta sin más:
nunca se lee, nunca se propaga a la URL de login — tras el login la
página real se re-inicializa y dispara su propio POST de HTMX fresco).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import hub_metrics_access, operations_access, same_origin_guard, workspaces
from webapp.services import auth_service, feedback_store
from webapp.services import timenotes_service, timenotes_send, timenotes_exceptions, notifications
from webapp.services.activity_service import log_activity, log_verbose
from webapp.services.activity_context import (
    SCOPE_OPERATIONS, AREA_STAFFING,
    EVENT_DATA_LOAD_FAILED, EVENT_DATA_LOAD_DIAGNOSTICS,
    EVENT_CONFIGURATION_INCOMPLETE, EVENT_TOKEN_ACQUISITION_FAILED,
)
# MODULE IDENTIFIER CONVENTION V2 — Fase 2: los productores de Activity de
# Staffing persisten el `module_id` canónico directo del registro (dueño
# único de la identidad de módulo) — ya NO reproducen el mapeo
# `assignments → OP01` acá ni importan el legacy `MODULE_ASSIGNMENTS` de
# `activity_context` (ese sigue existiendo solo para la normalización
# histórica interna de `activity_context`/`activity_analytics`).
from webapp.services.module_registry import OP01_MODULE_ID
from webapp.graph_auth_state import graph_error_state
from webapp.services.staffing_repository import AbsenceGraphError, AbsenceSchemaError
from webapp.services.staffing_service import StaffingService, build_dashboard_payload
from webapp.templating import templates

router = APIRouter()


def _flatten_staffing_diagnostics(diagnostics: dict) -> dict:
    """Aplana `StaffingRepository.last_diagnostics` (pre-PR corrective, ver
    HANDOFF) a kwargs planos para `log_verbose` — `{"asignaciones": {"rows":
    N, ...}}` → `{"asignaciones_rows": N, ...}`. NUNCA incluye token/payload/
    PII — el diccionario fuente ya es solo conteos/tiempos/booleanos (ver
    `StaffingRepository`)."""
    flat: dict = {}
    for source, stats in diagnostics.items():
        if source == "total_elapsed_ms":
            flat["total_elapsed_ms"] = stats
            continue
        for key, value in stats.items():
            flat[f"{source}_{key}"] = value
    return flat


# owner ticket "STAFFING — MSAL TOKEN FAILURE TELEMETRY": allowlist EXPLÍCITA
# y por-tipo de los ÚNICOS campos que el diagnóstico de adquisición de token
# (`hub_auth.js::dwGraphToken`) puede terminar en Activity Log. Nunca confía
# a ciegas en el JSON que manda el cliente — un campo desconocido, de tipo
# incorrecto, o el JSON entero siendo inválido/no-dict se descarta en
# silencio, nunca rompe el request ni termina logueado igual. Ningún campo de
# esta lista es una credencial/cookie/claim — ver hub_auth.js para qué SÍ
# captura el cliente.
_TOKEN_DIAGNOSTIC_FIELDS: dict[str, type] = {
    "stage": str,
    "msalErrorCode": str,
    "msalErrorName": str,
    "browserFamily": str,
    "platform": str,
    "cookieEnabled": bool,
    "sessionStorageAvailable": bool,
    "localStorageAvailable": bool,
    "isSecureContext": bool,
    "hadAccount": bool,
    "route": str,
}
# Cota defensiva de largo — ningún valor de esta allowlist es de negocio,
# así que nunca necesita más que esto; corta cualquier intento de abusar el
# campo para transportar algo más largo.
_TOKEN_DIAGNOSTIC_STRING_MAX_LEN = 64


def _safe_token_diagnostic(raw: str) -> dict:
    """JSON crudo del cliente -> SOLO los campos de `_TOKEN_DIAGNOSTIC_FIELDS`,
    con el tipo esperado. Fail-soft total: JSON inválido, no-dict, o
    cualquier excepción de parseo -> `{}` (el log_verbose de más abajo sigue
    llamándose igual, solo sin campos de diagnóstico, nunca bloquea la
    respuesta `missing_token`)."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    safe: dict = {}
    for field, expected_type in _TOKEN_DIAGNOSTIC_FIELDS.items():
        value = data.get(field)
        if value is None or not isinstance(value, expected_type):
            continue
        if expected_type is str:
            value = value[:_TOKEN_DIAGNOSTIC_STRING_MAX_LEN]
        safe[field] = value
    return safe


@router.post("/operaciones/staffing/feedback", include_in_schema=False)
def staffing_feedback(request: Request, rating: str = Form(""), comment: str = Form("")):
    """Feedback "¿Qué te pareció este reporte?" al cierre de Asignaciones
    (owner: la capacidad es TRANSVERSAL, no solo del tablero integral). Reusa
    el MISMO store compartido (`feedback_store` → FEEDBACK_LOG_DIR, campo
    `type`) y el MISMO pipeline de Activity que Reporting/Copilot — nunca un
    almacenamiento propio. Emite `staffing.feedback` (scope operations/area
    staffing), clasificado como `usage` para el dashboard de uso."""
    blocked = _require_operations_session(request)
    if blocked is not None:
        return blocked
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Feedback inválido")
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    payload = {
        "type": "operations_dashboard",
        "surface": "asignaciones",
        "rating": rating,
        "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
        "ts": now.isoformat(),
    }
    clean_comment = (comment or "").strip()
    if clean_comment:
        payload["comment"] = clean_comment
    feedback_store.write_feedback_record(payload, prefix="staffing_feedback", stamp=now.strftime("%Y%m%d_%H%M%S"))
    log_activity(
        "staffing.feedback", user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
        module=OP01_MODULE_ID, feedback_sentiment=("positive" if rating == "up" else "negative"),
    )
    return JSONResponse({"state": "ok"})


def _require_operations_session(request: Request, *, next_path: str | None = None):
    """Redirect (sin sesión) o 403 (sesión sin tier "full") — o None si OK.
    Compartido por GET y POST para no duplicar la regla de acceso.
    `next_path` explícito (solo lo pasa el POST de datos): ver docstring
    del módulo — sin sesión, `next` debe apuntar a la página, no al
    endpoint de acción que disparó el redirect."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Staffing")
    return None


@router.get("/operaciones/staffing/asignaciones", response_class=HTMLResponse, include_in_schema=False)
def staffing_page(request: Request):
    redirect = _require_operations_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    # Métricas de Uso v2 (fase 2): la VISITA a Asignaciones ya no la escribe el
    # servidor (`module_viewed`, antes `staffing_overview_viewed`); la lleva el
    # `page_view` del documento, atribuido a OP01 vía `dw_module_id` (abajo).
    return templates.TemplateResponse(
        request=request, name="operations/staffing.html",
        context={
            "current_workspace": workspaces.OPERATIONS,
            "available_workspaces": workspaces.available_workspaces(user),
            # Operaciones no tiene concepto de "pack" propio — la identidad
            # animada compartida (ver platform_topbar.html) es un asset físico
            # bajo content_packs/, así que se resuelve contra el pack por
            # defecto de la webapp (mismo mecanismo que ya usa Copiloto, sin
            # inventar un registro de packs para Operaciones).
            "pack": DEFAULT_PACK,
            # Misma sesión que ya usa Copiloto (modules.py/academy.py) para
            # la bienvenida del nav izquierdo (partials/workspace_shell.html)
            # — sin esto, operations_nav.html no puede renderizarla (ver
            # Shared Workspace Sidebar Frame en HANDOFF).
            "user": user,
            # Resalta "01 Asignaciones" en operations_nav.html (Reporting
            # Integral V0 — Align with Hub backbone corrective, ver HANDOFF).
            "active_page": "staffing",
            # Métricas de Uso v2: `page_view` de esta página = visita de OP01
            # (ver base.html window.DW_MODULE_ID / telemetry._log_page_view).
            "dw_module_id": OP01_MODULE_ID,
            # "03 Métricas de uso" en operations_nav.html (owner ticket "HUB
            # METRICS — CENTRALIZE ACCESS AND ENABLE BY VERTICAL" §6/§9): AMBAS
            # condiciones, ninguna sustituye a la otra — superficie habilitada
            # Y usuario autorizado. Ya pasamos `is_operations_authorized` para
            # llegar hasta acá; esto es un chequeo ADICIONAL, no un reemplazo.
            "can_hub_metrics": (
                hub_metrics_access.is_vertical_metrics_enabled("operations")
                and hub_metrics_access.can_access_hub_metrics(user)
            ),
        },
    )


@router.post("/operaciones/staffing/asignaciones/data", response_class=HTMLResponse, include_in_schema=False)
def staffing_data(request: Request, graph_token: str = Form(""), token_diagnostic: str = Form("")):
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect

    if not graph_token:
        # owner ticket "STAFFING — MSAL TOKEN FAILURE TELEMETRY": antes de
        # este ticket este branch no logueaba nada — cero visibilidad de POR
        # QUÉ el cliente nunca obtuvo el token (ver hub_auth.js::dwGraphToken).
        # `token_diagnostic` ya viene filtrado por `_safe_token_diagnostic`
        # (allowlist server-side, nunca confía en el cliente a ciegas).
        log_verbose(
            EVENT_TOKEN_ACQUISITION_FAILED, user=auth_service.session_user(request),
            scope=SCOPE_OPERATIONS, area=AREA_STAFFING, module=OP01_MODULE_ID,
            operation="initial", **_safe_token_diagnostic(token_diagnostic),
        )
        return templates.TemplateResponse(
            request=request, name="operations/staffing_dashboard.html", context={"state": "missing_token"},
        )

    user = auth_service.session_user(request)
    # cache_scope (SHARED SOURCE ACQUISITION corrective, ver HANDOFF) —
    # MISMA identidad estable de sesión que ya usa Reporting
    # (webapp/routers/reporting.py), NUNCA graph_token. Resuelto acá y
    # pasado explícito a StaffingService/StaffingRepository — nunca posee
    # el estado de cache.
    cache_scope = str((user or {}).get("username") or (user or {}).get("email") or (user or {}).get("sub") or "") or None
    service = StaffingService()
    try:
        rows = service.get_overview(graph_token, cache_scope=cache_scope)
    except RuntimeError:
        # Config de Staffing incompleta (site_id / field mapping) — ver Batch 2/3.
        log_verbose(
            EVENT_CONFIGURATION_INCOMPLETE, user=user, scope=SCOPE_OPERATIONS,
            area=AREA_STAFFING, module=OP01_MODULE_ID, operation="initial",
        )
        return templates.TemplateResponse(
            request=request, name="operations/staffing_dashboard.html", context={"state": "config_incomplete"},
        )
    except Exception as exc:  # noqa: BLE001 — falla de Graph/transporte, no de config
        # `data_load_failed`/`operation=initial` (corrective A2): reemplaza al
        # histórico `staffing_overview_error` — la FASE (carga inicial vs.
        # historial) queda en `payload.operation`, no en el nombre del evento.
        log_verbose(
            EVENT_DATA_LOAD_FAILED, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
            module=OP01_MODULE_ID, operation="initial", error_type=type(exc).__name__,
        )
        return templates.TemplateResponse(
            request=request, name="operations/staffing_dashboard.html", context={"state": "graph_error"},
        )

    # Diagnóstico (pre-PR corrective, ver HANDOFF: "measure the real 6
    # seconds") — SOLO en el camino exitoso, a propósito (mantener el
    # alcance acotado a lo pedido); rows/pages/elapsed_ms/filter_used/
    # fallback_used por fuente, nunca token/payload/PII. `data_load_diagnostics`/
    # `operation=initial` (corrective A2) reemplaza al histórico
    # `staffing_data_diagnostics`.
    log_verbose(
        EVENT_DATA_LOAD_DIAGNOSTICS, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
        module=OP01_MODULE_ID, operation="initial",
        **_flatten_staffing_diagnostics(getattr(service, "last_diagnostics", {})),
    )

    # Payload ÚNICO y normalizado (Asignaciones Dashboard v1) — el filtrado
    # de KPIs/timeline/disponibilidad/detalle ocurre 100% client-side (Alpine,
    # ver app.js::staffingDashboard) sobre este mismo array; no hay un
    # segundo endpoint de "datos filtrados" que vuelva a golpear Graph.
    payload = build_dashboard_payload(rows)
    return templates.TemplateResponse(
        request=request, name="operations/staffing_dashboard.html",
        context={"state": "ok", "payload": payload},
    )


@router.post("/operaciones/staffing/asignaciones/historial", include_in_schema=False)
def staffing_historial(request: Request, graph_token: str = Form(""), token_diagnostic: str = Form("")):
    """Carga LAZY del historial Inactivo (Staffing Director Priority V2, ver
    HANDOFF) — ruta NUEVA (route surface 82→83, cambio de contrato explícito
    y documentado, no oculto). Devuelve JSON (no HTML: `staffing_dashboard.html`
    solo se monta una vez en `#staffing-rows`; un segundo swap HTML
    remontaría Alpine y perdería todo el estado de filtros/UI vigente) —
    `app.js::staffingDashboard._loadInactiveHistory()` la llama UNA sola vez
    (primera vez que el usuario tilda Inactivo) y mergea `assignments` en el
    dataset ya cargado por assignment_id, nunca reemplaza el array completo
    de sus propias filas. Misma gate de sesión/autorización y mismo
    `graph_token` explícito por form que `staffing_data` — nunca un segundo
    mecanismo de auth."""
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect

    if not graph_token:
        # MISMO criterio que staffing_data (ver ahí) — owner ticket "STAFFING
        # — MSAL TOKEN FAILURE TELEMETRY".
        log_verbose(
            EVENT_TOKEN_ACQUISITION_FAILED, user=auth_service.session_user(request),
            scope=SCOPE_OPERATIONS, area=AREA_STAFFING, module=OP01_MODULE_ID,
            operation="history", **_safe_token_diagnostic(token_diagnostic),
        )
        return JSONResponse({"state": "missing_token"})

    user = auth_service.session_user(request)
    # cache_scope (SHARED SOURCE ACQUISITION corrective, ver HANDOFF) —
    # MISMA resolución que staffing_data: si Initial ya calentó Proyectos/
    # Colaboradores/Cliente en la MISMA sesión, Historial reusa ese estado
    # en vez de releerlos completos.
    cache_scope = str((user or {}).get("username") or (user or {}).get("email") or (user or {}).get("sub") or "") or None
    service = StaffingService()
    try:
        rows = service.get_overview(graph_token, assignment_scope="historical_inactive", cache_scope=cache_scope)
    except RuntimeError:
        log_verbose(
            EVENT_CONFIGURATION_INCOMPLETE, user=user, scope=SCOPE_OPERATIONS,
            area=AREA_STAFFING, module=OP01_MODULE_ID, operation="history",
        )
        return JSONResponse({"state": "config_incomplete"})
    except Exception as exc:  # noqa: BLE001 — falla de Graph/transporte, no de config
        # `data_load_failed`/`operation=history` (corrective A2): reemplaza al
        # histórico `staffing_historial_error` — mismo evento canónico que la
        # carga inicial, distinguible por `operation`.
        log_verbose(
            EVENT_DATA_LOAD_FAILED, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
            module=OP01_MODULE_ID, operation="history", error_type=type(exc).__name__,
        )
        return JSONResponse({"state": "graph_error"})

    # Mismo diagnóstico que staffing_data (ver arriba) — prueba explícita de
    # si el toggle de Inactivo disparó exactamente 1 request y si el
    # $filter/fallback de esa carga histórica funcionó. `data_load_diagnostics`/
    # `operation=history` (corrective A2) reemplaza al histórico
    # `staffing_historial_diagnostics`.
    log_verbose(
        EVENT_DATA_LOAD_DIAGNOSTICS, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
        module=OP01_MODULE_ID, operation="history",
        **_flatten_staffing_diagnostics(getattr(service, "last_diagnostics", {})),
    )

    payload = build_dashboard_payload(rows)
    return JSONResponse({"state": "ok", "assignments": payload["assignments"]})


@router.post("/operaciones/staffing/asignaciones/ausencias", include_in_schema=False)
def staffing_ausencias(request: Request, graph_token: str = Form(""), token_diagnostic: str = Form("")):
    """Overlay de ausencias aprobadas (Staffing Approved Absence Overlay,
    ver HANDOFF — generalización del Vacation Timeline Overlay original) —
    carga LAZY e INDEPENDIENTE de `staffing_data`, MISMO patrón JSON que
    `staffing_historial`. ADITIVO/presentacional a propósito: una falla acá
    (config incompleta, error de Graph) nunca degrada Asignaciones —
    `app.js::staffingDashboard._loadAbsences()` omite el overlay y muestra
    un aviso propio, sin afectar KPIs/timeline/detalle existentes."""
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect

    if not graph_token:
        # MISMO criterio que staffing_data (ver ahí) — owner ticket "STAFFING
        # — MSAL TOKEN FAILURE TELEMETRY".
        log_verbose(
            EVENT_TOKEN_ACQUISITION_FAILED, user=auth_service.session_user(request),
            scope=SCOPE_OPERATIONS, area=AREA_STAFFING, module=OP01_MODULE_ID,
            operation="absences", **_safe_token_diagnostic(token_diagnostic),
        )
        return JSONResponse({"state": "missing_token"})

    user = auth_service.session_user(request)
    # cache_scope (SHARED SOURCE ACQUISITION corrective, ver HANDOFF) —
    # MISMA resolución que staffing_data/staffing_historial: Colaboradores
    # reusa el estado ya calentado por Initial/Historial (§3 del ticket) y
    # el XLSX de Ausencias reusa el resultado parseado por versión (§5).
    cache_scope = str((user or {}).get("username") or (user or {}).get("email") or (user or {}).get("sub") or "") or None
    service = StaffingService()
    try:
        overlay = service.get_absence_overlay(graph_token, cache_scope=cache_scope)
    except RuntimeError:
        log_verbose(
            EVENT_CONFIGURATION_INCOMPLETE, user=user, scope=SCOPE_OPERATIONS,
            area=AREA_STAFFING, module=OP01_MODULE_ID, operation="absences",
        )
        return JSONResponse({"state": "config_incomplete"})
    except AbsenceSchemaError as exc:
        # Safe Graph Failure Diagnostics (ver HANDOFF): stage +
        # missing/ambiguous_fields son SIEMPRE labels del contrato semántico
        # REAL fijo (Apellido/Nombre/Motivo/Desde/Hasta/Estado) — nunca
        # internal names ni valores de dato, seguros de loguear y de
        # devolver.
        log_verbose(
            EVENT_DATA_LOAD_FAILED, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
            module=OP01_MODULE_ID, operation="absences", error_type=type(exc).__name__,
            stage=exc.stage, reason=exc.reason,
            missing_fields=exc.missing_fields, ambiguous_fields=exc.ambiguous_fields,
        )
        return JSONResponse({
            "state": "schema_error", "stage": exc.stage,
            "missing_fields": exc.missing_fields, "ambiguous_fields": exc.ambiguous_fields,
        })
    except AbsenceGraphError as exc:
        # stage + status_code son los ÚNICOS campos seguros de una falla de
        # transporte Graph (ver AbsenceGraphError) — nunca el HTTPError/
        # URLError crudo (podría incluir URL/headers).
        log_verbose(
            EVENT_DATA_LOAD_FAILED, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
            module=OP01_MODULE_ID, operation="absences", error_type=type(exc).__name__,
            stage=exc.stage, reason=exc.reason, http_status=exc.status_code,
        )
        # 401 = la autorización delegada de Graph vencida, NO una caída de
        # SharePoint. MISMA decisión compartida que Reporting
        # (`webapp.graph_auth_state`), con el vocabulario de falla de fuente
        # de Staffing (`graph_error`) intacto para todo lo demás — su
        # template y sus tests dependen de ese nombre.
        return JSONResponse({
            "state": graph_error_state(exc.status_code, source_state="graph_error"),
            "stage": exc.stage, "http_status": exc.status_code,
        })
    except Exception as exc:  # noqa: BLE001 — falla inesperada, nunca de config
        log_verbose(
            EVENT_DATA_LOAD_FAILED, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
            module=OP01_MODULE_ID, operation="absences", error_type=type(exc).__name__,
        )
        return JSONResponse({"state": "graph_error"})

    # Mismo diagnóstico canónico que initial/history — nunca nombres/PII, solo
    # conteos/tiempos + match/unmatched/ambiguous (ya son conteos puros en el
    # payload, ver StaffingService.get_absence_overlay).
    log_verbose(
        EVENT_DATA_LOAD_DIAGNOSTICS, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING,
        module=OP01_MODULE_ID, operation="absences",
        unmatched_count=overlay["unmatched_count"], ambiguous_count=overlay["ambiguous_count"],
        **_flatten_staffing_diagnostics(getattr(service, "last_diagnostics", {})),
    )

    return JSONResponse({
        "state": "ok",
        "absences": overlay["absences"],
        # Dirección corrective — Timeline/ventana de ausencias dinámica (ver
        # HANDOFF): el máximo del selector "Ausencias · últimos N días" ya
        # no es un número fijo — viene de acá, MISMA fuente que decidió qué
        # `absences` se hidrataron (ver build_absence_overlay_payload).
        "absence_history_start": overlay["absence_history_start"],
        "absence_lookback_max_days": overlay["absence_lookback_max_days"],
    })


# --- Compatibilidad histórica (nombres en inglés, Batch 3) ------------------
# 307 preserva method + body — el POST con `graph_token` en el form sigue
# llegando correctamente a la ruta canónica, HTMX incluido (el browser sigue
# la redirección de forma transparente). Nunca reimplementan la lógica de
# arriba: un solo punto de verdad.

@router.get("/operations/staffing", include_in_schema=False)
def staffing_page_compat():
    return RedirectResponse("/operaciones/staffing/asignaciones", status_code=307)


@router.post("/operations/staffing/data", include_in_schema=False)
def staffing_data_compat():
    return RedirectResponse("/operaciones/staffing/asignaciones/data", status_code=307)


# ───────────────────────── Carga de Horas (Operaciones → 01. Asignaciones → vista «Carga de Horas») ─────────────────────────
# Vista alternativa del MISMO módulo (OP01), seleccionable con la botonera `.dashboard-view-switch` del hero. Reemplaza SOLO el contenido central
# (`#staffing-rows`); la navegación, la cabecera y la vista Asignaciones no cambian. Cálculo en `timenotes_service` (independiente de UI y envío);
# envío en `timenotes_send` (reusa `institutional_mail`). Lectura con el MISMO token delegado base que Staffing (`data-dw-graph-token`).

def _timenotes_week(semana: str):
    """`semana` = una fecha YYYY-MM-DD dentro de la semana deseada; vacío = semana anterior completa (L–V)."""
    s = (semana or "").strip()
    if s:
        try:
            return timenotes_service.week_range(datetime.strptime(s, "%Y-%m-%d").date())
        except ValueError:
            pass
    return timenotes_service.previous_week()


def _timenotes_actor(request: Request) -> str:
    u = auth_service.session_user(request) or {}
    return str(u.get("email") or u.get("username") or u.get("sub") or "")


def _timenotes_estados(estados: list[str], presentes: str) -> list[str]:
    """Estados de asignación elegidos en el filtro (P7). Sin el marcador `estados_presentes` (primer ingreso / cliente viejo) → default (Activo).
    Con el marcador, la lista se respeta tal cual (incluso vacía)."""
    if not presentes:
        return list(timenotes_service.ESTADOS_DEFAULT)
    return [e.strip() for e in (estados or []) if e and e.strip()]


def _timenotes_cache_scope(request: Request):
    # MISMA identidad estable de sesión que usa Asignaciones para su cache de dimensiones (nunca el graph_token).
    user = auth_service.session_user(request) or {}
    return str(user.get("username") or user.get("email") or user.get("sub") or "") or None


def _timenotes_horas_base(raw: str) -> float:
    """Horas base de CONSULTA (simulación, owner 2026-11-12): 1–80 h en pasos de 0,5; vacío/ inválido → la regla vigente (40 h)."""
    try:
        v = round(float(str(raw or "").replace(",", ".")) * 2) / 2
    except ValueError:
        return timenotes_service.WEEKLY_HOURS
    return v if 1 <= v <= 80 else timenotes_service.WEEKLY_HOURS


def _timenotes_feriados(tipos: list[str], presentes: str) -> tuple:
    """Tipos de feriado elegidos. Sin el marcador del form (primer ingreso / cliente viejo) → regla operativa (solo Inamovible)."""
    if not presentes:
        return timenotes_service.HOLIDAY_TYPES_DEFAULT
    return timenotes_service.holiday_types_selection(tipos)


def _timenotes_compute(request: Request, graph_token: str, semana: str, estados: list[str], weekly_hours: float | None = None,
                       holiday_types: tuple | None = None) -> dict:
    # `weekly_hours` SOLO lo pasa la vista (simulación); el envío y la previsualización calculan siempre con la regla vigente (40 h).
    return timenotes_service.build_compliance(
        token=graph_token, week=_timenotes_week(semana), weekly_hours=weekly_hours, holiday_types=holiday_types,
        include_inactive=timenotes_service.estados_requieren_inactivos(estados), cache_scope=_timenotes_cache_scope(request))


_TN_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _timenotes_with_exceptions(data: dict) -> dict:
    """Aplica las excepciones PERMANENTES (store `timenotes_exceptions`) al resultado ya calculado: saca a los exceptuados de Colaboradores, recalcula
    el resumen sobre los restantes y arma `excepciones` = TODAS las entradas del registro (también las de quien no está en el padrón de la semana),
    cada una con su fila calculada si existe. No altera el cálculo de horas."""
    excluded = timenotes_exceptions.list_emails()
    data = timenotes_exceptions.apply(data, excluded)
    if data.get("status") == timenotes_service.STATUS_OK:
        rows = {c["email"]: c for c in data.get("excepciones_manuales_rows") or []}
        data["excepciones"] = [dict(e, fila=rows.get(e["email"])) for e in timenotes_exceptions.entries()
                               if not timenotes_service.is_non_collaborator(e["email"])]
    return data


@router.post("/operaciones/staffing/carga-horas/data", response_class=HTMLResponse, include_in_schema=False)
def timenotes_data(request: Request, graph_token: str = Form(""), semana: str = Form(""),
                   estados: list[str] = Form([]), estados_presentes: str = Form(""),
                   disponibilidad: str = Form(""), cumplimiento: str = Form(""), q: str = Form(""), horas_base: str = Form(""),
                   feriados_tipos: list[str] = Form([])):
    """Panel de cumplimiento (HTMX → `#staffing-rows`). Sin token: ofrece la recuperación Graph compartida (mismo criterio que Asignaciones)."""
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect
    if not graph_token:
        return templates.TemplateResponse(request=request, name="operations/timenotes_panel.html", context={"data": {"status": "missing_token"}})
    sel = _timenotes_estados(estados, estados_presentes)
    hb = _timenotes_horas_base(horas_base)
    tipos = _timenotes_feriados(feriados_tipos, estados_presentes)
    data = _timenotes_with_exceptions(_timenotes_compute(request, graph_token, semana, sel, weekly_hours=hb, holiday_types=tipos))
    # Simulación = cualquier consulta distinta de la regla operativa (40 h + feriados Inamovible): se ve, pero no se notifica.
    data["feriados_tipos_sel"] = list(tipos)
    data["simulacion_motivos"] = ([f"horas base {hb:g} h"] if hb != timenotes_service.WEEKLY_HOURS else []) + \
        ([("feriados: " + (", ".join(tipos) or "ninguno"))] if tipos != timenotes_service.HOLIDAY_TYPES_DEFAULT else [])
    data["simulacion"] = bool(data["simulacion_motivos"])
    data["estados_sel"] = sel
    # Filtros locales: viajan con el form para CONSERVARSE en recargas (semana / Estado con historial). Primer ingreso → valores aprobados.
    data["filtros"] = {
        "disponibilidad": (disponibilidad or "").strip() if estados_presentes else timenotes_service.DISPONIBILIDAD_DEFAULT,
        "cumplimiento": (cumplimiento or "").strip() if estados_presentes else "faltantes",
        "q": (q or "").strip()[:120],
    }
    disp = {c.get("disponibilidad") for c in data.get("colaboradores") or [] if c.get("disponibilidad")}
    data["disponibilidad_opciones"] = sorted(disp | {timenotes_service.DISPONIBILIDAD_DEFAULT})
    presentes = {e for c in data.get("colaboradores") or [] for e in (c.get("estados") or [])}
    data["estados_opciones"] = list(timenotes_service.ESTADOS_CONOCIDOS) + sorted(presentes - set(timenotes_service.ESTADOS_CONOCIDOS))
    data["notifications_enabled"] = notifications.enabled()
    data["test_recipient"] = _timenotes_actor(request)        # modo prueba redirige a la propia casilla del usuario (sin config)
    data["test_mode"] = timenotes_send.effective_test_mode(None)
    return templates.TemplateResponse(request=request, name="operations/timenotes_panel.html", context={"data": data})


@router.post("/operaciones/staffing/carga-horas/preview", include_in_schema=False)
def timenotes_preview(request: Request, graph_token: str = Form(""), email: str = Form(""), semana: str = Form(""), test_mode: str = Form("true")):
    """Previsualización del correo de UN colaborador (revisión antes de enviar). Recalcula en servidor: nunca arma el mensaje con números del cliente."""
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect
    if not same_origin_guard.is_same_origin_write(request):
        return JSONResponse({"error": "Origen no permitido"}, status_code=403)
    if not graph_token:
        return JSONResponse({"error": "Sin autorización de Microsoft"}, status_code=401)
    data = timenotes_service.build_compliance(token=graph_token, week=_timenotes_week(semana))
    if data["status"] != timenotes_service.STATUS_OK:
        return JSONResponse({"error": "No se puede previsualizar: los datos de origen no son válidos.", "reason": data["status"]}, status_code=422)
    target = (email or "").strip().lower()
    if target in timenotes_exceptions.list_emails():
        return JSONResponse({"error": "El colaborador está en Excepciones: no recibe recordatorios."}, status_code=409)
    c = next((x for x in data["colaboradores"] if x["email"] == target), None)
    if c is None:
        return JSONResponse({"error": "Colaborador no encontrado en el padrón de la semana."}, status_code=404)
    if not c.get("obligacion", True):
        return JSONResponse({"error": "Sin mínimo semanal definido para este colaborador (disponibilidad: " + str(c.get("disponibilidad") or "Sin dato") + "): no corresponde un recordatorio."}, status_code=409)
    tmode = timenotes_send.effective_test_mode(test_mode)
    recipient, err = timenotes_send.resolve_recipient(c["email"], test_mode=tmode, actor=_timenotes_actor(request))
    msg = timenotes_send.build_message(
        colaborador_email=c["email"], nombre=c["nombre"], semana_label=data["semana"]["label"],
        exigibles=c["horas_exigibles"], registradas=c["horas_registradas"], faltantes=c["faltantes"],
        recipient=recipient or c["email"], test_mode=tmode)
    return JSONResponse({"subject": msg["message"]["subject"], "html": msg["message"]["body"]["content"],
                         "recipient": recipient, "test_mode": tmode, "recipient_error": err})


@router.post("/operaciones/staffing/carga-horas/notificar", include_in_schema=False)
def timenotes_notify(request: Request, graph_token: str = Form(""), semana: str = Form(""),
                     test_mode: str = Form("true"), confirm: str = Form(""), emails: list[str] = Form([]),
                     estados: list[str] = Form([]), estados_presentes: str = Form(""), horas_base: str = Form(""),
                     feriados_tipos: list[str] = Form([])):
    """Envío MANUAL (individual o lote). Recalcula en servidor y SOLO notifica a colaboradores elegibles (faltantes y sin excepción pendiente):
    nunca notifica con datos inválidos/incompletos ni a quien tiene una excepción a revisar. Confirmación explícita obligatoria."""
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect
    if not same_origin_guard.is_same_origin_write(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido"}, status_code=403)
    if str(confirm).lower() not in {"yes", "true", "1", "on"}:
        return JSONResponse({"status": "error", "message": "Falta la confirmación explícita del envío."}, status_code=400)
    if not graph_token:
        return JSONResponse({"status": "error", "message": "Sin autorización de Microsoft."}, status_code=401)

    if (horas_base and _timenotes_horas_base(horas_base) != timenotes_service.WEEKLY_HOURS) or \
            (estados_presentes and _timenotes_feriados(feriados_tipos, estados_presentes) != timenotes_service.HOLIDAY_TYPES_DEFAULT):
        return JSONResponse({"status": "blocked", "reason": "simulacion",
                             "message": "La vista está en simulación (horas base o tipos de feriado distintos de la regla): no se envían recordatorios. Volvé a 40 h y solo Inamovible para notificar."},
                            status_code=409)
    sel = set(_timenotes_estados(estados, estados_presentes))
    data = _timenotes_compute(request, graph_token, semana, sel)          # SIEMPRE con la regla vigente (40 h), nunca con la simulación
    if data["status"] == timenotes_service.STATUS_OK and not (data.get("feriados") or {}).get("verificado", True):
        return JSONResponse({"status": "blocked", "reason": "feriados_no_verificados",
                             "message": "No se puede notificar: el calendario de feriados no se pudo validar. " + str((data.get("feriados") or {}).get("detalle") or "")},
                            status_code=422)
    if data["status"] == timenotes_service.STATUS_OK and not data.get("estados_disponibles"):
        return JSONResponse({"status": "blocked", "reason": "estados_no_disponibles",
                             "message": "No se puede notificar: no se pudo verificar el estado de asignación de los colaboradores."},
                            status_code=422)
    if data["status"] != timenotes_service.STATUS_OK:
        return JSONResponse({"status": "blocked", "reason": data["status"],
                             "message": "No se puede notificar: los datos de origen no son válidos o están incompletos. " + str(data.get("detalle") or "")},
                            status_code=422)

    # Revalidación en servidor: las excepciones permanentes vigentes AL MOMENTO DEL ENVÍO se excluyen siempre (aunque el cliente las mande).
    excluded = timenotes_exceptions.list_emails()
    by_email = {c["email"]: c for c in data["colaboradores"]}
    requested = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    destinatarios, skipped = [], []
    for e in requested:
        c = by_email.get(e)
        if e in excluded:
            skipped.append({"email": e, "reason": "en_excepciones"})
        elif c is not None and not c.get("obligacion", True):
            skipped.append({"email": e, "reason": "sin_obligacion_definida"})   # Part Time / sin dato: sin mínimo semanal aprobado
        elif c is not None and not (set(c.get("estados") or []) & sel):
            skipped.append({"email": e, "reason": "fuera_del_estado"})     # revalidación: fuera del universo de Estado elegido
        elif c is None:
            skipped.append({"email": e, "reason": "fuera_del_padron"})
        elif not c["notificable"]:
            skipped.append({"email": e, "reason": "con_excepcion_a_revisar" if c["revisar_excepcion"] else "sin_faltantes"})
        else:
            destinatarios.append({"email": c["email"], "nombre": c["nombre"], "exigibles": c["horas_exigibles"],
                                  "registradas": c["horas_registradas"], "faltantes": c["faltantes"]})
    if not destinatarios:
        return JSONResponse({"status": "nada", "message": "No hay destinatarios elegibles para notificar.", "skipped": skipped}, status_code=422)

    tmode = timenotes_send.effective_test_mode(test_mode)
    batch = timenotes_send.send_batch(destinatarios, semana=data["semana"], actor=_timenotes_actor(request), test_mode=tmode)
    return JSONResponse({"status": "ok", "test_mode": tmode, "skipped": skipped, **batch})


def _timenotes_exception_guard(request: Request):
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect
    if not same_origin_guard.is_same_origin_write(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido"}, status_code=403)
    return None


@router.post("/operaciones/staffing/carga-horas/excepciones/mover", include_in_schema=False)
def timenotes_exception_add(request: Request, email: str = Form(""), nombre: str = Form("")):
    """«Mover a excepciones»: alta PERMANENTE del colaborador en el registro durable (hasta reincorporarlo). No toca el CSV ni el padrón; el
    envío vuelve a validar el registro en servidor (`notificar`)."""
    blocked = _timenotes_exception_guard(request)
    if blocked is not None:
        return blocked
    em = (email or "").strip().lower()
    if not _TN_EMAIL_RE.match(em):
        return JSONResponse({"status": "error", "message": "Email inválido."}, status_code=400)
    try:
        timenotes_exceptions.add([{"email": em, "nombre": (nombre or "").strip()[:200]}], actor=_timenotes_actor(request))
    except timenotes_exceptions.ExceptionsUnavailable as exc:
        return JSONResponse({"status": "error", "message": f"No se pudo guardar la excepción ({exc}). No se aplicó ningún cambio."}, status_code=503)
    entry = next((e for e in timenotes_exceptions.entries() if e["email"] == em), None)
    return JSONResponse({"status": "ok", "entry": entry})


@router.post("/operaciones/staffing/carga-horas/excepciones/devolver", include_in_schema=False)
def timenotes_exception_remove(request: Request, email: str = Form("")):
    """«Reincorporar»: quita al colaborador del registro de excepciones (queda constancia de la baja en el historial)."""
    blocked = _timenotes_exception_guard(request)
    if blocked is not None:
        return blocked
    em = (email or "").strip().lower()
    if not em:
        return JSONResponse({"status": "error", "message": "Falta el email."}, status_code=400)
    try:
        timenotes_exceptions.remove([em], actor=_timenotes_actor(request))
    except timenotes_exceptions.ExceptionsUnavailable as exc:
        return JSONResponse({"status": "error", "message": f"No se pudo actualizar el registro ({exc}). No se aplicó ningún cambio."}, status_code=503)
    return JSONResponse({"status": "ok", "email": em})
