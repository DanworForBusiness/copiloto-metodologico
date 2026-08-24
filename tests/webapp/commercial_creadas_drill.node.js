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
  commercialCreadasFiltered,
  commercialCreadasSellers,
  commercialCreadasWeekLabel,
  commercialCreadasWeekCount,
  commercialCreadasHoursCell,
  commercialCreadasSorted,
  commercialCreadasMatchesSearch,
  commercialCreadasStatusColor,
  commercialCreadasHours,
  commercialCreadasSetSeller,
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
  commercialState.creadasExpandedMonths = [];
  commercialState.creadasSelection = null;
  commercialState.creadasSeller = "";
  commercialState.creadasSearch = "";
  commercialState.creadasSortKey = null;
  commercialState.creadasSortDir = 1;
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
    { month: `${CUR_YEAR}-03`, count: 1, opportunities: [{ seller: "Ana Gómez" }] },
    // El fixture NO deja empate a propósito. Antes Ana y Bruno quedaban 2-2, así
    // que lo que el assert de abajo observaba era el ORDEN DE ITERACIÓN, no el
    // ranking — y ese orden cambió legítimamente: `commercialCreadasFiltered`
    // devuelve los meses DESCENDENTE (el más reciente primero, MISMO criterio
    // que el legacy), así que en un empate ahora aparece primero quien esté en
    // el mes más nuevo. Un empate no tiene orden definido y no debería tenerlo:
    // con Ana en 3 y Bruno en 1, el assert mide lo que dice medir.
    // Otro año — NO debe contarse para el ranking del año actual.
    { month: `${PREV_YEAR}-01`, count: 1, opportunities: [{ seller: "Carla Ruiz" }] },
  ];
  const html = commercialCreadasSellerRankingHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR)));
  // La fila lleva el primitivo de barra Y el modificador de interaccion, los
  // dos compartidos (tools.css). El literal exacto cambio al hacerla
  // clickeable; lo que se prueba sigue siendo que CONSUME el primitivo.
  assertTrue(html.includes("bar-row bar-row--clickable"),
    "usa el primitivo .bar-row + el modificador de click compartido");
  const anaIdx = html.indexOf("Ana G");
  const brunoIdx = html.indexOf("Bruno Paz");
  assertTrue(anaIdx !== -1 && brunoIdx !== -1 && anaIdx < brunoIdx, "Ana Gómez (3) rankea ANTES que Bruno Paz (1) — descendente");
  assertTrue(!html.includes("Carla Ruiz"), "vendedor de OTRO año no entra al ranking del año seleccionado");

  const empty = commercialCreadasSellerRankingHtml(commercialCreadasFiltered([], String(CUR_YEAR)));
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
  const collapsed = commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR)));
  const marIdx = collapsed.indexOf(`data-commercial-creadas-month="${CUR_YEAR}-03"`);
  const eneIdx = collapsed.indexOf(`data-commercial-creadas-month="${CUR_YEAR}-01"`);
  assertTrue(marIdx !== -1 && eneIdx !== -1 && marIdx < eneIdx, "meses en orden DESCENDENTE (marzo antes que enero)");
  assertTrue(collapsed.includes("▸"), "mes colapsado muestra el caret ▸");
  assertTrue(!collapsed.includes(`data-commercial-creadas-week="${CUR_YEAR}-01:1"`), "sin expandir, NINGUNA fila de semana en el DOM");

  commercialState.creadasExpandedMonths = [`${CUR_YEAR}-01`];
  const expanded = commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR)));
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
  // El detalle ya NO se deriva del mes expandido: hay una SELECCIÓN explícita
  // (owner UX corrective — expandir y seleccionar son dos acciones distintas).
  commercialState.creadasSelection = { month: `${CUR_YEAR}-02`, week: null };
  const html = commercialCreadasDetailHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR)));
  const gIdx = html.indexOf("Ganada Co");
  const pIdx = html.indexOf("Perdida SA");
  const tIdx = html.indexOf("Tardía");
  assertTrue(gIdx !== -1 && pIdx !== -1 && tIdx !== -1 && gIdx < pIdx && pIdx < tIdx, "orden ASCENDENTE por entry_date (05 → 10 → 20), MISMO criterio legacy");
  assertTrue(html.includes("dashboard-status-badge--green"), "estado Ganada usa el badge verde compartido");
  assertTrue(html.includes("dashboard-status-badge--red"), "estado Perdida usa el badge rojo compartido");
  assertTrue(!html.includes('dashboard-status-badge--green">1 Contacto'), "un estado que no es Ganada/Perdida queda como texto plano, sin badge de color");

  // Semana específica dentro del mes filtra el detalle.
  commercialState.creadasSelection = { month: `${CUR_YEAR}-02`, week: 1 }; // día 5 → semana 1
  const weekHtml = commercialCreadasDetailHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR)));
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
  assertTrue(bodyHtml.includes("Ranking por vendedor") && bodyHtml.includes("Por mes y semana"),
    "ranking y tabla APILADOS — lado a lado eran dos capas desconectadas (owner UX corrective)");
  resetCreadasState();
}

// ==========================================================================
// OWNER UX CORRECTIVE — jerarquia, filtros locales, detalle descartable, orden
// ==========================================================================

