"""
test_reporting_date_range_masking.py — Q2 (owner live QA): Rentabilidad's
monthly Concepto matrix (and the equivalent Horas Vendidas matrix/KPIs)
kept showing non-zero values for months outside the active Desde/Hasta
filter (e.g. Sep-Oct-Nov-Dic with Hasta=agosto). The bug lives in the
frontend month-aggregation helpers (`monthsInRange`/`maskMonthly`/
`sumMonthly`), so this executes the REAL JS engine (via node — no browser/
jsdom framework) against synthetic fixtures rather than asserting on
source strings alone. See reporting_date_range_masking.node.js for the
actual invariants.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_date_range_masking.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_month_masking_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"month-masking invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL MONTH-MASKING INVARIANTS PASSED" in result.stdout


def test_rentabilidad_mensual_and_resumen_apply_the_same_month_mask():
    # Migración Año/Mes/Estado (§-16 #8): Rentabilidad YA NO usa Desde/Hasta.
    # Resumen y la matriz mensual derivan de la MISMA población seleccionada
    # (Año/Mes) — `rentabilidadSelectedRows()` + `rentabilidadSelectedPortfolio()`
    # — una sola población canónica, nunca un segundo cálculo.
    resumen = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidadResumen"):_REPORTING_JS.index("function certEjecGroupTooltip")]
    assert "const portfolio = rentabilidadSelectedPortfolio();" in resumen
    assert "rentabilidadFilteredProyectos()" in resumen  # rows = población seleccionada
    assert "renderRentabilidadMensual(rows, portfolio)" in resumen
    assert "monthsInRange" not in resumen  # Desde/Hasta retirado de Rentabilidad

    mensual = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidadMensual"):_REPORTING_JS.index("function renderRentabilidadResumen")]
    assert "portfolio.vendidas" in mensual and "portfolio.noAsignadas" in mensual
    assert "maskMonthly" not in mensual  # ya no aplica la máscara Desde/Hasta


def test_horas_vendidas_applies_month_mask_to_matrix_and_kpis():
    # Q2: la matriz mensual, el Total, Mantenimiento/Proyecto y Q1-Q4/Hs
    # Estimadas anuales deben derivar de la MISMA `monthMask` — todos
    # se calculan a partir de `totalMonthly`/`maskMonthly(r.monthly, ...)`
    # ya enmascarados, nunca del `monthly` crudo de la fila.
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderHorasVendidas"):_REPORTING_JS.index("function renderComercialParticipacion")]
    assert 'const monthMask = monthsInRange(getFilter("horasvendidas", "desde"), getFilter("horasvendidas", "hasta"));' in fn
    assert "const totalMonthly = sumMonthly(rows, monthMask);" in fn
    assert "maskMonthly(r.monthly, monthMask).reduce((a, b) => a + b, 0), 0);" in fn
    assert "renderTreeRows(stateKey, tree, 0, false, undefined, monthMask)" in fn


def test_calendario_and_no_asignadas_are_not_month_masked_no_date_filter_there():
    # Corrective explícito: Calendario/Capacidad No Asignadas NUNCA tuvieron
    # un filtro Desde/Hasta (ver HANDOFF) — deben seguir llamando
    # `renderTreeRows`/`sumMonthly` SIN pasar `monthMask` (el parámetro
    # `undefined` preserva el comportamiento exacto de siempre, ver harness
    # node "sumMonthly sin máscara").
    calendario = _REPORTING_JS[_REPORTING_JS.index("function renderCalendario("):_REPORTING_JS.index("function capacidadProductivasTotal")]
    assert "renderTreeRows(stateKey, tree, 0, true, renderCalendarioCell)" in calendario
    assert "monthMask" not in calendario

    no_asignadas = _REPORTING_JS[_REPORTING_JS.index("function renderCapacidadNoAsignadas"):_REPORTING_JS.index("function renderCapacidad(")]
    assert "renderTreeRows(stateKey, tree, 0)" in no_asignadas
    assert "monthMask" not in no_asignadas
