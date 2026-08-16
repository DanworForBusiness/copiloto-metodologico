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

const { capacidadProductivasSelectedRows, capacidadPanelAnioKey, renderCapacidadProductivas } = sandbox;
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
    cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5,
    horasByYear: { "2019": [10, 20, 30, 40, 10, 10, 10, 10, 10, 10, 10, 10] },
  },
  {
    cliente: "AySA", proyecto: "Costeo Federal", horas: 0, porcentajePart: 8,
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

if (failures > 0) {
  console.log(`${failures} capacidad UX corrective invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CAPACIDAD UX CORRECTIVE INVARIANTS PASSED");
process.exit(0);
