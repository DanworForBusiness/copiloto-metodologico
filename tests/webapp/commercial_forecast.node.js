// commercial_forecast.node.js — invocado por test_commercial_forecast.py.
// C6 (ver HANDOFF — "Pipeline Total / Ponderado → Forecast"): el análisis
// "Forecast — Pipeline por Mes de Cierre", accionado por los DOS tiles de
// Pipeline, cargado LAZY y renderizado quirúrgicamente.
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures y un `document`
// mínimo — MISMA técnica que el resto de los harnesses de Comercial. Se
// privilegian helpers puros y comportamiento observable sobre snapshots de
// HTML completo.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

// `document` configurable. `hosts` mapea selector → nodo falso, y el `root`
// falso lleva un `innerHTML` SENTINELA: si algún camino escalara a un render
// completo (`commercialRender`, que hace `root.innerHTML = ...`), el
// centinela desaparecería. Es la prueba de que el render del panel es
// quirúrgico, no una afirmación sobre qué API se llamó.
const ROOT_SENTINEL = "<!--root-intacto-->";
const hosts = Object.create(null);
const domCalls = { querySelector: [] };
const fetchCalls = [];
let fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });

const root = {
  innerHTML: ROOT_SENTINEL,
  querySelector(sel) { domCalls.querySelector.push(sel); return hosts[sel] || null; },
};

// Los listeners que registra commercial.js se GUARDAN para poder ejercitar
// los delegados REALES en el bloque de interacción del final (marcador visual
// único + X). `querySelectorAll` devuelve [] para que el motor compartido sea
// un no-op cuando se lo invoca sobre este `document` mínimo — en ese bloque se
// lo llama directo sobre un grupo falso de verdad.
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

// MOTOR COMPARTIDO REAL: se extrae `initEventBreakdown` de app.js — su texto
// tal cual, no una réplica — y se carga sobre el mismo contexto, pisando el
// stub de arriba. Sin esto el bloque de interacción del final probaría una
// imitación del motor, que es justo lo que NO sirve: el corrective existe
// porque el motor marca `.is-active` por VALOR del atributo, y eso hay que
// verlo pasar de verdad. `app.js` NO se modifica: sólo se lee.
const APP_JS = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");
const ENGINE_START = "function initEventBreakdown(";
const ENGINE_END = 'document.body.addEventListener("htmx:load", () => initEventBreakdown(document));';
const engineFrom = APP_JS.indexOf(ENGINE_START);
const engineTo = APP_JS.indexOf(ENGINE_END, engineFrom);
if (engineFrom < 0 || engineTo < 0) {
  console.log("FAIL: no se pudo extraer initEventBreakdown de app.js (¿cambió su forma?)");
  process.exit(1);
}
vm.runInContext(APP_JS.slice(engineFrom, engineTo), sandbox, { filename: "app.js:initEventBreakdown" });

const {
  commercialForecastPeriodLabel, commercialForecastTooltipLines, commercialForecastChartHtml,
  commercialForecastPanelBodyHtml, commercialForecastCountLabel, commercialTooltipShift,
  commercialRenderForecastPanel, commercialMaybeLoadForecast, commercialSetTipo,
  commercialForecastFilterOptions, commercialForecastMode, commercialForecastPeriodLongLabel,
  commercialForecastPreambleHtml,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;

// Fixture: el payload REAL de `_build_forecast` — meses ascendentes y el
// bucket "Sin fecha" SIEMPRE último (es el backend el que lo ordena así).
const MONTHS = [
  { month: "2026-07", count: 3, hours: 120, weighted_hours: 42.5, revenue: 90000 },
  { month: "2026-08", count: 12, hours: 430.4, weighted_hours: 118.7, revenue: 1234567.5 },
  { month: "2026-09", count: 5, hours: 260, weighted_hours: 91, revenue: 310000 },
  { month: "Sin fecha", count: 2, hours: 64, weighted_hours: 12.8, revenue: 15000 },
];
const FORECAST = { months: MONTHS };

function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.forecast = null;
  commercialState.forecastStatus = "idle";
  commercialState.forecastErrorState = "";
  Object.assign(commercialState, patch || {});
}
function fakeTrigger(eventKey, isActive) {
  return { dataset: { event: eventKey }, classList: { contains: c => c === "is-active" && isActive } };
}
function fakeNode() {
  return { innerHTML: "", textContent: "" };
}

// --- Etiqueta de período ----------------------------------------------------
// "Sin fecha" es un bucket VÁLIDO del backend: se muestra tal cual, nunca se
// trata como error ni se lo reescribe. Tabla fija en vez de
// `toLocaleDateString`: idéntico en el navegador y acá, sin depender del ICU.
{
  assertTrue(commercialForecastPeriodLabel("2026-08") === "ago 26", "YYYY-MM → mes corto + año de dos dígitos");
  // Forma LARGA (owner smoke: la convención del original). Las dos existen a
  // propósito: el original también usa las dos — corta en el eje del gráfico,
  // completa en el detalle (dashboard.js:2272 vs :2450).
  assertTrue(commercialForecastPeriodLongLabel("2026-08") === "Agosto de 2026", "YYYY-MM → mes completo + año");
  assertTrue(commercialForecastPeriodLongLabel("2027-01") === "Enero de 2027", "enero no se cae por el índice 0");
  assertTrue(commercialForecastPeriodLongLabel("2025-12") === "Diciembre de 2025", "diciembre es el último índice válido");
  assertTrue(commercialForecastPeriodLongLabel("Sin fecha") === "Sin fecha", "el bucket sin fecha se muestra literal");
  assertTrue(commercialForecastPeriodLongLabel("2026-13") === "2026-13", "mes fuera de rango cae al valor crudo");
  assertTrue(commercialForecastPeriodLongLabel(null) === "", "null no produce basura");
  assertTrue(commercialForecastPeriodLabel("2025-01") === "ene 25", "enero no se cae por el índice 0");
  assertTrue(commercialForecastPeriodLabel("2025-12") === "dic 25", "diciembre es el último índice válido");
  assertTrue(commercialForecastPeriodLabel("Sin fecha") === "Sin fecha", "el bucket sin fecha se muestra literal");
  assertTrue(commercialForecastPeriodLabel("2026-13") === "2026-13", "mes fuera de rango cae al valor crudo, nunca a undefined");
  assertTrue(commercialForecastPeriodLabel("") === "", "vacío no produce basura");
  assertTrue(commercialForecastPeriodLabel(null) === "", "null tampoco");
}

// --- Tooltip: las CUATRO magnitudes + el período ----------------------------
{
  const lines = commercialForecastTooltipLines(MONTHS[1]);
  assertTrue(lines.length === 5, "cinco líneas: período + las cuatro magnitudes");
  assertTrue(lines[0] === "Agosto de 2026",
    "la primera línea es el período con el mes COMPLETO — la convención que el original usa en el detalle");
  assertTrue(lines.some(l => /^Oportunidades: 12$/.test(l)), "el conteo está en el tooltip (desempata horas iguales)");
  assertTrue(lines.some(l => /^Horas: 430$/.test(l)), "horas");
  assertTrue(lines.some(l => /^Ponderadas: 119 hs$/.test(l)), "ponderadas, con la unidad explícita");
  assertTrue(lines.some(l => /^Revenue: /.test(l)), "Revenue existe SÓLO acá — nunca como barra (es otra unidad)");
}

