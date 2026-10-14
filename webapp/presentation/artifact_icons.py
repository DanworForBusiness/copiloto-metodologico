"""
artifact_icons.py — registro CENTRAL de íconos de artefactos de SAP Datasphere (gramática visual del Copiloto).

SVG PROPIOS (formas y semántica inspiradas en las de Datasphere; no son sus recursos): viewBox 24×24, solo trazos (``stroke: currentColor``, 1.6, puntas redondeadas), sin imágenes,
sin scripts, sin enlaces externos ni colores fijos: el color lo pone el CSS (`.ai-icon` en tools.css → dorado del Hub, legible en tema claro y oscuro).

Tres ejes DISTINTOS (nunca se mezclan):
  · TIPO de artefacto  (`table`, `graphical_view`, `sql_view`, `er_model`, `analytic_model`, `data_flow`, `replication_flow`, `transformation_flow`, `task_chain`, `data_access_control`);
  · VARIANTE técnica   (p. ej. tabla `delta` = tabla técnica de captura de cambios; es una marca sobre el mismo tipo, no otro tipo);
  · ESTADO de evidencia (comprobado / no verificado): lo decide quien dibuja con CSS (`is-unverified`), no el ícono.

El tipo NUNCA se infiere del prefijo del nombre: solo de metadata (`artefacto_tecnico` del Catálogo, `viewType` de la vista o el tipo de objeto del manifest del CLI). Sin metadata
suficiente `artifact_type()` devuelve None y quien llama cae a su representación neutra.

Uso (hoy solo «Orquestación de ingesta»; el resto de los diagramas puede migrar después sin tocar este módulo): `icon_group("replication_flow", x=8, y=12, size=18)` → `<g>` listo para
incrustar en un SVG.
"""
from __future__ import annotations

import html
from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactType:
    key: str
    label: str            # nombre visible (es-AR) y texto accesible
    glyph: str            # contenido SVG 24×24, solo trazos


_TYPES = (
    ArtifactType("table", "Tabla", '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 14.5h18M9 4v16M15 4v16"/>'),
    ArtifactType("graphical_view", "Vista gráfica", '<rect x="3" y="4" width="14" height="12" rx="2"/><path d="M3 8.5h14M3 12h14M8 4v12"/><rect x="12.5" y="12.5" width="8.5" height="8.5" rx="1.8" class="ai-bg"/><path d="M16.75 14.6v4.3M14.6 16.75h4.3"/>'),
    ArtifactType("sql_view", "Vista SQL", '<ellipse cx="12" cy="5.8" rx="7" ry="2.6"/><path d="M5 5.8v5.6c0 1.45 3.1 2.6 7 2.6s7-1.15 7-2.6V5.8"/><text x="12" y="21.4" text-anchor="middle" class="ai-text">SQL</text>'),
    ArtifactType("er_model", "Modelo entidad-relación", '<rect x="3" y="3" width="6.5" height="6.5" rx="1.5"/><rect x="14.5" y="3" width="6.5" height="6.5" rx="1.5"/><rect x="8.75" y="14.5" width="6.5" height="6.5" rx="1.5"/><path d="M6.25 9.5v2a1.5 1.5 0 0 0 1.5 1.5H12v1.5M17.75 9.5v2a1.5 1.5 0 0 1-1.5 1.5H12"/>'),
    ArtifactType("analytic_model", "Modelo analítico", '<path d="M12 2.5l8 4.5v10l-8 4.5-8-4.5V7z"/><path d="M12 12l8-5M12 12L4 7M12 12v9.5"/>'),
    ArtifactType("data_flow", "Data Flow", '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M8 10l3 2-3 2M13 10l3 2-3 2"/>'),
    ArtifactType("replication_flow", "Replication Flow", '<rect x="3" y="4" width="18" height="4.2" rx="2.1"/><rect x="3" y="9.9" width="18" height="4.2" rx="2.1"/><rect x="3" y="15.8" width="18" height="4.2" rx="2.1"/><path d="M6.4 6.1h.01M6.4 12h.01M6.4 17.9h.01"/>'),
    ArtifactType("transformation_flow", "Transformation Flow", '<path d="M3.5 13.5l4.2-8 4.2 8z"/><rect x="13.5" y="5" width="7" height="7" rx="1.2"/><path d="M7.5 19.5a6.2 6.2 0 0 0 11.2-3.4M18.7 12.7v3.4h-3.4"/>'),
    ArtifactType("task_chain", "Task Chain", '<rect x="3" y="4" width="12" height="16" rx="2"/><path d="M6.2 9l1.5 1.5L10.6 7.6M6.2 15h5.4M15 8h2.2M15 16h2.2M17.2 8v8"/><circle cx="19.6" cy="8" r="1.5"/><circle cx="19.6" cy="16" r="1.5"/>'),
    ArtifactType("data_access_control", "Data Access Control", '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/><rect x="9" y="14" width="6" height="5" rx="1"/><path d="M10.2 14v-1.4a1.8 1.8 0 0 1 3.6 0V14"/>'),
)
ARTIFACT_TYPES: dict[str, ArtifactType] = {t.key: t for t in _TYPES}

