// hub_logout_next.node.js — invocado por
// test_hub_logout_next.py::test_hub_logout_next_invariants_hold.
// HUB — PRESERVE CURRENT LOCATION ACROSS LOGOUT / LOGIN (owner contract).
// Ejecuta el `dwLogout` REAL extraído de app.js contra un `window.location`,
// `fetch` y `URLSearchParams` controlados — mismo patrón que
// hub_foreground_progress.node.js.
//
// Lo que se prueba es el lado CLIENTE del contrato: que el destino se derive
// de la ubicación actual (transversal, sin una rama por capability), que
// `next` viaje RELATIVO (sin origin, sin hash) y con el encoding exacto que
// el server espera, y que el POST de logout siga ocurriendo primero. La
// VALIDACIÓN de `next` es del server y ya tiene su cobertura
// (test_onedanwor_routes.py / test_platform_routes.py) — acá no se
// re-implementa esa regla, se comprueba que no se la contradiga.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "app.js");
const src = fs.readFileSync(APP_JS, "utf-8");
const start = src.indexOf("window.dwLogout = function () {");
if (start === -1) {
  console.log("FAIL: no se encontró window.dwLogout en app.js");
  process.exit(1);
}
const end = src.indexOf("\n};", start);
if (end === -1) {
  console.log("FAIL: no se pudo delimitar dwLogout en app.js");
  process.exit(1);
}
const snippet = src.slice(start, end + 3);

// `dwLogout` delega la limpieza de la credencial en `dwClearGraphAuth`, que
// vive en la autoridad compartida (hub_auth.js). Este harness NO carga la
// autoridad: la espia. Lo que se prueba aca es la NAVEGACION post-logout —el
// contrato de `next`—, y el ciclo de vida de la credencial es de
// hub_auth_authority.node.js, que lo cubre entero. Mezclar los dos obligaria a
// arrastrar MSAL a un harness de navegacion.

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(actual, expected, label) {
  if (actual !== expected) { failures++; console.log(`FAIL: ${label}\n  esperado: ${expected}\n  obtenido: ${actual}`); }
}

// Corre `dwLogout` con una ubicación dada y devuelve lo observable: los fetch
// que hizo, el href final, y cualquier intento de tocar almacenamiento.
function runLogout(location) {
  const calls = [];
  const forbidden = [];
  const loc = Object.assign({ href: "", origin: "https://hub.danwor.com" }, location);
  // sessionStorage: se ESPIA en vez de prohibirse — el logout ahora SI debe
  // limpiar el artefacto de Graph (higiene de auth), pero nada mas.
  const storageOps = [];
  const sessionTrap = {
    removeItem(k) { storageOps.push(`removeItem:${k}`); },
    getItem(k) { storageOps.push(`getItem:${k}`); return null; },
    setItem(k) { storageOps.push(`setItem:${k}`); },
    clear() { storageOps.push("clear"); },
  };
  const storageTrap = new Proxy({}, {
    get(_t, prop) { forbidden.push(`storage.${String(prop)}`); return () => undefined; },
    set(_t, prop) { forbidden.push(`storage.${String(prop)}`); return true; },
  });
  const documentTrap = {
    // hub_auth.js registra sus hooks de htmx al cargar: eso es la AUTORIDAD
    // inicializandose, no el logout tocando el DOM.
    addEventListener() { },
    get cookie() { forbidden.push("document.cookie(get)"); return ""; },
    set cookie(v) { forbidden.push("document.cookie(set)"); },
    querySelector() { forbidden.push("document.querySelector"); return null; },
    getElementById() { forbidden.push("document.getElementById"); return null; },
  };
  // La limpieza de la credencial la hace la AUTORIDAD (hub_auth.js). Aca se
  // espia que el logout la INVOQUE — que hace por dentro es de
  // hub_auth_authority.node.js, que lo cubre entero. Traer MSAL a este
  // harness seria mezclar dos contratos.
  const authorityOps = [];
  const sandbox = {
    window: { location: loc },
    location: loc,
    localStorage: storageTrap,
    sessionStorage: sessionTrap,
    document: documentTrap,
    URLSearchParams,
    encodeURIComponent,
    fetch(url, init) {
      calls.push({ url, method: init && init.method });
      return { finally(cb) { cb(); return this; } };
    },
    console,
  };
  vm.createContext(sandbox);
  // El espia de la autoridad, en su lugar real (window).
  sandbox.window.dwClearGraphAuth = function () { authorityOps.push("dwClearGraphAuth"); };
  vm.runInContext(snippet, sandbox, { filename: "dwLogout.js" });
  vm.runInContext("window.dwLogout()", sandbox);
  return { calls, href: loc.href, forbidden, storageOps, authorityOps };
}

// --- A: el contrato textual del owner ---------------------------------------
const comercial = runLogout({ pathname: "/comercial", search: "" });
assertEqual(comercial.href, "/onedanwor?next=%2Fcomercial",
  "A: logout desde /comercial deja al usuario en /onedanwor?next=%2Fcomercial");

// --- B: transversal — la misma construcción para cualquier capability -------
// Ninguna de estas rutas está nombrada en el código: el destino sale de la
// ubicación actual. Si alguien agregara una rama por capability, alguna de
// estas dejaría de derivarse igual.
const OTRAS = [
  ["/reporting", "", "/onedanwor?next=%2Freporting"],
  ["/copilot", "", "/onedanwor?next=%2Fcopilot"],
  ["/operaciones/staffing/asignaciones", "", "/onedanwor?next=%2Foperaciones%2Fstaffing%2Fasignaciones"],
  ["/modules/01_Arquitectura_y_Conectividad", "", "/onedanwor?next=%2Fmodules%2F01_Arquitectura_y_Conectividad"],
  ["/academy/intro", "", "/onedanwor?next=%2Facademy%2Fintro"],
];
OTRAS.forEach(([pathname, search, expected]) => {
  assertEqual(runLogout({ pathname, search }).href, expected, `B: logout desde ${pathname}`);
});

