"""Hechos de COMPLETITUD y PLANOS del cierre semantico (S1, ``closure-rules/v1``). Solo HECHOS: sin juicio de calidad.

COBERTURA vs CONFIANZA (decision del owner, Gate 8A.2):
  * un miembro REQUERIDO del cierre que se conoce y queda sin evaluar (referencia sin resolver, excluida por el plano) baja la COBERTURA: es un problema de
    COMPLETITUD. Se expresa como unidades NOT_ASSESSED de la superficie requerida ``am.closure_reference_resolution``;
  * una referencia cuya identidad es incierta (resolucion AMBIGUA) NO baja la cobertura (el artefacto existe y se evalua): afecta la CONFIANZA.

Hechos:
  am.closure_reference_resolution (subject = miembro del cierre)  A/B  las referencias DECLARADAS de ese miembro (linaje, asociacion), cada una en uno de
        RESOLVED | UNRESOLVED | EXCLUDED_BY_PLANE | AMBIGUOUS. Las referencias a objetos de plataforma (`SAP.*`, p. ej. dimensiones de tiempo) se listan
        aparte como ``platform_provided``: no son artefactos del proyecto que falten y no cambian el estado. Los identificadores FROM/JOIN que no resuelven (alias internos de vistas graficas como
        ``Projection 1``) NO son referencias a artefactos: se cuentan aparte y no cambian el estado.
  am.closure_plane_context (closure)  B  descriptivo: miembros HOJA que un flujo produce (y por que son miembros), entidades de contexto de Processing que
        ningun artefacto de Modeling lee, y la diferencia contra la politica anterior (marca del loader): lo que el cierre corregido agrega/quita.
"""
from __future__ import annotations

from typing import Any, Mapping

from .facts_support import make_fact, sort_facts
from .identity import _is_system_name
from .graph import PLANE_POLICY_LOADER_FILE_FLAG, PLANE_POLICY_MODELING_MEMBERSHIP, DependencyGraph, UnresolvedReason, is_processing_context_entity
from .model import Area, Assessability, Cause, EvidenceClass, EvidenceRef, Fact, Plane
from .scope import AssessmentUniverse

CLOSURE_RULESET = "closure-rules/v1"

REF_RESOLVED, REF_UNRESOLVED, REF_EXCLUDED_BY_PLANE, REF_AMBIGUOUS = "RESOLVED", "UNRESOLVED", "EXCLUDED_BY_PLANE", "AMBIGUOUS"
REFERENCE_STATES = (REF_RESOLVED, REF_UNRESOLVED, REF_EXCLUDED_BY_PLANE, REF_AMBIGUOUS)

AM_ = Area.ANALYTIC_MODEL


def _case_candidates(target: str, names: set[str]) -> list[str]:
    """Nodos cuyo nombre coincide con ``target`` SIN distinguir mayusculas (y no exactamente): identidad incierta, nunca se acepta como arista."""
    low = target.lower()
    return sorted(n for n in names if n != target and n.lower() == low)


