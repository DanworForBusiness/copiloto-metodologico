// reporting_rentabilidad_source_parity.node.js — invocado por
// test_reporting_rentabilidad.py. RENTABILIDAD — PBIP <-> HUB SOURCE-PARITY
// RECONCILIATION (owner live comparison vs PBIP): Vendidas dejó de ser un
// escalar de portfolio fijo (defecto #2, +892 vs PBIP) — ahora vive
// per-proyecto (`vendByYear`/`vendMonthly`, MISMO shape que
// certByYear/trabByYear) y responde a los MISMOS facets que Certificadas/
// Trabajadas (Estado + CP/Cliente/Tipo/Proyecto), reproduciendo los slicers
// "Tipo Proyecto Dash"/"Status" que el PBIP tiene en la página "Analisis
// Rentabilidad". Carga reporting.js en un sandbox mínimo y ejecuta el MOTOR
// REAL (`rentabilidadSelectedRows`) contra fixtures sintéticas — prueba que
// Vendidas efectivamente se ANGOSTA cuando Tipo Proyecto/Estado angostan la
// población, nunca que siga siendo un total fijo de portfolio (el bug real
// que infló Vendidas +892 sobre el caso dorado del owner).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  rentabilidadSelectedRows, rentabilidadFilteredProyectos, rentabilidadAnioOptions,
} = sandbox;
const state = vm.runInContext("state", sandbox);
const RENTABILIDAD_PROYECTOS = vm.runInContext("RENTABILIDAD_PROYECTOS", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

function resetProyectos(rows) {
  RENTABILIDAD_PROYECTOS.length = 0;
  RENTABILIDAD_PROYECTOS.push(...rows);
}

// --- Vendidas vive per-proyecto (vendByYear/vendMonthly) y se PROYECTA al
// --- Año/Mes elegido igual que Certificadas/Trabajadas — nunca un escalar
// --- de portfolio fijo -------------------------------------------------
{
  resetProyectos([
    { proyecto: "Mantenimiento Danone", cliente: "Danone", cp: "María López", tipo: "Mantenimiento", estado: "En curso",
      certByYear: { "2026": [10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      trabByYear: { "2026": [8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      vendByYear: { "2026": [12, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
    { proyecto: "Rollout YPF", cliente: "YPF", cp: "Diego Ramos", tipo: "Proyecto", estado: "En curso",
      certByYear: { "2026": [20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      trabByYear: { "2026": [18, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      vendByYear: { "2026": [25, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
  ]);
  state.filters.rentabilidad = { anio: "2026", mes: "" };
  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };

  const allRows = rentabilidadSelectedRows();
  const vendidasTotal = allRows.reduce((s, p) => s + p.vendidas, 0);
  assertEqual(vendidasTotal, 37, "sin filtro de Tipo Proyecto: Vendidas = 12+25 = 37 (ambos proyectos)");

  // Angostar por Tipo Proyecto=Mantenimiento (MISMO slicer "Tipo Proyecto
  // Dash" que el PBIP tiene en la página real) — Vendidas DEBE angostarse
  // junto con Certificadas/Trabajadas, nunca quedar en 37 (el bug real: un
  // escalar de portfolio que ignoraba este filtro, inflando el KPI).
  state.filters.rentabilidad.tipo = "Mantenimiento";
  const mantenimientoRows = rentabilidadSelectedRows();
  const vendidasMantenimiento = mantenimientoRows.reduce((s, p) => s + p.vendidas, 0);
  assertEqual(vendidasMantenimiento, 12, "Tipo Proyecto=Mantenimiento: Vendidas se angosta a 12 (SOLO Danone) — jamás 37 (portfolio completo, el defecto real)");
  const certMantenimiento = mantenimientoRows.reduce((s, p) => s + p.certificadas, 0);
  assertEqual(certMantenimiento, 10, "Certificadas se angosta IGUAL bajo el mismo filtro (comportamiento ya correcto, referencia)");
  delete state.filters.rentabilidad.tipo;
}

// --- Vendidas también responde a Estado (MISMO slicer "Status" del PBIP) --
{
  resetProyectos([
    { proyecto: "Proyecto Activo", cliente: "Aluar", cp: "Diego Ramos", tipo: "Proyecto", estado: "En curso",
      certByYear: { "2026": [5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      trabByYear: { "2026": [4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      vendByYear: { "2026": [6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
    { proyecto: "Proyecto Cerrado", cliente: "Mercado Libre", cp: "Diego Ramos", tipo: "Proyecto", estado: "Finalizado",
      certByYear: { "2026": [9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      trabByYear: { "2026": [7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      vendByYear: { "2026": [11, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
  ]);
  state.filters.rentabilidad = { anio: "2026", mes: "" };

  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
  assertEqual(rentabilidadSelectedRows().reduce((s, p) => s + p.vendidas, 0), 17, "ambos Estado tildados: Vendidas = 6+11 = 17");

  state.rentabilidadStatus = { "En curso": true, "Finalizado": false };
  assertEqual(rentabilidadSelectedRows().reduce((s, p) => s + p.vendidas, 0), 6, "SOLO En curso: Vendidas se angosta a 6 (excluye el Finalizado) — MISMO criterio que Certificadas/Trabajadas");
}

// --- Mes enmascara vendMonthly igual que cert/trabMonthly -----------------
{
  resetProyectos([
    { proyecto: "Proyecto Mensual", cliente: "AySA", cp: "Diego Ramos", tipo: "Proyecto", estado: "En curso",
      certByYear: { "2026": [1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      trabByYear: { "2026": [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
      vendByYear: { "2026": [4, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
  ]);
  state.filters.rentabilidad = { anio: "2026", mes: "2" };
  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
  const rows = rentabilidadSelectedRows();
  assertEqual(rows[0].vendidas, 5, "Mes=Febrero: Vendidas aísla el mes 2 (5), MISMO criterio que Certificadas/Trabajadas");
  // Índices 8-11 (Sep-Dic) llegan `null`, no `0` — CURRENT-YEAR FUTURE-MONTH
  // CAP (_applyMonthCap) sobre el año en curso, MISMO criterio que cert/
  // trabMonthly (ver _operationalMonthCap) — no es parte de este corrective,
  // solo se refleja acá porque el fixture usa Año=2026.
  const cap = sandbox._operationalMonthCap("2026");
  const expectedVend = [0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].map((v, i) => (cap != null && i >= cap ? null : v));
  assertEqual(rows[0].vendMonthly, expectedVend, "vendMonthly enmascarado igual que certMonthly/trabMonthly (respetando el mismo current-year future-month cap)");
}

// --- rentabilidadAnioOptions también deriva de vendByYear (no solo cert/trab) --
{
  resetProyectos([
    { proyecto: "Solo Vendidas 2019", cliente: "Ternium", cp: "Diego Ramos", tipo: "Proyecto", estado: "En curso",
      certByYear: {}, trabByYear: {}, vendByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  ]);
  state.filters.rentabilidad = {};
  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
  state.rentabilidadAniosDisponibles = [];
  assertTrue(rentabilidadAnioOptions().includes("2019"), "un año presente SOLO en vendByYear (ningún hecho de cert/trab ese año) sigue apareciendo como opción de Año");
}

if (failures > 0) {
  console.log(`${failures} rentabilidad source-parity invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL RENTABILIDAD SOURCE-PARITY INVARIANTS PASSED");
