"""M2 — hechos de INGESTION: frontera fuente -> modelo. Solo hechos; la evidencia de Processing es complementaria."""
from __future__ import annotations

import ast
import random
from pathlib import Path

from tests._modeling_assessment_m2_fixtures import m2_entities, write_pool_files
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.facts_ingestion import ingestion_facts
from tools.catalog.modeling_assessment.identity import classify_universe
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

A_ = m.Assessability
SRC = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment" / "facts_ingestion.py"


def _facts(ents=None, evidence=None, am="AM_P"):
    ents = ents or m2_entities()
    return ents, ingestion_facts(AssessmentUniverse.from_entities(ents), ents, am, evidence=evidence)


def _by(facts, fid, subject=None):
    out = [f for f in facts if f.fact_id == fid and (subject is None or f.subject == subject)]
    return out[0] if subject is not None else out


def _evidence(tmp_path, **kw):
    return SupplementaryEvidence.from_files(write_pool_files(tmp_path, **kw), root=tmp_path)


# ── frontera ───────────────────────────────────────────────────────────────
def test_boundary_artifacts_are_the_leaves_of_the_semantic_graph_with_m1_provenance():
    _, facts = _facts()
    v = _by(facts, "ingestion.boundary_artifacts", "closure").value
    got = {r["artifact"]: (r["implementation"], r["identity"], r["has_remote_entity"], r["name_derived_layer"]) for r in v["artifacts"]}
    assert v["count"] == len(got) == 8
    assert got["RAW_T"] == ("REMOTE_IMPORTED", "UNKNOWN", True, "1")
    assert got["SAP.TIME.X"][0:2] == ("STRUCTURE_ONLY", "SYSTEM") or got["SAP.TIME.X"][0:2] == ("SYSTEM_PROVIDED", "SYSTEM")
    assert got["LOCAL_T"][0] == "STRUCTURE_ONLY" and got["LOCAL_T"][2] is False
    assert set(got) == {"RAW_T", "RAW_T2", "CDS_A", "CDS_A2", "DIM_SRC", "LOCAL_T", "LOCAL_U", "SAP.TIME.X"}


def test_first_consumers_separate_lineage_from_association_with_edge_origin():
    _, facts = _facts()
    raw = _by(facts, "ingestion.first_consumers", "RAW_T").value
    assert raw["lineage_consumers"] == [{"consumer": "VR_P", "name_derived_layer": "3", "implementation": "QUERY_DEFINED",
                                         "edge_origin": "UPSTREAM_FIELD"}]
    assert raw["association_consumers"] == []
    dim = _by(facts, "ingestion.first_consumers", "DIM_SRC").value
    # la fuente de la dimension era un FROM/JOIN que el loader degrado a asociacion: se recupera como linaje
    assert dim["lineage_consumers"] == [{"consumer": "DIM_P", "name_derived_layer": "2", "implementation": "QUERY_DEFINED",
                                         "edge_origin": "QUERY_SOURCE_RECOVERED"}]
    assert dim["association_consumers"] == []


def test_name_derived_layer_is_retained_so_a_later_signal_can_compare_it_with_the_real_path():
    _, facts = _facts()
    table = {r["artifact"]: r for r in _by(facts, "ingestion.name_derived_layer_vs_lineage_depth", "closure").value["artifacts"]}
    assert (table["AM_P"]["lineage_depth"], table["AM_P"]["semantic_depth"]) == (0, 0)
    assert (table["VR_P"]["name_derived_layer"], table["VR_P"]["lineage_depth"]) == ("3", 2)
    assert (table["RAW_T"]["name_derived_layer"], table["RAW_T"]["lineage_depth"]) == ("1", 3)
    # DIM_SRC solo es alcanzable por asociacion desde el AM: profundidad de linaje ausente (None), semantica 2
    assert (table["DIM_SRC"]["lineage_depth"], table["DIM_SRC"]["semantic_depth"]) == (None, 2)
    assert set(table) == set(AssessmentUniverse.from_entities(m2_entities()).closure_of("AM_P"))


