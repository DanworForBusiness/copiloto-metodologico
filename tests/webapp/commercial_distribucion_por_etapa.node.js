// commercial_distribucion_por_etapa.node.js — invocado por
// test_commercial_distribucion_por_etapa.py. "Funnel por Etapa" (Active
// Opportunities Phase 2 / C3/C5a, ver HANDOFF — restore legacy funnel
// representation + 3-series simultáneas): GROUPED BARS — Oportunidades
// (escala izquierda), Horas y Hs Ponderadas (AMBAS escala derecha) como
// TRES barras por etapa, simultáneas, dentro del drilldown de Oportunidades
// Activas. C5a retiró el switch Horas|Ponderadas (C3/C3.1): ya no hay
// toggle, las tres series se muestran siempre juntas sobre 2 ejes, nunca 3.
//
// Carga commercial.js en un sandbox mínimo y ejecuta el MOTOR REAL
// (commercialDistribucionHtml / commercialNiceMax / commercialStageTick /
// commercialChartTooltip / commercialActivasAnalysisHtml) contra fixtures
// sintéticas — MISMA técnica que reporting_rentabilidad_chart_tooltip.node.js.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

// El bootstrap real cuelga de DOMContentLoaded; con `addEventListener` no-op
// el módulo se evalúa sin arrancar fetch alguno.
const sandbox = {
  document: { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; } },
  window: {},
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialDistribucionHtml,
  commercialNiceMax,
  commercialStageTick,
  commercialChartTooltip,
  commercialStageTooltipLines,
  commercialActivasAnalysisHtml,
  commercialTooltipNeedsFlip,
  commercialPositionChartTooltip,
} = sandbox;

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

// Etapas deliberadamente NO consecutivas y NO 8: el service agrupa sólo las
// etapas CON oportunidades, así que el motor nunca debe asumir 8 columnas.
const PIPELINE = {
  opportunities: 40,
  stages: [
    { stage: "1 Contacto Inicial", count: 7, hours: 2760, weighted_hours: 276, revenue: 120000 },
    { stage: "2 Relevamiento", count: 8, hours: 2670, weighted_hours: 534, revenue: 98000 },
    { stage: "4 Propuesta", count: 20, hours: 900, weighted_hours: 450, revenue: 250000 },
    { stage: "8 Negociación", count: 5, hours: 300, weighted_hours: 270, revenue: 75000 },
  ],
};
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
const barHeights = (html, cls) => (html.match(new RegExp(`category-plot__bar ${cls}" style="height:([\\d.]+)%`, "g")) || [])
  .map(m => m.replace(/.*height:/, "").replace("%", ""));
const axisTicks = (html, side) => {
  const block = html.split(`category-plot__axis--${side}">`, 2)[1] || "";
  return (block.split("</div>")[0].match(/>([^<]+)</g) || []).map(t => t.replace(/[><]/g, ""));
};

