// assessment_contextual_navigation.node.js — invocado por test_assessment_contextual_navigation.py.
// Ejecuta el `DwAssessmentNav` REAL y los handlers que lo cablean (extraídos de webapp/static/app.js) contra un DOM mínimo y verifica el contrato de
// navegación del informe de Assessment: #hash nativo (sin pushState propio), apertura de <details> ancestros, desplazamiento, resaltado único, foco,
// regreso al origen, deep link al cargar, destinos inexistentes/ajenos sin efecto y compatibilidad con el handler heredado de Catálogo.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");
const a = src.indexOf("const DwAssessmentNav = {");
const b = src.indexOf("\nfunction syncAssistantFeedbackState");
if (a < 0 || b < 0 || src.indexOf("const DwAssessmentNav", a + 10) !== -1) { console.log("FAIL: bloque DwAssessmentNav no encontrado (o duplicado) en app.js"); process.exit(1); }
const snippet = src.slice(a, b);

let failures = 0;
const check = (cond, msg) => { if (!cond) { failures++; console.log("FAIL: " + msg); } };

// ── DOM mínimo ──────────────────────────────────────────────────────────────
class El {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.cls = new Set((attrs.class || "").split(/\s+/).filter(Boolean));
    this.children = []; this.parentElement = null; this.open = "open" in attrs; this.scrolled = 0; this.focused = 0; this.offsetWidth = 1;
    this.classList = { add: (c) => this.cls.add(c), remove: (c) => this.cls.delete(c), contains: (c) => this.cls.has(c) };
  }
  get id() { return this.attrs.id; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  append(c) { c.parentElement = this; this.children.push(c); return c; }
  matches(sel) {
    return sel.split(",").map((s) => s.trim()).some((s) => {
      if (s === 'a[href^="#"]') return this.tagName === "A" && (this.attrs.href || "").startsWith("#");
      if (s[0] === ".") return this.cls.has(s.slice(1));
      const m = /^\[([\w-]+)\]$/.exec(s); if (m) return m[1] in this.attrs;
      return this.tagName === s.toUpperCase();
    });
  }
  closest(sel) { for (let e = this; e; e = e.parentElement) if (e.matches(sel)) return e; return null; }
  contains(o) { for (let e = o; e; e = e.parentElement) if (e === this) return true; return false; }
  querySelector(sel) {
    if (sel === ":scope > summary") return this.children.find((c) => c.tagName === "SUMMARY") || null;
    for (const c of this.children) { if (c.matches(sel)) return c; const r = c.querySelector(sel); if (r) return r; } return null;
  }
  all(out = []) { out.push(this); this.children.forEach((c) => c.all(out)); return out; }
  scrollIntoView(o) { this.scrolled++; this.lastScroll = o; }
  focus(o) { this.focused++; doc.activeElement = this; this.focusOpts = o; }
}

// Informe: article[data-asr-report] > section > details#asr-a-NAMING > (tabla con fila #asr-r-R1 + enlace) ; details#asr-g-R1 (cerrado) con enlace de regreso;
// details#asr-g-DESC (cerrado) y li#asr-f-DESC (residual). Fuera del informe: details#ajeno.
const body = new El("body");
const content = body.append(new El("main", { id: "content" }));
const report = content.append(new El("article", { "data-asr-report": "" }));
const area = report.append(new El("details", { id: "asr-a-NAMING" }));
area.append(new El("summary"));
const row = area.append(new El("tr", { id: "asr-r-R1" }));
const linkFwd = row.append(new El("a", { href: "#asr-g-R1" }));
const resid = area.append(new El("li", { id: "asr-f-DESC" }));
const linkResid = resid.append(new El("a", { href: "#asr-g-DESC" }));
const sec06 = report.append(new El("section", { id: "asr-revision" }));
const outer06 = sec06.append(new El("details", { id: "asr-obs-wrap" }));          // un ancestro extra cerrado (anidado)
const g1 = outer06.append(new El("details", { id: "asr-g-R1" }));
const g1sum = g1.append(new El("summary"));
const linkBack = g1.append(new El("a", { href: "#asr-r-R1", "data-asr-return": "" }));
const g2 = sec06.append(new El("details", { id: "asr-g-DESC" }));
g2.append(new El("summary"));
const alien = content.append(new El("details", { id: "ajeno" }));
const alienLink = content.append(new El("a", { href: "#ajeno" }));
const all = body.all();

const listeners = { document: {}, window: {}, body: {} };
const doc = {
  activeElement: null,
  getElementById: (id) => all.find((e) => e.id === id) || null,
  querySelectorAll: (sel) => all.filter((e) => e.matches(sel)),
  addEventListener: (t, fn) => { (listeners.document[t] = listeners.document[t] || []).push(fn); },
  body: { addEventListener: (t, fn) => { (listeners.body[t] = listeners.body[t] || []).push(fn); } },
};
const win = { location: { hash: "" }, addEventListener: (t, fn) => { (listeners.window[t] = listeners.window[t] || []).push(fn); } };
const sandbox = { window: win, document: doc, decodeURIComponent, console };
vm.createContext(sandbox);
vm.runInContext(snippet, sandbox);

check(!/pushState|replaceState/.test(snippet), "el módulo no debe usar un sistema propio de historial (pushState/replaceState)");
check(win.DwAssessmentNav && typeof win.DwAssessmentNav.go === "function", "DwAssessmentNav expuesto en window");

