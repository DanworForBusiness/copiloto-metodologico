"""judgment-rules/v6 — correcciones de RECONOCIMIENTO sobre v5 (sin cambios de scoring ni de orden de decision):
E2 (hoja MIXED con componentes todos explicitos y >=1 fisico => la Ingesta no queda «sin evidencia») y E4a (nombre heredado de CDS gobernada + etiqueta funcional = convencion).
Incluye las pruebas NEGATIVAS (no generalizar a componentes desconocidos/externos, no crear razones nuevas) y el aislamiento de v5. Sin datos de clientes."""
from __future__ import annotations

import copy

import pytest

from tests._judgment_cases import FROZEN_LABELS, fresh
from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog.methodological_judgment.rules import published_contract_conformance

V5 = dict(ruleset=C.RULESET_V5, ratified_contracts=[])
V6 = dict(ruleset=C.RULESET_V6, ratified_contracts=[])
PHYS, CDS, EXT, UNK = "PHYSICAL_ABAP_TABLE_BOUNDARY", "GOVERNED_SAP_CDS_CONTRACT", "EXTERNAL_CONTRACT_UNDETERMINED", "UNKNOWN"


def _key(j):
    return (j["status"], j["level"], j["level_rule"], j["closure"], sorted((r["reason_id"], r["classification"], r["state"]) for r in j["reasons"]),
            sorted(s["strength_id"] for s in j["strengths"]))


def _source_gap(j):
    return next((d for d in (j.get("not_determinable_detail") or {}).get("missing_strength_dimensions", ()) if d["dimension"] == C.SOURCE_INGESTION), None)


def _mixed(components, *, extra_external=False):
    """Producto sobre una hoja MIXED (componentes dados) + una CDS gobernada; contrato publicado sin fortaleza => el Juicio cae en LVL-06 y expone la causa de SOURCE."""
    b = PB().view("V1", ["MIX", "CDS1"], joins=1).remote("MIX").leaf_class("MIX", "MIXED_BOUNDARY_CLASSES").remote("CDS1").product("V1").measure("M", enriched=False)
    if extra_external:
        b.view("V1", ["MIX", "CDS1", "EXT"], joins=2).remote("EXT", external=True)
    b.trace("M", "CDS1")
    p = b.build()
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf"] == "MIX":
            r["boundary_classes"] = list(components)
    return p


# ───────────── E2: positivo ─────────────
def test_v6_an_explicit_mixed_leaf_with_a_physical_component_is_known_not_all_governed():
    p = _mixed([PHYS, CDS])
    g5, g6 = _source_gap(evaluate_judgment(copy.deepcopy(p), **V5)), _source_gap(evaluate_judgment(copy.deepcopy(p), **V6))
    assert (g5["state"], g5["cause"]) == ("INSUFFICIENT_EVIDENCE", "SOURCE_CONTRACT_CLASS_UNDETERMINED")
    assert (g6["state"], g6["cause"]) == ("NO_STRENGTH", "SOURCE_CONTACT_NOT_ALL_GOVERNED")


def test_v6_e2_never_changes_the_emitted_reasons_strengths_or_reviews():
    p = _mixed([PHYS, CDS])
    j5, j6 = evaluate_judgment(copy.deepcopy(p), **V5), evaluate_judgment(copy.deepcopy(p), **V6)
    assert _key(j5)[4:] == _key(j6)[4:]
    assert sorted(r["review_id"] for r in j5["review_candidates"]) == sorted(r["review_id"] for r in j6["review_candidates"])
    assert not any(r["classification"] == C.MATERIAL_DEVIATION for r in j6["reasons"])


# ───────────── E2: negativos (no generalizar) ─────────────
@pytest.mark.parametrize("components", [
    [PHYS, EXT],                 # componente externo => indeterminacion REAL
    [PHYS, UNK],                 # componente desconocido
    [CDS, EXT],                  # sin fisico y con externo
    [],                          # sin componentes declarados
    ["SOME_FUTURE_CLASS", PHYS],  # clase no reconocida
])
def test_v6_mixed_leaves_with_unknown_external_or_missing_components_stay_undetermined(components):
    g6 = _source_gap(evaluate_judgment(_mixed(components), **V6))
    assert (g6["state"], g6["cause"]) == ("INSUFFICIENT_EVIDENCE", "SOURCE_CONTRACT_CLASS_UNDETERMINED")


def test_v6_another_genuinely_undetermined_leaf_keeps_the_source_dimension_without_evidence():
    g6 = _source_gap(evaluate_judgment(_mixed([PHYS, CDS], extra_external=True), **V6))
    assert (g6["state"], g6["cause"]) == ("INSUFFICIENT_EVIDENCE", "SOURCE_CONTRACT_CLASS_UNDETERMINED")


def test_v6_an_all_governed_mixed_leaf_is_governed_exactly_as_in_v5():
    p = _mixed([CDS, "GOVERNED_SAP_ODP_SAPI_CONTRACT"])
    j5, j6 = evaluate_judgment(copy.deepcopy(p), **V5), evaluate_judgment(copy.deepcopy(p), **V6)
    assert _key(j5) == _key(j6) and "SRC-STR-01" in {s["rule_id"] for s in j6["strengths"]}


