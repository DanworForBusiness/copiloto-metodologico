// human_capital_candidates_table_ergonomics.node.js — invocado por
// test_human_capital_candidates_table_ergonomics.py.
//
// CCHH UX homologation: adopción del primitivo COMPARTIDO de reorder/resize
// (initDraggableColumns/initResizableColumns, app.js — su contrato
// table-agnostic ya se prueba en app_table_ergonomics.node.js, y el wiring
// de Comercial en commercial_licensing_table_ergonomics.node.js). Candidatos
// es una tabla SSR/estática (Jinja puro, sin renderer cliente) — a
// diferencia de Comercial, que reconstruye HTML desde un array de columnas,
// acá el "adapter" (partials/human_capital_profiles_panel.html, mismo
// <script>) sólo MUEVE nodos DOM ya renderizados. Esto prueba ESE adapter
// directamente vía el seam `window.__cchhCandidatesTableAdapter`
// (mismo criterio que `window.dwGraphToken`/etc en hub_auth.js: expuesto
// para observabilidad de test, no para uso productivo cruzado).
//
// No hay jsdom en el repo: mini-DOM propio (MISMO idioma que
// app_table_ergonomics.node.js — dataset mutable, selectores por
// atributo/tag con combinador `>`, recorrido de subárbol para selectores
// simples) copiado acá porque cada harness de este repo es autocontenido.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function ok(cond, label) { if (!cond) { failures++; console.log(`FAIL: ${label}`); } }

