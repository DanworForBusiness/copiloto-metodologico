"""M3 — hechos de HARMONIZATION: dimensiones declaradas, composicion y reuso en DOS alcances (sin juicio de valor)."""
from __future__ import annotations

from tests._modeling_assessment_m3_fixtures import fld, m3_entities, with_assocs
from tests.test_modeling_assessment_graph import ent, pool
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.facts_harmonization import harmonization_facts
from tools.catalog.modeling_assessment.scope import AssessmentUniverse


def _facts(ents=None, am="AM_M"):
    ents = ents or m3_entities()
    return ents, harmonization_facts(AssessmentUniverse.from_entities(ents), ents, am)


def _by(facts, fid, subject=None):
    out = [f for f in facts if f.fact_id == fid and (subject is None or f.subject == subject)]
    return out[0] if subject is not None else out


def test_all_harmonization_facts_are_in_the_harmonization_area_and_modeling_plane():
    _, facts = _facts()
    assert {f.area for f in facts} == {m.Area.HARMONIZATION} and {f.plane for f in facts} == {m.Plane.MODELING}
    assert {f.fact_id for f in facts} == {"harmonization.dimension_artifacts", "harmonization.composition_artifacts",
                                          "harmonization.reuse_context", "harmonization.local_chain_reuse",
                                          "harmonization.cross_am_reuse"}


# ── dimensiones declaradas ─────────────────────────────────────────────────
def test_dimension_artifacts_are_selected_by_the_declared_role_with_own_associations_and_chain_depth():
    _, facts = _facts()
    f = _by(facts, "harmonization.dimension_artifacts", "closure")
    v = f.value
    assert f.evidence_class is m.EvidenceClass.A and v["selection_basis"].startswith("declared dimension role")
    by = {r["artifact"]: r for r in v["artifacts"]}
    assert set(by) == {"DIM_A", "DIM_B", "DIM_V", "SAP.TIME.DAY"} and v["count"] == 4
    assert v["with_own_associations"] == 2                                          # DIM_A (2 destinos) y SAP.TIME.DAY (1)
    a = by["DIM_A"]
    assert a["own_association_targets"] == ["DIM_A_TXT", "SAP.TIME.DAY"]
    assert a["association_chain"] == {"reachable_by_associations": 3, "max_distance": 2}
    assert by["DIM_B"]["association_chain"] == {"reachable_by_associations": 0, "max_distance": 0}
    assert by["DIM_V"]["semantic_dependency_chain"]["reachable_artifacts"] == 1
    assert by["SAP.TIME.DAY"]["is_system_artifact"] is True and by["DIM_A"]["is_system_artifact"] is False


def test_strict_lineage_reach_and_semantic_scope_are_separate_facts_on_dimensions():
    _, facts = _facts()
    by = {r["artifact"]: r for r in _by(facts, "harmonization.dimension_artifacts", "closure").value["artifacts"]}
    # DIM_V alimenta al AM por linaje; DIM_A/DIM_B estan en el scope semantico (asociacion) sin camino de linaje puro:
    # la diferencia es un HECHO, no relevancia
    assert by["DIM_V"]["reaches_terminal_by_strict_lineage"] is True
    assert by["DIM_A"]["reaches_terminal_by_strict_lineage"] is False and by["DIM_A"]["semantic_distance"] == 1


def test_the_declared_role_never_comes_from_the_layer_classification():
    ents = m3_entities()
    # artefacto con prefijo de capa 2 en el NOMBRE (`2VR_`) pero SIN rol de dimension declarado: no se selecciona
    ents["2VR_FLAT"] = ent("2VR_FLAT", up=["SRC1"], has_query=True, fields=[fld("K")])
    ents["VR_M"].upstreams = sorted(ents["VR_M"].upstreams + ["2VR_FLAT"])
    ents["DIM_B"].modeling_pattern = ""             # sin rol declarado: aunque el NOMBRE parezca dimension, no se selecciona
    ents["VF_M"].modeling_pattern = "ANALYTICAL_DIMENSION"   # rol declarado de dimension aunque el nombre sea de hecho
    _, facts = _facts(ents)
    names = {r["artifact"] for r in _by(facts, "harmonization.dimension_artifacts", "closure").value["artifacts"]}
    assert "2VR_FLAT" not in names and "DIM_B" not in names and "VF_M" in names


