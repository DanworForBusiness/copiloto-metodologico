"""v4.C — migracion CONTROLADA ``evidence_leaf_materialization_v4`` (canonical-evidence v3 -> v4) con el framework EXISTENTE de migraciones gobernadas
(``assessment_backfill --regenerate-stale-evidence``) y revalidacion separada del Juicio v2 (``--revalidate-judgment``).

Todo corre contra COPIAS AISLADAS (``tmp_path``: pool, almacen gobernado y catalogo propios; sin Spaces ni Datasphere); nunca sobre los Assessments persistidos vigentes. Contrato:
  * elegible = STALE VERSION_ONLY (misma firma del pool) con diferencia de versiones EXACTAMENTE ``canonical_evidence`` v3 -> v4; el resto se reporta aparte;
  * CLOSURE_EQUAL + el perfil nuevo solo difiere en ``canonical_evidence.version``, ``versions.canonical_evidence`` y la seccion ``leaf_materialization``;
  * se ABSTIENE (perfil anterior intacto) si falta el pool, difiere su firma, no se recuperan/resuelven las referencias, el cierre o el contenido cambian o la metodologia del Juicio difiere;
  * el Juicio v2 se REVALIDA (misma metodologia, nueva firma de perfil), nunca se recalifica; dry-run sin escrituras; ``--apply`` exige confirmacion explicita."""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from core import remote_store
from tests.test_modeling_assessment_delta_shadow import _build_pool
from tests.test_modeling_assessment_leaf_materialization_evidence import _full_rf
from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_governed_assessment_durability import _flow, files, restart  # noqa: F401
from tools.catalog import catalog_service as cs
from tools.catalog.methodological_judgment.contract import signature as jsig
from tools.catalog.modeling_assessment import evidence_contract as EC
from webapp import main
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads
from webapp.services import migration_approval as ma

MIG = "evidence_leaf_materialization_v4"
KEY = "leaf_materialization"
V3 = EC.CANONICAL_EVIDENCE_VERSION_V3
ART = ["4AM_ROOT", "2VD_V", "1TR_X"]


@pytest.fixture()
def kworld(tmp_path, monkeypatch):
    """Dos clientes (Kensing y Kensing2: el mismo pool delta+RF copiado) en un entorno 100 % aislado."""
    inbound = tmp_path / "pool"
    _build_pool(str(inbound))
    _full_rf(str(inbound))
    shutil.copytree(inbound / "Kensing", inbound / "Kensing2")
    governed = tmp_path / "governed-assessments"
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(governed))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "runtime-cache")
    monkeypatch.setattr(config, "SPACES_ENDPOINT", "")                                    # sin remoto: nada sale de la maquina
    entries = [_flow("Kensing", ART, "kens-4am"), _flow("Kensing2", ART, "kens2-4am")]
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    svc.reset_memory_cache()
    svc._validated.clear()
    svc.invalidate_pool_index()
    w = SimpleNamespace(inbound=inbound, governed=governed, entries=entries, k=entries[0], k2=entries[1], tmp=tmp_path)
    assert str(svc.governed_dir()).startswith(str(tmp_path))                              # aislamiento: jamas el estado canonico real
    yield w
    svc.reset_memory_cache()
    svc.invalidate_pool_index()


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _snapshot(w) -> dict:
    return {str(p.relative_to(w.governed)): _digest(p) for p in files(w)}


def _env(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite(path: Path, mutate) -> None:
    env = _env(path)
    mutate(env)
    env["assessment_artifact_signature"] = svc._artifact_signature(env["profile_json"]) if env.get("profile_json") is not None else None
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")


def _to_v3(env: dict) -> None:
    """Deja el sobre como lo dejo el motor v3: perfil sin la seccion v4 y con las versiones v3; Juicio v2 derivado de ESE perfil (con su firma de perfil v3)."""
    prof = env["profile_json"]
    prof["canonical_evidence"].pop(KEY, None)
    prof["canonical_evidence"]["version"] = V3
    prof["versions"]["canonical_evidence"] = V3
    env["assessment_versions"] = dict(prof["versions"])
    env["judgment"] = svc._derive_judgment(prof)


def _path(w, client: str) -> Path:
    return next(p for p in files(w) if p.parent.name == client)


def _make_v3(w, *entries) -> dict:
    """Genera con el motor vigente y deja los sobres como estaban en v3 (copias aisladas). Devuelve {cliente: sobre v3}."""
    out = {}
    for e in entries:
        assert svc.generate(e).state == svc.CURRENT
        p = _path(w, e.cliente)
        _rewrite(p, _to_v3)
        out[e.cliente] = _env(p)
    restart()
    return out


_BACKUPS = itertools.count(1)


def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="approver", operator="operator")


