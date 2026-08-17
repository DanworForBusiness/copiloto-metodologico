// reporting_hub_estimated_progress_glue.node.js — invocado por
// test_hub_estimated_progress.py::test_reporting_hub_progress_glue_invariants_hold.
// HUB FOREGROUND ESTIMATED PROGRESS — CORRECTIVO (owner live QA: "STOP
// REINVENTING THE HUB LOADER"). El Hub tiene UN loader real
// (`.read-progress`/`.read-progress__fill`, montado en platform_topbar.html,
// hoy alimentado por scroll real en Copiloto vía app.js). Reporting NO crea
// otra barra: escribe `style.width` sobre ESE MISMO `.read-progress__fill`
// con progreso ESTIMADO por tiempo. Este harness prueba, ejecutando
// `render()` REAL (mismo patrón que reporting_initial_load_contract.node.js):
//   - la barra pintada es EXACTAMENTE `.read-progress__fill` (nunca otro nodo/
//     clase inventada);
//   - tickear la barra NUNCA recrea/reinicia el loader "Cargando…" (el DOM de
//     `#reporting-main-content` queda BYTE-IDÉNTICO mientras la barra
//     tickea, sin ningún cambio de status);
//   - los invariantes A-K del corrective (visible/settle/cleanup/background/
//     perspective-switch/early-completion/overrun-cap), contra un stub
//     CONTROLABLE de `HubEstimatedProgress` (el primitivo en sí se prueba con
//     datos reales en hub_estimated_progress_primitive.node.js).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();

// El nodo REAL del Hub — `.read-progress__fill` — MISMA API que un elemento
// DOM real necesita para este contrato: `style.width`. `document.
// querySelector` (no `getElementById`, porque el nodo real es un `<span
// class="read-progress__fill">`, sin id) es lo que reporting.js realmente usa.
const readProgressFill = { style: { width: "" } };

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
    querySelector(sel) { return sel === ".read-progress__fill" ? readProgressFill : null; },
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

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
function assertEqual(a, b, label) { assertTrue(a === b, `${label} (got ${JSON.stringify(a)}, expected ${JSON.stringify(b)})`); }

// ============================================================================
// A — primera hidratación FOREGROUND escribe sobre el nodo REAL del Hub
//     (`.read-progress__fill`) — NUNCA un elemento/clase inventada, NUNCA
//     embebida dentro de `#reporting-main-content`.
// ============================================================================
state.experience = "rentabilidad";
state.rentabilidadDataStatus = "idle";
state.rentabilidadAniosStatus = "idle";
state.reportingTabHydrated.rentabilidad = false;
render();
assertEqual(readProgressFill.style.width, "6%", "A: el ancho inicial se escribe sobre .read-progress__fill (checkpoint de arranque ~6%)");
assertFalse(mainEl.innerHTML.includes("read-progress"), "A: la barra NUNCA vive dentro del render de Reporting (#reporting-main-content)");
assertEqual(startCalls.filter(c => c.key === "rentabilidad").length, 1, "A: un único HubEstimatedProgress.start() para rentabilidad");
assertEqual(startCalls[0].seedMs, 14500, "A: seed de Rentabilidad es el pedido por el owner (~14-15s)");

// Renders sucesivos MIENTRAS sigue cargando NO vuelven a llamar start() (bar
// creation es idempotente por tab, ver reportingHubProgressBegin).
render();
render();
assertEqual(startCalls.filter(c => c.key === "rentabilidad").length, 1, "A: renders sucesivos en 'loading' no duplican el controller");

// ============================================================================
// G — tickear la barra (sin cambio de status) NUNCA recrea/reinicia el
//     loader "Cargando…": el DOM de #reporting-main-content queda ESTABLE
//     (byte-idéntico) mientras el timer compartido tickea. Este es el bug
//     reportado en live QA ("el GIF se reinicia") — la causa era que el tick
//     llamaba render() y render() reemplaza mainContent.innerHTML entero.
// ============================================================================
const mainHtmlDuringLoad = mainEl.innerHTML;
assertTrue(mainHtmlDuringLoad.includes("section-analysis-loading"), "G setup: el loader está montado");
controllerFor("rentabilidad").percent = 35;               // el tick avanza...
intervalCallback();                                        // ...simulando un tick del timer compartido
assertEqual(mainEl.innerHTML, mainHtmlDuringLoad, "G: tickear la barra (percent 6->35) NUNCA toca #reporting-main-content");
assertEqual(readProgressFill.style.width, "35%", "G: el nodo REAL del Hub SÍ se actualiza (width), solo el loader de Reporting no");
controllerFor("rentabilidad").percent = 60;
intervalCallback();
intervalCallback();
assertEqual(mainEl.innerHTML, mainHtmlDuringLoad, "G: varios ticks seguidos siguen sin tocar #reporting-main-content");

