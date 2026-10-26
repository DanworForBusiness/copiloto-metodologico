// human_capital_candidates_selection.node.js — invocado por test_human_capital_candidates_selection.py.
//
// Prueba el módulo `webapp/static/human_capital_candidates_selection.js` (CCHH · Candidatos: selección múltiple + «Compartir perfiles») contra un DOM mínimo propio (no hay jsdom en el
// repo): almacén de ids en sessionStorage (solo ids, sin datos personales), casilla de encabezado SOLO sobre la página visible (tri-estado), persistencia entre «páginas» (re-render del
// panel), contador y «Limpiar», límite de 25, enlaces al CV (validación, elección entre varios), mensaje HTML/texto plano, copiado con formato, límite práctico del `mailto:` y flujo del diálogo.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, b, label) { ok(JSON.stringify(a) === JSON.stringify(b), `${label} (esperado ${JSON.stringify(b)}, real ${JSON.stringify(a)})`); }

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "human_capital_candidates_selection.js"), "utf-8");

function load(extra) {
  const sandbox = Object.assign({ window: {}, console }, extra || {});
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "human_capital_candidates_selection.js" });
  return sandbox.window.__cchhSelection;
}
const api = load();

// ── DOM mínimo ────────────────────────────────────────────────────────────────────────────
function el(sels, attrs) {
  const node = {
    sels: sels || [], attrs: Object.assign({}, attrs || {}), checked: false, indeterminate: false, disabled: false, hidden: false, textContent: "", value: "", parent: null, q: {},
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); } },
    getAttribute(n) { return n in node.attrs ? node.attrs[n] : null; },
    setAttribute(n, v) { node.attrs[n] = String(v); },
    removeAttribute(n) { delete node.attrs[n]; },
    hasAttribute(n) { return n in node.attrs; },
    matches(sel) { return sel.split(",").map(s => s.trim()).some(s => node.sels.includes(s)); },
    closest(sel) { let n = node; while (n) { if (n.matches(sel)) return n; n = n.parent; } return null; },
    querySelector(sel) { return node.q[sel] || null; },
  };
  return node;
}
function box(id, row) { const b = el(["[data-hc-select]"], { "data-hc-select": id }); b.parent = row || el(["tr"]); return b; }
function makeStorage(initial) {
  const data = Object.create(null);
  if (initial !== undefined) data.k = initial;
  return { data, getItem(k) { return k in data ? data[k] : null; }, setItem(k, v) { data[k] = String(v); } };
}
function storeWith(storage) { return api.createStore({ getItem: k => storage.getItem(k === api.STORAGE_KEY ? "k" : k), setItem: (k, v) => storage.setItem("k", v) }); }

function barEl() {
  const bar = el(["[data-hc-selection-bar]"]);
  ["[data-hc-selection-count]", "[data-hc-selection-label]", "[data-hc-selection-hint]", "[data-hc-share-open]", "[data-hc-download-open]", "[data-hc-clear-selection]"].forEach(s => { bar.q[s] = el([s]); });
  return bar;
}
function pageDoc(ids) {
  const boxes = ids.map(i => box(i));
  const head = el(["[data-hc-select-page]"]);
  const bar = barEl();
  return { boxes, head, bar, querySelectorAll(sel) { return sel === "[data-hc-select]" ? boxes : sel === "[data-hc-select-page]" ? [head] : sel === "[data-hc-selection-bar]" ? [bar] : []; } };
}
const ids = n => Array.from({ length: n }, (_, i) => `id-${String(i).padStart(3, "0")}`);

// ── A: almacén: SOLO ids, en sessionStorage, forma exacta ──────────────────────────────────
{
  const st = makeStorage();
  const store = storeWith(st);
  store.add(["a1", "b2"]);
  eq(JSON.parse(st.data.k), { v: 1, ids: ["a1", "b2"] }, "A: lo persistido es EXACTAMENTE {v, ids} (sin nombres ni datos personales)");
  store.add("a1");
  store.add(["c3", "a1"]);
  eq(store.ids(), ["a1", "b2", "c3"], "A: sin duplicados y en orden de selección");
  store.remove(["b2"]);
  eq(store.ids(), ["a1", "c3"], "A: remove quita solo lo pedido");
  store.clear();
  eq(JSON.parse(st.data.k).ids, [], "A: clear vacía y persiste");
  ok(store.size() === 0 && !store.has("a1"), "A: tras clear no queda nada");
}

// ── B: ids inválidos descartados; tope; carga robusta ──────────────────────────────────────
{
  const store = storeWith(makeStorage());
  store.add(["ok-1", "con espacio", "", "<img src=x>", "x".repeat(65), 42, null, { a: 1 }, "Juan Perez", "ok_2"]);
  eq(store.ids(), ["ok-1", "ok_2"], "B: solo ids bien formados (un nombre, HTML o un objeto no entran)");
  const big = storeWith(makeStorage());
  big.add(Array.from({ length: 2500 }, (_, i) => `i${i}`));
  ok(big.size() === 2000, "B: el almacén tiene un tope duro (2000)");
  eq(storeWith(makeStorage("{no-json")).ids(), [], "B: JSON corrupto ⇒ vacío (fail-closed)");
  eq(storeWith(makeStorage(JSON.stringify({ v: 2, ids: ["a"] }))).ids(), [], "B: versión desconocida ⇒ vacío");
  eq(storeWith(makeStorage(JSON.stringify({ v: 1, ids: "a" }))).ids(), [], "B: forma inesperada ⇒ vacío");
  eq(storeWith(makeStorage(JSON.stringify({ v: 1, ids: ["ok", "bad id", 7, "ok", "z"] }))).ids(), ["ok", "z"], "B: al cargar se filtran y deduplican los ids");
  const broken = api.createStore({ getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } });
  broken.add(["a", "b"]);
  eq(broken.ids(), ["a", "b"], "B: si sessionStorage falla (modo privado/cuota) sigue en memoria y no lanza");
  eq(api.createStore(null).add("x") && true, true, "B: sin storage también funciona");
}

