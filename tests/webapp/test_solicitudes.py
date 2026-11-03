from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service


def test_transition_matrix():
    assert service.allowed_destinations(None) == ("Pendiente",)
    assert service.allowed_destinations("") == ("Pendiente",)
    assert service.allowed_destinations("Pendiente") == ("Planificado", "Suspendido")
    assert service.allowed_destinations("Planificado") == ("Pendiente", "En curso", "Suspendido")
    assert service.allowed_destinations("En curso") == ("Planificado", "Suspendido", "Finalizado")
    assert service.allowed_destinations("Suspendido") == ("Pendiente", "Planificado", "En curso")
    assert service.allowed_destinations("Finalizado") == ()
    assert service.allowed_destinations("Desconocido") == ()


def test_read_follows_graph_pages(monkeypatch):
    base = repository.GRAPH
    list_url = f"{base}/sites/example,site,web/lists/{repository.LIST_ID}"
    next_link = f"{list_url}/items?$skiptoken=next"
    responses = {
        f"{base}/sites/danwor.sharepoint.com:/sites/Gestion": {"id": "example,site,web"},
        f"{list_url}/items?$expand=fields": {"value": [{"id": "1"}], "@odata.nextLink": next_link},
        next_link: {"value": [{"id": "2"}]},
    }
    monkeypatch.setattr(repository, "graph_get_json", lambda url, token: responses[url])
    assert repository.read_requests("test-token") == [{"id": "1"}, {"id": "2"}]


def test_global_search_and_filter_uses_functional_date():
    def row(item_id, date, priority):
        return {"id": item_id, "fields": {
            service.CREATION_DATE: date,
            service.DESCRIPTION: "<b>Arquitectura</b> &amp; datos",
            "Prioridad": priority,
        }}

    rows = [row("1", "2026-01-01", "Alta"), row("2", "2026-02-01", "Alta"),
            row("3", "2026-03-01", "Baja")]
    assert [item["id"] for item in service.select_rows(rows, "arquitectura", {"Prioridad": "Alta"})] == ["2", "1"]


def test_partial_changes_preserve_untouched_html_and_dates():
    fields = {
        service.STATUS: None,
        service.DESCRIPTION: '<div style="color:red"><img src="/image.png"></div>',
        service.ASSIGNEES + "LookupId": ["7", "9"],
        service.PLANNED: "2026-11-03T00:00:00Z",
    }
    current = service.form_values(fields)
    submitted = dict(current, **{service.STATUS: "Pendiente", service.PLANNED: "", service.ASSIGNEES: ["9", "12"]})
    assert service.changes(current, submitted, {service.ASSIGNEES: {"9": "A", "12": "B"}}) == {
        service.STATUS: "Pendiente", service.PLANNED: None,
        service.ASSIGNEES + "LookupId@odata.type": "Collection(Edm.String)",
        service.ASSIGNEES + "LookupId": ["9", "12"],
    }


def test_invalid_transitions_and_manipulated_inputs():
    import pytest

    current = service.form_values({})
    options = {service.PROJECT: {}, service.ASSIGNEES: {}}
    for submitted in (
        {service.STATUS: "Finalizado"}, {service.FUNNEL: "Solicitado"},
        {service.PROJECT: "99"}, {service.ASSIGNEES: ["99"]},
        {service.PLANNED: "2026-02-30"},
        {service.DESCRIPTION: '<img src="x" onerror="alert(1)">'},
        {service.COMMENTS: '<a href="javascript:alert(1)">x</a>'},
    ):
        with pytest.raises(ValueError):
            service.changes(current, submitted, options)
    html = '<table><tr><td><a href="/a">x</a><img src="/b"></td></tr></table>'
    assert service.changes(current, {service.DESCRIPTION: html}, options) == {service.DESCRIPTION: html}


def test_transport_sends_exact_etag_and_partial_payload(monkeypatch):
    import io
    from core import microsoft365

    def open_request(request, *, timeout):
        assert request.get_method() == "PATCH"
        assert request.get_header("If-match") == '"7"'
        assert request.data == b'{"Prioridad": "Alta"}'
        return io.BytesIO(b'{"Prioridad":"Alta"}')

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", open_request)
    assert microsoft365.graph_patch_json("https://graph.microsoft.com/v1.0/test", "fake", {"Prioridad": "Alta"}, etag='"7"') == {"Prioridad": "Alta"}