// Fixture compartido: dos meses del año actual, tres vendedores, y una opp SIN
// horas cargadas (`horas: null`, que el backend distingue de 0 a proposito).
function uxMonths() {
  return [
    { month: `${CUR_YEAR}-07`, count: 3, opportunities: [
      { id: 1, entry_date: `${CUR_YEAR}-07-03`, name: "Alfa BDC", client: "Loma Negra", seller: "Ana Gomez", origen: "Base Instalada", status: "1 - Inicial", horas: 100 },
      { id: 2, entry_date: `${CUR_YEAR}-07-10`, name: "Beta PaPM", client: "Danone", seller: "Bruno Paz", origen: "Integrador", status: "Ganada", horas: null },
      { id: 3, entry_date: `${CUR_YEAR}-07-25`, name: "Gamma Datasphere", client: "Colbun", seller: "Ana Gomez", origen: "SAP NOW", status: "Perdida", horas: 50 },
    ] },
    { month: `${CUR_YEAR}-06`, count: 1, opportunities: [
      { id: 4, entry_date: `${CUR_YEAR}-06-08`, name: "Delta Workzone", client: "Ternium", seller: "Carla Ruiz", origen: "Sin definir", status: "2 - Relevamiento", horas: 30 },
    ] },
  ];
}
function uxReady(extra) {
  resetCreadasState();
  commercialState.creadas = { months: uxMonths() };
  commercialState.creadasStatus = "ready";
  commercialState.creadasYear = String(CUR_YEAR);
  Object.assign(commercialState, extra || {});
}
const uxFiltered = (year) => commercialCreadasFiltered(uxMonths(), String(year || CUR_YEAR));
const countOf = (h, sub) => h.split(sub).length - 1;

// --- A: UNA sola tabla jerarquica Mes/Semana -------------------------------
{
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`] });
  const html = commercialCreadasPanelBodyHtml();
  assertEqual(countOf(html, "commercial-creadas-table"), 1,
    "A: existe EXACTAMENTE una tabla jerarquica Mes/Semana");
  assertEqual(countOf(html, "<table"), 1,
    "A: sin seleccion, la jerarquica es la UNICA tabla (no hay una de semanas aparte)");
  assertTrue(html.includes("Mes / Semana"),
    "A: UNA columna para las dos jerarquias, no dos tablas desconectadas");
}

// --- B: las semanas son filas HIJAS de su mes -----------------------------
{
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`] });
  const html = commercialCreadasMonthTableHtml(uxFiltered());
  const jul = html.indexOf(`data-commercial-creadas-month="${CUR_YEAR}-07"`);
  const jun = html.indexOf(`data-commercial-creadas-month="${CUR_YEAR}-06"`);
  const wk1 = html.indexOf(`data-commercial-creadas-week="${CUR_YEAR}-07:1"`);
  assertTrue(jul !== -1 && jun !== -1 && wk1 !== -1, "B: mes, mes y semana presentes");
  assertTrue(jul < wk1 && wk1 < jun,
    "B: las semanas de julio van DENTRO de julio — entre su mes y el mes siguiente");
  assertEqual(countOf(html, `data-commercial-creadas-week="${CUR_YEAR}-06:`), 0,
    "B: un mes colapsado no emite filas de semana");
  assertEqual(countOf(html, `data-commercial-creadas-week="${CUR_YEAR}-07:`), 3,
    "B: solo las semanas CON oportunidades son seleccionables (dias 3, 10 y 25)");
  assertEqual(countOf(html, 'class="commercial-creadas-week'),
    commercialCreadasWeekCount(`${CUR_YEAR}-07`),
    "B: se renderizan TODAS las semanas del mes, MISMO criterio legacy");
  assertTrue(html.includes("is-empty"), "B: las vacias van atenuadas, no ocultas");
}

// --- C: las celdas de texto NO estan centradas ----------------------------
{
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`] });
  const html = commercialCreadasMonthTableHtml(uxFiltered());
  // El primitivo de <td> a la izquierda es `.left`. NO `.row-label-col`, que
  // SOLO tiene regla para <th>: un `<td class="row-label-col">` cae en el
  // `text-align:right` generico y queda desalineado con su propio header. Ese
  // era el defecto de alineacion que el owner reporto.
  assertTrue(html.includes('<td class="left">'), "C: la celda de MES usa el primitivo .left");
  assertTrue(html.includes('class="left commercial-creadas-week-label"'),
    "C: la celda de SEMANA tambien");
  assertEqual(countOf(html, '<td class="row-label-col"'), 0,
    "C: ningun <td> usa .row-label-col — no alinea a la izquierda");
  assertEqual(countOf(html, "text-align:center"), 0, "C: nada centrado inline");
}

// --- D: la etiqueta de semana RECUPERA la referencia legacy ---------------
// `frontend/js/dashboard.js::renderCreatedDrillTable` (legacy):
//   `Sem. ${wk} (${padZ(dayStart)}/${padZ(m)}-${padZ(dayEnd)}/${padZ(m)})`
// con EN DASH (U+2013) y el ultimo bucket recortado a `daysInMonth`.
{
  assertEqual(commercialCreadasWeekLabel(`${CUR_YEAR}-07`, 1), "Sem. 1 (01/07–07/07)",
    "D: semana 1 con su rango de fechas, no solo 'Semana 1'");
  assertEqual(commercialCreadasWeekLabel(`${CUR_YEAR}-07`, 2), "Sem. 2 (08/07–14/07)",
    "D: semana 2");
  assertEqual(commercialCreadasWeekLabel(`${CUR_YEAR}-07`, 5), "Sem. 5 (29/07–31/07)",
    "D: la ultima semana se RECORTA al ultimo dia del mes (31, no 35)");
  assertEqual(commercialCreadasWeekLabel(`${CUR_YEAR}-06`, 5), "Sem. 5 (29/06–30/06)",
    "D: y en un mes de 30 dias recorta a 30");
  assertTrue(commercialCreadasWeekLabel(`${CUR_YEAR}-07`, 1).indexOf("–") !== -1,
    "D: el separador es EN DASH como el legacy, no un guion ASCII");
  // Las HORAS son su propia COLUMNA (el legacy las tenia asi), con su criterio
  // de "—" vs "0" preservado.
  assertEqual(commercialCreadasHoursCell([{ horas: 10 }, { horas: 5 }]), "15", "D: horas sumadas");
  assertEqual(commercialCreadasHoursCell([{ horas: null }]), "—",
    "D: con oportunidades pero sin horas cargadas muestra em dash, MISMO criterio legacy");
  assertEqual(commercialCreadasHoursCell([]), "0", "D: sin oportunidades, cero");
  // Y la columna existe en la tabla.
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`] });
  assertTrue(commercialCreadasMonthTableHtml(uxFiltered()).includes("<th>Horas</th>"),
    "D: Horas es una columna de la tabla");
}

