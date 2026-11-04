"""Actor-bound bulk plans: one field, reviewed ETags, per-item outcomes.

Plans are process-local like creation tickets. Unknown/expired plans fail closed;
no token is retained and confirmed or uncertain rows are never auto-replayed.
"""
from copy import deepcopy
import json
import logging
import threading
import time
from uuid import uuid4
from urllib.error import HTTPError, URLError

from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service
from webapp.services import solicitudes_snapshot as snapshot

FIELDS = {service.STATUS: "Status Arquitectura", "Prioridad": "Prioridad", service.PROJECT: "Proyecto",
          service.ASSIGNEES: "Colaborador asignado", service.PLANNED: "Fecha de inicio planificada",
          service.ACTUAL: "Fecha de inicio real"}
FAILURES = frozenset(("conflict", "rejected", "uncertain"))
LABELS = {"pending": "Pendiente de ejecutar", "processing": "En proceso", "confirmed": "Confirmada",
          "unchanged": "Sin cambios", "conflict": "Conflicto", "rejected": "Rechazada", "uncertain": "Resultado incierto"}
_lock = threading.Lock()
_plans = {}
TTL = 1800
CHUNK = 5


def parse_ids(value):
    try:
        ids = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("Seleccioná solicitudes válidas.") from exc
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.isascii()
            or not i.isdecimal() or int(i) < 1 or str(int(i)) != i for i in ids)):
        raise ValueError("Seleccioná solicitudes válidas.")
    return list(dict.fromkeys(ids))


def selection(actor, token, ids, *, require_all=True):
    rows = snapshot.read(actor, token, refresh=True)
    by_id = {str(row['id']): row for row in rows}
    if require_all and any(i not in by_id for i in ids):
        raise ValueError("Algunas solicitudes ya no están disponibles. Actualizá el listado y revisá la selección.")
    return [deepcopy(by_id[i]) for i in ids if i in by_id]


def _record(actor, plan_id):
    plan = _plans.get(plan_id)
    if not plan or plan['actor'] != actor or plan['expires'] <= time.monotonic():
        raise ValueError("La revisión venció o no pertenece a tu sesión. Volvé a preparar el ajuste; no se repetirá ningún envío.")
    return plan


def get(actor, plan_id):
    with _lock:
        return deepcopy(_record(actor, plan_id))


def catalogs(token):
    columns = repository.read_creation_columns(token)
    options = repository.lookup_options(token, (service.STATUS, service.ASSIGNEES, service.PROJECT), columns=columns)
    return columns, options


def prepare(actor, token, ids, *, retry=False):
    selection(actor, token, ids, require_all=not retry)
    _, options = catalogs(token)
    with _lock:
        for key in [key for key, plan in _plans.items() if plan['expires'] <= time.monotonic()]:
            del _plans[key]
        if len(_plans) >= 512:
            raise ValueError("No se pudo preparar otra revisión. Intentá nuevamente más tarde.")
        key = uuid4().hex
        _plans[key] = {'id': key, 'actor': actor, 'expires': time.monotonic() + TTL,
                       'ids': list(ids), 'options': options, 'state': 'editing',
                       'rows': [], 'field': service.STATUS, 'value': '', 'busy': False}
    return get(actor, key)


def display(fields, key, options, row=None):
    value = service.form_values(fields).get(key)
    if key == service.ASSIGNEES:
        return ', '.join(options.get(key, {}).get(i, 'Referencia no disponible') for i in value) or 'Sin asignación'
    if key in (service.PLANNED, service.ACTUAL):
        return service.display_date(value)
    if key == service.PROJECT:
        return service.project_label(fields, options.get(key, {})) or '—'
    return value or '—'


def _patch(fields, field, value, options, columns):
    definition = next((c for c in columns if c.get('name') == field), {})
    kind = 'lookup' if field in (service.ASSIGNEES, service.PROJECT) else 'dateTime' if field in (service.PLANNED, service.ACTUAL) else 'choice'
    if (kind not in definition or definition.get('readOnly') or definition.get('calculated') is not None
            or (field == service.ASSIGNEES and definition['lookup'].get('allowMultipleValues') is not True)
            or (field == service.PROJECT and definition['lookup'].get('allowMultipleValues') is True)):
        raise ValueError("El esquema vigente de SharePoint no permite este ajuste.")
    payload = service.changes(service.form_values(fields), {field: value}, options)
    return admission.approval_patch(fields, {field: value}, payload, columns, confirmed=True)


