"""`join-key-profile/v1` — evidencia ESTRUCTURAL de composición (sidecar `evidence_extensions`, fuera del perfil), extraída del CSN del pool.

Transporta HECHOS, nunca juicios ni inferencias:
  · claves DECLARADAS (`elements.<col>.key`) de cada artefacto del cierre, con su procedencia (`CSN_ELEMENTS_KEY`);
  · por vista: ramas (SET/UNION), fuente conductora de cada rama, joins (tipo, artefacto secundario, igualdades del `on` clasificadas en
    COLUMN / CONSTANT / TRANSFORMED / OTHER), GROUP BY (columnas de salida agrupadas), igualdades contra constante del WHERE y columnas
    proyectadas por referencia simple (`<alias>.<col> as <salida>`);
  · columnas literales por rama (posible discriminador de UNION).
La cardinalidad NO se infiere aquí (ni de nombres técnicos): las reglas del Juicio la ACREDITAN o se abstienen a partir de estos hechos.
Un dato ausente se transporta como ausente (`None`/lista vacía) — nunca como cumplimiento ni deficiencia.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

JOIN_KEY_PROFILE_VERSION = "join-key-profile/v1"
KEY_SOURCE = "CSN_ELEMENTS_KEY"
EQ_COLUMN, EQ_CONSTANT, EQ_TRANSFORMED, EQ_OTHER = "COLUMN", "CONSTANT", "TRANSFORMED", "OTHER"


def _ref_name(r: Any) -> str | None:
    if isinstance(r, list) and r:
        r = r[0]
    if isinstance(r, str):
        return r
    if isinstance(r, Mapping):
        return str(r.get("id") or "") or None
    return None


def _declared_keys(defn: Mapping[str, Any]) -> list[str]:
    return [k for k, v in (defn.get("elements") or {}).items() if isinstance(v, Mapping) and v.get("key") is True]


def _atoms(tokens: Any) -> list[list]:
    """Átomos de un predicado partidos por `and` de nivel superior. Un `or` de nivel superior invalida la conjunción: se devuelve un átomo OTHER."""
    if not isinstance(tokens, list):
        return []
    if any(isinstance(t, str) and t.lower() == "or" for t in tokens):
        return [["__OR__"]]
    out, cur = [], []
    for t in tokens:
        if isinstance(t, str) and t.lower() == "and":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _operand(x: Any) -> dict:
    if isinstance(x, Mapping) and isinstance(x.get("ref"), list) and len(x["ref"]) == 2 and all(isinstance(p, str) for p in x["ref"]):
        return {"kind": "REF", "alias": x["ref"][0], "column": x["ref"][1]}
    if isinstance(x, Mapping) and "val" in x:
        return {"kind": "VAL", "value": x["val"]}
    if isinstance(x, Mapping) and "func" in x:
        refs = [a for a in (x.get("args") or []) if isinstance(a, Mapping) and isinstance(a.get("ref"), list) and len(a["ref"]) == 2]
        return {"kind": "FUNC", "func": str(x.get("func")), "alias": refs[0]["ref"][0] if len(refs) == 1 else None, "column": refs[0]["ref"][1] if len(refs) == 1 else None}
    return {"kind": "OTHER"}


def _equalities(on: Any, secondary_alias: str) -> list[dict]:
    """Igualdades del `on` vistas desde el lado SECUNDARIO: COLUMN (col = col sin función), CONSTANT (col = literal), TRANSFORMED (función sobre la clave), OTHER."""
    out = []
    for atom in _atoms(on):
        if len(atom) != 3 or atom[1] != "=":
            out.append({"kind": EQ_OTHER})
            continue
        a, b = _operand(atom[0]), _operand(atom[2])
        sec, oth = (a, b) if a.get("alias") == secondary_alias else ((b, a) if b.get("alias") == secondary_alias else (None, None))
        if sec is None:
            out.append({"kind": EQ_OTHER})
        elif sec["kind"] == "REF" and oth["kind"] == "REF":
            out.append({"kind": EQ_COLUMN, "secondary_column": sec["column"], "other": {"alias": oth["alias"], "column": oth["column"]}})
        elif sec["kind"] == "REF" and oth["kind"] == "VAL":
            out.append({"kind": EQ_CONSTANT, "secondary_column": sec["column"], "value": oth["value"]})
        elif "FUNC" in (sec["kind"], oth["kind"]):
            out.append({"kind": EQ_TRANSFORMED, "secondary_column": sec.get("column"), "functions": sorted({o["func"] for o in (sec, oth) if o["kind"] == "FUNC"})})
        else:
            out.append({"kind": EQ_OTHER})
    return out


def _from_tree(frm: Any, aliases: dict) -> tuple[dict | None, list[dict]]:
    """(conductora, joins) de un `from` encadenado a la izquierda. Un subselect en el from => conductora desconocida (None)."""
    if not isinstance(frm, Mapping):
        return None, []
    if "join" in frm:
        args = frm.get("args") or []
        if len(args) != 2:
            return None, []
        driver, joins = _from_tree(args[0], aliases)
        sec = args[1]
        name = _ref_name(sec.get("ref")) if isinstance(sec, Mapping) else None
        alias = (sec.get("as") if isinstance(sec, Mapping) else None) or name
        if name:
            aliases[alias] = name
        joins.append({"type": str(frm.get("join") or ""), "secondary": name, "alias": alias, "equalities": _equalities(frm.get("on"), alias) if alias else []})
        return driver, joins
    name = _ref_name(frm.get("ref"))
    if not name:
        return None, []
    alias = frm.get("as") or name
    aliases[alias] = name
    return {"artifact": name, "alias": alias}, []


def _branch(sel: Mapping[str, Any]) -> dict:
    aliases: dict[str, str] = {}
    driver, joins = _from_tree(sel.get("from"), aliases)
    cols, literals = [], []
    for c in sel.get("columns") or []:
        if isinstance(c, Mapping) and isinstance(c.get("ref"), list) and len(c["ref"]) == 2 and all(isinstance(p, str) for p in c["ref"]):
            cols.append({"output": c.get("as") or c["ref"][1], "artifact": aliases.get(c["ref"][0]), "column": c["ref"][1]})
        elif isinstance(c, Mapping) and "val" in c and c.get("as"):
            literals.append({"output": c["as"], "value": c["val"]})
    group_by = None
    if sel.get("groupBy"):
        group_by = [g["ref"][-1] for g in sel["groupBy"] if isinstance(g, Mapping) and isinstance(g.get("ref"), list) and g["ref"]]
    where_constants = []
    for atom in _atoms(sel.get("where")):
        if len(atom) == 3 and atom[1] == "=":
            a, b = _operand(atom[0]), _operand(atom[2])
            ref, val = (a, b) if a["kind"] == "REF" else (b, a)
            if ref["kind"] == "REF" and val["kind"] == "VAL":
                where_constants.append({"artifact": aliases.get(ref["alias"]), "column": ref["column"]})
    for j in joins:
        j["secondary"] = j["secondary"] or aliases.get(j["alias"])
    return {"driver": driver, "joins": joins, "projected_refs": cols, "literal_columns": literals, "group_by": group_by, "where_constants": where_constants,
            "has_subselect_source": driver is None}


def view_profile(name: str, defn: Mapping[str, Any]) -> dict | None:
    q = defn.get("query") if isinstance(defn, Mapping) else None
    if not isinstance(q, Mapping):
        return None
    if "SET" in q:
        args = (q.get("SET") or {}).get("args") or []
        branches = [_branch(a.get("SELECT") or {}) for a in args if isinstance(a, Mapping) and "SELECT" in a]
        union = True
    elif "SELECT" in q:
        branches, union = [_branch(q["SELECT"])], False
    else:
        return None
    return {"artifact": name, "declared_keys": _declared_keys(defn), "key_source": KEY_SOURCE, "union": union, "branches": branches}


def build(definitions: Mapping[str, Mapping[str, Any]], closure: Iterable[str]) -> dict:
    """Bloque `join-key-profile/v1` para los artefactos del CIERRE (no del pool entero): claves declaradas de todos y perfil de las vistas."""
    names = sorted({n for n in closure if n in definitions})
    return {"version": JOIN_KEY_PROFILE_VERSION,
            "keys": {n: {"declared": _declared_keys(definitions[n]), "source": KEY_SOURCE} for n in names},
            "views": {n: vp for n in names if (vp := view_profile(n, definitions[n])) is not None}}


def definitions_from_files(files: Iterable[Path]) -> dict[str, dict]:
    """Definiciones CSN del pool (solo lectura). Un archivo ilegible se omite: su artefacto queda SIN evidencia (no evaluable), nunca inventado."""
    out: dict[str, dict] = {}
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in ((d.get("definitions") or {}) if isinstance(d, Mapping) else {}).items():
            if isinstance(v, Mapping):
                out.setdefault(k, dict(v))
    return out