// --- C: pathname + search, con el encoding que el server espera -------------
const conQuery = runLogout({ pathname: "/reporting", search: "?view=foo" });
assertEqual(conQuery.href, "/onedanwor?next=%2Freporting%3Fview%3Dfoo",
  "C: el query string se preserva dentro de `next`");
const multiQuery = runLogout({ pathname: "/comercial", search: "?tipo=nn&tab=actividades" });
assertEqual(multiQuery.href, "/onedanwor?next=%2Fcomercial%3Ftipo%3Dnn%26tab%3Dactividades",
  "C: varios parámetros se preservan y el `&` interno queda encodeado");
// SIN doble encoding: el `next` decodificado una sola vez tiene que volver a
// ser exactamente la ubicación original.
const decoded = new URLSearchParams(multiQuery.href.split("?").slice(1).join("?")).get("next");
assertEqual(decoded, "/comercial?tipo=nn&tab=actividades",
  "C: un solo decode devuelve la ubicación original (sin doble encoding)");
assertTrue(!multiQuery.href.includes("%25"), "C: no aparece %25 (señal de doble encoding)");

// --- D: `next` es RELATIVO — nunca lleva origin ni hash ---------------------
const conHash = runLogout({ pathname: "/comercial", search: "", hash: "#detalle" });
assertEqual(conHash.href, "/onedanwor?next=%2Fcomercial",
  "D: el fragmento no entra en `next` (no viaja al server)");
[comercial, conQuery, conHash].forEach((r, i) => {
  assertTrue(!r.href.includes("http"), `D${i}: next no contiene una URL absoluta`);
  assertTrue(!r.href.includes("hub.danwor.com"), `D${i}: next no contiene el host`);
});

// --- E: el server sigue siendo la autoridad sobre `next` -------------------
// El cliente NO sanitiza: manda la ubicación tal cual. Si `location.pathname`
// fuera algo que el server rechaza (arranca con "//"), se manda igual y el
// server cae en su default — lo que NO puede pasar es que el cliente arme un
// destino externo por su cuenta. Acá se verifica justamente eso: el usuario
// termina en /onedanwor, no en el host ajeno.
const raro = runLogout({ pathname: "//evil.example/x", search: "" });
assertTrue(raro.href.startsWith("/onedanwor?"),
  "E: incluso con un pathname raro el destino sigue siendo /onedanwor (relativo)");
assertTrue(!raro.href.startsWith("//") && !raro.href.startsWith("http"),
  "E: el destino nunca es una URL externa");
assertEqual(new URLSearchParams(raro.href.split("?")[1]).get("next"), "//evil.example/x",
  "E: el cliente no sanitiza — manda la ubicación y deja que el server la rechace");

// --- F: sigue siendo un logout, y sigue siendo inmediato --------------------
assertEqual(comercial.calls.length, 1, "F: exactamente una request");
assertEqual(comercial.calls[0].url, "/api/auth/logout", "F: la request es el logout de siempre");
assertEqual(comercial.calls[0].method, "POST", "F: sigue siendo POST");
// Sin modal, sin confirmación, sin estado propio: nada de localStorage,
// cookies ni DOM. `sessionStorage` YA NO está prohibido — se espía aparte.
const tocado = [comercial, conQuery, raro].flatMap(r => r.forbidden);
assertTrue(tocado.length === 0, `F: no toca localStorage/cookie/DOM (tocó: ${tocado.join(", ")})`);
assertTrue(!snippet.includes("confirm(") && !snippet.includes("localStorage")
  && !snippet.includes("document.cookie"),
  "F: el código del logout no introduce confirmación ni persistencia propia");

// --- F2: HIGIENE DE AUTH — el logout baja la credencial ------------------
// Antes del corrective de Fase 0 la cookie moria y el token de Graph seguia
// vivo en el browser. La limpieza es de la AUTORIDAD (hub_auth.js): el chrome
// decide CUANDO, la autoridad sabe QUE. Lo que se verifica aca es la
// delegacion; el contenido de esa limpieza (bajar la cuenta de MSAL y vaciar
// su cache) lo cubre hub_auth_authority.node.js.
[comercial, conQuery, raro].forEach((r, i) => {
  assertEqual(r.authorityOps.join("|"), "dwClearGraphAuth",
    `F2[${i}]: el logout invoca la autoridad, exactamente una vez`);
});
// El chrome NO conoce el mecanismo: no toca storage ni nombra credenciales.
[comercial, conQuery, raro].forEach((r, i) => {
  assertEqual(r.storageOps.length, 0,
    `F2[${i}]: el logout no toca el storage por su cuenta`);
});
assertTrue(!src.includes("dw-graph-token") && !src.includes("msal"),
  "F2: app.js no nombra la credencial ni el proveedor");
// Y la limpieza ocurre ANTES del fetch: si la red falla, ya se bajo.
assertTrue(snippet.indexOf("dwClearGraphAuth()") < snippet.indexOf("fetch("),
  "F2: limpia antes del fetch — una red caida no puede dejar la credencial viva");

// --- G: el destino no está cableado a ninguna capability -------------------
assertTrue(!snippet.includes("/comercial") && !snippet.includes("/reporting")
  && !snippet.includes("/copilot") && !snippet.includes("/operaciones"),
  "G: el logout no nombra ninguna capability");

if (failures > 0) {
  console.log(`${failures} Hub logout-next invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUB LOGOUT NEXT INVARIANTS PASSED");
process.exit(0);
