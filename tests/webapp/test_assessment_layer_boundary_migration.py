"""Adopcion CONTROLADA de ``signal-rules/v4`` (LAYER_BOUNDARY_REVIEW): migracion ``signals_layer_boundary_v4`` con el framework EXISTENTE de migraciones gobernadas
(``assessment_backfill --regenerate-stale-evidence``: dry-run con plan y digest, autorizacion firmada, respaldo verificado y olas por cliente).

Todo corre contra COPIAS AISLADAS (``tmp_path``: pool, almacen gobernado y catalogo propios; sin Spaces ni Datasphere). El estado «v3» se produce de verdad: el motor corre con la regla VIEJA de
LAYER_BOUNDARY_REVIEW y ``signal-rules/v3`` parcheados. Contrato:
  * elegible = perfil con misma firma de pool y diferencia de versiones EXACTAMENTE ``signals`` v3 -> v4; sigue CURRENT (compatibilidad de transicion) pero queda pendiente de la migracion;
  * CLOSURE_EQUAL + el perfil nuevo solo difiere en ``versions.signals`` y en la revision LAYER_BOUNDARY_REVIEW (y lo derivado de ella);
  * el Juicio se REVALIDA (misma metodologia, nueva firma de perfil), nunca se recalifica; si difiere, la migracion se ABSTIENE;
  * dry-run sin escrituras; ``--apply`` exige confirmacion explicita y el camino asegurado."""
from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from tests import _layer_boundary_pool as LB
from tests.webapp.test_assessment_enrichment import ALWAYS
from tests.webapp.test_governed_assessment_durability import _flow, files, restart
from tools.catalog import catalog_service as cs
from tools.catalog.modeling_assessment import profile as P
from tools.catalog.modeling_assessment import signals as S
from webapp import main
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads
from webapp.services import migration_approval as ma

