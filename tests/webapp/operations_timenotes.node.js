// operations_timenotes.node.js — invocado por test_operations_timenotes_js.py.
//
// Hotfix de consistencia de notificaciones (Carga de Horas): `doNotify()` debe SEPARAR error de transporte, respuesta no interpretable y error de
// render posterior a una respuesta válida; una respuesta exitosa NUNCA debe convertirse en un falso «conexión interrumpida»; y `resultMessage()`
// ya no referencia la variable obsoleta `prueba` (sin ReferenceError en resultado parcial). DOM mínimo propio (no hay jsdom en el repo); se usa el
// seam `window.__opsTimenotes` (mismo patrón que `window.__cchhSelection`).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "operations_timenotes.js"), "utf-8");
function loadApi() {
  const sandbox = { window: {}, document: undefined, console };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "operations_timenotes.js" });
  return sandbox.window.__opsTimenotes;
}
const api = loadApi();
ok(api && typeof api.doNotify === "function" && typeof api.resultMessage === "function", "seam window.__opsTimenotes expuesto");

// ── DOM mínimo ───────────────────────────────────────────────────────────────
function makeEl(tag) {
  const n = {
    tag: tag || "div", className: "", textContent: "", attrs: {}, children: [], disabled: false, q: {}, qa: {},
    classList: { s: new Set(), add(c) { this.s.add(c); }, remove(c) { this.s.delete(c); }, contains(c) { return this.s.has(c); } },
    setAttribute(k, v) { n.attrs[k] = String(v); }, getAttribute(k) { return k in n.attrs ? n.attrs[k] : null; },
    hasAttribute(k) { return k in n.attrs; }, removeAttribute(k) { delete n.attrs[k]; },
    appendChild(c) { n.children.push(c); return c; },
    replaceChildren() { n.children = [].slice.call(arguments); },
    querySelector(sel) { return n.q[sel] || null; }, querySelectorAll(sel) { return n.qa[sel] || []; }, closest() { return null; },
  };
  return n;
}
function makeRoot() {
  const r = makeEl("div");
  r.attrs = { "data-timenotes-root": "", "data-semana": "", "data-horas-base": "", "data-simulacion": "0", "data-excepciones-ok": "1" };
  const notifyBtn = makeEl("button"); notifyBtn.attrs = { "data-tn-notify": "" };
  const status = makeEl("div"); status.attrs = { "data-tn-status": "" };
  const cell = makeEl("td"); cell.attrs = { "data-tn-notif-state": "" };
  const row = makeEl("tr"); row.attrs = { "data-tn-email": "ana@danwor.com" }; row.q = { "[data-tn-notif-state]": cell };
  r.q = { "[data-tn-notify]": notifyBtn, "[data-tn-status]": status, '[data-tn-email="ana@danwor.com"]': row };
  r.qa = { "[data-tn-feriado-opt]:checked": [], "[data-tn-estado-opt]:checked": [] };
  return { r, notifyBtn, status, cell };
}
function makeRootSel() {
  // root con TODO el andamiaje de selección, para que syncSelection() NO retorne temprano y ejecute la rama de `sim` (data-simulacion).
  const b = makeRoot();
  const tb = makeEl("tbody"); tb.attrs = { "data-tn-tbody": "colaboradores" }; tb.qa = { "[data-tn-select]": [] };
  b.r.q['[data-tn-tbody="colaboradores"]'] = tb;
  b.r.q["[data-tn-select-page]"] = null;
  ["[data-tn-selection-bar]", "[data-tn-selection-count]", "[data-tn-selection-label]", "[data-tn-selection-hint]"].forEach(s => { b.r.q[s] = makeEl("span"); });
  return b;
}
function fakeTableSelection() {
  const store = { reconcile() {}, size() { return 0; }, clear() {}, ids() { return []; }, has() { return false; }, add() {}, remove() {} };
  return { createStore() { return store; }, keepInPlace() {}, syncRows() { return { visibleSelected: 0 }; } };
}
function makeDoc(r) { return { createElement: makeEl, querySelector(sel) { return sel === "[data-timenotes-root]" ? r : null; }, defaultView: null }; }
function lastMsg(status) {
  const box = status.children[status.children.length - 1]; if (!box) return null;
  const tones = (box.className || "").split(" ").filter(c => c.indexOf("callout--") === 0 && c !== "callout--feedback").map(c => c.slice(9));
  let title = "", text = "";
  box.children.forEach(ch => {
    const cn = ch.className || "";
    if (cn.indexOf("callout__head") >= 0) ch.children.forEach(x => { if ((x.className || "").indexOf("callout__title") >= 0) title = x.textContent; });
    if (cn.indexOf("callout__body") >= 0) ch.children.forEach(p => { text = p.textContent; });
  });
  return { title, text, tone: tones[tones.length - 1] || "" };
}
const flush = () => new Promise(res => setTimeout(res, 0));
async function run(fetchImpl) {
  const { r, status, cell, notifyBtn } = makeRoot();
  let calls = 0;
  const win = { console, dwGraphToken: () => Promise.resolve("TOK"), fetch: function () { calls++; return fetchImpl.apply(null, arguments); } };
  api.doNotify(makeDoc(r), win, r, ["ana@danwor.com"]);
  for (let i = 0; i < 8; i++) await flush();
  return { msg: lastMsg(status), cell, notifyBtn, calls: () => calls };
}
function jsonRes(d, okFlag, st) { return Promise.resolve({ ok: okFlag !== false, status: st || 200, json: () => Promise.resolve(d) }); }

