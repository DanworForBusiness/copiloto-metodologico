"""Contrato de datos del Modeling Self-Assessment v2 — FASE 1 (solo perfil).

Este modulo define SOLO el contrato: taxonomias (enums), dataclasses inmutables y
serializacion canonica. No contiene reglas de extraccion ni de evaluacion.

LIMITE DE PRODUCTO (decision del owner, Fase 1):
  El perfil contiene HECHOS, SENALES, OBSERVACIONES, REVIEW REQUIRED,
  ASSESSABILITY, COBERTURA ESTRICTA, COBERTURA INCLUSIVA y CONFIANZA.
  NO contiene scores, bandas, pesos, umbrales, penalidades, ni veredictos
  (ni los del evaluador legacy). Esa exclusion se hace cumplir en construccion
  (ver `find_forbidden_tokens`) y la verifican los tests de esquema.

Aplicabilidad PRIMERO, assessability DESPUES: un hecho/area primero es o no
APLICABLE; solo si es aplicable puede ser ASSESSED / PARTIALLY_ASSESSED /
NOT_ASSESSED / INDETERMINATE. NOT_APPLICABLE sale del denominador de cobertura;
NOT_ASSESSED lo reduce. Cobertura baja NO es madurez baja.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any

SCHEMA_VERSION = "modeling_assessment.profile/v1"
RULESET_VERSION = "phase1-draft"


# --------------------------------------------------------------------------
# Vocabulario prohibido (el perfil de Fase 1 NO puede cargar estos conceptos)
# --------------------------------------------------------------------------
FORBIDDEN_TOKENS = frozenset({
    "score", "scores", "scoring", "scored",
    "band", "bands",
    "weight", "weights", "weighted", "weighting",
    "threshold", "thresholds",
    "penalty", "penalties", "penalize", "penalise",
    "bonus", "bonuses",
    "rating", "grade",
    "maturity",
    "verdict", "veredicto", "apto",
    "pleno", "bueno", "parcial", "plena", "buena",
    "legacy",
})

_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def tokenize_identifier(name: str) -> tuple[str, ...]:
    """Parte un identificador (snake_case, camelCase, UPPER_SNAKE) en tokens minuscula."""
    return tuple(t.lower() for t in _TOKEN_RE.findall(str(name)))


def find_forbidden_tokens(name: str) -> tuple[str, ...]:
    """Tokens prohibidos presentes en un NOMBRE (clave, campo, miembro de enum, id)."""
    return tuple(t for t in tokenize_identifier(name) if t in FORBIDDEN_TOKENS)


# --------------------------------------------------------------------------
# Taxonomias
# --------------------------------------------------------------------------
class Area(str, Enum):
    SCOPE = "SCOPE"
    ANALYTIC_MODEL = "ANALYTIC_MODEL"
    PROPAGATION = "PROPAGATION"
    HARMONIZATION = "HARMONIZATION"
    INGESTION = "INGESTION"
    NAMING = "NAMING"
    SOURCE_EXTRACTION = "SOURCE_EXTRACTION"


# Las seis areas evaluables, en orden canonico (SCOPE no es una tarjeta de area).
ASSESSED_AREAS: tuple[Area, ...] = (
    Area.ANALYTIC_MODEL, Area.PROPAGATION, Area.HARMONIZATION,
    Area.INGESTION, Area.NAMING, Area.SOURCE_EXTRACTION,
)


class MacroDimension(str, Enum):
    ARCHITECTURE_MODELING = "ARCHITECTURE_MODELING"
    NAMING = "NAMING"
    SOURCE_EXTRACTION = "SOURCE_EXTRACTION"


MACRO_DIMENSION_OF_AREA: dict[Area, MacroDimension] = {
    Area.ANALYTIC_MODEL: MacroDimension.ARCHITECTURE_MODELING,
    Area.PROPAGATION: MacroDimension.ARCHITECTURE_MODELING,
    Area.HARMONIZATION: MacroDimension.ARCHITECTURE_MODELING,
    Area.INGESTION: MacroDimension.ARCHITECTURE_MODELING,
    Area.NAMING: MacroDimension.NAMING,
    Area.SOURCE_EXTRACTION: MacroDimension.SOURCE_EXTRACTION,
}


class MacroDimensionState(str, Enum):
    HAS_ASSESSED_EVIDENCE = "HAS_ASSESSED_EVIDENCE"
    COMPLETELY_NOT_ASSESSED = "COMPLETELY_NOT_ASSESSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Plane(str, Enum):
    MODELING = "MODELING"
    PROCESSING = "PROCESSING"      # evidencia complementaria cross-plane; nunca cambia denominadores de Modeling


class EvidenceClass(str, Enum):
    A = "A"      # AUTO_OBSERVABLE
    B = "B"      # DERIVABLE
    C1 = "C1"    # ROADMAP GAP (existe en el export, el loader/pipeline aun no lo consume)
    C2 = "C2"    # MANUAL / EXPERT ONLY
    D = "D"      # CONTEXT REQUIRED


class Applicability(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ApplicabilityReason(str, Enum):
    SYSTEM_ARTIFACT = "SYSTEM_ARTIFACT"                 # p.ej. SAP.* — fuera del concepto de Source/Extraction
    INTERNAL_STATIC_OBJECT = "INTERNAL_STATIC_OBJECT"   # objeto interno/estatico/local CONOCIDO (no es contrato de extraccion)
    NO_TERMINAL_ANALYTIC_MODEL = "NO_TERMINAL_ANALYTIC_MODEL"                   # sin Modelo Analitico terminal => sin unidad de assessment
    NO_OTHER_TERMINAL_ANALYTIC_MODEL = "NO_OTHER_TERMINAL_ANALYTIC_MODEL"       # universo de 1 AM: no hay AM par con quien reusar (CROSS_AM_REUSE)


class Assessability(str, Enum):
    ASSESSED = "ASSESSED"
    PARTIALLY_ASSESSED = "PARTIALLY_ASSESSED"
    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INDETERMINATE = "INDETERMINATE"


class Cause(str, Enum):
    """Por que algo no es plenamente evaluable. NUNCA es una causa de calidad negativa."""
    NOT_OBSERVABLE_IN_STANDARD_EXPORT = "NOT_OBSERVABLE_IN_STANDARD_EXPORT"
    NOT_IN_SNAPSHOT = "NOT_IN_SNAPSHOT"
    CLASSIFIER_RULE_GAP = "CLASSIFIER_RULE_GAP"
    PLANE_BOUNDARY = "PLANE_BOUNDARY"
    C1_NOT_YET_PARSED = "C1_NOT_YET_PARSED"
    C2_EXPERT_ONLY = "C2_EXPERT_ONLY"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    PROVENANCE_UNKNOWN = "PROVENANCE_UNKNOWN"
    COLLISION_OR_LOAD_ERROR = "COLLISION_OR_LOAD_ERROR"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"


class SignalKind(str, Enum):
    DESCRIPTIVE = "DESCRIPTIVE"
    REVIEW = "REVIEW"
    OBSERVATION = "OBSERVATION"


class ObservationId(str, Enum):
    DIRECT_RAW_TABLE_ACCESS = "DIRECT_RAW_TABLE_ACCESS"


class ReviewId(str, Enum):
    PASS_THROUGH_REVIEW = "PASS_THROUGH_REVIEW"
    CROSS_LAYER_REDERIVATION = "CROSS_LAYER_REDERIVATION"
    ROLE_LAYER_DIVERGENCE = "ROLE_LAYER_DIVERGENCE"
    DUPLICATED_REMOTE_IMPORT = "DUPLICATED_REMOTE_IMPORT"
    LAYER_BOUNDARY_REVIEW = "LAYER_BOUNDARY_REVIEW"
    FLAT_CENTRAL_ASSEMBLY_REVIEW = "FLAT_CENTRAL_ASSEMBLY_REVIEW"
    FIELD_VOCABULARY_MIX = "FIELD_VOCABULARY_MIX"
    OPAQUE_FIELD_NAME = "OPAQUE_FIELD_NAME"
    LABEL_NOT_ENRICHING = "LABEL_NOT_ENRICHING"
    AM_SEMANTIC_UNDERUSE_REVIEW = "AM_SEMANTIC_UNDERUSE_REVIEW"


class Resolution(str, Enum):
    OPEN_CONTEXT_REQUIRED = "OPEN_CONTEXT_REQUIRED"   # unica resolucion posible en Fase 1


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConfidenceDriverName(str, Enum):
    """La confianza agregada es SOLO fiabilidad epistemica de la evidencia evaluada (decision del owner, M6): cobertura, contexto abierto y
    evidencia de otro plano NO son drivers de confianza y por eso no existen como valores."""
    EPISTEMIC_RELIABILITY = "EPISTEMIC_RELIABILITY"


# --------------------------------------------------------------------------
# Validacion de valores JSON
# --------------------------------------------------------------------------
def _ensure_jsonable(value: Any, path: str = "value") -> None:
    """El valor debe ser JSON puro, finito, sin claves con vocabulario prohibido."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: float no finito")
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _ensure_jsonable(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"{path}: clave no-string {k!r}")
            bad = find_forbidden_tokens(k)
            if bad:
                raise ValueError(f"{path}: la clave {k!r} contiene vocabulario prohibido {bad}")
            _ensure_jsonable(v, f"{path}.{k}")
        return
    raise ValueError(f"{path}: tipo no serializable {type(value).__name__}")


