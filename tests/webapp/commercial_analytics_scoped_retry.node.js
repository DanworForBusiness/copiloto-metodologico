// commercial_analytics_scoped_retry.node.js — invocado por
// test_commercial_analytics_scoped_retry.py. Owner corrective "COMMERCIAL —
// SCOPED RETRY FOR PIPELINE ANALYTICS" (ver HANDOFF): cuando el bootstrap
// inicial deja Resumen/Pipeline en READY pero "Analíticas del Pipeline"
// degradó (`analyticsStatus === "error"`), un click en su propio
// "Reintentar" pide EXACTAMENTE `/comercial/data/analytics` — nunca
// summary/pipeline/opportunities/activities, nunca los dos detalles lazy
// (`pipeline-analytics-detail`/`lead-conversion-detail`) — y actualiza SÓLO
// el host de esa sección (`[data-commercial-analytics-body]`), nunca un
// `commercialRender()`/`commercialLoadData()` completo.
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures y un `document`
// mínimo — MISMA técnica que commercial_forecast.node.js (fetch swappable +
// `root` con centinela + hosts falsos + delegados reales).
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

const { commercialAnalyticsBodyHtml, commercialLoadAnalytics, commercialCreadasSummaryTileHtml } = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

// Los delegados de click/keydown de commercial.js se cablean UNA sola vez,
// en DOMContentLoaded — MISMO criterio que commercial_forecast.node.js. Esto
// también dispara el `commercialLoadData()` del bootstrap real (con el
// `fetchImpl` default de arriba); cada bloque de abajo llama `resetState()`
// antes de sus propias afirmaciones, así que esa carga inicial nunca
// contamina un resultado.
(listeners.DOMContentLoaded || []).forEach(cb => cb());

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
// `outerHTML` como alias de `innerHTML` (owner smoke — "Creadas esta
// semana" se achicó, ver HANDOFF): `commercialRenderCreadasSummaryTile`
// ahora reemplaza el host con `outerHTML` (el host ES el `.activity-kpi`
// mismo, no un wrapper — `innerHTML` metería un `.activity-kpi` nuevo
// ADENTRO del viejo). Un nodo real de verdad reemplaza el elemento en su
// padre; acá alcanza con que el string quede accesible para las
// aserciones, sin modelar el árbol completo.
function fakeNode() {
  const node = { innerHTML: "" };
  Object.defineProperty(node, "outerHTML", {
    get() { return node.innerHTML; },
    set(html) { node.innerHTML = html; },
  });
  return node;
}
const tick = () => new Promise(resolve => setImmediate(resolve));

const ANALYTICS = {
  creadas: { this_week: 4, last_week: 6 },
  pipeline_age: { avg_days: 219.8, count: 40 },
  stage_time: { avg_days: 22.8, stages_with_data: 8 },
  stage_conversion: { min_rate: 33.3, steps: 7 },
  close_time: { avg_days: 112.5, count: 30 },
  stagnant: { count: 19, threshold_days: 30 },
  lead_conversion: { companies: 100, any_conv: 5, rate_any_conv: 5.3, adv_opp: 4, rate_adv_opp: 3.9 },
};

// Snapshot de un Dashboard READY con Analíticas ya rota — el escenario
// exacto que dispara el corrective ("Owner observed... Summary = READY,
// Analytics = ERROR").
function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.status = "ready";
  commercialState.summary = { marker: "summary-intacto" };
  commercialState.pipeline = { marker: "pipeline-intacto" };
  commercialState.opportunities = { marker: "opportunities-intacto" };
  commercialState.analytics = null;
  commercialState.analyticsStatus = "error";
  commercialState.analyticsErrorState = "source_error";
  Object.assign(commercialState, patch || {});
}

