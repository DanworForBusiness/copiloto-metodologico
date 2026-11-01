// catalog_filter_search.node.js — invocado por test_catalog_filter_search.py.
// Ejecuta el `DwCatalogSearch` y el `dwCatalogBrowse()` REALES (extraídos de webapp/static/app.js) contra un DOM mínimo y verifica el comportamiento del Catálogo:
// dropdown 1 → dropdown 2 → (valor elegido) buscador + «Limpiar» + leyenda «Filtrado por X ✕»; la ✕ restaura el 2.º dropdown; «Limpiar» solo vacía el texto;
// cambiar el dropdown 1 resetea todo; búsqueda en vivo por cualquier texto del tile, sin distinguir mayúsculas ni tildes; sin resultados → estado vacío.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8").split("\r\n").join("\n");
const a = src.indexOf("const DwCatalogSearch = {");
const f = src.indexOf("function dwCatalogBrowse(defaultKind) {");
const end = src.indexOf("\n}\n", f);
if (a < 0 || f < 0 || end < 0 || src.indexOf("function dwCatalogBrowse", f + 10) !== -1) { console.log("FAIL: bloque del buscador del Catálogo no encontrado (o duplicado) en app.js"); process.exit(1); }
const snippet = src.slice(a, end + 3);

let failures = 0;
const check = (cond, msg) => { if (!cond) { failures++; console.log("FAIL: " + msg); } };