# Variantes técnicas: una MARCA sobre el mismo tipo (no otro tipo).
_VARIANT_MARK = {"delta": '<path d="M17.2 21.5l2.3-4 2.3 4z" class="ai-mark"/>'}     # Δ = tabla técnica de captura de cambios

# Metadata CONFIABLE → tipo. `artefacto_tecnico` es el campo del front-matter que genera el Catálogo; `object_type` es el tipo de objeto del manifest del CLI.
_BY_ARTEFACTO_TECNICO = {
    "Tabla local": "table", "Tabla remota": "table", "Analytic Model": "analytic_model", "Data Flow": "data_flow", "Replication Flow": "replication_flow",
    "Transformation Flow": "transformation_flow", "Entity Relationship Model": "er_model", "Data Access Controls": "data_access_control", "Task Chain": "task_chain",
}
_BY_OBJECT_TYPE = {
    "local-tables": "table", "remote-tables": "table", "analytic-models": "analytic_model", "data-flows": "data_flow", "replication-flows": "replication_flow",
    "transformation-flows": "transformation_flow", "task-chains": "task_chain",
}
_BY_VIEW_TYPE = {"GRAPHICAL": "graphical_view", "SQL": "sql_view"}


def artifact_type(*, artefacto_tecnico: str | None = None, object_type: str | None = None, view_type: str | None = None) -> str | None:
    """Clave de tipo según metadata; None si no alcanza. Una VISTA solo se clasifica con su `viewType` (GRAPHICAL | SQL): sin él es None, nunca se adivina por nombre."""
    if artefacto_tecnico == "Vista" or object_type == "views":
        return _BY_VIEW_TYPE.get(str(view_type or "").upper())
    if artefacto_tecnico in _BY_ARTEFACTO_TECNICO:
        return _BY_ARTEFACTO_TECNICO[artefacto_tecnico]
    return _BY_OBJECT_TYPE.get(str(object_type or ""))


def label_of(type_key: str) -> str:
    return ARTIFACT_TYPES[type_key].label


def icon_group(type_key: str, *, variant: str | None = None, x: float = 0, y: float = 0, size: float = 20) -> str:
    """`<g>` SVG (decorativo: `aria-hidden`; el texto accesible lo aporta el nodo que lo contiene) del tipo pedido, escalado a `size` px y ubicado en (x, y). Tipo o variante
    desconocidos → KeyError/ValueError: es un error de programación, no un dato."""
    t = ARTIFACT_TYPES[type_key]
    if variant is not None and variant not in _VARIANT_MARK:
        raise ValueError(f"variante desconocida: {variant!r}")
    mark = _VARIANT_MARK[variant] if variant else ""
    scale = size / 24
    return (f'<g class="ai-icon ai-icon--{t.key}{" ai-icon--" + variant if variant else ""}" aria-hidden="true" focusable="false" '
            f'transform="translate({x:g} {y:g}) scale({scale:.4g})">{t.glyph}{mark}</g>')


def symbol_markup(type_key: str, *, variant: str | None = None, size: int = 24) -> str:
    """SVG independiente (para leyendas/HTML) de un ícono: mismo glifo, `viewBox` 24×24."""
    t = ARTIFACT_TYPES[type_key]
    return (f'<svg class="ai-icon ai-icon--{t.key}" viewBox="0 0 24 24" width="{size}" height="{size}" role="img" aria-label="{html.escape(t.label, quote=True)}" focusable="false">'
            f'<title>{html.escape(t.label)}</title>{t.glyph}{_VARIANT_MARK[variant] if variant else ""}</svg>')
