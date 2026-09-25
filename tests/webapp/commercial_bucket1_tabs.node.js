// commercial_bucket1_tabs.node.js — invocado por test_commercial_bucket1_tabs.py.
// Owner ticket "COMMERCIAL TABS — BUCKET 1: LICENCIAS + HISTORIAL PERDIDAS
// + DETALLE OPORTUNIDADES" (ver HANDOFF). Ejecuta el MOTOR REAL de
// commercial.js — MISMA técnica que commercial_analytics_scoped_retry.node.js
// (fetch swappable + `root` con centinela + hosts falsos + delegados
// reales) — para probar lo que los tests de Python (source-level) no
// pueden: el filtro CLIENT-SIDE de Licencias sobre un universo NO
// degenerado, la carga LAZY compartida entre Licencias/Detalle (una sola
// request para las dos tabs), zero-refetch en filtros/orden locales, y el
// reintento escopeado con single-flight.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const ROOT_SENTINEL = "<!--root-intacto-->";
const hosts = Object.create(null);
const fetchCalls = [];
let fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });

const root = {
  innerHTML: ROOT_SENTINEL,
  querySelector(sel) { return hosts[sel] || null; },
};

const listeners = Object.create(null);
const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById(id) { return id === "commercial-main-content" ? root : null; },
    querySelector() { throw new Error("commercial.js nunca debe usar document.querySelector"); },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch(url) { fetchCalls.push(url); return fetchImpl(url); },
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

const {
  commercialLicenciasAllRows, commercialDetalleAllRows, commercialSetCapability,
  commercialLicenciasHtml, commercialDetalleHtml, commercialLoadOpportunitiesDetail,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

(listeners.DOMContentLoaded || []).forEach(cb => cb());

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));

// Universo NO degenerado, deliberadamente atravesando VARIOS estados de
// negocio (owner §18 — "el test debe fallar si alguien reduce Licencias a
// 'sólo oportunidades activas'"): prospecto, activa, suspendida y ganada,
// TODAS con licencias="Si", + una activa CON licencias="No" y una SIN
// property. Si Licencias se redujera a "activas únicamente" o "sólo
// licencias='Si' Y activa", este fixture lo expone: el prospecto y la
// ganada desaparecerían del universo esperado.
const OPP_DETAIL_PAYLOAD = {
  opportunities: [
    { opportunity: "Prospecto Con Licencia", client: "A", seller: "Ana", stage: "0 Prospección",
      tags: [], licencias: "Si", hours: 10, weighted_hours: 1, probability: 10, priority: "0", activities: [] },
    { opportunity: "Activa Sin Licencia", client: "B", seller: "Ana", stage: "2 Relevamiento",
      tags: [], licencias: "No", hours: 20, weighted_hours: 8, probability: 40, priority: "0", activities: [] },
    { opportunity: "Suspendida Con Licencia", client: "C", seller: "Beto", stage: "5 Suspendido",
      tags: ["VIP"], licencias: "Si", hours: 30, weighted_hours: 6, probability: 20, priority: "0", activities: [] },
    { opportunity: "Ganada Con Licencia", client: "D", seller: "Beto", stage: "8 Cierre",
      tags: [], licencias: "Si", hours: 40, weighted_hours: 40, probability: 100, priority: "3", activities: [] },
    { opportunity: "Sin Property Licencias", client: "E", seller: "Ana", stage: "1 Contacto",
      tags: [], licencias: "Sin definir", hours: 5, weighted_hours: 0, probability: 5, priority: "0", activities: [] },
  ],
  total: 5,
};

function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.capability = "dashboard";
  commercialState.opportunitiesDetail = null;
  commercialState.opportunitiesDetailStatus = "idle";
  commercialState.opportunitiesDetailErrorState = "";
  commercialState.licSearchText = ""; commercialState.licStageFilter = ""; commercialState.licSellerFilter = "";
  commercialState.licensingProfile = null;
  commercialState.licensingProfileStatus = "idle";
  commercialState.licensingProfileErrorState = "";
  commercialState.licensingDrilldownAccountId = null;
  commercialState.licensingDrilldownOrigin = null;
  commercialState.detSearchText = ""; commercialState.detStageFilter = ""; commercialState.detSellerFilter = "";
  Object.assign(commercialState, patch || {});
}

