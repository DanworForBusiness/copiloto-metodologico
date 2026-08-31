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

from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import hub_metrics_access, operations_access, workspaces
from webapp.services import auth_service, feedback_store
from webapp.services.activity_service import log_activity, log_verbose
from webapp.services.activity_context import (
    SCOPE_OPERATIONS, AREA_STAFFING,
    EVENT_MODULE_VIEWED, EVENT_DATA_LOAD_FAILED, EVENT_DATA_LOAD_DIAGNOSTICS,
    EVENT_CONFIGURATION_INCOMPLETE,
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
    # Primer evento de USO real de Operations (Batch A1): un acceso exitoso a la
    # página de Asignaciones. Adopción (BASIC), como module_viewed de Copiloto
    # — NO un evento de diagnóstico. Un evento por acceso a la página (el GET);
    # el POST /data y las interacciones client-side NO se loguean (sin clickstream).
    # Usa la sesión ya resuelta; sin segunda llamada a Graph, sin token, sin dataset.
    # `module_viewed`/`module=assignments` (corrective A2 — Transversal Module):
    # reemplaza al histórico `staffing_overview_viewed`, mismo hecho (una vista
    # de módulo), ahora reusando el mismo evento genérico que Copiloto en vez
    # de repetir "staffing" en el nombre del evento.
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_OPERATIONS, area=AREA_STAFFING, module=OP01_MODULE_ID)
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
def staffing_data(request: Request, graph_token: str = Form("")):
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect

    if not graph_token:
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
def staffing_historial(request: Request, graph_token: str = Form("")):
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
def staffing_ausencias(request: Request, graph_token: str = Form("")):
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
