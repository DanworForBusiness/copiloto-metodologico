// commercial_licensing_profile.node.js — invocado por
// test_commercial_licensing_profile_frontend.py.
//
// Account Licensing Profile (ver HANDOFF — Commercial Licensing): la
// arquitectura de dos contenedores dentro de Licencias (A. Oportunidades de
// licenciamiento / B. Perfil de licenciamiento por cliente), su lazy load
// INDEPENDIENTE, la reactividad de LAS DOS a Todos/New Name/Base Instalada,
// el drilldown BIDIRECCIONAL por cuenta (id canónico, nunca nombre), el
// conteo visible (siempre el arreglo YA filtrado, nunca el dataset crudo),
// el filtro explícito "Licenciamiento" (reemplaza el viejo hardcode
// `licencias === "Si"`) y el Perfil SIEMPRE acotado a las cuentas de las
// oportunidades visibles del grupo A (owner corrective, ver HANDOFF: el
// Alcance seleccionable se retiró — "Todas las cuentas" ya no existe, el
// Perfil nunca muestra una cuenta sin al menos una oportunidad visible).
//
// Ejecuta el MOTOR REAL de commercial.js contra fixtures y un `document`
// mínimo — MISMA técnica que el resto de los harnesses de Comercial.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COMMERCIAL_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js");
const src = fs.readFileSync(COMMERCIAL_JS, "utf-8");

const hosts = Object.create(null);
const root = {
  innerHTML: "x",
  querySelector(sel) { return hosts[sel] || null; },
};
const listeners = Object.create(null);
const fetchCalls = [];
let fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });

