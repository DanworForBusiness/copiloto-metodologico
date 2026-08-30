"""
test_staffing_summary_tile_filters.py — STAFFING / ASIGNACIONES — CLICKABLE
SUMMARY TILES AS PANEL FILTERS (owner ticket, ver HANDOFF).

Homologa el patrón clickeable ya probado en Commercial CRM (tarjeta →
filtra el panel → "Filtrado por…" → descartable) a las 3 tarjetas de
Resumen de Asignaciones (Full Time / Part Time / Activos vencidos) — SIN
un framework de filtrado nuevo: reusa `filters.availability` (el dropdown
"Disponibilidad de asignación" YA existente) como única fuente de verdad
para Full Time/Part Time, y agrega `filters.activosVencidos` al MISMO
`filters` autoritativo para la tercera tarjeta (sin dropdown equivalente,
backed por el predicado `activo_vencido` que YA calcula el server —
`is_activo_vencido`, staffing_service.py — nunca reimplementado acá).

Ver staffing_summary_tile_filters.node.js para las invariantes de
comportamiento reales (A-M + H, ejercitando `staffingDashboard()` directo,
MISMO patrón que staffing_timeline_vacation_lookback.node.js). Este archivo
cubre lo que el harness Node NO puede: markup/atributos de accesibilidad
del template, presencia de los `$watch` backstop en el código fuente, y que
la suite EXISTENTE de Staffing sigue verde (§14.O del ticket)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "staffing_summary_tile_filters.node.js"
_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_DASHBOARD_HTML = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations"
                   / "staffing_dashboard.html").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_staffing_summary_tile_filter_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Staffing summary tile filter invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL STAFFING SUMMARY TILE FILTER INVARIANTS PASSED" in result.stdout


# ── §5 — ÚNICA fuente de verdad (fijada a nivel de código fuente) ──────────

def test_availability_is_the_single_authoritative_state_no_shadow_filter():
    # Las tarjetas Full Time/Part Time escriben `filters.availability`
    # DIRECTO (nunca un segundo campo "tileAvailability" paralelo que
    # pudiera desincronizarse del <select> — ver docstring de app.js).
    assert 'this.filters.availability = "Full Time"' in _APP_JS
    assert 'this.filters.availability = "Part Time"' in _APP_JS
    assert "tileAvailability" not in _APP_JS
    assert "activosVencidos: false," in _APP_JS


def test_manual_dropdown_backstop_watchers_present_and_scoped():
    # §5.D: elegir Full Time/Part Time a mano debe tener el MISMO efecto
    # que clickear la tarjeta — sólo un `$watch` (no los handlers de clic,
    # que el dropdown nunca invoca) puede cubrir ese camino.
    assert 'this.$watch("filters.availability", (value) => {' in _APP_JS
    assert 'if (value === "Full Time" || value === "Part Time") this.filters.activosVencidos = false;' in _APP_JS
    assert 'this.$watch("filters.activosVencidos", (value) => {' in _APP_JS
    assert "if (value) this.filters.availability = \"\";" in _APP_JS


def test_activos_vencidos_cleared_if_activo_status_unchecked():
    # Consistencia defensiva (no un caso explícito del ticket, pero
    # necesaria para que "el panel filtrado nunca discrepe" — §1/§4): la
    # tarjeta Activos vencidos ya está oculta sin Status Activo tildado
    # (`x-show="filters.status.Activo"` en el template) — este watcher
    # evita que el FILTRO siga encendido en silencio detrás de una tarjeta
    # invisible.
    assert 'this.$watch("filters.status.Activo", (value) => {' in _APP_JS
    assert "if (!value) this.filters.activosVencidos = false;" in _APP_JS


def test_reset_filters_clears_summary_tile_state_too():
    # El botón global "✕ Limpiar" (`resetFilters()`) es la ÚNICA "X" que el
    # audit encontró en esta pantalla (§8.B) — su contrato PROBADO de
    # siempre ("limpiar TODO", ver test_staffing_routes.py/
    # test_staffing_service.py existentes) se preserva sin angostarse;
    # sólo se le agrega que también apague `activosVencidos`, para no
    # dejar una tarjeta huérfana "activa" tras un reset total.
    idx = _APP_JS.index("resetFilters() {")
    body = _APP_JS[idx:idx + 400]
    assert "this.filters.activosVencidos = false;" in body


def test_facted_assignments_applies_activos_vencidos_unconditionally():
    # Sin dropdown propio (§6) — se aplica siempre, MISMO criterio que
    # `status` (nunca excluido por ningún `excludeKey`, a diferencia de
    # consultant/project/client/availability).
    idx = _APP_JS.index("facetedAssignments(excludeKey) {")
    body = _APP_JS[idx:idx + 1300]
    assert "if (this.filters.activosVencidos && !a.activo_vencido) return false;" in body


# ── §12 — accesibilidad y markup (template) ─────────────────────────────────

def test_three_tiles_are_accessible_toggle_buttons():
    for handler in ("clickFullTimeTile()", "clickPartTimeTile()", "clickActivosVencidosTile()"):
        idx = _DASHBOARD_HTML.index(f'@click="{handler}"')
        # Ventana generosa hacia atrás: cada tarjeta arma sus atributos en
        # varias líneas de HTML antes del `@click`.
        block = _DASHBOARD_HTML[max(0, idx - 500):idx + 200]
        assert 'role="button"' in block, f"{handler}: falta role=button"
        assert 'tabindex="0"' in block, f"{handler}: falta tabindex"
        assert ":aria-pressed=" in block, f"{handler}: falta aria-pressed (toggle real, ver bar-row--clickable de Commercial)"
        assert "aria-label=" in block, f"{handler}: falta aria-label"
        assert f'@keydown.enter="{handler}"' in block, f"{handler}: falta activación por Enter"
        assert f'@keydown.space.prevent="{handler}"' in block, f"{handler}: falta activación por Espacio"
        assert "activity-kpi--clickable" in block, f"{handler}: no reusa el primitivo visual de Commercial"


def test_activos_vencidos_tile_keeps_its_existing_visibility_gate():
    # La tarjeta ya estaba oculta sin Status Activo (comportamiento
    # PREVIO a este ticket) — no se toca esa condición, sólo se suma la
    # interactividad.
    idx = _DASHBOARD_HTML.index("clickActivosVencidosTile()")
    block = _DASHBOARD_HTML[max(0, idx - 500):idx]
    assert 'x-show="filters.status.Activo"' in block


def test_filtrado_por_chip_reuses_existing_hub_primitive_no_new_css():
    assert 'class="dashboard-subhead__filter"' in _DASHBOARD_HTML
    assert 'class="dashboard-subhead__reset"' in _DASHBOARD_HTML
    assert 'x-text="summaryTileFilterLabel"' in _DASHBOARD_HTML
    assert '@click="clearSummaryTileFilter()"' in _DASHBOARD_HTML
    assert 'x-show="activeSummaryTile"' in _DASHBOARD_HTML


def test_no_new_css_classes_introduced_for_tiles_or_chip():
    # §16/§12: reusar primitivos EXISTENTES (tools.css) — nunca un chip ni
    # un tile CSS nuevo. Grep negativo de nombres plausibles que un
    # framework nuevo hubiera introducido.
    for forbidden in ("summary-tile", "kpi-tile--", "staffing-tile-filter", "filter-chip"):
        assert forbidden not in _DASHBOARD_HTML
        assert forbidden not in _APP_JS


# ── §16 — scope guards: no se tocó Staffing source acquisition/absences ───

def test_scope_guard_no_delta_cache_source_files_touched():
    # Contrato NEGATIVO barato: este ticket es 100% frontend/client-side
    # (§13 del ticket — "no backend refetch"), así que ningún archivo de
    # adquisición/caché de Staffing debería mencionar "activosVencidos"/
    # "summaryTile" — son conceptos exclusivamente de presentación.
    staffing_repo = (
        Path(__file__).resolve().parents[2] / "webapp" / "services" / "staffing_repository.py"
    ).read_text(encoding="utf-8")
    assert "activosVencidos" not in staffing_repo
    assert "summaryTile" not in staffing_repo
