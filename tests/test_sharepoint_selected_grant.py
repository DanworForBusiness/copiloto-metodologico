"""test_sharepoint_selected_grant.py — herramienta administrativa `tools/sharepoint_selected_grant.py` (grant Selected `write`), CHECKPOINT: sólo preparación.

Todo el transporte (Graph y login) está SIMULADO: ningún test habla con la red ni crea un grant. Contrato: --check/--dry-run sólo leen (nunca POST); --apply está deshabilitado y falla antes de autenticar; sólo la PRESENCIA de un
grant equivalente es evidencia autoritativa (la ausencia NO se puede probar → UNDETERMINED → apply bloqueado); un único POST sin reintentos cuando (en el futuro) se habilite; el token nunca aparece en reportes ni logs."""
from __future__ import annotations

import ast
import base64
import io
import json
import time
from pathlib import Path

import pytest

from tools import sharepoint_selected_grant as sg

SITE, DRIVE, ITEM = "a129a1d0-a4ee-4438-8de8-9e39e7007764", "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3", "01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ"
LOCAL, PROD, ADMIN_APP = sg.LOCAL_APP_ID, sg.PRODUCTION_APP_ID, "14d82eec-204b-4c2f-b7e8-296a70dab67e"
PREVIEW_URL = f"https://graph.microsoft.com/v1.0/drives/{DRIVE}/items/{ITEM}/permissions"
SECRET = "SECRET-ACCESS-TOKEN-VALUE"


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _jwt(**over) -> str:
    claims = {"tid": sg.TENANT_ID, "azp": ADMIN_APP, "aud": "https://graph.microsoft.com", "scp": "Sites.ReadWrite.All", "exp": time.time() + 3600, **over}
    claims = {k: v for k, v in claims.items() if v is not None}
    return f"{_b64({'alg': 'none'})}.{_b64(claims)}.{SECRET}"


def _argv(mode="--dry-run", **over):
    a = {"--tenant-id": sg.TENANT_ID, "--client-id": LOCAL, "--site-id": SITE, "--drive-id": DRIVE, "--item-id": ITEM, **over}
    out = [mode]
    for k, v in a.items():
        if v is not None:
            out += [k, v]
    return out


def _meta(**over):
    body = {"id": ITEM, "name": "Hub", "folder": {"childCount": 3}, "parentReference": {"driveId": DRIVE}, "sharepointIds": {"siteId": SITE, "listId": "LIST1", "listItemId": "7"}}
    body.update(over)
    return body


def _err(status, code, msg="boom", rid="req-1"):
    return (status, {"request-id": rid, "client-request-id": "cli-1"}, {"error": {"code": code, "message": msg}})


class FakeGraph:
    """Transporte simulado: `routes` = lista de (predicado(method, url), respuesta (status, headers, dict) o callable). Registra todo (incluidos headers) para auditoría."""

    def __init__(self, drive_perms=None, item_perms=None, meta=None, post=None):
        self.requests: list[dict] = []
        self.meta = meta if meta is not None else (200, {}, _meta())
        self.drive_perms = drive_perms if drive_perms is not None else (200, {}, {"value": []})
        self.item_perms = item_perms if item_perms is not None else (200, {}, {"value": []})
        self.post = post if post is not None else (201, {"request-id": "req-post"}, {"id": "perm-1", "roles": ["write"], "grantedToV2": {"application": {"id": LOCAL, "displayName": "Hub local"}}})

    def __call__(self, method, url, headers, data):
        self.requests.append({"method": method, "url": url, "headers": headers, "data": data})
        if method == "POST":
            resp = self.post
        elif "$select=id,name,folder" in url:
            resp = self.meta
        elif url.startswith("https://graph.microsoft.com/v1.0/drives/") and url.endswith("/permissions"):
            resp = self.drive_perms
        elif "/lists/" in url and "/items/" in url:
            resp = self.item_perms
        else:
            resp = (404, {}, {"error": {"code": "unexpected", "message": url}})
        status, headers_, body = resp
        return status, headers_, json.dumps(body).encode()

    @property
    def methods(self):
        return [r["method"] for r in self.requests]


