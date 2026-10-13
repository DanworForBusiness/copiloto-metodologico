"""Registro CERRADO de indicadores gobernados del asistente y consulta de ordenamiento por indicador explícito (paso 2).

No existe un «scoring» universal: cada indicador es una medida DISTINTA, con su fuente autoritativa, su tipo, su sentido de comparación, los estados que quedan fuera y su advertencia metodológica.
El asistente solo ORDENA ENTRADAS INDIVIDUALES por UN indicador que el usuario nombró (o eligió en una aclaración); no promedia, no combina indicadores, no convierte niveles ordinales en números y no
agrega por dominio ni por línea de negocio (no hay una regla de agregación gobernada). Los valores y las identidades salen EXCLUSIVAMENTE de los contratos existentes (Juicio persistido, resumen del
Assessment vigente, portabilidad del Catálogo); acá no se recalcula ni se guarda nada. El veredicto legado del Catálogo NO es un indicador de este registro.

Reutiliza: ``assessment_service`` (estados), ``assessment_presenter.judgment_view`` (Juicio), ``client_assessment_portfolio.ADOPTION_LEVELS`` (orden ordinal), ``assessment_presenter.percent`` /
``confidence_label`` (presentación), ``assistant_capabilities`` (contrato de resultados y filas navegables).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable

from core.utils import norm_text
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_context as qctx
from webapp.services import client_assessment_portfolio as portfolio

MAX_LIST = 10

# ── estados de una medición ──────────────────────────────────────────────────────────────────────────────────────────────────────
OK = "OK"
NOT_ASSESSED = "NOT_ASSESSED"          # sin assessment vigente: no hay, no aplica, falló o no es compatible
STALE = "STALE"                        # el assessment está desactualizado: no es el vigente
NOT_DETERMINABLE = "NOT_DETERMINABLE"  # el Juicio vigente no es determinable: no es un valor
NO_VALUE = "NO_VALUE"                  # el contrato no trae un valor utilizable
LEGACY_RULES = "LEGACY_RULES"          # score evaluado con reglas anteriores: no comparable con el vigente
EXCLUSION_TEXT = {                     # estado → (singular, plural) para explicar qué quedó fuera
    NOT_ASSESSED: ("sin assessment vigente", "sin assessment vigente"),
    STALE: ("desactualizada", "desactualizadas"),
    NOT_DETERMINABLE: ("no determinable", "no determinables"),
    NO_VALUE: ("sin valor en el contrato", "sin valor en el contrato"),
    LEGACY_RULES: ("evaluada con reglas anteriores", "evaluadas con reglas anteriores"),
}

BEST_FIRST, WORST_FIRST = "best_first", "worst_first"


@dataclass(frozen=True)
class Measurement:
    state: str
    value: Any = None
    display: str = ""
    evidence: caps.Evidence | None = None


@dataclass(frozen=True)
class Indicator:
    id: str
    label: str                          # rótulo para el usuario (también es la frase canónica de las aclaraciones)
    aliases: tuple[str, ...]            # frases que lo nombran (se comparan normalizadas, por palabra completa)
    source: str                         # fuente autoritativa (documentación; los tests la fijan)
    kind: str                           # "ordinal" | "ratio" | "count" | "score"
    caveat: str                         # advertencia metodológica que se muestra con el resultado
    measure: Callable[[Any, Any, str], Measurement]
    levels: tuple[str, ...] = ()        # ordinal: de MEJOR a peor, tal como lo define la fuente autoritativa
    volume: bool = False                # True: mide VOLUMEN (más/menos), no calidad: «mejor/peor» no aplica

    def phrase(self, order: str) -> str:
        """Frase canónica de comparación que el reconocedor entiende (ida y vuelta): «mejor adopción metodológica», «menos puntos de atención»…"""
        word = ("más" if order == BEST_FIRST else "menos") if self.volume else ("mejor" if order == BEST_FIRST else "peor")
        return f"{word} {self.label.lower()}"

    def order_text(self, order: str) -> str:
        if self.volume:
            return "de más a menos" if order == BEST_FIRST else "de menos a más"
        return "de mejor a peor" if order == BEST_FIRST else "de peor a mejor"


def _date(value: Any) -> str | None:
    """Fecha legible (dd/mm/aaaa) de un instante ISO; None si no hay."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return str(value)[:10]


