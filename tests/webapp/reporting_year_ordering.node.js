// reporting_year_ordering.node.js — invocado por
// test_reporting_year_ordering.py. YEAR ORDERING corrective (owner decision
// 2026-08-19): homologa TODOS los selectores/filtros de Año del Hub a
// DESCENDENTE (más reciente primero), separando presentación (orden visual)
// de semántica (año default = "más reciente VÁLIDO", nunca "última posición
// del array"). Carga reporting.js real en un sandbox Node (mismo patrón que
// reporting_capacidad_ux_corrective.node.js) y ejecuta el MOTOR REAL contra
// fixtures sintéticas — prueba comportamiento real, no solo presencia de un
// patrón en el source.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const sandbox = {
  document: { addEventListener() {}, getElementById() { return makeEl(); }, querySelector() { return null; } },
  window: {},
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  initDashboardSections() {},
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  setInterval() { return 0; },
  clearInterval() {},
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  mostRecentValidYear, distinctValues,
  carteraAnioOptions, carteraAnioInicioOptions,
  rentabilidadAnioOptions, rentabilidadDefaultAnio,
  horasVendidasAnioOptions, horasVendidasDefaultAnio,
  comercialParticipacionAnioOptions,
  capacidadProductivasAnioOptions, capacidadProductivasDefaultAnio,
  capacidadNoAsignadasAnioOptions, capacidadNoAsignadasDefaultAnio,
} = sandbox;
const state = vm.runInContext("state", sandbox);
const CARTERA_MANTENIMIENTO_ROWS = vm.runInContext("CARTERA_MANTENIMIENTO_ROWS", sandbox);
const CARTERA_PROYECTO_ROWS = vm.runInContext("CARTERA_PROYECTO_ROWS", sandbox);
const RENTABILIDAD_PROYECTOS = vm.runInContext("RENTABILIDAD_PROYECTOS", sandbox);
const HORASVENDIDAS_ROWS = vm.runInContext("HORASVENDIDAS_ROWS", sandbox);
const COMERCIAL_PARTICIPACION_ROWS = vm.runInContext("COMERCIAL_PARTICIPACION_ROWS", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);
const CAPACIDAD_NOASIGNADAS_ROWS = vm.runInContext("CAPACIDAD_NOASIGNADAS_ROWS", sandbox);
// TASK CORRECTIVE (owner ticket "URGENT REPORTING CORRECTNESS — HNP TASK +
// SOLD HOURS YEAR ACCUMULATION" §1B): `capacidadNoAsignadasAnioOptions`
// ahora acota por la Tarea vigente (default = la tarea canónica 01) antes
// de calcular años -- las filas de este fixture necesitan la etiqueta
// explícita para seguir siendo elegibles bajo ese default.
const NOASIGNADAS_TAREA_DEFAULT = vm.runInContext("NOASIGNADAS_TAREA_DEFAULT", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { failures++; console.log(`FAIL: ${label} — expected ${e}, got ${a}`); }
}
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

function clearAll(arr) { arr.length = 0; }

// ============================================================================
// A) Cartera/Mantenimiento — YA descendente, preservar (no regresión).
// ============================================================================
{
  clearAll(CARTERA_MANTENIMIENTO_ROWS);
  CARTERA_MANTENIMIENTO_ROWS.push(
    { cliente: "Danone", status: "En curso", anio: "2024", mes: "3" },
    { cliente: "Danone", status: "En curso", anio: "2026", mes: "1" },
    { cliente: "Danone", status: "En curso", anio: "2025", mes: "6" },
  );
  state.carteraStatus = { "En curso": true, "Finalizado": true };
  state.carteraMantenimientoAniosDisponibles = [];
  assertEqual(carteraAnioOptions(), ["2026", "2025", "2024"], "A: Cartera/Mantenimiento sigue DESCENDENTE (2026,2025,2024)");
}

// ============================================================================
// B) Cartera/Proyecto — Año de Inicio pasa a descendente.
// ============================================================================
{
  clearAll(CARTERA_PROYECTO_ROWS);
  CARTERA_PROYECTO_ROWS.push(
    { cliente: "YPF", status: "En curso", anioInicio: "2023", proyecto: "P1", cp: "X" },
    { cliente: "YPF", status: "En curso", anioInicio: "2026", proyecto: "P2", cp: "X" },
    { cliente: "YPF", status: "En curso", anioInicio: "2025", proyecto: "P3", cp: "X" },
  );
  state.carteraStatus = { "En curso": true, "Finalizado": true };
  state.filters.cartera = {};
  assertEqual(carteraAnioInicioOptions(), ["2026", "2025", "2023"], "B: Cartera/Proyecto Año de Inicio pasa a DESCENDENTE");
}

