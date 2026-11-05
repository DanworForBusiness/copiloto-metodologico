"""Authenticated Operations requests pages."""
from urllib.error import HTTPError, URLError
import hashlib
import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from core.content_registry import DEFAULT_PACK
from webapp import hub_metrics_access, operations_access, workspaces
from webapp.services import auth_service, pagination, solicitudes_snapshot
from webapp.services import solicitudes_creation as creation
from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_journal as journal
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
    user = _user(request)
    if not user:
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
            rows = await run_in_threadpool(solicitudes_snapshot.read, user.get("sub"), graph_token,
                                           refresh=form.get("refresh") == "1")
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
    sort = str(form.get("sort", ""))
    sort = sort if sort in service.SORT_COLUMNS else ""
    direction = "desc" if form.get("dir") == "desc" else "asc"
    matches = service.select_rows(rows, query, selected)
    classified, unknown_ids = service.classify_rows(matches)
    # Los KPIs resumen las solicitudes VIGENTES (filtros y búsqueda aplicados, antes de paginar). Sin lectura confirmada no se muestra un cero como resultado.
    kpis = None if error else service.kpi_counts(classified['vigentes'])
    kpi_labels = {key: label for key, label, _tone in service.KPIS}
    kpi = str(form.get("kpi", ""))
    kpi = kpi if kpi in kpi_labels and not error else ""
    if kpi:
        classified['vigentes'] = [row for row in classified['vigentes'] if service.kpi_match(row, kpi)]   # clic en un tile: Vigentes muestra el alcance de ese KPI
    groups = []
    for key, title in (('vigentes', 'Solicitudes Vigentes'), ('cerradas', 'Solicitudes Cerradas')):
        group_sort = str(form.get('sort_' + key, sort))
        group_sort = group_sort if group_sort in service.SORT_COLUMNS else ''
        group_dir = 'desc' if form.get('dir_' + key, direction) == 'desc' else 'asc'
        ordered = service.sort_rows(classified[key], group_sort, group_dir)
        pager = pagination.paginate(ordered, page=form.get('page_' + key, form.get('page') if key == 'vigentes' else None), sizes=(50,), default_size=50)
        groups.append({'key': key, 'title': title, 'rows': pager['items'], 'pager': pager,
                       'count': len(ordered), 'sort': group_sort, 'direction': group_dir})
    pager = groups[0]['pager']
    return templates.TemplateResponse(request=request, name="partials/solicitudes_panel.html", context={
        "rows": pager["items"], "pager": pager, "groups": groups, "unknown_ids": unknown_ids, "kpis": kpis, "kpi_defs": service.KPIS, "kpi": kpi, "kpi_label": kpi_labels.get(kpi, ""),
        "count": len(matches), "source_count": len(rows), "error": error, "query": query,
        "selection_scope": hashlib.sha256((str(user.get('sub', '')) + ':operaciones:' + repository.LIST_ID).encode()).hexdigest(),
        "universe_ids": [str(row['id']) for row in rows],
        "selected": selected, "filters": FILTERS, "options": options,
        "columns": service.SORT_COLUMNS, "sort": sort, "direction": direction,
        "display_date": service.display_date, "created": service.CREATION_DATE, "planned": service.PLANNED,
        "status": service.STATUS, "funnel": service.FUNNEL,
        "assignees": service.ASSIGNEES, "values": service.values, "project_label": service.row_project_label,
    })


def _etag(item):
    return item.get("eTag") or item.get("@odata.etag") or ""


@router.get(PATH + "/nueva", response_class=HTMLResponse, include_in_schema=False)
def solicitudes_new(request: Request):
    response = solicitudes_page(request)
    if hasattr(response, "context"):
        response.context["is_new"] = True
        return templates.TemplateResponse(request=request, name="operations/solicitudes.html", context=response.context)
    return response


