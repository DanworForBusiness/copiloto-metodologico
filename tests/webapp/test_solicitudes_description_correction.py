"""Descripción de la demanda evolutiva (Operaciones / Solicitudes).

Etapa 1 — no tomada (Funnel Solicitado, sin colaborador, sin aclaraciones): cualquier usuario autorizado en Operaciones corrige la
Descripción por reemplazo; queda auditada en el Historial (versiones de SharePoint).
Etapa 2 — tomada y vigente: texto consolidado en solo lectura + aclaraciones firmadas que se AGREGAN (mismo mecanismo que las
observaciones de Arquitectura). Nadie (owner incluido) sobrescribe el texto consolidado. Rechazadas / cerradas: solo lectura.
Ninguna de las dos capacidades otorga can_edit ni permisos de Arquitectura. Graph simulado."""
import pytest
from bs4 import BeautifulSoup

from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_bulk as bulk
from webapp.services import solicitudes_journal as journal
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service
from tests.webapp.test_solicitudes_permissions import ORIGIN, environment, save  # noqa: F401

ORIGINAL = '<div>Texto original</div>'
OPEN = {service.FUNNEL: admission.REQUESTED, service.STATUS: 'Pendiente', service.ASSIGNEES + 'LookupId': [],
        service.DESCRIPTION: ORIGINAL}
RICH = {'readOnly': False, 'text': {'allowMultipleLines': True, 'appendChangesToExistingText': False, 'textType': 'richText'}}
COLUMNS = [{'name': service.COMMENTS, **RICH}, {'name': service.DESCRIPTION, **RICH}]
APPROVED = {service.FUNNEL: admission.APPROVED}
ASSIGNED = {service.ASSIGNEES + 'LookupId': ['9']}


@pytest.fixture
def open_request(environment, monkeypatch):  # noqa: F811
    client, state, item, writes, options = environment
    item['fields'] = dict(OPEN)
    state['actor'] = 'yanina@example.com'                                       # ni creadora, ni asignada, ni owner
    monkeypatch.setattr(repository, 'read_creation_columns', lambda *args: COLUMNS)
    return environment


def detail(env, query=''):
    return env[0].post('/operaciones/solicitudes/1/detalle' + query, data={'graph_token': 'fake'}).text


def take(env, change):
    env[2]['fields'].update(change)


def clarify(env, text, actor=None, ticket=None, **extra):
    actor = actor or env[1]['actor']
    return save(env, new_clarification=text, clarification_ticket=ticket or journal.issue('1', actor), **extra)


def writable(html):
    form = BeautifulSoup(html, 'html.parser').select_one('form[data-solicitudes-description-correction]')
    return sorted(el['name'] for el in form.select('[name]')) if form else []


# ── Etapa 1: corrección directa ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_untaken_request_offers_only_the_description_to_an_unrelated_operations_user(open_request):
    html = detail(open_request)
    soup = BeautifulSoup(html, 'html.parser')
    assert writable(html) == sorted([service.DESCRIPTION, 'etag'])
    form = soup.select_one('form[data-solicitudes-description-correction]')
    assert form['hx-post'] == '/operaciones/solicitudes/1/guardar' and not form.has_attr('data-solicitudes-admission')
    assert '/eliminar' not in html and '/rechazar' not in html and 'new_observation' not in html and 'new_clarification' not in html
    assert not [el for el in soup.select('[name]') if not el.find_parent('form', attrs={'data-solicitudes-description-correction': True})]


def test_description_correction_is_written_alone_with_etag_and_without_admission(open_request):
    take(open_request, {service.PLANNED: '2026-12-01T00:00:00Z'})           # compromiso preexistente: el creador propondría admitir
    response = save(open_request, **{service.DESCRIPTION: 'Texto corregido'})
    assert response.status_code == 200 and 'Guardado confirmado' in response.text
    assert open_request[3] == [{service.DESCRIPTION: '<div>Texto corregido</div>'}]


@pytest.mark.parametrize('field,value', [
    ('Prioridad', 'Alta'), (service.PROJECT, '3'), (service.PLANNED, '2026-12-01'), (service.ACTUAL, '2026-12-01'),
    (service.STATUS, 'En curso'), (service.ASSIGNEES, '10'), ('new_observation', 'Hola'), ('confirm_admission', 'yes'),
    ('observation_ticket', 'x'), ('new_clarification', 'Hola'),
])
def test_any_other_field_is_rejected_before_writing(open_request, field, value):
    response = save(open_request, **{service.DESCRIPTION: 'Texto corregido', field: value})
    assert response.status_code == 403 and not open_request[3]


