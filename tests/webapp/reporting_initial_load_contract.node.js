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
  // HubForegroundProgress vive en app.js, no en reporting.js (mismo motivo
  // que `initDashboardSections` arriba) — stub no-op suficiente para este
  // contrato (no prueba el boundary en sí, ver test_hub_estimated_progress.py).
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  // `vm.createContext` no inyecta timers de browser/Node globalScope por
  // default — stubs no-op (este contrato no depende de que el timer
  // realmente dispare, ver test_hub_estimated_progress.py para eso).
  setInterval() { return 0; },
  clearInterval() {},
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { render, capacidadPanelAnioKey } = sandbox;
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);
const CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR = vm.runInContext("CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR", sandbox);

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
// HOT WINDOW/COLD HISTORY corrective (owner decision 2026-08-18): capacidad
// ahora TAMBIÉN gatea el contenido por año hidratado (MISMO criterio que
// Cartera/Rentabilidad/HV, ver `capacidadLoadedYears()`/renderCapacidad en
// reporting.js) — simular que el año 2019 (seleccionado en ambos paneles,
// ver arriba) YA fue hidratado, igual que un `loadBatchBData()` real
// poblaría este centinela junto con las filas.
Object.assign(CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR, { "2019": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] });
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

// ============================================================================
// 7) COMERCIAL / PARTICIPACIÓN — la PRIMERA hidratación depende del dataset de
//    la PERSPECTIVA ACTIVA (Participación), NUNCA de batch-a/Salud. Owner live
//    smoke: batch-a (Salud/Calendario) ready revelaba filtros+feedback con
//    Participación (perspectiva por defecto) todavía en "Cargando…".
// ============================================================================
const FEEDBACK_MARK = "section-feedback";

// Case A — PRIMERA visita: Participación loading, batch-a (Salud) YA ready.
// Repro exacta del bug: antes `combine(...)` devolvía "ready" y hidrataba el tab.
state.experience = "comercial";
state.perspective.comercial = "Participación";
state.comercialParticipacionDataStatus = "loading";
state.batchADataStatus = "ready";
state.reportingTabHydrated.comercial = false;
render();
assertTrue(mainEl.innerHTML.includes(LOADER_MARK), "COMERCIAL A: loader inicial visible");
assertFalse(mainEl.innerHTML.includes(FILTERBAR_MARK), "COMERCIAL A: filtros AUSENTES (batch-a/Salud ready NO hidrata el tab)");
assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "COMERCIAL A: feedback AUSENTE durante la primera hidratación");
assertFalse(mainEl.innerHTML.includes(ERROR_MARK), "COMERCIAL A: no es estado de error (Participación sigue cargando)");
assertFalse(state.reportingTabHydrated.comercial, "COMERCIAL A: el tab NO se marca hidratado con solo batch-a ready");

// Case B — Participación (perspectiva activa) ready → primer paint completo.
state.comercialParticipacionDataStatus = "ready";
render();
assertTrue(state.reportingTabHydrated.comercial, "COMERCIAL B: el tab se hidrata cuando Participación (activa) está ready");
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "COMERCIAL B: loader inicial desaparece");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "COMERCIAL B: filtros visibles");
assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "COMERCIAL B: feedback visible (misma superficie que filtros/contenido)");

// Case C — año frío de Participación tras hidratar: NO re-entra al loader-only.
state.comercialParticipacionDataStatus = "loading";
render();
assertTrue(state.reportingTabHydrated.comercial, "COMERCIAL C: flag one-way sigue hidratado");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "COMERCIAL C: filtros permanecen durante el año frío");
// FEEDBACK VISIBILITY corrective (owner decision 2026-08-19): feedback ya
// NO sigue al flag one-way — sigue el status PRESENTE de la perspectiva
// activa (`isActiveReportReady`). Con Participación "loading" (año frío en
// vuelo), feedback se OCULTA, aunque filtros/contenido permanezcan.
assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "COMERCIAL C: feedback OCULTO durante el año frío (status activo = loading)");
state.comercialParticipacionDataStatus = "ready";

// Case D — switch a Salud de Cliente tras hidratar: sin blanking.
state.perspective.comercial = "Salud de Cliente";
render();
assertTrue(state.reportingTabHydrated.comercial, "COMERCIAL D: sigue hidratado al conmutar a Salud");
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "COMERCIAL D: no vuelve al loader inicial al conmutar de perspectiva");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "COMERCIAL D: filtros siguen visibles en Salud");

// Case E — volver a un Comercial ya hidratado: superficie inmediata (sin loader).
state.experience = "rentabilidad";
render();
state.experience = "comercial";
state.perspective.comercial = "Participación";
render();
assertFalse(mainEl.innerHTML.includes(LOADER_MARK), "COMERCIAL E: volver a un tab ya hidratado no re-muestra el loader inicial");
assertTrue(mainEl.innerHTML.includes(FEEDBACK_MARK), "COMERCIAL E: superficie (feedback/filtros) inmediata al volver");

