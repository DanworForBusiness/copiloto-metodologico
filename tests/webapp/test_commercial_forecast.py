"""
test_commercial_forecast.py — C6 (ver HANDOFF: "Pipeline Total / Ponderado →
Forecast"): el análisis "Forecast — Pipeline por Mes de Cierre" accionado por
los DOS tiles de Pipeline, cargado LAZY sobre el endpoint que ya existía
(`GET /comercial/data/forecast`, C2) y renderizado quirúrgicamente.

Las invariantes de comportamiento (helpers puros, estados del panel, ciclo
del lazy load, render quirúrgico) viven en commercial_forecast.node.js, que
ejecuta el motor REAL. Acá quedan las que son de ARCHIVO: que C6 fue
frontend-only, que no se tocó el motor compartido ni el template, y que la
composición nueva vive en el CSS de la vertical.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "commercial_forecast.node.js"
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")
_COMMERCIAL_CSS = (_STATIC / "commercial.css").read_text(encoding="utf-8")
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    """JS sin comentarios: los bloques que documentan estos contratos citan a
    propósito los literales que estos tests prohíben (patrón ya conocido en
    este repo — un comentario que menciona el literal prohibido hace fallar un
    scan del texto crudo). Todos los comentarios de commercial.js/app.js son
    de línea completa, así que basta con descartar esas líneas."""
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


def _css_rules(raw: str) -> str:
    """CSS sin comentarios: el encabezado y los bloques explicativos citan a
    propósito los literales que estos tests prohíben, y un scan del texto
    crudo daría un falso positivo (patrón ya conocido en este repo)."""
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.S)


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_forecast_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Forecast invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL FORECAST INVARIANTS PASSED" in result.stdout


def test_c6_never_touched_the_shared_breakdown_engine():
    # El motor de desglose (`initEventBreakdown`, app.js) resuelve por VALOR
    # del atributo, así que dos tiles con la MISMA key abren un solo panel sin
    # una línea nueva. C6 no lo modifica: no sabe nada de Comercial ni de
    # forecast, y sigue teniendo un único `show()` gobernando `hidden` +
    # `.is-active`.
    assert "forecast" not in _APP_JS.lower()
    assert _APP_JS.count("function initEventBreakdown(") == 1
    assert _js_code(_APP_JS).count("[data-breakdown-group]") == 1
    # La premisa EXACTA de la que depende el corrective visual de C6: el motor
    # marca `.is-active` por VALOR del atributo, o sea en TODOS los triggers de
    # ese valor. Nadie "arregló" eso en app.js — la normalización a un solo
    # marcador vive del lado de Comercial, sobre el nodo que el usuario activó.
    assert 'rows.forEach(r => r.classList.toggle("is-active", r.dataset.event === ev));' in _APP_JS


def test_single_visual_active_trigger_is_normalized_without_new_state():
    # C6 visual corrective (ver HANDOFF): varios triggers comparten un mismo
    # `data-event`, así que el motor los marca todos; Comercial deja
    # `.is-active` sólo en el que el usuario activó. Un único normalizador,
    # invocado desde los DOS caminos de activación reales (click y keydown) —
    # nunca una copia por camino, MISMO criterio que la navegación al abrir.
    assert _COMMERCIAL_JS.count("function commercialNormalizeSharedTriggerActiveState(") == 1
    assert _COMMERCIAL_JS.count("commercialNormalizeSharedTriggerActiveState(kpiTrigger, root)") == 2
    body = _COMMERCIAL_JS.split("function commercialNormalizeSharedTriggerActiveState(", 1)[1] \
        .split("\n}", 1)[0]
    # Cero estado persistente: el nodo activador llega por el evento y el DOM
    # es la única fuente de verdad.
    assert "commercialState." not in body
    # Se mira el CÓDIGO, no la prosa: el comentario que documenta este mismo
    # contrato nombra a propósito los literales prohibidos.
    code = _js_code(_COMMERCIAL_JS)
    for banned in ("forecastOpenedBy", "activeForecastTile", "pipelineEntrySource",
                   "openedBy", "entryTile", "activeTile"):
        assert banned not in code, banned
    # Cero red: cambiar de tile de entrada mueve una clase, nada más.
    assert "fetch(" not in body
    # Sólo actúa en una APERTURA — al cerrar, el motor ya dejó todo inactivo.
    assert 'if (!trigger.classList.contains("is-active")) return;' in body
    # GENÉRICO, no un caso especial de "forecast": para un análisis con un solo
    # trigger (Activas) es un no-op exacto.
    assert '"forecast"' not in body


def test_close_x_resolves_the_active_trigger_not_the_first_in_the_dom():
    # C6 visual corrective: con Forecast abierto desde "Pipeline ponderado", el
    # PRIMER `[data-event="forecast"]` del DOM es "Pipeline total", que está
    # inactivo — su toggle lo leería como apertura y el panel no se cerraría.
    close_branch = _COMMERCIAL_JS.split('closest("[data-commercial-close-analysis]");', 1)[1] \
        .split("return;", 1)[0]
    assert 'querySelector(`[data-event="${key}"].is-active`)?.click()' in close_branch
    # Sigue siendo un `.click()` REAL sobre el trigger: nunca manipulación
    # directa de hidden/open para esquivar la resolución del trigger.
    for banned in ("hidden =", ".open =", "classList"):
        assert banned not in close_branch, banned
    assert "commercialState." not in close_branch


def test_c6_was_frontend_only_and_left_the_template_alone():
    # El endpoint, el service y el router ya existían (C2) — C6 sólo agrega el
    # consumidor. Y el template no hace falta tocarlo: `commercial.css` ya
    # estaba linkeado y el panel lo dibuja el JS.
    crm_html = (_ROOT / "webapp" / "templates" / "commercial" / "crm.html").read_text(encoding="utf-8")
    assert "forecast" not in crm_html.lower()
    assert "path='commercial.css'" in crm_html
    router = (_ROOT / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
    assert router.count('@router.get("/comercial/data/forecast"') == 1
    service = (_ROOT / "webapp" / "services" / "commercial_service.py").read_text(encoding="utf-8")
    assert service.count("def get_forecast(") == 1 and service.count("def _build_forecast(") == 1
    surface = (_ROOT / "tests" / "route_surface.json").read_text(encoding="utf-8")
    assert "GET /comercial/data/forecast" in surface


def test_forecast_is_lazy_and_has_a_single_activation_bridge():
    # Un solo puente entre el motor compartido y el lazy load, invocado desde
    # los DOS caminos de activación reales (click y keydown Enter/Espacio) —
    # nunca una copia por camino, mismo criterio que
    # `commercialMaybeScrollToOpenedAnalysis`.
    assert _COMMERCIAL_JS.count("function commercialMaybeLoadForecast(") == 1
    assert _COMMERCIAL_JS.count("commercialMaybeLoadForecast(kpiTrigger)") == 2
    assert _COMMERCIAL_JS.count("async function commercialLoadForecast()") == 1
    # El load inicial del landing NO lo incluye: forecast no agrega una
    # request remota a cada carga del Dashboard.
    eager = _COMMERCIAL_JS.split("async function commercialLoadData()", 1)[1].split("\n}", 1)[0]
    assert "/comercial/data/forecast" not in eager
    assert eager.count("/comercial/data/") == 4
    # Y el cambio de tipo lo invalida sin re-pedirlo.
    set_tipo = _COMMERCIAL_JS.split("function commercialSetTipo(", 1)[1].split("\n}", 1)[0]
    assert 'commercialState.forecastStatus = "idle"' in set_tipo
    assert "commercialLoadForecast" not in set_tipo


def test_forecast_close_reuses_the_parametrized_activas_mechanism():
    # UN solo mecanismo de cierre para los dos análisis: el atributo lleva la
    # key y el delegado sintetiza un click sobre el trigger correspondiente.
    # Cero estado propio de abierto/cerrado.
    assert _js_code(_COMMERCIAL_JS).count("[data-commercial-close-analysis]") == 1
    for key in ("activas", "forecast"):
        assert f'data-commercial-close-analysis="{key}"' in _COMMERCIAL_JS
    close_branch = _COMMERCIAL_JS.split('closest("[data-commercial-close-analysis]");', 1)[1].split("return;", 1)[0]
    assert "closeBtn.dataset.commercialCloseAnalysis" in close_branch
    assert "commercialState." not in close_branch


def test_forecast_chart_composition_lives_in_the_vertical_css():
    # MISMO ownership gate que `.category-plot` (ver encabezado de
    # commercial.css): es la composición concreta de este gráfico, no un
    # primitivo del Hub. Se promoverá si aparece un segundo consumidor real.
    for rule in (".forecast-plot {", ".forecast-plot__scroll {", ".forecast-scope {"):
        assert rule in _COMMERCIAL_CSS, rule
        assert rule not in _TOOLS_CSS, rule


def test_forecast_uses_one_axis_and_reuses_the_existing_hour_tones():
    rules = _css_rules(_COMMERCIAL_CSS)
    plot = rules.split(".forecast-plot {", 1)[1].split("}", 1)[0]
    # UN eje: dos columnas de grid, sin la canaleta derecha de 52px que
    # quedaría vacía (las dos series son horas y comparten escala).
    grid = [d for d in plot.split(";") if "grid-template-columns" in d][0]
    assert grid.strip() == "grid-template-columns: 38px 1fr"
    assert "52px" not in grid
    # Cero color nuevo: el eje toma el MISMO par claro/oscuro que ya usa el
    # eje de horas del Funnel, y las barras los mismos dos tonos dorados.
    assert ".forecast-plot > .category-plot__axis--left span { color: #a67c00; }" in rules
    assert ".content-theme-dark .forecast-plot > .category-plot__axis--left span { color: #e8820a; }" in rules
    # Los dos tonos son LITERALMENTE los que ya usaba el Funnel para el mismo
    # par de series: no se introduce ninguna semántica de color nueva.
    assert ".category-plot__bar--weighted { background: #a67c00; }" in rules
    assert ".content-theme-dark .category-plot__bar--weighted { background: #e8820a; }" in rules


def test_forecast_density_is_solved_with_horizontal_overflow_not_truncation():
    rules = _css_rules(_COMMERCIAL_CSS)
    # El scroll box es propio y envuelve área + etiquetas juntas; el eje queda
    # afuera, fijo.
    assert ".forecast-plot__scroll { overflow-x: auto; }" in rules
    # `width:max-content` + `min-width:100%` es lo que da "usa el ancho
    # disponible cuando entran, scrollea cuando no" sin ninguna rama en JS.
    assert "width: max-content; min-width: 100%;" in rules
    # Ancho mínimo por período, ajustable en el smoke visual del owner.
    assert "--forecast-col-min:" in rules
    assert "min-width: var(--forecast-col-min);" in rules
    # Nada de truncar, ni de esconder etiquetas alternadas, ni de rotarlas como
    # solución por default: las etiquetas quedan horizontales y legibles.
    forecast_css = rules.split(".forecast-plot {", 1)[1].split(".hero-lead-row", 1)[0]
    for banned in ("nth-child(2n)", "rotate", "display: none", "text-overflow"):
        assert banned not in forecast_css, banned


def test_forecast_tooltip_override_is_positional_never_a_redeclaration():
    rules = _css_rules(_COMMERCIAL_CSS)
    # El tooltip vive DENTRO del área del gráfico porque el scroll box recorta
    # en ambos ejes (`overflow-x:auto` hace que `overflow-y` compute `auto`).
    # Es un override POSICIONAL — `.chart-tooltip` sigue siendo del Hub.
    for line in rules.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(".chart-tooltip {"), stripped
        assert not stripped.startswith(".chart-tooltip-anchor {"), stripped
    assert ".forecast-plot .chart-tooltip," in rules
    # El clamp horizontal es una decisión PURA y testeable, y sólo se aplica
    # dentro del scroll del Forecast: el camino del Funnel y de la tabla
    # (flip vertical contra `.matrix-scroll`/`.dashboard-section`) queda igual.
    assert _COMMERCIAL_JS.count("function commercialTooltipShift(") == 1
    assert 'closest(".forecast-plot__scroll")' in _COMMERCIAL_JS
    assert 'closest(".matrix-scroll, .dashboard-section")' in _COMMERCIAL_JS


def test_c6_adds_no_month_drill_affordance():
    # C7 (click en el mes → detalle embebido) NO se adelanta: cero handler,
    # cero identificador de mes pensado para click, cero cursor:pointer, cero
    # estado prearmado.
    forecast_chart = _js_code(_COMMERCIAL_JS).split("function commercialForecastChartHtml(", 1)[1] \
        .split("function commercialForecastPanelBodyHtml(", 1)[0]
    for banned in ("data-commercial-forecast-month", "data-month", 'role="button"', "onclick", "cursor:"):
        assert banned not in forecast_chart, banned
    rules = _css_rules(_COMMERCIAL_CSS)
    forecast_css = rules.split(".forecast-plot {", 1)[1].split(".hero-lead-row", 1)[0]
    assert "cursor: pointer" not in forecast_css and "cursor:pointer" not in forecast_css
    # Y el estado del workspace no gana ningún campo de drill.
    state = _COMMERCIAL_JS.split("const commercialState = {", 1)[1].split("\n};", 1)[0]
    assert "forecastMonth" not in state and "forecastDrill" not in state


def test_forecast_and_origen_stay_separate_increments():
    # C6 se entregó SIN "Oportunidades por Origen" a propósito, para que cada
    # incremento fuera reversible solo. C8 lo agregó — pero en el análisis de
    # ACTIVAS, no acá: los dos bloques siguen siendo independientes y ninguno
    # depende del otro. Lo que se afirma ya no es "Origen no existe" (existe),
    # sino que no se contaminaron entre sí.
    code = _js_code(_COMMERCIAL_JS)
    forecast_panel = code.split("function commercialForecastPanelBodyHtml(", 1)[1] \
        .split("function commercialForecastCountLabel(", 1)[0]
    for banned in ("Origen", "commercialDeriveOrigins", "commercialOriginsHtml", "origin-breakdown"):
        assert banned not in forecast_panel, banned
    origin_html = code.split("function commercialOriginsHtml(", 1)[1] \
        .split("function commercialFunnelAreaHtml(", 1)[0]
    for banned in ("forecast", "Forecast"):
        assert banned not in origin_html, banned
    # Y ninguno de los dos toca el backend: `source` y el payload de forecast ya
    # venían del service, C6/C8 sólo agregaron consumidores.
    assert "source_id" not in code, "el frontend nunca ve el campo crudo de Odoo"


# ── Regresion: el contrato de alto fijo de Forecast no es sobreescribible ───
# C8 (7182877) genero el layout de dos columnas para que lo compartieran
# Forecast y el analisis de Activas, y agrego tres reglas de CRECIMIENTO del
# grafico scopeadas a `.analysis-split__chart`. Una de ellas
# —`.category-plot__area { height: auto; min-height: 188px }`— alcanzaba
# tambien a Forecast, que tiene su propio `.category-plot__area` dentro de esa
# misma columna, y le ganaba por ORDEN DE FUENTE a
# `.forecast-plot .category-plot__area` (misma especificidad, mas arriba en el
# archivo). Medido: la columna colapsaba de 187px a 2px, todas las barras a
# 2px con el eje rotulando bien la escala, y aparecia scrollbar vertical
# porque los tooltips ocultos caian 83px fuera del scroll box.
#
# Estos tests fijan la PROPIEDAD de la regla, no pixeles: el crecimiento es
# del panel que alinea arranques (`--top`), y Forecast conserva su alto fijo.
_GEOMETRY_PROPS = {"height", "min-height", "max-height",
                   "flex", "flex-grow", "flex-basis", "grid-template-rows"}


def _rules_touching_the_chart_column(raw: str):
    """(selector, props) de cada regla que gobierna la columna del grafico."""
    out = []
    for selector, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", _css_rules(raw)):
        selector = " ".join(selector.split())
        if ".analysis-split__chart" not in selector:
            continue
        props = {d.split(":", 1)[0].strip() for d in decls.split(";") if ":" in d}
        out.append((selector, props))
    return out


def test_chart_growth_rules_are_owned_by_the_top_variant_not_by_the_split():
    # Regla GENERICA, no una lista de tres selectores: cualquier regla futura
    # que toque la geometria de la caja del grafico desde la columna del split
    # tiene que declarar de que panel es. Si no, se cuela en el otro.
    unowned = [s for s, props in _rules_touching_the_chart_column(_COMMERCIAL_CSS)
               if props & _GEOMETRY_PROPS and ".analysis-split--top" not in s]
    assert not unowned, f"reglas de geometria sin panel declarado: {unowned}"
    # Y las tres que existen hoy siguen existiendo, con `--top`.
    rules = _css_rules(_COMMERCIAL_CSS)
    for grow in (".analysis-split--top .analysis-split__chart > [data-commercial-dist]"
                 " { display: flex; flex-direction: column; flex: 1 1 auto; }",
                 ".analysis-split--top .analysis-split__chart .category-plot"
                 " { flex: 1 1 auto; grid-template-rows: 1fr auto; }",
                 ".analysis-split--top .analysis-split__chart .category-plot__area"
                 " { height: auto; min-height: 188px; }"):
        assert grow in rules, grow


def test_forecast_keeps_its_own_fixed_height_contract():
    rules = _css_rules(_COMMERCIAL_CSS)
    # El token es la unica fuente del alto, y el area lo consume.
    assert "--forecast-plot-h: 188px;" in rules
    assert ".forecast-plot .category-plot__area { height: var(--forecast-plot-h); }" in rules
    # El eje izquierdo lee el MISMO token — es lo que hace que los ticks caigan
    # sobre las lineas de grilla.
    assert ".forecast-plot > .category-plot__axis--left { height: var(--forecast-plot-h); }" in rules
    # Nadie le puso `min-height` al area de Forecast: `min-height` fija el alto
    # USADO pero no vuelve el alto DEFINIDO, asi que `height: 100%` de la
    # columna resolveria contra `auto` y colapsaria a los 2px de la barra.
    for selector, props in _rules_touching_the_chart_column(_COMMERCIAL_CSS):
        if ".analysis-split--top" in selector:
            continue
        assert "min-height" not in props, selector
    assert ".category-plot__col { position: relative; flex: 1 1 0; min-width: 0; height: 100%;" in rules
    assert "min-height: 2px;" in rules      # el 2px al que colapsaba la barra


def test_the_leak_vector_is_real_so_the_ownership_test_is_not_vacuous():
    # Sin esto, los dos tests de arriba podrian pasar por casualidad: prueban
    # que Forecast EFECTIVAMENTE vive dentro de `.analysis-split__chart` y
    # EFECTIVAMENTE tiene un `.category-plot__area`, o sea que la regla sin
    # `--top` lo alcanzaria.
    markup = _js_code(_COMMERCIAL_JS)
    chart = markup.split("function commercialForecastChartHtml(", 1)[1].split("\n}", 1)[0]
    assert "category-plot__area" in chart
    # El grafico de Forecast se emite DENTRO de la columna del split base.
    plain_block = markup.split('class="analysis-split">', 1)[1][:400]
    assert "analysis-split__chart" in plain_block
    assert "commercialForecastChartHtml" in plain_block


def test_forecast_and_the_activas_analysis_stay_different_by_design():
    markup = _js_code(_COMMERCIAL_JS)
    # Activas declara `--top` (arranques alineados + crecimiento); Forecast no
    # (piso compartido + alto fijo). Un solo consumidor de cada variante.
    assert markup.count('class="analysis-split analysis-split--top"') == 1
    assert markup.count('class="analysis-split">') == 1
    # El que lleva `--top` es el de Activas, no el de Forecast.
    top_block = markup.split('class="analysis-split analysis-split--top"', 1)[1][:400]
    assert "data-commercial-dist" in top_block and "forecast" not in top_block.lower()
    plain_block = markup.split('class="analysis-split">', 1)[1][:400]
    assert "commercialForecastChartHtml" in plain_block
    rules = _css_rules(_COMMERCIAL_CSS)
    # Y las dos decisiones de anclaje siguen siendo explicitas y opuestas.
    assert ".analysis-split__chart { display: flex; flex-direction: column; justify-content: flex-end; }" in rules
    assert ".analysis-split--top .analysis-split__chart { justify-content: flex-start; }" in rules


def test_activas_funnel_contract_is_untouched_by_c6():
    # Regresión: el Funnel de Activas sigue con sus TRES series, sus DOS ejes
    # y su universo 1-8 — C6 no comparte geometría con él.
    funnel = _COMMERCIAL_JS.split("function commercialDistribucionHtml(", 1)[1] \
        .split("\n// ── Forecast", 1)[0]
    for series in ("category-plot__bar--count", "category-plot__bar--hours", "category-plot__bar--weighted"):
        assert series in funnel, series
    assert 'commercialAxisHtml("left", maxCount)' in funnel
    assert 'commercialAxisHtml("right", maxHours)' in funnel
    assert "Funnel por Etapa" in funnel
    rules = _css_rules(_COMMERCIAL_CSS)
    # La geometría de dos ejes del Funnel queda intacta (nadie la reescribió
    # para "compartirla" con el Forecast).
    assert ".category-plot { display: grid; grid-template-columns: 38px 1fr 52px;" in rules
    assert ".category-plot__area {" in rules and "height: 188px;" in rules
