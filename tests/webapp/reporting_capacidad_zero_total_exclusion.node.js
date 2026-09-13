// reporting_capacidad_zero_total_exclusion.node.js — invocado por
// test_reporting_capacidad_zero_total_exclusion.py. Owner corrective
// "Horas Productivas / No Asignadas — fila total=0" (ver HANDOFF, owner
// live QA: "Damián Berni", año 2026, ENE..DIC=0/Total=0 — esa fila no debe
// existir). Carga reporting.js REAL en un sandbox mínimo (MISMO patrón que
// reporting_capacidad_hotcold_lifecycle.node.js) y llama
// `renderCapacidadNoAsignadas()` directamente (sin pasar por el fetch/
// hidratación completos, que ya tienen su propio harness) para probar el
// criterio de exclusión SUM(mensual del Año/Consultor activos) > 0 —
// nunca "algunos meses en 0", siempre el TOTAL del período visible.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();
const fetchCalls = [];

function fakeFetch(url) {
  fetchCalls.push(url);
  return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) });
}

const sandbox = {
  document: {
    addEventListener() {},
    getElementById(id) {
      if (id === "reporting-experience-tabs") return tabsEl;
      if (id === "reporting-main-content") return mainEl;
      return makeEl();
    },
  },
  window: {},
  dwGraphToken: async () => "",
  sessionStorage: { getItem() { return ""; } },
  fetch: fakeFetch,
  initDashboardSections() {},
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  setInterval() { return 0; },
  clearInterval() {},
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { renderCapacidadNoAsignadas, renderCapacidadProductivas, capacidadPanelAnioKey } = sandbox;
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_NOASIGNADAS_ROWS = vm.runInContext("CAPACIDAD_NOASIGNADAS_ROWS", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);
// TASK CORRECTIVE (owner ticket "URGENT REPORTING CORRECTNESS — HNP TASK +
// SOLD HOURS YEAR ACCUMULATION" §1B): `capacidadNoAsignadasSelectedRows`
// ahora filtra por `tarea` (default = la tarea canónica 01) — las filas de
// este fixture representan ESE concepto de siempre, así que necesitan la
// etiqueta explícita para seguir matcheando el filtro default (antes de
// esa corrective no existía tal filtro, cualquier fila calificaba).
const NOASIGNADAS_TAREA_DEFAULT = vm.runInContext("NOASIGNADAS_TAREA_DEFAULT", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }

function setRows(target, rows) { target.length = 0; rows.forEach(r => target.push(r)); }
function z12() { return Array(12).fill(0); }
function arr(...nonZeroAt) { const a = z12(); nonZeroAt.forEach(([i, v]) => { a[i] = v; }); return a; }

async function main() {
  state.experience = "capacidad";
  state.perspective.capacidad = "No Asignadas";
  state.batchBDataStatus = "ready";
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKey("noAsignadas")] = "2026";

  // Universo NO degenerado (owner §): doce ceros (debe excluirse), un solo
  // mes>0 (debe incluirse pese a los otros once en 0), y un tercero que
  // sólo tiene datos en OTRO año (para el corrective de cambio de año).
  setRows(CAPACIDAD_NOASIGNADAS_ROWS, [
    { consultor: "Damián Berni", tarea: NOASIGNADAS_TAREA_DEFAULT, hsAsig: 160, horasByYear: { "2026": z12() } },
    { consultor: "Ana López", tarea: NOASIGNADAS_TAREA_DEFAULT, hsAsig: 160, horasByYear: { "2026": arr([3, 5]) } },
    { consultor: "Beto Ruiz", tarea: NOASIGNADAS_TAREA_DEFAULT, hsAsig: 120, horasByYear: { "2026": arr([0, 2], [1, 2], [2, 2]), "2025": z12() } },
  ]);

  // ══ A) consultor con doce meses en 0 -> EXCLUIDO ══
  let html = renderCapacidadNoAsignadas();
  assertFalse(html.includes("Damián Berni"), "A: consultor con Total=0 (doce meses en 0) NUNCA aparece como fila");

  // ══ B) consultor con UN mes > 0 y el resto en 0 -> INCLUIDO ══
  assertTrue(html.includes("Ana López"), "B: consultor con UN solo mes > 0 sigue incluido (nunca se excluye por meses individuales en 0)");

  // ══ C) "Todos" excluye SÓLO los total=0 — Beto Ruiz (total>0 en 2026) también queda ══
  assertTrue(html.includes("Beto Ruiz"), "C: 'Todos' conserva TODOS los consultores con total>0, no sólo el primero");
  assertFalse(html.includes("Sin filas para los filtros actuales"), "C: con filas válidas, NUNCA el empty state");

  // ══ D) Total general = SUMA de las filas REALMENTE incluidas (nunca de las 3 originales) ══
  // Ana López: mes índice 3 = 5 · Beto Ruiz: meses 0/1/2 = 2 cada uno = 6 ·
  // Total esperado = 11 (Damián Berni, excluido, no suma nada).
  const totalMatch = html.match(/is-total[\s\S]*?<\/tr>/);
  assertTrue(!!totalMatch, "D: setup — hay fila de Total");
  if (totalMatch) {
    const nums = [...totalMatch[0].matchAll(/>(\d[\d.,]*)</g)].map(m => m[1]);
    const total = nums.reduce((s, n) => s + Number(n.replace(/\./g, "").replace(",", ".")), 0);
    assertTrue(total === 11, `D: el Total conserva la suma de las filas visibles (Ana 5 + Beto 6 = 11, got ${total})`);
  }

  // ══ E) consultor SELECCIONADO con total=0 -> empty state canónico, NUNCA una fila de doce ceros ══
  state.filters.capacidad.consultor = "Damián Berni";
  html = renderCapacidadNoAsignadas();
  assertTrue(html.includes("Sin filas para los filtros actuales"), "E: consultor seleccionado con Total=0 -> empty state canónico");
  assertFalse(html.includes("<td>0</td>") && html.includes("Damián Berni"), "E: NUNCA una fila de doce ceros para el consultor seleccionado");
  state.filters.capacidad.consultor = "";

  // ══ F) cambio de Año recalcula correctamente — Beto Ruiz tiene 2025 en 0 ══
  state.filters.capacidad[capacidadPanelAnioKey("noAsignadas")] = "2025";
  html = renderCapacidadNoAsignadas();
  assertFalse(html.includes("Beto Ruiz"), "F: Beto Ruiz (Total=0 en 2025) se excluye al cambiar de Año, aunque en 2026 sumara 6");
  assertFalse(html.includes("Ana López"), "F: Ana López tampoco tiene datos en 2025 (horasByYear sin esa key -> _z12() -> Total=0)");
  assertFalse(html.includes("Damián Berni"), "F: Damián Berni sigue excluido (nunca tuvo datos)");
  assertTrue(html.includes("Sin filas para los filtros actuales"), "F: 2025 sin ningún consultor con Total>0 -> empty state canónico ('Todos' también respeta la exclusión)");
  state.filters.capacidad[capacidadPanelAnioKey("noAsignadas")] = "2026";

  // ══ G) Productivas NO cambia — MISMA fuente/función, nunca tocada ══
  setRows(CAPACIDAD_PRODUCTIVAS_ROWS, [
    { cliente: "ClienteCero", proyecto: "ProyectoCero", estado: "En curso", porcentajePart: 0,
      horasByYear: { "2026": z12() } },
  ]);
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2026";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2026";
  state.filters.capacidad.proyecto = "";
  const productivasHtml = renderCapacidadProductivas();
  assertTrue(productivasHtml.includes("ClienteCero"),
    "G: Productivas SIGUE mostrando filas con Horas=0 — la exclusión es EXCLUSIVA de No Asignadas, nunca tocó Productivas");

  // ══ H) sin fetch adicional — todo el corrective es client-side puro ══
  assertTrue(fetchCalls.length === 0, `H: cero requests nuevas — filtrado 100% client-side sobre el dataset ya hidratado (got ${fetchCalls.length})`);

  if (failures > 0) {
    console.log(`\n${failures} invariante(s) de exclusión Total=0 FALLARON`);
    process.exit(1);
  }
  console.log("ALL CAPACIDAD ZERO-TOTAL EXCLUSION INVARIANTS PASSED");
  process.exit(0);
}

main().catch(err => { console.log("FAIL: uncaught error: " + err.stack); process.exit(1); });
