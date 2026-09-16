// commercial_prospect_ratio.node.js — invocado por
// test_commercial_prospect_ratio.py.
//
// Shared Stage-0 Prospect Ratio (ver HANDOFF — owner GO tras STOP semántico):
// el ratio manual de Prospecto (`commercialState.forecastProspectRatioRaw`)
// pasa a ser compartido por DOS superficies — Prospectos (dueño conceptual
// primario) y Forecast de pipeline (`forecastViewMode === "stage"`, ya
// existente desde C6/Pipeline Forecast) — SIN tocar el modelo Odoo-
// probability de "Pipeline ponderado"/"Hs Pond." en la vista por mes, que
// sigue siendo una magnitud DISTINTA.
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures y un `document`
// mínimo — MISMA técnica que commercial_forecast.node.js. Se privilegian
// helpers puros y comportamiento observable sobre snapshots de HTML completo.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const ROOT_SENTINEL = "<!--root-intacto-->";
const hosts = Object.create(null);
let fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });
const fetchCalls = [];

const root = {
  innerHTML: ROOT_SENTINEL,
  querySelector(sel) { return hosts[sel] || null; },
};

const listeners = Object.create(null);
const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById(id) { return id === "commercial-main-content" ? root : null; },
    querySelector() { throw new Error("commercial.js nunca debe usar document.querySelector"); },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch(url) { fetchCalls.push(url); return fetchImpl(url); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialForecastParseRatio, commercialForecastCalc, commercialForecastPanelBodyHtml,
  commercialProspectsPanelBodyHtml, commercialRenderForecastPanel, commercialRenderProspectsPanel,
  commercialMaybeRerenderForecastForMode, commercialMaybeRerenderProspectsForRatio, commercialSetTipo,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
function fakeTrigger(eventKey, isActive) {
  return { dataset: { event: eventKey }, classList: { contains: c => c === "is-active" && isActive } };
}
function fakeNode() { return { innerHTML: "", textContent: "" }; }

// Fixture: MISMOS números que la reconciliación "Todos" verificada en vivo
// contra Odoo real (ver commercial_forecast.node.js y PR #82) — 17984 horas
// activas (etapas 1-8) + 20314 horas prospecto (etapa 0), dos oportunidades
// por brevedad, classification YA resuelto por el backend (fixture, no algo
// que este test derive).
const FORECAST_OPPS = [
  { opportunity: "Activa Uno", client: "Cliente A", seller: "Ana", hours: 17984, classification: "ACTIVA", stage: "4 Propuesta" },
  { opportunity: "Prospecto Uno", client: "Cliente B", seller: "Beto", hours: 20314, classification: "PROSPECTO", stage: "0 Prospecto" },
];
// `weighted_hours` del bucket es el agregado Odoo-probability que YA trae el
// backend (no se recalcula del lado del cliente salvo con Etapa/Vendedor
// filtrados) — distinto a propósito de `hours`, para que el bloque N pueda
// confirmar que la vista por mes sigue leyendo ESTE campo, ajeno al ratio.
const FORECAST = { months: [{ month: "2026-09", count: 2, hours: 38298, weighted_hours: 12000,
  revenue: 0, opportunities: FORECAST_OPPS }] };

// Fixture de Prospectos: `_build_prospects` NUNCA manda `classification` (el
// universo entero ya es etapa 0 — el backend lo filtró) — sin ese campo a
// propósito, para probar que la columna manual-pond. de Prospectos NO
// depende de él (a diferencia de Forecast de pipeline, que sí lo necesita
// para distinguir ACTIVA/PROSPECTO dentro de un universo mixto 0-8).
const PROSPECTS_ROWS = [
  { opportunity: "Prospecto Uno", client: "Cliente Uno", seller: "Ana", tags: [], licencias: "No",
    hours: 100, weighted_hours: 40, probability: 40, priority: "0", activities: [], last_response: null, days_elapsed: null },
  { opportunity: "Prospecto Dos", client: "Cliente Dos", seller: "Beto", tags: ["VIP"], licencias: "Si",
    hours: 50, weighted_hours: 5, probability: 10, priority: "1", activities: [], last_response: null, days_elapsed: null },
];
const PROSPECTS = { opportunities: PROSPECTS_ROWS, total: 2 };

function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.forecast = null;
  commercialState.forecastStatus = "idle";
  commercialState.forecastErrorState = "";
  commercialState.forecastViewMode = "mes";
  commercialState.forecastProspectRatioRaw = "";
  commercialState.forecastStageFilter = "";
  commercialState.forecastSellerFilter = "";
  commercialState.prospects = null;
  commercialState.prospectsStatus = "idle";
  commercialState.prospectsErrorState = "";
  commercialState.prospectsSellerFilter = "";
  commercialState.prospectsSearchText = "";
  Object.assign(commercialState, patch || {});
}

