"""Gate B — el walk de linaje upstream sigue también los targets de asociación (``cds.Association``), no solo
FROM/JOIN, para que el cierre semántico de la raíz (p. ej. tablas de textos ``_Text`` alcanzadas por asociación)
entre en ``local_tables`` ANTES del descubrimiento de productores. Sin lógica específica de cliente/root.
"""
from __future__ import annotations

import pytest

from tools.catalog import DSP_CLI_Export_Lineage as dsp


class FakeCli:
    def __init__(self, inventories=None, objects=None):
        self.inventories = inventories or {}
        self.objects = objects or {}
        self.diagnostics = dsp.ExtractionDiagnostics(False)

    def list_objects(self, space, object_type):
        return [{"technicalName": x} for x in self.inventories.get(object_type, [])]

    def read_object(self, space, object_type, technical_name):
        return self.objects.get((object_type, technical_name), {})


def _view(name, *, sql=None, assoc=()):
    elems = {f"_assoc{i}": {"type": "cds.Association", "target": t, "on": []} for i, t in enumerate(assoc)}
    defn = {"kind": "entity", "elements": elems}
    if sql is not None:
        defn["@DataWarehouse.sqlEditor.query"] = sql
    return {"definitions": {name: defn}}


def _am(name, *, sources=(), assoc=()):
    dims = {str(i): {"dataEntity": {"key": s}} for i, s in enumerate(sources)}
    elems = {f"_a{i}": {"type": "cds.Association", "target": t} for i, t in enumerate(assoc)}
    return {"definitions": {name: {"kind": "entity", "elements": elems}},
            "businessLayerDefinitions": {"L": {"sourceModel": {"dimensionSources": dims}}}}


def _lt(name):
    return {"definitions": {name: {"kind": "entity", "elements": {}}}}


def _rf(name, targets):
    return {"replicationflows": {name: {"targets": {t: {} for t in targets},
                                        "connections": {"C": {}},
                                        "contents": {"replicationTasks": [{"sourceObject": {"name": "SRC"}}]}}}}


def _build(*, inventories, objects, root, root_type="analytic-model",
           features=("analytic-models", "views", "tables", "replication-flows")):
    inv = {t: [] for t in dsp.ROOT_OBJECT_TYPES}
    inv.update(inventories)
    exp = dsp.LineageExporter(FakeCli(inv, objects), "S", output_dir="C:/temp/none", root_object=root)
    exp.root_type = root_type
    exp.effective_features = set(features)
    return exp


def _nodes(exp, otype):
    return sorted(n for (t, n) in exp.nodes if t == otype)


def _produces(exp):
    return {(e.source_name, e.target_name) for e in exp.edges if e.relation == "PRODUCES"}


# A ─ existing SQL FROM/JOIN dependency still works unchanged
def test_a_sql_from_join_still_works():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V"], "local-tables": ["LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql='SELECT * FROM "LT"'),
                 ("local-tables", "LT"): _lt("LT")},
        root="AM")
    exp.walk_model_graph()
    assert "LT" in _nodes(exp, "local-tables")


# B ─ direct cds.Association target is added to the root walk
def test_b_direct_association_target_is_followed():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V"], "local-tables": ["LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql="SELECT 1", assoc=["LT"]),
                 ("local-tables", "LT"): _lt("LT")},
        root="AM")
    exp.walk_model_graph()
    assert "LT" in _nodes(exp, "local-tables")


# C ─ multi-hop: reached view -> association view -> SQL/local-table dependency
def test_c_multi_hop_association_then_sql():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V1", "V2"], "local-tables": ["LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V1"]),
                 ("views", "V1"): _view("V1", sql="SELECT 1", assoc=["V2"]),
                 ("views", "V2"): _view("V2", sql='SELECT * FROM "LT"'),
                 ("local-tables", "LT"): _lt("LT")},
        root="AM")
    exp.walk_model_graph()
    assert "V2" in _nodes(exp, "views")
    assert "LT" in _nodes(exp, "local-tables")


# D ─ association to a text table becomes a local-table leaf candidate
def test_d_association_to_text_table_is_a_leaf_candidate():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["2VD_MD_X"], "local-tables": ["1TT_MD_XTEXT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["2VD_MD_X"]),
                 ("views", "2VD_MD_X"): _view("2VD_MD_X", sql="SELECT 1", assoc=["1TT_MD_XTEXT"]),
                 ("local-tables", "1TT_MD_XTEXT"): _lt("1TT_MD_XTEXT")},
        root="AM")
    exp.walk_model_graph()
    assert "1TT_MD_XTEXT" in _nodes(exp, "local-tables")


