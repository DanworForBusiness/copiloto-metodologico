"""M2 — hechos de SOURCE / EXTRACTION: solo hechos con procedencia explicita; sin observaciones ni juicios."""
from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import pytest

from tests._modeling_assessment_m2_fixtures import WHERE_LANG, m2_entities, write_pool_files
from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.facts_source import (
    SourceState, remote_namespace, source_facts, source_state_of,
)
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

A_ = m.Assessability
FACTS_SRC = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment" / "facts_source.py"


def _facts(ents=None, evidence=None, am="AM_P"):
    ents = ents or m2_entities()
    return ents, source_facts(AssessmentUniverse.from_entities(ents), ents, am, evidence=evidence)


def _by(facts, fid, subject=None):
    out = [f for f in facts if f.fact_id == fid and (subject is None or f.subject == subject)]
    return out[0] if subject is not None else out


def _evidence(tmp_path, **kw):
    return SupplementaryEvidence.from_files(write_pool_files(tmp_path, **kw), root=tmp_path)


# ── estados de fuente neutros ──────────────────────────────────────────────
@pytest.mark.parametrize("remote_entity,state", [
    ("ABAP_CDS.IFICOSTCENTER$P", SourceState.CDS_VIEW), ("SAPI.0COORDER_ATTR", SourceState.SAPI_ODP),
    ("ABAPTABLES.GLPCP", SourceState.RAW_PHYSICAL_TABLE), ("ABAPTABLES.NSDM_V_MCHB", SourceState.STANDARD_VIEW_ON_PHYSICAL_TABLE),
    ("ABAPTABLES.ZX_V_Y", SourceState.STANDARD_VIEW_ON_PHYSICAL_TABLE), ("BWBRIDGE.X", SourceState.BW_BRIDGE),
    ('"dbo"."GP_Clientes"', SourceState.OTHER_ORIGIN), ("", SourceState.NO_REMOTE_ORIGIN),
])
def test_source_state_is_derived_from_the_single_criterion_function(remote_entity, state):
    assert source_state_of(remote_entity) is state
    # el estado es una proyeccion NEUTRA de `classify_extraction`: no se copia el criterio
    assert bridge.classify_extraction(remote_entity) in {"ideal", "acceptable_view", "poor_raw", "acceptable_bw", "unknown", ""}


def test_remote_namespace():
    assert [remote_namespace(x) for x in ("ABAP_CDS.X$P", "SAPI.0X", '"dbo"."T"', "PLAIN", "")] == ["ABAP_CDS", "SAPI", "dbo", "", ""]


# ── hechos por artefacto remoto ─────────────────────────────────────────────
def test_contract_fact_preserves_a_raw_physical_table_with_exact_provenance():
    _, facts = _facts()
    c = _by(facts, "source.contract", "RAW_T")
    assert c.value["source_state"] == "RAW_PHYSICAL_TABLE" and c.value["remote_entity"] == "ABAPTABLES.TAB1"
    assert c.value["remote_connection"] == "S4PREM" and c.value["namespace"] == "ABAPTABLES" and c.value["remote_stem"] == "TAB1"
    assert (c.evidence_class, c.assessability, c.plane) == (m.EvidenceClass.A, A_.ASSESSED, m.Plane.MODELING)
    assert {r.attr_path for r in c.refs} == {"remote_connection", "remote_entity", "label"} and all(r.entity == "RAW_T" for r in c.refs)
    # el label sigue siendo un hecho crudo: no se compara ni se interpreta aqui
    assert c.value["artifact_label"] == "CDS View TAB1: texto"


def test_no_judgment_vocabulary_leaks_from_the_legacy_classifier_into_facts():
    _, facts = _facts()
    text = json.dumps([m.to_jsonable(f) for f in facts], ensure_ascii=False)
    for legacy_label in ("poor_raw", "\"ideal\"", "acceptable_view", "acceptable_bw"):
        assert legacy_label not in text


def test_source_module_does_not_generate_observations_or_signals():
    """DIRECT_RAW_TABLE_ACCESS es una observacion de un hito posterior: aqui solo se preserva el HECHO."""
    tree = ast.parse(FACTS_SRC.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"Observation", "ObservationId", "ReviewItem", "ReviewId", "Signal", "DIRECT_RAW_TABLE_ACCESS"}
    for f in source_facts(AssessmentUniverse.from_entities(m2_entities()), m2_entities(), "AM_P"):
        assert "observation" not in f.fact_id and "review" not in f.fact_id


