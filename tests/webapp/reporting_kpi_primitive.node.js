// reporting_kpi_primitive.node.js — invocado por
// test_reporting_kpi_primitive.py::test_kpi_primitive_invariants_hold
// (via `node <este archivo>`), mismo patrón que reporting_rollup_
// reconciliation.node.js (QA4): carga reporting.js en un sandbox mínimo y
// ejecuta el PRIMITIVO REAL (`renderDashboardKpi`) contra los valores
// exactos reportados por el owner en vivo — CRITICAL HUB STANDARDIZATION
// CORRECTIVE: "Horas Productivas → Resumen → TOTAL HORAS" mostraba
// "184613.24999999997" (float crudo de sumar horas de Time Logs ya
// individualmente redondeadas — el ruido de punto flotante de JS
// reaparece al sumar varias, incluso post-redondeo por fila).
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

const { renderDashboardKpi, fmtNum } = sandbox;

let failures = 0;
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
function assertContains(haystack, needle, label) {
  if (!haystack.includes(needle)) {
    failures++;
    console.log(`FAIL: ${label} — expected to contain ${JSON.stringify(needle)}, got ${JSON.stringify(haystack)}`);
  }
}
function valueOf(html) {
  const m = html.match(/<span class="[^"]*activity-kpi__value[^"]*">([^<]*)<\/span>/);
  return m ? m[1] : null;
}

// --- Test 1: el valor EXACTO reportado por el owner en vivo (Horas
// Productivas/Resumen/TOTAL HORAS) — nunca el artefacto crudo. ---
{
  const html = renderDashboardKpi({ label: "Total Horas", value: 184613.24999999997, format: "hours" });
  assertEqual(valueOf(html), "184,613.2", "184613.24999999997 -> nunca el artefacto crudo, limpio a 1 decimal");
  assertContains(html, "Total Horas", "conserva el label");
}

// --- Test 2: otro artefacto de punto flotante representativo. ---
{
  const html = renderDashboardKpi({ label: "X", value: 52111.100000000006, format: "hours" });
  assertEqual(valueOf(html), "52,111.1", "52111.100000000006 -> limpio, sin ruido");
}

// --- Test 3: contrato 0 vs null — 0 es un cero numérico VÁLIDO, nunca "—". ---
{
  assertEqual(valueOf(renderDashboardKpi({ label: "X", value: 0, format: "hours" })), "0", "0 -> '0', nunca guion");
  assertEqual(valueOf(renderDashboardKpi({ label: "X", value: 0, format: "count" })), "0", "0 (count) -> '0'");
}

// --- Test 4: contrato null/undefined — convención existente del Hub ("—"),
// NUNCA convertido a cero numérico. ---
{
  assertEqual(valueOf(renderDashboardKpi({ label: "X", value: null, format: "hours" })), "—", "null -> '—'");
  assertEqual(valueOf(renderDashboardKpi({ label: "X", value: undefined, format: "hours" })), "—", "undefined -> '—'");
}

// --- Test 5: signo — "signed" antepone '+' a positivos, colorea
// diff-pos/diff-neg (MISMA convención que diffCell en celdas de tabla). ---
{
  const pos = renderDashboardKpi({ label: "X", value: 10448.4, format: "signed" });
  assertEqual(valueOf(pos), "+10,448.4", "signed positivo: '+' explícito");
  assertContains(pos, "diff-pos", "signed positivo: clase de color diff-pos");

  const neg = renderDashboardKpi({ label: "X", value: -744, format: "signed" });
  assertEqual(valueOf(neg), "-744", "signed negativo: signo nativo, sin '+'");
  assertContains(neg, "diff-neg", "signed negativo: clase de color diff-neg");
}

// --- Test 6: NUNCA aceptar HTML/string ya formateado como `value` — el
// primitivo espera SIEMPRE el número semántico crudo (mismo error que dejó
// el KPI Diff Hs de Cartera en blanco, ver corrective S.3: pasar HTML por
// un formateador que espera número da NaN). Verificado indirectamente: un
// string con separador de miles ya aplicado NO debe reproducirse literal
// (fmtNum lo re-parsea vía Number(), que falla con comas). ---
{
  const html = renderDashboardKpi({ label: "X", value: "1,234", format: "hours" });
  assertEqual(valueOf(html), "—", "string con separador de miles ya aplicado -> Number() falla -> '—' (nunca reproducido literal)");
}

// --- Test 7: costura estructural — `class="activity-kpi__value"` SOLO
// puede aparecer dentro de `renderDashboardKpi`, en ningún otro sitio del
// archivo (ningún renderer construye su propio markup de tile KPI). ---
{
  // Arranca en el comentario que documenta el primitivo (menciona la
  // clase una vez, a propósito) hasta el cierre de la función — CUALQUIER
  // ocurrencia FUERA de este bloque es un bypass.
  // Normalizado a LF antes de ubicar los límites del bloque — el checkout
  // en Windows (core.autocrlf) deja el archivo en CRLF, y `\n}\n` nunca
  // matchea `\r\n}\r\n` (el `\r` queda pegado al `}`), lo que rompía esta
  // costura estructural con un falso positivo de bypass en CUALQUIER SO
  // con checkout CRLF — nunca un problema del primitivo en sí.
  const srcLf = src.replace(/\r\n/g, "\n");
  const blockStart = srcLf.indexOf("// --- Primitivo KPI compartido del Hub");
  const fnStart = srcLf.indexOf("function renderDashboardKpi(");
  const fnEnd = srcLf.indexOf("\n}\n", fnStart) + 3;
  const block = srcLf.slice(blockStart, fnEnd);
  const allOccurrences = (srcLf.match(/activity-kpi__value/g) || []).length;
  const occurrencesInBlock = (block.match(/activity-kpi__value/g) || []).length;
  assertEqual(allOccurrences, occurrencesInBlock, "TODA ocurrencia de 'activity-kpi__value' vive dentro del bloque de renderDashboardKpi (ningún bypass)");
}

if (failures > 0) {
  console.log(`${failures} KPI primitive invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL KPI PRIMITIVE INVARIANTS PASSED");
process.exit(0);
