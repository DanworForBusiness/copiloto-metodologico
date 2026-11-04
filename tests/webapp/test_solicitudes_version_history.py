from copy import deepcopy
from urllib.error import HTTPError
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest
from webapp.main import app
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_journal as journal
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service


def version(number, **changes):
    return {'id':f'{number}.0','lastModifiedDateTime':f'2026-11-04T15:{number:02}:00Z',
            'lastModifiedBy':{'user':{'displayName':'Persona de prueba'}},
            'fields':{service.FUNNEL:admission.REQUESTED,service.STATUS:'Pendiente',service.PLANNED:None,service.ACTUAL:None,**changes}}


@pytest.mark.parametrize('existing', [True,False])
def test_verified_funnel_transition_reason_actor_time_badges_and_observations(existing):
    before=version(1, **{service.STATUS:'En curso' if existing else 'Pendiente'})
    after=version(2, **{service.STATUS:'En curso', service.FUNNEL:admission.APPROVED})
    original=deepcopy([before,after])
    rows,partial=journal.version_history('<p>Observación previa</p>','9',[after,before,after])
    funnel=[r for r in rows if r['movement']=='Reclasificación de Funnel']
    assert len(funnel)==1 and not partial
    assert funnel[0]['actor']=='Persona de prueba' and funnel[0]['date']=='04/11/2026 12:02'
    assert funnel[0]['before']==admission.REQUESTED and funnel[0]['after']==admission.APPROVED
    assert ('existente' if existing else 'nuevo') in funnel[0]['text']
    assert rows[-1]['text']=='Observación previa' and [before,after]==original
    assert rows==journal.version_history('<p>Observación previa</p>','9',[after,before])[0]


def test_no_delta_or_rejected_funnel_never_fabricates_transition():
    for prior in (admission.APPROVED,admission.REJECTED):
        rows,_=journal.version_history('','1',[version(1,**{service.FUNNEL:prior}),version(2,**{service.FUNNEL:admission.APPROVED})])
        assert not any(r['movement']=='Reclasificación de Funnel' for r in rows)
    rows,partial=journal.version_history('','1',[version(1,**{service.FUNNEL:admission.APPROVED})])
    assert rows==[] and partial  # oldest retained state is not a transition


def test_version_projection_lag_is_partial_and_can_be_retried_without_saving():
    versions=[version(1),version(2)]
    _,partial=journal.version_history('','1',versions,current_modified='2026-11-04T15:03:00Z')
    assert partial


@pytest.mark.parametrize('change', ['gap','fields','timestamp','conflicting_duplicate'])
def test_missing_or_conflicting_evidence_cannot_create_approval(change):
    old,new=version(1),version(2,**{service.FUNNEL:admission.APPROVED})
    versions=[old,new]
    if change=='gap':new['id']='4.0'
    if change=='fields':old.pop('fields')
    if change=='timestamp':new['lastModifiedDateTime']='not-a-time'
    if change=='conflicting_duplicate':versions.append(version(2))
    rows,partial=journal.version_history('','1',versions)
    assert partial and not any(r['movement']=='Reclasificación de Funnel' for r in rows)


def test_unknown_actor_and_reason_are_explicit_not_invented():
    old,new=version(1),version(2,**{service.FUNNEL:admission.APPROVED})
    old['fields'].pop(service.PLANNED);new['lastModifiedBy']={}
    rows,partial=journal.version_history('','1',[old,new])
    assert partial and rows[0]['actor']=='No identificado en SharePoint'
    assert 'Motivo no determinable' in rows[0]['text']


def test_malformed_operational_metadata_is_not_interpreted_as_a_real_change():
    old,new=version(1),version(2)
    new['fields'][service.PLANNED]='not-a-date'
    new['fields'][service.STATUS]={'unexpected':'shape'}
    new['lastModifiedBy']=['invalid']
    rows,partial=journal.version_history('','1',[old,new])
    assert partial and not rows


