from copy import deepcopy
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import solicitudes as routes
from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_journal as journal
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service

USER = {'sub': 'journal-fixture', 'email': 'fixture@example.invalid'}


@pytest.fixture
def columns():
    return [
        {'name': service.COMMENTS, 'readOnly': False, 'text': {'allowMultipleLines': True,
            'appendChangesToExistingText': False, 'textType': 'richText'}},
        {'name': service.FUNNEL, 'readOnly': False, 'choice': {'choices': [
            admission.REQUESTED, admission.APPROVED, admission.REJECTED]}},
    ]


def append(original, text, columns, **kwargs):
    return journal.append(original, text, journal.issue('1', USER['sub']), '1', USER, columns, **kwargs)


def test_first_and_second_entries_preserve_original_and_have_verifiable_metadata(columns):
    legacy = '<div class="ExternalClass"><table><tr><td>HTML &amp; original</td></tr></table><img src="https://example.invalid/x"></div>'
    first, a = append(legacy, 'Primera <img onerror=alert(1)> & línea\nSegunda línea', columns,
                      now=datetime(2026, 11, 4, 13, 27, tzinfo=timezone.utc))
    second, b = append(first, 'Segunda intervención', columns,
                       now=datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc))
    assert second.startswith(first) and first.startswith(legacy)
    assert a != b
    rows = journal.history(second, '1')
    assert [row.get('id') for row in rows] == [b, a, None]
    assert rows[0]['date'] == '04/11/2026 10:30'
    assert rows[0]['actor'] == USER['email']
    assert rows[1]['text'].endswith('Segunda línea')
    assert rows[2]['actor'] == rows[2]['date'] == '—'
    assert rows[2]['text'] == 'HTML & original'
    assert '<img onerror' not in first and '&lt;img onerror' in first


def test_empty_plain_and_legacy_are_not_split_or_falsely_attributed():
    assert journal.history('', '1') == []
    rows = journal.history('<p>04/11/2026 — Texto antiguo</p><p>Otra intervención aparente</p>', '1')
    assert len(rows) == 1 and rows[0]['movement'] == 'Observación heredada'
    assert rows[0]['date'] == rows[0]['actor'] == '—'


def test_ticket_cannot_be_reused_or_moved_to_other_actor_or_item(columns):
    ticket = journal.issue('1', USER['sub'])
    original, _ = journal.append('', 'Primera', ticket, '1', USER, columns)
    with pytest.raises(ValueError, match='ya está registrada'):
        journal.append(original, 'No duplicar', ticket, '1', USER, columns)
    for actor, item in [({'sub': 'other'}, '1'), (USER, '2')]:
        with pytest.raises(ValueError):
            journal.append('', 'Texto', ticket, item, actor, columns)
    with pytest.raises(ValueError):
        journal.append('', 'Texto', 'tampered', '1', USER, columns)


def test_changed_or_stripped_metadata_falls_back_to_legacy(columns):
    original, _ = append('', 'Verificable', columns)
    changed = original.replace('>Verificable<', '>Texto cambiado<')
    assert journal.history(changed, '1')[0]['actor'] == '—'
    assert journal.history(original, '2')[0]['actor'] == '—'
    stripped = original.replace('data-dw-observation=', 'data-unknown=')
    assert journal.history(stripped, '1')[0]['movement'] == 'Observación heredada'


def test_storage_limit_counts_html_and_signed_metadata_and_preserves_input(columns):
    original = '<p>Anterior</p>'
    columns[0]['text']['maxLength'] = 50
    with pytest.raises(ValueError, match='capacidad'):
        append(original, 'A' * 1000, columns)
    assert original == '<p>Anterior</p>'


@pytest.mark.parametrize('change', [{'appendChangesToExistingText': True}, {'allowMultipleLines': False}, {'textType': 'plain'}])
def test_incompatible_schema_blocks_append(columns, change):
    columns[0]['text'].update(change)
    with pytest.raises(ValueError, match='configuración'):
        append('', 'Texto', columns)


