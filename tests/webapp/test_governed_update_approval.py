"""PR-2 — actualización gobernada desde el Hub: contexto de conexiones por Space, huella de evidencia (identidad ≠ equivalencia), acreditación de
procedencia (manifiesto o evidencia reconstruida), y aprobación en fases separadas (autorización → verificación previa → ejecución) con estados
persistidos, un solo uso, idempotencia y recuperación tras reinicio. Sin escrituras reales: la vía asegurada se sustituye en las pruebas de rutas."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import connection_catalog as cc
from webapp.services import governed_evidence as ge
from webapp.services import governed_update_approval as gua
from webapp.services import governed_update_requests as gur
from tests.webapp.test_governed_update_requests import (  # noqa: F401  (fixtures y helpers compartidos)
    CAND_PROFILE, ORIGIN, RID, STEM, _client, _create, routes, world)


# ── contexto de conexiones por Space ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _conexiones(root: Path, client="Acme", **spaces):
    d = root / client / "Conexiones"
    d.mkdir(parents=True)
    for sp, conns in spaces.items():
        (d / f"conn_space_{sp}.json").write_text(json.dumps([{"name": n, "typeId": t} for n, t in conns.items()]), encoding="utf-8")
        for n, t in conns.items():
            (d / f"conn_space_{sp}_{n}.json").write_text(json.dumps({"name": n, "typeId": t, "space_id": "GUID"}), encoding="utf-8")
    return root


def test_connection_context_reads_only_the_declared_spaces(tmp_path):
    root = _conexiones(tmp_path, DEV={"SSFF": "SAPSF", "S4": "SAPS4HANA"}, PRD={"SSFF": "SAPSF"}, DEV_EU={"X": "ODATA"})
    assert cc.spaces_of(root, "Acme") == ["DEV", "DEV_EU", "PRD"]
    assert cc.connection_types(root, "Acme") == {"SSFF": "SAPSF", "S4": "SAPS4HANA", "X": "ODATA"}            # sin contexto: como siempre
    assert cc.connection_types(root, "Acme", spaces=["PRD"]) == {"SSFF": "SAPSF"}                                # S4 vive solo en DEV: no se ve
    assert cc.connection_types(root, "Acme", spaces=["DEV"]) == {"SSFF": "SAPSF", "S4": "SAPS4HANA"}             # «DEV_EU» no se confunde con «DEV»
    assert cc.connection_types(root, "Acme", spaces=["NOPE"]) == {}
    assert {(r["space"], r["name"]) for r in cc.connection_records(root, "Acme")} == {("DEV", "SSFF"), ("DEV", "S4"), ("PRD", "SSFF"), ("DEV_EU", "X")}


def test_engine_receives_the_connection_context_only_inside_the_scope(monkeypatch):
    from webapp.services import assessment_service as svc
    seen = []
    monkeypatch.setattr(svc.ma_shadow, "assess", lambda *a, **kw: seen.append(kw.get("connection_spaces")) or ({}, {}))
    monkeypatch.setattr(svc, "_governed_for", lambda root, client: {})
    svc._engine_assess(Path("."), "Acme", "AM", "D")
    with svc.connection_spaces(["PRD", "DEV"]):
        svc._engine_assess(Path("."), "Acme", "AM", "D")
    svc._engine_assess(Path("."), "Acme", "AM", "D")
    assert seen == [None, ("DEV", "PRD"), None]                                     # sin restricción global; el contexto no se filtra fuera del bloque


# ── huella de evidencia ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _profile(contract="GOVERNED_SAP_APPLICATION_API", closure=("AM_X", "1TR_A"), definition="texto", order=False, extra_scope=None):
    leaves = [{"leaf": "1TR_A", "leaf_kind": "REMOTE_IMPORTED", "contract_class": contract, "remote_connection": "SSFF", "connection_type": "SAPSF",
               "remote_entities": ["EmpJob"], "source_systems": [], "definition": definition},
              {"leaf": "1TR_B", "leaf_kind": "REMOTE_IMPORTED", "contract_class": contract, "remote_connection": "SSFF", "connection_type": "SAPSF",
               "remote_entities": ["PerPerson"], "source_systems": []}]
    if order:
        leaves.reverse()
    return {"scope": {"client": "Acme", "terminal_analytic_model": "AM_X", "root_object": "AM_X", "root_type": "analytic-model",
                      "closure_artifacts": list(reversed(closure)) if order else list(closure), "pool_signature": "p-" + definition,
                      "evidence_scope": {"effective_features": ["views", "tables"]}, **(extra_scope or {})},
            "canonical_evidence": {"version": "canonical-evidence/v6", "source_contract_classes": {"leaves": leaves}}}


def test_fingerprint_separates_identity_from_equivalence_and_ignores_irrelevant_differences():
    a, b = ge.fingerprint(_profile()), ge.fingerprint(_profile(definition="otro texto", order=True))
    assert a["identity_digest"] == b["identity_digest"] and a["equivalence_digest"] == b["equivalence_digest"]   # orden, textos y firma de pool no cuentan
    c = ge.fingerprint(_profile(contract="EXTERNAL_CONTRACT_UNDETERMINED"))
    assert c["identity_digest"] == a["identity_digest"] and c["equivalence_digest"] != a["equivalence_digest"]   # cambia un contrato: no equivalentes
    d = ge.fingerprint(_profile(closure=("AM_X", "1TR_A", "2VD_NEW")))
    assert d["equivalence_digest"] != a["equivalence_digest"]                                                    # cambia el alcance semántico
    assert ge.equivalence_diff(_profile(), _profile(contract="X")) == ["contratos de fuente en 2 hoja(s): 1TR_A, 1TR_B"]


# ── procedencia ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _verify(tmp_path, *, declared=("DEV",), adhoc_space="DEV", adhoc_fp=None, records=None, manifest=None, other_manifest=None, pool_files=None):
    catalog = tmp_path / "catalog"
    pool = tmp_path / "pool"
    for space, objs in ((manifest or {}).items()):
        mf = catalog / ".lineage_runs" / "Acme" / space / "analytic-model" / "AM_X" / "lineage-manifest.json"
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"space": space, "objects": objs}), encoding="utf-8")
    for space, objs in ((other_manifest or {}).items()):
        mf = catalog / ".lineage_runs" / "Acme" / space / "analytic-model" / "AM_OTHER" / "lineage-manifest.json"
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"space": space, "objects": objs}), encoding="utf-8")
    for rel, content in (pool_files or {}).items():
        f = pool / "Acme" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    recs = records if records is not None else [{"space": "DEV", "name": "SSFF", "typeId": "SAPSF"}, {"space": "PRD", "name": "SSFF", "typeId": "SAPSF"}]
    return ge.verify_provenance(client_dir="Acme", am="AM_X", declared_spaces=declared, governed_profile=_profile(),
                                adhoc_fingerprint=adhoc_fp if adhoc_fp is not None else ge.fingerprint(_profile()), adhoc_space=adhoc_space,
                                connection_records=recs, catalog_dir=catalog, pool_root=pool)


def test_provenance_accredited_by_reconstructed_evidence_without_any_manifest(tmp_path):
    out = _verify(tmp_path)
    assert out["state"] == ge.ACCREDITED and out["route"] == ge.ROUTE_RECONSTRUCTED and not out["reasons"]       # el manifiesto NO es requisito


def test_provenance_accredited_by_existing_manifest(tmp_path):
    objs = [{"object_type": "analytic-models", "technical_name": "AM_X"}, {"object_type": "remote-tables", "technical_name": "1TR_A"}]
    out = _verify(tmp_path, adhoc_space="PRD", declared=("DEV",), manifest={"DEV": objs})
    assert out["state"] == ge.ACCREDITED and out["route"] == ge.ROUTE_MANIFEST


@pytest.mark.parametrize("kw,reason", [
    ({"declared": ()}, "NO_SPACE_DECLARED"),
    ({"declared": ("QA",)}, "UNKNOWN_SPACE:QA"),
    ({"records": [{"space": "PRD", "name": "SSFF", "typeId": "SAPSF"}, {"space": "DEV", "name": "OTRA", "typeId": "X"}]}, "CONNECTION_OUTSIDE_AUTHORIZED:SSFF@PRD"),
    ({"records": [{"space": "DEV", "name": "SSFF", "typeId": "ODATA"}]}, "CONNECTION_TYPE_MISMATCH:SSFF"),
    ({"adhoc_fp": {}}, "NO_ADHOC_FINGERPRINT"),
    ({"adhoc_space": "PRD"}, "ORIGIN_NOT_ACCREDITED"),
])
def test_provenance_abstains_on_each_inconsistency(tmp_path, kw, reason):
    out = _verify(tmp_path, **kw)
    assert out["state"] == ge.NOT_ACCREDITED and reason in out["reasons"]


def test_provenance_abstains_when_the_evidence_is_not_equivalent(tmp_path):
    out = _verify(tmp_path, adhoc_fp=ge.fingerprint(_profile(contract="EXTERNAL_CONTRACT_UNDETERMINED")))
    assert out["state"] == ge.NOT_ACCREDITED and "EVIDENCE_NOT_EQUIVALENT" in out["reasons"]


def test_provenance_detects_content_published_from_another_space(tmp_path):
    import hashlib
    content = '{"definitions": {"1TR_A": {}}}'
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    out = _verify(tmp_path, pool_files={"remote-tables/1TR_A.json": content},
                  other_manifest={"PRD": [{"object_type": "remote-tables", "technical_name": "1TR_A", "sha256": sha}]})
    assert out["state"] == ge.NOT_ACCREDITED and "OBJECTS_FROM_OTHER_SPACE:1" in out["reasons"]
    legacy = _verify(tmp_path / "x", other_manifest={"PRD": [{"object_type": "remote-tables", "technical_name": "1TR_A"}]})   # sin hash: no prueba origen
    assert legacy["state"] == ge.ACCREDITED and legacy["checks"]["other_space_claims"]["unverifiable"] == ["1TR_A"]


def test_provenance_digest_is_stable_and_binds_the_declaration(tmp_path):
    a, b = _verify(tmp_path / "a"), _verify(tmp_path / "b")
    assert a["digest"] == b["digest"]
    assert _verify(tmp_path / "c", declared=("DEV", "PRD"))["digest"] != a["digest"]


# ── aprobación: fases, estados, un solo uso ──────────────────────────────────────────────────────────────────────────────────────────────────
READY = {"state": gur.PREVIEW_READY, "spaces": ["DEV"], "plan_digest": "p" * 64, "path": "MIGRATION:evidence_source_application_api_v6",
         "governed_sha256": "a" * 64, "dependency_signature": "dep1", "fingerprint": {"equivalence_digest": "e1"},
         "provenance": {"state": "ACCREDITED", "route": "RECONSTRUCTED", "digest": "v1"}, "judgment": {"to": "BUENA"}}


def _ready_request(world):
    rec, _ = gur.create(client_dir="Contreras", client_display="Contreras", terminal_am="AM_Workforce", space="DEV", result_id=RID,
                        candidate_judgment={"status": "DETERMINED", "level": "BUENA"}, candidate_profile=CAND_PROFILE, requested_by="c@danwor.com")
    with gur._lock:
        fresh = gur.load(rec["request_id"])
        fresh["preview"] = dict(READY)
        gur._write(fresh)
    return gur.load(rec["request_id"])


OWNER = {"sub": "owner@danwor.com", "email": "owner@danwor.com"}


def _wait(rid, states, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = (gur.load(rid).get("approval") or {}).get("state")
        if st in states:
            return st
        time.sleep(0.02)
    return (gur.load(rid).get("approval") or {}).get("state")


def test_register_requires_an_accredited_ready_preview_for_the_declared_spaces(world):
    rec = _ready_request(world)
    with pytest.raises(gua.ApprovalRefused) as e:
        gua.register(rec, OWNER, "APP_OWNER", ["PRD"], idempotency_key="k")
    assert e.value.code == "PREVIEW_NOT_FOR_DECLARED_SPACES"
    appr, created = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    assert created and appr["state"] == gua.REGISTERED and appr["schema"] == "hub-human-approval/v1"
    assert appr["expected"] == {"plan_digest": "p" * 64, "provenance_digest": "v1", "equivalence_digest": "e1", "envelope_sha256": "a" * 64,
                                "dependency_signature": "dep1"}


def test_register_is_idempotent_and_allows_a_single_active_approval(world):
    rec = _ready_request(world)
    a, created = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    b, created_b = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")              # reintento del mismo pedido
    c, created_c = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="otra")           # otro pedido con una activa
    assert created and not created_b and not created_c and a["approval_id"] == b["approval_id"] == c["approval_id"]


def test_precheck_denies_without_consuming_when_anything_changed(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    executed = []
    changed = {**READY, "plan_digest": "q" * 64}
    gua.start(rec, appr, compute=lambda r, spaces=None: changed, execute=lambda *a: executed.append(1) or {"outcome": "APPLIED"})
    assert _wait(rec["request_id"], {gua.DENIED}) == gua.DENIED and executed == []
    stored = gur.load(rec["request_id"])["approval"]
    assert stored["reason"] == "PLAN_CHANGED" and gua.STARTED not in [h["state"] for h in stored["history"]]      # nunca llegó a consumirse
    again, created = gua.register(gur.load(rec["request_id"]), OWNER, "APP_OWNER", ["DEV"], idempotency_key="k2")
    assert created and again["approval_id"] != appr["approval_id"]                                       # se puede volver a aprobar


@pytest.mark.parametrize("field,reason", [("provenance", "PROVENANCE_CHANGED"), ("fingerprint", "EVIDENCE_CHANGED"),
                                          ("governed_sha256", "OBSOLETE"), ("dependency_signature", "DEPENDENCIES_CHANGED")])
def test_precheck_names_each_kind_of_change(world, field, reason):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    changed = dict(READY)
    changed[field] = ({"state": "ACCREDITED", "digest": "v2"} if field == "provenance" else {"equivalence_digest": "e2"} if field == "fingerprint" else "zzz")
    gua.start(rec, appr, compute=lambda r, spaces=None: changed, execute=lambda *a: {"outcome": "APPLIED"})
    _wait(rec["request_id"], {gua.DENIED})
    assert reason in gur.load(rec["request_id"])["approval"]["reasons"]


def test_started_is_persisted_before_executing_and_applied_after(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    seen_state = []

    def execute(fresh, a, path):
        seen_state.append(gur.load(rec["request_id"])["approval"]["state"])                     # al invocar la vía asegurada ya está STARTED
        return {"outcome": "APPLIED", "applied": True, "writes": 1}
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY), execute=execute)
    assert _wait(rec["request_id"], {gua.APPLIED}) == gua.APPLIED and seen_state == [gua.STARTED]
    stored = gur.load(rec["request_id"])
    assert stored["status"] == "APPLIED" and [h["state"] for h in stored["approval"]["history"]] == ["REGISTERED", "VERIFYING", "STARTED", "APPLIED"]


@pytest.mark.parametrize("report,state", [({"outcome": "STOPPED_AT_WAVE"}, gua.ABSTAINED), ({"outcome": "DENIED"}, gua.ABSTAINED),
                                          ({"outcome": "REMOTE_REQUIRED"}, gua.ABSTAINED), ({"outcome": "WHATEVER"}, gua.FAILED)])
def test_secured_outcomes_map_to_abstained_or_failed(world, report, state):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY), execute=lambda *a: report)
    assert _wait(rec["request_id"], {state}) == state


def test_an_execution_exception_after_start_is_failed_not_retried(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError("x")
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY), execute=boom)
    assert _wait(rec["request_id"], {gua.FAILED}) == gua.FAILED
    assert gua.start(gur.load(rec["request_id"]), appr, compute=lambda r, spaces=None: dict(READY), execute=boom) == "STARTED_THREAD"
    time.sleep(0.2)
    assert calls == [1]                                                                            # una aprobación terminal no se re-ejecuta


def test_restart_recovery_interrupts_started_and_denies_verifying(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    gua._transition(rec["request_id"], appr["approval_id"], gua.VERIFYING)
    assert gua.recover(gur.load(rec["request_id"]))["approval"]["state"] == gua.DENIED                  # no había empezado: no se consumió
    fresh = gur.load(rec["request_id"])
    appr2, _ = gua.register(fresh, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k2")
    gua._transition(rec["request_id"], appr2["approval_id"], gua.VERIFYING)
    gua._transition(rec["request_id"], appr2["approval_id"], gua.STARTED)
    assert gua.recover(gur.load(rec["request_id"]))["approval"]["state"] == gua.INTERRUPTED           # pudo haber escrito: requiere verificación
    assert gua.approvable(gur.load(rec["request_id"]), ["DEV"]) == []                                 # terminal: se puede revisar y volver a aprobar


def test_hub_authorizer_grants_only_the_started_approval_with_the_exact_plan(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    auth = gua.hub_authorizer(rec["request_id"], appr["approval_id"], "p" * 64)
    assert not auth("m", {"digest": "p" * 64}).granted                                                # todavía REGISTERED
    gua._transition(rec["request_id"], appr["approval_id"], gua.VERIFYING)
    gua._transition(rec["request_id"], appr["approval_id"], gua.STARTED)
    assert auth("m", {"digest": "q" * 64}).reason == "PLAN_DIGEST_MISMATCH"
    ok = auth("m", {"digest": "p" * 64})
    assert ok.granted and ok.reason == "HUB_HUMAN_APPROVAL" and ok.document["schema"] == "hub-human-approval/v1"
    assert ok.document["declared_spaces"] == ["DEV"] and ok.operator_assurance == "AUTHENTICATED_HUB_SESSION"


# ── ruta de confirmación ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_approve_route_registers_once_and_runs_in_background(routes, monkeypatch, world):
    rec = _ready_request(world)
    started = []
    monkeypatch.setattr(gua, "start", lambda r, a, on_event=None: started.append(a["approval_id"]) or "STARTED_THREAD")
    url = f"/copilot/governed-updates/{rec['request_id']}/approve"
    data = {"confirm": "1", "idempotency_key": "k1", "spaces": ["DEV"]}
    owner = _client("owner@danwor.com")
    assert owner.post(url, data=data, headers=ORIGIN, follow_redirects=False).status_code == 303
    assert owner.post(url, data=data, headers=ORIGIN, follow_redirects=False).status_code == 303           # reintento: misma aprobación
    assert len(started) == 1
    assert [e for e, _ in routes].count("governed_update.approved") == 1
    assert _client("c@danwor.com").post(url, data=data, headers=ORIGIN, follow_redirects=False).status_code == 403   # quien solicitó no aprueba


# ── bifurcación por rol de quien solicita ────────────────────────────────────────────────────────────────────────────────────────────────────
def _route_record(monkeypatch, owner_email):
    import time as _t
    from types import SimpleNamespace
    from webapp.services import datasphere_assessment_service as dsa
    record = {"result_id": RID, "owner": owner_email, "client_dir": "Contreras", "client_display": "Contreras", "terminal_am": "AM_Workforce",
              "space": "DEV", "created_at": _t.time(), "profile": CAND_PROFILE}
    monkeypatch.setattr(dsa, "load_result", lambda rid: record if rid == RID else None)
    monkeypatch.setattr(dsa, "result_status", lambda rec: SimpleNamespace(client="Contreras", terminal_am="AM_Workforce", profile=CAND_PROFILE,
                                                                       judgment={"status": "DETERMINED", "level": "BUENA"}))


def test_approver_requester_goes_straight_to_review_without_emailing_itself(routes, monkeypatch):
    from webapp.services import notifications
    _route_record(monkeypatch, "owner@danwor.com")
    sent = []
    monkeypatch.setattr(notifications, "enabled", lambda: True)
    monkeypatch.setattr(notifications, "emit", lambda *a, **k: sent.append(a) or "queued")
    r = _client("owner@danwor.com").post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/copilot/governed-updates/")
    rec = gur.find_open("Contreras", "AM_Workforce")
    assert sent == [] and rec["notification"]["dispatch"] == "not_required" and rec["requested_by_role"] == "APP_OWNER"
    events = [(e, kw.get("dispatch") or kw.get("requester_role")) for e, kw in routes if e.startswith("governed_update.")]
    assert events == [("governed_update.requested", "APP_OWNER"), ("governed_update.notified", "not_required")]   # auditado igual
    html = _client("owner@danwor.com").get(r.headers["location"]).text
    assert 'data-gu-notification="not_required"' in html and "No requerida" in html
    assert "data-gu-preview-submit" in html                                                          # ya puede operar la revisión


def test_common_requester_registers_and_notifies_architecture(routes, monkeypatch):
    from webapp.services import notifications
    _route_record(monkeypatch, "consultor@danwor.com")
    sent = []
    monkeypatch.setattr(notifications, "enabled", lambda: True)
    monkeypatch.setattr(notifications, "emit", lambda event, key, data, origin=None: sent.append((event, key)) or "queued")
    r = _client("consultor@danwor.com").post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    rec = gur.find_open("Contreras", "AM_Workforce")
    assert sent == [(gur.NOTIFY_EVENT, rec["request_id"])] and rec["requested_by_role"] is None and rec["notification"]["dispatch"] == "queued"
    assert [kw.get("requester_role") for e, kw in routes if e == "governed_update.requested"] == ["REQUESTER"]


def test_approver_resolves_a_common_users_request_and_permissions_are_rechecked_at_confirm(routes, monkeypatch, world):
    from webapp import access
    rec = _ready_request(world)                                                                     # solicitada por un usuario común
    started = []
    monkeypatch.setattr(gua, "start", lambda r, a, on_event=None: started.append(a["approval_id"]) or "STARTED_THREAD")
    url = f"/copilot/governed-updates/{rec['request_id']}"
    owner = _client("owner@danwor.com")
    page = owner.get(url).text
    assert "data-gu-approve" in page                                                                 # el responsable resuelve desde el enlace
    monkeypatch.setattr(access, "APP_OWNER", "otro@danwor.com")                                       # pierde el rol entre la vista y la confirmación
    r = owner.post(f"{url}/approve", data={"confirm": "1", "idempotency_key": "k", "spaces": ["DEV"]}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 404 and started == []        # re-verificado en el servidor: sin rol ni autoría la solicitud es indistinguible de inexistente


def test_banner_cta_depends_on_the_viewers_role(monkeypatch):
    from tests.webapp.test_governed_update_opportunity import _opp_view, _render_callout
    up = _opp_view(monkeypatch)
    approver = _render_callout(up, adhoc_result_id="a" * 32, adhoc_stem=STEM, adhoc_approver=True)
    common = _render_callout(up, adhoc_result_id="a" * 32, adhoc_stem=STEM, adhoc_approver=False)
    assert "Revisar y aprobar" in approver and "Abrir revisión" in approver and "No se envía correo" in approver
    assert "Solicitar actualización" in common and "se notifica a Arquitectura" in common
    import re
    for html in (approver, common):
        assert re.findall(r'action="([^"]+)"', html) == ["/copilot/governed-updates/request"]        # la única acción sigue siendo registrar


def test_not_required_notification_is_not_counted_as_emitted():
    from webapp.services import catalog_activity
    ev = lambda d: {"event": "governed_update.notified", "ts_utc": "t", "user": "u", "payload": {"dispatch": d}}   # noqa: E731
    view = catalog_activity.build([ev("not_required"), ev("queued")], {})
    k = {x["key"]: x for x in view["kpis"]}
    assert k["notificaciones"]["value"] == 1 and "no requeridas" in k["notificaciones"]["hint"]
