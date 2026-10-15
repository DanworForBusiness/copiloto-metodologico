"""Materializacion OBSERVADA de hojas locales (``leaf-materialization/v1``): derivacion pura y de solo lectura sobre evidencia canonica ya persistida.

Fixtures explicitas (dicts de evidencia con la FORMA persistida de ``leaf_provenance`` / ``processing_flow_properties`` / ``physical_source_boundaries``). Sin suite completa, sin corpus obligatorio:
el oraculo sobre sobres reales se activa con ``LEAF_MATERIALIZATION_CORPUS=<dir>/v1/<cliente>/*.json`` y se omite si no existe."""
from __future__ import annotations

import copy
import glob
import json
import os
from pathlib import Path

import pytest

from tests._judgment_cases import BENCHMARK, fresh
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment import engine as _engine, rules as _rules
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog import leaf_materialization as lm
from tools.catalog.modeling_assessment import boundary as _boundary, readers as _readers
from tools.catalog.leaf_materialization import (
    DBC_GOVERNED, DBC_MIXED, DBC_PHYSICAL, DBC_UNDETERMINED, KIND_COPY, KIND_REPLICA, KIND_TRANSFORMED, KIND_UNKNOWN,
    classify_flow_kind, classify_mappings, derive_all, derive_boundary_class, derive_leaf_materialization, explicit_delta_links, rf_task_columns,
)

DF_CONSUMER, DF_PRODUCER = "com.sap.database.table.consumer", "com.sap.database.table.producer"
PROJECTION, JOIN = "com.sap.dataflow.projection", "com.sap.dataflow.join"
CDS, STDVIEW, PHYS, UNCL = "GOVERNED_CDS_CONTRACT", "STANDARD_VIEW_ON_PHYSICAL_TABLE", "PHYSICAL_TABLE", "UNCLASSIFIED"


# ───────────────────────────── constructores de evidencia ─────────────────────────────
def q(name: str) -> str:
    return f'"{name}"'


def ident(*cols: str) -> list[dict]:
    return [{"target": c, "expression": q(c)} for c in cols]


def prod(kind: str, name: str, match: str = "EXACT", load_types=()) -> dict:
    return {"kind": kind, "name": name, "role": "target", "target_match": match, "load_types": list(load_types)}


def lp(leaf: str, producers: list[dict], basis: str = "REPLICATION_FLOW") -> dict:
    return {"leaf": leaf, "assessability": "ASSESSED", "provenance_state": "ESTABLISHED" if producers else None, "basis": basis if producers else "NONE", "producer_flows": producers,
            "upstream": [], "chain_complete": True, "declaration": None}


def rf(name: str, target: str, mappings=None, filters=None, truncate=False, load_type="REPLICATE") -> dict:
    task = {"name": "replicationtask1", "source_object": target, "target_object": target, "load_type": load_type, "truncate": truncate, "delta_disabled": None, "delta_check_interval": 3600,
            "filters": [] if filters is None else filters, "attribute_mappings": [] if mappings is None else mappings, "change_capture_properties": {}}
    return {"flow_kind": "REPLICATION_FLOW", "flow_name": name, "closure_targets": [target], "sources": [target], "targets": [target], "properties": {"tasks": [task]}}


def df(name: str, target: str, mappings, components=(DF_CONSUMER, DF_PRODUCER), source="SRC_VIEW", where=None, mode="append", upsert=True) -> dict:
    counts = [{"component": c, "count": 1} for c in components]
    nodes = [{"process": "producer1", "role": "target", "entity": target, "remote_object_type": "TABLE", "mode": mode, "upsert": upsert, "batch_size": 1000, "attribute_mappings": mappings},
             {"process": "source1", "role": "source", "entity": source, "remote_object_type": "VIEW", "mode": None, "upsert": None, "batch_size": None, "attribute_mappings": []}]
    return {"flow_kind": "DATA_FLOW", "flow_name": name, "closure_targets": [target], "sources": [source], "targets": [target],
            "properties": {"nodes": nodes, "component_counts": counts}, "source_filters": ([{"source_entity": source, "where": where, "having": None}] if where else [])}


def bnd(entity: str, remote: str, cls: str, flows: list[str], path: list[str] | None = None) -> dict:
    return {"boundary_entity": entity, "remote_connection": "S4PREM", "remote_entity": remote, "source_state": None, "boundary_class": cls, "basis": "REMOTE_ENTITY_NAMESPACE", "container": None,
            "path": path or [], "via_flows": list(flows), "plane": "PROVENANCE", "is_semantic_closure_member": False, "counts_as_direct_raw_table_access": False}


def pbl(leaf: str, boundaries: list[dict], flows: list[str], gaps=None, complete=True) -> dict:
    return {"leaf": leaf, "boundaries": boundaries, "chain_gaps": [] if gaps is None else gaps, "chain_complete": complete, "flows": list(flows)}


def ev(lps: list[dict], flows: list[dict], pbs: list[dict]) -> dict:
    return {"version": "canonical-evidence/v3", "leaf_provenance": lps, "processing_flow_properties": {"flows": flows}, "physical_source_boundaries": {"leaves": pbs}}


def one_rf(mappings=None, filters=None, truncate=False, cls=CDS, name="RF_X", leaf="T_LOCAL") -> dict:
    return ev([lp(leaf, [prod("REPLICATION_FLOW", name, load_types=["REPLICATE"])])], [rf(name, leaf, mappings, filters, truncate)], [pbl(leaf, [bnd(None, "CDS_X", cls, [name])], [name])])


def one_df(mappings, components=(DF_CONSUMER, DF_PRODUCER), where=None, bounds=None, leaf="T_LOCAL", name="DF_X", **kw) -> dict:
    return ev([lp(leaf, [prod("DATA_FLOW", name)], "DATA_FLOW")], [df(name, leaf, mappings, components, where=where, **kw)],
              [pbl(leaf, bounds if bounds is not None else [bnd("B1", "ABAP_CDS.X", CDS, [name])], [name])])


def kind_of(ce: dict, leaf: str = "T_LOCAL") -> dict:
    return derive_leaf_materialization(leaf, ce)