// ── C: solo sessionStorage (jamás localStorage) ────────────────────────────────────────────
{
  let localTouched = false;
  const sess = makeStorage();
  const win = { sessionStorage: { getItem: k => sess.getItem("k"), setItem: (k, v) => sess.setItem("k", v) } };
  Object.defineProperty(win, "localStorage", { get() { localTouched = true; throw new Error("localStorage prohibido"); } });
  let listeners = 0;
  const doc = { body: { addEventListener() { listeners++; } }, readyState: "complete", addEventListener() { listeners++; }, querySelectorAll() { return []; }, getElementById() { return null; } };
  load({ window: win, document: doc });
  ok(!localTouched, "C: el módulo nunca toca localStorage");
  ok(listeners >= 4, "C: se cablea por delegación en document/body al cargar");
  ok(!/localStorage/.test(SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "")), "C: el código no menciona localStorage fuera de comentarios");
}

// ── D: sincronización: casillas, tri-estado, barra ─────────────────────────────────────────
{
  const store = storeWith(makeStorage());
  const d = pageDoc(["p1", "p2", "p3"]);
  api.syncUI(d, store);
  ok(d.bar.hidden === true, "D: sin selección la barra está OCULTA (no ocupa lugar)");
  ok(d.head.checked === false && d.head.indeterminate === false && d.head.disabled === false, "D: encabezado vacío con filas visibles");
  store.add(["p2"]);
  api.syncUI(d, store);
  ok(d.boxes[1].checked && !d.boxes[0].checked, "D: se marca solo la fila seleccionada");
  ok(d.head.indeterminate === true && d.head.checked === false, "D: encabezado indeterminado con selección parcial");
  ok(d.bar.hidden === false && d.bar.q["[data-hc-selection-count]"].textContent === "1" && d.bar.q["[data-hc-selection-label]"].textContent === "seleccionado", "D: contador visible, singular");
  store.add(["p1", "p3"]);
  api.syncUI(d, store);
  ok(d.head.checked === true && d.head.indeterminate === false, "D: encabezado marcado cuando TODAS las visibles están seleccionadas");
  ok(d.bar.q["[data-hc-selection-label]"].textContent === "seleccionados", "D: plural");
  ok(d.boxes[0].parent.classList.contains("is-selected"), "D: la fila seleccionada se resalta");
  const empty = pageDoc([]);
  api.syncUI(empty, store);
  ok(empty.head.disabled === true, "D: sin filas visibles el encabezado se deshabilita");
  ok(empty.bar.hidden === false && empty.bar.q["[data-hc-selection-count]"].textContent === "3", "D: la barra sigue mostrando lo seleccionado aunque la página no tenga filas (filtro sin resultados)");
}

// ── E: «hay seleccionados fuera de la vista» y límite de 25 ────────────────────────────────
{
  const store = storeWith(makeStorage());
  store.add(["x1", "x2", "p1"]);
  const d = pageDoc(["p1", "p2"]);
  api.syncUI(d, store);
  ok(/2 fuera de la vista actual/.test(d.bar.q["[data-hc-selection-hint]"].textContent), "E: la barra avisa cuántos seleccionados no se ven (otras páginas o filtros)");
  ok(d.bar.q["[data-hc-share-open]"].disabled === false, "E: con 3 seleccionados «Compartir» está habilitado");
  ok(d.bar.q["[data-hc-download-open]"].disabled === false, "E: con 3 seleccionados «Descargar CVs» está habilitado");
  store.clear();
  store.add(ids(25));
  api.syncUI(d, store);
  ok(d.bar.q["[data-hc-share-open]"].disabled === false, "E: 25 es el máximo permitido");
  store.add("id-extra");
  api.syncUI(d, store);
  ok(d.bar.q["[data-hc-share-open]"].disabled === true, "E: con 26 «Compartir» se deshabilita");
  ok(d.bar.q["[data-hc-download-open]"].disabled === true && /hasta 25 candidatos/.test(d.bar.q["[data-hc-download-open]"].getAttribute("title")), "E: con 26 «Descargar CVs» se deshabilita y explica el límite");
  ok(/hasta 25 perfiles/.test(d.bar.q["[data-hc-selection-hint]"].textContent) && /hasta 25/.test(d.bar.q["[data-hc-share-open]"].getAttribute("title")), "E: y se explica el motivo (texto + title)");
}

