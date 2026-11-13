import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_creation as creation
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service


@pytest.fixture
def schema():
    kinds = {
        "Title": {"text": {"maxLength": 255}, "required": True},
        service.STATUS: {"choice": {"choices": ["Pendiente", "Planificado"]}},
        "Prioridad": {"choice": {"choices": ["Baja", "Media", "Alta"]}},
        service.PROJECT: {"lookup": {"allowMultipleValues": False}},
        service.ASSIGNEES: {"lookup": {"allowMultipleValues": True}},
        service.PLANNED: {"dateTime": {}}, service.ACTUAL: {"dateTime": {}},
        service.DESCRIPTION: {"text": {}}, service.COMMENTS: {"text": {}},
        service.FUNNEL: {"choice": {"choices": ["Solicitado"]}, "required": True,
                         "defaultValue": {"value": "Solicitado"}},
        "Created": {"dateTime": {}, "readOnly": True, "required": True},
        "Calculated": {"calculated": {}, "required": True},
    }
    return [{"name": name, **value} for name, value in kinds.items()]


@pytest.fixture
def catalogs():
    return {service.PROJECT: {"4": "Proyecto simulado"}, service.ASSIGNEES: {"9": "Colaborador simulado"}, service.STATUS: ["Pendiente", "Planificado"]}


@pytest.fixture
def submitted():
    return {"Title": "Solicitud simulada"}


@pytest.fixture
def client(monkeypatch, schema, catalogs):
    monkeypatch.setattr(route, "_user", lambda request: {"sub": "create-test"})
    monkeypatch.setattr(repository, "read_creation_columns", lambda token: schema)
    monkeypatch.setattr(repository, "lookup_options", lambda token, **kwargs: catalogs)
    return TestClient(app)


def test_initial_form_reuses_edit_controls(client):
    html = client.post("/operaciones/solicitudes/nueva/datos", data={"graph_token": "fake"}).text
    assert 'id="sol-title" type="text" name="Title" value="" required maxlength="255"' in html
    assert '>Crear solicitud</button>' in html
    assert 'data-architecture-after-creation' in html
    for field in (service.STATUS, service.ASSIGNEES, service.ACTUAL, service.COMMENTS):
        assert f'name="{field}"' not in html
    assert 'id="sol-status"' not in html
    assert 'name="submission_id"' in html and 'name="etag"' not in html
    assert html.count('role="combobox"') == 1  # Proyecto; assignment is managed after creation.
    assert 'data-solicitudes-write-token' in html
    assert 'name="Created"' not in html
    assert '@beforeunload.window' in html


def test_payload_uses_lookup_ids_dates_safe_text_and_omits_managed_defaults(schema, catalogs, submitted):
    submitted.update({service.PROJECT: "4", service.PLANNED: "2026-11-04",
                      service.DESCRIPTION: "<script>& texto\nOtra línea"})
    fields = creation.payload(submitted, catalogs, schema)["fields"]
    assert fields["Title"] == "Solicitud simulada"
    assert fields[service.PROJECT + "LookupId"] == "4"
    assert fields[service.STATUS] == "Pendiente"
    assert service.ASSIGNEES + "LookupId" not in fields
    assert fields[service.PLANNED] == "2026-11-04T00:00:00Z"
    assert fields[service.DESCRIPTION] == "<div>&lt;script&gt;&amp; texto<br>Otra línea</div>"
    assert service.FUNNEL not in fields
    assert "Created" not in fields and service.CREATION_DATE not in fields
    assert service.ACTUAL not in fields


@pytest.mark.parametrize("changes", [
    {"Title": " "}, {"Title": "x" * 256}, {service.STATUS: "Inexistente"},
    {service.STATUS: ""}, {"Prioridad": "Urgente"}, {service.PROJECT: "Proyecto simulado"},
    {service.ASSIGNEES: ["9", "10"]}, {service.PLANNED: "2026-02-30"},
    {"id": "1"}, {"Created": "2026-01-01"}, {service.FUNNEL: "Solicitado"},
])
def test_reject_invalid_or_protected_creation_fields(changes, schema, catalogs, submitted):
    with pytest.raises(ValueError):
        creation.payload({**submitted, **changes}, catalogs, schema)


def test_schema_drift_and_additional_required_fields_block_before_post(schema, catalogs, submitted):
    for change in (
        {"name": "Extra", "required": True, "text": {}},
        {"name": service.FUNNEL, "required": True, "choice": {"choices": ["Solicitado"]}},
        {"name": service.PROJECT, "lookup": {"allowMultipleValues": True}},
        {"name": "Title", "text": {}, "readOnly": True},
    ):
        altered = [column for column in schema if column["name"] != change["name"]] + [change]
        with pytest.raises(ValueError):
            creation.payload(submitted, catalogs, altered)


def test_success_duplicate_and_snapshot_invalidation(client, monkeypatch, submitted, caplog):
    calls = []
    invalidated = []
    monkeypatch.setattr(repository, "create_item", lambda token, body: calls.append(body) or {"id": "7"})
    monkeypatch.setattr(repository, "read_item", lambda token, item_id: {"id": "7", "fields": {**submitted, service.STATUS: "Pendiente"}})
    monkeypatch.setattr(creation.solicitudes_snapshot, "invalidate", invalidated.append)
    ticket = creation.issue("create-test")
    data = {**submitted, "graph_token": "fake-secret", "submission_id": ticket}
    with caplog.at_level("INFO", logger="danwor.solicitudes.audit"):
        for _ in range(2):
            response = client.post("/operaciones/solicitudes/nueva", data=data, headers={"Origin": "http://testserver"})
            assert response.status_code == 200
            assert json.loads(response.headers["HX-Trigger-After-Swap"])["solicitudes-created"]["url"].endswith("/7/editar")
    assert len(calls) == 1 and invalidated == ["create-test"]
    assert "fake-secret" not in caplog.text and submitted["Title"] not in caplog.text


