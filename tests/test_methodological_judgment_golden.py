"""
Gate 8D.1 — benchmark DORADO: los 8 niveles humanos congelados deben reproducirse con el motor GENERICO (sin ramas por cliente/raiz).

Cada caso valida: nivel, estado, cierre, regla/razon decisiva (id, clase, dimension), que TODA evidence_ref exista en el perfil y los calificadores esperados.
"""
from __future__ import annotations

import pytest

from tests._judgment_cases import BENCHMARK, fresh
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence

ALIASES = sorted(BENCHMARK)


@pytest.mark.parametrize("alias", ALIASES)
def test_frozen_benchmark_level_and_decisive_reason(alias):
    level, decisive, cls, dim, closure = BENCHMARK[alias]
    p = fresh(alias)
    j = evaluate_judgment(p)
    assert (j["status"], j["level"], j["closure"]) == ("DETERMINED", level, closure)
    assert j["plena_assignment"] == "DISABLED_UNTIL_CALIBRATED" and j["level"] != "PLENA"
    if cls == "POSITIVE_EVIDENCE":
        assert {s["rule_id"] for s in j["strengths"]} >= set(decisive)
        assert not [r for r in j["reasons"] if r["classification"] in ("MATERIAL_DEVIATION", "RELEVANT_WEAKNESS")]
        assert j["level_rule"] == "LVL-04"
    else:
        hit = [r for r in j["reasons"] if r["classification"] == cls and r["rule_id"] in decisive]
        assert hit and all(r["dimension"] == dim for r in hit)
        assert j["level_rule"] == ("LVL-02" if level == "PARCIAL" else "LVL-03")
        for r in hit:
            assert r["evidence_refs"] and r["deterministic_basis"] and r["state"] == "OPEN_CANDIDATE" and r["disposition"]["allowed"]


@pytest.mark.parametrize("alias", ALIASES)
def test_every_evidence_ref_exists_and_every_material_or_relevant_reason_is_grounded(alias):
    p = fresh(alias)
    ev = ProfileEvidence(p)
    j = evaluate_judgment(p)
    for group in (j["reasons"], j["strengths"], j["review_candidates"], j["evidence_limitations"], j["cta_candidates"]):
        for item in group:
            assert all(ev.ref_exists(r) for r in item.get("evidence_refs", ())), item
    for r in j["reasons"]:
        if r["classification"] in ("MATERIAL_DEVIATION", "RELEVANT_WEAKNESS"):
            assert r["evidence_refs"] and r["deterministic_basis"] and r["rule_id"] and r["affected_artifacts"] and "disposition" in r and "state" in r
        if r["classification"] == "MATERIAL_DEVIATION":
            assert set(r["causal_conditions"]) >= {"expected_responsibility", "unresolved", "compensation"}
            assert set(r["affected_artifacts"]) <= ev.known_artifacts()


def test_the_pro_mat_01_calibration_cases_use_architectural_position_and_the_authoritative_mechanism():
    """BASE_STOCKS: el nodo de producto mezcla un contrato armonizado de nivel hecho con hojas de frontera directas. Reserva: hojas fisicas crudas + contrato armonizado.
    Lista: >=2 hojas de frontera (una fisica) integradas en el nodo de producto sin ningun candidato de armonizacion."""
    forms = {}
    for alias in ("biogenesis_base_stocks", "biogenesis_reserva_material", "biogenesis_lista_precios"):
        j = evaluate_judgment(fresh(alias))
        r = next(x for x in j["reasons"] if x["rule_id"] == "PRO-MAT-01")
        forms[alias] = [b for b in r["deterministic_basis"] if b.startswith("PRO.form.")][0]
        assert "PRO.product_position_node" in r["deterministic_basis"]
    assert forms == {"biogenesis_base_stocks": "PRO.form.i_mixed_universe", "biogenesis_reserva_material": "PRO.form.i_mixed_universe",
                     "biogenesis_lista_precios": "PRO.form.ii_multi_boundary_integration_without_harmonization"}


def test_the_lek_mat_02_calibration_cases_converge_on_distributed_leakage_with_unrestored_publication():
    for alias, element in (("illolay_pedidos_entregas", "LEK.L1.a_physical_raw_on_strict_spine"), ("biogenesis_sellout_gp", "LEK.L1.a_physical_raw_on_strict_spine")):
        r = next(x for x in evaluate_judgment(fresh(alias))["reasons"] if x["rule_id"] == "LEK-MAT-02")
        assert element in r["deterministic_basis"] and "LEK.L2.absorbed_by_non_candidate_nodes_with_logic" in r["deterministic_basis"]
        assert "LEK.L3.published_measure_without_business_identity" in r["deterministic_basis"]
        assert r["secondary_dimension"] == "PUBLISHED_CONTRACT"
    ill = next(x for x in evaluate_judgment(fresh("illolay_pedidos_entregas"))["reasons"] if x["rule_id"] == "LEK-MAT-02")
    assert "LEK.L1.b_reusable_calendar_semantics_derived_downstream" in ill["deterministic_basis"]       # el calendario derivado aguas abajo, ademas de la fuente fisica


