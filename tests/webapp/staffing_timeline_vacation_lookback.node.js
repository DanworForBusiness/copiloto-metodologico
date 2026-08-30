// staffing_timeline_vacation_lookback.node.js — invocado por
// test_staffing_timeline_vacation_lookback.py. Owner corrective "Timeline
// de asignaciones — ventana de ausencias DINÁMICA" (Dirección, ver
// HANDOFF). NOMBRE DE ARCHIVO histórico ("vacation lookback" era el
// alcance de la primera versión, revocada dos veces por el owner — primero
// para cubrir los 4 conceptos, después para reemplazar el techo fijo de 28
// por uno dinámico basado en lo que el backend realmente hidrata) — el
// control/estado real ya se llama `absenceLookbackDays`/
// `absenceLookbackMaxDays` (ver app.js), este archivo no se renombró para
// no romper la referencia de su wrapper pytest sin necesidad.
//
// `staffingDashboard(payload)` (app.js) es una fábrica PLANA — devuelve un
// objeto con getters/métodos, recién envuelto en reactividad por Alpine
// AFUERA de esta función — así que se puede instanciar y leer directo en
// Node sin simular Alpine/DOM. `Date` se pisa por una subclase que fija
// "hoy" al 28/08/2026 SOLO para `new Date()` sin argumentos.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "app.js");
const src = fs.readFileSync(APP_JS, "utf-8");

const REAL_TODAY = new Date(2026, 7, 28); // 28/08/2026, año/mes/día LOCAL — mismo ejemplo del owner.
class FixedDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(REAL_TODAY.getTime());
    else super(...args);
  }
  static now() { return REAL_TODAY.getTime(); }
}

const fetchCalls = [];
let fetchResponse = { state: "ok", absences: [], absence_history_start: "2026-07-01", absence_lookback_max_days: 58 };
function fakeFetch(url) {
  fetchCalls.push(url);
  return Promise.resolve({ json: () => Promise.resolve(fetchResponse) });
}

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
  Date: FixedDate,
  fetch: fakeFetch,
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
function assertClose(actual, expected, label, eps) {
  assertTrue(Math.abs(actual - expected) < (eps || 1e-6), `${label} (expected ${expected}, got ${actual})`);
}
function ids(list) { return list.map(a => a.source_id).sort(); }

// ── Fórmula de REFERENCIA, INDEPENDIENTE del código de producción ──────────
const HORIZON_START = new Date(2026, 6, 1); // 01/07/2026 local.
const TOTAL_MONTHS = 8; // _HORIZON_SIZE real (1 antes + actual + 6 después).
function _addDaysISOTest(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + days);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}
function refPct(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const monthOffset = (y - HORIZON_START.getFullYear()) * 12 + ((m - 1) - HORIZON_START.getMonth());
  const daysInMonth = new Date(y, m, 0).getDate();
  const dayFraction = (d - 1) / daysInMonth;
  return (monthOffset + dayFraction) / TOTAL_MONTHS * 100;
}
function absencePct(startISO, endISO) {
  return { start_pct: refPct(startISO), end_pct: refPct(_addDaysISOTest(endISO, 1)) };
}

function absence(source_id, consultant_id, start_date, end_date, concept) {
  const pct = absencePct(start_date, end_date);
  return { source_id, consultant_id, consultant_name: consultant_id, start_date, end_date,
    concept: concept || "Vacaciones", hours: null, ...pct };
}
function assignment(assignment_id, consultant_id, start_date, end_date) {
  return { assignment_id, consultant_id, consultant_name: consultant_id, project_id: "P1", project_name: "Proyecto 1",
    status: "Activo", on_timeline: true, start_date, end_date, start_pct: 0, end_pct: 50,
    allocation_unit: "hours_per_month", allocation_value: 80 };
}

const CONCEPTS = ["Vacaciones", "Danwor Day", "Cumpleaños", "Otros"];

