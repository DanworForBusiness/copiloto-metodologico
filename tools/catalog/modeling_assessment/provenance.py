"""Procedencia canonica de HOJAS LOCALES (G1): ¿como se produjo la hoja? Solo HECHOS, sin juicio de calidad.

Una hoja de frontera sin ``remote_entity`` (tabla local) no declara su origen en el export de Modelado. Su procedencia puede quedar ESTABLECIDA por
evidencia DIRECTA de un productor del plano de Procesamiento (Replication Flow / Data Flow cuyo DESTINO es la hoja), DECLARADA por el propio
artefacto (dataset externo / table function), o ser NOT_OBSERVABLE. La evidencia de Procesamiento satisface SOLO esta superficie de procedencia/
trazabilidad: no satisface ningun otro hecho de Modelado.

Semantica (decision del owner, Gate 4):
  provenance_state
    ESTABLISHED     un productor (RF/DF) con destino la hoja establece COMO se produjo y (RF) desde que sistema/objeto fuente;
    DECLARED        el artefacto declara su origen (dataset externo / table function): se registra tal cual, NO se promueve a ESTABLISHED;
    NOT_OBSERVABLE  ningun productor ni declaracion, y el alcance adquirido es CONCLUYENTE para afirmar esa ausencia (resultado EVALUADO);
    None            no se puede afirmar nada: alcance no declarado/incompleto o evidencia de productor no interpretable => NOT_ASSESSED.
  La ausencia de origen NUNCA se asume establecida: sin alcance concluyente el resultado es NOT_ASSESSED, no "NOT_OBSERVABLE".
  Sin inferencia por nombre: una hoja solo gana procedencia si un flujo la declara como DESTINO (con la unica regla explicita y acotada de
  captura de cambios ``X_Delta`` -> ``X`` de los Replication Flows; el tipo de coincidencia queda registrado en ``target_match``).
  Los Task Chains orquestan y NO son procedencia; los Transformation Flows no se interpretan (sin evidencia forense de una relacion confiable).
"""
from __future__ import annotations

from typing import Any, Mapping

from .readers import DATA_FLOW, DELTA_SHADOW_SUFFIX, REPLICATION_FLOW, SupplementaryEvidence

PROVENANCE_VERSION = "leaf-provenance/v1"

PROV_ESTABLISHED, PROV_DECLARED, PROV_NOT_OBSERVABLE = "ESTABLISHED", "DECLARED", "NOT_OBSERVABLE"
BASIS_REPLICATION_FLOW, BASIS_DATA_FLOW = "REPLICATION_FLOW", "DATA_FLOW"
BASIS_DECLARED_DATASET, BASIS_TABLE_FUNCTION, BASIS_NONE = "DECLARED_DATASET", "TABLE_FUNCTION", "NONE"

ASSESSED, NOT_ASSESSED = "ASSESSED", "NOT_ASSESSED"
CAUSE_NOT_IN_SNAPSHOT, CAUSE_NOT_PARSED = "NOT_IN_SNAPSHOT", "C1_NOT_YET_PARSED"

UP_REMOTE_TABLE, UP_LOCAL_TABLE, UP_DATASPHERE_VIEW, UP_OUTSIDE_POOL, UP_CYCLE = "REMOTE_TABLE", "LOCAL_TABLE", "DATASPHERE_VIEW", "OUTSIDE_POOL", "CYCLE"
_MAX_DEPTH = 6


def _target_match(entity: str, targets: tuple[str, ...]) -> str:
    return "EXACT" if entity in targets else "DELTA_SHADOW_SUFFIX" if f"{entity}{DELTA_SHADOW_SUFFIX}" in targets else "EXACT"


def _result(state: str | None, basis: str, status: str, cause: str | None, scope: dict, **extra: Any) -> dict:
    rec: dict[str, Any] = {"provenance_state": state, "basis": basis, "producer_flows": [], "origin": None, "upstream": [], "chain_complete": None,
                           "declaration": None, "scope": scope, "status": status, "cause": cause}
    rec.update(extra)
    return rec


def _is_view(name: str, ev: SupplementaryEvidence) -> bool:
    d = ev.derivation_evidence(name)
    return bool(d is not None and d.has_query)


