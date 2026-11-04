from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.error import HTTPError
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_bulk as bulk
from webapp.services import solicitudes_service as service
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_snapshot as snapshot
from webapp.services import solicitudes_admission as admission


@pytest.fixture
def lab(monkeypatch):
    bulk._plans.clear()
    rows = {str(i): {'id': str(i), 'eTag': '"7"', 'project_label': 'Proyecto simulado', 'fields': {
        'Title': 'Solicitud ' + str(i), 'Prioridad': 'Baja', service.STATUS: 'Pendiente',
        service.FUNNEL: admission.REQUESTED, service.PROJECT + 'LookupId': '4',
        service.ASSIGNEES + 'LookupId': ['9'], service.COMMENTS: '<b>Original intacto</b>',
    }} for i in range(1, 13)}
    columns = [
        {'name': service.STATUS, 'readOnly': False, 'choice': {'choices': ['Pendiente','Planificado','En curso','Finalizado','Suspendido']}},
        {'name': service.FUNNEL, 'readOnly': False, 'choice': {'choices': [admission.REQUESTED, admission.APPROVED, admission.REJECTED]}},
        {'name': 'Prioridad', 'readOnly': False, 'choice': {'choices': ['Baja','Media','Alta']}},
        {'name': service.ASSIGNEES, 'readOnly': False, 'lookup': {'listId': 'people', 'allowMultipleValues': True}},
        *[{'name': field, 'readOnly': False, 'dateTime': {}} for field in (service.PLANNED, service.ACTUAL)],
    ]
    options = {service.STATUS: columns[0]['choice']['choices'], service.ASSIGNEES: {'7': 'Nueva referencia', '9': 'Referencia anterior'}}
    writes, invalidated, reads, modes = [], [], [], {}
    def read(token, item_id):
        reads.append(item_id)
        if item_id not in rows: raise HTTPError('',404,'fixture',{},None)
        return deepcopy(rows[item_id])
    def update(token, item_id, payload, etag):
        writes.append((item_id, deepcopy(payload), etag))
        if modes.get(item_id) in (401,403,412,429,503): raise HTTPError('',modes[item_id],'fixture',{},None)
        if modes.get(item_id) == 'timeout': raise TimeoutError()
        if rows[item_id]['eTag'] != etag: raise HTTPError('',412,'fixture',{},None)
        rows[item_id]['fields'].update(payload)
        rows[item_id]['eTag'] = '"' + str(int(etag.strip('"'))+1) + '"'
        if modes.get(item_id) == 'applied_timeout': raise TimeoutError()
    monkeypatch.setattr(snapshot, 'read', lambda *args, **kwargs: deepcopy(list(rows.values())))
    monkeypatch.setattr(snapshot, 'invalidate', invalidated.append)
    monkeypatch.setattr(repository, 'read_creation_columns', lambda token: deepcopy(columns))
    monkeypatch.setattr(repository, 'lookup_options', lambda *args, **kwargs: deepcopy(options))
    monkeypatch.setattr(repository, 'read_item', read)
    monkeypatch.setattr(repository, 'update_item', update)
    monkeypatch.setattr(route, '_user', lambda request: {'sub': 'actor'})
    return {'rows': rows,'columns':columns,'options':options,'writes':writes,'invalidated':invalidated,'reads':reads,'modes':modes,'client':TestClient(app)}


def plan(lab, field='Prioridad', value='Alta', ids=('1','2')):
    created = bulk.prepare('actor','fake',list(ids))
    return bulk.review('actor',created['id'],'fake',field,value)


def send(lab, action, **data):
    return lab['client'].post('/operaciones/solicitudes/bulk/'+action,
        headers={'Origin':'http://testserver','HX-Request':'true'},data={'graph_token':'fake',**data})


@pytest.mark.parametrize('bad', ['null','{}','[]','[1]','["01"]','["-1"]','["1/fields"]','[true]'])
def test_ids_are_stable_numeric_not_indices_or_paths(bad):
    with pytest.raises(ValueError): bulk.parse_ids(bad)
    assert bulk.parse_ids('["2","1","2"]') == ['2','1']


