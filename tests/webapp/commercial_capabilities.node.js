// commercial_capabilities.node.js — invocado por
// test_commercial_capabilities.py. Batch 3A / C3: navegación de capacidades
// (Dashboard | Actividades) + la capability Actividades Pendientes.
//
// Carga commercial.js en un sandbox mínimo y ejecuta el MOTOR REAL contra
// fixtures — MISMA técnica que commercial_distribucion_por_etapa.node.js.
// Acá importa especialmente la SEMÁNTICA DE FECHA CALENDARIO: se construyen
// los deadlines relativos al día de hoy LOCAL, así el test no se rompe con el
// paso del tiempo ni depende de la timezone del runner.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; } },
  window: {},
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialNavHtml, commercialDaysUntil, commercialFmtDate,
  commercialFilteredActivities, commercialActivityBuckets,
  commercialActividadesHtml, commercialActivityTableHtml, commercialStaticSection,
  commercialActivitiesControlsHtml,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);
const CAPABILITIES = vm.runInContext("COMMERCIAL_CAPABILITIES", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;

// Deadline ISO a N días del hoy LOCAL — nunca `new Date(iso)`, que
// interpretaría UTC.
const today = new Date();
function isoOffset(days) {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function act(over) {
  return Object.assign({
    opportunity: "Opp", client: "Cliente", user: "Ana Gómez", type: "Llamada",
    summary: "Resumen", note: "Nota", deadline: isoOffset(1),
  }, over);
}
function loadActivities(rows) {
  commercialState.activities = { activities: rows, total: rows.length };
  commercialState.activitiesStatus = "ready";
  commercialState.actPeriod = "all";
  commercialState.actUser = "";
}

// --- Registry: sólo capabilities implementadas ------------------------------
{
  assertTrue(CAPABILITIES.length === 2, "sólo 2 capabilities implementadas");
  assertTrue(CAPABILITIES.map(c => c.id).join() === "dashboard,actividades", "Dashboard primero, luego Actividades");
  assertTrue(CAPABILITIES[0].label === "Dashboard" && CAPABILITIES[1].label === "Actividades", "copy de la nav");
  assertTrue(commercialState.capability === "dashboard", "Dashboard es la capability default");
  assertTrue(commercialState.activitiesStatus === "idle", "Actividades arranca sin pedir nada (lazy)");
}

// --- Nav: a11y mínima y correcta --------------------------------------------
{
  const html = commercialNavHtml();
  assertTrue(html.includes('<nav class="experience-tabs" aria-label="Capacidades comerciales">'), "nav con label");
  assertTrue(count(html, 'aria-current="true"') === 1, "exactamente UNA capability marcada como actual");
  assertTrue(/class="is-active" aria-current="true" aria-controls="commercial-capability-panel" data-commercial-capability="dashboard"/.test(html),
    "la activa es Dashboard y apunta al panel único");
  assertTrue(count(html, "<button") === 2, "una tab por capability implementada");
  ["role=\"tablist\"", "role=\"tab\"", "aria-selected"].forEach(banned =>
    assertTrue(!html.includes(banned), `no se declara un tablist ARIA incompleto (${banned})`));
  // Conmutar mueve aria-current y no lo duplica.
  commercialState.capability = "actividades";
  const other = commercialNavHtml();
  assertTrue(count(other, 'aria-current="true"') === 1, "aria-current sigue siendo único al conmutar");
  assertTrue(/data-commercial-capability="actividades"[^>]*>Actividades/.test(other.replace(/class="is-active" aria-current="true" /, "")) || other.includes('aria-current="true" aria-controls="commercial-capability-panel" data-commercial-capability="actividades"'),
    "la actual pasa a ser Actividades");
  commercialState.capability = "dashboard";
}

// --- days_until: FECHA CALENDARIO, no timestamp UTC -------------------------
{
  assertTrue(commercialDaysUntil(isoOffset(0)) === 0, "hoy → 0");
  assertTrue(commercialDaysUntil(isoOffset(-1)) === -1, "ayer → -1");
  assertTrue(commercialDaysUntil(isoOffset(1)) === 1, "mañana → 1");
  assertTrue(commercialDaysUntil(isoOffset(-10)) === -10 && commercialDaysUntil(isoOffset(45)) === 45,
    "estable a distancia (absorbe cambios de horario)");
  [null, undefined, "", false, "basura", "2026-13-45", "2026-02-30", "20260820"].forEach(bad =>
    assertTrue(commercialDaysUntil(bad) === null, `entrada inválida → null (${bad})`));
  assertTrue(commercialFmtDate("2026-08-20") === "20/08/2026", "formato dd/mm/yyyy");
  assertTrue(commercialFmtDate(null) === "—", "sin fecha muestra guion");
}

// --- Buckets: retrasada / pendiente / sin fecha ------------------------------
{
  loadActivities([
    act({ deadline: isoOffset(-3), opportunity: "Vencida" }),
    act({ deadline: isoOffset(0), opportunity: "Hoy" }),
    act({ deadline: isoOffset(2), opportunity: "Futura" }),
    act({ deadline: null, opportunity: "Sin fecha" }),
  ]);
  const b = commercialActivityBuckets(commercialFilteredActivities());
  assertTrue(b.overdue.length === 1 && b.overdue[0].opportunity === "Vencida", "sólo days<0 es Retrasada");
  assertTrue(b.pending.map(a => a.opportunity).join() === "Hoy,Futura,Sin fecha",
    "hoy, futuro y SIN FECHA cuentan como Pendientes");
}

// --- Período: semana lunes→domingo, sin fecha excluida ----------------------
{
  // `week:0` cubre desde el lunes de esta semana hasta el domingo. Se calcula
  // el offset del lunes/domingo respecto de HOY para no atarse a un día fijo.
  const dow = today.getDay();               // 0 = domingo
  const sinceMonday = dow === 0 ? 6 : dow - 1;
  const toSunday = dow === 0 ? 0 : 7 - dow;
  loadActivities([
    act({ deadline: isoOffset(-sinceMonday), opportunity: "LunesEstaSemana" }),
    act({ deadline: isoOffset(toSunday), opportunity: "DomingoEstaSemana" }),
    act({ deadline: isoOffset(toSunday + 1), opportunity: "LunesProxima" }),
    act({ deadline: isoOffset(-sinceMonday - 1), opportunity: "DomingoPasado" }),
    act({ deadline: null, opportunity: "SinFecha" }),
  ]);
  commercialState.actPeriod = "week:0";
  const wk0 = commercialFilteredActivities().map(a => a.opportunity);
  assertTrue(wk0.includes("LunesEstaSemana") && wk0.includes("DomingoEstaSemana"),
    "week:0 cubre de lunes a domingo de la semana en curso");
  assertTrue(!wk0.includes("LunesProxima") && !wk0.includes("DomingoPasado"),
    "week:0 excluye la semana anterior y la siguiente (cruce domingo→lunes)");
  assertTrue(!wk0.includes("SinFecha"), "con período activo, sin fecha queda EXCLUIDA");

  commercialState.actPeriod = "week:1";
  const wk1 = commercialFilteredActivities().map(a => a.opportunity);
  assertTrue(wk1.includes("LunesProxima") && !wk1.includes("DomingoEstaSemana"), "week:1 es la semana siguiente");

  commercialState.actPeriod = "all";
  assertTrue(commercialFilteredActivities().length === 5, "sin período, entra todo — incluida la sin fecha");
}

// --- Responsable: derivado del dataset, client-side --------------------------
{
  loadActivities([
    act({ user: "Ana Gómez" }), act({ user: "Bruno Paz" }), act({ user: "Ana Gómez" }), act({ user: "" }),
  ]);
  const controls = commercialActivitiesControlsHtml();
  const options = (controls.match(/<option value="[^"]*"/g) || []).map(o => o.replace(/.*value="/, "").replace('"', ""));
  assertTrue(options[0] === "", "primera opción = Todos");
  assertTrue(options.slice(1).join() === "Ana Gómez,Bruno Paz", "responsables derivados del dataset, únicos y ordenados");
  commercialState.actUser = "Bruno Paz";
  assertTrue(commercialFilteredActivities().length === 1, "el filtro de responsable opera client-side");
  commercialState.actUser = "";
}

// --- Presentación: KPIs, buckets, orden, no draggable -----------------------
{
  loadActivities([
    act({ deadline: isoOffset(-2) }), act({ deadline: isoOffset(-1) }), act({ deadline: isoOffset(5) }),
  ]);
  const html = commercialActividadesHtml();
  assertTrue(html.includes('<p class="perspective-label">Actividades Pendientes</p>'), "heading de la capability");
  assertTrue(count(html, 'class="activity-kpi"') === 2, "exactamente 2 KPIs — sin métricas inventadas");
  assertTrue(html.includes("Total pendientes") && html.includes("Retrasadas"), "los KPIs son Total pendientes y Retrasadas");
  const ids = (html.match(/data-section-id="([^"]+)"/g) || []).map(m => m.replace(/.*="/, "").replace('"', ""));
  assertTrue(ids.join() === "act-retrasadas,act-pendientes", "Retrasadas SIEMPRE antes que Pendientes");
  assertTrue(!html.includes("draggable"), "los buckets NO son draggables — el orden es semántico");
  assertTrue(!html.includes("data-dashboard-key"), "no se registran en el spine que wirea reorder/persistencia");
  assertTrue(!html.includes("dashboard-section__grip"), "sin grip: no se anuncia una afordancia que no existe");
  // Los KPIs reflejan el conjunto filtrado.
  assertTrue(/Total pendientes/.test(html) && html.includes(">3<"), "Total pendientes = filas visibles");
}

