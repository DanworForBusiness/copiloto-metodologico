// usage_cascade_integrity.node.js — CONSERVACIÓN DE LA CASCADA DE MÉTRICAS DE USO.
//
// El defecto que este harness fija: el panel derecho se resolvía por NOMBRE DE
// EVENTO solamente, así que seleccionar un módulo a la izquierda no viajaba a la
// etapa siguiente. En vivo: izquierda 175, desglose 978 — el universo global de
// `module_viewed`, que existe a propósito en Copiloto, Operaciones y Comercial.
//
// LA FIXTURE ES LO QUE IMPORTA. Con nombres de evento globalmente únicos, o con
// un solo módulo, el bug es INDISTINGUIBLE: el desglose global y el module-scoped
// dan idéntico. Por eso acá el MISMO evento existe en dos módulos y los usuarios
// solapan sólo parcialmente.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "..", "webapp", "static", "app.js"), "utf8");

let failures = 0;
function assertEqual(a, e, label) {
  const ja = JSON.stringify(a), je = JSON.stringify(e);
  if (ja !== je) { failures++; console.log(`FAIL: ${label} — esperado ${je}, obtenido ${ja}`); }
}
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

// ── DOM mínimo, con la MISMA forma que emite admin_activity_content.html ────
function crearDom(fixture) {
  const nodos = [];
  const nuevo = (tag, dataset, extra) => {
    const el = Object.assign({
      tag, dataset: dataset || {}, hidden: false, children: [], parent: null,
      textContent: "",
      classList: {
        _s: new Set(),
        add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
        contains(c) { return this._s.has(c); },
        toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
      },
      listeners: {},
      addEventListener(t, fn) { (this.listeners[t] || (this.listeners[t] = [])).push(fn); },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
      querySelectorAll(sel) { return buscar(this.children, sel); },
      closest(sel) {
        let n = this;
        while (n) { if (coincide(n, sel)) return n; n = n.parent; }
        return null;
      },
    }, extra || {});
    nodos.push(el);
    return el;
  };
  const coincide = (el, sel) => {
    const m = sel.match(/^\[([a-z-]+)\]$/);
    // Nombre de tag (p. ej. "strong"): el chip de filtro busca su <strong>
    // interno. Sin esto el fake devolvia null y el texto nunca se escribia —
    // un fallo del doble, no del producto.
    if (!m) return el.tag === sel;
    const camel = m[1].replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    return camel in el.dataset;
  };
  const buscar = (hijos, sel) => {
    const out = [];
    const rec = ns => ns.forEach(n => { if (coincide(n, sel)) out.push(n); rec(n.children); });
    rec(hijos);
    return out;
  };
  const adoptar = (padre, hijo) => { hijo.parent = padre; padre.children.push(hijo); return hijo; };

  const root = nuevo("div", {});
  root.querySelectorAll = sel => buscar(root.children, sel);
  root.querySelector = sel => buscar(root.children, sel)[0] || null;

  // Sección "Módulos y usuarios": una fila por módulo.
  Object.keys(fixture).forEach(mod => adoptar(root, nuevo("div", { module: mod })));

  // El chip "Filtrado por X".
  const chip = adoptar(root, nuevo("span", { moduleFilterLabel: "" }, { hidden: true }));
  const fuerte = adoptar(chip, nuevo("strong", {}));
  adoptar(chip, nuevo("button", { moduleReset: "" }));

  // Grupo de la cascada: izquierda (listas por módulo) + derecha (paneles).
  const grupo = adoptar(root, nuevo("div", { breakdownGroup: "", cascadeModule: "" }));

  // Panel global (sin módulo): la unión de eventos de todos los módulos.
  const globales = {};
  Object.values(fixture).forEach(evs => Object.entries(evs).forEach(([e, us]) => {
    globales[e] = (globales[e] || 0) + Object.values(us).reduce((a, b) => a + b, 0);
  }));
  const panelGlobal = adoptar(grupo, nuevo("div", { moduleBreakdown: "" }));
  Object.keys(globales).sort((a, b) => globales[b] - globales[a])
    .forEach(e => adoptar(panelGlobal, nuevo("div", { event: e })));

  // Una lista de eventos por módulo.
  Object.entries(fixture).forEach(([mod, evs]) => {
    const panel = adoptar(grupo, nuevo("div", { moduleBreakdown: mod }, { hidden: true }));
    Object.keys(evs).forEach(e => adoptar(panel, nuevo("div", { event: e })));
  });

  // Derecha: empty-state + un panel GLOBAL por evento + uno por (módulo, evento).
  adoptar(grupo, nuevo("div", { breakdown: "", breakdownModule: "" }, { hidden: true }));
  Object.keys(globales).forEach(e => {
    const p = adoptar(grupo, nuevo("div", { breakdown: e, breakdownModule: "" }, { hidden: true }));
    p.filas = Object.entries(fixture).flatMap(([, evs]) => Object.entries(evs[e] || {}));
  });
  Object.entries(fixture).forEach(([mod, evs]) => {
    Object.entries(evs).forEach(([e, us]) => {
      const p = adoptar(grupo, nuevo("div", { breakdown: e, breakdownModule: mod }, { hidden: true }));
      p.filas = Object.entries(us);
    });
  });

  return { root, chip, fuerte, todos: nodos };
}

