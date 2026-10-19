"""test_cchh_sharepoint_diagnostics.py — instrumentación SEGURA + sonda de LECTURA del upload de CVs a SharePoint.

Contrato: el uploader NO cambia de comportamiento (mismos errores/clasificación/mensajes); sólo deja de PERDER el cuerpo real de Graph (code/message/innerError/request-id) en un log estructurado.
Nunca se registra ni se devuelve el access token. La sonda es sólo GET y sólo APP_OWNER.
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


def _http_error(code: int, body: dict | None = None, *, url="https://graph.microsoft.com/v1.0/sites/s/drives/d/items/r:/x?token=LEAK&a=b", headers=None):
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    raw = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError(url, code, "Err", hdrs, io.BytesIO(raw))


def _graph_body(code="accessDenied", message="Access denied", inner=None):
    return {"error": {"code": code, "message": message, "innerError": inner or {"request-id": "req-123", "client-request-id": "cli-456", "date": "2026-10-19T12:00:00"}}}


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "host.sharepoint.com,SITEGUID,WEBGUID")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "b!DRIVE")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", "ROOTITEM")
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "Files.SelectedOperations.Selected")
    monkeypatch.setattr(config, "AZURE_CLIENT_ID", "11111111-2222-3333-4444-555555555555")


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


def _fake_jwt(claims: dict) -> str:
    seg = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return f"{seg({'alg': 'none'})}.{seg(claims)}.signature-part"


def test_token_claims_summary_reports_effective_scopes_and_hides_identity_claims():
    token = _fake_jwt({"aud": "https://graph.microsoft.com", "scp": "User.Read Sites.Read.All Files.SelectedOperations.Selected", "appid": "app-x", "tid": "tenant", "exp": 1893456000,
                       "upn": "someone@danwor.com", "name": "Someone", "oid": "obj-id"})
    s = diag.token_claims_summary(token)
    assert s["mode"] == "delegated" and s["write_scope_in_token"] is True and "Sites.Read.All" in s["scp"]
    assert s["aud"] == "https://graph.microsoft.com" and s["appid"] == "app-x" and s["exp_utc"].startswith("2030-")
    assert not any(k in s for k in ("upn", "name", "oid"))
    assert diag.token_claims_summary(_fake_jwt({"scp": "User.Read"}))["write_scope_in_token"] is False
    assert diag.token_claims_summary("not-a-jwt") == {"decodable": False}


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
    """Responde por sufijo de URL; registra cada llamada. Sólo `graph_get_json` está parcheado: cualquier POST/PUT/DELETE a Graph usaría el transporte real y haría fallar el test."""

    def __init__(self, fail_permissions=False):
        self.urls: list[str] = []
        self.fail_permissions = fail_permissions

    def __call__(self, url, token, **kw):
        assert token == SECRET_TOKEN
        self.urls.append(url)
        path = url.split("?", 1)[0]
        if "/shares/" in path:
            return {"id": "CTRLITEM", "name": "CV-prueba", "webUrl": "https://danwor.sharepoint.com/sites/Ctrl/Shared%20Documents/CV-prueba", "folder": {},
                    "parentReference": {"siteId": "danwor.sharepoint.com,CTRLSITE,CTRLWEB", "driveId": "b!CTRLDRIVE"}}
        if path.endswith("/permissions"):
            if self.fail_permissions:
                raise _http_error(403, _graph_body("accessDenied", "need FullControl"))
            return {"value": [{"roles": ["write"], "grantedToV2": {"application": {"id": "11111111-2222-3333-4444-555555555555", "displayName": "Danwor Hub"}}},
                              {"roles": ["owner"], "grantedToV2": {"user": {"displayName": "Someone", "email": "someone@danwor.com"}}}]}
        if path.endswith(":/__dw_diagnostic_probe_does_not_exist__"):
            raise _http_error(404, _graph_body("itemNotFound", "not found"))
        return {"id": "X", "name": "Carpeta"}


def test_probe_runs_only_gets_with_the_uploader_url_shapes_for_current_and_control(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(microsoft365, "graph_get_json", fake)
    monkeypatch.setattr(diag, "token_claims_summary", lambda t: {"decodable": True})
    result = diag.run_probe(SECRET_TOKEN, control_sharing_url="https://danwor.sharepoint.com/:f:/s/Operaciones-IniciativasOperacionesDanworTransversal/IgABC?e=xyz")
    assert result["current"]["item_id"] == "ROOTITEM" and result["control"]["item_id"] == "CTRLITEM" and result["control"]["drive_id"] == "b!CTRLDRIVE"
    assert result["control"]["site_path"] == "Operaciones-IniciativasOperacionesDanworTransversal" and result["control"]["hostname"] == "danwor.sharepoint.com"
    current_urls = [u.split("?", 1)[0] for u in fake.urls if "ROOTITEM" in u or "SITEGUID" in u]
    assert "https://graph.microsoft.com/v1.0/sites/host.sharepoint.com,SITEGUID,WEBGUID/drives/b!DRIVE/items/ROOTITEM" in current_urls          # MISMA forma que `_drive_items_base`
    assert "https://graph.microsoft.com/v1.0/sites/host.sharepoint.com,SITEGUID,WEBGUID/drives/b!DRIVE/items/ROOTITEM:/__dw_diagnostic_probe_does_not_exist__" in current_urls
    lookup = [s for s in result["current"]["steps"] if "nonexistent" in s["step"]][0]
    assert lookup["status"] == 404 and lookup["code"] == "itemNotFound" and lookup["ok"] is False
    assert result["current"]["grants"]["hub_app_has_grant"] is True and result["current"]["grants"]["other_grants_count"] == 1
    assert "someone@danwor.com" not in json.dumps(result)                                     # no se vuelcan correos de usuarios
    assert SECRET_TOKEN not in json.dumps(result) and "access_token" not in json.dumps(result)
    assert "?" not in "".join(s["endpoint"] for s in result["current"]["steps"] + result["control"]["steps"])   # endpoints sanitizados


def test_probe_reports_the_exact_failing_level_and_missing_grant_visibility(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_json", _FakeGraph(fail_permissions=True))
    monkeypatch.setattr(diag, "token_claims_summary", lambda t: {"decodable": True})
    res = diag.run_probe(SECRET_TOKEN)
    perm = [s for s in res["current"]["steps"] if s["step"].endswith("item_permissions")][0]
    assert perm["status"] == 403 and perm["code"] == "accessDenied" and res["current"]["grants"] is None and res["control"] is None


def test_probe_rejects_a_control_link_outside_sharepoint_without_calling_graph(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(microsoft365, "graph_get_json", fake)
    monkeypatch.setattr(diag, "token_claims_summary", lambda t: {"decodable": True})
    res = diag.run_probe(SECRET_TOKEN, control_sharing_url="http://evil.example.com/x")
    assert "sharepoint.com" in res["control"]["error"] and not any("/shares/" in u for u in fake.urls)


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
    r = owner.post("/cchh/diagnostico/sharepoint/sonda", data={"graph_token": SECRET_TOKEN, "control_url": ""})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert SECRET_TOKEN not in r.text and r.json()["ok"] is True and r.json()["control"] is None
