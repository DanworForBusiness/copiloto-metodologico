// reporting_hub_callout.node.js — invocado por test_reporting_hub_callout.py.
// HUB TRANSVERSAL PRIMITIVE TEST (owner corrective — live smoke found 2
// defects in the first pass): Horas Productivas debe consumir el MISMO
// primitivo de presentación que Copilot renderiza para `{{callout:...
// punch}}` (`content_service.py::callout()`) — específicamente la variante
// `punch` (NO el tono neutro "note" de la primera pasada), y debe aparecer
// AL FINAL de la superficie analítica (footnote de cierre), nunca antes.
// Carga reporting.js en un sandbox mínimo y ejecuta el MOTOR REAL
// (`renderHubCallout`/`renderCapacidadProductivas`) contra fixtures
// sintéticas.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");
const TOOLS_CSS = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "webapp", "static", "tools.css"), "utf-8",
);

function makeEl() { return { innerHTML: "" }; }
const sandbox = {
  document: { addEventListener() {}, getElementById() { return makeEl(); }, querySelector() { return null; } },
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
  const html = renderHubCallout({ tone: "punch", title: "Nota metodológica", bodyHtml: "<p>Texto de prueba.</p>" });
  assertTrue(html.includes('class="callout callout--punch"'), "renderHubCallout resuelve tone:'punch' a la MISMA clase que Copilot (content_service.py::callout(), tone='punch') — nunca una paleta local nueva");
  assertTrue(html.includes('class="callout__head"'), "incluye callout__head, MISMA estructura que Copilot");
  assertTrue(html.includes('class="callout__title"') && html.includes("Nota metodológica"), "título presente vía callout__title");
  assertTrue(html.includes('class="callout__body"') && html.includes("<p>Texto de prueba.</p>"), "bodyHtml pasado tal cual — Reporting NUNCA finge que su contenido vino de Markdown");
}

// --- El selector .callout--punch que consume Reporting es EL MISMO que
// --- Copilot consume — una única regla compartida en tools.css, nunca una
// --- variante duplicada/local de Reporting -------------------------------
{
  assertTrue(TOOLS_CSS.includes(".callout--punch {"), "tools.css define .callout--punch (movida desde content.css en el corrective anterior, nunca reimplementada acá)");
  const punchOccurrences = (TOOLS_CSS.match(/\.callout--punch\s*\{/g) || []).length;
  assertTrue(punchOccurrences === 1, "UNA sola definición de .callout--punch en todo tools.css — ninguna regla duplicada/override local de Reporting");
}

// --- Horas Productivas consume el primitivo compartido CON la variante
// --- punch, callout FUERA de "Horas consumidas por Cliente" Y AL FINAL
// --- (footnote de cierre, nunca antes del contenido) ----------------------
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
  assertTrue(html.includes('class="callout callout--punch"'), "renderCapacidadProductivas() incluye el callout compartido CON la variante punch (nunca 'note')");
  assertTrue(!html.includes('callout--note'), "NUNCA la variante neutra 'note' — el owner pidió explícitamente punch");
  assertTrue(html.includes("Las horas visualizadas corresponden"), "contiene el texto informativo pedido por el owner, sin reescribirlo");

  // Posición: el callout debe aparecer DESPUÉS de que la dashboard-section
  // "Horas consumidas por Cliente" (<details>...</details>) CIERRA — nunca
  // antes, y estructuralmente imposible que quede anidado adentro.
  const detalleSectionOpenIdx = html.indexOf('data-section-id="detalle"');
  const detalleSectionCloseIdx = html.indexOf("</details>", detalleSectionOpenIdx);
  const asideOpenIdx = html.indexOf('<aside class="callout callout--punch"');
  assertTrue(detalleSectionOpenIdx !== -1 && detalleSectionCloseIdx !== -1 && asideOpenIdx !== -1, "todas las piezas (sección + callout) están presentes");
  assertTrue(detalleSectionCloseIdx < asideOpenIdx, "el callout aparece DESPUÉS de que cierra '</details>' de la dashboard-section 'Horas consumidas por Cliente' — footnote de cierre, NUNCA antes del contenido");
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
  assertTrue(!html.includes('class="callout callout--punch"'), "batchBDataStatus='loading' (primera hidratación) — el callout NO aparece, MISMO gate que el resto del contenido (renderBatchBDataState)");
  state.batchBDataStatus = "ready";
}

if (failures > 0) {
  console.log(`${failures} hub callout invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB CALLOUT INVARIANTS PASSED");
