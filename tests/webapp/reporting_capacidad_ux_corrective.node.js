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

// DOM mínimo: `resetFilters`/`toggleCapacidadMes`/`setFilter` disparan
// `render()` real (MISMO criterio que reporting_initial_load_contract.node.js)
// — se necesita un stub de `getElementById`/`initDashboardSections`, aunque
// este harness solo inspecciona estado/HTML devuelto por las funciones
// puras, nunca el DOM mutado por `render()`.
function makeEl() { return { innerHTML: "" }; }
const sandbox = {
  document: {
    addEventListener() {},
    getElementById() { return makeEl(); },
    querySelector() { return null; },
  },
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
  initDashboardSections() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  capacidadProductivasSelectedRows, capacidadPanelAnioKey, renderCapacidadProductivas,
  capacidadProductivasAnioOptions, capacidadProductivasDefaultAnio, renderCapacidadFilterBar,
  resetFilters, toggleCapacidadMes, setFilter, toggleCapacidadStatus, fmtNum,
} = sandbox;
// Extractor de cuerpo de función por balance de llaves (MISMO criterio que
// `_fn_body_exact` en test_reporting_batch_b.py) — para pruebas de
// source-seam (§11 REGRESSION: "no new Time Logs fetch on Año/Mes changes")
// sin depender de que loadBatchBData realmente dispare un fetch de red en
// este sandbox mínimo.
function fnBody(name) {
  const sig = `function ${name}(`;
  const start = src.indexOf(sig);
  if (start === -1) throw new Error(`función no encontrada: ${name}`);
  const braceStart = src.indexOf("{", start);
  let depth = 0, i = braceStart;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") { depth--; if (depth === 0) break; }
  }
  return src.slice(braceStart, i + 1);
}
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);
// Denominador REAL de "% Incidencia" (SOURCE-PARITY corrective) — objeto
// PLANO {anio: [horas x 12]}, mutado in-place (nunca reasignado — `let` en
// reporting.js, pero esta referencia sigue siendo válida mientras solo se
// le agreguen/borren keys, nunca `= {}` desde afuera del sandbox).
const CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR = vm.runInContext("CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR", sandbox);
function setDenominador(byYear) {
  Object.keys(CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR).forEach(k => delete CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR[k]);
  Object.assign(CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR, byYear);
}

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
  // YEAR ORDERING corrective (owner decision 2026-08-19): selectores de
  // Año DESCENDENTE — orden de presentación, no afecta membership/facet.
  assertEqual(capacidadProductivasAnioOptions(), ["2019", "2018"], "tildar Finalizado también trae 2018 de vuelta a las opciones");
}

// --- UNRESOLVED-ESTADO corrective (owner report — "Horas Productivas solo
// ofrece el año actual aunque el CSV tiene años anteriores"): una fila cuyo
// Proyecto no matcheó contra la lista Proyectos (típico de proyectos
// HISTÓRICOS renombrados/archivados) llega con `estado: ""` desde
// `_shape_horas_productivas` — el PBIP no tiene dimensión de Estado en
// absoluto, así que "ambos tildados" debe incluirla igual que cualquier
// otra fila, nunca excluirla silenciosamente.
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2026": [1,1,1,1,1,1,1,1,1,1,1,1] } },
    { cliente: "Aluar", proyecto: "Legacy sin join", horas: 0, porcentajePart: 3, estado: "",
      horasByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = {};

  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  // YEAR ORDERING corrective (owner decision 2026-08-19): DESCENDENTE.
  assertEqual(capacidadProductivasAnioOptions(), ["2026", "2019"], "ambos Estado tildados == 'sin filtro' del PBIP: 2019 (estado sin resolver) aparece igual que 2026");
  assertEqual(capacidadProductivasSelectedRows("cliente").map(r => r.cliente).sort(), ["Aluar", "YPF"], "la fila con estado sin resolver entra a la población cuando Estado no angosta de verdad");

  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  assertEqual(capacidadProductivasAnioOptions(), ["2026"], "angostado a SOLO 'En curso', la fila sin estado resuelto se excluye (no sabemos a cuál de los dos pertenece) — MISMO criterio que una fila 'Finalizado' real");
  assertEqual(capacidadProductivasSelectedRows("cliente").map(r => r.cliente), ["YPF"], "misma exclusión en la población real, no solo en las opciones de Año");

  state.capacidadStatus = { "En curso": true, "Finalizado": true };
}