def test_clarification_is_not_offered_nor_accepted_before_the_request_is_taken(open_request):
    assert 'new_clarification' not in detail(open_request)
    assert clarify(open_request, 'Aclaración prematura').status_code == 403 and not open_request[3]


def test_stale_etag_is_a_conflict_without_writing(open_request):
    response = save(open_request, etag='"6"', **{service.DESCRIPTION: 'Texto corregido'})
    assert 'La versión cambió' in response.text and not open_request[3]


# ── Transición a modo acumulativo ───────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('taken', [APPROVED, ASSIGNED, {**APPROVED, **ASSIGNED},
                                   {service.ASSIGNEES + 'LookupId': None, service.ASSIGNEES: [{'LookupId': 9, 'LookupValue': 'Assigned'}]},
                                   {service.FUNNEL: None}])                    # Funnel vacío: falla cerrado, nunca edición directa
def test_taken_request_switches_to_consolidated_description_plus_empty_clarification(open_request, taken):
    take(open_request, taken)
    html = detail(open_request)
    assert writable(html) == sorted(['clarification_ticket', 'etag', 'new_clarification'])
    soup = BeautifulSoup(html, 'html.parser')
    assert soup.select_one('#sol-description').has_attr('readonly') and soup.select_one('#sol-description').text == 'Texto original'
    assert soup.select_one('#sol-clarification').text == ''                     # nunca precargado con el contenido histórico
    assert save(open_request, **{service.DESCRIPTION: 'Sobrescritura'}).status_code == 403 and not open_request[3]


def test_assignment_after_form_load_revokes_the_direct_correction(open_request):
    assert service.DESCRIPTION in writable(detail(open_request))
    take(open_request, ASSIGNED)                                                 # Arquitectura tomó la demanda mientras tanto
    assert save(open_request, **{service.DESCRIPTION: 'Texto corregido'}).status_code == 403 and not open_request[3]


@pytest.mark.parametrize('actor', ['creator@example.com', 'assigned@example.com', 'owner@example.com'])
def test_nobody_overwrites_the_consolidated_description(open_request, actor):
    take(open_request, {**APPROVED, **ASSIGNED})
    open_request[1]['actor'] = actor
    html = detail(open_request)
    assert 'name="' + service.DESCRIPTION + '"' not in html and 'name="new_clarification"' in html
    response = save(open_request, **{service.DESCRIPTION: 'Sobrescritura'})
    assert response.status_code == 403 and not open_request[3]


def test_unassignment_after_clarifications_keeps_the_accumulative_mode(open_request):
    take(open_request, ASSIGNED)
    assert 'Guardado confirmado' in clarify(open_request, 'Primera aclaración').text
    take(open_request, {service.ASSIGNEES + 'LookupId': []})                    # vuelve a Solicitado sin asignar, pero con entradas firmadas
    assert journal.description_mode(open_request[2]['fields'], '1') == 'append'
    assert save(open_request, etag='"8"', **{service.DESCRIPTION: 'Reemplazo'}).status_code == 403


# ── Aclaraciones acumulativas ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_two_consecutive_clarifications_preserve_the_text_and_keep_authorship(open_request):
    take(open_request, APPROVED)
    first = clarify(open_request, 'Alcance: modelos de ventas.')
    assert first.status_code == 200 and 'Guardado confirmado' in first.text and 'data-clarification=' in first.text
    open_request[1]['actor'] = 'otra@example.com'
    second = clarify(open_request, 'Agregar región\nNorte.', etag='"8"')
    assert second.status_code == 200 and 'Guardado confirmado' in second.text
    stored = open_request[2]['fields'][service.DESCRIPTION]
    assert stored.startswith(ORIGINAL)                                           # el texto aceptado queda byte a byte
    assert list(open_request[3][0]) == [service.DESCRIPTION] and list(open_request[3][1]) == [service.DESCRIPTION]
    consolidated, clarifications = journal.description_view(stored, '1')
    assert consolidated == 'Texto original'
    assert [(c['actor'], c['text']) for c in clarifications] == [('yanina@example.com', 'Alcance: modelos de ventas.'),
                                                                  ('otra@example.com', 'Agregar región\nNorte.')]
    html = detail(open_request)
    assert html.index('Alcance: modelos de ventas.') < html.index('Agregar región')   # cronológico
    assert BeautifulSoup(html, 'html.parser').select_one('#sol-description').text == 'Texto original'