// ── F: cableado: la casilla de encabezado actúa SOLO sobre la página visible; persiste entre páginas; «Limpiar» ──
function wired(initialIds) {
  const handlers = Object.create(null), bodyHandlers = Object.create(null);
  const state = pageDoc(initialIds);
  const doc = Object.assign(state, {
    readyState: "complete",
    body: { addEventListener(t, fn) { bodyHandlers[t] = fn; } },
    addEventListener(t, fn) { handlers[t] = fn; },
    getElementById() { return null; },
  });
  const store = storeWith(makeStorage());
  api.wire(doc, { navigator: {} }, store);
  return { doc, store, handlers, bodyHandlers, fire(type, target, extra) { handlers[type](Object.assign({ target, preventDefault() { this.prevented = true; } }, extra || {})); } };
}
{
  const w = wired(["p1", "p2", "p3"]);
  w.doc.boxes[0].checked = true;
  w.fire("change", w.doc.boxes[0]);
  eq(w.store.ids(), ["p1"], "F: marcar una casilla agrega su id");
  w.store.add(["otra-pagina-1", "otra-pagina-2"]);                                                // selección hecha en otra página/filtro
  w.doc.head.checked = true;
  w.fire("change", w.doc.head);
  eq(w.store.ids(), ["p1", "otra-pagina-1", "otra-pagina-2", "p2", "p3"], "F: la casilla de encabezado agrega SOLO las visibles; lo de otras páginas no se toca");
  w.doc.head.checked = false;
  w.fire("change", w.doc.head);
  eq(w.store.ids(), ["otra-pagina-1", "otra-pagina-2"], "F: deseleccionar desde el encabezado quita SOLO las visibles; la selección de otras páginas persiste");
  w.doc.boxes[1].checked = true;
  w.fire("change", w.doc.boxes[1]);
  w.doc.boxes[1].checked = false;
  w.fire("change", w.doc.boxes[1]);
  ok(!w.store.has("p2"), "F: se puede deseleccionar una fila sin ambigüedad");
  // «paginar»: el panel se reemplaza con otras filas; tras el swap se re-marca desde el almacén
  const second = ["otra-pagina-1", "n1", "n2"].map(i => box(i));
  w.doc.boxes.length = 0;
  second.forEach(b => w.doc.boxes.push(b));
  w.bodyHandlers["htmx:afterSwap"]({ detail: { target: { id: "hc-profiles-panel" } } });
  ok(second[0].checked === true && second[1].checked === false, "F: tras el swap del panel se re-marcan las casillas visibles desde la selección guardada");
  second.forEach(b => { b.checked = false; });
  w.bodyHandlers["htmx:afterSwap"]({ detail: { target: { id: "otro-elemento" } } });
  ok(second[0].checked === false, "F: un swap de otro elemento no dispara la re-sincronización");
  const clear = el(["[data-hc-clear-selection]"], { "data-hc-clear-selection": "" });
  clear.closest = sel => (sel.indexOf("[data-hc-clear-selection]") >= 0 ? clear : null);
  w.fire("click", clear);
  ok(w.store.size() === 0 && w.doc.bar.hidden === true, "F: «Limpiar selección» vacía el almacén y oculta la barra otra vez");
}

// ── F2: al aparecer/desaparecer la barra, el control accionado queda donde estaba (no se desvía el clic siguiente) ─────────────
{
  const BAR_H = 72;
  const scrollBox = { scrollTop: 0, scrollHeight: 5000, clientHeight: 700 };
  function world(startTop) {
    const store = storeWith(makeStorage());
    const doc = pageDoc(["p1", "p2", "p3"]);
    scrollBox.scrollTop = startTop;
    const b = doc.boxes[1];
    b.isConnected = true; b.parentElement = { parentElement: null, __scroll: scrollBox };
    // el alto de la barra empuja la tabla (y la casilla) hacia abajo mientras está visible; el desplazamiento del contenedor la sube de vuelta
    b.getBoundingClientRect = () => ({ top: 400 + (doc.bar.hidden ? 0 : BAR_H) - (scrollBox.scrollTop - startTop) });
    doc.defaultView = { getComputedStyle: n => ({ overflowY: n === b.parentElement ? "auto" : "visible" }) };
    b.parentElement.scrollHeight = 5000; b.parentElement.clientHeight = 700;
    Object.defineProperty(b.parentElement, "scrollTop", { get: () => scrollBox.scrollTop, set: v => { scrollBox.scrollTop = v; } });
    api.syncUI(doc, store);
    return { store, doc, b };
  }
  let w = world(0);
  const top0 = w.b.getBoundingClientRect().top;
  api.keepInPlace(w.doc, w.b, () => { w.store.add("p2"); api.syncUI(w.doc, w.store); });
  ok(w.doc.bar.hidden === false && scrollBox.scrollTop === BAR_H && w.b.getBoundingClientRect().top === top0, "F2: al mostrarse la barra el contenedor se desplaza su alto y la casilla accionada NO se mueve");
  const top1 = w.b.getBoundingClientRect().top;
  api.keepInPlace(w.doc, w.b, () => { w.store.remove("p2"); api.syncUI(w.doc, w.store); });
  ok(w.doc.bar.hidden === true && scrollBox.scrollTop === 0 && w.b.getBoundingClientRect().top === top1, "F2: al ocultarse (último deseleccionado) se revierte y tampoco se mueve");
  w = world(300);
  api.keepInPlace(w.doc, w.b, () => { w.store.add("p2"); api.syncUI(w.doc, w.store); });
  ok(scrollBox.scrollTop === 300 + BAR_H, "F2: con la página ya desplazada compensa igual");
  // un cambio que no mueve nada no toca el desplazamiento
  w = world(0);
  w.store.add("p1"); api.syncUI(w.doc, w.store);
  scrollBox.scrollTop = 123;
  api.keepInPlace(w.doc, w.b, () => { w.store.add("p3"); api.syncUI(w.doc, w.store); });
  ok(scrollBox.scrollTop === 123 + 0, "F2: sin cambio de altura (la barra ya estaba) no se altera el desplazamiento");
  // robustez: un DOM sin getBoundingClientRect no rompe la selección
  const bare = el(["[data-hc-select]"]);
  let ran = false;
  api.keepInPlace({}, bare, () => { ran = true; });
  ok(ran, "F2: sin medición posible se ejecuta el cambio igual");
}

