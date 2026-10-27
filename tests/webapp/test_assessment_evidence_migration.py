"""test_assessment_evidence_migration.py — migración CONTROLADA de assessments gobernados STALE por VERSIÓN de evidencia
(``assessment_backfill --regenerate-stale-evidence <migracion>``: ``evidence_g1_g3_v1`` y ``evidence_closure_contract_v2``, sin encadenamiento) y clasificador de vigencia.

Contrato (ver el docstring de ``assessment_backfill``):
  * modo EXPLÍCITO y separado: exige --dry-run XOR --apply, excluyente con --enrich-current-missing; el backfill normal y el enriquecimiento no cambian;
  * elegible = STALE VERSION_ONLY (misma firma del pool) cuya diferencia de versiones es EXACTAMENTE la del registro; STALE por entrada / con otras versiones /
    FAILED / INCOMPATIBLE / NOT_YET_ASSESSED / NOT_APPLICABLE se reportan aparte y NO se migran;
  * MISMO núcleo seguro que el enriquecimiento (regenerar en memoria → validar → postcondiciones → commit seguro → durabilidad remota exacta → reinicio);
  * postcondiciones por CONTENIDO de ``canonical_evidence`` e identidad canónica del cierre (CLOSURE_DRIFT = falla dura); ante cualquier fallo el registro previo queda intacto.
Fixtures sintéticas y un Spaces falso en memoria (sin red, sin Datasphere, sin credenciales reales).
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from types import SimpleNamespace

import pytest

from core import remote_store
from tests.webapp.test_assessment_enrichment import ALWAYS, _entry, _remote_key, spaces_rw  # noqa: F401  (fixture reutilizada)
from tests.webapp.test_governed_assessment_durability import FakeBucket, HX, ORIGIN, client_for, files, restart, spaces, world  # noqa: F401  (fixtures reutilizadas)
from tools.catalog.modeling_assessment import profile as ma_profile
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads

MIG = "evidence_closure_contract_v2"
OLD_MIG = "evidence_g1_g3_v1"
LIN_MIG = "evidence_field_lineage_v3"
OLD_TARGET = svc.EVIDENCE_MIGRATIONS[OLD_MIG]
TARGET = svc.EVIDENCE_MIGRATIONS[MIG]
OLD_VALUES = {k: next(iter(v)) for k, v in TARGET.eligible_old.items() if v != frozenset({None})}


# ═══════════════ 1. clasificador de vigencia (puro) ═══════════════
def _cur_versions():
    return svc.current_assessment_versions()


def _old_versions():
    v = dict(_cur_versions())
    v.update(OLD_VALUES)
    v.pop("closure", None)                                                      # el perfil previo al evidence release no traia la clave `closure`
    return v


def test_classifier_current_when_signature_and_versions_match():
    v = _cur_versions()
    assert svc.classify_currentness("sig", dict(v), "sig", v) == svc.Currentness(svc.CURRENT)


def test_classifier_distinguishes_version_only_input_changed_and_both():
    v = _cur_versions()
    only = svc.classify_currentness("sig", _old_versions(), "sig", v)
    assert (only.state, only.reason, only.pool_same, only.targeted) == (svc.STALE, svc.STALE_VERSION_ONLY, True, MIG)
    assert only.version_diff == tuple(sorted(TARGET.target_keys)) and not only.version_only_untargeted
    inp = svc.classify_currentness("old", dict(v), "new", v)
    assert (inp.state, inp.reason, inp.pool_same, inp.targeted, inp.version_diff) == (svc.STALE, svc.STALE_INPUT_CHANGED, False, None, ())
    both = svc.classify_currentness("old", _old_versions(), "new", v)
    assert (both.reason, both.pool_same, both.targeted) == (svc.STALE_VERSION_AND_INPUT, False, None)


def test_classifier_version_only_untargeted_when_the_diff_is_not_exactly_the_migration():
    v = _cur_versions()
    other = dict(v, identity="identity-rules/v0")
    c = svc.classify_currentness("sig", other, "sig", v)
    assert c.reason == svc.STALE_VERSION_ONLY and c.targeted is None and c.version_only_untargeted and c.version_diff == ("identity",)
    extra = dict(_old_versions(), identity="identity-rules/v0")                 # la migración MÁS otra clave
    assert svc.classify_currentness("sig", extra, "sig", v).targeted is None
    partial = dict(v, assessability=OLD_VALUES["assessability"])                # solo una de las cuatro claves
    assert svc.classify_currentness("sig", partial, "sig", v).targeted is None
    unknown_old = dict(_old_versions(), assessability="assessability-rules/v1") # valor viejo NO elegible (p. ej. un perfil previo a G1: otra migracion, ya aplicada)
    assert svc.classify_currentness("sig", unknown_old, "sig", v).targeted is None


def test_the_six_public_states_are_unchanged_and_stale_reasons_are_not_states():
    assert svc.STATES == (svc.CURRENT, svc.STALE, svc.NOT_YET_ASSESSED, svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA)
    assert not set(svc.STALE_REASONS) & set(svc.STATES)


def test_the_registry_is_small_explicit_and_uses_the_real_constants_as_target():
    assert set(svc.EVIDENCE_MIGRATIONS) == {OLD_MIG, MIG, LIN_MIG, "evidence_confidence_v3", "evidence_leaf_materialization_v4", "signals_layer_boundary_v4"}                        # ambas explicitas: un perfil historico no queda varado porque un cliente ya migro
    assert TARGET.target_keys == ("assessability", "confidence", "canonical_evidence", "closure") and TARGET.closure_policy == svc.TARGETED_CLOSURE_EXPANSION
    assert OLD_VALUES == {"assessability": "assessability-rules/v2", "confidence": "confidence-cuts/v1", "canonical_evidence": "canonical-evidence/v1"}
    assert TARGET.eligible_old["closure"] == frozenset({None})
    v = _cur_versions()
    assert (v["assessability"], v["confidence"], v["canonical_evidence"], v["closure"]) == ("assessability-rules/v4", "confidence-cuts/v3", "canonical-evidence/v4", "closure-rules/v1")
    assert v["signals"] == "signal-rules/v4" and v["finding_evidence"] == "finding-evidence/v2" and v["facts_profile"] == "modeling_assessment.profile/v1"     # lo no relacionado NO se subio


# ═══════════════ 2. unidades con dobles (sin motor ni disco) ═══════════════
def _prof(*, closure=("A", "B"), am="AM", cov=0.9, covi=0.95, conf="MEDIUM", reviews=1, oq=2, ingestion="PARTIALLY_ASSESSED", with_ce=False, leaves=(), reps=()):
    p = {"summary": {"coverage": {"strict": cov, "inclusive": covi}, "confidence": {"level": conf}, "context_required": {"open_questions": oq}},
         "scope": {"terminal_analytic_model": am, "root_object": am, "root_type": "analytic-model", "closure_artifacts": list(closure)},
         "review_required": [{"id": "CROSS_LAYER_REDERIVATION", "occurrence_count": reviews,
                              "occurrences": [{"values": {"provider": "P", "consumer": "C", "fields": ["F"]}}]}] if reviews else [],
         "areas": [{"id": "INGESTION", "assessability": ingestion, "finding_groups": ["G1"]}, {"id": "PROPAGATION", "assessability": "ASSESSED", "finding_groups": []}]}
    if with_ce:
        p["canonical_evidence"] = {"version": "canonical-evidence/v3", "leaf_provenance": list(leaves), "assembly_topology": {"count": 0, "artifacts": []},
                                   "expression_repetition": {"count": 0, "groups": []}, "derivation_semantics": {"repeated_derivations": list(reps), "relation_counts": {}, "fields": []},
                                   "closure": {"rules": "closure-rules/v1", "plane_policy": "MODELING_MEMBERSHIP",
                                               "reference_resolution": {"members_total": len(closure), "counts": {"RESOLVED": 3, "UNRESOLVED": 0, "EXCLUDED_BY_PLANE": 0, "AMBIGUOUS": 0}, "platform_provided_total": 0,
                                                                       "members_with_open_references": []},
                                               "plane_context": {"flow_produced_members": [], "added_vs_loader_file_flag": [], "removed_vs_loader_file_flag": [], "loader_file_flag_closure_size": len(closure)}},
                                   "published_contract": {"associations": {"aggregates": {"total": 1, "by_category": {"BUSINESS_DIMENSION": 1}}}, "fields": {"measure_type_composition": {"signature_count": 2, "family_count": 1}}},
                                   "harmonization_contract": {"artifacts": [{"character": {"value": "UNKNOWN"}}]}, "expression_occurrences": {"counts": {"occurrences": 4}},
                                   "branch_reconvergence": {"counts": {"pairs": 1}}, "view_complexity": {"views": [{"view_type": "SQL"}], "count": 1},
                                   "physical_source_boundaries": {"leaves": [], "boundaries_by_class": []}, "naming_evidence": {"taxonomy": []}, "processing_flow_properties": {"flows": [], "counts": {"flows": 0}},
                                   "published_field_lineage": None, "source_contract_classes": {"leaves": [], "by_class": []}}
    return p


def _status(entry, state, profile=None, *, reason=None, targeted=None, am=None):
    am = am or entry.artefactos[0]
    return svc.AssessmentStatus(state, entry.entry_id, entry.cliente, am if state != svc.NOT_APPLICABLE else None, profile=profile,
                                stale_reason=reason, stale_targeted=targeted, stale_version_diff=tuple(sorted(TARGET.target_keys)) if reason else ())


def _stale(entry, reason=svc.STALE_VERSION_ONLY, targeted=MIG, am=None):
    return _status(entry, svc.STALE, _prof(), reason=reason, targeted=targeted, am=am)


class Fakes:
    def __init__(self, statuses, durable=svc.DURABLE_REMOTE, fail=(), fail_commit=()):
        self.statuses, self.durable, self.fail, self.fail_commit = statuses, durable, set(fail), set(fail_commit)
        self.regenerated, self.committed, self.retried = [], [], []

    def status_for(self, e):
        return self.statuses[e.entry_id]

    def regenerate(self, e, migration):
        self.regenerated.append((e.entry_id, migration))
        if e.entry_id in self.fail:
            raise svc.EnrichmentError("CLOSURE_DRIFT")
        leaf = {"leaf": "L", "assessability": "ASSESSED", "provenance_state": "ESTABLISHED"}
        rep = {"field": "F", "provider": "P", "consumer": "C", "relation": "CHAINED_REFINEMENT", "relation_reason": "CONSUMER_READS_DERIVED_PROVIDER_FIELD",
               "exposed_by_root": True, "exposure_basis": "SAME_NAME_IN_ROOT_CONTRACT"}
        return SimpleNamespace(client=e.cliente, terminal_am="AM", profile=_prof(cov=1.0, covi=1.0, conf="HIGH", ingestion="ASSESSED", with_ce=True, leaves=[leaf], reps=[rep]),
                               envelope={"marker": e.entry_id}, previous_signature="sig")

    def commit(self, cand):
        if cand.envelope["marker"] in self.fail_commit:
            raise svc.EnrichmentError("PREVIOUS_CHANGED_DURING_RUN")
        self.committed.append(cand)
        return self.durable

    def retry(self, client, am):
        self.retried.append((client, am))
        return False

    def run(self, entries, apply=False, migration=MIG, **kw):
        kw.setdefault("remote_enabled", ALWAYS)
        kw.setdefault("verify_remote", lambda client, am: svc.REMOTE_CONFIRMED)
        return bf.migrate_stale_evidence(entries, migration, apply=apply, status_for=self.status_for, regenerate=self.regenerate, commit=self.commit, retry_remote=self.retry,
                                         governed_state_present=ALWAYS, **kw)


def _set(states):
    es = [_entry(f"e{i}", am=f"AM_{i}") for i, _ in enumerate(states)]
    return es, {e.entry_id: (st(e) if callable(st) else st) for e, st in zip(es, states)}


def test_eligibility_is_targeted_version_only_stale_and_every_other_case_is_reported_apart():
    cases = [lambda e: _stale(e),                                                                         # 0 elegible
             lambda e: _stale(e, svc.STALE_INPUT_CHANGED, None),                                         # 1
             lambda e: _stale(e, svc.STALE_VERSION_AND_INPUT, None),                                     # 2
             lambda e: _stale(e, svc.STALE_VERSION_ONLY, None),                                          # 3 untargeted
             lambda e: _status(e, svc.CURRENT, _prof()),                                                 # 4
             lambda e: _status(e, svc.NOT_YET_ASSESSED),                                                 # 5
             lambda e: _status(e, svc.FAILED, _prof()),                                                  # 6
             lambda e: _status(e, svc.INCOMPATIBLE_SCHEMA),                                              # 7
             lambda e: _status(e, svc.NOT_APPLICABLE)]                                                   # 8
    es, sts = _set(cases)
    fk = Fakes(sts)
    out = fk.run(es, apply=False)
    reasons = [r["reason"] for r in out["records"]]
    assert reasons == [None, bf.INPUT_CHANGED_STALE, bf.VERSION_AND_INPUT_CHANGED, bf.VERSION_ONLY_UNTARGETED, bf.SKIPPED_ALREADY_CURRENT,
                       bf.SKIPPED_NOT_YET_ASSESSED, bf.SKIPPED_FAILED_STATE, bf.SKIPPED_INCOMPATIBLE, bf.SKIPPED_NOT_APPLICABLE]
    assert fk.regenerated == [("e0", MIG)]                                                               # solo el elegible se regenera
    c = out["counts"]
    assert (c["eligible"], c["version_only_stale"], c["version_only_untargeted"], c["input_changed_stale"], c["version_and_input_changed"], c["current"],
            c["not_yet_assessed"], c["failed_state"], c["incompatible"], c["not_applicable"], c["would_regenerate"], c["remaining_stale_not_migrated"]) == (
        1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3)


def test_entries_pointing_to_the_same_governed_assessment_are_handled_once():
    a, b = _entry("a1", am="AM_X"), _entry("a2", am="AM_X")
    fk = Fakes({"a1": _stale(a, am="AM_X"), "a2": _stale(b, am="AM_X")})
    out = fk.run([a, b], apply=False)
    assert len(out["records"]) == 1 and out["records"][0]["entry_ids"] == ["a1", "a2"] and fk.regenerated == [("a1", MIG)]


def test_dry_run_reports_the_old_new_evidence_delta_without_writing_and_never_dumps_profiles():
    es, sts = _set([lambda e: _stale(e)])
    fk = Fakes(sts)
    out = fk.run(es, apply=False)
    rec = out["records"][0]
    d = rec["delta"]
    assert rec["action"] == bf.WOULD_REGENERATE and fk.committed == [] and out["dry_run"] and not out["apply"]
    assert rec["stale_reason"] == svc.STALE_VERSION_ONLY and d["pool_signature_same"] is True
    assert set(d["versions"]) == set(TARGET.target_keys) and d["versions"]["assessability"]["new"] == "assessability-rules/v4" and d["versions"]["closure"] == {"old": None, "new": "closure-rules/v1"}
    assert d["coverage_strict"] == {"old": 0.9, "new": 1.0, "changed": True} and d["coverage_inclusive"]["new"] == 1.0
    assert d["confidence"]["new"] == "HIGH" and d["areas"]["INGESTION"]["state"] == {"old": "PARTIALLY_ASSESSED", "new": "ASSESSED"}
    assert d["canonical_evidence"]["old"] == "absent" and d["canonical_evidence"]["new"] == "canonical-evidence/v3"
    assert set(d["canonical_evidence"]["sections_added"]) >= {"closure", "published_contract", "harmonization_contract", "expression_occurrences", "branch_reconvergence", "view_complexity"}
    assert d["leaf_provenance"]["new"]["established"] == 1 and d["leaf_provenance"]["old"]["leaves"] == 0
    assert d["closure"] == {"policy": "TARGETED_CLOSURE_EXPANSION", "old": 2, "new": 2, "terminal_am_same": True, "root_identity_same": True, "added": [], "removed": [], "drift": False}
    assert d["closure_reference_resolution"]["members_total"] == 2 and d["published_contract"]["associations"] == 1 and d["branch_reconvergence"] == {"pairs": 1}
    assert d["view_complexity"] == {"views": 1, "by_view_type": [{"value": "SQL", "count": 1}]} and d["harmonization_contract"] == [{"value": "UNKNOWN", "count": 1}]
    assert "fact_index" not in json.dumps(out) and "areas" in d


def test_the_presence_check_reads_canonical_evidence_content_never_fact_index_membership():
    old = _prof()
    old["fact_index"] = [{"fact": "propagation.expression_occurrences@closure"}, {"fact": "am.closure_plane_context@closure"}]      # nombres de hecho ya presentes
    cand = SimpleNamespace(profile=_prof(with_ce=True, leaves=[]))
    d = bf.compare_evidence_migration(old, cand, MIG, _old_versions(), _cur_versions())
    assert d["canonical_evidence"]["old"] == "absent" and "expression_occurrences" in d["canonical_evidence"]["sections_added"]


def test_apply_commits_through_the_shared_safe_path_and_one_failure_does_not_stop_the_batch():
    es, sts = _set([lambda e: _stale(e), lambda e: _stale(e), lambda e: _stale(e)])
    fk = Fakes(sts, fail={"e1"}, fail_commit={"e2"})
    out = fk.run(es, apply=True)
    assert [r["action"] for r in out["records"]] == [bf.REGENERATED, bf.FAILED_ACTION, bf.FAILED_ACTION]
    assert [r["reason"] for r in out["records"][1:]] == ["CLOSURE_DRIFT", "PREVIOUS_CHANGED_DURING_RUN"]
    assert len(fk.committed) == 1 and out["counts"]["failed"] == 2 and out["counts"]["regenerated_remote"] == 1


def test_durability_semantics_and_the_restart_rule_are_reused():
    es, sts = _set([lambda e: _stale(e)])
    remote = Fakes(sts, durable=svc.DURABLE_REMOTE).run(es, apply=True)
    assert remote["records"][0]["durable"] == "remote" and remote["restart"] == bf.RESTART_SAFE and remote["complete"] and remote["restart_safe"]
    local = Fakes(sts, durable=svc.DURABLE_LOCAL).run(es, apply=True)
    assert local["records"][0]["remote_retry"] == "NOT_CONFIRMED" and local["restart"] == bf.RESTART_BLOCKED and not local["complete"]
    mem = Fakes(sts, durable=svc.DURABLE_MEMORY).run(es, apply=True)
    assert mem["records"][0]["action"] == bf.FAILED_ACTION and mem["counts"]["failed"] == 1 and "registro previo conservado" in mem["records"][0]["reason"]


def test_already_current_records_have_their_remote_durability_verified_before_declaring_the_restart_safe():
    es, sts = _set([lambda e: _status(e, svc.CURRENT, _prof(with_ce=True))])
    ok = Fakes(sts).run(es, apply=True)
    assert ok["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT and ok["restart"] == bf.RESTART_SAFE
    pending = Fakes(sts).run(es, apply=True, verify_remote=lambda c, a: svc.REMOTE_PENDING)
    assert pending["restart"] == bf.RESTART_BLOCKED and pending["counts"]["pending_remote"] == 1 and not pending["complete"]
    unver = Fakes(sts).run(es, apply=False, verify_remote=lambda c, a: svc.REMOTE_UNVERIFIABLE)
    assert unver["restart"] == bf.RESTART_BLOCKED


def test_remaining_stale_not_migrated_never_make_the_restart_unsafe_but_are_reported():
    es, sts = _set([lambda e: _stale(e, svc.STALE_INPUT_CHANGED, None), lambda e: _status(e, svc.CURRENT, _prof(with_ce=True))])
    out = Fakes(sts).run(es, apply=True)
    assert out["counts"]["remaining_stale_not_migrated"] == 1 and out["restart"] == bf.RESTART_SAFE


def test_a_wrong_environment_is_reported_not_disguised_as_nothing_to_do():
    es, sts = _set([lambda e: _status(e, svc.NOT_YET_ASSESSED)])
    out = Fakes(sts).run(es)
    assert any("ENVIRONMENT_NO_GOVERNED_STATE" in w for w in out["environment_warnings"])
    assert any("NO_CATALOG_ENTRIES" in w for w in Fakes({}).run([])["environment_warnings"])


# ═══════════════ 3. CLI ═══════════════
@pytest.mark.parametrize("argv", [["--regenerate-stale-evidence", MIG],                                          # ni --dry-run ni --apply
                                  ["--regenerate-stale-evidence", MIG, "--dry-run", "--apply"],                  # ambos
                                  ["--regenerate-stale-evidence", "nope", "--dry-run"],                          # migración desconocida
                                  ["--regenerate-stale-evidence", MIG, "--enrich-current-missing", "view_type", "--dry-run"],   # excluyente con el otro modo
                                  ["--apply"]])                                                                  # --apply sin modo
def test_the_cli_contract_fails_safely_with_zero_writes(argv, monkeypatch):
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: pytest.fail("ni siquiera se lee el Catálogo"))
    monkeypatch.setattr(svc, "commit_regenerated", lambda *a, **k: pytest.fail("cero escrituras"))
    monkeypatch.setattr(svc, "commit_stale_migration", lambda *a, **k: pytest.fail("cero escrituras"))
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("cero escrituras"))
    with pytest.raises(SystemExit) as exc:
        bf.main(argv)
    assert exc.value.code == 2


def test_cli_modes_dispatch_and_the_client_scope_applies(monkeypatch):
    entries = [_entry("a1", "Acme"), _entry("b1", "Beta"), _entry("a2", "Acme", "AM_2")]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (None, entries))
    seen = []
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda es, name, apply=False, **k: seen.append(("migrate", [e.entry_id for e in es], name, apply)) or
                        {"counts": {"failed": 0}, "environment_warnings": []})
    monkeypatch.setattr(bf, "enrich_current", lambda *a, **k: pytest.fail("no es este modo"))
    assert bf.main(["--regenerate-stale-evidence", MIG, "--client", "acme", "--dry-run"]) == 0
    assert bf.main(["--regenerate-stale-evidence", MIG, "--apply"]) == 0
    assert seen == [("migrate", ["a1", "a2"], MIG, False), ("migrate", ["a1", "b1", "a2"], MIG, True)]


def test_the_default_backfill_and_the_view_type_enrichment_are_unchanged(monkeypatch):
    entries = [_entry("a")]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (None, entries))
    spy = []
    monkeypatch.setattr(bf, "backfill", lambda es, dry_run=False, **k: spy.append(("backfill", dry_run)) or {"generated": [], "skipped": [], "failed": []})
    monkeypatch.setattr(bf, "enrich_current", lambda es, name, apply=False, **k: spy.append(("enrich", name, apply)) or {"counts": {"failed": 0}, "environment_warnings": []})
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda *a, **k: pytest.fail("sin el flag explícito no se migra"))
    assert bf.main(["--dry-run"]) == 0 and bf.main(["--enrich-current-missing", "view_type", "--dry-run"]) == 0
    assert spy == [("backfill", True), ("enrich", "view_type", False)]


def test_the_migration_path_never_extracts_restores_or_uses_credentials():
    src = inspect.getsource(bf.migrate_stale_evidence) + inspect.getsource(svc.regenerate_stale_evidence) + inspect.getsource(svc.commit_stale_migration) \
        + inspect.getsource(svc._regenerate_core) + inspect.getsource(svc.migration_postcheck)
    import ast
    import textwrap
    idents: set[str] = set()                                                                                   # identificadores reales: docstrings y comentarios no cuentan
    for fn in (bf.migrate_stale_evidence, svc.regenerate_stale_evidence, svc.commit_stale_migration, svc._regenerate_core, svc.migration_postcheck):
        for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
            if isinstance(node, ast.Name):
                idents.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                idents.add(node.attr.lower())
    for forbidden in ("run_export", "exportrequest", "restore", "restore_path", "sharepoint", "oauth", "token", "client_secret", "spaces_key", "spaces_secret"):
        assert forbidden not in idents, forbidden
    assert "datasphere_publication" not in src and "datasphere_assessment_service" not in src


def test_the_two_regeneration_modes_share_one_safe_core():
    assert "_regenerate_core" in inspect.getsource(svc.regenerate_in_memory) and "_regenerate_core" in inspect.getsource(svc.regenerate_stale_evidence)
    assert "_regenerate_record" in inspect.getsource(bf.enrich_current) and "_regenerate_record" in inspect.getsource(bf.migrate_stale_evidence)
    assert "commit_regenerated" in inspect.getsource(svc.commit_stale_migration)


# ═══════════════ 4. integración con el servicio real (pool sintético, almacén gobernado temporal) ═══════════════
def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite(path, mutate):
    env = json.loads(path.read_text(encoding="utf-8"))
    mutate(env)
    env["assessment_artifact_signature"] = svc._artifact_signature(env["profile_json"]) if env.get("profile_json") is not None else None
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")


def _downgrade(env):
    """Deja el sobre como lo dejo el motor ANTERIOR al evidence release (el estado real de produccion): canonical_evidence v1 (cuatro secciones), assessability v2, confidence v1,
    sin la clave ``closure``."""
    from tools.catalog.modeling_assessment.canonical_sections import V2_SECTIONS, V3_SECTIONS, V4_SECTIONS
    prof = env["profile_json"]
    for sec in (*V2_SECTIONS, *V3_SECTIONS, *V4_SECTIONS):
        prof["canonical_evidence"].pop(sec, None)
    prof["canonical_evidence"]["version"] = "canonical-evidence/v1"
    prof["versions"].update(OLD_VALUES)
    prof["versions"].pop("closure", None)
    env["assessment_versions"] = dict(prof["versions"])


def _make_old(world, *, old_versions=True, input_changed=False, extra=None):
    """Genera el assessment con el motor y lo deja como uno de la versión ANTERIOR del contrato de evidencia (re-firmado, como lo habría dejado el motor viejo)."""
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)

    def mutate(env):
        if old_versions:
            _downgrade(env)
        if input_changed:
            env["input_pool_signature"] = "0" * 64
            env.pop("input_closure_signature", None)                 # sobre previo a la firma de dependencias: incierto => STALE explícito
        if extra:
            extra(env)
    _rewrite(path, mutate)
    restart()
    return path


def _run(world, apply=False, **kw):
    return bf.migrate_stale_evidence([world.acme], MIG, apply=apply, governed_state_present=ALWAYS, **kw)


def test_an_old_governed_profile_is_a_targeted_version_only_stale_with_the_real_status_path(world):
    _make_old(world)
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_ONLY and st.stale_targeted == MIG
    assert st.stale_version_diff == tuple(sorted(TARGET.target_keys)) and st.profile is not None


@pytest.mark.parametrize("kw,expect_state,expect_reason,expect_targeted", [
    ({"old_versions": False, "input_changed": True}, svc.STALE, svc.STALE_INPUT_CHANGED, None),
    ({"old_versions": True, "input_changed": True}, svc.STALE, svc.STALE_VERSION_AND_INPUT, None)])
def test_input_changed_and_both_are_never_eligible_and_are_reported_apart(world, kw, expect_state, expect_reason, expect_targeted):
    path = _make_old(world, **kw)
    st = svc.status_for_entry(world.acme)
    assert (st.state, st.stale_reason, st.stale_targeted) == (expect_state, expect_reason, expect_targeted)
    before = _digest(path)
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["action"] == bf.SKIP and rec["reason"] in (bf.INPUT_CHANGED_STALE, bf.VERSION_AND_INPUT_CHANGED) and out["counts"]["eligible"] == 0
    assert _digest(path) == before and files(world) == [path]


def test_an_untargeted_version_difference_is_not_eligible(world):
    path = _make_old(world, extra=lambda env: (env["assessment_versions"].update(identity="identity-rules/v0"), env["profile_json"]["versions"].update(identity="identity-rules/v0")))
    st = svc.status_for_entry(world.acme)
    assert st.stale_reason == svc.STALE_VERSION_ONLY and st.stale_targeted is None and "identity" in st.stale_version_diff
    before = _digest(path)
    out = _run(world, apply=True)
    assert out["records"][0]["reason"] == bf.VERSION_ONLY_UNTARGETED and out["counts"]["version_only_untargeted"] == 1 and _digest(path) == before


def test_current_not_yet_assessed_failed_incompatible_and_not_applicable_are_skipped(world):
    out = _run(world)                                                                                          # NOT_YET_ASSESSED
    assert out["records"][0]["reason"] == bf.SKIPPED_NOT_YET_ASSESSED
    svc.generate(world.acme)
    restart()
    assert _run(world)["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT                                  # CURRENT
    (path,) = files(world)
    _rewrite(path, lambda env: env.update(assessment_schema_version="otro/v9"))
    restart()
    assert svc.status_for_entry(world.acme).state == svc.INCOMPATIBLE_SCHEMA
    assert _run(world)["records"][0]["reason"] == bf.SKIPPED_INCOMPATIBLE                                      # INCOMPATIBLE_SCHEMA
    path.unlink()
    svc.reset_memory_cache()
    svc._store_envelope(svc._envelope("Acme", "4AM_ACME", status="FAILED", profile=None, pool_signature=None, versions=None, error_code="GENERATION_ERROR"))
    restart()
    assert svc.status_for_entry(world.acme).state == svc.FAILED
    assert _run(world)["records"][0]["reason"] == bf.SKIPPED_FAILED_STATE                                      # FAILED
    solo = bf.migrate_stale_evidence([world.entries[1]], MIG, governed_state_present=ALWAYS)                   # otro cliente sin AM terminal
    assert solo["records"][0]["reason"] == bf.SKIPPED_NOT_APPLICABLE and solo["counts"]["eligible"] == 0


def test_dry_run_runs_the_real_engine_in_memory_writes_nothing_and_reports_the_evidence_delta(world, monkeypatch):
    path = _make_old(world)
    before = _digest(path)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: pytest.fail("sin extracción de Datasphere"))
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("dry-run: cero escrituras"))
    calls = []
    real = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: calls.append(1) or real(*a, **k))               # el motor canónico SE EJECUTA
    out = _run(world, apply=False)
    rec = out["records"][0]
    d = rec["delta"]
    assert len(calls) == 1 and rec["action"] == bf.WOULD_REGENERATE and out["counts"]["would_regenerate"] == 1 and out["restart"] == bf.RESTART_DRY_RUN
    assert d["pool_signature_same"] and d["canonical_evidence"]["old"] == "canonical-evidence/v1" and d["canonical_evidence"]["new"] == "canonical-evidence/v4"
    assert "closure" in d["canonical_evidence"]["sections_added"] and "published_contract" in d["canonical_evidence"]["sections_added"]
    assert d["versions"]["canonical_evidence"] == {"old": "canonical-evidence/v1", "new": "canonical-evidence/v4"} and d["versions"]["closure"] == {"old": None, "new": "closure-rules/v1"}
    assert d["versions"]["assessability"] == {"old": "assessability-rules/v2", "new": "assessability-rules/v4"}
    assert d["closure"]["drift"] is False and d["closure"]["added"] == [] and d["closure_reference_resolution"]["members_total"] >= 1
    assert _digest(path) == before and len(files(world)) == 1                                                  # CERO escrituras
    restart()
    assert svc.status_for_entry(world.acme).state == svc.STALE


def test_apply_replaces_the_same_governed_record_and_the_result_is_current_and_valid(world):
    path = _make_old(world)
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["action"] == bf.REGENERATED and rec["durable"] == svc.DURABLE_LOCAL and out["restart"] == bf.RESTART_BLOCKED       # sin Spaces: local
    assert files(world) == [path]                                                                              # la MISMA llave: sin duplicados
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.CURRENT and st.stale_reason is None and st.profile["versions"] == svc.current_assessment_versions()
    ma_profile.validate_assessment(st.profile)
    assert st.profile["canonical_evidence"]["version"] == "canonical-evidence/v4" and "closure" in st.profile["canonical_evidence"]


def test_it_is_idempotent_a_second_run_skips_and_writes_nothing(world, monkeypatch):
    path = _make_old(world)
    _run(world, apply=True)
    after_first = _digest(path)
    restart()
    engine = []
    real = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: engine.append(1) or real(*a, **k))
    out = _run(world, apply=True)
    assert out["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT and out["counts"]["eligible"] == 0 and out["counts"]["regenerated_local"] == 0
    assert engine == [] and _digest(path) == after_first and files(world) == [path]
    assert _run(world, apply=False)["counts"]["current"] == 1


def _expect_preserved(world, path, reason, **kw):
    before = _digest(path)
    out = _run(world, apply=True, **kw)
    rec = out["records"][0]
    assert rec["action"] == bf.FAILED_ACTION and out["counts"]["failed"] == 1 and out["counts"]["regenerated_local"] == 0 and rec["reason"].startswith(reason), rec["reason"]
    assert _digest(path) == before and files(world) == [path]                                                  # el registro previo NO cambió ni se duplicó
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_ONLY                                 # y sigue siendo el STALE histórico, no un FAILED
    return rec


def _patch_engine(monkeypatch, mutate, skip_validation=False):
    real = svc._engine_assess

    def fake(*a, **k):
        doc, meta = real(*a, **k)
        mutate(doc)
        return doc, meta
    monkeypatch.setattr(svc, "_engine_assess", fake)
    if skip_validation:                                                                                          # para ejercitar la postcondición propia, no la validación de forma
        orig = ma_profile.validate_assessment
        monkeypatch.setattr(svc.ma_profile, "validate_assessment", lambda doc: None if doc.get("_tampered") else orig(doc))


def test_engine_failure_preserves_the_previous_record(world, monkeypatch):
    path = _make_old(world)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom /secret/path")))
    rec = _expect_preserved(world, path, "GENERATION_ERROR")
    assert "secret" not in json.dumps(rec)


def test_validation_failure_preserves_the_previous_record(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: d.pop("summary"))
    _expect_preserved(world, path, "INVALID_PROFILE")


def test_the_candidate_must_declare_the_current_versions(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: d["versions"].update(signals="signal-rules/v9"))
    _expect_preserved(world, path, "CANDIDATE_VERSIONS_NOT_CURRENT")


def test_canonical_evidence_is_required_and_validated_before_any_write(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: (d.update(_tampered=True), d.pop("canonical_evidence")), skip_validation=True)
    _expect_preserved(world, path, "CANONICAL_EVIDENCE_MISSING")
    _patch_engine(monkeypatch, lambda d: (d.update(_tampered=True), d.__setitem__("canonical_evidence", {"version": "canonical-evidence/v2", "leaf_provenance": [{"leaf": "x"}]})), skip_validation=True)
    _expect_preserved(world, path, "CANONICAL_EVIDENCE_INVALID")


def test_the_regenerated_pool_signature_must_equal_the_local_pool_signature(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: d["scope"].update(pool_signature="f" * 64))
    _expect_preserved(world, path, "POOL_SIGNATURE_MISMATCH")


def test_the_terminal_am_identity_must_be_stable(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: d["scope"].update(terminal_analytic_model="OTRO_AM"))
    _expect_preserved(world, path, "TERMINAL_AM_CHANGED")


def test_closure_drift_is_a_hard_failure_that_preserves_the_old_profile(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: d["scope"].update(closure_artifacts=d["scope"]["closure_artifacts"][:-1]))
    _expect_preserved(world, path, "CLOSURE_DRIFT")
    restart()
    _patch_engine(monkeypatch, lambda d: d["scope"]["closure_artifacts"].append("EXTRA_ARTEFACTO"))
    _expect_preserved(world, path, "CLOSURE_DRIFT")


def test_closure_identity_is_canonical_not_incidental_list_order():
    a = {"scope": {"terminal_analytic_model": "AM", "root_object": "AM", "root_type": "analytic-model", "closure_artifacts": ["B", "A", "C", "A"]}}
    b = {"scope": {"terminal_analytic_model": "AM", "root_object": "AM", "root_type": "analytic-model", "closure_artifacts": ["C", "B", "A"]}}
    assert svc.closure_identity(a) == svc.closure_identity(b) == ("AM", "AM", "analytic-model", ("A", "B", "C"))
    assert svc.closure_identity({"scope": {}}) is None and svc.closure_identity(None) is None


def test_a_reordered_but_identical_closure_is_not_drift(world, monkeypatch):
    path = _make_old(world)
    _patch_engine(monkeypatch, lambda d: d["scope"]["closure_artifacts"].reverse())
    out = _run(world, apply=True)
    assert out["records"][0]["action"] == bf.REGENERATED and out["records"][0]["delta"]["closure"]["drift"] is False and files(world) == [path]


def test_a_disk_write_failure_reports_memory_and_keeps_the_previous_good_state(world, monkeypatch):
    path = _make_old(world)
    monkeypatch.setattr(svc.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    rec = _expect_preserved(world, path, "NOT_PERSISTED_MEMORY_ONLY")
    assert rec["durable"] == svc.DURABLE_MEMORY and not [p for p in path.parent.iterdir() if p.name.startswith(".tmp-")]


def test_a_record_changed_during_the_run_is_not_overwritten(world, monkeypatch):
    path = _make_old(world)
    real_regen = svc.regenerate_stale_evidence

    def regen_then_ui_regenerates(entry, migration):
        cand = real_regen(entry, migration)
        svc.generate(entry)                                                                                      # alguien regeneró por la UI durante la ventana
        return cand
    out = bf.migrate_stale_evidence([world.acme], MIG, apply=True, regenerate=regen_then_ui_regenerates, governed_state_present=ALWAYS)
    rec = out["records"][0]
    assert rec["reason"] == bf.SKIPPED_ALREADY_CURRENT and rec["action"] == bf.SKIP and out["counts"]["failed"] == 0         # se tolera: ya está CURRENT en el destino
    assert svc.status_for_entry(world.acme).state == svc.CURRENT and files(world) == [path]


def test_an_already_current_target_manually_regenerated_during_the_window_is_skipped_and_its_durability_verified(world, spaces_rw):
    _make_old(world)
    svc.generate(world.acme)                                                                                     # regeneración manual por la UI: CURRENT y remoto
    restart()
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["reason"] == bf.SKIPPED_ALREADY_CURRENT and rec["remote_state"] == svc.REMOTE_CONFIRMED and out["restart"] == bf.RESTART_SAFE and out["complete"]


def test_remote_persistence_is_reported_as_remote_and_equals_the_local_file(world, spaces):
    path = _make_old(world)
    assert remote_store.push_path(path)                                                                          # el viejo histórico ya está en Spaces
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["durable"] == svc.DURABLE_REMOTE and out["restart"] == bf.RESTART_SAFE and out["complete"]
    assert spaces.objects[_remote_key(spaces, path)] == path.read_bytes()                                         # el objeto remoto ES el reemplazo


def test_pending_remote_survives_later_invocations_and_the_restart_stays_blocked_until_exact_confirmation(world, spaces_rw, monkeypatch):
    spaces = spaces_rw
    path = _make_old(world)
    assert remote_store.push_path(path)
    key = _remote_key(spaces, path)
    old_remote = spaces.objects[key]
    spaces.fail_uploads = True                                                                                   # 1) el local se escribe; el PUT remoto falla
    first = _run(world, apply=True)
    assert first["records"][0]["durable"] == svc.DURABLE_LOCAL and first["restart"] == bf.RESTART_BLOCKED and spaces.objects[key] == old_remote
    restart()                                                                                                    # 2) otra invocación: omite regenerar PERO sabe que el remoto es el viejo
    engine = []
    real = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: engine.append(1) or real(*a, **k))
    for apply in (False, True):
        again = _run(world, apply=apply)
        rec = again["records"][0]
        assert rec["reason"] == bf.SKIPPED_ALREADY_CURRENT and rec["remote_state"] == svc.REMOTE_PENDING
        assert again["restart"] == bf.RESTART_BLOCKED and not again["restart_safe"] and again["counts"]["pending_remote"] == 1 and not again.get("complete")
    assert engine == [] and spaces.objects[key] == old_remote
    spaces.fail_uploads = False                                                                                  # 3) se recupera: dry-run NO sube; apply empuja el MISMO archivo
    assert _run(world, apply=False)["restart"] == bf.RESTART_BLOCKED and spaces.objects[key] == old_remote
    done = _run(world, apply=True)
    assert done["records"][0]["remote_retry"] == "CONFIRMED" and done["restart"] == bf.RESTART_SAFE and done["complete"] and spaces.objects[key] == path.read_bytes()
    assert engine == [] and _run(world, apply=False)["restart"] == bf.RESTART_SAFE


def test_an_unverifiable_remote_never_counts_as_confirmed(world, spaces_rw, monkeypatch):
    _make_old(world)
    _run(world, apply=True)
    restart()
    monkeypatch.setattr(remote_store, "read_object_bytes", lambda p: (_ for _ in ()).throw(remote_store.RemoteReadError("GET irresoluble")))
    out = _run(world, apply=False)
    assert out["records"][0]["remote_state"] == svc.REMOTE_UNVERIFIABLE and out["restart"] == bf.RESTART_BLOCKED


def test_output_never_carries_credentials_or_filesystem_paths(world, spaces):
    _make_old(world)
    out = json.dumps(_run(world, apply=True), ensure_ascii=False)
    for secret in ("fake-key", "fake-secret", "fake.digitaloceanspaces.com", str(world.tmp), "governed-assessments"):
        assert secret not in out, secret


def test_client_scope_applies_only_to_the_requested_client(world):
    _make_old(world)
    out = bf.migrate_stale_evidence([world.entries[1]], MIG, governed_state_present=ALWAYS)
    assert out["counts"]["eligible"] == 0 and all(r["client"] == "Solo" for r in out["records"]) and len(files(world)) == 1


def test_a_missing_local_pool_blocks_the_migration_without_acquiring_anything(world, monkeypatch):
    path = _make_old(world)
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(world.tmp / "no-pool"))
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(world.tmp / "no-inbound"))
    restart()
    before = _digest(path)
    out = _run(world, apply=True)
    assert out["counts"]["eligible"] == 0 and out["counts"]["failed"] == 0 and _digest(path) == before            # sin pool no hay clasificación STALE/elegible: no se regenera
    assert out["records"][0]["reason"] in (bf.SKIPPED_NOT_YET_ASSESSED, bf.SKIPPED_FAILED_STATE)


# ═══════════════ 5. mensaje de STALE (único cambio visible) ═══════════════
def test_stale_notes_by_reason_never_claim_the_model_changed_for_a_version_only_stale():
    e = _entry("a")
    only = presenter.stale_note(_stale(e))
    assert "versión anterior de la metodología" in only and "El modelo cambió" not in only
    assert "modelo cambió" in presenter.stale_note(_stale(e, svc.STALE_INPUT_CHANGED, None)).lower()
    both = presenter.stale_note(_stale(e, svc.STALE_VERSION_AND_INPUT, None))
    assert "modelo cambió" in both and "versión anterior de la metodología" in both
    assert presenter.stale_note(_status(e, svc.STALE, _prof())) == presenter.STATE_NOTES[svc.STALE]                # sin causa conocida: la nota histórica
    assert presenter.stale_body(_stale(e)) != presenter.stale_body(_stale(e, svc.STALE_INPUT_CHANGED, None)) and presenter.stale_body(_status(e, svc.CURRENT, _prof())) is None


def _report_html(world):
    return client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text


def test_the_report_callout_says_the_right_thing_for_each_stale_reason(world):
    _make_old(world)                                                                                             # VERSION_ONLY
    html = _report_html(world)
    assert "Assessment desactualizado" in html and "versión anterior de la metodología" in html and "El modelo cambió desde la última evaluación" not in html
    assert "con la versión vigente de la metodología" in html
    restart()
    f = world.inbound / "Acme/views/1VR_RAW.json"                                                                # cambia la ENTRADA; las versiones siguen viejas ⇒ ambas
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A7"', 1), encoding="utf-8")
    both = _report_html(world)
    assert "El modelo cambió y la evaluación fue generada con una versión anterior" in both
    svc.reset_memory_cache()
    for p in files(world):
        p.unlink()
    restart()
    svc.generate(world.acme)                                                                                     # CURRENT con el pool ya modificado; ahora cambiamos de nuevo el pool
    restart()
    f.write_text(f.read_text(encoding="utf-8").replace('"A7"', '"A8"', 1), encoding="utf-8")
    changed = _report_html(world)
    assert "El modelo cambió desde la última evaluación" in changed and "versión anterior de la metodología" not in changed


# ═══════════════ 8. TARGETED_CLOSURE_EXPANSION (evidence release): el cierre solo puede CRECER por la correccion aprobada ═══════════════
def _expansion_case():
    """(doc nuevo del motor sobre un pool con una tabla producida por un flujo, versiones, previous-viejo-sin-esa-tabla)."""
    from tests._evidence_release_pool import AM, write_pool
    from tools.catalog.modeling_assessment import shadow as ma_shadow
    doc, _ = ma_shadow.assess(write_pool(bundled_df=True), "Acme", AM, "General")
    new = svc.closure_identity(doc)
    old = (new[0], new[1], new[2], tuple(a for a in new[3] if a != "2TR_VBAK"))
    return doc, old, new


def test_the_registry_declares_the_targeted_expansion_policy_and_the_equal_policy_is_untouched():
    assert TARGET.closure_policy == svc.TARGETED_CLOSURE_EXPANSION and svc.CLOSURE_EQUAL != svc.TARGETED_CLOSURE_EXPANSION


def test_a_closure_that_gains_only_a_flow_produced_table_read_by_modeling_is_accepted():
    doc, old, new = _expansion_case()
    svc._check_targeted_closure_expansion(old, new, doc)                          # no lanza
    assert svc.closure_delta(old, new) == (("2TR_VBAK",), ())


def test_an_identical_closure_is_accepted_unchanged_under_the_targeted_policy():
    doc, _old, new = _expansion_case()
    pc = doc["canonical_evidence"]["closure"]["plane_context"]
    pc["added_vs_loader_file_flag"], pc["loader_file_flag_closure_size"] = [], len(new[3])                   # una raiz sin tablas producidas por flujo
    svc._check_targeted_closure_expansion(new, new, doc)


@pytest.mark.parametrize("mutate", [
    lambda old, new: ((old[0], old[1], old[2], old[3] + ("ZZ_OLD_ONLY",)), new),                                  # el cierre viejo tenia algo que el nuevo ya no tiene
    lambda old, new: (old, ("OTRO_AM", new[1], new[2], new[3])),                                                   # cambia el AM terminal
    lambda old, new: (old, (new[0], "OTRA_RAIZ", new[2], new[3])),                                                 # cambia el objeto raiz
    lambda old, new: (old, (new[0], new[1], "view", new[3])),                                                      # cambia el tipo de raiz
])
def test_removals_and_root_changes_are_always_closure_drift(mutate):
    doc, old, new = _expansion_case()
    old, new = mutate(old, new)
    with pytest.raises(svc.EnrichmentError, match="CLOSURE_DRIFT"):
        svc._check_targeted_closure_expansion(old, new, doc)


@pytest.mark.parametrize("break_it", [
    lambda pc: pc["flow_produced_members"].clear(),                                                                # lo agregado no es un target de flujo leido por Modeling
    lambda pc: pc["flow_produced_members"][0].__setitem__("loader_file_flag", False),                              # la politica previa no lo habria excluido
    lambda pc: pc["flow_produced_members"][0].__setitem__("excluded_before", "OTHER"),
    lambda pc: pc["flow_produced_members"][0].__setitem__("semantic_providers", ["X"]),                            # no es una hoja
    lambda pc: pc["flow_produced_members"][0].__setitem__("semantic_consumers", ["NO_ESTABA_EN_EL_CIERRE_VIEJO"]), # nadie del cierre previo lo referenciaba
    lambda pc: pc.__setitem__("loader_file_flag_closure_size", 999),                                               # la politica previa reproducida NO da el cierre guardado
    lambda pc: pc.__setitem__("added_vs_loader_file_flag", ["2TR_VBAK", "OTRO"]),
    lambda pc: pc.__setitem__("removed_vs_loader_file_flag", ["X"]),
])
def test_an_unattributable_expansion_is_closure_drift(break_it):
    doc, old, new = _expansion_case()
    break_it(doc["canonical_evidence"]["closure"]["plane_context"])
    with pytest.raises(svc.EnrichmentError, match="CLOSURE_DRIFT"):
        svc._check_targeted_closure_expansion(old, new, doc)


def test_a_flow_itself_added_to_the_closure_is_drift_even_when_a_flow_produced_member_exists():
    doc, old, new = _expansion_case()
    new = (new[0], new[1], new[2], tuple(sorted(new[3] + ("DF_VBAK",))))
    pc = doc["canonical_evidence"]["closure"]["plane_context"]
    pc["added_vs_loader_file_flag"] = ["2TR_VBAK", "DF_VBAK"]
    with pytest.raises(svc.EnrichmentError, match="CLOSURE_DRIFT"):
        svc._check_targeted_closure_expansion(old, new, doc)


def test_the_postcheck_dispatches_on_the_registry_policy_and_a_closure_equal_target_still_rejects_any_growth(monkeypatch):
    doc, old, new = _expansion_case()
    versions = svc.current_assessment_versions()
    previous = {"profile_json": {"scope": {"terminal_analytic_model": new[0], "root_object": new[1], "root_type": new[2], "closure_artifacts": list(old[3])}}}
    svc.migration_postcheck(MIG, doc, previous, versions)                          # TARGETED: acepta
    strict = svc.MigrationTarget(**{**TARGET.__dict__, "closure_policy": svc.CLOSURE_EQUAL}) if hasattr(TARGET, "__dict__") else None
    if strict is None:                                                             # dataclass con slots
        import dataclasses
        strict = dataclasses.replace(TARGET, closure_policy=svc.CLOSURE_EQUAL)
    monkeypatch.setitem(svc.EVIDENCE_MIGRATIONS, "strict_probe", strict)
    with pytest.raises(svc.EnrichmentError, match="CLOSURE_DRIFT"):
        svc.migration_postcheck("strict_probe", doc, previous, versions)           # la misma expansion es drift bajo la politica global (no se debilito)


def test_the_expansion_is_reported_in_the_dry_run_delta_and_input_signature_semantics_do_not_change():
    from tests._evidence_release_pool import AM, write_pool
    doc, old, new = _expansion_case()
    old_prof = {"input_pool_signature": "sig", "scope": {"terminal_analytic_model": old[0], "root_object": old[1], "root_type": old[2], "closure_artifacts": list(old[3])},
                "versions": _old_versions(), "summary": {}, "fact_index": [], "canonical_evidence": None}
    cand = SimpleNamespace(profile=doc)
    d = bf.compare_evidence_migration(old_prof, cand, MIG, _old_versions(), _cur_versions())
    assert [a["artifact"] for a in d["closure"]["added"]] == ["2TR_VBAK"] and d["closure"]["added"][0]["basis"] == "READ_BY_SEMANTIC_MEMBER" and d["closure"]["removed"] == [] and d["closure"]["drift"] is False and d["closure"]["policy"] == "TARGETED_CLOSURE_EXPANSION"
    assert (d["closure"]["old"], d["closure"]["new"]) == (len(old[3]), len(new[3])) and d["pool_signature_same"] in (True, False)
    c = svc.classify_currentness("sig", _old_versions(), "sig", _cur_versions())
    assert c.reason == svc.STALE_VERSION_ONLY and c.targeted == MIG                                              # misma firma de entrada: VERSION_ONLY, no INPUT_CHANGED


# ═══════════════ 9. COMPATIBILIDAD: ambas migraciones explicitas, sin encadenamiento automatico ═══════════════
PRE_G1G3 = {"signals": "signal-rules/v2", "assessability": "assessability-rules/v1", "finding_evidence": "finding-evidence/v1", "confidence": "confidence-cuts/v1"}


def _pre_g1g3_versions():
    v = dict(_cur_versions())
    v.update(PRE_G1G3)
    v.pop("canonical_evidence", None)                                            # el perfil anterior a G1/G3 no traia canonical_evidence ni closure
    v.pop("closure", None)
    return v


def _downgrade_pre_g1g3(env):
    """Deja el sobre como lo dejaba el motor ANTERIOR a G1/G3 (sin canonical_evidence ni closure; signals v2, assessability v1, finding_evidence v1, confidence v1)."""
    prof = env["profile_json"]
    prof.pop("canonical_evidence", None)
    prof["versions"].update(PRE_G1G3)
    prof["versions"].pop("canonical_evidence", None)
    prof["versions"].pop("closure", None)
    env["assessment_versions"] = dict(prof["versions"])


def _make_pre_g1g3(world):
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)
    _rewrite(path, _downgrade_pre_g1g3)
    restart()
    return path


def test_the_classifier_assigns_each_historical_contract_to_exactly_its_own_migration():
    cur = _cur_versions()
    assert svc.targeted_migration(_pre_g1g3_versions(), cur) == OLD_MIG                       # 1: pre-G1/G3 -> evidence_g1_g3_v1
    assert svc.targeted_migration(_old_versions(), cur) == MIG                                # contrato intermedio (G1/G3 vigente en 11ce071) -> evidence_closure_contract_v2
    assert svc.targeted_migration(dict(cur), cur) is None                                     # CURRENT: ninguna
    mixed = dict(_pre_g1g3_versions(), signals=cur["signals"])                                # a mitad de camino: ninguna cubre EXACTAMENTE esa diferencia
    assert svc.targeted_migration(mixed, cur) is None
    assert OLD_TARGET.closure_policy == svc.TARGETED_CLOSURE_EXPANSION                        # ambas regeneran con el mismo motor


def test_a_pre_g1g3_profile_is_eligible_for_the_old_migration_and_not_directly_for_the_new_one():
    e = _entry("e0", am="AM_0")
    st = _status(e, svc.STALE, _prof(), reason=svc.STALE_VERSION_ONLY, targeted=OLD_MIG)
    old = Fakes({"e0": st}).run([e], migration=OLD_MIG)
    assert old["records"][0]["eligible"] is True and old["records"][0]["action"] == bf.WOULD_REGENERATE and old["counts"]["would_regenerate"] == 1
    fk = Fakes({"e0": st})
    new = fk.run([e], migration=MIG)
    rec = new["records"][0]
    assert not rec.get("eligible") and rec["reason"] == bf.OTHER_MIGRATION_APPLICABLE and rec["applicable_migration"] == OLD_MIG and rec["action"] == bf.SKIP
    assert fk.regenerated == [] and new["counts"]["eligible"] == 0 and new["counts"]["would_regenerate"] == 0 and new["counts"]["other_migration_applicable"] == 1
    assert new["counts"]["remaining_stale_not_migrated"] == 1                                 # no es un exito silencioso: queda reportado


def test_a_profile_at_the_intermediate_contract_skips_the_old_migration_and_says_which_one_applies():
    e = _entry("e0", am="AM_0")
    st = _status(e, svc.STALE, _prof(), reason=svc.STALE_VERSION_ONLY, targeted=MIG)
    fk = Fakes({"e0": st})
    out = fk.run([e], migration=OLD_MIG)
    rec = out["records"][0]
    assert not rec.get("eligible") and rec["reason"] == bf.OTHER_MIGRATION_APPLICABLE and rec["applicable_migration"] == MIG and fk.regenerated == []


def test_a_current_profile_skips_both_migrations_and_an_untargeted_one_is_still_untargeted():
    e = _entry("e0", am="AM_0")
    for mig in (OLD_MIG, MIG):
        fk = Fakes({"e0": _status(e, svc.CURRENT, _prof())})
        out = fk.run([e], migration=mig)
        assert out["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT and fk.regenerated == [] and out["counts"]["current"] == 1
        fk = Fakes({"e0": _status(e, svc.STALE, _prof(), reason=svc.STALE_VERSION_ONLY, targeted=None)})
        assert fk.run([e], migration=mig)["records"][0]["reason"] == bf.VERSION_ONLY_UNTARGETED and fk.regenerated == []


def test_the_new_migration_never_chains_the_old_one_on_a_real_pre_g1g3_governed_profile(world, monkeypatch):
    path = _make_pre_g1g3(world)
    before = _digest(path)
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_ONLY and st.stale_targeted == OLD_MIG
    calls = []
    real = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("cero escrituras"))
    out = bf.migrate_stale_evidence([world.acme], MIG, apply=True, governed_state_present=ALWAYS)          # --apply de la migracion NUEVA sobre un perfil pre-G1/G3
    rec = out["records"][0]
    assert rec["reason"] == bf.OTHER_MIGRATION_APPLICABLE and rec["applicable_migration"] == OLD_MIG and calls == [] and _digest(path) == before
    assert out["counts"]["regenerated_remote"] == out["counts"]["regenerated_local"] == 0 and out["counts"]["failed"] == 0


def test_the_old_migration_dry_run_writes_nothing_and_apply_lands_on_the_current_contract_in_one_explicit_step(world, monkeypatch):
    path = _make_pre_g1g3(world)
    before = _digest(path)
    with monkeypatch.context() as mp:
        mp.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("dry-run: cero escrituras"))
        dry = bf.migrate_stale_evidence([world.acme], OLD_MIG, apply=False, governed_state_present=ALWAYS)
    d = dry["records"][0]["delta"]
    assert dry["records"][0]["action"] == bf.WOULD_REGENERATE and _digest(path) == before
    assert d["versions"]["canonical_evidence"] == {"old": None, "new": "canonical-evidence/v4"} and d["versions"]["signals"] == {"old": "signal-rules/v2", "new": "signal-rules/v4"}
    assert d["closure"]["drift"] is False and d["closure"]["policy"] == "TARGETED_CLOSURE_EXPANSION"
    out = bf.migrate_stale_evidence([world.acme], OLD_MIG, apply=True, governed_state_present=ALWAYS)
    assert out["records"][0]["action"] == bf.REGENERATED and out["counts"]["failed"] == 0 and files(world) == [path]
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.CURRENT and st.profile["versions"] == svc.current_assessment_versions()                # el motor de este build solo emite el contrato vigente
    ma_profile.validate_assessment(st.profile)
    after = bf.migrate_stale_evidence([world.acme], MIG, apply=True, governed_state_present=ALWAYS)               # la migracion nueva ya no tiene nada que hacer
    assert after["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT and after["counts"]["eligible"] == 0
    again = bf.migrate_stale_evidence([world.acme], OLD_MIG, apply=True, governed_state_present=ALWAYS)          # idempotente
    assert again["records"][0]["reason"] == bf.SKIPPED_ALREADY_CURRENT


def test_an_intermediate_contract_profile_still_migrates_with_the_new_target_and_skips_with_the_old_one(world):
    path = _make_old(world)                                                                                      # contrato intermedio (el de produccion en 11ce071)
    st = svc.status_for_entry(world.acme)
    assert st.stale_targeted == MIG
    skipped = bf.migrate_stale_evidence([world.acme], OLD_MIG, apply=True, governed_state_present=ALWAYS)
    assert skipped["records"][0]["reason"] == bf.OTHER_MIGRATION_APPLICABLE and skipped["records"][0]["applicable_migration"] == MIG
    ok = bf.migrate_stale_evidence([world.acme], MIG, apply=True, governed_state_present=ALWAYS)
    assert ok["records"][0]["action"] == bf.REGENERATED and files(world) == [path]


def test_the_registry_has_no_chain_primitive_and_unknown_targets_stay_usage_errors(monkeypatch):
    assert not hasattr(svc, "chain_migrations") and not hasattr(bf, "migrate_chain")
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: pytest.fail("ni siquiera se lee el Catálogo"))
    with pytest.raises(SystemExit) as exc:
        bf.main(["--regenerate-stale-evidence", "evidence_chain_all", "--dry-run"])
    assert exc.value.code == 2


def test_the_cli_accepts_both_registered_targets_with_the_same_explicit_contract(monkeypatch):
    entries = [_entry("a1", "Acme")]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (None, entries))
    seen = []
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda es, name, apply=False, **k: seen.append((name, apply)) or {"counts": {"failed": 0}, "environment_warnings": []})
    assert bf.main(["--regenerate-stale-evidence", OLD_MIG, "--dry-run"]) == 0 and bf.main(["--regenerate-stale-evidence", MIG, "--apply"]) == 0
    assert seen == [(OLD_MIG, False), (MIG, True)]
    for argv in (["--regenerate-stale-evidence", OLD_MIG], ["--regenerate-stale-evidence", OLD_MIG, "--dry-run", "--apply"]):       # mismo contrato de uso seguro
        with pytest.raises(SystemExit) as exc:
            bf.main(argv)
        assert exc.value.code == 2


# ═══════════════ 8D.1d: migracion evidence_field_lineage_v3 (v2 -> v3; solo agrega evidencia) ═══════════════
LIN_TARGET = svc.EVIDENCE_MIGRATIONS[LIN_MIG]


def _downgrade_v2(env):
    """Deja el sobre como lo dejo el motor del evidence release v2 (produccion tras el evidence release): canonical_evidence v2 (sin las secciones de linaje), assessability v3."""
    from tools.catalog.modeling_assessment.canonical_sections import V3_SECTIONS, V4_SECTIONS
    prof = env["profile_json"]
    for sec in (*V3_SECTIONS, *V4_SECTIONS):
        prof["canonical_evidence"].pop(sec, None)
    prof["canonical_evidence"]["version"] = "canonical-evidence/v2"
    prof["versions"].update({"canonical_evidence": "canonical-evidence/v2", "assessability": "assessability-rules/v3"})
    env["assessment_versions"] = dict(prof["versions"])


def _make_v2(world, *, input_changed=False):
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)

    def mutate(env):
        _downgrade_v2(env)
        if input_changed:
            env["input_pool_signature"] = "0" * 64
            env.pop("input_closure_signature", None)                 # sobre previo a la firma de dependencias: incierto => STALE explícito
    _rewrite(path, mutate)
    restart()
    return path


def _run_lin(world, apply=False, **kw):
    return bf.migrate_stale_evidence([world.acme], LIN_MIG, apply=apply, governed_state_present=ALWAYS, **kw)


def test_the_lineage_migration_is_registered_with_exactly_the_two_changed_keys_and_closure_equal():
    assert LIN_TARGET.target_keys == ("assessability", "canonical_evidence") and LIN_TARGET.closure_policy == svc.CLOSURE_EQUAL
    assert LIN_TARGET.eligible_old == {"assessability": frozenset({"assessability-rules/v3"}), "canonical_evidence": frozenset({"canonical-evidence/v2"})}


def test_a_v2_contract_profile_is_a_targeted_version_only_stale_for_the_lineage_migration(world):
    _make_v2(world)
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_ONLY and st.stale_targeted == LIN_MIG
    assert st.stale_version_diff == ("assessability", "canonical_evidence")


def test_the_lineage_migration_dry_run_writes_nothing_and_apply_lands_on_current_with_the_same_closure(world):
    path = _make_v2(world)
    before = _digest(path)
    dry = _run_lin(world, apply=False)
    assert dry["records"][0]["action"] == bf.WOULD_REGENERATE and _digest(path) == before
    old_closure = svc.closure_identity(svc.status_for_entry(world.acme).profile)
    out = _run_lin(world, apply=True)
    assert out["records"][0]["action"] == bf.REGENERATED and files(world) == [path]
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.CURRENT and st.profile["versions"] == svc.current_assessment_versions()
    assert svc.closure_identity(st.profile) == old_closure                                                    # solo se agrego evidencia: el cierre no cambia
    ce = st.profile["canonical_evidence"]
    assert ce["version"] == "canonical-evidence/v4" and "published_field_lineage" in ce and "source_contract_classes" in ce


def test_input_changed_v2_profiles_are_never_absorbed_by_the_lineage_migration(world):
    path = _make_v2(world, input_changed=True)
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_AND_INPUT and st.stale_targeted is None
    before = _digest(path)
    out = _run_lin(world, apply=True)
    assert out["records"][0]["action"] != bf.REGENERATED and out["counts"]["regenerated_local"] == 0 and _digest(path) == before


def test_a_v1_profile_is_not_migrated_by_the_lineage_migration_and_nothing_chains(world):
    path = _make_old(world)                                                                                      # contrato anterior al evidence release v2
    before = _digest(path)
    out = _run_lin(world, apply=True)
    rec = out["records"][0]
    assert rec["reason"] == bf.OTHER_MIGRATION_APPLICABLE and rec["applicable_migration"] == MIG and _digest(path) == before


def test_a_v2_profile_is_not_eligible_for_the_older_migration(world):
    _make_v2(world)
    out = bf.migrate_stale_evidence([world.acme], MIG, apply=True, governed_state_present=ALWAYS)
    assert out["records"][0]["reason"] == bf.OTHER_MIGRATION_APPLICABLE and out["records"][0]["applicable_migration"] == LIN_MIG