@router.post(PATH + "/nueva/datos", response_class=HTMLResponse, include_in_schema=False)
def solicitudes_new_data(request: Request, graph_token: str = Form("")):
    user = _user(request)
    if not user or not user.get("sub"):
        raise HTTPException(401)
    if not graph_token:
        return _edit_panel(request, "", error="No pudimos obtener tu token de Microsoft.")
    try:
        columns = repository.read_creation_columns(graph_token)
        options = repository.lookup_options(graph_token, columns=columns)
        ticket = creation.issue(user["sub"])
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        return _edit_panel(request, "", error=_edit_error(exc))
    values = service.form_values({service.STATUS: "Pendiente" if "Pendiente" in options[service.STATUS] else ""})
    title_column = next((column for column in columns if column["name"] == "Title"), {})
    return templates.TemplateResponse(request=request, name="partials/solicitudes_edit.html", context={
        "is_new": True, "submission_id": ticket, "values": values, "current": values,
        "fields": service, "original_fields": {}, "options": options,
        "status_choices": options[service.STATUS], "project_display": "", "title_max_length": title_column.get("text", {}).get("maxLength"),
    })


@router.post(PATH + "/nueva", response_class=HTMLResponse, include_in_schema=False)
async def solicitudes_create(request: Request, graph_token: str = Form("")):
    from webapp import same_origin_guard

    user = _user(request)
    if not user:
        raise HTTPException(401)
    if not user.get("sub") or not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403)
    form = await request.form()
    allowed = set(creation.CREATE_FIELDS) | {"graph_token", "token_diagnostic", "submission_id"}
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form if key != service.ASSIGNEES):
        return _creation_feedback(request, "invalid", "El formulario contiene campos no permitidos. Revisá el borrador.")
    if not graph_token:
        return _creation_feedback(request, "auth", "Autorizá la escritura con Microsoft y después volvé a crear la solicitud. Tu borrador se conserva.")
    submitted = {key: str(form[key]) for key in creation.CREATE_FIELDS if key in form and key != service.ASSIGNEES}
    submitted[service.ASSIGNEES] = [str(value) for value in form.getlist(service.ASSIGNEES) if value]
    for key in service.NARRATIVE_FIELDS:
        if key in submitted:
            submitted[key] = service.normalize_narrative(submitted[key])
    try:
        item_id = await run_in_threadpool(creation.create, user["sub"], str(form.get("submission_id", "")), graph_token, submitted)
    except creation.UncertainCreation:
        return _creation_feedback(request, "uncertain", "No pudimos confirmar el resultado. Revisá el listado antes de iniciar otra alta. Este envío no se repetirá para evitar duplicados.")
    except creation.SubmissionError as exc:
        return _creation_feedback(request, "review", str(exc))
    except ValueError as exc:
        return _creation_feedback(request, "invalid", str(exc))
    except (HTTPError, URLError, TimeoutError, KeyError) as exc:
        return _creation_feedback(request, "auth" if getattr(exc, "code", None) == 401 else "error", _edit_error(exc))
    response = _creation_feedback(request, "saved", "Solicitud creada y confirmada en SharePoint.")
    import json
    response.headers["HX-Trigger-After-Swap"] = json.dumps({"solicitudes-created": {"url": f"{PATH}/{item_id}/editar"}})
    return response


def _creation_feedback(request, state, message):
    return templates.TemplateResponse(request=request, name="partials/solicitudes_create_feedback.html", context={
        "state": state, "message": message,
    })


@router.get(PATH + "/{item_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def solicitudes_edit(request: Request, item_id: str):
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)
    response = solicitudes_page(request)
    if hasattr(response, "context"):
        response.context["item_id"] = item_id
        response.context["display_only"] = request.query_params.get("mode") == "display"
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
    user = _user(request) or {}
    template = "partials/solicitudes_display.html" if request.query_params.get("mode") == "display" else "partials/solicitudes_edit.html"
    return templates.TemplateResponse(request=request, name=template, context={
        "item_id": item_id, "values": values, "current": values,
        "options": options or {}, "error": error,
        "project_display": service.project_label(item["fields"], (options or {}).get(service.PROJECT)) if item else "",
        "etag": _etag(item) if item else "", "funnel": item["fields"].get(service.FUNNEL) if item else "",
        "status_choices": (options or {}).get(service.STATUS, []),
        "observation_ticket": journal.issue(item_id, user.get("sub")) if item else "",
        "history_rows": journal.history(item["fields"].get(service.COMMENTS), item_id) if item else [],
        "fields": service, "original_fields": item["fields"] if item else {}, **_detail_context(item),
    })


