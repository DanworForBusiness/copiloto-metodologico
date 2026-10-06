"""
Gate 8D.1 — reglas del Juicio sobre perfiles SINTETICOS (sin clientes) y pruebas CONTRAFACTICAS / anti-sobreajuste.

El nivel es funcion de la EXISTENCIA de razones clasificadas (no de conteos ni proporciones); estas pruebas fijan esa propiedad.
"""
from __future__ import annotations

import copy
import json

import pytest

from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import evaluate_judgment


def rules(j, cls=None):
    return {r["rule_id"] for r in j["reasons"] if cls is None or r["classification"] == cls}


def reason(j, rule_id):
    return next(r for r in j["reasons"] if r["rule_id"] == rule_id)


def buena_model() -> PB:
    """Modelo limpio: AM -> V1 (producto) -> H1 (candidato 1:1 tecnico sobre contrato SAP gobernado), medidas con identidad de negocio y asociacion a dimension."""
    return (PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", sources=["CDS1"]).remote("CDS1").product("V1").assoc("DIM1").measure("M1")
            .flow("RF", delta=True))


# ───────────────────────────── fortalezas y BUENA ─────────────────────────────
def test_buena_requires_affirmative_strength_in_all_four_critical_contracts():
    j = evaluate_judgment(buena_model().build())
    assert (j["status"], j["level"], j["closure"], j["level_rule"]) == ("DETERMINED", "BUENA", "FINAL", "LVL-04")
    assert {s["rule_id"] for s in j["strengths"]} >= {"SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"}
    assert not j["reasons"]


def test_absence_of_findings_is_not_buena():
    """Sin candidato de armonizacion no hay fortaleza de armonizacion: la ausencia de hallazgos NO alcanza."""
    b = PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").assoc("D").measure("M")
    j = evaluate_judgment(b.build())
    assert not j["reasons"] and j["status"] == "NOT_DETERMINABLE" and j["level"] is None and j["closure"] == "PROVISIONAL"
    assert "HAR-STR-01" not in {s["rule_id"] for s in j["strengths"]}


@pytest.mark.parametrize("missing", ["SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"])
def test_dropping_any_one_critical_strength_prevents_buena(missing):
    b = buena_model()
    if missing == "SRC-STR-01":
        b.remote("TBL", raw=False).other_origin(1)               # un contacto con fuente no establecida como gobernada
    elif missing == "HAR-STR-01":
        b._cands["H1"]["source_vocabulary"]["fields_with_source_column_name"] = 4
    elif missing == "PRO-STR-01":
        b._views["V1"]["provider_names"].append("CDS9"); b.remote("CDS9")
    elif missing == "PUB-STR-01":
        b._assoc.clear()
    j = evaluate_judgment(b.build())
    assert missing not in {s["rule_id"] for s in j["strengths"]} and j["level"] != "BUENA"


def test_one_to_one_layers_are_not_penalized():
    j = evaluate_judgment(buena_model().build())
    assert j["level"] == "BUENA" and not rules(j, "RELEVANT_WEAKNESS") and not rules(j, "LOCAL_WEAKNESS")


# ───────────────────────────── PRO-MAT-01 ─────────────────────────────
def test_pro_mat_01_mixed_universe_is_material_and_parcial():
    b = PB().view("V1", ["H1", "CDS2"], joins=1).cand("H1").remote("CDS2").product("V1").assoc("D").measure("M")
    j = evaluate_judgment(b.build())
    assert j["level"] == "PARCIAL" and "PRO-MAT-01" in rules(j, "MATERIAL_DEVIATION") and j["level_rule"] == "LVL-02"


def test_pro_mat_01_does_not_fire_for_a_fact_composed_with_a_reusable_dimension():
    b = PB().view("V1", ["H1", "CDS2"], joins=1).cand("H1", dimension=True).remote("CDS2").product("V1").assoc("D").measure("M")
    assert "PRO-MAT-01" not in rules(evaluate_judgment(b.build()))


def test_pro_mat_01_multi_boundary_integration_without_harmonization_needs_a_physical_raw_leaf():
    gov_only = PB().view("V1", ["C1", "C2"], joins=1).remote("C1").remote("C2").product("V1").assoc("D").measure("M")
    assert "PRO-MAT-01" not in rules(evaluate_judgment(gov_only.build()))
    with_raw = PB().view("V1", ["T1", "C2"], joins=1).remote("T1", raw=True).remote("C2").product("V1").assoc("D").measure("M")
    assert "PRO-MAT-01" in rules(evaluate_judgment(with_raw.build()), "MATERIAL_DEVIATION")


