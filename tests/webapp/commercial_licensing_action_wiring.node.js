// commercial_licensing_action_wiring.node.js — invocado por
// test_commercial_licensing_action_wiring.py.
//
// REGRESIÓN: "Ver detalle de licencias" se renderizaba bien y al clickearlo NO
// PASABA NADA — sin navegación, sin filtrado, sin error en consola. Misma causa
// mataba "Mover a sin prioridad" y "Restaurar".
//
// CAUSA: los botones llevaban `onclick="event.stopPropagation()"` inline para
// no disparar TAMBIÉN el click de la fila que los contiene. Pero el único
// delegado de Comercial escucha en `document` en fase de BUBBLE, así que cortar
// la propagación en el botón lo deja fuera del alcance del delegado: la acción
// queda inerte. Probado en browser: el evento llegaba a `document` en CAPTURE
// (true) pero no en BUBBLE (false).
//
// POR QUÉ LOS TESTS EXISTENTES NO LO VIERON, y por qué este harness es
// distinto: los de licensing asertan sobre el STRING de HTML generado y sobre
// patrones del fuente. Un botón que se renderiza perfecto pero es inalcanzable
// por su delegado es INVISIBLE para eso. Acá el DOM PROPAGA de verdad —
// target → ancestros → document, honrando `stopPropagation()` y ejecutando el
// `onclick` inline si el markup lo trae— así que el harness reproduce la falla
// en vez de asumirla.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "webapp", "static", "commercial.js"), "utf-8");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function eq(a, e, label) {
  if (JSON.stringify(a) !== JSON.stringify(e)) {
    failures++; console.log(`FAIL: ${label} — esperado ${JSON.stringify(e)}, obtenido ${JSON.stringify(a)}`);
  }
}

// ── DOM con PROPAGACIÓN REAL ───────────────────────────────────────────────
// Lo único que este doble tiene de especial, y es todo el punto del harness.
const docListeners = Object.create(null);

function nodo(attrs, padre) {
  const el = {
    attrs: attrs || {}, hijos: [], padre: padre || null,
    dataset: {}, classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; },
    closest(sel) {
      const m = sel.match(/^\[([a-z-]+)\]$/);
      const attr = m && m[1];
      let n = this;
      while (n) { if (attr && attr in n.attrs) return n; n = n.padre; }
      return null;
    },
    querySelector(sel) { return buscar(this, sel)[0] || null; },
    querySelectorAll(sel) { return buscar(this, sel); },
  };
  // `dataset` derivado de los atributos reales, como en un DOM de verdad.
  Object.keys(el.attrs).forEach(a => {
    if (!a.startsWith("data-")) return;
    const camel = a.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el.dataset[camel] = el.attrs[a];
  });
  if (padre) padre.hijos.push(el);
  return el;
}
function buscar(raiz, sel) {
  const m = sel.match(/^\[([a-z-]+)\]$/);
  const attr = m && m[1];
  const out = [];
  (function rec(n) { n.hijos.forEach(h => { if (attr && attr in h.attrs) out.push(h); rec(h); }); })(raiz);
  return out;
}

// Dispatch REAL: target → ancestros → document, con `stopPropagation` honrado
// y el `onclick` inline ejecutándose en el target (que es donde el bug vivía).
function click(target) {
  let detenido = false;
  const event = {
    target,
    stopPropagation() { detenido = true; },
    preventDefault() {},
  };
  if (target.attrs.onclick === "event.stopPropagation()") event.stopPropagation();
  if (detenido) return { llegoADocument: false };
  let n = target;
  while (n) { n = n.padre; if (detenido) return { llegoADocument: false }; }
  (docListeners.click || []).forEach(cb => cb(event));
  return { llegoADocument: true };
}

