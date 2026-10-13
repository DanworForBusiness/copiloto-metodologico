"""Vocabulario gobernado y reconocimiento TIPADO de consultas estructuradas del asistente (paso 4A).

Qué hace: convierte una pregunta ya normalizada en un ``QueryFrame`` (qué niveles, qué estados, qué indicadores, qué cantidad/agrupación y qué operaciones NO soportadas nombró) usando SOLO
vocabulario DERIVADO de los contratos existentes: los niveles ordinales de cada indicador del registro (``assistant_indicators``), los estados reales del Assessment (``assessment_service.STATES``
y sus rótulos en ``assessment_presenter.STATE_LABELS``) y una taxonomía CERRADA de operaciones que el asistente declara no soportar. No hay intents por frase: agregar un nivel o un estado al
contrato lo agrega acá sin tocar el reconocedor.

Qué NO hace: no resuelve clientes ni líneas de negocio (los toma ``assistant_navigation`` del Catálogo), no consulta datos, no decide la respuesta ni escribe nada. Módulo PURO (solo constantes de
contratos importadas): lo que decide qué responder vive en ``assistant_navigation``.

Principio que fija este módulo (4A): ningún término GOBERNADO reconocido puede ignorarse en silencio. El ``QueryFrame`` lo registra; quien ejecuta debe consumirlo o aclarar/declarar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.utils import norm_text
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import assistant_indicators as indicators


# ── Estados: ausencia de EVALUACIÓN ≠ ausencia de RESULTADO UTILIZABLE ───────────────────────────────────────────────────────────────────────
# Correspondencia fijada con los estados REALES de ``AssessmentStatus.state`` (assessment_service):
#   NOT_YET_ASSESSED    → «sin assessment»: nunca se evaluó (un miss de la caché; el assessment se puede generar). Es AUSENCIA DE EVALUACIÓN.
#   NOT_APPLICABLE      → «no aplica»: el flujo no tiene un modelo analítico terminal que evaluar (o tiene varios): la evaluación no corresponde.
#   FAILED              → «assessment no disponible»: la evaluación se intentó y falló.
#   INCOMPATIBLE_SCHEMA → «informe no compatible»: hay un informe guardado que esta versión no sabe leer.
# Los tres últimos son AUSENCIA DE RESULTADO UTILIZABLE (grupo «sin resultado utilizable»): no son «sin assessment» y no se equiparan con él.
CURRENT_ID, STALE_ID = "CURRENT", "STALE"
NOT_EVALUATED_ID = "NOT_EVALUATED"
NOT_APPLICABLE_ID, FAILED_ID, INCOMPATIBLE_ID = "NOT_APPLICABLE", "FAILED", "INCOMPATIBLE_SCHEMA"
NO_USABLE_RESULT_ID = "NO_USABLE_RESULT"
NOT_CURRENT_ID = "NOT_CURRENT"
NOT_DETERMINABLE_ID = "NOT_DETERMINABLE"


@dataclass(frozen=True)
class StateConcept:
    id: str
    label: str                       # rótulo visible
    states: tuple[str, ...]          # estados REALES de AssessmentStatus que cubre; () = condición del Juicio (no es un estado del Assessment)
    terms: tuple[str, ...]           # frases que lo nombran (normalizadas; se comparan por palabra completa)
    canonical: str                   # frase canónica que el servidor usa en preguntas de aclaración (ida y vuelta: hay un test)
    group: bool = False              # concepto que AGRUPA varios estados reales
    judgment: bool = False           # condición del Juicio metodológico vigente (implica el indicador de adopción)


_NOT_CURRENT_STATES = tuple(s for s in svc.STATES if s != svc.CURRENT)
STATE_CONCEPTS: tuple[StateConcept, ...] = (
    StateConcept(CURRENT_ID, presenter.STATE_LABELS[svc.CURRENT], (svc.CURRENT,),
                 ("vigente", "vigentes"), "tienen assessment vigente"),
    StateConcept(STALE_ID, presenter.STATE_LABELS[svc.STALE], (svc.STALE,),
                 ("desactualizado", "desactualizada", "desactualizados", "desactualizadas"), "están desactualizadas"),
    StateConcept(NOT_EVALUATED_ID, presenter.STATE_LABELS[svc.NOT_YET_ASSESSED], (svc.NOT_YET_ASSESSED,),
                 ("sin assessment", "sin assessments", "sin evaluar", "sin evaluacion", "no evaluado", "no evaluada", "no evaluados", "no evaluadas",
                  "no tiene assessment", "no tienen assessment"), "están sin assessment"),
    StateConcept(NOT_APPLICABLE_ID, presenter.STATE_LABELS[svc.NOT_APPLICABLE], (svc.NOT_APPLICABLE,),
                 ("no aplica", "no aplican", "no aplicable", "no aplicables"), "figuran como no aplica"),
    StateConcept(FAILED_ID, presenter.STATE_LABELS[svc.FAILED], (svc.FAILED,),
                 ("assessment no disponible", "assessments no disponibles", "assessment fallido", "assessments fallidos"), "tienen assessment no disponible"),
    StateConcept(INCOMPATIBLE_ID, presenter.STATE_LABELS[svc.INCOMPATIBLE_SCHEMA], (svc.INCOMPATIBLE_SCHEMA,),
                 ("informe no compatible", "informes no compatibles", "assessment no compatible", "assessments no compatibles"), "tienen informe no compatible"),
    StateConcept(NO_USABLE_RESULT_ID, "Sin resultado utilizable", (svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA),
                 ("sin resultado utilizable", "sin resultados utilizables"), "están sin resultado utilizable", group=True),
    StateConcept(NOT_CURRENT_ID, "Sin assessment vigente", _NOT_CURRENT_STATES,
                 ("sin assessment vigente", "sin assessments vigentes", "no tiene assessment vigente", "no tienen assessment vigente", "no vigente", "no vigentes"),
                 "no tienen assessment vigente", group=True),
    StateConcept(NOT_DETERMINABLE_ID, "No determinable", (),
                 ("no determinable", "no determinables"), "son no determinables", judgment=True),
)
STATE_BY_ID = {c.id: c for c in STATE_CONCEPTS}
_ASSESSMENT_CONCEPTS = tuple(c for c in STATE_CONCEPTS if not c.judgment)
# Los conceptos de UN solo estado real (los que particionan el universo en la distribución); el resto son agrupaciones o condiciones del Juicio.
_SINGLE = tuple(c for c in STATE_CONCEPTS if not c.group and not c.judgment)


def unmapped_states() -> tuple[str, ...]:
    """Estados reales del Assessment que NINGÚN concepto de un solo estado cubre. Debe ser vacío: si el contrato suma un estado, la correspondencia tiene que decirlo (hay un test)."""
    covered = {s for c in _SINGLE for s in c.states}
    return tuple(s for s in svc.STATES if s not in covered)


def concept_for_state(state: str) -> StateConcept:
    """El concepto de UN estado real (la partición del universo)."""
    return next(c for c in _SINGLE if state in c.states)


def distribution(states: Iterable[str]) -> tuple[tuple[tuple[str, str, int], ...], tuple[tuple[str, int], ...]]:
    """(líneas, desglose) del estado del Assessment de un conjunto de entradas. Las líneas PARTEN el universo (suma = total): vigente, desactualizado, sin assessment y «sin resultado utilizable»
    (que agrupa no aplica / no disponible / no compatible); el desglose abre ese último grupo por estado real. Solo se listan los que tienen entradas."""
    counts: dict[str, int] = {}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    by_concept = {c.id: sum(counts.get(s, 0) for s in c.states) for c in STATE_CONCEPTS if not c.judgment}
    lines = tuple((cid, STATE_BY_ID[cid].label, by_concept[cid]) for cid in (CURRENT_ID, STALE_ID, NOT_EVALUATED_ID, NO_USABLE_RESULT_ID) if by_concept[cid])
    breakdown = tuple((STATE_BY_ID[cid].label, by_concept[cid]) for cid in (NOT_APPLICABLE_ID, FAILED_ID, INCOMPATIBLE_ID) if by_concept[cid])
    return lines, breakdown


# ── Niveles: derivados de los niveles ordinales de cada indicador ───────────────────────────────────────────────────────────────────────────
def _forms(label: str) -> tuple[str, ...]:
    """Inflexiones de género y número de un rótulo de nivel («Buena» → buena, buenas, bueno, buenos; «Parcial» → parcial, parciales)."""
    base = norm_text(label).strip()
    if base[-1:] in ("a", "o"):
        stem = base[:-1]
        return tuple(dict.fromkeys((stem + "a", stem + "as", stem + "o", stem + "os")))
    return (base, base + "es")


def _build_level_terms() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for ind in indicators.INDICATORS:
        for level, label in ind.level_labels:
            for form in _forms(label):
                if form in out and out[form] != (ind.id, level):
                    raise RuntimeError(f"el término de nivel {form!r} es ambiguo entre indicadores: {out[form]} y {(ind.id, level)}")      # un término de nivel debe pertenecer a UN indicador
                out[form] = (ind.id, level)
    return out


LEVEL_TERMS: dict[str, tuple[str, str]] = _build_level_terms()            # término normalizado → (indicador, nivel)


def level_label(indicator: str, level: str) -> str:
    return dict(indicators.BY_ID[indicator].level_labels).get(level, level)


def level_phrase(indicator: str, levels: Iterable[str]) -> str:
    """«Buena», «Buena o Adecuada» (rótulos visibles, en el orden de la fuente): una sola implementación, la del registro."""
    return indicators.level_text(indicators.BY_ID[indicator], tuple(levels))


# ── Operaciones NO soportadas: taxonomía CERRADA (agregar una clase = agregar una fila) ──────────────────────────────────────────────────────────
AGGREGATE, COMPARE, THRESHOLD, NEGATED_LEVEL = "aggregate", "compare", "threshold", "negated_level"
# «media» agrega solo como «la media de…»: «confianza media» es un NIVEL (no se confunde); «sumá» (imperativo) no es una suma, «suma de…» sí.
_RX_AGGREGATE = re.compile(r"(?<![a-z0-9])(?:promedios?|medianas?|sumar|sumatoria|(?:la )?suma (?:de|del)|acumulad[oa]s?|porcentajes?|porcentual(?:es)?|(?<!confianza )(?<!evidencia )(?:la )?media (?:de|del))(?![a-z0-9])|%")
_RX_TOTAL = re.compile(r"(?<![a-z0-9])(?:en total|total de|totales?)(?![a-z0-9])")
_RX_COMPARE = re.compile(r"(?<![a-z0-9])(?:compara\w*|comparacion(?:es)?|versus|vs)(?![a-z0-9])")
_RX_THRESHOLD = re.compile(r"(?<![a-z0-9])(?:mayor(?:es)?|menor(?:es)?|mas|menos|superior(?:es)?|inferior(?:es)?|al menos|como minimo|como maximo|minimo|maximo)\s*(?:a|de|que)?\s*(?:al\s+)?\d")
_RX_COMPLETE = re.compile(r"(?<![a-z0-9])(?:completa|completo|completas|completos|perfecta|perfecto)(?![a-z0-9])")
_RX_COUNT = re.compile(r"(?<![a-z0-9])(?:cuantos|cuantas|cantidad de|numero de)(?![a-z0-9])")
_RX_GROUP_BY = re.compile(r"(?<![a-z0-9])por\s+(clientes?|lineas? de negocio)(?![a-z0-9])")
_RX_ESTADO = re.compile(r"(?<![a-z0-9])estado(?![a-z0-9])")
_RX_SUPERLATIVE = re.compile(r"(?<![a-z0-9])(?:mas|menos|mayor(?:es)?|menor(?:es)?|mejor(?:es)?|peor(?:es)?)(?![a-z0-9])")
_NEGATORS = ("sin", "no", "ni")


@dataclass(frozen=True)
class QueryFrame:
    """Lo que una pregunta NOMBRA del vocabulario gobernado (no lo que se debe responder)."""
    indicators: tuple[str, ...] = ()                       # ids nombrados, en el orden del registro
    levels: tuple[tuple[str, str], ...] = ()               # (indicador dueño, nivel) en el orden en que aparecen
    states: tuple[str, ...] = ()                           # ids de StateConcept nombrados
    count: bool = False                                    # pidió una cantidad («cuántos», «cantidad de»)
    group_by: str | None = None                            # "client" | "lob" (solo si lo nombró)
    ops: tuple[str, ...] = ()                              # operaciones no soportadas nombradas (taxonomía cerrada)
    total_word: bool = False                               # «en total» / «total de»: agrega SOLO si el indicador mide volumen (lo decide quien ejecuta)
    threshold_word: bool = False                           # «completa»/«perfecta»: calificador numérico sin umbral gobernado (solo importa sobre un indicador no ordinal)
    dimension_state: bool = False                          # nombró la dimensión «estado» (solo vale con alcance: lo decide quien ejecuta)
    superlative: bool = False                              # más/menos/mayor/menor/mejor/peor
    masked: str = ""                                       # el texto normalizado SIN los términos gobernados (para decidir si queda contenido propio)

    @property
    def vocabulary(self) -> bool:
        """¿Hay algo del vocabulario 4A? Los niveles solo cuentan junto a un indicador (colisionan con el veredicto legado: «modelos parciales»)."""
        return bool(self.states or self.ops or self.group_by or self.dimension_state or self.indicators or self.threshold_word or (self.levels and self.indicators))

    def levels_of(self, indicator: str) -> tuple[str, ...]:
        return tuple(lv for owner, lv in self.levels if owner == indicator)


def _mask(text: str, rx: re.Pattern) -> str:
    return rx.sub(" ", text)


def _term_rx(term: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")


_STATE_TERM_RX = sorted(((_term_rx(t), c.id) for c in STATE_CONCEPTS for t in c.terms), key=lambda p: -len(p[0].pattern))
_LEVEL_TERM_RX = sorted(((_term_rx(t), owner_level) for t, owner_level in LEVEL_TERMS.items()), key=lambda p: -len(p[0].pattern))


def parse(question: str) -> QueryFrame:
    """Reconoce el vocabulario gobernado de una pregunta. Orden: operaciones no soportadas → estados (frases largas primero, para que «no determinable» no sea una negación) → niveles →
    cantidad, agrupación y dimensión «estado». Todo lo reconocido se enmascara en ``masked``."""
    w = f" {norm_text(question).strip().lstrip('¿¡')} "
    ops: list[str] = []
    named = indicators.indicators_named(w.strip())
    if _RX_THRESHOLD.search(w):
        ops.append(THRESHOLD)
        w = _mask(w, _RX_THRESHOLD)
    if _RX_AGGREGATE.search(w):
        ops.append(AGGREGATE)
        w = _mask(w, _RX_AGGREGATE)
    if _RX_COMPARE.search(w):
        ops.append(COMPARE)
        w = _mask(w, _RX_COMPARE)
    total_word = bool(_RX_TOTAL.search(w))
    w = _mask(w, _RX_TOTAL)
    states: list[str] = []
    for rx, cid in _STATE_TERM_RX:
        if rx.search(w):
            if cid not in states:
                states.append(cid)
            w = _mask(w, rx)
    levels: list[tuple[str, str]] = []
    negated = False
    for rx, owner_level in _LEVEL_TERM_RX:
        for m in list(rx.finditer(w)):
            if owner_level not in levels:
                levels.append(owner_level)
            before = w[:m.start()].split()[-3:]
            negated = negated or any(tok in _NEGATORS for tok in before)
        w = _mask(w, rx)
    if negated:
        ops.append(NEGATED_LEVEL)
    count = bool(_RX_COUNT.search(w))
    w = _mask(w, _RX_COUNT)
    gm = _RX_GROUP_BY.search(w)
    group_by = ("client" if gm.group(1).startswith("cliente") else "lob") if gm else None
    w = _mask(w, _RX_GROUP_BY)
    dimension_state = bool(_RX_ESTADO.search(w))
    w = _mask(w, _RX_ESTADO)
    threshold_word = bool(_RX_COMPLETE.search(w))
    superlative = bool(_RX_SUPERLATIVE.search(w))
    masked = re.sub(r"\s+", " ", indicators.without_indicator_names(w.strip())).strip()
    return QueryFrame(indicators=tuple(named), levels=tuple(levels), states=tuple(states), count=count, group_by=group_by, ops=tuple(ops), total_word=total_word,
                      threshold_word=threshold_word, dimension_state=dimension_state, superlative=superlative, masked=masked)