// ── FIXTURE OBLIGATORIA (owner §4): MISMO evento, DISTINTO módulo ──────────
const FIXTURE = {
  "Asignaciones": { "module_viewed": { "Ana": 2, "Juan": 1 }, "module_continue": { "Ana": 1 } },
  "Copiloto":     { "module_viewed": { "Pedro": 8 } },
};

const { root, chip, fuerte } = crearDom(FIXTURE);
const listeners = {};
const sandbox = {
  document: {
    addEventListener(t, fn) { (listeners[t] || (listeners[t] = [])).push(fn); },
    getElementById() { return null; }, querySelector() { return null; },
    querySelectorAll() { return []; },
    body: { addEventListener() { }, classList: { add() { }, remove() { }, toggle() { } } },
  },
  window: { location: { pathname: "/admin", search: "" }, addEventListener() { }, matchMedia: () => ({ matches: false, addEventListener() { } }) },
  localStorage: { getItem: () => null, setItem() { }, removeItem() { } },
  fetch: () => Promise.resolve({ json: () => Promise.resolve({}), text: () => Promise.resolve("") }),
  setInterval: () => 0, clearInterval() { }, setTimeout, clearTimeout,
  console, JSON, Object, Array, Math, Date, Promise, URLSearchParams, Set, Map,
  htmx: { process() { }, ajax() { } },
  navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: "node" },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
try { vm.runInContext(src, sandbox, { filename: "app.js" }); } catch (e) {
  console.log("FAIL: app.js no evaluó — " + String(e).slice(0, 200));
  process.exit(1);
}

const initEventBreakdown = vm.runInContext("initEventBreakdown", sandbox);
const initModuleFilter = vm.runInContext("initModuleFilter", sandbox);

const click = el => (el.listeners.click || []).forEach(fn => fn({ preventDefault() { } }));
const visible = () => root.querySelectorAll("[data-breakdown]").filter(p => !p.hidden);
const filasDe = p => (p && p.filas) || [];
const sumaDe = p => filasDe(p).reduce((a, [, n]) => a + n, 0);
const filaModulo = m => root.querySelectorAll("[data-module]").find(r => r.dataset.module === m);
const filaEventoVisible = e => root.querySelectorAll("[data-module-breakdown]")
  .filter(p => !p.hidden)
  .flatMap(p => p.querySelectorAll("[data-event]"))
  .find(r => r.dataset.event === e);

// Selecciona (módulo, evento). Elegir un módulo YA aterriza en su evento más
// frecuente —igual que el `openFirst` de siempre—, así que volver a clickear
// esa fila lo DES-selecciona. El helper sólo clickea si hace falta.
function seleccionar(mod, evt) {
  click(filaModulo(mod));
  const fila = filaEventoVisible(evt);
  if (fila && !fila.classList.contains("is-active")) click(fila);
  return fila;
}

initEventBreakdown(root);
initModuleFilter(root);

