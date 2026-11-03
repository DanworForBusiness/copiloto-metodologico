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
        "status": service.STATUS, "funnel": service.FUNNEL,
        "assignees": service.ASSIGNEES, "values": service.values, "project_label": service.row_project_label,
    })


def _etag(item):
    return item.get("eTag") or item.get("@odata.etag") or ""


@router.get(PATH + "/{item_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def solicitudes_edit(request: Request, item_id: str):
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)
    response = solicitudes_page(request)
    if hasattr(response, "context"):
        response.context["item_id"] = item_id
        return templates.TemplateResponse(request=request, name="operations/solicitudes.html", context=response.context)
    return response


def _detail_context(item):
    if not item:
        return {}
    fields = item["fields"]
    return {
        "detail_loaded": True,
        "refresh_hero": True,
        "detail_title": (fields.get("Title") or "").strip(),
        "detail_badges": (
            ("Status funnel", fields.get(service.FUNNEL)),
            ("Status Arquitectura", fields.get(service.STATUS)),
            ("Prioridad", fields.get("Prioridad")),
        ),
    }


def _edit_panel(request, item_id, *, item=None, options=None, error=""):
    values = service.form_values(item["fields"]) if item else {}
    return templates.TemplateResponse(request=request, name="partials/solicitudes_edit.html", context={
        "item_id": item_id, "values": values, "current": values,
        "options": options or {}, "error": error,
        "project_display": service.project_label(item["fields"], (options or {}).get(service.PROJECT)) if item else "",
        "etag": _etag(item) if item else "", "funnel": item["fields"].get(service.FUNNEL) if item else "",
        "destinations": service.allowed_destinations(values.get(service.STATUS)),
        "fields": service, "original_fields": item["fields"] if item else {}, **_detail_context(item),
    })


def _feedback(request, item_id, state, message, **context):
    # HTMX swaps only the status region. Validation and remote failures must
    # leave the form controls, dirty state and original ETag intact.
    return templates.TemplateResponse(request=request, name="partials/solicitudes_feedback.html", context={
        "item_id": item_id, "state": state, "message": message, **context,
    })


def _edit_error(exc):
    return {
        401: "La autorización de Microsoft venció. Volvé a iniciar sesión.",
        403: "Microsoft Graph denegó la operación para tu cuenta.",
        404: "La solicitud ya no está disponible.",
        429: "Microsoft Graph limitó temporalmente las consultas. Intentá más tarde.",
    }.get(getattr(exc, "code", None), "No se pudieron consultar los datos de SharePoint.")


@router.post(PATH + "/{item_id}/detalle", response_class=HTMLResponse, include_in_schema=False)
def solicitudes_detail(request: Request, item_id: str, graph_token: str = Form("")):
    if not _user(request):
        raise HTTPException(401)
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)
    if not graph_token:
        return _edit_panel(request, item_id, error="No pudimos obtener tu token de Microsoft.")
    try:
        item = repository.read_item(graph_token, item_id)
        options = repository.lookup_options(graph_token)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        return _edit_panel(request, item_id, error=_edit_error(exc))
    return _edit_panel(request, item_id, item=item, options=options)


@router.post(PATH + "/{item_id}/guardar", response_class=HTMLResponse, include_in_schema=False)
async def solicitudes_save(request: Request, item_id: str, graph_token: str = Form("")):
    import logging
    from uuid import uuid4

    from webapp import same_origin_guard

    user = _user(request)
    if not user:
        raise HTTPException(401)
    if not same_origin_guard.is_same_origin_write(request) or not user.get("sub"):
        raise HTTPException(403)
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)
    form = await request.form()
    allowed = set(service.EDITABLE) | {"graph_token", "token_diagnostic", "etag"}
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form if key != service.ASSIGNEES):
        if request.headers.get("HX-Request") == "true":
            return _feedback(request, item_id, "invalid", "El formulario contiene campos inválidos. Revisá el borrador antes de volver a intentar.")
        raise HTTPException(422, "Campos de guardado inválidos")
    if not graph_token:
        return _feedback(request, item_id, "auth", "Volvé a autenticarte con Microsoft sin cerrar este formulario y después reintentá.")
    submitted = {key: str(form[key]) for key in service.EDITABLE if key in form and key != service.ASSIGNEES}
    for key in service.NARRATIVE_FIELDS:
        if key in submitted:
            submitted[key] = service.normalize_narrative(submitted[key])
    if service.ASSIGNEES in form:
        submitted[service.ASSIGNEES] = [str(value) for value in form.getlist(service.ASSIGNEES) if value != ""]
    correlation = uuid4().hex
    outcome = "read_error"
    payload = {}
    try:
        item = await run_in_threadpool(repository.read_item, graph_token, item_id)
        etag = _etag(item)
        if not etag or etag == "*" or etag != form.get("etag"):
            outcome = "conflict"
            return _feedback(request, item_id, "conflict", "La versión cambió. Consultá los valores vigentes antes de volver a guardar.")
        options = await run_in_threadpool(repository.lookup_options, graph_token)
        current = service.form_values(item["fields"])
        try:
            payload = service.changes(current, submitted, options)
        except ValueError as exc:
            outcome = "invalid_fields"
            return _feedback(request, item_id, "invalid", str(exc))
        if not payload:
            outcome = "unchanged"
            return _feedback(request, item_id, "unchanged", "No hay cambios para guardar.")
        try:
            await run_in_threadpool(repository.update_item, graph_token, item_id, payload, etag)
        except HTTPError as exc:
            if exc.code == 412:
                outcome = "conflict"
                return _feedback(request, item_id, "conflict", "Otro usuario modificó la solicitud durante el guardado. Compará los valores vigentes con tu borrador.")
            if exc.code < 500:
                outcome = f"http_{exc.code}"
                return _feedback(request, item_id, "auth" if getattr(exc, "code", None) == 401 else "error", _edit_error(exc))
            outcome = "uncertain"
        except (URLError, TimeoutError, ValueError):
            outcome = "uncertain"
        else:
            outcome = "verify"
        try:
            updated = await run_in_threadpool(repository.read_item, graph_token, item_id)
            observed = service.form_values(updated["fields"])
            matches = all(
                set(observed[key]) == set(value) if key == service.ASSIGNEES else observed[key] == value
                for key, value in submitted.items()
                if key in payload or key + "LookupId" in payload
            )
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
            matches = False
        if not matches or not _etag(updated):
            outcome = "uncertain"
            return _feedback(request, item_id, "uncertain", "No pudimos confirmar el guardado. Consultá los valores vigentes antes de reintentar.")
        outcome = "confirmed"
        return _feedback(request, item_id, "saved", "Guardado confirmado en SharePoint.", **_detail_context(updated))
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        return _feedback(request, item_id, "auth" if getattr(exc, "code", None) == 401 else "error", _edit_error(exc))
    finally:
        logging.getLogger("danwor.solicitudes.audit").info(
            "actor=%s item=%s fields=%s outcome=%s correlation=%s",
            user["sub"], item_id, ",".join(sorted(payload)), outcome, correlation,
        )
