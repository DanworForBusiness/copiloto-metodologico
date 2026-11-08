"""signal-rules/v5 — reconocimiento de materializaciones de Armonizacion y la oportunidad Transformation Flow.

Prueba focalizada a nivel de FactIndex (sin pipeline completo): el caso representativo
`2VR (vista) -> DF -> 2TR (tabla) -> 3VF`. Verifica que:
  * el motivo NAME_LAYER_ABOVE_BOUNDARY_POSITION de ROLE_LAYER_DIVERGENCE se SUPRIME cuando la evidencia acredita
    la materializacion (productor adquirido + fuente directa = vista candidata + proyeccion pura);
  * se CONSERVAN los demas motivos (contradiccion de rol declarado);
  * la supresion se ABSTIENE sin evidencia suficiente;
  * una materializacion MIXED con proyeccion pura (renombrado de etiqueta, sin derivacion) TAMBIEN califica
    (decision metodologica v5: "sin transformacion adicional" = sin derivacion, no TECHNICAL_MATERIALIZATION estricto);
  * aparece la revision TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY con las cautelas acordadas (oportunidad
    sin viabilidad), sin penalizar el Data Flow.
"""
from __future__ import annotations

from tools.catalog.modeling_assessment.facts_support import make_fact
from tools.catalog.modeling_assessment.model import Area, EvidenceClass
from tools.catalog.modeling_assessment.signals import (
    FactIndex,
    _recognized_harmonization_materialization,
    _rule_role_layer_divergence,
    _rule_transformation_flow_opportunity,
)

TABLE, VIEW = "2TR_ANALISIS_VENCIMIENTOS", "2VR_ANALISIS_VENCIMIENTOS"


def _direct_source(*, is_query_view=True, layer2=True, remote=False, source=VIEW):
    return {"source": source, "is_query_view": is_query_view, "layer2_by_section28_name": layer2, "remote": remote}


def _hc_table(*, implementation="TABLE_PRODUCED_BY_FLOW", character="TECHNICAL_MATERIALIZATION",
              pure=True, mapping_det=True, labels_det=True, sources=(VIEW,),
              producer_flows=("DATA_FLOW:DF_ANALISIS_VENCIMIENTOS",), direct_source=...):
    if direct_source is ...:
        direct_source = _direct_source()
    return {"artifact": TABLE, "candidate_basis": ["LAYER_2_BY_SECTION_28_NAME"], "implementation": implementation,
            "producer_flows": list(producer_flows), "sources": list(sources),
            "mapping_counts": {"IDENTITY": 25, "RENAME": 1, "DERIVED": 0},
            "direct_source": direct_source,
            "character": {"value": character, "dimensions": {"pure_projection_only": pure,
                          "mapping_determinable": mapping_det, "labels_determinable": labels_det}}}


def _index(*, table_hc=None, declared_role=None, boundary=True):
    role = declared_role or {"declares_cube": False, "declares_fact": False, "declares_dimension": False}
    facts = [
        make_fact("ingestion.inferred_layer_vs_lineage_depth", Area.INGESTION, "closure",
                  {"artifacts": [{"artifact": TABLE, "inferred_layer": "2", "inferred_layer_basis": "SECTION_28_NAME"}]}, EvidenceClass.B),
        make_fact("ingestion.boundary_artifacts", Area.INGESTION, "closure",
                  {"artifacts": ([{"artifact": TABLE}] if boundary else [])}, EvidenceClass.B),
        make_fact("naming.artifact_name_contract", Area.INGESTION, TABLE, {"declared_role": role}, EvidenceClass.B),
        make_fact("ingestion.first_consumers", Area.INGESTION, TABLE,
                  {"lineage_consumers": [{"artifact": "3VF_ANALISIS_VENCIMIENTO"}], "association_consumers": []}, EvidenceClass.B),
        make_fact("harmonization.contract_character", Area.HARMONIZATION, TABLE, table_hc or _hc_table(), EvidenceClass.B),
    ]
    return FactIndex(facts)