const sandbox = {
  document: {
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    getElementById(id) { return id === "commercial-main-content" ? root : null; },
    querySelector() { throw new Error("commercial.js nunca debe usar document.querySelector"); },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch(url) { fetchCalls.push(url); return fetchImpl(url); },
  HubForegroundProgress: { begin() {}, complete() {}, fail() {} },
  initDashboardSections() {},
  initEventBreakdown() {},
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialLicenciasHtml, commercialLicensingProfileFilteredRows, commercialLicensingProfileAllRows,
  commercialSetTipo, commercialSetCapability, commercialLoadLicensingProfile, commercialLoadOpportunitiesDetail,
  commercialDetalleTableHtml, commercialDetalleAllRows, commercialLicenciasTableHtml, commercialLicenciasAllRows,
  commercialLicenciasFilteredRows, commercialLicenciasVisibleAccountIds, commercialLicenciasFiltersHtml,
  commercialLicensingProfileClearFilters, commercialLicenciasClearFilters, commercialLicensingProfileFiltersHtml,
  commercialLicensingCloseDrilldown, commercialLicensingProfileTableHtml,
  commercialLicenciasCountLabel, commercialLicensingProfileCountLabel,
} = sandbox;
const commercialState = vm.runInContext("commercialState", sandbox);

let failures = 0;
function assertTrue(cond, label) {
  if (!cond) { failures++; console.log(`FAIL: ${label}`); }
}
const count = (html, needle) => (html.match(new RegExp(needle, "g")) || []).length;

// `client` (Oportunidad) y `name` (Perfil) comparten el MISMO texto por
// default a propósito — en datos reales ambos vienen del mismo
// `res.partner.name` para el mismo `account_id`.
const OPP_ROW = (over) => ({
  opportunity: "Opp Uno", client: "Cuenta Uno", account_id: 900, seller: "Vendedor Uno",
  stage: "1 Contacto", tags: [], licencias: "Si", hours: 100, weighted_hours: 40,
  revenue: 5000, probability: 40, priority: "0", activities: [], last_response: null, days_elapsed: null,
  ...over,
});
const PROFILE_ACCOUNT = (over) => ({
  id: 900, name: "Cuenta Uno", partner_de_licencia: "05 - SOFTTEK", ae_sap: "AE Uno",
  arquitectura_actual: ["SAC"], monto_contrato: 1000, currency: "USD",
  vencimiento_contrato: "2027-01-01", tipo_contrato_sap: "Contrato Individual",
  segmento_cuenta: "Corporate", seller: "Vendedor Uno",
  ...over,
});

function resetState(patch) {
  commercialState.tipo = "todos";
  commercialState.capability = "dashboard";
  commercialState.opportunitiesDetail = null;
  commercialState.opportunitiesDetailStatus = "idle";
  commercialState.opportunitiesDetailErrorState = "";
  commercialState.licSearchText = ""; commercialState.licStageFilter = ""; commercialState.licSellerFilter = "";
  commercialState.licLicenciamientoFilter = "si";
  commercialState.licensingProfile = null;
  commercialState.licensingProfileStatus = "idle";
  commercialState.licensingProfileErrorState = "";
  commercialState.licProfileSearchText = ""; commercialState.licProfileSegmentoFilter = "";
  commercialState.licProfilePartnerFilter = ""; commercialState.licProfileAeSapFilter = "";
  commercialState.licProfileTipoContratoFilter = ""; commercialState.licProfileSellerFilter = "";
  commercialState.licProfileVencimientoFilter = "";
  commercialState.licensingDrilldownAccountId = null; commercialState.licensingDrilldownOrigin = null;
  commercialState.detSearchText = ""; commercialState.detStageFilter = ""; commercialState.detSellerFilter = "";
  Object.assign(commercialState, patch || {});
}

// --- Contenedor: DOS <details> independientes, Table A preservada --------
{
  resetState({
    opportunitiesDetailStatus: "ready", opportunitiesDetail: { opportunities: [OPP_ROW()], total: 1 },
    licensingProfileStatus: "ready", licensingProfile: { accounts: [PROFILE_ACCOUNT()], total: 1 },
  });
  const html = commercialLicenciasHtml();
  assertTrue(count(html, 'class="dashboard-section commercial-licensing-section"') === 2,
    "dos <details> independientes");
  assertTrue(count(html, "<details[^>]* open>") === 2, "ambos abiertos por default");
  assertTrue(html.includes('data-section-id="licensing-opportunities"'),
    "Oportunidades también lleva su id de scroll-jump (dirección B)");
  assertTrue(html.includes('data-section-id="licensing-profile"'),
    "la sección del Perfil lleva su id de scroll-jump");
  assertTrue(html.includes('data-commercial-licencias-count'), "conteo de Oportunidades con host propio");
  assertTrue(html.includes('data-commercial-licprofile-count'), "conteo de Perfil con host propio");
  assertTrue(html.includes("Oportunidades de licenciamiento"), "título de la sección A");
  assertTrue(html.includes("Perfil de licenciamiento por cliente"), "título de la sección B");
  assertTrue(html.indexOf("Oportunidades de licenciamiento") < html.indexOf("Perfil de licenciamiento por cliente"),
    "A antes que B en el DOM");
  assertTrue(!html.includes("Alcance") && !html.includes("data-commercial-licprofile-scope-filter"),
    "owner corrective: el Perfil ya NO tiene un control de Alcance seleccionable");
}

// --- Ingreso esperado: SOLO Licencias, nunca Detalle -----------------------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [OPP_ROW({ licencias: "Si" }), OPP_ROW({ licencias: "No", opportunity: "Opp Dos" })], total: 2 },
  });
  const licHtml = commercialLicenciasTableHtml(commercialLicenciasFilteredRows());
  assertTrue(licHtml.includes(">Ingreso esperado<"), "Licencias muestra la columna Ingreso esperado");
  assertTrue(licHtml.includes("5,000") || licHtml.includes("5000"), "y su valor formateado");

  const detHtml = commercialDetalleTableHtml(commercialDetalleAllRows());
  assertTrue(!detHtml.includes("Ingreso esperado"), "Detalle Oportunidades NUNCA muestra Ingreso esperado");
}

