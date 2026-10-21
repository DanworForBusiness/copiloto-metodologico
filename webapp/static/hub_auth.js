/* hub_auth.js — AUTORIDAD ÚNICA del Hub sobre la autenticación de Graph.

   FASE 1B: el proveedor pasa a ser MSAL Browser (owner decision 2026-08-23,
   OPCIÓN A). La Fase 1A ya había puesto a los 25 consumidores detrás de
   `await dwGraphToken()`, así que este cambio NO toca ninguno de ellos: el
   contrato es el mismo, cambia quién lo cumple.

   EL DEFECTO QUE CIERRA. El PKCE escrito a mano adquiría el access token UNA
   vez, en el login interactivo, y lo dejaba en sessionStorage. Ese token vive
   ~60-90 min; la sesión del Hub, 8 h. En la ventana del medio no había forma
   de renovar: no se pedía `offline_access`, no había refresh token, y el
   cliente no tenía a quién preguntarle. La Fase 0 hizo que el Hub al menos
   DIJERA la verdad en esa ventana ("tu sesión de datos necesita renovarse");
   esta hace que no haga falta decirlo.

   QUÉ HACE MSAL Y QUÉ NO HACEMOS NOSOTROS. MSAL es dueño COMPLETO de sus
   artefactos de credenciales, incluido el refresh token de SPA. Este archivo
   —y toda la app— NUNCA lo lee, escribe, rota, serializa ni exporta, y no hay
   ninguna clave de storage propia para credenciales. Tampoco se toca
   `offline_access`: MSAL lo trae en sus `defaultScopes`
   (`[OPENID_SCOPE, PROFILE_SCOPE, OFFLINE_ACCESS_SCOPE]`, verificado en el
   bundle 4.30.0), o sea que el ciclo de vida es de la librería y no algo que
   la app arme.

   DOS CONTRATOS, Y LA SEPARACIÓN ES EL PUNTO:

       const token = await dwGraphToken();        // silencioso, SIEMPRE
       const ok    = await dwGraphReauthenticate();  // interactivo, SÓLO por gesto

   `dwGraphToken` lo invoca la HIDRATACIÓN AUTOMÁTICA de los tabs, así que no
   puede abrir nada: sin un gesto de usuario detrás el browser bloquearía el
   popup, y si no lo bloqueara sería peor —un loader de datos abriendo
   Microsoft por su cuenta, en cada carga—. Cuando no puede resolver en
   silencio devuelve "" y LEVANTA UNA SEÑAL; el resto lo decide el usuario.

   `dwGraphReauthenticate` es el ÚNICO lugar del proyecto que llama
   `acquireTokenPopup`, y se dispara desde el botón del cuadro de la Fase 0.
   Devuelve si quedó lista, para que quien la pidió reintente SU componente:
   no navega, no recarga, no cierra sesión. El logout sigue siendo otra
   intención ("quiero terminar mi sesión del Hub"), con su propia función.

   CACHE: `sessionStorage`, elegida a propósito y no por comodidad — ver
   CACHE POLICY más abajo.

   NO ES un cliente de Graph: no habla con Graph, no arma requests, no sabe
   qué endpoint necesita qué.

   DÓNDE SE CARGA: `base.html` (todo el chrome) y también `landing.html` /
   `auth_callback.html`, que son documentos INDEPENDIENTES y son donde ocurre
   el login. El bundle de MSAL se sirve DESDE LA APP
   (`/static/vendor/msal-browser-4.30.0.min.js`, ver vendor/README.md), no
   desde un CDN de terceros: es el código que maneja las credenciales.


   ── CACHE POLICY ────────────────────────────────────────────────────────
   `cacheLocation: "sessionStorage"`. Comportamiento, caso por caso:

   * STANDALONE (pestaña normal, sin iframe): la cache vive mientras viva la
     pestaña. Login una vez, y `acquireTokenSilent` renueva desde ahí.

   * NAVEGACIÓN FULL-PAGE EN LA MISMA PESTAÑA: es el caso que descarta la
     alternativa. El Hub navega entre verticales con SSR, o sea recarga de
     documento — `memoryStorage` moriría en cada salto y pediría interacción
     al pasar de Operaciones a Comercial y volver. `sessionStorage` sobrevive
     la recarga, así que la navegación no cuesta nada.

   * ENTRE PESTAÑAS: NO se comparte. Abrir el Hub en una pestaña nueva pide
     login de nuevo. Es exactamente el comportamiento que el Hub ya tenía
     (el artefacto anterior también vivía en sessionStorage), así que no hay
     regresión — y compartir credenciales entre pestañas es una comodidad,
     no un requisito. `localStorage` la daría, al precio de dejar un refresh
     token en disco sobreviviendo al cierre del browser: no se cambia la
     superficie de exposición de una credencial por comodidad.

   * LOGOUT: `dwClearGraphAuth` baja la cuenta activa y vacía la cache de
     MSAL. La cookie del Hub la borra el POST de siempre. Las dos cosas se
     van juntas.

   * EMBEBIDO / STORAGE DE TERCEROS RESTRINGIDO: NO se declara resuelto. Con
     storage particionado MSAL puede caer a memoria, y memoria + navegación
     full-page = la cache puede desaparecer entre verticales. Eso es
     empírico, no deducible: se mide con `dwGraphAuthDiagnostics()` en el
     iframe real. No hay acá ningún workaround de Storage Access API, ni
     copia de tokens entre frames, ni paso de credenciales cross-frame. Si el
     contexto embebido no sostiene un ciclo de vida aceptable, la decisión es
     del owner (reconsiderar el broker server-side), no de este archivo. */
