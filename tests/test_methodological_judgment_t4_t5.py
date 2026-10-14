"""T4/T5 — variantes EXPERIMENTALES del Juicio (opt-in): no cambian la politica vigente, no quitan fortalezas, no inventan razones y su efecto es medible por ``ruleset_options``.

T4_HAR: HAR-STR-01 reconoce una frontera reutilizable 1:1 sobre contrato gobernado que preserva el vocabulario por ETIQUETA aunque conserve nombres tecnicos de columna.
T5_LOCAL_PHYSICAL: una tabla local con clase «tabla ABAP fisica» Y procedencia comprobada por Data Flow participa de SRC-LOC-01 (razon LOCAL: sin efecto en el nivel)."""
from __future__ import annotations

import copy

import pytest

from tests._judgment_profiles import PB
from tests.test_methodological_judgment_rules import buena_model, har_model, reason, rules
from tools.catalog.methodological_judgment import RULESET_VERSION, evaluate_judgment
from tools.catalog.methodological_judgment import contract as C

T4, T5 = ("T4_HAR",), ("T5_LOCAL_PHYSICAL",)
ALL = (T4[0], T5[0])


def strengths(j):
    return {s["rule_id"] for s in j["strengths"]}


def test_default_policy_is_untouched_and_variants_are_recorded_in_the_output():
    p = har_model(authored=True).build()
    base = evaluate_judgment(p)
    assert evaluate_judgment(p, variants=()) == base and "ruleset_options" not in base and base["ruleset_version"] == RULESET_VERSION == "judgment-rules/v2"
    v = evaluate_judgment(p, variants=ALL)
    assert v["ruleset_options"] == {"variants": sorted(ALL)} and v["output_signature"] != base["output_signature"]        # la firma distingue la variante: nunca se confunde con el Juicio vigente
    with pytest.raises(ValueError):
        evaluate_judgment(p, variants=("T9_NOPE",))


def test_t4_recognizes_a_reusable_boundary_that_keeps_source_column_names_but_preserves_business_labels():
    p = har_model(authored=True).build()                                           # candidato 1:1 sobre CDS gobernada, 4 campos con nombre de columna fuente, etiquetas del proveedor
    base, t4 = evaluate_judgment(p), evaluate_judgment(p, variants=T4)
    assert "HAR-STR-01" not in strengths(base) and base["status"] == "NOT_DETERMINABLE" and base["level_rule"] == "LVL-06"
    assert "HAR-STR-01" in strengths(t4) and (t4["status"], t4["level"], t4["level_rule"]) == ("DETERMINED", "BUENA", "LVL-04")      # las otras 3 dimensiones ya tenian fortaleza
    s = next(x for x in t4["strengths"] if x["rule_id"] == "HAR-STR-01")
    assert "HAR.business_vocabulary_preserved_by_provider_labels" in s["deterministic_basis"] and "HAR.consumed_by_downstream_artifact" in s["deterministic_basis"]
    assert evaluate_judgment(p, variants=T4)["reasons"] == base["reasons"]         # no inventa ni quita razones


def test_t4_never_removes_a_strength_the_current_rule_already_grants():
    p = buena_model().build()
    base, t4 = evaluate_judgment(p), evaluate_judgment(p, variants=T4)
    assert strengths(base) <= strengths(t4) and (base["status"], base["level"]) == (t4["status"], t4["level"]) == ("DETERMINED", "BUENA")
    assert next(x for x in base["strengths"] if x["rule_id"] == "HAR-STR-01")["deterministic_basis"] == next(x for x in t4["strengths"] if x["rule_id"] == "HAR-STR-01")["deterministic_basis"]