// ═══ A) Summary READY + Analytics ERROR — los dos estados en el mismo body ═
{
  resetState();
  const body = commercialAnalyticsBodyHtml();
  assertTrue(body.includes("dashboard-empty"), "Analíticas muestra el estado No disponible");
  assertTrue(body.includes("No disponible"), "con el eyebrow correcto");
  assertTrue(body.includes('data-commercial-retry="analytics"'), "y su PROPIO botón Reintentar");
  assertTrue(!body.includes('data-commercial-retry="dashboard"'),
    "nunca la key del Dashboard — un click acá no debe recargar todo");
  // config_incomplete no ofrece reintentar — mismo criterio que el resto de Comercial.
  resetState({ analyticsErrorState: "config_incomplete" });
  assertTrue(!commercialAnalyticsBodyHtml().includes("data-commercial-retry"),
    "con el CRM sin configurar tampoco se ofrece reintentar");
  resetState();
}

// ═══ E) Loading escopeado, sólo dentro de Analíticas ═══════════════════════
{
  resetState();
  hosts["[data-commercial-analytics-body]"] = fakeNode();
  root.innerHTML = ROOT_SENTINEL;
  fetchImpl = () => new Promise(() => {}); // nunca resuelve — sólo interesa el estado intermedio
  commercialLoadAnalytics();
  assertTrue(commercialState.analyticsStatus === "loading", "entra en loading LOCAL de inmediato");
  assertTrue(hosts["[data-commercial-analytics-body]"].innerHTML.includes("section-analysis-loading"),
    "y lo dice DENTRO de su propio host");
  assertTrue(hosts["[data-commercial-analytics-body]"].innerHTML.includes("Cargando analíticas"),
    'con el copy "Cargando analíticas…" (o el equivalente ya establecido)');
  assertTrue(root.innerHTML === ROOT_SENTINEL, "sin un solo render completo mientras vuela");
  assertTrue(commercialState.status === "ready", "el status GENERAL del Dashboard no se toca");
}

// ═══ I) Single-flight: el delegado real, doble/triple click ════════════════
{
  resetState();
  hosts["[data-commercial-analytics-body]"] = fakeNode();
  fetchCalls.length = 0;
  fetchImpl = () => new Promise(() => {}); // in-flight indefinido, a propósito
  const retry = () => (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-retry]" ? { dataset: { commercialRetry: "analytics" } } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
  retry();
  retry();
  retry();
  assertTrue(fetchCalls.length === 1, `TRES clicks mientras carga → una sola request (got ${fetchCalls.length})`);
  assertTrue(commercialState.analyticsStatus === "loading", "sigue en loading tras los clicks extra");
}

