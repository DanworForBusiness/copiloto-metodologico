// commercial_licensing_table_ergonomics.node.js — invocado por
// test_commercial_licensing_table_ergonomics.py.
//
// Director §18-27 (ver HANDOFF commercial_licensing_20260925): reorder +
// resize de columnas para Oportunidades de licenciamiento Y Perfil de
// licenciamiento por cliente, sobre el primitivo COMPARTIDO
// (initDraggableColumns/initResizableColumns, app.js — su contrato
// table-agnostic ya se prueba en app_table_ergonomics.node.js). ACÁ se
// prueba el WIRING específico de Comercial: catálogo de columnas, orden por
// key (nunca posición), fila de Total order-aware, y que las DOS acciones
// (fila → Detalle Oportunidades / botón → detalle de cuenta) sobreviven un
// reorder — sin simular pointer events reales (eso ya está cubierto), sólo
// inspeccionando el HTML real que producen las funciones de render.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

// `storedOrder`/`storedWidths` son mutables por test — simulan lo que
// localStorage tendría guardado, sin depender de un localStorage real (MISMA
// técnica que el resto de harnesses de Comercial, que stubean
// readStoredColumnOrder por completo).
let storedOrder = null;
let storedWidths = null;

const sandbox = {
  document: {
    addEventListener() {},
    getElementById() { return null; },
    querySelector() { throw new Error("commercial.js nunca debe usar document.querySelector"); },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  initDraggableColumns() {},
  initResizableColumns() {},
  readStoredColumnOrder(key, validKeys) { return storedOrder && storedOrder[key] || null; },
  readStoredColumnWidths(key) { return storedWidths && storedWidths[key] || null; },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialLicensingProfileTableHtml, commercialLicenciasTableHtml,
} = sandbox;
// `const` de nivel de módulo (a diferencia de las `function`, que SÍ quedan
// como propiedades del objeto sandbox) no se reflejan en `sandbox` — hay que
// pedirlas explícitamente al contexto, MISMO criterio que `commercialState`.
const commercialState = vm.runInContext("commercialState", sandbox);
const COMMERCIAL_LICENSING_PROFILE_COLUMN_KEYS = vm.runInContext("COMMERCIAL_LICENSING_PROFILE_COLUMN_KEYS", sandbox);
const COMMERCIAL_LICENCIAS_COMPACT_COLUMN_KEYS = vm.runInContext("COMMERCIAL_LICENCIAS_COMPACT_COLUMN_KEYS", sandbox);

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, e, label) {
  if (JSON.stringify(a) !== JSON.stringify(e)) {
    failures++; console.log(`FAIL: ${label} — esperado ${JSON.stringify(e)}, obtenido ${JSON.stringify(a)}`);
  }
}

const PROFILE_ACCOUNT = (over) => ({
  id: 900, name: "Cuenta Uno", partner_de_licencia: "05 - SOFTTEK", ae_sap: "AE Uno",
  arquitectura_actual: ["SAC"], monto_contrato: 1000, currency: "USD",
  vencimiento_contrato: "2027-01-01", tipo_contrato_sap: "Contrato Individual",
  segmento_cuenta: "Corporate", seller: "Vendedor Uno",
  ...over,
});
const OPP_ROW = (over) => ({
  id: 42, opportunity: "Opp Uno", client: "Cuenta Uno", account_id: 900, seller: "Vendedor Uno",
  stage: "1 Contacto", tags: [], revenue: 5000,
  ...over,
});

function resetPrefs() { storedOrder = null; storedWidths = null; }

