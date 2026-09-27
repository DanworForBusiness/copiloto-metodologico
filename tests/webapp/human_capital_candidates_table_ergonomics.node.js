// human_capital_candidates_table_ergonomics.node.js — invocado por
// test_human_capital_candidates_table_ergonomics.py.
//
// CCHH UX homologation: adopción del primitivo COMPARTIDO de reorder/resize
// (initDraggableColumns/initResizableColumns, app.js — su contrato
// table-agnostic ya se prueba en app_table_ergonomics.node.js, y el wiring
// de Comercial en commercial_licensing_table_ergonomics.node.js). Candidatos
// es una tabla SSR/estática (Jinja puro, sin renderer cliente) — a
// diferencia de Comercial, que reconstruye HTML desde un array de columnas,
// acá el "adapter" (partials/human_capital_profiles_panel.html, mismo
// <script>) sólo MUEVE nodos DOM ya renderizados.
//
// Owner post-#104 acceptance audit: los tests A-D de abajo (adapter puro,
// aplicar orden/anchos YA guardados) NUNCA hacían correr el wiring real
// (`initDraggableColumns`/`initResizableColumns` quedaban stubbeados como
// no-ops y `document.querySelectorAll` siempre devolvía `[]`) -- por eso
// dejaron pasar a producción DOS bugs reales a la vez (scope equivocado +
// orden de carga contra app.js) que ningún test detectó. Los tests E/F
// (nuevos) cierran ESE agujero: cargan el `app.js` REAL (mismo criterio que
// app_table_ergonomics.node.js) junto con este adapter, contra un mini-DOM
// con un árbol real (no un documento vacío) para que
// `document.querySelectorAll(...)` encuentre de verdad el contenedor de
// Candidatos -- exactamente la superficie donde vivían los dos bugs.
//
// No hay jsdom en el repo: mini-DOM propio (MISMO idioma que
// app_table_ergonomics.node.js — dataset/classList/className reales,
// selectores por atributo/clase/tag compuestos con `:scope`, addEventListener/
// dispatchEvent que bubbling de verdad) copiado acá porque cada harness de
// este repo es autocontenido.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

// ── Mini-DOM con dataset/classList/selectores reales (MISMO motor que
//    app_table_ergonomics.node.js — no una segunda implementación) ─────────
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
    appendChild(child) {
      if (child.parent) child.parent.children = child.parent.children.filter(c => c !== child);
      child.parent = node; node.children.push(child); return child;
    },
    getAttribute(n) { return n in node.attrs ? node.attrs[n] : null; },
    setAttribute(n, v) { node.attrs[n] = String(v); },
    getBoundingClientRect() { return { width: Number(node.attrs["data-test-width"] || 100) }; },
    setPointerCapture() {},
    addEventListener(type, cb) { (node._listeners[type] = node._listeners[type] || []).push(cb); },
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
  // Valor entre comillas simples O dobles -- el adapter real
  // (human_capital_profiles_panel.html) usa comillas simples
  // (`[data-draggable-columns-key='cchh-candidatos']`), a diferencia de
  // app_table_ergonomics.node.js que sólo necesitaba dobles.
  const m = sel.match(/^([a-z]*)(\.[a-z-]+)?(\[([a-z-]+)(?:=["']([^"']*)["'])?\])?$/i);
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

// ── Extracción de los dos scripts REALES bajo prueba ────────────────────────
const TEMPLATE_PATH = path.resolve(
  __dirname, "..", "..", "webapp", "templates", "partials", "human_capital_profiles_panel.html",
);
const templateSrc = fs.readFileSync(TEMPLATE_PATH, "utf-8");
const scriptStart = templateSrc.indexOf("<script>") + "<script>".length;
const scriptEnd = templateSrc.lastIndexOf("</script>");
const adapterScript = templateSrc.slice(scriptStart, scriptEnd);

const APP_JS_PATH = path.resolve(__dirname, "..", "..", "webapp", "static", "app.js");
const appJsSrc = fs.readFileSync(APP_JS_PATH, "utf-8");

