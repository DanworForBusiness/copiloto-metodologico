"""
commercial.py — Comercial / CRM HTTP (Commercial Hub Workspace Foundation).

Batch 0: `GET /comercial` (shell autenticado, sin Odoo/dato real) — la
MISMA arquitectura Hub que ya demostró Operations (auth/sesión, topbar,
switcher, sidebar, theme, CSS/JS compartido, loader canónico, permissions
pattern), aplicada a un TERCER workspace.

Batch 1 (Odoo read-only + Summary + Pipeline, ver HANDOFF): agrega
`GET /comercial/data/summary` y `GET /comercial/data/pipeline` — el primer
vertical slice REAL, 100% lectura. NUNCA reciben credenciales/token del
browser (Odoo es un service account server-side, ver `config.py`/
`webapp.services.odoo_client`) — a diferencia de Staffing/Reporting (Graph
delegado del usuario), acá NO hace falta transportar ningún token de sesión
Graph por request. `tipo` (`"todos"|"nn"|"bi"`) es el ÚNICO filtro de este
batch —
ver `webapp.services.commercial_service` para la semántica completa
(legacy-compatible semantic resolution). NUNCA integra el CRM Odoo desde
JavaScript ni expone credenciales — todo el transporte vive server-side en
`webapp.services.odoo_client.OdooClient`.

Consume `webapp.commercial_access.is_commercial_authorized` (allowlist
explícita `COMMERCIAL_ALLOWED_USERS` del workspace Comercial, INDEPENDIENTE
de `OPERATIONS_ALLOWED_USERS` — ver Commercial Hub Workspace Foundation en
HANDOFF), MISMO patrón que `webapp/routers/staffing.py` con
`webapp.operations_access` — MISMO gate para las 3 rutas de este router
(shell Y los 2 endpoints de datos).

El deep-link no autenticado entra por `webapp.workspaces.platform_login_redirect`
(apunta a `/onedanwor`, vía el alias de compatibilidad `/platform`) — NO por
`webapp.nav.login_redirect` (Copilot-específico): Comercial es un workspace
distinto del Copiloto, mismo criterio que Operations.

Fail-soft/localizado (MISMO contrato que Reporting, ver
`webapp/routers/reporting.py`): los endpoints de datos SIEMPRE devuelven
HTTP 200 con `{"state": ...}` — `"config_incomplete"` (Odoo sin configurar),
`"source_error"` (falla real de Odoo, con `stage`) o `"unexpected"` (falla
no prevista, logueada con traceback server-side, NUNCA expuesta al
browser). Comercial nunca rompe el Hub completo.

Clasificación: COMMERCIAL / CRM HTTP. NOT PLATFORM / SHARED. NO importa
`webapp.main`, no conoce JSON-RPC/transporte Odoo directamente (vive en
`webapp.services.odoo_client`), no posee semántica de negocio (vive en
`webapp.services.commercial_service`) — este router solo conecta
HTTP↔service, MISMO patrón que `staffing.py`/`reporting.py`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, workspaces
from webapp.services import auth_service, commercial_licensing_priority_repository, feedback_store
from webapp.services.activity_context import SCOPE_COMMERCIAL, AREA_CRM, EVENT_MODULE_VIEWED
from webapp.services.activity_service import log_activity
from webapp.services.commercial_service import (
    CommercialConfigurationError,
    CommercialDataError,
    CommercialService,
)
from webapp.services.module_registry import CM01_MODULE_ID
from webapp.templating import templates

router = APIRouter()
_logger = logging.getLogger("danwor.commercial")
_VALID_TIPOS = {"todos", "nn", "bi"}


def _require_commercial_session(request: Request, *, next_path: str | None = None):
    """Redirect (sin sesión) o 403 (sesión sin autorización de Comercial) —
    o None si OK. MISMO patrón que `staffing._require_operations_session`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not commercial_access.is_commercial_authorized(request):
        raise HTTPException(403, "No autorizado para Comercial")
    return None


