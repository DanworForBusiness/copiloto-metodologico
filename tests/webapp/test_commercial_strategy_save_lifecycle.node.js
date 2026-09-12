// test_commercial_strategy_save_lifecycle.node.js — SAVE / BEFOREUNLOAD BUG
// gate (ver HANDOFF). Prueba EJECUTANDO el `x-data` real de
// webapp/templates/commercial/strategy.html (extraído del archivo fuente en
// tiempo de test, nunca una copia pegada que pueda desincronizarse) contra
// mocks de `fetch`/`window`/`confirm` — no un chequeo de texto/orden, sino
// el comportamiento REAL de `save()`/`beforeunload` bajo éxito y fallo.
//
// Bug reportado por el owner: un Save exitoso dejaba `dirty=true` antes de
// disparar `window.location.reload()` — el listener de `beforeunload`
// (armado en TODO el x-data, ver strategy.html) interpretaba esa recarga
// INTENCIONAL como una salida con cambios sin guardar y mostraba el diálogo
// nativo del navegador. Si el usuario elegía "Cancelar" ahí, la página se
// quedaba en la revision vieja mientras el servidor ya había avanzado —
// cualquier Save posterior desde esa misma pestaña fallaba con 409 (stale
// revision) sin que hubiera existido ninguna edición concurrente real.
//
// Correr con: node tests/webapp/test_commercial_strategy_save_lifecycle.node.js
// (ver el wrapper pytest homónimo en test_commercial_strategy.py, que lo
// invoca como subproceso para que forme parte de `pytest tests/`).
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const TEMPLATE_PATH = path.resolve(__dirname, '..', '..', 'webapp', 'templates', 'commercial', 'strategy.html');
// El archivo fuente usa CRLF — normalizamos a LF acá para que los
// marcadores de extracción de abajo no tengan que lidiar con `\r`.
const source = fs.readFileSync(TEMPLATE_PATH, 'utf8').replace(/\r\n/g, '\n');

// Extrae el objeto literal del x-data del panel de edición — delimitado por
// `x-data="{` (el de `cs-edit-shell`, no el de `copilotLayout(...)` de más
// arriba ni el de segmentos/subtabs más abajo) hasta su `}"` de cierre,
// justo antes de `@beforeunload.window=`.
const startMarker = 'x-data="{\n           editing: false,';
const startIdx = source.indexOf(startMarker);
assert(startIdx >= 0, 'no se encontró el x-data del panel de edición — ¿cambió la firma en strategy.html?');
const openBraceIdx = source.indexOf('{', startIdx + 'x-data="'.length - 1);
const endMarker = '\n         }"\n         @beforeunload.window=';
const endIdx = source.indexOf(endMarker, startIdx);
assert(endIdx >= 0, 'no se encontró el cierre del x-data antes de @beforeunload — ¿cambió el layout?');
const objectLiteralSrc = source.slice(openBraceIdx, endIdx + '\n         }'.length);

// Extrae la expresión de `@beforeunload.window="..."` tal cual, para
// evaluarla con el MISMO `this` que el objeto de arriba (Alpine evalúa
// ambos atributos contra el mismo componente).
const beforeunloadMatch = source.match(/@beforeunload\.window="([^"]*)"/);
assert(beforeunloadMatch, 'no se encontró @beforeunload.window en strategy.html');
const beforeunloadExprSrc = beforeunloadMatch[1];

function buildComponent(fetchImpl) {
  // eslint-disable-next-line no-new-func
  const factory = new Function('document', 'return (' + objectLiteralSrc + ');');
  const fakeScript = { textContent: JSON.stringify({ revision: 1, hero: { kicker: 'x' } }) };
  const fakeDocument = { getElementById: () => fakeScript };
  const component = factory(fakeDocument);
  global.fetch = fetchImpl;
  return component;
}

function runBeforeunload(component) {
  // Alpine evalúa `@beforeunload.window="if (dirty) {...}"` con `dirty` como
  // identificador DESNUDO (scoping mágico de Alpine, equivalente a
  // `with(component) {...}`) — replicamos exactamente eso acá (`with` NO
  // está permitido en modo estricto, pero el cuerpo de un `new Function(...)`
  // corre en modo NO estricto por defecto sin importar el `'use strict'`
  // del archivo que lo construye).
  let prevented = false;
  const event = { preventDefault: () => { prevented = true; }, returnValue: undefined };
  // eslint-disable-next-line no-new-func
  const fn = new Function('$event', 'with (this) { ' + beforeunloadExprSrc + ' }');
  fn.call(component, event);
  return prevented;
}

let passed = 0;
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; console.log('ok -', name); })
    .catch((err) => { console.error('FAIL -', name); console.error(err); process.exitCode = 1; });
}

