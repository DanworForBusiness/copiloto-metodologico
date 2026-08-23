// hub_auth_authority.node.js — invocado por
// test_hub_auth_authority.py::test_hub_auth_authority_invariants_hold.
// GRAPH TOKEN LIFECYCLE — FASE 1A, comportamiento REAL de la autoridad.
//
// Ejecuta hub_auth.js contra un `document` con listeners de verdad y un htmx
// simulado que reproduce el ORDEN verificado en el artefacto pinneado
// (htmx.org@2.0.4): dispara `htmx:confirm` primero, respeta
// `defaultPrevented` abortando la pasada, y sólo despues arma los parametros
// via `htmx:configRequest`.
//
// Lo que un test de archivo NO puede probar y acá se prueba: que el request
// se DIFIERA de verdad (que no salga antes de que la autoridad conteste), que
// la reemision no entre en loop, y que dos requests marcados concurrentes no
// se pisen el token.
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

// --- un DOM y un sessionStorage minimos pero REALES ----------------------
function makeEnv(stored, opts) {
  const options = opts || {};
  const listeners = {};
  const store = Object.assign({}, stored);
  const ops = [];
  const sessionStorage = {
    getItem(k) {
      ops.push(`get:${k}`);
      if (options.throwOnAccess) throw new Error("storage particionado");
      return k in store ? store[k] : null;
    },
    setItem(k, v) { ops.push(`set:${k}`); if (options.throwOnAccess) throw new Error("x"); store[k] = v; },
    removeItem(k) { ops.push(`remove:${k}`); if (options.throwOnAccess) throw new Error("x"); delete store[k]; },
  };
  const document = {
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  };
  const win = {};
  const sandbox = { window: win, document, sessionStorage, console, Promise };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "hub_auth.js" });
  return { win, listeners, store, ops, sandbox };
}

// Un elemento con el `closest` que el hook usa para detectar el marcador.
function makeElt(marked, id) {
  const elt = { id: id || "elt", marked };
  elt.closest = (sel) => (marked && sel === "[data-dw-graph-token]" ? elt : null);
  return elt;
}

