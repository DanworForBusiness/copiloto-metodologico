"""test_datasphere_assessment_consultant_access.py — "Assessment de tu modelo" abierto a los consultores del Hub.

Contrato que fija (y que ``test_datasphere_assessment.py`` / ``test_datasphere_owner_profiles.py`` complementan):
  * ACCESO A LA HERRAMIENTA != ACCESO A RESULTADOS AJENOS: ejecutar lo habilita un consultor con sesión válida y perfil completo del Hub
    (o el dueño); ver / exportar / eliminar solo es posible sobre los resultados PROPIOS (el ajeno es indistinguible de uno inexistente);
  * la herramienta NO es pública: sin sesión, sin perfil completo (solo lectura / fuera de la allowlist) o fuera del módulo 04 no hay uso;
  * `access.is_owner` no se tocó; las capacidades administrativas (regenerar el Catálogo, perfiles de clientes de SharePoint con los
    secretos de TODOS los clientes, Publicar en el Catálogo) siguen siendo del dueño;
  * se conservan el control de origen, la validación de hosts/token URL, el preflight real y el saneamiento de secretos.
"""
from __future__ import annotations

import logging

import pytest

from tests.webapp.test_datasphere_assessment import (  # noqa: F401  (fixtures y helpers de la suite hermana)
    ORIGIN, REFRESH, RUN, SECRET, STEM, VALID, _client, _delete, _done, _events, _post, _results_url, _rid, dsp, fake_export, owner, restricted, svc,
)
from tests.webapp.test_datasphere_owner_profiles import (  # noqa: F401
    MODULE_URL, PROFILES_URL, RUN_A, RUN_P, SECRET_ACME, SECRET_BETA, _capture_runs, _form, _list, world,
)
from webapp import access
from webapp.services import auth_service, datasphere_assessment_service as asd, datasphere_profiles as dp, datasphere_publication_service as pub

OWNER, A, B = "owner@danwor.com", "consultor.a@danwor.com", "consultor.b@danwor.com"


@pytest.fixture()
def hub(monkeypatch):
    """APP_OWNER distinto de los consultores; sin allowlists configuradas (todo usuario admitido tiene perfil completo, como hoy)."""
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    svc._RUNNING.clear()


# ── 1 / 2. quién puede ejecutar ──────────────────────────────────────────────
def test_the_app_owner_keeps_executing(hub, fake_export):
    d = _done(_post(_client(OWNER)))
    assert d["state"] == "SUCCESS" and d["result_url"] and fake_export["exports"] == 1


def test_a_consultant_who_is_not_the_owner_executes_and_the_result_is_theirs(hub, fake_export):
    assert not access.is_owner_user({"sub": A})                                                          # el predicado global NO se tocó
    consultant = _client(A)
    r = _post(consultant)
    d = _done(r)
    assert d["state"] == "SUCCESS" and fake_export["exports"] == 1 and SECRET not in r.text
    rid = _rid(r)
    assert svc.load_result(rid)["owner"] == A
    html = consultant.get(f"/modules/{STEM}?pack=datasphere_core").text
    assert "Assessments recientes" in html and rid in html and "Acceso restringido" not in html      # sus resultados y sus acciones


# ── 3. sin sesión ────────────────────────────────────────────────────────────
def test_without_a_session_nothing_can_be_executed_or_read(hub, fake_export):
    rid = _rid(_post(_client(A)))
    anon = _client(None)
    assert anon.post(RUN, data=VALID, headers=ORIGIN).status_code == 401
    for suffix in ("", "/log", "/export.json", "/export.md"):
        assert anon.get(_results_url(rid, suffix)).status_code == 401, suffix
    assert anon.post(_results_url(rid, "/delete"), data={"pack": "datasphere_core"}, headers=ORIGIN).status_code == 401
    assert anon.post(PROFILES_URL, data={"graph_token": "t"}, headers=ORIGIN).status_code == 401
    assert anon.post(REFRESH, data={"pack": "datasphere_core"}, headers=ORIGIN).status_code == 401
    assert fake_export["exports"] == 1 and svc.load_result(rid) is not None


# ── 4. sin acceso al módulo / sin perfil completo ────────────────────────────
def test_users_without_the_full_hub_profile_cannot_use_the_tool_or_read_results(restricted, fake_export):
    rid = _rid(_post(_client("trusted@danwor.com")))                                                      # perfil completo: ejecuta
    for email in ("outsider@danwor.com", "reader@danwor.com"):                                           # fuera de la allowlist / solo lectura
        c = _client(email)
        assert c.post(RUN, data=VALID, headers=ORIGIN).status_code == 403, email
        for suffix in ("", "/log", "/export.json", "/export.md"):
            assert c.get(_results_url(rid, suffix)).status_code == 403, (email, suffix)
        assert _delete(c, rid).status_code == 403, email
    assert fake_export["exports"] == 1 and svc.load_result(rid) is not None


