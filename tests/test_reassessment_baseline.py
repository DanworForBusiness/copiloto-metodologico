"""Simulación de reassessment, fase de reproducibilidad (``tools.catalog.reassessment.baseline``): pruebas de aceptación.

Los casos con pool real publican con el exportador, la publicación y el motor REALES (``tests.publication_fakes``) y arman un snapshot local; el simulador
solo lee ese snapshot. El oráculo con los sobres productivos corre si ``DANWOR_REASSESS_SNAPSHOT`` apunta a un snapshot (no se versiona).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.publication_fakes import CLIENT
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog.modeling_assessment import currentness as cur_rule
from tools.catalog.reassessment import baseline as bl
from tools.catalog import publication_txn as txn

REPO = Path(__file__).resolve().parents[1]
AM_A = "AM_Lista_Precios"


@pytest.fixture()
def published(ctx, monkeypatch, tmp_path):                                                    # noqa: F811
    from core import publication_gate as gate
    from webapp.services import assessment_service as assess
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    monkeypatch.setenv(txn.SAFE_ENV, "1")
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate.set_resolver(None)
    res, _ = ctx.publish(AM_A, stage=True)
    assert res.status == txn.COMPLETED
    ctx.governed = tmp_path / "governed"
    yield ctx
    gate.clear(ctx.catalog)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()


def snapshot_of(w, dest: Path, *, envelope_edit=None) -> Path:
    """Snapshot local como lo deja la herramienta de copia: sobres + pool (mismo layout que `.csn_cache`)."""
    (dest / "envelopes").mkdir(parents=True)
    for p in w.governed.rglob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("envelope_version") == bl.ENVELOPE_VERSION:
            if envelope_edit:
                envelope_edit(data)
            (dest / "envelopes" / f"{data['client']}__{data['terminal_am']}.json").write_text(json.dumps(data), encoding="utf-8")
    shutil.copytree(w.pool, dest / "pool")
    (dest / "snapshot.json").write_text(json.dumps({"prefix_label": "test", "taken_at": "2026-11-02T00:00:00Z"}), encoding="utf-8")
    return dest


def row(report, am=AM_A):
    return next(r for r in report["envelopes"] if r["terminal_am"] == am)


def _pool_file(snap: Path, name: str) -> Path:
    return next(p for p in (snap / "pool").rglob(f"{name}.json"))


def _add_unrelated_file(snap: Path) -> None:
    """Archivo NUEVO en el pool del cliente que no menciona ningún objeto del cierre: cambia la firma del pool, no las dependencias."""
    views = next((snap / "pool").rglob("views"))
    (views / "ZZ_AJENA.json").write_text(json.dumps({"definitions": {"ZZ_AJENA": {"kind": "entity", "elements": {"X": {"type": "cds.String"}}}}}), encoding="utf-8")


# ── A3 · baseline reproducido y vigente ──
def test_published_assessment_is_reproduced_and_current(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    r = row(bl.run(snap))
    rep = r["reproducibility"]
    assert (rep["state"], rep["integrity"]["state"], rep["judgment"]["state"], rep["evidence"]["state"]) == (bl.REPRODUCED, bl.VERIFIED, bl.MATCH, bl.MATCH)
    assert (r["currency"]["state"], r["currency"]["basis"], r["currency"]["hub_state"]) == ("CURRENT", "POOL_IDENTICAL", "CURRENT")
    assert r["currency"]["hub_comparison"]["code"] == "AGREES"


# ── A4 / A5 / A6 · vigencia independiente de la reproducibilidad ──
def test_change_inside_the_closure_is_stale_and_evidence_is_not_attempted(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    p = _pool_file(snap, "3VF_LP")
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["definitions"]["3VF_LP"]["@EndUserText.label"] = "cambio dentro del cierre"
    p.write_text(json.dumps(doc), encoding="utf-8")
    r = row(bl.run(snap))
    assert (r["currency"]["state"], r["currency"]["basis"]) == ("STALE", "DEPENDENCIES_AFFECTED")
    assert (r["reproducibility"]["state"], r["reproducibility"]["evidence"]["reason"]) == (bl.JUDGMENT_ONLY, "POOL_NOT_EQUIVALENT")
    assert "POOL_NOT_EQUIVALENT" in r["flags"]


def test_change_outside_the_closure_keeps_it_current_by_independent_dependencies(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    _add_unrelated_file(snap)
    r = row(bl.run(snap))
    assert (r["currency"]["state"], r["currency"]["basis"], r["currency"]["pool_drift"]) == ("CURRENT", "DEPENDENCIES_INDEPENDENT", True)
    assert r["reproducibility"]["state"] == bl.JUDGMENT_ONLY                                   # la firma del pool cambió: no se reconstruye, pero NO es un defecto


def test_without_dependency_signature_a_changed_pool_is_undetermined_never_current_nor_stale(published, tmp_path):
    def strip(env):
        env.pop("input_closure_signature", None)
        env.pop("dependency_rule_version", None)
    snap = snapshot_of(published, tmp_path / "snap", envelope_edit=strip)
    _add_unrelated_file(snap)
    r = row(bl.run(snap))
    assert (r["currency"]["state"], r["currency"]["basis"], r["currency"]["hub_state"]) == ("UNDETERMINED", "NO_DEPENDENCY_SIGNATURE", "STALE")
    cmp = r["currency"]["hub_comparison"]                                                      # se preservan AMBOS resultados y se explica la diferencia
    assert (cmp["code"], cmp["hub_state"], cmp["simulation_state"]) == (bl.HUB_FAIL_CLOSED, "STALE", "UNDETERMINED") and "conservadora" in cmp["explanation"]
    assert "NO_DEPENDENCY_SIGNATURE" in r["flags"]
    assert r["reproducibility"]["state"] == bl.JUDGMENT_ONLY and r["reproducibility"]["integrity"]["state"] == bl.VERIFIED


def test_without_dependency_signature_and_identical_pool_it_is_current(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap", envelope_edit=lambda e: e.pop("input_closure_signature", None))
    r = row(bl.run(snap))
    assert (r["currency"]["state"], r["currency"]["basis"]) == ("CURRENT", "POOL_IDENTICAL") and r["reproducibility"]["state"] == bl.REPRODUCED


# ── A7 · versiones de evidencia distintas ──
def test_code_in_other_evidence_versions_never_reconstructs_and_flags_it(published, tmp_path, monkeypatch):
    snap = snapshot_of(published, tmp_path / "snap")
    real = bl.current_versions()
    monkeypatch.setattr(bl, "current_versions", lambda: {**real, "signals": "signal-rules/v9"})
    r = row(bl.run(snap))
    assert (r["reproducibility"]["state"], r["reproducibility"]["evidence"]["reason"]) == (bl.JUDGMENT_ONLY, "CODE_VERSIONS_DIFFER")
    assert (r["currency"]["state"], r["currency"]["basis"]) == ("STALE", "VERSIONS_DIFFER") and "MIXED_EVIDENCE_VERSIONS" in r["flags"]


# ── A2 · integridad y Juicio: NOT_REPRODUCED con su causa ──
def test_tampered_profile_breaks_integrity(published, tmp_path):
    def tamper(env):
        env["profile_json"]["scope"]["root_label"] = "alterado"
    r = row(bl.run(snapshot_of(published, tmp_path / "snap", envelope_edit=tamper)))
    assert r["reproducibility"]["state"] == bl.NOT_REPRODUCED and "ARTIFACT_SIGNATURE_MISMATCH" in r["reproducibility"]["causes"]


def test_tampered_judgment_is_not_reproduced(published, tmp_path):
    def tamper(env):
        env["judgment"]["output_signature"] = "sha256:" + "0" * 64
    r = row(bl.run(snapshot_of(published, tmp_path / "snap", envelope_edit=tamper)))
    assert r["reproducibility"]["state"] == bl.NOT_REPRODUCED and r["reproducibility"]["judgment"]["state"] == bl.MATCH or \
        r["reproducibility"]["causes"] == ["JUDGMENT_MISMATCH"]


# ── A9 · baseline inmutable ──
def test_the_snapshot_is_never_modified_and_published_is_a_literal_copy(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in snap.rglob("*") if p.is_file()}
    report = bl.run(snap)
    bl.write_report(report, tmp_path / "out", snap)
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in snap.rglob("*") if p.is_file()} == before
    env = json.loads(next((snap / "envelopes").glob("*.json")).read_text(encoding="utf-8"))
    pub = row(report)["published"]
    assert pub["output_signature"] == env["judgment"]["output_signature"] and pub["input_pool_signature"] == env["input_pool_signature"]
    assert pub["assessment_versions"] == env["assessment_versions"] and pub["level"] == env["judgment"]["level"]


# ── A10 · read-only intrínseco ──
def test_the_simulator_imports_nothing_that_can_write_or_publish():
    code = ("import sys; import tools.catalog.reassessment.baseline as b; "
            "bad=[m for m in sys.modules if m.split('.')[0] in ('webapp','boto3','botocore') or m in ('core.remote_store','core.publication_gate','tools.catalog.publication_txn','tools.catalog.DSP_CLI_Export_Lineage')]; "
            "print(','.join(sorted(bad)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    assert out == ""


@pytest.mark.parametrize("where", ["repo", "snapshot", "catalog", "governed"])
def test_output_inside_protected_locations_is_rejected(published, tmp_path, monkeypatch, where):
    snap = snapshot_of(published, tmp_path / "snap")
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "cat"))
    target = {"repo": REPO / "_tmp_reassess", "snapshot": snap / "out", "catalog": tmp_path / "cat" / "out", "governed": published.governed / "out"}[where]
    with pytest.raises(bl.OutputLocationError):
        bl.validate_output_dir(target, snap)


def test_runs_without_network(published, tmp_path, monkeypatch):
    snap = snapshot_of(published, tmp_path / "snap")

    def no_net(*a, **k):
        raise AssertionError("el simulador intentó usar la red")
    monkeypatch.setattr(socket, "socket", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    assert row(bl.run(snap))["reproducibility"]["state"] == bl.REPRODUCED


# ── A11 · determinismo ──
def test_two_runs_give_the_same_report(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    assert bl.run(snap, generated_at="x") == bl.run(snap, generated_at="x")


# ── A12 · identidad y ambientes ──
def test_same_am_in_two_clients_does_not_collide_and_duplicates_are_rejected(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    src = next((snap / "envelopes").glob("*.json"))
    other = json.loads(src.read_text(encoding="utf-8"))
    other["client"] = "OtroCliente"
    (snap / "envelopes" / "OtroCliente__x.json").write_text(json.dumps(other), encoding="utf-8")
    report = bl.run(snap)
    assert sorted(r["client"] for r in report["envelopes"]) == sorted([CLIENT, "OtroCliente"])
    assert next(r for r in report["envelopes"] if r["client"] == "OtroCliente")["currency"]["basis"] == "POOL_UNAVAILABLE"
    shutil.copy(src, snap / "envelopes" / "copia.json")
    with pytest.raises(bl.SnapshotError):
        bl.run(snap)


def test_snapshot_mixing_environments_is_rejected(published, tmp_path):
    snap = snapshot_of(published, tmp_path / "snap")
    (snap / "snapshot.json").write_text(json.dumps({"prefix_labels": ["prod", "dev"]}), encoding="utf-8")
    with pytest.raises(bl.SnapshotError):
        bl.run(snap)


# ── A13 · sin capacidades fuera de alcance ──
def test_cli_offers_no_candidate_rules_migrations_or_apply():
    out = subprocess.run([sys.executable, "-m", "tools.catalog.reassessment.baseline", "--help"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    for word in ("--ruleset", "--migrat", "--apply", "--publish", "--write"):
        assert word not in out


# ── A8 · regla de vigencia compartida: el servicio usa EXACTAMENTE la misma ──
def test_the_service_reexports_the_shared_currency_rule():
    from webapp.services import assessment_service as svc
    for name in ("classify_currentness", "resolve_currentness", "dependency_verdict_from", "version_diff", "targeted_migration", "EVIDENCE_MIGRATIONS", "Currentness",
                 "CURRENT", "STALE", "STALE_INPUT_CHANGED", "DEP_INDEPENDENT"):
        assert getattr(svc, name) is getattr(cur_rule, name), name


def _legacy_resolve(stored, versions_old, pool, versions, dep):
    """Lógica ANTERIOR del servicio (copiada como oráculo de equivalencia)."""
    c = cur_rule.classify_currentness(stored, versions_old, pool, versions)
    drift, verdict = False, None
    if c.state == cur_rule.STALE and c.reason in (cur_rule.STALE_INPUT_CHANGED, cur_rule.STALE_VERSION_AND_INPUT):
        verdict = dep()
        if verdict == cur_rule.DEP_INDEPENDENT:
            drift = True
            c = cur_rule.classify_currentness(stored, versions_old, stored, versions)
    return c, drift, verdict


@pytest.mark.parametrize("pool", ["A", "B"])
@pytest.mark.parametrize("old_versions", [{"signals": "signal-rules/v4"}, {"signals": "signal-rules/v3"}, {"canonical_evidence": "canonical-evidence/v3", "signals": "signal-rules/v4"}])
@pytest.mark.parametrize("verdict", ["INDEPENDENT", "AFFECTED", "UNCERTAIN"])
def test_resolve_currentness_is_equivalent_to_the_previous_service_logic(pool, old_versions, verdict):
    versions = {"signals": "signal-rules/v4", "canonical_evidence": "canonical-evidence/v4"}
    old = {**versions, **old_versions}
    assert cur_rule.resolve_currentness("A", old, pool, versions, lambda: verdict) == _legacy_resolve("A", old, pool, versions, lambda: verdict)


@pytest.mark.parametrize("stored, rule, current, expected", [
    (None, "dep-closure/v1", "x", "UNCERTAIN"), ("x", "otra", "x", "UNCERTAIN"), ("x", "dep-closure/v1", None, "UNCERTAIN"),
    ("x", "dep-closure/v1", "x", "INDEPENDENT"), ("x", "dep-closure/v1", "y", "AFFECTED")])
def test_dependency_verdict_from_matches_the_service_rule(stored, rule, current, expected):
    assert cur_rule.dependency_verdict_from(stored, rule, "dep-closure/v1", current) == expected


# ── A1 · oráculo con los sobres productivos ──
SNAP = os.environ.get("DANWOR_REASSESS_SNAPSHOT", "").strip()


@pytest.mark.skipif(not SNAP, reason="sin snapshot productivo (DANWOR_REASSESS_SNAPSHOT)")
def test_oracle_every_published_judgment_reproduces():
    report = bl.run(Path(SNAP))
    states = [r["reproducibility"]["judgment"]["state"] for r in report["envelopes"]]
    assert states and all(s == bl.MATCH for s in states)
    assert not [r for r in report["envelopes"] if r["reproducibility"]["state"] == bl.NOT_REPRODUCED]
    assert all(r["reproducibility"]["integrity"]["state"] == bl.VERIFIED for r in report["envelopes"])