_FACT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


# --------------------------------------------------------------------------
# Dataclasses del contrato
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EvidenceRef:
    entity: str
    attr_path: str
    source_file: str | None
    plane: Plane = Plane.MODELING


@dataclass(frozen=True)
class ApplicabilityDecision:
    state: Applicability
    reason: ApplicabilityReason | None = None

    def __post_init__(self) -> None:
        if self.state is Applicability.NOT_APPLICABLE and self.reason is None:
            raise ValueError("NOT_APPLICABLE exige un reason")
        if self.state is Applicability.APPLICABLE and self.reason is not None:
            raise ValueError("APPLICABLE no admite reason")


def _check_state_coupling(
    applicability: ApplicabilityDecision, assessability: Assessability, cause: Cause | None, what: str,
) -> None:
    not_app = applicability.state is Applicability.NOT_APPLICABLE
    if not_app != (assessability is Assessability.NOT_APPLICABLE):
        raise ValueError(f"{what}: NOT_APPLICABLE de aplicabilidad y de assessability deben coincidir")
    if assessability in (Assessability.NOT_ASSESSED, Assessability.INDETERMINATE) and cause is None:
        raise ValueError(f"{what}: {assessability.value} exige una causa")
    if assessability in (Assessability.ASSESSED, Assessability.NOT_APPLICABLE) and cause is not None:
        raise ValueError(f"{what}: {assessability.value} no admite causa")