// --- Licenciamiento: reemplaza el hardcode `licencias === "Si"` -----------
// (owner "Explicit Licensing Filter + Profile Scope" §1-§4).
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ opportunity: "Opp Si", licencias: "Si", stage: "1 Contacto", seller: "Vendedor Uno" }),
      OPP_ROW({ opportunity: "Opp No", licencias: "No", stage: "2 Propuesta", seller: "Vendedor Dos" }),
      OPP_ROW({ opportunity: "Opp Blank", licencias: "Sin definir", stage: "1 Contacto", seller: "Vendedor Uno" }),
    ], total: 3 },
  });
  // Default "si" reproduce EXACTO el hardcode viejo.
  assertTrue(commercialState.licLicenciamientoFilter === "si", "default es Sí");
  const defaultRows = commercialLicenciasFilteredRows();
  assertTrue(defaultRows.length === 1 && defaultRows[0].opportunity === "Opp Si",
    "el default reproduce EXACTO el viejo `licencias === \"Si\"` — nunca incluye No/Sin definir");

  // "no" es el COMPLEMENTO de "Si" (`!== "Si"`), corregido tras validación
  // en vivo: la property "Licencias" en Odoo casi nunca se completa
  // explícitamente en "No" — el uso real deja la mayoría SIN responder
  // ("Sin definir", normalizado, nunca null técnico). Un match EXACTO
  // contra el string "No" dejaba el filtro prácticamente vacío siempre —
  // "Sin definir" cae del lado de "No", no de un tercer balde.
  commercialState.licLicenciamientoFilter = "no";
  const noRows = commercialLicenciasFilteredRows();
  assertTrue(noRows.length === 2 && noRows.every(o => o.opportunity !== "Opp Si"),
    "Licenciamiento=No muestra TODO lo que no es \"Si\" — incluye \"No\" Y \"Sin definir\"");
  assertTrue(noRows.some(o => o.licencias === "No") && noRows.some(o => o.licencias === "Sin definir"),
    "\"Sin definir\" (estado de negocio real, nunca null técnico) SÍ cae dentro de No — es el complemento de Sí");

  // "todos" quita el predicado por completo.
  commercialState.licLicenciamientoFilter = "todos";
  assertTrue(commercialLicenciasFilteredRows().length === 3, "Todos quita el predicado de Licenciamiento");

  // Combina AND con Etapa/Vendedor/Buscar.
  commercialState.licLicenciamientoFilter = "si";
  commercialState.licStageFilter = "1 Contacto";
  assertTrue(commercialLicenciasFilteredRows().length === 1, "combina AND con Etapa");
  commercialState.licStageFilter = "";
  commercialState.licSellerFilter = "Vendedor Dos";
  assertTrue(commercialLicenciasFilteredRows().length === 0,
    "combina AND con Vendedor (Opp Si es de Vendedor Uno)");
  commercialState.licSellerFilter = "";

  // El conteo del header sigue el arreglo YA filtrado.
  assertTrue(commercialLicenciasCountLabel().startsWith("1 "), "el conteo sigue las filas filtradas por Licenciamiento");

  // Control de filtro presente, con las tres opciones.
  const filtersHtml = commercialLicenciasFiltersHtml();
  assertTrue(filtersHtml.includes("data-commercial-licencias-licenciamiento-filter"), "control Licenciamiento presente");
  for (const label of ["Sí", "No", "Todos"]) {
    assertTrue(filtersHtml.includes(`>${label}<`), `opción ${label} presente`);
  }
}

