// hub_tile_info.node.js — invocado por
// test_hub_tile_info.py::test_hub_tile_info_invariants_hold.
// HUB — TILE INFO CONTRACT (opcional, trust/explainability). Ejecuta el
// generador REAL de tiles KPI (`renderDashboardKpi`, reporting.js — el
// primitivo reusable que usan TODOS los tiles de Operaciones) en un sandbox
// mínimo, mismo patrón que reporting_rentabilidad_source_parity.node.js.
//
// Prueba el contrato: sin info el markup es el de siempre; con info hay
// EXACTAMENTE una afordancia, accesible, que no activa el tile padre.
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

const renderDashboardKpi = vm.runInContext("renderDashboardKpi", sandbox);
const carteraKpiTiles = vm.runInContext("carteraKpiTiles", sandbox);
const CARTERA_CONFIG = vm.runInContext("CARTERA_CONFIG", sandbox);
const CARTERA_MANTENIMIENTO_ROWS = vm.runInContext("CARTERA_MANTENIMIENTO_ROWS", sandbox);
const KPI_INFO = vm.runInContext("_KPI_INFO", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label}\n  esperado: ${expected}\n  obtenido: ${actual}`);
  }
}
const count = (s, sub) => s.split(sub).length - 1;

// --- A: tile SIN info == el tile de siempre ------------------------------
const sinInfo = renderDashboardKpi({ label: "Horas Certificadas", value: 1234.5, format: "hours" });
assertTrue(!sinInfo.includes("activity-kpi__info"), "A: sin info no hay afordancia");
assertTrue(!sinInfo.includes("chart-tooltip"), "A: sin info no hay tooltip");
assertTrue(!sinInfo.includes("<button"), "A: sin info no hay boton");
// El markup histórico intacto: card + value + label, y nada más.
assertEqual(sinInfo,
  '<div class="activity-kpi"><span class="activity-kpi__value">1,234.5</span>'
  + '<span class="activity-kpi__label">Horas Certificadas</span></div>',
  "A: markup sin info es exactamente el de siempre");
// `info` vacío o ausente se tratan igual — nunca una afordancia vacía.
assertEqual(renderDashboardKpi({ label: "X", value: 1, info: [] }),
  renderDashboardKpi({ label: "X", value: 1 }), "A: info=[] equivale a sin info");

// --- B: tile CON info -> exactamente UNA afordancia ---------------------
const conInfo = renderDashboardKpi({
  label: "Horas Trabajadas", value: 2843.5, format: "hours",
  info: ["Primera linea.", "Segunda linea."],
});
assertEqual(count(conInfo, "activity-kpi__info"), 1, "B: exactamente una afordancia");
assertEqual(count(conInfo, 'class="chart-tooltip"'), 1, "B: exactamente un tooltip");
assertEqual(count(conInfo, "<button"), 1, "B: exactamente un boton");
// El valor y el label no se tocan.
assertTrue(conInfo.includes('<span class="activity-kpi__value">2,843.5</span>'),
  "B: el valor del KPI queda intacto");
assertTrue(conInfo.includes('<span class="activity-kpi__label">Horas Trabajadas</span>'),
  "B: el label queda intacto");
// Cada línea es su propio <p> (lo que `.chart-tooltip p` ya sabe estilar).
assertEqual(count(conInfo, "<p>"), 2, "B: una linea = un <p>");
assertTrue(conInfo.includes("<p>Primera linea.</p><p>Segunda linea.</p>"),
  "B: las lineas salen en orden");

// --- C: la afordancia es del PRIMITIVO, dentro del card -----------------
// Va DESPUES del label y DENTRO del `.activity-kpi` — es el card el que la
// posiciona abajo a la derecha (position:absolute, ver tools.css), asi que no
// participa del flex y no puede cambiar el alto del tile.
assertTrue(conInfo.indexOf("activity-kpi__label") < conInfo.indexOf("activity-kpi__info"),
  "C: la afordancia va despues del label");
assertTrue(conInfo.trim().endsWith("</span></div>"),
  "C: el card cierra con el tooltip");
// El tooltip es HERMANO del boton, no su hijo: asi se ancla al CARD y su
// borde izquierdo no puede salirse del tile (ni del viewport). Medido:
// anclado al icono se salia 27,5px a 375px en la primera columna.
assertTrue(/<\/button><span class="chart-tooltip">/.test(conInfo),
  "C: el tooltip es hermano adyacente del boton");
assertTrue(!/<button[^>]*>[\s\S]*chart-tooltip[\s\S]*<\/button>/.test(conInfo),
  "C: el tooltip NO esta dentro del boton");
assertTrue(conInfo.startsWith('<div class="activity-kpi"'),
  "C: sigue siendo un solo card `.activity-kpi`");
// Reusa la CAJA compartida del Hub (`.chart-tooltip`), no una caja nueva. NO
// usa `.chart-tooltip-anchor`: ese patron envuelve al disparador, y aca el
// disparador es un icono en una esquina — el disparo va por hermano adyacente.
assertTrue(conInfo.includes('class="chart-tooltip"'), "C: reusa la caja `.chart-tooltip`");
assertTrue(!conInfo.includes("chart-tooltip-anchor"),
  "C: no usa el patron anchor (el tooltip se ancla al card)");

// --- D: accesibilidad ---------------------------------------------------
assertTrue(/aria-label="Información sobre Horas Trabajadas: /.test(conInfo),
  "D: aria-label descriptivo con el nombre del tile");
assertTrue(conInfo.includes("Primera linea. Segunda linea."),
  "D: el aria-label incluye el texto completo, no solo el titulo");
assertTrue(conInfo.includes('type="button"'),
  "D: es un <button> — foco y semantica de teclado gratis");
assertTrue(!conInfo.includes("title="), "D: no se depende de title=''");
assertTrue(conInfo.includes('aria-hidden="true"'), "D: el icono es decorativo");
assertTrue(conInfo.includes("Escape"), "D: Escape suelta el foco (cierra el tooltip)");

// --- E: no activa el tile padre ----------------------------------------
// Los tiles clickeables cablean `click` Y `keydown` DIRECTO sobre si mismos
// (initEventBreakdown, app.js), no por delegacion: hay que frenar los dos.
assertTrue(conInfo.includes('onclick="event.stopPropagation()"'),
  "E: el click no llega al tile");
assertTrue(/onkeydown="[^"]*Enter[^"]*event\.stopPropagation\(\)/.test(conInfo),
  "E: Enter/Espacio tampoco llegan al tile");

// --- F: los tiles de horas trabajadas de Operaciones reciben la metadata -
const mantHtml = carteraKpiTiles(CARTERA_CONFIG["Mantenimiento"], []);
const trabIdx = mantHtml.indexOf("Hs Trabajadas");
assertTrue(trabIdx !== -1, "F: el tile Hs Trabajadas existe en Mantenimiento");
assertEqual(count(mantHtml, "activity-kpi__info"), 1,
  "F: en Mantenimiento SOLO un tile lleva info");
// Y es el de Trabajadas: la afordancia cae dentro de su card.
const cards = mantHtml.split('<div class="activity-kpi').slice(1);
cards.forEach(c => {
  const esTrabajadas = c.includes("Hs Trabajadas");
  const tieneInfo = c.includes("activity-kpi__info");
  assertEqual(tieneInfo, esTrabajadas,
    `F: "${(c.match(/activity-kpi__label">([^<]*)/) || [])[1]}" info=${tieneInfo}`);
});
assertTrue(mantHtml.includes("Para comparar con Rentabilidad, aplicar Tipo Proyecto = Mantenimiento."),
  "F: la copy de Cartera nombra la regla de comparacion");