@router.get("/comercial", response_class=HTMLResponse, include_in_schema=False)
def commercial_page(request: Request):
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    # Primer evento de USO real de Comercial — MISMO criterio que
    # Operations/Asignaciones (module_viewed, ver staffing.staffing_page):
    # un acceso exitoso a la página. Sin clickstream (el GET, no
    # interacciones client-side).
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_CRM, module=CM01_MODULE_ID)
    return templates.TemplateResponse(
        request=request, name="commercial/crm.html",
        context={
            "current_workspace": workspaces.COMMERCIAL,
            "available_workspaces": workspaces.available_workspaces(user),
            # Comercial no tiene concepto de "pack" propio — MISMO criterio
            # que Operations (ver staffing.staffing_page): la identidad
            # animada compartida se resuelve contra el pack por defecto.
            "pack": DEFAULT_PACK,
            "user": user,
            # Resalta "01 CRM" en commercial_nav.html.
            "active_page": "crm",
        },
    )


def _normalize_tipo(tipo: str) -> str:
    value = (tipo or "todos").strip().lower()
    return value if value in _VALID_TIPOS else "todos"


def _commercial_data_error_response(request: Request, exc: Exception, *, endpoint: str):
    """Traduce una excepción de `CommercialService` al contrato fail-soft
    compartido (`{"state": ...}`, SIEMPRE HTTP 200 — MISMO criterio que
    `reporting.py`) y logea `commercial.data_error` (diagnóstico, server-
    side directo — ver activity_context.py)."""
    user = auth_service.session_user(request)
    if isinstance(exc, CommercialConfigurationError):
        return JSONResponse({"state": "config_incomplete"})
    if isinstance(exc, CommercialDataError):
        log_activity(
            "commercial.data_error", user=user, scope=SCOPE_COMMERCIAL, area=AREA_CRM,
            module=CM01_MODULE_ID, endpoint=endpoint, stage=exc.stage,
        )
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    # Inesperada: traceback real solo en el log del server, NUNCA al browser.
    _logger.exception("commercial %s: unexpected failure", endpoint)
    log_activity(
        "commercial.data_error", user=user, scope=SCOPE_COMMERCIAL, area=AREA_CRM,
        module=CM01_MODULE_ID, endpoint=endpoint, stage="unexpected",
    )
    return JSONResponse({"state": "source_error", "stage": "unexpected"})


@router.get("/comercial/data/summary", include_in_schema=False)
def commercial_data_summary(request: Request, tipo: str = Query("todos")):
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        summary = CommercialService().get_summary(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="summary")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="summary")
    return JSONResponse({"state": "ok", "summary": summary})


@router.get("/comercial/data/pipeline", include_in_schema=False)
def commercial_data_pipeline(request: Request, tipo: str = Query("todos")):
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        pipeline = CommercialService().get_pipeline(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="pipeline")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="pipeline")
    return JSONResponse({"state": "ok", "pipeline": pipeline})


@router.get("/comercial/data/opportunities", include_in_schema=False)
def commercial_data_opportunities(request: Request, tipo: str = Query("todos")):
    """Detalle por oportunidad de "Oportunidades activas" (Active
    Opportunities Phase 2, ver HANDOFF) — universo stages 1-8, MISMO que el
    KPI del landing. Alimenta la tabla de detalle Y el desglose por Origen
    (derivado client-side, sin endpoint propio). MISMO contrato fail-soft y
    MISMA gate que el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        opportunities = CommercialService().get_opportunities(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="opportunities")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="opportunities")
    return JSONResponse({"state": "ok", "opportunities": opportunities})


@router.get("/comercial/data/prospects", include_in_schema=False)
def commercial_data_prospects(request: Request, tipo: str = Query("todos")):
    """Detalle por oportunidad de "Prospectos" (Commercial Tile 5, ver
    HANDOFF) — universo stage 0, MISMO que el KPI del landing. LAZY: a
    diferencia de `opportunities` (que hidrata con el resto del landing),
    este endpoint sólo se pide en la primera apertura del desglose — MISMO
    contrato que `forecast`/`creadas`. MISMO contrato fail-soft y MISMA gate
    que el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        prospects = CommercialService().get_prospects(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="prospects")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="prospects")
    return JSONResponse({"state": "ok", "prospects": prospects})


