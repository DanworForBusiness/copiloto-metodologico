// human_capital_candidates_selection_consolidate.node.js — invocado por test_profile_consolidation_routes.py.
//
// «Consolidar perfiles» (solo el dueño) en la barra de selección de CCHH · Candidatos (`human_capital_candidates_selection.js`): el botón se habilita con EXACTAMENTE dos perfiles seleccionados, se
// deshabilita con 0, 1 o 3+, y al accionarlo envía por POST (formulario con campos ocultos, NO por la URL) SOLO los dos ids a `/cchh/candidatos/consolidar`.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "human_capital_candidates_selection.js"), "utf-8");
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "human_capital_candidates_selection.js" });
const api = sandbox.window.__cchhSelection;

function el(sels, attrs) {
  const node = {
    sels: sels || [], attrs: Object.assign({}, attrs || {}), checked: false, indeterminate: false, disabled: false, hidden: false, textContent: "", value: "", parent: null, q: {}, children: [],
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); } },
    getAttribute(n) { return n in node.attrs ? node.attrs[n] : null; },
    setAttribute(n, v) { node.attrs[n] = String(v); },
    hasAttribute(n) { return n in node.attrs; },
    matches(sel) { return sel.split(",").map(s => s.trim()).some(s => node.sels.includes(s)); },
    closest(sel) { let n = node; while (n) { if (n.matches(sel)) return n; n = n.parent; } return null; },
    querySelector(sel) { return node.q[sel] || null; },
    appendChild(child) { node.children.push(child); child.parent = node; return child; },
  };
  return node;
}
function makeStore(initial) {
  const data = { k: JSON.stringify({ v: 1, ids: initial }) };
  return api.createStore({ getItem: () => data.k, setItem: (_, v) => { data.k = String(v); } });
}
function docWith(storeIds, withButton) {
  const bar = el(["[data-hc-selection-bar]"]);
  ["[data-hc-selection-count]", "[data-hc-selection-label]", "[data-hc-selection-hint]", "[data-hc-share-open]", "[data-hc-download-open]"].forEach(s => { bar.q[s] = el([s]); });
  const button = el(["[data-hc-consolidate-open]"], { "data-hc-consolidate-open": "" });
  if (withButton) bar.q["[data-hc-consolidate-open]"] = button;
  const body = el(["body"]);
  body.addEventListener = function () {};
  const doc = {
    body, bar, button, created: [], submitted: [],
    readyState: "complete", _l: {},
    addEventListener(type, cb) { doc._l[type] = cb; },
    querySelectorAll(sel) { return sel === "[data-hc-selection-bar]" ? [bar] : []; },
    createElement(tag) { const n = el([tag]); n.tag = tag; if (tag === "form") n.submit = function () { doc.submitted.push(n); }; return n; },
  };
  return doc;
}

// A: habilitación según la cantidad seleccionada
for (const [count, enabled] of [[0, false], [1, false], [2, true], [3, false], [26, false]]) {
  const ids = Array.from({ length: count }, (_, i) => `id-${i}`);
  const doc = docWith(ids, true);
  api.syncUI(doc, makeStore(ids));
  ok(doc.button.disabled === !enabled, `A: con ${count} seleccionado(s) el botón ${enabled ? "se habilita" : "queda deshabilitado"}`);
}

// B: un usuario sin el botón (no es el dueño) no rompe nada
{
  const doc = docWith(["a", "b"], false);
  let threw = false;
  try { api.syncUI(doc, makeStore(["a", "b"])); } catch (e) { threw = true; }
  ok(!threw, "B: sin el botón (usuario que no es el dueño) la barra funciona igual");
}

// C: el clic envía por POST solo los dos ids (campos ocultos), nunca por la URL
{
  const doc = docWith(["a", "b"], true);
  const store = makeStore(["a", "b"]);
  api.wire(doc, { fetch() {} }, store);
  const click = doc._l.click;
  ok(typeof click === "function", "C: se registró el delegado de clic");
  if (click) {
    click({ target: { closest: () => doc.button } });
    ok(doc.submitted.length === 1, "C: el clic envía un formulario");
    const form = doc.submitted[0];
    ok(form && form.method === "post" && form.action === "/cchh/candidatos/consolidar", "C: POST a /cchh/candidatos/consolidar (los ids no viajan en la URL)");
    ok(form && form.children.map(c => c.value).sort().join(",") === "a,b" && form.children.every(c => c.name === "profile_ids" && c.type === "hidden"), "C: solo los dos ids como campos ocultos");
    const three = docWith(["a", "b", "c"], true);
    const store3 = makeStore(["a", "b", "c"]);
    api.wire(three, { fetch() {} }, store3);
    three._l.click({ target: { closest: () => three.button } });
    ok(three.submitted.length === 0, "C: con tres seleccionados el clic no envía nada");
  }
}

if (failures > 0) { console.log(`${failures} consolidate selection invariant(s) FAILED`); process.exit(1); }
console.log("ALL CONSOLIDATE SELECTION INVARIANTS PASSED");
process.exit(0);