MIG = "signals_layer_boundary_v4"
V3, V4 = "signal-rules/v3", "signal-rules/v4"
V = LB.view
PHYS = {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VR_TAB", ["SRC_TAB"]), V("VF_NODE", ["VR_TAB"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"}
GOV = {"leaves": {"SRC_CDS": LB.CDS}, "views": [V("VR_CDS", ["SRC_CDS"]), V("VF_NODE", ["VR_CDS"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"}


def _old_rule(idx):
    """LAYER_BOUNDARY_REVIEW de signal-rules/v3 (consumidor INMEDIATO con capa >= 3, sea cual sea su base): reproduce el estado persistido antes del cambio."""
    boundary_fact = idx.usable_get("ingestion.boundary_artifacts", "closure")
    if boundary_fact is None:
        return []
    remote_leaves = {a["artifact"] for a in boundary_fact.value["artifacts"] if a["has_remote_entity"]}
    out = []
    for f in idx.usable("ingestion.first_consumers"):
        if f.subject not in remote_leaves:
            continue
        cons = f.value["lineage_consumers"]
        if cons and all((S._numeric_layer(c["inferred_layer"]) or 0) >= 3 for c in cons):
            out.append(S.Finding("LAYER_BOUNDARY_REVIEW", (f.subject, *sorted(c["consumer"] for c in cons)), S._keys(f, boundary_fact),
                                 {"source": f.subject, "lineage_consumers": [{"consumer": c["consumer"], "inferred_layer": c["inferred_layer"], "inferred_layer_basis": c["inferred_layer_basis"]} for c in cons],
                                  "association_consumer_count": len(f.value["association_consumers"])}))
    return out


@pytest.fixture()
def lworld(tmp_path, monkeypatch):
    """Dos clientes aislados: ``Acme`` (tabla fisica tras una proyeccion) y ``Bcme`` (CDS gobernada tras una proyeccion)."""
    inbound = tmp_path / "pool"
    for client, topo in (("Acme", PHYS), ("Bcme", GOV)):
        root = tmp_path / "stage" / client
        LB.write_pool(root, topo)
        (inbound / client).parent.mkdir(parents=True, exist_ok=True)
        (root / LB.CLIENT).rename(inbound / client)
    governed = tmp_path / "governed-assessments"
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(governed))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "runtime-cache")
    monkeypatch.setattr(config, "SPACES_ENDPOINT", "")
    entries = [_flow("Acme", ["AM_TEST", "VF_NODE", "VR_TAB", "SRC_TAB"], "acme-am"), _flow("Bcme", ["AM_TEST", "VF_NODE", "VR_CDS", "SRC_CDS"], "bcme-am")]
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    restart()
    w = SimpleNamespace(inbound=inbound, governed=governed, entries=entries, a=entries[0], b=entries[1], tmp=tmp_path, monkeypatch=monkeypatch)
    assert str(svc.governed_dir()).startswith(str(tmp_path))                              # aislamiento: jamas el estado canonico real
    yield w
    restart()


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _snapshot(w) -> dict:
    return {str(p.relative_to(w.governed)): _digest(p) for p in files(w)}


def _env(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _path(w, client: str) -> Path:
    return next(p for p in files(w) if p.parent.name == client)


def _make_v3(w, *entries) -> dict:
    """Genera con el motor corriendo la regla VIEJA y ``signal-rules/v3`` (estado persistido antes del cambio) sobre copias aisladas. Devuelve {cliente: sobre v3}."""
    out = {}
    with w.monkeypatch.context() as mp:
        mp.setattr(S, "SIGNALS_RULESET", V3)
        mp.setattr(P, "SIGNALS_RULESET", V3)
        mp.setattr(S, "SIGNAL_REGISTRY", tuple(dataclasses.replace(s, rule=_old_rule) if s.signal_id == "LAYER_BOUNDARY_REVIEW" else s for s in S.SIGNAL_REGISTRY))
        mp.setattr(P, "SIGNAL_REGISTRY", S.SIGNAL_REGISTRY)
        restart()
        for e in entries:
            assert svc.generate(e).state == svc.CURRENT
            out[e.cliente] = _env(_path(w, e.cliente))
    restart()
    return out


_BACKUPS = itertools.count(1)


def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="approver", operator="operator")


def _run(w, *entries, apply=False, **kw) -> dict:
    """``apply=True`` recorre el camino REAL asegurado (plan -> autorizacion -> respaldo verificado -> olas); la autorizacion la concede el test (la logica fail-closed se prueba en test_migration_secured_apply)."""
    entries = list(entries) or list(w.entries)
    if not apply:
        return bf.migrate_stale_evidence(entries, MIG, apply=False, governed_state_present=ALWAYS, **kw)
    return bf.secured_migrate(entries, MIG, authorizer=_grant, backup_dir=w.tmp / f"backup-{next(_BACKUPS)}", allow_local_only=True, governed_state_present=ALWAYS, **kw)


def _rec(out: dict, client: str) -> dict:
    return next(r for r in out["records"] if r["client"] == client)


def _lbr(profile: dict) -> dict:
    return next((r for r in profile["review_required"] if r["id"] == "LAYER_BOUNDARY_REVIEW"), {})


# ───────────────────────────── registro y elegibilidad ─────────────────────────────
def test_the_migration_is_registered_in_the_existing_framework_and_targets_only_signals_v3(lworld):
    m = svc.EVIDENCE_MIGRATIONS[MIG]
    assert m.target_keys == ("signals",) and m.eligible_old == {"signals": frozenset({V3})} and m.closure_policy == svc.CLOSURE_EQUAL and m.transitional is True
    assert svc.LAYER_BOUNDARY_MIGRATION == MIG and MIG in bf.CONFIRMATION_REQUIRED and MIG in bf.SECURED_MIGRATIONS and MIG in svc.JUDGMENT_REVALIDATING_MIGRATIONS
    cur = svc.current_assessment_versions()
    assert cur["signals"] == V4 and svc.targeted_migration({**cur, "signals": V3}, cur) == MIG
    assert svc.targeted_migration({**cur, "signals": V3, "confidence": "confidence-cuts/v2"}, cur) is None                 # diferencias ajenas: no elegible, sin encadenamiento
    assert svc.targeted_migration({**cur, "signals": "signal-rules/v2"}, cur) is None                                      # v2 no salta a v4 por esta migracion


def test_a_v3_governed_profile_stays_current_but_pending_the_migration_and_assessment_versions_are_not_touched(lworld):
    old = _make_v3(lworld, lworld.a)["Acme"]
    st = svc.status_for_entry(lworld.a)
    assert (st.state, st.stale_reason, st.pending_migration) == (svc.CURRENT, None, MIG) and st.profile is not None       # compatibilidad de transicion: nada se vuelve STALE por subir el ruleset
    assert old["assessment_versions"]["signals"] == V3 and _env(_path(lworld, "Acme"))["assessment_versions"]["signals"] == V3   # el registro persistido NO se altera por consultarlo


# ───────────────────────────── dry-run ─────────────────────────────
def test_the_dry_run_runs_the_real_engine_in_memory_reports_the_delta_and_writes_nothing(lworld, monkeypatch):
    old = _make_v3(lworld, lworld.a, lworld.b)
    snap = _snapshot(lworld)

    def boom(*a, **k):
        raise AssertionError("el dry-run no debe escribir")
    monkeypatch.setattr(svc, "commit_stale_migration", boom)
    monkeypatch.setattr(svc, "commit_regenerated", boom)
    monkeypatch.setattr(svc, "_store_envelope", boom)
    out = bf.migrate_stale_evidence(lworld.entries, MIG, apply=False, governed_state_present=ALWAYS, commit=boom)
    assert out["dry_run"] and not out["apply"] and out["counts"]["would_regenerate"] == 2 and out["counts"]["eligible"] == 2 and out["plan"]["digest"]
    for client in ("Acme", "Bcme"):
        r = _rec(out, client)
        assert r["action"] == bf.WOULD_REGENERATE and r["eligible"]
        d = r["delta"]
        assert d["versions"] == {"signals": {"old": V3, "new": V4}} and d["closure"]["drift"] is False and d["coverage_strict"]["changed"] is False
        assert d["judgment"]["judgment_action"] == svc.JDG_SIGNATURE_REBOUND and d["judgment"]["previous_judgment_level"] == d["judgment"]["new_judgment_level"]
    assert _rec(out, "Acme")["delta"]["review_occurrences"]["new"] == _rec(out, "Acme")["delta"]["review_occurrences"]["old"]                 # fisica: sigue una ocurrencia (con otro fundamento)
    assert _rec(out, "Bcme")["delta"]["review_occurrences"]["new"] == _rec(out, "Bcme")["delta"]["review_occurrences"]["old"] - 1               # gobernada: la ocurrencia desaparece
    lb_a, lb_b = _rec(out, "Acme")["delta"]["layer_boundary"], _rec(out, "Bcme")["delta"]["layer_boundary"]
    assert lb_a["sources"] == {"old": ["SRC_TAB"], "new": ["SRC_TAB"]} and lb_a["removed_sources"] == [] and lb_a["context_question"] == {"old": True, "new": True}
    assert lb_b["sources"] == {"old": ["SRC_CDS"], "new": []} and lb_b["removed_sources"] == ["SRC_CDS"] and lb_b["context_question"] == {"old": True, "new": False}
    assert _snapshot(lworld) == snap and {c: _env(_path(lworld, c)) for c in ("Acme", "Bcme")} == old                                         # CERO escrituras
    assert "profile_json" not in json.dumps(out)                                                                                               # el informe no vuelca perfiles


# ───────────────────────────── aplicacion asegurada ─────────────────────────────
def test_apply_migrates_with_a_profile_identical_except_the_review_and_a_revalidated_judgment(lworld):
    old = _make_v3(lworld, lworld.a, lworld.b)
    out = _run(lworld, apply=True)
    assert out["counts"]["regenerated_local"] == 2 and out["counts"]["abstained"] == 0 and out["counts"]["failed"] == 0
    for client in ("Acme", "Bcme"):
        new = _env(_path(lworld, client))
        assert new["assessment_versions"] == svc.current_assessment_versions() and new["assessment_versions"]["signals"] == V4
        assert new["input_pool_signature"] == old[client]["input_pool_signature"]                                           # misma firma de pool
        assert svc._without_layer_boundary(new["profile_json"]) == svc._without_layer_boundary(old[client]["profile_json"])  # CLOSURE_EQUAL estricto: todo lo demas identico
        assert svc.closure_identity(new["profile_json"]) == svc.closure_identity(old[client]["profile_json"])
        assert svc.status_for_entry(lworld.a if client == "Acme" else lworld.b).state == svc.CURRENT
        assert new["assessment_artifact_signature"] == svc._artifact_signature(new["profile_json"])
        jo, jn = old[client]["judgment"], new["judgment"]
        assert svc.judgment_methodology_diff(jo, jn) == [] and (jn["level"], jn["level_rule"]) == (jo["level"], jo["level_rule"])        # NIVELES Y RAZONES INTACTOS
        assert [r["reason_id"] for r in jn["reasons"]] == [r["reason_id"] for r in jo["reasons"]]
        assert svc.classify_judgment_revalidation(new)[0] == svc.JDG_CURRENT
    acme, bcme = _env(_path(lworld, "Acme")), _env(_path(lworld, "Bcme"))
    occ = _lbr(acme["profile_json"])["occurrences"][0]["values"]
    assert occ["functional_consumers"][0]["consumer"] == "VF_NODE" and occ["functional_consumers"][0]["reached_through"] == ["VR_TAB"]       # nuevo contenido de la observacion
    assert _lbr(bcme["profile_json"]) == {}                                                                                             # CDS gobernada: la observacion falsa desaparece
    qs = lambda p: {q["id"] for q in p["summary"]["context_required"]["questions"]}                                                  # noqa: E731
    assert "LAYER_BOUNDARY_REVIEW" in qs(old["Bcme"]["profile_json"]) and "LAYER_BOUNDARY_REVIEW" not in qs(bcme["profile_json"]) and "LAYER_BOUNDARY_REVIEW" in qs(acme["profile_json"])
    assert bcme["profile_json"]["summary"]["context_required"]["open_questions"] == old["Bcme"]["profile_json"]["summary"]["context_required"]["open_questions"] - 1


def test_the_migration_is_idempotent_and_a_second_run_writes_nothing(lworld):
    _make_v3(lworld, lworld.a)
    assert _rec(_run(lworld, lworld.a, apply=True), "Acme")["action"] == bf.REGENERATED
    snap = _snapshot(lworld)
    out = _run(lworld, lworld.a, apply=True)
    assert _rec(out, "Acme")["reason"] == bf.SKIPPED_ALREADY_CURRENT and out["counts"]["eligible"] == 0 and out["counts"]["regenerated_local"] == 0 and _snapshot(lworld) == snap


def test_a_direct_apply_of_the_sensitive_migration_is_refused_never_downgraded_to_a_dry_run(lworld):
    _make_v3(lworld, lworld.a)
    snap = _snapshot(lworld)
    with pytest.raises(bf.ApplyRequiresAuthorization):
        bf.migrate_stale_evidence([lworld.a], MIG, apply=True, governed_state_present=ALWAYS)
    assert _snapshot(lworld) == snap


# ───────────────────────────── abstencion ─────────────────────────────
def _expect_abstention(w, code: str, mutate_engine=None, monkeypatch=None):
    old = _make_v3(w, w.a)["Acme"]
    if mutate_engine is not None:
        real = svc._engine_assess

        def patched(*a, **k):
            doc, meta = real(*a, **k)
            mutate_engine(doc)
            return doc, meta
        monkeypatch.setattr(svc, "_engine_assess", patched)
    snap = _snapshot(w)
    out = _run(w, w.a, apply=True)
    r = _rec(out, "Acme")
    assert r["action"] == bf.ABSTAINED and r["reason"] == code and out["counts"]["abstained"] == 1 and out["counts"]["regenerated_local"] == 0, r
    assert _snapshot(w) == snap and _env(_path(w, "Acme")) == old                       # el perfil anterior queda intacto
    return out


def test_it_abstains_when_anything_beyond_the_review_changes_in_the_profile(lworld, monkeypatch):
    def tamper(doc):
        doc["summary"]["coverage"]["strict"] = 0.5
    _expect_abstention(lworld, "NON_LAYER_BOUNDARY_CONTENT_CHANGED", tamper, monkeypatch)


def test_it_abstains_when_another_review_changes(lworld, monkeypatch):
    def tamper(doc):
        other = next(r for r in doc["review_required"] if r["id"] != "LAYER_BOUNDARY_REVIEW")
        other["title"] = other["title"] + " (alterado)"
    _expect_abstention(lworld, "NON_LAYER_BOUNDARY_CONTENT_CHANGED", tamper, monkeypatch)


def test_it_abstains_on_closure_drift(lworld, monkeypatch):
    def tamper(doc):
        doc["scope"]["closure_artifacts"] = [*doc["scope"]["closure_artifacts"], "EXTRA_ARTIFACT"]
    _expect_abstention(lworld, "CLOSURE_DRIFT", tamper, monkeypatch)


def test_it_abstains_when_the_judgment_methodology_would_differ_instead_of_requalifying(lworld, monkeypatch):
    _make_v3(lworld, lworld.a)
    snap = _snapshot(lworld)
    real = svc._derive_judgment
    monkeypatch.setattr(svc, "_derive_judgment", lambda profile, ratified=None: {**real(profile, ratified), "level": "BUENA"})
    out = _run(lworld, lworld.a, apply=True)
    r = _rec(out, "Acme")
    assert r["action"] == bf.ABSTAINED and r["reason"] == "JUDGMENT_METHODOLOGY_DIVERGED" and out["counts"]["regenerated_local"] == 0 and _snapshot(lworld) == snap


def test_a_profile_with_other_version_differences_is_not_eligible_and_is_reported_apart(lworld):
    _make_v3(lworld, lworld.a)
    p = _path(lworld, "Acme")
    env = _env(p)
    env["profile_json"]["versions"]["confidence"] = "confidence-cuts/v2"
    env["assessment_versions"] = dict(env["profile_json"]["versions"])
    env["assessment_artifact_signature"] = svc._artifact_signature(env["profile_json"])
    p.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    restart()
    snap = _snapshot(lworld)
    out = _run(lworld, lworld.a, apply=True)
    assert _rec(out, "Acme")["reason"] == bf.VERSION_ONLY_UNTARGETED and out["counts"]["eligible"] == 0 and _snapshot(lworld) == snap


# ───────────────────────────── CLI ─────────────────────────────
def _cli(monkeypatch, argv):
    seen, secured = [], []
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, []))
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda es, name, apply=False, **k: seen.append(("migrate", name, apply)) or {"counts": {"failed": 0, "abstained": 0}, "environment_warnings": []})
    monkeypatch.setattr(bf, "secured_migrate", lambda es, name, **k: secured.append(name) or {"outcome": "APPLIED", "counts": {"failed": 0, "abstained": 0}, "environment_warnings": []})
    return bf.main(argv), seen, secured


