// catalog_nav_states.node.js — invocado por test_catalog_navigation_states.py.
// Ejecuta el `DwCatalogNav` REAL (extraído de webapp/static/app.js) contra un DOM mínimo + un `htmx` espía, y verifica el contrato de carga/error/cancelación del
// Catálogo: la UI es de la ÚLTIMA solicitud (respuestas/abortos/errores tardíos de una anterior no la tocan), un fallo se muestra como ERROR con reintento (nunca como
// "sin datos"), el panel central muestra el loader apenas se pide un tile y jamás queda un loader permanente.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8");
const a = src.indexOf("const DwCatalogNav = (() => {");
const bMarker = "window.DwCatalogNav = DwCatalogNav;";
const b = src.indexOf(bMarker);
if (a < 0 || b < 0) { console.log("FAIL: no se encontró DwCatalogNav en app.js"); process.exit(1); }
const snippet = src.slice(a, b + bMarker.length);
if (src.indexOf("const DwCatalogNav", a + 10) !== -1) { console.log("FAIL: DwCatalogNav definido más de una vez"); process.exit(1); }

// ── DOM mínimo ──────────────────────────────────────────────────────────────
class El {
  constructor(tag = "div", attrs = {}) { this.tag = tag; this.attrs = { ...attrs }; this.cls = new Set((attrs.class || "").split(/\s+/).filter(Boolean)); this.children = []; this.parent = null; this.scrollTop = 0; this.classList = { add: (c) => this.cls.add(c), remove: (c) => this.cls.delete(c), contains: (c) => this.cls.has(c) }; }
  get id() { return this.attrs.id; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.cls = new Set(String(v).split(/\s+/)); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  append(c) { c.parent = this; this.children.push(c); return c; }
  replaceChildren(...nodes) { this.children.forEach((c) => (c.parent = null)); this.children = []; nodes.forEach((n) => this.append(n)); }
  matches(sel) { if (sel[0] === "#") return this.id === sel.slice(1); if (sel[0] === ".") return this.cls.has(sel.slice(1)); const m = /^\[([\w-]+)\]$/.exec(sel); return m ? m[1] in this.attrs : false; }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
  querySelector(sel) { for (const c of this.children) { if (c.matches(sel)) return c; const r = c.querySelector(sel); if (r) return r; } return null; }
}
const root = new El("body");
const content = root.append(new El("main", { id: "content" }));
const slot = content.append(new El("div", { id: "catalog-detail-slot" }));
const region = root.append(new El("div", { id: "catalog-lob-region", class: "catalog-region" }));
const box = region.append(new El("div", { id: "catalog-lob-flows", class: "catalog-flows" }));
const select = root.append(new El("select", { id: "catalog-lob", "data-dw-nav-region": "#catalog-lob-region" }));
const tplNode = (id, build) => ({ id, content: { cloneNode: () => build() } });
const loadingTpl = () => new El("div", { "data-dw-nav-loading": "" });
const errorTpl = (withBack) => () => { const e = new El("div", { class: "catalog-state catalog-state--error" }); e.append(new El("button", { "data-dw-nav-retry": "" })); if (withBack) e.append(new El("button", { class: "back" })); return e; };
const templates = { "catalog-detail-loading-tpl": tplNode("catalog-detail-loading-tpl", loadingTpl), "catalog-detail-error-tpl": tplNode("catalog-detail-error-tpl", errorTpl(true)), "catalog-list-error-tpl": tplNode("catalog-list-error-tpl", errorTpl(false)) };
const byId = (id) => (id === "content" ? content : id === "catalog-detail-slot" ? slot : templates[id] || null);

const listeners = {};
const calls = { trigger: [], ajax: [] };
const sandbox = {
  console,
  dwModuleScroll: 0,
  htmx: { trigger: (el, ev) => calls.trigger.push([el.id, ev]), ajax: (verb, p, opts) => calls.ajax.push([verb, p, opts.target]) },
  document: {
    body: { addEventListener: (n, fn) => { (listeners[n] = listeners[n] || []).push(fn); } },
    getElementById: byId,
    querySelector: (sel) => (sel === "#catalog-lob-region" ? region : null),
  },
  window: {},
};
vm.createContext(sandbox);
vm.runInContext(snippet, sandbox);

let failures = 0;
const check = (cond, msg) => { if (!cond) { failures++; console.log("FAIL: " + msg); } };
const fire = (name, detail) => (listeners[name] || []).forEach((fn) => fn({ detail, target: detail.elt, preventDefault() {} }));
const xhr = () => ({ aborted: 0, abort() { this.aborted++; } });
const reset = () => { content.cls.clear(); content.scrollTop = 0; slot.replaceChildren(); slot.removeAttribute("aria-busy"); box.replaceChildren(new El("div", { class: "card" })); box.removeAttribute("aria-busy"); calls.trigger.length = 0; calls.ajax.length = 0; sandbox.dwModuleScroll = 0; };
const listReq = (x) => ({ elt: select, xhr: x, target: box, requestConfig: { verb: "get" }, pathInfo: { finalRequestPath: "/catalog/lob?lob=X" } });
const slotReq = (x, verb = "get", p = "/catalog/flow?flow=a") => ({ elt: new El("li"), xhr: x, target: slot, requestConfig: { verb, path: p }, pathInfo: { finalRequestPath: p } });

// ── 0. cada evento se registra UNA sola vez (sin listeners duplicados) ──────
for (const n of ["htmx:beforeRequest", "htmx:afterRequest", "htmx:responseError", "htmx:sendError", "htmx:timeout", "htmx:sendAbort", "click"])
  check((listeners[n] || []).length === 1, `listener ${n} registrado ${(listeners[n] || []).length} veces`);
check(typeof sandbox.window.DwCatalogNav.cancelDetail === "function", "DwCatalogNav expuesto en window");

// ── 1. listas: cambio rápido A→B, la solicitud previa se cancela y no toca la UI ──
reset();
const A = xhr(), B = xhr();
fire("htmx:beforeRequest", listReq(A));
check(box.getAttribute("aria-busy") === "true", "aria-busy al iniciar");
fire("htmx:beforeRequest", listReq(B));
check(A.aborted === 1 && B.aborted === 0, "la solicitud anterior se aborta al iniciar la nueva");
fire("htmx:afterRequest", listReq(A));                       // htmx dispara afterRequest también para la abortada
check(box.getAttribute("aria-busy") === "true", "el aborto de A no apaga el aria-busy de B");
fire("htmx:sendAbort", listReq(A));
fire("htmx:sendError", listReq(A));
check(!box.querySelector(".catalog-state--error"), "ni el aborto ni un error TARDÍO de A pintan error sobre la selección de B");
fire("htmx:afterRequest", { ...listReq(B), successful: true });
check(box.getAttribute("aria-busy") === null, "al terminar B se limpia aria-busy");

// ── 2. fuera de orden: A responde tarde (con error) después de que B terminó ──
fire("htmx:responseError", listReq(A));
check(!box.querySelector(".catalog-state--error") && box.querySelector(".card"), "una respuesta tardía de A no reemplaza el contenido de B");

// ── 3. error HTTP / red / timeout → ERROR con reintento (no "sin datos"); reintento re-dispara el selector ──
for (const ev of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
  reset();
  const X = xhr();
  fire("htmx:beforeRequest", listReq(X));
  fire(ev, listReq(X));
  check(!!box.querySelector(".catalog-state--error") && !box.querySelector(".card"), `${ev}: muestra el estado de ERROR y descarta las tarjetas anteriores`);
  check(box.getAttribute("aria-busy") === null, `${ev}: sin aria-busy colgado`);
  const retry = box.querySelector("[data-dw-nav-retry]");
  fire("click", { elt: retry });
  check(calls.trigger.length === 1 && calls.trigger[0][0] === "catalog-lob" && calls.trigger[0][1] === "change", `${ev}: el reintento re-dispara el cambio del selector`);
}
reset();
const Y = xhr();
fire("htmx:beforeRequest", listReq(Y));
fire("htmx:sendAbort", listReq(Y));                          // abortada SIN sucesora: nadie más va a pintar → error con reintento
check(!!box.querySelector(".catalog-state--error"), "abort sin sucesora no deja tarjetas viejas ni loader");

// ── 4. panel central: loader inmediato, sin contenido previo ────────────────
reset();
content.scrollTop = 120;
slot.append(new El("section", { class: "module-hero" }));   // contenido del detalle anterior
const S1 = xhr();
fire("htmx:beforeRequest", slotReq(S1));
check(content.classList.contains("catalog-detail-open") && sandbox.dwModuleScroll === 120 && content.scrollTop === 0, "abre el panel, guarda el scroll del módulo y arranca desde arriba");
check(!!slot.querySelector("[data-dw-nav-loading]") && !slot.querySelector(".module-hero"), "el loader reemplaza el contenido anterior (no queda el detalle viejo)");
check(slot.getAttribute("aria-busy") === "true", "slot aria-busy");
// el swap del detalle lo reemplaza; afterRequest solo limpia aria-busy (el loader ya no está)
slot.replaceChildren(new El("section", { class: "module-hero" }));
fire("htmx:afterRequest", { ...slotReq(S1), successful: true });
check(slot.getAttribute("aria-busy") === null && content.classList.contains("catalog-detail-open") && !!slot.querySelector(".module-hero"), "con contenido incorporado el loader termina y el panel sigue abierto");

// ── 5. 2xx SIN contenido (204…): no queda un loader permanente ──────────────
reset();
const S2 = xhr();
fire("htmx:beforeRequest", slotReq(S2));
fire("htmx:afterRequest", { ...slotReq(S2), successful: true });
check(!slot.querySelector("[data-dw-nav-loading]") && !content.classList.contains("catalog-detail-open") && slot.getAttribute("aria-busy") === null, "204: se cierra el panel (sin loader permanente)");

// ── 6. panel central: tile B reemplaza a A; lo tardío de A no cuenta ─────────
reset();
const P = xhr(), Q = xhr();
fire("htmx:beforeRequest", slotReq(P, "get", "/catalog/flow?flow=A"));
fire("htmx:beforeRequest", slotReq(Q, "get", "/catalog/flow?flow=B"));
check(P.aborted === 1 && Q.aborted === 0, "el tile nuevo aborta la solicitud del anterior (una respuesta tardía de A no pisa a B)");
fire("htmx:afterRequest", slotReq(P));
fire("htmx:sendAbort", slotReq(P));
check(!!slot.querySelector("[data-dw-nav-loading]") && !slot.querySelector(".catalog-state--error") && slot.getAttribute("aria-busy") === "true", "el aborto de A no toca el loader de B");

// ── 7. panel central: error con reintento y volver; el reintento repite el MISMO GET ─────────
reset();
const E = xhr();
fire("htmx:beforeRequest", slotReq(E, "get", "/catalog/flow?flow=Z"));
fire("htmx:responseError", slotReq(E, "get", "/catalog/flow?flow=Z"));
check(!!slot.querySelector(".catalog-state--error") && !!slot.querySelector(".back") && !slot.querySelector("[data-dw-nav-loading]"), "error HTTP: estado de error con volver, sin loader");
fire("click", { elt: slot.querySelector("[data-dw-nav-retry]") });
check(calls.ajax.length === 1 && calls.ajax[0][0] === "GET" && calls.ajax[0][1] === "/catalog/flow?flow=Z" && calls.ajax[0][2] === "#catalog-detail-slot", "el reintento re-pide el mismo detalle");

// ── 8. "← Volver" durante la carga: aborta y la respuesta en vuelo no reabre nada ────────────
reset();
const V = xhr();
fire("htmx:beforeRequest", slotReq(V));
sandbox.window.DwCatalogNav.cancelDetail();
check(V.aborted === 1 && slot.getAttribute("aria-busy") === null, "cancelDetail aborta la solicitud y limpia aria-busy");
slot.replaceChildren();
content.cls.clear();
fire("htmx:afterRequest", slotReq(V));
fire("htmx:sendAbort", slotReq(V));
check(!slot.querySelector(".catalog-state--error") && !content.classList.contains("catalog-detail-open"), "lo tardío de la solicitud cancelada no reabre ni pinta nada");

// ── 9. lo que NO es navegación del Catálogo no se toca ──────────────────────
reset();
const other = new El("div", { id: "assistant-conversation" });
fire("htmx:beforeRequest", { elt: new El("form"), xhr: xhr(), target: other, requestConfig: { verb: "post" } });
fire("htmx:beforeRequest", slotReq(xhr(), "post"));          // POST al slot (p. ej. generar assessment) conserva su flujo
check(!content.classList.contains("catalog-detail-open") && !slot.querySelector("[data-dw-nav-loading]"), "POST al slot y otros destinos no activan el loader de navegación");
fire("htmx:responseError", { elt: new El("form"), xhr: xhr(), target: other });
check(box.querySelector(".card") && !box.querySelector(".catalog-state--error"), "errores ajenos no pintan estados en el Catálogo");

console.log(failures === 0 ? "OK" : `FAILED ${failures}`);
process.exit(failures === 0 ? 0 : 1);