def test_retry_with_the_same_ticket_never_duplicates(open_request):
    take(open_request, APPROVED)
    ticket = journal.issue('1', open_request[1]['actor'])
    assert 'Guardado confirmado' in clarify(open_request, 'Única', ticket=ticket).text
    retry = clarify(open_request, 'Única', ticket=ticket, etag='"8"')
    assert 'ya está registrada' in retry.text and len(open_request[3]) == 1


def test_clarification_with_a_stale_etag_is_a_conflict(open_request):
    take(open_request, APPROVED)
    assert 'La versión cambió' in clarify(open_request, 'Tarde', etag='"6"').text and not open_request[3]


def test_unsigned_or_foreign_ticket_is_rejected(open_request):
    take(open_request, APPROVED)
    assert 'nueva versión del formulario' in clarify(open_request, 'X', ticket='tampered').text
    assert 'no pertenece' in clarify(open_request, 'X', ticket=journal.issue('1', 'otra@example.com')).text
    assert not open_request[3]


@pytest.mark.parametrize('field,value', [
    ('Prioridad', 'Alta'), (service.PLANNED, '2026-12-01'), (service.STATUS, 'En curso'), (service.ASSIGNEES, '10'),
    ('new_observation', 'Hola'), ('confirm_admission', 'yes'), (service.DESCRIPTION, 'Sobrescritura'),
])
def test_clarifying_grants_no_other_field(open_request, field, value):
    take(open_request, APPROVED)
    assert clarify(open_request, 'Aclaración', **{field: value}).status_code == 403 and not open_request[3]


def test_non_rich_text_description_column_fails_closed(open_request, monkeypatch):
    take(open_request, APPROVED)
    monkeypatch.setattr(repository, 'read_creation_columns',
                        lambda *a: [COLUMNS[0], {'name': service.DESCRIPTION, 'readOnly': False, 'text': {'allowMultipleLines': True, 'textType': 'plain'}}])
    assert 'configuración de Descripción de la demanda' in clarify(open_request, 'Aclaración').text and not open_request[3]


def test_creator_can_clarify_together_with_general_fields_but_not_overwrite(open_request):
    take(open_request, APPROVED)
    open_request[1]['actor'] = 'creator@example.com'
    response = clarify(open_request, 'Del creador', Prioridad='Alta')
    assert 'Guardado confirmado' in response.text
    assert set(open_request[3][0]) == {'Prioridad', service.DESCRIPTION}


@pytest.mark.parametrize('closed', [{service.FUNNEL: admission.REJECTED}, {**APPROVED, service.STATUS: 'Finalizado'},
                                    {**APPROVED, **ASSIGNED, service.STATUS: 'Suspendido'}])
def test_rejected_or_closed_requests_stay_read_only(open_request, closed):
    take(open_request, closed)
    html = detail(open_request)
    assert writable(html) == [] and 'new_clarification' not in html and '/guardar' not in html
    assert clarify(open_request, 'Tarde').status_code == 403 and not open_request[3]


def test_existing_descriptions_read_as_consolidated_without_entries(open_request):
    legacy = '<p>Pedido <b>histórico</b></p><p>04/11/2026 — texto que parece una entrada</p>'
    take(open_request, {**APPROVED, service.DESCRIPTION: legacy})
    consolidated, clarifications = journal.description_view(legacy, '1')
    assert clarifications == [] and consolidated == service.narrative_text(legacy)
    assert BeautifulSoup(detail(open_request), 'html.parser').select_one('#sol-description').text == consolidated


def test_signed_entries_do_not_cross_columns():
    user = {'sub': 'actor@example.com'}
    comments, _ = journal.append('', 'Observación', journal.issue('1', user['sub']), '1', user, COLUMNS)
    clarified, _ = journal.append('', 'Aclaración', journal.issue('1', user['sub']), '1', user, COLUMNS,
                                  movement=journal.CLARIFICATION, column=service.DESCRIPTION)
    assert journal.entries(comments, '1', service.DESCRIPTION).entries == []
    assert journal.entries(clarified, '1').entries == []
    with pytest.raises(ValueError):
        journal.append('', 'X', journal.issue('1', user['sub']), '1', user, COLUMNS, column=service.DESCRIPTION)


