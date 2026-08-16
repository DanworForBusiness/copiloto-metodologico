// reporting_capacidad_ux_corrective.node.js — invocado por
// test_reporting_capacidad_ux_corrective.py. OWNER LIVE UX CORRECTIVE (2da
// pasada sobre Horas Productivas): (1) Mes multi-select — el PBIP original
// permite tildar VARIOS meses a la vez sobre CADA panel, un `<select>` de
// valor único no lo representa; (2) headers de Horas/% Incidencia
// clickeables para ordenar asc/desc. Carga reporting.js en un sandbox
// mínimo y ejecuta el MOTOR REAL (`capacidadProductivasSelectedRows`,
// `_maskMeses`, `renderCapacidadProductivas`) contra fixtures sintéticas —
// prueba comportamiento real, no solo presencia de un patrón en el source.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {} },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  capacidadProductivasSelectedRows, capacidadPanelAnioKey, renderCapacidadProductivas,
  capacidadProductivasAnioOptions, capacidadProductivasDefaultAnio, renderCapacidadFilterBar,
} = sandbox;
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);

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

CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
CAPACIDAD_PRODUCTIVAS_ROWS.push(
  {
    cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
    horasByYear: { "2019": [10, 20, 30, 40, 10, 10, 10, 10, 10, 10, 10, 10] },
  },
  {
    cliente: "AySA", proyecto: "Costeo Federal", horas: 0, porcentajePart: 8, estado: "En curso",
    horasByYear: { "2019": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  },
);

// --- Mes vacío (sin tildar ningún botón) = "Todos" — preserva el default --
// --- histórico del <select> retirado (owner: nunca romper el comportamiento
// --- previo por default) ---------------------------------------------------
{
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
  const rows = capacidadProductivasSelectedRows("cliente");
  const ypf = rows.find(r => r.cliente === "YPF");
  assertEqual(ypf.horas, 10 + 20 + 30 + 40 + 10 * 8, "Mes vacío = Todos (suma los 12 meses, mismo default que el <select> retirado)");
}

// --- Mes con UN mes tildado — MISMO resultado que el <select> original ----
{
  state.capacidadMesesSeleccionados = { cliente: new Set(["3"]), clientePart: new Set() }; // Marzo
  const rows = capacidadProductivasSelectedRows("cliente");
  const ypf = rows.find(r => r.cliente === "YPF");
  assertEqual(ypf.horas, 30, "Un solo mes tildado (Marzo) aísla ESE mes (30) — mismo comportamiento que el <select> single-select retirado");
}

// --- Mes con VARIOS meses tildados — SUMA solo esos, nunca los demás ------
{
  state.capacidadMesesSeleccionados = { cliente: new Set(["1", "2", "3"]), clientePart: new Set() }; // Ene+Feb+Mar
  const rows = capacidadProductivasSelectedRows("cliente");
  const ypf = rows.find(r => r.cliente === "YPF");
  assertEqual(ypf.horas, 10 + 20 + 30, "VARIOS meses tildados (owner: 'permite seleccionar los meses que quieras') suman solo esos — la capacidad NUEVA que el <select> de valor único no representaba");
}

// --- Cada panel tiene su PROPIO Set de meses — nunca compartido -----------
{
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(["1"]), clientePart: new Set(["1", "2"]) };
  const rowsCliente = capacidadProductivasSelectedRows("cliente");
  const rowsPart = capacidadProductivasSelectedRows("clientePart");
  const ypfCliente = rowsCliente.find(r => r.cliente === "YPF");
  const ypfPart = rowsPart.find(r => r.cliente === "YPF");
  assertEqual(ypfCliente.horas, 10, "panel Cliente con Set propio {Enero} aísla ese mes");
  assertEqual(ypfPart.horas, 30, "panel Cliente+Part con Set propio {Enero,Febrero} suma ambos — independiente del izquierdo (nunca 10)");
}

