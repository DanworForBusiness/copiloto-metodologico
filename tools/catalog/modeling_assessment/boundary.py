"""Frontera FISICA de integracion observada de una hoja local (S1, ``closure-rules/v1``): ¿en que borde externo nace la hoja? Solo HECHOS, sin juicio.

Una hoja local producida por un flujo (``2TR_VBAK`` <- ``DF_VBAK_HIST`` <- ``VBAK_HIST`` <- ``1TR_VBAK``) tiene una cadena de PROCEDENCIA que cruza de plano:
la hoja es miembro del cierre semantico; el flujo y las fuentes que lee son contexto de procedencia. El borde externo que la cadena revela (p. ej. la tabla
fisica ``ABAPTABLES.VBAK``) cuenta para el contrato de FUENTE/INGESTA, pero:

  * NO es miembro del cierre semantico por el solo hecho de ser alcanzado por procedencia;
  * NO es ``DIRECT_RAW_TABLE_ACCESS`` (esa regla mira consumo de una tabla fisica remota DENTRO del linaje semantico de Modeling).

Los dos conceptos no comparten hecho: ``source.physical_boundary`` (este modulo) vs ``source.contract`` + la regla.

Clasificacion del borde SOLO con evidencia explicita (nunca por nombre tecnico):
  * entidad remota importada: la FAMILIA/NAMESPACE de ``@DataWarehouse.remote.entity`` (``ABAP_CDS.*``, ``SAPI.*``, ``ABAPTABLES.*``) mediante el criterio unico
    ``classify_extraction`` (el estado de fuente que ya expone ``source.contract``);
  * Replication Flow: el ``container`` del sistema fuente declarado por el propio flujo (``/CDS_EXTRACTION``, ``/CDS``); cualquier otro contenedor queda
    ``UNCLASSIFIED`` (no se infiere gobierno).
Una vista o tabla materializada de Datasphere AGUAS ABAJO no convierte una frontera fisica en una API gobernada.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Mapping

from .graph import DependencyGraph, EdgeKind
from .readers import DATA_FLOW, REPLICATION_FLOW

BOUNDARY_VERSION = "physical-boundary/v1"

BC_PHYSICAL_TABLE = "PHYSICAL_TABLE"
BC_GOVERNED_CDS = "GOVERNED_CDS_CONTRACT"
BC_GOVERNED_SAPI = "GOVERNED_SAPI_EXTRACTOR"
BC_STANDARD_VIEW_ON_PHYSICAL = "STANDARD_VIEW_ON_PHYSICAL_TABLE"
BC_BW_BRIDGE = "BW_BRIDGE"
BC_UNCLASSIFIED = "UNCLASSIFIED"
BOUNDARY_CLASSES = frozenset({BC_PHYSICAL_TABLE, BC_GOVERNED_CDS, BC_GOVERNED_SAPI, BC_STANDARD_VIEW_ON_PHYSICAL, BC_BW_BRIDGE, BC_UNCLASSIFIED})

BASIS_REMOTE_NAMESPACE = "REMOTE_ENTITY_NAMESPACE"
BASIS_RF_CONTAINER = "RF_SOURCE_CONTAINER"
BOUNDARY_BASES = frozenset({BASIS_REMOTE_NAMESPACE, BASIS_RF_CONTAINER})

GAP_NO_PRODUCER = "LOCAL_LEAF_WITHOUT_ACQUIRED_PRODUCER"
GAP_OUTSIDE_POOL = "OUTSIDE_POOL"
GAP_NOT_IN_MODELING_GRAPH = "NOT_IN_MODELING_GRAPH"
GAP_DEPTH = "CHAIN_DEPTH_LIMIT"
CHAIN_GAPS = frozenset({GAP_NO_PRODUCER, GAP_OUTSIDE_POOL, GAP_NOT_IN_MODELING_GRAPH, GAP_DEPTH})

_STATE_TO_CLASS = {
    "RAW_PHYSICAL_TABLE": BC_PHYSICAL_TABLE, "CDS_VIEW": BC_GOVERNED_CDS, "SAPI_ODP": BC_GOVERNED_SAPI,
    "STANDARD_VIEW_ON_PHYSICAL_TABLE": BC_STANDARD_VIEW_ON_PHYSICAL, "BW_BRIDGE": BC_BW_BRIDGE,
}
_CDS_CONTAINERS = frozenset({"/CDS_EXTRACTION", "/CDS"})
_MAX_PROVENANCE_DEPTH = 8


def boundary_class_of_source_state(source_state: str) -> str:
    """Clase del borde segun el estado de fuente NEUTRO de ``source.contract`` (familia/namespace del ``remote.entity``). Otro origen => UNCLASSIFIED."""
    return _STATE_TO_CLASS.get(source_state, BC_UNCLASSIFIED)


def boundary_class_of_container(container: str | None) -> str:
    """Clase del borde de un Replication Flow segun el ``container`` declarado del sistema fuente."""
    return BC_GOVERNED_CDS if container in _CDS_CONTAINERS else BC_UNCLASSIFIED


def _remote_boundary(name: str, entity: Any, source_state: str, path: list[str], via: list[str]) -> dict:
    return {"boundary_entity": name, "remote_connection": getattr(entity, "remote_connection", None) or None, "remote_entity": entity.remote_entity,
            "source_state": source_state, "boundary_class": boundary_class_of_source_state(source_state), "basis": BASIS_REMOTE_NAMESPACE,
            "container": None, "path": path, "via_flows": via}


def resolve_physical_boundaries(leaf: str, rec: Mapping[str, Any], ev: Any, entities: Mapping[str, Any], graph: DependencyGraph, closure: Iterable[str],
                                source_state_of) -> dict:
    """Bordes externos revelados por la cadena de procedencia de ``leaf`` (registro ``rec`` de ``resolve_leaf_provenance``) y huecos de la cadena.

    ``source_state_of(remote_entity) -> str`` lo inyecta quien construye el hecho (``facts_source``) para reutilizar el criterio unico sin importarlo
    circularmente. Determinista; no lee nada fuera del grafo y de la evidencia ya cargada."""
    closure_set = set(closure)
    boundaries: list[dict] = []
    gaps: list[dict] = []
    seen_flows: list[str] = []

    def add_gap(entity: str, reason: str, path: list[str]) -> None:
        gaps.append({"entity": entity, "reason": reason, "path": path})

    def walk_entity(src: str, path: list[str], via: list[str], depth: int) -> None:
        """Sigue el origen ``src`` (leido por un flujo) hasta el borde remoto, por el linaje de Modeling del propio ``src``."""
        if depth >= _MAX_PROVENANCE_DEPTH:
            add_gap(src, GAP_DEPTH, path)
            return
        e = entities.get(src)
        if e is None:
            add_gap(src, GAP_OUTSIDE_POOL, path + [src])
            return
        if getattr(e, "remote_entity", ""):
            boundaries.append(_remote_boundary(src, e, source_state_of(e.remote_entity), path + [src], via))
            return
        if not graph.has_node(src):
            add_gap(src, GAP_NOT_IN_MODELING_GRAPH, path + [src])
            return
        provs = graph.providers(src, [EdgeKind.LINEAGE])
        if provs:
            for p in provs:
                if p in path:                                      # ciclo: se corta
                    continue
                walk_entity(p, path + [src], via, depth + 1)
            return
        # hoja local sin proveedores de Modeling: su propio productor (si se adquirio) explica su origen
        targets, _amb = ev.target_flows(src, entities.keys())
        if not targets:
            add_gap(src, GAP_NO_PRODUCER, path + [src])
            return
        for fl, _match in targets:
            walk_flow(src, fl, path + [src], via, depth + 1)

    def walk_flow(target: str, fl: Any, path: list[str], via: list[str], depth: int) -> None:
        if fl.name not in seen_flows:
            seen_flows.append(fl.name)
        flow_via = via + [fl.name]
        if fl.kind == REPLICATION_FLOW:
            for sy in fl.source_systems:
                for obj in fl.source_entities or ("",):
                    boundaries.append({"boundary_entity": None, "remote_connection": sy.connection_id, "remote_entity": obj or None, "source_state": None,
                                       "boundary_class": boundary_class_of_container(sy.container), "basis": BASIS_RF_CONTAINER, "container": sy.container,
                                       "path": path + [f"[{fl.name}]"], "via_flows": flow_via})
        elif fl.kind == DATA_FLOW:
            for s in fl.source_entities:
                walk_entity(s, path + [f"[{fl.name}]"], flow_via, depth)

    # punto de partida: los productores que establecen la procedencia de la hoja
    for p in rec.get("producer_flows") or []:
        fl = next((f for f in ev.flows if f.kind == p["kind"] and f.name == p["name"]), None)
        if fl is not None:
            walk_flow(leaf, fl, [leaf], [], 0)

    # sin duplicados (un mismo borde alcanzado por dos caminos conserva el primero, que es el mas corto por orden de recorrido)
    uniq: dict[tuple, dict] = {}
    for b in boundaries:
        uniq.setdefault((b["boundary_entity"], b["remote_connection"], b["remote_entity"], b["container"]), b)
    out = []
    for b in sorted(uniq.values(), key=lambda b: (str(b["boundary_entity"]), str(b["remote_entity"]), str(b["remote_connection"]))):
        out.append({**b, "plane": "PROVENANCE", "is_semantic_closure_member": bool(b["boundary_entity"] and b["boundary_entity"] in closure_set),
                    "counts_as_direct_raw_table_access": False})
    gaps_out = sorted({(g["entity"], g["reason"], tuple(g["path"])) for g in gaps})
    return {"boundaries": out, "chain_gaps": [{"entity": e, "reason": r, "path": list(p)} for e, r, p in gaps_out],
            "chain_complete": not gaps_out, "flows": sorted(seen_flows)}