# ── Regresión de permisos (PR #304) ─────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('action,data', [
    ('rechazar', {'reason': 'No aplica', 'confirm_rejection': 'yes'}),
    ('eliminar', {'confirm_delete': 'yes'}),
])
@pytest.mark.parametrize('state', [{}, APPROVED])
def test_description_capabilities_grant_neither_delete_nor_reject(open_request, action, data, state):
    take(open_request, state)
    result = open_request[0].post('/operaciones/solicitudes/1/' + action, headers=ORIGIN,
                                  data={'graph_token': 'fake', 'etag': '"7"', **data})
    assert result.status_code == 403 and not open_request[3]


def test_bulk_is_not_widened(open_request):
    row = {'id': '1', 'etag': '"7"', 'status': 'pending'}
    result, attempted = bulk._one(row, {'actor': 'yanina@example.com', 'targets': {'Prioridad': 'Alta'}, 'id': 't'}, 'fake', [], open_request[4])
    assert result['status'] == 'rejected' and not attempted and not open_request[3]


def test_explicit_display_mode_stays_read_only(open_request):
    assert writable(detail(open_request, '?mode=display')) == []
    take(open_request, APPROVED)
    assert writable(detail(open_request, '?mode=display')) == []


def test_creator_keeps_the_full_edit_form_before_the_take(open_request):
    open_request[1]['actor'] = 'creator@example.com'
    html = detail(open_request)
    assert 'data-solicitudes-description-correction' not in html and 'name="Prioridad"' in html
    assert 'name="' + service.DESCRIPTION + '"' in html


# ── Historial ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _version(number, description, who):
    return {'id': f'{number}.0', 'lastModifiedDateTime': f'2026-11-25T18:{number:02}:00Z',
            'lastModifiedBy': {'user': {'displayName': who}}, 'fields': {**OPEN, service.DESCRIPTION: description}}


def test_history_records_who_when_previous_and_new_description():
    rows, coverage = journal.version_history_coverage('', '8', [
        _version(3, '<div>Texto corregido<br>con detalle</div>', 'Yanina Prueba'),
        _version(2, '<p>Texto original</p>', 'Owner'),                        # solo re-serialización HTML: no es un cambio
        _version(1, '<div>Texto original</div>', 'Owner')])
    changes = [r for r in rows if r['movement'] == 'Cambio de descripción']
    assert len(changes) == 1 and coverage['level'] != 'warning'
    row = changes[0]
    assert row['actor'] == 'Yanina Prueba' and row['date'] == '25/11/2026 15:03' and row['at'].startswith('2026-11-25T18:03')
    assert row['text'] == 'Anterior: Texto original\nNueva: Texto corregido\ncon detalle'
    assert row['version'] == '3.0' and row['source'] == 'SharePoint'


def test_history_shows_each_clarification_once_and_never_as_a_rewrite():
    user = {'sub': 'yanina@example.com'}
    clarified, entry_id = journal.append(ORIGINAL, 'Alcance ampliado', journal.issue('8', user['sub']), '8', user, COLUMNS,
                                         movement=journal.CLARIFICATION, column=service.DESCRIPTION)
    rows, _ = journal.version_history_coverage('', '8', [_version(1, ORIGINAL, 'Owner'), _version(2, clarified, 'Yanina')],
                                               description=clarified)
    assert not [r for r in rows if r['movement'] == 'Cambio de descripción']
    clarifications = [r for r in rows if r['movement'] == journal.CLARIFICATION]
    assert len(clarifications) == 1 and clarifications[0]['id'] == entry_id
    assert clarifications[0]['actor'] == 'yanina@example.com' and clarifications[0]['text'] == 'Alcance ampliado'


def test_history_route_integrates_clarifications(open_request, monkeypatch):
    take(open_request, APPROVED)
    clarify(open_request, 'Visible en el historial')
    monkeypatch.setattr(repository, 'read_item_versions', lambda *a: {'versions': [], 'partial': False, 'issues': []})
    html = open_request[0].post('/operaciones/solicitudes/1/historial', data={'graph_token': 'fake'}).text
    assert journal.CLARIFICATION in html and 'Visible en el historial' in html and 'yanina@example.com' in html


# ── Integridad de entradas gobernadas y límites ─────────────────────────────────────────────────────────────────────────────────────