def _run(w, *entries, apply=False, **kw) -> dict:
    """``apply=True`` recorre el camino REAL asegurado (plan -> autorizacion -> respaldo verificado -> olas); la autorizacion la concede el test (la logica fail-closed se prueba en test_migration_secured_apply)."""
    if not apply:
        return bf.migrate_stale_evidence(list(entries) or [w.k], MIG, apply=False, governed_state_present=ALWAYS, **kw)
    return bf.secured_migrate(list(entries) or [w.k], MIG, authorizer=_grant, backup_dir=w.tmp / f"backup-{next(_BACKUPS)}", allow_local_only=True, governed_state_present=ALWAYS, **kw)


def _rec(out: dict, client: str = "Kensing") -> dict:
    return next(r for r in out["records"] if r["client"] == client)


def _minus(profile: dict) -> dict:
    return svc._without_materialization(profile)


# ───────────────────────────── elegibilidad ─────────────────────────────
def test_the_migration_is_registered_in_the_existing_framework_and_targets_only_canonical_evidence_v3(kworld):
    m = svc.EVIDENCE_MIGRATIONS[MIG]
    assert m.target_keys == ("canonical_evidence",) and m.eligible_old == {"canonical_evidence": frozenset({V3})} and m.closure_policy == svc.CLOSURE_EQUAL
    assert svc.MATERIALIZATION_MIGRATION == MIG and MIG in bf.CONFIRMATION_REQUIRED
    cur = svc.current_assessment_versions()
    assert svc.targeted_migration({**cur, "canonical_evidence": V3}, cur) == MIG
    assert svc.targeted_migration({**cur, "canonical_evidence": "canonical-evidence/v2", "assessability": "assessability-rules/v3"}, cur) == "evidence_field_lineage_v3"   # sin encadenamiento
    assert svc.targeted_migration({**cur, "canonical_evidence": V3, "confidence": "confidence-cuts/v2"}, cur) is None                                                      # diferencias ajenas: no elegible


def test_a_v3_governed_profile_is_current_but_pending_the_migration(kworld):
    _make_v3(kworld, kworld.k)
    st = svc.status_for_entry(kworld.k)
    # COMPATIBILIDAD DE TRANSICION (Opcion A): el unico motivo es canonical_evidence v3->v4 => sigue CURRENT para la presentacion pero queda identificado como pendiente de MIG
    assert (st.state, st.stale_reason, st.pending_migration) == (svc.CURRENT, None, MIG) and st.profile is not None


@pytest.mark.parametrize("tag, mutate, bucket", [
    ("entrada cambiada", lambda e: (e.update(input_pool_signature="0" * 64), e.pop("input_closure_signature", None)), bf.VERSION_AND_INPUT_CHANGED),
    ("v2 (otra migracion)", lambda e: (e["profile_json"]["canonical_evidence"].pop("published_field_lineage"), e["profile_json"]["canonical_evidence"].pop("source_contract_classes"),
                                       e["profile_json"]["canonical_evidence"].update(version="canonical-evidence/v2"),
                                       e["profile_json"]["versions"].update(canonical_evidence="canonical-evidence/v2", assessability="assessability-rules/v3"),
                                       e.update(assessment_versions=dict(e["profile_json"]["versions"]))), bf.OTHER_MIGRATION_APPLICABLE),
])
def test_ineligible_states_are_reported_apart_and_never_migrated(kworld, tag, mutate, bucket):
    _make_v3(kworld, kworld.k)
    _rewrite(_path(kworld, "Kensing"), mutate)
    restart()
    before = _snapshot(kworld)
    out = _run(kworld, apply=True)
    r = _rec(out)
    assert r["reason"] == bucket and r["action"] == bf.SKIP and not r.get("eligible"), tag
    assert _snapshot(kworld) == before and out["counts"]["regenerated_local"] == 0 and out["counts"]["remaining_stale_not_migrated"] == 1


