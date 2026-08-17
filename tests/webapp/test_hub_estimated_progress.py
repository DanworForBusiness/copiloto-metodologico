"""
test_hub_estimated_progress.py — Contrato de HUB FOREGROUND ESTIMATED
PROGRESS tras el corrective "STOP REINVENTING THE HUB LOADER": el Hub tiene
UN SOLO loader/progress bar real — `.read-progress`/`.read-progress__fill`
(montado en platform_topbar.html, hoy alimentado por scroll REAL en Copiloto
vía app.js `copilotLayout().init()`, SIN CAMBIOS). Reporting NO crea otra
barra — reusa ESE MISMO nodo, escribiendo `style.width` con progreso
ESTIMADO por tiempo (`HubEstimatedProgress`, matemática pura: checkpoints/
overrun/EWMA/localStorage) en vez de scroll. Mismo DOM/CSS/posición/
comportamiento — solo cambia la FUENTE del valor.

Dos capas:
1) ejecución REAL vía Node (mismo patrón que
   test_reporting_initial_load_contract.py) — matemática del primitivo
   contra `app.js`, y el GLUE de reporting.js (creación/settle/cleanup,
   escritura sobre `.read-progress__fill`, estabilidad del loader "Cargando…"
   durante los ticks) contra un stub controlable del primitivo, ambos con
   `render()`/`HubEstimatedProgress` REALES, no un mock de su comportamiento.
2) contrato de texto fuente (mismo criterio que test_reporting_batch_b.py
   §"Frontend") para invariantes estructurales: que NO exista un segundo
   nodo/clase de progress, que Copiloto quede intacto, etc.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_REPORTING_JS = (_STATIC / "reporting.js").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")
_APP_CSS = (_STATIC / "app.css").read_text(encoding="utf-8")
_PLATFORM_TOPBAR_HTML = (_TEMPLATES / "partials" / "platform_topbar.html").read_text(encoding="utf-8")
_MODULE_HTML = (_TEMPLATES / "module.html").read_text(encoding="utf-8")
_REPORTING_INTEGRAL_HTML = (_TEMPLATES / "operations" / "reporting_integral.html").read_text(encoding="utf-8")

_PRIMITIVE_HARNESS = Path(__file__).resolve().parent / "hub_estimated_progress_primitive.node.js"
_GLUE_HARNESS = Path(__file__).resolve().parent / "reporting_hub_estimated_progress_glue.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_estimated_progress_primitive_invariants_hold():
    result = subprocess.run(["node", str(_PRIMITIVE_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"HubEstimatedProgress primitive invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB ESTIMATED PROGRESS PRIMITIVE INVARIANTS PASSED" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_reporting_hub_progress_glue_invariants_hold():
    # Cubre, ejecutando render() real: A (start único/seed), C (overrun
    # clamp), G (ticks nunca recrean el DOM de Reporting), H (early finish
    # resetea+settle sin esperar), K (perspective/tab churn no re-dispara),
    # E/D (fail-soft + cleanup en error), I/D (background settle sin
    # re-render, timer se auto-limpia) — ver el .node.js para el detalle.
    result = subprocess.run(["node", str(_GLUE_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Reporting hub-progress glue invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB ESTIMATED PROGRESS GLUE INVARIANTS PASSED" in result.stdout


def _hub_estimated_progress_block() -> str:
    start = _APP_JS.index("const HubEstimatedProgress = (() => {")
    end = _APP_JS.index("return { start, percentForRatio };", start)
    return _APP_JS[start:end]


def test_hub_estimated_progress_primitive_exists_in_app_js():
    assert "const HubEstimatedProgress = (() => {" in _APP_JS
    assert "function start(key" in _hub_estimated_progress_block()


# ============================================================================
# A — existe UN solo loader/progress DOM del Hub (nunca dos)
# ============================================================================

def test_only_one_progress_bar_dom_exists_in_the_whole_codebase():
    # `.read-progress` (clase) aparece en EXACTAMENTE un lugar de todo el
    # árbol de templates: el macro compartido `platform_topbar.html` — nunca
    # duplicado por página (ni siquiera module.html, que antes lo montaba
    # directamente). Ningún otro nombre de clase/id de progress bar existe.
    templates_dir = Path(__file__).resolve().parents[2] / "webapp" / "templates"
    hits = [
        p for p in templates_dir.rglob("*.html")
        if 'class="read-progress"' in p.read_text(encoding="utf-8")
    ]
    assert [p.name for p in hits] == ["platform_topbar.html"]
    # Ningún vestigio del primitivo inventado en el intento anterior de este
    # mismo corrective (owner: "STOP REINVENTING THE HUB LOADER").
    for leftover in ("hub-progress", "hub-foreground-progress", "HubEstimatedProgress.start(cfg.key"):
        assert leftover not in _PLATFORM_TOPBAR_HTML
    assert "hub-progress" not in _TOOLS_CSS
    assert ".hub-progress" not in _REPORTING_JS


def test_hub_progress_node_lives_in_platform_topbar_canonical_position():
    # Posición canónica del Hub: montado UNA vez en platform_topbar.html,
    # inmediatamente debajo de `</header>` — NUNCA en el template de una
    # superficie puntual (module.html/reporting_integral.html no lo definen,
    # heredan el MISMO nodo del macro compartido).
    header_close = _PLATFORM_TOPBAR_HTML.index("</header>")
    after_header = _PLATFORM_TOPBAR_HTML[header_close:header_close + 1000]
    assert 'class="read-progress"' in after_header
    assert 'class="read-progress__fill"' in after_header
    assert 'class="read-progress"' not in _REPORTING_INTEGRAL_HTML
    assert 'class="read-progress"' not in _MODULE_HTML


# ============================================================================
# B — Copiloto sigue usando ese loader (sin cambios en su UX/comportamiento)
# ============================================================================

def test_copilot_scroll_driver_in_app_js_is_untouched():
    # `copilotLayout().init()` sigue escribiendo sobre `.read-progress__fill`
    # vía scroll REAL, exactamente como antes de este corrective — NUNCA se
    # tocó su lógica (mismo query, mismo cálculo de ratio, mismos listeners).
    assert 'document.querySelector(".read-progress__fill")' in _APP_JS
    scroll_block = _APP_JS[_APP_JS.index('const progressFill = document.querySelector(".read-progress__fill");'):]
    scroll_block = scroll_block[:scroll_block.index("if (contentEl) {")]
    assert 'document.addEventListener("scroll", updateProgress' in scroll_block
    assert 'window.addEventListener("resize", updateProgress' in scroll_block
    assert "el.scrollTop / range" in scroll_block


# ============================================================================
# C — Reporting usa EXACTAMENTE ese mismo loader (el nodo real, no uno propio)
# ============================================================================

def test_reporting_writes_to_the_exact_same_read_progress_fill_node():
    paint_fn = _REPORTING_JS[
        _REPORTING_JS.index("function _reportingHubProgressPaint() {"):
        _REPORTING_JS.index("\n}\n", _REPORTING_JS.index("function _reportingHubProgressPaint() {"))
    ]
    assert 'document.querySelector(".read-progress__fill")' in paint_fn
    assert "getElementById" not in paint_fn  # el nodo real no tiene id propio


# ============================================================================
# D — Reporting no contiene markup propio de progress
# ============================================================================

def test_reporting_loader_never_embeds_any_progress_bar_markup():
    # El loader "Cargando…" vuelve a su forma original, SIN parámetro de
    # barra: la barra NUNCA viaja dentro del innerHTML de Reporting.
    assert 'function reportingLoadingHtml(label) {' in _REPORTING_JS
    assert "barHtml" not in _REPORTING_JS
    assert 'reportingLoadingHtml("Cargando…");' in _REPORTING_JS
    assert "<div" not in _REPORTING_JS[
        _REPORTING_JS.index("function reportingLoadingHtml(label) {"):
        _REPORTING_JS.index("function reportingSourceErrorHtml(label) {")
    ]


# ============================================================================
# E — el timer de progreso ESTIMADO de Reporting NO llama render()
# ============================================================================

def test_sweep_timer_never_calls_render():
    sweep_fn = _REPORTING_JS[
        _REPORTING_JS.index("function _reportingHubProgressSweep() {"):
        _REPORTING_JS.index("\n}\n", _REPORTING_JS.index("function _reportingHubProgressSweep() {"))
    ]
    assert "render()" not in sweep_fn
    assert "_reportingHubProgressPaint();" in sweep_fn


def test_progress_paint_never_touches_reporting_main_content():
    paint_fn = _REPORTING_JS[
        _REPORTING_JS.index("function _reportingHubProgressPaint() {"):
        _REPORTING_JS.index("\n}\n", _REPORTING_JS.index("function _reportingHubProgressPaint() {"))
    ]
    assert "reporting-main-content" not in paint_fn
    assert "innerHTML" not in paint_fn


# ============================================================================
# F — el loader "Cargando…" se crea UNA vez por hydration (ver también G en
#     el harness — este test cubre la ubicación estructural del call site)
# ============================================================================

def test_progress_bar_is_invoked_from_exactly_the_one_first_hydration_loader_site():
    # Definición + 1 único call-site (el loader de PRIMERA hidratación en
    # render()) — si esto fuera >1, algún prefetch/warming en background
    # (Cartera Proyecto, Comercial Salud vía batch-a, etc.) estaría mostrando
    # progress indebidamente.
    assert _REPORTING_JS.count("reportingHubProgressBegin(expId)") == 2  # definición + el call-site


def test_progress_bar_call_site_is_only_inside_the_not_yet_hydrated_branch():
    hydrated_branch = _REPORTING_JS[
        _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"):
        _REPORTING_JS.index("} else {", _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"))
    ]
    assert "reportingHubProgressBegin" not in hydrated_branch


# ============================================================================
# H — early completion no espera la animación
# ============================================================================

def test_ready_branch_settles_progress_before_showing_content_with_no_delay():
    ready_branch = _REPORTING_JS[
        _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"):
        _REPORTING_JS.index("} else {", _REPORTING_JS.index("if (state.reportingTabHydrated[expId]) {"))
    ]
    assert "reportingHubProgressSettle(expId, true);" in ready_branch
    assert "setTimeout" not in ready_branch  # jamás una espera artificial para "terminar" la animación


def test_error_branch_settles_progress_before_showing_the_existing_fail_soft_error():
    loader_branch = _REPORTING_JS[
        _REPORTING_JS.index("const rawStatus = reportingTabRawStatus(expId);"):
        _REPORTING_JS.index("// Repinta el nodo COMPARTIDO según el tab ACTIVO")
    ]
    assert 'reportingHubProgressSettle(expId, false);' in loader_branch
    assert 'reportingSourceErrorHtml("los datos de esta pestaña")' in loader_branch  # fail-soft actual, sin cambios


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


# ============================================================================
# I — background prefetch/warming -> nunca activa el loader
# ============================================================================

def test_background_sister_loaders_never_reference_the_progress_primitive():
    for fn_name in ("loadCarteraProyecto", "hydrateComercialParticipacionTab"):
        start = _REPORTING_JS.index(f"function {fn_name}(")
        # Función completa hasta el próximo `\nfunction ` top-level.
        end = _REPORTING_JS.index("\nfunction ", start + 1)
        body = _REPORTING_JS[start:end]
        assert "reportingHubProgress" not in body, fn_name


# ============================================================================
# J — cero requests Graph/HTTP adicionales
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


# ============================================================================
# K — perspective switch ya hidratada -> nunca dispara first-loading
# ============================================================================
# Cubierto por el harness de glue (label "K": tab churn post-hidratación
# nunca re-dispara start()) — ver test_reporting_hub_progress_glue_invariants_hold.


# ============================================================================
# L — reduced-motion conserva comportamiento adecuado
# ============================================================================

def test_reduced_motion_globally_disables_any_transition_including_read_progress():
    # `.read-progress__fill` no tiene NINGUNA animación/transition propia
    # (el ancho se fija por JS, instantáneo) — y el kill-switch GLOBAL ya
    # existente (`* { transition: none !important }` bajo
    # prefers-reduced-motion) cubre cualquier transition que exista en el
    # Hub, sin necesidad de una regla nueva para este corrective.
    fill_rule = _TOOLS_CSS[_TOOLS_CSS.index(".read-progress__fill {"):]
    fill_rule = fill_rule[:fill_rule.index("}") + 1]
    assert "animation" not in fill_rule
    assert "transition" not in fill_rule
    compact = _APP_CSS.replace(" ", "")
    reduced_block = compact[compact.index("@media(prefers-reduced-motion:reduce){"):]
    reduced_block = reduced_block[:reduced_block.index("}}") + 2]
    assert "*{transition:none!important}" in reduced_block
