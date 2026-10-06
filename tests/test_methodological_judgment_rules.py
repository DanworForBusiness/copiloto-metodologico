"""
Gate 8D.1 — reglas del Juicio sobre perfiles SINTETICOS (sin clientes) y pruebas CONTRAFACTICAS / anti-sobreajuste.

El nivel es funcion de la EXISTENCIA de razones clasificadas (no de conteos ni proporciones); estas pruebas fijan esa propiedad.
Gate 8D.1d: la restauracion/no-restauracion semantica del publicado se establece por LINAJE DE CAMPO (canonical-evidence/v3), no por senales por existencia.
"""
from __future__ import annotations

import copy
import json

import pytest

from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence


def rules(j, cls=None):
    return {r["rule_id"] for r in j["reasons"] if cls is None or r["classification"] == cls}


def reason(j, rule_id):
    return next(r for r in j["reasons"] if r["rule_id"] == rule_id)


def buena_model(*, assoc=True, authored=False) -> PB:
    """Modelo limpio: AM -> V1 (producto) -> H1 (candidato 1:1 tecnico sobre contrato SAP gobernado), asociacion a dimension y campos publicados trazables a la hoja gobernada a
    traves del contrato de armonizacion. ``authored``: ademas, un campo con etiqueta propia (distinta de la de la hoja)."""
    b = PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", sources=["CDS1"]).remote("CDS1").product("V1").measure("M1").flow("RF", delta=True)
    if assoc:
        b.assoc("DIM1")
    if authored:
        b.measure("M_AUTH")
        b.trace("M_AUTH", "CDS1", character="WITH_DERIVATION", through=True, label="DIFFERENT_FROM_LEAF", name_equals=False, via=["H1"])
    return b.trace_all("CDS1", character="IDENTITY_OR_RENAME", through=True, label="EQUALS_LEAF_LABEL", via=["H1"])


# ───────────────────────────── fortalezas y BUENA ─────────────────────────────
def test_buena_requires_affirmative_strength_in_all_four_critical_contracts():
    j = evaluate_judgment(buena_model().build())
    assert (j["status"], j["level"], j["closure"], j["level_rule"]) == ("DETERMINED", "BUENA", "FINAL", "LVL-04")
    assert {s["rule_id"] for s in j["strengths"]} >= {"SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"}
    assert not j["reasons"]


def test_absence_of_findings_is_not_buena():
    """Sin candidato de armonizacion no hay fortaleza de armonizacion: la ausencia de hallazgos NO alcanza."""
    b = PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").assoc("D").measure("M").trace_all("CDS1")
    j = evaluate_judgment(b.build())
    assert not j["reasons"] and j["status"] == "NOT_DETERMINABLE" and j["level"] is None and j["closure"] == "PROVISIONAL"
    assert "HAR-STR-01" not in {s["rule_id"] for s in j["strengths"]}


@pytest.mark.parametrize("missing", ["SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"])
def test_dropping_any_one_critical_strength_prevents_buena(missing):
    b = buena_model()
    if missing == "SRC-STR-01":
        b.remote("TBL", external=True)                           # un contacto con fuente de clase no gobernada
    elif missing == "HAR-STR-01":
        b._cands["H1"]["source_vocabulary"]["fields_with_source_column_name"] = 4
    elif missing == "PRO-STR-01":
        b._views["V1"]["provider_names"].append("CDS9"); b.remote("CDS9")
    elif missing == "PUB-STR-01":
        b._assoc.clear()                                         # una sola familia afirmativa (reuso gobernado) ya no alcanza
    j = evaluate_judgment(b.build())
    assert missing not in {s["rule_id"] for s in j["strengths"]} and j["level"] != "BUENA"


def test_one_to_one_layers_are_not_penalized():
    j = evaluate_judgment(buena_model().build())
    assert j["level"] == "BUENA" and not rules(j, "RELEVANT_WEAKNESS") and not rules(j, "LOCAL_WEAKNESS")


