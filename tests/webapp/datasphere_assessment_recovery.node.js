// datasphere_assessment_recovery.node.js — invocado por test_datasphere_assessment_long_run.py.
// Ejecuta el webapp/static/datasphere_assessment.js REAL contra un DOM y un fetch mínimos y verifica la recuperación tras un corte:
//   A. el stream se corta a mitad de la extracción → el navegador consulta …/operation/{run_id}?since=N, tolera una consulta fallida,
//      muestra solo lo nuevo y ofrece el informe con un enlace explícito, sin navegar solo (sin volver a ejecutar: un solo POST);
//   B. la operación ya no existe (404) → falla visible CONNECTION_INTERRUPTED, sin reintentos infinitos;
//   C. stream completo con latidos → sin consultas de recuperación; los `ping` no ensucian el detalle técnico.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "datasphere_assessment.js"), "utf-8");
let failures = 0;
const check = (cond, msg) => { if (!cond) { failures++; console.log("FAIL: " + msg); } };

class Node_ {
  constructor(attrs = {}) { this.attrs = { ...attrs }; this.hidden = false; this.textContent = ""; this.value = ""; this.disabled = false; this.open = false; this.dataset = attrs.dataset || {}; this.scrollTop = 0; this.scrollHeight = 0; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
}

function world(fetchImpl) {
  const nodes = {
    "[data-dsa-progress]": new Node_(), "[data-dsa-status]": new Node_({ dataset: { runningText: "Ejecutando extracción...", resumingText: "Retomando..." } }),
    "[data-dsa-spinner]": new Node_(), "[data-dsa-log]": new Node_(), "[data-dsa-detail]": new Node_(), "[data-dsa-failed]": new Node_(),
    "[data-dsa-title]": new Node_(), "[data-dsa-message]": new Node_(), "[data-dsa-done]": new Node_(), "[data-dsa-done-link]": new Node_(),
  };
  nodes["[data-dsa-done]"].hidden = true;
  nodes["[data-dsa-done]"].querySelector = (s) => nodes[s] || null;          // «Abrir Assessment» vive dentro del aviso de éxito
  const panel = { querySelector: (s) => nodes[s] || null };
  class HTMLFormElement extends Node_ {}
  const submit = new Node_();
  const form = new HTMLFormElement({ action: "/modules/04_X/datasphere-assessment/run", "data-dsa-form": "" });
  form.closest = (s) => (s === "#dsa-panel" ? panel : null);
  form.querySelector = (s) => (s === "[data-dsa-submit]" ? submit : null);
  form.querySelectorAll = () => [];
  let handler = null;
  const assigned = [];
  const sandbox = {
    console, URLSearchParams, TextDecoder, TextEncoder, JSON, Promise, Error, Set, Object, String,
    HTMLFormElement, FormData: class { constructor() { return []; } },
    setTimeout: (fn) => setImmediate(fn),                                   // sin esperas reales entre consultas
    document: { addEventListener: (type, fn) => { if (type === "submit") handler = fn; } },
    window: { location: { assign: (u) => assigned.push(u) } },
    fetch: fetchImpl,
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { nodes, form, submit, assigned, fire: () => handler({ target: form, defaultPrevented: false, preventDefault() {} }) };
}

function streamOf(lines, { breakAfter = false } = {}) {
  const enc = new TextEncoder();
  const chunks = lines.map((l) => enc.encode(JSON.stringify(l) + "\n"));
  let i = 0;
  return { ok: true, status: 200, body: { getReader: () => ({ read: async () => {
    if (i < chunks.length) return { done: false, value: chunks[i++] };
    if (breakAfter) throw new TypeError("network error");                 // el proxy corta la conexión
    return { done: true };
  } }) } };
}

const flush = () => new Promise((r) => setTimeout(r, 50));

(async () => {
  // A — corte a mitad + recuperación
  {
    const calls = [];
    let polls = 0;
    const w = world(async (url, opts) => {
      calls.push([url, (opts && opts.method) || "GET"]);
      if (url.endsWith("/run")) return streamOf([{ t: "start", run_id: "abc" }, { t: "line", v: "[RF 3/3] RF_Billing" }, { t: "ping" }], { breakAfter: true });
      polls += 1;
      if (polls === 1) throw new TypeError("red caída");                    // una consulta fallida se tolera
      return { ok: true, status: 200, json: async () => ({ events: [{ t: "line", v: "Replication Flows relacionados: 1/3" }, { t: "phase", phase: "assessing", v: "Generando assessment..." },
        { t: "done", state: "SUCCESS", result_url: "/modules/04_X/datasphere-assessment/result/r1?pack=p" }], next: 4, finished: true }) };
    });
    w.fire();
    await flush();
    const posts = calls.filter((c) => c[1] === "POST");
    const gets = calls.filter((c) => c[1] === "GET");
    check(posts.length === 1, "A: un solo POST (no se reinicia la extracción)");
    check(gets.length === 2 && gets.every((g) => g[0] === "/modules/04_X/datasphere-assessment/operation/abc?since=1"), "A: consulta la operación desde el evento 1: " + JSON.stringify(gets));
    check(w.nodes["[data-dsa-log]"].textContent === "[RF 3/3] RF_Billing\nReplication Flows relacionados: 1/3\n", "A: el detalle muestra cada línea una vez: " + JSON.stringify(w.nodes["[data-dsa-log]"].textContent));
    check(w.assigned.length === 0 && w.nodes["[data-dsa-done]"].hidden === false && String(w.nodes["[data-dsa-done-link]"].href).endsWith("/result/r1?pack=p"),
          "A: ofrece el informe recuperado con un enlace explícito (sin navegar solo)");
  }
  // B — la operación ya no existe
  {
    let gets = 0;
    const w = world(async (url) => {
      if (url.endsWith("/run")) return streamOf([{ t: "start", run_id: "gone" }], { breakAfter: true });
      gets += 1;
      return { ok: false, status: 404, json: async () => ({}) };
    });
    w.fire();
    await flush();
    check(gets === 1, "B: 404 detiene la recuperación");
    check(w.nodes["[data-dsa-failed]"].hidden === false && w.nodes["[data-dsa-log]"].textContent.includes("[CONNECTION_INTERRUPTED]"), "B: falla visible");
    check(w.nodes["[data-dsa-message]"].textContent.includes("Assessments recientes"), "B: indica dónde aparece un resultado terminado");
    check(w.submit.disabled === false && w.assigned.length === 0, "B: permite reintentar");
  }
  // C — stream completo con latidos
  {
    const calls = [];
    const w = world(async (url, opts) => {
      calls.push(url);
      return streamOf([{ t: "start", run_id: "ok" }, { t: "ping" }, { t: "line", v: "L1" }, { t: "ping" }, { t: "done", state: "SUCCESS", result_url: "/r" }]);
    });
    w.fire();
    await flush();
    check(calls.length === 1, "C: sin consultas de recuperación");
    check(w.nodes["[data-dsa-log]"].textContent === "L1\n" && w.assigned.length === 0 && w.nodes["[data-dsa-done-link]"].href === "/r",
          "C: latidos invisibles y el informe se ofrece con un enlace (sin navegar solo)");
  }
  console.log(failures ? `FAILURES: ${failures}` : "OK");
  process.exit(failures ? 1 : 0);
})();
