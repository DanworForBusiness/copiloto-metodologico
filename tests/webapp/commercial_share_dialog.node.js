// commercial_share_dialog.node.js — invocado por test_commercial_share.py::test_browser_module_selection_copy_and_send.
//
// Prueba el módulo REAL `commercial_share.js` (Gestión Comercial → Compartir) sobre las primitivas reales `table_selection.js` y
// `share_recipients.js`: selección múltiple del catálogo (individual, todas las visibles, parcial, limpiar, coherencia con el filtro), un ÚNICO
// diálogo para Estrategia / presentación / selección, «Copiar enlace» (misma URL de siempre; varias = bloques legibles), envío con «Enviando…»,
// doble clic = una sola request, 202 → cierra + aviso, incierto → bloquea el reenvío, error → conserva el contenido.
// DOM mínimo propio (no hay jsdom en el repo); `fetch`, portapapeles y token simulados: nada sale de este proceso.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, b, label) { ok(JSON.stringify(a) === JSON.stringify(b), `${label} (esperado ${JSON.stringify(b)}, real ${JSON.stringify(a)})`); }

// ── DOM mínimo: atributos, hijos, `[attr]` / `[attr="v"]`, closest("tr"), getElementById ──
const byId = {};
function el(tag, attrs, children) {
  const n = {
    tag, attrs: Object.assign({}, attrs || {}), children: [], parent: null, value: "", checked: false, indeterminate: false, disabled: false, hidden: false, _text: "",
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); } },
    getAttribute(k) { return k in n.attrs ? n.attrs[k] : null; },
    setAttribute(k, v) { n.attrs[k] = String(v); },
    removeAttribute(k) { delete n.attrs[k]; },
    hasAttribute(k) { return k in n.attrs; },
    appendChild(c) { n.children.push(c); c.parent = n; return c; },
    replaceChildren(...cs) { n.children = []; cs.forEach(c => n.appendChild(c)); },
    focus() {}, matches() { return false; },
    closest(sel) { for (let x = n; x; x = x.parent) { if (x.tag === sel) return x; } return null; },
    querySelectorAll(sel) {
      const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel); const out = [];
      const walk = x => { for (const c of x.children) { if (m && (m[2] === undefined ? c.hasAttribute(m[1]) : c.getAttribute(m[1]) === m[2])) out.push(c); walk(c); } };
      walk(n); return out;
    },
    querySelector(sel) { return n.querySelectorAll(sel)[0] || null; },
  };
  Object.defineProperty(n, "textContent", { get: () => n._text, set: v => { n._text = String(v); n.children = []; } });
  if (n.attrs.id) byId[n.attrs.id] = n;
  (children || []).forEach(c => n.appendChild(c));
  return n;
}

const ROWS = [["p-uno", "Propuesta Uno", true], ["p-dos", "Propuesta Dos", true], ["p-tres", "Propuesta Tres", true], ["p-rota", "Rota", false]];
const head = el("input", { "data-cp-select-page": "" });
const rows = ROWS.map(([id, title, available]) => {
  const box = el("input", { "data-cp-select": id, "data-cshare-title": title, "data-cshare-path": "/presentaciones/" + id });
  box.disabled = !available;
  return el("tr", {}, [el("td", {}, [box])]);
});
const table = el("table", { id: "cp-catalog-table" }, [el("thead", {}, [el("tr", {}, [el("th", {}, [head])])]), el("tbody", {}, rows)]);
const shareSel = el("button", { "data-cshare-open": "selection" });
const bar = el("div", { "data-cp-selection-bar": "" }, [el("strong", { "data-cp-selection-count": "" }), el("span", { "data-cp-selection-label": "" }),
                                                       el("span", { "data-cp-selection-hint": "" }), shareSel]);
bar.hidden = true;
const toastHost = el("div", { "data-cp-status": "" });
const F = {};
["data-cshare-title", "data-cshare-items", "data-share-chips", "data-share-search", "data-share-results", "data-share-results-list", "data-share-results-hint",
 "data-share-dir-blocked", "data-cshare-subject", "data-cshare-message", "data-cshare-status", "data-cshare-copy", "data-cshare-send"].forEach(a => { F[a] = el("x", { [a]: "" }); });
const dlg = el("dialog", { id: "cshare-dialog", "data-catalog-url": "/comercial/compartir/catalogo", "data-directory-url": "/comercial/compartir/destinatarios",
                           "data-send-url": "/comercial/compartir/enviar", "data-directory-enabled": "1", "data-recipient-provider": "allowlist",
                           "data-send-enabled": "1", "data-max-recipients": "20" }, Object.values(F));
dlg.open = false; dlg.showModal = function () { this.open = true; }; dlg.close = function () { this.open = false; };
const body = el("body", {}, [table, bar, toastHost, dlg]);