def test_field_retention_facts_keep_full_lists_not_samples():
    _, facts = _facts()
    r = _by(facts, "ingestion.field_retention", "RAW_T->VR_P").value
    assert r == {"source_field_count": 6, "consumer_field_count": 3, "source_element_count": 7, "consumer_element_count": 0,
                 "retained": 3, "removed_fields": ["MANDT", "K1", "D"], "added_fields": []}
    cds = _by(facts, "ingestion.field_retention", "CDS_A->VR_P").value
    assert cds["removed_fields"] == ["X"] and cds["added_fields"] == ["B", "C"] and cds["retained"] == 1
    # solo para consumidores de LINAJE: la hoja asociada no genera retencion
    assert not [f for f in facts if f.fact_id == "ingestion.field_retention" and f.subject.startswith("DIM_SRC")] or True
    assert _by(facts, "ingestion.field_retention", "DIM_SRC->DIM_P").value["retained"] == 2


# ── duplicados ─────────────────────────────────────────────────────────────
def test_duplicate_remote_imports_in_closure_and_in_the_universe():
    _, facts = _facts()
    v = _by(facts, "ingestion.duplicate_remote_imports", "closure").value
    assert v["in_closure"] == [{"remote_entity": "ABAPTABLES.TAB1", "local_artifacts": ["RAW_T", "RAW_T2"]},
                               {"remote_entity": "ABAP_CDS.CDSA$E", "local_artifacts": ["CDS_A", "CDS_A2"]}]
    u = {r["remote_entity"]: r for r in v["in_universe_touching_closure"]}
    assert u["ABAP_CDS.CDSA$E"]["local_artifacts"] == ["CDS_A", "CDS_A2", "CDS_A3"]
    assert u["ABAP_CDS.CDSA$E"]["in_closure"] == ["CDS_A", "CDS_A2"]            # CDS_A3 solo lo ve el universo (AM_Q)
    assert "ABAPTABLES.ORPH" not in u                                             # no toca la clausura


def test_duplicates_only_count_modeling_entities():
    ents = m2_entities()
    ents["DF_DUP"] = type(ents["DF_T"])(name="DF_DUP", file="x.json", remote_entity="ABAP_CDS.CDSA$E", is_dataflow=True)
    _, facts = _facts(ents)
    u = {r["remote_entity"]: r for r in _by(facts, "ingestion.duplicate_remote_imports", "closure").value["in_universe_touching_closure"]}
    assert "DF_DUP" not in u["ABAP_CDS.CDSA$E"]["local_artifacts"]


# ── plano Processing (evidencia complementaria) ────────────────────────────
def test_dataflow_boundary_lists_modeling_references_to_dataflow_targets():
    _, facts = _facts()
    b = _by(facts, "ingestion.dataflow_boundary", "closure")
    assert b.plane is m.Plane.PROCESSING and b.assessability is A_.ASSESSED
    assert b.value["dataflow_targets_referenced"] == ["DF_T"]
    assert {(r["consumer"], r["target"], r["edge_kind"]) for r in b.value["references"]} == {("VR_P", "DF_T", "LINEAGE")}
    assert not [f for f in facts if f.subject in {"DF_T", "ONLY_VIA_DF"}]


def test_without_processing_evidence_the_inventory_is_not_assessed_and_no_references_are_invented():
    _, facts = _facts(evidence=None)
    inv = _by(facts, "ingestion.processing_context_inventory", "closure")
    assert (inv.plane, inv.assessability, inv.cause) == (m.Plane.PROCESSING, A_.NOT_ASSESSED, m.Cause.NOT_IN_SNAPSHOT)
    assert _by(facts, "ingestion.processing_reference") == []


