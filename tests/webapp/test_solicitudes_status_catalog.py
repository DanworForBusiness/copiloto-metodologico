from urllib.error import HTTPError

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from webapp.main import app
from webapp import access
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_creation as creation
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service

STATES = ["Pendiente", "Planificado", "En curso", "Finalizado", "Suspendido"]
BASE = repository.GRAPH + "/sites/test/lists/requests"


@pytest.fixture
def columns():
    return [
        {"name": "Title", "text": {"maxLength": 255}, "required": True},
        {"name": service.STATUS, "choice": {"choices": STATES.copy()}},
        {"name": "Prioridad", "choice": {"choices": ["Baja", "Media", "Alta"]}},
        {"name": service.PROJECT, "lookup": {"listId": "projects", "columnName": "Title", "allowMultipleValues": False}},
        {"name": service.ASSIGNEES, "lookup": {"listId": "people", "columnName": "Title", "allowMultipleValues": True}},
        *[{"name": field, "dateTime": {}} for field in (service.PLANNED, service.ACTUAL)],
        *[{"name": field, "text": {}} for field in service.NARRATIVE_FIELDS],
    ]


@pytest.fixture
def graph(monkeypatch, columns):
    calls = []
    monkeypatch.setattr(repository, "list_url", lambda token: BASE)
    def get(url, token):
        calls.append(url)
        if url.startswith(BASE + "/columns"):
            return {"value": columns}
        if "/lists/projects/items?" in url or "/lists/people/items?" in url:
            return {"value": [{"id": "1", "fields": {"Title": "Referencia simulada"}}]}
        raise AssertionError("Unexpected Graph GET")
    monkeypatch.setattr(repository, "graph_get_json", get)
    return calls


@pytest.fixture
def client(monkeypatch):
    actor = "catalog-owner@example.invalid"
    monkeypatch.setattr(access, "APP_OWNER", actor)
    monkeypatch.setattr(route, "_user", lambda request: {"sub": actor})
    return TestClient(app)


@pytest.mark.parametrize("current,target", [
    ("Pendiente", "En curso"), ("Pendiente", "Finalizado"),
    ("Finalizado", "Pendiente"), ("Suspendido", "Finalizado"),
    ("Pendiente", "Pendiente"),
])
def test_form_and_backend_share_graph_catalog(client, monkeypatch, graph, current, target):
    item = {"id": "9", "eTag": '"7"', "fields": {"Title": "Solicitud simulada", service.STATUS: current}}
    monkeypatch.setattr(repository, "read_item", lambda *args: item)
    patches = []
    def update(token, item_id, payload, etag):
        patches.append(payload)
        assert item_id == "9" and etag == '"7"'
        item["fields"].update(payload)
        item["eTag"] = '"8"'
    monkeypatch.setattr(repository, "update_item", update)
    html = client.post("/operaciones/solicitudes/9/detalle", data={"graph_token": "fake"}).text
    select = BeautifulSoup(html, "html.parser").select_one("#sol-status")
    assert [option["value"] for option in select.select("option")] == STATES
    assert select.select_one("option[selected]")["value"] == current
    assert graph.count(BASE + "/columns") == 1
    graph.clear()
    saved = client.post("/operaciones/solicitudes/9/guardar", headers={"Origin": "http://testserver"},
                        data={"graph_token": "fake", "etag": '"7"', service.STATUS: target})
    assert saved.status_code == 200
    assert patches == ([] if current == target else [{service.STATUS: target}])
    assert ("No hay cambios" if current == target else "Guardado confirmado") in saved.text
    assert graph.count(BASE + "/columns") == 1


def test_extra_state_and_metadata_order_are_not_hardcoded(client, monkeypatch, columns, graph):
    ordered = ["Nuevo estado del catálogo", "Finalizado", "Pendiente"]
    columns[1]["choice"]["choices"] = ordered
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {service.STATUS: "Pendiente"}})
    html = client.post("/operaciones/solicitudes/9/detalle", data={"graph_token": "fake"}).text
    select = BeautifulSoup(html, "html.parser").select_one("#sol-status")
    assert [option["value"] for option in select.select("option")] == ordered
    assert service.changes(service.form_values({service.STATUS: "Finalizado"}),
                           {service.STATUS: ordered[0]}, {service.STATUS: ordered}) == {service.STATUS: ordered[0]}