// htmx 2.0.4 simulado: confirm -> (si no se aborto) configRequest -> request.
// `skipConfirmation` reproduce el `if (confirmed === undefined)` de la 4213.
function makeHtmx(env) {
  const issued = [];      // requests que SALIERON, con sus parametros
  const order = [];       // traza para verificar el orden real
  const queued = [];      // requests ENCOLADOS (nunca llegaron a los params)
  let queueNext = false;  // la proxima reemision se encola en vez de salir
  function issueAjaxRequest(elt, confirmed) {
    order.push(`issueAjaxRequest(confirmed=${!!confirmed})`);
    // htmx 2.0.4 linea 4290: con un xhr en vuelo el request se ENCOLA y
    // retorna ANTES de `getExpressionVars` (4341). O sea: el gate ya corrio,
    // pero los parametros no se armaron. Lo encolado se reemite en 4276-4287
    // SIN el 6o argumento, asi que vuelve a pasar por el gate.
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
      // triggerEvent: devuelve false si defaultPrevented -> aborta la pasada.
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
  // Reproduce el path de COLA de htmx 2.0.4 (4276-4287): el request encolado
  // se reemite SIN el 6o argumento, o sea que vuelve a pasar por el gate.
  return {
    fire: (elt) => issueAjaxRequest(elt, undefined),
    queueTheRetry: () => { queueNext = true; },
    drainQueue: () => { const q = queued.splice(0); q.forEach(f => f()); },
    queuedCount: () => queued.length,
    issued, order,
  };
}

// El hook resuelve en microtasks: hay que drenarlas antes de mirar.
const drain = () => new Promise(r => setImmediate(r));

(async function main() {
  // --- A: el contrato ------------------------------------------------------
  {
    const env = makeEnv({ "dw-graph-token": "TOKEN-A" });
    assertTrue(typeof env.win.dwGraphToken === "function", "A: dwGraphToken existe");
    const got = env.win.dwGraphToken();
    assertTrue(got && typeof got.then === "function",
      "A: devuelve una promesa (es el async lo que hace posible renovar en 1B)");
    assertEqual(await got, "TOKEN-A", "A: resuelve el token vigente");
  }
  // Sin token: "" — NO null, NO undefined, NO excepcion. El server responde
  // `missing_token` y la Fase 0 ya lo traduce a la accion correcta.
  {
    const env = makeEnv({});
    assertEqual(await env.win.dwGraphToken(), "", "A: sin token devuelve string vacio");
  }
  // Storage particionado (incognito embebido): tampoco lanza.
  {
    const env = makeEnv({ "dw-graph-token": "X" }, { throwOnAccess: true });
    let threw = false;
    let v = "sentinel";
    try { v = await env.win.dwGraphToken(); } catch (e) { threw = true; }
    assertTrue(!threw, "A: storage inaccesible no propaga excepcion");
    assertEqual(v, "", "A: storage inaccesible resuelve a vacio");
  }

  // --- B: escritura y limpieza pasan por la autoridad ---------------------
  {
    const env = makeEnv({});
    env.win.dwRememberGraphAuth("NUEVO");
    assertEqual(env.store["dw-graph-token"], "NUEVO", "B: la autoridad guarda");
    assertEqual(await env.win.dwGraphToken(), "NUEVO", "B: y lo devuelve");
    env.win.dwClearGraphAuth();
    assertEqual(env.store["dw-graph-token"], undefined, "B: la autoridad limpia");
    assertEqual(await env.win.dwGraphToken(), "", "B: y despues no hay token");
    // Un token vacio no se guarda: escribir "" dejaria un artefacto que miente.
    env.win.dwRememberGraphAuth("");
    assertEqual(env.store["dw-graph-token"], undefined, "B: no guarda un token vacio");
  }

  // --- C: htmx DIFIERE de verdad ------------------------------------------
  {
    const env = makeEnv({ "dw-graph-token": "TOKEN-C" });
    const htmx = makeHtmx(env);
    const elt = makeElt(true, "marcado");
    htmx.fire(elt);
    // ANTES de drenar: el request NO puede haber salido.
    assertEqual(htmx.issued.length, 0,
      "C: el request no sale antes de que la autoridad conteste");
    await drain();
    assertEqual(htmx.issued.length, 1, "C: despues de la autoridad, sale una vez");
    assertEqual(htmx.issued[0].params.graph_token, "TOKEN-C",
      "C: el hook inyecta el token que resolvio la autoridad");
    // El orden real: confirm aborta, y la reemision entra con confirmed=true.
    assertEqual(htmx.order.join(" | "),
      "issueAjaxRequest(confirmed=false) | confirm -> prevented=true | "
      + "issueAjaxRequest(confirmed=true) | configRequest | REQUEST SALE",
      "C: el orden es diferir -> resolver -> reemitir -> armar params -> salir");
  }

  // --- D: sin marcador, htmx queda intacto --------------------------------
  {
    const env = makeEnv({ "dw-graph-token": "TOKEN-D" });
    const htmx = makeHtmx(env);
    htmx.fire(makeElt(false, "sinmarca"));
    assertEqual(htmx.issued.length, 1, "D: un request sin Graph sale de inmediato");
    assertEqual(htmx.issued[0].params.graph_token, undefined,
      "D: y no se le inyecta ninguna credencial");
    assertTrue(!htmx.order.includes("confirm -> prevented=true"),
      "D: no se difiere lo que no necesita Graph");
  }

  // --- E: concurrencia — dos requests marcados no se pisan ----------------
  // El panel de Admin tiene DOS botones marcados ("Refrescar cache" y
  // "Generar seed"). Con un slot global compartido, el segundo le robaria el
  // valor al primero. Por eso el hook indexa por elemento.
  //
  // OJO con lo que se prueba acá: NO que cada request lleve el token vigente
  // al momento de disparar. El contrato es "un token valido AHORA", donde
  // "ahora" es cuando la autoridad contesta — si el token se renovo mientras
  // el request esperaba, corresponde que salga el nuevo. Lo que se aisla es
  // que un elemento no consuma la resolucion del OTRO: con un slot global,
  // el segundo le roba el valor al primero y el primero sale con "".
  {
    const env = makeEnv({ "dw-graph-token": "irrelevante" });
    const htmx = makeHtmx(env);
    // Una resolucion distinta por llamada, para poder rastrear cual fue de quien.
    let n = 0;
    env.win.dwGraphToken = async function () { n += 1; return `TOKEN-${n}`; };
    const a = makeElt(true, "refrescar");
    const b = makeElt(true, "seed");
    htmx.fire(a);
    htmx.fire(b);
    await drain(); await drain();
    assertEqual(htmx.issued.length, 2, "E: los dos requests salen");
    const byId = {};
    htmx.issued.forEach(r => { byId[r.elt] = r.params.graph_token; });
    assertEqual(n, 2, "E: la autoridad se consulta una vez por request, no una para los dos");
    // Cada uno con SU resolucion, y ninguno vacio: eso es lo que el slot por
    // elemento (WeakMap) garantiza.
    assertTrue(byId["refrescar"] && byId["seed"], "E: ninguno sale sin token");
    assertTrue(byId["refrescar"] !== byId["seed"],
      "E: cada request lleva su propia resolucion");
  }

  // --- I: un request ENCOLADO vuelve a pedir el token ---------------------
  // El caso que un guard de "ya resolvi para este elemento" romperia, y que
  // solo se ve reproduciendo el encolado REAL de htmx: el gate corre, la
  // autoridad resuelve, la reemision se ENCOLA (4290) sin armar parametros, y
  // recien mucho despues se drena — reentrando por el gate (4276-4287).
  //
  // Sin guard, ese request vuelve a pedir el token y sale con el fresco. Con
  // guard, pasa derecho y consume el que quedo colgado desde antes de la
  // espera: en Fase 1A da igual, en 1B es exactamente el token vencido que
  // todo este trabajo viene a evitar.
  {
    const env = makeEnv({ "dw-graph-token": "VIEJO" });
    const htmx = makeHtmx(env);
    const elt = makeElt(true, "encolado");
    let pedidos = 0;
    env.win.dwGraphToken = async function () {
      pedidos += 1;
      return env.store["dw-graph-token"] || "";
    };
    htmx.queueTheRetry();            // la reemision se va a encolar
    htmx.fire(elt); await drain();
    assertEqual(pedidos, 1, "I: el primer disparo consulta la autoridad");
    assertEqual(htmx.issued.length, 0, "I: nada salio todavia — quedo encolado");
    assertEqual(htmx.queuedCount(), 1, "I: hay un request en la cola");
    // Pasa el tiempo, el token se renueva, y la cola se drena.
    env.store["dw-graph-token"] = "RENOVADO";
    htmx.drainQueue(); await drain();
    assertEqual(pedidos, 2, "I: el request encolado vuelve a consultar la autoridad");
    assertEqual(htmx.issued.length, 1, "I: y sale");
    assertEqual(htmx.issued[0].params.graph_token, "RENOVADO",
      "I: lleva el token fresco, no el de antes de la espera");
  }

  // --- F: la reemision no entra en loop -----------------------------------
  // Si el hook no marcara la reemision, `htmx:confirm` volveria a diferir para
  // siempre. El corte es doble: `issueRequest(true)` y el guard de `resolved`.
  {
    const env = makeEnv({ "dw-graph-token": "TOKEN-F" });
    const htmx = makeHtmx(env);
    htmx.fire(makeElt(true, "unavez"));
    await drain(); await drain(); await drain();
    assertEqual(htmx.issued.length, 1, "F: sale exactamente una vez");
    const confirms = htmx.order.filter(o => o.startsWith("confirm ->")).length;
    assertEqual(confirms, 1, "F: el gate se evalua una sola vez");
  }

  // --- G: el token se consume, no se cachea -------------------------------
  // El proximo disparo del MISMO elemento vuelve a pasar por la autoridad. Si
  // el valor quedara pegado, en la Fase 1B un token vencido se reusaria.
  {
    const env = makeEnv({ "dw-graph-token": "PRIMERO" });
    const htmx = makeHtmx(env);
    const elt = makeElt(true, "repetido");
    htmx.fire(elt); await drain();
    env.store["dw-graph-token"] = "SEGUNDO";
    htmx.fire(elt); await drain();
    assertEqual(htmx.issued.length, 2, "G: los dos disparos salen");
    assertEqual(htmx.issued[0].params.graph_token, "PRIMERO", "G: el primero lleva el viejo");
    assertEqual(htmx.issued[1].params.graph_token, "SEGUNDO",
      "G: el segundo vuelve a preguntar (no reusa un token cacheado)");
  }

  // --- H: si la autoridad falla, el request sale igual con "" -------------
  // Dejarlo colgado seria peor: un spinner infinito sin explicacion. Con ""
  // el server responde `missing_token` y el usuario ve la accion que resuelve.
  {
    const env = makeEnv({ "dw-graph-token": "X" });
    const htmx = makeHtmx(env);
    env.win.dwGraphToken = async function () { throw new Error("autoridad caida"); };
    htmx.fire(makeElt(true, "falla"));
    await drain(); await drain();
    assertEqual(htmx.issued.length, 1, "H: el request no queda colgado");
    assertEqual(htmx.issued[0].params.graph_token, "",
      "H: sale con vacio y el server decide");
  }

  if (failures > 0) {
    console.log(`${failures} Hub auth authority invariant(s) FAILED`);
    process.exit(1);
  }
  console.log("ALL HUB AUTH AUTHORITY INVARIANTS PASSED");
  process.exit(0);
})();
