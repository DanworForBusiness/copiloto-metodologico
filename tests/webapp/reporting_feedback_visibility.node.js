// reporting_feedback_visibility.node.js — invocado por
// test_reporting_feedback_visibility.py. FEEDBACK VISIBILITY corrective
// (owner decision 2026-08-19 — owner live smoke: "¿Qué te pareció este
// reporte?" quedaba visible mientras el contenido activo todavía estaba
// cargando). Carga reporting.js real en un sandbox Node con un `fetch` FAKE
// (mismo patrón que reporting_cartera_lifecycle_active_perspective_only.
// node.js), ejecuta el ciclo de hidratación REAL vía `render()`, y prueba
// que la visibilidad del control de feedback sigue el status PRESENTE de
// la perspectiva/experiencia ACTIVA (`isActiveReportReady`), nunca el flag
// one-way `reportingTabHydrated` que sigue gateando SOLO filtros/contenido.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();

let carteraMantenimientoCalls = 0;
let carteraProyectoCalls = 0;
let rentabilidadCalls = 0;
let feedbackPosts = [];
let failOnce = { mantenimiento: false, rentabilidad: false };

function fakeFetch(url, opts) {
  const params = new URLSearchParams((opts && opts.body) || "");
  if (url === "/operaciones/reporting/integral/data/cartera") {
    const persp = params.get("perspectiva");
    if (persp === "mantenimiento") {
      carteraMantenimientoCalls++;
      if (failOnce.mantenimiento) {
        failOnce.mantenimiento = false;
        return Promise.resolve({ json: () => Promise.resolve({ state: "source_error", stage: "horas_proyectos" }) });
      }
      return Promise.resolve({ json: () => Promise.resolve({
        state: "ok",
        cartera: { mantenimiento: [{
          cliente: "Danone", cp: "María López", proyecto: "Soporte Analítica", diff: 4,
          trabajadas: 92, certificadas: 96, vendidasMensuales: 88, facturadas: 96,
          status: "En curso", fecha: "2026-07-01", anio: "2026", mes: "7",
        }] },
      })});
    }
    if (persp === "proyecto") {
      carteraProyectoCalls++;
      return Promise.resolve({ json: () => Promise.resolve({
        state: "ok",
        cartera: { proyecto: [{
          cliente: "YPF", cp: "Diego Ramos", idProyecto: "DAN-1042", proyecto: "Implementación SADI v2",
          diff: -18, trabajadas: 340, certificadas: 322, vendidas: 360, facturadas: 290,
          status: "En curso", anioInicio: "2026",
        }] },
      })});
    }
  }
  if (url === "/operaciones/reporting/integral/data/cartera/anios-disponibles") {
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok", aniosDisponibles: [{ anio: "2026", estado: "En curso" }] }) });
  }
  if (url === "/operaciones/reporting/integral/data/rentabilidad") {
    rentabilidadCalls++;
    if (failOnce.rentabilidad) {
      failOnce.rentabilidad = false;
      return Promise.resolve({ json: () => Promise.resolve({ state: "source_error", stage: "time_logs_files" }) });
    }
    return Promise.resolve({ json: () => Promise.resolve({
      state: "ok",
      rentabilidad: { proyectos: [], noAsignadasMonthly: Array(12).fill(0), noAsignadasByYear: {} },
    })});
  }
  if (url === "/operaciones/reporting/integral/data/rentabilidad/anios-disponibles") {
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok", aniosDisponibles: [] }) });
  }
  if (url === "/operaciones/reporting/integral/feedback") {
    feedbackPosts.push(params.get("report_tab") + ":" + params.get("rating"));
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok" }) });
  }
  return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) });
}

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
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  sessionStorage: { getItem() { return ""; } },
  fetch: fakeFetch,
  initDashboardSections() {},
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  setInterval() { return 0; },
  clearInterval() {},
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { render, submitReportingFeedback } = sandbox;
const state = vm.runInContext("state", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }

const FEEDBACK_MARK = "section-feedback";

async function flush() {
  await new Promise(setImmediate);
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}

