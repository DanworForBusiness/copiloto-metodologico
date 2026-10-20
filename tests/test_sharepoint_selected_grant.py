"""test_sharepoint_selected_grant.py — herramienta administrativa `tools/sharepoint_selected_grant.py` (grant Selected `write`): apply controlado + rollback acotado.

Todo el transporte (Graph y login) está SIMULADO: ningún test habla con la red ni crea/borra un grant real. Contrato: --check/--dry-run sólo leen (nunca POST/DELETE ni archivos); la PRESENCIA de un grant equivalente es la única
evidencia autoritativa (la ausencia NO se puede probar → UNDETERMINED → el owner debe reconocerlo); --apply emite UN POST sin reintentos, sólo tras banderas explícitas, TTY/confirmación interactiva, identidad verificada y RELECTURA
de permisos; un corte después del POST es resultado DESCONOCIDO (código 4, sin reintento); --rollback borra UN permiso registrado, verificado y confirmado; el token nunca aparece en reportes, logs ni auditoría."""
from __future__ import annotations

import ast
import base64
import io
import json
import socket
import sys
import time
from pathlib import Path

import pytest

from tools import sharepoint_selected_grant as sg

SITE, DRIVE, ITEM = "a129a1d0-a4ee-4438-8de8-9e39e7007764", "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3", "01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ"
LOCAL, PROD, ADMIN_APP = sg.LOCAL_APP_ID, sg.PRODUCTION_APP_ID, "14d82eec-204b-4c2f-b7e8-296a70dab67e"
USER = "jose@danwor.com"
PREVIEW_URL = f"https://graph.microsoft.com/v1.0/drives/{DRIVE}/items/{ITEM}/permissions"
SECRET = "SECRET-ACCESS-TOKEN-VALUE"
OTHER_GUID = "11111111-2222-3333-4444-555555555555"
YES = lambda text: sg.CONFIRM_PHRASE  # noqa: E731
YES_ROLLBACK = lambda text: sg.ROLLBACK_PHRASE  # noqa: E731


