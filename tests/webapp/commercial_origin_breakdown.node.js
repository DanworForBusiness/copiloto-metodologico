// commercial_origin_breakdown.node.js — invocado por
// test_commercial_origin_breakdown.py. C8 (ver HANDOFF): "Oportunidades por
// Origen" dentro del análisis de Oportunidades Activas — breakdown
// FRONTEND-ONLY derivado del payload que ya está en memoria.
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures y, para el lifecycle
// de filtros, contra los delegados REALES sobre un DOM mínimo — MISMA técnica
// que el resto de los harnesses de Comercial.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

// Los listeners se guardan para poder ejercitar los delegados reales; los
// hosts falsos permiten ver QUÉ bloque se re-renderizó con cada interacción.
const listeners = Object.create(null);
const fetchCalls = [];
const hosts = Object.create(null);
const root = {
  innerHTML: "<!--root-intacto-->",
  querySelector(sel) { return hosts[sel] || null; },
};
const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById() { return root; },
    querySelector() { throw new Error("commercial.js nunca debe usar document.querySelector"); },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch(url) { fetchCalls.push(url); return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialDeriveOrigins, commercialOriginsHtml, commercialFunnelAreaHtml,
  commercialCurrentAnalysisFilteredOpportunities, commercialCurrentTableRows,
  commercialActivasAnalysisHtml, commercialDistribucionHtml,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
const sources = rows => rows.map(r => r.source);
const cells = html => [...html.matchAll(/row-label-col">([^<]*)</g)].map(m => m[1]);

function opp(source, stage, seller, name) {
  return { opportunity: name || `Opp ${source}-${stage}-${seller}`, client: "Cliente", seller,
    stage, source, hours: 10, weighted_hours: 4, revenue: 1000, probability: 40, priority: "0",
    licencias: null, activities: [], last_response: null, days_elapsed: null };
}
// 10 oportunidades: 4 SAP / 3 Referido / 2 Evento / 1 sin origen.
const OPPS = [
  opp("SAP Website", "1 Contacto", "Ana"), opp("SAP Website", "1 Contacto", "Beto"),
  opp("SAP Website", "2 Relevamiento", "Ana"), opp("SAP Website", "2 Relevamiento", "Beto"),
  opp("Referido", "1 Contacto", "Ana"), opp("Referido", "2 Relevamiento", "Beto"),
  opp("Referido", "2 Relevamiento", "Ana", "Loma Negra - Mantenimiento"),
  opp("Evento", "1 Contacto", "Beto"), opp("Evento", "2 Relevamiento", "Ana"),
  opp(null, "1 Contacto", "Ana"),
];
const PIPELINE = { opportunities: OPPS.length, stages: [
  { stage: "1 Contacto", count: 5, hours: 50, weighted_hours: 20, revenue: 5000 },
  { stage: "2 Relevamiento", count: 5, hours: 50, weighted_hours: 20, revenue: 5000 },
] };

function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.pipeline = PIPELINE;
  commercialState.opportunities = { opportunities: OPPS, total: OPPS.length };
  commercialState.oppStageFilter = "";
  commercialState.oppSellerFilter = "";
  commercialState.oppSearchText = "";
  Object.assign(commercialState, patch || {});
}

// ═══ DATA ═══════════════════════════════════════════════════════════════════

// --- 1/2/3: agrupa por `source`, con "Sin definir" para vacío/nulo ---------
{
  const rows = commercialDeriveOrigins(OPPS);
  assertTrue(rows.length === 4, `cuatro orígenes distintos (got ${rows.length})`);
  const byName = Object.fromEntries(rows.map(r => [r.source, r.count]));
  assertTrue(byName["SAP Website"] === 4, "SAP Website: 4");
  assertTrue(byName["Referido"] === 3, "Referido: 3");
  assertTrue(byName["Evento"] === 2, "Evento: 2");
  assertTrue(byName["Sin definir"] === 1, "`source` nulo cae en 'Sin definir'");
  // Blanco y sólo-espacios también, no sólo `null`: el legacy usaba el mismo
  // fallback y un `""` que pasara como origen propio sería una categoría falsa.
  const blancos = commercialDeriveOrigins([opp("", "1 Contacto", "Ana"), opp("   ", "1 Contacto", "Ana"),
                                           opp(undefined, "1 Contacto", "Ana")]);
  assertTrue(blancos.length === 1 && blancos[0].source === "Sin definir" && blancos[0].count === 3,
    "cadena vacía, espacios y undefined caen todos en 'Sin definir'");
}

// --- 4/5: pct sobre el TOTAL del subset, y cierra en 100 -------------------
{
  const rows = commercialDeriveOrigins(OPPS);
  const pct = Object.fromEntries(rows.map(r => [r.source, r.pct]));
  assertTrue(Math.abs(pct["SAP Website"] - 40) < 0.01, `SAP Website = 4/10 = 40% (got ${pct["SAP Website"]})`);
  assertTrue(Math.abs(pct["Referido"] - 30) < 0.01, "Referido = 3/10 = 30%");
  assertTrue(Math.abs(pct["Evento"] - 20) < 0.01, "Evento = 2/10 = 20%");
  assertTrue(Math.abs(pct["Sin definir"] - 10) < 0.01, "Sin definir = 1/10 = 10%");
  assertTrue(Math.abs(rows.reduce((a, r) => a + r.pct, 0) - 100) < 0.01,
    "los porcentajes suman 100: el denominador es el total del subset, no el máximo");
  // Y con un subset FILTRADO el denominador es el del subset, no el global.
  const soloAna = OPPS.filter(o => o.seller === "Ana");
  const anaRows = commercialDeriveOrigins(soloAna);
  assertTrue(Math.abs(anaRows.reduce((a, r) => a + r.pct, 0) - 100) < 0.01,
    "sobre un subset filtrado los porcentajes también cierran en 100");
  // Derivado del fixture, no un número a mano: el denominador es el del subset.
  const anaSap = soloAna.filter(o => o.source === "SAP Website").length;
  assertTrue(Math.abs(anaRows.find(r => r.source === "SAP Website").pct - (anaSap / soloAna.length) * 100) < 0.01,
    `SAP Website de Ana es ${anaSap}/${soloAna.length} del subset, nunca sobre el total global`);
  assertTrue(Math.abs(anaRows.find(r => r.source === "SAP Website").pct
    - (anaSap / OPPS.length) * 100) > 1,
    "y ese número es DISTINTO del que daría el total global — la prueba no es vacua");
}

// --- 6: orden count DESC + source ASC como desempate estable ---------------
{
  assertTrue(sources(commercialDeriveOrigins(OPPS)).join("|") === "SAP Website|Referido|Evento|Sin definir",
    "orden por conteo descendente");
  const empate = [opp("Zeta", "1 Contacto", "Ana"), opp("Alfa", "1 Contacto", "Ana"),
                  opp("Medio", "1 Contacto", "Ana")];
  assertTrue(sources(commercialDeriveOrigins(empate)).join("|") === "Alfa|Medio|Zeta",
    "con el mismo conteo desempata alfabéticamente — orden estable entre renders");
  // Y el desempate no rompe el criterio primario.
  const mixto = [...empate, opp("Zeta", "1 Contacto", "Beto")];
  assertTrue(sources(commercialDeriveOrigins(mixto))[0] === "Zeta", "el conteo sigue mandando sobre el alfabético");
}

// --- 7: no muta la entrada -------------------------------------------------
{
  const snapshot = JSON.stringify(OPPS);
  const arr = OPPS.slice();
  commercialDeriveOrigins(arr);
  commercialOriginsHtml(arr);
  assertTrue(JSON.stringify(OPPS) === snapshot, "el payload fuente queda intacto");
  assertTrue(arr.length === OPPS.length && arr[0] === OPPS[0], "y el array recibido tampoco se reordena");
  assertTrue(commercialOriginsHtml(undefined) === "" && commercialOriginsHtml(null) === "",
    "entrada ausente no rompe ni produce NaN");
}

// ═══ FILTER COMPOSITION ═════════════════════════════════════════════════════
// Origen se deriva del MISMO subset analítico que el Funnel: el estado
// vigente → Etapa → Vendedor. La búsqueda queda AFUERA, es sólo de tabla.

// --- 8: Etapa afecta Origen ------------------------------------------------
{
  resetState({ oppStageFilter: "1 Contacto" });
  const rows = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  assertTrue(rows.reduce((a, r) => a + r.count, 0) === 5, "Etapa recorta el universo de Origen");
  assertTrue(Object.fromEntries(rows.map(r => [r.source, r.count]))["SAP Website"] === 2,
    "y recalcula los conteos por origen");
  assertTrue(Math.abs(rows.reduce((a, r) => a + r.pct, 0) - 100) < 0.01, "con su propio denominador");
}

// --- 9: Vendedor afecta Origen --------------------------------------------
{
  resetState({ oppSellerFilter: "Beto" });
  const rows = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  assertTrue(rows.reduce((a, r) => a + r.count, 0) === 4, "Vendedor recorta el universo de Origen");
  assertTrue(!sources(rows).includes("Sin definir"),
    "y un origen que se queda sin oportunidades desaparece de la tabla");
}

// --- 10: Etapa + Vendedor componen (AND) ----------------------------------
{
  resetState({ oppStageFilter: "2 Relevamiento", oppSellerFilter: "Ana" });
  const rows = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  assertTrue(rows.reduce((a, r) => a + r.count, 0) === 3, "los dos filtros se combinan, no se reemplazan");
  assertTrue(sources(rows).sort().join("|") === "Evento|Referido|SAP Website", "sobre el cruce exacto");
}

// --- 11/12: la BÚSQUEDA no entra en Origen, pero sí en la tabla -----------
{
  resetState();
  const sinBuscar = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  const filasSinBuscar = commercialCurrentTableRows().length;
  resetState({ oppSearchText: "Loma Negra" });
  const conBuscar = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  assertTrue(JSON.stringify(conBuscar) === JSON.stringify(sinBuscar),
    "la búsqueda NO cambia Origen: sale del subset previo a Search");
  assertTrue(commercialCurrentTableRows().length === 1 && filasSinBuscar === OPPS.length,
    "pero sí sigue filtrando la tabla (10 → 1)");
  // Y el bloque renderizado tampoco cambia.
  resetState();
  const htmlSin = commercialOriginsHtml(commercialCurrentAnalysisFilteredOpportunities());
  resetState({ oppSearchText: "Loma Negra" });
  assertTrue(commercialOriginsHtml(commercialCurrentAnalysisFilteredOpportunities()) === htmlSin,
    "el HTML de Origen es idéntico con y sin búsqueda");
}

// --- 13: "Limpiar" devuelve Origen al universo Etapa/Vendedor completo ----
//     Se ejercita el delegado REAL de click (el mismo que resetea los filtros).
{
  hosts["[data-commercial-funnel-area]"] = { innerHTML: "" };
  hosts["[data-commercial-opportunities]"] = { innerHTML: "" };
  const bar = { querySelector: () => ({ value: "SUCIO" }) };
  const clearBtn = { closest: sel => (sel === "[data-commercial-analysis-filters]" ? bar : null) };
  (listeners.DOMContentLoaded || []).forEach(cb => cb());
  const fire = () => (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-clear-filters]" ? clearBtn : null) },
    preventDefault() {}, stopPropagation() {},
  }));

  resetState({ oppStageFilter: "1 Contacto", oppSellerFilter: "Ana", oppSearchText: "Loma" });
  fetchCalls.length = 0;
  root.innerHTML = "<!--root-intacto-->";
  fire();
  const rows = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  assertTrue(rows.reduce((a, r) => a + r.count, 0) === OPPS.length,
    "tras Limpiar, Origen vuelve al universo completo");
  assertTrue(sources(rows).join("|") === "SAP Website|Referido|Evento|Sin definir", "con su orden original");
  assertTrue(hosts["[data-commercial-funnel-area]"].innerHTML.includes("Oportunidades por Origen"),
    "y el bloque se re-renderizó junto con el Funnel, por el lifecycle local que ya existía");
  assertTrue(fetchCalls.length === 0, "sin una sola request: Origen es derivado puro");
  assertTrue(root.innerHTML === "<!--root-intacto-->", "y sin render completo de la página");
}