(function () {
  "use strict";

  // Los permisos de RECURSO que el Hub necesita de Graph. Los de OIDC
  // (openid/profile/offline_access) NO van acá: son de MSAL.
  const GRAPH_SCOPES = ["User.Read", "Sites.Read.All"];
  const CACHE_LOCATION = "sessionStorage";

  // ── DIAGNÓSTICO DE ADQUISICIÓN DE TOKEN (owner ticket "STAFFING — MSAL
  // TOKEN FAILURE TELEMETRY") ────────────────────────────────────────────
  // `dwGraphToken` "Nunca lanza" (ver arriba) — ese contrato NO cambia acá.
  // Lo que faltaba es que, cuando devuelve "", alguien pueda preguntar POR
  // QUÉ sin inspeccionar la excepción cruda (que nunca sale de esta función).
  // Whitelist EXPLÍCITA de lo que se captura — nunca token/id_token/
  // refresh_token/código de auth/cookies/claims/UA completo (ver el resto de
  // este archivo: MSAL es dueño absoluto de sus credenciales, esto no cambia
  // esa regla, solo agrega contexto de POR QUÉ una adquisición falló).
  //
  // `lastTokenDiagnostic` es estado COMPARTIDO por diseño (mismo patrón que
  // `needsInteraction` más abajo) — bajo llamadas CONCURRENTES a
  // `dwGraphToken` (ver "D: seis fallos silenciosos concurrentes" en el
  // harness), el diagnóstico que un caller lee inmediatamente después de su
  // propio await puede, en teoría, pertenecer a OTRA llamada concurrente que
  // resolvió en el medio. Aceptable a propósito: esto es telemetría de
  // observabilidad ("por qué falló, en general, recién"), nunca una entrada
  // de una decisión de negocio — y en la práctica real (un usuario, una
  // pestaña, una carga) no hay concurrencia que perder.
  let lastTokenDiagnostic = null;

  // Probe de disponibilidad de storage — MISMO patrón que ya usaba
  // `dwGraphAuthDiagnostics` en línea (ver abajo, ahora reusa esta función
  // en vez de duplicar el try/set/get/remove). `storage` puede ser `null`/
  // `undefined` (entorno sin ese storage global, p. ej. un harness de test) —
  // se trata como "no disponible", nunca una excepción.
  function probeStorage(storage) {
    if (!storage) return false;
    const probeKey = "dw-storage-probe";
    try {
      storage.setItem(probeKey, "1");
      const ok = storage.getItem(probeKey) === "1";
      storage.removeItem(probeKey);
      return ok;
    } catch (e) {
      return false;
    }
  }

  // Buckets GRUESOS únicamente — NUNCA el user-agent completo (identificable,
  // no necesario: "por qué falló" no requiere saber la build exacta).
  function coarseBrowserFamily() {
    const ua = (typeof navigator !== "undefined" && navigator.userAgent) || "";
    if (ua.indexOf("Edg/") !== -1) return "edge";
    if (ua.indexOf("Chrome/") !== -1) return "chrome";
    if (ua.indexOf("Firefox/") !== -1) return "firefox";
    // Chrome/Edge también incluyen "Safari/" en su UA — por eso este check
    // va DESPUÉS de los suyos, nunca antes.
    if (ua.indexOf("Safari/") !== -1) return "safari";
    return "other";
  }

  function coarsePlatform() {
    const nav = typeof navigator !== "undefined" ? navigator : {};
    const platform = String(nav.platform || "").toLowerCase();
    const ua = String(nav.userAgent || "").toLowerCase();
    if (platform.indexOf("mac") !== -1 || ua.indexOf("macintosh") !== -1) return "macos";
    if (platform.indexOf("win") !== -1 || ua.indexOf("windows") !== -1) return "windows";
    if (ua.indexOf("iphone") !== -1 || ua.indexOf("ipad") !== -1) return "ios";
    if (ua.indexOf("android") !== -1) return "android";
    if (platform.indexOf("linux") !== -1 || ua.indexOf("linux") !== -1) return "linux";
    return "other";
  }

  // Campos que NO dependen de en qué branch de `dwGraphToken` se falló —
  // computados una sola vez por diagnóstico, nunca duplicados a mano en
  // cada branch.
  function commonDiagnosticFields() {
    return {
      browserFamily: coarseBrowserFamily(),
      platform: coarsePlatform(),
      cookieEnabled: !!(typeof navigator !== "undefined" && navigator.cookieEnabled),
      sessionStorageAvailable: probeStorage(typeof sessionStorage !== "undefined" ? sessionStorage : null),
      localStorageAvailable: probeStorage(typeof localStorage !== "undefined" ? localStorage : null),
      isSecureContext: !!(typeof window !== "undefined" && window.isSecureContext),
      route: (typeof window !== "undefined" && window.location && window.location.pathname) || "",
    };
  }

  function recordTokenFailure(stage, hadAccount, err) {
    lastTokenDiagnostic = Object.assign({
      stage: stage,
      hadAccount: hadAccount,
      msalErrorCode: (err && err.errorCode) || null,
      msalErrorName: (err && err.name) || null,
    }, commonDiagnosticFields());
  }

  // ── instancia única ────────────────────────────────────────────────────
  // UNA `PublicClientApplication` para todo el Hub. Memoizada por promesa (no
  // por valor) para que dos consumidores que arranquen a la vez compartan la
  // misma inicialización en vez de crear dos instancias.
  let appPromise = null;
  // El `cfg` que resolvió `getApp()` -- reusado por
  // `dwHumanCapitalDocumentWriteToken` para leer `hc_document_write_scope`
  // sin un segundo fetch (Fase 4.1.1 corrective, §5). `null` hasta que
  // `getApp()` resuelva al menos una vez; se limpia junto con `appPromise`
  // si la inicialización falla (mismo criterio: un 503 puntual no debe
  // quedar memoizado para siempre).
  let cachedConfig = null;

  function getApp() {
    if (appPromise) return appPromise;
    appPromise = (async () => {
      if (typeof msal === "undefined") throw new Error("MSAL no está cargado");
      const r = await fetch("/api/auth/microsoft/config");
      const cfg = await r.json();
      if (!r.ok || !cfg.enabled || !cfg.tenant_id || !cfg.client_id) {
        throw new Error("Microsoft no está configurado para esta app");
      }
      cachedConfig = cfg;
      const app = new msal.PublicClientApplication({
        auth: {
          clientId: cfg.client_id,
          authority: "https://login.microsoftonline.com/" + cfg.tenant_id,
          // Debe coincidir EXACTO con el redirect URI (SPA) registrado en
          // Azure, con barra — el mismo que usaba el PKCE a mano.
          redirectUri: window.location.origin + "/",
        },
        cache: { cacheLocation: CACHE_LOCATION },
        system: { loggerOptions: { loggerCallback: function () { } } },
      });
      await app.initialize();
      return app;
    })();
    // Un fallo de inicialización no puede quedar memoizado para siempre: el
    // config puede haber fallado por un 503 puntual.
    appPromise.catch(() => { appPromise = null; cachedConfig = null; });
    return appPromise;
  }

  function activeAccount(app) {
    const active = app.getActiveAccount();
    if (active) return active;
    const all = app.getAllAccounts();
    if (!all.length) return null;
    app.setActiveAccount(all[0]);
    return all[0];
  }

  // ── una sola operación interactiva a la vez ────────────────────────────
  // Sin esto, seis tabs de Reporting pidiendo token con la autorización
  // vencida abrirían SEIS popups de Microsoft. Todos los concurrentes
  // esperan la misma.
  let interactive = null;

  // "Hay una reautenticacion que serviria". Lo levanta el camino silencioso
  // cuando se topa con `InteractionRequiredAuthError` o con la ausencia de
  // cuenta, y lo baja una adquisicion exitosa. NO es una credencial ni un
  // estado de datos: es la respuesta a "conviene ofrecer el boton?".
  let needsInteraction = false;

  function runInteractive(fn) {
    if (!interactive) {
      interactive = Promise.resolve()
        .then(fn)
        .finally(() => { interactive = null; });
    }
    return interactive;
  }

  // ── EL CONTRATO — SILENCIOSO, SIEMPRE ─────────────────────────────────
  // "dame un access token de Graph si se puede SIN interacción".
  //
  // NUNCA abre un popup. Ni una ventana, ni un redirect, ni nada que le robe
  // el foco al usuario. Esta función la invoca la HIDRATACIÓN AUTOMÁTICA de
  // los tabs, así que cualquier interacción que naciera de acá no tendría un
  // gesto de usuario detrás: el browser la bloquearía, y si no la bloqueara
  // sería peor —un loader de datos abriendo Microsoft por su cuenta, en cada
  // carga—. La interacción vive en `dwGraphReauthenticate`, y sólo ahí.
  //
  // Nunca lanza: un consumidor no puede decidir qué hacer con una falla de
  // auth. Devuelve "" y el server responde `missing_token`, que la Fase 0 ya
  // traduce a la acción correcta — que ahora ES la reautenticación explícita.
  window.dwGraphToken = async function () {
    let app;
    try { app = await getApp(); } catch (e) {
      recordTokenFailure("app_init", false, e);
      return "";
    }

    const account = activeAccount(app);

    // SIN CUENTA en esta pestaña: no hay nada silencioso que intentar.
    if (!account) {
      needsInteraction = true;
      recordTokenFailure("no_account", false, null);
      return "";
    }

    try {
      // El corazón del fix: MSAL renueva sin interacción usando SU cache.
      const res = await app.acquireTokenSilent({ scopes: GRAPH_SCOPES, account });
      if (res && res.accessToken) {
        needsInteraction = false;
        lastTokenDiagnostic = null;   // éxito -- ningún diagnóstico de falla pendiente.
        return res.accessToken;
      }
      needsInteraction = true;
      recordTokenFailure("acquire_silent_no_token", true, null);
      return "";
    } catch (e) {
      if (e instanceof msal.InteractionRequiredAuthError) {
        // El caso que este corrective cierra. Antes escalaba al popup acá
        // mismo; ahora se SEÑALA y se devuelve "". La señal viaja por el
        // contrato de la Fase 0 (`auth_required` → el cuadro con su botón) y
        // el usuario decide. Es la diferencia entre ofrecer reautenticar y
        // hacerlo por él.
        needsInteraction = true;
        recordTokenFailure("acquire_silent_interaction_required", true, e);
        return "";
      }
      // Un fallo de red o de configuración NO es un problema de autorización:
      // no se ofrece reautenticar algo que reautenticar no arregla.
      recordTokenFailure("acquire_silent_error", true, e);
      return "";
    }
  };

  // Getter del diagnóstico de la ÚLTIMA falla de `dwGraphToken` — no cambia
  // su contrato ("Nunca lanza", devuelve string), solo permite que un
  // caller pregunte POR QUÉ después del hecho. `null` si la última
  // adquisición fue exitosa, o si `dwGraphToken` nunca corrió en esta
  // pestaña. Ver el comentario de `lastTokenDiagnostic` (arriba) sobre la
  // limitación conocida bajo llamadas concurrentes.
  window.dwLastTokenFailureDiagnostic = function () { return lastTokenDiagnostic; };

  // ── CAPITAL HUMANO: TOKEN DE ESCRITURA DE DOCUMENTOS (Fase 4.1.1
  // corrective) ───────────────────────────────────────────────────────────
  // `dwGraphToken` (arriba) sigue pidiendo EXCLUSIVAMENTE `GRAPH_SCOPES`
  // (User.Read + Sites.Read.All, de solo LECTURA) — los 25+ consumidores
  // existentes (Staffing/Reporting/Copiloto) NO cambian. Esta función es
  // capability-específica: pide ADEMÁS el scope Selected de escritura
  // (`cachedConfig.hc_document_write_scope`, servido por
  // `/api/auth/microsoft/config` — ver webapp/routers/auth.py) y SOLO la
  // invoca el submit handler del form de documentos de Capital Humano
  // (ver human_capital_profile_documents_panel.html) — nunca la
  // hidratación automática de tabs, nunca ningún otro vertical.
  //
  // A diferencia de `dwGraphToken` (NUNCA escala a popup — lo invoca un
  // loader sin gesto de usuario detrás), esta función SÍ puede llegar a
  // pedir interacción cuando MSAL la exige (ticket §4: "acquireTokenSilent
  // → acquireTokenPopup only when MSAL requires interaction"): es seguro
  // acá porque su ÚNICO caller es el submit handler de un form —siempre
  // detrás de un click real del usuario ("Subir documento"/"Reintentar")—,
  // nunca un loader automático. PERO nunca llama `acquireTokenPopup`
  // directamente — delega ÍNTEGRAMENTE en `dwGraphReauthenticate` (arriba),
  // que sigue siendo el ÚNICO dueño de esa API en todo el proyecto (GUARD
  // 5): le pasa el scope de escritura como `extraScopes`, y tras una
  // reautenticación exitosa el token ya quedó en la cache de MSAL, así que
  // un segundo `acquireTokenSilent` alcanza para devolverlo.
  //
  // Nunca lanza — mismo contrato que `dwGraphToken`: "" y el caller decide
  // (el router ya trata `graph_token` vacío como falta de autorización).
  window.dwHumanCapitalDocumentWriteToken = async function (opts) {
    // `interactive` (default true, contrato sin cambios para los consumidores
    // existentes -- form de Documentos y su retry): cuando MSAL exige
    // interacción, escala a popup vía `dwGraphReauthenticate`. El alta con CV
    // (create-with-CV) lo llama con `{interactive:false}`: NUNCA escala a popup
    // desde el submit del alta -- ese popup, invocado tras cruzar un `await`
    // (gesto ya perdido), es la fuente del `Promise` pendiente que dejaba el
    // clic inerte; en silent-only devuelve "" y el alta cae al partial-success
    // (persistir el Profile + subir el CV luego desde Documentos, con gesto real).
    const interactive = !(opts && opts.interactive === false);
    let app;
    try { app = await getApp(); } catch (e) { recordTokenFailure("hc_write_get_app_failed", false, e); return ""; }

    const writeScope = cachedConfig && cachedConfig.hc_document_write_scope;
    if (!writeScope) { recordTokenFailure("hc_write_scope_not_configured", false, null); return ""; }   // CONFIG_INCOMPLETE -- fail closed, sin pedir Graph con un scope vacío.
    const scopes = GRAPH_SCOPES.concat([writeScope]);

    const account = activeAccount(app);
    if (!account) { recordTokenFailure("hc_write_no_account", false, null); return ""; }      // Sin cuenta: nada silencioso que intentar (mismo criterio que dwGraphToken).

    try {
      const res = await app.acquireTokenSilent({ scopes, account });
      lastTokenDiagnostic = null;
      return (res && res.accessToken) || "";
    } catch (e) {
      if (!(e instanceof msal.InteractionRequiredAuthError)) { recordTokenFailure("hc_write_silent_error", true, e); return ""; }
      if (!interactive) { recordTokenFailure("hc_write_interaction_required_silent_only", true, e); return ""; }  // silent-only (create-with-CV): sin popup, cae a partial-success.
      const reauthenticated = await window.dwGraphReauthenticate([writeScope]);
      if (!reauthenticated) { recordTokenFailure("hc_write_reauth_failed", true, e); return ""; }
      try {
        const res2 = await app.acquireTokenSilent({ scopes, account: activeAccount(app) });
        lastTokenDiagnostic = null;
        return (res2 && res2.accessToken) || "";
      } catch (e2) {
        recordTokenFailure("hc_write_after_reauth_silent_error", true, e2);
        return "";
      }
    }
  };

  // ── LA ÚNICA PUERTA A LA INTERACCIÓN ──────────────────────────────────
  // Este es el ÚNICO lugar del proyecto que llama `acquireTokenPopup`
  // (GUARD 5, test_hub_auth_gesture.py — invariante deliberado: cada call
  // site nuevo de una API interactiva es un lugar más donde un loader
  // podría, por error, abrir Microsoft sin gesto detrás). Se invoca desde
  // un GESTO DEL USUARIO —el botón "Renovar sesión" del cuadro de la Fase
  // 0, o el submit de un form de Capital Humano vía
  // `dwHumanCapitalDocumentWriteToken` (Fase 4.1.1 corrective, que NUNCA
  // llama `acquireTokenPopup` directamente — delega ACÁ)—, nunca desde un
  // loader.
  //
  // `extraScopes` (Fase 4.1.1, opcional): scopes ADICIONALES a
  // `GRAPH_SCOPES` para este popup puntual — p. ej. el scope Selected de
  // escritura de documentos, para que el consentimiento incremental cubra
  // ESE scope y no solo lectura. `undefined`/`[]` preserva el
  // comportamiento exacto de siempre (reautenticación de sólo lectura).
  //
  // Compartido: si el usuario clickea dos veces, o si dos tabs ofrecen el
  // botón y se clickean ambos, se abre UN popup. Sin esto, un doble click es
  // una tormenta de ventanas de Microsoft.
  //
  // Devuelve `true` si la reautenticación quedó lista, para que quien la pidió
  // pueda reintentar SU componente. No navega, no recarga, no cierra sesión.
  window.dwGraphReauthenticate = async function (extraScopes) {
    let app;
    try { app = await getApp(); } catch (e) { return false; }
    return runInteractive(async () => {
      try {
        const account = activeAccount(app);
        const req = { scopes: GRAPH_SCOPES.concat(extraScopes || []) };
        if (account) req.account = account;      // reautenticar AL MISMO usuario
        const res = await app.acquireTokenPopup(req);
        if (res && res.account) app.setActiveAccount(res.account);
        const ok = !!(res && res.accessToken);
        if (ok) needsInteraction = false;
        return ok;
      } catch (e) {
        // Cancelar el popup no es un error del usuario: se queda como estaba y
        // el cuadro sigue ofreciendo la acción.
        return false;
      }
    });
  };

  // Señal consultable, para que el Hub pueda decidir si OFRECER la acción sin
  // tener que provocar un fallo primero. No es una credencial ni un estado de
  // datos: sólo dice "hay una reautenticación que serviría".
  window.dwGraphNeedsInteraction = function () { return needsInteraction; };

  // ── LOGIN ─────────────────────────────────────────────────────────────
  // El popup lo abre MSAL. Lo que sigue igual es lo de siempre: el `id_token`
  // validado va al backend, que crea la sesión del Hub (cookie). MSAL no sabe
  // nada de la sesión del Hub, y el Hub no sabe nada de la cache de MSAL.
  window.dwHubLogin = async function () {
    const app = await getApp();
    return runInteractive(async () => {
      const res = await app.loginPopup({
        scopes: GRAPH_SCOPES,
        prompt: "select_account",   // preservado del flujo anterior
      });
      if (res && res.account) app.setActiveAccount(res.account);
      if (!res || !res.idToken) throw new Error("Microsoft no devolvió un token válido");
      const r = await fetch("/api/auth/microsoft/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: res.idToken }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const det = d.detail;
        const err = new Error((det && det.message) || "No se pudo crear la sesión");
        err.detail = det;
        throw err;
      }
      return d;
    });
  };

  // ── LIMPIEZA ──────────────────────────────────────────────────────────
  // La invoca `dwLogout` (app.js): el chrome decide CUÁNDO, la autoridad sabe
  // QUÉ. Baja la cuenta activa y vacía la cache de MSAL — sin tocar ninguna
  // clave a mano, porque las claves son de MSAL.
  window.dwClearGraphAuth = function () {
    needsInteraction = false;     // tras cerrar sesión no hay nada que renovar
    if (!appPromise) return;      // nunca se inicializó: no hay nada que bajar
    appPromise
      .then((app) => {
        app.setActiveAccount(null);
        return app.clearCache();
      })
      .catch(() => { })
      .finally(() => { appPromise = null; });
  };

  // ── DIAGNÓSTICO (para el smoke del owner, sobre todo el del iframe) ────
  // NO expone credenciales: sólo si HAY cuenta y qué cache quedó disponible.
  // El punto que el owner marcó —MSAL puede caer a memoria bajo storage
  // particionado, y con navegación full-page eso significa perder la cache
  // entre verticales— no se puede deducir, hay que medirlo.
  window.dwGraphAuthDiagnostics = async function () {
    // `probeStorage` (arriba) — MISMA función que usa `recordTokenFailure`
    // para `sessionStorageAvailable`/`localStorageAvailable`, nunca un
    // segundo try/set/get/remove duplicado acá.
    const sessionUsable = probeStorage(typeof sessionStorage !== "undefined" ? sessionStorage : null);

    const diag = {
      msalLoaded: typeof msal !== "undefined",
      cacheLocationRequested: CACHE_LOCATION,
      // Si la cache pedida no está usable, MSAL cae a memoria — y con
      // navegación full-page eso es perderla en cada salto de vertical.
      requestedCacheUsable: sessionUsable,
      likelyFallbackToMemory: !sessionUsable,
      embedded: window.top !== window.self,
      graphScopes: GRAPH_SCOPES.slice(),
      initialized: !!appPromise,
      needsInteraction: needsInteraction,
      hasAccount: false,
      accountCount: 0,
      msalCacheEntries: 0,
      silentRenewal: null,
    };
    try {
      const app = await getApp();
      diag.initialized = true;
      const accounts = app.getAllAccounts();
      diag.accountCount = accounts.length;
      diag.hasAccount = accounts.length > 0;
      if (sessionUsable) {
        // Cuántas claves dejó MSAL: si son 0 con cuenta presente, la cache no
        // es la que creemos.
        try {
          diag.msalCacheEntries = Object.keys(sessionStorage)
            .filter((k) => k.indexOf("msal") === 0 || k.indexOf("{") === 0).length;
        } catch (e) { }
      }
      if (diag.hasAccount) {
        try {
          const res = await app.acquireTokenSilent({
            scopes: GRAPH_SCOPES, account: activeAccount(app),
          });
          // Nunca el token: sólo si se pudo y por cuánto vale.
          diag.silentRenewal = {
            ok: !!(res && res.accessToken),
            fromCache: !!(res && res.fromCache),
            expiresOn: res && res.expiresOn ? String(res.expiresOn) : null,
          };
        } catch (e) {
          diag.silentRenewal = {
            ok: false,
            interactionRequired: e instanceof msal.InteractionRequiredAuthError,
            errorCode: (e && e.errorCode) || (e && e.name) || "unknown",
          };
        }
      }
    } catch (e) {
      diag.initError = (e && e.message) || "unknown";
    }
    return diag;
  };

  // ── htmx: DIFERIR, NO CREAR UNA SEGUNDA AUTORIDAD ─────────────────────
  // `hx-vals='js:...'` se evalúa SINCRÓNICAMENTE, así que no puede esperar a
  // `dwGraphToken()`. La salida fácil sería un getter síncrono para los
  // templates — y eso sería una segunda autoridad, capaz de servir un token
  // vencido justo cuando la única que sabe renovar no fue consultada. Ahora
  // que renovar es real, eso importa más que en la Fase 1A.
  //
  // En cambio se DIFIERE el request. Verificado contra el artefacto pinneado
  // (htmx.org@2.0.4/dist/htmx.js), no contra la documentación:
  //
  //   `issueAjaxRequest` entra en 4165; dispara `htmx:confirm` en 4218
  //   exponiendo `issueRequest`; `triggerEvent` devuelve false ante
  //   `defaultPrevented` (3050-3052), lo que aborta esta pasada; y
  //   `getExpressionVars` —que evalúa `hx-vals`— recién corre en 4341.
  //
  // El gate está 123 líneas ANTES de que se armen los parámetros. Y reemitir
  // con `issueRequest(true)` entra con `confirmed = true`, que saltea el
  // `if (confirmed === undefined)` de 4213: no hay loop posible.
  //
  // Los templates quedan SIN la credencial: sólo declaran que la necesitan
  // con `data-dw-graph-token`, y el parámetro lo inyecta este hook.
  const MARKER = "data-dw-graph-token";
  const PARAM = "graph_token";
  // owner ticket "STAFFING — MSAL TOKEN FAILURE TELEMETRY": SOLO viaja
  // cuando el token vino vacío (ver abajo) — MISMO nombre de campo que
  // esperan los 3 routers de Staffing (`token_diagnostic`, Form field).
  const DIAGNOSTIC_PARAM = "token_diagnostic";

  // Indexado por elemento (WeakMap, para no retener nodos removidos del DOM):
  // "el token (+ diagnóstico si vino vacío) resuelto para ESTE request".
  const resolved = new WeakMap();

  function needsGraph(elt) {
    return !!(elt && elt.closest && elt.closest("[" + MARKER + "]"));
  }

  // NO hay guard de "ya resolví para este elemento", a propósito. Verificado
  // en htmx 2.0.4: cuando un request se ENCOLA (hx-sync, o un segundo disparo
  // con un xhr en vuelo), htmx lo reemite en 4276-4287 como
  // `issueAjaxRequest(verb, path, elt, event, etc)` — SIN el 6º argumento, o
  // sea que `htmx:confirm` vuelve a dispararse cuando la cola se drena. Un
  // guard ahí dejaría pasar el token que se resolvió antes de la espera, que
  // es justo el que puede haber vencido. Sin guard, el request encolado vuelve
  // a pedirlo. Y no hay loop: la reemisión inmediata usa `issueRequest(true)`.
  document.addEventListener("htmx:confirm", function (event) {
    const elt = event.detail && event.detail.elt;
    if (!needsGraph(elt)) return;                  // request sin Graph: intacto
    event.preventDefault();                        // aborta ESTA pasada
    Promise.resolve()
      .then(() => window.dwGraphToken())
      // Si la autoridad falla, se emite con "" igual: el server responde
      // `missing_token` y el usuario ve la acción que corresponde. Dejar el
      // request colgado sería peor — un spinner infinito sin explicación.
      .catch(() => "")
      .then((token) => {
        // owner ticket "STAFFING — MSAL TOKEN FAILURE TELEMETRY": el
        // diagnóstico SOLO se lee/adjunta cuando el token vino vacío — con
        // token válido no hay nada que explicar, y no vale la pena el campo
        // extra en el body. Leído en la MISMA vuelta que la resolución del
        // token (ver el comentario de `lastTokenDiagnostic` sobre el límite
        // conocido bajo llamadas concurrentes).
        const diagnostic = token ? null : window.dwLastTokenFailureDiagnostic();
        resolved.set(elt, { token: token || "", diagnostic: diagnostic });
        try { event.detail.issueRequest(true); }
        catch (e) { resolved.delete(elt); }
      });
  });

  document.addEventListener("htmx:configRequest", function (event) {
    const elt = event.detail && event.detail.elt;
    if (!needsGraph(elt)) return;
    // Se consume una sola vez: el próximo disparo del mismo elemento vuelve a
    // pasar por el gate y pide un token fresco.
    const entry = resolved.get(elt);
    resolved.delete(elt);
    event.detail.parameters[PARAM] = (entry && entry.token) || "";
    if (entry && entry.diagnostic) {
      event.detail.parameters[DIAGNOSTIC_PARAM] = JSON.stringify(entry.diagnostic);
    }
  });
})();
