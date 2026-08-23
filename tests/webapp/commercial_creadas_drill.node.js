// commercial_creadas_drill.node.js — invocado por
// test_commercial_creadas_drill.py. Creadas Legacy Parity (ver HANDOFF —
// paridad con el legacy `#created-drill-section`/`/crm/opp-by-month`):
// ranking por vendedor + tabla mensual con expansión a semana (día-del-mes,
// NUNCA el bucket Monday-anchored del KPI) + detalle por oportunidad.
//
// Carga commercial.js en un sandbox mínimo y ejecuta el MOTOR REAL
// (commercialCreadasWeekOfMonth/commercialCreadasDefaultYear/
// commercialCreadasSellerRankingHtml/commercialCreadasMonthTableHtml/
// commercialCreadasDetailHtml/commercialCreadasPanelBodyHtml) contra
// fixtures sintéticas — MISMA técnica que
// commercial_opportunities_table.node.js. Años SIEMPRE relativos al año
// real de ejecución (nunca un literal fijo) — mismo criterio que ese
// harness, por la MISMA razón (ver su comentario sobre el bug de fecha fija
// de "creadas" en el backend).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; } },
  window: {},
  dwGraphToken: async () => "",
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialCreadasWeekOfMonth,
  commercialCreadasYears,
  commercialCreadasDefaultYear,
  commercialCreadasEffectiveYear,
  commercialCreadasSellerRankingHtml,
  commercialCreadasMonthTableHtml,
  commercialCreadasDetailHtml,
  commercialCreadasCountLabel,
  commercialCreadasPanelBodyHtml,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { failures++; console.log(`FAIL: ${label} — expected ${e}, got ${a}`); }
}
function resetCreadasState() {
  commercialState.creadasStatus = "idle";
  commercialState.creadasErrorState = "";
  commercialState.creadas = null;
  commercialState.creadasYear = null;
  commercialState.creadasExpandedMonth = "";
  commercialState.creadasExpandedWeek = "";
}

const CUR_YEAR = new Date().getFullYear();
const PREV_YEAR = CUR_YEAR - 1;

// --- Semana dentro del mes: ceil(día/7), MISMA fórmula que el legacy ------
// --- NUNCA el bucket Monday-anchored del KPI semanal (owner directive:
// --- "do not correct or unify the two concepts") ---------------------------
{
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-03-01`), 1, "día 1 → semana 1");
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-03-07`), 1, "día 7 → semana 1 (límite)");
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-03-08`), 2, "día 8 → semana 2");
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-03-14`), 2, "día 14 → semana 2 (límite)");
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-03-28`), 4, "día 28 → semana 4");
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-03-31`), 5, "día 31 → semana 5 (mes de 31 días)");
}

// --- Año por default: el actual SI tiene datos, si no el más reciente ------
{
  const months = [
    { month: `${PREV_YEAR}-11`, count: 2, opportunities: [] },
    { month: `${CUR_YEAR}-01`, count: 1, opportunities: [] },
  ];
  assertEqual(commercialCreadasYears(months), [String(CUR_YEAR), String(PREV_YEAR)], "años derivados, descendente");
  assertEqual(commercialCreadasDefaultYear(months), String(CUR_YEAR), "año actual, presente entre los datos → default");

  const onlyOld = [{ month: `${PREV_YEAR}-06`, count: 1, opportunities: [] }];
  assertEqual(commercialCreadasDefaultYear(onlyOld), String(PREV_YEAR), "año actual SIN datos → cae al más reciente disponible, nunca al calendario a secas");

  resetCreadasState();
  assertEqual(commercialCreadasEffectiveYear(months), String(CUR_YEAR), "sin selección explícita, usa el default");
  commercialState.creadasYear = String(PREV_YEAR);
  assertEqual(commercialCreadasEffectiveYear(months), String(PREV_YEAR), "con selección explícita, la respeta");
  resetCreadasState();
}

