// commercial_distribucion_por_etapa.node.js — invocado por
// test_commercial_distribucion_por_etapa.py. Batch 2B: "Distribución por
// Etapa", DOS small multiples coordinados (Oportunidades / Horas ↔ Horas
// Ponderadas) dentro del drilldown de Oportunidades Activas.
//
// Carga commercial.js en un sandbox mínimo y ejecuta el MOTOR REAL
// (commercialDistribucionHtml / commercialBarRow / commercialChartTooltip /
// commercialActivasAnalysisHtml) contra fixtures sintéticas — MISMA técnica
// que reporting_rentabilidad_chart_tooltip.node.js.
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
  commercialBarRow,
  commercialChartTooltip,
  commercialActivasAnalysisHtml,
  commercialStageTooltipLines,
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
// etapas CON oportunidades, así que el motor nunca debe asumir 8 filas.
const PIPELINE = {
  opportunities: 40,
  stages: [
    { stage: "1 Contacto Inicial", count: 7, hours: 2760, weighted_hours: 276, revenue: 120000 },
    { stage: "2 Relevamiento", count: 8, hours: 2670, weighted_hours: 534, revenue: 98000 },
    { stage: "4 Propuesta", count: 20, hours: 900, weighted_hours: 450, revenue: 250000 },
    { stage: "8 Negociación", count: 5, hours: 300, weighted_hours: 270, revenue: 75000 },
  ],
};
const widthsOf = html => (html.match(/width:([\d.]+)%/g) || []).map(w => w.replace("width:", "").replace("%", ""));
const countOccurrences = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
// El bloque de cada small multiple, aislado por su <section aria-label>.
const sectionsOf = html => html.split("<section aria-label=").slice(1);

// --- Una fila por etapa presente, en el orden recibido -----------------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(countOccurrences(html, 'class="bar-row') === 8, "4 etapas × 2 small multiples = 8 filas (nunca 8 etapas hardcodeadas)");
  const labels = (html.match(/class="bar-label">([^<]+)</g) || []).map(m => m.replace(/.*>/, "").replace("<", ""));
  assertTrue(
    labels.join("|") === "1 Contacto Inicial|2 Relevamiento|4 Propuesta|8 Negociación|1 Contacto Inicial|2 Relevamiento|4 Propuesta|8 Negociación",
    "orden de etapas preservado tal cual lo entrega el service, en ambos small multiples"
  );
  assertTrue(!html.includes("0 Prospecto"), "la etapa 0 nunca entra en este universo (Activas = 1–8)");
}

// --- Escalas INDEPENDIENTES: cada serie contra su propio máximo --------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  const [countSection, hoursSection] = sectionsOf(html);
  const countWidths = widthsOf(countSection);
  const hoursWidths = widthsOf(hoursSection);
  // Oportunidades: max(count) = 20 → 7/20=35%, 8/20=40%, 20/20=100%, 5/20=25%
  assertTrue(countWidths.join(",") === "35.0,40.0,100.0,25.0", `Oportunidades escala contra max(count)=20 (got ${countWidths})`);
  // Horas: max(hours) = 2760 → 100%, 96.7%, 32.6%, 10.9%
  assertTrue(hoursWidths[0] === "100.0", `Horas escala contra su PROPIO max(hours)=2760 (got ${hoursWidths[0]})`);
  // La prueba de que NO comparten unidad: la etapa con más oportunidades
  // (4 Propuesta, 100% en el primer chart) NO es la de más horas.
  assertTrue(countWidths[2] === "100.0" && hoursWidths[2] !== "100.0", "el máximo de cada serie es propio — no hay escala compartida entre unidades heterogéneas");
}

// --- Switch: cambia SÓLO el segundo small multiple ---------------------------
{
  const normal = commercialDistribucionHtml(PIPELINE, "hours");
  const ponderado = commercialDistribucionHtml(PIPELINE, "weighted");
  const [countNormal, hoursNormal] = sectionsOf(normal);
  const [countPonderado, hoursPonderado] = sectionsOf(ponderado);
  assertTrue(countNormal === countPonderado, "Oportunidades queda BYTE-IDÉNTICO al conmutar la dimensión");
  assertTrue(hoursNormal !== hoursPonderado, "el segundo small multiple sí cambia");
  // Ponderadas: max(weighted_hours) = 534 → 276/534=51.7%, 534/534=100%
  const pw = widthsOf(hoursPonderado);
  assertTrue(pw[0] === "51.7" && pw[1] === "100.0", `Ponderadas re-escala contra max(weighted_hours)=534 (got ${pw})`);
  assertTrue(normal.includes(">Horas</p>") && !normal.includes(">Horas Ponderadas</p>"), "subtítulo 'Horas' en modo normal");
  assertTrue(ponderado.includes(">Horas Ponderadas</p>"), "subtítulo pasa a 'Horas Ponderadas' en modo ponderado");
  assertTrue(ponderado.includes('data-commercial-dim="weighted"') && /class="is-active" data-commercial-dim="weighted"/.test(ponderado),
    "el botón activo del switch sigue la dimensión");
}