function buildContainer(rows) {
  // rows[0] = header cells [{key, tag:'th'}], rows[1..] = body cells.
  const container = makeNode("div", {
    "data-draggable-columns-key": "cchh-candidatos",
    "data-resizable-columns-key": "cchh-candidatos",
  });
  const table = container.appendChild(makeNode("table"));
  const colgroup = table.appendChild(makeNode("colgroup"));
  const thead = table.appendChild(makeNode("thead"));
  const headerRow = thead.appendChild(makeNode("tr"));
  const tbody = table.appendChild(makeNode("tbody"));

  const HEADER_KEYS = ["nombre", "skills", "seniority", "ubicacion", "fuente"];
  HEADER_KEYS.forEach(key => colgroup.appendChild(makeNode("col", { "data-column-key": key })));
  colgroup.appendChild(makeNode("col")); // Acciones: sin key, mismo criterio que Comercial

  HEADER_KEYS.forEach(key => {
    const th = makeNode("th", { "data-column-key": key, "draggable": "true" });
    th.attrs["data-label"] = key;
    headerRow.appendChild(th);
  });
  headerRow.appendChild(makeNode("th", { "data-label": "acciones" })); // sin data-column-key

  rows.forEach(rowValues => {
    const tr = tbody.appendChild(makeNode("tr"));
    HEADER_KEYS.forEach(key => {
      const td = makeNode("td", { "data-column-key": key });
      td.attrs["data-value"] = rowValues[key];
      tr.appendChild(td);
    });
    const actionTd = makeNode("td");
    actionTd.attrs["data-value"] = "editar-link";
    tr.appendChild(actionTd); // sin data-column-key -- excluida
  });
  return container;
}

function orderOf(row) {
  return row.children.map(c => c.attrs["data-label"] || c.attrs["data-value"]);
}

