// reporting_temporal_contract.node.js — invocado por
// test_reporting_temporal_contract.py (mismo patrón que
// reporting_rollup_reconciliation.node.js): carga reporting.js en un
// sandbox mínimo y ejecuta el TEMPORAL DOMAIN CONTRACT REAL (owner live
// QA — "Septiembre" en el <select> de Año de Horas Vendidas, current-year
// future-month leakage en la matriz/quarters/totales).
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
  _isValidAnioValue, _operationalMonthCap, _applyMonthCap, _sumMonthlyArrays,
  _diffMonthly, _quarterValue, _validMesOptions,
} = sandbox;
// `MESES_LARGOS` es `const` a nivel de módulo — no se adjunta como
// propiedad del objeto de contexto (a diferencia de `function`/`var`, ver
// MISMO caveat en reporting_rentabilidad_ranking.node.js para `state`).
const MESES_LARGOS = vm.runInContext("MESES_LARGOS", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}

const now = new Date();
const CURRENT_YEAR = String(now.getFullYear());
const CURRENT_MONTH = now.getMonth() + 1; // 1-based
const HISTORICAL_YEAR = String(now.getFullYear() - 1);

// --- YEAR DOMAIN: solo 4 dígitos, "Septiembre" NUNCA es un año válido. ---
{
  assertTrue(_isValidAnioValue("2026") === true, "2026 es un año válido");
  assertTrue(_isValidAnioValue("Septiembre") === false, "'Septiembre' NUNCA es un año válido (root cause del owner)");
  assertTrue(_isValidAnioValue("") === false, "string vacío no es un año válido");
  assertTrue(_isValidAnioValue("Todos") === false, "'Todos' no es un año válido (pertenece al dominio de Mes)");
  assertTrue(_isValidAnioValue("202") === false, "3 dígitos no es un año válido");
  assertTrue(_isValidAnioValue("20266") === false, "5 dígitos no es un año válido");
}

// --- MONTH DOMAIN: MESES_LARGOS tiene exactamente los 12 meses fijos. ---
{
  assertEqual(MESES_LARGOS.length, 12, "MESES_LARGOS tiene exactamente 12 meses");
  assertTrue(MESES_LARGOS.includes("Septiembre"), "Septiembre SÍ es un mes válido (dominio correcto)");
}

// --- CURRENT-YEAR FUTURE-MONTH CAP ---
{
  assertEqual(_operationalMonthCap(CURRENT_YEAR), CURRENT_MONTH, "el cap del año actual es el mes calendario real");
  assertTrue(_operationalMonthCap(HISTORICAL_YEAR) === null, "un año histórico NUNCA tiene cap");
  assertTrue(_operationalMonthCap("2099") === null, "un año futuro-no-soportado tampoco tiene cap (no es 'el año actual')");
}

// --- _applyMonthCap: índices >= cap quedan null, nunca 0. ---
{
  const arr = Array.from({ length: 12 }, (_, i) => i + 1); // [1..12]
  const capped = _applyMonthCap(arr, 8); // Agosto: índices 0..7 válidos
  assertEqual(capped.slice(0, 8), [1, 2, 3, 4, 5, 6, 7, 8], "meses hasta el cap (inclusive) preservan su valor real");
  assertTrue(capped.slice(8).every(v => v === null), "meses posteriores al cap son null, NUNCA 0 (owner: 0 afirmaría una medición real)");
  assertEqual(_applyMonthCap(arr, null), arr, "cap null/falsy = passthrough sin cambios (año histórico)");
}

// --- _sumMonthlyArrays: NULL-AWARE — un índice queda null SOLO si TODAS las filas están capadas ahí. ---
{
  const rowsAllCapped = [
    _applyMonthCap([5, 5, 5, 5], 2),
    _applyMonthCap([3, 3, 3, 3], 2),
  ];
  const total = _sumMonthlyArrays(rowsAllCapped);
  assertEqual(total.slice(0, 2), [8, 8], "meses NO capados suman normal (5+3=8)");
  assertTrue(total.slice(2).every(v => v === null), "un mes capado en TODAS las filas preserva null en el total (nunca colapsa a 0)");
}
{
  // Mezcla: una fila capada, otra NO (ej. proyecto histórico sin cap conviviendo
  // con uno del año actual en el MISMO índice — caso límite, pero el contrato
  // dice: CUALQUIER contribución real gana sobre null).
  const mixed = [_applyMonthCap([5, 5], 1), [3, 3]];
  const total = _sumMonthlyArrays(mixed);
  assertEqual(total.slice(0, 2), [8, 3], "si UNA fila aporta valor real en un índice, el total NO es null ahí (5+null=5 en idx0 ya cuenta, luego +3; idx1: null+3=3)");
}

// --- _diffMonthly: null si CUALQUIERA de los dos operandos está capado. ---
{
  const a = _applyMonthCap([10, 10], 1);
  const b = _applyMonthCap([4, 4], 1);
  const diff = _diffMonthly(a, b);
  assertEqual(diff[0], 6, "diff normal cuando ambos tienen valor real");
  assertTrue(diff[1] === null, "diff es null si CUALQUIERA de los dos está capado (no se puede calcular margen de un período no aplicable)");
}

// --- _quarterValue: trimestre ENTERO capado -> null ("no contribution"). Parcial -> suma lo válido. ---
{
  const totalMonthly = _applyMonthCap([10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10], 8); // cap=Agosto
  assertEqual(_quarterValue(totalMonthly, 0), 30, "Q1 (Ene-Mar) totalmente antes del cap: suma normal");
  assertEqual(_quarterValue(totalMonthly, 1), 30, "Q2 (Abr-Jun) totalmente antes del cap: suma normal");
  assertEqual(_quarterValue(totalMonthly, 2), 20, "Q3 (Jul-Ago) parcialmente capado: Jul+Ago=20, Sep excluido (owner: Q3=Jul+Ago, NUNCA Jul+Ago+Sep)");
  assertTrue(_quarterValue(totalMonthly, 3) === null, "Q4 (Oct-Dic) TOTALMENTE capado: null, 'no contribution/not applicable' — NUNCA un 0 numérico");
}

// --- _validMesOptions: año actual omite meses futuros; año histórico ofrece los 12. ---
{
  const opts = _validMesOptions(CURRENT_YEAR);
  assertEqual(opts.length, CURRENT_MONTH, "año actual: SOLO los meses hasta el actual (owner: nunca ofrecer un mes futuro como opción)");
  assertTrue(!opts.some(m => m.label === "Septiembre") || CURRENT_MONTH >= 9, "si el mes actual es anterior a Septiembre, Septiembre NUNCA aparece como opción");

  const histOpts = _validMesOptions(HISTORICAL_YEAR);
  assertEqual(histOpts.length, 12, "año histórico: los 12 meses completos, source-driven, SIN cap");
}

if (failures > 0) {
  console.log(`${failures} temporal contract invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL TEMPORAL CONTRACT INVARIANTS PASSED");
process.exit(0);