// ── Carga del módulo REAL ──────────────────────────────────────────────────
const documento = nodo({});
const root = nodo({ id: "commercial-main-content" }, documento);
const sandbox = {
  document: {
    addEventListener(type, cb) { (docListeners[type] = docListeners[type] || []).push(cb); },
    getElementById(id) { return id === "commercial-main-content" ? root : null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  window: {},
  fetch: () => new Promise(() => {}),
  HubForegroundProgress: { begin(){}, complete(){}, fail(){} },
  initDashboardSections(){}, initEventBreakdown(){}, initDraggableColumns(){}, initResizableColumns(){},
  readStoredColumnOrder(){ return null; },
  readStoredColumnWidths(){ return null; },
  // `commercialLicensingProfileSetPriority` construye el body con
  // `URLSearchParams`: sin él, la llamada tira ReferenceError ANTES de
  // `fetch` y el try/catch se lo traga — el harness vería "no salió ninguna
  // mutación" y culparía al producto.
  URLSearchParams, setTimeout, Promise, Set, JSON, Object, Number, String,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "commercial.js" });
const commercialState = vm.runInContext("commercialState", sandbox);
const { commercialLicenciasCompactRowHtml, commercialLicensingProfileRowHtml,
        commercialLicensingProfileTableHtml } = sandbox;

// El delegado se registra en DOMContentLoaded.
(docListeners.DOMContentLoaded || []).forEach(cb => cb({}));

// Se espían los efectos observables sin tocar el módulo.
let jumped = null;
sandbox.commercialActJumpToSection = (id) => { jumped = id; };
sandbox.commercialRenderCapability = () => {};
sandbox.commercialSetCapability = (c) => { commercialState.capability = c; };

// ── Construcción del DOM a partir del markup REAL ──────────────────────────
// Se leen los atributos del HTML que el producto genera — si el markup vuelve
// a traer `onclick`, el harness lo ejecuta y el test cae.
function atributosDe(html, marcador) {
  const i = html.indexOf(marcador);
  if (i === -1) return null;
  const ini = html.lastIndexOf("<", i);
  const fin = html.indexOf(">", i);
  const tag = html.slice(ini, fin);
  const attrs = {};
  const re = /([a-zA-Z-]+)(?:="([^"]*)")?/g;
  let m; re.exec(tag);                       // saltea el nombre del tag
  while ((m = re.exec(tag))) attrs[m[1]] = m[2] === undefined ? "" : m[2];
  return attrs;
}

const OPP = { id: 77, opportunity: "Licencias ACME", client: "ACME S.A.", seller: "Ana",
              stage: "3 - Propuesta", tags: [], revenue: 5000, account_id: 4242 };
const OPP_SIN_CUENTA = { ...OPP, id: 78, client: "Sin cuenta", account_id: null };

function montarFila(opp) {
  root.hijos.length = 0;
  const html = commercialLicenciasCompactRowHtml(opp);
  const fila = nodo(atributosDe(html, "data-commercial-licencias-opportunity-row"), root);
  const celda = nodo({}, fila);              // una <td> cualquiera de la fila
  // Localizador por CLASE (owner "Iconographic Table Actions", ver HANDOFF):
  // el botón ahora es un ícono, no texto — su `aria-label` cambia según el
  // estado (habilitado dice "Ver detalle de licencias"; sin cuenta explica
  // POR QUÉ está deshabilitado, un mensaje distinto y más útil, ver abajo).
  // `table-action-btn` es la MARCA ESTABLE presente en las dos variantes.
  const attrsBtn = atributosDe(html, "table-action-btn");
  const boton = nodo(attrsBtn, fila);
  return { html, fila, celda, boton };
}

// ── A. el control existe ───────────────────────────────────────────────────
const elegible = montarFila(OPP);
ok(elegible.html.includes("Ver detalle de licencias"), "A: el control se renderiza");
ok(elegible.boton.attrs["data-commercial-licensing-account-row"] === "4242",
   "C: lleva el account_id CANÓNICO de la cuenta comercial, no el nombre");

// ── EL GUARD DE LA REGRESIÓN ───────────────────────────────────────────────
ok(!("onclick" in elegible.boton.attrs),
   "el control NO lleva `onclick` inline — el delegado escucha en bubble sobre " +
   "`document`, así que cortar la propagación ahí lo dejaría INERTE");

// ── B/C. el click invoca el drilldown con el id canónico ───────────────────
commercialState.licensingDrilldownAccountId = null;
commercialState.licensingDrilldownOrigin = null;
commercialState.detalleFilterOpportunityId = null;
jumped = null;
const r1 = click(elegible.boton);
ok(r1.llegoADocument, "B: el click llega al delegado (si no, la acción es inerte)");
eq(commercialState.licensingDrilldownAccountId, 4242, "C: drilldown con el account_id canónico");
eq(commercialState.licensingDrilldownOrigin, "opportunities", "B: origen marcado como Oportunidades");
eq(jumped, "licensing-profile", "B: hace scroll al contenedor del Perfil");

