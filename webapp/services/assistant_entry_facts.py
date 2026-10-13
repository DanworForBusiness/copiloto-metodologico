"""Lo que el asistente SABE de UN modelo, leído de los contratos gobernados (paso 4B.1 / 4B.2). Módulo de recuperación: no identifica modelos (eso es ``assistant_entry_resolver``), no redacta
la respuesta (eso es ``assistant_navigation``) y no calcula nada propio.

Reutiliza, sin copiar: ``Indicator.measure`` (adopción, confianza, cobertura, casos a revisar, consistencia de tipos), ``AssessmentStatus`` (vigencia), ``presenter.judgment_view`` (fundamentos del Juicio),
``presenter.report_view`` (limitaciones de evidencia), ``presenter.STATE_LABELS`` / ``stale_note`` / ``stale_body`` (rótulos y notas de vigencia).

Dimensiones que NO se mezclan: vigencia (estado) · grado (Juicio) · indicadores · limitaciones de evidencia. Política por estado:
  * CURRENT          → valores vigentes con su evidencia. Un Juicio no determinable se dice (no hay nivel) y los demás indicadores siguen su curso.
  * STALE            → el último valor conocido SOLO como histórico y no vigente (``historical=True``); jamás entra a un ranking ni se equipara al actual.
  * NOT_YET_ASSESSED → «sin assessment»: nada que mostrar. NOT_APPLICABLE / FAILED / INCOMPATIBLE_SCHEMA → «sin resultado utilizable» con el motivo del producto.
  * Consistencia de tipos es de la PORTABILIDAD del Catálogo: no depende del estado del Assessment (su fuente se rotula).
Los campos que el producto NO presenta (``what_would_change_the_judgment``, ``what_would_improve_the_solution``, ``cta_candidates``, ``review_candidates``) no se exponen.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_indicators as indicators
from webapp.services import assistant_query as query

log = logging.getLogger("danwor.assistant_entry_facts")

ATTRIBUTE_ORDER = ("adoption", "confidence", "coverage", "types", "attention")      # orden de la ficha (el estado va siempre primero)
MAX_REASONS, MAX_STRENGTHS, MAX_LIMITATIONS = 3, 3, 2
UNREADABLE = -1                                                                     # ``limitations_total`` cuando el informe no se pudo leer
STATE_ATTRIBUTE = "state"                                                           # «estado del assessment»: no es un indicador del registro

# códigos de lo no entregado: distinguen AUSENTE, NO DETERMINABLE, NO APLICABLE, NO DISPONIBLE y DESACTUALIZADO (nunca un «sin dato» genérico)
ABSENT, NOT_APPLICABLE, UNAVAILABLE, INCOMPATIBLE, STALE, NOT_DETERMINABLE, NO_VALUE, LEGACY_RULES = (
    "ABSENT", "NOT_APPLICABLE", "UNAVAILABLE", "INCOMPATIBLE", "STALE", "NOT_DETERMINABLE", "NO_VALUE", "LEGACY_RULES")


def state_info(status: Any) -> caps.StateInfo:
    note = (presenter.stale_note(status) if status.state == svc.STALE else status.reason) or ""
    return caps.StateInfo(state=status.state, label=presenter.STATE_LABELS[status.state], concept=query.concept_for_state(status.state).id, note=note,
                          body=(presenter.stale_body(status) or "") if status.state == svc.STALE else "", generated_at=indicators._date(status.generated_at),
                          current=status.state == svc.CURRENT)


def _historical(ind: indicators.Indicator, entry: Any, governed: caps.Governed, pack: str) -> indicators.Measurement:
    """El valor que el MISMO indicador leería del último informe de un Assessment desactualizado: se reutiliza ``Indicator.measure`` presentando ese informe como vigente SOLO para leerlo. El
    resultado se marca histórico aguas arriba; el ranking vigente sigue usando ``measure`` sobre el estado real (STALE queda excluido)."""
    status = governed.statuses[entry.entry_id]
    shadow = caps.Governed({**governed.statuses, entry.entry_id: dataclasses.replace(status, state=svc.CURRENT)}, governed.eff)
    return ind.measure(entry, shadow, pack)


def _withheld_text(ind: indicators.Indicator, m: indicators.Measurement, status: Any, nd_reason: str) -> tuple[str, str]:
    """(código, texto) de por qué NO se entrega el valor."""
    if m.state == indicators.NOT_DETERMINABLE:
        return NOT_DETERMINABLE, f"No determinable: el Juicio vigente no llegó a un nivel ({nd_reason or 'evidencia insuficiente'})."
    if m.state == indicators.STALE:
        return STALE, "El Assessment está desactualizado y no hay un último valor legible: no se entrega un valor vigente."
    if m.state == indicators.NO_VALUE:
        return NO_VALUE, "El contrato no trae un valor utilizable para esta medida."
    if m.state == indicators.LEGACY_RULES:
        return LEGACY_RULES, "Se calculó con reglas anteriores: no es comparable con el valor vigente."
    s = status.state
    if s == svc.NOT_YET_ASSESSED:
        return ABSENT, "Sin assessment: este modelo todavía no se evaluó."
    reason = f" {status.reason}" if status.reason else ""
    if s == svc.NOT_APPLICABLE:
        return NOT_APPLICABLE, f"No aplica.{reason}"
    if s == svc.FAILED:
        return UNAVAILABLE, f"Assessment no disponible.{reason}"
    if s == svc.INCOMPATIBLE_SCHEMA:
        return INCOMPATIBLE, f"Informe no compatible.{reason}"
    return NO_VALUE, "El Assessment vigente no trae esta medida."


def attributes(entry: Any, governed: caps.Governed, pack: str, ids: tuple[str, ...]) -> tuple[tuple[caps.AttributeValue, ...], tuple[caps.Withheld, ...]]:
    """(valores, no entregados) de los indicadores pedidos, SOLO de ``entry``. Un valor de un Assessment desactualizado se entrega únicamente como histórico."""
    status = governed.statuses[entry.entry_id]
    jv = presenter.judgment_view(status.judgment) if status.judgment else None
    nd_reason = (jv or {}).get("nd_reason") or ""
    values, withheld = [], []
    for iid in ids:
        ind = indicators.BY_ID[iid]
        m = ind.measure(entry, governed, pack)
        historical = False
        if m.state == indicators.STALE:
            past = _historical(ind, entry, governed, pack)
            if past.state == indicators.OK:
                m, historical = past, True
        if m.state == indicators.OK:
            values.append(caps.AttributeValue(iid, ind.label, entry.entry_id, m.display, m.value, m.evidence, ind.caveat, historical))
        else:
            code, text = _withheld_text(ind, m, status, nd_reason)
            withheld.append(caps.Withheld(iid, ind.label, code, text))
    return tuple(values), tuple(withheld)


def fundamentals(entry: Any, governed: caps.Governed, pack: str) -> caps.Fundamentals | None:
    """Fundamentos del Juicio de la entrada con los textos del producto. None si no hay Juicio (sin assessment, no aplica, no disponible, no compatible)."""
    status = governed.statuses[entry.entry_id]
    jv = presenter.judgment_view(status.judgment) if status.state in (svc.CURRENT, svc.STALE) and status.judgment else None
    if not jv:
        return None
    limitations: tuple = ()
    total = 0 if not status.viewable else UNREADABLE                                  # sin informe legible no hay limitaciones que contar; si el informe no se pudo armar, se dice (no se afirma «ninguna»)
    if status.viewable:
        try:
            rows = presenter.report_view(status, entry, pack).get("limitations") or []
            total = len(rows)
            limitations = tuple((r["title"], r["area"], r["state"], r["reason"]) for r in rows[:MAX_LIMITATIONS])
        except Exception:  # noqa: BLE001 — las limitaciones son un complemento: si el informe no se arma, los fundamentos del Juicio igual se entregan
            log.exception("fundamentos del asistente: no se pudieron leer las limitaciones de %s", entry.entry_id)
            total = UNREADABLE
    return caps.Fundamentals(
        entry_id=entry.entry_id, determined=bool(jv["determined"]), level=jv["level"], level_label=jv["level_label"], headline=jv["headline"],
        contracts=tuple((c["label"], c["state_text"]) for c in jv["contracts"]),
        strengths=tuple((s["dimension"], s["text"]) for s in jv["strengths"][:MAX_STRENGTHS]), reasons=tuple((r["dimension"], r["kind"], r["text"]) for r in jv["reasons"][:MAX_REASONS]),
        strengths_total=jv["strengths_count"], reasons_total=jv["reasons_count"], limitations=limitations, limitations_total=total,
        nd_reason=jv["nd_reason"] or "", nd_missing=tuple(m["dimension"] for m in jv["nd_missing"]), historical=status.state == svc.STALE,
        report_url=presenter.assessment_url(entry, pack))


def answer(entry: Any, governed: caps.Governed, pack: str, ids: tuple[str, ...], *, with_fundamentals: bool = False) -> caps.EntryAnswer:
    """La respuesta estructurada sobre UNA entrada: identidad, vigencia, los atributos pedidos (o los no entregados con su motivo), fundamentos si se pidieron y los enlaces."""
    status = governed.statuses[entry.entry_id]
    item = caps.build_items([entry], governed, pack)[0]
    values, withheld = attributes(entry, governed, pack, ids)
    return caps.EntryAnswer(
        entry=item, state=state_info(status), attributes=values, withheld=withheld,
        fundamentals=fundamentals(entry, governed, pack) if with_fundamentals else None,
        links=(("Ver Assessment", presenter.assessment_url(entry, pack)), ("Abrir detalle", presenter.flow_url(entry, pack))))
