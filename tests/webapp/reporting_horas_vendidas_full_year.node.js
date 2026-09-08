// reporting_horas_vendidas_full_year.node.js — invocado por
// test_reporting_horas_vendidas_full_year.py. Owner ticket "URGENT
// REPORTING CORRECTNESS — HNP TASK + SOLD HOURS YEAR ACCUMULATION" §2:
// Horas Vendidas, sin Mes explícito seleccionado, debe usar el AÑO
// COMPLETO como horizonte (Oct/Nov/Dic del año en curso NO deben quedar
// excluidos solo por ser "futuro" respecto a "hoy"). Carga reporting.js
// REAL en un sandbox mínimo (MISMO patrón que
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

const { horasVendidasSelectedRows, sumMonthly } = sandbox;
const state = vm.runInContext("state", sandbox);
const HORASVENDIDAS_ROWS = vm.runInContext("HORASVENDIDAS_ROWS", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
function assertDeepEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { failures++; console.log(`FAIL: ${label} — expected ${e}, got ${a}`); }
}

// Fixture del ticket: año 2026, Ene/Sep/Oct/Nov/Dic con valores reales,
// resto del año en 0 (genuino, sin dato) -- DOS proyectos, para poder
// probar también que la agrupación "Proyecto" (misma población, ver
// renderHorasVendidas) reconcilia igual.
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

  // I + M: Mes SIN seleccionar ("Todos") -- Oct/Nov/Dic (futuro respecto a
  // "hoy" = Septiembre) participan con sus valores REALES, el mes actual
  // (Septiembre) NUNCA actúa como corte implícito.
  {
    const rows = horasVendidasSelectedRows();
    const p1 = rows.find(r => r.proyecto === "Proyecto Uno");
    assertDeepEqual(p1.monthly, [10, 0, 0, 0, 0, 0, 0, 0, 5, 7, 3, 2],
      "I/M: sin Mes, Proyecto Uno conserva Oct=7/Nov=3/Dic=2 (nunca null/0 solo por ser futuro)");
  }

  // J + N: el total anual (todas las filas -- MISMA población que agrupa
  // "Proyecto", ver renderHorasVendidas) reconcilia Ene..Dic incluyendo
  // ambos proyectos.
  {
    const rows = horasVendidasSelectedRows();
    const total = sumMonthly(rows);
    // 10+5+7+3+2 (Proyecto Uno) + 4 (Proyecto Dos, Oct) = 31
    assertEqual(total.reduce((s, v) => s + (v || 0), 0), 31,
      "J/N: total anual (agrupación Proyecto) reconcilia Ene-Dic de AMBOS proyectos, incluido Oct/Nov/Dic");
    assertEqual(total[9], 11, "N: Octubre agregado = 7 (Proyecto Uno) + 4 (Proyecto Dos) = 11");
  }

  // K: seleccionar un Mes explícito PASADO (Enero) preserva el
  // comportamiento de siempre -- valor real en Enero, resto en 0.
  {
    state.horasVendidasMeses = new Set(["1"]);
    const rows = horasVendidasSelectedRows();
    const p1 = rows.find(r => r.proyecto === "Proyecto Uno");
    assertEqual(p1.monthly[0], 10, "K: Mes=Enero explícito -- valor real preservado");
    assertEqual(p1.monthly[7], 0, "K: Mes=Enero explícito -- Agosto no seleccionado, enmascarado a 0");
  }

  // K (bonus): Mes explícito FUTURO (Octubre) preserva el cap operacional
  // de SIEMPRE -- el ticket dice "preservar semántica de Mes explícito",
  // nunca se tocó ese camino.
  {
    state.horasVendidasMeses = new Set(["10"]);
    const rows = horasVendidasSelectedRows();
    const p1 = rows.find(r => r.proyecto === "Proyecto Uno");
    assertEqual(p1.monthly[9], null, "K bonus: Mes=Octubre explícito -- cap operacional de siempre, sin cambios");
  }

  // L: meses futuros SIN dato en la fuente permanecen en 0 genuino, nunca
  // fabricado -- Proyecto Dos no tiene nada en Nov/Dic.
  {
    state.horasVendidasMeses = new Set();
    const rows = horasVendidasSelectedRows();
    const p2 = rows.find(r => r.proyecto === "Proyecto Dos");
    assertEqual(p2.monthly[10], 0, "L: Noviembre sin dato en la fuente -- 0 genuino, no fabricado");
    assertEqual(p2.monthly[11], 0, "L: Diciembre sin dato en la fuente -- 0 genuino, no fabricado");
  }

  if (failures > 0) {
    console.log(`${failures} Horas Vendidas full-year invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL HORAS VENDIDAS FULL-YEAR INVARIANTS PASSED");
  process.exit(0);
}

main();
