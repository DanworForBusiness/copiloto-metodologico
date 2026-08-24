// commercial_creadas_no_refetch.node.js — invocado por
// test_commercial_creadas_drill.py::test_creadas_never_refetches_on_local_interaction.
// PERFORMANCE GUARD del owner UX corrective:
//
//   "After first successful Creadas lazy load: year changes, seller changes,
//    search typing, month expand/collapse, month detail selection, week detail
//    selection, detail close must perform ZERO additional
//    /comercial/data/creadas requests."
//
// Por que un harness y no un test de archivo: lo que hay que probar no es que
// el codigo "no llame a fetch" en algun lugar —eso es facil de leer y facil de
// romper sin que se note— sino que la SECUENCIA REAL de interacciones no
// produzca ni una request. Se ejecutan los listeners REALES de commercial.js
// contra un DOM y un `fetch` instrumentados, y se cuenta.
//
// Tipo (Todos/NN/BI) queda AFUERA a proposito: cambia el universo de negocio y
// su contrato de dataset es intencionalmente distinto (invalida y vuelve a
// pedir). Eso se verifica al final, en positivo, para que el guard no se lea
// como "Creadas nunca pide nada".
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

let failures = 0;
function assertEqual(a, e, label) {
  if (JSON.stringify(a) !== JSON.stringify(e)) {
    failures++; console.log(`FAIL: ${label} — esperado ${JSON.stringify(e)}, obtenido ${JSON.stringify(a)}`);
  }
}
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