// --- A/B: UN solo estado de ratio, y los DOS consumidores lo leen ----------
{
  resetState();
  assertTrue(count(src, "forecastProspectRatioRaw:") === 1,
    "una sola declaración del estado en commercialState — nunca un segundo campo");
  for (const banned of ["prospectsRatio", "forecastRatio", "weightedRatio", "prospectRatio:"]) {
    assertTrue(!src.includes(banned), `sin estado paralelo: ${banned}`);
  }
  // Prospectos: su propio input, MISMO estado, marcado con SU atributo.
  resetState({ prospectsStatus: "ready", prospects: PROSPECTS, forecastProspectRatioRaw: "10" });
  const prospectsBody = commercialProspectsPanelBodyHtml();
  assertTrue(prospectsBody.includes("data-commercial-prospects-ratio"),
    "Prospectos expone el campo de ratio con su PROPIO atributo");
  assertTrue(prospectsBody.match(/data-commercial-prospects-ratio[^>]*value="10"|value="10"[^>]*data-commercial-prospects-ratio/),
    "y refleja el valor VIGENTE del estado compartido");
  // Forecast de pipeline: MISMO estado, SU atributo (distinto del de arriba).
  resetState({ forecastStatus: "ready", forecast: FORECAST, forecastViewMode: "stage", forecastProspectRatioRaw: "10" });
  const forecastBody = commercialForecastPanelBodyHtml("total");
  assertTrue(forecastBody.includes("data-commercial-forecast-ratio"),
    "Forecast de pipeline sigue con su PROPIO atributo, sin colisionar con el de Prospectos");
  assertTrue(!forecastBody.includes("data-commercial-prospects-ratio"),
    "y el cuerpo de Forecast nunca emite el atributo de Prospectos");
  assertTrue(!prospectsBody.includes("data-commercial-forecast-ratio"),
    "ni viceversa: cada panel emite SOLO el suyo");
}

// --- C/D/E: el ratio sobrevive el switch de vista dentro del MISMO tipo ----
{
  resetState({
    prospectsStatus: "ready", prospects: PROSPECTS,
    forecastStatus: "ready", forecast: FORECAST, forecastViewMode: "stage",
  });
  // Prospectos → enter 15% (simula el input del usuario: sólo el estado
  // compartido cambia, el listener real de app.js hace exactamente esto).
  commercialState.forecastProspectRatioRaw = "15";
  hosts["[data-commercial-forecast-body]"] = fakeNode();
  hosts["[data-commercial-forecast-count]"] = fakeNode();
  commercialRenderForecastPanel();
  assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes('value="15"'),
    "C: ratio tipeado en Prospectos se ve en Forecast de pipeline sin tocarlo ahí");
  // Forecast de pipeline → adjust a 25%.
  commercialState.forecastProspectRatioRaw = "25";
  hosts["[data-commercial-prospects-body]"] = fakeNode();
  hosts["[data-commercial-prospects-count]"] = fakeNode();
  commercialRenderProspectsPanel();
  assertTrue(hosts["[data-commercial-prospects-body]"].innerHTML.includes('value="25"'),
    "D: ratio ajustado en Forecast de pipeline se ve en Prospectos sin tocarlo ahí");
  // E: reabrir cada uno (trigger activo, status ready) refleja el ÚLTIMO
  // valor — el gancho `commercialMaybeRerenderProspectsForRatio`/
  // `commercialMaybeRerenderForecastForMode`, no sólo el input handler.
  commercialState.forecastProspectRatioRaw = "30";
  commercialMaybeRerenderProspectsForRatio(fakeTrigger("prospectos", true));
  assertTrue(hosts["[data-commercial-prospects-body]"].innerHTML.includes('value="30"'),
    "E: reabrir Prospectos (sin tocar su propio input) ya muestra el ratio más reciente");
  commercialMaybeRerenderForecastForMode(fakeTrigger("forecast", true));
  assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes('value="30"'),
    "E: reabrir Forecast de pipeline también");
  delete hosts["[data-commercial-forecast-body]"];
  delete hosts["[data-commercial-forecast-count]"];
  delete hosts["[data-commercial-prospects-body]"];
  delete hosts["[data-commercial-prospects-count]"];
}

