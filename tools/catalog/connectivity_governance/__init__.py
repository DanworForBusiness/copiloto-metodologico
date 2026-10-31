"""Gobierno de conectividad — motor PURO `connectivity-rules/v1` (PoC F1).

Contrato de diseño: docs/CONNECTIVITY_GOVERNANCE_F0.md (`connectivity-governance/v1`, aprobado 2026-10-30).
PoC read-only sobre snapshots existentes: sin OAuth productivo, sin persistencia, sin UI.
"""
from __future__ import annotations

from .contract import (
    ExpectedMember,
    GovernedException,
    HomologationContract,
    LogicalSystem,
)
from .engine import compare_configurations, evaluate, evaluate_all
from .model import (
    AgentObservation,
    ConnectionIdentity,
    ConnectivityVerdict,
    Conformance,
    Coverage,
    Divergence,
    ObservedConnection,
    Operational,
    PhysicalSystemIdentity,
)
from .snapshot import from_snapshot, is_connection_doc

# F2.1 — extracción read-only + inventario normalizado (importables sin el CLI; la fuente real lo carga perezoso)
from .inventory import (
    ConnectivityInventory,
    ExtractionError,
    InventoryCoverage,
    InventoryStatus,
    compute_status,
)
from .extraction import (
    ConnectionSource,
    RetryPolicy,
    SourceError,
    extract_inventory,
    sanitize,
    with_retry,
)
from .sources import CliConnectionSource, SimulatedConnectionSource, rate_limited, timed_out

__all__ = [
    "compare_configurations", "evaluate", "evaluate_all",
    "from_snapshot", "is_connection_doc",
    "HomologationContract", "LogicalSystem", "ExpectedMember", "GovernedException",
    "ObservedConnection", "ConnectionIdentity", "PhysicalSystemIdentity", "AgentObservation",
    "ConnectivityVerdict", "Conformance", "Operational", "Coverage", "Divergence",
    # F2.1
    "ConnectivityInventory", "InventoryCoverage", "InventoryStatus", "ExtractionError", "compute_status",
    "extract_inventory", "ConnectionSource", "SourceError", "RetryPolicy", "with_retry", "sanitize",
    "SimulatedConnectionSource", "CliConnectionSource", "rate_limited", "timed_out",
]