# ───────────────────────────── REPLICA: Replication Flow puro ─────────────────────────────
def test_pure_replication_flow_is_replica_with_governed_boundary_and_separate_attributes():
    r = kind_of(one_rf())
    assert (r["materialization_kind"], r["derived_boundary_class"], r["unknown_reasons"]) == (KIND_REPLICA, DBC_GOVERNED, [])
    assert r["flags"] == {"multi_producer": False, "filtered": False, "writes": ["no_truncate"]}
    assert r["mapping_profile"]["implicit_identity"] is True and r["chain_complete"] is True and r["version"] == lm.LEAF_MATERIALIZATION_VERSION
    assert r["producers"][0]["load_types"] == ["REPLICATE"] and r["terminal_boundaries"][0]["boundary_class"] == CDS
    assert r["evidence_refs"][:2] == ["ce:leaf_provenance/T_LOCAL", "ce:physical_source_boundaries/T_LOCAL"]


def test_explicit_identity_and_skip_mapping_keep_replica_and_are_counted_apart_from_the_implicit_default():
    r = kind_of(one_rf(ident("A", "B") + [{"target": "C", "expression": q("Skip Mapping")}]))
    assert r["materialization_kind"] == KIND_REPLICA
    assert (r["mapping_profile"]["identity"], r["mapping_profile"]["skip"], r["mapping_profile"]["implicit_identity"]) == (2, 1, False)


def test_replication_flow_filter_and_truncate_are_separate_flags_not_a_different_kind():
    r = kind_of(one_rf(filters=[{"name": "LEDGER", "elements": [{"low": "0L", "comparison": "="}]}], truncate=True))
    assert r["materialization_kind"] == KIND_REPLICA and r["flags"]["filtered"] is True and r["flags"]["writes"] == ["truncate"]


def test_a_real_rename_or_an_expression_in_a_replication_flow_is_transformed_not_replica():
    assert kind_of(one_rf([{"target": "FKDAT_2", "expression": q("FKDAT")}]))["materialization_kind"] == KIND_TRANSFORMED
    assert kind_of(one_rf([{"target": "X", "expression": "UPPER(\"X\")"}]))["materialization_kind"] == KIND_TRANSFORMED


def test_a_case_only_rename_is_not_an_identity_mapping_and_is_reported_as_such():
    r = kind_of(one_rf([{"target": "audat", "expression": q("AUDAT")}]))
    assert r["materialization_kind"] == KIND_TRANSFORMED and r["mapping_profile"]["rename"] == 1 and r["mapping_profile"]["rename_case_only"] == 1


def test_classify_mappings_distinguishes_every_mapping_class():
    p = classify_mappings([{"target": "A", "expression": q("A")}, {"target": "B", "expression": q("b")}, {"target": "C", "expression": q("Z")}, {"target": "D", "expression": q("Skip Mapping")},
                           {"target": "E", "expression": "\"A\" + \"B\""}, {"target": "F"}, {"expression": q("G")}, "basura"])
    assert (p["identity"], p["rename"], p["rename_case_only"], p["skip"], p["expression"], p["unknown"], p["implicit_identity"]) == (1, 2, 1, 1, 1, 3, False)
    assert classify_mappings([])["implicit_identity"] is True and classify_mappings(None)["implicit_identity"] is True


def test_replication_flow_without_the_mapping_property_is_unknown_not_implicit_identity():
    ce = one_rf()
    del ce["processing_flow_properties"]["flows"][0]["properties"]["tasks"][0]["attribute_mappings"]
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_MAPPING_NOT_OBSERVED]


# ───────────────────────────── multiples productores (RF + DF sobre el mismo destino) ─────────────────────────────
def two_producers(df_components, df_mappings=None, leaf="T_LOCAL") -> dict:
    mp = ident("A") if df_mappings is None else df_mappings
    return ev([lp(leaf, [prod("DATA_FLOW", "DF_X"), prod("REPLICATION_FLOW", "RF_X", load_types=["REPLICATE"])])],
              [rf("RF_X", leaf), df("DF_X", leaf, mp, df_components)],
              [pbl(leaf, [bnd("B1", "ABAP_CDS.X", CDS, ["DF_X"]), bnd(None, "CDS_X", CDS, ["RF_X"])], ["DF_X", "RF_X"])])


def test_rf_plus_df_with_projection_on_the_same_target_is_multi_producer_and_transformed():
    r = kind_of(two_producers((DF_CONSUMER, DF_PRODUCER, PROJECTION)))
    assert r["flags"]["multi_producer"] is True and r["materialization_kind"] == KIND_TRANSFORMED
    assert sorted((p["kind"], p["materialization_kind"]) for p in r["producers"]) == [("DATA_FLOW", KIND_TRANSFORMED), ("REPLICATION_FLOW", KIND_REPLICA)]


def test_rf_plus_a_pure_copy_df_is_multi_producer_copy_never_replica():
    r = kind_of(two_producers((DF_CONSUMER, DF_PRODUCER)))
    assert r["flags"]["multi_producer"] is True and r["materialization_kind"] == KIND_COPY
    assert r["flags"]["writes"] == ["append", "no_truncate"]


def test_several_replication_flows_on_one_target_cannot_be_certified_as_a_replica():
    ce = ev([lp("T_LOCAL", [prod("REPLICATION_FLOW", "RF_A"), prod("REPLICATION_FLOW", "RF_B")])], [rf("RF_A", "T_LOCAL"), rf("RF_B", "T_LOCAL")],
            [pbl("T_LOCAL", [bnd(None, "CDS_X", CDS, ["RF_A", "RF_B"])], ["RF_A", "RF_B"])])
    r = kind_of(ce)
    assert r["flags"]["multi_producer"] is True and r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_MULTI_PRODUCER_NOT_COMPARABLE]


def test_one_unresolvable_producer_makes_the_whole_leaf_unknown_and_keeps_the_per_producer_detail():
    ce = two_producers((DF_CONSUMER, DF_PRODUCER))
    ce["leaf_provenance"][0]["producer_flows"][1]["target_match"] = "DELTA_SHADOW_SUFFIX"
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_TARGET_MATCH_NOT_EXACT]
    assert r["flags"]["multi_producer"] is False                    # un solo flujo EXACT
    assert {p["name"]: p["materialization_kind"] for p in r["producers"]} == {"DF_X": KIND_COPY, "RF_X": KIND_UNKNOWN}


def test_strictly_identical_duplicate_records_count_once_but_conflicting_ones_are_ambiguous():
    ce = one_df(ident("A"))
    ce["processing_flow_properties"]["flows"].append(copy.deepcopy(ce["processing_flow_properties"]["flows"][0]))
    ce["leaf_provenance"][0]["producer_flows"].append(dict(ce["leaf_provenance"][0]["producer_flows"][0]))
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_COPY and r["flags"]["multi_producer"] is False and len(r["producers"]) == 1
    ce["processing_flow_properties"]["flows"].append(df("DF_X", "T_LOCAL", ident("A", "B")))   # mismo nombre, definicion distinta
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_FLOW_AMBIGUOUS]


