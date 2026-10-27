"""«Publicar modelo» para consultores del Hub — autorización UI/backend por capacidad, apagada por defecto.

Contrato que fijan estas pruebas:
  * UN solo predicado (``datasphere_publication_service.can_execute``) gobierna la UI (botón «+», panel, deep link) y el backend (POST /run): lo que la UI oculta, el POST manual lo rechaza;
  * dueño (APP_OWNER): conserva sus permisos; consultor full: solo con la capacidad DISPONIBLE (interruptor + garantías de la publicación segura + clientes autorizados) y solo sobre sus clientes; solo lectura,
    fuera de la allowlist y anónimo: denegados SIEMPRE; con todo apagado (el default) el comportamiento es el de siempre;
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
SECRET = "s3cr3t-SINTETICO-NO-REAL"
ORIGIN = {"Origin": "http://testserver"}
FORM = {"pack": "datasphere_core", "host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token", "datasphere_client_id": "sb-propio",
        "datasphere_client_secret": SECRET, "space": "PROD", "datasphere_root_object": "AM_Compras", "client": "Pampa"}
PLUS = 'aria-label="Publicar en el catálogo"'


@pytest.fixture()
def hub(monkeypatch, tmp_path):
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{CONSULTANT},{OTHER}")           # perfil completo
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", READER)                                   # solo lectura
    for name in (pub.CONSULTANTS_ENV, pub.CONSULTANT_CLIENTS_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(sys.modules, "tools.catalog.publication_txn", raising=False)
    pub._RUNNING.clear()
    _demo_catalog(monkeypatch, tmp_path)
    yield
    main._invalidate_catalog_cache()


def capability(monkeypatch, *, flag=True, guarantees=True, clients="Pampa, Acme Corp"):
    """Apaga/enciende cada una de las tres condiciones por separado. Las garantías de la publicación segura (fase 0 / P1) se simulan con un módulo mínimo."""
    if flag:
        monkeypatch.setenv(pub.CONSULTANTS_ENV, "1")
    if clients:
        monkeypatch.setenv(pub.CONSULTANT_CLIENTS_ENV, clients)
    monkeypatch.setitem(sys.modules, "tools.catalog.publication_txn", types.SimpleNamespace(safe_mode_enabled=lambda: guarantees))


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


# ═══════════════ por defecto (todo apagado): sin cambios ═══════════════
def test_with_everything_off_only_the_owner_publishes_exactly_as_before(hub, runs):
    owner, consultant = _client(OWNER), _client(CONSULTANT)
    assert PLUS in ui(owner) and 'id="dsp-form"' in ui(owner) and done(post(owner))["state"] == "SUCCESS"
    html = ui(consultant)
    assert PLUS not in html and 'id="dsp-form"' not in html and 'id="catalog-publish-state"' not in html
    assert post(consultant).status_code == 403                                                    # el POST manual tampoco pasa
    assert len(runs) == 1 and runs[0]["user"] == OWNER


def test_access_is_owner_was_not_widened(hub, monkeypatch):
    capability(monkeypatch)
    assert access.is_owner_user({"sub": OWNER}) and not access.is_owner_user({"sub": CONSULTANT}) and not access.is_owner_user({"sub": READER})


# ═══════════════ cada condición, por separado, es necesaria ═══════════════
@pytest.mark.parametrize("kwargs,why", [({"flag": False}, "interruptor apagado"), ({"guarantees": False}, "garantías de la publicación segura inactivas"), ({"clients": ""}, "ningún cliente autorizado")])
def test_each_of_the_three_conditions_is_required_in_ui_and_backend(hub, monkeypatch, runs, kwargs, why):
    capability(monkeypatch, **kwargs)
    consultant = _client(CONSULTANT)
    assert PLUS not in ui(consultant) and 'id="dsp-form"' not in ui(consultant), why
    assert post(consultant).status_code == 403 and not runs, why
    assert PLUS in ui(_client(OWNER)) and done(post(_client(OWNER)))["state"] == "SUCCESS"        # el dueño no depende de la capacidad de consultores


def test_without_the_safe_publication_code_in_the_deployment_the_guarantee_cannot_be_active(hub, monkeypatch):
    monkeypatch.setenv(pub.CONSULTANTS_ENV, "1")
    monkeypatch.setenv(pub.CONSULTANT_CLIENTS_ENV, "Pampa")
    monkeypatch.setitem(sys.modules, "tools.catalog.publication_txn", None)                       # el import falla: este despliegue no trae la publicación segura
    assert pub.publication_guarantees_active() is False and pub.consultants_enabled() is False


def test_a_failing_guarantee_probe_fails_closed(hub, monkeypatch):
    monkeypatch.setenv(pub.CONSULTANTS_ENV, "1")
    monkeypatch.setenv(pub.CONSULTANT_CLIENTS_ENV, "Pampa")
    monkeypatch.setitem(sys.modules, "tools.catalog.publication_txn", types.SimpleNamespace(safe_mode_enabled=lambda: 1 / 0))
    assert pub.consultants_enabled() is False


# ═══════════════ capacidad disponible: consultor full ═══════════════
def test_a_full_consultant_sees_the_plus_and_publishes_on_an_authorized_client_with_their_own_credentials(hub, monkeypatch, runs):
    capability(monkeypatch)
    consultant = _client(CONSULTANT)
    html = ui(consultant)
    assert PLUS in html and 'id="dsp-form"' in html and "data-dsp-allowed-clients" in html and "Pampa, Acme Corp" in html     # visibilidad = la de la captura del dueño
    r = post(consultant)
    assert done(r)["state"] == "SUCCESS" and SECRET not in r.text
    assert runs == [{"client": "Pampa", "model": "AM_Compras", "secret": SECRET, "user": CONSULTANT, "client_id": "sb-propio"}]    # SUS credenciales, atribuidas a SU usuario
    assert "Acceso restringido" not in html


def test_first_publication_and_republication_follow_the_same_gates(hub, monkeypatch, runs):
    capability(monkeypatch)
    consultant = _client(CONSULTANT)
    assert done(post(consultant))["state"] == "SUCCESS"                                            # «primera»
    assert done(post(consultant))["state"] == "SUCCESS"                                            # «republicación» del mismo modelo
    assert len(runs) == 2                                                                          # no hay una distinción CREATE/UPDATE que se pueda demostrar: ambas pasan por las mismas compuertas
    capability(monkeypatch, guarantees=False)
    assert post(consultant).status_code == 403 and len(runs) == 2                                  # y las dos se cierran juntas si se caen las garantías


def test_the_form_shows_no_owner_only_profile_selector_to_a_consultant(hub, monkeypatch):
    capability(monkeypatch)
    monkeypatch.setattr(dprofiles, "source_configured", lambda: True)
    assert "datasphere-profiles" not in ui(_client(CONSULTANT))
    assert "datasphere-profiles" in ui(_client(OWNER))


def test_a_deep_link_does_not_open_the_publish_state_for_someone_without_the_capability(hub, monkeypatch):
    html = _client(CONSULTANT).get(CATALOG_PUBLISH_URL).text
    assert 'id="catalog-publish-state"' not in html and "catalogView = 'publish'" not in html
    capability(monkeypatch)
    assert 'id="catalog-publish-state"' in _client(CONSULTANT).get(CATALOG_PUBLISH_URL).text


# ═══════════════ clientes ajenos ═══════════════
@pytest.mark.parametrize("client_name", ["OtroCliente", "pampa2", "", "*"])
def test_a_consultant_cannot_publish_on_a_client_that_is_not_explicitly_authorized(hub, monkeypatch, runs, client_name):
    capability(monkeypatch)
    d = done(post(_client(CONSULTANT), client=client_name))
    assert d["state"] == "FAILED" and d["error_code"] == "CLIENT_NOT_AUTHORIZED" and not runs


def test_client_names_match_without_regard_to_case_or_repeated_spaces(hub, monkeypatch, runs):
    capability(monkeypatch)
    assert done(post(_client(CONSULTANT), client="  acme   CORP "))["state"] == "SUCCESS" and len(runs) == 1


def test_the_owner_may_publish_on_any_client(hub, monkeypatch, runs):
    capability(monkeypatch)
    assert done(post(_client(OWNER), client="CualquierOtro"))["state"] == "SUCCESS"


def test_a_wildcard_in_the_allowed_clients_grants_nothing(hub, monkeypatch, runs):
    capability(monkeypatch, clients="*")
    assert pub.consultant_clients() == () and post(_client(CONSULTANT)).status_code == 403


# ═══════════════ denegados siempre ═══════════════
def test_read_only_profile_outside_the_allowlist_and_anonymous_are_denied_in_ui_and_backend(hub, monkeypatch, runs):
    capability(monkeypatch)
    stranger = "ajeno@otro.com"
    for email in (READER, stranger):
        c = _client(email)
        assert PLUS not in ui(c) and 'id="dsp-form"' not in ui(c), email
        assert post(c).status_code == 403, email
    anonymous = _client(None)
    assert PLUS not in ui(anonymous) and post(anonymous).status_code == 401
    assert not runs


def test_losing_the_allowlist_takes_effect_on_the_next_request(hub, monkeypatch, runs):
    capability(monkeypatch)
    c = _client(CONSULTANT)
    assert done(post(c))["state"] == "SUCCESS"
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{OTHER}")                         # se lo retira con la sesión viva
    assert post(c).status_code == 403 and PLUS not in ui(c) and len(runs) == 1


def test_a_manual_post_cannot_bypass_what_the_ui_hides(hub, monkeypatch, runs):
    """El control no está en el botón: todas las variantes de POST directo a la ruta protegida con la capacidad apagada o sin permiso."""
    consultant = _client(CONSULTANT)
    assert post(consultant).status_code == 403                                                     # capacidad apagada
    capability(monkeypatch, flag=False)
    assert post(consultant, client="Pampa").status_code == 403
    capability(monkeypatch)
    assert post(_client(READER)).status_code == 403
    assert post(_client(None)).status_code == 401
    other_module = "/modules/01_Arquitectura_y_Conectividad/datasphere-publication/run"
    assert consultant.post(other_module, data=FORM, headers=ORIGIN).status_code == 404            # fuera del módulo que ofrece la herramienta
    assert not runs


# ═══════════════ lo administrativo sigue siendo solo del dueño ═══════════════
def test_shared_client_profiles_stay_owner_only_even_with_the_capability_on(hub, monkeypatch, runs):
    capability(monkeypatch)
    consultant = _client(CONSULTANT)
    r = post(consultant, profile_id="abc123", graph_token="tok")
    assert r.status_code == 403 and not runs                                                       # el secreto de un perfil compartido jamás se resuelve para un consultor
    resolved = []
    monkeypatch.setattr(dprofiles, "resolve", lambda pid, tok: resolved.append(pid) or None)
    assert consultant.post(f"/modules/{STEM}/datasphere-profiles", data={"graph_token": "t"}, headers=ORIGIN).status_code == 403
    assert not resolved
    owner_run = post(_client(OWNER), profile_id="abc123", graph_token="tok")
    assert done(owner_run)["error_code"] == "PROFILE_UNAVAILABLE" and resolved == ["abc123"]       # el dueño sí lo usa


def test_refresh_catalog_stays_owner_only(hub, monkeypatch):
    capability(monkeypatch)
    refresh = f"/modules/{STEM}/datasphere-assessment/refresh-catalog"
    assert _client(CONSULTANT).post(refresh, data={"pack": "datasphere_core"}, headers=ORIGIN).status_code == 403


# ═══════════════ origen, hosts y credenciales ═══════════════
def test_same_origin_is_still_required(hub, monkeypatch, runs):
    capability(monkeypatch)
    c = _client(CONSULTANT)
    assert c.post(RUN_URL, data=FORM, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(RUN_URL, data=FORM).status_code == 403
    assert not runs


def test_host_and_token_url_validation_still_runs_for_a_consultant_before_any_extraction(hub, monkeypatch):
    capability(monkeypatch)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: (_ for _ in ()).throw(AssertionError("el extractor no debe correr con un host inválido")))
    d = done(post(_client(CONSULTANT), host="http://evil.example"))
    assert d["state"] == "INVALID" and d["error_code"] == "INVALID_PARAMETER"


def test_the_client_secret_never_reaches_the_response_or_the_form_context(hub, monkeypatch, runs):
    capability(monkeypatch)
    c = _client(CONSULTANT)
    assert SECRET not in post(c).text and SECRET not in ui(c)
    assert "client_secret" not in asd.FormValues.__dataclass_fields__
