// reporting_kpi_smart_accent.node.js — invocado por
// test_reporting_kpi_smart_accent.py (via `node <este archivo>`), MISMO patrón
// que reporting_date_range_masking.node.js: carga reporting.js en un sandbox
// mínimo y ejecuta el PRIMITIVO REAL `renderDashboardKpi` contra casos
// sintéticos — prueba el COMPORTAMIENTO del override semántico de accent
// (SMART KPI TILE ACCENT OVERRIDE), no solo presencia de un patrón en el source.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {} },
  window: {},
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const { renderDashboardKpi } = sandbox;

let failures = 0;
function cls(html) {
  const m = html.match(/class="(activity-kpi[^"]*)"/);
  return m ? m[1] : "";
}
function assertClass(kpi, expected, label) {
  const actual = cls(renderDashboardKpi(kpi));
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected "${expected}", got "${actual}"`);
  }
}

// 1. KPI neutro (hours) conserva el accent patronado por defecto — sin override.
assertClass({ label: "Horas trabajadas", value: 37.5, format: "hours" }, "activity-kpi", "neutral hours keeps default");
// KPI neutro (count) tampoco se overridea aunque sea positivo.
assertClass({ label: "Proyectos", value: 12, format: "count" }, "activity-kpi", "neutral count keeps default");

// 2. KPI firmado positivo → accent verde.
assertClass({ label: "Rentabilidad horas", value: 34765, format: "signed" }, "activity-kpi activity-kpi--positive", "signed positive → green");

// 3. KPI firmado negativo → accent rojo.
assertClass({ label: "Rentabilidad horas", value: -365, format: "signed" }, "activity-kpi activity-kpi--negative", "signed negative → red");

// 4. KPI firmado con 0 o null → conserva el accent por defecto (neutral).
assertClass({ label: "Rentabilidad horas", value: 0, format: "signed" }, "activity-kpi", "signed zero → neutral");
assertClass({ label: "Rentabilidad horas", value: null, format: "signed" }, "activity-kpi", "signed null → neutral");

// 4b. Con 0, un accent explícito pasado NO se pisa (override solo cuando aplica).
assertClass({ label: "X", value: 0, format: "signed", accent: "warning" }, "activity-kpi activity-kpi--warning", "signed zero keeps explicit accent");

// 5. Opt-in/opt-out explícito vía smartAccent (independiente de format).
assertClass({ label: "Delta", value: 5, format: "hours", smartAccent: true }, "activity-kpi activity-kpi--positive", "smartAccent:true on non-signed → green");
assertClass({ label: "Rentabilidad horas", value: 5, format: "signed", smartAccent: false }, "activity-kpi", "smartAccent:false opts out");

if (failures === 0) {
  console.log("ALL SMART ACCENT INVARIANTS PASSED");
} else {
  console.log(`${failures} SMART ACCENT INVARIANT(S) FAILED`);
  process.exit(1);
}
