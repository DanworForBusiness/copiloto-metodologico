"""
confidence-cuts/v3 — el factor `resolved_provenance` mide el ORIGEN DE LOS DATOS, no la identidad del nombre del artefacto.

Causa que corrige: una hoja local (tabla/estructura sin remoto ni query) tiene identidad UNKNOWN (``identity-rules/v1`` solo lee atributos de la entidad) aunque un Replication Flow demuestre su origen. Ese UNKNOWN de
NOMBRE se contaba como «procedencia no resuelta» y bajaba la confianza (Kensing: LOW/MEDIUM por la proporción de tablas de aterrizaje del cierre). Ahora una hoja UNKNOWN cuenta como resuelta SOLO si el hecho
`source.contract_class` (el que usa el Juicio) la da como ``LOCAL_FLOW_PRODUCED`` con clase de contrato resuelta. Siguen penalizando: declaradas, no observables/sin productor, no evaluadas y cadena incompleta.
Fixtures sintéticas; sin motor completo (hechos inyectados en la composición pura).
"""
from __future__ import annotations

import dataclasses

import pytest

from tests.test_modeling_assessment_assessability import _factor, _local_chain, all_facts, compose
from tools.catalog.modeling_assessment import assessability as A
from tools.catalog.modeling_assessment import facts_field_lineage as FL
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import profile as P
from tools.catalog.modeling_assessment.facts_support import make_fact

LEAF = "LOC_C"                                          # hoja local de identidad UNKNOWN del patrón `_local_chain` (1 de 4 artefactos del cierre)


def _contract_class(leaf_kind, contract_class, basis, *, assessability=m.Assessability.ASSESSED, cause=None):
    return make_fact("source.contract_class", m.Area.SOURCE_EXTRACTION, LEAF,
                     {"leaf": LEAF, "leaf_kind": leaf_kind, "contract_class": contract_class, "classification_basis": basis, "classification_bases": [basis], "boundary_classes": [],
                      "source_systems": [], "remote_entities": [], "remote_connection": None, "definition": "test"},
                     m.EvidenceClass.B, assessability=assessability, cause=cause)


def _compose_with(fact):
    ents = _local_chain()
    facts = [*all_facts(ents, "AM_C", None), *([fact] if fact is not None else [])]
    return compose(ents, "AM_C", facts=facts)


def test_literals_used_by_the_pure_module_match_their_source_of_truth():
    assert A._LEAF_FLOW_PRODUCED in FL.LEAF_KINDS and A._LEAF_FLOW_PRODUCED == "LOCAL_FLOW_PRODUCED"
    assert A._CONTRACT_CLASS_UNKNOWN == FL.SCC_UNKNOWN


def test_confidence_ruleset_is_v3():
    assert A.CONFIDENCE_RULESET == "confidence-cuts/v3"


def test_baseline_an_unknown_identity_leaf_without_demonstrated_origin_still_lowers_the_share():
    f = _factor(_compose_with(None), "resolved_provenance")
    assert f.value == 0.75 and f.level is m.ConfidenceLevel.MEDIUM                     # 1 UNKNOWN de 4: el comportamiento histórico para una hoja sin evidencia de productor


def test_a_leaf_whose_origin_is_demonstrated_by_a_producer_counts_as_resolved():
    comp = _compose_with(_contract_class("LOCAL_FLOW_PRODUCED", "GOVERNED_SAP_CDS_CONTRACT", "RF_SOURCE_CONTAINER"))
    f = _factor(comp, "resolved_provenance")
    assert f.value == 1.0 and f.level is m.ConfidenceLevel.HIGH and f.contributes_to_confidence
    assert "1 UNKNOWN-identity leaves whose origin is demonstrated" in f.detail
    assert comp.confidence.level is m.ConfidenceLevel.HIGH                               # los demás factores del patrón son 1.0


