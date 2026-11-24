"""judgment-rules/v7 — E3 (enriquecimiento legitimo) y E5 (suficiencia de la clave publicada) sobre `join-key-profile/v1`. Positivos y NEGATIVOS
(cardinalidad desconocida, claves transformadas, claves no cubiertas, agregaciones legitimas/insuficientes, UNION, conductora no gobernada, sin evidencia).
Perfiles y bloques SINTETICOS: sin datos de clientes."""
from __future__ import annotations

import copy

import pytest

from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment import structural as ST
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence

V6 = dict(ruleset=C.RULESET_V6, ratified_contracts=[])
V7 = dict(ruleset=C.RULESET_V7, ratified_contracts=[])


def _profile(*, dim_measure=False, driver_external=False):
    b = PB().view("VF", ["FACT", "DIM"], joins=1).remote("FACT", external=driver_external).remote("DIM").product("VF")
    b.field("K").field("ATTR").measure("Q")
    b.trace("K", "FACT", character="IDENTITY_ONLY").trace("Q", "FACT", character="IDENTITY_ONLY")
    b.trace("ATTR", "DIM", character="IDENTITY_ONLY")
    if dim_measure:
        b.measure("DQ").trace("DQ", "DIM", character="IDENTITY_ONLY")
    return b.build()


def _jkp(*, vf_keys=("K",), fact_keys=("K", "ITEM"), dim_keys=("DK",), eqs=None, join_type="left", group_by=None, union=False, project_item=False,
         rename_item=None, where_item=False, extra_proj=()):
    eqs = eqs if eqs is not None else [{"kind": "COLUMN", "secondary_column": "DK", "other": {"alias": "FACT", "column": "DK_REF"}}]
    proj = [{"output": "K", "artifact": "FACT", "column": "K"}, {"output": "Q", "artifact": "FACT", "column": "Q"}, {"output": "ATTR", "artifact": "DIM", "column": "ATTR"}, *extra_proj]
    if project_item or rename_item:
        proj.append({"output": rename_item or "ITEM", "artifact": "FACT", "column": "ITEM"})
    br = {"driver": {"artifact": "FACT", "alias": "FACT"}, "joins": [{"type": join_type, "secondary": "DIM", "alias": "DIM", "equalities": eqs}],
          "projected_refs": proj, "literal_columns": [], "group_by": group_by, "where_constants": [{"artifact": "FACT", "column": "ITEM"}] if where_item else [],
          "has_subselect_source": False}
    return {"join_key_profile": {"version": ST.JKP_VERSION,
                                 "keys": {"FACT": {"declared": list(fact_keys), "source": "CSN_ELEMENTS_KEY"}, "DIM": {"declared": list(dim_keys), "source": "CSN_ELEMENTS_KEY"},
                                          "VF": {"declared": list(vf_keys), "source": "CSN_ELEMENTS_KEY"}},
                                 "views": {"VF": {"artifact": "VF", "declared_keys": list(vf_keys), "key_source": "CSN_ELEMENTS_KEY", "union": union,
                                                  "branches": [br, copy.deepcopy(br)] if union else [br]}}}}


def _e(profile, block):
    ev = ProfileEvidence(profile, None)
    jkp = ST.valid_block(block["join_key_profile"])
    return ST.e3_enrichment(ev, jkp, "UNDETERMINED"), ST.e5_key_sufficiency(ev, jkp, "UNDETERMINED")


def _dims(j):
    return {d["dimension"]: d["state"] for d in j.get("dimension_states") or ()}


# ───────────── E3 ─────────────
def test_e3_demonstrated_n1_enrichment_resolves_harmonization_and_propagation_in_v7_only():
    p, s = _profile(), _jkp(vf_keys=())
    assert _e(p, s)[0]["state"] == ST.DEMONSTRATED
    j6, j7 = evaluate_judgment(copy.deepcopy(p), **V6), evaluate_judgment(copy.deepcopy(p), structural_evidence=s, **V7)
    assert _dims(j6)[C.HARMONIZATION] == C.DS_UNRESOLVED and _dims(j6)[C.PROPAGATION] == C.DS_UNRESOLVED
    assert _dims(j7)[C.HARMONIZATION] == C.DS_NOT_APPLICABLE and _dims(j7)[C.PROPAGATION] == C.DS_MET
    assert {s_["rule_id"] for s_ in j7["strengths"]} <= {s_["rule_id"] for s_ in j6["strengths"]}           # sin fortalezas nuevas


