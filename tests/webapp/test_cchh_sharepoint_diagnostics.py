"""test_cchh_sharepoint_diagnostics.py — instrumentación SEGURA + sonda de LECTURA del upload de CVs a SharePoint.

Contrato: el uploader NO cambia de comportamiento (mismos errores/clasificación/mensajes); sólo deja de PERDER el cuerpo real de Graph (code/message/innerError/request-id) en un log estructurado.
Nunca se registra ni se devuelve el access token ni claims de identidad. La sonda es sólo GET y sólo APP_OWNER. Evidencia que produce para los permisos «Selected»: scope presente en el token +
grant explícito de la APLICACIÓN sobre el driveItem (el acceso del usuario/admin al sitio NO es evidencia), y el caso A–E de la matriz de decisión.
"""
from __future__ import annotations

import base64
import email.message
import io
import json
import logging
import urllib.error

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365
from webapp import access
from webapp.main import app
from webapp.services import auth_service, profile_document_service, sharepoint_graph_diagnostics as diag

SECRET_TOKEN = "eyJhbGciOiJub25lIn0.SECRET-ACCESS-TOKEN-VALUE.sig"
OWNER = "owner-sp-diag@danwor.com"
HC_USER = "hc-sp-diag@danwor.com"
APP_ID = "11111111-2222-3333-4444-555555555555"
SCOPES_OK = "User.Read Sites.Read.All Files.SelectedOperations.Selected"
CONTROL_URL = "https://danwor.sharepoint.com/:f:/s/Operaciones-IniciativasOperacionesDanworTransversal/IgABC?e=xyz"


def _http_error(code: int, body: dict | None = None, *, url="https://graph.microsoft.com/v1.0/sites/s/drives/d/items/r:/x?token=LEAK&a=b", headers=None):
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    raw = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError(url, code, "Err", hdrs, io.BytesIO(raw))


def _graph_body(code="accessDenied", message="Access denied", inner=None):
    return {"error": {"code": code, "message": message, "innerError": inner or {"request-id": "req-123", "client-request-id": "cli-456", "date": "2026-10-19T12:00:00"}}}


def _fake_jwt(claims: dict) -> str:
    seg = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return f"{seg({'alg': 'none'})}.{seg(claims)}.signature-part"


def _tok(scp=SCOPES_OK) -> str:
    return _fake_jwt({"aud": "https://graph.microsoft.com", "tid": "tenant-1", "appid": APP_ID, "scp": scp, "upn": "someone@danwor.com"})


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "host.sharepoint.com,SITEGUID,WEBGUID")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "b!DRIVE")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", "ROOTITEM")
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "Files.SelectedOperations.Selected")
    monkeypatch.setattr(config, "AZURE_CLIENT_ID", APP_ID)


# ── helpers puros ────────────────────────────────────────────────────────────

def test_sanitize_endpoint_drops_query_and_fragment_but_keeps_resource_ids():
    url = "https://graph.microsoft.com/v1.0/sites/s/drives/d/items/r/children?$top=1&access_token=LEAK#frag"
    assert diag.sanitize_endpoint(url) == "https://graph.microsoft.com/v1.0/sites/s/drives/d/items/r/children"


def test_graph_error_detail_recovers_the_real_graph_body_that_the_uploader_used_to_discard():
    d = diag.graph_error_detail(_http_error(403, _graph_body(), headers={"WWW-Authenticate": 'Bearer error="insufficient_claims"'}))
    assert (d["status"], d["code"], d["message"], d["request_id"], d["client_request_id"]) == (403, "accessDenied", "Access denied", "req-123", "cli-456")
    assert "insufficient_claims" in d["www_authenticate"]
    assert diag.graph_error_detail(_http_error(500, None))["code"] is None                   # cuerpo vacío: no rompe
    assert diag.graph_error_detail(urllib.error.URLError("timed out"))["kind"] == "transport"