def _resolve_upstream(source: str, ev: SupplementaryEvidence, entities: Mapping[str, Any], depth: int, seen: tuple[str, ...]) -> dict:
    if source in seen or depth >= _MAX_DEPTH:
        return {"entity": source, "kind": UP_CYCLE, "provenance_state": None, "basis": None, "remote_entity": None, "chain_complete": False}
    ent = entities.get(source)
    if ent is None:
        return {"entity": source, "kind": UP_OUTSIDE_POOL, "provenance_state": None, "basis": None, "remote_entity": None, "chain_complete": False}
    remote = getattr(ent, "remote_entity", "") or None
    if remote:
        return {"entity": source, "kind": UP_REMOTE_TABLE, "provenance_state": None, "basis": None, "remote_entity": remote, "chain_complete": True}
    if _is_view(source, ev):
        return {"entity": source, "kind": UP_DATASPHERE_VIEW, "provenance_state": None, "basis": None, "remote_entity": None, "chain_complete": False}
    sub = resolve_leaf_provenance(source, ev, entities, _depth=depth + 1, _seen=seen + (source,))
    ok = sub["provenance_state"] in (PROV_ESTABLISHED, PROV_DECLARED) and sub["chain_complete"] is not False        # una cadena es completa si TODA la cadena aguas arriba lo es
    return {"entity": source, "kind": UP_LOCAL_TABLE, "provenance_state": sub["provenance_state"], "basis": sub["basis"], "remote_entity": None, "chain_complete": ok}


def resolve_leaf_provenance(name: str, ev: SupplementaryEvidence, entities: Mapping[str, Any], *, _depth: int = 0, _seen: tuple[str, ...] = ()) -> dict:
    """Registro JSON de procedencia de la hoja local ``name`` (ver el docstring del modulo). Determinista; solo evidencia declarada."""
    scope = ev.provenance_scope
    targets = [(fl, role) for fl, role in ev.flows_touching(name) if role == "target"]
    rfs = [fl for fl, _ in targets if fl.kind == REPLICATION_FLOW]
    dfs = [fl for fl, _ in targets if fl.kind == DATA_FLOW]
    producers = [{"kind": fl.kind, "name": fl.name, "role": "target", "target_match": _target_match(name, fl.target_entities),
                  "load_types": list(fl.load_types), "source_file": fl.source_file} for fl, _ in targets]

    complete_rfs = [fl for fl in rfs if fl.source_systems and fl.source_entities]
    if complete_rfs:
        fl = complete_rfs[0]
        origin = {"systems": [{"connection_id": s.connection_id, "connection_type": s.connection_type, "container": s.container} for s in fl.source_systems],
                  "source_objects": list(fl.source_entities)}
        return _result(PROV_ESTABLISHED, BASIS_REPLICATION_FLOW, ASSESSED, None, scope, producer_flows=producers, origin=origin, chain_complete=True)
    complete_dfs = [fl for fl in dfs if fl.source_entities]
    if complete_dfs:
        fl = complete_dfs[0]
        up = [_resolve_upstream(s, ev, entities, _depth, _seen + (name,)) for s in fl.source_entities]
        complete = all(u["chain_complete"] for u in up)
        return _result(PROV_ESTABLISHED, BASIS_DATA_FLOW, ASSESSED, None, scope, producer_flows=producers, upstream=up, chain_complete=complete)
    if targets:                                       # un productor declara la hoja como destino pero no expone entradas interpretables
        return _result(None, BASIS_NONE, NOT_ASSESSED, CAUSE_NOT_PARSED, scope, producer_flows=producers)
    decl = ev.local_declaration(name)
    if decl is not None:
        basis = BASIS_DECLARED_DATASET if decl.kind == "DECLARED_DATASET" else BASIS_TABLE_FUNCTION
        return _result(PROV_DECLARED, basis, ASSESSED, None, scope,
                       declaration={"kind": decl.kind, "data_source_schema": decl.data_source_schema, "data_source_entity": decl.data_source_entity,
                                    "source_file": decl.source_file})
    if scope["conclusive"]:
        return _result(PROV_NOT_OBSERVABLE, BASIS_NONE, ASSESSED, None, scope)
    return _result(None, BASIS_NONE, NOT_ASSESSED, CAUSE_NOT_IN_SNAPSHOT, scope)
