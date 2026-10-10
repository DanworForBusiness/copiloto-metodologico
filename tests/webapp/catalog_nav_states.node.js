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
  get childNodes() { return this.children; }
  cloneNode() { const c = new El(this.tag, this.attrs); c.cls = new Set(this.cls); this.children.forEach((k) => c.append(k.cloneNode())); return c; }
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
const calls = { trigger: [], ajax: [], process: [] };
let clock = 0, timerId = 0, timers = [], fakeNow = 1000000;
const fakeSetTimeout = (fn, ms) => { timers.push({ id: ++timerId, fn, at: clock + ms }); return timerId; };
const fakeClearTimeout = (id) => { timers = timers.filter((t) => t.id !== id); };
const advance = (ms) => { clock += ms; fakeNow += ms; timers.filter((t) => t.at <= clock).sort((a, b) => a.at - b.at).forEach((t) => { if (timers.includes(t)) { timers = timers.filter((x) => x !== t); t.fn(); } }); };
const sandbox = {
  console,
  dwModuleScroll: 0,
  htmx: { trigger: (el, ev) => calls.trigger.push([el.id, ev]), ajax: (verb, p, opts) => calls.ajax.push([verb, p, opts.target]), process: (el) => calls.process.push(el.id) },
  setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout, URL, location: { href: "http://localhost/" }, Date: { now: () => fakeNow },
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
const fire = (name, detail) => { let prevented = false; (listeners[name] || []).forEach((fn) => fn({ detail, target: detail.elt, preventDefault() { prevented = true; } })); return prevented; };
const xhr = () => ({ aborted: 0, abort() { this.aborted++; } });
const reset = () => { content.cls.clear(); content.scrollTop = 0; slot.replaceChildren(); slot.removeAttribute("aria-busy"); box.replaceChildren(new El("div", { class: "card" })); box.removeAttribute("aria-busy"); calls.trigger.length = 0; calls.ajax.length = 0; calls.process.length = 0; sandbox.dwModuleScroll = 0; sandbox.window.DwCatalogNav.cancelDetail(); timers = []; };
const listReq = (x) => ({ elt: select, xhr: x, target: box, requestConfig: { verb: "get" }, pathInfo: { finalRequestPath: "/catalog/lob?lob=X" } });
const slotReq = (x, verb = "get", p = "/catalog/flow?flow=a") => ({ elt: new El("li"), xhr: x, target: slot, requestConfig: { verb, path: p }, pathInfo: { finalRequestPath: p } });

// ── 0. cada evento se registra UNA sola vez (sin listeners duplicados) ──────
for (const n of ["htmx:beforeRequest", "htmx:afterRequest", "htmx:afterSwap", "htmx:responseError", "htmx:sendError", "htmx:timeout", "htmx:sendAbort", "click"])
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

// ── 4. panel central: loader PROPORCIONAL a la espera (umbral de continuidad) ────────────────
const GRACE = 150;
reset();
content.scrollTop = 120;
slot.append(new El("section", { class: "module-hero" }));   // contenido del detalle anterior
const S1 = xhr();
fire("htmx:beforeRequest", slotReq(S1));
check(!content.classList.contains("catalog-detail-open") && !slot.querySelector("[data-dw-nav-loading]") && !!slot.querySelector(".module-hero"), "antes del umbral: sin loader y el contenido anterior sigue visible (continuidad)");
check(slot.getAttribute("aria-busy") === "true", "slot aria-busy desde el inicio");
advance(GRACE - 1);
check(!slot.querySelector("[data-dw-nav-loading]"), "un instante antes del umbral todavía no hay loader");
advance(1);
check(content.classList.contains("catalog-detail-open") && sandbox.dwModuleScroll === 120 && content.scrollTop === 0, "superado el umbral: abre el panel, guarda el scroll del módulo y arranca desde arriba");
check(!!slot.querySelector("[data-dw-nav-loading]") && !slot.querySelector(".module-hero"), "el loader reemplaza el contenido anterior (no queda el detalle viejo)");
advance(5000);
check(!!slot.querySelector("[data-dw-nav-loading]"), "el loader dura lo que dure la espera real: sin tope ni mínimo artificial");
slot.replaceChildren(new El("section", { class: "module-hero" }));   // el swap del detalle lo reemplaza
fire("htmx:afterRequest", { ...slotReq(S1), successful: true });
check(slot.getAttribute("aria-busy") === null && content.classList.contains("catalog-detail-open") && !!slot.querySelector(".module-hero") && timers.length === 0, "con contenido incorporado el loader termina, el panel sigue abierto y no quedan timers");

// ── 4b. respuesta RÁPIDA (antes del umbral): jamás aparece el loader ni se vacía el panel ──
reset();
slot.append(new El("section", { class: "module-hero" }));
const F1 = xhr();
fire("htmx:beforeRequest", slotReq(F1));
advance(40);
slot.replaceChildren(new El("section", { class: "module-hero", id: "nuevo" }));   // swap
fire("htmx:afterSwap", { ...slotReq(F1), successful: true });
fire("htmx:afterRequest", { ...slotReq(F1), successful: true });
advance(10000);
check(!slot.querySelector("[data-dw-nav-loading]") && slot.querySelector("#nuevo") && timers.length === 0, "respuesta rápida: nunca hay loader (sin parpadeo) y el timer se cancela");

// ── 4c. fallo RÁPIDO (antes del umbral): el panel se abre y muestra el ERROR (no queda vacío) ──
reset();
const F2 = xhr();
fire("htmx:beforeRequest", slotReq(F2));
fire("htmx:responseError", slotReq(F2));
advance(10000);
check(content.classList.contains("catalog-detail-open") && !!slot.querySelector(".catalog-state--error") && !slot.querySelector("[data-dw-nav-loading]"), "fallo antes del umbral: abre el panel con el ERROR y jamás aparece un loader tardío");

// ── 5. 2xx SIN contenido (204…): no queda un loader permanente ──────────────
reset();
const S2 = xhr();
fire("htmx:beforeRequest", slotReq(S2));
advance(GRACE);                                              // la espera superó el umbral: hay loader
check(!!slot.querySelector("[data-dw-nav-loading]"), "precondición: loader visible");
fire("htmx:afterRequest", { ...slotReq(S2), successful: true });
check(!slot.querySelector("[data-dw-nav-loading]") && !content.classList.contains("catalog-detail-open") && slot.getAttribute("aria-busy") === null, "204: se cierra el panel (sin loader permanente)");
reset();
const S3 = xhr();
fire("htmx:beforeRequest", slotReq(S3));
fire("htmx:afterRequest", { ...slotReq(S3), successful: true });   // 204 rápido: nunca hubo loader
advance(10000);
check(!slot.querySelector("[data-dw-nav-loading]") && !content.classList.contains("catalog-detail-open"), "204 rápido: tampoco queda loader tardío");

// ── 6. panel central: tile B reemplaza a A; lo tardío de A no cuenta ─────────
reset();
const P = xhr(), Q = xhr();
fire("htmx:beforeRequest", slotReq(P, "get", "/catalog/flow?flow=A"));
fire("htmx:beforeRequest", slotReq(Q, "get", "/catalog/flow?flow=B"));
check(P.aborted === 1 && Q.aborted === 0 && timers.length === 1, "el tile nuevo aborta la solicitud del anterior y reemplaza su timer (un solo loader pendiente)");
advance(GRACE);
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
check(V.aborted === 1 && slot.getAttribute("aria-busy") === null && timers.length === 0, "cancelDetail aborta la solicitud, limpia aria-busy y cancela el timer del loader");
advance(10000);
check(!slot.querySelector("[data-dw-nav-loading]") && !content.classList.contains("catalog-detail-open"), "tras cancelar, el loader pendiente nunca aparece");
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


// ── 10. «← Volver» restaura el contenido ya cargado SIN repetir la solicitud (instantánea de un nivel) ─────────────
const FLOW = "/catalog/flow?cliente=K&dominio=Billing%20Doc&flow=a&pack=p";
const FLOW_BACK = "/catalog/flow?pack=p&flow=a&dominio=Billing+Doc&cliente=K";            // misma consulta: otro orden y `+` en lugar de `%20`
const ASSESS = "/catalog/flow/assessment?flow=a&pack=p";
const swapIn = (x, p, node) => { slot.replaceChildren(node); fire("htmx:afterSwap", { ...slotReq(x, "get", p), successful: true }); fire("htmx:afterRequest", { ...slotReq(x, "get", p), successful: true }); };
const hero = (mark) => { const h = new El("section", { class: "module-hero", "data-mark": mark }); h.append(new El("a", { class: "support-link" })); return h; };
const backLink = (x, p, cls = "catalog-back") => ({ elt: new El("a", { class: cls }), xhr: x, target: slot, requestConfig: { verb: "get", path: p }, pathInfo: { finalRequestPath: p } });
const forward = (p, mark) => { const x = xhr(); fire("htmx:beforeRequest", slotReq(x, "get", p)); swapIn(x, p, hero(mark)); return x; };

reset();
forward(FLOW, "flujo");                                      // abrir el tile
content.scrollTop = 340;
forward(ASSESS, "assessment");                               // ir al Assessment: se guarda el flujo
check(slot.querySelector(".module-hero").getAttribute("data-mark") === "assessment", "precondición: el slot muestra el Assessment");
const BK = xhr();
const prevented = fire("htmx:beforeRequest", backLink(BK, FLOW_BACK));
check(prevented === true, "Volver al contenido ya cargado cancela la solicitud (preventDefault): cero requests");
check(slot.querySelector(".module-hero").getAttribute("data-mark") === "flujo" && slot.getAttribute("aria-busy") === null && !slot.querySelector("[data-dw-nav-loading]"), "restaura el flujo (sin loader, sin aria-busy)");
check(content.scrollTop === 340 && calls.process.length === 1 && calls.trigger.some((t) => t[1] === "htmx:load"), "restaura el scroll y reengancha htmx (process + htmx:load)");
advance(10000);
check(!slot.querySelector("[data-dw-nav-loading]") && timers.length === 0, "tras restaurar no aparece ningún loader tardío");
// un nivel solamente: volver otra vez (sin instantánea) sí pide
const BK2 = xhr();
check(fire("htmx:beforeRequest", backLink(BK2, FLOW_BACK)) === false, "sin instantánea (ya consumida) la navegación normal sigue: se pide");

// invalidaciones: nunca se sirve contenido que pudo cambiar
const invalid = (label, arrange, req) => {
  reset();
  forward(FLOW, "flujo");
  forward(ASSESS, "assessment");
  arrange();
  const r = fire("htmx:beforeRequest", req());
  check(r === false && slot.getAttribute("aria-busy") === "true", label);
};
invalid("un POST intermedio (p. ej. regenerar) invalida la instantánea: Volver pide de nuevo", () => { fire("htmx:beforeRequest", { elt: new El("button"), xhr: xhr(), target: slot, requestConfig: { verb: "post" } }); }, () => backLink(xhr(), FLOW_BACK));
invalid("pasado el TTL no se restaura", () => advance(121000), () => backLink(xhr(), FLOW_BACK));
invalid("otra URL no se restaura (solo la MISMA consulta)", () => {}, () => backLink(xhr(), "/catalog/flow?cliente=K&dominio=Billing%20Doc&flow=OTRO&pack=p"));
invalid("un enlace que no es «Volver» (sin .catalog-back) no restaura", () => {}, () => backLink(xhr(), FLOW_BACK, "support-link"));
invalid("cerrar el detalle descarta la instantánea", () => { sandbox.window.DwCatalogNav.cancelDetail(); }, () => backLink(xhr(), FLOW_BACK));
reset();
forward(FLOW, "flujo"); forward(ASSESS, "assessment"); forward("/catalog/client/assessment?flow=a&pack=p", "informe");
check(fire("htmx:beforeRequest", backLink(xhr(), FLOW_BACK)) === false, "la instantánea es de UN nivel: A→B→C solo permite volver a B, no a A");
reset();
forward(FLOW, "flujo"); forward(ASSESS, "assessment"); forward("/catalog/client/assessment?flow=a&pack=p", "informe");
check(fire("htmx:beforeRequest", backLink(xhr(), ASSESS)) === true && slot.querySelector(".module-hero").getAttribute("data-mark") === "assessment", "A→B→C: Volver a B restaura B");

// con una carga en vuelo que ya mostró loader, la instantánea sigue siendo el contenido real abandonado
reset();
forward(FLOW, "flujo");
const L1 = xhr(); fire("htmx:beforeRequest", slotReq(L1, "get", ASSESS)); advance(GRACE);           // loader visible
const L2 = xhr(); fire("htmx:beforeRequest", slotReq(L2, "get", "/catalog/client/assessment?flow=a&pack=p"));
check(L1.aborted === 1 && fire("htmx:beforeRequest", backLink(xhr(), FLOW_BACK)) === true && slot.querySelector(".module-hero").getAttribute("data-mark") === "flujo" && L2.aborted === 1,
      "Volver a un contenido guardado durante una carga en vuelo restaura y aborta la carga pendiente");

console.log(failures === 0 ? "OK" : `FAILED ${failures}`);
process.exit(failures === 0 ? 0 : 1);
