"""
test_copilot_operations.py — continuidad de ejecuciones del Copiloto (registro compartido + aviso global + «Ver ejecución»).

Contrato: diagnóstico y publicación siguen corriendo como siempre (hilo del servidor); el registro en memoria es la ÚNICA fuente de estados,
acotada al usuario autenticado, sin credenciales; un timeout del monitor no cancela nada ni afirma resultados; volver a la pantalla no
duplica publicaciones. Sin publicaciones reales (el servicio de publicación se sustituye) ni escrituras en Spaces.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from webapp import access, main
from webapp.routers import datasphere_publication as pub_router
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as asd
from webapp.services import datasphere_publication_service as pubsvc
from webapp.services import operation_registry as reg

STEM = "04_Modelado_y_Desarrollo"
RUN_URL = f"/modules/{STEM}/datasphere-publication/run"
ORIGIN = {"Origin": "http://testserver"}
SECRET = "s3cr3t-NO-DEBE-APARECER"
FORM = {"pack": "datasphere_core", "host": "https://acme.eu10.hcs.cloud.sap",
        "token_url": "https://acme.authentication.eu10.hana.ondemand.com/oauth/token",
        "datasphere_client_id": "sb-client-id", "datasphere_client_secret": SECRET, "space": "CALIDAD",
        "datasphere_root_object": "AM_ACME", "client": "Acme"}


def _client(email):
    c = TestClient(main.app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(pubsvc, "client_authorized", lambda request, client: True)
    reg._OPERATIONS.clear()
    return _client("owner@danwor.com")


def _fake_publication(monkeypatch, gate: threading.Event | None = None, state="SUCCESS"):
    calls = {"runs": 0, "secrets": []}

    def run(values, secret, *, user_email="", on_line=None, on_phase=None, replace_token=""):
        calls["runs"] += 1
        calls["secrets"].append(bool(secret))
        on_line("PUBLICACION TRANSACCIONAL (simulada)")
        if gate is not None:
            assert gate.wait(10)
        on_line("fin de la simulación")
        return asd.Outcome(state, None if state == "SUCCESS" else "EXTRACTION_FAILED", "Publicado." if state == "SUCCESS" else "Falló la extracción.",
                           operation_id="op-test", title="" if state == "SUCCESS" else "No se pudo publicar")
    monkeypatch.setattr(pubsvc, "run", run)
    return calls


def _ops(c):
    r = c.get("/copilot/operations")
    assert r.status_code == 200
    return r.json()["operations"]


# ── registro compartido ─────────────────────────────────────────────────────────────────────────────────────────────
def test_registry_keeps_only_safe_metadata_and_isolates_owners():
    reg._OPERATIONS.clear()
    rid = reg.start_operation("A@danwor.com", meta={"kind": "publication", "client": "Acme", "client_secret": SECRET, "token": "x"})
    reg.publish_event(rid, {"t": "line", "v": "hola"})
    assert reg._OPERATIONS[rid].meta == {"kind": "publication", "client": "Acme"}            # whitelist: ninguna credencial entra
    assert [o["run_id"] for o in reg.list_operations("a@danwor.com")] == [rid] and reg.list_operations("b@danwor.com") == []
    assert reg.operation_detail(rid, "b@danwor.com") is None and reg.operation_events(rid, "b@danwor.com") is None
    assert reg.operation_detail(rid, "a@danwor.com")["log_lines"] == ["hola"]


def test_monitor_timeout_never_claims_a_result_and_the_real_outcome_replaces_it(monkeypatch):
    reg._OPERATIONS.clear()
    rid = reg.start_operation("a@danwor.com", meta={"kind": "publication"}, max_s=0.01,
                              timeout_event=pub_router.PUBLICATION_MONITOR_TIMEOUT, accept_late_done=True)
    time.sleep(0.05)
    op = reg.list_operations("a@danwor.com")[0]
    assert op["state"] == "UNKNOWN" and op["error_code"] == "MONITOR_TIMEOUT"
    assert "No se canceló nada" in op["message"] and "rollback" not in op["message"].lower()   # no afirma rollback ni éxito
    reg.publish_event(rid, {"t": "done", "state": "SUCCESS", "result_url": "/modules/x?tab=catalogo"})   # el proceso terminó después
    assert reg.list_operations("a@danwor.com")[0]["state"] == "SUCCESS"


def test_assessment_keeps_its_registry_names_and_timeout_semantics():
    reg._OPERATIONS.clear()
    assert asd._OPERATIONS is reg._OPERATIONS and asd.operation_events is reg.operation_events
    rid = asd.start_operation("a@danwor.com", meta={"stem": STEM, "client": "Acme"})
    assert reg._OPERATIONS[rid].meta["kind"] == "assessment"
    assert reg._OPERATIONS[rid].timeout_event["error_code"] == "OPERATION_TIMEOUT" and not reg._OPERATIONS[rid].accept_late_done


# ── rutas ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_operations_require_a_session_and_are_scoped_to_the_user(owner):
    assert _client(None).get("/copilot/operations").status_code == 401
    rid = reg.start_operation("otro@danwor.com", meta={"kind": "assessment", "stem": STEM})
    assert _ops(owner) == []                                                                  # la ajena no se lista
    assert owner.get(f"/copilot/operations/{rid}").status_code == 404                         # ni se consulta
    assert owner.get(f"/copilot/operations/{rid}/log").status_code == 404                     # ni se ve su log


def test_publication_gets_a_run_id_is_recoverable_and_finishes_with_its_real_result(owner, monkeypatch):
    calls = _fake_publication(monkeypatch)
    r = owner.post(RUN_URL, data=FORM, headers=ORIGIN)
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert events[0]["t"] == "start" and len(events[0]["run_id"]) == 32
    assert events[-1]["t"] == "done" and events[-1]["state"] == "SUCCESS"
    op = _ops(owner)[0]
    assert op["run_id"] == events[0]["run_id"] and op["kind"] == "publication" and op["state"] == "SUCCESS"
    assert op["result_url"].endswith("tab=catalogo") and op["client"] == "Acme" and op["space"] == "CALIDAD"
    assert SECRET not in json.dumps(_ops(owner)) and SECRET not in owner.get(op["log_url"]).text   # nunca credenciales
    assert calls == {"runs": 1, "secrets": [True]}                                            # una sola publicación


def test_navigating_away_does_not_stop_the_publication_and_it_is_recovered_running(owner, monkeypatch):
    gate = threading.Event()
    calls = _fake_publication(monkeypatch, gate=gate)
    t = threading.Thread(target=lambda: owner.post(RUN_URL, data=FORM, headers=ORIGIN), daemon=True)
    t.start()                                                                                 # el usuario «se va»: otra página consulta el registro
    deadline = time.monotonic() + 5
    while not _ops(owner):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    op = _ops(owner)[0]
    assert op["state"] == "RUNNING" and not op["finished"]
    page = BeautifulSoup(owner.get(op["log_url"]).text, "html.parser")                        # «Ver ejecución» en curso: sigue en vivo
    live = page.select_one("[data-op-live-url]")
    assert live is not None and live["data-op-live-url"] == f"/copilot/operations/{op['run_id']}"
    assert "PUBLICACION TRANSACCIONAL (simulada)" in page.select_one("[data-op-log]").get_text()
    gate.set()
    t.join(5)
    done = _ops(owner)[0]
    assert done["state"] == "SUCCESS" and calls["runs"] == 1                                  # terminó sola, sin reintentos
    page = BeautifulSoup(owner.get(done["log_url"]).text, "html.parser")
    assert page.select_one("[data-op-live-url]") is None and not page.select_one("[data-op-success]").has_attr("hidden")


def test_a_failed_publication_shows_its_real_result_and_technical_detail(owner, monkeypatch):
    _fake_publication(monkeypatch, state="FAILED")
    owner.post(RUN_URL, data=FORM, headers=ORIGIN)
    op = _ops(owner)[0]
    assert op["state"] == "FAILED" and op["title"] == "No se pudo publicar" and op["result_url"] is None
    page = BeautifulSoup(owner.get(op["log_url"]).text, "html.parser")
    failed = page.select_one("[data-op-failed]")
    assert not failed.has_attr("hidden") and "No se pudo publicar" in failed.get_text()
    assert "fin de la simulación" in page.select_one("[data-op-log]").get_text()


def test_returning_to_the_screen_never_starts_another_publication(owner, monkeypatch):
    calls = _fake_publication(monkeypatch)
    owner.post(RUN_URL, data=FORM, headers=ORIGIN)
    owner.get(f"/modules/{STEM}?pack=datasphere_core&tab=catalogo&catalog_action=publish")   # volver a la pantalla
    _ops(owner)
    assert calls["runs"] == 1


def test_the_copilot_page_has_the_global_notice_and_loads_its_script(owner):
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core").text
    soup = BeautifulSoup(html, "html.parser")
    notice = soup.select_one("[data-copilot-operations]")
    assert notice is not None and notice.has_attr("hidden") and "copilot_operations.js" in html
