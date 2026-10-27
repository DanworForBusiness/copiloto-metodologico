"""«Publicar modelo» para consultores del Hub — la autorización es el PERFIL (dueño o ``user_tier == "full"``), no la configuración.

Contrato que fijan estas pruebas:
  * UN solo predicado (``datasphere_publication_service.can_execute``) gobierna la UI (botón «+», panel, deep link) y el backend (POST /run): lo que la UI oculta, el POST manual lo rechaza;
  * dueño (APP_OWNER) y consultor ``full`` (el mismo criterio del Assessment): publican, con ``DANWOR_PUBLICATION_SAFE`` encendida o apagada —la publicación segura mejora la integridad de la operación, no decide
    QUIÉN la ejecuta—; solo lectura, fuera de la allowlist y anónimo: denegados SIEMPRE;
  * ``DANWOR_PUBLICATION_CONSULTANT_CLIENTS`` es una restricción OPCIONAL: ausente o vacía no restringe; si está definida, limita los clientes de un consultor (el dueño, no);
  * lo administrativo (perfiles de clientes compartidos con secretos de TODOS los clientes, regenerar el Catálogo) sigue siendo SOLO del dueño; ``access.is_owner`` no se amplió;
  * se conservan el mismo origen, la validación de hosts/token URL y las credenciales PROPIAS del consultor (nunca un perfil compartido).
La publicación real (extractor, pool, Catálogo) se simula en la frontera: aquí se prueba la AUTORIZACIÓN, no la integridad de la escritura (que es de la publicación segura, fase 0 / P1).
"""
from __future__ import annotations

import json
import sys
import types

import pytest

import webapp.main as main
from tests.webapp.test_datasphere_publication import CATALOG_PUBLISH_URL, MODULE_URL, RUN_URL, STEM, _client, _demo_catalog
from webapp import access
from webapp.services import auth_service, datasphere_assessment_service as asd, datasphere_profiles as dprofiles, datasphere_publication_service as pub

OWNER, CONSULTANT, OTHER, READER = "owner@danwor.com", "consultor@danwor.com", "otro.consultor@danwor.com", "lector@danwor.com"
STRANGER = "ajeno@otro.com"
SECRET = "s3cr3t-SINTETICO-NO-REAL"
ORIGIN = {"Origin": "http://testserver"}
FORM = {"pack": "datasphere_core", "host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token", "datasphere_client_id": "sb-propio",
        "datasphere_client_secret": SECRET, "space": "PROD", "datasphere_root_object": "AM_Compras", "client": "Pampa"}
PLUS = 'aria-label="Publicar en el catálogo"'


@pytest.fixture(params=["safe_off", "safe_on"])
def hub(request, monkeypatch, tmp_path):
    """Perfiles del Hub y catálogo de demostración. Se corre CON y SIN ``DANWOR_PUBLICATION_SAFE``: la autorización no puede cambiar."""
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{CONSULTANT},{OTHER}")           # perfil completo
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", READER)                                   # solo lectura
    monkeypatch.delenv(pub.CONSULTANT_CLIENTS_ENV, raising=False)
    monkeypatch.setenv("DANWOR_PUBLICATION_SAFE", "1" if request.param == "safe_on" else "0")
    pub._RUNNING.clear()
    _demo_catalog(monkeypatch, tmp_path)
    yield request.param
    main._invalidate_catalog_cache()


def restrict_clients(monkeypatch, clients="Pampa, Acme Corp"):
    """Restricción OPCIONAL de clientes para consultores (no es una condición de acceso)."""
    monkeypatch.setenv(pub.CONSULTANT_CLIENTS_ENV, clients)


@pytest.fixture()
def runs(monkeypatch):
    """Registra cada ejecución real de la publicación (el extractor jamás corre en estas pruebas)."""
    calls: list[dict] = []

    def fake_run(values, client_secret, *, user_email="", on_line=None, on_phase=None):
        calls.append({"client": values.client, "model": values.root_object, "secret": client_secret, "user": user_email, "client_id": values.client_id})
        return asd.Outcome("SUCCESS", operation_id="op1", duration_s=0.1, stage="publish", terminal_am=values.root_object)
    monkeypatch.setattr(pub, "run", fake_run)
    return calls


def done(response) -> dict:
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    return next(e for e in events if e["t"] == "done")


def post(session, **override):
    return session.post(RUN_URL, data={**FORM, **override}, headers=ORIGIN)


def ui(client, url=MODULE_URL) -> str:
    return client.get(url).text


# ═══════════════ el perfil decide: dueño y consultor full, sin configuración ═══════════════
def test_owner_and_full_consultant_publish_with_no_configuration_at_all(hub, runs):
    owner, consultant = _client(OWNER), _client(CONSULTANT)
    for c in (owner, consultant):
        html = ui(c)
        assert PLUS in html and 'id="dsp-form"' in html and "Acceso restringido" not in html           # visibilidad = la de la captura del dueño
        assert "data-dsp-allowed-clients" not in html                                                  # sin restricción opcional no hay lista que mostrar
    assert done(post(owner))["state"] == "SUCCESS" and done(post(consultant))["state"] == "SUCCESS"
    assert [r["user"] for r in runs] == [OWNER, CONSULTANT]


