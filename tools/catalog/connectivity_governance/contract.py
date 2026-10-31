"""Registro de conectividad homologada (`connectivity-homologation/v1`, sección 2 del contrato F0).

PURO: estructura + búsquedas deterministas; sin persistencia. En la PoC el contrato se arma en memoria
(provisto por el responsable de gobierno); en producción viviría en un namespace propio, append-only,
con autorización y vigencia — NUNCA mezclado con las ratificaciones del Judgment de modelos.

Principio: la asignación lógica (qué conexiones son la misma familia) y el perfil esperado son SIEMPRE
explícitos y gobernados. El motor jamás los infiere por nombre.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import ObservedConnection


@dataclass(frozen=True)
class ExpectedMember:
    """Un entorno homologado dentro de una familia lógica."""
    environment: str
    related_connections: tuple = ()                 # connection_name(s) que materializan este miembro
    expected_agent: Optional[str] = None            # perfil de conectividad esperado (agente)
    expected_agent_group: Optional[str] = None
    expected_physical: Optional[tuple] = None        # (host, sysid, client) esperado, opcional (evidencia)


@dataclass(frozen=True)
class GovernedException:
    """Desviación AUTORIZADA y vigente (sección 4). `review_by` = fecha ISO de caducidad."""
    environment: str
    connection_name: str
    rationale: str
    approver: str
    review_by: str
    evidence_ref: str = ""


@dataclass(frozen=True)
class LogicalSystem:
    """Una familia SAP gobernada (p. ej. S4_FAMILIA con miembros DEV/QAS/PRD)."""
    logical_system_id: str
    description: str
    members: tuple = ()                 # ExpectedMember
    exceptions: tuple = ()              # GovernedException


@dataclass(frozen=True)
class HomologationContract:
    """Contrato de homologación de conectividad de UN cliente. `as_of` = fecha de evaluación (vigencia)."""
    client: str
    version: str = "connectivity-homologation/v1"
    systems: tuple = ()                 # LogicalSystem
    owner: str = ""
    approved_at: str = ""
    as_of: str = ""                     # fecha contra la que se juzga la vigencia de excepciones

    # ── Búsquedas (todas explícitas; nunca por heurística de nombre) ─────────
    def logical_of(self, conn: ObservedConnection) -> Optional[str]:
        sys = self._system_of(conn)
        return sys.logical_system_id if sys else None

    def expected_for(self, conn: ObservedConnection) -> Optional[ExpectedMember]:
        sys = self._system_of(conn)
        if not sys:
            return None
        for m in sys.members:
            if m.environment == conn.identity.environment and conn.identity.connection_name in m.related_connections:
                return m
        return None

    def active_exception_for(self, conn: ObservedConnection) -> Optional[GovernedException]:
        """Excepción que cubre esta conexión Y está vigente (`review_by` >= `as_of`)."""
        sys = self._system_of(conn)
        if not sys:
            return None
        for exc in sys.exceptions:
            if (exc.environment == conn.identity.environment
                    and exc.connection_name == conn.identity.connection_name
                    and self._is_active(exc)):
                return exc
        return None

    # ── internos ─────────────────────────────────────────────────────────────
    def _system_of(self, conn: ObservedConnection) -> Optional[LogicalSystem]:
        for sys in self.systems:
            for m in sys.members:
                if m.environment == conn.identity.environment and conn.identity.connection_name in m.related_connections:
                    return sys
        return None

    def _is_active(self, exc: GovernedException) -> bool:
        # Sin `as_of` declarado no se puede juzgar vigencia → fail-closed: la excepción NO cuenta como activa.
        if not self.as_of or not exc.review_by:
            return False
        return exc.review_by >= self.as_of


# Contrato vacío de conveniencia: "no hay homologación cargada".
NO_CONTRACT: Optional[HomologationContract] = None
