// human_capital_new_profile_submit_loader.node.js — invocado por test_human_capital_new_profile_submit_loader.py.
//
// CONTRATO (UX, alta de candidato): el submit muestra el estado de carga del Hub («botón con carga INTEGRADA», `.asr-generate` + `.htmx-request`) de forma inmediata, lo mantiene durante toda
// la operación (token + subida del CV + respuesta), bloquea envíos duplicados y se restaura al volver desde el historial (bfcache). NO cambia el flujo de submit/token (eso lo cubre
// human_capital_create_cv_terminal_outcome.node.js, que sigue pasando sin cambios).
//
// Se ejecuta el JS REAL inline del form (extraído de human_capital_profile_new_form.html) contra un DOM mínimo con botones, classList, atributos y `window.addEventListener`.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const TEMPLATE = path.resolve(__dirname, "..", "..", "webapp", "templates", "partials", "human_capital_profile_new_form.html");
const m = fs.readFileSync(TEMPLATE, "utf-8").match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.log("FAIL: no se encontró <script> inline en el form de alta"); process.exit(1); }
const SCRIPT = m[1];
const BOUND_MS = 40;
let failures = 0;
function check(cond, label) { if (!cond) { failures++; console.log("FAIL: " + label); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function makeButton(name) {
  const b = { name, disabled: false, _classes: new Set() };
  b.classList = { add(c) { b._classes.add(c); }, remove(c) { b._classes.delete(c); }, contains(c) { return b._classes.has(c); } };
  return b;
}

function setup({ withFile, tokenFn }) {
  const main = makeButton("main");
  const dup = makeButton("dup");
  const attrs = {};
  const graphTokenField = { value: "" };
  const fileInput = { files: withFile ? [{ name: "cv.pdf" }] : [] };
  const windowListeners = {};
  const form = {
    id: "hc-profile-form",
    dataset: {},
    _submitCalls: 0,
    _handler: null,
    getAttribute(name) { return name === "data-hc-token-bound-ms" ? String(BOUND_MS) : (attrs[name] === undefined ? null : attrs[name]); },
    setAttribute(name, value) { attrs[name] = String(value); },
    removeAttribute(name) { delete attrs[name]; },
    addEventListener(type, fn) { if (type === "submit") this._handler = fn; },
    submit() { this._submitCalls++; },
    querySelector(sel) {
      if (sel.indexOf('type="file"') !== -1) return fileInput;
      if (sel.indexOf('name="graph_token"') !== -1) return graphTokenField;
      return null;
    },
    querySelectorAll(sel) { return sel.indexOf('button[type="submit"]') !== -1 ? [dup, main] : []; },
  };
  const document = { querySelectorAll(sel) { return sel.indexOf("data-hc-profile-new-form") !== -1 ? [form] : []; } };
  const win = { dwHumanCapitalDocumentWriteToken: tokenFn, addEventListener(type, fn) { windowListeners[type] = fn; } };
  const sandbox = { document, window: win, Promise, setTimeout, console };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT, sandbox);
  function fire(submitter) {
    const ev = { prevented: false, preventDefault() { this.prevented = true; }, submitter };
    form._handler(ev);
    return ev;
  }
  return { form, main, dup, fire, attrs, graphTokenField, pageshow: (persisted) => windowListeners.pageshow({ persisted }) };
}

function busyUi(ctx, button) { return button.classList.contains("htmx-request"); }

(async function () {
  // 1. estado normal: sin carga, sin deshabilitar, sin aria-busy
  {
    const c = setup({ withFile: false, tokenFn: () => Promise.resolve("T") });
    check(!busyUi(c, c.main) && !busyUi(c, c.dup), "normal: ningún botón en estado de carga");
    check(!c.main.disabled && !c.dup.disabled && c.attrs["aria-busy"] === undefined && c.form.dataset.hcBusy === undefined, "normal: interactivo, sin aria-busy");
  }

  // 2. submit sin CV: activación INMEDIATA; el submit nativo sigue (no se previene); `disabled` recién después del evento
  {
    const c = setup({ withFile: false, tokenFn: () => Promise.resolve("T") });
    const ev = c.fire(c.main);
    check(ev.prevented === false, "sin CV: el submit nativo NO se previene (comportamiento actual intacto)");
    check(busyUi(c, c.main) && !busyUi(c, c.dup), "sin CV: sólo el botón que envió muestra el estado de carga (inmediato)");
    check(c.attrs["aria-busy"] === "true", "sin CV: aria-busy en el form");
    check(c.main.disabled === false && c.dup.disabled === false, "sin CV: `disabled` NO se aplica dentro del evento (no se pierde name/value del submitter)");
    await wait(5);
    check(c.main.disabled === true && c.dup.disabled === true, "sin CV: tras el evento todos los submit quedan deshabilitados (anti doble envío)");
    check(c.form._submitCalls === 0, "sin CV: no hay form.submit() programático (lo hace el navegador)");
  }

  // 3. submit con CV: activación inmediata, persiste durante la espera del token y tras form.submit()
  {
    let resolveToken; let tokenCalls = 0;
    const c = setup({ withFile: true, tokenFn: () => { tokenCalls++; return new Promise((r) => { resolveToken = r; }); } });
    const ev = c.fire(c.main);
    check(ev.prevented === true, "con CV: se intercepta el submit (flujo de token existente)");
    check(busyUi(c, c.main) && c.attrs["aria-busy"] === "true", "con CV: el loader se activa ANTES de obtener el token");
    await wait(10);
    check(c.form._submitCalls === 0 && busyUi(c, c.main) && c.main.disabled === true, "con CV: sigue ocupado mientras el token está pendiente");
    resolveToken("TOK");
    await wait(10);
    check(c.form._submitCalls === 1 && c.graphTokenField.value === "TOK", "con CV: exactamente un submit con el token");
    check(busyUi(c, c.main) && c.attrs["aria-busy"] === "true" && c.main.disabled === true, "con CV: el loader se mantiene durante la subida/respuesta (hasta que el navegador reemplaza la página)");

    // 4. envíos duplicados mientras está ocupado: ni segundo token ni segundo submit
    const dupEv = c.fire(c.main);
    const dupEv2 = c.fire(c.dup);
    await wait(10);
    check(dupEv.prevented === true && dupEv2.prevented === true, "duplicado: el segundo submit se previene");
    check(tokenCalls === 1 && c.form._submitCalls === 1, "duplicado: un solo token y un solo submit");
  }

  // 5. el submitter «Cargar de todos modos» recibe el estado de carga y conserva su name/value durante el evento
  {
    const c = setup({ withFile: false, tokenFn: () => Promise.resolve("T") });
    c.fire(c.dup);
    check(busyUi(c, c.dup) && !busyUi(c, c.main), "submitter alternativo: sólo ese botón muestra la carga");
    check(c.dup.disabled === false, "submitter alternativo: no se deshabilita dentro del evento");
  }

  // 6. restauración: pageshow persistido limpia todo; no persistido no toca nada
  {
    const c = setup({ withFile: true, tokenFn: () => Promise.resolve("T") });
    c.fire(c.main);
    await wait(BOUND_MS + 60);
    c.pageshow(false);
    check(busyUi(c, c.main) && c.main.disabled === true, "pageshow no persistido: no cambia el estado");
    c.pageshow(true);
    check(!busyUi(c, c.main) && !busyUi(c, c.dup) && c.main.disabled === false && c.dup.disabled === false, "pageshow persistido: botones interactivos");
    check(c.attrs["aria-busy"] === undefined && c.form.dataset.hcBusy === undefined && c.form.dataset.hcTokenReady === undefined, "pageshow persistido: aria-busy y estado interno limpios");
    // y se puede volver a enviar (flujo completo otra vez)
    const before = c.form._submitCalls;
    c.fire(c.main);
    await wait(BOUND_MS + 60);
    check(c.form._submitCalls === before + 1 && busyUi(c, c.main), "tras restaurar: un nuevo envío vuelve a mostrar la carga y a enviar una vez");
  }

  if (failures === 0) { console.log("ALL CCHH NEW-PROFILE SUBMIT LOADER INVARIANTS PASSED"); process.exit(0); }
  console.log(failures + " CCHH NEW-PROFILE SUBMIT LOADER INVARIANT(S) FAILED");
  process.exit(1);
})();
