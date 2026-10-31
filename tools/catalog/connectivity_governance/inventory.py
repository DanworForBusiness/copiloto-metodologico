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
    """Estado de EJECUCIÓN de la extracción (¿terminaron bien las llamadas que se hicieron?)."""
    COMPLETE = "COMPLETE"      # todos los espacios esperados accesibles y todas las conexiones extraídas, sin errores
    PARTIAL = "PARTIAL"        # se extrajo algo pero hay huecos/errores
    FAILED = "FAILED"          # preflight falló o no se pudo acceder a nada


class Exhaustiveness(Enum):
    """Exhaustividad COMPROBADA del inventario (¿lo extraído es TODO lo que existe?). Eje SEPARADO del estado
    de ejecución: una ejecución COMPLETE no implica un inventario exhaustivo (ver `compute_exhaustiveness`)."""
    VERIFIED = "VERIFIED"            # la fuente puede probar completitud (cursor/total contrastado, o datos conocidos)
    UNVERIFIABLE = "UNVERIFIABLE"    # todas las llamadas OK pero sin señal verificable de completitud (p. ej. CLI sin cursor/total)
    INCOMPLETE = "INCOMPLETE"        # se sabe que falta algo (espacio inaccesible, listado/ get con error, truncación)


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
    status: InventoryStatus            # EJECUCIÓN (COMPLETE/PARTIAL/FAILED)
    coverage: InventoryCoverage
    connections: tuple = ()            # ObservedConnection[]
    exhaustiveness: Exhaustiveness = Exhaustiveness.INCOMPLETE   # ¿es TODO lo que existe? eje separado del estado
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
            "exhaustiveness": self.exhaustiveness.value,
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


def compute_exhaustiveness(status: InventoryStatus, *, source_guarantees_exhaustive: bool) -> Exhaustiveness:
    """Exhaustividad SEPARADA del estado de ejecución.

    - Ejecución no COMPLETE (PARTIAL/FAILED) ⇒ INCOMPLETE (se sabe que falta algo).
    - Ejecución COMPLETE: VERIFIED solo si la fuente puede PROBAR completitud (cursor/total o datos conocidos);
      si no hay señal verificable (p. ej. el CLI devuelve un array sin cursor/total), UNVERIFIABLE — una
      ejecución correcta NO se convierte automáticamente en inventario exhaustivo.
    """
    if status != InventoryStatus.COMPLETE:
        return Exhaustiveness.INCOMPLETE
    return Exhaustiveness.VERIFIED if source_guarantees_exhaustive else Exhaustiveness.UNVERIFIABLE
