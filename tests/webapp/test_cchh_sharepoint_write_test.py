"""test_cchh_sharepoint_write_test.py — prueba de escritura CONTROLADA a la carpeta de CONTROL (diagnostico del upload de CVs a SharePoint).

Contrato: sólo APP_OWNER y sólo localhost, con frase de confirmación exacta; UN archivo de texto inocuo de nombre ÚNICO (`cchh-sharepoint-upload-test-<UTC>-<UUID>.txt`) con el MISMO builder
de URL y el MISMO transporte (`graph_put_bytes`) que el uploader; exactamente UN PUT (sin reintentos); `@microsoft.graph.conflictBehavior=fail` como protección ADICIONAL; rechazo si el destino
coincide con CURRENT (site id, drive id o item id); sin carpetas, sin ProfileDocument, sin tocar la config; nunca devuelve ni registra el token. NINGÚN test hace un PUT real: Graph se simula."""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365
from webapp import access
from webapp.main import app
from webapp.services import auth_service, profile_document_repository, profile_document_service, sharepoint_graph_diagnostics as diag
from tests.webapp.test_cchh_sharepoint_diagnostics import (  # noqa: F401  (_cfg: fixture autouse con la config de SharePoint de prueba)
    APP_ID, HC_USER, OWNER, SECRET_TOKEN, _cfg, _graph_body, _http_error, _tok,
)

CONTROL_URL = "https://danwor.sharepoint.com/:f:/s/Operaciones-IniciativasOperacionesDanworTransversal/IgABC?e=xyz"
CTRL = {"site": "danwor.sharepoint.com,CTRLSITE,CTRLWEB", "drive": "b!CTRLDRIVE", "item": "CTRLITEM"}
DEFAULT_ALLOWED = dict(diag.WRITE_TEST_ALLOWED)                 # los valores REALES (antes de que el fixture los reemplace por los de prueba)
NAME_RE = re.compile(r"^cchh-sharepoint-upload-test-\d{8}T\d{6}Z-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.txt$")


def _shares(site=CTRL["site"], drive=CTRL["drive"], item=CTRL["item"], folder=True, fail=None):
    def fake(url, token, **kw):
        assert "/shares/" in url, url                                   # la escritura SOLO lee para resolver el sharing link
        if fail is not None:
            raise fail
        body = {"id": item, "name": "Hub", "webUrl": "https://danwor.sharepoint.com/sites/Ctrl/Shared%20Documents/Hub", "parentReference": {"siteId": site, "driveId": drive}}
        if folder:
            body["folder"] = {}
        return body
    return fake


def _name_of(url: str) -> str:
    return url.split("/items/")[1].split(":/")[1].split(":/content")[0]


class _Put:
    """Doble de `graph_put_bytes`: registra cada llamada; responde ok o levanta el error pedido."""

    def __init__(self, error=None):
        self.calls: list[dict] = []
        self.error = error
        self.response_override: dict = {}

    def __call__(self, url, token, data, *, content_type="application/octet-stream", timeout=30):
        self.calls.append({"url": url, "token": token, "data": data, "content_type": content_type})
        if self.error is not None:
            raise self.error
        item = {"id": "NEWITEM", "name": _name_of(url), "size": len(data), "webUrl": "https://danwor.sharepoint.com/x", "parentReference": {"driveId": CTRL["drive"], "id": CTRL["item"]}}
        item.update(self.response_override)
        return item


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(diag, "WRITE_TEST_ALLOWED", {"app_id": APP_ID, "drive_id": CTRL["drive"], "item_id": CTRL["item"]})      # destino/app de prueba (los reales están en DEFAULT_ALLOWED)
    put = _Put()
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares())
    monkeypatch.setattr(microsoft365, "graph_put_bytes", put)
    monkeypatch.setattr(microsoft365, "graph_post_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no se crean carpetas ni se hace POST")))
    monkeypatch.setattr(microsoft365, "graph_delete", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no se borra nada")))
    for fn in ("create_with_explicit_id", "delete"):
        monkeypatch.setattr(profile_document_repository, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError("no se tocan ProfileDocument ni registros de negocio")))
    return put


