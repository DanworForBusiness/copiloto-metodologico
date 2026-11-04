from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service


STATUS_CHOICES = ["Pendiente", "Planificado", "En curso", "Finalizado", "Suspendido"]


def test_every_catalog_state_can_replace_any_current_state():
    catalog = STATUS_CHOICES + ["Estado agregado en SharePoint"]
    for current in ["", *catalog]:
        for target in catalog:
            changes = service.changes(service.form_values({service.STATUS: current}),
                                      {service.STATUS: target}, {service.STATUS: catalog})
            assert changes == ({} if current == target else {service.STATUS: target})


def test_read_follows_graph_pages(monkeypatch):
    base = repository.GRAPH
    list_url = f"{base}/sites/example,site,web/lists/{repository.LIST_ID}"
    next_link = f"{list_url}/items?$skiptoken=next"
    responses = {
        f"{base}/sites/danwor.sharepoint.com:/sites/Gestion": {"id": "example,site,web"},
        f"{list_url}/items?$expand=fields($select=Title,Proyecto,ProyectoLookupId,{service.DESCRIPTION},Prioridad,{service.FUNNEL},{service.STATUS},{service.ASSIGNEES},{service.CREATION_DATE},{service.PLANNED},{service.ASSIGNEES}LookupId,{service.ACTUAL})": {"value": [{"id": "1"}], "@odata.nextLink": next_link},
        next_link: {"value": [{"id": "2"}]},
    }
    monkeypatch.setattr(repository, "graph_get_json", lambda url, token: responses[url])
    assert repository.read_requests("test-token") == [{"id": "1", "project_label": ""}, {"id": "2", "project_label": ""}]


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
    assert service.changes(current, submitted, {service.STATUS: STATUS_CHOICES, service.ASSIGNEES: {"9": "A", "12": "B"}}) == {
        service.STATUS: "Pendiente", service.PLANNED: None,
        service.ASSIGNEES + "LookupId@odata.type": "Collection(Edm.String)",
        service.ASSIGNEES + "LookupId": ["12"],
    }


def test_invalid_catalog_state_and_manipulated_inputs():
    import pytest

    current = service.form_values({})
    options = {service.PROJECT: {}, service.ASSIGNEES: {}, service.STATUS: STATUS_CHOICES}
    for submitted in (
        {service.STATUS: "Inexistente"}, {service.FUNNEL: "Solicitado"}, {"Title": "Protegido"},
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
    invalidated = []
    monkeypatch.setattr(solicitudes.solicitudes_snapshot, "invalidate", invalidated.append)
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
    assert invalidated == ["test-actor"]
    assert "fake-secret" not in caplog.text


def test_detail_form_renders_multiple_lookup_and_escapes_html(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test-actor"})
    monkeypatch.setattr(repository, "read_item", lambda *args: {"eTag": '"7"', "fields": {
        service.ASSIGNEES + "LookupId": ["9"], service.DESCRIPTION: "<b>Test</b>",
    }})
    monkeypatch.setattr(repository, "lookup_options", lambda token: {service.PROJECT: {}, service.ASSIGNEES: {"9": "Test"}, service.STATUS: STATUS_CHOICES})
    result = TestClient(app).post("/operaciones/solicitudes/1/detalle", data={"graph_token": "fake"})
    assert result.status_code == 200
    assert f'type="hidden" name="{service.ASSIGNEES}" :value="selected" value="9"' in result.text
    assert ">Test</textarea>" in result.text
    assert ">Cancelar</a>" not in result.text
    assert 'id="solicitudes-save-result" role="status"' in result.text
    assert '<option value="Pendiente">Pendiente</option>' in result.text
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
    monkeypatch.setattr(repository, "lookup_options", lambda *args: {service.STATUS: STATUS_CHOICES})
    def conflict(*args):
        raise HTTPError("https://graph.microsoft.com", 412, "Conflict", {}, None)
    monkeypatch.setattr(repository, "update_item", conflict)
    client = TestClient(app)
    headers = {"Origin": "http://testserver", "HX-Request": "true", "HX-Target": "solicitudes-save-result"}
    path = "/operaciones/solicitudes/1/guardar"
    data = {"graph_token": "fake", "etag": '"7"'}
    invalid = client.post(path, data=dict(data, **{service.STATUS: "Inexistente"}), headers=headers)
    assert invalid.status_code == 200
    assert "Status Arquitectura válido" in invalid.text
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
    assert '<td class="left" data-column-key="project">Proyecto de prueba</td>' in result
    assert '<td class="left" data-column-key="project">—</td>' in result
    assert result.count('aria-label="Abrir solicitud ') == 2
    assert 'class="activity-pager"' not in result
    assert "Página 1 de 1" not in result
    assert "2 solicitudes" in result and "chart-tooltip-anchor" in result
    rows.extend({"id": str(i), "fields": {"Proyecto": "Proyecto de prueba"}} for i in range(3, 55))
    result = client.post(path, data={"graph_token": "fake", "q": "Proyecto", "page": "2"}).text
    assert 'aria-current="page">2</span>' in result
    assert 'value="Proyecto"' in result
    assert result.count('aria-label="Abrir solicitud ') == 3


def test_detail_context_uses_title_and_keeps_empty_statuses():
    from webapp.routers.solicitudes import _detail_context

    context = _detail_context({"fields": {"Title": "Solicitud real", "ProyectoLookupId": "4", "Prioridad": "Alta"}})
    assert context["detail_title"] == "Solicitud real"
    assert context["detail_badges"] == (("Status funnel", None), ("Status Arquitectura", None), ("Prioridad", "Alta"))
    assert _detail_context({"fields": {"Proyecto": "Proyecto sin título"}})["detail_title"] == ""


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
              "new_observation": ""})
    assert "data-solicitudes-saved" in result.text
    assert fields[service.COMMENTS] == "<p><b>Conservar</b></p>"


