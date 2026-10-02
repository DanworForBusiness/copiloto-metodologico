"""M3 — contrato estructural de un artefacto (helpers compartidos). Solo hechos; sin ninguna interpretacion."""
from __future__ import annotations

from tests._modeling_assessment_m3_fixtures import fld, m3_entities, with_assocs
from tests.test_modeling_assessment_graph import ent
from tools.catalog.modeling_assessment import contract as C


def test_declared_role_copies_the_export_declaration_without_inferring_a_functional_role():
    e = m3_entities()
    assert C.declared_role(e["DIM_A"]) == {"modeling_pattern": "ANALYTICAL_DIMENSION", "data_category": None,
                                           "declares_dimension": True, "declares_fact": False, "declares_cube": False}
    assert C.declared_role(e["AM_M"])["declares_cube"] is True
    assert C.declared_role(e["VF_M"])["declares_fact"] is True
    plain = C.declared_role(e["SRC1"])
    assert plain["modeling_pattern"] is None and not any(plain[k] for k in ("declares_dimension", "declares_fact", "declares_cube"))


def test_inferred_layer_is_a_composite_inference_not_a_functional_role():
    e = m3_entities()
    assert [C.inferred_layer(e[n]) for n in ("VF_M", "AM_M", "SRC1")] == ["3", "4", "1"]


def test_inferred_layer_basis_exposes_the_rule_that_produced_each_layer():
    cases = [(ent("S", remote_entity="X.S", remote_connection="C"), ("1", "REMOTE_ENTITY")),
             (ent("2VR_FLAT"), ("2", "SECTION_28_NAME")),
             (ent("X_FACT", pattern="ANALYTICAL_FACT"), ("3", "DECLARED_ROLE")),
             (ent("ANY", pattern="ANALYTICAL_DIMENSION"), ("2", "DECLARED_ROLE")),
             (ent("AM_Z"), ("4", "NAME_PREFIX")), (ent("VX_Q"), ("3", "NAME_PREFIX")),
             (ent("ZZZ"), ("9", "UNKNOWN"))]
    for e, (layer, basis) in cases:
        assert C.layer_inference(e) == {"inferred_layer": layer, "inferred_layer_basis": basis}, e.name


def test_basis_precedence_matches_the_composite_criterion_exhaustively():
    """La base se deriva replicando la precedencia de `_capa_of`; este test fuerza la paridad en un producto cartesiano."""
    from tools.catalog.modeling_assessment import legacy_bridge as bridge
    remotes, names = ("", "X.E"), ("2VR_A", "VD_A", "AM_A", "VF_A", "EM_A", "ZZZ", "3VF_A", "SAP.TIME.X")
    patterns = ("", "ANALYTICAL_CUBE", "ANALYTICAL_FACT", "ANALYTICAL_DIMENSION", "DATA_STRUCTURE")
    for r in remotes:
        for n in names:
            for p in patterns:
                e = ent(n, pattern=p, remote_entity=r, remote_connection="C" if r else "")
                layer, basis = C.inferred_layer(e), C.inferred_layer_basis(e)
                assert layer == str(bridge.capa_of(e))
                assert (basis == "REMOTE_ENTITY") == bool(r)
                assert (basis == "UNKNOWN") == (layer == "9")
                if basis == "SECTION_28_NAME":
                    assert bridge.parse_name(n).get("capa") == layer
                if basis == "DECLARED_ROLE":
                    assert not r and not bridge.parse_name(n).get("capa") and layer in ("2", "3", "4")


def test_the_declared_role_and_the_inferred_layer_are_separate_surfaces():
    # el mismo artefacto: la capa "2" se INFIERE de su rol declarado; el rol sigue siendo un hecho aparte
    e = ent("ANY", pattern="ANALYTICAL_DIMENSION")
    assert C.inferred_layer_basis(e) == "DECLARED_ROLE" and C.declared_role(e)["declares_dimension"] is True


def test_associations_are_grouped_by_target_and_keep_every_field_name():
    e = m3_entities()
    assert C.associations_by_target(e["AM_M"]) == {"DIM_A": ["_dimA", "_dimA2"], "DIM_B": ["_dimB"]}
    assert C.associations_by_target(e["VF_M"]) == {"DIM_A": ["_a1"], "DIM_B": ["_b1"]}