// --- E: el chevron es un control SEPARADO de la fila ----------------------
{
  uxReady({ creadasExpandedMonths: [] });
  const collapsed = commercialCreadasMonthTableHtml(uxFiltered());
  assertTrue(collapsed.includes(`data-commercial-creadas-toggle="${CUR_YEAR}-07"`),
    "E: el chevron tiene su PROPIO hook, distinto del de la fila");
  assertTrue(collapsed.includes('aria-expanded="false"'), "E: colapsado lo anuncia");
  assertTrue(collapsed.indexOf("▸") !== -1, "E: y muestra el caret cerrado");
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`] });
  const expanded = commercialCreadasMonthTableHtml(uxFiltered());
  assertTrue(expanded.includes('aria-expanded="true"'), "E: expandido lo anuncia");
  assertTrue(expanded.indexOf("▾") !== -1, "E: y muestra el caret abierto");
  // VARIOS meses a la vez — mejora aprobada sobre el legacy, que abria uno solo.
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`, `${CUR_YEAR}-06`] });
  assertEqual(countOf(commercialCreadasMonthTableHtml(uxFiltered()), 'aria-expanded="true"'), 2,
    "E: DOS meses pueden estar abiertos a la vez");
}

// --- F y G: seleccion de mes y de semana ---------------------------------
{
  uxReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: null } });
  const mes = commercialCreadasDetailHtml(uxFiltered());
  assertTrue(mes.indexOf("Detalle · ") !== -1, "F: el header del detalle declara su alcance");
  assertTrue(mes.includes("3 oportunidad(es)"), "F: el detalle de MES trae las 3 del mes");
  assertTrue(mes.includes("Alfa BDC") && mes.includes("Beta PaPM") && mes.includes("Gamma Datasphere"),
    "F: las tres oportunidades del mes");

  uxReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: 2 } });
  const sem = commercialCreadasDetailHtml(uxFiltered());
  assertTrue(sem.includes("Sem. 2 (08/07–14/07)"),
    "G: el alcance del detalle de SEMANA usa la etiqueta legacy completa");
  assertTrue(sem.includes("1 oportunidad(es)"), "G: solo la opp del dia 10 (semana 2)");
  assertTrue(sem.includes("Beta PaPM") && !sem.includes("Alfa BDC"),
    "G: filtra a esa semana y excluye las otras");
}

// --- H: el detalle solo existe con seleccion, y se puede cerrar -----------
{
  uxReady({ creadasSelection: null });
  assertEqual(commercialCreadasDetailHtml(uxFiltered()), "",
    "H: sin seleccion NO hay detalle (antes se derivaba del mes expandido)");
  uxReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: null } });
  const html = commercialCreadasDetailHtml(uxFiltered());
  assertTrue(html.includes("data-commercial-creadas-detail-close"), "H: hay accion de cierre");
  assertTrue(html.includes('aria-label="Cerrar detalle"'), "H: accesible");
  assertTrue(html.includes('class="panel-close"'),
    "H: el x reusa el primitivo del Hub, cero clase nueva");
  // Expandir NO abre detalle: son dos acciones distintas.
  uxReady({ creadasExpandedMonths: [`${CUR_YEAR}-07`], creadasSelection: null });
  assertTrue(!commercialCreadasPanelBodyHtml().includes("data-commercial-creadas-detail"),
    "H: un mes EXPANDIDO sin seleccionar no abre el detalle");
}

// --- I: cerrar el detalle no toca nada mas -------------------------------
{
  uxReady({
    creadasExpandedMonths: [`${CUR_YEAR}-07`, `${CUR_YEAR}-06`],
    creadasSeller: "Ana Gomez", creadasSearch: "alfa",
    creadasSelection: { month: `${CUR_YEAR}-07`, week: null },
  });
  const antes = {
    expanded: [...commercialState.creadasExpandedMonths],
    seller: commercialState.creadasSeller,
    search: commercialState.creadasSearch,
    year: commercialState.creadasYear,
  };
  const datasetAntes = commercialState.creadas;
  commercialState.creadasSelection = null;          // exactamente lo que hace el x
  assertEqual(commercialState.creadasExpandedMonths, antes.expanded, "I: preserva los meses expandidos");
  assertEqual(commercialState.creadasSeller, antes.seller, "I: preserva el vendedor");
  assertEqual(commercialState.creadasSearch, antes.search, "I: preserva la busqueda");
  assertEqual(commercialState.creadasYear, antes.year, "I: preserva el año");
  assertTrue(commercialState.creadas === datasetAntes,
    "I: y el dataset sigue siendo el MISMO objeto (cero refetch)");
  assertEqual(commercialCreadasDetailHtml(uxFiltered()), "", "I: el detalle desaparece");
}

