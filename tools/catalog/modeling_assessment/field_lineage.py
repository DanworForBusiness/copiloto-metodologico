"""Linaje de CAMPO desde el CSN COMPILADO (``query``): a nivel de columna, a traves de subselects anidados, joins y uniones. Solo HECHOS de procedencia; ningun juicio.

Reutiliza la infraestructura de ``derivation.py`` (misma disciplina: se recorre el ARBOL compilado, sin texto SQL ni regex) y la EXTIENDE donde esa infraestructura se detiene:
``derivation`` resuelve el SELECT EXTERNO y marca ``SUBSELECT_SOURCE`` como ambiguo; las vistas graficas compilan a una cadena de SELECT anidados, asi que aqui se DESCIENDE por
ellos hasta las entidades. Dos fases:

  1. ``extract_query_ir(definition)``  (puro, sin entidades): IR del query -> ``SelectIR`` / ``SetIR`` (fuentes con alias, columnas de salida con sus referencias).
  2. ``resolve_query(ir, fields_of)``  (necesita la lista de campos de las entidades para desambiguar una referencia NO calificada): para cada columna de salida, sus ORIGENES
     ``(entidad proveedora, campo, tipo de mapeo)`` o una AMBIGUEDAD explicita.

NUNCA se elige un proveedor arbitrario: una referencia no calificada que mas de una fuente podria satisfacer, un alias sin resolver, un ``*`` sobre una fuente sin lista de campos,
una subconsulta dentro de una expresion o una union de aridad distinta quedan como ``ambiguity`` explicita. La ambiguedad afecta la evaluabilidad (completitud del linaje), nunca la calidad.

Tipo de mapeo de un salto (entidad -> sus proveedores): IDENTITY (referencia simple, mismo nombre) · RENAME (referencia simple, otro nombre) · CAST (referencia simple con ``cast``) ·
DERIVED (expresion/funcion con UN insumo) · EXPRESSION (expresion con >= 2 insumos) · UNKNOWN. Los subselects anidados DENTRO de una misma entidad se componen en UN salto
(gana el tipo de mapeo mas fuerte: IDENTITY < RENAME < CAST < DERIVED < EXPRESSION).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .derivation import _output_name, _segment, _walk

FIELD_LINEAGE_EXTRACTOR_VERSION = "field-lineage/v1"

MT_IDENTITY, MT_RENAME, MT_CAST, MT_DERIVED, MT_EXPRESSION, MT_UNKNOWN = "IDENTITY", "RENAME", "CAST", "DERIVED", "EXPRESSION", "UNKNOWN"
MAPPING_TYPES = (MT_IDENTITY, MT_RENAME, MT_CAST, MT_DERIVED, MT_EXPRESSION, MT_UNKNOWN)
_RANK = {MT_IDENTITY: 0, MT_RENAME: 1, MT_CAST: 2, MT_DERIVED: 3, MT_EXPRESSION: 4}

AMB_UNQUALIFIED_MULTI = "UNQUALIFIED_MULTI_SOURCE"
AMB_UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"
AMB_STAR = "STAR_ON_UNKNOWN_SOURCE"
AMB_SET_ARITY = "SET_ARITY_MISMATCH"
AMB_SUBQUERY = "SUBQUERY_IN_EXPRESSION"
AMB_NO_OUTPUT_FIELD = "FIELD_NOT_IN_SOURCE_OUTPUT"
AMB_UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
AMBIGUITY_CODES = frozenset({AMB_UNQUALIFIED_MULTI, AMB_UNRESOLVED_ALIAS, AMB_STAR, AMB_SET_ARITY, AMB_SUBQUERY, AMB_NO_OUTPUT_FIELD, AMB_UNKNOWN_SOURCE})

SHAPE_REF, SHAPE_EXPR, SHAPE_VAL, SHAPE_STAR = "REF", "EXPR", "VAL", "STAR"


def compose(a: str, b: str) -> str:
    """Tipo de mapeo compuesto de dos saltos consecutivos (gana el mas fuerte). UNKNOWN contagia."""
    if MT_UNKNOWN in (a, b):
        return MT_UNKNOWN
    return a if _RANK[a] >= _RANK[b] else b


# ───────────────────────────── IR ─────────────────────────────
@dataclass(frozen=True)
class RefIn:
    alias: str | None               # primer segmento cuando la ruta tiene >= 2 segmentos (alias o nombre de entidad)
    field: str
    param: bool = False


@dataclass(frozen=True)
class OutCol:
    name: str | None
    shape: str                      # REF | EXPR | VAL | STAR
    cast: bool
    refs: tuple[RefIn, ...]
    subquery: bool = False
    star_alias: str | None = None


@dataclass(frozen=True)
class SelectIR:
    sources: tuple[tuple[str, str, Any], ...]      # (alias, "ENTITY"|"QUERY"|"UNKNOWN", nombre de entidad | QueryIR | None)
    columns: tuple[OutCol, ...]


@dataclass(frozen=True)
class SetIR:
    branches: tuple[Any, ...]


def _sources(frm: Any, out: list[tuple[str, str, Any]]) -> None:
    if not isinstance(frm, dict):
        return
    ref = frm.get("ref")
    if isinstance(ref, list) and ref:
        ent = _segment(ref[0])
        out.append((str(frm.get("as") or ent), "ENTITY", ent))
        if frm.get("as") and str(frm["as"]) != ent:
            out.append((ent, "ENTITY", ent))              # tambien resoluble por nombre de entidad (igual que ``derivation._source_aliases``)
    elif "SELECT" in frm or "SET" in frm:
        out.append((str(frm.get("as") or f"<subselect{len(out)}>"), "QUERY", _query(frm)))
    for a in frm.get("args") or []:
        _sources(a, out)


def _column(col: Any) -> OutCol | None:
    if col == "*":
        return OutCol(None, SHAPE_STAR, False, ())
    if not isinstance(col, dict):
        return None
    ref = col.get("ref")
    if isinstance(ref, list) and ref and _segment(ref[-1]) == "*":
        return OutCol(None, SHAPE_STAR, False, (), star_alias=_segment(ref[0]) if len(ref) >= 2 else None)
    name = _output_name(col)
    if not name:
        return None
    content = {k for k in col if not str(k).startswith("@")} - {"as", "key", "cast", "ref"}
    if isinstance(ref, list) and ref and not content:
        path = [_segment(x) for x in ref]
        r = RefIn(path[0] if len(path) >= 2 else None, path[-1], param=path[0].startswith("$") or col.get("param") is True)
        return OutCol(name, SHAPE_REF, "cast" in col, (r,))
    refs: list[RefIn] = []
    sub = [False]

    def f(n: dict) -> None:
        if isinstance(n.get("ref"), list) and n["ref"]:
            path = [_segment(x) for x in n["ref"]]
            refs.append(RefIn(path[0] if len(path) >= 2 else None, path[-1], param=path[0].startswith("$") or n.get("param") is True))   # ``"param": true`` = parametro de entrada, no una columna
        if "SELECT" in n or "SET" in n:
            sub[0] = True
    _walk({k: v for k, v in col.items() if k != "as"}, f)
    uniq = tuple(sorted({(r.alias or "", r.field, r.param): r for r in refs}.values(), key=lambda r: (r.alias or "", r.field, r.param)))
    shape = SHAPE_VAL if ("val" in col and not uniq and not sub[0]) else SHAPE_EXPR
    return OutCol(name, shape, "cast" in col, uniq, sub[0])


def _query(node: Any) -> Any:
    """``SelectIR`` / ``SetIR`` de un nodo ``{"SELECT": ...}`` o ``{"SET": ...}`` (o None si no es un query)."""
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("SELECT"), dict):
        sel = node["SELECT"]
        srcs: list[tuple[str, str, Any]] = []
        _sources(sel.get("from"), srcs)
        cols = tuple(c for c in (_column(x) for x in (sel.get("columns") or [])) if c is not None)
        return SelectIR(tuple(srcs), cols)
    if isinstance(node.get("SET"), dict):
        return SetIR(tuple(q for q in (_query(a) for a in (node["SET"].get("args") or [])) if q is not None))
    return None


def extract_query_ir(definition: Any) -> Any:
    """IR del ``query`` de ``definition`` o None si no trae query. Determinista y puro."""
    return _query(definition.get("query")) if isinstance(definition, dict) else None


# ───────────────────────────── resolucion ─────────────────────────────
@dataclass(frozen=True)
class Origin:
    """Un origen estructural de una columna de salida: la entidad proveedora y su campo (``provider`` None si no hay insumo de campo o hay ambiguedad)."""
    provider: str | None
    field: str | None
    mapping_type: str
    ambiguity: str | None = None

    def to_json(self) -> dict:
        return {"provider": self.provider, "field": self.field, "mapping_type": self.mapping_type, "ambiguity": self.ambiguity}


Resolution = dict[str, list[Origin]]          # nombre de salida -> origenes (orden de aparicion; sin duplicados)


def _add(out: Resolution, name: str, origin: Origin) -> None:
    lst = out.setdefault(name, [])
    if origin not in lst:
        lst.append(origin)


def resolve_query(ir: Any, fields_of: Callable[[str], tuple[str, ...] | None]) -> Resolution:
    """Origenes de cada columna de salida de ``ir``. ``fields_of(entidad) -> campos`` (None si la entidad es desconocida: no se puede desambiguar con ella)."""
    memo: dict[int, Resolution] = {}

    def go(node: Any) -> Resolution:
        key = id(node)
        if key in memo:
            return memo[key]
        memo[key] = {}                                      # guarda contra ciclos (el IR es un arbol, pero no se asume)
        res = _set(node) if isinstance(node, SetIR) else _select(node) if isinstance(node, SelectIR) else {}
        memo[key] = res
        return res

    def _set(node: SetIR) -> Resolution:
        branches = [go(b) for b in node.branches]
        names = [list(r) for r in branches]
        if not branches:
            return {}
        first = names[0]
        out: Resolution = {n: [] for n in first}
        for bi, r in enumerate(branches):
            order = names[bi]
            for i, n in enumerate(first):
                if len(order) != len(first):
                    _add(out, n, Origin(None, None, MT_UNKNOWN, AMB_SET_ARITY))
                    continue
                for o in r[order[i]]:
                    _add(out, n, o)
        return out

    def _select(sel: SelectIR) -> Resolution:
        by_alias: dict[str, tuple[str, Any, Resolution | None, tuple[str, ...] | None]] = {}
        for alias, kind, target in sel.sources:
            if kind == "ENTITY":
                by_alias.setdefault(alias, ("ENTITY", target, None, fields_of(target)))
            elif kind == "QUERY":
                sub = go(target)
                by_alias.setdefault(alias, ("QUERY", target, sub, tuple(sub)))
            else:
                by_alias.setdefault(alias, ("UNKNOWN", None, None, None))
        distinct = {(v[0], v[1] if v[0] == "ENTITY" else id(v[1])): k for k, v in by_alias.items()}
        out: Resolution = {}

        def step(src_alias: str | None, field: str, outer_kind: str) -> list[Origin]:
            """Origenes de la referencia (``src_alias``, ``field``) con el tipo de mapeo del salto exterior ``outer_kind``."""
            cands: list[str]
            if src_alias is not None:
                if src_alias not in by_alias:
                    return [Origin(None, None, MT_UNKNOWN, AMB_UNRESOLVED_ALIAS)]
                cands = [src_alias]
            else:
                if len(distinct) == 1:
                    cands = [next(iter(distinct.values()))]
                else:
                    # varias fuentes: solo se resuelve si EXACTAMENTE una fuente distinta expone el campo y ninguna otra fuente es desconocida (nunca se elige una al azar)
                    holders = {(v[0], v[1] if v[0] == "ENTITY" else id(v[1])): a for a, v in by_alias.items() if v[3] is not None and field in v[3]}
                    unknown_present = any(v[0] == "UNKNOWN" or (v[0] == "ENTITY" and v[3] is None) for v in by_alias.values())
                    if len(holders) != 1 or unknown_present:
                        return [Origin(None, None, MT_UNKNOWN, AMB_UNQUALIFIED_MULTI)]
                    cands = [next(iter(holders.values()))]
            kind, target, sub, flds = by_alias[cands[0]]
            if kind == "UNKNOWN":
                return [Origin(None, None, MT_UNKNOWN, AMB_UNKNOWN_SOURCE)]
            if kind == "ENTITY":
                return [Origin(target, field, outer_kind)]
            origins = sub.get(field)
            if origins is None:
                return [Origin(None, None, MT_UNKNOWN, AMB_NO_OUTPUT_FIELD)]
            return [o if o.ambiguity else Origin(o.provider, o.field, compose(outer_kind, o.mapping_type)) for o in origins]

        def emit(name: str, col: OutCol) -> None:
            if col.shape == SHAPE_REF:
                r = col.refs[0]
                if r.param:
                    _add(out, name, Origin(None, None, MT_DERIVED))
                    return
                outer = MT_CAST if col.cast else (MT_IDENTITY if name == r.field else MT_RENAME)
                for o in step(r.alias, r.field, outer):
                    _add(out, name, o)
                return
            real = [r for r in col.refs if not r.param]
            if col.subquery:
                _add(out, name, Origin(None, None, MT_UNKNOWN, AMB_SUBQUERY))
            if not real:
                _add(out, name, Origin(None, None, MT_DERIVED))          # constante / funcion sin insumo de campo: sin proveedor, sin ambiguedad
                return
            kind = MT_EXPRESSION if len(real) >= 2 else MT_DERIVED
            for r in real:
                for o in step(r.alias, r.field, kind):
                    _add(out, name, o)

        for col in sel.columns:
            if col.shape != SHAPE_STAR:
                emit(col.name or "", col)
                continue
            srcs = [col.star_alias] if col.star_alias is not None else list(distinct.values())
            expanded = False
            for a in srcs:
                v = by_alias.get(a)
                if v is None or v[3] is None:
                    _add(out, f"*{a or ''}", Origin(None, None, MT_UNKNOWN, AMB_STAR))
                    continue
                for fld in v[3]:
                    for o in step(a, fld, MT_IDENTITY):
                        _add(out, fld, o)
                    expanded = True
            if not srcs and not expanded:
                _add(out, "*", Origin(None, None, MT_UNKNOWN, AMB_STAR))
        return out

    return go(ir)
