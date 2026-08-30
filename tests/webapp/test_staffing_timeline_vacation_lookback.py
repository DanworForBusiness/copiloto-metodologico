"""
test_staffing_timeline_vacation_lookback.py — Owner corrective "Timeline de
asignaciones — ventana de ausencias DINÁMICA" (Dirección, ver HANDOFF).
NOMBRE DE ARCHIVO histórico ("vacation lookback" era el alcance de la
primera versión, revocada dos veces por el owner — primero para cubrir los
4 conceptos, después para reemplazar el techo fijo de 28 por uno dinámico
basado en lo que el backend realmente hidrata) — el control/estado real ya
se llama `absenceLookbackDays`/`absenceLookbackMaxDays` (ver app.js), no se
renombró el archivo para no romper la referencia del harness sin necesidad.

Ver staffing_timeline_vacation_lookback.node.js para las invariantes del
motor real: cutoff = hoy - N días aplicado a los CUATRO conceptos, CLIPPING
VISUAL para cualquier concepto, techo DINÁMICO (`absenceLookbackMaxDays`,
arranca en un valor seguro y se actualiza al hidratar el payload real —
NUNCA un 28/365 fijo), clamp robusto ante cambios de máximo entre
hidrataciones, revelado progresivo del rango completo cuando el máximo
dinámico lo permite, independencia de `projectionDays`/`resetFilters()`/
asignaciones/tabla de Detalle, cero fetch adicional por cambiar N/
checkboxes, y visibilidad del control (`anyConceptSelected`).

Ver test_staffing_absence_service.py para los tests del CONTRATO BACKEND
(`absence_history_start`/`absence_lookback_max_days` en
`build_absence_overlay_payload`).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "staffing_timeline_vacation_lookback.node.js"
_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_DASHBOARD_HTML = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations"
                   / "staffing_dashboard.html").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_staffing_timeline_absence_lookback_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Staffing timeline absence lookback invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL STAFFING TIMELINE ABSENCE LOOKBACK INVARIANTS PASSED" in result.stdout


# ── default=10 / techo DINÁMICO — fijados a nivel de código fuente ─────────

def test_absence_lookback_default_is_10():
    assert "absenceLookbackDays: 10," in _APP_JS


def test_absence_lookback_max_days_is_dynamic_state_not_a_fixed_constant():
    # Owner decision (revocando el corrective anterior): 28 NO es el
    # contrato productivo — era sólo el piso mínimo garantizado que motivó
    # el discovery. El techo ahora es ESTADO (`absenceLookbackMaxDays`),
    # nunca una `const ..._MAX_DAYS = <número>` fija.
    assert "absenceLookbackMaxDays: 10," in _APP_JS
    assert "const ABSENCE_LOOKBACK_MAX_DAYS" not in _APP_JS
    assert "const VACATION_LOOKBACK_MAX_DAYS" not in _APP_JS


def test_absence_lookback_clamp_uses_runtime_state_never_a_literal_number():
    fn_block = _APP_JS[_APP_JS.index("setAbsenceLookbackDays(value) {"):
                       _APP_JS.index("setAbsenceLookbackDays(value) {") + 250]
    assert "Math.min(this.absenceLookbackMaxDays, Math.max(1, n))" in fn_block
    assert "28" not in fn_block
    assert "365" not in fn_block


def test_load_absences_updates_max_from_the_response_payload_only():
    # `_loadAbsences()` es el ÚNICO lugar que escribe `absenceLookbackMaxDays`
    # fuera de su valor inicial — y SIEMPRE desde `data.absence_lookback_max_days`
    # (el campo que ahora manda el backend), nunca una fórmula propia
    # (`_HORIZON_MONTHS_BEFORE`, "primer día del mes anterior", etc. no
    # deben aparecer en app.js en absoluto — ver discovery del owner:
    # "NO recalcular en frontend la regla backend").
    fn_block = _APP_JS[_APP_JS.index("async _loadAbsences()"):
                       _APP_JS.index("async _loadInactiveHistory()")]
    assert "data.absence_lookback_max_days" in fn_block
    assert "this.absenceLookbackMaxDays = max" in fn_block
    # Nunca una reimplementación JS de la regla backend ("primer día del
    # mes anterior") — `_HORIZON_MONTHS_BEFORE`/`function compute_horizon_months`
    # son nombres exclusivos de staffing_service.py; sólo pueden aparecer
    # en JS como REFERENCIA en comentario (texto libre), nunca como
    # identificador de función/constante definido acá.
    assert "_HORIZON_MONTHS_BEFORE" not in _APP_JS
    assert "function compute_horizon_months" not in _APP_JS
    assert "const compute_horizon_months" not in _APP_JS


def test_absence_lookback_input_uses_dynamic_max_binding_never_a_fixed_html_attribute():
    # `:max` (binding Alpine dinámico) — MISMA fuente runtime que el clamp
    # de arriba, nunca un `max="28"`/`max="365"` estático en el HTML (esa
    # duplicación es exactamente lo que el owner pidió evitar: "HTML max
    # fijo X, JS max calculado Y").
    assert ':max="absenceLookbackMaxDays" :value="absenceLookbackDays"' in _DASHBOARD_HTML
    assert 'max="28"' not in _DASHBOARD_HTML
    assert 'max="365" :value="absenceLookbackDays"' not in _DASHBOARD_HTML


def test_no_lingering_claim_that_28_is_the_functional_ceiling():
    # Limpieza explícita (owner): ningún comentario/documentación debe
    # seguir afirmando "28 es el máximo funcional del selector" como
    # contrato VIGENTE — puede quedar mencionado como el piso histórico que
    # motivó el discovery original, siempre que el propio texto aclare que
    # dejó de ser el contrato productivo.
    assert "dejó de ser el contrato productivo" in _APP_JS
    # Guard literal acotado al INPUT de ausencias específicamente (nunca al
    # de "Proyección a", que SÍ conserva su propio `max="365"` fijo — otro
    # dataset, sin este límite de historia garantizada, ver test de arriba
    # que ya fija el binding dinámico correcto).
    assert 'max="28"' not in _DASHBOARD_HTML


def test_clipping_derivation_documents_the_missing_horizon_anchor_and_the_algebraic_workaround():
    fn_block = _APP_JS[_APP_JS.index("function _visibleAbsenceStartPct"):
                       _APP_JS.index("function _visibleAbsenceStartPct") + 400]
    assert "_monthsBetween" in fn_block and "_dayFractionOfMonth" in fn_block
    context = _APP_JS[_APP_JS.index("Ventana de ausencias — CLIPPING VISUAL"):
                      _APP_JS.index("function _monthsBetween")]
    assert "horizon" in context.lower()
    assert "nunca" in context.lower() and "mand" in context.lower()  # "nunca manda [la fecha]"


# ── los 4 conceptos obedecen cutoff/clipping por igual ─────────────────────

def test_filtered_absences_applies_cutoff_to_all_four_concepts_uniformly():
    block = _APP_JS[_APP_JS.index("get filteredAbsences()"):_APP_JS.index("get absencesByConsultant()")]
    assert 'a.concept === "Vacaciones"' not in block
    assert "CONCEPT_OPTIONS[0]" not in block
    assert "if (a.end_date < cutoff) return false;" in block
    assert "if (a.start_date >= cutoff) return { ...a };" in block


# ── visibilidad del control ─────────────────────────────────────────────

def test_any_concept_selected_getter_exists_and_is_pure():
    assert "get anyConceptSelected() {" in _APP_JS
    block = _APP_JS[_APP_JS.index("get anyConceptSelected() {"):
                    _APP_JS.index("get anyConceptSelected() {") + 200]
    assert "CONCEPT_OPTIONS.some(" in block
    assert "fetch(" not in block


def test_absence_lookback_control_uses_x_show_never_x_if():
    assert 'x-show="anyConceptSelected"' in _DASHBOARD_HTML
    assert 'x-if="anyConceptSelected"' not in _DASHBOARD_HTML


# ── layout: fila propia, debajo del toolbar de Consultor/Proyecto/
#    Expandir/Contraer (owner: "que no esté en la misma línea") ───────────

def test_absence_lookback_row_is_separate_from_the_grouping_toolbar():
    timeline_block = _DASHBOARD_HTML[_DASHBOARD_HTML.index('data-section-id="timeline"'):
                                     _DASHBOARD_HTML.index('data-section-id="detalle"')]
    first_toolbar = timeline_block.index('<div class="staffing-timeline-toolbar">')
    second_toolbar = timeline_block.index('<div class="staffing-timeline-toolbar"', first_toolbar + 1)
    between = timeline_block[first_toolbar:second_toolbar]
    # El primer toolbar (Consultor/Proyecto/Expandir/Contraer) se CIERRA
    # antes de que abra el segundo (Ausencias) — son dos `<div>` hermanos,
    # nunca uno anidado dentro del otro.
    assert between.count("</div>") >= 1
    assert "setGrouping(" in timeline_block[:second_toolbar]
    assert "absenceLookbackDays" in timeline_block[second_toolbar:]
    assert "setGrouping(" not in timeline_block[second_toolbar:second_toolbar + 500]