@pytest.fixture
def setup(monkeypatch, columns):
    original = '<div class="ExternalClass"><b>Comentario anterior</b></div>'
    current = {'id': '1', 'eTag': '"7"', 'fields': {service.FUNNEL: admission.REQUESTED,
        service.STATUS: 'Pendiente', service.COMMENTS: original}}
    calls, invalidated = [], []
    monkeypatch.setattr(routes, '_user', lambda req: USER)
    monkeypatch.setattr(repository, 'read_item', lambda *args: deepcopy(current))
    monkeypatch.setattr(repository, 'read_creation_columns', lambda *args: columns)
    monkeypatch.setattr(repository, 'lookup_options', lambda *args: {
        service.STATUS: ['Pendiente', 'En curso'], service.PROJECT: {}, service.ASSIGNEES: {}})
    monkeypatch.setattr(routes.solicitudes_snapshot, 'invalidate', invalidated.append)
    def update(token, item_id, payload, etag):
        calls.append((deepcopy(payload), etag))
        current['fields'].update(payload)
        current['eTag'] = '"8"'
    monkeypatch.setattr(repository, 'update_item', update)
    return TestClient(app), current, calls, invalidated


def send(setup, path='guardar', **changes):
    return setup[0].post('/operaciones/solicitudes/1/' + path,
        headers={'Origin': 'http://testserver', 'HX-Request': 'true'}, data={
            'graph_token': 'fixture-secret', 'etag': '"7"',
            'observation_ticket': journal.issue('1', USER['sub']), **changes})


def test_combined_observation_operational_change_and_approval_use_one_patch(setup, caplog):
    with caplog.at_level('INFO', logger='danwor.solicitudes.audit'):
        response = send(setup, new_observation='Nueva confidencial', confirm_admission='yes', **{service.STATUS: 'En curso'})
    _, current, calls, invalidated = setup
    assert 'data-solicitudes-saved' in response.text
    assert len(calls) == 1 and calls[0][1] == '"7"'
    assert set(calls[0][0]) == {service.STATUS, service.FUNNEL, service.COMMENTS}
    assert calls[0][0][service.COMMENTS].startswith('<div class="ExternalClass"><b>Comentario anterior</b></div>')
    assert invalidated == [USER['sub']]
    assert 'id="solicitudes-history"' in response.text and 'hx-swap-oob="outerHTML"' in response.text
    assert 'Nueva confidencial' not in caplog.text and 'fixture-secret' not in caplog.text


def test_observation_only_does_not_approve_and_blank_does_not_append(setup):
    assert 'data-solicitudes-saved' in send(setup, new_observation='Solo comentario').text
    assert set(setup[2][0][0]) == {service.COMMENTS}
    setup[2].clear()
    response = send(setup, new_observation=' \n ', etag='"8"', Prioridad='Alta')
    assert 'data-solicitudes-saved' in response.text
    assert setup[2][0][0] == {'Prioridad': 'Alta'}


def test_direct_comments_replacement_is_rejected(setup):
    response = send(setup, **{service.COMMENTS: 'Borrar anteriores'})
    assert 'campos inválidos' in response.text and not setup[2]


def test_two_users_conflict_and_same_ticket_replay_never_append_twice(setup):
    ticket = journal.issue('1', USER['sub'])
    assert 'data-solicitudes-saved' in send(setup, new_observation='Una vez', observation_ticket=ticket).text
    conflict = send(setup, new_observation='Segundo usuario', observation_ticket=ticket)
    assert 'data-solicitudes-review' in conflict.text
    replay = send(setup, new_observation='Una vez', observation_ticket=ticket, etag='"8"')
    assert 'ya está registrada' in replay.text and len(setup[2]) == 1


@pytest.mark.parametrize('applied', [True, False])
def test_timeout_is_reconciled_without_second_patch(setup, monkeypatch, applied):
    _, current, calls, _ = setup
    def timeout(token, item_id, payload, etag):
        calls.append((payload, etag))
        if applied:
            current['fields'].update(payload)
            current['eTag'] = '"8"'
        raise TimeoutError()
    monkeypatch.setattr(repository, 'update_item', timeout)
    response = send(setup, new_observation='Conservar borrador')
    assert len(calls) == 1
    assert ('data-solicitudes-saved' in response.text) == applied
    assert ('data-solicitudes-review' in response.text) != applied


