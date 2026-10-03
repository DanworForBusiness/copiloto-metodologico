"""Utilidades compartidas por los extractores de HECHOS (M2+). Sin reglas de evaluacion.

Disciplina de Fase 1: METADATA -> HECHO NORMALIZADO (nunca metadata -> juicio de calidad). Cada hecho conserva
su clase de evidencia (A auto-observable / B derivable / C1 brecha de roadmap / C2 solo experto / D requiere
contexto), su plano (MODELING / PROCESSING) y referencias de procedencia.

Los valores de los hechos NO usan nombres de artefactos como claves de diccionario (un nombre de cliente podria
chocar con el vocabulario prohibido del perfil): se emiten como listas de registros.
"""
from __future__ import annotations

from typing import Any, Iterable

from .model import (
    Applicability, ApplicabilityDecision, ApplicabilityReason, Area, Assessability, Cause, EvidenceClass,
    EvidenceRef, Fact, Plane,
)

APPLICABLE = ApplicabilityDecision(Applicability.APPLICABLE)


def not_applicable(reason: ApplicabilityReason) -> ApplicabilityDecision:
    return ApplicabilityDecision(Applicability.NOT_APPLICABLE, reason)


def make_fact(
    fact_id: str,
    area: Area,
    subject: str,
    value: Any,
    evidence_class: EvidenceClass,
    *,
    refs: Iterable[EvidenceRef] = (),
    plane: Plane = Plane.MODELING,
    applicability: ApplicabilityDecision = APPLICABLE,
    assessability: Assessability = Assessability.ASSESSED,
    cause: Cause | None = None,
) -> Fact:
    return Fact(fact_id, area, subject, value, evidence_class, plane, tuple(refs), applicability, assessability, cause)


def entity_refs(
    entity: Any, attrs: Iterable[str], *, source_file: str | None = None, plane: Plane = Plane.MODELING,
) -> tuple[EvidenceRef, ...]:
    """Referencias de procedencia a atributos de una entidad del loader (archivo de origen si se conoce)."""
    name = str(getattr(entity, "name", ""))
    sf = source_file or getattr(entity, "file", None) or None
    return tuple(EvidenceRef(name, a, sf, plane) for a in attrs)


def sort_facts(facts: Iterable[Fact]) -> tuple[Fact, ...]:
    return tuple(sorted(facts, key=lambda f: (f.area.value, f.fact_id, f.subject)))


def records(items: Iterable[dict]) -> list[dict]:
    """Lista de registros estable (se conserva el orden recibido, que ya es determinista)."""
    return [dict(i) for i in items]
