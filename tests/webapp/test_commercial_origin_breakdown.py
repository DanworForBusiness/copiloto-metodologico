"""
test_commercial_origin_breakdown.py — C8 (ver HANDOFF): "Oportunidades por
Origen" dentro del análisis de Oportunidades Activas.

Las invariantes de comportamiento (agregación, composición de filtros,
presentación, ausencia de drill) viven en commercial_origin_breakdown.node.js,
que ejecuta el motor REAL. Acá quedan las de ARCHIVO: que el incremento fue
frontend-only, que no se acopló a una clase de Forecast, y que no se tocó ni
el backend ni el motor compartido.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "commercial_origin_breakdown.node.js"
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")
_COMMERCIAL_CSS = (_STATIC / "commercial.css").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    """JS sin comentarios: los bloques que documentan estos contratos citan a
    propósito los literales que estos tests prohíben."""
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


def _css_rules(raw: str) -> str:
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.S)


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_origin_breakdown_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Origin breakdown invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL ORIGIN BREAKDOWN INVARIANTS PASSED" in result.stdout


def test_origin_is_frontend_only_over_the_payload_already_in_memory():
    # `source` viaja en cada oportunidad desde C4; el Hub ya tiene el dato. C8
    # no agrega endpoint, ni query, ni fetch, ni superficie de ruta.
    service = (_ROOT / "webapp" / "services" / "commercial_service.py").read_text(encoding="utf-8")
    assert '"source": opp["source_id"][1] if opp.get("source_id") else "Sin definir"' in service
    surface = (_ROOT / "tests" / "route_surface.json").read_text(encoding="utf-8")
    assert "origen" not in surface.lower() and "origin" not in surface.lower()
    # El frontend sigue pidiendo los MISMOS endpoints de siempre para Origin
    # (C8 no agregó ninguno): cuatro en el load inicial + activities +
    # forecast lazy + creadas lazy + prospects lazy + ganadas lazy + perdidas
    # lazy + suspendidas lazy + pipeline-analytics-detail lazy +
    # lead-conversion-detail lazy (Creadas Legacy Parity, Commercial Tile 5,
    # 6, 7, 8 y Analítica del Pipeline, ver HANDOFF — siete incrementos
    # POSTERIORES y no relacionados con Origin, que sí agregaron el suyo
    # propio cada uno).
    code = _js_code(_COMMERCIAL_JS)
    # 11 y no 10: el corrective hot/cold agrega
    # `/comercial/data/creadas/anios-disponibles` — descubrimiento de años
    # SEPARADO de los datos, mismo criterio que los `anios-disponibles` de
    # Reporting. Si viajara dentro del payload, el `<select>` solo podria
    # ofrecer los años ya hidratados. Commercial Tile 5 (ver HANDOFF) agrega
    # `/comercial/data/prospects`, lazy, MISMO patrón que forecast/creadas.
    # Commercial Tile 6 (ver HANDOFF) agrega `/comercial/data/ganadas`,
    # lazy, año-scoped — SIN un endpoint de descubrimiento propio (el
    # selector de año es un rango estático, no un `anios-disponibles`).
    # Commercial Tile 7 (ver HANDOFF) agrega `/comercial/data/perdidas`,
    # MISMO patrón que Ganadas — el desglose por Motivo se deriva
    # CLIENT-SIDE de esas mismas filas, así que no suma un endpoint propio.
    # Commercial Tile 8 (ver HANDOFF) agrega `/comercial/data/suspendidas`,
    # lazy, SIN año — MISMO patrón que Prospectos. Analítica del Pipeline
    # (ver HANDOFF) agrega DOS: `/comercial/data/pipeline-analytics-detail`
    # (CINCO sub-vistas, UN fetch compartido) y
    # `/comercial/data/lead-conversion-detail` (universo propio). Owner
    # corrective "SCOPED RETRY FOR PIPELINE ANALYTICS" (ver HANDOFF) agrega
    # UNA ocurrencia MÁS de `/comercial/data/analytics` — CERO endpoint
    # nuevo, es el MISMO literal ya contado en la carga inicial
    # (`commercialLoadData`), repetido en `commercialLoadAnalytics` (el
    # reintento escopeado pega al mismo endpoint).
    # Bucket 1 (ver HANDOFF) agrega DOS más: `/comercial/data/
    # opportunities-detail` (compartido por Licencias Y Detalle
    # Oportunidades — el legacy ya los trata como la misma fuente, cero
    # endpoint propio para Licencias) y `/comercial/data/lost-history`
    # (Historial de Perdidas, universo propio).
    assert code.count("/comercial/data/") == 17  # 4 iniciales + activities + forecast + creadas + creadas-años + prospects + ganadas + perdidas + suspendidas + pipeline-analytics-detail + lead-conversion-detail + retry de analytics (mismo endpoint) + opportunities-detail + lost-history
    # No se usa el endpoint legacy: se mira el CÓDIGO, no la prosa (los
    # comentarios lo nombran a propósito para explicar por qué NO se usa).
    for banned in ("/crm/origen", "/comercial/data/origen", "/comercial/data/origin"):
        assert banned not in code, banned
    # Y la derivación es una función pura: recibe el subset, no lee estado.
    derive = code.split("function commercialDeriveOrigins(", 1)[1].split("\n}", 1)[0]
    assert "commercialState." not in derive
    assert "fetch(" not in derive


def test_origin_reuses_the_shared_bar_primitives_without_coupling_to_forecast():
    # `.bar-track`/`.bar-fill` son primitivos COMPARTIDOS del Hub (tools.css).
    # Origen los consume directo; no reusa `.forecast-bar-cell`, que es la
    # composición "barra + número en la misma celda" — otra cosa.
    assert ".bar-track {" in _TOOLS_CSS and ".bar-fill {" in _TOOLS_CSS
    rules = _css_rules(_COMMERCIAL_CSS)
    assert ".origin-breakdown .bar-track {" in rules
    # Lo único que aporta la regla local es el contexto de bloque dentro de la
    # celda: cero color y cero forma propios.
    local = rules.split(".origin-breakdown .bar-track {", 1)[1].split("}", 1)[0]
    for banned in ("background", "color", "border-radius", "var(--gold)"):
        assert banned not in local, banned
    # El markup de Origen no menciona ninguna clase de Forecast.
    origin_html = _js_code(_COMMERCIAL_JS).split("function commercialOriginsHtml(", 1)[1] \
        .split("function commercialFunnelAreaHtml(", 1)[0]
    assert "forecast" not in origin_html.lower()
    # Ni al revés: la composición "barra + número en la misma celda" sigue con
    # su único consumidor (la tabla de Forecast).
    assert rules.count(".forecast-bar-cell") == 4  # 3 reglas + su override en la media query
    # El LAYOUT de dos columnas, en cambio, ya tiene dos consumidores reales
    # (Forecast y el análisis de Activas), así que lleva nombre neutro: llamarlo
    # `forecast-*` desde Activas sería mentir.
    assert ".analysis-split {" in rules
    assert ".forecast-split" not in rules
    # Las DOS variantes del split son explícitas y no se pisan: Forecast
    # comparte el PISO (el gráfico se ancla abajo) y el análisis de Activas
    # comparte el ARRANQUE (`--top`).
    assert ".analysis-split__chart { display: flex; flex-direction: column; justify-content: flex-end; }" in rules
    assert ".analysis-split--top .analysis-split__chart { justify-content: flex-start; }" in rules
    # El Funnel crece para llenar su columna, y eso está SCOPEADO a `--top`,
    # no al split: Forecast también vive en un `.analysis-split__chart` y tiene
    # su propio `.category-plot__area`, así que sin el modificador la regla se
    # le colaba (ver test_forecast_fixed_height_contract_is_not_overridable...
    # en test_commercial_forecast.py). El `height: 188px` base y el override de
    # Forecast quedan intactos.
    assert ".analysis-split--top .analysis-split__chart .category-plot__area { height: auto; min-height: 188px; }" in rules
    assert "height: 188px;" in rules            # la regla base sigue existiendo
    assert ".forecast-plot .category-plot__area { height: var(--forecast-plot-h); }" in rules
    # El separador análisis|detalle no usa `var(--line)`: ese token está
    # scopeado a `.copilot-panel`, así que acá resolvería al valor pensado para
    # fondo oscuro y la línea sería casi invisible en claro.
    sep = rules.split(".analysis-split + .perspective-label { padding-top:", 1)[1].split("}", 1)[0]
    assert "var(--line)" not in sep and "rgba(128,128,128,.24)" in sep


def test_origin_lives_inside_the_activas_analysis_with_no_new_breakdown():
    # No es un panel nuevo: comparte el análisis expandido de Activas, así que
    # Origin en sí (C8) sigue sin agregar ningún `data-breakdown` propio —
    # el conteo de 14 (Activas + Forecast + Creadas + Prospectos + Ganadas +
    # Perdidas + Suspendidas + las seis de Analítica del Pipeline, todos
    # incrementos POSTERIORES y no relacionados — ver Creadas Legacy Parity,
    # Commercial Tile 5, 6, 7, 8 y Analítica del Pipeline en HANDOFF — + el
    # selector del scroll) es el mismo total que cualquier otro test de esta
    # suite que audite la superficie completa de paneles.
    markup = _js_code(_COMMERCIAL_JS)
    assert markup.count('data-breakdown="') == 14   # activas + forecast + creadas + prospectos + ganadas + perdidas + suspendidas + las seis de Analítica del Pipeline + el selector del scroll
    # Y vive en el host que YA se re-renderiza con Etapa/Vendedor y no con la
    # búsqueda: cero hooks de DOM nuevos.
    queries = re.findall(r"\.querySelector[A-Za-z]*\(([^)]*)\)", _COMMERCIAL_JS)
    # Bucket 1 (ver HANDOFF) agrega NUEVE: los TRES hosts estables
    # `[data-commercial-{licencias,detalle,historial}-table]` — cada uno
    # aparece TRES veces (orden de columna, un `change`/`input` de filtro
    # propio, y el "✕ Limpiar" del tab), MISMO patrón de re-render
    # quirúrgico que ya usa el resto de Comercial.
    assert len(queries) == 58, queries   # + los 8 de Analítica del Pipeline + los 2 de la atribución de Conversión de Leads + los 2 de la tabla de Tiempo por etapa/Conversión etapa a etapa + 1 de "Retrasadas"/"Por vencer" clickeables en Actividades + 1 de "[data-commercial-analytics-body]" + 1 de "[data-commercial-creadas-summary-tile]" + 9 de Bucket 1 (ver HANDOFF)
    assert '"[data-commercial-funnel-area]"' in queries
    # Un solo lugar arma el contenido de ese host — antes estaba duplicado.
    assert _COMMERCIAL_JS.count("function commercialFunnelAreaHtml(") == 1
    # Los DOS call sites (render completo + quirúrgico); el tercer match es la
    # propia definición.
    assert _COMMERCIAL_JS.count("commercialFunnelAreaHtml(dist, analysisFiltered)") == 2 + 1


def test_c8_adds_no_origin_drill_affordance():
    # El legacy tenía drill por origen; será un incremento aparte. Acá las
    # filas son informativas: cero handler, cero identificador de origen, cero
    # cursor:pointer, cero estado prearmado.
    code = _js_code(_COMMERCIAL_JS)
    origin_html = code.split("function commercialOriginsHtml(", 1)[1] \
        .split("function commercialFunnelAreaHtml(", 1)[0]
    for banned in ("onclick", "data-source", "data-commercial-origin", "cursor:",
                   'role="button"', "tabindex"):
        assert banned not in origin_html, banned
    state = code.split("const commercialState = {", 1)[1].split("\n};", 1)[0]
    # `\b` antes de "origin"/"origen" — la palabra como IDENTIFICADOR
    # propio, no como substring embebido en otro campo ya legítimo (owner
    # corrective de Conversión de Leads, ver HANDOFF §4, agregó
    # `leadConversionOrigenFilter` — "origen" de atribución UTM, NUNCA el
    # "origen" de Origin/C8 que esta guarda vigila).
    for banned in (r"\borigin", r"\borigen", "selectedOrigin", "originOpenedBy"):
        assert not re.search(banned, state, re.IGNORECASE), banned


def test_c8_left_backend_and_shared_engine_untouched():
    # "origen"/"origin" son palabras comunes (app.js las usa hablando del
    # origin de `postMessage`), así que se afirma lo que realmente importa:
    # ninguno de los dos ganó un hook, clase o helper de ESTE incremento.
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    router = (_ROOT / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
    for f, name in ((app_js, "app.js"), (router, "commercial.py")):
        for banned in ("origin-breakdown", "commercialDeriveOrigins", "commercialOriginsHtml",
                       "source_id", "/origen"):
            assert banned not in f, (name, banned)
    # `tools.css` no gana nada: Origen consume lo que ya estaba.
    assert ".origin-breakdown" not in _TOOLS_CSS


def test_forecast_and_activas_contracts_are_untouched_by_c8():
    code = _js_code(_COMMERCIAL_JS)
    # Forecast: sigue con su panel, su lazy load y su lectura por tile.
    for kept in ('data-breakdown="forecast"', "async function commercialLoadForecast()",
                 "function commercialForecastMode(", 'eventKey: "forecast"'):
        assert kept in code, kept
    # Activas: el Funnel conserva sus tres series y la búsqueda sigue siendo
    # sólo de tabla (un único consumidor de `oppSearchText` en el render).
    funnel = code.split("function commercialDistribucionHtml(", 1)[1].split("\n// ", 1)[0]
    for series in ("category-plot__bar--count", "category-plot__bar--hours",
                   "category-plot__bar--weighted"):
        assert series in funnel, series
    table_rows = code.split("function commercialCurrentTableRows(", 1)[1].split("\n}", 1)[0]
    assert "commercialState.oppSearchText" in table_rows
    subset = code.split("function commercialCurrentAnalysisFilteredOpportunities(", 1)[1].split("\n}", 1)[0]
    assert "oppSearchText" not in subset, "el subset analítico NO puede incluir la búsqueda"
