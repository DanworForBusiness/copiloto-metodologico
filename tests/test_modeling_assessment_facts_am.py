"""M3 — hechos del MODELO ANALITICO. Solo hechos: sin riqueza semantica, sin subutilizacion, sin juicio de valor agregado."""
from __future__ import annotations

from tests._modeling_assessment_m3_fixtures import fld, m3_entities
from tests.test_modeling_assessment_graph import ent, pool
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.facts_am import am_facts
from tools.catalog.modeling_assessment.identity import classify_universe
from tools.catalog.modeling_assessment.scope import AssessmentUniverse


def _facts(ents=None, am="AM_M", **kw):
    ents = ents or m3_entities()
    return ents, am_facts(AssessmentUniverse.from_entities(ents), ents, am, **kw)


def _by(facts, fid, subject=None):
    out = [f for f in facts if f.fact_id == fid and (subject is None or f.subject == subject)]
    return out[0] if subject is not None else out


def test_all_am_facts_are_analytic_model_modeling_plane_and_assessed():
    _, facts = _facts()
    assert {f.fact_id for f in facts} == {"am.contract_inventory", "am.association_targets", "am.contract_delta",
                                          "am.numeric_attributes_not_declared_as_measures", "am.closure_composition"}
    assert {f.area for f in facts} == {m.Area.ANALYTIC_MODEL} and {f.plane for f in facts} == {m.Plane.MODELING}
    assert {f.assessability for f in facts} == {m.Assessability.ASSESSED}


def test_inventory_keeps_declared_role_full_field_list_and_element_vs_field_counts():
    _, facts = _facts()
    f = _by(facts, "am.contract_inventory", "AM_M")
    v = f.value
    assert f.evidence_class is m.EvidenceClass.A
    assert v["declared_role"]["declares_cube"] is True and (v["inferred_layer"], v["inferred_layer_basis"]) == ("4", "DECLARED_ROLE")
    assert v["summary"]["n_elements"] == 14 and v["summary"]["n_fields"] == 8       # L2: no se infieren campos faltantes
    assert [r["field"] for r in v["fields"]] == ["K", "A", "B", "AMT", "QTY", "CALC", "W", "TXT"]
    assert v["measures"] == [{"field": "AMT", "aggregation": None}, {"field": "QTY", "aggregation": None},
                             {"field": "CALC", "aggregation": "FORMULA"}]
    assert v["derived_fields"] == [{"field": "CALC", "is_measure": True, "is_association_foreign_key": False, "cast": None}]
    assert v["direct_lineage_providers"] == ["VF_M"] and sorted(v["direct_association_providers"]) == ["DIM_A", "DIM_B"]
    assert v["label_coverage"] == {"fields": 8, "with_label": 7, "without_label": 1} and v["keys"] == []


def test_association_targets_group_by_target_resolution_role_and_foreign_keys():
    _, facts = _facts()
    recs = _by(facts, "am.association_targets", "AM_M").value["associations"]
    by_field = {r["field"]: r for r in recs}
    assert set(by_field) == {"_dimA", "_dimB", "_dimA2"}                          # una fila por asociacion declarada
    a = by_field["_dimA"]
    assert a["target"] == "DIM_A" and a["target_in_graph"] and a["target_in_semantic_closure"]
    assert a["target_declared_role"]["modeling_pattern"] == "ANALYTICAL_DIMENSION" and a["target_own_association_count"] == 2
    assert a["edge_origin"] == "DECLARED_ASSOCIATION" and a["on_columns"] == [{"source_column": "K", "target_column": "K"}]
    assert by_field["_dimB"]["foreign_key_fields"] == ["B"] and by_field["_dimA"]["foreign_key_fields"] == []


def test_association_to_an_unresolvable_target_is_kept_explicitly_and_never_fabricated():
    am = ent("AM_Z", cube=True, has_query=True, fields=[fld("A")])
    am.associations = [{"campo": "_ghost", "target": "NOT_IN_POOL", "on": {}}]
    ents = pool(am)
    _, facts = _facts(ents, "AM_Z")
    r = _by(facts, "am.association_targets", "AM_Z").value["associations"][0]
    assert r["target"] == "NOT_IN_POOL" and r["target_in_graph"] is False and r["target_in_semantic_closure"] is False
    assert r["target_declared_role"] is None and r["target_identity"] is None and r["edge_origin"] is None