// --- G: los demas tiles NO ganan info de arrastre -----------------------
const proyHtml = carteraKpiTiles(CARTERA_CONFIG["Proyecto"], []);
assertEqual(count(proyHtml, "activity-kpi__info"), 0,
  "G: la perspectiva Proyecto no gana info (su contrato no esta documentado)");
["Diff Hs", "Hs Certificadas", "Hs Vendidas", "Hs Facturadas"].forEach(l => {
  const card = cards.find(c => c.includes(l));
  assertTrue(card && !card.includes("activity-kpi__info"), `G: "${l}" sin info`);
});
// Solo DOS copys cableadas en todo el archivo.
assertEqual(Object.keys(KPI_INFO).length, 2, "G: exactamente dos contratos documentados");

// --- H: la copy explica el contrato, no disculpa una diferencia ---------
const todaLaCopy = [].concat(KPI_INFO.carteraTrabajadas, KPI_INFO.rentabilidadTrabajadas).join(" ");
assertTrue(/Time Logs/.test(todaLaCopy), "H: nombra la FUENTE");
assertTrue(/Grano: proyecto/.test(todaLaCopy), "H: nombra el GRANO");
assertEqual(count(todaLaCopy, "Tipo Proyecto = Mantenimiento"), 2,
  "H: las dos copys nombran la CONDICION de comparabilidad");
[/pueden diferir/i, /puede variar/i, /aproximad/i, /error/i, /no coincid/i].forEach(rx => {
  assertTrue(!rx.test(todaLaCopy), `H: la copy no disculpa una diferencia (${rx})`);
});

if (failures > 0) {
  console.log(`${failures} Hub tile info invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB TILE INFO INVARIANTS PASSED");
process.exit(0);
