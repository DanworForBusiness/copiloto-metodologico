// graph_auth_client.node.js — invocado por
// test_graph_auth_phase0.py::test_graph_auth_client_invariants_hold.
// GRAPH TOKEN LIFECYCLE — FASE 0, lado CLIENTE.
//
// Ejecuta el clasificador y los renderers REALES de reporting.js. Lo que un
// test de archivo no puede probar es justamente lo que importa acá: que un
// payload con `state: "auth_required"` termine produciendo el cuadro de
// "renovar sesion" y NO el de "no pudimos cargar desde SharePoint", y que un
// `source_error` siga produciendo el de fuente. Son cuatro payloads y dos
// cuadros: el defecto original era que tres de los cuatro caian en el mismo.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() { }, querySelector() { return null } },
  window: {},
  sessionStorage: { getItem() { return "" } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }) },
  console,
  HubForegroundProgress: { begin() { }, complete() { }, fail() { } },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const classify = vm.runInContext("reportingClassifyState", sandbox);
const authGateHtml = vm.runInContext("reportingAuthGateHtml", sandbox);
const authRequiredHtml = vm.runInContext("reportingAuthRequiredHtml", sandbox);
const sourceErrorHtml = vm.runInContext("reportingSourceErrorHtml", sandbox);
const state = vm.runInContext("state", sandbox);

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(a, e, label) {
  if (a !== e) { failures++; console.log(`FAIL: ${label}\n  esperado: ${e}\n  obtenido: ${a}`); }
}
// El flag es pegajoso a proposito (una vez que la autorizacion murio, murio
// para todos los tabs) — hay que resetearlo entre escenarios.
function reset() { state.graphAuthRequired = false; }

// --- A: los cuatro payloads del server se clasifican distinto -------------
reset();
assertEqual(classify({ state: "ok" }), "ready", "A: ok -> ready");
assertTrue(state.graphAuthRequired === false, "A: ok no marca la autorizacion como muerta");

reset();
assertEqual(classify({ state: "source_error", stage: "rentabilidad" }), "error",
  "A: source_error -> error");
assertTrue(state.graphAuthRequired === false,
  "A: una falla de FUENTE no se confunde con una de AUTORIZACION");

reset();
assertEqual(classify({ state: "auth_required", http_status: 401 }), "auth_required",
  "A: auth_required conserva su propio estado (no colapsa en error)");
assertTrue(state.graphAuthRequired === true, "A: auth_required marca la autorizacion como muerta");

reset();
assertEqual(classify({ state: "missing_token" }), "missing_token",
  "A: missing_token conserva su propio estado");
assertTrue(state.graphAuthRequired === true, "A: sin token tampoco hay autorizacion");

// Un payload desconocido o vacio cae en `error`, nunca en `auth_required`:
// inventar un pedido de reautenticacion sobre un estado que no entendemos
// mandaria al usuario a un logout que no lo desbloquea.
reset();
["config_incomplete", "", null, undefined, "cualquier_cosa_nueva"].forEach(s => {
  reset();
  assertEqual(classify(s === undefined ? undefined : { state: s }), "error",
    `A: estado inesperado (${String(s)}) -> error, no auth_required`);
  assertTrue(state.graphAuthRequired === false,
    `A: estado inesperado (${String(s)}) no pide reautenticar`);
});