def test_delta_vs_the_direct_lineage_provider_inherits_associations_by_target():
    _, facts = _facts()
    f = _by(facts, "am.contract_delta", "VF_M->AM_M")
    v = f.value
    assert f.evidence_class is m.EvidenceClass.B
    assert v["fields"]["added"] == ["CALC", "TXT"] and v["fields"]["removed"] == ["NEWF", "OLDF"]
    assert [a["target"] for a in v["associations"]["inherited"]] == ["DIM_A", "DIM_B"] and v["associations"]["introduced"] == []
    assert v["measures"]["added"] == ["CALC"] and v["derived"]["only_in_consumer"] == ["CALC"]
    assert v["keys"]["lost"] == ["K"]                                              # hecho estructural, sin interpretar
    assert v["consumer_lineage_provider_count"] == 1


def test_numeric_attributes_not_declared_as_measures_report_where_upstream_declares_them_as_measures():
    _, facts = _facts()
    v = _by(facts, "am.numeric_attributes_not_declared_as_measures", "AM_M").value
    assert v["fields"] == [{"field": "W", "type": "Decimal", "length": "13,3", "measure_in_direct_lineage_providers": []}]
    ents = m3_entities()
    for f in ents["VF_M"].fields:
        if f["campo"] == "W":
            f["medida"] = True
    ents["VF_M"].measures.append({"campo": "W", "agg": "SUM"})
    _, facts2 = _facts(ents)
    assert _by(facts2, "am.numeric_attributes_not_declared_as_measures", "AM_M").value["fields"][0]["measure_in_direct_lineage_providers"] == ["VF_M"]


def test_am_without_lineage_provider_has_no_delta_fact_and_says_so_in_the_inventory():
    ents = pool(ent("AM_ALONE", cube=True, has_query=True, fields=[fld("A")]))
    _, facts = _facts(ents, "AM_ALONE")
    assert _by(facts, "am.contract_delta") == []
    assert _by(facts, "am.contract_inventory", "AM_ALONE").value["direct_lineage_providers"] == []


def test_precomputed_identity_gives_the_same_facts():
    ents = m3_entities()
    u = AssessmentUniverse.from_entities(ents)
    assert am_facts(u, ents, "AM_M") == am_facts(u, ents, "AM_M", identity=classify_universe(ents))


def test_no_dict_key_is_an_artifact_or_field_name():
    ents, facts = _facts()
    names = set(ents) | {f["campo"] for e in ents.values() for f in e.fields}

    def keys(v):
        if isinstance(v, dict):
            for k, x in v.items():
                yield k
                yield from keys(x)
        elif isinstance(v, list):
            for x in v:
                yield from keys(x)
    bad = {k for f in facts for k in keys(f.value)} & names
    assert bad == set()


def test_scoring_like_artifact_and_field_names_do_not_break_fact_construction():
    am = ent("AM_S", up=["SCORE_VIEW"], cube=True, has_query=True, fields=[fld("weight", measure=True)],
             measures=[{"campo": "weight", "agg": "SUM"}])
    view = ent("SCORE_VIEW", has_query=True, fields=[fld("weight", measure=True), fld("penalty")], measures=[{"campo": "weight", "agg": "SUM"}])
    _, facts = _facts(pool(am, view), "AM_S")
    assert _by(facts, "am.contract_delta", "SCORE_VIEW->AM_S").value["fields"]["removed"] == ["penalty"]


def test_closure_composition_is_a_descriptive_partition_of_the_semantic_closure():
    ents, facts = _facts()
    f = _by(facts, "am.closure_composition")[0]
    u = AssessmentUniverse.from_entities(ents)
    closure, lineage = set(u.closure_of(f.subject)), set(u.lineage_closure_of(f.subject))
    v = f.value
    assert v["semantic_closure_size"] == len(closure) == v["strict_lineage_size"] + v["association_mediated_size"]
    assert set(v["strict_lineage_artifacts"]) == lineage and set(v["association_mediated_artifacts"]) == closure - lineage
    assert not set(v["strict_lineage_artifacts"]) & set(v["association_mediated_artifacts"]) and f.subject in v["strict_lineage_artifacts"]
    assert f.evidence_class is m.EvidenceClass.B and not [k for k in v if m.find_forbidden_tokens(k)]       # contexto de arquitectura: sin vocabulario de calidad
