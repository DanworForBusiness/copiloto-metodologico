// reporting_hub_estimated_progress_glue.node.js — invocado por
// test_hub_estimated_progress.py::test_reporting_hub_progress_glue_invariants_hold.
// HUB FOREGROUND PROGRESS — reporting.js's GLUE (owner corrective: "el
// loader pertenece al Hub, los consumidores SOLO notifican lifecycle").
// Reporting NUNCA toca DOM/CSS/timer del progress — eso vive enteramente en
// `HubForegroundProgress` (app.js, probado con datos reales en
// hub_foreground_progress.node.js). Este harness ejecuta `render()` REAL
// (mismo patrón que reporting_initial_load_contract.node.js) contra un
// STUB/SPY de `HubForegroundProgress` para probar:
//   - PRIMERA hidratación: begin/complete/fail con la key/seed correctos,
//     idempotencia, y que el DOM de #reporting-main-content queda ESTABLE
//     (byte-idéntico) mientras el sweep tickea (el bug "el GIF se reinicia"
//     de live QA — la causa era que el tick llamaba render());
//   - el sweep de PRIMERA hidratación liquida un tab en background (el
//     usuario conmutó de tab antes de que la carga resolviera) sin
//     re-renderizar Reporting;
//   - los 4 loaders de COLD-YEAR (Cartera/Rentabilidad/Horas Vendidas/
//     Participación) llaman begin/complete/fail DIRECTAMENTE desde la
//     operación real (nunca detectado desde render()), preservan la
//     superficie ya hidratada (filtros/contenido, nunca vuelven al
//     first-load blanking), y nunca disparan un Graph request adicional
//     más allá del ya esperado.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();

// --- Stub/spy de HubForegroundProgress (el primitivo en sí, y el listener
// htmx de Asignaciones, se prueban con código REAL en
// hub_foreground_progress.node.js) ------------------------------------------
const foregroundCalls = [];  // { op: "begin"|"complete"|"fail", key, seedMs }
const foregroundActive = new Set();
const HubForegroundProgress = {
  begin(key, opts) {
    foregroundCalls.push({ op: "begin", key, seedMs: opts && opts.seedMs });
    foregroundActive.add(key);
  },
  complete(key) {
    foregroundCalls.push({ op: "complete", key });
    foregroundActive.delete(key);
  },
  fail(key) {
    foregroundCalls.push({ op: "fail", key });
    foregroundActive.delete(key);
  },
};
function callsFor(key, op) { return foregroundCalls.filter(c => c.key === key && (!op || c.op === op)); }

// Fetch controlable — cada test setea `fetchImpl` para simular éxito/error
// del endpoint que corresponda; cuenta invocaciones para el chequeo "cero
// Graph requests adicionales".
let fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) });
let fetchCallCount = 0;

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
  fetch(...args) { fetchCallCount++; return fetchImpl(...args); },
  initDashboardSections() {},
  HubForegroundProgress,
  setInterval(fn) { intervalCallback = fn; intervalCleared = false; return 1; },
  clearInterval() { intervalCallback = null; intervalCleared = true; },
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { render, loadCarteraMantenimientoColdYear, loadRentabilidadColdYear,
  loadHorasVendidasColdYear, loadComercialParticipacionColdYears } = sandbox;
