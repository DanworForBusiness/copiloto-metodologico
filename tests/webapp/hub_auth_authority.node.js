// hub_auth_authority.node.js — invocado por
// test_hub_auth_authority.py::test_hub_auth_authority_invariants_hold.
// GRAPH TOKEN LIFECYCLE — FASE 1B: la autoridad con MSAL detras.
//
// Ejecuta hub_auth.js REAL contra un doble de MSAL que respeta su API publica
// (`PublicClientApplication`, `initialize`, `get/setActiveAccount`,
// `getAllAccounts`, `acquireTokenSilent`, `acquireTokenPopup`, `loginPopup`,
// `clearCache`, `InteractionRequiredAuthError`) y un htmx simulado que
// reproduce el ORDEN verificado en el artefacto pinneado (htmx 2.0.4).
//
// Por que un doble y no MSAL de verdad: MSAL real necesita un tenant, un
// browser y un usuario. Lo que hay que probar aca es la LOGICA DE LA AUTORIDAD
// —a quien le pide el token, en que orden, cuando escala a interactivo, y que
// dos consumidores concurrentes no abran dos popups—, y eso se prueba con un
// doble que devuelva lo que MSAL devolveria. El comportamiento de MSAL contra
// Entra es del smoke real del owner, que es donde corresponde.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HUB_AUTH = path.resolve(__dirname, "..", "..", "webapp", "static", "hub_auth.js");
const src = fs.readFileSync(HUB_AUTH, "utf-8");

let failures = 0;
function assertTrue(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }
function assertEqual(a, e, label) {
  if (a !== e) { failures++; console.log(`FAIL: ${label}\n  esperado: ${JSON.stringify(e)}\n  obtenido: ${JSON.stringify(a)}`); }
}

// ── el doble de MSAL ──────────────────────────────────────────────────────
// `behaviour` decide que hace cada llamada; `log` registra QUE se llamo, que
// es lo que casi todos los invariantes de abajo miran.
function makeMsal(behaviour, captured) {
  const b = behaviour || {};
  const log = [];
  class AuthError extends Error {
    constructor(code) { super(code); this.errorCode = code; this.name = "AuthError"; }
  }
  class InteractionRequiredAuthError extends AuthError { }

  class PublicClientApplication {
    constructor(config) { log.push("new"); captured.config = config; this._active = null; }
    async initialize() { log.push("initialize"); if (b.initializeThrows) throw new Error("init fallo"); }
    getAllAccounts() { return (b.accounts || []).slice(); }
    getActiveAccount() { return this._active; }
    setActiveAccount(a) { log.push("setActiveAccount:" + (a ? a.username : "null")); this._active = a; }
    async acquireTokenSilent(req) {
      log.push("acquireTokenSilent");
      captured.silentScopes = req.scopes;
      if (b.silent === "interaction") throw new InteractionRequiredAuthError("interaction_required");
      if (b.silent === "network") throw new AuthError("network_error");
      return { accessToken: b.silentToken || "AT-SILENT", fromCache: true, expiresOn: new Date(0) };
    }
    async acquireTokenPopup(req) {
      log.push("acquireTokenPopup");
      if (b.popupDelayMs) await new Promise(r => setTimeout(r, b.popupDelayMs));
      if (b.popup === "cancel") throw new AuthError("user_cancelled");
      return { accessToken: b.popupToken || "AT-POPUP" };
    }
    async loginPopup(req) {
      log.push("loginPopup:" + JSON.stringify(req.scopes) + ":" + req.prompt);
      if (b.login === "cancel") throw new AuthError("user_cancelled");
      return { idToken: "ID-TOKEN", account: { username: "joe@danwor.com" } };
    }
    async clearCache() { log.push("clearCache"); }
  }
  return { msal: { PublicClientApplication, InteractionRequiredAuthError, AuthError }, log };
}