// --- J: los filtros son LOCALES sobre el payload ya cargado --------------
{
  uxReady({ creadasSeller: "Ana Gomez" });
  let f = uxFiltered();
  assertEqual(f.reduce((s, m) => s + m.count, 0), 2, "J: Vendedor filtra a las 2 de Ana");
  assertEqual(f.length, 1, "J: y el mes que se queda sin filas desaparece");
  assertTrue(!commercialCreadasSellerRankingHtml(f).includes("Bruno"),
    "J: el ranking responde al filtro — sin reglas especiales para el ranking");

  const opp = uxMonths()[0].opportunities[1];
  assertTrue(commercialCreadasMatchesSearch(opp, "beta"), "J: busca por oportunidad");
  assertTrue(commercialCreadasMatchesSearch(opp, "danone"), "J: por cliente");
  assertTrue(commercialCreadasMatchesSearch(opp, "bruno"), "J: por vendedor");
  assertTrue(commercialCreadasMatchesSearch(opp, "integrador"), "J: por origen");
  assertTrue(commercialCreadasMatchesSearch(opp, "ganada"), "J: por estado");
  assertTrue(commercialCreadasMatchesSearch(opp, "danone"), "J: case-insensitive");
  assertTrue(!commercialCreadasMatchesSearch(opp, "inexistente"), "J: descarta lo que no coincide");

  uxReady({ creadasSearch: "loma" });
  assertEqual(uxFiltered().reduce((s, m) => s + m.count, 0), 1,
    "J: Buscar filtra a la opp de Loma Negra");

  uxReady({});
  assertEqual(uxFiltered(PREV_YEAR).length, 0, "J: otro año no produce filas del dataset actual");
  assertEqual(commercialCreadasSellers(uxMonths(), String(CUR_YEAR)),
    ["Ana Gomez", "Bruno Paz", "Carla Ruiz"], "J: opciones de Vendedor derivadas y ordenadas");
  assertEqual(commercialCreadasSellers(uxMonths(), String(PREV_YEAR)), [],
    "J: acotadas al año activo — nunca ofrece un vendedor que no puede dar filas");
}

// --- K: Limpiar resetea SOLO los filtros locales ------------------------
{
  uxReady({
    creadasSeller: "Ana Gomez", creadasSearch: "alfa",
    creadasExpandedMonths: [`${CUR_YEAR}-07`],
  });
  const tipoAntes = commercialState.tipo;
  const datasetAntes = commercialState.creadas;
  commercialState.creadasSeller = "";              // lo que hace el boton
  commercialState.creadasSearch = "";
  assertEqual(commercialState.creadasYear, String(CUR_YEAR),
    "K: el AÑO se preserva — es la ventana que el usuario eligio, no un filtro");
  assertEqual(commercialState.tipo, tipoAntes,
    "K: el selector GLOBAL de Tipo no se toca (cambia el universo de negocio)");
  assertTrue(commercialState.creadas === datasetAntes, "K: el dataset es el MISMO (cero refetch)");
  assertEqual(uxFiltered().reduce((s, m) => s + m.count, 0), 4,
    "K: vuelve el universo completo del año");
}

// --- M: sin resultados usa el primitivo compartido del Hub --------------
{
  uxReady({ creadasSeller: "Nadie Existe" });
  const f = uxFiltered();
  assertEqual(f.length, 0, "M: el filtro no deja filas");
  [["tabla", commercialCreadasMonthTableHtml(f)], ["ranking", commercialCreadasSellerRankingHtml(f)]]
    .forEach(([que, html]) => {
      assertTrue(html.includes('class="dashboard-empty"'), `M: ${que} usa el primitivo compartido`);
      assertTrue(html.includes("Sin resultados"), `M: ${que} con el eyebrow compartido`);
    });
  assertTrue(commercialCreadasMonthTableHtml(f).includes("estos filtros"),
    "M: el mensaje distingue 'no hay CON ESTOS FILTROS'");
  uxReady({});
  assertTrue(commercialCreadasMonthTableHtml(uxFiltered(PREV_YEAR)).includes("en este año"),
    "M: de 'no hay EN EL AÑO'");
}

// --- N: la falla de fuente conserva su propia semantica ----------------
{
  resetCreadasState();
  commercialState.creadasStatus = "error";
  commercialState.creadasErrorState = "source_error";
  const html = commercialCreadasPanelBodyHtml();
  assertTrue(html.includes("No disponible"), "N: la falla de fuente NO se disfraza de vacio");
  assertTrue(!html.includes("Sin resultados"), "N: y no usa el eyebrow de vacio");
  assertTrue(html.includes("Reintentar"), "N: conserva su accion de reintento");
  commercialState.creadasErrorState = "config_incomplete";
  const cfg = commercialCreadasPanelBodyHtml();
  assertTrue(!cfg.includes("Reintentar"),
    "N: config incompleta sigue sin ofrecer reintento — un retry no lo arregla");
  resetCreadasState();
}