// ── G: enlaces al CV (validación), filas, mensaje HTML/plano y mailto ──────────────────────
const SP = "https://danwor.sharepoint.com/sites/cchh/Shared%20Documents/";
{
  // validación de URL: solo https://*.sharepoint.com sin credenciales ni caracteres peligrosos
  ["https://danwor.sharepoint.com/x", "https://danwor-my.sharepoint.com/personal/a/b.pdf", "https://DANWOR.SHAREPOINT.COM:443/x"].forEach(u => ok(api.safeCvUrl(u) === u, "G: URL válida aceptada: " + u));
  ["javascript:alert(1)", "http://danwor.sharepoint.com/x", "https://evil.com/x", "https://u:pw@danwor.sharepoint.com/x", "https://danwor.sharepoint.com@evil.com/x", "https://sharepoint.com/x", "https://.sharepoint.com/x",
   "https://danwor.sharepoint.com.evil.io/x", "https://danwor.sharepoint.com/a b", 'https://danwor.sharepoint.com/x"y', "https://danwor.sharepoint.com/x<y", "https://danwor.sharepoint.com/" + "a".repeat(2100), "/sites/x", "", null, undefined, 5]
    .forEach(u => ok(api.safeCvUrl(u) === null, "G: URL inválida rechazada: " + String(u).slice(0, 40)));

  // filas: un CV con enlace ⇒ incluido; varios ⇒ hay que elegir (nunca se elige solo); sin CV / sin enlace ⇒ informado y excluido
  const items = [
    { id: "u1", name: "Ana Uno", docs: [{ id: "d1", label: "a.pdf · 2026-01-01", url: SP + "a.pdf" }] },
    { id: "m1", name: "Beto Multi", docs: [{ id: "d2", label: "nuevo.pdf", url: SP + "n.pdf" }, { id: "d3", label: "viejo.pdf", url: SP + "v.pdf" }, { id: "d4", label: "roto.pdf", url: null }] },
    { id: "n1", name: "Cata Sin", docs: [] },
    { id: "l1", name: "Dani SinLink", docs: [{ id: "d5", label: "x.pdf", url: "javascript:alert(1)" }] },
    { id: "l2", name: "Eli TodosSinLink", docs: [{ id: "d6", label: "y.pdf", url: null }, { id: "d7", label: "z.pdf", url: "http://x.sharepoint.com/z" }] },
  ];
  let rows = api.rowsModel(items, {});
  eq(rows.map(r => r.kind), ["linked", "pending", "no_cv", "no_link", "no_link"], "G: un CV con enlace ⇒ incluido; varios sin elección ⇒ PENDIENTE; sin CV ⇒ no_cv; sin enlace válido ⇒ no_link");
  ok(rows[1].multi === true && rows[0].multi === false && rows[4].multi === false, "G: solo con varios CVs (con algún enlace) se ofrece elegir");
  ok(rows[1].options.length === 3 && rows[1].options[2].disabled === true && rows[1].options[0].disabled === false, "G: se listan TODOS los CVs; el que no tiene enlace queda deshabilitado");
  ok(rows[1].selected === "" && rows[1].url === null, "G: sin elección no hay URL ni CV preseleccionado (nunca se elige uno arbitrariamente)");
  let m = api.resolveShare(items, {});
  eq(m.included.map(n => n.name), ["Ana Uno"], "G: sin resolver el CV múltiple, solo está incluido el candidato sin ambigüedad");
  eq(m.pending, ["Beto Multi"], "G: Beto queda pendiente");
  eq(m.excluded.map(n => n.reason), ["no_cv", "no_link", "no_link"], "G: excluidos con su motivo");
  m = api.resolveShare(items, { m1: "d3" });
  eq(m.included.map(n => [n.name, n.url]), [["Ana Uno", SP + "a.pdf"], ["Beto Multi", SP + "v.pdf"]], "G: al elegir, se incluye ESE CV (y solo ese)");
  ok(m.pending.length === 0, "G: ya no hay pendientes");
  m = api.resolveShare(items, { m1: "d4" });
  eq(m.pending, ["Beto Multi"], "G: elegir un CV sin enlace NO resuelve (sigue pendiente)");
  m = api.resolveShare(items, { m1: "no-existe" });
  eq(m.pending, ["Beto Multi"], "G: una elección que no corresponde a ningún CV no se acepta");
  m = api.resolveShare(items, { m1: api.SKIP });
  ok(m.pending.length === 0 && m.excluded.some(n => n.name === "Beto Multi" && n.reason === "skipped") && !m.included.some(n => n.name === "Beto Multi"), "G: «No incluir» omite al candidato explícitamente");

  // mensaje: texto plano con URL completa; HTML con el nombre como hipervínculo y sin URL visible
  const model = { included: [{ name: "Ana Pérez", url: SP + "a.pdf" }, { name: "Luis Gómez", url: SP + "l.pdf?x=1&y=2" }], excluded: [], pending: [] };
  eq(api.buildPlain(model, "Hola:", "Saludos."), "Hola:\n\n- Ana Pérez: " + SP + "a.pdf\n- Luis Gómez: " + SP + "l.pdf?x=1&y=2\n\nSaludos.", "G: texto plano = «- Nombre: URL completa» (fallback con URLs completas)");
  const html = api.buildHtml(model, "Hola:\nsegunda línea", "Saludos.");
  eq(html, "<p>Hola:<br>segunda línea</p><ul><li><a href=\"" + SP + "a.pdf\">Ana Pérez</a></li><li><a href=\"" + SP + "l.pdf?x=1&amp;y=2\">Luis Gómez</a></li></ul><p>Saludos.</p>", "G: HTML: el nombre es el enlace, la URL solo va en el href (escapada)");
  ok(!/>https?:/.test(html) && !html.includes(">" + SP), "G: ninguna URL larga queda visible como texto en el HTML");
  const hostile = api.buildHtml({ included: [{ name: "O'Hara <img src=x onerror=1>", url: SP + "x.pdf" }], excluded: [], pending: [] }, "<script>alert(1)</script>", "a & b");
  ok(!hostile.includes("<script>") && !hostile.includes("<img") && hostile.includes("&lt;script&gt;") && hostile.includes("O&#39;Hara &lt;img src=x onerror=1&gt;") && hostile.includes("a &amp; b"), "G: nombres, intro y cierre se escapan en el HTML");
  eq(api.escapeHtml('<>&"\''), "&lt;&gt;&amp;&quot;&#39;", "G: escape de los cinco caracteres");

  // mailto: texto plano
  const url = api.mailtoUrl("Asunto & más", "linea 1\nlinea 2 100% #ok https://x/y");
  ok(url.startsWith("mailto:?subject=Asunto%20%26%20m%C3%A1s&body="), "G: el asunto se codifica");
  ok(url.includes("linea%201%0D%0Alinea%202%20100%25%20%23ok%20https://x/y"), "G: saltos de línea CRLF, %, # y & codificados; ':' y '/' sin inflar");
  const fitsFor = n => api.mailtoFits("Perfiles", api.buildPlain({ included: Array.from({ length: n }, (_, k) => ({ name: `Candidato Numero ${k}`, url: SP + `Carpeta%20de%20candidato%20${k}/CV_Candidato_Numero_${k}_2026.pdf` })) }, "Intro", "Fin"));
  ok(fitsFor(2) === true && fitsFor(25) === false, "G: pocos CVs entran en el límite práctico del mailto (URLs completas de SharePoint); 25 no");
  ok(api.mailtoUrl("a", "b").length < api.MAILTO_MAX, "G: el límite práctico está declarado (≈1900)");
}