const requests = [];
let respond = () => ({ status: 200, json: { status: "accepted", recipients: ["com.uno@danwor.com"] } });
let copied = [];
let copyOk = true;
const storage = { data: {}, getItem(k) { return this.data[k] || null; }, setItem(k, v) { this.data[k] = v; } };
const doc = { readyState: "complete", addEventListener() {}, createElement: t => el(t), getElementById: id => byId[id] || null,
              querySelector: s => body.querySelector(s), querySelectorAll: s => body.querySelectorAll(s), defaultView: null };
const win = {
  location: { origin: "http://localhost:8082" }, sessionStorage: storage, setTimeout: f => f(), clearTimeout() {},
  copyTextToClipboard: text => { copied.push(text); return Promise.resolve(copyOk); },
  dwGraphToken: () => Promise.resolve(""),
  fetch: (url, opts) => {
    requests.push({ url, body: opts.body });
    if (url.endsWith("/catalogo")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ recipients: [{ name: "Com Uno", email: "com.uno@danwor.com" }] }) });
    const r = respond();
    if (r.reject) return Promise.reject(new Error("net"));
    return Promise.resolve({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.json) });
  },
};
const STATIC = path.resolve(__dirname, "..", "..", "webapp", "static");
const SRC = ["table_selection.js", "share_recipients.js", "commercial_share.js"].map(f => fs.readFileSync(path.join(STATIC, f), "utf-8")).join("\n");
const sandbox = { window: win, document: doc, console, Promise, JSON, Date, Math, parseInt, isNaN, encodeURIComponent, Object, Set };
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "commercial_share.js" });
const api = win.dwCommercialShare;
const tick = () => new Promise(r => setImmediate(r));
const box = id => table.querySelector(`[data-cp-select="${id}"]`);