// --- F/G/H/I: aritmética de Forecast de pipeline (stage 0 vs 1-8) ---------
// La aritmética PURA (`commercialForecastCalc`/`commercialForecastRowForecastHours`)
// ya está cubierta exhaustivamente en commercial_forecast.node.js — acá sólo
// se confirma que sigue siendo la MISMA fuente detrás del KPI redesign, sin
// una segunda implementación.
{
  const calc0 = commercialForecastCalc(FORECAST_OPPS, commercialForecastParseRatio("0"));
  assertTrue(calc0.forecastHours === 17984, "H: 0% -> Forecast total = Horas activas");
  const calc100 = commercialForecastCalc(FORECAST_OPPS, commercialForecastParseRatio("100"));
  assertTrue(calc100.forecastHours === 17984 + 20314, "I: 100% -> Forecast total = Activas + Prospecto");
  const calc10 = commercialForecastCalc(FORECAST_OPPS, commercialForecastParseRatio("10"));
  assertTrue(Math.abs(calc10.prospectWeighted - 2031.4) < 0.05, "F: prospecto ponderado = horas etapa 0 × ratio");
  assertTrue(calc10.activeHours === 17984, "G: etapas 1-8 quedan crudas, el ratio nunca las toca");
}

// --- §8: KPI row rediseñado — CUATRO tiles, sin "Ratio aplicado" ----------
{
  resetState({ forecastStatus: "ready", forecast: FORECAST, forecastViewMode: "stage", forecastProspectRatioRaw: "10" });
  const body = commercialForecastPanelBodyHtml("total");
  for (const label of ["Horas activas", "Horas prospecto", "Prospecto ponderado", "Forecast total"]) {
    assertTrue(body.includes(`>${label}<`), `KPI presente: ${label}`);
  }
  assertTrue(!body.includes(">Ratio aplicado<"),
    "el tile 'Ratio aplicado' se retiró: el <input> ya es el control editable");
  assertTrue(!body.includes(">Horas prospecto ajustadas<") && !body.includes(">Forecast de horas<"),
    "las etiquetas viejas no conviven con las nuevas");
  assertTrue(count(body, "activity-kpi-grid") >= 1 && count(body, 'class="activity-kpi"') === 4,
    `exactamente CUATRO KPIs primarios (got ${count(body, 'class="activity-kpi"')})`);
  assertTrue(/10%\s*de etapa 0/.test(body), "Prospecto ponderado expone el ratio vigente en su subtítulo");
  assertTrue(body.includes("Activas + prospecto ponderado"), "Forecast total explica su fórmula en el subtítulo");
  assertTrue(body.includes("Etapas 1–8") && body.includes(">Etapa 0<"),
    "Horas activas/Horas prospecto declaran su universo en el subtítulo");

  // Ratio en blanco: el subtítulo de Prospecto ponderado no inventa un %.
  resetState({ forecastStatus: "ready", forecast: FORECAST, forecastViewMode: "stage" });
  const blankBody = commercialForecastPanelBodyHtml("total");
  assertTrue(!/%\s*de etapa 0/.test(blankBody), "sin ratio cargado, ningún % aparece en el subtítulo");
  assertTrue(blankBody.includes(">—<"), "y los valores derivados muestran '—', nunca 0 encubierto");
}

