"""
test_confidence_origin_migration.py — migración dirigida ``evidence_confidence_v3`` (confidence-cuts/v2 → v3) con el mecanismo EXISTENTE de migraciones de evidencia.

Contrato: regenera con el MISMO motor sobre el pool LOCAL (sin Datasphere/OAuth/restore); la única diferencia admitida es la CONFIANZA (``summary.confidence`` / ``versions.confidence``): el resto del
perfil (evidencia, cobertura, áreas, hallazgos, cierre) queda IDÉNTICO o la migración falla sin escribir (``NON_CONFIDENCE_CONTENT_CHANGED``). Evidence, veredicto del Juicio y clasificaciones de negocio
no cambian de significado. Pool sintético y almacén gobernado temporal (fixtures de la suite de migraciones).
"""
from __future__ import annotations

import copy
import json

import pytest

from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_evidence_migration import _digest, _downgrade, _patch_engine, _rewrite
from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401  (fixtures reutilizadas)
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc

CONF_MIG = "evidence_confidence_v3"
OLD_CONF = "confidence-cuts/v2"


def _confidence_v2(env, *, level="LOW", share=0.4231):
    """Deja el sobre como lo dejó el motor con confidence-cuts/v2: misma evidencia, confianza calculada con el factor viejo (más baja) y versión v2."""
    prof = env["profile_json"]
    prof["versions"]["confidence"] = OLD_CONF
    env["assessment_versions"] = dict(prof["versions"])
    conf = prof["summary"]["confidence"]
    conf["level"] = level
    for c in conf["components"]:
        if c["id"] == "resolved_provenance":
            c["value"], c["level"] = share, level


def _make_v2(world, mutate=None):
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)
    before = json.loads(path.read_text(encoding="utf-8"))

    def m(env):
        _confidence_v2(env)
        if mutate:
            mutate(env)
    _rewrite(path, m)
    restart()
    return path, before


def _run(world, apply=False, migration=CONF_MIG, **kw):
    return bf.migrate_stale_evidence([world.acme], migration, apply=apply, governed_state_present=ALWAYS, **kw)


def test_the_migration_is_registered_with_exactly_the_confidence_key_and_the_real_target_version():
    m = svc.EVIDENCE_MIGRATIONS[CONF_MIG]
    assert m.target_keys == ("confidence",) and m.eligible_old == {"confidence": frozenset({OLD_CONF})} and m.closure_policy == svc.CLOSURE_EQUAL
    assert svc.current_assessment_versions()["confidence"] == "confidence-cuts/v3"
    cur = svc.current_assessment_versions()
    assert svc.targeted_migration({**cur, "confidence": OLD_CONF}, cur) == CONF_MIG
    assert svc.targeted_migration({**cur, "confidence": "confidence-cuts/v1"}, cur) is None                       # solo v2 → v3
    assert svc.targeted_migration({**cur, "confidence": OLD_CONF, "assessability": "assessability-rules/v3"}, cur) is None   # otra diferencia de versión: no es esta migración


def test_a_confidence_v2_profile_is_a_targeted_version_only_stale(world):
    _make_v2(world)
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_ONLY and st.stale_targeted == CONF_MIG


def test_dry_run_runs_the_engine_in_memory_writes_nothing_and_reports_the_confidence_delta(world, monkeypatch):
    path, _ = _make_v2(world)
    digest = _digest(path)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: pytest.fail("sin extracción de Datasphere"))
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("dry-run: cero escrituras"))
    out = _run(world, apply=False)
    rec = out["records"][0]
    assert rec["action"] == bf.WOULD_REGENERATE and out["counts"]["would_regenerate"] == 1
    assert rec["delta"]["versions"]["confidence"] == {"old": OLD_CONF, "new": "confidence-cuts/v3"}
    assert rec["delta"]["confidence"]["changed"] is True and rec["delta"]["closure"]["drift"] is False
    assert _digest(path) == digest


def test_apply_changes_only_the_confidence_and_keeps_evidence_coverage_judgment_and_business_classification(world):
    path, before = _make_v2(world)
    out = _run(world, apply=True)
    assert out["records"][0]["action"] == bf.REGENERATED and files(world) == [path]
    after = json.loads(path.read_text(encoding="utf-8"))
    strip = svc._without_confidence
    assert strip(after["profile_json"]) == strip(before["profile_json"])                                         # evidencia, cobertura, áreas, hallazgos, cierre: idénticos
    assert after["profile_json"]["summary"]["confidence"] == before["profile_json"]["summary"]["confidence"]    # la regeneración v3 reproduce la confianza del motor v3
    assert after["profile_json"]["versions"]["confidence"] == "confidence-cuts/v3"
    assert after["business_classification"] == before["business_classification"]
    assert after["domain_classification"] == before["domain_classification"]
    jb, ja = before["judgment"], after["judgment"]
    for k in ("status", "level", "level_rule", "closure", "reasons", "strengths", "qualifiers", "review_candidates", "evidence_limitations", "not_determinable_reason"):
        assert ja.get(k) == jb.get(k), k                                                                           # el veredicto y sus fundamentos no cambian
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.CURRENT and st.profile["versions"] == svc.current_assessment_versions()


def test_it_is_idempotent_a_second_run_skips_and_writes_nothing(world, monkeypatch):
    path, _ = _make_v2(world)
    _run(world, apply=True)
    restart()
    digest = _digest(path)
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("idempotente: cero escrituras"))
    engine = []
    real = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: engine.append(1) or real(*a, **k))
    out = _run(world, apply=True)
    assert out["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT and out["counts"]["eligible"] == 0 and out["counts"]["regenerated_local"] == 0
    assert engine == [] and _digest(path) == digest


@pytest.mark.parametrize("mutate", [
    lambda d: d["summary"]["coverage"].update(strict=0.123),                      # cobertura distinta
    lambda d: d["summary"].update(extra_field="x"),                               # cualquier otro contenido del resumen
    lambda d: d["areas"].append({"id": "EXTRA"}),                                 # áreas
])
def test_any_non_confidence_difference_is_a_hard_failure_that_preserves_the_record(world, monkeypatch, mutate):
    path, _ = _make_v2(world)
    digest = _digest(path)
    _patch_engine(monkeypatch, mutate, skip_validation=False)
    monkeypatch.setattr(svc.ma_profile, "validate_assessment", lambda doc: None)               # la postcondición propia, no la validación de forma
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["action"] == bf.FAILED_ACTION and rec["reason"].startswith("NON_CONFIDENCE_CONTENT_CHANGED"), rec["reason"]
    assert _digest(path) == digest and files(world) == [path]


def test_a_profile_in_another_contract_is_reported_apart_and_never_chained(world):
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)
    _rewrite(path, _downgrade)                                                     # contrato anterior al evidence release (otra migración del registro)
    restart()
    out = _run(world, apply=False)
    rec = out["records"][0]
    assert rec["action"] != bf.WOULD_REGENERATE and rec["reason"] == bf.OTHER_MIGRATION_APPLICABLE and rec["applicable_migration"] != CONF_MIG


def test_the_confidence_migration_path_never_extracts_restores_or_uses_credentials():
    import inspect
    src = inspect.getsource(svc._without_confidence) + inspect.getsource(svc.migration_postcheck)
    for needle in ("run_export", "restore(", "boto", "SPACES_", "oauth", "requests."):
        assert needle not in src, needle
    assert copy.deepcopy({"a": 1}) == {"a": 1}