def _assessment_gate(entry: Any, g: caps.Governed | None) -> tuple[Any, Measurement | None]:
    """(estado, None) si el assessment es el VIGENTE; (None, medición excluida) si no. Misma regla que la síntesis de adopción: solo cuenta lo vigente."""
    status = g.statuses[entry.entry_id] if g is not None else None
    if status is None or status.state == svc.NOT_YET_ASSESSED:
        return None, Measurement(NOT_ASSESSED)
    if status.state == svc.STALE:
        return None, Measurement(STALE)
    if status.state != svc.CURRENT:
        return None, Measurement(NOT_ASSESSED)
    return status, None


def _evidence(source: str, status: Any, entry: Any, pack: str, ruleset: str | None = None) -> caps.Evidence:
    return caps.Evidence(source=source, generated_at=_date(status.generated_at), ruleset=ruleset, url=presenter.assessment_url(entry, pack))


def _measure_adoption(entry: Any, g: caps.Governed | None, pack: str) -> Measurement:
    status, excluded = _assessment_gate(entry, g)
    if excluded:
        return excluded
    jv = presenter.judgment_view(status.judgment)
    if not jv:
        return Measurement(NOT_ASSESSED)
    if not jv["determined"]:
        return Measurement(NOT_DETERMINABLE)
    if jv["level"] not in portfolio.ADOPTION_LEVELS:                                       # nivel desconocido: jamás se inventa una categoría
        return Measurement(NO_VALUE)
    return Measurement(OK, jv["level"], jv["level_label"], _evidence("Juicio metodológico", status, entry, pack, status.judgment.get("ruleset_version")))


def _summary_measure(key: str, source: str, show: Callable[[Any], str], valid: Callable[[Any], bool]) -> Callable[[Any, Any, str], Measurement]:
    def measure(entry: Any, g: caps.Governed | None, pack: str) -> Measurement:
        status, excluded = _assessment_gate(entry, g)
        if excluded:
            return excluded
        summary = status.summary
        if not summary or not valid(summary.get(key)):
            return Measurement(NOT_ASSESSED if not summary else NO_VALUE)
        return Measurement(OK, summary[key], show(summary[key]), _evidence(source, status, entry, pack))
    return measure


_CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")          # de MAYOR a menor confianza (los valores del resumen del Assessment)


def _measure_types(entry: Any, g: caps.Governed | None, pack: str) -> Measurement:
    port = getattr(entry, "portabilidad_bdc", None)
    score = port.get("score") if isinstance(port, dict) else None
    if isinstance(score, bool) or not isinstance(score, int):
        return Measurement(NO_VALUE)
    if not port.get("version"):                                                            # reglas anteriores (v1): no es comparable con el score vigente
        return Measurement(LEGACY_RULES)
    return Measurement(OK, score, f"{score}/100", caps.Evidence("Portabilidad · consistencia de tipos", None, port.get("version"), presenter.flow_url(entry, pack)))