// --- Ranking por vendedor: agregado descendente, primitivo .bar-row -------
{
  const months = [
    { month: `${CUR_YEAR}-02`, count: 3, opportunities: [
      { seller: "Ana Gómez" }, { seller: "Bruno Paz" }, { seller: "Ana Gómez" },
    ] },
    { month: `${CUR_YEAR}-03`, count: 1, opportunities: [{ seller: "Bruno Paz" }] },
    // Otro año — NO debe contarse para el ranking del año actual.
    { month: `${PREV_YEAR}-01`, count: 1, opportunities: [{ seller: "Carla Ruiz" }] },
  ];
  const html = commercialCreadasSellerRankingHtml(months, String(CUR_YEAR));
  assertTrue(html.includes('class="bar-row"'), "usa el primitivo .bar-row (Hub-wide, tools.css)");
  const anaIdx = html.indexOf("Ana G");
  const brunoIdx = html.indexOf("Bruno Paz");
  assertTrue(anaIdx !== -1 && brunoIdx !== -1 && anaIdx < brunoIdx, "Ana Gómez (2) rankea ANTES que Bruno Paz (1) — descendente");
  assertTrue(!html.includes("Carla Ruiz"), "vendedor de OTRO año no entra al ranking del año seleccionado");

  const empty = commercialCreadasSellerRankingHtml([], String(CUR_YEAR));
  assertTrue(empty.includes("Sin resultados"), "sin oportunidades ese año → estado vacío compartido, nunca un ranking en blanco");
}

// --- Tabla mensual: descendente, filtrada al año, caret + expansión -------
{
  resetCreadasState();
  // MISMO orden que ya entrega el backend (`_group_by_month`, descendente) —
  // `commercialCreadasMonthTableHtml` no re-ordena, confía en el payload.
  const months = [
    { month: `${CUR_YEAR}-03`, count: 1, opportunities: [{ entry_date: `${CUR_YEAR}-03-05`, seller: "Ana" }] },
    { month: `${CUR_YEAR}-01`, count: 2, opportunities: [
      { entry_date: `${CUR_YEAR}-01-03`, seller: "Ana" },
      { entry_date: `${CUR_YEAR}-01-20`, seller: "Bruno" },
    ] },
  ];
  const collapsed = commercialCreadasMonthTableHtml(months, String(CUR_YEAR));
  const marIdx = collapsed.indexOf(`data-commercial-creadas-month="${CUR_YEAR}-03"`);
  const eneIdx = collapsed.indexOf(`data-commercial-creadas-month="${CUR_YEAR}-01"`);
  assertTrue(marIdx !== -1 && eneIdx !== -1 && marIdx < eneIdx, "meses en orden DESCENDENTE (marzo antes que enero)");
  assertTrue(collapsed.includes("▸"), "mes colapsado muestra el caret ▸");
  assertTrue(!collapsed.includes(`data-commercial-creadas-week="${CUR_YEAR}-01:1"`), "sin expandir, NINGUNA fila de semana en el DOM");

  commercialState.creadasExpandedMonth = `${CUR_YEAR}-01`;
  const expanded = commercialCreadasMonthTableHtml(months, String(CUR_YEAR));
  assertTrue(expanded.includes("▾"), "mes expandido muestra el caret ▾");
  assertTrue(expanded.includes(`data-commercial-creadas-week="${CUR_YEAR}-01:1"`), "semana 1 (día 3) presente tras expandir enero");
  assertTrue(expanded.includes(`data-commercial-creadas-week="${CUR_YEAR}-01:3"`), "semana 3 (día 20) presente tras expandir enero");
  assertTrue(!expanded.includes(`data-commercial-creadas-week="${CUR_YEAR}-03:1"`), "marzo sigue colapsado — ningún mes distinto se expande a la vez");
  resetCreadasState();
}