// ============================================================================
// C) distinctValues() genérico (facets de string no-año) NO cambia de
//    default — sigue ascendente/localeCompare.
// ============================================================================
{
  const rows = [{ cliente: "YPF" }, { cliente: "Aluar" }, { cliente: "Danone" }];
  assertEqual(distinctValues(rows, "cliente"), ["Aluar", "Danone", "YPF"], "C: distinctValues() genérico (cliente) sigue ASCENDENTE por default, sin tocar");
}

// ============================================================================
// D) Rentabilidad — descendente.
// ============================================================================
{
  clearAll(RENTABILIDAD_PROYECTOS);
  RENTABILIDAD_PROYECTOS.push({
    tipo: "Mantenimiento", cliente: "Danone", cp: "X", proyecto: "P1", estado: "En curso",
    certByYear: { "2024": Array(12).fill(0), "2026": Array(12).fill(0), "2025": Array(12).fill(0) },
    trabByYear: {}, vendByYear: {},
  });
  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
  state.filters.rentabilidad = {};
  state.rentabilidadAniosDisponibles = [];
  assertEqual(rentabilidadAnioOptions(), ["2026", "2025", "2024"], "D: Rentabilidad selector DESCENDENTE");
}

// ============================================================================
// E) Horas Vendidas — descendente.
// ============================================================================
{
  clearAll(HORASVENDIDAS_ROWS);
  HORASVENDIDAS_ROWS.push({
    clientPartner: "X", tipoProyecto: "Mantenimiento", cliente: "Danone", proyecto: "P1", estado: "En curso",
    vendidasByYear: { "2024": Array(12).fill(0), "2026": Array(12).fill(0), "2025": Array(12).fill(0) },
  });
  state.horasVendidasStatus = { "En curso": true, "Finalizado": true };
  state.filters.horasvendidas = {};
  state.horasVendidasAniosDisponibles = [];
  assertEqual(horasVendidasAnioOptions(), ["2026", "2025", "2024"], "E: Horas Vendidas selector DESCENDENTE");
}

// ============================================================================
// F) Participación/Comercial — descendente.
// ============================================================================
{
  clearAll(COMERCIAL_PARTICIPACION_ROWS);
  COMERCIAL_PARTICIPACION_ROWS.push({
    cp: "X", cliente: "Danone",
    vendidasByYear: { "2024": { "En curso": 1 }, "2026": { "En curso": 1 }, "2025": { "En curso": 1 } },
    certificadasByYear: {},
  });
  state.comercialStatus = { "En curso": true, "Finalizado": true };
  state.filters.comercial = {};
  state.comercialParticipacionAniosDisponibles = [];
  assertEqual(comercialParticipacionAnioOptions(), ["2026", "2025", "2024"], "F: Participación/Comercial selector DESCENDENTE");
}

// ============================================================================
// G) Capacidad/Productivas — descendente.
// ============================================================================
{
  clearAll(CAPACIDAD_PRODUCTIVAS_ROWS);
  CAPACIDAD_PRODUCTIVAS_ROWS.push({
    cliente: "YPF", proyecto: "SADI v2", estado: "En curso",
    horasByYear: { "2024": Array(12).fill(0), "2026": Array(12).fill(0), "2025": Array(12).fill(0) },
  });
  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  state.filters.capacidad = {};
  state.capacidadAniosDisponibles = [];
  assertEqual(capacidadProductivasAnioOptions(), ["2026", "2025", "2024"], "G: Capacidad/Productivas selector DESCENDENTE");
}

// ============================================================================
// H) Capacidad/No Asignadas — descendente.
// ============================================================================
{
  clearAll(CAPACIDAD_NOASIGNADAS_ROWS);
  CAPACIDAD_NOASIGNADAS_ROWS.push({
    consultor: "Juan",
    tarea: NOASIGNADAS_TAREA_DEFAULT,
    horasByYear: { "2024": Array(12).fill(0), "2026": Array(12).fill(0), "2025": Array(12).fill(0) },
  });
  state.filters.capacidad = {};
  state.capacidadAniosDisponibles = [];
  assertEqual(capacidadNoAsignadasAnioOptions(), ["2026", "2025", "2024"], "H: Capacidad/No Asignadas selector DESCENDENTE");
}