// ═══ A) Licencias — oráculo del filtro client-side, universo NO degenerado ═
// Owner "Final Business Semantics Realignment" (ver HANDOFF): el selector
// "Licenciamiento" se retiró — `commercialLicenciasAllRows` vuelve a
// aplicar el predicado FIJO `licencias === "Si"`, MISMO invariante
// original.
{
  resetState({ opportunitiesDetail: OPP_DETAIL_PAYLOAD, opportunitiesDetailStatus: "ready" });
  const licRows = commercialLicenciasAllRows();
  const names = licRows.map(r => r.opportunity).sort();
  assertTrue(names.join("|") === ["Ganada Con Licencia", "Prospecto Con Licencia", "Suspendida Con Licencia"].sort().join("|"),
    `Licencias == licencias==="Si", atravesando prospecto/activa/suspendida/ganada (got ${names.join(",")})`);
  assertTrue(!names.includes("Activa Sin Licencia"), 'licencias="No" queda afuera');
  assertTrue(!names.includes("Sin Property Licencias"), '"Sin definir" queda afuera (nunca "Si" laxo)');
  // Detalle Oportunidades: el universo COMPLETO, sin el filtro de Licencias.
  const detRows = commercialDetalleAllRows();
  assertTrue(detRows.length === 5, "Detalle Oportunidades muestra las 5 filas, sin filtrar por Licencias");
}

