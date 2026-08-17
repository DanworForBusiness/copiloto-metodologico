"""
test_reporting_rentabilidad_chart_tooltip.py — Rentabilidad: transversal
chart tooltip primitive (reuse Asignaciones' custom tooltip experience,
never the native browser title) + Total row on "Detalle por proyecto"
(same criterion as Calendario). Ver
reporting_rentabilidad_chart_tooltip.node.js para las invariantes del motor
real.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_rentabilidad_chart_tooltip.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_rentabilidad_chart_tooltip_and_total_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Rentabilidad chart-tooltip/total invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL RENTABILIDAD CHART-TOOLTIP/TOTAL INVARIANTS PASSED" in result.stdout
