// hub_estimated_progress_primitive.node.js — invocado por
// test_hub_estimated_progress.py::test_hub_estimated_progress_primitive_invariants_hold.
// Ejecuta el `HubEstimatedProgress` REAL extraído de app.js (no un mock de su
// lógica) contra un `Date.now`/`localStorage` controlados — prueba el
// contrato de UX real: checkpoints (nunca 100% solo por tiempo, tope ~94%),
// overrun (permanece <=95% con `overrun=true`), EWMA (0.70 previo + 0.30
// última duración real, persistida por `key` — nunca por tab), y que un
// `finishError` NUNCA graba estimate. Mismo patrón que
// reporting_initial_load_contract.node.js (vm.createContext + código REAL).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "app.js");
const src = fs.readFileSync(APP_JS, "utf-8");
const start = src.indexOf("const HubEstimatedProgress = (() => {");
const closeMarker = "\n})();";
const end = src.indexOf(closeMarker, start) + closeMarker.length;
if (start === -1 || end === -1) {
  console.log("FAIL: no se encontró el bloque HubEstimatedProgress en app.js");
  process.exit(1);
}
const snippet = src.slice(start, end);

let fakeNow = 0;
const store = {};
const sandbox = {
  Date: { now: () => fakeNow },
  localStorage: {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
  },
  console,
};
vm.createContext(sandbox);
vm.runInContext(snippet, sandbox, { filename: "HubEstimatedProgress.js" });
// `const` de top-level NO se adjunta como propiedad del objeto sandbox (quirk
// conocido de `vm`, a diferencia de `function`/`var`) — se lee evaluando la
// referencia DENTRO del contexto, mismo patrón que `state` en
// reporting_initial_load_contract.node.js.
const HubEstimatedProgress = vm.runInContext("HubEstimatedProgress", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
function assertEqual(a, b, label) {
  assertTrue(a === b, `${label} (got ${a}, expected ${b})`);
}

// ============================================================================
// 1) Checkpoints — inicio ~5-8%, 25/50/75% del estimado -> ~35/60/80%, fin del
//    estimado -> ~94% (NUNCA 100 solo por tiempo).
// ============================================================================
fakeNow = 0;
const c1 = HubEstimatedProgress.start("k-checkpoints", { seedMs: 10000 });
assertEqual(c1.tick().percent, 6, "start: ~5-8% al comenzar");
fakeNow = 2500;
assertEqual(c1.tick().percent, 35, "25% del estimado -> ~35%");
fakeNow = 5000;
assertEqual(c1.tick().percent, 60, "50% del estimado -> ~60%");
fakeNow = 7500;
assertEqual(c1.tick().percent, 80, "75% del estimado -> ~80%");
fakeNow = 10000;
const atEstimate = c1.tick();
assertEqual(atEstimate.percent, 94, "fin del estimado -> ~94%, nunca 100 solo por tiempo");
assertTrue(!atEstimate.overrun, "exactamente en el borde del estimado todavía no es overrun");

// ============================================================================
// 2) Overrun — superado el estimado, permanece <=95% con overrun=true (el
//    caller es quien pinta el sweep continuo, ver reporting.js).
// ============================================================================
fakeNow = 25000;
const overrunTick = c1.tick();
assertTrue(overrunTick.overrun, "overrun=true tras superar el estimado");
assertTrue(overrunTick.percent <= 95, "overrun nunca sube de ~95%");
assertEqual(overrunTick.percent, 94, "overrun se queda clavado en el tope (~94%), no sigue subiendo con el tiempo");

// ============================================================================
// 3) EWMA — finishSuccess graba 0.70*previo + 0.30*duración real, por `key`.
// ============================================================================
fakeNow = 0;
const c3 = HubEstimatedProgress.start("k-ewma", { seedMs: 10000 });
fakeNow = 4000;
c3.finishSuccess();
const storedRaw = store["dw-hub-progress-estimate:k-ewma"];
assertTrue(storedRaw !== undefined, "finishSuccess persiste el estimate en localStorage");
assertEqual(parseFloat(storedRaw), 10000 * 0.70 + 4000 * 0.30, "EWMA grabada con la fórmula exacta del owner");

// La SIGUIENTE corrida con la MISMA key usa el estimate aprendido, ignorando
// cualquier seed nuevo (los seeds son SOLO el arranque en frío).
fakeNow = 0;
const c4 = HubEstimatedProgress.start("k-ewma", { seedMs: 999999 });
fakeNow = parseFloat(storedRaw);
assertEqual(c4.tick().percent, 94, "la siguiente corrida usa el estimate aprendido, no el seed");

// ============================================================================
// 4) finishError NUNCA graba estimate — una duración hasta un error no es
//    evidencia de cuánto tarda un fetch exitoso.
// ============================================================================
fakeNow = 0;
const c5 = HubEstimatedProgress.start("k-error", { seedMs: 5000 });
fakeNow = 9999;
c5.finishError();
assertTrue(
  !Object.prototype.hasOwnProperty.call(store, "dw-hub-progress-estimate:k-error"),
  "finishError no persiste ningún estimate",
);

// ============================================================================
// 5) finishSuccess es idempotente — llamarlo 2 veces no vuelve a promediar.
// ============================================================================
fakeNow = 0;
const c6 = HubEstimatedProgress.start("k-idempotent", { seedMs: 10000 });
fakeNow = 2000;
c6.finishSuccess();
const afterFirst = store["dw-hub-progress-estimate:k-idempotent"];
fakeNow = 8000;
c6.finishSuccess();
assertEqual(store["dw-hub-progress-estimate:k-idempotent"], afterFirst, "finishSuccess repetido no vuelve a grabar");

// ============================================================================
// 6) Dos keys distintas nunca se pisan (Comercial/Calendario comparten key
//    "batch-a" A PROPÓSITO en reporting.js — pero dos fuentes DISTINTAS deben
//    aprender estimates independientes).
// ============================================================================
fakeNow = 0;
const cA = HubEstimatedProgress.start("k-a", { seedMs: 8000 });
const cB = HubEstimatedProgress.start("k-b", { seedMs: 8000 });
fakeNow = 1000;
cA.finishSuccess();
assertTrue(
  !Object.prototype.hasOwnProperty.call(store, "dw-hub-progress-estimate:k-b"),
  "grabar el estimate de una key nunca toca el estimate de otra key",
);
cB.finishError();

if (failures > 0) {
  console.log(`${failures} HubEstimatedProgress primitive invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB ESTIMATED PROGRESS PRIMITIVE INVARIANTS PASSED");
process.exit(0);
