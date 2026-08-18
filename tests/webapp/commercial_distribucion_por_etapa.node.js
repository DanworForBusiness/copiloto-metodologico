// commercial_distribucion_por_etapa.node.js — invocado por
// test_commercial_distribucion_por_etapa.py. "Funnel por Etapa" (Active
// Opportunities Phase 2 / C3, ver HANDOFF — restore legacy funnel
// representation): GROUPED BARS — Oportunidades (escala izquierda) y
// Horas ↔ Horas Ponderadas (escala derecha) como DOS barras por etapa,
// dentro del drilldown de Oportunidades Activas. Reemplaza la versión
// anterior (barra + línea con puntos, "A'") — el owner pidió el grouped bar
// real del legacy, no la variante Hub previa.
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
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

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

// --- Dos barras por etapa presente, mismo orden -----------------------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(count(html, "category-plot__col") === 4, "una columna por etapa presente (nunca 8 hardcodeadas)");
  assertTrue(barHeights(html, "category-plot__bar--count").length === 4, "una barra de Oportunidades por etapa presente");
  assertTrue(barHeights(html, "category-plot__bar--hours").length === 4, "una barra de Horas por etapa presente");
  const ticks = (html.match(/category-plot__xlab">([\s\S]*?)<\/div>/)[1].match(/>([^<]+)</g) || [])
    .map(t => t.replace(/[><]/g, ""));
  assertTrue(ticks.join("") === "1248", `eje X usa el número de etapa, en orden (got ${ticks})`);
  // Las dos series comparten el MISMO orden de columnas: la barra i-ésima de
  // cada serie corresponde a la etapa i-ésima (verificado abajo por altura).
}

// --- Escalas independientes, redondeadas y arrancando en 0 -----------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  const left = axisTicks(html, "left");
  const right = axisTicks(html, "right");
  assertTrue(left[0] === "0" && right[0] === "0", `ambos ejes arrancan en 0 (got ${left[0]}/${right[0]})`);
  assertTrue(left[left.length - 1] === "20", `eje izquierdo = max(count) redondeado (got ${left})`);
  assertTrue(right[right.length - 1] === "3,000", `eje derecho = max(hours) redondeado (got ${right})`);
  // El tope redondeado NUNCA puede quedar por debajo del máximo real.
  assertTrue(commercialNiceMax(2760) >= 2760 && commercialNiceMax(534) >= 534 && commercialNiceMax(20) >= 20,
    "el tope redondeado siempre es >= el máximo real");
  assertTrue(commercialNiceMax(2760) === 3000 && commercialNiceMax(534) === 550, "redondeo legible (2760→3000, 534→550)");
  // Cada serie se mide contra SU PROPIO eje — nunca una barra de
  // Oportunidades comparada numéricamente contra la de Horas.
  const countBars = barHeights(html, "category-plot__bar--count");
  const hoursBars = barHeights(html, "category-plot__bar--hours");
  assertTrue(countBars[2] === "100.0", "la etapa con más oportunidades llega al tope del eje izquierdo (20/20)");
  assertTrue(countBars[0] === "35.0", "Oportunidades se escala contra el eje izquierdo redondeado (7/20)");
  assertTrue(hoursBars[0] === "92.0", "Horas se escala contra el eje derecho redondeado (2760/3000)");
  assertTrue(countBars[0] !== hoursBars[0], "las dos series NO comparten escala — misma etapa, alturas distintas");
}

// --- El switch cambia SÓLO la segunda serie (Horas/Ponderadas) y su eje ----
{
  const normal = commercialDistribucionHtml(PIPELINE, "hours");
  const ponderado = commercialDistribucionHtml(PIPELINE, "weighted");
  assertTrue(barHeights(normal, "category-plot__bar--count").join() === barHeights(ponderado, "category-plot__bar--count").join(),
    "las barras de Oportunidades quedan INVARIANTES al conmutar la dimensión");
  assertTrue(axisTicks(normal, "left").join() === axisTicks(ponderado, "left").join(),
    "el eje izquierdo no se toca");
  assertTrue(barHeights(normal, "category-plot__bar--hours").join() !== barHeights(ponderado, "category-plot__bar--hours").join(),
    "las barras de Horas SÍ cambian al conmutar a Ponderadas");
  assertTrue(axisTicks(ponderado, "right").pop() === "550", "el eje derecho re-escala a max(weighted_hours)");
  assertTrue(normal.includes(">Horas <em>") && !normal.includes("Horas Ponderadas"), "leyenda 'Horas' en modo normal");
  assertTrue(ponderado.includes("Horas Ponderadas"), "leyenda pasa a 'Horas Ponderadas' en modo ponderado");
  assertTrue(/class="is-active" data-commercial-dim="weighted"/.test(ponderado), "el botón activo sigue la dimensión");
}