// --- Clamp horizontal del tooltip (decisión pura, sin DOM) ------------------
// El scroll horizontal recorta a izquierda y agranda el área scrolleable a
// derecha: un tooltip de 200px centrado sobre una columna de ~52px quedaba
// ilegible en el primer y el último período.
{
  // Firma: (left, right, boundaryLeft, boundaryRight, margin). Recibe los
  // BORDES REALES del tooltip en reposo — no el centro del ancla y su ancho —
  // así funciona igual esté centrado o anclado al borde por CSS.
  assertTrue(commercialTooltipShift(400, 600, 0, 1000, 7) === 0, "si ya entra, no se mueve");
  assertTrue(commercialTooltipShift(-50, 150, 0, 1000, 7) === 57, "se sale por izquierda → corrimiento positivo exacto");
  assertTrue(commercialTooltipShift(880, 1080, 0, 1000, 7) === -87, "se sale por derecha → corrimiento negativo exacto");
  assertTrue(commercialTooltipShift(7, 207, 0, 1000, 7) === 0, "justo en el límite todavía no se mueve");
  // Boundary desplazado (el scroll box no arranca en 0 en la página real).
  assertTrue(commercialTooltipShift(240, 440, 300, 900, 7) === 67, "el clamp respeta el borde REAL del boundary, no el viewport");
  // Un tooltip anclado al borde derecho de su columna (el último período) ya
  // entra sin corrimiento: eso es lo que evita el scroll fantasma.
  assertTrue(commercialTooltipShift(800, 1000, 0, 1000, 0) === 0, "anclado al borde exacto, no se mueve");
}

// --- Chart: DOS series, UN eje, cero switch ---------------------------------
{
  // UNA sola serie por vez, la del tile de entrada (owner smoke): el gráfico
  // y la tabla hablan de la MISMA lectura. El tooltip sigue trayendo las
  // cuatro magnitudes, así que no se pierde nada.
  const html = commercialForecastChartHtml(MONTHS, "total");
  const pondHtml = commercialForecastChartHtml(MONTHS, "ponderado");
  assertTrue(count(html, "category-plot__bar--hours") === 4, "modo total: una barra de Horas por período");
  assertTrue(count(html, "category-plot__bar--weighted") === 0, "y NINGUNA de Ponderadas");
  assertTrue(count(pondHtml, "category-plot__bar--weighted") === 4, "modo ponderado: una barra de Ponderadas por período");
  assertTrue(count(pondHtml, "category-plot__bar--hours") === 0, "y NINGUNA de Horas");
  assertTrue(count(html, "category-plot__bar--count") === 0, "NINGUNA barra de Oportunidades — el conteo vive en el tooltip");
  assertTrue(count(html, "category-plot__col") === 4, "exactamente una columna por período, sin columnas fantasma");
  assertTrue(count(html, "category-plot__axis--left") === 1, "UN solo eje");
  assertTrue(!html.includes("category-plot__axis--right"), "sin la canaleta derecha: las dos series son horas y comparten escala");
  // La leyenda y el calificador de universo YA NO viven en el gráfico: son del
  // PANEL (owner smoke — vivir en la columna izquierda empujaba el área de
  // plot 44px y dejaba la tabla "más arriba"). Se testean sobre su propio
  // helper y sobre el cuerpo completo del panel.
  assertTrue(!html.includes("swatch-bar") && !html.includes("forecast-scope"),
    "el gráfico ya no emite ni leyenda ni calificador: son del panel");
  const preTotal = commercialForecastPreambleHtml("total");
  const prePond = commercialForecastPreambleHtml("ponderado");
  assertTrue(count(preTotal, "swatch-bar--hours") === 1 && count(preTotal, "swatch-bar--weighted") === 0,
    "el preámbulo expresa exactamente la serie visible, ni una más");
  assertTrue(count(prePond, "swatch-bar--weighted") === 1 && count(prePond, "swatch-bar--hours") === 0,
    "y en el otro modo, la otra");
  assertTrue(preTotal.includes(">Horas</b>") && prePond.includes(">Hs Ponderadas</b>"),
    "con el rótulo de cada lectura");
  assertTrue(!preTotal.includes("swatch-bar--count"), "y ninguna tercera");
  // Sin el switch legacy Horas|Ponderadas (retirado del Funnel en C5a).
  assertTrue(!/dashboard-view-switch/.test(html), "sin switch de vista");
  assertTrue(!/Ver Ponderado|Ver Normal|forecast-mode|forecastMode/i.test(html), "sin toggle de serie de ningún tipo");
}

// --- Orden del backend intacto: "Sin fecha" queda ÚLTIMO -------------------
{
  const html = commercialForecastChartHtml(MONTHS);
  const labels = [...html.matchAll(/<span>([^<]+)<\/span>/g)].map(m => m[1]);
  assertTrue(labels.join("|") === "jul 26|ago 26|sep 26|Sin fecha",
    "el cliente NO reordena: mismo orden que entregó el backend");
  assertTrue(labels[labels.length - 1] === "Sin fecha", "y el bucket sin fecha nunca se cuela entre meses");
  // Un payload deliberadamente "desordenado" alfabéticamente debe salir tal
  // cual: la prueba de que no hay ningún sort escondido.
  const scrambled = [{ month: "Sin fecha", count: 1, hours: 5, weighted_hours: 1, revenue: 0 },
                     { month: "2026-01", count: 1, hours: 5, weighted_hours: 1, revenue: 0 }];
  const raw = [...commercialForecastChartHtml(scrambled).matchAll(/<span>([^<]+)<\/span>/g)].map(m => m[1]);
  assertTrue(raw.join("|") === "Sin fecha|ene 26", "orden recibido = orden dibujado, siempre");
}

