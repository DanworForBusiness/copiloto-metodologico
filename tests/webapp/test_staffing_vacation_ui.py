"""
test_staffing_vacation_ui.py — Contrato de UI del Vacation Timeline Overlay
(ver HANDOFF): carga lazy independiente, overlay SOLO en agrupación
Consultor, ausente en Proyecto, sin refetch al cambiar de agrupación,
fail-soft visible. Mismo criterio que test_staffing_app_js.py/
test_staffing_director_priority_v2.py: la lógica que vive 100% en app.js
(sin runtime de JS en esta suite) se fija por CONTENIDO DE FUENTE.
"""
from __future__ import annotations

from pathlib import Path

_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_DASHBOARD_HTML = (
    Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations" / "staffing_dashboard.html"
).read_text(encoding="utf-8")
_TOOLS_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


# ============================================================================
# Carga lazy independiente (fail-soft)
# ============================================================================

def test_vacations_load_independently_on_init_not_on_user_interaction():
    # A diferencia del historial de Inactivo (que espera el primer toggle),
    # el overlay de vacaciones arranca solo, sin esperar ninguna acción.
    assert "this._loadVacations();" in _APP_JS
    init_block = _APP_JS[_APP_JS.index("init() {"):_APP_JS.index("async _loadInactiveHistory")]
    assert "this._loadVacations();" in init_block


def test_vacations_endpoint_is_the_documented_lazy_route():
    assert '"/operaciones/staffing/asignaciones/vacaciones"' in _APP_JS


def test_vacations_load_failure_is_fail_soft_never_throws_uncaught():
    block = _APP_JS[_APP_JS.index("async _loadVacations() {"):_APP_JS.index("async _loadInactiveHistory() {")]
    assert "this.vacationsLoadError = true;" in block
    assert "try {" in block and "catch (e) {" in block


def test_vacation_error_message_is_non_blocking_and_separate_from_history_error():
    assert 'class="staffing-vacation-error"' in _DASHBOARD_HTML
    assert "vacationsLoadError && !vacationsLoading" in _DASHBOARD_HTML
    # Mensaje propio, NUNCA el mismo texto que el error de historial.
    vacation_block = _DASHBOARD_HTML[
        _DASHBOARD_HTML.index('class="staffing-vacation-error"'):
        _DASHBOARD_HTML.index('class="staffing-vacation-error"') + 260
    ]
    assert "overlay de vacaciones" in vacation_block
    assert "historial de Inactivo" not in vacation_block


# ============================================================================
# Consultant-only, ausente en Proyecto, sin refetch (tests 17/18/22)
# ============================================================================

def test_vacations_attach_only_in_consultant_grouping_not_project():
    block = _APP_JS[_APP_JS.index("get timelineGroups()"):_APP_JS.index("get vacationsByConsultant()")]
    assert "byProject ? null : this.vacationsByConsultant" in block


def test_vacations_by_consultant_is_a_pure_getter_no_fetch():
    block = _APP_JS[_APP_JS.index("get vacationsByConsultant()"):_APP_JS.index("vacationBarAriaLabel")]
    assert "fetch(" not in block
    assert "this.vacations.forEach" in block


def test_toggle_grouping_never_calls_load_vacations_again():
    toggle_block = _APP_JS[_APP_JS.index("toggleGrouping()"):_APP_JS.index("get groupToggleLabel")]
    assert "_loadVacations" not in toggle_block


def test_template_renders_vacation_lane_conditionally_on_group_vacations():
    assert 'x-if="group.vacations.length > 0"' in _DASHBOARD_HTML
    assert 'staffing-timeline__vacation-row' in _DASHBOARD_HTML
    assert 'x-for="vacation in group.vacations"' in _DASHBOARD_HTML


# ============================================================================
# Múltiples períodos / un solo día (tests 20/21, a nivel de template/JS)
# ============================================================================

def test_vacation_bars_key_by_source_id_so_multiple_periods_render_independently():
    assert ':key="vacation.source_id"' in _DASHBOARD_HTML


def test_vacation_bar_reuses_bar_style_positioning_same_as_assignment_bars():
    # Reusa el MISMO helper de posicionamiento — sin una segunda
    # implementación de grid-column para vacaciones (funciona igual para un
    # período de un solo día: start_index === end_index).
    assert ':style="barStyle(vacation)"' in _DASHBOARD_HTML


# ============================================================================
# Barras de asignación NUNCA se alteran por el overlay (test 19)
# ============================================================================

def test_assignment_bar_rendering_untouched_by_vacation_overlay():
    # paletteClass/barStyle de la barra de ASIGNACIÓN siguen exactamente
    # como antes — el overlay es un lane SEPARADO, nunca mezclado con la
    # fila de asignación existente.
    assert ':class="[paletteClass(row), \'is-status-\' + statusSlug(row.status)' in _DASHBOARD_HTML


def test_vacation_visual_treatment_is_distinct_from_project_palette():
    # Rayado neutro (hatch), NUNCA uno de los 6 tonos de identidad de
    # proyecto (staffing-palette-N) — evita leerse como "otro proyecto más".
    assert ".staffing-timeline__vacation-bar" in _TOOLS_CSS
    vacation_css = _TOOLS_CSS[_TOOLS_CSS.index(".staffing-timeline__vacation-bar {"):]
    assert "staffing-palette-" not in vacation_css[:400]
    assert "repeating-linear-gradient" in _TOOLS_CSS


# ============================================================================
# No impacto en KPIs/capacidad — sección "assignments" nunca lee de vacations
# ============================================================================

def test_kpis_getter_never_reads_vacations_state():
    kpis_block = _APP_JS[_APP_JS.index("get kpis()"):_APP_JS.index("toggleGrouping()")]
    assert "vacations" not in kpis_block


def test_filtered_assignments_never_reads_vacations_state():
    block = _APP_JS[_APP_JS.index("get filteredAssignments()"):_APP_JS.index("get kpis()")]
    assert "vacations" not in block