@pytest.mark.parametrize('path', ['guardar', 'rechazar'])
@pytest.mark.parametrize('code', [401, 403, 412, 429])
def test_graph_error_preserves_form_and_observation(setup, monkeypatch, code, path):
    def fail(*args):
        raise HTTPError('', code, 'Fixture', {}, None)
    monkeypatch.setattr(repository, 'update_item', fail)
    data = {'new_observation': 'No perder'} if path == 'guardar' else {'reason': 'No perder', 'confirm_rejection': 'yes'}
    response = send(setup, path=path, **data)
    assert '<form' not in response.text and 'data-solicitudes-saved' not in response.text


def test_rejection_appends_reason_and_changes_only_two_fields(setup):
    response = send(setup, path='rechazar', reason='No corresponde al área', confirm_rejection='yes')
    assert 'data-solicitudes-saved' in response.text and 'data-solicitudes-rejection' in response.text
    assert len(setup[2]) == 1 and set(setup[2][0][0]) == {service.FUNNEL, service.COMMENTS}
    assert setup[1]['fields'][service.STATUS] == 'Pendiente'
    rows = journal.history(setup[1]['fields'][service.COMMENTS], '1')
    assert rows[0]['movement'] == 'Rechazo / Derivación a Operaciones'
    assert len(rows) == 2


@pytest.mark.parametrize('value', [admission.APPROVED, admission.REJECTED])
def test_rejection_cannot_reverse_existing_decision(setup, value):
    setup[1]['fields'][service.FUNNEL] = value
    response = send(setup, path='rechazar', reason='Motivo', confirm_rejection='yes')
    assert 'data-solicitudes-review' in response.text and not setup[2]


def test_rejection_requires_confirmation_reason_and_excludes_draft_fields(setup):
    for changes in ({'reason': ''}, {'reason': 'Motivo'}, {'reason': 'Motivo', 'confirm_rejection': 'yes', 'Prioridad': 'Alta'}):
        response = send(setup, path='rechazar', **changes)
        assert 'data-solicitudes-saved' not in response.text
    assert not setup[2]


def test_form_shows_empty_workspace_and_safe_readonly_legacy(setup):
    setup[1]['fields'][service.COMMENTS] += '<script>alert(1)</script>'
    response = setup[0].post('/operaciones/solicitudes/1/detalle', data={'graph_token': 'fixture'})
    assert 'name="new_observation"' in response.text
    assert 'name="' + service.COMMENTS + '"' not in response.text
    assert 'rows="8" placeholder="Escribí una nueva observación…"></textarea>' in response.text
    assert 'Observación heredada' in response.text and '<script>alert(1)</script>' not in response.text
    assert 'name="reason"' in response.text and 'data-solicitudes-reject-open' in response.text


def test_rejection_timeout_is_reconciled_and_never_replayed(setup, monkeypatch):
    _, current, calls, invalidated = setup
    def applied_timeout(token, item_id, payload, etag):
        calls.append(payload)
        current['fields'].update(payload)
        current['eTag'] = '"8"'
        raise TimeoutError()
    monkeypatch.setattr(repository, 'update_item', applied_timeout)
    response = send(setup, path='rechazar', reason='Motivo', confirm_rejection='yes')
    assert 'data-solicitudes-saved' in response.text
    conflict = send(setup, path='rechazar', reason='Motivo', confirm_rejection='yes')
    assert 'data-solicitudes-review' in conflict.text and len(calls) == 1
    assert invalidated == [USER['sub']]


def test_rejection_authorization_and_same_origin_precede_graph(setup, monkeypatch):
    def no_graph(*args):
        pytest.fail('Graph must not be called')
    monkeypatch.setattr(repository, 'read_item', no_graph)
    assert setup[0].post('/operaciones/solicitudes/1/rechazar').status_code == 403
    monkeypatch.setattr(routes, '_user', lambda req: None)
    assert send(setup, path='rechazar', reason='Motivo', confirm_rejection='yes').status_code == 401


def test_graph_strips_metadata_not_reported_as_success(setup, monkeypatch):
    _, current, calls, _ = setup
    def normalize(token, item_id, payload, etag):
        calls.append(payload)
        current['fields'].update(payload)
        current['fields'][service.COMMENTS] = payload[service.COMMENTS].replace('data-dw-observation=', 'removed=')
        current['eTag'] = '"8"'
    monkeypatch.setattr(repository, 'update_item', normalize)
    result = send(setup, new_observation='No confirmar pérdida de metadata')
    assert 'data-solicitudes-review' in result.text and 'data-solicitudes-saved' not in result.text
    assert len(calls) == 1