// --- SOURCE-PARITY corrective: el DENOMINADOR real de "% Incidencia" ya
// --- NO se deriva de las filas mostradas (CAPACIDAD_PRODUCTIVAS_ROWS,
// --- Cliente-placeholder-interno ya excluido, Estado ya filtrado) sino de
// --- CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR (grand total real del
// --- backend — evidencia PRIMARIA: LEFT PBI ≈50.5 vs KPI visible 42.182
// --- para el MISMO Cliente/Año). Prueba EXACTAMENTE lo que pide el
// --- ticket: "LEFT visible Total Horas != LEFT incidence denominator" —
// --- acá el denominador (240) es DISTINTO tanto de la suma de filas
// --- mostradas (180) como del Total Horas del panel tras filtrar
// --- Proyecto (120). Proyecto SIGUE siendo REMOVEFILTERS de las filas
// --- MOSTRADAS (narrows 180→120, mismo criterio que Cliente) — pero el
// --- denominador ya ni siquiera "ve" las filas, es un grand total
// --- independiente. Por defecto el árbol arranca colapsado
// --- (treeExpandedSet vacío), así que se verifica en el rollup del nodo
// --- Cliente, no en una fila hija todavía no expandida.
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10] } },
    { cliente: "YPF", proyecto: "Costeo", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5] } },
  );
  // Denominador REAL (240) — MAYOR que la suma de las filas mostradas
  // (180): simula el cliente placeholder interno de "No Asignadas" que
  // REMOVEFILTERS(Cliente[Cliente]) reintroduce en el DAX real, nunca
  // visible en las filas mostradas ni en ningún KPI de panel.
  setDenominador({ "2019": [20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20] });
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
  assertTrue(ypfRow.includes(">120<"), "Proyecto='SADI v2': el rollup MOSTRADO de YPF sigue siendo 120 (numerador/filas visibles sin cambios — REMOVEFILTERS de Proyecto en las filas, no en el denominador)");
  assertTrue(ypfRow.includes("50.00%"), "% Incidencia = 120/240 = 50.00% (owner request: 2 decimales) — usa el DENOMINADOR REAL (240), NUNCA la suma de filas mostradas (180, daría 66.67%) ni el Total Horas del panel filtrado (120, daría 100.00%)");
  setDenominador({});
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

// --- SOURCE-PARITY §11 YEAR DOMAIN: los años históricos sobreviven, el Año
// --- seleccionado de un panel NUNCA elimina opciones del otro (ni las
// --- propias), y las opciones se derivan de las filas hidratadas CRUDAS
// --- excluyendo el propio facet de Año — evidencia PRIMARIA:
// --- `_CAPACIDAD_SELECT_FIELDS` (capacidadProductivasAnioOptions) solo
// --- lista cliente/proyecto, nunca anio_cliente/anio_clientePart, así que
// --- `facetRowsExcept` estructuralmente NUNCA filtra por Año — la lista de
// --- opciones es la MISMA sin importar qué año esté elegido en NINGÚN
// --- panel. Esto también prueba que el bug real (colapso a solo 2026) no
// --- era un defecto de este motor de facets sino el default viejo de
// --- Estado (ver bloque "Estado ... escopea qué Años aparecen").
{
  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1], "2021": [1,1,1,1,1,1,1,1,1,1,1,1] } },
    { cliente: "Aluar", proyecto: "Costeo", horas: 0, porcentajePart: 5, estado: "En curso",
      horasByYear: { "2023": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = {};
  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  const baseline = capacidadProductivasAnioOptions();
  // YEAR ORDERING corrective (owner decision 2026-08-19): DESCENDENTE.
  assertEqual(baseline, ["2023", "2021", "2019"], "los 3 años históricos hidratados aparecen como opción, ninguno perdido");

  // Elegir un Año en el panel LEFT (cliente) no debe quitar opciones —
  // ni siquiera las que ese propio panel ya no puede mostrar.
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  assertEqual(capacidadProductivasAnioOptions(), baseline, "seleccionar Año=2019 en el panel LEFT no elimina 2021/2023 de las opciones (propio facet excluido)");

  // Elegir un Año DISTINTO en el panel RIGHT (clientePart) — independiente
  // del LEFT — tampoco debe alterar las opciones ni pisar la selección LEFT.
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2023";
  assertEqual(capacidadProductivasAnioOptions(), baseline, "seleccionar Año=2023 en el panel RIGHT no elimina años de las opciones, ni afecta al LEFT");
  assertEqual(state.filters.capacidad[capacidadPanelAnioKey("cliente")], "2019", "la selección de Año del panel LEFT sigue intacta tras elegir un Año distinto en el RIGHT");
}

// --- SOURCE-PARITY §8/§9/§11 ESTADO: default = AMBOS tildados (el PBIP
// --- original no tiene filtro de Estado — extensión Hub neutral, ver
// --- comentario en `state` de reporting.js), "Limpiar" restaura AMBOS, y
// --- angostar a un solo estado sigue funcionando en cada sentido ----------
{
  assertTrue(/capacidadStatus:\s*\{\s*"En curso":\s*true,\s*"Finalizado":\s*true\s*\}/.test(src), "el literal `state.capacidadStatus` por default tilda AMBOS 'En curso' y 'Finalizado' (source-parity con el PBIP, que no filtra Estado)");

  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1] } },
    { cliente: "Molinos", proyecto: "Legacy", horas: 0, porcentajePart: 3, estado: "Finalizado",
      horasByYear: { "2019": [1,1,1,1,1,1,1,1,1,1,1,1] } },
  );
  state.filters.capacidad = { cliente: "" };
  state.capacidadMesesSeleccionados = { cliente: new Set(["1"]), clientePart: new Set(["2"]) };
  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  resetFilters("capacidad");
  assertEqual(state.capacidadStatus, { "En curso": true, "Finalizado": true }, "'Limpiar' en Horas Productivas restaura AMBOS estados tildados (baseline neutral del PBIP), nunca 'solo En curso' (criterio de Cartera/Rentabilidad, NO aplica acá)");

  state.capacidadStatus = { "En curso": true, "Finalizado": false };
  assertEqual(capacidadProductivasSelectedRows("cliente").map(r => r.cliente), ["YPF"], "tildar SOLO En curso angosta correctamente (excluye Molinos)");
  state.capacidadStatus = { "En curso": false, "Finalizado": true };
  assertEqual(capacidadProductivasSelectedRows("cliente").map(r => r.cliente), ["Molinos"], "tildar SOLO Finalizado angosta correctamente en el otro sentido (excluye YPF)");
}

