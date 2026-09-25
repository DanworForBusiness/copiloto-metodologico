// commercial_table_action_icons.node.js — invocado por
// test_commercial_table_action_icons.py.
//
// Owner "Iconographic Table Actions" (ver HANDOFF): las tres acciones de fila
// de Licencias ("Ver detalle de licencias", "Mover a sin prioridad",
// "Restaurar") pasan de texto a ícono compacto + tooltip + `aria-label`. El
// contrato de CLICK (dispara la acción correcta, no dispara la fila, sin
// `onclick` inline reintroducido) ya está cubierto —y sigue en verde con este
// markup— por commercial_licensing_action_wiring.node.js; acá se prueba
// específicamente lo NUEVO: el ícono, el nombre accesible, el tooltip visible
// y que el primitivo sea genuinamente reusable (no atado a Comercial).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.resolve(__dirname, "..", "..", "webapp", "static");
const commercialJs = fs.readFileSync(path.join(STATIC, "commercial.js"), "utf-8");
const toolsCss = fs.readFileSync(path.join(STATIC, "tools.css"), "utf-8");
const commercialCss = fs.readFileSync(path.join(STATIC, "commercial.css"), "utf-8");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, e, label) {
  if (JSON.stringify(a) !== JSON.stringify(e)) {
    failures++; console.log(`FAIL: ${label} — esperado ${JSON.stringify(e)}, obtenido ${JSON.stringify(a)}`);
  }
}

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; } },
  window: {},
  fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {}, initEventBreakdown() {}, initDraggableColumns() {}, initResizableColumns() {},
  readStoredColumnOrder() { return null; },
  readStoredColumnWidths() { return null; },
  console,
};
vm.createContext(sandbox);
vm.runInContext(commercialJs, sandbox, { filename: "commercial.js" });
const { commercialLicenciasCompactRowHtml, commercialLicenciasTableHtml, commercialLicensingProfileRowHtml } = sandbox;
const commercialTableActionButtonHtml = vm.runInContext("commercialTableActionButtonHtml", sandbox);

const OPP = { id: 1, opportunity: "Opp Uno", client: "Cuenta Uno", account_id: 900, seller: "Vendedor Uno",
              stage: "1 Contacto", tags: [], revenue: 5000 };
const OPP_SIN_CUENTA = { ...OPP, id: 2, account_id: null };
const ACCOUNT = { id: 900, name: "Cuenta Uno", partner_de_licencia: "05 - SOFTTEK", ae_sap: "AE Uno",
                  arquitectura_actual: [], monto_contrato: 1000, vencimiento_contrato: "2027-01-01",
                  tipo_contrato_sap: "Contrato Individual", segmento_cuenta: "Corporate", seller: "Vendedor Uno" };

function extractButton(html, marker) {
  const i = html.indexOf(marker);
  if (i === -1) return null;
  const start = html.lastIndexOf("<button", i);
  const end = html.indexOf("</button>", i) + "</button>".length;
  return html.slice(start, end);
}

// ── A/M: "Ver detalle de licencias" es un <button> real con ícono SVG ──────
{
  const html = commercialLicenciasCompactRowHtml(OPP);
  const btn = extractButton(html, "Ver detalle de licencias");
  ok(btn !== null, "A: el botón se ubica en el HTML renderizado");
  ok(btn.startsWith("<button"), "M: es un <button> real, no un span/div decorativo");
  ok(btn.includes("<svg"), "A: renderiza un ícono SVG en vez de texto plano visible");
  // El texto SÍ aparece dentro de `.chart-tooltip` (a propósito, ver C) —
  // acá se confirma que no queda TAMBIÉN como texto visible DIRECTO del
  // botón: entre el cierre del ícono y el `<span>` del tooltip no puede
  // haber texto suelto.
  ok(/<\/svg><span class="chart-tooltip"/.test(btn),
     "A: 'Ver detalle de licencias' no queda como texto visible directo del botón (el ícono cierra justo antes del tooltip oculto)");
}

// ── B: accessible label exactamente correcto ───────────────────────────────
{
  const html = commercialLicenciasCompactRowHtml(OPP);
  ok(html.includes('aria-label="Ver detalle de licencias"'),
     "B: aria-label exactamente \"Ver detalle de licencias\"");
}

// ── C: el texto semántico sigue disponible como tooltip visible ───────────
{
  const html = commercialLicenciasCompactRowHtml(OPP);
  const btn = extractButton(html, "Ver detalle de licencias");
  ok(btn.includes('<span class="chart-tooltip"') && btn.includes("<p>Ver detalle de licencias</p>"),
     "C: el tooltip visible (.chart-tooltip) contiene el texto de negocio exacto");
}

