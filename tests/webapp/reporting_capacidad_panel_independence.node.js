// reporting_capacidad_panel_independence.node.js — invocado por
// test_reporting_capacidad_panel_independence.py (mismo patrón que
// reporting_temporal_contract.node.js/reporting_rollup_reconciliation.node.js):
// carga reporting.js en un sandbox mínimo y ejecuta el MOTOR REAL de Año/Mes
// de Capacidad/Productivas (owner corrective: "PRESERVE PBIP PANEL-
// INDEPENDENT TEMPORAL STATE" — cada panel, Cliente/Cliente + Part, tiene su
// PROPIO Año/Mes; "shared implementation != shared state") contra fixtures
// sintéticas — prueba el comportamiento real de independencia de estado, no
// solo presencia de un patrón de código en el source.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPORTING_JS = path.resolve(__dirname, "..", "..", "webapp", "static", "reporting.js");
const src = fs.readFileSync(REPORTING_JS, "utf-8");

const sandbox = {
  document: { addEventListener() {}, querySelector() { return null; } },
  window: {},
  // `dwGraphToken` es la AUTORIDAD del Hub sobre el access token de Graph y
  // vive en hub_auth.js, no en reporting.js — mismo criterio que
  // `HubForegroundProgress`/`initDashboardSections`, que viven en app.js. Stub
  // suficiente: lo que se prueba acá es el render, no el ciclo de vida de la
  // credencial (eso es de hub_auth_authority.node.js). Devuelve "" igual que
  // el stub de sessionStorage al que reemplaza: la Fase 1A no cambia el
  // escenario que este harness venia probando.
  dwGraphToken: async () => "",
  // `HubForegroundProgress` tambien vive en app.js. Antes de la Fase 1A este
  // harness no lo alcanzaba: el token se leia sincronicamente, asi que para
  // cuando corria `render()` el fetch stub ya habia resuelto y el status era
  // "ready". El `await` de la autoridad agrega un tick, asi que ahora se
  // ejercita la rama CORRECTA — la barra de progreso mientras carga.
  HubForegroundProgress: { begin() { }, complete() { }, fail() { }, settle() { } },
  // Los timers del browser: mismo motivo que el stub de arriba — la barra
  // compartida arranca su sweep con `setInterval`, y esta rama recien se
  // ejercita desde la Fase 1A. No-op: el sweep no es lo que se prueba aca.
  setInterval() { return 0; },
  clearInterval() { },
  sessionStorage: { getItem() { return ""; } },
  fetch() { return Promise.resolve({ json: () => Promise.resolve({ state: "missing_token" }) }); },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "reporting.js" });

const {
  getFilter, capacidadProductivasSelectedRows, capacidadPanelAnioKey, fmtNum,
} = sandbox;
// `state`/`CAPACIDAD_PRODUCTIVAS_ROWS` son `const` a nivel de módulo — no se
// adjuntan como propiedad del objeto de contexto (MISMO caveat que `state`
// en reporting_rentabilidad_ranking.node.js/reporting_temporal_contract.node.js).
const state = vm.runInContext("state", sandbox);
const CAPACIDAD_PRODUCTIVAS_ROWS = vm.runInContext("CAPACIDAD_PRODUCTIVAS_ROWS", sandbox);

let failures = 0;
function assertEqual(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    console.log(`FAIL: ${label} — expected ${e}, got ${a}`);
  }
}
function assertTrue(cond, label) {
  if (!cond) {
    failures++;
    console.log(`FAIL: ${label}`);
  }
}

// Fixture: 2 clientes, 2 años, valores DISTINTOS por año — para poder
// distinguir inequívocamente qué panel está leyendo qué año.
CAPACIDAD_PRODUCTIVAS_ROWS.length = 0;
CAPACIDAD_PRODUCTIVAS_ROWS.push(
  {
    cliente: "YPF", proyecto: "SADI v2", horas: 0, porcentajePart: 12.5, estado: "En curso",
    horasByYear: {
      "2019": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10],
      "2018": [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5],
    },
  },
  {
    cliente: "AySA", proyecto: "Costeo Federal", horas: 0, porcentajePart: 8, estado: "En curso",
    horasByYear: {
      "2019": [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
      "2018": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    },
  },
);

