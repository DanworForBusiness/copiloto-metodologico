// profile_send_dialog.node.js — invocado por test_profile_send.py::test_dialog_recipient_selector_confirmation_and_double_click_in_the_browser_module.
//
// Prueba el selector de destinatarios y el envío del diálogo «Compartir perfiles» del módulo REAL `human_capital_candidates_selection.js`
// (CCHH · Candidatos): chips (agregar, quitar, sin duplicados, tope), búsqueda en vivo (≥3 caracteres, resultados, sin coincidencias, bloqueo
// por permiso), envío directo (sin confirmación secundaria), estado «Enviando…», cierre + toast con 202, protección contra doble clic, identidad de
// operación por apertura, saludo personalizado y continuidad de «Copiar mensaje».
// DOM mínimo propio (no hay jsdom en el repo) y `fetch` / token delegado simulados: nada sale de este proceso.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, b, label) { ok(JSON.stringify(a) === JSON.stringify(b), `${label} (esperado ${JSON.stringify(b)}, real ${JSON.stringify(a)})`); }

const STATIC = path.resolve(__dirname, "..", "..", "webapp", "static");
const SRC = fs.readFileSync(path.join(STATIC, "table_selection.js"), "utf-8") + "\n" + fs.readFileSync(path.join(STATIC, "human_capital_candidates_selection.js"), "utf-8");
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "human_capital_candidates_selection.js" });
const api = sandbox.window.__cchhSelection;
const SP = "https://danwor.sharepoint.com/sites/cchh/";

// ── DOM mínimo ────────────────────────────────────────────────────────────────────────────
function node(tag, attrs) {
  const n = {
    tag, attrs: Object.assign({}, attrs || {}), children: [], parent: null, style: {}, value: "", disabled: false, hidden: false, innerHTML: "", _text: "",
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); } },
    getAttribute(k) { return k in n.attrs ? n.attrs[k] : null; },
    setAttribute(k, v) { n.attrs[k] = String(v); },
    removeAttribute(k) { delete n.attrs[k]; },
    hasAttribute(k) { return k in n.attrs; },
    appendChild(c) { n.children.push(c); c.parent = n; return c; },
    removeChild(c) { n.children.splice(n.children.indexOf(c), 1); return c; },
    focus() { n.focused = true; },
    matches() { return false; },
    closest() { return null; },
    querySelector(sel) {
      const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
      const walk = x => { for (const c of x.children) { if (m && (m[2] === undefined ? c.hasAttribute(m[1]) : c.getAttribute(m[1]) === m[2])) return c; const r = walk(c); if (r) return r; } return null; };
      return walk(n);
    },
  };
  Object.defineProperty(n, "textContent", { get: () => n._text, set: v => { n._text = String(v); n.children = []; } });   // como el DOM: asignar texto vacía los hijos
  Object.defineProperty(n, "firstChild", { get: () => n.children[0] || null });
  return n;
}

