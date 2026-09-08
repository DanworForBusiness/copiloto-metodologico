// reporting_capacidad_no_asignadas_tarea.node.js — invocado por
// test_reporting_hnp_task_semantics.py. Owner ticket "URGENT REPORTING
// CORRECTNESS — HNP TASK + SOLD HOURS YEAR ACCUMULATION" §1B: "Horas No
// Productivas" != "No asignadas a servicios" — el proyecto placeholder HNP
// agrupa VARIAS tareas de negocio (01 No Asignación/Vacaciones/otras), y
// sumarlas todas mezclaría conceptos distintos. Carga reporting.js REAL en
// un sandbox mínimo (MISMO patrón que reporting_capacidad_composition.node.js)
// y ejercita las funciones bespoke de Tarea (capacidadNoAsignadasTareaOptions/
// capacidadNoAsignadasSelectedRows) contra una fixture sintética de 3 tareas.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  dwGraphToken: async () => "",
  HubForegroundProgress: { begin() {}, complete() {}, fail() {}, settle() {} },
  setInterval() { return 0; },
  clearInterval() {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { capacidadNoAsignadasTareaOptions, capacidadNoAsignadasSelectedRows } = sandbox;
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_NOASIGNADAS_ROWS = vm.runInContext("CAPACIDAD_NOASIGNADAS_ROWS", sandbox);
const TAREA_KEY = vm.runInContext("_NOASIGNADAS_TAREA_KEY", sandbox);
const TAREA_DEFAULT = vm.runInContext("NOASIGNADAS_TAREA_DEFAULT", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
function assertEqSet(actual, expected, label) {
  const a = [...actual].sort().join(",");
  const e = [...expected].sort().join(",");
  if (a !== e) { failures++; console.log(`FAIL: ${label} (expected [${e}], got [${a}])`); }
}

// Fixture: MISMA que la ticket pide — un consultor, 3 tareas HNP en 2026,
// Enero (índice 0) para simplificar la suma mensual.
function seedRows() {
  CAPACIDAD_NOASIGNADAS_ROWS.length = 0;
  CAPACIDAD_NOASIGNADAS_ROWS.push(
    { consultor: "Ana", tarea: "01. No Asignación a Servicio en Cliente", hsAsig: 0,
      horasByYear: { "2026": [8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
    { consultor: "Ana", tarea: "02. Vacaciones", hsAsig: 0,
      horasByYear: { "2026": [6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
    { consultor: "Ana", tarea: "03. Other", hsAsig: 0,
      horasByYear: { "2026": [4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
  );
}

function totalHoras(rows) {
  return rows.reduce((acc, r) => acc + (r.monthly || []).reduce((a, b) => a + b, 0), 0);
}

async function main() {
  // D + F: default es la tarea canónica, y el dropdown solo ofrece las
  // 3 tareas HNP observadas (nunca "Todas", nunca fabricado).
  seedRows();
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKeySafe()] = "2026";
  assertEqSet(
    capacidadNoAsignadasTareaOptions(),
    ["01. No Asignación a Servicio en Cliente", "02. Vacaciones", "03. Other"],
    "F: el dropdown de Tarea contiene EXACTAMENTE las 3 tareas observadas bajo HNP",
  );
  assertEqual(getFilterSafe(TAREA_KEY) || TAREA_DEFAULT, TAREA_DEFAULT,
    "D: sin selección explícita, la tarea vigente es la canónica (01)");

  // A: default (sin elegir Tarea) = 8h, NUNCA 18h (la suma de las 3 tareas).
  {
    const rows = capacidadNoAsignadasSelectedRows();
    assertEqual(totalHoras(rows), 8, "A: default No asignadas = 8h, no 18h (nunca suma las 3 tareas)");
    assertEqual(rows.length, 1, "A: default -- una sola fila (la de la tarea 01), no las 3");
  }

  // B: seleccionar Vacaciones explícitamente -- 6h, ninguna otra tarea entra.
  {
    state.filters.capacidad[TAREA_KEY] = "02. Vacaciones";
    const rows = capacidadNoAsignadasSelectedRows();
    assertEqual(totalHoras(rows), 6, "B: seleccionar Vacaciones = 6h");
  }

  // C: seleccionar la tarea 03 -- 4h.
  {
    state.filters.capacidad[TAREA_KEY] = "03. Other";
    const rows = capacidadNoAsignadasSelectedRows();
    assertEqual(totalHoras(rows), 4, "C: seleccionar tarea 03 = 4h");
  }

  // E: la tarea 01 AUSENTE del todo (nunca observada) -- default sigue
  // siendo la canónica, CERO filas, NUNCA cae a sumar las tareas restantes.
  {
    CAPACIDAD_NOASIGNADAS_ROWS.length = 0;
    CAPACIDAD_NOASIGNADAS_ROWS.push(
      { consultor: "Ana", tarea: "02. Vacaciones", hsAsig: 0,
        horasByYear: { "2026": [6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
      { consultor: "Ana", tarea: "03. Other", hsAsig: 0,
        horasByYear: { "2026": [4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
    );
    delete state.filters.capacidad[TAREA_KEY];   // sin selección explícita -- vuelve al default
    const rows = capacidadNoAsignadasSelectedRows();
    assertEqual(rows.length, 0, "E: tarea 01 ausente -- CERO filas, nunca fallback a Vacaciones/Other");
    assertEqual(totalHoras(rows), 0, "E: total 0, nunca las 6h+4h de las otras tareas");
  }

  if (failures > 0) {
    console.log(`${failures} No Asignadas Tarea invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL NO ASIGNADAS TAREA INVARIANTS PASSED");
  process.exit(0);
}

// Helpers locales mínimos -- evitan pull extra de funciones internas de
// reporting.js que no hacen falta para este harness (capacidadPanelAnioKey/
// getFilter SÍ están expuestas como top-level function, se re-exponen acá
// con nombre distinto para no chocar con las de arriba si alguna vez cambian).
function capacidadPanelAnioKeySafe() { return vm.runInContext('capacidadPanelAnioKey("noAsignadas")', sandbox); }
function getFilterSafe(key) { return vm.runInContext(`getFilter("capacidad", ${JSON.stringify(key)})`, sandbox); }

main();