@router.get("/comercial/data/ganadas", include_in_schema=False)
def commercial_data_ganadas(request: Request, tipo: str = Query("todos"), year: int = Query(0)):
    """Detalle de "Ganadas" (Commercial Tile 6, ver HANDOFF) — universo
    canónico stage_id.is_won=True + date_closed dentro del año pedido
    (year=0 resuelve al año actual), MISMO dominio que el KPI del landing.
    LAZY: sólo se pide al abrir el análisis, nunca en la hidratación normal
    del dashboard. Cache por año en el service
    (`commercial:ganadas:<year>`) — SIN ventana caliente: el frontend nunca
    prefetchea el año anterior en background (owner directive). MISMO
    contrato fail-soft y MISMA gate que el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        ganadas = CommercialService().get_ganadas_detail(_normalize_tipo(tipo), year or None)
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="ganadas")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="ganadas")
    return JSONResponse({"state": "ok", "ganadas": ganadas})


@router.get("/comercial/data/perdidas", include_in_schema=False)
def commercial_data_perdidas(request: Request, tipo: str = Query("todos"), year: int = Query(0)):
    """Detalle de "Perdidas" (Commercial Tile 7, ver HANDOFF) — universo
    canónico active=False + probability=0 + date_closed dentro del año
    pedido (year=0 resuelve al año actual, límites CON hora), MENOS las
    exclusiones administrativas de motivo — MISMO dominio + MISMO cascade
    de motivo/exclusión que el KPI del landing (`_resolve_lost_motivos`/
    `_filter_excluded_lost`, reusados literal, nunca reimplementados). LAZY:
    sólo se pide al abrir el análisis, nunca en la hidratación normal del
    dashboard. Cache por año en el service (`commercial:perdidas:<year>`)
    — SIN ventana caliente. El desglose por Motivo se deriva CLIENT-SIDE de
    estas MISMAS filas (nunca un endpoint separado — corrige el bug real
    del legacy `/lost-reasons`, hardcodeado a año actual, sin agregar
    ningún RPC extra). MISMO contrato fail-soft y MISMA gate que el resto
    de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        perdidas = CommercialService().get_perdidas_detail(_normalize_tipo(tipo), year or None)
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="perdidas")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="perdidas")
    return JSONResponse({"state": "ok", "perdidas": perdidas})


@router.get("/comercial/data/suspendidas", include_in_schema=False)
def commercial_data_suspendidas(request: Request, tipo: str = Query("todos")):
    """Detalle de "Suspendidas" (Commercial Tile 8, ver HANDOFF) — universo
    canónico active=True + stage_id en `suspended_ids` (nombre de etapa que
    CONTIENE "suspendido"), MISMO dominio que el KPI del landing. STOCK
    actual — SIN año, a diferencia de Ganadas/Perdidas. LAZY: sólo se pide
    al abrir el análisis, nunca en la hidratación normal del dashboard.
    MISMO contrato fail-soft y MISMA gate que el resto de Comercial —
    read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        suspendidas = CommercialService().get_suspendidas_detail(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="suspendidas")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="suspendidas")
    return JSONResponse({"state": "ok", "suspendidas": suspendidas})


@router.get("/comercial/data/opportunities-detail", include_in_schema=False)
def commercial_data_opportunities_detail(request: Request, tipo: str = Query("todos")):
    """Detalle Oportunidades (Bucket 1, ver HANDOFF) — universo canónico
    stages 0-8, active=True (MISMO universo que Forecast, ver
    `CommercialService._build_opportunities_detail`). TAMBIÉN sirve
    "Licencias": el legacy ya trata a las dos tabs como la MISMA fuente,
    filtrada `licencias === "Si"` client-side — un solo endpoint/cache, cero
    request propia para Licencias. LAZY: sólo se pide al abrir CUALQUIERA de
    las dos tabs, nunca en la hidratación normal del dashboard. MISMO
    contrato fail-soft y MISMA gate que el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        detail = CommercialService().get_opportunities_detail(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="opportunities_detail")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="opportunities_detail")
    return JSONResponse({"state": "ok", "opportunities_detail": detail})


