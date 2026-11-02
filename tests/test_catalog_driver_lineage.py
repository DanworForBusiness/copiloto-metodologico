"""Linaje publicado del Catálogo: la FUENTE CONDUCTORA de una vista analítica no se degrada a asociación solo por ser remota.

Caso real (Contreras, `HCM: Turnover`): `2VF_HCM_Employment` (ANALYTICAL_FACT) parte de `1TR_SSF_EmpEmployment` (FROM, remota) y
enriquece con `1TR_SSF_EmpEmploymentTermination` (LEFT JOIN, remota). La regla del loader degradaba las dos a asociación y la vista
simplificada perdía el tramo de ingesta. La corrección es una PROYECCIÓN de publicación (`published_lineage`): el loader y el contrato
que consume el Modeling Assessment (`upstreams` / `assoc_upstreams`) no cambian. CSN sintéticos con la misma forma; sin red."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "catalog"))

import generate_catalog as gc  # noqa: E402


def _remote(name):
    return {"kind": "entity", "@DataWarehouse.remote.entity": name.split("_")[-1], "@DataWarehouse.remote.connection": "SRC",
            "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}, "elements": {"id": {"type": "cds.String", "key": True}}}


def _view(frm, pattern="ANALYTICAL_FACT", query=None):
    return {"kind": "entity", "@ObjectModel.modelingPattern": {"#": pattern}, "elements": {"id": {"type": "cds.String"}},
            "query": query or {"SELECT": {"from": frm, "columns": [{"ref": ["id"]}]}}}


def _ref(name, alias=None):
    return {"ref": [name], **({"as": alias} if alias else {})}


def _join(kind, *args):
    return {"join": kind, "args": list(args), "on": [{"ref": ["a", "id"]}, "=", {"ref": ["b", "id"]}]}


def _load(tmp_path, defs):
    f = tmp_path / "pool.json"
    f.write_text(json.dumps({"definitions": defs}), encoding="utf-8")
    ents, _ = gc.load_folder(json_files=[f], txt_files=[], dataflow_lineage=False)
    return ents


CONTRERAS = {
    "2VF_HCM_Employment": _view(_join("left", _ref("1TR_SSF_EmpEmployment", "E"), _ref("1TR_SSF_EmpEmploymentTermination", "T"))),
    "1TR_SSF_EmpEmployment": _remote("1TR_SSF_EmpEmployment"),
    "1TR_SSF_EmpEmploymentTermination": _remote("1TR_SSF_EmpEmploymentTermination"),
}


# ── fuente conductora: solo estructuras inequívocas ──
@pytest.mark.parametrize("frm, expected", [
    (_ref("A"), ["A"]),                                                           # FROM simple
    (_join("left", _ref("A"), _ref("B")), ["A"]),                                 # LEFT JOIN: la preservada
    (_join("left", _join("left", _ref("A"), _ref("B")), _ref("C")), ["A"]),       # cadena de LEFT JOIN
    (_join("inner", _ref("A"), _ref("B")), []),                                   # INNER: ambiguo → sin conductora
    (_join("right", _ref("A"), _ref("B")), []),                                   # RIGHT: ambiguo
    (_join("left", _join("inner", _ref("A"), _ref("B")), _ref("C")), []),         # base ambigua dentro de la cadena
    ({"SELECT": {"from": _ref("A")}}, []),                                        # subconsulta en FROM: ambigua
])
def test_driver_sources_only_for_unambiguous_shapes(frm, expected):
    assert gc._driver_sources({"SELECT": {"from": frm}}) == expected


def test_union_takes_the_driver_of_each_unambiguous_branch():
    q = {"SET": {"op": "union", "args": [{"SELECT": {"from": _ref("A")}}, {"SELECT": {"from": _join("left", _ref("B"), _ref("C"))}},
                                         {"SELECT": {"from": _join("inner", _ref("D"), _ref("E"))}}]}}
    assert gc._driver_sources(q) == ["A", "B"]


# ── caso Contreras ──
def test_contreras_driver_is_published_as_direct_and_the_left_join_stays_complementary(tmp_path):
    ents = _load(tmp_path, CONTRERAS)
    fact = ents["2VF_HCM_Employment"]
    assert fact.upstreams == [] and fact.assoc_upstreams == ["1TR_SSF_EmpEmployment", "1TR_SSF_EmpEmploymentTermination"]   # loader intacto
    assert gc.published_lineage(fact) == (["1TR_SSF_EmpEmployment"], ["1TR_SSF_EmpEmploymentTermination"])
    md = gc.render_vista(fact, "Contreras", "HCM")
    assert "upstreams_conductora:\n  - 1TR_SSF_EmpEmployment" in md and "| Deriva de | `1TR_SSF_EmpEmployment` |" in md


def test_contreras_flow_shows_the_driver_in_the_simplified_view_and_the_join_only_when_expanded(tmp_path, monkeypatch):
    pool = tmp_path / "pool" / "Contreras" / "HCM_Turnover"
    pool.mkdir(parents=True)
    (pool / "2VF_HCM_Employment.json").write_text(json.dumps({"definitions": CONTRERAS}), encoding="utf-8")
    out = tmp_path / "cat"
    gc.generate_all(tmp_path / "pool", out)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    import catalog_service as cs
    import webapp.main as m
    seen = {}
    real = m._catalog_flow_keep

    def spy(nodes, show_assoc):
        keep = real(nodes, show_assoc)
        seen[show_assoc] = (keep, {n["name"]: (n["upstreams"], n["assoc_upstreams"]) for n in nodes})
        return keep
    monkeypatch.setattr(m, "_catalog_flow_keep", spy)
    entry = cs.load_catalog(str(out))[0]
    for show in (False, True):
        m._catalog_flow_view(entry, show_assoc=show)
    keep, edges = seen[False]
    assert edges["2VF_HCM_Employment"][0] == ["1TR_SSF_EmpEmployment"]
    assert "1TR_SSF_EmpEmployment" in keep and "1TR_SSF_EmpEmploymentTermination" not in keep
    keep, edges = seen[True]
    assert "1TR_SSF_EmpEmploymentTermination" in keep and edges["2VF_HCM_Employment"][1] == ["1TR_SSF_EmpEmploymentTermination"]


# ── negativos: lo que ya estaba bien no cambia ──
def test_fact_on_relational_base_keeps_dimensions_texts_and_remote_joins_as_associations(tmp_path):
    defs = {
        "2VF_X": _view(_join("left", _join("left", _ref("2VR_X", "a"), _ref("VD_MAT", "d")), _ref("1TR_TXT", "t"))),
        "2VR_X": _view(_ref("1TR_SRC"), pattern="DATA_STRUCTURE"),
        "VD_MAT": _view(_ref("1TR_MAT"), pattern="ANALYTICAL_DIMENSION"),
        "1TR_TXT": _remote("1TR_TXT"), "1TR_SRC": _remote("1TR_SRC"), "1TR_MAT": _remote("1TR_MAT"),
    }
    fact = _load(tmp_path, defs)["2VF_X"]
    assert gc.published_lineage(fact) == (fact.upstreams, fact.assoc_upstreams) == (["2VR_X"], ["1TR_TXT", "VD_MAT"])


def test_driver_that_is_a_dimension_keeps_its_classification(tmp_path):
    defs = {"2VF_Y": _view(_join("left", _ref("VD_DIM"), _ref("1TR_A"))), "VD_DIM": _view(_ref("1TR_B"), pattern="ANALYTICAL_DIMENSION"),
            "1TR_A": _remote("1TR_A"), "1TR_B": _remote("1TR_B")}
    fact = _load(tmp_path, defs)["2VF_Y"]
    assert gc.published_lineage(fact) == ([], ["1TR_A", "VD_DIM"])


def test_inner_join_of_remote_sources_is_ambiguous_and_unchanged(tmp_path):
    defs = {"2VF_Z": _view(_join("inner", _ref("1TR_A"), _ref("1TR_B"))), "1TR_A": _remote("1TR_A"), "1TR_B": _remote("1TR_B")}
    fact = _load(tmp_path, defs)["2VF_Z"]
    assert gc.published_lineage(fact) == ([], ["1TR_A", "1TR_B"])


def test_dimension_with_a_remote_base_publishes_its_driver(tmp_path):
    """Una dimensión también tiene fuente conductora; que no aparezca en el camino simplificado del hecho es tarea de la selección de
    caminos del catálogo (la dimensión llega al hecho por asociación)."""
    defs = {"VD_MAT": _view(_join("left", _ref("1TR_MAT"), _ref("1TR_MAT_TXT")), pattern="ANALYTICAL_DIMENSION"),
            "1TR_MAT": _remote("1TR_MAT"), "1TR_MAT_TXT": _remote("1TR_MAT_TXT")}
    dim = _load(tmp_path, defs)["VD_MAT"]
    assert gc.published_lineage(dim) == (["1TR_MAT"], ["1TR_MAT_TXT"])


def test_relational_views_and_semantic_associations_are_untouched(tmp_path):
    defs = {"2VR_R": {**_view(_join("left", _ref("1TR_A"), _ref("1TR_B")), pattern="DATA_STRUCTURE"),
                      "elements": {"id": {"type": "cds.String"}, "_dim": {"type": "cds.Association", "target": "VD_D"}}},
            "VD_D": _view(_ref("1TR_A"), pattern="ANALYTICAL_DIMENSION"), "1TR_A": _remote("1TR_A"), "1TR_B": _remote("1TR_B")}
    view = _load(tmp_path, defs)["2VR_R"]
    assert gc.published_lineage(view) == (view.upstreams, view.assoc_upstreams)
    assert "VD_D" not in view.upstreams


# ── invariancia del Modeling Assessment ──
def test_modeling_assessment_graph_is_unchanged(tmp_path):
    """El assessment lee `upstreams`/`assoc_upstreams` del loader: la proyección no los toca, así que el grafo y el ORIGEN de cada arista
    (la conductora sigue como linaje recuperado de la query) son idénticos a antes del cambio."""
    from tools.catalog.modeling_assessment.graph import DependencyGraph, EdgeOrigin
    ents = _load(tmp_path, CONTRERAS)
    graph = DependencyGraph.from_entities(ents)
    origins = {(e.provider, e.origin) for e in graph.edges_of("2VF_HCM_Employment")}
    assert ("1TR_SSF_EmpEmployment", EdgeOrigin.QUERY_SOURCE_RECOVERED) in origins
    assert ("1TR_SSF_EmpEmploymentTermination", EdgeOrigin.QUERY_SOURCE_RECOVERED) in origins
    assert not any(o == EdgeOrigin.UPSTREAM_FIELD for _p, o in origins)


def test_data_products_consume_the_same_published_lineage():
    src = (REPO / "tools" / "catalog" / "generate_data_products_catalog.py").read_text(encoding="utf-8")
    assert "from tools.catalog.generate_catalog import published_lineage as _published_lineage" in src
    assert "_pub_up, _pub_assoc = _published_lineage(ent)" in src