// ── deshabilitado: explica la razón, no repite la acción ───────────────────
{
  const html = commercialLicenciasCompactRowHtml(OPP_SIN_CUENTA);
  const btn = extractButton(html, "table-action-btn");
  ok(/\bdisabled\b/.test(btn), "deshabilitado: sigue quedando disabled sin cuenta asociada");
  ok(btn.includes('aria-label="Esta oportunidad no tiene una cuenta comercial asociada"'),
     "deshabilitado: aria-label explica la razón específica");
}

// ── F/G: "Mover a sin prioridad" (Perfil, main) ────────────────────────────
{
  const html = commercialLicensingProfileRowHtml(ACCOUNT, "main");
  const btn = extractButton(html, "Mover a sin prioridad");
  ok(btn && btn.startsWith("<button") && btn.includes("<svg"),
     "F: 'Mover a sin prioridad' es un botón ícono real");
  ok(html.includes('aria-label="Mover a sin prioridad"'), "G: aria-label exacto");
  ok(btn.includes("<p>Mover a sin prioridad</p>"), "F: tooltip visible con el texto exacto");
}

// ── I/J: "Restaurar" (Perfil, secondary) ───────────────────────────────────
{
  const html = commercialLicensingProfileRowHtml(ACCOUNT, "secondary");
  const btn = extractButton(html, "Restaurar");
  ok(btn && btn.startsWith("<button") && btn.includes("<svg"),
     "I: 'Restaurar' es un botón ícono real");
  ok(html.includes('aria-label="Restaurar"'), "J: aria-label exacto");
  ok(btn.includes("<p>Restaurar</p>"), "I: tooltip visible con el texto exacto");
}

// ── modo "detail": sin acción (contrato de Perfil sin tocar) ───────────────
{
  const html = commercialLicensingProfileRowHtml(ACCOUNT, "detail");
  ok(html.includes('class="dashboard-table-muted">—<'),
     "el modo detail sigue sin acción de prioridad — no se convirtió en ícono deshabilitado");
}

// ── N: la columna de acción es compacta (ancho fijo, no basado en texto) ──
{
  const htmlOpp = commercialLicenciasTableHtml([OPP], { sortKey: "", sortDir: 1, emptyLabel: "" });
  const colgroupOpp = htmlOpp.match(/<colgroup>(.*?)<\/colgroup>/s)[1];
  ok(/<col style="width:48px">\s*$/.test(colgroupOpp.trim()),
     "N (Oportunidades): la última columna (acción) tiene un ancho FIJO y angosto, no relativo al texto que ya no lleva");
}

// ── O: el primitivo es genuinamente reusable, no atado a Comercial ────────
{
  // 1) La función helper no conoce nada de licencias/cuentas/oportunidades —
  //    sólo icon/label/dataAttributes/disabled.
  // OJO: en JS (a diferencia de Python) `.split(sep, limit)` RECORTA el
  // array resultante a `limit` elementos y DESCARTA el resto del string —
  // no es "partir sólo una vez". Por eso acá se usa `.split(sep)` SIN límite
  // (el separador es un nombre de función completo, aparece una sola vez).
  const helperSrc = commercialJs.split("function commercialTableActionButtonHtml(")[1].split("\n}")[0];
  ["licens", "oportunidad", "cuenta", "prioridad", "defer", "restore"].forEach(banned => {
    ok(!helperSrc.toLowerCase().includes(banned),
       `O: el helper genérico no menciona vocabulario de negocio ('${banned}')`);
  });
  // 2) Se puede invocar con datos arbitrarios, sin relación con Licencias.
  const generic = commercialTableActionButtonHtml({
    icon: '<svg aria-hidden="true"><circle/></svg>',
    label: "Acción genérica de prueba",
    dataAttributes: 'data-un-atributo-cualquiera="42"',
  });
  ok(generic.includes('aria-label="Acción genérica de prueba"') && generic.includes('data-un-atributo-cualquiera="42"'),
     "O: el helper funciona con CUALQUIER icon/label/dataAttributes, no sólo los de Licencias");
  // 3) El CSS que lo viste vive en tools.css (compartido), no en commercial.css.
  ok(/\.table-action-btn\s*\{/.test(toolsCss), "O: `.table-action-btn` vive en tools.css (Hub-compartido)");
  ok(!/\.table-action-btn\s*\{/.test(commercialCss), "O: y NO se redeclara en commercial.css");
  ok(/\.chart-tooltip-anchor\s*\{/.test(toolsCss), "O: reusa el tooltip YA compartido, no uno nuevo");
}

// ── L (reforzado a nivel de archivo completo): sin onclick inline nuevo ────
{
  const sinComentarios = commercialJs.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  ok(!sinComentarios.includes("onclick="), "L: cero onclick inline en todo commercial.js, ícono incluido");
}

if (failures > 0) {
  console.log(`${failures} Commercial table action icon invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL COMMERCIAL TABLE ACTION ICON INVARIANTS PASSED");
process.exit(0);