def test_log_graph_failure_emits_one_structured_line_without_query_string_or_token(caplog):
    with caplog.at_level(logging.INFO, logger="danwor.cchh.sharepoint"):
        diag.log_graph_failure("upload.put", "PUT", "https://graph.microsoft.com/v1.0/x?access_token=" + SECRET_TOKEN, _http_error(403, _graph_body()))
    assert len(caplog.records) == 1 and caplog.records[0].levelno == logging.WARNING
    rec = json.loads(caplog.records[0].getMessage())
    assert rec["step"] == "upload.put" and rec["method"] == "PUT" and rec["status"] == 403 and rec["code"] == "accessDenied"
    assert SECRET_TOKEN not in caplog.text and "LEAK" not in caplog.text and "access_token" not in caplog.text


def test_token_claims_summary_exposes_only_aud_tid_app_id_and_scopes_and_flags_the_two_relevant_scopes():
    token = _fake_jwt({"aud": "https://graph.microsoft.com", "tid": "tenant-1", "scp": SCOPES_OK, "appid": APP_ID, "exp": 1893456000,
                       "upn": "someone@danwor.com", "name": "Someone", "oid": "obj-id", "iss": "https://sts.windows.net/x/", "ver": "1.0"})
    s = diag.token_claims_summary(token)
    assert (s["aud"], s["tid"], s["client_application_id"]) == ("https://graph.microsoft.com", "tenant-1", APP_ID)
    assert s["files_selectedoperations_selected_present"] is True and s["sites_read_all_present"] is True and s["token_app_matches_configured_client_id"] is True
    assert sorted(s) == sorted(["decodable", "aud", "tid", "client_application_id", "scp", "files_selectedoperations_selected_present", "sites_read_all_present",
                                "configured_write_scope", "configured_write_scope_in_token", "token_app_matches_configured_client_id"])     # nada de usuario/oid/iss/exp
    assert "someone@danwor.com" not in json.dumps(s) and token not in json.dumps(s)
    v2 = diag.token_claims_summary(_fake_jwt({"scp": "User.Read", "azp": "app-from-azp"}))                  # tokens v2: azp en vez de appid
    assert v2["client_application_id"] == "app-from-azp" and v2["files_selectedoperations_selected_present"] is False and v2["sites_read_all_present"] is False
    assert diag.token_claims_summary("not-a-jwt") == {"decodable": False}


def test_app_grant_for_matches_only_the_token_application_id_and_distinguishes_unreadable_from_absent():
    perms = [{"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": "AAAA-1111", "displayName": "Danwor Hub"}}},
             {"id": "perm-2", "roles": ["read"], "grantedToIdentitiesV2": [{"application": {"id": "other-app", "displayName": "Otra app"}}]},
             {"id": "perm-3", "roles": ["owner"], "grantedToV2": {"user": {"displayName": "Someone", "email": "someone@danwor.com"}}}]
    g = diag.app_grant_for("aaaa-1111", perms)
    assert g["found"] is True and g["roles"] == ["write"] and g["permission_id"] == "perm-1" and g["has_write_role"] is True and g["application_id"] == "AAAA-1111"
    assert [o["application_id"] for o in g["other_application_grants"]] == ["other-app"]
    assert "someone@danwor.com" not in json.dumps(g) and "Someone" not in json.dumps(g)                      # usuarios/grupos: ni se devuelven ni se cuentan
    read_only = diag.app_grant_for("other-app", perms)
    assert read_only["found"] is True and read_only["has_write_role"] is False
    assert diag.app_grant_for("nobody", perms)["found"] is False
    assert diag.app_grant_for("aaaa-1111", None)["found"] is None                                           # permissions no legibles ≠ «sin grant»


# ── el uploader no cambia: mismas excepciones/mensajes, ahora con log ────────