# ── composicion / fan-in ───────────────────────────────────────────────────
def test_composition_artifacts_are_those_with_fan_in_and_report_the_declared_contract_including_zero_zero_zero():
    _, facts = _facts()
    v = _by(facts, "harmonization.composition_artifacts", "closure").value
    assert v["count"] == 1
    r = v["artifacts"][0]
    assert r["artifact"] == "VR_M" and r["lineage_provider_count"] == 4
    assert r["declares_no_associations_measures_dimensions"] is True
    assert {p["artifact"] for p in r["lineage_providers"]} == {"SRC1", "SRC2", "DIM_V", "NOQ_V"}
    assert {p["artifact"]: p["is_boundary_artifact"] for p in r["lineage_providers"]} == {
        "SRC1": True, "SRC2": True, "DIM_V": False, "NOQ_V": False}
    assert r["reaches_terminal_by_strict_lineage"] is True and r["lineage_distance"] == 2


def test_composition_that_declares_associations_is_not_flagged_as_zero_zero_zero():
    ents = m3_entities()
    with_assocs(ents["VR_M"], [("_a", "DIM_B")])
    _, facts = _facts(ents)
    assert _by(facts, "harmonization.composition_artifacts", "closure").value["artifacts"][0]["declares_no_associations_measures_dimensions"] is False


# ── reuso en dos alcances ──────────────────────────────────────────────────
def test_reuse_context_defines_the_local_chain_and_the_cross_am_opportunity_denominator():
    _, facts = _facts()
    v = _by(facts, "harmonization.reuse_context", "closure").value
    assert v["local_chain_members"] == ["AM_M", "VF_M"]
    assert v["universe_terminal_am_count"] == 4 and v["other_terminal_am_count"] == 3
    assert v["other_terminal_ams"] == ["AM_N", "AM_O", "AM_P"]
    assert "terminal analytic model and its direct lineage providers" in v["local_chain_definition"]


def test_cross_am_reuse_counts_other_terminal_models_whose_closure_contains_the_artifact_excluding_the_evaluated_one():
    _, facts = _facts()
    a = _by(facts, "harmonization.cross_am_reuse", "DIM_A")
    assert a.evidence_class is m.EvidenceClass.B and a.assessability is m.Assessability.ASSESSED
    assert a.value == {"cross_am_reuse_count": 2, "other_terminal_am_count": 3, "terminal_ams_containing_artifact": ["AM_N", "AM_O"]}
    assert "AM_M" not in a.value["terminal_ams_containing_artifact"]
    b = _by(facts, "harmonization.cross_am_reuse", "DIM_B").value
    assert b["terminal_ams_containing_artifact"] == ["AM_O"] and b["cross_am_reuse_count"] == 1
    # dimension que solo vive en la clausura del AM evaluado, con 3 AM par disponibles: 0 EVALUADO (no es NOT_APPLICABLE)
    dv = _by(facts, "harmonization.cross_am_reuse", "DIM_V")
    assert dv.value["cross_am_reuse_count"] == 0 and dv.value["other_terminal_am_count"] == 3
    assert dv.assessability is m.Assessability.ASSESSED and dv.cause is None


def test_cross_am_reuse_is_recursive_through_the_other_models_closure():
    _, facts = _facts()
    # AM_N -> DIM_A -> SAP.TIME.DAY: el artefacto de sistema esta en la clausura RECURSIVA de AM_N y AM_O
    assert _by(facts, "harmonization.cross_am_reuse", "SAP.TIME.DAY").value["terminal_ams_containing_artifact"] == ["AM_N", "AM_O"]


def _single_am_pool():
    ents = pool(ent("AM_ONE", up=["VF1"], cube=True, has_query=True), ent("VF1", up=["D"], has_query=True),
                ent("D", pattern="ANALYTICAL_DIMENSION", has_query=True))
    ents["VF1"].assoc_upstreams = []
    with_assocs(ents["AM_ONE"], [("_d", "D")])
    return ents


def test_example_a_a_universe_with_a_single_terminal_model_makes_cross_am_reuse_not_applicable_keeping_the_raw_zero():
    _, facts = _facts(_single_am_pool(), "AM_ONE")
    f = _by(facts, "harmonization.cross_am_reuse", "D")
    assert f.assessability is m.Assessability.NOT_APPLICABLE and f.cause is None
    assert f.applicability.state is m.Applicability.NOT_APPLICABLE
    assert f.applicability.reason is m.ApplicabilityReason.NO_OTHER_TERMINAL_ANALYTIC_MODEL
    assert f.value == {"cross_am_reuse_count": 0, "other_terminal_am_count": 0, "terminal_ams_containing_artifact": []}
    # la cadena local NO depende de que existan AM par: sigue evaluada
    assert _by(facts, "harmonization.local_chain_reuse", "D").assessability is m.Assessability.ASSESSED


