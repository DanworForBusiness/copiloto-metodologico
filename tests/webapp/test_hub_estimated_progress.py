"""
test_hub_estimated_progress.py — Contrato de HUB FOREGROUND PROGRESS tras dos
correctivas sucesivas:
1) "STOP REINVENTING THE HUB LOADER": el Hub tiene UN SOLO loader/progress
   bar real — `.read-progress`/`.read-progress__fill` (montado en
   platform_topbar.html, alimentado por scroll REAL en Copiloto vía app.js
   `copilotLayout().init()`, SIN CAMBIOS).
2) "el loader pertenece al Hub, no a quien lo dispara": se extrajo un
   BOUNDARY compartido — `HubForegroundProgress.begin/complete/fail` (app.js)
   — que es el ÚNICO que toca DOM/CSS/timer de la barra. Reporting
   (fetch()+Alpine, primera hidratación Y cold-year) y Staffing/Asignaciones
   (htmx) SOLO notifican lifecycle a ese boundary; ninguno de los dos sabe
   de la existencia de `.read-progress__fill`.

Tres capas:
1) ejecución REAL vía Node (mismo patrón que
   test_reporting_initial_load_contract.py):
   - hub_estimated_progress_primitive.node.js — matemática pura
     (checkpoints/overrun/EWMA/localStorage) contra `app.js`;
   - hub_foreground_progress.node.js — el BOUNDARY en sí (begin/complete/
     fail, timer compartido, pintado real sobre `.read-progress__fill`) MÁS
     el listener htmx real de Asignaciones (`#staffing-rows`), extraídos y
     ejecutados juntos de `app.js`;
   - reporting_hub_estimated_progress_glue.node.js — el GLUE de reporting.js
     (primera hidratación + los 4 loaders de cold-year) contra un stub/spy
     de `HubForegroundProgress`, con `render()` REAL.
2) contrato de texto fuente (mismo criterio que test_reporting_batch_b.py
   §"Frontend") para invariantes estructurales: que NO exista un segundo
   nodo/clase de progress, que Copiloto/Asignaciones queden intactos, que
   ningún consumidor conozca DOM/CSS del progress, etc.
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
_STAFFING_HTML = (_TEMPLATES / "operations" / "staffing.html").read_text(encoding="utf-8")

_PRIMITIVE_HARNESS = Path(__file__).resolve().parent / "hub_estimated_progress_primitive.node.js"
_BOUNDARY_HARNESS = Path(__file__).resolve().parent / "hub_foreground_progress.node.js"
_GLUE_HARNESS = Path(__file__).resolve().parent / "reporting_hub_estimated_progress_glue.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_estimated_progress_primitive_invariants_hold():
    result = subprocess.run(["node", str(_PRIMITIVE_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"HubEstimatedProgress primitive invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB ESTIMATED PROGRESS PRIMITIVE INVARIANTS PASSED" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_foreground_progress_boundary_invariants_hold():
    # Cubre, con código REAL (begin/complete/fail + timer + pintado sobre
    # .read-progress__fill + el listener htmx real de Asignaciones): begin
    # idempotente, complete graba EWMA, fail no graba, el timer se auto-
    # limpia, y el listener de #staffing-rows dispara/limpia correctamente
    # sin activarse para elementos no relacionados (background nunca
    # activa la barra) — ver el .node.js para el detalle.
    result = subprocess.run(["node", str(_BOUNDARY_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"HubForegroundProgress boundary invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB FOREGROUND PROGRESS BOUNDARY INVARIANTS PASSED" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_reporting_hub_progress_glue_invariants_hold():
    # Cubre, ejecutando render() y los 4 loaders de cold-year REALES: A
    # (begin único/seed), G (ticks del sweep nunca recrean el DOM de
    # Reporting), H (early finish notifica complete sin esperar), K
    # (perspective/tab churn no re-dispara), E/D (fail-soft + cleanup en
    # error), I/D (background settle sin re-render, sweep se auto-limpia),
    # y COLD-YEAR (Cartera/Rentabilidad/Horas Vendidas/Participación:
    # ownership dentro de la operación real, keys correctas, cero fetches
    # adicionales) — ver el .node.js para el detalle.
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

def test_reporting_never_touches_the_progress_dom_itself():
    # Reporting NUNCA toca `.read-progress__fill`/DOM del progress — eso es
    # responsabilidad EXCLUSIVA de HubForegroundProgress (app.js). Reporting
    # SOLO notifica lifecycle (begin/complete/fail) sobre ese boundary.
    assert "querySelector" not in _REPORTING_JS
    assert ".read-progress" not in _REPORTING_JS
    assert "HubForegroundProgress.begin(" in _REPORTING_JS
    assert "HubForegroundProgress.complete(" in _REPORTING_JS
    assert "HubForegroundProgress.fail(" in _REPORTING_JS


def test_hub_foreground_progress_boundary_writes_to_the_real_read_progress_fill_node():
    paint_fn = _APP_JS[
        _APP_JS.index("function paint() {"):
        _APP_JS.index("\n  }\n", _APP_JS.index("function paint() {"))
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
    # Delimitado por el CIERRE de la propia función, no por "lo que viene
    # después": el corte posicional se rompía al insertar cualquier helper
    # entre las dos (pasó con `reportingEmptyStateHtml`), y lo que este test
    # afirma es sobre el LOADER, no sobre sus vecinos.
    loader = _REPORTING_JS.split("function reportingLoadingHtml(label) {", 1)[1].split("\n}", 1)[0]
    assert "<div" not in loader


# ============================================================================
# E — el timer de progreso ESTIMADO de Reporting NO llama render()
# ============================================================================

def test_sweep_timer_never_calls_render():
    # El sweep de PRIMERA hidratación de reporting.js (necesario porque
    # HubForegroundProgress no sabe de tabs de Reporting — ver comentario en
    # el archivo) solo lee status y notifica settle, nunca re-renderiza.
    sweep_fn = _REPORTING_JS[
        _REPORTING_JS.index("function _reportingForegroundSweep() {"):
        _REPORTING_JS.index("\n}\n", _REPORTING_JS.index("function _reportingForegroundSweep() {"))
    ]
    assert "render()" not in sweep_fn
    assert "reportingTabRawStatus(expId)" in sweep_fn


def test_hub_foreground_progress_boundary_never_touches_reporting_main_content():
    paint_fn = _APP_JS[
        _APP_JS.index("function paint() {"):
        _APP_JS.index("\n  }\n", _APP_JS.index("function paint() {"))
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
        _REPORTING_JS.index("// NOTA: no hay un \"repaint\" acá")
    ]
    assert 'reportingHubProgressSettle(expId, false);' in loader_branch
    assert 'reportingSourceErrorHtml("los datos de esta pestaña")' in loader_branch  # fail-soft actual, sin cambios


def test_settle_notifies_the_foreground_boundary_and_frees_tracking():
    settle_fn = _REPORTING_JS[
        _REPORTING_JS.index("function reportingHubProgressSettle(expId, success) {"):
        _REPORTING_JS.index("\n}\n", _REPORTING_JS.index("function reportingHubProgressSettle(expId, success) {"))
    ]
    assert "_reportingForegroundExpIds.delete(expId);" in settle_fn
    assert "HubForegroundProgress.complete(REPORTING_HUB_PROGRESS[expId].key);" in settle_fn
    assert "HubForegroundProgress.fail(REPORTING_HUB_PROGRESS[expId].key);" in settle_fn


def test_first_hydration_sweep_self_clears_once_no_tab_is_waiting():
    sweep_fn = _REPORTING_JS[_REPORTING_JS.index("function _reportingForegroundSweep() {"):]
    assert "clearInterval(_reportingForegroundSweepTimer);" in sweep_fn
    assert "!_reportingForegroundExpIds.size" in sweep_fn


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
# AMPLIACIÓN — "toda espera Graph FOREGROUND visible, sin importar qué
# vertical la inició" (owner corrective): cold-year de Reporting +
# Asignaciones (Staffing, vía htmx) también deben notificar el MISMO
# boundary, nunca detectado desde render()/un listener genérico de "hubo un
# fetch".
# ============================================================================

def test_cold_year_loaders_own_the_begin_complete_fail_lifecycle_themselves():
    # El ownership vive DENTRO de la operación real (nunca detectado desde
    # render()) — cada uno de los 4 loaders de cold-year llama
    # HubForegroundProgress directamente, con la MISMA key que su propia
    # primera hidratación (Participación usa una key propia, ver abajo).
    expected_keys = {
        "loadCarteraMantenimientoColdYear": "cartera",
        "loadRentabilidadColdYear": "rentabilidad",
        "loadHorasVendidasColdYear": "horasvendidas",
        "loadComercialParticipacionColdYears": "participacion",
    }
    for fn_name, key in expected_keys.items():
        start = _REPORTING_JS.index(f"async function {fn_name}(")
        end = _REPORTING_JS.index("\n}\n", start)
        body = _REPORTING_JS[start:end]
        assert f'HubForegroundProgress.begin("{key}"' in body, fn_name
        assert f'HubForegroundProgress.complete("{key}")' in body, fn_name
        assert f'HubForegroundProgress.fail("{key}")' in body, fn_name
        # Nunca embebido en render() ni en el sweep de primera hidratación.
        assert "render()" in body  # sigue re-renderizando al terminar (comportamiento existente, sin cambios)


def test_cold_year_participacion_never_reuses_the_batch_a_key():
    # Participación SIEMPRE pega contra /data/participacion — un endpoint
    # real distinto de Salud/Calendario (/data/batch-a) — key propia, nunca
    # compartida, para que el estimate aprendido no se contamine entre dos
    # operaciones reales distintas.
    start = _REPORTING_JS.index("async function loadComercialParticipacionColdYears(")
    end = _REPORTING_JS.index("\n}\n", start)
    body = _REPORTING_JS[start:end]
    assert '"batch-a"' not in body


def test_no_dispatcher_still_self_detects_cold_year_progress_from_render():
    # El ownership NO debe vivir en render()/renderCartera()/etc. — ver
    # owner corrective: "No agregar lógica al render() sólo para detectar
    # visualmente que dice 'Cargando...'". Los ÚNICOS call-sites de
    # HubForegroundProgress.begin/complete/fail con una key de cold-year
    # ("cartera"/"rentabilidad"/"horasvendidas"/"participacion") deben vivir
    # dentro de los propios loaders de cold-year, no en ningún `render*`.
    for fn_name in ("renderCartera", "renderRentabilidad", "renderHorasVendidas", "renderComercialParticipacion"):
        start = _REPORTING_JS.index(f"function {fn_name}(")
        end = _REPORTING_JS.index("\nfunction ", start + 1)
        body = _REPORTING_JS[start:end]
        assert "HubForegroundProgress" not in body, fn_name


# ============================================================================
# Asignaciones (Staffing) — first load vía htmx, MISMO boundary.
# ============================================================================

def test_staffing_first_load_is_htmx_driven_and_wires_the_shared_boundary():
    assert 'hx-post="/operaciones/staffing/asignaciones/data"' in _STAFFING_HTML
    assert 'hx-trigger="load"' in _STAFFING_HTML
    assert 'id="staffing-rows"' in _STAFFING_HTML
    # El listener vive en app.js (compartido, NUNCA un archivo propio de
    # Staffing) y detecta la request por el ELEMENTO que la disparó — nunca
    # asumiendo "cualquier fetch Graph es foreground".
    assert 'event.detail.elt?.closest("#staffing-rows")' in _APP_JS
    assert 'HubForegroundProgress.begin("assignments")' in _APP_JS
    assert 'HubForegroundProgress.complete("assignments")' in _APP_JS
    assert 'HubForegroundProgress.fail("assignments")' in _APP_JS


def test_staffing_html_contains_no_progress_markup_of_its_own():
    # Asignaciones NUNCA emite su propio DOM/CSS de progress — ni
    # `.read-progress` ni ningún nombre de clase/id de progress inventado.
    assert "read-progress" not in _STAFFING_HTML
    assert "hub-progress" not in _STAFFING_HTML


def test_staffing_background_loaders_never_reference_the_foreground_boundary():
    # Ausencias/historial inactivo son cargas AUXILIARES (fetch() plano, no
    # htmx, no bloquean la superficie de Asignaciones) — nunca deben activar
    # el progress compartido.
    for fn_name in ("_loadAbsences", "_loadInactiveHistory"):
        start = _APP_JS.index(f"async {fn_name}(")
        end = _APP_JS.index("\n    },\n", start)
        body = _APP_JS[start:end]
        assert "HubForegroundProgress" not in body, fn_name


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