# ───────────────────────────── Data Flow: COPY / TRANSFORMED ─────────────────────────────
@pytest.mark.parametrize("extra", [PROJECTION, JOIN, "com.sap.dataflow.aggregation", "com.sap.dataflow.some_future_component"])
def test_any_data_flow_component_beyond_consumer_and_producer_is_transformed(extra):
    r = kind_of(one_df(ident("A", "B"), (DF_CONSUMER, DF_PRODUCER, extra)))
    assert r["materialization_kind"] == KIND_TRANSFORMED and extra in r["producers"][0]["components"]


def test_data_flow_with_projection_stays_transformed_even_when_every_mapping_is_identity():
    r = kind_of(one_df(ident("A", "B"), (DF_CONSUMER, DF_PRODUCER, PROJECTION)))
    assert r["materialization_kind"] == KIND_TRANSFORMED and r["mapping_profile"]["identity"] == 2 and r["mapping_profile"]["rename"] == 0


def test_filtered_consumer_to_producer_data_flow_is_copy_with_filtered_true():
    where = [{"ref": ["S", "WERKS"]}, "in", {"list": [{"val": "AR01"}]}]
    r = kind_of(one_df(ident("A", "B"), where=where, mode="truncate", upsert=False))
    assert r["materialization_kind"] == KIND_COPY and r["flags"]["filtered"] is True and r["flags"]["writes"] == ["truncate"] and r["producers"][0]["upsert"] is False
    r = kind_of(one_df(ident("A", "B")))
    assert r["materialization_kind"] == KIND_COPY and r["flags"]["filtered"] is False and r["flags"]["writes"] == ["append"]


def test_copy_tolerates_skip_mapping_but_not_rename_or_expression():
    assert kind_of(one_df(ident("A") + [{"target": "B", "expression": q("Skip Mapping")}]))["materialization_kind"] == KIND_COPY
    assert kind_of(one_df(ident("A") + [{"target": "B", "expression": q("Z")}]))["materialization_kind"] == KIND_TRANSFORMED
    assert kind_of(one_df(ident("A") + [{"target": "B", "expression": "\"A\" || \"Z\""}]))["materialization_kind"] == KIND_TRANSFORMED


@pytest.mark.parametrize("mutate, reason", [
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"]["nodes"][0].update(attribute_mappings=[]), lm.U_MAPPING_NOT_OBSERVED),
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"]["nodes"][0].update(attribute_mappings=[{"target": "A"}]), lm.U_MAPPING_NOT_CLASSIFIABLE),
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"].update(component_counts=[]), lm.U_COMPONENTS_NOT_OBSERVED),
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"].pop("component_counts"), lm.U_COMPONENTS_NOT_OBSERVED),
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"].update(component_counts=[{"component": DF_CONSUMER, "count": 2}, {"component": DF_PRODUCER, "count": 1}]), lm.U_SHAPE_NOT_INTERPRETABLE),
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"]["nodes"].append(copy.deepcopy(ce["processing_flow_properties"]["flows"][0]["properties"]["nodes"][0])), lm.U_TARGET_AMBIGUOUS),
    (lambda ce: ce["processing_flow_properties"]["flows"][0]["properties"]["nodes"].pop(0), lm.U_TARGET_NOT_FOUND),
])
def test_data_flow_with_incomplete_or_unreadable_definition_is_unknown_with_a_cause_code(mutate, reason):
    ce = one_df(ident("A"))
    mutate(ce)
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [reason]
    assert r["unknown_detail"][reason] == ["DF_X"]


# ───────────────────────────── hojas fisicas por vistas intermedias / clases mixtas / cadenas incompletas ─────────────────────────────
def test_leaf_fed_through_an_intermediate_view_down_to_physical_tables_is_physical_derived():
    bounds = [bnd("1TR_FACT", "ABAPTABLES.MCHB", PHYS, ["DF_X"], ["T_LOCAL", "[DF_X]", "2VR_X", "1TR_FACT"]), bnd("DIM", "ABAPTABLES.MLGN", PHYS, ["DF_X"], ["T_LOCAL", "[DF_X]", "2VR_X", "DIM"])]
    r = kind_of(one_df(ident("A"), bounds=bounds))
    assert (r["materialization_kind"], r["derived_boundary_class"], r["boundary_reasons"]) == (KIND_COPY, DBC_PHYSICAL, [])
    assert r["chain_complete"] is True and r["composition"] == {PHYS: 2} and [b["remote_entity"] for b in r["terminal_boundaries"]] == ["ABAPTABLES.MCHB", "ABAPTABLES.MLGN"]
    assert all(b["via_flows"] == ["DF_X"] for b in r["terminal_boundaries"])


def test_governed_plus_physical_boundaries_are_mixed_with_their_composition():
    bounds = [bnd("A", "ABAPTABLES.NSDM_V_MCHB", STDVIEW, ["DF_X"]), bnd("B", "ABAPTABLES.MLGN", PHYS, ["DF_X"]), bnd("C", "ABAP_CDS.Z", CDS, ["DF_X"])]
    r = kind_of(one_df(ident("A"), bounds=bounds))
    assert r["derived_boundary_class"] == DBC_MIXED and r["composition"] == {CDS: 1, PHYS: 1, STDVIEW: 1}


def test_two_governed_families_stay_governed_derived_and_keep_both_classes_in_the_composition():
    r = kind_of(one_df(ident("A"), bounds=[bnd("A", "ABAPTABLES.NSDM_V_MCHB", STDVIEW, ["DF_X"]), bnd("C", "ABAP_CDS.Z", CDS, ["DF_X"])]))
    assert r["derived_boundary_class"] == DBC_GOVERNED and r["composition"] == {CDS: 1, STDVIEW: 1}


@pytest.mark.parametrize("kwargs, reason", [
    (dict(gaps=["OUTSIDE_POOL"]), lm.B_CHAIN_GAPS),
    (dict(complete=False), lm.B_CHAIN_INCOMPLETE),
    (dict(complete=None), lm.B_CHAIN_INCOMPLETE),
])
def test_incomplete_chain_is_undetermined_but_does_not_change_the_materialization_kind(kwargs, reason):
    ce = one_rf()
    ce["physical_source_boundaries"]["leaves"][0].update(chain_gaps=kwargs.get("gaps", []), chain_complete=kwargs.get("complete", True))
    r = kind_of(ce)
    assert r["derived_boundary_class"] == DBC_UNDETERMINED and reason in r["boundary_reasons"] and r["materialization_kind"] == KIND_REPLICA
    assert r["composition"] == {CDS: 1}                             # lo observado hasta el hueco se informa igual