def test_folder_lookup_403_keeps_the_permission_classification_and_now_logs_the_real_graph_reason(monkeypatch, caplog):
    def boom(url, token, **kw):
        raise _http_error(403, _graph_body("accessDenied", "The app lacks access to this resource"))
    monkeypatch.setattr(microsoft365, "graph_get_json", boom)
    with caplog.at_level(logging.INFO, logger="danwor.cchh.sharepoint"):
        with pytest.raises(profile_document_service.ProfileDocumentPermissionDeniedError) as ei:
            profile_document_service.ensure_profile_folder("PROFILE-1", SECRET_TOKEN)
    assert str(ei.value) == profile_document_service._PERMISSION_DENIED_MESSAGE
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["step"] == "ensure_folder.lookup" and rec["code"] == "accessDenied" and "lacks access" in rec["message"]
    assert SECRET_TOKEN not in caplog.text


def test_a_404_on_the_folder_lookup_is_the_normal_absent_case_logged_at_info_and_still_creates(monkeypatch, caplog):
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda url, token, **kw: (_ for _ in ()).throw(_http_error(404, _graph_body("itemNotFound", "not found"))))
    monkeypatch.setattr(microsoft365, "graph_post_json", lambda url, token, body, **kw: {"id": "NEWFOLDER"})
    with caplog.at_level(logging.INFO, logger="danwor.cchh.sharepoint"):
        assert profile_document_service.ensure_profile_folder("PROFILE-1", SECRET_TOKEN) == "NEWFOLDER"
    assert [r.levelno for r in caplog.records] == [logging.INFO]


def test_folder_create_non_403_error_keeps_graph_source_error_and_logs_the_cause(monkeypatch, caplog):
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda url, token, **kw: (_ for _ in ()).throw(_http_error(404, _graph_body("itemNotFound", "nf"))))
    monkeypatch.setattr(microsoft365, "graph_post_json", lambda url, token, body, **kw: (_ for _ in ()).throw(_http_error(400, _graph_body("invalidRequest", "bad parent"))))
    with caplog.at_level(logging.INFO, logger="danwor.cchh.sharepoint"):
        with pytest.raises(profile_document_service.ProfileDocumentGraphSourceError) as ei:
            profile_document_service.ensure_profile_folder("PROFILE-1", SECRET_TOKEN)
    assert str(ei.value) == profile_document_service._GRAPH_SOURCE_ERROR_MESSAGE
    steps = [json.loads(r.getMessage()) for r in caplog.records]
    assert steps[-1]["step"] == "ensure_folder.create" and steps[-1]["code"] == "invalidRequest" and steps[-1]["method"] == "POST"


def test_put_non_403_is_still_unconfirmed_and_retried_once_but_each_attempt_logs_the_real_reason(monkeypatch, caplog):
    calls = []

    def put(url, token, data, content_type=None, **kw):
        calls.append(url)
        raise _http_error(500, _graph_body("generalException", "boom"))
    monkeypatch.setattr(microsoft365, "graph_put_bytes", put)
    with caplog.at_level(logging.INFO, logger="danwor.cchh.sharepoint"):
        with pytest.raises(profile_document_service.ProfileDocumentRemoteUploadUnknownError):
            profile_document_service.upload_document_content("https://graph.microsoft.com/v1.0/x/content", SECRET_TOKEN, b"x", "text/plain")
    assert len(calls) == 2                                                                  # contrato de reintento único intacto
    assert [json.loads(r.getMessage())["step"] for r in caplog.records] == ["upload.put", "upload.put"]
    assert all(json.loads(r.getMessage())["code"] == "generalException" for r in caplog.records)


# ── sonda: sólo GET, mismas formas de URL que el uploader ───────────────────