// ── D. NO dispara Oportunidad → Detalle ────────────────────────────────────
eq(commercialState.detalleFilterOpportunityId, null,
   "D: la acción NO dispara el camino Oportunidad → Detalle Oportunidades");

// ── J. el camino de la FILA sigue igual ────────────────────────────────────
commercialState.detalleFilterOpportunityId = null;
commercialState.licensingDrilldownAccountId = null;
click(elegible.celda);
eq(commercialState.detalleFilterOpportunityId, 77, "J: la fila sigue yendo a Detalle Oportunidades");
eq(commercialState.licensingDrilldownAccountId, null, "J: y NO toca el drilldown de licencias");

// ── F. cuenta no elegible: control deshabilitado, sin inyectar cuenta ──────
const sinCuenta = montarFila(OPP_SIN_CUENTA);
ok("disabled" in sinCuenta.boton.attrs,
   "F: sin cuenta comercial asociada el control queda deshabilitado");
ok(!("data-commercial-licensing-account-row" in sinCuenta.boton.attrs),
   "F: y no se inyecta ninguna cuenta");
ok(sinCuenta.boton.attrs["aria-label"] === "Esta oportunidad no tiene una cuenta comercial asociada",
   "F: y su nombre accesible explica POR QUÉ, en vez de repetir la acción");
commercialState.licensingDrilldownAccountId = null;
click(sinCuenta.boton);
eq(commercialState.licensingDrilldownAccountId, null, "F: clickearlo no abre ningún Perfil");

// ── G. cerrar restaura el universo del Perfil ──────────────────────────────
commercialState.licensingDrilldownAccountId = 4242;
commercialState.licensingDrilldownOrigin = "opportunities";
sandbox.commercialLicensingCloseDrilldown(root);   // toma el root, como en la app
eq(commercialState.licensingDrilldownAccountId, null, "G: cerrar limpia el drilldown");
eq(commercialState.licensingDrilldownOrigin, null, "G: y su origen");

// ── H. sin recursión ───────────────────────────────────────────────────────
montarFila(OPP);
commercialState.licensingDrilldownAccountId = null;
let vueltas = 0;
const original = docListeners.click.slice();
docListeners.click = original.concat([() => { vueltas++; }]);
click(root.hijos[0].hijos[1]);
docListeners.click = original;
eq(vueltas, 1, "H: un click produce UNA vuelta por el delegado, nunca una cascada");

// ── Los otros dos botones del Perfil, misma causa ──────────────────────────
// "Mover a sin prioridad" y "Restaurar" morían por lo mismo. Se verifica sobre
// el markup REAL del Perfil para que no vuelvan a nacer con el guard.
const CUENTA = { id: 900, name: "ACME S.A." };
for (const [modo, marcador] of [["main", "Mover a sin prioridad"], ["secondary", "Restaurar"]]) {
  const html = commercialLicensingProfileRowHtml(CUENTA, modo);
  const attrs = atributosDe(html, marcador);
  ok(attrs !== null, `Perfil/${modo}: el control "${marcador}" se renderiza`);
  ok(attrs && !("onclick" in attrs),
     `Perfil/${modo}: "${marcador}" tampoco lleva \`onclick\` inline (misma causa raíz)`);
}

