"""Modelo de datos del motor de gobierno de conectividad (contrato `connectivity-governance/v1`, F1 PoC).

PURO: sólo dataclasses y enums; sin I/O, sin red, sin persistencia. Ver docs/CONNECTIVITY_GOVERNANCE_F0.md.

Separa deliberadamente TRES identidades (sección 1 del contrato) y TRES ejes ortogonales de evaluación
(sección 3): la conformidad, el estado operativo y la cobertura de evidencia se concluyen por separado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Ejes ortogonales de evaluación (sección 3 del contrato) ──────────────────
class Conformance(Enum):
    """Eje 3.1 — conformidad arquitectónica contra el contrato."""
    CONFORME = "CONFORME"
    DIVERGENTE = "DIVERGENTE"          # divergencia observada, pendiente de revisión (sin contrato)
    NO_CONFORME = "NO_CONFORME"        # contradice un perfil homologado ACTIVE
    NO_EVALUABLE = "NO_EVALUABLE"      # sin base para juzgar (sin contrato y sin divergencia observable)


class Operational(Enum):
    """Eje 3.2 — estado operativo observado (punto en el tiempo)."""
    CONECTADO = "CONECTADO"
    DESCONECTADO = "DESCONECTADO"
    DESCONOCIDO = "DESCONOCIDO"


class Coverage(Enum):
    """Eje 3.3 — cobertura y confianza de la evidencia."""
    COMPLETA = "COMPLETA"
    PARCIAL = "PARCIAL"
    INSUFICIENTE = "INSUFICIENTE"


# ── Las TRES identidades (sección 1 del contrato) ────────────────────────────
@dataclass(frozen=True)
class ConnectionIdentity:
    """1.1 — identidad de conexión (técnica, inequívoca).

    `tenant` es la identidad técnica ESTABLE (CUID/host del tenant Datasphere). `environment`
    (DEV/QAS/PRD) es un ATRIBUTO GOBERNADO, NO un identificador técnico: lo provee la gobernanza
    (mapa space→environment del registro), nunca se deriva del nombre del espacio.
    """
    tenant: str
    environment: str
    space: str
    connection_name: str
    type_id: Optional[str]

    @property
    def key(self) -> tuple:
        return (self.tenant, self.environment, self.space, self.connection_name, self.type_id)


@dataclass(frozen=True)
class PhysicalSystemIdentity:
    """1.2 — identidad del sistema físico (observable). Es EVIDENCIA, no la clave de equivalencia."""
    host: Optional[str] = None
    sysid: Optional[str] = None
    systemnumber: Optional[str] = None
    client: Optional[str] = None

    @property
    def observable(self) -> bool:
        # Destino observable = host + sysid presentes (un JDBC genérico sin host no aporta destino).
        return bool(self.host and self.sysid)

    @property
    def key(self) -> tuple:
        return (self.host or "", self.sysid or "", self.systemnumber or "", self.client or "")


@dataclass(frozen=True)
class AgentObservation:
    """Perfil de conectividad OBSERVADO (agente/grupo/ubicación/estado)."""
    agent_name: Optional[str] = None
    agent_group: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None       # agentStatus crudo del export

    @property
    def profile(self) -> tuple:
        """Firma del perfil de conectividad observado (lo que se compara entre entornos)."""
        return (self.agent_name or "", self.agent_group or "")


@dataclass(frozen=True)
class ObservedConnection:
    """Una conexión observada en un snapshot (read-only). No contiene juicio: sólo lo observado."""
    identity: ConnectionIdentity
    physical: PhysicalSystemIdentity
    agent: AgentObservation
    adapter: Optional[str] = None
    credential_mode: Optional[str] = None


# ── Resultado de la evaluación ───────────────────────────────────────────────
@dataclass(frozen=True)
class Divergence:
    """Una divergencia OBSERVADA entre conexiones del mismo sistema físico (señal, no incumplimiento)."""
    physical_key: tuple
    environments: tuple
    connection_names: tuple
    kind: str              # p. ej. "agent"
    detail: str


@dataclass(frozen=True)
class ConnectivityVerdict:
    """Veredicto por conexión: los tres ejes se reportan POR SEPARADO, sin score global."""
    connection_key: tuple
    conformance: Conformance
    operational: Operational
    evidence: Coverage
    logical_system_id: Optional[str]
    divergence: Optional[Divergence]
    governed_exception: Optional[str]          # id/rationale de la excepción ACTIVE que la cubre, si existe
    rationale: dict = field(default_factory=dict)   # rationale por eje
    labels: tuple = ()                          # etiquetas de PRESENTACIÓN derivadas (sección 3.4)
