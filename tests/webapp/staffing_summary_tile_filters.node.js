// staffing_summary_tile_filters.node.js — invocado por
// test_staffing_summary_tile_filters.py. STAFFING / ASIGNACIONES —
// CLICKABLE SUMMARY TILES AS PANEL FILTERS (owner ticket, ver HANDOFF).
//
// MISMO patrón que staffing_timeline_vacation_lookback.node.js:
// `staffingDashboard(payload)` es una fábrica PLANA (getters/métodos sobre
// un objeto simple) — se puede instanciar y leer directo en Node sin
// simular Alpine/DOM. `init()` (que registra `$watch`) NO se llama acá —
// mismo criterio que el harness de ausencias: el diseño deliberadamente
// resuelve la mutua exclusión de las tarjetas de FORMA DOBLE (ver app.js,
// comentario de `clickFullTimeTile`) — inline en cada handler de clic
// (testeable sin `$watch`, lo que este harness ejercita) Y vía `$watch` en
// `init()` (backstop del camino MANUAL del `<select>` Disponibilidad, que
// SÓLO se puede probar con Alpine real — owner UX smoke, no este harness).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "app.js");
const src = fs.readFileSync(APP_JS, "utf-8");

function noop() {}
const sandbox = {
  document: {
    addEventListener: noop,
    body: { addEventListener: noop },
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  window: { addEventListener: noop },
  fetch: () => Promise.resolve({ json: () => Promise.resolve({ state: "ok", absences: [] }) }),
  dwGraphToken: async () => "tok",
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "app.js" });
const { staffingDashboard } = sandbox;

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertFalse(cond, label) { assertTrue(!cond, label); }
function assertEqual(actual, expected, label) {
  const same = Array.isArray(expected) ? JSON.stringify(actual) === JSON.stringify(expected) : actual === expected;
  assertTrue(same, `${label} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
}
function ids(list) { return list.map(a => a.assignment_id).sort(); }

// ── Fixture: 5 asignaciones cubriendo Full Time / Part Time / Sin dato / ──
// Activos vencidos, cruzadas con otros filtros (Cliente) para probar
// composición (§7 del ticket).
function row(overrides) {
  return {
    assignment_id: "a1", consultant_id: "c1", consultant_name: "Ana",
    project_id: "p1", project_name: "Proyecto Alfa", client_id: null, client_name: null,
    allocation_value: 40, allocation_unit: "hours_per_month", allocation_label: "40 h/mes",
    status: "Activo", start_date: "2026-01-01", end_date: "2026-12-31",
    availability: "Full Time", activo_vencido: false, on_timeline: true,
    start_pct: 10, end_pct: 20, clipped_start: false, clipped_end: false,
    consultant_unresolved: false, project_unresolved: false,
    ...overrides,
  };
}
const ASSIGNMENTS = [
  row({ assignment_id: "ft1", consultant_id: "c1", consultant_name: "Ana Pérez", availability: "Full Time", client_id: "cli1", client_name: "Cliente X" }),
  row({ assignment_id: "ft2", consultant_id: "c2", consultant_name: "Beto Ruiz", availability: "Full Time", client_id: "cli2", client_name: "Cliente Y" }),
  row({ assignment_id: "pt1", consultant_id: "c3", consultant_name: "Cora Díaz", availability: "Part Time", client_id: "cli1", client_name: "Cliente X" }),
  row({ assignment_id: "sd1", consultant_id: "c4", consultant_name: "Dani Soto", availability: "Sin dato", client_id: "cli1", client_name: "Cliente X" }),
  // Activo vencido: Status Activo + end_date pasado — el server ya resolvió
  // `activo_vencido=true` (is_activo_vencido, staffing_service.py); este
  // harness NUNCA reimplementa esa regla, sólo consume el campo.
  row({ assignment_id: "av1", consultant_id: "c5", consultant_name: "Eva Vega", availability: "Full Time", status: "Activo", activo_vencido: true, client_id: "cli2", client_name: "Cliente Y" }),
];
const HORIZON = [{ index: 0, label: "ago 2026", is_current: true }];

function freshApp() {
  return staffingDashboard({ assignments: ASSIGNMENTS, horizon: HORIZON, today_marker_pct: null });
}

// ── A. Estado default — sin tarjeta activa, panel sin cambios ────────────
{
  const app = freshApp();
  assertEqual(app.activeSummaryTile, null, "A: default sin tarjeta activa");
  assertEqual(app.summaryTileFilterLabel, "", "A: sin label de chip");
  assertEqual(app.filters.availability, "", "A: Disponibilidad default Todos");
  assertFalse(app.filters.activosVencidos, "A: activosVencidos default false");
  assertEqual(app.filteredAssignments.length, ASSIGNMENTS.length, "A: universo completo (default status = Activo, todas son Activo)");
}

// ── B. Tarjeta Full Time ──────────────────────────────────────────────────
{
  const app = freshApp();
  app.clickFullTimeTile();
  assertEqual(app.filters.availability, "Full Time", "B: dropdown Disponibilidad -> Full Time");
  assertEqual(app.activeSummaryTile, "full_time", "B: tarjeta Full Time activa");
  assertEqual(app.summaryTileFilterLabel, "Full Time", "B: chip dice Full Time");
  assertEqual(ids(app.filteredAssignments), ["av1", "ft1", "ft2"].sort(), "B: Timeline/detalle sólo Full Time");
  assertTrue(app.filteredAssignments.every(a => a.availability === "Full Time"), "B: población consistente Full Time");
}

// ── C. Tarjeta Part Time ──────────────────────────────────────────────────
{
  const app = freshApp();
  app.clickPartTimeTile();
  assertEqual(app.filters.availability, "Part Time", "C: dropdown Disponibilidad -> Part Time");
  assertEqual(app.activeSummaryTile, "part_time", "C: tarjeta Part Time activa");
  assertEqual(ids(app.filteredAssignments), ["pt1"], "C: Timeline/detalle sólo Part Time");
}

// ── D. Tarjeta Activos vencidos — usa exactamente `activo_vencido` ────────
{
  const app = freshApp();
  app.clickActivosVencidosTile();
  assertEqual(app.activeSummaryTile, "activos_vencidos", "D: tarjeta Activos vencidos activa");
  assertEqual(app.filters.availability, "", "D: Disponibilidad SIN mismatch (Todos)");
  assertEqual(ids(app.filteredAssignments), ["av1"], "D: sólo la asignación con activo_vencido=true");
  assertTrue(app.filteredAssignments.every(a => a.activo_vencido === true), "D: población == predicado activo_vencido");
}

// ── E. Full Time -> Part Time: un solo filtro activo, dropdown correcto ──
{
  const app = freshApp();
  app.clickFullTimeTile();
  app.clickPartTimeTile();
  assertEqual(app.filters.availability, "Part Time", "E: dropdown termina en Part Time");
  assertEqual(app.activeSummaryTile, "part_time", "E: sólo Part Time activa");
  assertEqual(ids(app.filteredAssignments), ["pt1"], "E: población Part Time únicamente");
}

// ── F. Full Time -> Activos vencidos: FT se apaga, Disponibilidad Todos ──
{
  const app = freshApp();
  app.clickFullTimeTile();
  app.clickActivosVencidosTile();
  assertEqual(app.filters.availability, "", "F: Disponibilidad vuelve a Todos");
  assertEqual(app.activeSummaryTile, "activos_vencidos", "F: Activos vencidos toma el control");
  assertEqual(ids(app.filteredAssignments), ["av1"], "F: población pasa a activo_vencido");
}

// ── G. Activos vencidos -> Part Time: se apaga el vencido, dropdown PT ───
{
  const app = freshApp();
  app.clickActivosVencidosTile();
  app.clickPartTimeTile();
  assertFalse(app.filters.activosVencidos, "G: activosVencidos apagado");
  assertEqual(app.filters.availability, "Part Time", "G: Disponibilidad Part Time");
  assertEqual(app.activeSummaryTile, "part_time", "G: sólo Part Time activa");
}

// ── I/J. dismiss del chip (§8.A) — sólo toca el filtro de tarjeta ─────────
{
  const app = freshApp();
  app.filters.client = "cli1"; // filtro NO relacionado, ya elegido por el usuario
  app.clickFullTimeTile();
  app.clearSummaryTileFilter();
  assertEqual(app.activeSummaryTile, null, "I: tarjeta se apaga tras dismiss");
  assertEqual(app.filters.availability, "", "I: Disponibilidad vuelve a Todos");
  assertEqual(app.filters.client, "cli1", "I: Cliente NO se resetea por el dismiss del chip");
}

// ── K. filtros no relacionados sobreviven (Cliente + Full Time compuestos) ─
{
  const app = freshApp();
  app.filters.client = "cli1";
  app.clickFullTimeTile();
  // Cliente X (cli1) ∩ Full Time = sólo ft1 (ft2 es Cliente Y).
  assertEqual(ids(app.filteredAssignments), ["ft1"], "K: Cliente + Full Time componen, no se pisan");
  assertEqual(app.filters.client, "cli1", "K: Cliente sigue elegido tras activar la tarjeta");
}

// ── L. equivalencia con el predicado del KPI ──────────────────────────────
{
  const app = freshApp();
  const kpiCount = app.kpis.activosVencidos; // universo YA filtrado, ver docstring de kpis en app.js
  app.clickActivosVencidosTile();
  assertEqual(app.filteredAssignments.length, kpiCount, "L: población de la tarjeta == kpis.activosVencidos previo");
}

// ── M. Timeline y detalle nunca discrepan — mismas asignaciones ──────────
{
  const app = freshApp();
  app.clickFullTimeTile();
  const timelineIds = app.timelineGroups.flatMap(g => g.rows.map(r => r.assignment_id)).sort();
  const detailIds = app.aggregatedDetailRows.flatMap(g => g.rows.map(r => r.assignment_id)).sort();
  assertEqual(JSON.stringify(timelineIds), JSON.stringify(detailIds), "M: Timeline y Detalle representan la misma población");
}

// ── H. dropdown MANUAL — mismo efecto que clickear la tarjeta (§5.D/E) ───
// `init()` (que registra los `$watch`) nunca corre en el resto de este
// harness (mismo criterio que staffing_timeline_vacation_lookback.node.js
// — Alpine envuelve el objeto plano en reactividad AFUERA de esta fábrica).
// Acá SÍ hace falta probar ese camino (es el único que resuelve la mutua
// exclusión para un cambio de `<select>`, que nunca pasa por
// `clickFullTimeTile`/`clickActivosVencidosTile`) — se arma un polyfill
// MÍNIMO de `$watch` (getters/setters profundos sobre `filters`, re-atados
// tras cada reasignación de un sub-objeto) sólo para esta sección, nunca
// para simular Alpine completo.
function makeWatchable(target, onSet, pathPrefix) {
  Object.keys(target).forEach(key => {
    let value = target[key];
    const path = pathPrefix ? `${pathPrefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) value = makeWatchable(value, onSet, path);
    Object.defineProperty(target, key, {
      enumerable: true, configurable: true,
      get() { return value; },
      set(v) {
        if (v && typeof v === "object" && !Array.isArray(v)) v = makeWatchable(v, onSet, path);
        value = v;
        onSet(path, v);
      },
    });
  });
  return target;
}
{
  const app = freshApp();
  const watchers = {};
  app.$watch = (path, cb) => { (watchers[path] = watchers[path] || []).push(cb); };
  makeWatchable(app.filters, (path, value) => { (watchers[path] || []).forEach(cb => cb(value)); }, "filters");
  app.init();

  // D primero (activa Activos vencidos)…
  app.clickActivosVencidosTile();
  assertEqual(app.activeSummaryTile, "activos_vencidos", "H1: Activos vencidos activa antes del cambio manual");
  // …luego el usuario elige "Full Time" A MANO en el <select> (x-model
  // escribe filters.availability directo, nunca clickFullTimeTile()).
  app.filters.availability = "Full Time";
  assertEqual(app.activeSummaryTile, "full_time", "H2: elegir Full Time a mano == clickear la tarjeta Full Time");
  assertFalse(app.filters.activosVencidos, "H2: Activos vencidos se apaga por el watcher backstop");

  // Volver el dropdown a Todos limpia la tarjeta (§5.E).
  app.filters.availability = "";
  assertEqual(app.activeSummaryTile, null, "H3: dropdown Todos -> ninguna tarjeta activa");
}

console.log(failures === 0 ? "ALL STAFFING SUMMARY TILE FILTER INVARIANTS PASSED" : `${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
