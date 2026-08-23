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

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, workspaces
from webapp.services import auth_service
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


@router.get("/comercial/data/creadas", include_in_schema=False)
def commercial_data_creadas(request: Request, tipo: str = Query("todos")):
    """Drill de "Creadas" (Creadas Legacy Parity, ver HANDOFF — paridad con
    el legacy `/crm/opp-by-month`): grano mensual + por oportunidad
    (vendedor, cliente, origen, estado, horas) sobre el MISMO conjunto canónico
    de eventos que el KPI semanal de `/comercial/data/analytics` (ver
    `commercial_service._created_entry_events`) — nunca un segundo
    algoritmo de entrada al pipeline.

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
        creadas = CommercialService().get_creadas_detail(_normalize_tipo(tipo))
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
