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
    classify_flow_kind, classify_mappings, derive_all, derive_boundary_class, derive_leaf_materialization,
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