// --- K/L/M: Prospectos — columna manual NUEVA vs. columna Odoo EXISTENTE --
// Extracción POSICIONAL de celdas (MISMO criterio que `totalCells` en
// commercial_forecast.node.js): el orden de columnas es Oportunidad(0)/
// Cliente(1)/Vendedor(2)/Etiquetas(3)/Lic.(4)/Hs(5)/Hs prob. pond.(6)/
// Hs prosp. pond.(7, NUEVA)/Prob.(8)/... — así un `10` que apareciera por
// casualidad en OTRA columna nunca puede pasar por el valor esperado.
// Extrae la CORRIDA de texto visible al inicio de cada celda (hasta el
// primer tag anidado o el cierre de celda) — a diferencia de un regex
// `<td>...</td>` ingenuo, esto NO pierde una celda entera cuando contiene
// markup anidado (Etiquetas con `<span>`, Prioridad con su tooltip
// `<div>`), que de otro modo correría los índices de las columnas
// siguientes silenciosamente.
function rowCells(rowHtml) {
  return rowHtml.split("<td").slice(1).map(seg => {
    const afterOpenTag = seg.slice(seg.indexOf(">") + 1);
    const cut = afterOpenTag.search(/<|$/);
    return afterOpenTag.slice(0, cut === -1 ? undefined : cut);
  });
}
function dataRows(html) {
  const body = html.split("<tbody>")[1].split("</tbody>")[0];
  return [...body.matchAll(/<tr>[\s\S]*?<\/tr>/g)].map(m => m[0])
    .filter(r => !r.includes('class="is-total"'))
    .map(rowCells);
}
{
  resetState({ prospectsStatus: "ready", prospects: PROSPECTS, forecastProspectRatioRaw: "10" });
  const body = commercialProspectsPanelBodyHtml();
  assertTrue(body.includes(">Hs prob. pond.<"),
    "la columna Odoo-probability existente se renombra para desambiguar (L)");
  assertTrue(body.includes(">Hs prosp. pond.<"),
    "la columna manual NUEVA usa un rótulo distinto (K)");
  assertTrue(!body.includes(">Hs Pond.<"),
    "el rótulo viejo y ambiguo ya no aparece en ningún lado");

  const rows = dataRows(body);
  assertTrue(rows.length === 2, `dos filas de datos (got ${rows.length})`);
  // K: fila 1 = 100hs × 10% = 10; fila 2 = 50hs × 10% = 5 — columna 7.
  assertTrue(rows[0][7] === "10" && rows[1][7] === "5",
    `K: Hs prosp. pond. = horas cruda × ratio manual (got ${rows[0][7]}, ${rows[1][7]})`);
  // L: columna 6 (Hs prob. pond.) es el `weighted_hours` del payload TAL
  // CUAL (40 y 5) — no recalculado, no tocado por el ratio manual.
  assertTrue(rows[0][6] === "40" && rows[1][6] === "5",
    `L: Hs prob. pond. sigue siendo weighted_hours del payload (got ${rows[0][6]}, ${rows[1][6]})`);
  // M: Prob. (columna 8) es la probabilidad Odoo cruda, ajena al ratio.
  assertTrue(rows[0][8] === "40%" && rows[1][8] === "10%",
    `M: Prob. es la probabilidad Odoo cruda (got ${rows[0][8]}, ${rows[1][8]})`);

  // M — independencia dura: cambiar el ratio manual NO muta el payload de
  // Prospectos (identidad de referencia, nunca una copia derivada).
  const before = JSON.parse(JSON.stringify(commercialState.prospects));
  commercialState.forecastProspectRatioRaw = "90";
  const rows90 = dataRows(commercialProspectsPanelBodyHtml());
  assertTrue(JSON.stringify(commercialState.prospects) === JSON.stringify(before),
    "M: cambiar el ratio manual no muta el payload de Prospectos (Odoo probability intacto)");
  assertTrue(rows90[0][6] === "40" && rows90[0][8] === "40%",
    "M: Hs prob. pond./Prob. no se mueven un milímetro aunque el ratio manual cambie a 90%");
  assertTrue(rows90[0][7] === "90", "y sólo la columna manual (7) responde al ratio nuevo (100×0.90=90)");

  // J: ratio en blanco -> "—" en la columna nueva, nunca 0 encubierto.
  resetState({ prospectsStatus: "ready", prospects: PROSPECTS });
  const blankRows = dataRows(commercialProspectsPanelBodyHtml());
  assertTrue(blankRows[0][7] === "—" && blankRows[1][7] === "—",
    "J: sin ratio, la columna manual muestra '—' en cada fila, nunca 0");

  // 0% explícito: valor real y distinto de blanco.
  resetState({ prospectsStatus: "ready", prospects: PROSPECTS, forecastProspectRatioRaw: "0" });
  const zeroRows = dataRows(commercialProspectsPanelBodyHtml());
  assertTrue(zeroRows[0][7] === "0" && zeroRows[1][7] === "0",
    "0% explícito produce 0 real en la columna manual, nunca '—' (0 es un ratio válido)");
}

