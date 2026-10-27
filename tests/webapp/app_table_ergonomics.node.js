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
  // `clientWidth` (T1 compatibility corrective): MISMO `data-test-width` que
  // `getBoundingClientRect().width` ya usa arriba -- un getter vivo, no un
  // valor congelado al crear el nodo, porque los tests de compatibilidad
  // (ver D5/D7/D8) necesitan simular el contenedor midiendo un ancho
  // DISTINTO en el restore que el que tenía al guardar.
  Object.defineProperty(node, "clientWidth", {
    get() { return Number(node.attrs["data-test-width"] || 100); },
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
const { initDraggableColumns, initResizableColumns, readStoredColumnOrder, readStoredColumnWidths, restorePersistedColumnWidths, columnLayoutFor, columnLayoutOfContainer } = sandbox;

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
    ...(opts.layoutVersion ? { "data-layout-version": String(opts.layoutVersion) } : {}),
  });
  root.appendChild(container);
  const table = makeNode("table"); container.appendChild(table);
  const colgroup = makeNode("colgroup"); table.appendChild(colgroup);
  const thead = makeNode("thead"); table.appendChild(thead);
  const row = makeNode("tr"); thead.appendChild(row);
  const cols = opts.columns || ["alpha", "beta", "gamma"];
  const ths = cols.map(key => {
    // `null` = columna SIN key (casilla de selección, Acciones): <col> y <th> sin data-column-key.
    const col = makeNode("col", key ? { "data-column-key": key } : {});
    colgroup.appendChild(col);
    // `data-test-width` va en el <th> (no en el <col>): el código bajo
    // prueba mide `th.getBoundingClientRect().width` para el ancho de
    // arranque del resize, nunca el del <col>.
    const th = makeNode("th", { ...(key ? { "data-column-key": key } : {}), draggable: "true", "data-test-width": "120" });
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
  const { root, container, table, ths, colgroup } = buildSyntheticTable("ergon-d");
  initResizableColumns(root);
  const betaHandle = ths[1].children[0];
  dispatchOn(container, "pointerdown", { target: betaHandle, clientX: 300, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 340 });
  dispatchOn(container, "pointerup", {});
  const betaCol = colgroup.children.find(c => c.dataset.columnKey === "beta");
  const alphaCol = colgroup.children.find(c => c.dataset.columnKey === "alpha");
  eq(betaCol.style.width, "160px", "D: el <col> de la columna arrastrada (identificada por key) cambia de ancho en vivo");
  // Contrato T1 (owner): las columnas < N NO se redistribuyen -- quedan FIJAS
  // en su ancho efectivo, ahora congelado a px explícito (antes quedaba
  // `undefined` y el browser las reescalaba dentro del 100%). Y la tabla crece
  // a max-content (desborda) en vez de clavarse al ancho del contenedor.
  eq(alphaCol.style.width, "120px", "D: la columna < N queda fija en su ancho efectivo (px congelado, sin redistribuir)");
  // Total EXPLÍCITO en px (no max-content): 3 cols × 120 = 360, +40 del resize = 400.
  eq(table.style.width, "400px", "D: la tabla crece exactamente por el delta (px explícito), no se clava al 100%");
}

// ── D2 (T1): resize de la columna DEL MEDIO — < N fijas, N cambia por el
//     delta, > N fijas (se corren por el crecimiento de la tabla), overflow ──
{
  const { root, container, table, ths, colgroup } = buildSyntheticTable("ergon-t1", { columns: ["a", "b", "c", "d"] });
  initResizableColumns(root);
  const bHandle = ths[1].children[0]; // N = "b" (segunda de cuatro)
  dispatchOn(container, "pointerdown", { target: bHandle, clientX: 200, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 250 }); // delta +50
  dispatchOn(container, "pointerup", {});
  const col = k => colgroup.children.find(c => c.dataset.columnKey === k);
  eq(col("a").style.width, "120px", "T1: columna < N (a) sin cambio");
  eq(col("b").style.width, "170px", "T1: columna N (b) cambia por el delta (120+50)");
  eq(col("c").style.width, "120px", "T1: columna > N (c) conserva su ancho (se corre por overflow, no se encoge)");
  eq(col("d").style.width, "120px", "T1: columna > N (d) conserva su ancho");
  // Total EXPLÍCITO: 4 cols × 120 = 480, +50 del resize = 530 (crece exactamente por el delta).
  eq(table.style.width, "530px", "T1: total de la tabla += delta (px explícito, overflow permitido)");
}

// ── D3 (T1): delta NEGATIVO — N se achica por el delta, < N/> N fijas, total -= delta ──
{
  const { root, container, table, ths, colgroup } = buildSyntheticTable("ergon-t1neg", { columns: ["a", "b", "c"] });
  initResizableColumns(root);
  dispatchOn(container, "pointerdown", { target: ths[1].children[0], clientX: 200, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 170 }); // delta -30
  dispatchOn(container, "pointerup", {});
  const col = k => colgroup.children.find(c => c.dataset.columnKey === k);
  eq(col("a").style.width, "120px", "T1(-): < N sin cambio");
  eq(col("b").style.width, "90px", "T1(-): N se achica por el delta (120-30)");
  eq(col("c").style.width, "120px", "T1(-): > N sin cambio");
  eq(table.style.width, "330px", "T1(-): total -= delta (360-30)");
}

// ── D4 (T1): min-width clamp — arrastrar N muy por debajo del mínimo lo clampa a 60 ──
{
  const { root, container, table, ths, colgroup } = buildSyntheticTable("ergon-t1clamp", { columns: ["a", "b", "c"] });
  initResizableColumns(root);
  dispatchOn(container, "pointerdown", { target: ths[1].children[0], clientX: 200, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 50 }); // delta -150 -> 120-150 = -30 -> clamp 60
  dispatchOn(container, "pointerup", {});
  const col = k => colgroup.children.find(c => c.dataset.columnKey === k);
  eq(col("b").style.width, "60px", "T1(clamp): N nunca baja de 60px");
  eq(col("a").style.width, "120px", "T1(clamp): < N sin cambio");
  eq(table.style.width, "300px", "T1(clamp): total refleja el ancho clampeado (a120 + b60 + c120)");
}

// ── D5 (T1 reload): restorePersistedColumnWidths es el algoritmo COMPARTIDO —
//     aplica overrides guardados, congela el resto a px y fija el TOTAL
//     explícito (= suma). Vive en el primitivo, no en un consumidor. El
//     envelope v1 (owner post-#107 acceptance) exige `containerWidth`
//     COMPATIBLE con el contenedor actual (acá, ambos 100 -- el default de
//     `clientWidth` del mini-DOM cuando no se setea `data-test-width`). ──
{
  ok(typeof restorePersistedColumnWidths === "function", "D5: app.js expone restorePersistedColumnWidths (algoritmo compartido)");
  const { root, container, table, colgroup } = buildSyntheticTable("ergon-restore", { columns: ["a", "b", "c"] });
  store["dw-col-widths:ergon-restore"] = JSON.stringify({ schemaVersion: 1, containerWidth: container.clientWidth, widths: { b: "200px" } });
  const applied = restorePersistedColumnWidths(container);
  const col = k => colgroup.children.find(c => c.dataset.columnKey === k);
  ok(applied === true, "D5: aplica cuando hay overrides guardados y el contenedor es compatible");
  eq(col("b").style.width, "200px", "D5: la columna con override toma su px");
  eq(col("a").style.width, "120px", "D5: las demás se congelan a su ancho efectivo (px), no quedan sin setear");
  eq(col("c").style.width, "120px", "D5: idem la otra columna");
  eq(table.style.width, "440px", "D5: total EXPLÍCITO = suma (120+200+120), nunca 100% que redistribuye");
}

// ── D6 (T1 reload): sin overrides guardados NO reconstruye (fresh-load intacto) ──
{
  const { root, container, table } = buildSyntheticTable("ergon-norestore", { columns: ["a", "b"] });
  const applied = restorePersistedColumnWidths(container);
  ok(applied === false, "D6: sin overrides guardados devuelve false (no toca la tabla; fresh-load queda width:100%)");
  ok(!table.style.width, "D6: la tabla no recibe ancho inline si no hay nada persistido");
}

// ── D7 (T1 persistence compatibility corrective, owner post-#107 acceptance):
//     ancho de contenedor MATERIALMENTE distinto al guardado -> se descarta
//     la preferencia ENTERA, la tabla queda responsive (nunca un total
//     congelado calculado sobre un layout que ya no existe). ESTE es el bug
//     de producción real: un resize hecho en una ventana angosta dejaba la
//     tabla congelada/comprimida en una ventana ancha (o con overflow
//     evitable en una más angosta), porque antes NUNCA se comparaba el
//     ancho de contenedor guardado contra el actual. ──
{
  const { container, table } = buildSyntheticTable("ergon-incompatible", { columns: ["a", "b"] });
  container.attrs["data-test-width"] = "1000"; // contenedor ACTUAL: 1000px
  // Guardado cuando el contenedor medía 400px -- fuera de la tolerancia del 8%.
  store["dw-col-widths:ergon-incompatible"] = JSON.stringify({ schemaVersion: 1, containerWidth: 400, widths: { a: "200px" } });
  const applied = restorePersistedColumnWidths(container);
  ok(applied === false, "D7: ancho de contenedor materialmente distinto -> se descarta, no se aplica nada");
  ok(!table.style.width, "D7: la tabla queda sin ancho inline -- responsive (width:100% declarativo), nunca un total viejo");
}

// ── D8 (T1 persistence compatibility corrective): estado PRE-CONTRATO (el
//     shape plano `{colKey:"Npx"}` de antes de este corrective, sin
//     schemaVersion/containerWidth) se trata como INCOMPATIBLE -- fail-safe
//     a responsive, NUNCA se re-interpreta/migra en caliente. ──
{
  const { container, table } = buildSyntheticTable("ergon-legacy-shape", { columns: ["a", "b"] });
  store["dw-col-widths:ergon-legacy-shape"] = JSON.stringify({ a: "200px" }); // shape viejo, sin envelope
  const applied = restorePersistedColumnWidths(container);
  ok(applied === false, "D8: shape plano pre-contrato -> incompatible, se descarta (fail-safe a responsive)");
  ok(!table.style.width, "D8: la tabla queda sin ancho inline");
  eq(readStoredColumnWidths("ergon-legacy-shape"), null, "D8: readStoredColumnWidths (contrato público) también trata el shape viejo como ausente");
}

// ── D9: ancho de contenedor DENTRO de tolerancia (7% de diferencia, bajo el
//     8% documentado) SÍ se restaura -- la tolerancia no es "exact match". ──
{
  const { container, colgroup } = buildSyntheticTable("ergon-tolerance", { columns: ["a", "b"] });
  container.attrs["data-test-width"] = "1070"; // guardado a 1000, actual 1070 -> 7% de diferencia
  store["dw-col-widths:ergon-tolerance"] = JSON.stringify({ schemaVersion: 1, containerWidth: 1000, widths: { a: "300px" } });
  const applied = restorePersistedColumnWidths(container);
  ok(applied === true, "D9: 7% de diferencia (bajo la tolerancia del 8%) sigue considerándose compatible");
  eq(colgroup.children.find(c => c.dataset.columnKey === "a").style.width, "300px", "D9: el override se aplica igual dentro de tolerancia");
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
  // Envelope v1 (T1 persistence compatibility corrective), nunca el mapa
  // plano crudo -- `containerWidth` es lo que restorePersistedColumnWidths
  // necesita comparar contra el contenedor ACTUAL en un restore futuro.
  ok(typeof saved === "object" && !Array.isArray(saved), "E: se persiste como objeto (envelope), nunca un array");
  eq(saved.schemaVersion, 2, "E: el envelope declara su schemaVersion (formato)");
  eq(saved.layout, { version: 1, signature: "alpha|beta|gamma".split("|").slice().sort().join("|") }, "E: el envelope graba la estructura (versión + firma determinista) de la tabla");
  ok(typeof saved.containerWidth === "number", "E: el envelope graba el ancho de contenedor vigente al guardar");
  ok(typeof saved.widths === "object" && !Array.isArray(saved.widths), "E: los anchos en sí viven en widths, como mapa plano {key: width}");
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
//     inválida (mismo criterio que readStoredColumnOrder para el orden).
//     Owner post-#107 acceptance (T1 persistence compatibility corrective):
//     el shape plano `{colKey:"Npx"}` de ANTES de este corrective es AHORA
//     una forma inválida más -- sin `schemaVersion`/`containerWidth` no hay
//     forma de validar para qué ancho de contenedor son esos px, así que se
//     trata igual que JSON corrupto (null, fail-open), nunca se reinterpreta. ──
{
  localStorage.setItem("dw-col-widths:ergon-i-bad-json", "{not json");
  localStorage.setItem("dw-col-widths:ergon-i-array", "[1,2,3]");
  localStorage.setItem("dw-col-widths:ergon-i-null", "null");
  localStorage.setItem("dw-col-widths:ergon-i-legacy-flat", JSON.stringify({ alpha: "140px" }));
  localStorage.setItem("dw-col-widths:ergon-i-ok", JSON.stringify({ schemaVersion: 1, containerWidth: 500, widths: { alpha: "140px" } }));
  localStorage.setItem("dw-col-widths:ergon-i-wrong-version", JSON.stringify({ schemaVersion: 2, containerWidth: 500, widths: { alpha: "140px" } }));
  eq(readStoredColumnWidths("ergon-i-bad-json"), null, "I: JSON corrupto -> null (fail-open)");
  eq(readStoredColumnWidths("ergon-i-array"), null, "I: un array (no un objeto plano) -> null");
  eq(readStoredColumnWidths("ergon-i-null"), null, "I: `null` guardado -> null");
  eq(readStoredColumnWidths("ergon-i-legacy-flat"), null, "I: shape plano pre-contrato -> null (incompatible, ver D8)");
  eq(readStoredColumnWidths("ergon-i-wrong-version"), null, "I: schemaVersion v2 SIN layout (o con otra versión de formato) -> null (nunca migrado en caliente)");
  eq(readStoredColumnWidths("ergon-i-ok"), { alpha: "140px" }, "I: envelope legacy v1 válido -> devuelve el mapa plano de widths");
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


// ═══════════════ K: COMPATIBILIDAD ESTRUCTURAL de los anchos guardados (hotfix Candidatos) ═══════════════
// Un registro de anchos es válido solo para la ESTRUCTURA de la tabla que lo grabó: ids de columna, lugar de las columnas sin key
// y -en tablas no reordenables- el orden; más un `data-layout-version` explícito. Incompatible => se elimina SOLO ese key.
const WKEY = k => `dw-col-widths:${k}`;
const record = (container, widths, extra = {}) => {
  const l = columnLayoutOfContainer(container);
  return JSON.stringify({ schemaVersion: 2, containerWidth: container.clientWidth, layout: { version: l.version, signature: l.signature }, widths, ...extra });
};
const colW = (colgroup, k) => colgroup.children.find(c => c.dataset.columnKey === k).style.width;

// K1: primera visita (sin nada guardado) -> no aplica, no escribe nada, no crea claves.
{
  const { container, table } = buildSyntheticTable("k-first", { columns: ["a", "b"] });
  const before = Object.keys(store).length;
  ok(restorePersistedColumnWidths(container) === false, "K1: sin preferencias guardadas no aplica nada");
  ok(!table.style.width && Object.keys(store).length === before, "K1: una carga sin preferencias no escribe en localStorage");
}

// K2: registro VÁLIDO para la estructura vigente -> se conserva (se aplica) y la carga NO lo reescribe.
{
  const { container, colgroup } = buildSyntheticTable("k-valid", { columns: ["a", "b", "c"] });
  store[WKEY("k-valid")] = record(container, { b: "210px" });
  const raw = store[WKEY("k-valid")];
  ok(restorePersistedColumnWidths(container) === true, "K2: un registro válido se aplica");
  eq(colW(colgroup, "b"), "210px", "K2: el ancho guardado se respeta");
  ok(store[WKEY("k-valid")] === raw, "K2: la carga no sobrescribe un registro ya válido (cero escrituras)");
}

// K3: se AGREGA una columna (con o sin key) -> incompatible: se descarta SOLO esa clave; la tabla usa sus defaults.
for (const [label, cols] of [["con key", ["a", "b", "z"]], ["sin key (casilla)", [null, "a", "b"]]]) {
  const old = buildSyntheticTable("k-old", { columns: ["a", "b"] });
  const stale = record(old.container, { a: "300px" });                    // grabado cuando la tabla tenía a,b
  const { container, table } = buildSyntheticTable("k-add", { columns: cols });
  store[WKEY("k-add")] = stale;
  store[WKEY("k-other")] = "intacto";                                      // otra tabla
  store["dw-col-order:k-add"] = JSON.stringify(["b", "a"]);                // preferencia INDEPENDIENTE de la misma tabla
  store["hc-filtros"] = "q=ana";                                           // filtros / selección / indicadores: no son de esta capa
  ok(restorePersistedColumnWidths(container) === false, `K3(${label}): estructura con una columna más -> no se aplican los anchos viejos`);
  ok(!table.style.width, `K3(${label}): la tabla queda con su layout responsive por defecto`);
  ok(!(WKEY("k-add") in store), `K3(${label}): se elimina el registro incompatible de ESA tabla`);
  ok(store[WKEY("k-other")] === "intacto" && store["dw-col-order:k-add"] && store["hc-filtros"] === "q=ana", `K3(${label}): no toca otras tablas ni preferencias independientes`);
  delete store["dw-col-order:k-add"]; delete store["hc-filtros"]; delete store[WKEY("k-other")];
}

// K4: se ELIMINA una columna -> incompatible. REORDENAMIENTO: tabla no reordenable => incompatible; tabla reordenable por el usuario => compatible.
{
  const full = buildSyntheticTable("k-del", { columns: ["a", "b", "c"], draggable: false });
  const { container } = buildSyntheticTable("k-del", { columns: ["a", "c"], draggable: false });
  store[WKEY("k-del")] = record(full.container, { a: "150px" });
  ok(restorePersistedColumnWidths(container) === false && !(WKEY("k-del") in store), "K4: una columna menos -> incompatible y descartado");

  const fixed = buildSyntheticTable("k-fixed", { columns: ["a", "b", "c"], draggable: false });
  const swapped = buildSyntheticTable("k-fixed", { columns: ["c", "b", "a"], draggable: false });
  store[WKEY("k-fixed")] = record(fixed.container, { a: "150px" });
  ok(restorePersistedColumnWidths(swapped.container) === false, "K4: en una tabla NO reordenable, otro orden es otra estructura -> incompatible");

  const drag = buildSyntheticTable("k-drag", { columns: ["a", "b", "c"] });
  const dragSwapped = buildSyntheticTable("k-drag", { columns: ["c", "a", "b"] });
  store[WKEY("k-drag")] = record(drag.container, { a: "150px" });
  ok(restorePersistedColumnWidths(dragSwapped.container) === true, "K4: en una tabla REORDENABLE por el usuario, reordenar las columnas NO invalida los anchos (van por key)");
  ok(columnLayoutFor(["x", "", "y"], { reorderable: true }).signature === columnLayoutFor(["y", "", "x"], { reorderable: true }).signature, "K4: la firma reordenable es invariante a permutar las columnas con key");
  ok(columnLayoutFor(["x", "", "y"], { reorderable: true }).signature !== columnLayoutFor(["", "x", "y"], { reorderable: true }).signature, "K4: pero sí distingue DÓNDE están las columnas sin key");
}

// K5: cambio EXPLÍCITO de layout sin cambiar identificadores (data-layout-version).
{
  const v1 = buildSyntheticTable("k-ver", { columns: ["a", "b"] });
  const v2 = buildSyntheticTable("k-ver", { columns: ["a", "b"], layoutVersion: 2 });
  store[WKEY("k-ver")] = record(v1.container, { a: "150px" });
  ok(restorePersistedColumnWidths(v2.container) === false && !(WKEY("k-ver") in store), "K5: mismas columnas pero layoutVersion 2 vs registro v1 -> incompatible");
  store[WKEY("k-ver")] = record(v2.container, { a: "150px" });
  ok(restorePersistedColumnWidths(v2.container) === true, "K5: el registro grabado bajo la versión vigente sí se aplica");
}

// K6: registros LEGACY (schemaVersion 1, sin firma).
{
  // (a) la tabla sigue en layout v1 y las columnas nombradas existen -> se conserva y se firma UNA vez, sin tocar widths/containerWidth.
  const { container, colgroup } = buildSyntheticTable("k-legacy-ok", { columns: ["a", "b"] });
  store[WKEY("k-legacy-ok")] = JSON.stringify({ schemaVersion: 1, containerWidth: container.clientWidth, widths: { a: "160px" } });
  ok(restorePersistedColumnWidths(container) === true && colW(colgroup, "a") === "160px", "K6a: un registro legacy compatible con una tabla que no cambió se conserva");
  const stamped = JSON.parse(store[WKEY("k-legacy-ok")]);
  eq([stamped.schemaVersion, stamped.widths, stamped.containerWidth, stamped.layout.version], [2, { a: "160px" }, container.clientWidth, 1], "K6a: queda firmado con la estructura vigente (mismos widths)");
  const again = store[WKEY("k-legacy-ok")];
  restorePersistedColumnWidths(buildSyntheticTable("k-legacy-ok", { columns: ["a", "b"] }).container);
  ok(store[WKEY("k-legacy-ok")] === again, "K6a: ya firmado, una nueva carga no vuelve a escribir");
  // (b) la tabla declara layoutVersion 2 (p. ej. cchh-candidatos con la casilla): el legacy describe otra tabla -> se descarta.
  const cand = buildSyntheticTable("cchh-candidatos-sim", { columns: [null, "nombre", "skills", "seniority", "ubicacion", "fuente", null], layoutVersion: 2 });
  store[WKEY("cchh-candidatos-sim")] = JSON.stringify({ schemaVersion: 1, containerWidth: cand.container.clientWidth, widths: { seniority: "180px", skills: "340px", fuente: "199px" } });
  ok(restorePersistedColumnWidths(cand.container) === false && !(WKEY("cchh-candidatos-sim") in store), "K6b: el registro legacy de Candidatos (estructura anterior) se descarta y no se vuelve a restaurar");
  ok(restorePersistedColumnWidths(cand.container) === false, "K6b: una segunda carga tampoco lo revive");
  // (c) legacy que nombra una columna que ya no existe -> incompatible aunque la versión coincida.
  const gone = buildSyntheticTable("k-legacy-gone", { columns: ["a", "b"] });
  store[WKEY("k-legacy-gone")] = JSON.stringify({ schemaVersion: 1, containerWidth: gone.container.clientWidth, widths: { zzz: "160px" } });
  ok(restorePersistedColumnWidths(gone.container) === false && !(WKEY("k-legacy-gone") in store), "K6c: un legacy que nombra una columna inexistente se descarta");
}

// K7: valores inválidos o corruptos -> nunca se aplican ni lanzan; el resto del storage no se toca.
{
  const { container } = buildSyntheticTable("k-bad", { columns: ["a", "b"] });
  const good = JSON.parse(record(container, { a: "150px" }));
  const bad = {
    "ancho no numérico": { ...good, widths: { a: "mucho" } },
    "ancho sin unidad px": { ...good, widths: { a: "150" } },
    "ancho cero": { ...good, widths: { a: "0px" } },
    "ancho negativo": { ...good, widths: { a: "-20px" } },
    "ancho absurdo": { ...good, widths: { a: "999999px" } },
    "ancho no string": { ...good, widths: { a: 150 } },
    "v2 sin layout": { schemaVersion: 2, containerWidth: good.containerWidth, widths: good.widths },
    "layout.version inválida": { ...good, layout: { version: "uno", signature: good.layout.signature } },
    "layout.signature vacía": { ...good, layout: { version: 1, signature: "" } },
    "containerWidth inválido": { ...good, containerWidth: -3 },
  };
  store["hc-seleccion"] = "a,b,c";
  Object.entries(bad).forEach(([label, value]) => {
    store[WKEY("k-bad")] = JSON.stringify(value);
    let threw = false, applied;
    try { applied = restorePersistedColumnWidths(container); } catch (e) { threw = true; }
    ok(!threw && applied === false, `K7: ${label} -> no aplica y no lanza`);
  });
  store[WKEY("k-bad")] = "{truncado";
  ok(restorePersistedColumnWidths(container) === false, "K7: JSON corrupto -> no aplica");
  ok(store["hc-seleccion"] === "a,b,c", "K7: las demás preferencias (selección, filtros) quedan intactas");
}

// K8: tamaño del contenedor -- más angosto o más ancho que el guardado: el registro compatible NO se aplica fuera de tolerancia y NO se elimina (otro tamaño puede volver a ser válido); dentro de tolerancia sí.
{
  const wide = buildSyntheticTable("k-size", { columns: ["a", "b"] });
  wide.container.attrs["data-test-width"] = "1552";
  const rec = record(wide.container, { a: "300px" });                      // grabado a 1552
  const narrow = buildSyntheticTable("k-size", { columns: ["a", "b"] });
  narrow.container.attrs["data-test-width"] = "1000";                       // iframe más angosto
  store[WKEY("k-size")] = rec;
  ok(restorePersistedColumnWidths(narrow.container) === false && !narrow.table.style.width, "K8: contenedor materialmente más angosto -> layout responsive (no se fuerza un total congelado)");
  ok(store[WKEY("k-size")] === rec, "K8: el registro NO se elimina por el ancho del contenedor (solo por estructura)");
  const wider = buildSyntheticTable("k-size", { columns: ["a", "b"] });
  wider.container.attrs["data-test-width"] = "2000";
  ok(restorePersistedColumnWidths(wider.container) === false, "K8: contenedor materialmente más ancho -> tampoco se aplica");
  const near = buildSyntheticTable("k-size", { columns: ["a", "b"] });
  near.container.attrs["data-test-width"] = "1500";                         // 3% de diferencia
  ok(restorePersistedColumnWidths(near.container) === true, "K8: una diferencia de píxeles dentro de la tolerancia se conserva");
}

// K9: aislamiento -- descartar una tabla no toca el registro (válido) de otra con la misma estructura ni el de otra key.
{
  const t1 = buildSyntheticTable("k-iso-1", { columns: ["a", "b", "c"] });
  const t2 = buildSyntheticTable("k-iso-2", { columns: ["a", "b", "c"] });
  store[WKEY("k-iso-1")] = record(buildSyntheticTable("x", { columns: ["a", "b"] }).container, { a: "150px" });   // obsoleto
  store[WKEY("k-iso-2")] = record(t2.container, { b: "170px" });                                                   // válido
  restorePersistedColumnWidths(t1.container);
  ok(!(WKEY("k-iso-1") in store), "K9: se descarta la tabla con estructura obsoleta");
  ok(restorePersistedColumnWidths(t2.container) === true && colW(t2.colgroup, "b") === "170px", "K9: la otra tabla conserva y aplica sus anchos");
}

// K10: lectura declarativa (Comercial arma su <colgroup> desde un catálogo): misma regla con `{tokens, reorderable}`.
{
  const spec = { tokens: ["x", "y", ""], reorderable: true };
  const current = columnLayoutFor(spec.tokens, spec);
  store[WKEY("k-decl")] = JSON.stringify({ schemaVersion: 2, containerWidth: 900, layout: { version: current.version, signature: current.signature }, widths: { x: "150px" } });
  eq(readStoredColumnWidths("k-decl", spec), { x: "150px" }, "K10: registro compatible con el catálogo declarativo -> devuelve los anchos");
  eq(readStoredColumnWidths("k-decl", { tokens: ["x", "y", "w", ""], reorderable: true }), null, "K10: el catálogo ganó una columna -> null");
  ok(!(WKEY("k-decl") in store), "K10: y el registro obsoleto se descartó");
  eq(readStoredColumnWidths("k-decl-sin-spec"), null, "K10: sin registro -> null");
}

// K11: el resize NO mezcla un registro de otra estructura ni de otro ancho de contenedor con el override nuevo.
{
  const old = buildSyntheticTable("k-merge", { columns: ["a", "b"] });
  const { root, container } = buildSyntheticTable("k-merge", { columns: ["a", "b", "c"] });
  store[WKEY("k-merge")] = record(old.container, { a: "300px" });          // estructura anterior
  initResizableColumns(root);
  const handle = queryAll(container, "th[data-column-key]")[1].children[0];
  dispatchOn(container, "pointerdown", { target: handle, clientX: 0, pointerId: 1, preventDefault(){}, stopPropagation(){} });
  dispatchOn(container, "pointermove", { clientX: 30 });
  dispatchOn(container, "pointerup", {});
  const saved = JSON.parse(store[WKEY("k-merge")]);
  eq(Object.keys(saved.widths), ["b"], "K11: el override nuevo no arrastra los anchos de la estructura anterior");
  eq(saved.layout.signature, columnLayoutOfContainer(container).signature, "K11: lo grabado lleva la estructura vigente (registrada para futuras cargas)");
}

if (failures > 0) {
  console.log(`${failures} app.js table ergonomics invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL APP TABLE ERGONOMICS INVARIANTS PASSED");
process.exit(0);