def _feedback(request, item_id, state, message, **context):
    # HTMX swaps only the status region. Validation and remote failures must
    # leave the form controls, dirty state and original ETag intact.
    return templates.TemplateResponse(request=request, name="partials/solicitudes_feedback.html", context={
        "item_id": item_id, "state": state, "message": message,
        "is_list": request.headers.get("HX-Target") == "solicitudes-list-result", **context,
    })


def _edit_error(exc):
    if isinstance(exc, service.StatusCatalogError):
        return str(exc)
    return {
        400: "SharePoint rechazó los valores enviados. Revisá los campos; tu borrador se conserva.",
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


@router.post(PATH + "/{item_id}/historial", response_class=HTMLResponse, include_in_schema=False)
def solicitudes_history(request: Request, item_id: str, graph_token: str = Form("")):
    if not _user(request):
        raise HTTPException(401)
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)
    rows, coverage, failure = [], journal.coverage_summary(()), None
    if not graph_token:
        failure = ("auth", True, "No se obtuvo la autorización de Microsoft para consultar las versiones. Reintentá sin volver a guardar.")
    else:
        item = None
        try:
            item = repository.read_item(graph_token, item_id)
            rows = journal.history(item['fields'].get(service.COMMENTS), item_id)          # las observaciones se conservan aunque fallen las versiones
            result = repository.read_item_versions(graph_token, item_id)
            rows, coverage = journal.version_history_coverage(item['fields'].get(service.COMMENTS), item_id, result['versions'],
                                                              issues=result.get('issues') or (('version_read_error',) if result.get('partial') else ()),
                                                              current_modified=item.get('lastModifiedDateTime'))
        except HTTPError as exc:
            failure = ({401: ("auth", True, "La autorización de Microsoft venció antes de leer las versiones. Las observaciones disponibles se conservan: reintentá la consulta."),
                        403: ("access", False, "Tu cuenta no tiene acceso a las versiones de esta solicitud en SharePoint. Las observaciones disponibles se conservan.")}
                       .get(exc.code, ("read", True, "SharePoint no respondió al leer las versiones. Las observaciones disponibles se conservan: reintentá la consulta; no vuelvas a guardar.")))
        except (URLError, TimeoutError):
            failure = ("read", True, "No se pudo conectar con SharePoint para leer las versiones. Las observaciones disponibles se conservan: reintentá la consulta; no vuelvas a guardar.")
        except (ValueError, KeyError):
            failure = ("read", True, "No se pudieron verificar los movimientos operativos en SharePoint. Las observaciones disponibles se conservan. Reintentá la consulta; no vuelvas a guardar.")
    # Diagnóstico sin datos: solo los códigos de cobertura (nunca contenido, actores ni tokens).
    logging.getLogger("danwor.solicitudes.history").info("solicitudes.history coverage=%s failure=%s",
                                                         ",".join(coverage["codes"]) or "complete", failure[0] if failure else "none")
    return templates.TemplateResponse(request=request, name="partials/solicitudes_history.html", context={
        'item_id': item_id, 'history_rows': rows, 'history_loaded': True, 'history_coverage': coverage,
        'history_failure': {"kind": failure[0], "retryable": failure[1], "message": failure[2]} if failure else None,
    })


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
    allowed = (set(service.EDITABLE) - {service.COMMENTS}) | {"graph_token", "token_diagnostic", "etag", "confirm_admission", "new_observation", "observation_ticket"}
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
    observation_id = ""
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
            new_observation = str(form.get("new_observation", "")).strip()
            columns = None
            if new_observation:
                columns = await run_in_threadpool(repository.read_creation_columns, graph_token)
                payload[service.COMMENTS], observation_id = journal.append(
                    item["fields"].get(service.COMMENTS), new_observation,
                    str(form.get("observation_ticket", "")), item_id, user, columns,
                )
            if admission.proposes_approval(item["fields"], submitted, payload):
                if columns is None:
                    columns = await run_in_threadpool(repository.read_creation_columns, graph_token)
                payload = admission.approval_patch(
                    item["fields"], submitted, payload, columns,
                    confirmed=form.get("confirm_admission") == "yes",
                )
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
        await run_in_threadpool(solicitudes_snapshot.invalidate, user["sub"])
        try:
            updated = await run_in_threadpool(repository.read_item, graph_token, item_id)
            observed = service.form_values(updated["fields"])
            matches = all(
                set(observed[key]) == set(value) if key == service.ASSIGNEES else observed[key] == value
                for key, value in submitted.items()
                if key in payload or key + "LookupId" in payload
            )
            if service.FUNNEL in payload:
                matches = matches and updated["fields"].get(service.FUNNEL) == payload[service.FUNNEL]
            if observation_id:
                matches = matches and journal.confirms_append(
                    payload[service.COMMENTS], updated["fields"].get(service.COMMENTS), item_id, observation_id)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
            matches = False
        if not matches or not _etag(updated):
            outcome = "uncertain"
            return _feedback(request, item_id, "uncertain", "No pudimos confirmar el guardado. Consultá los valores vigentes antes de reintentar.")
        outcome = "confirmed"
        if payload.get(service.FUNNEL) == admission.APPROVED:
            admission.audit_approval(user['sub'], item_id, correlation)
        return _feedback(request, item_id, "saved", "Guardado confirmado en SharePoint.",
                         history_rows=journal.history(updated["fields"].get(service.COMMENTS), item_id),
                         refresh_history=True, **_detail_context(updated))
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        return _feedback(request, item_id, "auth" if getattr(exc, "code", None) == 401 else "error", _edit_error(exc))
    finally:
        logging.getLogger("danwor.solicitudes.audit").info(
            "actor=%s item=%s fields=%s outcome=%s correlation=%s",
            user["sub"], item_id, ",".join(sorted(payload)), outcome, correlation,
        )


