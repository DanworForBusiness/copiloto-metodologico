// reporting_hub_estimated_progress_glue.node.js — invocado por
// test_hub_estimated_progress.py::test_reporting_hub_progress_glue_invariants_hold.
// HUB FOREGROUND ESTIMATED PROGRESS: prueba el GLUE de reporting.js (creación
// de la barra, settle en ready/error, y el timer compartido que liquida un
// tab aunque el usuario haya conmutado a OTRO mientras cargaba) ejecutando
// `render()` REAL contra un stub CONTROLABLE de `HubEstimatedProgress` (el
// primitivo en sí se prueba con datos reales en
// hub_estimated_progress_primitive.node.js). Mismo patrón que
// reporting_initial_load_contract.node.js.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();

// --- Stub CONTROLABLE de HubEstimatedProgress + timer (spy) ----------------
const startCalls = [];              // { key, seedMs }
const controllers = {};             // key -> { percent, overrun, finishSuccessCalls, finishErrorCalls }
function controllerFor(key) {
  if (!controllers[key]) controllers[key] = { percent: 6, overrun: false, finishSuccessCalls: 0, finishErrorCalls: 0 };
  return controllers[key];
}
let intervalCallback = null;
let intervalCleared = false;

const sandbox = {
  document: {
    addEventListener() {},
    getElementById(id) {
      if (id === "reporting-experience-tabs") return tabsEl;
      if (id === "reporting-main-content") return mainEl;
      return makeEl();
    },
  },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  initDashboardSections() {},
  HubEstimatedProgress: {
    start(key, opts) {
      startCalls.push({ key, seedMs: opts && opts.seedMs });
      const c = controllerFor(key);
      return {
        tick() { return { percent: c.percent, overrun: c.overrun }; },
        finishSuccess() { c.finishSuccessCalls++; },
        finishError() { c.finishErrorCalls++; },
      };
    },
  },
  setInterval(fn) { intervalCallback = fn; intervalCleared = false; return 1; },
  clearInterval() { intervalCallback = null; intervalCleared = true; },
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { render } = sandbox;
const state = vm.runInContext("state", sandbox);
const _reportingHubProgress = () => vm.runInContext("_reportingHubProgress", sandbox);
const _reportingHubProgressTimer = () => vm.runInContext("_reportingHubProgressTimer", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
function assertEqual(a, b, label) { assertTrue(a === b, `${label} (got ${JSON.stringify(a)}, expected ${JSON.stringify(b)})`); }

const BAR_MARK = "hub-progress";
const OVERRUN_MARK = "hub-progress--overrun";

// ============================================================================
// A/G — primera hidratación FOREGROUND muestra la barra; jamás en un tab ya
//       hidratado (perspective switch post-hidratación).
// ============================================================================
state.experience = "rentabilidad";
state.rentabilidadDataStatus = "idle";
state.rentabilidadAniosStatus = "idle";
state.reportingTabHydrated.rentabilidad = false;
render();
assertTrue(mainEl.innerHTML.includes(BAR_MARK), "A: la barra estimada aparece en el loader de primera hidratación");
assertEqual(startCalls.filter(c => c.key === "rentabilidad").length, 1, "A: un único HubEstimatedProgress.start() para rentabilidad");
assertEqual(startCalls[0].seedMs, 14500, "A: seed de Rentabilidad es el pedido por el owner (~14-15s)");

// Renders sucesivos MIENTRAS sigue cargando NO vuelven a llamar start() (bar
// creation es idempotente por tab, ver reportingHubProgressBarHtml).
render();
render();
assertEqual(startCalls.filter(c => c.key === "rentabilidad").length, 1, "A: renders sucesivos en 'loading' no duplican el controller");

// ============================================================================
// C — overrun: la barra pasa a mostrar la clase de sweep continuo.
// ============================================================================
controllerFor("rentabilidad").overrun = true;
controllerFor("rentabilidad").percent = 94;
render();
assertTrue(mainEl.innerHTML.includes(OVERRUN_MARK), "C: overrun=true agrega la clase de sweep continuo");

// ============================================================================
// B — early/overrun finish: ready reemplaza el loader por contenido
//     INMEDIATO y liquida (finishSuccess) el controller — nunca se espera la
//     animación.
// ============================================================================
state.rentabilidadDataStatus = "ready";
render();
assertFalse(mainEl.innerHTML.includes(BAR_MARK), "B: la barra desaparece apenas hay contenido real");
assertEqual(controllerFor("rentabilidad").finishSuccessCalls, 1, "B: finishSuccess() se llama exactamente una vez al hidratar");
assertFalse(!!_reportingHubProgress().rentabilidad, "B: el controller se libera (delete) al hidratar");

// G — perspective/tab churn post-hidratación NUNCA vuelve a mostrar la barra
// ni a llamar start() de nuevo.
render(); render();
assertEqual(startCalls.filter(c => c.key === "rentabilidad").length, 1, "G: un tab ya hidratado nunca re-dispara start()");

// ============================================================================
// E — error en la primera hidratación: detiene la barra (finishError), sin
//     tocar el fail-soft existente (estado de error compartido).
// ============================================================================
state.experience = "horasvendidas";
state.horasVendidasDataStatus = "idle";
state.reportingTabHydrated.horasvendidas = false;
render();  // arranca loading -> crea el controller
assertTrue(mainEl.innerHTML.includes(BAR_MARK), "E setup: barra visible mientras Horas Vendidas carga");
state.horasVendidasDataStatus = "error";
render();
assertFalse(mainEl.innerHTML.includes(BAR_MARK), "E: la barra desaparece en el estado de error compartido");
assertTrue(mainEl.innerHTML.includes("empty-panel"), "E: el fail-soft existente (estado de error) sigue intacto");
assertEqual(controllerFor("horasvendidas").finishErrorCalls, 1, "E: finishError() se llama exactamente una vez");
assertFalse(!!_reportingHubProgress().horasvendidas, "E: el controller se libera también en error");

// ============================================================================
// F/D — background/switch-away: el usuario conmuta a OTRO tab mientras el
//       primero sigue cargando -> el timer compartido liquida ese controller
//       igual (nunca queda huérfano), sin re-renderizar el tab que NO está
//       activo.
// ============================================================================
state.experience = "capacidad";
state.batchBDataStatus = "idle";
state.reportingTabHydrated.capacidad = false;
render();  // dispara loadBatchBData (-> "loading") y crea el controller de "batch-b"
assertTrue(!!_reportingHubProgress().capacidad, "F setup: hay un controller vivo para capacidad");
assertTrue(!!intervalCallback, "F setup: el timer compartido está corriendo");

// El usuario se va a OTRO tab ya hidratado (rentabilidad, del bloque B) antes
// de que capacidad termine de cargar.
state.experience = "rentabilidad";
render();
assertFalse(mainEl.innerHTML.includes(BAR_MARK), "F: el tab activo (ya hidratado) nunca muestra la barra de otro tab");

// El fetch de "batch-b" resuelve EN BACKGROUND (nadie está mirando capacidad).
state.batchBDataStatus = "ready";
const mainHtmlBeforeSweep = mainEl.innerHTML;
intervalCallback();  // simula el tick del timer compartido
assertEqual(controllerFor("batch-b").finishSuccessCalls, 1, "D/F: el sweep liquida el controller aunque el tab no esté activo");
assertFalse(!!_reportingHubProgress().capacidad, "D/F: el controller de un tab en background se libera igual");
assertEqual(mainEl.innerHTML, mainHtmlBeforeSweep, "F: liquidar un tab en background NUNCA re-renderiza el tab activo (batch-b != rentabilidad)");

// D — el timer compartido se auto-limpia cuando ya no queda ningún tab
// esperando (nunca queda corriendo para siempre).
assertTrue(intervalCleared, "D: el timer compartido se limpia solo cuando no queda ningún controller vivo");

if (failures > 0) {
  console.log(`${failures} Hub Estimated Progress glue invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB ESTIMATED PROGRESS GLUE INVARIANTS PASSED");
process.exit(0);