# -- reconocimiento -------------------------------------------------------------
def test_recognition_predicate_holds_for_materialized_table():
    rec = _recognized_harmonization_materialization(_index(), TABLE)
    assert rec is not None
    assert rec["source_view"] == VIEW
    assert rec["character"] == "TECHNICAL_MATERIALIZATION"
    assert rec["has_downstream_consumers"] is True


def test_recognition_holds_for_mixed_with_pure_projection():
    # Decision metodologica v5: un renombrado de etiqueta (MIXED) con proyeccion pura (sin derivacion) SI califica
    # como materializacion sin transformacion adicional. Es el caso real de Biogenesis (2TR character=MIXED).
    rec = _recognized_harmonization_materialization(_index(table_hc=_hc_table(character="MIXED", pure=True)), TABLE)
    assert rec is not None and rec["character"] == "MIXED"


def test_role_layer_divergence_suppressed_when_materialization_recognized():
    # Unico motivo era la posicion de frontera -> al reconocer la materializacion, no hay finding (no mas pregunta generica).
    out = _rule_role_layer_divergence(_index())
    assert out == []


def test_declared_role_contradiction_is_preserved():
    # La tabla declara rol de HECHO (capa 3) != capa 2 del nombre -> ese motivo SE CONSERVA; el de frontera se resuelve.
    role = {"declares_cube": False, "declares_fact": True, "declares_dimension": False}
    out = _rule_role_layer_divergence(_index(declared_role=role))
    assert len(out) == 1
    d = out[0].detail
    assert d["divergence_kinds"] == ["NAME_LAYER_DIFFERS_FROM_DECLARED_ROLE_LAYER"]
    assert d["resolved_by_evidence"] == ["NAME_LAYER_ABOVE_BOUNDARY_POSITION"]


# -- abstencion (incertidumbre real -> se preserva la pregunta) ----------------
def test_abstains_without_acquired_producer():
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(implementation="TABLE_NO_ACQUIRED_PRODUCER", producer_flows=())))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


def test_abstains_when_derivation_present():
    # Hay derivacion -> proyeccion no pura -> no es materializacion "sin transformacion adicional".
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(character="SEMANTIC_TRANSFORMATION_PRESENT", pure=False)))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


def test_abstains_when_mapping_not_determinable():
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(mapping_det=False)))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


def test_abstains_when_source_is_not_a_single_entry():
    # La fuente directa del DF no es unica (varias entradas) -> no se puede atribuir responsabilidad a una vista.
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(sources=(VIEW, "OTRA_FUENTE"))))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


def test_abstains_when_direct_source_not_query_view():
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(direct_source=_direct_source(is_query_view=False))))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


def test_abstains_when_direct_source_not_layer2_candidate():
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(direct_source=_direct_source(layer2=False))))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


def test_abstains_when_direct_source_is_remote():
    out = _rule_role_layer_divergence(_index(table_hc=_hc_table(direct_source=_direct_source(remote=True))))
    assert len(out) == 1 and "NAME_LAYER_ABOVE_BOUNDARY_POSITION" in out[0].detail["divergence_kinds"]


# -- oportunidad Transformation Flow -------------------------------------------
def test_transformation_flow_opportunity_emitted_with_cautions():
    out = _rule_transformation_flow_opportunity(_index())
    assert len(out) == 1
    d = out[0].detail
    assert d["materialized_table"] == TABLE and d["source_view"] == VIEW and d["data_flows"]
    assert d["opportunity_detected"] is True and d["viability_verified"] is False
    assert "acceso a las tablas remotas" in d["viability_to_confirm"]


def test_transformation_flow_opportunity_not_emitted_when_transformation_present():
    out = _rule_transformation_flow_opportunity(_index(table_hc=_hc_table(character="SEMANTIC_TRANSFORMATION_PRESENT", pure=False)))
    assert out == []