@router.post(PATH + "/{item_id}/rechazar", response_class=HTMLResponse, include_in_schema=False)
async def solicitudes_reject(request: Request, item_id: str, graph_token: str = Form("")):
    import logging
    from uuid import uuid4
    from webapp import same_origin_guard

    user = _user(request)
    if not user:
        raise HTTPException(401)
    if not user.get("sub") or not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403)
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)

    def feedback(state, message, **context):
        return _feedback(request, item_id, state, message, rejection=True, **context)

    form = await request.form()
    allowed = {"graph_token", "token_diagnostic", "etag", "reason", "confirm_rejection", "observation_ticket"}
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form):
        return feedback("invalid", "El rechazo contiene campos no permitidos. No se guardaron cambios.")
    if not graph_token:
        return feedback("auth", "Autorizá la escritura con Microsoft; tu justificación se conserva.")
    reason = str(form.get("reason", "")).strip()
    if not reason or form.get("confirm_rejection") != "yes":
        return feedback("invalid", "Ingresá una justificación y confirmá expresamente el rechazo.")
    outcome, payload, correlation = "read_error", {}, uuid4().hex
    try:
        item = await run_in_threadpool(repository.read_item, graph_token, item_id)
        etag = _etag(item)
        if not etag or etag == "*" or etag != form.get("etag"):
            outcome = "conflict"
            return feedback("conflict", "La solicitud cambió. Conservá la justificación y consultá los valores vigentes antes de rechazar.")
        columns = await run_in_threadpool(repository.read_creation_columns, graph_token)
        admission.validate_column(columns)
        if item["fields"].get(service.FUNNEL) != admission.REQUESTED:
            outcome = "existing_decision"
            return feedback("conflict", "La solicitud ya tiene una decisión de admisión; no se modificó.")
        reason = admission.rejection_reason(item["fields"], reason, columns, confirmed=True)
        comments, entry_id = journal.append(
            item["fields"].get(service.COMMENTS), reason,
            str(form.get("observation_ticket", "")), item_id, user, columns,
            movement="Rechazo / Derivación a Operaciones",
        )
        payload = {service.FUNNEL: admission.REJECTED, service.COMMENTS: comments}
        try:
            await run_in_threadpool(repository.update_item, graph_token, item_id, payload, etag)
        except HTTPError as exc:
            if exc.code == 412:
                outcome = "conflict"
                return feedback("conflict", "Otro usuario modificó la solicitud. No se sobrescribió su decisión.")
            if exc.code < 500:
                outcome = f"http_{exc.code}"
                return feedback("auth" if exc.code == 401 else "error", _edit_error(exc))
            outcome = "uncertain"
        except (URLError, TimeoutError, ValueError):
            outcome = "uncertain"
        await run_in_threadpool(solicitudes_snapshot.invalidate, user["sub"])
        try:
            updated = await run_in_threadpool(repository.read_item, graph_token, item_id)
            matches = (bool(_etag(updated)) and updated["fields"].get(service.FUNNEL) == admission.REJECTED
                       and journal.confirms_append(comments, updated["fields"].get(service.COMMENTS), item_id, entry_id))
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
            matches = False
        if not matches:
            outcome = "uncertain"
            return feedback("uncertain", "No pudimos confirmar el rechazo. Revisá los valores vigentes antes de reintentar; la justificación se conserva.")
        outcome = "confirmed"
        return feedback("saved", "Rechazo confirmado en SharePoint.",
                        history_rows=journal.history(updated["fields"].get(service.COMMENTS), item_id),
                        refresh_history=True, **_detail_context(updated))
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        outcome = "invalid" if isinstance(exc, ValueError) else "read_error"
        return feedback("invalid" if isinstance(exc, ValueError) else "error", str(exc) if isinstance(exc, ValueError) else _edit_error(exc))
    finally:
        logging.getLogger("danwor.solicitudes.audit").info(
            "actor=%s item=%s fields=%s operation=reject outcome=%s correlation=%s",
            user["sub"], item_id, ",".join(sorted(payload)), outcome, correlation,
        )


