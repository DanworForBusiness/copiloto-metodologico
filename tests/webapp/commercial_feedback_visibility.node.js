// commercial_feedback_visibility.node.js — invocado por
// test_commercial_feedback_visibility.py. Owner corrective "Homologación al
// Danwor Hub — Feedback transversal" (ver HANDOFF). MISMA técnica que
// commercial_bucket1_tabs.node.js/commercial_bucket2_tabs.node.js/
// reporting_feedback_visibility.node.js: motor REAL de commercial.js vía
// `vm.runInContext`, para probar lo que los tests de Python (source-level)
// no pueden — visibilidad del feedback gateada por el status PRESENTE de
// CADA capability (MISMO criterio que `isActiveReportReady` en
// reporting.js), exactamente UN control por capability, sin duplicarse al
// reabrir/filtrar/ordenar/cambiar de sub-tab, sin fetches de datasets
// adicionales al reaccionar, y sin romper el lazy-loading ya probado por
// los harnesses de Bucket 1/2.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const hosts = Object.create(null);
const root = { innerHTML: "", querySelector(sel) { return hosts[sel] || null; } };
const fetchCalls = [];
const feedbackPosts = [];

// Payloads mínimos "ok" por dataset — sólo lo que cada renderer necesita
// para no reventar (no es un fixture de negocio, es infraestructura de test).
const DATASETS = {
  summary: { state: "ok", summary: {
    tipo: "todos", labels: { nn: "New Name", bi: "Base Instalada" },
    prospectos: { count: 0, hours: 0 }, pipeline: { hours: 0, weighted_hours: 0 },
    activas: { count: 0, hours: 0 }, ganadas: { count: 0, hours: 0 },
    perdidas: { count: 0, hours: 0 }, suspendidas: { count: 0, hours: 0 },
    conversion: 0, conversion_with_suspended: 0,
  } },
  pipeline: { state: "ok", pipeline: { tipo: "todos", opportunities: 0, revenue: 0, stages: [] } },
  analytics: { state: "ok", analytics: {
    creadas: { this_week: 0, last_week: 0 }, pipeline_age: { avg_days: 0, count: 0 },
    stage_time: { avg_days: 0, stages_with_data: 0 }, stage_conversion: { min_rate: 0, steps: 0 },
    close_time: { avg_days: 0, count: 0 }, stagnant: { count: 0, threshold_days: 30 },
    lead_conversion: { companies: 0, any_conv: 0, rate_any_conv: 0, adv_opp: 0, rate_adv_opp: 0 },
  } },
  opportunities: { state: "ok", opportunities: [], total: 0 },
  activities: { state: "ok", activities: [], total: 0 },
  "opportunities-detail": { state: "ok", opportunities_detail: { opportunities: [], total: 0 } },
  "licensing-profile": { state: "ok", licensing_profile: { accounts: [], total: 0 } },
  "lost-history": { state: "ok", lost_history: { opportunities: [], total: 0 } },
  clients: { state: "ok", clients: { clients: [], total: 0 } },
  "ex-clients": { state: "ok", ex_clients: { clients: [], total: 0 } },
  leads: { state: "ok", leads: { companies: [], total_companies: 0, total_leads: 0 } },
};
const failOnce = Object.create(null); // { key: true } -> la PRÓXIMA fetch de esa key falla una vez

function fakeFetch(url, opts) {
  fetchCalls.push(url);
  if (url.startsWith("/comercial/feedback")) {
    const params = new URLSearchParams((opts && opts.body) || "");
    feedbackPosts.push(params.get("capability") + ":" + params.get("rating"));
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok" }) });
  }
  const m = url.match(/^\/comercial\/data\/([a-z-]+)/);
  const key = m && m[1];
  if (key && DATASETS[key]) {
    if (failOnce[key]) {
      failOnce[key] = false;
      return Promise.resolve({ json: () => Promise.resolve({ state: "source_error", stage: "x" }) });
    }
    return Promise.resolve({ json: () => Promise.resolve(DATASETS[key]) });
  }
  return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });
}