// --- Sin normalización porcentual: los ejes llevan magnitudes reales --------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(!/%\s*del máximo/i.test(html), "el gráfico NO normaliza a porcentaje (eso fue descartado por producto)");
  assertTrue(axisTicks(html, "right").includes("1,500"), "el eje derecho muestra horas reales, no %");
}

// --- max = 0: sin NaN / Infinity (ambas series) -----------------------------
{
  const zeroed = { stages: [
    { stage: "1 Contacto Inicial", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
    { stage: "2 Relevamiento", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
  ] };
  const html = commercialDistribucionHtml(zeroed, "hours");
  assertTrue(!/NaN|Infinity/.test(html), "serie entera en 0 no produce NaN/Infinity");
  assertTrue(barHeights(html, "category-plot__bar--count").every(h => h === "0.0"), "Oportunidades queda en 0% cuando su máximo es 0");
  assertTrue(barHeights(html, "category-plot__bar--hours").every(h => h === "0.0"), "Horas queda en 0% cuando su máximo es 0");
  assertTrue(commercialNiceMax(0) === 0 && commercialNiceMax(-5) === 0, "niceMax degrada a 0 sin romper");
}

// --- Etapas vacías: sin rótulos huérfanos ------------------------------------
{
  assertTrue(commercialDistribucionHtml({ stages: [] }, "hours") === "", "sin etapas no se dibuja gráfico");
  assertTrue(commercialDistribucionHtml(null, "hours") === "", "pipeline nulo no rompe");
  const analysis = commercialActivasAnalysisHtml({ stages: [] });
  assertTrue(!analysis.includes("Funnel por Etapa") && !analysis.includes("Detalle por Etapa"),
    "sin datos no quedan rótulos sobre la nada — sólo el .empty-panel de la tabla");
  assertTrue(analysis.includes("empty-panel"), "el vacío lo comunica el .empty-panel compartido");
}

// --- Accesibilidad: la columna es la superficie, las barras son mudas -------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(!/title="/.test(html), "NUNCA el atributo title nativo del navegador");
  assertTrue(count(html, "chart-tooltip-anchor") === 4, "cada columna es un anchor del tooltip compartido");
  assertTrue(count(html, 'class="chart-tooltip"') === 4, "cada columna incluye el tooltip visual compartido");
  assertTrue(count(html, 'tabindex="0"') === 4, "cada columna es foco-able por teclado");
  // El aria-label lleva magnitudes REALES y el nombre COMPLETO de la etapa
  // (el eje X sólo muestra el número — esa compacidad es deliberada). UN
  // solo punto de foco por etapa (la columna), no uno por barra.
  assertTrue(html.includes("1 Contacto Inicial · Oportunidades: 7 · Horas: 2,760 · Ponderadas: 276 hs · Revenue: 120,000"),
    "aria-label con nombre completo y magnitudes reales");
  const lines = commercialStageTooltipLines(PIPELINE.stages[0]);
  assertTrue(lines.length === 5, "tooltip: etapa + Oportunidades + Horas + Ponderadas + Revenue");
  assertTrue(html.includes("Revenue: 120,000"), "Revenue vive en el tooltip, nunca como serie gráfica");
}

// --- Sin línea/polyline/puntos de "A'", sin SVG, sin canvas, sin librería ---
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
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
  const html = commercialDistribucionHtml(nasty, "hours");
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

// --- El estado arranca en la dimensión cruda ---------------------------------
{
  assertTrue(commercialState.horasDim === "hours", "la dimensión por defecto es Horas (no ponderadas)");
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL DISTRIBUCION-POR-ETAPA INVARIANTS PASSED");