async function main() {
  // A + B + C: dirty=true antes de Save; Save exitoso desarma dirty/editing
  // ANTES de recargar; beforeunload ya no dispara el diálogo nativo tras
  // ese Save exitoso (repro exacta del "cancelar el diálogo nativo" del
  // owner — acá simplemente probamos que el diálogo ya no se arma).
  await test('A/B/C: successful save clears dirty+editing before reload; beforeunload no longer fires', async () => {
    let reloadCalled = false;
    global.window = { location: { reload: () => { reloadCalled = true; } } };
    const component = buildComponent(async () => ({
      ok: true,
      json: async () => ({ state: 'ok', document: { revision: 2 } }),
    }));
    component.dirty = true;
    component.editing = true;
    assert.strictEqual(component.dirty, true, 'precondition: dirty debe empezar en true');
    assert.strictEqual(runBeforeunload(component), true, 'precondition: beforeunload SÍ debe armar el diálogo mientras dirty=true');

    await component.save();

    assert.strictEqual(component.dirty, false, 'un Save exitoso debe dejar dirty=false');
    assert.strictEqual(component.editing, false, 'un Save exitoso debe dejar editing=false');
    assert.strictEqual(reloadCalled, true, 'la recarga intencional sigue ocurriendo (VIEW es server-rendered)');
    assert.strictEqual(runBeforeunload(component), false, 'beforeunload NO debe armar el diálogo nativo después de un Save exitoso');
  });

  // D: un Save fallido (422/403/etc — cualquier !res.ok) preserva dirty=true.
  await test('D: failed save (non-ok response) keeps dirty=true and does not reload', async () => {
    let reloadCalled = false;
    global.window = { location: { reload: () => { reloadCalled = true; } } };
    const component = buildComponent(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: 'validation error' }),
    }));
    component.dirty = true;
    component.editing = true;

    await component.save();

    assert.strictEqual(component.dirty, true, 'un Save fallido NUNCA debe tocar dirty');
    assert.strictEqual(component.editing, true, 'un Save fallido NUNCA debe salir de edición');
    assert.strictEqual(reloadCalled, false, 'un Save fallido nunca debe recargar');
    assert.ok(component.error, 'el mensaje de error debe quedar visible');
  });

  // E: 409 (stale revision) — mismo camino que D (!res.ok), draft y dirty
  // se preservan explícitamente.
  await test('E: 409 stale revision keeps the full draft and dirty=true', async () => {
    global.window = { location: { reload: () => {} } };
    const component = buildComponent(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ state: 'stale', current_revision: 5, message: 'La Estrategia cambió desde que la cargaste...' }),
    }));
    component.dirty = true;
    component.doc.hero.kicker = 'cambio sin guardar del usuario';

    await component.save();

    assert.strictEqual(component.dirty, true);
    assert.strictEqual(component.doc.hero.kicker, 'cambio sin guardar del usuario', 'el draft del usuario nunca se descarta en un 409');
    assert.ok(component.error.includes('cambió'));
  });

  // F: falla de red (fetch rechaza) — mismo contrato: dirty se preserva.
  await test('F: network failure keeps dirty=true and draft intact', async () => {
    global.window = { location: { reload: () => {} } };
    const component = buildComponent(async () => { throw new Error('network down'); });
    component.dirty = true;
    component.doc.hero.kicker = 'otro cambio sin guardar';

    await component.save();

    assert.strictEqual(component.dirty, true);
    assert.strictEqual(component.doc.hero.kicker, 'otro cambio sin guardar');
    assert.ok(component.error);
  });

  // G: Cancel semantics sin cambios — confirm() rechazado (usuario declina
  // descartar) mantiene editing=true; confirm() aceptado restaura y limpia.
  await test('G: cancelEdit semantics unchanged', async () => {
    const component = buildComponent(async () => ({ ok: true, json: async () => ({}) }));
    component.original = { hero: { kicker: 'original' } };
    component.doc = { hero: { kicker: 'editado' } };
    component.dirty = true;
    component.editing = true;

    global.confirm = () => false; // el usuario declina descartar
    component.cancelEdit();
    assert.strictEqual(component.editing, true, 'declinar el confirm debe mantener editing=true');

    global.confirm = () => true; // el usuario acepta descartar
    component.cancelEdit();
    assert.strictEqual(component.editing, false);
    assert.strictEqual(component.dirty, false);
  });

  // H: un Save exitoso deja el componente listo para un ÚNICO ciclo — no
  // hay un segundo estado "stale" fabricado por el cliente (la revision
  // que se hubiera usado en un segundo Save ya no importa: `editing` quedó
  // en false y la recarga trae la nueva revision del servidor).
  await test('H: exactly one successful save per click, no client-side second stale attempt', async () => {
    let reloadCalls = 0;
    global.window = { location: { reload: () => { reloadCalls++; } } };
    let fetchCalls = 0;
    const component = buildComponent(async () => {
      fetchCalls++;
      return { ok: true, json: async () => ({ state: 'ok', document: { revision: 2 } }) };
    });
    component.dirty = true;
    component.editing = true;

    await component.save();

    assert.strictEqual(fetchCalls, 1);
    assert.strictEqual(reloadCalls, 1);
    assert.strictEqual(component.dirty, false);
  });

  console.log(`\n${passed} passed`);
  if (process.exitCode) {
    console.error('SOME TESTS FAILED');
    process.exit(1);
  }
}

main();