def test_the_tool_is_unavailable_outside_the_modules_that_offer_it(hub, fake_export):
    c, other = _client(A), "/modules/01_Arquitectura_y_Conectividad/datasphere-assessment"
    assert c.post(f"{other}/run", data=VALID, headers=ORIGIN).status_code == 404
    assert c.get(f"{other}/result/{'0' * 32}").status_code == 404 and fake_export["exports"] == 0


def test_losing_the_access_takes_effect_on_the_next_request(hub, fake_export, monkeypatch):
    c = _client(A)
    rid = _rid(_post(c))
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "someone-else@danwor.com")                        # se lo retira de la allowlist con la sesión viva
    assert c.get(_results_url(rid)).status_code == 403 and _post(c).status_code == 403


# ── 5 / 6. aislamiento entre consultores (la diferencia herramienta vs. resultados ajenos) ──
def test_a_consultant_manages_their_own_results(hub, fake_export):
    c = _client(A)
    rid = _rid(_post(c))
    for suffix in ("", "/log", "/export.json", "/export.md"):
        assert c.get(_results_url(rid, suffix)).status_code == 200, suffix
    assert _delete(c, rid).json() == {"ok": True, "remaining": 0}
    assert c.get(_results_url(rid)).status_code == 404 and svc.load_result(rid) is None


def test_consultant_b_cannot_read_export_or_delete_what_consultant_a_generated_but_can_use_the_tool(hub, fake_export):
    a, b = _client(A), _client(B)
    rid = _rid(_post(a))
    for suffix in ("", "/log", "/export.json", "/export.md"):
        assert b.get(_results_url(rid, suffix)).status_code == 404, suffix                               # indistinguible de un id inexistente
    assert _delete(b, rid).status_code == 404 and svc.load_result(rid) is not None
    assert rid not in b.get(f"/modules/{STEM}?pack=datasphere_core").text and svc.list_results(B) == []
    assert _done(_post(b))["state"] == "SUCCESS"                                                           # B SÍ puede usar la herramienta
    assert b.get(_results_url(rid)).status_code == 404 and a.get(_results_url(rid)).status_code == 200


def test_not_even_the_owner_reads_a_consultants_result_through_the_tool(hub, fake_export):
    rid = _rid(_post(_client(A)))
    o = _client(OWNER)
    assert o.get(_results_url(rid)).status_code == 404 and _delete(o, rid).status_code == 404


def test_enumeration_by_foreign_or_malformed_ids_is_uniform(hub, fake_export):
    b, rid = _client(B), _rid(_post(_client(A)))
    foreign = b.get(_results_url(rid)).status_code
    assert foreign == b.get(_results_url("0" * 32)).status_code == 404                                    # ajeno == inexistente
    assert b.get(_results_url("zzz")).status_code == 404


# ── 7 / 8. control de origen y validación de entradas ───────────────────────
def test_posts_from_a_disallowed_origin_are_still_rejected(hub, fake_export):
    c = _client(A)
    rid = _rid(_post(c))
    for headers in ({}, {"Origin": "https://evil.example"}):
        assert c.post(RUN, data=VALID, headers=headers).status_code == 403
        assert c.post(_results_url(rid, "/delete"), data={"pack": "datasphere_core"}, headers=headers).status_code == 403
    assert fake_export["exports"] == 1 and svc.load_result(rid) is not None


def test_invalid_host_or_token_url_is_still_rejected_before_any_extraction(hub, fake_export):
    for bad in (dict(VALID, host="https://evil.example.com"), dict(VALID, token_url="https://evil.example.com/oauth/token"),
                dict(VALID, host="http://acme.eu10.hcs.cloud.sap"), dict(VALID, token_url="https://user:pw@acme.authentication.eu10.hana.ondemand.com/oauth/token")):
        r = _post(_client(A), bad)
        d = _done(r)
        assert d["state"] == "INVALID" and d["result_url"] is None and SECRET not in r.text
    assert fake_export["exports"] == 0 and not fake_export["cli"]