// --- B: los dos cuadros son distinguibles --------------------------------
const auth = authRequiredHtml();
const fuente = sourceErrorHtml("los datos de Rentabilidad");
assertTrue(auth !== fuente, "B: el cuadro de autorizacion no es el de fuente");
// El de autorizacion nombra la accion que SI resuelve...
assertTrue(auth.includes("dwLogout()"), "B: ofrece el logout compartido del Hub");
assertTrue(auth.includes("Microsoft"), "B: dice con quien hay que renovar");
// ...y NO culpa a SharePoint, que es lo que el usuario leia antes.
assertTrue(!auth.includes("SharePoint"), "B: no le echa la culpa a la fuente");
assertTrue(!auth.includes("No pudimos cargar"), "B: no reusa la copy de fuente");
// El de fuente, al reves: no promete una reautenticacion que no va a arreglar
// una caida real de SharePoint.
assertTrue(!fuente.includes("dwLogout"), "B: el error de fuente no ofrece reautenticar");
assertTrue(fuente.includes("No pudimos cargar"), "B: el error de fuente conserva su copy");
// Y ninguno de los dos se disfraza de "no hay datos".
assertTrue(!auth.includes("Sin resultados"), "B: autorizacion != vacio");
assertTrue(!fuente.includes("Sin resultados"), "B: fuente != vacio");
// Usa el primitivo compartido del Hub, no markup nuevo.
assertTrue(auth.includes('<div class="dashboard-empty">'),
  "B: el cuadro de autorizacion usa el primitivo compartido");
assertTrue(auth.includes('class="dashboard-subhead__reset"'),
  "B: el boton usa el primitivo compartido de accion");

// --- C: el gate se abre para los DOS estados de autorizacion -------------
reset();
assertEqual(authGateHtml("auth_required"), auth, "C: auth_required abre el gate");
reset();
assertEqual(authGateHtml("missing_token"), auth, "C: missing_token abre el gate");
reset();
assertEqual(authGateHtml("error"), "", "C: un error de fuente NO abre el gate");
reset();
assertEqual(authGateHtml("ready"), "", "C: un tab que cargo bien NO abre el gate");

// --- D: transversal — un solo 401 cubre todos los tabs -------------------
// Los tabs comparten UNA autorizacion. Si el de Cartera devuelve 401, el de
// Rentabilidad no tiene por que reintentar para descubrir lo mismo: el flag
// pegajoso hace que su render tambien ofrezca renovar, sin otro round-trip.
reset();
classify({ state: "auth_required", http_status: 401 });
assertEqual(authGateHtml("ready"), auth,
  "D: con la autorizacion muerta, cualquier tab ofrece renovar");
assertEqual(authGateHtml(undefined), auth,
  "D: incluso un tab sin status propio la respeta");
// Sin el 401, ningun tab la inventa.
reset();
assertEqual(authGateHtml(undefined), "", "D: sin 401 previo no hay gate");

// --- E: todos los renderers de datos pasan por el gate ------------------
// Cobertura, no muestreo: si manana alguien agrega un `render*DataState` sin
// el gate, el tab volveria a mostrar "no pudimos cargar desde SharePoint"
// cuando el problema es la autorizacion.
const renderers = [...src.matchAll(/function (render\w*DataState)\s*\(/g)].map(m => m[1]);
assertTrue(renderers.length > 0, "E: se encontraron renderers de estado");
renderers.forEach(name => {
  const start = src.indexOf(`function ${name}(`);
  const body = src.slice(start, src.indexOf("\n}", start));
  assertTrue(body.includes("reportingAuthGateHtml"),
    `E: ${name} consulta el gate de autorizacion`);
  // Y lo consulta PRIMERO: si mirara el error de fuente antes, el 401 seguiria
  // saliendo como "no pudimos cargar".
  const gate = body.indexOf("reportingAuthGateHtml");
  const src_err = body.indexOf("reportingSourceErrorHtml");
  assertTrue(src_err === -1 || gate < src_err,
    `E: ${name} consulta el gate ANTES del error de fuente`);
});

// --- F: el gate no es un reload ------------------------------------------
// Owner: la recuperacion no puede ser recargar la pagina — perderia la
// ubicacion y no renovaria nada.
assertTrue(!auth.includes("reload") && !auth.includes("location"),
  "F: la recuperacion no es un reload");

if (failures > 0) {
  console.log(`${failures} Graph auth client invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL GRAPH AUTH CLIENT INVARIANTS PASSED");
process.exit(0);
