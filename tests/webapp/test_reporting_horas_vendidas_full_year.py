"""
test_reporting_horas_vendidas_full_year.py — owner ticket "URGENT
REPORTING CORRECTNESS — HNP TASK + SOLD HOURS YEAR ACCUMULATION" §2: sin
Mes explícito seleccionado, Horas Vendidas debe usar el AÑO COMPLETO como
horizonte de acumulación/proyección -- Oct/Nov/Dic del año en curso no
deben quedar excluidos solo por ser "futuro" respecto a la fecha real.
Ejercita la lógica REAL de `reporting.js` (horasVendidasSelectedRows/
_yearRangeMonthly) vía el harness Node — ver
reporting_horas_vendidas_full_year.node.js para el detalle de los
invariantes I-N.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_horas_vendidas_full_year.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_horas_vendidas_full_year_js_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Horas Vendidas full-year invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HORAS VENDIDAS FULL-YEAR INVARIANTS PASSED" in result.stdout
