"""test_assessment_evidence_migration.py — migración CONTROLADA de assessments gobernados STALE por VERSIÓN de evidencia
(``assessment_backfill --regenerate-stale-evidence evidence_g1_g3_v1``) y clasificador de vigencia.

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

MIG = "evidence_g1_g3_v1"
TARGET = svc.EVIDENCE_MIGRATIONS[MIG]
OLD_VALUES = {k: next(iter(v)) for k, v in TARGET.eligible_old.items() if v != frozenset({None})}


# ═══════════════ 1. clasificador de vigencia (puro) ═══════════════
def _cur_versions():
    return svc.current_assessment_versions()


def _old_versions():
    v = dict(_cur_versions())
    v.update(OLD_VALUES)
    v.pop("canonical_evidence", None)
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
    other = dict(v, confidence="confidence-cuts/v0")
    c = svc.classify_currentness("sig", other, "sig", v)
    assert c.reason == svc.STALE_VERSION_ONLY and c.targeted is None and c.version_only_untargeted and c.version_diff == ("confidence",)
    extra = dict(_old_versions(), identity="identity-rules/v0")                 # la migración MÁS otra clave
    assert svc.classify_currentness("sig", extra, "sig", v).targeted is None
    partial = dict(v, signals=OLD_VALUES["signals"])                            # solo una de las cuatro claves
    assert svc.classify_currentness("sig", partial, "sig", v).targeted is None
    unknown_old = dict(_old_versions(), signals="signal-rules/v1")              # valor viejo NO elegible
    assert svc.classify_currentness("sig", unknown_old, "sig", v).targeted is None


def test_the_six_public_states_are_unchanged_and_stale_reasons_are_not_states():
    assert svc.STATES == (svc.CURRENT, svc.STALE, svc.NOT_YET_ASSESSED, svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA)
    assert not set(svc.STALE_REASONS) & set(svc.STATES)


def test_the_registry_is_small_explicit_and_uses_the_real_constants_as_target():
    assert set(svc.EVIDENCE_MIGRATIONS) == {MIG} and TARGET.target_keys == ("signals", "assessability", "finding_evidence", "canonical_evidence")
    assert OLD_VALUES == {"signals": "signal-rules/v2", "assessability": "assessability-rules/v1", "finding_evidence": "finding-evidence/v1"}
    assert TARGET.eligible_old["canonical_evidence"] == frozenset({None})
    v = _cur_versions()
    assert (v["signals"], v["assessability"], v["finding_evidence"], v["canonical_evidence"]) == (
        "signal-rules/v3", "assessability-rules/v2", "finding-evidence/v2", "canonical-evidence/v1")


# ═══════════════ 2. unidades con dobles (sin motor ni disco) ═══════════════
def _prof(*, closure=("A", "B"), am="AM", cov=0.9, covi=0.95, conf="MEDIUM", reviews=1, oq=2, ingestion="PARTIALLY_ASSESSED", with_ce=False, leaves=(), reps=()):
    p = {"summary": {"coverage": {"strict": cov, "inclusive": covi}, "confidence": {"level": conf}, "context_required": {"open_questions": oq}},
         "scope": {"terminal_analytic_model": am, "root_object": am, "root_type": "analytic-model", "closure_artifacts": list(closure)},
         "review_required": [{"id": "CROSS_LAYER_REDERIVATION", "occurrence_count": reviews,
                              "occurrences": [{"values": {"provider": "P", "consumer": "C", "fields": ["F"]}}]}] if reviews else [],
         "areas": [{"id": "INGESTION", "assessability": ingestion, "finding_groups": ["G1"]}, {"id": "PROPAGATION", "assessability": "ASSESSED", "finding_groups": []}]}
    if with_ce:
        p["canonical_evidence"] = {"version": "canonical-evidence/v1", "leaf_provenance": list(leaves), "assembly_topology": {"count": 0, "artifacts": []},
                                   "expression_repetition": {"count": 0, "groups": []},
                                   "derivation_semantics": {"repeated_derivations": list(reps), "relation_counts": {}, "fields": []}}
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

    def run(self, entries, apply=False, **kw):
        kw.setdefault("remote_enabled", ALWAYS)
        kw.setdefault("verify_remote", lambda client, am: svc.REMOTE_CONFIRMED)
        return bf.migrate_stale_evidence(entries, MIG, apply=apply, status_for=self.status_for, regenerate=self.regenerate, commit=self.commit, retry_remote=self.retry,
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
    assert set(d["versions"]) == set(TARGET.target_keys) and d["versions"]["signals"]["new"] == "signal-rules/v3"
    assert d["coverage_strict"] == {"old": 0.9, "new": 1.0, "changed": True} and d["coverage_inclusive"]["new"] == 1.0
    assert d["confidence"]["new"] == "HIGH" and d["areas"]["INGESTION"]["state"] == {"old": "PARTIALLY_ASSESSED", "new": "ASSESSED"}
    assert d["canonical_evidence"]["old"] == "absent" and d["canonical_evidence"]["new"] == "present"
    assert d["canonical_evidence"]["sections"]["leaf_provenance"] == {"old": "absent", "new": "present"}
    assert d["leaf_provenance"]["new"]["established"] == 1 and d["leaf_provenance"]["old"]["leaves"] == 0
    assert d["repeated_derivations"] == [{"field": "F", "provider": "P", "consumer": "C", "old_review_present": True, "relation": "CHAINED_REFINEMENT",
                                          "relation_reason": "CONSUMER_READS_DERIVED_PROVIDER_FIELD", "exposed_by_root": True,
                                          "exposed_by_root_basis": "SAME_NAME_IN_ROOT_CONTRACT"}]
    assert d["closure_drift"] == {"drift": False, "terminal_am_same": True, "closure_artifacts_old": 2, "closure_artifacts_new": 2}
    assert "fact_index" not in json.dumps(out) and "areas" in d


def test_the_presence_check_reads_canonical_evidence_content_never_fact_index_membership():
    old = _prof()
    old["fact_index"] = [{"fact": "propagation.derivation_semantics@closure"}, {"fact": "harmonization.assembly_topology@closure"}]      # nombres de hecho ya presentes
    cand = SimpleNamespace(profile=_prof(with_ce=True, leaves=[]))
    d = bf.compare_evidence_migration(old, cand, MIG, _old_versions(), _cur_versions())
    assert d["canonical_evidence"]["old"] == "absent" and d["canonical_evidence"]["sections"]["derivation_semantics"]["old"] == "absent"


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


def _make_old(world, *, old_versions=True, input_changed=False, extra=None):
    """Genera el assessment con el motor y lo deja como uno de la versión ANTERIOR del contrato de evidencia (re-firmado, como lo habría dejado el motor viejo)."""
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)

    def mutate(env):
        prof = env["profile_json"]
        if old_versions:
            prof.pop("canonical_evidence", None)
            prof["versions"].pop("canonical_evidence", None)
            prof["versions"].update(OLD_VALUES)
            env["assessment_versions"] = dict(prof["versions"])
        if input_changed:
            env["input_pool_signature"] = "0" * 64
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
    assert d["pool_signature_same"] and d["canonical_evidence"] == {"old": "absent", "new": "present", "sections": d["canonical_evidence"]["sections"]}
    assert d["versions"]["canonical_evidence"] == {"old": None, "new": "canonical-evidence/v1"} and d["versions"]["signals"] == {"old": "signal-rules/v2", "new": "signal-rules/v3"}
    assert d["closure_drift"]["drift"] is False and d["leaf_provenance"]["new"]["leaves"] >= 0
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
    assert st.profile["canonical_evidence"]["version"] == "canonical-evidence/v1"


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
    _patch_engine(monkeypatch, lambda d: (d.update(_tampered=True), d.__setitem__("canonical_evidence", {"version": "canonical-evidence/v1", "leaf_provenance": [{"leaf": "x"}]})), skip_validation=True)
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
    assert out["records"][0]["action"] == bf.REGENERATED and out["records"][0]["delta"]["closure_drift"]["drift"] is False and files(world) == [path]


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
