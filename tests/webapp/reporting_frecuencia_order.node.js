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

function makeEl() { return { innerHTML: "" }; }
const sandbox = {
  document: { addEventListener() {}, getElementById() { return makeEl(); } },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  initDashboardSections() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { distinctValues, _frecuenciaCompare, buildTree, renderCalendario } = sandbox;
const FRECUENCIA_ORDER = vm.runInContext("FRECUENCIA_ORDER", sandbox);
const state = vm.runInContext("state", sandbox);
const CALENDARIO_ROWS = vm.runInContext("CALENDARIO_ROWS", sandbox);

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

// --- REGRESIÓN CENTRAL (owner: "sigo sin ver el orden en la tabla"): el
// --- fix anterior SOLO reordenaba las OPCIONES del <select> de Frecuencia
// --- (`distinctValues`, arriba) — nunca los GRUPOS de la TABLA en sí, que
// --- `buildTree` arma por orden de PRIMERA APARICIÓN en las filas, ciego a
// --- cualquier criterio de negocio salvo que se le pase un comparator
// --- explícito. Prueba `buildTree` DIRECTO (el motor real de la tabla, no
// --- un proxy) con filas insertadas en orden ARBITRARIO. -------------------
{
  const rows = [
    { frecuencia: "Semestral", cliente: "A" }, { frecuencia: "Anual", cliente: "B" },
    { frecuencia: "Trimestral", cliente: "C" }, { frecuencia: "Mensual", cliente: "D" },
  ];
  const withoutComparator = buildTree(rows, ["frecuencia"]);
  assertEqual(withoutComparator.map(n => n.label), ["Semestral", "Anual", "Trimestral", "Mensual"], "SIN comparator, buildTree preserva orden de inserción (precondición — así es como se veía el bug: 'sin criterio')");

  const withComparator = buildTree(rows, ["frecuencia"], [], { frecuencia: _frecuenciaCompare });
  assertEqual(withComparator.map(n => n.label), ["Mensual", "Trimestral", "Semestral", "Anual"], "CON comparator, buildTree ordena los GRUPOS de la tabla por ID Frecuencia real — el fix que faltaba");
}

// --- El comparator funciona en CUALQUIER profundidad de la jerarquía —
// --- perspectiva "Client Partner" tiene Frecuencia en el nivel 3 (cp >
// --- cliente > frecuencia), no en la raíz -----------------------------------
{
  const rows = [
    { cp: "X", cliente: "Uno", frecuencia: "Anual" },
    { cp: "X", cliente: "Uno", frecuencia: "Mensual" },
    { cp: "X", cliente: "Uno", frecuencia: "Trimestral" },
  ];
  const tree = buildTree(rows, ["cp", "cliente", "frecuencia"], [], { frecuencia: _frecuenciaCompare });
  const frecuenciaNodes = tree[0].children[0].children.map(n => n.label);
  assertEqual(frecuenciaNodes, ["Mensual", "Trimestral", "Anual"], "el comparator ordena Frecuencia aunque esté anidada en el nivel 3 (perspectiva Client Partner), no solo en la raíz");
}

// --- End-to-end: renderCalendario() real usa el comparator — la tabla
// --- efectivamente renderizada respeta la cronología ------------------------
{
  CALENDARIO_ROWS.length = 0;
  CALENDARIO_ROWS.push(
    { frecuencia: "Semestral", cliente: "Cliente Semestral", cp: "CP1", monthly: [1,0,0,0,0,0,1,0,0,0,0,0] },
    { frecuencia: "Anual", cliente: "Cliente Anual", cp: "CP1", monthly: [0,0,0,0,0,0,0,0,0,0,0,1] },
    { frecuencia: "Mensual", cliente: "Cliente Mensual", cp: "CP1", monthly: [1,1,1,1,1,1,1,1,1,1,1,1] },
    { frecuencia: "Trimestral", cliente: "Cliente Trimestral", cp: "CP1", monthly: [1,0,0,1,0,0,1,0,0,1,0,0] },
  );
  state.filters.calendario = {};
  state.perspective.calendario = "Frecuencia";
  state.batchADataStatus = "ready";

  const html = renderCalendario();
  const positions = ["Mensual", "Trimestral", "Semestral", "Anual"].map(label => html.indexOf(`>${label}<`));
  assertTrue(positions.every(p => p !== -1), "las 4 etiquetas de Frecuencia aparecen en el HTML renderizado");
  const inOrder = positions.every((p, i) => i === 0 || positions[i - 1] < p);
  assertTrue(inOrder, "renderCalendario() real renderiza los grupos de Frecuencia en orden cronológico (Mensual→Bimestral→Trimestral→Cuatrimestral→Semestral→Anual), pese a que CALENDARIO_ROWS los tenía en orden arbitrario");
}

if (failures > 0) {
  console.log(`${failures} frecuencia order invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL FRECUENCIA ORDER INVARIANTS PASSED");
