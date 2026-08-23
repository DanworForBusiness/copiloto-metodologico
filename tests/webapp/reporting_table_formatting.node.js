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

const { renderBusinessTreeRows, buildTree, sumField, hierarchyToggleHtml } = sandbox;

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

// --- "Sin dato" (owner live QA — Participación real: ID Frecuencia/
// Permite Adelanto/Condición de Pago son OPCIONALES en la fuente real, a
// diferencia del mock donde siempre estaban poblados — un valor vacío
// dejaba el nodo de jerarquía sin texto visible, "missing texts"). ---
{
  const rows = [
    { idFrecuencia: "", cliente: "SinDatos SA" },
    { idFrecuencia: "1 Mensual", cliente: "ConDatos SA" },
  ];
  const tree = buildTree(rows, ["idFrecuencia", "cliente"]);
  const blankNode = tree.find(n => n.pathKey === "");
  assertTrue(!!blankNode, "las filas con valor vacío siguen agrupándose entre sí (pathKey crudo)");
  assertTrue(blankNode.label === "Sin dato", "el label visible de un valor vacío es 'Sin dato', nunca texto invisible");
  const html = hierarchyToggleHtml("test", blankNode.pathKey, blankNode.label, true, false, "▸");
  assertTrue(html.includes("Sin dato"), "el HTML del nodo realmente contiene el texto de fallback");

  const populated = tree.find(n => n.label === "1 Mensual");
  assertTrue(!!populated, "un valor con dato real conserva su label tal cual, sin pisar el fallback");
}

if (failures > 0) {
  console.log(`${failures} table formatting invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL TABLE FORMATTING INVARIANTS PASSED");
process.exit(0);