// --- Subtítulos sobre el primitivo compartido, no una clase propia -----------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(countOccurrences(html, 'class="perspective-label"') === 2, "un .perspective-label por small multiple (primitivo compartido promovido)");
  assertTrue(html.includes('class="dashboard-view-switch"'), "el switch usa el segmented control compartido");
  assertTrue(html.includes('class="staffing-timeline-toolbar"'), "el switch vive en la fila de controles compartida");
}

// --- Un solo .bar-fill por .bar-row: NO grouped bars -------------------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(countOccurrences(html, 'class="bar-row') === countOccurrences(html, 'class="bar-fill"'),
    "exactamente UNA .bar-fill por .bar-row — el primitivo es single-series, nunca grouped");
  assertTrue(!html.includes("<canvas") && !html.includes("<svg"), "sin canvas ni SVG");
}

// --- max = 0: sin NaN / Infinity ---------------------------------------------
{
  const zeroed = { stages: [
    { stage: "1 Contacto Inicial", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
    { stage: "2 Relevamiento", count: 0, hours: 0, weighted_hours: 0, revenue: 0 },
  ] };
  const html = commercialDistribucionHtml(zeroed, "hours");
  assertTrue(!/NaN|Infinity/.test(html), "serie entera en 0 no produce NaN/Infinity");
  assertTrue(widthsOf(html).every(w => w === "0.0"), "todas las barras quedan en 0% cuando el máximo es 0");
}

// --- Etapas vacías: sin rótulos huérfanos ------------------------------------
{
  assertTrue(commercialDistribucionHtml({ stages: [] }, "hours") === "", "sin etapas no se dibuja ningún small multiple");
  assertTrue(commercialDistribucionHtml(null, "hours") === "", "pipeline nulo no rompe");
  const analysis = commercialActivasAnalysisHtml({ stages: [] });
  assertTrue(!analysis.includes("Distribución por Etapa") && !analysis.includes("Detalle por Etapa"),
    "sin datos no quedan rótulos sobre la nada — sólo el .empty-panel de la tabla");
  assertTrue(analysis.includes("empty-panel"), "el vacío lo comunica el .empty-panel compartido");
}

// --- Tooltip compartido + a11y, nunca el title nativo ------------------------
{
  const html = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(!/title="/.test(html), "NUNCA el atributo title nativo del navegador");
  assertTrue(countOccurrences(html, "chart-tooltip-anchor") === 8, "cada fila es un anchor del tooltip compartido");
  assertTrue(countOccurrences(html, 'class="chart-tooltip"') === 8, "cada fila incluye el tooltip visual compartido");
  assertTrue(countOccurrences(html, 'aria-hidden="true"') === 8, "el tooltip visual queda aria-hidden (el aria-label ya lo anuncia)");
  assertTrue(countOccurrences(html, 'tabindex="0"') === 8, "cada fila es foco-able por teclado");
  // El tooltip lleva SIEMPRE el cuadro completo, en ambos small multiples.
  const lines = commercialStageTooltipLines(PIPELINE.stages[0]);
  assertTrue(lines.length === 5, "tooltip: etapa + Oportunidades + Horas + Ponderadas + Revenue");
  assertTrue(lines[4].startsWith("Revenue:"), "Revenue vive en el tooltip");
  assertTrue(html.includes("Revenue: 120,000"), "Revenue se muestra en tooltip/tabla, nunca como serie gráfica");
  assertTrue(countOccurrences(html, "aria-label=") >= 8, "aria-label plano por fila");
}

// --- El escape del dominio se aplica a datos que vienen de Odoo --------------
{
  const nasty = { stages: [{ stage: '1 <script>alert("x")</script>', count: 1, hours: 1, weighted_hours: 1, revenue: 1 }] };
  const html = commercialDistribucionHtml(nasty, "hours");
  assertTrue(!html.includes("<script>"), "el nombre de etapa que llega de Odoo se escapa");
  assertTrue(html.includes("&lt;script&gt;"), "escapado, no removido");
}

// --- commercialBarRow: guard de división por cero -----------------------------
{
  assertTrue(commercialBarRow("X", 5, 0, "5", ["X"]).includes("width:0.0%"), "max=0 → ancho 0, nunca división por cero");
  assertTrue(commercialBarRow("X", 5, 10, "5", ["X"]).includes("width:50.0%"), "ancho proporcional al máximo de su serie");
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