def _app_perm(app_id, roles, shape="grantedToV2"):
    ident = {"application": {"id": app_id}}
    if shape == "grantedToV2":
        return {"id": "p", "roles": roles, "grantedToV2": ident}
    return {"id": "p", "roles": roles, "grantedToIdentitiesV2": [ident]}


def _go(argv, graph, token=None, **kw):
    out, err = io.StringIO(), io.StringIO()
    env = {sg.TOKEN_ENV: token if token is not None else _jwt()}
    code = sg.main(argv, env=env, transport=graph, stdout=out, stderr=err, **kw)
    return code, json.loads(out.getvalue()), out.getvalue() + err.getvalue()


# ── allowlist ────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_exact_control_local_combination_is_allowed_and_normalizes_guid_case():
    assert sg.resolve_grant(sg.TENANT_ID.upper(), LOCAL.upper(), SITE.upper(), DRIVE, ITEM)["name"] == "control-local"


@pytest.mark.parametrize("field,value", [("tenant_id", "11111111-2222-3333-4444-555555555555"), ("client_id", "11111111-2222-3333-4444-555555555555"), ("site_id", "11111111-2222-3333-4444-555555555555"),
                                         ("drive_id", DRIVE[:-1] + "X"), ("item_id", ITEM[:-1] + "X")])
def test_any_identifier_outside_the_allowlist_is_rejected_without_network(field, value):
    ids = {"tenant_id": sg.TENANT_ID, "client_id": LOCAL, "site_id": SITE, "drive_id": DRIVE, "item_id": ITEM, field: value}
    with pytest.raises(sg.ToolError) as e:
        sg.resolve_grant(**ids)
    assert e.value.stage == "config_rejected" and field in e.value.detail["fields"]


def test_the_production_app_is_reserved_and_never_an_executable_target():
    with pytest.raises(sg.ToolError) as e:
        sg.resolve_grant(sg.TENANT_ID, PROD, SITE, DRIVE, ITEM)
    assert e.value.stage == "config_rejected" and "RESERVADA" in e.value.message
    graph = FakeGraph()
    code, report, _ = _go(_argv(**{"--client-id": PROD}), graph)
    assert code == 2 and report["stage"] == "config_rejected" and graph.requests == []
    assert all(row["client_id"] != PROD for row in sg.ALLOWED_GRANTS)


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


# ── --apply deshabilitado ────────────────────────────────────────────────────────────────────────────────────
def test_apply_is_disabled_and_fails_before_authenticating_or_calling_graph():
    graph = FakeGraph()
    code, report, text = _go(_argv("--apply", **{}) + ["--confirm-apply", sg.CONFIRM_PHRASE], graph)
    assert sg.APPLY_ENABLED is False
    assert code == 3 and report["stage"] == "apply_disabled" and graph.requests == [] and "token" not in report and SECRET not in text


# ── autenticación ────────────────────────────────────────────────────────────────────────────────────────────
def test_no_credential_is_a_clear_auth_error_not_a_prompt():
    out = io.StringIO()
    code = sg.main(_argv(), env={}, transport=FakeGraph(), stdout=out, stderr=io.StringIO())
    report = json.loads(out.getvalue())
    assert code == 2 and report["stage"] == "auth_missing"


@pytest.mark.parametrize("over,problem", [({"tid": "11111111-2222-3333-4444-555555555555"}, "tenant_mismatch"), ({"azp": LOCAL}, "token_is_a_hub_app"), ({"azp": PROD}, "token_is_a_hub_app"),
                                          ({"aud": "https://other.example"}, "audience_not_graph"), ({"idtyp": "app"}, "not_a_delegated_token"), ({"scp": None}, "not_a_delegated_token"),
                                          ({"exp": 1}, "token_expired_or_without_exp")])