@pytest.mark.parametrize("choices", [None, [], [""], ["Pendiente", "Pendiente"], [None], [123]])
def test_invalid_catalog_is_controlled_and_cannot_patch(client, monkeypatch, columns, graph, choices):
    columns[1]["choice"]["choices"] = choices
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {service.STATUS: "Pendiente"}})
    monkeypatch.setattr(repository, "update_item", lambda *args: pytest.fail("Unexpected PATCH"))
    html = client.post("/operaciones/solicitudes/9/detalle", data={"graph_token": "fake"}).text
    assert "catálogo de Status Arquitectura" in html and "<form" not in html
    assert graph == [BASE + "/columns"]
    html = client.post("/operaciones/solicitudes/9/guardar", headers={"Origin": "http://testserver"},
                       data={"graph_token": "fake", "etag": '"7"', service.STATUS: "En curso"}).text
    assert "catálogo de Status Arquitectura" in html and "<form" not in html


def test_metadata_http_failure_preserves_draft_and_never_patches(client, monkeypatch):
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {service.STATUS: "Pendiente"}})
    monkeypatch.setattr(repository, "list_url", lambda token: BASE)
    def unavailable(*args):
        raise HTTPError("", 503, "Metadata unavailable", {}, None)
    monkeypatch.setattr(repository, "graph_get_json", unavailable)
    monkeypatch.setattr(repository, "update_item", lambda *args: pytest.fail("Unexpected PATCH"))
    assert "<form" not in client.post("/operaciones/solicitudes/9/detalle", data={"graph_token": "fake"}).text
    response = client.post("/operaciones/solicitudes/9/guardar", headers={"Origin": "http://testserver"},
                           data={"graph_token": "fake", "etag": '"7"', service.STATUS: "En curso"})
    assert "No se pudieron consultar" in response.text and "<form" not in response.text


def test_invalid_state_rejected_in_backend(client, monkeypatch, graph):
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {service.STATUS: "Pendiente"}})
    monkeypatch.setattr(repository, "update_item", lambda *args: pytest.fail("Unexpected PATCH"))
    response = client.post("/operaciones/solicitudes/9/guardar", headers={"Origin": "http://testserver"},
                           data={"graph_token": "fake", "etag": '"7"', service.STATUS: "Inventado"})
    assert "Status Arquitectura válido" in response.text and "<form" not in response.text


def test_catalog_can_be_on_second_metadata_page(monkeypatch):
    calls = []
    def get(url, token):
        calls.append(url)
        return ({"value": [{"name": "Other"}], "@odata.nextLink": BASE + "/columns?$skiptoken=2"}
                if len(calls) == 1 else {"value": [{"name": service.STATUS, "choice": {"choices": STATES}}]})
    monkeypatch.setattr(repository, "graph_get_json", get)
    assert repository.lookup_options("fake", (service.STATUS,), base=BASE) == {service.STATUS: STATES}
    assert calls == [BASE + "/columns", BASE + "/columns?$skiptoken=2"]


def test_creation_reuses_metadata_and_defaults_to_pending(client, columns, graph):
    html = client.post("/operaciones/solicitudes/nueva/datos", data={"graph_token": "fake"}).text
    select = BeautifulSoup(html, "html.parser").select_one("#sol-status")
    assert select is None
    assert BeautifulSoup(html, "html.parser").select_one('[data-architecture-after-creation]')
    assert len([url for url in graph if "/columns" in url]) == 1
    options = {service.PROJECT: {}, service.ASSIGNEES: {}}
    assert creation.payload({"Title": "Simulada"}, options, columns)["fields"][service.STATUS] == "Pendiente"
    for state in STATES:
        with pytest.raises(ValueError, match="campos no permitidos"):
            creation.payload({"Title": "Simulada", service.STATUS: state}, options, columns)


def test_creation_without_pending_does_not_invent_a_default(client, columns, graph):
    columns[1]["choice"]["choices"] = ["En curso", "Finalizado"]
    html = client.post("/operaciones/solicitudes/nueva/datos", data={"graph_token": "fake"}).text
    select = BeautifulSoup(html, "html.parser").select_one("#sol-status")
    assert select is None and "Pendiente" in html
    assert BeautifulSoup(html, "html.parser").select_one('form') is None
    with pytest.raises(ValueError, match="Status Arquitectura válido"):
        creation.payload({"Title": "Simulada"}, {service.PROJECT: {}, service.ASSIGNEES: {}}, columns)


def test_missing_status_column_is_not_an_unrestricted_catalog():
    with pytest.raises(service.StatusCatalogError):
        service.status_choices([])


def test_unchanged_legacy_state_is_not_accepted_as_an_explicit_catalog_value():
    with pytest.raises(ValueError):
        service.changes(service.form_values({service.STATUS: "Estado retirado"}),
                        {service.STATUS: "Estado retirado"}, {service.STATUS: STATES})
    assert service.changes(service.form_values({}), {service.STATUS: ""}, {}) == {}
