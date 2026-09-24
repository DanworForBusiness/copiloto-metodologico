// reporting_unavailable_state.node.js — invocado por
// test_reporting_empty_states.py::test_reporting_unavailable_state_invariants_hold.
//
// Owner increment ("consistent unavailable state"): el fallo de fuente de
// Reporting deja de emitir un `<p class="empty-panel">` suelto y pasa al
// primitivo compartido `.dashboard-empty` (mismo que cero-filas), distinto SOLO
// por el eyebrow — "No disponible" vs "Sin resultados". Suma un "Reintentar"
// ACOTADO que reusa la maquinaria de `reportingReauthenticate`, y que se oculta
// donde reintentar no ayuda (config incompleta) o donde ya tiene su propio
// camino (`auth_required`).
//
// Ejecuta las funciones REALES de reporting.js contra un sandbox mínimo y
// verifica sobre el DOM/estado producido lo que un test de archivo no puede:
// que el gate de retry responde al estado, que el retry reintenta sólo el tab
// activo, y que el camino de `auth_required` no cambió.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.resolve(__dirname, "..", "..", "webapp", "static");
const src = fs.readFileSync(path.join(STATIC, "reporting.js"), "utf-8");
const toolsCss = fs.readFileSync(path.join(STATIC, "tools.css"), "utf-8");
const reportingCss = fs.readFileSync(path.join(STATIC, "reporting.css"), "utf-8");
const appCss = fs.readFileSync(path.join(STATIC, "app.css"), "utf-8");

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(a, e, label) {
  if (a !== e) { failures++; console.log(`FAIL: ${label}\n  esperado: ${JSON.stringify(e)}\n  obtenido: ${JSON.stringify(a)}`); }
}

function makeEnv() {
  const triggered = [];
  let renders = 0;
  const mainEl = { innerHTML: "" };
  const sandbox = {
    window: {},
    document: {
      addEventListener() {},
      querySelector() { return null; },
      getElementById(id) {
        if (id === "reporting-main-content") return mainEl;
        return { innerHTML: "", classList: { add() {}, remove() {}, toggle() {} } };
      },
    },
    sessionStorage: { getItem() { return ""; } },
    dwGraphToken: async () => "",
    fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }); },
    HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
    initDashboardSections() {},
    setInterval() { return 0; }, clearInterval() {},
    console,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "reporting.js" });
  const get = (name) => vm.runInContext(name, sandbox);
  return { sandbox, get, mainEl, triggered, renders: () => renders };
}

// ── A: el helper emite el primitivo COMPARTIDO, eyebrow "No disponible" ──
{
  const { get } = makeEnv();
  const html = get("reportingSourceErrorHtml")("los datos de Cartera");
  assertTrue(html.includes('<div class="dashboard-empty">'),
    "A: usa `.dashboard-empty`, el primitivo compartido del Hub");
  assertTrue(html.includes('<span class="dashboard-empty__label">No disponible</span>'),
    "A: eyebrow 'No disponible' (no 'Sin resultados')");
  assertTrue(!html.includes("empty-panel"),
    "A: ya no queda el `<p class=\"empty-panel\">` local");
  assertTrue(html.includes("No pudimos cargar los datos de Cartera."),
    "A: mensaje = el hecho observable, con el label de la experiencia");
  // NEUTRO EN CAUSA — el cliente no puede saber por qué falló.
  ["autoriz", "permiso", "temporal", "en este momento", "siguen disponibles"].forEach(w =>
    assertTrue(!html.toLowerCase().includes(w), `A: no afirma '${w}'`));
}

// ── B: "Reintentar" aparece en una falla de fuente reintentable ─────────
{
  const { get } = makeEnv();
  const html = get("reportingSourceErrorHtml")("los datos de Cartera");
  assertTrue(html.includes('class="dashboard-subhead__reset"'),
    "B: el botón reusa el primitivo del botón de reauth");
  assertTrue(html.includes('onclick="reportingRetryTab()"'), "B: dispara el retry acotado");
  assertTrue(html.includes(">Reintentar<"), "B: copy 'Reintentar'");
}

// ── C: config incompleta NO ofrece "Reintentar" ────────────────────────
{
  const { get } = makeEnv();
  const state = get("state");
  // Se prende como lo haría el clasificador ante `{state:"config_incomplete"}`.
  assertEqual(get("reportingClassifyState")({ state: "config_incomplete" }), "error",
    "C: `config_incomplete` comparte el status visual 'error'…");
  assertEqual(state.reportingConfigIncomplete, true,
    "C: …pero el clasificador preserva la señal (como `graphAuthRequired`)");
  const html = get("reportingSourceErrorHtml")("los datos de Cartera");
  assertTrue(html.includes('<div class="dashboard-empty">'),
    "C: sigue mostrando la caja 'No disponible'");
  assertTrue(!html.includes("Reintentar"),
    "C: sin 'Reintentar' — reintentar no arregla un id sin configurar");
}

