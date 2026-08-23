// reporting_capacidad_composition.node.js — invocado por
// test_reporting_capacidad_composition.py (mismo patrón que
// reporting_capacidad_panel_independence.node.js): carga reporting.js en un
// sandbox mínimo y ejecuta `renderCapacidadProductivas()` REAL (no un mock
// de su HTML) contra una fixture sintética — prueba la ESTRUCTURA DOM
// resultante (owner live QA — "VISUAL COMPOSITION CORRECTIVE": cada panel
// debe ser una tarjeta autocontenida, Total Horas/Año/Mes/Expandir-Contraer
// DENTRO de cada panel, nunca "desprendidos" arriba de ambos; sin overflow
// horizontal a nivel página).
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

const { renderCapacidadProductivas, capacidadPanelAnioKey } = sandbox;
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// Fixture: 2 clientes con hechos reales — suficiente para renderizar AMBOS
// paneles con tabla (no el estado vacío).
CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
CAPACIDAD_PRODUCTIVAS_ROWS.push(
  {
    cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
    horasByYear: { "2019": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10] },
  },
  {
    cliente: "AySA", proyecto: "Costeo Federal", horas: 0, porcentajePart: 8, estado: "En curso",
    horasByYear: { "2018": [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2] },
  },
);

state.batchBDataStatus = "ready";
state.perspective.capacidad = "Productivas";
state.filters.capacidad = { cliente: "" };
state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2018";

const html = renderCapacidadProductivas();

// --- DOS contenedores de panel — cada uno una tarjeta `.dashboard-section` ---
const panelMatches = [...html.matchAll(/<div class="dashboard-section">/g)];
assertEqual(panelMatches.length, 2, "existen EXACTAMENTE dos contenedores de panel (.dashboard-section, tarjetas autocontenidas)");

// --- El primitivo compartido de 2 columnas se usa, nunca uno nuevo --------
assertTrue(html.includes('class="activity-grid-2"'), "usa el primitivo compartido .activity-grid-2 (NUNCA un grid propio)");

// Extrae el contenido de CADA panel (entre su apertura y la del siguiente/cierre
// del grid) para verificar que cada uno posee sus PROPIOS controles.
function panelSlice(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  const end = endMarker ? html.indexOf(endMarker, start) : html.length;
  return html.slice(start, end === -1 ? html.length : end);
}
const leftStart = html.indexOf('<div class="dashboard-section">');
const rightStart = html.indexOf('<div class="dashboard-section">', leftStart + 1);
const leftPanel = html.slice(leftStart, rightStart);
const rightPanel = html.slice(rightStart);

// --- Cada panel posee su PROPIO Año/Mes (nunca el del otro) ---------------
// Mes es multi-select (botones, owner corrective) — `toggleCapacidadMes('<panel>', ...)`
// reemplaza el `<select>` de valor único retirado.
assertTrue(leftPanel.includes(`setFilter('capacidad','${capacidadPanelAnioKey("cliente")}'`), "el panel IZQUIERDO monta su propio <select> de Año (anio_cliente)");
assertTrue(leftPanel.includes(`toggleCapacidadMes('cliente',`), "el panel IZQUIERDO monta sus propios botones de Mes (toggleCapacidadMes('cliente', ...))");
assertTrue(!leftPanel.includes(`anio_clientePart`), "el panel IZQUIERDO NUNCA referencia la key de Año del panel derecho");
assertTrue(rightPanel.includes(`setFilter('capacidad','${capacidadPanelAnioKey("clientePart")}'`), "el panel DERECHO monta su propio <select> de Año (anio_clientePart)");
assertTrue(rightPanel.includes(`toggleCapacidadMes('clientePart',`), "el panel DERECHO monta sus propios botones de Mes (toggleCapacidadMes('clientePart', ...))");
assertTrue(!rightPanel.includes(`anio_cliente'`), "el panel DERECHO NUNCA referencia la key de Año del panel izquierdo (no confundir con 'anio_clientePart' que SÍ contiene el substring 'anio_cliente')");
assertTrue(!leftPanel.includes(`toggleCapacidadMes('clientePart',`), "el panel IZQUIERDO NUNCA monta los botones de Mes del panel derecho");