const state = vm.runInContext("state", sandbox);
const _reportingForegroundExpIds = () => vm.runInContext("_reportingForegroundExpIds", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
function assertEqual(a, b, label) { assertTrue(a === b, `${label} (got ${JSON.stringify(a)}, expected ${JSON.stringify(b)})`); }

// Los loaders de cold-year son `async function` — top-level await no está
// disponible en un script CommonJS plano (`node file.js`), de ahí el IIFE.
(async () => {

// ============================================================================
// A — primera hidratación FOREGROUND notifica begin() con la key/seed
//     correctos — NUNCA toca DOM/CSS/width (eso es 100% de HubForegroundProgress).
// ============================================================================
state.experience = "rentabilidad";
state.rentabilidadDataStatus = "idle";
state.rentabilidadAniosStatus = "idle";
state.reportingTabHydrated.rentabilidad = false;
render();
assertEqual(callsFor("rentabilidad", "begin").length, 1, "A: un único begin() para rentabilidad");
assertEqual(callsFor("rentabilidad", "begin")[0].seedMs, 14500, "A: seed de Rentabilidad es el pedido por el owner (~14-15s)");
assertFalse(mainEl.innerHTML.includes("read-progress"), "A: la barra NUNCA vive dentro del render de Reporting (#reporting-main-content)");

// Renders sucesivos MIENTRAS sigue cargando NO vuelven a llamar begin().
render();
render();
assertEqual(callsFor("rentabilidad", "begin").length, 1, "A: renders sucesivos en 'loading' no duplican el begin()");

// ============================================================================
// G — el sweep de PRIMERA hidratación NUNCA llama render(): el DOM de
//     #reporting-main-content queda ESTABLE (byte-idéntico) mientras
//     tickea, sin ningún cambio de status real (el bug "el GIF se reinicia"
//     de live QA — la causa era que el tick anterior llamaba render()).
// ============================================================================
const mainHtmlDuringLoad = mainEl.innerHTML;
assertTrue(mainHtmlDuringLoad.includes("section-analysis-loading"), "G setup: el loader está montado");
assertTrue(!!intervalCallback, "G setup: el sweep de primera hidratación está corriendo");
intervalCallback();
intervalCallback();
assertEqual(mainEl.innerHTML, mainHtmlDuringLoad, "G: varios ticks del sweep (sin cambio de status) NUNCA tocan #reporting-main-content");
assertEqual(callsFor("rentabilidad").length, 1, "G: ticks sin cambio de status no vuelven a notificar begin/complete/fail");

// ============================================================================
// H — early finish: ready reemplaza el loader por contenido INMEDIATO y
//     notifica complete() — nunca se espera ninguna animación.
// ============================================================================
state.rentabilidadDataStatus = "ready";
render();
assertEqual(callsFor("rentabilidad", "complete").length, 1, "H: complete() se notifica exactamente una vez al hidratar");
assertFalse(_reportingForegroundExpIds().has("rentabilidad"), "H: el tracking interno se libera al hidratar");

// K — perspective/tab churn post-hidratación NUNCA vuelve a notificar begin().
render(); render();
assertEqual(callsFor("rentabilidad", "begin").length, 1, "K: un tab ya hidratado nunca re-dispara begin()");

// ============================================================================
// E — error en la primera hidratación: notifica fail(), sin tocar el
//     fail-soft existente (estado de error compartido).
// ============================================================================
state.experience = "horasvendidas";
state.horasVendidasDataStatus = "idle";
state.reportingTabHydrated.horasvendidas = false;
render();  // arranca loading -> begin()
assertEqual(callsFor("horasvendidas", "begin").length, 1, "E setup: begin() para horasvendidas");
state.horasVendidasDataStatus = "error";
render();
assertTrue(mainEl.innerHTML.includes("empty-panel"), "E: el fail-soft existente (estado de error) sigue intacto");
assertEqual(callsFor("horasvendidas", "fail").length, 1, "E: fail() se notifica exactamente una vez");
assertFalse(_reportingForegroundExpIds().has("horasvendidas"), "E: el tracking interno se libera también en error");

// ============================================================================
// I/D — background/switch-away: el usuario conmuta a OTRO tab mientras el
//       primero sigue cargando -> el sweep de primera hidratación liquida
//       ese controller igual (nunca queda huérfano) sin re-renderizar
//       Reporting. (La barra en sí SIGUE reflejando esa espera hasta que se
//       liquide — deliberado, ver comentario en render(): el boundary no
//       tiene noción de "tab activo de Reporting".)
// ============================================================================
state.experience = "capacidad";
state.batchBDataStatus = "idle";
state.reportingTabHydrated.capacidad = false;
render();  // dispara loadBatchBData (-> "loading") y notifica begin("batch-b")
assertTrue(_reportingForegroundExpIds().has("capacidad"), "I setup: capacidad sigue trackeada como primera-hidratación en curso");
assertTrue(!!intervalCallback, "I setup: el sweep está corriendo");

// El usuario se va a OTRO tab ya hidratado (rentabilidad) antes de que
// capacidad termine de cargar.
state.experience = "rentabilidad";
render();
assertEqual(callsFor("batch-b", "complete").length, 0, "I: switch-away por sí solo NO liquida la espera en background (la liquida el sweep, no render())");

// El fetch de "batch-b" resuelve EN BACKGROUND (nadie está mirando capacidad).
state.batchBDataStatus = "ready";
const mainHtmlBeforeSweep = mainEl.innerHTML;
intervalCallback();  // simula el tick del sweep de primera hidratación
assertEqual(callsFor("batch-b", "complete").length, 1, "D/I: el sweep notifica complete() aunque el tab no esté activo (key 'batch-b', ver REPORTING_HUB_PROGRESS.capacidad)");
assertFalse(_reportingForegroundExpIds().has("capacidad"), "D/I: el tracking interno de un tab en background se libera igual");
assertEqual(mainEl.innerHTML, mainHtmlBeforeSweep, "I: liquidar un tab en background NUNCA re-renderiza Reporting (nunca llama render())");

// D — el sweep de primera hidratación se auto-limpia cuando ya no queda
// ningún tab esperando (nunca queda corriendo para siempre).
assertTrue(intervalCleared, "D: el sweep se limpia solo cuando no queda ningún tab de primera-hidratación pendiente");

// ============================================================================
// COLD-YEAR — el ownership del begin/complete/fail vive DENTRO de la
// operación real (nunca detectado desde render()); preserva la superficie
// ya hidratada; cero Graph requests adicionales más allá del esperado.
// ============================================================================

// Cartera: éxito.
fetchCallCount = 0;
fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", cartera: { mantenimiento: [] } }) });
state.carteraMantenimientoYearLoading = new Set();
state.carteraMantenimientoYearErrors = new Set();
await loadCarteraMantenimientoColdYear("2023");
assertEqual(callsFor("cartera", "begin").length, 1, "Cartera cold-year: begin() con la MISMA key que su primera hidratación");
assertEqual(callsFor("cartera", "complete").length, 1, "Cartera cold-year: complete() al resolver OK");
assertEqual(fetchCallCount, 1, "Cartera cold-year: exactamente UN fetch (cero requests adicionales)");

// Cartera: fetch OK pero backend reporta error de negocio -> fail().
fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "graph_error" }) });
state.carteraMantenimientoYearLoading = new Set();
await loadCarteraMantenimientoColdYear("2022");
assertEqual(callsFor("cartera", "fail").length, 1, "Cartera cold-year: fail() cuando el backend responde error");
assertTrue(state.carteraMantenimientoYearErrors.has("2022"), "Cartera cold-year: el fail-soft existente (error de año, ahora un Set — AÑO/MES MULTI-SELECT corrective) sigue intacto");