// --- ORDEN por columna clickeable --------------------------------------
{
  const opps = uxMonths()[0].opportunities;
  commercialState.creadasSortKey = null;
  assertEqual(commercialCreadasSorted(opps).map(o => o.id), [1, 2, 3],
    "ORDEN: sin clave activa, el CANONICO del legacy (entry_date ascendente)");
  commercialState.creadasSortKey = "client"; commercialState.creadasSortDir = 1;
  assertEqual(commercialCreadasSorted(opps).map(o => o.client), ["Colbun", "Danone", "Loma Negra"],
    "ORDEN: por cliente ascendente");
  commercialState.creadasSortDir = -1;
  assertEqual(commercialCreadasSorted(opps).map(o => o.client), ["Loma Negra", "Danone", "Colbun"],
    "ORDEN: invertido al re-clickear la misma columna");
  // La fecha ordena por el ISO, NO por el texto dd/mm/aaaa — que pondria el
  // 01/12 antes del 02/01.
  const cruzado = [
    { id: "a", entry_date: `${CUR_YEAR}-12-01` }, { id: "b", entry_date: `${CUR_YEAR}-01-02` },
  ];
  commercialState.creadasSortKey = "entry_date"; commercialState.creadasSortDir = 1;
  assertEqual(commercialCreadasSorted(cruzado).map(o => o.id), ["b", "a"],
    "ORDEN: la fecha ordena por ISO, nunca por el texto formateado");
  const originalIds = opps.map(o => o.id);
  commercialState.creadasSortKey = "client";
  commercialCreadasSorted(opps);
  assertEqual(opps.map(o => o.id), originalIds, "ORDEN: no muta el array de entrada");
  resetCreadasState();

  uxReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: null } });
  const detalle = commercialCreadasDetailHtml(uxFiltered());
  assertEqual(countOf(detalle, "dashboard-table-th--sortable"), 6,
    "ORDEN: las SEIS columnas del detalle son ordenables, con el primitivo COMPARTIDO");
  assertTrue(detalle.includes('data-commercial-creadas-sort="entry_date"'), "ORDEN: hook por columna");
  uxReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: null },
            creadasSortKey: "client", creadasSortDir: 1 });
  assertTrue(commercialCreadasDetailHtml(uxFiltered()).indexOf("Cliente ▲") !== -1,
    "ORDEN: la columna activa muestra su direccion");
  resetCreadasState();
}

// --- O: la conservacion no se toco -------------------------------------
{
  // El bucket de semana sigue siendo el del legacy — NO la semana
  // Monday-anchored del KPI. Es el invariante que separa los dos conceptos.
  assertEqual(commercialCreadasWeekOfMonth(`${CUR_YEAR}-07-08`), 2,
    "O: el bucket sigue siendo ceil(dia/7), sin unificar con el KPI semanal");
  uxReady({});
  const f = uxFiltered();
  const julio = f.find(m => m.month === `${CUR_YEAR}-07`);
  let porSemana = 0;
  for (let w = 1; w <= commercialCreadasWeekCount(julio.month); w += 1) {
    porSemana += julio.opportunities.filter(o => commercialCreadasWeekOfMonth(o.entry_date) === w).length;
  }
  assertEqual(porSemana, julio.count,
    "O: las semanas suman EXACTAMENTE el mes — cero perdida, cero duplicacion");
  assertEqual(f.reduce((s, m) => s + m.count, 0), 4, "O: y los meses suman el universo del año");
  resetCreadasState();
}

// --- ESTADO: el badge resuelve las VARIANTES de la fuente ---------------
// Owner live smoke: los datos de Odoo NO estan estandarizados — "Perdida" y
// "Perdido" conviven en el mismo mes, y "Suspendido" nunca tuvo badge. Con la
// comparacion exacta que tenia el legacy (`status === "Perdida"`) una de las
// dos variantes salia como texto plano al lado de la otra con badge rojo, en la
// misma columna. Se compara por RAIZ, sin distinguir mayusculas ni acentos.
{
  assertEqual(commercialCreadasStatusColor("Ganada"), "green", "ESTADO: Ganada");
  assertEqual(commercialCreadasStatusColor("Ganado"), "green", "ESTADO: Ganado (variante de genero)");
  assertEqual(commercialCreadasStatusColor("Perdida"), "red", "ESTADO: Perdida");
  assertEqual(commercialCreadasStatusColor("Perdido"), "red",
    "ESTADO: Perdido — la variante que se quedaba SIN badge");
  assertEqual(commercialCreadasStatusColor("Suspendido"), "grey",
    "ESTADO: Suspendido — no tenia badge en absoluto");
  assertEqual(commercialCreadasStatusColor("Suspendida"), "grey", "ESTADO: Suspendida");
  // Insensible a mayusculas y a espacios de la fuente.
  assertEqual(commercialCreadasStatusColor("PERDIDO"), "red", "ESTADO: insensible a mayusculas");
  assertEqual(commercialCreadasStatusColor("  Perdido  "), "red", "ESTADO: tolera espacios");
  // Las ETAPAS del pipeline NO llevan badge: un badge marca un desenlace, y una
  // etapa intermedia no lo es.
  ["1 - Inicial", "3 - Armando Orden Magnitud", "6 - Armando Propuestas Formales",
   "2 - Relevamiento", "8 - Propuesta formal Presentada"].forEach(etapa => {
    assertEqual(commercialCreadasStatusColor(etapa), null, `ESTADO: "${etapa}" va sin badge`);
  });
  assertEqual(commercialCreadasStatusColor(""), null, "ESTADO: vacio sin badge");
  assertEqual(commercialCreadasStatusColor(null), null, "ESTADO: null sin badge");

  // Y el TEXTO se muestra tal como viene de la fuente: normalizarlo en el
  // cliente esconderia la inconsistencia de Odoo, que es un problema real.
  uxReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: null } });
  const conVariantes = uxMonths();
  conVariantes[0].opportunities[0].status = "Perdido";
  const html = commercialCreadasDetailHtml(
    commercialCreadasFiltered(conVariantes, String(CUR_YEAR)));
  assertTrue(html.indexOf("Perdido") !== -1,
    "ESTADO: el texto se muestra tal cual llega, sin corregirlo por detras");
  assertTrue(html.indexOf("dashboard-status-badge--red") !== -1,
    "ESTADO: y ahora si resuelve su badge");
  resetCreadasState();
}

