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
// UI Spine corrective (ver HANDOFF — Commercial UI Spine Audit): el KPI
// grid y la tabla de Pipeline viven DENTRO del backbone de dashboard
// compartido (.dashboard-sections[data-dashboard-key] > <details
// class="dashboard-section"> > .dashboard-section__body, wireado por
// app.js::initDashboardSections — MISMA marca que Reporting/Staffing,
// nunca un motor de collapse/drag propio). `commercialDashboardSection`/
// `commercialDashboardSections` son un helper LOCAL mínimo (mismo shape
// que reporting.js::dashboardSection/dashboardSections) — NO existe un
// renderer compartido global para esto (auditado: Reporting lo define
// local, Staffing lo arma directo en su template Alpine) — extraer un
// helper transversal ahora sería una refactorización prematura fuera de
// scope; si esta duplicación reaparece en una futura vertical, ahí sí es
// candidato natural de extracción.
//
// Loader: el ÚNICO progress bar del Hub es `HubForegroundProgress`
// (app.js) — este archivo NUNCA toca el nodo/DOM del progress
// directamente, solo notifica lifecycle (begin/complete/fail) con la key
// "commercial". Un cache-hit server-side (mismo `tipo` reconsultado dentro
// del TTL, o el switch Todos/NN/BI que golpea el MISMO cache — ver
// commercial_service.py) resuelve casi instantáneo: begin()+complete() en
// sucesión inmediata, sin espera artificial (ver HANDOFF §12).
//
// KPI → Contextual Drilldown (ver HANDOFF — Batch 2A, Oportunidades
// Activas): "Oportunidades activas" es HOY el único KPI clickeable
// (`commercialClickableKpi`) — el resto del grid sigue siendo
// `commercialKpi` display-only hasta que tengan su propio drilldown real.
// Reusa el motor COMPARTIDO `initEventBreakdown` (app.js, ya usado por
// "Eventos y catálogo" en Actividad/Admin) vía el contrato
// `[data-breakdown-group]`/`[data-event]`/`[data-breakdown]` — nunca un
// handler de click propio. `{openFirst:false}` (parámetro nuevo del motor,
// ver app.js) evita el auto-open del primer trigger que "Eventos y
// catálogo" sí quiere; acá el análisis arranca cerrado (estado codificado
// directo en el markup: panel `data-breakdown=""` visible, panel
// `data-breakdown="activas"` `hidden`).

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

// KPI → Contextual Drilldown (ver HANDOFF): variante clickeable de
// `commercialKpi`, trigger de `initEventBreakdown` (app.js) vía `data-event`
// — NUNCA un handler propio. Solo el KPI con un drilldown real gana esta
// forma; el resto sigue siendo `commercialKpi` display-only.
function commercialClickableKpi(label, value, eventKey) {
  return `<div class="activity-kpi activity-kpi--clickable" data-event="${eventKey}" role="button" tabindex="0" aria-label="Ver análisis de ${commercialEsc(label)}">`
    + `<span class="activity-kpi__value">${commercialEsc(value)}</span>`
    + `<span class="activity-kpi__label">${commercialEsc(label)}</span></div>`;
}

function commercialLoadingHtml(label) {
  return `<p class="section-analysis-loading"><span class="section-analysis-spinner"></span>`
    + `<span class="section-analysis-loading-copy">${commercialEsc(label)}</span></p>`;
}

// View Controls Spine corrective (ver HANDOFF — Tabs vs View Controls
// Audit): Todos/NN/BI es un VIEW CONTROL (filtra la población de TODAS las
// secciones), no navegación entre capacidades — por eso vive dentro de
// `.dashboard-sticky-header`, el MISMO contenedor page-level que Reporting
// (filterBarShell()) y Staffing ya usan para su fila de filtros, nunca
// suelto contra el hero. Comercial tiene un solo control simple: no adopta
// el layout interno que arma Staffing para VARIOS controles + un botón
// Limpiar en una fila (ese es un helper propio de esa vertical).
function commercialFilterHtml() {
  // Copy visible (micro-corrective aprobado): "New Name"/"Base Instalada" en
  // vez de las siglas NN/BI — los valores TÉCNICOS internos (data attribute,
  // query param `tipo=nn|bi`, contrato del service/endpoint) NO cambian.
  const tipos = [["todos", "Todos"], ["nn", "New Name"], ["bi", "Base Instalada"]];
  const buttons = tipos.map(([key, label]) =>
    `<button type="button" class="${commercialState.tipo === key ? "is-active" : ""}" data-commercial-tipo="${key}">${commercialEsc(label)}</button>`
  ).join("");
  return `<div class="dashboard-sticky-header">
    <div class="dashboard-view-switch" role="group" aria-label="Tipo">${buttons}</div>
  </div>`;
}