def test_derived_flag_is_not_assumed_to_mean_measure():
    e = ent("X", fields=[fld("M", measure=True, derived=True), fld("FK", derived=True, assoc="DIM_Z"), fld("PLAIN")], has_query=True)
    recs = C.derived_records(e)
    assert recs == [{"field": "M", "is_measure": True, "is_association_foreign_key": False, "cast": None},
                    {"field": "FK", "is_measure": False, "is_association_foreign_key": True, "cast": None}]


def test_numeric_non_measure_fields_use_the_declared_type_and_skip_measures_keys_and_fks():
    e = ent("X", fields=[fld("W", tipo="Decimal"), fld("N", tipo="Integer"), fld("S"), fld("MEAS", tipo="Decimal", measure=True),
                         fld("KEY", tipo="Integer", key=True), fld("FK", tipo="Integer", assoc="D")])
    assert [r["field"] for r in C.numeric_non_measure_fields(e)] == ["W", "N"]


def test_label_coverage_and_element_vs_field_counts_are_reported_separately():
    e = m3_entities()["AM_M"]
    assert C.label_coverage(e) == {"fields": 8, "with_label": 7, "without_label": 1}
    s = C.contract_summary(e)
    assert (s["n_elements"], s["n_fields"], s["n_associations"], s["n_measures"], s["n_derived"]) == (14, 8, 3, 3, 1)


def test_edge_delta_lists_are_complete_and_associations_are_matched_by_target_not_by_field_name():
    e = m3_entities()
    d = C.edge_delta(e["VF_M"], e["AM_M"], consumer_lineage_provider_count=1)
    assert d["fields"]["added"] == ["CALC", "TXT"] and d["fields"]["removed"] == ["NEWF", "OLDF"] and d["fields"]["retained_count"] == 6
    assert d["associations"]["introduced"] == [] and d["associations"]["removed"] == []
    assert d["associations"]["inherited"] == [
        {"target": "DIM_A", "provider_fields": ["_a1"], "consumer_fields": ["_dimA", "_dimA2"]},
        {"target": "DIM_B", "provider_fields": ["_b1"], "consumer_fields": ["_dimB"]}]
    assert d["measures"]["added"] == ["CALC"] and d["measures"]["removed"] == []
    assert d["measures"]["aggregation_changes"] == [{"field": "AMT", "provider_aggregation": "SUM", "consumer_aggregation": None},
                                                    {"field": "QTY", "provider_aggregation": "SUM", "consumer_aggregation": None}]
    assert d["keys"] == {"provider": ["K"], "consumer": [], "both_declare_keys": False, "equal": False, "lost": ["K"], "gained": []}
    assert d["derived"]["only_in_consumer"] == ["CALC"] and d["derived"]["in_both"] == []


def test_measure_role_change_and_type_change_are_recorded_for_retained_fields():
    p = ent("P", fields=[fld("A", tipo="Decimal", longitud="13,3"), fld("B", measure=True)])
    c = ent("C", fields=[fld("A", tipo="Decimal", longitud="17,3", measure=True), fld("B")])
    d = C.edge_delta(p, c, consumer_lineage_provider_count=1)["fields"]
    assert d["measure_role_changes"] == [{"field": "A", "provider_is_measure": False, "consumer_is_measure": True},
                                         {"field": "B", "provider_is_measure": True, "consumer_is_measure": False}]
    assert d["type_changes"] == [{"field": "A", "provider_type": "Decimal", "provider_length": "13,3",
                                  "consumer_type": "Decimal", "consumer_length": "17,3"}]


def test_pass_through_descriptors_are_plain_booleans_not_a_judgment():
    p = ent("P", fields=[fld("A"), fld("B")], has_query=True)
    c = ent("C", fields=[fld("A"), fld("B")], has_query=True)
    d = C.edge_delta(p, c, consumer_lineage_provider_count=1)["descriptors"]
    assert d == {"schema_identical": True, "introduces_no_derived_field": True,
                 "introduces_no_association": True, "introduces_no_measure": True}
    c2 = with_assocs(ent("C2", fields=[fld("A"), fld("B"), fld("Z", derived=True)], has_query=True), [("_d", "D")])
    d2 = C.edge_delta(p, c2, consumer_lineage_provider_count=1)["descriptors"]
    assert d2["schema_identical"] is False and d2["introduces_no_derived_field"] is False and d2["introduces_no_association"] is False


def test_derivation_visibility_requires_a_parsed_query():
    e = m3_entities()
    assert C.derivation_visible(e["VF_M"]) is True and C.derivation_visible(e["NOQ_V"]) is False