def _patch_many(fields, targets, options, columns):
    for key, value in targets.items():
        _patch(fields, key, value, options, columns)
    payload = service.changes(service.form_values(fields), targets, options)
    return admission.approval_patch(fields, targets, payload, columns, confirmed=True)


def review(actor, plan_id, token, field, value):
    raw = value if field == '__multiple__' else {field: value}
    if not isinstance(raw, dict) or not raw or set(raw) - FIELDS.keys():
        raise ValueError("Seleccioná únicamente campos autorizados para el ajuste.")
    if any(not isinstance(v, str) for v in raw.values()):
        raise ValueError("Ingresá un único valor por campo.")
    field, value = next(iter(raw.items()))
    old = get(actor, plan_id)
    if old['state'] not in ('editing', 'reviewed'):
        raise ValueError("Este ajuste ya comenzó. Revisá sus resultados antes de preparar otro.")
    if not isinstance(value, str):
        raise ValueError("Ingresá un único valor para el campo elegido.")
    targets = {k: ([v] if v else []) if k == service.ASSIGNEES else v for k, v in raw.items()}
    target = targets[field]
    columns, options = catalogs(token)
    for key, selected in targets.items():
        _patch({}, key, selected, options, columns)
    fresh = snapshot.read(actor, token, refresh=True)
    by_id = {str(row['id']): row for row in fresh}
    rows = []
    for item_id in old['ids']:
        item = by_id.get(item_id)
        row = {'id': item_id, 'title': (item or {}).get('fields', {}).get('Title') or 'Solicitud #' + item_id,
               'status': 'pending', 'message': '', 'derived': False, 'before': '—', 'after': '—'}
        try:
            if not item:
                raise ValueError("No disponible para tu cuenta.")
            etag = item.get('eTag') or item.get('@odata.etag')
            if not etag or etag == '*':
                raise ValueError("No se pudo verificar su versión.")
            payload = _patch_many(item['fields'], targets, options, columns)
            row.update(etag=etag, derived=service.FUNNEL in payload,
                       before=' · '.join(FIELDS[k] + ': ' + display(item['fields'], k, options) for k in targets),
                       after=' · '.join(FIELDS[k] + ': ' + display({**item['fields'], **{k + 'LookupId' if k in (service.ASSIGNEES, service.PROJECT) else k: v}}, k, options) for k,v in targets.items()))
            if not payload:
                row['status'] = 'unchanged'
        except ValueError as exc:
            row.update(status='rejected', message=str(exc))
        rows.append(row)
    with _lock:
        plan = _record(actor, plan_id)
        if plan['state'] not in ('editing', 'reviewed'):
            raise ValueError("El ajuste ya comenzó; no se modificó su revisión.")
        plan.update(rows=rows, field=field, value=value, target=target, targets=targets, changes=raw, options=options, state='reviewed')
    return get(actor, plan_id)


def _one(row, plan, token, columns, options):
    payload, attempted = {}, False
    result = dict(row)
    try:
        item = repository.read_item(token, row['id'])
        etag = item.get('eTag') or item.get('@odata.etag')
        if not etag or etag == '*' or etag != row['etag']:
            result.update(status='conflict', message='La versión cambió desde la revisión; no se sobrescribió.')
            return result, attempted
        payload = _patch_many(item['fields'], plan['targets'], options, columns)
        if not payload:
            result.update(status='unchanged', message='El valor vigente ya coincide.')
            return result, attempted
        attempted = True
        try:
            repository.update_item(token, row['id'], payload, etag)
        except HTTPError as exc:
            if exc.code < 500:
                result.update(status='conflict' if exc.code == 412 else 'rejected',
                              message={401: 'La autorización Microsoft venció.', 403: 'Microsoft denegó la escritura.',
                                       412: 'Conflicto de versión.', 429: 'Microsoft limitó las operaciones. Revisá antes de reintentar.'}.get(exc.code, 'Microsoft rechazó la operación.'))
                return result, attempted
        except (URLError, TimeoutError, ValueError):
            pass
        try:
            updated = repository.read_item(token, row['id'])
            matches = not service.changes(service.form_values(updated['fields']), plan['targets'], options)
            if service.FUNNEL in payload:
                matches = matches and updated['fields'].get(service.FUNNEL) == payload[service.FUNNEL]
            matches = matches and bool(updated.get('eTag') or updated.get('@odata.etag'))
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
            matches = False
        result.update(status='confirmed' if matches else 'uncertain',
                      message='Relectura confirmada.' if matches else 'No se pudo confirmar. No se repetirá automáticamente.')
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        result.update(status='uncertain' if attempted else 'rejected', message=str(exc) if isinstance(exc, ValueError) else 'No se pudo validar el registro con Microsoft.')
    finally:
        logging.getLogger('danwor.solicitudes.audit').info(
            'actor=%s operation=bulk correlation=%s item=%s fields=%s outcome=%s',
            plan['actor'], plan['id'], row['id'], ','.join(sorted(payload)), result['status'])
    return result, attempted