def test_unclassified_unknown_or_missing_boundaries_are_undetermined():
    assert kind_of(one_rf(cls=UNCL))["boundary_reasons"] == [lm.B_UNCLASSIFIED]
    assert kind_of(one_rf(cls="CLASE_INVENTADA"))["derived_boundary_class"] == DBC_UNDETERMINED
    ce = one_rf()
    ce["physical_source_boundaries"]["leaves"][0]["boundaries"] = []
    assert kind_of(ce)["boundary_reasons"] == [lm.B_NO_BOUNDARIES]
    ce = one_rf()
    ce["physical_source_boundaries"]["leaves"] = []
    r = kind_of(ce)
    assert (r["derived_boundary_class"], r["boundary_reasons"], r["materialization_kind"]) == (DBC_UNDETERMINED, [lm.B_NO_RECORD], KIND_REPLICA)


def test_derive_boundary_class_accepts_governed_sets_and_never_raises_on_garbage():
    assert derive_boundary_class([{"boundary_class": "GOVERNED_SAPI_EXTRACTOR"}, {"boundary_class": "BW_BRIDGE"}], [], True)[0] == DBC_GOVERNED
    assert derive_boundary_class(None, None, None)[0] == DBC_UNDETERMINED
    assert derive_boundary_class("x", 3, True)[0] == DBC_UNDETERMINED
    assert derive_boundary_class([{"boundary_class": CDS}], [], True, governed_classes=frozenset())[0] == DBC_UNDETERMINED


# ───────────────────────────── relaciones incompletas, no EXACT o contradictorias ─────────────────────────────
def test_producer_with_non_exact_target_match_is_unknown_even_if_a_flow_with_that_name_exists():
    ce = one_rf()
    ce["leaf_provenance"][0]["producer_flows"][0]["target_match"] = "DELTA_SHADOW_SUFFIX"
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_TARGET_MATCH_NOT_EXACT] and r["unknown_detail"][lm.U_TARGET_MATCH_NOT_EXACT] == ["RF_X:DELTA_SHADOW_SUFFIX"]
    assert r["derived_boundary_class"] == DBC_GOVERNED              # la clase de borde es otra pregunta


def test_producer_flow_missing_from_processing_evidence_or_with_unsupported_kind_is_unknown():
    ce = one_rf()
    ce["processing_flow_properties"]["flows"] = []
    assert kind_of(ce)["unknown_reasons"] == [lm.U_FLOW_NOT_IN_EVIDENCE]
    ce = one_rf()
    ce["processing_flow_properties"]["flows"][0]["flow_kind"] = "TRANSFORMATION_FLOW"
    ce["leaf_provenance"][0]["producer_flows"][0]["kind"] = "TRANSFORMATION_FLOW"
    assert kind_of(ce)["unknown_reasons"] == [lm.U_FLOW_KIND_UNSUPPORTED]


def test_replication_flow_task_with_a_different_or_duplicated_target_is_unknown():
    ce = one_rf()
    ce["processing_flow_properties"]["flows"][0]["properties"]["tasks"][0]["target_object"] = "OTRA"
    assert kind_of(ce)["unknown_reasons"] == [lm.U_TARGET_NOT_FOUND]
    ce = one_rf()
    t = ce["processing_flow_properties"]["flows"][0]["properties"]["tasks"]
    t.append(dict(t[0], name="replicationtask2"))
    assert kind_of(ce)["unknown_reasons"] == [lm.U_TARGET_AMBIGUOUS]


def test_producers_that_the_boundary_record_does_not_walk_are_contradictory_evidence():
    ce = one_rf()
    ce["physical_source_boundaries"]["leaves"][0]["flows"] = ["RF_OTRO"]
    r = kind_of(ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_CONTRADICTORY] and r["unknown_detail"][lm.U_CONTRADICTORY] == ["producers_not_in_boundary_flows:RF_X"]


def test_leaf_without_producer_or_with_declared_provenance_is_unknown_never_assumed_good():
    ce = ev([lp("T_LOCAL", [])], [], [])
    assert kind_of(ce)["unknown_reasons"] == [lm.U_NO_PRODUCER]
    ce["leaf_provenance"][0]["basis"] = "DECLARED_DATASET"
    r = kind_of(ce)
    assert r["unknown_reasons"] == [lm.U_DECLARED_NOT_OBSERVED] and r["derived_boundary_class"] == DBC_UNDETERMINED
    r = kind_of(ce, "NO_EXISTE")
    assert r["unknown_reasons"] == [lm.U_LEAF_NOT_FOUND]


# ───────────────────────────── ausencia de inferencias por nombre ─────────────────────────────
def test_names_never_create_or_alter_a_relation():
    # dos hojas de nombres casi iguales; solo una tiene productor. La otra NO hereda nada por semejanza ni por sufijo.
    ce = ev([lp("ORDERS", []), lp("ORDERS_H", [prod("REPLICATION_FLOW", "RF_ORDERS")]), lp("ORDERS_Delta", [])], [rf("RF_ORDERS", "ORDERS_H")],
            [pbl("ORDERS_H", [bnd(None, "CDS_O", CDS, ["RF_ORDERS"])], ["RF_ORDERS"])])
    out = derive_all(ce)
    assert out["ORDERS_H"]["materialization_kind"] == KIND_REPLICA
    for other in ("ORDERS", "ORDERS_Delta"):
        assert out[other]["materialization_kind"] == KIND_UNKNOWN and out[other]["unknown_reasons"] == [lm.U_NO_PRODUCER] and out[other]["producers"] == []
    # el nombre del flujo no decide el tipo: un "RF_..._REPLICA" con projection es TRANSFORMED y un "DF_PROJECTION_JOIN" que es un Replication Flow puro es REPLICA
    r = kind_of(one_df(ident("A"), (DF_CONSUMER, DF_PRODUCER, PROJECTION), name="RF_REPLICA_COPY"))
    assert r["materialization_kind"] == KIND_TRANSFORMED
    r = kind_of(one_rf(name="DF_PROJECTION_JOIN"))
    assert r["materialization_kind"] == KIND_REPLICA
    # un flujo cuyo destino es OTRA tabla no se asocia a la hoja por llamarse parecido
    ce = one_rf(name="DF_T_LOCAL", leaf="T_LOCAL")
    ce["processing_flow_properties"]["flows"][0]["properties"]["tasks"][0]["target_object"] = "T_LOCAL_COPY"
    assert kind_of(ce)["unknown_reasons"] == [lm.U_TARGET_NOT_FOUND]


