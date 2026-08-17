// hub_foreground_progress.node.js — invocado por
// test_hub_estimated_progress.py::test_hub_foreground_progress_boundary_invariants_hold.
// HUB FOREGROUND PROGRESS BOUNDARY (owner corrective — "el loader pertenece
// al Hub, no importa qué vertical la inició"). Ejecuta el `HubForegroundProgress`
// REAL (extraído de app.js junto con `HubEstimatedProgress`, su driver, y los
// listeners htmx de Asignaciones que van justo después en el mismo archivo)
// contra un `Date.now`/`localStorage`/`.read-progress__fill`/`setInterval`
// controlados — mismo patrón que hub_estimated_progress_primitive.node.js y
// reporting_hub_estimated_progress_glue.node.js. Prueba:
//   - begin/complete/fail (idempotencia, EWMA vía finishSuccess, sin grabar
//     en finishError) escriben sobre el nodo REAL del Hub;
//   - un único timer compartido, se auto-limpia cuando no queda ninguna key
//     activa;
//   - el listener htmx real de Asignaciones (`#staffing-rows`) dispara
//     begin/complete/fail correctamente, y NUNCA para un elemento no
//     relacionado (control de que "no toda request Graph es foreground").
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "app.js");
const src = fs.readFileSync(APP_JS, "utf-8");
const start = src.indexOf("const HubEstimatedProgress = (() => {");
if (start === -1) {
  console.log("FAIL: no se encontró HubEstimatedProgress en app.js");
  process.exit(1);
}
// Hasta EOF — HubEstimatedProgress + HubForegroundProgress + los 2 listeners
// htmx de Asignaciones son, en ese orden, el resto del archivo (ver app.js).
const snippet = src.slice(start);

let fakeNow = 0;
const store = {};
const fill = { style: { width: "" } };
const listeners = {};  // eventName -> [callback, ...]
let intervalCallback = null;
let intervalCleared = false;

const sandbox = {
  Date: { now: () => fakeNow },
  localStorage: {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
  },
  document: {
    querySelector(sel) { return sel === ".read-progress__fill" ? fill : null; },
    body: {
      addEventListener(name, cb) { (listeners[name] = listeners[name] || []).push(cb); },
    },
  },
  setInterval(fn) { intervalCallback = fn; intervalCleared = false; return 1; },
  clearInterval() { intervalCallback = null; intervalCleared = true; },
  console,
};
vm.createContext(sandbox);
vm.runInContext(snippet, sandbox, { filename: "HubForegroundProgress.js" });
// `const` de top-level no se adjunta como propiedad del sandbox (quirk de
// `vm`) — se lee evaluando la referencia DENTRO del contexto.
const HubForegroundProgress = vm.runInContext("HubForegroundProgress", sandbox);

function dispatch(name, detail) {
  (listeners[name] || []).forEach(cb => cb({ detail }));
}
function fakeElt(matches) {
  return { closest(sel) { return matches.includes(sel) ? this : null; } };
}

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
function assertEqual(a, b, label) { assertTrue(a === b, `${label} (got ${JSON.stringify(a)}, expected ${JSON.stringify(b)})`); }

// ============================================================================
// A — begin() escribe sobre el nodo REAL del Hub (.read-progress__fill).
// ============================================================================
fakeNow = 0;
HubForegroundProgress.begin("k1", { seedMs: 10000 });
assertEqual(fill.style.width, "6%", "A: begin() pinta de inmediato el checkpoint de arranque (~6%)");
assertTrue(!!intervalCallback, "A: arranca el timer compartido");

// B — begin() repetido con la MISMA key es idempotente (no reinicia el
// controller — si lo hiciera, el % volvería a 6 tras avanzar el reloj).
fakeNow = 2500;
intervalCallback();
assertEqual(fill.style.width, "35%", "B setup: el controller avanzó (25% del estimado -> ~35%)");
HubForegroundProgress.begin("k1", { seedMs: 10000 });
assertEqual(fill.style.width, "35%", "B: begin() repetido no reinicia una key ya activa");

// ============================================================================
// C — complete() graba EWMA (vía HubEstimatedProgress.finishSuccess) y
//     libera la key; fail() libera SIN grabar.
// ============================================================================
HubForegroundProgress.complete("k1");
assertTrue(!!store["dw-hub-progress-estimate:k1"], "C: complete() graba el estimate aprendido (EWMA)");
assertEqual(fill.style.width, "0%", "C: complete() resetea la barra (0% = invisible) de inmediato, sin esperar al próximo tick");
// El timer en sí se auto-limpia en su PRÓXIMO tick (mismo criterio que el
// sweep de reporting.js) — no sincrónicamente en complete()/fail(), pero la
// barra ya quedó en 0% de inmediato (ver arriba), así que no hay ningún
// efecto visible durante esa ventana.
intervalCallback();
assertTrue(intervalCleared, "C: el timer compartido se limpia en su próximo tick al no quedar ninguna key activa");

fakeNow = 0;
HubForegroundProgress.begin("k2", { seedMs: 5000 });
fakeNow = 4000;
HubForegroundProgress.fail("k2");
assertFalse(!!store["dw-hub-progress-estimate:k2"], "C: fail() NUNCA graba estimate (una duración hasta un error no es evidencia)");
assertEqual(fill.style.width, "0%", "C: fail() también resetea la barra");

// D — complete()/fail() son no-op si la key no está activa (nunca lanzan).
HubForegroundProgress.complete("no-existe");
HubForegroundProgress.fail("tampoco-existe");
assertTrue(true, "D: complete()/fail() sobre una key inexistente no lanza (no-op)");

// ============================================================================
// F — el listener htmx REAL de Asignaciones (#staffing-rows) dispara
//     begin/complete/fail — y NUNCA para un elemento no relacionado (así es
//     como un fetch Graph de background nunca activa esto: nunca pasa por
//     htmx con este id).
// ============================================================================
dispatch("htmx:beforeRequest", { elt: fakeElt(["#staffing-rows"]) });
assertEqual(fill.style.width, "6%", "F: htmx:beforeRequest sobre #staffing-rows arranca la barra");

dispatch("htmx:afterRequest", { elt: fakeElt(["#staffing-rows"]), successful: true });
assertEqual(fill.style.width, "0%", "F: htmx:afterRequest exitoso (#staffing-rows) completa y resetea la barra");
assertTrue(!!store["dw-hub-progress-estimate:assignments"], "F: el éxito grabó el estimate de 'assignments'");

dispatch("htmx:beforeRequest", { elt: fakeElt(["#staffing-rows"]) });
dispatch("htmx:afterRequest", { elt: fakeElt(["#staffing-rows"]), successful: false });
assertEqual(fill.style.width, "0%", "F: htmx:afterRequest fallido (#staffing-rows) también limpia la barra");

// Elemento NO relacionado (p. ej. otra request htmx cualquiera en el Hub) —
// NUNCA debe activar la barra. Prueba explícita del "no toda request Graph
// es foreground".
const widthBefore = fill.style.width;
dispatch("htmx:beforeRequest", { elt: fakeElt(["#alguna-otra-cosa"]) });
assertEqual(fill.style.width, widthBefore, "F: un elemento no relacionado con #staffing-rows nunca activa la barra");
dispatch("htmx:beforeRequest", { elt: undefined });
assertEqual(fill.style.width, widthBefore, "F: un evento htmx sin elt (defensivo) tampoco activa la barra");

if (failures > 0) {
  console.log(`${failures} Hub Foreground Progress boundary invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB FOREGROUND PROGRESS BOUNDARY INVARIANTS PASSED");
process.exit(0);
