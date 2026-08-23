/* hub_auth.js — AUTORIDAD ÚNICA del Hub sobre el access token de Graph.

   FASE 1A del ciclo de vida del token (owner decision 2026-08-23).
   PROVIDER-NEUTRAL A PROPÓSITO: hoy resuelve contra el mismo artefacto de
   sessionStorage que ya usaba todo el mundo. CERO cambio de comportamiento
   de auth. Lo que cambia es de QUIÉN es la responsabilidad.

   EL PROBLEMA QUE RESUELVE. Había 25 consumidores leyendo la credencial
   directo del storage —17 en Reporting, 3 en app.js, 4 en atributos htmx de
   templates—, cada uno con su propio `|| ""`. Con esa forma, cambiar el
   proveedor de tokens significaba tocar 25 lugares y no poder garantizar que
   ninguno quedara con la lectura vieja. Peor: ningún consumidor podía
   RENOVAR nada, porque leer un string de storage no es una operación que
   pueda esperar.

   EL CONTRATO. Uno solo:

       const token = await dwGraphToken();

   "dame un access token de Graph VÁLIDO ahora". Es `async` desde el día uno
   —aunque hoy resuelva de inmediato— justamente para que la Fase 1B pueda
   renovar de verdad (MSAL `acquireTokenSilent`) sin volver a tocar ninguno
   de los 25 consumidores. Ese es todo el punto de este commit.

   ESTE ARCHIVO ES EL ÚNICO DEL PROYECTO QUE NOMBRA EL ARTEFACTO. Hay un
   test arquitectónico que lo hace cumplir (`test_hub_auth_authority.py`):
   ninguna vertical, ningún template y ningún feature puede volver a tocar el
   storage de credenciales. En la Fase 1B, `ARTIFACT` y `readArtifact` se
   reemplazan por MSAL acá adentro y afuera no se entera nadie.

   NO ES un cliente de Graph: no habla con Graph, no arma requests, no sabe
   qué endpoint necesita qué. Sólo responde por la credencial.

   DÓNDE SE CARGA: en `base.html` (todo el chrome del Hub) y también en
   `landing.html` / `auth_callback.html`, que son documentos INDEPENDIENTES
   —no extienden base.html— y son justo donde el login GUARDA el token. La
   autoridad tiene que estar en los tres o alguno se lo salta. */
(function () {
  "use strict";

  // El nombre del artefacto vive acá y en ningún otro lado del proyecto.
  const ARTIFACT = "dw-graph-token";

  // sessionStorage puede no existir (incógnito con storage particionado, ver
  // `warnIfStorageRestricted` en auth.js): eso no es un error, es "no hay
  // token". El server responde `missing_token` y el Hub ya sabe mostrarlo.
  function readArtifact() {
    try { return sessionStorage.getItem(ARTIFACT) || ""; } catch (e) { return ""; }
  }

  // --- EL CONTRATO ---------------------------------------------------------
  // Devuelve un access token de Graph válido AHORA, o "" si no hay ninguno.
  // Nunca lanza: un consumidor que no puede pedir el token no puede decidir
  // qué hacer con la falla, y devolver "" hace que el server responda
  // `missing_token` — que la Fase 0 ya traduce a la acción correcta.
  window.dwGraphToken = async function () {
    return readArtifact();
  };

  // --- ESCRITURA Y LIMPIEZA -----------------------------------------------
  // El login (auth.js) guarda por acá; el logout (dwLogout en app.js) limpia
  // por acá. Ninguno de los dos nombra el artefacto.
  window.dwRememberGraphAuth = function (token) {
    if (!token) return;
    try { sessionStorage.setItem(ARTIFACT, token); } catch (e) { /* sin storage: se omite */ }
  };

  // Higiene de auth del logout (venía de app.js, Fase 0): la limpieza es de
  // la autoridad, no del chrome. En la Fase 1B esto además tiene que bajar la
  // cuenta y la cache de MSAL — otra razón para que viva acá.
  window.dwClearGraphAuth = function () {
    try { sessionStorage.removeItem(ARTIFACT); } catch (e) { /* sin storage: nada que limpiar */ }
  };

  // --- htmx: DIFERIR, NO CREAR UNA SEGUNDA AUTORIDAD ----------------------
  // `hx-vals='js:...'` se evalúa SINCRÓNICAMENTE, así que no puede esperar a
  // `dwGraphToken()`. La salida fácil sería exponer un getter síncrono para
  // los templates — y eso sería exactamente una segunda autoridad, capaz de
  // servir un token vencido justo cuando la única que sabe renovar no fue
  // consultada.
  //
  // En cambio se DIFIERE el request. Verificado contra el artefacto pinneado
  // (htmx.org@2.0.4/dist/htmx.js), no contra la documentación:
  //
  //   `issueAjaxRequest` entra en 4165; dispara `htmx:confirm` en 4218
  //   exponiendo `issueRequest`; `triggerEvent` devuelve false ante
  //   `defaultPrevented` (3050-3052), lo que aborta esta pasada; y
  //   `getExpressionVars` —que evalúa `hx-vals`— recién corre en 4341.
  //
  // O sea: el gate está 123 líneas ANTES de que se armen los parámetros. Y
  // reemitir con `issueRequest(true)` entra con `confirmed = true`, que
  // saltea el `if (confirmed === undefined)` de 4213: no hay loop posible.
  //
  // Los templates quedan SIN la credencial. Sólo declaran que la necesitan
  // con `data-dw-graph-token`, y el parámetro lo inyecta este hook.
  const MARKER = "data-dw-graph-token";
  const PARAM = "graph_token";

  // Indexado por elemento (WeakMap, para no retener nodos removidos del DOM):
  // "el token resuelto para ESTE request". Hoy `set` y `get` caen en la misma
  // cadena sincrónica, así que un slot global también alcanzaría — se elige el
  // mapa igual porque en la Fase 1B la resolución deja de ser instantánea y la
  // semántica correcta ya está en su lugar.
  const resolved = new WeakMap();

  function needsGraph(elt) {
    return !!(elt && elt.closest && elt.closest("[" + MARKER + "]"));
  }

  // NO hay guard de "ya resolví para este elemento", a propósito. Verificado en
  // htmx 2.0.4: cuando un request se ENCOLA (hx-sync, o un segundo disparo con
  // un xhr en vuelo), htmx lo reemite en 4276-4287 como
  // `issueAjaxRequest(verb, path, elt, event, etc)` — SIN el 6º argumento, o
  // sea que `htmx:confirm` vuelve a dispararse cuando la cola se drena. Un
  // guard ahí dejaría pasar el token que se resolvió antes de la espera, que es
  // justo el que puede haber vencido. Sin guard, el request encolado vuelve a
  // pedirlo. Y no hay loop: la reemisión inmediata usa `issueRequest(true)`,
  // que entra con `confirmed = true` y saltea el gate.
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