def test_save_conflict_and_protected_fields_never_patch(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test-actor"})
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"8"', "fields": {}})
    def unexpected_write(*args):
        raise AssertionError("unexpected PATCH")
    monkeypatch.setattr(repository, "update_item", unexpected_write)
    client = TestClient(app)
    path = "/operaciones/solicitudes/1/guardar"
    origin = {"Origin": "http://testserver"}
    data = {"graph_token": "fake", "etag": '"7"', "Prioridad": "Alta"}
    assert client.post(path, data=data).status_code == 403
    assert client.post(path, data=dict(data, **{service.FUNNEL: "Solicitado"}), headers=origin).status_code == 422
    assert "cambió" in client.post(path, data=data, headers=origin).text


def test_save_audits_and_reconciles_timeout(monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test-actor"})
    observed = iter([{"eTag": '"7"', "fields": {}}, {"eTag": '"8"', "fields": {"Prioridad": "Alta"}}])
    monkeypatch.setattr(repository, "read_item", lambda *args: next(observed))
    monkeypatch.setattr(repository, "lookup_options", lambda token: {})
    def write(token, item, payload, etag):
        assert (item, payload, etag) == ("1", {"Prioridad": "Alta"}, '"7"')
        raise TimeoutError()
    monkeypatch.setattr(repository, "update_item", write)
    with caplog.at_level("INFO", logger="danwor.solicitudes.audit"):
        result = TestClient(app).post("/operaciones/solicitudes/1/guardar",
            headers={"Origin": "http://testserver"},
            data={"graph_token": "fake-secret", "etag": '"7"', "Prioridad": "Alta"})
    assert "data-solicitudes-saved" in result.text
    assert "fields=Prioridad outcome=confirmed" in caplog.text
    assert "fake-secret" not in caplog.text


def test_detail_form_renders_multiple_lookup_and_escapes_html(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test-actor"})
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {
        service.ASSIGNEES + "LookupId": ["9"], service.DESCRIPTION: "<b>Test</b>",
    }})
    monkeypatch.setattr(repository, "lookup_options", lambda token: {service.PROJECT: {}, service.ASSIGNEES: {"9": "Test"}})
    result = TestClient(app).post("/operaciones/solicitudes/1/detalle", data={"graph_token": "fake"})
    assert result.status_code == 200
    assert f'name="{service.ASSIGNEES}" multiple' in result.text
    assert "&lt;b&gt;Test&lt;/b&gt;" in result.text
    assert "<option>Pendiente</option>" in result.text


def test_htmx_feedback_only_replaces_status_for_validation_and_412(monkeypatch):
    from urllib.error import HTTPError
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test-actor"})
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {}})
    monkeypatch.setattr(repository, "lookup_options", lambda *args: {})
    def conflict(*args):
        raise HTTPError("https://graph.microsoft.com", 412, "Conflict", {}, None)
    monkeypatch.setattr(repository, "update_item", conflict)
    client = TestClient(app)
    headers = {"Origin": "http://testserver", "HX-Request": "true", "HX-Target": "solicitudes-save-result"}
    path = "/operaciones/solicitudes/1/guardar"
    data = {"graph_token": "fake", "etag": '"7"'}
    invalid = client.post(path, data=dict(data, **{service.STATUS: "Finalizado"}), headers=headers)
    assert invalid.status_code == 200
    assert "transición de estado no está permitida" in invalid.text
    assert "<form" not in invalid.text and "data-solicitudes-review" not in invalid.text
    remote = client.post(path, data=dict(data, Prioridad="Alta"), headers=headers)
    assert remote.status_code == 200
    assert "data-solicitudes-review" in remote.text and 'target="_blank"' in remote.text
    assert "<form" not in remote.text and 'name="etag"' not in remote.text


def test_rich_html_round_trip_does_not_rewrite_unchanged_styles():
    original = '<div style="color:#123456"><table><tr><td><a href="/a">Enlace</a><img src="/b"></td></tr></table></div>'
    fields = {service.DESCRIPTION: original}
    current = service.form_values(fields)
    patch = service.changes(current, {service.DESCRIPTION: original, "Prioridad": "Alta"}, {})
    assert patch == {"Prioridad": "Alta"}
    assert service.form_values(fields | patch)[service.DESCRIPTION] == original
