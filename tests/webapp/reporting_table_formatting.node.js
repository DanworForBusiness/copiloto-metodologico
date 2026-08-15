// reporting_table_formatting.node.js — invocado por
// test_reporting_table_formatting.py (mismo patrón que
// reporting_kpi_primitive.node.js/reporting_rollup_reconciliation.node.js):
// carga reporting.js en un sandbox mínimo y ejecuta el PRIMITIVO REAL
// (`renderBusinessTreeRows` + `buildTree`) contra un total con ruido de
// punto flotante REAL de JS (1.2+1.2+1.2 === 3.5999999999999996) — CRITICAL
// TRANSVERSAL FORMATTING CORRECTIVE: la corrective anterior (KPI primitive)
// cerró el bypass en `renderDashboardKpi` y en los footers de matriz
// mensual, pero `renderBusinessTreeRows` (el motor de tabla de negocio de
// Cartera/Capacidad-Productivas/Comercial-Participación) seguía
// interpolando el número CRUDO devuelto por `sumField` sin `formatFn` — el
// artefacto reportado por el owner en Horas Productivas ("3.5999999999999996").
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

const { renderBusinessTreeRows, buildTree, sumField } = sandbox;

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

// --- Reproduce el artefacto EXACTO reportado por el owner (Horas
// Productivas: 3 filas de 1.2hs bajo el mismo Cliente/Proyecto). ---
{
  const rows = [
    { cliente: "Andesmar", proyecto: "Soporte", horas: 1.2 },
    { cliente: "Andesmar", proyecto: "Soporte", horas: 1.2 },
    { cliente: "Andesmar", proyecto: "Soporte", horas: 1.2 },
  ];
  assertTrue(rows.reduce((s, r) => s + r.horas, 0) === 3.5999999999999996, "fixture reproduce el ruido de punto flotante real de JS (precondición del test)");

  const columns = [{ label: "Horas", numeric: true, render: r => r.horas, rollup: sumField("horas") }];
  const tree = buildTree(rows, ["cliente", "proyecto"]);
  const html = renderBusinessTreeRows("test::productivas", tree, 0, columns);

  assertTrue(!html.includes("3.5999999999999996"), "el artefacto crudo NUNCA debe aparecer en el HTML de la tabla");
  assertTrue(!/\d\.\d{3,}/.test(html), "ningún número en la tabla debe tener 3+ decimales (fmtNum(hours) redondea a 1)");
  assertTrue(html.includes("3.6"), "el total se muestra limpio (3.6, redondeado a 1 decimal)");
}

// --- Contrato 0 (owner: "0 debe renderizar como 0, nunca en blanco"). ---
{
  const rows = [{ cliente: "X", proyecto: "Y", horas: 0 }];
  const columns = [{ label: "Horas", numeric: true, render: r => r.horas, rollup: sumField("horas") }];
  const tree = buildTree(rows, ["cliente", "proyecto"]);
  const html = renderBusinessTreeRows("test::cero", tree, 0, columns);
  assertTrue(/<td[^>]*>0<\/td>/.test(html), "0 debe renderizar como '0' en la celda, nunca vacío/blanco");
}

if (failures > 0) {
  console.log(`${failures} table formatting invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL TABLE FORMATTING INVARIANTS PASSED");
process.exit(0);
