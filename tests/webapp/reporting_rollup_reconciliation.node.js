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
  document: { addEventListener() {}, querySelector() { return null; } },
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

// --- Test 5 (TRANSVERSAL AGGREGATION AUDIT — owner: "no asumir que las
// fixes previas cubrieron todos los casos"): jerarquía de 3+ NIVELES
// (mismo patrón que Participación real: ID Frecuencia > Permite Adelanto >
// Cliente) — el rollup de un nivel INTERMEDIO debe ser EXACTAMENTE la suma
// de sus propios hijos directos, no solo el total de raíz vs. hojas
// (los tests 1-4 solo cubrían 2 niveles). ---
{
  const rows = [
    mkRow3("1 Mensual", "Si", "ClienteA", 10, 20, "En curso"),
    mkRow3("1 Mensual", "Si", "ClienteB", 5, 8, "En curso"),
    mkRow3("1 Mensual", "No", "ClienteC", 3, 3, "En curso"),
  ];
  const COLUMNS3 = [
    { key: "trabajadas", numeric: true, render: r => String(r.trabajadas), rollup: sumField("trabajadas") },
    { key: "certificadas", numeric: true, render: r => String(r.certificadas), rollup: sumField("certificadas") },
  ];
  const tree = buildTree(rows, ["idFrecuencia", "permiteAdelanto", "cliente"]);
  expandAll(tree, "test::tres-niveles");
  const html = renderBusinessTreeRows("test::tres-niveles", tree, 0, COLUMNS3);
  const rowsHtml = rowsOf(html);
  // 1 Mensual (raíz) + Si/No (nivel 2) + ClienteA/B (bajo Si) + ClienteC (bajo No) = 6
  assertEqual(rowsHtml.length, 6, "3 niveles: raíz + 2 nodos intermedios + 3 hojas");
  const root = cellsOf(rowsHtml[0]);
  assertEqual(root[1], "18", "3 niveles: raíz = SUM de TODAS las hojas (10+5+3), no solo su hijo directo 'Si'");
  const nodoSi = cellsOf(rowsHtml[1]);
  assertEqual(nodoSi[1], "15", "3 niveles: nodo intermedio 'Si' = SUM de SUS hijos directos (ClienteA+ClienteB = 10+5), nunca el total de la raíz");
  const nodoNo = cellsOf(rowsHtml[4]);
  assertEqual(nodoNo[1], "3", "3 niveles: nodo intermedio 'No' = solo ClienteC, no arrastra el total de 'Si'");
  assertEqual(root[1], String(Number(nodoSi[1]) + Number(nodoNo[1])), "3 niveles: raíz == SUM de sus 2 hijos directos intermedios (Si + No)");
}
function mkRow3(idFrecuencia, permiteAdelanto, cliente, trabajadas, certificadas, status) {
  return { idFrecuencia, permiteAdelanto, cliente, trabajadas, certificadas, status };
}

// --- Test 6: medida Clase B (% contra denominador — nunca se promedia un
// % ya calculado). Reproduce capacidadPctIncidencia/comercialPctVendidas:
// numerador SUMADO primero por el subárbol, dividido por un denominador
// FIJO recién al final — nunca sumar/promediar porcentajes de filas hijas
// individuales (eso daría un número sin sentido matemático). ---
{
  const GRAND_TOTAL = 100;
  const pctOf = horas => (horas / GRAND_TOTAL * 100).toFixed(1) + "%";
  const rows = [
    mkRow3("A", "X", "Cliente1", 30, 30, "En curso"),
    mkRow3("A", "X", "Cliente2", 20, 20, "En curso"),
  ];
  const COLUMNS_PCT = [{ key: "pct", numeric: true, render: r => pctOf(r.trabajadas), rollup: sumField("trabajadas", pctOf) }];
  const tree = buildTree(rows, ["idFrecuencia", "permiteAdelanto", "cliente"]);
  expandAll(tree, "test::pct");
  const html = renderBusinessTreeRows("test::pct", tree, 0, COLUMNS_PCT);
  const rowsHtml = rowsOf(html);
  const rootPct = cellsOf(rowsHtml[0])[1];
  // Numerador SUMADO primero (30+20=50), dividido UNA vez -> 50%.
  // Si se hubiera promediado 30% y 20% ya calculados, daría 25% (incorrecto).
  assertEqual(rootPct, "50.0%", "Clase B: % del padre = SUM(numeradores) / denominador FIJO, nunca el promedio de % ya calculados de las hojas");
}

if (failures > 0) {
  console.log(`${failures} reconciliation invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL RECONCILIATION INVARIANTS PASSED");
process.exit(0);