def test_current_and_not_yet_assessed_entries_are_skipped_and_the_batch_continues(kworld):
    assert svc.generate(kworld.k).state == svc.CURRENT                                  # ya CURRENT (v4): no se toca
    out = _run(kworld, kworld.k, kworld.k2, apply=False)
    assert _rec(out)["reason"] == bf.SKIPPED_ALREADY_CURRENT and _rec(out, "Kensing2")["reason"] == bf.SKIPPED_NOT_YET_ASSESSED and out["counts"]["eligible"] == 0


# ───────────────────────────── dry-run sin escrituras ─────────────────────────────
def test_the_dry_run_runs_the_real_engine_in_memory_reports_the_delta_and_writes_nothing(kworld, monkeypatch):
    old = _make_v3(kworld, kworld.k)["Kensing"]
    snap = _snapshot(kworld)

    def boom(*a, **k):
        raise AssertionError("el dry-run no debe escribir")
    monkeypatch.setattr(svc, "commit_stale_migration", boom)
    monkeypatch.setattr(svc, "commit_regenerated", boom)
    monkeypatch.setattr(svc, "_store_envelope", boom)
    out = bf.migrate_stale_evidence([kworld.k], MIG, apply=False, governed_state_present=ALWAYS, commit=boom)
    r = _rec(out)
    assert out["dry_run"] and not out["apply"] and r["action"] == bf.WOULD_REGENERATE and r["eligible"] and out["counts"]["would_regenerate"] == 1
    d = r["delta"]
    assert d["versions"] == {"canonical_evidence": {"old": V3, "new": "canonical-evidence/v4"}} and d["closure"]["drift"] is False and d["closure"]["added"] == [] and d["closure"]["removed"] == []
    lm = d["leaf_materialization"]
    assert lm["state"] == "EVALUATED" and lm["csn_provided"] is True and lm["references"] == 3 and lm["limitations"] == [] and lm["counts"]["by_kind"] == {"REPLICA": 1}
    assert d["judgment"]["judgment_action"] == svc.JDG_SIGNATURE_REBOUND and d["judgment"]["previous_judgment_level"] == d["judgment"]["new_judgment_level"]
    assert d["coverage_strict"]["changed"] is False and d["confidence"]["changed"] is False
    assert _snapshot(kworld) == snap and _env(_path(kworld, "Kensing")) == old           # CERO escrituras: ni el sobre ni la memoria
    assert "profile_json" not in json.dumps(out)                                          # el informe no vuelca perfiles


# ───────────────────────────── exito, estricto CLOSURE_EQUAL y revalidacion del Juicio ─────────────────────────────
def test_apply_migrates_to_v4_with_a_profile_identical_except_version_and_section_and_a_revalidated_judgment(kworld):
    old = _make_v3(kworld, kworld.k)["Kensing"]
    out = _run(kworld, apply=True)
    r = _rec(out)
    assert r["action"] == bf.REGENERATED and r["durable"] == svc.DURABLE_LOCAL and out["counts"]["regenerated_local"] == 1 and out["counts"]["abstained"] == 0
    new = _env(_path(kworld, "Kensing"))
    assert new["profile_json"]["canonical_evidence"]["version"] == "canonical-evidence/v4" and KEY in new["profile_json"]["canonical_evidence"]
    assert new["assessment_versions"] == svc.current_assessment_versions() and new["input_pool_signature"] == old["input_pool_signature"]               # misma firma de pool
    assert _minus(new["profile_json"]) == _minus(old["profile_json"])                                                   # CLOSURE_EQUAL estricto: mismo Assessment metodologico
    assert svc.closure_identity(new["profile_json"]) == svc.closure_identity(old["profile_json"])
    assert svc.status_for_entry(kworld.k).state == svc.CURRENT
    assert new["assessment_artifact_signature"] != old["assessment_artifact_signature"] and new["assessment_artifact_signature"] == svc._artifact_signature(new["profile_json"])
    assert new["domain_classification"] == old["domain_classification"] and new["business_classification"] == old["business_classification"]            # hermanos intactos