@dataclass(frozen=True)
class Fact:
    fact_id: str
    area: Area
    subject: str
    value: Any
    evidence_class: EvidenceClass
    plane: Plane
    refs: tuple[EvidenceRef, ...]
    applicability: ApplicabilityDecision
    assessability: Assessability
    cause: Cause | None = None

    def __post_init__(self) -> None:
        if not _FACT_ID_RE.match(self.fact_id):
            raise ValueError(f"fact_id invalido {self.fact_id!r} (esperado 'dominio.nombre')")
        if self.area is Area.SCOPE:
            raise ValueError("los hechos de SCOPE viven en ProfileScope, no en tarjetas de area")
        if not self.subject:
            raise ValueError("subject vacio")
        bad = find_forbidden_tokens(self.fact_id)
        if bad:
            raise ValueError(f"fact_id {self.fact_id!r} contiene vocabulario prohibido {bad}")
        _ensure_jsonable(self.value, f"fact[{self.fact_id}@{self.subject}].value")
        _check_state_coupling(self.applicability, self.assessability, self.cause, f"fact {self.key}")

    @property
    def key(self) -> str:
        return f"{self.fact_id}@{self.subject}"


@dataclass(frozen=True)
class Signal:
    signal_id: str
    area: Area
    kind: SignalKind
    role_scope: tuple[str, ...]
    fact_refs: tuple[str, ...]
    interpretation_limits: tuple[str, ...]   # que NO debe leerse de esta senal (p.ej. "no es un defecto automatico")
    ruleset_version: str = RULESET_VERSION

    def __post_init__(self) -> None:
        if not self.signal_id or find_forbidden_tokens(self.signal_id):
            raise ValueError(f"signal_id invalido/prohibido: {self.signal_id!r}")


