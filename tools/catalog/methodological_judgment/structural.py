"""judgment-rules/v7 — reglas sobre la evidencia ESTRUCTURAL (`join-key-profile/v1`, entrada explícita del Juicio; nunca leída de fondo).

E3 — ENRIQUECIMIENTO LEGÍTIMO: un nodo de producto que lee UNA fuente conductora gobernada y la enriquece con dimensiones gobernadas por joins
     cuya cardinalidad N:1 está DEMOSTRADA (las igualdades del `on` —columna sin función o constante— cubren la clave DECLARADA completa del lado
     secundario), sin UNION ni GROUP BY (granularidad preservada) y aportando solo ATRIBUTOS por identidad (ninguna medida).
     Cualquier dato faltante, clave transformada (p. ej. LTRIM), predicado no resoluble o clave secundaria desconocida ⇒ NOT_EVALUABLE.
E5 — SUFICIENCIA DE LA CLAVE PUBLICADA (conservadora): con clave DECLARADA en el nodo de producto, la compara con la granularidad EFECTIVA:
     con GROUP BY, el conjunto agrupado (agregación legítima: la clave debe cubrirlo, no igualar la de origen); sin agregación, la clave declarada
     de la conductora (CDS gobernada) proyectada por referencia (los renombres se respetan) menos las columnas fijadas por igualdad a constante.
     DEFICIENT solo con evidencia POSITIVA (un componente de la granularidad no está en la clave publicada); sin clave declarada ⇒ NOT_APPLICABLE;
     UNION, join no demostrado, conductora no CDS gobernada o sin clave ⇒ NOT_EVALUABLE.
Puro: sin E/S; nunca infiere cardinalidad desde nombres técnicos.
"""
from __future__ import annotations

from typing import Any, Mapping

from tools.catalog.methodological_judgment.profile_access import GOVERNED_SAP_CONTRACT, ProfileEvidence

JKP_VERSION = "join-key-profile/v1"
DEMONSTRATED, SUFFICIENT, DEFICIENT, NOT_EVALUABLE, NOT_APPLICABLE = "DEMONSTRATED", "SUFFICIENT", "DEFICIENT", "NOT_EVALUABLE", "NOT_APPLICABLE"
_GOVERNED_CDS = "GOVERNED_SAP_CDS_CONTRACT"
_IDENTITY = frozenset({"IDENTITY_ONLY", "IDENTITY_OR_RENAME"})


def valid_block(block: Any) -> Mapping[str, Any] | None:
    return block if isinstance(block, Mapping) and block.get("version") == JKP_VERSION and isinstance(block.get("views"), Mapping) else None


def _keys(jkp: Mapping[str, Any], name: str) -> list[str]:
    return list(((jkp.get("keys") or {}).get(name) or {}).get("declared") or [])


def join_n1(join: Mapping[str, Any], jkp: Mapping[str, Any]) -> tuple[bool, str]:
    """(demostrado, motivo). Demostrado SOLO si las igualdades COLUMN/CONSTANT cubren la clave declarada completa del secundario y hay >=1 igualdad de columna."""
    keys = _keys(jkp, str(join.get("secondary") or ""))
    eqs = list(join.get("equalities") or ())
    if not keys:
        return False, "SECONDARY_DECLARED_KEY_UNKNOWN"
    if not eqs or any(e.get("kind") not in ("COLUMN", "CONSTANT") for e in eqs):
        return False, "TRANSFORMED_OR_UNRESOLVED_JOIN_PREDICATE"
    if not any(e.get("kind") == "COLUMN" for e in eqs):
        return False, "NO_COLUMN_EQUALITY"
    covered = {e.get("secondary_column") for e in eqs}
    return (True, "SECONDARY_KEY_COVERED") if set(keys) <= covered else (False, "SECONDARY_KEY_NOT_COVERED")