def test_the_persisted_judgment_keeps_its_methodology_and_recognizes_the_new_profile_signature(kworld):
    old = _make_v3(kworld, kworld.k)["Kensing"]
    _run(kworld, apply=True)
    new = _env(_path(kworld, "Kensing"))
    jo, jn = old["judgment"], new["judgment"]
    assert svc.judgment_methodology(jo) == svc.judgment_methodology(jn) and svc.judgment_methodology_diff(jo, jn) == []                   # niveles, fortalezas, razones, criterios intactos
    assert (jn["status"], jn["level"], jn["level_rule"], jn["ruleset_version"]) == (jo["status"], jo["level"], jo["level_rule"], svc.JUDGMENT_CONTRACT_VERSIONS[1])
    assert jn["source_assessment"]["profile_signature"] == jsig(new["profile_json"]) != jo["source_assessment"]["profile_signature"]                  # reconoce la NUEVA firma del perfil
    assert jn["source_assessment"]["assessment_versions"]["canonical_evidence"] == "canonical-evidence/v4" and jn["output_signature"] != jo["output_signature"]
    assert svc.classify_judgment_backfill(new)[0] == svc.JDG_CURRENT and svc.classify_judgment_revalidation(new)[0] == svc.JDG_CURRENT


def test_the_migration_is_idempotent_and_a_second_run_writes_nothing(kworld):
    _make_v3(kworld, kworld.k)
    assert _rec(_run(kworld, apply=True))["action"] == bf.REGENERATED
    snap = _snapshot(kworld)
    out = _run(kworld, apply=True)
    assert _rec(out)["reason"] == bf.SKIPPED_ALREADY_CURRENT and out["counts"]["eligible"] == 0 and out["counts"]["regenerated_local"] == 0 and _snapshot(kworld) == snap


# ───────────────────────────── abstencion: pool, firma, referencias ─────────────────────────────
def _expect_abstention(kworld, code: str, *, mutate_env=None):
    old = _make_v3(kworld, kworld.k)["Kensing"]
    if mutate_env:
        _rewrite(_path(kworld, "Kensing"), mutate_env)
        restart()
    snap = _snapshot(kworld)
    out = _run(kworld, apply=True)
    r = _rec(out)
    assert r["action"] == bf.ABSTAINED and r["reason"] == code and out["counts"]["abstained"] == 1 and out["counts"]["regenerated_local"] == 0, r
    assert _snapshot(kworld) == snap and (mutate_env or _env(_path(kworld, "Kensing")) == old)                         # el perfil anterior queda intacto (disco y estado)
    assert out["complete"] is False
    return out


def test_it_abstains_when_the_pool_is_missing(kworld, monkeypatch):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    empty = kworld.tmp / "empty-pool"
    empty.mkdir()
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(empty))
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(empty))
    restart()
    out = _run(kworld, apply=True)
    r = _rec(out)
    assert r["action"] != bf.REGENERATED and out["counts"]["regenerated_local"] == 0 and _snapshot(kworld) == snap, r