const listeners = Object.create(null);
const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById(id) { return id === "commercial-main-content" ? root : null; },
    querySelector() { throw new Error("commercial.js nunca debe usar document.querySelector"); },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch: fakeFetch,
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  initDraggableColumns() {},
  readStoredColumnOrder() { return null; },
  readStoredColumnWidths() { return null; },
  initResizableColumns() {},
  // `URLSearchParams` NO es un global de V8/vm — hay que pasarlo explícito
  // (MISMO gap que ya resolvió reporting_feedback_visibility.node.js).
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const { commercialSetCapability, commercialSetClientesSubTab } = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
const tick = () => new Promise(resolve => setImmediate(resolve));
async function flush() { await tick(); await tick(); await tick(); }

const MARK = "section-feedback";
function feedbackCount() { return (root.innerHTML.match(/data-commercial-feedback="/g) || []).length; }
function hasFeedbackFor(cap) { return root.innerHTML.includes(`data-commercial-feedback="${cap}"`); }

// Fire un click REAL a través del delegado real de commercial.js —
// MISMO patrón que los harnesses de Bucket 1/2 (`event.target.closest`
// acotado al selector pedido, el resto del delegado ve `null` y no
// interfiere).
function fireClick(sel, dataset) {
  (listeners.click || []).forEach(cb => cb({
    target: { closest: s => (s === sel ? { dataset: dataset || {}, closest: () => null } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
}

async function main() {
  // ══ A) Bootstrap Dashboard — mientras carga, feedback OCULTO ══
  (listeners.DOMContentLoaded || []).forEach(cb => cb()); // dispara commercialLoadData() -> loading síncrono
  assertTrue(commercialState.status === "loading", "A: setup — bootstrap entra en loading");
  assertFalse(root.innerHTML.includes(MARK), "A: Dashboard LOADING -> feedback OCULTO");
  await flush();

  // ══ B) Dashboard ready — feedback VISIBLE, exactamente UNO ══
  assertTrue(commercialState.status === "ready", "B: setup — Dashboard terminó ready");
  assertTrue(hasFeedbackFor("dashboard"), "B: Dashboard READY -> feedback VISIBLE con identidad 'dashboard'");
  assertTrue(feedbackCount() === 1, `B: exactamente UN feedback (got ${feedbackCount()})`);

  // ══ C) Switch a Actividades (loading) — feedback OCULTO ══
  fetchCalls.length = 0;
  commercialSetCapability("actividades");
  assertTrue(commercialState.activitiesStatus === "loading", "C: setup — Actividades entra en loading");
  assertFalse(root.innerHTML.includes(MARK), "C: Actividades LOADING -> feedback OCULTO pese a Dashboard ready en background");
  await flush();

  // ══ D) Actividades ready — feedback VISIBLE, identidad 'actividades' ══
  assertTrue(commercialState.activitiesStatus === "ready", "D: setup — Actividades terminó ready");
  assertTrue(hasFeedbackFor("actividades"), "D: Actividades READY -> feedback VISIBLE con identidad propia");
  assertTrue(feedbackCount() === 1, "D: sigue siendo exactamente UNO (nunca se acumula el de Dashboard)");

  // ══ E) Licencias/Detalle comparten loader — CADA UNA su PROPIO feedback ══
  fetchCalls.length = 0;
  commercialSetCapability("licencias");
  assertTrue(commercialState.opportunitiesDetailStatus === "loading", "E: setup — Licencias entra en loading");
  assertFalse(root.innerHTML.includes(MARK), "E: Licencias LOADING -> feedback OCULTO");
  await flush();
  assertTrue(commercialState.opportunitiesDetailStatus === "ready", "E: setup — queda ready");
  assertTrue(hasFeedbackFor("licencias"), "E: Licencias READY -> feedback propio 'licencias'");
  assertTrue(feedbackCount() === 1, "E: exactamente UNO");

  // Detalle Oportunidades reusa el MISMO dataset (ya ready) — feedback
  // VISIBLE DE INMEDIATO con IDENTIDAD DISTINTA ('detalle', no 'licencias'),
  // y CERO fetch nuevo (paridad con el harness de Bucket 1).
  fetchCalls.length = 0;
  commercialSetCapability("detalle");
  assertTrue(hasFeedbackFor("detalle"), "E: Detalle Oportunidades -> feedback propio 'detalle', identidad DISTINTA de Licencias");
  assertFalse(hasFeedbackFor("licencias"), "E: y NUNCA el de Licencias (paneles distintos, nunca se mezclan)");
  assertTrue(feedbackCount() === 1, "E: exactamente UNO en Detalle también");
  assertTrue(fetchCalls.length === 0, "E: reusar el dataset compartido no dispara ninguna request nueva");

  // ══ F) Historial Perdidas — endpoint propio, feedback propio ══
  commercialSetCapability("historial-perdidas");
  assertTrue(commercialState.lostHistoryStatus === "loading", "F: setup — Historial entra en loading");
  assertFalse(root.innerHTML.includes(MARK), "F: Historial Perdidas LOADING -> feedback OCULTO");
  await flush();
  assertTrue(hasFeedbackFor("historial-perdidas"), "F: Historial Perdidas READY -> feedback propio");
  assertTrue(feedbackCount() === 1, "F: exactamente UNO");

  // ══ G) Clientes — feedback lee el status del SUB-TAB ACTIVO ══
  commercialSetCapability("clientes");
  assertTrue(commercialState.clientsStatus === "loading", "G: setup — Clientes entra en loading");
  assertFalse(root.innerHTML.includes(MARK), "G: Clientes LOADING -> feedback OCULTO");
  await flush();
  assertTrue(hasFeedbackFor("clientes"), "G: Clientes READY -> feedback propio 'clientes'");
  assertTrue(feedbackCount() === 1, "G: exactamente UNO");

  // Abrir el sub-tab Ex Clientes (lazy, todavía no cargó) -> feedback
  // OCULTO pese a que Clientes (el status "primario") sigue ready en
  // background — MISMO criterio "perspectiva activa" que Reporting/Cartera.
  commercialSetClientesSubTab("ex-clientes");
  assertTrue(commercialState.exClientsStatus === "loading", "G: setup — Ex Clientes entra en loading");
  assertTrue(commercialState.clientsStatus === "ready", "G: Clientes sigue ready en background (no se resetea)");
  assertFalse(root.innerHTML.includes(MARK), "G: Ex Clientes LOADING -> feedback OCULTO pese a Clientes ready en background");
  await flush();
  assertTrue(hasFeedbackFor("clientes"), "G: Ex Clientes READY -> feedback sigue siendo identidad 'clientes' (Ex Clientes no es su propia capability)");
  assertTrue(feedbackCount() === 1, "G: exactamente UNO");

  // Volver al sub-tab Clientes (ya ready) -> feedback VISIBLE de inmediato.
  commercialSetClientesSubTab("clientes");
  assertTrue(hasFeedbackFor("clientes"), "G: revisitar el sub-tab Clientes (ya ready) -> feedback VISIBLE de inmediato");
  assertTrue(feedbackCount() === 1, "G: exactamente UNO");

  // ══ H) Leads — endpoint propio, feedback propio ══
  commercialSetCapability("leads");
  assertTrue(commercialState.leadsStatus === "loading", "H: setup — Leads entra en loading");
  assertFalse(root.innerHTML.includes(MARK), "H: Leads LOADING -> feedback OCULTO");
  await flush();
  assertTrue(hasFeedbackFor("leads"), "H: Leads READY -> feedback propio");
  assertTrue(feedbackCount() === 1, "H: exactamente UNO");

  // ══ I) Reabrir una capability YA ready -> feedback VISIBLE de inmediato,
  //       sin flash de "oculto" transitorio, CERO refetch. ══
  fetchCalls.length = 0;
  commercialSetCapability("dashboard");
  assertTrue(hasFeedbackFor("dashboard"), "I: revisitar Dashboard (ya ready) -> feedback VISIBLE de inmediato");
  commercialSetCapability("leads");
  assertTrue(hasFeedbackFor("leads"), "I: revisitar Leads (ya ready) -> feedback VISIBLE de inmediato");
  assertTrue(fetchCalls.length === 0, "I: reabrir capabilities ya ready no dispara NINGUNA request");

  // ══ J) Filtros/orden/búsqueda LOCALES no tocan el panel -> feedback NUNCA
  //       se duplica (la actualización es escopeada al host de la tabla,
  //       nunca re-renderiza el panel completo donde vive el feedback). ══
  hosts["[data-commercial-leads-table]"] = { innerHTML: "" };
  const beforeSearch = root.innerHTML;
  (listeners.input || []).forEach(cb => cb({
    target: { closest: s => (s === "[data-commercial-leads-search]" ? { value: "Alfa" } : null) },
  }));
  assertTrue(root.innerHTML === beforeSearch, "J: Buscar en Leads NO toca el panel completo — el feedback ni se re-renderiza");
  assertTrue(feedbackCount() === 1, "J: sigue habiendo exactamente UNO tras buscar");

  // ══ K) 👍 en una capability representativa: MISMO mecanismo/endpoint,
  //       identidad correcta, sin fetch de datasets. ══
  fetchCalls.length = 0;
  fireClick("[data-commercial-feedback-submit]",
    { commercialFeedbackSubmit: "leads", commercialFeedbackRating: "up" });
  await flush();
  assertTrue(feedbackPosts.includes("leads:up"), "K: 👍 en Leads postea capability=leads,rating=up al endpoint compartido");
  assertTrue(commercialState.feedback.leads === "up", "K: estado interno pasa a 'up'");
  assertTrue(root.innerHTML.includes("¡Gracias!"), "K: la botonera se reemplaza por el agradecimiento");
  assertTrue(!fetchCalls.some(u => u.startsWith("/comercial/data/")), "K: reaccionar NUNCA dispara un fetch de dataset");

  // ══ L) 👎 abre el formulario de comentario opcional (nunca persiste de
  //       una) en OTRA capability — Dashboard, para probar que cada
  //       capability tiene su PROPIO estado, sin pisarse entre sí. ══
  commercialSetCapability("dashboard");
  assertTrue(hasFeedbackFor("dashboard"), "L: setup — feedback de Dashboard visible");
  fireClick("[data-commercial-feedback-open]", { commercialFeedbackOpen: "dashboard" });
  assertTrue(commercialState.feedback.dashboard === "form", "L: 👎 abre el formulario, todavía no persiste");
  assertTrue(root.innerHTML.includes("¿Qué esperabas o qué faltó acá?"), "L: formulario de comentario visible");
  assertTrue(commercialState.feedback.leads === "up", "L: el 'up' de Leads (paso K) sigue intacto — estados independientes por capability");
  fireClick("[data-commercial-feedback-cancel]", { commercialFeedbackCancel: "dashboard" });
  assertTrue(commercialState.feedback.dashboard === undefined, "L: Cancelar vuelve al estado inicial (sin persistir nada)");

  // ══ M) Estado de ERROR de la capability activa -> feedback OCULTO ══
  commercialSetCapability("actividades"); // ya ready (paso D) -> feedback visible
  assertTrue(hasFeedbackFor("actividades"), "M: setup — Actividades ready, feedback visible");
  commercialState.activitiesStatus = "error";
  const { commercialRenderCapability } = sandbox;
  commercialRenderCapability();
  assertFalse(root.innerHTML.includes(MARK), "M: un error en la capability activa OCULTA el feedback, mismo criterio que loading");
  commercialState.activitiesStatus = "ready";
  commercialRenderCapability();

  // ══ N) Cambio de Tipo invalida el feedback de TODAS las capabilities ══
  commercialState.feedback.dashboard = "up";
  commercialState.feedback.leads = "up";
  const { commercialSetTipo } = sandbox;
  commercialSetTipo("nn");
  assertTrue(Object.keys(commercialState.feedback).length === 0, "N: cambiar Tipo resetea el feedback de TODAS las capabilities");
  commercialSetTipo("todos");

  if (failures > 0) {
    console.log(`\n${failures} invariante(s) de feedback FALLARON`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL FEEDBACK VISIBILITY INVARIANTS PASSED");
  process.exit(0);
}

main().catch(err => { console.log("FAIL: uncaught error: " + err.stack); process.exit(1); });
