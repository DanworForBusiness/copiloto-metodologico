"""Authorization uses persisted creator/assignment; Graph writes remain simulated."""
from copy import deepcopy
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from webapp.main import app
from webapp import access
from webapp.routers import solicitudes as routes
from webapp.services import solicitudes_repository as repository, solicitudes_service as service
from webapp.services import solicitudes_creation as creation, solicitudes_journal as journal
from webapp.services import solicitudes_bulk as bulk
from webapp.services.reporting_repository import ReportingRepository
from tests.webapp.test_solicitudes_creation import schema, catalogs  # noqa: F401

ORIGIN={'Origin':'http://testserver'}


@pytest.fixture
def environment(monkeypatch):
    state={'actor':'creator@example.com'}
    item={'id':'1','eTag':'"7"','createdBy':{'user':{'email':'creator@example.com'}},
          'fields':{service.STATUS:'Pendiente',service.ASSIGNEES+'LookupId':['9']}}
    options={service.STATUS:['Pendiente','Suspendido','En curso'],service.ASSIGNEES:{'9':'Assigned','10':'Other'},service.PROJECT:{}}
    writes=[]
    monkeypatch.setattr(access,'APP_OWNER','owner@example.com')
    monkeypatch.setattr(routes,'_user',lambda request:{'sub':state['actor']})
    monkeypatch.setattr(ReportingRepository,'get_colaboradores_padron',lambda self,token:({}, {'assigned@example.com':'9','other@example.com':'10'}))
    monkeypatch.setattr(repository,'read_item',lambda *args:deepcopy(item))
    monkeypatch.setattr(repository,'lookup_options',lambda *args,**kwargs:options)
    monkeypatch.setattr(repository,'read_creation_columns',lambda *args:[{'name':service.COMMENTS,'readOnly':False,'text':{'allowMultipleLines':True,'appendChangesToExistingText':False,'textType':'richText'}}])
    monkeypatch.setattr(routes.solicitudes_snapshot,'invalidate',lambda *a:None)
    def update(token,id,payload,etag):
        writes.append(deepcopy(payload)); item['fields'].update(payload); item['eTag']='"8"'
    monkeypatch.setattr(repository,'update_item',update)
    monkeypatch.setattr(repository,'delete_item',lambda *args:pytest.fail('Unexpected deletion'))
    return TestClient(app),state,item,writes,options


def save(env,**fields):
    client,_,_,_,_=env
    return client.post('/operaciones/solicitudes/1/guardar',headers=ORIGIN,
                       data={'graph_token':'fake','etag':'"7"',**fields})


def test_creator_can_change_general_fields(environment):
    result=save(environment,Prioridad='Alta')
    assert 'data-solicitudes-saved' in result.text
    assert environment[3]==[{'Prioridad':'Alta'}]


@pytest.mark.parametrize('field,value',[(service.STATUS,'Suspendido'),(service.ASSIGNEES,'10'),(service.ACTUAL,'2026-11-13'),('new_observation','Cambio no autorizado'),(service.COMMENTS,'Reemplazar historia')])
def test_creator_cannot_change_protected_fields_even_with_self_assignment(environment,field,value):
    result=save(environment,**{field:value})
    assert result.status_code in (403,422)
    assert not environment[3]


def test_access_to_vertical_does_not_grant_edit_right(environment):
    environment[1]['actor']='unrelated@example.com'
    result=save(environment,Prioridad='Alta')
    assert result.status_code==403 and not environment[3]


@pytest.mark.parametrize('actor',['assigned@example.com','owner@example.com'])
def test_architecture_can_change_status_and_append_audited_comment(environment,actor):
    environment[1]['actor']=actor
    result=save(environment,**{service.STATUS:'Suspendido',service.ACTUAL:'2026-11-13','new_observation':'Observación legítima', 'observation_ticket':journal.issue('1',actor)})
    assert 'data-solicitudes-saved' in result.text
    patch=environment[3][0]
    assert patch[service.STATUS]=='Suspendido' and patch[service.ACTUAL]=='2026-11-13T00:00:00Z'
    assert journal.history(patch[service.COMMENTS],'1')[0]['text']=='Observación legítima'


def test_owner_can_make_first_assignment(environment):
    environment[1]['actor']='owner@example.com'
    environment[2]['fields'][service.ASSIGNEES+'LookupId']=[]
    assert 'data-solicitudes-saved' in save(environment,**{service.ASSIGNEES:'10'}).text
    assert environment[3][0][service.ASSIGNEES+'LookupId']==['10']


def test_cannot_gain_architecture_rights_with_same_request(environment):
    result=save(environment,**{service.ASSIGNEES:'10',service.STATUS:'En curso'})
    assert result.status_code==403 and not environment[3]
    assert environment[2]['fields'][service.ASSIGNEES+'LookupId']==['9']