// --- Estados vacío / carga / error -------------------------------------------
{
  loadActivities([]);
  const empty = commercialActividadesHtml();
  assertTrue(count(empty, "dashboard-empty__label") === 2, "cada bucket vacío comunica su propio estado (MISMO patrón que Sin selección/Copiloto, ver HANDOFF)");
  commercialState.activitiesStatus = "loading";
  assertTrue(commercialActividadesHtml().includes("section-analysis-loading"), "loader compartido del Hub");
  commercialState.activitiesStatus = "error";
  commercialState.activitiesErrorState = "config_incomplete";
  assertTrue(/todavía no está configurado/.test(commercialActividadesHtml()), "fail-soft: CRM sin configurar");
  commercialState.activitiesErrorState = "source_error";
  assertTrue(/No pudimos cargar las actividades/.test(commercialActividadesHtml()), "fail-soft: error de fuente");
  commercialState.activitiesStatus = "ready";
}

// --- Escaping de TODO dato de Odoo -------------------------------------------
{
  const nasty = act({
    opportunity: '<img src=x onerror="alert(1)">', client: "<b>C</b>", user: "<i>U</i>",
    type: '<script>alert("t")</script>', summary: "<svg/onload=1>", note: "<a href='#'>n</a>",
    deadline: isoOffset(0),
  });
  const html = commercialActivityTableHtml(commercialActivityBuckets([nasty]).pending);
  ["<img", "<script>", "<svg", "<b>", "<i>", "<a href"].forEach(tag =>
    assertTrue(!html.includes(tag), `escapado: ${tag}`));
  assertTrue(html.includes("&lt;script&gt;") && html.includes("&lt;img"), "escapado, no removido");
}

