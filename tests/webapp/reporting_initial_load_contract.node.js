// reporting_initial_load_contract.node.js — invocado por
// test_reporting_initial_load_contract.py. SHARED REPORTING INITIAL-LOAD
// CONTRACT (owner corrective, ver HANDOFF §-18): carga reporting.js en un
// sandbox mínimo y ejecuta `render()` REAL (no un mock de su HTML) contra
// estado sintético — prueba el comportamiento real de la primera
// hidratación de un tab principal: SOLO tabs + loader inicial hasta que
// hay contenido usable, filtros/contenido entran JUNTOS recién entonces,
// y una carga incremental posterior (año frío, error parcial) NUNCA
// revierte una experiencia ya hidratada a la pantalla de loader inicial.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

// DOM mínimo: dos contenedores con `.innerHTML` asignable (lo único que
// `render()` toca), más un stub no-op de `initDashboardSections` (vive en
// app.js, no en reporting.js — ver comentario en `render()`).
function makeEl() { return { innerHTML: "" }; }
const tabsEl = makeEl();
const mainEl = makeEl();
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
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { render, capacidadPanelAnioKey } = sandbox;
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}
function assertFalse(cond, label) {
  assertTrue(!cond, label);
}

const LOADER_MARK = "section-analysis-loading";
const ERROR_MARK = "empty-panel";
const FILTERBAR_MARK = "dashboard-sticky-header";

// ============================================================================
// 1) FIRST VISIT — tabs visibles, loader inicial SOLO, filtros/contenido
//    ausentes (nunca el flash "filtros + página vacía + loader debajo").
// ============================================================================
state.experience = "rentabilidad";
state.rentabilidadDataStatus = "idle";
state.rentabilidadAniosStatus = "idle";
state.reportingTabHydrated.rentabilidad = false;
render();

assertTrue(tabsEl.innerHTML.includes("Rentabilidad"), "FIRST VISIT: la navegación de tabs principal se renderiza");
assertTrue(mainEl.innerHTML.includes(LOADER_MARK), "FIRST VISIT: se muestra el loader inicial compartido");
assertFalse(mainEl.innerHTML.includes(FILTERBAR_MARK), "FIRST VISIT: la filter bar NO se renderiza todavía");
assertFalse(mainEl.innerHTML.includes(ERROR_MARK), "FIRST VISIT: no es un estado de error (todavía no falló nada)");
// El trigger de hidratación SÍ debe haber corrido (si no, el tab quedaría
// "idle" para siempre) — `loadRentabilidadData()` pasa a "loading" de forma
// SÍNCRONA antes del primer `await`.
assertTrue(state.rentabilidadDataStatus === "loading", "FIRST VISIT: el trigger de hidratación centralizado en el shell SÍ disparó el fetch (status pasó a loading)");
assertFalse(state.reportingTabHydrated.rentabilidad, "FIRST VISIT: el flag de hidratación sigue en false (todavía no llegó ready)");

// ============================================================================
// 2) AFTER FIRST HYDRATION — loader desaparece, filtros + contenido entran
//    JUNTOS como una sola superficie estable.
// ============================================================================
state.experience = "capacidad";
state.perspective.capacidad = "Productivas";
state.batchBDataStatus = "idle";
state.reportingTabHydrated.capacidad = false;
CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
CAPACIDAD_PRODUCTIVAS_ROWS.push({
  cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5,
  horasByYear: { "2019": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10] },
});
state.filters.capacidad = { cliente: "" };
state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";

render();
// Primer render con status "idle": el trigger centralizado (`TAB_HYDRATION_
// TRIGGERS.capacidad`) dispara `loadBatchBData()` — en este sandbox
// `loadBatchBData` es async y muta `state.batchBDataStatus` de forma
// síncrona antes del primer `await` (mismo patrón que Rentabilidad arriba).
// Para probar el estado "ya hidratado" sin depender de esa mecánica async,
// se fuerza el status a "ready" directamente (equivalente a que el fetch ya
// resolvió) y se vuelve a renderizar.
state.batchBDataStatus = "ready";
render();

assertTrue(state.reportingTabHydrated.capacidad, "AFTER FIRST HYDRATION: el flag se prende la primera vez que el status crudo es 'ready'");
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "AFTER FIRST HYDRATION: el loader inicial desaparece");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "AFTER FIRST HYDRATION: la filter bar entra");
assertTrue(mainEl.innerHTML.includes("dashboard-section"), "AFTER FIRST HYDRATION: el contenido del reporte entra (mismo render, junto con los filtros)");