def test_an_env_token_that_is_not_a_clean_delegated_admin_token_is_rejected(over, problem):
    graph = FakeGraph()
    code, report, text = _go(_argv(), graph, token=_jwt(**over))
    assert code == 2 and report["stage"] == "token_rejected" and problem in report["problems"] and graph.requests == [] and SECRET not in text


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
    """Simula login.microsoftonline.com (device code)."""

    def __init__(self, polls=("authorization_pending", "ok"), scope="Sites.Read.All", devicecode_status=200):
        self.polls, self.scope, self.dc_status, self.requests = list(polls), scope, devicecode_status, []

    def __call__(self, method, url, headers, data):
        self.requests.append((method, url, data.decode()))
        if url.endswith("/devicecode"):
            if self.dc_status != 200:
                return self.dc_status, {}, json.dumps({"error": "unauthorized_client"}).encode()
            return 200, {}, json.dumps({"device_code": "DEV", "user_code": "ABCD1234", "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900, "interval": 5,
                                        "message": "Abrí https://microsoft.com/devicelogin e ingresá ABCD1234"}).encode()
        nxt = self.polls.pop(0)
        if nxt == "ok":
            return 200, {}, json.dumps({"access_token": SECRET, "scope": self.scope, "expires_in": 3599, "token_type": "Bearer"}).encode()
        return 400, {}, json.dumps({"error": nxt}).encode()


def _ticker():
    t = {"v": 0.0}

    def clock():
        return t["v"]

    def sleep(s):
        t["v"] += s
    return clock, sleep


def test_device_code_flow_uses_the_tenant_authority_a_public_client_and_never_prints_the_token():
    login, graph, (clock, sleep) = _Login(), FakeGraph(), _ticker()
    out, err = io.StringIO(), io.StringIO()
    code = sg.main(_argv(**{"--admin-client-id": ADMIN_APP}), env={}, transport=graph, device_transport=login, sleep=sleep, clock=clock, stdout=out, stderr=err)
    assert code == 0 and json.loads(out.getvalue())["token"]["source"] == "device_code"
    assert login.requests[0][1] == f"https://login.microsoftonline.com/{sg.TENANT_ID}/oauth2/v2.0/devicecode"
    assert "client_secret" not in "".join(r[2] for r in login.requests) and "scope=https%3A%2F%2Fgraph.microsoft.com%2FSites.Read.All" in login.requests[0][2]
    assert "ABCD1234" in err.getvalue() and SECRET not in out.getvalue() + err.getvalue()
    assert graph.requests[0]["headers"]["Authorization"] == f"Bearer {SECRET}"  # el token sólo viaja en el header hacia Graph


@pytest.mark.parametrize("polls,error", [(["authorization_declined"], "authorization_declined"), (["expired_token"], "expired_token"), (["bad_verification_code"], "bad_verification_code")])
def test_device_code_failures_stop_the_tool_without_calling_graph(polls, error):
    login, graph, (clock, sleep) = _Login(polls=polls), FakeGraph(), _ticker()
    out = io.StringIO()
    code = sg.main(_argv(**{"--admin-client-id": ADMIN_APP}), env={}, transport=graph, device_transport=login, sleep=sleep, clock=clock, stdout=out, stderr=io.StringIO())
    report = json.loads(out.getvalue())
    assert code == 2 and report["stage"] == "auth_failed" and report["error"] == error and graph.requests == []


def test_device_code_scopes_come_from_the_token_response_and_a_missing_sites_scope_is_rejected():
    login, graph, (clock, sleep) = _Login(polls=["ok"], scope="User.Read"), FakeGraph(), _ticker()
    out = io.StringIO()
    code = sg.main(_argv(**{"--admin-client-id": ADMIN_APP}), env={}, transport=graph, device_transport=login, sleep=sleep, clock=clock, stdout=out, stderr=io.StringIO())
    assert code == 2 and json.loads(out.getvalue())["stage"] == "token_rejected" and graph.requests == []


def test_the_admin_client_cannot_be_a_hub_app():
    code, report, _ = _go(_argv(**{"--admin-client-id": LOCAL}), FakeGraph(), token="")
    assert code == 2 and report["stage"] == "token_rejected" and "Hub" in report["error"]


