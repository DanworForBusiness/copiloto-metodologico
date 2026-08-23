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
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  // `HubForegroundProgress` tambien vive en app.js. Antes de la Fase 1A este
  // harness no lo alcanzaba: el token se leia sincronicamente, asi que para
  // cuando corria `render()` el fetch stub ya habia resuelto y el status era
  // "ready". El `await` de la autoridad agrega un tick, asi que ahora se
  // ejercita la rama CORRECTA — la barra de progreso mientras carga.
  HubForegroundProgress: { begin() { }, complete() { }, fail() { }, settle() { } },
  // Los timers del browser: mismo motivo que el stub de arriba — la barra
  // compartida arranca su sweep con `setInterval`, y esta rama recien se
  // ejercita desde la Fase 1A. No-op: el sweep no es lo que se prueba aca.
  setInterval() { return 0; },
  clearInterval() { },
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
