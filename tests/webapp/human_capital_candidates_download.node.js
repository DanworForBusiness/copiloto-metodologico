// human_capital_candidates_download.node.js — invocado por test_human_capital_candidates_download.py.
//
// Prueba el módulo `webapp/static/human_capital_candidates_download.js` (CCHH · Candidatos · «Descargar CVs») contra un DOM mínimo propio (no hay jsdom en el repo): resumen con SOLO ids, token delegado
// en el CUERPO (nunca en la URL, el DOM ni la consola), entrega del ZIP como descarga, segunda confirmación del parcial (allow_partial + ids reconocidos), cancelación, sesión vencida, errores y
// que los mensajes del navegador solo usen textContent.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, b, label) { ok(JSON.stringify(a) === JSON.stringify(b), `${label} (esperado ${JSON.stringify(b)}, real ${JSON.stringify(a)})`); }

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "human_capital_candidates_download.js"), "utf-8");
const sandbox = { window: {}, console: { log() {}, error() {}, warn() {} } };
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "human_capital_candidates_download.js" });
const api = sandbox.window.__cchhDownload;
const TOKEN = "tok-ENSAYO-no-real-0a1b2c3d4e5f";
const DIGEST = "a".repeat(64);

// ── DOM mínimo ────────────────────────────────────────────────────────────────────────────
let innerHtmlWrites = 0;
function node(tag) {
  const n = {
    tag, attrs: {}, children: [], style: {}, parent: null, textContent: "", _html: "", clicked: 0,
    setAttribute(k, v) { n.attrs[k] = String(v); }, getAttribute(k) { return k in n.attrs ? n.attrs[k] : null; }, hasAttribute(k) { return k in n.attrs; },
    appendChild(c) { n.children.push(c); c.parent = n; return c; }, removeChild(c) { n.children.splice(n.children.indexOf(c), 1); return c; },
    get firstChild() { return n.children[0] || null; },
    click() { n.clicked++; },
    closest(sel) { let x = n; while (x) { if (x.matches && x.matches(sel)) return x; x = x.parent; } return null; },
    matches(sel) { return sel.split(",").map(s => s.trim()).some(s => { const m = /^\[([\w-]+)\]$/.exec(s); return m ? x_has(n, m[1]) : false; }); },
    querySelector(sel) { return find(n, sel); },
  };
  return n;
}
function x_has(n, attr) { return attr in n.attrs; }
function find(root, sel) {
  const m = /^\[([\w-]+)\]$/.exec(sel);
  if (!m) return null;
  if (root._rootAttrs && m[1] === "data-dl-root") return root._rootAttrs;                      // contenido HTML del servidor simulado
  for (const c of root.children) { if (c.hasAttribute(m[1])) return c; const r = find(c, sel); if (r) return r; }
  return null;
}
function contentNode() {
  const c = node("div");
  Object.defineProperty(c, "innerHTML", { get() { return c._html; }, set(v) {
    innerHtmlWrites++;
    c._html = v; c.children = [];
    const d = /data-dl-root[^>]*data-plan-digest="([0-9a-f]+)"/.exec(v);
    c._rootAttrs = d ? Object.assign(node("div"), { attrs: { "data-dl-root": "", "data-plan-digest": d[1] } }) : null;
  } });
  return c;
}
function textOf(n) { return [n.textContent].concat(n.children.map(textOf)).join(" "); }
function allText(content) { return textOf(content) + " " + content._html; }