def test_pro_mat_01_is_position_based_not_name_based():
    """El mismo patron en un nodo que NO es de producto (mas profundo) no genera MATERIAL de producto."""
    b = (PB().view("TOP", ["MID"]).view("MID", ["H1", "CDS2"], joins=1).cand("H1").remote("CDS2").product("TOP").assoc("D").measure("M"))
    assert "PRO-MAT-01" not in rules(evaluate_judgment(b.build()))


def test_pass_through_view_between_product_and_leaf_does_not_hide_the_pattern():
    b = (PB().view("V1", ["PT", "H1"], joins=1).view("PT", ["CDS2"]).cand("H1").remote("CDS2").product("V1").assoc("D").measure("M"))
    assert "PRO-MAT-01" in rules(evaluate_judgment(b.build()), "MATERIAL_DEVIATION")


# ───────────────────────────── LEK-MAT-02 ─────────────────────────────
def lek_model(*, enriched=False, raw=True, logic=True) -> PB:
    b = (PB().view("V1", ["V2"], derived=1).view("V2", ["TBL"], joins=1 if logic else 0, derived=1 if logic else 0).remote("TBL", raw=raw).product("V1").assoc("D"))
    b.measure("M", enriched=enriched)
    return b


def test_lek_mat_02_requires_raw_on_spine_with_downstream_logic_and_unrestored_publication():
    j = evaluate_judgment(lek_model().build())
    r = reason(j, "LEK-MAT-02")
    assert j["level"] == "PARCIAL" and r["classification"] == "MATERIAL_DEVIATION" and r["secondary_dimension"] == "PUBLISHED_CONTRACT"
    assert {"LEK.L1.a_physical_raw_on_strict_spine", "LEK.L2.absorbed_by_non_candidate_nodes_with_logic", "LEK.L3.published_measure_without_business_identity"} <= set(r["deterministic_basis"])


def test_lek_l3_is_existence_based_not_a_ratio():
    """UNA medida sin identidad de negocio basta, aunque haya muchas otras con identidad (no hay umbral de mayoria)."""
    b = lek_model(enriched=True)
    for i in range(9):
        b.measure(f"OK{i}")
    b.measure("BAD", enriched=False)
    assert "LEK-MAT-02" in rules(evaluate_judgment(b.build()), "MATERIAL_DEVIATION")
    b2 = lek_model(enriched=True)                                                # todas con identidad: la publicacion restaura -> no hay MATERIAL
    assert "LEK-MAT-02" not in rules(evaluate_judgment(b2.build()))


def test_raw_alone_is_never_material():
    j = evaluate_judgment(lek_model(enriched=True, logic=False).build())
    assert "LEK-MAT-02" not in rules(j, "MATERIAL_DEVIATION") and j["level"] != "PARCIAL"


def test_lek_l1_b_calendar_semantics_reused_across_sites_without_raw():
    b = lek_model(raw=False)
    b._views["V2"]["provider_names"] = ["CDS1"]; b.remote("CDS1")
    b.temporal_reuse("V1", "V2")
    j = evaluate_judgment(b.build())
    assert "LEK.L1.b_reusable_calendar_semantics_derived_downstream" in reason(j, "LEK-MAT-02")["deterministic_basis"]
    b2 = lek_model(raw=False); b2._views["V2"]["provider_names"] = ["CDS1"]; b2.remote("CDS1"); b2.temporal_reuse("V1")        # un solo sitio: no es reutilizacion
    assert "LEK-MAT-02" not in rules(evaluate_judgment(b2.build()))


def test_a_candidate_that_owns_the_temporal_semantics_is_not_a_leak():
    b = lek_model(raw=False)
    b._views["V2"]["provider_names"] = ["CDS1"]; b.remote("CDS1")
    b.cand("V2", character="SEMANTIC_TRANSFORMATION_PRESENT", sources=["CDS1"]).temporal_reuse("V2", "V2")
    assert "LEK-MAT-02" not in rules(evaluate_judgment(b.build()))