# ───────────── aislamiento: v5 intacto, v6 disponible e inactivo ─────────────
@pytest.mark.parametrize("alias", sorted(FROZEN_LABELS))
def test_v6_keeps_every_frozen_reference_case_identical_to_v5(alias):
    j5, j6 = evaluate_judgment(fresh(alias), **V5), evaluate_judgment(fresh(alias), **V6)
    assert _key(j5) == _key(j6) and j6["ruleset_version"] == C.RULESET_V6 and j5["ruleset_version"] == C.RULESET_V5


def test_v6_is_available_but_not_the_current_ruleset():
    assert C.RULESET_V6 in C.RULESETS and C.CURRENT_RULESET_VERSION != C.RULESET_V6


# ───────────── E4a: nombres heredados de CDS gobernadas con etiqueta funcional ─────────────
def _pub(*, src_class=CDS, label="Documento de facturacion", derived=False, normalized=False, technical=False, srcs=("SRC",)):
    """Contrato publicado con una medida con identidad de negocio (semantica propia) + un atributo que conserva el nombre de la columna de ``SRC``."""
    b = (PB().view("V1", ["SRC"]).remote("SRC").leaf_class("SRC", src_class).product("V1").measure("M")
         .trace("M", "SRC", character="IDENTITY_OR_RENAME", label="DIFFERENT_FROM_LEAF", name_equals=False))
    p = b.build()
    p["canonical_evidence"]["published_contract"]["fields"]["fields"].append(
        {"technical_name": "BILLINGDOCUMENT", "label": label, "role": "ATTRIBUTE", "business_name_equals_technical": technical, "label_matches_technical_normalized": normalized,
         "name_equals_remote_source_column": True, "derived": derived, "remote_source_artifacts": list(srcs)})
    p["canonical_evidence"]["published_field_lineage"]["fields"].append(
        {"technical_name": "BILLINGDOCUMENT", "label": label, "role": "ATTRIBUTE", "completeness": "COMPLETE", "frontier_leaves": [
            {"leaf": "SRC", "leaf_field": "BILLINGDOCUMENT", "source_contract_class": src_class, "paths": 1,
             "path_profiles": [{"character": "IDENTITY_ONLY", "through_harmonization": False, "paths": 1}],
             "vocabulary": {"name_equals_leaf_field": True, "label_relation": "EQUALS_LEAF_LABEL"}}]})
    return p


def _conds(j):
    return {m["dimension"]: m.get("unmet_conditions") for m in (j.get("not_determinable_detail") or {}).get("missing_strength_dimensions", ())}


def test_v6_a_governed_cds_name_with_a_functional_label_is_a_valid_convention():
    p = _pub()
    j5, j6 = evaluate_judgment(copy.deepcopy(p), **V5), evaluate_judgment(copy.deepcopy(p), **V6)
    assert "PUB-STR-01" not in {s["rule_id"] for s in j5["strengths"]}
    assert "PUB-STR-01" in {s["rule_id"] for s in j6["strengths"]}


@pytest.mark.parametrize("variant", [
    dict(src_class=PHYS),                              # tabla fisica ABAP: el nombre de columna cruda SIGUE contando
    dict(src_class="GOVERNED_SAP_ODP_SAPI_CONTRACT"),  # fuera del alcance autorizado (solo CDS)
    dict(src_class=EXT),                               # fuente no acreditada
    dict(src_class="MIXED_BOUNDARY_CLASSES"),
    dict(label=""),                                    # sin etiqueta funcional
    dict(technical=True),                              # etiqueta = nombre tecnico
    dict(normalized=True),                             # etiqueta = nombre tecnico normalizado
    dict(derived=True),                                # campo/medida calculada
    dict(srcs=()),                                     # fuente remota no identificada
    dict(srcs=("SRC", "OTHER")),                       # una de las fuentes no es una CDS gobernada conocida
])
def test_v6_the_convention_never_extends_beyond_governed_cds_with_an_accredited_label(variant):
    p = _pub(**variant)
    j5, j6 = evaluate_judgment(copy.deepcopy(p), **V5), evaluate_judgment(copy.deepcopy(p), **V6)
    assert "PUB-STR-01" not in {s["rule_id"] for s in j6["strengths"]}
    assert _key(j5)[:4] == _key(j6)[:4]


def test_v6_calculated_measures_without_business_identity_still_block_the_published_contract():
    p = _pub()
    p["canonical_evidence"]["published_contract"]["fields"]["fields"].append(
        {"technical_name": "RM0", "label": "RM0", "role": "MEASURE", "business_name_equals_technical": True, "label_matches_technical_normalized": True,
         "name_equals_remote_source_column": False, "derived": True, "remote_source_artifacts": []})
    j6 = evaluate_judgment(copy.deepcopy(p), **V6)
    assert "PUB-STR-01" not in {s["rule_id"] for s in j6["strengths"]}
    conds = published_contract_conformance(ProfileEvidence(p, None), "UNDETERMINED", True)["conditions"]
    assert conds["no_source_column_names"] is True and conds["measures_business_identity"] is False
