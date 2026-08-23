// reporting_participacion_sort_frecuencia.node.js — invocado por
// test_reporting_participacion_sort_frecuencia.py. Owner request: "falta
// que las columnas de la tabla (% Participación Hs Vendidas, %
// Participación Hs Certificadas) se puedan ordenar ascendente/descendente
// como hicimos en horas productivas... Agreguemos frecuencia como filtro
// también al lado de cliente y antes de año". Carga reporting.js en un
// sandbox mínimo y ejecuta el MOTOR REAL (comercialParticipacionSorted/
// toggleComercialParticipacionSort/facetSelectField/renderComercialFilterBar)
// contra fixtures sintéticas.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

function makeEl() { return { innerHTML: "" }; }
const sandbox = {
  document: { addEventListener() {}, getElementById() { return makeEl(); }, querySelector() { return null; } },
  window: {},
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  // `HubForegroundProgress` tambien vive en app.js. Antes de la Fase 1A este
  // harness no lo alcanzaba: el token se leia sincronicamente, asi que para
  // cuando corria `render()` el fetch stub ya habia resuelto y el status era
  // "ready". El `await` de la autoridad agrega un tick, asi que ahora se
  // ejercita la rama CORRECTA — la barra de progreso mientras carga.
  HubForegroundProgress: { begin() { }, complete() { }, fail() { }, settle() { } },
  // Los timers del browser: mismo motivo que el stub de arriba — la barra
  // compartida arranca su sweep con `setInterval`, y esta rama recien se
  // ejercita desde la Fase 1A. No-op: el sweep no es lo que se prueba aca.
  setInterval() { return 0; },
  clearInterval() { },
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  initDashboardSections() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  comercialParticipacionSorted, toggleComercialParticipacionSort, renderComercialParticipacion,
  renderComercialFilterBar,
} = sandbox;
const state = vm.runInContext("state", sandbox);
const COMERCIAL_PARTICIPACION_ROWS = vm.runInContext("COMERCIAL_PARTICIPACION_ROWS", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

function resetRows() {
  COMERCIAL_PARTICIPACION_ROWS.length = 0;
  COMERCIAL_PARTICIPACION_ROWS.push(
    { idFrecuencia: "Mensual", permiteAdelanto: "Si", condicionPago: "30", cliente: "A", cp: "CP1",
      vendidasByYear: { "2026": { "En curso": 10 } }, certificadasByYear: { "2026": { "En curso": 30 } } },
    { idFrecuencia: "Trimestral", permiteAdelanto: "No", condicionPago: "60", cliente: "B", cp: "CP1",
      vendidasByYear: { "2026": { "En curso": 30 } }, certificadasByYear: { "2026": { "En curso": 10 } } },
    { idFrecuencia: "Anual", permiteAdelanto: "No", condicionPago: "30", cliente: "C", cp: "CP2",
      vendidasByYear: { "2026": { "En curso": 20 } }, certificadasByYear: { "2026": { "En curso": 20 } } },
  );
  state.comercialParticipacionAnios = new Set(["2026"]);
  state.comercialStatus = { "En curso": true, "Finalizado": true };
  state.filters.comercial = {};
  state.perspective.comercial = "Participación";
  state.comercialParticipacionDataStatus = "ready";
  state.comercialParticipacionLoadedYears = new Set(["2026"]);
  state.comercialParticipacionYearErrors = new Set();
  state.comercialParticipacionYearLoading = new Set();
  state.comercialParticipacionSortKey = null;
  state.comercialParticipacionSortDir = "desc";
}

// --- comercialParticipacionSorted: sin clave activa, orden preservado -----
{
  resetRows();
  const rows = sandbox.comercialParticipacionSelectedRows();
  assertEqual(comercialParticipacionSorted(rows).map(r => r.cliente), ["A", "B", "C"], "sin sortKey activa, el orden de entrada se preserva (nunca reordena por default)");
}

// --- Click en Vendidas ordena desc por horasVendidas, click de nuevo → asc --
{
  resetRows();
  toggleComercialParticipacionSort("vendidas");
  assertEqual(state.comercialParticipacionSortKey, "vendidas", "primer click en 'vendidas' lo vuelve la clave activa");
  assertEqual(state.comercialParticipacionSortDir, "desc", "primer click en una columna nueva arranca en desc");
  let rows = comercialParticipacionSorted(sandbox.comercialParticipacionSelectedRows());
  assertEqual(rows.map(r => r.cliente), ["B", "C", "A"], "desc por horasVendidas: B(30) > C(20) > A(10)");

  toggleComercialParticipacionSort("vendidas");
  assertEqual(state.comercialParticipacionSortDir, "asc", "click de NUEVO en la MISMA columna togglea la dirección");
  rows = comercialParticipacionSorted(sandbox.comercialParticipacionSelectedRows());
  assertEqual(rows.map(r => r.cliente), ["A", "C", "B"], "asc por horasVendidas: A(10) < C(20) < B(30)");
}

// --- Certificadas ordena por SU PROPIO numerador — independiente de Vendidas --
{
  resetRows();
  toggleComercialParticipacionSort("certificadas");
  const rows = comercialParticipacionSorted(sandbox.comercialParticipacionSelectedRows());
  assertEqual(rows.map(r => r.cliente), ["A", "C", "B"], "desc por horasCertificadas: A(30) > C(20) > B(10) — orden DISTINTO al de Vendidas (no son transform monótono una de la otra)");
}

// --- Cambiar de columna activa resetea a desc (nunca hereda la dirección
// --- de la columna anterior) ----------------------------------------------
{
  resetRows();
  toggleComercialParticipacionSort("vendidas");
  toggleComercialParticipacionSort("vendidas"); // ahora asc
  assertEqual(state.comercialParticipacionSortDir, "asc", "precondición: vendidas quedó en asc");
  toggleComercialParticipacionSort("certificadas");
  assertEqual(state.comercialParticipacionSortKey, "certificadas", "cambiar de columna activa la nueva clave");
  assertEqual(state.comercialParticipacionSortDir, "desc", "cambiar de columna resetea a desc, nunca hereda 'asc' de la columna anterior");
}

// --- Headers clickeables presentes en el HTML renderizado, con flecha en
// --- la columna activa únicamente ------------------------------------------
{
  resetRows();
  toggleComercialParticipacionSort("vendidas");
  const html = renderComercialParticipacion();
  assertTrue(html.includes('onclick="toggleComercialParticipacionSort(\'vendidas\')"'), "header de Vendidas es clickeable");
  assertTrue(html.includes('onclick="toggleComercialParticipacionSort(\'certificadas\')"'), "header de Certificadas es clickeable");
  const vendidasHeaderIdx = html.indexOf("toggleComercialParticipacionSort('vendidas')");
  const vendidasHeaderEnd = html.indexOf("</th>", vendidasHeaderIdx);
  assertTrue(html.slice(vendidasHeaderIdx, vendidasHeaderEnd).includes("▼"), "columna ACTIVA (vendidas, desc) muestra la flecha ▼");
  const certHeaderIdx = html.indexOf("toggleComercialParticipacionSort('certificadas')");
  const certHeaderEnd = html.indexOf("</th>", certHeaderIdx);
  assertTrue(!html.slice(certHeaderIdx, certHeaderEnd).includes("▼") && !html.slice(certHeaderIdx, certHeaderEnd).includes("▲"), "columna INACTIVA (certificadas) no muestra flecha");
}

// --- idFrecuencia ya NO lleva el número antepuesto (SOURCE-PARITY, MISMO
// --- criterio que Calendario: solo texto) ----------------------------------
{
  resetRows();
  const html = renderComercialParticipacion();
  assertTrue(html.includes(">Mensual<") || html.includes("Mensual"), "el valor de Frecuencia se muestra sin número (Mensual, nunca '1 Mensual')");
  assertTrue(!html.includes("1 Mensual") && !html.includes("3 Trimestral"), "NUNCA aparece el índice numérico antepuesto en la fila renderizada");
}

// --- Filtro de Frecuencia: presente en el filter bar, posicionado
// --- DESPUÉS de Cliente y ANTES de Año (Participación), AUSENTE en Salud --
{
  resetRows();
  const fb = renderComercialFilterBar();
  const clienteIdx = fb.indexOf("setFilter('comercial','cliente'");
  const frecuenciaIdx = fb.indexOf("setFilter('comercial','frecuencia'");
  const anioIdx = fb.indexOf("toggleComercialParticipacionAnio(");
  assertTrue(clienteIdx !== -1 && frecuenciaIdx !== -1 && anioIdx !== -1, "el filter bar de Participación monta Cliente + Frecuencia + Año");
  assertTrue(clienteIdx < frecuenciaIdx && frecuenciaIdx < anioIdx, "orden: Cliente → Frecuencia (al lado) → Año (antes)");

  state.perspective.comercial = "Salud de Cliente";
  const fbSalud = renderComercialFilterBar();
  assertTrue(!fbSalud.includes("setFilter('comercial','frecuencia'"), "Salud de Cliente NUNCA monta el filtro de Frecuencia (esas filas no tienen ese campo)");
}

if (failures > 0) {
  console.log(`${failures} participacion sort/frecuencia invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL PARTICIPACION SORT/FRECUENCIA INVARIANTS PASSED");