// --- Cada panel posee su PROPIO KPI "Total Horas" --------------------------
const leftKpiCount = (leftPanel.match(/activity-kpi__label">Total Horas</g) || []).length;
const rightKpiCount = (rightPanel.match(/activity-kpi__label">Total Horas</g) || []).length;
assertEqual(leftKpiCount, 1, "el panel IZQUIERDO tiene EXACTAMENTE un KPI 'Total Horas' propio");
assertEqual(rightKpiCount, 1, "el panel DERECHO tiene EXACTAMENTE un KPI 'Total Horas' propio");
// El KPI vive DENTRO de la tarjeta (antes de la tabla), nunca "desprendido"
// arriba de ambos paneles — ver B2: no debe haber ningún KPI FUERA de
// ambos `.dashboard-section` (owner: "Do not leave two detached KPIs above
// independently-filtered panels").
const beforeFirstPanel = html.slice(0, leftStart);
assertTrue(!beforeFirstPanel.includes("activity-kpi__label"), "NINGÚN KPI vive fuera de los paneles (nunca 'desprendido' arriba de ambos)");

// --- Cada panel posee sus PROPIOS botones Expandir/Contraer ---------------
assertTrue(leftPanel.includes(`expandAllTree('capacidad::Productivas::Cliente')`), "el panel IZQUIERDO tiene su PROPIO botón Expandir todo (stateKey Cliente)");
assertTrue(rightPanel.includes(`expandAllTree('capacidad::Productivas::ClientePart')`), "el panel DERECHO tiene su PROPIO botón Expandir todo (stateKey ClientePart)");
assertTrue(!leftPanel.includes("ClientePart"), "el panel IZQUIERDO NUNCA referencia el stateKey del panel derecho");

// --- Simetría estructural: ambos paneles usan la MISMA plantilla ----------
const leftSkeleton = leftPanel.replace(/Cliente(?! \+ Part)/g, "X").replace(/2019|2018/g, "Y");
const rightSkeleton = rightPanel.replace(/Cliente \+ Part/g, "X").replace(/2019|2018/g, "Y");
assertTrue(leftPanel.includes('class="dashboard-section__body"'), "el panel izquierdo usa .dashboard-section__body (padding compartido)");
assertTrue(rightPanel.includes('class="dashboard-section__body"'), "el panel derecho usa .dashboard-section__body (padding compartido)");
assertTrue(leftPanel.includes('class="perspective-label"'), "el panel izquierdo usa .perspective-label para el título (MISMO primitivo que Rentabilidad Mejor/Peor)");
assertTrue(rightPanel.includes('class="perspective-label"'), "el panel derecho usa .perspective-label para el título");

// --- countLabel de la sección envolvente = "N fila(s)" (owner feedback: no
// repetir "Cliente › Proyecto (izq.)..." — MISMO criterio que Cartera/
// Proyectos/Calendario) ------------------------------------------------
assertTrue(html.includes("2 fila(s)"), "el countLabel de la sección envolvente es 'N fila(s)' (2 Clientes en la fixture), nunca la descripción larga de la jerarquía");
assertTrue(!html.includes("(izq.)") && !html.includes("(der.)"), "NUNCA se imprime 'Cliente › Proyecto (izq.)/(der.)' como countLabel (owner feedback explícito)");

// --- FINAL VISUAL COMPOSITION corrective: colgroup explícito FLEXIBLE +
// COMPACTO (ambos paneles deben entrar en pantalla sin scroll horizontal
// Y sin un hueco vacío antes de Horas) — <col> explícito gana sobre
// CUALQUIER ancho de th en table-layout:fixed. La tabla usa el modifier
// `dashboard-table--compact` (anula min-width:900px/300px pensados para
// las matrices mensuales de ancho completo). --------------------------------
assertTrue(leftPanel.includes('<table class="dashboard-table dashboard-table--compact">'), "el panel IZQUIERDO usa el modifier --compact (anula min-width:900px)");
assertTrue(rightPanel.includes('<table class="dashboard-table dashboard-table--compact">'), "el panel DERECHO usa el modifier --compact");
const leftColgroup = leftPanel.slice(leftPanel.indexOf("<colgroup>"), leftPanel.indexOf("</colgroup>") + 11);
assertTrue(leftColgroup.includes('class="dashboard-table-col--flex"'), "la 1ra columna (label) es FLEXIBLE (--flex)");
assertEqual((leftColgroup.match(/dashboard-table-col--compact/g) || []).length, 2, "las columnas 2 y 3 (Horas/% Incidencia) son COMPACTAS (--compact)");
assertTrue(!leftPanel.includes("row-label-col--panel"), "la columna de label ya NO depende de un ancho fijo en el th — el <colgroup> manda");

// --- Toolbar de panel: Año | Mes | Expandir/Contraer como TRES hijos
// DIRECTOS del grid (owner corrective — "Año alineado al borde izquierdo,
// Mes en la misma línea de base, Expandir/Contraer alineados") ------------
assertTrue(leftPanel.includes('class="staffing-timeline-toolbar staffing-timeline-toolbar--panel"'), "el panel IZQUIERDO usa el grid de toolbar --panel");
assertTrue(rightPanel.includes('class="staffing-timeline-toolbar staffing-timeline-toolbar--panel"'), "el panel DERECHO usa el grid de toolbar --panel");
const leftToolbarStart = leftPanel.indexOf('class="staffing-timeline-toolbar staffing-timeline-toolbar--panel"');
const leftToolbarActionsIdx = leftPanel.indexOf('class="staffing-timeline-toolbar__actions"', leftToolbarStart);
const leftAnioIdx = leftPanel.indexOf("setFilter('capacidad','anio_cliente'", leftToolbarStart);
const leftMesIdx = leftPanel.indexOf("toggleCapacidadMes('cliente'", leftToolbarStart);
assertTrue(leftToolbarStart < leftAnioIdx && leftAnioIdx < leftMesIdx && leftMesIdx < leftToolbarActionsIdx, "orden DOM dentro del toolbar: Año → Mes → acciones (Expandir/Contraer)");
assertTrue(leftPanel.slice(leftToolbarActionsIdx, leftToolbarActionsIdx + 200).includes("expandAllTree"), "Expandir/Contraer viven envueltos en .staffing-timeline-toolbar__actions (1 solo hijo de grid, no 2 sueltos)");

// --- KPI → tabla: respiración vertical (owner corrective) ------------------
assertTrue(html.includes('class="activity-kpi-grid"'), "el KPI sigue usando el primitivo compartido (el margen vive en la clase, no inline en reporting.js)");

// --- Headers de Horas/% Incidencia son CLICKEABLES para ordenar -----------
assertTrue(leftPanel.includes(`onclick="toggleCapacidadProductivasSort('cliente')"`), "el panel IZQUIERDO tiene headers clickeables que ordenan SU PROPIO panel (cliente)");
assertTrue(rightPanel.includes(`onclick="toggleCapacidadProductivasSort('clientePart')"`), "el panel DERECHO tiene headers clickeables que ordenan SU PROPIO panel (clientePart)");
assertTrue(!leftPanel.includes(`toggleCapacidadProductivasSort('clientePart')`), "el panel IZQUIERDO nunca dispara el orden del panel derecho");
const leftHeaderMatches = [...leftPanel.matchAll(/dashboard-table-th--sortable/g)];
assertEqual(leftHeaderMatches.length, 2, "el panel IZQUIERDO tiene EXACTAMENTE 2 headers ordenables (Horas + % Incidencia)");

// --- Ningún elemento con overflow horizontal a nivel PÁGINA introducido ---
// (el scroll ancho, si hace falta, vive DENTRO de .matrix-scroll por panel,
// nunca un wrapper nuevo a nivel de la grilla completa).
assertTrue(!html.includes("overflow-x") || html.includes("matrix-scroll"), "cualquier overflow-x declarado vive dentro de .matrix-scroll (scroll INTERNO al panel)");
assertTrue(!/class="[^"]*capacidad-grid[^"]*"/.test(html), "no se introdujo una clase de grid nueva propia de Horas Productivas");

if (failures > 0) {
  console.log(`${failures} capacidad composition invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CAPACIDAD COMPOSITION INVARIANTS PASSED");
process.exit(0);
