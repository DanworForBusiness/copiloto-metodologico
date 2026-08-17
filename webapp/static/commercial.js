// commercial.js — Comercial / CRM (Commercial Hub Odoo Batch 1: Summary +
// Pipeline, primer vertical slice real, 100% lectura). MISMO patrón de
// hidratación que reporting.js (fetch + render manual sobre innerHTML, sin
// Alpine reactivo) — bootstrap en DOMContentLoaded, ver el final de este
// archivo. Consume ÚNICAMENTE los 2 endpoints propios
// (`/comercial/data/summary|pipeline`, `tipo=todos|nn|bi`), reusa
// primitives visuales COMPARTIDOS del Hub (.activity-kpi-grid/.activity-kpi,
// .dashboard-view-switch, table.dashboard-table, .empty-panel,
// .section-analysis-loading — todos en app.css/tools.css, ya usados por
// Reporting/Asignaciones) — CERO CSS nuevo, CERO chart library.
//
// Loader: el ÚNICO progress bar del Hub es `HubForegroundProgress`
// (app.js) — este archivo NUNCA toca el nodo/DOM del progress
// directamente, solo notifica lifecycle (begin/complete/fail) con la key
// "commercial". Un cache-hit server-side (mismo `tipo` reconsultado dentro
// del TTL, o el switch Todos/NN/BI que golpea el MISMO cache — ver
// commercial_service.py) resuelve casi instantáneo: begin()+complete() en
// sucesión inmediata, sin espera artificial (ver HANDOFF §12).

const commercialState = {
  tipo: "todos",
  status: "idle", // idle | loading | ready | error
  errorState: "", // "config_incomplete" | "source_error" | ""
  summary: null,
  pipeline: null,
};

function commercialEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function commercialFmtInt(value) {
  const n = Number(value);
  return isFinite(n) ? Math.round(n).toLocaleString("en-US") : "—";
}

function commercialFmtMoney(value) {
  const n = Number(value);
  return isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
}

function commercialFmtPercent(value) {
  const n = Number(value);
  return isFinite(n) ? `${n.toLocaleString("en-US", { maximumFractionDigits: 1 })}%` : "—";
}

function commercialKpi(label, value) {
  return `<div class="activity-kpi"><span class="activity-kpi__value">${commercialEsc(value)}</span>`
    + `<span class="activity-kpi__label">${commercialEsc(label)}</span></div>`;
}

function commercialLoadingHtml(label) {
  return `<p class="section-analysis-loading"><span class="section-analysis-spinner"></span>`
    + `<span class="section-analysis-loading-copy">${commercialEsc(label)}</span></p>`;
}

function commercialFilterHtml() {
  const tipos = [["todos", "Todos"], ["nn", "NN"], ["bi", "BI"]];
  const buttons = tipos.map(([key, label]) =>
    `<button type="button" class="${commercialState.tipo === key ? "is-active" : ""}" data-commercial-tipo="${key}">${commercialEsc(label)}</button>`
  ).join("");
  return `<div class="dashboard-view-switch" role="group" aria-label="Tipo">${buttons}</div>`;
}

function commercialPipelineTableHtml(pipeline) {
  if (!pipeline || !pipeline.stages || !pipeline.stages.length) {
    return `<p class="empty-panel">Sin oportunidades activas en pipeline para este filtro.</p>`;
  }
  const rows = pipeline.stages.map(stage => `
    <tr>
      <td class="left">${commercialEsc(stage.stage)}</td>
      <td>${commercialFmtInt(stage.count)}</td>
      <td>${commercialFmtInt(stage.hours)}</td>
      <td>${commercialFmtInt(stage.weighted_hours)}</td>
      <td>${commercialFmtMoney(stage.revenue)}</td>
    </tr>`).join("");
  return `
    <table class="dashboard-table">
      <thead><tr><th class="left">Etapa</th><th>Oportunidades</th><th>Horas</th><th>Ponderadas (hs)</th><th>Revenue</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function commercialRender() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;

  if (commercialState.status === "idle" || commercialState.status === "loading") {
    root.innerHTML = commercialLoadingHtml("Cargando datos comerciales…");
    return;
  }
  if (commercialState.status === "error") {
    const label = commercialState.errorState === "config_incomplete"
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los datos comerciales desde el CRM en este momento.";
    root.innerHTML = `${commercialFilterHtml()}<p class="empty-panel">${commercialEsc(label)}</p>`;
    return;
  }

  const s = commercialState.summary;
  const p = commercialState.pipeline;
  root.innerHTML = `
    ${commercialFilterHtml()}
    <div class="activity-kpi-grid">
      ${commercialKpi("Pipeline horas", commercialFmtInt(s.pipeline.hours))}
      ${commercialKpi("Pipeline ponderado (hs)", commercialFmtInt(s.pipeline.weighted_hours))}
      ${commercialKpi("Oportunidades activas", commercialFmtInt(s.activas.count))}
      ${commercialKpi("Ganadas", commercialFmtInt(s.ganadas.count))}
      ${commercialKpi("Perdidas", commercialFmtInt(s.perdidas.count))}
      ${commercialKpi("Conversión", commercialFmtPercent(s.conversion))}
    </div>
    <h2 class="commercial-pipeline-heading">Pipeline por Etapa</h2>
    ${commercialPipelineTableHtml(p)}
  `;
}

async function commercialLoadData() {
  commercialState.status = "loading";
  commercialRender();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const [summaryResp, pipelineResp] = await Promise.all([
      fetch(`/comercial/data/summary?${qs}`).then(r => r.json()),
      fetch(`/comercial/data/pipeline?${qs}`).then(r => r.json()),
    ]);
    if (summaryResp.state === "ok" && pipelineResp.state === "ok") {
      commercialState.summary = summaryResp.summary;
      commercialState.pipeline = pipelineResp.pipeline;
      commercialState.status = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      const failed = summaryResp.state !== "ok" ? summaryResp : pipelineResp;
      commercialState.status = "error";
      commercialState.errorState = failed.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.status = "error";
    commercialState.errorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRender();
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("commercial-main-content");
  if (!root) return; // otra página del Hub — este script no aplica.
  root.addEventListener("click", event => {
    const button = event.target.closest("[data-commercial-tipo]");
    if (!button) return;
    const tipo = button.dataset.commercialTipo;
    if (tipo === commercialState.tipo) return;
    commercialState.tipo = tipo;
    commercialLoadData();
  });
  commercialLoadData();
});
