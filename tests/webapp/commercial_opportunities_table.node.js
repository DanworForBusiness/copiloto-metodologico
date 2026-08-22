// commercial_opportunities_table.node.js — invocado por
// test_commercial_opportunities_table.py. Active Opportunities Phase 2 / C4
// (ver HANDOFF): reemplaza el placeholder "Detalle por Etapa" (agrupado,
// Batch 2A) por el detalle REAL por oportunidad — mismas columnas del
// legacy MENOS "Etiquetas" (contrato ya decidido en el backend), con orden
// por columna preservado (Lic./Días resp. intencionalmente NO ordenables,
// igual que el legacy).
//
// Carga commercial.js en un sandbox mínimo y ejecuta el MOTOR REAL
// (commercialOpportunitiesTableHtml / commercialOpportunitiesSorted /
// commercialToggleOppSort / commercialDaysUntil / commercialActivasAnalysisHtml)
// contra fixtures sintéticas — MISMA técnica que
// commercial_distribucion_por_etapa.node.js. Las fechas de la fixture se
// derivan de "hoy" en tiempo de ejecución (nunca un offset fijo tipo
// "2026-08-20"): el mismo tipo de acoplamiento fecha-fija que causó la
// falla de "creadas" en el backend esta sesión no debe reaparecer acá.
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
  commercialOpportunitiesTableHtml,
  commercialToggleOppSort,
  commercialDaysUntil,
  commercialActivasAnalysisHtml,
  commercialDistribucionHtml,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
function resetSort() {
  commercialState.oppSortKey = null;
  commercialState.oppSortDir = 1;
}

// Fechas SIEMPRE relativas al día real de ejecución — nunca un literal fijo.
function isoOffset(days) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Tres filas deliberadamente distintas: una "completa", una con actividad
// VENCIDA, y una con ausencias (hours=false, sin actividades, sin última
// respuesta) — para probar el fallback determinístico de cada sort key.
const OPPORTUNITIES = {
  opportunities: [
    {
      opportunity: "Zeta Corp", client: "Cliente Z", seller: "Vendedor Z",
      source: "Google Ads", licencias: "Si", hours: 50, weighted_hours: 20,
      probability: 40, priority: "2",
      activities: [{ type: "Llamada", summary: "Seguimiento", deadline: isoOffset(5) }],
      last_response: isoOffset(-3), days_elapsed: 3,
    },
    {
      opportunity: "Alpha SA", client: "Cliente A", seller: "Vendedor A",
      source: "Sin definir", licencias: "No", hours: 10, weighted_hours: 2,
      probability: 20, priority: "1",
      activities: [{ type: "Reunión", summary: "Cierre", deadline: isoOffset(-2) }],
      last_response: isoOffset(-20), days_elapsed: 20,
    },
    {
      opportunity: "Mid Co", client: "Cliente M", seller: "Vendedor M",
      source: "Sin definir", licencias: null, hours: false, weighted_hours: 0,
      probability: null, priority: "0",
      activities: [], last_response: null, days_elapsed: null,
    },
  ],
  total: 3,
};

const count = (html, needle) => (html.match(new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) || []).length;

// --- Columnas exactas presentes, ninguna Etiquetas --------------------------
{
  resetSort();
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  for (const label of ["Oportunidad", "Cliente", "Vendedor", "Lic.", "Hs", "Hs Pond.", "Prob.", "Prior.", "Próx. Acción", "Últ. Resp.", "Días resp."]) {
    assertTrue(html.includes(`>${label}<`) || html.includes(`>${label}`), `columna presente: ${label}`);
  }
  assertTrue(!html.includes(">Etiquetas<"), "NUNCA una columna de Etiquetas");
  // `<th` seguido de espacio o `>`, nunca a secas: ese substring también
  // matchea `<thead>`.
  const thCount = (html.match(/<th[ >]/g) || []).length;
  assertTrue(thCount === 11, `exactamente 11 columnas (got ${thCount})`);
}

// --- Lic. y Días resp. son intencionalmente NO ordenables -------------------
{
  resetSort();
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  assertTrue(!/data-commercial-sort="licencias"/.test(html), "Lic. nunca lleva data-commercial-sort");
  assertTrue(!/data-commercial-sort="days_elapsed"/.test(html), "Días resp. nunca lleva data-commercial-sort (mismo criterio que el legacy)");
  for (const key of ["opportunity", "client", "seller", "hours", "weighted_hours", "probability", "priority", "next_action", "last_response"]) {
    assertTrue(html.includes(`data-commercial-sort="${key}"`), `columna ordenable presente: ${key}`);
  }
}

// --- Totales: SOLO Hs/Hs Pond., el resto en blanco --------------------------
{
  resetSort();
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  assertTrue(html.includes('<tr class="is-total">'), "fila de totales presente");
  const totalRow = html.split('<tr class="is-total">')[1];
  assertTrue(totalRow.includes(">60<") || totalRow.includes(">60</td>"), `Hs total = 50+10+0 = 60 (got fragment: ${totalRow.slice(0, 200)})`);
  assertTrue(totalRow.includes(">22<") || totalRow.includes(">22</td>"), "Hs Pond. total = 20+2+0 = 22");
  assertTrue(totalRow.includes("Total (3)"), "el label de totales incluye la cantidad de filas");
}