@pytest.mark.parametrize("leaf_kind,contract_class,basis", [
    ("LOCAL_DECLARED_SOURCE", "EXTERNAL_CONTRACT_UNDETERMINED", "EXPLICIT_SOURCE_METADATA"),     # declarada: NO se promueve a establecida (decisión de owner, Gate 4)
    ("LOCAL_NO_PRODUCER", "UNKNOWN", "NO_PRODUCER_OBSERVED"),                                       # no observable / sin productor
    ("LOCAL_PROVENANCE_NOT_ASSESSED", "UNKNOWN", "PROVENANCE_NOT_ASSESSED"),                        # no evaluada
    ("LOCAL_FLOW_PRODUCED", "UNKNOWN", "SOURCE_CHAIN_INCOMPLETE"),                                  # productor conocido pero cadena/clase sin resolver
    ("REMOTE_IMPORTED", "GOVERNED_SAP_CDS_CONTRACT", "REMOTE_ENTITY_NAMESPACE"),                    # no es una hoja local producida por flujo: fuera del alcance de esta regla
])
def test_declared_unobservable_unassessed_and_incomplete_chains_keep_penalizing(leaf_kind, contract_class, basis):
    f = _factor(_compose_with(_contract_class(leaf_kind, contract_class, basis)), "resolved_provenance")
    assert f.value == 0.75 and "count as resolved" not in f.detail


def test_a_contract_class_fact_that_is_not_usable_does_not_exempt_the_leaf():
    fact = _contract_class("LOCAL_FLOW_PRODUCED", "GOVERNED_SAP_CDS_CONTRACT", "RF_SOURCE_CONTAINER", assessability=m.Assessability.NOT_ASSESSED,
                           cause=m.Cause.NOT_IN_SNAPSHOT)
    assert _factor(_compose_with(fact), "resolved_provenance").value == 0.75


def test_the_exemption_is_per_leaf_and_never_by_name():
    """Un hecho de otra hoja (otro nombre) no exime a la hoja UNKNOWN: la evidencia es por sujeto, no por patrón de nombre."""
    other = dataclasses.replace(_contract_class("LOCAL_FLOW_PRODUCED", "GOVERNED_SAP_CDS_CONTRACT", "RF_SOURCE_CONTAINER"), subject="1TR_OTRA")
    assert _factor(_compose_with(other), "resolved_provenance").value == 0.75


def test_the_other_confidence_factors_and_coverage_are_unaffected_by_the_origin_evidence():
    base = _compose_with(None)
    promoted = _compose_with(_contract_class("LOCAL_FLOW_PRODUCED", "GOVERNED_SAP_CDS_CONTRACT", "RF_SOURCE_CONTAINER"))
    names = ("non_heuristic_identity", "complete_rule_inputs", "unambiguous_closure_references", "strict_coverage", "inclusive_coverage")
    for n in names:
        a = next((f for f in base.confidence_factors if f.name == n), None)
        b = next((f for f in promoted.confidence_factors if f.name == n), None)
        assert (a is None) == (b is None) and (a is None or (a.value, a.level) == (b.value, b.level)), n
    assert base.coverage == promoted.coverage                                           # la cobertura no cambia
    assert [s.surface_id for s in base.surfaces] == [s.surface_id for s in promoted.surfaces]


def test_explanatory_texts_distinguish_artifact_identity_from_data_origin():
    assert "origen de los datos" in P._COMPONENT_LIMITATION["resolved_provenance"]
    assert "origen de datos" in P._COMPONENT_TEXT["resolved_provenance"] and "identidad" in P._COMPONENT_TEXT["resolved_provenance"]
    assert "procedencia" not in P._COMPONENT_TEXT["resolved_provenance"].lower() and "procedencia" not in P._COMPONENT_LIMITATION["resolved_provenance"].lower()


def test_a_1tr_named_landing_table_without_producer_evidence_is_never_exempt_by_its_name():
    """Sin inferencias por naming: una tabla `1TR_*` UNKNOWN SIN hecho de productor sigue penalizando (el nombre jamás demuestra el origen)."""
    from tests._modeling_assessment_m4_fixtures import f4
    from tests.test_modeling_assessment_graph import ent, pool
    f = lambda: [f4("A", "Alfa"), f4("B", "Beta")]                                      # noqa: E731
    ents = pool(ent("AM_C", up=["VF_C"], cube=True, has_query=True, fields=f()),
                ent("VF_C", up=["VR_C"], pattern="ANALYTICAL_FACT", has_query=True, fields=f()),
                ent("VR_C", up=["1TR_SAP_LOC"], has_query=True, fields=f()),
                ent("1TR_SAP_LOC", repository_type="DWC_LOCAL_TABLE", fields=f()))
    f_ = _factor(compose(ents, "AM_C", facts=all_facts(ents, "AM_C", None)), "resolved_provenance")
    assert f_.value == 0.75 and "count as resolved" not in f_.detail