// ── el entorno ────────────────────────────────────────────────────────────
function makeEnv(behaviour, opts) {
  const options = opts || {};
  const captured = {};
  const { msal, log } = makeMsal(behaviour, captured);
  const listeners = {};
  const fetches = [];
  const store = {};
  const sessionStorage = {
    getItem(k) { if (options.storageThrows) throw new Error("particionado"); return k in store ? store[k] : null; },
    setItem(k, v) { if (options.storageThrows) throw new Error("particionado"); store[k] = v; },
    removeItem(k) { if (options.storageThrows) throw new Error("particionado"); delete store[k]; },
  };
  const win = { location: { origin: "https://hub.danwor.com", pathname: "/reporting", search: "" } };
  win.self = win;
  win.top = options.embedded ? { otro: true } : win;
  const sandbox = {
    window: win, msal, sessionStorage, console, Promise, setTimeout, Object, JSON, Date, Error,
    document: { addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); } },
    fetch(url, init) {
      fetches.push({ url: String(url), method: (init && init.method) || "GET", body: init && init.body });
      if (String(url).indexOf("/api/auth/microsoft/config") === 0) {
        const cfg = options.config === "disabled"
          ? { enabled: false }
          : { enabled: true, tenant_id: "TENANT", client_id: "CLIENT" };
        return Promise.resolve({ ok: options.configHttpOk !== false, json: () => Promise.resolve(cfg) });
      }
      if (String(url).indexOf("/api/auth/microsoft/token") === 0) {
        return Promise.resolve({
          ok: options.sessionOk !== false,
          json: () => Promise.resolve(options.sessionOk === false
            ? { detail: { message: "no autorizado", code: "microsoft_user_not_authorized" } }
            : { ok: true }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "hub_auth.js" });
  return { win, listeners, msalLog: log, fetches, store, captured };
}

function makeElt(marked, id) {
  const elt = { id: id || "elt", marked };
  elt.closest = (sel) => (marked && sel === "[data-dw-graph-token]" ? elt : null);
  return elt;
}

// htmx 2.0.4 simulado — mismo orden que el artefacto pinneado.
function makeHtmx(env) {
  const issued = [];
  const order = [];
  const queued = [];
  let queueNext = false;
  function issueAjaxRequest(elt, confirmed) {
    order.push(`issueAjaxRequest(confirmed=${!!confirmed})`);
    // htmx 2.0.4 linea 4290: con un xhr en vuelo el request se ENCOLA y
    // retorna ANTES de `getExpressionVars` (4341). Lo encolado se reemite en
    // 4276-4287 SIN el 6o argumento, asi que reentra por el gate.
    if (confirmed && queueNext) {
      queueNext = false;
      order.push("ENCOLADO (sin armar params)");
      queued.push(() => issueAjaxRequest(elt, undefined));
      return;
    }
    if (confirmed === undefined) {
      let prevented = false;
      const detail = {
        elt,
        issueRequest(skip) { return issueAjaxRequest(elt, !!skip); },
        preventDefault() { prevented = true; },
      };
      (env.listeners["htmx:confirm"] || []).forEach(fn => fn({ detail, preventDefault: detail.preventDefault }));
      order.push(`confirm -> prevented=${prevented}`);
      if (prevented) return;
    }
    const params = {};
    (env.listeners["htmx:configRequest"] || []).forEach(fn => fn({ detail: { elt, parameters: params } }));
    order.push("configRequest");
    issued.push({ elt: elt.id, params });
    order.push("REQUEST SALE");
  }
  return {
    fire: (elt) => issueAjaxRequest(elt, undefined),
    queueTheRetry: () => { queueNext = true; },
    drainQueue: () => { const q = queued.splice(0); q.forEach(f => f()); },
    queuedCount: () => queued.length,
    issued, order,
  };
}

const drain = () => new Promise(r => setImmediate(r));
const ACCOUNT = { username: "joe@danwor.com", homeAccountId: "hid" };

(async function main() {
  // ── A: token en cache -> se devuelve sin interaccion ────────────────────
  {
    const env = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-EN-CACHE" });
    assertEqual(await env.win.dwGraphToken(), "AT-EN-CACHE", "A: devuelve el access token vigente");
    assertTrue(env.msalLog.includes("acquireTokenSilent"), "A: lo pide a MSAL, no a un storage propio");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"),
      "A: con token valido NO abre ninguna ventana");
    assertEqual(env.msalLog.filter(l => l === "new").length, 1, "A: UNA PublicClientApplication");
    assertEqual(env.msalLog.filter(l => l === "initialize").length, 1, "A: initialize() una vez");
    // La instancia se comparte: un segundo pedido no crea otra.
    await env.win.dwGraphToken();
    assertEqual(env.msalLog.filter(l => l === "new").length, 1,
      "A: el segundo pedido reusa la misma instancia");
  }

  // ── B: RENOVACION SILENCIOSA — el fix ──────────────────────────────────
  // MSAL resuelve `acquireTokenSilent` con su propia cache. La app no sabe ni
  // tiene por que saber si eso salio de cache, de un refresh token o de la red.
  {
    const env = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-RENOVADO" });
    assertEqual(await env.win.dwGraphToken(), "AT-RENOVADO",
      "B: la renovacion silenciosa devuelve token nuevo sin molestar al usuario");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"), "B: sin popup");
    assertEqual(JSON.stringify(env.captured.silentScopes), '["User.Read","Sites.Read.All"]',
      "B: renueva con los MISMOS permisos de Graph");
  }

  // ── C: InteractionRequired -> popup; cualquier otro fallo -> vacio ─────
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popupToken: "AT-TRAS-POPUP" });
    assertEqual(await env.win.dwGraphToken(), "AT-TRAS-POPUP",
      "C: cuando el silencioso no alcanza, escala al popup");
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 1, "C: UN popup");
  }
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "network" });
    assertEqual(await env.win.dwGraphToken(), "", "C: un fallo de red devuelve vacio");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"),
      "C: un fallo de red NO abre un popup — no esta en manos del usuario");
  }
  // SIN CUENTA no se abre NADA. Es la linea entre un popup "controlado" y uno
  // intruso: `dwGraphToken()` lo dispara la CARGA de un tab de Reporting, asi
  // que escalar ahi abriria una ventana de Microsoft no pedida en cada carga —y
  // el browser la bloquearia igual, porque no hay gesto de usuario detras—.
  {
    const env = makeEnv({ accounts: [], popupToken: "AT-NO-DEBERIA" });
    assertEqual(await env.win.dwGraphToken(), "",
      "C: sin cuenta devuelve vacio y deja que la Fase 0 ofrezca la accion");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"),
      "C: sin cuenta NO abre un popup no solicitado");
    assertTrue(!env.msalLog.includes("acquireTokenSilent"),
      "C: y sin cuenta no tiene sentido intentar silencioso");
  }
  // CON cuenta, cancelar el popup devuelve vacio — no una excepcion sin dueño.
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popup: "cancel" });
    let threw = false, v = "sentinel";
    try { v = await env.win.dwGraphToken(); } catch (e) { threw = true; }
    assertTrue(!threw, "C: cancelar el popup no propaga excepcion");
    assertEqual(v, "", "C: cancelar el popup devuelve vacio (la Fase 0 lo explica)");
  }

  // ── D: CONCURRENCIA — N consumidores, UN popup ─────────────────────────
  // Sin esto, seis tabs de Reporting con la autorizacion vencida abrian SEIS
  // ventanas de Microsoft. Es el invariante mas importante de este archivo.
  {
    const env = makeEnv({
      accounts: [ACCOUNT], silent: "interaction", popupToken: "AT-COMPARTIDO", popupDelayMs: 20,
    });
    const results = await Promise.all([
      env.win.dwGraphToken(), env.win.dwGraphToken(), env.win.dwGraphToken(),
      env.win.dwGraphToken(), env.win.dwGraphToken(), env.win.dwGraphToken(),
    ]);
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 1,
      "D: seis pedidos concurrentes abren UN solo popup");
    assertTrue(results.every(t => t === "AT-COMPARTIDO"), "D: los seis reciben el mismo token");
    // Y el candado se libera: un pedido POSTERIOR si puede volver a interactuar.
    await env.win.dwGraphToken();
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 2,
      "D: el candado se libero (no queda trabado para siempre)");
  }

  // ── E: LOGIN — MSAL abre el popup, el BACKEND crea la sesion ───────────
  {
    // `dwHubLogin` SI nace de un gesto del usuario (el boton "Ingresar"), asi
    // que aca el popup sin cuenta es exactamente lo correcto.
    const env = makeEnv({ accounts: [] });
    const res = await env.win.dwHubLogin();
    assertTrue(!!res && res.ok === true, "E: el login resuelve con la respuesta del backend");
    const login = env.msalLog.find(l => l.indexOf("loginPopup") === 0);
    assertTrue(!!login, "E: el popup lo abre MSAL");
    assertTrue(login.includes('["User.Read","Sites.Read.All"]'),
      "E: pide los MISMOS permisos de Graph que antes");
    // Los scopes de OIDC (openid/profile/offline_access) NO los pide la app:
    // son `defaultScopes` de MSAL. Pedirlos a mano seria gestionar el protocolo.
    assertTrue(!login.includes("offline_access") && !login.includes("openid"),
      "E: la app NO gestiona los scopes de OIDC — son de MSAL");
    assertTrue(login.includes("select_account"), "E: preserva prompt=select_account");
    const sesion = env.fetches.find(f => f.url.indexOf("/api/auth/microsoft/token") === 0);
    assertTrue(!!sesion, "E: hay POST al backend para crear la sesion del Hub");
    assertEqual(sesion.method, "POST", "E: sigue siendo POST");
    assertTrue(String(sesion.body).includes("ID-TOKEN"), "E: viaja el id_token validado");
    assertTrue(!String(sesion.body).includes("AT-"), "E: NO viaja ningun access token al backend");
    assertTrue(env.msalLog.includes("setActiveAccount:joe@danwor.com"),
      "E: la cuenta queda activa para que el silencioso funcione despues");
  }
  {
    const env = makeEnv({ accounts: [] }, { sessionOk: false });
    let detail = null;
    try { await env.win.dwHubLogin(); } catch (e) { detail = e.detail; }
    assertTrue(!!detail && detail.code === "microsoft_user_not_authorized",
      "E: el detalle de 'acceso pendiente' del backend se preserva para la UI");
  }

  // ── F: LOGOUT — baja la cuenta y la cache de MSAL ──────────────────────
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    await env.win.dwGraphToken();
    env.win.dwClearGraphAuth();
    await drain(); await drain();
    assertTrue(env.msalLog.includes("clearCache"), "F: vacia la cache de MSAL");
    assertTrue(env.msalLog.includes("setActiveAccount:null"), "F: baja la cuenta activa");
  }
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    let threw = false;
    try { env.win.dwClearGraphAuth(); } catch (e) { threw = true; }
    await drain();
    assertTrue(!threw, "F: limpiar sin haber inicializado no explota");
    assertTrue(!env.msalLog.includes("clearCache"),
      "F: y no fuerza una inicializacion solo para limpiar");
  }

  // ── G: la cache se elige a proposito, y el diagnostico no miente ───────
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    await env.win.dwGraphToken();
    assertEqual(env.captured.config.cache.cacheLocation, "sessionStorage",
      "G: la instancia se construye con sessionStorage (sobrevive la navegacion full-page)");
    assertEqual(env.captured.config.auth.authority, "https://login.microsoftonline.com/TENANT",
      "G: authority del tenant que informa el backend");
    assertEqual(env.captured.config.auth.redirectUri, "https://hub.danwor.com/",
      "G: el redirect URI (SPA) registrado, con barra");
    const d = await env.win.dwGraphAuthDiagnostics();
    assertEqual(d.cacheLocationRequested, "sessionStorage", "G: el diagnostico reporta la cache pedida");
    assertEqual(d.requestedCacheUsable, true, "G: y que aca esta usable");
    assertEqual(d.likelyFallbackToMemory, false, "G: sin fallback a memoria");
    assertEqual(d.embedded, false, "G: este contexto no es embebido");
    assertTrue(d.hasAccount === true, "G: reporta que hay cuenta");
    assertTrue(!!d.silentRenewal && d.silentRenewal.ok === true,
      "G: reporta si el silencioso funciona — el dato clave del smoke");
    // NUNCA una credencial en el diagnostico.
    const json = JSON.stringify(d);
    assertTrue(!json.includes("AT-") && !json.includes("ID-TOKEN"),
      "G: el diagnostico NO expone credenciales");
  }
  // Storage particionado + embebido: el diagnostico lo DICE en vez de asumir
  // que anda. Es exactamente el dato que el owner necesita del smoke del iframe.
  {
    const env = makeEnv({ accounts: [ACCOUNT] }, { storageThrows: true, embedded: true });
    const d = await env.win.dwGraphAuthDiagnostics();
    assertEqual(d.requestedCacheUsable, false, "G: detecta que la cache pedida no esta usable");
    assertEqual(d.likelyFallbackToMemory, true,
      "G: avisa el fallback a memoria (con navegacion full-page = cache perdida entre verticales)");
    assertEqual(d.embedded, true, "G: detecta el contexto embebido");
  }
  // Y el diagnostico reporta el fallo del silencioso sin inventar exito.
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction" });
    const d = await env.win.dwGraphAuthDiagnostics();
    assertEqual(d.silentRenewal.ok, false, "G: no finge que el silencioso anduvo");
    assertEqual(d.silentRenewal.interactionRequired, true,
      "G: distingue 'hace falta interaccion' de un error cualquiera");
  }

  // ── H: config incompleta o caida -> vacio, sin explotar y sin memoizar ─
  {
    const env = makeEnv({ accounts: [ACCOUNT] }, { config: "disabled" });
    assertEqual(await env.win.dwGraphToken(), "", "H: Microsoft sin configurar devuelve vacio");
  }
  {
    const env = makeEnv({ accounts: [ACCOUNT] }, { configHttpOk: false });
    const cfgCalls = () => env.fetches.filter(f => f.url.indexOf("/api/auth/microsoft/config") === 0).length;
    assertEqual(await env.win.dwGraphToken(), "", "H: config con error HTTP devuelve vacio");
    assertEqual(cfgCalls(), 1, "H: se intento una vez...");
    await env.win.dwGraphToken();
    assertEqual(cfgCalls(), 2,
      "H: ...y el siguiente pedido REINTENTA — un 503 puntual no deja el Hub sin auth hasta recargar");
  }

  // ── I: htmx DIFIERE de verdad ─────────────────────────────────────────
  {
    const env = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-HTMX" });
    const htmx = makeHtmx(env);
    htmx.fire(makeElt(true, "marcado"));
    assertEqual(htmx.issued.length, 0, "I: el request no sale antes de que la autoridad conteste");
    await drain(); await drain(); await drain();
    assertEqual(htmx.issued.length, 1, "I: despues de la autoridad, sale una vez");
    assertEqual(htmx.issued[0].params.graph_token, "AT-HTMX",
      "I: el hook inyecta el token que resolvio la autoridad");
    assertEqual(htmx.order.join(" | "),
      "issueAjaxRequest(confirmed=false) | confirm -> prevented=true | "
      + "issueAjaxRequest(confirmed=true) | configRequest | REQUEST SALE",
      "I: diferir -> resolver -> reemitir -> armar params -> salir");
  }

  // ── J: sin marcador, htmx queda intacto ───────────────────────────────
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    const htmx = makeHtmx(env);
    htmx.fire(makeElt(false, "sinmarca"));
    assertEqual(htmx.issued.length, 1, "J: un request sin Graph sale de inmediato");
    assertEqual(htmx.issued[0].params.graph_token, undefined,
      "J: y no se le inyecta ninguna credencial");
  }

  // ── K: un request ENCOLADO vuelve a pedir el token ────────────────────
  // Un guard de "ya resolvi para este elemento" le daria el token de ANTES de
  // la espera — que ahora que la renovacion es real, es el vencido.
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    const htmx = makeHtmx(env);
    const elt = makeElt(true, "encolado");
    let n = 0;
    env.win.dwGraphToken = async function () { n += 1; return "AT-" + n; };
    htmx.queueTheRetry();
    htmx.fire(elt); await drain(); await drain();
    assertEqual(n, 1, "K: el primer disparo consulta la autoridad");
    assertEqual(htmx.issued.length, 0, "K: nada salio — quedo encolado");
    assertEqual(htmx.queuedCount(), 1, "K: hay un request en la cola");
    htmx.drainQueue(); await drain(); await drain();
    assertEqual(n, 2, "K: el encolado vuelve a consultar la autoridad");
    assertEqual(htmx.issued.length, 1, "K: y sale");
    assertEqual(htmx.issued[0].params.graph_token, "AT-2",
      "K: con el token fresco, no el de antes de la espera");
  }

  // ── L: si la autoridad falla, el request sale igual con "" ────────────
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    const htmx = makeHtmx(env);
    env.win.dwGraphToken = async function () { throw new Error("autoridad caida"); };
    htmx.fire(makeElt(true, "falla"));
    await drain(); await drain();
    assertEqual(htmx.issued.length, 1, "L: el request no queda colgado");
    assertEqual(htmx.issued[0].params.graph_token, "", "L: sale con vacio y el server decide");
  }

  // ── M: la app NO tiene ninguna API de refresh token ───────────────────
  // Owner: la app nunca lee, escribe, rota, serializa ni exporta un refresh
  // token, y no tiene clave de storage propia para credenciales.
  {
    const env = makeEnv({ accounts: [ACCOUNT] });
    await env.win.dwGraphToken();
    const expuesto = Object.keys(env.win).filter(k => k.indexOf("dw") === 0);
    assertTrue(!expuesto.some(k => /refresh/i.test(k)),
      "M: no hay ninguna funcion de refresh token expuesta");
    // La autoridad no escribio NADA por su cuenta: las claves que existan son
    // de MSAL. Aca el doble no escribe, asi que el storage queda vacio.
    assertEqual(Object.keys(env.store).length, 0,
      "M: la autoridad no escribe claves propias de credenciales");
  }

  if (failures > 0) {
    console.log(`${failures} Hub auth authority invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL HUB AUTH AUTHORITY INVARIANTS PASSED");
  process.exit(0);
})();
