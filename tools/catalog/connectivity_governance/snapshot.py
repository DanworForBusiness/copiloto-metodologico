"""Parseo de un snapshot de conexión a `ObservedConnection` (read-only, PoC F1).

PURO sobre un dict ya cargado (el llamador lee el archivo). Reconoce la forma real de los exports del
cache (`catalog_local/.csn_cache/<cliente>/Conexiones/conn_space_*.json`): sólo EXTRAE lo observado,
no juzga nada.

`tenant` y `environment` NO salen del archivo: son atributos GOBERNADOS que provee el llamador (el mapa
space→environment del registro). Así el motor nunca deriva `environment` del nombre del espacio.
"""
from __future__ import annotations

from typing import Optional

from .model import AgentObservation, ConnectionIdentity, ObservedConnection, PhysicalSystemIdentity


def _clean(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def from_snapshot(doc: dict, *, tenant: str, environment: str) -> ObservedConnection:
    """Construye una `ObservedConnection` desde el dict de un `conn_space_<SPACE>_<NAME>.json`."""
    name = doc.get("name")
    space = doc.get("space_name")

    ci: dict = {}
    try:
        ci = doc["content"]["objects"][name]["configuration"]["ConnectionProperties"]["ConnectionInfo"] or {}
    except (KeyError, TypeError):
        ci = {}

    physical = PhysicalSystemIdentity(
        host=_clean(ci.get("host")),
        sysid=_clean(ci.get("sysid")),
        systemnumber=_clean(ci.get("systemnumber")),
        client=_clean(ci.get("client")),
    )
    agent = AgentObservation(
        agent_name=_clean(doc.get("agentName")),
        agent_group=_clean(doc.get("agentGroupName")),
        location=_clean(doc.get("location")),
        status=_clean(doc.get("agentStatus")),
    )
    identity = ConnectionIdentity(
        tenant=tenant,
        environment=environment,
        space=space,
        connection_name=name,
        type_id=_clean(doc.get("typeId")),
    )
    return ObservedConnection(
        identity=identity,
        physical=physical,
        agent=agent,
        adapter=_clean(doc.get("adapter")),
        credential_mode=_clean(doc.get("credentialMode")),
    )


def is_connection_doc(doc) -> bool:
    """True si el dict es un export de UNA conexión (tiene `name`), no el agregado de espacio (una lista)."""
    return isinstance(doc, dict) and bool(doc.get("name"))