// ==========================================================================
// OWNER FINAL CORRECTIVE — proporciones de las barras, ranking interactivo,
// jerarquia visual y chevron compartido
// ==========================================================================

// Extrae los anchos de barra en ORDEN de aparicion.
const barWidths = (html) =>
  [...html.matchAll(/bar-fill" style="width:([\d.]+)%/g)].map(m => parseFloat(m[1]));

// Fixture con el EJEMPLO REAL del owner: julio con 4.040 h repartidas en cinco
// semanas. Los dias caen en semanas 1..5 con `ceil(dia/7)`.
function barsMonths() {
  const hs = [2220, 80, 420, 680, 640];
  const dias = [3, 10, 17, 24, 30];
  return [{
    month: `${CUR_YEAR}-07`, count: hs.length,
    opportunities: hs.map((h, i) => ({
      id: i, entry_date: `${CUR_YEAR}-07-${String(dias[i]).padStart(2, "0")}`,
      name: "Opp " + i, client: "Cliente " + i, seller: i < 3 ? "Ana" : "Bruno",
      origen: "Base Instalada", status: "1 - Inicial", horas: h,
    })),
  }];
}
function barsReady(extra) {
  resetCreadasState();
  commercialState.creadas = { months: barsMonths() };
  commercialState.creadasStatus = "ready";
  commercialState.creadasYear = String(CUR_YEAR);
  commercialState.creadasExpandedMonths = [`${CUR_YEAR}-07`];
  Object.assign(commercialState, extra || {});
}
const cerca = (a, b, tol) => Math.abs(a - b) <= (tol === undefined ? 0.15 : tol);

// --- BARRAS: la semana es su parte DEL MES PADRE --------------------------
// EL DEFECTO QUE CIERRA. Las barras median CANTIDAD DE OPORTUNIDADES —
// `d.count / maxCount` para meses y `opps.length / weekMax` para semanas,
// heredado del legacy (`renderCreatedDrillTable`)— mientras la columna de al
// lado muestra HORAS. Dos semanas de 80 h y 420 h con una oportunidad cada una
// salian IDENTICAS. Y las semanas se normalizaban contra la semana MAS GRANDE,
// asi que esa daba 100% siempre, sin importar que fraccion del mes fuera.
{
  barsReady({});
  const html = commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(barsMonths(), String(CUR_YEAR)));
  const w = barWidths(html);
  const hs = [2220, 80, 420, 680, 640];
  const total = 4040;

  assertEqual(w.length, 6, "BARRAS: una del mes + cinco de semana");
  // El mes es el unico visible, asi que es su propio maximo: 100%.
  assertTrue(cerca(w[0], 100), `BARRAS: el mes unico visible ocupa el 100% (dio ${w[0]})`);

  hs.forEach((h, i) => {
    const esperado = h / total * 100;
    assertTrue(cerca(w[i + 1], esperado),
      `BARRAS: semana ${i + 1} con ${h} h ≈ ${esperado.toFixed(1)}% del mes (dio ${w[i + 1]})`);
  });

  // EL GUARD CRITICO del owner: 80 h y 420 h NO pueden resolver al mismo ancho.
  assertTrue(w[2] !== w[3],
    `BARRAS: 80 h (${w[2]}%) y 420 h (${w[3]}%) tienen anchos DISTINTOS`);
  // Y la relacion entre ellas refleja la de los datos (5.25x), no un empate.
  const ratio = w[3] / w[2];
  assertTrue(cerca(ratio, 420 / 80, 0.3),
    `BARRAS: la razon entre 420 h y 80 h es ~5.25 en la barra (dio ${ratio.toFixed(2)})`);

  // Composicion: las semanas suman el mes y NINGUNA lo excede.
  const suma = w.slice(1).reduce((a, b) => a + b, 0);
  assertTrue(cerca(suma, 100, 0.5), `BARRAS: las semanas suman ~100% del mes (dio ${suma.toFixed(1)})`);
  assertTrue(w.slice(1).every(x => x <= 100), "BARRAS: ninguna semana excede el 100% de su padre");
}

// --- BARRAS: el mes se compara contra el MAXIMO VISIBLE -------------------
{
  resetCreadasState();
  const months = [
    { month: `${CUR_YEAR}-07`, count: 1, opportunities: [
      { id: 1, entry_date: `${CUR_YEAR}-07-03`, name: "A", client: "C", seller: "Ana",
        origen: "x", status: "1 - Inicial", horas: 1000 }] },
    { month: `${CUR_YEAR}-06`, count: 1, opportunities: [
      { id: 2, entry_date: `${CUR_YEAR}-06-03`, name: "B", client: "C", seller: "Bruno",
        origen: "x", status: "1 - Inicial", horas: 250 }] },
  ];
  commercialState.creadas = { months };
  commercialState.creadasStatus = "ready";
  commercialState.creadasYear = String(CUR_YEAR);
  const w = barWidths(commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR))));
  assertEqual(w.length, 2, "MESES: dos barras, ninguna semana (nada expandido)");
  assertTrue(cerca(w[0], 100), `MESES: el mes de 1000 h es el maximo → 100% (dio ${w[0]})`);
  assertTrue(cerca(w[1], 25), `MESES: el de 250 h es un cuarto → 25% (dio ${w[1]})`);
  // Y el denominador son HORAS, no oportunidades: los dos meses tienen UNA
  // oportunidad cada uno, asi que con la formula vieja habrian salido iguales.
  assertTrue(w[0] !== w[1],
    "MESES: dos meses con la MISMA cantidad de oportunidades pero distintas horas NO salen iguales");
}

