"""
test_hub_estimated_progress.py — Contrato de HUB FOREGROUND ESTIMATED
PROGRESS: primitivo compartido `HubEstimatedProgress` (app.js) + adopción en
Reporting Integral (reporting.js, PRIMER adopter, no dueño). Dos capas:
1) ejecución REAL vía Node (mismo patrón que
   test_reporting_initial_load_contract.py) — matemática del primitivo
   (checkpoints/overrun/EWMA) contra `app.js`, y el GLUE de reporting.js
   (creación/settle/cleanup de la barra) contra un stub controlable del
   primitivo, ambos con `render()`/`HubEstimatedProgress` REALES, no un mock
   de su comportamiento.
2) contrato de texto fuente (mismo criterio que test_reporting_batch_b.py
   §"Frontend") para invariantes puramente estructurales (dónde vive cada
   pieza, qué NO debe referenciar cada bloque).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_REPORTING_JS = (_STATIC / "reporting.js").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")

_PRIMITIVE_HARNESS = Path(__file__).resolve().parent / "hub_estimated_progress_primitive.node.js"
_GLUE_HARNESS = Path(__file__).resolve().parent / "reporting_hub_estimated_progress_glue.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_estimated_progress_primitive_invariants_hold():
    result = subprocess.run(["node", str(_PRIMITIVE_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"HubEstimatedProgress primitive invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB ESTIMATED PROGRESS PRIMITIVE INVARIANTS PASSED" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_reporting_hub_progress_glue_invariants_hold():
    result = subprocess.run(["node", str(_GLUE_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Reporting hub-progress glue invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB ESTIMATED PROGRESS GLUE INVARIANTS PASSED" in result.stdout


def _hub_estimated_progress_block() -> str:
    start = _APP_JS.index("const HubEstimatedProgress = (() => {")
    end = _APP_JS.index("return { start, percentForRatio };", start)
    return _APP_JS[start:end]


# ============================================================================
# A — foreground start -> progress visible
# ============================================================================

def test_hub_estimated_progress_primitive_exists_in_app_js():
    assert "const HubEstimatedProgress = (() => {" in _APP_JS
    assert "function start(key" in _hub_estimated_progress_block()


def test_reporting_loader_embeds_the_progress_bar_markup():
    assert 'reportingLoadingHtml("Cargando…", reportingHubProgressBarHtml(expId))' in _REPORTING_JS
    assert 'function reportingLoadingHtml(label, barHtml)' in _REPORTING_JS
    assert '${barHtml || ""}' in _REPORTING_JS


def test_hub_progress_css_primitive_exists():
    assert ".hub-progress {" in _TOOLS_CSS
    assert ".hub-progress__fill {" in _TOOLS_CSS
    # MISMO lenguaje visual gold que el resto del Hub, nunca un color propio.
    assert "background: var(--gold);" in _TOOLS_CSS.split(".hub-progress__fill {", 1)[1][:200]


# ============================================================================
# B — early finish -> 100 + contenido, sin esperar la animación
# ============================================================================

def test_ready_branch_settles_progress_before_showing_content_with_no_delay():
    ready_branch = _REPORTING_JS[
        _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"):
        _REPORTING_JS.index("} else {", _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"))
    ]
    assert "reportingHubProgressSettle(expId, true);" in ready_branch
    assert "setTimeout" not in ready_branch  # jamás una espera artificial para "terminar" la animación


# ============================================================================
# C — overrun -> tope <=95% + sweep continuo
# ============================================================================

def test_checkpoints_never_reach_100_percent_purely_by_time():
    checkpoints_line = [l for l in _hub_estimated_progress_block().splitlines() if "const CHECKPOINTS" in l][0]
    # último checkpoint (ratio=1, fin del estimado) <= .95 - nunca 100 por tiempo.
    last_percent = float(checkpoints_line.rstrip("];").rsplit(",", 1)[-1].strip())
    assert last_percent <= 0.95
    assert "[1, 0.94]" in checkpoints_line


def test_overrun_flag_computed_from_elapsed_vs_estimate_and_clamped():
    block = _hub_estimated_progress_block()
    assert "const overrun = elapsed > estimateMs;" in block
    assert "percentForRatio(overrun ? 1 : elapsed / estimateMs)" in block


def test_overrun_state_shows_a_continuous_sweep_on_the_gold_bar():
    assert ".hub-progress--overrun .hub-progress__fill::after {" in _TOOLS_CSS
    assert "@keyframes hub-progress-sweep {" in _TOOLS_CSS
    assert 'hub-progress--overrun' in _REPORTING_JS  # reporting.js aplica la clase cuando overrun=true


# ============================================================================
# D — complete after overrun -> cleanup (nunca un controller/timer huérfano)
# ============================================================================

def test_settle_deletes_the_controller_entry():
    settle_fn = _REPORTING_JS[
        _REPORTING_JS.index("function reportingHubProgressSettle(expId, success) {"):
        _REPORTING_JS.index("\n}\n", _REPORTING_JS.index("function reportingHubProgressSettle(expId, success) {"))
    ]
    assert "delete _reportingHubProgress[expId];" in settle_fn


def test_shared_timer_self_clears_once_no_tab_is_waiting():
    sweep_fn = _REPORTING_JS[_REPORTING_JS.index("function _reportingHubProgressSweep() {"):]
    assert "clearInterval(_reportingHubProgressTimer);" in sweep_fn
    assert "!Object.keys(_reportingHubProgress).length" in sweep_fn


# ============================================================================
# E — error -> barra/sweep finalizan, fail-soft intacto
# ============================================================================

def test_error_branch_settles_progress_before_showing_the_existing_fail_soft_error():
    loader_branch = _REPORTING_JS[
        _REPORTING_JS.index("const rawStatus = reportingTabRawStatus(expId);"):
        _REPORTING_JS.index("}\n  }\n  // Backbone compartido")
    ]
    assert 'reportingHubProgressSettle(expId, false);' in loader_branch
    assert 'reportingSourceErrorHtml("los datos de esta pestaña")' in loader_branch  # fail-soft actual, sin cambios


# ============================================================================
# F — background prefetch/warming -> nunca muestra progress
# ============================================================================

def test_progress_bar_is_invoked_from_exactly_the_one_first_hydration_loader_site():
    # Definición + 1 único call-site (el loader de PRIMERA hidratación en
    # render()) — si esto fuera >1, algún prefetch/warming en background
    # (Cartera Proyecto, Comercial Salud vía batch-a, etc.) estaría mostrando
    # progress indebidamente.
    assert _REPORTING_JS.count("reportingHubProgressBarHtml(expId)") == 2  # definición + el call-site


def test_background_sister_loaders_never_reference_the_progress_primitive():
    for fn_name in ("loadCarteraProyecto", "hydrateComercialParticipacionTab"):
        start = _REPORTING_JS.index(f"function {fn_name}(")
        # Función completa hasta el próximo `\nfunction ` top-level.
        end = _REPORTING_JS.index("\nfunction ", start + 1)
        body = _REPORTING_JS[start:end]
        assert "reportingHubProgress" not in body, fn_name


# ============================================================================
# G — perspective switch post-hidratación -> nunca vuelve al loader de first-load
# ============================================================================

def test_progress_bar_call_site_is_only_inside_the_not_yet_hydrated_branch():
    hydrated_branch = _REPORTING_JS[
        _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"):
        _REPORTING_JS.index("} else {", _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"))
    ]
    assert "reportingHubProgressBarHtml" not in hydrated_branch


# ============================================================================
# H — prefers-reduced-motion -> sin sweep agresivo
# ============================================================================

def test_reduced_motion_disables_the_sweep_animation():
    reduced = _TOOLS_CSS[_TOOLS_CSS.index("@media (prefers-reduced-motion: reduce) {\n  .hub-progress--overrun"):]
    reduced = reduced[:reduced.index("}\n}") + 3]
    assert "animation: none" in reduced


# ============================================================================
# I — cero requests Graph/HTTP adicionales
# ============================================================================

def test_hub_estimated_progress_never_performs_network_calls():
    block = _hub_estimated_progress_block()
    assert "fetch(" not in block
    assert "XMLHttpRequest" not in block
    assert "localStorage" in block  # única persistencia — nunca backend nuevo


def test_reporting_progress_glue_never_performs_network_calls():
    glue = _REPORTING_JS[
        _REPORTING_JS.index("const REPORTING_HUB_PROGRESS = {"):
        _REPORTING_JS.index("// --- SHARED REPORTING INITIAL-LOAD CONTRACT")
    ]
    assert "fetch(" not in glue


# ============================================================================
# J — la estimación se actualiza (EWMA) sin alterar el data lifecycle
# ============================================================================

def test_ewma_formula_matches_the_spec_0_70_previous_0_30_last_duration():
    block = _hub_estimated_progress_block()
    assert "previousMs * 0.70 + actualMs * 0.30" in block


def test_hub_estimated_progress_never_touches_alpine_state():
    # El primitivo compartido (app.js) es agnóstico de Reporting/Alpine — no
    # debe referenciar `state.*` (esa es responsabilidad EXCLUSIVA del glue
    # de reporting.js, nunca del primitivo).
    assert "state." not in _hub_estimated_progress_block()


def test_existing_data_loaders_are_untouched_by_the_progress_glue():
    # Los loaders que efectivamente disparan Graph/SharePoint no referencian
    # el primitivo — la instrumentación es puramente de presentación.
    for fn_name in (
        "loadCarteraMantenimiento", "loadRentabilidadData", "loadBatchAData",
        "loadHorasVendidasData", "loadComercialParticipacionData", "loadBatchBData",
    ):
        start = _REPORTING_JS.index(f"async function {fn_name}(")
        candidates = [
            i for i in (
                _REPORTING_JS.find("\nasync function ", start + 1),
                _REPORTING_JS.find("\nfunction ", start + 1),
            ) if i != -1
        ]
        end = min(candidates) if candidates else len(_REPORTING_JS)
        body = _REPORTING_JS[start:end]
        assert "reportingHubProgress" not in body, fn_name