function env(opts) {
  opts = opts || {};
  const content = contentNode();
  const dialog = Object.assign(node("dialog"), { open: false, showModal() { dialog.open = true; }, listeners: {}, addEventListener(t, f) { dialog.listeners[t] = f; } });
  const body = node("body");
  const created = [];
  const doc = {
    body, handlers: {},
    getElementById: id => (id === "hc-download-dialog" ? dialog : id === "hc-download-content" ? content : null),
    createElement: tag => { const n = node(tag); created.push(n); return n; },
    addEventListener(t, f) { doc.handlers[t] = f; },
  };
  const calls = [];
  const blobs = [];
  const win = {
    __cchhSelection: { store: { ids: () => (opts.ids || ["id-a", "id-b"]) } },
    dwGraphToken: "token" in opts ? opts.token : () => Promise.resolve(TOKEN),
    AbortController, setTimeout: (f) => { win.timers.push(f); return 1; }, timers: [],
    URL: { createObjectURL: b => { blobs.push(b); return "blob:fake"; }, revokeObjectURL: u => { win.revoked = u; } },
    fetch: (url, init) => { calls.push({ url, init }); return opts.fetch ? opts.fetch(url, init) : Promise.resolve(zipResponse()); },
  };
  return { doc, win, content, dialog, calls, blobs, created };
}
function headers(map) { return { get: k => map[k] || map[k.toLowerCase()] || null }; }
function zipResponse(extra) {
  return Object.assign({ ok: true, status: 200, redirected: false, headers: headers({ "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="cvs-candidatos-20261026-1530.zip"', "X-Export-Files": "2", "X-Export-Status": "full" }),
                        blob: () => Promise.resolve({ size: 150000 }) }, extra || {});
}
function htmlResponse(html, status) { return { ok: (status || 200) < 400, status: status || 200, redirected: false, headers: headers({ "Content-Type": "text/html; charset=utf-8" }), text: () => Promise.resolve(html) }; }
const tick = () => new Promise(r => setTimeout(r, 5));
function summaryHtml() { return `<div data-dl-root data-plan-digest="${DIGEST}" data-has-omitted="0"></div>`; }
function bodyOf(call) { return new URLSearchParams(call.init.body); }

(async () => {
  // ── A: resumen ─────────────────────────────────────────────────────────────────────────
  let e = env({ fetch: () => Promise.resolve(htmlResponse(summaryHtml())) });
  api.openSummary(e.doc, e.win);
  await tick();
  ok(e.calls.length === 1 && e.calls[0].url === api.SUMMARY_URL && e.calls[0].init.method === "POST" && e.calls[0].init.credentials === "same-origin", "A: POST al resumen, mismo origen");
  ok(e.calls[0].init.body === "profile_ids=id-a&profile_ids=id-b", "A: el resumen lleva SOLO ids (sin token ni datos)");
  ok(e.dialog.open === true && e.content._html.includes(DIGEST), "A: se muestra el resumen del servidor y se abre el diálogo");
  e = env({ ids: [] }); api.openSummary(e.doc, e.win); await tick();
  ok(e.calls.length === 0 && e.dialog.open === false, "A: sin selección no se llama al servidor");
  e = env({ ids: Array.from({ length: 26 }, (_, i) => "id-" + i) }); api.openSummary(e.doc, e.win); await tick();
  ok(e.calls.length === 0, "A: con más de 25 no se llama al servidor");
  e = env({ fetch: () => Promise.resolve({ ok: true, status: 200, redirected: true, headers: headers({}), text: () => Promise.resolve("") }) }); api.openSummary(e.doc, e.win); await tick();
  ok(/sesión venció/.test(allText(e.content)), "A: una redirección (sesión vencida) se informa con claridad");
  e = env({ fetch: () => Promise.resolve({ ok: false, status: 500, redirected: false, headers: headers({}), text: () => Promise.resolve("") }) }); api.openSummary(e.doc, e.win); await tick();
  ok(/No se pudo preparar el resumen/.test(allText(e.content)) && e.dialog.open, "A: un 500 se informa en el diálogo");
  e = env({ fetch: () => Promise.reject(new Error("net")) }); api.openSummary(e.doc, e.win); await tick();
  ok(/No se pudo preparar el resumen/.test(allText(e.content)), "A: un fallo de red también se informa (no queda mudo)");

  // ── B: descarga exitosa ─────────────────────────────────────────────────────────────────
  e = env();
  e.content.innerHTML = summaryHtml();
  api.startExport(e.doc, e.win, { partial: false });
  ok(api.state.working === true && /Preparando la descarga/.test(allText(e.content)) && e.content.querySelector("[data-dl-cancel-run]"), "B: mientras se prepara se muestra el progreso con «Cancelar»");
  api.startExport(e.doc, e.win, { partial: false });                                         // doble clic: se ignora
  await tick();
  ok(e.calls.length === 1, "B: un segundo clic mientras se prepara NO dispara otra solicitud");
  const call = e.calls[0], form = bodyOf(call);
  ok(call.url === api.DOWNLOAD_URL && !call.url.includes(TOKEN) && !call.url.includes("?"), "B: el token NO viaja en la URL");
  ok(form.get("graph_token") === TOKEN && form.get("plan_digest") === DIGEST && form.get("ack_omitted") === "1" && form.getAll("profile_ids").join() === "id-a,id-b" && !form.has("allow_partial"), "B: el cuerpo lleva token, digest, confirmación e ids (y no pide parcial)");
  ok(call.init.signal && typeof call.init.signal.aborted === "boolean", "B: la solicitud es cancelable (AbortSignal)");
  eq(e.blobs.length, 1, "B: el ZIP se recibe como blob");
  const anchor = e.created.find(n => n.tag === "a");
  ok(anchor && anchor.getAttribute("download") === "cvs-candidatos-20261026-1530.zip" && anchor.getAttribute("href") === "blob:fake" && anchor.clicked === 1, "B: se dispara la descarga con el nombre del servidor");
  ok(/Descarga lista/.test(allText(e.content)) && /2 archivos/.test(allText(e.content)) && /0,1 MB/.test(allText(e.content)), "B: mensaje final con cantidad y tamaño");
  ok(api.state.working === false && api.state.controller === null, "B: el estado de trabajo se libera");
  ok(!allText(e.content).includes(TOKEN) && !JSON.stringify(e.created.map(n => n.attrs)).includes(TOKEN), "B: el token no aparece en el DOM");
  e.win.timers.forEach(f => f());
  ok(e.win.revoked === "blob:fake", "B: la URL del blob se revoca");

  // ── C: respuesta HTML (parcial / errores del servidor), con status de error incluido ────
  const partialHtml = `<div data-dl-root data-plan-digest="${DIGEST}"></div><button data-dl-partial data-ack-failed="d1 d2" data-parent-op="${"b".repeat(32)}">Descargar sin esos 2</button>`;
  e = env({ fetch: () => Promise.resolve(htmlResponse(partialHtml)) });
  e.content.innerHTML = summaryHtml();
  api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(e.content._html === partialHtml && e.blobs.length === 0 && !e.created.some(n => n.tag === "a"), "C: con fallos el servidor NO entrega ZIP y se muestra su HTML (segunda confirmación)");
  for (const status of [429, 503, 500]) {
    e = env({ fetch: () => Promise.resolve(htmlResponse("<p data-x>estado " + status + "</p>", status)) });
    e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
    ok(e.content._html.includes("estado " + status) && e.blobs.length === 0, "C: un " + status + " con HTML del servidor se muestra tal cual (busy/auditoría/error)");
  }

  // ── D: segunda confirmación del parcial ─────────────────────────────────────────────────
  e = env();
  e.content.innerHTML = summaryHtml();
  api.wire(e.doc, e.win);
  const partialBtn = Object.assign(node("button"), { attrs: { "data-dl-partial": "", "data-ack-failed": "d1 d2 ../mal x".padEnd(0) + " " + "z".repeat(70), "data-parent-op": "b".repeat(32) } });
  e.doc.handlers.click({ target: partialBtn });
  await tick();
  const f2 = bodyOf(e.calls[0]);
  ok(f2.get("allow_partial") === "1" && f2.getAll("ack_failed").join() === "d1,d2,x" && f2.get("parent_operation_id") === "b".repeat(32), "D: el parcial envía allow_partial, SOLO los ids válidos reconocidos y la operación anterior");
  e = env(); e.content.innerHTML = summaryHtml(); api.wire(e.doc, e.win);
  e.doc.handlers.click({ target: Object.assign(node("button"), { attrs: { "data-dl-partial": "", "data-ack-failed": "d1", "data-parent-op": "no-es-hex" } }) });
  await tick();
  ok(!bodyOf(e.calls[0]).has("parent_operation_id"), "D: una operación previa mal formada no se envía");

  // ── E: cancelación ──────────────────────────────────────────────────────────────────────
  e = env({ fetch: (u, init) => new Promise((res, rej) => { init.signal.addEventListener("abort", () => rej(Object.assign(new Error("abort"), { name: "AbortError" }))); }) });
  e.content.innerHTML = summaryHtml();
  api.wire(e.doc, e.win);
  api.startExport(e.doc, e.win, { partial: false });
  await tick();
  ok(api.state.working === true, "E: la solicitud está en vuelo");
  e.doc.handlers.click({ target: Object.assign(node("button"), { attrs: { "data-dl-cancel-run": "" } }) });
  await tick();
  ok(/Descarga cancelada/.test(allText(e.content)) && api.state.working === false && e.blobs.length === 0, "E: «Cancelar descarga» aborta la solicitud y no entrega nada");
  e = env({ fetch: (u, init) => new Promise((res, rej) => { init.signal.addEventListener("abort", () => rej(Object.assign(new Error("abort"), { name: "AbortError" }))); }) });
  e.content.innerHTML = summaryHtml(); api.wire(e.doc, e.win);
  api.startExport(e.doc, e.win, { partial: false }); await tick();
  e.dialog.listeners.close();
  await tick();
  ok(/Descarga cancelada/.test(allText(e.content)), "E: cerrar el diálogo (Esc) mientras se prepara también cancela");

  // ── F: token, sesión y red ──────────────────────────────────────────────────────────────
  for (const label of ["token vacío", "sin dwGraphToken"]) {
    e = env(label === "token vacío" ? { token: () => Promise.resolve("") } : { token: undefined });
    if (label === "sin dwGraphToken") delete e.win.dwGraphToken;
    e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
    ok(e.calls.length === 0 && /Falta el permiso de Microsoft/.test(allText(e.content)) && api.state.working === false, "F: " + label + ": no se llama al servidor y se explica");
  }
  e = env({ fetch: () => Promise.resolve({ ok: true, status: 200, redirected: true, headers: headers({}), text: () => Promise.resolve("") }) });
  e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(/sesión venció/.test(allText(e.content)) && e.blobs.length === 0, "F: sesión vencida durante la descarga");
  e = env({ fetch: () => Promise.reject(new TypeError("Failed to fetch")) });
  e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(/Se interrumpió la conexión/.test(allText(e.content)) && /no se entregó ningún archivo/.test(allText(e.content)) && api.state.working === false, "F: un corte de red se informa y libera el estado");
  e = env({ fetch: () => Promise.resolve({ ok: true, status: 200, redirected: false, headers: headers({ "Content-Type": "application/octet-stream" }), text: () => Promise.resolve("") }) });
  e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(/No se pudo completar la descarga/.test(allText(e.content)) && e.blobs.length === 0, "F: una respuesta inesperada no se entrega como ZIP");

  // ── G: sin resumen vigente / sin selección no se exporta ────────────────────────────────
  e = env(); api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(e.calls.length === 0, "G: sin resumen confirmable (sin digest) no se llama al servidor");
  e = env({ ids: [] }); e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(e.calls.length === 0, "G: sin selección no se exporta");
  e = env({ ids: Array.from({ length: 26 }, (_, i) => "i" + i) }); e.content.innerHTML = summaryHtml(); api.startExport(e.doc, e.win, { partial: false }); await tick();
  ok(e.calls.length === 0, "G: con más de 25 no se exporta");

  // ── H: nombre de archivo, mensajes con textContent y delegación ────────────────────────
  eq(api.filenameFrom('attachment; filename="cvs-candidatos-20261026-1530.zip"'), "cvs-candidatos-20261026-1530.zip", "H: nombre válido");
  for (const bad of ['attachment; filename="../../etc/passwd"', 'attachment; filename="..\\\\win.ini"', "", null, 'attachment; filename="<script>.zip"', 'attachment; filename=".."']) {
    const name = api.filenameFrom(bad);
    ok(/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) && !name.includes(".."), "H: nombre hostil se reemplaza por uno seguro: " + String(bad));
  }
  e = env(); innerHtmlWrites = 0;
  api.renderMessage(e.doc, "<img src=x onerror=alert(1)>", "<script>alert(1)</script> " + TOKEN, [{ label: "<b>x</b>", close: true }]);
  ok(innerHtmlWrites === 0 && e.content._html === "" && /<script>/.test(textOf(e.content)), "H: los mensajes del navegador usan textContent (nada se interpreta como HTML)");
  e = env({ fetch: () => Promise.resolve(htmlResponse(summaryHtml())) }); api.wire(e.doc, e.win);
  e.doc.handlers.click({ target: Object.assign(node("button"), { attrs: { "data-hc-download-open": "" } }) }); await tick();
  ok(e.calls.length === 1 && e.calls[0].url === api.SUMMARY_URL, "H: «Descargar CVs» (barra) abre el resumen");
  e.doc.handlers.click({ target: Object.assign(node("button"), { attrs: { "data-dl-reopen": "" } }) }); await tick();
  ok(e.calls.length === 2 && e.calls[1].url === api.SUMMARY_URL, "H: «Volver a revisar» reabre el resumen");
  e.doc.handlers.click({ target: Object.assign(node("button"), { attrs: { "data-dl-confirm": "" } }) }); await tick();
  ok(e.calls[2] && e.calls[2].url === api.DOWNLOAD_URL, "H: «Descargar ZIP» confirma");
  e.doc.handlers.click({ target: node("span") });
  eq(e.calls.length, 3, "H: un clic fuera de los controles no hace nada");

  if (failures > 0) { console.log(`${failures} Human Capital candidates download invariant(s) FAILED`); process.exit(1); }
  console.log("ALL HUMAN CAPITAL CANDIDATES DOWNLOAD INVARIANTS PASSED");
  process.exit(0);
})();