@router.post(PATH + "/{item_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
async def solicitudes_delete(request: Request, item_id: str, graph_token: str = Form("")):
    import json
    import logging
    from uuid import uuid4
    from webapp import same_origin_guard

    user = _user(request)
    if not user:
        raise HTTPException(401)
    if not user.get("sub") or not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403)
    if not item_id.isascii() or not item_id.isdecimal():
        raise HTTPException(404)
    form = await request.form()
    if (set(form) - {"graph_token", "token_diagnostic", "etag", "confirm_delete"}
            or any(len(form.getlist(key)) != 1 for key in form)
            or form.get("confirm_delete") != "yes"):
        return _feedback(request, item_id, "invalid", "Confirmá explícitamente la eliminación de la solicitud.")
    if not graph_token:
        return _feedback(request, item_id, "auth", "Autorizá la escritura con Microsoft antes de eliminar. Tu borrador se conserva.")
    outcome = "read_error"
    correlation = uuid4().hex
    try:
        item = await run_in_threadpool(repository.read_item, graph_token, item_id)
        etag = _etag(item)
        if not etag or etag == "*" or etag != form.get("etag"):
            outcome = "conflict"
            return _feedback(request, item_id, "conflict", "La solicitud cambió. Consultá los valores vigentes antes de eliminar.")
        outcome = "uncertain"
        try:
            await run_in_threadpool(repository.delete_item, graph_token, item_id, etag)
        except HTTPError as exc:
            if exc.code == 412:
                outcome = "conflict"
                return _feedback(request, item_id, "conflict", "Otro usuario modificó la solicitud. La eliminación fue rechazada.")
            if exc.code < 500 and exc.code != 404:
                outcome = f"http_{exc.code}"
                return _feedback(request, item_id, "auth" if exc.code == 401 else "error", _edit_error(exc))
        except (URLError, TimeoutError, ValueError):
            pass
        await run_in_threadpool(solicitudes_snapshot.invalidate, user["sub"])
        try:
            await run_in_threadpool(repository.read_item, graph_token, item_id)
        except HTTPError as exc:
            if exc.code == 404:
                outcome = "confirmed_absent"
                response = _feedback(request, item_id, "deleted", "La solicitud ya no está en la lista de SharePoint.")
                response.headers["HX-Trigger-After-Swap"] = json.dumps({"solicitudes-deleted": {"url": PATH}})
                return response
        except (URLError, TimeoutError, ValueError, KeyError):
            pass
        return _feedback(request, item_id, "uncertain", "No pudimos confirmar la eliminación. Revisá el listado antes de intentar nuevamente.")
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        return _feedback(request, item_id, "auth" if getattr(exc, "code", None) == 401 else "error", _edit_error(exc))
    finally:
        logging.getLogger("danwor.solicitudes.audit").info(
            "actor=%s item=%s operation=delete outcome=%s correlation=%s",
            user["sub"], item_id, outcome, correlation,
        )