// --- Orden por columna de TEXTO: ascendente/descendente ---------------------
{
  resetSort();
  commercialToggleOppSort("opportunity");
  assertTrue(commercialState.oppSortKey === "opportunity" && commercialState.oppSortDir === 1, "primer click: ascendente");
  let html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  let order = [...html.matchAll(/row-label">([^<]+)</g)].map(m => m[1]);
  assertTrue(order.join(",") === "Alpha SA,Mid Co,Zeta Corp", `orden alfabético ascendente (got ${order})`);
  commercialToggleOppSort("opportunity");
  assertTrue(commercialState.oppSortDir === -1, "segundo click en la MISMA columna invierte la dirección");
  html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  order = [...html.matchAll(/row-label">([^<]+)</g)].map(m => m[1]);
  assertTrue(order.join(",") === "Zeta Corp,Mid Co,Alpha SA", `orden alfabético descendente (got ${order})`);
}

// --- Orden por columna NUMÉRICA, con ausente (false) al fondo en ascendente -
{
  resetSort();
  commercialToggleOppSort("hours");
  let html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  let order = [...html.matchAll(/row-label">([^<]+)</g)].map(m => m[1]);
  assertTrue(order.join(",") === "Mid Co,Alpha SA,Zeta Corp", `hours=false ordena al fondo en ascendente (got ${order})`);
  commercialToggleOppSort("hours");
  html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  order = [...html.matchAll(/row-label">([^<]+)</g)].map(m => m[1]);
  assertTrue(order.join(",") === "Zeta Corp,Alpha SA,Mid Co", `descendente: el ausente queda primero al invertir (got ${order})`);
}

// --- Orden por "Próx. Acción": vencida < futura < sin actividades ----------
{
  resetSort();
  commercialToggleOppSort("next_action");
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  const order = [...html.matchAll(/row-label">([^<]+)</g)].map(m => m[1]);
  // Alpha SA: actividad vencida hace 2d (days=-2) < Zeta Corp: en 5d (days=5)
  // < Mid Co: sin actividades (Infinity).
  assertTrue(order.join(",") === "Alpha SA,Zeta Corp,Mid Co", `vencida primero, sin actividad al fondo (got ${order})`);
}

// --- Orden por "Últ. Resp.": por days_elapsed, null al fondo en ascendente --
{
  resetSort();
  commercialToggleOppSort("last_response");
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  const order = [...html.matchAll(/row-label">([^<]+)</g)].map(m => m[1]);
  // Zeta Corp (3d) < Alpha SA (20d) < Mid Co (sin respuesta, Infinity).
  assertTrue(order.join(",") === "Zeta Corp,Alpha SA,Mid Co", `respuesta más reciente primero, sin respuesta al fondo (got ${order})`);
}

// --- Cambiar de columna resetea a ascendente, nunca hereda la dirección ----
{
  resetSort();
  commercialToggleOppSort("hours");
  commercialToggleOppSort("hours"); // ahora descendente
  commercialToggleOppSort("probability"); // columna distinta
  assertTrue(commercialState.oppSortKey === "probability" && commercialState.oppSortDir === 1,
    "cambiar de columna vuelve a ascendente, no hereda la dirección de la anterior");
}

// --- Vacío: mensaje empty-panel, nunca una tabla rota -----------------------
{
  resetSort();
  assertTrue(commercialOpportunitiesTableHtml(null).includes("empty-panel"), "opportunities null no rompe, muestra empty-panel");
  assertTrue(commercialOpportunitiesTableHtml({ opportunities: [], total: 0 }).includes("empty-panel"), "lista vacía muestra empty-panel");
}

// --- commercialDaysUntil real: base de todos los sorts de fecha -------------
{
  assertTrue(commercialDaysUntil(isoOffset(5)) === 5, "5 días en el futuro");
  assertTrue(commercialDaysUntil(isoOffset(-2)) === -2, "2 días en el pasado (vencida)");
  assertTrue(commercialDaysUntil(null) === null, "sin fecha no rompe");
}

// --- El escape del dominio se aplica a datos que vienen de Odoo -------------
{
  resetSort();
  const nasty = { opportunities: [{
    opportunity: '<script>alert("x")</script>', client: "C", seller: "S",
    licencias: null, hours: 1, weighted_hours: 1, probability: 10, priority: "0",
    activities: [], last_response: null, days_elapsed: null,
  }], total: 1 };
  const html = commercialOpportunitiesTableHtml(nasty);
  assertTrue(!html.includes("<script>"), "el nombre de oportunidad que llega de Odoo se escapa");
  assertTrue(html.includes("&lt;script&gt;"), "escapado, no removido");
}

// --- Regresión: el Funnel (C3/C3.1) queda INTOCADO por C4 -------------------
{
  resetSort();
  commercialState.opportunities = OPPORTUNITIES;
  const PIPELINE = { opportunities: 3, stages: [
    { stage: "1 Contacto", count: 2, hours: 60, weighted_hours: 22, revenue: 1000 },
  ] };
  const analysis = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(analysis.includes("Funnel por Etapa"), "el Funnel sigue presente");
  assertTrue(analysis.includes("category-plot"), "el gráfico grouped-bars sigue presente");
  assertTrue(analysis.includes("dashboard-view-switch--compact"), "el switch compacto (C3.1) sigue presente");
  assertTrue(analysis.includes("Detalle de Oportunidades"), "debajo, el nuevo detalle por oportunidad (C4)");
  assertTrue(analysis.indexOf("Funnel por Etapa") < analysis.indexOf("Detalle de Oportunidades"),
    "el Funnel sigue ARRIBA, la tabla ABAJO — mismo orden que antes de C4");
  // Y el gráfico en sí es BIT A BIT el mismo que antes de este corrective.
  const distStandalone = commercialDistribucionHtml(PIPELINE, "hours");
  assertTrue(analysis.includes(distStandalone), "el HTML del Funnel dentro del panel es idéntico al que devuelve commercialDistribucionHtml sola");
  commercialState.opportunities = null;
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL OPPORTUNITIES TABLE INVARIANTS PASSED");
