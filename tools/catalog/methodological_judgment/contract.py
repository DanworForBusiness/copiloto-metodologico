"""Contrato canonico del Juicio metodologico v1 (constantes, taxonomias, firma). Sin logica de reglas.

Principios (Gate 8C.1 / 8D.1): determinista, NO numerico (sin puntajes, pesos ni porcentajes de calidad), basado en evidencia, puro (sin escrituras, red, LLM ni reloj),
sin veredicto legacy como insumo. PLENA esta RESERVADA en el esquema pero v1 NUNCA la emite.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

JUDGMENT_SCHEMA_VERSION = "judgment/v1"
RULESET_V2 = "judgment-rules/v2"
RULESET_V3 = "judgment-rules/v3"
RULESET_V4 = "judgment-rules/v4"
RULESETS = (RULESET_V2, RULESET_V3, RULESET_V4)
RULESET_VERSION = RULESET_V2                           # ruleset POR DEFECTO del motor: judgment-rules/v2 sigue siendo reproducible e inalterado para toda llamada que no pida v3 explicitamente
# judgment-rules/v3 = judgment-rules/v2 + UNA sola politica adicional: una ratificacion de gobierno vigente (governed-decision/v1, inyectada como ENTRADA explicita; el motor jamas la lee) resuelve
# unicamente la indeterminacion contractual decision-critica de LVL-05 (variante GOVERNED_CONTRACT_RATIFIED, alcance conservador). Sin ratificaciones inyectadas v3 produce los mismos niveles que v2.
# CURRENT_RULESET_VERSION es el contrato de Juicio VIGENTE para el Juicio PERSISTIDO (lo adopta el servicio de Assessment); no cambia el defecto del motor.
# judgment-rules/v4 = v3 + resolucion de nivel por ESTADO DE DIMENSION para los casos que v3 deja en LVL-06 (politica transversal; ver rules.dimension_resolution_v4) + T5 (tabla local de origen fisico
# comprobado = SRC-LOC-01, debilidad LOCAL) + refinamiento de la causa de ND (candidato de armonizacion UNKNOWN => evidencia insuficiente). No agrega fortalezas ni otras razones; nunca BUENA ni PARCIAL por resolucion.
CURRENT_RULESET_VERSION = RULESET_V4
DECISION_INPUTS_VERSION = "judgment-decision-inputs/v1"
# Variantes EXPERIMENTALES (T4/T5) del MISMO motor: opt-in, solo para medir impacto. Vacio (la politica vigente) = salida byte a byte identica a judgment-rules/v2. Adoptar una variante como
# regla definitiva exige subir RULESET_VERSION (=> JDG_VERSION_STALE en los Juicios persistidos; migracion explicita ``--backfill-judgment``, sin tocar el Assessment).
VARIANT_T4_HAR = "T4_HAR"                      # HAR-STR-01 reformulada: reconoce proyecciones tecnicas reutilizables 1:1 de contratos gobernados que preservan el vocabulario por ETIQUETA
VARIANT_T5_LOCAL_PHYSICAL = "T5_LOCAL_PHYSICAL"    # SRC-LOC-01 tambien para tablas locales cuya procedencia comprobada (Data Flow) es una tabla fisica ABAP
VARIANT_LEVEL_RESOLUTION = "LEVEL_RESOLUTION"   # EXPLORATORIA: resuelve el nivel de los LVL-06 con estados explicitos por dimension (fortaleza / responsabilidad cumplida / deficiencia / no evaluable / no aplicable)
# MODIFICADOR (no una regla): T5_LOCAL_PHYSICAL y LEVEL_RESOLUTION leen la materializacion OBSERVADA de las hojas locales (``leaf-materialization/v1``, derivada del perfil persistido) en vez de
# sus condiciones ad hoc. Sin T5/LEVEL no tiene efecto. Solo con el perfil (sin CSN) las hojas cuya relacion no se puede verificar quedan UNKNOWN y NO aportan evidencia positiva.
VARIANT_MATERIALIZED_INPUT = "MATERIALIZED_INPUT"
# B5 (SIMULACION, opt-in): una ratificacion de gobierno vigente (governed-decision/v1, resuelta FUERA del motor) resuelve UNICAMENTE la indeterminacion contractual decision-critica de LVL-05 de una hoja
# EXTERNAL_CONTRACT_UNDETERMINED. NO la convierte en gobernada para las demas reglas ni otorga fortalezas (SRC-STR-01, PUB-STR-01...). Vacio = salida byte a byte identica a judgment-rules/v2.
VARIANT_GOVERNED_CONTRACT_RATIFIED = "GOVERNED_CONTRACT_RATIFIED"
DISPOSITION_GOVERNED_CONTRACT_RATIFIED = "GOVERNED_CONTRACT_RATIFIED"
RATIFICATION_SCOPE = "LVL-05_SOURCE_CONTRACT_CLASS_ONLY"
VARIANTS = frozenset({VARIANT_T4_HAR, VARIANT_T5_LOCAL_PHYSICAL, VARIANT_LEVEL_RESOLUTION, VARIANT_MATERIALIZED_INPUT, VARIANT_GOVERNED_CONTRACT_RATIFIED})

# Estados por dimension (solo con la variante LEVEL_RESOLUTION). «No evaluable» (falta evidencia) y «No aplicable» (justificado por la arquitectura observada) NO son equivalentes; la ausencia de
# fortaleza no es deficiencia; «cumplida» exige EVIDENCIA POSITIVA, nunca la mera ausencia de debilidades.
DS_STRENGTH, DS_MET, DS_DEFICIENCY, DS_NOT_EVALUABLE, DS_NOT_APPLICABLE, DS_UNRESOLVED = (
    "STRENGTH", "RESPONSIBILITY_MET", "DEFICIENCY_DEMONSTRATED", "NOT_EVALUABLE", "NOT_APPLICABLE", "UNRESOLVED_NO_POSITIVE_EVIDENCE")
LEVEL_RULE_BUENA_BY_RESOLUTION, LEVEL_RULE_ADECUADA_BY_RESOLUTION = "LVL-07", "LVL-08"
BUENA_MIN_STRENGTH_DIMENSIONS = 3                # hipotesis a contrastar: fortalezas en >= 3 dimensiones; el resto, cumplida o no aplicable
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