def test_authorization_origin_and_manipulated_requests(client, monkeypatch, submitted):
    def forbidden(*args):
        pytest.fail("Unexpected Graph call")
    monkeypatch.setattr(repository, "create_item", forbidden)
    monkeypatch.setattr(repository, "read_creation_columns", forbidden)
    assert client.post("/operaciones/solicitudes/nueva", data=submitted).status_code == 403
    headers = {"Origin": "http://testserver"}
    assert "campos no permitidos" in client.post("/operaciones/solicitudes/nueva", data={**submitted, "id": "1"}, headers=headers).text
    assert "Autorizar escritura con Microsoft" in client.post("/operaciones/solicitudes/nueva", data=submitted, headers=headers).text
    monkeypatch.setattr(route, "_user", lambda request: None)
    assert client.post("/operaciones/solicitudes/nueva", data=submitted, headers=headers).status_code == 401


@pytest.mark.parametrize("code", [400, 401, 403, 429, 503])
def test_graph_errors_preserve_draft_and_avoid_uncertain_replays(client, monkeypatch, submitted, code):
    calls = []
    def fail(*args):
        calls.append(1)
        raise HTTPError("", code, "Graph", {}, None)
    monkeypatch.setattr(repository, "create_item", fail)
    ticket = creation.issue("create-test")
    data = {**submitted, "graph_token": "fake", "submission_id": ticket}
    response = client.post("/operaciones/solicitudes/nueva", data=data, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert '<form' not in response.text and '<textarea' not in response.text
    if code == 503:
        assert "no se repetirá" in response.text
        client.post("/operaciones/solicitudes/nueva", data=data, headers={"Origin": "http://testserver"})
        assert calls == [1]
    elif code == 403:
        assert "denegó" in response.text


def test_timeout_and_failed_confirmation_never_repeat_post(client, monkeypatch, submitted):
    calls = []
    def create_item(*args):
        calls.append(1)
        raise TimeoutError()
    monkeypatch.setattr(repository, "create_item", create_item)
    ticket = creation.issue("create-test")
    with pytest.raises(creation.UncertainCreation):
        creation.create("create-test", ticket, "fake", submitted)
    with pytest.raises(creation.SubmissionError):
        creation.create("create-test", ticket, "fake", submitted)
    assert calls == [1]


def test_concurrent_submits_and_other_actor_are_rejected(client, monkeypatch, submitted):
    entered, release = Event(), Event()
    calls = []
    def create_item(*args):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"id": "8"}
    monkeypatch.setattr(repository, "create_item", create_item)
    monkeypatch.setattr(repository, "read_item", lambda *args: {"id": "8", "fields": {**submitted, service.STATUS: "Pendiente"}})
    ticket = creation.issue("create-test")
    with ThreadPoolExecutor() as pool:
        first = pool.submit(creation.create, "create-test", ticket, "fake", submitted)
        assert entered.wait(5)
        try:
            for actor in ("other-actor", "create-test"):
                with pytest.raises(creation.SubmissionError):
                    creation.create(actor, ticket, "fake", submitted)
        finally:
            release.set()
        assert first.result() == "8"
    assert calls == [1]


def test_unknown_ticket_after_restart_never_contacts_graph(client, monkeypatch, submitted):
    monkeypatch.setattr(repository, "read_creation_columns", lambda *args: pytest.fail("Graph contacted"))
    with pytest.raises(creation.SubmissionError):
        creation.create("create-test", "unknown-ticket", "fake", submitted)


def test_graph_create_transport_uses_items_collection_and_fields_envelope(monkeypatch):
    from core import microsoft365
    from io import BytesIO

    monkeypatch.setattr(repository, "list_url", lambda token: repository.GRAPH + "/sites/test/lists/test")
    calls = []
    def send(request, **kwargs):
        calls.append(request)
        assert request.get_method() == "POST"
        assert request.full_url.endswith("/items")
        assert json.loads(request.data) == {"fields": {"Title": "Simulada"}}
        return BytesIO(b'{"id":"77"}')
    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", send)
    assert repository.create_item("fake", {"fields": {"Title": "Simulada"}}) == {"id": "77"}
    assert len(calls) == 1


def test_failed_confirmation_cannot_issue_second_post(client, monkeypatch, submitted):
    posts = []
    monkeypatch.setattr(repository, "create_item", lambda *args: posts.append(1) or {"id": "8"})
    monkeypatch.setattr(repository, "read_item", lambda *args: {"id": "8", "fields": {**submitted, "Title": "Otro valor"}})
    ticket = creation.issue("create-test")
    with pytest.raises(creation.UncertainCreation):
        creation.create("create-test", ticket, "fake", submitted)
    with pytest.raises(creation.SubmissionError):
        creation.create("create-test", ticket, "fake", submitted)
    assert posts == [1]


def test_invalid_form_can_be_corrected_with_same_ticket(client, monkeypatch, submitted):
    posts = []
    monkeypatch.setattr(repository, "create_item", lambda *args: posts.append(1) or {"id": "8"})
    monkeypatch.setattr(repository, "read_item", lambda *args: {"id": "8", "fields": {**submitted, service.STATUS: "Pendiente"}})
    ticket = creation.issue("create-test")
    with pytest.raises(ValueError):
        creation.create("create-test", ticket, "fake", {**submitted, "Title": ""})
    assert posts == []
    assert creation.create("create-test", ticket, "fake", submitted) == "8"
    with pytest.raises(creation.SubmissionError):
        creation.create("create-test", ticket, "fake", {**submitted, "Title": "Otra solicitud"})
    assert posts == [1]