def test_device_code_times_out_with_expired_token_instead_of_polling_forever():
    login, graph = _Login(polls=["authorization_pending"] * 500), FakeGraph()
    clock, sleep = _ticker()
    out = io.StringIO()
    code = sg.main(_argv(**{"--admin-client-id": ADMIN_APP}), env={}, transport=graph, device_transport=login, sleep=sleep, clock=clock, stdout=out, stderr=io.StringIO())
    assert code == 2 and json.loads(out.getvalue())["error"] == "expired_token" and len(login.requests) < 300


# ── --check / --dry-run: sólo lectura, destino y evidencia ───────────────────────────────────────────────────
def test_check_only_reads_and_reports_the_target_and_an_undetermined_duplicate_state():
    graph = FakeGraph()
    code, report, text = _go(_argv("--check"), graph)
    assert code == 0 and report["ok"] is True and set(graph.methods) == {"GET"}
    assert report["target"] == {"name": "Hub", "site_id": SITE, "list_id": "LIST1", "list_item_id": "7"}
    assert report["duplicate_state"]["state"] == "UNDETERMINED" and report["would_apply"] is False
    assert [e["source"] for e in report["evidence"]] == ["driveItem.permissions", "listItem.permissions"] and all(e["complete"] for e in report["evidence"])
    assert SECRET not in text and "Authorization" not in text


def test_dry_run_prepares_the_exact_minimal_request_but_never_posts():
    graph = FakeGraph()
    code, report, _ = _go(_argv("--dry-run"), graph)
    assert code == 0 and "POST" not in graph.methods
    assert report["preview"] == {"method": "POST", "url": PREVIEW_URL, "body": {"grantedToV2": {"application": {"id": LOCAL}}, "roles": ["write"]}}
    assert set(report["preview"]["body"]) == {"grantedToV2", "roles"} and list(report["preview"]["body"]["grantedToV2"]) == ["application"]
    assert {"apply_disabled_in_this_checkpoint", "duplicate_evidence_not_authoritative"} <= set(report["blockers"]) and report["would_apply"] is False


@pytest.mark.parametrize("meta,stage,fields", [
    ((200, {}, _meta(folder=None, file={"mimeType": "text/plain"})), "target_not_a_folder", None),
    ((200, {}, {k: v for k, v in _meta().items() if k != "folder"}), "target_not_a_folder", None),
    ((200, {}, _meta(sharepointIds={"siteId": "11111111-2222-3333-4444-555555555555"})), "target_mismatch", ["site_id"]),
    ((200, {}, _meta(sharepointIds=None)), "target_mismatch", ["site_id"]),
    ((200, {}, _meta(parentReference={"driveId": "b!OTHER"})), "target_mismatch", ["drive_id"]),
    ((200, {}, _meta(id="OTHERITEM")), "target_mismatch", ["item_id"]),
])
def test_a_destination_that_is_not_the_allowlisted_folder_is_rejected_before_any_grant_is_prepared(meta, stage, fields):
    graph = FakeGraph(meta=meta)
    code, report, _ = _go(_argv("--dry-run"), graph)
    assert code == 2 and report["stage"] == stage and "preview" not in report and graph.methods == ["GET"]
    if fields:
        assert report["fields"] == fields


@pytest.mark.parametrize("status,code_", [(401, "InvalidAuthenticationToken"), (403, "accessDenied"), (404, "itemNotFound"), (409, "conflict"), (500, "generalException")])
def test_graph_errors_on_the_target_are_reported_with_status_code_and_request_id_only(status, code_):
    graph = FakeGraph(meta=_err(status, code_, rid="req-xyz"))
    code, report, text = _go(_argv("--check"), graph)
    assert code == 1 and report["stage"] == "target_unreadable"
    assert report["graph"] == {"status": status, "code": code_, "message": "boom", "request_id": "req-xyz", "client_request_id": "cli-1"} and SECRET not in text
    assert len(graph.requests) == 1  # sin reintentos