(async () => {
  // ── selección ──
  eq(bar.hidden, true, "sin selección la barra está oculta");
  box("p-uno").checked = true; api.toggle(box("p-uno"));
  eq([bar.hidden, bar.querySelector("[data-cp-selection-count]").textContent], [false, "1"], "una fila: barra visible con contador 1");
  eq(bar.querySelector("[data-cp-selection-label]").textContent, "presentación seleccionada", "rótulo en singular");
  eq([head.checked, head.indeterminate], [false, true], "encabezado parcial con una de tres");
  eq(shareSel.disabled, false, "«Compartir presentaciones» habilitado con ≥1");
  head.checked = true; api.toggle(head);
  eq([head.checked, head.indeterminate, bar.querySelector("[data-cp-selection-count]").textContent], [true, false, "3"], "encabezado: todas las visibles habilitadas (la no disponible no)");
  eq(box("p-rota").checked, false, "una presentación no disponible nunca se selecciona");
  rows[2].hidden = true; api.syncSelection();                                                      // el filtro oculta «Propuesta Tres»
  eq(bar.querySelector("[data-cp-selection-hint]").textContent, "1 fuera de esta búsqueda", "la selección filtrada se conserva y se informa");
  eq([head.checked, head.indeterminate], [true, false], "encabezado lleno respecto de lo VISIBLE");
  head.checked = false; api.toggle(head);
  eq(api.selectedItems().map(i => i.id), ["p-tres"], "desmarcar el encabezado solo quita las visibles");
  rows[2].hidden = false; api.syncSelection();
  box("p-dos").checked = true; api.toggle(box("p-dos"));
  eq(api.selectedItems(), [{ id: "p-tres", title: "Propuesta Tres", path: "/presentaciones/p-tres" }, { id: "p-dos", title: "Propuesta Dos", path: "/presentaciones/p-dos" }],
     "ítems seleccionados con título y ruta de las filas del servidor");
  ok(JSON.parse(storage.data["comercial-presentaciones-selection"]).ids.length === 2, "selección persistida (solo ids) en sessionStorage");
  api.clearSelection();
  eq([bar.hidden, head.checked, head.indeterminate, box("p-dos").checked], [true, false, false, false], "«Limpiar» vacía la selección y el encabezado");

  // ── Copiar enlace: un único enlace = la MISMA URL de siempre; varios = bloques legibles ──
  api.open("strategy", [{ id: "estrategia", title: "Estrategia comercial", path: "/comercial/estrategia" }]);
  eq([dlg.open, F["data-cshare-title"].textContent, F["data-cshare-subject"].value], [true, "Compartir Estrategia comercial", "Estrategia comercial"], "Estrategia abre el diálogo único");
  await api.copyLinks(); await tick(); await tick();
  eq(copied.pop(), "http://localhost:8082/comercial/estrategia", "Estrategia copia exactamente la URL actual");
  eq([dlg.open, toastHost.children.length], [false, 1], "copiar cierra y avisa con el toast de la página");
  api.open("presentations", [{ id: "p-uno", title: "Propuesta Uno", path: "/presentaciones/p-uno" }, { id: "p-dos", title: "Propuesta Dos", path: "/presentaciones/p-dos" }]);
  eq(F["data-cshare-copy"].textContent, "Copiar enlaces", "con varias, el botón dice «Copiar enlaces»");
  api.copyLinks(); await tick(); await tick();
  eq(copied.pop(), "Propuesta Uno\nhttp://localhost:8082/presentaciones/p-uno\n\nPropuesta Dos\nhttp://localhost:8082/presentaciones/p-dos", "varias: título + URL por bloque");

  // ── Enviar ──
  api.open("presentations", [{ id: "p-uno", title: "Propuesta Uno", path: "/presentaciones/p-uno" }]);
  await tick(); await tick();                                                                     // catálogo allowlist: UNA lectura por apertura
  eq(requests.filter(r => r.url.endsWith("/catalogo")).length, 3, "una lectura de catálogo por apertura");
  eq(F["data-cshare-send"].disabled, true, "sin destinatarios «Enviar» está deshabilitado");
  api.addRecipient({ name: "Com Uno", email: "com.uno@danwor.com" });
  api.syncSelection();
  ok(F["data-cshare-message"].value.startsWith("Hola Com,"), "saludo personalizado con un destinatario: " + F["data-cshare-message"].value);
  let release;
  respond = () => ({ status: 200, json: { status: "accepted", recipients: ["com.uno@danwor.com"] } });
  const before = requests.length;
  const origFetch = win.fetch;
  win.fetch = (url, opts) => new Promise(r => { release = () => r(origFetch(url, opts)); });
  eq(F["data-cshare-send"].disabled, false, "con un destinatario «Enviar» se habilita");
  api.send(); api.send();                                                                          // doble clic
  await tick();
  eq(F["data-cshare-send"].textContent, "Enviando…", "estado «Enviando…» durante el envío");
  eq(F["data-cshare-send"].disabled, true, "el botón queda deshabilitado mientras envía");
  release(); await tick(); await tick(); await tick();
  win.fetch = origFetch;
  const sends = requests.slice(before).filter(r => r.url.endsWith("/enviar"));
  eq(sends.length, 1, "doble clic = una sola request");
  const sent = new URLSearchParams(sends[0].body);
  eq([sent.get("kind"), sent.getAll("presentation_ids"), sent.getAll("recipients")], ["presentations", ["p-uno"], ["com.uno@danwor.com"]], "el envío manda kind + ids, nunca URLs");
  ok(!sends[0].body.includes("http"), "ninguna URL viaja desde el navegador");
  ok((sent.get("operation_id") || "").length >= 8, "operation_id por apertura");
  eq(dlg.open, false, "202 → cierra el diálogo");
  eq(toastHost.children[0].children[0].children[1].textContent, "Correo aceptado para envío", "202 → toast");

  // incierto: bloquea el reenvío y conserva el contenido
  api.open("strategy", [{ id: "estrategia", title: "Estrategia comercial", path: "/comercial/estrategia" }]);
  api.addRecipient({ name: "Com Uno", email: "com.uno@danwor.com" });
  F["data-cshare-message"].value = "Texto propio";
  respond = () => ({ status: 200, json: { status: "uncertain" } });
  api.send(); await tick(); await tick(); await tick();
  eq([dlg.open, F["data-cshare-message"].value, F["data-cshare-send"].disabled], [true, "Texto propio", true], "incierto: diálogo abierto, contenido intacto, reenvío bloqueado");
  ok(/sin confirmación/.test(F["data-cshare-status"].textContent), "incierto: aviso claro");
  const n = requests.length; api.send(); await tick();
  eq(requests.length, n, "incierto: un nuevo clic no reenvía");

  // error de validación: se puede corregir y reintentar
  api.open("strategy", [{ id: "estrategia", title: "Estrategia comercial", path: "/comercial/estrategia" }]);
  api.addRecipient({ name: "Ext", email: "ext@danwor.com" });
  respond = () => ({ status: 422, json: { status: "error", reason: "no_recipients", message: "No hay destinatarios corporativos válidos seleccionados." } });
  api.send(); await tick(); await tick(); await tick();
  eq([dlg.open, F["data-cshare-send"].disabled, F["data-cshare-status"].textContent], [true, false, "No hay destinatarios corporativos válidos seleccionados."], "error: diálogo abierto y reintentable");

  // red caída: incierto (no se reenvía a ciegas)
  respond = () => ({ reject: true });
  api.send(); await tick(); await tick(); await tick();
  eq(F["data-cshare-send"].disabled, true, "sin respuesta: se bloquea el reenvío");

  if (failures) { console.log(`${failures} fallas`); process.exit(1); }
  console.log("OK");
})().catch(e => { console.log("ERROR " + (e && e.stack)); process.exit(1); });
