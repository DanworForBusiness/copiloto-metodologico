// commercial_licensing_profile.node.js — invocado por
// test_commercial_licensing_profile_frontend.py.
//
// Account Licensing Profile (ver HANDOFF — Commercial Licensing, owner
// ticket "Final Business Semantics Realignment"): DOS superficies
// analíticas INDEPENDIENTES dentro de Licencias.
//
// Tabla A — Oportunidades de licenciamiento: universo FIJO
// `licencias === "Si"` (sin selector, restaurado), + Etapa/Vendedor/Buscar,
// + drilldown contextual opcional. Tabla B — Perfil de licenciamiento por
// cliente: universo FIJO `Es Cliente?=Sí` (resuelto server-side, el
// frontend nunca lo re-deriva), + filtros locales propios, + drilldown
// contextual opcional. NINGÚN filtro normal de una tabla afecta a la otra
// — sólo el drilldown explícito (bidireccional, ya aprobado) las conecta,
// de forma TEMPORAL y reversible.
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
  initDraggableColumns() {},
  initResizableColumns() {},
  readStoredColumnOrder() { return null; },
  readStoredColumnWidths() { return null; },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });

const {
  commercialLicenciasHtml, commercialLicensingProfileFilteredRows, commercialLicensingProfileAllRows,
  commercialSetTipo, commercialSetCapability, commercialLoadLicensingProfile, commercialLoadOpportunitiesDetail,
  commercialDetalleTableHtml, commercialDetalleAllRows, commercialLicenciasTableHtml, commercialLicenciasAllRows,
  commercialLicenciasFilteredRows, commercialLicenciasFiltersHtml,
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
// Ya no lleva ningún indicio de "Es Cliente?" — el backend sólo devuelve
// cuentas YA elegibles (Es Cliente?=Sí), el frontend nunca re-evalúa eso.
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
  // TRES (no dos) desde Director §5/§6 (ver HANDOFF): "Sin prioridad" es
  // un TERCER `<details ... open>` anidado dentro de la sección del
  // Perfil — main + secondary del Perfil, más Oportunidades.
  assertTrue(count(html, "<details[^>]* open>") === 3, "las tres secciones abiertas por default (Oportunidades + Perfil + Sin prioridad)");
  assertTrue(html.includes('data-section-id="licensing-opportunities"'),
    "Oportunidades lleva su id de scroll-jump (dirección B)");
  assertTrue(html.includes('data-section-id="licensing-profile"'),
    "el Perfil lleva su id de scroll-jump");
  assertTrue(html.includes('data-commercial-licencias-count'), "conteo de Oportunidades con host propio");
  assertTrue(html.includes('data-commercial-licprofile-count'), "conteo de Perfil con host propio");
  assertTrue(html.includes("Oportunidades de licenciamiento"), "título de la sección A");
  assertTrue(html.includes("Perfil de licenciamiento por cliente"), "título de la sección B");
  assertTrue(!html.includes("Licenciamiento") || !html.includes("<select"),
    "owner Final Business Semantics Realignment: sin selector Licenciamiento en Oportunidades");
  assertTrue(!html.includes("Alcance"), "y sin selector Alcance en el Perfil");
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

// --- Tabla A: universo FIJO Licenciamiento=Sí, sin selector ---------------
// (owner "Final Business Semantics Realignment" §2, tests A-D/I/J)
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ opportunity: "Opp Si", licencias: "Si", stage: "1 Contacto", seller: "Vendedor Uno", account_id: 900 }),
      OPP_ROW({ opportunity: "Opp No", licencias: "No", stage: "2 Propuesta", seller: "Vendedor Dos", account_id: 901 }),
      OPP_ROW({ opportunity: "Opp Blank", licencias: "Sin definir", stage: "1 Contacto", seller: "Vendedor Uno", account_id: 902 }),
    ], total: 3 },
  });
  const rows = commercialLicenciasFilteredRows();
  assertTrue(rows.length === 1 && rows[0].opportunity === "Opp Si",
    "A/B/C: SOLO Licenciamiento=Sí se renderiza — No y Sin definir quedan afuera, siempre, sin selector");
  assertTrue(!commercialLicenciasFiltersHtml().includes("Licenciamiento"),
    "D: no existe ningún control Licenciamiento en la barra de Oportunidades");

  // H — combina con Etapa/Vendedor/Buscar sobre el universo YA fijo.
  commercialState.licStageFilter = "1 Contacto";
  assertTrue(commercialLicenciasFilteredRows().length === 1, "H: Etapa sigue componiendo AND sobre el universo fijo");
  commercialState.licStageFilter = "";
  commercialState.licSellerFilter = "Vendedor Dos";
  assertTrue(commercialLicenciasFilteredRows().length === 0,
    "H: Vendedor también — Opp Si es de Vendedor Uno, Vendedor Dos filtra a 0");
  commercialState.licSellerFilter = "";

  // I/J — Es Cliente? NUNCA determina inclusión en Tabla A (no hay ningún
  // campo/predicado de ese tipo en absoluto en el pipeline de Oportunidades
  // — se prueba en test_commercial_licensing_profile_frontend.py vía grep
  // estructural; acá sólo se confirma que ningún campo "es_cliente" viaja
  // en las filas ni afecta el conteo).
  assertTrue(!("es_cliente" in rows[0]), "I: ninguna fila de Oportunidades lleva un campo Es Cliente?");
  assertTrue(commercialLicenciasCountLabel().startsWith("1 "), "el conteo refleja el universo fijo + filtros locales");
}

