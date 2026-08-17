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
covers the structural/source-seam guarantees (no "Todos" in Año, Rentabilidad
hot/cold mirrors Horas Vendidas) that don't need runtime execution.
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


# --- YEAR DOMAIN: Año es single-select, "Todos" retirado -------------------

def test_horas_vendidas_and_rentabilidad_anio_field_never_offer_todos():
    for fn in ("horasVendidasAnioField", "rentabilidadAnioField"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 500]
        assert '>Todos</option>' not in body, f"{fn} NUNCA debe ofrecer 'Todos' — Año es single-select (owner)"
        assert "options.map(a =>" in body


def test_horas_vendidas_and_rentabilidad_mes_field_keeps_todos_and_is_cap_aware():
    for fn in ("horasVendidasMesField", "rentabilidadMesField"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 500]
        assert '>Todos</option>' in body, f"{fn} SÍ debe seguir ofreciendo 'Todos' (pertenece al dominio de Mes)"
        assert "_validMesOptions(anio)" in body


def test_anio_options_functions_filter_through_valid_anio_value():
    for fn in ("horasVendidasAnioOptions", "rentabilidadAnioOptions"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 1500]
        assert ".filter(_isValidAnioValue)" in body, f"{fn} debe validar el dominio de año — segunda línea de defensa"


# --- resetFilters ("Limpiar") — default sensato, nunca Desde/Hasta residual -

def test_limpiar_resets_rentabilidad_and_horas_vendidas_to_anio_mes_estado():
    fn = _REPORTING_JS[_REPORTING_JS.index("function resetFilters("):_REPORTING_JS.index("function toggleCarteraAnioInicio(")]
    assert 'state.filters.rentabilidad = { anio: rentabilidadDefaultAnio(), mes: "" };' in fn
    assert 'state.filters.horasvendidas = { anio: horasVendidasDefaultAnio(), mes: "" };' in fn
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
    fn = _REPORTING_JS[_REPORTING_JS.index("async function loadRentabilidadColdYear"):_REPORTING_JS.index("async function loadRentabilidadColdYear") + 950]
    assert '"/operaciones/reporting/integral/data/rentabilidad"' in fn
    assert "anios: anio" in fn


def test_rentabilidad_anios_disponibles_endpoint_wired():
    fn = _REPORTING_JS[_REPORTING_JS.index("async function loadRentabilidadAnios"):_REPORTING_JS.index("async function loadRentabilidadAnios") + 700]
    assert '"/operaciones/reporting/integral/data/rentabilidad/anios-disponibles"' in fn