// --- Orden clickeable: desc (default) mantiene el Rank original -----------
{
  state.filters.capacidad = { cliente: "" };
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
  state.capacidadProductivasSortDir = { cliente: "desc", clientePart: "desc" };
  state.perspective.capacidad = "Productivas";
  state.batchBDataStatus = "ready";

  const htmlDesc = renderCapacidadProductivas();
  const leftStartDesc = htmlDesc.indexOf('<div class="dashboard-section">');
  const rightStartDesc = htmlDesc.indexOf('<div class="dashboard-section">', leftStartDesc + 1);
  const leftDesc = htmlDesc.slice(leftStartDesc, rightStartDesc);
  const ypfIdxDesc = leftDesc.indexOf(">YPF<");
  const aysaIdxDesc = leftDesc.indexOf(">AySA<");
  assertTrue(ypfIdxDesc !== -1 && aysaIdxDesc !== -1 && ypfIdxDesc < aysaIdxDesc, "orden DESC (default): YPF (más horas) aparece ANTES que AySA — mismo Rank original del PBIP");

  // --- Togglear a asc invierte el orden — Horas y % Incidencia comparten ---
  // --- LA MISMA dirección (transform monótono, ver comentario en state) ---
  state.capacidadProductivasSortDir.cliente = "asc";
  const htmlAsc = renderCapacidadProductivas();
  const leftStartAsc = htmlAsc.indexOf('<div class="dashboard-section">');
  const rightStartAsc = htmlAsc.indexOf('<div class="dashboard-section">', leftStartAsc + 1);
  const leftAsc = htmlAsc.slice(leftStartAsc, rightStartAsc);
  const ypfIdxAsc = leftAsc.indexOf(">YPF<");
  const aysaIdxAsc = leftAsc.indexOf(">AySA<");
  assertTrue(aysaIdxAsc !== -1 && ypfIdxAsc !== -1 && aysaIdxAsc < ypfIdxAsc, "orden ASC: AySA (menos horas) aparece ANTES que YPF — el toggle invierte el Rank");

  // El panel derecho (clientePart) NUNCA cambió de orden — sort es POR PANEL.
  const rightAsc = htmlAsc.slice(rightStartAsc);
  const ypfIdxRight = rightAsc.indexOf(">YPF ");
  const aysaIdxRight = rightAsc.indexOf(">AySA ");
  assertTrue(ypfIdxRight !== -1 && aysaIdxRight !== -1 && ypfIdxRight < aysaIdxRight, "panel derecho (clientePart) sigue en DESC (su propio sortDir, nunca afectado por el toggle del izquierdo)");
}

// ============================================================================
// OWNER LIVE UX CORRECTIVE (2da pasada): Estado/Proyecto/Año default/Mes
// estructuralmente estable — ver reporting.js renderCapacidadFilterBar/
// capacidadProductivasSelectedRows/capacidadProductivasDefaultAnio/
// capacidadProductivasMesButtons.
// ============================================================================

// --- Estado escopea la POBLACIÓN real (como Año/Mes) — MISMO criterio que
// --- Cartera/Rentabilidad, nunca una dimensión REMOVEFILTERS -------------
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10] } },
    { cliente: "Molinos", proyecto: "Legacy", horas: 0, porcentajePart: 3, estado: "Finalizado",
      horasByYear: { "2019": [7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7] } },
  );
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };

  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  let rows = capacidadProductivasSelectedRows("cliente");
  assertEqual(rows.map(r => r.cliente).sort(), ["YPF"], "Estado default (SOLO En curso) excluye Molinos (Finalizado) de la población real");

  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  rows = capacidadProductivasSelectedRows("cliente");
  assertEqual(rows.map(r => r.cliente).sort(), ["Molinos", "YPF"], "tildar Finalizado también incluye a Molinos en la población");
}

// --- Estado, como en las demás tabs, escopea qué Años aparecen como opción
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1] } },
    { cliente: "Molinos", proyecto: "Legacy", horas: 0, porcentajePart: 3, estado: "Finalizado",
      horasByYear: { "2018": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = {};
  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  assertEqual(capacidadProductivasAnioOptions(), ["2019"], "con SOLO 'En curso' tildado, 2018 (año de Molinos, Finalizado) NO aparece como opción de Año");
  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  assertEqual(capacidadProductivasAnioOptions(), ["2018", "2019"], "tildar Finalizado también trae 2018 de vuelta a las opciones");
}

// --- Proyecto es REMOVEFILTERS (narrows la POBLACIÓN mostrada — el rollup
// --- de "YPF" pasa de 180 a 120 —, pero el DENOMINADOR fijo del %
// --- Incidencia sigue siendo 180, MISMO criterio que Cliente) —————————
// --- Por defecto el árbol arranca colapsado (treeExpandedSet vacío), así
// --- que se verifica en el rollup del nodo Cliente, no en una fila hija
// --- todavía no expandida.
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10] } },
    { cliente: "YPF", proyecto: "Costeo", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5] } },
  );
  state.filters.capacidad = { cliente: "", proyecto: "SADI v2" };
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
  state.capacidadProductivasSortDir = { cliente: "desc", clientePart: "desc" };
  state.perspective.capacidad = "Productivas";
  state.batchBDataStatus = "ready";
  const html = renderCapacidadProductivas();
  const ypfRowStart = html.indexOf(">YPF<");
  const ypfRow = html.slice(ypfRowStart, ypfRowStart + 200);
  assertTrue(ypfRow.includes(">120<"), "Proyecto='SADI v2': el rollup de YPF muestra 120 (SOLO SADI v2, Costeo narrowed out)");
  assertTrue(ypfRow.includes("66.7%"), "el % Incidencia (120/180) prueba que el DENOMINADOR sigue siendo el total SIN filtrar por Proyecto (REMOVEFILTERS)");
}

