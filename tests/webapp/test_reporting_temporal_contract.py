"""
test_reporting_temporal_contract.py — REPORTING TEMPORAL CONTRACT +
PERFORMANCE CORRECTIVE (owner live QA): Horas Vendidas' Año dropdown
showed "Septiembre" as an option and still offered "Todos"; selecting
Año=2026 (año calendario real en curso) / Mes=Todos in August leaked
Sep-Dec values into the matrix/quarters/totals.

Executes the REAL shared primitives (`_isValidAnioValue`/
`_operationalMonthCap`/`_applyMonthCap`/`_sumMonthlyArrays`/
`_diffMonthly`/`_quarterValue`/`_validMesOptions`) via node — no browser
framework, same pattern as reporting_rollup_reconciliation.node.js. Also
covers the structural/source-seam guarantees (Rentabilidad hot/cold
mirrors Horas Vendidas) that don't need runtime execution.

AÑO/MES MULTI-SELECT corrective (owner directive — "REPORTING INTEGRAL —
PROPAGATE APPROVED TEMPORAL FILTER PATTERN"): Año SUPERSEDE su regla
previa "single-select, nunca Todos" — Rentabilidad/Horas Vendidas
migraron a los mismos pills multi-select que Cartera, empty-selection ==
Todos. Los tests de esa regla vieja fueron reemplazados por sus
equivalentes multi-select (ver test_reporting_cartera.py, sección AÑO/MES
MULTI-SELECT).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_temporal_contract.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_temporal_contract_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Temporal contract invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL TEMPORAL CONTRACT INVARIANTS PASSED" in result.stdout


# --- YEAR DOMAIN: Año pasó a multi-select pills (AÑO/MES MULTI-SELECT
# corrective — SUPERSEDE la regla vieja "single-select, nunca Todos") ------

def test_horas_vendidas_and_rentabilidad_anio_is_now_multiselect_pills_like_cartera():
    for fn in ("horasVendidasAnioPills", "rentabilidadAnioPills"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 300]
        assert "pillMultiField(\"Año\"" in body, fn
        assert "AnioOptions()" in body, fn


def test_rentabilidad_mes_is_multiselect_pills_and_cap_aware():
    # Rentabilidad SIGUE deshabilitando meses futuros del año en curso
    # (Certificadas/Trabajadas representan trabajo que todavía no se
    # ejecutó) — protección NUNCA tocada por el ticket "HORAS VENDIDAS —
    # ENABLE FUTURE MONTH SELECTION" (ver test siguiente, Horas Vendidas
    # es la ÚNICA excepción).
    fn = "rentabilidadMesPills"
    body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 500]
    assert 'pillMultiField("Mes"' in body, fn
    # Cap generalizado a multi-año (owner decision: válido si lo es
    # para ALGÚN año tildado) — reusa el MISMO helper que Cartera.
    assert "_mesValidoParaAlgunAnio(" in body, fn


def test_horas_vendidas_mes_is_multiselect_pills_without_future_month_cap():
    # SUPERSEDE el contrato viejo (owner ticket "HORAS VENDIDAS — ENABLE
    # FUTURE MONTH SELECTION"): Horas Vendidas es un compromiso YA VENDIDO/
    # proyectado, un mes posterior a "hoy" es un período de reporte VÁLIDO,
    # nunca deshabilitado — a diferencia de Rentabilidad (test anterior),
    # que SÍ conserva `_mesValidoParaAlgunAnio`. Sigue siendo pills
    # multi-select (eso no cambió), solo dejó de ser cap-aware.
    fn = "horasVendidasMesPills"
    body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 500]
    assert 'pillMultiField("Mes"' in body, fn
    assert "_mesValidoParaAlgunAnio(" not in body, (
        "horasVendidasMesPills ya NO debe usar el cap de mes futuro — "
        "ver owner ticket HORAS VENDIDAS — ENABLE FUTURE MONTH SELECTION"
    )


def test_anio_options_functions_filter_through_valid_anio_value():
    for fn in ("horasVendidasAnioOptions", "rentabilidadAnioOptions"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 1500]
        assert ".filter(_isValidAnioValue)" in body, f"{fn} debe validar el dominio de año — segunda línea de defensa"


# --- resetFilters ("Limpiar") — default sensato, nunca Desde/Hasta residual -

def test_limpiar_resets_rentabilidad_and_horas_vendidas_to_anio_mes_estado():
    # AÑO/MES MULTI-SELECT corrective: Año/Mes son Sets propios ahora,
    # "Limpiar" los reasigna directo (default sensato para Año, Mes a
    # vacío/Todos) — MISMO criterio que Cartera.
    fn = _REPORTING_JS[_REPORTING_JS.index("function resetFilters("):_REPORTING_JS.index("function toggleCarteraAnioInicio(")]
    assert "state.rentabilidadAnios = rentabilidadDefaultAniosSet();" in fn
    assert "state.rentabilidadMeses = new Set();" in fn
    assert "state.horasVendidasAnios = horasVendidasDefaultAniosSet();" in fn
    assert "state.horasVendidasMeses = new Set();" in fn
    # YEAR_TO_DATE_EXPERIENCES (el generador Desde/Hasta viejo) quedó VACÍO —
    # las 3 experiencias que alguna vez lo usaron migraron a Año/Mes/Estado
    # (Rentabilidad/HV) o Año/Estado (Participación, owner directive — "Do
    # NOT keep Participación on full-history").
    ytd = _REPORTING_JS[_REPORTING_JS.index("const YEAR_TO_DATE_EXPERIENCES"):_REPORTING_JS.index("const YEAR_TO_DATE_EXPERIENCES") + 200]
    assert '"rentabilidad"' not in ytd
    assert '"horasvendidas"' not in ytd
    assert '"comercial"' not in ytd


# --- Rentabilidad hot/cold mirrors Horas Vendidas (owner: "apply the SAME
# temporal runtime principle to Rentabilidad") -----------------------------

def test_rentabilidad_hot_cold_lifecycle_mirrors_horas_vendidas():
    for fn_name in ("hydrateRentabilidadTab", "rentabilidadLoadedYears", "loadRentabilidadAnios",
                     "loadRentabilidadColdYear", "mergeRentabilidadRows"):
        assert f"function {fn_name}(" in _REPORTING_JS, f"falta {fn_name} — Rentabilidad debe replicar el ciclo de HV"


def test_rentabilidad_data_endpoint_accepts_anios_param():
    fn = _REPORTING_JS[_REPORTING_JS.index("async function loadRentabilidadColdYear"):_REPORTING_JS.index("async function loadRentabilidadColdYear") + 1300]
    assert '"/operaciones/reporting/integral/data/rentabilidad"' in fn
    assert "anios: anio" in fn


def test_rentabilidad_anios_disponibles_endpoint_wired():
    fn = _REPORTING_JS[_REPORTING_JS.index("async function loadRentabilidadAnios"):_REPORTING_JS.index("async function loadRentabilidadAnios") + 700]
    assert '"/operaciones/reporting/integral/data/rentabilidad/anios-disponibles"' in fn