def test_list_prioritizes_description_without_truncating_search(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test"})
    description = "Detalle extenso " * 30 + "término final"
    monkeypatch.setattr(repository, "read_requests", lambda token: [
        {"id": "1", "fields": {service.DESCRIPTION: "<p>" + description + "</p>"}},
    ])
    result = TestClient(app).post("/operaciones/solicitudes/data",
        data={"graph_token": "fake", "q": "término final"}).text
    headers = ("Solicitud", "Proyecto", "Creada", "Inicio", "Prioridad", "Funnel", "Arquitectura", "Colaborador", "Acciones")
    positions = [result.index(label, result.index("<thead>")) for label in headers]
    assert positions == sorted(positions)
    assert "Solicitud #1" in result and description not in result
    assert 'style="width:20%"' in result
    assert 'href="/operaciones/solicitudes/1/editar"' in result


def test_list_resolves_project_ids_once_without_overwriting_labels(monkeypatch):
    base = f"{repository.GRAPH}/sites/example/lists/{repository.LIST_ID}"
    monkeypatch.setattr(repository, "list_url", lambda token: base)
    monkeypatch.setattr(repository, "graph_get_json", lambda url, token: {"value": [
        {"id": "1", "fields": {"Title": "Solicitud A", "ProyectoLookupId": "4"}},
        {"id": "2", "fields": {"Title": "Solicitud B", "ProyectoLookupId": "4"}},
        {"id": "3", "fields": {"ProyectoLookupId": "5", "Proyecto": "Etiqueta directa"}},
    ]})
    calls = []
    def catalog(token, fields, *, base):
        calls.append(fields)
        return {service.PROJECT: {"4": "Proyecto de prueba"}}
    monkeypatch.setattr(repository, "lookup_options", catalog)
    assert [row["project_label"] for row in repository.read_requests("fake")] == [
        "Proyecto de prueba", "Proyecto de prueba", "Etiqueta directa",
    ]
    assert calls == [(service.PROJECT,)]


def test_global_search_matches_title_with_other_filters():
    rows = [
        {"id": "1", "fields": {"Title": "Automatización SAC", "Prioridad": "Alta"}},
        {"id": "2", "fields": {"Title": "Automatización SAC", "Prioridad": "Baja"}},
        {"id": "3", "fields": {"Title": None, "Prioridad": "Alta"}},
    ]
    assert service.select_rows(rows, "AUTOMATIZACIÓN", {"Prioridad": "Alta"}) == rows[:1]


def test_project_lookup_representations_keep_id_and_label_separate():
    catalog = {"4": "Nombre de catálogo"}
    cases = [
        ({"ProyectoLookupId": 4}, "4", "Nombre de catálogo"),
        ({"ProyectoLookupId": "4"}, "4", "Nombre de catálogo"),
        ({"Proyecto": 4}, "4", "Nombre de catálogo"),
        ({"Proyecto": "4"}, "4", "Nombre de catálogo"),
        ({"Proyecto": {"LookupId": 4, "LookupValue": "Nombre directo"}}, "4", "Nombre de catálogo"),
        ({"Proyecto": {"LookupId": "9", "LookupValue": "Nombre directo"}}, "9", "Nombre directo"),
        ({"Proyecto": {"LookupId": "9"}}, "9", ""),
        ({"Proyecto": "Nombre directo"}, "", "Nombre directo"),
        ({"Proyecto": "9"}, "9", ""),
        ({"Proyecto": {"LookupId": "9", "LookupValue": "9"}}, "9", ""),
        ({"ProyectoLookupId": "9", "Proyecto": None}, "9", ""),
        ({"Proyecto": None}, "", ""),
        ({"Proyecto": ""}, "", ""),
    ]
    for fields, lookup_id, label in cases:
        assert service.project_lookup_id(fields) == lookup_id
        assert service.form_values(fields)[service.PROJECT] == lookup_id
        assert service.project_label(fields, catalog) == label


def test_list_and_detail_share_catalog_label_without_overwriting_graph_fields(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    fields = {"Title": "Solicitud A", "ProyectoLookupId": 4, "Proyecto": 4}
    item = {"id": "1", "eTag": '"7"', "fields": fields}
    catalog = {service.PROJECT: {"4": "Proyecto de prueba del catalogo"}, service.ASSIGNEES: {}}
    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test"})
    monkeypatch.setattr(repository, "list_url", lambda token: f"{repository.GRAPH}/sites/test/lists/list")
    monkeypatch.setattr(repository, "graph_get_json", lambda url, token: {"value": [item]})
    monkeypatch.setattr(repository, "lookup_options", lambda *args, **kwargs: catalog)
    monkeypatch.setattr(repository, "read_item", lambda *args: item)
    client = TestClient(app)
    listed = client.post("/operaciones/solicitudes/data", data={"graph_token": "fake"}).text
    detail = client.post("/operaciones/solicitudes/1/detalle", data={"graph_token": "fake"}).text
    assert '<td class="left" data-column-key="project">Proyecto de prueba del catalogo</td>' in listed
    assert "Proyecto de prueba del catalogo" in detail
    assert 'name="Proyecto" :value="selected" value="4"' in detail
    assert fields["Proyecto"] == 4


def test_sort_projects_uses_visible_labels_and_keeps_blanks_last():
    rows = [{"id": "1", "project_label": "Zulu"}, {"id": "2", "project_label": ""},
            {"id": "3", "project_label": "Alfa"}, {"id": "4", "project_label": "Alfa"}]
    assert [r["id"] for r in service.sort_rows(rows, "project", "asc")] == ["3", "4", "1", "2"]
    assert [r["id"] for r in service.sort_rows(rows, "project", "desc")] == ["1", "3", "4", "2"]
    assert service.sort_rows(rows, "actions", "asc") == rows
    priorities = [{"fields": {"Prioridad": p}} for p in ("Alta", "Baja", "Media")]
    assert [r["fields"]["Prioridad"] for r in service.sort_rows(priorities, "priority", "asc")] == ["Baja", "Media", "Alta"]


def test_sort_filters_then_paginates_and_keeps_actions_fixed(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes

    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": "test"})
    rows = [{"id": str(i), "fields": {"Title": f"Título {i:02}", "Prioridad": "Alta"}} for i in range(1, 56)]
    monkeypatch.setattr(repository, "read_requests", lambda token: rows[::-1])
    result = TestClient(app).post("/operaciones/solicitudes/data", data={
        "graph_token": "fake", "sort": "title", "dir": "asc", "page": "2", "Prioridad": "Alta", "q": "título",
    }).text
    assert 'href="/operaciones/solicitudes/51/editar"' in result
    assert 'href="/operaciones/solicitudes/50/editar"' not in result
    assert 'aria-sort="ascending"' in result
    assert 'data-draggable-columns-key="operaciones-solicitudes-v3"' in result
    assert 'data-resizable-columns-key="operaciones-solicitudes-v3"' in result
    assert '<th class="center" scope="col">Acciones</th>' in result
    assert 'hx-params="none"' in result
    assert 'name="sort_vigentes" value="title"' in result


def test_snapshot_reuses_filters_sort_and_pages_and_refreshes(monkeypatch):
    import base64
    import json
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes
    from webapp.services import solicitudes_snapshot as snapshot

    actor = "snapshot-test-user"
    snapshot.invalidate(actor)
    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": actor})
    calls = []
    rows = [{"id": str(i), "fields": {"Title": f"Solicitud {i:03}"}} for i in range(1, 106)]

    def read(token):
        calls.append(token)
        return rows

    monkeypatch.setattr(repository, "read_requests", read)
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(snapshot.time.time()) + 900}).encode()).decode().rstrip("=")
    token = f"header.{claims}.signature"
    client = TestClient(app)
    path = "/operaciones/solicitudes/data"

    def post(**params):
        return client.post(path, data={"graph_token": token, **params}).text

    assert 'data-pager-pages="3"' in post()
    assert 'activity-pager__size' not in post(q="Solicitud", sort="title")
    assert '/solicitudes/101/editar' in post(sort="title", page="3")
    assert calls == [token]
    assert client.get("/operaciones/solicitudes/1/editar").status_code == 200
    listing = client.get("/operaciones/solicitudes").text
    assert listing.count("Cargando solicitudes…") == 1
    from bs4 import BeautifulSoup
    document = BeautifulSoup(listing, 'html.parser')
    # Preserve the shared hero / loading-slot / panel spacing contract.
    assert document.select_one('.solicitudes-loading-slot').find_next_sibling()['id'] == 'solicitudes-panel'
    assert document.select_one('#solicitudes-panel').find_next_sibling()['id'] == 'solicitudes-list-result'
    assert 'hx-trigger="load" hx-target="#solicitudes-panel" hx-indicator="#solicitudes-loading"' in listing
    post()
    assert calls == [token]
    post(refresh="1")
    assert calls == [token, token]
    snapshot.invalidate(actor)
    post()
    assert len(calls) == 3
    snapshot.invalidate(actor)