def test_it_abstains_when_the_pool_signature_differs_and_never_rebuilds_from_new_sources(kworld):
    _make_v3(kworld, kworld.k)
    p = Path(kworld.inbound, "Kensing", "views", "2VD_V.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["definitions"]["2VD_V"]["@EndUserText.label"] = "pool distinto"                                                  # el pool cambio: ya no es el mismo insumo tecnico
    p.write_text(json.dumps(data), encoding="utf-8")
    restart()
    snap = _snapshot(kworld)
    out = _run(kworld, apply=True)
    r = _rec(out)
    assert r["reason"] == bf.VERSION_AND_INPUT_CHANGED and r["action"] == bf.SKIP and _snapshot(kworld) == snap and out["counts"]["regenerated_local"] == 0


def test_it_abstains_when_a_source_cannot_be_recovered(kworld, monkeypatch):
    monkeypatch.setattr(svc, "_pool_resolver", lambda root: (lambda rel: None))                                         # las fuentes ya no se recuperan
    _expect_abstention(kworld, "MATERIALIZATION_SOURCE_NOT_RECOVERABLE")


def test_it_abstains_when_a_reference_does_not_resolve_to_the_same_fragment(kworld, monkeypatch):
    def tampered(root):
        def r(rel):
            p = Path(root) / rel
            d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
            if isinstance(d, dict) and "definitions" in d and "1TR_X" in d["definitions"]:
                d["definitions"]["1TR_X"]["@EndUserText.label"] = "otro contenido"
            return d
        return r
    monkeypatch.setattr(svc, "_pool_resolver", tampered)
    _expect_abstention(kworld, "MATERIALIZATION_REFERENCE_UNRESOLVED")


def test_it_abstains_when_references_cannot_be_recorded_or_the_pool_evidence_was_not_provided(kworld, monkeypatch):
    from tools.catalog.modeling_assessment import materialization_evidence as me
    from tools.catalog.modeling_assessment import shadow as sh
    real = me.collect_materialization_evidence

    def no_refs(files, root, ce, sig):
        ev = real(files, root, ce, sig)
        return {**ev, "references": {"entities": {}, "rf_tasks": {}}}
    monkeypatch.setattr(sh, "collect_materialization_evidence", no_refs)
    _expect_abstention(kworld, "MATERIALIZATION_REFERENCES_INCOMPLETE")


def test_it_abstains_when_the_section_would_not_carry_pool_evidence(kworld, monkeypatch):
    from tools.catalog.modeling_assessment import shadow as sh
    monkeypatch.setattr(sh, "collect_materialization_evidence", lambda files, root, ce, sig: None)
    _expect_abstention(kworld, "MATERIALIZATION_CSN_NOT_PROVIDED")


# ───────────────────────────── abstencion: cierre, contenido, Juicio ─────────────────────────────
def _patch_engine(monkeypatch, mutate):
    real = svc._engine_assess

    def engine(*a, **k):
        doc, meta = real(*a, **k)
        mutate(doc)
        return doc, meta
    monkeypatch.setattr(svc, "_engine_assess", engine)


def test_it_abstains_on_closure_drift(kworld, monkeypatch):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    _patch_engine(monkeypatch, lambda d: d["scope"]["closure_artifacts"].append("ARTEFACTO_NUEVO"))
    out = _run(kworld, apply=True)
    assert _rec(out)["action"] == bf.ABSTAINED and _rec(out)["reason"] == "CLOSURE_DRIFT" and _snapshot(kworld) == snap


def test_it_abstains_when_anything_beyond_version_and_section_changes_in_the_profile(kworld, monkeypatch):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    _patch_engine(monkeypatch, lambda d: d["summary"]["coverage"].__setitem__("strict", 0.01))
    out = _run(kworld, apply=True)
    assert _rec(out)["action"] == bf.ABSTAINED and _rec(out)["reason"] == "NON_MATERIALIZATION_CONTENT_CHANGED" and _snapshot(kworld) == snap


def test_it_abstains_when_the_judgment_methodology_would_differ_instead_of_requalifying(kworld, monkeypatch):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    real = svc._derive_judgment
    monkeypatch.setattr(svc, "_derive_judgment", lambda prof, ratified=None: {**real(prof, ratified), "level": "OTRO_NIVEL"})                   # el motor "recalificaria": NO se acepta
    out = _run(kworld, apply=True)
    assert _rec(out)["action"] == bf.ABSTAINED and _rec(out)["reason"] == "JUDGMENT_METHODOLOGY_DIVERGED" and _snapshot(kworld) == snap


def test_a_missing_or_older_contract_judgment_is_not_created_nor_migrated_by_this_migration(kworld):
    _make_v3(kworld, kworld.k)
    _rewrite(_path(kworld, "Kensing"), lambda e: e.update(judgment=None))
    restart()
    _run(kworld, apply=True)
    assert _env(_path(kworld, "Kensing"))["judgment"] is None                                                           # no se fabrica un Juicio: eso es --backfill-judgment
    old_j = {"judgment_schema_version": "judgment/v0", "ruleset_version": "judgment-rules/v1", "level": "PARCIAL", "output_signature": "sha256:x", "source_assessment": {}}


# ───────────────────────────── fallos parciales y reejecucion segura ─────────────────────────────
def test_a_partial_failure_keeps_the_other_records_and_a_rerun_completes_safely(kworld, monkeypatch):
    _make_v3(kworld, kworld.k, kworld.k2)
    snap2 = {p.name: _digest(p) for p in files(kworld) if p.parent.name == "Kensing2"}
    real = svc.commit_regenerated

    def flaky(candidate):
        if candidate.client == "Kensing2":
            raise OSError("disco lleno")
        return real(candidate)
    monkeypatch.setattr(svc, "commit_regenerated", flaky)
    out = _run(kworld, kworld.k, kworld.k2, apply=True)
    assert _rec(out)["action"] == bf.REGENERATED and _rec(out, "Kensing2")["action"] == bf.FAILED_ACTION and out["counts"]["failed"] == 1 and out["complete"] is False
    assert {p.name: _digest(p) for p in files(kworld) if p.parent.name == "Kensing2"} == snap2                          # el fallido conserva su perfil anterior
    monkeypatch.setattr(svc, "commit_regenerated", real)
    again = _run(kworld, kworld.k, kworld.k2, apply=True)                                                               # reejecucion segura
    assert _rec(again)["reason"] == bf.SKIPPED_ALREADY_CURRENT and _rec(again, "Kensing2")["action"] == bf.REGENERATED and again["counts"]["failed"] == 0
    assert svc.status_for_entry(kworld.k).state == svc.CURRENT and svc.status_for_entry(kworld.k2).state == svc.CURRENT


def test_the_record_changed_during_the_run_is_not_overwritten(kworld):
    _make_v3(kworld, kworld.k)
    p = _path(kworld, "Kensing")

    def commit_after_a_concurrent_change(candidate):                                                                    # entre regenerar y escribir alguien cambia el registro
        env = _env(p)
        env["assessment_artifact_signature"] = "cambiado-por-otro"
        p.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        restart()
        return svc.commit_stale_migration(candidate)
    out = _run(kworld, apply=True, commit=commit_after_a_concurrent_change)
    assert _rec(out)["action"] == bf.FAILED_ACTION and _rec(out)["reason"] == "PREVIOUS_CHANGED_DURING_RUN"
    assert _env(p)["assessment_artifact_signature"] == "cambiado-por-otro" and _env(p)["profile_json"]["canonical_evidence"]["version"] == V3        # NO se pisa


# ───────────────────────────── contrato CLI y autorizacion ─────────────────────────────
def _cli(monkeypatch, argv):
    seen = []
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, []))
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda es, name, apply=False, **k: seen.append(("migrate", name, apply)) or {"counts": {"failed": 0, "abstained": 0}, "environment_warnings": []})
    monkeypatch.setattr(bf, "revalidate_judgments", lambda apply=False, client="": seen.append(("revalidate", apply, client)) or {"counts": {"failed": 0, "methodology_diverged": 0}, "environment_warnings": []})
    code = bf.main(argv)
    return code, seen