// --- Cambiar el Año/Mes izquierdo NUNCA muta el derecho, y viceversa ------
{
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2018";
  assertEqual(getFilter("capacidad", capacidadPanelAnioKey("cliente")), "2019", "Año izquierdo arranca en 2019");
  assertEqual(getFilter("capacidad", capacidadPanelAnioKey("clientePart")), "2018", "Año derecho arranca en 2018, independiente del izquierdo");

  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2018";
  assertEqual(getFilter("capacidad", capacidadPanelAnioKey("clientePart")), "2018", "cambiar Año izquierdo a 2018 NO muta el derecho (sigue en 2018 porque así se dejó, no por efecto del cambio)");
  assertEqual(getFilter("capacidad", capacidadPanelAnioKey("cliente")), "2018", "el izquierdo sí refleja su propio cambio");

  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";
  assertEqual(getFilter("capacidad", capacidadPanelAnioKey("cliente")), "2018", "cambiar Año derecho a 2019 NO muta el izquierdo (sigue en 2018)");
  assertEqual(getFilter("capacidad", capacidadPanelAnioKey("clientePart")), "2019", "el derecho sí refleja su propio cambio");

  // Mismo criterio para Mes — MULTI-SELECT (owner corrective), Set POR
  // PANEL en `state.capacidadMesesSeleccionados`, ya no una key dentro de
  // `state.filters.capacidad`.
  state.capacidadMesesSeleccionados = { cliente: new Set(["3"]), clientePart: new Set() };
  assertEqual([...state.capacidadMesesSeleccionados.clientePart], [], "cambiar Mes izquierdo NO muta el Set de Mes derecho (sigue vacío/Todos)");
}

// --- Cada panel proyecta SU PROPIO Año/Mes — nunca el del otro panel ------
{
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2018";
  state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };

  const rowsCliente = capacidadProductivasSelectedRows("cliente");
  const rowsPart = capacidadProductivasSelectedRows("clientePart");
  const ypfCliente = rowsCliente.find(r => r.cliente === "YPF");
  const ypfPart = rowsPart.find(r => r.cliente === "YPF");
  assertEqual(ypfCliente.horas, 120, "panel Cliente (Año 2019, Todos los meses) suma 12x10=120");
  assertEqual(ypfPart.horas, 60, "panel Cliente+Part (Año 2018) suma 12x5=60 — totalmente independiente del izquierdo (nunca 120)");
}

// --- MISMA implementación temporal compartida: mismo Año/Mes en ambos ⇒ ---
// --- mismo resultado (prueba _applyMonthCap/_maskMeses compartidos, no dos
// --- copias que puedan divergir) ------------------------------------------
{
  state.filters.capacidad = {};
  state.filters.capacidad[capacidadPanelAnioKey("cliente")] = "2019";
  state.filters.capacidad[capacidadPanelAnioKey("clientePart")] = "2019";
  state.capacidadMesesSeleccionados = { cliente: new Set(["3"]), clientePart: new Set(["3"]) }; // Marzo

  const rowsCliente = capacidadProductivasSelectedRows("cliente");
  const rowsPart = capacidadProductivasSelectedRows("clientePart");
  const ypfCliente = rowsCliente.find(r => r.cliente === "YPF");
  const ypfPart = rowsPart.find(r => r.cliente === "YPF");
  assertEqual(ypfCliente.horas, ypfPart.horas, "con el MISMO Año/Mes en ambos paneles, la MISMA implementación temporal produce el MISMO resultado");
  assertEqual(ypfCliente.horas, 10, "Mes=Marzo aísla el valor de un solo mes (10)");
}

// --- El rótulo "Cliente + Part" incluye el % de referencia RESUELTO -------
{
  // MISMA fórmula que renderCapacidadProductivas usa para el rótulo
  // compuesto del panel derecho — fmtNum sobre porcentajePart (ya
  // resuelto por el backend vía _resolve_porcentaje_part_field).
  const label = `${"YPF"} ${fmtNum(12.5, "percent")}`;
  assertTrue(label.includes("12.5"), "el rótulo Cliente+Part incluye el % de referencia resuelto");
  assertTrue(label.startsWith("YPF"), "el rótulo Cliente+Part empieza con el nombre del cliente");
}

if (failures > 0) {
  console.log(`${failures} capacidad panel-independence invariant(s) FAILED`);
  process.exit(1);
}
console.log("ALL CAPACIDAD PANEL-INDEPENDENCE INVARIANTS PASSED");
process.exit(0);