@pytest.mark.parametrize('field,value,expected', [
    ('Prioridad','Alta',{'Prioridad':'Alta'}),
    (service.STATUS,'En curso',{service.STATUS:'En curso',service.FUNNEL:admission.APPROVED}),
    (service.ASSIGNEES,'7',{service.ASSIGNEES+'LookupId':['7'],service.ASSIGNEES+'LookupId@odata.type':'Collection(Edm.String)'}),
    (service.ASSIGNEES,'',{service.ASSIGNEES+'LookupId':[],service.ASSIGNEES+'LookupId@odata.type':'Collection(Edm.String)'}),
    (service.PLANNED,'2026-11-04',{service.PLANNED:'2026-11-04T00:00:00Z',service.FUNNEL:admission.APPROVED}),
    (service.ACTUAL,'2026-11-04',{service.ACTUAL:'2026-11-04T00:00:00Z',service.FUNNEL:admission.APPROVED}),
])
def test_one_field_partial_patch_reuses_admission_and_preserves_other_fields(lab,field,value,expected):
    p = plan(lab,field,value)
    assert all(row['derived'] == (service.FUNNEL in expected) for row in p['rows'])
    result = bulk.execute('actor',p['id'],'write-token',confirmed=True)
    assert [r['status'] for r in result['rows']] == ['confirmed','confirmed']
    assert all(payload == expected and etag == '"7"' for _,payload,etag in lab['writes'])
    assert all(row['fields'][service.COMMENTS] == '<b>Original intacto</b>' for row in lab['rows'].values())
    assert lab['invalidated'] == ['actor']


def test_existing_date_reconciles_but_rejected_decision_is_preserved(lab):
    lab['rows']['1']['fields'][service.PLANNED] = '2026-11-01T00:00:00Z'
    lab['rows']['2']['fields'][service.FUNNEL] = admission.REJECTED
    p=plan(lab,service.PLANNED,'2026-11-04')
    assert [r['derived'] for r in p['rows']] == [True, False]
    bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert lab['writes'][0][1][service.FUNNEL] == admission.APPROVED
    assert service.FUNNEL not in lab['writes'][1][1]


def test_multi_assignee_preserved_while_other_records_can_update(lab):
    lab['rows']['1']['fields'][service.ASSIGNEES+'LookupId']=['7','9']
    p=plan(lab,service.ASSIGNEES,'7')
    assert p['rows'][0]['status']=='rejected'
    result=bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert [r['status'] for r in result['rows']]==['rejected','confirmed']
    assert [i for i,_,_ in lab['writes']]==['2']


@pytest.mark.parametrize('field,value', [('Title','hack'),(service.PROJECT,'4'),(service.COMMENTS,'hack'),(service.STATUS,'Inventado'),(service.ASSIGNEES,'7,9'),(service.PLANNED,'2026-02-30')])
def test_manipulated_target_or_value_rejected_before_patch(lab,field,value):
    with pytest.raises(ValueError): plan(lab,field,value)
    assert not lab['writes']


def test_chunking_double_submit_partial_failures_and_safe_reviewed_retry(lab,caplog):
    p=plan(lab,ids=['1','2','3','4','5','6'])
    lab['rows']['2']['eTag']='"8"'
    lab['modes'].update({'3':403,'4':'timeout','5':'applied_timeout'})
    with caplog.at_level('INFO',logger='danwor.solicitudes.audit'):
        first=bulk.execute('actor',p['id'],'private-token',confirmed=True)
        assert first['state']=='running' and first['rows'][5]['status']=='pending'
        result=bulk.execute('actor',p['id'],'private-token',confirmed=True)
    assert [r['status'] for r in result['rows']]==['confirmed','conflict','rejected','uncertain','confirmed','confirmed']
    count=len(lab['writes'])
    bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert len(lab['writes'])==count
    retry=bulk.retry('actor',p['id'],'fake')
    assert retry['ids']==['2','3','4'] and retry['state']=='editing'
    assert len(lab['writes'])==count
    lab['modes'].clear()
    reviewed=bulk.review('actor',retry['id'],'fake',retry['field'],retry['value'])
    retried=bulk.execute('actor',reviewed['id'],'fake',confirmed=True)
    assert all(r['status']=='confirmed' for r in retried['rows'])
    assert sum(i=='1' for i,_,_ in lab['writes'])==1
    assert sum(i=='5' for i,_,_ in lab['writes'])==1
    assert 'private-token' not in caplog.text
    assert 'operation=bulk' in caplog.text


