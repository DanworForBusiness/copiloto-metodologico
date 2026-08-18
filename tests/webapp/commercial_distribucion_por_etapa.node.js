// commercial_distribucion_por_etapa.node.js — invocado por
// test_commercial_distribucion_por_etapa.py. "Distribución por Etapa": UNA
// superficie compacta con Oportunidades como barras (escala izquierda) y
// Horas ↔ Horas Ponderadas como línea con puntos (escala derecha), dentro
// del drilldown de Oportunidades Activas.
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
const barHeights = html => (html.match(/category-plot__bar" style="height:([\d.]+)%/g) || [])
  .map(m => m.replace(/.*height:/, "").replace("%", ""));
const polyPoints = html => {
  const m = /<polyline points="([^"]*)"/.exec(html);
  return m ? m[1].split(" ").filter(Boolean).map(p => p.split(",").map(Number)) : [];
};
const axisTicks = (html, side) => {
  const block = html.split(`category-plot__axis--${side}">`, 2)[1] || "";
  return (block.split("</div>")[0].match(/>([^<]+)</g) || []).map(t => t.replace(/[><]/g, ""));
};

// --- Una barra y un punto de línea por etapa presente, mismo orden ----------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(count(html, "category-plot__col") === 4, "una columna por etapa presente (nunca 8 hardcodeadas)");
  assertTrue(barHeights(html).length === 4, "una barra por etapa presente");
  assertTrue(polyPoints(html).length === 4, "un punto de línea por etapa presente");
  assertTrue(count(html, "<line ") === 4, "un marcador de punto por etapa");
  const ticks = (html.match(/category-plot__xlab">([\s\S]*?)<\/div>/)[1].match(/>([^<]+)</g) || [])
    .map(t => t.replace(/[><]/g, ""));
  assertTrue(ticks.join("") === "1248", `eje X usa el número de etapa, en orden (got ${ticks})`);
  // barras y línea comparten el MISMO orden de categorías: la x del punto i
  // crece monótonamente con i.
  const xs = polyPoints(html).map(p => p[0]);
  assertTrue(xs.every((x, i) => i === 0 || x > xs[i - 1]), "las x de la línea siguen el orden de las columnas");
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
  // La barra más alta se mide contra SU máximo, no contra el de horas.
  assertTrue(barHeights(html)[2] === "100.0", "la etapa con más oportunidades llega al tope del eje izquierdo");
  assertTrue(barHeights(html)[0] === "35.0", "las barras se escalan contra el eje izquierdo redondeado (7/20)");
}

// --- El switch cambia SÓLO la línea y el eje derecho ------------------------
{
  const normal = commercialDistribucionHtml(PIPELINE, "hours");
  const ponderado = commercialDistribucionHtml(PIPELINE, "weighted");
  assertTrue(barHeights(normal).join() === barHeights(ponderado).join(),
    "las barras de Oportunidades quedan INVARIANTES al conmutar la dimensión");
  assertTrue(axisTicks(normal, "left").join() === axisTicks(ponderado, "left").join(),
    "el eje izquierdo no se toca");
  assertTrue(JSON.stringify(polyPoints(normal)) !== JSON.stringify(polyPoints(ponderado)),
    "la línea sí cambia");
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

// --- max = 0: sin NaN / Infinity -------------------------------------------
{
  const zeroed = { stages: [
    { stage: "1 Contacto Inicial", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
    { stage: "2 Relevamiento", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
  ] };
  const html = commercialDistribucionHtml(zeroed, "hours");
  assertTrue(!/NaN|Infinity/.test(html), "serie entera en 0 no produce NaN/Infinity");
  assertTrue(barHeights(html).every(h => h === "0.0"), "todas las barras quedan en 0% cuando el máximo es 0");
  assertTrue(polyPoints(html).every(p => p[1] === 100), "la línea se apoya en la base cuando el máximo es 0");
  assertTrue(commercialNiceMax(0) === 0 && commercialNiceMax(-5) === 0, "niceMax degrada a 0 sin romper");
}

// --- Etapas vacías: sin rótulos huérfanos ------------------------------------
{
  assertTrue(commercialDistribucionHtml({ stages: [] }, "hours") === "", "sin etapas no se dibuja gráfico");
  assertTrue(commercialDistribucionHtml(null, "hours") === "", "pipeline nulo no rompe");
  const analysis = commercialActivasAnalysisHtml({ stages: [] });
  assertTrue(!analysis.includes("Distribución por Etapa") && !analysis.includes("Detalle por Etapa"),
    "sin datos no quedan rótulos sobre la nada — sólo el .empty-panel de la tabla");
  assertTrue(analysis.includes("empty-panel"), "el vacío lo comunica el .empty-panel compartido");
}

// --- Accesibilidad: la columna es la superficie, el SVG es decorativo -------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(!/title="/.test(html), "NUNCA el atributo title nativo del navegador");
  assertTrue(count(html, "chart-tooltip-anchor") === 4, "cada columna es un anchor del tooltip compartido");
  assertTrue(count(html, 'class="chart-tooltip"') === 4, "cada columna incluye el tooltip visual compartido");
  assertTrue(count(html, 'tabindex="0"') === 4, "cada columna es foco-able por teclado");
  assertTrue(html.includes('aria-hidden="true" focusable="false"'), "el SVG de la línea es presentacional");
  assertTrue(count(html, "<svg") === 1, "un solo SVG, sólo para la línea");
  // El aria-label lleva magnitudes REALES y el nombre COMPLETO de la etapa
  // (el eje X sólo muestra el número — esa compacidad es deliberada).
  assertTrue(html.includes("1 Contacto Inicial · Oportunidades: 7 · Horas: 2,760 · Ponderadas: 276 hs · Revenue: 120,000"),
    "aria-label con nombre completo y magnitudes reales");
  const lines = commercialStageTooltipLines(PIPELINE.stages[0]);
  assertTrue(lines.length === 5, "tooltip: etapa + Oportunidades + Horas + Ponderadas + Revenue");
  assertTrue(html.includes("Revenue: 120,000"), "Revenue vive en el tooltip, nunca como serie gráfica");
}

// --- Sin librería / canvas ---------------------------------------------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
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