// ── H: diálogo: DOM, elección de CV, Outlook, copiado ──────────────────────────────────────
function mini(tag) {   // DOM mínimo con árbol, para renderList/refreshShare
  const n = el([]);
  n.tag = tag; n.children = []; n.style = {}; n.innerHTML = "";
  n.appendChild = c => { n.children.push(c); c.parent = n; return c; };
  n.removeChild = c => { n.children.splice(n.children.indexOf(c), 1); return c; };
  Object.defineProperty(n, "firstChild", { get: () => n.children[0] || null });
  n.focus = () => { n.focused = true; };
  n.querySelector = sel => {
    const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
    const walk = node => { for (const c of node.children) { if (m && (m[2] === undefined ? c.hasAttribute(m[1]) : c.getAttribute(m[1]) === m[2])) return c; const r = walk(c); if (r) return r; } return null; };
    return n.q[sel] || walk(n);
  };
  return n;
}
function shareDoc(opts) {
  const root = el(["[data-share-root]"], { "data-default-subject": "Perfiles", "data-intro": "Hola", "data-outro": "Chau" });
  const subject = el(["[data-share-subject]"]), intro = el(["[data-share-intro]"]), outro = el(["[data-share-outro]"]);
  const list = mini("ul"), note = el(["[data-share-note]"]), pend = el(["[data-share-pending]"]), stat = el(["[data-share-status]"]);
  const link = el(["[data-share-open]"], { href: "#" }), copy = el(["[data-share-copy]"], { "data-share-copy": "mensaje" });
  const itemsEl = el([]); itemsEl.textContent = JSON.stringify(opts.items);
  const unavEl = el([]); unavEl.textContent = JSON.stringify(opts.unavailable || []);
  // Estructura REAL de la plantilla: asunto/intro/lista/cierre/notas viven dentro de `data-share-root`; las acciones («Copiar mensaje», «Abrir Outlook») y los JSON quedan FUERA.
  root.q = { "[data-share-subject]": subject, "[data-share-intro]": intro, "[data-share-outro]": outro, "[data-share-list]": list, "[data-share-note]": note, "[data-share-pending]": pend, "[data-share-status]": stat };
  const content = mini("div");
  content.q = Object.assign({}, root.q, { "[data-share-root]": root, "[data-share-open]": link, '[data-share-copy="mensaje"]': copy, "[data-share-items]": itemsEl, "[data-share-unavailable]": unavEl });
  content.querySelector = sel => content.q[sel] || list.querySelector(sel);
  const base = pageDoc(opts.visible || []);
  const d = Object.assign(base, { getElementById: id => (id === "hc-share-content" ? content : null), createElement: tag => mini(tag) });
  return { d, subject, intro, outro, list, link, copy, note, pend, stat };
}
{
  const store = storeWith(makeStorage());
  store.add(["u1", "u2", "keep", "multi"]);
  const items = [{ id: "keep", name: "Ana", docs: [{ id: "d1", label: "a.pdf", url: SP + "a.pdf" }] },
                 { id: "multi", name: "Beto", docs: [{ id: "d2", label: "n.pdf · 2026-09-01", url: SP + "n.pdf" }, { id: "d3", label: "v.pdf · 2025-03-01", url: SP + "v.pdf" }] }];
  const s = shareDoc({ items, unavailable: ["u1", "u2"], visible: ["keep"] });
  api.initShare(s.d, store, { location: { origin: "https://hub.danwor.test" } });
  eq(store.ids(), ["keep", "multi"], "H: los ids que el servidor marcó como no disponibles se sacan de la selección (el contador no miente)");
  ok(s.subject.value === "Perfiles" && s.intro.value === "Hola" && s.outro.value === "Chau", "H: asunto, introducción y cierre se inicializan editables");
  // vista previa: nombres como enlaces; el candidato con varios CVs muestra el selector y está pendiente
  eq(s.list.children.length, 2, "H: una fila por candidato");
  const li0 = s.list.children[0];
  const a = li0.children.find(c => c.getAttribute("href"));
  ok(li0.children[0].textContent === "Ana" && a.textContent === "{Link}" && !li0.children.some(c => /https?:/.test(c.textContent)), "H: cada candidato se ve como «Nombre: {Link}», sin URLs largas a la vista");
  ok(a.getAttribute("href") === SP + "a.pdf" && a.getAttribute("rel") === "noopener noreferrer" && a.getAttribute("target") === "_blank" && /Ana/.test(a.getAttribute("title")), "H: {Link} abre ESE CV (nueva pestaña, sin opener) para poder comprobarlo");
  const sel = s.list.querySelector('[data-share-choose="multi"]');
  ok(sel && sel.children.length === 4 && sel.value === "", "H: el candidato con varios CVs ofrece 'Elegir CV…', cada CV y 'No incluir' — sin nada elegido");
  ok(s.pend.hidden === false && /Falta elegir el CV de 1 candidato/.test(s.pend.textContent), "H: avisa que falta elegir el CV");
  ok(s.copy.disabled === true && s.link.getAttribute("aria-disabled") === "true" && s.link.getAttribute("href") === "#", "H: mientras falte elegir, «Copiar mensaje» y «Abrir Outlook» están deshabilitados");
  // textContent, nunca innerHTML con datos
  ok(!s.list.children.some(li => li.innerHTML), "H: la vista previa no usa innerHTML");
}
{
  // cableado completo del diálogo (elección, copiar, edición de campos)
  const store = storeWith(makeStorage());
  store.add(["multi"]);
  const items = [{ id: "keep", name: "Ana", docs: [{ id: "d1", label: "a.pdf", url: SP + "a.pdf" }] },
                 { id: "multi", name: "Beto", docs: [{ id: "d2", label: "n.pdf", url: SP + "n.pdf" }, { id: "d3", label: "v.pdf", url: SP + "v.pdf" }] }];
  const s = shareDoc({ items, visible: [] });
  const handlers = {};
  s.d.addEventListener = (n, f) => { handlers[n] = f; };
  s.d.body = { addEventListener() {} };
  s.d.readyState = "complete";
  const clips = [];
  const win = { location: { origin: "https://hub.danwor.test" }, navigator: {}, Blob: function (parts, opts) { this.parts = parts; this.type = opts.type; } };
  api.wire(s.d, win, store);
  api.initShare(s.d, store, win);
  const choose = value => {
    const select = s.list.querySelector('[data-share-choose="multi"]');
    select.value = value;
    select.matches = sel => sel.split(",").map(x => x.trim()).includes("[data-share-choose]");
    select.getAttribute = n => (n === "data-share-choose" ? "multi" : null);
    handlers.change({ target: select });
  };
  choose("d3");
  ok(s.copy.disabled === false && s.pend.hidden === true && s.link.getAttribute("href").startsWith("mailto:?subject=Perfiles&body="), "H: al elegir el CV se habilitan las acciones");
  const body = decodeURIComponent(s.link.getAttribute("href").split("&body=")[1]).replace(/\r\n/g, "\n");
  ok(body.includes("- Beto: " + SP + "v.pdf") && body.includes("- Ana: " + SP + "a.pdf") && !body.includes("n.pdf"), "H: «Abrir Outlook» usa texto plano con las URLs completas del CV ELEGIDO (y de ningún otro)");
  const chosen = s.list.querySelector('[data-share-choose="multi"]');
  ok(chosen.focused === true, "H: tras elegir se conserva el foco en el selector");
  choose(api.SKIP);
  ok(s.pend.hidden === true && !decodeURIComponent(s.link.getAttribute("href")).includes("Beto"), "H: «No incluir» saca al candidato del mensaje");
  choose("");
  ok(s.copy.disabled === true, "H: volver a «Elegir CV…» deja el mensaje otra vez incompleto");
  choose("d2");
  // editar el asunto/introducción actualiza el mailto
  s.intro.value = "Intro nueva";
  s.intro.matches = sel => sel.split(",").map(x => x.trim()).includes("[data-share-intro]");
  handlers.input({ target: s.intro });
  ok(decodeURIComponent(s.link.getAttribute("href")).includes("Intro nueva"), "H: la introducción editada se refleja en el mensaje");
  // Outlook se deshabilita por longitud (con nota que manda a «Copiar mensaje»)
  s.intro.value = "x".repeat(2200);
  handlers.input({ target: s.intro });
  ok(s.link.getAttribute("href") === "#" && s.link.getAttribute("aria-disabled") === "true" && s.link.classList.contains("is-disabled"), "H: si el mensaje es demasiado largo «Abrir Outlook» se deshabilita");
  ok(s.note.hidden === false && /Copiar mensaje/.test(s.note.textContent) && /texto plano/.test(s.note.textContent) && s.copy.disabled === false, "H: con una nota honesta (el mailto es texto plano) y «Copiar mensaje» sigue disponible");
  s.intro.value = "corto";
  handlers.input({ target: s.intro });
  ok(s.link.getAttribute("aria-disabled") === null && s.link.getAttribute("href").startsWith("mailto:") && s.note.hidden === true, "H: al acortar el mensaje se rehabilita");
}