def test_illolay_stays_parcial_without_any_raw_source_element():
    """La cruda REFUERZA pero no es la causa unica: si se retiran los elementos de fuente fisica, el calendario reutilizado sigue sosteniendo PARCIAL."""
    p = fresh("illolay_pedidos_entregas")
    for key in ("observations", "review_required"):
        p[key] = [g for g in p[key] if g["id"] != "DIRECT_RAW_TABLE_ACCESS"]
    j = evaluate_judgment(p)
    assert j["level"] == "PARCIAL"
    lek = next(x for x in j["reasons"] if x["rule_id"] == "LEK-MAT-02")
    assert not any("a_physical_raw" in b for b in lek["deterministic_basis"]) and "LEK.L1.b_reusable_calendar_semantics_derived_downstream" in lek["deterministic_basis"]


def test_qualifiers_for_the_calibration_cases():
    q = {a: evaluate_judgment(fresh(a))["qualifiers"] for a in ALIASES}
    assert q["biogenesis_prefacturacion"]["design_coherence"]["value"] == "WEAK"
    assert q["biogenesis_sellout_gp"]["design_coherence"]["value"] == "WEAK"
    assert q["biogenesis_descuentos_comerciales"]["design_coherence"]["value"] == "OPPORTUNITIES"
    assert q["kensing_gl_line_item_raw"]["design_coherence"]["value"] == "SOLID" and q["kensing_gl_line_item_raw"]["product_focus"]["value"] == "GENERIC"
    assert q["kensing_gl_line_item_raw"]["naming_governance"]["state"] == "MINOR_NAMING_DEVIATION"
    for a in ALIASES:
        assert q[a]["naming_governance"]["level_effect"] == "NONE" and q[a]["naming_governance"]["closure_effect"] == "NONE" and q[a]["product_focus"]["state"] == "SUGGESTED_NEEDS_CONTEXT"
    assert q["biogenesis_sellout_gp"]["naming_governance"]["state"] == "NAMING_PLUS_ROLE_AMBIGUITY"


def test_sellout_exposes_complex_graphical_chain_review_as_a_review_not_a_level_driver():
    j = evaluate_judgment(fresh("biogenesis_sellout_gp"))
    rv = [r for r in j["review_candidates"] if r["review_id"] == "COMPLEX_GRAPHICAL_CHAIN_REVIEW"]
    assert rv and rv[0]["level_effect"] == "NONE"
    assert not [r for r in j["reasons"] if r["dimension"] == "MAINTAINABILITY"]
    assert any(c["cta_id"] == "CTA-MNT-REVIEW-GRAPH-DECOMPOSITION" and c["priority_class"] == "MAINTAINABILITY" for c in j["cta_candidates"])


def test_prefacturacion_and_descuentos_are_two_different_adequada_profiles():
    pref, desc = evaluate_judgment(fresh("biogenesis_prefacturacion")), evaluate_judgment(fresh("biogenesis_descuentos_comerciales"))
    assert pref["level"] == desc["level"] == "ADECUADA"
    assert {r["rule_id"] for r in pref["reasons"] if r["classification"] == "RELEVANT_WEAKNESS"} == {"HAR-REL-02"}
    assert {r["rule_id"] for r in desc["reasons"] if r["classification"] == "RELEVANT_WEAKNESS"} == {"HAR-REL-01"}
    assert pref["qualifiers"]["design_coherence"]["value"] == "WEAK" and desc["qualifiers"]["design_coherence"]["value"] == "OPPORTUNITIES"      # matiz por calificador, sin quinto nivel


def test_open_reviews_do_not_block_final_closure_of_the_buena_case_and_open_naming_does_not_either():
    j = evaluate_judgment(fresh("kensing_gl_line_item_raw"))
    assert j["closure"] == "FINAL" and j["status"] == "DETERMINED" and j["level"] == "BUENA"
    nm = j["qualifiers"]["naming_governance"]
    assert nm["disposition"] == "NEEDS_CONTEXT" and nm["level_effect"] == nm["closure_effect"] == "NONE"      # revision de naming abierta, y aun asi BUENA/FINAL
    assert any(c["cta_id"] == "CTA-GOV-DECLARE-NAMING-CONVENTION" for c in j["cta_candidates"])


def test_fixed_predicate_ownership_is_not_evaluated_and_never_blocks_judgment():
    j = evaluate_judgment(fresh("biogenesis_base_stocks"))
    rv = [r for r in j["review_candidates"] if r["review_id"] == "FIXED_PREDICATE_OWNERSHIP_REVIEW"]
    assert rv and rv[0]["state"] == "NOT_EVALUATED/DISABLED_PENDING_EVIDENCE" and rv[0]["level_effect"] == "NONE"
    assert any(l["key"] == "predicate_ownership_not_evaluated" for l in j["evidence_limitations"])
    assert not [r for r in j["reasons"] if r["rule_id"] == "PRO-REV-01"]


def test_the_eight_labels_are_exactly_the_frozen_benchmark():
    expected = {"illolay_pedidos_entregas": "PARCIAL", "biogenesis_base_stocks": "PARCIAL", "biogenesis_descuentos_comerciales": "ADECUADA", "kensing_gl_line_item_raw": "BUENA",
                "biogenesis_prefacturacion": "ADECUADA", "biogenesis_lista_precios": "PARCIAL", "biogenesis_reserva_material": "PARCIAL", "biogenesis_sellout_gp": "PARCIAL"}
    assert {a: BENCHMARK[a][0] for a in BENCHMARK} == expected and len(expected) == 8
    assert {a: evaluate_judgment(fresh(a))["level"] for a in expected} == expected
