// Ejecuta el `dwSapTypes` REAL (extraído de webapp/static/app.js, con el `DwCatalogSearch.fold` real) contra tarjetas mínimas y verifica la
// búsqueda por nombre visible/typeId (sin mayúsculas ni tildes), los filtros por grupo (O dentro del grupo, Y entre grupos), el contador y «Limpiar».
// También verifica que `dwCatalogBrowse` arranque en el catálogo pedido (módulo 01) y en ninguno sin argumento (módulo 04, sin cambios).
// Uso: node tests/webapp/catalog_sap_types.node.js  (exit 0 = OK)
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "webapp", "static", "app.js"), "utf-8").split("\r\n").join("\n");
const block = (start, stop) => {
  const a = src.indexOf(start);
  const b = src.indexOf(stop, a);
  if (a < 0 || b < 0 || src.indexOf(start, a + 1) !== -1) { console.log("FAIL: bloque no encontrado (o duplicado): " + start); process.exit(1); }
  return src.slice(a, b + stop.length);
};
const snippet = [block("const DwCatalogSearch = {", "\n};\n"), block("function dwCatalogBrowse(defaultKind) {", "\n}\n"),
                 block("function dwSapTypes(total) {", "\n}\n")].join("\n");

let failures = 0;
const check = (cond, msg) => { if (!cond) { failures++; console.log("FAIL: " + msg); } };

const sandbox = { window: {}, console, String, Array, Object };
vm.createContext(sandbox);
vm.runInContext(snippet + "\nthis.dwSapTypes = dwSapTypes; this.dwCatalogBrowse = dwCatalogBrowse;", sandbox);
const { dwSapTypes, dwCatalogBrowse } = sandbox;

check(dwCatalogBrowse("connections").catalogKind === "connections", "el módulo 01 arranca en «Conexiones»");
check(dwCatalogBrowse().catalogKind === "", "el módulo 04 sigue arrancando sin catálogo elegido");
check(dwCatalogBrowse("connections").kindLabel === "Conexiones", "leyenda «Filtrado por» con el nombre del catálogo");

const card = (search, sources, categories, capabilities, requirements) =>
  ({ hidden: false, dataset: { search, sources, categories, capabilities, requirements } });
const req = (dp, extra = "") => `dp_agent_required:${dp} ip_allowlisting_required:null dis_replication_required:null advanced_mode:null generic_framework:null${extra}`;
const cards = [
  card("SAP ABAP ABAP", "SAP", "OnPremise CloudApp", "HanaSdi DataflowSource ReplicationflowSource", req("true")),
  card("Google BigQuery BIGQUERY", "NonSAP", "CloudApp", "HanaSdi DataflowSource ReplicationflowTarget", req("false")),
  card("MYSQL", "NonSAP", "CloudApp", "ReplicationflowSource", req("null", " availability:conditional")),
  card("Adverity AdverityDatatap", "Partner", "CloudApp", "PartnerSchema", req("null")),
  card("Generic HTTP GENERICHTTP", "SAP", "CloudApp OnPremise", "ApiTasks", req("null")),
];
const c = dwSapTypes(cards.length);
c.$refs = { list: { children: cards } };
const visible = () => cards.filter((x) => !x.hidden).map((x) => x.dataset.search.split(" ").pop());

c.init();
check(c.shown === 5 && c.total === 5, "sin criterios: todas visibles y contador 5/5");
c.q = "bigquery"; c.apply();
check(visible().join() === "BIGQUERY" && c.shown === 1, "búsqueda por nombre visible (sin mayúsculas)");
c.q = "generichttp"; c.apply();
check(visible().join() === "GENERICHTTP", "búsqueda por typeId");
c.q = "sap abap"; c.apply();
check(visible().join() === "ABAP", "varias palabras: todas deben aparecer");
c.clear();
c.filters.sources = ["NonSAP"]; c.apply();
check(visible().join() === "BIGQUERY,MYSQL", "filtro de origen");
c.filters.capabilities = ["ReplicationflowSource"]; c.apply();
check(visible().join() === "MYSQL", "grupos distintos se combinan (Y)");
c.filters.capabilities = ["ReplicationflowSource", "ApiTasks"]; c.filters.sources = []; c.apply();
check(visible().join() === "ABAP,MYSQL,GENERICHTTP", "opciones del MISMO grupo se suman (O)");
c.filters.capabilities = []; c.filters.requirements = ["dp_agent_required:false"]; c.apply();
check(visible().join() === "BIGQUERY", "requisito en estado «no requerido»");
c.filters.requirements = ["dp_agent_required:true"]; c.apply();
check(visible().join() === "ABAP", "requisito en estado «requerido» (no confunde «no declarado»)");
c.filters.requirements = ["availability:conditional"]; c.apply();
check(visible().join() === "MYSQL", "disponibilidad condicional");
c.filters.requirements = []; c.filters.categories = ["OnPremise"]; c.apply();
check(visible().join() === "ABAP,GENERICHTTP", "categoría On-Premise (código exacto, no subcadena)");
c.filters.categories = ["Cloud"]; c.apply();
check(c.shown === 0, "un código parcial no coincide (CloudApp ≠ Cloud)");
check(c.active === true, "hay criterios activos (habilita «Limpiar»)");
c.clear();
check(c.shown === 5 && c.q === "" && Object.values(c.filters).every((v) => Array.isArray(v) && v.length === 0) && c.active === false,
      "«Limpiar» restablece búsqueda y filtros");
const details = { open: false };
c.toggle({ querySelector: () => details });
check(details.open === true, "click en la tarjeta abre su detalle técnico");

if (failures) { console.log(`${failures} verificación(es) fallida(s)`); process.exit(1); }
console.log("OK: dwSapTypes (búsqueda, filtros combinables, contador, limpiar) y dwCatalogBrowse por defecto");