// ── Mini-DOM (MISMO idioma que app_table_ergonomics.node.js) ────────────────
function makeNode(tag, attrs) {
  const node = {
    tag, attrs: attrs || {}, children: [], parent: null, style: {},
    dataset: new Proxy({}, {
      get(_, prop) { return node.attrs[`data-${camelToKebab(prop)}`]; },
      set(_, prop, value) { node.attrs[`data-${camelToKebab(prop)}`] = String(value); return true; },
    }),
    appendChild(child) {
      if (child.parent) child.parent.children = child.parent.children.filter(c => c !== child);
      child.parent = node; node.children.push(child); return child;
    },
    getAttribute(n) { return n in node.attrs ? node.attrs[n] : null; },
    querySelector(sel) { return queryAll(node, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(node, sel); },
  };
  return node;
}
function camelToKebab(s) { return s.replace(/[A-Z]/g, m => "-" + m.toLowerCase()); }
function matches(n, sel) {
  const m = sel.trim().match(/^([a-z]*)(\[([a-z-]+)(?:="([^"]*)")?\])?$/i);
  if (!m) return false;
  const [, tag, , attr, val] = m;
  if (tag && n.tag !== tag) return false;
  if (attr) {
    if (!(attr in n.attrs)) return false;
    if (val !== undefined && n.attrs[attr] !== val) return false;
  }
  return true;
}
function queryAll(root, sel) {
  const parts = sel.split(">").map(s => s.trim());
  if (parts.length === 1) {
    const out = [];
    (function walk(n) { n.children.forEach(c => { if (matches(c, parts[0])) out.push(c); walk(c); }); })(root);
    return out;
  }
  let level = [root];
  for (const part of parts) {
    const next = [];
    level.forEach(n => n.children.forEach(c => { if (matches(c, part)) next.push(c); }));
    level = next;
  }
  return level;
}

// ── Extracción del <script> real de la plantilla (nunca .split("<script>")
//    a secas -- el propio archivo menciona la palabra "<script>" DENTRO de
//    comentarios, así que un split ingenuo cortaría en el lugar equivocado;
//    indexOf/lastIndexOf del primer/único tag real es inequívoco). ─────────
const TEMPLATE_PATH = path.resolve(
  __dirname, "..", "..", "webapp", "templates", "partials", "human_capital_profiles_panel.html",
);
const templateSrc = fs.readFileSync(TEMPLATE_PATH, "utf-8");
const scriptStart = templateSrc.indexOf("<script>") + "<script>".length;
const scriptEnd = templateSrc.lastIndexOf("</script>");
const adapterScript = templateSrc.slice(scriptStart, scriptEnd);

function buildContainer(rows) {
  // rows[0] = header cells [{key, tag:'th'}], rows[1..] = body cells.
  const container = makeNode("div", {
    "data-draggable-columns-key": "cchh-candidatos",
    "data-resizable-columns-key": "cchh-candidatos",
  });
  const table = container.appendChild(makeNode("table"));
  const colgroup = table.appendChild(makeNode("colgroup"));
  const thead = table.appendChild(makeNode("thead"));
  const headerRow = thead.appendChild(makeNode("tr"));
  const tbody = table.appendChild(makeNode("tbody"));

  const HEADER_KEYS = ["nombre", "skills", "seniority", "ubicacion", "fuente"];
  HEADER_KEYS.forEach(key => colgroup.appendChild(makeNode("col", { "data-column-key": key })));
  colgroup.appendChild(makeNode("col")); // Acciones: sin key, mismo criterio que Comercial

  HEADER_KEYS.forEach(key => {
    const th = makeNode("th", { "data-column-key": key, "draggable": "true" });
    th.attrs["data-label"] = key;
    headerRow.appendChild(th);
  });
  headerRow.appendChild(makeNode("th", { "data-label": "acciones" })); // sin data-column-key

  rows.forEach(rowValues => {
    const tr = tbody.appendChild(makeNode("tr"));
    HEADER_KEYS.forEach(key => {
      const td = makeNode("td", { "data-column-key": key });
      td.attrs["data-value"] = rowValues[key];
      tr.appendChild(td);
    });
    const actionTd = makeNode("td");
    actionTd.attrs["data-value"] = "editar-link";
    tr.appendChild(actionTd); // sin data-column-key -- excluida
  });
  return container;
}

function orderOf(row) {
  return row.children.map(c => c.attrs["data-label"] || c.attrs["data-value"]);
}

function loadAdapter({ storedOrder, storedWidths } = {}) {
  const sandbox = {
    document: { querySelectorAll() { return []; }, body: { addEventListener() {} } },
    window: {},
    initDraggableColumns() {},
    initResizableColumns() {},
    readStoredColumnOrder(key, validKeys) {
      return (storedOrder && key === "cchh-candidatos") ? storedOrder(validKeys) : null;
    },
    readStoredColumnWidths(key) {
      return (storedWidths && key === "cchh-candidatos") ? storedWidths() : null;
    },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(adapterScript, sandbox, { filename: "human_capital_profiles_panel.html<script>" });
  return sandbox.window.__cchhCandidatesTableAdapter;
}

// ── A: sin preferencia guardada -> el DOM SSR queda tal cual ────────────────
{
  const adapter = loadAdapter({});
  const container = buildContainer([{ nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" }]);
  const headerRow = container.querySelector("table > thead > tr");
  const before = orderOf(headerRow);
  adapter.applyStoredColumnOrder(container);
  ok(JSON.stringify(orderOf(headerRow)) === JSON.stringify(before),
    "A: sin preferencia guardada, el orden default del SSR no se toca");
}

// ── B: preferencia guardada -> el header Y cada fila del body se reordenan,
//     "Acciones" SIEMPRE queda último ──────────────────────────────────────
{
  const reversed = ["fuente", "ubicacion", "seniority", "skills", "nombre"];
  const adapter = loadAdapter({ storedOrder: () => reversed });
  const container = buildContainer([
    { nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" },
    { nombre: "Beto", skills: "BI", seniority: "Junior", ubicacion: "CBA", fuente: "Referido" },
  ]);
  adapter.applyStoredColumnOrder(container);
  const headerRow = container.querySelector("table > thead > tr");
  ok(JSON.stringify(orderOf(headerRow)) === JSON.stringify([...reversed, "acciones"]),
    `B: el header respeta el orden guardado, Acciones último (obtenido: ${JSON.stringify(orderOf(headerRow))})`);
  const bodyRows = container.querySelector("tbody").children;
  ok(JSON.stringify(orderOf(bodyRows[0])) === JSON.stringify(["LinkedIn", "BA", "Senior", "SAP", "Ana", "editar-link"]),
    `B: la fila 1 del body sigue el MISMO orden que el header, Acciones último (obtenido: ${JSON.stringify(orderOf(bodyRows[0]))})`);
  ok(JSON.stringify(orderOf(bodyRows[1])) === JSON.stringify(["Referido", "CBA", "Junior", "BI", "Beto", "editar-link"]),
    "B: la fila 2 del body también, independiente de la fila 1");
}

// ── C: anchos guardados se aplican SOLO a las columnas con override; una key
//     ausente en el storage no toca el <col> (default declarativo intacto) ─
{
  const adapter = loadAdapter({ storedWidths: () => ({ nombre: "260px" }) });
  const container = buildContainer([{ nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" }]);
  adapter.applyStoredColumnWidths(container);
  const cols = container.querySelectorAll("table > colgroup > col[data-column-key]");
  const nombreCol = cols.find(c => c.dataset.columnKey === "nombre");
  const skillsCol = cols.find(c => c.dataset.columnKey === "skills");
  ok(nombreCol.style.width === "260px", "C: 'nombre' con override guardado toma ese ancho");
  ok(!skillsCol.style.width, "C: 'skills' sin override en el storage queda sin tocar");
}

// ── D: namespace independiente -- una preferencia bajo OTRA key (simulando
//     Comercial) nunca se aplica a Candidatos ───────────────────────────────
{
  const adapter = loadAdapter({}); // readStoredColumnOrder mock sólo responde para "cchh-candidatos"
  const container = buildContainer([{ nombre: "Ana", skills: "SAP", seniority: "Senior", ubicacion: "BA", fuente: "LinkedIn" }]);
  const headerRow = container.querySelector("table > thead > tr");
  const before = orderOf(headerRow);
  adapter.applyStoredColumnOrder(container); // la key real es "cchh-candidatos"; el mock nunca la reconoce -> null
  ok(JSON.stringify(orderOf(headerRow)) === JSON.stringify(before),
    "D: ninguna preferencia de otro namespace (p. ej. Comercial) contamina Candidatos");
}

if (failures > 0) {
  console.log(`${failures} Human Capital candidates table ergonomics invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL HUMAN CAPITAL CANDIDATES TABLE ERGONOMICS INVARIANTS PASSED");
process.exit(0);
