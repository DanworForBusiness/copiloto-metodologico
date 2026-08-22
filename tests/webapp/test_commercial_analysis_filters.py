"""
test_commercial_analysis_filters.py — C5e (ver HANDOFF): filtro de análisis
Etapa/Vendedor + búsqueda de texto libre sobre el detalle de Oportunidades
Activas. Ver commercial_analysis_filters.node.js para las invariantes del
motor real (opciones de Etapa/Vendedor, composición AND, recomputación del
Funnel con Revenue, búsqueda RAW/DISPLAY, contrato "texto libre nunca toca
el Funnel", reset tras cambio de tipo, estados vacíos, y ausencia de
mutación/red para los filtros locales).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_analysis_filters.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_analysis_filters_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Analysis filters invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL ANALYSIS FILTERS INVARIANTS PASSED" in result.stdout
