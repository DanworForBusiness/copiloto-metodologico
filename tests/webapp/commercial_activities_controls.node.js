// commercial_activities_controls.node.js — invocado por
// test_commercial_activities_controls.py. C9 (ver HANDOFF): la capability
// Actividades adopta los tres comportamientos TRANSVERSALES que ya tenía el
// detalle de Oportunidades Activas — búsqueda de texto libre + "✕ Limpiar",
// badges tonales de urgencia, y orden por columna.
//
// Ejecuta el MOTOR REAL de commercial.js y, para el lifecycle, los delegados
// REALES sobre un DOM mínimo — MISMA técnica que el resto de los harnesses.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const listeners = Object.create(null);
const fetchCalls = [];
const hosts = Object.create(null);
const root = { innerHTML: "<!--root-intacto-->", querySelector: sel => hosts[sel] || null };
const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById(id) { return id === "commercial-main-content" ? root : null; },
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
  commercialActivitiesBodyHtml, commercialActivitiesControlsHtml, commercialActividadesHtml,
  commercialActivityTableHtml, commercialActivityBuckets, commercialFilteredActivities,
  commercialActivitySearchableText, commercialActivitiesSorted, commercialToggleActSort,
  commercialUrgencyColor,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;
const kpis = html => (html.match(/activity-kpi__value">(\d+)</g) || []).map(m => m.match(/>(\d+)</)[1]);

function isoOffset(days) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function act(type, opportunity, client, user, days, summary) {
  return { type, opportunity, client, user, deadline: days === null ? null : isoOffset(days),
    summary: summary || "", note: "" };
}
// 6 actividades: 3 vencidas (-40/-10/-2) y 3 por vencer (3/12/25) — cubre los
// cuatro tonos de la escala en ambos signos.
const ACTS = [
  act("To-Do", "Loma Negra - Mantenimiento", "Loma Negra SA", "Ana", -40, "Llamar a compras"),
  act("Email", "Farma Plus - BDC", "FarmaPlus", "Beto", -10, ""),
  act("Reunión", "Southbay - Implementacion", "Southbay SRL", "Ana", -2, ""),
  act("To-Do", "Murchison - Piloto", "Murchison SA", "Beto", 3, ""),
  act("Call", "Cafes La Virginia", "La Virginia", "Ana", 12, ""),
  act("To-Do", "Aluar - Assestment", "Aluar", "Beto", 25, "Revisión interna"),
];

function resetState(patch) {
  commercialState.activitiesStatus = "ready";
  commercialState.activities = { activities: ACTS, total: ACTS.length };
  commercialState.actPeriod = "all";
  commercialState.actUser = "";
  commercialState.actSearchText = "";
  commercialState.actSortKey = null;
  commercialState.actSortDir = 1;
  Object.assign(commercialState, patch || {});
}

// ═══ BÚSQUEDA + LIMPIAR ═════════════════════════════════════════════════════

// --- Los controles: Buscar junto a Responsable, y el Limpiar compartido ----
{
  resetState();
  const bar = commercialActivitiesControlsHtml();
  assertTrue(bar.includes("data-commercial-act-search"), "hay un campo de búsqueda");
  assertTrue(bar.indexOf("data-commercial-act-user") < bar.indexOf("data-commercial-act-search"),
    "Buscar va DESPUÉS de Responsable, como pidió el owner");
  assertTrue(bar.includes('class="whatsnew-filter__clear" data-commercial-act-clear'),
    "y el ✕ Limpiar reusa el primitivo COMPARTIDO del Hub, sin clase nueva");
  assertTrue(bar.includes("✕ Limpiar"), "con el mismo copy que el resto del Hub");
  assertTrue(bar.includes('type="search"') && bar.includes("aria-label="),
    "input de búsqueda real y con nombre accesible");
  assertTrue(count(bar, "whatsnew-filter--fill") === 1,
    "sólo Buscar es elástico — así Limpiar queda a la derecha sin posicionamiento propio");
  assertTrue(bar.includes("Período") && bar.includes("Responsable"),
    "y los dos controles que ya existían siguen ahí");
  // El valor tipeado sobrevive el re-render de la barra.
  resetState({ actSearchText: "Loma" });
  assertTrue(commercialActivitiesControlsHtml().includes('value="Loma"'), "el input refleja el estado");
}

// --- La búsqueda filtra sobre lo que la fila MUESTRA ----------------------
{
  resetState();
  const txt = commercialActivitySearchableText(ACTS[0]);
  for (const needle of ["to-do", "loma negra", "loma negra sa", "ana", "llamar a compras", "venc. 40d"]) {
    assertTrue(txt.includes(needle), `el texto buscable incluye "${needle}"`);
  }
  resetState({ actSearchText: "Loma" });
  assertTrue(commercialFilteredActivities().length === 1, "busca por oportunidad");
  resetState({ actSearchText: "beto" });
  assertTrue(commercialFilteredActivities().length === 3, "y por responsable, case-insensitive");
  resetState({ actSearchText: "  VENC. 40D  " });
  assertTrue(commercialFilteredActivities().length === 1, "y por el relativo, con trim");
  resetState({ actSearchText: "zzz-no-existe" });
  assertTrue(commercialFilteredActivities().length === 0, "sin match, cero filas");
}

// --- Los KPIs siguen a la búsqueda: nunca contradicen las secciones -------
// Diferencia DELIBERADA con Activas: allá la búsqueda es sólo de tabla para no
// mover el Funnel, que es una vista analítica aparte. Acá los dos KPIs son
// literalmente los contadores de las secciones de abajo.
{
  resetState();
  assertTrue(kpis(commercialActivitiesBodyHtml()).join("/") === "6/3/3",
    "sin filtros: 6 totales, 3 retrasadas, 3 por vencer");
  resetState({ actSearchText: "beto" });
  const body = commercialActivitiesBodyHtml();
  assertTrue(kpis(body).join("/") === "3/1/2", "buscando 'beto': 3 totales, 1 retrasada, 2 por vencer");
  // Y coinciden con los encabezados de sección — el contrato que esto protege.
  assertTrue(body.includes("1 actividad(es)") && body.includes("2 actividad(es)"),
    "los contadores de Retrasadas/Pendientes acompañan, nunca se contradicen con los KPIs");
  resetState({ actSearchText: "zzz-no-existe" });
  assertTrue(kpis(commercialActivitiesBodyHtml()).join("/") === "0/0/0",
    "sin resultados, los KPIs también en cero");
  assertTrue(commercialActivitiesBodyHtml().includes("dashboard-empty"),
    "y las secciones muestran el estado vacío del Hub, no una tabla con sólo cabeceras");
}

// ═══ BADGES TONALES ═════════════════════════════════════════════════════════

// --- La magnitud (no el signo) define el tono ----------------------------
{
  assertTrue(commercialUrgencyColor(-2) === "green" && commercialUrgencyColor(3) === "green",
    "vencida hace 2 días y por vencer en 3 comparten tono: la magnitud es lo que importa");
  assertTrue(commercialUrgencyColor(-10) === "amber" && commercialUrgencyColor(12) === "amber", "8-15 días = amber");
  assertTrue(commercialUrgencyColor(25) === "orange", "16-30 días = orange");
  assertTrue(commercialUrgencyColor(-40) === "red", "+30 días = red");
  assertTrue(commercialUrgencyColor(null) === null, "sin fecha no hay tono que asignar");
}

// --- El badge en la fila, y el texto SIEMPRE presente --------------------
{
  resetState();
  const buckets = commercialActivityBuckets(commercialFilteredActivities());
  const html = commercialActivityTableHtml(buckets.overdue) + commercialActivityTableHtml(buckets.pending);
  const badges = [...html.matchAll(/dashboard-status-badge--(\w+)">([^<]*)</g)].map(m => `${m[1]}:${m[2]}`);
  assertTrue(badges.join(" ") === "red:venc. 40d amber:venc. 10d green:venc. 2d green:3d amber:12d orange:25d",
    `un badge por fila, con el tono de su urgencia (got ${badges.join(" ")})`);
  assertTrue(count(html, "dashboard-status-badge--") === 6, "seis filas, seis badges");
  // Reusa el primitivo COMPARTIDO, no una clase propia de Actividades.
  assertTrue(!/activity-badge|act-badge/.test(html), "sin clase de badge propia");
  // El texto del relativo existe SIEMPRE: la urgencia no depende del color.
  for (const t of ["venc. 40d", "venc. 10d", "venc. 2d", "3d", "12d", "25d"]) {
    assertTrue(html.includes(`>${t}<`), `"${t}" está escrito, no sólo pintado`);
  }
  // Sin fecha: ni badge ni relativo inventado.
  const sinFecha = commercialActivityTableHtml(commercialActivityBuckets([act("To-Do", "X", "Y", "Z", null)]).pending);
  assertTrue(!sinFecha.includes("dashboard-status-badge"), "sin fecha no hay badge");
  assertTrue(!/NaN|undefined|null/.test(sinFecha), "y no inventa un relativo");
}

// --- La leyenda, una sola vez y sólo con filas ---------------------------
{
  resetState();
  const body = commercialActivitiesBodyHtml();
  assertTrue(count(body, "urgency-legend\\b") === 1,
    "la leyenda aparece UNA vez para las dos secciones — misma escala, no se repite");
  // `data-section-id="..."` (no el substring "act-retrasadas" a secas): el
  // tile clickeable de arriba también contiene "act-retrasadas" en su
  // `data-commercial-act-jump`, así que un match laxo encontraría ESE, no
  // la sección real.
  assertTrue(body.indexOf("urgency-legend") < body.indexOf('data-section-id="act-retrasadas"'),
    "y va arriba de las secciones");
  assertTrue(count(body, "urgency-legend__dot--") === 4, "con sus cuatro tonos");
  resetState({ actSearchText: "zzz-no-existe" });
  assertTrue(!commercialActivitiesBodyHtml().includes("urgency-legend"),
    "sin filas no hay leyenda — nunca una escala sobre la nada");
}

// ═══ ORDEN POR COLUMNA ══════════════════════════════════════════════════════

// --- Las seis cabeceras son ordenables, con el primitivo compartido ------
{
  resetState();
  const buckets = commercialActivityBuckets(commercialFilteredActivities());
  const html = commercialActivityTableHtml(buckets.overdue);
  const keys = [...html.matchAll(/data-commercial-act-sort="(\w+)"/g)].map(m => m[1]);
  assertTrue(keys.join("|") === "deadline|type|opportunity|client|user|detail",
    `las seis columnas ordenables, en orden (got ${keys.join("|")})`);
  assertTrue(count(html, "dashboard-table-th--sortable") === 6,
    "con el MISMO primitivo compartido que el detalle de Oportunidades");
  assertTrue(!html.includes("▲") && !html.includes("▼"), "sin flecha mientras no hay orden elegido");
}

// --- Toggle: primera vez ASC, segunda DESC, tercera columna reinicia -----
{
  resetState();
  commercialToggleActSort("client");
  assertTrue(commercialState.actSortKey === "client" && commercialState.actSortDir === 1, "primer click: ascendente");
  commercialToggleActSort("client");
  assertTrue(commercialState.actSortDir === -1, "segundo click sobre la MISMA columna: descendente");
  commercialToggleActSort("user");
  assertTrue(commercialState.actSortKey === "user" && commercialState.actSortDir === 1,
    "otra columna arranca ascendente de nuevo");
}

// --- El orden aplica DENTRO de cada bucket, y no muta la entrada ---------
{
  resetState();
  const buckets = commercialActivityBuckets(commercialFilteredActivities());
  const snapshot = buckets.overdue.map(a => a.opportunity).join("|");
  commercialState.actSortKey = "client"; commercialState.actSortDir = 1;
  const asc = commercialActivitiesSorted(buckets.overdue).map(a => a.client);
  commercialState.actSortDir = -1;
  const desc = commercialActivitiesSorted(buckets.overdue).map(a => a.client);
  assertTrue(asc.join("|") === [...asc].sort().join("|"), "ascendente ordena de verdad");
  assertTrue(desc.join("|") === [...asc].reverse().join("|"), "y descendente es el inverso exacto");
  assertTrue(buckets.overdue.map(a => a.opportunity).join("|") === snapshot,
    "el array recibido no se muta — `.sort` sobre una copia");
  // Fecha ordena por el RELATIVO (días), no por el string de la fecha.
  commercialState.actSortKey = "deadline"; commercialState.actSortDir = 1;
  const byDays = commercialActivitiesSorted(buckets.overdue).map(a => a.days);
  assertTrue(byDays.join("|") === [...byDays].sort((a, b) => a - b).join("|"),
    "Fecha ordena numéricamente por días, que es la señal de triage");
  // Sin fecha va al final en ascendente, nunca antes que un vencimiento real.
  const conNull = commercialActivityBuckets([act("A", "a", "c", "u", null), act("B", "b", "c", "u", 5)]).pending;
  commercialState.actSortKey = "deadline"; commercialState.actSortDir = 1;
  assertTrue(commercialActivitiesSorted(conNull).map(a => a.days).join("|") === "5|",
    "sin fecha ordena al final en ascendente");
  resetState();
}

// --- La flecha marca la columna activa ----------------------------------
{
  resetState({ actSortKey: "client", actSortDir: 1 });
  let html = commercialActivityTableHtml(commercialActivityBuckets(commercialFilteredActivities()).overdue);
  assertTrue(count(html, "▲") === 1 && !html.includes("▼"), "ascendente: una sola flecha arriba");
  assertTrue(/data-commercial-act-sort="client">Cliente ▲/.test(html), "y sobre la columna elegida");
  commercialState.actSortDir = -1;
  html = commercialActivityTableHtml(commercialActivityBuckets(commercialFilteredActivities()).overdue);
  assertTrue(count(html, "▼") === 1 && !html.includes("▲"), "descendente: una sola flecha abajo");
  resetState();
}

// ═══ LIFECYCLE ══════════════════════════════════════════════════════════════
{
  hosts["[data-commercial-activities-body]"] = { innerHTML: "" };
  (listeners.DOMContentLoaded || []).forEach(cb => cb());
  const fireInput = value => (listeners.input || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-act-search]" ? { value } : null) },
  }));
  const fireClick = sel0 => (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === sel0 ? { dataset: { commercialActSort: "client" } } : null) },
    preventDefault() {}, stopPropagation() {},
  }));

  // Buscar: render QUIRÚRGICO del cuerpo, sin tocar la barra ni la página.
  resetState();
  root.innerHTML = "<!--root-intacto-->";
  fetchCalls.length = 0;
  fireInput("beto");
  assertTrue(commercialState.actSearchText === "beto", "el delegado de input guarda el texto");
  assertTrue(kpis(hosts["[data-commercial-activities-body]"].innerHTML).join("/") === "3/1/2",
    "y re-renderiza el cuerpo con el resultado");
  assertTrue(root.innerHTML === "<!--root-intacto-->",
    "sin render completo: el `<input>` no se recrea y no pierde el foco entre teclas");
  assertTrue(fetchCalls.length === 0, "y sin una sola request: es client-side puro");

  // Orden: mismo camino quirúrgico.
  resetState();
  root.innerHTML = "<!--root-intacto-->";
  fetchCalls.length = 0;
  fireClick("[data-commercial-act-sort]");
  assertTrue(commercialState.actSortKey === "client" && commercialState.actSortDir === 1,
    "el click en la cabecera ordena");
  assertTrue(root.innerHTML === "<!--root-intacto-->", "por render quirúrgico");
  assertTrue(fetchCalls.length === 0, "y sin red");

  // Limpiar: resetea los TRES filtros.
  resetState({ actPeriod: "week:1", actUser: "Ana", actSearchText: "Loma",
               actSortKey: "client", actSortDir: -1 });
  fetchCalls.length = 0;
  (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-act-clear]" ? { dataset: {} } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
  assertTrue(commercialState.actPeriod === "all" && commercialState.actUser === ""
    && commercialState.actSearchText === "",
    "Limpiar resetea Período + Responsable + Buscar de una vez");
  assertTrue(commercialState.actSortKey === "client" && commercialState.actSortDir === -1,
    "pero NO el orden: es una preferencia de lectura, no un filtro (mismo criterio que Activas)");
  assertTrue(fetchCalls.length === 0, "y sin red");
  resetState();
}