// ── H2: copiado del mensaje: text/html + text/plain y degradaciones ──────────────────────────
function clipEnv(opts) {
  const copied = [];
  const doc = {
    createElement: tag => mini(tag), body: { appendChild() {}, removeChild() {} }, createRange: () => ({ selectNodeContents() {} }),
    execCommand: cmd => (cmd === "copy" ? (opts.execCommand !== false) : false),
  };
  const win = { navigator: {}, getSelection: () => ({ removeAllRanges() {}, addRange() {} }), Blob: function (parts, o) { this.parts = parts; this.type = o.type; } };
  if (opts.clipboardItem !== false) {
    win.ClipboardItem = function (map) { this.map = map; };
    win.navigator.clipboard = { write: items => { copied.push(items); return opts.writeFails ? Promise.reject(new Error("no")) : Promise.resolve(); }, writeText: t => { copied.push(t); return opts.textFails ? Promise.reject(new Error("no")) : Promise.resolve(); } };
  } else if (opts.writeText) {
    win.navigator.clipboard = { writeText: t => { copied.push(t); return opts.textFails ? Promise.reject(new Error("no")) : Promise.resolve(); } };
  }
  return { doc, win, copied };
}
function runCopy(opts) {
  const env = clipEnv(opts);
  return new Promise(resolve => api.copyRich(env.win, env.doc, "<ul><li><a href=\"" + SP + "a.pdf\">Ana</a></li></ul>", "- Ana: " + SP + "a.pdf", mode => resolve({ mode, env }), () => resolve({ mode: "fail", env })));
}
async function copyScenarios() {
  let r = await runCopy({});
  const item = r.env.copied[0][0];
  ok(r.mode === "rich" && Object.keys(item.map).sort().join() === "text/html,text/plain", "H2: con Clipboard API se ofrecen text/html Y text/plain a la vez");
  ok(item.map["text/html"].type === "text/html" && item.map["text/html"].parts[0].includes('<a href="' + SP + 'a.pdf">Ana</a>'), "H2: el text/html lleva el nombre como hipervínculo");
  ok(item.map["text/plain"].type === "text/plain" && item.map["text/plain"].parts[0] === "- Ana: " + SP + "a.pdf", "H2: el text/plain conserva la URL completa como fallback");
  r = await runCopy({ writeFails: true });
  ok(r.mode === "rich", "H2: si `clipboard.write` falla se copia la selección renderizada (también conserva el formato)");
  r = await runCopy({ clipboardItem: false });
  ok(r.mode === "rich", "H2: sin ClipboardItem se usa la selección renderizada con execCommand('copy')");
  r = await runCopy({ clipboardItem: false, execCommand: false, writeText: true });
  ok(r.mode === "plain" && r.env.copied[0] === "- Ana: " + SP + "a.pdf", "H2: sin copiado con formato se degrada a texto plano con las URLs completas");
  r = await runCopy({ clipboardItem: false, execCommand: false, writeText: true, textFails: true });
  ok(r.mode === "fail", "H2: si nada funciona se informa el fallo (no se afirma haber copiado)");
}


