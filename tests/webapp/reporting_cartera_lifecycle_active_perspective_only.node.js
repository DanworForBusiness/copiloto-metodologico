// reporting_cartera_lifecycle_active_perspective_only.node.js — invocado por
// test_reporting_cartera_lifecycle_active_perspective_only.py. ACTIVE
// EXPERIENCE + ACTIVE PERSPECTIVE ONLY corrective (owner decision
// 2026-08-19 — lifecycle audit): carga reporting.js en un sandbox mínimo
// con un `fetch` FAKE que cuenta llamadas por endpoint/perspectiva, para
// probar en runtime real que `hydrateCarteraTab()` deja de disparar AMBAS
// perspectivas de Cartera eager — Proyecto arranca SOLO cuando es
// realmente la perspectiva activa, Mantenimiento (+ su metadata de años)
// arranca en el bootstrap, y ninguna revisita a una perspectiva ya
// `ready` dispara un fetch adicional (mismo mecanismo idle→loading→
// ready/error de siempre, sin infraestructura de cache nueva).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();

const callsByPerspectiva = {}; // "mantenimiento" | "proyecto" -> count
let aniosCalls = 0;
let rentabilidadCalls = 0;

function fakeFetch(url, opts) {
  const params = new URLSearchParams((opts && opts.body) || "");
  if (url === "/operaciones/reporting/integral/data/cartera") {
    const persp = params.get("perspectiva");
    callsByPerspectiva[persp] = (callsByPerspectiva[persp] || 0) + 1;
    if (persp === "mantenimiento") {
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
    aniosCalls++;
    return Promise.resolve({ json: () => Promise.resolve({
      state: "ok", aniosDisponibles: [{ anio: "2026", estado: "En curso" }],
    })});
  }
  if (url === "/operaciones/reporting/integral/data/rentabilidad") {
    rentabilidadCalls++;
    return Promise.resolve({ json: () => Promise.resolve({
      state: "ok",
      rentabilidad: { proyectos: [], noAsignadasMonthly: Array(12).fill(0), noAsignadasByYear: {} },
    })});
  }
  if (url === "/operaciones/reporting/integral/data/rentabilidad/anios-disponibles") {
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok", aniosDisponibles: [] }) });
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

const { render } = sandbox;
const state = vm.runInContext("state", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
function assertFalse(cond, label) { assertTrue(!cond, label); }

async function flush() {
  await new Promise(setImmediate);
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}

async function main() {
  // ==========================================================================
  // A) BOOTSTRAP — Cartera/Mantenimiento es la experience+perspective activa
  //    por default. Debe iniciar SOLO Mantenimiento + su metadata de años.
  //    Proyecto NUNCA se solicita.
  // ==========================================================================
  state.experience = "cartera";
  state.perspective.cartera = "Mantenimiento";
  render();
  await flush();

  assertTrue(callsByPerspectiva["mantenimiento"] === 1, "A: Mantenimiento solicitado exactamente una vez en el bootstrap");
  assertFalse(callsByPerspectiva["proyecto"], "A: Proyecto NUNCA se solicita en el bootstrap");
  assertTrue(aniosCalls === 1, "A: metadata de años de Mantenimiento se solicita según el contrato actual");
  assertTrue(state.carteraMantenimientoStatus === "ready", "A: Mantenimiento terminó ready tras el fetch");
  assertTrue(state.carteraProyectoStatus === "idle", "A: Proyecto sigue idle — nunca se disparó");

  // ==========================================================================
  // E) REPEATED RENDER — mientras el status ya no es "idle" (loading/ready),
  //    renderizar de nuevo NUNCA duplica requests.
  // ==========================================================================
  render(); render(); render();
  await flush();
  assertTrue(callsByPerspectiva["mantenimiento"] === 1, "E: repeated render NO duplica Mantenimiento");
  assertFalse(callsByPerspectiva["proyecto"], "E: repeated render sigue sin disparar Proyecto");
  assertTrue(aniosCalls === 1, "E: repeated render NO duplica la metadata de años");

  // ==========================================================================
  // B) SWITCH A PROYECTO — recién ahí debe iniciar Proyecto, exactamente una
  //    vez.
  // ==========================================================================
  state.perspective.cartera = "Proyecto";
  render();
  await flush();

  assertTrue(callsByPerspectiva["proyecto"] === 1, "B: Proyecto solicitado exactamente una vez al conmutar la perspectiva");
  assertTrue(state.carteraProyectoStatus === "ready", "B: Proyecto terminó ready tras el fetch");
  // Mantenimiento no se re-solicita por el solo hecho de conmutar.
  assertTrue(callsByPerspectiva["mantenimiento"] === 1, "B: Mantenimiento no se re-solicita al conmutar a Proyecto");

  // ==========================================================================
  // C) REVISIT MANTENIMIENTO (ya ready) — CERO refetch.
  // ==========================================================================
  state.perspective.cartera = "Mantenimiento";
  render();
  render();
  await flush();
  assertTrue(callsByPerspectiva["mantenimiento"] === 1, "C: revisitar Mantenimiento ya ready -> CERO refetch");

  // ==========================================================================
  // D) REVISIT PROYECTO (ya ready) — CERO refetch.
  // ==========================================================================
  state.perspective.cartera = "Proyecto";
  render();
  render();
  await flush();
  assertTrue(callsByPerspectiva["proyecto"] === 1, "D: revisitar Proyecto ya ready -> CERO refetch");

  // ==========================================================================
  // F) NO REGRESSION — otras experiencias siguen hidratando con sus triggers
  //    actuales (este corrective NO las toca).
  // ==========================================================================
  state.experience = "rentabilidad";
  render();
  await flush();
  assertTrue(rentabilidadCalls === 1, "F: Rentabilidad sigue hidratando normalmente (sin regresión de este corrective)");

  if (failures > 0) {
    console.log(`${failures} cartera active-perspective-only lifecycle invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL CARTERA ACTIVE-PERSPECTIVE-ONLY LIFECYCLE INVARIANTS PASSED");
  process.exit(0);
}

main().catch(err => { console.log("FAIL: uncaught error: " + err.stack); process.exit(1); });