// --- Escala: techo común a las dos series, sin NaN -------------------------
{
  const html = commercialForecastChartHtml(MONTHS);
  const ticks = [...html.matchAll(/category-plot__axis--left">(.*?)<\/div>/gs)][0][1];
  // El techo es el de la serie VISIBLE (owner smoke): en modo total el máximo
  // real es 430.4 → 450; en ponderado es 118.7 → 150, y NO se desperdicia alto
  // escalando contra una serie que no se dibuja.
  assertTrue(/450/.test(ticks), "modo total: techo redondeado sobre la serie visible");
  const pondTicks = [...commercialForecastChartHtml(MONTHS, "ponderado")
    .matchAll(/category-plot__axis--left">(.*?)<\/div>/gs)][0][1];
  assertTrue(/150/.test(pondTicks) && !/450/.test(pondTicks),
    "modo ponderado: su PROPIO techo, no el de la serie que no se dibuja");
  assertTrue(/>0</.test(ticks), "y arranca en 0, nunca en un mínimo recortado");
  assertTrue(!/%\s*del máximo/i.test(html), "el gráfico no normaliza a porcentaje");
  const zeroed = [{ month: "2026-01", count: 0, hours: 0, weighted_hours: 0, revenue: 0 }];
  assertTrue(!/NaN|Infinity/.test(commercialForecastChartHtml(zeroed)), "serie entera en 0 no produce NaN/Infinity");
  assertTrue(commercialForecastChartHtml([]) === "", "sin períodos no se dibuja un gráfico vacío");
  assertTrue(commercialForecastChartHtml(undefined) === "", "payload ausente tampoco rompe");
}

// --- CERO afordancia de drill mensual (eso es C7) --------------------------
{
  const html = commercialForecastChartHtml(MONTHS);
  assertTrue(!/onclick|data-commercial-forecast-month|data-month|cursor:\s*pointer/i.test(html),
    "ninguna columna finge ser clickeable: sin handler, sin identificador de mes, sin cursor:pointer");
  assertTrue(!/data-event=/.test(html), "el chart no introduce triggers de breakdown propios");
  assertTrue(!/role="button"/.test(html), "las columnas no se anuncian como botones");
  // Lo que SÍ tienen es foco + tooltip, igual que el Funnel: accesibilidad,
  // no acción.
  assertTrue(count(html, 'tabindex="0"') === 4, "una superficie foco-able por período (el tooltip, no un click)");
  assertTrue(count(html, "chart-tooltip-anchor") === 4, "y su ancla de tooltip");
  assertTrue(count(html, 'class="chart-tooltip"') === 4, "un tooltip por período");
  assertTrue(count(html, "aria-label=") === 4, "con el aria-label plano de las cuatro magnitudes");
}

// --- Densidad: scroll horizontal, nunca truncar ----------------------------
{
  const many = Array.from({ length: 30 }, (_, i) =>
    ({ month: `20${25 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`,
       count: i, hours: i * 10, weighted_hours: i * 3, revenue: i * 100 }));
  const html = commercialForecastChartHtml(many);
  assertTrue(count(html, "category-plot__col") === 30, "30 períodos → 30 columnas: nunca se truncan en silencio");
  assertTrue(count(html, "<span>") === 30, "y 30 etiquetas: nunca se esconden alternadas dejando barras ambiguas");
  assertTrue(count(html, "forecast-plot__scroll") === 1,
    "el área y las etiquetas comparten UN scroll box (no pueden desincronizarse)");
  const scroll = html.split("forecast-plot__scroll", 2)[1];
  assertTrue(scroll.indexOf("category-plot__area") < scroll.indexOf("category-plot__xlab"),
    "las dos van adentro del scroll, en ese orden");
  assertTrue(html.indexOf("category-plot__axis--left") < html.indexOf("forecast-plot__scroll"),
    "el eje queda AFUERA del scroll: scrollear nunca pierde la escala");
}

// --- Universo declarado en el panel (0-8), no normalizado a 1-8 -----------
{
  resetState({ forecastStatus: "ready", forecast: FORECAST });
  const panel = commercialForecastPanelBodyHtml("total");
  assertTrue(/Pipeline total/.test(panel) && /etapas 0.8/.test(panel),
    "el panel declara su universo justo donde puede confundirse con Activas (1-8)");
  assertTrue(panel.includes("forecast-scope"), "usando el calificador propio, no una clase compartida redefinida");
  // Va ARRIBA del split: aplica a las DOS mitades, y sacarlo de la columna del
  // gráfico es lo que alinea el arranque de ambas (owner smoke).
  assertTrue(panel.indexOf("forecast-scope") < panel.indexOf("forecast-split"),
    "y vive antes del split, no dentro de la columna del gráfico");
  assertTrue(panel.indexOf("category-plot-legend") < panel.indexOf("forecast-split"),
    "la leyenda también");
  resetState();
}

// --- Estados del panel: loading / error / vacío / listo -------------------
{
  resetState();
  assertTrue(commercialForecastPanelBodyHtml("total").includes("section-analysis-loading"),
    "idle (panel montado antes del primer fetch) muestra el primitivo de carga del Hub");
  resetState({ forecastStatus: "loading" });
  assertTrue(commercialForecastPanelBodyHtml("total").includes("section-analysis-loading"), "loading, el mismo");

  resetState({ forecastStatus: "error", forecastErrorState: "source_error" });
  const err = commercialForecastPanelBodyHtml("total");
  assertTrue(err.includes("dashboard-empty") && /No pudimos cargar el forecast/.test(err),
    "error usa el primitivo de no-disponible del Hub");
  assertTrue(!err.includes("category-plot"), "y NO deja ningún gráfico viejo en pie");

  resetState({ forecastStatus: "error", forecastErrorState: "config_incomplete" });
  assertTrue(/todavía no está configurado/.test(commercialForecastPanelBodyHtml("total")),
    "config_incomplete tiene su propio mensaje, igual que el resto de Comercial");

  resetState({ forecastStatus: "ready", forecast: { months: [] } });
  const empty = commercialForecastPanelBodyHtml("total");
  assertTrue(empty.includes("dashboard-empty") && !empty.includes("category-plot"),
    "payload vacío usa el primitivo de vacío y tampoco deja gráfico");

  resetState({ forecastStatus: "ready", forecast: FORECAST });
  assertTrue(commercialForecastPanelBodyHtml("total").includes("category-plot__bar--hours"), "listo dibuja el gráfico");
}

// --- Conteo del header: "períodos", nunca "meses" -------------------------
// El dataset puede incluir el bucket VÁLIDO "Sin fecha", que no es un mes.
{
  resetState({ forecastStatus: "ready", forecast: FORECAST });
  // C6b: el conteo refleja lo que la TABLA muestra (períodos con valor en la
  // métrica activa) más el total de esa métrica — el equivalente del
  // "N meses · N hs totales" legacy, sin llamarle "mes" a "Sin fecha". Sin
  // root, el modo cae en "total".
  assertTrue(commercialForecastCountLabel("total") === "4 período(s) · 874 hs",
    `cuatro buckets con horas → cuatro períodos + total (got ${commercialForecastCountLabel("total")})`);
  assertTrue(!/mes/i.test(commercialForecastCountLabel("total")), "nunca se llama mes a todo bucket");
  resetState({ forecastStatus: "ready", forecast: { months: [] } });
  assertTrue(commercialForecastCountLabel("total") === "", "sin datos, sin conteo inventado");
  resetState({ forecastStatus: "loading" });
  assertTrue(commercialForecastCountLabel("total") === "", "mientras carga tampoco");
}

// --- Render QUIRÚRGICO: sólo los dos hosts del panel ----------------------
{
  resetState({ forecastStatus: "ready", forecast: FORECAST });
  hosts["[data-commercial-forecast-body]"] = fakeNode();
  hosts["[data-commercial-forecast-count]"] = fakeNode();
  root.innerHTML = ROOT_SENTINEL;
  domCalls.querySelector.length = 0;
  commercialRenderForecastPanel();
  assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("category-plot__bar--hours"),
    "el cuerpo del panel se llena con el gráfico");
  assertTrue(hosts["[data-commercial-forecast-count]"].textContent === "4 período(s) · 874 hs",
    "y el conteo del header se actualiza aparte");
  assertTrue(root.innerHTML === ROOT_SENTINEL,
    "`#commercial-main-content` queda INTACTO: un render completo cerraría el propio panel");
  assertTrue(domCalls.querySelector.join("|")
    === '[data-event="forecast"].is-active|[data-commercial-forecast-body]'
      + "|[data-commercial-forecast-count]|[data-commercial-forecast-title]",
    "cuatro búsquedas acotadas a root: el modo UNA vez + los tres hosts estables, nada más");
  // Sin hosts montados (panel todavía no renderizado) no explota.
  delete hosts["[data-commercial-forecast-body]"];
  delete hosts["[data-commercial-forecast-count]"];
  commercialRenderForecastPanel();
  assertTrue(true, "sin hosts en el DOM, el render quirúrgico es un no-op silencioso");
}

// --- LAZY: quién dispara el fetch y quién no -----------------------------
{
  const okFetch = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", forecast: FORECAST }) });

  // Otro trigger del grupo: jamás pide forecast.
  resetState();
  fetchImpl = okFetch; fetchCalls.length = 0;
  commercialMaybeLoadForecast(fakeTrigger("activas", true));
  assertTrue(fetchCalls.length === 0, "abrir Oportunidades Activas no pide el forecast");

  // Cierre (el trigger quedó inactivo tras el toggle del motor): tampoco.
  resetState();
  fetchCalls.length = 0;
  commercialMaybeLoadForecast(fakeTrigger("forecast", false));
  assertTrue(fetchCalls.length === 0, "cerrar Forecast no dispara ninguna request");

  // Primera apertura: exactamente una request, con el tipo vigente.
  resetState({ tipo: "bi" });
  fetchCalls.length = 0;
  commercialMaybeLoadForecast(fakeTrigger("forecast", true));
  assertTrue(fetchCalls.length === 1, "la primera apertura pide una sola vez");
  assertTrue(fetchCalls[0] === "/comercial/data/forecast?tipo=bi",
    "y respeta el filtro global Todos/NN/BI del workspace");
}

// --- LAZY: reapertura con el MISMO tipo no re-pide -----------------------
{
  const okFetch = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", forecast: FORECAST }) });
  resetState({ forecastStatus: "ready", forecast: FORECAST });
  fetchImpl = okFetch; fetchCalls.length = 0;
  commercialMaybeLoadForecast(fakeTrigger("forecast", true));
  commercialMaybeLoadForecast(fakeTrigger("forecast", true));
  assertTrue(fetchCalls.length === 0, "cerrar y reabrir con el mismo tipo reusa el payload en memoria");

  // Tras un error NO se reintenta en cada apertura (un CRM caído no debe
  // convertirse en una tormenta de requests).
  resetState({ forecastStatus: "error", forecastErrorState: "source_error" });
  fetchCalls.length = 0;
  commercialMaybeLoadForecast(fakeTrigger("forecast", true));
  assertTrue(fetchCalls.length === 0, "tras un error, reabrir no reintenta solo");
}

// --- Cambio de tipo: invalida, NO recarga -----------------------------
{
  resetState({ forecastStatus: "ready", forecast: FORECAST });
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });
  fetchCalls.length = 0;
  commercialSetTipo("nn");
  assertTrue(commercialState.forecast === null && commercialState.forecastStatus === "idle",
    "cambiar Todos/NN/BI invalida el forecast del tipo anterior");
  assertTrue(commercialState.forecastErrorState === "", "y limpia cualquier error viejo");
  assertTrue(!fetchCalls.some(u => u.includes("/forecast")),
    "pero NO lo re-pide: se carga recién cuando se vuelva a abrir el análisis");

  // Y la próxima apertura pide el tipo NUEVO.
  fetchCalls.length = 0;
  commercialMaybeLoadForecast(fakeTrigger("forecast", true));
  assertTrue(fetchCalls.length === 1 && fetchCalls[0] === "/comercial/data/forecast?tipo=nn",
    "la apertura posterior al cambio de tipo pide el tipo nuevo");
}

// --- Fuente: el load inicial NO incluye forecast ---------------------
{
  const eager = src.split("async function commercialLoadData()", 2)[1].split("\n}", 2)[0];
  assertTrue(!eager.includes("/comercial/data/forecast"),
    "forecast NO entra al Promise.all inicial: no agrega una request a cada carga del landing");
  assertTrue(count(eager, "/comercial/data/") === 4, "el load inicial sigue pidiendo exactamente cuatro endpoints");
  // Un solo lugar en todo el archivo construye la URL del forecast.
  assertTrue(count(src, "/comercial/data/forecast") === 1, "y hay UN solo punto de fetch del forecast");
}

// --- Fuente: estructura del panel y su cierre parametrizado ----------
{
  const section = src.split('data-section-id="forecast-analisis"', 2)[1].split("</details>", 2)[0];
  const summaryBlock = section.split("</summary>", 2)[0];
  assertTrue(section.includes('data-breakdown="forecast" open hidden') || src.includes('data-breakdown="forecast" open hidden'),
    "el panel arranca oculto, como el de Activas");
  // C6b: el título es CONMUTABLE (Forecast / Forecast Ponderado), así que sale
  // del mapa de métrica y vive en un host propio para el render quirúrgico.
  assertTrue(summaryBlock.includes("data-commercial-forecast-title"), "host estable del título, en el <summary>");
  assertTrue(src.includes('title: "Forecast — Pipeline por Mes de Cierre"')
    && src.includes('title: "Forecast Ponderado — Pipeline por Mes de Cierre"'),
    "los dos títulos del legacy, uno por lectura");
  assertTrue(summaryBlock.includes("data-commercial-forecast-count"), "host estable del conteo, en el <summary>");
  assertTrue(/<button type="button" class="panel-close" data-commercial-close-analysis="forecast" aria-label="Cerrar análisis">/.test(summaryBlock),
    "X propia, con la key del análisis — mismo mecanismo que Activas, no uno nuevo");
  assertTrue(summaryBlock.indexOf("dashboard-section__count") < summaryBlock.indexOf("panel-close"),
    "la X va después del conteo, en el MISMO flex row del primitivo compartido");
  assertTrue(section.includes("data-commercial-forecast-body"), "host estable del cuerpo");
  // Un solo grupo de breakdown en todo el archivo: el motor compartido es el
  // que garantiza "un análisis abierto por vez".
  // Se cuenta el atributo EMITIDO (`data-breakdown-group>`), no la palabra en
  // el archivo: la prosa que documenta el contrato la menciona varias veces y
  // un scan del texto crudo daría un falso positivo.
  assertTrue(count(src, "data-breakdown-group>") === 1, "un solo grupo de breakdown, cero lifecycle paralelo");
  // El render quirúrgico no puede caer en el render completo.
  const surgical = src.split("function commercialRenderForecastPanel()", 2)[1].split("\n}", 2)[0];
  assertTrue(!/commercialRender\(|commercialRenderCapability\(/.test(surgical),
    "el render del panel nunca escala a un render completo");
}

// --- C6b: tabla por período, columna según el tile de entrada -------------
// Paridad con el `#pipeline-drill-section` legacy: UNA columna de métrica,
// según la lectura del tile que abrió el panel.
{
  // `ene 27` tiene horas pero CERO ponderadas: es el caso que hace que la
  // tabla muestre distinta cantidad de filas en cada modo (comportamiento
  // legacy, consecuencia aceptada por el owner).
  const MIXED = [
    { month: "2026-08", count: 6, hours: 3360, weighted_hours: 1296, revenue: 1,
      opportunities: [{ stage: "4 Propuesta", seller: "Ana", hours: 3360, probability: 38 }] },
    { month: "2026-09", count: 43, hours: 27342, weighted_hours: 10320, revenue: 1,
      opportunities: [{ stage: "0 Prospecto", seller: "Beto", hours: 27342, probability: 37 }] },
    { month: "2027-01", count: 1, hours: 800, weighted_hours: 0, revenue: 1,
      opportunities: [{ stage: "0 Prospecto", seller: "Ana", hours: 800, probability: 0 }] },
    { month: "Sin fecha", count: 2, hours: 64, weighted_hours: 12, revenue: 1,
      opportunities: [{ stage: "1 Contacto", seller: "Beto", hours: 64, probability: 19 }] },
  ];
  const cols = html => [...html.matchAll(/<th>([^<]*)<\/th>/g)].map(m => m[1]);
  // Períodos = filas del `<tbody>` SIN la de totales (que también usa
  // `.row-label-col`). Separarlas es a propósito: así un cambio en la fila de
  // totales no puede pasar por "un período más".
  const allLabels = html => [...html.matchAll(/row-label-col">([^<]*)</g)].map(m => m[1]);
  const rows = html => allLabels(html).filter(l => !/^Total /.test(l));
  const totalCells = html => [...html.split('class="is-total"')[1].split("</tr>")[0]
    .matchAll(/<td[^>]*>([^<]*)<\/td>/g)].map(m => m[1]);

  resetState({ forecastStatus: "ready", forecast: { months: MIXED } });
  const total = commercialForecastPanelBodyHtml("total");
  const pond = commercialForecastPanelBodyHtml("ponderado");

  assertTrue(cols(total).join("|") === "Mes|Opps.|Hs Total|% del total",
    "modo total: las cuatro columnas del legacy, con Hs Total");
  assertTrue(cols(pond).join("|") === "Mes|Opps.|Hs Pond.|% del total",
    "modo ponderado: la MISMA tabla, con Hs Pond.");
  assertTrue(cols(total).length === 4 && cols(pond).length === 4,
    "UNA sola columna de métrica por vez — nunca las dos juntas");

  // El gráfico sigue mostrando las DOS series en los dos modos: lo que
  // conmuta es la tabla, no el gráfico (que el owner validó como está).
  // Gráfico y tabla comparten la lectura: una serie, la del modo, sobre TODOS
  // los buckets (el gráfico no esconde los de cero como sí hace la tabla).
  assertTrue(count(total, "category-plot__bar--hours") === 4
    && count(total, "category-plot__bar--weighted") === 0,
    "modo total: el gráfico dibuja Horas sobre los cuatro buckets, y sólo Horas");
  assertTrue(count(pond, "category-plot__bar--weighted") === 4
    && count(pond, "category-plot__bar--hours") === 0,
    "modo ponderado: dibuja Ponderadas sobre los cuatro buckets, y sólo Ponderadas");
  // Lado a lado: gráfico a la izquierda, tabla a la derecha (owner smoke).
  assertTrue(total.includes("forecast-split__chart") && total.includes("forecast-split__table"),
    "gráfico y tabla viven en la grilla de dos columnas");
  assertTrue(total.indexOf("forecast-split__chart") < total.indexOf("forecast-split__table"),
    "y el gráfico va primero: patrón a la izquierda, precisión a la derecha");
  // Ningún campo de la barra de filtros se estira al ancho completo.
  assertTrue(!total.includes("whatsnew-filter--fill"),
    "ni Etapa ni Vendedor son elásticos: los dos cortan en el ancho del select");

  // Meses sin valor en la métrica ACTIVA no entran en la tabla (legacy).
  assertTrue(rows(total).join(", ") === "Agosto de 2026, Septiembre de 2026, Enero de 2027, Sin fecha de cierre",
    `modo total: los cuatro períodos con horas, con el mes completo (got ${rows(total).join(", ")})`);
  assertTrue(rows(pond).join(", ") === "Agosto de 2026, Septiembre de 2026, Sin fecha de cierre",
    "modo ponderado: `Enero de 2027` desaparece porque no tiene ponderadas");
  // El EJE del gráfico se queda con la forma corta: ~52px por período no dan
  // para "Septiembre de 2026". Es el mismo reparto que hace el original.
  const xlab = total.split('category-plot__xlab">')[1].split("</div>")[0];
  assertTrue([...xlab.matchAll(/<span>([^<]*)<\/span>/g)].map(m => m[1]).join("|")
    === "ago 26|sep 26|ene 27|Sin fecha",
    "el eje X conserva la etiqueta corta");
  assertTrue(rows(total).includes("Sin fecha de cierre"),
    "el bucket sin fecha se explicita en la tabla — 'Sin fecha' solo sería ambiguo entre meses");
  assertTrue(rows(total)[rows(total).length - 1] === "Sin fecha de cierre", "y queda último");
  // Fila de TOTALES (owner smoke): reusa `tr.is-total`, el primitivo que ya
  // cierra la tabla de detalle de Oportunidades — cero clase nueva.
  assertTrue(total.includes('class="is-total"'), "la tabla cierra con una fila de totales");
  assertTrue(totalCells(total).join(" | ") === "Total (4) | 52 | 31,566 hs | 100%",
    `totales del modo total (got ${totalCells(total).join(" | ")})`);
  assertTrue(totalCells(pond).join(" | ") === "Total (3) | 51 | 11,628 hs | 100%",
    `totales del modo ponderado — sin el período que no tiene ponderadas (got ${totalCells(pond).join(" | ")})`);
  assertTrue(!totalCells(total).some(c => /bar-/.test(c)),
    "la celda de la métrica va SIN barra: al 100% se leería como un mes más, no como el agregado");
  // El total de la fila coincide SIEMPRE con el del header — misma fuente.
  assertTrue(commercialForecastCountLabel("total").includes("31,566 hs")
    && totalCells(total)[2] === "31,566 hs", "el total de la tabla y el del header no pueden divergir");

  // Conteo del header: períodos VISIBLES + total de la métrica activa.
  assertTrue(commercialForecastCountLabel("total") === "4 período(s) · 31,566 hs", "header en modo total");
  assertTrue(commercialForecastCountLabel("ponderado") === "3 período(s) · 11,628 hs", "header en modo ponderado");

  // % del total suma 100 (con el redondeo de una decimal).
  const pcts = [...total.matchAll(/<td>(\d+\.\d)%<\/td>/g)].map(m => parseFloat(m[1]));
  assertTrue(pcts.length === 4 && Math.abs(pcts.reduce((a, b) => a + b, 0) - 100) < 0.2,
    "los porcentajes son sobre el total de la métrica y cierran en 100");

  // Barra in-row: primitivos COMPARTIDOS del Hub, sin clase de color propia.
  assertTrue(count(total, "bar-track") === 4 && count(total, "bar-fill") === 4 && count(total, "bar-value") === 4,
    "una barra por fila, con los primitivos compartidos `.bar-track`/`.bar-fill`/`.bar-value`");
  assertTrue(total.includes("dashboard-table") && total.includes("matrix-scroll"),
    "y la tabla es el `.dashboard-table` de siempre, dentro del scroll compartido");

  // --- Filtros: re-agrupan gráfico Y tabla, cero red ---------------------
  const options = commercialForecastFilterOptions(MIXED);
  assertTrue(options.stages.join("|") === "0 Prospecto|1 Contacto|4 Propuesta",
    "las etapas salen del payload de FORECAST (universo 0-8, incluye prospectos), no de pipeline.stages");
  assertTrue(options.sellers.join("|") === "Ana|Beto", "y los vendedores, alfabéticos");

  fetchCalls.length = 0;
  commercialState.forecastSellerFilter = "Ana";
  const filtered = commercialForecastPanelBodyHtml("total");
  assertTrue(rows(filtered).join(", ") === "Agosto de 2026, Enero de 2027", "el filtro de Vendedor re-agrupa la tabla");
  assertTrue(count(filtered, "category-plot__col") === 4,
    "el gráfico recibe el MISMO universo filtrado (mismos buckets, valores recalculados)");
  assertTrue(commercialForecastCountLabel("total") === "2 período(s) · 4,160 hs", "y el header acompaña");
  assertTrue(fetchCalls.length === 0, "filtrar es client-side puro: cero requests");
  // Las OPCIONES siguen siendo las del payload completo: una opción no puede
  // desaparecer por haberla elegido.
  assertTrue(filtered.includes(">Beto<"), "el vendedor no elegido sigue disponible en el select");
  assertTrue(filtered.includes('value="Ana" selected'), "y el elegido queda seleccionado");

  commercialState.forecastStageFilter = "0 Prospecto";
  assertTrue(rows(commercialForecastPanelBodyHtml("total")).join(", ") === "Enero de 2027",
    "Etapa y Vendedor se combinan");
  commercialState.forecastStageFilter = "4 Propuesta";
  commercialState.forecastSellerFilter = "Beto";
  const none = commercialForecastPanelBodyHtml("total");
  assertTrue(none.includes("dashboard-empty") && /Ningún período/.test(none),
    "una combinación sin resultados usa el primitivo de vacío, no una tabla fantasma");

  // Barra de filtros: mismos primitivos que Activas + el ✕ Limpiar compartido.
  assertTrue(none.includes('class="whatsnew-filters" data-commercial-forecast-filters'),
    "la barra reusa el primitivo compartido de filtros del Hub");
  assertTrue(none.includes('class="whatsnew-filter__clear" data-commercial-forecast-clear'),
    "con el MISMO ✕ Limpiar que Activas — un solo lenguaje de filtros en la vertical");
  assertTrue(none.includes("Todas las etapas") && none.includes("Todos los vendedores"),
    "y el copy neutro del legacy");

  // Cambiar Todos/NN/BI invalida los filtros junto con el payload.
  commercialSetTipo("nn");
  assertTrue(commercialState.forecastStageFilter === "" && commercialState.forecastSellerFilter === "",
    "un cambio de tipo resetea los filtros: podrían apuntar a un universo que ya no existe");
  resetState();
}

// ── DOM mínimo para probar la INTERACCIÓN real ──────────────────────────────
// Sólo lo que tocan el motor compartido y los delegados de commercial.js:
// dataset, classList, hidden, listeners, matches/closest/querySelector(All) y
// un `click()`/`keydown` que burbujean del trigger al `document` en ESE orden
// — que es la premisa de todo el wiring (el listener del motor cuelga directo
// del trigger y corre ANTES de que el evento llegue al delegado).
function domCamel(attr) {
  return attr.replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}
function domMatches(node, selector) {
  return selector.split(",").some(part => {
    const tokens = part.trim().match(/\[[^\]]+\]|\.[A-Za-z0-9_-]+/g) || [];
    if (!tokens.length) return false;
    return tokens.every(token => {
      if (token.startsWith(".")) return node.classSet.has(token.slice(1));
      const inner = token.slice(1, -1);
      const eq = inner.indexOf("=");
      if (eq < 0) return inner in node.attrs;
      const name = inner.slice(0, eq);
      const value = inner.slice(eq + 1).replace(/^["']|["']$/g, "");
      return node.attrs[name] === value;
    });
  });
}
function domEl(attrs = {}, classes = []) {
  const classSet = new Set(classes);
  const own = Object.create(null);
  const node = {
    attrs: { ...attrs }, dataset: Object.create(null), classSet,
    children: [], parent: null, hidden: false, open: false,
    classList: {
      add: c => classSet.add(c),
      remove: c => classSet.delete(c),
      contains: c => classSet.has(c),
      toggle(c, force) {
        const on = force === undefined ? !classSet.has(c) : !!force;
        if (on) classSet.add(c); else classSet.delete(c);
        return on;
      },
    },
    addEventListener(type, cb) { (own[type] = own[type] || []).push(cb); },
    append(...kids) { kids.forEach(k => { k.parent = node; node.children.push(k); }); return node; },
    matches: sel => domMatches(node, sel),
    closest(sel) { let n = node; while (n) { if (domMatches(n, sel)) return n; n = n.parent; } return null; },
    querySelector(sel) { return domDescendants(node).find(d => domMatches(d, sel)) || null; },
    querySelectorAll(sel) { return domDescendants(node).filter(d => domMatches(d, sel)); },
    scrollIntoView() { node.scrolled = (node.scrolled || 0) + 1; },
    // Burbujeo real: primero los listeners del propio nodo (el del motor),
    // después los delegados de `document`.
    click() {
      const event = { target: node, preventDefault() {}, stopPropagation() {} };
      (own.click || []).forEach(cb => cb(event));
      (listeners.click || []).forEach(cb => cb(event));
    },
    press(key) {
      const event = { target: node, key, preventDefault() {}, stopPropagation() {} };
      (own.keydown || []).forEach(cb => cb(event));
      (listeners.keydown || []).forEach(cb => cb(event));
    },
  };
  Object.keys(node.attrs).forEach(a => {
    if (a.startsWith("data-")) node.dataset[domCamel(a)] = node.attrs[a];
  });
  return node;
}
function domDescendants(node) {
  return node.children.flatMap(c => [c, ...domDescendants(c)]);
}

// --- Interacción: marcador visual único + X sobre el trigger ACTIVO --------
// (C6 visual corrective, ver HANDOFF). Se ejecuta el motor compartido REAL y
// los delegados REALES de commercial.js: nada de assertions sobre texto fuente.
{
  const restoreGetElementById = sandbox.document.getElementById;

  // Los tres tiles del contrato: Activas (trigger único) y los DOS de Pipeline
  // que comparten `data-event="forecast"`. Panel de Activas primero, para que
  // "Pipeline total" sea de verdad el PRIMER `[data-event="forecast"]` del DOM
  // — es exactamente la trampa que rompía la X.
  const build = () => {
    const activas = domEl({ "data-event": "activas" }, ["activity-kpi", "activity-kpi--clickable"]);
    const total = domEl({ "data-event": "forecast", "data-commercial-event-view": "total" },
      ["activity-kpi", "activity-kpi--clickable"]);
    const ponderado = domEl({ "data-event": "forecast", "data-commercial-event-view": "ponderado" },
      ["activity-kpi", "activity-kpi--clickable"]);
    const panelActivas = domEl({ "data-breakdown": "activas" });
    const panelForecast = domEl({ "data-breakdown": "forecast" });
    const xActivas = domEl({ "data-commercial-close-analysis": "activas" }, ["panel-close"]);
    const xForecast = domEl({ "data-commercial-close-analysis": "forecast" }, ["panel-close"]);
    panelActivas.append(xActivas);
    panelForecast.append(xForecast);
    panelActivas.hidden = true;
    panelForecast.hidden = true;
    const group = domEl({ "data-breakdown-group": "" }, ["dashboard-sections"]);
    group.append(activas, total, ponderado, panelActivas, panelForecast);
    const domRoot = domEl({ id: "commercial-main-content" });
    domRoot.append(group);
    sandbox.document.getElementById = id => (id === "commercial-main-content" ? domRoot : null);
    // Motor compartido REAL sobre un scope que sí tiene el grupo.
    sandbox.initEventBreakdown({ querySelectorAll: sel => domRoot.querySelectorAll(sel) }, { openFirst: false });
    return { activas, total, ponderado, panelActivas, panelForecast, xActivas, xForecast, domRoot };
  };
  // UN solo DOM y UN solo cableado, como en la app real: los delegados de
  // commercial.js capturan `root` una sola vez (en DOMContentLoaded), así que
  // reconstruir el DOM entre sub-bloques dejaría al delegado apuntando al
  // primero. Entre casos se reinicia el ESTADO, nunca el árbol.
  const on = el => el.classList.contains("is-active");
  const open = panel => panel.hidden === false;

  const d = build();
  const reset = () => {
    [d.activas, d.total, d.ponderado].forEach(t => t.classList.remove("is-active"));
    d.panelActivas.hidden = true;
    d.panelForecast.hidden = true;
  };
  // Los delegados de commercial.js se cablean una sola vez, en DOMContentLoaded.
  (listeners.DOMContentLoaded || []).forEach(cb => cb());
  assertTrue((listeners.click || []).length === 1, "un solo delegado de click en todo commercial.js");
  assertTrue((listeners.keydown || []).length === 1, "y un solo delegado de teclado");

  // --- 1: los dos tiles siguen compartiendo el MISMO data-event ------------
  assertTrue(d.total.dataset.event === "forecast" && d.ponderado.dataset.event === "forecast",
    "los dos tiles de Pipeline siguen apuntando al MISMO análisis");

  // --- 2: click en Pipeline total → abre y queda activo SÓLO él ------------
  {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    d.total.click();
    assertTrue(open(d.panelForecast), "Pipeline total abre el Forecast");
    assertTrue(on(d.total) && !on(d.ponderado), "y queda activo SÓLO Pipeline total");
    // C6b: el MODO se LEE del DOM (del tile activo), no se guarda.
    assertTrue(commercialForecastMode(d.domRoot) === "total", "y el modo de lectura es Total");
    assertTrue(!open(d.panelActivas) && !on(d.activas), "Activas queda cerrada — exclusividad del motor compartido");
  }

  // --- 3: click en Pipeline ponderado desde cerrado → el MISMO panel -------
  {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    d.ponderado.click();
    assertTrue(open(d.panelForecast), "Pipeline ponderado abre el MISMO panel (no hay un segundo)");
    assertTrue(on(d.ponderado) && !on(d.total), "y queda activo SÓLO Pipeline ponderado");
    assertTrue(commercialForecastMode(d.domRoot) === "ponderado", "y el modo de lectura es Ponderado");
  }

  // --- 4 y 5: cambiar de tile de entrada con el panel ABIERTO --------------
  {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", forecast: FORECAST }) });
    d.total.click();
    fetchCalls.length = 0;
    d.ponderado.click();
    assertTrue(open(d.panelForecast), "el Forecast SIGUE abierto al cambiar de tile de entrada");
    assertTrue(on(d.ponderado) && !on(d.total), "el marcador visual se MUEVE al tile recién clickeado");
    assertTrue(commercialForecastMode(d.domRoot) === "ponderado",
      "y la LECTURA cambia con él: la tabla pasa a Hs Pond. sin volver a pedir nada");
    assertTrue(fetchCalls.length === 0, "y no se dispara ninguna request: el payload es el mismo");
    // Simétrico, de vuelta.
    d.total.click();
    assertTrue(open(d.panelForecast) && on(d.total) && !on(d.ponderado),
      "simétrico: ponderado → total mueve el marcador y deja el panel abierto");
    assertTrue(commercialForecastMode(d.domRoot) === "total", "y la lectura vuelve a Total");
    assertTrue(fetchCalls.length === 0, "tampoco al volver");
  }

  // --- 6: click en el tile ACTIVO → cierra, ninguno queda activo -----------
  {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    d.ponderado.click();
    d.ponderado.click();
    assertTrue(!open(d.panelForecast), "re-clickear el tile activo cierra el Forecast");
    assertTrue(!on(d.total) && !on(d.ponderado), "y no deja ninguno marcado");
    assertTrue(commercialForecastMode(d.domRoot) === "total",
      "sin tile activo el modo cae en el default, nunca en undefined");
  }

  // --- 7: Enter y Espacio con la MISMA semántica visual --------------------
  for (const key of ["Enter", " "]) {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    d.ponderado.press(key);
    assertTrue(open(d.panelForecast), `${key === " " ? "Espacio" : key} abre el Forecast`);
    assertTrue(on(d.ponderado) && !on(d.total), `${key === " " ? "Espacio" : key}: un solo tile marcado`);
    d.total.press(key);
    assertTrue(open(d.panelForecast) && on(d.total) && !on(d.ponderado),
      `${key === " " ? "Espacio" : key}: cambiar de tile mueve el marcador sin cerrar`);
    d.total.press(key);
    assertTrue(!open(d.panelForecast) && !on(d.total) && !on(d.ponderado),
      `${key === " " ? "Espacio" : key}: sobre el activo, cierra`);
  }

  // --- 8: la X resuelve el trigger ACTIVO, no el primero del DOM ----------
  // El caso que rompía: abrir desde "Pipeline ponderado" deja INACTIVO a
  // "Pipeline total", que es el PRIMER [data-event="forecast"] del documento.
  {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    d.ponderado.click();
    assertTrue(d.total === d.panelForecast.parent.querySelector('[data-event="forecast"]'),
      "confirmación del fixture: el primer trigger del DOM es el que NO abrió el panel");
    d.xForecast.click();
    assertTrue(!open(d.panelForecast), "la X cierra el Forecast abierto desde Pipeline ponderado");
    assertTrue(!on(d.total) && !on(d.ponderado), "y deja los dos tiles inactivos");
  }
  {
    reset();
    resetState({ forecastStatus: "ready", forecast: FORECAST });
    d.total.click();
    d.xForecast.click();
    assertTrue(!open(d.panelForecast) && !on(d.total) && !on(d.ponderado),
      "y también cuando lo abrió Pipeline total — la X no depende de cuál fue");
  }

  // --- 9: la X de Activas se comporta EXACTAMENTE como antes ---------------
  {
    reset();
    resetState();
    d.activas.click();
    assertTrue(open(d.panelActivas) && on(d.activas), "Activas abre igual que siempre");
    d.xActivas.click();
    assertTrue(!open(d.panelActivas) && !on(d.activas), "y su X la cierra igual que siempre");
    assertTrue(!open(d.panelForecast), "sin efectos colaterales sobre el Forecast");
  }

  // --- 11: cero estado persistente de "tile de entrada" -------------------
  {
    const stateKeys = Object.keys(commercialState);
    for (const banned of ["forecastOpenedBy", "activeForecastTile", "pipelineEntrySource", "forecastTrigger"]) {
      assertTrue(!stateKeys.includes(banned), `no existe estado nuevo \`${banned}\``);
    }
    // Se mira el CÓDIGO, no la prosa: el comentario que documenta este mismo
    // contrato nombra a propósito los literales prohibidos.
    const code = src.split("\n").filter(l => !l.trim().startsWith("//")).join("\n");
    assertTrue(!/openedBy|entryTile|EntrySource|activeTile/i.test(code),
      "ni ningún equivalente en todo el archivo");
    const normalizer = src.split("function commercialNormalizeSharedTriggerActiveState(", 2)[1].split("\n}", 2)[0];
    assertTrue(!normalizer.includes("commercialState."),
      "el normalizador no lee ni escribe estado: el DOM es la única fuente de verdad");
    assertTrue(!/fetch\(/.test(normalizer), "y no toca la red");
  }

  sandbox.document.getElementById = restoreGetElementById;
}

// --- Ciclo COMPLETO del lazy load (asíncrono, al final para no pisarse con
//     los bloques sincrónicos de arriba) ---------------------------------
(async () => {
  // Se drena lo que quedó pendiente de los bloques sincrónicos de arriba
  // (`commercialSetTipo` dispara su propio load) para que los contadores de
  // DOM de acá abajo midan SÓLO este ciclo.
  await new Promise(resolve => setImmediate(resolve));
  // El load resuelve y actualiza SÓLO el panel, nunca la página.
  resetState();
  hosts["[data-commercial-forecast-body]"] = fakeNode();
  hosts["[data-commercial-forecast-count]"] = fakeNode();
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", forecast: FORECAST }) });
  fetchCalls.length = 0;
  root.innerHTML = ROOT_SENTINEL;
  const inFlight = sandbox.commercialLoadForecast();
  // Mientras vuela, el panel ya muestra su estado de carga LOCAL.
  assertTrue(commercialState.forecastStatus === "loading", "el panel entra en loading sin esperar la respuesta");
  assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("section-analysis-loading"),
    "y lo dice en su propio cuerpo, con el primitivo de carga del Hub");
  await inFlight;
  assertTrue(commercialState.forecastStatus === "ready", "al resolver, queda listo");
  assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("category-plot__bar--"),
    "y el gráfico aparece por render quirúrgico");
  assertTrue(hosts["[data-commercial-forecast-count]"].textContent === "4 período(s) · 874 hs", "con su conteo");
  assertTrue(root.innerHTML === ROOT_SENTINEL, "sin un solo `commercialRender()` de por medio");

  // El fallo queda AISLADO: sólo este panel se entera.
  resetState({ status: "ready", summary: { marker: 1 }, pipeline: { stages: [{ stage: "1 X" }] },
               analytics: { marker: 2 }, opportunities: { marker: 3 } });
  const before = {
    status: commercialState.status, summary: commercialState.summary,
    pipeline: commercialState.pipeline, analytics: commercialState.analytics,
    opportunities: commercialState.opportunities,
  };
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "source_error" }) });
  await sandbox.commercialLoadForecast();
  assertTrue(commercialState.forecastStatus === "error" && commercialState.forecastErrorState === "source_error",
    "el panel registra su propio error");
  assertTrue(commercialState.status === before.status && commercialState.summary === before.summary
    && commercialState.pipeline === before.pipeline && commercialState.analytics === before.analytics
    && commercialState.opportunities === before.opportunities,
    "y NO toca el estado del Dashboard, de Activas, de las analíticas ni del detalle");
  assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("dashboard-empty"),
    "el cuerpo pasa al estado de no-disponible…");
  assertTrue(!hosts["[data-commercial-forecast-body]"].innerHTML.includes("category-plot"),
    "…sin dejar el gráfico anterior en pantalla");
  assertTrue(hosts["[data-commercial-forecast-count]"].textContent === "", "y el conteo se limpia");

  // Un fetch que directamente explota (red caída) también degrada solo.
  resetState();
  fetchImpl = () => Promise.reject(new Error("network down"));
  await sandbox.commercialLoadForecast();
  assertTrue(commercialState.forecastStatus === "error" && commercialState.forecastErrorState === "source_error",
    "una excepción de red cae en el mismo camino fail-soft, sin propagar");

  // --- "Reintentar" del Forecast (corrective, ver HANDOFF) ---------------
  // El primitivo visual es el MISMO que el del estado de error del Dashboard,
  // pero el callback NO: el hook lleva la key de lo que reintenta. Sin ella,
  // el botón del Forecast recargaba el Dashboard entero (cuatro endpoints que
  // no habían fallado), no pedía el forecast, y de paso cerraba el propio
  // panel. Se ejercita el delegado REAL.
  {
    const retry = key => (listeners.click || []).forEach(cb => cb({
      target: { closest: sel => (sel === "[data-commercial-retry]" ? { dataset: { commercialRetry: key } } : null) },
      preventDefault() {}, stopPropagation() {},
    }));
    // El modo se lee del DOM: se monta un trigger activo para que el render
    // quirúrgico tenga de dónde sacarlo.
    hosts['[data-event="forecast"].is-active'] = { dataset: { commercialEventView: "ponderado" } };
    hosts["[data-commercial-forecast-body]"] = fakeNode();
    hosts["[data-commercial-forecast-count]"] = fakeNode();
    hosts["[data-commercial-forecast-title]"] = fakeNode();

    // El botón que se emite declara SU key, distinta de la del Dashboard.
    resetState({ forecastStatus: "error", forecastErrorState: "source_error" });
    const errorBody = commercialForecastPanelBodyHtml("total");
    assertTrue(errorBody.includes('data-commercial-retry="forecast"'),
      "el estado de error del Forecast ofrece reintentar CON su propia key");
    assertTrue(!errorBody.includes('data-commercial-retry="dashboard"'),
      "y nunca la del Dashboard");
    resetState({ forecastStatus: "error", forecastErrorState: "config_incomplete" });
    assertTrue(!commercialForecastPanelBodyHtml("total").includes("data-commercial-retry"),
      "con el CRM sin configurar tampoco ofrece reintentar acá");

    // Éxito: error → loading → ready, una sola request, y sólo la del forecast.
    const dashboard = { status: "ready", summary: { marker: 1 }, pipeline: { stages: [] },
                        analytics: { marker: 2 }, opportunities: { marker: 3 } };
    resetState({ tipo: "bi", forecastStatus: "error", forecastErrorState: "source_error",
                 forecastStageFilter: "4 Propuesta", forecastSellerFilter: "Ana", ...dashboard });
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", forecast: FORECAST }) });
    fetchCalls.length = 0;
    root.innerHTML = ROOT_SENTINEL;
    retry("forecast");
    assertTrue(fetchCalls.length === 1 && fetchCalls[0] === "/comercial/data/forecast?tipo=bi",
      `pide EXACTAMENTE el forecast, con el tipo vigente (got ${fetchCalls.join(",")})`);
    assertTrue(!fetchCalls.some(u => /summary|pipeline|analytics|opportunities/.test(u)),
      "cero requests a los otros cuatro endpoints: no habían fallado");
    assertTrue(commercialState.forecastStatus === "loading",
      "entra en carga LOCAL de inmediato, sin esperar la respuesta");
    assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("section-analysis-loading"),
      "y lo dice en su propio cuerpo — el panel no se cierra ni queda con el cartel de error");
    assertTrue(commercialState.status === "ready", "el Dashboard sigue `ready`, nadie lo tocó");
    await new Promise(resolve => setImmediate(resolve));
    assertTrue(commercialState.forecastStatus === "ready", "al resolver, error → loading → ready");
    assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("category-plot__bar--weighted"),
      "y el panel se llena por render quirúrgico");
    assertTrue(root.innerHTML === ROOT_SENTINEL,
      "sin `commercialRender()`: un render completo cerraría el panel que se está reintentando");
    assertTrue(commercialState.forecastStageFilter === "4 Propuesta"
      && commercialState.forecastSellerFilter === "Ana",
      "los filtros vigentes del Forecast sobreviven el reintento");
    assertTrue(commercialState.summary === dashboard.summary && commercialState.pipeline === dashboard.pipeline
      && commercialState.analytics === dashboard.analytics
      && commercialState.opportunities === dashboard.opportunities,
      "y el resto de los datos de Comercial queda intacto, por identidad de referencia");
    assertTrue(hosts["[data-commercial-forecast-title]"].textContent.includes("Ponderado"),
      "el modo se sigue leyendo del DOM tras el reintento");

    // Vuelve a fallar: error → loading → error.
    resetState({ tipo: "bi", forecastStatus: "error", forecastErrorState: "source_error", ...dashboard });
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "source_error" }) });
    fetchCalls.length = 0;
    retry("forecast");
    assertTrue(commercialState.forecastStatus === "loading", "un reintento que va a fallar igual entra en carga");
    await new Promise(resolve => setImmediate(resolve));
    assertTrue(fetchCalls.length === 1, "una sola request");
    assertTrue(commercialState.forecastStatus === "error" && commercialState.forecastErrorState === "source_error",
      "y vuelve a `source_error`, sin quedar pegado en loading");
    assertTrue(hosts["[data-commercial-forecast-body]"].innerHTML.includes("data-commercial-retry=\"forecast\""),
      "el panel vuelve a ofrecer el reintento — nunca un estado sin salida");

    // Doble click: el guard es contra `loading`, no contra `error`.
    resetState({ tipo: "bi", forecastStatus: "error", ...dashboard });
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", forecast: FORECAST }) });
    fetchCalls.length = 0;
    retry("forecast");
    retry("forecast");
    assertTrue(fetchCalls.length === 1, "doble click → una sola request, nunca dos concurrentes");
    await new Promise(resolve => setImmediate(resolve));

    // Y el retry del Dashboard sigue haciendo LO SUYO: el reload completo.
    resetState({ tipo: "bi", status: "error", errorState: "source_error", forecastStatus: "ready", forecast: FORECAST });
    // Se lo deja fallar a propósito: acá sólo interesan las requests que
    // dispara y que no toque el forecast, no el render de los tiles (que
    // exigiría un fixture completo de summary/pipeline).
    fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "source_error" }) });
    fetchCalls.length = 0;
    retry("dashboard");
    assertTrue(fetchCalls.length === 4, "el retry del Dashboard sigue recargando sus cuatro endpoints");
    assertTrue(!fetchCalls.some(u => u.includes("/forecast")),
      "y NO arrastra el forecast: sigue siendo lazy");
    assertTrue(commercialState.forecastStatus === "ready" && commercialState.forecast === FORECAST,
      "ni invalida el payload de forecast ya cargado");
    await new Promise(resolve => setImmediate(resolve));

    delete hosts['[data-event="forecast"].is-active'];
    delete hosts["[data-commercial-forecast-title]"];
  }

  finish();
})();

function finish() {
  if (failures) {
    console.log(`\n${failures} invariante(s) fallaron`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL FORECAST INVARIANTS PASSED");
}
