"""Compatibilidad TRANSITORIA v3/v4 (Opcion A, estricta): un perfil v3 cuyo UNICO motivo de STALE es ``canonical_evidence`` v3->v4 sigue CURRENT en la presentacion, queda
identificado como pendiente de ``evidence_leaf_materialization_v4`` y es elegible para ella; NINGUN otro motivo de STALE se oculta; firmas y Juicio no se tocan; se retira quitando
``MigrationTarget.transitional``."""
from __future__ import annotations

import dataclasses

import pytest

from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import (KEY, MIG, V3, _env, _make_v3, _path, _rec, _rewrite, _snapshot, kworld)  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc

POOL = "a" * 64


def _versions(**over) -> dict:
    return {**svc.current_assessment_versions(), **over}


# ───────────────────────────── clasificador puro: estricto ─────────────────────────────
def test_only_the_leaf_materialization_and_the_layer_boundary_migrations_are_transitional():
    # ampliar la compatibilidad exige editar este test a conciencia: ``signals_layer_boundary_v4`` (signal-rules v3 -> v4, solo LAYER_BOUNDARY_REVIEW) es transicional A PROPOSITO: subir el ruleset NO debe volver STALE
    # a los assessments gobernados vigentes ni obligar a regenerarlos; se adopta con la migracion asegurada (plan, aprobacion firmada, respaldo y olas).
    assert {n for n, m in svc.EVIDENCE_MIGRATIONS.items() if m.transitional} == {MIG, "signals_layer_boundary_v4"}


def test_the_single_canonical_evidence_v3_to_v4_difference_with_the_same_pool_is_current_but_pending():
    cur = svc.current_assessment_versions()
    out = svc.classify_currentness(POOL, _versions(canonical_evidence=V3), POOL, cur)
    assert (out.state, out.reason, out.pending_migration, out.version_diff) == (svc.CURRENT, None, MIG, ("canonical_evidence",))
    assert svc.classify_currentness(POOL, cur, POOL, cur).pending_migration is None                       # vigente de verdad: sin pendiente


@pytest.mark.parametrize("stored, pool, expected_state, expected_reason", [
    (lambda: _versions(canonical_evidence=V3), "b" * 64, svc.STALE, svc.STALE_VERSION_AND_INPUT),   # entrada distinta: NO se oculta
    (lambda: _versions(canonical_evidence=V3, signals="signal-rules/v0"), POOL, svc.STALE, svc.STALE_VERSION_ONLY),                             # otra clave de version tambien difiere
    (lambda: _versions(canonical_evidence=V3, confidence="confidence-cuts/v1"), POOL, svc.STALE, svc.STALE_VERSION_ONLY),
    (lambda: _versions(canonical_evidence="canonical-evidence/v2", assessability="assessability-rules/v3"), POOL, svc.STALE, svc.STALE_VERSION_ONLY),   # v2: otra migracion
    (lambda: _versions(canonical_evidence=None), POOL, svc.STALE, svc.STALE_VERSION_ONLY),                                                          # valor viejo desconocido
])
def test_every_other_stale_cause_stays_stale(stored, pool, expected_state, expected_reason):
    out = svc.classify_currentness(POOL, stored(), pool, svc.current_assessment_versions())
    assert (out.state, out.reason, out.pending_migration) == (expected_state, expected_reason, None)


def test_a_different_input_without_version_difference_is_still_input_changed():
    cur = svc.current_assessment_versions()
    assert svc.classify_currentness(POOL, cur, "b" * 64, cur).reason == svc.STALE_INPUT_CHANGED


def test_the_compatibility_is_retired_by_removing_the_flag_and_the_profile_becomes_a_targeted_stale(monkeypatch):
    cur = svc.current_assessment_versions()
    monkeypatch.setitem(svc.EVIDENCE_MIGRATIONS, MIG, dataclasses.replace(svc.EVIDENCE_MIGRATIONS[MIG], transitional=False))
    out = svc.classify_currentness(POOL, _versions(canonical_evidence=V3), POOL, cur)
    assert (out.state, out.reason, out.targeted, out.pending_migration) == (svc.STALE, svc.STALE_VERSION_ONLY, MIG, None)


