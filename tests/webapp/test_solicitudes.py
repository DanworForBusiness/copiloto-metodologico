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
        f"{list_url}/items?$expand=fields($select=Proyecto,{service.DESCRIPTION},Prioridad,{service.FUNNEL},{service.STATUS},{service.ASSIGNEES},{service.CREATION_DATE})": {"value": [{"id": "1"}], "@odata.nextLink": next_link},
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
        service.ASSIGNEES + "LookupId": ["7"],
        service.PLANNED: "2026-11-03T00:00:00Z",
    }
    current = service.form_values(fields)
    submitted = dict(current, **{service.STATUS: "Pendiente", service.PLANNED: "", service.ASSIGNEES: ["12"]})
    assert service.changes(current, submitted, {service.ASSIGNEES: {"9": "A", "12": "B"}}) == {
        service.STATUS: "Pendiente", service.PLANNED: None,
        service.ASSIGNEES + "LookupId@odata.type": "Collection(Edm.String)",
        service.ASSIGNEES + "LookupId": ["12"],
    }


def test_invalid_transitions_and_manipulated_inputs():
    import pytest

    current = service.form_values({})
    options = {service.PROJECT: {}, service.ASSIGNEES: {}}
    for submitted in (
        {service.STATUS: "Finalizado"}, {service.FUNNEL: "Solicitado"},
        {service.PROJECT: "99"}, {service.ASSIGNEES: ["99"]},
        {service.PLANNED: "2026-02-30"},
    ):
        with pytest.raises(ValueError):
            service.changes(current, submitted, options)


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
    assert f'type="hidden" name="{service.ASSIGNEES}" :value="selected" value="9"' in result.text
    assert ">Test</textarea>" in result.text
    assert "<option>Pendiente</option>" in result.text
    assert result.text.count('id="sol-person"') == 1
    assert 'aria-label="Quitar asignación"' in result.text
    assert 'aria-controls="sol-person-list"' in result.text
    assert 'aria-controls="sol-project-list"' in result.text
    assert result.text.count('role="combobox"') == 2


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
    patch = service.changes(current, {service.DESCRIPTION: current[service.DESCRIPTION], "Prioridad": "Alta"}, {})
    assert patch == {"Prioridad": "Alta"}
    assert (fields | patch)[service.DESCRIPTION] == original


