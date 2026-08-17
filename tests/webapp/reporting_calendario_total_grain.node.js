// reporting_calendario_total_grain.node.js — invocado por
// test_reporting_calendario_total_grain.py::test_calendario_total_grain_invariants_hold
// (via `node <este archivo>`), mismo patrón que
// reporting_rollup_reconciliation.node.js (QA4): carga reporting.js en un
// sandbox mínimo y ejecuta el MOTOR REAL (buildTree/renderTreeRows/
// sumMonthly) contra fixtures sintéticos que reproducen la tabla calculada
// PRIMARIA del PBIP `Calendario_Cliente` (ver Danwor - Tablero
// Integral.SemanticModel/definition/tables/Calendario_Cliente.tmdl):
// UNA fila por Cliente, 12 columnas booleanas (Ene..Dic, 1 si
// CONTAINSSTRING(Calendario_Act, <mes>) > 0), `Total` = suma de esas 12
// columnas POR FILA (grano Cliente, no un gran total de compañía).
//
// Q3 (owner live QA): "el gran total suma aritméticamente bien pero los
// totales mensuales individuales no reconcilian con los chequeos
// booleanos visibles" — investigado contra evidencia PRIMARIA: es
// comportamiento CORRECTO por diseño (mismo grano que Power BI: un
// subtotal de columna en una matriz SIEMPRE se calcula contra el grano de
// hoja subyacente, INDEPENDIENTE del estado expandido/colapsado de las
// filas — nunca cambia porque el usuario colapsó un grupo). La fila
// "Total" del pie (`sumMonthly(rows)`) sobre la lista PLANA de Cliente es
// EXACTAMENTE ese grano — nunca se recalcula por nodo de árbol, nunca
// doble-cuenta.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { buildTree, renderTreeRows, sumMonthly, treeExpandedSet } = sandbox;

function expandAll(nodes, stateKey) {
  const set = treeExpandedSet(stateKey);
  nodes.forEach(n => {
    if (n.children && n.children.length) {
      set.add(n.pathKey);
      expandAll(n.children, stateKey);
    }
  });
}

let failures = 0;
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
function stripTags(html) { return html.replace(/<[^>]*>/g, "").trim(); }
function cellsOf(rowHtml) { return [...rowHtml.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(m => stripTags(m[1])); }
function rowsOf(html) { return [...html.matchAll(/<tr[^>]*>[\s\S]*?<\/tr>/g)].map(m => m[0]); }

// 3 Clientes (grano PBIP: 1 fila por Cliente), reproduce
// `Calendario_Cliente` — booleano 0/1 por mes:
//   Andesmar (Mensual, CP María López): checkpoint TODOS los meses.
//   Danone   (Mensual, CP María López): checkpoint SOLO Ene/Jul.
//   YPF      (Trimestral, CP Diego Ramos): checkpoint Ene/Abr/Jul/Oct.
const rows = [
  { frecuencia: "Mensual", cliente: "Andesmar", cp: "María López", monthly: [1,1,1,1,1,1,1,1,1,1,1,1] },
  { frecuencia: "Mensual", cliente: "Danone", cp: "María López", monthly: [1,0,0,0,0,0,1,0,0,0,0,0] },
  { frecuencia: "Trimestral", cliente: "YPF", cp: "Diego Ramos", monthly: [1,0,0,1,0,0,1,0,0,1,0,0] },
];

// --- Test 1: footer Total mensual (grano hoja Cliente, PLANO) — Enero =
// COUNT de Clientes con checkpoint ese mes = 3 (los 3 tienen Enero). ---
{
  const totalMonthly = sumMonthly(rows);
  assertEqual(totalMonthly[0], 3, "footer Enero: 3 Clientes tienen checkpoint en enero (Andesmar+Danone+YPF)");
  assertEqual(totalMonthly[1], 1, "footer Febrero: solo Andesmar (Danone/YPF no tienen Feb)");
  const grandTotal = totalMonthly.reduce((a, b) => a + b, 0);
  assertEqual(grandTotal, 12 + 2 + 4, "footer gran total: suma aritmética de los 3 Total-por-fila (12+2+4=18)");
}

// --- Test 2: el footer NUNCA cambia según el estado colapsado/expandido
// del árbol (mismo comportamiento que un subtotal de columna real en Power
// BI — grano de hoja fijo, independiente de la UI de disclosure). ---
{
  const stateKeyCollapsed = "test::calendario-collapsed";
  const stateKeyExpanded = "test::calendario-expanded";
  const treeA = buildTree(rows, ["frecuencia", "cliente", "cp"]);
  const treeB = buildTree(rows, ["frecuencia", "cliente", "cp"]);
  expandAll(treeB, stateKeyExpanded);
  // Colapsado: renderTreeRows NO expande nada (set vacío) — solo se ven
  // los nodos de nivel 0 (Mensual/Trimestral).
  renderTreeRows(stateKeyCollapsed, treeA, 0, true);
  renderTreeRows(stateKeyExpanded, treeB, 0, true);
  const totalCollapsed = sumMonthly(rows);
  const totalExpanded = sumMonthly(rows);
  assertEqual(JSON.stringify(totalCollapsed), JSON.stringify(totalExpanded), "footer idéntico colapsado vs expandido — el grano de hoja nunca cambia con la UI");
}

// --- Test 3: el nodo padre "Mensual" (2 hojas: Andesmar+Danone) muestra
// ✓ (presencia booleana) en TODOS los meses donde AL MENOS un hijo tiene
// checkpoint — nunca la cuenta cruda en la celda mensual — pero su PROPIA
// columna Total (showTotal, por fila) SÍ es la cuenta real de esa fila
// (suma de sus 12 meses agregados, igual que `Calendario_Cliente[Total]`
// del PBIP: Ene+Feb+...+Dic). ---
{
  const stateKey = "test::calendario-parent-total";
  const tree = buildTree(rows, ["frecuencia", "cliente", "cp"]);
  expandAll(tree, stateKey);
  const html = renderTreeRows(stateKey, tree, 0, true, v => (v ? "✓" : ""));
  const rowsHtml = rowsOf(html);
  // Nodo "Mensual" es el primero (2 hojas agregadas: Andesmar 12 meses +
  // Danone 2 meses). cells[0] = label (caret+"Mensual"), cells[1..12] =
  // Ene..Dic, cells[13] = Total de fila.
  const mensualCells = cellsOf(rowsHtml[0]);
  assertEqual(mensualCells[1], "✓", "Mensual/Enero: ambas hojas tienen Enero -> ✓ (presencia, no '2')");
  assertEqual(mensualCells[2], "✓", "Mensual/Febrero: Andesmar SÍ tiene Feb (Danone no) -> ✓ (al menos 1 hijo)");
  assertEqual(mensualCells[13], "14", "Mensual/Total de fila: cuenta real agregada = 12 (Andesmar) + 2 (Danone) = 14, NUNCA booleana");
}

if (failures > 0) {
  console.log(`${failures} calendario total-grain invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CALENDARIO TOTAL-GRAIN INVARIANTS PASSED");
process.exit(0);
