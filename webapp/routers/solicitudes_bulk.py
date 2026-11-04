"""Solicitudes selection dialogs; all Microsoft calls stay server-side."""
from collections import Counter
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from webapp import same_origin_guard
from webapp.routers import solicitudes
from webapp.services import solicitudes_bulk as bulk
from webapp.services import solicitudes_service as service
from webapp.templating import templates

router = APIRouter()
PARAMETERS = {
    'compartir': {'selected_ids'}, 'preparar': {'selected_ids'}, 'consolidar': {'selected_ids'},
    'revisar': {'plan_id', 'field', 'value', 'assignee_value'} | {prefix + key for key in bulk.FIELDS for prefix in ('apply_', 'value_')},
    'ejecutar': {'plan_id', 'confirm'}, 'reintentar': {'plan_id'},
    'estado': {'plan_id'},
}


def _view(plan):
    return 'edit' if plan['state'] == 'editing' else 'review' if plan['state'] == 'reviewed' else 'results'


def render(request, mode, **context):
    plan = context.get('plan') or {}
    return templates.TemplateResponse(request=request, name='partials/solicitudes_bulk.html', context={
        'mode': mode, 'fields': service, 'bulk_fields': bulk.FIELDS, 'status_labels': bulk.LABELS,
        'counts': Counter(row['status'] for row in plan.get('rows', [])), **context,
    })


@router.post(solicitudes.PATH + '/bulk/{action}', response_class=HTMLResponse, include_in_schema=False)
async def solicitudes_bulk_action(request: Request, action: str):
    user = solicitudes._user(request)
    if not user or not user.get('sub'):
        raise HTTPException(401)
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403)
    if action not in PARAMETERS:
        raise HTTPException(404)
    form = await request.form()
    if set(form) - (PARAMETERS[action] | {'graph_token', 'token_diagnostic'}) or any(len(form.getlist(k)) != 1 for k in form):
        return render(request, 'error', message='La operación contiene parámetros no permitidos.')
    token, actor = str(form.get('graph_token', '')), user['sub']
    if action == 'estado':
        try:
            plan = bulk.get(actor, str(form.get('plan_id', '')))
            return render(request, _view(plan), plan=plan, paused=True)
        except ValueError as exc:
            return render(request, 'error', message=str(exc))
    if not token:
        if action == 'ejecutar':
            try:
                plan = bulk.get(actor, str(form.get('plan_id', '')))
                return render(request, _view(plan), plan=plan,
                              paused=True, authorize=True, message='La autorización de Microsoft no está disponible. Los resultados y las solicitudes pendientes se conservan.')
            except ValueError:
                pass
        return render(request, 'error', authorize=action == 'ejecutar', message='No se obtuvo la autorización de Microsoft. Tu revisión se conserva; autorizá y prepará nuevamente el envío.')
    try:
        if action == 'compartir':
            items = await run_in_threadpool(bulk.selection, actor, token, bulk.parse_ids(form.get('selected_ids')))
            return render(request, 'share', items=items)
        if action == 'consolidar':
            items, comparisons, candidates = await run_in_threadpool(bulk.consolidation, actor, token, bulk.parse_ids(form.get('selected_ids')))
            return render(request, 'consolidate', items=items, comparisons=comparisons, candidates=candidates)
        if action == 'preparar':
            plan = await run_in_threadpool(bulk.prepare, actor, token, bulk.parse_ids(form.get('selected_ids')))
            return render(request, 'edit', plan=plan)
        plan_id = str(form.get('plan_id', ''))
        if action == 'revisar':
            field = str(form.get('field', ''))
            value = str(form.get('assignee_value' if field == service.ASSIGNEES else 'value', ''))
            if 'field' not in form:
                field = '__multiple__'
                value = {key: str(form.get('value_' + key, '')) for key in bulk.FIELDS if form.get('apply_' + key) == 'yes'}
            plan = await run_in_threadpool(bulk.review, actor, plan_id, token, field, value)
            return render(request, 'review', plan=plan)
        if action == 'reintentar':
            plan = await run_in_threadpool(bulk.retry, actor, plan_id, token)
            return render(request, 'edit', plan=plan, retry=True)
        plan = await run_in_threadpool(bulk.execute, actor, plan_id, token, confirmed=form.get('confirm') == 'yes')
        return render(request, 'results', plan=plan)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else solicitudes._edit_error(exc)
        if action in ('revisar', 'ejecutar'):
            try:
                plan = bulk.get(actor, str(form.get('plan_id', '')))
                if action == 'revisar' and plan['state'] in ('editing', 'reviewed'):
                    field = str(form.get('field', ''))
                    if field in bulk.FIELDS:
                        plan.update(field=field, value=str(form.get('assignee_value' if field == service.ASSIGNEES else 'value', '')))
                    if 'field' not in form:
                        plan['changes'] = {key: str(form.get('value_' + key, '')) for key in bulk.FIELDS if form.get('apply_' + key) == 'yes'}
                    return render(request, 'edit', plan=plan, message=message)
                return render(request, _view(plan), plan=plan, paused=True, message=message)
            except ValueError:
                pass
        return render(request, 'error', message=message)
