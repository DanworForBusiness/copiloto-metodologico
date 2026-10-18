"""
judgment-rules/v4 — resolucion de nivel por ESTADO DE DIMENSION (politica transversal, B5.5).

Principio: «No determinable» legitimo = evidencia INSUFICIENTE (o una clase de contrato critica sin decidir). Con evidencia suficiente en los cuatro contratos el nivel sale de los hechos verificados.
v4 NO agrega razones ni fortalezas (salvo la debilidad LOCAL de T5 para tablas locales de origen fisico comprobado), NUNCA asigna BUENA ni PARCIAL por resolucion, y deja v2/v3 reproducibles.
"""
from __future__ import annotations

import copy
import glob
import os

import pytest

from tests._judgment_cases import FIXTURES, fresh
from tests._judgment_profiles import PB
from tests.test_methodological_judgment_level_resolution import _replica_model, states_of
from tests.test_methodological_judgment_materialized_input import phys_profile, rf_replica
from tests.test_methodological_judgment_rules import buena_model
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.rules import dimension_resolution_v4, resolve_level_v4

V2, V3, V4 = C.RULESET_V2, C.RULESET_V3, C.RULESET_V4
ALIASES = sorted(os.path.basename(f)[: -len(".json.gz")] for f in glob.glob(str(FIXTURES / "*.json.gz")))
S, M, D, NE, NA, UN = C.DS_STRENGTH, C.DS_MET, C.DS_DEFICIENCY, C.DS_NOT_EVALUABLE, C.DS_NOT_APPLICABLE, C.DS_UNRESOLVED


def v(p, ruleset):
    return evaluate_judgment(p, ruleset=ruleset, ratified_contracts=[]) if ruleset != V2 else evaluate_judgment(p)


def content(j):
    return (j["status"], j["level"], j["level_rule"], j["closure"], sorted(s["rule_id"] for s in j["strengths"]), sorted((r["rule_id"], r["classification"]) for r in j["reasons"]))


def st(*xs):
    return [{"dimension": f"D{i}", "state": x} for i, x in enumerate(xs)]


# ───────── la funcion de nivel (pura) ─────────
def test_the_level_function_needs_every_dimension_resolved_and_one_strength_and_never_returns_buena_or_parcial():
    assert resolve_level_v4(st(S, M, M, S)) == (C.ADECUADA, "LVL-08") and resolve_level_v4(st(S, S, S, S)) == (C.ADECUADA, "LVL-08")     # BUENA sigue exigiendo las 4 fortalezas del escalon 5 (no de la resolucion)
    assert resolve_level_v4(st(S, NA, M, S)) == (C.ADECUADA, "LVL-08") and resolve_level_v4(st(S, D, S, S)) == (C.ADECUADA, "LVL-08")     # una deficiencia LOCAL verificada no impide un nivel determinado
    for bad in (NE, UN):
        assert resolve_level_v4(st(S, S, S, bad)) is None and resolve_level_v4(st(S, M, D, bad)) is None                                  # evidencia insuficiente o arquitectura no clasificable: sigue ND
    assert resolve_level_v4(st(M, M, M, M)) is None and resolve_level_v4(st(D, D, D, D)) is None and resolve_level_v4(st(NA, NA, NA, NA)) is None   # sin ninguna fortaleza no hay ancla positiva
    assert all(resolve_level_v4(x)[0] != C.PARCIAL for x in (st(S, D, D, D), st(S, S, D, D)))                                             # PARCIAL exige desviacion MATERIAL, nunca por conteo de deficiencias