// ── A: sin preferencia guardada, el orden DEFAULT es el catálogo declarado -
{
  resetPrefs();
  const html = commercialLicensingProfileTableHtml([PROFILE_ACCOUNT()]);
  const idx = (key) => html.indexOf(`data-column-key="${key}"`);
  ok(idx("name") < idx("partner_de_licencia") && idx("partner_de_licencia") < idx("tipo_contrato_sap"),
    "A (Perfil): sin preferencia, respeta el orden default del catálogo");
  const htmlOpp = commercialLicenciasTableHtml([OPP_ROW()], { sortKey: "", sortDir: 1, emptyLabel: "" });
  const idxO = (key) => htmlOpp.indexOf(`data-column-key="${key}"`);
  ok(idxO("opportunity") < idxO("client") && idxO("client") < idxO("revenue"),
    "A (Oportunidades): sin preferencia, respeta el orden default del catálogo");
}

// ── B: preferencia de orden guardada — Perfil y Oportunidades cada uno con
//     su PROPIA key, independientes ──────────────────────────────────────
{
  resetPrefs();
  storedOrder = {
    "commercial-licprofile": [...COMMERCIAL_LICENSING_PROFILE_COLUMN_KEYS].reverse(),
  };
  const html = commercialLicensingProfileTableHtml([PROFILE_ACCOUNT()]);
  const idx = (key) => html.indexOf(`data-column-key="${key}"`);
  ok(idx("tipo_contrato_sap") < idx("name"),
    "B: el Perfil respeta SU preferencia guardada (orden invertido)");
  // Oportunidades NO tiene preferencia -> sigue en su default, nunca hereda
  // la del Perfil (namespaces independientes por key de tabla).
  const htmlOpp = commercialLicenciasTableHtml([OPP_ROW()], { sortKey: "", sortDir: 1, emptyLabel: "" });
  const idxO = (key) => htmlOpp.indexOf(`data-column-key="${key}"`);
  ok(idxO("opportunity") < idxO("client"),
    "B: Oportunidades no se contamina con la preferencia del Perfil (key distinta)");
}

// ── C: colgroup <col> lleva data-column-key — identidad, no posición ──────
// (el resize del <col> correcto depende de esto — probado a nivel primitivo
// en app_table_ergonomics.node.js; acá se confirma que EL MARKUP REAL de
// Comercial efectivamente lo emite).
{
  resetPrefs();
  const html = commercialLicensingProfileTableHtml([PROFILE_ACCOUNT()]);
  const colgroup = html.match(/<colgroup>(.*?)<\/colgroup>/s)[1];
  COMMERCIAL_LICENSING_PROFILE_COLUMN_KEYS.forEach(key => {
    ok(colgroup.includes(`<col data-column-key="${key}"`), `C (Perfil): <col data-column-key="${key}"> presente`);
  });
  const htmlOpp = commercialLicenciasTableHtml([OPP_ROW()], { sortKey: "", sortDir: 1, emptyLabel: "" });
  const colgroupOpp = htmlOpp.match(/<colgroup>(.*?)<\/colgroup>/s)[1];
  COMMERCIAL_LICENCIAS_COMPACT_COLUMN_KEYS.forEach(key => {
    ok(colgroupOpp.includes(`<col data-column-key="${key}"`), `C (Oportunidades): <col data-column-key="${key}"> presente`);
  });
}

// ── D: anchos guardados se aplican; una key sin override usa el default ───
{
  resetPrefs();
  storedWidths = { "commercial-licprofile": { name: "260px" } };
  const html = commercialLicensingProfileTableHtml([PROFILE_ACCOUNT()]);
  ok(html.includes('<col data-column-key="name" style="width:260px">'),
    "D: una columna con override guardado usa ese ancho");
  ok(html.includes('<col data-column-key="ae_sap" style="width:12%">'),
    "D: una columna SIN override sigue con el default declarado del catálogo");
}