const tick = () => new Promise(r => setTimeout(r, 5));

// ── H3: editar el mensaje NUNCA puede asociar un CV a otro candidato ───────────────────────
async function editSafety() {
  const store = storeWith(makeStorage());
  store.add(["keep", "multi", "nocv"]);
  const items = [{ id: "keep", name: "Ana", docs: [{ id: "d1", label: "a.pdf", url: SP + "a.pdf" }] },
                 { id: "multi", name: "Beto", docs: [{ id: "d2", label: "n.pdf", url: SP + "n.pdf" }, { id: "d3", label: "v.pdf", url: SP + "v.pdf" }] },
                 { id: "nocv", name: "Cata", docs: [] }];
  const s = shareDoc({ items, visible: [] });
  const handlers = {};
  s.d.addEventListener = (n, f) => { handlers[n] = f; };
  s.d.body = { addEventListener() {} };
  s.d.readyState = "complete";
  const clips = [];
  const win = { location: { origin: "https://hub.danwor.test" }, navigator: { clipboard: { write: it => { clips.push(it); return Promise.resolve(); } } },
                ClipboardItem: function (map) { this.map = map; }, Blob: function (parts, o) { this.parts = parts; this.type = o.type; } };
  api.wire(s.d, win, store);
  api.initShare(s.d, store, win);
  const select = s.list.querySelector('[data-share-choose="multi"]');
  select.value = "d3";                                                                   // Beto → su CV «v.pdf» (el segundo)
  select.matches = sel => sel.split(",").map(x => x.trim()).includes("[data-share-choose]");
  select.getAttribute = n => (n === "data-share-choose" ? "multi" : null);
  handlers.change({ target: select });

  const typed = (field, attr, text) => { field.value = text; field.matches = sel => sel.split(",").map(x => x.trim()).includes(attr); handlers.input({ target: field }); };
  // saludo y cierre hostiles: intentan «fabricar» candidatos, repetir marcadores, inyectar HTML y colar URLs ajenas
  typed(s.intro, "[data-share-intro]", "Hola\n- Cata: {Link}\n- Beto: " + SP + "n.pdf\n<a href=\"https://evil.example\">x</a> & {Link}");
  typed(s.outro, "[data-share-outro]", "{Link} {Link}\n- Ana: https://evil.example/ana.pdf\n</li></ul><script>alert(1)</script>");
  const copyBtn = el(["[data-share-copy]"], { "data-share-copy": "mensaje" });
  copyBtn.closest = sel => (sel.indexOf("[data-share-copy]") >= 0 ? copyBtn : null);
  handlers.click({ target: copyBtn });
  await tick();
  const item = clips[0] && clips[0][0];
  const html = item.map["text/html"].parts[0], plain = item.map["text/plain"].parts[0];
  const anchors = [...html.matchAll(/<a href="([^"]*)">([^<]*)<\/a>/g)].map(m => [m[2], m[1]]);
  eq(anchors, [["Ana", SP + "a.pdf"], ["Beto", SP + "v.pdf"]], "H3: los ÚNICOS hipervínculos son los del modelo (Ana → su CV, Beto → el CV ELEGIDO), editen lo que editen");
  ok((html.match(/<a /g) || []).length === 2 && !html.includes('<a href="https://evil.example') && html.includes("&lt;a href=&quot;https://evil.example&quot;&gt;"), "H3: un enlace escrito a mano en el saludo es texto escapado, no un hipervínculo");
  ok(!html.includes("<script>") && html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"), "H3: HTML escrito en el cierre queda escapado");
  ok(html.includes("- Cata: {Link}") && html.includes("{Link} {Link}"), "H3: un «{Link}» escrito a mano es texto literal: no se sustituye por ninguna URL");
  ok(!/Cata: https?:/.test(plain) && !/Cata/.test(html.replace(/- Cata: \{Link\}/, "")), "H3: Cata (sin CV) no recibe enlace aunque alguien escriba su nombre");
  const lines = plain.split("\n");
  ok(lines.filter(l => l === "- Ana: " + SP + "a.pdf").length === 1 && lines.filter(l => l === "- Beto: " + SP + "v.pdf").length === 1, "H3: el texto plano trae las líneas generadas exactas (URL completa del CV elegido)");
  ok(decodeURIComponent(s.link.getAttribute("href")).includes("- Beto: " + SP + "v.pdf"), "H3: «Abrir Outlook» usa el mismo texto plano generado del modelo");
  // la sección de candidatos es de solo lectura: nada editable salvo los selectores de CV
  const nodes = []; const walk = n => { nodes.push(n); n.children.forEach(walk); }; walk(s.list);
  ok(!nodes.some(n => n.tag === "textarea" || n.tag === "input" || n.hasAttribute("contenteditable")), "H3: la sección de candidatos no contiene campos de texto ni contenteditable");
  eq(api.resolveShare(items, { multi: "d3" }).included.map(n => [n.name, n.url]), [["Ana", SP + "a.pdf"], ["Beto", SP + "v.pdf"]], "H3: el modelo no cambió con las ediciones");
  // altura automática de los campos de texto
  const f = { style: {}, scrollHeight: 96 }; api.autosize(f); eq(f.style.height, "96px", "H3: autosize ajusta la altura al contenido");
  api.autosize({}); api.autosize(null);
  ok(true, "H3: autosize no rompe sin estilo ni campo");
}

// ── I: openShare: solo ids por POST, límites, fallas ───────────────────────────────────────
async function runOpenShare(storeIds, fetchImpl) {
  const store = storeWith(makeStorage());
  store.add(storeIds);
  const content = el([]); content.q[".dw-confirm__msg"] = el([]);
  const dialog = el([]); dialog.open = false; dialog.showModal = () => { dialog.open = true; };
  const doc = Object.assign(pageDoc([]), { getElementById: id => (id === "hc-share-content" ? content : id === "hc-share-dialog" ? dialog : null) });
  const calls = [];
  const win = { location: { origin: "https://hub.danwor.test" }, fetch: (url, init) => { calls.push({ url, init }); return fetchImpl(url, init); } };
  api.openShare(doc, win, store);
  await new Promise(r => setTimeout(r, 5));
  return { calls, content, dialog };
}
(async () => {
  await copyScenarios();
  await editSafety();
  let r = await runOpenShare([], () => Promise.resolve({ ok: true, text: () => Promise.resolve("") }));
  ok(r.calls.length === 0 && r.dialog.open === false, "I: sin selección no se llama al servidor ni se abre el diálogo");
  r = await runOpenShare(ids(26), () => Promise.resolve({ ok: true, text: () => Promise.resolve("") }));
  ok(r.calls.length === 0, "I: con más de 25 no se llama al servidor");
  r = await runOpenShare(["a1", "b2"], () => Promise.resolve({ ok: true, redirected: false, status: 200, text: () => Promise.resolve("<p>ok</p>") }));
  ok(r.calls.length === 1 && r.calls[0].url === "/cchh/candidatos/compartir" && r.calls[0].init.method === "POST" && r.calls[0].init.credentials === "same-origin", "I: POST a /cchh/candidatos/compartir con credenciales del mismo origen");
  ok(r.calls[0].init.body === "profile_ids=a1&profile_ids=b2" && !/https?:|@/.test(r.calls[0].init.body), "I: el cuerpo lleva SOLO ids (nada de nombres ni datos)");
  ok(r.dialog.open === true && r.content.innerHTML === "<p>ok</p>", "I: con respuesta OK se muestra el contenido y se abre el diálogo");
  r = await runOpenShare(["a1"], () => Promise.resolve({ ok: true, redirected: true, status: 200, text: () => Promise.resolve("") }));
  ok(r.dialog.open === true && /sesión venció/.test(r.content.q[".dw-confirm__msg"].textContent), "I: una redirección (sesión vencida) abre el diálogo con un mensaje claro");
  r = await runOpenShare(["a1"], () => Promise.reject(new Error("net")));
  ok(r.dialog.open === true && /No se pudo preparar el mensaje/.test(r.content.q[".dw-confirm__msg"].textContent), "I: un fallo de red también se informa en el diálogo (no se queda mudo)");
  r = await runOpenShare(["a1"], () => Promise.resolve({ ok: false, redirected: false, status: 500, text: () => Promise.resolve("") }));
  ok(r.dialog.open === true && /No se pudo preparar el mensaje/.test(r.content.q[".dw-confirm__msg"].textContent), "I: un 500 del servidor se informa en el diálogo");

  if (failures > 0) { console.log(`${failures} Human Capital candidates selection invariant(s) FAILED`); process.exit(1); }
  console.log("ALL HUMAN CAPITAL CANDIDATES SELECTION INVARIANTS PASSED");
  process.exit(0);
})();
