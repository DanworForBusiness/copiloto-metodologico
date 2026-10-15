"""canonical-evidence/v4.A — seccion ``leaf_materialization``: persistencia del resultado DERIVADO de ``leaf-materialization/v1`` y de sus pruebas tecnicas minimas (sin CSN completo), con referencias
RECUPERABLES (archivo del pool + puntero + hashes + firma del pool), auditoria, compatibilidad historica (v3 sin la seccion = evidencia NO evaluada) y no regresion del Juicio (judgment-rules/v2).

Pool real minimo (Kensing: AM -> vista -> tabla local delta-capable X -> D, con un Replication Flow que escribe en D) generado por ``shadow.assess``: ejercita el recolector, el contrato y el Juicio."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.test_modeling_assessment_delta_shadow import _build_pool
from tools.catalog import leaf_materialization as LM
from tools.catalog import leaf_materialization_evidence as LME
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog.modeling_assessment import evidence_contract as EC
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as sh
from tools.catalog.modeling_assessment.canonical_sections import V4_SECTIONS
from tools.catalog.modeling_assessment.evidence_contract_v4 import validate_leaf_materialization
from tools.catalog.modeling_assessment.evidence_errors import CanonicalEvidenceError
from webapp.services import assessment_service as svc

KEY = LME.SECTION_KEY
COLS = [{"name": "CostCenter"}, {"name": "Name"}]


def _full_rf(dst: str, *, mappings=None, source_columns=None) -> None:
    """Reemplaza el RF del pool por uno con tarea completa (origen CDS gobernada, columnas origen/destino, destino fisico D)."""
    task = {"name": "replicationtask1", "loadType": "REPLICATE", "truncate": False,
            "sourceObject": {"name": "ZCDS_X", "definition": {"columns": source_columns or COLS}},
            "targetObject": {"name": "1TR_X_Delta", "definition": {"columns": COLS}, "properties": {}},
            "transform": {"attributeMappings": mappings or []}}
    flow = {"targets": {"1TR_X_Delta": {}}, "connections": {"S4_HANA": {}},
            "contents": {"sourceSystem": [{"connectionId": "S4_HANA", "connectionType": "SAPS4HANAOP", "container": "/CDS_EXTRACTION"}], "replicationTasks": [task]}}
    Path(dst, "Kensing", "replication-flows", "RF_X.json").write_text(json.dumps({"replicationflows": {"RF_X": flow}}), encoding="utf-8")


def _assess(dst: str) -> dict:
    os.environ["DANWOR_CATALOG_CSN_CACHE"] = dst
    doc, _meta = sh.assess(Path(dst), "Kensing", "4AM_ROOT", "General", generic_root=True, root_type="analytic-model")
    return doc


@pytest.fixture
def pool(tmp_path):
    dst = str(tmp_path / ".csn_cache")
    _build_pool(dst)
    _full_rf(dst)
    return dst


@pytest.fixture
def doc(pool):
    d = _assess(pool)
    ma_profile.validate_assessment(d)
    return d


def _sec(d: dict) -> dict:
    return d["canonical_evidence"][KEY]


def _leaf(d: dict, name: str = "1TR_X") -> dict:
    return next(r for r in _sec(d)["leaves"] if r["leaf"] == name)


def _resolver(root: str):
    def r(rel: str):
        p = Path(root) / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    return r


def _v3(d: dict) -> dict:
    """El mismo documento tal como lo habria emitido el motor v3 (sin la seccion v4, con las versiones v3)."""
    old = copy.deepcopy(d)
    old["canonical_evidence"].pop(KEY)
    old["canonical_evidence"]["version"] = EC.CANONICAL_EVIDENCE_VERSION_V3
    old["versions"]["canonical_evidence"] = EC.CANONICAL_EVIDENCE_VERSION_V3
    return old


# ───────────────────────────── contrato y serializacion ─────────────────────────────
def test_the_profile_declares_v4_and_persists_the_derived_section_with_the_exact_identities(doc):
    assert doc["versions"]["canonical_evidence"] == doc["canonical_evidence"]["version"] == "canonical-evidence/v4" and set(V4_SECTIONS) == {KEY}
    s = _sec(doc)
    assert s["version"] == LM.LEAF_MATERIALIZATION_VERSION and s["evidence_version"] == LME.EVIDENCE_VERSION and s["evaluation_state"] == LME.EVALUATED
    assert s["derivation"] == {"module": LME.DERIVATION_MODULE, "version": LM.LEAF_MATERIALIZATION_VERSION}
    r = _leaf(doc)
    assert (r["leaf"], r["materialization_kind"], r["derived_boundary_class"], r["verifiability"], r["unknown_reasons"]) == ("1TR_X", "REPLICA", "GOVERNED_DERIVED", LME.VER_BY_CSN, [])
    p = r["producers"][0]
    assert (p["kind"], p["name"], p["target_match"], p["destination"], p["resolved_via"], p["shadow_entity"]) == ("REPLICATION_FLOW", "RF_X", "DELTA_SHADOW_SUFFIX", "1TR_X_Delta", "CSN_DELTA_LINK", "1TR_X_Delta")
    assert p["wrapper"]["reads_shadow"] is True and p["wrapper"]["projection"] == "COLUMNS_ONLY" and p["wrapper"]["column_identity"] == "VERIFIED_DEFINITION"
    assert p["mapping_profile"]["implicit_identity"] is True and p["mapping_profile"]["column_evidence"] == "VERIFIED"


def test_the_persisted_section_serializes_deterministically_and_round_trips_as_json(pool):
    a, b = _assess(pool), _assess(pool)
    assert json.dumps(a["canonical_evidence"], sort_keys=True) == json.dumps(b["canonical_evidence"], sort_keys=True)
    rt = json.loads(ma_profile.canonical_assessment_json(a))
    assert rt["canonical_evidence"][KEY] == json.loads(json.dumps(_sec(a)))
    ma_profile.validate_assessment(rt)


def test_the_full_csn_is_never_persisted_only_verdicts_proofs_and_references(doc):
    text = json.dumps(_sec(doc), ensure_ascii=False)
    for forbidden in ("Cost center", "@EndUserText", "\"query\"", "\"SELECT\"", "\"elements\"", "\"definitions\""):
        assert forbidden not in text, forbidden
    tr = _leaf(doc)["technical_references"]
    assert set(tr) == {"delta_links", "rf_columns", "case_equivalence"} and len(tr["delta_links"]) == 1 and len(tr["rf_columns"]) == 1
    assert "CostCenter" not in json.dumps(tr) and tr["rf_columns"][0]["n_source_columns"] == 2                           # solo huellas y conteos, no los nombres de columna


def test_references_are_recoverable_not_just_hashes(doc, pool):
    sig = doc["scope"]["pool_signature"]
    d = _leaf(doc)["technical_references"]["delta_links"][0]
    ref = d["entity_definition"]
    assert ref["source_file"] == "Kensing/local-tables/1TR_X.json" and ref["pointer"] == "/definitions/1TR_X" and ref["pool_signature"] == sig
    raw = Path(pool, ref["source_file"]).read_bytes()
    assert ref["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert ref["fragment_sha256"] == LME.sha256_json(json.loads(raw)["definitions"]["1TR_X"])
    assert d["shadow_definition"]["pointer"] == "/definitions/1TR_X_Delta"
    task = _leaf(doc)["technical_references"]["rf_columns"][0]["task_reference"]
    assert task["source_file"] == "Kensing/replication-flows/RF_X.json" and task["pointer"] == "/replicationflows/RF_X/contents/replicationTasks/0"
    refs = _sec(doc)["derivation_inputs"]["csn"]["references"]
    assert {(r["source_file"], r["pointer"]) for r in refs} == {(ref["source_file"], ref["pointer"]), (d["shadow_definition"]["source_file"], d["shadow_definition"]["pointer"]),
                                                               (task["source_file"], task["pointer"])}
    assert _sec(doc)["derivation_inputs"]["csn"]["provided"] is True and _sec(doc)["derivation_inputs"]["csn"]["pool_signature"] == sig


# ───────────────────────────── auditoria: fuente recuperable / no recuperable ─────────────────────────────
def test_audit_verifies_every_reference_against_the_original_source(doc, pool):
    a = LME.audit_section(_sec(doc), _resolver(pool))
    assert a["all_verified"] and a["counts"] == {LME.AUD_VERIFIED: 3} and not a["has_unrecoverable"]


def test_audit_reports_an_unrecoverable_source_instead_of_trusting_the_hash(doc, pool):
    os.remove(Path(pool, "Kensing", "local-tables", "1TR_X.json"))
    a = LME.audit_section(_sec(doc), _resolver(pool))
    assert a["has_unrecoverable"] and not a["all_verified"] and a["counts"][LME.AUD_NOT_RECOVERABLE] == 2 and a["counts"][LME.AUD_VERIFIED] == 1


def test_audit_detects_a_changed_source_and_a_missing_pointer(doc, pool):
    p = Path(pool, "Kensing", "local-tables", "1TR_X.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["definitions"]["1TR_X"]["@EndUserText.label"] = "cambiado"
    p.write_text(json.dumps(data), encoding="utf-8")
    a = LME.audit_section(_sec(doc), _resolver(pool))
    assert a["counts"][LME.AUD_FRAGMENT_MISMATCH] == 1
    del data["definitions"]["1TR_X"]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert LME.audit_section(_sec(doc), _resolver(pool))["counts"][LME.AUD_POINTER_NOT_FOUND] == 1


# ───────────────────────────── reproducibilidad y correspondencia con la derivacion ─────────────────────────────
def test_the_persisted_records_correspond_to_the_original_derivation(doc, pool):
    from tools.catalog.modeling_assessment.materialization_evidence import collect_materialization_evidence
    ce = {k: v for k, v in doc["canonical_evidence"].items() if k != KEY}
    root = Path(pool)
    files = sorted(p for p in root.rglob("*.json"))
    ev = collect_materialization_evidence(files, root, ce, doc["scope"]["pool_signature"])
    derived = LM.derive_all(ce, ev["csn"])
    assert [r["leaf"] for r in _sec(doc)["leaves"]] == sorted(derived)
    for rec in _sec(doc)["leaves"]:
        base = {k: v for k, v in rec.items() if k not in ("verifiability", "technical_references")}
        assert base == json.loads(json.dumps(derived[rec["leaf"]], default=str)), rec["leaf"]                           # la seccion es EXACTAMENTE la derivacion (fuente unica)
        assert rec["verifiability"] == LME.verifiability_of(derived[rec["leaf"]])
    rebuilt = LME.build_section(ce, ev["csn"], ev["references"], pool_signature=doc["scope"]["pool_signature"])
    assert json.dumps(rebuilt, sort_keys=True) == json.dumps(_sec(doc), sort_keys=True)                                 # reproducible desde evidencia + pool


def test_the_section_is_a_pure_function_of_its_inputs_and_never_mutates_them(doc):
    ce = {k: v for k, v in doc["canonical_evidence"].items() if k != KEY}
    snap = copy.deepcopy(ce)
    a, b = LME.build_section(ce), LME.build_section(ce)
    assert ce == snap and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ───────────────────────────── UNKNOWN e insuficientemente verificable ─────────────────────────────
def test_without_pool_evidence_the_delta_relation_stays_unknown_and_the_limitation_is_declared(doc):
    ce = {k: v for k, v in doc["canonical_evidence"].items() if k != KEY}
    s = LME.build_section(ce)                                                                                          # sin pool: sin CSN
    csn = s["derivation_inputs"]["csn"]
    assert csn == {"provided": False, "pool_signature": None, "limitations": [LME.LIM_CSN_NOT_PROVIDED], "references": []}
    r = next(x for x in s["leaves"] if x["leaf"] == "1TR_X")
    assert r["materialization_kind"] == "UNKNOWN" and r["unknown_reasons"] == [LM.U_TARGET_MATCH_NOT_EXACT] and r["verifiability"] == LME.VER_UNVERIFIABLE
    assert all(p["resolved_via"] is None for p in r["producers"]) and r["technical_references"] == {"delta_links": [], "rf_columns": [], "case_equivalence": []}
    validate_leaf_materialization(s, ce)


def test_a_leaf_without_producer_is_not_evaluated_never_clean_and_an_unverifiable_wrapper_is_unverifiable(pool, tmp_path):
    ce = {"leaf_provenance": [{"leaf": "T_NO_PROD", "producer_flows": [], "basis": "NONE", "provenance_state": None}], "processing_flow_properties": {"flows": []},
          "physical_source_boundaries": {"leaves": []}}
    r = LME.build_section(ce)["leaves"][0]
    assert r["materialization_kind"] == "UNKNOWN" and r["unknown_reasons"] == [LM.U_NO_PRODUCER] and r["verifiability"] == LME.VER_NOT_EVALUATED
    # wrapper cuya definicion falta en el pool: el vinculo se declara pero no se verifica => UNVERIFIABLE con causa
    dst = str(tmp_path / "p2" / ".csn_cache")
    _build_pool(dst)
    _full_rf(dst)
    p = Path(dst, "Kensing", "local-tables", "1TR_X.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["definitions"]["1TR_X"]["query"]["SELECT"]["mixin"] = {}                                                         # forma de SELECT no reconocida: el wrapper no se puede verificar
    p.write_text(json.dumps(data), encoding="utf-8")
    d = _assess(dst)
    ma_profile.validate_assessment(d)
    r = _leaf(d)
    assert r["materialization_kind"] == "UNKNOWN" and r["verifiability"] == LME.VER_UNVERIFIABLE and LM.U_DELTA_WRAPPER_NOT_VERIFIABLE in r["unknown_reasons"]
    assert _sec(d)["counts"]["by_verifiability"].get(LME.VER_UNVERIFIABLE) == 1


def test_a_source_reference_that_cannot_be_recorded_is_declared_as_a_limitation(doc):
    ce = {k: v for k, v in doc["canonical_evidence"].items() if k != KEY}
    csn = {"entity_definitions": {"1TR_X": {"@DataWarehouse.delta": {"deltaFromEntities": ["1TR_X_Delta"]}, "query": {"SELECT": {"from": {"ref": ["1TR_X_Delta"]}, "columns": [{"ref": ["CostCenter"]}]}}},
                                  "1TR_X_Delta": {"elements": {"CostCenter": {}}}}, "delta_links": {}, "rf_task_columns": {}}
    s = LME.build_section(ce, csn, references={}, pool_signature="s" * 8)
    assert LME.LIM_REFERENCE_UNAVAILABLE in s["derivation_inputs"]["csn"]["limitations"]
    assert next(x for x in s["leaves"] if x["leaf"] == "1TR_X")["technical_references"]["delta_links"][0]["entity_definition"] is None
    validate_leaf_materialization(s, ce)                                                                               # valido: la limitacion esta declarada, no oculta


# ───────────────────────────── el contrato rechaza lo que no cumple ─────────────────────────────
def _tamper(doc: dict, fn) -> None:
    ce = copy.deepcopy(doc["canonical_evidence"])
    fn(ce[KEY], ce)
    with pytest.raises(CanonicalEvidenceError):
        EC.validate_canonical_evidence(ce)


@pytest.mark.parametrize("name, fn", [
    ("clave extra en la seccion", lambda s, ce: s.__setitem__("csn_full", {})),
    ("version de derivacion desconocida", lambda s, ce: s.__setitem__("version", "leaf-materialization/v9")),
    ("estado de evaluacion invalido", lambda s, ce: s.__setitem__("evaluation_state", "MAYBE")),
    ("NOT_EVALUATED con hojas (evidencia no evaluada nunca es 'sin problemas')", lambda s, ce: s.update(evaluation_state="NOT_EVALUATED", not_evaluated_reason=LME.NE_SOURCE_SECTIONS_MISSING)),
    ("hoja faltante", lambda s, ce: s["leaves"].pop()),
    ("hoja duplicada", lambda s, ce: s["leaves"].append(copy.deepcopy(s["leaves"][0]))),
    ("clave extra en una hoja", lambda s, ce: s["leaves"][0].__setitem__("quality", "good")),
    ("kind incoherente con las causas", lambda s, ce: s["leaves"][0].__setitem__("materialization_kind", "UNKNOWN")),
    ("causa fuera del vocabulario", lambda s, ce: s["leaves"][0].__setitem__("unknown_reasons", ["INVENTADA"]) or s["leaves"][0].__setitem__("materialization_kind", "UNKNOWN")),
    ("clase de borde fuera del vocabulario", lambda s, ce: s["leaves"][0].__setitem__("derived_boundary_class", "RAW")),
    ("verifiability incoherente", lambda s, ce: s["leaves"][0].__setitem__("verifiability", LME.VER_NOT_EVALUATED)),
    ("relacion Delta resuelta sin evidencia tecnica declarada", lambda s, ce: s["derivation_inputs"]["csn"].update(provided=False, limitations=[LME.LIM_CSN_NOT_PROVIDED], pool_signature=None, references=[])),
    ("referencia con ruta absoluta", lambda s, ce: s["derivation_inputs"]["csn"]["references"][0].__setitem__("source_file", "/etc/passwd")),
    ("referencia con salto de directorio", lambda s, ce: s["derivation_inputs"]["csn"]["references"][0].__setitem__("source_file", "../x.json")),
    ("hash de fragmento invalido", lambda s, ce: s["derivation_inputs"]["csn"]["references"][0].__setitem__("fragment_sha256", "abc")),
    ("referencia sin firma de pool", lambda s, ce: s["derivation_inputs"]["csn"]["references"][0].__setitem__("pool_signature", "otra")),
    ("referencia usada que no figura en la lista", lambda s, ce: s["derivation_inputs"]["csn"].__setitem__("references", s["derivation_inputs"]["csn"]["references"][1:])),
    ("conteos inconsistentes", lambda s, ce: s["counts"].__setitem__("leaves", 99)),
    ("clave de veredicto prohibida", lambda s, ce: s["leaves"][0]["flags"].__setitem__("score", 1) or s["leaves"][0].__setitem__("flags", {**s["leaves"][0]["flags"], "score": 1})),
])
def test_the_contract_rejects_invalid_sections(doc, name, fn):
    _tamper(doc, fn)


def test_v4_requires_the_section_and_older_versions_reject_it(doc):
    ce = copy.deepcopy(doc["canonical_evidence"])
    ce.pop(KEY)
    with pytest.raises(CanonicalEvidenceError):
        EC.validate_canonical_evidence(ce)
    old = copy.deepcopy(doc["canonical_evidence"])
    old["version"] = EC.CANONICAL_EVIDENCE_VERSION_V3
    with pytest.raises(CanonicalEvidenceError):
        EC.validate_canonical_evidence(old)                                                                           # una v3 no puede traer claves de v4


# ───────────────────────────── compatibilidad historica ─────────────────────────────
def test_v3_profiles_without_the_section_remain_valid_and_readable(doc):
    old = _v3(doc)
    ma_profile.validate_assessment(old)
    EC.validate_canonical_evidence(old["canonical_evidence"])


def test_a_historical_profile_reads_as_not_evaluated_never_as_no_problems(doc):
    ev = ProfileEvidence(_v3(doc))
    view = ev.persisted_materialization
    assert view["persisted"] is False and view["state"] == LME.NOT_EVALUATED and view["reason"] == LM.U_SECTION_NOT_PERSISTED and view["leaves"] == {}
    r = ev.persisted_materialization_of("1TR_X")
    assert r["materialization_kind"] == "UNKNOWN" and r["unknown_reasons"] == [LM.U_SECTION_NOT_PERSISTED] and r["verifiability"] == LME.VER_NOT_EVALUATED and r["derived_boundary_class"] == "UNDETERMINED"
    assert ev.persisted_materialization_of("NO_EXISTE")["unknown_reasons"] == [LM.U_SECTION_NOT_PERSISTED]
    for garbage in (None, {}, [], {KEY: "x"}, {KEY: {"leaves": "x"}}):
        assert LME.read_section(garbage)["state"] == LME.NOT_EVALUATED and LME.read_section(garbage)["leaves"] == {}


def test_the_persisted_view_of_a_v4_profile_is_the_single_source_and_ignores_a_runtime_csn(doc):
    ev = ProfileEvidence(doc, materialization_csn={"delta_links": {"1TR_X": ("OTRA",)}, "entity_definitions": {}})
    assert ev.persisted_materialization["persisted"] is True and ev.persisted_materialization["state"] == LME.EVALUATED
    assert ev.materialization == {r["leaf"]: r for r in _sec(doc)["leaves"]} and ev.materialization_of("1TR_X")["materialization_kind"] == "REPLICA"
    v3 = ProfileEvidence(_v3(doc))                                                                                      # historico: derivacion experimental sobre la evidencia ya persistida
    assert v3.materialization == LM.derive_all(_v3(doc)["canonical_evidence"]) and v3.materialization["1TR_X"]["materialization_kind"] == "UNKNOWN"


def test_both_contract_versions_are_accepted_by_the_judgment_gate(doc):
    assert ProfileEvidence(doc).has_canonical_evidence_v3 and ProfileEvidence(_v3(doc)).has_canonical_evidence_v3
    bad = _v3(doc)
    bad["canonical_evidence"]["version"] = "canonical-evidence/v2"
    assert not ProfileEvidence(bad).has_canonical_evidence_v3


# ───────────────────────────── firmas y vigencia (STALE) ─────────────────────────────
def test_a_v3_profile_is_covered_exactly_by_the_v4c_migration_and_is_transitionally_current(doc):
    cur = svc.current_assessment_versions()
    assert cur["canonical_evidence"] == "canonical-evidence/v4" and cur["assessability"] == "assessability-rules/v4" and cur["confidence"] == "confidence-cuts/v3"       # solo cambia canonical_evidence
    old = dict(cur, canonical_evidence="canonical-evidence/v3")
    c = svc.classify_currentness("sig", old, "sig", cur)
    # Opcion A (compatibilidad transitoria estricta): la UNICA diferencia es la que cubre la migracion => CURRENT pendiente de ella (la diferencia sigue visible)
    assert (c.state, c.version_diff, c.pool_same, c.pending_migration) == (svc.CURRENT, ("canonical_evidence",), True, "evidence_leaf_materialization_v4")
    assert svc.targeted_migration(old, cur) == "evidence_leaf_materialization_v4"                                         # la migracion registrada cubre EXACTAMENTE esa diferencia
    assert svc.classify_currentness("sig", cur, "sig", cur).pending_migration is None
    assert svc.classify_currentness("old", old, "new", cur).reason == svc.STALE_VERSION_AND_INPUT                         # otro motivo de STALE: no se oculta


def test_the_signature_of_the_profile_changes_only_through_the_new_section_and_version(doc):
    old, new = _v3(doc), copy.deepcopy(doc)
    assert svc._artifact_signature(old) != svc._artifact_signature(new)
    strip = copy.deepcopy(new)
    strip["canonical_evidence"].pop(KEY)
    strip["canonical_evidence"]["version"] = old["canonical_evidence"]["version"]
    strip["versions"]["canonical_evidence"] = old["versions"]["canonical_evidence"]
    assert svc._artifact_signature(strip) == svc._artifact_signature(old)                                              # nada mas cambia en el perfil


# ───────────────────────────── el enriquecimiento no cambia nivel, fortalezas, razones, cobertura ni confianza ─────────────────────────────
def test_enrichment_changes_no_coverage_confidence_findings_or_judgment(doc):
    old = _v3(doc)
    for k in ("summary", "areas", "observations", "review_required", "descriptive_signals", "rule_results", "evidence_limitations", "surfaces", "fact_index", "scope"):
        assert old[k] == doc[k], k
    assert {k: v for k, v in old["versions"].items() if k != "canonical_evidence"} == {k: v for k, v in doc["versions"].items() if k != "canonical_evidence"}
    a, b = evaluate_judgment(old), evaluate_judgment(doc)
    keep = ("status", "level", "level_rule", "closure", "strengths", "reasons", "review_candidates", "evidence_limitations", "qualifiers", "ruleset_version")
    assert {k: a.get(k) for k in keep} == {k: b.get(k) for k in keep}
    assert b["ruleset_version"] == "judgment-rules/v2" and "ruleset_options" not in b


def test_the_experimental_variants_read_the_persisted_section_without_any_runtime_csn(doc):
    v = ("T5_LOCAL_PHYSICAL", "LEVEL_RESOLUTION", "MATERIALIZED_INPUT")
    a = evaluate_judgment(doc, variants=v)
    assert evaluate_judgment(doc, variants=v, materialization_csn={"delta_links": {"1TR_X": ("OTRA",)}})["output_signature"] == a["output_signature"]    # el CSN de ejecucion no se mezcla
    mo = a["ruleset_options"]["materialization"]
    assert mo["source"] == "PERSISTED_SECTION" and mo["csn_evidence_provided"] is True and a["ruleset_options"]["variants"] == sorted(v)          # la seccion persistida declara que hubo evidencia tecnica del pool


# ───────────────────────────── aislamiento arquitectonico ─────────────────────────────
def test_the_bridge_is_the_single_access_point_and_re_exports_the_same_neutral_modules():
    from tools.catalog.modeling_assessment import legacy_bridge as b
    assert b.leaf_materialization is LM and b.leaf_materialization_evidence is LME                                    # fuente unica de la logica, sin copias


def test_the_contract_and_the_collector_hold_no_classification_logic_of_their_own():
    pkg = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "modeling_assessment"
    for name in ("evidence_contract_v4.py", "materialization_evidence.py"):
        src = (pkg / name).read_text(encoding="utf-8")
        for dup in ("attribute_mappings", "Skip Mapping", "component_counts", "GOVERNED_BOUNDARY_CLASSES =", "def classify_", "def derive_"):
            assert dup not in src, (name, dup)
