"""Hechos de HARMONIZATION (M3) — dimensiones declaradas, composicion/fan-in y reuso en DOS alcances. Solo HECHOS.

La poblacion se define por EVIDENCIA ESTRUCTURAL/DECLARADA, no por la capa inferida (criterio compuesto que usa el rol declarado: seria circular):
  * dimension  = el export DECLARA rol de dimension (`dataCategory`/`modelingPattern`);
  * composicion = artefacto con >= 2 proveedores de LINAJE (fan-in).
La capa por nombre se copia solo como contexto. NO se decide si un ensamble plano es una debilidad, si una
dimension es "reutilizable" ni si el reuso es bueno: el reuso NO esta calibrado como calidad (CASE 3 `VD_Material`
con mucho reuso entre AM vs CASE 4 con artefactos mas especificos de su producto).

Hechos:
  harmonization.dimension_artifacts (closure)  A/B  artefactos con rol de dimension DECLARADO: asociaciones propias,
        profundidad de su cadena de dependencias (semantica y solo-asociacion), alcance por linaje estricto
  harmonization.composition_artifacts (closure)  B  artefactos con fan-in >= 2: proveedores, contrato declarado propio
        (asociaciones/medidas/dimensiones declaradas), si declara 0/0/0
  harmonization.expression_repetition (closure)  B  DESCRIPTIVO (G3): estructuras de derivacion IDENTICAS (huella exacta) repetidas en >= 2 artefactos
        del cierre, hermanos o relacionados por linaje. Evidencia de reuso para una futura revision de armonizacion; NO es re-derivacion de
        propagacion y no tiene senal ni juicio
  harmonization.assembly_topology (closure)     B  DESCRIPTIVO (G4): topologia de cada artefacto de composicion (proveedores directos, de frontera,
        intermedios y reutilizables, consumidores, distancia a la raiz, participacion sobre las hojas del cierre). Sin umbral ni senal
  harmonization.reuse_context (closure)        B  definicion de los dos alcances y el universo de AM terminales
  harmonization.local_chain_reuse (subject = artefacto)  B  LOCAL_CHAIN_REUSE + conteo crudo de consumidores en la
        clausura (contexto, NO reuso)
  harmonization.cross_am_reuse (subject = artefacto)  B  CROSS_AM_REUSE con su denominador de oportunidad
        (`other_terminal_am_count`); NOT_APPLICABLE (`NO_OTHER_TERMINAL_ANALYTIC_MODEL`) si el universo tiene un solo AM

Los dos alcances NO se mezclan ni se suman:
  LOCAL_CHAIN_REUSE(D) = consumidores directos de D entre el AM terminal y sus proveedores directos de linaje
                         (la cadena de serving local; NO es evidencia suficiente de reuso entre modelos).
  CROSS_AM_REUSE(D)    = numero de OTROS AM terminales del mismo universo cuya clausura semantica recursiva
                         CONTIENE a D (excluye al AM evaluado).
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from .facts_support import NoEvidence, APPLICABLE, make_fact, not_applicable, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import ApplicabilityReason, Area, Assessability, EvidenceClass, EvidenceRef, Fact, Plane
from .scope import AssessmentUniverse

L, ASSOC = EdgeKind.LINEAGE, EdgeKind.ASSOCIATION
H_ = Area.HARMONIZATION


def harmonization_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    ev = evidence if evidence is not None else NoEvidence()
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    lin_dist = graph.distances(am, [L])
    sem_dist = graph.distances(am)
    facts: list[Fact] = []
    clo_ref = [EvidenceRef(am, "closure", None, Plane.MODELING)]

    def inside(names) -> list[str]:
        return [n for n in names if n in closure_set]

    # ---- dimensiones DECLARADAS (A/B) --------------------------------------------------------------
    dims = [n for n in closure if n != am and C.declared_role(entities[n])["declares_dimension"]]
    dim_records = []
    for n in dims:
        e = entities[n]
        assoc_depth = graph.distances(n, [ASSOC])
        sem_depth = graph.distances(n)
        dim_records.append({
            "artifact": n, "declared_role": C.declared_role(e), **C.layer_inference(e),
            "implementation": ident[n].implementation.value, "identity": ident[n].identity.value,
            "has_remote_entity": bool(getattr(e, "remote_entity", "")),
            "is_system_artifact": ident[n].identity.value == "SYSTEM",
            "summary": C.contract_summary(e),
            "own_association_targets": [a["target"] for a in C.association_records(e)],
            "association_chain": {"reachable_by_associations": len(assoc_depth) - 1,
                                  "max_distance": max(assoc_depth.values())},
            "semantic_dependency_chain": {"reachable_artifacts": len(sem_depth) - 1, "max_distance": max(sem_depth.values())},
            "reaches_terminal_by_strict_lineage": graph.feeds(n, am), "semantic_distance": sem_dist.get(n),
        })
    facts.append(make_fact(
        "harmonization.dimension_artifacts", H_, "closure",
        {"count": len(dim_records),
         "with_own_associations": sum(1 for r in dim_records if r["summary"]["n_associations"] > 0),
         "artifacts": dim_records, "selection_basis": "declared dimension role in the export (dataCategory/modelingPattern)"},
        EvidenceClass.A, refs=clo_ref))

    # ---- composicion / fan-in (B) ------------------------------------------------------------------
    comp_records = []
    for n in closure:
        if n == am:
            continue
        provs = inside(graph.providers(n, [L]))
        if len(provs) < 2:
            continue
        e = entities[n]
        s = C.contract_summary(e)
        comp_records.append({
            "artifact": n, "declared_role": C.declared_role(e), **C.layer_inference(e),
            "implementation": ident[n].implementation.value, "identity": ident[n].identity.value,
            "lineage_provider_count": len(provs),
            "lineage_providers": [{"artifact": p, **C.layer_inference(entities[p]),
                                   "declared_role": C.declared_role(entities[p]),
                                   "is_boundary_artifact": not graph.providers(p),
                                   "has_remote_entity": bool(getattr(entities[p], "remote_entity", ""))} for p in provs],
            "summary": s,
            "declares_no_associations_measures_dimensions":
                s["n_associations"] == 0 and s["n_measures"] == 0 and s["n_declared_dimensions"] == 0,
            "lineage_consumer_count": len(inside(graph.consumers(n, [L]))),
            "reaches_terminal_by_strict_lineage": graph.feeds(n, am), "lineage_distance": lin_dist.get(n),
        })
    facts.append(make_fact(
        "harmonization.composition_artifacts", H_, "closure",
        {"count": len(comp_records), "artifacts": comp_records,
         "selection_basis": "two or more direct lineage providers inside the semantic closure"},
        EvidenceClass.B, refs=clo_ref))

    # ---- topologia de ensamble (B, descriptivo; sin umbral) ------------------------------------------
    leaves = {n for n in closure if not graph.providers(n) and not (n in ident and ident[n].identity.value == "SYSTEM")}

    def trans_leaves(n: str) -> set[str]:
        seen, stack = set(), [n]
        while stack:
            x = stack.pop()
            for p in inside(graph.providers(x, [L])):
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        return {x for x in seen if x in leaves}

    topo = []
    for n in closure:
        if n == am:
            continue
        provs = inside(graph.providers(n, [L]))
        if len(provs) < 2:
            continue
        inter = [p for p in provs if graph.providers(p)]
        reusable = [p for p in inter if len(inside(graph.consumers(p, [L]))) >= 2]
        reached = trans_leaves(n)
        topo.append({"artifact": n, "direct_providers": len(provs), "boundary_providers": sum(1 for p in provs if not graph.providers(p)),
                     "intermediate_providers": len(inter), "reusable_intermediate_providers": len(reusable),
                     "lineage_consumers": len(inside(graph.consumers(n, [L]))), "lineage_distance_to_root": lin_dist.get(n),
                     "reaches_root_by_strict_lineage": graph.feeds(n, am), "closure_leaves_reached": len(reached), "closure_leaves_total": len(leaves),
                     "share_of_closure_leaves": round(len(reached) / len(leaves), 4) if leaves else None})
    facts.append(make_fact("harmonization.assembly_topology", H_, "closure",
                           {"count": len(topo), "artifacts": topo,
                            "definition": "composition artifacts = two or more direct lineage providers inside the semantic closure; descriptive topology, no threshold"},
                           EvidenceClass.B, refs=clo_ref))

    # ---- repeticion de expresiones entre artefactos (B, descriptivo) ----------------------------------
    by_fp: dict[str, list] = {}
    for n in closure:
        d = ev.derivation_evidence(n)
        if d is None or (n in ident and ident[n].identity.value == "SYSTEM"):
            continue
        for c in d.columns:
            if set(c.operations) <= {"PASSTHROUGH", "CAST", "LITERAL"}:
                continue                                              # una proyeccion o conversion trivial repetida no es evidencia de reuso
            by_fp.setdefault(c.exact_fp, []).append(c)
    groups = []
    for fp_, cols in sorted(by_fp.items()):
        arts = sorted({c.artifact for c in cols})
        if len(arts) < 2:
            continue
        related = sum(1 for i, a in enumerate(arts) for b in arts[i + 1:] if b in graph.distances(a, [L]) or a in graph.distances(b, [L]))
        pairs = len(arts) * (len(arts) - 1) // 2
        groups.append({"exact_fp": fp_, "shape_fp": cols[0].shape_fp, "operations": list(cols[0].operations),
                       "members": [{"artifact": c.artifact, "field": c.field} for c in sorted(cols, key=lambda c: (c.artifact, c.field, c.branch))],
                       "artifact_pairs": pairs, "lineage_related_pairs": related, "sibling_pairs": pairs - related,
                       "kind": "SIBLING" if related == 0 else ("LINEAGE_RELATED" if related == pairs else "MIXED")})
    facts.append(make_fact("harmonization.expression_repetition", H_, "closure",
                           {"count": len(groups), "groups": groups,
                            "definition": "identical exact derivation structure (alias-insensitive) in two or more closure artifacts; descriptive reuse evidence, not re-derivation"},
                           EvidenceClass.B, refs=clo_ref))

    # ---- contexto de reuso (B) ---------------------------------------------------------------------
    is_am = universe.root_type_of(am) == "analytic-model"
    pair = [am, *inside(graph.providers(am, [L]))]
    others = [a for a in universe.terminal_ams if a != am] if is_am else []      # "AM par" solo tiene sentido cuando la raiz es un AM
    facts.append(make_fact(
        "harmonization.reuse_context", H_, "closure",
        {"terminal_am": am if is_am else None, "root_object": am, "root_type": universe.root_type_of(am), "local_chain_members": pair,
         "universe_terminal_am_count": len(universe.terminal_ams), "other_terminal_am_count": len(others),
         "other_terminal_ams": others,
         "local_chain_definition": "direct consumers of the artifact among the terminal analytic model and its direct lineage providers",
         "cross_am_definition": "other terminal analytic models of the universe whose recursive semantic closure contains the artifact",
         "cross_am_applicability": "NOT_APPLICABLE when no other terminal analytic model exists in the universe (no peer to reuse with)"},
        EvidenceClass.B, refs=clo_ref))

    # ---- reuso por artefacto: DOS hechos separados, sin total ni ranking (B) ----------------------
    targets = {t for n in closure for t in inside(graph.providers(n, [ASSOC]))}
    candidates = sorted((targets | set(dims)) - {am})
    pair_set = set(pair)
    no_peer = not others
    for d in candidates:
        assoc_cons = inside(graph.consumers(d, [ASSOC]))
        lin_cons = inside(graph.consumers(d, [L]))
        direct = sorted(set(assoc_cons) | set(lin_cons))
        local = [c for c in direct if c in pair_set]
        cross = list(universe.other_terminal_ams_containing(d, excluding=am)) if is_am else []
        e = entities[d]
        ref = [EvidenceRef(d, "dependency_graph", None, Plane.MODELING)]
        facts.append(make_fact(
            "harmonization.local_chain_reuse", H_, d,
            {"is_association_target_in_closure": d in targets,
             "declares_dimension": C.declared_role(e)["declares_dimension"],
             **C.layer_inference(e), "identity": ident[d].identity.value,
             "association_consumers_in_closure": assoc_cons, "lineage_consumers_in_closure": lin_cons,
             "direct_consumers_in_closure_count": len(direct),
             "local_chain_reuse_count": len(local), "local_chain_consumers": local,
             "other_direct_consumers_in_closure": [c for c in direct if c not in pair_set]},
            EvidenceClass.B, refs=ref))
        # CROSS_AM_REUSE: el conteo crudo se conserva SIEMPRE junto a su denominador de oportunidad
        # (`other_terminal_am_count`). Sin AM par, 0 NO equivale a "0 reuso pese a haber pares": es NOT_APPLICABLE.
        facts.append(make_fact(
            "harmonization.cross_am_reuse", H_, d,
            {"cross_am_reuse_count": len(cross), "other_terminal_am_count": len(others),
             "terminal_ams_containing_artifact": cross},
            EvidenceClass.B, refs=ref,
            applicability=(not_applicable(ApplicabilityReason.ROOT_NOT_ANALYTIC_MODEL if not is_am else ApplicabilityReason.NO_OTHER_TERMINAL_ANALYTIC_MODEL)
                           if no_peer else APPLICABLE),
            assessability=Assessability.NOT_APPLICABLE if no_peer else Assessability.ASSESSED))

    return sort_facts(facts)
