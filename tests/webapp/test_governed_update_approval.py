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


READY_EV = {**READY, "candidate_artifact_signature": "c" * 64}                  # el dry-run aprobado identifica el artefacto candidato (evidencia C3)


def _started(world, rec, *, pre="a" * 64, expected="c" * 64):
    """Aprobación consumida (STARTED) con la evidencia que se registra al consumir, sin hilo vivo (simula otro proceso / reinicio)."""
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k-started")
    gua._transition(rec["request_id"], appr["approval_id"], gua.VERIFYING)
    gua._transition(rec["request_id"], appr["approval_id"], gua.STARTED, pre_sha256=pre, expected_candidate_signature=expected)
    return appr


def _write_result(world, *, sha, signature):
    world["sha"] = sha
    world["env"] = {**world["env"], "assessment_artifact_signature": signature}


def _state(rec):
    return (gur.load(rec["request_id"]).get("approval") or {}).get("state")


# ── C1: transiciones estrictas ─────────────────────────────────────────────────────────
def test_transitions_require_the_allowed_origin_and_never_repeat(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    aid = appr["approval_id"]
    assert gua._transition(rec["request_id"], aid, gua.STARTED) is None                             # REGISTERED→STARTED: salto prohibido
    assert gua._transition(rec["request_id"], aid, gua.APPLIED) is None
    assert gua._transition(rec["request_id"], aid, gua.VERIFYING) is not None
    assert gua._transition(rec["request_id"], aid, gua.VERIFYING) is None                           # repetida: la segunda pierde, no escribe
    assert gua._transition(rec["request_id"], aid, gua.STARTED) is not None
    assert gua._transition(rec["request_id"], aid, gua.STARTED) is None                             # STARTED→STARTED no existe
    assert gua._transition(rec["request_id"], "otra", gua.APPLIED) is None                          # aprobación reemplazada / ajena
    assert [h["state"] for h in gur.load(rec["request_id"])["approval"]["history"]] == ["REGISTERED", "VERIFYING", "STARTED"]


# ── resultados de la vía asegurada: el estado lo decide la EVIDENCIA ──────────────────────
@pytest.mark.parametrize("report,state", [({"outcome": "STOPPED_AT_WAVE"}, gua.ABSTAINED), ({"outcome": "DENIED"}, gua.ABSTAINED),
                                          ({"outcome": "REMOTE_REQUIRED"}, gua.ABSTAINED), ({"outcome": "RECOVERY_ONLY"}, gua.ABSTAINED),
                                          ({"outcome": "WHATEVER"}, gua.FAILED)])
def test_not_applied_outcomes_keep_their_state_only_if_the_envelope_is_unchanged(world, report, state):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV), execute=lambda *a: report)
    assert _wait(rec["request_id"], {state}) == state
    assert gur.load(rec["request_id"])["approval"]["evidence"] == gua.EV_UNCHANGED


@pytest.mark.parametrize("signature,state", [("c" * 64, gua.APPLIED), ("otra", gua.RECONCILIATION_REQUIRED)])
def test_a_not_applied_report_with_a_changed_envelope_is_settled_by_evidence(world, signature, state):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV),
              execute=lambda *a: _write_result(world, sha="b" * 64, signature=signature) or {"outcome": "STOPPED_AT_WAVE"})
    assert _wait(rec["request_id"], {state}) == state                                             # una escritura parcial no se disfraza de abstenida


def test_an_execution_exception_without_effects_is_failed_and_never_re_executed(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError("x")
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV), execute=boom)
    assert _wait(rec["request_id"], {gua.FAILED}) == gua.FAILED
    assert gua.start(gur.load(rec["request_id"]), appr, compute=lambda r, spaces=None: dict(READY_EV), execute=boom) == "NOT_STARTABLE"
    time.sleep(0.2)
    assert calls == [1]                                                                            # una aprobación procesada nunca se re-ejecuta