// Case F (auditoría del mismo invariante) — tras hidratar, conmutar a la
// perspectiva hermana (Salud) con su dataset (batch-a) TODAVÍA cargando NO
// oculta la filter bar (renderComercialFilterBar no gatea por status).
state.perspective.comercial = "Salud de Cliente";
state.batchADataStatus = "loading";
render();
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "COMERCIAL F: filter bar PRESENTE con Salud (hermana) en carga, post-hidratación");
// FEEDBACK VISIBILITY corrective: Salud (perspectiva ACTIVA) está
// "loading" -> feedback OCULTO, aunque la filter bar (que no gatea por
// status) permanezca.
assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "COMERCIAL F: feedback OCULTO (Salud, la perspectiva activa, está loading)");
state.batchADataStatus = "ready";

// ============================================================================
// 8) CARTERA — la PRIMERA hidratación gatea por la PERSPECTIVA ACTIVA, NO por
//    "alguna fuente ready" (hardening: se eliminó el `combine` que asumía que
//    Mantenimiento resolvía antes que Proyecto — supuesto de timing, no
//    contrato).
// ============================================================================
// Case A — activa=Mantenimiento loading, Proyecto YA ready → loader solo.
state.experience = "cartera";
state.perspective.cartera = "Mantenimiento";
state.carteraMantenimientoStatus = "loading";
state.carteraProyectoStatus = "ready";
state.reportingTabHydrated.cartera = false;
render();
assertTrue(mainEl.innerHTML.includes(LOADER_MARK), "CARTERA A: loader inicial visible");
assertFalse(mainEl.innerHTML.includes(FILTERBAR_MARK), "CARTERA A: filtros AUSENTES (Proyecto ready NO hidrata si la activa es Mantenimiento)");
assertFalse(state.reportingTabHydrated.cartera, "CARTERA A: NO hidratado con solo Proyecto ready");
// Mantenimiento (activa) ready → superficie hidratada.
state.carteraMantenimientoStatus = "ready";
render();
assertTrue(state.reportingTabHydrated.cartera, "CARTERA A: se hidrata cuando Mantenimiento (activa) está ready");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "CARTERA A: filtros aparecen al hidratar");

// Case B — inversa: activa=Proyecto loading, Mantenimiento ready → loader solo.
state.perspective.cartera = "Proyecto";
state.carteraProyectoStatus = "loading";
state.carteraMantenimientoStatus = "ready";
state.reportingTabHydrated.cartera = false;
render();
assertTrue(mainEl.innerHTML.includes(LOADER_MARK), "CARTERA B (inversa): loader inicial visible");
assertFalse(mainEl.innerHTML.includes(FILTERBAR_MARK), "CARTERA B (inversa): filtros AUSENTES (Mantenimiento ready NO hidrata si la activa es Proyecto)");
assertFalse(state.reportingTabHydrated.cartera, "CARTERA B (inversa): NO hidratado con solo Mantenimiento ready");
state.carteraProyectoStatus = "ready";
render();
assertTrue(state.reportingTabHydrated.cartera, "CARTERA B (inversa): se hidrata cuando Proyecto (activa) está ready");

// Case C — tras la PRIMERA hidratación, conmutar a una perspectiva hermana cuyo
// dataset sigue cargando debe MANTENER la superficie report-level: filter bar +
// feedback presentes, el contenido muestra su loader contextual. NUNCA se
// oculta la filter bar ni se vuelve al layout de primer-loader (owner: "only
// FIRST hydration may hide filters"). Se prueban AMBAS direcciones.
// C.1 — Proyecto (hidratado en B) → Mantenimiento con Mantenimiento loading.
state.perspective.cartera = "Mantenimiento";
state.carteraMantenimientoStatus = "loading";
render();
assertTrue(state.reportingTabHydrated.cartera, "CARTERA C.1: flag one-way sigue hidratado al conmutar a Mantenimiento");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "CARTERA C.1: filter bar PRESENTE pese a Mantenimiento en carga (post-hidratación)");
// FEEDBACK VISIBILITY corrective: Mantenimiento (perspectiva ACTIVA) está
// "loading" -> feedback OCULTO (ya no sigue el flag one-way de hidratación).
assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "CARTERA C.1: feedback OCULTO (Mantenimiento, la perspectiva activa, está loading)");
assertTrue(mainEl.innerHTML.includes(LOADER_MARK), "CARTERA C.1: loader CONTEXTUAL del contenido presente (perspectiva en carga)");
// C.2 — inversa: Mantenimiento ready → Proyecto con Proyecto loading.
state.carteraMantenimientoStatus = "ready";
state.perspective.cartera = "Proyecto";
state.carteraProyectoStatus = "loading";
render();
assertTrue(state.reportingTabHydrated.cartera, "CARTERA C.2 (inversa): sigue hidratado al conmutar a Proyecto");
assertTrue(mainEl.innerHTML.includes(FILTERBAR_MARK), "CARTERA C.2 (inversa): filter bar PRESENTE pese a Proyecto en carga");
assertFalse(mainEl.innerHTML.includes(FEEDBACK_MARK), "CARTERA C.2 (inversa): feedback OCULTO (Proyecto, la perspectiva activa, está loading)");
assertTrue(mainEl.innerHTML.includes(LOADER_MARK), "CARTERA C.2 (inversa): loader contextual del contenido presente");
state.carteraProyectoStatus = "ready";  // deja el estado limpio

if (failures > 0) {
  console.log(`${failures} initial-load contract invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL REPORTING INITIAL-LOAD CONTRACT INVARIANTS PASSED");
process.exit(0);