class Controls(HTMLParser):
    def __init__(self,html):
        super().__init__();self.fields={};self.disabled=False;self.feed(html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='fieldset': self.disabled='disabled' in a
        if 'id' in a:self.fields[a['id']]='disabled' in a or self.disabled
    def handle_endtag(self,tag):
        if tag=='fieldset':self.disabled=False


@pytest.mark.parametrize('actor,protected',[('creator@example.com',True),('assigned@example.com',False),('owner@example.com',False)])
def test_edit_form_permissions(environment,actor,protected):
    environment[1]['actor']=actor
    html=environment[0].post('/operaciones/solicitudes/1/detalle',data={'graph_token':'fake'}).text
    controls=Controls(html).fields
    for id in ('sol-status','sol-person','sol-actual','sol-observation'):
        if protected:
            assert id not in controls,id
        else:
            assert controls[id] is False,id
    if protected:
        for kind in ('status','assignees','actual'):
            assert f'data-architecture-followup="{kind}"' in html
        assert 'name="new_observation"' not in html
        assert 'name="'+service.COMMENTS+'"' not in html
    assert not controls['sol-priority']


def test_unrelated_user_gets_read_only_view(environment):
    environment[1]['actor']='unrelated@example.com'
    html=environment[0].post('/operaciones/solicitudes/1/detalle',data={'graph_token':'fake'}).text
    assert 'data-solicitudes-display' in html and '/guardar' not in html


def test_bulk_cannot_bypass_persisted_permissions(environment):
    row={'id':'1','etag':'"7"','status':'pending'}
    result,attempted=bulk._one(row,{'actor':'creator@example.com','targets':{service.STATUS:'Suspendido'},'id':'test'},'fake',[],environment[4])
    assert result['status']=='rejected' and not attempted and not environment[3]


def test_permission_identity_never_uses_display_names(environment):
    item=environment[2]
    item['createdBy']={'user':{'displayName':'creator@example.com'}}
    assert repository.edit_permissions('fake',item,{'sub':'creator@example.com'})==(False,False)


def test_creator_identity_can_resolve_sharepoint_author_lookup(environment,monkeypatch):
    item=environment[2];item.pop('createdBy');item['fields']['AuthorLookupId']='42'
    monkeypatch.setattr(repository,'list_url',lambda token:repository.GRAPH+'/sites/site/lists/requests')
    monkeypatch.setattr(repository,'graph_get_json',lambda url,token:{'fields':{'Name':'i:0#.f|membership|creator@example.com'}})
    assert repository.edit_permissions('fake',item,{'sub':'creator@example.com'})==(True,False)


def test_creation_form_omits_architecture_controls_and_preserves_general_fields(environment,monkeypatch,schema,catalogs):
    monkeypatch.setattr(repository,'read_creation_columns',lambda *args:schema)
    monkeypatch.setattr(repository,'lookup_options',lambda *args,**kwargs:catalogs)
    response=environment[0].post('/operaciones/solicitudes/nueva/datos',data={'graph_token':'fake'})
    assert response.status_code==200
    controls=Controls(response.text).fields
    for id in ('sol-status','sol-person','sol-actual','sol-comments'):assert id not in controls
    for field in (service.STATUS,service.ASSIGNEES,service.ACTUAL,service.COMMENTS):
        assert f'name="{field}"' not in response.text
    for id in ('sol-title','sol-priority','sol-project','sol-planned','sol-description'):
        assert controls[id] is False
    assert 'data-architecture-after-creation' in response.text


@pytest.mark.parametrize('actor',['creator@example.com','owner@example.com'])
@pytest.mark.parametrize('field,value',[(service.STATUS,'En curso'),(service.ASSIGNEES,'10'),(service.ACTUAL,'2026-11-13'),(service.COMMENTS,'Comentario')])
def test_manipulated_creation_rejected_before_sharepoint(environment,monkeypatch,actor,field,value):
    environment[1]['actor']=actor
    monkeypatch.setattr(repository,'create_item',lambda *a:pytest.fail('No debe crearse'))
    response=environment[0].post('/operaciones/solicitudes/nueva',headers=ORIGIN,
        data={'Title':'Nueva','graph_token':'fake','submission_id':'fake',field:value})
    assert 'campos no permitidos' in response.text


@pytest.mark.parametrize('action,data',[
    ('rechazar',{'reason':'No aplica','confirm_rejection':'yes'}),
    ('eliminar',{'confirm_delete':'yes'}),
])
def test_unrelated_user_cannot_use_alternative_write_routes(environment,action,data):
    environment[1]['actor']='unrelated@example.com'
    result=environment[0].post('/operaciones/solicitudes/1/'+action,headers=ORIGIN,
        data={'graph_token':'fake','etag':'"7"',**data})
    assert result.status_code==403 and not environment[3]


def test_creator_cannot_reject_and_write_architecture_history(environment):
    result=environment[0].post('/operaciones/solicitudes/1/rechazar',headers=ORIGIN,
        data={'graph_token':'fake','etag':'"7"','reason':'No aplica','confirm_rejection':'yes'})
    assert result.status_code==403 and not environment[3]


def test_persisted_assignment_removed_since_form_load_revokes_architecture_permission(environment):
    environment[1]['actor']='assigned@example.com'
    environment[2]['fields'][service.ASSIGNEES+'LookupId']=[]
    assert save(environment,**{service.STATUS:'Suspendido'}).status_code==403
    assert not environment[3]


def test_missing_identity_configuration_fails_closed(environment,monkeypatch):
    from webapp.services.reporting_repository import ReportingConfigurationError
    def unavailable(*args):
        raise ReportingConfigurationError('Missing mapping')
    monkeypatch.setattr(ReportingRepository,'get_colaboradores_padron',unavailable)
    with pytest.raises(ValueError,match='identidad'):
        repository.edit_permissions('fake',environment[2],{'sub':'assigned@example.com'})
