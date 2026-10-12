"""API PÚBLICA de presentación del assessment (frontera soportada para consumidores del documento).

El JSON es el contrato canónico; el Markdown (`render_md`) y cualquier UI estructurada son dos representaciones del MISMO
documento. Para que el vocabulario y el formato humano (español) no puedan divergir entre ambas, los consumidores externos
(p. ej. la capa web del Catálogo) obtienen de AQUÍ —y solo de aquí— los helpers de presentación que ya son propiedad de este
paquete.

Este módulo NO razona, NO interpreta hechos, NO altera el esquema ni las reglas: solo expone, con nombres estables y sin
guion bajo, los mapeos y formateadores que ya usa `render_md`. Delega en ellos (dependencia INTERNA del paquete); si `render_md`
cambia un texto, el Markdown y todo consumidor de esta API cambian juntos.

Contrato de las funciones de traducción: reciben el identificador técnico (enum del documento) y devuelven el texto en español;
un identificador desconocido se devuelve tal cual (nunca lanza), de modo que una versión futura del documento no rompe la lectura.
"""
from __future__ import annotations

from typing import Any

from . import render_md as _md

__all__ = [
    "area_title", "macro_dimension_title", "assessability_text", "assessability_plural_text", "execution_state_text",
    "confidence_level_text", "finding_kind_text", "rule_result_text", "unit_label", "is_artifact_name",
    "percent_text", "plural", "value_text", "occurrence_text", "row_key_text", "evidence_not_acquired", "view_composition_text",
    "has_structural_membership", "review_required_text", "context_open_text", "summary_relation_text", "reason_text", "highlight_label", "display_doc", "display_title", "structural_views_caveat", "canonical_signal_id", "boundary_breakdown",
]


def evidence_not_acquired(scope: dict | None) -> list[str]:
    """Familias de evidencia NO adquiridas (etiquetas humanas) según ``scope.evidence_scope`` del documento; [] si se desconoce o fue completo."""
    return _md.evidence_not_acquired(scope)


def _lookup(table: dict[str, str], key: str) -> str:
    return table.get(key, key)


def area_title(area_id: str) -> str:
    """Título en español de un área del assessment (p. ej. ``ANALYTIC_MODEL`` → "Modelo analítico")."""
    return _lookup(_md._AREA_TITLE, area_id)


def macro_dimension_title(macro_id: str) -> str:
    """Título en español de una macro-dimensión (Arquitectura / Modelado, Convención de nombres, Fuente / Extracción)."""
    return _lookup(_md._MACRO_TEXT, macro_id)


def assessability_text(state: str) -> str:
    """Estado de evaluabilidad de una evidencia/área ("Evaluado", "Parcialmente evaluado", "No evaluado", "No aplica")."""
    return _lookup(_md._STATE_TEXT, state)


def assessability_plural_text(state: str) -> str:
    """Igual que ``assessability_text`` en plural y minúscula ("evaluados", "parcialmente evaluados", …)."""
    return _lookup(_md._STATE_PLURAL_TEXT, state)


def execution_state_text(state: str) -> str:
    """Estado de EJECUCIÓN del documento ("Ejecutado", "No aplica", "Indeterminado"); no es la evaluabilidad de la evidencia."""
    return _lookup(_md._EXECUTION_TEXT, state)


def confidence_level_text(level: str) -> str:
    """Nivel de confianza en español y mayúsculas ("ALTA", "MEDIA", "BAJA")."""
    return _lookup(_md._LEVEL_TEXT, level)


def finding_kind_text(kind: str) -> str:
    """Tipo de grupo de hallazgos ("observación", "revisión requerida", "descriptivo")."""
    return _lookup(_md._KIND_TEXT, kind)


def rule_result_text(result: str) -> str:
    """Resultado de una regla en español (incluye la advertencia de ausencia NO establecida)."""
    return _lookup(_md._RULE_RESULT_TEXT, result)


def unit_label(unit: str) -> str:
    """Nombre en español de una unidad de evaluación que NO es un artefacto (p. ej. ``closure``); un artefacto se devuelve tal cual."""
    return _lookup(_md._UNIT_TEXT, unit)


