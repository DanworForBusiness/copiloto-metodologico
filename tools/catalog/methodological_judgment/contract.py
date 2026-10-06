"""Contrato canonico del Juicio metodologico v1 (constantes, taxonomias, firma). Sin logica de reglas.

Principios (Gate 8C.1 / 8D.1): determinista, NO numerico (sin puntajes, pesos ni porcentajes de calidad), basado en evidencia, puro (sin escrituras, red, LLM ni reloj),
sin veredicto legacy como insumo. PLENA esta RESERVADA en el esquema pero v1 NUNCA la emite.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

JUDGMENT_SCHEMA_VERSION = "judgment/v1"
RULESET_VERSION = "judgment-rules/v1"
EXECUTIVE_INPUT_VERSION = "executive-input/v1"
EXECUTIVE_OUTPUT_CONTRACT_VERSION = "executive-output-contract/v1"
CTA_PLAYBOOK_VERSION = "cta-playbook/v1"

# estado / nivel / cierre
DETERMINED, NOT_DETERMINABLE = "DETERMINED", "NOT_DETERMINABLE"
BUENA, ADECUADA, PARCIAL = "BUENA", "ADECUADA", "PARCIAL"
PLENA = "PLENA"                                                    # reservada en el esquema; v1 NO la emite
LEVELS = (BUENA, ADECUADA, PARCIAL)
RESERVED_LEVELS = (PLENA,)
FINAL, PROVISIONAL = "FINAL", "PROVISIONAL"
PLENA_ASSIGNMENT = "DISABLED_UNTIL_CALIBRATED"
PLENA_STATE = "POSITIVE_CONTROL_UNCALIBRATED"

# clases de razon
POSITIVE_EVIDENCE, LOCAL_WEAKNESS, RELEVANT_WEAKNESS, MATERIAL_DEVIATION, QUALIFIER_ONLY = (
    "POSITIVE_EVIDENCE", "LOCAL_WEAKNESS", "RELEVANT_WEAKNESS", "MATERIAL_DEVIATION", "QUALIFIER_ONLY")
REASON_CLASSES = (POSITIVE_EVIDENCE, LOCAL_WEAKNESS, RELEVANT_WEAKNESS, MATERIAL_DEVIATION, QUALIFIER_ONLY)

# dimensiones
SOURCE_INGESTION, HARMONIZATION, PROPAGATION, PUBLISHED_CONTRACT = "SOURCE_INGESTION", "HARMONIZATION", "PROPAGATION", "PUBLISHED_CONTRACT"
GOVERNANCE, MAINTAINABILITY, PRODUCT_FOCUS = "GOVERNANCE", "MAINTAINABILITY", "PRODUCT_FOCUS"
CRITICAL_DIMENSIONS = (SOURCE_INGESTION, HARMONIZATION, PROPAGATION, PUBLISHED_CONTRACT)
DIMENSIONS = (*CRITICAL_DIMENSIONS, GOVERNANCE, MAINTAINABILITY, PRODUCT_FOCUS)

# area de Assessment que respalda cada contrato critico (compuerta de determinabilidad)
CONTRACT_AREAS = {
    SOURCE_INGESTION: ("SOURCE_EXTRACTION", "INGESTION"),
    HARMONIZATION: ("HARMONIZATION",),
    PROPAGATION: ("PROPAGATION",),
    PUBLISHED_CONTRACT: ("ANALYTIC_MODEL",),
}
EVALUABLE_AREA_STATES = ("ASSESSED", "PARTIALLY_ASSESSED")

# estados de razon (resultado de la interpretacion; NO alteran la clase determinista)
OPEN_CANDIDATE = "OPEN_CANDIDATE"
CONFIRMED_DEVIATION, CONFIRMED_WEAKNESS = "CONFIRMED_DEVIATION", "CONFIRMED_WEAKNESS"
ACCEPTED_AS_DESIGNED, GOVERNED_EXCEPTION, NOT_APPLICABLE, NEEDS_CONTEXT = "ACCEPTED_AS_DESIGNED", "GOVERNED_EXCEPTION", "NOT_APPLICABLE", "NEEDS_CONTEXT"
DISPOSITION_DECISIONS = (CONFIRMED_DEVIATION, CONFIRMED_WEAKNESS, ACCEPTED_AS_DESIGNED, GOVERNED_EXCEPTION, NOT_APPLICABLE, NEEDS_CONTEXT)
REMOVING_STATES = (GOVERNED_EXCEPTION, NOT_APPLICABLE)             # unicos estados que sacan una razon de la determinacion del nivel
UNRESOLVED_STATES = (OPEN_CANDIDATE, NEEDS_CONTEXT)

# evaluabilidad de una regla
EVALUATED, PARTIAL, NOT_EVALUATED = "EVALUATED", "PARTIAL", "NOT_EVALUATED"
DISABLED_PENDING_EVIDENCE = "DISABLED_PENDING_EVIDENCE"

# calificadores
DESIGN_COHERENCE_VALUES = ("SOLID", "OPPORTUNITIES", "WEAK", "NOT_DETERMINABLE")
PRODUCT_FOCUS_VALUES = ("FOCUSED", "GENERIC", "PLACEHOLDER_LIKE", "NOT_DETERMINABLE")
NAMING_STATES = ("NONE", "MINOR_NAMING_DEVIATION", "PARTIAL_DIVERGENCE", "SYSTEMATIC_GOVERNANCE_DIVERGENCE", "NAMING_PLUS_ROLE_AMBIGUITY", "NOT_DETERMINABLE")

# clases de condicion de aplicacion de una CTA
CTA_PRIORITIES = ("MATERIAL", "RELEVANT", "LOCAL", "MAINTAINABILITY", "REVIEW", "EVIDENCE")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_default)


def _default(o: Any):
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(o) if not isinstance(o, tuple) else list(o)
    raise TypeError(f"no serializable: {type(o).__name__}")


def signature(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