// ============================================================================
// 3) COLD YEAR / SUBSEQUENT LOAD — una vez hidratado, un status crudo que
//    vuelve a "loading"/"error" (año frío, fetch incremental) NUNCA revierte
//    la experiencia al modo "solo loader inicial": la filter bar (marca de
//    que el shell sigue en modo "hidratado") permanece visible. El
//    CONTENIDO puede mostrar su propio fragmento incremental de loading/
//    error inline (comportamiento EXISTENTE y protegido por ticket — ver
//    `renderBatchBDataState`/`renderCarteraDataState` — la garantía NUEVA
//    de este ticket es que el SHELL no colapsa a tabs+loader-solo).
//    Se usa "capacidad" (1 solo status de tab, `batchBDataStatus`, ya
//    hidratado en el paso 2) — su filter bar NO gatea por status (a
//    diferencia de Cartera, que sí lo hace POR PERSPECTIVA — comportamiento
//    preexistente y protegido, no forma parte de este contrato).
state.experience = "capacidad";
assertTrue(state.reportingTabHydrated.capacidad, "COLD YEAR setup: capacidad ya está hidratado (paso 2)");
state.batchBDataStatus = "error";
render();
assertTrue(state.reportingTabHydrated.capacidad, "COLD YEAR: el flag de hidratación NUNCA se apaga (una sola vía)");
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "COLD YEAR: NO se re-entra al modo 'solo loader inicial' tras un error incremental");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "COLD YEAR: la filter bar permanece visible (el shell nunca vuelve a ocultarla)");
state.batchBDataStatus = "ready"; // deja el estado limpio para los pasos siguientes

// ============================================================================
// 4) PERSPECTIVE SWITCH — cambiar de perspectiva dentro de un tab YA
//    hidratado nunca dispara el modo "solo loader inicial".
// ============================================================================
// Hidrata Cartera primero (Mantenimiento) — recién DESPUÉS se cambia de
// perspectiva, para probar el escenario real: "ya hidratado, después
// cambia de vista", no ambos a la vez.
state.experience = "cartera";
state.reportingTabHydrated.cartera = false;
state.carteraMantenimientoStatus = "ready";
state.carteraProyectoStatus = "ready";
state.perspective.cartera = "Mantenimiento";
render();
assertTrue(state.reportingTabHydrated.cartera, "PERSPECTIVE SWITCH setup: Cartera se hidrata en Mantenimiento");

state.perspective.cartera = "Proyecto";
render();
assertTrue(state.reportingTabHydrated.cartera, "PERSPECTIVE SWITCH: el tab sigue marcado como hidratado");
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "PERSPECTIVE SWITCH: nunca vuelve al loader inicial por cambiar de perspectiva");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "PERSPECTIVE SWITCH: filtros siguen visibles");

// ============================================================================
// 5) RETURN TO PREVIOUSLY VISITED TAB — visitar otro tab y volver no
//    re-muestra el loader inicial si el tab de vuelta ya estaba hidratado.
// ============================================================================
state.experience = "rentabilidad"; // ya "loading" desde el paso 1, todavía sin hidratar
render();
state.experience = "cartera"; // ya hidratado en el paso 4
render();
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "RETURN TO TAB: volver a un tab ya hidratado no re-muestra el loader inicial");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "RETURN TO TAB: filtros/contenido aparecen de inmediato al volver");

// ============================================================================
// 6) ERROR — si la PRIMERA hidratación falla, se muestra tabs + estado de
//    error compartido, NUNCA una filter bar "usable" sobre un reporte vacío.
// ============================================================================
state.experience = "horasvendidas";
state.horasVendidasDataStatus = "error";
state.reportingTabHydrated.horasvendidas = false;
render();
assertTrue(mainEl.innerHTML.includes(ERROR_MARK), "ERROR (primera hidratación): se muestra el estado de error compartido");
assertFalse(mainEl.innerHTML.includes(FILTERBAR_MARK), "ERROR (primera hidratación): NUNCA se muestra una filter bar usable sobre un reporte vacío");
assertFalse(state.reportingTabHydrated.horasvendidas, "ERROR (primera hidratación): el flag de hidratación NO se prende con un status de error");

if (failures > 0) {
  console.log(`${failures} initial-load contract invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL REPORTING INITIAL-LOAD CONTRACT INVARIANTS PASSED");
process.exit(0);