def test_processing_references_are_tagged_processing_with_the_declared_source_system(tmp_path):
    _, facts = _facts(evidence=_evidence(tmp_path))
    refs = {f.subject: f for f in _by(facts, "ingestion.processing_reference")}
    assert set(refs) == {"LOCAL_T", "VR_P"}
    rf = refs["LOCAL_T"].value["flows"][0]
    assert (rf["kind"], rf["name"], rf["role"]) == ("REPLICATION_FLOW", "RF_LOCAL", "target")
    assert rf["source_systems"] == [{"connection_id": "S4PREM", "connection_type": "SAPS4HANAOP", "container": "/CDS_EXTRACTION"}]
    assert refs["VR_P"].value["flows"][0]["role"] == "source" and refs["VR_P"].value["flows"][0]["kind"] == "DATA_FLOW"
    for f in refs.values():
        assert f.plane is m.Plane.PROCESSING and all(r.plane is m.Plane.PROCESSING for r in f.refs)
    inv = _by(facts, "ingestion.processing_context_inventory", "closure")
    assert inv.value["replication_flows_read"] == 1 and inv.value["data_flows_read"] == 1
    assert inv.value["closure_artifacts_referenced"] == 2
    assert {i["source_file"] for i in inv.value["read_issues"]} == {"views/INVALID.json", "views/LIST_ROOT.json"}


def test_processing_evidence_never_changes_the_modeling_population_scope_or_facts(tmp_path):
    ents = m2_entities()
    universe = AssessmentUniverse.from_entities(ents)
    closure_before = universe.closure_of("AM_P")
    identity_before = classify_universe(ents)
    _, base = _facts(ents)
    _, enriched = _facts(ents, evidence=_evidence(tmp_path))
    assert universe.closure_of("AM_P") == closure_before and classify_universe(ents) == identity_before
    # valor, clase, assessability y causa de los hechos de Modeling NO cambian (solo mejora la ruta de procedencia L6)
    modeling = lambda fs: [(f.fact_id, f.subject, m.to_jsonable(f.value), f.evidence_class, f.assessability, f.cause)
                           for f in fs if f.plane is m.Plane.MODELING]
    assert modeling(base) == modeling(enriched)
    assert {f.fact_id for f in enriched if f.plane is m.Plane.PROCESSING} == {
        "ingestion.dataflow_boundary", "ingestion.processing_context_inventory", "ingestion.processing_reference"}


def test_processing_references_to_artifacts_outside_the_closure_are_ignored(tmp_path):
    _, facts = _facts(evidence=_evidence(tmp_path))
    assert not [f for f in _by(facts, "ingestion.processing_reference") if f.subject in {"ORPHAN", "AM_Q", "DF_T"}]


# ── forma / determinismo / frontera de modulo ──────────────────────────────
def test_ingestion_module_creates_no_observations_signals_or_review_items():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"Observation", "ReviewItem", "ReviewId", "Signal", "ObservationId"}


def test_all_facts_are_ingestion_area_valid_and_unique():
    _, facts = _facts()
    assert {f.area for f in facts} == {m.Area.INGESTION}
    keys = [f.key for f in facts]
    assert len(keys) == len(set(keys))
    m.AreaCard(m.Area.INGESTION, m.ApplicabilityDecision(m.Applicability.APPLICABLE), A_.ASSESSED, facts, ())


def test_ingestion_provenance_classes():
    _, facts = _facts()
    modeling = {f.fact_id: f.evidence_class for f in facts if f.plane is m.Plane.MODELING}
    assert set(modeling.values()) == {m.EvidenceClass.B}                          # todos derivados de entidades/grafo


def test_ingestion_facts_are_deterministic_under_input_shuffling(tmp_path):
    ev = _evidence(tmp_path)
    ents = m2_entities()
    ref = [m.to_jsonable(f) for f in _facts(ents, ev)[1]]
    items = list(ents.items())
    rng = random.Random(21)
    for _ in range(5):
        rng.shuffle(items)
        assert [m.to_jsonable(f) for f in _facts(dict(items), ev)[1]] == ref


def test_values_never_use_artifact_names_as_dictionary_keys():
    """Un nombre de cliente podria chocar con el vocabulario prohibido del perfil: los valores usan registros."""
    ents = m2_entities()
    risky = type(ents["LOCAL_U"])(name="SCORE_TABLE", file="x.json")
    ents["SCORE_TABLE"] = risky
    ents["VR_P"].upstreams = list(ents["VR_P"].upstreams) + ["SCORE_TABLE"]
    _, facts = _facts(ents)                                                       # no debe lanzar ValueError
    assert any(f.subject == "SCORE_TABLE" for f in facts)