// Cartera: excepción de red -> fail() también (catch).
fetchImpl = () => Promise.reject(new Error("network down"));
state.carteraMantenimientoYearLoading = new Set();
await loadCarteraMantenimientoColdYear("2021");
assertEqual(callsFor("cartera", "fail").length, 2, "Cartera cold-year: fail() también ante una excepción de red (catch)");

// Rentabilidad: éxito, MISMA key que su primera hidratación.
fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", rentabilidad: {} }) });
state.rentabilidadYearLoading = new Set();
await loadRentabilidadColdYear("2023");
assertEqual(callsFor("rentabilidad", "begin").length, 2, "Rentabilidad cold-year: begin() (el 2do — el 1ro fue la primera hidratación del bloque A)");
assertTrue(callsFor("rentabilidad", "complete").length >= 1, "Rentabilidad cold-year: complete() al resolver OK");

// Horas Vendidas: éxito.
fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", horasVendidas: [] }) });
state.horasVendidasYearLoading = new Set();
await loadHorasVendidasColdYear("2023");
assertEqual(callsFor("horasvendidas", "begin").filter(c => c.seedMs === 9500).length >= 1, true, "Horas Vendidas cold-year: begin() con su propia key/seed");
assertTrue(callsFor("horasvendidas", "complete").length >= 1, "Horas Vendidas cold-year: complete() al resolver OK");

// Participación (Comercial): key PROPIA ("participacion"), NUNCA "batch-a"
// (esa es la key de Salud/Calendario — un endpoint real distinto).
fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", participacion: [] }) });
state.comercialParticipacionYearLoading = new Set();
state.comercialParticipacionYearErrors = new Set();
state.comercialParticipacionLoadedYears = new Set();
await loadComercialParticipacionColdYears(["2023"]);
assertEqual(callsFor("participacion", "begin").length, 1, "Participación cold-year: begin() con key propia 'participacion'");
assertEqual(callsFor("participacion", "complete").length, 1, "Participación cold-year: complete() al resolver OK");
assertEqual(callsFor("batch-a", "begin").length, 0, "Participación cold-year: NUNCA reusa la key 'batch-a' de Salud/Calendario");

if (failures > 0) {
  console.log(`${failures} Hub Estimated Progress glue invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB ESTIMATED PROGRESS GLUE INVARIANTS PASSED");
process.exit(0);

})().catch(e => {
  console.log(`FAIL: excepción no capturada: ${(e && e.stack) || e}`);
  process.exit(1);
});