def test_signed_observation_and_transition_merge_by_verified_time_without_duplicates():
    from datetime import datetime, timezone
    columns=[{'name':service.COMMENTS,'readOnly':False,'text':{'allowMultipleLines':True,'appendChangesToExistingText':False,'textType':'richText'}}]
    html,_=journal.append('<p>Legado</p>','Observación real',journal.issue('1','actor'),'1',{'sub':'actor','name':'Persona'},columns,now=datetime(2026,11,4,15,3,tzinfo=timezone.utc))
    versions=[version(1),version(2,**{service.STATUS:'En curso',service.FUNNEL:admission.APPROVED}),version(3,**{service.STATUS:'En curso',service.FUNNEL:admission.APPROVED})]
    rows,_=journal.version_history(html,'1',versions)
    assert rows[0]['movement']=='Observación de Arquitectura'
    assert sum(row['movement']=='Reclasificación de Funnel' for row in rows)==1
    assert sum(row['movement']=='Observación de Arquitectura' for row in rows)==1
    assert rows[-1]['movement']=='Observación heredada'


def test_versions_repository_reads_pages_and_documented_expanded_detail(monkeypatch):
    prefix=repository.GRAPH+'/sites/fixture/lists/requests/items/1/versions'
    calls=[];monkeypatch.setattr(repository,'item_url',lambda *args:prefix.removesuffix('/versions'))
    def get(url,token):
        calls.append(url)
        if url==prefix:return {'value':[{'id':'2.0'}],'@odata.nextLink':prefix+'?$skiptoken=x'}
        if url==prefix+'?$skiptoken=x':return {'value':[version(1)]}
        assert url==prefix+'/2.0?$expand=fields'
        return version(2,**{service.FUNNEL:admission.APPROVED})
    monkeypatch.setattr(repository,'graph_get_json',get)
    result=repository.read_item_versions('fixture','1')
    assert len(calls)==3 and len(result['versions'])==2 and not result['partial']


def test_versions_repository_rejects_foreign_continuation_and_marks_missing_fields(monkeypatch):
    monkeypatch.setattr(repository,'item_url',lambda *args:repository.GRAPH+'/sites/fixture/lists/requests/items/1')
    monkeypatch.setattr(repository,'graph_get_json',lambda *args:{'value':[],'@odata.nextLink':'https://other.invalid/versions'})
    with pytest.raises(ValueError):repository.read_item_versions('fixture','1')
    def missing(url,token):
        if url.endswith('/versions'):return {'value':[{'id':'1.0'}]}
        raise HTTPError('',403,'Fixture',{},None)
    monkeypatch.setattr(repository,'graph_get_json',missing)
    assert repository.read_item_versions('fixture','1')['partial']


def test_history_route_preserves_observations_on_graph_failure_and_is_read_only(monkeypatch):
    monkeypatch.setattr(route,'_user',lambda request:{'sub':'actor'})
    monkeypatch.setattr(repository,'read_item',lambda *args:{'fields':{service.COMMENTS:'<p>Observación previa</p>'}})
    def forbidden(*args,**kwargs):raise AssertionError('No write permitted')
    monkeypatch.setattr(repository,'update_item',forbidden)
    monkeypatch.setattr(repository,'read_item_versions',lambda *args:{'versions':[version(1),version(2,**{service.FUNNEL:admission.APPROVED,service.STATUS:'En curso'})],'partial':False})
    client=TestClient(app)
    response=client.post('/operaciones/solicitudes/1/historial',data={'graph_token':'fixture'})
    soup=BeautifulSoup(response.text,'html.parser')
    assert len(soup.select('[data-history-version="2.0"]'))==2 # Funnel and Architecture, not duplicate Funnel
    assert 'Reclasificación de Funnel' in response.text and 'Persona de prueba' in response.text
    assert soup.select_one('[data-badge-kind=funnel]').get_text()==admission.REQUESTED
    assert 'Observación previa' in response.text and 'data-history-loading' not in response.text
    def failure(*args):raise HTTPError('',403,'Fixture',{},None)
    monkeypatch.setattr(repository,'read_item_versions',failure)
    response=client.post('/operaciones/solicitudes/1/historial',data={'graph_token':'fixture'})
    assert 'Observación previa' in response.text and 'Reintentar consulta' in response.text
    assert 'Reclasificación de Funnel' not in response.text
    monkeypatch.setattr(route,'_user',lambda request:None)
    assert client.post('/operaciones/solicitudes/1/historial').status_code==401