// ── Sandbox A: adapter solo (initDraggableColumns/initResizableColumns
//    stubbeados) -- para los tests A-D, que prueban EXCLUSIVAMENTE la lógica
//    de aplicar preferencias ya guardadas, independiente del wiring real. ──
function loadAdapter({ storedOrder, storedWidths, onRestore } = {}) {
  const sandbox = {
    document: { querySelectorAll() { return []; }, body: { addEventListener() {} } },
    window: {},
    initDraggableColumns() {},
    initResizableColumns() {},
    readStoredColumnOrder(key, validKeys) {
      return (storedOrder && key === "cchh-candidatos") ? storedOrder(validKeys) : null;
    },
    readStoredColumnWidths(key) {
      return (storedWidths && key === "cchh-candidatos") ? storedWidths() : null;
    },
    // El ALGORITMO de reconstrucción vive en el primitivo compartido (app.js,
    // probado en app_table_ergonomics.node.js::D5). Acá se stubbea para verificar
    // sólo la INTEGRACIÓN: que el adapter CCHH DELEGA en él con su container.
    restorePersistedColumnWidths(container) { if (onRestore) onRestore(container); return true; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(adapterScript, sandbox, { filename: "human_capital_profiles_panel.html<script>" });
  return sandbox.window.__cchhCandidatesTableAdapter;
}

// ── Sandbox B: app.js REAL + adapter REAL, contra un árbol de documento de
//    verdad -- para los tests E/F, que prueban el WIRING end-to-end (la
//    superficie exacta donde vivían los dos bugs de producción). MISMO set
//    de globals que app_table_ergonomics.node.js usa para cargar app.js
//    (su top-level corre código fuera de cualquier función bajo prueba). ──
function buildRealSandbox(readyState) {
  const documentRoot = makeNode("html");
  const body = makeNode("body");
  documentRoot.appendChild(body);
  const container = buildContainer([
    { nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" },
  ]);
  body.appendChild(container);

  const documentListeners = Object.create(null);
  const documentDouble = {
    readyState,
    createElement: tag => makeNode(tag),
    addEventListener(type, cb) { (documentListeners[type] = documentListeners[type] || []).push(cb); },
    querySelectorAll(sel) { return queryAll(documentRoot, sel); },
    querySelector(sel) { return queryAll(documentRoot, sel)[0] || null; },
    getElementById() { return null; },
    body,
  };
  const store = Object.create(null);
  const localStorage = {
    getItem(k) { return k in store ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
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
  return { sandbox, documentListeners, container };
}

// ── A: sin preferencia guardada -> el DOM SSR queda tal cual ────────────────
{
  const adapter = loadAdapter({});
  const container = buildContainer([{ nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" }]);
  const headerRow = container.querySelector("table > thead > tr");
  const before = orderOf(headerRow);
  adapter.applyStoredColumnOrder(container);
  ok(JSON.stringify(orderOf(headerRow)) === JSON.stringify(before),
    "A: sin preferencia guardada, el orden default del SSR no se toca");
}

// ── B: preferencia guardada -> el header Y cada fila del body se reordenan,
//     "Acciones" SIEMPRE queda último ──────────────────────────────────────
{
  const reversed = ["fuente", "ubicacion", "seniority", "skills", "nombre"];
  const adapter = loadAdapter({ storedOrder: () => reversed });
  const container = buildContainer([
    { nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" },
    { nombre: "Beto", skills: "BI", seniority: "Junior", ubicacion: "CBA", fuente: "Referido" },
  ]);
  adapter.applyStoredColumnOrder(container);
  const headerRow = container.querySelector("table > thead > tr");
  ok(JSON.stringify(orderOf(headerRow)) === JSON.stringify([...reversed, "acciones"]),
    `B: el header respeta el orden guardado, Acciones último (obtenido: ${JSON.stringify(orderOf(headerRow))})`);
  const bodyRows = container.querySelector("tbody").children;
  ok(JSON.stringify(orderOf(bodyRows[0])) === JSON.stringify(["LinkedIn", "BA", "Senior", "SAP", "Ana", "editar-link"]),
    `B: la fila 1 del body sigue el MISMO orden que el header, Acciones último (obtenido: ${JSON.stringify(orderOf(bodyRows[0]))})`);
  ok(JSON.stringify(orderOf(bodyRows[1])) === JSON.stringify(["Referido", "CBA", "Junior", "BI", "Beto", "editar-link"]),
    "B: la fila 2 del body también, independiente de la fila 1");
}

// ── C: INTEGRACIÓN — el adapter CCHH DELEGA la reconstrucción de anchos en el
//     algoritmo COMPARTIDO `restorePersistedColumnWidths` (app.js), con su
//     propio container. El algoritmo en sí (overrides + freeze + total
//     explícito) se prueba en app_table_ergonomics.node.js::D5, NO acá. ─
{
  const restoreCalls = [];
  const adapter = loadAdapter({ storedWidths: () => ({ nombre: "260px" }), onRestore: (c) => restoreCalls.push(c) });
  const container = buildContainer([{ nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" }]);
  adapter.applyStoredColumnWidths(container);
  ok(restoreCalls.length === 1, "C: el adapter delega EXACTAMENTE una vez en restorePersistedColumnWidths");
  ok(restoreCalls[0] === container, "C: delega con su propio container (integración, no copia del algoritmo)");
}

// ── D: namespace independiente -- una preferencia bajo OTRA key (simulando
//     Comercial) nunca se aplica a Candidatos ───────────────────────────────
{
  const adapter = loadAdapter({}); // readStoredColumnOrder mock sólo responde para "cchh-candidatos"
  const container = buildContainer([{ nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" }]);
  const headerRow = container.querySelector("table > thead > tr");
  const before = orderOf(headerRow);
  adapter.applyStoredColumnOrder(container); // la key real es "cchh-candidatos"; el mock nunca la reconoce -> null
  ok(JSON.stringify(orderOf(headerRow)) === JSON.stringify(before),
    "D: ninguna preferencia de otro namespace (p. ej. Comercial) contamina Candidatos");
}

// ── E: WIRING REAL, scope correcto -- app.js YA cargado (document.readyState
//     distinto de "loading"): wire() debe correr de inmediato y cablear el
//     contenedor REAL de Candidatos vía `initDraggableColumns(document)`/
//     `initResizableColumns(document)` -- si alguien regresara al bug de
//     scope (pasar `container` en vez de `document`), `container.
//     querySelectorAll(...)` nunca matchea el propio `container` y este test
//     falla (dragColsWired/resizeColsWired nunca se setean). ─────────────────
{
  const { sandbox, container } = buildRealSandbox("complete");
  vm.runInContext(appJsSrc, sandbox, { filename: "app.js" });
  vm.runInContext(adapterScript, sandbox, { filename: "human_capital_profiles_panel.html<script>" });

  ok(container.dataset.dragColsWired === "1",
    "E: con app.js ya cargado, initDraggableColumns(document) cablea el contenedor REAL de Candidatos");
  ok(container.dataset.resizeColsWired === "1",
    "E: initResizableColumns(document) también lo cablea");
  const nombreHeader = container.querySelector('table > thead > tr > th[data-column-key="nombre"]');
  ok(!!nombreHeader && nombreHeader.children.length === 1 && nombreHeader.children[0].classList.contains("col-resize-handle"),
    "E: el header REAL de Candidatos (no una tabla sintética) recibe un handle de resize genuino");
}

// ── F: ORDEN DE CARGA -- el adapter corre ANTES de que app.js exista
//     (document.readyState === "loading", igual que un <script> inline en
//     <main> corriendo antes del <script src> de app.js al final de <body>):
//     wire() NO debe correr de inmediato, sino diferirse a DOMContentLoaded.
//     Si alguien regresara al bug de orden de carga (llamar wire() sin
//     esperar), este test falla en dos frentes: nada queda cableado
//     prematuramente Y no se registra ningún listener de DOMContentLoaded
//     al que este test pueda disparar después. ─────────────────────────────
{
  const { sandbox, documentListeners, container } = buildRealSandbox("loading");
  // El <script> inline corre primero -- initDraggableColumns/
  // initResizableColumns TODAVÍA no existen en este punto del sandbox.
  vm.runInContext(adapterScript, sandbox, { filename: "human_capital_profiles_panel.html<script>" });

  ok(container.dataset.dragColsWired === undefined,
    "F: con document.readyState='loading' y app.js sin cargar, wire() no corre de inmediato (nada cableado prematuramente)");
  ok(Array.isArray(documentListeners.DOMContentLoaded) && documentListeners.DOMContentLoaded.length === 1,
    "F: en cambio, wire() queda diferido a un único listener de DOMContentLoaded");

  // app.js termina de cargar (script síncrono al final de <body>, MISMO
  // scope global del sandbox) -- ahora sí existen las funciones reales.
  vm.runInContext(appJsSrc, sandbox, { filename: "app.js" });
  // El browser dispara DOMContentLoaded una vez que el parsing termina.
  // `|| []` -- si el bug de orden de carga regresara (wire() sin diferir),
  // el assert de arriba ya falló y acá nunca se registró ningún listener;
  // sin el guard, este disparo crashearía el harness entero en vez de dejar
  // un FAIL limpio y seguir corriendo el resto de los tests.
  (documentListeners.DOMContentLoaded || []).forEach(cb => cb());

  ok(container.dataset.dragColsWired === "1",
    "F: al disparar DOMContentLoaded con app.js ya disponible, wire() ahora sí cablea reorder");
  ok(container.dataset.resizeColsWired === "1",
    "F: ...y resize también -- cierra el bug de orden de carga que escapó a PR #104");
}

if (failures > 0) {
  console.log(`${failures} Human Capital candidates table ergonomics invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUMAN CAPITAL CANDIDATES TABLE ERGONOMICS INVARIANTS PASSED");
process.exit(0);