def execute(actor, plan_id, token, *, confirmed):
    if confirmed is not True:
        raise ValueError("Confirmá expresamente el ajuste revisado.")
    with _lock:
        plan = _record(actor, plan_id)
        if plan['state'] not in ('reviewed', 'running', 'complete'):
            raise ValueError("Revisá los registros y el valor antes de ejecutar.")
        if plan['busy']:
            raise ValueError("Este ajuste ya está procesándose. No se volvió a enviar.")
        pending = [i for i, row in enumerate(plan['rows']) if row['status'] == 'pending'][:CHUNK]
        if not pending:
            plan['state'] = 'complete'
            return deepcopy(plan)
        plan['busy'], plan['state'] = True, 'running'
        for i in pending:
            plan['rows'][i]['status'] = 'processing'
        local = deepcopy(plan)
    attempted = False
    results = {}
    try:
        columns, options = catalogs(token)
        for i in pending:
            results[i], wrote = _one(local['rows'][i], local, token, columns, options)
            attempted = attempted or wrote
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
        for i in pending:
            results.setdefault(i, dict(local['rows'][i], status='rejected', message='No se pudieron validar los catálogos con Microsoft. Revisá antes de reintentar.'))
    finally:
        if attempted:
            snapshot.invalidate(actor)
        with _lock:
            for i in pending:
                plan['rows'][i] = results.get(i, dict(local['rows'][i], status='uncertain', message='Resultado no confirmado. Revisá el registro antes de reintentar.'))
            plan['busy'] = False
            if not any(row['status'] == 'pending' for row in plan['rows']):
                plan['state'] = 'complete'
    return get(actor, plan_id)


def retry(actor, plan_id, token):
    old = get(actor, plan_id)
    if old['state'] != 'complete' or old['busy']:
        raise ValueError("Esperá los resultados del ajuste antes de preparar un reintento.")
    ids = [row['id'] for row in old['rows'] if row['status'] in FAILURES]
    if not ids:
        raise ValueError("No hay resultados fallidos que revisar.")
    plan = prepare(actor, token, ids, retry=True)
    with _lock:
        _plans[plan['id']].update(field=old['field'], value=old['value'], changes=old.get('changes', {}))
    return get(actor, plan['id'])


def consolidation(actor, token, ids):
    if len(ids) < 2:
        raise ValueError("Seleccioná al menos dos solicitudes para la previsualización.")
    items = selection(actor, token, ids)
    columns, options = catalogs(token)
    comparisons = []
    for key, label in [(service.PROJECT, 'Proyecto'), (service.FUNNEL, 'Funnel'), *FIELDS.items()]:
        values = [display(item['fields'], key, options, item) if key != service.FUNNEL else item['fields'].get(key) or '—' for item in items]
        unique = set(values)
        comparisons.append({'label': label, 'values': values,
                            'kind': 'Coincide' if len(unique) == 1 else 'Posible conflicto' if len(unique - {'—', 'Sin asignación'}) > 1 else 'Diferente'})
    candidates = [column.get('displayName') or column['name'] for column in columns
                  if (column.get('lookup') or {}).get('listId', '').strip('{}').lower() == repository.LIST_ID.lower()]
    return items, comparisons, candidates