// --- BARRAS: el denominador se recalcula con los FILTROS ------------------
// El maximo tiene que reflejar lo que se esta VIENDO. Si el filtro saca al mes
// mas grande, el que queda pasa a ser el 100%.
{
  resetCreadasState();
  const months = [
    { month: `${CUR_YEAR}-07`, count: 1, opportunities: [
      { id: 1, entry_date: `${CUR_YEAR}-07-03`, name: "A", client: "C", seller: "Ana",
        origen: "x", status: "1 - Inicial", horas: 1000 }] },
    { month: `${CUR_YEAR}-06`, count: 1, opportunities: [
      { id: 2, entry_date: `${CUR_YEAR}-06-03`, name: "B", client: "C", seller: "Bruno",
        origen: "x", status: "1 - Inicial", horas: 250 }] },
  ];
  commercialState.creadas = { months };
  commercialState.creadasStatus = "ready";
  commercialState.creadasYear = String(CUR_YEAR);

  const sinFiltro = barWidths(commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR))));
  assertTrue(cerca(sinFiltro[1], 25), "FILTRO: sin filtrar, el mes chico es el 25%");

  // Filtrando a Bruno desaparece el mes de 1000 h, asi que el de 250 pasa a
  // ser el maximo visible.
  commercialState.creadasSeller = "Bruno";
  const conFiltro = barWidths(commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR))));
  assertEqual(conFiltro.length, 1, "FILTRO: queda un solo mes visible");
  assertTrue(cerca(conFiltro[0], 100),
    `FILTRO: el denominador se recalculo — el mes que queda es el 100% (dio ${conFiltro[0]})`);
  resetCreadasState();
}

// --- BARRAS: cero horas no fabrica una barra positiva ---------------------
{
  resetCreadasState();
  const months = [{ month: `${CUR_YEAR}-07`, count: 2, opportunities: [
    { id: 1, entry_date: `${CUR_YEAR}-07-03`, name: "A", client: "C", seller: "Ana",
      origen: "x", status: "1 - Inicial", horas: null },
    { id: 2, entry_date: `${CUR_YEAR}-07-10`, name: "B", client: "C", seller: "Ana",
      origen: "x", status: "1 - Inicial", horas: null },
  ] }];
  commercialState.creadas = { months };
  commercialState.creadasStatus = "ready";
  commercialState.creadasYear = String(CUR_YEAR);
  commercialState.creadasExpandedMonths = [`${CUR_YEAR}-07`];
  const w = barWidths(commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(months, String(CUR_YEAR))));
  // Sin horas en ningun lado: cero barras positivas, ni un minimo inventado.
  assertTrue(w.every(x => x === 0),
    `CERO: sin horas cargadas ninguna barra se fabrica un ancho (dio ${JSON.stringify(w)})`);
  // Y la celda de Horas conserva su semantica de siempre.
  assertEqual(commercialCreadasHoursCell(months[0].opportunities), "—",
    "CERO: la celda sigue mostrando em-dash, no 0");
  resetCreadasState();
}

// --- RANKING CLICKEABLE: contrato de Metricas de Uso ----------------------
// Reusa `.bar-row--clickable` (tools.css, el MISMO bloque que
// `.activity-bar-row--clickable` de Metricas de Uso) y su contrato de markup.
{
  barsReady({});
  const html = commercialCreadasSellerRankingHtml(
    commercialCreadasFiltered(barsMonths(), String(CUR_YEAR)));
  assertTrue(html.includes("bar-row bar-row--clickable"), "RANKING: consume el primitivo compartido");
  assertTrue(html.includes('role="button"'), "RANKING: rol de boton");
  assertTrue(html.includes('tabindex="0"'), "RANKING: focoable con teclado");
  assertTrue(html.includes('aria-label="Filtrar las oportunidades creadas por'),
    "RANKING: dice que hace, no solo el nombre");
  assertTrue(html.includes("data-commercial-creadas-seller-pick="),
    "RANKING: hook por vendedor");
  // La fila ENTERA es el target: etiqueta, barra y valor van adentro.
  const fila = html.slice(html.indexOf("bar-row bar-row--clickable"));
  const cierre = fila.indexOf("</div>", fila.indexOf("bar-value"));
  const contenido = fila.slice(0, cierre);
  ["bar-label", "bar-track", "bar-value"].forEach(parte => {
    assertTrue(contenido.includes(parte), `RANKING: ${parte} esta DENTRO del target clickeable`);
  });
}

// --- RANKING: estado seleccionado, en los DOS sentidos --------------------
{
  barsReady({ creadasSeller: "Ana" });
  const html = commercialCreadasSellerRankingHtml(
    commercialCreadasFiltered(barsMonths(), String(CUR_YEAR)));
  assertTrue(html.includes("bar-row bar-row--clickable is-active"),
    "RANKING: el vendedor seleccionado se marca con el estado compartido");
  assertTrue(html.includes('aria-pressed="true"'), "RANKING: y lo expone semanticamente");
  // Solo UNO queda activo.
  assertEqual((html.match(/is-active/g) || []).length, 1, "RANKING: exactamente uno activo");
  assertEqual((html.match(/aria-pressed="true"/g) || []).length, 1, "RANKING: un solo aria-pressed");

  // Sin vendedor, ninguno.
  barsReady({});
  const limpio = commercialCreadasSellerRankingHtml(
    commercialCreadasFiltered(barsMonths(), String(CUR_YEAR)));
  assertEqual((limpio.match(/is-active/g) || []).length, 0,
    "RANKING: sin vendedor seleccionado, ninguna fila activa");
  assertEqual((limpio.match(/aria-pressed="true"/g) || []).length, 0, "RANKING: ni un aria-pressed");
}

