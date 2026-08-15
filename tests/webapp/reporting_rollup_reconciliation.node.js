// reporting_rollup_reconciliation.node.js — invocado por
// test_reporting_rollup_reconciliation.py::test_hierarchy_rollup_invariants_hold
// (via `node <este archivo>`), NUNCA un framework de browser-testing nuevo:
// carga reporting.js en un sandbox mínimo (vm.Context con `document`/
// `sessionStorage`/`fetch` stubeados — nada de esto se invoca, solo evita que
// el `document.addEventListener("DOMContentLoaded", render)` del final del
// script tire al cargar) y ejecuta el MOTOR REAL de árbol/rollup
// (buildTree/renderBusinessTreeRows/sumField/uniformValue) contra fixtures
// sintéticos — prueba la aritmética real, no solo presencia de un patrón de
// código en el source (corrective owner live QA — "Avanti", ver HANDOFF:
// QA4 "parent aggregate == aggregate of canonical descendant leaves").
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

const { buildTree, renderBusinessTreeRows, sumField, uniformValue, treeExpandedSet } = sandbox;

// El árbol arranca COLAPSADO por default (mismo comportamiento real de la
// UI) — para probar que un nodo padre expandido muestra a sus hijos con
// sus PROPIOS totales (no el del hermano), hay que expandirlo explícito
// antes de renderizar, igual que un click real en el disclosure.
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
function cellsOf(rowHtml) {
  // Extrae el contenido de texto de cada <td> de UNA fila <tr>...</tr>.
  const cells = [...rowHtml.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(m => stripTags(m[1]));
  return cells;
}
function rowsOf(html) {
  return [...html.matchAll(/<tr[^>]*>[\s\S]*?<\/tr>/g)].map(m => m[0]);
}

// Columnas sintéticas: Trabajadas/Certificadas/Vendidas/Facturadas
// (aditivas, Clase A) + Diff (derivada, Certificadas - Trabajadas por
// fila — recalculada vía sumField, NUNCA promediando diffs ya calculados
// de un padre distinto).
const COLUMNS = [
  { key: "diff", numeric: true, render: r => String(r.diff), rollup: sumField("diff") },
  { key: "trabajadas", numeric: true, render: r => String(r.trabajadas), rollup: sumField("trabajadas") },
  { key: "certificadas", numeric: true, render: r => String(r.certificadas), rollup: sumField("certificadas") },
  { key: "vendidas", numeric: true, render: r => String(r.vendidas), rollup: sumField("vendidas") },
  { key: "facturadas", numeric: true, render: r => String(r.facturadas), rollup: sumField("facturadas") },
  { key: "estado", numeric: false, render: r => r.status, rollup: uniformValue("status") },
];

function mkRow(cliente, proyecto, trabajadas, certificadas, vendidas, facturadas, status) {
  return { cliente, proyecto, trabajadas, certificadas, vendidas, facturadas, diff: certificadas - trabajadas, status };
}

// --- Test 1: single child — parent additive values == child's, exactamente
// (reproduce el caso "Avanti": UN proyecto, PERO con 2 hechos MENSUALES
// bajo la MISMA clave hoja cliente+proyecto). ---
{
  const rows = [
    mkRow("Avanti", "Desarrollo Tableros", 0, 20, 20, 20, "En curso"),
    mkRow("Avanti", "Desarrollo Tableros", 0, 20, 20, 20, "En curso"),
  ];
  const tree = buildTree(rows, ["cliente", "proyecto"]);
  const html = renderBusinessTreeRows("test::single-child", tree, 0, COLUMNS);
  const rowsHtml = rowsOf(html);
  assertEqual(rowsHtml.length, 1, "single-child: exactamente 1 fila (padre y único hijo comparten la misma clave hoja, sin nivel de Mes)");
  const cells = cellsOf(rowsHtml[0]);
  // [0]=label(caret+cliente), luego columnas: diff,trabajadas,certificadas,vendidas,facturadas,estado
  assertEqual(cells[1], "40", "single-child: diff agregado (2 hechos mensuales)");
  assertEqual(cells[2], "0", "single-child: trabajadas agregada");
  assertEqual(cells[3], "40", "single-child: certificadas agregada (NO trunca al primer hecho)");
  assertEqual(cells[4], "40", "single-child: vendidas agregada");
  assertEqual(cells[5], "40", "single-child: facturadas agregada");
}

// --- Test 2: multi-child — Cliente > Proyecto, 2 proyectos distintos bajo
// el mismo Cliente; el padre Cliente debe ser la SUMA de ambos proyectos,
// y CADA proyecto debe mostrar su propio total (no el del hermano). ---
{
  const rows = [
    mkRow("YPF", "Proyecto A", 10, 20, 30, 20, "En curso"),
    mkRow("YPF", "Proyecto B", 5, 8, 12, 8, "En curso"),
  ];
  const tree = buildTree(rows, ["cliente", "proyecto"]);
  expandAll(tree, "test::multi-child");
  const html = renderBusinessTreeRows("test::multi-child", tree, 0, COLUMNS);
  const rowsHtml = rowsOf(html);
  assertEqual(rowsHtml.length, 3, "multi-child: 1 fila Cliente + 2 filas Proyecto");
  const clienteCells = cellsOf(rowsHtml[0]);
  assertEqual(clienteCells[1], "13", "multi-child: Diff del Cliente = SUM(diffs) = (20-10)+(8-5)");
  assertEqual(clienteCells[2], "15", "multi-child: Trabajadas del Cliente = 10+5");
  assertEqual(clienteCells[3], "28", "multi-child: Certificadas del Cliente = 20+8");
  assertEqual(clienteCells[4], "42", "multi-child: Vendidas del Cliente = 30+12");
  assertEqual(clienteCells[5], "28", "multi-child: Facturadas del Cliente = 20+8");
  const projA = cellsOf(rowsHtml[1]);
  const projB = cellsOf(rowsHtml[2]);
  assertEqual(projA[2], "10", "multi-child: Proyecto A conserva SU propio total, no el del hermano");
  assertEqual(projB[2], "5", "multi-child: Proyecto B conserva SU propio total, no el del hermano");
}

// --- Test 3: filtered population — quitar Proyecto B (mismo criterio que
// un filtro activo excluyendo esas filas ANTES de buildTree) debe dejar al
// Cliente EXACTAMENTE igual al Proyecto A restante, sin totales stale del
// conjunto sin filtrar. ---
{
  const rowsFiltered = [mkRow("YPF", "Proyecto A", 10, 20, 30, 20, "En curso")];
  const tree = buildTree(rowsFiltered, ["cliente", "proyecto"]);
  expandAll(tree, "test::filtered");
  const html = renderBusinessTreeRows("test::filtered", tree, 0, COLUMNS);
  const rowsHtml = rowsOf(html);
  assertEqual(rowsHtml.length, 2, "filtered: 1 fila Cliente + 1 fila Proyecto (B excluido, no fantasma)");
  const clienteCells = cellsOf(rowsHtml[0]);
  const projCells = cellsOf(rowsHtml[1]);
  assertEqual(clienteCells[2], projCells[2], "filtered: Cliente == único Proyecto visible tras filtrar (sin stale total previo)");
  assertEqual(clienteCells[2], "10", "filtered: Cliente = Proyecto A exacto, no 15 (el total sin filtrar)");
}

// --- Test 4: columna categórica (Estado) — propaga SOLO si todas las hojas
// coinciden; con valores distintos, blanco (nunca "el primero que aparezca"). ---
{
  const rowsUniform = [
    mkRow("Molinos", "Proyecto X", 1, 1, 1, 1, "En curso"),
    mkRow("Molinos", "Proyecto X", 1, 1, 1, 1, "En curso"),
  ];
  const treeUniform = buildTree(rowsUniform, ["cliente", "proyecto"]);
  const htmlUniform = renderBusinessTreeRows("test::estado-uniforme", treeUniform, 0, COLUMNS);
  assertEqual(cellsOf(rowsOf(htmlUniform)[0])[6], "En curso", "estado uniforme: propaga (ambas hojas coinciden)");

  const rowsMixed = [
    mkRow("Ternium", "Proyecto Y", 1, 1, 1, 1, "En curso"),
    mkRow("Ternium", "Proyecto Y", 1, 1, 1, 1, "Finalizado"),
  ];
  const treeMixed = buildTree(rowsMixed, ["cliente", "proyecto"]);
  const htmlMixed = renderBusinessTreeRows("test::estado-mixto", treeMixed, 0, COLUMNS);
  assertEqual(cellsOf(rowsOf(htmlMixed)[0])[6], "", "estado mixto: blanco, NUNCA el primero/mayoría");
}

if (failures > 0) {
  console.log(`${failures} reconciliation invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL RECONCILIATION INVARIANTS PASSED");
process.exit(0);
