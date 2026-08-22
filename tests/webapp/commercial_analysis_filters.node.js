// commercial_analysis_filters.node.js — invocado por
// test_commercial_analysis_filters.py. C5e (ver HANDOFF): filtro de análisis
// Etapa/Vendedor + búsqueda de texto libre sobre el detalle de Oportunidades
// Activas. Etapa/Vendedor son ANALÍTICOS (afectan Funnel + tabla); el texto
// libre es SÓLO de tabla (nunca toca el Funnel) — contrato explícito del
// owner. Revenue es un campo ADITIVO del contrato de oportunidades (backend,
// ver test_commercial_service.py) preservado en el Funnel derivado
// localmente cuando hay filtros activos.
//
// Carga commercial.js en un sandbox mínimo y ejecuta el MOTOR REAL — MISMA
// técnica que commercial_opportunities_table.node.js.
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

const {
  commercialOppStageOptions,
  commercialOppSellerOptions,
  commercialAnalysisFilteredOpportunities,
  commercialDeriveFunnelFromOpportunities,
  commercialSearchOpportunities,
  commercialOpportunitySearchableText,
  commercialResetInvalidAnalysisFilters,
  commercialActivasAnalysisHtml,
  commercialDistribucionHtml,
  commercialOpportunitiesTableHtml,
  commercialDaysUntil,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
function resetState() {
  commercialState.oppStageFilter = "";
  commercialState.oppSellerFilter = "";
  commercialState.oppSearchText = "";
  commercialState.opportunities = null;
  commercialState.pipeline = null;
}

function isoOffset(days) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Universo sintético: 2 etapas, 3 vendedores, revenue real por fila — cubre
// composición Etapa+Vendedor, agregación del Funnel y búsqueda de texto.
const PIPELINE = {
  opportunities: 5,
  stages: [
    { stage: "1 Contacto Inicial", count: 3, hours: 300, weighted_hours: 90, revenue: 30000 },
    { stage: "2 Relevamiento", count: 2, hours: 200, weighted_hours: 80, revenue: 20000 },
  ],
};
const OPPS = [
  { opportunity: "Loma Negra - Mantenimiento", client: "Loma Negra", seller: "Roberto Seminara",
    stage: "1 Contacto Inicial", hours: 100, weighted_hours: 30, revenue: 10000, probability: 30,
    priority: "2", licencias: "Si", activities: [{ type: "To-Do", summary: "", deadline: isoOffset(-3) }],
    last_response: null, days_elapsed: 5 },
  { opportunity: "Farma Plus - BDC", client: "FarmaPlus", seller: "Maria Villalobos",
    stage: "1 Contacto Inicial", hours: 100, weighted_hours: 30, revenue: 10000, probability: 30,
    priority: "1", licencias: "No", activities: [], last_response: null, days_elapsed: null },
  { opportunity: "Cafes La Virginia", client: "Cafes La Virginia S.A.", seller: "Maria Villalobos",
    stage: "1 Contacto Inicial", hours: 100, weighted_hours: 30, revenue: 10000, probability: 30,
    priority: "0", licencias: null, activities: [], last_response: null, days_elapsed: null },
  { opportunity: "Southbay - Implementacion", client: "Southbay S.R.L.", seller: "Roberto Seminara",
    stage: "2 Relevamiento", hours: 100, weighted_hours: 40, revenue: 10000, probability: 40,
    priority: "0", licencias: null, activities: [], last_response: null, days_elapsed: null },
  { opportunity: "Murchison - Piloto", client: "Murchison S.A.", seller: "Yanina Catalfamo",
    stage: "2 Relevamiento", hours: 100, weighted_hours: 40, revenue: 10000, probability: 40,
    priority: "0", licencias: null, activities: [], last_response: null, days_elapsed: null },
];

// --- 1: Opciones de Etapa — orden CANÓNICO (el de pipeline.stages), sólo
//     etapas presentes en las oportunidades --------------------------------
{
  const opts = commercialOppStageOptions(PIPELINE, OPPS);
  assertTrue(JSON.stringify(opts) === JSON.stringify(["1 Contacto Inicial", "2 Relevamiento"]),
    `orden canónico, sólo presentes (got ${JSON.stringify(opts)})`);
  const subset = OPPS.filter(o => o.stage === "2 Relevamiento");
  assertTrue(JSON.stringify(commercialOppStageOptions(PIPELINE, subset)) === JSON.stringify(["2 Relevamiento"]),
    "si sólo hay filas de una etapa, sólo esa etapa es opción — nunca una fantasma");
}

// --- 2: Opciones de Vendedor — alfabético, únicos ---------------------------
{
  const opts = commercialOppSellerOptions(OPPS);
  assertTrue(JSON.stringify(opts) === JSON.stringify(["Maria Villalobos", "Roberto Seminara", "Yanina Catalfamo"]),
    `alfabético y sin duplicados (got ${JSON.stringify(opts)})`);
}

// --- 3/4/5: Filtrado por Etapa, Vendedor, y la combinación (AND) -----------
{
  const byStage = commercialAnalysisFilteredOpportunities(OPPS, "2 Relevamiento", "");
  assertTrue(byStage.length === 2 && byStage.every(o => o.stage === "2 Relevamiento"), "Etapa sola filtra correctamente");

  const bySeller = commercialAnalysisFilteredOpportunities(OPPS, "", "Roberto Seminara");
  assertTrue(bySeller.length === 2 && bySeller.every(o => o.seller === "Roberto Seminara"),
    "Vendedor sólo (Todas las etapas) trae TODAS sus oportunidades, cruzando etapas");

  const combined = commercialAnalysisFilteredOpportunities(OPPS, "1 Contacto Inicial", "Roberto Seminara");
  assertTrue(combined.length === 1 && combined[0].opportunity === "Loma Negra - Mantenimiento",
    "Etapa 6 + Roberto = SÓLO las de Roberto EN esa etapa, nunca una unión (AND, no OR)");

  const none = commercialAnalysisFilteredOpportunities(OPPS, "", "");
  assertTrue(none.length === OPPS.length, "Todas + Todos = el universo completo, sin filtrar");
}

// --- 6: Recomputación del Funnel — opportunities/horas/ponderadas/revenue,
//     Revenue SIGUE disponible para el tooltip (owner decisión C5e) --------
{
  const filtered = commercialAnalysisFilteredOpportunities(OPPS, "", "Roberto Seminara");
  const derived = commercialDeriveFunnelFromOpportunities(filtered, PIPELINE);
  assertTrue(derived.opportunities === 2, "conteo total = filas filtradas");
  assertTrue(derived.stages.length === 2, "una etapa por cada etapa PRESENTE en las filas filtradas — nunca renumerada");
  const s1 = derived.stages.find(s => s.stage === "1 Contacto Inicial");
  const s2 = derived.stages.find(s => s.stage === "2 Relevamiento");
  assertTrue(s1.count === 1 && s1.hours === 100 && s1.weighted_hours === 30 && s1.revenue === 10000,
    `etapa 1 agregada correctamente (got ${JSON.stringify(s1)})`);
  assertTrue(s2.count === 1 && s2.hours === 100 && s2.weighted_hours === 40 && s2.revenue === 10000,
    `etapa 2 agregada correctamente (got ${JSON.stringify(s2)})`);
  // El identificador de etapa NUNCA se renumera: filtrando a UNA sola etapa,
  // sigue siendo "2 Relevamiento", nunca pasa a ser la "primera" etapa.
  const oneStage = commercialDeriveFunnelFromOpportunities(
    commercialAnalysisFilteredOpportunities(OPPS, "2 Relevamiento", ""), PIPELINE);
  assertTrue(oneStage.stages.length === 1 && oneStage.stages[0].stage === "2 Relevamiento",
    "filtrar a una sola etapa preserva su identidad real, nunca la renumera a \"etapa 1\"");
  // Revenue sigue viajando al tooltip del Funnel derivado — MISMA función
  // `commercialDistribucionHtml`, cero fork.
  const html = commercialDistribucionHtml(derived);
  assertTrue(/Revenue: \$?10[.,]?000/.test(html) || html.includes("Revenue"),
    "el tooltip del Funnel derivado localmente sigue mostrando Revenue (owner decisión: preservarlo)");
}

// --- 7: Búsqueda de texto — case-insensitive, trim, RAW/DISPLAY nunca HTML -
{
  assertTrue(commercialSearchOpportunities(OPPS, "  loma negra  ").length === 1, "case-insensitive + trim");
  assertTrue(commercialSearchOpportunities(OPPS, "LOMA NEGRA").length === 1, "mayúsculas también matchean");
  assertTrue(commercialSearchOpportunities(OPPS, "").length === OPPS.length, "texto vacío = sin filtrar");
  assertTrue(commercialSearchOpportunities(OPPS, "   ").length === OPPS.length, "sólo espacios = sin filtrar (trim)");
  assertTrue(commercialSearchOpportunities(OPPS, "maria villalobos").length === 2, "busca por vendedor");
  assertTrue(commercialSearchOpportunities(OPPS, "to-do").length === 1, "busca por tipo de próxima acción");
  const text = commercialOpportunitySearchableText(OPPS[0]);
  assertTrue(!text.includes("<") && !text.includes(">"), "el texto buscable nunca incluye HTML generado (got: " + text + ")");
  assertTrue(!commercialSearchOpportunities(OPPS, "chart-tooltip-anchor").length,
    "clases/atributos internos del render NUNCA son buscables — sólo datos reales");
}

// --- 8: El texto libre NO afecta el Funnel — sólo la tabla ------------------
{
  resetState();
  commercialState.pipeline = PIPELINE;
  commercialState.opportunities = { opportunities: OPPS, total: OPPS.length };
  const distStandalone = commercialDistribucionHtml(PIPELINE);
  commercialState.oppSearchText = "Loma Negra";
  const searched = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(searched.includes(distStandalone),
    "Etapa=Todas, Vendedor=Todos, Buscar=\"Loma Negra\": el Funnel sigue siendo BIT A BIT el mismo que el universo sin filtrar");
  assertTrue((searched.match(/<tr>\s*<td/g) || []).length === 1, "la tabla, en cambio, sólo muestra la fila que matchea (excluye <thead><tr> y la fila de totales)");
  resetState();
}

// --- 9: Totales siguen las filas VISIBLES (Etapa/Vendedor + búsqueda) ------
{
  const filtered = commercialSearchOpportunities(
    commercialAnalysisFilteredOpportunities(OPPS, "", "Roberto Seminara"), "");
  const html = commercialOpportunitiesTableHtml({ opportunities: filtered });
  const totalRow = html.split('<tr class="is-total">')[1];
  assertTrue(totalRow.includes(">200<"), `Hs total de Roberto (100+100=200) (fragment: ${totalRow.slice(0, 150)})`);
  assertTrue(totalRow.includes("Total (2)"), "el label de totales refleja el conteo VISIBLE, no el universo completo");
}

// --- 10: Sorting sigue funcionando sobre el resultado YA filtrado/buscado —
//     auditado sobre el código fuente del delegado (mismo criterio que el
//     resto de este archivo para lógica de eventos DOM) --------------------
{
  const sortBranch = src.split('const sortTh = event.target.closest("[data-commercial-sort]");', 2)[1].split("return;", 2)[0];
  assertTrue(sortBranch.includes("commercialCurrentTableRows()"),
    "el delegado de sort re-renderiza sobre el resultado YA filtrado/buscado, nunca sobre commercialState.opportunities crudo");
}

// --- 11: Etapa/Vendedor inválidos se resetean tras cambio de tipo; texto
//     libre se resetea SIEMPRE (mismo criterio que actPeriod/actUser) ------
{
  resetState();
  commercialState.oppStageFilter = "9 Etapa Que Ya No Existe";
  commercialState.oppSellerFilter = "Roberto Seminara";
  commercialState.opportunities = { opportunities: OPPS, total: OPPS.length }; // universo NUEVO (post-tipo-change)
  commercialResetInvalidAnalysisFilters();
  assertTrue(commercialState.oppStageFilter === "", "Etapa inválida en el nuevo universo → Todas");
  assertTrue(commercialState.oppSellerFilter === "Roberto Seminara", "Vendedor que SIGUE siendo válido se preserva");

  const setTipoSrc = src.split("function commercialSetTipo(", 2)[1].split("\n}", 2)[0];
  assertTrue(setTipoSrc.includes('commercialState.oppSearchText = "";'),
    "el texto libre se resetea siempre en commercialSetTipo — mismo criterio que actPeriod/actUser");
  assertTrue(!setTipoSrc.includes("oppStageFilter") && !setTipoSrc.includes("oppSellerFilter"),
    "Etapa/Vendedor NO se tocan en commercialSetTipo — su reset es condicional y depende del universo nuevo, que todavía no llegó");
  resetState();
}

// --- 12: Resultado de análisis vacío (Etapa/Vendedor) → Funnel y tabla
//     coherentemente vacíos, sin datos stale de la selección anterior ------
{
  resetState();
  commercialState.pipeline = PIPELINE;
  commercialState.opportunities = { opportunities: OPPS, total: OPPS.length };
  commercialState.oppSellerFilter = "Vendedor Inexistente";
  const html = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(!html.includes("category-plot__col"), "sin filas, el Funnel no retiene barras de la selección anterior");
  assertTrue(html.includes("dashboard-empty__label") && html.includes(">Sin resultados<"),
    "la tabla reusa el estado vacío Hub-consistente, nunca una tabla stale");
  resetState();
}

// --- 13: Resultado de BÚSQUEDA vacío → el Funnel queda INTACTO (Etapa/
//     Vendedor siguen siendo la única fuente del Funnel) -------------------
{
  resetState();
  commercialState.pipeline = PIPELINE;
  commercialState.opportunities = { opportunities: OPPS, total: OPPS.length };
  commercialState.oppSearchText = "texto que no matchea absolutamente nada";
  const html = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(html.includes("category-plot__col"), "búsqueda sin resultados: el Funnel sigue representando Etapa/Vendedor, intacto");
  assertTrue(html.includes("dashboard-empty__label") && html.includes(">Sin resultados<"), "sólo la tabla queda vacía");
  resetState();
}

// --- 14: Sin mutación del payload fuente ------------------------------------
{
  const original = JSON.parse(JSON.stringify(OPPS));
  commercialAnalysisFilteredOpportunities(OPPS, "1 Contacto Inicial", "Roberto Seminara");
  commercialSearchOpportunities(OPPS, "loma");
  commercialDeriveFunnelFromOpportunities(OPPS, PIPELINE);
  assertTrue(JSON.stringify(OPPS) === JSON.stringify(original), "ningún helper de filtro muta el array fuente");
}

// --- 15: Cero requests de red para los filtros locales ----------------------
{
  const changeBranch = src.split('event.target.closest("[data-commercial-stage-filter]")', 2)[1].split("});", 2)[0];
  const inputBranch = src.split('event.target.closest("[data-commercial-search]")', 2)[1].split("});", 2)[0];
  assertTrue(!changeBranch.includes("fetch("), "Etapa/Vendedor: cero fetch — client-side puro");
  assertTrue(!inputBranch.includes("fetch("), "Buscar: cero fetch — client-side puro");
}

// --- 16: El resto del panel (C5b/C5c/C5d, 3-series) sigue intocado ----------
{
  resetState();
  commercialState.pipeline = PIPELINE;
  commercialState.opportunities = { opportunities: OPPS, total: OPPS.length };
  const html = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(html.includes("category-plot__bar--weighted"), "3 series del Funnel (C5a) siguen presentes");
  assertTrue(html.includes("panel-close") === false, "el botón de cierre NO vive más en el body (vive en <summary>, C5d — sin regresión)");
  assertTrue(html.includes('data-commercial-close-analysis') === false, "confirmación: el hook del botón de cierre no está en este body");
  resetState();
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL ANALYSIS FILTERS INVARIANTS PASSED");