// --- Ciclo COMPLETO (asíncrono, al final para no pisarse con los bloques
//     sincrónicos de arriba) — B/C/D, F, G, H, J -----------------------------
(async () => {
  await tick(); // drena los in-flight indefinidos de los bloques E/I de arriba

  // ═══ B/C/D) UNA sola request, al endpoint correcto, CERO a los demás ═════
  {
    resetState({ tipo: "nn" });
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    fetchCalls.length = 0;
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", analytics: ANALYTICS }) });
    await commercialLoadAnalytics();
    assertTrue(fetchCalls.length === 1, `EXACTAMENTE una request (got ${fetchCalls.length}: ${fetchCalls.join(",")})`);
    assertTrue(fetchCalls[0] === "/comercial/data/analytics?tipo=nn",
      `al endpoint correcto, con el Tipo vigente (got ${fetchCalls[0]})`);
    for (const banned of ["summary", "/comercial/data/pipeline?", "/comercial/data/opportunities",
                            "/comercial/data/activities", "pipeline-analytics-detail", "lead-conversion-detail"]) {
      assertTrue(!fetchCalls.some(u => u.includes(banned)), `cero requests a "${banned}"`);
    }
  }

  // ═══ F) Éxito: error → loading → ready, reemplaza SÓLO el body ══════════
  {
    resetState();
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    root.innerHTML = ROOT_SENTINEL;
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", analytics: ANALYTICS }) });
    await commercialLoadAnalytics();
    assertTrue(commercialState.analyticsStatus === "ready", "al resolver, error → loading → ready");
    assertTrue(commercialState.analytics === ANALYTICS, "con el payload real");
    const html = hosts["[data-commercial-analytics-body]"].innerHTML;
    assertTrue(html.includes("Edad del pipeline") && html.includes("Conversión de leads"),
      "y el body pasa a mostrar los seis tiles reales");
    assertTrue(!html.includes("dashboard-empty") && !html.includes("Cargando"),
      "sin rastro del estado de error ni del de carga anterior");
    assertTrue(root.innerHTML === ROOT_SENTINEL, "un render completo hubiera cerrado Activas/Forecast si estaban abiertos");
  }

  // ═══ G) Resumen queda igual salvo el ÚNICO tile que SÍ depende de
  //        Analíticas ("Creadas esta semana") — owner follow-up "FINAL
  //        DEPENDENCY CHECK" (ver HANDOFF): auditado contra el backend
  //        (`CommercialService.get_analytics` → `_view()`), `creadas` sale
  //        del MISMO payload que los seis tiles de Analíticas — así que
  //        preservar el placeholder degradado ("—") DESPUÉS de que su
  //        fuente se recuperó sería el error opuesto al que este corrective
  //        previene. El resto de Resumen (summary/pipeline/opportunities,
  //        ocho tiles que NO dependen de `analytics`) sí debe quedar
  //        byte/referencia-idéntico. ══════════════════════════════════════
  {
    resetState();
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    hosts["[data-commercial-creadas-summary-tile]"] = fakeNode();
    hosts["[data-commercial-creadas-summary-tile]"].innerHTML = commercialCreadasSummaryTileHtml(); // estado inicial: "—"
    assertTrue(hosts["[data-commercial-creadas-summary-tile]"].innerHTML.includes(">—<"),
      'inicial: Summary sano salvo Creadas="—" (Analytics=error)');
    const before = { summary: commercialState.summary, pipeline: commercialState.pipeline,
                      opportunities: commercialState.opportunities, status: commercialState.status,
                      tipo: commercialState.tipo };
    fetchCalls.length = 0;
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", analytics: ANALYTICS }) });
    await commercialLoadAnalytics();
    assertTrue(fetchCalls.length === 1 && fetchCalls[0] === "/comercial/data/analytics?tipo=todos",
      "el reintento sigue siendo EXACTAMENTE una request, al endpoint de siempre");
    assertTrue(commercialState.analyticsStatus === "ready", "éxito: Analytics=ready");
    const creadasHtml = hosts["[data-commercial-creadas-summary-tile]"].innerHTML;
    assertTrue(!creadasHtml.includes(">—<") && creadasHtml.includes(`>${ANALYTICS.creadas.this_week}<`),
      'Creadas pasa de "—" al valor recuperado — MISMA respuesta ya en memoria, sin fetch propio');
    assertTrue(creadasHtml.includes("Semana Anterior") && creadasHtml.includes(String(ANALYTICS.creadas.last_week)),
      "con su línea secundaria (Semana Anterior) también recuperada");
    assertTrue(commercialState.summary === before.summary, "summary: MISMA referencia, cero mutación");
    assertTrue(commercialState.pipeline === before.pipeline, "pipeline: ídem — ningún tile no-relacionado cambia");
    assertTrue(commercialState.opportunities === before.opportunities, "opportunities: ídem");
    assertTrue(commercialState.status === before.status, "status general: ídem");
    assertTrue(commercialState.tipo === before.tipo, "Tipo vigente: ídem");
  }

  // ═══ H) Reintento que vuelve a fallar: error → loading → error, con salida ═
  {
    resetState();
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    hosts["[data-commercial-creadas-summary-tile]"] = fakeNode();
    hosts["[data-commercial-creadas-summary-tile]"].innerHTML = commercialCreadasSummaryTileHtml();
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "source_error" }) });
    await commercialLoadAnalytics();
    assertTrue(commercialState.analyticsStatus === "error" && commercialState.analyticsErrorState === "source_error",
      "vuelve a No disponible, nunca se queda pegado en loading");
    assertTrue(hosts["[data-commercial-analytics-body]"].innerHTML.includes('data-commercial-retry="analytics"'),
      "y el botón Reintentar sigue ahí — nunca un estado sin salida");
    assertTrue(hosts["[data-commercial-creadas-summary-tile]"].innerHTML.includes(">—<"),
      "y Creadas se queda en \"—\": no había nada que recuperar, no hay nada que perder");
    // Una excepción de red (fetch rechaza) cae en el MISMO camino fail-soft.
    resetState();
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    fetchImpl = () => Promise.reject(new Error("network down"));
    await commercialLoadAnalytics();
    assertTrue(commercialState.analyticsStatus === "error" && commercialState.analyticsErrorState === "source_error",
      "una excepción de transporte también degrada, sin propagar");
  }

  // ═══ J) Tipo-safety: no adopta una respuesta obsoleta ════════════════════
  {
    resetState({ tipo: "nn" });
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    hosts["[data-commercial-creadas-summary-tile]"] = fakeNode();
    hosts["[data-commercial-creadas-summary-tile]"].innerHTML = "<!--bi-nuevo-creadas-->";
    let releaseFetch;
    fetchImpl = () => new Promise(resolve => { releaseFetch = resolve; });
    const inFlight = commercialLoadAnalytics(); // arranca bajo tipo "nn"
    assertTrue(commercialState.analyticsStatus === "loading", "arranca en loading bajo el tipo vigente");

    // El usuario cambia de Tipo ANTES de que la respuesta (todavía de "nn") llegue.
    commercialState.tipo = "bi";
    commercialState.analyticsStatus = "idle"; // MISMO reset que hace `commercialSetTipo` real
    commercialState.analytics = null;
    hosts["[data-commercial-analytics-body]"].innerHTML = "<!--bi-nuevo-->";

    // Ahora resuelve la request VIEJA (todavía con datos de "nn").
    releaseFetch({ json: () => Promise.resolve({ state: "ok", analytics: ANALYTICS }) });
    await inFlight;

    assertTrue(commercialState.tipo === "bi", "el Tipo vigente sigue siendo el que el usuario eligió");
    assertTrue(commercialState.analyticsStatus === "idle",
      "la respuesta obsoleta NO pisa el status — sigue como lo dejó el cambio de Tipo");
    assertTrue(commercialState.analytics === null,
      "ni el dato: la respuesta de \"nn\" nunca se adopta bajo \"bi\"");
    assertTrue(hosts["[data-commercial-analytics-body]"].innerHTML === "<!--bi-nuevo-->",
      "y el host NO se re-renderiza con la respuesta obsoleta");
    assertTrue(hosts["[data-commercial-creadas-summary-tile]"].innerHTML === "<!--bi-nuevo-creadas-->",
      "y Creadas TAMPOCO — la misma respuesta obsoleta que no toca Analíticas no toca su tile derivado");

    // Camino de excepción: MISMO guard.
    resetState({ tipo: "nn" });
    hosts["[data-commercial-analytics-body]"] = fakeNode();
    let rejectFetch;
    fetchImpl = () => new Promise((_resolve, reject) => { rejectFetch = reject; });
    const inFlight2 = commercialLoadAnalytics();
    commercialState.tipo = "bi";
    commercialState.analyticsStatus = "idle";
    commercialState.analytics = null;
    hosts["[data-commercial-analytics-body]"].innerHTML = "<!--bi-nuevo-2-->";
    rejectFetch(new Error("network down, tarde"));
    await inFlight2;
    assertTrue(commercialState.analyticsStatus === "idle",
      "una excepción tardía de la request vieja tampoco pisa el estado del Tipo nuevo");
    assertTrue(hosts["[data-commercial-analytics-body]"].innerHTML === "<!--bi-nuevo-2-->",
      "ni re-renderiza sobre el host del Tipo nuevo");
  }

  finish();
})();

function finish() {
  if (failures) {
    console.log(`\n${failures} invariante(s) fallaron`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL ANALYTICS SCOPED RETRY INVARIANTS PASSED");
}