// ============================================================================
// I) mostRecentValidYear — MISMO resultado semántico sea el input
//    ascendente, descendente, o sin ordenar. Nunca depende de posición.
// ============================================================================
assertEqual(mostRecentValidYear(["2024", "2025", "2026"]), "2026", "I: mostRecentValidYear — input ASCENDENTE");
assertEqual(mostRecentValidYear(["2026", "2025", "2024"]), "2026", "I: mostRecentValidYear — input DESCENDENTE");
assertEqual(mostRecentValidYear(["2024", "2026", "2025"]), "2026", "I: mostRecentValidYear — input SIN ORDENAR");

// ============================================================================
// J) valores inválidos/no-año NUNCA ganan por accidente (ni por ser el
//    último del array, ni por comparar mayor numéricamente si algo
//    corrupto colara un valor no-4-dígitos).
// ============================================================================
assertEqual(mostRecentValidYear(["2024", "2025", "Septiembre"]), "2025", "J: valor no-año ('Septiembre') NUNCA se elige como año más reciente");
assertEqual(mostRecentValidYear(["", "2023"]), "2023", "J: string vacío se descarta, no gana por posición");
assertEqual(mostRecentValidYear([]), "", "J: sin opciones válidas -> string vacío, nunca undefined/NaN");

// ============================================================================
// K-N) Los 4 defaults acoplados (audit previo) siguen eligiendo el año más
//      reciente VÁLIDO, ahora independiente del orden de presentación
//      (ya DESCENDENTE desde D-H arriba).
// ============================================================================
{
  // K) Rentabilidad — año actual (2026, hoy) representado -> ese mismo.
  assertEqual(rentabilidadDefaultAnio(), "2026", "K: rentabilidadDefaultAnio — año actual representado, lo elige");
  // Simula año actual AUSENTE — debe caer al más reciente disponible, NUNCA
  // al primero del array (que ahora es descendente, o sea 2025 sería el MÁS
  // VIEJO de los dos si el helper mirara posición en vez de valor).
  RENTABILIDAD_PROYECTOS[0].certByYear = { "2023": Array(12).fill(0), "2025": Array(12).fill(0) };
  assertEqual(rentabilidadDefaultAnio(), "2025", "K: rentabilidadDefaultAnio sin año actual -> el MÁS RECIENTE disponible (2025), no el primero del array descendente");
}
{
  // L) Horas Vendidas — mismo criterio.
  assertEqual(horasVendidasDefaultAnio(), "2026", "L: horasVendidasDefaultAnio — año actual representado, lo elige");
  HORASVENDIDAS_ROWS[0].vendidasByYear = { "2021": Array(12).fill(0), "2024": Array(12).fill(0) };
  assertEqual(horasVendidasDefaultAnio(), "2024", "L: horasVendidasDefaultAnio sin año actual -> el MÁS RECIENTE (2024)");
}
{
  // M) Capacidad/Productivas.
  assertEqual(capacidadProductivasDefaultAnio(), "2026", "M: capacidadProductivasDefaultAnio — año actual representado, lo elige");
  CAPACIDAD_PRODUCTIVAS_ROWS[0].horasByYear = { "2018": Array(12).fill(0), "2022": Array(12).fill(0) };
  assertEqual(capacidadProductivasDefaultAnio(), "2022", "M: capacidadProductivasDefaultAnio sin año actual -> el MÁS RECIENTE (2022)");
}
{
  // N) Capacidad/No Asignadas.
  assertEqual(capacidadNoAsignadasDefaultAnio(), "2026", "N: capacidadNoAsignadasDefaultAnio — año actual representado, lo elige");
  CAPACIDAD_NOASIGNADAS_ROWS[0].horasByYear = { "2020": Array(12).fill(0), "2017": Array(12).fill(0) };
  assertEqual(capacidadNoAsignadasDefaultAnio(), "2020", "N: capacidadNoAsignadasDefaultAnio sin año actual -> el MÁS RECIENTE (2020), no el primero del array descendente");
}

if (failures > 0) {
  console.log(`${failures} year-ordering invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL YEAR ORDERING INVARIANTS PASSED");
process.exit(0);
