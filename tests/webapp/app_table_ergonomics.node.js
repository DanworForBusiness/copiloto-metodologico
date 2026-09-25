// app_table_ergonomics.node.js — invocado por
// test_app_table_ergonomics.py.
//
// CONTRATO (Director §18-27/§36, ver HANDOFF commercial_licensing_20260925):
// reorder + resize de columnas como UN primitivo compartido, opt-in, que no
// sabe nada de negocio — el mismo que ya reordena el Perfil de licenciamiento
// se generaliza acá para probar que es genuinamente TABLE-AGNOSTIC (una tabla
// sintética sin relación con Comercial), no una segunda copia disfrazada.
//
// No hay jsdom en el repo (ver HANDOFF): este harness trae su propio DOM
// mínimo, con lo justo que `initDraggableColumns`/`initResizableColumns`
// (app.js) tocan de verdad — dataset mutable, classList con estado real,
// selectores por atributo/clase/tag compuestos (`table > colgroup > col[...]`,
// `:scope > .clase`), y un dispatch de click que PROPAGA (target → ancestros),
// igual de real que el de commercial_licensing_action_wiring.node.js, para
// probar la frontera de eventos, no asumirla.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, e, label) {
  if (JSON.stringify(a) !== JSON.stringify(e)) {
    failures++; console.log(`FAIL: ${label} — esperado ${JSON.stringify(e)}, obtenido ${JSON.stringify(a)}`);
  }
}

