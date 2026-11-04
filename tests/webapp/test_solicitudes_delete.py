import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_repository as repository


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(route, "_user", lambda request: {"sub": "delete-actor"})
    return TestClient(app)


def send(client, **changes):
    return client.post("/operaciones/solicitudes/1/eliminar",
                       headers={"Origin": "http://testserver", "HX-Request": "true"},
                       data={"graph_token": "fake", "etag": '"7"', "confirm_delete": "yes", **changes})


def test_confirmation_and_access_precede_graph(client, monkeypatch):
    def unexpected(*args):
        pytest.fail("Graph must not be called")
    monkeypatch.setattr(repository, "read_item", unexpected)
    monkeypatch.setattr(repository, "delete_item", unexpected)
    assert "Confirmá" in send(client, confirm_delete="").text
    assert "Confirmá" in send(client, Title="protected").text
    assert "Autorizar escritura" in send(client, graph_token="").text
    assert client.post("/operaciones/solicitudes/1/eliminar").status_code == 403
    monkeypatch.setattr(route, "_user", lambda request: None)
    assert send(client).status_code == 401


def test_current_etag_required(client, monkeypatch):
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"8"'})
    monkeypatch.setattr(repository, "delete_item", lambda *args: pytest.fail("Unexpected DELETE"))
    result = send(client)
    assert "cambió" in result.text and "data-solicitudes-review" in result.text


def test_confirmed_delete_invalidates_snapshot_and_redirects(client, monkeypatch, caplog):
    calls, invalidated = [], []
    def read(*args):
        if calls:
            raise HTTPError("", 404, "Absent", {}, None)
        return {"eTag": '"7"'}
    monkeypatch.setattr(repository, "read_item", read)
    monkeypatch.setattr(repository, "delete_item", lambda *args: calls.append(args))
    monkeypatch.setattr(route.solicitudes_snapshot, "invalidate", invalidated.append)
    with caplog.at_level("INFO", logger="danwor.solicitudes.audit"):
        result = send(client)
    assert calls == [("fake", "1", '"7"')]
    assert invalidated == ["delete-actor"]
    assert json.loads(result.headers["HX-Trigger-After-Swap"])["solicitudes-deleted"]["url"] == "/operaciones/solicitudes"
    assert "operation=delete outcome=confirmed_absent" in caplog.text
    assert "fake" not in caplog.text


@pytest.mark.parametrize("code", [401, 403, 412, 429, 503])
def test_graph_rejection_or_uncertainty_preserves_form(client, monkeypatch, code):
    calls = []
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"'})
    def delete(*args):
        calls.append(args)
        raise HTTPError("", code, "Graph", {}, None)
    monkeypatch.setattr(repository, "delete_item", delete)
    result = send(client)
    assert result.status_code == 200
    assert len(calls) == 1
    assert "<form" not in result.text
    assert "HX-Trigger-After-Swap" not in result.headers
    if code in (412, 503):
        assert "data-solicitudes-review" in result.text
    if code == 403:
        assert "denegó" in result.text


def test_timeout_followed_by_absence_confirms_without_retry(client, monkeypatch):
    calls = []
    def read(*args):
        if calls:
            raise HTTPError("", 404, "Absent", {}, None)
        return {"eTag": '"7"'}
    def delete(*args):
        calls.append(args)
        raise TimeoutError()
    monkeypatch.setattr(repository, "read_item", read)
    monkeypatch.setattr(repository, "delete_item", delete)
    assert "HX-Trigger-After-Swap" in send(client).headers
    assert len(calls) == 1


def test_transport_deletes_item_with_exact_if_match(monkeypatch):
    from core import microsoft365
    calls = []
    monkeypatch.setattr(repository, "list_url", lambda token: repository.GRAPH + "/sites/test/lists/test")
    def send_request(request, **kwargs):
        calls.append(request)
        assert request.get_method() == "DELETE"
        assert request.full_url.endswith("/items/1")
        assert request.get_header("If-match") == '"7"'
        assert request.data is None
        return BytesIO(b"")
    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", send_request)
    repository.delete_item("fake", "1", '"7"')
    with pytest.raises(ValueError):
        repository.delete_item("fake", "1", "*")
    assert len(calls) == 1


def test_list_delete_uses_existing_endpoint_and_exact_snapshot_etag(client, monkeypatch):
    from bs4 import BeautifulSoup
    from webapp.services import solicitudes_snapshot
    monkeypatch.setattr(solicitudes_snapshot, 'read', lambda *args, **kwargs: [
        {'id': '1', 'eTag': '"7"', 'fields': {'Title': 'Ejemplo'}},
        {'id': '2', 'fields': {'Title': 'Sin versión'}},
    ])
    response = client.post('/operaciones/solicitudes/data', data={'graph_token': 'fixture'})
    soup = BeautifulSoup(response.text, 'html.parser')
    button = soup.select_one('button[aria-label="Eliminar solicitud 1"]')
    assert button['hx-post'] == '/operaciones/solicitudes/1/eliminar'
    assert button['hx-trigger'] == 'confirmed'
    assert button['hx-target'] == '#solicitudes-list-result'
    assert json.loads(button['hx-vals']) == {'etag': '"7"', 'confirm_delete': 'yes'}
    assert button.has_attr('data-solicitudes-write-token') and button.has_attr('data-dw-confirm')
    assert not button.has_attr('disabled')
    assert soup.select_one('button[aria-label="Eliminar solicitud 2"]').has_attr('disabled')


def test_list_conflict_retains_rows_and_does_not_claim_a_draft(client, monkeypatch):
    monkeypatch.setattr(repository, 'read_item', lambda *args: {'eTag': '"8"'})
    response = client.post('/operaciones/solicitudes/1/eliminar',
        headers={'Origin': 'http://testserver', 'HX-Target': 'solicitudes-list-result'},
        data={'graph_token': 'fixture', 'etag': '"7"', 'confirm_delete': 'yes'})
    assert 'El listado conserva sus datos' in response.text
    assert 'Tu borrador' not in response.text
    assert 'HX-Trigger-After-Swap' not in response.headers
