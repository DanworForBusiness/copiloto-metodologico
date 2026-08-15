// reporting_rentabilidad_ranking.node.js — invocado por
// test_reporting_rentabilidad.py::test_rentabilidad_ranking_top5_invariants_hold
// (mismo patrón que reporting_rollup_reconciliation.node.js): carga
// reporting.js en un sandbox mínimo y ejecuta el PRIMITIVO REAL
// (`rentabilidadRankingList`/`toggleRentabilidadRanking`) — owner feedback:
// "Top/Bottom rankings should show TOP 5 by default... provide a
// lightweight affordance to reveal more, but do not clutter the default
// view".
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

// `toggleRentabilidadRanking` llama a `render()`, que toca
// `document.getElementById`/`initDashboardSections` (definido en app.js,
// NO cargado en este sandbox mínimo) — se neutraliza a un no-op DESPUÉS de
// evaluar el script (la llamada interna a `render()` resuelve contra el
// binding global actualizado). El harness prueba el ESTADO/HTML que
// devuelven los primitivos puros, no el DOM real (eso lo cubre el resto de
// la suite vía source-seam).
sandbox.render = () => {};

// `state` es `const` a nivel de módulo en reporting.js — un `const`/`let`
// de top-level NO se adjunta como propiedad del objeto de contexto (a
// diferencia de `function`/`var`), así que `sandbox.state` sería
// `undefined`. Se obtiene la referencia REAL evaluando la expresión
// `state` en el MISMO contexto (mismo realm — el objeto no se clona, es
// la misma referencia que usan internamente `toggleRentabilidadRanking`/
// `rentabilidadRankingList`).
const state = vm.runInContext("state", sandbox);
const { rentabilidadRankingList, toggleRentabilidadRanking } = sandbox;

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}
function countBarRows(html) {
  return (html.match(/class="bar-row"/g) || []).length;
}

const sevenItems = Array.from({ length: 7 }, (_, i) => ({ proyecto: `Proyecto ${i + 1}`, rentabilidad: 100 - i }));
const threeItems = Array.from({ length: 3 }, (_, i) => ({ proyecto: `Proyecto ${i + 1}`, rentabilidad: 100 - i }));

// --- Top 5 por default, con 7 items disponibles. ---
{
  const html = rentabilidadRankingList(sevenItems, "positivos", 100);
  assertTrue(countBarRows(html) === 5, "con 7 proyectos, el default muestra exactamente 5 (Top 5)");
  assertTrue(html.includes("+ Ver 2 más"), "la afordancia muestra cuántos quedan ocultos");
  assertTrue(!html.includes("Proyecto 6") && !html.includes("Proyecto 7"), "los proyectos fuera del Top 5 no están en el HTML todavía (no solo ocultos por CSS)");
}

// --- Sin afordancia cuando hay <=5 items (no clutter). ---
{
  const html = rentabilidadRankingList(threeItems, "positivos", 100);
  assertTrue(countBarRows(html) === 3, "con 3 proyectos, se muestran los 3");
  assertTrue(!html.includes("dashboard-action-link"), "con <=5 proyectos no aparece ninguna afordancia de 'ver más'");
}

// --- Toggle revela el resto, y se puede volver a colapsar. ---
{
  state.rentabilidadRankingExpanded.positivos = false;
  toggleRentabilidadRanking("positivos");
  assertTrue(state.rentabilidadRankingExpanded.positivos === true, "toggle activa el estado expandido");
  const expandedHtml = rentabilidadRankingList(sevenItems, "positivos", 100);
  assertTrue(countBarRows(expandedHtml) === 7, "expandido, muestra los 7 proyectos completos");
  assertTrue(expandedHtml.includes("Ver menos"), "expandido, la afordancia ofrece colapsar de nuevo");

  toggleRentabilidadRanking("positivos");
  assertTrue(state.rentabilidadRankingExpanded.positivos === false, "un segundo toggle vuelve a colapsar");
}

// --- Mejor/Peor tienen estado independiente (expandir uno no afecta al otro). ---
{
  state.rentabilidadRankingExpanded.positivos = false;
  state.rentabilidadRankingExpanded.negativos = false;
  toggleRentabilidadRanking("positivos");
  assertTrue(state.rentabilidadRankingExpanded.positivos === true, "positivos expandido");
  assertTrue(state.rentabilidadRankingExpanded.negativos === false, "negativos NO se ve afectado por el toggle de positivos");
}

// --- La escala de barras (maxAbs) viene del ranking COMBINADO, no
// recalculada por lista (mismo ancho relativo en ambos paneles). ---
{
  const html = rentabilidadRankingList(threeItems, "positivos", 1000);
  const width = html.match(/width:([\d.]+)%/);
  assertTrue(!!width, "la barra expone su ancho relativo");
  assertTrue(Math.abs(parseFloat(width[1]) - 10) < 0.01, "con maxAbs=1000 y rentabilidad=100, el ancho es 10% (nunca recalculado contra un máximo propio de la lista)");
}

if (failures > 0) {
  console.log(`${failures} ranking invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL RENTABILIDAD RANKING INVARIANTS PASSED");
process.exit(0);