# ── éxito: URL, contenido, nombre único, un solo PUT ─────────────────────────────────────────────────────────
def test_success_issues_exactly_one_put_with_the_uploader_url_builder_a_fixed_innocuous_body_and_a_unique_name(world):
    now, uid = datetime(2026, 10, 20, 1, 2, 3, tzinfo=timezone.utc), uuid.UUID("12345678-1234-5678-1234-567812345678")
    r = diag.write_test(_tok(), CONTROL_URL, now=now, uid=uid)
    assert r["ok"] is True and r["stage"] == "put" and r["attempts"] == 1 and len(world.calls) == 1
    name = "cchh-sharepoint-upload-test-20261020T010203Z-12345678-1234-5678-1234-567812345678.txt"
    assert r["filename"] == name and NAME_RE.match(name)
    call = world.calls[0]
    # MISMO builder que el uploader (`/sites/{site}/drives/{drive}/items/{folder}:/{nombre}:/content`) + protección adicional conflictBehavior=fail
    assert call["url"] == profile_document_service.upload_content_url(CTRL["item"], name, site_id=CTRL["site"], drive_id=CTRL["drive"]) + "?@microsoft.graph.conflictBehavior=fail"
    assert call["url"] == f"https://graph.microsoft.com/v1.0/sites/{CTRL['site']}/drives/{CTRL['drive']}/items/{CTRL['item']}:/{name}:/content?@microsoft.graph.conflictBehavior=fail"
    assert call["content_type"] == "text/plain" and call["data"] == diag.WRITE_TEST_CONTENT
    assert b"@" not in call["data"] and all(ch < 128 for ch in call["data"])                 # contenido fijo, ASCII, sin correos ni datos personales
    assert r["put"]["item"] == {"id": "NEWITEM", "name": name, "size": len(diag.WRITE_TEST_CONTENT), "web_url": "https://danwor.sharepoint.com/x"}
    assert r["control"] == {"site_id": CTRL["site"], "drive_id": CTRL["drive"], "item_id": CTRL["item"], "folder": "Hub"}
    assert "?" not in r["put"]["endpoint"] and SECRET_TOKEN not in json.dumps(r) and _tok() not in json.dumps(r)


def test_the_uploader_url_builder_is_unchanged_by_default_and_uses_the_config(monkeypatch):
    assert profile_document_service.upload_content_url("FOLDER", "f.pdf") == "https://graph.microsoft.com/v1.0/sites/host.sharepoint.com,SITEGUID,WEBGUID/drives/b!DRIVE/items/FOLDER:/f.pdf:/content"


# ── nombre único generado del lado del servidor, inmediatamente antes del PUT ──────────────────────────────
def test_the_name_is_generated_per_call_server_side_and_never_repeats(world):
    names = {diag.write_test(_tok(), CONTROL_URL)["filename"] for _ in range(3)}
    assert len(names) == 3 and all(NAME_RE.match(n) for n in names) and len(world.calls) == 3
    assert NAME_RE.match(diag.unique_test_filename()) and diag.unique_test_filename() != diag.unique_test_filename()
    assert "cchh-sharepoint-upload-test.txt" not in {_name_of(c["url"]) for c in world.calls}      # jamás el nombre fijo: no puede pisar un archivo existente


