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
    // `this.constructor.name` (no un literal "AuthError" fijo) — así una
    // subclase como `InteractionRequiredAuthError` reporta SU PROPIO nombre
    // real, igual que MSAL de verdad (owner ticket "STAFFING — MSAL TOKEN
    // FAILURE TELEMETRY", que sí distingue por `e.name`).
    constructor(code) { super(code); this.errorCode = code; this.name = this.constructor.name; }
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
      // "resuelve sin excepción, pero sin token" (owner ticket "STAFFING —
      // MSAL TOKEN FAILURE TELEMETRY", stage=acquire_silent_no_token) — un
      // caso real de MSAL, distinto de cualquier excepción.
      if (b.silent === "no_token") return { accessToken: "", fromCache: true };
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
  // owner ticket "STAFFING — MSAL TOKEN FAILURE TELEMETRY": storage/navigator
  // separados de `sessionStorage` arriba (distinto storage, distinto flag de
  // falla — `localStorageThrows` nunca pisa `storageThrows`).
  const localStore = {};
  const localStorage = {
    getItem(k) { if (options.localStorageThrows) throw new Error("particionado"); return k in localStore ? localStore[k] : null; },
    setItem(k, v) { if (options.localStorageThrows) throw new Error("particionado"); localStore[k] = v; },
    removeItem(k) { if (options.localStorageThrows) throw new Error("particionado"); delete localStore[k]; },
  };
  const navigator = options.navigator || {
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    platform: "Win32",
    cookieEnabled: true,
  };
  const win = { location: { origin: "https://hub.danwor.com", pathname: "/reporting", search: "" } };
  win.self = win;
  win.top = options.embedded ? { otro: true } : win;
  win.isSecureContext = options.isSecureContext !== false;   // default true — solo false si se pide explícito.
  const sandbox = {
    window: win, msal, sessionStorage, localStorage, navigator, console, Promise, setTimeout, Object, JSON, Date, Error,
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

  // ── C: dwGraphToken NUNCA abre un popup ────────────────────────────────
  // EL INVARIANTE CENTRAL de este corrective. `dwGraphToken` lo invoca la
  // hidratacion automatica de los tabs, asi que cualquier interaccion que
  // naciera de ahi no tendria un gesto de usuario detras: el browser la
  // bloquearia, y si no la bloqueara seria peor —un loader de datos abriendo
  // Microsoft por su cuenta, en cada carga—.
  //
  // Se cubren los CUATRO caminos por los que puede no haber token.
  {
    // 1. InteractionRequiredAuthError: el caso que este corrective cierra.
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popupToken: "NO-DEBERIA" });
    assertEqual(await env.win.dwGraphToken(), "",
      "C1: con InteractionRequired devuelve vacio");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"),
      "C1: y NO abre popup — la senal viaja por el contrato de la Fase 0");
    // Pero SI deja dicho que reautenticar serviria.
    assertEqual(env.win.dwGraphNeedsInteraction(), true,
      "C1: levanta la senal para que el Hub OFREZCA la accion");
  }
  {
    // 2. Sin cuenta.
    const env = makeEnv({ accounts: [], popupToken: "NO-DEBERIA" });
    assertEqual(await env.win.dwGraphToken(), "", "C2: sin cuenta devuelve vacio");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"), "C2: sin cuenta NO abre popup");
    assertTrue(!env.msalLog.includes("acquireTokenSilent"),
      "C2: sin cuenta no tiene sentido intentar silencioso");
    assertEqual(env.win.dwGraphNeedsInteraction(), true, "C2: y la senal queda levantada");
  }
  {
    // 3. Un fallo de red NO es de autorizacion: no se ofrece reautenticar algo
    //    que reautenticar no arregla.
    const env = makeEnv({ accounts: [ACCOUNT], silent: "network" });
    assertEqual(await env.win.dwGraphToken(), "", "C3: un fallo de red devuelve vacio");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"), "C3: sin popup");
    assertEqual(env.win.dwGraphNeedsInteraction(), false,
      "C3: y NO ofrece reautenticar — la red no se arregla con un login");
  }
  {
    // 4. Config caida.
    const env = makeEnv({ accounts: [ACCOUNT] }, { config: "disabled" });
    assertEqual(await env.win.dwGraphToken(), "", "C4: config incompleta devuelve vacio");
    assertTrue(!env.msalLog.includes("acquireTokenPopup"), "C4: sin popup");
  }
  // Y el exito BAJA la senal: no queda ofreciendo renovar algo que ya anda.
  {
    const env = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-OK" });
    await env.win.dwGraphToken();
    assertEqual(env.win.dwGraphNeedsInteraction(), false,
      "C: una adquisicion exitosa baja la senal");
  }

  // ── D: SEIS fallos silenciosos concurrentes -> CERO popups ─────────────
  // El escenario real: seis datasets de un tab hidratando a la vez con la
  // autorizacion vencida. Antes de este corrective eso era —en el mejor caso—
  // un popup, y en el peor seis.
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popupToken: "NO-DEBERIA" });
    const results = await Promise.all([1, 2, 3, 4, 5, 6].map(() => env.win.dwGraphToken()));
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 0,
      "D: seis fallos silenciosos concurrentes abren CERO popups");
    assertTrue(results.every(t => t === ""), "D: los seis devuelven vacio");
    assertEqual(env.win.dwGraphNeedsInteraction(), true, "D: y la senal queda levantada una vez");
  }

  // ── D2: LA REAUTENTICACION EXPLICITA es la unica que interactua ────────
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popupToken: "AT-REAUTH" });
    // Primero el camino automatico falla en silencio...
    await env.win.dwGraphToken();
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 0, "D2: sin popup todavia");
    // ...y recien el gesto del usuario abre UNO.
    assertEqual(await env.win.dwGraphReauthenticate(), true,
      "D2: la reautenticacion explicita informa que quedo lista");
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 1,
      "D2: EXACTAMENTE un popup");
    assertEqual(env.win.dwGraphNeedsInteraction(), false, "D2: y baja la senal");
    // Tras reautenticar, el camino silencioso vuelve a servir.
    const env2 = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-DESPUES" });
    assertEqual(await env2.win.dwGraphToken(), "AT-DESPUES",
      "D2: despues de reautenticar el silencioso alcanza de nuevo");
  }
  // Reautentica AL MISMO usuario cuando hay cuenta (no un select_account a ciegas).
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction" });
    await env.win.dwGraphReauthenticate();
    assertTrue(env.msalLog.includes("acquireTokenPopup"), "D2: abrio el popup");
  }

  // ── D3: la recuperacion explicita CONCURRENTE no hace tormenta ─────────
  // Doble click en "Renovar sesion", o dos tabs ofreciendo el boton y los dos
  // clickeados: UN popup, y todos reciben el mismo resultado.
  {
    const env = makeEnv({
      accounts: [ACCOUNT], silent: "interaction", popupToken: "AT-UNO", popupDelayMs: 20,
    });
    const results = await Promise.all([1, 2, 3, 4, 5, 6].map(() => env.win.dwGraphReauthenticate()));
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 1,
      "D3: seis recuperaciones explicitas concurrentes abren UN popup");
    assertTrue(results.every(r => r === true), "D3: las seis reciben el mismo resultado");
    // Y el candado se libera: un click POSTERIOR si puede volver a interactuar.
    await env.win.dwGraphReauthenticate();
    assertEqual(env.msalLog.filter(l => l === "acquireTokenPopup").length, 2,
      "D3: el candado se libero (no queda trabado para siempre)");
  }
  // Cancelar devuelve false — el cuadro sigue ofreciendo la accion.
  {
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popup: "cancel" });
    let threw = false, v = "sentinel";
    try { v = await env.win.dwGraphReauthenticate(); } catch (e) { threw = true; }
    assertTrue(!threw, "D3: cancelar no propaga excepcion");
    assertEqual(v, false, "D3: cancelar devuelve false");
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

  // ── N: DIAGNÓSTICO DE FALLA DE TOKEN (owner ticket "STAFFING — MSAL TOKEN
  // FAILURE TELEMETRY") ───────────────────────────────────────────────────
  // `dwGraphToken` sigue "Nunca lanza" y devolviendo "" (invariante C de
  // arriba, sin cambios) — lo nuevo es que `dwLastTokenFailureDiagnostic()`
  // deja dicho POR QUÉ, en vez de perder la excepción original.
  {
    // N1: adquisición exitosa -> sin diagnóstico pendiente.
    const env = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-OK" });
    await env.win.dwGraphToken();
    assertEqual(env.win.dwLastTokenFailureDiagnostic(), null,
      "N1: el camino exitoso no deja ningún diagnóstico de falla");
  }
  {
    // N2: sin cuenta -> stage=no_account, hadAccount=false, sin error MSAL
    // (no hubo excepción, así que no hay código/nombre que reportar).
    const env = makeEnv({ accounts: [] });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.stage, "no_account", "N2: distingue la etapa sin cuenta");
    assertEqual(d.hadAccount, false, "N2: hadAccount refleja que no había cuenta");
    assertEqual(d.msalErrorCode, null, "N2: sin excepción, sin código MSAL inventado");
  }
  {
    // N3: InteractionRequiredAuthError -> stage + código/nombre reales.
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction" });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.stage, "acquire_silent_interaction_required", "N3: distingue la etapa de interacción requerida");
    assertEqual(d.msalErrorCode, "interaction_required", "N3: código MSAL real, no descartado");
    assertEqual(d.msalErrorName, "InteractionRequiredAuthError", "N3: nombre de clase real");
    assertEqual(d.hadAccount, true, "N3: hadAccount refleja que sí había cuenta");
  }
  {
    // N4: fallo MSAL genérico (red/config) -> stage + código/nombre reales,
    // MISMO criterio que N3 pero sin escalar a "hace falta interacción".
    const env = makeEnv({ accounts: [ACCOUNT], silent: "network" });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.stage, "acquire_silent_error", "N4: distingue la etapa de error genérico");
    assertEqual(d.msalErrorCode, "network_error", "N4: código MSAL real");
    assertEqual(d.msalErrorName, "AuthError", "N4: nombre de clase real");
  }
  {
    // N4b: éxito SIN accessToken (caso real de MSAL, sin excepción) -> etapa
    // propia, distinguible de los dos casos de arriba.
    const env = makeEnv({ accounts: [ACCOUNT], silent: "no_token" });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.stage, "acquire_silent_no_token", "N4b: distingue éxito-sin-token de una excepción");
  }
  {
    // N5: falla de inicialización (config caída) -> etapa distinguible de
    // las de arriba, ANTES de que exista cuenta u oportunidad de silencioso.
    const env = makeEnv({ accounts: [ACCOUNT] }, { config: "disabled" });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.stage, "app_init", "N5: distingue la etapa de inicialización");
    assertEqual(d.hadAccount, false, "N5: nunca se llegó a mirar la cuenta");
  }
  {
    // N6: el diagnóstico NUNCA expone credenciales -- MISMO criterio que ya
    // prueba G para `dwGraphAuthDiagnostics()`, acá para el nuevo getter.
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction", popupToken: "AT-NUNCA" });
    await env.win.dwGraphToken();
    const json = JSON.stringify(env.win.dwLastTokenFailureDiagnostic());
    assertTrue(!json.includes("AT-") && !json.includes("ID-TOKEN") && !json.includes("refresh"),
      "N6: el diagnóstico de falla NUNCA expone credenciales");
  }
  {
    // N7: campos comunes -- buckets GRUESOS, nunca el user-agent completo.
    const env = makeEnv({ accounts: [] }, {
      navigator: { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15", platform: "MacIntel", cookieEnabled: false },
    });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.browserFamily, "safari", "N7: reconoce Safari (sin Chrome/Edg en el UA)");
    assertEqual(d.platform, "macos", "N7: reconoce macOS");
    assertEqual(d.cookieEnabled, false, "N7: refleja cookieEnabled real");
    assertTrue(!JSON.stringify(d).includes("AppleWebKit"),
      "N7: NUNCA el user-agent completo, solo el bucket grueso");
    assertEqual(d.sessionStorageAvailable, true, "N7: sessionStorage disponible en este entorno de test");
    assertEqual(d.localStorageAvailable, true, "N7: localStorage disponible en este entorno de test");
  }
  {
    // N8: Chrome en Windows -- el otro extremo del bucketing, para probar
    // que no siempre cae en "safari"/"macos" (Chrome también trae "Safari/"
    // en su UA -- si el orden de chequeo estuviera mal, esto fallaría).
    const env = makeEnv({ accounts: [] });   // navigator default: Chrome/Win32
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.browserFamily, "chrome", "N8: Chrome no cae en el bucket Safari");
    assertEqual(d.platform, "windows", "N8: reconoce Windows");
  }
  {
    // N9: storage particionado -- se refleja como no disponible, nunca
    // finge que sí (MISMO criterio que `dwGraphAuthDiagnostics`).
    const env = makeEnv({ accounts: [] }, { storageThrows: true, localStorageThrows: true });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.sessionStorageAvailable, false, "N9: sessionStorage particionado se refleja como no disponible");
    assertEqual(d.localStorageAvailable, false, "N9: localStorage particionado se refleja como no disponible");
  }
  {
    // N10: isSecureContext -- refleja el contexto real, nunca asume true.
    const env = makeEnv({ accounts: [] }, { isSecureContext: false });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.isSecureContext, false, "N10: refleja isSecureContext=false real");
  }
  {
    // N11: route -- el pathname actual, nunca la URL completa (sin query string).
    const env = makeEnv({ accounts: [] });
    await env.win.dwGraphToken();
    const d = env.win.dwLastTokenFailureDiagnostic();
    assertEqual(d.route, "/reporting", "N11: captura el pathname del entorno de test");
  }
  {
    // N12: htmx SOLO adjunta el diagnóstico cuando el token vino vacío --
    // con token válido, el campo extra ni se arma (payload mínimo).
    const env = makeEnv({ accounts: [ACCOUNT], silentToken: "AT-CON-TOKEN" });
    const htmx = makeHtmx(env);
    htmx.fire(makeElt(true, "ok"));
    await drain(); await drain(); await drain();
    assertEqual(htmx.issued[0].params.graph_token, "AT-CON-TOKEN", "N12: el token viaja igual que siempre");
    assertEqual(htmx.issued[0].params.token_diagnostic, undefined,
      "N12: sin falla, no se arma ningún campo de diagnóstico");
  }
  {
    // N13: htmx SÍ adjunta el diagnóstico cuando el token vino vacío -- el
    // servidor puede loguear POR QUÉ sin que el cliente tenga que reintentar
    // ni exponer nada más que el string ya filtrado del lado del cliente.
    const env = makeEnv({ accounts: [ACCOUNT], silent: "interaction" });
    const htmx = makeHtmx(env);
    htmx.fire(makeElt(true, "falla-diagnostico"));
    await drain(); await drain(); await drain();
    assertEqual(htmx.issued[0].params.graph_token, "", "N13: el token sigue vacío, sin cambios");
    const sent = JSON.parse(htmx.issued[0].params.token_diagnostic);
    assertEqual(sent.stage, "acquire_silent_interaction_required",
      "N13: el diagnóstico adjunto es el de ESTA falla");
  }

  if (failures > 0) {
    console.log(`${failures} Hub auth authority invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL HUB AUTH AUTHORITY INVARIANTS PASSED");
  process.exit(0);
})();
