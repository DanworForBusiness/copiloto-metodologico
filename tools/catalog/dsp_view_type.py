"""dsp_view_type.py — FUENTE ÚNICA de la clasificación del tipo de vista de SAP Datasphere (GRAPHICAL | SQL | UNKNOWN).

Lo usan, sin copias:
  * el EXPORTADOR (`DSP_CLI_Export_Lineage.LineageExporter.classify_view_type`), que escribe `viewType` en el JSON de cada vista exportada;
  * el Modeling Assessment (vía `modeling_assessment/legacy_bridge`), que lo CONSUME y, si el JSON no lo trae (exports anteriores al campo),
    lo DERIVA con esta misma función.

Módulo puro (solo stdlib, sin imports del proyecto): importable desde el exportador (que puede correr como script) y desde el paquete
del assessment sin arrastrar el legacy.

Precedencia EXACTA del exportador:
    GRAPHICAL  si ``editorSettings.<nombre técnico>.editor.default == "GRAPHICALVIEWBUILDER"``  (gana aunque exista también la query SQL)
    SQL        si no, y la definición trae ``@DataWarehouse.sqlEditor.query``
    UNKNOWN    en cualquier otro caso

Un tipo de vista NO es un juicio de calidad: SQL no es "peor" ni "mejor" que GRAPHICAL.
"""
from __future__ import annotations

from typing import Any

GRAPHICAL = "GRAPHICAL"
SQL = "SQL"
UNKNOWN = "UNKNOWN"
VIEW_TYPES = (GRAPHICAL, SQL, UNKNOWN)

SQL_QUERY_KEY = "@DataWarehouse.sqlEditor.query"
GRAPHICAL_EDITOR = "GRAPHICALVIEWBUILDER"

# Procedencia del tipo (no confundir con la FAMILIA/carpeta del artefacto): el JSON ya traía `viewType` | se derivó de la metadata cruda.
BASIS_STORED = "STORED"
BASIS_DERIVED = "DERIVED"


def classify_view_type(data: dict[str, Any], technical_name: str) -> str:
    editor_settings = data.get("editorSettings", {}) if isinstance(data, dict) else {}
    if isinstance(editor_settings, dict):
        settings = editor_settings.get(technical_name)
        if isinstance(settings, dict):
            editor = settings.get("editor", {})
            if isinstance(editor, dict) and editor.get("default") == GRAPHICAL_EDITOR:
                return GRAPHICAL

    definitions = data.get("definitions", {}) if isinstance(data, dict) else {}
    if not isinstance(definitions, dict):
        return UNKNOWN

    definition = definitions.get(technical_name)
    if not isinstance(definition, dict):
        definition = next((v for v in definitions.values() if isinstance(v, dict)), None)

    if isinstance(definition, dict) and SQL_QUERY_KEY in definition:
        return SQL

    return UNKNOWN


def view_type_of_export(data: dict[str, Any], technical_name: str) -> tuple[str, str]:
    """``(view_type, basis)`` de un export. ``STORED`` si el JSON ya contiene un ``viewType`` reconocido (el exportador lo derivó al exportar);
    ``DERIVED`` si no (export anterior al campo, o valor desconocido): se deriva con ``classify_view_type``."""
    stored = data.get("viewType") if isinstance(data, dict) else None
    if isinstance(stored, str) and stored in VIEW_TYPES:
        return stored, BASIS_STORED
    return classify_view_type(data, technical_name), BASIS_DERIVED