// --- El Perfil SIEMPRE deriva de las oportunidades visibles del grupo A ---
// (owner corrective, ver HANDOFF: el Alcance seleccionable se retiró — esto
// es ahora el ÚNICO comportamiento, no un modo entre varios).
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ opportunity: "Opp A1", account_id: 900, licencias: "Si", stage: "1 Contacto" }),
      OPP_ROW({ opportunity: "Opp A2", account_id: 900, licencias: "Si", stage: "2 Propuesta" }),
      OPP_ROW({ opportunity: "Opp B1", account_id: 901, licencias: "Si", stage: "1 Contacto" }),
    ], total: 3 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [
      PROFILE_ACCOUNT({ id: 900, name: "Cuenta Uno" }),
      PROFILE_ACCOUNT({ id: 901, name: "Cuenta Dos" }),
      PROFILE_ACCOUNT({ id: 902, name: "Cuenta Sin Pipeline" }),
    ], total: 3 },
  });
  // Deriva DISTINCT commercial_partner_id de las filas visibles — 3
  // oportunidades (2 de la cuenta 900, 1 de la 901) → 2 cuentas distintas.
  // La 902 (sin ninguna oportunidad visible) queda EXCLUIDA.
  const scoped = commercialLicensingProfileFilteredRows();
  assertTrue(scoped.length === 2 && scoped.every(a => [900, 901].includes(a.id)),
    "deriva cuentas DISTINTAS por account_id — 3 oportunidades, 2 cuentas, la sin-pipeline afuera");
  assertTrue(commercialLicenciasFilteredRows().length === 3 && scoped.length === 2,
    "3 oportunidades visibles / 2 cuentas — los conteos NO tienen por qué coincidir (grano distinto)");
  const scopedHtml = commercialLicensingProfileTableHtml(scoped);
  assertTrue(!scopedHtml.includes("Cuenta Sin Pipeline"), "y la cuenta sin oportunidades no aparece en la tabla");

  // Un filtro de Oportunidades recalcula el set derivado.
  commercialState.licStageFilter = "1 Contacto";
  const scopedAfterStage = commercialLicensingProfileFilteredRows();
  assertTrue(scopedAfterStage.length === 2, "Etapa=1 Contacto dejó 2 oportunidades (900,901), 2 cuentas");
  commercialState.licStageFilter = "2 Propuesta";
  const scopedAfterStage2 = commercialLicensingProfileFilteredRows();
  assertTrue(scopedAfterStage2.length === 1 && scopedAfterStage2[0].id === 900,
    "Etapa=2 Propuesta deja SÓLO la oportunidad de la cuenta 900 — el Perfil recalcula a 1 cuenta");
  commercialState.licStageFilter = "";

  // Cero oportunidades visibles → cero cuentas. Ya NO existe un modo
  // "Todas las cuentas" que mantenga el universo completo.
  commercialState.licLicenciamientoFilter = "no"; // ninguna opp tiene licencias==="No"/"Sin definir" en esta fixture
  assertTrue(commercialLicenciasFilteredRows().length === 0, "precondición: cero oportunidades visibles");
  assertTrue(commercialLicensingProfileFilteredRows().length === 0,
    "cero oportunidades visibles → cero cuentas, sin universo alternativo al que caer");
  commercialState.licLicenciamientoFilter = "si";
}

