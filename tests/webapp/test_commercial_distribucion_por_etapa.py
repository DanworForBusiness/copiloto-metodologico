"""
test_commercial_distribucion_por_etapa.py — Comercial / Batch 2B:
"Distribución por Etapa", dos small multiples coordinados (Oportunidades /
Horas ↔ Horas Ponderadas) dentro del drilldown de Oportunidades Activas.
Ver commercial_distribucion_por_etapa.node.js para las invariantes del motor
real (escalas independientes, switch que sólo toca el segundo gráfico,
single-series por barra, tooltip compartido).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_distribucion_por_etapa.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_distribucion_por_etapa_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Distribución por Etapa invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL DISTRIBUCION-POR-ETAPA INVARIANTS PASSED" in result.stdout