@pytest.mark.parametrize('code,status',[(401,'rejected'),(403,'rejected'),(412,'conflict'),(429,'rejected'),(503,'uncertain')])
def test_graph_failures_are_individual_not_transactional(lab,code,status):
    p=plan(lab)
    lab['modes']['1']=code
    result=bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert result['rows'][0]['status']==status and result['rows'][1]['status']=='confirmed'


def test_same_value_no_patch_and_no_invalidation(lab):
    p=plan(lab,value='Baja')
    assert all(r['status']=='unchanged' for r in p['rows'])
    bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert not lab['writes'] and not lab['invalidated']


def test_actor_expiry_and_confirmation_fail_closed(lab):
    p=plan(lab)
    for actor,key,confirm in [('other',p['id'],True),('actor','unknown',True),('actor',p['id'],False)]:
        with pytest.raises(ValueError): bulk.execute(actor,key,'fake',confirmed=confirm)
    bulk._plans[p['id']]['expires']=0
    with pytest.raises(ValueError): bulk.get('actor',p['id'])
    assert not lab['writes']


def test_concurrent_duplicate_is_not_processed(lab,monkeypatch):
    p=plan(lab,ids=['1'])
    entered,release=Event(),Event()
    original=repository.update_item
    def slow(*args):
        entered.set(); assert release.wait(5); return original(*args)
    monkeypatch.setattr(repository,'update_item',slow)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first=executor.submit(bulk.execute,'actor',p['id'],'fake',confirmed=True)
        assert entered.wait(5)
        with pytest.raises(ValueError,match='procesándose'): bulk.execute('actor',p['id'],'fake',confirmed=True)
        release.set(); assert first.result()['state']=='complete'
    assert len(lab['writes'])==1


def test_share_resolves_authorized_titles_and_internal_links_without_writes(lab):
    lab['rows']['1']['fields']['Title']='<script>never execute</script>'
    response=send(lab,'compartir',selected_ids='["1","2"]')
    soup=BeautifulSoup(response.text,'html.parser')
    assert [a['href'] for a in soup.select('[data-sol-share-link]')]==['http://testserver/operaciones/solicitudes/1/editar?mode=display','http://testserver/operaciones/solicitudes/2/editar?mode=display']
    assert '<script>never execute</script>' not in response.text and 'fake' not in response.text
    assert not lab['writes']
    assert 'ya no están disponibles' in send(lab,'compartir',selected_ids='["999"]').text


def test_routes_enforce_origin_allowlist_and_explicit_confirmation(lab,monkeypatch):
    assert lab['client'].post('/operaciones/solicitudes/bulk/preparar').status_code==403
    assert 'no permitidos' in send(lab,'ejecutar',plan_id='x',field='Title').text
    p=plan(lab)
    assert 'Confirmá' in send(lab,'ejecutar',plan_id=p['id']).text
    assert not lab['writes']
    monkeypatch.setattr(route,'_user',lambda request:None)
    assert send(lab,'compartir',selected_ids='["1"]').status_code==401


def test_previews_render_and_consolidation_cannot_execute(lab):
    prepared=send(lab,'preparar',selected_ids='["1","2"]')
    soup=BeautifulSoup(prepared.text,'html.parser')
    plan_id=soup.select_one('[name=plan_id]')['value']
    reviewed=send(lab,'revisar',plan_id=plan_id,field=service.STATUS,value='En curso')
    assert 'Aprobación de admisión' in reviewed.text and 'Confirmar y aplicar' in reviewed.text
    assert not lab['writes']
    result=send(lab,'ejecutar',plan_id=plan_id,confirm='yes')
    assert '2 confirmadas' in result.text
    count=len(lab['writes'])
    preview=send(lab,'consolidar',selected_ids='["1","2"]')
    assert 'Solo previsualización' in preview.text and 'preview_principal' in preview.text
    assert 'No se detectaron columnas' in preview.text
    assert len(lab['writes'])==count
    assert send(lab,'consolidar-ejecutar',selected_ids='["1","2"]').status_code==404
    assert 'al menos dos' in send(lab,'consolidar',selected_ids='["1"]').text