// ── E: acción fija — NUNCA entra al `order` reordenable ────────────────────
{
  resetPrefs();
  storedOrder = { "commercial-oportunidades-licencias": [...COMMERCIAL_LICENCIAS_COMPACT_COLUMN_KEYS].reverse() };
  const html = commercialLicenciasTableHtml([OPP_ROW()], { sortKey: "", sortDir: 1, emptyLabel: "" });
  ok(html.includes("Ver detalle de licencias"), "E: la acción sigue presente");
  // Ningún key de negocio se llama "_action"/"action" — confirmamos que la
  // celda de acción es la ÚLTIMA <td> de la fila pase lo que pase con el
  // orden de las demás.
  const row = html.match(/<tr class="dashboard-table-row--clickable"[^>]*>(.*?)<\/tr>/s)[1];
  const tds = [...row.matchAll(/<td[^>]*>/g)].map(m => m[0]);
  ok(tds[tds.length - 1].includes('data-commercial-licencias-col="_action"'),
    "E: la celda de acción sigue siendo SIEMPRE la última, sin importar el orden de las demás");
}

// ── F: fila de Total — order-aware, nunca colspan fijo ─────────────────────
// (el defecto que este ticket evita: reordenar "Ingreso esperado" a otra
// posición no puede dejar el total bajo la columna equivocada, ni el label
// "Total (N)" en una celda que ya no es la primera visible).
{
  resetPrefs();
  storedOrder = { "commercial-oportunidades-licencias": ["revenue", "client", "opportunity", "seller", "tags", "stage"] };
  const html = commercialLicenciasTableHtml([OPP_ROW({ revenue: 1234 })], { sortKey: "", sortDir: 1, emptyLabel: "" });
  const totalRow = html.match(/<tr class="is-total">(.*?)<\/tr>/s)[1];
  const cells = [...totalRow.matchAll(/<td[^>]*>(.*?)<\/td>/gs)].map(m => m[1]);
  ok(cells[0].includes("1,234") || cells[0].includes("1.234") || cells[0].includes("1234"),
    `F: revenue quedó PRIMERO tras el reorder -> el total va en la celda 0 (obtenido: ${JSON.stringify(cells)})`);
  ok(cells[1].includes("Total (1)"),
    `F: como revenue ocupó la celda 0, "Total (N)" cae en la siguiente celda visible, nunca perdido (obtenido: ${JSON.stringify(cells)})`);
}

// ── G: reorder no rompe NINGUNA de las dos acciones (fila / botón) ─────────
{
  resetPrefs();
  storedOrder = { "commercial-oportunidades-licencias": [...COMMERCIAL_LICENCIAS_COMPACT_COLUMN_KEYS].reverse() };
  const html = commercialLicenciasTableHtml([OPP_ROW()], { sortKey: "", sortDir: 1, emptyLabel: "" });
  ok(html.includes('data-commercial-licencias-opportunity-row="42"'), "G: la fila sigue siendo clickeable hacia Detalle Oportunidades");
  ok(/data-commercial-licensing-account-row="900"/.test(html), "G: el botón sigue apuntando a la cuenta correcta");
}

// ── H: no nth-child — mover "Etiqueta" (no sortable) también reordena ──────
// una columna SIN sort key demuestra que reorder no depende de "sortable".
{
  resetPrefs();
  storedOrder = { "commercial-oportunidades-licencias": ["tags", "opportunity", "client", "seller", "stage", "revenue"] };
  const html = commercialLicenciasTableHtml([OPP_ROW()], { sortKey: "", sortDir: 1, emptyLabel: "" });
  const idxO = (key) => html.indexOf(`data-column-key="${key}"`);
  ok(idxO("tags") < idxO("opportunity"), "H: 'Etiqueta' (no sortable) también es reordenable, primera en este caso");
  ok(html.includes('data-column-key="tags" draggable="true"'), "H: 'Etiqueta' es draggable pese a no ser sortable");
  ok(!html.includes('data-commercial-licencias-sort="tags"'), "H: 'Etiqueta' sigue sin atributo de sort (reorder != sortable)");
}

if (failures > 0) {
  console.log(`${failures} Commercial licensing table ergonomics invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL COMMERCIAL LICENSING TABLE ERGONOMICS INVARIANTS PASSED");
process.exit(0);