// ═══ B) Carga LAZY compartida: Licencias y Detalle reusan la MISMA request
// de `opportunitiesDetail` — el perfil de licenciamiento (Account Licensing
// Profile, ver HANDOFF) es un SEGUNDO endpoint/estado independiente que
// SOLO Licencias dispara (nunca Detalle, que no lo usa) ═══
(async () => {
  await tick(); // drena el bootstrap inicial del DOMContentLoaded de arriba

  resetState();
  hosts["[data-commercial-analytics-body]"] = { innerHTML: "" }; // host de otra capability, no debe interferir
  fetchCalls.length = 0;
  fetchImpl = url => Promise.resolve({ json: () => Promise.resolve(
    url.includes("/licensing-priority")
      ? { state: "ok", deferred_account_ids: [] }
      : url.includes("/licensing-profile")
      ? { state: "ok", licensing_profile: { accounts: [], total: 0 } }
      : { state: "ok", opportunities_detail: OPP_DETAIL_PAYLOAD }
  ) });

  // Abrir Licencias PRIMERO dispara LAS TRES cargas (opportunities-detail +
  // licensing-profile + licensing-priority, Director §5-§8, ver HANDOFF) —
  // independientes entre sí, nunca una sola request compartida.
  commercialSetCapability("licencias");
  assertTrue(commercialState.opportunitiesDetailStatus === "loading", "abrir Licencias entra en loading (opportunities-detail)");
  assertTrue(commercialState.licensingProfileStatus === "loading", "y también el perfil de licenciamiento");
  assertTrue(commercialState.licensingPriorityStatus === "loading", "y también la clasificación de prioridad");
  await tick();
  assertTrue(fetchCalls.length === 3
    && fetchCalls.includes("/comercial/data/opportunities-detail?tipo=todos")
    && fetchCalls.includes("/comercial/data/licensing-profile?tipo=todos")
    && fetchCalls.includes("/comercial/data/licensing-priority"),
    `TRES requests independientes al abrir Licencias (got ${fetchCalls.join(",")})`);
  assertTrue(commercialState.opportunitiesDetailStatus === "ready" && commercialState.licensingProfileStatus === "ready"
    && commercialState.licensingPriorityStatus === "ready",
    "las tres quedan ready");

  // Abrir Detalle DESPUÉS reusa el `opportunitiesDetail` ya cargado — cero
  // request nueva (ni de ese endpoint ni del perfil/prioridad, que Detalle
  // no usa).
  commercialSetCapability("detalle");
  assertTrue(fetchCalls.length === 3, "Detalle Oportunidades NO dispara ninguna request nueva — mismo dataset ya cargado");
  assertTrue(commercialDetalleHtml().includes("Ganada Con Licencia"), "y renderiza con el dataset ya en memoria");

  // Volver a Licencias: tampoco refetch (las tres ya están ready).
  commercialSetCapability("licencias");
  assertTrue(fetchCalls.length === 3, "volver a Licencias tampoco refetch");

  // ═══ C) Historial Perdidas — endpoint y estado TOTALMENTE separados ═══
  resetState();
  commercialState.lostHistory = null;
  commercialState.lostHistoryStatus = "idle";
  fetchCalls.length = 0;
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", lost_history: { opportunities: [], total: 0 } }) });
  commercialSetCapability("historial-perdidas");
  await tick();
  assertTrue(fetchCalls.length === 1 && fetchCalls[0] === "/comercial/data/lost-history?tipo=todos",
    "Historial Perdidas pide su PROPIO endpoint — nunca opportunities-detail");
  assertTrue(commercialState.opportunitiesDetailStatus === "idle",
    "y NO toca el estado de Licencias/Detalle — universos totalmente separados");

  // ═══ D) Filtros/orden/búsqueda locales — CERO request ═══
  resetState({ opportunitiesDetail: OPP_DETAIL_PAYLOAD, opportunitiesDetailStatus: "ready" });
  hosts["[data-commercial-licencias-table]"] = { innerHTML: "" };
  fetchCalls.length = 0;
  root.innerHTML = ROOT_SENTINEL;
  const fireInput = (sel, value) => (listeners.input || []).forEach(cb => cb({
    target: { closest: s => (s === sel ? { value } : null) },
  }));
  fireInput("[data-commercial-licencias-search]", "Ganada");
  assertTrue(commercialState.licSearchText === "Ganada", "Buscar actualiza el estado local");
  assertTrue(hosts["[data-commercial-licencias-table]"].innerHTML.includes("Ganada Con Licencia")
    && !hosts["[data-commercial-licencias-table]"].innerHTML.includes("Suspendida Con Licencia"),
    "y re-renderiza SÓLO la tabla, filtrando client-side");
  assertTrue(fetchCalls.length === 0, "cero requests por buscar");
  assertTrue(root.innerHTML === ROOT_SENTINEL, "sin render completo — el input no pierde foco");

  const fireClick = sel => (listeners.click || []).forEach(cb => cb({
    target: { closest: s => (s === sel ? { dataset: { commercialLicenciasSort: "opportunity" }, closest: () => null } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
  fireClick("[data-commercial-licencias-sort]");
  assertTrue(commercialState.licSortKey === "opportunity", "orden de columna actualiza el estado local");
  assertTrue(fetchCalls.length === 0, "y tampoco pide nada");

  // ═══ E) Reintento escopeado — single-flight, endpoint único ═══
  resetState({ opportunitiesDetailStatus: "error", opportunitiesDetailErrorState: "source_error" });
  hosts["[data-commercial-analytics-body]"] = { innerHTML: "" };
  fetchCalls.length = 0;
  fetchImpl = () => new Promise(() => {}); // in-flight indefinido
  const retry = () => (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-retry]" ? { dataset: { commercialRetry: "opportunities-detail" } } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
  retry(); retry(); retry();
  assertTrue(fetchCalls.length === 1, `TRES clicks en Reintentar mientras carga -> una sola request (got ${fetchCalls.length})`);
  assertTrue(!fetchCalls.some(u => /summary|pipeline|activities|analytics|lost-history/.test(u)),
    "y NUNCA arrastra otros endpoints (summary/pipeline/activities/analytics/lost-history)");

  finish();
})();

function finish() {
  if (failures) {
    console.log(`\n${failures} invariante(s) fallaron`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL BUCKET 1 TABS INVARIANTS PASSED");
}