def test_cli_exposes_the_migration_and_apply_needs_the_explicit_confirmation_naming_it(monkeypatch):
    assert _cli(monkeypatch, ["--regenerate-stale-evidence", MIG, "--dry-run"])[1] == [("migrate", MIG, False)]
    for argv in (["--regenerate-stale-evidence", MIG], ["--regenerate-stale-evidence", MIG, "--apply"],
                 ["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", "evidence_confidence_v3"]):
        with pytest.raises(SystemExit) as e:
            _cli(monkeypatch, argv)
        assert e.value.code == 2                                                                                          # usage: cero escrituras
    code, seen, secured = _cli(monkeypatch, ["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", MIG])
    assert (code, seen, secured) == (0, [], [MIG])                                                                       # SOLO por el camino asegurado


# ───────────────────────────── confianza: unico movimiento permitido, explicado por los resultados de regla ─────────────────────────────
def _profile(lworld, entry):
    res = svc.resolve_entry(entry)
    doc, _meta = svc._engine_assess(res.pool_root, res.client_dir, res.terminal_am, res.domain_dir)
    return doc


def _move_rule_inputs(doc: dict, value: float, level=None) -> dict:
    import copy
    out = copy.deepcopy(doc)
    for c in out["summary"]["confidence"]["components"]:
        if c["id"] == "complete_rule_inputs":
            c["value"] = value
            c["summary"] = f"proporción confiable: {value}"
            if level:
                c["level"] = level
    return out


