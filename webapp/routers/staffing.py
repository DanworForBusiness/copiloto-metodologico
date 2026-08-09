"""
staffing.py — Operations / Staffing HTTP (Batch 3 de aceleración post-Fase 2).

Router de las 2 rutas del primer slice read-only de Staffing:
`GET /operations/staffing` (shell autenticado, sin Graph) y
`POST /operations/staffing/data` (recibe `graph_token` explícito del
browser, delega en `StaffingService`, devuelve un fragmento HTML para
HTMX). Namespace `/operations/...` deliberado: primer HTTP real del
workspace Operations.

El token Graph delegado sigue viviendo en `sessionStorage` del browser
(mismo mecanismo de `webapp/static/auth.js` que ya usa el panel de Admin —
`hx-vals='js:{graph_token: sessionStorage.getItem("dw-graph-token") ...}'`);
este router NUNCA lo posee, lo persiste ni lo loguea. `GET` no requiere
`graph_token` ni llama a Graph — el token del browser no está disponible en
un GET server-side normal (arquitectura preservada de Batch 1: el
transporte compartido no posee tokens).

Clasificación: OPERATIONS / STAFFING HTTP. NOT PLATFORM / SHARED. NO
importa `webapp.main`, no construye URLs de Graph, no posee el mapeo de
campos, no posee transporte Graph, no realiza cálculos de negocio — todo
eso vive en `StaffingService`/`StaffingRepository`/`core.microsoft365`
(Batch 1/2). Consume `webapp.operations_access` (política mínima y
provisoria, separada de `webapp.access`).

El deep-link no autenticado entra por `webapp.workspaces.platform_login_redirect`
(batch de transición multi-workspace) — NO por `webapp.nav.login_redirect`
(Copilot-específico): Operations es un workspace distinto del Copiloto, así
que su puerta de entrada es la platform-neutral (`/platform`), preservando
`next` con el mismo mecanismo de siempre.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from webapp import operations_access, workspaces
from webapp.services import auth_service
from webapp.services.activity_service import log_verbose
from webapp.services.staffing_service import StaffingService
from webapp.templating import templates

router = APIRouter()


def _require_operations_session(request: Request):
    """Redirect (sin sesión) o 403 (sesión sin tier "full") — o None si OK.
    Compartido por GET y POST para no duplicar la regla de acceso."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_staffing_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Staffing")
    return None


@router.get("/operations/staffing", response_class=HTMLResponse, include_in_schema=False)
def staffing_page(request: Request):
    redirect = _require_operations_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="operations/staffing.html",
        context={
            "current_workspace": workspaces.OPERATIONS,
            "available_workspaces": workspaces.available_workspaces(user),
        },
    )


@router.post("/operations/staffing/data", response_class=HTMLResponse, include_in_schema=False)
def staffing_data(request: Request, graph_token: str = Form("")):
    redirect = _require_operations_session(request)
    if redirect is not None:
        return redirect

    if not graph_token:
        return templates.TemplateResponse(
            request=request, name="operations/staffing_rows.html", context={"state": "missing_token"},
        )

    user = auth_service.session_user(request)
    try:
        rows = StaffingService().get_overview(graph_token)
    except RuntimeError:
        # Config de Staffing incompleta (site_id / field mapping) — ver Batch 2/3.
        log_verbose("staffing_overview_config_incomplete", user=user)
        return templates.TemplateResponse(
            request=request, name="operations/staffing_rows.html", context={"state": "config_incomplete"},
        )
    except Exception as exc:  # noqa: BLE001 — falla de Graph/transporte, no de config
        log_verbose("staffing_overview_error", user=user, error_type=type(exc).__name__)
        return templates.TemplateResponse(
            request=request, name="operations/staffing_rows.html", context={"state": "graph_error"},
        )

    return templates.TemplateResponse(
        request=request, name="operations/staffing_rows.html", context={"state": "ok", "rows": rows},
    )