@router.get("/comercial/data/licensing-profile", include_in_schema=False)
def commercial_data_licensing_profile(request: Request, tipo: str = Query("todos")):
    """Perfil de licenciamiento por cliente (Account Licensing Profile, ver
    HANDOFF — Commercial Licensing) — INDEPENDIENTE del endpoint de
    Licencias/Detalle: universo (grano `commercial_partner_id`) y falla
    propia, nunca comparte fetch/cache con `opportunities-detail` (owner
    §7). LAZY: sólo se pide al abrir Licencias. MISMO contrato fail-soft y
    MISMA gate que el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        profile = CommercialService().get_licensing_profile(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="licensing_profile")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="licensing_profile")
    return JSONResponse({"state": "ok", "licensing_profile": profile})


@router.get("/comercial/data/licensing-priority", include_in_schema=False)
def commercial_data_licensing_priority(request: Request):
    """Director §5-§8 (ver HANDOFF): sólo las EXCEPCIONES — account_ids
    actualmente en la tabla secundaria ("sin prioridad"). Ausencia de un
    account_id acá = tabla principal (default). LAZY: se pide junto con
    `licensing-profile` (mismo caller en commercial.js), nunca antes."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    deferred = sorted(commercial_licensing_priority_repository.list_deferred_account_ids())
    return JSONResponse({"state": "ok", "deferred_account_ids": deferred})


@router.get("/comercial/data/licensing-account", include_in_schema=False)
def commercial_data_licensing_account(request: Request, account_id: int = Query(...)):
    """Director ("Ver detalle de licencias"): inspección PUNTUAL de UNA cuenta.

    Endpoint propio y no un parámetro del Perfil a propósito: el Perfil es una
    COLECCIÓN gobernada (`Es Cliente? = Sí AND Argentina`) y esto es una
    CONSULTA por id. Mezclarlos obligaría a re-pedir el universo entero para
    mirar una sola cuenta, y tentaría a relajar el filtro de la colección —
    que es justamente el guardrail que no se toca.

    Devuelve la fila con el MISMO adaptador de properties que el Perfil.
    `account` en `null` = la cuenta no se pudo resolver (no existe), que es
    distinto de "no es elegible para el Perfil": una cuenta inelegible SÍ se
    devuelve y SÍ se muestra.
    """
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        account = CommercialService().get_licensing_account_detail(account_id)
    except CommercialConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except CommercialDataError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    return JSONResponse({"state": "ok", "account": account})


@router.post("/comercial/licensing/priority/defer", include_in_schema=False)
def commercial_licensing_priority_defer(request: Request, account_id: int = Form(...)):
    """Director §5/§6: "Mover a sin prioridad" — mueve `account_id` (id
    CANÓNICO de cuenta, ver Perfil de licenciamiento) de la tabla principal
    a la secundaria. Comercial SHARED state, server-side (owner §7,
    explícito) — nunca sólo en el browser."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request) or {}
    identity = str(user.get("username") or user.get("email") or user.get("sub") or "")
    try:
        commercial_licensing_priority_repository.defer(account_id, by=identity)
    except commercial_licensing_priority_repository.CommercialLicensingPriorityStorageError as exc:
        return JSONResponse({"state": "source_error", "message": str(exc)}, status_code=200)
    return JSONResponse({"state": "ok"})


@router.post("/comercial/licensing/priority/restore", include_in_schema=False)
def commercial_licensing_priority_restore(request: Request, account_id: int = Form(...)):
    """Director §5/§6: "Restaurar" — devuelve `account_id` a la tabla
    principal. Idempotente: restaurar una cuenta ya en la tabla principal
    es un éxito no-op, nunca un error."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        commercial_licensing_priority_repository.restore(account_id)
    except commercial_licensing_priority_repository.CommercialLicensingPriorityStorageError as exc:
        return JSONResponse({"state": "source_error", "message": str(exc)}, status_code=200)
    return JSONResponse({"state": "ok"})


@router.get("/comercial/data/lost-history", include_in_schema=False)
def commercial_data_lost_history(request: Request, tipo: str = Query("todos")):
    """Historial de Perdidas (Bucket 1, ver HANDOFF) — universo canónico
    `type=opportunity, active=False, probability=0`, TODOS los años excepto
    el actual (que vive en el KPI/drill "Perdidas" del dashboard principal,
    ya cerrado) + el bucket "Sin fecha". Reusa LITERAL
    `_resolve_lost_motivos`/`_filter_excluded_lost` — nunca una segunda
    definición de Perdidas. LAZY, MISMO contrato fail-soft y MISMA gate que
    el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        history = CommercialService().get_lost_history(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="lost_history")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="lost_history")
    return JSONResponse({"state": "ok", "lost_history": history})


@router.get("/comercial/data/forecast", include_in_schema=False)
def commercial_data_forecast(request: Request, tipo: str = Query("todos")):
    """Forecast por mes de cierre (Active Opportunities Phase 2, ver
    HANDOFF) — universo stages 0-8, el MISMO que Pipeline Total/Ponderado
    (nunca el de Oportunidades Activas, 1-8). Semántica legacy intencional,
    no un bug. MISMO contrato fail-soft y MISMA gate que el resto de
    Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        forecast = CommercialService().get_forecast(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="forecast")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="forecast")
    return JSONResponse({"state": "ok", "forecast": forecast})