# ───────────────────────────── PUB-STR-01 (8D.1d): sin requisito de asociaciones ─────────────────────────────
def test_pub_str_01_does_not_require_any_association():
    """Cero asociaciones NO es debil por si mismo: con OTRAS familias afirmativas independientes (reuso gobernado trazable + semantica propia) la fortaleza se sostiene."""
    j = evaluate_judgment(buena_model(assoc=False, authored=True).build())
    assert "PUB-STR-01" in {s["rule_id"] for s in j["strengths"]} and j["level"] == "BUENA"
    assert j["supporting_evidence"]["published_business_association_targets"] == []


def test_a_single_association_never_completes_the_published_strength():
    """Una asociacion es evidencia positiva LOCALIZADA: sola (sin otra familia afirmativa independiente) no alcanza, y el numero de asociaciones no es un puntaje."""
    b = PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").measure("M").assoc("D1").assoc("D2").assoc("D3")
    b.trace_all("CDS1", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")                # gobernada, sin armonizacion ni semantica propia: solo la asociacion
    assert "PUB-STR-01" not in {s["rule_id"] for s in evaluate_judgment(b.build())["strengths"]}


def test_adding_or_removing_one_association_cannot_automatically_change_the_level():
    base = evaluate_judgment(buena_model(assoc=True, authored=True).build())
    without = evaluate_judgment(buena_model(assoc=False, authored=True).build())
    assert base["level"] == without["level"] == "BUENA"                                                       # con otra familia afirmativa: +/-1 asociacion no mueve el nivel
    mat = evaluate_judgment(mat_model().build())
    assert evaluate_judgment(mat_model().assoc("D2").build())["level"] == mat["level"] == "PARCIAL"


def test_published_strength_requires_complete_lineage_and_no_unresolved_vocabulary_leak():
    incomplete = buena_model()
    incomplete.incomplete("M1")
    j = evaluate_judgment(incomplete.build())
    assert "PUB-STR-01" not in {s["rule_id"] for s in j["strengths"]} and any(l["key"] == "published_field_lineage_incomplete" for l in j["evidence_limitations"])
    leaky = buena_model(authored=True)
    leaky.remote("TBL", raw=True)
    leaky.trace("M1", "TBL", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")              # un campo hereda sin cambios el vocabulario de una tabla fisica
    assert "PUB-STR-01" not in {s["rule_id"] for s in evaluate_judgment(leaky.build())["strengths"]}


def test_two_projections_of_the_same_fact_are_not_two_affirmative_families():
    from tools.catalog.methodological_judgment.rules import _independent_pair_exists
    assert not _independent_pair_exists({"A": {"field:X"}, "B": {"field:X"}})
    assert _independent_pair_exists({"A": {"field:X"}, "B": {"field:Y"}})
    assert _independent_pair_exists({"A": {"field:X", "field:Y"}, "B": {"field:Y", "dim:D"}})
    assert _independent_pair_exists({"A": {"field:X", "field:Y"}, "B": {"field:Y"}})                             # mismo campo, afirmaciones distintas


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


# ───────────────────────────── LEK-MAT-02 (8D.1d: consecuencia trazable por linaje de campo) ─────────────────────────────
def lek_model(*, raw=True, logic=True, exposure="verbatim", assoc=False, authored_others=0) -> PB:
    """AM -> V1 -> V2 -> TBL (fisica cruda): la cruda llega al producto SIN pasar por un candidato de armonizacion y V1/V2 la compensan con logica.
    ``exposure``: 'verbatim' = un campo publicado hereda sin cambios el vocabulario de TBL por un camino casi-identidad sin armonizacion; 'authored' = el campo tiene etiqueta propia;
    'derived' = el campo se deriva aguas abajo; 'harmonized' = el camino cruza un contrato de armonizacion; 'none' = ningun campo se traza a TBL."""
    b = (PB().view("V1", ["V2"], derived=1 if logic else 0).view("V2", ["TBL"], joins=1 if logic else 0, derived=1 if logic else 0).remote("TBL", raw=raw).remote("CDS1").product("V1"))
    if assoc:
        b.assoc("D")
    b.measure("M")
    for i in range(authored_others):
        b.measure(f"OK{i}")
        b.trace(f"OK{i}", "CDS1", character="WITH_DERIVATION", label="DIFFERENT_FROM_LEAF", name_equals=False)
    if exposure == "verbatim":
        b.trace("M", "TBL", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    elif exposure == "authored":
        b.trace("M", "TBL", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False)
    elif exposure == "derived":
        b.trace("M", "TBL", character="WITH_DERIVATION", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False)
    elif exposure == "harmonized":
        b.trace("M", "TBL", character="IDENTITY_ONLY", through=True, label="EQUALS_LEAF_LABEL")
    else:
        b.trace("M", "CDS1", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    return b


def test_raw_bypass_leak_with_a_traceable_published_path_is_relevant_not_material():
    """Una fuga cruda/bypass con camino publicado trazable, pero SIN centralidad estructural de contrato (no hay L1(b): la misma responsabilidad
    reutilizable no se crea/posee en varios nodos aguas abajo), NO es MATERIAL por si sola: es una debilidad RELEVANTE del contrato publicado."""
    j = evaluate_judgment(lek_model().build())
    assert "LEK-MAT-02" not in rules(j) and j["level"] != "PARCIAL"                                   # la cruda trazable sola NO es MATERIAL
    r = reason(j, "LEK-REL-01")
    assert r["classification"] == "RELEVANT_WEAKNESS" and r["dimension"] == "PUBLISHED_CONTRACT"
    assert {"LEK.L1.a_physical_raw_bypassing_harmonization_compensated_downstream", "LEK.L2.absorbed_by_non_candidate_nodes_with_logic",
            "LEK.L3.unresolved_boundary_vocabulary_reaches_the_consumer_verbatim_through_traceable_paths",
            "LEK.no_structural_contract_centrality_reusable_responsibility_not_owned_in_multiple_downstream_nodes"} <= set(r["deterministic_basis"])
    assert "ce:published_field_lineage/M" in r["evidence_refs"]                                       # cita el campo publicado que sostiene la consecuencia


@pytest.mark.parametrize("exposure", ["authored", "derived", "harmonized", "none"])
def test_without_a_verbatim_unharmonized_path_from_the_leaked_boundary_there_is_no_material(exposure):
    """Restauracion/ausencia de trazabilidad: etiqueta propia, derivacion genuina, paso por un contrato de armonizacion o ningun campo trazable a la frontera NO son fuga al consumidor."""
    j = evaluate_judgment(lek_model(exposure=exposure).build())
    assert "LEK-MAT-02" not in rules(j) and j["level"] != "PARCIAL"
    assert "SRC-REL-01" in rules(j, "RELEVANT_WEAKNESS")                                               # la cruda omitida y compensada sigue siendo RELEVANT (8D.1b), no MATERIAL


def test_unavailable_lineage_never_creates_material():
    b = lek_model()
    b._traces.clear()                                                                                  # ningun campo con linaje: la restauracion no se puede determinar (evaluabilidad)
    j = evaluate_judgment(b.build())
    assert "LEK-MAT-02" not in rules(j) and any(l["key"] == "published_field_lineage_incomplete" for l in j["evidence_limitations"])


def test_one_enriched_or_authored_field_cannot_establish_restoration():
    """Muchos campos con semantica propia NO borran la fuga cruda mientras exista un camino trazable que expone el vocabulario no resuelto
    (sin conteos ni proporciones): sigue siendo una debilidad RELEVANTE del contrato publicado (ya no MATERIAL: no hay centralidad estructural)."""
    j = evaluate_judgment(lek_model(authored_others=9).build())
    assert "LEK-MAT-02" not in rules(j) and "LEK-REL-01" in rules(j, "RELEVANT_WEAKNESS")


def test_one_non_enriched_field_alone_cannot_establish_non_restoration_without_a_traceable_leak():
    """Un campo con etiqueta tecnica que viene de una hoja GOBERNADA (no de la frontera cruda compensada) no es evidencia de fuga."""
    j = evaluate_judgment(lek_model(exposure="none").build())
    assert "LEK-MAT-02" not in rules(j)


def test_associations_are_irrelevant_to_lek_in_both_directions():
    """Las asociaciones no afectan LEK: ni crean ni vetan. La fuga cruda trazable es RELEVANT con 0/1/3 asociaciones; la restauracion (etiqueta
    propia) la quita en todos los casos. La conclusion se decide por trazabilidad y centralidad estructural, nunca por conteo de asociaciones."""
    results = {}
    for n in (0, 1, 3):
        b = lek_model()
        for i in range(n):
            b.assoc(f"D{i}")
        results[n] = evaluate_judgment(b.build())
    assert all("LEK-MAT-02" not in rules(j) and "LEK-REL-01" in rules(j, "RELEVANT_WEAKNESS") for j in results.values())
    clean = {n: evaluate_judgment((lambda b: [b.assoc(f"D{i}") for i in range(n)] and b or b)(lek_model(exposure="authored")).build()) for n in (0, 1, 3)}
    assert all("LEK-MAT-02" not in rules(j) and "LEK-REL-01" not in rules(j) for j in clean.values())  # restaurado: ni MATERIAL ni RELEVANT por LEK


def _und_model(*, exposure="verbatim") -> PB:
    """AM con una hoja EXTERNAL_CONTRACT_UNDETERMINED cuyo vocabulario llega (verbatim) o no al campo publicado."""
    b = PB().view("V1", ["EXT"], derived=1).remote("EXT", external=True).remote("CDS1").product("V1").measure("M")
    if exposure == "verbatim":
        b.trace("M", "EXT", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    else:                                                                                             # etiqueta propia: no hay herencia verbatim
        b.trace("M", "EXT", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False)
    return b


def test_decision_critical_external_undetermined_source_is_not_determinable_but_undetermined_alone_is_not():
    """Una hoja externa de clase INDETERMINADA cuyo vocabulario llega VERBATIM al publicado hace la calidad del contrato publicado
    indeterminable (si cruda, debilidad; si gobernada, reuso): NOT_DETERMINABLE decision-critico. La MISMA hoja indeterminada SIN
    esa exposicion verbatim NO dispara la condicion (indeterminada sola != ND). La tecnologia nunca resuelve la clase."""
    from tools.catalog.methodological_judgment.rules import decision_critical_undetermined_source
    ev_v = ProfileEvidence(_und_model(exposure="verbatim").build())
    ev_a = ProfileEvidence(_und_model(exposure="authored").build())
    assert decision_critical_undetermined_source(ev_v, "UNDETERMINED") and not decision_critical_undetermined_source(ev_a, "UNDETERMINED")
    jv = evaluate_judgment(_und_model(exposure="verbatim").build())
    assert jv["status"] == "NOT_DETERMINABLE" and jv["level"] is None and jv["level_rule"] == "LVL-05"
    assert jv["not_determinable_reason"] == "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"
    ja = evaluate_judgment(_und_model(exposure="authored").build())
    assert ja.get("not_determinable_reason") != "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"           # indeterminada sin exposicion verbatim: no es decision-critica


def test_an_independent_material_rule_keeps_parcial_despite_a_decision_critical_external_source():
    """El paso decision-critico va DESPUES de MATERIAL: si una regla ratificada ya establece PARCIAL, una fuente externa indeterminada no lo
    convierte en NOT_DETERMINABLE (no se degrada un MATERIAL ya probado)."""
    from tools.catalog.methodological_judgment.engine import _decide
    reasons = [{"classification": "MATERIAL_DEVIATION", "state": "OPEN_CANDIDATE", "rule_id": "PRO-MAT-01"}]
    assert _decide(reasons, [], [], critical_undetermined=True)[:2] == ("DETERMINED", "PARCIAL")


def test_raw_alone_is_never_material():
    j = evaluate_judgment(lek_model(logic=False).build())
    assert "LEK-MAT-02" not in rules(j, "MATERIAL_DEVIATION") and j["level"] != "PARCIAL"


def test_src_rel_01_needs_bypass_of_the_reusable_boundary_and_compensation_not_just_raw_in_spine():
    bypass = evaluate_judgment(lek_model(exposure="authored", assoc=True).build())
    assert "SRC-REL-01" in rules(bypass, "RELEVANT_WEAKNESS") and bypass["level"] == "ADECUADA" and "LEK-MAT-02" not in rules(bypass)
    # misma cruda en la espina, pero el camino al producto ATRAVIESA un candidato de armonizacion: evidencia de fuente, no debilidad de nivel
    b = (PB().view("V1", ["H1"], derived=1).view("H1", ["TBL"], joins=1).cand("H1", character="SEMANTIC_TRANSFORMATION_PRESENT", sources=["TBL"]).remote("TBL", raw=True)
         .product("V1").assoc("D").measure("M").trace_all("TBL", through=True))
    j = evaluate_judgment(b.build())
    assert "SRC-REL-01" not in rules(j) and "SRC-LOC-01" in rules(j, "LOCAL_WEAKNESS") and not rules(j, "RELEVANT_WEAKNESS")
    # cruda en la espina sin logica de compensacion: tampoco
    nocomp = evaluate_judgment(lek_model(logic=False, exposure="authored").build())
    assert "SRC-REL-01" not in rules(nocomp)


def test_physical_abap_table_is_not_automatically_bad():
    j = evaluate_judgment(lek_model(logic=False, exposure="authored", assoc=True).build())
    assert not rules(j, "MATERIAL_DEVIATION") and not rules(j, "RELEVANT_WEAKNESS")


def l1b_model(*, technical_boundary=True, two_nodes=True, calendar_names=False, exposed=True) -> PB:
    """Semantica TEMPORAL reutilizada aguas abajo. La frontera de armonizacion aguas arriba es tecnica (o no existe). ``exposed``: algun campo publicado atraviesa los sitios con derivacion."""
    mid = ["H1"] if technical_boundary else ["CDS1"]
    b = PB().view("V1", ["V2", "V3"], joins=1).view("V2", mid, derived=1).view("V3", mid, derived=1).remote("CDS1").product("V1").measure("M")
    if technical_boundary:
        b.view("H1", ["CDS1"]).cand("H1", carry=3, sources=["CDS1"])
    b.temporal_reuse("V2", "V3") if two_nodes else b.temporal_reuse("V2", "V2")
    if calendar_names:
        b.measure("Mes", ), b.measure("Month"), b.measure("Calendar")
    if exposed:
        b.trace("M", "CDS1", character="WITH_DERIVATION", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False, via=["V2", "V3"])
    else:
        b.trace("M", "CDS1", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL", via=["V1"])
    return b.trace_all("CDS1", through=True, via=["H1"] if technical_boundary else [])


def test_l1b_requires_a_technical_boundary_upstream_downstream_ownership_in_multiple_nodes_and_a_traceable_published_path():
    j = evaluate_judgment(l1b_model().build())
    r = reason(j, "LEK-MAT-02")
    assert "LEK.L1.b_reusable_semantics_created_downstream_over_technical_boundary" in r["deterministic_basis"] and j["level"] == "PARCIAL"
    assert "LEK.L3.downstream_created_semantics_reaches_the_consumer_through_traceable_paths" in r["deterministic_basis"]
    # sin frontera de armonizacion tecnica aguas arriba (A no demostrada): revision, sin efecto en nivel
    nob = evaluate_judgment(l1b_model(technical_boundary=False).build())
    assert "LEK-MAT-02" not in rules(nob)
    rv = [x for x in nob["review_candidates"] if x["review_id"] == "REUSED_TEMPORAL_DERIVATION_REVIEW"]
    assert rv and "A_boundary_demonstrably_technical" in rv[0]["missing_for_material"] and rv[0]["level_effect"] == "NONE"
    assert "LEK-MAT-02" not in rules(evaluate_judgment(l1b_model(two_nodes=False).build()))              # un solo nodo (B no demostrada)
    assert "LEK-MAT-02" not in rules(evaluate_judgment(l1b_model(exposed=False).build()))                # ningun campo publicado atraviesa los sitios: sin consecuencia trazable


def test_calendar_like_field_names_alone_never_create_material():
    """Nombres como Mes/Month/Calendar no son evidencia: sin la convergencia estructural A+B no hay MATERIAL, y con ella no agregan ni quitan nada."""
    j = evaluate_judgment(l1b_model(technical_boundary=False, two_nodes=False, calendar_names=True).build())
    assert not rules(j, "MATERIAL_DEVIATION")
    with_names = evaluate_judgment(l1b_model(calendar_names=True).build())
    without = evaluate_judgment(l1b_model().build())
    assert with_names["level"] == without["level"] and rules(with_names, "MATERIAL_DEVIATION") == rules(without, "MATERIAL_DEVIATION")


def test_a_candidate_that_owns_the_temporal_semantics_is_not_a_leak():
    b = l1b_model()
    b._cands["H1"]["character"] = {"value": "SEMANTIC_TRANSFORMATION_PRESENT"}
    b._reuse.clear(); b.temporal_reuse("H1", "V3")
    assert "LEK-MAT-02" not in rules(evaluate_judgment(b.build()))


# ───────────────────────────── HAR-REL-01 (8D.1b / 8D.1d) ─────────────────────────────
def har_model(*, passthrough=False, align=False, carry=4, authored=False) -> PB:
    """Armonizacion tecnica que conserva el vocabulario de la fuente; el producto la consume. ``passthrough``: el vocabulario atraviesa el contrato tecnico y llega SIN CAMBIOS a un campo
    publicado (consecuencia observable por linaje)."""
    b = PB().view("V1", ["H1"], derived=1 if align else 0, unions=1 if align else 0).view("H1", ["CDS1"]).cand("H1", carry=carry, sources=["CDS1"]).remote("CDS1").product("V1").measure("M")
    if passthrough:
        b.trace("M", "CDS1", character="IDENTITY_ONLY", through=True, label="EQUALS_LEAF_LABEL", via=["H1"])
    elif authored:
        b.trace("M", "CDS1", character="IDENTITY_OR_RENAME", through=True, label="DIFFERENT_FROM_LEAF", name_equals=False, via=["H1"])
    else:
        b.trace("M", "CDS1", character="WITH_DERIVATION", through=True, label="DIFFERENT_FROM_LEAF", name_equals=False, via=["H1"])
    return b


def test_technical_harmonization_alone_is_descriptive_not_a_weakness():
    """Armonizacion tecnica con vocabulario fuente, SIN consecuencia semantica observable (el consumidor recibe semantica propia): no es debilidad ni crea ADECUADA."""
    j = evaluate_judgment(har_model(authored=True).build())
    assert "HAR-REL-01" not in rules(j) and j["level"] is None and j["status"] == "NOT_DETERMINABLE"
    assert j["supporting_evidence"]["technical_harmonization_candidates"] == ["H1"]


def test_har_rel_01_fires_when_technical_vocabulary_passes_through_the_harmonization_boundary_to_the_consumer():
    j = evaluate_judgment(har_model(passthrough=True).build())
    assert "HAR-REL-01" in rules(j, "RELEVANT_WEAKNESS") and j["level"] == "ADECUADA" and j["level_rule"] == "LVL-03"
    assert "HAR.consequence.technical_vocabulary_passes_through_the_harmonization_boundary_to_the_consumer" in reason(j, "HAR-REL-01")["deterministic_basis"]
    aligned = evaluate_judgment(har_model(align=True, authored=True).build())
    assert "HAR-REL-01" in rules(aligned, "RELEVANT_WEAKNESS") and "HAR.consequence.propagation_completes_branch_alignment" in reason(aligned, "HAR-REL-01")["deterministic_basis"]


def test_har_rel_01_ignores_associations_in_both_directions():
    base = evaluate_judgment(har_model(passthrough=True).build())
    many = evaluate_judgment(har_model(passthrough=True).assoc("D1").assoc("D2").build())
    clean = evaluate_judgment(har_model(authored=True).assoc("D1").build())
    assert "HAR-REL-01" in rules(base) and "HAR-REL-01" in rules(many) and "HAR-REL-01" not in rules(clean)


def test_one_non_enriched_field_alone_cannot_create_a_relevant_weakness():
    """Un campo con etiqueta tecnica que NO atraviesa la armonizacion tecnica (viene de otra hoja gobernada) no es consecuencia de ella."""
    b = har_model(authored=True)
    b.measure("OTHER")
    b.remote("CDS2").trace("OTHER", "CDS2", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    j = evaluate_judgment(b.build())
    assert not rules(j, "RELEVANT_WEAKNESS") and not rules(j, "MATERIAL_DEVIATION")


def test_har_rel_01_not_triggered_if_one_candidate_enriches():
    b = (PB().view("V1", ["H1", "H2"], joins=1).view("H1", ["C1"]).view("H2", ["C2"]).cand("H1", carry=4, sources=["C1"]).cand("H2", character="SEMANTIC_TRANSFORMATION_PRESENT", sources=["C2"])
         .remote("C1").remote("C2").product("V1").measure("M").trace_all("C1", through=True))
    assert "HAR-REL-01" not in rules(evaluate_judgment(b.build()))


def test_technical_one_to_one_harmonization_over_a_governed_contract_may_remain_buena():
    """El patron Kensing: capa 1:1 sobre contrato gobernado que conserva vocabulario de negocio (sin arrastre de nombres de fuente): la heredad verbatim de una hoja GOBERNADA no es fuga."""
    j = evaluate_judgment(buena_model().build())
    assert j["level"] == "BUENA" and j["closure"] == "FINAL" and "HAR-STR-01" in {x["rule_id"] for x in j["strengths"]}


# ───────────────────────────── RELEVANT / LOCAL ─────────────────────────────









def test_external_contract_undetermined_never_creates_material():
    """Fuente no SAP (clase EXTERNAL_CONTRACT_UNDETERMINED): ni MATERIAL ni alteracion mecanica de nivel."""
    b = PB().view("V1", ["E1", "H1"], joins=1).cand("H1").remote("H1x").remote("E1", external=True).product("V1").assoc("D").measure("M").trace_all("E1")
    j = evaluate_judgment(b.build())
    assert not rules(j, "MATERIAL_DEVIATION")


@pytest.mark.parametrize("policy", ["UNDETERMINED", "PHYSICAL_RAW", "GOVERNED"])
def test_external_policy_is_a_sensitivity_input_only_and_default_never_materializes(policy):
    b = PB().view("V1", ["E1", "E2"], joins=1).remote("E1", external=True).remote("E2", external=True).product("V1").assoc("D").measure("M").trace_all("E1")
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
        b._traces.clear()
        return b.trace_all("CDS1", character="IDENTITY_OR_RENAME", through=True, label="EQUALS_LEAF_LABEL", via=["H1"])
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


def test_unjudgeable_when_assessment_is_not_assessed_or_has_no_v3_evidence():
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
