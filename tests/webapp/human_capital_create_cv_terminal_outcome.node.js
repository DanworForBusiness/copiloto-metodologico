// human_capital_create_cv_terminal_outcome.node.js — invocado por
// test_human_capital_create_cv_terminal_outcome.py.
//
// CONTRATO (owner, CCHH create-with-CV): clic en "Cargar CV" con un archivo
// debe SIEMPRE terminar en EXACTAMENTE un `form.submit()` (outcome observable;
// el backend traduce cualquier token, incluso "", a un estado visible). El
// defecto raíz probado: si la promesa de token queda PENDIENTE, el flujo queda
// inerte para siempre (silent no-op). El fix impone un BOUND (Promise.race) y
// un guard de submit único; el token se pide SILENT-ONLY (sin popup).
//
// Se ejecuta el JS REAL inline del form (extraído de
// human_capital_profile_new_form.html) contra un DOM mínimo, con un fake
// configurable de `window.dwHumanCapitalDocumentWriteToken`. El bound se acota
// a 40ms vía el atributo `data-hc-token-bound-ms` que el JS ya respeta.
//
// Cinco outcomes:
//   1. token resuelve con valor       -> exactamente un submit, con el token
//   2. token resuelve ""              -> exactamente un submit, vacío
//   3. token rechaza                  -> exactamente un submit, vacío
//   4. token pendiente pasado el bound-> exactamente un submit, vacío
//   5. token resuelve TARDE (post-bound) -> sigue siendo exactamente un submit
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const TEMPLATE = path.resolve(
  __dirname, "..", "..", "webapp", "templates", "partials",
  "human_capital_profile_new_form.html",
);
const html = fs.readFileSync(TEMPLATE, "utf-8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.log("FAIL: no se encontró <script> inline en el form de alta"); process.exit(1); }
const SCRIPT = m[1];

const BOUND_MS = 40;
let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log("FAIL: " + label); } }

function setup(tokenFn) {
  const graphTokenField = { value: "" };
  const fileInput = { files: [{ name: "cv.pdf" }] };
  const form = {
    dataset: {},
    _submitCalls: 0,
    _submitHandler: null,
    getAttribute(name) { return name === "data-hc-token-bound-ms" ? String(BOUND_MS) : null; },
    addEventListener(type, fn) { if (type === "submit") this._submitHandler = fn; },
    submit() { this._submitCalls++; },
    querySelector(sel) {
      if (sel.indexOf('type="file"') !== -1) return fileInput;
      if (sel.indexOf('name="graph_token"') !== -1) return graphTokenField;
      return null;
    },
  };
  const document = {
    querySelectorAll(sel) {
      return sel.indexOf("data-hc-profile-new-form") !== -1 ? [form] : [];
    },
  };
  const sandbox = { document, window: { dwHumanCapitalDocumentWriteToken: tokenFn }, Promise, setTimeout, console };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT, sandbox);
  function fire() { form._submitHandler({ preventDefault() {} }); }
  return { form, fire, graphTokenField };
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async function () {
  // 1. token con valor -> un submit con el token
  {
    const { form, fire, graphTokenField } = setup(() => Promise.resolve("TOK123"));
    fire();
    await wait(BOUND_MS + 60);
    assertTrue(form._submitCalls === 1, "token con valor: exactamente un submit");
    assertTrue(graphTokenField.value === "TOK123", "token con valor: graph_token seteado");
  }

  // 2. token "" -> un submit vacío
  {
    const { form, fire, graphTokenField } = setup(() => Promise.resolve(""));
    fire();
    await wait(BOUND_MS + 60);
    assertTrue(form._submitCalls === 1, "token vacío: exactamente un submit");
    assertTrue(graphTokenField.value === "", "token vacío: graph_token vacío");
  }

  // 3. token rechaza -> un submit vacío
  {
    const { form, fire, graphTokenField } = setup(() => Promise.reject(new Error("boom")));
    fire();
    await wait(BOUND_MS + 60);
    assertTrue(form._submitCalls === 1, "token rechaza: exactamente un submit");
    assertTrue(graphTokenField.value === "", "token rechaza: graph_token vacío");
  }

  // 4. token pendiente pasado el bound -> un submit vacío (el bound lo garantiza)
  {
    const { form, fire, graphTokenField } = setup(() => new Promise(function () { /* nunca resuelve */ }));
    fire();
    await wait(BOUND_MS + 80);
    assertTrue(form._submitCalls === 1, "token pendiente: el bound garantiza exactamente un submit");
    assertTrue(graphTokenField.value === "", "token pendiente: submit con token vacío");
  }

  // 5. token resuelve TARDE (después del bound) -> sigue siendo un solo submit
  {
    let lateResolve;
    const { form, fire } = setup(() => new Promise((res) => { lateResolve = res; }));
    fire();
    await wait(BOUND_MS + 40);                 // el bound ya disparó el submit
    if (lateResolve) lateResolve("LATE_TOKEN"); // resolución tardía
    await wait(60);
    assertTrue(form._submitCalls === 1, "resolución tardía post-bound: sigue siendo exactamente un submit");
  }

  if (failures === 0) {
    console.log("ALL CCHH CREATE-CV TERMINAL OUTCOME INVARIANTS PASSED");
    process.exit(0);
  } else {
    console.log(failures + " CCHH CREATE-CV TERMINAL OUTCOME INVARIANT(S) FAILED");
    process.exit(1);
  }
})();
