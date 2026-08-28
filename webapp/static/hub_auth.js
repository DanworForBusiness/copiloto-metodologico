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

  // ── instancia única ────────────────────────────────────────────────────
  // UNA `PublicClientApplication` para todo el Hub. Memoizada por promesa (no
  // por valor) para que dos consumidores que arranquen a la vez compartan la
  // misma inicialización en vez de crear dos instancias.
  let appPromise = null;

  function getApp() {
    if (appPromise) return appPromise;
    appPromise = (async () => {
      if (typeof msal === "undefined") throw new Error("MSAL no está cargado");
      const r = await fetch("/api/auth/microsoft/config");
      const cfg = await r.json();
      if (!r.ok || !cfg.enabled || !cfg.tenant_id || !cfg.client_id) {
        throw new Error("Microsoft no está configurado para esta app");
      }
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
    appPromise.catch(() => { appPromise = null; });
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
    try { app = await getApp(); } catch (e) { return ""; }

    const account = activeAccount(app);

    // SIN CUENTA en esta pestaña: no hay nada silencioso que intentar.
    if (!account) { needsInteraction = true; return ""; }

    try {
      // El corazón del fix: MSAL renueva sin interacción usando SU cache.
      const res = await app.acquireTokenSilent({ scopes: GRAPH_SCOPES, account });
      if (res && res.accessToken) { needsInteraction = false; return res.accessToken; }
      needsInteraction = true;
      return "";
    } catch (e) {
      if (e instanceof msal.InteractionRequiredAuthError) {
        // El caso que este corrective cierra. Antes escalaba al popup acá
        // mismo; ahora se SEÑALA y se devuelve "". La señal viaja por el
        // contrato de la Fase 0 (`auth_required` → el cuadro con su botón) y
        // el usuario decide. Es la diferencia entre ofrecer reautenticar y
        // hacerlo por él.
        needsInteraction = true;
        return "";
      }
      // Un fallo de red o de configuración NO es un problema de autorización:
      // no se ofrece reautenticar algo que reautenticar no arregla.
      return "";
    }
  };

  // ── LA ÚNICA PUERTA A LA INTERACCIÓN ──────────────────────────────────
  // Este es el ÚNICO lugar del proyecto que llama `acquireTokenPopup`. Se
  // invoca desde un GESTO DEL USUARIO —el botón "Renovar sesión" del cuadro de
  // la Fase 0—, nunca desde un loader.
  //
  // Compartido: si el usuario clickea dos veces, o si dos tabs ofrecen el
  // botón y se clickean ambos, se abre UN popup. Sin esto, un doble click es
  // una tormenta de ventanas de Microsoft.
  //
  // Devuelve `true` si la reautenticación quedó lista, para que quien la pidió
  // pueda reintentar SU componente. No navega, no recarga, no cierra sesión.
  window.dwGraphReauthenticate = async function () {
    let app;
    try { app = await getApp(); } catch (e) { return false; }
    return runInteractive(async () => {
      try {
        const account = activeAccount(app);
        const req = { scopes: GRAPH_SCOPES };
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
    const probeKey = "dw-storage-probe";
    let sessionUsable = false;
    try {
      sessionStorage.setItem(probeKey, "1");
      sessionUsable = sessionStorage.getItem(probeKey) === "1";
      sessionStorage.removeItem(probeKey);
    } catch (e) { sessionUsable = false; }

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

  // Indexado por elemento (WeakMap, para no retener nodos removidos del DOM):
  // "el token resuelto para ESTE request".
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
        resolved.set(elt, token || "");
        try { event.detail.issueRequest(true); }
        catch (e) { resolved.delete(elt); }
      });
  });

  document.addEventListener("htmx:configRequest", function (event) {
    const elt = event.detail && event.detail.elt;
    if (!needsGraph(elt)) return;
    // Se consume una sola vez: el próximo disparo del mismo elemento vuelve a
    // pasar por el gate y pide un token fresco.
    const token = resolved.get(elt);
    resolved.delete(elt);
    event.detail.parameters[PARAM] = token || "";
  });
})();