async function main() {
  // ==========================================================================
  // A) Bootstrap Cartera/Mantenimiento — mientras carga, feedback OCULTO.
  // ==========================================================================
  state.experience = "cartera";
  state.perspective.cartera = "Mantenimiento";
  render(); // dispara hydrateCarteraTab -> loadCarteraMantenimiento (status pasa a "loading" síncrono)
  assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "A: bootstrap Mantenimiento LOADING -> feedback OCULTO");
  await flush();

  // ==========================================================================
  // B) Mantenimiento ready — feedback VISIBLE.
  // ==========================================================================
  assertTrue(state.carteraMantenimientoStatus === "ready", "B: setup — Mantenimiento terminó ready");
  render();
  assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "B: Mantenimiento READY -> feedback VISIBLE");

  // ==========================================================================
  // C) Switch a Proyecto (idle -> loading) — feedback OCULTO, aunque
  //    Mantenimiento siga "ready" en el state de fondo.
  // ==========================================================================
  state.perspective.cartera = "Proyecto";
  render(); // dispara loadCarteraProyecto (status pasa a "loading" síncrono)
  assertTrue(state.carteraMantenimientoStatus === "ready", "C: Mantenimiento sigue ready en background (no se resetea)");
  assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "C: Proyecto LOADING (primera vez) -> feedback OCULTO pese a Mantenimiento ready en background");
  await flush();

  // ==========================================================================
  // D) Proyecto ready — feedback VISIBLE.
  // ==========================================================================
  assertTrue(state.carteraProyectoStatus === "ready", "D: setup — Proyecto terminó ready");
  render();
  assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "D: Proyecto READY -> feedback VISIBLE");

  // ==========================================================================
  // E) Switch a Rentabilidad (loading) — feedback OCULTO, aunque Cartera
  //    (Mantenimiento+Proyecto) siga ready en background/state.
  // ==========================================================================
  state.experience = "rentabilidad";
  render(); // dispara hydrateRentabilidadTab -> loadRentabilidadData (loading síncrono)
  assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "E: Rentabilidad LOADING -> feedback OCULTO pese a Cartera ready en background");
  await flush();

  // ==========================================================================
  // F) Rentabilidad ready — feedback VISIBLE.
  // ==========================================================================
  assertTrue(state.rentabilidadDataStatus === "ready", "F: setup — Rentabilidad terminó ready");
  render();
  assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "F: Rentabilidad READY -> feedback VISIBLE");

  // ==========================================================================
  // G) Revisit — volver a un tab/perspectiva YA ready muestra feedback DE
  //    INMEDIATO, sin flash de "oculto" transitorio.
  // ==========================================================================
  state.experience = "cartera";
  state.perspective.cartera = "Mantenimiento";
  render();
  assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "G: revisitar Cartera/Mantenimiento (ya ready) -> feedback VISIBLE de inmediato");
  state.perspective.cartera = "Proyecto";
  render();
  assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "G: revisitar Cartera/Proyecto (ya ready) -> feedback VISIBLE de inmediato");

  // ==========================================================================
  // H) ERROR — primera carga fallida (nunca hidratado) Y error incremental
  //    post-hidratación (perspectiva hermana que falla en su primera carga)
  //    -> feedback OCULTO en ambos casos, MISMO criterio.
  // ==========================================================================
  // H.1 — primera carga fallida (tab nunca hidratado todavía).
  state.experience = "horasvendidas";
  state.horasVendidasDataStatus = "error";
  state.reportingTabHydrated.horasvendidas = false;
  render();
  assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "H.1: primera carga con ERROR -> feedback OCULTO");

  // H.2 — error incremental: Rentabilidad ya hidratado (paso F), pero un
  // year frío (cold year) que falla deja el status ACTIVO en "error" sin
  // apagar el flag one-way de hidratación.
  state.experience = "rentabilidad";
  state.rentabilidadDataStatus = "error";
  render();
  assertTrue(state.reportingTabHydrated.rentabilidad, "H.2: setup — Rentabilidad sigue con el flag one-way hidratado");
  assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "H.2: error INCREMENTAL post-hidratación -> feedback OCULTO igual (mismo criterio que primera carga)");
  state.rentabilidadDataStatus = "ready";
  render();

  // ==========================================================================
  // I) Con feedback visible, la acción 👍 sigue invocando el MISMO
  //    handler/endpoint/payload que antes de este corrective (visibilidad
  //    ÚNICAMENTE — nada de la acción en sí cambió).
  // ==========================================================================
  assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "I: setup — feedback visible antes de reaccionar");
  await submitReportingFeedback("rentabilidad", "up");
  assertTrue(feedbackPosts.includes("rentabilidad:up"), "I: 👍 sigue posteando MISMO report_tab/rating al endpoint de feedback");
  assertTrue(state.reportingFeedback.rentabilidad === "up", "I: MISMO estado interno de feedback tras reaccionar (sin cambios de esta corrective)");

  if (failures > 0) {
    console.log(`${failures} feedback visibility invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL FEEDBACK VISIBILITY INVARIANTS PASSED");
  process.exit(0);
}

main().catch(err => { console.log("FAIL: uncaught error: " + err.stack); process.exit(1); });