// --- 14: cero oportunidades por Etapa/Vendedor → Origen no dibuja nada ----
{
  resetState({ oppStageFilter: "9 Inexistente" });
  const subset = commercialCurrentAnalysisFilteredOpportunities();
  assertTrue(subset.length === 0, "el fixture deja el subset vacío");
  const html = commercialOriginsHtml(subset);
  assertTrue(html === "", "Origen desaparece — MISMO lenguaje que el Funnel hermano");
  assertTrue(!/NaN|Infinity/.test(html), "sin NaN");
  assertTrue(!html.includes("<th>"), "y nunca una tabla con sólo cabeceras");
  // El mensaje único lo da la tabla de detalle, como ya era el contrato.
  assertTrue(commercialActivasAnalysisHtml(PIPELINE).includes("dashboard-empty"),
    "el panel sigue teniendo exactamente un mensaje de vacío, el de la tabla");
}

// --- 15: búsqueda sin resultados NO vacía Origen --------------------------
{
  resetState({ oppSearchText: "zzzz-no-existe" });
  assertTrue(commercialCurrentTableRows().length === 0, "la búsqueda deja la tabla en cero");
  const rows = commercialDeriveOrigins(commercialCurrentAnalysisFilteredOpportunities());
  assertTrue(rows.reduce((a, r) => a + r.count, 0) === OPPS.length,
    "pero Origen queda INTACTO: la búsqueda es sólo de tabla");
  assertTrue(commercialOriginsHtml(commercialCurrentAnalysisFilteredOpportunities()).includes("SAP Website"),
    "y sigue dibujando sus filas");
}