def test_t4_does_not_grant_by_absence_of_defects_or_when_a_demonstrated_consequence_exists():
    # consecuencia demostrada (vocabulario tecnico que atraviesa al consumidor / alineacion de ramas): HAR-REL-01 sigue siendo la razon y NO hay fortaleza
    for kw in ({"passthrough": True}, {"align": True, "authored": True}):
        j = evaluate_judgment(har_model(**kw).build(), variants=T4)
        assert "HAR-STR-01" not in strengths(j) and "HAR-REL-01" in rules(j) and j["level"] == "ADECUADA"
    # etiquetas que NO son las del proveedor gobernado (enriquecimiento propio): no es esta fortaleza
    enriched = PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", carry=4, sources=["CDS1"], label_diff=2).remote("CDS1").product("V1").measure("M")
    assert "HAR-STR-01" not in strengths(evaluate_judgment(enriched.build(), variants=T4))
    # fuente no gobernada: sin contrato gobernado no hay frontera legitima
    ext = PB().view("V1", ["H1"]).view("H1", ["EXT1"]).cand("H1", carry=4, sources=["EXT1"]).remote("EXT1", external=True).product("V1").measure("M")
    assert "HAR-STR-01" not in strengths(evaluate_judgment(ext.build(), variants=T4))
    # sin candidatos de armonizacion no hay nada que reconocer
    assert "HAR-STR-01" not in strengths(evaluate_judgment(PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").measure("M").build(), variants=T4))


def _with_local_leaf(basis, boundary="PHYSICAL_TABLE", state="ESTABLISHED"):
    b = buena_model().local_leaf("LT1", state=state, boundary=boundary)
    p = b.build()
    for rec in p["canonical_evidence"]["leaf_provenance"]:
        if rec["leaf"] == "LT1":
            rec["basis"] = basis
    return p


def test_t5_brings_a_data_flow_materialized_physical_table_into_the_reasons_without_touching_the_level():
    p = _with_local_leaf("DATA_FLOW")
    base, t5 = evaluate_judgment(p), evaluate_judgment(p, variants=T5)
    assert "SRC-LOC-01" not in rules(base) and "SRC-LOC-01" in rules(t5, "LOCAL_WEAKNESS")
    r = reason(t5, "SRC-LOC-01")
    assert "LT1" in r["affected_artifacts"] and "SRC.local_table_materialized_from_physical_abap_table_by_established_data_flow" in r["deterministic_basis"]
    assert (t5["status"], t5["level"], t5["level_rule"]) == (base["status"], base["level"], base["level_rule"])          # LOCAL: nunca cambia el nivel
    assert strengths(t5) == strengths(base) or strengths(t5) < strengths(base)                                         # puede impedir SRC-STR-01 (ya lo impedia el contacto crudo), nunca agrega


def test_t5_does_not_treat_every_local_table_as_a_raw_boundary_and_never_deduces_by_name():
    for basis, boundary, state in (("REPLICATION_FLOW", "PHYSICAL_TABLE", "ESTABLISHED"),      # replicada por RF: no es materializacion de una tabla fisica por Data Flow
                                   ("DATA_FLOW", "GOVERNED_CDS_CONTRACT", "ESTABLISHED"),      # transformada pero de contrato gobernado
                                   ("DATA_FLOW", "PHYSICAL_TABLE", None),                      # sin procedencia comprobada
                                   ("DATA_FLOW", "EXTERNAL", "ESTABLISHED")):                  # clase indeterminada/mixta
        p = _with_local_leaf(basis, boundary, state)
        assert "SRC-LOC-01" not in rules(evaluate_judgment(p, variants=T5)), (basis, boundary, state)
    assert "SRC-LOC-01" not in rules(evaluate_judgment(buena_model().build(), variants=T5))


def test_variants_keep_confidence_coverage_inputs_and_the_profile_untouched():
    p = _with_local_leaf("DATA_FLOW")
    before = copy.deepcopy(p)
    for v in (T4, T5, ALL):
        evaluate_judgment(p, variants=v)
    assert p == before                                                               # solo lectura: confianza, cobertura y evidencia del perfil no se tocan (el Juicio no las calcula)
    assert C.VARIANTS == frozenset(ALL)