def closure_facts(universe: AssessmentUniverse, entities: Mapping[str, Any], root: str, *, evidence: Any | None = None) -> tuple[Fact, ...]:
    graph = universe.graph
    closure = universe.closure_of(root)
    closure_set = set(closure)
    names = set(graph.nodes)
    facts: list[Fact] = []
    unresolved_by: dict[str, list] = {}
    for u in graph.unresolved_of(closure):
        unresolved_by.setdefault(u.consumer, []).append(u)
    boundary_by: dict[str, list] = {}
    for b in graph.processing_context.boundary_references:
        if b.consumer in closure_set:
            boundary_by.setdefault(b.consumer, []).append(b)

    for member in closure:
        resolved = [{"target": e.provider, "kind": e.kind.value, "origin": e.origin.value} for e in graph.edges_of(member)]
        unres, ambiguous, excluded, platform, aliases = [], [], [], [], 0
        for u in unresolved_by.get(member, []):
            if u.reason is UnresolvedReason.SELF_REFERENCE:
                continue
            cands = _case_candidates(u.target, names)
            if cands:
                ambiguous.append({"target": u.target, "kind": u.kind.value, "evidence_source": u.evidence_source, "candidates": cands})
            elif _is_system_name(u.target):
                platform.append({"target": u.target, "kind": u.kind.value, "evidence_source": u.evidence_source})      # objeto de plataforma (`SAP.*`): no es un artefacto del proyecto que falte
            elif u.reason is UnresolvedReason.MISSING_TARGET:
                unres.append({"target": u.target, "kind": u.kind.value, "evidence_source": u.evidence_source})
            else:
                aliases += 1                                      # UNVERIFIED_QUERY_REFERENCE sin candidato: alias interno / sombra, no un artefacto
        for b in boundary_by.get(member, []):
            excluded.append({"target": b.target, "kind": b.kind.value, "evidence_source": b.evidence_source})
        key = lambda r: (r["target"], r["kind"], r.get("evidence_source", ""))              # noqa: E731
        unres, ambiguous, excluded, platform = sorted(unres, key=key), sorted(ambiguous, key=key), sorted(excluded, key=key), sorted(platform, key=key)
        value = {"resolved": sorted(resolved, key=lambda r: (r["target"], r["kind"])), "unresolved": unres, "excluded_by_plane": excluded, "ambiguous": ambiguous, "platform_provided": platform,
                 "internal_query_aliases_unverified": aliases,
                 "counts": {REF_RESOLVED: len(resolved), REF_UNRESOLVED: len(unres), REF_EXCLUDED_BY_PLANE: len(excluded), REF_AMBIGUOUS: len(ambiguous)}}
        ref = [EvidenceRef(member, "dependency_graph", None, Plane.MODELING)]
        if excluded or unres:
            cause = Cause.PLANE_BOUNDARY if excluded else Cause.NOT_IN_SNAPSHOT
            facts.append(make_fact("am.closure_reference_resolution", AM_, member, value, EvidenceClass.B, refs=ref, assessability=Assessability.NOT_ASSESSED, cause=cause))
        else:
            facts.append(make_fact("am.closure_reference_resolution", AM_, member, value, EvidenceClass.B, refs=ref))

    # ---- contexto de planos: miembros producidos por un flujo + diferencia contra la politica anterior --------------------------------------------------
    pc = graph.processing_context
    ev = evidence
    members = []
    for m in pc.flow_produced_members:
        if m not in closure_set:
            continue
        producers = []
        if ev is not None and hasattr(ev, "target_flows"):
            targets, _amb = ev.target_flows(m, entities.keys())
            producers = sorted({f"{fl.kind}:{fl.name}" for fl, _ in targets})
        consumers = [c for c in graph.consumers(m) if c in closure_set]
        members.append({"artifact": m, "semantic_consumers": consumers, "semantic_providers": [p for p in graph.providers(m) if p in closure_set], "producer_flows": producers,
                        "loader_file_flag": is_processing_context_entity(entities[m]), "membership_basis": "READ_BY_SEMANTIC_MEMBER", "excluded_before": "LOADER_FILE_FLAG"})
    old_graph = DependencyGraph.from_entities(entities, PLANE_POLICY_LOADER_FILE_FLAG)
    old_closure = set(old_graph.semantic_closure(root)) if old_graph.has_node(root) else set()
    value = {"closure_rules": CLOSURE_RULESET, "plane_policy": PLANE_POLICY_MODELING_MEMBERSHIP,
             "flow_produced_members": sorted(members, key=lambda r: r["artifact"]),
             "processing_only_entities": sorted(pc.dataflow_targets),
             "loader_file_flag_closure_size": len(old_closure),
             "added_vs_loader_file_flag": sorted(closure_set - old_closure), "removed_vs_loader_file_flag": sorted(old_closure - closure_set),
             "definition": "a local table read by a Modeling artifact is a semantic closure member even when a flow produces it; the producer flow is provenance context"}
    facts.append(make_fact("am.closure_plane_context", AM_, "closure", value, EvidenceClass.B, refs=[EvidenceRef(root, "closure", None, Plane.MODELING)]))
    return sort_facts(facts)