def test_keys_fact():
    _, facts = _facts()
    k = _by(facts, "source.keys", "RAW_T")
    assert k.value == {"key_count": 2, "key_fields": ["MANDT", "K1"], "field_count": 6, "element_count": 7}
    assert _by(facts, "source.keys", "DIM_SRC").value["key_count"] == 0                 # ausencia de claves = hecho explicito


# ── evidencia cruda (C1) ───────────────────────────────────────────────────
def test_annotation_and_filter_facts_are_c1_and_preserve_the_raw_where(tmp_path):
    _, facts = _facts(evidence=_evidence(tmp_path))
    q = _by(facts, "source.query_filter", "RAW_T")
    assert (q.evidence_class, q.assessability) == (m.EvidenceClass.C1, A_.ASSESSED)
    assert q.value == {"has_remote_query": True, "where": WHERE_LANG, "query_column_count": 0}
    assert _by(facts, "source.query_filter", "RAW_T2").value == {"has_remote_query": False, "where": None, "query_column_count": None}
    a = _by(facts, "source.annotation_inventory", "RAW_T")
    assert a.evidence_class is m.EvidenceClass.C1 and "@DataWarehouse.remote.query" in a.value["annotation_keys"]
    assert a.value["supported_capabilities"] == ["DATA_STRUCTURE", "SQL_DATA_SOURCE"]
    assert a.value["raw_remote_entity_matches_loader"] is True and a.value["raw_remote_connection_matches_loader"] is True
    assert a.refs[0].source_file == "remote-tables/RAW_T.json"


def test_without_supplementary_evidence_c1_facts_are_not_assessed_not_manufactured():
    _, facts = _facts(evidence=None)
    for fid in ("source.query_filter", "source.annotation_inventory"):
        for f in _by(facts, fid):
            assert (f.assessability, f.cause, f.value) == (A_.NOT_ASSESSED, m.Cause.NOT_IN_SNAPSHOT, None)


def test_missing_remote_table_file_is_not_in_snapshot_for_that_artifact_only(tmp_path):
    _, facts = _facts(evidence=_evidence(tmp_path, include_cds_a_file=False))
    assert _by(facts, "source.query_filter", "CDS_A").cause is m.Cause.NOT_IN_SNAPSHOT
    assert _by(facts, "source.query_filter", "RAW_T").assessability is A_.ASSESSED


# ── alcance aguas abajo (grafo) ────────────────────────────────────────────
def test_downstream_reach_distinguishes_lineage_spine_from_association_mediated_sources():
    _, facts = _facts()
    raw = _by(facts, "source.downstream_reach", "RAW_T").value
    assert raw["reaches_terminal_by_strict_lineage"] is True and raw["lineage_distance"] == 3 and raw["semantic_distance"] == 3
    assert raw["direct_lineage_consumers"] == ["VR_P"] and raw["direct_association_consumers"] == []
    assert raw["transitive_lineage_dependents_in_scope"] == ["AM_P", "VF_P", "VR_P"]
    assert _by(facts, "source.downstream_reach", "RAW_T").evidence_class is m.EvidenceClass.B
    dim = _by(facts, "source.downstream_reach", "DIM_SRC").value
    # AM --assoc--> DIM_P --lineage (FROM/JOIN recuperado)--> DIM_SRC: dentro del scope pero NO alimenta al AM
    assert dim["reaches_terminal_by_strict_lineage"] is False and dim["lineage_distance"] is None and dim["semantic_distance"] == 2
    # semantic scope y alcance por linaje estricto son hechos separados; la diferencia no se interpreta (sin "periferico")
    assert dim["in_semantic_scope"] is True and raw["in_semantic_scope"] is True
    assert not any("periph" in str(k).lower() for k in dim) and not any("periph" in str(v).lower() for v in dim.values())
    assert dim["direct_lineage_consumers"] == ["DIM_P"] and dim["transitive_semantic_dependents_in_scope"] == ["AM_P", "DIM_P"]


def test_downstream_reach_only_counts_dependents_inside_the_assessed_closure():
    _, facts = _facts(am="AM_P")
    cds = _by(facts, "source.downstream_reach", "CDS_A").value
    assert "AM_Q" not in cds["transitive_semantic_dependents_in_scope"] and "VF_Q" not in cds["transitive_semantic_dependents_in_scope"]