// --- Tabla A: Todos/New Name/Base instalada siguen vía tags de -----------
// oportunidad (owner §2 "The global tipo selector continues to use the
// existing opportunity-tag semantics" — tests E/F/G, a nivel de contrato:
// el universo de `opportunitiesDetail` ya llega segmentado por tipo desde
// el backend, el frontend sólo filtra Licenciamiento=Sí sobre lo que
// recibe — ver test_commercial_service.py para la semántica de tags). -----
{
  resetState({
    tipo: "nn",
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [OPP_ROW({ licencias: "Si" })], total: 1 },
  });
  assertTrue(commercialLicenciasFilteredRows().length === 1,
    "E/F/G: Tabla A sigue funcionando bajo cualquier tipo — el universo por tag ya viene resuelto del backend");
}

// --- Tabla B: universo FIJO Es Cliente?=Sí, sin Alcance, SIN acoplar -----
// con Oportunidades (owner §3-§5, tests K-R) --------------------------------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    // Deliberadamente SIN ninguna oportunidad para la cuenta 901 — prueba
    // O/P: un cliente sin pipeline y sin Licenciamiento=Sí igual aparece.
    opportunitiesDetail: { opportunities: [OPP_ROW({ account_id: 900, licencias: "Si" })], total: 1 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [
      PROFILE_ACCOUNT({ id: 900, name: "Cuenta Con Pipeline" }),
      PROFILE_ACCOUNT({ id: 901, name: "Cuenta Sin Pipeline" }),
    ], total: 2 },
  });
  const rows = commercialLicensingProfileFilteredRows();
  assertTrue(rows.length === 2, "K-Q: TODAS las cuentas que el backend envía se muestran — ninguna se filtra por pipeline/Licenciamiento");
  assertTrue(rows.some(a => a.name === "Cuenta Sin Pipeline"),
    "O/P: la cuenta SIN ninguna oportunidad (ni Licenciamiento=Sí) sigue incluida");
  assertTrue(!commercialLicensingProfileFiltersHtml().includes("Alcance"),
    "R: no existe ningún control Alcance en la barra del Perfil");

  // Cambiar filtros de Oportunidades (Etapa/Vendedor/Buscar) NUNCA debe
  // tocar el Perfil (owner §7: "Changing opportunity filters must not
  // silently change the normal Profile count").
  commercialState.licStageFilter = "8 Propuesta formal Presentada"; // deja 0 oportunidades visibles
  assertTrue(commercialLicenciasFilteredRows().length === 0, "precondición: 0 oportunidades visibles");
  assertTrue(commercialLicensingProfileFilteredRows().length === 2,
    "§7: el Perfil sigue mostrando sus 2 cuentas — INDEPENDIENTE de qué oportunidades están visibles");
  commercialState.licStageFilter = "";
}

