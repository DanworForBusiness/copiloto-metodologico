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
  commercialFmtDate,
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

// --- Lic.: tilde para "Si", celda en blanco para cualquier otro valor ------
// (visual polish C5b, ver HANDOFF — MISMO patrón que
// reporting.js::renderCalendarioCell: nunca "Sí"/"No" como texto).
function rowCells(html, opportunityName) {
  const rowHtml = html.split(`row-label">${opportunityName}<`, 2)[1].split("</tr>", 1)[0];
  return [...rowHtml.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(m => m[1]);
}
{
  resetSort();
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  const zeta = rowCells(html, "Zeta Corp"); // licencias: "Si"
  const alpha = rowCells(html, "Alpha SA"); // licencias: "No"
  const mid = rowCells(html, "Mid Co"); // licencias: null
  assertTrue(zeta[2] === "✓", `"Si" → tilde (got ${JSON.stringify(zeta[2])})`);
  assertTrue(alpha[2] === "", `"No" → celda en blanco, nunca el texto "No" (got ${JSON.stringify(alpha[2])})`);
  assertTrue(mid[2] === "", `null/ausente → celda en blanco (got ${JSON.stringify(mid[2])})`);
  assertTrue(!html.includes(">Sí<") && !html.includes(">No<"), "Lic. nunca renderiza \"Sí\"/\"No\" como texto");
}

// --- Prior.: SOLO estrellas visibles, el texto vive en tooltip accesible ---
// (visual polish C5b, ver HANDOFF): reusa `.chart-tooltip`/
// `chart-tooltip-anchor` (tools.css) — MISMO primitivo que ya usa el
// Funnel de este archivo — en vez de un `title=` nativo (ya prohibido para
// el Funnel, ver commercial_distribucion_por_etapa.node.js).
{
  resetSort();
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  const zeta = rowCells(html, "Zeta Corp"); // priority: "2" → Alta
  const alpha = rowCells(html, "Alpha SA"); // priority: "1" → Media
  const mid = rowCells(html, "Mid Co"); // priority: "0" → Sin prioridad
  assertTrue(zeta[6].startsWith("⭐⭐<div"), `sólo estrellas visibles antes del tooltip (got ${JSON.stringify(zeta[6])})`);
  const zetaVisible = zeta[6].split('<div class="chart-tooltip"')[0];
  assertTrue(zetaVisible === "⭐⭐", `la parte VISIBLE de la celda es sólo estrellas, nunca el texto (got ${JSON.stringify(zetaVisible)})`);
  assertTrue(/aria-label="Alta"/.test(html) && zeta[6].includes('<p>Alta</p>'), "el texto semántico vive en aria-label Y en el tooltip (mouse + teclado/AT)");
  assertTrue(alpha[6].startsWith("⭐<div") && alpha[6].includes("<p>Media</p>"), "Media: una estrella, tooltip con el texto");
  assertTrue(mid[6].startsWith("—<div") && mid[6].includes("<p>Sin prioridad</p>"), "sin prioridad: guion, tooltip explica el porqué");
  assertTrue(html.includes('class="chart-tooltip-anchor" tabindex="0"'), "foco-able por teclado — MISMO contrato que el resto del Hub");
  assertTrue(!/<td[^>]*title="/.test(html), "NUNCA el atributo title nativo del navegador (mismo criterio que el Funnel)");
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

// --- Vacío: estado Hub-consistente, nunca una tabla rota (visual polish,
//     ver HANDOFF — MISMO patrón que "Sin selección" en Copiloto) ----------
{
  resetSort();
  const nullHtml = commercialOpportunitiesTableHtml(null);
  const emptyHtml = commercialOpportunitiesTableHtml({ opportunities: [], total: 0 });
  assertTrue(nullHtml.includes("dashboard-empty__label") && nullHtml.includes(">Sin resultados<"), "opportunities null no rompe, muestra el estado Hub-consistente");
  assertTrue(emptyHtml.includes("dashboard-empty__label") && emptyHtml.includes(">Sin resultados<"), "lista vacía muestra el estado Hub-consistente");
  assertTrue(!nullHtml.includes("empty-panel") && !emptyHtml.includes("empty-panel"), "ya no usa el texto suelto .empty-panel");
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

// --- Badges de urgencia en "Próx. Acción" (owner corrective, ver HANDOFF:
//     leyenda de 4 colores "0-7/8-15/16-30/+30 días"). La MAGNITUD de
//     `days` decide el color, no el signo — una actividad vencida hace 10d
//     es tan urgente como una que vence en 10d. Reusa el primitivo
//     COMPARTIDO `.dashboard-status-badge` (tools.css, mismo que Salud de
//     Cliente) — el badge envuelve SOLO tipo+relativo, la fecha queda
//     como texto plano antes (mismo criterio visual que el legacy). -------
{
  resetSort();
  const URGENCY_OPPS = {
    opportunities: [
      { opportunity: "Verde SA", client: "C", seller: "S", licencias: null, hours: 1, weighted_hours: 1,
        probability: 10, priority: "0", activities: [{ type: "To-Do", summary: "", deadline: isoOffset(-3) }],
        last_response: null, days_elapsed: null },
      { opportunity: "Amber SA", client: "C", seller: "S", licencias: null, hours: 1, weighted_hours: 1,
        probability: 10, priority: "0", activities: [{ type: "Email", summary: "", deadline: isoOffset(10) }],
        last_response: null, days_elapsed: null },
      { opportunity: "Naranja SA", client: "C", seller: "S", licencias: null, hours: 1, weighted_hours: 1,
        probability: 10, priority: "0", activities: [{ type: "Meeting", summary: "", deadline: isoOffset(-22) }],
        last_response: null, days_elapsed: null },
      { opportunity: "Rojo SA", client: "C", seller: "S", licencias: null, hours: 1, weighted_hours: 1,
        probability: 10, priority: "0", activities: [{ type: "Revisión", summary: "", deadline: isoOffset(40) }],
        last_response: null, days_elapsed: null },
      { opportunity: "Sin Tipo SA", client: "C", seller: "S", licencias: null, hours: 1, weighted_hours: 1,
        probability: 10, priority: "0", activities: [{ type: "", summary: "", deadline: isoOffset(-3) }],
        last_response: null, days_elapsed: null },
      { opportunity: "Sin Actividad SA", client: "C", seller: "S", licencias: null, hours: 1, weighted_hours: 1,
        probability: 10, priority: "0", activities: [], last_response: null, days_elapsed: null },
    ],
    total: 6,
  };
  const html = commercialOpportunitiesTableHtml(URGENCY_OPPS);
  const nextActionCell = name => rowCells(html, name)[7];
  assertTrue(/dashboard-status-badge--green">To-Do \(venc\. 3d\)<\/span>/.test(nextActionCell("Verde SA")),
    `0-7 días (abs) → green (got ${JSON.stringify(nextActionCell("Verde SA"))})`);
  assertTrue(/dashboard-status-badge--amber">Email \(10d\)<\/span>/.test(nextActionCell("Amber SA")),
    `8-15 días → amber (got ${JSON.stringify(nextActionCell("Amber SA"))})`);
  assertTrue(/dashboard-status-badge--orange">Meeting \(venc\. 22d\)<\/span>/.test(nextActionCell("Naranja SA")),
    `16-30 días → orange (got ${JSON.stringify(nextActionCell("Naranja SA"))})`);
  assertTrue(/dashboard-status-badge--red">Revisión \(40d\)<\/span>/.test(nextActionCell("Rojo SA")),
    `+30 días → red (got ${JSON.stringify(nextActionCell("Rojo SA"))})`);
  const verdeCell = nextActionCell("Verde SA");
  assertTrue(verdeCell.startsWith(`${commercialFmtDate(isoOffset(-3))} · <span`),
    `la fecha queda como texto plano ANTES del badge, nunca envuelta (got ${JSON.stringify(verdeCell)})`);
  assertTrue(nextActionCell("Sin Tipo SA") === commercialFmtDate(isoOffset(-3)),
    `sin tipo de actividad → sólo la fecha, sin badge vacío (got ${JSON.stringify(nextActionCell("Sin Tipo SA"))})`);
  assertTrue(nextActionCell("Sin Actividad SA") === "—", "sin actividades → guion, sin badge");
}

// --- Leyenda de urgencia: debajo de "Detalle de Oportunidades", antes de la
//     TABLA (el `<table>` en sí, dentro de `[data-commercial-opportunities]`
//     — C5e, ver HANDOFF, movió la leyenda DENTRO de ese wrapper junto con
//     la tabla para que un re-render quirúrgico de sólo esa tabla, al
//     filtrar/buscar, mantenga la leyenda sincronizada con las filas
//     visibles en una sola asignación de innerHTML) — sólo si hay filas que
//     clasificar (nunca un rótulo huérfano sobre una tabla vacía). ---------
{
  resetSort();
  commercialState.opportunities = OPPORTUNITIES;
  const withRows = commercialActivasAnalysisHtml({ opportunities: 0, stages: [] });
  assertTrue(withRows.includes('<div class="urgency-legend">'), "leyenda presente cuando hay oportunidades");
  for (const [color, label] of [["green", "0-7 días"], ["amber", "8-15 días"], ["orange", "16-30 días"], ["red", "+30 días"]]) {
    assertTrue(withRows.includes(`urgency-legend__dot--${color}"></i>${label}`), `tier de leyenda: ${color} → ${label}`);
  }
  assertTrue(withRows.indexOf("Detalle de Oportunidades") < withRows.indexOf('class="urgency-legend"'),
    "la leyenda va DESPUÉS del rótulo \"Detalle de Oportunidades\"");
  assertTrue(withRows.indexOf("data-commercial-opportunities") < withRows.indexOf('class="urgency-legend"'),
    "la leyenda vive DENTRO de [data-commercial-opportunities] (C5e) — no ya como hermana suelta antes de él");
  assertTrue(withRows.indexOf('class="urgency-legend"') < withRows.indexOf('class="matrix-scroll"'),
    "la leyenda va ANTES del wrapper de la tabla en sí");
  commercialState.opportunities = { opportunities: [], total: 0 };
  const withoutRows = commercialActivasAnalysisHtml({ opportunities: 0, stages: [] });
  assertTrue(!withoutRows.includes("urgency-legend"), "sin oportunidades → sin leyenda huérfana");
  commercialState.opportunities = null;
}

// --- Colgroup rebalanceado (owner screenshot: "Días resp." partiendo su
//     título en dos líneas con espacio muerto en Próx. Acción) -------------
{
  resetSort();
  const html = commercialOpportunitiesTableHtml(OPPORTUNITIES);
  const cols = [...html.matchAll(/<col style="width:(\d+)%">/g)].map(m => Number(m[1]));
  assertTrue(cols.length === 11, `11 <col> en el colgroup (got ${cols.length})`);
  assertTrue(cols[8] === 18, `Próx. Acción baja a 18% (got ${cols[8]})`);
  assertTrue(cols[10] === 7, `Días resp. sube a 7% para no partir el título (got ${cols[10]})`);
  assertTrue(cols.reduce((a, b) => a + b, 0) === 100, `el colgroup sigue sumando 100% (got ${cols.reduce((a, b) => a + b, 0)})`);
}

// --- Regresión: el Funnel (C3/C3.1/C5a) queda INTOCADO por C4 ---------------
{
  resetSort();
  commercialState.opportunities = OPPORTUNITIES;
  const PIPELINE = { opportunities: 3, stages: [
    { stage: "1 Contacto", count: 2, hours: 60, weighted_hours: 22, revenue: 1000 },
  ] };
  const analysis = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(analysis.includes("Funnel por Etapa"), "el Funnel sigue presente");
  assertTrue(analysis.includes("category-plot"), "el gráfico grouped-bars sigue presente");
  assertTrue(analysis.includes("category-plot__bar--weighted"), "las tres series (C5a) siguen presentes, sin toggle");
  assertTrue(analysis.includes("Detalle de Oportunidades"), "debajo, el nuevo detalle por oportunidad (C4)");
  assertTrue(analysis.indexOf("Funnel por Etapa") < analysis.indexOf("Detalle de Oportunidades"),
    "el Funnel sigue ARRIBA, la tabla ABAJO — mismo orden que antes de C4");
  // Y el gráfico en sí es BIT A BIT el mismo que antes de este corrective.
  const distStandalone = commercialDistribucionHtml(PIPELINE);
  assertTrue(analysis.includes(distStandalone), "el HTML del Funnel dentro del panel es idéntico al que devuelve commercialDistribucionHtml sola");
  commercialState.opportunities = null;
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL OPPORTUNITIES TABLE INVARIANTS PASSED");