// ── D: `{ retry:false }` (fallas incrementales por año) sin botón ───────
{
  const { get } = makeEnv();
  const html = get("reportingSourceErrorHtml")("los datos de Cartera del año 2024", { retry: false });
  assertTrue(html.includes('<div class="dashboard-empty">'), "D: misma caja compartida");
  assertTrue(!html.includes("Reintentar"),
    "D: la falla por año no ofrece el retry de tab (tiene su propia maquinaria)");
}

// ── E: `reportingRetryTab` reintenta SÓLO el tab activo, sin reload ─────
{
  const { get, mainEl } = makeEnv();
  const state = get("state");
  state.experience = "comercial";
  // Comercial: 2 familias. Una falló, la otra ya cargó bien.
  state.comercialParticipacionDataStatus = "error";
  state.batchADataStatus = "ready";
  // Un tab que el usuario NO está mirando, también fallado: no debe tocarse.
  state.rentabilidadDataStatus = "error";
  state.reportingTabHydrated.comercial = false;

  get("reportingRetryTab")();

  // Vuelve a "idle" y el trigger idempotente lo re-dispara en el acto → "loading".
  assertEqual(state.comercialParticipacionDataStatus, "loading",
    "E: el dataset fallado del tab activo se resetea y su trigger lo re-dispara");
  assertEqual(state.batchADataStatus, "ready",
    "E: un dataset que YA cargó bien no se resetea");
  assertEqual(state.rentabilidadDataStatus, "error",
    "E: un tab que el usuario no está mirando no se toca");
  assertTrue(mainEl.innerHTML.length > 0, "E: re-renderiza el contenido");
  // El código no fuerza logout ni recarga.
  const body = src.slice(src.indexOf("function reportingRetryTab"));
  const fn = body.slice(0, body.indexOf("\n}") + 2);
  assertTrue(!/location\.(reload|href)/.test(fn) && !/[lL]ogout/.test(fn),
    "E: `reportingRetryTab` ni recarga ni cierra sesión");
}

// ── F: `auth_required` NO cambió — su camino gana antes de llegar acá ───
{
  const { get } = makeEnv();
  const state = get("state");
  assertEqual(get("reportingClassifyState")({ state: "auth_required" }), "auth_required",
    "F: el clasificador sigue distinguiendo `auth_required`");
  assertEqual(state.graphAuthRequired, true, "F: y prende el flag transversal");
  const gate = get("reportingAuthGateHtml")();
  assertTrue(gate.includes("Renovar sesión") && gate.includes("reportingReauthenticate()"),
    "F: el gate de auth conserva su acción propia ('Renovar sesión')");
  assertTrue(!gate.includes("Reintentar"),
    "F: y no es el retry de fuente");
}

// ── G: cero-filas conserva su eyebrow "Sin resultados" ─────────────────
{
  const { get } = makeEnv();
  const html = get("reportingEmptyStateHtml")("Sin proyectos para los filtros actuales.");
  assertTrue(html.includes('class="dashboard-empty__label">Sin resultados<'),
    "G: el estado de cero-filas sigue diciendo 'Sin resultados'");
  assertTrue(!html.includes("No disponible") && !html.includes("Reintentar"),
    "G: y no pide prestado el vocabulario ni la acción del error");
}

// ── H: cero CSS nuevo — sólo primitivos ya compartidos ─────────────────
{
  assertTrue(/\.dashboard-empty\s*\{/.test(toolsCss) && /\.dashboard-empty__label\s*\{/.test(toolsCss),
    "H: `.dashboard-empty` vive en tools.css (compartido)");
  assertTrue(/\.dashboard-subhead__reset\s*\{/.test(toolsCss),
    "H: `.dashboard-subhead__reset` (botón) vive en tools.css (compartido)");
  assertTrue(!/dashboard-empty/.test(reportingCss),
    "H: reporting.css NO redefine el primitivo compartido");
  ["reporting-unavailable", "reporting-retry", "reporting-error", "unavailable-panel"].forEach(c =>
    assertTrue(!reportingCss.includes(c) && !src.includes(c),
      `H: no se inventó un primitivo local ('${c}')`));
  // `.empty-panel` sigue vivo en app.css: lo usan Staffing / catálogo / novedades.
  assertTrue(/\.empty-panel\s*\{/.test(appCss),
    "H: `.empty-panel` sigue definido (otros consumidores lo usan)");
}

if (failures > 0) {
  console.log(`${failures} Reporting unavailable-state invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL REPORTING UNAVAILABLE STATE INVARIANTS PASSED");
process.exit(0);
