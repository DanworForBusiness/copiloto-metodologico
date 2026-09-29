// star_rating.node.js — invocado por test_star_rating.py.
//
// CONTRATO (owner closure, control 1..5 de `potential_rating`): primitivo
// COMPARTIDO de estrellas. Editable -> hover acumulativo (preview), mouseleave
// restaura el valor commiteado, click commitea, teclado (←/↓ baja, →/↑ sube,
// 1..N fija). El valor posteado es un entero en un <input> hidden (vacío = sin
// rating). Se carga el `app.js` REAL en un sandbox (MISMO patrón que
// indicator_toggles.node.js) y se ejercita `initStarRatings` contra un mini-DOM.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(c, l) { if (!c) { failures++; console.log("FAIL: " + l); } }
function eq(a, e, l) { if (JSON.stringify(a) !== JSON.stringify(e)) { failures++; console.log(`FAIL: ${l} — esperado ${JSON.stringify(e)}, obtuvo ${JSON.stringify(a)}`); } }

// ── Mini-DOM ───────────────────────────────────────────────────────────────
function makeStar(idx) {
  const listeners = {};
  const cls = new Set();
  return {
    dataset: { starIndex: String(idx) },
    tabIndex: -1,
    _attrs: {},
    _focused: false,
    classList: { toggle(n, on) { if (on) cls.add(n); else cls.delete(n); }, contains: (n) => cls.has(n) },
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k]; },
    focus() { this._focused = true; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    _fire(t, ev) { (listeners[t] || []).forEach(fn => fn(ev || {})); },
  };
}
function makeGroup(value, stars) {
  const listeners = {};
  const input = { value: value ? String(value) : "" };
  return {
    dataset: { starValue: String(value || 0) },
    _input: input,
    querySelector(sel) { return sel.indexOf("data-star-input") !== -1 ? input : null; },
    querySelectorAll(sel) { return sel.indexOf("data-star-index") !== -1 ? stars : []; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    _fire(t, ev) { (listeners[t] || []).forEach(fn => fn(ev || {})); },
  };
}
function makeScope(group) {
  return { querySelectorAll(sel) { return sel.indexOf("data-star-rating") !== -1 ? [group] : []; } };
}
function filled(stars) { return stars.filter(s => s.classList.contains("is-filled")).length; }
function key(k) { return { key: k, preventDefault() {} }; }

// ── Cargar app.js real en sandbox (MISMO idioma que indicator_toggles) ──────
const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");
const noopEl = { classList: { add() {}, remove() {}, contains: () => false }, addEventListener() {}, remove() {}, appendChild() {} };
const sandbox = {
  sessionStorage: { getItem: () => null, setItem() {} },
  localStorage: { getItem: () => null, setItem() {} },
  document: {
    addEventListener() {}, createElement: () => noopEl,
    querySelectorAll: () => [], querySelector: () => null,
    getElementById: () => null, body: { addEventListener() {} },
  },
  window: { addEventListener() {}, setInterval: () => 0, clearInterval() {}, requestAnimationFrame() {}, cancelAnimationFrame() {} },
  navigator: {}, console, setTimeout: (fn) => fn && fn(), clearInterval() {}, setInterval: () => 0,
  requestAnimationFrame() {}, cancelAnimationFrame() {}, CustomEvent: function () {}, CSS: { escape: (s) => s },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { initStarRatings } = sandbox;

ok(typeof initStarRatings === "function", "app.js expone initStarRatings");

// ── Render inicial: value=3 -> 3 estrellas llenas ───────────────────────────
{
  const stars = [1, 2, 3, 4, 5].map(makeStar);
  const group = makeGroup(3, stars);
  initStarRatings(makeScope(group));
  eq(filled(stars), 3, "render inicial value=3 -> 3 llenas");
  // (aria-checked inicial lo pone el server en el markup del macro, no el JS.)
}

// ── Hover acumulativo + mouseleave restaura ─────────────────────────────────
{
  const stars = [1, 2, 3, 4, 5].map(makeStar);
  const group = makeGroup(3, stars);
  initStarRatings(makeScope(group));
  stars[3]._fire("mouseenter");                 // hover estrella 4
  eq(filled(stars), 4, "hover estrella 4 -> preview 4 llenas");
  group._fire("mouseleave");                     // salir
  eq(filled(stars), 3, "mouseleave -> restaura al commiteado (3)");
}

// ── Click commitea + actualiza el input hidden ──────────────────────────────
{
  const stars = [1, 2, 3, 4, 5].map(makeStar);
  const group = makeGroup(3, stars);
  initStarRatings(makeScope(group));
  stars[3]._fire("click");                       // click estrella 4
  eq(group._input.value, "4", "click estrella 4 -> input=4");
  eq(filled(stars), 4, "click estrella 4 -> 4 llenas");
  eq(stars[3].getAttribute("aria-checked"), "true", "commit -> estrella 4 aria-checked=true");
  eq(stars[2].getAttribute("aria-checked"), "false", "commit -> estrella 3 aria-checked=false");
  group._fire("mouseleave");
  eq(filled(stars), 4, "tras commit, mouseleave mantiene 4 (nuevo commiteado)");
}

// ── Teclado: flechas y dígitos ──────────────────────────────────────────────
{
  const stars = [1, 2, 3, 4, 5].map(makeStar);
  const group = makeGroup(4, stars);
  initStarRatings(makeScope(group));
  stars[3]._fire("keydown", key("ArrowLeft"));   // 4 -> 3
  eq(group._input.value, "3", "ArrowLeft desde 4 -> 3");
  stars[2]._fire("keydown", key("ArrowRight"));  // 3 -> 4
  eq(group._input.value, "4", "ArrowRight -> 4");
  stars[0]._fire("keydown", key("5"));           // set 5
  eq(group._input.value, "5", "dígito 5 -> 5");
}

// ── Sin rating (value=0): 0 llenas; click estrella 2 -> 2 ───────────────────
{
  const stars = [1, 2, 3, 4, 5].map(makeStar);
  const group = makeGroup(0, stars);
  initStarRatings(makeScope(group));
  eq(filled(stars), 0, "value=0 -> 0 llenas (sin rating)");
  eq(group._input.value, "", "value=0 -> input vacío");
  stars[1]._fire("click");                        // click estrella 2
  eq(group._input.value, "2", "click estrella 2 desde vacío -> 2");
}

// ── ArrowLeft desde 1 puede llegar a 0 (campo opcional, se limpia) ──────────
{
  const stars = [1, 2, 3, 4, 5].map(makeStar);
  const group = makeGroup(1, stars);
  initStarRatings(makeScope(group));
  stars[0]._fire("keydown", key("ArrowLeft"));   // 1 -> 0
  eq(group._input.value, "", "ArrowLeft desde 1 -> 0 (input vacío, sin rating)");
  eq(filled(stars), 0, "0 llenas tras limpiar");
}

if (failures === 0) { console.log("ALL STAR RATING INVARIANTS PASSED"); process.exit(0); }
else { console.log(failures + " STAR RATING INVARIANT(S) FAILED"); process.exit(1); }
