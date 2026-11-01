"""F2.2c — CAPTURA gobernada (opt-in) del inventario de una extracción F2.1 + reconciliación F2.2a sobre capturas.

Documentos del CLI SINTÉTICOS (fixtures con trampas de credenciales) y Spaces FALSO en memoria: sin SAP, sin OAuth, sin red. Cubre retención
solo-dueño y saneada, opt-in con tenant activo + host vinculado, asociación tenant/Space/ambiente/procedencia, aislamiento por cliente,
idempotencia, persistencia aislada, cobertura (Space inaccesible ⇒ sin evidencia, nunca baja), reconciliación y la integración con el run F2.1."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import remote_store
from tools.catalog.connectivity_governance import reconciliation as rc
from webapp.services import connectivity_capture_service as capsvc
from webapp.services import connectivity_capture_store as cstore

from tests.webapp.test_connectivity_registry_admin import FakeSpaces, _client, _hx, _post, VIEW, bucket, owner  # noqa: F401 — fixtures

REPO = Path(__file__).resolve().parents[2]
CONN = REPO / "tests" / "fixtures" / "connectivity_reconciliation" / "Sintetico" / "Conexiones"
TRAPS = ("mrk_user", "mrk_password_value", "mrk_token_value", "MRK_SNC_NAME", "MRK-INTERNAL-ID", "mrk_creator")
HOST = "bio-devqa.eu10.hcs.cloud.sap"


def _raw():
    listing = json.loads((CONN / "conn_space_DEV.json").read_text(encoding="utf-8"))
    details = {("DEV", d["name"]): d for d in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(CONN.glob("conn_space_DEV_*.json")))}
    return listing, details


def _inv(status="COMPLETE", snapshot="s1", at="2026-11-01T10:00:00Z"):
    return {"status": status, "exhaustiveness": "UNVERIFIABLE", "snapshot_id": snapshot, "captured_at": at}


def _hold(op="op1", owner="owner@danwor.com", client="Biogenesis", listings=None, details=None, **inv):
    listing, det = _raw()
    return capsvc.hold(op, owner=owner, client=client, host=HOST.upper(), inventory=_inv(**inv),
                       listings=listings if listings is not None else {"DEV": listing, "QAS": None},
                       details=det if details is None else details)


@pytest.fixture(autouse=True)
def _clean_pending():
    capsvc._PENDING.clear()
    yield
    capsvc._PENDING.clear()


def _setup_registry(owner, envs=(("DEV", "DEV"),), bind=True):
    _post(owner, "register-tenant")
    if bind:
        _post(owner, "bind-host", host=HOST)
    for space, env in envs:
        _post(owner, "assign-space", space=space, environment=env)


def _capture(owner, op="op1", tenant="TNT-BIO-1", **extra):
    return _post(owner, "capture", operation_id=op, tenant_id=tenant, rationale="captura inicial", **extra)


# ═══════════════ retención (antes del opt-in) ═══════════════
def test_hold_keeps_only_the_sanitized_observation_and_never_failed_runs(bucket):
    held = _hold()
    assert held == {"operation_id": "op1", "host": HOST, "spaces": ["DEV", "QAS"], "connections": 3}
    text = json.dumps(capsvc._PENDING["op1"]["observation"])
    assert not any(t in text for t in TRAPS)                                      # allowlist del contrato: sin credenciales
    assert capsvc._PENDING["op1"]["observation"]["coverage"]["inaccessible_spaces"] == ["QAS"]
    assert _hold(op="op2", status="FAILED") is None and "op2" not in capsvc._PENDING
    assert bucket.objects == {}                                                    # retener no escribe nada


def test_pending_captures_expire_and_are_bounded(bucket, monkeypatch):
    clock = [0.0]
    capsvc.hold("old", owner="owner@danwor.com", client="Biogenesis", host=HOST, inventory=_inv(), listings={"DEV": []}, details={},
                now=lambda: clock[0])
    clock[0] = capsvc.PENDING_TTL_S + 1
    monkeypatch.setattr(capsvc.time, "monotonic", lambda: clock[0])
    with pytest.raises(capsvc.CaptureRejected, match="EXPIRED"):
        capsvc.capture("old", actor="owner@danwor.com", tenant_id="X", rationale="r", request_id="req-00000001", now_iso="2026-11-01T10:00:00Z")
    for i in range(capsvc.MAX_PENDING + 5):
        capsvc.hold(f"o{i}", owner="owner@danwor.com", client="Biogenesis", host=HOST, inventory=_inv(), listings={"DEV": []}, details={},
                    now=lambda: clock[0])
    assert len(capsvc._PENDING) == capsvc.MAX_PENDING


# ═══════════════ captura opt-in ═══════════════
def test_capture_requires_owner_active_tenant_and_bound_host(bucket, owner):
    _setup_registry(owner, bind=False)
    _hold()
    assert _post(_client("consultor@danwor.com"), "capture", operation_id="op1", rationale="x").status_code == 403
    r = _capture(owner)
    assert r.status_code == 409 and r.json()["reason"] == "HOST_NOT_BOUND"
    assert _capture(owner, tenant="TNT-NOPE").json()["reason"] == "TENANT_NOT_ACTIVE"
    assert _capture(owner, op="missing").status_code == 410
    assert not [k for k in bucket.objects if "_connectivity-captures" in k]


def test_capture_by_another_owner_session_is_rejected(bucket, owner):
    _setup_registry(owner)
    _hold(owner="otro@danwor.com")
    assert _capture(owner).json()["reason"] == "NOT_YOURS"


def test_capture_associates_tenant_space_environment_and_provenance(bucket, owner):
    _setup_registry(owner)
    _hold()
    r = _capture(owner)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "APPLIED" and body["governed_spaces"] == 1 and body["ungoverned_spaces"] == 1
    keys = [k for k in bucket.objects if "_connectivity-captures" in k]
    assert len(keys) == 1 and keys[0].startswith("governed-decisions/_connectivity-captures/Biogenesis/TNT-BIO-1/")
    env = json.loads(bucket.objects[keys[0]])
    assert env["schema"] == "connectivity-capture/v1" and env["captured_by"] == "owner@danwor.com" and env["host"] == HOST
    assert env["space_environments"] == {"DEV": "DEV", "QAS": None}
    assert env["provenance"]["operation_id"] == "op1" and env["provenance"]["kind"] == "cli_extraction"
    o = env["observation"]
    assert o["provenance"]["tenant_id"] == "TNT-BIO-1" and o["provenance"]["tenant_governed"] is True
    assert {c["key"]["tenant_id"] for c in o["connections"]} == {"TNT-BIO-1"} and {c["environment"] for c in o["connections"]} == {"DEV"}
    assert not any(t in bucket.objects[keys[0]].decode() for t in TRAPS)
    assert "op1" not in capsvc._PENDING                                            # capturada: no queda pendiente


def test_capture_is_idempotent_by_request_and_not_published_in_the_catalog(bucket, owner, tmp_path, monkeypatch):
    _setup_registry(owner)
    _hold()
    first = _capture(owner, request_id="req-capture-01")
    _hold()                                                                        # el mismo pedido reintentado
    again = _capture(owner, request_id="req-capture-01")
    assert first.json()["capture_id"] == again.json()["capture_id"] and again.json()["idempotent"] is True
    assert len([k for k in bucket.objects if "_connectivity-captures" in k]) == 1
    assert all(k.startswith("governed-decisions/") for k in bucket.objects)       # nada fuera del namespace gobernado (ni Catálogo)


def test_captures_are_isolated_per_client(bucket, owner):
    _setup_registry(owner)
    _post(owner, "register-tenant", client="Loma", tenant_id="TNT-LOMA-1")
    _hold(client="Loma")
    r = _capture(owner)                                                            # TNT-BIO-1 es de Biogenesis, la extracción de Loma
    assert r.json()["reason"] == "TENANT_NOT_ACTIVE"
    assert cstore.load_captures("Loma", "TNT-BIO-1") == []


def test_store_fails_closed_on_tampered_capture(bucket, owner):
    _setup_registry(owner)
    _hold()
    _capture(owner)
    key = next(k for k in bucket.objects if "_connectivity-captures" in k)
    env = json.loads(bucket.objects[key])
    env["observation"]["connections"][0]["fields"]["host"]["value"] = "password=mrk_password_value"
    bucket.objects[key] = json.dumps(env).encode()
    with pytest.raises(cstore.HistoryIntegrityError):
        cstore.load_captures("Biogenesis", "TNT-BIO-1")
    html = _hx(owner, VIEW + "&client=Biogenesis&tenant=TNT-BIO-1")
    assert "Capturas no disponibles" in html


# ═══════════════ reconciliación sobre capturas ═══════════════
def test_first_capture_reconciles_against_published_catalog_and_second_against_previous(bucket, owner, monkeypatch):
    from webapp.services import catalog_reads
    monkeypatch.setattr(catalog_reads, "_catalog_architecture_connections", lambda: (None, []))
    _setup_registry(owner, envs=(("DEV", "DEV"), ("QAS", "QAS")))
    _hold(at="2026-11-01T10:00:00Z")
    _capture(owner)
    html = _hx(owner, VIEW + "&client=Biogenesis&tenant=TNT-BIO-1")
    assert "Catálogo publicado (procedencia limitada)" in html and "data-registry-reconciliation" in html

    listing, det = _raw()
    gone = listing[0]["name"]
    capsvc.hold("op2", owner="owner@danwor.com", client="Biogenesis", host=HOST, inventory=_inv(snapshot="s2", at="2026-11-02T10:00:00Z"),
                listings={"DEV": listing[1:], "QAS": None}, details={k: v for k, v in det.items() if k[1] != gone})
    assert _capture(owner, op="op2").json()["status"] == "APPLIED"
    state = __import__("webapp.services.connectivity_registry_store", fromlist=["x"]).load_state()
    view = capsvc.captures_view(state, "Biogenesis", "TNT-BIO-1")
    r = view["reconciliation"]
    assert r["baseline_label"] == "captura anterior" and len(view["captures"]) == 2
    item = next(i for i in r["items"] if i["key"]["connection_name"] == gone)
    assert item["outcome"] == rc.ONLY_IN_BASELINE and item["absence"]["is_deletion_evidence"] is False
    assert item["absence"]["review_priority"] == "NORMAL"
    assert all(i["outcome"] != rc.ONLY_IN_BASELINE for i in r["items"] if i["key"]["space"] == "QAS")   # QAS inaccesible: sin falsas bajas
    html = _hx(owner, VIEW + "&client=Biogenesis&tenant=TNT-BIO-1")
    assert "Solo en la base" in html and "prioridad de revisión NORMAL" in html and "no es una baja" in html


def test_unassigned_space_is_reported_without_scope_not_as_change(bucket, owner, monkeypatch):
    from webapp.services import catalog_reads
    monkeypatch.setattr(catalog_reads, "_catalog_architecture_connections", lambda: (None, []))
    _setup_registry(owner, envs=())                                                 # DEV sin ambiente gobernado
    _hold()
    _capture(owner)
    state = __import__("webapp.services.connectivity_registry_store", fromlist=["x"]).load_state()
    r = capsvc.captures_view(state, "Biogenesis", "TNT-BIO-1")["reconciliation"]
    assert {i["outcome"] for i in r["items"]} == {rc.SCOPE_UNRESOLVED}


# ═══════════════ integración con el run F2.1 ═══════════════
def test_f21_run_holds_for_owner_only_and_done_offers_capture(bucket, owner, monkeypatch):
    from webapp.services import connectivity_inventory_service as svc
    listing, det = _raw()

    def fake_run(values, secret, *, user_email="", on_phase=None, on_line=None, sleep=None, capture_for=""):
        held = capsvc.hold("opF", owner=capture_for, client=values.client, host="bio-devqa.eu10.hcs.cloud.sap", inventory=_inv(),
                           listings={"DEV": listing}, details=det) if capture_for else None
        return svc.ExtractionOutcome("SUCCESS", operation_id="opF", summary={"inventory": {"status": "COMPLETE"}}, capture=held)
    monkeypatch.setattr(svc, "run", fake_run)
    monkeypatch.setattr(svc, "tool_enabled", lambda stem: True)
    _setup_registry(owner)
    form = {"host": "https://bio-devqa.eu10.hcs.cloud.sap", "token_url": "https://x.authentication.eu10.hana.ondemand.com/oauth/token",
            "datasphere_client_id": "cid", "datasphere_client_secret": "s", "client": "Biogenesis", "space_id": ["DEV"]}
    url = "/modules/01_Arquitectura_y_Conectividad/connectivity-inventory/run"
    done = [json.loads(l) for l in owner.post(url, data=form, headers={"origin": "http://testserver"}).text.splitlines() if l.strip()][-1]
    assert done["capture"]["expires_in_s"] == 1800 and done["capture"]["suggested"] == "TNT-BIO-1" and done["capture"]["tenants"] == [{"tenant_id": "TNT-BIO-1", "display_name": ""}]
    assert "host" not in json.dumps(done["capture"]).lower().replace("ghost", "")
    capsvc._PENDING.clear()
    c = _client("consultor@danwor.com")
    done = [json.loads(l) for l in c.post(url, data=form, headers={"origin": "http://testserver"}).text.splitlines() if l.strip()][-1]
    assert "capture" not in done and capsvc._PENDING == {}


def test_observed_source_records_documents_only_when_requested():
    from webapp.services import connectivity_inventory_service as svc
    p = svc._Progress(["DEV"], record=False)
    assert p.listings is None and p.details is None
    p = svc._Progress(["DEV"], record=True)
    assert p.listings == {} and p.details == {}


def test_capture_offer_reports_its_30_minute_expiry():
    assert capsvc.PENDING_TTL_S == 30 * 60
    src = (REPO / "webapp" / "routers" / "connectivity_inventory.py").read_text(encoding="utf-8")
    assert '"expires_in_s": capsvc.PENDING_TTL_S' in src
    js = (REPO / "webapp" / "static" / "connectivity_inventory.js").read_text(encoding="utf-8")
    assert "Disponible para capturar durante " in js and "data-dsc-capture-expiry" in js
    tpl = (REPO / "webapp" / "templates" / "partials" / "connectivity_inventory_tool.html").read_text(encoding="utf-8")
    assert "data-dsc-capture-expiry" in tpl
