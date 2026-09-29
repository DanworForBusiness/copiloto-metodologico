// dashboard_section_collapse_persistence.node.js — invocado por
// test_dashboard_section_collapse_persistence.py.
//
// CONTRATO (owner T2): el estado colapsado/expandido de una dashboard-section
// debe SOBREVIVIR un HTMX swap (CCHH re-renderiza el panel al filtrar). El
// primitivo compartido `initDashboardSections` gana persistencia de colapso
// OPT-IN vía `data-persist-collapse` (sessionStorage, igual que el orden);
// los consumidores SIN el atributo conservan el comportamiento nativo (no
// persisten) — no-regresión.
//
// Se carga el `app.js` REAL en un sandbox y se ejercitan
// `saveSectionCollapse`/`applyStoredSectionCollapse` (las funciones que hacen
// el round-trip) contra un mini-DOM con `<details>` reales (open via atributo).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(c, l) { if (!c) { failures++; console.log("FAIL: " + l); } }
function eq(a, e, l) { if (JSON.stringify(a) !== JSON.stringify(e)) { failures++; console.log(`FAIL: ${l} — esperado ${JSON.stringify(e)}, obtuvo ${JSON.stringify(a)}`); } }

// ── Mini-DOM: <details class="dashboard-section" data-section-id> ──────────
function makeSection(id, open) {
  const attrs = open ? { open: "" } : {};
  return {
    dataset: { sectionId: id },
    classList: { contains: (c) => c === "dashboard-section" },
    hasAttribute: (n) => n in attrs,
    setAttribute: (n, v) => { attrs[n] = v; },
    removeAttribute: (n) => { delete attrs[n]; },
  };
}
function makeContainer(dashKey, sections, persist) {
  const dataset = { dashboardKey: dashKey };
  if (persist) dataset.persistCollapse = "";
  return {
    dataset,
    querySelectorAll: (sel) => (sel.indexOf("dashboard-section") !== -1 ? sections : []),
  };
}