def test_shared_selection_node_invariants():
    node=shutil.which('node')
    assert node
    subprocess.run([node,str(Path(__file__).with_name('table_selection.node.js'))],check=True)


def test_selection_context_uses_full_universe_not_filtered_page_and_actor_scope(lab,monkeypatch):
    first=lab['client'].post('/operaciones/solicitudes/data',data={'graph_token':'fake','q':'Solicitud 12'})
    soup=BeautifulSoup(first.text,'html.parser')
    bar=soup.select_one('[data-sol-selection-bar]')
    assert len(soup.select('[data-sol-select]'))==1
    assert json.loads(bar['data-sol-universe'])==list(lab['rows'])
    assert len(soup.select('thead th'))==10
    assert soup.select_one('[data-draggable-columns-key]')['data-layout-version']=='3'
    assert soup.select_one('thead th')['data-column-fixed']=='start'
    scope=bar['data-sol-scope']
    monkeypatch.setattr(route,'_user',lambda request:{'sub':'other-actor'})
    other=BeautifulSoup(lab['client'].post('/operaciones/solicitudes/data',data={'graph_token':'fake'}).text,'html.parser')
    assert other.select_one('[data-sol-selection-bar]')['data-sol-scope']!=scope


def test_retry_keeps_deleted_failures_visible_without_blocking_other_failed_rows(lab):
    p=plan(lab)
    lab['modes'].update({'1':403,'2':403})
    bulk.execute('actor',p['id'],'fake',confirmed=True)
    del lab['rows']['2'];lab['modes'].clear()
    retried=bulk.retry('actor',p['id'],'fake')
    reviewed=bulk.review('actor',retried['id'],'fake','Prioridad','Alta')
    result=bulk.execute('actor',reviewed['id'],'fake',confirmed=True)
    assert [r['status'] for r in result['rows']]==['confirmed','rejected']


def test_metadata_drift_prevents_patch_and_missing_derived_funnel_is_uncertain(lab,monkeypatch):
    p=plan(lab)
    lab['columns'][2]['readOnly']=True
    result=bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert all(r['status']=='rejected' for r in result['rows']) and not lab['writes']
    lab['columns'][2]['readOnly']=False
    p=plan(lab,service.STATUS,'En curso')
    original=repository.update_item
    def incomplete(token,item_id,payload,etag):
        original(token,item_id,payload,etag)
        lab['rows'][item_id]['fields'][service.FUNNEL]=admission.REQUESTED
    monkeypatch.setattr(repository,'update_item',incomplete)
    result=bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert all(r['status']=='uncertain' for r in result['rows'])


def test_expired_token_pauses_pending_without_replaying_confirmed_records(lab):
    p=plan(lab,ids=[str(i) for i in range(1,7)])
    bulk.execute('actor',p['id'],'fake',confirmed=True)
    count=len(lab['writes'])
    response=lab['client'].post('/operaciones/solicitudes/bulk/ejecutar',headers={'Origin':'http://testserver'},data={'plan_id':p['id'],'confirm':'yes'})
    assert 'Continuar solicitudes pendientes' in response.text and '5 confirmadas' in response.text
    assert 'hx-trigger="load"' not in response.text
    assert len(lab['writes'])==count
    recovered=send(lab,'estado',plan_id=p['id'])
    assert '5 confirmadas' in recovered.text
    final=bulk.execute('actor',p['id'],'fake',confirmed=True)
    assert all(r['status']=='confirmed' for r in final['rows']) and len(lab['writes'])==6


def test_shared_display_has_no_write_controls_and_preserves_edit_mode(lab):
    lab['options'][service.PROJECT] = {'4': 'Proyecto simulado'}
    response = lab['client'].post('/operaciones/solicitudes/1/detalle?mode=display', data={'graph_token': 'fake'})
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, 'html.parser')
    assert soup.select_one('[data-solicitudes-display]')
    assert not soup.select('form, [data-solicitudes-write-token], button[type=submit], [hx-post]')
    assert 'Solo lectura' in response.text
    editable = lab['client'].post('/operaciones/solicitudes/1/detalle', data={'graph_token': 'fake'})
    assert BeautifulSoup(editable.text, 'html.parser').select_one('form[data-solicitudes-write-token]')
    assert not lab['writes']