# ───────────────────────────── perfiles historicos y entradas anomalas ─────────────────────────────
@pytest.mark.parametrize("ce", [None, {}, [], "x", 3, {"version": "canonical-evidence/v1"}, {"closure": {}, "view_complexity": {}},
                                {"leaf_provenance": [], "processing_flow_properties": {"flows": []}},                 # v2 sin bordes fisicos
                                {"leaf_provenance": "x", "processing_flow_properties": [], "physical_source_boundaries": None}])
def test_historical_or_malformed_evidence_yields_unknown_without_raising(ce):
    r = derive_leaf_materialization("T_LOCAL", ce)
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] and r["derived_boundary_class"] == DBC_UNDETERMINED
    assert r["unknown_reasons"][0] in (lm.U_EVIDENCE_NOT_AVAILABLE, lm.U_SECTION_MISSING)
    assert derive_all(ce) == {}


def test_garbage_inside_the_sections_never_raises():
    ce = ev([lp("T_LOCAL", [prod("DATA_FLOW", "DF_X")]), "x", None, {"leaf": 3}], [df("DF_X", "T_LOCAL", "no-es-lista"), "x", {"flow_kind": 1}, None],
            [{"leaf": "T_LOCAL", "boundaries": "no-es-lista", "chain_gaps": 5, "chain_complete": "si", "flows": None}, 7])
    out = derive_all(ce)
    assert out["T_LOCAL"]["materialization_kind"] == KIND_UNKNOWN
    ce["leaf_provenance"][0]["producer_flows"] = [None, "x", {"kind": 2, "name": ["z"]}]
    assert derive_leaf_materialization("T_LOCAL", ce)["materialization_kind"] == KIND_UNKNOWN
    assert classify_flow_kind(None, "X")["materialization_kind"] == KIND_UNKNOWN and classify_flow_kind({"flow_kind": "DATA_FLOW", "properties": "x"}, "X")["unknown_reasons"] == [lm.U_TARGET_NOT_FOUND]


def test_derivation_is_deterministic_independent_of_input_order_and_never_mutates_the_input():
    ce = two_producers((DF_CONSUMER, DF_PRODUCER, PROJECTION))
    snapshot = copy.deepcopy(ce)
    a = derive_all(ce)
    assert ce == snapshot and derive_all(ce) == a
    shuffled = copy.deepcopy(ce)
    shuffled["leaf_provenance"][0]["producer_flows"].reverse()
    shuffled["processing_flow_properties"]["flows"].reverse()
    shuffled["physical_source_boundaries"]["leaves"][0]["boundaries"].reverse()
    assert json.dumps(derive_all(shuffled), sort_keys=True) == json.dumps(a, sort_keys=True)
    json.dumps(a)                                                   # JSON-serializable: lista para persistirse en un incremento posterior


def test_output_contract_invariants_hold_for_every_scenario_in_this_module():
    scenarios = [one_rf(), one_rf(cls=UNCL), one_df(ident("A")), one_df(ident("A"), (DF_CONSUMER, DF_PRODUCER, PROJECTION)), two_producers((DF_CONSUMER, DF_PRODUCER)), ev([lp("T_LOCAL", [])], [], [])]
    keys = None
    for ce in scenarios:
        r = kind_of(ce)
        assert r["materialization_kind"] in lm.MATERIALIZATION_KINDS and r["derived_boundary_class"] in lm.DERIVED_BOUNDARY_CLASSES
        assert (r["materialization_kind"] == KIND_UNKNOWN) == bool(r["unknown_reasons"]) and set(r["unknown_reasons"]) <= lm.UNKNOWN_REASON_CODES
        keys = keys or set(r)
        assert set(r) == keys and set(r["flags"]) == {"multi_producer", "filtered", "writes"}


def test_the_neutral_module_imports_neither_package_and_its_boundary_literals_match_the_source_of_truth():
    src = Path(lm.__file__).read_text(encoding="utf-8")
    code = "; ".join(l for l in src.splitlines() if l.lstrip().startswith(("import ", "from ")))
    assert "methodological_judgment" not in code and "modeling_assessment" not in code and "boundary" not in code
    for const in ("PHYSICAL_TABLE", "GOVERNED_CDS", "GOVERNED_SAPI", "STANDARD_VIEW_ON_PHYSICAL", "BW_BRIDGE", "UNCLASSIFIED"):
        assert getattr(lm, f"BC_{const}") == getattr(_boundary, f"BC_{const}"), const
    assert (lm.DATA_FLOW, lm.REPLICATION_FLOW) == (_readers.DATA_FLOW, _readers.REPLICATION_FLOW)


# ───────────────────────────── evidencia CSN explicita: vinculos delta, identidad implicita y equivalencia solo-mayusculas ─────────────────────────────
def rf_to(name: str, leaf: str, target: str, mappings=None, cdc=None, extra_tasks=()) -> dict:
    f = rf(name, target, mappings)
    f["properties"]["tasks"][0]["change_capture_properties"] = cdc or {}
    f["properties"]["tasks"].extend(extra_tasks)
    f["closure_targets"] = f["targets"] = [target]
    return f


def delta_ce(match="DELTA_SHADOW_SUFFIX", mappings=None, cdc=None, bclass=CDS, leaf="X", shadow="X_Delta", extra_tasks=()) -> dict:
    return ev([lp(leaf, [prod("REPLICATION_FLOW", "RF_X", match=match, load_types=["REPLICATE"])])], [rf_to("RF_X", leaf, shadow, mappings, cdc, extra_tasks)],
              [pbl(leaf, [bnd(None, "CDS_X", bclass, ["RF_X"])], ["RF_X"])])


def test_delta_shadow_producer_stays_unknown_without_an_explicit_csn_link_even_if_the_suffix_matches():
    r = derive_leaf_materialization("X", delta_ce())
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_TARGET_MATCH_NOT_EXACT] and r["unknown_detail"][lm.U_TARGET_MATCH_NOT_EXACT] == ["RF_X:DELTA_SHADOW_SUFFIX"]
    for csn in ({}, {"delta_links": {}}, {"delta_links": {"OTRA": ("X_Delta",)}}, {"delta_links": {"X": ()}}):
        assert derive_leaf_materialization("X", delta_ce(), csn)["unknown_reasons"] == [lm.U_TARGET_MATCH_NOT_EXACT], csn