// ═══ "RETRASADAS"/"POR VENCER" CLICKEABLES (owner follow-up, ver HANDOFF) ═══
{
  resetState();
  const body = commercialActivitiesBodyHtml();
  assertTrue(count(body, 'data-commercial-act-jump="act-retrasadas"') === 1,
    "el tile Retrasadas lleva su propio hook de salto");
  assertTrue(count(body, 'data-commercial-act-jump="act-pendientes"') === 1,
    "y el tile Por vencer también");
  assertTrue(count(body, "activity-kpi--clickable") === 2,
    "sólo esos dos tiles son clickeables — Total pendientes no tiene sección propia");
  // El motor real: click/keydown sobre el tile fuerza `open` y scrollea la
  // `<details>` correspondiente — nunca `document.querySelector` (el sandbox
  // arriba tira si algo lo usa).
  const target = { open: false, scrolled: 0, scrollIntoView() { this.scrolled++; } };
  hosts['[data-section-id="act-retrasadas"]'] = target;
  fetchCalls.length = 0;
  (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-act-jump]" ? { dataset: { commercialActJump: "act-retrasadas" } } : null) },
    preventDefault() {}, stopPropagation() {},
  }));
  assertTrue(target.open === true, "el click reabre la sección si estaba colapsada");
  assertTrue(target.scrolled === 1, "y hace scroll hacia ella");
  assertTrue(fetchCalls.length === 0, "sin red: es sólo navegación local");
  // Enter/Espacio (role=button, no <button> nativo) hacen lo mismo.
  target.open = false; target.scrolled = 0;
  (listeners.keydown || []).forEach(cb => cb({
    key: "Enter",
    target: { closest: sel => (sel === "[data-commercial-act-jump]" ? { dataset: { commercialActJump: "act-retrasadas" } } : null) },
    preventDefault() {},
  }));
  assertTrue(target.open === true && target.scrolled === 1, "y el mismo camino por teclado (Enter)");
  resetState();
}