@pytest.mark.parametrize("email,allowed", [(OWNER, True), (CONSULTANT, True), (OTHER, True), (READER, False), (STRANGER, False)])
def test_the_publish_profile_is_the_assessment_profile(hub, email, allowed):
    """Dueño o perfil completo: la UI lo refleja por perfil."""
    html = ui(_client(email))
    assert (PLUS in html) is allowed and ('id="dsp-form"' in html) is allowed


def test_the_publish_predicate_is_the_assessment_predicate_not_a_copy(hub, monkeypatch, runs):
    """Si el criterio del Assessment cambia, el de publicar cambia con él (no hay una segunda definición de «consultor autorizado»)."""
    monkeypatch.setattr(asd, "can_execute", lambda request: False)
    consultant = _client(CONSULTANT)
    assert PLUS not in ui(consultant) and post(consultant).status_code == 403 and not runs


def test_access_is_owner_was_not_widened(hub):
    assert access.is_owner_user({"sub": OWNER}) and not access.is_owner_user({"sub": CONSULTANT}) and not access.is_owner_user({"sub": READER})


def test_authorization_does_not_consult_the_safe_publication_code(hub, monkeypatch, runs):
    """La publicación segura no decide quién publica: aunque su módulo ni siquiera pueda importarse o su sonda falle, el predicado no cambia."""
    monkeypatch.setitem(sys.modules, "tools.catalog.publication_txn", None)
    consultant = _client(CONSULTANT)
    assert PLUS in ui(consultant) and done(post(consultant))["state"] == "SUCCESS"
    monkeypatch.setitem(sys.modules, "tools.catalog.publication_txn", types.SimpleNamespace(safe_mode_enabled=lambda: 1 / 0))
    assert PLUS in ui(consultant) and done(post(consultant))["state"] == "SUCCESS"
    assert not hasattr(pub, "publication_guarantees_active") and not hasattr(pub, "consultants_enabled") and not hasattr(pub, "CONSULTANTS_ENV")


# ═══════════════ consultor full ═══════════════
def test_a_full_consultant_publishes_with_their_own_credentials_attributed_to_them(hub, runs):
    consultant = _client(CONSULTANT)
    r = post(consultant)
    assert done(r)["state"] == "SUCCESS" and SECRET not in r.text
    assert runs == [{"client": "Pampa", "model": "AM_Compras", "secret": SECRET, "user": CONSULTANT, "client_id": "sb-propio"}]    # SUS credenciales, atribuidas a SU usuario


def test_first_publication_and_republication_follow_the_same_gates(hub, runs):
    consultant = _client(CONSULTANT)
    assert done(post(consultant))["state"] == "SUCCESS"                                            # «primera»
    assert done(post(consultant))["state"] == "SUCCESS"                                            # «republicación» del mismo modelo
    assert len(runs) == 2                                                                          # no hay una distinción CREATE/UPDATE que se pueda demostrar: ambas pasan por las mismas compuertas
    assert done(post(_client(OWNER)))["state"] == "SUCCESS" and len(runs) == 3                     # y el dueño puede republicar lo del consultor


def test_the_form_shows_no_owner_only_profile_selector_to_a_consultant(hub, monkeypatch):
    monkeypatch.setattr(dprofiles, "source_configured", lambda: True)
    assert "datasphere-profiles" not in ui(_client(CONSULTANT))
    assert "datasphere-profiles" in ui(_client(OWNER))


def test_a_deep_link_opens_the_publish_state_for_the_consultant_but_not_for_a_reader(hub):
    assert 'id="catalog-publish-state"' in _client(CONSULTANT).get(CATALOG_PUBLISH_URL).text
    html = _client(READER).get(CATALOG_PUBLISH_URL).text
    assert 'id="catalog-publish-state"' not in html and "catalogView = 'publish'" not in html


# ═══════════════ restricción OPCIONAL de clientes ═══════════════
def test_without_the_optional_restriction_a_consultant_may_publish_on_any_client(hub, runs):
    assert done(post(_client(CONSULTANT), client="CualquierOtro"))["state"] == "SUCCESS" and len(runs) == 1


@pytest.mark.parametrize("value", ["", "   ", ",,", "*"])
def test_an_empty_or_wildcard_only_value_adds_no_restriction(hub, monkeypatch, runs, value):
    monkeypatch.setenv(pub.CONSULTANT_CLIENTS_ENV, value)
    assert pub.consultant_clients() == () and done(post(_client(CONSULTANT), client="Otro"))["state"] == "SUCCESS"