def test_explicit_csn_delta_link_resolves_the_producer_and_records_how():
    r = derive_leaf_materialization("X", delta_ce(), {"delta_links": {"X": ("X_Delta",)}})
    assert (r["materialization_kind"], r["unknown_reasons"], r["flags"]["multi_producer"]) == (KIND_REPLICA, [], False)
    p = r["producers"][0]
    assert (p["resolved_via"], p["shadow_entity"], p["target_match"]) == (lm.RESOLVED_VIA_DELTA_LINK, "X_Delta", "DELTA_SHADOW_SUFFIX")      # el target_match persistido no se reescribe
    assert derive_all(delta_ce(), {"delta_links": {"X": ["X_Delta"]}})["X"]["materialization_kind"] == KIND_REPLICA


def test_the_link_decides_not_the_suffix_the_declared_companion_must_be_the_exact_target_of_the_task():
    ce = delta_ce(shadow="X_Delta")
    r = derive_leaf_materialization("X", ce, {"delta_links": {"X": ("OTRA_COMPANERA",)}})              # el sufijo coincide pero el CSN declara otra companera
    assert r["materialization_kind"] == KIND_UNKNOWN and r["unknown_reasons"] == [lm.U_CONTRADICTORY]
    assert r["unknown_detail"][lm.U_CONTRADICTORY] == ["delta_link_does_not_cover_producer_target:RF_X"]
    # un destino SIN sufijo _Delta se resuelve igual si y solo si el CSN lo declara (el nombre es irrelevante)
    ce = delta_ce(shadow="TECH_STORE_X")
    assert derive_leaf_materialization("X", ce, {"delta_links": {"X": ("TECH_STORE_X",)}})["materialization_kind"] == KIND_REPLICA


def test_link_covering_several_task_targets_is_ambiguous_and_a_link_never_applies_to_data_flows_or_exact_matches():
    other = rf("RF_X", "X_Delta2")["properties"]["tasks"][0]
    r = derive_leaf_materialization("X", delta_ce(extra_tasks=[other]), {"delta_links": {"X": ("X_Delta", "X_Delta2")}})
    assert r["unknown_reasons"] == [lm.U_TARGET_AMBIGUOUS]
    ce = one_df(ident("A"), name="DF_X", leaf="X")
    ce["leaf_provenance"][0]["producer_flows"][0]["target_match"] = "DELTA_SHADOW_SUFFIX"
    assert derive_leaf_materialization("X", ce, {"delta_links": {"X": ("T",)}})["unknown_reasons"] == [lm.U_TARGET_MATCH_NOT_EXACT]
    r = derive_leaf_materialization("T_LOCAL", one_rf(), {"delta_links": {"T_LOCAL": ("T_LOCAL_Delta",)}})
    assert r["materialization_kind"] == KIND_REPLICA and r["producers"][0]["resolved_via"] is None   # EXACT: el vinculo no interviene


def test_resolved_delta_producer_counts_for_multi_producer_and_still_cannot_be_certified_replica_with_a_second_replicator():
    ce = delta_ce()
    ce["leaf_provenance"][0]["producer_flows"].append(prod("REPLICATION_FLOW", "RF_Y"))
    ce["processing_flow_properties"]["flows"].append(rf("RF_Y", "X"))
    ce["physical_source_boundaries"]["leaves"][0]["flows"] = ["RF_X", "RF_Y"]
    r = derive_leaf_materialization("X", ce, {"delta_links": {"X": ("X_Delta",)}})
    assert r["flags"]["multi_producer"] is True and r["unknown_reasons"] == [lm.U_MULTI_PRODUCER_NOT_COMPARABLE]


def test_explicit_delta_links_reads_only_the_declared_annotation():
    defs = {"X": {"@DataWarehouse.delta": {"deltaFromEntities": ["X_Delta", "X", 3, ""]}}, "Y": {"@DataWarehouse.delta": {"type": "ACTIVE"}}, "Y_Delta": {}, "Z_Delta": {"kind": "entity"},
            "W": {"@DataWarehouse.delta": {"deltaFromEntities": "no-lista"}}, "V": "x"}
    assert explicit_delta_links(defs) == {"X": ("X_Delta",)}                  # ni Y/Y_Delta ni Z_Delta por sufijo; sin autoenlaces ni basura
    assert explicit_delta_links(None) == {} and explicit_delta_links([1]) == {}


def raw_rf(flow="RF_X", target="X", src=("A", "B"), tgt=("A", "B"), maps=()) -> dict:
    col = lambda names: {"definition": {"columns": [{"name": n} for n in names]}}      # noqa: E731
    return {flow: {"contents": {"replicationTasks": [{"name": "t", "sourceObject": dict(col(src), name="S"), "targetObject": dict(col(tgt), name=target),
                                                     "transform": {"attributeMappings": list(maps)}}]}}}


def test_rf_task_columns_extracts_unambiguous_column_evidence_and_omits_the_rest():
    assert rf_task_columns(raw_rf()) == {("RF_X", "X"): {"source_columns": ["A", "B"], "target_columns": ["A", "B"]}}
    assert rf_task_columns(raw_rf(src=())) == {} and rf_task_columns(None) == {} and rf_task_columns({"RF": "x"}) == {}
    both = raw_rf()
    both["RF_X"]["contents"]["replicationTasks"].append(copy.deepcopy(both["RF_X"]["contents"]["replicationTasks"][0]))
    assert ("RF_X", "X") in rf_task_columns(both)                             # duplicado identico: una sola entrada
    both["RF_X"]["contents"]["replicationTasks"][1]["sourceObject"]["definition"]["columns"].append({"name": "C"})
    assert rf_task_columns(both) == {}                                        # misma clave, contenido distinto: ambigua, se omite


def cols_csn(src, tgt, flow="RF_X", target="T_LOCAL"):
    return {"rf_task_columns": {(flow, target): {"source_columns": list(src), "target_columns": list(tgt)}}}


def test_column_evidence_verifies_the_implicit_identity_per_task_and_the_default_contract_still_applies_without_it():
    plain = kind_of(one_rf())
    assert plain["materialization_kind"] == KIND_REPLICA and plain["mapping_profile"]["column_evidence"] == lm.COLUMN_EVIDENCE_NOT_PROVIDED and plain["mapping_profile"]["implicit_identity"] is True
    r = derive_leaf_materialization("T_LOCAL", one_rf(), cols_csn(["A", "B", "C"], ["A", "B"]))              # seleccion de columnas del RF: sin perdida de valores
    assert r["materialization_kind"] == KIND_REPLICA and r["mapping_profile"]["column_evidence"] == lm.COLUMN_EVIDENCE_VERIFIED and r["mapping_profile"]["implicit_identity"] is True


