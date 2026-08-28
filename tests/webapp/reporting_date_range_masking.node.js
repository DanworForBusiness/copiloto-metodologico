// reporting_date_range_masking.node.js — invocado por
// test_reporting_date_range_masking.py::test_month_masking_invariants_hold
// (via `node <este archivo>`), mismo patrón que
// reporting_rollup_reconciliation.node.js (QA4): carga reporting.js en un
// sandbox mínimo y ejecuta el MOTOR REAL (monthsInRange/maskMonthly/
// sumMonthly) contra fixtures sintéticos — prueba la aritmética real de
// enmascarado mensual, no solo presencia de un patrón de código en el
// source (Q2, owner live QA: "Rentabilidad con Hasta=agosto seguía
// mostrando Sep-Oct-Nov-Dic no-cero").
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  // `HubForegroundProgress` tambien vive en app.js. Antes de la Fase 1A este
  // harness no lo alcanzaba: el token se leia sincronicamente, asi que para
  // cuando corria `render()` el fetch stub ya habia resuelto y el status era
  // "ready". El `await` de la autoridad agrega un tick, asi que ahora se
  // ejercita la rama CORRECTA — la barra de progreso mientras carga.
  HubForegroundProgress: { begin() { }, complete() { }, fail() { }, settle() { } },
  // Los timers del browser: mismo motivo que el stub de arriba — la barra
  // compartida arranca su sweep con `setInterval`, y esta rama recien se
  // ejercita desde la Fase 1A. No-op: el sweep no es lo que se prueba aca.
  setInterval() { return 0; },
  clearInterval() { },
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { monthsInRange, maskMonthly, sumMonthly } = sandbox;

let failures = 0;
function assertDeepEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}

// --- Test 1: sin filtro activo (Desde/Hasta vacíos) — los 12 meses incluidos. ---
{
  const mask = monthsInRange("", "");
  assertDeepEqual(mask, Array(12).fill(true), "sin filtro: los 12 meses incluidos (comportamiento Todos)");
}

// --- Test 2: reproduce el bug reportado — Desde=01/01/2026 Hasta=14/08/2026
// (grano MENSUAL: el día 14 redondea al mes de Agosto completo) — Ene..Ago
// incluidos, Sep..Dic excluidos. ---
{
  const mask = monthsInRange("2026-01-01", "2026-08-14");
  assertDeepEqual(
    mask,
    [true, true, true, true, true, true, true, true, false, false, false, false],
    "Desde=ene Hasta=ago: Sep-Oct-Nov-Dic excluidos (el bug reportado por el owner)",
  );
}

// --- Test 3: rango dentro de un mismo mes — ese único mes incluido. ---
{
  const mask = monthsInRange("2026-03-05", "2026-03-20");
  assertDeepEqual(
    mask,
    [false, false, true, false, false, false, false, false, false, false, false, false],
    "rango dentro de marzo: solo marzo incluido",
  );
}

// --- Test 4: rango cruzando 2 años calendario — unión de meses tocados en
// AMBOS años (la matriz no tiene eje de Año, ver comentario del código). ---
{
  const mask = monthsInRange("2025-11-01", "2026-02-28");
  assertDeepEqual(
    mask,
    [true, true, false, false, false, false, false, false, false, false, true, true],
    "cruce de año Nov 2025 - Feb 2026: Ene/Feb/Nov/Dic incluidos, Mar-Oct excluidos",
  );
}

// --- Test 5: maskMonthly pone en 0 los meses excluidos, preserva los incluidos. ---
{
  const monthly = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120];
  const mask = [true, true, true, true, true, true, true, true, false, false, false, false];
  const masked = maskMonthly(monthly, mask);
  assertDeepEqual(
    masked,
    [10, 20, 30, 40, 50, 60, 70, 80, 0, 0, 0, 0],
    "maskMonthly: meses excluidos en 0, incluidos intactos, array de 12 preservado (headers Ene-Dic estables)",
  );
  assertDeepEqual(maskMonthly(monthly, undefined), monthly, "maskMonthly sin mask: passthrough exacto (Calendario/Capacidad No Asignadas)");
}

// --- Test 6: sumMonthly con máscara — reproduce el caso real de Horas
// Vendidas: una fila con datos reales Ene-Dic (proyecto activo todo el
// año) que YA pasó el filtro de fila por su `fecha` más temprana (Enero,
// dentro de Desde-Hasta), pero cuyo `monthly` sigue teniendo horas reales
// en Sep-Dic — sumMonthly debe excluirlas del total agregado. ---
{
  const rows = [
    { fecha: "2026-01-01", monthly: [8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8] },
  ];
  const mask = monthsInRange("2026-01-01", "2026-08-14");
  const totals = sumMonthly(rows, mask);
  assertDeepEqual(
    totals,
    [8, 8, 8, 8, 8, 8, 8, 8, 0, 0, 0, 0],
    "sumMonthly enmascarado: fila con datos reales Ene-Dic, Desde-Hasta=ene-ago -> Sep-Dic en 0 en el TOTAL agregado",
  );
  const totalsUnmasked = sumMonthly(rows, undefined);
  assertDeepEqual(
    totalsUnmasked,
    [8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8],
    "sumMonthly sin máscara: comportamiento exacto de siempre (Calendario/Capacidad No Asignadas, sin filtro de fecha)",
  );
}

if (failures > 0) {
  console.log(`${failures} month-masking invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL MONTH-MASKING INVARIANTS PASSED");
process.exit(0);