# ───────── versionado: v2 y v3 reproducibles, v4 explicito ─────────
def test_v2_stays_the_engine_default_and_v3_never_emits_dimension_states_or_resolves_lvl06():
    for p in (rf_replica(), phys_profile(), _replica_model()):
        a, b = evaluate_judgment(p), evaluate_judgment(copy.deepcopy(p), ruleset=V2)
        assert a["output_signature"] == b["output_signature"] and a["ruleset_version"] == V2 and "dimension_states" not in a and "decision_inputs" not in a
        j3 = v(p, V3)
        assert j3["ruleset_version"] == V3 and "dimension_states" not in j3 and (j3["status"], j3["level_rule"]) == (a["status"], a["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06")


def test_v4_is_selected_explicitly_rejects_experimental_variants_and_current_is_v4():
    assert C.CURRENT_RULESET_VERSION == V4 and C.RULESET_VERSION == V2 and V4 in C.RULESETS
    with pytest.raises(ValueError, match="variantes"):
        evaluate_judgment(rf_replica(), ruleset=V4, variants=["LEVEL_RESOLUTION"])
    j = v(rf_replica(), V4)
    assert j["ruleset_version"] == V4 and "ruleset_options" not in j and j["decision_inputs"]["ratified_contracts"] == []


@pytest.mark.parametrize("alias", ALIASES)
def test_v4_equals_v3_on_every_determined_golden_fixture_in_level_closure_strengths_and_reasons(alias):
    p = fresh(alias)
    a, b = v(p, V3), v(p, V4)
    assert content(a) == content(b), alias                                          # nada cambia fuera de los LVL-06 (y T5 solo puede agrandar los artefactos de una SRC-LOC-01 ya existente)


def test_v4_equals_v3_on_the_clean_controls_and_on_every_non_lvl06_synthetic_model():
    for p in (buena_model().build(), buena_model(assoc=False).build(), buena_model(authored=True).build()):
        assert content(v(p, V3)) == content(v(p, V4))


# ───────── hechos verificados que determinan el nivel ─────────
def test_a_technically_accredited_replica_without_integration_is_adecuada_by_resolution_and_adds_no_strength_or_reason():
    p = rf_replica()
    a, b = v(p, V3), v(p, V4)
    assert (a["status"], a["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06")
    assert (b["status"], b["level"], b["level_rule"], b["closure"]) == ("DETERMINED", "ADECUADA", "LVL-08", "PROVISIONAL") and "not_determinable_detail" not in b
    assert states_of(b) == {"SOURCE_INGESTION": S, "HARMONIZATION": NA, "PROPAGATION": M, "PUBLISHED_CONTRACT": S}
    assert b["strengths"] == a["strengths"] and b["reasons"] == a["reasons"]                                                       # preserva fortalezas y debilidades originales
    assert [w["change_key"] for w in b["what_would_change_the_judgment"]][0] == "ESTABLISH_AFFIRMATIVE_STRENGTH_IN_EVERY_DIMENSION_FOR_BUENA"


def test_responsibility_met_needs_positive_evidence_never_the_mere_absence_of_weaknesses():
    for tag, p in (("sin evidencia de materializacion", _replica_model()), ("sin procedencia comprobada", _replica_model(state=None)), ("integra entradas con un join", _replica_model(join=True))):
        b = v(p, V4)
        assert (b["status"], b["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06") and states_of(b)["PROPAGATION"] == UN, tag


def test_a_verified_local_deficiency_plus_positive_evidence_elsewhere_determines_adecuada_instead_of_nd():
    p = phys_profile()                                                                                  # tabla local de origen fisico comprobado (T5) = debilidad LOCAL de la ingesta
    a, b = v(p, V3), v(p, V4)
    assert (a["status"], a["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06")
    assert (b["status"], b["level"], b["level_rule"]) == ("DETERMINED", "ADECUADA", "LVL-08") and states_of(b)["SOURCE_INGESTION"] == D
    assert ("SRC-LOC-01", "LOCAL_WEAKNESS") in {(r["rule_id"], r["classification"]) for r in b["reasons"]}                # T5 deja la debilidad LOCAL registrada (no la oculta)
    assert not [r for r in b["reasons"] if r["classification"] in ("MATERIAL_DEVIATION", "RELEVANT_WEAKNESS")]               # y la tabla fisica cruda NO se convierte en MATERIAL


def test_v4_never_invents_strengths_and_only_t5_may_add_a_local_weakness():
    for p in (rf_replica(), phys_profile(), _replica_model(), buena_model().build()):
        a, b = v(p, V3), v(p, V4)
        assert {s["rule_id"] for s in b["strengths"]} == {s["rule_id"] for s in a["strengths"]}
        extra = {(r["rule_id"], r["classification"]) for r in b["reasons"]} - {(r["rule_id"], r["classification"]) for r in a["reasons"]}
        assert extra <= {("SRC-LOC-01", "LOCAL_WEAKNESS")}


# ───────── evidencia insuficiente: sigue ND, ahora con causa precisa ─────────
def unknown_candidate_model(consumed=True) -> dict:
    b = buena_model().cand("H1", character="UNKNOWN", sources=["CDS1"])
    return b.build() if consumed else buena_model().cand("H2", character="UNKNOWN").build()


def test_a_consumed_candidate_of_unknown_character_is_insufficient_evidence_not_a_non_legitimate_boundary():
    p = unknown_candidate_model(True)
    a, b = v(p, V3), v(p, V4)
    assert (a["status"], a["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06") == (b["status"], b["level_rule"])
    assert {d["dimension"]: d["cause"] for d in a["not_determinable_detail"]["missing_strength_dimensions"]}["HARMONIZATION"] == "HARMONIZATION_NOT_A_LEGITIMATE_REUSABLE_BOUNDARY"
    det = {d["dimension"]: d for d in b["not_determinable_detail"]["missing_strength_dimensions"]}
    assert (det["HARMONIZATION"]["state"], det["HARMONIZATION"]["cause"]) == ("INSUFFICIENT_EVIDENCE", "HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE")
    assert b["not_determinable_detail"]["evidence_sufficient"] is False and states_of(b)["HARMONIZATION"] == NE


def test_an_unconsumed_candidate_with_missing_evidence_does_not_block_the_evaluation_of_the_product():
    b = v(unknown_candidate_model(False), V4)
    assert states_of(b)["HARMONIZATION"] != NE


# ───────── composicion normal (propagacion) y hechos adversos verificados ─────────
def _gov_with_dimension_harmonization() -> dict:
    b = PB().view("V1", ["H1", "CDS1"], joins=1).cand("H1", dimension=True, sources=["CDS2"]).remote("CDS1").remote("CDS2").product("V1").measure("M").assoc("D")
    return b.trace_all("CDS1", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False).build()


def test_a_governed_boundary_leaf_composed_with_a_harmonized_dimension_is_normal_composition_responsibility_met_not_a_strength():
    p = _gov_with_dimension_harmonization()
    a, b = v(p, V3), v(p, V4)
    assert (a["status"], a["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06")                           # PRO-STR-01 exige que no haya hoja de frontera directa: v3 no lo reconoce
    assert (b["status"], b["level"], b["level_rule"]) == ("DETERMINED", "ADECUADA", "LVL-08") and states_of(b)["PROPAGATION"] == M
    assert "PRO-STR-01" not in {x["rule_id"] for x in b["strengths"]} and b["strengths"] == a["strengths"]            # cumplida != fortaleza


def test_integrating_several_leaves_without_any_harmonization_is_reintegration_in_the_product_not_normal_composition():
    bad = PB().view("V1", ["CDS1", "CDS2"], joins=1, derived=1).remote("CDS1").remote("CDS2").product("V1").measure("M").assoc("D")
    b = v(bad.trace_all("CDS1", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False).build(), V4)
    assert (b["status"], b["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06") and states_of(b)["PROPAGATION"] == UN and states_of(b)["HARMONIZATION"] == UN


def test_a_harmonization_boundary_that_projects_a_verified_physical_raw_source_is_a_demonstrated_deficiency_not_a_gap():
    raw = PB().view("V1", ["H1"]).view("H1", ["RAW1"]).cand("H1", sources=["RAW1"]).remote("RAW1", raw=True).product("V1").measure("M1").assoc("D").flow("RF", delta=True)
    b = v(raw.trace_all("RAW1", character="IDENTITY_OR_RENAME", through=True, label="EQUALS_LEAF_LABEL", via=["H1"]).build(), V4)
    st_ = states_of(b)
    assert st_["HARMONIZATION"] == D and st_["SOURCE_INGESTION"] == D and b["status"] == "NOT_DETERMINABLE"           # ND por la dimension PUBLICADA sin resolver, no por «armonizacion desconocida»


def test_published_vocabulary_leak_counts_only_for_a_verified_physical_raw_leaf_never_for_an_undetermined_or_governed_one():
    from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
    from tools.catalog.methodological_judgment.rules import published_vocabulary_leak_from_physical_raw as leak_raw
    raw = PB().view("V1", ["CDS1", "RAW1"]).remote("CDS1").remote("RAW1", raw=True).product("V1").measure("M").assoc("D").trace("M", "RAW1", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    ext = PB().view("V1", ["EXT"]).remote("EXT", external=True).product("V1").measure("M").assoc("D").trace("M", "EXT", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    gov = PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").measure("M").assoc("D").trace("M", "CDS1", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    assert leak_raw(ProfileEvidence(raw.build()), "UNDETERMINED") == ["M"]
    assert leak_raw(ProfileEvidence(ext.build()), "UNDETERMINED") == [] and leak_raw(ProfileEvidence(gov.build()), "UNDETERMINED") == []


# ───────── no promover lo que la evidencia no sostiene ─────────
def test_resolution_never_promotes_over_a_critical_source_class_a_material_reason_or_a_not_evaluable_contract():
    gp = fresh("biogenesis_sellout_gp")
    a, b = v(gp, V3), v(gp, V4)
    assert (a["level_rule"], b["level_rule"]) == ("LVL-05", "LVL-05") and b["status"] == "NOT_DETERMINABLE"                  # sin ratificacion la clase critica sigue decidiendo
    for alias in ALIASES:
        j = v(fresh(alias), V4)
        if j["level"] == "PARCIAL":
            assert j["level_rule"] == "LVL-02"                                                              # PARCIAL solo por una razon MATERIAL del escalon 1


def test_golden_ratification_still_works_under_v4_and_resolves_only_lvl05():
    from tests.test_methodological_judgment_governed_ratification import CRITICAL, ratif, with_identities
    p = with_identities(fresh("biogenesis_sellout_gp"))
    items = [ratif(p, leaf) for leaf in CRITICAL]
    j = evaluate_judgment(p, ruleset=V4, ratified_contracts=items)
    assert (j["ruleset_version"], j["status"], j["level"], j["level_rule"]) == (V4, "DETERMINED", "ADECUADA", "LVL-03")
    assert [i["event_id"] for i in j["decision_inputs"]["ratified_contracts"]] == [i["event_id"] for i in items]
    assert {s["rule_id"] for s in j["strengths"]} == {s["rule_id"] for s in v(p, V3)["strengths"]}