async function main() {
  const payload = {
    assignments: [assignment("A1", "C1", "2026-01-01", "2026-12-31")],
    // 8 meses reales (jul 2026 → feb 2027), MISMO tamaño que
    // `_HORIZON_SIZE` (1 antes + actual + 6 después).
    horizon: [
      { index: 0, label: "jul 2026", is_current: false },
      { index: 1, label: "ago 2026", is_current: true },
      { index: 2, label: "sep 2026", is_current: false },
      { index: 3, label: "oct 2026", is_current: false },
      { index: 4, label: "nov 2026", is_current: false },
      { index: 5, label: "dic 2026", is_current: false },
      { index: 6, label: "ene 2027", is_current: false },
      { index: 7, label: "feb 2027", is_current: false },
    ],
    today_marker_pct: 50,
  };
  const app = staffingDashboard(payload);

  // ══ A) Estado SEGURO antes de hidratar — nunca promete más que el
  //       propio default hasta que el backend confirme cuánto hay. ══
  assertTrue(app.absenceLookbackDays === 10, `A: default es 10 (got ${app.absenceLookbackDays})`);
  assertTrue(app.absenceLookbackMaxDays === 10, `A: techo INICIAL (antes de hidratar) es el propio default, nunca un 28/365 fijo (got ${app.absenceLookbackMaxDays})`);

  // ══ B) Hidratación real — el máximo viene EXCLUSIVAMENTE del payload,
  //       nunca hardcoded. Ejemplo textual del owner: today=28/08,
  //       history_start=01/07 -> max=58. ══
  fetchResponse = { state: "ok", absences: [], absence_history_start: "2026-07-01", absence_lookback_max_days: 58 };
  await app._loadAbsences();
  assertTrue(fetchCalls.length === 1, "B: setup — una sola request de ausencias");
  assertTrue(app.absenceLookbackMaxDays === 58, `B: el techo se actualiza al valor REAL del payload (got ${app.absenceLookbackMaxDays})`);
  assertTrue(app.absenceLookbackDays === 10, "B: el valor actual (10) sigue siendo válido bajo el nuevo máximo — no se toca");

  // ══ C) max=58 permite 58; 59 clampa a 58 ══
  app.setAbsenceLookbackDays(58);
  assertTrue(app.absenceLookbackDays === 58, "C: 58 (el máximo exacto) es un valor VÁLIDO, no se recorta");
  app.setAbsenceLookbackDays(59);
  assertTrue(app.absenceLookbackDays === 58, "C: 59 (uno por encima del máximo real) se normaliza a 58, NUNCA a un 28/365 fijo");
  app.setAbsenceLookbackDays(21);
  assertTrue(app.absenceLookbackDays === 21, "C: 21 permanece 21 (dentro del máximo 58)");

  // ══ D) si el máximo cambia entre hidrataciones: conservar el valor
  //       actual si sigue siendo válido; si lo supera, clampear al nuevo. ══
  app.setAbsenceLookbackDays(50);
  fetchResponse = { state: "ok", absences: [], absence_history_start: "2026-08-14", absence_lookback_max_days: 45 };
  await app._loadAbsences();
  assertTrue(app.absenceLookbackMaxDays === 45, "D: el techo se actualiza al nuevo valor (45)");
  assertTrue(app.absenceLookbackDays === 45, "D: value=50 excede el nuevo máximo (45) -> clampeado a 45");

  fetchResponse = { state: "ok", absences: [], absence_history_start: "2026-07-01", absence_lookback_max_days: 58 };
  await app._loadAbsences();
  assertTrue(app.absenceLookbackMaxDays === 58, "D: setup — vuelve a subir a 58");
  assertTrue(app.absenceLookbackDays === 45, "D: value=45 (bajo el máximo anterior de 45) SIGUE siendo válido bajo 58 -> se conserva, no salta a 58");
  app.setAbsenceLookbackDays(10); // reset al default

  // ══ E) los CUATRO conceptos obedecen el mismo cutoff — ejemplo textual
  //       EXACTO del smoke: un registro de 08/07 (cualquier concepto) NO
  //       debe aparecer con N=10. ══
  app.absences = CONCEPTS.map(c => absence(`OLD-${c}`, "C1", "2026-07-08", "2026-07-08", c));
  const visibleOld = ids(app.filteredAbsences);
  CONCEPTS.forEach(c => assertFalse(visibleOld.includes(`OLD-${c}`), `E: ${c} de 08/07 NO aparece con N=10 (cutoff 18/08)`));

  // ══ F) los CUATRO conceptos obedecen CLIPPING — 15/08→20/08 se muestra
  //       18/08→20/08, tooltip 15/08→20/08. ══
  app.absences = CONCEPTS.map(c => absence(`MID-${c}`, "C1", "2026-08-15", "2026-08-20", c));
  const realMid = absencePct("2026-08-15", "2026-08-20");
  CONCEPTS.forEach(c => {
    const a = app.filteredAbsences.find(x => x.source_id === `MID-${c}`);
    assertTrue(!!a, `F: ${c} (15/08→20/08) sigue visible (cruza el cutoff)`);
    assertClose(a.start_pct, refPct("2026-08-18"), `F: ${c} -> visible_start clippeado a 18/08 (el cutoff)`);
    assertClose(a.end_pct, realMid.end_pct, `F: ${c} -> end_pct intacto (20/08 real, nunca se recorta)`);
    assertTrue(a.start_date === "2026-08-15" && a.end_date === "2026-08-20", `F: ${c} -> tooltip conserva 15/08→20/08 (fechas reales)`);
  });

  // ══ G) progresivo hacia atrás — ejemplo EXACTO del owner (27/07→07/08),
  //       ahora con máximo dinámico >= 32 se puede revelar el rango
  //       COMPLETO (a diferencia del corrective anterior, tope fijo 28). ══
  const G_START = "2026-07-27", G_END = "2026-08-07";
  const gReal = absencePct(G_START, G_END);
  app.absences = [absence("G", "C1", G_START, G_END, "Vacaciones")];
  const findG = () => app.filteredAbsences.find(a => a.source_id === "G");

  app.setAbsenceLookbackDays(21);
  assertTrue(app.absenceCutoffISO === "2026-08-07", `G: setup — cutoff N=21 es 07/08 (got ${app.absenceCutoffISO})`);
  assertClose(findG().start_pct, refPct("2026-08-07"), "G: N=21 -> visible_start == 07/08 (single-day)");

  app.setAbsenceLookbackDays(22);
  assertClose(findG().start_pct, refPct("2026-08-06"), "G: N=22 -> visible_start == 06/08");

  app.setAbsenceLookbackDays(23);
  assertClose(findG().start_pct, refPct("2026-08-05"), "G: N=23 -> visible_start == 05/08");

  app.setAbsenceLookbackDays(32); // cutoff = 28/08 - 32 = 27/07 == inicio real
  assertTrue(app.absenceCutoffISO === G_START, `G: setup — cutoff N=32 == inicio real 27/07 (got ${app.absenceCutoffISO})`);
  assertClose(findG().start_pct, gReal.start_pct, "G: N=32 (máximo dinámico ahora lo permite) -> revela el inicio REAL completo, sin clip");
  assertClose(findG().end_pct, gReal.end_pct, "G: end_pct siempre intacto");
  app.setAbsenceLookbackDays(10);

  // ══ H) boundary exacto (con cualquier concepto) ══
  app.absences = [
    absence("BOUND-IGUAL", "C1", "2026-08-11", "2026-08-18", "Danwor Day"),
    absence("BOUND-ANTES", "C1", "2026-08-11", "2026-08-17", "Cumpleaños"),
  ];
  const visibleBound = ids(app.filteredAbsences);
  assertTrue(visibleBound.includes("BOUND-IGUAL"), "H: end == cutoff -> INCLUIDA (>=, no >)");
  assertFalse(visibleBound.includes("BOUND-ANTES"), "H: end un día antes del cutoff -> EXCLUIDA");

  // ══ I) checkboxes de Concepto siguen componiendo AND con la ventana ══
  app.absences = [absence("VAC-RECIENTE", "C1", "2026-08-20", "2026-08-25", "Vacaciones")];
  assertTrue(ids(app.filteredAbsences).includes("VAC-RECIENTE"), "I: setup — visible por defecto");
  app.filters.concept["Vacaciones"] = false;
  assertFalse(ids(app.filteredAbsences).includes("VAC-RECIENTE"), "I: destildar el concepto la oculta aunque esté DENTRO de la ventana (AND, no OR)");
  app.filters.concept["Vacaciones"] = true;
  assertTrue(ids(app.filteredAbsences).includes("VAC-RECIENTE"), "I: retildar la muestra de nuevo");

  // ══ J) asignaciones / Detalle invariantes ante cualquier N ══
  const asigBefore = JSON.stringify(app.filteredAssignments.map(a => ({ start_pct: a.start_pct, end_pct: a.end_pct })));
  const detailBefore = JSON.stringify(app.aggregatedDetailRows);
  [1, 10, 21, 45, 58].forEach(n => app.setAbsenceLookbackDays(n));
  const asigAfter = JSON.stringify(app.filteredAssignments.map(a => ({ start_pct: a.start_pct, end_pct: a.end_pct })));
  const detailAfter = JSON.stringify(app.aggregatedDetailRows);
  assertTrue(asigBefore === asigAfter, "J: las asignaciones NUNCA cambian de posición, para ningún valor de absenceLookbackDays");
  assertTrue(detailBefore === detailAfter, "J: la tabla de Detalle tampoco se altera");
  app.setAbsenceLookbackDays(10);

  // ══ K) cero fetch adicional al cambiar N o los checkboxes ══
  fetchCalls.length = 0;
  [1, 10, 21, 58].forEach(n => app.setAbsenceLookbackDays(n));
  app.filters.concept["Vacaciones"] = false; app.filters.concept["Vacaciones"] = true;
  assertTrue(fetchCalls.length === 0, `K: cambiar N o los checkboxes NUNCA dispara una request (got ${fetchCalls.length})`);
  app.setAbsenceLookbackDays(10);

  // ══ L) objeto FUENTE nunca mutado; filteredAbsences es idempotente ══
  app.absences = [absence("MUT", "C1", "2026-08-05", "2026-08-10", "Vacaciones")];
  const mutReal = absencePct("2026-08-05", "2026-08-10");
  app.filteredAbsences;
  assertClose(app.absences[0].start_pct, mutReal.start_pct, "L: el objeto fuente (this.absences) NUNCA se muta");
  const clipped = app.filteredAbsences.find(a => a.source_id === "MUT");
  assertTrue(app.absences[0] !== clipped, "L: filteredAbsences siempre devuelve una COPIA, nunca la referencia original");
  const r1 = ids(app.filteredAbsences), r2 = ids(app.filteredAbsences);
  assertTrue(r1.join(",") === r2.join(","), "L: leer filteredAbsences repetidas veces es IDEMPOTENTE");

  // ══ M) visibilidad del control ══
  function setConcepts(vac, dday, cumple, otros) {
    app.filters.concept["Vacaciones"] = vac;
    app.filters.concept["Danwor Day"] = dday;
    app.filters.concept["Cumpleaños"] = cumple;
    app.filters.concept["Otros"] = otros;
  }
  setConcepts(true, false, false, false);
  assertTrue(app.anyConceptSelected, "M1: Vacaciones ON, resto OFF -> selector visible");
  setConcepts(false, true, false, false);
  assertTrue(app.anyConceptSelected, "M2: Vacaciones OFF, Danwor Day ON -> selector visible");
  setConcepts(false, false, true, false);
  assertTrue(app.anyConceptSelected, "M3: sólo Cumpleaños ON -> selector visible");
  setConcepts(false, false, false, true);
  assertTrue(app.anyConceptSelected, "M4: sólo Otros ON -> selector visible");
  setConcepts(false, false, false, false);
  assertFalse(app.anyConceptSelected, "M5: los 4 conceptos OFF -> selector OCULTO");
  app.setAbsenceLookbackDays(19);
  setConcepts(false, false, false, false);
  assertTrue(app.absenceLookbackDays === 19, "M6: absenceLookbackDays NO se resetea mientras el control está oculto");
  setConcepts(true, true, true, true);
  assertTrue(app.anyConceptSelected, "M6: reaparece al tildar de nuevo");
  assertTrue(app.absenceLookbackDays === 19, "M6: y conserva el MISMO N (19, clampeado al máximo runtime — sigue válido bajo 58)");
  app.setAbsenceLookbackDays(10);

  if (failures > 0) {
    console.log(`\n${failures} invariante(s) de ventana de ausencias FALLARON`);
    process.exit(1);
  }
  console.log("ALL STAFFING TIMELINE ABSENCE LOOKBACK INVARIANTS PASSED");
  process.exit(0);
}

main().catch(err => { console.log("FAIL: uncaught error: " + err.stack); process.exit(1); });
