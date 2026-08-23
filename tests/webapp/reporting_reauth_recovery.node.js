// reporting_reauth_recovery.node.js — invocado por
// test_hub_auth_gesture.py::test_reporting_reauth_recovery_invariants_hold.
// GRAPH TOKEN LIFECYCLE — la RECUPERACION ACOTADA tras reautenticar.
//
// Owner: "retry the smallest useful failed component/request. Do NOT force
// logout. Do NOT require a full Hub reload if the component can retry.
// Preserve current vertical, filters and context."
//
// Ejecuta `reportingReauthenticate` REAL de reporting.js contra un
// `dwGraphReauthenticate` controlado, y verifica sobre el ESTADO lo que un test
// de archivo no puede: que se reintente solo el tab que el usuario esta
// mirando, que no se pierdan filtros ni perspectiva, que un dataset que ya
// habia cargado bien no se vuelva a pedir, y que cancelar no cambie nada.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(a, e, label) {
  if (a !== e) { failures++; console.log(`FAIL: ${label}\n  esperado: ${JSON.stringify(e)}\n  obtenido: ${JSON.stringify(a)}`); }
}

function makeEnv(reauthResult) {
  const fetched = [];
  const win = { location: { origin: "https://hub.danwor.com", pathname: "/reporting", search: "" } };
  win.self = win; win.top = win;
  let reauthCalls = 0;
  let tokenActual = "";
  win.dwGraphReauthenticate = async function () {
    reauthCalls += 1;
    if (reauthResult) tokenActual = "AT-TRAS-REAUTH";
    return reauthResult;
  };
  // Si `reportingReauthenticate` llamara al logout, este espia lo delata: el
  // owner fue explicito en que la recuperacion NO puede forzar logout.
  let logoutCalls = 0;
  win.dwLogout = function () { logoutCalls += 1; };
  let reloads = 0;
  const sandbox = {
    window: win,
    location: win.location,
    document: {
      addEventListener() { },
      querySelector() { return null },
      getElementById() { return { innerHTML: "", classList: { add() { }, remove() { }, toggle() { } } } },
      body: { classList: { add() { }, remove() { }, toggle() { } } },
    },
    sessionStorage: { getItem() { return "" } },
    URLSearchParams,
    // El token que la AUTORIDAD entrega. Arranca vacio (autorizacion vencida)
    // y la reautenticacion exitosa lo enciende: asi el retry recorre el flujo
    // real —token vacio, cuadro, gesto, token, datos— en vez de un escenario
    // donde el token nunca cambia.
    dwGraphToken: async () => tokenActual,
    HubForegroundProgress: { begin() { }, complete() { }, fail() { }, settle() { } },
    setInterval() { return 0 }, clearInterval() { },
    setTimeout, Promise, Object, JSON, Date, Error, console,
    fetch(url) {
      fetched.push(String(url));
      // El server responde segun el token que le llego: sin token,
      // `missing_token` (que es lo que la Fase 0 traduce al cuadro); con token,
      // datos. Un stub que respondiera siempre lo mismo no podria distinguir
      // "reautentico y recupero" de "reautentico y sigue roto".
      const body = tokenActual
        ? { state: "ok", rentabilidad: {}, cartera: {}, participacion: [], capacidad: {} }
        : { state: "missing_token" };
      return Promise.resolve({ json: () => Promise.resolve(body) });
    },
    initDashboardSections() { },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "reporting.js" });
  return {
    sandbox, fetched,
    state: vm.runInContext("state", sandbox),
    reauth: vm.runInContext("reportingReauthenticate", sandbox),
    datasets: vm.runInContext("REPORTING_TAB_DATASETS", sandbox),
    reauthCalls: () => reauthCalls,
    logoutCalls: () => logoutCalls,
    reloads: () => reloads,
  };
}

// El retry pasa por `await dwGraphToken()` y despues por el fetch, asi que
// hay varios saltos de microtask entre el click y la request. Se drena hasta
// que la cola queda quieta en vez de adivinar un numero de ticks.
async function drain() {
  for (let i = 0; i < 12; i += 1) await new Promise(r => setImmediate(r));
}

