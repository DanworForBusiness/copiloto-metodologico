"""Hechos de INGESTION (M2) — frontera fuente -> modelo. Solo HECHOS: sin observaciones, senales ni juicios.

No se decide si un camino fuente -> vista de capa >= 3 "salta" una capa, ni si hay divergencia rol/capa: esos son
hitos posteriores. Aqui se RETIENE lo necesario para que la senal posterior pueda distinguirlos.

Hechos de clausura (subject = "closure"):
  ingestion.boundary_artifacts          B  hojas del grafo semantico con su implementacion e identidad (M1)
  ingestion.duplicate_remote_imports    B  mismo `remote_entity` bajo >1 identidad local (clausura y universo)
  ingestion.inferred_layer_vs_lineage_depth B  capa INFERIDA (criterio compuesto) + su base vs profundidad real por linaje/semantica
  ingestion.processing_context_inventory  PROCESSING  que se leyo de RF/DF (evidencia cross-plane complementaria)
  ingestion.dataflow_boundary           PROCESSING  referencias de Modeling a targets de Data Flow (M1)
Hechos por hoja (subject = hoja):
  ingestion.first_consumers             B  consumidores directos por LINEAGE y por ASSOCIATION (con origen de arista)
Hechos por par hoja->consumidor de linaje (subject = "hoja->consumidor"):
  ingestion.field_retention             B  campos de la fuente retenidos/quitados/agregados en el primer consumidor
Hechos por artefacto referenciado por un flujo (subject = artefacto):
  ingestion.processing_reference        PROCESSING  RF/DF que lo referencian, rol y sistema de origen declarado

Frontera de planos: la evidencia de Processing (RF/DF) se ADJUNTA con `plane = PROCESSING`; no agrega artefactos al
scope, no cambia poblacion ni denominadores, no cambia la identidad de Modeling y no convierte NOT_ASSESSED en
ASSESSED. Los RF/DF no son nodos del grafo de Modeling.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from .facts_support import make_fact, sort_facts
from .graph import EdgeKind, modeling_population
from .identity import IdentityClassification, classify
from .model import Area, Assessability, Cause, EvidenceClass, EvidenceRef, Fact, Plane
from .readers import DATA_FLOW, REPLICATION_FLOW, SupplementaryEvidence
from .scope import AssessmentUniverse

L, ASSOC = EdgeKind.LINEAGE, EdgeKind.ASSOCIATION
I_ = Area.INGESTION


def _field_names(entity: Any) -> list[str]:
    return [f["campo"] for f in (getattr(entity, "fields", None) or [])]


def _src(entity: Any, ev: SupplementaryEvidence) -> str | None:
    files = ev.files_defining(getattr(entity, "name", ""))
    return files[0] if files else (getattr(entity, "file", None) or None)


def ingestion_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    evidence: SupplementaryEvidence | None = None,
    identity: Mapping[str, IdentityClassification] | None = None,
) -> tuple[Fact, ...]:
    ev = evidence if evidence is not None else SupplementaryEvidence.empty()
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    lineage_dist = graph.distances(am, [L])
    semantic_dist = graph.distances(am)
    pop = modeling_population(entities)
    origin = {(e.consumer, e.provider, e.kind): e.origin.value for e in graph.edges}
    facts: list[Fact] = []
    clo_ref = [EvidenceRef(am, "closure", None, Plane.MODELING)]

    leaves = [n for n in closure if not graph.providers(n)]

    def consumer_record(consumer: str, leaf: str, kind: EdgeKind) -> dict:
        c = ident[consumer]
        return {"consumer": consumer, **C.layer_inference(entities[consumer]),
                "implementation": c.implementation.value, "edge_origin": origin[(consumer, leaf, kind)]}

    # ---- hojas / frontera ------------------------------------------------------------------------
    facts.append(make_fact(
        "ingestion.boundary_artifacts", I_, "closure",
        {"count": len(leaves),
         "artifacts": [{"artifact": n, "implementation": ident[n].implementation.value,
                        "identity": ident[n].identity.value,
                        "has_remote_entity": bool(getattr(entities[n], "remote_entity", "")),
                        **C.layer_inference(entities[n])} for n in leaves]},
        EvidenceClass.B, refs=clo_ref))

    for leaf in leaves:
        lin = [c for c in graph.consumers(leaf, [L]) if c in closure_set]
        asc = [c for c in graph.consumers(leaf, [ASSOC]) if c in closure_set]
        facts.append(make_fact(
            "ingestion.first_consumers", I_, leaf,
            {"lineage_consumers": [consumer_record(c, leaf, L) for c in lin],
             "association_consumers": [consumer_record(c, leaf, ASSOC) for c in asc]},
            EvidenceClass.B, refs=[EvidenceRef(leaf, "dependency_graph", None, Plane.MODELING)]))
        src_fields = _field_names(entities[leaf])
        for c in lin:
            cons_fields = _field_names(entities[c])
            s, t = set(src_fields), set(cons_fields)
            facts.append(make_fact(
                "ingestion.field_retention", I_, f"{leaf}->{c}",
                {"source_field_count": len(src_fields), "consumer_field_count": len(cons_fields),
                 "source_element_count": entities[leaf].n_elements, "consumer_element_count": entities[c].n_elements,
                 "retained": len(s & t), "removed_fields": [f for f in src_fields if f not in t],
                 "added_fields": [f for f in cons_fields if f not in s]},
                EvidenceClass.B,
                refs=[EvidenceRef(leaf, "fields", _src(entities[leaf], ev), Plane.MODELING),
                      EvidenceRef(c, "fields", _src(entities[c], ev), Plane.MODELING)]))

    # ---- duplicados de import -------------------------------------------------------------------
    def groups(names) -> dict[str, list[str]]:
        g: dict[str, list[str]] = {}
        for n in names:
            e = pop[n]
            if getattr(e, "remote_entity", ""):
                g.setdefault(e.remote_entity, []).append(n)
        return {k: sorted(v) for k, v in g.items() if len(v) > 1}

    in_closure = groups(closure)
    closure_remotes = {entities[n].remote_entity for n in closure if getattr(entities[n], "remote_entity", "")}
    in_universe = {k: v for k, v in groups(pop).items() if k in closure_remotes}
    facts.append(make_fact(
        "ingestion.duplicate_remote_imports", I_, "closure",
        {"in_closure": [{"remote_entity": k, "local_artifacts": v} for k, v in sorted(in_closure.items())],
         "in_universe_touching_closure": [
             {"remote_entity": k, "local_artifacts": v, "in_closure": [x for x in v if x in closure_set]}
             for k, v in sorted(in_universe.items())]},
        EvidenceClass.B, refs=clo_ref))

    # ---- capa por nombre vs profundidad real ---------------------------------------------------------
    facts.append(make_fact(
        "ingestion.inferred_layer_vs_lineage_depth", I_, "closure",
        {"artifacts": [{"artifact": n, **C.layer_inference(entities[n]),
                        "lineage_depth": lineage_dist.get(n), "semantic_depth": semantic_dist.get(n),
                        "implementation": ident[n].implementation.value} for n in closure]},
        EvidenceClass.B, refs=clo_ref))

    # ---- plano PROCESSING (evidencia complementaria cross-plane) ---------------------------------
    pc = universe.processing_context
    boundary = [b for b in pc.boundary_references if b.consumer in closure_set]
    if ev.data_flows_acquired:
        facts.append(make_fact(
            "ingestion.dataflow_boundary", I_, "closure",
            {"dataflow_targets_referenced": sorted({b.target for b in boundary}),
             "references": [{"consumer": b.consumer, "target": b.target, "edge_kind": b.kind.value,
                             "evidence_source": b.evidence_source} for b in boundary]},
            EvidenceClass.B, plane=Plane.PROCESSING, refs=clo_ref))
    else:                                                               # Data Flows NO adquiridos: "sin referencias" no es una conclusion
        facts.append(make_fact(
            "ingestion.dataflow_boundary", I_, "closure", {"dataflow_targets_referenced": [], "references": []},
            EvidenceClass.B, plane=Plane.PROCESSING, refs=clo_ref,
            assessability=Assessability.NOT_ASSESSED, cause=Cause.NOT_IN_SNAPSHOT))

    if not ev.available or not ev.acquired_flow_families:
        facts.append(make_fact(
            "ingestion.processing_context_inventory", I_, "closure",
            {"replication_flows_read": 0, "data_flows_read": 0} if not ev.available and ev.flows_conclusive
            else {"replication_flows_read": None, "data_flows_read": None},        # familias NO adquiridas: sin dato (no cero)
            EvidenceClass.A, plane=Plane.PROCESSING, refs=clo_ref,
            assessability=Assessability.NOT_ASSESSED, cause=Cause.NOT_IN_SNAPSHOT))
    else:
        referenced = 0
        for name in closure:
            touched = ev.flows_touching(name, entities.keys())
            if not touched:
                continue
            referenced += 1
            facts.append(make_fact(
                "ingestion.processing_reference", I_, name,
                {"flows": [{"kind": fl.kind, "name": fl.name, "role": role,
                            "source_entities": list(fl.source_entities), "target_entities": list(fl.target_entities),
                            "source_systems": [{"connection_id": s.connection_id, "connection_type": s.connection_type,
                                                "container": s.container} for s in fl.source_systems]}
                           for fl, role in touched]},
                EvidenceClass.A, plane=Plane.PROCESSING,
                refs=[EvidenceRef(name, "flow_reference", fl.source_file, Plane.PROCESSING) for fl, _r in touched]))
        facts.append(make_fact(
            "ingestion.processing_context_inventory", I_, "closure",
            {"replication_flows_read": sum(1 for f in ev.flows if f.kind == REPLICATION_FLOW) if "replication-flows" in ev.acquired_flow_families else None,
             "data_flows_read": sum(1 for f in ev.flows if f.kind == DATA_FLOW) if "data-flows" in ev.acquired_flow_families else None,
             "closure_artifacts_referenced": referenced,
             "read_issues": [{"source_file": i.source_file, "reason": i.reason} for i in ev.issues]},
            EvidenceClass.A, plane=Plane.PROCESSING, refs=clo_ref,
            **({} if ev.flows_conclusive else {"assessability": Assessability.PARTIALLY_ASSESSED, "cause": Cause.NOT_IN_SNAPSHOT})))

    return sort_facts(facts)
