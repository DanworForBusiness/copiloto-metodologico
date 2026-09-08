"""
test_reporting_horas_vendidas_full_year.py

Owner ticket "URGENT REPORTING CORRECTNESS — HNP TASK + SOLD HOURS YEAR
ACCUMULATION" §2: sin Mes explícito seleccionado, Horas Vendidas debe usar
el AÑO COMPLETO como horizonte de acumulación/proyección -- Oct/Nov/Dic
del año en curso no deben quedar excluidos solo por ser "futuro" respecto
a la fecha real.

Owner ticket "HORAS VENDIDAS — ENABLE FUTURE MONTH SELECTION" (follow-up):
el fix anterior dejaba el cap operacional vigente apenas el usuario
tildaba explícitamente un mes futuro (pill deshabilitado en la UI + valor
`null` en el dato pese a la selección) -- en Horas Vendidas el mes
SELECCIONADO es siempre autoritativo, sin excepción (a diferencia de
Certificadas/Trabajadas de Rentabilidad, que sí representan trabajo no
ejecutado todavía y CONSERVAN su protección de mes futuro, sin tocar).

Ejercita la lógica REAL de `reporting.js` (horasVendidasSelectedRows/
_yearRangeMonthly/horasVendidasMesPills/pruneHorasVendidasMes) vía el
harness Node — ver reporting_horas_vendidas_full_year.node.js para el
detalle de los invariantes A-J (más Rentabilidad como control negativo,
punto I).
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