def test_snapshot_isolates_and_expires_and_does_not_serve_failed_refresh(monkeypatch):
    import pytest
    from urllib.error import HTTPError
    from webapp.services import solicitudes_snapshot as snapshot

    actors = ("cache-a", "cache-b")
    for actor in actors:
        snapshot.invalidate(actor)
    clock = [1000]
    monkeypatch.setattr(snapshot.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(snapshot, "_expiration", lambda token: snapshot.time.time() + 1000)
    calls = []

    def read(token):
        calls.append(token)
        return [{"id": str(len(calls))}]

    monkeypatch.setattr(repository, "read_requests", read)
    assert snapshot.read(actors[0], "token-a") == [{"id": "1"}]
    assert snapshot.read(actors[1], "token-a") == [{"id": "2"}]
    assert snapshot.read(actors[0], "token-b") == [{"id": "3"}]
    clock[0] += snapshot.TTL_SECONDS
    assert snapshot.read(actors[0], "token-a") == [{"id": "4"}]

    def denied(token):
        raise HTTPError("", 403, "Forbidden", {}, None)

    monkeypatch.setattr(repository, "read_requests", denied)
    with pytest.raises(HTTPError):
        snapshot.read(actors[0], "token-a", refresh=True)
    with pytest.raises(HTTPError):
        snapshot.read(actors[0], "token-a")
    for actor in actors:
        snapshot.invalidate(actor)


def test_table_dates_preserve_calendar_day_and_sort_chronologically():
    from webapp.services import solicitudes_service as svc

    for raw, expected in (
        ("2026-11-03T23:30:00-03:00", "03/11/2026"),
        ("2026-01-02T00:30:00+14:00", "02/01/2026"),
        ("2024-02-29", "29/02/2024"),
        (None, "—"), ("", "—"), ("2026-02-30", "—"),
    ):
        assert svc.display_date(raw) == expected

    for column, field in (("created", svc.CREATION_DATE), ("planned", svc.PLANNED)):
        rows = [
            {"id": "empty", "fields": {field: None}},
            {"id": "november", "fields": {field: "2026-11-03T00:00:00Z"}},
            {"id": "january", "fields": {field: "2026-01-20T00:00:00Z"}},
            {"id": "previous-year", "fields": {field: "2025-12-31T23:30:00-03:00"}},
        ]
        assert [row["id"] for row in svc.sort_rows(rows, column, "asc")] == [
            "previous-year", "january", "november", "empty",
        ]
        assert [row["id"] for row in svc.sort_rows(rows, column, "desc")] == [
            "november", "january", "previous-year", "empty",
        ]


def test_nine_column_table_dates_snapshot_sort_and_pagination(monkeypatch):
    import base64
    import json
    import re
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.routers import solicitudes
    from webapp.services import solicitudes_snapshot as snapshot

    actor = "table-dates-test"
    snapshot.invalidate(actor)
    monkeypatch.setattr(solicitudes, "_user", lambda request: {"sub": actor})
    calls = []
    rows = [{"id": str(i), "fields": {
        "Title": f"Solicitud {i}",
        service.CREATION_DATE: f"{2020 + i}-01-02T23:30:00-03:00",
        service.PLANNED: f"{2020 + i}-11-03T00:00:00Z",
        service.ACTUAL: "2001-01-01T00:00:00Z",
    }} for i in range(1, 56)]
    rows.append({"id": "empty", "fields": {}})

    def read(token):
        calls.append(token)
        return rows[::-1]

    monkeypatch.setattr(repository, "read_requests", read)
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(snapshot.time.time()) + 900}).encode()).decode().rstrip("=")
    token = f"header.{claims}.signature"
    client = TestClient(app)
    try:
        for column in ("created", "planned"):
            result = client.post("/operaciones/solicitudes/data", data={
                "graph_token": token, "sort": column, "dir": "asc", "page": "2",
            }, headers={"HX-Request": "true"})
            assert result.status_code == 200
            table = result.text.split('<table class="dashboard-table solicitudes-table">', 1)[1].split("</table>", 1)[0]
            headers = re.findall(r"<th\b[^>]*>(.*?)</th>", table, re.S)
            labels = [re.sub(r"<[^>]+>", "", value).strip().rstrip("▲").strip() for value in headers]
            assert labels == [
                "", "Solicitud", "Proyecto", "Creada", "Inicio", "Prioridad", "Funnel",
                "Arquitectura", "Colaborador", "Acciones",
            ]
            body = re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>", 1)[1], re.S)
            assert len(body) == 6
            assert "/51/editar" in body[0]
            assert "/empty/editar" in body[-1]
            assert 'data-column-key="created">02/01/2071</td>' in body[0]
            assert 'data-column-key="planned">03/11/2071</td>' in body[0]
            assert 'data-column-key="planned">—</td>' in body[-1]
            assert 'data-column-key="created">—</td>' in body[-1]
            assert 'data-layout-version="3"' in result.text
            assert table.count('draggable="true"') == 8
            assert '<th class="center" scope="col">Acciones</th>' in table
            assert 'data-resizable-columns-key="operaciones-solicitudes-v3"' in result.text
            assert 'data-draggable-columns-key="operaciones-solicitudes-v3"' in result.text
            assert 'hx-post="/operaciones/solicitudes/data"' in result.text
        assert len(calls) == 1
        assert service.form_values(rows[0]["fields"])[service.PLANNED] == "2021-11-03"
    finally:
        snapshot.invalidate(actor)
