"""
Gate 8D.1 — benchmark DORADO: los 8 niveles humanos congelados deben reproducirse con el motor GENERICO (sin ramas por cliente/raiz).

Cada caso valida: nivel, estado, cierre, regla/razon decisiva (id, clase, dimension), que TODA evidence_ref exista en el perfil y los calificadores esperados.
"""
from __future__ import annotations

import json

import pytest

from tests._judgment_cases import BENCHMARK, FROZEN_LABELS, KNOWN_EVIDENCE_GAPS, fresh
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence

ALIASES = sorted(BENCHMARK)


@pytest.mark.parametrize("alias", ALIASES)
def test_frozen_benchmark_level_and_decisive_reason(alias):
    level, decisive, cls, dim, closure = BENCHMARK[alias]
    p = fresh(alias)
    j = evaluate_judgment(p)
    assert j["plena_assignment"] == "DISABLED_UNTIL_CALIBRATED" and j["level"] != "PLENA"
    if cls == "NOT_DETERMINABLE":
        # El motor NO afirma el nivel: lo marca NOT_DETERMINABLE con el codigo decision-critico (brecha nombrada, no un nivel inventado).
        assert (j["status"], j["level"], j["closure"]) == ("NOT_DETERMINABLE", None, closure)
        assert j["level_rule"] == "LVL-05" and j.get("not_determinable_reason") == decisive[0]
        return
    assert (j["status"], j["level"], j["closure"]) == ("DETERMINED", level, closure)
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


def test_the_frozen_human_labels_are_unchanged_and_every_divergence_is_a_documented_evidence_gap():
    assert FROZEN_LABELS == {"illolay_pedidos_entregas": "PARCIAL", "biogenesis_base_stocks": "PARCIAL", "biogenesis_descuentos_comerciales": "ADECUADA", "kensing_gl_line_item_raw": "BUENA",
                             "biogenesis_prefacturacion": "ADECUADA", "biogenesis_lista_precios": "PARCIAL", "biogenesis_reserva_material": "PARCIAL", "biogenesis_sellout_gp": "PARCIAL"}
    produced = {a: evaluate_judgment(fresh(a))["level"] for a in FROZEN_LABELS}
    diverging = {a for a in FROZEN_LABELS if produced[a] != FROZEN_LABELS[a]}
    assert diverging == set(KNOWN_EVIDENCE_GAPS) == {"biogenesis_sellout_gp"}                     # 7/8 con la evidencia actual; la brecha esta nombrada, no se oculta ni se rodea
    assert {a: BENCHMARK[a][0] for a in BENCHMARK} == produced


def test_the_sellout_gap_is_exactly_the_unclassified_external_contract_not_a_workaround():
    """La evidencia por campo muestra POR QUE: A004/A307 (tablas ABAP crudas) NO llegan al consumidor con su vocabulario; el vocabulario tecnico
    llega VERBATIM desde fuentes SQL Server cuya clase es EXTERNAL_CONTRACT_UNDETERMINED. Esa clase es DECISION-CRITICA (si cruda, debilidad de
    contrato publicado; si gobernada, reuso) => el motor emite NOT_DETERMINABLE, no ADECUADA/PARCIAL falsas. Resolver la clase (a cruda O a
    gobernada) elimina la condicion y el caso vuelve a ser DETERMINADO; y Prefacturacion NO se mueve bajo ninguna politica."""
    from tools.catalog.methodological_judgment.rules import vocabulary_exposure, decision_critical_undetermined_source
    p = fresh("biogenesis_sellout_gp")
    ev = ProfileEvidence(p)
    raw = [l for l in ev.remote_leaves if ev.classify_source(l) == "PHYSICAL_RAW_BOUNDARY"]
    assert raw and vocabulary_exposure(ev, raw) == {}
    external = [l for l, r in ev.leaf_class_records.items() if r["contract_class"] == "EXTERNAL_CONTRACT_UNDETERMINED"]
    assert external and vocabulary_exposure(ev, external)                                          # el vocabulario tecnico sí llega al publicado... desde fuentes de clase indeterminada
    assert decision_critical_undetermined_source(ev, "UNDETERMINED")                               # y esa exposicion verbatim es justo la condicion decision-critica
    j = evaluate_judgment(p)
    assert j["status"] == "NOT_DETERMINABLE" and j["level"] is None and j["level_rule"] == "LVL-05"
    assert j["not_determinable_reason"] == "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"
    # Resolver la clase externa — a cruda O a gobernada — levanta la indeterminacion: el caso vuelve a ser DETERMINADO (y la cruda sola, por la
    # nueva semantica de LEK, ya NO es MATERIAL: es RELEVANT, no LEK-MAT-02).
    for pol in ("PHYSICAL_RAW", "GOVERNED"):
        sens = evaluate_judgment(p, external_policy=pol)
        assert sens["status"] == "DETERMINED" and sens["level"] == "ADECUADA"
    assert "LEK-MAT-02" not in {r["rule_id"] for r in evaluate_judgment(p, external_policy="PHYSICAL_RAW")["reasons"]}
    pref = fresh("biogenesis_prefacturacion")
    assert {evaluate_judgment(pref, external_policy=pol)["level"] for pol in ("UNDETERMINED", "PHYSICAL_RAW", "GOVERNED")} == {"ADECUADA"}


