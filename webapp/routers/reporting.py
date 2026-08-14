"""
reporting.py — Operations / Reporting Integral V0 preview (ver HANDOFF).

`GET /operaciones/reporting/integral` — superficie de PREVISUALIZACIÓN
interna para que el owner revise el prototipo de consolidación del reporte
Power BI "Danwor - Tablero Integral" (12 páginas → 6 experiencias) dentro
del Hub real, no como HTML standalone. Deliberadamente mínima: sin Graph,
sin persistencia, sin fragmento HTMX — el dataset es mock y vive embebido
client-side (`webapp/static/reporting.js`, portado 1:1 del prototipo
standalone `prototypes/reporting-integral-v0/index.html`, que sigue siendo
la referencia de diseño). Un solo GET, sin POST — no hay nada que cargar
de una fuente externa todavía.

Agregada a `partials/operations_nav.html` como "02 Reporting" (Align with
Hub backbone corrective, ver HANDOFF) — YA es navegación real, no solo URL
directa. Sigue sin ser la ruta canónica final: el nombre/ubicación pueden
cambiar cuando Reporting deje de ser un preview.

Reusa el MISMO gate de autorización que Staffing
(`webapp.operations_access.is_operations_authorized` + sesión vía
`auth_service`) — NUNCA un mecanismo de auth nuevo. Mismo criterio que
`staffing_page`: sin `graph_token`, sin llamada a Graph en el GET (acá
directamente no hay ninguna, ni siquiera vía POST — todo el dato es mock
embebido en el JS servido).

Clasificación: OPERATIONS / REPORTING HTTP (preview). NOT PLATFORM /
SHARED. NO importa `webapp.main`, no posee transporte Graph, no define
lógica de negocio de Reporting (eso vive enteramente en
`webapp/static/reporting.js` mientras siga siendo mock — se moverá a un
service real cuando haya fuente de datos)."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.content_registry import DEFAULT_PACK
from webapp import operations_access, workspaces
from webapp.services import auth_service
from webapp.services.reporting_repository import ReportingGraphError, ReportingSourceError
from webapp.services.reporting_service import ReportingService
from webapp.templating import templates

router = APIRouter()


@router.get("/operaciones/reporting/integral", response_class=HTMLResponse, include_in_schema=False)
def reporting_integral_preview(request: Request):
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="operations/reporting_integral.html",
        context={
            "current_workspace": workspaces.OPERATIONS,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            # Resalta "02 Reporting" en operations_nav.html.
            "active_page": "reporting",
        },
    )


@router.post("/operaciones/reporting/integral/data/cartera", include_in_schema=False)
def reporting_cartera_data(request: Request, graph_token: str = Form("")):
    """Datos REALES normalizados de Cartera (Reporting Integral real-data Fase 1).
    MISMO gate de Operations + MISMO token delegado del browser que Staffing —
    sin auth ni cliente Graph nuevos. Fail-soft/localizado: cualquier falla
    devuelve HTTP 200 con `state != "ok"` (el renderer entra en su estado de
    error propio de Cartera; el resto de Reporting sigue usable). NUNCA expone
    el token, el JSON crudo de Graph ni internal names de SharePoint."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        cartera = service.get_cartera(graph_token)
    except RuntimeError:
        # Config incompleta (site/list ID sin configurar) — nunca datos.
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001 — falla de transporte/parse, nunca cruda al cliente
        return JSONResponse({"state": "source_error", "stage": "unknown"})
    return JSONResponse({"state": "ok", "cartera": cartera})