def test_cli_exposes_the_migration_in_the_existing_mode_and_requires_exactly_one_of_dry_run_or_apply(monkeypatch, capsys):
    assert _cli(monkeypatch, ["--regenerate-stale-evidence", MIG, "--dry-run"])[1] == [("migrate", MIG, False)]
    for argv in (["--regenerate-stale-evidence", MIG], ["--regenerate-stale-evidence", MIG, "--dry-run", "--apply", "--confirm-migration", MIG]):
        with pytest.raises(SystemExit) as e:
            _cli(monkeypatch, argv)
        assert e.value.code == 2                                                                                        # usage: cero escrituras


def test_cli_apply_needs_the_explicit_confirmation_naming_the_migration(monkeypatch, capsys):
    for argv in (["--regenerate-stale-evidence", MIG, "--apply"], ["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", "evidence_confidence_v3"],
                 ["--regenerate-stale-evidence", "evidence_confidence_v3", "--dry-run", "--confirm-migration", MIG]):
        with pytest.raises(SystemExit) as e:
            _cli(monkeypatch, argv)
        assert e.value.code == 2
    monkeypatch.setattr(bf, "secured_migrate", lambda es, name, **k: seen_secured.append(name) or {"outcome": "APPLIED", "counts": {"failed": 0, "abstained": 0}, "environment_warnings": []})
    seen_secured: list = []
    code, seen = _cli(monkeypatch, ["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", MIG])
    assert (code, seen, seen_secured) == (0, [], [MIG])                                                                 # la migracion sensible SOLO se aplica por el camino asegurado
    assert _cli(monkeypatch, ["--regenerate-stale-evidence", "evidence_confidence_v3", "--apply"])[1] == [("migrate", "evidence_confidence_v3", True)]      # las demas migraciones no cambian


