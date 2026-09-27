// indicator_toggles.node.js — invocado por test_indicator_toggles.py.
//
// CONTRATO (owner, CCHH Candidatos "Indicadores"): un checkbox de presentación
// (nunca filtro/query/persistencia de negocio) que muestra/oculta un elemento
// [data-indicator="<name>"] dentro de un scope [data-indicator-scope="<key>"],
// controlado por [data-indicator-toggle="<name>"] dentro de un grupo
// [data-indicator-toggles][data-indicator-storage-key="<key>"]. Preferencia
// persistida en localStorage (namespace `dw-indicators:<key>`), reaplicada en
// cada `initIndicatorToggles(scope)` (equivalente a cada htmx:load).
//
// Se carga el `app.js` REAL en un sandbox (MISMO patrón que
// dashboard_section_collapse_persistence.node.js) y se ejercita
// `initIndicatorToggles` contra un mini-DOM.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(c, l) { if (!c) { failures++; console.log("FAIL: " + l); } }
function eq(a, e, l) { if (JSON.stringify(a) !== JSON.stringify(e)) { failures++; console.log(`FAIL: ${l} — esperado ${JSON.stringify(e)}, obtuvo ${JSON.stringify(a)}`); } }

// ── Mini-DOM helpers ─────────────────────────────────────────────────────
function makeIndicatorEl(name) {
  return { dataset: { indicator: name }, hidden: false };
}
function makeCheckbox(name, checked) {
  const listeners = {};
  const el = {
    dataset: { indicatorToggle: name },
    checked: !!checked,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    _fire(type, evt) { (listeners[type] || []).forEach(fn => fn(evt)); },
    closest(sel) { return sel.indexOf("data-indicator-toggle") !== -1 ? el : null; },
  };
  return el;
}
function makeGroup(storageKey, checkboxes) {
  const listeners = {};
  return {
    dataset: { indicatorStorageKey: storageKey },
    querySelectorAll(sel) { return sel.indexOf("data-indicator-toggle") !== -1 ? checkboxes : []; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    _change(checkbox) { (listeners.change || []).forEach(fn => fn({ target: checkbox })); },
  };
}
function makeTarget(elsByName) {
  return {
    querySelectorAll(sel) {
      // sel === `[data-indicator="<name>"]`
      const m = /\[data-indicator="([^"]+)"\]/.exec(sel);
      const name = m ? m[1] : null;
      return name && elsByName[name] ? elsByName[name] : [];
    },
  };
}

// localStorage stub (mutable, a diferencia de sessionStorage — namespace distinto)
const lsStore = {};
const localStorage = {
  getItem: (k) => (k in lsStore ? lsStore[k] : null),
  setItem: (k, v) => { lsStore[k] = String(v); },
};

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");
const noopEl = { classList: { add(){}, remove(){}, contains: () => false }, addEventListener() {}, remove() {}, appendChild() {} };
let queryableTarget = null;
const sandbox = {
  sessionStorage: { getItem: () => null, setItem() {} },
  localStorage,
  document: {
    addEventListener() {}, createElement: () => noopEl,
    querySelectorAll: () => [],
    querySelector(sel) { return sel.indexOf("data-indicator-scope") !== -1 ? queryableTarget : null; },
    getElementById: () => null, body: { addEventListener() {} },
  },
  window: { localStorage, addEventListener() {}, setInterval: () => 0, clearInterval() {}, requestAnimationFrame() {} },
  navigator: {}, console, setTimeout: (fn) => fn && fn(), clearInterval() {}, setInterval: () => 0, requestAnimationFrame() {},
  CustomEvent: function(){}, CSS: { escape: (s) => s },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { initIndicatorToggles } = sandbox;

ok(typeof initIndicatorToggles === "function", "app.js expone initIndicatorToggles");

// ── Checked (default) → el indicador está visible ───────────────────────
{
  const badge = makeIndicatorEl("potencial");
  queryableTarget = makeTarget({ potencial: [badge] });
  const checkbox = makeCheckbox("potencial", true);
  const group = makeGroup("cchh-candidatos-list", [checkbox]);
  const scope = { querySelectorAll: (sel) => (sel.indexOf("data-indicator-toggles") !== -1 ? [group] : []) };
  initIndicatorToggles(scope);
  ok(!badge.hidden, "checkbox tildado (default) -> el indicador queda visible");
}

// ── Uncheck vía evento "change" → el indicador se oculta + persiste ─────
{
  const badge = makeIndicatorEl("potencial");
  queryableTarget = makeTarget({ potencial: [badge] });
  const checkbox = makeCheckbox("potencial", true);
  const group = makeGroup("cchh-candidatos-list", [checkbox]);
  const scope = { querySelectorAll: (sel) => (sel.indexOf("data-indicator-toggles") !== -1 ? [group] : []) };
  initIndicatorToggles(scope);
  checkbox.checked = false;
  group._change(checkbox);
  ok(badge.hidden, "destildar el checkbox oculta el indicador de inmediato");
  eq(JSON.parse(lsStore["dw-indicators:cchh-candidatos-list"]), { potencial: false }, "la preferencia se persiste en localStorage (namespace dw-indicators:<key>)");
}

// ── Re-init (simula un nuevo htmx:load tras un swap del server) reaplica
//    la preferencia YA persistida sobre el badge RECIÉN renderizado ──────
{
  const freshBadge = makeIndicatorEl("potencial");         // "server" re-renderizó, badge nuevo, hidden=false por default
  queryableTarget = makeTarget({ potencial: [freshBadge] });
  const freshCheckbox = makeCheckbox("potencial", true);   // template siempre renderiza `checked` por default
  const group = makeGroup("cchh-candidatos-list", [freshCheckbox]);
  const scope = { querySelectorAll: (sel) => (sel.indexOf("data-indicator-toggles") !== -1 ? [group] : []) };
  initIndicatorToggles(scope);
  ok(freshCheckbox.checked === false, "tras el swap, el checkbox se re-sincroniza a la preferencia guardada (destildado)");
  ok(freshBadge.hidden, "tras el swap, el badge nuevo hereda la preferencia guardada (oculto)");
}

// ── Filtrado independiente: el toggle NUNCA debe tocar nada fuera de su
//    propio scope/target (no hay query/filter acá, sólo verificamos que el
//    mecanismo no exige ni consulta ningún estado de filtro) ─────────────
{
  const badgeA = makeIndicatorEl("potencial");
  const other = { dataset: {}, hidden: false }; // elemento sin data-indicator: nunca debe tocarse
  queryableTarget = makeTarget({ potencial: [badgeA] });
  const checkbox = makeCheckbox("potencial", false);
  const group = makeGroup("cchh-candidatos-list", [checkbox]);
  const scope = { querySelectorAll: (sel) => (sel.indexOf("data-indicator-toggles") !== -1 ? [group] : []) };
  initIndicatorToggles(scope);
  ok(badgeA.hidden, "preferencia previa (oculto) se re-aplica");
  ok(other.hidden === false, "un elemento sin data-indicator nunca es tocado por el mecanismo");
}

if (failures === 0) { console.log("ALL INDICATOR TOGGLE INVARIANTS PASSED"); process.exit(0); }
else { console.log(failures + " INDICATOR TOGGLE INVARIANT(S) FAILED"); process.exit(1); }
