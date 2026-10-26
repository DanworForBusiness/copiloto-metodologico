// human_capital_candidates_selection.node.js — invocado por test_human_capital_candidates_selection.py.
//
// Prueba el módulo `webapp/static/human_capital_candidates_selection.js` (CCHH · Candidatos: selección múltiple + «Compartir perfiles») contra un DOM mínimo propio (no hay jsdom en el
// repo): almacén de ids en sessionStorage (solo ids, sin datos personales), casilla de encabezado SOLO sobre la página visible (tri-estado), persistencia entre «páginas» (re-render del
// panel), contador y «Limpiar», límite de 25, armado del mensaje, límite práctico del `mailto:` y flujo del diálogo.
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
  ["[data-hc-selection-count]", "[data-hc-selection-label]", "[data-hc-selection-hint]", "[data-hc-share-open]", "[data-hc-clear-selection]"].forEach(s => { bar.q[s] = el([s]); });
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
  store.clear();
  store.add(ids(25));
  api.syncUI(d, store);
  ok(d.bar.q["[data-hc-share-open]"].disabled === false, "E: 25 es el máximo permitido");
  store.add("id-extra");
  api.syncUI(d, store);
  ok(d.bar.q["[data-hc-share-open]"].disabled === true, "E: con 26 «Compartir» se deshabilita");
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

// ── G: mensaje y mailto ────────────────────────────────────────────────────────────────────
{
  const items = [{ name: "Ana Pérez", path: "/cchh/candidatos/abc-1" }, { name: "Luis Gómez", path: "/cchh/candidatos/def-2" }];
  const msg = api.buildMessage(items, "https://hub.danwor.test", "Hola:", "Saludos.");
  eq(msg, "Hola:\n\n- Ana Pérez: https://hub.danwor.test/cchh/candidatos/abc-1\n- Luis Gómez: https://hub.danwor.test/cchh/candidatos/def-2\n\nSaludos.", "G: formato exacto del mensaje (nombre + enlace de la ficha)");
  ok(!/editar|sharepoint|@|\+\d/.test(msg), "G: ni /editar ni SharePoint ni emails/teléfonos en el mensaje");
  const url = api.mailtoUrl("Asunto & más", "linea 1\nlinea 2 100% #ok https://x/y");
  ok(url.startsWith("mailto:?subject=Asunto%20%26%20m%C3%A1s&body="), "G: el asunto se codifica");
  ok(url.includes("linea%201%0D%0Alinea%202%20100%25%20%23ok%20https://x/y"), "G: saltos de línea CRLF, %, # y & codificados; ':' y '/' sin inflar");
  const fits = i => api.mailtoFits("Perfiles", api.buildMessage(Array.from({ length: i }, (_, k) => ({ name: `Candidato Numero ${k}`, path: "/cchh/candidatos/3f2b8c1e-9d4a-4c1e-8b7a-0123456789ab" })), "https://hub.danwor.com", "Intro", "Fin"));
  ok(fits(5) === true && fits(25) === false, "G: pocos perfiles entran en el límite práctico del mailto; 25 no");
  ok(api.mailtoUrl("a", "b").length < api.MAILTO_MAX, "G: el límite práctico está declarado (≈1900)");
}

// ── H: diálogo: Outlook se deshabilita por longitud y se rehabilita; inicialización con ids no disponibles ──
function shareDoc(opts) {
  const root = el(["[data-share-root]"], { "data-default-subject": "Perfiles", "data-intro": "Hola", "data-outro": "Chau" });
  const subject = el(["[data-share-subject]"]), message = el(["[data-share-message]"]);
  const link = el(["[data-share-open]"], { href: "#" });
  const note = el(["[data-share-note]"]), stat = el(["[data-share-status]"]);
  const itemsEl = el([]); itemsEl.textContent = JSON.stringify(opts.items);
  const unavEl = el([]); unavEl.textContent = JSON.stringify(opts.unavailable || []);
  // Estructura REAL de la plantilla: subject/message/note/status viven dentro de `data-share-root`; el enlace «Abrir Outlook» y los JSON de datos quedan FUERA (en las acciones / al final).
  root.q = { "[data-share-subject]": subject, "[data-share-message]": message, "[data-share-note]": note, "[data-share-status]": stat };
  const content = el([]); content.q = { "[data-share-root]": root, "[data-share-subject]": subject, "[data-share-message]": message, "[data-share-note]": note, "[data-share-status]": stat, "[data-share-open]": link, "[data-share-items]": itemsEl, "[data-share-unavailable]": unavEl };
  const d = Object.assign(pageDoc(opts.visible || []), { getElementById: id => (id === "hc-share-content" ? content : null) });
  return { d, subject, message, link, note, stat };
}
{
  const store = storeWith(makeStorage());
  store.add(["u1", "u2", "keep"]);
  const s = shareDoc({ items: [{ name: "Ana", path: "/cchh/candidatos/keep" }], unavailable: ["u1", "u2"], visible: ["keep"] });
  api.initShare(s.d, store, { location: { origin: "https://hub.danwor.test" } });
  eq(store.ids(), ["keep"], "H: los ids que el servidor marcó como no disponibles se sacan de la selección (el contador no miente)");
  ok(s.subject.value === "Perfiles" && s.message.value === "Hola\n\n- Ana: https://hub.danwor.test/cchh/candidatos/keep\n\nChau", "H: asunto y mensaje prearmados con el origen del propio navegador");
  ok(s.link.getAttribute("href").startsWith("mailto:?subject=Perfiles&body=") && s.note.hidden === true, "H: con mensaje corto «Abrir Outlook» apunta a un mailto");
  s.message.value = "x".repeat(2200);
  api.refreshMailto(s.d);
  ok(s.link.getAttribute("href") === "#" && s.link.getAttribute("aria-disabled") === "true" && s.link.classList.contains("is-disabled"), "H: si el mensaje es demasiado largo «Abrir Outlook» se deshabilita");
  ok(s.note.hidden === false && /Copiar mensaje/.test(s.note.textContent), "H: con una nota que manda a «Copiar mensaje»");
  s.message.value = "corto";
  api.refreshMailto(s.d);
  ok(s.link.getAttribute("aria-disabled") === null && s.link.getAttribute("href").startsWith("mailto:") && s.note.hidden === true, "H: al acortar el mensaje se rehabilita");
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