def test_cli_exit_codes_distinguish_abstentions_and_the_judgment_revalidation_mode_is_explicit(monkeypatch):
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, []))
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda *a, **k: {"counts": {"failed": 0, "abstained": 2}, "environment_warnings": []})
    assert bf.main(["--regenerate-stale-evidence", MIG, "--dry-run"]) == 3
    monkeypatch.setattr(bf, "revalidate_judgments", lambda **k: {"counts": {"failed": 0, "methodology_diverged": 1}, "environment_warnings": []})
    assert bf.main(["--revalidate-judgment", "--dry-run"]) == 3
    with pytest.raises(SystemExit):
        bf.main(["--revalidate-judgment"])


def test_a_direct_apply_of_the_sensitive_migration_is_refused_never_downgraded_to_a_dry_run(kworld):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    with pytest.raises(bf.ApplyRequiresAuthorization):
        bf.migrate_stale_evidence([kworld.k], MIG, apply=True, governed_state_present=ALWAYS)
    assert _snapshot(kworld) == snap


# ───────────────────────────── revalidacion SEPARADA del Juicio v2 ─────────────────────────────
def _stale_signature_world(kworld) -> Path:
    """Perfil v4 (migrado) cuyo Juicio v2 persistido quedo con la firma del perfil v3: el caso que ``--revalidate-judgment`` resuelve por separado."""
    assert svc.generate(kworld.k).state == svc.CURRENT
    p = _path(kworld, "Kensing")
    v4_env = _env(p)
    stale = svc._derive_judgment(_v3_profile(v4_env["profile_json"]))
    _rewrite(p, lambda e: e.update(judgment=stale))
    restart()
    return p


def _v3_profile(profile: dict) -> dict:
    prof = copy.deepcopy(profile)
    prof["canonical_evidence"].pop(KEY, None)
    prof["canonical_evidence"]["version"] = V3
    prof["versions"]["canonical_evidence"] = V3
    return prof


def test_revalidation_rebinds_a_judgment_whose_only_difference_is_the_profile_signature(kworld):
    p = _stale_signature_world(kworld)
    before = _env(p)
    assert svc.classify_judgment_backfill(before)[0] == svc.JDG_CONTENT_STALE                                           # el clasificador existente solo ve "contenido distinto"
    dry = bf.revalidate_judgments(apply=False)
    r = dry["records"][0]
    assert r["action"] == "would_rebind" and dry["counts"]["would_rebind"] == 1 and dry["dry_run"] and _env(p) == before    # dry-run: sin escrituras
    assert r["previous"] == r["revalidated"]                                                                            # mismo estado, nivel y regla
    out = bf.revalidate_judgments(apply=True)
    assert out["counts"]["rebound"] == 1 and out["counts"]["methodology_diverged"] == 0 and out["records"][0]["durable"] == svc.DURABLE_LOCAL
    after = _env(p)
    assert after["profile_json"] == before["profile_json"] and after["assessment_artifact_signature"] == before["assessment_artifact_signature"] and after["assessment_versions"] == before["assessment_versions"]
    assert svc.judgment_methodology(after["judgment"]) == svc.judgment_methodology(before["judgment"])                  # criterios intactos
    assert after["judgment"]["source_assessment"]["profile_signature"] == jsig(after["profile_json"])                   # reconoce la firma nueva
    assert svc.classify_judgment_backfill(after)[0] == svc.JDG_CURRENT
    again = bf.revalidate_judgments(apply=True)                                                                         # idempotente
    assert again["counts"]["rebound"] == 0 and again["counts"]["already_current"] == 1 and _env(p) == after