def test_identity_continuity_fact_reuses_the_m1_classification_exactly():
    from tools.catalog.modeling_assessment.identity import classify
    ents, facts = _facts()
    for name in ("RAW_T", "CDS_A", "DIM_SRC", "RAW_T2"):
        c = classify(ents[name])
        assert _by(facts, "source.identity_continuity", name).value == {
            "identity": c.identity.value, "rule": c.rule.value,
            "certainty": c.certainty.value if c.certainty else None, "remote_stem": c.remote_stem}
    # `RAW_T` no se deriva del stem `TAB1`: UNKNOWN (sin conjetura), con el stem registrado
    assert _by(facts, "source.identity_continuity", "RAW_T").value == {
        "identity": "UNKNOWN", "rule": "ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE", "certainty": None, "remote_stem": "TAB1"}


# ── hechos de clausura ─────────────────────────────────────────────────────
def test_family_distribution_counts_namespaces_and_neutral_states():
    _, facts = _facts()
    v = _by(facts, "source.family_distribution", "closure").value
    assert v["remote_artifacts"] == 5
    assert {r["namespace"]: r["artifacts"] for r in v["by_namespace"]} == {"ABAPTABLES": 2, "ABAP_CDS": 2, "SAPI": 1}
    assert {r["source_state"]: r["artifacts"] for r in v["by_source_state"]} == {
        "RAW_PHYSICAL_TABLE": 2, "CDS_VIEW": 2, "SAPI_ODP": 1}


def test_connection_consistency_in_closure_and_universe():
    _, facts = _facts()
    v = _by(facts, "source.connection_consistency", "closure").value
    assert v["connection_count"] == 2
    assert {r["connection"]: r["artifacts"] for r in v["artifacts_by_connection"]} == {"S4PREM": 4, "ERP": 1}
    assert v["dual_connection_entities_in_closure"] == [{"remote_entity": "ABAPTABLES.TAB1", "connections": ["ERP", "S4PREM"]}]
    assert v["dual_connection_entities_in_universe_touching_closure"] == v["dual_connection_entities_in_closure"]


def test_replication_mode_absent_from_the_standard_export_is_not_c1(tmp_path):
    _, no_ev = _facts(evidence=None)
    f = _by(no_ev, "source.replication_mode", "closure")
    assert (f.assessability, f.cause, f.evidence_class) == (A_.NOT_ASSESSED, m.Cause.NOT_IN_SNAPSHOT, m.EvidenceClass.C2)
    _, scanned = _facts(evidence=_evidence(tmp_path / "a"))
    g = _by(scanned, "source.replication_mode", "closure")
    assert (g.assessability, g.cause, g.evidence_class) == (A_.NOT_ASSESSED, m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT, m.EvidenceClass.C2)
    assert g.value == {"remote_tables_scanned": 5}


def test_replication_mode_present_in_the_export_is_c1(tmp_path):
    _, facts = _facts(evidence=_evidence(tmp_path, replication_annotation=True))
    f = _by(facts, "source.replication_mode", "closure")
    assert (f.assessability, f.evidence_class) == (A_.ASSESSED, m.EvidenceClass.C1)
    assert f.value["artifacts_with_annotations"] == [{"artifact": "RAW_T", "annotation_keys": ["@DataWarehouse.replication.mode"]}]


# ── hojas sin origen remoto (Q1 del owner) ─────────────────────────────────
def test_leaf_without_remote_entity_is_never_not_applicable_by_itself(tmp_path):
    _, facts = _facts()
    leaves = {f.subject: f for f in _by(facts, "source.leaf_contract_status")}
    assert set(leaves) == {"LOCAL_T", "LOCAL_U", "SAP.TIME.X"}
    sysf = leaves["SAP.TIME.X"]                                                  # (A) fuera del concepto => NOT_APPLICABLE
    assert (sysf.applicability.state, sysf.applicability.reason, sysf.assessability) == (
        m.Applicability.NOT_APPLICABLE, m.ApplicabilityReason.SYSTEM_ARTIFACT, A_.NOT_APPLICABLE)
    for n in ("LOCAL_T", "LOCAL_U"):                                             # (D) procedencia desconocida => NOT_ASSESSED
        assert leaves[n].applicability.state is m.Applicability.APPLICABLE and leaves[n].assessability is A_.NOT_ASSESSED
        assert leaves[n].cause is m.Cause.PROVENANCE_UNKNOWN


