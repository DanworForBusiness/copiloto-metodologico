"""Hechos de RECONVERGENCIA de ramas semanticas paralelas (S5, evidence release). Solo HECHOS descriptivos: compartir una fuente NO es una desviacion.

Un par de ramas paralelas = dos consumidores distintos de un MISMO proveedor dentro del cierre, ninguno ancestro del otro (por linaje), que ademas
RECONVERGEN aguas abajo (existe un artefacto del cierre que desciende de ambos por linaje). Para cada par se persiste, sin interpretarlo:
  shared_provider            el contrato compartido aguas arriba;
  branch_a / branch_b        los consumidores y los CAMPOS que cada uno consume del proveedor compartido (lectura del AST: alias resueltos a entidad);
  overlap / jaccard / subset_ratio / consumer_asymmetry
                             solape de los conjuntos de campos: Jaccard (|A∩B| / |A∪B|), razon sobre el menor (|A∩B| / min) y la forma del solape
                             (SYMMETRIC | A_SUBSET_OF_B | B_SUBSET_OF_A | PARTIAL_OVERLAP | DISJOINT). Un Jaccard alto sugiere el MISMO contrato consumido por
                             las dos ramas; un subconjunto asimetrico sugiere una consulta puntual (lookup);
  common_upstream_identities identidades compartidas aguas arriba por las dos ramas (cierre de linaje de cada una);
  reconvergence_artifact     el artefacto mas cercano a las ramas que desciende de ambas;
  paths / depth              caminos de linaje distintos desde la raiz hasta el proveedor compartido y su profundidad minima/maxima.

El numero de pares, el solape o los caminos NO son un puntaje. Es evidencia candidata a una revision de estabilidad/reuso de contratos.
"""
from __future__ import annotations

from typing import Any, Mapping

from .facts_support import make_fact, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, EvidenceRef, Fact, Plane
from .scope import AssessmentUniverse

PRO_ = Area.PROPAGATION
L = EdgeKind.LINEAGE
ASYMMETRIES = ("SYMMETRIC", "A_SUBSET_OF_B", "B_SUBSET_OF_A", "PARTIAL_OVERLAP", "DISJOINT")
RECONVERGENCE_EXTRACTOR_VERSION = "branch-reconvergence/v1"
_MAX_LISTED_UPSTREAM = 40


def _asymmetry(a: set[str], b: set[str]) -> str:
    inter = a & b
    if not inter:
        return "DISJOINT"
    if a == b:
        return "SYMMETRIC"
    if a < b:
        return "A_SUBSET_OF_B"
    if b < a:
        return "B_SUBSET_OF_A"
    return "PARTIAL_OVERLAP"


def reconvergence_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    system = {n for n in closure if n in ident and ident[n].identity.value == "SYSTEM"}

    anc: dict[str, set[str]] = {}

    def ancestors(x: str) -> set[str]:
        if x not in anc:
            anc[x] = {a for a in graph.lineage_closure(x) if a in closure_set}
        return anc[x]

    used: dict[str, dict[str, set[str]]] = {}                           # consumidor -> proveedor -> campos
    for n in closure:
        d = evidence.derivation_evidence(n) if evidence is not None else None
        if d is not None:
            used[n] = {p: set(fs) for p, fs in d.consumed_fields}

    dist = graph.distances(am, [L]) if graph.has_node(am) else {}
    path_cache: dict[str, tuple[int, int, int]] = {}

    def path_stats(target: str) -> tuple[int, int, int]:
        """(caminos de linaje raiz->target, profundidad minima, profundidad maxima) dentro del cierre."""
        if target in path_cache:
            return path_cache[target]
        memo: dict[str, tuple[int, int, int] | None] = {}

        def rec(x: str, visiting: frozenset) -> tuple[int, int, int] | None:
            if x == target:
                return (1, 0, 0)
            if x in memo:
                return memo[x]
            if x in visiting:
                return None
            cnt, mn, mx = 0, None, None
            for p in graph.providers(x, [L]):
                if p not in closure_set or p in system:
                    continue
                r = rec(p, visiting | {x})
                if r is None or r[0] == 0:
                    continue
                cnt += r[0]
                mn = r[1] + 1 if mn is None else min(mn, r[1] + 1)
                mx = r[2] + 1 if mx is None else max(mx, r[2] + 1)
            out = (cnt, mn or 0, mx or 0)
            memo[x] = out
            return out
        r = rec(am, frozenset()) or (0, 0, 0)
        path_cache[target] = r
        return r

    records = []
    consumers_of: dict[str, list[str]] = {}
    for c, per in used.items():
        for p in per:
            if p in closure_set and p not in system and c in closure_set:
                consumers_of.setdefault(p, []).append(c)
    for p, cons in sorted(consumers_of.items()):
        cons = sorted(set(cons))
        for i in range(len(cons)):
            for j in range(i + 1, len(cons)):
                a, b = cons[i], cons[j]
                if a in ancestors(b) or b in ancestors(a):
                    continue
                desc = [x for x in closure if x not in (a, b) and a in ancestors(x) and b in ancestors(x)]
                if not desc:
                    continue                                              # no reconvergen por linaje dentro del cierre
                nearest = sorted(desc, key=lambda x: (-(dist.get(x, -1)), x))[0]
                fa, fb = used[a][p], used[b][p]
                inter = fa & fb
                common = sorted((ancestors(a) & ancestors(b)) - system)
                n_paths, dmin, dmax = path_stats(p)
                records.append({
                    "shared_provider": p, "branch_a": a, "branch_b": b, "fields_consumed_a": len(fa), "fields_consumed_b": len(fb), "overlap": len(inter),
                    "jaccard": round(len(inter) / len(fa | fb), 4) if (fa | fb) else None, "subset_ratio": round(len(inter) / min(len(fa), len(fb)), 4) if fa and fb else None,
                    "consumer_asymmetry": _asymmetry(fa, fb), "shared_fields": sorted(inter)[:60],
                    "common_upstream_count": len(common), "common_upstream_identities": common[:_MAX_LISTED_UPSTREAM], "common_upstream_truncated": len(common) > _MAX_LISTED_UPSTREAM,
                    "reconvergence_artifact": nearest, "branch_depths": {"a": dist.get(a), "b": dist.get(b)},
                    "paths_root_to_shared_provider": n_paths, "path_depth_min": dmin, "path_depth_max": dmax})
    value = {"extractor_version": RECONVERGENCE_EXTRACTOR_VERSION, "reconvergences": records,
             "counts": {"pairs": len(records), "shared_providers": len({r["shared_provider"] for r in records})},
             "definition": "two consumers of the same provider inside the closure, neither an ancestor of the other, that reconverge downstream by lineage; "
                           "descriptive evidence of shared contracts, not a deviation"}
    return sort_facts([make_fact("propagation.branch_reconvergence", PRO_, "closure", value, EvidenceClass.B, refs=[EvidenceRef(am, "closure", None, Plane.MODELING)])])