// --- Drilldown bidireccional por cuenta (id canónico, nunca nombre) -------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ account_id: 900, client: "Cuenta Uno" }),
      OPP_ROW({ opportunity: "Opp Dos", account_id: 901, client: "Cuenta Dos" }),
    ], total: 2 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [
      PROFILE_ACCOUNT({ id: 900, name: "Cuenta Uno" }),
      PROFILE_ACCOUNT({ id: 901, name: "Cuenta Dos" }),
      PROFILE_ACCOUNT({ id: 902, name: "Cuenta Sin Oportunidades" }), // sin fila de Oportunidad — nunca visible en el Perfil
    ], total: 3 },
  });
  // Dirección A: fila de Oportunidades lleva el account_id como data-attr,
  // accesible, sin marca por default.
  const licHtml = commercialLicenciasTableHtml(commercialLicenciasFilteredRows());
  assertTrue(licHtml.includes('data-commercial-licensing-account-row="900"'), "fila de A lleva el account_id");
  assertTrue(licHtml.includes('role="button"') && licHtml.includes('tabindex="0"'), "accesible por teclado");
  assertTrue(!licHtml.includes(' is-active"'), "sin drilldown, ninguna fila está marcada");
  assertTrue(commercialLicensingProfileFilteredRows().length === 2,
    "sin drilldown, B muestra las DOS cuentas con oportunidad visible (902 queda afuera)");

  // Simula lo que hace el delegado real: setea id CANÓNICO + origen — NUNCA
  // un texto de búsqueda.
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "opportunities";
  const filtered = commercialLicensingProfileFilteredRows();
  assertTrue(filtered.length === 1 && filtered[0].id === 900, "Oportunidad→Perfil filtra a ESA cuenta, por id");
  assertTrue(commercialLicensingProfileCountLabel() === "1 cuenta(s)", "y el conteo del header dice 1");
  const activeHtml = commercialLicenciasTableHtml(commercialLicenciasFilteredRows());
  assertTrue(activeHtml.includes('class="dashboard-table-row--clickable is-active"'), "la fila de origen queda marcada .is-active");
  const profileHtmlActive = commercialLicensingProfileTableHtml(filtered);
  assertTrue(!profileHtmlActive.includes(' is-active"'), "en el Perfil ninguna fila es \"origen\" — todas son RESULTADO");

  // Cierre: unifica las dos direcciones, restaura el universo vigente, vuelve
  // el foco al origen.
  hosts['[data-commercial-licensing-account-row="900"]'] = { focused: false, focus() { this.focused = true; } };
  commercialLicensingCloseDrilldown(root);
  assertTrue(commercialState.licensingDrilldownAccountId === null && commercialState.licensingDrilldownOrigin === null,
    "cierra -> limpia el foco contextual");
  assertTrue(commercialLicensingProfileFilteredRows().length === 2, "y las DOS cuentas vuelven a estar visibles");
  assertTrue(hosts['[data-commercial-licensing-account-row="900"]'].focused === true,
    "el foco vuelve a la fila de origen — el navegador la scrollea a la vista sola");

  // Dirección B: fila de Perfil clickeable, lleva su propio account_id.
  // Sólo las cuentas CON oportunidad visible llegan a tener fila.
  const profileHtml = commercialLicensingProfileTableHtml(commercialLicensingProfileFilteredRows());
  assertTrue(profileHtml.includes('data-commercial-licensing-profile-account-row="900"'), "fila de cuenta con account_id propio");
  assertTrue(!profileHtml.includes('data-commercial-licensing-profile-account-row="902"'),
    "la cuenta sin ninguna oportunidad visible NUNCA llega a tener fila (owner corrective)");
  assertTrue(count(profileHtml, 'role="button"') === 2, "las DOS filas de cuenta visibles son clickeables");

  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "profile";
  const oppFiltered = commercialLicenciasFilteredRows();
  assertTrue(oppFiltered.length === 1 && oppFiltered[0].account_id === 900, "Perfil→Oportunidades filtra a ESA cuenta, por id");
  const profileHtmlB = commercialLicensingProfileTableHtml(commercialLicensingProfileFilteredRows());
  assertTrue(profileHtmlB.includes('class="dashboard-table-row--clickable is-active"'), "la fila de cuenta de origen queda marcada");
  commercialLicensingCloseDrilldown(root);
}

