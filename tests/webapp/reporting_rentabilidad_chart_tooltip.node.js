// reporting_rentabilidad_chart_tooltip.node.js — invocado por
// test_reporting_rentabilidad_chart_tooltip.py. Owner request: "al menos en
// graficos los tooltip debieran ser una experiencia transversal... en
// asignaciones renderizamos un tooltip propio... asi como calendario tiene
// en su tabla al final un total calculado, me gustaria incluir lo mismo en
// rentabilidad". Carga reporting.js en un sandbox mínimo y ejecuta el MOTOR
// REAL (chartTooltip/rentabilidadBarRow/renderRentabilidadCertEjecutadas)
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
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  initDashboardSections() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { chartTooltip, rentabilidadBarRow, renderRentabilidadCertEjecutadas } = sandbox;
const state = vm.runInContext("state", sandbox);
const RENTABILIDAD_PROYECTOS = vm.runInContext("RENTABILIDAD_PROYECTOS", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

// --- chartTooltip: primitivo compartido, MISMA forma que el de Staffing ---
{
  const single = chartTooltip("Una línea");
  assertTrue(single.includes('class="chart-tooltip"'), "chartTooltip usa la clase compartida .chart-tooltip");
  assertTrue(single.includes('aria-hidden="true"'), "el tooltip visual queda aria-hidden (MISMO criterio que Staffing — el aria-label ya lo anuncia)");
  assertTrue(single.includes("<p>Una línea</p>"), "string único → una sola <p>");

  const multi = chartTooltip(["Línea 1", "Línea 2", "Línea 3"]);
  assertTrue((multi.match(/<p>/g) || []).length === 3, "array de N líneas → N elementos <p> (MISMO patrón que Staffing separa Consultor/Estado/Cliente/Proyecto/Fechas)");
}

// --- rentabilidadBarRow: nunca usa el title nativo, usa el primitivo
// --- compartido + aria-label ------------------------------------------------
{
  const html = rentabilidadBarRow("Proyecto X", 42, 100);
  assertTrue(!/title="/.test(html), "rentabilidadBarRow NUNCA usa el atributo title nativo del navegador");
  assertTrue(html.includes("chart-tooltip-anchor"), "el contenedor de la barra es un chart-tooltip-anchor");
  assertTrue(html.includes('class="chart-tooltip"'), "incluye el tooltip visual compartido");
  assertTrue(html.includes('aria-label="Proyecto X'), "aria-label repite el mismo contenido en plano, accesible sin el tooltip visual");
  assertTrue(html.includes('tabindex="0"'), "foco-able por teclado (MISMO criterio que Staffing — :focus también revela el tooltip)");
}

// --- Certificadas vs Ejecutadas: mismos criterios + fila Total nueva ------
{
  RENTABILIDAD_PROYECTOS.length = 0;
  RENTABILIDAD_PROYECTOS.push(
    { tipo: "Mantenimiento", cliente: "Danone", cp: "María López", proyecto: "Soporte Analítica", estado: "En curso",
      certByYear: { "2026": [10,0,0,0,0,0,0,0,0,0,0,0] }, trabByYear: { "2026": [8,0,0,0,0,0,0,0,0,0,0,0] },
      certMonthly: [10,0,0,0,0,0,0,0,0,0,0,0], trabMonthly: [8,0,0,0,0,0,0,0,0,0,0,0],
      certificadas: 10, trabajadas: 8 },
    { tipo: "Proyecto", cliente: "YPF", cp: "Diego Ramos", proyecto: "SADI v2", estado: "En curso",
      certByYear: { "2026": [20,0,0,0,0,0,0,0,0,0,0,0] }, trabByYear: { "2026": [15,0,0,0,0,0,0,0,0,0,0,0] },
      certMonthly: [20,0,0,0,0,0,0,0,0,0,0,0], trabMonthly: [15,0,0,0,0,0,0,0,0,0,0,0],
      certificadas: 20, trabajadas: 15 },
  );
  // AÑO/MES MULTI-SELECT corrective: anio/mes son Sets propios ahora.
  state.filters.rentabilidad = {};
  state.rentabilidadAnios = new Set(["2026"]);
  state.rentabilidadMeses = new Set();
  state.rentabilidadStatus = { "En curso": true, "Finalizado": true };
  state.rentabilidadRankingExpanded = { positivos: false, negativos: false };
  state.rentabilidadDataStatus = "ready";
  state.perspective.rentabilidad = "Certificadas vs Ejecutadas";

  const html = renderRentabilidadCertEjecutadas();
  assertTrue(!/title="/.test(html), "Certificadas vs Ejecutadas NUNCA usa el title nativo tampoco");
  assertTrue((html.match(/class="chart-tooltip"/g) || []).length >= 2, "cada bar-row (Certificadas + Ejecutadas, por Tipo) trae su propio tooltip compartido");

  // Fila Total (owner request, MISMO criterio que Calendario `tr.is-total`):
  // Certificadas 10+20=30, Trabajadas 8+15=23, Margen Absoluto 30-23=7,
  // Margen Relativo 7/30*100 ≈ 23.3%.
  assertTrue(html.includes('class="is-total"'), "la tabla 'Detalle por proyecto' tiene una fila Total (MISMO primitivo tr.is-total que Calendario)");
  const totalRowIdx = html.indexOf('class="is-total"');
  const totalRowEnd = html.indexOf("</tr>", totalRowIdx);
  const totalRow = html.slice(totalRowIdx, totalRowEnd);
  assertTrue(totalRow.includes(">30<") || totalRow.includes("30"), "Total Certificadas = 10+20 = 30");
  assertTrue(totalRow.includes("23"), "Total Ejecutadas = 8+15 = 23");
  assertTrue(totalRow.includes("+7"), "Total Margen Absoluto = 30-23 = +7");
  // El Total va DESPUÉS de las filas de proyecto individuales, nunca antes.
  const sadiIdx = html.indexOf("SADI v2");
  assertTrue(sadiIdx !== -1 && sadiIdx < totalRowIdx, "la fila Total aparece AL FINAL de la tabla, después de los proyectos individuales");
}

if (failures > 0) {
  console.log(`${failures} rentabilidad chart-tooltip/total invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL RENTABILIDAD CHART-TOOLTIP/TOTAL INVARIANTS PASSED");
