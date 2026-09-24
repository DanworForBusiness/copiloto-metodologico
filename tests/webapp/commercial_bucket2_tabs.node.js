// commercial_bucket2_tabs.node.js — invocado por test_commercial_bucket2_tabs.py.
// Owner ticket "COMMERCIAL TABS — BUCKET 2: CLIENTES + LEADS" (ver HANDOFF).
// Ejecuta el MOTOR REAL de commercial.js — MISMA técnica que
// commercial_bucket1_tabs.node.js (fetch swappable + `root` con centinela +
// hosts falsos + delegados reales) — para probar lo que los tests de Python
// (source-level, universo/grouping oracle) no cubren: la carga LAZY de
// Leads/Clientes (una request al abrir la capability), Ex Clientes LAZY
// DENTRO del sub-tab de Clientes (recién al abrirlo, nunca junto con
// Clientes), zero-refetch en expandir/ordenar/filtrar/buscar locales, el
// reintento escopeado single-flight por dataset, y tipo-safety (descarte de
// respuesta obsoleta si Todos/NN/BI cambió mientras viajaba).
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
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialLeadsAllCompanies, commercialClientesAllRows, commercialExClientesAllRows,
  commercialSetCapability, commercialSetClientesSubTab,
  commercialLeadsHtml, commercialClientesHtml,
  commercialLoadLeads, commercialLoadClients, commercialLoadExClients,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

(listeners.DOMContentLoaded || []).forEach(cb => cb());

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));

// Universos NO degenerados: Leads con DOS empresas reales + "Sin empresa"
// (owner §19 — nunca colapsar "Sin empresa" con una empresa real, y su
// posición/orden ya la prueba el oráculo de Python); Clientes con un
// cliente CON oportunidades activas + un cliente CON licencias pero CERO
// oportunidades abiertas (owner §18 — huérfano sin opps sigue apareciendo);
// Ex Clientes con un solo registro (universo propio, endpoint propio).
const LEADS_PAYLOAD = {
  companies: [
    { id: 1, name: "Alfa SA", leads: [
      { lead: "Lead Alfa 1", contact: "Juan Perez", seller: "Ana", email: "juan@alfa.com", create_date: "2026-01-05" },
      { lead: "Lead Alfa 2", contact: "Maria Gomez", seller: "Beto", email: "maria@alfa.com", create_date: "2026-01-06" },
    ] },
    { id: 0, name: "Sin empresa", leads: [
      { lead: "Lead Suelto", contact: "", seller: "Ana", email: "", create_date: "2026-01-07" },
    ] },
  ],
  total_companies: 2,
  total_leads: 3,
};
const CLIENTS_PAYLOAD = {
  clients: [
    { id: 100, name: "Cliente Uno", sellers: ["Ana"], has_licencias: true,
      active: [{ opportunity: "Op Activa", seller: "Ana", stage: "2 Relevamiento", probability: 40, hours: 10 }],
      prospects: [] },
    { id: 105, name: "Cliente Sin Opps", sellers: [], has_licencias: false, active: [], prospects: [] },
  ],
  total: 2,
};
const EX_CLIENTS_PAYLOAD = {
  clients: [
    { id: 200, name: "Ex Cliente Uno", sellers: ["Beto"], active: [], prospects: [], history: [] },
  ],
  total: 1,
};

function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.capability = "dashboard";
  // `status`/`errorState` del Dashboard NO se tocan acá — igual que
  // commercial_bucket1_tabs.node.js: el bootstrap inicial (arriba) ya los
  // deja en "error"/"config_incomplete" (el `fetchImpl` default resuelve
  // `config_incomplete`), así que un reintento que caiga de vuelta en el
  // panel Dashboard (`capability` sigue en "dashboard" acá, nunca se pasa
  // por `commercialSetCapability`) renderiza su estado de error, no un
  // `commercialDashboardHtml()` con `summary` en null.
  commercialState.leads = null; commercialState.leadsStatus = "idle"; commercialState.leadsErrorState = "";
  commercialState.leadsExpanded = []; commercialState.leadsSearchText = "";
  commercialState.leadsSortKey = null; commercialState.leadsSortDir = 1;
  commercialState.clients = null; commercialState.clientsStatus = "idle"; commercialState.clientsErrorState = "";
  commercialState.clientesSubTab = "clientes"; commercialState.clientesExpanded = [];
  commercialState.clientesSearchText = ""; commercialState.clientesSellerFilter = "";
  commercialState.clientesStatusFilter = "all";
  commercialState.clientesSortKey = null; commercialState.clientesSortDir = 1;
  commercialState.exClients = null; commercialState.exClientsStatus = "idle"; commercialState.exClientsErrorState = "";
  commercialState.exClientesSearchText = ""; commercialState.exClientesSellerFilter = "";
  Object.assign(commercialState, patch || {});
}