// Table Spine corrective (ver HANDOFF — Commercial Table Spine Audit):
// `table-layout:fixed` (tools.css) reparte el ancho sobrante ENTERO a
// cualquier columna sin ancho explícito — `.row-label-col` (340px) es el
// contrato compartido ya usado por Reporting para la columna descriptiva,
// que deja a las columnas métricas (sin clase) con el ancho default de
// 88px cada una, en vez de que la descriptiva absorba todo el espacio. Cero
// ancho/CSS propio de Comercial — mismo contrato, misma forma (1 columna
// de texto + varias métricas) que ya resuelve Reporting.
function commercialPipelineTableHtml(pipeline) {
  if (!pipeline || !pipeline.stages || !pipeline.stages.length) {
    return `<p class="empty-panel">Sin oportunidades activas en pipeline para este filtro.</p>`;
  }
  const rows = pipeline.stages.map(stage => `
    <tr>
      <td class="row-label">${commercialEsc(stage.stage)}</td>
      <td>${commercialFmtInt(stage.count)}</td>
      <td>${commercialFmtInt(stage.hours)}</td>
      <td>${commercialFmtInt(stage.weighted_hours)}</td>
      <td>${commercialFmtMoney(stage.revenue)}</td>
    </tr>`).join("");
  // Fila de totales — rollup client-side sobre las etapas ya recibidas
  // (MISMO criterio que Reporting: `tr.is-total`/`td.row-label`, nunca un
  // query ni un campo de total propio en el backend).
  const totals = pipeline.stages.reduce((acc, stage) => ({
    count: acc.count + (stage.count || 0),
    hours: acc.hours + (stage.hours || 0),
    weighted_hours: acc.weighted_hours + (stage.weighted_hours || 0),
    revenue: acc.revenue + (stage.revenue || 0),
  }), { count: 0, hours: 0, weighted_hours: 0, revenue: 0 });
  const totalRow = `
    <tr class="is-total">
      <td class="row-label">Total</td>
      <td>${commercialFmtInt(totals.count)}</td>
      <td>${commercialFmtInt(totals.hours)}</td>
      <td>${commercialFmtInt(totals.weighted_hours)}</td>
      <td>${commercialFmtMoney(totals.revenue)}</td>
    </tr>`;
  return `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr><th class="row-label-col">Etapa</th><th>Oportunidades</th><th>Horas</th><th>Ponderadas (hs)</th><th>Revenue</th></tr></thead>
        <tbody>${rows}${totalRow}</tbody>
      </table>
    </div>`;
}

// Backbone de secciones draggable/colapsables (UI Spine corrective, ver
// arriba) — MISMA marca que reporting.js::dashboardSection/
// dashboardSections, helper LOCAL (sin renderer compartido global, ver
// auditoría). `dashboardKey` propio de Comercial ("commercial") para que
// el orden guardado no choque con el de otra vertical.
function commercialDashboardSection(id, title, countLabel, bodyHtml) {
  return `<details class="dashboard-section" data-section-id="${id}" open>
    <summary class="dashboard-section__head" draggable="true">
      <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
      <span class="dashboard-section__caret"></span>
      <span class="dashboard-section__title">${commercialEsc(title)}</span>
      ${countLabel ? `<span class="dashboard-section__count">${commercialEsc(countLabel)}</span>` : ""}
    </summary>
    <div class="dashboard-section__body">${bodyHtml}</div>
  </details>`;
}
// `data-breakdown-group` (KPI → Contextual Drilldown, ver HANDOFF): ancestro
// compartido que `initEventBreakdown` (app.js) necesita para emparejar el
// trigger `[data-event]` (tile "Oportunidades activas", dentro de "Resumen")
// con su panel `[data-breakdown]` (dentro de "Oportunidades Activas —
// Análisis") — coexiste sin conflicto con `data-dashboard-key` (drag/collapse
// de secciones, `initDashboardSections`), son contratos ortogonales sobre el
// mismo contenedor. Hoy Comercial tiene un solo grupo; si en el futuro
// aparecen relaciones drilldown que NO deban compartir scope con otras, ahí
// sí correspondería acotar `data-breakdown-group` a un wrapper más chico.
function commercialDashboardSections(dashboardKey, sectionsHtml) {
  return `<div class="dashboard-sections" data-dashboard-key="${dashboardKey}" data-breakdown-group>${sectionsHtml}</div>`;
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
  const kpiGrid = `<div class="activity-kpi-grid">
      ${commercialKpi("Pipeline horas", commercialFmtInt(s.pipeline.hours))}
      ${commercialKpi("Pipeline ponderado (hs)", commercialFmtInt(s.pipeline.weighted_hours))}
      ${commercialClickableKpi("Oportunidades activas", commercialFmtInt(s.activas.count), "activas")}
      ${commercialKpi("Ganadas", commercialFmtInt(s.ganadas.count))}
      ${commercialKpi("Perdidas", commercialFmtInt(s.perdidas.count))}
      ${commercialKpi("Conversión", commercialFmtPercent(s.conversion))}
    </div>`;
  // "Oportunidades Activas — Análisis" (KPI → Contextual Drilldown, ver
  // HANDOFF): la tabla stages 1–8 (antes sección paralela "Pipeline por
  // Etapa") es semánticamente el detalle de "Oportunidades activas", no de
  // Pipeline Total (universo 0–8, drilldown propio no implementado en este
  // batch) — audit live legacy confirmó ambos universos son distintos.
  // Panel `data-breakdown=""` = estado "sin selección" (MISMO patrón que
  // "Eventos y catálogo"): visible por default, se oculta recién cuando
  // `initEventBreakdown` activa `data-breakdown="activas"` al click. Arranca
  // así en el markup — `initEventBreakdown(document, {openFirst:false})` NO
  // auto-abre el primero (ver app.js), a diferencia de su comportamiento
  // histórico.
  const analysisPanels = `
    <div class="activity-breakdown__panel" data-breakdown="">
      <p class="empty-panel">Hacé clic en "Oportunidades activas" para ver el detalle por etapa.</p>
    </div>
    <div class="activity-breakdown__panel" data-breakdown="activas" hidden>
      ${commercialPipelineTableHtml(p)}
    </div>`;
  const sections = commercialDashboardSection("resumen", "Resumen", `${commercialFmtInt(s.activas.count)} oportunidad(es) activa(s)`, kpiGrid)
    + commercialDashboardSection("activas-analisis", "Oportunidades Activas — Análisis", `${commercialFmtInt(p.opportunities)} oportunidad(es)`, analysisPanels);
  root.innerHTML = `
    ${commercialFilterHtml()}
    ${commercialDashboardSections("commercial", sections)}
  `;
  initDashboardSections(document);
  initEventBreakdown(document, { openFirst: false });
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