def test_cdc_and_skipped_target_columns_are_explained_but_an_unexplained_target_column_contradicts_the_implicit_identity():
    ce = one_rf([{"target": "FKDAT", "expression": q("Skip Mapping")}])
    ce["processing_flow_properties"]["flows"][0]["properties"]["tasks"][0]["change_capture_properties"] = {"com.sap.datasuite.cdc.mode.column": "Change_Type"}
    ok = derive_leaf_materialization("T_LOCAL", ce, cols_csn(["A"], ["A", "FKDAT", "Change_Type"]))
    assert ok["materialization_kind"] == KIND_REPLICA and ok["mapping_profile"]["column_evidence"] == lm.COLUMN_EVIDENCE_VERIFIED
    bad = derive_leaf_materialization("T_LOCAL", one_rf(), cols_csn(["A"], ["A", "INVENTADA"]))
    assert bad["materialization_kind"] == KIND_UNKNOWN and bad["unknown_reasons"] == [lm.U_IMPLICIT_IDENTITY_CONTRADICTED]
    assert bad["unknown_detail"][lm.U_IMPLICIT_IDENTITY_CONTRADICTED] == ["RF_X:target_column_without_origin:INVENTADA"] and bad["mapping_profile"]["column_evidence"] == lm.COLUMN_EVIDENCE_CONTRADICTED
    missing = derive_leaf_materialization("T_LOCAL", one_rf(ident("Z")), cols_csn(["A"], ["A", "Z"]))        # un mapeo identidad cuyo origen no existe
    assert missing["unknown_reasons"] == [lm.U_IMPLICIT_IDENTITY_CONTRADICTED]


def case_ce(bclass=CDS):
    return one_rf([{"target": "audat", "expression": q("AUDAT")}], cls=bclass)


def test_case_only_rename_is_identity_normalized_only_with_verified_columns_and_a_cds_source():
    csn = cols_csn(["AUDAT", "B"], ["audat", "B"])
    r = derive_leaf_materialization("T_LOCAL", case_ce(), csn)
    mp = r["mapping_profile"]
    assert r["materialization_kind"] == KIND_REPLICA and (mp["identity_normalized"], mp["rename"], mp["rename_case_only"], mp["identity"]) == (1, 0, 1, 0)
    # sin evidencia de columnas: sigue siendo renombre (conservador) y rename_case_only se preserva
    r = derive_leaf_materialization("T_LOCAL", case_ce())
    assert r["materialization_kind"] == KIND_TRANSFORMED and (r["mapping_profile"]["identity_normalized"], r["mapping_profile"]["rename"], r["mapping_profile"]["rename_case_only"]) == (0, 1, 1)
    # origen que NO es una CDS gobernada: la premisa de identificadores no se cumple
    for cls in (PHYS, STDVIEW):
        r = derive_leaf_materialization("T_LOCAL", case_ce(cls), csn)
        assert r["materialization_kind"] == KIND_TRANSFORMED and r["mapping_profile"]["identity_normalized"] == 0, cls


@pytest.mark.parametrize("src, tgt", [(["AUDAT", "Audat"], ["audat"]),            # colision de mayusculas en el origen
                                      (["AUDAT"], ["audat", "AUDat"]),            # colision en el destino
                                      (["audat"], ["audat"]),                     # el origen mapeado no existe EXACTAMENTE
                                      (["AUDAT"], ["otra"])])                     # el destino mapeado no existe
def test_collisions_or_missing_exact_names_prevent_the_case_equivalence(src, tgt):
    r = derive_leaf_materialization("T_LOCAL", case_ce(), cols_csn(src, tgt))
    assert r["materialization_kind"] == KIND_TRANSFORMED and r["mapping_profile"]["identity_normalized"] == 0 and r["mapping_profile"]["rename"] == 1 and r["mapping_profile"]["rename_case_only"] == 1


def test_there_is_no_universal_casefold_only_verified_case_only_pairs_are_normalized_and_data_flows_never_are():
    mixed = [{"target": "audat", "expression": q("AUDAT")}, {"target": "fkdat_2", "expression": q("FKDAT")}]       # el segundo es un renombre real
    r = derive_leaf_materialization("T_LOCAL", one_rf(mixed), cols_csn(["AUDAT", "FKDAT"], ["audat", "fkdat_2"]))
    assert r["materialization_kind"] == KIND_TRANSFORMED and (r["mapping_profile"]["identity_normalized"], r["mapping_profile"]["rename"]) == (1, 1)
    ce = one_df([{"target": "vbeln", "expression": q("VBELN")}], bounds=[bnd("B", "ABAP_CDS.X", CDS, ["DF_X"])])
    r = derive_leaf_materialization("T_LOCAL", ce, cols_csn(["VBELN"], ["vbeln"], flow="DF_X"))
    assert r["materialization_kind"] == KIND_TRANSFORMED and r["mapping_profile"]["identity_normalized"] == 0 and r["mapping_profile"]["rename_case_only"] == 1
    p = classify_mappings([{"target": "a", "expression": q("A")}], frozenset({("a", "A")}))
    assert (p["identity_normalized"], p["rename"], p["rename_case_only"]) == (1, 0, 1)
    p = classify_mappings([{"target": "a", "expression": q("A")}], frozenset({("b", "B")}))
    assert (p["identity_normalized"], p["rename"], p["rename_case_only"]) == (0, 1, 1)


def test_transformed_is_technical_and_nothing_in_the_record_claims_semantic_harmonization():
    r = kind_of(one_df(ident("A"), (DF_CONSUMER, DF_PRODUCER, PROJECTION)))
    assert r["materialization_kind"] == KIND_TRANSFORMED
    assert not any("harmon" in k.lower() or "strength" in k.lower() or "har_" in k.lower() for k in r)       # sin campos de armonizacion/fortaleza
    assert "TECNICA" in (lm.__doc__ or "").upper() or "TECHNICAL" in (lm.__doc__ or "").upper()


def test_csn_evidence_never_changes_the_default_output_for_leaves_it_does_not_cover():
    for ce in (one_rf(), one_df(ident("A")), two_producers((DF_CONSUMER, DF_PRODUCER))):
        base = derive_all(ce)
        with_csn = derive_all(ce, {"delta_links": {"OTRA": ("D",)}, "rf_task_columns": {("RF_OTRO", "OTRA"): {"source_columns": ["A"], "target_columns": ["A"]}}})
        norm = lambda d: json.loads(json.dumps(d))                                                        # noqa: E731
        assert norm(base) == norm(with_csn)