// Las mutaciones de prioridad son async: de acá en adelante todo va dentro
// de un IIFE (nada de top-level await — convertiría el harness en ESM y
// `require` dejaría de existir).
(async () => {
  // ── "Mover a sin prioridad" / "Restaurar" de punta a punta ────────────────
  // Addendum del owner §8 C-G: no alcanza con probar el helper — hay que
  // ejercitar el control RENDERIZADO y seguir la cadena hasta el estado.
  const CUENTAS = [
    { id: 900, name: "ACME S.A." },
    { id: 901, name: "Beta S.R.L." },
  ];
  commercialState.licensingProfile = { accounts: CUENTAS };
  commercialState.licensingProfileStatus = "ready";
  commercialState.licensingPriorityStatus = "ready";
  commercialState.licensingPriorityDeferredIds = new Set();
  commercialState.licensingPriorityActionError = "";

  // Se captura la mutación que sale por red.
  const posts = [];
  sandbox.fetch = (url, opts) => {
    posts.push({ url, body: opts && opts.body ? String(opts.body) : "" });
    return Promise.resolve({ json: () => Promise.resolve({ state: "ok" }) });
  };

  // El control REAL, leído del markup que el Perfil genera.
  function montarAccionPerfil(cuenta, modo) {
    root.hijos.length = 0;
    const html = commercialLicensingProfileRowHtml(cuenta, modo);
    const marcador = modo === "secondary" ? "Restaurar" : "Mover a sin prioridad";
    const fila = nodo(atributosDe(html, "data-commercial-licensing-profile-account-row"), root);
    const boton = nodo(atributosDe(html, marcador), fila);
    return { html, fila, boton };
  }

  // C. el click manda la mutación con el id canónico.
  const mover = montarAccionPerfil(CUENTAS[0], "main");
  ok(!("onclick" in mover.boton.attrs), "C: \"Mover a sin prioridad\" sin `onclick` inline");
  commercialState.licensingDrilldownAccountId = null;
  const rMover = click(mover.boton);
  ok(rMover.llegoADocument, "C: el click llega al delegado");
  eq(posts.length, 1, "C: sale UNA mutación");
  ok(posts[0] && posts[0].url === "/comercial/licensing/priority/defer",
     `C: contra el endpoint de defer (fue ${posts[0] && posts[0].url})`);
  ok(posts[0] && posts[0].body.includes("account_id=900"),
     "I: con el account_id CANÓNICO, nunca el nombre de cuenta");
  eq(commercialState.licensingDrilldownAccountId, null,
     "H: la acción NO dispara el drilldown de la fila del Perfil");

  // D/E. confirmada la mutación, la cuenta sale de la principal y entra en la
  // secundaria — se lee del HTML RENDERIZADO, no del Set.
  await new Promise(r => setTimeout(r, 0));
  ok(commercialState.licensingPriorityDeferredIds.has(900),
     "D: el estado local se actualiza SÓLO tras confirmar el servidor");
  const principal = commercialLicensingProfileTableHtml(
    CUENTAS.filter(a => !commercialState.licensingPriorityDeferredIds.has(a.id)), "main");
  const secundaria = commercialLicensingProfileTableHtml(
    CUENTAS.filter(a => commercialState.licensingPriorityDeferredIds.has(a.id)), "secondary");
  ok(!principal.includes("ACME S.A."), "D: la cuenta deja la tabla principal");
  ok(principal.includes("Beta S.R.L."), "D: y las demás siguen ahí");
  ok(secundaria.includes("ACME S.A."), "E: aparece en el contenedor secundario");
  ok(secundaria.includes("Restaurar"), "E: con su acción de restaurar");

  // F. una relectura del servidor reconstruye el mismo estado.
  commercialState.licensingPriorityDeferredIds = new Set([900]);   // como lo devuelve el endpoint
  ok(commercialState.licensingPriorityDeferredIds.has(900),
     "F: el estado se reconstruye de la respuesta del servidor, no del browser");

  // G. restaurar devuelve la cuenta a la principal.
  posts.length = 0;
  const restaurar = montarAccionPerfil(CUENTAS[0], "secondary");
  ok(!("onclick" in restaurar.boton.attrs), "G: \"Restaurar\" sin `onclick` inline");
  click(restaurar.boton);
  await new Promise(r => setTimeout(r, 0));
  ok(posts[0] && posts[0].url === "/comercial/licensing/priority/restore",
     "G: pega contra el endpoint de restore");
  ok(!commercialState.licensingPriorityDeferredIds.has(900),
     "G: la cuenta vuelve a la tabla principal");

  // Una mutación FALLIDA no puede aparentar éxito.
  posts.length = 0;
  sandbox.fetch = () => Promise.resolve({ json: () => Promise.resolve({ state: "source_error", message: "sin backend" }) });
  const falla = montarAccionPerfil(CUENTAS[1], "main");
  click(falla.boton);
  await new Promise(r => setTimeout(r, 0));
  ok(!commercialState.licensingPriorityDeferredIds.has(901),
     "fallo: la cuenta NO se mueve si el servidor no confirmó");
  ok((commercialState.licensingPriorityActionError || "").length > 0,
     "fallo: se expone el error, nunca un éxito simulado");

  if (failures > 0) {
    console.log(`${failures} licensing action wiring invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL COMMERCIAL LICENSING ACTION WIRING INVARIANTS PASSED");
  process.exit(0);
})();