// --- Filtros adicionales del Perfil (owner corrective post-live-QA) -------
{
  resetState({
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [
      PROFILE_ACCOUNT({ id: 900, partner_de_licencia: "05 - SOFTTEK", ae_sap: "AE Uno", tipo_contrato_sap: "Contrato Individual", seller: "Vendedor Uno" }),
      PROFILE_ACCOUNT({ id: 901, name: "Cuenta Otra", partner_de_licencia: "01 - SAP", ae_sap: "AE Dos", tipo_contrato_sap: "Asociado Rise", seller: "Vendedor Dos" }),
    ], total: 2 },
  });
  assertTrue(commercialLicensingProfileFilteredRows().length === 2, "T: sin filtros, las dos cuentas");
  commercialState.licProfilePartnerFilter = "05 - SOFTTEK";
  assertTrue(commercialLicensingProfileFilteredRows().length === 1 && commercialLicensingProfileFilteredRows()[0].id === 900,
    "T: filtro Partner de licencia funciona");
  commercialState.licProfilePartnerFilter = "";
  commercialState.licProfileAeSapFilter = "AE Dos";
  assertTrue(commercialLicensingProfileFilteredRows()[0].id === 901, "T: filtro AE SAP funciona");
  commercialState.licProfileAeSapFilter = "";
  commercialState.licProfileTipoContratoFilter = "Asociado Rise";
  assertTrue(commercialLicensingProfileFilteredRows()[0].id === 901, "T: filtro Tipo de contrato SAP funciona");
  commercialState.licProfileTipoContratoFilter = "";
  commercialState.licProfileSellerFilter = "Vendedor Uno";
  assertTrue(commercialLicensingProfileFilteredRows()[0].id === 900, "T: filtro Vendedor funciona");
  commercialState.licProfileSellerFilter = "";
  // U — el conteo del header sigue el arreglo YA filtrado, nunca el crudo.
  commercialState.licProfileSellerFilter = "Vendedor Uno";
  assertTrue(commercialLicensingProfileCountLabel() === "1 cuenta(s)", "U: conteo sigue las filas filtradas");
  commercialState.licProfileSellerFilter = "";
}