(async function main() {
  // ── A: el mapa cubre TODOS los tabs ─────────────────────────────────────
  // Si un tab quedara afuera, su cuadro de "renovar sesion" reautenticaria y
  // despues no reintentaria nada: el usuario veria el mismo cuadro para siempre.
  {
    const env = makeEnv(true);
    const tabs = Object.keys(vm.runInContext("TAB_HYDRATION_TRIGGERS", env.sandbox));
    const cubiertos = Object.keys(env.datasets);
    tabs.forEach(t => assertTrue(cubiertos.indexOf(t) !== -1,
      `A: el tab "${t}" tiene datasets declarados para el retry`));
    // Y todo dataset nombrado existe de verdad en el estado.
    Object.entries(env.datasets).forEach(([tab, keys]) => {
      keys.forEach(k => assertTrue(k in env.state,
        `A: ${tab} -> state.${k} existe (si no, el retry no resetea nada)`));
    });
  }

  // ── B: RETRY ACOTADO — solo el tab activo ───────────────────────────────
  {
    const env = makeEnv(true);
    env.state.experience = "rentabilidad";
    env.state.graphAuthRequired = true;
    env.state.rentabilidadDataStatus = "auth_required";
    // Otros tabs tambien fallados: NO deben tocarse.
    env.state.horasVendidasDataStatus = "auth_required";
    env.state.batchBDataStatus = "auth_required";

    const ok = await env.reauth();
    await drain(); await drain();

    assertEqual(ok, true, "B: informa que recupero");
    assertEqual(env.reauthCalls(), 1, "B: reautentica UNA vez");
    assertEqual(env.state.graphAuthRequired, false, "B: baja el flag transversal");
    // El tab activo volvio a pedir.
    assertTrue(env.fetched.some(u => u.includes("/data/rentabilidad")),
      "B: el tab ACTIVO vuelve a pedir sus datos");
    // Los otros NO.
    assertTrue(!env.fetched.some(u => u.includes("/data/horas-vendidas")),
      "B: un tab que el usuario no esta mirando NO se reintenta");
    assertTrue(!env.fetched.some(u => u.includes("/data/batch-b")),
      "B: tampoco otro tab fallado");
    assertEqual(env.state.horasVendidasDataStatus, "auth_required",
      "B: y su status queda como estaba (se recupera cuando lo visite)");
  }

  // ── C: NO fuerza logout, NO recarga ─────────────────────────────────────
  {
    const env = makeEnv(true);
    env.state.experience = "cartera";
    env.state.carteraMantenimientoStatus = "auth_required";
    await env.reauth();
    await drain();
    assertEqual(env.logoutCalls(), 0,
      "C: la recuperacion NO cierra sesion — eso cobraba de mas por renovar datos");
    assertEqual(env.reloads(), 0, "C: y no recarga el Hub");
    // El codigo tampoco lo nombra.
    const fn = src.slice(src.indexOf("async function reportingReauthenticate"));
    const body = fn.slice(0, fn.indexOf("\n}") + 2);
    assertTrue(!body.includes("dwLogout"), "C: `reportingReauthenticate` no menciona el logout");
    assertTrue(!body.includes("location.reload") && !body.includes("location.href"),
      "C: ni recarga ni navega");
  }

  // ── D: PRESERVA vertical, filtros y contexto ────────────────────────────
  {
    const env = makeEnv(true);
    env.state.experience = "capacidad";
    env.state.batchBDataStatus = "auth_required";
    env.state.perspective.cartera = "Proyecto";
    env.state.filters.rentabilidad = { cliente: ["ACME"] };
    const antesExp = env.state.experience;
    const antesPersp = env.state.perspective.cartera;
    const antesFiltros = JSON.stringify(env.state.filters.rentabilidad);

    await env.reauth();
    await drain();

    assertEqual(env.state.experience, antesExp, "D: no cambia la vertical/tab activa");
    assertEqual(env.state.perspective.cartera, antesPersp, "D: no toca las perspectivas");
    assertEqual(JSON.stringify(env.state.filters.rentabilidad), antesFiltros,
      "D: no toca los filtros");
  }

  // ── E: un dataset que YA cargo bien no se vuelve a pedir ────────────────
  // Comercial tiene dos familias. Si una anduvo y la otra no, reautenticar no
  // puede tirar a la basura la que andaba.
  {
    const env = makeEnv(true);
    env.state.experience = "comercial";
    env.state.comercialParticipacionDataStatus = "auth_required";
    env.state.batchADataStatus = "ready";           // esta ya cargo
    await env.reauth();
    await drain();
    assertEqual(env.state.batchADataStatus, "ready",
      "E: el dataset que ya estaba listo sigue listo");
    // Mirar solo el status final NO alcanza: si el retry lo resetea, el trigger
    // lo vuelve a cargar y termina en "ready" igual. Lo observable es si se lo
    // VOLVIO A PEDIR — un fetch de mas contra SharePoint por una autorizacion
    // que no era la suya. (Lo descubri con el mutation test: la version que
    // miraba el status pasaba con el reset puesto.)
    assertTrue(!env.fetched.some(u => u.includes("/data/batch-a")),
      "E: y NO se lo vuelve a pedir");
    assertTrue(env.fetched.some(u => u.includes("/data/participacion")),
      "E: el que fallo si se reintenta");
  }

  // ── F: cancelar la reautenticacion no cambia NADA ───────────────────────
  {
    const env = makeEnv(false);                     // el usuario cierra el popup
    env.state.experience = "rentabilidad";
    env.state.graphAuthRequired = true;
    env.state.rentabilidadDataStatus = "auth_required";
    const ok = await env.reauth();
    await drain();
    assertEqual(ok, false, "F: informa que no recupero");
    assertEqual(env.state.graphAuthRequired, true,
      "F: el flag sigue arriba — el cuadro sigue ofreciendo la accion");
    assertEqual(env.state.rentabilidadDataStatus, "auth_required",
      "F: el status no se toca");
    assertEqual(env.fetched.length, 0, "F: no se dispara ningun fetch");
  }

  // ── G: sin la autoridad cargada, no explota ─────────────────────────────
  // `hub_auth.js` podria no estar (o fallar en cargar): el boton no puede
  // tirar una excepcion sin dueño en la cara del usuario.
  {
    const env = makeEnv(true);
    delete env.sandbox.window.dwGraphReauthenticate;
    let threw = false, v = "sentinel";
    try { v = await env.reauth(); } catch (e) { threw = true; }
    assertTrue(!threw, "G: sin la autoridad no lanza");
    assertEqual(v, false, "G: informa que no recupero");
  }

  // ── H: el cuadro ofrece la reautenticacion, NO el logout ────────────────
  {
    const env = makeEnv(true);
    const html = vm.runInContext("reportingAuthRequiredHtml", env.sandbox)();
    assertTrue(html.includes("reportingReauthenticate()"),
      "H: el boton dispara la reautenticacion compartida");
    assertTrue(!html.includes("dwLogout"),
      "H: y ya NO cierra sesion (perdia ubicacion y filtros por renovar datos)");
    assertTrue(html.includes("Renovar sesión"), "H: conserva la copy aprobada");
  }

  if (failures > 0) {
    console.log(`${failures} Reporting reauth recovery invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL REPORTING REAUTH RECOVERY INVARIANTS PASSED");
  process.exit(0);
})();