// ============================================================================
// C — overrun: el ancho se mantiene clavado (~94%, tope), sin exceder el
//     valor que el controller reporta — reporting.js no le agrega ningún
//     tratamiento visual propio (ver corrective: "no crear otro sweep").
// ============================================================================
controllerFor("rentabilidad").overrun = true;
controllerFor("rentabilidad").percent = 94;
intervalCallback();
assertEqual(readProgressFill.style.width, "94%", "C: overrun se refleja como el mismo % clavado (~94%), sin clase/animación propia");
assertEqual(mainEl.innerHTML, mainHtmlDuringLoad, "C: el overrun tampoco toca #reporting-main-content");

// ============================================================================
// H — early/overrun finish: ready reemplaza el loader por contenido
//     INMEDIATO, resetea la barra a 0% y liquida (finishSuccess) el
//     controller — nunca se espera la animación.
// ============================================================================
state.rentabilidadDataStatus = "ready";
render();
assertEqual(readProgressFill.style.width, "0%", "H: la barra se resetea (0%, invisible) apenas hay contenido real");
assertEqual(controllerFor("rentabilidad").finishSuccessCalls, 1, "H: finishSuccess() se llama exactamente una vez al hidratar");
assertFalse(!!_reportingHubProgress().rentabilidad, "H: el controller se libera (delete) al hidratar");

// K — perspective/tab churn post-hidratación NUNCA vuelve a mostrar la barra
// ni a llamar start() de nuevo.
render(); render();
assertEqual(startCalls.filter(c => c.key === "rentabilidad").length, 1, "K: un tab ya hidratado nunca re-dispara start()");

// ============================================================================
// E — error en la primera hidratación: resetea la barra (finishError), sin
//     tocar el fail-soft existente (estado de error compartido).
// ============================================================================
state.experience = "horasvendidas";
state.horasVendidasDataStatus = "idle";
state.reportingTabHydrated.horasvendidas = false;
render();  // arranca loading -> crea el controller
assertEqual(readProgressFill.style.width, "6%", "E setup: barra activa mientras Horas Vendidas carga");
state.horasVendidasDataStatus = "error";
render();
assertEqual(readProgressFill.style.width, "0%", "E: la barra se resetea en el estado de error compartido");
assertTrue(mainEl.innerHTML.includes("empty-panel"), "E: el fail-soft existente (estado de error) sigue intacto");
assertEqual(controllerFor("horasvendidas").finishErrorCalls, 1, "E: finishError() se llama exactamente una vez");
assertFalse(!!_reportingHubProgress().horasvendidas, "E: el controller se libera también en error");

// ============================================================================
// I/D — background/switch-away: el usuario conmuta a OTRO tab mientras el
//       primero sigue cargando -> la barra refleja el tab ACTIVO (nunca el
//       de background) y el timer compartido liquida ese controller igual
//       (nunca queda huérfano) sin re-renderizar Reporting.
// ============================================================================
state.experience = "capacidad";
state.batchBDataStatus = "idle";
state.reportingTabHydrated.capacidad = false;
render();  // dispara loadBatchBData (-> "loading") y crea el controller de "batch-b"
assertTrue(!!_reportingHubProgress().capacidad, "I setup: hay un controller vivo para capacidad");
assertTrue(!!intervalCallback, "I setup: el timer compartido está corriendo");
assertEqual(readProgressFill.style.width, "6%", "I setup: la barra está activa (capacidad es el tab activo)");

// El usuario se va a OTRO tab ya hidratado (rentabilidad, del bloque H) antes
// de que capacidad termine de cargar.
state.experience = "rentabilidad";
render();
assertEqual(readProgressFill.style.width, "0%", "I: la barra se resetea al volver a un tab hidratado (nunca muestra el progreso de OTRO tab en background)");

// El fetch de "batch-b" resuelve EN BACKGROUND (nadie está mirando capacidad).
state.batchBDataStatus = "ready";
const mainHtmlBeforeSweep = mainEl.innerHTML;
intervalCallback();  // simula el tick del timer compartido
assertEqual(controllerFor("batch-b").finishSuccessCalls, 1, "D/I: el sweep liquida el controller aunque el tab no esté activo");
assertFalse(!!_reportingHubProgress().capacidad, "D/I: el controller de un tab en background se libera igual");
assertEqual(mainEl.innerHTML, mainHtmlBeforeSweep, "I: liquidar un tab en background NUNCA re-renderiza Reporting (nunca llama render())");

// D — el timer compartido se auto-limpia cuando ya no queda ningún tab
// esperando (nunca queda corriendo para siempre).
assertTrue(intervalCleared, "D: el timer compartido se limpia solo cuando no queda ningún controller vivo");

if (failures > 0) {
  console.log(`${failures} Hub Estimated Progress glue invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB ESTIMATED PROGRESS GLUE INVARIANTS PASSED");
process.exit(0);