# ───────────────────────────── sobre real aislado ─────────────────────────────
def test_a_pending_profile_is_presented_as_current_and_nothing_about_it_changes(kworld):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    env = _env(_path(kworld, "Kensing"))
    st = svc.status_for_entry(kworld.k)
    assert st.state == svc.CURRENT and st.pending_migration == MIG and st.stale_reason is None and st.stale_targeted is None and not st.can_generate
    assert st.profile == env["profile_json"] and st.judgment == env["judgment"]                       # perfil y Juicio persistidos, sin recalcular ni re-firmar
    assert env["assessment_versions"]["canonical_evidence"] == V3 and KEY not in env["profile_json"]["canonical_evidence"]
    assert _snapshot(kworld) == snap                                                                   # leer el estado no escribe
    from webapp.services import assessment_presenter as presenter
    assert presenter.stale_note(st) is None and presenter.stale_body(st) is None                       # sin banner de desactualizado


def test_other_stale_causes_are_not_hidden_in_a_real_envelope(kworld):
    _make_v3(kworld, kworld.k)
    _rewrite(_path(kworld, "Kensing"), lambda e: (e.update(input_pool_signature="0" * 64), e.pop("input_closure_signature", None)))
    restart()
    st = svc.status_for_entry(kworld.k)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_AND_INPUT and st.pending_migration is None


def test_the_pending_profile_is_eligible_for_the_migration_and_identified_in_the_report(kworld):
    _make_v3(kworld, kworld.k, kworld.k2)
    out = bf.migrate_stale_evidence([kworld.k, kworld.k2], MIG, apply=False, governed_state_present=ALWAYS)
    assert out["counts"]["eligible"] == 2 and out["counts"]["would_regenerate"] == 2 and out["counts"]["pending_migration_compat"] == 2 and out["counts"]["current"] == 0
    assert _rec(out)["state"] == svc.CURRENT and _rec(out)["action"] == bf.WOULD_REGENERATE and _rec(out)["delta"]["canonical_evidence"]["new"] != _rec(out)["delta"]["canonical_evidence"]["old"]


def test_after_the_migration_nothing_is_pending_and_the_retirement_criterion_is_zero(kworld, tmp_path):
    _make_v3(kworld, kworld.k)
    from tests.webapp.test_assessment_leaf_materialization_migration import _run
    out = _run(kworld, apply=True)
    assert out["outcome"] == bf.APPLIED and out["counts"]["regenerated_local"] + out["counts"]["regenerated_remote"] == 1
    st = svc.status_for_entry(kworld.k)
    assert st.state == svc.CURRENT and st.pending_migration is None
    after = bf.migrate_stale_evidence([kworld.k], MIG, apply=False, governed_state_present=ALWAYS)
    assert after["counts"]["pending_migration_compat"] == 0 and after["counts"]["eligible"] == 0 and after["counts"]["current"] == 1 and after["plan"]["items"] == []


def test_a_pending_profile_is_not_enriched_or_regenerated_behind_the_migration(kworld):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    out = bf.enrich_current([kworld.k], "view_type", apply=True, governed_state_present=ALWAYS)
    assert out["records"][0]["reason"] == bf.SKIPPED_NON_CURRENT and out["records"][0]["action"] == bf.SKIP and _snapshot(kworld) == snap
    assert svc.status_for_entry(kworld.k).pending_migration == MIG


def test_the_direct_regeneration_precondition_accepts_a_pending_profile_and_still_rejects_others(kworld):
    _make_v3(kworld, kworld.k)
    cand = svc.regenerate_stale_evidence(kworld.k, MIG)                                                  # CURRENT pendiente: aceptado
    assert cand.previous_signature and KEY in cand.profile["canonical_evidence"]
    with pytest.raises(svc.EnrichmentError) as e:
        svc.regenerate_stale_evidence(kworld.k, "evidence_confidence_v3")                                # no es la migracion que lo cubre
    assert e.value.code == "PREVIOUS_NOT_TARGETED_VERSION_ONLY_STALE"
