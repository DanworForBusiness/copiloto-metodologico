"""Explicabilidad de «NO DETERMINABLE» (``explain_determination``, nd-explanation/v1): PROYECCION determinista de un Juicio persistido sobre su perfil.

Contrato verificado:
  * un Juicio determinado jamas tiene impedimentos (ni uno ratificado por gobierno);
  * cada impedimento coincide con la logica que produjo el veredicto (el predicado del motor se vuelve a evaluar sobre el perfil: «corroborado»);
  * se distingue evidencia no adquirida / no interpretable / contrato de origen sin determinar / responsabilidad sin demostrar / decision de gobierno;
  * si el Juicio no registro una causa verificable, se declara (``diagnosis_limits``): jamas se inventa precision;
  * no altera el Juicio (no agrega campos ni cambia su firma), es determinista, serializable y no hace E/S."""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path

import pytest

from tests._judgment_cases import FIXTURES as LEGACY_FIXTURES, fresh as legacy_fresh
from tests._judgment_profiles import PB
from tests._nd_cases import ALIASES, FIXTURES, MANIFEST, fresh, ratification
from tests.test_methodological_judgment_rules import buena_model
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment import explain as X
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog.methodological_judgment.rules import decision_critical_undetermined_source

V4 = C.RULESET_V4
NE, UN = C.DS_NOT_EVALUABLE, C.DS_UNRESOLVED
KEYS = {"blocker_id", "dimension", "state", "rule_id", "condition_id", "reason_code", "category", "artifacts", "evidence_refs", "facts", "required_evidence", "resolution_type",
        "resolution_hint_key", "confidence", "corroborated"}


def judge(p, **kw):
    return evaluate_judgment(p, ruleset=V4, ratified_contracts=kw.pop("ratified_contracts", []), **kw)


def explain(p, **kw):
    j = judge(p, **kw)
    return j, X.explain_determination(j, p)


def by_id(e):
    return {b["blocker_id"]: b for b in e["blockers"]}


def gov_model(**cand_kw):
    b = PB().view("V1", ["H1", "CDS1"], joins=1).cand("H1", dimension=True, sources=["CDS2"], **cand_kw).remote("CDS1").remote("CDS2").product("V1").measure("M").assoc("D")
    return b.trace_all("CDS1", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False)


# ───────────────────────────── nada que explicar ─────────────────────────────
def test_a_determined_judgment_or_no_judgment_has_no_blockers():
    assert X.explain_determination(None, {}) is None and X.explain_determination({}, {}) is None
    j, e = explain(buena_model().build())
    assert j["status"] == "DETERMINED" and e is None


@pytest.mark.parametrize("alias", sorted(p.name[: -len(".json.gz")] for p in LEGACY_FIXTURES.glob("*.json.gz")))
def test_no_golden_determined_model_receives_a_fictitious_blocker_and_every_nd_does_have_one(alias):
    p = legacy_fresh(alias)
    j, e = explain(p)
    assert (e is None) == (j["status"] == "DETERMINED"), alias
    if e is not None:
        assert e["blockers"] and all(b["category"] in X.CATEGORIES for b in e["blockers"])


def test_a_judgment_determined_by_a_governance_ratification_has_no_blockers_while_without_it_the_same_profile_is_explained():
    p = fresh("nd_lvl05_resolvable_by_ratification")
    j0, e0 = explain(p)
    assert j0["level_rule"] == "LVL-05" and e0 is not None and by_id(e0)["SOURCE_INGESTION:EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"]["resolution_type"] == X.GOVERNANCE_DECISION
    critical = sorted(decision_critical_undetermined_source(ProfileEvidence(p), "UNDETERMINED"))
    j1, e1 = explain(p, ratified_contracts=[ratification(p, leaf) for leaf in critical])
    assert j1["status"] == "DETERMINED" and e1 is None                                  # decision simulada (NO real): el Juicio queda determinado y la explicacion desaparece