@dataclass(frozen=True)
class Observation:
    observation_id: ObservationId
    area: Area
    subjects: tuple[str, ...]
    fact_refs: tuple[str, ...]
    context_question_key: str
    resolution: Resolution = Resolution.OPEN_CONTEXT_REQUIRED

    def __post_init__(self) -> None:
        if not self.subjects:
            raise ValueError("Observation sin subjects")
        if self.resolution is not Resolution.OPEN_CONTEXT_REQUIRED:
            raise ValueError("Fase 1: toda observacion queda OPEN_CONTEXT_REQUIRED")


@dataclass(frozen=True)
class ReviewItem:
    review_id: ReviewId
    area: Area
    subjects: tuple[str, ...]
    fact_refs: tuple[str, ...]
    missing_context: str
    legitimate_reasons: tuple[str, ...]
    no_automatic_quality_judgment: bool = True

    def __post_init__(self) -> None:
        if not self.subjects:
            raise ValueError("ReviewItem sin subjects")
        if self.no_automatic_quality_judgment is not True:
            raise ValueError("un ReviewItem NUNCA emite juicio automatico de calidad")


@dataclass(frozen=True)
class AreaCard:
    area: Area
    applicability: ApplicabilityDecision
    assessability: Assessability
    facts: tuple[Fact, ...]
    signals: tuple[Signal, ...]
    not_observable: tuple[Cause, ...] = ()
    cause: Cause | None = None

    def __post_init__(self) -> None:
        if self.area not in ASSESSED_AREAS:
            raise ValueError(f"{self.area.value} no es un area evaluable")
        _check_state_coupling(self.applicability, self.assessability, self.cause, f"area {self.area.value}")
        keys = [f.key for f in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError(f"area {self.area.value}: hechos con key duplicada")
        for f in self.facts:
            if f.area is not self.area:
                raise ValueError(f"hecho {f.key} de area {f.area.value} dentro de la tarjeta {self.area.value}")


@dataclass(frozen=True)
class CoverageCounts:
    """Conteos de assessability. `applicable` = todo salvo NOT_APPLICABLE (que sale del denominador).

    INDETERMINATE cuenta como aplicable y NO evaluado (no suma a ninguna razon).
    Si no hay nada aplicable la razon es None: "sin evidencia aplicable" no es 0 %.
    """
    assessed: int = 0
    partially_assessed: int = 0
    not_assessed: int = 0
    indeterminate: int = 0
    not_applicable: int = 0

    def __post_init__(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) < 0:
                raise ValueError(f"{f.name} negativo")

    @property
    def applicable(self) -> int:
        return self.assessed + self.partially_assessed + self.not_assessed + self.indeterminate

    @property
    def ratio_strict(self) -> float | None:
        """Solo ASSESSED. Es la razon candidata al gate futuro (decision del owner)."""
        return None if self.applicable == 0 else self.assessed / self.applicable

    @property
    def ratio_inclusive(self) -> float | None:
        """ASSESSED + PARTIALLY_ASSESSED. Solo diagnostica (no es base de gate)."""
        return None if self.applicable == 0 else (self.assessed + self.partially_assessed) / self.applicable

    def _jsonable_extra(self) -> dict[str, Any]:
        return {"applicable": self.applicable, "ratio_strict": self.ratio_strict,
                "ratio_inclusive": self.ratio_inclusive}


@dataclass(frozen=True)
class AreaCoverage:
    area: Area
    counts: CoverageCounts


@dataclass(frozen=True)
class MacroDimensionStatus:
    macro_dimension: MacroDimension
    state: MacroDimensionState


@dataclass(frozen=True)
class CoverageReport:
    overall: CoverageCounts
    by_area: tuple[AreaCoverage, ...]
    macro_dimensions: tuple[MacroDimensionStatus, ...]

    def __post_init__(self) -> None:
        areas = [a.area for a in self.by_area]
        if len(areas) != len(set(areas)):
            raise ValueError("by_area con areas duplicadas")


@dataclass(frozen=True)
class ConfidenceDriver:
    name: ConfidenceDriverName
    level: ConfidenceLevel
    detail: str


@dataclass(frozen=True)
class Confidence:
    level: ConfidenceLevel
    drivers: tuple[ConfidenceDriver, ...]


@dataclass(frozen=True)
class ProfileScope:
    applicability: ApplicabilityDecision
    terminal_am: str | None
    closure_size: int
    closure_names: tuple[str, ...]
    pool_signature: str | None = None

    def __post_init__(self) -> None:
        if self.applicability.state is Applicability.NOT_APPLICABLE:
            if self.applicability.reason is not ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL:
                raise ValueError("el scope solo es NOT_APPLICABLE por NO_TERMINAL_ANALYTIC_MODEL")
            if self.terminal_am is not None or self.closure_size != 0 or self.closure_names:
                raise ValueError("scope NOT_APPLICABLE no admite AM ni clausura")
        else:
            if not self.terminal_am:
                raise ValueError("scope aplicable exige terminal_am")
            if self.closure_size != len(self.closure_names):
                raise ValueError("closure_size debe igualar len(closure_names)")


@dataclass(frozen=True)
class ModelingProfile:
    schema_version: str
    ruleset_version: str
    scope: ProfileScope
    areas: tuple[AreaCard, ...]
    observations: tuple[Observation, ...]
    review_required: tuple[ReviewItem, ...]
    coverage: CoverageReport
    confidence: Confidence

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version {self.schema_version!r} != {SCHEMA_VERSION!r}")
        if tuple(c.area for c in self.areas) != ASSESSED_AREAS:
            raise ValueError("areas deben ser exactamente las seis areas evaluables, en orden canonico")
        known = {f.key for c in self.areas for f in c.facts}
        refs: list[str] = []
        for c in self.areas:
            for s in c.signals:
                refs.extend(s.fact_refs)
        for o in self.observations:
            refs.extend(o.fact_refs)
        for r in self.review_required:
            refs.extend(r.fact_refs)
        missing = sorted(set(refs) - known)
        if missing:
            raise ValueError(f"fact_refs que no existen en ninguna tarjeta: {missing}")


# --------------------------------------------------------------------------
# Serializacion canonica
# --------------------------------------------------------------------------
def to_jsonable(obj: Any) -> Any:
    """Dataclass/Enum/tuple -> estructura JSON pura (orden de campos = orden de definicion)."""
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
        extra = getattr(obj, "_jsonable_extra", None)
        if callable(extra):
            out.update(extra())
        return out
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return obj


def canonical_json(profile: ModelingProfile) -> str:
    """JSON determinista: claves ordenadas, sin NaN, UTF-8, salto final."""
    return json.dumps(to_jsonable(profile), sort_keys=True, ensure_ascii=False, indent=2,
                      separators=(",", ": "), allow_nan=False) + "\n"
