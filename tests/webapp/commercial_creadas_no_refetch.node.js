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
// El root NO puede ser un elemento nuevo por llamada: para probar que el
// loader del año frio esta ACOTADO al panel hay que observar QUE hosts se
// escribieron, y eso exige que sobrevivan entre renders.
const hosts = {};
const selectoresConsultados = [];
const rootEl = makeEl({});
rootEl.querySelector = (sel) => {
  selectoresConsultados.push(sel);
  if (!hosts[sel]) { hosts[sel] = makeEl({}); hosts[sel].textContent = ""; }
  return hosts[sel];
};
let fetchOverride = null;
const sandbox = {
  document: {
    addEventListener(type, fn) { (listeners[type] || (listeners[type] = [])).push(fn); },
    getElementById(id) { return id === "commercial-main-content" ? rootEl : null },
    querySelector() { return null },
    querySelectorAll() { return [] },
    body: { addEventListener() { }, classList: { add() { }, remove() { }, toggle() { } } },
  },
  window: { location: { pathname: "/comercial", search: "" }, addEventListener() { } },
  dwGraphToken: async () => "",
  fetch(url) {
    fetches.push(String(url));
    // `fetchOverride` deja colgar una request a proposito (GUARD B). Sin un
    // fetch controlable, "no espera al previo" no se puede DISTINGUIR de
    // "los dos resolvieron rapido" — el test pasaria por accidente.
    if (fetchOverride) {
      const forzado = fetchOverride(String(url));
      if (forzado) return forzado;
    }
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
const commercialLoadCreadas = vm.runInContext("commercialLoadCreadas", sandbox);
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
  // HOT WINDOW ya hidratada: el guard mide las interacciones POSTERIORES. Sin
  // esto el estado por año esta vacio y cualquier toque dispararia una carga
  // legitima, escondiendo lo que se quiere medir.
  commercialState.creadasByYear = {
    [String(CUR)]: { status: "ready", data: commercialState.creadas },
    [String(CUR - 1)]: { status: "ready", data: { months: [] } },
  };
  commercialState.creadasSelectableYears = [String(CUR), String(CUR - 1), String(CUR - 3)];
  commercialState.creadasAniosStatus = "ready";
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

  // ── GUARDS D y E — EL AÑO FRIO, la UNICA interaccion que hidrata ────────
  // Owner: primera seleccion de un año sin cache = EXACTAMENTE una hidratacion
  // acotada; segunda = CERO. Se mide aparte de la secuencia local justamente
  // porque el año NO es local: es el limite del contrato hot/cold.
  const antesDelFrio = creadasRequests();
  const frio = String(CUR - 3);
  const yearSel = makeEl({ commercialCreadasYear: "" });
  yearSel.value = frio;
  fire("change", yearSel);
  await drain();
  const trasPrimera = creadasRequests() - antesDelFrio;
  assertEqual(trasPrimera, 1,
    `GUARD D: la PRIMERA seleccion del año frio ${frio} produce EXACTAMENTE una hidratacion`);
  // Y acotada a ESE año: la request lleva su `anio`.
  const url = fetches[fetches.length - 1];
  assertTrue(url.indexOf("anio=" + frio) !== -1,
    `GUARD D: la request esta acotada al año pedido (${url})`);
  assertTrue(commercialState.creadasByYear[frio] !== undefined,
    "GUARD D: y queda registrada en el estado por año");

  // Segunda seleccion: el cache responde, cero red.
  commercialState.creadasByYear[frio] = { status: "ready", data: { months: [] } };
  const antesDeLaSegunda = creadasRequests();
  fire("change", yearSel);
  await drain();
  assertEqual(creadasRequests() - antesDeLaSegunda, 0,
    "GUARD E: la SEGUNDA seleccion del mismo año produce CERO requests");

  // ── EL AÑO CACHEADO TIENE QUE RENDERIZARSE, no solo "no pedirse" ───────
  // Regresion encontrada en el smoke del owner: elegir un año que YA estaba
  // en cache dejaba el panel VACIO. El cache-hit cortaba temprano sin
  // publicar el dataset, asi que `commercialState.creadas` seguia siendo el
  // del año anterior y la tabla —que filtra por prefijo de mes— no matcheaba
  // nada. Los años frios andaban porque pasaban por el fetch, que si adoptaba;
  // fallaban justo los de la VENTANA CALIENTE. Contar requests no alcanzaba
  // para verlo: cero requests era exactamente lo que el guard pedia.
  const datosA = { months: [{ month: `${CUR}-07`, count: 1, opportunities: [
    { id: 91, entry_date: `${CUR}-07-03`, name: "EnA", client: "CliA", seller: "Ana", origen: "BI", status: "Ganada", horas: 7 }] }] };
  const datosB = { months: [{ month: `${CUR - 1}-03`, count: 1, opportunities: [
    { id: 92, entry_date: `${CUR - 1}-03-05`, name: "EnB", client: "CliB", seller: "Bruno", origen: "NN", status: "Ganada", horas: 9 }] }] };
  commercialState.creadasByYear = {
    [String(CUR)]: { status: "ready", data: datosA },
    [String(CUR - 1)]: { status: "ready", data: datosB },
  };
  commercialState.creadasYear = String(CUR);
  commercialState.creadas = datosA;
  commercialState.creadasStatus = "ready";
  commercialState.creadasSeller = "";
  commercialState.creadasSearch = "";

  const selAB = makeEl({ commercialCreadasYear: "" });
  const antesDelSwitch = creadasRequests();
  selAB.value = String(CUR - 1);
  fire("change", selAB);
  await drain();
  assertEqual(creadasRequests() - antesDelSwitch, 0,
    "AÑO CACHEADO: cambiar a un año ya hidratado sigue sin pedir nada");
  assertTrue(commercialState.creadas === datosB,
    "AÑO CACHEADO: y PUBLICA el dataset de ESE año (no deja el anterior)");
  let cuerpo = hosts["[data-commercial-creadas-body]"].innerHTML;
  // El panel muestra ranking + tabla mes/semana; el NOMBRE de la oportunidad
  // vive en el detalle, que exige una seleccion. Se afirma sobre lo que el
  // panel realmente pinta: el rotulo del mes (lleva el año) y el vendedor.
  assertTrue(cuerpo.indexOf("de " + (CUR - 1)) !== -1,
    "AÑO CACHEADO: el panel renderiza los meses del año elegido");
  assertTrue(cuerpo.indexOf("de " + CUR) === -1,
    "AÑO CACHEADO: y ya no los del anterior");
  assertTrue(cuerpo.indexOf("Bruno") !== -1 && cuerpo.indexOf("Ana") === -1,
    "AÑO CACHEADO: el ranking tambien se rehizo sobre el año elegido");
  assertTrue(cuerpo.indexOf("Sin datos") === -1,
    "AÑO CACHEADO: no cae al estado vacio teniendo datos");

  // Y de vuelta: el primer año tiene que volver a aparecer. El owner reporto
  // exactamente esto — 2026 tenia datos, y despues de pasear por otros años
  // volvia vacio.
  selAB.value = String(CUR);
  fire("change", selAB);
  await drain();
  assertEqual(creadasRequests() - antesDelSwitch, 0,
    "AÑO CACHEADO: volver tampoco pide nada");
  assertTrue(commercialState.creadas === datosA,
    "AÑO CACHEADO: volver al año original lo REPUBLICA");
  cuerpo = hosts["[data-commercial-creadas-body]"].innerHTML;
  assertTrue(cuerpo.indexOf("de " + CUR) !== -1 && cuerpo.indexOf("de " + (CUR - 1)) === -1,
    "AÑO CACHEADO: y el panel vuelve a mostrar sus meses");
  assertTrue(cuerpo.indexOf("Ana") !== -1 && cuerpo.indexOf("Bruno") === -1,
    "AÑO CACHEADO: con su propio ranking");

  // GUARD C: tener el año en el `<select>` no implica estar hidratado.
  const noHidratado = String(CUR - 5);
  commercialState.creadasSelectableYears.push(noHidratado);
  assertTrue(commercialState.creadasByYear[noHidratado] === undefined,
    "GUARD C: una opcion del selector NO implica estado hidratado");

  // GUARD A: la apertura del drill nunca pide un año mas viejo que actual-1.
  const pedidos = fetches.filter(u => u.indexOf("/comercial/data/creadas?") !== -1)
    .map(u => (u.match(/anio=(\d+)/) || [])[1]).filter(Boolean).map(Number);
  assertTrue(pedidos.every(y => y >= CUR - 1 || y === CUR - 3),
    `GUARD A: solo se pidieron años hot o el frio elegido a mano (${JSON.stringify(pedidos)})`);

  // ── TIPO sigue con SU contrato (invalida y vuelve a pedir) ──────────────
  // El guard no dice "Creadas nunca pide nada": dice que las interacciones
  // LOCALES no piden. Tipo cambia el universo de negocio y su comportamiento
  // es intencionalmente distinto.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "");
  assertTrue(code.indexOf('creadasStatus = "idle"') !== -1,
    "TIPO: cambiar el universo devuelve Creadas a `idle` — su contrato de dataset sigue intacto");

  // ── GUARD B — ACTIVE-FIRST: el previo ni siquiera ARRANCA antes ────────
  // Contrato nuevo (owner performance gate). Antes los dos años salian
  // juntos; medido contra el Odoo real, el previo le costaba ~1.3s al activo
  // (~8.0s solo vs ~9.3s concurrente, 3 de 3 muestras pareadas, mismo signo
  // en las tres). Compiten por el servidor, no por el event loop. La ventana
  // caliente es trabajo OPCIONAL: no puede competir con el primer pintado.
  //
  // Por eso ya no alcanza con "el activo no espera al previo": hay que exigir
  // el ORDEN. Se cuelga el ACTIVO y se comprueba que mientras tanto no salio
  // ninguna request del previo.
  const previo = String(CUR - 1);
  commercialState.creadasByYear = {};
  commercialState.creadasStatus = "idle";
  commercialState.creadas = null;
  commercialState.creadasAniosStatus = "ready";
  let activoResolver = null;
  let previoResolver = null;
  fetchOverride = (u) => {
    if (u.indexOf("anio=" + CUR) !== -1) return new Promise((res) => { activoResolver = res; });
    if (u.indexOf("anio=" + previo) !== -1) return new Promise((res) => { previoResolver = res; });
    return null;
  };
  const aniosPedidos = () => fetches.filter(u => u.indexOf("/comercial/data/creadas?") !== -1)
    .map(u => (u.match(/anio=([0-9]+)/) || [])[1]);
  const antesDeAbrir = aniosPedidos().length;
  const abriendo = commercialLoadCreadas();       // SIN await: el activo cuelga
  await drain();
  const durante = aniosPedidos().slice(antesDeAbrir);
  assertTrue(durante.indexOf(String(CUR)) !== -1,
    "GUARD B: la apertura pide el año ACTIVO");
  assertEqual(durante.indexOf(previo), -1,
    `GUARD B: y NO arranca el previo mientras el activo sigue en vuelo (${JSON.stringify(durante)})`);

  // Resuelto el activo: pinta, y RECIEN ahi sale el previo.
  activoResolver({ json: () => Promise.resolve({ state: "ok", creadas: { months: [
    { month: `${CUR}-05`, count: 1, opportunities: [{ id: 77, entry_date: `${CUR}-05-02`,
      name: "Act", client: "C", seller: "Ana", origen: "BI", status: "Ganada", horas: 3 }] }] } }) });
  await abriendo;
  await drain();
  assertEqual(commercialState.creadasStatus, "ready",
    "GUARD B: el año activo queda usable");
  assertTrue(commercialState.creadas !== null,
    "GUARD B: y con dataset, no un loader");
  const despues = aniosPedidos().slice(antesDeAbrir);
  assertTrue(despues.indexOf(previo) !== -1,
    "GUARD B: el prefetch del previo arranca DESPUES — no se pierde, se pospone");
  assertEqual((commercialState.creadasByYear[previo] || {}).status, "loading",
    "GUARD B: y sigue en vuelo (si no, el resto del guard no probaria nada)");

  // Y cuando el previo llega tarde, NO pisa la vista del año activo.
  const activoAntes = commercialState.creadas;
  previoResolver({ json: () => Promise.resolve({ state: "ok", creadas: { months: [{ month: previo + "-01", count: 9, opportunities: [] }] } }) });
  await drain();
  fetchOverride = null;
  assertTrue(commercialState.creadas === activoAntes,
    "GUARD B: el previo que termina en background NO pisa el dataset activo");
  assertEqual((commercialState.creadasByYear[previo] || {}).status, "ready",
    "GUARD B: pero si queda tibio en cache para cuando lo elijan");

  // ── GUARD — EL LOADER DEL AÑO FRIO ESTA ACOTADO AL PANEL ───────────────
  // Owner: elegir un año frio muestra un estado de carga de CREADAS, no
  // devuelve todo el dashboard al loader inicial. La ventana caliente y el
  // resto de la pantalla no se re-hidratan por elegir un año.
  const otroFrio = String(CUR - 7);
  commercialState.creadasSelectableYears.push(otroFrio);
  let frioResolver = null;
  fetchOverride = (u) => (u.indexOf("anio=" + otroFrio) !== -1
    ? new Promise((res) => { frioResolver = res; })
    : null);
  selectoresConsultados.length = 0;
  const selFrio = makeEl({ commercialCreadasYear: "" });
  selFrio.value = otroFrio;
  fire("change", selFrio);
  await drain();
  const bodyHost = hosts["[data-commercial-creadas-body]"];
  assertTrue(!!bodyHost && bodyHost.innerHTML.indexOf("Cargando oportunidades creadas") !== -1,
    "AÑO FRIO: mientras hidrata, el panel de Creadas muestra SU estado de carga");
  // Acotado: los unicos hosts escritos son los de Creadas.
  const ajenos = selectoresConsultados.filter(sel => sel.indexOf("creadas") === -1);
  assertEqual(ajenos, [],
    "AÑO FRIO: el render no toca ningun host fuera de Creadas");
  frioResolver({ json: () => Promise.resolve({ state: "ok", creadas: { months: [] } }) });
  await drain();
  fetchOverride = null;
  assertEqual(commercialState.creadasStatus, "ready",
    "AÑO FRIO: al llegar, el panel sale del estado de carga");
  assertTrue(bodyHost.innerHTML.indexOf("Cargando oportunidades creadas") === -1,
    "AÑO FRIO: y el loader se retira");


  assertEqual(handlerErrors, [], "ningun handler lanzo durante la secuencia");
  if (failures > 0) {
    console.log(`${failures} creadas no-refetch invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL CREADAS NO-REFETCH INVARIANTS PASSED");
  process.exit(0);
})();