// --- Cierre preserva filtros de la tabla OPUESTA (owner §4/§12) -----------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ account_id: 900, licencias: "No", stage: "3 Negociación" }),
      OPP_ROW({ opportunity: "Opp Dos", account_id: 900, licencias: "No", stage: "3 Negociación" }),
    ], total: 2 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [PROFILE_ACCOUNT({ id: 900 }), PROFILE_ACCOUNT({ id: 901, name: "Cuenta Otra" })], total: 2 },
    licLicenciamientoFilter: "no", licStageFilter: "3 Negociación",
    licProfileSegmentoFilter: "Corporate",
  });
  // Dirección A: cerrar el drilldown Oportunidad→Perfil NO debe tocar los
  // filtros manuales del Perfil (Segmento sigue "Corporate").
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "opportunities";
  commercialLicensingCloseDrilldown(root);
  assertTrue(commercialState.licProfileSegmentoFilter === "Corporate",
    "cerrar Oportunidad→Perfil preserva los filtros manuales del Perfil (owner §4)");

  // Dirección B: cerrar Perfil→Oportunidades NO debe resetear
  // Licenciamiento/Etapa/Vendedor/Buscar de Oportunidades.
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "profile";
  const duringDrilldown = commercialLicenciasFilteredRows();
  assertTrue(duringDrilldown.length === 2, "el drilldown compone DENTRO de Licenciamiento=No + Etapa=3 Negociación");
  commercialLicensingCloseDrilldown(root);
  assertTrue(commercialState.licLicenciamientoFilter === "no" && commercialState.licStageFilter === "3 Negociación",
    "cerrar Perfil→Oportunidades preserva Licenciamiento/Etapa (owner §12)");
  assertTrue(commercialLicenciasFilteredRows().length === 2, "y el universo filtrado previo vuelve completo");
}

// --- Cambio de Tipo limpia drilldown + resetea Licenciamiento a su default
{
  resetState({ capability: "licencias" });
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });
  commercialState.opportunitiesDetailStatus = "ready";
  commercialState.licensingProfileStatus = "ready";
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "opportunities";
  commercialState.licLicenciamientoFilter = "no";
  commercialSetTipo("bi");
  assertTrue(commercialState.licensingDrilldownAccountId === null && commercialState.licensingDrilldownOrigin === null,
    "el drilldown por cuenta se limpia al cambiar Tipo");
  assertTrue(commercialState.licLicenciamientoFilter === "si",
    "Licenciamiento vuelve a su default \"si\" — MISMO lifecycle que el resto de filtros de Licencias");
}

// --- Identidad semántica ESTABLE por columna, nunca posicional -----------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [OPP_ROW()], total: 1 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [PROFILE_ACCOUNT()], total: 1 },
  });
  const tableHtml = commercialLicensingProfileTableHtml(commercialLicensingProfileFilteredRows());
  for (const key of ["name", "partner_de_licencia", "segmento_cuenta", "vencimiento_contrato",
    "seller", "ae_sap", "arquitectura_actual", "monto_contrato", "tipo_contrato_sap"]) {
    assertTrue(tableHtml.includes(`data-commercial-licprofile-col="${key}"`), `celda con identidad estable: ${key}`);
    assertTrue(tableHtml.includes(`data-commercial-licprofile-sort="${key}"`), `header con la MISMA clave: ${key}`);
  }
}

// --- Cero framework genérico de resize/visibilidad introducido -----------
{
  for (const banned of ["resize-handle", "column-visibility", "colResize", "data-resize",
    "columnManager", "toggleColumn", "drag-to-resize"]) {
    assertTrue(!src.includes(banned), `sin código genérico de ergonomía de columnas: ${banned}`);
  }
}

