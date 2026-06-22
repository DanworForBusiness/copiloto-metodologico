"""
pipeline/common.py — utilidades compartidas de los scripts de análisis offline.

Centraliza lo que estaba duplicado en analyze_cases / analyze_feedback /
propose_content_updates: el volcado de reportes (`write_report`/`dump_yaml`) y el
modelo por defecto (desde config, así honra OPENAI_MODEL en vez de hardcodear).

Los loaders (`load_cases`/`load_feedback`) NO se centralizan acá: cada script
los necesita con anotaciones/filtros/errores distintos. Unificarlos cambiaría
comportamiento, así que quedan por script hasta un rediseño con contrato común.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from config import BASE_DIR, MODEL  # noqa: F401  (MODEL se re-exporta)

REPORTS_DIR = BASE_DIR / "reports"


def dump_yaml(data) -> str:
    """Serialización YAML canónica de los reportes (unicode, orden preservado)."""
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def write_report(reports_dir: Path, filename: str, data) -> None:
    """Escribe un reporte (str tal cual; dict/list como YAML canónico)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    dest = reports_dir / filename
    if isinstance(data, str):
        dest.write_text(data, encoding="utf-8")
    else:
        dest.write_text(dump_yaml(data), encoding="utf-8")
    try:
        shown = dest.relative_to(BASE_DIR)
    except ValueError:
        shown = dest
    print(f"  [ok] {shown}")