// --- Tres barras por etapa presentes, mismo orden, simultáneas -------------
{
  const html = commercialDistribucionHtml(PIPELINE);
  assertTrue(count(html, "category-plot__col") === 4, "una columna por etapa presente (nunca 8 hardcodeadas)");
  assertTrue(barHeights(html, "category-plot__bar--count").length === 4, "una barra de Oportunidades por etapa presente");
  assertTrue(barHeights(html, "category-plot__bar--hours").length === 4, "una barra de Horas por etapa presente");
  assertTrue(barHeights(html, "category-plot__bar--weighted").length === 4, "una barra de Hs Ponderadas por etapa presente — SIEMPRE, sin toggle");
  const ticks = (html.match(/category-plot__xlab">([\s\S]*?)<\/div>/)[1].match(/>([^<]+)</g) || [])
    .map(t => t.replace(/[><]/g, ""));
  assertTrue(ticks.join("") === "1248", `eje X usa el número de etapa, en orden (got ${ticks})`);
  // Las tres series comparten el MISMO orden de columnas: la barra i-ésima de
  // cada serie corresponde a la etapa i-ésima (verificado abajo por altura).
}

// --- 2 ejes, NUNCA 3: Horas y Ponderadas comparten la escala derecha -------
{
  const html = commercialDistribucionHtml(PIPELINE);
  const left = axisTicks(html, "left");
  const right = axisTicks(html, "right");
  assertTrue(count(html, "category-plot__axis--") === 2, "exactamente DOS ejes en el markup — nunca un tercero");
  assertTrue(left[0] === "0" && right[0] === "0", `ambos ejes arrancan en 0 (got ${left[0]}/${right[0]})`);
  assertTrue(left[left.length - 1] === "20", `eje izquierdo = max(count) redondeado (got ${left})`);
  // max(hours, weighted_hours) por etapa, luego max entre etapas: 2760 gana
  // (ninguna weighted_hours supera a su propia hours en este fixture) — el
  // eje derecho re-escala al máximo de LAS DOS series juntas, no sólo Horas.
  assertTrue(right[right.length - 1] === "3,000", `eje derecho = max(hours, weighted_hours) redondeado (got ${right})`);
  // El tope redondeado NUNCA puede quedar por debajo del máximo real.
  assertTrue(commercialNiceMax(2760) >= 2760 && commercialNiceMax(534) >= 534 && commercialNiceMax(20) >= 20,
    "el tope redondeado siempre es >= el máximo real");
  assertTrue(commercialNiceMax(2760) === 3000 && commercialNiceMax(534) === 550, "redondeo legible (2760→3000, 534→550)");
  // Cada eje mide LO QUE LE CORRESPONDE — Oportunidades nunca comparable
  // numéricamente contra Horas/Ponderadas por su altura.
  const countBars = barHeights(html, "category-plot__bar--count");
  const hoursBars = barHeights(html, "category-plot__bar--hours");
  const weightedBars = barHeights(html, "category-plot__bar--weighted");
  assertTrue(countBars[2] === "100.0", "la etapa con más oportunidades llega al tope del eje izquierdo (20/20)");
  assertTrue(countBars[0] === "35.0", "Oportunidades se escala contra el eje izquierdo redondeado (7/20)");
  assertTrue(hoursBars[0] === "92.0", "Horas se escala contra el eje derecho redondeado (2760/3000)");
  assertTrue(weightedBars[1] === "17.8", "Ponderadas se escala contra el MISMO eje derecho (534/3000)");
  assertTrue(countBars[0] !== hoursBars[0], "Oportunidades NO comparte escala con Horas — misma etapa, alturas distintas");
  assertTrue(hoursBars[0] !== weightedBars[0], "Horas y Ponderadas, aunque comparten EJE, tienen valores propios distintos");
}

// --- Sin switch: las tres series se muestran SIEMPRE, sin toggle -----------
{
  const html = commercialDistribucionHtml(PIPELINE);
  assertTrue(!html.includes("dashboard-view-switch"), "el switch Horas|Ponderadas (C3/C3.1) fue retirado — C5a muestra las tres series juntas");
  assertTrue(!/data-commercial-dim/.test(html), "sin atributo de dimensión — ya no hay estado de toggle que leer");
  assertTrue(html.includes(">Horas <em>") && html.includes(">Hs Ponderadas <em>") && html.includes(">Oportunidades <em>"),
    "la leyenda muestra las TRES series a la vez, siempre");
  // Llamar dos veces con el MISMO pipeline es determinístico — no hay
  // estado local (ex `horasDim`) que pueda hacer variar el resultado.
  assertTrue(commercialDistribucionHtml(PIPELINE) === html, "el render es puro respecto del pipeline — sin estado oculto");
}

// --- Cierre explícito del análisis (C5d, ver HANDOFF — segunda vuelta:
//     owner corrective "la X debe permanecer aunque colapses el grupo" +
//     "la X debe estar alineada al texto"): el botón se movió DE
//     `.dashboard-section__body` (desaparecía al colapsar el `<details>`
//     nativamente) A DENTRO de `<summary>`, como flex-item más junto a
//     `.dashboard-section__count` — mismo row, mismo `align-items:center`
//     del primitivo compartido, alineación gratis sin un `top` en px
//     calibrado a mano. Se verifica sobre el TEXTO FUENTE del template
//     (`commercialDashboardHtml`, igual que el resto de este bloque ya
//     testea el delegado de click con `src.split`) en vez de un render
//     completo — ese exigiría fixture de summary+pipeline+analytics sólo
//     para llegar a una fila de `<summary>`. -------------------------------
{
  const summaryBlock = src.split('data-section-id="activas-analisis"', 2)[1].split("</summary>", 2)[0];
  assertTrue(/<button type="button" class="panel-close" data-commercial-close-analysis aria-label="Cerrar análisis">/.test(summaryBlock),
    "botón real (no span/div), foco-able por default, con aria-label semántico, DENTRO de <summary>");
  assertTrue(summaryBlock.includes(">&times;<") || summaryBlock.includes(">×<"), "el glifo visible es sólo la X — el texto accesible vive en aria-label");
  assertTrue(summaryBlock.indexOf("dashboard-section__count") < summaryBlock.indexOf("panel-close"),
    "el botón va DESPUÉS del conteo, en el MISMO flex row — comparte gap/align-items del primitivo, cero posicionamiento a mano");
  assertTrue(!commercialActivasAnalysisHtml(PIPELINE).includes("panel-close"),
    "el body YA NO devuelve el botón — vivir ahí es lo que lo hacía desaparecer al colapsar");
  // El delegado de click (ver DOMContentLoaded) NO debe crear un flag propio
  // — sólo disparar un `.click()` real sobre el MISMO tile que abre el
  // panel, reusando el camino que ya maneja `initEventBreakdown`. Ahora
  // también debe frenar el toggle NATIVO de `<summary>` (preventDefault +
  // stopPropagation) — el botón vive adentro, así que cualquier click sin
  // esto también abriría/cerraría el `<details>`.
  const clickDelegate = src.split('document.addEventListener("click", event => {', 2)[1]
    .split('document.addEventListener("change", event => {', 2)[0];
  const closeBranch = clickDelegate.split('"[data-commercial-close-analysis]");', 2)[1].split("return;", 2)[0];
  assertTrue(closeBranch.includes('querySelector(\'[data-event="activas"]\')?.click()'),
    "cerrar = simular un click real sobre el MISMO trigger que abre — nunca un segundo lifecycle");
  assertTrue(closeBranch.includes("event.preventDefault()") && closeBranch.includes("event.stopPropagation()"),
    "frena el toggle nativo de <summary> — si no, el click también abre/cierra el <details>");
  assertTrue(!closeBranch.includes("commercialState."), "el cierre no lee/escribe ningún campo de estado propio — cero flag nuevo");
}

// --- Sin normalización porcentual: los ejes llevan magnitudes reales --------
{
  const html = commercialDistribucionHtml(PIPELINE);
  assertTrue(!/%\s*del máximo/i.test(html), "el gráfico NO normaliza a porcentaje (eso fue descartado por producto)");
  assertTrue(axisTicks(html, "right").includes("1,500"), "el eje derecho muestra horas reales, no %");
}

// --- max = 0: sin NaN / Infinity (las tres series) --------------------------
{
  const zeroed = { stages: [
    { stage: "1 Contacto Inicial", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
    { stage: "2 Relevamiento", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
  ] };
  const html = commercialDistribucionHtml(zeroed);
  assertTrue(!/NaN|Infinity/.test(html), "serie entera en 0 no produce NaN/Infinity");
  assertTrue(barHeights(html, "category-plot__bar--count").every(h => h === "0.0"), "Oportunidades queda en 0% cuando su máximo es 0");
  assertTrue(barHeights(html, "category-plot__bar--hours").every(h => h === "0.0"), "Horas queda en 0% cuando su máximo es 0");
  assertTrue(barHeights(html, "category-plot__bar--weighted").every(h => h === "0.0"), "Ponderadas queda en 0% cuando su máximo es 0");
  assertTrue(commercialNiceMax(0) === 0 && commercialNiceMax(-5) === 0, "niceMax degrada a 0 sin romper");
}

// --- Etapas vacías: sin rótulos huérfanos ------------------------------------
{
  assertTrue(commercialDistribucionHtml({ stages: [] }) === "", "sin etapas no se dibuja gráfico");
  assertTrue(commercialDistribucionHtml(null) === "", "pipeline nulo no rompe");
  const analysis = commercialActivasAnalysisHtml({ stages: [] });
  assertTrue(!analysis.includes("Funnel por Etapa"),
    "sin datos no quedan rótulos sobre la nada — sólo el .dashboard-empty de la tabla");
  assertTrue(analysis.includes("dashboard-empty__label") && analysis.includes(">Sin resultados<"),
    "el vacío lo comunica el .dashboard-empty compartido (MISMO patrón que Sin selección/Copiloto, ver HANDOFF)");
}

// --- Accesibilidad: la columna es la superficie, las barras son mudas -------
{
  const html = commercialDistribucionHtml(PIPELINE);
  assertTrue(!/title="/.test(html), "NUNCA el atributo title nativo del navegador");
  assertTrue(count(html, "chart-tooltip-anchor") === 4, "cada columna es un anchor del tooltip compartido — uno por ETAPA, nunca uno por serie");
  assertTrue(count(html, 'class="chart-tooltip"') === 4, "cada columna incluye el tooltip visual compartido");
  assertTrue(count(html, 'tabindex="0"') === 4, "cada columna es foco-able por teclado");
  // El aria-label lleva magnitudes REALES y el nombre COMPLETO de la etapa
  // (el eje X sólo muestra el número — esa compacidad es deliberada). UN
  // solo punto de foco por etapa (la columna), no uno por serie/barra.
  assertTrue(html.includes("1 Contacto Inicial · Oportunidades: 7 · Horas: 2,760 · Ponderadas: 276 hs · Revenue: 120,000"),
    "aria-label con nombre completo y magnitudes reales — las TRES series juntas");
  const lines = commercialStageTooltipLines(PIPELINE.stages[0]);
  assertTrue(lines.length === 5, "tooltip: etapa + Oportunidades + Horas + Ponderadas + Revenue");
  assertTrue(html.includes("Revenue: 120,000"), "Revenue vive en el tooltip, nunca como serie gráfica");
}

// --- Sin línea/polyline/puntos de "A'", sin SVG, sin canvas, sin librería ---
{
  const html = commercialDistribucionHtml(PIPELINE);
  assertTrue(!html.includes("<svg"), "sin SVG — el chart es DOM puro (grouped bars)");
  assertTrue(!html.includes("<polyline"), "sin polyline — la representación 'A' con línea se retiró");
  assertTrue(!html.includes("<line "), "sin marcadores de punto de línea");
  assertTrue(!html.includes("swatch-line"), "leyenda sin el swatch de línea");
  assertTrue(!html.includes("<canvas"), "sin canvas");
  assertTrue(!/Chart|d3\./.test(html), "sin librería de charts");
}

// --- El escape del dominio se aplica a datos que vienen de Odoo --------------
{
  const nasty = { stages: [{ stage: '1 <script>alert("x")</script>', count: 1, hours: 1, weighted_hours: 1, revenue: 1 }] };
  const html = commercialDistribucionHtml(nasty);
  assertTrue(!html.includes("<script>"), "el nombre de etapa que llega de Odoo se escapa");
  assertTrue(html.includes("&lt;script&gt;"), "escapado, no removido");
}

// --- Tick del eje X: prefijo numérico, con fallback -------------------------
{
  assertTrue(commercialStageTick({ stage: "4 Propuesta" }, 0) === "4", "usa el prefijo numérico de la etapa");
  assertTrue(commercialStageTick({ stage: "Sin etapa" }, 2) === "3", "sin prefijo numérico cae al ordinal");
  assertTrue(commercialStageTick({}, 0) === "1", "etapa sin nombre no rompe el eje");
}

// --- chartTooltip: misma forma que el helper compartido de Reporting ---------
{
  assertTrue(commercialChartTooltip("una").includes('class="chart-tooltip"'), "usa la clase compartida");
  assertTrue((commercialChartTooltip(["a", "b", "c"]).match(/<p>/g) || []).length === 3, "N líneas → N <p>");
}

// --- Flip dinámico del tooltip (corrective, ver HANDOFF): decisión pura,
//     sin pinnear píxeles arbitrarios — casos límite exactos -------------
{
  assertTrue(commercialTooltipNeedsFlip(200, 60, 7) === false, "hay lugar de sobra arriba (200 >= 67) → no flippea");
  assertTrue(commercialTooltipNeedsFlip(50, 60, 7) === true, "no hay lugar arriba (50 < 67) → flippea");
  assertTrue(commercialTooltipNeedsFlip(67, 60, 7) === false, "empate exacto (67 >= 67) → todavía entra, no flippea");
  assertTrue(commercialTooltipNeedsFlip(66, 60, 7) === true, "un px menos que el empate → flippea");
  assertTrue(commercialTooltipNeedsFlip(0, 60, 7) === true, "sin espacio en absoluto → flippea");
}

// --- Flip dinámico: el wrapper mide contra el boundary real que recorta
//     (`.matrix-scroll` si el anchor vive en una tabla scrolleable, si no
//     `.dashboard-section` — owner corrective, ver HANDOFF: el MISMO
//     recorte apareció en el tooltip de Prior., dentro de la tabla) y sólo
//     toca la clase LOCAL del anchor. `closest` recibe el selector
//     COMBINADO real (`.matrix-scroll, .dashboard-section`), nunca los dos
//     por separado — el mock lo matchea literal para no dar falsos verdes
//     si el código volviera a llamar `closest` dos veces. -----------------
const COMMERCIAL_TOOLTIP_BOUNDARY_SELECTOR = ".matrix-scroll, .dashboard-section";
function fakeAnchor({ anchorTop, tooltipHeight, boundaryTop }) {
  const classes = new Set();
  const tooltip = { getBoundingClientRect: () => ({ height: tooltipHeight }) };
  const boundary = boundaryTop === null ? null : { getBoundingClientRect: () => ({ top: boundaryTop }) };
  return {
    classes,
    querySelector: sel => (sel === ".chart-tooltip" ? tooltip : null),
    closest: sel => (sel === COMMERCIAL_TOOLTIP_BOUNDARY_SELECTOR ? boundary : null),
    getBoundingClientRect: () => ({ top: anchorTop }),
    classList: { toggle: (cls, on) => (on ? classes.add(cls) : classes.delete(cls)) },
  };
}
{
  // Panel arrancando en top:0 (viewport = boundary): barra a 200px del
  // borde superior, tooltip de 60px de alto → 200 >= 67, entra arriba.
  const anchor = fakeAnchor({ anchorTop: 200, tooltipHeight: 60, boundaryTop: 0 });
  commercialPositionChartTooltip(anchor);
  assertTrue(!anchor.classes.has("is-tooltip-below"), "espacio de sobra dentro del boundary → arriba (default)");
}
{
  // MISMA barra, pero el boundary (`.dashboard-section` o `.matrix-scroll`,
  // según cuál sea el ancestro real) está scrolleado y ahora empieza en
  // top:180px — el espacio REAL arriba de la barra (200-180=20) ya no
  // alcanza para el tooltip (60+7=67). Un solo mock cubre AMBOS
  // consumidores: `closest` no distingue cuál de los dos matcheó, sólo que
  // el selector combinado lo hizo — la lógica de flip es idéntica para el
  // Funnel y para Prior., que es justo el punto de la generalización.
  const anchor = fakeAnchor({ anchorTop: 200, tooltipHeight: 60, boundaryTop: 180 });
  commercialPositionChartTooltip(anchor);
  assertTrue(anchor.classes.has("is-tooltip-below"), "el espacio se mide DENTRO del boundary que recorta, no contra el viewport entero");
}
{
  // Sin `.matrix-scroll` NI `.dashboard-section` como ancestro (defensivo):
  // boundaryTop cae a 0.
  const anchor = fakeAnchor({ anchorTop: 5, tooltipHeight: 60, boundaryTop: null });
  commercialPositionChartTooltip(anchor);
  assertTrue(anchor.classes.has("is-tooltip-below"), "sin boundary encontrado, cae a 0 — barra casi pegada arriba sigue flippeando");
}
{
  // Sin `.chart-tooltip` interno (defensivo): no debe romper ni tocar la clase.
  const anchor = { querySelector: () => null, classList: { toggle: () => { throw new Error("no debería tocar la clase"); } } };
  let threw = false;
  try { commercialPositionChartTooltip(anchor); } catch (e) { threw = true; }
  assertTrue(!threw, "sin tooltip interno, no rompe (return temprano)");
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL DISTRIBUCION-POR-ETAPA INVARIANTS PASSED");