def test_list_separates_project_and_action_and_hides_single_page_pager(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test"})
    rows = [{"id": "1", "fields": {"Proyecto": "Proyecto de prueba"}},
            {"id": "2", "fields": {"Proyecto": None}}]
    monkeypatch.setattr(repository, "read_requests", lambda token: rows)
    client = TestClient(app)
    path = "/operaciones/solicitudes/data"
    result = client.post(path, data={"graph_token": "fake"}).text
    assert '<td class="left">Proyecto de prueba</td>' in result
    assert '<td class="left">—</td>' in result
    assert result.count('aria-label="Abrir solicitud ') == 2
    assert 'class="activity-pager"' not in result
    assert "Página 1 de 1" not in result
    assert "2 requerimientos" in result and "chart-tooltip-anchor" in result
    rows.extend({"id": str(i), "fields": {"Proyecto": "Proyecto de prueba"}} for i in range(3, 55))
    result = client.post(path, data={"graph_token": "fake", "q": "Proyecto", "page": "2"}).text
    assert 'aria-current="page">2 / 2' in result
    assert 'value="Proyecto"' in result
    assert result.count('aria-label="Abrir solicitud ') == 3


def test_detail_context_uses_lookup_label_and_keeps_empty_statuses():
    from webapp.routers.solicitudes import _detail_context

    context = _detail_context({"fields": {"ProyectoLookupId": "4", "Prioridad": "Alta"}}, {"Proyecto": {"4": "Proyecto de prueba"}})
    assert context["detail_project"] == "Proyecto de prueba"
    assert context["detail_badges"] == (("Status funnel", None), ("Status Arquitectura", None), ("Prioridad", "Alta"))
    assert _detail_context({"fields": {}}, {})["detail_project"] == ""


def test_individual_assignment_preserves_legacy_and_rejects_multiple_updates():
    import pytest

    key = service.ASSIGNEES
    options = {key: {"7": "A", "9": "B"}}
    empty = service.form_values({})
    assert service.changes(empty, {key: ["7"]}, options)[key + "LookupId"] == ["7"]
    single = service.form_values({key + "LookupId": ["7"]})
    assert service.changes(single, {key: ["7"]}, options) == {}
    assert service.changes(single, {key: ["9"]}, options)[key + "LookupId"] == ["9"]
    assert service.changes(single, {key: []}, options)[key + "LookupId"] == []
    with pytest.raises(ValueError):
        service.changes(single, {key: ["7", "9"]}, options)
    legacy = service.form_values({key + "LookupId": ["7", "9"]})
    assert service.changes(legacy, {"Prioridad": "Alta"}, options) == {"Prioridad": "Alta"}
    assert service.changes(legacy, {key: ["7", "9"]}, options) == {}
    for attempted in ([], ["7"], ["9", "7"]):
        with pytest.raises(ValueError):
            service.changes(legacy, {key: attempted}, options)


def test_project_lookup_patch_is_partial_and_validated():
    import pytest

    key = service.PROJECT
    current = service.form_values({key + "LookupId": "4"})
    options = {key: {"4": "Proyecto vigente", "8": "Proyecto nuevo"}}
    assert service.changes(current, {key: "4", "Prioridad": "Alta"}, options) == {"Prioridad": "Alta"}
    assert service.changes(current, {key: "8"}, options) == {key + "LookupId": "8"}
    assert service.changes(current, {key: ""}, options) == {key + "LookupId": None}
    with pytest.raises(ValueError):
        service.changes(current, {key: "99"}, options)


def test_narrative_text_handles_sharepoint_blocks_entities_and_table():
    original = ('<div class="ExternalClass" style="color:red"><span>A&#58; &amp; B&nbsp;C</span></div>'
                '<p>Primera<br>Segunda</p><a href="/a">Vínculo</a>'
                '<table><tr><th>Uno</th><td>Dos</td></tr><tr><td>Tres</td><td>Cuatro</td></tr></table>'
                '<script>alert(1)</script>')
    assert service.narrative_text(original) == "A: & B\u00a0C\nPrimera\nSegunda\nVínculo\nUno\tDos\nTres\tCuatro"
    assert service.narrative_text(None) == ""


def test_narrative_patch_escapes_only_changed_text_and_round_trips():
    fields = {service.DESCRIPTION: "<div>Viejo</div>", service.COMMENTS: "<p><b>Intacto</b></p>"}
    current = service.form_values(fields)
    text = "<img src=x> & literal &amp; >\n\nOtra línea"
    patch = service.changes(current, {service.DESCRIPTION: text, service.COMMENTS: "Intacto"}, {})
    assert patch == {service.DESCRIPTION: "<div>&lt;img src=x&gt; &amp; literal &amp;amp; &gt;<br><br>Otra línea</div>"}
    assert service.form_values(fields | patch)[service.DESCRIPTION] == text
    assert service.changes(current, {service.DESCRIPTION: ""}, {}) == {service.DESCRIPTION: None}


def test_save_narrative_confirms_plain_text_after_graph_reread(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test-actor"})
    fields = {service.DESCRIPTION: "<p>Anterior</p>", service.COMMENTS: "<p><b>Conservar</b></p>"}
    item = {"eTag": '"7"', "fields": fields}
    monkeypatch.setattr(repository, "read_item", lambda *args: item)
    monkeypatch.setattr(repository, "lookup_options", lambda *args: {})

    def write(token, item_id, payload, etag):
        assert etag == '"7"'
        assert payload == {service.DESCRIPTION: "<div>A &amp; &lt;B&gt;<br>Otra</div>"}
        fields.update(payload)
        item["eTag"] = '"8"'

    monkeypatch.setattr(repository, "update_item", write)
    result = TestClient(app).post("/operaciones/solicitudes/1/guardar",
        headers={"Origin": "http://testserver", "HX-Request": "true"},
        data={"graph_token": "fake", "etag": '"7"', service.DESCRIPTION: "A & <B>\r\nOtra",
              service.COMMENTS: "Conservar"})
    assert "data-solicitudes-saved" in result.text
    assert fields[service.COMMENTS] == "<p><b>Conservar</b></p>"
