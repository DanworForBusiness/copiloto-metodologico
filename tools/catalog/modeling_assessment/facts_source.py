"""Hechos de SOURCE / EXTRACTION (M2) — contrato de origen observable en Modeling. Solo HECHOS.

NO emite observaciones, senales, severidades ni juicios. En particular, `DIRECT_RAW_TABLE_ACCESS` NO se genera
aqui (es una observacion de un hito posterior): este modulo preserva el HECHO "el tipo de origen es una tabla
fisica" (`source.contract` con `source_state = RAW_PHYSICAL_TABLE`) y su procedencia exacta, mas los hechos de
alcance aguas abajo derivados del grafo.

Hechos por artefacto remoto (subject = artefacto):
  source.contract              A  conexion, entidad remota, familia/namespace, estado de fuente neutro, stem, label
  source.keys                  A  claves declaradas (Entity.keys) y tamanos de campos/elementos
  source.annotation_inventory  C1 anotaciones del artefacto remoto y `supportedCapabilities` (lector suplementario)
  source.query_filter          C1 `@DataWarehouse.remote.query` crudo, incluido `where` verbatim (lector suplementario)
  source.downstream_reach      B  alcance por linaje/semantico, distancias y dependientes en el scope (grafo)
  source.identity_continuity   B  identidad local vs stem de la fuente (regla y certeza de M1)
Hechos de clausura (subject = "closure"):
  source.family_distribution   B  conteos por namespace y por estado de fuente
  source.connection_consistency B conexiones y fuentes duplicadas por conexion (en la clausura y en el universo)
  source.replication_mode      C1/C2  evidencia de replicacion/federacion: ausente del export estandar => NOT_ASSESSED
Hechos por hoja sin origen remoto (Q1 del owner: aplicabilidad primero, assessability despues):
  source.leaf_contract_status  SAP.* => NOT_APPLICABLE; resto => NOT_ASSESSED (PLANE_BOUNDARY si hay referencia de
                               Processing, PROVENANCE_UNKNOWN si no). Nunca NOT_APPLICABLE "por no tener remote_entity".

Estados de fuente (neutros; derivados de la funcion unica del criterio `classify_extraction`, no copiada):
  CDS_VIEW · SAPI_ODP · RAW_PHYSICAL_TABLE · STANDARD_VIEW_ON_PHYSICAL_TABLE · BW_BRIDGE · OTHER_ORIGIN · NO_REMOTE_ORIGIN
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from . import legacy_bridge as bridge
from .facts_support import entity_refs, make_fact, not_applicable, sort_facts
from .graph import EdgeKind, modeling_population
from .identity import IdentityClassification, classify, remote_stem
from .model import (
    ApplicabilityReason, Area, Assessability, Cause, EvidenceClass, EvidenceRef, Fact, Plane,
)
from .readers import SupplementaryEvidence
from .scope import AssessmentUniverse

L = EdgeKind.LINEAGE
A_ = Area.SOURCE_EXTRACTION


class SourceState(str, Enum):
    CDS_VIEW = "CDS_VIEW"
    SAPI_ODP = "SAPI_ODP"
    RAW_PHYSICAL_TABLE = "RAW_PHYSICAL_TABLE"
    STANDARD_VIEW_ON_PHYSICAL_TABLE = "STANDARD_VIEW_ON_PHYSICAL_TABLE"
    BW_BRIDGE = "BW_BRIDGE"
    OTHER_ORIGIN = "OTHER_ORIGIN"
    NO_REMOTE_ORIGIN = "NO_REMOTE_ORIGIN"


_STATE_OF_CLASSIFICATION = {
    "ideal": SourceState.CDS_VIEW,
    "acceptable_view": SourceState.STANDARD_VIEW_ON_PHYSICAL_TABLE,
    "poor_raw": SourceState.RAW_PHYSICAL_TABLE,
    "acceptable_bw": SourceState.BW_BRIDGE,
}


def source_state_of(remote_entity: str) -> SourceState:
    """Estado de fuente NEUTRO. Reutiliza `classify_extraction` (fuente unica del criterio) y descarta su
    vocabulario evaluativo: solo se conserva el TIPO de origen."""
    cls = bridge.classify_extraction(remote_entity)
    if cls in _STATE_OF_CLASSIFICATION:
        return _STATE_OF_CLASSIFICATION[cls]
    if cls == "unknown":
        return SourceState.SAPI_ODP if bridge.is_sapi_remote(remote_entity) else SourceState.OTHER_ORIGIN
    return SourceState.NO_REMOTE_ORIGIN


def remote_namespace(remote_entity: str) -> str:
    """Prefijo de familia del `remote_entity` ("ABAP_CDS" en "ABAP_CDS.X$P"); vacio si no hay namespace."""
    s = str(remote_entity or "")
    return s.split(".", 1)[0].strip("\"'") if "." in s else ""


def _src_file(entity: Any, evidence: SupplementaryEvidence) -> str | None:
    files = evidence.files_defining(getattr(entity, "name", ""))
    return files[0] if files else (getattr(entity, "file", None) or None)


def source_facts(
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
    facts: list[Fact] = []

    remote = [n for n in closure if getattr(entities[n], "remote_entity", "")]

    for name in remote:
        e = entities[name]
        sf = _src_file(e, ev)
        state = source_state_of(e.remote_entity)
        stem = remote_stem(e.remote_entity)
        facts.append(make_fact(
            "source.contract", A_, name,
            {"remote_connection": e.remote_connection or None, "remote_entity": e.remote_entity,
             "namespace": remote_namespace(e.remote_entity), "source_state": state.value,
             "remote_stem": stem or None, "artifact_label": e.label or None},
            EvidenceClass.A, refs=entity_refs(e, ["remote_connection", "remote_entity", "label"], source_file=sf)))
        facts.append(make_fact(
            "source.keys", A_, name,
            {"key_count": len(e.keys), "key_fields": list(e.keys), "field_count": len(e.fields),
             "element_count": e.n_elements},
            EvidenceClass.A, refs=entity_refs(e, ["keys", "fields", "n_elements"], source_file=sf)))

        # ---- evidencia cruda (lector suplementario): C1 --------------------------------------------
        rt = ev.remote_table(name)
        raw_ref = [EvidenceRef(name, "remote_table_definition", ev.files_defining(name)[0] if ev.files_defining(name) else None,
                               Plane.MODELING)]
        if rt is None:
            cause = Cause.NOT_IN_SNAPSHOT        # sin lector, o el archivo de la remote table no esta en el pool
            facts.append(make_fact("source.annotation_inventory", A_, name, None, EvidenceClass.C1, refs=raw_ref,
                                   assessability=Assessability.NOT_ASSESSED, cause=cause))
            facts.append(make_fact("source.query_filter", A_, name, None, EvidenceClass.C1, refs=raw_ref,
                                   assessability=Assessability.NOT_ASSESSED, cause=cause))
        else:
            ref = [EvidenceRef(name, "remote_table_definition", rt.source_file, Plane.MODELING)]
            facts.append(make_fact(
                "source.annotation_inventory", A_, name,
                {"annotation_keys": list(rt.annotation_keys), "supported_capabilities": list(rt.supported_capabilities),
                 "modeling_pattern": rt.modeling_pattern,
                 "raw_remote_entity_matches_loader": rt.remote_entity == e.remote_entity,
                 "raw_remote_connection_matches_loader": rt.remote_connection == (e.remote_connection or None)},
                EvidenceClass.C1, refs=ref))
            facts.append(make_fact(
                "source.query_filter", A_, name,
                {"has_remote_query": rt.has_remote_query, "where": rt.query_where,
                 "query_column_count": rt.query_column_count},
                EvidenceClass.C1, refs=ref))

        # ---- alcance aguas abajo (grafo): B --------------------------------------------------------
        # `in_semantic_scope` y `reaches_terminal_by_strict_lineage` son hechos SEPARADOS: que no exista camino de
        # linaje puro hasta el AM NO significa irrelevante/periferico (p. ej. AM -ASSOC-> DIM -LINEAGE-> RAW).
        lin_consumers = [c for c in graph.consumers(name, [L]) if c in closure_set]
        assoc_consumers = [c for c in graph.consumers(name, [EdgeKind.ASSOCIATION]) if c in closure_set]
        facts.append(make_fact(
            "source.downstream_reach", A_, name,
            {"in_semantic_scope": True, "reaches_terminal_by_strict_lineage": graph.feeds(name, am), "lineage_distance": lineage_dist.get(name),
             "semantic_distance": semantic_dist.get(name),
             "direct_lineage_consumers": lin_consumers, "direct_association_consumers": assoc_consumers,
             "transitive_lineage_dependents_in_scope": [c for c in graph.transitive_consumers(name, [L]) if c in closure_set],
             "transitive_semantic_dependents_in_scope": [c for c in graph.transitive_consumers(name) if c in closure_set]},
            EvidenceClass.B, refs=[EvidenceRef(name, "dependency_graph", None, Plane.MODELING)]))

        i = ident[name]
        facts.append(make_fact(
            "source.identity_continuity", A_, name,
            {"identity": i.identity.value, "rule": i.rule.value, "certainty": i.certainty.value if i.certainty else None,
             "remote_stem": i.remote_stem},
            EvidenceClass.B, refs=i.evidence))

    # ---- hechos de clausura ------------------------------------------------------------------
    by_ns: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for name in remote:
        by_ns[remote_namespace(entities[name].remote_entity)] = by_ns.get(remote_namespace(entities[name].remote_entity), 0) + 1
        st = source_state_of(entities[name].remote_entity).value
        by_state[st] = by_state.get(st, 0) + 1
    facts.append(make_fact(
        "source.family_distribution", A_, "closure",
        {"remote_artifacts": len(remote),
         "by_namespace": [{"namespace": k, "artifacts": v} for k, v in sorted(by_ns.items())],
         "by_source_state": [{"source_state": k, "artifacts": v} for k, v in sorted(by_state.items())]},
        EvidenceClass.B, refs=[EvidenceRef(am, "closure", None, Plane.MODELING)]))

    conns: dict[str, int] = {}
    for name in remote:
        c = entities[name].remote_connection or ""
        conns[c] = conns.get(c, 0) + 1

    def dual(names) -> list[dict]:
        per: dict[str, set[str]] = {}
        for n in names:
            e = pop[n]
            if getattr(e, "remote_entity", ""):
                per.setdefault(e.remote_entity, set()).add(e.remote_connection or "")
        return [{"remote_entity": k, "connections": sorted(v)} for k, v in sorted(per.items()) if len(v) > 1]

    in_closure_dual = dual(remote)
    touching = {r["remote_entity"] for r in dual(pop)} & {entities[n].remote_entity for n in remote}
    universe_dual = [r for r in dual(pop) if r["remote_entity"] in touching]
    facts.append(make_fact(
        "source.connection_consistency", A_, "closure",
        {"connection_count": len([c for c in conns if c]),
         "artifacts_by_connection": [{"connection": k or None, "artifacts": v} for k, v in sorted(conns.items())],
         "dual_connection_entities_in_closure": in_closure_dual,
         "dual_connection_entities_in_universe_touching_closure": universe_dual},
        EvidenceClass.A, refs=[EvidenceRef(am, "closure", None, Plane.MODELING)]))

    # ---- modo de replicacion/federacion --------------------------------------------------------
    repl_refs = [EvidenceRef(am, "remote_table_definitions", None, Plane.MODELING)]
    if not ev.available:
        facts.append(make_fact("source.replication_mode", A_, "closure", {"remote_tables_scanned": 0},
                               EvidenceClass.C2, refs=repl_refs, assessability=Assessability.NOT_ASSESSED,
                               cause=Cause.NOT_IN_SNAPSHOT))
    else:
        scanned = [ev.remote_table(n) for n in remote]
        found = [(n, t.replication_annotation_keys) for n, t in zip(remote, scanned) if t is not None and t.replication_annotation_keys]
        n_scanned = sum(1 for t in scanned if t is not None)
        if found:
            facts.append(make_fact(
                "source.replication_mode", A_, "closure",
                {"remote_tables_scanned": n_scanned,
                 "artifacts_with_annotations": [{"artifact": n, "annotation_keys": list(k)} for n, k in found]},
                EvidenceClass.C1, refs=repl_refs))
        else:
            # Ausente del export estandar de Modeling: NO es C1 (no hay evidencia que el pipeline deje de consumir).
            facts.append(make_fact(
                "source.replication_mode", A_, "closure", {"remote_tables_scanned": n_scanned},
                EvidenceClass.C2, refs=repl_refs, assessability=Assessability.NOT_ASSESSED,
                cause=Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT))

    # ---- hojas sin origen remoto (Q1: aplicabilidad primero, assessability despues) ------------
    for name in closure:
        e = entities[name]
        if graph.providers(name) or getattr(e, "remote_entity", ""):
            continue
        i = ident[name]
        refs = i.evidence
        value = {"implementation": i.implementation.value, "identity": i.identity.value}
        if i.identity.value == "SYSTEM":
            facts.append(make_fact("source.leaf_contract_status", A_, name, value, EvidenceClass.A, refs=refs,
                                   applicability=not_applicable(ApplicabilityReason.SYSTEM_ARTIFACT),
                                   assessability=Assessability.NOT_APPLICABLE))
            continue
        touched = ev.flows_touching(name)
        value["processing_reference_present"] = bool(touched)
        facts.append(make_fact(
            "source.leaf_contract_status", A_, name, value, EvidenceClass.D, refs=refs,
            assessability=Assessability.NOT_ASSESSED,
            cause=Cause.PLANE_BOUNDARY if touched else Cause.PROVENANCE_UNKNOWN))

    return sort_facts(facts)