function fireClick(sel, dataset) {
  (listeners.click || []).forEach(cb => cb({
    target: { closest: s => (s === sel ? { dataset: dataset || {}, closest: () => null } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
}
function fireInput(sel, value) {
  (listeners.input || []).forEach(cb => cb({ target: { closest: s => (s === sel ? { value } : null) } }));
}
function fireChange(sel, value) {
  (listeners.change || []).forEach(cb => cb({ target: { closest: s => (s === sel ? { value } : null) } }));
}

(async () => {
  await tick(); // drena el bootstrap inicial del DOMContentLoaded de arriba

  // ═══ A) Leads — carga LAZY al abrir la capability, UNA sola request ═══
  resetState();
  fetchCalls.length = 0;
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", leads: LEADS_PAYLOAD }) });
  commercialState.capability = "dashboard"; // set directo, sin pasar por commercialSetCapability (no dispara fetch)
  commercialSetCapability("leads");
  assertTrue(commercialState.leadsStatus === "loading", "abrir Leads entra en loading");
  await tick();
  assertTrue(fetchCalls.length === 1 && fetchCalls[0] === "/comercial/data/leads?tipo=todos",
    `UNA sola request al abrir Leads (got ${fetchCalls.join(",")})`);
  assertTrue(commercialState.leadsStatus === "ready", "queda ready");
  assertTrue(commercialLeadsAllCompanies().length === 2, "el dataset completo queda en memoria (2 empresas)");

  // Reabrir (salir y volver) con el dataset ya ready -> CERO refetch.
  commercialState.capability = "dashboard";
  commercialSetCapability("leads");
  assertTrue(fetchCalls.length === 1, "reabrir Leads con dataset ya ready no repite la request");

  // ═══ B) Leads — expandir/ordenar/buscar: CERO request, re-render acotado ═
  hosts["[data-commercial-leads-table]"] = { innerHTML: "" };
  root.innerHTML = ROOT_SENTINEL;
  fireClick("[data-commercial-leads-toggle]", { commercialLeadsToggle: "1_Alfa SA" });
  assertTrue(commercialState.leadsExpanded.indexOf("1_Alfa SA") !== -1, "toggle de empresa abre su clave en el estado local");
  assertTrue(hosts["[data-commercial-leads-table]"].innerHTML.includes("Lead Alfa 1"),
    "y re-renderiza SÓLO la tabla, mostrando sus leads");
  assertTrue(fetchCalls.length === 1, "expandir una empresa no dispara ninguna request");
  assertTrue(root.innerHTML === ROOT_SENTINEL, "sin render completo — nunca se pierde el resto del panel");

  fireClick("[data-commercial-leads-toggle]", { commercialLeadsToggle: "1_Alfa SA" });
  assertTrue(commercialState.leadsExpanded.indexOf("1_Alfa SA") === -1, "un segundo click colapsa la misma empresa");

  fireClick("[data-commercial-leads-sort]", { commercialLeadsSort: "count" });
  assertTrue(commercialState.leadsSortKey === "count", "orden de columna (Leads) actualiza el estado local");
  assertTrue(fetchCalls.length === 1, "y tampoco pide nada");

  fireInput("[data-commercial-leads-search]", "Alfa");
  assertTrue(commercialState.leadsSearchText === "Alfa", "Buscar actualiza el estado local");
  assertTrue(hosts["[data-commercial-leads-table]"].innerHTML.includes("Alfa SA")
    && !hosts["[data-commercial-leads-table]"].innerHTML.includes("Sin empresa"),
    "y re-renderiza SÓLO la tabla, filtrando client-side (Sin empresa queda afuera de este filtro)");
  assertTrue(fetchCalls.length === 1, "cero requests por buscar");

  // ═══ C) Leads — reintento escopeado: single-flight, endpoint único ═══
  resetState({ leadsStatus: "error", leadsErrorState: "source_error" });
  hosts["[data-commercial-analytics-body]"] = { innerHTML: "" };
  fetchCalls.length = 0;
  fetchImpl = () => new Promise(() => {}); // in-flight indefinido
  const retry = key => (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-retry]" ? { dataset: { commercialRetry: key } } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
  retry("leads"); retry("leads"); retry("leads");
  assertTrue(fetchCalls.length === 1, `TRES clicks en Reintentar Leads mientras carga -> una sola request (got ${fetchCalls.length})`);
  assertTrue(!fetchCalls.some(u => /clients|opportunities-detail|lost-history/.test(u)),
    "y NUNCA arrastra otros endpoints (clients/ex-clients/opportunities-detail/lost-history)");

  // ═══ D) Leads — tipo-safety: respuesta obsoleta se descarta ═══
  resetState();
  fetchCalls.length = 0;
  let resolveFetch;
  fetchImpl = () => new Promise(resolve => { resolveFetch = resolve; });
  const leadsLoadPromise = commercialLoadLeads();
  assertTrue(commercialState.leadsStatus === "loading", "arranca loading con tipo=todos");
  commercialState.tipo = "nn"; // el usuario cambia Tipo mientras la request viaja
  resolveFetch({ json: () => Promise.resolve({ state: "ok", leads: LEADS_PAYLOAD }) });
  await leadsLoadPromise;
  assertTrue(commercialState.leadsStatus === "loading",
    "la respuesta para el tipo VIEJO se descarta silenciosamente — el status sigue esperando el tipo nuevo");
  assertTrue(commercialState.leads === null, "y el dataset NO se pisa con la respuesta obsoleta");

  // ═══ E) Clientes — carga LAZY al abrir, Ex Clientes NO se pide todavía ═
  resetState();
  fetchCalls.length = 0;
  fetchImpl = url => Promise.resolve({
    json: () => Promise.resolve(
      url.startsWith("/comercial/data/clients") ? { state: "ok", clients: CLIENTS_PAYLOAD }
        : url.startsWith("/comercial/data/ex-clients") ? { state: "ok", ex_clients: EX_CLIENTS_PAYLOAD }
        : { state: "config_incomplete" }),
  });
  commercialState.capability = "dashboard";
  commercialSetCapability("clientes");
  assertTrue(commercialState.clientsStatus === "loading", "abrir Clientes entra en loading");
  await tick();
  assertTrue(fetchCalls.length === 1 && fetchCalls[0] === "/comercial/data/clients?tipo=todos",
    `UNA sola request al abrir Clientes (got ${fetchCalls.join(",")})`);
  assertTrue(commercialState.clientsStatus === "ready", "queda ready");
  assertTrue(commercialState.exClientsStatus === "idle",
    "Ex Clientes NO se pide junto con Clientes — sigue idle hasta que se abra ese sub-tab");
  assertTrue(commercialClientesAllRows().length === 2,
    "el dataset completo queda en memoria, INCLUYENDO el cliente con licencias y cero opps abiertas");

  // Reabrir Clientes con el dataset ya ready -> CERO refetch.
  commercialState.capability = "dashboard";
  commercialSetCapability("clientes");
  assertTrue(fetchCalls.length === 1, "reabrir Clientes con dataset ya ready no repite la request");

  // ═══ F) Ex Clientes — LAZY dentro del sub-tab, una sola vez ═══
  commercialSetClientesSubTab("ex-clientes");
  assertTrue(commercialState.exClientsStatus === "loading", "abrir el sub-tab Ex Clientes entra en loading");
  await tick();
  assertTrue(fetchCalls.length === 2 && fetchCalls[1] === "/comercial/data/ex-clients?tipo=todos",
    `abrir el sub-tab dispara SU PROPIA request, separada de Clientes (got ${fetchCalls.join(",")})`);
  assertTrue(commercialState.exClientsStatus === "ready", "queda ready");
  assertTrue(commercialExClientesAllRows().length === 1, "el dataset de Ex Clientes queda en memoria");

  // Volver a Clientes y volver a Ex Clientes: NINGUNA de las dos re-pide nada.
  commercialSetClientesSubTab("clientes");
  assertTrue(fetchCalls.length === 2, "volver al sub-tab Clientes no refetch");
  commercialSetClientesSubTab("ex-clientes");
  assertTrue(fetchCalls.length === 2, "volver a Ex Clientes (ya ready) tampoco refetch");

  // ═══ G) Clientes — expandir/ordenar/filtrar/buscar: CERO request ═══
  commercialSetClientesSubTab("clientes");
  hosts["[data-commercial-clientes-table]"] = { innerHTML: "" };
  root.innerHTML = ROOT_SENTINEL;
  fireClick("[data-commercial-clientes-toggle]", { commercialClientesToggle: "100" });
  assertTrue(commercialState.clientesExpanded.indexOf(100) !== -1,
    "toggle de cliente abre su id NUMÉRICO en el estado local (Number-coerced, no string)");
  assertTrue(hosts["[data-commercial-clientes-table]"].innerHTML.includes("Op Activa"),
    "y re-renderiza SÓLO la tabla, mostrando sus oportunidades activas");
  assertTrue(fetchCalls.length === 2, "expandir un cliente no dispara ninguna request");
  assertTrue(root.innerHTML === ROOT_SENTINEL, "sin render completo");

  fireClick("[data-commercial-clientes-sort]", { commercialClientesSort: "active" });
  assertTrue(commercialState.clientesSortKey === "active", "orden de columna (Clientes) actualiza el estado local");

  fireInput("[data-commercial-clientes-search]", "Uno");
  assertTrue(commercialState.clientesSearchText === "Uno", "Buscar (Clientes) actualiza el estado local");
  assertTrue(hosts["[data-commercial-clientes-table]"].innerHTML.includes("Cliente Uno")
    && !hosts["[data-commercial-clientes-table]"].innerHTML.includes("Cliente Sin Opps"),
    "y filtra client-side");

  fireChange("[data-commercial-clientes-seller-filter]", "Ana");
  assertTrue(commercialState.clientesSellerFilter === "Ana", "el <select> de Vendedor actualiza el estado local");
  fireChange("[data-commercial-clientes-status-filter]", "active");
  assertTrue(commercialState.clientesStatusFilter === "active", "el <select> de Estado actualiza el estado local");
  assertTrue(fetchCalls.length === 2, "ningún filtro/orden/búsqueda de Clientes dispara requests");

  // Ex Clientes — Buscar/Vendedor propios, CERO request.
  commercialSetClientesSubTab("ex-clientes");
  hosts["[data-commercial-ex-clientes-table]"] = { innerHTML: "" };
  fireInput("[data-commercial-ex-clientes-search]", "Uno");
  assertTrue(commercialState.exClientesSearchText === "Uno", "Buscar (Ex Clientes) actualiza SU PROPIO estado local");
  fireChange("[data-commercial-ex-clientes-seller-filter]", "Beto");
  assertTrue(commercialState.exClientesSellerFilter === "Beto", "el <select> de Vendedor (Ex Clientes) actualiza el estado local");
  assertTrue(fetchCalls.length === 2, "ningún filtro/búsqueda de Ex Clientes dispara requests");

  // ═══ H) Clientes/Ex Clientes — reintento escopeado, cada uno su endpoint ═
  resetState({ clientsStatus: "error", clientsErrorState: "source_error" });
  fetchCalls.length = 0;
  fetchImpl = () => new Promise(() => {});
  retry("clients"); retry("clients");
  assertTrue(fetchCalls.length === 1, "DOS clicks en Reintentar Clientes mientras carga -> una sola request");
  assertTrue(fetchCalls[0] === "/comercial/data/clients?tipo=todos", "al endpoint de Clientes, nunca otro");

  resetState({ exClientsStatus: "error", exClientsErrorState: "source_error" });
  fetchCalls.length = 0;
  retry("ex-clients"); retry("ex-clients");
  assertTrue(fetchCalls.length === 1, "DOS clicks en Reintentar Ex Clientes mientras carga -> una sola request");
  assertTrue(fetchCalls[0] === "/comercial/data/ex-clients?tipo=todos", "al endpoint de Ex Clientes, nunca Clientes");

  // ═══ I) Clientes — tipo-safety: respuesta obsoleta se descarta ═══
  resetState();
  fetchCalls.length = 0;
  fetchImpl = () => new Promise(resolve => { resolveFetch = resolve; });
  const clientsLoadPromise = commercialLoadClients();
  commercialState.tipo = "bi";
  resolveFetch({ json: () => Promise.resolve({ state: "ok", clients: CLIENTS_PAYLOAD }) });
  await clientsLoadPromise;
  assertTrue(commercialState.clientsStatus === "loading",
    "la respuesta de Clientes para el tipo VIEJO se descarta — el status sigue esperando el tipo nuevo");
  assertTrue(commercialState.clients === null, "y el dataset de Clientes NO se pisa con la respuesta obsoleta");

  finish();
})();

function finish() {
  if (failures) {
    console.log(`\n${failures} invariante(s) fallaron`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL BUCKET 2 TABS INVARIANTS PASSED");
}
