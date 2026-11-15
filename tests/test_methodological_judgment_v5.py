"""judgment-rules/v5 — golden contra las etiquetas humanas CONGELADAS (sin tocarlas), compuerta de naming (BUENA->ADECUADA), PRO-MAT-02 (M1-M5), exactitud LVL-05/SOURCE (R4)
y aislamiento de v2/v3/v4. Sin datos de clientes: fixtures seudonimizados + constructor sintetico."""
from __future__ import annotations

import copy

import pytest

from tests._judgment_cases import FROZEN_LABELS, fresh
from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import RULESET_V4, RULESET_V5, evaluate_judgment
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog.methodological_judgment.rules import naming_gate, rule_pro_mat_02

V5 = dict(ruleset=RULESET_V5, ratified_contracts=[])
V4 = dict(ruleset=RULESET_V4, ratified_contracts=[])
_RANK = {"PARCIAL": 0, "ADECUADA": 1, "BUENA": 2}


# ───────────── golden v5: 8/8 contra las etiquetas humanas congeladas ─────────────
@pytest.mark.parametrize("alias", sorted(FROZEN_LABELS))
def test_v5_reproduces_every_frozen_human_label(alias):
    j = evaluate_judgment(fresh(alias), **V5)
    assert (j["status"], j["level"]) == ("DETERMINED", FROZEN_LABELS[alias])
    assert j["plena_assignment"] == "DISABLED_UNTIL_CALIBRATED" and j["ruleset_version"] == RULESET_V5


def test_sellout_gp_is_parcial_by_pro_mat_02_independently_of_the_external_source_class():
    """El PARCIAL humano de SELLOUT_GP viene de A004/A307 (frontera cruda compensada con filtrado e integracion que produce medidas publicadas), NO de la clase SQL: es el mismo
    con cualquier politica de sensibilidad sobre las fuentes externas."""
    p = fresh("biogenesis_sellout_gp")
    for pol in ("UNDETERMINED", "PHYSICAL_RAW", "GOVERNED"):
        j = evaluate_judgment(copy.deepcopy(p), external_policy=pol, **V5)
        assert (j["level"], j["level_rule"]) == ("PARCIAL", "LVL-02")
        assert {r["rule_id"] for r in j["reasons"] if r["classification"] == "MATERIAL_DEVIATION"} == {"PRO-MAT-02"}
    j = evaluate_judgment(p, **V5)
    acc = [r for r in j["material_compensation"] if r["state"] == C.MATERIAL_COMPENSATION_ACCREDITED]
    assert len(acc) == 2 and all(r["published_witnesses"] and r["predicate_witnesses"] and r["integration_nodes"] for r in acc)


def test_the_kensing_buena_survives_the_naming_gate_and_its_typed_am_is_recoverable_only_by_its_type():
    j = evaluate_judgment(fresh("kensing_gl_line_item_raw"), **V5)
    g = j["naming_gate"]
    assert (j["level"], j["level_rule"], g["state"]) == ("BUENA", "LVL-04", "PASS")
    assert g["recoverable_by_type"] == 1 and g["non_recoverable"] == 0 and g["ratio_nr"] == 0
    # los UNKNOWN conformes NO entran en ninguna formula (no se acredita merito sin responsabilidad)
    assert g["unknown"] == 15 and g["unknown_non_recoverable"] == 0 and g["ratio_nr_max"] == 0


# ───────────── v2/v3/v4 intactos ─────────────
@pytest.mark.parametrize("alias", sorted(FROZEN_LABELS))
def test_v4_output_has_no_v5_sections_and_is_unchanged_by_the_v5_code(alias):
    j = evaluate_judgment(fresh(alias), **V4)
    assert "naming_gate" not in j and "material_compensation" not in j and j["level_rule"] != "LVL-09"
    assert not any(r["rule_id"] == "PRO-MAT-02" for r in j["reasons"])


# ───────────── compuerta de naming ─────────────
def _kensing_with(*, pa_non_compliant=(), unknown_non_compliant=(), typed_digit="4"):
    p = fresh("kensing_gl_line_item_raw")
    pops = p["canonical_evidence"]["naming_evidence"]["hygiene"]["artifact_name_populations"]["by_identity"]
    pa = next(x for x in pops if x["identity"] == "PROJECT_ASSIGNED")
    un = next(x for x in pops if x["identity"] == "UNKNOWN")
    for a in pa_non_compliant:
        pa["compliant_artifacts"].remove(a)
        pa["non_compliant_artifacts"].append(a)
        pa["compliant"] -= 1
        pa["non_compliant"] += 1
    for a in unknown_non_compliant:
        un["compliant_artifacts"].remove(a)
        un.setdefault("non_compliant_artifacts", []).append(a)
        un["compliant"] -= 1
        un["non_compliant"] = un.get("non_compliant", 0) + 1
    if typed_digit != "4":
        for r in pa["violation_reason_histogram"]:
            r["key"] = r["key"].replace("'4'", f"'{typed_digit}'")
    for v in p["canonical_evidence"]["view_complexity"]["views"]:                 # sin rol declarado: las nuevas no conformes NO son recuperables
        if v["artifact"] in (*pa_non_compliant, *unknown_non_compliant):
            v["inferred_layer_basis"] = "NAME_PREFIX"
    return p