// ═══ PRESENTATION ═══════════════════════════════════════════════════════════

// --- 16/18/19: cabeceras, porcentaje como TEXTO, "Sin definir" visible ----
{
  const html = commercialOriginsHtml(OPPS);
  // El regex acepta atributos en el `<th>`: la columna del rotulo lleva `.left`
  // desde el corrective de alineacion compartida. Lo que se prueba son los
  // ROTULOS y su orden, no si el `<th>` tiene clase.
  assertTrue([...html.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map(m => m[1]).join("|")
    === "Origen|Opps.|Distribución|% del total", "las cuatro columnas, con sus rótulos");
  assertTrue(html.includes("Oportunidades por Origen"), "y el bloque va rotulado");
  assertTrue(html.includes("perspective-label"), "con el primitivo de rótulo compartido");
  assertTrue(cells(html).join("|") === "SAP Website|Referido|Evento|Sin definir",
    "una fila por origen, en orden, con 'Sin definir' visible");
  // El porcentaje existe como TEXTO: la distribución no depende del color ni
  // del ancho de la barra.
  assertTrue(/>40%</.test(html) && />30%</.test(html) && /># *10%</.test(html) === false && />10%</.test(html),
    "los porcentajes están escritos, no sólo dibujados");
  assertTrue(count(html, "bar-track") === 4 && count(html, "bar-fill") === 4,
    "una barra por fila, con los primitivos COMPARTIDOS del Hub");
  assertTrue(!/forecast/i.test(html), "y sin acoplarse a ninguna clase de Forecast");
  assertTrue(html.includes("dashboard-table") && html.includes("matrix-scroll"),
    "tabla y scroll son los primitivos de siempre");
}

// --- 17: la barra representa el MISMO pct que el texto -------------------
{
  const html = commercialOriginsHtml(OPPS);
  const anchos = [...html.matchAll(/bar-fill" style="width:([\d.]+)%/g)].map(m => m[1]);
  assertTrue(anchos.join("|") === "40.0|30.0|20.0|10.0",
    `la barra es el % del total, no relativo al máximo (got ${anchos.join("|")})`);
  assertTrue(anchos[0] !== "100.0", "el origen mayor NO se dibuja al 100% sólo por ser el mayor");
  // Un único origen: 100% del subset, y ahí la barra sí llena.
  const solo = commercialOriginsHtml([opp("Único", "1 Contacto", "Ana")]);
  assertTrue(/width:100.0%/.test(solo) && />100%</.test(solo), "con un solo origen, 100% de texto y de barra");
}

// --- 20/21: CERO afordancia de drill (eso es un incremento aparte) --------
{
  const html = commercialOriginsHtml(OPPS);
  for (const banned of ["onclick", "data-source", "data-commercial-origin", "cursor:", 'role="button"', "tabindex"]) {
    assertTrue(!html.includes(banned), `sin ${banned}`);
  }
  assertTrue(!/data-event=|data-breakdown=/.test(html),
    "Origen NO crea un panel ni un breakdown propio: vive dentro del análisis de Activas");
  // La barra es decorativa: el lector no la anuncia dos veces.
  assertTrue(html.includes('class="bar-track" aria-hidden="true"'),
    "la barra queda aria-hidden — la magnitud ya está en la columna de %");
}

// --- Ubicación: entre el Funnel y el Detalle -----------------------------
{
  resetState();
  const area = commercialFunnelAreaHtml(commercialDistribucionHtml(PIPELINE), OPPS);
  const iDist = area.indexOf("data-commercial-dist");
  const iOrigen = area.indexOf("Oportunidades por Origen");
  const iDetalle = area.indexOf("Detalle de Oportunidades");
  assertTrue(iDist >= 0 && iOrigen > iDist && iDetalle > iOrigen,
    "orden en el panel: Funnel → Origen → Detalle");
  // C8 visual corrective: Funnel y Origen van LADO A LADO, en la composición
  // que ya usaba Forecast — misma figura (gráfico de alto fijo | tabla que
  // crece), nombre neutro porque ahora tiene dos consumidores.
  assertTrue(area.includes('class="analysis-split analysis-split--top"'),
    "el análisis usa la composición de dos columnas, con arranque alineado");
  assertTrue(area.indexOf("analysis-split__chart") < area.indexOf("analysis-split__table"),
    "Funnel a la izquierda, Origen a la derecha");
  assertTrue(area.split("analysis-split__chart", 2)[1].split("</div>", 2)[0].includes("data-commercial-dist"),
    "el Funnel vive en la columna izquierda");
  assertTrue(area.split("analysis-split__table", 2)[1].includes("Oportunidades por Origen"),
    "y Origen en la derecha");
  // El Detalle queda FUERA de las dos columnas: análisis arriba, detalle abajo.
  assertTrue(iDetalle > area.indexOf("</div>", area.indexOf("analysis-split__table")),
    "el rótulo del Detalle está fuera del split, a ancho completo");
  assertTrue(!area.split("analysis-split", 2)[1].split("Detalle de Oportunidades", 2)[0].includes("data-commercial-opportunities"),
    "y la tabla de detalle nunca queda dentro de una columna");
  // Sin Origen el Funnel va a ancho completo, no media grilla vacía.
  resetState({ oppStageFilter: "" });
  const soloFunnel = commercialFunnelAreaHtml(commercialDistribucionHtml(PIPELINE), []);
  assertTrue(!soloFunnel.includes("analysis-split") && soloFunnel.includes("data-commercial-dist"),
    "sin Origen no se abre la grilla: el Funnel toma el ancho completo");
  // Sin Funnel (sin etapas) el área entera desaparece, como antes de C8.
  assertTrue(commercialFunnelAreaHtml("", OPPS) === "",
    "sin Funnel el área queda vacía — nunca un rótulo huérfano");
  // Y el panel completo lo compone igual.
  const panel = commercialActivasAnalysisHtml(PIPELINE);
  assertTrue(panel.indexOf("Oportunidades por Origen") > panel.indexOf("data-commercial-dist")
    && panel.indexOf("Detalle de Oportunidades") > panel.indexOf("Oportunidades por Origen"),
    "el render completo respeta el mismo orden");
}

// ═══ NETWORK / OWNERSHIP / NO REGRESSION ════════════════════════════════════
{
  // 22: Origen no introduce endpoint ni fetch.
  const fn = src.split("function commercialDeriveOrigins(", 2)[1].split("\n}", 2)[0]
    + src.split("function commercialOriginsHtml(", 2)[1].split("\n}", 2)[0];
  assertTrue(!/fetch\(|\/comercial\/data\//.test(fn), "cero fetch y cero endpoint en la derivación/render");
  assertTrue(!/commercialState\./.test(src.split("function commercialDeriveOrigins(", 2)[1].split("\n}", 2)[0]),
    "la derivación es pura: recibe el subset, no lee estado");
  // 23/24/25 se verifican del lado de pytest (archivos intactos).
  // Origen no agrega estado al workspace.
  const state = src.split("const commercialState = {", 2)[1].split("\n};", 2)[0];
  // `\b` antes de "origin"/"origen" — la palabra como IDENTIFICADOR propio,
  // no como substring embebido en otro campo ya legítimo (owner corrective
  // de Conversión de Leads, ver HANDOFF §4, agregó
  // `leadConversionOrigenFilter` — "origen" de atribución UTM, NUNCA el
  // "origen" de Origin/C8 que esta guarda vigila).
  for (const banned of [/\borigin/i, /\borigen/i, /selectedOrigin/i, /originOpenedBy/i]) {
    assertTrue(!banned.test(state), `el estado no gana \`${banned}\``);
  }

  // 26: el Funnel sigue saliendo del MISMO subset analítico.
  resetState({ oppStageFilter: "1 Contacto" });
  const subset = commercialCurrentAnalysisFilteredOpportunities();
  const origenTotal = commercialDeriveOrigins(subset).reduce((a, r) => a + r.count, 0);
  assertTrue(origenTotal === subset.length,
    "Funnel y Origen cuentan exactamente el mismo universo");
  // 27: la búsqueda sigue siendo table-only (ya cubierto arriba, se re-afirma
  // sobre el helper que arma la tabla).
  resetState({ oppSearchText: "Loma" });
  assertTrue(commercialCurrentTableRows().length < commercialCurrentAnalysisFilteredOpportunities().length,
    "la tabla se recorta y el subset analítico no");
  resetState();
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL ORIGIN BREAKDOWN INVARIANTS PASSED");