class _FakeGraph:
    """Responde por URL; registra cada llamada. Sólo `graph_get_json` está parcheado: cualquier POST/PUT/DELETE a Graph usaría el transporte real y haría fallar el test."""

    def __init__(self, current_roles=("write",), control_roles=("write",), unreadable=()):
        self.urls: list[str] = []
        self.roles = {"ROOTITEM": current_roles, "CTRLITEM": control_roles}
        self.unreadable = set(unreadable)

    def __call__(self, url, token, **kw):
        assert token.count(".") == 2
        self.urls.append(url)
        path = url.split("?", 1)[0]
        if "/shares/" in path:
            return {"id": "CTRLITEM", "name": "CV-prueba", "webUrl": "https://danwor.sharepoint.com/sites/Ctrl/Shared%20Documents/CV-prueba", "folder": {},
                    "parentReference": {"siteId": "danwor.sharepoint.com,CTRLSITE,CTRLWEB", "driveId": "b!CTRLDRIVE"}}
        if path.endswith("/permissions"):
            item = path.rsplit("/items/", 1)[1].split("/", 1)[0]
            if item in self.unreadable:
                raise _http_error(403, _graph_body("accessDenied", "need FullControl"))
            perms = [{"id": "u-1", "roles": ["owner"], "grantedToV2": {"user": {"displayName": "Someone", "email": "someone@danwor.com"}}}]
            if self.roles.get(item):
                perms.append({"id": f"perm-{item}", "roles": list(self.roles[item]), "grantedToV2": {"application": {"id": APP_ID, "displayName": "Danwor Hub"}}})
            return {"value": perms}
        if path.endswith(":/__dw_diagnostic_probe_does_not_exist__"):
            raise _http_error(404, _graph_body("itemNotFound", "not found"))
        return {"id": "X", "name": "Carpeta"}