// --- Drilldown bidireccional por cuenta (id canónico, nunca nombre) -------
// (owner "Bidirectional Account Drilldown", preservado — tests V-AC) -------
{
  resetState({
    opportunitiesDetailStatus: "ready",
    opportunitiesDetail: { opportunities: [
      OPP_ROW({ account_id: 900, client: "Cuenta Uno", licencias: "Si" }),
      OPP_ROW({ opportunity: "Opp Dos", account_id: 901, client: "Cuenta Dos", licencias: "Si" }),
    ], total: 2 },
    licensingProfileStatus: "ready",
    licensingProfile: { accounts: [
      PROFILE_ACCOUNT({ id: 900, name: "Cuenta Uno" }),
      PROFILE_ACCOUNT({ id: 901, name: "Cuenta Dos" }),
      // Cliente elegible SIN ninguna oportunidad Licenciamiento=Sí — caso Z.
      PROFILE_ACCOUNT({ id: 902, name: "Cuenta Sin Licenciamiento" }),
    ], total: 3 },
  });

  // V — Oportunidad → Perfil, cuenta elegible: aparece.
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "opportunities";
  const filtered = commercialLicensingProfileFilteredRows();
  assertTrue(filtered.length === 1 && filtered[0].id === 900, "V: Oportunidad→Perfil filtra a ESA cuenta, por id");
  assertTrue(commercialLicensingProfileCountLabel() === "1 cuenta(s)", "y el conteo del header dice 1");

  // X — cierre restaura el universo COMPLETO del Perfil (las 3 cuentas).
  hosts['[data-commercial-licensing-account-row="900"]'] = { focused: false, focus() { this.focused = true; } };
  commercialLicensingCloseDrilldown(root);
  assertTrue(commercialLicensingProfileFilteredRows().length === 3, "X: cierre restaura el universo COMPLETO del Perfil");
  assertTrue(hosts['[data-commercial-licensing-account-row="900"]'].focused === true,
    "y el foco vuelve a la fila de origen");

  // W — Oportunidad cuya cuenta NO es cliente (no está en el Perfil en
  // absoluto): resultado contextual = 0, empty state de negocio explícito,
  // NUNCA se inyecta la cuenta temporalmente al Perfil.
  // CONTRATO NUEVO (Director, "Ver detalle de licencias"): una cuenta fuera
  // del universo gobernado YA NO termina en un empty state — se inspecciona
  // igual. Lo que sigue intacto es el guardrail de la COLECCIÓN: la cuenta
  // NUNCA se inyecta en `licensingProfile.accounts`; la fila viene aparte, de
  // `licensingDetailAccount`.
  commercialState.licensingDrilldownAccountId = 999; // no existe en licensingProfile.accounts
  commercialState.licensingDrilldownOrigin = "opportunities";
  commercialState.licensingDetailAccount = null;
  assertTrue(commercialLicensingProfileFilteredRows().length === 0,
    "W: sin la cuenta traída todavía, cero filas — nunca una inyectada al universo");
  assertTrue(commercialLicensingProfileAllRows().every(a => a.id !== 999),
    "W: y el universo gobernado sigue sin conocerla");
  commercialState.licensingDetailAccount = { id: 999, name: "Cuenta Ajena" };
  const detalle = commercialLicensingProfileFilteredRows();
  assertTrue(detalle.length === 1 && detalle[0].id === 999,
    "W: con la cuenta traída, se muestra ESA cuenta — 1 fila, no un empty state");
  const detalleHtml = commercialLicensingProfileTableHtml(detalle, "detail");
  assertTrue(detalleHtml.includes("Cuenta Ajena"), "W: y su nombre se renderiza");
  assertTrue(!detalleHtml.includes("Mover a sin prioridad"),
    "W: sin acción de prioridad — la clasificación es del universo gobernado");
  commercialState.licensingDetailAccount = null;
  commercialLicensingCloseDrilldown(root);

  // Y/Z/AA — Perfil → Oportunidades, incluso para una cuenta SIN ninguna
  // oportunidad Licenciamiento=Sí (owner §10: "Do NOT broaden Table A to
  // Licenciamiento=No/Todos just to find something").
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "profile";
  const oppFiltered = commercialLicenciasFilteredRows();
  assertTrue(oppFiltered.length === 1 && oppFiltered[0].account_id === 900, "Y: Perfil→Oportunidades filtra a ESA cuenta, por id");

  commercialState.licensingDrilldownAccountId = 902; // sin ninguna oportunidad Licenciamiento=Sí
  const zeroOppResult = commercialLicenciasFilteredRows();
  assertTrue(zeroOppResult.length === 0, "Z: cuenta elegible sin oportunidades Licenciamiento=Sí -> 0, resultado válido");
  const zeroOppHtml = commercialLicenciasTableHtml(zeroOppResult);
  assertTrue(zeroOppHtml.includes("Sin oportunidades de licenciamiento identificadas para esta cuenta"),
    "Z: empty state de negocio, NUNCA se amplía a No/Todos para encontrar algo");

  // AA — cierre restaura el universo COMPLETO de Oportunidades (2 filas).
  hosts['[data-commercial-licensing-profile-account-row="902"]'] = { focused: false, focus() { this.focused = true; } };
  commercialLicensingCloseDrilldown(root);
  assertTrue(commercialLicenciasFilteredRows().length === 2, "AA: cierre restaura el universo COMPLETO de Oportunidades");
  assertTrue(hosts['[data-commercial-licensing-profile-account-row="902"]'].focused === true, "y el foco vuelve al origen");
}

// --- F: cambio de Tipo limpia el drilldown, aunque esté activo ------------
{
  resetState({ capability: "licencias" });
  fetchImpl = () => Promise.resolve({ json: () => Promise.resolve({ state: "config_incomplete" }) });
  commercialState.opportunitiesDetailStatus = "ready";
  commercialState.licensingProfileStatus = "ready";
  commercialState.licensingDrilldownAccountId = 900;
  commercialState.licensingDrilldownOrigin = "opportunities";
  commercialSetTipo("bi");
  assertTrue(commercialState.licensingDrilldownAccountId === null && commercialState.licensingDrilldownOrigin === null,
    "el drilldown por cuenta se limpia al cambiar Tipo");
}

