"""
test_reporting_kpi_primitive.py — CRITICAL HUB STANDARDIZATION CORRECTIVE
(owner live QA): "Reporting → Horas Productivas → Resumen → TOTAL HORAS"
rendered the raw float "184613.24999999997". The shared `.activity-kpi`
CSS class was already transversal, but the JS-level formatting was NOT:
every renderer decided on its own whether to route a value through
`fmtNum` before interpolating it, so a bypass was always one new tile
away.

Fix: `renderDashboardKpi({label, value, format, accent})` is now the
ONLY function in reporting.js that constructs `.activity-kpi` markup —
numeric formatting, the null/zero contract, and signed-value coloring are
its exclusive responsibility. All 16 KPI tiles across Reporting (Cartera,
Rentabilidad, Horas Vendidas, Calendario, Horas Productivas) were
migrated to call it instead of building markup by hand. This file
executes the REAL primitive (via node — no browser/jsdom framework)
against the exact reported value plus the documented zero/null/sign
contract — see reporting_kpi_primitive.node.js for the invariants.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_kpi_primitive.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")

# Las 17 llamadas KPI de Reporting (5 grids: Cartera 2 fijas + N via
# carteraKpiTiles, Rentabilidad 5, Horas Vendidas 7, Calendario 2,
# Horas Productivas 2 — panel-independent Año/Mes, ver §8ter item 1) — ver
# HANDOFF para el inventario completo.
_EXPECTED_MIGRATED_LABELS = [
    "Horas Vendidas", "Horas Certificadas", "Horas Trabajadas",
    "Rentabilidad horas", "No asignadas a servicios",  # Rentabilidad Resumen
    "Hs Estimadas anuales", "Mantenimiento", "Proyecto",
    "Vendidas Q1", "Vendidas Q2", "Vendidas Q3", "Vendidas Q4",  # Horas Vendidas
    "Clientes con calendario", "Entregas/checkpoints (año)",  # Calendario
    "Total Horas",  # Horas Productivas — AHORA 2 tiles (una por panel, cada
    # una dentro de su propia tarjeta autocontenida, owner composition
    # corrective) pero MISMO label "Total Horas" en ambas — el contexto lo
    # da la tarjeta que lo envuelve (Cliente / Cliente + Part), ver §8ter
    # item 1 + composition corrective en HANDOFF.
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_kpi_primitive_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"KPI primitive invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL KPI PRIMITIVE INVARIANTS PASSED" in result.stdout


def test_renderdashboardkpi_is_the_single_source_of_kpi_markup():
    assert "function renderDashboardKpi({ label, value, format = \"hours\", accent, smartAccent }) {" in _REPORTING_JS
    # El primitivo llama a fmtNum internamente — nunca al revés (un call
    # site formateando y pasando ya-texto).
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderDashboardKpi("):_REPORTING_JS.index("function renderDashboardKpi(") + 1200]
    assert "const display = fmtNum(value, format);" in fn


def test_no_reporting_kpi_call_site_interpolates_a_raw_value():
    # Ningún sitio de llamada debe volver a construir el markup a mano
    # (`<div class="activity-kpi">...${value}...`) — todos pasan por
    # `renderDashboardKpi`. Cuenta de invocaciones >= 16 (una por tile
    # conocido; una futura tile nueva solo puede sumar, nunca bypassear).
    call_count = _REPORTING_JS.count("renderDashboardKpi({")
    assert call_count >= len(_EXPECTED_MIGRATED_LABELS), (
        f"se esperaban al menos {len(_EXPECTED_MIGRATED_LABELS)} invocaciones de renderDashboardKpi, hay {call_count}"
    )
    for label in _EXPECTED_MIGRATED_LABELS:
        assert f'label: "{label}"' in _REPORTING_JS, f"KPI '{label}' no aparece migrado a renderDashboardKpi"


def test_rentabilidad_horas_kpi_passes_raw_number_not_diffcell_html():
    # Antes: `${diffCell(rentabilidadHoras)}` — HTML ya coloreado
    # interpolado directo en el tile (mismo patrón que dejó el KPI Diff Hs
    # de Cartera en blanco, ver corrective S.3). Ahora: valor CRUDO +
    # format:"signed" — el primitivo decide color/signo.
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidadResumen"):_REPORTING_JS.index("function certEjecGroupTooltip")]
    assert 'renderDashboardKpi({ label: "Rentabilidad horas", value: rentabilidadHoras, format: "signed" })' in fn
    assert "diffCell(rentabilidadHoras)" not in fn


def test_monthly_matrix_footers_and_cells_no_longer_interpolate_raw_numbers():
    # Auditoría extendida (mismo root cause, encontrado durante la
    # migración de KPIs): las filas Total de pie de matriz mensual
    # (Horas Vendidas/Calendario/Capacidad No Asignadas) y la matriz de
    # Concepto de Rentabilidad tenían el MISMO bypass a nivel de celda de
    # tabla — corregido con el mismo fmtNum, preservando la convención
    # existente de cada tabla (0/falsy -> "—" en matrices de horas,
    # siempre numérico en Calendario que es un conteo real).
    assert 'showTotal ? `<td>${fmtNum(rowTotal, "count")}</td>` : ""' in _REPORTING_JS
    horas_vendidas = _REPORTING_JS[_REPORTING_JS.index("function renderHorasVendidas("):_REPORTING_JS.index("function renderComercialParticipacion")]
    assert '${totalMonthly.map(v => `<td>${v === 0 || v ? fmtNum(v, "hours") : "—"}</td>`).join("")}' in horas_vendidas
    calendario = _REPORTING_JS[_REPORTING_JS.index("function renderCalendario("):_REPORTING_JS.index("function capacidadProductivasTotal")]
    assert '${totalMonthly.map(v => `<td>${fmtNum(v, "count")}</td>`).join("")}<td>${fmtNum(grandTotal, "count")}</td>' in calendario
    no_asignadas = _REPORTING_JS[_REPORTING_JS.index("function renderCapacidadNoAsignadas("):_REPORTING_JS.index("function renderCapacidad(")]
    # CORRECTIVO UX (owner: Horas Productivas SIN decimales) — "hours0" en
    # vez de "hours" (1 decimal, resto del Hub), mismo criterio 0/falsy -> "—".
    assert '${totalMonthly.map(v => `<td>${v === 0 || v ? fmtNum(v, "hours0") : "—"}</td>`).join("")}' in no_asignadas
    mensual = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidadMensual("):_REPORTING_JS.index("function renderRentabilidadResumen")]
    assert '${c.monthly.map(v => `<td>${c.signed ? diffCell(v) : (v ? fmtNum(v, "hours") : "—")}</td>`).join("")}' in mensual
    assert '<td>${c.signed ? diffCell(total) : fmtNum(total, "hours")}</td>' in mensual