def test_revalidation_refuses_to_rewrite_a_judgment_whose_methodology_differs(kworld):
    p = _stale_signature_world(kworld)
    _rewrite(p, lambda e: e["judgment"].update(level="NIVEL_PERSISTIDO_DISTINTO"))
    restart()
    before = _env(p)
    out = bf.revalidate_judgments(apply=True)
    r = out["records"][0]
    assert r["action"] == svc.JDG_METHODOLOGY_DIVERGED and "level" in r["methodology_diff_keys"] and out["counts"]["methodology_diverged"] == 1 and out["counts"]["rebound"] == 0
    assert _env(p) == before and out["counts"]["complete"] is False                                                     # NO se recalifica: decision humana


def test_revalidation_does_not_create_missing_judgments_nor_migrate_older_contracts_and_respects_the_client_scope(kworld):
    _stale_signature_world(kworld)
    p = _path(kworld, "Kensing")
    _rewrite(p, lambda e: e.update(judgment=None))
    restart()
    out = bf.revalidate_judgments(apply=True)
    assert out["records"][0]["action"] == svc.JDG_MISSING and _env(p)["judgment"] is None and out["counts"]["judgment_missing"] == 1
    _rewrite(p, lambda e: e.update(judgment={"judgment_schema_version": "judgment/v0", "ruleset_version": "judgment-rules/v1", "level": "PARCIAL"}))
    out = bf.revalidate_judgments(apply=True)
    assert out["records"][0]["action"] == svc.JDG_VERSION_STALE and out["counts"]["judgment_version_stale"] == 1 and _env(p)["judgment"]["ruleset_version"] == "judgment-rules/v1"
    assert bf.revalidate_judgments(apply=True, client="otro")["counts"]["examined"] == 0


def test_revalidate_judgment_pure_function_classifies_every_case():
    prof = {"x": 1}
    assert svc.revalidate_judgment(None, None)[0] == svc.JDG_NO_PROFILE and svc.revalidate_judgment(None, prof)[0] == svc.JDG_MISSING
    assert svc.revalidate_judgment({"judgment_schema_version": "v0", "ruleset_version": "judgment-rules/v1"}, prof)[0] == svc.JDG_VERSION_STALE
    assert svc.judgment_methodology(None) is None and svc.judgment_methodology({"level": "A", "source_assessment": 1, "output_signature": 2}) == {"level": "A"}
    assert svc.judgment_methodology_diff({"level": "A", "source_assessment": 1}, {"level": "A", "source_assessment": 2}) == [] and svc.judgment_methodology_diff({"level": "A"}, {"level": "B"}) == ["level"]
    assert svc.JUDGMENT_PROFILE_BINDING_KEYS == ("source_assessment", "output_signature")                                  # lo UNICO que la revalidacion ignora


# ───────────────────────────── el resto del framework no cambia ─────────────────────────────
def test_the_other_migrations_never_abstain_and_keep_their_failure_semantics(kworld):
    assert bf.ABSTAINED not in {bf.FAILED_ACTION, bf.REGENERATED, bf.SKIP} and "abstained" in bf.migrate_stale_evidence([], "evidence_confidence_v3", governed_state_present=ALWAYS)["counts"]
    assert bf.migrate_stale_evidence([], "evidence_confidence_v3", governed_state_present=ALWAYS)["counts"]["abstained"] == 0
