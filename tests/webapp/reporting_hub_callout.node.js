// reporting_hub_callout.node.js — invocado por test_reporting_hub_callout.py.
// HUB TRANSVERSAL PRIMITIVE TEST (owner directive): Horas Productivas debe
// consumir el MISMO primitivo de presentación que Copilot renderiza para
// `{{callout:...}}` (`content_service.py::callout()`), nunca una imitación
// local. Carga reporting.js en un sandbox mínimo y ejecuta el MOTOR REAL
// (`renderHubCallout`/`renderCapacidadProductivas`) contra fixtures
// sintéticas.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const sandbox = {
  document: { addEventListener() {}, getElementById() { return makeEl(); } },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  initDashboardSections() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { renderHubCallout, renderCapacidadProductivas } = sandbox;
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

// --- renderHubCallout arma EXACTAMENTE la forma HTML/clases que
// --- content_service.py::callout() genera para Copilot (mismo primitivo
// --- compartido, ver tools.css) ---------------------------------------
{
  const html = renderHubCallout({ tone: "note", title: "Nota metodológica", bodyHtml: "<p>Texto de prueba.</p>" });
  assertTrue(html.includes('class="callout callout--note"'), "renderHubCallout usa la MISMA clase base 'callout' + modificador de tono que Copilot (content_service.py::callout())");
  assertTrue(html.includes('class="callout__head"'), "incluye callout__head, MISMA estructura que Copilot");
  assertTrue(html.includes('class="callout__title"') && html.includes("Nota metodológica"), "título presente vía callout__title");
  assertTrue(html.includes('class="callout__body"') && html.includes("<p>Texto de prueba.</p>"), "bodyHtml pasado tal cual — Reporting NUNCA finge que su contenido vino de Markdown");
}

// --- Horas Productivas consume el primitivo compartido, callout FUERA de
// --- "Horas consumidas por Cliente", nunca dentro -----------------------
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2026": [10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] } },
  );
  Object.keys(CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR).forEach(k => delete CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR[k]);
  state.filters.capacidad = { cliente: "", anio_cliente: "2026", anio_clientePart: "2026" };
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
  state.capacidadProductivasSortDir = { cliente: "desc", clientePart: "desc" };
  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  state.perspective.capacidad = "Productivas";
  state.batchBDataStatus = "ready";

  const html = renderCapacidadProductivas();
  assertTrue(html.includes('class="callout callout--note"'), "renderCapacidadProductivas() incluye el callout compartido");
  assertTrue(html.includes("Las horas visualizadas corresponden"), "contiene el texto informativo pedido por el owner");
  const calloutIdx = html.indexOf('class="callout callout--note"');
  const detalleIdx = html.indexOf('Horas consumidas por Cliente');
  assertTrue(calloutIdx !== -1 && detalleIdx !== -1 && calloutIdx < detalleIdx, "el callout aparece ANTES/AFUERA del grupo 'Horas consumidas por Cliente', nunca dentro");
  // El callout NUNCA queda envuelto por dashboard-section__body de "detalle"
  // — se verifica que el bloque <aside class="callout ...</aside> completo
  // termina ANTES de que abra el dashboard-section de detalle.
  const asideCloseIdx = html.indexOf("</aside>");
  const detalleSectionOpenIdx = html.indexOf('data-section-id="detalle"');
  assertTrue(asideCloseIdx !== -1 && detalleSectionOpenIdx !== -1 && asideCloseIdx < detalleSectionOpenIdx, "el <aside> del callout cierra ANTES de que abra la dashboard-section 'Horas consumidas por Cliente' — visualmente independiente, no anidado");
}

// --- No Reporting-local duplicate callout renderer/CSS class -------------
{
  const calloutFnMatches = (src.match(/function render\w*[Cc]allout\w*\(/g) || []);
  assertTrue(calloutFnMatches.length === 1 && calloutFnMatches[0] === "function renderHubCallout(", "UN SOLO renderer de callout en reporting.js (renderHubCallout) — ninguna imitación local (p.ej. renderCapacidadCallout/renderReportingCallout)");
}

// --- Callout NUNCA aparece durante la primera hidratación (loader-only) —
// --- MISMO gate que el resto del contenido de Horas Productivas ----------
{
  state.batchBDataStatus = "loading";
  const html = renderCapacidadProductivas();
  assertTrue(!html.includes('class="callout callout--note"'), "batchBDataStatus='loading' (primera hidratación) — el callout NO aparece, MISMO gate que el resto del contenido (renderBatchBDataState)");
  state.batchBDataStatus = "ready";
}

if (failures > 0) {
  console.log(`${failures} hub callout invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB CALLOUT INVARIANTS PASSED");