def test_lek_material_requires_structural_contract_centrality_and_raw_bypass_alone_is_only_relevant():
    """MATERIAL por LEK exige centralidad ESTRUCTURAL (L1(b): la misma responsabilidad reutilizable creada/poseida en varios nodos aguas
    abajo), no un camino crudo existencial. Illolay la tiene (b) => LEK-MAT-02. Lista/Reserva tienen solo la fuga cruda (a) => LEK-REL-01
    (RELEVANT); su PARCIAL lo determina PRO-MAT-01 de forma independiente, no LEK. Sin proxies de medida/clave, sin conteos."""
    # Illolay: LEK-MAT-02 MATERIAL, sostenido por (b) estructural + consecuencia publicada trazable.
    ill = next(x for x in evaluate_judgment(fresh("illolay_pedidos_entregas"))["reasons"] if x["rule_id"] == "LEK-MAT-02")
    assert ill["classification"] == "MATERIAL_DEVIATION" and ill["secondary_dimension"] == "PUBLISHED_CONTRACT"
    assert "LEK.L1.b_reusable_semantics_created_downstream_over_technical_boundary" in ill["deterministic_basis"]
    assert "LEK.L3.downstream_created_semantics_reaches_the_consumer_through_traceable_paths" in ill["deterministic_basis"]
    assert any(ref.startswith("ce:published_field_lineage/") for ref in ill["evidence_refs"])
    # Lista/Reserva: SOLO (a) => LEK-REL-01 RELEVANT (no MATERIAL), y PARCIAL lo trae PRO-MAT-01.
    for alias in ("biogenesis_lista_precios", "biogenesis_reserva_material"):
        reasons = evaluate_judgment(fresh(alias))["reasons"]
        ids = {r["rule_id"] for r in reasons}
        assert "LEK-MAT-02" not in ids                                                              # la fuga cruda existencial ya no es MATERIAL
        rel = next(x for x in reasons if x["rule_id"] == "LEK-REL-01")
        assert rel["classification"] == "RELEVANT_WEAKNESS" and rel["dimension"] == "PUBLISHED_CONTRACT"
        assert "LEK.L1.a_physical_raw_bypassing_harmonization_compensated_downstream" in rel["deterministic_basis"]
        assert "LEK.L3.unresolved_boundary_vocabulary_reaches_the_consumer_verbatim_through_traceable_paths" in rel["deterministic_basis"]
        assert "LEK.no_structural_contract_centrality_reusable_responsibility_not_owned_in_multiple_downstream_nodes" in rel["deterministic_basis"]
        assert any(ref.startswith("ce:published_field_lineage/") for ref in rel["evidence_refs"])
        mat = [r for r in reasons if r["classification"] == "MATERIAL_DEVIATION"]
        assert mat and {r["rule_id"] for r in mat} == {"PRO-MAT-01"}                                # otra regla ratificada determina PARCIAL


def test_illolay_stays_parcial_without_any_raw_source_through_generic_structural_and_lineage_evidence():
    """Si ninguna hoja es una tabla ABAP fisica, la semantica reutilizable creada aguas abajo sobre una frontera tecnica (A+B estructurales) con consecuencia trazable en el publicado
    (campos que atraviesan los sitios con derivacion sin armonizacion) sigue sosteniendo PARCIAL."""
    p = fresh("illolay_pedidos_entregas")
    for key in ("observations", "review_required"):
        p[key] = [g for g in p[key] if g["id"] != "DIRECT_RAW_TABLE_ACCESS"]
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["contract_class"] == "PHYSICAL_ABAP_TABLE_BOUNDARY":
            r["contract_class"] = "GOVERNED_SAP_CDS_CONTRACT"
    for f in p["canonical_evidence"]["published_field_lineage"]["fields"]:
        for lf in f["frontier_leaves"]:
            if lf["source_contract_class"] == "PHYSICAL_ABAP_TABLE_BOUNDARY":
                lf["source_contract_class"] = "GOVERNED_SAP_CDS_CONTRACT"
    j = evaluate_judgment(p)
    assert j["level"] == "PARCIAL"
    lek = next(x for x in j["reasons"] if x["rule_id"] == "LEK-MAT-02")
    assert not any("a_physical_raw" in b for b in lek["deterministic_basis"]) and "LEK.L1.b_reusable_semantics_created_downstream_over_technical_boundary" in lek["deterministic_basis"]
    assert "LEK.L3.downstream_created_semantics_reaches_the_consumer_through_traceable_paths" in lek["deterministic_basis"]


def test_the_v2_profile_is_not_judgeable_because_causal_lineage_is_required():
    p = fresh("kensing_gl_line_item_raw")
    p["canonical_evidence"].pop("published_field_lineage")
    p["canonical_evidence"]["version"] = "canonical-evidence/v2"
    j = evaluate_judgment(p)
    assert j["status"] == "NOT_DETERMINABLE" and j["level"] is None and "CANONICAL_EVIDENCE_V3_REQUIRED" in json.dumps(j)
    # la remediacion sugerida pide evidencia v3 (no la v2 stale): el Juicio exige v3.
    keys = [c["change_key"] for c in j["what_would_change_the_judgment"]]
    assert any("CANONICAL_EVIDENCE_V3" in k for k in keys) and not any("CANONICAL_EVIDENCE_V2" in k for k in keys)