@router.get("/comercial/data/creadas/anios-disponibles", include_in_schema=False)
def commercial_data_creadas_anios(request: Request):
    """Años SELECCIONABLES del drill — metadata, NO datos (owner decision,
    OPTION A). Rango contiguo desde el año de la oportunidad más antigua hasta
    el actual, con UNA consulta `limit=1`.

    Declara disponibilidad, no contenido: un año seleccionado puede devolver
    "Sin resultados" legítimamente, y eso NO es un error de descubrimiento
    (owner: "YEAR OPTION != YEAR GUARANTEED TO CONTAIN DATA"). Reconstruir el
    historial de etapas para poblar un `<select>` es exactamente el costo que
    el corrective de hidratación elimina — MISMO criterio que los
    `anios-disponibles` de Reporting, separados de sus datos a propósito.

    Endpoint separado y no un campo del payload de datos: si viajara adentro,
    el `<select>` sólo podría ofrecer los años ya hidratados.
    """
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        anios = CommercialService().creadas_selectable_years()
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="creadas-anios")
    except Exception as exc:  # noqa: BLE001
        return _commercial_data_error_response(request, exc, endpoint="creadas-anios")
    return JSONResponse({"state": "ok", "anios": anios})


@router.get("/comercial/data/creadas", include_in_schema=False)
def commercial_data_creadas(request: Request, tipo: str = Query("todos"),
                            anio: str = Query("")):
    """Drill de "Creadas" (Creadas Legacy Parity, ver HANDOFF — paridad con
    el legacy `/crm/opp-by-month`): grano mensual + por oportunidad
    (vendedor, cliente, origen, estado, horas) sobre el MISMO conjunto canónico
    de eventos que el KPI semanal de `/comercial/data/analytics` (ver
    `commercial_service._created_entry_events`) — nunca un segundo
    algoritmo de entrada al pipeline.

    HOT/COLD POR AÑO (owner performance corrective): `anio` acota el universo
    canónico a ESE año. Antes el endpoint no aceptaba año y devolvía TODOS los
    meses de la historia — el drill hidrataba 2019 aunque el análisis por
    default consuma el año actual. `anio` vacío resuelve al año actual, así que
    el contrato viejo sigue funcionando para cualquier consumidor que no lo
    mande.

    LAZY por diseño (owner directive — "the KPI must remain lightweight"):
    endpoint propio, SÓLO se pide al abrir el análisis desde el tile
    "Creadas esta semana" — nunca durante la hidratación normal del
    dashboard (`/comercial/data/summary|pipeline|analytics|opportunities`).
    MISMO contrato fail-soft y MISMA gate de sesión/entitlement que el
    resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        year = None
        if anio.strip().isdigit():
            year = int(anio.strip())
        creadas = CommercialService().get_creadas_detail(_normalize_tipo(tipo), year=year)
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="creadas")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="creadas")
    return JSONResponse({"state": "ok", "creadas": creadas})


@router.get("/comercial/data/activities", include_in_schema=False)
def commercial_data_activities(request: Request, tipo: str = Query("todos")):
    """Actividades pendientes (`mail.activity` sobre `crm.lead`). MISMO
    contrato fail-soft y MISMA gate de sesión/entitlement que summary/
    pipeline — read-only, sin ningún write hacia Odoo."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        activities = CommercialService().get_activities(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="activities")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="activities")
    return JSONResponse({"state": "ok", "activities": activities})


