// reporting_frecuencia_order.node.js — invocado por
// test_reporting_frecuencia_order.py. Owner live comparison vs PBIP:
// "tabla frecuencia no ordena por ID Frecuencia igual al PBIP (que ordena
// por ID donde ademas es cronologico Mensual/Bimestral/Trimestral/
// Cuatrimestral/Semestral/Anual) mientras que ... estamos ordenandolo sin
// criterio". Evidencia PRIMARIA (Cliente.tmdl, columna 'ID Frecuencia'):
//   SWITCH(Frecuencia Actualizacion, "Mensual",1, "Bimestral",2,
//     "Trimestral",3, "Cuatrimestral",4, "Semestral",5, "Anual",6,
//     "7  Sin especificar")
// La lista previa (FRECUENCIA_ORDER) tenía "Semanal" en el índice 2 — un
// valor que ni existe en el SWITCH real. Carga reporting.js en un sandbox
// mínimo y ejecuta el MOTOR REAL (distinctValues/_frecuenciaCompare) contra
// fixtures sintéticas.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {} },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { distinctValues, _frecuenciaCompare } = sandbox;
const FRECUENCIA_ORDER = vm.runInContext("FRECUENCIA_ORDER", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}

// --- FRECUENCIA_ORDER es la cronología REAL del PBIP — "Semanal" nunca
// --- existió en el SWITCH real, "Bimestral" es el índice 2 correcto ------
assertEqual(FRECUENCIA_ORDER, ["Mensual", "Bimestral", "Trimestral", "Cuatrimestral", "Semestral", "Anual"], "FRECUENCIA_ORDER reproduce EXACTO el SWITCH de Cliente.tmdl 'ID Frecuencia' — nunca 'Semanal'");

// --- Filas desordenadas (fixture, insertadas SIN criterio a propósito)
// --- deben ordenar cronológico, nunca alfabético ni por orden de inserción --
{
  const rows = [
    { frecuencia: "Anual" }, { frecuencia: "Semestral" }, { frecuencia: "Trimestral" },
    { frecuencia: "Mensual" }, { frecuencia: "Cuatrimestral" }, { frecuencia: "Bimestral" },
  ];
  const ordered = distinctValues(rows, "frecuencia", _frecuenciaCompare);
  assertEqual(ordered, ["Mensual", "Bimestral", "Trimestral", "Cuatrimestral", "Semestral", "Anual"], "distinctValues ordena por ID Frecuencia (cronológico), pese a que las filas de entrada estaban en orden arbitrario");
}

// --- Un valor NO reconocido (fuera del SWITCH real) ordena AL FINAL,
// --- NUNCA primero (indexOf(-1) sin guard rompía esto) --------------------
{
  const rows = [{ frecuencia: "Sin especificar" }, { frecuencia: "Mensual" }, { frecuencia: "Anual" }];
  const ordered = distinctValues(rows, "frecuencia", _frecuenciaCompare);
  assertEqual(ordered, ["Mensual", "Anual", "Sin especificar"], "un valor fuera de las 6 categorías canónicas cae AL FINAL (mismo criterio que '7  Sin especificar' del PBIP), nunca al principio");
}

if (failures > 0) {
  console.log(`${failures} frecuencia order invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL FRECUENCIA ORDER INVARIANTS PASSED");