# ───────────────────────────── RELEVANT / LOCAL ─────────────────────────────
def test_har_rel_01_all_candidates_technical_with_source_vocabulary_is_adequada():
    b = (PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", carry=4, sources=["CDS1"]).remote("CDS1").product("V1").assoc("D").measure("M"))
    j = evaluate_judgment(b.build())
    assert j["level"] == "ADECUADA" and "HAR-REL-01" in rules(j, "RELEVANT_WEAKNESS") and j["level_rule"] == "LVL-03"


def test_har_rel_01_not_triggered_if_one_candidate_enriches():
    b = (PB().view("V1", ["H1", "H2"], joins=1).view("H1", ["C1"]).view("H2", ["C2"]).cand("H1", carry=4, sources=["C1"]).cand("H2", character="SEMANTIC_TRANSFORMATION_PRESENT", sources=["C2"])
         .remote("C1").remote("C2").product("V1").assoc("D").measure("M"))
    assert "HAR-REL-01" not in rules(evaluate_judgment(b.build()))


def test_src_rel_01_raw_on_spine_with_compensation_is_relevant_but_not_material_by_itself():
    j = evaluate_judgment(lek_model(enriched=True).build())
    assert "SRC-REL-01" in rules(j, "RELEVANT_WEAKNESS") and "LEK-MAT-02" not in rules(j) and j["level"] == "ADECUADA"


def test_external_contract_undetermined_never_creates_material():
    """Fuente no SAP (clase EXTERNAL_CONTRACT_UNDETERMINED): ni MATERIAL ni alteracion mecanica de nivel."""
    b = PB().view("V1", ["E1", "H1"], joins=1).cand("H1").remote("H1x").remote("E1").other_origin(1).product("V1").assoc("D").measure("M")
    j = evaluate_judgment(b.build())
    assert not rules(j, "MATERIAL_DEVIATION")


@pytest.mark.parametrize("policy", ["UNDETERMINED", "PHYSICAL_RAW", "GOVERNED"])
def test_external_policy_is_a_sensitivity_input_only_and_default_never_materializes(policy):
    b = PB().view("V1", ["E1", "E2"], joins=1).remote("E1").remote("E2").other_origin(2).product("V1").assoc("D").measure("M")
    j = evaluate_judgment(b.build(), external_policy=policy)
    if policy == "UNDETERMINED":
        assert not rules(j, "MATERIAL_DEVIATION")
    assert j["plena_assignment"] == "DISABLED_UNTIL_CALIBRATED"


# ───────────────────────────── naming / calificadores / revisiones ─────────────────────────────
@pytest.mark.parametrize("non_compliant", [0, 1, 4, 5, 6, 10])
def test_naming_ratio_never_changes_level_or_closure(non_compliant):
    """Ninguna proporcion de nombres (incl. cruzar el 50%) mueve nivel/cierre."""
    base = evaluate_judgment(buena_model().naming(10, 0).build())
    j = evaluate_judgment(buena_model().naming(10, non_compliant).build())
    assert (j["level"], j["closure"], j["status"]) == (base["level"], base["closure"], base["status"]) == ("BUENA", "FINAL", "DETERMINED")
    assert j["qualifiers"]["naming_governance"]["level_effect"] == "NONE" and j["qualifiers"]["naming_governance"]["closure_effect"] == "NONE"


def test_field_label_ratio_crossing_fifty_percent_does_not_change_the_level():
    def model(n_technical):
        b = buena_model()
        b._fields = [{"technical_name": f"F{i}", "role": "ATTRIBUTE", "business_name_equals_technical": i < n_technical, "name_equals_remote_source_column": i < n_technical, "derived": False}
                     for i in range(10)]
        return b
    levels = {evaluate_judgment(model(n).build())["level"] for n in (0, 4, 5, 6, 10)}
    assert levels == {"BUENA"}


def test_association_count_plus_minus_one_does_not_flip_the_level():
    b = buena_model().assoc("DIM2").assoc("DIM3")
    base = evaluate_judgment(b.build())["level"]
    b._assoc.pop()
    assert evaluate_judgment(b.build())["level"] == base == "BUENA"


def test_graphical_only_and_sql_only_do_not_decide_level():
    graphical, sql = buena_model(), buena_model()
    for v in graphical._views.values():
        v["view_type"] = "GRAPHICAL"
    for v in sql._views.values():
        v["view_type"] = "SQL"
    assert evaluate_judgment(graphical.build())["level"] == evaluate_judgment(sql.build())["level"] == "BUENA"


def test_open_qualifier_review_does_not_force_provisional():
    b = buena_model()
    for v in b._views.values():
        v["view_type"] = "GRAPHICAL"; v["metrics"]["graph_depth"] = 9; v["metrics"]["select_nodes"] = 30
    j = evaluate_judgment(b.build())
    assert j["closure"] == "FINAL" and j["level"] == "BUENA"
    assert all(r["level_effect"] == "NONE" for r in j["review_candidates"])


def test_product_focus_is_a_qualifier_with_no_level_effect_and_needs_context():
    j = evaluate_judgment(buena_model().build())
    pf = j["qualifiers"]["product_focus"]
    assert pf["value"] in ("FOCUSED", "GENERIC", "PLACEHOLDER_LIKE", "NOT_DETERMINABLE") and pf["state"] == "SUGGESTED_NEEDS_CONTEXT"


# ───────────────────────────── dispositions ─────────────────────────────
def mat_model() -> PB:
    return PB().view("V1", ["H1", "CDS2"], joins=1).cand("H1").remote("CDS2").product("V1").assoc("D").measure("M")


def disp(target, decision, **kw):
    d = {"target": target, "decision": decision, "actor": "analyst", "rationale": "ok"}
    d.update(kw)
    return d


def test_accepted_as_designed_records_intent_but_changes_neither_class_nor_level():
    p = mat_model().build()
    base = evaluate_judgment(p)
    rid = reason(base, "PRO-MAT-01")["reason_id"]
    j = evaluate_judgment(p, [disp(rid, "ACCEPTED_AS_DESIGNED")])
    assert j["level"] == "PARCIAL" and reason(j, "PRO-MAT-01")["classification"] == "MATERIAL_DEVIATION"
    assert reason(j, "PRO-MAT-01")["state"] == "ACCEPTED_AS_DESIGNED" and j["closure"] == "FINAL"
    assert j["dispositions_applied"][0]["status"] == "APPLIED"


def test_confirmed_deviation_makes_parcial_final():
    p = mat_model().build()
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    j = evaluate_judgment(p, [disp(rid, "CONFIRMED_DEVIATION")])
    assert (j["level"], j["closure"]) == ("PARCIAL", "FINAL")


def test_needs_context_keeps_the_level_and_provisional_closure():
    p = mat_model().build()
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    j = evaluate_judgment(p, [disp(rid, "NEEDS_CONTEXT")])
    assert (j["level"], j["closure"]) == ("PARCIAL", "PROVISIONAL")


def valid_exception():
    return {"policy_ref": "POL-1", "approver": "architect", "version": "1", "scope": ["PRO-MAT-01"]}


def test_valid_governed_exception_changes_the_level_and_exposes_level_without_exceptions():
    p = mat_model().build()
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    j = evaluate_judgment(p, [disp(rid, "GOVERNED_EXCEPTION", exception=valid_exception(), evidence_refs=["ce:view_complexity"])])
    assert j["level"] != "PARCIAL" and j["level_without_exceptions"] == "PARCIAL"
    assert j["dispositions_applied"][0]["status"] == "APPLIED" and j["dispositions_applied"][0]["exception"]["policy_ref"] == "POL-1"


@pytest.mark.parametrize("patch", [
    {"exception": None},
    {"exception": {"policy_ref": "P", "approver": "analyst", "version": "1", "scope": ["x"]}},      # el aprobador no puede ser el actor
    {"exception": {"policy_ref": "P", "approver": "boss", "version": "", "scope": ["x"]}},
    {"exception": {"policy_ref": "P", "approver": "boss", "version": "1", "scope": []}},
    {"evidence_refs": []},
])
def test_invalid_governed_exception_is_rejected_and_ignored(patch):
    p = mat_model().build()
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    d = disp(rid, "GOVERNED_EXCEPTION", exception=valid_exception(), evidence_refs=["ce:view_complexity"])
    d.update(patch)
    j = evaluate_judgment(p, [d])
    assert j["level"] == "PARCIAL" and j["dispositions_applied"][0]["status"] == "REJECTED" and j["dispositions_applied"][0]["reject_code"] == "INVALID_EXCEPTION_OBJECT"


def test_not_applicable_requires_rule_specific_justification_and_evidence():
    p = mat_model().build()
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    bad = evaluate_judgment(p, [disp(rid, "NOT_APPLICABLE")])
    assert bad["dispositions_applied"][0]["reject_code"] == "JUSTIFICATION_AND_EVIDENCE_REQUIRED" and bad["level"] == "PARCIAL"
    ok = evaluate_judgment(p, [disp(rid, "NOT_APPLICABLE", justification="producto declarado de integracion", evidence_refs=["ce:view_complexity"])])
    assert ok["dispositions_applied"][0]["status"] == "APPLIED"


def test_confirmed_weakness_never_lowers_a_material_reason():
    p = mat_model().build()
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    j = evaluate_judgment(p, [disp(rid, "CONFIRMED_WEAKNESS")])
    assert j["level"] == "PARCIAL" and j["dispositions_applied"][0]["reject_code"] == "CLASS_NOT_ALLOWED"


def test_unknown_target_and_missing_actor_are_rejected():
    p = mat_model().build()
    assert evaluate_judgment(p, [disp("NOPE", "NEEDS_CONTEXT")])["dispositions_applied"][0]["reject_code"] == "UNKNOWN_TARGET"
    rid = reason(evaluate_judgment(p), "PRO-MAT-01")["reason_id"]
    assert evaluate_judgment(p, [disp(rid, "NEEDS_CONTEXT", actor="")])["dispositions_applied"][0]["reject_code"] == "ACTOR_AND_RATIONALE_REQUIRED"


def test_no_level_is_ever_lowered_by_one_step():
    """No existe 'bajar un nivel': PARCIAL con razon MATERIAL viva jamas deriva en un nivel distinto por conteo de razones."""
    for n in (1, 3, 6):
        b = mat_model()
        for i in range(n):
            b._views["V1"]["provider_names"].append(f"X{i}"); b.remote(f"X{i}")
        assert evaluate_judgment(b.build())["level"] == "PARCIAL"


# ───────────────────────────── no evaluable ─────────────────────────────
def test_not_evaluable_critical_area_makes_status_nd_even_with_relevant_reason():
    b = (PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", carry=4, sources=["CDS1"]).remote("CDS1").product("V1").assoc("D").measure("M"))
    b.area("PROPAGATION", "NOT_ASSESSABLE")
    j = evaluate_judgment(b.build())
    assert j["status"] == "NOT_DETERMINABLE" and j["level"] is None and j["contracts_not_evaluable"]


def test_unjudgeable_when_assessment_is_not_assessed_or_has_no_v2_evidence():
    p = buena_model().build()
    p["assessment_state"] = "NOT_ASSESSED"
    j = evaluate_judgment(p)
    assert j["status"] == "NOT_DETERMINABLE" and j["level"] is None
    p2 = buena_model().build()
    p2.pop("canonical_evidence")
    assert evaluate_judgment(p2)["status"] == "NOT_DETERMINABLE"


# ───────────────────────────── pureza / determinismo ─────────────────────────────
def test_pure_deterministic_and_does_not_mutate_the_profile():
    p = mat_model().build()
    snap = copy.deepcopy(p)
    a, b = evaluate_judgment(p), evaluate_judgment(copy.deepcopy(p))
    assert p == snap and a == b and a["output_signature"] == b["output_signature"]
    assert json.loads(json.dumps(a)) == a                                       # JSON-serializable
    assert "generated_at" not in json.dumps(a) and "timestamp" not in json.dumps(a)


def test_plena_is_never_emitted():
    for p in (buena_model().build(), mat_model().build(), lek_model().build()):
        j = evaluate_judgment(p)
        assert j["level"] != "PLENA" and j["plena_assignment"] == "DISABLED_UNTIL_CALIBRATED"


def test_output_has_no_numeric_score_or_weights():
    j = evaluate_judgment(mat_model().build())
    text = json.dumps(j)
    for token in ('"score"', '"weight"', '"points"', '"rating"', '"grade"'):
        assert token not in text


def test_judgment_vocabulary_is_not_written_into_the_profile():
    p = mat_model().build()
    evaluate_judgment(p)
    assert "judgment" not in json.dumps(p).lower() and "level" not in p