def test_the_name_is_generated_after_all_validations_so_a_rejection_never_mints_one(monkeypatch, world):
    monkeypatch.setattr(diag, "unique_test_filename", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe generarse")))
    r = diag.write_test(_tok(), "http://evil.example.com/x")
    assert r["stage"] == "control_invalid" and not world.calls


# ── errores de Graph: una sola llamada, sin reintentos, con code/message/request-id ─────────────────────────
@pytest.mark.parametrize("code,gcode,msg", [(401, "InvalidAuthenticationToken", "token expired"), (403, "accessDenied", "Access denied"), (404, "itemNotFound", "folder not found"),
                                            (409, "nameAlreadyExists", "name conflict")])
def test_graph_errors_are_reported_with_status_code_message_and_request_id_after_a_single_attempt(monkeypatch, world, caplog, code, gcode, msg):
    world.error = _http_error(code, _graph_body(gcode, msg, {"request-id": "req-1", "client-request-id": "cli-2"}), headers={"WWW-Authenticate": 'Bearer realm=""'})
    with caplog.at_level(logging.INFO, logger="danwor.cchh.sharepoint"):
        r = diag.write_test(_tok(), CONTROL_URL)
    assert r["ok"] is False and r["stage"] == "put" and r["attempts"] == 1 and len(world.calls) == 1          # SIN reintentos
    p = r["put"]
    assert (p["ok"], p["status"], p["code"], p["message"], p["request_id"], p["client_request_id"]) == (False, code, gcode, msg, "req-1", "cli-2")
    assert _tok() not in json.dumps(r) and _tok() not in caplog.text and "access_token" not in caplog.text
    assert json.loads(caplog.records[-1].getMessage())["step"] == "write_test.put"


@pytest.mark.parametrize("error", [_http_error(500, _graph_body("generalException", "boom")), urllib.error.URLError("timed out"), _http_error(503, None)])
def test_transient_failures_are_never_retried(world, error):
    world.error = error
    r = diag.write_test(_tok(), CONTROL_URL)
    assert r["ok"] is False and len(world.calls) == 1 and r["attempts"] == 1


# ── rechazo de CURRENT por IDENTIFICADORES, no por URL ───────────────────────────────────────────────────────
@pytest.mark.parametrize("ctrl,reason", [
    (dict(site="host.sharepoint.com,SITEGUID,WEBGUID", drive="b!OTHER", item="OTHERITEM"), ["site_id"]),                 # mismo sitio, otro drive e ítem
    (dict(site="SITEGUID", drive="b!OTHER", item="OTHERITEM"), ["site_id"]),                                              # id simple de colección = componente del id compuesto
    (dict(site="danwor.sharepoint.com,OTHERSITE,OTHERWEB", drive="b!DRIVE", item="OTHERITEM"), ["drive_id"]),            # misma biblioteca
    (dict(site="danwor.sharepoint.com,OTHERSITE,OTHERWEB", drive="b!OTHER", item="ROOTITEM"), ["item_id"]),              # misma carpeta raíz de CVs
    (dict(site="host.sharepoint.com,SITEGUID,WEBGUID", drive="b!DRIVE", item="ROOTITEM"), ["site_id", "drive_id", "item_id"]),
])
def test_a_control_that_matches_current_by_real_ids_is_rejected_without_any_put(monkeypatch, world, ctrl, reason):
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(**ctrl))
    r = diag.write_test(_tok(), CONTROL_URL)
    assert r["ok"] is False and r["stage"] == "control_is_current" and r["matches"] == reason and r["attempts"] == 0 and not world.calls


def test_the_current_check_compares_ids_never_urls_and_the_shared_hostname_does_not_count(monkeypatch, world):
    # mismo hostname (danwor.sharepoint.com) y misma ruta de sitio en el link, pero ids DISTINTOS: no es CURRENT
    r = diag.write_test(_tok(), "https://host.sharepoint.com/:f:/s/CCHH%20HUB/IgABC?e=1")
    assert r["ok"] is True and len(world.calls) == 1
    assert diag.control_matches_current({"site_id": "a.sharepoint.com,X,Y", "drive_id": "d1", "item_id": "i1"}, {"site_id": "b.sharepoint.com,P,Q", "drive_id": "d2", "item_id": "i2"}) == []


# ── control inválido ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", ["", "   ", "http://danwor.sharepoint.com/:f:/s/X/Ig", "https://evil.example.com/:f:/s/X/Ig", "ftp://danwor.sharepoint.com/x"])
def test_an_invalid_control_link_is_rejected_without_calling_graph(monkeypatch, world, url):
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe llamar a Graph")))
    r = diag.write_test(_tok(), url)
    assert r["ok"] is False and r["stage"] == "control_invalid" and not world.calls


def test_an_unresolvable_sharing_link_is_reported_without_any_put(monkeypatch, world):
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(fail=_http_error(403, _graph_body("accessDenied", "no access to share"))))
    r = diag.write_test(_tok(), CONTROL_URL)
    assert r["stage"] == "control_unresolved" and r["discovery"]["status"] == 403 and r["discovery"]["code"] == "accessDenied" and not world.calls


def test_a_control_that_is_not_a_folder_or_lacks_ids_is_rejected_without_any_put(monkeypatch, world):
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(folder=False))
    assert diag.write_test(_tok(), CONTROL_URL)["stage"] == "control_not_a_folder"
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(drive=None))
    assert diag.write_test(_tok(), CONTROL_URL)["stage"] == "control_unresolved"
    assert not world.calls


# ── ruta: APP_OWNER + localhost + confirmación + token ──────────────────────────────────────────────────────
def _client(email: str, monkeypatch, *, local: bool = True) -> TestClient:
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{OWNER},{HC_USER}")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    kw = {"base_url": "http://127.0.0.1:8505", "client": ("127.0.0.1", 50000)} if local else {}
    c = TestClient(app, follow_redirects=False, **kw)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def _post(c, **over):
    data = {"graph_token": _tok(), "control_url": CONTROL_URL, "confirm": diag.WRITE_TEST_PHRASE, **over}
    return c.post("/cchh/diagnostico/sharepoint/escritura", data=data)