# ───────────────────────────── accesor de lectura y no regresion del Juicio ─────────────────────────────
def test_profile_evidence_exposes_materialization_read_only_and_historical_profiles_give_empty():
    p = {"canonical_evidence": one_rf()}
    snapshot = copy.deepcopy(p)
    pe = ProfileEvidence(p)
    assert pe.materialization_of("T_LOCAL")["materialization_kind"] == KIND_REPLICA and pe.materialization_of("NO_EXISTE") is None and set(pe.materialization) == {"T_LOCAL"}
    assert p == snapshot
    assert ProfileEvidence({}).materialization == {} and ProfileEvidence({"canonical_evidence": {"version": "canonical-evidence/v1"}}).materialization == {}


@pytest.mark.parametrize("alias", sorted(BENCHMARK))
def test_judgment_output_and_signature_are_unchanged_by_deriving_the_materialization(alias):
    """El Juicio NO consume la derivacion: derivarla antes sobre el mismo perfil (y consultarla via el accesor) no cambia ni un byte de la salida ni su firma, ni muta el perfil."""
    p0 = fresh(alias)
    baseline = evaluate_judgment(copy.deepcopy(p0))
    p1 = copy.deepcopy(p0)
    pe = ProfileEvidence(p1)
    derived = pe.materialization
    assert p1 == p0 and isinstance(derived, dict)
    after = evaluate_judgment(p1)
    assert after == baseline and after["output_signature"] == baseline["output_signature"]


def test_no_rule_or_engine_code_references_the_materialization():
    for mod in (_rules, _engine):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert not any(tok in src for tok in ("leaf_materialization", "materialization_of", ".materialization", "derive_all")), mod.__name__


@pytest.mark.parametrize("alias", sorted(BENCHMARK))
def test_derivation_is_total_over_the_frozen_golden_profiles(alias):
    ce = fresh(alias).get("canonical_evidence") or {}
    for leaf, r in derive_all(ce).items():
        assert r["leaf"] == leaf and r["materialization_kind"] in lm.MATERIALIZATION_KINDS and set(r["unknown_reasons"]) <= lm.UNKNOWN_REASON_CODES
        assert (r["materialization_kind"] == KIND_UNKNOWN) == bool(r["unknown_reasons"])


# ───────────────────────────── oraculo opcional sobre sobres reales ─────────────────────────────
_CORPUS = os.environ.get("LEAF_MATERIALIZATION_CORPUS", "")
_SCC = {DBC_GOVERNED: {"GOVERNED_SAP_CDS_CONTRACT", "GOVERNED_SAP_ODP_SAPI_CONTRACT", "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE", "SAP_BW_BRIDGE_VIRTUALIZATION"},
        DBC_PHYSICAL: {"PHYSICAL_ABAP_TABLE_BOUNDARY"}, DBC_MIXED: {"MIXED_BOUNDARY_CLASSES"}}


@pytest.mark.skipif(not _CORPUS or not glob.glob(os.path.join(_CORPUS, "v1", "*", "*.json")), reason="LEAF_MATERIALIZATION_CORPUS no definido o sin sobres")
def test_oracle_derived_boundary_class_matches_the_persisted_source_contract_class_on_real_envelopes():
    checked = 0
    for f in sorted(glob.glob(os.path.join(_CORPUS, "v1", "*", "*.json"))):
        ce = json.loads(Path(f).read_text(encoding="utf-8"))["profile_json"]["canonical_evidence"]
        before = json.dumps(ce, sort_keys=True)
        scc = {r["leaf"]: r["contract_class"] for r in ce["source_contract_classes"]["leaves"]}
        out = derive_all(ce)
        assert json.dumps(ce, sort_keys=True) == before
        for leaf in {r["leaf"] for r in ce["physical_source_boundaries"]["leaves"]}:
            r = out[leaf]
            expected = _SCC.get(r["derived_boundary_class"])
            if expected is None or leaf not in scc:
                continue
            checked += 1
            # source_contract_classes llama MIXED a ">1 clase de borde" (tambien dos familias gobernadas); aqui MIXED = gobernado + fisico y la composicion conserva ambas clases
            assert scc[leaf] in expected or (r["derived_boundary_class"] == DBC_GOVERNED and scc[leaf] == "MIXED_BOUNDARY_CLASSES" and len(r["composition"]) >= 2), (f, leaf, r["derived_boundary_class"], scc[leaf])
    assert checked > 0


_CSN = os.environ.get("LEAF_MATERIALIZATION_CSN", "")


def _csn_for(client: str) -> dict:
    links, cols = {}, {}
    for jf in glob.glob(os.path.join(_CSN, client, "**", "*.json"), recursive=True):
        try:
            doc = json.loads(Path(jf).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            links.update(explicit_delta_links(doc.get("definitions")))
            cols.update(rf_task_columns(doc.get("replicationflows")))
    return {"delta_links": links, "rf_task_columns": cols}


@pytest.mark.skipif(not (_CORPUS and _CSN) or not glob.glob(os.path.join(_CORPUS, "v1", "*", "*.json")), reason="LEAF_MATERIALIZATION_CORPUS / LEAF_MATERIALIZATION_CSN no definidos")
def test_oracle_with_explicit_csn_evidence_resolves_every_delta_shadow_leaf_and_contradicts_no_replication_flow():
    resolved = verified = 0
    for f in sorted(glob.glob(os.path.join(_CORPUS, "v1", "*", "*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        ce = d["profile_json"]["canonical_evidence"]
        csn = _csn_for(d["client"])
        base, out = derive_all(ce), derive_all(ce, csn)
        for leaf, r in out.items():
            for p in r["producers"]:
                assert p["mapping_profile"]["column_evidence"] != lm.COLUMN_EVIDENCE_CONTRADICTED, (f, leaf, p["name"])      # el contrato "anulaciones + identidad por nombre" se sostiene
                verified += p["mapping_profile"]["column_evidence"] == lm.COLUMN_EVIDENCE_VERIFIED
                resolved += p["resolved_via"] == lm.RESOLVED_VIA_DELTA_LINK
            if lm.U_TARGET_MATCH_NOT_EXACT in base[leaf]["unknown_reasons"]:
                assert lm.U_TARGET_MATCH_NOT_EXACT not in r["unknown_reasons"], (f, leaf)         # todo vinculo por sufijo del corpus esta respaldado por una declaracion CSN explicita
    assert resolved > 0 and verified > 0