// --- Identidad semántica ESTABLE por columna, nunca posicional -----------
{
  resetState({
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

// --- Reactividad global a Tipo: LAS DOS tablas se recargan (owner §2 -----
// original de Bidirectional Account Drilldown — el REFETCH persiste, el
// backend ahora devuelve el MISMO resultado sin importar tipo, ver
// test_commercial_licensing_profile.py) ------------------------------------
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
    "y TAMBIÉN recarga B de inmediato — S: aunque el resultado no cambie, la recarga es inocua");
  assertTrue(commercialState.licensingDrilldownAccountId === null,
    "el drilldown se limpia al cambiar Tipo — nunca sobrevive a un universo nuevo");
}

// --- Owner corrective (post-live-QA): el click es una intención de VER el
// resultado — foco/scroll deben seguir al resultado, en LAS DOS direcciones.
// Se ejercitan los delegados REALES (click Y keydown). AB: nunca dispara la
// dirección contraria. AC: teclado funciona igual. -------------------------
{
  resetState({
    capability: "licencias",
    opportunitiesDetailStatus: "ready", opportunitiesDetail: { opportunities: [OPP_ROW({ licencias: "Si" })], total: 1 },
    licensingProfileStatus: "ready", licensingProfile: { accounts: [PROFILE_ACCOUNT()], total: 1 },
  });
  (listeners.DOMContentLoaded || []).forEach(cb => cb());

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
  assertTrue(oppTarget.scrolled === 0, "AB: y NUNCA dispara además la dirección contraria");
  assertTrue(commercialState.licensingDrilldownOrigin === "opportunities", "origen correcto");

  commercialLicensingCloseDrilldown(root);
  profileTarget.scrolled = 0; profileTarget.open = false; oppTarget.scrolled = 0;

  const fakeProfileRow = { dataset: { commercialLicensingProfileAccountRow: "900" } };
  (listeners.click || []).forEach(cb => cb({
    target: { closest: sel => (sel === "[data-commercial-licensing-profile-account-row]" ? fakeProfileRow : null) },
  }));
  assertTrue(oppTarget.open === true && oppTarget.scrolled === 1,
    "click en fila de cuenta del Perfil abre y scrollea a Oportunidades");
  assertTrue(profileTarget.scrolled === 0, "AB: y NUNCA dispara la dirección contraria");
  assertTrue(commercialState.licensingDrilldownOrigin === "profile", "origen correcto");

  // "AC: Enter sobre la fila de Oportunidad hace lo mismo" — YA NO aplica
  // como estaba (Director §1/§3, ver HANDOFF): `data-commercial-licensing-
  // account-row` ahora vive en el botón NATIVO "Ver detalle de licencias"
  // (antes vivía en la fila entera `role="button"`) — un `<button>` real
  // maneja Enter/Espacio SOLO, vía el browser, nunca por este delegado de
  // `keydown` (MISMO criterio ya documentado para la X de cierre). El
  // camino de click de arriba (líneas 402-409) YA cubre su activación: la
  // activación por teclado de un `<button>` nativo se reduce SIEMPRE a un
  // evento `click`, el mismo que ese bloque ya simula y confirma.
  commercialLicensingCloseDrilldown(root);
  oppTarget.scrolled = 0; oppTarget.open = false; profileTarget.scrolled = 0; profileTarget.open = false;
  profileTarget.scrolled = 0; profileTarget.open = false;
  (listeners.keydown || []).forEach(cb => cb({
    key: "Enter",
    target: { closest: sel => (sel === "[data-commercial-licensing-profile-account-row]" ? fakeProfileRow : null) },
    preventDefault() {},
  }));
  assertTrue(oppTarget.open === true && oppTarget.scrolled === 1, "AC: Enter sobre la fila de cuenta hace lo mismo");
}

// --- Sin stale rows: tras el cambio de tipo, el perfil viejo no sobrevive -
(async () => {
  resetState({ capability: "licencias" });
  commercialState.opportunitiesDetailStatus = "ready";
  commercialState.opportunitiesDetail = { opportunities: [OPP_ROW({ licencias: "Si" })], total: 1 };
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
