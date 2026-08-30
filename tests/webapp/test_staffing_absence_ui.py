"""
test_staffing_absence_ui.py — Contrato de UI del Staffing Approved Absence
Overlay (generalización del Vacation Timeline Overlay original, ver
HANDOFF): carga lazy independiente, overlay SOLO en agrupación Consultor
(un ÚNICO lane "Ausencias" compartido por los 4 conceptos), ausente en
Proyecto, sin refetch al cambiar de agrupación, fail-soft visible, filtro
Concepto (4 checkboxes, sin "Todos", 100% client-side, sin impacto en
Asignaciones/KPIs). Mismo criterio que test_staffing_app_js.py/
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

def test_absences_load_independently_on_init_not_on_user_interaction():
    # A diferencia del historial de Inactivo (que espera el primer toggle),
    # el overlay de ausencias arranca solo, sin esperar ninguna acción.
    assert "this._loadAbsences();" in _APP_JS
    init_block = _APP_JS[_APP_JS.index("init() {"):_APP_JS.index("async _loadInactiveHistory")]
    assert "this._loadAbsences();" in init_block


def test_absences_endpoint_is_the_documented_lazy_route():
    assert '"/operaciones/staffing/asignaciones/ausencias"' in _APP_JS


def test_absences_load_failure_is_fail_soft_never_throws_uncaught():
    block = _APP_JS[_APP_JS.index("async _loadAbsences() {"):_APP_JS.index("async _loadInactiveHistory() {")]
    assert "this.absencesLoadError = true;" in block
    assert "try {" in block and "catch (e) {" in block


def test_absence_error_message_is_non_blocking_and_separate_from_history_error():
    assert 'class="staffing-absence-error"' in _DASHBOARD_HTML
    assert "absencesLoadError && !absencesLoading" in _DASHBOARD_HTML
    # Mensaje propio, NUNCA el mismo texto que el error de historial.
    absence_block = _DASHBOARD_HTML[
        _DASHBOARD_HTML.index('class="staffing-absence-error"'):
        _DASHBOARD_HTML.index('class="staffing-absence-error"') + 260
    ]
    assert "ausencias aprobadas" in absence_block
    assert "historial de Inactivo" not in absence_block


# ============================================================================
# Consultant-only, ausente en Proyecto, sin refetch
# ============================================================================

def test_absences_attach_only_in_consultant_grouping_not_project():
    block = _APP_JS[_APP_JS.index("get timelineGroups()"):_APP_JS.index("get filteredAbsences()")]
    assert "byProject ? null : this.absencesByConsultant" in block


def test_absences_by_consultant_is_a_pure_getter_no_fetch():
    block = _APP_JS[_APP_JS.index("get absencesByConsultant()"):_APP_JS.index("absenceLaneLabel")]
    assert "fetch(" not in block
    assert "this.filteredAbsences.forEach" in block


def test_toggle_grouping_never_calls_load_absences_again():
    # Hub Dashboard Contextual View Control corrective (ver HANDOFF):
    # `groupToggleLabel()` fue retirado (segmented switch reemplazó el botón
    # único) — `setGrouping(mode)` es la primitiva real donde vivía este
    # invariante; `toggleGrouping()` sigue siendo el wrapper fino.
    toggle_block = _APP_JS[_APP_JS.index("setGrouping(mode) {"):_APP_JS.index("rowGroupSecondaryLabel(row) {")]
    assert "_loadAbsences" not in toggle_block


def test_template_renders_absence_lane_conditionally_on_group_absences():
    assert 'x-if="group.absences.length > 0"' in _DASHBOARD_HTML
    assert 'staffing-timeline__absence-row' in _DASHBOARD_HTML
    assert 'x-for="absence in group.absences"' in _DASHBOARD_HTML


# ============================================================================
# Un ÚNICO lane compartido "Ausencias" — NUNCA uno por concepto
# ============================================================================

def test_single_shared_absence_lane_not_one_per_concept():
    # Un solo bloque x-if="group.absences.length > 0" en todo el template —
    # nunca 4 bloques condicionados por concepto.
    assert _DASHBOARD_HTML.count('x-if="group.absences.length > 0"') == 1
    assert _DASHBOARD_HTML.count("staffing-timeline__absence-row") == 1


def test_no_concept_specific_css_classes_or_colors():
    # El tratamiento visual (hatch gris) es el MISMO para los 4 conceptos —
    # nunca una clase/color condicionado por concepto.
    for forbidden_class in (
        "absence-bar--vacaciones", "absence-bar--danwor-day", "absence-bar--cumpleanos",
        "absence-bar--otros", "is-concept-",
    ):
        assert forbidden_class not in _TOOLS_CSS
        assert forbidden_class not in _DASHBOARD_HTML


# ============================================================================
# Concepto — filtro de 4 checkboxes fijos, sin "Todos"
# ============================================================================

def test_concept_filter_has_exactly_four_fixed_options():
    assert 'const CONCEPT_OPTIONS = ["Vacaciones", "Danwor Day", "Cumpleaños", "Otros"];' in _APP_JS


def test_concept_filter_all_four_selected_by_default():
    block = _APP_JS[_APP_JS.index("const defaultConceptFilter"):_APP_JS.index("const PALETTE_SIZE")]
    assert "c, true" in block


def test_concept_filter_has_no_todos_checkbox():
    # A diferencia de Consultor/Proyecto/Cliente/Disponibilidad (que sí
    # tienen <option value="">Todos</option>), el bloque de Concepto en el
    # template es EXCLUSIVAMENTE checkboxes sobre conceptOptions — sin una
    # opción "Todos" extra.
    concept_block = _DASHBOARD_HTML[
        _DASHBOARD_HTML.index("<span>Concepto</span>"):
        _DASHBOARD_HTML.index("<span>Concepto</span>") + 600
    ]
    assert "Todos" not in concept_block
    assert 'x-for="c in conceptOptions"' in concept_block
    assert 'x-model="filters.concept[c]"' in concept_block


def test_concept_checkbox_group_reuses_the_same_compact_pattern_as_estado():
    # Máximo 4 filas por columna (ver HANDOFF) — Concepto reusa EXACTAMENTE
    # el mismo par de clases que ya usa Estado (whatsnew-filter--compact +
    # dashboard-checkbox-group), sin una variante nueva que pueda crecer el
    # panel de filtros.
    concept_idx = _DASHBOARD_HTML.index("<span>Concepto</span>")
    label_start = _DASHBOARD_HTML.rindex('<label class="whatsnew-filter whatsnew-filter--compact">', 0, concept_idx)
    concept_label_block = _DASHBOARD_HTML[label_start:concept_idx + 400]
    assert "dashboard-checkbox-group" in concept_label_block
    assert 'whatsnew-filter--compact' in concept_label_block


def test_concept_options_is_a_fixed_array_not_derived_from_data():
    # A diferencia de statusOptions (derivado de assignments), conceptOptions
    # es literalmente CONCEPT_OPTIONS — nunca calculado desde this.absences.
    assert "conceptOptions: CONCEPT_OPTIONS," in _APP_JS


def test_reset_filters_restores_default_concept_selection():
    block = _APP_JS[_APP_JS.index("resetFilters() {"):_APP_JS.index("statusSlug(status)")]
    assert "this.filters.concept = defaultConceptFilter();" in block


# ============================================================================
# Filtrado 100% client-side — nunca Graph, nunca toca Asignaciones/KPIs
# ============================================================================

def test_filtered_absences_is_pure_client_side_no_fetch():
    block = _APP_JS[_APP_JS.index("get filteredAbsences()"):_APP_JS.index("get absencesByConsultant()")]
    assert "fetch(" not in block
    assert "this.absences" in block and ".filter(" in block
    assert "filters.concept" in block


def test_concept_filter_never_touches_assignments_or_filtered_assignments():
    block = _APP_JS[_APP_JS.index("get filteredAbsences()"):_APP_JS.index("get absencesByConsultant()")]
    assert "this.assignments" not in block
    assert "filteredAssignments" not in block


def test_kpis_getter_never_reads_absences_state():
    kpis_block = _APP_JS[_APP_JS.index("get kpis()"):_APP_JS.index("toggleGrouping()")]
    assert "absences" not in kpis_block


def test_filtered_assignments_never_reads_absences_state():
    block = _APP_JS[_APP_JS.index("get filteredAssignments()"):_APP_JS.index("get kpis()")]
    assert "absences" not in block


def test_facet_reconciliation_never_watches_concept_filter():
    # `_reconcileSelections`/los 5 `$watch` de facetado son EXCLUSIVOS de
    # Consultor/Proyecto/Cliente/Disponibilidad/Estado — Concepto nunca
    # dispara ni participa de esa revalidación (no comparte universo con
    # Asignaciones).
    init_block = _APP_JS[_APP_JS.index("init() {"):_APP_JS.index("this._loadAbsences();")]
    assert "filters.concept" not in init_block


# ============================================================================
# Ausencias visibles / total de horas recalculan tras el filtro Concepto
# ============================================================================

def test_absence_lane_label_sums_hours_from_the_periods_it_receives():
    # `absenceLaneLabel` suma sobre el array que RECIBE (group.absences, ya
    # filtrado por Concepto vía absencesByConsultant) — no vuelve a leer
    # this.absences crudo, así que un checkbox de Concepto recalcula el
    # total automáticamente sin lógica adicional.
    block = _lane_label_block()
    assert "p.hours" in block
    assert "this.absences" not in block


def test_absences_by_consultant_groups_the_already_filtered_array():
    block = _APP_JS[_APP_JS.index("get absencesByConsultant()"):_APP_JS.index("absenceLaneLabel")]
    assert "this.filteredAbsences" in block
    assert "this.absences.forEach" not in block


# ============================================================================
# Todos los conceptos destildados → lane vacía sin espacio residual
# ============================================================================

def test_unchecking_all_concepts_empties_the_group_leaving_no_lane():
    # filteredAbsences vacío → absencesByConsultant vacío → group.absences
    # === [] → el x-if="group.absences.length > 0" ya existente oculta el
    # lane entero, sin necesidad de código extra ad-hoc.
    # Absence Lookback corrective (ver HANDOFF): el filtro de Concepto se
    # compone AND con el cutoff de la ventana de ausencias (los 4
    # conceptos por igual, tras revocarse una versión previa exclusiva de
    # "Vacaciones") — sigue siendo el MISMO filter() único antes del
    # clipping visual (`.map`), nunca un segundo paso de filtrado.
    filtered_block = _APP_JS[_APP_JS.index("get filteredAbsences()"):_APP_JS.index("get absencesByConsultant()")]
    assert "if (this.filters.concept[a.concept] === false) return false;" in filtered_block
    assert "if (a.end_date < cutoff) return false;" in filtered_block
    assert 'x-if="group.absences.length > 0"' in _DASHBOARD_HTML


# ============================================================================
# Múltiples períodos / un solo día
# ============================================================================

def test_absence_bars_key_by_source_id_so_multiple_periods_render_independently():
    assert ':key="absence.source_id"' in _DASHBOARD_HTML


def test_absence_bar_reuses_bar_style_positioning_same_as_assignment_bars():
    # Reusa el MISMO helper de posicionamiento día-exacto (left/width % —
    # ver HANDOFF) — sin una segunda implementación para ausencias (funciona
    # igual para un período de un solo día: ancho positivo chico, nunca cero).
    assert ':style="barStyle(absence)"' in _DASHBOARD_HTML


# ============================================================================
# Barras de asignación NUNCA se alteran por el overlay
# ============================================================================

def test_assignment_bar_rendering_untouched_by_absence_overlay():
    # paletteClass/barStyle de la barra de ASIGNACIÓN siguen exactamente
    # como antes — el overlay es un lane SEPARADO, nunca mezclado con la
    # fila de asignación existente.
    assert ':class="[paletteClass(row), \'is-status-\' + statusSlug(row.status)' in _DASHBOARD_HTML


def test_absence_visual_treatment_is_distinct_from_project_palette():
    # Rayado neutro (hatch), NUNCA uno de los 6 tonos de identidad de
    # proyecto (staffing-palette-N) — evita leerse como "otro proyecto más".
    assert ".staffing-timeline__absence-bar" in _TOOLS_CSS
    absence_css = _TOOLS_CSS[_TOOLS_CSS.index(".staffing-timeline__absence-bar {"):]
    assert "staffing-palette-" not in absence_css[:400]
    assert "repeating-linear-gradient" in _TOOLS_CSS


# ============================================================================
# Etiqueta de horas (Días × 8) y concepto — post-generalización
# ============================================================================

def test_absence_bar_label_includes_the_concept():
    block = _APP_JS[_APP_JS.index("absenceBarLabel(absence) {"):_APP_JS.index("absenceBarAriaLabel")]
    assert "absence.concept" in block


def test_absence_bar_label_appends_hours_when_available():
    block = _APP_JS[_APP_JS.index("absenceBarLabel(absence) {"):_APP_JS.index("absenceBarAriaLabel")]
    assert "`${absence.concept} (${hours}hs)`" in block
    assert 'typeof hours === "number"' in block


def test_absence_bar_label_falls_back_to_plain_concept_when_hours_missing():
    block = _APP_JS[_APP_JS.index("absenceBarLabel(absence) {"):_APP_JS.index("absenceBarAriaLabel")]
    assert ": absence.concept;" in block


def test_template_absence_tooltip_uses_the_hours_aware_label():
    assert 'x-text="absenceBarLabel(absence)"' in _DASHBOARD_HTML


def test_raw_motivo_field_never_referenced_anywhere_in_the_frontend():
    # Privacy boundary (ver HANDOFF): el frontend NUNCA lee un campo motivo
    # crudo — solo `absence.concept`, ya clasificado server-side.
    for forbidden in ("absence.motivo", "absence.raw_motivo", ".Motivo"):
        assert forbidden not in _APP_JS
        assert forbidden not in _DASHBOARD_HTML


def _lane_label_block():
    return _APP_JS[_APP_JS.index("absenceLaneLabel(periods) {"):_APP_JS.index("absenceBarLabel(absence) {")]


def test_visible_row_label_uses_lane_helper_not_static_text():
    assert 'x-text="absenceLaneLabel(group.absences)"' in _DASHBOARD_HTML


def test_lane_label_sums_known_hours():
    # una sola de 80 → 80; dos (40 + 80) → 120 (suma, no first/last).
    block = _lane_label_block()
    assert "`Ausencias (${total}hs)`" in block
    assert "reduce" in block and "+ h" in block


def test_lane_label_falls_back_to_plain_when_no_known_hours():
    block = _lane_label_block()
    assert '"Ausencias"' in block
    assert 'typeof h === "number"' in block


def test_lane_label_sums_only_known_values_never_invents_from_dates():
    block = _lane_label_block()
    assert "p.hours" in block
    for forbidden in ("start_date", "end_date", "getDay", "allocation", "Horaspormes"):
        assert forbidden not in block


def test_absence_hours_label_is_display_only_never_from_calendar_or_allocation():
    # El label NUNCA se deriva de días hábiles del rango ni de allocation —
    # usa absence.hours (Días×8 del repo); la barra se posiciona con
    # barStyle(absence) sobre start/end (dimensiones separadas).
    block = _APP_JS[_APP_JS.index("absenceBarLabel(absence) {"):_APP_JS.index("absenceBarAriaLabel")]
    assert "absence.hours" in block
    for forbidden in ("start_date", "end_date", "Horaspormes", "allocation", "getDay"):
        assert forbidden not in block


# ============================================================================
# Assignments-first UX — carga independiente/lazy
# ============================================================================

_STAFFING_SHELL_HTML = (
    Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations" / "staffing.html"
).read_text(encoding="utf-8")


def test_assignments_load_independently_on_page_load_via_htmx():
    # Las asignaciones se cargan solas por HTMX al cargar la página; el
    # spinner "Cargando asignaciones…" vive en ESE contenedor, no en el
    # camino de ausencias.
    assert 'hx-post="/operaciones/staffing/asignaciones/data"' in _STAFFING_SHELL_HTML
    assert 'hx-trigger="load"' in _STAFFING_SHELL_HTML
    assert "Cargando asignaciones…" in _STAFFING_SHELL_HTML


def test_assignments_shell_never_requests_absences():
    # el shell/endpoint de asignaciones no menciona la ruta de ausencias —
    # el XLSX nunca se carga en el camino de asignaciones.
    assert "ausencias" not in _STAFFING_SHELL_HTML


def test_absences_load_is_fire_and_forget_not_awaited_in_init():
    # init() dispara _loadAbsences() SIN await — el render de asignaciones
    # (payload SSR ya presente) no espera a las ausencias.
    init_block = _APP_JS[_APP_JS.index("init() {"):_APP_JS.index("async _loadAbsences")]
    assert "this._loadAbsences();" in init_block
    assert "await this._loadAbsences" not in init_block


def test_absence_load_failure_never_mutates_assignments_state():
    # una falla de ausencias solo toca su propio estado
    # (absences/absencesLoading/absencesLoadError), NUNCA this.assignments.
    block = _APP_JS[_APP_JS.index("async _loadAbsences() {"):_APP_JS.index("async _loadInactiveHistory() {")]
    assert "this.assignments" not in block
