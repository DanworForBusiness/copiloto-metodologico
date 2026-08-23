// reporting_capacidad_hotcold_lifecycle.node.js — invocado por
// test_reporting_capacidad_hotcold_lifecycle.py. HOT WINDOW / COLD HISTORY
// corrective (Horas Productivas, owner decision 2026-08-18) — carga
// reporting.js en un sandbox mínimo con un `fetch` FAKE que responde según
// el endpoint/params reales, para probar el LIFECYCLE JS completo que las
// pruebas de backend (call-count vía mocks de microsoft365) no cubren:
// available != loaded, single-flight, reuse sin fetch, selector estable.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();

const ANIOS_DISPONIBLES = ["2021", "2022", "2023", "2024", "2025", "2026"];
const batchBCallsByAnios = {}; // "" (hot) | "2024" | "2023" -> count

function fakeFetch(url, opts) {
  if (url === "/operaciones/reporting/integral/data/batch-b/anios-disponibles") {
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok", aniosDisponibles: ANIOS_DISPONIBLES }) });
  }
  if (url === "/operaciones/reporting/integral/data/batch-b") {
    const params = new URLSearchParams(opts.body);
    const anios = params.get("anios") || "";
    batchBCallsByAnios[anios] = (batchBCallsByAnios[anios] || 0) + 1;
    if (anios === "") {
      // Ventana caliente por defecto: 2025+2026.
      return Promise.resolve({ json: () => Promise.resolve({
        state: "ok",
        horasProductivas: [{ cliente: "YPF", proyecto: "SADI v2", horas: 20, porcentajePart: 12.5, estado: "En curso",
          horasByYear: { "2026": [10,10,10,10,10,10,10,10,10,10,10,10], "2025": [10,10,10,10,10,10,10,10,10,10,10,10] } }],
        horasProductivasNoAsignadas: [],
        horasProductivasDenominador: { "2026": [10,10,10,10,10,10,10,10,10,10,10,10], "2025": [10,10,10,10,10,10,10,10,10,10,10,10] },
      })});
    }
    // Año frío puntual (2024/2023/etc).
    const arr = Array(12).fill(0); arr[4] = 5;
    return Promise.resolve({ json: () => Promise.resolve({
      state: "ok",
      horasProductivas: [{ cliente: "YPF", proyecto: "SADI v2", horas: 5, porcentajePart: 12.5, estado: "En curso",
        horasByYear: { [anios]: arr } }],
      horasProductivasNoAsignadas: [],
      horasProductivasDenominador: { [anios]: arr },
    })});
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

const { render, capacidadPanelAnioKey, capacidadProductivasAnioOptions,
        loadCapacidadAnios, loadBatchBData, loadCapacidadColdYear } = sandbox;
const state = vm.runInContext("state", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
function assertEqSet(actual, expected, label) {
  const a = [...actual].sort().join(",");
  const e = [...expected].sort().join(",");
  assertTrue(a === e, `${label} (expected [${e}], got [${a}])`);
}

async function main() {
  state.experience = "capacidad";
  state.perspective.capacidad = "Productivas";
  state.filters.capacidad = { cliente: "" };
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2026";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2026";
  state.filters.capacidad[capacidadPanelAnioKey("noAsignadas")] = "2026";

  // --- A/B: primera hidratación — catálogo completo, ventana caliente sola.
  await loadBatchBData();
  await loadCapacidadAnios();
  assertEqSet(capacidadProductivasAnioOptions(), ANIOS_DISPONIBLES,
    "D: available years = 2021..2026 aunque loaded inicialmente sea 2025/2026");
  assertTrue((batchBCallsByAnios[""] || 0) === 1, "B: initial batch-b (hot window) fetched exactly once");
  assertTrue(!batchBCallsByAnios["2024"], "B: 2024 NUNCA se descarga en la carga inicial");
  assertTrue(!batchBCallsByAnios["2023"], "B: 2023 NUNCA se descarga en la carga inicial");

  // --- E: seleccionar 2024 (cold) — exactamente un fetch.
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2024";
  render(); // dispara el pendientes-check -> loadCapacidadColdYear("2024")
  await new Promise(setImmediate);
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assertTrue((batchBCallsByAnios["2024"] || 0) === 1, "E: select 2024 -> exactamente UN fetch frío");

  // --- F: reseleccionar/reusar 2024 -> cero fetch adicional.
  render();
  render();
  await Promise.resolve(); await Promise.resolve();
  assertTrue((batchBCallsByAnios["2024"] || 0) === 1, "F: reusar 2024 ya cargado -> CERO fetch adicional");

  // --- G: selector sigue completo después del merge.
  assertEqSet(capacidadProductivasAnioOptions(), ANIOS_DISPONIBLES,
    "G: el selector sigue mostrando 2021..2026 después de mergear 2024");

  // --- H: single-flight — dos intentos concurrentes del mismo año frío.
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2023";
  const p1 = loadCapacidadColdYear("2023");
  const p2 = loadCapacidadColdYear("2023"); // concurrente, mismo año, ANTES de que p1 resuelva
  await Promise.all([p1, p2]);
  assertTrue((batchBCallsByAnios["2023"] || 0) === 1,
    "H: single-flight -> 2 intentos concurrentes del mismo año generan 1 SOLA request");

  if (failures > 0) {
    console.log(`${failures} capacidad hot/cold lifecycle invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL CAPACIDAD HOT/COLD LIFECYCLE INVARIANTS PASSED");
  process.exit(0);
}

main().catch(err => { console.log("FAIL: uncaught error: " + err.stack); process.exit(1); });