// sessionStorage stub
const store = {};
const sessionStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");
const noopEl = { classList: { add(){}, remove(){}, contains: () => false }, addEventListener() {}, remove() {}, appendChild() {} };
const sandbox = {
  sessionStorage,
  document: {
    addEventListener() {}, createElement: () => noopEl, querySelectorAll: () => [],
    querySelector: () => null, getElementById: () => null, body: { addEventListener() {} },
  },
  window: { localStorage: { getItem: () => null, setItem() {} }, addEventListener() {}, setInterval: () => 0, clearInterval() {}, requestAnimationFrame() {} },
  localStorage: { getItem: () => null, setItem() {} },
  navigator: {}, console, setTimeout: (fn) => fn && fn(), clearInterval() {}, setInterval: () => 0, requestAnimationFrame() {},
  CustomEvent: function(){}, CSS: { escape: (s) => s },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { applyStoredSectionCollapse, saveSectionCollapse, saveSectionOrder, initDashboardSections, setDashboardGroupsCollapse } = sandbox;

ok(typeof saveSectionCollapse === "function", "app.js expone saveSectionCollapse");
ok(typeof applyStoredSectionCollapse === "function", "app.js expone applyStoredSectionCollapse");

// ── Round-trip: guardar estado colapsado y re-aplicarlo sobre un panel
//    recién re-renderizado (simula el swap: nueva sección, abierta por default) ─
{
  const key = "dw-dash-collapse:cchh-candidatos-list";
  const s1 = makeSection("candidatos", true);   // abierta
  const c1 = makeContainer("cchh-candidatos-list", [s1], true);
  // usuario COLAPSA -> el atributo open se va -> se persiste
  s1.removeAttribute("open");
  saveSectionCollapse(c1, key);
  eq(JSON.parse(store[key]), { candidatos: false }, "persiste el estado colapsado en sessionStorage");

  // SWAP: panel re-renderizado desde el server -> sección nueva, ABIERTA por default
  const s2 = makeSection("candidatos", true);
  const c2 = makeContainer("cchh-candidatos-list", [s2], true);
  applyStoredSectionCollapse(c2, key);
  ok(!s2.hasAttribute("open"), "tras el swap, la sección re-renderizada queda COLAPSADA (el estado sobrevivió)");
}

// ── No-regresión: sin data-persist-collapse no hay persistencia declarada
//    (applyStoredSectionCollapse sin estado guardado = no toca nada) ─
{
  const key = "dw-dash-collapse:otro-panel";
  const s = makeSection("x", true);
  const c = makeContainer("otro-panel", [s], false);
  applyStoredSectionCollapse(c, key); // sin estado en store para esta key
  ok(s.hasAttribute("open"), "sin estado guardado, applyStoredSectionCollapse no altera la sección");
}

// ── Orden + colapso persisten JUNTOS (keys sessionStorage independientes) ──
{
  const s = makeSection("candidatos", true);
  // querySelectorAll(":scope > .dashboard-section") lo necesita saveSectionOrder
  const c = makeContainer("cchh-x", [s], true);
  saveSectionOrder(c, "dw-dash-order:cchh-x");
  s.removeAttribute("open");
  saveSectionCollapse(c, "dw-dash-collapse:cchh-x");
  eq(JSON.parse(store["dw-dash-order:cchh-x"]), ["candidatos"], "orden persistido en su propia key");
  eq(JSON.parse(store["dw-dash-collapse:cchh-x"]), { candidatos: false }, "colapso persistido en su propia key (coexisten sin pisarse)");
}

// ── Idempotencia: initDashboardSections dos veces NO duplica listeners ──────
{
  const listenerCounts = {};
  const s = makeSection("candidatos", true);
  const container = {
    dataset: { dashboardKey: "cchh-idem", persistCollapse: "" },
    querySelectorAll: (sel) => {
      if (sel.indexOf("is-drop-target") !== -1) return [];
      if (sel.indexOf("dashboard-section") !== -1) return [s];
      return [];
    },
    addEventListener: (type) => { listenerCounts[type] = (listenerCounts[type] || 0) + 1; },
    insertBefore() {}, appendChild() {},
  };
  const scope = { querySelectorAll: (sel) => (sel.indexOf("data-dashboard-key") !== -1 ? [container] : []) };
  initDashboardSections(scope);
  initDashboardSections(scope);  // segunda pasada (simula otro htmx:load)
  eq(listenerCounts["toggle"], 1, "toggle listener se cablea UNA sola vez (idempotente)");
  eq(listenerCounts["dragstart"], 1, "drag listeners se cablean UNA sola vez (guard dashWired)");
}

// ── Expandir/Contraer TODOS (owner request, Candidate Edit — CCHH): un solo
//    grupo, con persistencia opt-in ────────────────────────────────────────
{
  const key = "dw-dash-collapse:grupo-a";
  const s1 = makeSection("x", false), s2 = makeSection("y", false); // ambas colapsadas
  const containerA = makeContainer("grupo-a", [s1, s2], true); // data-persist-collapse
  sandbox.document.querySelector = (sel) => (sel === '[data-dashboard-key="grupo-a"]' ? containerA : null);
  setDashboardGroupsCollapse(["grupo-a"], true);
  ok(s1.hasAttribute("open") && s2.hasAttribute("open"), "expandir-todos abre TODAS las secciones del grupo");
  eq(JSON.parse(store[key]), { x: true, y: true }, "persiste el estado resultante, MISMO mecanismo que un toggle individual (data-persist-collapse)");

  setDashboardGroupsCollapse(["grupo-a"], false);
  ok(!s1.hasAttribute("open") && !s2.hasAttribute("open"), "contraer-todos cierra TODAS las secciones del grupo");
  eq(JSON.parse(store[key]), { x: false, y: false }, "persiste el nuevo estado colapsado");
}

// ── MÚLTIPLES grupos con una sola acción (Candidate Edit: 5 bloques +
//    Documentos, dos namespaces de persistencia independientes) ───────────
{
  const sEdit = makeSection("datos-candidato", false);
  const sDocs = makeSection("documentos", false);
  const containerEdit = makeContainer("cchh-candidate-edit", [sEdit], true);
  const containerDocs = makeContainer("cchh-candidate-documents", [sDocs], true);
  sandbox.document.querySelector = (sel) => {
    if (sel === '[data-dashboard-key="cchh-candidate-edit"]') return containerEdit;
    if (sel === '[data-dashboard-key="cchh-candidate-documents"]') return containerDocs;
    return null;
  };
  setDashboardGroupsCollapse(["cchh-candidate-edit", "cchh-candidate-documents"], true);
  ok(sEdit.hasAttribute("open"), "grupo 1 (edición) se expande");
  ok(sDocs.hasAttribute("open"), "grupo 2 (documentos) se expande EN LA MISMA acción");
  eq(JSON.parse(store["dw-dash-collapse:cchh-candidate-edit"]), { "datos-candidato": true }, "cada grupo persiste en SU PROPIA key, sin pisarse");
  eq(JSON.parse(store["dw-dash-collapse:cchh-candidate-documents"]), { documentos: true }, "namespace de Documentos independiente del de edición");
}

// ── Sin `data-persist-collapse`: expandir/contraer-todos sigue funcionando
//    (toca el DOM) pero NO escribe en sessionStorage (no-regresión, MISMO
//    criterio que un toggle individual) ────────────────────────────────────
{
  delete store["dw-dash-collapse:grupo-sin-persistencia"];
  const s = makeSection("z", true);
  const container = makeContainer("grupo-sin-persistencia", [s], false); // SIN persist
  sandbox.document.querySelector = (sel) => (sel === '[data-dashboard-key="grupo-sin-persistencia"]' ? container : null);
  setDashboardGroupsCollapse(["grupo-sin-persistencia"], false);
  ok(!s.hasAttribute("open"), "el DOM sí se afecta aunque el grupo no persista");
  ok(!("dw-dash-collapse:grupo-sin-persistencia" in store), "sin data-persist-collapse, no se escribe nada en sessionStorage");
}

// ── Grupo inexistente: no rompe (defensivo) ─────────────────────────────────
{
  sandbox.document.querySelector = () => null;
  let threw = false;
  try { setDashboardGroupsCollapse(["no-existe"], true); } catch (e) { threw = true; }
  ok(!threw, "un key sin contenedor real en el DOM no rompe (no-op silencioso)");
}

// ── Expandir/Contraer-todos NUNCA toca el orden persistido (keys totalmente
//    independientes) ────────────────────────────────────────────────────────
{
  const s = makeSection("candidatos", true);
  const container = makeContainer("cchh-orden-intacto", [s], true);
  saveSectionOrder(container, "dw-dash-order:cchh-orden-intacto");
  sandbox.document.querySelector = (sel) => (sel === '[data-dashboard-key="cchh-orden-intacto"]' ? container : null);
  setDashboardGroupsCollapse(["cchh-orden-intacto"], false);
  eq(JSON.parse(store["dw-dash-order:cchh-orden-intacto"]), ["candidatos"], "el orden persistido queda intocado por expandir/contraer-todos");
}

if (failures === 0) { console.log("ALL DASHBOARD SECTION COLLAPSE PERSISTENCE INVARIANTS PASSED"); process.exit(0); }
else { console.log(failures + " DASHBOARD SECTION COLLAPSE INVARIANT(S) FAILED"); process.exit(1); }