def test_only_the_rule_inputs_share_of_confidence_may_move_and_only_when_the_profile_own_results_explain_it(lworld):
    base = _profile(lworld, lworld.a)
    share = svc._rule_conclusion_share(base)
    assert share is not None and svc._rule_inputs_component_value(base) == share                           # la parte de la confianza SE deriva de los resultados de regla del perfil
    svc._check_layer_boundary_delta(base, base)                                                            # identico: ok
    # un movimiento de esa parte NO explicado por las superficies del propio perfil => abstencion (nadie puede colar un cambio de confianza)
    with pytest.raises(svc.EnrichmentError) as e:
        svc._check_layer_boundary_delta(_move_rule_inputs(base, 0.1234), base)
    assert e.value.code == "NON_LAYER_BOUNDARY_CONTENT_CHANGED"
    # el nivel de confianza NO puede cambiar por esta migracion: decision humana
    import copy
    lifted = copy.deepcopy(base)
    lifted["summary"]["confidence"]["level"] = "LOW" if base["summary"]["confidence"]["level"] != "LOW" else "HIGH"
    with pytest.raises(svc.EnrichmentError) as e:
        svc._check_layer_boundary_delta(lifted, base)
    assert e.value.code == "CONFIDENCE_LEVEL_CHANGED" and "CONFIDENCE_LEVEL_CHANGED" in bf.ABSTENTION_CODES


def test_a_confidence_level_change_makes_the_migration_abstain_and_leaves_the_previous_profile_untouched(lworld, monkeypatch):
    def tamper(doc):
        doc["summary"]["confidence"]["level"] = "LOW" if doc["summary"]["confidence"]["level"] != "LOW" else "HIGH"
    _expect_abstention(lworld, "CONFIDENCE_LEVEL_CHANGED", tamper, monkeypatch)