# ── evidencia de duplicados ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("where", ["drive", "item"])
@pytest.mark.parametrize("shape", ["grantedToV2", "grantedToIdentitiesV2"])
@pytest.mark.parametrize("role", ["write", "owner", "fullcontrol"])
def test_an_equivalent_grant_found_in_any_source_is_authoritative_presence_and_nothing_is_posted(where, shape, role):
    perms = (200, {}, {"value": [_app_perm(LOCAL, [role], shape)]})
    graph = FakeGraph(**{("drive_perms" if where == "drive" else "item_perms"): perms})
    code, report, _ = _go(_argv("--dry-run"), graph)
    assert code == 0 and report["duplicate_state"]["state"] == "ALREADY_GRANTED" and "already_granted_nothing_to_do" in report["blockers"] and "POST" not in graph.methods


def test_a_read_only_grant_is_flagged_as_insufficient_not_as_absent():
    graph = FakeGraph(item_perms=(200, {}, {"value": [_app_perm(LOCAL, ["read"])]}))
    _, report, _ = _go(_argv("--dry-run"), graph)
    assert report["duplicate_state"]["state"] == "ROLE_INSUFFICIENT_PRESENT" and "existing_grant_with_insufficient_role" in report["blockers"]


def test_other_apps_grants_and_application_links_do_not_count_as_a_grant_for_the_target():
    other = _app_perm("11111111-2222-3333-4444-555555555555", ["write"])
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


@pytest.mark.parametrize("which", ["drive_perms", "item_perms"])
@pytest.mark.parametrize("status,code_", [(401, "unauthorized"), (403, "accessDenied"), (404, "itemNotFound")])
def test_an_enumeration_that_fails_is_reported_incomplete_and_never_read_as_absence(which, status, code_):
    graph = FakeGraph(**{which: _err(status, code_, rid="req-enum")})
    _, report, _ = _go(_argv("--dry-run"), graph)
    failed = next(e for e in report["evidence"] if e["complete"] is False)
    assert (failed["status"], failed["code"], failed["request_id"]) == (status, code_, "req-enum")
    assert report["duplicate_state"]["state"] == "UNDETERMINED" and any("incompleta" in r for r in report["duplicate_state"]["reasons"]) and report["would_apply"] is False


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


# ── --apply (plumbing futuro, sólo con transporte simulado y compuertas forzadas) ─────────────────────────────
@pytest.fixture
def armed(monkeypatch):
    """Simula un FUTURO habilitado: apply encendido y ausencia 'probada'. Sólo para verificar la plomería del único POST."""
    monkeypatch.setattr(sg, "APPLY_ENABLED", True)
    monkeypatch.setattr(sg, "absence_is_authoritative", lambda evidence: (True, []))


def _apply(graph, token=None, confirm=sg.CONFIRM_PHRASE):
    return _go(_argv("--apply") + (["--confirm-apply", confirm] if confirm is not None else []), graph, token=token)


def test_when_armed_apply_issues_exactly_one_post_with_the_minimal_body_and_verifies_the_response(armed):
    graph = FakeGraph()
    code, report, text = _apply(graph)
    posts = [r for r in graph.requests if r["method"] == "POST"]
    assert code == 0 and report["ok"] is True and len(posts) == 1
    assert posts[0]["url"] == PREVIEW_URL and json.loads(posts[0]["data"]) == {"grantedToV2": {"application": {"id": LOCAL}}, "roles": ["write"]}
    assert report["post"]["status"] == 201 and report["post"]["response_matches_request"] is True and report["post"]["permission_id"] == "perm-1" and "evidence_after" in report
    assert SECRET not in text and set(graph.methods) <= {"GET", "POST"}


@pytest.mark.parametrize("status,code_", [(401, "InvalidAuthenticationToken"), (403, "accessDenied"), (404, "itemNotFound"), (409, "nameAlreadyExists"), (500, "generalException"), (503, "serviceNotAvailable")])
def test_when_armed_a_failed_post_is_reported_once_and_never_retried(armed, status, code_):
    graph = FakeGraph(post=_err(status, code_, "denied", rid="req-post"))
    code, report, _ = _apply(graph)
    assert code == 1 and report["stage"] == "post" and graph.methods.count("POST") == 1
    assert (report["post"]["status"], report["post"]["code"], report["post"]["request_id"]) == (status, code_, "req-post")