function dialog(opts) {
  const o = Object.assign({ directory: true, maxRecipients: 20, items: [{ id: "p1", name: "Ana Candidata", docs: [{ id: "d1", label: "cv.pdf", url: SP + "cv.pdf" }] }] }, opts || {});
  const f = {
    root: node("div", { "data-share-root": "", "data-default-subject": "Perfiles", "data-intro": "Hola", "data-outro": "Saludos",
                        "data-directory-enabled": o.directory ? "1" : "", "data-max-recipients": String(o.maxRecipients),
                        "data-recipient-provider": o.provider || "directory", "data-send-enabled": o.sendEnabled === false ? "" : "1" }),
    subject: node("input"), intro: node("textarea"), outro: node("textarea"), list: node("ul"), pending: node("p"), note: node("p"), status: node("p"),
    chips: node("ul"), search: node("input"), results: node("div"), resultsList: node("div"), hint: node("p"), blocked: node("p"),
    sendStatus: node("p"), send: node("button"), copy: node("button", { "data-share-copy": "mensaje" }),
    items: node("script"), unavailable: node("script"),
  };
  f.items._text = JSON.stringify(o.items); f.unavailable._text = "[]";
  f.blocked.hidden = !!o.directory; f.results.hidden = true; f.send.disabled = true; f.send.textContent = "Enviar";
  const map = {
    "[data-share-root]": f.root, "[data-share-subject]": f.subject, "[data-share-intro]": f.intro, "[data-share-outro]": f.outro, "[data-share-list]": f.list,
    "[data-share-pending]": f.pending, "[data-share-note]": f.note, "[data-share-status]": f.status, "[data-share-chips]": f.chips, "[data-share-search]": f.search,
    "[data-share-results]": f.results, "[data-share-results-list]": f.resultsList, "[data-share-results-hint]": f.hint, "[data-share-dir-blocked]": f.blocked,
    "[data-share-send-status]": f.sendStatus, "[data-share-send]": f.send,
    '[data-share-copy="mensaje"]': f.copy, "[data-share-items]": f.items, "[data-share-unavailable]": f.unavailable,
  };
  const content = node("div");
  content.querySelector = sel => map[sel] || f.list.querySelector(sel);
  const dlg = { open: true, closed: 0, close() { this.open = false; this.closed++; } };
  const toast = node("div");
  f.dialog = dlg; f.toast = toast; f.content = content;
  const doc = { getElementById: id => (id === "hc-share-content" ? content : id === "hc-share-dialog" ? dlg : null), createElement: tag => node(tag),
                querySelectorAll: () => [], querySelector: sel => (sel === "[data-hc-share-toast]" ? toast : null) };
  return { doc, f };
}

function makeWin(handler) {
  let n = 0;
  const win = {
    fetches: [],
    crypto: { randomUUID: () => `0000000${++n}-aaaa-bbbb-cccc-dddddddddddd` },
    dwHumanCapitalDirectoryToken: () => Promise.resolve(win.token === undefined ? "delegated-token" : win.token),
    fetch(url, init) {
      win.fetches.push({ url, body: init.body });
      const { status, data } = handler(url, init);
      return Promise.resolve({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(data) });
    },
  };
  return win;
}
const store = () => api.createStore(null);
const drain = () => new Promise(r => setImmediate(r));
const chips = f => f.chips.children.map(li => li.children[1].getAttribute("data-share-chip-remove"));
const params = body => { const out = {}; for (const kv of body.split("&")) { const [k, v] = kv.split("="); const key = decodeURIComponent(k); const val = decodeURIComponent(v); (out[key] = out[key] || []).push(val); } return out; };

