// reporting_salud_semaforo.node.js — invocado por
// test_reporting_salud_semaforo.py. Ejecuta el semáforo REAL de Salud de
// Cliente (saludScore/statusPillSalud/saludRollup) contra casos sintéticos —
// prueba el mapeo visual + el rollup semántico de filas padre colapsadas
// (SALUD DE CLIENTE — UX SEMÁFORO + ROLLUP), no solo presencia en el source.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");
const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } }, window: {},
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
  fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }); }, console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });
const { saludScore, statusPillSalud, saludRollup } = sandbox;

let failures = 0;
const color = h => (h.match(/badge--(\w+)/) || [])[1];
const text = h => (h.match(/aria-hidden="true">●<\/span>([^<]*)</) || [])[1];
function eq(actual, expected, label) {
  if (actual !== expected) { failures++; console.log(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`); }
}
const rows = (...vs) => vs.map(v => ({ f: v }));
const R = saludRollup("f");

// 1) Mapeo visual/semántico (ambos vocabularios: clasificación Y color).
eq(saludScore("Saludable"), 3, "score Saludable=3");
eq(saludScore("Verde"), 3, "score Verde=3");
eq(saludScore("Regular"), 2, "score Regular=2");
eq(saludScore("Amarillo"), 2, "score Amarillo=2");
eq(saludScore("Crítico"), 1, "score Crítico=1");
eq(saludScore("Rojo"), 1, "score Rojo=1");
eq(saludScore(""), null, "score vacío=null");
eq(saludScore("Excelente"), null, "score no reconocido=null");
eq(color(statusPillSalud("Saludable")), "green", "Saludable → verde");
eq(color(statusPillSalud("Regular")), "amber", "Regular → ámbar");
eq(color(statusPillSalud("Crítico")), "red", "Crítico → rojo");
eq(color(statusPillSalud("")), "grey", "Sin dato → neutro");
eq(text(statusPillSalud("")), "Sin dato", "vacío muestra 'Sin dato'");
// Accesibilidad: el texto (no solo el color) siempre presente.
eq(text(statusPillSalud("Saludable")), "Saludable", "el texto de la clasificación se mantiene (no solo color)");

// 2) Rollup homogéneo — todos los hijos iguales → ese mismo valor (caso Yanina).
eq(color(R(rows("Saludable", "Saludable", "Saludable"))), "green", "rollup homogéneo Saludable → verde");
eq(text(R(rows("Saludable", "Saludable", "Saludable"))), "Saludable", "rollup homogéneo → mismo valor");

// 3) Rollup mixto — promedio aritmético de scores válidos → clase.
eq(text(R(rows("Saludable", "Regular", "Crítico"))), "Regular", "mixto avg=2.0 → Regular");
eq(color(R(rows("Saludable", "Regular", "Crítico"))), "amber", "mixto avg=2.0 → ámbar");
eq(text(R(rows("Saludable", "Saludable", "Regular"))), "Saludable", "mixto avg≈2.67 (>=2.5) → Saludable");
eq(text(R(rows("Crítico", "Crítico", "Regular"))), "Crítico", "mixto avg≈1.33 (<1.5) → Crítico");
eq(text(R(rows("Regular", "Regular", "Crítico"))), "Regular", "mixto avg≈1.67 (>=1.5) → Regular");

// 4) Null handling — vacíos no contaminan el promedio; todos null → Sin dato.
eq(text(R(rows("Saludable", "", "Saludable"))), "Saludable", "null ignorado en el promedio");
eq(text(R(rows("Crítico", "", ""))), "Crítico", "un solo valor válido → esa clase");
eq(text(R(rows("", "", ""))), "Sin dato", "todos null → Sin dato");
eq(color(R(rows("", "", ""))), "grey", "todos null → neutro");

if (failures === 0) {
  console.log("ALL SALUD SEMAFORO INVARIANTS PASSED");
} else {
  console.log(`${failures} SALUD SEMAFORO INVARIANT(S) FAILED`);
  process.exit(1);
}