@router.get("/comercial/data/analytics", include_in_schema=False)
def commercial_data_analytics(request: Request, tipo: str = Query("todos")):
    """Analíticas del Pipeline (los seis titulares del bloque homólogo del
    CRM legacy) + "Creadas". MISMO contrato fail-soft y MISMA gate que el
    resto de Comercial — read-only, sin ningún write hacia Odoo."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        analytics = CommercialService().get_analytics(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="analytics")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="analytics")
    return JSONResponse({"state": "ok", "analytics": analytics})


@router.get("/comercial/data/pipeline-analytics-detail", include_in_schema=False)
def commercial_data_pipeline_analytics_detail(request: Request, tipo: str = Query("todos")):
    """Detalle compartido de CINCO tiles de "Analítica del Pipeline" (Edad
    del pipeline/Tiempo por etapa/Conversión etapa a etapa/Tiempo de
    cierre/Estancadas, ver HANDOFF) — comparten el MISMO universo
    (activas 1-8 + ganadas del año + historial de etapa), así que viven en
    UN solo endpoint lazy en vez de cinco. "Conversión de leads" NO vive acá
    — universo totalmente distinto, ver `/lead-conversion-detail`. MISMO
    contrato fail-soft y MISMA gate que el resto de Comercial —
    read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        detail = CommercialService().get_pipeline_analytics_detail(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="pipeline_analytics_detail")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="pipeline_analytics_detail")
    return JSONResponse({"state": "ok", "pipeline_analytics_detail": detail})


@router.get("/comercial/data/lead-conversion-detail", include_in_schema=False)
def commercial_data_lead_conversion_detail(request: Request, tipo: str = Query("todos")):
    """Detalle de "Conversión de leads" (ver HANDOFF) — universo propio
    (`crm.lead type=lead` + historial del campo `type`), desglosado por
    resultado (Prospecto/Activa/Ganada/Perdida — antes sólo "cualquier
    conversión"/"avanzó a oportunidad" en el titular EAGER). LAZY, MISMO
    contrato fail-soft y MISMA gate que el resto de Comercial —
    read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        detail = CommercialService().get_lead_conversion_detail(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="lead_conversion_detail")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="lead_conversion_detail")
    return JSONResponse({"state": "ok", "lead_conversion_detail": detail})


@router.get("/comercial/data/leads", include_in_schema=False)
def commercial_data_leads(request: Request, tipo: str = Query("todos")):
    """Leads (Bucket 2, ver HANDOFF) — universo propio `crm.lead
    type=lead, active=True`, agrupado por empresa. NUNCA
    type=opportunity (universo de Detalle Oportunidades/Prospectos/
    Licencias, ya cerrados) — un lead convertido cambia de tipo y
    desaparece SOLO de acá. LAZY, MISMO contrato fail-soft y MISMA gate
    que el resto de Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        leads = CommercialService().get_leads(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="leads")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="leads")
    return JSONResponse({"state": "ok", "leads": leads})


@router.get("/comercial/data/clients", include_in_schema=False)
def commercial_data_clients(request: Request, tipo: str = Query("todos")):
    """Clientes (Bucket 2, ver HANDOFF) — universo canónico: partners
    res.partner con la property "Es Cliente?" = "Si" (contactos
    colapsados a su empresa vía commercial_partner_id), con sus
    oportunidades activas (etapas 1-8) y prospectos (etapa 0). Fallback
    fail-soft a TODAS las oportunidades activas si esa property no
    existe en esta instancia de Odoo — nunca una lista vacía disfrazada.
    LAZY, MISMO contrato fail-soft y MISMA gate que el resto de
    Comercial — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        clients = CommercialService().get_clients(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="clients")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="clients")
    return JSONResponse({"state": "ok", "clients": clients})


@router.get("/comercial/data/ex-clients", include_in_schema=False)
def commercial_data_ex_clients(request: Request, tipo: str = Query("todos")):
    """Ex Clientes (Bucket 2, ver HANDOFF) — MISMA mecánica que Clientes,
    property "Es Cliente?" = "Ex" — universo propio (incluye historial de
    oportunidades ganadas/perdidas), endpoint y estado TOTALMENTE
    separados de Clientes (sub-tab lazy independiente, MISMO criterio que
    el legacy). LAZY, MISMO contrato fail-soft y MISMA gate — read-only."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    try:
        clients = CommercialService().get_ex_clients(_normalize_tipo(tipo))
    except (CommercialConfigurationError, CommercialDataError) as exc:
        return _commercial_data_error_response(request, exc, endpoint="ex_clients")
    except Exception as exc:  # noqa: BLE001 — inesperada, ver _commercial_data_error_response
        return _commercial_data_error_response(request, exc, endpoint="ex_clients")
    return JSONResponse({"state": "ok", "ex_clients": clients})