// --- Detalle por oportunidad: orden ascendente, 6 columnas, badge de estado --
{
  resetCreadasState();
  const months = [{
    month: `${CUR_YEAR}-02`, count: 3, opportunities: [
      { entry_date: `${CUR_YEAR}-02-20`, entry_fmt: "20/02", name: "Tardía", client: "C3", seller: "V3", origen: "Web", status: "1 Contacto" },
      { entry_date: `${CUR_YEAR}-02-05`, entry_fmt: "05/02", name: "Ganada Co", client: "C1", seller: "V1", origen: "Referido", status: "Ganada" },
      { entry_date: `${CUR_YEAR}-02-10`, entry_fmt: "10/02", name: "Perdida SA", client: "C2", seller: "V2", origen: "Sin definir", status: "Perdida" },
    ],
  }];
  commercialState.creadasExpandedMonth = `${CUR_YEAR}-02`;
  const html = commercialCreadasDetailHtml(months);
  const gIdx = html.indexOf("Ganada Co");
  const pIdx = html.indexOf("Perdida SA");
  const tIdx = html.indexOf("Tardía");
  assertTrue(gIdx !== -1 && pIdx !== -1 && tIdx !== -1 && gIdx < pIdx && pIdx < tIdx, "orden ASCENDENTE por entry_date (05 → 10 → 20), MISMO criterio legacy");
  assertTrue(html.includes("dashboard-status-badge--green"), "estado Ganada usa el badge verde compartido");
  assertTrue(html.includes("dashboard-status-badge--red"), "estado Perdida usa el badge rojo compartido");
  assertTrue(!html.includes('dashboard-status-badge--green">1 Contacto'), "un estado que no es Ganada/Perdida queda como texto plano, sin badge de color");

  // Semana específica dentro del mes filtra el detalle.
  commercialState.creadasExpandedWeek = `${CUR_YEAR}-02:1`; // día 5 → semana 1
  const weekHtml = commercialCreadasDetailHtml(months);
  assertTrue(weekHtml.includes("Ganada Co") && !weekHtml.includes("Perdida SA") && !weekHtml.includes("Tardía"),
    "detalle de semana filtra a SOLO las oportunidades de esa semana (día 5 = semana 1)");
  resetCreadasState();
}

// --- Conteo del header + los 4 estados del lazy load -----------------------
{
  resetCreadasState();
  commercialState.creadasStatus = "loading";
  assertEqual(commercialCreadasCountLabel(), "", "conteo vacío mientras carga");
  assertTrue(commercialCreadasPanelBodyHtml().includes("Cargando"), "cuerpo muestra el loader canónico mientras carga");

  commercialState.creadasStatus = "error";
  commercialState.creadasErrorState = "source_error";
  assertTrue(commercialCreadasPanelBodyHtml().includes("data-commercial-retry=\"creadas\""), "error de transporte ofrece Reintentar con la key propia");
  commercialState.creadasErrorState = "config_incomplete";
  assertTrue(!commercialCreadasPanelBodyHtml().includes("data-commercial-retry"), "config_incomplete NUNCA ofrece Reintentar (no puede resolverse solo)");

  commercialState.creadasStatus = "ready";
  commercialState.creadas = { months: [] };
  assertTrue(commercialCreadasPanelBodyHtml().includes("Sin datos"), "payload vacío → estado vacío compartido, nunca un panel en blanco");

  commercialState.creadas = { months: [{ month: `${CUR_YEAR}-02`, count: 2, opportunities: [
    { entry_date: `${CUR_YEAR}-02-05`, seller: "Ana" }, { entry_date: `${CUR_YEAR}-02-10`, seller: "Ana" },
  ] }] };
  assertEqual(commercialCreadasCountLabel(), `2 oportunidad(es) · ${CUR_YEAR}`, "conteo del header refleja el año efectivo");
  const bodyHtml = commercialCreadasPanelBodyHtml();
  assertTrue(bodyHtml.includes("data-commercial-creadas-year"), "cuerpo listo incluye el selector de Año");
  assertTrue(bodyHtml.includes("Ranking por vendedor") && bodyHtml.includes("Por mes"), "ranking y tabla mensual, lado a lado (.analysis-split, MISMO primitivo que Forecast)");
  resetCreadasState();
}

if (failures > 0) {
  console.log(`${failures} creadas drill invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CREADAS DRILL INVARIANTS PASSED");
