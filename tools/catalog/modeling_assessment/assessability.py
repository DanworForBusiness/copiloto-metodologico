"""Composicion de ASSESSABILITY + COVERAGE + CONFIDENCE (M6) sobre las superficies de evidencia de M0-M5. Sin juicio de calidad.

M6 COMPONE la semantica del assessment; M7 solo la serializa/renderiza. No hay score numerico, banda ni PLENO/BUENO/PARCIAL.
Cobertura baja NO es madurez baja. Confianza NO es madurez ni es la cobertura: son salidas distintas.

SUPERFICIE DE EVIDENCIA REQUERIDA (unidad de cobertura)
  La cobertura NO cuenta objetos Fact, ni senales, ni findings. Se calcula sobre un REGISTRO de superficies (`SURFACE_REGISTRY`):
    * FACT_EVIDENCE   una familia de hechos (p. ej. `source.contract`) evaluada sobre todas sus unidades (artefactos/aristas/clausura);
    * RULE_EVALUATION si una regla de M5 pudo EVALUARSE (una senal con cero findings puede tener assessability completa o nula).
  Cada superficie tiene un ROL:
    REQUIRED             entra en numerador y denominador segun su estado;
    CONTEXT_ONLY         pregunta/definicion de contexto (p. ej. `naming.convention_context`): fuera de numerador Y denominador;
    DERIVED_VIEW         agregado derivado de superficies que ya cuentan (evita doble cobro): fuera de numerador y denominador;
    CROSS_PLANE_COMPLEMENT  evidencia de Processing: complementaria, nunca altera denominadores de Modeling.
  Una superficie REQUERIDA no disponible / C2 SI reduce la cobertura. CONTEXT_REQUIRED no es lo mismo que evidencia requerida faltante.

ASSESSABILITY de una superficie = agregado del estado de sus unidades:
  todas ASSESSED -> ASSESSED; alguna ASSESSED/PARTIAL y otras no -> PARTIALLY_ASSESSED; ninguna -> NOT_ASSESSED (+causa dominante);
  sin unidades aplicables -> NOT_APPLICABLE (sale del denominador). Aplicabilidad primero, assessability despues.

AUSENCIA DE SENAL != AUSENCIA EVALUADA (regla del owner): cada superficie de regla informa `result`:
  PATTERN_FOUND | ASSESSED_ABSENCE (evidencia disponible + regla evaluada + patron no encontrado) |
  ABSENCE_NOT_ESTABLISHED (la regla no pudo evaluarse por completo) | NOT_APPLICABLE.

TRES EJES DISTINTOS (decision del owner; no se colapsan):
  COVERAGE         cuanta de la evidencia REQUERIDA pudo evaluarse (completitud).
  CONFIDENCE       cuan confiable/epistemicamente fiable es lo que SI se evaluo.
  CONTEXT REQUIRED lo que aun requiere resolucion humana/de negocio/de arquitectura.
CONFIANZA: solo la FIABILIDAD EPISTEMICA de la evidencia evaluada la determina. La cobertura (completitud) y el conteo de items de contexto
abierto NO entran en la confianza (no se cuenta dos veces la incompletitud; "contexto sin resolver" != "evidencia poco fiable"; una observacion
de tabla raw con metadata objetiva es evidencia fiable aunque su interpretacion siga abierta). Factores que contribuyen (marcas explicitas del
modelo canonico): certeza de la identidad (PROVEN vs HEURISTIC), procedencia sin resolver (UNKNOWN) y entradas parciales de una regla.
Se conservan como DIAGNOSTICO (`ConfidenceFactor.contributes_to_confidence = False`): cobertura estricta/inclusiva, brechas de evidencia abiertas
(completitud), preguntas/items de contexto abiertos (contextualidad), evidencia de Processing y A-vs-B (no es una marca de fiabilidad).
La evidencia de Processing NO es una recompensa global de confianza y no vuelve ASSESSED una superficie NOT_ASSESSED de Modeling.
Cortes PROVISIONALES (`CONFIDENCE_CUTS`): VALIDAR EN M8; no son umbrales de calidad ni de madurez; no se ajustan a puntajes humanos.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping

from .model import (
    ASSESSED_AREAS, MACRO_DIMENSION_OF_AREA, Applicability, ApplicabilityDecision, ApplicabilityReason, Area, AreaCard, AreaCoverage, Assessability,
    Cause, Confidence, ConfidenceDriver, ConfidenceDriverName, ConfidenceLevel, CoverageCounts, CoverageReport, EvidenceClass, Fact,
    MacroDimension, MacroDimensionState, MacroDimensionStatus, Observation, Plane, ProfileScope, ReviewItem, Signal, SignalKind,
)
from .signals import FactIndex, Finding, SignalEvaluation, evaluate_signals

ASSESSABILITY_RULESET = "assessability-rules/v4"       # v4 (field-lineage evidence release): superficies descriptivas `am.published_field_lineage` y `source.contract_class` (no cuentan en cobertura); v3 (evidence release): superficie REQUERIDA de completitud del cierre (`am.closure_reference_resolution`) + superficies descriptivas del contrato publicado, armonizacion, expresiones, reconvergencia, complejidad y propiedades de flujo; v2 (G1): `source.leaf_provenance` reemplaza a `source.leaf_contract_status`
CONFIDENCE_RULESET = "confidence-cuts/v2"       # v2: factor epistemico `unambiguous_closure_references` (la resolucion AMBIGUA de una referencia del cierre afecta la CONFIANZA, no la cobertura)

# Cortes PROVISIONALES (no son umbrales de calidad/madurez): (corte para HIGH, corte para MEDIUM). Se validan en M8.
CONFIDENCE_CUTS = {                                   # PROVISIONAL, VALIDAR EN M8 (no son umbrales de calidad/madurez)
    "reliable_share": (0.8, 0.5),                     # share fiable de cada factor epistemico: >= 0.8 -> HIGH; >= 0.5 -> MEDIUM; si no LOW
}

_LEVEL_ORDER = (ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH)
_OPEN_GAP_CLASSES = (EvidenceClass.C1, EvidenceClass.C2, EvidenceClass.D)


class SurfaceKind(str, Enum):
    FACT_EVIDENCE = "FACT_EVIDENCE"
    RULE_EVALUATION = "RULE_EVALUATION"


class SurfaceRole(str, Enum):
    REQUIRED = "REQUIRED"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    DERIVED_VIEW = "DERIVED_VIEW"
    CROSS_PLANE_COMPLEMENT = "CROSS_PLANE_COMPLEMENT"
    OPTIONAL_DEEPER_EVIDENCE = "OPTIONAL_DEEPER_EVIDENCE"   # evidencia mas profunda (p. ej. C2) que ninguna conclusion de Fase 1 requiere: no reduce la cobertura


class RuleResult(str, Enum):
    PATTERN_FOUND = "PATTERN_FOUND"
    ASSESSED_ABSENCE = "ASSESSED_ABSENCE"
    ABSENCE_NOT_ESTABLISHED = "ABSENCE_NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Unit:
    """Una unidad de evaluacion de una superficie (artefacto, arista, clausura)."""
    subject: str
    assessability: Assessability
    cause: Cause | None = None
    not_applicable_reason: str | None = None
    evidence_class: EvidenceClass | None = None


@dataclass(frozen=True)
class SurfaceAssessment:
    surface_id: str
    area: Area
    kind: SurfaceKind
    role: SurfaceRole
    assessability: Assessability
    cause: Cause | None
    not_applicable_reason: str | None
    counts: CoverageCounts                      # estados de las UNIDADES de la superficie
    open_gap_classes: tuple[EvidenceClass, ...]
    units: tuple[Unit, ...]
    rationale: str
    result: RuleResult | None = None            # solo superficies de regla
    finding_count: int | None = None


class ConfidenceAxis(str, Enum):
    EPISTEMIC_RELIABILITY = "EPISTEMIC_RELIABILITY"      # unico eje que puede contribuir a la confianza agregada
    COMPLETENESS = "COMPLETENESS"                        # pertenece a COVERAGE
    CONTEXTUALITY = "CONTEXTUALITY"                      # pertenece a CONTEXT REQUIRED / REVIEW
    CROSS_PLANE = "CROSS_PLANE"                          # evidencia de otro plano: ni premio ni castigo global


@dataclass(frozen=True)
class ConfidenceFactor:
    """Factor (contribuyente o diagnostico) de la confianza. `level` solo existe para los que contribuyen."""
    axis: ConfidenceAxis
    name: str
    value: float | int | None
    detail: str
    contributes_to_confidence: bool
    level: ConfidenceLevel | None = None


@dataclass(frozen=True)
class AssessmentComposition:
    """Semantica compuesta del assessment: M7 la serializa/renderiza sin reinterpretar hechos crudos."""
    scope: ProfileScope
    areas: tuple[AreaCard, ...]
    surfaces: tuple[SurfaceAssessment, ...]
    coverage: CoverageReport
    confidence: Confidence
    confidence_factors: tuple[ConfidenceFactor, ...]
    observations: tuple[Observation, ...]
    review_required: tuple[ReviewItem, ...]
    signals: tuple[Signal, ...]
    findings: tuple[Finding, ...]
    assessability_ruleset: str = ASSESSABILITY_RULESET
    signals_ruleset: str = ""
    confidence_ruleset: str = CONFIDENCE_RULESET


# ───────────────────────────── agregacion ─────────────────────────────
def _dominant_cause(causes: Iterable[Cause | None]) -> Cause | None:
    c = Counter(x for x in causes if x is not None)
    if not c:
        return None
    order = {k: i for i, k in enumerate(Cause)}
    return sorted(c.items(), key=lambda kv: (-kv[1], order[kv[0]]))[0][0]


def _aggregate(states: list[tuple[Assessability, Cause | None, str | None]]) -> tuple[Assessability, Cause | None, str | None, CoverageCounts]:
    """(estado, causa, razon NA, conteos) de una coleccion de estados de unidades."""
    na = [s for s in states if s[0] is Assessability.NOT_APPLICABLE]
    app = [s for s in states if s[0] is not Assessability.NOT_APPLICABLE]
    cnt = Counter(s[0] for s in app)
    counts = CoverageCounts(cnt[Assessability.ASSESSED], cnt[Assessability.PARTIALLY_ASSESSED], cnt[Assessability.NOT_ASSESSED],
                            cnt[Assessability.INDETERMINATE], len(na))
    if not app:
        reason = Counter(s[2] for s in na if s[2]).most_common(1)
        return Assessability.NOT_APPLICABLE, None, reason[0][0] if reason else None, counts
    gaps = [s[1] for s in app if s[0] is not Assessability.ASSESSED]
    if cnt[Assessability.ASSESSED] == len(app):
        return Assessability.ASSESSED, None, None, counts
    if cnt[Assessability.ASSESSED] or cnt[Assessability.PARTIALLY_ASSESSED]:
        return Assessability.PARTIALLY_ASSESSED, _dominant_cause(gaps), None, counts
    if cnt[Assessability.INDETERMINATE] and not cnt[Assessability.NOT_ASSESSED]:
        return Assessability.INDETERMINATE, _dominant_cause(gaps) or Cause.CONTRADICTORY_EVIDENCE, None, counts
    return Assessability.NOT_ASSESSED, _dominant_cause(gaps) or Cause.NOT_IN_SNAPSHOT, None, counts


def _class_of_cause(cause: Cause | None) -> EvidenceClass | None:
    if cause is None:
        return None
    if cause in (Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT, Cause.C2_EXPERT_ONLY):
        return EvidenceClass.C2
    if cause in (Cause.PROVENANCE_UNKNOWN, Cause.PLANE_BOUNDARY, Cause.CONTEXT_REQUIRED):
        return EvidenceClass.D
    if cause in (Cause.CLASSIFIER_RULE_GAP, Cause.C1_NOT_YET_PARSED, Cause.NOT_IN_SNAPSHOT):
        return EvidenceClass.C1
    return None


class Ctx:
    def __init__(self, facts: list[Fact], evaluation: SignalEvaluation, root_type: str = "analytic-model") -> None:
        self.root_type = root_type
        self.facts = facts
        self.idx = FactIndex(facts)
        self.evaluation = evaluation
        self.area_present = {a: any(f.area is a for f in facts) for a in ASSESSED_AREAS}
        self.finding_counts = Counter(f.signal_id for f in evaluation.findings)

    def remote_artifacts_in_scope(self) -> int:
        fam = self.idx.usable_get("source.family_distribution", "closure")
        if fam is not None:
            return int(fam.value["remote_artifacts"])
        return len(self.idx.all("source.contract"))


def _unit_of_fact(f: Fact) -> Unit:
    na = f.assessability is Assessability.NOT_APPLICABLE
    return Unit(f.subject, f.assessability, f.cause, f.applicability.reason.value if na and f.applicability.reason else None, f.evidence_class)


# ───────────────────────────── registro de superficies ─────────────────────────────
@dataclass(frozen=True)
class SurfaceSpec:
    surface_id: str
    area: Area
    kind: SurfaceKind
    role: SurfaceRole
    rationale: str
    fact_ids: tuple[str, ...] = ()
    per_unit: bool = True                                   # FACT: cero unidades => NOT_APPLICABLE; si no, faltante => NOT_ASSESSED
    na_when: Callable[[Ctx], str | None] | None = None      # razon de NOT_APPLICABLE de toda la superficie
    units: Callable[[Ctx], list[Unit] | None] | None = None  # RULE: unidades; None => poblacion no determinable
    signal_id: str | None = None


def _not_am_root(ctx: Ctx) -> str | None:
    """Evidencia/regla ESPECIFICA de Modelo Analitico: NOT_APPLICABLE (no reduce la cobertura) cuando la raiz evaluada es vista o tabla."""
    return "ROOT_NOT_ANALYTIC_MODEL" if ctx.root_type != "analytic-model" else None


def _no_remote(ctx: Ctx) -> str | None:
    return "NO_REMOTE_SOURCE_IN_SCOPE" if ctx.remote_artifacts_in_scope() == 0 else None


F, R = SurfaceKind.FACT_EVIDENCE, SurfaceKind.RULE_EVALUATION
REQ, CTX, VIEW, XPL, OPT = (SurfaceRole.REQUIRED, SurfaceRole.CONTEXT_ONLY, SurfaceRole.DERIVED_VIEW, SurfaceRole.CROSS_PLANE_COMPLEMENT,
                            SurfaceRole.OPTIONAL_DEEPER_EVIDENCE)
_SRC, _ING, _AM, _PRO, _HAR, _NAM = (Area.SOURCE_EXTRACTION, Area.INGESTION, Area.ANALYTIC_MODEL, Area.PROPAGATION, Area.HARMONIZATION, Area.NAMING)

_FACT_SURFACES: tuple[SurfaceSpec, ...] = (
    # SOURCE / EXTRACTION
    SurfaceSpec("source.contract", _SRC, F, REQ, "contract of every remote-bearing artifact in scope", ("source.contract",)),
    SurfaceSpec("source.keys", _SRC, F, REQ, "declared keys of remote artifacts", ("source.keys",)),
    SurfaceSpec("source.annotation_inventory", _SRC, F, REQ, "supplementary C1 read of remote table annotations", ("source.annotation_inventory",)),
    SurfaceSpec("source.query_filter", _SRC, F, REQ, "supplementary C1 read of remote.query (filters)", ("source.query_filter",)),
    SurfaceSpec("source.downstream_reach", _SRC, F, REQ, "downstream reach of remote artifacts", ("source.downstream_reach",)),
    SurfaceSpec("source.identity_continuity", _SRC, F, REQ, "local identity vs remote stem", ("source.identity_continuity",)),
    SurfaceSpec("source.family_distribution", _SRC, F, VIEW, "closure aggregate of source.contract: already counted there", ("source.family_distribution",), per_unit=False),
    SurfaceSpec("source.connection_consistency", _SRC, F, REQ, "connections and dual-connection entities", ("source.connection_consistency",), per_unit=False, na_when=_no_remote),
    SurfaceSpec("source.replication_mode", _SRC, F, XPL, "federated vs replicated mode: Processing/Integration execution semantics, not observable in the standard Modeling export; complementary, never a universal Modeling coverage requirement",
                ("source.replication_mode",), per_unit=False, na_when=_no_remote),
    SurfaceSpec("source.leaf_provenance", _SRC, F, REQ, "boundary leaves without remote origin: provenance ESTABLISHED/DECLARED by direct producer evidence, NOT_OBSERVABLE only with a conclusive acquired scope, NOT_ASSESSED otherwise (never NOT_APPLICABLE)",
                ("source.leaf_provenance",)),
    SurfaceSpec("source.physical_boundary", _SRC, F, XPL, "external integration boundary revealed by the provenance chain of a flow-produced local leaf (physical table / governed CDS / governed SAPI extractor / unclassified): Processing-provenance evidence for the SOURCE contract; never a semantic closure member and never DIRECT_RAW_TABLE_ACCESS evidence",
                ("source.physical_boundary",)),
    SurfaceSpec("source.contract_class", _SRC, F, VIEW, "descriptive: explicit source-contract class of every frontier leaf (governed SAP CDS / ODP-SAPI, physical ABAP table, external contract undetermined, platform provided, unknown) with the basis of the classification; technology alone never classifies governance or rawness",
                ("source.contract_class",)),
    # INGESTION
    SurfaceSpec("ingestion.boundary_artifacts", _ING, F, REQ, "boundary leaves of the closure", ("ingestion.boundary_artifacts",), per_unit=False),
    SurfaceSpec("ingestion.first_consumers", _ING, F, REQ, "first consumers of each boundary leaf", ("ingestion.first_consumers",)),
    SurfaceSpec("ingestion.field_retention", _ING, F, REQ, "field retention leaf -> first consumer", ("ingestion.field_retention",)),
    SurfaceSpec("ingestion.duplicate_remote_imports", _ING, F, REQ, "remote entities imported under several identities", ("ingestion.duplicate_remote_imports",), per_unit=False),
    SurfaceSpec("ingestion.inferred_layer_vs_lineage_depth", _ING, F, REQ, "inferred layer vs real lineage depth", ("ingestion.inferred_layer_vs_lineage_depth",), per_unit=False),
    SurfaceSpec("ingestion.dataflow_boundary", _ING, F, XPL, "Processing-plane references: never alter Modeling denominators", ("ingestion.dataflow_boundary",), per_unit=False),
    SurfaceSpec("ingestion.processing_context_inventory", _ING, F, XPL, "Processing-plane inventory", ("ingestion.processing_context_inventory",), per_unit=False),
    SurfaceSpec("ingestion.flow_load_properties", _ING, F, XPL, "exact exported load properties (mode / upsert / filter / delta flags) of the flows that produce closure leaves; unobserved properties stay unobserved, nothing is inferred",
                ("ingestion.flow_load_properties",), per_unit=False),
    SurfaceSpec("ingestion.processing_reference", _ING, F, XPL, "Processing-plane flow references", ("ingestion.processing_reference",)),
    # ANALYTIC MODEL
    SurfaceSpec("am.contract_inventory", _AM, F, REQ, "declared contract of the terminal analytic model", ("am.contract_inventory",), per_unit=False, na_when=_not_am_root),
    SurfaceSpec("am.association_semantics", _AM, F, VIEW, "descriptive: association categories (business dimension / time platform dimension / other platform / unknown) from explicit identity and declared role; the number of associations is not a measure of quality",
                ("am.association_semantics",), per_unit=False, na_when=_not_am_root),
    SurfaceSpec("am.field_semantics", _AM, F, VIEW, "descriptive: published fields and measures (label vs technical name, datatype family / precision / scale, name equal to a remote source column) and the measure datatype composition",
                ("am.field_semantics",), per_unit=False, na_when=_not_am_root),
    SurfaceSpec("am.published_field_lineage", _AM, F, VIEW, "descriptive: factual backward provenance of each published field through the compiled queries of the closure to the frontier leaves (mapping type per hop, harmonization contracts crossed, completeness COMPLETE/PARTIAL/UNRESOLVED/AMBIGUOUS); ambiguity is explicit, no quality judgment",
                ("am.published_field_lineage",), per_unit=False, na_when=_not_am_root),
    SurfaceSpec("am.association_targets", _AM, F, REQ, "association targets of the analytic model", ("am.association_targets",), per_unit=False, na_when=_not_am_root),
    SurfaceSpec("am.contract_delta", _AM, F, REQ, "what the analytic model adds/removes vs each direct lineage provider", ("am.contract_delta",), na_when=_not_am_root),
    SurfaceSpec("am.numeric_attributes_not_declared_as_measures", _AM, F, REQ, "numeric attributes not declared as measures", ("am.numeric_attributes_not_declared_as_measures",), per_unit=False,
                na_when=_not_am_root),
    SurfaceSpec("am.closure_reference_resolution", _AM, F, REQ, "completeness of the semantic closure: every declared reference of a closure member is RESOLVED (UNRESOLVED / EXCLUDED_BY_PLANE members lower coverage; AMBIGUOUS identity affects confidence)",
                ("am.closure_reference_resolution",)),
    SurfaceSpec("am.closure_plane_context", _AM, F, VIEW, "descriptive: local tables read by Modeling that a flow produces (semantic members) and the difference against the previous plane classification",
                ("am.closure_plane_context",), per_unit=False),
    SurfaceSpec("am.closure_composition", _AM, F, VIEW, "descriptive composition of the semantic closure (strict lineage vs association): graph structure, not a required evidence surface",
                ("am.closure_composition",), per_unit=False),
    SurfaceSpec("am.view_type_composition", _AM, F, VIEW, "descriptive composition of the closure by Datasphere view type (GRAPHICAL/SQL/UNKNOWN): objective evidence, no judgment, not a required surface",
                ("am.view_type_composition",), per_unit=False),
    # PROPAGATION
    SurfaceSpec("propagation.derived_fields", _PRO, F, REQ, "derived fields per non-leaf artifact (needs a visible query)", ("propagation.derived_fields",)),
    SurfaceSpec("propagation.contract_delta", _PRO, F, REQ, "contract delta per lineage edge", ("propagation.contract_delta",)),
    SurfaceSpec("propagation.contract_occurrences", _PRO, F, REQ, "occurrences of the AM contract along the closure", ("propagation.contract_occurrences",), per_unit=False),
    SurfaceSpec("propagation.rederivation", _PRO, F, REQ, "same field derived in lineage ancestors", ("propagation.rederivation",), per_unit=False),
    SurfaceSpec("propagation.derivation_semantics", _PRO, F, OPT, "structural derivation semantics from the compiled query AST (operations, inputs, fingerprints, relation of repeated derivations); business semantics of formulas stay out of scope, so it is optional deeper evidence and would become REQUIRED only if a future surface/judgment depends on it",
                ("propagation.derivation_semantics",), per_unit=False),
    SurfaceSpec("propagation.expression_occurrences", _PRO, F, VIEW, "descriptive: expression occurrences per clause (SELECT / WHERE / GROUP BY / HAVING / JOIN ON) with transformation class, layer and observed structural reuse; no ownership judgment",
                ("propagation.expression_occurrences",), per_unit=False),
    SurfaceSpec("propagation.branch_reconvergence", _PRO, F, VIEW, "descriptive: parallel branches that share a provider contract and reconverge downstream (consumed-field overlap, paths); a shared source is not a deviation",
                ("propagation.branch_reconvergence",), per_unit=False),
    # HARMONIZATION
    SurfaceSpec("harmonization.dimension_artifacts", _HAR, F, REQ, "declared dimensions of the closure", ("harmonization.dimension_artifacts",), per_unit=False),
    SurfaceSpec("harmonization.composition_artifacts", _HAR, F, REQ, "composition artifacts (fan-in >= 2)", ("harmonization.composition_artifacts",), per_unit=False),
    SurfaceSpec("harmonization.expression_repetition", _HAR, F, VIEW, "descriptive: identical derivation structures repeated across artifacts of the closure (sibling or lineage related); reuse evidence, no judgment, not a required surface",
                ("harmonization.expression_repetition",), per_unit=False),
    SurfaceSpec("harmonization.assembly_topology", _HAR, F, VIEW, "descriptive topology of the composition artifacts (providers, boundary providers, reusable intermediates, consumers, distance, share of closure leaves); no threshold, no judgment",
                ("harmonization.assembly_topology",), per_unit=False),
    SurfaceSpec("harmonization.contract_character", _HAR, F, VIEW, "descriptive: how the contract of each harmonization candidate is built (mapping kinds, retained source fields, label origin, vocabulary carry-through, transformation classes); no quality score",
                ("harmonization.contract_character",)),
    SurfaceSpec("harmonization.view_complexity", _HAR, F, VIEW, "descriptive structural complexity of the closure views next to the view type; maintainability qualifier, no threshold, no directionality",
                ("harmonization.view_complexity",)),
    SurfaceSpec("harmonization.reuse_context", _HAR, F, CTX, "definitions of the two reuse scopes: context to read the reuse facts", ("harmonization.reuse_context",), per_unit=False),
    SurfaceSpec("harmonization.local_chain_reuse", _HAR, F, REQ, "LOCAL_CHAIN_REUSE per dimension/association target", ("harmonization.local_chain_reuse",)),
    SurfaceSpec("harmonization.cross_am_reuse", _HAR, F, REQ, "CROSS_AM_REUSE per artifact (NOT_APPLICABLE when no peer analytic model exists or the root is not an analytic model)",
                ("harmonization.cross_am_reuse",), na_when=_not_am_root),
    # NAMING
    SurfaceSpec("naming.artifact_name_contract", _NAM, F, REQ, "section 28 compliance and provenance per artifact", ("naming.artifact_name_contract",)),
    SurfaceSpec("naming.field_name_contract", _NAM, F, REQ, "labels per field", ("naming.field_name_contract",)),
    SurfaceSpec("naming.field_name_forms", _NAM, F, REQ, "mechanical field-name forms", ("naming.field_name_forms",)),
    SurfaceSpec("naming.field_name_continuity", _NAM, F, REQ, "field-name continuity along the observable lineage", ("naming.field_name_continuity",)),
    SurfaceSpec("naming.artifact_name_populations", _NAM, F, VIEW, "aggregate of naming.artifact_name_contract: already counted there", ("naming.artifact_name_populations",), per_unit=False),
    SurfaceSpec("naming.field_vocabulary_summary", _NAM, F, VIEW, "aggregate of the field facts: already counted there", ("naming.field_vocabulary_summary",), per_unit=False),
    SurfaceSpec("naming.same_normalized_label_multiple_technical_names", _NAM, F, VIEW, "derived view of the label facts; the rule surface covers its use",
                ("naming.same_normalized_label_multiple_technical_names",), per_unit=False),
    SurfaceSpec("naming.convention_context", _NAM, F, CTX, "historical/local convention: explains a deviation, never rewrites current compliance (context only)",
                ("naming.convention_context",), per_unit=False),
)


# ── unidades de las superficies de REGLA (M5): poblacion + evaluabilidad ──
def _source_units(ctx: Ctx, extra_required: tuple[str, ...] = ()) -> list[Unit] | None:
    b = ctx.idx.usable_get("ingestion.boundary_artifacts", "closure")
    if b is None or any(ctx.idx.usable_get(r, "closure") is None for r in extra_required):
        return None
    out = []
    for a in b.value["artifacts"]:
        n = a["artifact"]
        if a["identity"] == "SYSTEM":
            out.append(Unit(n, Assessability.NOT_APPLICABLE, None, "SYSTEM_ARTIFACT"))
        elif a["has_remote_entity"]:
            c = ctx.idx.get("source.contract", n)
            ok = c is not None and c.assessability is Assessability.ASSESSED
            out.append(Unit(n, Assessability.ASSESSED if ok else Assessability.NOT_ASSESSED, None if ok else Cause.NOT_IN_SNAPSHOT,
                            None, c.evidence_class if c else EvidenceClass.C1))
        else:
            lp = ctx.idx.get("source.leaf_provenance", n)           # G1: la procedencia ESTABLECIDA/DECLARADA/NOT_OBSERVABLE-concluyente es un resultado EVALUADO
            if lp is not None and lp.assessability is Assessability.ASSESSED:
                out.append(Unit(n, Assessability.ASSESSED, None, None, lp.evidence_class))
            else:
                out.append(Unit(n, Assessability.NOT_ASSESSED, lp.cause if lp is not None and lp.cause else Cause.PROVENANCE_UNKNOWN, None,
                                lp.evidence_class if lp is not None else EvidenceClass.D))
    return out


def _units_direct_raw(ctx: Ctx):
    return _source_units(ctx)


def _units_layer_boundary(ctx: Ctx):
    units = _source_units(ctx)
    if units is None:
        return None
    return [Unit(u.subject, Assessability.NOT_ASSESSED, Cause.NOT_IN_SNAPSHOT, None, EvidenceClass.C1)
            if u.assessability is Assessability.ASSESSED and ctx.idx.usable_get("ingestion.first_consumers", u.subject) is None else u
            for u in units]                                         # una hoja remota solo es evaluable si se conocen sus primeros consumidores


def _units_duplicates(ctx: Ctx):
    return _source_units(ctx, ("ingestion.duplicate_remote_imports",))


EDGE_OUTSIDE_RULE_POPULATION = "EDGE_OUTSIDE_RULE_POPULATION"


def _units_pass_through(ctx: Ctx):
    """Poblacion de PASS_THROUGH_REVIEW = transformaciones INTERNAS modelado->modelado (`propagation.contract_delta`). Las aristas fuente->primer
    consumidor (`ingestion.field_retention`) y ultimo proveedor->AM terminal (`am.contract_delta`) NO son elegibles: NOT_APPLICABLE a esta regla
    (no bajan la cobertura ni hacen parcial la superficie; se conservan solo como unidades para trazabilidad)."""
    deltas = ctx.idx.all("propagation.contract_delta")
    if not deltas and not (ctx.area_present[Area.PROPAGATION] or ctx.area_present[Area.ANALYTIC_MODEL]):
        return None
    out = []
    for f in deltas:
        if f.assessability is Assessability.NOT_APPLICABLE or f.value is None:
            out.append(_unit_of_fact(f))
        elif f.value["consumer_role"]["declares_dimension"]:
            out.append(Unit(f.subject, Assessability.NOT_APPLICABLE, None, "CONSUMER_IS_DECLARED_DIMENSION"))
        elif not f.value["derived"]["consumer_derivation_visible"]:
            out.append(Unit(f.subject, Assessability.NOT_ASSESSED, Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT, None, EvidenceClass.C2))
        else:
            out.append(Unit(f.subject, Assessability.ASSESSED))
    for fid in ("ingestion.field_retention", "am.contract_delta"):          # aristas fuera de la poblacion de la regla
        for f in ctx.idx.all(fid):
            out.append(Unit(f.subject, Assessability.NOT_APPLICABLE, None, EDGE_OUTSIDE_RULE_POPULATION))
    return out


def _units_rederivation(ctx: Ctx):
    if ctx.idx.usable_get("propagation.rederivation", "closure") is None:
        return None
    out = []
    for f in ctx.idx.all("propagation.derived_fields"):
        if f.assessability is Assessability.NOT_APPLICABLE:
            out.append(_unit_of_fact(f))
        elif f.assessability is Assessability.ASSESSED:
            out.append(Unit(f.subject, Assessability.ASSESSED))
        else:
            out.append(Unit(f.subject, Assessability.NOT_ASSESSED, f.cause or Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT, None, EvidenceClass.C2))
    for f in ctx.idx.usable("am.contract_inventory"):
        ok = bool(f.value["summary"]["has_query"])
        out.append(Unit(f.subject, Assessability.ASSESSED if ok else Assessability.NOT_ASSESSED, None if ok else Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT,
                        None, None if ok else EvidenceClass.C2))
    return out


def _units_role_layer(ctx: Ctx):
    lf = ctx.idx.usable_get("ingestion.inferred_layer_vs_lineage_depth", "closure")
    if lf is None or ctx.idx.usable_get("ingestion.boundary_artifacts", "closure") is None:
        return None
    out = []
    for a in lf.value["artifacts"]:
        n = a["artifact"]
        if a["implementation"] == "SYSTEM_PROVIDED":
            out.append(Unit(n, Assessability.NOT_APPLICABLE, None, "SYSTEM_ARTIFACT"))
        elif a["inferred_layer_basis"] != "SECTION_28_NAME":
            out.append(Unit(n, Assessability.NOT_APPLICABLE, None, "NAME_DOES_NOT_ENCODE_LAYER"))
        elif ctx.idx.usable_get("naming.artifact_name_contract", n) is None:
            out.append(Unit(n, Assessability.PARTIALLY_ASSESSED, Cause.NOT_IN_SNAPSHOT, None, EvidenceClass.C1))
        else:
            out.append(Unit(n, Assessability.ASSESSED))
    return out


def _units_flat_central(ctx: Ctx):
    f = ctx.idx.usable_get("harmonization.composition_artifacts", "closure")
    if f is None:
        return None
    return [Unit(a["artifact"], Assessability.NOT_APPLICABLE, None, "DECLARED_DIMENSION") if a["declared_role"]["declares_dimension"]
            else Unit(a["artifact"], Assessability.ASSESSED) for a in f.value["artifacts"]]


def _per_artifact_units(ctx: Ctx, fact_ids: tuple[str, ...], closure_fact: str | None = None):
    if closure_fact is not None and ctx.idx.usable_get(closure_fact, "closure") is None:
        return None
    base = ctx.idx.all(fact_ids[0])
    if not base:
        return None
    out = []
    for f in base:
        if f.assessability is Assessability.NOT_APPLICABLE:
            out.append(_unit_of_fact(f))
        elif all(ctx.idx.usable_get(fid, f.subject) is not None for fid in fact_ids):
            out.append(Unit(f.subject, Assessability.ASSESSED))
        else:
            out.append(Unit(f.subject, Assessability.NOT_ASSESSED, f.cause or Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT, None, EvidenceClass.C2))
    return out


def _units_vocab_mix(ctx: Ctx):
    return _per_artifact_units(ctx, ("naming.field_name_forms",), "naming.same_normalized_label_multiple_technical_names")


def _units_opaque(ctx: Ctx):
    return _per_artifact_units(ctx, ("naming.field_name_forms", "naming.field_name_contract"))


def _units_label(ctx: Ctx):
    return _per_artifact_units(ctx, ("naming.field_name_contract",))


def _units_am_underuse(ctx: Ctx):
    fs = ctx.idx.all("am.contract_inventory")
    return [Unit(f.subject, Assessability.ASSESSED if f.assessability is Assessability.ASSESSED else Assessability.NOT_ASSESSED,
                 f.cause or None) for f in fs] if fs else None


_RULE_SURFACES: tuple[SurfaceSpec, ...] = (
    SurfaceSpec("rule.direct_raw_table_access", _SRC, R, REQ, "can DIRECT_RAW_TABLE_ACCESS be evaluated? only remote boundary leaves can; a local leaf's provenance is not observable (Q1)",
                units=_units_direct_raw, signal_id="DIRECT_RAW_TABLE_ACCESS"),
    SurfaceSpec("rule.duplicated_remote_import", _ING, R, REQ, "can DUPLICATED_REMOTE_IMPORT be evaluated over every boundary source?",
                units=_units_duplicates, signal_id="DUPLICATED_REMOTE_IMPORT"),
    SurfaceSpec("rule.layer_boundary_review", _ING, R, REQ, "can LAYER_BOUNDARY_REVIEW be evaluated over every boundary source?",
                units=_units_layer_boundary, signal_id="LAYER_BOUNDARY_REVIEW"),
    SurfaceSpec("rule.role_layer_divergence", _ING, R, REQ, "ROLE_LAYER_DIVERGENCE applies only to artifacts whose NAME encodes a layer",
                units=_units_role_layer, signal_id="ROLE_LAYER_DIVERGENCE"),
    SurfaceSpec("rule.pass_through_review", _PRO, R, REQ, "PASS_THROUGH_REVIEW needs a visible derivation flag; leaf -> first-consumer edges are not evaluated by the rule",
                units=_units_pass_through, signal_id="PASS_THROUGH_REVIEW"),
    SurfaceSpec("rule.repeated_derivation_in_lineage", _PRO, R, REQ, "REPEATED_DERIVATION_IN_LINEAGE needs a visible derivation flag in each artifact",
                units=_units_rederivation, signal_id="REPEATED_DERIVATION_IN_LINEAGE"),
    SurfaceSpec("rule.flat_central_assembly_review", _HAR, R, REQ, "FLAT_CENTRAL_ASSEMBLY_REVIEW over the composition artifacts of the closure",
                units=_units_flat_central, signal_id="FLAT_CENTRAL_ASSEMBLY_REVIEW"),
    SurfaceSpec("rule.am_semantic_underuse_review", _AM, R, REQ, "AM_SEMANTIC_UNDERUSE_REVIEW over the terminal analytic model",
                units=_units_am_underuse, signal_id="AM_SEMANTIC_UNDERUSE_REVIEW", na_when=_not_am_root),
    SurfaceSpec("rule.field_vocabulary_mix", _NAM, R, REQ, "FIELD_VOCABULARY_MIX needs exposed fields and the shared-label evidence",
                units=_units_vocab_mix, signal_id="FIELD_VOCABULARY_MIX"),
    SurfaceSpec("rule.opaque_field_name", _NAM, R, REQ, "OPAQUE_FIELD_NAME needs exposed fields with forms and labels",
                units=_units_opaque, signal_id="OPAQUE_FIELD_NAME"),
    SurfaceSpec("rule.label_not_enriching", _NAM, R, REQ, "LABEL_NOT_ENRICHING needs exposed fields with labels",
                units=_units_label, signal_id="LABEL_NOT_ENRICHING"),
)

SURFACE_REGISTRY: tuple[SurfaceSpec, ...] = (*_FACT_SURFACES, *_RULE_SURFACES)


# ───────────────────────────── evaluacion de superficies ─────────────────────────────
def _gap_classes(units: list[Unit]) -> tuple[EvidenceClass, ...]:
    seen = set()
    for u in units:
        if u.assessability in (Assessability.NOT_APPLICABLE, Assessability.ASSESSED):
            continue
        c = u.evidence_class if u.evidence_class in _OPEN_GAP_CLASSES else _class_of_cause(u.cause)
        if c in _OPEN_GAP_CLASSES:
            seen.add(c)
    return tuple(sorted(seen, key=lambda c: c.value))


def _surface_units(spec: SurfaceSpec, ctx: Ctx) -> tuple[list[Unit], str | None, bool]:
    """(unidades, razon NA de toda la superficie, poblacion_determinable)."""
    if spec.na_when is not None:                              # N/A de TODA la superficie (hechos o regla)
        reason = spec.na_when(ctx)
        if reason:
            return [], reason, True
    if spec.kind is F:
        fs = [f for fid in spec.fact_ids for f in ctx.idx.all(fid)]
        if not ctx.area_present[spec.area]:
            return [], None, False
        if not fs:
            return [], ("NO_UNITS_IN_SCOPE" if spec.per_unit else None), spec.per_unit
        return [_unit_of_fact(f) for f in fs], None, True
    units = spec.units(ctx)
    return (units or []), None, units is not None


def assess_surface(spec: SurfaceSpec, ctx: Ctx) -> SurfaceAssessment:
    units, na_reason, known = _surface_units(spec, ctx)
    if na_reason:
        state, cause, reason, counts = Assessability.NOT_APPLICABLE, None, na_reason, CoverageCounts(not_applicable=1)
    elif not known:
        state, cause, reason, counts = Assessability.NOT_ASSESSED, Cause.NOT_IN_SNAPSHOT, None, CoverageCounts()
        units = []
    elif not units:
        # poblacion de una REGLA determinada y vacia: el patron no puede aparecer y esa ausencia SI esta evaluada
        state, cause, reason, counts = Assessability.ASSESSED, None, None, CoverageCounts()
    else:
        state, cause, reason, counts = _aggregate([(u.assessability, u.cause, u.not_applicable_reason) for u in units])
    result = finding_count = None
    if spec.kind is R:
        finding_count = ctx.finding_counts.get(spec.signal_id or "", 0)
        result = (RuleResult.PATTERN_FOUND if finding_count else RuleResult.ASSESSED_ABSENCE if state is Assessability.ASSESSED
                  else RuleResult.NOT_APPLICABLE if state is Assessability.NOT_APPLICABLE else RuleResult.ABSENCE_NOT_ESTABLISHED)
    gap = _gap_classes(units) if state not in (Assessability.ASSESSED, Assessability.NOT_APPLICABLE) else ()
    if state is Assessability.NOT_ASSESSED and not gap and cause:
        c = _class_of_cause(cause)
        gap = (c,) if c in _OPEN_GAP_CLASSES else ()
    return SurfaceAssessment(spec.surface_id, spec.area, spec.kind, spec.role, state, cause, reason, counts, gap, tuple(units), spec.rationale, result, finding_count)


# ───────────────────────────── composicion ─────────────────────────────
def _macro_state(surfaces: list[SurfaceAssessment]) -> MacroDimensionState:
    app = [s for s in surfaces if s.assessability is not Assessability.NOT_APPLICABLE]
    if not app:
        return MacroDimensionState.NOT_APPLICABLE
    if all(s.assessability in (Assessability.NOT_ASSESSED, Assessability.INDETERMINATE) for s in app):
        return MacroDimensionState.COMPLETELY_NOT_ASSESSED
    return MacroDimensionState.HAS_ASSESSED_EVIDENCE


def _sum_counts(parts: Iterable[CoverageCounts]) -> CoverageCounts:
    p = list(parts)
    return CoverageCounts(sum(c.assessed for c in p), sum(c.partially_assessed for c in p), sum(c.not_assessed for c in p),
                          sum(c.indeterminate for c in p), sum(c.not_applicable for c in p))


def _surface_counts(surfaces: list[SurfaceAssessment]) -> CoverageCounts:
    """Cobertura: UNA entrada por superficie REQUERIDA, segun su estado agregado (nunca por Fact ni por finding)."""
    c = Counter(s.assessability for s in surfaces if s.role is SurfaceRole.REQUIRED)
    return CoverageCounts(c[Assessability.ASSESSED], c[Assessability.PARTIALLY_ASSESSED], c[Assessability.NOT_ASSESSED],
                          c[Assessability.INDETERMINATE], c[Assessability.NOT_APPLICABLE])


def _level(value: float | None, cuts: tuple[float, float]) -> ConfidenceLevel:
    if value is None:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.HIGH if value >= cuts[0] else ConfidenceLevel.MEDIUM if value >= cuts[1] else ConfidenceLevel.LOW


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 6)


def _confidence_factors(surfaces: list[SurfaceAssessment], ctx: Ctx, coverage: CoverageCounts) -> tuple[ConfidenceFactor, ...]:
    E, C, X, P = ConfidenceAxis.EPISTEMIC_RELIABILITY, ConfidenceAxis.COMPLETENESS, ConfidenceAxis.CONTEXTUALITY, ConfidenceAxis.CROSS_PLANE
    cuts = CONFIDENCE_CUTS["reliable_share"]
    out: list[ConfidenceFactor] = []

    def contributing(name: str, share: float | None, detail: str) -> None:
        out.append(ConfidenceFactor(E, name, share, detail, True, None if share is None else _level(share, cuts)))

    # ── EPISTEMICOS que contribuyen (marcas explicitas del modelo canonico) ──
    idents = [f.value["identity"] for f in ctx.idx.usable("naming.artifact_name_contract")]
    heuristic = sum(1 for i in idents if i["certainty"] == "HEURISTIC")
    unknown = sum(1 for i in idents if i["identity"] == "UNKNOWN")
    contributing("non_heuristic_identity", _share(len(idents) - heuristic, len(idents)),
                 f"{heuristic} of {len(idents)} artifact identities rest on a HEURISTIC rule (the rest are PROVEN or UNKNOWN)")
    contributing("resolved_provenance", _share(len(idents) - unknown, len(idents)),
                 f"{unknown} of {len(idents)} artifacts have unresolved (UNKNOWN) provenance")
    concl = [s for s in surfaces if s.kind is SurfaceKind.RULE_EVALUATION and s.result in (RuleResult.PATTERN_FOUND, RuleResult.ASSESSED_ABSENCE)]
    complete = sum(1 for s in concl if s.assessability is Assessability.ASSESSED)
    contributing("complete_rule_inputs", _share(complete, len(concl)),
                 f"{complete} of {len(concl)} rule conclusions were reached with fully assessed inputs")
    refs_fx = ctx.idx.usable("am.closure_reference_resolution")
    n_amb = sum(f.value["counts"]["AMBIGUOUS"] for f in refs_fx)
    n_res = sum(f.value["counts"]["RESOLVED"] for f in refs_fx)
    if refs_fx:                                          # sin hechos de resolucion del cierre (fixtures historicos) el factor no existe: no se inventa un 1.0
        contributing("unambiguous_closure_references", _share(n_res, n_res + n_amb),
                     f"{n_amb} of {n_res + n_amb} references of the closure members resolve only by an ambiguous (case-insensitive) identity match")
    # ── EPISTEMICO pero NO contribuye: A vs B no es una marca de fiabilidad del modelo canonico (B = derivacion explicita sobre metadata observable) ──
    fact_surf = [s for s in surfaces if s.kind is SurfaceKind.FACT_EVIDENCE and s.role is SurfaceRole.REQUIRED
                 and s.assessability in (Assessability.ASSESSED, Assessability.PARTIALLY_ASSESSED)]
    direct = sum(1 for s in fact_surf if s.units and all(u.evidence_class is EvidenceClass.A for u in s.units if u.assessability is Assessability.ASSESSED))
    out.append(ConfidenceFactor(E, "directly_observed_surfaces", _share(direct, len(fact_surf)),
                                f"{direct} of {len(fact_surf)} assessed fact surfaces rest only on class A evidence; diagnostic only: class B is a deterministic "
                                "derivation over observable metadata, not a reliability marker", False))
    # ── COMPLETITUD (pertenece a COVERAGE): diagnostico ──
    out.append(ConfidenceFactor(C, "strict_coverage", coverage.ratio_strict, "belongs to COVERAGE; never an input to confidence", False))
    out.append(ConfidenceFactor(C, "inclusive_coverage", coverage.ratio_inclusive, "belongs to COVERAGE; never an input to confidence", False))
    req = [s for s in surfaces if s.role is SurfaceRole.REQUIRED and s.assessability is not Assessability.NOT_APPLICABLE]
    open_gap = [s for s in req if s.assessability is not Assessability.ASSESSED and s.open_gap_classes]
    out.append(ConfidenceFactor(C, "open_evidence_gap_surfaces", len(open_gap),
                                f"{len(open_gap)} of {len(req)} applicable required surfaces carry an open C1/C2/D evidence gap; completeness, not reliability", False))
    # ── CONTEXTUALIDAD (pertenece a CONTEXT REQUIRED / REVIEW): diagnostico ──
    questions = {s.signal_id for s in ctx.evaluation.signals if s.kind in (SignalKind.OBSERVATION, SignalKind.REVIEW)}
    ctx_open = [s for s in surfaces if s.role is SurfaceRole.CONTEXT_ONLY and s.assessability is Assessability.NOT_ASSESSED]
    out.append(ConfidenceFactor(X, "open_context_questions", len(questions) + len(ctx_open),
                                "distinct open context questions (observation/review kinds with findings + open context-only surfaces); "
                                "unresolved context is not unreliable evidence", False))
    out.append(ConfidenceFactor(X, "open_context_occurrences", len(ctx.evaluation.observations) + len(ctx.evaluation.review_items),
                                "occurrence-level observations and review items awaiting context", False))
    # ── PLANO CRUZADO: ni premio ni castigo global ──
    attached = [f for f in ctx.facts if f.plane is Plane.PROCESSING and f.value is not None
                and (f.fact_id == "ingestion.processing_reference" or (f.fact_id == "ingestion.dataflow_boundary" and f.value.get("references")))]
    citing = [f for f in ctx.facts if f.plane is Plane.MODELING and any(r.plane is Plane.PROCESSING for r in f.refs)]
    out.append(ConfidenceFactor(P, "processing_evidence_attached", len(attached),
                                f"{len(attached)} Processing-plane facts attached to the closure; {len(citing)} Modeling facts cite Processing evidence. "
                                "Complementary evidence never makes a Modeling NOT_ASSESSED surface ASSESSED and is no global confidence reward", False))
    return tuple(out)


def _confidence(factors: tuple[ConfidenceFactor, ...], applicable_scope: bool) -> Confidence:
    """Confianza agregada = MINIMO de los factores EPISTEMICOS que contribuyen (solo ese eje); nada mas la determina."""
    if not applicable_scope:
        return Confidence(ConfidenceLevel.LOW, (ConfidenceDriver(ConfidenceDriverName.EPISTEMIC_RELIABILITY, ConfidenceLevel.LOW,
                                                                 "scope not applicable: no assessed evidence to qualify"),))
    live = [f for f in factors if f.axis is ConfidenceAxis.EPISTEMIC_RELIABILITY and f.contributes_to_confidence and f.level is not None]
    if not live:
        return Confidence(ConfidenceLevel.LOW, (ConfidenceDriver(ConfidenceDriverName.EPISTEMIC_RELIABILITY, ConfidenceLevel.LOW,
                                                                 "no assessed evidence carries a reliability marker to qualify"),))
    level = min((f.level for f in live), key=_LEVEL_ORDER.index)
    detail = "epistemic reliability of the ASSESSED evidence (min of components): " + "; ".join(f"{f.name}={f.value} {f.level.value}" for f in live)
    return Confidence(level, (ConfidenceDriver(ConfidenceDriverName.EPISTEMIC_RELIABILITY, level, detail),))


def compose_assessment(scope: ProfileScope, facts: Iterable[Fact], evaluation: SignalEvaluation | None = None) -> AssessmentComposition:
    """Compone assessability + cobertura + confianza. Pura y determinista; recibe SOLO hechos, senales y scope."""
    fl = sorted(facts, key=lambda f: (f.area.value, f.fact_id, f.subject))
    ev = evaluation if evaluation is not None else evaluate_signals(fl)
    ctx = Ctx(fl, ev, scope.root_type)
    applicable_scope = scope.applicability.state is Applicability.APPLICABLE
    surfaces = [assess_surface(spec, ctx) for spec in SURFACE_REGISTRY] if applicable_scope else []

    cards = []
    by_area_counts: dict[Area, CoverageCounts] = {}
    for area in ASSESSED_AREAS:
        area_surfaces = [s for s in surfaces if s.area is area]
        area_req = [s for s in area_surfaces if s.role is SurfaceRole.REQUIRED]
        facts_a = tuple(f for f in fl if f.area is area)
        sigs = tuple(s for s in ev.signals if s.area is area)
        by_area_counts[area] = _surface_counts(area_surfaces)
        if not applicable_scope:
            cards.append(AreaCard(area, ApplicabilityDecision(Applicability.NOT_APPLICABLE, ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL),
                                  Assessability.NOT_APPLICABLE, facts_a, sigs))
            continue
        state, cause, reason, _ = _aggregate([(s.assessability, s.cause, s.not_applicable_reason) for s in area_req])
        limited = [s for s in area_surfaces if s.role in (SurfaceRole.REQUIRED, SurfaceRole.OPTIONAL_DEEPER_EVIDENCE, SurfaceRole.CROSS_PLANE_COMPLEMENT)]
        not_obs = tuple(sorted({s.cause for s in limited if s.cause in (Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT, Cause.C2_EXPERT_ONLY)
                                and s.assessability is not Assessability.ASSESSED}, key=lambda c: c.value))      # la limitacion C2 sigue visible aunque no reduzca la cobertura
        if state is Assessability.NOT_APPLICABLE:
            cards.append(AreaCard(area, ApplicabilityDecision(Applicability.NOT_APPLICABLE, ApplicabilityReason.SYSTEM_ARTIFACT), state, facts_a, sigs))
        else:
            cards.append(AreaCard(area, ApplicabilityDecision(Applicability.APPLICABLE), state, facts_a, sigs, not_obs,
                                  cause if state in (Assessability.NOT_ASSESSED, Assessability.PARTIALLY_ASSESSED, Assessability.INDETERMINATE) else None))
    overall = _sum_counts(by_area_counts.values())
    macros = []
    for macro in MacroDimension:
        ss = [s for s in surfaces if s.role is SurfaceRole.REQUIRED and MACRO_DIMENSION_OF_AREA[s.area] is macro]
        macros.append(MacroDimensionStatus(macro, _macro_state(ss) if applicable_scope else MacroDimensionState.NOT_APPLICABLE))
    coverage = CoverageReport(overall, tuple(AreaCoverage(a, by_area_counts[a]) for a in ASSESSED_AREAS), tuple(macros))
    factors = _confidence_factors(surfaces, ctx, overall) if applicable_scope else ()
    return AssessmentComposition(
        scope, tuple(cards), tuple(surfaces), coverage, _confidence(factors, applicable_scope), factors,
        ev.observations, ev.review_items, ev.signals, ev.findings, signals_ruleset=ev.ruleset_version)
