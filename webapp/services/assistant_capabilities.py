"""Capacidades de consulta estructurada del asistente sobre el Catálogo (paso 1: contrato de resultados + ejecución del inventario).

El asistente RESUME, ayuda a acotar y lleva al Catálogo; la consulta (qué entradas, en qué orden, con qué clasificación) se ejecuta acá, de forma determinista y SOLO con los contratos
gobernados existentes, y devuelve un ``ResultSet`` independiente del render. El texto de la respuesta y las filas del chat se construyen aparte (``assistant_navigation`` y la plantilla
``partials/assistant_result_rows.html``).

Reutiliza, sin copiar nada: ``catalog_navigation`` (universo navegable y orden por mirada Danwor), ``dcs.effective_lob_by_entry`` (clasificación EFECTIVA, por lote) y el resolver de
títulos del propio Catálogo (``presenter.entry_title`` / ``presenter.flow_heading``). No hay inventario, clasificación ni persistencia propios, y las URLs salen SIEMPRE del ``entry_id``
real de la entrada (``presenter.flow_url``), nunca de texto.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from webapp.services import catalog_navigation
from webapp.services import domain_classification_service as dcs

log = logging.getLogger("danwor.assistant_capabilities")


@dataclass(frozen=True)
class ResultItem:
    """Una entrada navegable del Catálogo, lista para una fila compacta del chat."""
    ref: str                # ``entry_id`` real: única identidad de la entrada
    title: str              # título legible del modelo (el MISMO resolver que el encabezado del detalle); nunca el rótulo de dominio legado
    client: str
    lob_label: str          # línea de negocio EFECTIVA («Sin clasificar» / «Clasificación no disponible» incluidos)
    lob_source: str         # MANUAL | AUTOMATIC | UNCLASSIFIED | UNAVAILABLE | "" (clasificación no leída)
    url: str                # detalle de la entrada (``presenter.flow_url``)
    mode: str = "embed"     # cómo se abre: «embed» = panel central por HTMX (mismo mecanismo que las acciones del asistente)


@dataclass(frozen=True)
class ResultSet:
    capability: str                                          # "summary" | "list"
    total: int                                               # entradas del universo tras los filtros
    items: tuple[ResultItem, ...] = ()                       # ya acotado (≤ MAX_LIST); vacío en el resumen
    aggregates: tuple[tuple[str, str, int], ...] = ()        # (grupo, rótulo, n): distribución por línea de negocio EFECTIVA; es pertenencia, no un ranking
    classified: bool = True                                  # False si la clasificación efectiva no se pudo leer (jamás se reemplaza por una supuesta)
    footer: str = ""                                         # nota en texto plano bajo las filas


@dataclass(frozen=True)
class Governed:
    """Lectura gobernada de un lote de entradas: estados de Assessment (títulos) y clasificación efectiva, leídos UNA vez."""
    statuses: dict
    eff: dict


def lob_labels() -> dict[str, str]:
    """id de línea de negocio → rótulo (taxonomía gobernada, «Sin clasificar» incluido) más el rótulo de «no disponible»."""
    labels = {o["id"]: o["label"] for o in dcs.lob_options()}
    labels[dcs.UNAVAILABLE] = "Clasificación no disponible"
    return labels


def governed_read(flows: list) -> Governed | None:
    """Estados + clasificación efectiva por lote; None si no se pudo leer: la clasificación nunca rompe el asistente y jamás se reemplaza por una supuesta."""
    try:
        from webapp.services import assessment_service
        statuses = assessment_service.statuses_for_entries(flows)
        return Governed(statuses, dcs.effective_lob_by_entry(flows, statuses))
    except Exception:  # noqa: BLE001 — se informa como no disponible
        log.exception("inventario del asistente: no se pudo leer la clasificación efectiva")
        return None


def order_entries(scope: list) -> list:
    """Orden determinista: mirada Danwor (plena → parcial), luego cliente, nombre, consumo y ``entry_id`` (independiente del orden de carga)."""
    return sorted(scope, key=lambda e: (catalog_navigation._VERDICT_RANK.get(e.veredicto, 9), (e.cliente or "").lower(), (e.dominio or "").lower(),
                                        [c.lower() for c in e.consumo], e.entry_id))


def distribution(scope: list, eff: dict, labels: dict[str, str]) -> tuple[tuple[str, str, int], ...]:
    """(grupo, rótulo, n) por cantidad (desc) y orden de la taxonomía; «sin clasificar» y «no disponible» siempre al final y rotulados como tales."""
    counts: dict[str, int] = {}
    for e in scope:
        g = eff[e.entry_id]["group"]
        counts[g] = counts.get(g, 0) + 1
    order = list(labels)
    lines = [(g, labels[g], counts[g]) for g in sorted((g for g in counts if g not in (dcs.UNCLASSIFIED, dcs.UNAVAILABLE)), key=lambda g: (-counts[g], order.index(g)))]
    for g in (dcs.UNCLASSIFIED, dcs.UNAVAILABLE):
        if counts.get(g):
            lines.append((g, labels[g], counts[g]))
    return tuple(lines)


def _title(entry: Any, governed: Governed | None) -> str:
    """Título legible del modelo con el resolver del Catálogo; sin lectura gobernada, el resolver del propio Catálogo por artefacto de consumo. Nunca el dominio legado."""
    from webapp.services import assessment_presenter as presenter
    try:
        if governed is not None:
            return presenter.entry_title(governed.statuses[entry.entry_id], entry)[0] or presenter.NO_TERMINAL_TITLE
        return presenter.flow_heading(entry)[0]
    except Exception:  # noqa: BLE001 — un título nunca rompe la respuesta
        log.exception("inventario del asistente: no se pudo resolver el título de %s", getattr(entry, "entry_id", "?"))
        return presenter.NO_TERMINAL_TITLE


def build_items(entries: list, governed: Governed | None, pack: str) -> tuple[ResultItem, ...]:
    """Filas de las entradas MOSTRADAS (≤ MAX_LIST). Identidad = ``entry_id``; título y línea de negocio, de los contratos gobernados."""
    from webapp.services import assessment_presenter as presenter
    items = []
    for e in entries:
        lob = presenter.lob_display(governed.eff[e.entry_id]) if governed is not None else None
        items.append(ResultItem(
            ref=e.entry_id, title=_title(e, governed), client=str(e.cliente or ""),
            lob_label=lob["label"] if lob else "", lob_source=lob["source"] if lob else "",
            url=presenter.flow_url(e, pack)))
    return tuple(items)