// ── 1. EL CASO DEL OWNER ───────────────────────────────────────────────────
seleccionar("Asignaciones", "module_viewed");

let abiertos = visible();
assertEqual(abiertos.length, 1, "un solo panel de desglose abierto");
const panel = abiertos[0];
assertEqual(panel.dataset.breakdown, "module_viewed", "el panel es el del evento elegido");
assertEqual(panel.dataset.breakdownModule, "Asignaciones",
  "y el del MÓDULO elegido — la etapa hereda la dimensión de arriba");

const etiquetas = filasDe(panel).map(([u]) => u).sort();
assertEqual(etiquetas, ["Ana", "Juan"], "sólo los usuarios de Asignaciones");
assertTrue(!etiquetas.includes("Pedro"),
  "Pedro (Copiloto) NO puede aparecer bajo Asignaciones — es el corazón del corrective");
assertEqual(sumaDe(panel), 3, "SUM(desglose) == 3");

// ── 2. CONSERVACIÓN (owner §5) ─────────────────────────────────────────────
Object.entries(FIXTURE).forEach(([mod, evs]) => {
  Object.entries(evs).forEach(([evt, us]) => {
    const fila = seleccionar(mod, evt);
    if (!fila) { failures++; console.log(`FAIL: ${mod}/${evt} no tiene fila visible`); return; }
    const p = visible()[0];
    const esperado = Object.values(us).reduce((a, b) => a + b, 0);
    assertEqual(sumaDe(p), esperado, `CONSERVA ${mod}/${evt}: SUM(usuarios) == conteo del evento`);
    // Universo hijo ⊆ universo padre.
    const global = root.querySelectorAll("[data-breakdown]")
      .find(x => x.dataset.breakdown === evt && x.dataset.breakdownModule === "");
    assertTrue(sumaDe(p) <= sumaDe(global), `${mod}/${evt}: hijo ⊆ padre`);
  });
});

// ── 3. HERENCIA Y LIMPIEZA (owner §3) ──────────────────────────────────────
seleccionar("Asignaciones", "module_viewed");
// Limpiar el EVENTO preserva el módulo.
click(filaEventoVisible("module_viewed"));           // toggle sobre la activa
assertEqual(visible().map(p => p.dataset.breakdown), [""],
  "sin evento: queda el empty-state");
assertTrue(filaModulo("Asignaciones").classList.contains("is-active"),
  "limpiar el evento PRESERVA el módulo");
assertTrue(!chip.hidden && fuerte.textContent === "Asignaciones",
  "y el chip de filtro sigue puesto");

// Limpiar el MÓDULO vuelve a la vista global.
const reset = chip.querySelectorAll("[data-module-reset]")[0];
click(reset);
assertTrue(chip.hidden, "limpiar el módulo esconde el chip");
assertTrue(!filaModulo("Asignaciones").classList.contains("is-active"),
  "y ninguna fila de módulo queda activa");
const trasReset = visible();
assertTrue(trasReset.every(p => (p.dataset.breakdownModule || "") === ""),
  "el desglose visible vuelve al universo global, nunca a uno module-scoped huérfano");

// ── 4. CAMBIO DE MÓDULO CON UN EVENTO QUE EL DESTINO NO TIENE ──────────────
// `module_continue` sólo existe en Asignaciones. Al pasar a Copiloto el estado
// no puede quedar apuntando a un evento que ese módulo no tiene.
seleccionar("Asignaciones", "module_continue");
assertEqual(visible()[0].dataset.breakdownModule, "Asignaciones", "arranca en Asignaciones");
click(filaModulo("Copiloto"));
const tras = visible();
assertEqual(tras.length, 1, "sigue habiendo exactamente un panel abierto");
assertEqual(tras[0].dataset.breakdownModule, "Copiloto", "el panel es del módulo nuevo");
assertEqual(tras[0].dataset.breakdown, "module_viewed",
  "y cae al evento más frecuente de ESE módulo, no a uno que no tiene");
assertEqual(sumaDe(tras[0]), 8, "con su propio universo");

if (failures > 0) {
  console.log(`${failures} usage cascade invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL USAGE CASCADE INVARIANTS PASSED");
process.exit(0);
