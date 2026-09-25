// commercial_active_opportunities_navigation.node.js — invocado por
// test_commercial_active_opportunities_navigation.py. Active Opportunities
// Phase 2 / C3.1 (ver HANDOFF — interaction + spacing corrective): al ABRIR
// el análisis desde el KPI, la vista navega a la sección — nunca al
// cerrarla, cambiar Horas/Ponderadas o filtrar. Ejecuta el MOTOR REAL
// (commercialMaybeScrollToOpenedAnalysis) contra mocks mínimos de
// `trigger`/`root` — MISMA técnica que los demás harnesses de este archivo.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; } },
  window: {},
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  initDraggableColumns() {},
  readStoredColumnOrder() { return null; },
  readStoredColumnWidths() { return null; },
  initResizableColumns() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const { commercialMaybeScrollToOpenedAnalysis } = sandbox;

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}

// Mock mínimo de un elemento: sólo lo que la función real toca.
function fakeTrigger(isActive, eventKey) {
  return {
    dataset: { event: eventKey },
    classList: { contains: cls => cls === "is-active" && isActive },
  };
}
function fakeRootWithTarget(initialOpen) {
  const calls = [];
  const target = { open: initialOpen, scrollIntoView(opts) { calls.push(opts); } };
  const root = { querySelector(sel) { root.lastSelector = sel; return sel === '[data-breakdown="activas"]' ? target : null; } };
  return { root, calls, target };
}

// --- A/B: abrir (is-active=true) navega a la sección correcta ---------------
{
  const { root, calls } = fakeRootWithTarget();
  commercialMaybeScrollToOpenedAnalysis(fakeTrigger(true, "activas"), root);
  assertTrue(calls.length === 1, "scrollIntoView se llama exactamente una vez al abrir");
  assertTrue(root.lastSelector === '[data-breakdown="activas"]', "busca la sección por su propio data-breakdown, no un id fijo");
}

// --- C: cerrar (is-active=false, ej. toggle-to-deselect) NO fuerza scroll ---
{
  const { root, calls } = fakeRootWithTarget();
  commercialMaybeScrollToOpenedAnalysis(fakeTrigger(false, "activas"), root);
  assertTrue(calls.length === 0, "sin is-active, ningún scroll — el cierre nunca navega");
}

// --- D: abrir fuerza `open=true` en el <details> (owner corrective, ver
//     HANDOFF): la X de cierre ahora vive en <summary> y sobrevive el
//     colapso NATIVO del `<details>` — pero `initEventBreakdown` sólo
//     gobierna `hidden` (mostrar/ocultar el bloque), nunca `open`
//     (expandido/colapsado adentro). Sin esto, reabrir el tile tras haber
//     colapsado el grupo a mano dejaría sólo la barra del <summary>
//     visible, sin contenido, hasta el próximo render completo. ------------
{
  const { root, target } = fakeRootWithTarget(false); // simula: el owner lo había colapsado a mano
  commercialMaybeScrollToOpenedAnalysis(fakeTrigger(true, "activas"), root);
  assertTrue(target.open === true, "al abrir, se fuerza open=true aunque el <details> estuviera colapsado nativamente");
}
{
  // Cerrar (is-active=false) nunca toca `open` — no es su responsabilidad,
  // y forzarlo ahí pisaría una preferencia de collapse que el owner haya
  // dejado a propósito para la próxima vez que se abra.
  const { root, target } = fakeRootWithTarget(false);
  commercialMaybeScrollToOpenedAnalysis(fakeTrigger(false, "activas"), root);
  assertTrue(target.open === false, "al cerrar, `open` queda intocado — sólo se toca al ABRIR");
}

// --- E: sin `behavior` explícito — hereda scroll-behavior de <html>
//        (app.css: smooth normal, auto bajo prefers-reduced-motion) --------
{
  const { root, calls } = fakeRootWithTarget();
  commercialMaybeScrollToOpenedAnalysis(fakeTrigger(true, "activas"), root);
  const opts = calls[0];
  assertTrue(opts && opts.block === "start", "block:'start' — el heading queda arriba, nunca centrado/al fondo");
  assertTrue(opts && !("behavior" in opts), "SIN behavior explícito — decide el scroll-behavior de <html>, nunca un chequeo de motion propio");
}

// --- Búsqueda cuelga de `root`, nunca de `document` (MISMO contrato que
//     el resto del archivo — ver test Python de la progress bar) -----------
{
  const { root, calls } = fakeRootWithTarget();
  let documentTouched = false;
  const originalQuerySelector = sandbox.document.querySelector;
  sandbox.document.querySelector = () => { documentTouched = true; return null; };
  commercialMaybeScrollToOpenedAnalysis(fakeTrigger(true, "activas"), root);
  sandbox.document.querySelector = originalQuerySelector;
  assertTrue(!documentTouched, "la función nunca busca en `document`, sólo en el `root` que recibe");
  assertTrue(calls.length === 1, "y aun así encuentra su target vía `root`");
}

// --- Target inexistente no rompe (defensivo — optional chaining) -----------
{
  const root = { querySelector() { return null; } };
  let threw = false;
  try { commercialMaybeScrollToOpenedAnalysis(fakeTrigger(true, "activas"), root); }
  catch (e) { threw = true; }
  assertTrue(!threw, "sin target encontrado, no rompe (optional chaining, no excepción)");
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL ACTIVE OPPORTUNITIES NAVIGATION INVARIANTS PASSED");
