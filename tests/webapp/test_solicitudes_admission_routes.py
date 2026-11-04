from copy import deepcopy
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import solicitudes as routes
from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service


@pytest.fixture
def setup(monkeypatch):
    current = {'eTag': '"7"', 'fields': {service.FUNNEL: admission.REQUESTED, service.STATUS: 'Pendiente'}}
    calls, invalidated = [], []
    columns = [{'name': service.FUNNEL, 'readOnly': False, 'choice': {
        'choices': [admission.REQUESTED, admission.APPROVED, admission.REJECTED]}}]
    monkeypatch.setattr(routes, '_user', lambda req: {'sub': 'admission-fixture'})
    monkeypatch.setattr(repository, 'read_item', lambda *args: deepcopy(current))
    monkeypatch.setattr(repository, 'lookup_options', lambda *args: {service.STATUS: ['Pendiente', 'En curso', 'Suspendido']})
    monkeypatch.setattr(repository, 'read_creation_columns', lambda *args: columns)
    monkeypatch.setattr(routes.solicitudes_snapshot, 'invalidate', invalidated.append)
    def update(token, item_id, payload, etag):
        calls.append((deepcopy(payload), etag))
        current['fields'].update(payload)
        current['eTag'] = '"8"'
        return {}
    monkeypatch.setattr(repository, 'update_item', update)
    return TestClient(app), current, calls, invalidated, columns


def send(client, **overrides):
    return client.post('/operaciones/solicitudes/1/guardar',
        headers={'Origin': 'http://testserver', 'HX-Request': 'true'},
        data={'etag': '"7"', 'graph_token': 'fixture-token', service.STATUS: 'En curso', **overrides})


def test_approval_is_single_partial_patch_and_confirmed_by_reread(setup, caplog):
    client, current, calls, invalidated, _ = setup
    with caplog.at_level('INFO', logger='danwor.solicitudes.audit'):
        response = send(client, confirm_admission='yes')
    assert 'data-solicitudes-saved' in response.text
    assert calls == [({service.STATUS: 'En curso', service.FUNNEL: admission.APPROVED}, '"7"')]
    assert invalidated == ['admission-fixture']
    assert admission.APPROVED in response.text
    assert 'outcome=confirmed' in caplog.text and 'fixture-token' not in caplog.text


def test_missing_confirmation_does_not_write_or_replace_form(setup):
    client, _, calls, _, _ = setup
    result = send(client)
    assert '¿Continuar?' in result.text and '<form' not in result.text
    assert not calls


@pytest.mark.parametrize('existing', [
    *[{service.STATUS: state} for state in ('Planificado','En curso','Finalizado')],
    {service.PLANNED:'2026-11-01T00:00:00Z'}, {service.ACTUAL:'2026-11-01T00:00:00Z'},
])
def test_no_delta_save_reconciles_only_funnel_after_confirmation(setup,existing,caplog):
    client,current,calls,invalidated,_=setup
    current['fields'].update(existing)
    data={'etag':'"7"','graph_token':'fixture-token'}
    result=client.post('/operaciones/solicitudes/1/guardar',data=data,headers={'Origin':'http://testserver','HX-Request':'true'})
    assert '¿Continuar?' in result.text and not calls and '<form' not in result.text
    with caplog.at_level('INFO',logger='danwor.solicitudes.audit'):
        result=client.post('/operaciones/solicitudes/1/guardar',data={**data,'confirm_admission':'yes'},headers={'Origin':'http://testserver','HX-Request':'true'})
    assert 'data-solicitudes-saved' in result.text
    assert calls==[({service.FUNNEL:admission.APPROVED},'"7"')]
    assert invalidated==['admission-fixture']
    assert 'operation=funnel_reconciliation' in caplog.text
    assert 'Solicitado -> Aprobado para Arquitectura' in caplog.text
    assert service.COMMENTS not in calls[0][0]


def test_invalid_metadata_prevents_operational_write_too(setup):
    client, _, calls, _, columns = setup
    columns[0]['readOnly'] = True
    assert 'catálogo editable' in send(client, confirm_admission='yes').text
    assert not calls


def test_prior_decision_is_preserved(setup):
    client, current, calls, _, _ = setup
    current['fields'][service.FUNNEL] = admission.REJECTED
    assert 'data-solicitudes-saved' in send(client).text
    assert calls[0][0] == {service.STATUS: 'En curso'}


def test_same_etag_cannot_replay_confirmed_approval(setup):
    client, _, calls, _, _ = setup
    send(client, confirm_admission='yes')
    assert 'data-solicitudes-review' in send(client, confirm_admission='yes').text
    assert len(calls) == 1


@pytest.mark.parametrize('code', [401, 403, 412, 429])
def test_graph_errors_preserve_draft(setup, monkeypatch, code):
    client, _, _, _, _ = setup
    def fail(*args):
        raise HTTPError('', code, 'Fixture', {}, None)
    monkeypatch.setattr(repository, 'update_item', fail)
    response = send(client, confirm_admission='yes')
    assert 'data-solicitudes-saved' not in response.text and '<form' not in response.text
    if code == 412:
        assert 'data-solicitudes-review' in response.text


def test_reread_must_confirm_derived_funnel_not_only_architecture(setup, monkeypatch):
    client, current, calls, _, _ = setup
    def uncertain(token, item_id, payload, etag):
        calls.append(payload)
        current['fields'][service.STATUS] = 'En curso'
        current['eTag'] = '"8"'
        raise TimeoutError()
    monkeypatch.setattr(repository, 'update_item', uncertain)
    result = send(client, confirm_admission='yes')
    assert 'data-solicitudes-review' in result.text and 'data-solicitudes-saved' not in result.text
    assert len(calls) == 1