// --- SOURCE-PARITY §11 REGRESSION: cambiar Año/Mes de Horas Productivas es
// --- PURO estado de UI sobre datos ya hidratados — NUNCA dispara un nuevo
// --- fetch de Time Logs (source-seam: los cuerpos de `toggleCapacidadMes`
// --- y `setFilter` no invocan `loadBatchBData`) ----------------------------
{
  assertTrue(!fnBody("toggleCapacidadMes").includes("loadBatchBData"), "toggleCapacidadMes (togglear un botón de Mes) nunca invoca loadBatchBData — puro re-render sobre datos ya hidratados");
  assertTrue(!fnBody("setFilter").includes("loadBatchBData"), "setFilter (cambiar Año vía Cliente/Proyecto/Estado o el <select> de Año) nunca invoca loadBatchBData — mismo criterio, ningún facet de Reporting re-fetchea al cambiar filtros locales");
}

// --- Precisión (owner corrective — Horas Productivas SIN decimales en
// --- Horas, tiles Y tablas; % Incidencia sigue en 2 decimales — nunca el
// --- default de 1 decimal del resto del Hub) ------------------------------
{
  assertEqual(fmtNum(1234.4219, "hours0"), "1,234", "fmtNum('hours0') redondea a 0 decimales");
  assertEqual(fmtNum(5.4, "hours"), "5.4", "fmtNum('hours') (default, resto del Hub) sigue en 1 decimal — sin regresión");

  CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
  CAPACIDAD_PRODUCTIVAS_ROWS.push(
    { cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
      horasByYear: { "2019": [1234.4219, 0,0,0,0,0,0,0,0,0,0,0] } },
  );
  setDenominador({ "2019": [1234.4219, 0,0,0,0,0,0,0,0,0,0,0] });
  state.filters.capacidad = { cliente: "" };
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
  state.capacidadProductivasSortDir = { cliente: "desc", clientePart: "desc" };
  state.capacidadStatus = { "En curso": true, "Finalizado": true };
  state.perspective.capacidad = "Productivas";
  state.batchBDataStatus = "ready";
  const html = renderCapacidadProductivas();
  assertTrue(html.includes(">1,234<"), "fila de la tabla: Horas SIN decimales (1,234 - no 1,234.4 ni 1,234.422)");
  assertTrue(html.includes("100.00%"), "% Incidencia con 2 decimales (100.00%, no 100.0%)");
  assertTrue((html.match(/1,234</g) || []).length >= 2, "el tile 'Total Horas' TAMBIÉN usa 0 decimales (mismo valor 1,234 aparece en el tile Y en la fila)");
  setDenominador({});
}

if (failures > 0) {
  console.log(`${failures} capacidad UX corrective invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CAPACIDAD UX CORRECTIVE INVARIANTS PASSED");
process.exit(0);
