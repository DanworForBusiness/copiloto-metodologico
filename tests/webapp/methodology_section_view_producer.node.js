// methodology_section_view_producer.node.js — invocado por test_methodology_section_view_producer.py.
// Extrae el `emitSectionView` REAL (scroll-spy de copilotLayout en webapp/static/app.js) y verifica el contrato del
// productor de `section_view` metodológico: slug canónico (con guiones) válido, rechazo de texto arbitrario, se omite
// el hero `__module__`, deduplicación por (visita de página = DW_VIEW_ID, sección), y no emite sin DW_VIEW_ID.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8").split("\r\n").join("\n");
const a = src.indexOf("const DW_SECTION_SLUG = /^[a-z][a-z0-9_-]{1,47}$/;");
const end = src.indexOf("const recomputeActive = () => {", a);
if (a < 0 || end < 0) { console.log("FAIL: bloque emitSectionView no encontrado en app.js"); process.exit(1); }
const block = src.slice(a, end);

let failures = 0;
const check = (cond, msg) => { if (!cond) { failures++; console.log("FAIL: " + msg); } };

// ── sandbox con beacons capturados ───────────────────────────────────────────
const sent = [];
const sandbox = {
  window: { DW_VIEW_ID: "view1", DW_MODULE_ID: "CP04" },
  navigator: { sendBeacon: (url, blob) => { sent.push({ url, body: JSON.parse(blob._t) }); return true; } },
  location: { pathname: "/modules/04_Modelado_y_Desarrollo" },
  Blob: class { constructor(parts) { this._t = parts[0]; } },
  fetch: () => {},
  console,
};
const emit = vm.runInNewContext(block + "\nemitSectionView;", sandbox);

// 1) slug canónico con guiones → emite; texto arbitrario / hero → no emite.
emit("referencias-de-arquitectura");
check(sent.length === 1 && sent[0].body.event === "section_view" && sent[0].body.section === "referencias-de-arquitectura",
      "slug metodológico con guiones debe emitir section_view");
check(sent[0].body.view_id === "view1" && sent[0].body.module_id === "CP04" && sent[0].url === "/telemetry/event",
      "el envelope lleva view_id, module_id y va a /telemetry/event");
const before = sent.length;
emit("DROP TABLE x"); emit("Texto Libre"); emit("__module__");
check(sent.length === before, "texto arbitrario y el hero `__module__` NO deben emitir");

// 2) deduplicación por (visita, sección): la misma sección no se emite dos veces en la misma visita.
emit("modelado-de-vistas"); emit("modelado-de-vistas");
check(sent.filter(s => s.body.section === "modelado-de-vistas").length === 1, "misma sección en la misma visita = 1 emisión");

// 3) nueva visita de página (cambia DW_VIEW_ID) → se puede volver a emitir esa sección.
sandbox.window.DW_VIEW_ID = "view2";
emit("modelado-de-vistas");
check(sent.filter(s => s.body.section === "modelado-de-vistas").length === 2, "nueva visita reinicia la deduplicación");

// 4) sin DW_VIEW_ID no se emite (no atribuible).
sandbox.window.DW_VIEW_ID = "";
const n = sent.length;
emit("convencion-de-nombres");
check(sent.length === n, "sin view_id (visita de página) no se emite");

// 5) el scroll-gate y el debounce viven en recomputeActive (sólo tras scroll real, con permanencia).
const spy = src.slice(end, src.indexOf("this.activeSection = sections[0]?.id", end));
check(/spyScrolled/.test(spy) && /setTimeout\([^,]+,\s*700\)/.test(spy) && /this\.activeSection === id/.test(spy),
      "recomputeActive emite SÓLO tras scroll real, con debounce 700ms y confirmación de permanencia");
check(/document\.addEventListener\("scroll",[^;]*spyScrolled = true/.test(src),
      "sólo el scroll (no el resize) marca spyScrolled");

if (failures) { console.log(`${failures} fallo(s)`); process.exit(1); }
console.log("OK");