# ── 9. credenciales erróneas / sin autorización SAP ─────────────────────────
@pytest.mark.parametrize("hook, message, code", [
    ("oauth_preflight", "PRECHECK_OAUTH_401 - rechazado {secret} Bearer abcdefghijklmnop", "AUTHENTICATION_FAILED"),
    ("authorization_preflight", "PRECHECK_AUTH_403 - sin permisos sobre el Space {secret}", "INSUFFICIENT_DATASPHERE_PRIVILEGES"),
])
def test_wrong_credentials_or_no_sap_authorization_fail_in_a_controlled_way_without_leaking_secrets(hub, fake_export, monkeypatch, caplog, hook, message, code):
    def boom(self, *a):
        raise dsp.DatasphereCliError(message.format(secret=self.client_secret))
    monkeypatch.setattr(dsp.DatasphereCli, hook, boom)
    with caplog.at_level(logging.DEBUG):
        c = _client(A)
        r = _post(c)
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == code and d["result_url"] is None
    assert SECRET not in r.text and "abcdefghijklmnop" not in r.text and SECRET not in caplog.text and svc.list_results(A) == []
    assert SECRET not in c.get(f"/modules/{STEM}?pack=datasphere_core").text


# ── 10. lo administrativo sigue siendo del dueño ────────────────────────────
def test_administrative_functions_stay_owner_only_even_though_consultants_can_run_assessments(hub, fake_export, monkeypatch):
    ran = []
    monkeypatch.setattr(asd, "refresh_catalog", lambda **k: (ran.append(1), asd.Outcome("FAILED", "SENTINEL", "x"))[1])
    consultant = _client(A)
    assert consultant.post(REFRESH, data={"pack": "datasphere_core"}, headers=ORIGIN).status_code == 403 and ran == []
    assert consultant.post(PROFILES_URL, data={"graph_token": "t"}, headers=ORIGIN).status_code == 403
    assert asd.can_execute(_request_for(A)) and not asd.can_administer(_request_for(A)) and not asd.can_use_client_profiles(_request_for(A))
    assert asd.can_administer(_request_for(OWNER)) and asd.can_use_client_profiles(_request_for(OWNER))


def test_publishing_to_the_catalog_is_not_opened_by_the_assessment_change(hub, fake_export, monkeypatch):
    ran = []
    monkeypatch.setattr(pub, "run", lambda *a, **k: (ran.append(1), asd.Outcome("FAILED", "SENTINEL", "x"))[1])      # centinela (un fail dentro del hilo colgaría el stream)
    consultant = _client(A)
    assert consultant.post(RUN_P, data=_form(datasphere_client_secret="s"), headers=ORIGIN).status_code == 403 and ran == []
    assert not pub.can_execute(_request_for(A)) and pub.can_execute(_request_for(OWNER))
    html = consultant.get(f"{MODULE_URL}&tab=catalogo&catalog_action=publish").text
    assert 'id="dsp-client_secret"' not in html and "datasphere-publication/run" not in html


def _request_for(email):
    class _R:
        cookies = {auth_service.COOKIE_NAME: auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"})}
    return _R()


# ── 11. selector de perfiles: no revela secretos ni amplía privilegios ──────
def test_the_client_profile_selector_does_not_reach_consultants_and_never_reveals_secrets(world, hub, monkeypatch):
    monkeypatch.setattr(dp, "load_profiles", lambda t: pytest.fail("SharePoint no debe consultarse para un consultor"))
    consultant = _client(A)
    html = consultant.get(MODULE_URL).text
    assert 'id="dsa-form"' in html and 'id="dsa-host"' in html and 'id="dsa-client_secret"' in html           # formulario manual completo
    assert "data-dw-profile-select" not in html and "data-dw-owner-profile" not in html and "profiles-url" not in html
    assert SECRET_ACME not in html and SECRET_BETA not in html
    assert consultant.post(PROFILES_URL, data={"graph_token": "t"}, headers=ORIGIN).status_code == 403


def test_a_consultant_cannot_use_a_profile_id_to_run_even_with_the_tool_enabled(world, hub, monkeypatch):
    seen = _capture_runs(monkeypatch)
    pid = _list(_client(OWNER)).json()["profiles"][0]["profile_id"]
    assert _client(A).post(RUN_A, data=_form(profile_id=pid, graph_token="t"), headers=ORIGIN).status_code == 403
    assert seen["asm"] == [] and world["download"] == 1                                                   # solo la lectura del dueño tocó SharePoint
    manual = _client(A).post(RUN_A, data=_form(datasphere_client_secret="manual-secret"), headers=ORIGIN)
    assert manual.status_code == 200 and seen["asm"][0][1] == "manual-secret" and world["download"] == 1


def test_the_owner_keeps_the_selector_and_the_profiles(world, hub):
    html = _client(OWNER).get(MODULE_URL).text
    assert "data-dw-owner-profile" in html and f'data-profiles-url="{PROFILES_URL}"' in html
    r = _list(_client(OWNER))
    assert r.status_code == 200 and r.json()["ok"] and SECRET_ACME not in r.text and SECRET_BETA not in r.text