// --- RANKING: el dropdown y el ranking comparten estado ------------------
// La sincronizacion bidireccional NO necesita codigo de sincronizacion: los dos
// DERIVAN de `commercialState.creadasSeller`, asi que no pueden divergir.
{
  barsReady({ creadasSeller: "Bruno" });
  const body = commercialCreadasPanelBodyHtml();
  assertTrue(body.includes('<option value="Bruno" selected>'),
    "SYNC: el dropdown muestra el vendedor activo");
  // La fila activa y el hook de Bruno tienen que ser LA MISMA fila. Se busca la
  // fila entera por su apertura, sin slices posicionales fragiles.
  const filas = [...body.matchAll(/<div class="bar-row bar-row--clickable([^"]*)"\s+data-commercial-creadas-seller-pick="([^"]+)"/g)]
    .map(m => ({ activa: m[1].includes("is-active"), vendedor: m[2] }));
  assertEqual(filas.filter(f => f.activa).map(f => f.vendedor), ["Bruno"],
    "SYNC: el ranking marca al MISMO vendedor que el dropdown, y solo a ese");
  // El universo tambien responde: la tabla del mes cuenta solo lo de Bruno (2
  // de las 5 oportunidades). Se cuenta sobre el bloque de la TABLA, no sobre
  // todo el body — el dropdown lista a todos los vendedores por definicion.
  const tabla = body.slice(body.indexOf("Por mes y semana"));
  const opps = (tabla.match(/<td>(\d+)<\/td>/) || [])[1];
  assertEqual(opps, "2", "SYNC: la tabla cuenta solo las oportunidades del vendedor filtrado");
}

// --- CAMBIO DE VENDEDOR: que pasa con el DETALLE abierto ------------------
{
  // El mes seleccionado SIGUE teniendo oportunidades del vendedor nuevo: el
  // detalle se re-renderiza al subconjunto, no se cierra.
  barsReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: null } });
  commercialCreadasSetSeller("Ana");
  assertTrue(commercialState.creadasSelection !== null,
    "DETALLE: sigue abierto si el mes conserva oportunidades del vendedor");
  assertEqual(commercialState.creadasExpandedMonths, [`${CUR_YEAR}-07`],
    "DETALLE: y los meses expandidos NO se colapsan");

  // La SEMANA seleccionada se queda sin oportunidades bajo el filtro nuevo:
  // se cierra SOLO el detalle.
  barsReady({ creadasSelection: { month: `${CUR_YEAR}-07`, week: 1 } });
  commercialCreadasSetSeller("Bruno");   // Bruno esta en las semanas 4 y 5
  assertEqual(commercialState.creadasSelection, null,
    "DETALLE: se cierra si la seleccion se queda vacia");
  assertEqual(commercialState.creadasExpandedMonths, [`${CUR_YEAR}-07`],
    "DETALLE: pero la expansion se preserva igual");
  assertEqual(commercialState.creadasSeller, "Bruno", "DETALLE: y el filtro quedo aplicado");
  resetCreadasState();
}

// --- CHEVRON: el primitivo COMPARTIDO del Hub ----------------------------
// `.dashboard-table-toggle` + `.caret` (tools.css) es el chevron DORADO que ya
// usan Reporting (arboles de Rentabilidad/Horas Vendidas) y Staffing (detalle
// por consultor). Creadas tenia un boton gris propio.
{
  barsReady({});
  const html = commercialCreadasMonthTableHtml(
    commercialCreadasFiltered(barsMonths(), String(CUR_YEAR)));
  assertTrue(html.includes('class="dashboard-table-toggle"'),
    "CHEVRON: consume el primitivo compartido del Hub");
  assertTrue(html.includes('<span class="caret">'),
    "CHEVRON: con el `.caret` del primitivo, que es quien lleva el dorado");
  assertTrue(!html.includes("commercial-creadas-caret"),
    "CHEVRON: cero rastro del primitivo local que tenia antes");
  // Sigue siendo un <button> real: Enter/Space nativos, sin JS de teclado.
  assertTrue(html.includes('<button type="button" class="dashboard-table-toggle"'),
    "CHEVRON: `<button>` real — teclado gratis del browser");
  assertTrue(html.includes('aria-expanded="true"'), "CHEVRON: anuncia su estado");
}

// --- JERARQUIA VISUAL: la semana, mas liviana ---------------------------
{
  const css = fs.readFileSync(
    path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.css"), "utf-8");
  const sinComentarios = css.replace(/\/\*[\s\S]*?\*\//g, "");
  assertTrue(/\.commercial-creadas-week[^{]*\.bar-fill\s*\{[^}]*opacity:/.test(sinComentarios),
    "JERARQUIA: la barra de semana baja la intensidad con opacity");
  // Y NO con un segundo amarillo inventado.
  const bloqueSemana = (sinComentarios.match(/\.commercial-creadas-week[^{]*\.bar-fill\s*\{([^}]*)\}/) || [])[1] || "";
  ["#", "rgb", "hsl", "gold"].forEach(color => {
    assertTrue(!bloqueSemana.includes(color),
      `JERARQUIA: la semana no declara color propio (${color}) — usa el token del padre`);
  });
}

if (failures > 0) {
  console.log(`${failures} creadas drill invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CREADAS DRILL INVARIANTS PASSED");
