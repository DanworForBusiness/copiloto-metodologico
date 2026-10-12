"""Orden VERTICAL de los nodos dentro de cada columna del diagrama de linaje del Catálogo.

Contrato (solo presentación): se ajusta únicamente el criterio de ordenamiento existente (baricentro de los vecinos de las otras columnas, determinista); las columnas, los
nodos, las aristas y la numeración NO cambian; el orden nuevo reemplaza al actual SOLO si reduce estrictamente los cruces; sin mejora demostrable (o con un conflicto entre
múltiples conexiones) se conserva el orden actual. Sin casos por cliente/modelo: se prueba con grafos sintéticos que reproducen las situaciones observadas."""
from __future__ import annotations

import re

import pytest

import webapp.main as main


def _node(name, ups=(), num=0, assoc=()):
    return {"name": name, "label": name, "title": name, "layer": "armonizacion", "upstreams": list(ups), "assoc_upstreams": list(assoc), "slug": f"s-{name.lower()}", "num": num}


def _graph(spec: list[tuple[str, list[str]]]) -> list[dict]:
    """spec en el ORDEN VISIBLE (el que numera las secciones): [(nombre, [upstreams])]."""
    return [_node(n, ups, i + 1) for i, (n, ups) in enumerate(spec)]


def _render(nodes, monkeypatch, *, reorder: bool, show_assoc=False):
    if not reorder:
        monkeypatch.setattr(main, "_lineage_row_order", lambda by_col, cols, edges, sweeps=8: by_col)           # el comportamiento ANTERIOR (sin reordenar)
    return main._catalog_lineage_svg(nodes, show_assoc, {n["name"] for n in nodes})


