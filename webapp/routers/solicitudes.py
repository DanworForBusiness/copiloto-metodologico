"""Authenticated Operations requests pages."""
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from core.content_registry import DEFAULT_PACK
from webapp import hub_metrics_access, operations_access, workspaces
from webapp.services import auth_service
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service
from webapp.templating import templates

router = APIRouter()
PATH = "/operaciones/solicitudes"
FILTERS = {
    service.STATUS: "Status Arquitectura",
    "Prioridad": "Prioridad",
    service.FUNNEL: "Status funnel",
    service.ASSIGNEES: "Colaborador Asignado",
}


def _user(request):
    user = auth_service.session_user(request)
    if user and not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operaciones")
    return user


@router.get(PATH, response_class=HTMLResponse, include_in_schema=False)
def solicitudes_page(request: Request):
    user = _user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    return templates.TemplateResponse(request=request, name="operations/solicitudes.html", context={
        "current_workspace": workspaces.OPERATIONS,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "solicitudes",
        "can_hub_metrics": hub_metrics_access.is_vertical_metrics_enabled("operations")
        and hub_metrics_access.can_access_hub_metrics(user),
    })


@router.post(PATH + "/data", response_class=HTMLResponse, include_in_schema=False)
async def solicitudes_data(request: Request, graph_token: str = Form("")):
    if not _user(request):
        raise HTTPException(401, "Sin sesión")
    form = await request.form()
    selected = {key: str(form.get(key, "")) for key in FILTERS}
    query = str(form.get("q", ""))
    error = ""
    rows = []
    if not graph_token:
        error = "No pudimos obtener tu token de Microsoft. Volvé a iniciar sesión."
    else:
        try:
            rows = await run_in_threadpool(repository.read_requests, graph_token)
        except HTTPError as exc:
            error = {
                401: "La autorización de Microsoft venció. Volvé a iniciar sesión.",
                403: "Tu cuenta no tiene acceso a esta lista mediante Microsoft Graph.",
                429: "Microsoft Graph limitó temporalmente las consultas. Intentá nuevamente más tarde.",
            }.get(exc.code, "No se pudieron cargar las solicitudes desde SharePoint.")
        except (URLError, TimeoutError, ValueError, KeyError):
            error = "No se pudieron cargar todas las solicitudes desde SharePoint."
    options = {
        key: sorted({value for row in rows for value in service.values(row.get("fields", {}).get(key))})
        for key in FILTERS
    }
    matches = service.select_rows(rows, query, selected)
    pages = max(1, (len(matches) + 49) // 50)
    raw_page = str(form.get("page", "1"))
    page = min(pages, max(1, int(raw_page))) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) < 8 else 1
    return templates.TemplateResponse(request=request, name="partials/solicitudes_panel.html", context={
        "rows": matches[(page - 1) * 50:page * 50], "page": page, "pages": pages,
        "count": len(matches), "source_count": len(rows), "error": error, "query": query,
        "selected": selected, "filters": FILTERS, "options": options,
        "description": service.DESCRIPTION, "status": service.STATUS, "funnel": service.FUNNEL,
        "assignees": service.ASSIGNEES, "plain_text": service.plain_text, "values": service.values,
    })