@pytest.mark.parametrize("kw,why", [
    (dict(eqs=[{"kind": "TRANSFORMED", "secondary_column": "DK", "functions": ["LTRIM"]}]), "clave transformada (LTRIM)"),
    (dict(dim_keys=()), "clave del secundario desconocida"),
    (dict(dim_keys=("DK", "SPRAS")), "clave del secundario no cubierta (N:1 no demostrado)"),
    (dict(eqs=[{"kind": "OTHER"}]), "predicado no resoluble"),
    (dict(join_type="inner"), "join no LEFT"),
    (dict(group_by=["K"]), "agregacion: granularidad alterada"),
    (dict(union=True), "UNION"),
])
def test_e3_negatives_stay_not_evaluable_and_change_nothing(kw, why):
    p, s = _profile(), _jkp(vf_keys=(), **kw)
    assert _e(p, s)[0]["state"] == ST.NOT_EVALUABLE, why
    assert _dims(evaluate_judgment(copy.deepcopy(p), structural_evidence=s, **V7)) == _dims(evaluate_judgment(copy.deepcopy(p), **V6))


def test_e3_constant_equality_completes_the_secondary_key():
    eqs = [{"kind": "COLUMN", "secondary_column": "DK", "other": {"alias": "FACT", "column": "DK_REF"}}, {"kind": "CONSTANT", "secondary_column": "SPRAS", "value": "S"}]
    assert _e(_profile(), _jkp(vf_keys=(), dim_keys=("DK", "SPRAS"), eqs=eqs))[0]["state"] == ST.DEMONSTRATED


def test_e3_a_dimension_that_contributes_a_measure_is_not_enrichment():
    assert _e(_profile(dim_measure=True), _jkp(vf_keys=()))[0]["state"] == ST.NOT_EVALUABLE


def test_e3_an_ungoverned_driver_is_not_evaluable():
    assert _e(_profile(driver_external=True), _jkp(vf_keys=()))[0]["state"] == ST.NOT_EVALUABLE


# ───────────── E5 ─────────────
def test_e5_declared_key_missing_a_grain_component_is_a_local_published_contract_deficiency():
    p, s = _profile(), _jkp(vf_keys=("K",))
    assert _e(p, s)[1]["state"] == ST.DEFICIENT and _e(p, s)[1]["nodes"][0]["missing"] == ["ITEM"]
    j7 = evaluate_judgment(copy.deepcopy(p), structural_evidence=s, **V7)
    loc = [r for r in j7["reasons"] if r["rule_id"] == "PUB-LOC-02"]
    assert loc and loc[0]["classification"] == C.LOCAL_WEAKNESS and "PUB-STR-01" not in {x["rule_id"] for x in j7["strengths"]}
    assert _dims(j7)[C.PUBLISHED_CONTRACT] == C.DS_DEFICIENCY


@pytest.mark.parametrize("kw", [
    dict(vf_keys=("K", "ITEM"), project_item=True),                    # clave completa
    dict(vf_keys=("K", "POS"), rename_item="POS"),                     # renombre: NO se exige igualdad literal
    dict(vf_keys=("K",), where_item=True),                             # componente fijado por igualdad a constante
    dict(vf_keys=("K", "ATTR"), group_by=["K", "ATTR"]),               # agregacion LEGITIMA: la clave cubre el conjunto agrupado
])
def test_e5_sufficient_keys_and_legitimate_aggregation_are_never_penalized(kw):
    p, s = _profile(), _jkp(**kw)
    assert _e(p, s)[1]["state"] == ST.SUFFICIENT
    assert not [r for r in evaluate_judgment(copy.deepcopy(p), structural_evidence=s, **V7)["reasons"] if r["rule_id"] == "PUB-LOC-02"]


def test_e5_aggregation_not_covered_by_the_declared_key_is_deficient():
    assert _e(_profile(), _jkp(vf_keys=("K",), group_by=["K", "ATTR"]))[1]["state"] == ST.DEFICIENT