def test_block_limits_an_accredited_buena_to_adecuada_lvl09_and_explains_the_way_back():
    pa = fresh("kensing_gl_line_item_raw")["canonical_evidence"]["naming_evidence"]["hygiene"]["artifact_name_populations"]["by_identity"][0]["compliant_artifacts"][:3]
    j = evaluate_judgment(_kensing_with(pa_non_compliant=pa), **V5)
    assert (j["status"], j["level"], j["level_rule"], j["naming_gate"]["state"]) == ("DETERMINED", "ADECUADA", "LVL-09", "BLOCK")
    assert j["level_without_naming_gate"] == "BUENA" and j["closure"] == "PROVISIONAL"
    assert j["what_would_change_the_judgment"][0]["change_key"] == "RESOLVE_NON_RECOVERABLE_NAMING_DEVIATIONS_OF_GOVERNED_ARTIFACTS"


def test_not_accredited_limits_buena_without_claiming_a_deviation_when_the_outcome_depends_on_unknown_responsibility():
    un = [x for x in fresh("kensing_gl_line_item_raw")["canonical_evidence"]["naming_evidence"]["hygiene"]["artifact_name_populations"]["by_identity"]
          if x["identity"] == "UNKNOWN"][0]["compliant_artifacts"][:4]
    j = evaluate_judgment(_kensing_with(unknown_non_compliant=un), **V5)
    g = j["naming_gate"]
    assert g["ratio_nr"] <= C.NAMING_GATE_MAX_NONRECOVERABLE_RATIO < g["ratio_nr_max"]
    assert (j["level"], j["level_rule"], g["state"]) == ("ADECUADA", "LVL-09", "NOT_ACCREDITED")
    assert any(r["review_id"] == "NAMING_RESPONSIBILITY_UNDETERMINED" for r in j["review_candidates"])


def test_a_numbered_prefix_is_not_homologated_indiscriminately():
    """Solo el AM terminal (tipo declarado en el alcance) con el digito canonico '4' es recuperable por tipo; otro digito queda no recuperable."""
    g = naming_gate(ProfileEvidence(_kensing_with(typed_digit="2")))
    assert g["recoverable_by_type"] == 0 and g["non_recoverable"] == 1


@pytest.mark.parametrize("alias", sorted(FROZEN_LABELS))
def test_the_gate_never_raises_a_level_and_never_touches_parcial_or_not_determinable(alias):
    p = fresh(alias)
    j4, j5 = evaluate_judgment(copy.deepcopy(p), **V4), evaluate_judgment(copy.deepcopy(p), **V5)
    if j4["level"] in ("PARCIAL",) or j4["status"] == "NOT_DETERMINABLE":
        assert j5["level_rule"] != "LVL-09"
    if j4["level"] and j5["level"]:
        no_material = not any(r["rule_id"] == "PRO-MAT-02" for r in j5["reasons"])
        if no_material:
            assert _RANK[j5["level"]] <= _RANK[j4["level"]]


def test_missing_naming_evidence_never_accredits_buena():
    p = fresh("kensing_gl_line_item_raw")
    p["canonical_evidence"]["naming_evidence"]["hygiene"]["artifact_name_populations"]["by_identity"] = []
    g = naming_gate(ProfileEvidence(p))
    assert g["state"] == "NOT_ACCREDITED" and g["basis"] == "NAMING_EVIDENCE_ABSENT"


# ───────────── PRO-MAT-02 (M1-M5) ─────────────
def _gp():
    return fresh("biogenesis_sellout_gp")


def test_without_record_selection_predicates_the_compensation_is_not_observed_m5a():
    p = _gp()
    occ = p["canonical_evidence"]["expression_occurrences"]["occurrences"]
    p["canonical_evidence"]["expression_occurrences"]["occurrences"] = [o for o in occ if o.get("clause") != "WHERE"]
    j = evaluate_judgment(p, **V5)
    assert not any(r["rule_id"] == "PRO-MAT-02" for r in j["reasons"])
    assert {r["failed_condition"] for r in j["material_compensation"] if r["leaf"] in ("ART_0007", "ART_0008")} == {"M5a"}


def test_without_expression_evidence_the_detector_is_fail_closed_and_says_so():
    p = _gp()
    p["canonical_evidence"]["expression_occurrences"]["occurrences"] = []
    j = evaluate_judgment(p, **V5)
    assert not any(r["rule_id"] == "PRO-MAT-02" for r in j["reasons"])
    assert {r["state"] for r in j["material_compensation"] if r["leaf"] in ("ART_0007", "ART_0008")} == {C.MATERIAL_COMPENSATION_NOT_EVALUABLE}
    lim = [l for l in j["evidence_limitations"] if l["key"] == "material_compensation_not_evaluable"]
    assert lim and lim[0]["can_change_level"] is True