INDICATORS: tuple[Indicator, ...] = (
    Indicator(
        id="adoption", label="Adopción metodológica", kind="ordinal", levels=portfolio.ADOPTION_LEVELS,
        aliases=("adopcion metodologica", "nivel de adopcion", "grado de adopcion", "adopcion", "juicio metodologico", "juicio"),
        source="Juicio metodológico persistido del Assessment vigente (assessment_presenter.judgment_view; orden: client_assessment_portfolio.ADOPTION_LEVELS)",
        caveat="Es un nivel ordinal del Juicio metodológico vigente: no se promedia ni se convierte en número.",
        measure=_measure_adoption),
    Indicator(
        id="coverage", label="Cobertura estricta", kind="ratio",
        aliases=("cobertura estricta", "cobertura"),
        source="Resumen del Assessment vigente: cobertura estricta (assessment_service.headline_summary)",
        caveat="Es la cobertura estricta del Assessment vigente; no se combina con otros indicadores ni es un puntaje global.",
        measure=_summary_measure("strict", "Assessment · cobertura estricta", presenter.percent, lambda v: isinstance(v, (int, float)) and not isinstance(v, bool))),
    Indicator(
        id="confidence", label="Confianza de la evidencia", kind="ordinal", levels=_CONFIDENCE_LEVELS,
        aliases=("confianza de la evidencia", "nivel de confianza", "confianza"),
        source="Resumen del Assessment vigente: nivel de confianza (assessment_service.headline_summary; presentación: presenter.confidence_label)",
        caveat="Es la confianza de la evidencia del Assessment, no un juicio sobre la calidad del modelo: una confianza alta no significa que el modelo sea favorable.",
        measure=_summary_measure("confidence", "Assessment · confianza de la evidencia", presenter.confidence_label, lambda v: v in _CONFIDENCE_LEVELS)),
    Indicator(
        # «Casos a revisar» (`review_occurrences` del Assessment) es el nombre VISIBLE; «puntos de atención» queda solo como alias de reconocimiento. NO son las razones ni las fortalezas del Juicio
        # metodológico (esas son otro conteo) ni las observaciones del Assessment (`observation_occurrences`, otro contador que este registro no ofrece): por eso «observaciones» no es alias.
        id="attention", label="Casos a revisar", kind="count", volume=True,
        aliases=("casos a revisar", "puntos de atencion", "revisiones"),
        source="Resumen del Assessment vigente: casos a revisar (assessment_service.headline_summary.review_occurrences); distinto de las razones y fortalezas del Juicio metodológico",
        caveat="Cuenta los casos a revisar del Assessment, no las razones ni las fortalezas del Juicio metodológico: más casos no equivale necesariamente a peor calidad.",
        measure=_summary_measure("review_occurrences", "Assessment · casos a revisar", lambda n: f"{n} caso{'s' if n != 1 else ''} a revisar",
                                 lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0)),
    Indicator(
        id="types", label="Consistencia de tipos", kind="score",
        aliases=("consistencia de tipos", "consistencia estructural", "consistencia"),
        source="Portabilidad del Catálogo: score 0–100 de consistencia estructural de tipos (CatalogEntry.portabilidad_bdc; presenter: asset_evaluation.portability_view)",
        caveat="Es la consistencia estructural de los tipos (0–100), independiente de la compatibilidad con un destino BDC: un valor alto no implica compatibilidad con cualquier destino. No es un puntaje global.",
        measure=_measure_types),
)
BY_ID = {i.id: i for i in INDICATORS}


