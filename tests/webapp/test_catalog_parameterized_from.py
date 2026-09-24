"""
test_catalog_parameterized_from.py — Extracción de la dependencia FROM cuando la
fuente es una entidad PARAMETRIZADA.

Root cause (PARAMETERIZED_FROM_EXTRACTION_BUG): `_collect_refs` solo tomaba
`ref[0]` cuando era string. Con un FROM parametrizado
`ref:[{"id":"3VF_X","args":{...}}]`, `ref[0]` es dict → no se extraía el id del
fact y se recursaba dentro de `args`, colectando el parámetro (`P_*`) en lugar de
la fuente real. Resultado: el cube perdía su upstream directo (el 3VF), que no
entraba a `_cube_subgraph` / `auto.artefactos` / lineage.

Contrato: la dependencia de fuente es el `id` del FROM; los refs dentro de `args`
son parámetros, NO upstreams. Fixtures sintéticas — sin catalog_local.
"""
import json
import sys
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parents[2] / "tools" / "catalog")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import generate_catalog as g  # noqa: E402


def _q(from_clause):
    return {"query": {"SELECT": {"from": from_clause, "columns": []}}}


def test_A_parameterized_from_returns_source_id():
    defn = _q({"ref": [{"id": "3VF_X", "args": {"P": {"param": True, "ref": ["P_VALUE"]}}}]})
    assert g._extract_query_sources(defn) == ["3VF_X"]


def test_B_multiple_args_no_parameter_leaks():
    defn = _q({"ref": [{"id": "3VF_X", "args": {
        "P1": {"param": True, "ref": ["P_ONE"]},
        "P2": {"param": True, "ref": ["P_TWO"]},
    }}]})
    src = g._extract_query_sources(defn)
    assert src == ["3VF_X"]
    assert "P_ONE" not in src and "P_TWO" not in src


def test_C_plain_from_unchanged():
    defn = _q({"ref": ["3VF_X"]})
    assert g._extract_query_sources(defn) == ["3VF_X"]


def test_D_parameterized_with_alias_source_is_id():
    defn = _q({"ref": [{"id": "3VF_X", "args": {"P": {"param": True, "ref": ["P_VALUE"]}}}],
               "as": "FACT_ALIAS"})
    # El alias es para resolución de columnas; la identidad del source es el id.
    assert g._extract_query_sources(defn) == ["3VF_X"]


def test_E_join_collects_each_source_not_params():
    # Join de una fuente parametrizada y una plana; sin fugas de parámetros.
    defn = _q({"join": "left",
               "args": [
                   {"ref": [{"id": "3VF_X", "args": {"P": {"param": True, "ref": ["P_VALUE"]}}}], "as": "A"},
                   {"ref": ["DIM_Y"], "as": "B"},
               ],
               "on": [{"ref": ["A", "k"]}, "=", {"ref": ["B", "k"]}]})
    src = set(g._extract_query_sources(defn))
    assert {"3VF_X", "DIM_Y"} <= src
    assert "P_VALUE" not in src


# ── End-to-end del generador: C → 3VF_X → 4AM_X parametrizado ────────────────
def _csn(path, definitions):
    path.write_text(json.dumps({"definitions": definitions}), encoding="utf-8")


def test_end_to_end_parameterized_cube_includes_fact(tmp_path):
    d = tmp_path / "Cli"
    d.mkdir()
    # C (fuente cruda) → 3VF_X (fact, from C) → 4AM_X (cube, from 3VF_X parametrizado)
    _csn(d / "C.json", {"C": {"kind": "entity", "elements": {"k": {"type": "cds.String", "length": 5}}}})
    _csn(d / "3VF_X.json", {"3VF_X": {
        "kind": "entity",
        "elements": {"M": {"type": "cds.Decimal", "precision": 13, "scale": 3}},
        "query": {"SELECT": {"from": {"ref": ["C"], "as": "C"}, "columns": []}},
    }})
    _csn(d / "4AM_X.json", {"4AM_X": {
        "kind": "entity",
        "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"},
        "@Analytics.dataCategory": {"#": "CUBE"},
        "elements": {"M": {"@AnalyticsDetails.measureType": {"#": "AGGREGATED"}}},
        "query": {"SELECT": {
            "from": {"ref": [{"id": "3VF_X", "args": {"P": {"param": True, "ref": ["P_VALUE"]}}}], "as": "F"},
            "columns": [{"ref": ["F", "M"], "as": "M"}],
        }},
    }})
    entities, _ = g.load_folder(d)
    assert "3VF_X" in entities
    cube = entities["4AM_X"]
    raw = getattr(cube, "_raw_refs", [])
    assert "3VF_X" in raw
    assert "P_VALUE" not in raw
    assert cube.upstreams == ["3VF_X"]
    sub = g._cube_subgraph(entities, cube)
    assert {"C", "3VF_X", "4AM_X"} <= set(sub)