// ── DOM minimo pero con listeners REALES ──────────────────────────────────
// `document.addEventListener` se captura de verdad: los handlers de Creadas se
// registran al cargar commercial.js y despues se disparan a mano con eventos
// sinteticos, igual que un click del usuario.
const listeners = {};
const fetches = [];
function makeEl(attrs) {
  const el = {
    dataset: {}, value: "", selectionStart: 0, innerHTML: "",
    classList: { add() { }, remove() { }, toggle() { }, contains() { return false } },
    focus() { }, setSelectionRange() { },
    querySelector() { return null }, querySelectorAll() { return [] },
    getAttribute() { return null }, setAttribute() { }, hasAttribute() { return false },
    scrollIntoView() { },
  };
  Object.assign(el.dataset, attrs || {});
  // `closest` busca por el atributo que el handler consulta: se resuelve contra
  // el propio dataset del elemento, que es lo que un click real produciria.
  el.closest = (sel) => {
    const m = sel.match(/^\[([a-z-]+)/);
    if (!m) return null;
    const key = m[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase()).replace(/^data/, "");
    const camel = key.charAt(0).toLowerCase() + key.slice(1);
    return camel in el.dataset ? el : null;
  };
  return el;
}
const sandbox = {
  document: {
    addEventListener(type, fn) { (listeners[type] || (listeners[type] = [])).push(fn); },
    getElementById(id) { return id === "commercial-main-content" ? makeEl({}) : null },
    querySelector() { return null },
    querySelectorAll() { return [] },
    body: { addEventListener() { }, classList: { add() { }, remove() { }, toggle() { } } },
  },
  window: { location: { pathname: "/comercial", search: "" }, addEventListener() { } },
  dwGraphToken: async () => "",
  fetch(url) {
    fetches.push(String(url));
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok", creadas: { months: [] } }) });
  },
  HubForegroundProgress: { begin() { }, complete() { }, fail() { }, settle() { } },
  initDashboardSections() { }, initEventBreakdown() { },
  setInterval() { return 0 }, clearInterval() { }, setTimeout, Promise, Object, JSON, Date, Error,
  URLSearchParams, console,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const commercialState = vm.runInContext("commercialState", sandbox);
const CUR = new Date().getFullYear();

// Payload ya cargado: el guard arranca DESPUES de la primera hidratacion.
function seed() {
  commercialState.creadas = { months: [
    { month: `${CUR}-07`, count: 2, opportunities: [
      { id: 1, entry_date: `${CUR}-07-03`, name: "Alfa", client: "Loma", seller: "Ana", origen: "BI", status: "1 - Inicial", horas: 10 },
      { id: 2, entry_date: `${CUR}-07-19`, name: "Beta", client: "Danone", seller: "Bruno", origen: "NN", status: "Ganada", horas: 20 },
    ] },
    { month: `${CUR}-06`, count: 1, opportunities: [
      { id: 3, entry_date: `${CUR}-06-05`, name: "Gamma", client: "Ternium", seller: "Ana", origen: "BI", status: "Perdida", horas: 5 },
    ] },
  ] };
  commercialState.creadasStatus = "ready";
  commercialState.creadasYear = String(CUR);
  commercialState.creadasExpandedMonths = [];
  commercialState.creadasSelection = null;
  commercialState.creadasSeller = "";
  commercialState.creadasSearch = "";
  commercialState.creadasSortKey = null;
  commercialState.creadasSortDir = 1;
}

// Dispara un evento sobre los listeners REALES registrados por commercial.js.
const handlerErrors = [];
function fire(type, el) {
  (listeners[type] || []).forEach((fn, i) => {
    try { fn({ target: el, key: el.key, detail: {}, stopPropagation() { }, preventDefault() { } }); }
    catch (e) { handlerErrors.push(`${type}#${i}: ${String(e).slice(0, 80)}`); }
  });
}
const drain = async () => { for (let i = 0; i < 8; i += 1) await new Promise(r => setImmediate(r)); };
const creadasRequests = () => fetches.filter(u => u.indexOf("/comercial/data/creadas") !== -1).length;

(async function main() {
  vm.runInContext("commercialLoadData = function () {}; "
    + "commercialLoadActivities = function () {}; "
    + "commercialRenderCapability = function () {};", sandbox);
  fire("DOMContentLoaded", makeEl({}));
  await drain();
  const registrados = Object.keys(listeners);
  assertTrue(registrados.indexOf("click") !== -1 && registrados.indexOf("change") !== -1
    && registrados.indexOf("input") !== -1,
    "SETUP: los listeners reales quedaron registrados (sin esto el guard es un verde falso)");

  seed();
  fetches.length = 0;

  // ── LA SECUENCIA COMPLETA que el owner enumero ──────────────────────────
  const pasos = [];
  const check = async (label) => { await drain(); pasos.push([label, creadasRequests()]); };

  // 1. cambio de AÑO
  const yearSel = makeEl({ commercialCreadasYear: "" });
  yearSel.value = String(CUR - 1);
  fire("change", yearSel);
  await check("cambio de Año");

  // 2. cambio de VENDEDOR
  const sellerSel = makeEl({ commercialCreadasSeller: "" });
  sellerSel.value = "Ana";
  fire("change", sellerSel);
  await check("cambio de Vendedor");

  // 3. TIPEAR en la busqueda — tecla por tecla, que es el caso peor
  const search = makeEl({ commercialCreadasSearch: "" });
  for (const q of ["l", "lo", "lom", "loma"]) {
    search.value = q;
    fire("input", search);
  }
  await check("tipear 4 teclas en Buscar");

  // 4. EXPANDIR y COLAPSAR un mes
  const toggle = makeEl({ commercialCreadasToggle: `${CUR}-07` });
  fire("click", toggle);
  await check("expandir mes");
  fire("click", toggle);
  await check("colapsar mes");

  // 5. seleccionar el DETALLE DE MES
  const monthRow = makeEl({ commercialCreadasMonth: `${CUR}-07` });
  fire("click", monthRow);
  await check("seleccionar detalle de mes");

  // 6. seleccionar el DETALLE DE SEMANA
  const weekRow = makeEl({ commercialCreadasWeek: `${CUR}-07:1` });
  fire("click", weekRow);
  await check("seleccionar detalle de semana");

  // 7. CERRAR el detalle
  const close = makeEl({ commercialCreadasDetailClose: "" });
  fire("click", close);
  await check("cerrar el detalle");

  // 8. ORDENAR por una columna (el agregado del owner)
  const sortTh = makeEl({ commercialCreadasSort: "client" });
  fire("click", sortTh);
  fire("click", sortTh);
  await check("ordenar por columna (dos clicks)");

  // 9. CLICK EN EL RANKING — la puerta nueva del owner corrective. Filtra en
  //    cascada sobre el payload ya cargado, igual que el dropdown.
  const rankRow = makeEl({ commercialCreadasSellerPick: "Ana" });
  fire("click", rankRow);
  await check("click en un vendedor del ranking");
  // 10. Y por TECLADO, que es el otro camino a la misma accion.
  fire("keydown", Object.assign(rankRow, { key: "Enter" }));
  await check("Enter sobre un vendedor del ranking");

  // 11. LOS DOS ✕ DE CONTEXTO — se prueban acá y no en el harness de render
  //     porque lo que importa es su ALCANCE, y eso vive en el HANDLER: llamar a
  //     la función de negocio directamente no puede ver si el handler hace de
  //     más. (Un mutation test de "el ✕ del vendedor también limpia Buscar"
  //     pasaba sin que nadie lo notara, justamente por eso.)
  commercialState.creadasSeller = "Ana";
  commercialState.creadasSearch = "beta";
  commercialState.creadasExpandedMonths = [`${CUR}-07`];
  commercialState.creadasSelection = { month: `${CUR}-07`, week: 1 };
  const yearAntes = commercialState.creadasYear;

  fire("click", makeEl({ commercialCreadasSellerClear: "" }));
  await check("✕ del contexto de Vendedor");
  assertEqual(commercialState.creadasSeller, "", "✕ Vendedor: limpia el vendedor");
  assertEqual(commercialState.creadasSearch, "beta", "✕ Vendedor: PRESERVA Buscar");
  assertEqual(commercialState.creadasYear, yearAntes, "✕ Vendedor: PRESERVA el Año");
  assertEqual(commercialState.creadasExpandedMonths, [`${CUR}-07`],
    "✕ Vendedor: PRESERVA los meses expandidos");

  commercialState.creadasSeller = "Ana";
  commercialState.creadasSelection = { month: `${CUR}-07`, week: 1 };
  fire("click", makeEl({ commercialCreadasDetailClose: "" }));
  await check("✕ del contexto de Mes/Semana");
  assertEqual(commercialState.creadasSelection, null, "✕ Mes: cierra la selección");
  assertEqual(commercialState.creadasSeller, "Ana", "✕ Mes: PRESERVA el Vendedor");
  assertEqual(commercialState.creadasSearch, "beta", "✕ Mes: PRESERVA Buscar");
  assertEqual(commercialState.creadasExpandedMonths, [`${CUR}-07`],
    "✕ Mes: NO colapsa ningún mes expandido");

  // 12. LIMPIAR — su alcance más amplio, ya aprobado, sigue igual.
  const clear = makeEl({ commercialCreadasClear: "" });
  fire("click", clear);
  await check("Limpiar");

  // ── EL GUARD: cero requests en TODA la secuencia ────────────────────────
  pasos.forEach(([label, n]) => {
    assertEqual(n, 0, `CERO requests a /comercial/data/creadas tras: ${label}`);
  });
  assertEqual(creadasRequests(), 0,
    "GUARD: la secuencia COMPLETA de interacciones locales no produjo ni una request");
  // Y tampoco a NINGUN otro endpoint: una interaccion local no puede disparar
  // la carga de otra capability de rebote.
  assertEqual(fetches.length, 0,
    "GUARD: cero requests de cualquier tipo — ni de Creadas ni de otra capability");

  // ── EN POSITIVO: el estado quedo donde tiene que quedar ─────────────────
  // Si el guard pasara porque los handlers no hicieron NADA, esto lo delata.
  assertEqual(commercialState.creadasSortKey, "client", "el orden se aplico (los handlers corrieron)");
  assertEqual(commercialState.creadasSortDir, -1, "y el segundo click invirtio");
  assertEqual(commercialState.creadasSeller, "",
    "Limpiar reseteo el vendedor que el ranking habia aplicado");
  assertEqual(commercialState.creadasSearch, "", "Limpiar reseteo la busqueda");
  assertEqual(commercialState.creadasSelection, null, "el detalle quedo cerrado");
  // La expansion la siembran los pasos de los dos ✕ (que verifican, justamente,
  // que NO la tocan), asi que al final del recorrido el mes sigue abierto — y
  // Limpiar tampoco lo colapsa: su alcance son los FILTROS, no la expansion.
  assertEqual(commercialState.creadasExpandedMonths, [`${CUR}-07`],
    "Limpiar no colapsa la expansion — su alcance son los filtros");
  assertTrue(commercialState.creadas !== null, "y el dataset NUNCA se invalido");

  // ── TIPO sigue con SU contrato (invalida y vuelve a pedir) ──────────────
  // El guard no dice "Creadas nunca pide nada": dice que las interacciones
  // LOCALES no piden. Tipo cambia el universo de negocio y su comportamiento
  // es intencionalmente distinto.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "");
  assertTrue(code.indexOf('creadasStatus = "idle"') !== -1,
    "TIPO: cambiar el universo devuelve Creadas a `idle` — su contrato de dataset sigue intacto");

  assertEqual(handlerErrors, [], "ningun handler lanzo durante la secuencia");
  if (failures > 0) {
    console.log(`${failures} creadas no-refetch invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL CREADAS NO-REFETCH INVARIANTS PASSED");
  process.exit(0);
})();
