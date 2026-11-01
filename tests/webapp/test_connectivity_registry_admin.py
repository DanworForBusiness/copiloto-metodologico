"""F2.2c.1 — «Tenants y ambientes» (Catálogo de conectividad, módulo 01): administración GOBERNADA del registro `connectivity-registry/v1`.

Vista central `GET /catalog/connectivity-registry` (lectura: dueño o consultor con perfil completo + acceso al cliente) y escritura owner-only
`POST /connectivity-registry/{action}` (sesión → mismo origen → APP_OWNER). Cubre altas, conflictos, idempotencia, revocaciones, vincular/
desvincular hosts, desactivación, escritura concurrente simulada, aislamiento entre clientes, permisos y navegación del dropdown.
Spaces FALSO en memoria: sin SAP, sin OAuth, sin red."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from core import remote_store
from tools.catalog.connectivity_governance import registry as reg
from webapp import access
from webapp.main import app, templates
from webapp.services import auth_service, catalog_reads
from webapp.services import connectivity_registry_store as store

ORIGIN = {"origin": "http://testserver"}
VIEW = "/catalog/connectivity-registry?pack=datasphere_core"


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeSpaces:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoSuchKey()
        return {"Body": SimpleNamespace(read=lambda k=Key: self.objects[k])}

    def get_paginator(self, _name):
        bucket = self

        class _P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in sorted(bucket.objects) if k.startswith(Prefix)]}]
        return _P()


@pytest.fixture()
def bucket(monkeypatch):
    b = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: b)
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "consultor@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "lector@danwor.com")
    monkeypatch.delenv("DANWOR_PUBLICATION_CONSULTANT_CLIENTS", raising=False)
    monkeypatch.setattr(catalog_reads, "_catalog_connection_clients", lambda: [{"cliente": "Biogenesis", "groups": []}, {"cliente": "Loma", "groups": []}])
    return b


def _client(email):
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": email, "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture()
def owner():
    return _client("owner@danwor.com")


_n = iter(range(10_000))


def _post(c, action, headers=ORIGIN, **fields):
    data = {"client": "Biogenesis", "tenant_id": "TNT-BIO-1", "rationale": "alta inicial", "request_id": f"req-{next(_n):06d}", **fields}
    return c.post(f"/connectivity-registry/{action}", data=data, headers=headers)


def _state():
    return store.load_state()


def _hx(c, url):
    r = c.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text
    return r.text


# ═══════════════ permisos ═══════════════
def test_write_requires_session_same_origin_and_owner(bucket, owner):
    assert _post(TestClient(app), "register-tenant").status_code == 401
    assert _post(_client("consultor@danwor.com"), "register-tenant").status_code == 403
    r = _post(owner, "register-tenant", headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["detail"] == "Origen no permitido"
    assert bucket.objects == {}
    assert _post(owner, "drop-everything").status_code == 404


def test_read_policy_full_profile_consultant_yes_readonly_and_anonymous_no(bucket):
    assert _client("consultor@danwor.com").get(VIEW, headers={"HX-Request": "true"}).status_code == 200
    assert _client("lector@danwor.com").get(VIEW, headers={"HX-Request": "true"}).status_code == 403
    r = TestClient(app).get(VIEW, headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)


def test_consultant_reads_without_forms_and_owner_sees_forms(bucket, owner):
    assert _post(owner, "register-tenant").json()["status"] == "APPLIED"
    mine = _hx(owner, VIEW + "&client=Biogenesis")
    theirs = _hx(_client("consultor@danwor.com"), VIEW + "&client=Biogenesis")
    assert "TNT-BIO-1" in theirs and "data-cr-form" not in theirs and "data-cr-form" in mine


def test_client_restriction_for_consultants_applies_to_reading(bucket, owner, monkeypatch):
    _post(owner, "register-tenant")
    _post(owner, "register-tenant", client="Loma", tenant_id="TNT-LOMA-1")
    monkeypatch.setenv("DANWOR_PUBLICATION_CONSULTANT_CLIENTS", "Loma")
    c = _client("consultor@danwor.com")
    assert c.get(VIEW + "&client=Biogenesis", headers={"HX-Request": "true"}).status_code == 403
    html = _hx(c, VIEW)
    assert '<option value="Loma"' in html and '<option value="Biogenesis"' not in html


# ═══════════════ flujo completo ═══════════════
def test_full_lifecycle_register_bind_assign_reassign_revoke_unbind_deactivate(bucket, owner):
    assert _post(owner, "register-tenant", display_name="Biogénesis DEV/QA").json()["status"] == "APPLIED"
    assert _post(owner, "bind-host", host="BIO-DEVQA.eu10.hcs.cloud.sap.").json()["kind"] == "HOST_BOUND"
    assert _post(owner, "assign-space", space="DESARROLLO", environment="DEV").json()["status"] == "APPLIED"
    st = _state()
    t = st["tenants"]["TNT-BIO-1"]
    assert t["hosts"][0]["host"] == "bio-devqa.eu10.hcs.cloud.sap"                  # normalizado por el contrato
    assign_id = t["spaces"]["DESARROLLO"]["event_id"]
    ev = next(e for e in store.load_events() if e["event_id"] == assign_id)
    assert ev["actor"] == "owner@danwor.com" and ev["event_id"].startswith("cr-")    # impuestos por el backend

    assert _post(owner, "assign-space", assignment=f"{assign_id}::DESARROLLO", environment="QAS").json()["status"] == "APPLIED"
    st = _state()
    assert reg.space_environments(st, "Biogenesis", "TNT-BIO-1") == {"DESARROLLO": "QAS"}
    # deactivation blocked while host/assignment remain
    r = _post(owner, "deactivate-tenant")
    assert r.status_code == 409 and r.json()["reason"] == "TENANT_HAS_ACTIVE_BINDINGS"
    t = st["tenants"]["TNT-BIO-1"]
    assert _post(owner, "revoke-assignment", target_event_id=t["spaces"]["DESARROLLO"]["event_id"]).json()["status"] == "APPLIED"
    assert _post(owner, "unbind-host", target_event_id=t["hosts"][0]["event_id"]).json()["kind"] == "HOST_UNBOUND"
    assert _post(owner, "deactivate-tenant", rationale="registro equivocado").json()["kind"] == "TENANT_DEACTIVATED"
    st = _state()
    t = st["tenants"]["TNT-BIO-1"]
    assert t["active"] is False and st["hosts"] == {} and st["rejected"] == []
    assert [h["kind"] for h in t["history"]] == ["TENANT_REGISTERED", "HOST_BOUND", "SPACE_ASSIGNED", "SPACE_ASSIGNED", "ASSIGNMENT_REVOKED",
                                                "HOST_UNBOUND", "TENANT_DEACTIVATED"]
    # desactivado: no admite cambios ni reutiliza el id
    assert _post(owner, "bind-host", host="x.hana.ondemand.com").json()["reason"] == "TENANT_DEACTIVATED"
    assert _post(owner, "register-tenant").json()["reason"] == "TENANT_ALREADY_REGISTERED"
    html = _hx(owner, VIEW + "&client=Biogenesis&tenant=TNT-BIO-1")
    assert "Desactivado" in html and "Tenant desactivado" in html and "Host desvinculado" in html


def test_conflicts_are_rejected_before_writing_with_business_messages(bucket, owner):
    _post(owner, "register-tenant")
    _post(owner, "register-tenant", client="Loma", tenant_id="TNT-LOMA-1")
    _post(owner, "bind-host", host="bio.eu10.hcs.cloud.sap")
    _post(owner, "assign-space", space="S1", environment="DEV")
    n = len(bucket.objects)
    r = _post(owner, "bind-host", client="Loma", tenant_id="TNT-LOMA-1", host="bio.eu10.hcs.cloud.sap")
    assert r.status_code == 409 and r.json()["reason"] == "HOST_BOUND_TO_ANOTHER_TENANT" and "TNT-BIO-1" not in r.json()["detail"]
    assert _post(owner, "assign-space", space="S1", environment="PRD").json()["reason"] == "SPACE_ALREADY_ASSIGNED"
    assert _post(owner, "register-tenant", client="Loma").json()["reason"] == "TENANT_BELONGS_TO_ANOTHER_CLIENT"
    assert _post(owner, "assign-space", space="S2", environment="PROD").status_code == 400          # vocabulario cerrado
    assert _post(owner, "bind-host", host="https://bio.eu10.hcs.cloud.sap:443/x").status_code == 400
    assert _post(owner, "register-tenant", tenant_id="TNT-2", rationale="  ").status_code == 400  # motivo obligatorio
    assert len(bucket.objects) == n


def test_same_request_id_is_idempotent_and_reused_with_other_content_conflicts(bucket, owner):
    first = _post(owner, "register-tenant", request_id="req-fixed-0001")
    again = _post(owner, "register-tenant", request_id="req-fixed-0001")
    assert first.json()["status"] == again.json()["status"] == "APPLIED" and again.json()["idempotent"] is True
    assert first.json()["event_id"] == again.json()["event_id"] and len(bucket.objects) == 1
    other = _post(owner, "register-tenant", request_id="req-fixed-0001", display_name="otro")
    assert other.status_code == 409 and len(bucket.objects) == 1


def test_concurrent_write_returns_explicit_status_and_is_not_applied(bucket, owner, monkeypatch):
    _post(owner, "register-tenant")
    from datetime import timedelta
    after_registration = reg._utc(_state()["tenants"]["TNT-BIO-1"]["registered_at"]) + timedelta(microseconds=1)
    rival = reg.assign_space(tenant_id="TNT-BIO-1", space="SANDBOX", environment="DEV", event_id="rival", client="Biogenesis",
                             actor="owner@danwor.com", decided_at=after_registration.isoformat(), rationale="otra pestaña", provenance=None)
    real_put = remote_store.put_governed_decision_object

    def racing_put(key, data):                       # la otra escritura entra entre el chequeo previo y el PUT
        real_put(store.event_key(rival), reg.canonical_json(rival).encode("utf-8"))
        monkeypatch.setattr(remote_store, "put_governed_decision_object", real_put)
        return real_put(key, data)
    monkeypatch.setattr(remote_store, "put_governed_decision_object", racing_put)
    r = _post(owner, "assign-space", space="SANDBOX", environment="PRD")
    body = r.json()
    assert r.status_code == 409 and body["status"] == "CONCURRENT_CONFLICT" and body["reason"] == "SPACE_ALREADY_ASSIGNED"
    assert reg.space_environments(_state(), "Biogenesis", "TNT-BIO-1") == {"SANDBOX": "DEV"}
    html = _hx(owner, VIEW + "&client=Biogenesis")
    assert "data-registry-rejected" in html and body["event_id"][:11] in html          # visible para el dueño, no silenciado


def test_view_isolates_clients_and_explains_it_does_not_homologate(bucket, owner):
    _post(owner, "register-tenant")
    _post(owner, "register-tenant", client="Loma", tenant_id="TNT-LOMA-1")
    html = _hx(owner, VIEW + "&client=Loma")
    assert "TNT-LOMA-1" in html and "TNT-BIO-1" not in html
    assert "Registrar un ambiente no homologa conexiones" in html and "data-registry-view" in html
    assert "logical_system" not in html and "homologation" not in html


def test_unknown_client_is_not_selected_and_unavailable_store_fails_closed(bucket, owner, monkeypatch):
    html = _hx(owner, VIEW + "&client=Inexistente")
    assert "Elegí un cliente" in html and "data-cr-form" not in html

    def boom(_prefix):
        raise remote_store.RemoteReadError("sin almacén")
    monkeypatch.setattr(remote_store, "list_governed_decision_keys", boom)
    html = _hx(owner, VIEW + "&client=Biogenesis")
    assert "Registro no disponible" in html and "data-cr-form" not in html
    r = _post(owner, "register-tenant")
    assert r.status_code == 503 and r.json()["status"] == "UNAVAILABLE"


def test_direct_navigation_renders_full_page(bucket, owner):
    r = owner.get(VIEW)
    assert r.status_code == 200 and "<title>" in r.text and "data-registry-view" in r.text and 'id="content"' in r.text


# ═══════════════ navegación (dropdown 1 del módulo 01) ═══════════════
def _render_tab(request):
    ctx = dict(document=SimpleNamespace(summary=SimpleNamespace(number="01", stem="01_Arquitectura_y_Conectividad")), pack="datasphere_core",
               request=request, catalog_models=[], catalog_domains=[], catalog_lobs=[], catalog_clients=[], catalog_data_products=[],
               catalog_connection_types=[], catalog_security_families=[], catalog_connection_clients=[])
    return templates.get_template("partials/catalog_tab.html").render(**ctx)


def _fake_request(email):
    from starlette.requests import Request
    token = auth_service.create_session({"email": email, "name": email, "first_name": "T", "family_name": "U"})
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"cookie", f"{auth_service.COOKIE_NAME}={token}".encode())],
             "query_string": b"", "server": ("testserver", 80), "scheme": "http", "app": app, "router": app.router}
    return Request(scope)


def test_dropdown_offers_tenants_and_environments_only_to_readers(bucket):
    html = _render_tab(_fake_request("owner@danwor.com"))
    sel = html[html.index('<select id="catalog-conn-kind"'):]
    sel = sel[:sel.index("</select>")]
    assert re.findall(r'<option value="([^"]+)">', sel) == ["connections", "clients", "sap_types", "registry"]
    assert 'data-catalog-kind="registry"' in html and 'hx-trigger="click, dw-registry-open"' in html
    assert "$refs.registryOpen" in html
    reader = _render_tab(_fake_request("lector@danwor.com"))
    assert 'value="registry"' not in reader and 'data-catalog-kind="registry"' not in reader