def test_when_armed_a_response_that_does_not_match_the_request_is_not_reported_as_success(armed):
    graph = FakeGraph(post=(201, {}, {"id": "p", "roles": ["read"], "grantedToV2": {"application": {"id": LOCAL}}}))
    code, report, _ = _apply(graph)
    assert code == 1 and report["post"]["response_matches_request"] is False and report["ok"] is False


def test_when_armed_apply_still_needs_the_explicit_confirmation(armed):
    for confirm in (None, "", "si", sg.CONFIRM_PHRASE.lower()):
        graph = FakeGraph()
        code, report, _ = _apply(graph, confirm=confirm)
        assert code == 3 and report["stage"] == "apply_not_confirmed" and graph.requests == []


@pytest.mark.parametrize("graph_kw,blocker", [({"item_perms": (200, {}, {"value": [_app_perm(LOCAL, ["write"])]})}, "already_granted_nothing_to_do"),
                                              ({"item_perms": (200, {}, {"value": [_app_perm(LOCAL, ["read"])]})}, "existing_grant_with_insufficient_role")])
def test_when_armed_apply_does_not_post_over_an_existing_grant(armed, graph_kw, blocker):
    graph = FakeGraph(**graph_kw)
    code, report, _ = _apply(graph)
    assert code == 3 and report["stage"] == "apply_blocked" and blocker in report["blockers"] and "POST" not in graph.methods


def test_when_armed_apply_is_blocked_by_a_token_without_write_scope(armed):
    graph = FakeGraph()
    code, report, _ = _apply(graph, token=_jwt(scp="Sites.Read.All"))
    assert code == 3 and "token_scope_insufficient_for_apply" in report["blockers"] and "POST" not in graph.methods


def test_even_when_apply_is_enabled_indeterminate_duplicate_evidence_blocks_the_post(monkeypatch):
    monkeypatch.setattr(sg, "APPLY_ENABLED", True)  # la ausencia NO se parchea: es la compuerta real
    graph = FakeGraph()
    code, report, _ = _apply(graph)
    assert code == 3 and report["stage"] == "apply_blocked" and "duplicate_evidence_not_authoritative" in report["blockers"] and "POST" not in graph.methods


# ── independencia y superficie mínima ────────────────────────────────────────────────────────────────────────
def test_the_tool_is_stdlib_only_and_independent_of_the_hub_runtime_and_uploader():
    src = Path(sg.__file__).read_text(encoding="utf-8")
    imported = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert imported <= {"__future__", "argparse", "base64", "json", "os", "re", "sys", "time", "urllib", "typing"}
    assert "profile_document" not in src and "microsoft365" not in src and "webapp" not in src.replace("`webapp`", "")


def test_the_graph_client_cannot_put_patch_or_delete_and_never_leaves_graph():
    graph = sg.Graph("t", FakeGraph())
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(ValueError):
            graph._call(method, "https://graph.microsoft.com/v1.0/x")
    with pytest.raises(sg.ToolError):
        graph.get("https://evil.example.com/v1.0/x")
    assert not any(hasattr(graph, name) for name in ("put", "delete", "patch"))
    assert sg.GRANT_ROLE == "write" and sg.build_grant_request(sg.ALLOWED_GRANTS[0])["body"]["roles"] == ["write"]


def test_secrets_never_reach_the_report_nor_the_streams_in_any_mode():
    for argv in (_argv("--check"), _argv("--dry-run")):
        _, report, text = _go(argv, FakeGraph())
        assert SECRET not in json.dumps(report) and SECRET not in text
    _, report, text = _go(_argv("--dry-run"), FakeGraph(meta=_err(403, "accessDenied")))
    assert SECRET not in text
