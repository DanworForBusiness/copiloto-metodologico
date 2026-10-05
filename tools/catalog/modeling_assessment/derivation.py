"""Extraccion ESTRUCTURAL de derivaciones desde el CSN COMPILADO (``query.SELECT`` / ``query.SET``) — G3. Solo HECHOS, sin juicio.

El export de Datasphere trae, tanto para vistas SQL como para vistas graficas, el query compilado como ARBOL JSON (``xpr`` / ``func`` / ``ref`` /
``val`` / ``cast`` / ``as``, mas ``from`` con alias). Este modulo recorre ese arbol: NO parsea texto SQL, NO usa regex y NO infiere semantica de
negocio. Lo que no se puede resolver estructuralmente queda como ambiguedad EXPLICITA (``ambiguity``), nunca como un valor por defecto.

Taxonomia de operaciones (pequena y estructural): PASSTHROUGH, CAST, ARITHMETIC, CONDITIONAL, FUNCTION, AGGREGATION, CONCATENATION, LITERAL, OTHER.

Huellas:
  * ``exact_fp``  estructura normalizada: se descarta el calificador/alias de las referencias (queda solo el nombre del campo), se conservan los
                  literales, el ``cast`` y los nombres de funciones/palabras clave. Un cambio de alias NO altera la huella exacta.
  * ``shape_fp``  forma: referencias y literales abstraidos. Solo la forma (operadores/funciones/palabras clave) cuenta.

Relacion entre dos derivaciones del MISMO nombre de campo (contrato fijo del owner, G3):
  DUPLICATED_TRANSFORMATION  la transformacion se REPITE (estructura exacta igual) en vez de consumir el campo ya derivado;
  CHAINED_REFINEMENT         la expresion del consumidor LEE el campo ya derivado del proveedor (y agrega/refina trabajo). NO es una desviacion;
  DISTINCT_TRANSFORMATION    mismo nombre, pero estructura y trabajo distintos;
  UNKNOWN                    evidencia insuficiente o estructuralmente ambigua (referencia no calificada con varios origenes, subselect, ...).
Un nombre de campo repetido, por si solo, NO decide nada.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

DERIVATION_EXTRACTOR_VERSION = "derivation-extractor/v1"

OP_PASSTHROUGH, OP_CAST, OP_ARITHMETIC, OP_CONDITIONAL = "PASSTHROUGH", "CAST", "ARITHMETIC", "CONDITIONAL"
OP_FUNCTION, OP_AGGREGATION, OP_CONCATENATION, OP_LITERAL, OP_OTHER = "FUNCTION", "AGGREGATION", "CONCATENATION", "LITERAL", "OTHER"
OPERATION_CLASSES = (OP_PASSTHROUGH, OP_CAST, OP_ARITHMETIC, OP_CONDITIONAL, OP_FUNCTION, OP_AGGREGATION, OP_CONCATENATION, OP_LITERAL, OP_OTHER)

REL_DUPLICATED = "DUPLICATED_TRANSFORMATION"
REL_CHAINED = "CHAINED_REFINEMENT"
REL_DISTINCT = "DISTINCT_TRANSFORMATION"
REL_UNKNOWN = "UNKNOWN"
RELATIONS = (REL_DUPLICATED, REL_CHAINED, REL_DISTINCT, REL_UNKNOWN)

# Estados de una referencia de entrada
IN_RESOLVED, IN_PARAMETER = "RESOLVED", "PARAMETER"
IN_UNQUALIFIED_MULTI, IN_SUBSELECT, IN_UNRESOLVED_ALIAS = "UNQUALIFIED_MULTI_SOURCE", "SUBSELECT_SOURCE", "UNRESOLVED_ALIAS"
_UNRESOLVED_STATES = frozenset({IN_UNQUALIFIED_MULTI, IN_SUBSELECT, IN_UNRESOLVED_ALIAS})

AMB_SUBQUERY = "SUBQUERY_IN_EXPRESSION"

_AGGREGATES = frozenset({"SUM", "COUNT", "AVG", "MIN", "MAX", "STDDEV", "VAR", "COUNT_DISTINCT", "STRING_AGG", "FIRST_VALUE", "LAST_VALUE", "MEDIAN"})
_ARITH_TOKENS = frozenset({"+", "-", "*", "/", "%"})
_KEYWORDS = frozenset({"case", "when", "then", "else", "end", "and", "or", "not", "in", "is", "null", "like", "between", "exists", "distinct"})


@dataclass(frozen=True)
class InputRef:
    provider: str | None            # artefacto del que se lee (alias resuelto) o None si no se pudo resolver
    field: str
    state: str

    def to_json(self) -> dict:
        return {"provider": self.provider, "field": self.field, "state": self.state}


@dataclass(frozen=True)
class DerivedColumn:
    artifact: str
    field: str
    branch: int                     # 0 para SELECT simple; indice de la rama para SET
    operations: tuple[str, ...]
    functions: tuple[str, ...]
    inputs: tuple[InputRef, ...]
    exact_fp: str
    shape_fp: str
    ambiguity: tuple[str, ...]      # codigos; vacio = estructuralmente resuelto

    @property
    def resolvable(self) -> bool:
        return not self.ambiguity

    def reads(self, field: str, providers: Iterable[str]) -> bool | None:
        """¿Lee ``field`` de alguno de ``providers``? True/False si es determinable; None si hay referencias a ``field`` sin resolver."""
        provs = set(providers)
        if any(i.field == field and i.state == IN_RESOLVED and i.provider in provs for i in self.inputs):
            return True
        if any(i.field == field and i.state in _UNRESOLVED_STATES for i in self.inputs):
            return None
        return False

    def to_json(self) -> dict:
        return {"artifact": self.artifact, "field": self.field, "branch": self.branch, "operations": list(self.operations), "functions": list(self.functions),
                "inputs": [i.to_json() for i in self.inputs], "exact_fp": self.exact_fp, "shape_fp": self.shape_fp,
                "resolvable": self.resolvable, "ambiguity": list(self.ambiguity)}


# ───────────────────────────── recorrido del arbol ─────────────────────────────
def _walk(node: Any, fn) -> None:
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            _walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk(v, fn)


def _segment(x: Any) -> str:
    """Un elemento de ruta ``ref`` es un string o un objeto con ``id`` (entidad parametrizada)."""
    if isinstance(x, str):
        return x
    if isinstance(x, dict) and isinstance(x.get("id"), str):
        return x["id"]
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def select_branches(definition: dict) -> list[dict]:
    """Los SELECT del query compilado, en orden: ``query.SELECT`` o las ramas de ``query.SET`` (recursivo). Sin ``query``: lista vacia."""
    out: list[dict] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if isinstance(node.get("SELECT"), dict):
            out.append(node["SELECT"])
        elif isinstance(node.get("SET"), dict):
            for a in node["SET"].get("args") or []:
                visit(a)

    visit(definition.get("query") if isinstance(definition, dict) else None)
    return out


def _source_aliases(frm: Any, out: dict[str, tuple[str, str | None]] | None = None) -> dict[str, tuple[str, str | None]]:
    """alias (o nombre de entidad) -> ("ENTITY", nombre) | ("SUBSELECT", None)."""
    out = {} if out is None else out
    if not isinstance(frm, dict):
        return out
    ref = frm.get("ref")
    if isinstance(ref, list) and ref:
        ent = _segment(ref[0])
        out[str(frm.get("as") or ent)] = ("ENTITY", ent)
        out.setdefault(ent, ("ENTITY", ent))
    elif "SELECT" in frm or "SET" in frm:
        out[str(frm.get("as") or f"<subselect{len(out)}>")] = ("SUBSELECT", None)
    for a in frm.get("args") or []:
        _source_aliases(a, out)
    return out


def _is_derived(col: dict) -> bool:
    if any(k in col for k in ("xpr", "func", "val")):
        return True
    found = []
    _walk(col, lambda n: found.append(1) if "cast" in n else None)
    return bool(found)


def _output_name(col: dict) -> str | None:
    if isinstance(col.get("as"), str) and col["as"]:
        return col["as"]
    ref = col.get("ref")
    if isinstance(ref, list) and ref:
        return _segment(ref[-1])
    return None


def _classify_ops(col: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ops: set[str] = set()
    funcs: set[str] = set()

    def f(n: dict) -> None:
        if "func" in n:
            name = str(n["func"]).upper()
            if name in _AGGREGATES:
                ops.add(OP_AGGREGATION)
            else:
                ops.add(OP_FUNCTION)
                funcs.add(name)
        if isinstance(n.get("xpr"), list):
            toks = [t for t in n["xpr"] if isinstance(t, str)]
            if any(t.lower() == "case" for t in toks):
                ops.add(OP_CONDITIONAL)
            if any(t in _ARITH_TOKENS for t in toks):
                ops.add(OP_ARITHMETIC)
            if any(t == "||" for t in toks):
                ops.add(OP_CONCATENATION)
        if "cast" in n:
            ops.add(OP_CAST)
    _walk(col, f)
    if not ops:
        ops.add(OP_PASSTHROUGH if "ref" in col else (OP_LITERAL if "val" in col else OP_OTHER))
    return tuple(sorted(ops)), tuple(sorted(funcs))


def _canon(node: Any, exact: bool) -> Any:
    if isinstance(node, dict):
        if isinstance(node.get("ref"), list):
            last = _segment(node["ref"][-1]) if node["ref"] else None
            out: dict[str, Any] = {"REF": last if exact else "*"}
            if "cast" in node and exact:
                out["cast"] = _canon(node["cast"], True)
            return out
        if "val" in node:
            return {"VAL": node["val"] if exact else "*"}
        out = {}
        for k, v in sorted(node.items()):
            if k == "as" or (k == "cast" and not exact):
                continue
            out[k] = _canon(v, exact)
        return out
    if isinstance(node, list):
        return [_canon(x, exact) for x in node]
    if isinstance(node, str):
        return node.lower() if node.lower() in _KEYWORDS else (node.upper() if node.upper() in _AGGREGATES else node)
    return node


def _fp(col: dict, exact: bool) -> str:
    body = json.dumps(_canon(col, exact), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def _inputs(col: dict, aliases: dict[str, tuple[str, str | None]]) -> tuple[tuple[InputRef, ...], list[str]]:
    refs: list[list[str]] = []
    amb: list[str] = []

    def f(n: dict) -> None:
        if isinstance(n.get("ref"), list):
            refs.append([_segment(x) for x in n["ref"]])
        if "SELECT" in n or "SET" in n:
            amb.append(AMB_SUBQUERY)
    _walk(col, f)
    # un mismo origen puede figurar bajo alias y bajo nombre de entidad: se cuenta por ORIGEN distinto
    distinct_sources = {v for v in aliases.values()}
    out: list[InputRef] = []
    for path in refs:
        if not path:
            continue
        if path[0].startswith("$"):
            out.append(InputRef(None, path[-1], IN_PARAMETER))
            continue
        field = path[-1]
        if len(path) >= 2:
            src = aliases.get(path[0])
            if src is None:
                out.append(InputRef(None, field, IN_UNRESOLVED_ALIAS))
            elif src[0] == "SUBSELECT":
                out.append(InputRef(None, field, IN_SUBSELECT))
            else:
                out.append(InputRef(src[1], field, IN_RESOLVED))
        else:
            if len(distinct_sources) == 1:
                src = next(iter(distinct_sources))
                out.append(InputRef(src[1], field, IN_RESOLVED) if src[0] == "ENTITY" else InputRef(None, field, IN_SUBSELECT))
            else:
                out.append(InputRef(None, field, IN_UNQUALIFIED_MULTI))
    uniq = sorted({(i.provider or "", i.field, i.state): i for i in out}.items())
    return tuple(i for _, i in uniq), amb


def extract_derived_columns(artifact: str, definition: dict) -> tuple[DerivedColumn, ...]:
    """Columnas DERIVADAS (``xpr``/``func``/``val`` o con ``cast``) de cada rama del query de ``definition``. Determinista; sin ``query`` => ()."""
    out: list[DerivedColumn] = []
    for bi, sel in enumerate(select_branches(definition)):
        aliases = _source_aliases(sel.get("from"))
        for col in sel.get("columns") or []:
            if not isinstance(col, dict) or not _is_derived(col):
                continue
            name = _output_name(col)
            if not name:
                continue
            ins, amb = _inputs(col, aliases)
            ambiguity = set(amb)
            for i in ins:
                if i.state in _UNRESOLVED_STATES:
                    ambiguity.add(i.state)
            ops, funcs = _classify_ops(col)
            out.append(DerivedColumn(artifact, name, bi, ops, funcs, ins, _fp(col, True), _fp(col, False), tuple(sorted(ambiguity))))
    return tuple(sorted(out, key=lambda c: (c.field, c.branch)))


# ───────────────────────────── relacion entre dos derivaciones ─────────────────────────────
def classify_relation(provider_cols: Iterable[DerivedColumn], consumer_cols: Iterable[DerivedColumn], *, field: str,
                      chain_sources: Iterable[str]) -> tuple[str, str]:
    """``(relation, reason)`` entre la derivacion de ``field`` en un ancestro (``provider_cols``) y en el consumidor (``consumer_cols``).

    ``chain_sources``: artefactos desde los que el consumidor puede leer el campo ya derivado (sus proveedores directos de linaje que son el
    proveedor/ancestro o descienden de el). Prioridad: CAUSALIDAD estructural (¿el consumidor lee el campo derivado?) antes que cualquier
    comparacion de huellas; ante duda, UNKNOWN (nunca un valor por defecto)."""
    prov, cons = list(provider_cols), list(consumer_cols)
    if not prov or not cons:
        return REL_UNKNOWN, "NO_QUERY_EVIDENCE"
    reads = [c.reads(field, chain_sources) for c in cons]
    if all(r is True for r in reads):
        return REL_CHAINED, "CONSUMER_READS_DERIVED_PROVIDER_FIELD"
    if any(r is True for r in reads):
        return REL_UNKNOWN, "BRANCHES_DISAGREE"
    if any(r is None for r in reads):
        return REL_UNKNOWN, "AMBIGUOUS_REFERENCE_TO_SAME_FIELD"
    if len(prov) > 1 or len(cons) > 1:
        return REL_UNKNOWN, "MULTIPLE_BRANCHES"
    p, c = prov[0], cons[0]
    if p.exact_fp == c.exact_fp:
        return REL_DUPLICATED, "IDENTICAL_STRUCTURE"
    if p.ambiguity or c.ambiguity:
        return REL_UNKNOWN, (c.ambiguity or p.ambiguity)[0]
    if p.shape_fp == c.shape_fp and {i.field for i in p.inputs} == {i.field for i in c.inputs}:
        return REL_UNKNOWN, "NEAR_EQUIVALENT_UNPROVEN"        # misma forma e insumos; difieren solo en literales/cast: no se afirma duplicacion
    return REL_DISTINCT, "DIFFERENT_STRUCTURE"