// --- Filtros adicionales del Perfil (owner corrective post-live-QA) -------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ opportunity: "Opp Uno", account_id: 900 }),
      OPP_ROW({ opportunity: "Opp Dos", account_id: 901 }),
    ], total: 2 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [
      PROFILE_ACCOUNT({ id: 900, partner_de_licencia: "05 - SOFTTEK", ae_sap: "AE Uno", tipo_contrato_sap: "Contrato Individual", seller: "Vendedor Uno" }),
      PROFILE_ACCOUNT({ id: 901, name: "Cuenta Otra", partner_de_licencia: "01 - SAP", ae_sap: "AE Dos", tipo_contrato_sap: "Asociado Rise", seller: "Vendedor Dos" }),
    ], total: 2 },
  });
  assertTrue(commercialLicensingProfileFilteredRows().length === 2, "sin filtros, las dos cuentas");
  commercialState.licProfilePartnerFilter = "05 - SOFTTEK";
  assertTrue(commercialLicensingProfileFilteredRows().length === 1 && commercialLicensingProfileFilteredRows()[0].id === 900,
    "filtro Partner de licencia funciona");
  commercialState.licProfilePartnerFilter = "";
  commercialState.licProfileAeSapFilter = "AE Dos";
  assertTrue(commercialLicensingProfileFilteredRows()[0].id === 901, "filtro AE SAP funciona");
  commercialState.licProfileAeSapFilter = "";
  commercialState.licProfileTipoContratoFilter = "Asociado Rise";
  assertTrue(commercialLicensingProfileFilteredRows()[0].id === 901, "filtro Tipo de contrato SAP funciona");
  commercialState.licProfileTipoContratoFilter = "";
  commercialState.licProfileSellerFilter = "Vendedor Uno";
  assertTrue(commercialLicensingProfileFilteredRows()[0].id === 900, "filtro Vendedor funciona");
  commercialState.licProfileSellerFilter = "";
}

// --- Reactividad global a Tipo: LAS DOS tablas se recargan (owner §2) -----
{
  resetState({ capability: "licencias" });
  fetchImpl = url => Promise.resolve({ json: () => Promise.resolve(
    url.includes("licensing-profile")
      ? { state: "ok", licensing_profile: { accounts: [], total: 0 } }
      : { state: "ok", opportunities_detail: { opportunities: [], total: 0 } }
  ) });
  fetchCalls.length = 0;
  commercialState.opportunitiesDetailStatus = "ready";
  commercialState.licensingProfileStatus = "ready";
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "opportunities";

  commercialSetTipo("nn");
  assertTrue(commercialState.opportunitiesDetailStatus === "loading",
    "cambiar Tipo con Licencias activa recarga A de inmediato (nunca se queda en idle)");
  assertTrue(commercialState.licensingProfileStatus === "loading",
    "y TAMBIÉN recarga B de inmediato — owner §2, las dos deben responder");
  assertTrue(commercialState.licensingDrilldownAccountId === null,
    "el drilldown se limpia al cambiar Tipo — nunca sobrevive a un universo nuevo");
}