def project_catalog(lab):
    lab['columns'].append({'name': service.PROJECT, 'lookup': {'allowMultipleValues': False}})
    lab['options'][service.PROJECT] = {'4': 'Proyecto actual', '8': 'Otro proyecto'}


def test_bulk_entry_requires_two_distinct_requests_but_sharing_accepts_one(lab):
    for ids in ('["1"]', '["1","1"]'):
        assert 'al menos dos solicitudes' in send(lab, 'preparar', selected_ids=ids).text
    assert not bulk._plans
    assert 'Revisar cambios' in send(lab, 'preparar', selected_ids='["1","2"]').text
    assert 'data-sol-share-link' in send(lab, 'compartir', selected_ids='["1"]').text
    assert not lab['writes']


def test_multiple_fields_single_patch_and_reviewed_retry(lab):
    project_catalog(lab)
    p = bulk.prepare('actor', 'fake', ['1','2'])
    changes = {'Prioridad': 'Alta', service.PROJECT: '8', service.ASSIGNEES: '7'}
    reviewed = bulk.review('actor', p['id'], 'fake', '__multiple__', changes)
    assert 'Otro proyecto' in reviewed['rows'][0]['after']
    lab['modes']['2'] = 403
    result = bulk.execute('actor', p['id'], 'fake', confirmed=True)
    payload = lab['writes'][0][1]
    assert payload['ProyectoLookupId'] == '8'
    assert payload[service.ASSIGNEES+'LookupId'] == ['7']
    assert payload['Prioridad'] == 'Alta'
    assert not set(payload) & {service.DESCRIPTION, service.COMMENTS, service.PLANNED, service.ACTUAL, service.STATUS}
    retry = bulk.retry('actor', result['id'], 'fake')
    assert retry['ids'] == ['2'] and retry['changes'] == changes


def test_unchecked_values_are_ignored_and_no_selection_is_rejected(lab):
    project_catalog(lab)
    p = bulk.prepare('actor', 'fake', ['1'])
    response = send(lab, 'revisar', plan_id=p['id'], apply_Prioridad='yes', value_Prioridad='Alta', **{'value_Proyecto':'999'})
    assert 'Confirmar y aplicar' in response.text
    bulk.execute('actor', p['id'], 'fake', confirmed=True)
    assert lab['writes'][0][1] == {'Prioridad':'Alta'}
    p = bulk.prepare('actor', 'fake', ['2'])
    with pytest.raises(ValueError): bulk.review('actor', p['id'], 'fake', '__multiple__', {})


def test_explicit_project_clear_and_legacy_assignment_are_safe(lab):
    project_catalog(lab)
    p = bulk.prepare('actor', 'fake', ['1'])
    bulk.review('actor', p['id'], 'fake', '__multiple__', {service.PROJECT:''})
    bulk.execute('actor', p['id'], 'fake', confirmed=True)
    assert lab['writes'][0][1] == {'ProyectoLookupId':None}
    lab['rows']['2']['fields'][service.ASSIGNEES+'LookupId'] = ['7','9']
    p = bulk.prepare('actor', 'fake', ['2'])
    bulk.review('actor', p['id'], 'fake', '__multiple__', {'Prioridad':'Alta'})
    bulk.execute('actor', p['id'], 'fake', confirmed=True)
    assert lab['writes'][-1][1] == {'Prioridad':'Alta'}
    p = bulk.prepare('actor', 'fake', ['2'])
    result = bulk.review('actor', p['id'], 'fake', '__multiple__', {'Prioridad':'Media',service.ASSIGNEES:'7'})
    assert result['rows'][0]['status'] == 'rejected'


def test_multi_field_invalid_id_or_protected_field_cannot_write(lab):
    project_catalog(lab)
    for changes in ({service.PROJECT:'999'}, {'Title':'No'}, {service.PROJECT:'8', 'Prioridad':'Invalid'}):
        p = bulk.prepare('actor', 'fake', ['1'])
        with pytest.raises(ValueError): bulk.review('actor', p['id'], 'fake', '__multiple__', changes)
    assert not lab['writes']
