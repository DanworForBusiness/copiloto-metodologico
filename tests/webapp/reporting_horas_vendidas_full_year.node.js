// reporting_horas_vendidas_full_year.node.js — invocado por
// test_reporting_horas_vendidas_full_year.py.
//
// Owner ticket "URGENT REPORTING CORRECTNESS — HNP TASK + SOLD HOURS YEAR
// ACCUMULATION" §2: Horas Vendidas, sin Mes explícito seleccionado, debe
// usar el AÑO COMPLETO como horizonte (Oct/Nov/Dic del año en curso NO
// deben quedar excluidos solo por ser "futuro" respecto a "hoy").
//
// Owner ticket "HORAS VENDIDAS — ENABLE FUTURE MONTH SELECTION" (follow-up):
// el fix anterior dejaba el cap operacional VIGENTE apenas el usuario
// tildaba un Mes explícito futuro (pill deshabilitado + valor `null` pese
// a la selección) — Horas Vendidas es un compromiso YA VENDIDO/proyectado,
// el mes SELECCIONADO es SIEMPRE autoritativo acá, sin excepción. Este
// harness reemplaza el invariante viejo ("Mes explícito futuro conserva el
// cap") por el nuevo contrato correcto, y agrega A-J completos.
//
// Carga reporting.js REAL en un sandbox mínimo (MISMO patrón que
// reporting_capacidad_no_asignadas_tarea.node.js), con un `Date` FIJO
// (2026-09-08, "hoy" = Septiembre) para que el test sea determinístico
// sin importar cuándo se ejecute realmente.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

// `Date` fijo: SOLO el constructor sin argumentos ("hoy") se fija a
// 2026-09-08 (Septiembre) -- toda otra forma (`new Date(2026, 0, 1)`, etc.)
// sigue delegando al Date real, sin perder ninguna otra semántica del archivo.
const RealDate = Date;
class FixedNowDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(2026, 8, 8); // mes 0-based: 8 = Septiembre
    else super(...args);
  }
  static now() { return new RealDate(2026, 8, 8).getTime(); }
}

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  dwGraphToken: async () => "",
  HubForegroundProgress: { begin() {}, complete() {}, fail() {}, settle() {} },
  setInterval() { return 0; },
  clearInterval() {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  Date: FixedNowDate,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  horasVendidasSelectedRows, sumMonthly, horasVendidasMesPills, rentabilidadMesPills,
  pruneHorasVendidasMes, reconcileExperienceFacets, _mesValidoParaAlgunAnio,
} = sandbox;
const state = vm.runInContext("state", sandbox);
const HORASVENDIDAS_ROWS = vm.runInContext("HORASVENDIDAS_ROWS", sandbox);
const RENTABILIDAD_PROYECTOS = vm.runInContext("RENTABILIDAD_PROYECTOS", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
function assertDeepEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { failures++; console.log(`FAIL: ${label} — expected ${e}, got ${a}`); }
}

// Fixture del ticket: año 2026, Sep/Oct/Nov/Dic con valores reales (más
// Enero para probar mes pasado), resto del año en 0 (genuino, sin dato).
// DOS filas -- una "Proyecto", una "Mantenimiento" (H: ambas obedecen la
// MISMA regla, misma función `horasVendidasSelectedRows` sin ramificar por
// tipo) -- y una tercera fila de otro proyecto para probar la agregación
// "Proyecto" (J/N del ticket anterior).
//   Índices 0..11 = Ene..Dic.
function seedRows() {
  HORASVENDIDAS_ROWS.length = 0;
  HORASVENDIDAS_ROWS.push(
    { cp: "María López", tipo: "Proyecto", cliente: "AySA", proyecto: "Proyecto Uno", estado: "En curso",
      vendidasByYear: { "2026": [10, 0, 0, 0, 0, 0, 0, 0, 5, 7, 3, 2] },
      monthly: [10, 0, 0, 0, 0, 0, 0, 0, 5, 7, 3, 2], fecha: "2026-01-01" },
    { cp: "Diego Ramos", tipo: "Proyecto", cliente: "YPF", proyecto: "Proyecto Dos", estado: "En curso",
      vendidasByYear: { "2026": [0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0] },
      monthly: [0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0], fecha: "2026-10-01" },
    // H: Mantenimiento -- MISMA fila-shape, mismo `_yearRangeMonthly`, sin
    // rama especial por tipo en `horasVendidasSelectedRows`.
    { cp: "María López", tipo: "Mantenimiento", cliente: "Andesmar", proyecto: "Mantenimiento Costeo", estado: "En curso",
      vendidasByYear: { "2026": [0, 0, 0, 0, 0, 0, 0, 0, 6, 8, 9, 5] },
      monthly: [0, 0, 0, 0, 0, 0, 0, 0, 6, 8, 9, 5], fecha: "2026-09-01" },
  );
}