// ── Mini-DOM con dataset/classList/selectores reales ───────────────────────
function makeNode(tag, attrs) {
  const node = {
    tag, attrs: attrs || {}, children: [], parent: null, style: {},
    _listeners: Object.create(null),
    dataset: new Proxy({}, {
      get(_, prop) { return node.attrs[`data-${camelToKebab(prop)}`]; },
      set(_, prop, value) { node.attrs[`data-${camelToKebab(prop)}`] = String(value); return true; },
    }),
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    appendChild(child) { child.parent = node; node.children.push(child); return child; },
    getAttribute(n) { return n in node.attrs ? node.attrs[n] : null; },
    setAttribute(n, v) { node.attrs[n] = String(v); },
    getBoundingClientRect() { return { width: Number(node.attrs["data-test-width"] || 100) }; },
    setPointerCapture() {},
    addEventListener(type, cb) { (node._listeners[type] = node._listeners[type] || []).push(cb); },
    // `dispatchEvent` real: invoca los listeners del propio nodo y, si el
    // evento declara `bubbles`, sigue subiendo por `parent` — MISMO contrato
    // que initDraggableColumns/initResizableColumns asumen al hacer
    // `container.dispatchEvent(new CustomEvent(..., { bubbles: true }))`.
    dispatchEvent(event) {
      let n = node;
      while (n) {
        (n._listeners[event.type] || []).forEach(cb => cb(event));
        if (!event.bubbles) break;
        n = n.parent;
      }
      return true;
    },
    contains(other) { let n = other; while (n) { if (n === node) return true; n = n.parent; } return false; },
    closest(sel) { let n = node; while (n) { if (matches(n, sel)) return n; n = n.parent; } return null; },
    querySelector(sel) { return queryAll(node, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(node, sel); },
  };
  // `className` sincroniza con `classList` (en un DOM real son la MISMA
  // fuente de verdad) — el código bajo prueba usa `handle.className = "..."`,
  // no `classList.add(...)`, así que sin este puente `classList.contains`
  // nunca vería lo que `className` asignó.
  Object.defineProperty(node, "className", {
    get() { return [...node.classList._set].join(" "); },
    set(value) { node.classList._set = new Set(String(value).split(/\s+/).filter(Boolean)); },
  });
  return node;
}
function camelToKebab(s) { return s.replace(/[A-Z]/g, m => "-" + m.toLowerCase()); }
function matches(n, sel) {
  sel = sel.trim();
  if (sel.startsWith(":scope > ")) sel = sel.slice(9);
  const m = sel.match(/^([a-z]*)(\.[a-z-]+)?(\[([a-z-]+)(?:="([^"]*)")?\])?$/i);
  if (!m) return false;
  const [, tag, cls, , attr, val] = m;
  if (tag && n.tag !== tag) return false;
  if (cls && !n.classList.contains(cls.slice(1))) return false;
  if (attr) {
    if (!(attr in n.attrs)) return false;
    if (val !== undefined && n.attrs[attr] !== val) return false;
  }
  return true;
}
// Soporta un único combinador directo `a > b > c` (lo único que
// initResizableColumns usa: `table > colgroup > col[...]`) además de un
// selector simple recorriendo TODO el subárbol (lo que initDraggableColumns/
// initResizableColumns usan para `[data-column-key]`/`[data-*-columns-key]`).
function queryAll(root, sel) {
  const parts = sel.split(">").map(s => s.trim());
  if (parts.length === 1) {
    const out = [];
    (function walk(n) { n.children.forEach(c => { if (matches(c, parts[0])) out.push(c); walk(c); }); })(root);
    return out;
  }
  let level = [root];
  for (const part of parts) {
    const next = [];
    level.forEach(n => n.children.forEach(c => { if (matches(c, part)) next.push(c); }));
    level = next;
  }
  return level;
}
function dispatchClick(target) {
  let stopped = false;
  const event = { target, stopPropagation() { stopped = true; }, preventDefault() {} };
  let n = target;
  while (n && !stopped) {
    (n._listeners.click || []).forEach(cb => cb(event));
    n = n.parent;
  }
  return { reachedRoot: !stopped };
}
function dispatchOn(node, type, event) {
  (node._listeners[type] || []).forEach(cb => cb(event));
}

// ── Carga del módulo REAL ───────────────────────────────────────────────────
const store = Object.create(null);
const localStorage = {
  getItem(k) { return k in store ? store[k] : null; },
  setItem(k, v) { store[k] = String(v); },
  removeItem(k) { delete store[k]; },
};
// `document` de nivel de MÓDULO (app.js corre código top-level al cargar,
// fuera de cualquier función bajo prueba) — sólo necesita no explotar; nada
// de lo que ese código dispare en carga es parte de este contrato.
const documentDouble = {
  createElement: tag => makeNode(tag),
  addEventListener() {},
  querySelectorAll() { return []; },
  querySelector() { return null; },
  getElementById() { return null; },
  body: makeNode("body"),
};
function CustomEventDouble(type, opts) {
  this.type = type;
  this.detail = opts && opts.detail;
  this.bubbles = !!(opts && opts.bubbles);
}
const sandbox = {
  document: documentDouble,
  window: { localStorage, addEventListener() {}, setInterval() { return 0; }, clearInterval() {}, requestAnimationFrame() {} },
  localStorage,
  CustomEvent: CustomEventDouble,
  CSS: { escape: s => s.replace(/"/g, '\\"') },
  requestAnimationFrame() {},
  setInterval() { return 0; },
  clearInterval() {},
  setTimeout: (fn) => fn && fn(),
  navigator: {},
  console,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "app.js" });
const { initDraggableColumns, initResizableColumns, readStoredColumnOrder, readStoredColumnWidths } = sandbox;

// ── Helper: arma una tabla sintética SIN relación con Comercial ────────────
// (Director §36/§27: "table-agnostic" — si esto funciona igual que el Perfil
// de licenciamiento, el primitivo no está agazapado detrás de una key/verticals
// hardcodeada.)
function buildSyntheticTable(tableKey, opts = {}) {
  // `root` es el "scope" que initDraggableColumns/initResizableColumns
  // reciben en uso real (siempre `document`, nunca el propio contenedor): las
  // dos funciones hacen `scope.querySelectorAll("[data-...-columns-key]")`,
  // que busca DESCENDIENTES — pasarles el contenedor mismo (que ES quien
  // lleva el atributo) nunca lo encontraría, igual que en un DOM real.
  const root = makeNode("div");
  const container = makeNode("div", {
    ...(opts.draggable !== false ? { "data-draggable-columns-key": tableKey } : {}),
    ...(opts.resizable !== false ? { "data-resizable-columns-key": tableKey } : {}),
  });
  root.appendChild(container);
  const table = makeNode("table"); container.appendChild(table);
  const colgroup = makeNode("colgroup"); table.appendChild(colgroup);
  const thead = makeNode("thead"); table.appendChild(thead);
  const row = makeNode("tr"); thead.appendChild(row);
  const cols = opts.columns || ["alpha", "beta", "gamma"];
  const ths = cols.map(key => {
    const col = makeNode("col", { "data-column-key": key });
    colgroup.appendChild(col);
    // `data-test-width` va en el <th> (no en el <col>): el código bajo
    // prueba mide `th.getBoundingClientRect().width` para el ancho de
    // arranque del resize, nunca el del <col>.
    const th = makeNode("th", { "data-column-key": key, draggable: "true", "data-test-width": "120" });
    row.appendChild(th);
    return th;
  });
  return { root, container, table, colgroup, ths };
}

// ── A: opt-in — un contenedor SIN el atributo queda intacto ────────────────
{
  const { root, container, ths } = buildSyntheticTable("ergon-a", { resizable: false });
  initResizableColumns(root);
  ok(ths.every(th => !th.children.length), "A: sin data-resizable-columns-key, no se inyecta ningún handle");
}

// ── B: opt-in — CON el atributo, cada th[data-column-key] recibe un handle,
//     y el handle NO es draggable (frontera con el reorder, no timing hack) ─
{
  const { root, container, ths } = buildSyntheticTable("ergon-b");
  initResizableColumns(root);
  ok(ths.every(th => th.children.length === 1 && th.children[0].classList.contains("col-resize-handle")),
    "B: cada th[data-column-key] recibe exactamente un handle");
  ok(ths.every(th => th.children[0].draggable === false),
    "B: el handle es draggable=false — no puede arrancar un reorder nativo");
}

// ── C: idempotente — correr initResizableColumns dos veces no duplica handles
{
  const { root, container, ths } = buildSyntheticTable("ergon-c");
  initResizableColumns(root);
  initResizableColumns(root);
  ok(ths.every(th => th.children.length === 1), "C: segunda pasada no duplica el handle (opt-in idempotente)");
}

// ── D: resize en vivo — pointerdown+move+up cambia el <col> por data-column-key,
//     NUNCA por posición (mismo test correría igual si se reordenara el DOM) ─
{
  const { root, container, ths, colgroup } = buildSyntheticTable("ergon-d");
  initResizableColumns(root);
  const betaHandle = ths[1].children[0];
  dispatchOn(container, "pointerdown", { target: betaHandle, clientX: 300, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 340 });
  dispatchOn(container, "pointerup", {});
  const betaCol = colgroup.children.find(c => c.dataset.columnKey === "beta");
  const alphaCol = colgroup.children.find(c => c.dataset.columnKey === "alpha");
  eq(betaCol.style.width, "160px", "D: el <col> de la columna arrastrada (identificada por key) cambia de ancho en vivo");
  ok(alphaCol.style.width === undefined, "D: ninguna OTRA columna se toca");
}

// ── E: persistencia — el ancho queda en localStorage bajo dw-col-widths:<key>,
//     namespaced independientemente de otra tabla con OTRA key ──────────────
{
  const { root: r1, container: c1 } = buildSyntheticTable("ergon-e1");
  const { root: r2, container: c2 } = buildSyntheticTable("ergon-e2");
  initResizableColumns(r1); initResizableColumns(r2);
  const handle1 = queryAll(c1, "th[data-column-key]")[0].children[0];
  dispatchOn(c1, "pointerdown", { target: handle1, clientX: 0, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(c1, "pointermove", { clientX: 50 });
  dispatchOn(c1, "pointerup", {});
  ok(localStorage.getItem("dw-col-widths:ergon-e1") !== null, "E: persiste bajo la key de la tabla 1");
  ok(localStorage.getItem("dw-col-widths:ergon-e2") === null, "E: la tabla 2 (key distinta) no se contamina");
  const saved = JSON.parse(localStorage.getItem("dw-col-widths:ergon-e1"));
  ok(typeof saved === "object" && !Array.isArray(saved), "E: se persiste como objeto plano {key: width}");
}

// ── F: evento dw:columns-resized se dispara con key/width, bubbles ─────────
{
  const { root, container } = buildSyntheticTable("ergon-f");
  initResizableColumns(root);
  let detail = null;
  container.addEventListener("dw:columns-resized", e => { detail = e.detail; });
  const handle = queryAll(container, "th[data-column-key]")[0].children[0];
  dispatchOn(container, "pointerdown", { target: handle, clientX: 0, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 10 });
  dispatchOn(container, "pointerup", {});
  ok(detail && detail.key === "alpha", "F: dw:columns-resized informa la key de la columna redimensionada");
}

// ── G: FRONTERA DE EVENTOS — un click que se origina en el handle NUNCA llega
//     a un listener puesto en el header (que podría ser un sort-on-click) ──
{
  const { root, container, ths } = buildSyntheticTable("ergon-g");
  initResizableColumns(root);
  let sortFired = false;
  ths[0].addEventListener("click", () => { sortFired = true; });
  const handle = ths[0].children[0];
  const { reachedRoot } = dispatchClick(handle);
  ok(!sortFired, "G: el click en el handle nunca dispara el 'sort' del header que lo contiene");
  ok(!reachedRoot, "G: la propagación se corta explícitamente en el handle (stopPropagation), no por timing");
}

// ── H: reorder sigue table-agnostic — MISMO primitivo, tabla sintética SIN
//     relación con Comercial, dispara dw:columns-reordered con el nuevo orden
//     y persiste independiente de cualquier otra tabla ─────────────────────
{
  const { root, container, ths } = buildSyntheticTable("ergon-h");
  initDraggableColumns(root);
  let order = null;
  container.addEventListener("dw:columns-reordered", e => { order = e.detail.order; });
  // drag "gamma" -> soltar sobre "alpha" (primero)
  dispatchOn(container, "dragstart", { target: ths[2], dataTransfer: {} });
  dispatchOn(container, "dragover", { target: ths[0], preventDefault(){} });
  dispatchOn(container, "drop", { target: ths[0], preventDefault(){} });
  eq(order, ["gamma", "alpha", "beta"], "H: reorder sigue funcionando en una tabla sin relación con Comercial (table-agnostic real)");
  ok(readStoredColumnOrder("ergon-h", ["alpha", "beta", "gamma"]) !== null, "H: el orden nuevo quedó persistido bajo dw-col-order:<key>");
}

// ── I: readStoredColumnWidths — fail-open ante storage corrupto/con forma
//     inválida (mismo criterio que readStoredColumnOrder para el orden) ────
{
  localStorage.setItem("dw-col-widths:ergon-i-bad-json", "{not json");
  localStorage.setItem("dw-col-widths:ergon-i-array", "[1,2,3]");
  localStorage.setItem("dw-col-widths:ergon-i-null", "null");
  localStorage.setItem("dw-col-widths:ergon-i-ok", JSON.stringify({ alpha: "140px" }));
  eq(readStoredColumnWidths("ergon-i-bad-json"), null, "I: JSON corrupto -> null (fail-open)");
  eq(readStoredColumnWidths("ergon-i-array"), null, "I: un array (no un objeto plano) -> null");
  eq(readStoredColumnWidths("ergon-i-null"), null, "I: `null` guardado -> null");
  eq(readStoredColumnWidths("ergon-i-ok"), { alpha: "140px" }, "I: forma válida se lee tal cual");
  eq(readStoredColumnWidths("ergon-i-missing"), null, "I: key nunca usada -> null");
}

// ── J: resize handle no dispara reorder ni deja "colgado" un drag ──────────
{
  const { root, container, ths } = buildSyntheticTable("ergon-j");
  initDraggableColumns(root);
  initResizableColumns(root);
  let reordered = false;
  container.addEventListener("dw:columns-reordered", () => { reordered = true; });
  const handle = ths[0].children[0];
  // El handle es draggable=false: en un browser real esto le impide al motor
  // de HTML5 DnD siquiera emitir dragstart para un gesto iniciado ahí. Acá lo
  // afirmamos directamente sobre el estado del nodo (ver test B) y confirmamos
  // que un resize normal (pointerdown/move/up) nunca dispara el evento de
  // reorder.
  dispatchOn(container, "pointerdown", { target: handle, clientX: 0, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 20 });
  dispatchOn(container, "pointerup", {});
  ok(!reordered, "J: un resize nunca dispara dw:columns-reordered");
}

if (failures > 0) {
  console.log(`${failures} app.js table ergonomics invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL APP TABLE ERGONOMICS INVARIANTS PASSED");
process.exit(0);