const fire = (kind, type, ev) => (listeners[kind][type] || []).forEach((fn) => fn(ev));
const resetState = () => { all.forEach((e) => { e.scrolled = 0; e.focused = 0; e.cls.delete("is-nav-target"); }); [area, outer06, g1, g2, alien].forEach((d) => { d.open = false; }); doc.activeElement = null; win.location.hash = ""; };
const navigate = (hash) => { win.location.hash = hash; fire("window", "hashchange", {}); };                       // lo que haría el navegador al seguir un enlace
const click = (el, extra = {}) => { const ev = { target: el, button: 0, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra }; fire("document", "click", ev); return ev; };

// 1. hashchange al detalle: abre el propio <details> y TODOS los ancestros, desplaza, resalta, enfoca el summary
resetState();
navigate("#asr-g-R1");
check(g1.open && outer06.open, "abre el detalle y su ancestro anidado");
check(g1.scrolled === 1 && g1.lastScroll.block === "start", "desplaza el destino");
check(g1.cls.has("is-nav-target"), "resalta el destino");
check(doc.activeElement === g1sum && g1sum.focused === 1 && g1sum.focusOpts.preventScroll === true, "enfoca el summary del detalle sin volver a desplazar");

// 2. un único resaltado: navegar a otro destino quita el anterior
navigate("#asr-g-DESC");
check(g2.cls.has("is-nav-target") && !g1.cls.has("is-nav-target"), "un solo destino resaltado a la vez");

// 3. regreso al origen: enfoca la fila (tabindex -1), abre el área, y el historial es el nativo (solo cambia el hash)
navigate("#asr-r-R1");
check(area.open && row.cls.has("is-nav-target") && doc.activeElement === row && row.attrs.tabindex === "-1", "el regreso abre el área y enfoca la fila de origen");
check(g1.open, "el detalle abierto no se cierra al volver (el usuario puede alternar)");

// 4. destino inexistente o fuera del informe: sin efecto (ni abrir, ni desplazar, ni foco)
resetState();
check(win.DwAssessmentNav.go(doc, "#no-existe") === false && win.DwAssessmentNav.go(doc, "#ajeno") === false && win.DwAssessmentNav.go(doc, "") === false, "destino inexistente/ajeno/vacío devuelve false");
navigate("#ajeno");
check(!alien.open && alien.scrolled === 0 && alien.focused === 0 && doc.activeElement === null, "no toca nada fuera del informe");

// 5. click en enlace del informe con hash distinto: NO se previene (navegación nativa → entrada de historial); el salto lo hace hashchange
resetState();
let ev = click(linkFwd);
check(ev.defaultPrevented === false && g1.scrolled === 0, "click con hash nuevo deja actuar a la navegación nativa");
navigate("#asr-g-R1");
check(g1.open && g1.scrolled === 1, "y el hashchange posterior revela el destino");

// 6. mismo hash otra vez (no habría hashchange): se resuelve en el click y se previene el salto nativo
g1.open = false; outer06.open = false; g1.scrolled = 0;
ev = click(linkFwd);
check(ev.defaultPrevented === true && g1.open && outer06.open && g1.scrolled === 1, "mismo destino: revela, desplaza y previene el salto nativo");

// 7. clic con modificadores o botón no primario: no se intercepta (abrir en pestaña nueva, etc.)
resetState(); win.location.hash = "#asr-g-R1";
for (const extra of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
  const e2 = click(linkFwd, extra); check(e2.defaultPrevented === false && g1.open === false, "no intercepta clic con modificadores/botón " + JSON.stringify(extra));
}

// 8. teclado: Enter sobre el enlace dispara `click`; el foco queda en el destino y el summary es operable (Enter/Espacio alternan <details> nativamente)
resetState();
navigate("#asr-g-DESC");
check(doc.activeElement.tagName === "SUMMARY", "el foco cae en un elemento operable por teclado");
navigate("#asr-r-R1");
check(doc.activeElement === row, "el regreso deja el foco en el origen para seguir tabulando desde ahí");

// 9. handler heredado de Catálogo: fuera del informe, con detalle abierto, sigue desplazando sin tocar el hash ni abrir nada
resetState(); content.cls.add("catalog-detail-open");
ev = click(alienLink);
check(ev.defaultPrevented === true && alien.scrolled === 1 && !alien.open, "enlaces ajenos al informe conservan el comportamiento heredado");
content.cls.delete("catalog-detail-open");
ev = click(alienLink);
check(ev.defaultPrevented === false, "sin detalle abierto el handler heredado no interviene");

// 10. deep link: al cargarse el informe (htmx:load) se resuelve el hash que ya traía la URL; sin hash o sin informe en el fragmento no hace nada
resetState(); win.location.hash = "#asr-g-R1";
fire("body", "htmx:load", { target: report });
check(g1.open && outer06.open && g1.scrolled === 1, "deep link: revela el hallazgo al cargar el informe");
resetState(); win.location.hash = "#asr-g-R1";
fire("body", "htmx:load", { target: alien }); check(g1.scrolled === 0 && !g1.open, "un fragmento sin informe no dispara el deep link");
fire("body", "htmx:load", { target: body }); check(g1.scrolled === 1 && g1.open, "la carga de página completa (body contiene el informe) sí lo resuelve");
resetState(); fire("body", "htmx:load", { target: report }); check(g1.scrolled === 0, "sin hash no hay navegación");

// 11. hash codificado y residual
resetState();
navigate("#asr-g-DESC"); check(g2.open, "los hallazgos descriptivos (sin regla) también son destino navegable");
check(win.DwAssessmentNav.go(doc, "#asr-f-DESC") && area.open && resid.cls.has("is-nav-target"), "el ítem residual del área es destino navegable");

if (failures) { console.log(failures + " verificación(es) fallida(s)"); process.exit(1); }
console.log("OK");