# ── Feedback + telemetría de Comercial (Homologación al Danwor Hub, ver
# HANDOFF) ────────────────────────────────────────────────────────────────
# Comercial es SPA client-side (commercial.js), no HTMX — MISMO criterio que
# Reporting (ver reporting.py) y Staffing: reusa las CAPACIDADES compartidas
# (mismo store de feedback, mismo pipeline de Activity) vía un endpoint
# JSON propio, en vez de inventar un almacenamiento paralelo. "La
# homologación al Hub debe ENRIQUECER las iniciativas, no debilitarlas" —
# CRM PARTICIPA del mismo patrón transversal, no lo copia.
#
# Granularidad: UN control por CAPABILITY — misma unidad que ya usa el nav
# (`COMMERCIAL_CAPABILITIES` en commercial.js) y MISMO criterio que "un
# control por MAIN TAB" en Reporting. Licencias y Detalle Oportunidades
# comparten el MISMO loader/dataset (`opportunities-detail`) pero son
# capabilities DISTINTAS en el nav, así que cada una cierra con su PROPIO
# feedback — compartir loader no implica compartir feedback, el nav ya las
# trata como entradas separadas. Clientes/Ex Clientes NO se separan: Ex
# Clientes es un sub-tab DENTRO de la capability "clientes", nunca su
# propia entrada de nav — MISMO criterio que Reporting/Cartera, cuyas
# perspectivas Mantenimiento/Proyecto comparten el feedback de su ÚNICO
# main tab "cartera" (ver `isActiveReportReady`/`reportingTabRawStatus` en
# reporting.js) — no se inventa una granularidad de sub-tab que el patrón
# transversal del Hub no usa en ningún otro lado.

# Títulos canónicos de las 7 capabilities — misma taxonomía que
# `COMMERCIAL_CAPABILITIES` en commercial.js. `capability` del cliente se
# valida contra esto (nunca se persiste/loguea una capability arbitraria
# inyectada) — MISMO criterio que `_REPORT_TABS` en reporting.py.
_COMMERCIAL_CAPABILITIES: dict[str, str] = {
    "dashboard": "Dashboard",
    "actividades": "Actividades",
    "licencias": "Licencias",
    "historial-perdidas": "Historial Perdidas",
    "detalle": "Detalle Oportunidades",
    "clientes": "Clientes",
    "leads": "Leads",
}


@router.post("/comercial/feedback", include_in_schema=False)
def commercial_feedback(request: Request, capability: str = Form(""), rating: str = Form(""),
                        comment: str = Form("")):
    """Feedback "¿Qué te pareció este reporte?" al cierre de cada capability.
    Reusa el store COMPARTIDO (`feedback_store` → `FEEDBACK_LOG_DIR`, mismo
    formato/analítica que Copilot/Reporting/Staffing) y emite un evento del
    MISMO pipeline de Activity (`log_activity('commercial.feedback', ...)`).
    NO crea almacenamiento propio de Comercial. Best-effort: nunca rompe la
    vista."""
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    cap = (capability or "").strip()
    if rating not in {"up", "down"} or cap not in _COMMERCIAL_CAPABILITIES:
        raise HTTPException(400, "Feedback inválido")
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    payload = {
        "type": "commercial_capability",
        "capability": cap,
        "capability_title": _COMMERCIAL_CAPABILITIES[cap],
        "rating": rating,
        "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
        "ts": now.isoformat(),
    }
    clean_comment = (comment or "").strip()
    if clean_comment:
        payload["comment"] = clean_comment
    feedback_store.write_feedback_record(payload, prefix="commercial_feedback", stamp=now.strftime("%Y%m%d_%H%M%S"))
    # Evento del pipeline de Activity del Hub (mismo sink que Copilot/
    # Reporting/Staffing). Metadata semántica: capability + sentimiento
    # (nunca valores de negocio).
    log_activity(
        "commercial.feedback", user=user, scope=SCOPE_COMMERCIAL, area=AREA_CRM,
        module=CM01_MODULE_ID, capability=cap,
        feedback_sentiment=("positive" if rating == "up" else "negative"),
    )
    return JSONResponse({"state": "ok"})