// --- Fila: fecha relativa legible --------------------------------------------
{
  const rows = commercialActivityBuckets([
    act({ deadline: isoOffset(-2) }), act({ deadline: isoOffset(0) }), act({ deadline: isoOffset(3) }), act({ deadline: null }),
  ]);
  const html = commercialActivityTableHtml(rows.overdue) + commercialActivityTableHtml(rows.pending);
  assertTrue(html.includes("(venc. 2d)"), "vencida muestra hace cuánto");
  assertTrue(html.includes("(hoy)"), "hoy se dice explícito");
  assertTrue(html.includes("(3d)"), "futura muestra días restantes");
  assertTrue(html.includes("—"), "sin fecha no inventa una");
}

// --- La sección estática reusa el contrato visual compartido -----------------
{
  const html = commercialStaticSection("x", "Título", "9 actividad(es)", "<p>body</p>");
  assertTrue(html.includes('class="dashboard-section"'), "reusa el contrato visual del Hub");
  assertTrue(html.includes("dashboard-section__count"), "el contador va en el primitivo compartido");
  assertTrue(!html.includes("draggable"), "pero sin heredar el drag");
}

if (failures) {
  console.log(`\n${failures} invariante(s) fallaron`);
  process.exit(1);
}
console.log("ALL COMMERCIAL CAPABILITIES INVARIANTS PASSED");