// --- N: Pipeline ponderado (vista por mes) no gana semántica de ratio -----
{
  resetState({ forecastStatus: "ready", forecast: FORECAST });
  const pond = commercialForecastPanelBodyHtml("ponderado");
  assertTrue(!pond.includes("data-commercial-forecast-ratio"),
    "N: la vista por mes (Pipeline ponderado) NO expone el ratio manual — sigue siendo 100% Odoo-probability");
  assertTrue(!/Prospecto ponderado|Forecast total/.test(pond),
    "y no hereda ningún rótulo del modelo de ratio manual");
  assertTrue(pond.includes("Hs Pond."),
    "el título de columna de la vista por mes sigue diciendo 'Hs Pond.' (Odoo), sin renombrar");
}

// --- S: cambiar Todos/NN/BI sigue reseteando el ratio manual (owner GO,
// revocando el requerimiento anterior) ---------------------------------
{
  resetState({
    forecastStatus: "ready", forecast: FORECAST, forecastProspectRatioRaw: "42", forecastViewMode: "stage",
    prospectsStatus: "ready", prospects: PROSPECTS,
  });
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });
  commercialSetTipo("nn");
  assertTrue(commercialState.forecastProspectRatioRaw === "",
    "S: cambiar el tipo resetea el ratio manual — comportamiento existente, ahora explícitamente requerido");
  assertTrue(commercialState.forecastViewMode === "mes",
    "y también la vista, MISMO criterio que Etapa/Vendedor");
  assertTrue(commercialState.prospectsStatus === "idle" && commercialState.prospects === null,
    "Prospectos se invalida junto con el resto — universo nuevo, se vuelve a pedir en la próxima apertura");
}

// --- Fuente: cero duplicación de aritmética/markup entre Prospectos y
// Forecast de pipeline ------------------------------------------------
{
  assertTrue((src.match(/function commercialProspectRatioFieldHtml\(/g) || []).length === 1,
    "un solo generador de markup del campo de ratio, parametrizado por atributo");
  assertTrue((src.match(/function commercialForecastRowForecastHours\(/g) || []).length === 1,
    "la aritmética de fila sigue viviendo en un solo lugar");
  assertTrue((src.match(/hours \|\| 0\) \* ratioFraction/g) || []).length >= 1,
    "Prospectos reusa la MISMA forma de cálculo (hours × ratioFraction), no una copia divergente");
}

finish();
function finish() {
  if (failures) {
    console.log(`\n${failures} invariante(s) fallaron`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL PROSPECT RATIO INVARIANTS PASSED");
}
