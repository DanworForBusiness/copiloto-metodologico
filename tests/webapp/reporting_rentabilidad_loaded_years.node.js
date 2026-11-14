"use strict";
// Same VM pattern as reporting_initial_load_contract.node.js. No real transport.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.resolve(__dirname, "../../webapp/static/reporting.js"), "utf8");
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const ok = (rentabilidad = {}) => ({ ok: true, json: async () => ({ state: "ok", rentabilidad }) });

function fixture(handler = () => ok()) {
  const requests = [];
  const progress = [];
  let markup = "";
  const sandbox = {
    document: { addEventListener() {}, querySelector() { return null; } },
    window: {}, console, URLSearchParams,
    sessionStorage: { getItem() { return ""; } },
    dwGraphToken: async () => "simulated-token",
    HubForegroundProgress: Object.fromEntries(["begin", "complete", "fail", "settle"].map(op => [op, () => progress.push(op)])),
    setInterval() { return 0; }, clearInterval() {},
    fetch: async (url, options) => {
      assert.equal(url, "/operaciones/reporting/integral/data/rentabilidad");
      const years = options.body.get("anios");
      requests.push(years);
      // Prevent an unfixed render loop from hanging the test process.
      if (requests.length > 10) return new Promise(() => {});
      return handler(years, requests.length);
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "reporting.js" });
  vm.runInContext("RENTABILIDAD_PROYECTOS.splice(0); RENTABILIDAD_NOASIGNADAS_BY_YEAR = {};", sandbox);
  const state = vm.runInContext("state", sandbox);
  state.rentabilidadDataStatus = "ready";
  state.rentabilidadAniosStatus = "ready";
  state.rentabilidadAnios = new Set(["2027"]);
  // Exercise the real dispatcher/loaders/merge/filter handlers. Unrelated
  // chart markup and facet reconciliation have their own existing tests.
  sandbox.reconcileExperienceFacets = () => {};
  sandbox.renderRentabilidadResumen = () => "result";
  sandbox.renderRentabilidadCertEjecutadas = () => "result";
  sandbox.render = () => { markup = sandbox.renderRentabilidad(); };
  return { sandbox, state, requests, progress, markup: () => markup,
    rows: () => vm.runInContext("RENTABILIDAD_PROYECTOS", sandbox) };
}

async function run() {
  {
    const f = fixture(() => ok({ proyectos: [{ proyecto: "P", certByYear: { "2027": [12] } }] }));
    await f.sandbox.loadRentabilidadColdYear("2027");
    await flush();
    assert(f.sandbox.rentabilidadLoadedYears().has("2027"), "successful year must not depend on unassigned hours");
    assert.equal(f.requests.length, 1, "render after success must not fetch again");
    assert.equal(f.rows()[0].certByYear["2027"][0], 12);
    assert(!f.markup().includes("Cargando datos"));
    for (const key of ["cliente", "proyecto", "estado"]) f.sandbox.setFilter("rentabilidad", key, "filter");
    f.sandbox.toggleRentabilidadAnio("2027");
    f.sandbox.toggleRentabilidadAnio("2027");
    await f.sandbox.loadRentabilidadColdYear("2027");
    await flush();
    assert.equal(f.requests.length, 1, "filters, reselecting and redundant direct calls reuse cached years");
    assert(f.progress.includes("complete"));
  }
  {
    const f = fixture(() => ok({ proyectos: [], noAsignadasByYear: {} }));
    await f.sandbox.loadRentabilidadColdYear("2027");
    await flush();
    assert(f.sandbox.rentabilidadLoadedYears().has("2027"));
    assert.equal(f.rows().length, 0, "empty successful year is legitimate");
    assert.equal(f.requests.length, 1);
    assert(!f.markup().includes("Cargando datos"));
  }
  for (const failed of [
    () => ({ ok: true, json: async () => ({ state: "source_error" }) }),
    () => { throw new Error("simulated network failure"); },
    () => ({ ok: false, json: async () => ({ state: "ok", rentabilidad: {} }) }),
    () => ({ ok: true, json: async () => { throw new Error("invalid json"); } }),
  ]) {
    const f = fixture((years, count) => count === 1 ? failed() : ok());
    await f.sandbox.loadRentabilidadColdYear("2027");
    await flush();
    assert(!f.sandbox.rentabilidadLoadedYears().has("2027"));
    assert(f.state.rentabilidadYearErrors.has("2027"));
    assert(!f.state.rentabilidadYearLoading.has("2027"));
    f.sandbox.render();
    await flush();
    assert.equal(f.requests.length, 1, "failed years do not trigger automatic render retries");
    await f.sandbox.loadRentabilidadColdYear("2027");
    await flush();
    assert.equal(f.requests.length, 2, "an explicit retry remains possible");
    assert(f.sandbox.rentabilidadLoadedYears().has("2027"));
    assert(!f.state.rentabilidadYearErrors.has("2027"));
    assert(f.progress.includes("fail"));
  }
  {
    const pending = new Map();
    const f = fixture(year => new Promise(resolve => pending.set(year, resolve)));
    f.state.rentabilidadAnios.add("2028");
    f.sandbox.render();
    await flush();
    assert.equal(f.requests.length, 2);
    assert(f.state.rentabilidadYearLoading.has("2027"));
    assert(f.state.rentabilidadYearLoading.has("2028"));
    f.sandbox.render();
    await flush();
    assert.equal(f.requests.length, 2, "single flight is independent for each selected year");
    pending.get("2027")(ok());
    pending.get("2028")({ ok: true, json: async () => ({ state: "source_error" }) });
    await flush();
    assert(f.sandbox.rentabilidadLoadedYears().has("2027"));
    assert(!f.sandbox.rentabilidadLoadedYears().has("2028"));
    assert(f.state.rentabilidadYearErrors.has("2028"));
    assert(!f.state.rentabilidadYearErrors.has("2027"));
    const retry = f.sandbox.loadRentabilidadColdYear("2028");
    await flush();
    pending.get("2028")(ok());
    await retry;
    f.sandbox.setFilter("rentabilidad", "cliente", "another");
    await flush();
    assert.equal(f.requests.filter(y => y === "2027").length, 1);
    assert.equal(f.requests.filter(y => y === "2028").length, 2);
    assert(f.sandbox.rentabilidadLoadedYears().has("2028"));
  }
  {
    const f = fixture();
    f.state.rentabilidadAnios.clear();
    f.state.rentabilidadDataStatus = "idle";
    await f.sandbox.loadRentabilidadData();
    await flush();
    const current = new Date().getFullYear();
    assert.equal(f.requests[0], `${current},${current - 1}`, "initial request explicitly identifies its hot window");
    for (const year of [String(current), String(current - 1)]) {
      assert(f.sandbox.rentabilidadLoadedYears().has(year), "even empty initial years are hydrated");
      f.state.rentabilidadAnios = new Set([year]);
      f.sandbox.render();
    }
    await flush();
    assert.equal(f.requests.length, 1, "initial hot years are not fetched again");
  }
  {
    const f = fixture(() => ({ ok: false, json: async () => ({ state: "source_error" }) }));
    f.state.rentabilidadAnios.clear();
    f.sandbox.render = () => {};
    await f.sandbox.loadRentabilidadData();
    assert.equal(f.sandbox.rentabilidadLoadedYears().size, 0, "failed initial hydration cannot mark years loaded");
    assert.notEqual(f.state.rentabilidadDataStatus, "ready");
  }
  console.log("ALL RENTABILIDAD LOADED YEAR INVARIANTS PASSED (9 scenarios)");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