# E ─ association-reached leaf with an EXACT producer RF is acquired
def test_e_exact_producer_of_association_leaf_is_acquired():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V"], "local-tables": ["LT"], "replication-flows": ["RF_LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql="SELECT 1", assoc=["LT"]),
                 ("local-tables", "LT"): _lt("LT"),
                 ("replication-flows", "RF_LT"): _rf("RF_LT", ["LT"])},
        root="AM")
    exp.walk_model_graph()
    exp.discover_producers()
    assert ("RF_LT", "LT") in _produces(exp)


# F ─ association-reached leaf with a DELTA_SHADOW producer is acquired (Gate A matcher)
def test_f_delta_shadow_producer_of_association_leaf_is_acquired():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V"], "local-tables": ["LT"], "replication-flows": ["RF_LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql="SELECT 1", assoc=["LT"]),
                 ("local-tables", "LT"): _lt("LT"),
                 ("replication-flows", "RF_LT"): _rf("RF_LT", ["LT_Delta"])},
        root="AM")
    exp.walk_model_graph()
    exp.discover_producers()
    assert ("RF_LT", "LT") in _produces(exp)


# G ─ association cycle A -> B -> A terminates with no duplication
def test_g_association_cycle_terminates():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["A", "B"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["A"]),
                 ("views", "A"): _view("A", sql="SELECT 1", assoc=["B"]),
                 ("views", "B"): _view("B", sql="SELECT 1", assoc=["A"])},
        root="AM")
    exp.walk_model_graph()                    # must not hang / recurse forever
    assert _nodes(exp, "views") == ["A", "B"]


# H ─ unresolved association target does not fabricate a node
def test_h_unresolved_association_target_is_not_a_node():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql="SELECT 1", assoc=["GHOST"])},
        root="AM")
    exp.walk_model_graph()
    assert not any(n == "GHOST" for (_t, n) in exp.nodes)
    assert "GHOST" in exp.external_sources        # tratado como externo, igual que un FROM/JOIN no resoluble


# I ─ scope gating still excludes a disallowed dependency type
def test_i_scope_gating_excludes_disallowed_type():
    # sin la feature "tables": los local-tables (incluidos los alcanzados por asociación) quedan fuera de alcance
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V"], "local-tables": ["LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql="SELECT 1", assoc=["LT"]),
                 ("local-tables", "LT"): _lt("LT")},
        root="AM", features=("analytic-models", "views"))
    exp.walk_model_graph()
    assert "LT" not in _nodes(exp, "local-tables")


# J ─ unrelated Space entities are not pulled into the root closure
def test_j_unrelated_entities_are_not_acquired():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["V", "UNRELATED_V"],
                     "local-tables": ["LT", "UNRELATED_LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["V"]),
                 ("views", "V"): _view("V", sql="SELECT 1", assoc=["LT"]),
                 ("views", "UNRELATED_V"): _view("UNRELATED_V", sql='SELECT * FROM "UNRELATED_LT"'),
                 ("local-tables", "LT"): _lt("LT"),
                 ("local-tables", "UNRELATED_LT"): _lt("UNRELATED_LT")},
        root="AM")
    exp.walk_model_graph()
    assert "UNRELATED_V" not in _nodes(exp, "views")
    assert "UNRELATED_LT" not in _nodes(exp, "local-tables")


# K ─ structural association metadata drives inclusion, not SQL vs graphical
def test_k_graphical_view_association_is_still_followed():
    # vista GRAPHICAL: sin @DataWarehouse.sqlEditor.query, pero con asociación estructural
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["GV"], "local-tables": ["LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=["GV"]),
                 ("views", "GV"): _view("GV", sql=None, assoc=["LT"]),
                 ("local-tables", "LT"): _lt("LT")},
        root="AM")
    exp.walk_model_graph()
    assert "LT" in _nodes(exp, "local-tables")


# L ─ analytic-model level association is followed (AM declares associations in elements)
def test_l_analytic_model_association_is_followed():
    exp = _build(
        inventories={"analytic-models": ["AM"], "views": ["VD"], "local-tables": ["LT"]},
        objects={("analytic-models", "AM"): _am("AM", sources=[], assoc=["VD"]),
                 ("views", "VD"): _view("VD", sql='SELECT * FROM "LT"'),
                 ("local-tables", "LT"): _lt("LT")},
        root="AM")
    exp.walk_model_graph()
    assert "VD" in _nodes(exp, "views")
    assert "LT" in _nodes(exp, "local-tables")
