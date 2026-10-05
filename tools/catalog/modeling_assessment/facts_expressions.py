"""Hechos de EXPRESIONES por clausula y ubicacion de transformaciones (S4, evidence release). Solo HECHOS descriptivos: NO se decide de que capa es una transformacion.

Responde "¿que transformacion existe, DONDE ocurre, en que capa, y reaparece estructuralmente en otra parte?" para que una revision/juicio FUTURO combine
clase de transformacion, reuso observado, capa, especificidad de producto y contexto humano cuando haga falta. No existe ninguna regla "campo X => capa Y".

Hecho (subject = ``closure``): ``propagation.expression_occurrences``
  occurrences   expresiones NO triviales por clausula (SELECT derivadas, GROUP BY, WHERE, HAVING, JOIN ON) con huella exacta y de forma, operaciones, funciones,
                clases de transformacion (por familia de funcion), entradas y resolubilidad; ademas la CAPA del artefacto (por rol declarado o nombre §28);
  trivial_predicates  cantidad de predicados triviales (solo referencias/literales y comparadores) por clausula;
  reuse_groups  la MISMA huella exacta presente en >= 2 ocurrencias (artefactos o clausulas distintas): ``WHOLE`` = la expresion completa en una clausula,
                ``CONTAINED`` = la expresion de una columna derivada aparece como SUBARBOL dentro de otra clausula (p. ej. un WHERE). Las huellas son las de G3
                (alias-insensibles), generalizadas a cualquier subarbol: no hay parseo de texto SQL ni regex.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from . import contract as C
from .derivation import (
    EXPRESSION_EXTRACTOR_VERSION, OCC_DERIVED_COLUMN, OP_CAST, OP_LITERAL, OP_PASSTHROUGH, TRANSFORMATION_CLASSES,
)
from .facts_support import make_fact, sort_facts
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, EvidenceRef, Fact, Plane
from .scope import AssessmentUniverse

PRO_ = Area.PROPAGATION
CONTAIN_WHOLE, CONTAIN_CONTAINED = "WHOLE", "CONTAINED"
CONTAINMENTS = (CONTAIN_WHOLE, CONTAIN_CONTAINED)
_TRIVIAL_OPS = frozenset({OP_PASSTHROUGH, OP_CAST, OP_LITERAL})


def expression_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    closure = universe.closure_of(am)
    ident = identity or {n: classify(entities[n]) for n in closure}
    ref = [EvidenceRef(am, "closure", None, Plane.MODELING)]
    occs: list[dict] = []
    trivial: dict[str, int] = defaultdict(int)
    subs: list[tuple[str, str, str, int]] = []                      # (fp, artifact, clause, select_node)
    without_query: list[str] = []
    layer_of: dict[str, dict] = {}
    for n in closure:
        if n in ident and ident[n].identity.value == "SYSTEM":
            continue
        d = evidence.derivation_evidence(n) if evidence is not None else None
        if d is None or d.expressions is None:
            if d is None and not getattr(entities[n], "remote_entity", ""):
                without_query.append(n)
            continue
        lay = C.layer_inference(entities[n])
        layer_of[n] = lay
        for o in d.expressions.occurrences:
            occs.append({**o.to_json(), "layer": lay["inferred_layer"], "layer_basis": lay["inferred_layer_basis"]})
        for clause, k in d.expressions.trivial_predicates:
            trivial[clause] += k
        subs += [(fp, n, clause, node) for fp, clause, node in d.expressions.subexpressions]

    targets = {o["exact_fp"] for o in occs if o["kind"] == OCC_DERIVED_COLUMN and not set(o["operations"]) <= _TRIVIAL_OPS}
    groups: dict[str, list[dict]] = defaultdict(list)
    meta: dict[str, dict] = {}
    seen_whole: set[tuple[str, str, int, str]] = set()
    for o in occs:
        if o["exact_fp"] in targets:
            groups[o["exact_fp"]].append({"artifact": o["artifact"], "clause": o["clause"], "select_node": o["select_node"], "containment": CONTAIN_WHOLE,
                                          "kind": o["kind"], "layer": o["layer"]})
            meta.setdefault(o["exact_fp"], {"shape_fp": o["shape_fp"], "transformation_classes": o["transformation_classes"], "functions": o["functions"]})
            seen_whole.add((o["artifact"], o["clause"], o["select_node"], o["exact_fp"]))
    for fp, art, clause, node in subs:
        if fp in targets and (art, clause, node, fp) not in seen_whole:
            groups[fp].append({"artifact": art, "clause": clause, "select_node": node, "containment": CONTAIN_CONTAINED, "kind": "SUBEXPRESSION",
                               "layer": layer_of.get(art, {}).get("inferred_layer")})
    reuse = []
    for fp, members in sorted(groups.items()):
        uniq = {(m["artifact"], m["clause"], m["select_node"], m["containment"]): m for m in members}
        ms = sorted(uniq.values(), key=lambda m: (m["artifact"], m["clause"], m["select_node"], m["containment"]))
        if len(ms) < 2:
            continue
        info = meta[fp]
        reuse.append({"exact_fp": fp, "shape_fp": info["shape_fp"], "transformation_classes": info["transformation_classes"], "functions": info["functions"],
                      "occurrences": ms, "occurrence_count": len(ms), "artifacts": sorted({m["artifact"] for m in ms}), "clauses": sorted({m["clause"] for m in ms}),
                      "layers": sorted({str(m["layer"]) for m in ms if m["layer"] is not None})})
    by_clause = defaultdict(int)
    for o in occs:
        by_clause[o["clause"]] += 1
    value = {"extractor_version": EXPRESSION_EXTRACTOR_VERSION, "occurrences": occs, "trivial_predicates": [{"clause": k, "count": v} for k, v in sorted(trivial.items())],
             "reuse_groups": reuse, "counts": {"occurrences": len(occs), "by_clause": [{"clause": k, "count": v} for k, v in sorted(by_clause.items())],
                                               "reuse_groups": len(reuse), "artifacts_without_query_evidence": len(without_query)},
             "definition": "structural expression occurrences per clause (compiled AST, no text parsing); a reuse group is the same exact structure in two or more occurrences; "
                           "descriptive evidence, no ownership judgment"}
    return sort_facts([make_fact("propagation.expression_occurrences", PRO_, "closure", value, EvidenceClass.B, refs=ref)])