def test_probe_runs_only_gets_uses_the_uploader_url_shapes_and_the_requested_permissions_endpoint(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(microsoft365, "graph_get_json", fake)
    result = diag.run_probe(_tok(), control_sharing_url=CONTROL_URL)
    cur, ctl = result["current_target"], result["control_target"]
    assert cur["item_id"] == "ROOTITEM" and ctl["item_id"] == "CTRLITEM" and ctl["drive_id"] == "b!CTRLDRIVE" and ctl["site_id"] == "danwor.sharepoint.com,CTRLSITE,CTRLWEB"
    assert ctl["site_path"] == "Operaciones-IniciativasOperacionesDanworTransversal" and ctl["hostname"] == "danwor.sharepoint.com" and ctl["is_folder"] is True
    urls = [u.split("?", 1)[0] for u in fake.urls]
    assert "https://graph.microsoft.com/v1.0/sites/host.sharepoint.com,SITEGUID,WEBGUID/drives/b!DRIVE/items/ROOTITEM" in urls                  # MISMA forma que `_drive_items_base`
    assert "https://graph.microsoft.com/v1.0/sites/host.sharepoint.com,SITEGUID,WEBGUID/drives/b!DRIVE/items/ROOTITEM:/__dw_diagnostic_probe_does_not_exist__" in urls
    assert "https://graph.microsoft.com/v1.0/drives/b!DRIVE/items/ROOTITEM/permissions" in urls                                                    # GET /drives/{drive}/items/{item}/permissions
    assert "https://graph.microsoft.com/v1.0/drives/b!CTRLDRIVE/items/CTRLITEM/permissions" in urls
    lookup = [s for s in cur["steps"] if "nonexistent" in s["step"]][0]
    assert lookup["status"] == 404 and lookup["code"] == "itemNotFound" and lookup["ok"] is False
    assert cur["read_status"] == {"site": 200, "drive": 200, "folder_metadata": 200, "children_top1": 200}
    assert result["app_grants"]["current"]["permission_id"] == "perm-ROOTITEM" and result["app_grants"]["control"]["roles"] == ["write"]
    blob = json.dumps(result)
    assert "someone@danwor.com" not in blob and "Someone" not in blob and "access_token" not in blob and _tok() not in blob
    assert "?" not in "".join(s["endpoint"] for s in cur["steps"] + ctl["steps"])


@pytest.mark.parametrize("scp,cur_roles,ctl_roles,unreadable,control_url,case", [
    ("User.Read Sites.Read.All", ("write",), ("write",), (), CONTROL_URL, "C"),                         # sin scope Selected: primero consentimiento/token
    (SCOPES_OK, (), ("write",), (), CONTROL_URL, "A"),                                                  # scope sí, carpeta actual sin grant
    (SCOPES_OK, ("read",), ("write",), (), CONTROL_URL, "A"),                                           # grant read ≠ write
    (SCOPES_OK, ("write",), (), (), "", "B"),                                                           # grant write en la actual, control no evaluado: el problema está más abajo
    (SCOPES_OK, ("write",), (), (), CONTROL_URL, "D"),                                                  # actual sí, control no
    (SCOPES_OK, ("write",), ("write",), (), CONTROL_URL, "E"),                                          # ambos
    (SCOPES_OK, ("write",), ("write",), ("ROOTITEM",), CONTROL_URL, "?"),                               # permissions de la actual no legibles: no se afirma nada
])
def test_next_step_follows_the_owner_decision_matrix(monkeypatch, scp, cur_roles, ctl_roles, unreadable, control_url, case):
    monkeypatch.setattr(microsoft365, "graph_get_json", _FakeGraph(current_roles=cur_roles, control_roles=ctl_roles, unreadable=unreadable))
    assert diag.run_probe(_tok(scp), control_sharing_url=control_url)["next_step"]["case"] == case


def test_unreadable_permissions_are_reported_as_such_not_as_a_missing_grant(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_json", _FakeGraph(unreadable=("ROOTITEM",)))
    res = diag.run_probe(_tok())
    perm = [s for s in res["current_target"]["steps"] if s["step"].endswith("item_permissions")][0]
    assert perm["status"] == 403 and perm["code"] == "accessDenied"
    assert res["current_target"]["app_grant"]["found"] is None and res["control_target"] is None


def test_probe_rejects_a_control_link_outside_sharepoint_without_calling_graph(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(microsoft365, "graph_get_json", fake)
    res = diag.run_probe(_tok(), control_sharing_url="http://evil.example.com/x")
    assert "sharepoint.com" in res["control_target"]["error"] and not any("/shares/" in u for u in fake.urls)


def test_sharing_id_follows_the_documented_graph_encoding():
    url = "https://danwor.sharepoint.com/:f:/s/Site/IgAB?e=cP88d4"
    sid = diag.sharing_id(url)
    assert sid.startswith("u!") and "=" not in sid and "/" not in sid and "+" not in sid
    assert base64.urlsafe_b64decode(sid[2:] + "=" * (-len(sid[2:]) % 4)).decode() == url


# ── rutas: sólo APP_OWNER ────────────────────────────────────────────────────

def _client(email: str, monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{OWNER},{HC_USER}")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def test_diagnostic_routes_are_owner_only_and_hidden_from_the_nav(monkeypatch):
    anon = TestClient(app, follow_redirects=False)
    assert anon.get("/cchh/diagnostico/sharepoint").status_code in (302, 303, 307)
    assert anon.post("/cchh/diagnostico/sharepoint/sonda", data={"graph_token": "x"}).status_code in (302, 303, 307)
    regular = _client(HC_USER, monkeypatch)
    assert regular.get("/cchh/diagnostico/sharepoint").status_code == 403
    assert regular.post("/cchh/diagnostico/sharepoint/sonda", data={"graph_token": SECRET_TOKEN}).status_code == 403
    owner = _client(OWNER, monkeypatch)
    page = owner.get("/cchh/diagnostico/sharepoint")
    assert page.status_code == 200 and "Files.SelectedOperations.Selected" in page.text and "b!DRIVE" in page.text and "dwHumanCapitalDocumentWriteToken" in page.text
    assert "/cchh/diagnostico" not in owner.get("/cchh/candidatos").text                      # fuera del nav


def test_probe_route_requires_a_token_and_never_echoes_it_back(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_json", _FakeGraph())
    owner = _client(OWNER, monkeypatch)
    r = owner.post("/cchh/diagnostico/sharepoint/sonda", data={"graph_token": ""})
    assert r.status_code == 400 and r.headers["cache-control"] == "no-store"
    tok = _tok()
    r = owner.post("/cchh/diagnostico/sharepoint/sonda", data={"graph_token": tok, "control_url": ""})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert tok not in r.text and body["ok"] is True and body["control_target"] is None and body["token"]["client_application_id"] == APP_ID
    assert body["next_step"]["case"] == "B" and set(body) == {"ok", "token", "current_target", "control_target", "app_grants", "next_step"}