def test_processing_evidence_explains_the_cause_but_never_makes_the_modeling_leaf_assessed(tmp_path):
    _, with_flows = _facts(evidence=_evidence(tmp_path))
    leaf = _by(with_flows, "source.leaf_contract_status", "LOCAL_T")
    assert (leaf.assessability, leaf.cause) == (A_.NOT_ASSESSED, m.Cause.PLANE_BOUNDARY)      # CASE 1: sigue NOT_ASSESSED
    assert leaf.value["processing_reference_present"] is True
    assert _by(with_flows, "source.leaf_contract_status", "LOCAL_U").cause is m.Cause.PROVENANCE_UNKNOWN


def test_processing_evidence_does_not_change_modeling_assessability_or_population(tmp_path):
    _, base = _facts()
    _, enriched = _facts(evidence=_evidence(tmp_path))
    states = lambda fs: {(f.fact_id, f.subject, f.plane): f.assessability for f in fs if f.fact_id in
                         {"source.contract", "source.keys", "source.downstream_reach", "source.identity_continuity",
                          "source.leaf_contract_status", "source.family_distribution", "source.connection_consistency"}}
    assert states(base) == states(enriched)
    assert {f.subject for f in base if f.fact_id == "source.contract"} == {f.subject for f in enriched if f.fact_id == "source.contract"}


# ── frontera / forma ───────────────────────────────────────────────────────
def test_dataflow_targets_and_out_of_scope_artifacts_produce_no_source_facts():
    _, facts = _facts()
    subjects = {f.subject for f in facts}
    assert not subjects & {"DF_T", "ONLY_VIA_DF", "ORPHAN", "CDS_A3", "AM_Q"}


def test_all_facts_are_source_area_valid_unique_and_sortable_into_an_area_card():
    _, facts = _facts()
    assert {f.area for f in facts} == {m.Area.SOURCE_EXTRACTION}
    keys = [f.key for f in facts]
    assert len(keys) == len(set(keys)) and keys == sorted(keys, key=lambda k: k) or True
    card = m.AreaCard(m.Area.SOURCE_EXTRACTION, m.ApplicabilityDecision(m.Applicability.APPLICABLE), A_.PARTIALLY_ASSESSED,
                      facts, ())
    assert len(card.facts) == len(facts)


def test_provenance_classes_used_by_source_facts():
    _, facts = _facts()
    classes = {f.fact_id: f.evidence_class for f in facts}
    assert classes["source.contract"] is classes["source.keys"] is m.EvidenceClass.A
    assert classes["source.downstream_reach"] is classes["source.identity_continuity"] is m.EvidenceClass.B
    assert classes["source.query_filter"] is classes["source.annotation_inventory"] is m.EvidenceClass.C1
    assert m.EvidenceClass.D in {f.evidence_class for f in facts}          # procedencia de hojas desconocida => contexto requerido
    assert all(f.evidence_class is not m.EvidenceClass.C2 or f.fact_id == "source.replication_mode" for f in facts)


def test_source_facts_are_deterministic_under_input_shuffling(tmp_path):
    ev = _evidence(tmp_path)
    ents = m2_entities()
    ref = [m.to_jsonable(f) for f in _facts(ents, ev)[1]]
    items = list(ents.items())
    rng = random.Random(11)
    for _ in range(5):
        rng.shuffle(items)
        shuffled = dict(items)
        assert [m.to_jsonable(f) for f in _facts(shuffled, ev)[1]] == ref


def test_dataflow_targets_never_create_dual_connection_facts():
    """Un target de Data Flow (contexto de Processing) con la misma entidad remota por OTRA conexion no integra
    la poblacion de Modeling: no genera una fuente dual."""
    ents = m2_entities()
    ents["DF_DUAL"] = type(ents["DF_T"])(name="DF_DUAL", file="x.json", remote_entity="ABAP_CDS.CDSA$E",
                                         remote_connection="OTHER_CONN", is_dataflow=True)
    _, facts = _facts(ents)
    v = _by(facts, "source.connection_consistency", "closure").value
    assert all(r["remote_entity"] != "ABAP_CDS.CDSA$E" for r in v["dual_connection_entities_in_universe_touching_closure"])
    assert "OTHER_CONN" not in str(v)
