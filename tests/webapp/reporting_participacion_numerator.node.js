// reporting_participacion_numerator.node.js — invocado por
// test_reporting_participacion_numerator.py (mismo patrón que
// reporting_capacidad_panel_independence.node.js/reporting_temporal_contract.node.js):
// carga reporting.js en un sandbox mínimo y ejecuta el MOTOR REAL de
// Participación (comercialParticipacionSelectedRows/comercialPctVendidas/
// comercialPctCertificadas/comercialParticipacionTotalVendidas) contra una
// fixture sintética con hechos REALES no-cero — prueba la CADENA COMPLETA
// selected temporal fact total → numerador → denominador fijo → porcentaje
// renderizado (owner live QA corrective: "127 clientes, TODOS 0.00%" — el
// bug real era `Object.keys()` sobre un NÚMERO plano en vez de un objeto
// {estado: horas}, ver ROOT CAUSE CORRECTIVE en reporting_service.py).
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

const {
  comercialParticipacionSelectedRows, comercialParticipacionTotalVendidas,
  comercialParticipacionTotalCertificadas, comercialPctVendidas, comercialPctCertificadas,
} = sandbox;
const state = vm.runInContext("state", sandbox);
const COMERCIAL_PARTICIPACION_ROWS = vm.runInContext("COMERCIAL_PARTICIPACION_ROWS", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

// Fixture: 3 clientes — YPF (En curso, 2025+2026), Danone (Finalizado,
// 2026 SOLO), Aluar (sin hechos — cliente_dim sin actividad calificante).
// Mismo escenario del owner: Años={2025,2026}, Estado={En curso}.
COMERCIAL_PARTICIPACION_ROWS.length = 0;
COMERCIAL_PARTICIPACION_ROWS.push(
  {
    idFrecuencia: "1 Mensual", permiteAdelanto: "Si", condicionPago: "30", cliente: "YPF", cp: "Diego Ramos",
    vendidasByYear: { "2025": { "En curso": 100 }, "2026": { "En curso": 200 } },
    certificadasByYear: { "2025": { "En curso": 90 }, "2026": { "En curso": 180 } },
  },
  {
    idFrecuencia: "3 Trimestral", permiteAdelanto: "No", condicionPago: "60", cliente: "Danone", cp: "María López",
    vendidasByYear: { "2026": { "Finalizado": 50 } },
    certificadasByYear: { "2026": { "Finalizado": 48 } },
  },
  {
    idFrecuencia: "", permiteAdelanto: "", condicionPago: "", cliente: "Aluar", cp: "",
    vendidasByYear: {}, certificadasByYear: {},
  },
);

// --- Escenario EXACTO del owner: Años={2025,2026}, Estado={En curso} ------
{
  state.comercialParticipacionAnios = new Set(["2025", "2026"]);
  state.comercialStatus = { "En curso": true, "Finalizado": false };

  const rows = comercialParticipacionSelectedRows();
  const ypf = rows.find(r => r.cliente === "YPF");
  const danone = rows.find(r => r.cliente === "Danone");
  const aluar = rows.find(r => r.cliente === "Aluar");

  assertEqual(ypf.horasVendidas, 300, "YPF (En curso, 2025+2026): numerador NO CERO — 100+200=300");
  assertEqual(ypf.horasCertificadas, 270, "YPF certificadas: 90+180=270");
  assertEqual(danone.horasVendidas, 0, "Danone (Finalizado, Estado NO tildado) queda en 0 — Estado SÍ cambia la contribución");
  assertEqual(aluar.horasVendidas, 0, "Aluar (sin hechos) queda en 0 — Cliente dimension row sin actividad calificante");

  const total = comercialParticipacionTotalVendidas();
  assertEqual(total, 300, "denominador fijo = SOLO YPF bajo Estado=En curso (Danone excluido, Aluar sin hechos)");
  assertTrue(total > 0, "el denominador NO es cero cuando existen hechos calificantes para el contexto temporal seleccionado");

  const pct = comercialPctVendidas(ypf.horasVendidas);
  assertTrue(pct !== "0.00 %", `el % Participación de YPF NO debe ser 0.00% — se obtuvo "${pct}"`);
  assertEqual(pct, "100.00 %", "YPF es el ÚNICO contribuyente bajo este contexto ⇒ 100.00% (denominador fijo, no hay otros hechos calificantes)");
}

// --- Estado cambia la contribución: tildar Finalizado también -------------
{
  state.comercialParticipacionAnios = new Set(["2025", "2026"]);
  state.comercialStatus = { "En curso": true, "Finalizado": true };

  const rows = comercialParticipacionSelectedRows();
  const danone = rows.find(r => r.cliente === "Danone");
  assertEqual(danone.horasVendidas, 50, "con Finalizado tildado, Danone SÍ contribuye (antes 0)");

  const total = comercialParticipacionTotalVendidas();
  assertEqual(total, 350, "el denominador crece al incluir Finalizado (300 YPF + 50 Danone)");
}

// --- Año multi-select agrega correctamente ---------------------------------
{
  state.comercialParticipacionAnios = new Set(["2025"]);
  state.comercialStatus = { "En curso": true, "Finalizado": false };
  const rows = comercialParticipacionSelectedRows();
  const ypf = rows.find(r => r.cliente === "YPF");
  assertEqual(ypf.horasVendidas, 100, "Año=2025 SOLO: únicamente el bucket 2025 (100), nunca 2026 (200) mezclado");

  state.comercialParticipacionAnios = new Set(["2025", "2026"]);
  const rows2 = comercialParticipacionSelectedRows();
  const ypf2 = rows2.find(r => r.cliente === "YPF");
  assertEqual(ypf2.horasVendidas, 300, "Año={2025,2026}: se agregan AMBOS años (100+200=300)");
}

// --- Denominador fijo: certificadas nunca se contamina con vendidas -------
{
  state.comercialParticipacionAnios = new Set(["2025", "2026"]);
  state.comercialStatus = { "En curso": true, "Finalizado": false };
  const totalCert = comercialParticipacionTotalCertificadas();
  assertEqual(totalCert, 270, "denominador de Certificadas es independiente del de Vendidas (270, no 300)");
  const pctCert = comercialPctCertificadas(270);
  assertEqual(pctCert, "100.00 %", "% Certificadas también resuelve no-cero bajo el mismo contexto");
}

if (failures > 0) {
  console.log(`${failures} participacion numerator/denominator invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL PARTICIPACION NUMERATOR/DENOMINATOR INVARIANTS PASSED");
process.exit(0);