def indicators_named(q: str) -> list[str]:
    """Ids de los indicadores que la consulta (YA normalizada) nombra, en el orden del registro. Se compara por palabra completa."""
    padded = f" {q} "
    return [i.id for i in INDICATORS if any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", padded) for a in i.aliases)]


def without_indicator_names(q: str) -> str:
    """La consulta (ya normalizada) sin las frases que nombran un indicador. Sirve para decidir si es metodológica sin que el NOMBRE de un indicador («adopción metodológica», «juicio
    metodológico») la haga pasar por una pregunta de metodología."""
    out = f" {q} "
    for alias in sorted((a for i in INDICATORS for a in i.aliases), key=len, reverse=True):
        out = re.sub(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def canonical_question(indicator: str, order: str, *, client: str | None = None, lob_label: str | None = None, unclassified: bool = False) -> str:
    """Pregunta canónica (fijada por el SERVIDOR, nunca texto del usuario) que una aclaración reenvía al MISMO router. Es reconocible de ida y vuelta (hay un test)."""
    where = (f" de {client}" if client else "") + (" sin clasificar" if unclassified else (f" en {lob_label}" if lob_label else ""))
    return f"¿Qué entradas{where} tienen {BY_ID[indicator].phrase(order)}?"


# ── ordenamiento ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _order_value(ind: Indicator, value: Any, order: str):
    """Clave de orden: nivel ordinal por POSICIÓN en la fuente (nunca convertido en número de negocio); valores numéricos tal cual. El primero es el «mejor» (o «más») salvo `worst_first`."""
    if ind.kind == "ordinal":
        pos = ind.levels.index(value)
        return pos if order == BEST_FIRST else -pos
    return -value if order == BEST_FIRST else value


def _excluded_text(excluded: tuple[tuple[str, int], ...]) -> str:
    parts = [f"{n} {EXCLUSION_TEXT[code][0 if n == 1 else 1]}" for code, n in excluded]
    return ", ".join(parts[:-1]) + (" y " if len(parts) > 1 else "") + parts[-1] if parts else ""


def rank_scope(ind: Indicator, order: str, scope: list, governed: caps.Governed | None, pack: str, limit: int = MAX_LIST, offset: int = 0, scope_key: tuple = ()) -> caps.ResultSet:
    """Ordena las entradas de ``scope`` por UN indicador y devuelve un ``ResultSet`` de solo lectura: filas (≤ limit) con valor y evidencia, cobertura, exclusiones y empates.
    Las entradas sin valor comparable NO se rankean: se cuentan y se explican. El orden lo determina el indicador (nunca el veredicto legado); los empates se agrupan y entre ellos el orden es
    estable (cliente, nombre, ``entry_id``), no un ranking."""
    measured = {e.entry_id: ind.measure(e, governed, pack) for e in scope}
    ok = [e for e in scope if measured[e.entry_id].state == OK]
    excluded: dict[str, int] = {}
    for e in scope:
        if measured[e.entry_id].state != OK:
            excluded[measured[e.entry_id].state] = excluded.get(measured[e.entry_id].state, 0) + 1
    ok.sort(key=lambda e: (_order_value(ind, measured[e.entry_id].value, order), (e.cliente or "").lower(), (e.dominio or "").lower(), e.entry_id))
    shown = ok[offset:offset + limit]
    by_value: dict[Any, int] = {}
    for e in ok:
        by_value[measured[e.entry_id].value] = by_value.get(measured[e.entry_id].value, 0) + 1
    ties, seen = [], set()
    for e in shown:
        v = measured[e.entry_id].value
        if by_value[v] > 1 and v not in seen:
            seen.add(v)
            ties.append((measured[e.entry_id].display, by_value[v]))
    cut = None
    after = ok[offset + len(shown):]
    if shown and after:
        last = measured[shown[-1].entry_id].value
        extra = sum(1 for e in after if measured[e.entry_id].value == last)
        cut = (measured[shown[-1].entry_id].display, extra) if extra > 0 else None
    continues = None
    if shown and offset > 0 and measured[ok[offset - 1].entry_id].value == measured[shown[0].entry_id].value:      # la página anterior terminó en el mismo valor: sigue el MISMO empate
        first = measured[shown[0].entry_id].value
        continues = (measured[shown[0].entry_id].display, sum(1 for e in ok[:offset] if measured[e.entry_id].value == first))
    items = []
    for item, e in zip(caps.build_items(shown, governed, pack), shown):
        m = measured[e.entry_id]
        metric = caps.Metric(ind.id, m.value, m.display, m.evidence)
        evidence = " · ".join(x for x in ((m.evidence.source if m.evidence else None), (m.evidence.ruleset if m.evidence else None), (m.evidence.generated_at if m.evidence else None)) if x)
        attrs = replace(item.catalog, metrics=(metric,)) if item.catalog else None
        items.append(replace(item, badge=m.display, note=evidence, catalog=attrs))
    info = caps.RankingInfo(indicator=ind.id, label=ind.label, order=order, order_text=ind.order_text(order), universe=len(scope), evaluated=len(ok),
                            excluded=tuple(sorted(excluded.items())), ties=tuple(ties), cut_in_tie=cut, caveat=ind.caveat,
                            page=offset // max(limit, 1) + 1, pages=max(1, -(-len(ok) // max(limit, 1))), continues_tie=continues)
    rows = [(caps.CATALOG_ENTRY, e.entry_id, repr(measured[e.entry_id].value)) for e in ok]                  # el conjunto ORDENADO completo y el valor que determina su posición
    return caps.ResultSet("rank", total=len(scope), items=tuple(items), footer=footer_text(info, len(after)), catalog=caps.CatalogSummary(classified=governed is not None), ranking=info,
                          fingerprint=qctx.fingerprint(("rank", ind.id, order) + tuple(scope_key), rows))


def footer_text(info: caps.RankingInfo, more: int) -> str:
    """Nota en texto plano bajo las filas: empates, lo que no se compara, lo que falta mostrar y la advertencia metodológica del indicador."""
    parts = []
    if info.continues_tie:
        parts.append(f"Estas entradas continúan el empate de «{info.continues_tie[0]}»: hay {info.continues_tie[1]} más con ese valor en páginas anteriores. El orden entre ellas no implica un ranking.")
    if info.ties:
        shown = "; ".join(f"{n} entradas con «{display}»" for display, n in info.ties[:3])
        parts.append(f"Empatan: {shown}. Entre ellas el orden es estable pero no implica un ranking.")
    if info.cut_in_tie:
        parts.append(f"El corte cae dentro de un empate: hay {info.cut_in_tie[1]} entrada{'s' if info.cut_in_tie[1] != 1 else ''} más con «{info.cut_in_tie[0]}».")
    if info.excluded:
        parts.append(f"No se comparan: {_excluded_text(info.excluded)}.")
    if more > 0:
        parts.append(f"Hay {more} más con valor comparable: acotá por cliente o línea de negocio, o abrí el Catálogo. Pedime «mostrame los siguientes» para ver la página siguiente.")
    parts.append(info.caveat)
    return " ".join(parts)
