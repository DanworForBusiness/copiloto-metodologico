"""
test_datasphere_assessment_long_run.py — la extracción ad hoc es una operación del SERVIDOR, independiente de la conexión HTTP.

Causa raíz reproducida: el proxy de App Platform corta una conexión sin bytes (≈100 s) y una lectura larga del CLI (un Replication Flow
grande) dejaba el stream NDJSON en silencio; el navegador declaraba CONNECTION_INTERRUPTED y el resultado —aunque la extracción siguiera—
nunca llegaba al informe. Contratos:
  * latido `{"t":"ping"}` cada HEARTBEAT_S mientras no hay salida: la conexión nunca queda inactiva;
  * un corte REAL de la conexión (socket cerrado) no detiene ni reinicia la extracción: GET …/operation/{run_id}?since=N devuelve el avance
    restante y el `done` con el informe; una sola extracción;
  * la operación es del usuario que la inició (otro: 404) y solo expone los eventos saneados del stream (sin secreto);
  * evaluar != publicar: ni Catálogo ni Spaces (lo vigila `fake_export`).
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.parse

import pytest
import uvicorn

import webapp.main as main
from tests.webapp import test_datasphere_assessment as ds
from tests.webapp.test_datasphere_assessment import fake_export, owner  # noqa: F401 — fixtures
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from webapp import access
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as svc

OPERATION = f"/modules/{ds.STEM}/datasphere-assessment/operation/"


def _slow_export(monkeypatch, gate: threading.Event | None = None, delay: float = 0.0):
    """La extracción (fake real de `fake_export`) emite una línea y después queda en SILENCIO —como la lectura de un RF grande— hasta `gate`."""
    fake = dsp.LineageExporter.run

    def slow(self):
        dsp._say("[RF 3/3] RF_Billing")
        if gate is not None:
            assert gate.wait(10), "la prueba no liberó la extracción"
        time.sleep(delay)
        return fake(self)
    monkeypatch.setattr(dsp.LineageExporter, "run", slow)


def _poll(client, run_id: str, since: int = 0, timeout: float = 10.0) -> list[dict]:
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"{OPERATION}{run_id}?since={since}")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        data = r.json()
        events.extend(data["events"])
        since = data["next"]
        if data["finished"]:
            return events
        time.sleep(0.05)
    raise AssertionError("la operación no terminó")


# ── latido: la conexión no queda inactiva durante una lectura larga ──────────────────────────────────────────────────
def test_silent_extraction_keeps_the_stream_alive_with_heartbeats(owner, fake_export, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "HEARTBEAT_S", 0.05)
    _slow_export(monkeypatch, delay=0.4)
    r = ds._post(owner)
    events = ds._events(r)
    assert events[0]["t"] == "start" and len(events[0]["run_id"]) == 32
    assert sum(1 for e in events if e["t"] == "ping") >= 3                           # latidos durante el silencio
    done = ds._done(r)
    assert done["state"] == "SUCCESS" and done["result_url"]
    assert ds.SECRET not in r.text


# ── corte real de la conexión → recuperación sin reiniciar ──────────────────────────────────────────────────────────
@pytest.fixture()
def live_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn no arrancó"
        time.sleep(0.02)
    yield port
    server.should_exit = True
    thread.join(5)


def test_connection_drop_does_not_stop_the_extraction_and_the_result_is_recovered(owner, fake_export, monkeypatch, live_server):  # noqa: F811
    gate = threading.Event()
    _slow_export(monkeypatch, gate=gate)
    port = live_server
    cookie = owner.cookies.get(auth_service.COOKIE_NAME)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", ds.RUN, body=urllib.parse.urlencode(ds.VALID),
                 headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://127.0.0.1:{port}",
                          "Cookie": f"{auth_service.COOKIE_NAME}={cookie}"})
    resp = conn.getresponse()
    assert resp.status == 200
    start = json.loads(resp.readline())
    assert start["t"] == "start"
    run_id = start["run_id"]
    shown = 0                                                                        # eventos que el navegador ya mostró
    while True:                                                                      # hasta la línea previa al silencio
        event = json.loads(resp.readline())
        if event["t"] == "ping":
            continue
        shown += 1
        if event == {"t": "line", "v": "[RF 3/3] RF_Billing"}:
            break
    conn.sock.shutdown(socket.SHUT_RDWR)                                             # el proxy corta la conexión a mitad de la extracción
    conn.close()

    gate.set()                                                                       # la extracción sigue y termina en el servidor
    events = _poll(owner, run_id, since=shown)
    done = events[-1]
    assert done["t"] == "done" and done["state"] == "SUCCESS" and done["result_url"]
    assert not any(e.get("v") == "[RF 3/3] RF_Billing" for e in events)              # no repite lo ya mostrado
    assert fake_export["exports"] == 1                                               # nunca se reinicia la extracción
    report = owner.get(done["result_url"])
    assert report.status_code == 200 and "Adopción metodológica provisional" in report.text   # informe con su Juicio provisional
    assert ds.SECRET not in json.dumps(events)
    assert not fake_export["canonical"].exists() or not any(fake_export["canonical"].rglob("*.json"))      # evaluar != publicar


def test_recovery_replays_everything_from_the_start(owner, fake_export):  # noqa: F811
    r = ds._post(owner)
    run_id = ds._events(r)[0]["run_id"]
    streamed = [e for e in ds._events(r) if e["t"] not in ("start", "ping")]
    assert _poll(owner, run_id) == streamed                                          # mismos eventos, mismo orden


# ── propiedad y vencimiento ────────────────────────────────────────────────────────────────────────────────────────
def test_operation_belongs_to_its_owner(owner, fake_export, monkeypatch):  # noqa: F811
    r = ds._post(owner)
    run_id = ds._events(r)[0]["run_id"]
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    other = ds._client("otro@danwor.com")
    assert other.get(f"{OPERATION}{run_id}").status_code in (403, 404)
    assert svc.operation_events(run_id, "otro@danwor.com") is None
    assert owner.get(f"{OPERATION}{'0' * 32}").status_code == 404
    assert owner.get(f"{OPERATION}{run_id}?since=-1").status_code == 422


def test_operations_expire_with_the_result(monkeypatch):
    old = svc.start_operation("a@danwor.com", now=time.time() - svc.OPERATION_TTL_S - 1)
    assert svc.operation_events(old, "a@danwor.com") is None                        # vencida
    svc.start_operation("a@danwor.com")
    assert old not in svc._OPERATIONS                                                 # se purga al iniciar otra


def test_line_cap_never_drops_the_done(monkeypatch):
    monkeypatch.setattr(svc, "_OPERATION_MAX_EVENTS", 3)
    run_id = svc.start_operation("a@danwor.com")
    for i in range(10):
        svc.publish_event(run_id, {"t": "line", "v": str(i)})
    svc.publish_event(run_id, {"t": "done", "state": "SUCCESS"})
    svc.publish_event(run_id, {"t": "line", "v": "tarde"})                           # nada después del done
    data = svc.operation_events(run_id, "a@danwor.com")
    assert [e.get("v") for e in data["events"][:3]] == ["0", "1", "2"]
    assert data["events"][-1] == {"t": "done", "state": "SUCCESS"} and data["finished"] and len(data["events"]) == 4


def test_unexpected_worker_failure_still_closes_the_operation(owner, fake_export, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise RuntimeError("inesperado")
    monkeypatch.setattr(svc, "run", boom)
    r = ds._post(owner)
    done = ds._done(r)
    assert done["state"] == "FAILED" and done["error_code"] == "EXTRACTION_FAILED" and done["result_url"] is None


# ── navegador: recuperación real de datasphere_assessment.js (harness de Node) ──────────────────────────────────────
def test_browser_recovers_the_operation_after_a_cut():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no disponible")
    harness = Path(__file__).with_name("datasphere_assessment_recovery.node.js")
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("OK"), proc.stdout + proc.stderr