# ───────────────────────────── causas distintas, explicaciones distintas ─────────────────────────────
def test_nd_by_insufficient_evidence_a_consumed_harmonization_candidate_whose_character_cannot_be_determined():
    p = buena_model().cand("H1", character="UNKNOWN", sources=["CDS1"]).build()
    j, e = explain(p)
    b = by_id(e)["HARMONIZATION:HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE"]
    assert (j["level_rule"], b["state"], b["category"], b["resolution_type"], b["artifacts"], b["rule_id"]) == ("LVL-06", NE, X.EVIDENCE_NOT_INTERPRETABLE, X.TECHNICAL_EVIDENCE, ["H1"], "LVL-06")
    assert b["corroborated"] is True and b["required_evidence"] == ["FIELD_LABEL_ORIGIN_OF_THE_CANDIDATE"] and e["corroboration"]["status"] == "CORROBORATED"
    assert {r["dimension"] for r in e["resolved_dimensions"]} == {"SOURCE_INGESTION", "PROPAGATION", "PUBLISHED_CONTRACT"}      # lo que SI tiene veredicto no se marca como bloqueado
    assert e["blocked_dimensions"] == ["HARMONIZATION"] and e["categories"] == [X.EVIDENCE_NOT_INTERPRETABLE]


def test_nd_by_unknown_source_contract_decision_critical_lvl05_is_a_governance_decision_with_the_exposed_fields():
    p = PB().view("V1", ["EXT"]).remote("EXT", external=True).product("V1").measure("M").assoc("D").trace("M", "EXT", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL").build()
    j, e = explain(p)
    b = by_id(e)["SOURCE_INGESTION:EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"]
    assert (j["level_rule"], b["category"], b["resolution_type"], b["artifacts"], b["rule_id"], b["state"]) == ("LVL-05", X.SOURCE_CONTRACT_UNDETERMINED, X.GOVERNANCE_DECISION, ["EXT"], "LVL-05", NE)
    assert b["facts"]["leaves"][0]["exposed_published_fields"] == ["M"] and b["corroborated"] is True and b["confidence"] == X.HIGH


def test_a_non_critical_undetermined_source_class_is_not_presented_as_a_governance_decision_because_a_ratification_would_have_no_effect():
    p = PB().view("V1", ["EXT"]).remote("EXT", external=True).product("V1").measure("M").assoc("D").trace("M", "EXT", character="WITH_DERIVATION", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False).build()
    j, e = explain(p)
    b = by_id(e)["SOURCE_INGESTION:SOURCE_CONTRACT_CLASS_UNDETERMINED"]
    assert j["level_rule"] == "LVL-06" and b["category"] == X.SOURCE_CONTRACT_UNDETERMINED
    assert b["facts"]["ratification_would_apply"] is False and b["resolution_type"] == X.METHODOLOGICAL_REVIEW and b["resolution_hint_key"] == "SOURCE_CLASS_RATIFICATION_HAS_NO_EFFECT_OUTSIDE_LVL05"


def test_nd_by_incomplete_lineage_lists_the_unresolved_published_fields():
    j, e = explain(gov_model().incomplete("M").build())
    b = by_id(e)["PUBLISHED_CONTRACT:PUBLISHED_LINEAGE_INCOMPLETE"]
    assert j["level_rule"] == "LVL-06" and b["category"] == X.EVIDENCE_NOT_INTERPRETABLE and b["facts"]["incomplete_fields"] == ["M"] and b["evidence_refs"] == ["ce:published_field_lineage"]
    assert "lineage_complete" in b["facts"]["unmet_conditions"] and b["corroborated"] is True


def test_nd_by_an_unevaluated_area_lvl01_names_the_area_and_is_evidence_not_acquired():
    j, e = explain(buena_model().area("HARMONIZATION", "NOT_ASSESSED").area("PROPAGATION", "NOT_ASSESSED").build())
    assert j["level_rule"] == "LVL-01" and sorted(by_id(e)) == ["HARMONIZATION:CONTRACT_NOT_EVALUABLE", "PROPAGATION:CONTRACT_NOT_EVALUABLE"]
    b = by_id(e)["HARMONIZATION:CONTRACT_NOT_EVALUABLE"]
    assert b["category"] == X.EVIDENCE_NOT_ACQUIRED and b["facts"]["areas"] == {"HARMONIZATION": "NOT_ASSESSED"} and e["corroboration"]["status"] == "CORROBORATED"


def test_an_assessment_that_cannot_be_judged_is_a_precondition_not_a_methodological_blocker():
    bad = buena_model().build()
    bad["assessment_state"] = "NOT_ASSESSED"
    j, e = explain(bad)
    (b,) = e["blockers"]
    assert j["not_determinable_reason"] == "ASSESSMENT_NOT_JUDGEABLE" and (b["dimension"], b["category"]) == (None, X.ASSESSMENT_PRECONDITION)
    old = buena_model().build()
    old["canonical_evidence"]["version"] = "canonical-evidence/v2"
    j2 = evaluate_judgment(old, ruleset=V4, ratified_contracts=[])
    if j2.get("not_determinable_reason") == "CANONICAL_EVIDENCE_V3_REQUIRED":
        assert X.explain_determination(j2, old)["blockers"][0]["condition_id"] == "CANONICAL_EVIDENCE_V3_REQUIRED"


def test_unresolved_dimensions_are_responsibilities_not_demonstrated_not_missing_evidence():
    p = PB().view("V1", ["CDS1"]).remote("CDS1").measure("M").assoc("D").trace_all("CDS1").build()                 # sin armonizacion ni nodo de producto
    j, e = explain(p)
    b = by_id(e)
    assert b["PROPAGATION:NO_PRODUCT_NODE_OBSERVED"]["category"] == X.EVIDENCE_NOT_INTERPRETABLE
    assert b["PUBLISHED_CONTRACT:NO_AFFIRMATIVE_PUBLISHED_FAMILY"]["category"] == X.ARCHITECTURAL_RESPONSIBILITY_UNPROVEN and b["PUBLISHED_CONTRACT:NO_AFFIRMATIVE_PUBLISHED_FAMILY"]["state"] == UN


def test_several_simultaneous_blockers_are_all_reported_with_their_own_category_and_nothing_else():
    m = gov_model().incomplete("M")
    m.remote("EXT", external=True)
    j, e = explain(m.build())
    assert sorted(by_id(e)) == ["PUBLISHED_CONTRACT:PUBLISHED_LINEAGE_INCOMPLETE", "SOURCE_INGESTION:SOURCE_CONTRACT_CLASS_UNDETERMINED"]
    assert e["categories"] == sorted({X.EVIDENCE_NOT_INTERPRETABLE, X.SOURCE_CONTRACT_UNDETERMINED}) and e["corroboration"]["status"] == "CORROBORATED"


# ───────────────────────────── cuando no hay una causa verificable, se dice ─────────────────────────────
def test_a_judgment_without_a_recorded_cause_is_declared_undetermined_never_invented():
    j = {"status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-06", "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE", "reasons": [], "strengths": []}
    e = X.explain_determination(j, buena_model().build())
    (b,) = e["blockers"]
    assert b["category"] == X.UNDETERMINED_CAUSE and b["confidence"] == X.LOW and b["artifacts"] == [] and b["evidence_refs"] == [] and b["facts"] == {}
    assert "NO_VERIFIABLE_CAUSE_PERSISTED" in {l["key"] for l in e["diagnosis_limits"]} and e["corroboration"]["status"] == "PARTIAL"


def test_a_cause_this_version_does_not_recognize_is_reported_as_such():
    j = {"status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-06", "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE",
         "dimension_states": [{"dimension": "HARMONIZATION", "state": NE, "cause": "SOME_FUTURE_CAUSE", "basis": [], "local_deficiencies": []}]}
    e = X.explain_determination(j, buena_model().build())
    (b,) = e["blockers"]
    assert (b["category"], b["confidence"], b["condition_id"]) == (X.UNDETERMINED_CAUSE, X.LOW, "SOME_FUTURE_CAUSE") and "CAUSE_NOT_RECOGNIZED" in {l["key"] for l in e["diagnosis_limits"]}


def test_an_unreadable_profile_keeps_only_what_the_judgment_recorded_and_says_so():
    j, e = explain(buena_model().cand("H1", character="UNKNOWN", sources=["CDS1"]).build())
    e2 = X.explain_determination(j, None)
    (b,) = e2["blockers"]
    assert b["artifacts"] == [] and b["evidence_refs"] == [] and b["confidence"] != X.HIGH and "PROFILE_NOT_READABLE" in {l["key"] for l in e2["diagnosis_limits"]}


def test_a_judgment_that_the_profile_does_not_corroborate_is_flagged_instead_of_trusted():
    j = judge(buena_model().cand("H1", character="UNKNOWN", sources=["CDS1"]).build())
    other = buena_model().build()                                                    # otro perfil: sin candidatos UNKNOWN
    e = X.explain_determination(j, other)
    assert e["corroboration"]["status"] == "NOT_CORROBORATED" and by_id(e)["HARMONIZATION:HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE"]["corroborated"] is False


def test_a_field_level_detail_that_is_not_persisted_is_declared_a_limit():
    j, e = explain(fresh("nd_lvl06_harmonization_label_unknown"))
    assert "UNKNOWN_LABEL_FIELDS_NOT_PERSISTED" in {l["key"] for l in e["diagnosis_limits"]}
    (c,) = by_id(e)["HARMONIZATION:HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE"]["facts"]["candidates"]
    assert c["unknown_label_field_count"] == 1 and c["derived_field_count"] == 1 and c["unknown_labels_equal_derived_fields"] is True and c["feeds_product"] is True


# ───────────────────────────── perfiles / Juicios historicos ─────────────────────────────
def test_a_historical_judgment_without_dimension_states_is_explained_from_its_detail_with_low_confidence():
    p = fresh("nd_lvl06_mapping_lineage_and_class_not_observable")
    j = judge(p)
    old = {k: v for k, v in j.items() if k != "dimension_states"}                    # un Juicio v3 persistido no trae ``dimension_states``
    e = X.explain_determination(old, p)
    assert "HISTORICAL_JUDGMENT_WITHOUT_DIMENSION_STATES" in {l["key"] for l in e["diagnosis_limits"]}
    assert {b["condition_id"] for b in e["blockers"]} == {d["cause"] for d in j["not_determinable_detail"]["missing_strength_dimensions"]}
    assert all(b["confidence"] == X.LOW for b in e["blockers"])


def test_judgments_of_every_ruleset_are_explainable_on_the_real_nd_profiles():
    for alias in ALIASES:
        p = fresh(alias)
        for ruleset in (C.RULESET_V2, C.RULESET_V3, V4):
            j = evaluate_judgment(p, ruleset=ruleset, ratified_contracts=[]) if ruleset != C.RULESET_V2 else evaluate_judgment(p)
            e = X.explain_determination(j, p)
            assert (e is None) == (j["status"] == "DETERMINED") and (e is None or e["blockers"])


# ───────────────────────────── los ND reales (golden) ─────────────────────────────
@pytest.mark.parametrize("alias", ALIASES)
def test_golden_real_nd_blockers_match_the_manifest_and_the_engine_logic(alias):
    exp = MANIFEST[alias]
    p = fresh(alias)
    j, e = explain(p)
    assert (j["level_rule"], j.get("not_determinable_reason")) == (exp["level_rule"], exp["not_determinable_reason"]) and j["status"] == "NOT_DETERMINABLE"
    got = [{"blocker_id": b["blocker_id"], "state": b["state"], "category": b["category"], "resolution_type": b["resolution_type"], "n_artifacts": len(b["artifacts"]), "confidence": b["confidence"],
            "corroborated": b["corroborated"]} for b in e["blockers"]]
    assert got == exp["blockers"] and sorted(r["dimension"] for r in e["resolved_dimensions"]) == exp["resolved_dimensions"]
    assert sorted(l["key"] for l in e["diagnosis_limits"]) == exp["limits"] and e["corroboration"]["status"] == exp["corroboration"]
    # coincide con la logica que produjo el veredicto: los impedimentos son EXACTAMENTE las dimensiones bloqueantes del Juicio (o la fuente critica de LVL-05)
    if j["level_rule"] == "LVL-06":
        blocking = sorted(s["dimension"] for s in j["dimension_states"] if s["state"] in (NE, UN))
        assert sorted(b["dimension"] for b in e["blockers"]) == blocking and C.DS_NOT_APPLICABLE not in {s["state"] for s in j["dimension_states"] if s["dimension"] in blocking}
        assert {b["condition_id"] for b in e["blockers"]} == {s["cause"] for s in j["dimension_states"] if s["state"] in (NE, UN)}
    else:
        assert [b["artifacts"] for b in e["blockers"]] == [sorted(decision_critical_undetermined_source(ProfileEvidence(p), "UNDETERMINED"))]


@pytest.mark.parametrize("alias", ALIASES)
def test_every_evidence_ref_and_artifact_cited_by_a_blocker_exists_in_the_profile(alias):
    p = fresh(alias)
    j, e = explain(p)
    ev = ProfileEvidence(p)
    known = ev.known_artifacts() | set(ev.leaf_class_records) | set(ev.harmonization) | set(ev.product_nodes) | set(ev.views)
    for b in e["blockers"]:
        assert all(ev.ref_exists(r) for r in b["evidence_refs"]), (alias, b["blocker_id"])
        assert set(b["artifacts"]) <= known | {ev.am}, (alias, b["blocker_id"], set(b["artifacts"]) - known)


# ───────────────────────────── contrato, determinismo y no alteracion ─────────────────────────────
@pytest.mark.parametrize("alias", ALIASES)
def test_the_contract_is_complete_closed_serializable_and_deterministic(alias):
    p = fresh(alias)
    j, e = explain(p)
    assert e["version"] == X.EXPLANATION_VERSION == "nd-explanation/v1" and e["source"] == "PROJECTION_OF_PERSISTED_JUDGMENT_AND_PROFILE"
    for b in e["blockers"]:
        assert set(b) == KEYS and b["state"] in (NE, UN) and b["category"] in X.CATEGORIES and b["resolution_type"] in X.RESOLUTION_TYPES and b["confidence"] in (X.HIGH, X.MEDIUM, X.LOW)
        assert b["required_evidence"] and b["blocker_id"] == f"{b['dimension'] or 'GLOBAL'}:{b['condition_id']}" and b["corroborated"] in (True, False, None)
    assert not ({b["dimension"] for b in e["blockers"]} & {r["dimension"] for r in e["resolved_dimensions"]})
    assert json.dumps(e, sort_keys=True) == json.dumps(X.explain_determination(copy.deepcopy(j), copy.deepcopy(p)), sort_keys=True)
    assert json.dumps(X.explain_determination(j, p), sort_keys=True) == json.dumps(e, sort_keys=True)               # idempotente: misma entrada, misma salida


@pytest.mark.parametrize("alias", ALIASES)
def test_the_explanation_never_alters_the_judgment_its_signature_or_the_profile(alias):
    p = fresh(alias)
    before_p = json.dumps(p, sort_keys=True)
    j = judge(p)
    before_j = json.dumps(j, sort_keys=True)
    X.explain_determination(j, p)
    assert json.dumps(j, sort_keys=True) == before_j and json.dumps(p, sort_keys=True) == before_p
    assert "determination_blockers" not in j and j["output_signature"] == judge(fresh(alias))["output_signature"]       # el contrato del Juicio NO se extiende: sin migracion


def test_the_explainer_is_a_pure_module_without_io_network_or_clock():
    tree = ast.parse(Path(X.__file__).read_text(encoding="utf-8"))
    mods = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else ("." if isinstance(n, ast.ImportFrom) else n.names[0].name.split(".")[0])
            for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert mods <= {"__future__", "typing", "."}, mods


def test_no_judgment_is_changed_to_a_level_by_explaining_it_every_real_nd_stays_nd_in_the_engine():
    for alias in ALIASES:
        assert judge(fresh(alias))["status"] == "NOT_DETERMINABLE"


# ───────────────────────────── fixtures seudonimizadas ─────────────────────────────
@pytest.mark.parametrize("alias", ALIASES)
def test_nd_fixtures_are_pseudonymized(alias):
    import re
    text = json.dumps(fresh(alias), ensure_ascii=False).lower()
    for forbidden in ("biogenesis", "kensing", "illolay", "pampa", "danone", "desvio", "osbc", "jmb", "resp_com", "hojobar", "sell_out", "sellout", "base_ventas", "lista_precios", "s4prem", "sql_server"):
        assert forbidden not in text, (alias, forbidden)
    assert not re.search(r"(?<![a-z0-9_])loma(?![a-z0-9_])", text) and (FIXTURES / "README.md").is_file()


# ───────────────────────────── corpus completo (opcional: requiere el pool local; no corre en CI) ─────────────────────────────
@pytest.mark.skipif(not os.environ.get("DANWOR_ND_CORPUS"), reason="DANWOR_ND_CORPUS (JSON {items:{key:{profile,judgment}}}) no definido")
def test_whole_corpus_every_nd_is_explained_and_no_determined_model_receives_a_blocker():
    items = json.loads(Path(os.environ["DANWOR_ND_CORPUS"]).read_text(encoding="utf-8"))["items"]
    nd = det = 0
    for key, it in items.items():
        e = X.explain_determination(it["judgment"], it["profile"])
        if it["judgment"]["status"] == "DETERMINED":
            det += 1
            assert e is None, key
        else:
            nd += 1
            assert e is not None and e["blockers"] and e["corroboration"]["status"] != "NOT_CORROBORATED", key
    assert nd and det
