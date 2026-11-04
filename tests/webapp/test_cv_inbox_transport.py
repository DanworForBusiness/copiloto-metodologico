import io
import json
import urllib.error
import pytest
from core import microsoft365
from webapp.services import cv_inbox as inbox


@pytest.mark.parametrize('mode,method',[(inbox.MODE_INCORPORATED,'PATCH'),(inbox.MODE_DELETE,'DELETE')])
def test_retirement_uses_the_real_shared_transport_contract(monkeypatch,mode,method):
    item={'id':'source','eTag':'"7"','parentReference':{'id':'inbox'}}
    monkeypatch.setattr(inbox,'_get_item',lambda *args:item)
    monkeypatch.setattr(inbox,'_same_content',lambda *args:True)
    monkeypatch.setattr(inbox,'_subfolder',lambda *args:'incorporated-folder')
    calls=[]
    def opened(request,timeout):
        calls.append(request)
        return io.BytesIO(b'{"id":"source"}')
    monkeypatch.setattr(microsoft365.urllib.request,'urlopen',opened)
    result=inbox._physical(mode,{'drive_id':'drive','item_id':'source','parent_id':'inbox'},'fixture')
    assert result==(inbox.RETIRE_DONE,None) and len(calls)==1
    assert calls[0].method==method and calls[0].get_header('If-match')=='"7"'
    if method=='PATCH':assert json.loads(calls[0].data)=={'parentReference':{'id':'incorporated-folder'}}


def test_original_deletion_reconciles_a_lost_response_without_another_delete(monkeypatch):
    observations=iter([{'id':'source','eTag':'"7"','parentReference':{'id':'inbox'}},None])
    monkeypatch.setattr(inbox,'_get_item',lambda *args:next(observations))
    monkeypatch.setattr(inbox,'_same_content',lambda *args:True)
    calls=[]
    def lost(request,timeout):
        calls.append(request)
        raise urllib.error.URLError('simulated lost response')
    monkeypatch.setattr(microsoft365.urllib.request,'urlopen',lost)
    assert inbox._physical(inbox.MODE_DELETE,{'drive_id':'drive','item_id':'source','parent_id':'inbox'},'fixture')==(inbox.RETIRE_DONE,None)
    assert len(calls)==1