def test_the_route_runs_the_write_for_the_owner_on_localhost_with_the_exact_confirmation_and_never_echoes_the_token(monkeypatch, world):
    r = _post(_client(OWNER, monkeypatch))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.json()["ok"] is True and len(world.calls) == 1
    assert _tok() not in r.text and SECRET_TOKEN not in r.text and NAME_RE.match(r.json()["filename"])


def test_the_route_requires_authentication_the_owner_and_localhost(monkeypatch, world):
    anon = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1:8505", client=("127.0.0.1", 1))
    assert anon.post("/cchh/diagnostico/sharepoint/escritura", data={"graph_token": "x"}).status_code in (302, 303, 307)
    assert _post(_client(HC_USER, monkeypatch)).status_code == 403                                  # usuario de Capital Humano que NO es owner
    assert _post(_client(OWNER, monkeypatch, local=False)).status_code == 403                        # host testserver / cliente no-loopback: ni owner puede fuera de localhost
    spoofed = _client(OWNER, monkeypatch, local=False)
    spoofed.headers["host"] = "127.0.0.1:8505"                                                      # Host falsificado pero cliente no-loopback: igual se rechaza
    assert _post(spoofed).status_code == 403
    assert not world.calls


@pytest.mark.parametrize("over,status", [({"confirm": ""}, 400), ({"confirm": "escribir archivo de prueba"}, 400), ({"confirm": "si"}, 400), ({"graph_token": ""}, 400)])
def test_the_route_refuses_without_the_exact_confirmation_or_a_token_and_never_puts(monkeypatch, world, over, status):
    r = _post(_client(OWNER, monkeypatch), **over)
    assert r.status_code == status and r.json()["ok"] is False and not world.calls


def test_the_route_rejects_current_and_reports_it_without_putting(monkeypatch, world):
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(site="host.sharepoint.com,SITEGUID,WEBGUID", drive="b!DRIVE", item="ROOTITEM"))
    r = _post(_client(OWNER, monkeypatch))
    assert r.status_code == 200 and r.json()["stage"] == "control_is_current" and not world.calls


def test_the_write_page_section_exists_for_the_owner_and_the_read_only_probe_is_untouched(monkeypatch, world):
    c = _client(OWNER, monkeypatch)
    page = c.get("/cchh/diagnostico/sharepoint").text
    assert diag.WRITE_TEST_PHRASE in page and "/cchh/diagnostico/sharepoint/escritura" in page and "/cchh/diagnostico/sharepoint/sonda" in page
    assert "graph_token:" not in page                                                                # sin credenciales en atributos HTML
    assert hasattr(diag, "run_probe") and hasattr(diag, "app_grant_for") and hasattr(diag, "decision_case")         # instrumentación read-only preservada


def test_the_write_never_touches_the_sharepoint_config_or_scopes(monkeypatch, world):
    before = (config.HC_SHAREPOINT_SITE_ID, config.HC_SHAREPOINT_DRIVE_ID, config.HC_CV_ROOT_ITEM_ID, config.HC_DOCUMENT_WRITE_SCOPE, config.AZURE_CLIENT_ID)
    diag.write_test(_tok(), CONTROL_URL)
    assert before == (config.HC_SHAREPOINT_SITE_ID, config.HC_SHAREPOINT_DRIVE_ID, config.HC_CV_ROOT_ITEM_ID, config.HC_DOCUMENT_WRITE_SCOPE, config.AZURE_CLIENT_ID)


# ── destino y aplicación FIJOS (CONTROL + LOCAL) y verificación de la respuesta ─────────────────────────────
PROD_DO = {"app": "eeca6df8-2436-422f-a1b8-05ff9e8ff653", "drive": "b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE", "item": "01ULA425ONX3YLUO3XTNDI46FMF43PFL4X",
           "site": "danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a"}
ADMIN_TOOL_APP = "14d82eec-204b-4c2f-b7e8-296a70dab67e"


def _tok_for(app_id: str) -> str:
    from tests.webapp.test_cchh_sharepoint_diagnostics import _fake_jwt
    return _fake_jwt({"aud": "https://graph.microsoft.com", "tid": "tenant-1", "appid": app_id, "scp": "User.Read Sites.Read.All Files.SelectedOperations.Selected"})


