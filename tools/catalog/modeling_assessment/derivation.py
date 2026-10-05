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
AMBIGUITY_CODES = frozenset({IN_UNQUALIFIED_MULTI, IN_SUBSELECT, IN_UNRESOLVED_ALIAS, AMB_SUBQUERY})
INPUT_STATES = frozenset({IN_RESOLVED, IN_PARAMETER, IN_UNQUALIFIED_MULTI, IN_SUBSELECT, IN_UNRESOLVED_ALIAS})

# Base de la exposicion por la raiz: coincidencia de NOMBRE con el contrato de la raiz. NO es linaje de campo probado (G5 sigue abierto): descriptiva.
EXPOSURE_BASIS_SAME_NAME = "SAME_NAME_IN_ROOT_CONTRACT"

# Razones permitidas por relacion (contrato de G3): una relacion y su razon son coherentes entre si; la razon de UNKNOWN puede ser ademas un codigo de ambiguedad.
RELATION_REASONS = {
    "DUPLICATED_TRANSFORMATION": frozenset({"IDENTICAL_STRUCTURE"}),
    "CHAINED_REFINEMENT": frozenset({"CONSUMER_READS_DERIVED_PROVIDER_FIELD"}),
    "DISTINCT_TRANSFORMATION": frozenset({"DIFFERENT_STRUCTURE"}),
    "UNKNOWN": frozenset({"NO_QUERY_EVIDENCE", "BRANCHES_DISAGREE", "AMBIGUOUS_REFERENCE_TO_SAME_FIELD", "MULTIPLE_BRANCHES", "NEAR_EQUIVALENT_UNPROVEN"}) | AMBIGUITY_CODES,
}

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


# ═════════════════════════ S3/S4 (evidence release): clausulas, predicados y proyecciones ═════════════════════════
# Misma disciplina que G3: se recorre el ARBOL compilado (sin parsear texto SQL ni usar regex) y se reutiliza el canonicalizador de huellas. Las huellas son
# genericas sobre cualquier subarbol, asi que cubren WHERE / JOIN ON / GROUP BY / HAVING sin cambiar el algoritmo.
EXPRESSION_EXTRACTOR_VERSION = "expression-occurrences/v1"

CLAUSE_SELECT, CLAUSE_WHERE, CLAUSE_GROUP_BY, CLAUSE_HAVING, CLAUSE_JOIN_ON = "SELECT", "WHERE", "GROUP_BY", "HAVING", "JOIN_ON"
CLAUSES = (CLAUSE_SELECT, CLAUSE_WHERE, CLAUSE_GROUP_BY, CLAUSE_HAVING, CLAUSE_JOIN_ON)
OCC_DERIVED_COLUMN, OCC_GROUP_BY_EXPRESSION, OCC_PREDICATE = "DERIVED_COLUMN", "GROUP_BY_EXPRESSION", "PREDICATE"
OCCURRENCE_KINDS = (OCC_DERIVED_COLUMN, OCC_GROUP_BY_EXPRESSION, OCC_PREDICATE)

# Clases de transformacion (taxonomia CERRADA por FAMILIA DE FUNCION estructural; nunca por nombre de campo ni de artefacto).
TC_TEMPORAL, TC_TEXT_FORMAT, TC_TYPE_CONVERSION, TC_NUMERIC_FUNCTION = "TEMPORAL", "TEXT_FORMAT", "TYPE_CONVERSION", "NUMERIC_FUNCTION"
TC_NULL_HANDLING, TC_UNIT_CURRENCY, TC_AGGREGATION, TC_CONDITIONAL = "NULL_HANDLING", "UNIT_CURRENCY_CONVERSION", "AGGREGATION", "CONDITIONAL"
TC_ARITHMETIC, TC_LITERAL, TC_PASSTHROUGH, TC_OTHER_FUNCTION = "ARITHMETIC", "LITERAL", "PASSTHROUGH", "OTHER_FUNCTION"
TRANSFORMATION_CLASSES = (TC_TEMPORAL, TC_TEXT_FORMAT, TC_TYPE_CONVERSION, TC_NUMERIC_FUNCTION, TC_NULL_HANDLING, TC_UNIT_CURRENCY, TC_AGGREGATION, TC_CONDITIONAL,
                          TC_ARITHMETIC, TC_LITERAL, TC_PASSTHROUGH, TC_OTHER_FUNCTION)