def test_an_execution_exception_after_a_partial_write_blocks_new_approvals(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")

    def partial(*a):
        _write_result(world, sha="b" * 64, signature="otra")
        raise RuntimeError("caída a mitad")
    gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV), execute=partial)
    assert _wait(rec["request_id"], {gua.RECONCILIATION_REQUIRED}) == gua.RECONCILIATION_REQUIRED
    assert "RECONCILIATION_REQUIRED" in gua.approvable(gur.load(rec["request_id"]), ["DEV"])
    with pytest.raises(gua.ApprovalRefused, match="RECONCILIATION_REQUIRED"):
        gua.register(gur.load(rec["request_id"]), OWNER, "APP_OWNER", ["DEV"], idempotency_key="k-otra")


def test_a_duplicate_concurrent_start_executes_at_most_once(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    gate, calls = threading.Event(), []

    def slow(*a):
        calls.append(1)
        gate.wait(2)
        return {"outcome": "APPLIED"}
    first = gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV), execute=slow)
    second = gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV), execute=slow)
    gate.set()
    assert first == "STARTED_THREAD" and second in ("ALREADY_RUNNING", "NOT_STARTABLE")
    assert _wait(rec["request_id"], {gua.APPLIED}) == gua.APPLIED and calls == [1]
    assert gua.register(gur.load(rec["request_id"]), OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")[0]["state"] == gua.APPLIED   # misma clave: misma


# ── C2/C3: recuperación de ARRANQUE por evidencia (nunca desde un GET) ──────────────────────
@pytest.mark.parametrize("pre_state", [gua.REGISTERED, gua.VERIFYING])
def test_boot_recovery_denies_a_not_started_approval_without_consuming_it(world, pre_state):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    if pre_state == gua.VERIFYING:
        gua._transition(rec["request_id"], appr["approval_id"], gua.VERIFYING)
    assert gua.recover_on_boot() == [{"request_id": rec["request_id"], "approval_id": appr["approval_id"], "from": pre_state, "to": gua.DENIED}]
    assert gua.approvable(gur.load(rec["request_id"]), ["DEV"]) == []                                # no se consumió: se puede volver a aprobar


def test_boot_recovery_marks_applied_only_when_the_exact_result_is_accredited(world):
    rec = _ready_request(world)
    _started(world, rec)
    _write_result(world, sha="b" * 64, signature="c" * 64)                                         # completó, pero el proceso murió antes de persistir el estado
    gua.recover_on_boot()
    stored = gur.load(rec["request_id"])
    assert stored["approval"]["state"] == gua.APPLIED and stored["approval"]["reconciled"] is True and stored["status"] == "APPLIED"


@pytest.mark.parametrize("pre,expected,result,evidence", [
    ("a" * 64, "c" * 64, None, gua.EV_UNCHANGED),                     # el sobre anterior sigue: NO equivale a «seguro reintentar» (otro proceso podría seguir)
    ("a" * 64, "c" * 64, ("b" * 64, "otra"), gua.EV_DIVERGENT),        # escritura parcial / resultado ajeno
    (None, None, None, gua.EV_UNVERIFIABLE),                           # consumida sin evidencia registrada (p. ej. anterior a la fase B)
])
def test_boot_recovery_blocks_every_ambiguous_execution(world, pre, expected, result, evidence):
    rec = _ready_request(world)
    _started(world, rec, pre=pre, expected=expected)
    if result:
        _write_result(world, sha=result[0], signature=result[1])
    gua.recover_on_boot()
    appr = gur.load(rec["request_id"])["approval"]
    assert appr["state"] == gua.RECONCILIATION_REQUIRED and appr["evidence"] == evidence
    with pytest.raises(gua.ApprovalRefused, match="RECONCILIATION_REQUIRED"):
        gua.register(gur.load(rec["request_id"]), OWNER, "APP_OWNER", ["DEV"], idempotency_key="k-new")


def test_boot_recovery_skips_an_execution_alive_in_this_process(world):
    rec = _ready_request(world)
    appr = _started(world, rec)
    gua._running.add(appr["approval_id"])
    try:
        assert gua.recover_on_boot() == [] and _state(rec) == gua.STARTED
    finally:
        gua._running.discard(appr["approval_id"])


def test_repeated_gets_never_recover_or_change_states(routes, world):
    rec = _ready_request(world)
    _started(world, rec)                                                                            # huérfana: sin hilo vivo en este proceso
    url = f"/copilot/governed-updates/{rec['request_id']}"
    client = _client("owner@danwor.com")
    before = gur._path("Contreras", "AM_Workforce", rec["request_id"]).read_bytes()               # C6: tampoco la primera visita escribe
    for _ in range(4):
        assert client.get(url).status_code == 200
    assert gur._path("Contreras", "AM_Workforce", rec["request_id"]).read_bytes() == before and _state(rec) == gua.STARTED


# ── C3: reconciliación EXPLÍCITA de mantenimiento ────────────────────────────────────────────
def _blocked(world, rec, monkeypatch, tmp_path):
    monkeypatch.setattr(gua, "_backup_dir", lambda aid: tmp_path / aid)
    appr = _started(world, rec)
    gua.recover_on_boot()
    assert _state(rec) == gua.RECONCILIATION_REQUIRED
    return appr


def test_reconcile_requires_the_operator_confirmation(world, monkeypatch, tmp_path):
    rec = _ready_request(world)
    _blocked(world, rec, monkeypatch, tmp_path)
    assert gua.reconcile(rec["request_id"], operator="op", confirm_no_active_execution=False)["result"] == "CONFIRMATION_REQUIRED"
    assert _state(rec) == gua.RECONCILIATION_REQUIRED


def test_reconcile_reopens_only_with_no_effect_accredited(world, monkeypatch, tmp_path):
    rec = _ready_request(world)
    _blocked(world, rec, monkeypatch, tmp_path)                                                    # sin directorio de respaldo y sin Spaces: no empezó a escribir
    out = gua.reconcile(rec["request_id"], operator="op", confirm_no_active_execution=True)
    assert out["result"] == gua.NOT_APPLIED and gua.approvable(gur.load(rec["request_id"]), ["DEV"]) == []


def test_reconcile_keeps_blocked_when_the_journal_shows_a_commit(world, monkeypatch, tmp_path):
    from webapp.services import governed_backup as gb
    rec = _ready_request(world)
    appr = _blocked(world, rec, monkeypatch, tmp_path)
    (tmp_path / appr["approval_id"]).mkdir()
    gb.append_journal(tmp_path / appr["approval_id"], {"event": gb.EV_COMMITTED, "key": "x"})
    out = gua.reconcile(rec["request_id"], operator="op", confirm_no_active_execution=True)
    assert out["result"] == "STILL_BLOCKED" and out["journal_committed"] is True and _state(rec) == gua.RECONCILIATION_REQUIRED


def test_reconcile_marks_applied_with_the_exact_result_and_the_cli_reports_it(world, monkeypatch, tmp_path, capsys):
    rec = _ready_request(world)
    _blocked(world, rec, monkeypatch, tmp_path)
    _write_result(world, sha="b" * 64, signature="c" * 64)
    assert gua._main(["--reconcile", rec["request_id"], "--operator", "op", "--confirm-no-active-execution"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == gua.APPLIED and gur.load(rec["request_id"])["status"] == "APPLIED"


# ── C4: verificación inmediatamente antes de escribir (no es un CAS distribuido) ─────────────
def test_hub_authorizer_grants_only_the_started_approval_with_the_exact_plan(world):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")
    aid = appr["approval_id"]
    auth = gua.hub_authorizer(rec["request_id"], aid, "p" * 64)
    assert not auth("m", {"digest": "p" * 64}).granted                                                # todavía REGISTERED
    gua._transition(rec["request_id"], aid, gua.VERIFYING)
    gua._transition(rec["request_id"], aid, gua.STARTED, pre_sha256="a" * 64, expected_candidate_signature="c" * 64)
    assert auth("m", {"digest": "p" * 64}).reason == "NOT_EXECUTING_IN_THIS_PROCESS"                 # STARTED pero sin hilo propio
    gua._running.add(aid)
    try:
        assert auth("m", {"digest": "q" * 64}).reason == "PLAN_DIGEST_MISMATCH"
        ok = auth("m", {"digest": "p" * 64})
        assert ok.granted and ok.reason == "HUB_HUMAN_APPROVAL" and ok.document["schema"] == "hub-human-approval/v1"
        assert ok.document["declared_spaces"] == ["DEV"] and ok.operator_assurance == "AUTHENTICATED_HUB_SESSION"
        world["sha"] = "b" * 64
        assert auth("m", {"digest": "p" * 64}).reason == "ENVELOPE_CHANGED"                         # el sobre cambió desde el consumo
        world["sha"] = "a" * 64
        with gur._lock:
            fresh = gur.load(rec["request_id"])
            fresh["approvals"] = [{"state": gua.APPLIED}]
            gur._write(fresh)
        assert auth("m", {"digest": "p" * 64}).reason == "ALREADY_APPLIED"
    finally:
        gua._running.discard(aid)


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


# ── C5: disponibilidad gobernada durante el arranque (falla cerrado) ─────────────────────────
@pytest.fixture
def pending(world, monkeypatch):
    monkeypatch.setattr(gua, "_readiness", {"state": gua.READINESS_PENDING, "reason": "BOOT_RECOVERY_PENDING"})
    return world


def _bytes(rec):
    return gur._path("Contreras", "AM_Workforce", rec["request_id"]).read_bytes()


def test_the_module_starts_closed_and_only_boot_recovery_opens_it(world, monkeypatch):
    import importlib
    fresh = importlib.reload(gua)                                                                   # estado inicial del módulo, sin lifespan
    try:
        assert fresh.readiness()["state"] == fresh.READINESS_PENDING
    finally:
        importlib.reload(gua)


@pytest.mark.parametrize("state,code", [(gua.READINESS_PENDING, "RECOVERY_PENDING"), (gua.READINESS_FAILED, "RECOVERY_FAILED")])
def test_outside_ready_nothing_is_registered_started_or_authorized_and_the_registry_is_untouched(world, monkeypatch, state, code):
    rec = _ready_request(world)
    appr, _ = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")                   # registrada con el proceso READY
    gua._transition(rec["request_id"], appr["approval_id"], gua.VERIFYING)
    gua._transition(rec["request_id"], appr["approval_id"], gua.STARTED)
    gua._running.add(appr["approval_id"])
    other = gur.create(client_dir="Contreras", client_display="Contreras", terminal_am="AM_EducationPro", space="DEV", result_id=RID,
                       candidate_judgment={"status": "DETERMINED", "level": "BUENA"}, candidate_profile=CAND_PROFILE, requested_by="c@danwor.com")[0]
    before = gur._path("Contreras", "AM_EducationPro", other["request_id"]).read_bytes()
    monkeypatch.setattr(gua, "_readiness", {"state": state, "reason": "x"})
    try:
        with pytest.raises(gua.ApprovalRefused, match=code):
            gua.register(other, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k2")
        assert gur._path("Contreras", "AM_EducationPro", other["request_id"]).read_bytes() == before      # el registro no se alteró
        assert gua.start(rec, appr, compute=lambda r, spaces=None: pytest.fail("no arranca"), execute=lambda *a: pytest.fail("no ejecuta")) == "NOT_READY"
        assert gua.hub_authorizer(rec["request_id"], appr["approval_id"], "p" * 64)("m", {"digest": "p" * 64}).reason == "PROCESS_NOT_READY"
        assert code in gua.approvable(gur.load(other["request_id"]), ["DEV"])                       # la vista no ofrece aprobar
    finally:
        gua._running.discard(appr["approval_id"])


def test_the_approve_route_answers_503_while_pending_without_touching_the_registry(routes, pending):
    rec = _ready_request(pending)
    before = _bytes(rec)
    r = _client("owner@danwor.com").post(f"/copilot/governed-updates/{rec['request_id']}/approve",
                                         data={"confirm": "1", "idempotency_key": "k1", "spaces": ["DEV"]}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 503 and r.headers.get("retry-after") and _bytes(rec) == before
    assert ("governed_update.rejected", ) == tuple(e for e, kw in routes if kw.get("reason") == "RECOVERY_PENDING")


def test_successful_boot_recovery_opens_ready(world, monkeypatch):
    monkeypatch.setattr(gua, "_readiness", {"state": gua.READINESS_PENDING, "reason": None})
    rec = _ready_request(world)
    gua.boot_recovery()
    assert gua.readiness()["state"] == gua.READINESS_READY
    assert gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")[1] is True


@pytest.mark.parametrize("break_it,reason", [
    (lambda mp: (mp.setattr(gua.svc.remote_store, "enabled", lambda: True), mp.setattr(gua.svc.remote_store, "sync_blocked_reason", lambda: "el restore falló")),
     "RESTORE_NOT_COMPLETE"),
    (lambda mp: mp.setattr(gua, "recover_on_boot", lambda: (_ for _ in ()).throw(RuntimeError("x"))), "RECOVERY_ERROR"),
    (lambda mp: mp.setattr(gua, "recover_on_boot", lambda: [{"request_id": "r", "approval_id": "a", "from": "STARTED", "to": None}]), "RECOVERY_INCOMPLETE"),
])
def test_a_failed_restore_or_recovery_leaves_the_gate_failed_and_closed(world, monkeypatch, break_it, reason):
    monkeypatch.setattr(gua, "_readiness", {"state": gua.READINESS_PENDING, "reason": None})
    rec = _ready_request(world)
    break_it(monkeypatch)
    gua.boot_recovery()
    assert gua.readiness()["state"] == gua.READINESS_FAILED and gua.readiness()["reason"].startswith(reason)
    with pytest.raises(gua.ApprovalRefused, match="RECOVERY_FAILED"):                         # la compuerta rechaza ANTES de tocar el registro
        gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k")


def test_the_isolated_boot_race_no_longer_reproduces(world, monkeypatch):
    """Escenario de `scratchpad/race_demo.py`: con la recuperación pendiente, otra solicitud no se aprueba ni ejecuta, y no hay DENIED espurio."""
    monkeypatch.setattr(gua, "_readiness", {"state": gua.READINESS_PENDING, "reason": None})
    rec = _ready_request(world)
    with pytest.raises(gua.ApprovalRefused, match="RECOVERY_PENDING"):                              # 1) antes: se registraba y ejecutaba (APPLIED)
        gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="kb")
    assert gua.boot_recovery() == [] and gua.readiness()["state"] == gua.READINESS_READY            # 3) nada que denegar en falso
    appr, created = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="kb")
    assert created and gua.start(rec, appr, compute=lambda r, spaces=None: dict(READY_EV), execute=lambda *a: {"outcome": "APPLIED"}) == "STARTED_THREAD"
    assert _wait(rec["request_id"], {gua.APPLIED}) == gua.APPLIED


def test_gets_and_the_dry_run_stay_available_while_pending_but_do_not_offer_to_approve(routes, pending):
    rec = _ready_request(pending)
    r = _client("owner@danwor.com").get(f"/copilot/governed-updates/{rec['request_id']}")
    assert r.status_code == 200 and "data-gu-approve" not in r.text and "recuperación de arranque" in r.text