@pytest.mark.parametrize("client_name", ["OtroCliente", "pampa2", "", "*"])
def test_when_configured_a_consultant_is_limited_to_the_listed_clients(hub, monkeypatch, runs, client_name):
    restrict_clients(monkeypatch)
    d = done(post(_client(CONSULTANT), client=client_name))
    assert d["state"] == "FAILED" and d["error_code"] == "CLIENT_NOT_AUTHORIZED" and not runs


def test_when_configured_the_form_lists_the_allowed_clients_and_matching_ignores_case_and_spaces(hub, monkeypatch, runs):
    restrict_clients(monkeypatch)
    consultant = _client(CONSULTANT)
    html = ui(consultant)
    assert "data-dsp-allowed-clients" in html and "Pampa, Acme Corp" in html
    assert done(post(consultant, client="  acme   CORP "))["state"] == "SUCCESS" and len(runs) == 1


def test_the_owner_may_publish_on_any_client_even_when_the_restriction_is_configured(hub, monkeypatch, runs):
    restrict_clients(monkeypatch)
    assert done(post(_client(OWNER), client="CualquierOtro"))["state"] == "SUCCESS"


# ═══════════════ denegados siempre ═══════════════
def test_read_only_profile_outside_the_allowlist_and_anonymous_are_denied_in_ui_and_backend(hub, runs):
    for email in (READER, STRANGER):
        c = _client(email)
        assert PLUS not in ui(c) and 'id="dsp-form"' not in ui(c), email
        assert post(c).status_code == 403, email
    anonymous = _client(None)
    assert PLUS not in ui(anonymous) and post(anonymous).status_code == 401
    assert not runs


def test_losing_the_allowlist_takes_effect_on_the_next_request(hub, monkeypatch, runs):
    c = _client(CONSULTANT)
    assert done(post(c))["state"] == "SUCCESS"
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{OTHER}")                         # se lo retira con la sesión viva
    assert post(c).status_code == 403 and PLUS not in ui(c) and len(runs) == 1


def test_a_manual_post_cannot_bypass_what_the_ui_hides(hub, monkeypatch, runs):
    """El control no está en el botón: variantes de POST directo a la ruta protegida sin permiso."""
    consultant = _client(CONSULTANT)
    assert post(_client(READER)).status_code == 403
    assert post(_client(None)).status_code == 401
    restrict_clients(monkeypatch, "Acme Corp")
    assert done(post(consultant, client="Pampa"))["error_code"] == "CLIENT_NOT_AUTHORIZED"
    other_module = "/modules/01_Arquitectura_y_Conectividad/datasphere-publication/run"
    assert consultant.post(other_module, data=FORM, headers=ORIGIN).status_code == 404            # fuera del módulo que ofrece la herramienta
    assert not runs


# ═══════════════ lo administrativo sigue siendo solo del dueño ═══════════════
def test_shared_client_profiles_stay_owner_only(hub, monkeypatch, runs):
    consultant = _client(CONSULTANT)
    r = post(consultant, profile_id="abc123", graph_token="tok")
    assert r.status_code == 403 and not runs                                                       # el secreto de un perfil compartido jamás se resuelve para un consultor
    resolved = []
    monkeypatch.setattr(dprofiles, "resolve", lambda pid, tok: resolved.append(pid) or None)
    assert consultant.post(f"/modules/{STEM}/datasphere-profiles", data={"graph_token": "t"}, headers=ORIGIN).status_code == 403
    assert not resolved
    owner_run = post(_client(OWNER), profile_id="abc123", graph_token="tok")
    assert done(owner_run)["error_code"] == "PROFILE_UNAVAILABLE" and resolved == ["abc123"]       # el dueño sí lo usa


def test_refresh_catalog_stays_owner_only(hub):
    refresh = f"/modules/{STEM}/datasphere-assessment/refresh-catalog"
    assert _client(CONSULTANT).post(refresh, data={"pack": "datasphere_core"}, headers=ORIGIN).status_code == 403


# ═══════════════ origen, hosts y credenciales ═══════════════
def test_same_origin_is_still_required(hub, runs):
    c = _client(CONSULTANT)
    assert c.post(RUN_URL, data=FORM, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(RUN_URL, data=FORM).status_code == 403
    assert not runs


def test_host_and_token_url_validation_still_runs_for_a_consultant_before_any_extraction(hub, monkeypatch):
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: (_ for _ in ()).throw(AssertionError("el extractor no debe correr con un host inválido")))
    d = done(post(_client(CONSULTANT), host="http://evil.example"))
    assert d["state"] == "INVALID" and d["error_code"] == "INVALID_PARAMETER"


def test_the_client_secret_never_reaches_the_response_or_the_form_context(hub, runs):
    c = _client(CONSULTANT)
    assert SECRET not in post(c).text and SECRET not in ui(c)
    assert "client_secret" not in asd.FormValues.__dataclass_fields__