def test_a_single_source_normalization_node_resolves_m2_even_with_filters():
    """Vista de UNA sola fuente con logica (filtros / uniones / derivaciones) antes de integrar = frontera legitima por responsabilidad, aunque no sea candidato declarado."""
    b = (PB().remote("RAW", raw=True).remote("CDS1").view("N1", ["RAW"], where=1, derived=1).view("P", ["N1", "CDS1"], joins=1, derived=2, where=1)
         .product("P").measure("M").trace("M", "RAW", character="WITH_DERIVATION", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False, via=("N1", "P")))
    _, recs = rule_pro_mat_02(ProfileEvidence(b.build()), "UNDETERMINED")
    assert recs and (recs[0]["state"], recs[0]["failed_condition"], recs[0]["resolved_at"]) == (C.MATERIAL_COMPENSATION_NOT_OBSERVED, "M2", "N1")
    # contraste: la MISMA cadena con un nodo de paso (sin logica) no resuelve la responsabilidad antes de integrar
    b2 = (PB().remote("RAW", raw=True).remote("CDS1").view("N1", ["RAW"]).view("P", ["N1", "CDS1"], joins=1, derived=2, where=1)
          .product("P").measure("M").trace("M", "RAW", character="WITH_DERIVATION", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False, via=("N1", "P")))
    _, recs2 = rule_pro_mat_02(ProfileEvidence(b2.build()), "UNDETERMINED")
    assert recs2[0]["conditions"]["M2_unresolved"] is True


def test_external_undetermined_sources_are_never_m1_whatever_the_sensitivity_policy():
    b = (PB().remote("EXT", external=True).remote("CDS1").view("P", ["EXT", "CDS1"], joins=1, derived=2, where=1).product("P").measure("M")
         .trace("M", "EXT", character="WITH_DERIVATION", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False, via=("P",)))
    for pol in ("UNDETERMINED", "PHYSICAL_RAW", "GOVERNED"):
        reasons, recs = rule_pro_mat_02(ProfileEvidence(b.build()), pol)
        assert reasons == [] and recs == []


def test_a_leaf_already_covered_by_pro_mat_01_is_not_counted_twice():
    ev = ProfileEvidence(_gp())
    reasons, recs = rule_pro_mat_02(ev, "UNDETERMINED", frozenset({"ART_0007", "ART_0008"}))
    assert reasons == [] and {r.get("covered_by") for r in recs if r["state"] == C.MATERIAL_COMPENSATION_ACCREDITED} == {"PRO-MAT-01"}


# ───────────── R4: exactitud LVL-05/SOURCE + caso de referencia LVL-05 sintetico ─────────────
def _und_model(*, exposure="verbatim") -> PB:
    b = PB().view("V1", ["EXT"], derived=1).remote("EXT", external=True).remote("CDS1").product("V1").measure("M")
    if exposure == "verbatim":
        b.trace("M", "EXT", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    else:
        b.trace("M", "EXT", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False)
    return b


def test_the_synthetic_lvl05_reference_case_stays_lvl05_in_v5():
    j = evaluate_judgment(_und_model().build(), **V5)
    assert (j["status"], j["level_rule"], j["not_determinable_reason"]) == ("NOT_DETERMINABLE", "LVL-05", "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL")


def test_v5_declares_that_an_undetermined_class_can_change_the_level_when_it_blocks_the_source_dimension():
    j4 = evaluate_judgment(_und_model(exposure="authored").build(), **V4)
    j5 = evaluate_judgment(_und_model(exposure="authored").build(), **V5)
    lim = lambda j: next(l for l in j["evidence_limitations"] if l["key"] == "external_source_contract_undetermined")   # noqa: E731
    assert lim(j4)["can_change_level"] is False and lim(j5)["can_change_level"] is True


def test_v5_opens_the_source_contract_review_for_a_mixed_boundary_with_a_raw_component():
    b = PB().view("V1", ["MIX"], derived=1).remote("MIX").leaf_class("MIX", "MIXED_BOUNDARY_CLASSES").product("V1").measure("M")
    p = b.build()
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf"] == "MIX":
            r["boundary_classes"] = ["PHYSICAL_ABAP_TABLE_BOUNDARY", "GOVERNED_SAP_CDS_CONTRACT"]
    j4, j5 = evaluate_judgment(copy.deepcopy(p), **V4), evaluate_judgment(copy.deepcopy(p), **V5)
    assert not any(r["rule_id"] == "SRC-REV-01" for r in j4["review_candidates"])
    rev = [r for r in j5["review_candidates"] if r["rule_id"] == "SRC-REV-01"]
    assert rev and rev[0]["mixed_boundaries"][0]["leaf"] == "MIX"
