// hub_transition.node.js — invocado por test_hub_vertical_transition_preload.py.
// Ejecuta el controlador REAL de webapp/static/hub_transition.js con un DOM mínimo y ejercita sus DOS vías de entrada:
// (A) el switcher de verticales del encabezado y (B) las tarjetas del landing «Danwor Hub» (links dentro de [data-hub-transition-links]).
// Imprime una línea JSON por caso; el test de Python afirma sobre ellas.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "hub_transition.js"), "utf-8");
const ORIGIN = "http://hub.test";

function makeEnv(currentPath) {
  const listeners = Object.create(null);
  const bodyClasses = new Set();
  const appended = [];
  function el(tag) {
    const attrs = Object.create(null);
    return {
      tagName: tag, children: [], className: "", textContent: "",
      setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
      appendChild(c) { this.children.push(c); return c; },
      querySelector(sel) { return sel === "[data-hub-transition-text]" ? this.children.find((c) => c.getAttribute("data-hub-transition-text") !== null) || null : null; },
    };
  }
  const document = {
    body: { classList: { add: (c) => bodyClasses.add(c), remove: (c) => bodyClasses.delete(c), contains: (c) => bodyClasses.has(c) },
            appendChild(c) { appended.push(c); return c; } },
    createElement: el,
    getElementById(id) { return appended.find((c) => c.getAttribute && c.getAttribute("id") === id) || appended.find((c) => c.id === id) || null; },
    addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
  };
  const window = { location: new URL(ORIGIN + currentPath), addEventListener(type, cb) { (listeners["w:" + type] = listeners["w:" + type] || []).push(cb); } };
  const sandbox = { document, window, URL, setTimeout: () => 1, clearTimeout: () => {} };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return { listeners, bodyClasses, appended };
}

// Elemento <a> mínimo con `closest` que resuelve SOLO el selector que el controlador consulta.
function link({ href, cls = [], parentMenu = false, inTiles = false, title = null, text = "", attrs = {} }) {
  const classes = new Set(cls);
  // Título de tarjeta: texto PROPIO + subtítulo anidado (como el <h2>{{ title }}<span>{{ accent }}</span></h2> del landing).
  const heading = title === null ? null : { childNodes: [{ nodeType: 3, nodeValue: title }, { nodeType: 1, nodeValue: null }] };
  const a = {
    href: new URL(href, ORIGIN).href, textContent: text,
    classList: { contains: (c) => classes.has(c) },
    getAttribute(k) { return k === "href" ? href : (k in attrs ? attrs[k] : null); },
    querySelector(sel) { return sel === "h1, h2, h3" ? heading : null; },
  };
  a.closest = (sel) => {
    const parts = sel.split(",").map((s) => s.trim());
    const ok = parts.some((p) => (p === ".workspace-switch-menu a[href]" && parentMenu) || (p === "a.workspace-switch-platform[href]" && classes.has("workspace-switch-platform"))
      || (p === "[data-hub-transition-links] a[href]" && inTiles));
    return ok ? a : null;
  };
  return a;
}

function click(env, a, extra = {}) {
  const ev = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false, target: a, ...extra,
               preventDefault() { this.defaultPrevented = true; } };
  for (const cb of env.listeners.click || []) cb(ev);
  const overlay = env.appended.find((c) => c.getAttribute && c.getAttribute("data-hub-transition") !== null);
  const text = overlay ? (overlay.children.find((c) => c.getAttribute("data-hub-transition-text") !== null) || {}).textContent : null;
  return { shown: env.bodyClasses.has("hub-transitioning"), prevented: ev.defaultPrevented, text,
           overlays: env.appended.filter((c) => c.getAttribute && c.getAttribute("data-hub-transition") !== null).length };
}

const out = {};
let env;
// (A) switcher del encabezado — comportamiento conservado
env = makeEnv("/copiloto"); out.switcher_other = click(env, link({ href: "/comercial", parentMenu: true, text: "Comercial" }));
env = makeEnv("/copiloto"); out.switcher_active = click(env, link({ href: "/copiloto", parentMenu: true, cls: ["is-active"], text: "Copiloto" }));
env = makeEnv("/copiloto"); out.switcher_platform = click(env, link({ href: "/onedanwor", cls: ["workspace-switch-platform"], text: "Todos los espacios" }));
env = makeEnv("/copiloto"); out.switcher_modified = click(env, link({ href: "/comercial", parentMenu: true, text: "Comercial" }), { ctrlKey: true });
// (B) tarjetas del landing
const tile = (href, label) => link({ href, inTiles: true, title: label, text: label + "SUBTITULO descripcion larga lista de capacidades" });
env = makeEnv("/onedanwor"); out.tile_click = click(env, tile("/comercial", "Comercial"));
env = makeEnv("/onedanwor"); out.tile_enter = click(env, tile("/comercial", "Comercial"), { detail: 0 });   // Enter sobre un <a> dispara click con button 0
env = makeEnv("/onedanwor"); out.tile_double = [click(env, tile("/comercial", "Comercial")), click(env, tile("/comercial", "Comercial"))];
env = makeEnv("/onedanwor"); out.tile_same_page = click(env, tile("/onedanwor", "Todos"));
env = makeEnv("/onedanwor"); out.tile_new_tab = click(env, tile("/comercial", "Comercial"), { metaKey: true });
env = makeEnv("/onedanwor"); out.tile_middle = click(env, tile("/comercial", "Comercial"), { button: 1 });
env = makeEnv("/onedanwor"); out.tile_target_blank = click(env, link({ href: "/comercial", inTiles: true, title: "Comercial", attrs: { target: "_blank" } }));
env = makeEnv("/onedanwor"); out.not_a_vertical_link = click(env, link({ href: "/comercial", text: "otro link" }));
// bfcache: volver con el navegador limpia el overlay
env = makeEnv("/onedanwor"); click(env, tile("/comercial", "Comercial")); for (const cb of env.listeners["w:pageshow"] || []) cb({ persisted: true });
out.bfcache_cleared = !env.bodyClasses.has("hub-transitioning");
process.stdout.write(JSON.stringify(out));