def e3_enrichment(ev: ProfileEvidence, jkp: Mapping[str, Any] | None, policy: str) -> dict:
    """{state, nodes: [{node, state, reason, joins}]}. Global DEMONSTRATED solo si TODO nodo de producto lo está."""
    if jkp is None:
        return {"state": NOT_EVALUABLE, "reason": "NO_STRUCTURAL_EVIDENCE", "nodes": []}
    nodes = []
    for n in ev.product_nodes:
        vp = (jkp.get("views") or {}).get(n)
        st, why, js = NOT_EVALUABLE, "", []
        if vp is None:
            why = "NO_VIEW_PROFILE"
        elif vp.get("union") or len(vp.get("branches") or ()) != 1:
            why = "UNION_OR_MULTIBRANCH"
        else:
            br = vp["branches"][0]
            drv = (br.get("driver") or {}).get("artifact")
            if not br.get("joins"):
                st, why = NOT_APPLICABLE, "NO_JOINS"
            elif br.get("group_by"):
                why = "GRAIN_ALTERED_BY_AGGREGATION"
            elif not drv or ev.classify_source(drv, policy) != GOVERNED_SAP_CONTRACT:
                why = "DRIVER_NOT_GOVERNED_BOUNDARY"
            else:
                ok = True
                for j in br["joins"]:
                    sec = j.get("secondary")
                    n1, r = join_n1(j, jkp)
                    contrib = [(name, fl) for name, rec in ev.lineage_fields.items() for fl in rec.get("frontier_leaves") or () if fl.get("leaf") == sec]
                    attrs_only = bool(contrib) and all(ev.lineage_fields[name].get("role") == "ATTRIBUTE"
                                                       and all(pp.get("character") in _IDENTITY for pp in fl.get("path_profiles") or ()) for name, fl in contrib)
                    js.append({"secondary": sec, "type": j.get("type"), "n1": n1, "reason": r, "attributes_only": attrs_only})
                    if j.get("type") != "left" or not n1 or not sec or ev.classify_source(sec, policy) != GOVERNED_SAP_CONTRACT or not attrs_only:
                        ok = False
                st, why = (DEMONSTRATED, "N1_ENRICHMENT_BY_GOVERNED_DIMENSIONS") if ok else (NOT_EVALUABLE, "JOIN_NOT_DEMONSTRATED_AS_ENRICHMENT")
        nodes.append({"node": n, "state": st, "reason": why, "joins": js})
    glob = DEMONSTRATED if nodes and all(x["state"] == DEMONSTRATED for x in nodes) else NOT_EVALUABLE
    return {"state": glob, "nodes": nodes}


def e5_key_sufficiency(ev: ProfileEvidence, jkp: Mapping[str, Any] | None, policy: str) -> dict:
    """{state, nodes: [{node, state, reason, declared_key, grain, missing}]}. Global: DEFICIENT si algun nodo lo es; si no, SUFFICIENT/NOT_APPLICABLE/NOT_EVALUABLE."""
    if jkp is None:
        return {"state": NOT_EVALUABLE, "reason": "NO_STRUCTURAL_EVIDENCE", "nodes": []}
    nodes = []
    for n in ev.product_nodes:
        vp = (jkp.get("views") or {}).get(n)
        rec = {"node": n, "state": NOT_EVALUABLE, "reason": "", "declared_key": [], "grain": [], "missing": []}
        if vp is None:
            rec["reason"] = "NO_VIEW_PROFILE"
        elif not vp.get("declared_keys"):
            rec.update(state=NOT_APPLICABLE, reason="NO_DECLARED_KEY")
        elif vp.get("union") or len(vp.get("branches") or ()) != 1:
            rec["reason"] = "UNION_OR_MULTIBRANCH"
        else:
            br, key = vp["branches"][0], set(vp["declared_keys"])
            rec["declared_key"] = sorted(key)
            bad = [j for j in br.get("joins") or () if not join_n1(j, jkp)[0]]
            proj = br.get("projected_refs") or ()
            if bad:
                rec["reason"] = "JOIN_CARDINALITY_NOT_DEMONSTRATED"
            elif br.get("group_by"):
                outs = []
                for g in br["group_by"]:
                    m = sorted({p["output"] for p in proj if p.get("column") == g})
                    outs.append(m[0] if len(m) == 1 else None)
                if None in outs:
                    rec["reason"] = "GROUP_BY_OUTPUT_NOT_RESOLVABLE"
                else:
                    missing = sorted(set(outs) - key)
                    rec.update(grain=sorted(outs), missing=missing, state=DEFICIENT if missing else SUFFICIENT,
                               reason="AGGREGATED_GRAIN_NOT_COVERED" if missing else "AGGREGATED_GRAIN_COVERED")
            else:
                drv = (br.get("driver") or {}).get("artifact")
                rec_cls = (ev.leaf_class_records.get(drv) or {}).get("contract_class") if drv else None
                dkeys = _keys(jkp, drv) if drv else []
                if rec_cls != _GOVERNED_CDS or not dkeys:
                    rec["reason"] = "DRIVER_NOT_GOVERNED_CDS_WITH_DECLARED_KEY"
                else:
                    fixed = {w["column"] for w in br.get("where_constants") or () if w.get("artifact") == drv}
                    outmap = {}
                    for p in proj:
                        if p.get("artifact") == drv:
                            outmap.setdefault(p["column"], set()).add(p["output"])
                    grain = [d for d in dkeys if d not in fixed]
                    missing = sorted(d for d in grain if not (outmap.get(d, set()) & key))
                    rec.update(grain=grain, missing=missing, state=DEFICIENT if missing else SUFFICIENT,
                               reason="DECLARED_KEY_DOES_NOT_COVER_DRIVER_GRAIN" if missing else "DECLARED_KEY_COVERS_DRIVER_GRAIN")
        nodes.append(rec)
    states = {x["state"] for x in nodes}
    glob = DEFICIENT if DEFICIENT in states else (SUFFICIENT if states == {SUFFICIENT} else (NOT_APPLICABLE if states == {NOT_APPLICABLE} else NOT_EVALUABLE))
    return {"state": glob, "nodes": nodes}