def test_example_b_zero_cross_am_reuse_with_available_peers_is_assessed_and_distinguishable_from_example_a():
    ents = _single_am_pool()
    for i in range(20):                              # 20 AM par que NO contienen a D
        ents[f"AM_PEER{i}"] = ent(f"AM_PEER{i}", cube=True, has_query=True)
    _, facts = _facts(ents, "AM_ONE")
    b = _by(facts, "harmonization.cross_am_reuse", "D")
    assert b.value["cross_am_reuse_count"] == 0 and b.value["other_terminal_am_count"] == 20
    assert b.assessability is m.Assessability.ASSESSED and b.applicability.state is m.Applicability.APPLICABLE
    _, facts_a = _facts(_single_am_pool(), "AM_ONE")
    a = _by(facts_a, "harmonization.cross_am_reuse", "D")
    assert (a.value["cross_am_reuse_count"], b.value["cross_am_reuse_count"]) == (0, 0)
    assert (a.assessability, b.assessability) == (m.Assessability.NOT_APPLICABLE, m.Assessability.ASSESSED)


def test_every_cross_am_reuse_fact_in_a_one_model_universe_is_not_applicable():
    ents = {n: e for n, e in m3_entities().items() if n in
            {"AM_M", "VF_M", "VR_M", "SRC1", "SRC2", "SRC3", "DIM_V", "NOQ_V", "DIM_A", "DIM_B", "DIM_A_TXT", "SAP.TIME.DAY", "SAP.TIME.M"}}
    _, facts = _facts(ents)
    cross = _by(facts, "harmonization.cross_am_reuse")
    assert cross and {f.assessability for f in cross} == {m.Assessability.NOT_APPLICABLE}
    assert {f.applicability.reason for f in cross} == {m.ApplicabilityReason.NO_OTHER_TERMINAL_ANALYTIC_MODEL}


def test_local_chain_reuse_is_kept_apart_from_cross_am_reuse_and_from_the_raw_consumer_count():
    _, facts = _facts()
    v = _by(facts, "harmonization.local_chain_reuse", "DIM_A").value
    assert v["association_consumers_in_closure"] == ["AM_M", "VF_M"] and v["lineage_consumers_in_closure"] == []
    assert v["local_chain_reuse_count"] == 2 and v["local_chain_consumers"] == ["AM_M", "VF_M"]
    assert v["direct_consumers_in_closure_count"] == 2 and v["other_direct_consumers_in_closure"] == []
    # un consumidor dentro de la clausura pero FUERA de la cadena local no cuenta como reuso local
    dv = _by(facts, "harmonization.local_chain_reuse", "DIM_V").value
    assert dv["local_chain_reuse_count"] == 0 and dv["other_direct_consumers_in_closure"] == ["VR_M"]
    assert "cross_am_reuse_count" not in v                                           # los dos alcances no se mezclan


def test_reuse_has_no_polarity_no_ranking_and_no_aggregate_across_scopes():
    _, facts = _facts()
    for f in _by(facts, "harmonization.cross_am_reuse"):
        assert set(f.value) == {"cross_am_reuse_count", "other_terminal_am_count", "terminal_ams_containing_artifact"}
    for f in _by(facts, "harmonization.local_chain_reuse"):
        assert not any(k in f.value for k in ("total_reuse", "reuse", "reuse_total", "cross_am_reuse_count"))


def test_inferred_layer_context_is_copied_with_its_basis_but_never_selects_the_population():
    _, facts = _facts()
    comp = _by(facts, "harmonization.composition_artifacts", "closure").value["artifacts"][0]
    assert comp["inferred_layer"] == "3" and comp["inferred_layer_basis"] == "NAME_PREFIX"
    assert "name_derived_layer" not in repr([f.value for f in facts])


def test_data_flow_targets_never_count_as_consumers_or_in_other_closures():
    ents = m3_entities()
    ents["DF_T"].upstreams = ["DIM_B"]                       # un target de DF que lee DIM_B: contexto Processing, no consumidor
    _, facts = _facts(ents)
    assert "DF_T" not in repr([(f.subject, f.value) for f in facts])