// ═══ ANCHOS DE COLUMNA ══════════════════════════════════════════════════════
// Owner smoke: "sobra mucho espacio en la columna fecha, prefiero que ese
// espacio lo gane Oportunidad". Con `table-layout:fixed` el sobrante se reparte
// en PROPORCIÓN a los anchos declarados, así que lo que se fija es la relación.
{
  resetState();
  const html = commercialActivityTableHtml(commercialActivityBuckets(commercialFilteredActivities()).overdue);
  const widths = [...html.matchAll(/<col style="width:(\d+)px">/g)].map(m => Number(m[1]));
  assertTrue(widths.length === 6, "las seis columnas declaradas");
  assertTrue(widths[2] > widths[0],
    `Oportunidad (${widths[2]}px) pesa MÁS que Fecha (${widths[0]}px) — antes era al revés`);
  assertTrue(widths.reduce((a, b) => a + b, 0) === 680,
    "y la suma no cambió: el piso de scroll horizontal en mobile queda igual");
  assertTrue(widths[2] === Math.max(...widths), "Oportunidad es la columna más ancha");
  assertTrue(html.includes("matrix-scroll"), "el scroll horizontal sigue siendo el primitivo compartido");
}

// ═══ NO REGRESIÓN ═══════════════════════════════════════════════════════════
{
  resetState();
  // El shell conserva su barra; el cuerpo va en su host estable. El rótulo
  // "Actividades Pendientes" se sacó (owner follow-up, ver HANDOFF) — sólo
  // queda Período + su botonera arriba del cuerpo.
  const panel = commercialActividadesHtml();
  assertTrue(!panel.includes("Actividades Pendientes"), "el título de la capability ya no se imprime");
  assertTrue(panel.includes("data-commercial-activities-body"), "y el cuerpo en su host estable");
  assertTrue(panel.indexOf("data-commercial-act-search") < panel.indexOf("data-commercial-activities-body"),
    "la barra de controles queda FUERA del host que se re-renderiza al buscar");
  // Retrasadas siempre antes que Pendientes (comparando las SECCIONES, no
  // los tiles clickeables que ya mencionan ambos ids más arriba).
  assertTrue(panel.indexOf('data-section-id="act-retrasadas"') < panel.indexOf('data-section-id="act-pendientes"'),
    "el orden semántico de las secciones no cambió");
  // Estados de carga/error intactos.
  commercialState.activitiesStatus = "loading";
  assertTrue(commercialActividadesHtml().includes("section-analysis-loading"), "estado de carga intacto");
  commercialState.activitiesStatus = "error";
  commercialState.activitiesErrorState = "source_error";
  assertTrue(commercialActividadesHtml().includes("dashboard-empty"), "estado de error intacto");
  resetState();
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL ACTIVITIES CONTROLS INVARIANTS PASSED");