// --- Año default = año actual válido (owner corrective) --------------------
{
  const currentYear = String(new Date().getFullYear());
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { [currentYear]: [1,1,1,1,1,1,1,1,1,1,1,1], "2018": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = {};
  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  assertEqual(capacidadProductivasDefaultAnio(), currentYear, "el año actual está representado en los datos ⇒ default = año actual, nunca blanco/Todos");
}
{
  // Año actual NO representado ⇒ cae al año más reciente disponible (MISMO
  // fallback que rentabilidadDefaultAnio/horasVendidasDefaultAnio), nunca
  // blanco.
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2018": [1,1,1,1,1,1,1,1,1,1,1,1], "2017": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = {};
  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  assertEqual(capacidadProductivasDefaultAnio(), "2018", "año actual NO representado ⇒ cae al año más reciente disponible, nunca a blanco");
}

// --- Estructura del toolbar de Mes SIEMPRE estable: 12 botones SIEMPRE en
// --- el DOM (owner: "que la posicion del dropdown de año no cambie de
// --- lugar... dejemos siempre la misma estructura") — meses futuros
// --- DESHABILITADOS, nunca ausentes -----------------------------------------
{
  const currentYear = String(new Date().getFullYear());
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { [currentYear]: [1,1,1,1,1,1,1,1,1,1,1,1], "2018": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = { cliente: "" };
  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
  state.capacidadProductivasSortDir = { cliente: "desc", clientePart: "desc" };
  state.perspective.capacidad = "Productivas";
  state.batchBDataStatus = "ready";

  // Año histórico (2018) — SIN cap de mes futuro: los 12 botones, NINGUNO
  // deshabilitado.
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2018";
  const htmlHist = renderCapacidadProductivas();
  const leftHist = htmlHist.slice(0, htmlHist.indexOf('<div class="dashboard-section">', htmlHist.indexOf('<div class="dashboard-section">') + 1));
  const buttonCountHist = (leftHist.match(/toggleCapacidadMes\('cliente'/g) || []).length;
  const disabledCountHist = (leftHist.match(/ disabled onclick="toggleCapacidadMes\('cliente'/g) || []).length;
  assertEqual(buttonCountHist, 12, "Año histórico (2018): SIEMPRE 12 botones de Mes en el DOM");
  assertEqual(disabledCountHist, 0, "Año histórico: NINGÚN botón deshabilitado (sin cap de mes futuro)");

  // Año en curso — SIGUEN siendo 12 botones (nunca menos), pero los meses
  // futuros están deshabilitados.
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = currentYear;
  const htmlCurrent = renderCapacidadProductivas();
  const leftCurrent = htmlCurrent.slice(0, htmlCurrent.indexOf('<div class="dashboard-section">', htmlCurrent.indexOf('<div class="dashboard-section">') + 1));
  const buttonCountCurrent = (leftCurrent.match(/toggleCapacidadMes\('cliente'/g) || []).length;
  assertEqual(buttonCountCurrent, 12, "Año en curso: SIGUEN siendo 12 botones (owner: nunca desaparecer, deshabilitar en su lugar)");
  const disabledCountCurrent = (leftCurrent.match(/ disabled onclick="toggleCapacidadMes\('cliente'/g) || []).length;
  assertTrue(disabledCountCurrent > 0, "Año en curso: AL MENOS un mes futuro queda deshabilitado (no ausente)");
}

// --- Filter bar Productivas: Cliente → Proyecto → Estado, en ese orden ----
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = {};
  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  state.perspective.capacidad = "Productivas";
  const fb = renderCapacidadFilterBar();
  const clienteIdx = fb.indexOf("setFilter('capacidad','cliente'");
  const proyectoIdx = fb.indexOf("setFilter('capacidad','proyecto'");
  const estadoIdx = fb.indexOf("toggleCapacidadStatus(");
  assertTrue(clienteIdx !== -1 && proyectoIdx !== -1 && estadoIdx !== -1, "el filter bar de Productivas monta Cliente + Proyecto + Estado");
  assertTrue(clienteIdx < proyectoIdx && proyectoIdx < estadoIdx, "orden: Cliente → Proyecto (a su derecha) → Estado (a la derecha de Proyecto)");
}

if (failures > 0) {
  console.log(`${failures} capacidad UX corrective invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CAPACIDAD UX CORRECTIVE INVARIANTS PASSED");
process.exit(0);