function resetState() {
  state.horasVendidasStatus = { "En curso": true, "Finalizado": false };
  state.filters.horasvendidas = {};
  state.horasVendidasAnios = new Set(["2026"]);
  state.horasVendidasMeses = new Set();
}

async function main() {
  seedRows();
  resetState();

  // ── A: los controles de Mes (pills) de Oct/Nov/Dic están HABILITADOS ──
  {
    const html = horasVendidasMesPills();
    // Los 3 <button> de Oct/Nov/Dic (índices 10/11/12 en la fila de meses,
    // MESES = ["Ene",...,"Dic"]) no deben llevar el atributo `disabled`.
    const buttonRe = label => new RegExp(`<button[^>]*>${label}</button>`);
    ["Oct", "Nov", "Dic"].forEach(label => {
      const m = html.match(buttonRe(label));
      assertTrue(!!m, `A: setup — el pill de ${label} existe en el HTML`);
      if (m) assertFalse(m[0].includes("disabled"), `A: el pill de ${label} está HABILITADO (sin atributo disabled)`);
    });
  }

  // ── B/C/D + E: seleccionar Oct/Nov/Dic EXPLÍCITAMENTE filtra a ESE mes
  //    con su valor real proyectado/vendido (nunca null, nunca 0 fabricado) ──
  {
    state.horasVendidasMeses = new Set(["10"]);
    const p1 = horasVendidasSelectedRows().find(r => r.proyecto === "Proyecto Uno");
    assertEqual(p1.monthly[9], 7, "B: seleccionar Octubre -- filtra a Octubre, valor real 7 (nunca null)");
    assertEqual(p1.monthly[0], 0, "B: seleccionar Octubre -- Enero (no seleccionado) enmascarado a 0");
  }
  {
    state.horasVendidasMeses = new Set(["11"]);
    const p1 = horasVendidasSelectedRows().find(r => r.proyecto === "Proyecto Uno");
    assertEqual(p1.monthly[10], 3, "C: seleccionar Noviembre -- filtra a Noviembre, valor real 3 (nunca null)");
  }
  {
    state.horasVendidasMeses = new Set(["12"]);
    const p1 = horasVendidasSelectedRows().find(r => r.proyecto === "Proyecto Uno");
    assertEqual(p1.monthly[11], 2, "D: seleccionar Diciembre -- filtra a Diciembre, valor real 2 (nunca null)");
  }

  // ── F: sin Mes seleccionado sigue siendo Ene-Dic completo ──
  {
    state.horasVendidasMeses = new Set();
    const p1 = horasVendidasSelectedRows().find(r => r.proyecto === "Proyecto Uno");
    assertDeepEqual(p1.monthly, [10, 0, 0, 0, 0, 0, 0, 0, 5, 7, 3, 2],
      "F: sin Mes -- Proyecto Uno conserva Ene..Dic completo, Oct/Nov/Dic incluidos");
  }

  // ── G: seleccionar un mes futuro NUNCA dispara el truncamiento de "mes
  //    actual" -- reemplaza el invariante viejo (que esperaba `null`) ──
  {
    state.horasVendidasMeses = new Set(["10", "11", "12"]);
    const p1 = horasVendidasSelectedRows().find(r => r.proyecto === "Proyecto Uno");
    assertEqual(p1.monthly[9], 7, "G: Oct/Nov/Dic tildados juntos -- Octubre real, sin truncar");
    assertEqual(p1.monthly[10], 3, "G: Oct/Nov/Dic tildados juntos -- Noviembre real, sin truncar");
    assertEqual(p1.monthly[11], 2, "G: Oct/Nov/Dic tildados juntos -- Diciembre real, sin truncar");
    assertTrue(p1.monthly.every(v => v !== null), "G: ningún mes queda en null por el cap operacional de 'hoy'");
  }

  // ── H: Mantenimiento obedece la MISMA regla que Proyecto ──
  {
    state.horasVendidasMeses = new Set(["11"]);
    const mant = horasVendidasSelectedRows().find(r => r.proyecto === "Mantenimiento Costeo");
    assertEqual(mant.monthly[10], 9, "H: Mantenimiento -- seleccionar Noviembre filtra a Noviembre, valor real 9");
    state.horasVendidasMeses = new Set();
    const mantFull = horasVendidasSelectedRows().find(r => r.proyecto === "Mantenimiento Costeo");
    assertDeepEqual(mantFull.monthly, [0, 0, 0, 0, 0, 0, 0, 0, 6, 8, 9, 5],
      "H: Mantenimiento -- sin Mes, año completo (Sep=6/Oct=8/Nov=9/Dic=5) igual que Proyecto");
  }

  // ── J: mes futuro SIN dato en la fuente permanece en 0 genuino, nunca
  //    fabricado (Proyecto Dos no tiene nada en Nov/Dic) ──
  {
    state.horasVendidasMeses = new Set();
    const p2 = horasVendidasSelectedRows().find(r => r.proyecto === "Proyecto Dos");
    assertEqual(p2.monthly[10], 0, "J: Noviembre sin dato en la fuente -- 0 genuino, no fabricado");
    assertEqual(p2.monthly[11], 0, "J: Diciembre sin dato en la fuente -- 0 genuino, no fabricado");
  }

  // ── pruneHorasVendidasMes: un mes futuro SELECCIONADO nunca se poda al
  //    reconciliar facets (cambiar Año, tocar Estado, etc.) ──
  {
    state.horasVendidasMeses = new Set(["10", "11", "12"]);
    pruneHorasVendidasMes();
    assertDeepEqual([...state.horasVendidasMeses].sort(), ["10", "11", "12"],
      "pruneHorasVendidasMes: Oct/Nov/Dic tildados SOBREVIVEN la poda (nunca se retiran por ser futuro)");
    // Vía el flujo real (reconcileExperienceFacets -- lo que se dispara al
    // cambiar Año en la UI): MISMO resultado, cambiar de año NO elimina un
    // mes tildado (todo año tiene los 12 meses como horizonte de reporte
    // válido en Horas Vendidas -- no existe "mes fuera del año elegido").
    state.horasVendidasAnios = new Set(["2025"]);
    reconcileExperienceFacets("horasvendidas");
    assertDeepEqual([...state.horasVendidasMeses].sort(), ["10", "11", "12"],
      "pruneHorasVendidasMes vía reconcile: cambiar de Año NO retira Oct/Nov/Dic (todo año tiene 12 meses válidos en HV)");
    state.horasVendidasAnios = new Set(["2026"]);
    state.horasVendidasMeses = new Set();
  }

  // ── I: OTRAS pestañas de Reporting CONSERVAN su protección de mes futuro
  //    (Rentabilidad, MISMA `_mesValidoParaAlgunAnio` de siempre) ──
  {
    RENTABILIDAD_PROYECTOS.length = 0;
    RENTABILIDAD_PROYECTOS.push({ cliente: "C", proyecto: "P", cp: "X", tipo: "Proyecto", estado: "En curso",
      certByYear: { "2026": Array(12).fill(0) }, trabByYear: { "2026": Array(12).fill(0) }, vendByYear: { "2026": Array(12).fill(0) } });
    state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
    state.filters.rentabilidad = {};
    state.rentabilidadAnios = new Set(["2026"]);
    const html = rentabilidadMesPills();
    const octMatch = html.match(/<button[^>]*>Oct<\/button>/);
    assertTrue(!!octMatch, "I: setup -- el pill de Octubre existe en Rentabilidad");
    if (octMatch) assertTrue(octMatch[0].includes("disabled"), "I: Rentabilidad SIGUE deshabilitando Octubre (año en curso) -- protección intacta, no tocada por este ticket");
    assertFalse(_mesValidoParaAlgunAnio(new Set(["2026"]), 10), "I: _mesValidoParaAlgunAnio (motor compartido) sigue rechazando Octubre-2026 -- NUNCA alterado globalmente");
  }

  if (failures > 0) {
    console.log(`${failures} Horas Vendidas full-year invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL HORAS VENDIDAS FULL-YEAR INVARIANTS PASSED");
  process.exit(0);
}

main();
