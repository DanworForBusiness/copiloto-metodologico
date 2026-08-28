// commercial_landing.node.js — invocado por test_commercial_landing.py.
// Homologación del landing del CRM legacy (ver HANDOFF — Landing
// Homologation): las DOS familias de tiles (9 principales + 6 Analíticas del
// Pipeline), la composición por segmento honesta, y el análisis contextual
// que no ocupa espacio hasta que se lo acciona.
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures — MISMA técnica que
// commercial_capabilities.node.js.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

// Los listeners se GUARDAN para poder ejercitar el delegado REAL de click en
// el bloque de "Reintentar" (abajo), en vez de afirmar sobre texto fuente.
const listeners = Object.create(null);
const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const { commercialDashboardHtml, commercialSplitHtml, commercialRichKpi } = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
const labelsOf = html => [...html.matchAll(/activity-kpi__label">([^<]+)</g)].map(m => m[1]);
const tilesOf = html => count(html, '<div class="activity-kpi[" ]');

function seg(nn, bi, both, neither) {
  return {
    nn: { count: nn, hours: nn * 10 }, bi: { count: bi, hours: bi * 10 },
    both: { count: both, hours: both * 10 }, neither: { count: neither, hours: neither * 10 },
  };
}
const SEGMENTS = {
  activas: seg(19, 20, 2, 3), prospectos: seg(1, 1, 0, 1), pipeline: seg(1, 1, 0, 0),
  ganadas: seg(5, 6, 0, 1), perdidas: seg(3, 4, 0, 1), suspendidas: seg(1, 1, 0, 0),
};
const SUMMARY = {
  labels: { nn: "New Name", bi: "Base Instalada" },
  prospectos: { count: 51, hours: 24544 },
  pipeline: { hours: 50963, weighted_hours: 13815 },
  activas: { count: 40, hours: 26419 },
  ganadas: { count: 30, hours: 11432 },
  perdidas: { count: 29, hours: 13513 },
  suspendidas: { count: 8, hours: 4260 },
  conversion: 50.8, conversion_with_suspended: 44.8,
  segments: SEGMENTS,
};
const ANALYTICS = {
  creadas: { this_week: 0, last_week: 1 },
  pipeline_age: { avg_days: 219.8, count: 40 },
  stage_time: { avg_days: 22.8, stages_with_data: 8 },
  stage_conversion: { min_rate: 33.3, steps: 7 },
  close_time: { avg_days: 112.5, count: 30 },
  stagnant: { count: 19, threshold_days: 30 },
  lead_conversion: { companies: 100, any_conv: 5, rate_any_conv: 5.3, adv_opp: 4, rate_adv_opp: 3.9 },
};
const PIPELINE = { opportunities: 40, stages: [
  { stage: "1 Contacto", count: 7, hours: 2760, weighted_hours: 276, revenue: 1 },
  { stage: "4 Propuesta", count: 20, hours: 900, weighted_hours: 450, revenue: 2 },
] };

function load({ summary = SUMMARY, analytics = ANALYTICS, pipeline = PIPELINE } = {}) {
  commercialState.status = "ready";
  commercialState.summary = JSON.parse(JSON.stringify(summary));
  commercialState.analytics = analytics ? JSON.parse(JSON.stringify(analytics)) : null;
  commercialState.pipeline = pipeline;
  return commercialDashboardHtml();
}

// --- Paridad de landing: 9 principales + 6 analíticas -----------------------
{
  const html = load();
  assertTrue(tilesOf(html) === 15, `15 tiles en total (9 + 6), got ${tilesOf(html)}`);
  const labels = labelsOf(html);
  const main = ["Oportunidades activas", "Prospectos", "Pipeline total", "Pipeline ponderado",
                "Ganadas", "Perdidas", "Suspendidas", "Creadas esta semana", "Conversión"];
  assertTrue(labels.slice(0, 9).join("|") === main.join("|"), `los 9 principales, en orden (got ${labels.slice(0, 9)})`);
  const analytics = ["Edad del pipeline", "Tiempo por etapa", "Conversión etapa a etapa",
                     "Tiempo de cierre", "Estancadas", "Conversión de leads"];
  assertTrue(labels.slice(9).join("|") === analytics.join("|"), `las 6 analíticas, en orden (got ${labels.slice(9)})`);
  assertTrue(html.includes("Analíticas del Pipeline"), "la agrupación conceptual sigue nombrada");
  assertTrue(count(html, "activity-kpi-grid") === 2, "dos familias, dos grillas");
}

// --- Métricas exactas de cada tile ------------------------------------------
{
  const html = load();
  const tile = label => {
    const i = html.indexOf(`>${label}<`);
    const start = html.lastIndexOf('<div class="activity-kpi', i);
    return html.slice(start, html.indexOf("</div>", i));
  };
  assertTrue(tile("Oportunidades activas").includes(">40<"), "Activas: count");
  assertTrue(tile("Oportunidades activas").includes("26,419 hs"), "Activas: horas como magnitud secundaria");
  assertTrue(tile("Prospectos").includes(">51<") && tile("Prospectos").includes("24,544 hs"), "Prospectos: count + horas");
  assertTrue(tile("Ganadas").includes(">30<") && tile("Ganadas").includes("11,432 hs"), "Ganadas: count + horas");
  assertTrue(tile("Perdidas").includes(">29<") && tile("Perdidas").includes("13,513 hs"), "Perdidas: count + horas");
  assertTrue(tile("Suspendidas").includes(">8<") && tile("Suspendidas").includes("4,260 hs"), "Suspendidas: count + horas");
  // Pipeline: la unidad va en la línea secundaria — pegada al valor
  // desbordaba el tile, que es `white-space:nowrap`.
  assertTrue(tile("Pipeline total").includes(">50,963<"), "Pipeline total: horas como valor");
  assertTrue(tile("Pipeline total").includes(">horas<"), "Pipeline total: unidad en la línea secundaria");
  assertTrue(!/>50,963 hs</.test(html), "la unidad NUNCA va pegada al valor principal");
  assertTrue(tile("Pipeline ponderado").includes(">13,815<"), "Pipeline ponderado: horas");
  // Conversión conserva SUS DOS lecturas, como el legacy.
  assertTrue(tile("Conversión").includes("50.8%"), "Conversión: sin suspendidas");

  // Creadas y Conversión: su lectura secundaria usa el MISMO formato que la
  // composición por segmento (owner smoke) — `.kpi-split__item`, no `.kpi-sub`
  // en mono/600, que rompía la homogeneidad de la grilla.
  assertTrue(tile("Creadas esta semana").includes(">0<"), "Creadas: esta semana");
  assertTrue(tile("Creadas esta semana").includes("Semana Anterior <b>1</b>"), "Creadas: semana anterior");
  assertTrue(!tile("Creadas esta semana").includes("kpi-sub"), "y ya no como `.kpi-sub`");
  assertTrue(!/la semana pasada/.test(html), "el copy viejo no queda en ningún lado");
  assertTrue(tile("Conversión").includes("Incluyendo suspendidas <b>44.8%</b>"), "Conversión: incluyendo suspendidas, mismo formato");
  assertTrue(!tile("Conversión").includes("kpi-sub"), "y tampoco como `.kpi-sub`");
  // Las dos líneas nuevas NO son foco-ables: no tienen tooltip detrás, a
  // diferencia de NN/BI. Se cuenta por el ancla que distingue cada familia —
  // el marcador de anchor lo emite sólo el desglose por segmento, que sí tiene
  // tooltip; el modificador de pares, sólo estas dos líneas.
  assertTrue(count(html, "kpi-split chart-tooltip-anchor") === 6,
    "sólo los seis desgloses por segmento son foco-ables");
  assertTrue(count(html, "kpi-split kpi-split--pairs") === 2,
    "y las dos líneas nuevas reusan la caja, sin foco ni tooltip");
}

// --- Analíticas: valor + subtítulo ------------------------------------------
{
  const html = load();
  assertTrue(html.includes("219.8d") && html.includes("40 oportunidad(es) activa(s)"), "Edad del pipeline");
  assertTrue(html.includes("22.8d") && html.includes("8 etapa(s) con datos"), "Tiempo por etapa");
  assertTrue(html.includes("33.3%") && html.includes("cuello de botella"), "Conversión etapa a etapa");
  assertTrue(html.includes("112.5d") && html.includes("30 oportunidad(es) ganada(s)"), "Tiempo de cierre");
  assertTrue(html.includes(">19<") && html.includes("+30 días sin movimiento"), "Estancadas");
  assertTrue(html.includes("5.3%") && html.includes("3.9% avanzó a oportunidad"), "Conversión de leads");
}

// --- Sin actionability falsa -------------------------------------------------
{
  const html = load();
  const tile = label => {
    const i = html.indexOf(`>${label}<`);
    const start = html.lastIndexOf('<div class="activity-kpi', i);
    return html.slice(start, html.indexOf("</div>", i));
  };
  // C6 (ver HANDOFF) + Creadas Legacy Parity + Commercial Tile 5 + Commercial
  // Tile 6 + Commercial Tile 7 + Commercial Tile 8 + Analítica del Pipeline
  // (ver HANDOFF, incrementos POSTERIORES): CATORCE tiles clickeables sobre
  // TRECE análisis. "Pipeline total" y "Pipeline ponderado" comparten
  // `data-event="forecast"` a propósito — misma población (0-8) medida con y
  // sin probabilidad, y el panel muestra las dos series juntas. "Creadas
  // esta semana", "Prospectos", "Ganadas", "Perdidas", "Suspendidas" y las
  // SEIS de Analítica del Pipeline abren cada uno su propio drill (paridad
  // con el legacy `/crm/opp-by-month`, `/crm/relevamiento?stage_id=<etapa
  // 0>`, `/crm/kpi-detail` buckets "won"/"lost",
  // `/crm/relevamiento?stage_ids=<suspended>` y
  // `setAnalyticsView`/`renderAnalyticsPanel*`/`/lead-conversion`
  // respectivamente). El único tile restante ("Conversión") sigue sin
  // fingir una acción inexistente, que es lo que este bloque protege.
  assertTrue(count(html, "activity-kpi--clickable") === 14, "exactamente catorce tiles clickeables");
  assertTrue(count(html, 'data-event="activas"') === 1, "uno es Oportunidades activas");
  assertTrue(count(html, 'data-event="forecast"') === 2, "y dos son los Pipeline, ambos al MISMO análisis");
  assertTrue(count(html, 'data-event="creadas"') === 1, "y uno es Creadas esta semana");
  assertTrue(count(html, 'data-event="prospectos"') === 1, "y uno es Prospectos");
  assertTrue(count(html, 'data-event="ganadas"') === 1, "y uno es Ganadas");
  assertTrue(count(html, 'data-event="perdidas"') === 1, "y uno es Perdidas");
  assertTrue(count(html, 'data-event="suspendidas"') === 1, "y uno es Suspendidas");
  assertTrue(count(html, 'data-event="pipeline_age"') === 1, "y uno es Edad del pipeline");
  assertTrue(count(html, 'data-event="stage_time"') === 1, "y uno es Tiempo por etapa");
  assertTrue(count(html, 'data-event="stage_conversion"') === 1, "y uno es Conversión etapa a etapa");
  assertTrue(count(html, 'data-event="close_time"') === 1, "y uno es Tiempo de cierre");
  assertTrue(count(html, 'data-event="stagnant"') === 1, "y uno es Estancadas");
  assertTrue(count(html, 'data-event="lead_conversion"') === 1, "y uno es Conversión de leads");
  // Ganadas/Perdidas/Suspendidas/Analítica del Pipeline empiezan "idle"
  // (LAZY, ver HANDOFF): sus paneles muestran el loader canónico, nunca el
  // ranking/tabla (que agregarían sus propios `role="button"`) — el
  // landing en frío sigue teniendo exactamente catorce.
  assertTrue(count(html, 'role="button"') === 14, "ningún otro tile finge ser accionable");
  // Los DOS triggers de forecast son los tiles de Pipeline, no otros.
  assertTrue(tile("Pipeline total").includes('data-event="forecast"'), "Pipeline total abre Forecast");
  assertTrue(tile("Pipeline ponderado").includes('data-event="forecast"'), "Pipeline ponderado abre el mismo Forecast");
  assertTrue(tile("Creadas esta semana").includes('data-event="creadas"'), "Creadas esta semana abre su propio drill");
  assertTrue(tile("Prospectos").includes('data-event="prospectos"'), "Prospectos abre su propio drill");
  assertTrue(tile("Ganadas").includes('data-event="ganadas"'), "Ganadas abre su propio drill");
  assertTrue(tile("Perdidas").includes('data-event="perdidas"'), "Perdidas abre su propio drill");
  assertTrue(tile("Suspendidas").includes('data-event="suspendidas"'), "Suspendidas abre su propio drill");
  assertTrue(tile("Edad del pipeline").includes('data-event="pipeline_age"'), "Edad del pipeline abre su propio drill");
  assertTrue(tile("Tiempo por etapa").includes('data-event="stage_time"'), "Tiempo por etapa abre su propio drill");
  assertTrue(tile("Conversión etapa a etapa").includes('data-event="stage_conversion"'), "Conversión etapa a etapa abre su propio drill");
  assertTrue(tile("Tiempo de cierre").includes('data-event="close_time"'), "Tiempo de cierre abre su propio drill");
  assertTrue(tile("Estancadas").includes('data-event="stagnant"'), "Estancadas abre su propio drill");
  assertTrue(tile("Conversión de leads").includes('data-event="lead_conversion"'), "Conversión de leads abre su propio drill");
  for (const label of ["Conversión"]) {
    assertTrue(!tile(label).includes("data-event="), `${label} sigue siendo informativo`);
  }
}

// --- Composición NN/BI honesta ----------------------------------------------
{
  const html = load();
  assertTrue(count(html, "kpi-split chart-tooltip-anchor") === 6, "seis tiles con composición por segmento");
  const split = commercialSplitHtml(SEGMENTS.activas, SUMMARY.labels);
  assertTrue(split.includes("New Name <b>19</b>"), "lectura visible: New Name");
  assertTrue(split.includes("Base Instalada <b>20</b>"), "lectura visible: Base Instalada");
  // La explicación honesta va en el tooltip, no como tercer número inventado.
  assertTrue(split.includes("Con ambos segmentos: 2"), "el solapamiento se declara");
  assertTrue(split.includes("Sin segmento asignado: 3"), "el hueco se declara");
  assertTrue(split.includes("no suman el total"), "se dice explícitamente que no es una partición");
  assertTrue(!/Otros/.test(split), "NUNCA un resto derivado total - nn - bi");
  assertTrue(split.includes('tabindex="0"'), "accesible por teclado, no sólo hover");
  assertTrue(split.includes("chart-tooltip"), "usa el tooltip compartido del Hub");
  // 19 + 20 no da 40: es justamente lo que el tooltip explica.
  assertTrue(SEGMENTS.activas.nn.count + SEGMENTS.activas.bi.count !== 40, "el fixture refleja la no reconciliación real");
}

// --- Filtrado por segmento: sin desglose redundante --------------------------
{
  const filtered = JSON.parse(JSON.stringify(SUMMARY));
  delete filtered.segments;  // el service no manda `segments` fuera de "todos"
  const html = load({ summary: filtered });
  assertTrue(count(html, "kpi-split chart-tooltip-anchor") === 0, "en New Name / Base Instalada no se repite el desglose");
  assertTrue(tilesOf(html) === 15, "pero siguen estando los 15 tiles");
}

// --- El análisis contextual no ocupa espacio hasta accionarlo ---------------
{
  const html = load();
  assertTrue(html.includes('data-breakdown="activas" open hidden'), "la sección entera arranca oculta");
  // C6: un SEGUNDO panel de análisis, también oculto de entrada, en el MISMO
  // grupo de breakdown (de ahí que el motor compartido muestre uno por vez).
  assertTrue(html.includes('data-breakdown="forecast" open hidden'), "el panel de Forecast también arranca oculto");
  // Creadas Legacy Parity (ver HANDOFF, incremento POSTERIOR): un TERCER
  // panel, MISMO grupo, MISMO criterio.
  assertTrue(html.includes('data-breakdown="creadas" open hidden'), "el panel de Creadas también arranca oculto");
  // Commercial Tile 5 — Prospectos (ver HANDOFF, incremento POSTERIOR): un
  // CUARTO panel, MISMO grupo, MISMO criterio.
  assertTrue(html.includes('data-breakdown="prospectos" open hidden'), "el panel de Prospectos también arranca oculto");
  // Commercial Tile 6 — Ganadas (ver HANDOFF, incremento POSTERIOR): un
  // QUINTO panel, MISMO grupo, MISMO criterio.
  assertTrue(html.includes('data-breakdown="ganadas" open hidden'), "el panel de Ganadas también arranca oculto");
  // Commercial Tile 7 — Perdidas (ver HANDOFF, incremento POSTERIOR): un
  // SEXTO panel, MISMO grupo, MISMO criterio.
  assertTrue(html.includes('data-breakdown="perdidas" open hidden'), "el panel de Perdidas también arranca oculto");
  // Commercial Tile 8 — Suspendidas (ver HANDOFF, incremento POSTERIOR): un
  // SÉPTIMO y ÚLTIMO panel, MISMO grupo, MISMO criterio.
  assertTrue(html.includes('data-breakdown="suspendidas" open hidden'), "el panel de Suspendidas también arranca oculto");
  // Analítica del Pipeline (ver HANDOFF, incremento POSTERIOR): las SEIS
  // últimas secciones, MISMO grupo, MISMO criterio.
  for (const key of ["pipeline_age", "stage_time", "stage_conversion", "close_time", "stagnant", "lead_conversion"]) {
    assertTrue(html.includes(`data-breakdown="${key}" open hidden`), `el panel de ${key} también arranca oculto`);
  }
  assertTrue(count(html, "data-breakdown=") === 13, "exactamente trece contratos de breakdown, sin panel placeholder");
  assertTrue(count(html, "data-breakdown-group") === 1, "y UN solo grupo — nunca un lifecycle paralelo");
  assertTrue(!/Hacé clic en/.test(html), "sin texto placeholder en el landing");
  // Y cuando se muestra, adentro está el Funnel (grouped bars, C3) intacto.
  assertTrue(html.includes("category-plot"), "el Funnel sigue siendo el gráfico");
  assertTrue(html.includes("Funnel por Etapa"), "con su rótulo");
  assertTrue(html.includes("Detalle de Oportunidades"), "y la tabla debajo (C4: detalle real por oportunidad)");
}

// --- Analíticas degradan solas ----------------------------------------------
{
  const html = load({ analytics: null });
  assertTrue(tilesOf(html) === 9, "sin analíticas quedan los 9 principales");
  assertTrue(html.includes("No pudimos calcular las analíticas"), "el bloque avisa en vez de desaparecer");
  assertTrue(html.includes(">—<"), "Creadas degrada a guion, no rompe");
}

// --- Escaping de datos de origen Odoo ---------------------------------------
{
  const nasty = JSON.parse(JSON.stringify(SUMMARY));
  nasty.labels = { nn: '<img src=x onerror="alert(1)">', bi: "<b>BI</b>" };
  const html = load({ summary: nasty });
  assertTrue(!html.includes("<img") && !html.includes("<b>BI</b>"), "las etiquetas de tag se escapan");
  assertTrue(html.includes("&lt;img"), "escapado, no removido");
}

// --- El tile es el primitivo compartido, no uno propio ----------------------
{
  const plain = commercialRichKpi("X", "1");
  assertTrue(plain.startsWith('<div class="activity-kpi">'), "sin eventKey: tile display-only");
  assertTrue(!plain.includes("role=") && !plain.includes("tabindex"), "sin afordancia de click");
  const clickable = commercialRichKpi("X", "1", { eventKey: "k" });
  assertTrue(clickable.includes("activity-kpi--clickable"), "con eventKey: modificador COMPARTIDO");
  assertTrue(clickable.includes('data-event="k"'), "y el contrato del motor compartido");
}

// --- "Reintentar" en el estado de no-disponible (owner request) -------------
{
  const errorHtml = errorState => {
    commercialState.status = "error";
    commercialState.errorState = errorState;
    const html = commercialDashboardHtml();
    commercialState.status = "ready";
    commercialState.errorState = "";
    return html;
  };

  // `source_error` = falla de transporte contra Odoo: la próxima request puede
  // resolverla, así que el botón tiene sentido.
  const transport = errorHtml("source_error");
  assertTrue(transport.includes("dashboard-empty"), "sigue siendo el primitivo de no-disponible del Hub");
  assertTrue(/No pudimos cargar los datos comerciales/.test(transport), "con el mismo mensaje de siempre");
  // El hook lleva la KEY de lo que reintenta (corrective, ver HANDOFF): el
  // primitivo visual es compartido con el estado de error del Forecast, pero
  // el callback NO. Sin el discriminador los dos caían en la misma rama y el
  // del Forecast recargaba el Dashboard entero.
  assertTrue(transport.includes('data-commercial-retry="dashboard"'),
    "y ahora ofrece reintentar, declarando QUÉ reintenta");
  assertTrue(transport.includes(">Reintentar</button>"), "con copy explícito");
  assertTrue(transport.includes('class="dashboard-subhead__reset"'),
    "reusa el primitivo compartido de acción-dentro-de-estado-vacío, sin clase propia de Comercial");
  assertTrue(transport.includes('<button type="button" class="dashboard-subhead__reset" data-commercial-retry="dashboard"'),
    "botón real (foco-able por default), nunca un span/div con handler");
  assertTrue(transport.includes("aria-label="), "y con nombre accesible propio, no sólo 'Reintentar'");
  // El botón vive DENTRO del `<p>`, como el único precedente del Hub.
  assertTrue(transport.indexOf("data-commercial-retry") < transport.indexOf("</p>"),
    "dentro del párrafo del mensaje, no como bloque suelto debajo");

  // `config_incomplete` = falta configuración del espacio: reintentar daría el
  // MISMO resultado siempre, así que no se ofrece.
  const misconfigured = errorHtml("config_incomplete");
  assertTrue(/todavía no está configurado/.test(misconfigured), "el mensaje de configuración no cambia");
  assertTrue(!misconfigured.includes("data-commercial-retry"),
    "y NO ofrece reintentar: sería una promesa falsa, el resultado no puede cambiar");

  // Ningún otro estado gana el botón.
  commercialState.status = "loading";
  assertTrue(!commercialDashboardHtml().includes("data-commercial-retry"), "mientras carga, no");
  commercialState.status = "ready";
  assertTrue(!commercialDashboardHtml().includes("data-commercial-retry"), "y con datos, tampoco");

  // --- Comportamiento REAL: se ejercita el delegado de click de commercial.js
  const fetches = [];
  sandbox.fetch = url => { fetches.push(url); return Promise.resolve({ json: () => Promise.resolve({ state: "source_error" }) }); };
  // Root mínimo: `DOMContentLoaded` sale temprano si no encuentra
  // `#commercial-main-content`, así que sin esto no se cablearía ningún
  // delegado. `innerHTML` absorbe los re-renders sin necesidad de un DOM real.
  sandbox.document.getElementById = () => ({ innerHTML: "" });
  (listeners.DOMContentLoaded || []).forEach(cb => cb());
  const clickDelegates = listeners.click || [];
  assertTrue(clickDelegates.length === 1, "un solo delegado de click en todo commercial.js");
  const fire = () => clickDelegates.forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-retry]" ? { dataset: { commercialRetry: "dashboard" } } : null) },
    preventDefault() {}, stopPropagation() {},
  }));

  commercialState.status = "error";
  commercialState.errorState = "source_error";
  fetches.length = 0;
  fire();
  assertTrue(fetches.length === 4,
    "reintentar vuelve a pedir el dataset del Dashboard por el MISMO camino (los cuatro endpoints del load inicial)");
  assertTrue(!fetches.some(u => u.includes("/forecast")),
    "y sigue sin pedir el forecast: el lazy no se altera");
  assertTrue(commercialState.status === "loading", "el estado pasa a carga, no queda pegado en error");

  // Doble click no apila requests.
  fetches.length = 0;
  fire();
  assertTrue(fetches.length === 0, "un segundo click mientras carga no apila otra tanda de requests");

  commercialState.status = "ready";
  commercialState.errorState = "";
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL LANDING INVARIANTS PASSED");
