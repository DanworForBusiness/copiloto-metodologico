"""Corrección colaborativa de la Descripción de una solicitud NO TOMADA (Funnel Solicitado, sin colaborador asignado).

Cualquier usuario autorizado en Operaciones puede corregir SOLO la Descripción; el resto de los campos y acciones conserva sus
permisos (creador / asignado / owner). La trazabilidad usa el Historial existente (versiones de SharePoint). Graph simulado."""
import pytest
from bs4 import BeautifulSoup

from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_bulk as bulk
from webapp.services import solicitudes_journal as journal
from webapp.services import solicitudes_service as service
from tests.webapp.test_solicitudes_permissions import ORIGIN, environment, save  # noqa: F401

OPEN = {service.FUNNEL: admission.REQUESTED, service.STATUS: 'Pendiente', service.ASSIGNEES + 'LookupId': [],
        service.DESCRIPTION: '<div>Texto original</div>'}


@pytest.fixture
def open_request(environment):  # noqa: F811
    client, state, item, writes, options = environment
    item['fields'] = dict(OPEN)
    state['actor'] = 'yanina@example.com'                                       # ni creadora, ni asignada, ni owner
    return environment


def detail(env, query=''):
    return env[0].post('/operaciones/solicitudes/1/detalle' + query, data={'graph_token': 'fake'}).text


def test_untaken_request_offers_only_the_description_to_an_unrelated_operations_user(open_request):
    html = detail(open_request)
    soup = BeautifulSoup(html, 'html.parser')
    form = soup.select_one('form[data-solicitudes-description-correction]')
    assert form is not None and form['hx-post'] == '/operaciones/solicitudes/1/guardar'
    assert sorted(el['name'] for el in form.select('[name]')) == sorted([service.DESCRIPTION, 'etag'])
    assert not form.has_attr('data-solicitudes-admission')                     # nunca dispara el diálogo de admisión
    assert '/eliminar' not in html and '/rechazar' not in html and 'new_observation' not in html
    named_outside = [el for el in soup.select('[name]') if not el.find_parent('form', attrs={'data-solicitudes-description-correction': True})]
    assert not named_outside                                                    # el resto sigue en solo lectura


def test_description_correction_is_written_alone_with_etag_and_without_admission(open_request):
    open_request[2]['fields'][service.PLANNED] = '2026-12-01T00:00:00Z'         # compromiso preexistente: el creador propondría admitir
    response = save(open_request, **{service.DESCRIPTION: 'Texto corregido'})
    assert response.status_code == 200 and 'Guardado confirmado' in response.text
    assert open_request[3] == [{service.DESCRIPTION: '<div>Texto corregido</div>'}]


@pytest.mark.parametrize('field,value', [
    ('Prioridad', 'Alta'), (service.PROJECT, '3'), (service.PLANNED, '2026-12-01'), (service.ACTUAL, '2026-12-01'),
    (service.STATUS, 'En curso'), (service.ASSIGNEES, '10'), ('new_observation', 'Hola'), ('confirm_admission', 'yes'),
    ('observation_ticket', 'x'),
])
def test_any_other_field_is_rejected_before_writing(open_request, field, value):
    response = save(open_request, **{service.DESCRIPTION: 'Texto corregido', field: value})
    assert response.status_code == 403 and not open_request[3]


@pytest.mark.parametrize('taken', [
    {service.FUNNEL: admission.APPROVED}, {service.FUNNEL: admission.REJECTED}, {service.FUNNEL: None},
    {service.ASSIGNEES + 'LookupId': ['9']},
    {service.ASSIGNEES + 'LookupId': None, service.ASSIGNEES: [{'LookupId': 9, 'LookupValue': 'Assigned'}]},
])
def test_taken_request_loses_the_correction(open_request, taken):
    open_request[2]['fields'].update(taken)
    html = detail(open_request)
    assert 'data-solicitudes-display' in html and '/guardar' not in html
    assert save(open_request, **{service.DESCRIPTION: 'Texto corregido'}).status_code == 403
    assert not open_request[3]


def test_assignment_after_form_load_revokes_the_correction(open_request):
    assert 'data-solicitudes-description-correction' in detail(open_request)
    open_request[2]['fields'][service.ASSIGNEES + 'LookupId'] = ['9']          # Arquitectura tomó la demanda mientras tanto
    assert save(open_request, **{service.DESCRIPTION: 'Texto corregido'}).status_code == 403 and not open_request[3]


def test_stale_etag_is_a_conflict_without_writing(open_request):
    response = save(open_request, etag='"6"', **{service.DESCRIPTION: 'Texto corregido'})
    assert 'La versión cambió' in response.text and not open_request[3]


@pytest.mark.parametrize('action,data', [
    ('rechazar', {'reason': 'No aplica', 'confirm_rejection': 'yes'}),
    ('eliminar', {'confirm_delete': 'yes'}),
])
def test_correction_grants_neither_delete_nor_reject(open_request, action, data):
    result = open_request[0].post('/operaciones/solicitudes/1/' + action, headers=ORIGIN,
                                  data={'graph_token': 'fake', 'etag': '"7"', **data})
    assert result.status_code == 403 and not open_request[3]


def test_bulk_is_not_widened(open_request):
    row = {'id': '1', 'etag': '"7"', 'status': 'pending'}
    result, attempted = bulk._one(row, {'actor': 'yanina@example.com', 'targets': {'Prioridad': 'Alta'}, 'id': 't'}, 'fake', [], open_request[4])
    assert result['status'] == 'rejected' and not attempted and not open_request[3]


def test_explicit_display_mode_stays_read_only(open_request):
    assert 'data-solicitudes-description-correction' not in detail(open_request, '?mode=display')


def test_creator_keeps_the_full_edit_form(open_request):
    open_request[1]['actor'] = 'creator@example.com'
    html = detail(open_request)
    assert 'data-solicitudes-description-correction' not in html and 'name="Prioridad"' in html


def test_history_records_who_when_previous_and_new_description():
    def version(number, description, who='Yanina Prueba'):
        return {'id': f'{number}.0', 'lastModifiedDateTime': f'2026-11-25T18:{number:02}:00Z',
                'lastModifiedBy': {'user': {'displayName': who}},
                'fields': {**OPEN, service.DESCRIPTION: description}}
    rows, coverage = journal.version_history_coverage('', '8', [
        version(3, '<div>Texto corregido<br>con detalle</div>'),
        version(2, '<p>Texto original</p>', who='Owner'),                   # solo re-serialización HTML: no es un cambio
        version(1, '<div>Texto original</div>', who='Owner')])
    changes = [r for r in rows if r['movement'] == 'Cambio de descripción']
    assert len(changes) == 1 and coverage['level'] != 'warning'
    row = changes[0]
    assert row['actor'] == 'Yanina Prueba' and row['date'] == '25/11/2026 15:03' and row['at'].startswith('2026-11-25T18:03')
    assert row['text'] == 'Anterior: Texto original\nNueva: Texto corregido\ncon detalle'
    assert row['version'] == '3.0' and row['source'] == 'SharePoint'