def _clarified(texts, original=ORIGINAL, item='1'):
    html = original
    for i, text in enumerate(texts):
        user = {'sub': f'persona{i}@example.com'}
        html, _ = journal.append(html, text, journal.issue(item, user['sub']), item, user, COLUMNS,
                                 movement=journal.CLARIFICATION, column=service.DESCRIPTION)
    return html


def test_externally_altered_clarification_is_never_promoted_to_consolidated_text():
    html = _clarified(['Aclaración legítima', 'Aclaración que alguien editó en SharePoint'])
    tampered = html.replace('que alguien editó', 'ALTERADA')
    consolidated, clarifications = journal.description_view(tampered, '1')
    assert consolidated == 'Texto original'                                      # el texto aceptado no absorbe la entrada alterada
    assert [(c['verified'], c['actor']) for c in clarifications] == [(True, 'persona0@example.com'), (False, '—')]
    assert 'ALTERADA' in clarifications[1]['text'] and clarifications[1]['movement'] == journal.UNVERIFIABLE
    rows = journal.history('', '1', tampered)
    assert [r['movement'] for r in rows] == [journal.CLARIFICATION, journal.UNVERIFIABLE]


@pytest.mark.parametrize('forge', ['other_item', 'comments_entry', 'stripped_signature'])
def test_governed_markers_in_the_wrong_context_are_unverifiable_not_free_text(forge):
    user = {'sub': 'x@example.com'}
    if forge == 'other_item':
        html = _clarified(['De otra solicitud'], item='2')
    elif forge == 'comments_entry':
        html, _ = journal.append(ORIGINAL, 'Observación copiada', journal.issue('1', user['sub']), '1', user, COLUMNS)
    else:
        html = _clarified(['Sin firma']).replace('data-dw-observation="', 'data-dw-observation="x')
    consolidated, clarifications = journal.description_view(html, '1')
    assert consolidated == 'Texto original' and [c['verified'] for c in clarifications] == [False]


def test_an_unverifiable_marker_also_prevents_reopening_direct_edition(open_request):
    take(open_request, {service.DESCRIPTION: _clarified(['x']).replace('data-dw-observation="', 'data-dw-observation="x')})
    assert journal.description_mode(open_request[2]['fields'], '1') == 'append'  # Solicitado y sin asignar, pero con contenido gobernado
    html = detail(open_request)
    assert 'data-clarifications-unverifiable="1"' in html and 'name="' + service.DESCRIPTION + '"' not in html
    assert save(open_request, **{service.DESCRIPTION: 'Reemplazo'}).status_code == 403 and not open_request[3]


def test_appending_preserves_an_unverifiable_entry_byte_for_byte(open_request):
    tampered = _clarified(['Original']).replace('Original', 'Editada afuera')
    take(open_request, {**APPROVED, service.DESCRIPTION: tampered})
    assert 'Guardado confirmado' in clarify(open_request, 'Nueva').text
    stored = open_request[2]['fields'][service.DESCRIPTION]
    assert stored.startswith(tampered)
    assert [c['verified'] for c in journal.description_view(stored, '1')[1]] == [False, True]


def test_column_length_limit_blocks_the_append_without_writing(open_request, monkeypatch):
    take(open_request, APPROVED)
    limited = [COLUMNS[0], {'name': service.DESCRIPTION, **RICH, 'text': {**RICH['text'], 'maxLength': len(ORIGINAL) + 50}}]
    monkeypatch.setattr(repository, 'read_creation_columns', lambda *a: limited)
    assert 'supera la capacidad disponible de Descripción de la demanda' in clarify(open_request, 'x' * 200).text
    assert not open_request[3]


def test_accumulated_content_is_fully_recovered_in_order():
    texts = [f'Aclaración {i}: ' + 'detalle ' * 40 for i in range(25)]
    html = _clarified(texts)
    consolidated, clarifications = journal.description_view(html, '1')
    assert consolidated == 'Texto original'
    assert [c['text'] for c in clarifications] == [service.normalize_narrative(t.strip()) for t in texts]
    assert all(c['verified'] for c in clarifications)


def test_real_sharepoint_column_shape_is_accepted():
    # Metadatos reales leídos de la lista (2026-11-25): Descripción = Comentarios = richText, multilínea, sin anexado, sin maxLength.
    real = {'readOnly': False, 'text': {'allowMultipleLines': True, 'appendChangesToExistingText': False, 'linesForEditing': 6, 'textType': 'richText'}}
    assert journal.validate_column([{'name': service.DESCRIPTION, **real}], service.DESCRIPTION) == real['text']