class RealNetworkAttempt(BaseException):
    """BaseException a propósito: ningún `except Exception` de la herramienta puede tragarse un intento de red real durante los tests."""


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Red de seguridad de TODO este módulo: cualquier operación real (transporte por defecto, urlopen o sockets) falla el test. Los tests sólo usan transportes simulados."""
    def boom(*a, **k):
        raise RealNetworkAttempt("un test intentó una operación de red real")
    monkeypatch.setattr(sg, "_http_transport", boom)
    monkeypatch.setattr(sg.urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _jwt(**over) -> str:
    claims = {"tid": sg.TENANT_ID, "azp": ADMIN_APP, "aud": "https://graph.microsoft.com", "scp": "Sites.ReadWrite.All User.Read", "exp": time.time() + 3600, **over}
    claims = {k: v for k, v in claims.items() if v is not None}
    return f"{_b64({'alg': 'none'})}.{_b64(claims)}.{SECRET}"


def _argv(mode="--dry-run", **over):
    a = {"--tenant-id": sg.TENANT_ID, "--client-id": LOCAL, "--site-id": SITE, "--drive-id": DRIVE, "--item-id": ITEM, **over}
    out = [mode]
    for k, v in a.items():
        if v is not None:
            out += [k, v]
    return out


def _with(argv, opts):
    for k, v in opts.items():
        if v is None:
            continue
        argv += [k] if v is True else [k, v]
    return argv


def _apply_argv(tmp_path, **over):
    opts = {"--admin-client-id": ADMIN_APP, "--expect-user": USER, "--acknowledge-undetermined": True, "--acknowledge-inheritance-break": True,
            "--confirm-apply": sg.CONFIRM_PHRASE, "--audit-dir": str(tmp_path / "audit"), **over}
    return _with(_argv("--apply"), opts)


def _meta(**over):
    body = {"id": ITEM, "name": "Hub", "folder": {"childCount": 3}, "parentReference": {"driveId": DRIVE}, "sharepointIds": {"siteId": SITE, "listId": "LIST1", "listItemId": "7"}}
    body.update(over)
    return body


def _err(status, code, msg="boom", rid="req-1"):
    return (status, {"request-id": rid, "client-request-id": "cli-1"}, {"error": {"code": code, "message": msg}})


def _app_perm(app_id, roles, shape="grantedToV2", pid="p"):
    ident = {"application": {"id": app_id}}
    if shape == "grantedToV2":
        return {"id": pid, "roles": roles, "grantedToV2": ident}
    return {"id": pid, "roles": roles, "grantedToIdentitiesV2": [ident]}


class FakeGraph:
    """Transporte simulado de Graph. Cada respuesta puede ser (status, headers, dict), una LISTA (se consume en orden; la última se repite) o una excepción (se lanza). Registra todo, incluidos headers."""

    def __init__(self, drive_perms=None, item_perms=None, meta=None, post=None, me=None, perm_get=None, delete=None):
        self.requests: list[dict] = []
        self.meta = meta if meta is not None else (200, {}, _meta())
        self.drive_perms = drive_perms if drive_perms is not None else (200, {}, {"value": []})
        self.item_perms = item_perms if item_perms is not None else (200, {}, {"value": []})
        self.me = me if me is not None else (200, {}, {"id": "u-1", "userPrincipalName": USER, "displayName": "Jose"})
        self.post = post if post is not None else (201, {"request-id": "req-post"}, {"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL, "displayName": "Hub local"}}})
        self.perm_get = perm_get if perm_get is not None else (200, {}, {"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL}}})
        self.delete = delete if delete is not None else (204, {}, {})

    @staticmethod
    def _take(resp):
        if isinstance(resp, list):
            resp = resp.pop(0) if len(resp) > 1 else resp[0]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def __call__(self, method, url, headers, data):
        self.requests.append({"method": method, "url": url, "headers": headers, "data": data})
        root = "https://graph.microsoft.com/v1.0"
        if method == "POST":
            resp = self._take(self.post)
        elif method == "DELETE":
            resp = self._take(self.delete)
        elif url.startswith(root + "/me"):
            resp = self._take(self.me)
        elif "$select=id,name,folder" in url:
            resp = self._take(self.meta)
        elif url.startswith(root + "/drives/") and "/permissions/" in url:
            resp = self._take(self.perm_get)
        elif url.startswith(root + "/drives/") and url.endswith("/permissions"):
            resp = self._take(self.drive_perms)
        elif "/lists/" in url and "/items/" in url:
            resp = self._take(self.item_perms)
        else:
            resp = (404, {}, {"error": {"code": "unexpected", "message": url}})
        status, headers_, body = resp
        return status, headers_, json.dumps(body).encode()

    @property
    def methods(self):
        return [r["method"] for r in self.requests]


def _go(argv, graph, token=None, **kw):
    out, err = io.StringIO(), io.StringIO()
    env = {sg.TOKEN_ENV: token if token is not None else _jwt()}
    code = sg.main(argv, env=env, transport=graph, stdout=out, stderr=err, **kw)
    return code, json.loads(out.getvalue()), out.getvalue() + err.getvalue()


def _apply(graph, tmp_path, token=None, prompt=YES, **over):
    return _go(_apply_argv(tmp_path, **over), graph, token=token, prompt=prompt)


def _audit_files(tmp_path):
    d = tmp_path / "audit"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _result_file(tmp_path):
    return next(p for p in (tmp_path / "audit").iterdir() if p.name.endswith("-result.json") and "rollback" not in p.name)


# ── allowlist ────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_exact_control_local_combination_is_allowed_and_normalizes_guid_case():
    assert sg.resolve_grant(sg.TENANT_ID.upper(), LOCAL.upper(), SITE.upper(), DRIVE, ITEM)["name"] == "control-local"


@pytest.mark.parametrize("field,value", [("tenant_id", OTHER_GUID), ("client_id", OTHER_GUID), ("site_id", OTHER_GUID), ("drive_id", DRIVE[:-1] + "X"), ("item_id", ITEM[:-1] + "X")])
def test_any_identifier_outside_the_allowlist_is_rejected_without_network(field, value):
    ids = {"tenant_id": sg.TENANT_ID, "client_id": LOCAL, "site_id": SITE, "drive_id": DRIVE, "item_id": ITEM, field: value}
    with pytest.raises(sg.ToolError) as e:
        sg.resolve_grant(**ids)
    assert e.value.stage == "config_rejected" and field in e.value.detail["fields"]


def test_a_productive_destination_is_rejected_by_the_allowlist_in_every_mode(tmp_path):
    productive = {"--site-id": OTHER_GUID, "--drive-id": "b!PRODUCTIVEDRIVEID", "--item-id": "01PRODUCTIVEFOLDERID"}  # p. ej. `CCHH HUB` del sitio Comunicaciones
    for mode in ("--check", "--dry-run", "--apply", "--rollback"):
        graph = FakeGraph()
        argv = _with(_argv(mode, **productive), {"--audit-dir": str(tmp_path / "audit"), "--admin-client-id": ADMIN_APP, "--expect-user": USER, "--acknowledge-undetermined": True,
                                                  "--acknowledge-inheritance-break": True, "--confirm-apply": sg.CONFIRM_PHRASE, "--permission-id": "perm-1", "--audit-file": "x.json",
                                                  "--confirm-rollback": sg.ROLLBACK_PHRASE})
        code, report, _ = _go(argv, graph, prompt=YES)
        assert code == 2 and report["stage"] == "config_rejected" and graph.requests == [] and _audit_files(tmp_path) == []


def test_the_production_app_is_never_combinable_with_the_control_destination(tmp_path):
    with pytest.raises(sg.ToolError) as e:
        sg.resolve_grant(sg.TENANT_ID, PROD, SITE, DRIVE, ITEM)  # app de producción + carpeta de CONTROL
    assert e.value.stage == "config_rejected" and e.value.detail["fields"] == ["combination"]
    for argv in (_argv(**{"--client-id": PROD}), _apply_argv(tmp_path, **{"--client-id": PROD})):
        graph = FakeGraph()
        code, report, _ = _go(argv, graph, prompt=YES)
        assert code == 2 and report["stage"] == "config_rejected" and graph.requests == []
    assert [row["name"] for row in sg.ALLOWED_GRANTS if row["client_id"] == PROD] == ["production-do"]


@pytest.mark.parametrize("field,value", [("--tenant-id", "not-a-guid"), ("--site-id", "xyz"), ("--drive-id", "no-bang"), ("--item-id", "has space")])
def test_malformed_identifiers_are_a_config_error(field, value):
    graph = FakeGraph()
    code, report, _ = _go(_argv(**{field: value}), graph)
    assert code == 2 and report["stage"] == "config_invalid" and graph.requests == []


def test_every_identifier_is_required_and_the_token_is_never_a_cli_argument(capsys):
    with pytest.raises(SystemExit) as e:
        sg.main(["--dry-run", "--client-id", LOCAL])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        sg.main(_argv() + ["--token", SECRET])
    assert e.value.code == 2 and SECRET not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        sg.main(_argv("--dry-run") + ["--check"])  # modos mutuamente excluyentes
    with pytest.raises(SystemExit):
        sg.main(_argv("--apply") + ["--rollback"])


# ── autenticación ────────────────────────────────────────────────────────────────────────────────────────────
def test_no_credential_is_a_clear_auth_error_not_a_prompt():
    out = io.StringIO()
    code = sg.main(_argv(), env={}, transport=FakeGraph(), stdout=out, stderr=io.StringIO())
    report = json.loads(out.getvalue())
    assert code == 2 and report["stage"] == "auth_missing"


@pytest.mark.parametrize("over,problem", [({"tid": OTHER_GUID}, "tenant_mismatch"), ({"azp": LOCAL}, "token_is_a_hub_app"), ({"azp": PROD}, "token_is_a_hub_app"),
                                          ({"aud": "https://other.example"}, "audience_not_graph"), ({"idtyp": "app"}, "not_a_delegated_token"), ({"scp": None}, "not_a_delegated_token"),
                                          ({"exp": 1}, "token_expired_or_without_exp")])
def test_an_env_token_that_is_not_a_clean_delegated_admin_token_is_rejected(over, problem):
    graph = FakeGraph()
    code, report, text = _go(_argv(), graph, token=_jwt(**over))
    assert code == 2 and report["stage"] == "token_rejected" and problem in report["problems"] and graph.requests == [] and SECRET not in text


def test_an_env_token_from_another_admin_app_than_the_declared_one_is_rejected(tmp_path):
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path, token=_jwt(azp=OTHER_GUID))
    assert code == 2 and report["stage"] == "token_rejected" and "admin_client_mismatch" in report["problems"] and graph.requests == []


def test_an_opaque_token_is_rejected_because_tenant_and_client_cannot_be_verified():
    code, report, _ = _go(_argv(), FakeGraph(), token="opaque-token-without-jwt-shape")
    assert code == 2 and report["stage"] == "token_rejected" and "device code" in report["error"]


def test_a_token_without_a_sites_scope_is_rejected():
    code, report, _ = _go(_argv(), FakeGraph(), token=_jwt(scp="Files.SelectedOperations.Selected User.Read"))
    assert code == 2 and report["stage"] == "token_rejected" and "need_any" in report


def test_a_read_only_token_runs_check_but_flags_that_it_cannot_apply():
    code, report, _ = _go(_argv(), FakeGraph(), token=_jwt(scp="Sites.Read.All"))
    assert code == 0 and report["token"]["can_write"] is False and "token_scope_insufficient_for_apply" in report["blockers"]


class _Login:
    """Simula login.microsoftonline.com (device code). Un elemento de `polls` puede ser un str (código de error), un dict (cuerpo de error completo) o 'ok'."""

    def __init__(self, polls=("authorization_pending", "ok"), scope="Sites.Read.All User.Read", devicecode_status=200, devicecode_body=None):
        self.polls, self.scope, self.dc_status, self.dc_body, self.requests = list(polls), scope, devicecode_status, devicecode_body, []

    def __call__(self, method, url, headers, data):
        self.requests.append((method, url, data.decode()))
        if url.endswith("/devicecode"):
            if self.dc_status != 200:
                return self.dc_status, {}, json.dumps(self.dc_body or {"error": "unauthorized_client"}).encode()
            return 200, {}, json.dumps({"device_code": "DEV", "user_code": "ABCD1234", "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900, "interval": 5,
                                        "message": "Abrí https://microsoft.com/devicelogin e ingresá ABCD1234"}).encode()
        nxt = self.polls.pop(0)
        if nxt == "ok":
            return 200, {}, json.dumps({"access_token": SECRET, "scope": self.scope, "expires_in": 3599, "token_type": "Bearer"}).encode()
        return 400, {}, json.dumps(nxt if isinstance(nxt, dict) else {"error": nxt}).encode()


def _ticker():
    t = {"v": 0.0}

    def clock():
        return t["v"]

    def sleep(s):
        t["v"] += s
    return clock, sleep


def _go_device(argv, login, graph, **kw):
    clock, sleep = _ticker()
    out, err = io.StringIO(), io.StringIO()
    code = sg.main(argv, env={}, transport=graph, device_transport=login, sleep=sleep, clock=clock, stdout=out, stderr=err, **kw)
    return code, json.loads(out.getvalue()), out.getvalue() + err.getvalue()


def test_device_code_flow_uses_the_tenant_authority_a_public_client_and_never_prints_the_token():
    login, graph = _Login(), FakeGraph()
    code, report, text = _go_device(_argv(**{"--admin-client-id": ADMIN_APP}), login, graph)
    assert code == 0 and report["token"]["source"] == "device_code"
    assert login.requests[0][1] == f"https://login.microsoftonline.com/{sg.TENANT_ID}/oauth2/v2.0/devicecode"
    body = login.requests[0][2]
    assert "client_secret" not in "".join(r[2] for r in login.requests) and "scope=https%3A%2F%2Fgraph.microsoft.com%2FSites.Read.All" in body
    assert "Sites.ReadWrite.All" not in body and "User.Read" in body  # check/dry-run: lectura + identidad, nunca escritura
    assert "ABCD1234" in text and SECRET not in text
    assert graph.requests[0]["headers"]["Authorization"] == f"Bearer {SECRET}"  # el token sólo viaja en el header hacia Graph


def test_the_write_scope_is_requested_only_on_demand_or_for_apply_and_rollback(tmp_path):
    login = _Login(scope="Sites.ReadWrite.All User.Read")
    code, report, _ = _go_device(_argv("--dry-run", **{"--admin-client-id": ADMIN_APP, "--request-write-scope": None}) + ["--request-write-scope"], login, FakeGraph())
    assert code == 0 and "Sites.ReadWrite.All" in login.requests[0][2] and report["token"]["can_write"] is True
    login, graph = _Login(scope="Sites.ReadWrite.All User.Read"), FakeGraph()
    code, report, _ = _go_device(_apply_argv(tmp_path), login, graph, prompt=YES)
    assert "Sites.ReadWrite.All" in login.requests[0][2] and code == 0 and graph.methods.count("POST") == 1


@pytest.mark.parametrize("polls,error", [(["authorization_declined"], "authorization_declined"), (["expired_token"], "expired_token"), (["bad_verification_code"], "bad_verification_code")])
def test_device_code_failures_stop_the_tool_without_calling_graph(polls, error):
    login, graph = _Login(polls=polls), FakeGraph()
    code, report, _ = _go_device(_argv(**{"--admin-client-id": ADMIN_APP}), login, graph)
    assert code == 2 and report["stage"] == "auth_failed" and report["error"] == error and graph.requests == []


@pytest.mark.parametrize("body,hint,aadsts", [
    ({"error": "authorization_declined", "error_description": "AADSTS65004: The user declined to consent to access the app.\r\nTrace ID: t"}, "consent_rejected_by_user", ["AADSTS65004"]),
    ({"error": "consent_required", "error_description": "AADSTS65001: The user or administrator has not consented to use the application.\r\nTrace ID: t", "error_codes": [65001]}, "consent_not_granted", ["AADSTS65001"]),
    ({"error": "invalid_grant", "error_description": "AADSTS90094: Admin consent is required.\r\nTrace ID: t", "error_codes": [90094]}, "consent_not_granted", ["AADSTS90094"]),
    ({"error": "invalid_scope", "error_description": "AADSTS70011: The provided value for the input parameter 'scope' is not valid."}, "scope_not_requestable_by_this_client", ["AADSTS70011"]),
])
def test_a_rejected_oauth_consent_keeps_the_aadsts_code_and_a_hint_and_never_reaches_graph(tmp_path, body, hint, aadsts):
    for argv in (_argv(**{"--admin-client-id": ADMIN_APP}), _apply_argv(tmp_path)):
        login, graph = _Login(polls=[body]), FakeGraph()
        code, report, text = _go_device(argv, login, graph, prompt=YES)
        assert code == 2 and report["stage"] == "auth_failed" and report["error"] == body["error"] and report["hint"] == hint and report["aadsts"] == aadsts
        assert "Trace ID" not in report.get("error_description", "") and graph.requests == [] and _audit_files(tmp_path) == [] and SECRET not in text


def test_a_consent_error_at_the_devicecode_step_is_also_classified():
    login = _Login(devicecode_status=400, devicecode_body={"error": "invalid_request", "error_description": "AADSTS65001: not consented"})
    code, report, _ = _go_device(_argv(**{"--admin-client-id": ADMIN_APP}), login, FakeGraph())
    assert code == 2 and report["stage"] == "auth_failed" and report["aadsts"] == ["AADSTS65001"] and report["hint"] == "consent_not_granted"


def test_device_code_scopes_come_from_the_token_response_and_a_missing_sites_scope_is_rejected():
    login, graph = _Login(polls=["ok"], scope="User.Read"), FakeGraph()
    code, report, _ = _go_device(_argv(**{"--admin-client-id": ADMIN_APP}), login, graph)
    assert code == 2 and report["stage"] == "token_rejected" and graph.requests == []


def test_the_admin_client_cannot_be_a_hub_app():
    code, report, _ = _go(_argv(**{"--admin-client-id": LOCAL}), FakeGraph(), token="")
    assert code == 2 and report["stage"] == "token_rejected" and "Hub" in report["error"]


def test_device_code_times_out_with_expired_token_instead_of_polling_forever():
    login, graph = _Login(polls=["authorization_pending"] * 500), FakeGraph()
    code, report, _ = _go_device(_argv(**{"--admin-client-id": ADMIN_APP}), login, graph)
    assert code == 2 and report["error"] == "expired_token" and len(login.requests) < 300


# ── --check / --dry-run: sólo lectura, destino y evidencia ───────────────────────────────────────────────────
def test_check_only_reads_and_reports_the_target_and_an_undetermined_duplicate_state():
    graph = FakeGraph()
    code, report, text = _go(_argv("--check"), graph)
    assert code == 0 and report["ok"] is True and set(graph.methods) == {"GET"}
    assert report["target"] == {"name": "Hub", "site_id": SITE, "list_id": "LIST1", "list_item_id": "7"}
    assert report["duplicate_state"]["state"] == "UNDETERMINED" and report["would_apply"] is False
    assert [e["source"] for e in report["evidence"]] == ["driveItem.permissions", "listItem.permissions"] and all(e["complete"] for e in report["evidence"])
    assert SECRET not in text and "Authorization" not in text


@pytest.mark.parametrize("mode", ["--check", "--dry-run"])
def test_check_and_dry_run_never_write_neither_to_graph_nor_to_disk(mode, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("check/dry-run no deben escribir auditoría")
    monkeypatch.setattr(sg, "_write_audit", boom)
    graph = FakeGraph()
    code, report, _ = _go(_with(_argv(mode), {"--audit-dir": str(tmp_path / "audit"), "--acknowledge-undetermined": True, "--acknowledge-inheritance-break": True,
                                               "--confirm-apply": sg.CONFIRM_PHRASE, "--request-write-scope": True}), graph, prompt=lambda t: pytest.fail("no hay prompt en check/dry-run"))
    assert code == 0 and set(graph.methods) == {"GET"} and not (tmp_path / "audit").exists()
    assert not any(c[0] in ("POST", "DELETE", "PUT", "PATCH") for c in [(m,) for m in graph.methods])


def test_dry_run_prepares_the_exact_minimal_request_but_never_posts():
    graph = FakeGraph()
    code, report, _ = _go(_argv("--dry-run"), graph)
    assert code == 0 and "POST" not in graph.methods
    assert report["preview"] == {"method": "POST", "url": PREVIEW_URL, "body": {"grantedToV2": {"application": {"id": LOCAL}}, "roles": ["write"]}}
    assert set(report["preview"]["body"]) == {"grantedToV2", "roles"} and list(report["preview"]["body"]["grantedToV2"]) == ["application"]
    assert {"undetermined_not_acknowledged", "inheritance_break_not_acknowledged"} <= set(report["blockers"]) and report["would_apply"] is False
    assert any("confirmación interactiva" in p for p in report["pending_at_apply"]) and report["user"]["verified"] is True


def test_dry_run_with_the_acknowledgements_and_a_write_token_says_what_apply_would_do_without_doing_it():
    graph = FakeGraph()
    code, report, _ = _go(_with(_argv("--dry-run"), {"--acknowledge-undetermined": True, "--acknowledge-inheritance-break": True}), graph)
    assert code == 0 and report["blockers"] == [] and report["would_apply"] is True and report["duplicate_state"]["state"] == "UNDETERMINED"
    assert set(graph.methods) == {"GET"}


@pytest.mark.parametrize("meta,stage,fields", [
    ((200, {}, _meta(folder=None, file={"mimeType": "text/plain"})), "target_not_a_folder", None),
    ((200, {}, {k: v for k, v in _meta().items() if k != "folder"}), "target_not_a_folder", None),
    ((200, {}, _meta(sharepointIds={"siteId": OTHER_GUID})), "target_mismatch", ["site_id"]),
    ((200, {}, _meta(sharepointIds=None)), "target_mismatch", ["site_id"]),
    ((200, {}, _meta(parentReference={"driveId": "b!OTHER"})), "target_mismatch", ["drive_id"]),
    ((200, {}, _meta(id="OTHERITEM")), "target_mismatch", ["item_id"]),
    ((200, {}, _meta(name="CCHH HUB")), "target_mismatch", ["name"]),
])
def test_a_destination_that_is_not_the_allowlisted_folder_is_rejected_before_any_grant_is_prepared(meta, stage, fields):
    graph = FakeGraph(meta=meta)
    code, report, _ = _go(_argv("--dry-run"), graph)
    assert code == 2 and report["stage"] == stage and "preview" not in report and graph.methods == ["GET"]
    if fields:
        assert report["fields"] == fields


def test_a_destination_mismatch_blocks_apply_before_any_post(tmp_path):
    graph = FakeGraph(meta=(200, {}, _meta(name="CCHH HUB")))
    code, report, _ = _apply(graph, tmp_path)
    assert code == 2 and report["stage"] == "target_mismatch" and "POST" not in graph.methods and _audit_files(tmp_path) == []


@pytest.mark.parametrize("status,code_", [(401, "InvalidAuthenticationToken"), (403, "accessDenied"), (404, "itemNotFound"), (409, "conflict"), (500, "generalException")])
def test_graph_errors_on_the_target_are_reported_with_status_code_and_request_id_only(status, code_):
    graph = FakeGraph(meta=_err(status, code_, rid="req-xyz"))
    code, report, text = _go(_argv("--check"), graph)
    assert code == 1 and report["stage"] == "target_unreadable"
    assert report["graph"] == {"status": status, "code": code_, "message": "boom", "request_id": "req-xyz", "client_request_id": "cli-1"} and SECRET not in text
    assert len(graph.requests) == 1  # sin reintentos


# ── identidad del usuario ────────────────────────────────────────────────────────────────────────────────────
def test_the_authenticated_identity_is_reported_and_an_unexpected_user_is_rejected(tmp_path):
    _, report, _ = _go(_argv("--check"), FakeGraph())
    assert report["user"] == {"verified": True, "id": "u-1", "upn": USER, "display_name": "Jose", "matches_expected": False}
    graph = FakeGraph(me=(200, {}, {"id": "u-2", "userPrincipalName": "otra.persona@danwor.com"}))
    code, report, _ = _apply(graph, tmp_path)
    assert code == 2 and report["stage"] == "identity_mismatch" and report["expected"] == USER and "POST" not in graph.methods and _audit_files(tmp_path) == []
    code, report, _ = _go(_argv("--dry-run") + ["--expect-user", USER], FakeGraph(me=(200, {}, {"userPrincipalName": "x@y.com"})))
    assert code == 2 and report["stage"] == "identity_mismatch"


def test_an_unverifiable_identity_blocks_apply_but_not_check(tmp_path):
    graph = FakeGraph(me=_err(403, "accessDenied"))
    code, report, _ = _apply(graph, tmp_path)
    assert code == 2 and report["stage"] == "identity_unverified" and "POST" not in graph.methods
    code, report, _ = _go(_argv("--check"), FakeGraph(me=_err(403, "accessDenied")))
    assert code == 0 and report["user"]["verified"] is False


# ── evidencia de duplicados ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("where", ["drive", "item"])
@pytest.mark.parametrize("shape", ["grantedToV2", "grantedToIdentitiesV2"])
@pytest.mark.parametrize("role", ["write", "owner", "fullcontrol"])
def test_an_equivalent_grant_found_in_any_source_is_authoritative_presence_and_nothing_is_posted(where, shape, role, tmp_path):
    perms = (200, {}, {"value": [_app_perm(LOCAL, [role], shape)]})
    graph = FakeGraph(**{("drive_perms" if where == "drive" else "item_perms"): perms})
    code, report, _ = _go(_argv("--dry-run"), graph)
    assert code == 0 and report["duplicate_state"]["state"] == "ALREADY_GRANTED" and "already_granted_nothing_to_do" in report["blockers"] and "POST" not in graph.methods
    graph = FakeGraph(**{("drive_perms" if where == "drive" else "item_perms"): perms})
    code, report, _ = _apply(graph, tmp_path)
    assert code == 3 and report["stage"] == "apply_blocked" and "already_granted_nothing_to_do" in report["blockers"] and "POST" not in graph.methods and _audit_files(tmp_path) == []


def test_a_read_only_grant_is_flagged_as_insufficient_not_as_absent(tmp_path):
    graph = FakeGraph(item_perms=(200, {}, {"value": [_app_perm(LOCAL, ["read"])]}))
    _, report, _ = _go(_argv("--dry-run"), graph)
    assert report["duplicate_state"]["state"] == "ROLE_INSUFFICIENT_PRESENT" and "existing_grant_with_insufficient_role" in report["blockers"]
    code, report, _ = _apply(FakeGraph(item_perms=(200, {}, {"value": [_app_perm(LOCAL, ["read"])]})), tmp_path)
    assert code == 3 and "existing_grant_with_insufficient_role" in report["blockers"]


def test_other_apps_grants_and_application_links_do_not_count_as_a_grant_for_the_target():
    other = _app_perm(OTHER_GUID, ["write"])
    link = {"id": "l", "roles": ["write"], "link": {"type": "edit", "application": {"id": LOCAL}}}
    graph = FakeGraph(drive_perms=(200, {}, {"value": [other, link]}))
    _, report, _ = _go(_argv("--check"), graph)
    assert report["duplicate_state"]["state"] == "UNDETERMINED" and all(e["matches"] == [] for e in report["evidence"])


def test_absence_is_never_declared_authoritative_even_when_every_enumeration_is_complete_and_empty():
    graph = FakeGraph()
    _, report, _ = _go(_argv("--check"), graph)
    state = report["duplicate_state"]
    assert state["state"] == "UNDETERMINED" and any("permisos de compartir" in r for r in state["reasons"]) and any("heredados" in r for r in state["reasons"])
    assert sg.absence_is_authoritative(report["evidence"])[0] is False


def test_undetermined_state_blocks_apply_until_the_owner_acknowledges_it_explicitly(tmp_path):
    # Sin la bandera: ni siquiera se autentica (compuerta temprana). Con la bandera: avanza, pero el estado sigue siendo UNDETERMINED (no se simula idempotencia).
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path, **{"--acknowledge-undetermined": None})
    assert code == 3 and report["stage"] == "apply_not_confirmed" and report["missing"] == ["--acknowledge-undetermined"] and graph.requests == []
    code, report, _ = _go(_with(_argv("--dry-run"), {"--acknowledge-inheritance-break": True}), FakeGraph())
    assert "undetermined_not_acknowledged" in report["blockers"] and report["would_apply"] is False
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path)
    assert code == 0 and report["duplicate_state"]["state"] == "UNDETERMINED" and graph.methods.count("POST") == 1


@pytest.mark.parametrize("which", ["drive_perms", "item_perms"])
@pytest.mark.parametrize("status,code_", [(401, "unauthorized"), (403, "accessDenied"), (404, "itemNotFound")])
def test_an_enumeration_that_fails_is_reported_incomplete_and_never_read_as_absence(which, status, code_, tmp_path):
    graph = FakeGraph(**{which: _err(status, code_, rid="req-enum")})
    _, report, _ = _go(_argv("--dry-run"), graph)
    failed = next(e for e in report["evidence"] if e["complete"] is False)
    assert (failed["status"], failed["code"], failed["request_id"]) == (status, code_, "req-enum")
    assert report["duplicate_state"]["state"] == "UNDETERMINED" and any("incompleta" in r for r in report["duplicate_state"]["reasons"]) and report["would_apply"] is False
    assert "evidence_incomplete" in report["blockers"]
    graph = FakeGraph(**{which: _err(status, code_)})
    code, report, _ = _apply(graph, tmp_path)
    assert code == 3 and "evidence_incomplete" in report["blockers"] and "POST" not in graph.methods


def test_without_list_identifiers_the_list_item_source_is_unavailable_and_reported():
    graph = FakeGraph(meta=(200, {}, _meta(sharepointIds={"siteId": SITE})))
    _, report, _ = _go(_argv("--check"), graph)
    item = next(e for e in report["evidence"] if e["source"] == "listItem.permissions")
    assert item["complete"] is False and "no disponible" in item["note"] and report["duplicate_state"]["state"] == "UNDETERMINED"


def test_pagination_is_followed_and_a_next_link_outside_graph_is_never_called_with_the_token():
    graph = FakeGraph()
    base = PREVIEW_URL
    first = (200, {}, {"value": [], "@odata.nextLink": base + "?$skiptoken=2"})
    second = (200, {}, {"value": [_app_perm(LOCAL, ["write"])]})
    original = graph.__call__

    def routed(method, url, headers, data):
        if url == base:
            graph.requests.append({"method": method, "url": url, "headers": headers, "data": data})
            return 200, {}, json.dumps(first[2]).encode()
        if url.endswith("$skiptoken=2"):
            graph.requests.append({"method": method, "url": url, "headers": headers, "data": data})
            return 200, {}, json.dumps(second[2]).encode()
        return original(method, url, headers, data)
    _, report, _ = _go(_argv("--check"), routed)
    assert report["duplicate_state"]["state"] == "ALREADY_GRANTED"

    evil = FakeGraph(drive_perms=(200, {}, {"value": [], "@odata.nextLink": "https://evil.example.com/steal"}))
    _, report, _ = _go(_argv("--check"), evil)
    assert all("evil.example.com" not in r["url"] for r in evil.requests)
    assert next(e for e in report["evidence"] if e["source"] == "driveItem.permissions")["complete"] is False


# ── --apply: compuertas previas a cualquier llamada ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("opts,missing", [
    ({"--confirm-apply": None}, ["--confirm-apply"]),
    ({"--confirm-apply": ""}, ["--confirm-apply"]),
    ({"--confirm-apply": "si"}, ["--confirm-apply"]),
    ({"--confirm-apply": sg.CONFIRM_PHRASE.lower()}, ["--confirm-apply"]),
    ({"--acknowledge-undetermined": None}, ["--acknowledge-undetermined"]),
    ({"--acknowledge-inheritance-break": None}, ["--acknowledge-inheritance-break"]),
    ({"--confirm-apply": None, "--acknowledge-undetermined": None, "--acknowledge-inheritance-break": None}, ["--confirm-apply", "--acknowledge-undetermined", "--acknowledge-inheritance-break"]),
])
def test_apply_needs_every_explicit_confirmation_and_fails_before_authenticating_or_calling_graph(tmp_path, opts, missing):
    graph = FakeGraph()
    code, report, text = _apply(graph, tmp_path, **opts)
    assert code == 3 and report["stage"] == "apply_not_confirmed" and report["missing"] == missing and graph.requests == [] and "token" not in report and SECRET not in text
    assert _audit_files(tmp_path) == []


@pytest.mark.parametrize("opt,stage", [("--admin-client-id", "admin_client_required"), ("--expect-user", "expect_user_required")])
def test_apply_requires_the_administrative_app_and_the_expected_user(tmp_path, opt, stage):
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path, **{opt: None})
    assert code == 2 and report["stage"] == stage and graph.requests == []


def test_apply_refuses_to_run_without_an_interactive_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # no es TTY (p. ej. un agente o un pipe)
    graph = FakeGraph()
    code, report, _ = _go(_apply_argv(tmp_path), graph)  # sin prompt inyectado
    assert code == 3 and report["stage"] == "interactive_required" and graph.requests == [] and _audit_files(tmp_path) == []


@pytest.mark.parametrize("answer", ["", "si", "yes", sg.CONFIRM_PHRASE.lower(), sg.ROLLBACK_PHRASE, sg.CONFIRM_PHRASE + " ahora"])
def test_a_wrong_interactive_confirmation_aborts_without_posting_or_writing_audit(tmp_path, answer):
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path, prompt=lambda text: answer)
    assert code == 3 and report["stage"] == "apply_not_confirmed" and "POST" not in graph.methods and _audit_files(tmp_path) == []


def test_the_interactive_prompt_shows_the_exact_operation_and_the_risks(tmp_path):
    seen = []
    _apply(FakeGraph(), tmp_path, prompt=lambda text: seen.append(text) or sg.CONFIRM_PHRASE)
    text = seen[0]
    assert USER in text and ADMIN_APP in text and ITEM in text and LOCAL in text and PREVIEW_URL in text and '"roles":["write"]' in text and "UNDETERMINED" in text and "herencia" in text and sg.CONFIRM_PHRASE in text


def test_a_token_that_cannot_write_blocks_apply_without_posting(tmp_path):
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path, token=_jwt(scp="Sites.Read.All User.Read"))
    assert code == 3 and report["stage"] == "apply_blocked" and "token_scope_insufficient_for_apply" in report["blockers"] and "POST" not in graph.methods and _audit_files(tmp_path) == []


# ── --apply: un único POST, relectura, auditoría ────────────────────────────────────────────────────────────
def test_apply_rereads_permissions_right_before_a_single_post_and_audits_it(tmp_path):
    graph = FakeGraph()
    code, report, text = _apply(graph, tmp_path)
    posts = [r for r in graph.requests if r["method"] == "POST"]
    assert code == 0 and report["ok"] is True and len(posts) == 1
    assert posts[0]["url"] == PREVIEW_URL and json.loads(posts[0]["data"]) == {"grantedToV2": {"application": {"id": LOCAL}}, "roles": ["write"]}
    # target, drive, item, /me, (confirmación) RELECTURA drive+item, POST, verificación drive+item
    assert graph.methods == ["GET"] * 6 + ["POST"] + ["GET"] * 2 and graph.requests[4]["url"].endswith("/permissions") and graph.requests[5]["url"].endswith("/permissions")
    assert report["post"]["status"] == 201 and report["post"]["response_matches_request"] is True and report["post"]["permission_id"] == "perm-1" and "evidence_after" in report and "evidence_before_post" in report
    assert "DELETE" not in graph.methods and set(graph.methods) <= {"GET", "POST"}
    files = _audit_files(tmp_path)
    assert len(files) == 2 and any(f.endswith("-intent.json") for f in files) and any(f.endswith("-result.json") for f in files)
    saved = json.loads(_result_file(tmp_path).read_text(encoding="utf-8"))
    assert saved["tool"] == sg.TOOL_ID and saved["post"]["permission_id"] == "perm-1" and saved["ids"]["client_id"] == LOCAL and saved["user"]["upn"] == USER
    assert report["audit"]["result"].endswith("-result.json") and "rollback_hint" in report
    blob = text + "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "audit").iterdir())
    assert SECRET not in blob and "Authorization" not in blob


def test_a_grant_that_appears_between_the_first_read_and_the_post_aborts_without_posting(tmp_path):
    appeared = (200, {}, {"value": [_app_perm(LOCAL, ["write"], pid="perm-raced")]})
    graph = FakeGraph(drive_perms=[(200, {}, {"value": []}), appeared])
    code, report, _ = _apply(graph, tmp_path)
    assert code == 3 and report["stage"] == "duplicate_observed_before_post" and "POST" not in graph.methods
    assert report["observed_before_post"][0]["permission_id"] == "perm-raced" and _audit_files(tmp_path) == []


def test_any_observable_match_in_the_reread_aborts_even_with_a_read_only_role_or_from_the_list_item_source(tmp_path):
    for role, key in (("read", "drive_perms"), ("owner", "item_perms")):
        graph = FakeGraph(**{key: [(200, {}, {"value": []}), (200, {}, {"value": [_app_perm(LOCAL, [role])]})]})
        code, report, _ = _apply(graph, tmp_path)
        assert code == 3 and report["stage"] == "duplicate_observed_before_post" and "POST" not in graph.methods


def test_an_incomplete_reread_before_the_post_aborts_without_posting(tmp_path):
    graph = FakeGraph(item_perms=[(200, {}, {"value": []}), _err(403, "accessDenied")])
    code, report, _ = _apply(graph, tmp_path)
    assert code == 3 and report["stage"] == "evidence_incomplete_before_post" and report["incomplete"] == ["listItem.permissions"] and "POST" not in graph.methods


def test_apply_aborts_before_the_post_when_the_audit_record_cannot_be_written(tmp_path):
    blocker = tmp_path / "audit"
    blocker.write_text("un archivo, no una carpeta", encoding="utf-8")
    graph = FakeGraph()
    code, report, _ = _apply(graph, tmp_path)
    assert code == 2 and report["stage"] == "audit_unwritable" and "POST" not in graph.methods


@pytest.mark.parametrize("status,code_", [(401, "InvalidAuthenticationToken"), (403, "accessDenied"), (404, "itemNotFound"), (409, "nameAlreadyExists"), (500, "generalException"), (503, "serviceNotAvailable")])
def test_a_failed_post_is_reported_once_and_never_retried(tmp_path, status, code_):
    graph = FakeGraph(post=_err(status, code_, "denied", rid="req-post"))
    code, report, _ = _apply(graph, tmp_path)
    assert code == 1 and report["stage"] == "post" and graph.methods.count("POST") == 1 and "DELETE" not in graph.methods
    assert (report["post"]["status"], report["post"]["code"], report["post"]["request_id"]) == (status, code_, "req-post")
    assert json.loads(_result_file(tmp_path).read_text(encoding="utf-8"))["stage"] == "post"
    if status in (401, 403):
        assert len(report["diagnosis"]) == 2 and any("Owner/FullControl" in d for d in report["diagnosis"]) and any("Propietario" in d for d in report["diagnosis"])


def test_a_response_that_does_not_match_the_request_is_not_reported_as_success_and_keeps_the_id_for_rollback(tmp_path):
    graph = FakeGraph(post=(201, {}, {"id": "perm-odd", "roles": ["read"], "grantedToV2": {"application": {"id": LOCAL}}}))
    code, report, _ = _apply(graph, tmp_path)
    assert code == 1 and report["stage"] == "post_response_mismatch" and report["post"]["response_matches_request"] is False and report["ok"] is False
    assert report["post"]["permission_id"] == "perm-odd" and "rollback_hint" in report


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), ConnectionResetError("reset"), sg.ToolError("network_error", "sin respuesta completa", exit_code=1)])
def test_a_timeout_after_a_potentially_executed_post_is_an_unknown_outcome_with_no_retry(tmp_path, failure):
    graph = FakeGraph(post=failure)
    code, report, _ = _apply(graph, tmp_path)
    assert code == sg.EXIT_OUTCOME_UNKNOWN == 4 and report["stage"] == "post_outcome_unknown" and report["ok"] is False
    assert report["post"]["outcome"] == "UNKNOWN" and graph.methods.count("POST") == 1  # NUNCA se reintenta
    assert graph.methods[-2:] == ["GET", "GET"] and "evidence_after" in report and "DELETE" not in graph.methods  # sólo relectura; jamás un rollback solo
    assert json.loads(_result_file(tmp_path).read_text(encoding="utf-8"))["stage"] == "post_outcome_unknown"
    assert "permission_id" not in report["post"] and "NO se reintenta" in report["error"]


def test_apply_never_retries_on_any_outcome(tmp_path):
    for post in (_err(500, "generalException"), _err(429, "tooManyRequests"), TimeoutError(), (201, {}, {"id": "p", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL}}})):
        graph = FakeGraph(post=post)
        _apply(graph, tmp_path / str(id(post)))
        assert graph.methods.count("POST") == 1 and "DELETE" not in graph.methods


# ── --rollback ───────────────────────────────────────────────────────────────────────────────────────────────
def _applied(tmp_path, **graph_kw):
    graph = FakeGraph(**graph_kw)
    code, _, _ = _apply(graph, tmp_path)
    return code, _result_file(tmp_path)


def _rollback_argv(tmp_path, audit_file, perm_id="perm-1", **over):
    opts = {"--permission-id": perm_id, "--audit-file": str(audit_file), "--confirm-rollback": sg.ROLLBACK_PHRASE, "--admin-client-id": ADMIN_APP, "--expect-user": USER,
            "--audit-dir": str(tmp_path / "audit"), **over}
    return _with(_argv("--rollback"), opts)


def _rollback(graph, tmp_path, audit_file, perm_id="perm-1", prompt=YES_ROLLBACK, token=None, **over):
    return _go(_rollback_argv(tmp_path, audit_file, perm_id, **over), graph, token=token, prompt=prompt)


def test_rollback_deletes_exactly_the_recorded_permission_after_verifying_and_confirming_it(tmp_path):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph(perm_get=[(200, {}, {"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL}}}), _err(404, "itemNotFound")])
    code, report, text = _rollback(graph, tmp_path, audit_file)
    deletes = [r for r in graph.requests if r["method"] == "DELETE"]
    assert code == 0 and report["ok"] is True and len(deletes) == 1 and deletes[0]["url"] == PREVIEW_URL + "/perm-1" and deletes[0]["data"] is None
    assert graph.methods == ["GET", "GET", "GET", "DELETE", "GET"] and report["delete"]["status"] == 204 and report["permission_gone"] is True and "NO garantiza" in report["note"]
    assert "POST" not in graph.methods and SECRET not in text
    names = _audit_files(tmp_path)
    assert any(n.endswith("-rollback-intent.json") for n in names) and any(n.endswith("-rollback-result.json") for n in names)


def test_rollback_never_runs_without_every_explicit_input_and_fails_before_any_call(tmp_path):
    _, audit_file = _applied(tmp_path)
    for over, stage in (({"--confirm-rollback": None}, "rollback_not_confirmed"), ({"--confirm-rollback": "revocar grant write"}, "rollback_not_confirmed"),
                        ({"--permission-id": None}, "rollback_input_missing"), ({"--audit-file": None}, "rollback_input_missing"),
                        ({"--admin-client-id": None}, "rollback_input_missing"), ({"--expect-user": None}, "rollback_input_missing")):
        graph = FakeGraph()
        code, report, _ = _go(_rollback_argv(tmp_path, audit_file, **over), graph, prompt=YES_ROLLBACK)
        assert code in (2, 3) and report["stage"] == stage and graph.requests == []


def test_rollback_refuses_without_an_interactive_terminal(tmp_path, monkeypatch):
    _, audit_file = _applied(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    graph = FakeGraph()
    code, report, _ = _go(_rollback_argv(tmp_path, audit_file), graph)
    assert code == 3 and report["stage"] == "interactive_required" and graph.requests == []


@pytest.mark.parametrize("perm_id", ["../etc", "a/b", "perm 1", "x" * 201, "perm?x=1"])
def test_rollback_rejects_a_malformed_permission_id_without_network(tmp_path, perm_id):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph()
    code, report, _ = _rollback(graph, tmp_path, audit_file, perm_id)
    assert code == 2 and report["stage"] == "rollback_invalid" and graph.requests == []


def test_rollback_only_accepts_a_permission_id_recorded_by_a_previous_apply(tmp_path):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph()
    code, report, _ = _rollback(graph, tmp_path, audit_file, "some-other-permission")
    assert code == 3 and report["stage"] == "rollback_permission_not_recorded" and graph.requests == []


def test_rollback_rejects_an_audit_file_that_is_missing_foreign_or_for_another_target(tmp_path):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph()
    code, report, _ = _rollback(graph, tmp_path, tmp_path / "no-such-file.json")
    assert code == 2 and report["stage"] == "rollback_audit_unreadable" and graph.requests == []
    foreign = json.loads(audit_file.read_text(encoding="utf-8"))
    foreign["ids"]["item_id"] = "01OTHERITEM"
    other = tmp_path / "foreign.json"
    other.write_text(json.dumps(foreign), encoding="utf-8")
    code, report, _ = _rollback(graph, tmp_path, other)
    assert code == 3 and report["stage"] == "rollback_audit_mismatch" and graph.requests == []
    other.write_text(json.dumps({**json.loads(audit_file.read_text(encoding="utf-8")), "tool": "otra-herramienta"}), encoding="utf-8")
    code, report, _ = _rollback(graph, tmp_path, other)
    assert code == 3 and report["stage"] == "rollback_audit_mismatch" and graph.requests == []


@pytest.mark.parametrize("live", [
    {"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": OTHER_GUID}}},                                                                     # otra app
    {"id": "perm-1", "roles": ["read"], "grantedToV2": {"application": {"id": LOCAL}}},                                                                           # otro rol
    {"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL}}, "inheritedFrom": {"driveId": DRIVE, "id": "PARENT"}},                     # heredado
    {"id": "perm-1", "roles": ["write"], "grantedToV2": {"user": {"id": "u"}}},                                                                                    # no es un grant de aplicación
    {"id": "perm-1", "roles": ["write", "owner"], "grantedToV2": {"application": {"id": LOCAL}}},                                                                  # rol distinto de exactamente `write`
])
def test_rollback_refuses_to_delete_a_live_permission_that_is_not_the_non_inherited_write_grant_of_the_local_app(tmp_path, live):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph(perm_get=(200, {}, live))
    code, report, _ = _rollback(graph, tmp_path, audit_file)
    assert code == 3 and report["stage"] == "rollback_target_mismatch" and "DELETE" not in graph.methods


def test_rollback_does_not_delete_a_permission_that_no_longer_exists_nor_with_a_wrong_confirmation(tmp_path):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph(perm_get=_err(404, "itemNotFound"))
    code, report, _ = _rollback(graph, tmp_path, audit_file)
    assert code == 1 and report["stage"] == "rollback_target_unreadable" and "DELETE" not in graph.methods
    graph = FakeGraph()
    code, report, _ = _rollback(graph, tmp_path, audit_file, prompt=lambda t: "si")
    assert code == 3 and report["stage"] == "rollback_not_confirmed" and "DELETE" not in graph.methods
    graph = FakeGraph()
    code, report, _ = _rollback(graph, tmp_path, audit_file, prompt=lambda t: sg.CONFIRM_PHRASE)  # la frase de apply NO sirve para revocar
    assert code == 3 and "DELETE" not in graph.methods


def test_rollback_needs_a_write_token_and_the_right_identity_and_target(tmp_path):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph()
    code, report, _ = _rollback(graph, tmp_path, audit_file, token=_jwt(scp="Sites.Read.All User.Read"))
    assert code == 3 and report["stage"] == "token_rejected" and "DELETE" not in graph.methods
    graph = FakeGraph(me=(200, {}, {"userPrincipalName": "otra@danwor.com"}))
    code, report, _ = _rollback(graph, tmp_path, audit_file)
    assert code == 2 and report["stage"] == "identity_mismatch" and "DELETE" not in graph.methods
    graph = FakeGraph(meta=(200, {}, _meta(name="CCHH HUB")))
    code, report, _ = _rollback(graph, tmp_path, audit_file)
    assert code == 2 and report["stage"] == "target_mismatch" and "DELETE" not in graph.methods


def test_a_failed_or_timed_out_delete_is_never_retried(tmp_path):
    _, audit_file = _applied(tmp_path)
    graph = FakeGraph(delete=_err(403, "accessDenied"))
    code, report, _ = _rollback(graph, tmp_path / "a", audit_file)
    assert code == 1 and report["stage"] == "delete" and graph.methods.count("DELETE") == 1
    graph = FakeGraph(delete=TimeoutError())
    code, report, _ = _rollback(graph, tmp_path / "b", audit_file)
    assert code == 4 and report["stage"] == "delete_outcome_unknown" and graph.methods.count("DELETE") == 1 and report["delete"]["outcome"] == "UNKNOWN"


def test_after_an_unknown_post_outcome_the_rollback_can_target_the_id_observed_in_the_reread(tmp_path):
    seen = (200, {}, {"value": [_app_perm(LOCAL, ["write"], pid="perm-9")]})
    graph = FakeGraph(post=TimeoutError(), drive_perms=[(200, {}, {"value": []}), (200, {}, {"value": []}), seen])
    code, _, _ = _apply(graph, tmp_path)
    assert code == 4
    audit_file = _result_file(tmp_path)
    assert "perm-9" in sg._recorded_permission_ids(json.loads(audit_file.read_text(encoding="utf-8")))
    graph = FakeGraph(perm_get=[(200, {}, {"id": "perm-9", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL}}}), _err(404, "itemNotFound")])
    code, report, _ = _rollback(graph, tmp_path, audit_file, "perm-9")
    assert code == 0 and [r["url"] for r in graph.requests if r["method"] == "DELETE"] == [PREVIEW_URL + "/perm-9"]


def test_no_apply_path_ever_issues_a_delete_and_rollback_never_posts(tmp_path):
    for i, kw in enumerate([{}, {"post": _err(500, "x")}, {"post": TimeoutError()}, {"drive_perms": (200, {}, {"value": [_app_perm(LOCAL, ["write"])]})}]):
        graph = FakeGraph(**kw)
        _apply(graph, tmp_path / f"run{i}")
        assert "DELETE" not in graph.methods


# ── independencia y superficie mínima ────────────────────────────────────────────────────────────────────────
def test_the_tool_is_stdlib_only_and_independent_of_the_hub_runtime_and_uploader():
    src = Path(sg.__file__).read_text(encoding="utf-8")
    imported = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert imported <= {"__future__", "argparse", "base64", "http", "json", "os", "re", "sys", "time", "urllib", "typing"}
    assert "profile_document" not in src and "microsoft365" not in src and "webapp" not in src.replace("`webapp`", "")


def test_the_graph_client_cannot_put_or_patch_and_deletes_only_through_the_dedicated_method():
    graph = sg.Graph("t", FakeGraph())
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(ValueError):
            graph._call(method, "https://graph.microsoft.com/v1.0/x")
    with pytest.raises(ValueError):
        graph._call("PUT", "https://graph.microsoft.com/v1.0/x", _delete_ok=True)
    with pytest.raises(sg.ToolError):
        graph.get("https://evil.example.com/v1.0/x")
    with pytest.raises(sg.ToolError):
        graph.delete_permission({"url": "https://evil.example.com/v1.0/x"})
    assert not any(hasattr(graph, name) for name in ("put", "delete", "patch"))
    assert sg.GRANT_ROLE == "write" and sg.build_grant_request(sg.ALLOWED_GRANTS[0])["body"]["roles"] == ["write"]
    req = sg.build_rollback_request(sg.ALLOWED_GRANTS[0], "perm-1")
    assert req == {"method": "DELETE", "url": PREVIEW_URL + "/perm-1"}


def test_secrets_never_reach_the_report_nor_the_streams_in_any_mode(tmp_path):
    for argv in (_argv("--check"), _argv("--dry-run")):
        _, report, text = _go(argv, FakeGraph())
        assert SECRET not in json.dumps(report) and SECRET not in text
    _, report, text = _go(_argv("--dry-run"), FakeGraph(meta=_err(403, "accessDenied")))
    assert SECRET not in text
    _, report, text = _apply(FakeGraph(post=_err(403, "accessDenied")), tmp_path)
    assert SECRET not in text and all(SECRET not in p.read_text(encoding="utf-8") for p in (tmp_path / "audit").iterdir())


# ── perfil production-do: aislamiento y sólo lectura ─────────────────────────────────────────────────────────
DO_COLL, DO_WEB = "65c64790-f933-46b9-823f-8e4ad57e69b1", "b9b7da7d-8f67-46a9-be27-5f176394718a"
DO_SITE = f"danwor.sharepoint.com,{DO_COLL},{DO_WEB}"
DO_DRIVE = "b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE"
DO_ITEM = "01ULA425ONX3YLUO3XTNDI46FMF43PFL4X"


def _do_argv(mode="--dry-run", **over):
    return _argv(mode, **{"--client-id": PROD, "--site-id": DO_SITE, "--drive-id": DO_DRIVE, "--item-id": DO_ITEM, **over})


def _do_meta(**over):
    body = {"id": DO_ITEM, "name": "CCHH HUB", "folder": {"childCount": 9}, "parentReference": {"driveId": DO_DRIVE},
            "sharepointIds": {"siteId": DO_COLL, "webId": DO_WEB, "listId": "LISTDO", "listItemId": "11"}}
    body.update(over)
    return body


def test_the_control_profile_is_exactly_the_one_that_was_already_approved():
    control = sg.ALLOWED_GRANTS[0]
    assert {k: control[k] for k in ("name", "tenant_id", "client_id", "site_id", "drive_id", "item_id", "folder_name")} == {
        "name": "control-local", "tenant_id": "f287b953-5428-4a5e-ae17-bd9c4cfaedbe", "client_id": "ad98b5cd-71d0-451f-8d70-e8581a4788fa", "site_id": SITE, "drive_id": DRIVE, "item_id": ITEM, "folder_name": "Hub"}
    assert control["modes"] == ("check", "dry-run", "apply", "rollback")


def test_the_production_profile_has_the_approved_identifiers_and_is_read_only():
    rows = {r["name"]: r for r in sg.ALLOWED_GRANTS}
    assert list(rows) == ["control-local", "production-do"]  # sólo estos dos destinos; sin parámetros genéricos
    do = rows["production-do"]
    assert (do["client_id"], do["site_id"], do["drive_id"], do["item_id"], do["folder_name"]) == (PROD, DO_SITE, DO_DRIVE, DO_ITEM, "CCHH HUB")
    assert do["modes"] == ("check", "dry-run") and "apply" not in do["modes"] and "rollback" not in do["modes"]
    control = rows["control-local"]
    for key in ("client_id", "site_id", "drive_id", "item_id"):
        assert control[key] != do[key]  # ningún identificador compartido entre perfiles
    assert sg.resolve_grant(sg.TENANT_ID, PROD, DO_SITE.upper(), DO_DRIVE, DO_ITEM)["name"] == "production-do"


@pytest.mark.parametrize("site,ok", [(SITE, True), (DO_SITE, True), (DO_SITE.upper(), True), ("danwor.sharepoint.com," + DO_COLL, False), ("danwor.sharepoint.com,x,y", False), ("," + DO_COLL + "," + DO_WEB, False), ("not a site", False)])
def test_the_site_id_accepts_a_collection_guid_or_the_composite_graph_id_only(site, ok):
    if ok:
        assert sg._norm("site", site) == site.lower()
    else:
        with pytest.raises(sg.ToolError) as e:
            sg._norm("site", site)
        assert e.value.stage == "config_invalid"


@pytest.mark.parametrize("ids", [
    {"client": LOCAL, "site": DO_SITE, "drive": DO_DRIVE, "item": DO_ITEM},      # app LOCAL sobre el destino productivo
    {"client": PROD, "site": SITE, "drive": DRIVE, "item": ITEM},                # app productiva sobre CONTROL
    {"client": PROD, "site": DO_SITE, "drive": DRIVE, "item": ITEM},             # sitio productivo + drive/item de CONTROL
    {"client": PROD, "site": SITE, "drive": DO_DRIVE, "item": DO_ITEM},          # sitio de CONTROL + drive/item productivos
    {"client": PROD, "site": DO_SITE, "drive": DO_DRIVE, "item": ITEM},          # item de CONTROL en el drive productivo
    {"client": LOCAL, "site": SITE, "drive": DO_DRIVE, "item": ITEM},
])
def test_mixed_profile_combinations_are_rejected_in_every_mode_without_network(tmp_path, ids):
    for mode in ("--check", "--dry-run", "--apply", "--rollback"):
        graph = FakeGraph()
        argv = _with(_argv(mode, **{"--client-id": ids["client"], "--site-id": ids["site"], "--drive-id": ids["drive"], "--item-id": ids["item"]}),
                     {"--audit-dir": str(tmp_path / "audit"), "--admin-client-id": ADMIN_APP, "--expect-user": USER, "--acknowledge-undetermined": True, "--acknowledge-inheritance-break": True,
                      "--confirm-apply": sg.CONFIRM_PHRASE, "--permission-id": "perm-1", "--audit-file": "x.json", "--confirm-rollback": sg.ROLLBACK_PHRASE})
        code, report, _ = _go(argv, graph, prompt=lambda t: pytest.fail("sin prompt"))
        assert code == 2 and report["stage"] == "config_rejected" and graph.requests == [] and _audit_files(tmp_path) == []


@pytest.mark.parametrize("mode", ["--check", "--dry-run"])
def test_production_check_and_dry_run_read_only_the_production_destination_and_never_touch_control(mode, tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_write_audit", lambda *a, **k: pytest.fail("lectura: no debe haber auditoría"))
    graph = FakeGraph(meta=(200, {}, _do_meta()))
    argv = _with(_do_argv(mode), {"--audit-dir": str(tmp_path / "audit"), "--acknowledge-undetermined": True, "--acknowledge-inheritance-break": True, "--confirm-apply": sg.CONFIRM_PHRASE})
    code, report, text = _go(argv, graph)
    assert code == 0 and report["ok"] is True and set(graph.methods) == {"GET"} and not (tmp_path / "audit").exists()
    assert report["allowlist"] == "production-do" and report["profile"] == {"name": "production-do", "enabled_modes": ["check", "dry-run"], "folder_name": "CCHH HUB"}
    assert report["target"]["name"] == "CCHH HUB" and report["preview"]["url"] == f"https://graph.microsoft.com/v1.0/drives/{DO_DRIVE}/items/{DO_ITEM}/permissions"
    assert report["preview"]["body"] == {"grantedToV2": {"application": {"id": PROD}}, "roles": ["write"]}
    urls = [r["url"] for r in graph.requests]
    assert not any(DRIVE in u or ITEM in u or SITE in u for u in urls) and any(DO_DRIVE in u for u in urls)
    assert any(u.startswith(f"https://graph.microsoft.com/v1.0/sites/danwor.sharepoint.com,{DO_COLL},{DO_WEB}/lists/LISTDO/items/11/permissions") for u in urls)  # id compuesto, sin resolver otro web
    assert "apply_disabled_for_profile" in report["blockers"] and report["would_apply"] is False  # aun con todos los reconocimientos y un token con escritura
    notes = " ".join(report["notes"])
    assert "Comunicaciones" in notes and "CONTROL" in notes and "herencia" in notes and "no garantiza restaurarla" in notes
    if mode == "--dry-run":
        assert any("deshabilitado" in p for p in report["pending_at_apply"])
    assert SECRET not in text


@pytest.mark.parametrize("mode", ["--apply", "--rollback"])
def test_production_apply_and_rollback_are_disabled_before_authenticating_confirming_or_calling_graph(mode, tmp_path):
    graph = FakeGraph(meta=(200, {}, _do_meta()))
    argv = _with(_do_argv(mode), {"--audit-dir": str(tmp_path / "audit"), "--admin-client-id": ADMIN_APP, "--expect-user": USER, "--acknowledge-undetermined": True, "--acknowledge-inheritance-break": True,
                                  "--confirm-apply": sg.CONFIRM_PHRASE, "--permission-id": "perm-1", "--audit-file": "x.json", "--confirm-rollback": sg.ROLLBACK_PHRASE})
    code, report, text = _go(argv, graph, prompt=lambda t: pytest.fail("no debe preguntarse nada"))
    assert code == 3 and report["stage"] == "mode_disabled_for_profile" and report["enabled_modes"] == ["check", "dry-run"]
    assert graph.requests == [] and _audit_files(tmp_path) == [] and "token" not in report and SECRET not in text


def test_production_does_not_even_ask_for_a_write_scope():
    graph = FakeGraph(meta=(200, {}, _do_meta()))
    code, report, _ = _go(_with(_do_argv("--dry-run"), {"--request-write-scope": True}), graph)
    assert code == 3 and report["stage"] == "write_scope_disabled_for_profile" and graph.requests == []
    login = _Login()
    code, report, _ = _go_device(_do_argv("--check", **{"--admin-client-id": ADMIN_APP}), login, FakeGraph(meta=(200, {}, _do_meta())))
    assert code == 0 and "Sites.ReadWrite.All" not in login.requests[0][2] and "Sites.Read.All" in login.requests[0][2]


@pytest.mark.parametrize("meta,fields", [
    (_meta(), ["item_id", "drive_id", "site_id", "web_id", "name"]),                                   # lo que hay en CONTROL no pasa por producción
    (_do_meta(name="Hub"), ["name"]),
    (_do_meta(sharepointIds={"siteId": DO_COLL, "listId": "L", "listItemId": "1"}), ["web_id"]),   # sin webId = fail closed
    (_do_meta(sharepointIds={"siteId": DO_COLL, "webId": SITE, "listId": "L", "listItemId": "1"}), ["web_id"]),
    (_do_meta(sharepointIds={"siteId": SITE, "webId": DO_WEB}), ["site_id"]),
    (_do_meta(parentReference={"driveId": DRIVE}), ["drive_id"]),
    (_do_meta(id=ITEM), ["item_id"]),
])
def test_the_production_destination_is_verified_by_ids_and_nothing_from_control_is_accepted(meta, fields):
    graph = FakeGraph(meta=(200, {}, meta))
    code, report, _ = _go(_do_argv("--dry-run"), graph)
    assert code == 2 and report["stage"] == "target_mismatch" and sorted(report["fields"]) == sorted(fields) and graph.methods == ["GET"] and "preview" not in report


def test_production_authorization_is_never_inferred_from_what_the_user_can_do_on_control():
    graph = FakeGraph(meta=_err(403, "accessDenied"))  # el usuario puede todo en CONTROL, pero Comunicaciones lo rechaza
    code, report, _ = _go(_do_argv("--check"), graph)
    assert code == 1 and report["stage"] == "target_unreadable" and report["graph"]["status"] == 403
    assert len(graph.requests) == 1 and DO_DRIVE in graph.requests[0]["url"]  # sin reintento ni consulta alternativa a CONTROL
    graph = FakeGraph(meta=(200, {}, _do_meta()), drive_perms=_err(403, "accessDenied"))
    code, report, _ = _go(_do_argv("--check"), graph)
    assert code == 0 and report["duplicate_state"]["state"] == "UNDETERMINED" and "evidence_incomplete" in report["blockers"]  # 403 de enumeración ≠ ausencia ≠ autorización


def test_a_control_audit_record_cannot_be_used_against_the_production_profile(tmp_path):
    _, audit_file = _applied(tmp_path)
    do_grant = sg.resolve_grant(sg.TENANT_ID, PROD, DO_SITE, DO_DRIVE, DO_ITEM)
    with pytest.raises(sg.ToolError) as e:
        sg._load_audit(str(audit_file), do_grant)
    assert e.value.stage == "rollback_audit_mismatch"
    with pytest.raises(sg.ToolError):
        sg.build_rollback_request(do_grant, "../x")  # y la forma del request sigue validándose


def test_the_control_profile_still_runs_all_modes_unchanged(tmp_path):
    code, report, _ = _go(_argv("--dry-run"), FakeGraph())
    assert code == 0 and report["profile"]["enabled_modes"] == ["check", "dry-run", "apply", "rollback"] and "apply_disabled_for_profile" not in report["blockers"]
    code, _, _ = _apply(FakeGraph(), tmp_path)
    assert code == 0


def test_the_network_guard_really_blocks_real_operations():
    with pytest.raises(RealNetworkAttempt):
        sg.Graph("t").get("https://graph.microsoft.com/v1.0/x")
    with pytest.raises(RealNetworkAttempt):
        sg.Graph("t").post_permission({"url": "https://graph.microsoft.com/v1.0/x", "body": {}})
    with pytest.raises(RealNetworkAttempt):
        sg.Graph("t").delete_permission({"url": "https://graph.microsoft.com/v1.0/x"})
    with pytest.raises(RealNetworkAttempt):
        socket.create_connection(("graph.microsoft.com", 443))
