"""
Evidence release v3 — linaje de campo publicado (``am.published_field_lineage``) y clase de contrato de fuente por hoja (``source.contract_class``).

Disciplina: HECHOS de procedencia, sin juicio; ambiguedad EXPLICITA (nunca se elige un proveedor); independiente de como se empaquete el export (entidad embebida en otro
export / exportada aparte / tabla local definida dentro de un flujo).
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import evidence_contract as EC
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.evidence_errors import CanonicalEvidenceError
from tools.catalog.modeling_assessment.field_lineage import (
    AMB_NO_OUTPUT_FIELD, AMB_SET_ARITY, AMB_STAR, AMB_SUBQUERY, AMB_UNQUALIFIED_MULTI, AMB_UNRESOLVED_ALIAS, MT_CAST, MT_DERIVED, MT_EXPRESSION, MT_IDENTITY, MT_RENAME,
    extract_query_ir, resolve_query,
)

AM = "4AM_X"
ALL_PRODUCERS = ["data-flows", "replication-flows", "transformation-flows"]
SCOPE_ALL = {"effective_features": ALL_PRODUCERS, "requested_features": ALL_PRODUCERS}
STR = {"type": "cds.String"}


# ═══════════════════════ 1. resolucion de columnas sobre el query compilado (sin pool) ═══════════════════════
def _fields_of(table: dict[str, tuple[str, ...]]):
    return lambda n: table.get(n)


def _sel(frm, cols):
    return {"SELECT": {"from": frm, "columns": cols}}


def _res(definition: dict, table: dict | None = None):
    return resolve_query(extract_query_ir({"query": definition}), _fields_of(table or {}))


def _o(r, name):
    return [(o.provider, o.field, o.mapping_type, o.ambiguity) for o in r[name]]


def test_simple_references_are_identity_rename_or_cast():
    r = _res(_sel({"ref": ["S"]}, [{"ref": ["S", "A"]}, {"ref": ["S", "B"], "as": "B2"}, {"ref": ["S", "C"], "as": "C", "cast": {"type": "cds.Integer"}}]), {"S": ("A", "B", "C")})
    assert _o(r, "A") == [("S", "A", MT_IDENTITY, None)] and _o(r, "B2") == [("S", "B", MT_RENAME, None)] and _o(r, "C") == [("S", "C", MT_CAST, None)]


def test_expressions_are_derived_with_one_input_and_expression_with_several():
    r = _res(_sel({"ref": ["S"]}, [{"func": "UPPER", "args": [{"ref": ["S", "A"]}], "as": "U"}, {"xpr": [{"ref": ["S", "A"]}, "*", {"ref": ["S", "B"]}], "as": "AB"},
                                  {"val": 1, "as": "ONE"}]), {"S": ("A", "B")})
    assert _o(r, "U") == [("S", "A", MT_DERIVED, None)]
    assert sorted(_o(r, "AB")) == [("S", "A", MT_EXPRESSION, None), ("S", "B", MT_EXPRESSION, None)]
    assert _o(r, "ONE") == [(None, None, MT_DERIVED, None)]                                       # constante: sin insumo de campo y sin ambiguedad


def test_nested_subselects_compose_into_one_hop_with_the_strongest_mapping():
    inner = _sel({"ref": ["S"]}, [{"ref": ["S", "A"], "as": "A1"}, {"func": "UPPER", "args": [{"ref": ["S", "B"]}], "as": "B1"}])
    outer = _sel({**inner, "as": "Node 1"}, [{"ref": ["Node 1", "A1"], "as": "A"}, {"ref": ["Node 1", "B1"], "as": "B"}])
    r = _res(outer, {"S": ("A", "B")})
    assert _o(r, "A") == [("S", "A", MT_RENAME, None)] and _o(r, "B") == [("S", "B", MT_DERIVED, None)]


def test_an_unqualified_reference_is_resolved_only_when_exactly_one_source_exposes_the_field():
    frm = {"join": "inner", "args": [{"ref": ["S1"]}, {"ref": ["S2"]}], "on": []}
    r = _res(_sel(frm, [{"ref": ["ONLY1"]}, {"ref": ["BOTH"]}]), {"S1": ("ONLY1", "BOTH"), "S2": ("BOTH",)})
    assert _o(r, "ONLY1") == [("S1", "ONLY1", MT_IDENTITY, None)]
    assert _o(r, "BOTH") == [(None, None, "UNKNOWN", AMB_UNQUALIFIED_MULTI)]                    # dos fuentes la exponen: NO se elige una


def test_an_unqualified_reference_stays_ambiguous_when_a_source_is_unknown():
    frm = {"join": "inner", "args": [{"ref": ["S1"]}, {"ref": ["UNKNOWN_ENTITY"]}], "on": []}
    r = _res(_sel(frm, [{"ref": ["A"]}]), {"S1": ("A",)})
    assert _o(r, "A") == [(None, None, "UNKNOWN", AMB_UNQUALIFIED_MULTI)]


def test_ambiguity_codes_for_alias_subquery_output_and_star():
    r = _res(_sel({"ref": ["S"]}, [{"ref": ["NOPE", "A"], "as": "X"}, {"xpr": [{"SELECT": {"from": {"ref": ["S"]}, "columns": [{"ref": ["A"]}]}}], "as": "Y"}]), {"S": ("A",)})
    assert _o(r, "X") == [(None, None, "UNKNOWN", AMB_UNRESOLVED_ALIAS)] and (None, None, "UNKNOWN", AMB_SUBQUERY) in _o(r, "Y")
    sub = _sel({"ref": ["S"]}, [{"ref": ["S", "A"]}])
    r2 = _res(_sel({**sub, "as": "N"}, [{"ref": ["N", "MISSING"], "as": "Z"}]), {"S": ("A",)})
    assert _o(r2, "Z") == [(None, None, "UNKNOWN", AMB_NO_OUTPUT_FIELD)]
    r3 = _res(_sel({"ref": ["NOT_IN_POOL"]}, ["*"]))
    assert any(o.ambiguity == AMB_STAR for os_ in r3.values() for o in os_)


def test_star_expands_over_a_source_with_a_field_list():
    r = _res(_sel({"ref": ["S"]}, ["*"]), {"S": ("A", "B")})
    assert _o(r, "A") == [("S", "A", MT_IDENTITY, None)] and _o(r, "B") == [("S", "B", MT_IDENTITY, None)]


def test_union_branches_are_matched_by_position_and_keep_every_origin():
    b1 = _sel({"ref": ["S1"]}, [{"ref": ["S1", "A"], "as": "X"}])
    b2 = _sel({"ref": ["S2"]}, [{"ref": ["S2", "Q"], "as": "Y"}])
    r = _res({"SET": {"op": "union", "args": [b1, b2]}}, {"S1": ("A",), "S2": ("Q",)})
    assert sorted(_o(r, "X")) == [("S1", "A", MT_RENAME, None), ("S2", "Q", MT_RENAME, None)]
    bad = _res({"SET": {"op": "union", "args": [b1, _sel({"ref": ["S2"]}, [{"ref": ["S2", "Q"]}, {"ref": ["S2", "Q"], "as": "Q2"}])]}}, {"S1": ("A",), "S2": ("Q",)})
    assert any(o.ambiguity == AMB_SET_ARITY for o in bad["X"])


def test_a_parameter_reference_is_not_a_field_origin():
    r = _res(_sel({"ref": ["S"]}, [{"ref": ["$parameters", "P"], "as": "PV"}]), {"S": ("A",)})
    assert _o(r, "PV") == [(None, None, MT_DERIVED, None)]


# ═══════════════════════ 2. pool sintetico: hechos de linaje y de clase de fuente ═══════════════════════
def _w(root: Path, rel: str, body) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _remote(name: str, entity: str, fields=("A", "B", "M"), connection: str = "S4PREM") -> dict:
    return {"definitions": {name: {"kind": "entity", "elements": {f: STR for f in fields}, "@DataWarehouse.remote.connection": connection, "@DataWarehouse.remote.entity": entity}}}


def _define(name: str, fields, query=None, **extra) -> dict:
    body = {"kind": "entity", "elements": {f: {**STR, "@EndUserText.label": f} for f in fields}, **extra}
    if query is not None:
        body["query"] = query
    return body


def _build(layout: str = "separate") -> Path:
    """Acme: RAW_T (tabla ABAP fisica) y CDS_T (CDS) y SQLSRV_T (SQL Server) como remotas; 2VD_DIM (dimension, candidato) sobre CDS_T; 3VF_P con subselects anidados leyendo RAW_T y 2VD_DIM
    y SQLSRV_T; AM 4AM_X con campos identidad / renombre / derivado / expresion / cast. ``layout='embedded'`` define las tablas remotas DENTRO del export de la vista que las lee."""
    root = Path(tempfile.mkdtemp(prefix="lineage_"))
    remotes = {"RAW_T": _remote("RAW_T", "ABAPTABLES.TAB1"), "CDS_T": _remote("CDS_T", "ABAP_CDS.ZCDS_X", ("A", "B")), "SQLSRV_T": _remote("SQLSRV_T", "dbo.facts", ("A", "Q"), connection="SQLSERVER")}
    dim = _define("2VD_DIM", ("A", "Bname"), _sel({"ref": ["CDS_T"]}, [{"ref": ["CDS_T", "A"], "as": "A"}, {"ref": ["CDS_T", "B"], "as": "Bname"}]), **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_DIMENSION"}})
    inner = _sel({"join": "inner", "args": [{"ref": ["RAW_T"]}, {"ref": ["SQLSRV_T"]}], "on": []},
                 [{"ref": ["RAW_T", "A"], "as": "A"}, {"ref": ["RAW_T", "B"], "as": "B"}, {"ref": ["RAW_T", "M"], "as": "M"}, {"ref": ["SQLSRV_T", "Q"], "as": "Q"}])
    outer = _sel({"join": "left", "args": [{**inner, "as": "Node 1"}, {"ref": ["2VD_DIM"]}], "on": []},
                 [{"ref": ["Node 1", "A"], "as": "A"}, {"ref": ["Node 1", "B"], "as": "B"}, {"ref": ["Node 1", "M"], "as": "M"}, {"ref": ["Node 1", "Q"], "as": "Q"},
                  {"ref": ["2VD_DIM", "Bname"], "as": "Bname"}])
    fact = _define("3VF_P", ("A", "B", "M", "Q", "Bname"), outer, **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"}})
    am_cols = [{"ref": ["3VF_P", "A"], "as": "A"}, {"ref": ["3VF_P", "B"], "as": "Brenamed"}, {"func": "UPPER", "args": [{"ref": ["3VF_P", "Bname"]}], "as": "BnameU"},
               {"xpr": [{"ref": ["3VF_P", "M"]}, "*", {"ref": ["3VF_P", "Q"]}], "as": "MQ"}, {"ref": ["3VF_P", "M"], "as": "M", "cast": {"type": "cds.Integer"}}]
    am = _define(AM, ("A", "Brenamed", "BnameU", "MQ", "M"), _sel({"ref": ["3VF_P"]}, am_cols), **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"}})
    if layout == "separate":
        for n, d in remotes.items():
            _w(root, f"Acme/remote-tables/{n}.json", d)
        _w(root, "Acme/views/2VD_DIM.json", {"definitions": {"2VD_DIM": dim}})
        _w(root, "Acme/views/3VF_P.json", {"definitions": {"3VF_P": fact}})
    else:                                                                                           # las remotas embebidas en el export de la vista que las lee; la dimension junto a la fact
        _w(root, "Acme/views/3VF_P.json", {"definitions": {"3VF_P": fact, "2VD_DIM": dim, **{n: d["definitions"][n] for n, d in remotes.items()}}})
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: am}})
    return root


def _doc(root: Path, scope: dict | None = None) -> dict:
    kw = {"evidence_scope": scope} if scope else {}
    doc, _ = ma_shadow.assess(root, "Acme", AM, "General", **kw)
    return doc


def _lineage(doc: dict) -> dict[str, dict]:
    return {r["technical_name"]: r for r in doc["canonical_evidence"]["published_field_lineage"]["fields"]}


def _classes(doc: dict) -> dict[str, dict]:
    return {r["leaf"]: r for r in doc["canonical_evidence"]["source_contract_classes"]["leaves"]}


@pytest.fixture(scope="module")
def doc() -> dict:
    d = _doc(_build())
    ma_profile.validate_assessment(d)
    return d


def test_the_profile_declares_the_current_contract_which_keeps_the_v3_sections_and_validates(doc):
    ce = doc["canonical_evidence"]
    assert ce["version"] == "canonical-evidence/v4" == doc["versions"]["canonical_evidence"] and doc["versions"]["assessability"] == "assessability-rules/v4"      # v4 = v3 + leaf_materialization (v4.A)
    EC.validate_canonical_evidence(ce)
    assert ce["published_field_lineage"]["counts"] == {"fields": 5, "COMPLETE": 5, "PARTIAL": 0, "UNRESOLVED": 0, "AMBIGUOUS": 0}


def test_identity_through_nested_subselects_reaches_the_physical_boundary(doc):
    a = _lineage(doc)["A"]
    leaves = {(l["leaf"], l["leaf_field"]) for l in a["frontier_leaves"]}
    assert leaves == {("RAW_T", "A")} and a["completeness"] == "COMPLETE"
    only = a["frontier_leaves"][0]
    assert only["source_contract_class"] == "PHYSICAL_ABAP_TABLE_BOUNDARY" and only["path_profiles"] == [{"character": "IDENTITY_ONLY", "through_harmonization": False, "paths": 1}]
    assert a["immediate_providers"] == [{"artifact": "3VF_P", "field": "A", "mapping_type": "IDENTITY"}] and a["harmonization_boundaries_crossed"] == []


def test_a_rename_is_recorded_as_identity_or_rename_without_derivation(doc):
    b = _lineage(doc)["Brenamed"]
    assert b["immediate_providers"][0]["mapping_type"] == "RENAME"
    assert [(l["leaf"], l["leaf_field"], l["path_profiles"]) for l in b["frontier_leaves"]] == [("RAW_T", "B", [{"character": "IDENTITY_OR_RENAME", "through_harmonization": False, "paths": 1}])]


def test_a_derived_field_crossing_a_harmonization_candidate_records_both_facts(doc):
    u = _lineage(doc)["BnameU"]
    assert u["immediate_providers"][0]["mapping_type"] == "DERIVED" and u["harmonization_boundaries_crossed"] == ["2VD_DIM"]
    (leaf,) = u["frontier_leaves"]
    assert (leaf["leaf"], leaf["leaf_field"], leaf["source_contract_class"]) == ("CDS_T", "B", "GOVERNED_SAP_CDS_CONTRACT")
    assert leaf["path_profiles"] == [{"character": "WITH_DERIVATION", "through_harmonization": True, "paths": 1}]


def test_a_multi_input_expression_reaches_every_leaf_it_reads_with_its_own_class(doc):
    mq = _lineage(doc)["MQ"]
    got = {(l["leaf"], l["leaf_field"]): (l["source_contract_class"], l["path_profiles"]) for l in mq["frontier_leaves"]}
    derived = [{"character": "WITH_DERIVATION", "through_harmonization": False, "paths": 1}]
    assert got == {("RAW_T", "M"): ("PHYSICAL_ABAP_TABLE_BOUNDARY", derived), ("SQLSRV_T", "Q"): ("EXTERNAL_CONTRACT_UNDETERMINED", derived)}
    assert mq["paths"]["total"] == 2 and mq["immediate_providers"][0]["mapping_type"] == "EXPRESSION"


def test_a_cast_is_recorded_as_a_cast_path(doc):
    m = _lineage(doc)["M"]
    assert m["immediate_providers"][0]["mapping_type"] == "CAST" and m["frontier_leaves"][0]["path_profiles"] == [{"character": "WITH_CAST", "through_harmonization": False, "paths": 1}]


def test_source_contract_classes_never_infer_governance_or_rawness_from_technology(doc):
    c = _classes(doc)
    assert c["RAW_T"]["contract_class"] == "PHYSICAL_ABAP_TABLE_BOUNDARY" and c["RAW_T"]["classification_basis"] == "REMOTE_ENTITY_NAMESPACE"
    assert c["CDS_T"]["contract_class"] == "GOVERNED_SAP_CDS_CONTRACT" and c["CDS_T"]["classification_basis"] == "REMOTE_ENTITY_NAMESPACE"
    sql = c["SQLSRV_T"]                                                                         # SQL Server: ni "cruda" ni "gobernada" por tecnologia
    assert sql["contract_class"] == "EXTERNAL_CONTRACT_UNDETERMINED" and sql["classification_basis"] == "NO_RECOGNIZED_NAMESPACE" and sql["leaf_kind"] == "REMOTE_IMPORTED"


def test_artifacts_traversed_lists_the_intermediate_artifacts_of_every_path(doc):
    lin = _lineage(doc)
    assert lin["A"]["artifacts_traversed"] == ["3VF_P"] and lin["BnameU"]["artifacts_traversed"] == ["2VD_DIM", "3VF_P"]
    assert all(AM not in r["artifacts_traversed"] and "RAW_T" not in r["artifacts_traversed"] for r in lin.values())


def test_the_vocabulary_relation_to_the_leaf_is_factual_and_leaf_specific(doc):
    a = _lineage(doc)["A"]["frontier_leaves"][0]["vocabulary"]
    assert a == {"name_equals_leaf_field": True, "label_relation": "EQUALS_LEAF_FIELD_NAME"}               # el nombre es el del campo de la hoja y la etiqueta repite ese nombre
    b = _lineage(doc)["Brenamed"]["frontier_leaves"][0]["vocabulary"]
    assert b["name_equals_leaf_field"] is False and b["label_relation"] == "LEAF_LABEL_UNKNOWN"              # renombrado; la hoja no declara etiqueta: no se afirma nada mas


def test_the_vocabulary_relation_distinguishes_an_inherited_leaf_label_from_an_authored_one():
    root = _build()
    p = root / "Acme/remote-tables/RAW_T.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["definitions"]["RAW_T"]["elements"]["A"] = {**STR, "@EndUserText.label": "A"}                       # igual a la etiqueta publicada de ``A``
    d["definitions"]["RAW_T"]["elements"]["B"] = {**STR, "@EndUserText.label": "Business B"}               # distinta de la publicada de ``Brenamed``
    p.write_text(json.dumps(d), encoding="utf-8")
    lin = _lineage(_doc(root))
    assert lin["A"]["frontier_leaves"][0]["vocabulary"]["label_relation"] == "EQUALS_LEAF_LABEL"
    assert lin["Brenamed"]["frontier_leaves"][0]["vocabulary"]["label_relation"] == "DIFFERENT_FROM_LEAF"


def test_sample_paths_are_consistent_with_the_aggregates(doc):
    for r in doc["canonical_evidence"]["published_field_lineage"]["fields"]:
        for p in r["lineage_paths"]:
            assert p["nodes"][0] == {"artifact": AM, "field": r["technical_name"], "layer": "4"} or p["nodes"][0]["artifact"] == AM
            assert p["nodes"][-1].get("frontier_leaf") is True and len(p["edges"]) == len(p["nodes"]) - 1


def test_the_new_evidence_contains_no_verdict_vocabulary(doc):
    text = json.dumps({k: doc["canonical_evidence"][k] for k in ("published_field_lineage", "source_contract_classes")})
    for banned in ('"quality"', '"score"', '"restored"', '"deviation"', '"weakness"', '"verdict"'):
        assert banned not in text


def test_the_existing_sections_are_untouched_by_the_new_evidence(doc):
    ce = doc["canonical_evidence"]
    assert ce["published_contract"]["fields"]["counts"]["fields"] == 5 and doc["areas"]


# ═══════════════════════ 3. ambiguedad e incompletitud explicitas ═══════════════════════
def _ambiguous_pool() -> Path:
    """AM lee una vista cuyo campo ``X`` sale de un join entre dos fuentes que lo exponen y se referencia SIN calificar: no se puede elegir proveedor."""
    root = Path(tempfile.mkdtemp(prefix="lineage_amb_"))
    _w(root, "Acme/remote-tables/S1.json", _remote("S1", "ABAP_CDS.ZS1", ("X", "K")))
    _w(root, "Acme/remote-tables/S2.json", _remote("S2", "ABAP_CDS.ZS2", ("X", "K")))
    view = _define("3VF_P", ("X", "K"), _sel({"join": "inner", "args": [{"ref": ["S1"]}, {"ref": ["S2"]}], "on": []}, [{"ref": ["X"]}, {"ref": ["S1", "K"], "as": "K"}]),
                   **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"}})
    _w(root, "Acme/views/3VF_P.json", {"definitions": {"3VF_P": view}})
    am = _define(AM, ("X", "K"), _sel({"ref": ["3VF_P"]}, [{"ref": ["3VF_P", "X"], "as": "X"}, {"ref": ["3VF_P", "K"], "as": "K"}]),
                 **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"}})
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: am}})
    return root


def test_an_ambiguous_hop_is_explicit_and_never_collapsed_into_a_single_provider():
    d = _doc(_ambiguous_pool())
    ma_profile.validate_assessment(d)
    lin = _lineage(d)
    assert lin["X"]["completeness"] == "AMBIGUOUS" and lin["X"]["ambiguity_codes"] == [AMB_UNQUALIFIED_MULTI] and lin["X"]["frontier_leaves"] == []
    assert lin["X"]["paths"]["ambiguous"] == 1 and lin["X"]["paths"]["to_frontier_leaf"] == 0
    assert lin["K"]["completeness"] == "COMPLETE" and [l["leaf"] for l in lin["K"]["frontier_leaves"]] == ["S1"]               # lo no ambiguo se sigue resolviendo
    assert d["canonical_evidence"]["published_field_lineage"]["counts"]["AMBIGUOUS"] == 1


def test_a_root_without_query_evidence_has_unresolved_lineage_not_a_fabricated_one():
    root = _build()
    am_path = root / f"Acme/analytic-models/{AM}.json"
    data = json.loads(am_path.read_text(encoding="utf-8"))
    data["definitions"][AM].pop("query")
    am_path.write_text(json.dumps(data), encoding="utf-8")
    d = _doc(root)
    counts = d["canonical_evidence"]["published_field_lineage"]["counts"]
    assert counts["UNRESOLVED"] == 5 and counts["COMPLETE"] == 0
    assert all(r["unresolved_reasons"] == ["NO_QUERY_EVIDENCE"] and r["frontier_leaves"] == [] for r in d["canonical_evidence"]["published_field_lineage"]["fields"])


def test_completeness_is_epistemic_and_never_a_quality_statement(doc):
    for r in doc["canonical_evidence"]["published_field_lineage"]["fields"]:
        assert set(r) >= {"completeness", "paths"} and "quality" not in r


# ═══════════════════════ 4. sombra de captura de cambios X_Delta ═══════════════════════
def _delta_pool() -> Path:
    """La tabla local LOCAL_X (producida por un RF) trae un query que lee su propia sombra ``LOCAL_X_Delta``: es una HOJA del cierre, no un ciclo de linaje."""
    root = Path(tempfile.mkdtemp(prefix="lineage_delta_"))
    _w(root, "Acme/local-tables/LOCAL_X.json", {"definitions": {"LOCAL_X": _define("LOCAL_X", ("A",), _sel({"ref": ["LOCAL_X_Delta"]}, [{"ref": ["LOCAL_X_Delta", "A"], "as": "A"}]))}})
    view = _define("3VF_P", ("A",), _sel({"ref": ["LOCAL_X"]}, [{"ref": ["LOCAL_X", "A"], "as": "A"}]), **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"}})
    _w(root, "Acme/views/3VF_P.json", {"definitions": {"3VF_P": view}})
    am = _define(AM, ("A",), _sel({"ref": ["3VF_P"]}, [{"ref": ["3VF_P", "A"], "as": "A"}]), **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"}})
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: am}})
    _w(root, "Acme/replication-flows/RF_X.json", {"replicationflows": {"RF_X": {"kind": "sap.dis.replicationflow", "contents": {
        "sourceSystem": [{"connectionId": "S4_HANA", "connectionType": "SAPS4HANAOP", "container": "/CDS_EXTRACTION"}],
        "replicationTasks": [{"name": "t1", "loadType": "REPLICATE", "sourceObject": {"name": "ZCDS_SRC"}, "targetObject": {"name": "LOCAL_X_Delta"}}]},
        "sources": {}, "targets": {"LOCAL_X_Delta": {"elements": {}}}}}})
    return root


def test_a_local_leaf_that_reads_its_own_delta_shadow_is_a_frontier_leaf_and_not_a_cycle():
    d = _doc(_delta_pool(), SCOPE_ALL)
    ma_profile.validate_assessment(d)
    a = _lineage(d)["A"]
    assert a["completeness"] == "COMPLETE" and [(l["leaf"], l["leaf_field"]) for l in a["frontier_leaves"]] == [("LOCAL_X", "A")]
    leaf = _classes(d)["LOCAL_X"]
    assert leaf["leaf_kind"] == "LOCAL_FLOW_PRODUCED" and leaf["contract_class"] == "GOVERNED_SAP_CDS_CONTRACT" and leaf["classification_basis"] == "RF_SOURCE_CONTAINER"
    assert leaf["source_systems"] == [{"connection_id": "S4_HANA", "connection_type": "SAPS4HANAOP", "container": "/CDS_EXTRACTION"}]


# ═══════════════════════ 5. clase de contrato de fuente de hojas locales ═══════════════════════
def _local_leaf_pool(container: str | None, *, with_rf: bool = True, embedded_in_flow: bool = False) -> Path:
    root = Path(tempfile.mkdtemp(prefix="lineage_leaf_"))
    local = {"definitions": {"LOCAL_X": _define("LOCAL_X", ("A",))}}
    if embedded_in_flow and with_rf:                                                      # la tabla local definida DENTRO del export del flujo (empaquetado alternativo)
        local = None
    if local is not None:
        _w(root, "Acme/local-tables/LOCAL_X.json", local)
    view = _define("3VF_P", ("A",), _sel({"ref": ["LOCAL_X"]}, [{"ref": ["LOCAL_X", "A"], "as": "A"}]), **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"}})
    _w(root, "Acme/views/3VF_P.json", {"definitions": {"3VF_P": view}})
    am = _define(AM, ("A",), _sel({"ref": ["3VF_P"]}, [{"ref": ["3VF_P", "A"], "as": "A"}]), **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"}})
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: am}})
    if with_rf:
        rf = {"replicationflows": {"RF_X": {"kind": "sap.dis.replicationflow", "contents": {
            "sourceSystem": [{"connectionId": "CONN", "connectionType": "SQLSERVER", "container": container}],
            "replicationTasks": [{"name": "t1", "loadType": "REPLICATE", "sourceObject": {"name": "dbo.t"}, "targetObject": {"name": "LOCAL_X"}}]},
            "sources": {}, "targets": {"LOCAL_X": {"elements": {}}}}}}
        if embedded_in_flow:
            rf["definitions"] = {"LOCAL_X": _define("LOCAL_X", ("A",))}
        _w(root, "Acme/replication-flows/RF_X.json", rf)
    return root


def test_a_non_sap_replication_source_stays_undetermined_even_with_a_container_and_connection_type():
    d = _doc(_local_leaf_pool("/dbo"), SCOPE_ALL)
    leaf = _classes(d)["LOCAL_X"]
    assert leaf["contract_class"] == "EXTERNAL_CONTRACT_UNDETERMINED" and leaf["classification_basis"] == "RF_SOURCE_CONTAINER"
    assert leaf["source_systems"][0]["connection_type"] == "SQLSERVER"                          # se EXPONE como hecho; no clasifica


def test_a_local_leaf_without_an_acquired_producer_is_unknown_with_its_reason():
    d = _doc(_local_leaf_pool(None, with_rf=False), SCOPE_ALL)
    leaf = _classes(d)["LOCAL_X"]
    assert leaf["contract_class"] == "UNKNOWN" and leaf["classification_basis"] in ("NO_ACQUIRED_PRODUCER", "PROVENANCE_NOT_ASSESSED")
    assert _doc(_local_leaf_pool(None, with_rf=False))["canonical_evidence"]["source_contract_classes"]["leaves"][0]["classification_basis"] == "PROVENANCE_NOT_ASSESSED"      # sin alcance declarado


# ═══════════════════════ 6. invarianza al empaquetado del export ═══════════════════════
def _strip_volatile(d: dict) -> dict:
    ce = copy.deepcopy(d["canonical_evidence"])
    return {"published_field_lineage": ce["published_field_lineage"], "source_contract_classes": ce["source_contract_classes"]}


def test_lineage_and_source_classes_do_not_depend_on_where_the_remote_definitions_are_exported():
    separate, embedded = _doc(_build("separate")), _doc(_build("embedded"))
    assert _strip_volatile(separate) == _strip_volatile(embedded)


def test_a_local_table_defined_inside_the_flow_export_yields_the_same_lineage_and_class_as_a_separate_export():
    separate = _doc(_local_leaf_pool("/CDS_EXTRACTION"), SCOPE_ALL)
    embedded = _doc(_local_leaf_pool("/CDS_EXTRACTION", embedded_in_flow=True), SCOPE_ALL)
    assert _strip_volatile(separate) == _strip_volatile(embedded)
    assert _classes(separate)["LOCAL_X"]["contract_class"] == "GOVERNED_SAP_CDS_CONTRACT"


def test_the_flow_artifact_with_and_without_embedded_definitions_does_not_change_the_closure_or_the_lineage():
    a = _doc(_local_leaf_pool("/CDS_EXTRACTION"), SCOPE_ALL)
    b = _doc(_local_leaf_pool("/CDS_EXTRACTION", embedded_in_flow=True), SCOPE_ALL)
    assert a["scope"]["closure_artifacts"] == b["scope"]["closure_artifacts"] and a["scope"]["closure_size"] == b["scope"]["closure_size"]


def test_the_result_is_deterministic():
    root = _build()
    assert json.dumps(_strip_volatile(_doc(root)), sort_keys=True) == json.dumps(_strip_volatile(_doc(root)), sort_keys=True)


# ═══════════════════════ 7. contrato (validador) ═══════════════════════
def _tamper(doc: dict, fn) -> None:
    ce = copy.deepcopy(doc["canonical_evidence"])
    fn(ce)
    with pytest.raises(CanonicalEvidenceError):
        EC.validate_canonical_evidence(ce)


def test_the_validator_rejects_inconsistent_completeness_and_counts(doc):
    _tamper(doc, lambda ce: ce["published_field_lineage"]["fields"][0].update(completeness="AMBIGUOUS"))
    _tamper(doc, lambda ce: ce["published_field_lineage"]["fields"][0]["paths"].update(total=99))
    _tamper(doc, lambda ce: ce["published_field_lineage"]["counts"].update(COMPLETE=0))


def test_the_validator_rejects_a_leaf_whose_class_does_not_match_the_class_section(doc):
    _tamper(doc, lambda ce: ce["published_field_lineage"]["fields"][0]["frontier_leaves"][0].update(source_contract_class="GOVERNED_SAP_CDS_CONTRACT"))
    _tamper(doc, lambda ce: ce["published_field_lineage"]["fields"][0]["frontier_leaves"][0].update(leaf="NOT_A_LEAF"))


def test_the_validator_rejects_verdict_vocabulary_and_unknown_classes(doc):
    _tamper(doc, lambda ce: ce["published_field_lineage"]["fields"][0].update(quality="BAD"))
    _tamper(doc, lambda ce: ce["source_contract_classes"]["leaves"][0].update(contract_class="GOVERNED_BY_TECHNOLOGY"))


def test_the_validator_rejects_a_physical_table_class_without_explicit_evidence(doc):
    def f(ce):
        r = next(x for x in ce["source_contract_classes"]["leaves"] if x["contract_class"] == "PHYSICAL_ABAP_TABLE_BOUNDARY")
        r["classification_basis"] = "NO_RECOGNIZED_NAMESPACE"
    _tamper(doc, f)


def test_the_validator_requires_lineage_to_cover_exactly_the_published_fields(doc):
    _tamper(doc, lambda ce: ce["published_field_lineage"]["fields"].pop())
    _tamper(doc, lambda ce: ce.pop("source_contract_classes"))


def test_v2_profiles_stay_valid_without_the_new_sections():
    d = _doc(_build())
    from tools.catalog.modeling_assessment.canonical_sections import V3_SECTIONS, V4_SECTIONS
    old = copy.deepcopy(d)
    for sec in (*V3_SECTIONS, *V4_SECTIONS):
        old["canonical_evidence"].pop(sec)
    old["canonical_evidence"]["version"] = "canonical-evidence/v2"
    old["versions"].update({"canonical_evidence": "canonical-evidence/v2", "assessability": "assessability-rules/v3"})
    ma_profile.validate_assessment(old)