def test_the_real_allowed_target_is_the_control_hub_folder_and_the_local_app_and_never_production():
    assert DEFAULT_ALLOWED == {"app_id": "ad98b5cd-71d0-451f-8d70-e8581a4788fa", "drive_id": "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3", "item_id": "01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ"}
    assert PROD_DO["drive"] != DEFAULT_ALLOWED["drive_id"] and PROD_DO["item"] != DEFAULT_ALLOWED["item_id"] and PROD_DO["app"] != DEFAULT_ALLOWED["app_id"]


@pytest.mark.parametrize("ids", [
    dict(site=PROD_DO["site"], drive=PROD_DO["drive"], item=PROD_DO["item"]),                  # CCHH HUB de producción
    dict(site=PROD_DO["site"], drive=PROD_DO["drive"], item="01ULA425KSLK6LZAFFEVHKUSCO32CH64JG"),  # CCHH HUB/Busqueda y Seleccion/CVs de producción
    dict(site=CTRL["site"], drive=CTRL["drive"], item="OTHERFOLDER"),                           # otra carpeta (p. ej. una subcarpeta) del mismo drive de CONTROL
    dict(site=CTRL["site"], drive="b!OTHERDRIVE", item=CTRL["item"]),                           # mismo item id en otro drive
    dict(site="danwor.sharepoint.com,X,Y", drive="b!OTHERDRIVE", item="OTHERITEM"),             # cualquier otro sitio
])
def test_a_folder_other_than_the_allowed_control_hub_is_rejected_without_any_put(monkeypatch, world, ids):
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(**ids))
    r = diag.write_test(_tok(), CONTROL_URL)
    assert r["ok"] is False and r["stage"] == "control_not_allowlisted" and r["attempts"] == 0 and not world.calls


def test_the_route_also_rejects_a_foreign_folder_with_the_real_allowlist(monkeypatch, world):
    monkeypatch.setattr(diag, "WRITE_TEST_ALLOWED", dict(DEFAULT_ALLOWED, app_id=APP_ID))                 # allowlist REAL de destino; sólo la app de prueba
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares(site=PROD_DO["site"], drive=PROD_DO["drive"], item=PROD_DO["item"]))
    r = _post(_client(OWNER, monkeypatch))
    assert r.status_code == 200 and r.json()["stage"] == "control_not_allowlisted" and not world.calls


@pytest.mark.parametrize("app", [ADMIN_TOOL_APP, PROD_DO["app"], "99999999-0000-0000-0000-000000000000"])
def test_a_token_from_any_app_other_than_local_is_rejected_before_any_graph_call(monkeypatch, world, app):
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe llamar a Graph")))
    r = diag.write_test(_tok_for(app), CONTROL_URL)
    assert r["ok"] is False and r["stage"] == "token_app_not_allowed" and r["token_app"] == app and r["attempts"] == 0 and not world.calls


def test_an_undecodable_token_is_rejected_and_the_allowed_local_app_token_passes(monkeypatch, world):
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe llamar a Graph")))
    assert diag.write_test("not-a-jwt", CONTROL_URL)["stage"] == "token_app_not_allowed"
    monkeypatch.setattr(microsoft365, "graph_get_json", _shares())
    assert diag.write_test(_tok_for(APP_ID), CONTROL_URL)["ok"] is True and len(world.calls) == 1


def test_a_successful_put_is_verified_against_the_expected_file_and_folder(world):
    r = diag.write_test(_tok(), CONTROL_URL)
    assert r["ok"] is True and r["stage"] == "put" and r["verification"] == {"name_matches": True, "size_matches": True, "drive_matches": True, "parent_is_control_folder": True, "all_ok": True}


@pytest.mark.parametrize("override,failed", [({"name": "otro-archivo.txt"}, "name_matches"), ({"size": 1}, "size_matches"), ({"parentReference": {"driveId": "b!OTHER", "id": CTRL["item"]}}, "drive_matches"),
                                             ({"parentReference": {"driveId": CTRL["drive"], "id": "ELSEWHERE"}}, "parent_is_control_folder"), ({"parentReference": None}, "drive_matches")])
def test_a_put_response_that_does_not_match_is_not_reported_as_ok_and_keeps_the_filename_for_cleanup(world, override, failed):
    world.response_override = override
    r = diag.write_test(_tok(), CONTROL_URL)
    assert r["ok"] is False and r["stage"] == "put_unverified" and r["verification"][failed] is False and r["verification"]["all_ok"] is False
    assert NAME_RE.match(r["filename"]) and len(world.calls) == 1 and r["attempts"] == 1                 # un solo PUT, y el nombre queda a la vista para la limpieza
