"""Inventario técnico normalizado de conectividad (`connectivity-inventory/v1`) — F2.1.

PLANO OBSERVADO: solo lo que se extrajo del tenant (read-only). NO contiene homologación ni juicio
gobernado (eso vive aparte, en `connectivity-homologation/v1`). Ver _handoffs/CONNECTIVITY_GOVERNANCE_F2_DESIGN.md.

Declara COBERTURA explícita (espacios esperados/accesibles, conexiones descubiertas/extraídas, errores) y un
ESTADO honesto: una extracción incompleta es PARCIAL o FALLIDA, nunca «inventario completo».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .model import ObservedConnection

VERSION = "connectivity-inventory/v1"


class InventoryStatus(Enum):
    COMPLETE = "COMPLETE"      # todos los espacios esperados accesibles y todas las conexiones extraídas, sin errores
    PARTIAL = "PARTIAL"        # se extrajo algo pero hay huecos/errores
    FAILED = "FAILED"          # preflight falló o no se pudo acceder a nada


@dataclass(frozen=True)
class ExtractionError:
    """Un error de extracción, ya SANITIZADO (sin secretos/tokens/respuestas crudas)."""
    scope: str                 # "preflight" | "spaces" | "space:<name>" | "connection:<space>/<name>"
    code: str                  # OAUTH_FAILED | SCOPE_DENIED | RATE_LIMITED | TIMEOUT | SPACE_INACCESSIBLE | GET_FAILED | ENVIRONMENT_UNGOVERNED | ...
    message: str               # mensaje legible, sanitizado
    retriable: bool = False


@dataclass(frozen=True)
class InventoryCoverage:
    """Cobertura explícita de la extracción (condición 1 del owner)."""
    expected_spaces: tuple = ()        # espacios que se pidió recorrer
    accessible_spaces: tuple = ()      # respondieron `connections list` OK
    inaccessible_spaces: tuple = ()    # no se pudieron listar
    discovered_connections: int = 0    # cuántas reportó `connections list` (en espacios accesibles)
    extracted_connections: int = 0     # cuántas se obtuvieron (`get`) y parsearon OK
    errors: tuple = ()                 # ExtractionError[]


@dataclass(frozen=True)
class ConnectivityInventory:
    """Snapshot de inventario observado. Identidad estable por `snapshot_id`; se serializa con `to_dict`."""
    client: str
    snapshot_id: str
    captured_at: str                   # ISO-8601 UTC
    tenant_id: str                     # identidad técnica estable (CUID/host contrastado por OAuth)
    tenant_host: str
    status: InventoryStatus
    coverage: InventoryCoverage
    connections: tuple = ()            # ObservedConnection[]
    version: str = VERSION

    def to_dict(self) -> dict:
        def conn_dict(c: ObservedConnection) -> dict:
            return {
                "identity": {
                    "tenant": c.identity.tenant, "environment": c.identity.environment,
                    "space": c.identity.space, "connection_name": c.identity.connection_name,
                    "type_id": c.identity.type_id,
                },
                "physical_system": {
                    "host": c.physical.host, "sysid": c.physical.sysid,
                    "systemnumber": c.physical.systemnumber, "client": c.physical.client,
                    "observable": c.physical.observable,
                },
                "agent_observed": {
                    "agent_name": c.agent.agent_name, "agent_group": c.agent.agent_group,
                    "location": c.agent.location, "status": c.agent.status,
                },
                "adapter": c.adapter, "credential_mode": c.credential_mode,
            }
        return {
            "version": self.version, "client": self.client, "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "tenant": {"tenant_id": self.tenant_id, "host": self.tenant_host},
            "status": self.status.value,
            "coverage": {
                "expected_spaces": list(self.coverage.expected_spaces),
                "accessible_spaces": list(self.coverage.accessible_spaces),
                "inaccessible_spaces": list(self.coverage.inaccessible_spaces),
                "discovered_connections": self.coverage.discovered_connections,
                "extracted_connections": self.coverage.extracted_connections,
                "errors": [{"scope": e.scope, "code": e.code, "message": e.message, "retriable": e.retriable}
                           for e in self.coverage.errors],
            },
            "connections": [conn_dict(c) for c in self.connections],
        }


def compute_status(coverage: InventoryCoverage, *, preflight_ok: bool, had_targets: bool) -> InventoryStatus:
    """Regla honesta de estado (condición 3): COMPLETE solo si todo salió; si no, PARTIAL o FAILED."""
    if not preflight_ok:
        return InventoryStatus.FAILED
    if not had_targets:
        return InventoryStatus.FAILED
    # Fracaso total: ningún espacio accesible y nada extraído.
    if not coverage.accessible_spaces and coverage.extracted_connections == 0:
        return InventoryStatus.FAILED
    complete = (
        not coverage.inaccessible_spaces
        and not coverage.errors
        and set(coverage.accessible_spaces) == set(coverage.expected_spaces)
        and coverage.extracted_connections == coverage.discovered_connections
    )
    return InventoryStatus.COMPLETE if complete else InventoryStatus.PARTIAL