(async () => {
  // 1) Error de TRANSPORTE real (fetch rechaza) → "conexión interrumpida", sin reenvío.
  {
    const o = await run(() => Promise.reject(new Error("neterr")));
    ok(/no se pudo confirmar el resultado/i.test(o.msg.title) && /conexión se interrumpió/i.test(o.msg.text), "1 transporte: mensaje de conexión interrumpida");
    ok(o.calls() === 1, "1 transporte: sin reenvío automático (1 fetch)");
  }
  // 2) Respuesta NO interpretable (res.json rechaza), HTTP presente → NO es conexión interrumpida.
  {
    const o = await run(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError("bad")) }));
    ok(/no se pudo leer la confirmación/i.test(o.msg.title), "2 parsing: mensaje distinto (no interpretable)");
    ok(!/conexión se interrumpió/i.test(o.msg.text), "2 parsing: NO dice conexión interrumpida");
    ok(/pudo haberse procesado/i.test(o.msg.text) && o.calls() === 1, "2 parsing: fail-closed, sin reenvío");
  }
  // 3) accepted individual → confirmación + badge "Enviada".
  {
    const o = await run(() => jsonRes({ status: "ok", skipped: [], resultados: [{ colaborador: "ana@danwor.com", status: "accepted", recipient: "ana@danwor.com" }], resumen: { total: 1, accepted: 1 } }));
    ok(/microsoft aceptó el envío/i.test(o.msg.title) && o.msg.tone === "success", "3 accepted: confirmación de aceptación");
    ok(o.cell.textContent === "Enviada" && /solicitudes-status-badge--green/.test(o.cell.className), "3 accepted: badge Enviada (verde)");
  }
  // 4) duplicate → "ya se había enviado", NO tratado como nuevo envío.
  {
    const o = await run(() => jsonRes({ status: "ok", skipped: [], resultados: [{ colaborador: "ana@danwor.com", status: "duplicate", recipient: "ana@danwor.com" }], resumen: { total: 1, duplicate: 1 } }));
    ok(/ya se había enviado/i.test(o.msg.title), "4 duplicate: mensaje de duplicado");
    ok(o.cell.textContent === "Ya enviada", "4 duplicate: badge Ya enviada");
  }
  // 5) MIXTO/parcial → sin ReferenceError de `prueba`, mensaje parcial coherente.
  {
    const o = await run(() => jsonRes({ status: "ok", skipped: [], resultados: [{ colaborador: "ana@danwor.com", status: "accepted" }, { colaborador: "x@danwor.com", status: "failed" }], resumen: { total: 2, accepted: 1, failed: 1 } }));
    ok(/envío parcial/i.test(o.msg.title), "5 mixto: título de envío parcial (sin ReferenceError)");
    ok(/aceptado/i.test(o.msg.text) && /fallido/i.test(o.msg.text) && !/undefined/.test(o.msg.text), "5 mixto: resumen por estado, sin 'undefined' ni crash");
  }
  // 6) Respuesta válida pero ERROR al renderizar (resultados no iterable) → nunca "conexión interrumpida".
  {
    const o = await run(() => jsonRes({ status: "ok", skipped: [], resultados: 5, resumen: { total: 1, accepted: 1 } }));
    ok(!/conexión se interrumpió/i.test(o.msg.text), "6 render: NO dice conexión interrumpida");
    ok(/envío procesado/i.test(o.msg.title) || /actualizar la vista/i.test(o.msg.text), "6 render: informa procesado + problema de vista");
  }
  // 7) uncertain → incierto, sin reintento automático.
  {
    const o = await run(() => jsonRes({ status: "ok", skipped: [], resultados: [{ colaborador: "ana@danwor.com", status: "uncertain" }], resumen: { total: 1, uncertain: 1 } }));
    ok(/sin confirmación/i.test(o.msg.title) && /no se reenvía automáticamente/i.test(o.msg.text), "7 uncertain: incierto, sin reintento");
    ok(o.cell.textContent === "Incierta" && o.calls() === 1, "7 uncertain: badge Incierta, 1 fetch (sin reintento)");
  }

  // 8) RECORRIDO REAL accepted → applyResults → clearSelection → syncSelection CON dwTableSelection presente.
  //    Antes del fix, `if (sim)` en syncSelection lanzaba ReferenceError tras un accepted → la confirmación se perdía (render-error).
  {
    const apiSel = loadApi();
    const { r, status, cell } = makeRootSel();
    const win = { console, dwGraphToken: () => Promise.resolve("TOK"), dwTableSelection: fakeTableSelection(),
                  fetch: () => jsonRes({ status: "ok", skipped: [], resultados: [{ colaborador: "ana@danwor.com", status: "accepted", recipient: "ana@danwor.com" }], resumen: { total: 1, accepted: 1 } }) };
    apiSel.doNotify(makeDoc(r), win, r, ["ana@danwor.com"]);
    for (let i = 0; i < 8; i++) await flush();
    const m = lastMsg(status);
    ok(/microsoft aceptó el envío/i.test(m.title) && m.tone === "success", "8 syncSelection: confirmación se CONSERVA (sin ReferenceError de `sim`)");
    ok(!/actualizar la vista/i.test(m.text) && !/conexión se interrumpió/i.test(m.text), "8 syncSelection: no degrada a error de render/transporte");
    ok(cell.textContent === "Enviada", "8 syncSelection: badge Enviada tras el recorrido completo");
  }

  if (failures === 0) console.log("ALL OPERATIONS TIMENOTES NOTIFY INVARIANTS PASSED");
  else { console.log(`${failures} FAILED`); process.exit(1); }
})();