(async function main() {
  // ── 4–6 · chips: agregar varios, sin duplicados (mayúsculas), quitar, tope ──
  {
    const { doc, f } = dialog({ maxRecipients: 2 });
    api.initShare(doc, store(), makeWin(() => ({ status: 200, data: {} })));
    ok(f.send.disabled === true, "4: sin destinatarios «Enviar por correo» está deshabilitado");
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana" });
    api.addRecipient(doc, { email: "ANA@danwor.com", name: "Ana otra vez" });
    api.addRecipient(doc, { email: "bruno@danwor.com", name: "Bruno" });
    eq(chips(f), ["ana@danwor.com", "bruno@danwor.com"], "4/6: varios destinatarios, sin duplicar por mayúsculas");
    ok(f.chips.children[0].children[0].textContent === "Ana · ana@danwor.com", "4: el chip muestra nombre y correo");
    ok(f.send.disabled === false, "4: con destinatarios y CV listo, se habilita el envío");
    api.addRecipient(doc, { email: "carla@danwor.com", name: "Carla" });
    eq(chips(f), ["ana@danwor.com", "bruno@danwor.com"], "4: el tope de destinatarios se respeta");
    ok(/hasta 2 destinatarios/.test(f.sendStatus.textContent), "4: aviso al superar el tope");
    api.removeRecipient(doc, "BRUNO@danwor.com");
    eq(chips(f), ["ana@danwor.com"], "5: quitar un chip (sin distinguir mayúsculas)");
    api.removeRecipient(doc, "ana@danwor.com");
    ok(f.send.disabled === true && chips(f).length === 0, "5: sin chips vuelve a deshabilitarse");
  }

  // ── 3 · búsqueda en vivo: ≥3 caracteres, resultados, sin coincidencias, elegir ──
  {
    const { doc, f } = dialog();
    const win = makeWin(url => ({ status: 200, data: { results: [{ name: "Ana Directorio", email: "ana.directorio@danwor.com" }] } }));
    api.initShare(doc, store(), win);
    f.search.value = "an";
    api.runSearch(doc, win); await drain(); await drain();
    ok(win.fetches.length === 0 && /al menos 3/.test(f.hint.textContent), "3: con menos de 3 caracteres no se consulta el directorio");
    f.search.value = "Ana";
    api.runSearch(doc, win); for (let i = 0; i < 5; i++) await drain();
    ok(win.fetches.length === 1 && win.fetches[0].url === api.DIRECTORY_URL, "3: con 3+ caracteres se consulta la ruta del directorio");
    const p = params(win.fetches[0].body);
    ok(p.q[0] === "Ana" && p.graph_token[0] === "delegated-token", "3: viaja el término y el token DELEGADO (en el cuerpo, nunca en la URL)");
    ok(f.resultsList.children.length === 1 && f.resultsList.children[0].getAttribute("data-share-pick") === "ana.directorio@danwor.com", "3: se listan los resultados");
    const empty = makeWin(() => ({ status: 200, data: { results: [] } }));
    f.search.value = "zzzz"; api.runSearch(doc, empty); for (let i = 0; i < 5; i++) await drain();
    ok(f.resultsList.children.length === 0 && /Sin coincidencias/.test(f.hint.textContent), "3: sin resultados se informa");
    const blocked = makeWin(() => ({ status: 503, data: { blocked: true, message: "Permiso de directorio pendiente." } }));
    f.search.value = "Ana"; api.runSearch(doc, blocked); for (let i = 0; i < 5; i++) await drain();
    ok(f.search.disabled === true && f.blocked.hidden === false && f.blocked.textContent === "Permiso de directorio pendiente.", "3: 503 bloquea la búsqueda con el mensaje del servidor");
    api.addRecipient(doc, { email: "x@danwor.com", name: "X" });
    ok(f.send.disabled === true, "3: con el directorio bloqueado no se puede enviar (Copiar/Outlook siguen)");
  }

  // ── envío directo, estado de procesamiento, doble clic, cierre + toast con 202 ──
  {
    const { doc, f } = dialog();
    let release;
    const win = makeWin(() => ({ status: 200, data: { status: "accepted", recipients: ["ana@danwor.com"] } }));
    const realFetch = win.fetch;
    win.fetch = (url, init) => new Promise(r => { release = () => r(realFetch(url, init)); });     // el servidor «tarda»: se ve el estado intermedio
    api.initShare(doc, store(), win);
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana Pérez" });
    api.doSend(doc, win); api.doSend(doc, win);                                                   // «Enviar» + doble clic: sin confirmación secundaria
    for (let i = 0; i < 4; i++) await drain();
    ok(f.send.disabled === true && f.send.textContent === "Enviando…" && f.send.getAttribute("aria-busy") === "true", "UX: al enviar el botón se deshabilita y muestra «Enviando…»");
    ok(f.dialog.open === true, "UX: mientras procesa, el diálogo sigue abierto");
    release(); for (let i = 0; i < 6; i++) await drain();
    ok(win.fetches.length === 1 && win.fetches[0].url === api.SEND_URL, "UX: «Enviar» envía directo y el doble clic produce UN solo request");
    const p = params(win.fetches[0].body);
    ok(/^[A-Za-z0-9_-]{8,64}$/.test(p.operation_id[0]) && p.recipients[0] === "ana@danwor.com" && p.profile_ids[0] === "p1" && p.graph_token[0] === "delegated-token",
       "11: viajan identidad de operación, destinatarios, ids de candidatos y token delegado");
    ok(!Object.keys(p).some(k => /url|link|href/i.test(k)), "2: el navegador no envía enlaces: el servidor los resuelve");
    ok(p.message[0] === f.intro.value || /^Hola Ana,/.test(p.message[0]), "correo: el cuerpo viaja solo (saludo + texto del usuario)");
    ok(!/https?:\/\//.test(p.message[0]) && !/Ana Candidata/.test(p.message[0]) && p.closing[0] === "Saludos", "correo: sin lista ni URLs en el cuerpo; el cierre viaja aparte");
    ok(f.dialog.open === false && f.dialog.closed === 1 && f.content.children.length === 0, "UX: 202 cierra el diálogo y limpia su contenido");
    const box = f.toast.children[0];
    ok(box && box.className === "callout callout--success callout--feedback" && box.getAttribute("role") === "status"
       && box.textContent === "Correo aceptado para envío a 1 destinatario.", "UX: toast de éxito FUERA del diálogo, con el patrón compartido (aceptado, no «entregado»)");
    ok(api._state().operationId === "" && api._state().recipients.length === 0, "UX: el estado local del diálogo queda limpio");
    eq(api.acceptedText(3), "Correo aceptado para envío a 3 destinatarios.", "UX: plural correcto");
  }
  {
    const { doc, f } = dialog();
    const win = makeWin(() => ({ status: 200, data: { status: "accepted" } }));
    api.initShare(doc, store(), win);
    const firstOp = api._state().operationId;
    api.initShare(doc, store(), win);                                                            // reabrir el diálogo = envío deliberado nuevo
    ok(api._state().operationId !== firstOp && api._state().sent === false, "12: cada apertura del diálogo tiene una identidad de operación nueva");
  }

  // ── 13 + 15 · resultados del servidor ──
  // errores: el diálogo NO se cierra, se conserva todo; incierto/fallido bloquean el reenvío con la misma operación
  for (const [data, sent, rx, label] of [
    [{ status: "uncertain" }, true, /sin confirmación de Microsoft/, "15: incierto → bloqueado, sin reintento automático"],
    [{ status: "failed", message: "El servicio de correo rechazó el envío." }, true, /rechazó/, "13: rechazo de Graph → bloqueado"],
    [{ status: "duplicate" }, true, /ya se había registrado/, "11: duplicado informado"],
    [{ status: "error", reason: "no_recipients", message: "No hay destinatarios corporativos válidos seleccionados." }, false, /No hay destinatarios/, "7: validación del servidor → reintentable"],
    [{ status: "config_incomplete", message: "El correo institucional no está configurado; no se envió nada." }, false, /no está configurado/, "config incompleta → reintentable con la misma operación"],
  ]) {
    const { doc, f } = dialog();
    const win = makeWin(() => ({ status: 200, data }));
    api.initShare(doc, store(), win);
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana" });
    f.subject.value = "Asunto editado"; f.outro.value = "Cierre editado";
    const op = api._state().operationId;
    api.doSend(doc, win); for (let i = 0; i < 6; i++) await drain();
    ok(api._state().sent === sent && rx.test(f.sendStatus.textContent), label);
    ok(f.dialog.open === true && f.toast.children.length === 0, label + " · el diálogo sigue abierto y sin toast de éxito");
    ok(f.subject.value === "Asunto editado" && f.outro.value === "Cierre editado" && api._state().recipients.length === 1 && api._state().operationId === op,
       label + " · se conservan contenido, destinatarios y la MISMA operación");
    ok(f.send.disabled === sent && f.send.textContent === "Enviar", label + (sent ? " · sin reenvío inseguro" : " · se puede corregir y reintentar"));
  }

  // ── sin token delegado no se envía ──
  {
    const { doc, f } = dialog();
    const win = makeWin(() => ({ status: 200, data: { status: "accepted" } }));
    win.token = "";
    api.initShare(doc, store(), win);
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana" });
    api.doSend(doc, win); for (let i = 0; i < 4; i++) await drain();
    ok(win.fetches.length === 0 && /autorización de Microsoft/.test(f.sendStatus.textContent) && api._state().sent === false, "sin token delegado no hay envío y se puede reintentar");
  }

  // ── 18 · «Copiar mensaje» sigue disponible sin proveedor de destinatarios ──
  {
    const { doc, f } = dialog({ directory: false });
    api.initShare(doc, store(), makeWin(() => ({ status: 200, data: {} })));
    ok(f.search.disabled === true && f.blocked.hidden === false, "18: sin proveedor de destinatarios el selector queda inerte con su aviso");
    ok(f.copy.disabled === false, "18: «Copiar mensaje» sigue habilitado con el mensaje listo");
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana" });
    ok(f.send.disabled === true, "18: «Enviar» no se habilita sin proveedor");
  }

  // ── Saludo personalizado: un destinatario con nombre verificado → «Hola <Nombre>,»; 0, varios o sin nombre → «Hola,» ──
  {
    const { doc, f } = dialog();
    f.root.attrs["data-intro"] = "Hola, te comparto los siguientes perfiles:";
    api.initShare(doc, store(), makeWin(() => ({ status: 200, data: {} })));
    const greet = () => f.intro.value.split(",")[0] + ",";
    eq(greet(), "Hola,", "saludo: sin destinatarios, genérico");
    api.addRecipient(doc, { email: "bjurado@danwor.com", name: "Bruno Jurado" });
    eq(f.intro.value, "Hola Bruno, te comparto los siguientes perfiles:", "saludo: un destinatario identificado → solo el nombre de pila; el resto intacto");
    api.addRecipient(doc, { email: "otra@danwor.com", name: "Otra Persona" });
    eq(greet(), "Hola,", "saludo: dos destinatarios → genérico");
    api.removeRecipient(doc, "otra@danwor.com");
    eq(greet(), "Hola Bruno,", "saludo: al quitar uno se recupera el saludo de la persona restante");
    api.removeRecipient(doc, "bjurado@danwor.com");
    eq(greet(), "Hola,", "saludo: sin destinatarios vuelve al genérico");
    api.addRecipient(doc, { email: "sin.padron@danwor.com", name: "sin.padron@danwor.com" });
    eq(greet(), "Hola,", "saludo: sin nombre verificado (solo correo) → genérico; nunca se deduce del alias");
    api.removeRecipient(doc, "sin.padron@danwor.com");
    // el usuario edita el saludo → se respeta
    f.intro.value = "Estimado equipo, te comparto los siguientes perfiles:";
    api.addRecipient(doc, { email: "bjurado@danwor.com", name: "Bruno Jurado" });
    eq(f.intro.value, "Estimado equipo, te comparto los siguientes perfiles:", "saludo: editado a mano → no se sobrescribe");
    api.removeRecipient(doc, "bjurado@danwor.com");
    eq(f.intro.value, "Estimado equipo, te comparto los siguientes perfiles:", "saludo: sigue respetado al cambiar destinatarios");
  }
  {
    const { doc, f } = dialog();
    f.root.attrs["data-intro"] = "Hola, te comparto los siguientes perfiles:";
    api.initShare(doc, store(), makeWin(() => ({ status: 200, data: {} })));
    f.intro.value = "Hola, te comparto estos perfiles que vimos ayer:";                          // edita el cuerpo, no el saludo
    api.addRecipient(doc, { email: "bjurado@danwor.com", name: "Bruno Jurado" });
    eq(f.intro.value, "Hola Bruno, te comparto estos perfiles que vimos ayer:", "saludo: con el cuerpo editado solo cambia el saludo; el texto del usuario se conserva");
  }

  // ── proveedor transitorio «allowlist»: catálogo UNA vez por apertura (token base de lectura), filtro en memoria, mismo envío ──
  {
    const { doc, f } = dialog({ provider: "allowlist" });
    const CATALOG = [{ name: "José Pérez Núñez", email: "jperez@danwor.com" }, { name: "sinnombre@danwor.com", email: "sinnombre@danwor.com" }];
    const win = makeWin(url => ({ status: 200, data: url === api.CATALOG_URL ? { recipients: CATALOG, names_resolved: 1 } : { status: "accepted" } }));
    let directoryAsked = 0, baseAsked = 0;
    win.dwHumanCapitalDirectoryToken = () => { directoryAsked++; return Promise.resolve("x"); };
    win.dwGraphToken = () => { baseAsked++; return Promise.resolve("base-read-token"); };
    api.initShare(doc, store(), win); for (let i = 0; i < 5; i++) await drain();
    ok(win.fetches.length === 1 && win.fetches[0].url === api.CATALOG_URL && params(win.fetches[0].body).graph_token[0] === "base-read-token" && baseAsked === 1 && directoryAsked === 0,
       "allowlist: UNA carga del catálogo al abrir, con el token BASE de lectura (nunca el de directorio)");
    const pick = q => { f.search.value = q; api.runSearch(doc, win); return f.resultsList.children.map(o => o.getAttribute("data-share-pick")); };
    eq(pick("jose"), ["jperez@danwor.com"], "allowlist: busca por nombre sin distinguir acentos");
    eq(pick("NUÑEZ"), ["jperez@danwor.com"], "allowlist: busca por apellido");
    eq(pick("jpere"), ["jperez@danwor.com"], "allowlist: busca por alias");
    eq(pick("sinnombre@dan"), ["sinnombre@danwor.com"], "allowlist: busca por correo; sin nombre resuelto queda el correo");
    eq(pick("zzz"), [], "allowlist: sin coincidencias");
    ok(win.fetches.length === 1, "allowlist: escribir no consulta al servidor (filtro en memoria)");
    pick("pérez");
    const opt = f.resultsList.children[0];
    ok(opt.children.length === 2 && opt.children[0].textContent === "José Pérez Núñez" && opt.children[1].textContent === "jperez@danwor.com"
       && opt.children[1].className === "hc-share-option__email", "allowlist: «Nombre Apellido» y debajo el correo");
    pick("sinnombre");
    ok(f.resultsList.children[0].children.length === 0 && f.resultsList.children[0].textContent === "sinnombre@danwor.com", "allowlist: sin nombre, una sola línea con el correo");
    api.addRecipient(doc, { email: "jperez@danwor.com", name: "José Pérez Núñez" });
    api.doSend(doc, win); for (let i = 0; i < 6; i++) await drain();
    const sent = params(win.fetches[1].body);
    ok(win.fetches.length === 2 && win.fetches[1].url === api.SEND_URL && sent.recipients[0] === "jperez@danwor.com" && directoryAsked === 0 && f.dialog.open === false,
       "allowlist: envía solo el correo (el nombre es presentación) y sin token de directorio");
  }
  {
    const { doc, f } = dialog({ provider: "allowlist" });
    const win = makeWin(() => ({ status: 500, data: {} }));
    win.dwGraphToken = () => Promise.reject(new Error("sin cuenta"));
    api.initShare(doc, store(), win); for (let i = 0; i < 6; i++) await drain();
    f.search.value = "abc"; api.runSearch(doc, win);
    ok(api._state().catalog !== null && f.resultsList.children.length === 0 && /Sin coincidencias/.test(f.hint.textContent), "allowlist: si el catálogo falla no se cuelga (sin resultados, se puede reabrir)");
  }

  // ── interruptor global apagado: «Enviar» deshabilitado, «Copiar mensaje» disponible; el estado «disabled» del servidor bloquea el reenvío ──
  {
    const { doc, f } = dialog({ sendEnabled: false });
    const win = makeWin(() => ({ status: 200, data: { status: "accepted" } }));
    api.initShare(doc, store(), win);
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana" });
    ok(f.send.disabled === true && f.copy.disabled === false, "interruptor: apagado → «Enviar» deshabilitado y «Copiar mensaje» disponible");
    api.doSend(doc, win); await drain();
    ok(win.fetches.length === 0, "interruptor: apagado → no se intenta enviar");
  }
  {
    const { doc, f } = dialog();
    const win = makeWin(() => ({ status: 503, data: { status: "disabled", reason: "disabled", message: "El envío por correo institucional está deshabilitado. Podés usar «Copiar mensaje»." } }));
    api.initShare(doc, store(), win);
    api.addRecipient(doc, { email: "ana@danwor.com", name: "Ana" });
    api.doSend(doc, win); for (let i = 0; i < 6; i++) await drain();
    ok(f.dialog.open === true && f.send.disabled === true && /deshabilitado/.test(f.sendStatus.textContent) && f.copy.disabled === false,
       "interruptor: cambio de estado durante la operación → diálogo abierto, «Enviar» bloqueado, «Copiar mensaje» sigue");
    api.doSend(doc, win); await drain();
    ok(win.fetches.length === 1, "interruptor: sin reintentos después de «disabled»");
  }

  if (failures) { console.log(`${failures} PROFILE SEND DIALOG CHECK(S) FAILED`); process.exit(1); }
  console.log("ALL PROFILE SEND DIALOG CHECKS PASSED");
  process.exit(0);
})();