def is_artifact_name(unit: str) -> bool:
    """True si la unidad es un nombre técnico de artefacto (y no una unidad de evaluación como el cierre semántico)."""
    return unit not in _md._UNIT_TEXT


def view_composition_text(total: int, breakdown: list[dict], *, structural: bool = False) -> str:
    """Lectura objetiva de la composición del cierre por tipo de vista (misma función que el Markdown; sin juicio). Con ``structural`` no
    afirma "0 sin clasificación" (en pools planos UNKNOWN no es exhaustivamente observable)."""
    return _md.view_composition_text(total, breakdown, structural=structural)


def highlight_label(h: dict) -> str:
    """Etiqueta visible vigente de un hecho descriptivo (la misma del Markdown)."""
    return _md.highlight_label(h)


def display_doc(doc: dict) -> dict:
    """Documento con los títulos públicos vigentes (la misma normalización que usa el Markdown)."""
    return _md.display_doc(doc)


def display_title(item_id: str, title: str) -> str:
    """Título visible vigente de un hallazgo o regla (el persistido puede traer la redacción anterior)."""
    return _md.display_title(item_id, title)


def structural_views_caveat() -> str:
    """Salvedad breve para pools sin carpetas por tipo: las vistas sin marcador gráfico o SQL no son detectables (misma frase del Markdown)."""
    return _md.STRUCTURAL_VIEWS_CAVEAT


def reason_text(reason: dict) -> str:
    """Texto visible de una razón pública con la redacción vigente (la misma del Markdown)."""
    return _md.reason_text(reason)


def summary_relation_text() -> str:
    """Frase única que relaciona casos a revisar y preguntas abiertas en el Resumen (la misma del Markdown)."""
    return _md.SUMMARY_RELATION_TEXT


def review_required_text(n_occurrences: int, n_groups: int) -> tuple[str, str]:
    """(titular, lectura) de "Casos a revisar" (ocurrencias de los grupos de revisión); misma función que el Markdown."""
    return _md.review_required_text(n_occurrences, n_groups)


def context_open_text(ctx: dict) -> tuple[str, str]:
    """(titular, lectura) de "Preguntas abiertas" (tipos de pregunta distintos); misma función que el Markdown. ``ctx`` = ``summary.context_required``."""
    return _md.context_open_text(ctx)


def has_structural_membership(highlights: list[dict]) -> bool:
    """True si alguna vista del cierre se reconoció solo por marcador estructural (pool plano)."""
    return _md.has_structural_membership(highlights)


def percent_text(value: float | None) -> str:
    """Proporción [0,1] como porcentaje con un decimal ("89.7%"); ``None`` → "n/d". Mismo formato que el Markdown."""
    return _md._pct(value)


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    """"1 ocurrencia" / "3 ocurrencias": mismo criterio de plural que el Markdown."""
    return _md._plural(n, singular, plural_form)


def value_text(value: Any) -> str:
    """Valor descriptivo de un highlight (escalar, lista o dict) como texto legible."""
    return _md._value_text(value)


def occurrence_text(group_id: str, occurrence: dict, *, plain: bool = False) -> str:
    """Una línea en español por ocurrencia, calculada SOLO desde los valores públicos del documento: es el MISMO formateador
    que usa el Markdown. ``plain=True`` elimina los delimitadores de código inline (texto plano para escapar en HTML)."""
    line = _md._occurrence_line(group_id, occurrence)
    return line.replace("`", "") if plain else line


def row_key_text(key: str) -> str:
    """Texto en español de una clave de fila de un hecho descriptivo (``compliant`` → "cumplen"); desconocida → con espacios."""
    return _md._ROW_KEY_TEXT.get(key, key.replace("_", " "))


def canonical_signal_id(signal_id: str) -> str:
    """Id canonico vigente de una senal (un id historico persistido se normaliza al actual); paridad con ``model.canonical_signal_id`` fijada por test."""
    return _md._canonical_signal_id(signal_id)


def boundary_breakdown(doc: dict, original: list[dict]) -> list[dict] | None:
    """Reparto de las hojas de frontera de Ingesta con la procedencia resuelta por la evidencia canónica del documento (misma función que el Markdown); ``None`` si el
    documento no la trae o no permite una partición exacta (se conserva el desglose persistido)."""
    return _md.projected_boundary_breakdown(doc, original)