_FUNCTION_CLASS: dict[str, str] = {}
for _cls, _names in (
    (TC_TEMPORAL, ("EXTRACT", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND", "WEEK", "ISOWEEK", "QUARTER", "WEEKDAY", "DAYNAME", "MONTHNAME", "LAST_DAY",
                   "ADD_DAYS", "ADD_MONTHS", "ADD_YEARS", "ADD_SECONDS", "DAYS_BETWEEN", "MONTHS_BETWEEN", "SECONDS_BETWEEN", "YEARS_BETWEEN", "TO_DATE", "TO_DATS",
                   "TO_TIME", "TO_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP", "CURRENT_UTCDATE", "CURRENT_UTCTIMESTAMP", "NOW", "UTCTOLOCAL",
                   "LOCALTOUTC")),
    (TC_TEXT_FORMAT, ("CONCAT", "LPAD", "RPAD", "SUBSTRING", "SUBSTR", "LEFT", "RIGHT", "TRIM", "LTRIM", "RTRIM", "UPPER", "LOWER", "REPLACE", "LENGTH", "INITCAP",
                      "REVERSE", "CHAR", "LOCATE", "INSTR", "SUBSTR_AFTER", "SUBSTR_BEFORE", "REPLACE_REGEXPR")),
    (TC_TYPE_CONVERSION, ("TO_INT", "TO_INTEGER", "TO_BIGINT", "TO_SMALLINT", "TO_DECIMAL", "TO_DOUBLE", "TO_REAL", "TO_VARCHAR", "TO_NVARCHAR", "TO_CHAR", "TO_NUMBER",
                          "TO_BOOLEAN", "CAST")),
    (TC_NUMERIC_FUNCTION, ("ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "MOD", "POWER", "SQRT", "SIGN", "TRUNC", "EXP", "LN", "LOG")),
    (TC_NULL_HANDLING, ("COALESCE", "IFNULL", "NULLIF", "NVL", "MAP")),
    (TC_UNIT_CURRENCY, ("CONVERT_CURRENCY", "CONVERT_UNIT")),
):
    for _n in _names:
        _FUNCTION_CLASS[_n] = _cls
_OPERATION_CLASS = {OP_AGGREGATION: TC_AGGREGATION, OP_CONDITIONAL: TC_CONDITIONAL, OP_ARITHMETIC: TC_ARITHMETIC, OP_CONCATENATION: TC_TEXT_FORMAT,
                    OP_CAST: TC_TYPE_CONVERSION, OP_LITERAL: TC_LITERAL, OP_PASSTHROUGH: TC_PASSTHROUGH}


def transformation_classes(operations: Iterable[str], functions: Iterable[str]) -> tuple[str, ...]:
    """Clases de transformacion de una expresion a partir de sus operaciones y NOMBRES DE FUNCION (estructura del AST). Una funcion fuera de la tabla cerrada es
    ``OTHER_FUNCTION`` (nunca se adivina). Determinista y ordenada."""
    out: set[str] = set()
    for f in functions:
        out.add(_FUNCTION_CLASS.get(str(f).upper(), TC_OTHER_FUNCTION))
    for o in operations:
        if o == OP_FUNCTION:
            continue                                  # las funciones se clasifican por NOMBRE
        if o in _OPERATION_CLASS:
            out.add(_OPERATION_CLASS[o])
    return tuple(c for c in TRANSFORMATION_CLASSES if c in out)


@dataclass(frozen=True)
class SelectNode:
    index: int                      # orden de descubrimiento (preorden); 0 = SELECT externo (o primera rama)
    depth: int                      # 0 = externo; >0 = subselect anidado (vistas graficas)
    select: dict


def iter_select_nodes(definition: dict) -> tuple[SelectNode, ...]:
    """TODOS los SELECT del query compilado (rama de SET, subselect anidado en ``from``), en preorden. Sin ``query``: ()."""
    out: list[SelectNode] = []

    def visit(node: Any, depth: int) -> None:
        if not isinstance(node, dict):
            return
        if isinstance(node.get("SELECT"), dict):
            sel = node["SELECT"]
            out.append(SelectNode(len(out), depth, sel))
            visit(sel.get("from"), depth + 1)
        elif isinstance(node.get("SET"), dict):
            for a in node["SET"].get("args") or []:
                visit(a, depth)
        elif isinstance(node.get("args"), list):          # join: sus argumentos
            for a in node["args"]:
                visit(a, depth)

    visit(definition.get("query") if isinstance(definition, dict) else None, 0)
    return tuple(out)


def _join_on_clauses(frm: Any) -> list[list]:
    """Los ``on`` de los joins del ``from`` PROPIO de un SELECT (no entra a sus subselects: son nodos aparte)."""
    out: list[list] = []

    def visit(n: Any) -> None:
        if not isinstance(n, dict) or "SELECT" in n or "SET" in n:
            return
        if isinstance(n.get("on"), list):
            out.append(n["on"])
        for a in n.get("args") or []:
            visit(a)

    visit(frm)
    return out


def _split_atoms(tokens: Any) -> list[Any]:
    """Atomos de un predicado: se parte por ``and``/``or`` de nivel superior; cada atomo es una sublista de tokens (o un objeto)."""
    if not isinstance(tokens, list):
        return [tokens] if tokens else []
    atoms, cur = [], []
    for t in tokens:
        if isinstance(t, str) and t.lower() in ("and", "or"):
            if cur:
                atoms.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        atoms.append(cur)
    return atoms


def _is_trivial(node: Any) -> bool:
    """Un atomo sin funcion, ``xpr`` ni subselect ni cast (solo referencias, literales y comparadores)."""
    found: list[int] = []
    _walk(node, lambda n: found.append(1) if ("func" in n or "xpr" in n or "SELECT" in n or "cast" in n) else None)
    return not found


@dataclass(frozen=True)
class ExpressionOccurrence:
    artifact: str
    select_node: int
    clause: str
    kind: str
    output_name: str | None
    operations: tuple[str, ...]
    functions: tuple[str, ...]
    transformation_classes: tuple[str, ...]
    inputs: tuple[InputRef, ...]
    exact_fp: str
    shape_fp: str
    ambiguity: tuple[str, ...]

    @property
    def resolvable(self) -> bool:
        return not self.ambiguity

    def to_json(self) -> dict:
        return {"artifact": self.artifact, "select_node": self.select_node, "clause": self.clause, "kind": self.kind, "output_name": self.output_name,
                "operations": list(self.operations), "functions": list(self.functions), "transformation_classes": list(self.transformation_classes),
                "inputs": [i.to_json() for i in self.inputs], "exact_fp": self.exact_fp, "shape_fp": self.shape_fp, "resolvable": self.resolvable,
                "ambiguity": list(self.ambiguity)}


@dataclass(frozen=True)
class ExpressionSurface:
    """Evidencia de expresiones de una definicion: ocurrencias NO triviales por clausula, contadores de predicados triviales y el indice de SUBEXPRESIONES
    de predicados/GROUP BY (para vincular una expresion derivada en otra clausula, p. ej. una columna calculada reaparece dentro de un WHERE)."""
    occurrences: tuple[ExpressionOccurrence, ...]
    trivial_predicates: tuple[tuple[str, int], ...]                    # (clausula, cantidad)
    subexpressions: tuple[tuple[str, str, int], ...]                   # (exact_fp, clausula, select_node) de subarboles func/case dentro de predicados/GROUP BY


def _occurrence(artifact: str, node_index: int, clause: str, kind: str, name: str | None, tree: Any, aliases: dict) -> ExpressionOccurrence:
    ins, amb = _inputs(tree, aliases)
    ambiguity = set(amb)
    for i in ins:
        if i.state in _UNRESOLVED_STATES:
            ambiguity.add(i.state)
    holder = tree if isinstance(tree, dict) else {"xpr": tree}
    ops, funcs = _classify_ops(holder)
    return ExpressionOccurrence(artifact, node_index, clause, kind, name, ops, funcs, transformation_classes(ops, funcs), ins,
                                _fp(holder, True), _fp(holder, False), tuple(sorted(ambiguity)))


def extract_expression_surface(artifact: str, definition: dict) -> ExpressionSurface | None:
    """Expresiones de TODAS las clausulas estructurales de ``definition`` (SELECT derivadas, GROUP BY, WHERE, HAVING, JOIN ON). Sin ``query`` => None.
    Reutiliza el canonicalizador de huellas de G3: no hay parseo de texto ni regex."""
    nodes = iter_select_nodes(definition)
    if not nodes:
        return None
    occs: list[ExpressionOccurrence] = []
    trivial: dict[str, int] = {}
    subs: list[tuple[str, str, int]] = []

    def scan_subexpressions(tree: Any, clause: str, node_index: int) -> None:
        def f(n: dict) -> None:
            if "func" in n or (isinstance(n.get("xpr"), list) and any(isinstance(t, str) and t.lower() == "case" for t in n["xpr"])):
                subs.append((_fp(n, True), clause, node_index))
        _walk(tree, f)

    for sn in nodes:
        sel, aliases = sn.select, _source_aliases(sn.select.get("from"))
        for col in sel.get("columns") or []:
            if isinstance(col, dict) and _is_derived(col):
                name = _output_name(col)
                if name:
                    occs.append(_occurrence(artifact, sn.index, CLAUSE_SELECT, OCC_DERIVED_COLUMN, name, {k: v for k, v in col.items() if k != "as"}, aliases))
        for item in sel.get("groupBy") or []:
            if isinstance(item, dict) and not _is_trivial(item):
                occs.append(_occurrence(artifact, sn.index, CLAUSE_GROUP_BY, OCC_GROUP_BY_EXPRESSION, None, item, aliases))
            scan_subexpressions(item, CLAUSE_GROUP_BY, sn.index)
        preds = [(CLAUSE_WHERE, sel.get("where")), (CLAUSE_HAVING, sel.get("having"))] + [(CLAUSE_JOIN_ON, on) for on in _join_on_clauses(sel.get("from"))]
        for clause, tokens in preds:
            if not tokens:
                continue
            for atom in _split_atoms(tokens):
                if _is_trivial(atom):
                    trivial[clause] = trivial.get(clause, 0) + 1
                else:
                    occs.append(_occurrence(artifact, sn.index, clause, OCC_PREDICATE, None, atom, aliases))
            scan_subexpressions(tokens, clause, sn.index)
    occs.sort(key=lambda o: (o.select_node, o.clause, o.kind, o.exact_fp, o.output_name or ""))
    return ExpressionSurface(tuple(occs), tuple(sorted(trivial.items())), tuple(sorted(set(subs))))


# ───────────────────────────── proyecciones (S3): como se arma cada columna de salida ─────────────────────────────
MAP_IDENTITY, MAP_RENAME, MAP_DERIVED, MAP_CAST, MAP_UNKNOWN = "IDENTITY", "RENAME", "DERIVED", "CAST", "UNKNOWN"
MAPPING_KINDS = (MAP_IDENTITY, MAP_RENAME, MAP_DERIVED, MAP_CAST, MAP_UNKNOWN)


@dataclass(frozen=True)
class ProjectionColumn:
    """Columna de salida del SELECT externo: nombre, tipo de mapeo (IDENTITY mismo nombre, RENAME otro nombre, DERIVED expresion, CAST conversion) y la
    referencia fuente ``(proveedor, campo)`` cuando la columna es una referencia simple resoluble."""
    artifact: str
    branch: int
    output: str
    kind: str
    source_provider: str | None
    source_field: str | None

    def to_json(self) -> dict:
        return {"output": self.output, "kind": self.kind, "source_provider": self.source_provider, "source_field": self.source_field, "branch": self.branch}


def extract_projection_columns(artifact: str, definition: dict) -> tuple[ProjectionColumn, ...]:
    """Columnas del SELECT EXTERNO (cada rama de SET) con su tipo de mapeo. Una referencia simple es IDENTITY/RENAME segun el nombre de salida; una expresion o
    una referencia con ``cast`` se separa en DERIVED/CAST. El proveedor se resuelve solo si el alias lo permite (subselect/ambiguo => None)."""
    out: list[ProjectionColumn] = []
    for bi, sel in enumerate(select_branches(definition)):
        aliases = _source_aliases(sel.get("from"))
        distinct = {v for v in aliases.values()}
        for col in sel.get("columns") or []:
            if not isinstance(col, dict):
                continue
            name = _output_name(col)
            if not name:
                continue
            if isinstance(col.get("ref"), list) and col["ref"] and not (set(col) - {"ref", "as", "key", "cast"}):
                path = [_segment(x) for x in col["ref"]]
                field = path[-1]
                provider = None
                if len(path) >= 2:
                    src = aliases.get(path[0])
                    provider = src[1] if src is not None and src[0] == "ENTITY" else None
                elif len(distinct) == 1:
                    src = next(iter(distinct))
                    provider = src[1] if src[0] == "ENTITY" else None
                kind = MAP_CAST if "cast" in col else (MAP_IDENTITY if name == field else MAP_RENAME)
                out.append(ProjectionColumn(artifact, bi, name, kind, provider, field))
            else:
                out.append(ProjectionColumn(artifact, bi, name, MAP_DERIVED, None, None))
    return tuple(out)


# ───────────────────────────── campos consumidos por proveedor (S5) ─────────────────────────────
def extract_consumed_fields(definition: dict) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``((proveedor, (campos...)), ...)``: los campos de cada proveedor-ENTIDAD que el query de ``definition`` referencia (columnas, WHERE, GROUP BY, HAVING,
    JOIN ON), por alias resuelto a entidad en cada nodo SELECT. Las referencias a un subselect interno NO se cuentan (el nodo anidado cuenta las suyas) y las no
    calificadas con varios origenes se omiten (no se adivina). Sin ``query`` => ()."""
    used: dict[str, set[str]] = {}
    for sn in iter_select_nodes(definition):
        sel = sn.select
        aliases = _source_aliases(sel.get("from"))
        distinct = {v for v in aliases.values()}
        trees = [sel.get("columns"), sel.get("where"), sel.get("having"), sel.get("groupBy"), sel.get("orderBy"), *_join_on_clauses(sel.get("from"))]

        def f(n: dict) -> None:
            ref = n.get("ref")
            if not isinstance(ref, list) or not ref:
                return
            path = [_segment(x) for x in ref]
            if path[0].startswith("$"):
                return
            if len(path) >= 2:
                src = aliases.get(path[0])
                if src is not None and src[0] == "ENTITY":
                    used.setdefault(src[1], set()).add(path[-1])
            elif len(distinct) == 1:
                src = next(iter(distinct))
                if src[0] == "ENTITY":
                    used.setdefault(src[1], set()).add(path[-1])
        for t in trees:
            if t:
                _walk(t, f)
    return tuple((p, tuple(sorted(fs))) for p, fs in sorted(used.items()))