// ── DOM mínimo ──────────────────────────────────────────────────────────────
class El {
  constructor(tag, attrs = {}, text = "") { this.tag = tag; this.attrs = { ...attrs }; this.cls = new Set((attrs.class || "").split(/\s+/).filter(Boolean)); this.children = []; this.parent = null; this.text = text; this.value = attrs.value || ""; this.hidden = "hidden" in attrs; this.focused = 0; this.offsetParent = {}; this.events = []; }
  append(c) { c.parent = this; this.children.push(c); return c; }
  get textContent() { return this.text + this.children.map((c) => c.textContent).join(" "); }
  dispatchEvent(ev) { this.events.push(ev.type); return true; }
  focus() { this.focused++; activeEl = this; }
  get options() { return this.children.filter((c) => c.tag === "option"); }
  get selectedIndex() { return Math.max(0, this.options.findIndex((o) => o.attrs.value === this.value)); }
  matchesSimple(sel) {
    // compuesto: tag, .clase, [attr], [attr="v"], :(no soportados)
    const re = /^([a-z]+)?((?:\.[\w-]+|\[[\w-]+(?:="[^"]*")?\])*)(\[hidden\])?$/;
    const m = re.exec(sel); if (!m) throw new Error("selector no soportado: " + sel);
    if (m[1] && m[1] !== this.tag) return false;
    for (const part of (m[2] || "").match(/\.[\w-]+|\[[\w-]+(?:="[^"]*")?\]/g) || []) {
      if (part[0] === ".") { if (!this.cls.has(part.slice(1))) return false; }
      else { const mm = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(part); if (!(mm[1] in this.attrs) && !(mm[1] === "hidden" && this.hidden)) return false; if (mm[2] !== undefined && this.attrs[mm[1]] !== mm[2]) return false; }
    }
    if (m[3] && !this.hidden) return false;
    return true;
  }
  all(out = []) { this.children.forEach((c) => { out.push(c); c.all(out); }); return out; }
  querySelectorAll(sel) {
    const parts = sel.trim().split(/\s+/);
    let scope = [this];
    parts.forEach((p) => { const next = []; scope.forEach((s) => s.all().forEach((e) => { if (e.matchesSimple(p) && !next.includes(e)) next.push(e); })); scope = next; });
    return scope;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
let activeEl = null;

const mkCard = (title, extra = "") => { const li = new El("li", { class: "catalog-card" }); li.append(new El("strong", {}, title)); li.append(new El("details", {}, extra)); return li; };
const mkSection = (kind, withSelect) => {
  const sec = new El("div", { "data-catalog-kind": kind });
  if (withSelect) {
    const pk = sec.append(new El("div", { class: "tree-picker catalog-picker--secondary" }));
    const sel = pk.append(new El("select", { id: "sel-" + kind }));
    sel.append(new El("option", { value: "" }, "Elegí…")); sel.append(new El("option", { value: "A" }, "Acme")); sel.append(new El("option", { value: "B" }, "Beta"));
    pk.append(new El("input", { type: "search", "data-catalog-search": "" }));
  } else sec.append(new El("input", { type: "search", "data-catalog-search": "" }));
  const res = sec.append(new El("div", { "data-catalog-results": "" }));
  return { sec, res, sel: withSelect ? sec.querySelector("select") : null };
};
const root = new El("div");
const S = { clients: mkSection("clients", true), lob: mkSection("lob", true), domains: mkSection("domains", true), data_products: mkSection("data_products", false) };
Object.values(S).forEach((x) => root.append(x.sec));
const cards = [mkCard("Acme Despachos", "Consumo: EXPORTACIONES · artefactos VF_ENVÍOS · 3 OBSERVACIONES DE GOBIERNO"), mkCard("Acme Análisis de Vencimiento", "Consumo: STOCK"), mkCard("Acme Gestión Logística", "ventas · observación")];
cards.forEach((c) => S.clients.res.append(c));
const dpCards = [mkCard("Producto Uno", "categoría Finanzas"), mkCard("Producto Dos", "Industria: Química")];
dpCards.forEach((c) => S.data_products.res.append(c));

const sandbox = { window: {}, console, String, Array, Event: class { constructor(type) { this.type = type; } }, setTimeout: (fn) => fn() };
vm.createContext(sandbox);
vm.runInContext(snippet + "\nthis.DwCatalogSearch = DwCatalogSearch; this.dwCatalogBrowse = dwCatalogBrowse;", sandbox);
const { DwCatalogSearch, dwCatalogBrowse } = sandbox;

const make = () => { const c = dwCatalogBrowse(); c.$root = root; c.$nextTick = (fn) => fn(); return c; };
const visible = (host) => host.querySelectorAll("li.catalog-card").filter((l) => !l.hidden).map((l) => l.children[0].text);

// 1. normalización: sin mayúsculas ni tildes; varias palabras = todas (en cualquier orden)
check(DwCatalogSearch.fold("ÁNÁLISIS  Gestión") === "analisis  gestion", "fold quita tildes y pasa a minúsculas");
let r = DwCatalogSearch.apply(S.clients.res, "analisis");
check(r.total === 3 && r.shown === 1 && visible(S.clients.res)[0] === "Acme Análisis de Vencimiento", "«analisis» encuentra «Análisis» (sin tildes)");
r = DwCatalogSearch.apply(S.clients.res, "ANÁLISIS");
check(r.shown === 1, "mayúsculas y tildes en la consulta tampoco importan");
// 2. busca en CUALQUIER texto del tile, no solo en el título (incluye lo que vive en <details> plegados)
r = DwCatalogSearch.apply(S.clients.res, "exportaciones");
check(r.shown === 1 && visible(S.clients.res)[0] === "Acme Despachos", "encuentra texto de consumo (fuera del título)");
r = DwCatalogSearch.apply(S.clients.res, "vf_envios");
check(r.shown === 1, "encuentra un artefacto dentro del <details> del tile");
r = DwCatalogSearch.apply(S.clients.res, "gobierno 3");
check(r.shown === 1 && visible(S.clients.res)[0] === "Acme Despachos", "varias palabras: todas deben estar, en cualquier orden");
r = DwCatalogSearch.apply(S.clients.res, "  ");
check(r.shown === 3 && cards.every((c) => !c.hidden), "consulta vacía o solo espacios: muestra todo");
r = DwCatalogSearch.apply(S.clients.res, "zzz");
check(r.total === 3 && r.shown === 0 && cards.every((c) => c.hidden), "sin coincidencias: total > 0 y shown = 0");
r = DwCatalogSearch.apply(null, "x"); check(r.total === 0 && r.shown === 0, "sin contenedor: no falla");
r = DwCatalogSearch.apply(S.clients.res, ""); check(r.shown === 3, "(prep) vuelve a mostrar todo");

// 3. flujo: dropdown 1 → dropdown 2 → buscador
let c = make();
check(c.catalogKind === "" && !c.hasPick && !c.showSearch && c.kindLabel === "", "estado inicial: sin catálogo, sin leyenda ni buscador");
c.catalogKind = "clients";
check(!c.hasPick && !c.showSearch && c.kindLabel === "Clientes", "elegido el catálogo pero no el valor: el 2.º dropdown sigue (sin buscador ni leyenda)");
S.clients.sel.value = "A"; c.pick({ target: S.clients.sel });
check(c.picked === "A" && c.pickedLabel === "Acme" && c.hasPick && c.showSearch, "elegido el valor: aparece el buscador y la leyenda");
check(activeEl && activeEl.attrs["data-catalog-search"] !== undefined, "el foco pasa al buscador");
for (const [k, label] of [["domains", "Modelos"], ["lob", "Líneas de negocio"], ["clients", "Clientes"]]) { c.catalogKind = k; check(c.kindLabel === label, "leyenda «Filtrado por " + label + "»"); }
c.catalogKind = "data_products";
check(c.showSearch && !c.hasPick && c.kindLabel === "", "Data Products no tiene 2.º dropdown: buscador desde el inicio, sin leyenda");

// 4. búsqueda en vivo + estado vacío
c = make(); c.catalogKind = "clients"; S.clients.sel.value = "A"; c.pick({ target: S.clients.sel });
c.q = "stock"; c.search(); check(visible(S.clients.res).length === 1 && !c.noMatch, "búsqueda en vivo filtra los tiles");
c.q = "nada que ver"; c.search(); check(c.noMatch === true, "sin resultados: estado vacío visible");
c.q = ""; c.search(); check(c.noMatch === false && visible(S.clients.res).length === 3, "sin texto: vuelve todo y desaparece el estado vacío");
// el estado vacío NO aparece cuando la región no tiene tiles (p. ej. el mensaje inicial del servidor)
S.lob.res.children.length = 0; c.catalogKind = "lob"; c.q = "x"; c.search(); check(c.noMatch === false, "región sin tiles: no es «sin coincidencias»");
c.catalogKind = "clients";

// 5. «Limpiar» solo vacía el texto: no toca el filtro del 2.º dropdown
c = make(); c.catalogKind = "clients"; S.clients.sel.value = "A"; c.pick({ target: S.clients.sel });
c.q = "stock"; c.search();
c.clearSearch();
check(c.q === "" && c.picked === "A" && c.hasPick && S.clients.sel.value === "A" && visible(S.clients.res).length === 3, "«Limpiar»: texto vacío, todos los tiles del subconjunto, filtro del dropdown 2 intacto");

// 6. ✕ de la leyenda: restaura el 2.º dropdown (valor vacío + change para que htmx repinte) y quita buscador y leyenda
S.clients.sel.events.length = 0; c.q = "stock"; c.search();
c.clearPick();
check(c.picked === "" && c.pickedLabel === "" && c.q === "" && !c.hasPick && !c.showSearch && c.noMatch === false, "✕: desaparecen leyenda y buscador");
check(S.clients.sel.value === "" && S.clients.sel.events.includes("change"), "✕: el 2.º dropdown vuelve a «Elegí…» y dispara su change (el servidor devuelve el estado inicial)");
check(activeEl === S.clients.sel, "✕: el foco vuelve al 2.º dropdown");

// 7. cambiar el dropdown 1 resetea TODO (todos los 2.º dropdown, el texto y los tiles ocultos)
c = make(); c.catalogKind = "clients"; S.clients.sel.value = "B"; c.pick({ target: S.clients.sel }); S.lob.sel.value = "A"; c.q = "stock"; c.search();
S.clients.sel.events.length = 0; S.lob.sel.events.length = 0;
c.resetPick();
check(c.picked === "" && c.q === "" && !c.hasPick && S.clients.sel.value === "" && S.lob.sel.value === "", "cambio del dropdown 1: filtros del 2.º dropdown reseteados");
check(S.clients.sel.events.includes("change") && S.lob.sel.events.includes("change"), "…y cada uno dispara su change");
check(cards.every((x) => !x.hidden), "…y no queda ningún tile oculto por una búsqueda previa");
S.domains.sel.events.length = 0; c.resetPick(); check(S.domains.sel.events.length === 0, "un 2.º dropdown ya vacío no dispara requests innecesarios");

if (failures) { console.log(failures + " verificación(es) fallida(s)"); process.exit(1); }
console.log("OK");
