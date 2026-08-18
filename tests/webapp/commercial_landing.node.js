// commercial_landing.node.js — invocado por test_commercial_landing.py.
// Homologación del landing del CRM legacy (ver HANDOFF — Landing
// Homologation): las DOS familias de tiles (9 principales + 6 Analíticas del
// Pipeline), la composición por segmento honesta, y el análisis contextual
// que no ocupa espacio hasta que se lo acciona.
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures — MISMA técnica que
// commercial_capabilities.node.js.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

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

const { commercialDashboardHtml, commercialSplitHtml, commercialRichKpi } = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
const labelsOf = html => [...html.matchAll(/activity-kpi__label">([^<]+)</g)].map(m => m[1]);
const tilesOf = html => count(html, '<div class="activity-kpi[" ]');

function seg(nn, bi, both, neither) {
  return {
    nn: { count: nn, hours: nn * 10 }, bi: { count: bi, hours: bi * 10 },
    both: { count: both, hours: both * 10 }, neither: { count: neither, hours: neither * 10 },
  };
}
const SEGMENTS = {
  activas: seg(19, 20, 2, 3), prospectos: seg(1, 1, 0, 1), pipeline: seg(1, 1, 0, 0),
  ganadas: seg(5, 6, 0, 1), perdidas: seg(3, 4, 0, 1), suspendidas: seg(1, 1, 0, 0),
};
const SUMMARY = {
  labels: { nn: "New Name", bi: "Base Instalada" },
  prospectos: { count: 51, hours: 24544 },
  pipeline: { hours: 50963, weighted_hours: 13815 },
  activas: { count: 40, hours: 26419 },
  ganadas: { count: 30, hours: 11432 },
  perdidas: { count: 29, hours: 13513 },
  suspendidas: { count: 8, hours: 4260 },
  conversion: 50.8, conversion_with_suspended: 44.8,
  segments: SEGMENTS,
};
const ANALYTICS = {
  creadas: { this_week: 0, last_week: 1 },
  pipeline_age: { avg_days: 219.8, count: 40 },
  stage_time: { avg_days: 22.8, stages_with_data: 8 },
  stage_conversion: { min_rate: 33.3, steps: 7 },
  close_time: { avg_days: 112.5, count: 30 },
  stagnant: { count: 19, threshold_days: 30 },
  lead_conversion: { companies: 100, any_conv: 5, rate_any_conv: 5.3, adv_opp: 4, rate_adv_opp: 3.9 },
};
const PIPELINE = { opportunities: 40, stages: [
  { stage: "1 Contacto", count: 7, hours: 2760, weighted_hours: 276, revenue: 1 },
  { stage: "4 Propuesta", count: 20, hours: 900, weighted_hours: 450, revenue: 2 },
] };

function load({ summary = SUMMARY, analytics = ANALYTICS, pipeline = PIPELINE } = {}) {
  commercialState.status = "ready";
  commercialState.summary = JSON.parse(JSON.stringify(summary));
  commercialState.analytics = analytics ? JSON.parse(JSON.stringify(analytics)) : null;
  commercialState.pipeline = pipeline;
  return commercialDashboardHtml();
}

// --- Paridad de landing: 9 principales + 6 analíticas -----------------------
{
  const html = load();
  assertTrue(tilesOf(html) === 15, `15 tiles en total (9 + 6), got ${tilesOf(html)}`);
  const labels = labelsOf(html);
  const main = ["Oportunidades activas", "Prospectos", "Pipeline total", "Pipeline ponderado",
                "Ganadas", "Perdidas", "Suspendidas", "Creadas esta semana", "Conversión"];
  assertTrue(labels.slice(0, 9).join("|") === main.join("|"), `los 9 principales, en orden (got ${labels.slice(0, 9)})`);
  const analytics = ["Edad del pipeline", "Tiempo por etapa", "Conversión etapa a etapa",
                     "Tiempo de cierre", "Estancadas", "Conversión de leads"];
  assertTrue(labels.slice(9).join("|") === analytics.join("|"), `las 6 analíticas, en orden (got ${labels.slice(9)})`);
  assertTrue(html.includes("Analíticas del Pipeline"), "la agrupación conceptual sigue nombrada");
  assertTrue(count(html, "activity-kpi-grid") === 2, "dos familias, dos grillas");
}

// --- Métricas exactas de cada tile ------------------------------------------
{
  const html = load();
  const tile = label => {
    const i = html.indexOf(`>${label}<`);
    const start = html.lastIndexOf('<div class="activity-kpi', i);
    return html.slice(start, html.indexOf("</div>", i));
  };
  assertTrue(tile("Oportunidades activas").includes(">40<"), "Activas: count");
  assertTrue(tile("Oportunidades activas").includes("26,419 hs"), "Activas: horas como magnitud secundaria");
  assertTrue(tile("Prospectos").includes(">51<") && tile("Prospectos").includes("24,544 hs"), "Prospectos: count + horas");
  assertTrue(tile("Ganadas").includes(">30<") && tile("Ganadas").includes("11,432 hs"), "Ganadas: count + horas");
  assertTrue(tile("Perdidas").includes(">29<") && tile("Perdidas").includes("13,513 hs"), "Perdidas: count + horas");
  assertTrue(tile("Suspendidas").includes(">8<") && tile("Suspendidas").includes("4,260 hs"), "Suspendidas: count + horas");
  // Pipeline: la unidad va en la línea secundaria — pegada al valor
  // desbordaba el tile, que es `white-space:nowrap`.
  assertTrue(tile("Pipeline total").includes(">50,963<"), "Pipeline total: horas como valor");
  assertTrue(tile("Pipeline total").includes(">horas<"), "Pipeline total: unidad en la línea secundaria");
  assertTrue(!/>50,963 hs</.test(html), "la unidad NUNCA va pegada al valor principal");
  assertTrue(tile("Pipeline ponderado").includes(">13,815<"), "Pipeline ponderado: horas");
  // Conversión conserva SUS DOS lecturas, como el legacy.
  assertTrue(tile("Conversión").includes("50.8%"), "Conversión: sin suspendidas");
  assertTrue(tile("Conversión").includes("44.8% con suspendidas"), "Conversión: con suspendidas");
  // Creadas: semana actual + anterior.
  assertTrue(tile("Creadas esta semana").includes(">0<"), "Creadas: esta semana");
  assertTrue(tile("Creadas esta semana").includes("1 la semana pasada"), "Creadas: semana anterior");
}

// --- Analíticas: valor + subtítulo ------------------------------------------
{
  const html = load();
  assertTrue(html.includes("219.8d") && html.includes("40 oportunidad(es) activa(s)"), "Edad del pipeline");
  assertTrue(html.includes("22.8d") && html.includes("8 etapa(s) con datos"), "Tiempo por etapa");
  assertTrue(html.includes("33.3%") && html.includes("cuello de botella"), "Conversión etapa a etapa");
  assertTrue(html.includes("112.5d") && html.includes("30 oportunidad(es) ganada(s)"), "Tiempo de cierre");
  assertTrue(html.includes(">19<") && html.includes("+30 días sin movimiento"), "Estancadas");
  assertTrue(html.includes("5.3%") && html.includes("3.9% avanzó a oportunidad"), "Conversión de leads");
}

// --- Sin actionability falsa -------------------------------------------------
{
  const html = load();
  assertTrue(count(html, "activity-kpi--clickable") === 1, "un solo tile clickeable");
  assertTrue(count(html, 'data-event="activas"') === 1, "y es Oportunidades activas");
  assertTrue(count(html, 'role="button"') === 1, "ningún otro tile finge ser accionable");
}

// --- Composición NN/BI honesta ----------------------------------------------
{
  const html = load();
  assertTrue(count(html, 'class="kpi-split ') === 6, "seis tiles con composición por segmento");
  const split = commercialSplitHtml(SEGMENTS.activas, SUMMARY.labels);
  assertTrue(split.includes("New Name <b>19</b>"), "lectura visible: New Name");
  assertTrue(split.includes("Base Instalada <b>20</b>"), "lectura visible: Base Instalada");
  // La explicación honesta va en el tooltip, no como tercer número inventado.
  assertTrue(split.includes("Con ambos segmentos: 2"), "el solapamiento se declara");
  assertTrue(split.includes("Sin segmento asignado: 3"), "el hueco se declara");
  assertTrue(split.includes("no suman el total"), "se dice explícitamente que no es una partición");
  assertTrue(!/Otros/.test(split), "NUNCA un resto derivado total - nn - bi");
  assertTrue(split.includes('tabindex="0"'), "accesible por teclado, no sólo hover");
  assertTrue(split.includes("chart-tooltip"), "usa el tooltip compartido del Hub");
  // 19 + 20 no da 40: es justamente lo que el tooltip explica.
  assertTrue(SEGMENTS.activas.nn.count + SEGMENTS.activas.bi.count !== 40, "el fixture refleja la no reconciliación real");
}

// --- Filtrado por segmento: sin desglose redundante --------------------------
{
  const filtered = JSON.parse(JSON.stringify(SUMMARY));
  delete filtered.segments;  // el service no manda `segments` fuera de "todos"
  const html = load({ summary: filtered });
  assertTrue(count(html, 'class="kpi-split ') === 0, "en New Name / Base Instalada no se repite el desglose");
  assertTrue(tilesOf(html) === 15, "pero siguen estando los 15 tiles");
}

// --- El análisis contextual no ocupa espacio hasta accionarlo ---------------
{
  const html = load();
  assertTrue(html.includes('data-breakdown="activas" open hidden'), "la sección entera arranca oculta");
  assertTrue(count(html, "data-breakdown=") === 1, "un solo contrato de breakdown, sin panel placeholder");
  assertTrue(!/Hacé clic en/.test(html), "sin texto placeholder en el landing");
  // Y cuando se muestra, adentro está el Funnel (grouped bars, C3) intacto.
  assertTrue(html.includes("category-plot"), "el Funnel sigue siendo el gráfico");
  assertTrue(html.includes("Funnel por Etapa"), "con su rótulo");
  assertTrue(html.includes("Detalle por Etapa"), "y la tabla debajo");
}

// --- Analíticas degradan solas ----------------------------------------------
{
  const html = load({ analytics: null });
  assertTrue(tilesOf(html) === 9, "sin analíticas quedan los 9 principales");
  assertTrue(html.includes("No pudimos calcular las analíticas"), "el bloque avisa en vez de desaparecer");
  assertTrue(html.includes(">—<"), "Creadas degrada a guion, no rompe");
}

// --- Escaping de datos de origen Odoo ---------------------------------------
{
  const nasty = JSON.parse(JSON.stringify(SUMMARY));
  nasty.labels = { nn: '<img src=x onerror="alert(1)">', bi: "<b>BI</b>" };
  const html = load({ summary: nasty });
  assertTrue(!html.includes("<img") && !html.includes("<b>BI</b>"), "las etiquetas de tag se escapan");
  assertTrue(html.includes("&lt;img"), "escapado, no removido");
}

// --- El tile es el primitivo compartido, no uno propio ----------------------
{
  const plain = commercialRichKpi("X", "1");
  assertTrue(plain.startsWith('<div class="activity-kpi">'), "sin eventKey: tile display-only");
  assertTrue(!plain.includes("role=") && !plain.includes("tabindex"), "sin afordancia de click");
  const clickable = commercialRichKpi("X", "1", { eventKey: "k" });
  assertTrue(clickable.includes("activity-kpi--clickable"), "con eventKey: modificador COMPARTIDO");
  assertTrue(clickable.includes('data-event="k"'), "y el contrato del motor compartido");
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL LANDING INVARIANTS PASSED");