@pytest.mark.parametrize("kw,expected", [
    (dict(vf_keys=()), ST.NOT_APPLICABLE),                                                        # sin clave declarada: nada que contradecir
    (dict(vf_keys=("K",), union=True), ST.NOT_EVALUABLE),
    (dict(vf_keys=("K",), eqs=[{"kind": "TRANSFORMED", "secondary_column": "DK", "functions": ["LTRIM"]}]), ST.NOT_EVALUABLE),
    (dict(vf_keys=("K",), dim_keys=()), ST.NOT_EVALUABLE),                                         # cardinalidad desconocida
    (dict(vf_keys=("K",), fact_keys=()), ST.NOT_EVALUABLE),                                        # conductora sin clave declarada
])
def test_e5_abstains_without_positive_evidence(kw, expected):
    assert _e(_profile(), _jkp(**kw))[1]["state"] == expected


def test_e5_an_ungoverned_driver_is_not_evaluable():
    assert _e(_profile(driver_external=True), _jkp(vf_keys=("K",)))[1]["state"] == ST.NOT_EVALUABLE


# ───────────── aislamiento ─────────────
def test_without_structural_evidence_v7_records_nothing_and_v6_ignores_it():
    p = _profile()
    j7 = evaluate_judgment(copy.deepcopy(p), **V7)
    assert "structural_evidence" not in j7
    j6a, j6b = evaluate_judgment(copy.deepcopy(p), **V6), evaluate_judgment(copy.deepcopy(p), structural_evidence=_jkp(), **V6)
    assert j6a == j6b                                                                               # v6 nunca la consume


def test_the_structural_input_enters_the_output_signature():
    p = _profile()
    a = evaluate_judgment(copy.deepcopy(p), structural_evidence=_jkp(vf_keys=("K",)), **V7)
    b = evaluate_judgment(copy.deepcopy(p), structural_evidence=_jkp(vf_keys=("K", "ITEM"), project_item=True), **V7)
    assert a["structural_evidence"]["digest"] != b["structural_evidence"]["digest"] and a["output_signature"] != b["output_signature"]


# ───────────── base de la cardinalidad: contrato estructural ≠ unicidad física ─────────────
def test_e3_e5_record_a_contract_basis_and_never_claim_physical_uniqueness():
    e3, e5 = _e(_profile(), _jkp(vf_keys=("K",)))
    for r in (e3, e5):
        assert r["cardinality_basis"] == "DECLARED_KEY_CONTRACT" and r["physical_uniqueness"] == "NOT_VERIFIED"
    assert all(j["cardinality_basis"] == "DECLARED_KEY_CONTRACT" and j["physical_uniqueness"] == "NOT_VERIFIED" for n in e3["nodes"] for j in n["joins"])


@pytest.mark.parametrize("who", ["DIM", "FACT"])
def test_a_key_not_taken_from_the_csn_key_elements_is_not_a_reliable_contract(who):
    s = _jkp(vf_keys=("K",))
    s["join_key_profile"]["keys"][who]["source"] = "INFERRED_FROM_NAMES"
    e3, e5 = _e(_profile(), s)
    assert e5["state"] == ST.NOT_EVALUABLE
    if who == "DIM":
        assert e3["state"] == ST.NOT_EVALUABLE and e3["nodes"][0]["joins"][0]["reason"] == "SECONDARY_KEY_CONTRACT_NOT_RELIABLE_FOR_CLASS"


def test_a_secondary_outside_the_governed_cds_class_has_no_reliable_key_contract():
    p = _profile()
    ev = ProfileEvidence(p, None)
    ev.leaf_class_records["DIM"] = {**(ev.leaf_class_records.get("DIM") or {}), "contract_class": "GOVERNED_SAP_APPLICATION_API"}
    jkp = ST.valid_block(_jkp(vf_keys=("K",))["join_key_profile"])
    assert ST.e3_enrichment(ev, jkp, "UNDETERMINED")["state"] == ST.NOT_EVALUABLE and ST.e5_key_sufficiency(ev, jkp, "UNDETERMINED")["state"] == ST.NOT_EVALUABLE
