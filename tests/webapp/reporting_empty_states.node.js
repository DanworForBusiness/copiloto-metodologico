// reporting_empty_states.node.js — invocado por
// test_reporting_empty_states.py::test_reporting_empty_states_invariants_hold.
// Ejecuta el render REAL de Rentabilidad (reporting.js) en un sandbox minimo y
// verifica el CONTRATO de estado vacio contra el DOM producido: cada lado del
// ranking cae en el primitivo compartido del Hub cuando no tiene filas, y sigue
// mostrando su lista cuando si las tiene — incluido el caso mixto (un lado con
// datos, el otro vacio), que es el que un test de archivo no puede probar.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null } },
  window: {},
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  sessionStorage: { getItem() { return "" } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }) },
  console,
  //  vive en app.js (chrome del Hub), fuera de este
  // sandbox: Cartera lo invoca si dispara un lazy-load de anio frio.
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const reportingEmptyStateHtml = vm.runInContext("reportingEmptyStateHtml", sandbox);
const renderRentabilidadResumen = vm.runInContext("renderRentabilidadResumen", sandbox);
const state = vm.runInContext("state", sandbox);
const RENTABILIDAD_PROYECTOS = vm.runInContext("RENTABILIDAD_PROYECTOS", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(a, e, label) {
  if (a !== e) { failures++; console.log(`FAIL: ${label}\n  esperado: ${e}\n  obtenido: ${a}`); }
}
const count = (s, sub) => s.split(sub).length - 1;

// --- A: el helper emite EXACTAMENTE el primitivo compartido --------------
const solo = reportingEmptyStateHtml("Sin proyectos con rentabilidad ≥ 0.");
assertEqual(solo,
  '<div class="dashboard-empty"><span class="dashboard-empty__label">Sin resultados</span>'
  + "<p>Sin proyectos con rentabilidad ≥ 0.</p></div>",
  "A: markup identico al primitivo del Hub");
assertTrue(!solo.includes("empty-note"), "A: no queda rastro de la clase local");
// Escapa las dos partes (nunca inyecta markup del mensaje).
const malicioso = reportingEmptyStateHtml("<b>x</b>", "<i>y</i>");
assertTrue(!malicioso.includes("<b>") && !malicioso.includes("<i>"),
  "A: escapa mensaje y eyebrow");

// --- helpers de escenario ------------------------------------------------
// `state`/`RENTABILIDAD_PROYECTOS` se mutan a proposito (sandbox descartable),
// nunca se toca el archivo bajo prueba.
function conProyectos(filas) {
  RENTABILIDAD_PROYECTOS.length = 0;
  filas.forEach(f => RENTABILIDAD_PROYECTOS.push(f));
  state.filters.rentabilidad = {};
  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
  state.rentabilidadAnios = new Set(["2026"]);
  state.rentabilidadMeses = new Set();
  state.perspective.rentabilidad = "Resumen";
  // Gate del ciclo de vida real-data: sin esto `renderRentabilidadResumen`
  // devuelve su loader y nunca se llega al bloque del ranking.
  state.rentabilidadDataStatus = "ready";
  return renderRentabilidadResumen();
}
function proy(nombre, cert, trab) {
  const m = n => [0, 0, 0, 0, 0, 0, 0, n, 0, 0, 0, 0];
  return {
    tipo: "Proyecto", cliente: "ACME", cp: "CP", proyecto: nombre, estado: "En curso",
    certByYear: { "2026": m(cert) }, trabByYear: { "2026": m(trab) }, vendByYear: { "2026": m(0) },
    certMonthly: m(cert), trabMonthly: m(trab), vendMonthly: m(0),
    certificadas: cert, trabajadas: trab, vendidas: 0, fecha: "2026-08-01",
  };
}
// El bloque del ranking, aislado de los demas del tab.
function ranking(html) {
  const i = html.indexOf("Mejor performance");
  const j = html.indexOf("Certificadas vs Ejecutadas");
  return html.slice(i, j > i ? j : undefined);
}

// --- B: los DOS lados vacios --------------------------------------------
// Sin filas en absoluto: el bloque entero cae en el primitivo.
const vacioTotal = conProyectos([]);
assertTrue(vacioTotal.includes('<div class="dashboard-empty">'),
  "B: sin filas usa el primitivo compartido");
assertTrue(!vacioTotal.includes("empty-note"), "B: sin filas no usa texto plano");
assertTrue(vacioTotal.includes("Sin proyectos para los filtros actuales."),
  "B: conserva el mensaje de negocio");

// --- C: UN lado con datos, el otro vacio (el caso reportado) -------------
// Solo rentabilidad POSITIVA (cert > trab) => "Peor performance" queda vacio.
const soloPositivos = ranking(conProyectos([proy("Alfa", 120, 100)]));
assertEqual(count(soloPositivos, '<div class="dashboard-empty">'), 1,
  "C: exactamente UN lado renderiza el estado vacio");
assertTrue(soloPositivos.includes("Sin proyectos con rentabilidad negativa."),
  "C: el lado vacio es el de negativos");
assertTrue(!soloPositivos.includes("Sin proyectos con rentabilidad ≥ 0."),
  "C: el lado con datos NO renderiza estado vacio");
assertTrue(soloPositivos.includes("ranking-row") || soloPositivos.includes("bar-track"),
  "C: el lado con datos sigue mostrando su lista");
assertTrue(!soloPositivos.includes("empty-note"), "C: cero texto plano");

// Y el espejo: solo rentabilidad NEGATIVA => "Mejor performance" vacio, que es
// literalmente el caso que reporto el owner.
const soloNegativos = ranking(conProyectos([proy("Beta", 80, 120)]));
assertEqual(count(soloNegativos, '<div class="dashboard-empty">'), 1,
  "C: espejo — exactamente un lado vacio");
assertTrue(soloNegativos.includes("Sin proyectos con rentabilidad ≥ 0."),
  "C: espejo — 'Mejor performance' vacio usa el primitivo (caso reportado)");
assertTrue(!soloNegativos.includes("Sin proyectos con rentabilidad negativa."),
  "C: espejo — el lado con datos no renderiza estado vacio");

// --- D: los DOS lados con datos: cero estado vacio ----------------------
const ambos = ranking(conProyectos([proy("Alfa", 120, 100), proy("Beta", 80, 120)]));
assertEqual(count(ambos, '<div class="dashboard-empty">'), 0,
  "D: con datos en los dos lados no hay ningun estado vacio");
assertTrue(!ambos.includes("empty-note"), "D: cero texto plano");
// Los dos rotulos y la composicion de 2 columnas siguen ahi.
assertTrue(ambos.includes("Mejor performance") && ambos.includes("Peor performance"),
  "D: los dos rotulos siguen presentes");
assertTrue(conProyectos([proy("Alfa", 120, 100)]).includes('class="activity-grid-2"'),
  "D: la composicion de 2 columnas no cambia");

// --- E: un estado vacio por CADA tab normalizado ------------------------
// Los 7 sitios restantes vivian en otros tabs. Se vacia el dataset de cada uno
// y se verifica el DOM producido por su render REAL: primitivo compartido, cero
// texto plano, y el mensaje de negocio conservado. Con `DataStatus = "ready"`
// a proposito — es justamente la combinacion "cargo bien y no hay filas".
const TABS = [
  ["renderCartera", "CARTERA_MANTENIMIENTO_ROWS", null],
  ["renderHorasVendidas", "HORASVENDIDAS_ROWS", "horasVendidasDataStatus"],
  ["renderComercialParticipacion", "COMERCIAL_PARTICIPACION_ROWS", "comercialParticipacionDataStatus"],
  ["renderComercialSalud", "COMERCIAL_SALUD_ROWS", "batchADataStatus"],
  ["renderCalendario", "CALENDARIO_ROWS", "batchADataStatus"],
  ["renderCapacidadProductivas", "CAPACIDAD_PRODUCTIVAS_ROWS", "batchBDataStatus"],
  ["renderCapacidadNoAsignadas", "CAPACIDAD_NOASIGNADAS_ROWS", "batchBDataStatus"],
];

TABS.forEach(([fnName, arrName, statusKey]) => {
  const fn = vm.runInContext(fnName, sandbox);
  const arr = vm.runInContext(arrName, sandbox);
  if (typeof fn !== "function") { failures++; console.log(`FAIL: E: ${fnName} no es funcion`); return; }
  if (!Array.isArray(arr)) { failures++; console.log(`FAIL: E: ${arrName} no es array`); return; }
  arr.length = 0;                                  // dataset vacio: cero filas
  if (statusKey) state[statusKey] = "ready";        // cargo bien, no hay datos
  // Cartera tiene su propio ciclo de vida POR PERSPECTIVA (no un solo
  // DataStatus): hay que declarar la perspectiva hidratada y su status.
  state.carteraMantenimientoStatus = "ready";
  state.carteraProyectoStatus = "ready";
  state.reportingTabHydrated.cartera = true;
  // Set vacio == "Todos": sin un anio puntual tildado, Cartera no dispara
  // el lazy-load de anio frio (que ademas no es lo que este test mide).
  state.carteraAnios = new Set();
  state.carteraMeses = new Set();
  let html = "";
  try { html = fn() || ""; } catch (e) {
    failures++; console.log(`FAIL: E: ${fnName} lanzo ${String(e).slice(0, 90)}`); return;
  }
  assertTrue(!html.includes("empty-note"), `E: ${fnName} sin texto plano`);
  assertTrue(html.includes('<div class="dashboard-empty">'),
    `E: ${fnName} usa el primitivo compartido`);
  assertTrue(html.includes('class="dashboard-empty__label">Sin resultados<'),
    `E: ${fnName} lleva el eyebrow compartido`);
  assertTrue(html.includes("Sin filas para los filtros actuales."),
    `E: ${fnName} conserva su mensaje de negocio`);
});

// --- F: el error NUNCA se disfraza de "Sin resultados" -------------------
const errHtml = vm.runInContext("reportingSourceErrorHtml", sandbox)("los datos de Cartera");
assertTrue(!errHtml.includes("dashboard-empty"),
  "F: el estado de error no usa el primitivo de vacio");
assertTrue(!errHtml.includes("Sin resultados"),
  "F: el estado de error no dice 'Sin resultados'");
assertTrue(errHtml.includes("No pudimos cargar"),
  "F: el estado de error conserva su propia copy");

if (failures > 0) {
  console.log(`${failures} Reporting empty state invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL REPORTING EMPTY STATE INVARIANTS PASSED");
process.exit(0);