def _parse(svg: str):
    """(posiciones {nombre: (x, y)}, aristas {(origen, destino)}, números {nombre: texto}) leídos del SVG."""
    rects = {}
    nums = {}
    for m in re.finditer(r'<a href="#[^"]*" class="[^"]*"><title>([^<]*)</title><rect class="[^"]*" x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"[^>]*/><text class="ln-num"[^>]*>(\d+)</text>', svg):
        name, x, y, w, h, num = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6)
        rects[name] = (x, y, w, h)
        nums[name] = num
    right = {(x + w, y + h // 2): n for n, (x, y, w, h) in rects.items()}
    left = {(x, y + h // 2): n for n, (x, y, w, h) in rects.items()}
    edges = set()
    for m in re.finditer(r'd="M(\d+),(\d+) C\d+,\d+ \d+,\d+ (\d+),(\d+)"', svg):
        edges.add((right[(int(m.group(1)), int(m.group(2)))], left[(int(m.group(3)), int(m.group(4)))]))
    return {n: (r[0], r[1]) for n, r in rects.items()}, edges, nums


def _crossings(svg: str) -> int:
    pos, edges, _ = _parse(svg)
    cols = sorted({x for x, _y in pos.values()})
    order = {c: [n for n, (x, y) in sorted(pos.items(), key=lambda kv: kv[1][1]) if x == c] for c in cols}
    return main._lineage_crossings(order, edges)


def _column_order(svg: str) -> dict:
    pos, _e, _n = _parse(svg)
    cols = sorted({x for x, _y in pos.values()})
    return {c: [n for n, (x, y) in sorted(pos.items(), key=lambda kv: kv[1][1]) if x == c] for c in cols}


# El caso observado (Biogenesis · Ocupación Almacenes): en la misma columna `1TR_ZA_OCU_AL` quedaba ARRIBA de `2VR_…` pero sus destinos estaban al revés.
OBSERVED = [("1TR_LAGP", []), ("1TR_LQUA", []), ("1TR_ZA_OCU_AL", []), ("2TR_OCUPACION_ALMACENES", ["2VR_OCUPACION_ALMACENES"]), ("2TR_OCUPACION_ALMACENES_H", ["1TR_ZA_OCU_AL"]),
            ("2VR_OCUPACION_ALMACENES", ["1TR_LAGP", "1TR_LQUA"]), ("3VF_OCUPACION_ALMACENES", ["2TR_OCUPACION_ALMACENES", "2TR_OCUPACION_ALMACENES_H"]),
            ("AM_OCUPACION_ALMACENES", ["3VF_OCUPACION_ALMACENES"])]


def test_the_observed_case_loses_its_crossing_and_keeps_nodes_edges_and_numbering(monkeypatch):
    nodes = _graph(OBSERVED)
    before = _render(nodes, monkeypatch, reorder=False)
    monkeypatch.undo()
    after = _render(nodes, monkeypatch, reorder=True)
    assert _crossings(before) == 1 and _crossings(after) == 0                                                  # reducción demostrada
    (pb, eb, nb), (pa, ea, na) = _parse(before), _parse(after)
    assert set(pb) == set(pa) and eb == ea and nb == na                                                       # mismos nodos, mismas aristas, misma numeración
    assert {n: x for n, (x, _y) in pb.items()} == {n: x for n, (x, _y) in pa.items()}                         # las columnas no cambian: solo el orden vertical
    col = _column_order(after)
    assert [n for n in col[sorted(col)[1]]] == ["2VR_OCUPACION_ALMACENES", "1TR_ZA_OCU_AL"]                   # cada nodo alineado con su destino


def test_the_result_is_deterministic(monkeypatch):
    nodes = _graph(OBSERVED)
    first = main._catalog_lineage_svg(nodes, False, {n["name"] for n in nodes})
    assert all(main._catalog_lineage_svg(_graph(OBSERVED), False, {n["name"] for n in nodes}) == first for _ in range(3))


CONVERGE_BRANCH = [("S1", []), ("S2", []), ("S3", []), ("S4", []), ("M1", ["S3", "S4"]), ("M2", ["S1", "S2"]), ("M3", ["S1"]), ("T1", ["M3", "M2"]), ("T2", ["M1", "M2"]), ("OUT", ["T1", "T2"])]


def test_a_model_with_converging_and_branching_dependencies_never_gets_worse_and_keeps_its_graph(monkeypatch):
    nodes = _graph(CONVERGE_BRANCH)
    before = _render(nodes, monkeypatch, reorder=False)
    monkeypatch.undo()
    after = _render(nodes, monkeypatch, reorder=True)
    (pb, eb, nb), (pa, ea, na) = _parse(before), _parse(after)
    assert _crossings(after) <= _crossings(before) and set(pb) == set(pa) and eb == ea and nb == na
    assert _crossings(after) < _crossings(before)                                                             # y aquí hay una disposición con menos cruces


def _complex_without_crossings():
    """Grafo amplio ya correctamente ordenado (0 cruces): dos cadenas paralelas que convergen al final."""
    spec = [(f"A{i}", [f"A{i - 1}"] if i else []) for i in range(6)] + [(f"B{i}", [f"B{i - 1}"] if i else []) for i in range(6)] + [("OUT", ["A5", "B5"])]
    return _graph(spec)


def test_a_complex_model_that_is_already_correct_keeps_its_exact_order(monkeypatch):
    nodes = _complex_without_crossings()
    before = _render(nodes, monkeypatch, reorder=False)
    monkeypatch.undo()
    after = _render(nodes, monkeypatch, reorder=True)
    assert _crossings(before) == 0 and after == before                                                        # byte a byte


def test_a_simple_linear_model_is_untouched(monkeypatch):
    nodes = _graph([("T0", []), ("V1", ["T0"]), ("V2", ["V1"]), ("AM", ["V2"])])
    before = _render(nodes, monkeypatch, reorder=False)
    monkeypatch.undo()
    assert main._catalog_lineage_svg(nodes, False, {n["name"] for n in nodes}) == before


def test_without_a_demonstrable_improvement_the_current_order_is_kept(monkeypatch):
    # bipartito completo K3,3: en dos columnas los cruces no dependen del orden → no hay mejora posible
    spec = [(f"U{i}", []) for i in range(3)] + [(f"V{i}", [f"U{j}" for j in range(3)]) for i in range(3)]
    nodes = _graph(spec)
    before = _render(nodes, monkeypatch, reorder=False)
    monkeypatch.undo()
    assert main._catalog_lineage_svg(nodes, False, {n["name"] for n in nodes}) == before


def test_the_row_order_never_moves_a_node_across_columns_or_drops_nodes():
    by_col = {0: [_node("a"), _node("b")], 1: [_node("c", ["b"]), _node("d", ["a"])]}
    out = main._lineage_row_order(by_col, [0, 1], [("b", "c"), ("a", "d")])
    assert sorted(n["name"] for n in out[0]) == ["a", "b"] and sorted(n["name"] for n in out[1]) == ["c", "d"]
    assert main._lineage_crossings({d: [n["name"] for n in out[d]] for d in out}, [("b", "c"), ("a", "d")]) == 0


def test_the_ordering_never_uses_names_layer_or_number():
    # mismo grafo con nombres/números invertidos: la reducción de cruces es la misma (no se ordena por nombre técnico ni por número)
    renamed = [("Z" + n[1:] if n.startswith("1TR") else n, [("Z" + u[1:] if u.startswith("1TR") else u) for u in ups]) for n, ups in OBSERVED]
    for spec in (OBSERVED, renamed, list(reversed(OBSERVED))):
        nodes = _graph(spec)
        assert _crossings(main._catalog_lineage_svg(nodes, False, {n["name"] for n in nodes})) == 0
