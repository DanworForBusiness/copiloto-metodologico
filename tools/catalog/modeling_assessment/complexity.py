"""Metricas ESTRUCTURALES de una vista desde el CSN compilado (S6, evidence release). Solo HECHOS descriptivos: sin umbral, sin puntaje compuesto y sin direccionalidad.

El tipo de vista (GRAPHICAL | SQL) es UNA dimension descriptiva mas: ni ``GRAPHICAL`` es malo ni ``SQL`` es bueno. Las metricas se leen del arbol compilado
(``query.SELECT`` / ``query.SET`` con ``from``/``join``/``columns``/``where``/``groupBy``/``having``): no se parsea texto SQL.

Metricas por vista (solo las extraibles de forma confiable):
  select_nodes       cantidad de nodos SELECT (rama de SET, subselect anidado)
  graph_depth        profundidad maxima de anidado de nodos SELECT (1 = un solo SELECT)
  providers          nombres distintos de artefactos leidos en los ``from`` de todos los nodos (el hecho filtra los que existen en el pool)
  joins / join_types cantidad de joins y su tipo (``left``/``inner``/...)
  unions             argumentos de SET menos uno, sumados
  output_columns     columnas del SELECT externo (primera rama)
  columns_total      columnas sumadas de todos los nodos SELECT
  derived_columns    columnas derivadas (``xpr``/``func``/``val``/``cast``) de todos los nodos
  filters            nodos con ``where`` / con ``having``
  predicate_atoms    atomos de predicado (``where``/``having``/``on``) no triviales y triviales
  function_calls     nodos ``func`` del arbol
  expressions        ocurrencias de expresion no triviales (derivadas + GROUP BY + predicados no triviales)
"""
from __future__ import annotations

from typing import Any

from .derivation import _is_derived, _is_trivial, _join_on_clauses, _split_atoms, _walk, extract_expression_surface, iter_select_nodes

COMPLEXITY_EXTRACTOR_VERSION = "view-complexity/v1"


def _from_providers_and_joins(frm: Any, providers: set[str], join_types: dict[str, int]) -> None:
    if not isinstance(frm, dict) or "SELECT" in frm or "SET" in frm:
        return
    if "join" in frm:
        jt = str(frm["join"])
        join_types[jt] = join_types.get(jt, 0) + 1
    ref = frm.get("ref")
    if isinstance(ref, list) and ref and isinstance(ref[0], str):
        providers.add(ref[0])
    for a in frm.get("args") or []:
        _from_providers_and_joins(a, providers, join_types)


def _count_sets(node: Any) -> int:
    n = 0

    def f(x: dict) -> None:
        nonlocal n
        if isinstance(x.get("SET"), dict):
            n += max(0, len(x["SET"].get("args") or []) - 1)
    _walk(node, f)
    return n


def view_structure(artifact: str, definition: dict) -> dict | None:
    """Metricas estructurales de ``definition`` (JSON-safe) o None si no tiene ``query``."""
    nodes = iter_select_nodes(definition)
    if not nodes:
        return None
    providers: set[str] = set()
    join_types: dict[str, int] = {}
    columns_total = derived = where_nodes = having_nodes = atoms_nontrivial = atoms_trivial = 0
    for sn in nodes:
        sel = sn.select
        _from_providers_and_joins(sel.get("from"), providers, join_types)
        cols = [c for c in (sel.get("columns") or []) if isinstance(c, dict)]
        columns_total += len(cols)
        derived += sum(1 for c in cols if _is_derived(c))
        where_nodes += 1 if sel.get("where") else 0
        having_nodes += 1 if sel.get("having") else 0
        for tokens in [sel.get("where"), sel.get("having"), *_join_on_clauses(sel.get("from"))]:
            if tokens:
                for atom in _split_atoms(tokens):
                    if _is_trivial(atom):
                        atoms_trivial += 1
                    else:
                        atoms_nontrivial += 1
    funcs = 0

    def f(n: dict) -> None:
        nonlocal funcs
        if "func" in n:
            funcs += 1
    _walk(definition.get("query"), f)
    surface = extract_expression_surface(artifact, definition)
    outer = [c for c in (nodes[0].select.get("columns") or []) if isinstance(c, dict)]
    elements = definition.get("elements")
    return {"extractor_version": COMPLEXITY_EXTRACTOR_VERSION, "select_nodes": len(nodes), "graph_depth": max(n.depth for n in nodes) + 1,
            "provider_names": sorted(providers), "joins": sum(join_types.values()), "join_types": [{"type": k, "count": v} for k, v in sorted(join_types.items())],
            "unions": _count_sets(definition.get("query")), "output_columns": len(outer), "columns_total": columns_total, "derived_columns": derived,
            "filters": {"where_nodes": where_nodes, "having_nodes": having_nodes},
            "predicate_atoms": {"non_trivial": atoms_nontrivial, "trivial": atoms_trivial}, "function_calls": funcs,
            "expressions": len(surface.occurrences) if surface is not None else 0,
            "elements": len(elements) if isinstance(elements, dict) else None}