// --- Owner corrective (post-live-QA): el click es una intención de VER el
// resultado — foco/scroll deben seguir al resultado, en LAS DOS direcciones,
// MISMA mecánica que "Retrasadas"/"Por vencer" (`commercialActJumpToSection`).
// Se ejercitan los delegados REALES (click Y keydown), nunca una llamada
// directa a la función. También prueba que un click NUNCA dispara la
// dirección contraria (owner §9: "prevent interaction loops"). -----------
{
  resetState({
    capability: "licencias",
    opportunitiesDetailStatus: "ready", opportunitiesDetail: { opportunities: [OPP_ROW()], total: 1 },
    licensingProfileStatus: "ready", licensingProfile: { accounts: [PROFILE_ACCOUNT()], total: 1 },
  });
  (listeners.DOMContentLoaded || []).forEach(cb => cb());

  // Dirección A → salta a "licensing-profile".
  const profileTarget = { open: false, scrolled: 0, scrollIntoView() { this.scrolled++; } };
  hosts['[data-section-id="licensing-profile"]'] = profileTarget;
  const oppTarget = { open: false, scrolled: 0, scrollIntoView() { this.scrolled++; } };
  hosts['[data-section-id="licensing-opportunities"]'] = oppTarget;

  const fakeOppRow = { dataset: { commercialLicensingAccountRow: "900" } };
  (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-licensing-account-row]" ? fakeOppRow : null) },
  }));
  assertTrue(profileTarget.open === true && profileTarget.scrolled === 1,
    "click en fila de Oportunidad abre y scrollea al Perfil");
  assertTrue(oppTarget.scrolled === 0, "y NUNCA dispara además la dirección contraria (Oportunidades no scrollea)");
  assertTrue(commercialState.licensingDrilldownOrigin === "opportunities", "origen correcto");

  commercialLicensingCloseDrilldown(root);
  profileTarget.scrolled = 0; profileTarget.open = false; oppTarget.scrolled = 0;

  // Dirección B → salta a "licensing-opportunities".
  const fakeProfileRow = { dataset: { commercialLicensingProfileAccountRow: "900" } };
  (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-licensing-profile-account-row]" ? fakeProfileRow : null) },
  }));
  assertTrue(oppTarget.open === true && oppTarget.scrolled === 1,
    "click en fila de cuenta del Perfil abre y scrollea a Oportunidades");
  assertTrue(profileTarget.scrolled === 0, "y NUNCA dispara la dirección contraria (Perfil no vuelve a scrollear)");
  assertTrue(commercialState.licensingDrilldownOrigin === "profile", "origen correcto");

  // Teclado — mismo camino, las dos direcciones.
  commercialLicensingCloseDrilldown(root);
  oppTarget.scrolled = 0; oppTarget.open = false; profileTarget.scrolled = 0; profileTarget.open = false;
  (listeners.keydown || []).forEach(cb => cb({
    key: "Enter",
    target: { closest: sel => (sel === "[data-commercial-licensing-account-row]" ? fakeOppRow : null) },
    preventDefault() {},
  }));
  assertTrue(profileTarget.open === true && profileTarget.scrolled === 1, "Enter sobre la fila de Oportunidad hace lo mismo");
  commercialLicensingCloseDrilldown(root);
  profileTarget.scrolled = 0; profileTarget.open = false;
  (listeners.keydown || []).forEach(cb => cb({
    key: "Enter",
    target: { closest: sel => (sel === "[data-commercial-licensing-profile-account-row]" ? fakeProfileRow : null) },
    preventDefault() {},
  }));
  assertTrue(oppTarget.open === true && oppTarget.scrolled === 1, "Enter sobre la fila de cuenta hace lo mismo");
}

// --- Sin stale rows: tras el cambio de tipo, el perfil viejo no sobrevive -
(async () => {
  resetState({ capability: "licencias" });
  commercialState.opportunitiesDetailStatus = "ready";
  commercialState.opportunitiesDetail = { opportunities: [OPP_ROW()], total: 1 };
  commercialState.licensingProfileStatus = "ready";
  commercialState.licensingProfile = { accounts: [PROFILE_ACCOUNT({ name: "Cuenta Vieja" })], total: 1 };
  hosts["[data-commercial-licprofile-table]"] = { innerHTML: "" };
  hosts["[data-commercial-licencias-table]"] = { innerHTML: "" };

  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve(
    { state: "ok", licensing_profile: { accounts: [PROFILE_ACCOUNT({ id: 950, name: "Cuenta Nueva" })], total: 1 } }
  ) });
  commercialSetTipo("bi");
  assertTrue(commercialState.licensingProfile === null,
    "el dataset viejo se invalida de inmediato (null), nunca se muestra mientras carga el nuevo");
  await new Promise(resolve => setImmediate(resolve));
  assertTrue(commercialState.licensingProfile && commercialState.licensingProfile.accounts[0].name === "Cuenta Nueva",
    "al resolver, el perfil refleja el universo NUEVO, sin rastro del viejo");

  finish();
})();

function finish() {
  if (failures) {
    console.log(`\n${failures} invariante(s) fallaron`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL LICENSING PROFILE FRONTEND INVARIANTS PASSED");
}
