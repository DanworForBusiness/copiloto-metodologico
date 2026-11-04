from copy import deepcopy
from bs4 import BeautifulSoup
import pytest
from fastapi.testclient import TestClient
from webapp.main import app
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_service as service
from webapp.services import solicitudes_snapshot as snapshot


@pytest.mark.parametrize('status', ['Pendiente','Planificado','En curso','Finalizado','Suspendido',None,'Nuevo'])
@pytest.mark.parametrize('funnel', ['Solicitado','Aprobado para Arquitectura','Rechazado, Aplica Operaciones'])
def test_classification_is_exclusive_and_never_drops_rows(status, funnel):
    row = {'id':'1','fields':{service.STATUS:status, service.FUNNEL:funnel}}
    before = deepcopy(row)
    groups, unknown = service.classify_rows([row])
    key = 'cerradas' if funnel == 'Rechazado, Aplica Operaciones' or status in ('Finalizado','Suspendido') else 'vigentes'
    assert groups[key] == [row] and len(groups['vigentes']) + len(groups['cerradas']) == 1
    assert unknown == (['1'] if status in (None,'Nuevo') and funnel != 'Rechazado, Aplica Operaciones' else [])
    assert row == before


@pytest.fixture
def grouped(monkeypatch):
    rows = [{'id':str(i),'fields':{'Title':f'Solicitud {i:03}',service.STATUS:'Pendiente' if i<=105 else 'Finalizado',service.FUNNEL:'Solicitado','Prioridad':'Alta'}} for i in range(1,211)]
    monkeypatch.setattr(route,'_user',lambda request:{'sub':'actor'})
    monkeypatch.setattr(snapshot,'read',lambda *args,**kwargs:deepcopy(rows))
    client = TestClient(app)
    def post(**data):
        response=client.post('/operaciones/solicitudes/data',data={'graph_token':'fixture',**data})
        assert response.status_code==200
        return BeautifulSoup(response.text,'html.parser')
    return rows, post


def test_independent_pagination_sort_and_filtered_counts(grouped):
    _,post=grouped
    soup=post(page_vigentes='2',page_cerradas='3',sort_vigentes='title',dir_vigentes='desc')
    active=soup.select_one('#solicitudes-vigentes');closed=soup.select_one('#solicitudes-cerradas')
    assert active.select_one('[data-pager]')['data-pager-page']=='2'
    assert closed.select_one('[data-pager]')['data-pager-page']=='3'
    assert len(active.select('[data-sol-select]'))==50 and len(closed.select('[data-sol-select]'))==5
    assert active.select_one('[data-sol-select]')['data-sol-select']=='55'
    assert active.select_one('.dashboard-section__count').get_text()=='105 solicitudes'
    assert 'page_cerradas' in closed.select_one('button[hx-vals]')['hx-vals']
    filtered=post(q='105')
    assert len(filtered.select('#solicitudes-vigentes [data-sol-select]'))==1
    assert not filtered.select('#solicitudes-cerradas [data-sol-select]')
    for elt in soup.select('#solicitudes-filters [hx-trigger]'):
        assert 'page_vigentes' in elt['hx-vals'] and 'page_cerradas' in elt['hx-vals']


def test_refresh_relocates_rejected_keeps_badges_and_unknowns_visible(grouped):
    rows,post=grouped
    rows[0]['fields'][service.FUNNEL]='Rechazado, Aplica Operaciones'
    rows[1]['fields'][service.STATUS]=None
    soup=post(refresh='1',q='001')
    assert not soup.select('#solicitudes-vigentes [data-sol-select]')
    assert soup.select_one('#solicitudes-cerradas [data-sol-select]')['data-sol-select']=='1'
    assert soup.select_one('[data-badge-kind=architecture]').get_text()=='Pendiente'
    assert soup.select_one('[data-badge-kind=funnel]').get_text()=='Rechazado, Aplica Operaciones'
    soup=post(q='002');assert soup.select_one('[data-unknown-state-count]')['data-unknown-state-count']=='1'
    rows[0]['fields'][service.FUNNEL]='Solicitado'
    soup=post(refresh='1',q='001');assert soup.select_one('#solicitudes-vigentes [data-sol-select]')


def test_group_empty_states_are_not_graph_errors(grouped,monkeypatch):
    rows,post=grouped
    rows.clear();soup=post()
    assert 'Sin solicitudes vigentes' in soup.get_text() and 'Sin solicitudes cerradas' in soup.get_text()
    monkeypatch.setattr(snapshot,'read',lambda *args,**kwargs:(_ for _ in ()).throw(TimeoutError()))
    soup=post()
    assert len(soup.select('[role=alert]'))==2
    assert 'Sin solicitudes vigentes' not in soup.get_text()
