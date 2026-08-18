// commercial.js — Comercial / CRM (Commercial Hub Odoo Batch 1: Summary +
// Pipeline, primer vertical slice real, 100% lectura). MISMO patrón de
// hidratación que reporting.js (fetch + render manual sobre innerHTML, sin
// Alpine reactivo) — bootstrap en DOMContentLoaded, ver el final de este
// archivo. Consume ÚNICAMENTE los 2 endpoints propios
// (`/comercial/data/summary|pipeline`, `tipo=todos|nn|bi`), reusa
// primitives visuales COMPARTIDOS del Hub (.activity-kpi-grid/.activity-kpi,
// .dashboard-view-switch, table.dashboard-table, .empty-panel,
// .section-analysis-loading — todos en app.css/tools.css, ya usados por
// Reporting/Asignaciones) — CERO CSS propio de esta vertical, CERO chart
// library. "Distribución por Etapa" se dibuja con el primitivo COMPARTIDO
// `.category-plot` (tools.css): barras para Oportunidades sobre la escala
// izquierda y una línea con puntos para Horas/Ponderadas sobre la derecha,
// todo en UNA superficie de ~250-300px. Sin librería, sin canvas, sin
// dependencia nueva — el único SVG es la polilínea, y es presentacional.
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

// Capacidades IMPLEMENTADAS del workspace. Deliberadamente NO se listan las
// que todavía no tienen funcionalidad detrás (Licencias, Clientes, Leads, Mis
// Tareas, Historial de Perdidas, Detalle de Oportunidades): una tab muerta es
// una promesa rota, se agregan cuando exista contenido real.
const COMMERCIAL_CAPABILITIES = [
  { id: "dashboard", label: "Dashboard" },
  { id: "actividades", label: "Actividades" },
];

const commercialState = {
  tipo: "todos",
  // Capability activa. State client-side puro, sin routing nuevo: MISMO
  // criterio que Reporting (`state.experience`, sin URL) — no hay
  // requerimiento de deep-link a una capability.
  capability: "dashboard",
  // Dimensión de horas de "Distribución por Etapa" (la serie de línea). NO
  // es un filtro: no cambia universo, etapas, capability ni la tabla — sólo
  // qué magnitud dibuja la línea y su eje. Por eso vive acá y NO dispara
  // fetch.
  horasDim: "hours", // hours | weighted
  status: "idle", // idle | loading | ready | error
  errorState: "", // "config_incomplete" | "source_error" | ""
  summary: null,
  pipeline: null,
  // Actividades — LAZY (ver commercialSetCapability): Dashboard no paga el
  // costo de una capability que no se está viendo. `idle` = todavía no se
  // pidió, o quedó stale tras cambiar el filtro global.
  activities: null,
  activitiesStatus: "idle", // idle | loading | ready | error
  activitiesErrorState: "",
  // Filtros LOCALES de Actividades — client-side sobre el dataset ya
  // cargado, nunca un round-trip (ver commercialFilteredActivities).
  actPeriod: "all", // all | week:0 | week:1 | week:2
  actUser: "",
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

// ── Distribución por Etapa ─────────────────────────────────────────────────
// UNA sola superficie compacta sobre el primitivo COMPARTIDO
// `.category-plot` (tools.css): Oportunidades como barras (escala
// izquierda) y Horas/Ponderadas como línea con puntos (escala derecha),
// sobre las MISMAS posiciones de etapa.
//
// Por qué así, y no como estaba: la primera versión usaba dos bloques de
// barras horizontales (`.bar-row`), una fila por etapa y por serie. Con 8
// etapas eran 16 filas, más de 500px de alto, y la tabla de abajo volvía a
// listar las mismas etapas por tercera vez — se leía como un ranking, no
// como una lectura de pipeline. Poner las etapas sobre el eje X gasta
// ancho en vez de alto y devuelve la lectura rápida y conjunta que daba el
// CRM legacy, con la jerarquía visual del Hub.
//
// DOBLE ESCALA, A PROPÓSITO: cantidad y horas no comparten unidad ni orden
// de magnitud. Lo que se compara es la DISTRIBUCIÓN de cada serie a lo
// largo del embudo — nunca la altura de una barra contra la posición de la
// línea, que sería un artefacto de las escalas elegidas. De ahí las
// mitigaciones obligatorias: ticks en ambos ejes, los dos arrancando en 0,
// unidad explícita en la leyenda y dos marcas visualmente distintas. Los
// valores REALES viven en el tooltip y en la tabla; el gráfico nunca
// normaliza a porcentaje.
function commercialChartTooltip(lines) {
  // Copia local mínima del helper homónimo de reporting.js — MISMA convención
  // ya establecida para `dashboardSection` (cada vertical define su copia
  // chica contra las clases compartidas; no existe renderer global). Con un
  // tercer consumidor real corresponde extraerlo, no antes.
  const arr = Array.isArray(lines) ? lines : [lines];
  return `<div class="chart-tooltip" aria-hidden="true">${arr.map(l => `<p>${commercialEsc(l)}</p>`).join("")}</div>`;
}

// Tooltip por etapa: SIEMPRE el cuadro completo (nombre de la etapa + las 4
// métricas). El eje X sólo muestra el número de etapa — esa compacidad es
// deliberada, y el nombre completo se recupera acá y en la tabla.
function commercialStageTooltipLines(stage) {
  return [
    stage.stage,
    `Oportunidades: ${commercialFmtInt(stage.count)}`,
    `Horas: ${commercialFmtInt(stage.hours)}`,
    `Ponderadas: ${commercialFmtInt(stage.weighted_hours)} hs`,
    `Revenue: ${commercialFmtMoney(stage.revenue)}`,
  ];
}

// Tope "redondo" para el eje: SIEMPRE >= el máximo real, para que la escala
// se lea en pasos limpios (2.760 → 3.000) y la serie no toque el techo.
function commercialNiceMax(value) {
  const v = Number(value);
  if (!isFinite(v) || v <= 0) return 0;
  const step = Math.pow(10, Math.floor(Math.log10(v))) / 2;
  return Math.ceil(v / step) * step;
}

// Número de etapa para el eje X: el prefijo del nombre ("4 Propuesta" → "4").
// Si una etapa no lo trae, se cae al ordinal — nunca se rompe el eje.
function commercialStageTick(stage, index) {
  const first = String(stage.stage || "").trim().charAt(0);
  return /[0-9]/.test(first) ? first : String(index + 1);
}

const COMMERCIAL_PLOT_TICKS = [0, 0.25, 0.5, 0.75, 1];

function commercialAxisHtml(side, max) {
  const ticks = COMMERCIAL_PLOT_TICKS.map(t =>
    `<span style="bottom:${(t * 100).toFixed(0)}%">${commercialFmtInt(Math.round(max * t))}</span>`
  ).join("");
  return `<div class="category-plot__axis category-plot__axis--${side}">${ticks}</div>`;
}

// Contenido INTERNO de `[data-commercial-dist]` — el switch y el gráfico. Se
// devuelve aparte del wrapper justamente para que el switch pueda
// re-renderizar SÓLO este subárbol (ver el handler): un `commercialRender()`
// completo reconstruiría todo `#commercial-main-content` y, como
// `initEventBreakdown` guarda el estado abierto/cerrado únicamente en el DOM
// (`hidden` + `.is-active`), cerraría el panel de Activas justo cuando el
// usuario toca el switch que vive adentro.
function commercialDistribucionHtml(pipeline, dim) {
  const stages = (pipeline && pipeline.stages) || [];
  if (!stages.length) return "";
  // Sólo las etapas PRESENTES en la respuesta y en el orden recibido (el
  // service ya las entrega ordenadas 1→8 y agrupa únicamente las que tienen
  // oportunidades) — nunca 8 columnas hardcodeadas, nunca la etapa 0.
  const hoursOf = s => (dim === "weighted" ? (s.weighted_hours || 0) : (s.hours || 0));
  const maxCount = commercialNiceMax(Math.max(...stages.map(s => s.count || 0)));
  const maxHours = commercialNiceMax(Math.max(...stages.map(hoursOf)));
  const hoursLabel = dim === "weighted" ? "Horas Ponderadas" : "Horas";

  const dims = [["hours", "Horas"], ["weighted", "Ponderadas"]];
  const switchButtons = dims.map(([key, label]) =>
    `<button type="button" class="${dim === key ? "is-active" : ""}" data-commercial-dim="${key}">${commercialEsc(label)}</button>`
  ).join("");

  // La COLUMNA es la superficie accesible (no la barra): cubre toda la banda
  // de la etapa, lleva el `aria-label` plano con las magnitudes reales y
  // ancla el tooltip compartido. El SVG de la línea queda `aria-hidden`
  // para no duplicar esa semántica.
  const cols = stages.map((stage, index) => {
    const lines = commercialStageTooltipLines(stage);
    const height = maxCount > 0 ? ((stage.count || 0) / maxCount * 100).toFixed(1) : "0.0";
    return `<div class="category-plot__col chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(lines.join(" · "))}">`
      + `<div class="category-plot__bar" style="height:${height}%"></div>`
      + `${commercialChartTooltip(lines)}</div>`;
  }).join("");

  const point = (stage, index) => {
    const x = ((index + 0.5) / stages.length * 100).toFixed(2);
    const y = maxHours > 0 ? (100 - hoursOf(stage) / maxHours * 100).toFixed(2) : "100.00";
    return [x, y];
  };
  const polyline = stages.map((s, i) => point(s, i).join(",")).join(" ");
  // Los puntos son `<line>` de largo cero con `stroke-linecap:round`: un
  // `<circle>` se deformaría en óvalo, porque el viewBox se estira de forma
  // no uniforme para llenar el área.
  const dots = stages.map((stage, index) => {
    const [x, y] = point(stage, index);
    return `<line x1="${x}" y1="${y}" x2="${x}" y2="${y}" stroke="var(--gold)" stroke-width="6"`
      + ` stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  }).join("");

  const grid = COMMERCIAL_PLOT_TICKS.map(t => `<i style="bottom:${(t * 100).toFixed(0)}%"></i>`).join("");
  const xlab = stages.map((stage, index) =>
    `<span>${commercialEsc(commercialStageTick(stage, index))}</span>`).join("");

  return `
    <p class="perspective-label">Distribución por Etapa</p>
    <div class="staffing-timeline-toolbar">
      <div class="dashboard-view-switch" role="group" aria-label="Dimensión de horas">${switchButtons}</div>
    </div>
    <div class="category-plot-legend">
      <b><i class="swatch-bar"></i>Oportunidades <em>· escala izq.</em></b>
      <b><i class="swatch-line"></i>${commercialEsc(hoursLabel)} <em>· escala der.</em></b>
    </div>
    <div class="category-plot">
      ${commercialAxisHtml("left", maxCount)}
      <div class="category-plot__area">
        <div class="category-plot__grid">${grid}</div>
        ${cols}
        <svg class="category-plot__line" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <polyline points="${polyline}" fill="none" stroke="var(--gold)" stroke-width="2"
            vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
          ${dots}
        </svg>
      </div>
      ${commercialAxisHtml("right", maxHours)}
      <div class="category-plot__xlab">${xlab}</div>
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

// Cuerpo del panel de "Oportunidades Activas — Análisis": gráfico ARRIBA
// (patrón), tabla ABAJO (precisión). Ambos en la MISMA `.dashboard-section`
// — no una sección nueva, que rompería la relación contextual con el KPI que
// abre este panel. La tabla NO se filtra ni se re-renderiza con el switch:
// sigue mostrando Horas y Ponderadas en columnas, así que es la referencia
// numérica completa en cualquier modo del gráfico.
// Sin etapas, `commercialDistribucionHtml` devuelve "" y el único mensaje lo
// da el `.empty-panel` de la tabla — nunca rótulos huérfanos sobre la nada.
function commercialActivasAnalysisHtml(pipeline) {
  const dist = commercialDistribucionHtml(pipeline, commercialState.horasDim);
  // El rótulo "Distribución por Etapa" lo emite el propio gráfico (vive
  // dentro del subárbol que re-renderiza el switch), así que acá NO se
  // repite — sólo el de la tabla.
  return `${dist ? `<div data-commercial-dist>${dist}</div>
      <p class="perspective-label">Detalle por Etapa</p>` : ""}
    ${commercialPipelineTableHtml(pipeline)}`;
}

// ── Capability navigation ───────────────────────────────────────────────────
// Barra de capacidades sobre el primitivo COMPARTIDO `.experience-tabs`
// (promovido a tools.css en este batch — antes vivía scopeado a Reporting, o
// sea inerte acá porque base.html no carga reporting.css). Lo que NO se reusa
// es la navegación de Reporting (EXPERIENCES/setExperience/hydration
// registry/telemetría/renderers): eso es lógica suya. Acá alcanza con un
// registry chico y un state local.
//
// A11y: `<nav>` + botones, con `aria-current` en el activo. Deliberadamente
// NO se declara `role="tablist"`/`role="tab"`: ese contrato exige roving
// tabindex y navegación por flechas, que el Hub no implementa en ningún lado
// — anunciarlo sin cumplirlo sería peor que no anunciarlo. `aria-controls` sí
// aplica: hay UN contenedor único de capability.
function commercialNavHtml() {
  const buttons = COMMERCIAL_CAPABILITIES.map(cap => {
    const active = commercialState.capability === cap.id;
    return `<button type="button" class="${active ? "is-active" : ""}"`
      + `${active ? ' aria-current="true"' : ""}`
      + ` aria-controls="commercial-capability-panel" data-commercial-capability="${cap.id}">`
      + `${commercialEsc(cap.label)}</button>`;
  }).join("");
  return `<nav class="experience-tabs" aria-label="Capacidades comerciales">${buttons}</nav>`;
}

// ── Actividades pendientes ──────────────────────────────────────────────────
// `days_until` se calcula ACÁ, no en el backend: el server cachea 300s, así
// que un valor calculado allá queda congelado y al cruzar la medianoche
// clasifica mal el bucket.
//
// La fecha llega como "YYYY-MM-DD" y es una FECHA CALENDARIO, no un instante.
// `new Date("2026-08-20")` la interpretaría como medianoche UTC y en un huso
// al oeste devolvería el día anterior — de ahí el parseo por componentes
// contra `new Date(y, m-1, d)`, que construye medianoche LOCAL. El
// `Math.round` sobre la diferencia absorbe los días de 23/25 horas de los
// cambios de horario, que si no correrían el resultado en 1.
function commercialDaysUntil(iso) {
  if (!iso) return null;
  const parts = String(iso).split("-");
  if (parts.length !== 3) return null;
  const y = Number(parts[0]), m = Number(parts[1]), d = Number(parts[2]);
  if (!isFinite(y) || !isFinite(m) || !isFinite(d)) return null;
  const target = new Date(y, m - 1, d);
  // Rechaza fechas imposibles que el constructor "normaliza" (2026-13-45).
  if (target.getFullYear() !== y || target.getMonth() !== m - 1 || target.getDate() !== d) return null;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 86400000);
}

function commercialFmtDate(iso) {
  if (!iso) return "—";
  const parts = String(iso).split("-");
  if (parts.length !== 3) return "—";
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

// Semana LUNES→DOMINGO (semántica legacy). `week:0` = la semana en curso.
function commercialDaysSinceMonday() {
  const day = new Date().getDay(); // 0 = domingo
  return day === 0 ? 6 : day - 1;
}
function commercialDaysToEndOfWeek() {
  const day = new Date().getDay();
  return day === 0 ? 0 : 7 - day;
}

// Filtros LOCALES: client-side sobre el dataset ya cargado. No van al
// endpoint porque el volumen es chico y porque las opciones de Responsable se
// DERIVAN del propio dataset (no hay catálogo de usuarios que consultar). Sin
// persistencia — no hay precedente Hub y arrastraría estado de usuario, fuera
// de alcance. Sólo LEEN el tipo global, nunca lo tocan.
function commercialFilteredActivities() {
  const all = (commercialState.activities && commercialState.activities.activities) || [];
  const period = commercialState.actPeriod;
  const user = commercialState.actUser;
  const since = commercialDaysSinceMonday();
  const toEnd = commercialDaysToEndOfWeek();
  return all.filter(act => {
    if (user && act.user !== user) return false;
    if (period !== "all") {
      const offset = parseInt(String(period).split(":")[1] ?? "0", 10);
      const days = commercialDaysUntil(act.deadline);
      // Sin fecha queda EXCLUIDA cuando hay período activo (semántica legacy
      // preservada): no se puede afirmar que caiga en esa semana.
      if (days === null) return false;
      if (days < offset * 7 - since || days > offset * 7 + toEnd) return false;
    }
    return true;
  });
}

// Buckets: exactamente los dos del legacy. Sin fecha cuenta como Pendiente.
function commercialActivityBuckets(rows) {
  const overdue = [], pending = [];
  rows.forEach(act => {
    const days = commercialDaysUntil(act.deadline);
    (days !== null && days < 0 ? overdue : pending).push({ ...act, days });
  });
  return { overdue, pending };
}

// Sección NO reordenable: sin `draggable`, sin grip, y montada FUERA de un
// contenedor `[data-dashboard-key]` — con lo cual `initDashboardSections`
// (app.js) directamente no la ve: sus listeners cuelgan del contenedor y el
// dragstart exige el atributo. Reusa el MISMO contrato visual de
// `.dashboard-section` sin heredar un comportamiento que acá sería
// incorrecto: el orden Retrasadas → Pendientes es semántico, no una
// preferencia del usuario.
function commercialStaticSection(id, title, countLabel, bodyHtml) {
  return `<details class="dashboard-section" data-section-id="${id}" open>
    <summary class="dashboard-section__head">
      <span class="dashboard-section__caret"></span>
      <span class="dashboard-section__title">${commercialEsc(title)}</span>
      ${countLabel ? `<span class="dashboard-section__count">${commercialEsc(countLabel)}</span>` : ""}
    </summary>
    <div class="dashboard-section__body">${bodyHtml}</div>
  </details>`;
}

// TODO dato que llega de Odoo se escapa acá (`commercialEsc`), incluida la
// `note` que el backend ya normalizó: esa limpieza es de payload/formato, NO
// un permiso para tratar la fuente como HTML confiable.
function commercialActivityTableHtml(rows) {
  if (!rows.length) return `<p class="empty-panel">Sin actividades para este filtro.</p>`;
  const body = rows.map(act => {
    const rel = act.days === null ? ""
      : act.days < 0 ? ` (venc. ${Math.abs(act.days)}d)`
      : act.days === 0 ? " (hoy)" : ` (${act.days}d)`;
    const detail = [act.summary, act.note].filter(Boolean).join(" · ") || "—";
    return `<tr>
      <td class="left">${commercialEsc(commercialFmtDate(act.deadline))}${commercialEsc(rel)}</td>
      <td class="left">${commercialEsc(act.type)}</td>
      <td class="left">${commercialEsc(act.opportunity)}</td>
      <td class="left">${commercialEsc(act.client)}</td>
      <td class="left">${commercialEsc(act.user)}</td>
      <td class="left">${commercialEsc(detail)}</td>
    </tr>`;
  }).join("");
  // `<colgroup>` dimensionando las columnas desde el markup: MISMO recurso
  // que el Detalle de Asignaciones (staffing_dashboard.html) para una tabla
  // de VARIAS columnas de texto. Sin esto, `table-layout:fixed` reparte el
  // sobrante en partes iguales y con `.row-label-col` (340px) para una fecha
  // corta las cinco columnas descriptivas quedaban en ~40px ("Mi…", "AC…").
  // No es CSS nuevo: es el mismo primitivo compartido.
  //
  // Anchos en PX, no en %: con porcentajes la tabla siempre mide lo que mide
  // el panel, y en 375px las seis columnas quedaban en ~50px cada una,
  // ilegibles. En px la tabla conserva su ancho y `.matrix-scroll` (primitivo
  // Hub, promovido justamente para esto) da scroll horizontal DENTRO del
  // panel — MISMO comportamiento que las matrices mensuales de Reporting. Se
  // prefiere scroll a truncar: el contenido nunca queda inalcanzable.
  // Fecha va primera y entera porque "(venc. 12d)" es la señal de triage.
  return `<div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:160px"><col style="width:70px"><col style="width:130px">
          <col style="width:80px"><col style="width:90px"><col style="width:150px">
        </colgroup>
        <thead><tr><th class="left">Fecha</th><th class="left">Tipo</th><th class="left">Oportunidad</th><th class="left">Cliente</th><th class="left">Responsable</th><th class="left">Detalle</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function commercialActivitiesControlsHtml() {
  const periods = [["all", "Todas"], ["week:0", "Esta semana"], ["week:1", "Próxima"], ["week:2", "En 2 semanas"]];
  const buttons = periods.map(([key, label]) =>
    `<button type="button" class="${commercialState.actPeriod === key ? "is-active" : ""}" data-commercial-act-period="${key}">${commercialEsc(label)}</button>`
  ).join("");
  // Responsables DERIVADOS del dataset cargado (mismo criterio que el legacy)
  // — no existe un endpoint de usuarios y no hace falta.
  const all = (commercialState.activities && commercialState.activities.activities) || [];
  const users = [...new Set(all.map(a => a.user).filter(Boolean))].sort();
  const options = users.map(u =>
    `<option value="${commercialEsc(u)}"${commercialState.actUser === u ? " selected" : ""}>${commercialEsc(u)}</option>`
  ).join("");
  return `<div class="whatsnew-filters">
      <label class="whatsnew-filter">
        <span>Período</span>
        <div class="dashboard-view-switch" role="group" aria-label="Período">${buttons}</div>
      </label>
      <label class="whatsnew-filter">
        <span>Responsable</span>
        <select data-commercial-act-user>
          <option value=""${commercialState.actUser === "" ? " selected" : ""}>Todos</option>
          ${options}
        </select>
      </label>
    </div>`;
}

function commercialActividadesHtml() {
  if (commercialState.activitiesStatus === "idle" || commercialState.activitiesStatus === "loading") {
    return commercialLoadingHtml("Cargando actividades pendientes…");
  }
  if (commercialState.activitiesStatus === "error") {
    const label = commercialState.activitiesErrorState === "config_incomplete"
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar las actividades desde el CRM en este momento.";
    return `<p class="empty-panel">${commercialEsc(label)}</p>`;
  }
  const rows = commercialFilteredActivities();
  const buckets = commercialActivityBuckets(rows);
  // KPIs: SOLO los dos que los datos sostienen directamente. Reflejan el
  // conjunto FILTRADO, para que nunca contradigan los contadores de las
  // secciones de abajo.
  const kpis = `<div class="activity-kpi-grid">
      ${commercialKpi("Total pendientes", commercialFmtInt(rows.length))}
      ${commercialKpi("Retrasadas", commercialFmtInt(buckets.overdue.length))}
    </div>`;
  // Retrasadas SIEMPRE antes que Pendientes — orden semántico, ver
  // commercialStaticSection.
  const sections = commercialStaticSection("act-retrasadas", "Retrasadas",
      `${commercialFmtInt(buckets.overdue.length)} actividad(es)`, commercialActivityTableHtml(buckets.overdue))
    + commercialStaticSection("act-pendientes", "Pendientes",
      `${commercialFmtInt(buckets.pending.length)} actividad(es)`, commercialActivityTableHtml(buckets.pending));
  return `<p class="perspective-label">Actividades Pendientes</p>
    ${commercialActivitiesControlsHtml()}
    ${kpis}
    <div>${sections}</div>`;
}

// Contenido del Dashboard (la capability inicial). Se separó de
// `commercialRender` al aparecer la segunda capability: el shell (nav +
// filtro global + contenedor) es común, y esto es sólo lo que va adentro.
function commercialDashboardHtml() {
  if (commercialState.status === "idle" || commercialState.status === "loading") {
    return commercialLoadingHtml("Cargando datos comerciales…");
  }
  if (commercialState.status === "error") {
    const label = commercialState.errorState === "config_incomplete"
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los datos comerciales desde el CRM en este momento.";
    return `<p class="empty-panel">${commercialEsc(label)}</p>`;
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
      ${commercialActivasAnalysisHtml(p)}
    </div>`;
  const sections = commercialDashboardSection("resumen", "Resumen", `${commercialFmtInt(s.activas.count)} oportunidad(es) activa(s)`, kpiGrid)
    + commercialDashboardSection("activas-analisis", "Oportunidades Activas — Análisis", `${commercialFmtInt(p.opportunities)} oportunidad(es)`, analysisPanels);
  return commercialDashboardSections("commercial", sections);
}

// Shell de la página: navegación de capacidades + filtro GLOBAL + el
// contenedor único de la capability activa. El filtro Todos/New Name/Base
// Instalada vive ACÁ, fuera del contenedor de capability y por encima de él:
// es transversal a todo Comercial, así que no se duplica dentro de cada
// capability ni se reinicia al conmutar (su estado real vive en
// `commercialState.tipo`, no en el DOM).
function commercialRender() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  root.innerHTML = `
    ${commercialNavHtml()}
    ${commercialFilterHtml()}
    <div id="commercial-capability-panel">${commercialCapabilityHtml()}</div>
  `;
  commercialWireCapability();
}

function commercialCapabilityHtml() {
  return commercialState.capability === "actividades"
    ? commercialActividadesHtml()
    : commercialDashboardHtml();
}

// Los motores compartidos se wirean SOLO cuando el Dashboard está en pantalla
// — Actividades no tiene secciones draggables ni breakdown. Se llaman después
// de cada render porque el subárbol es nuevo: nodos frescos, sin listeners
// duplicados.
function commercialWireCapability() {
  if (commercialState.capability !== "dashboard") return;
  initDashboardSections(document);
  initEventBreakdown(document, { openFirst: false });
}

// Re-render acotado al panel de la capability (sin tocar nav ni filtro
// global). Lo usan los filtros locales de Actividades y los estados de carga.
function commercialRenderCapability() {
  const panel = document.getElementById("commercial-capability-panel");
  if (!panel) return commercialRender();
  panel.innerHTML = commercialCapabilityHtml();
  commercialWireCapability();
}

// Conmutación de capability. LAZY (ver HANDOFF — Batch 3A): Dashboard no paga
// el costo de Actividades, y Actividades se pide la PRIMERA vez que se abre.
// Una re-entrada con el dataset ya `ready` no vuelve a pedir nada.
function commercialSetCapability(capability) {
  if (capability === commercialState.capability) return;
  commercialState.capability = capability;
  commercialRender();
  if (capability === "actividades" && commercialState.activitiesStatus === "idle") {
    commercialLoadActivities();
  } else if (capability === "dashboard" && commercialState.status === "idle") {
    commercialLoadData();
  }
}

async function commercialLoadData() {
  commercialState.status = "loading";
  commercialRenderCapability();
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
  commercialRenderCapability();
}

// Carga de Actividades — MISMO contrato fail-soft y MISMO loader canónico que
// el Dashboard. Una sola request; el universo (todas las `mail.activity`
// pendientes de cualquier `crm.lead`, incluidas ganadas/perdidas/archivadas)
// lo resuelve el service.
async function commercialLoadActivities() {
  commercialState.activitiesStatus = "loading";
  commercialRenderCapability();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/activities?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.activities = resp.activities;
      commercialState.activitiesStatus = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.activitiesStatus = "error";
      commercialState.activitiesErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.activitiesStatus = "error";
    commercialState.activitiesErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderCapability();
}

// Cambio del filtro GLOBAL: invalida los datasets de TODAS las capabilities
// (el tipo cambia la población de todas) pero sólo re-pide el de la que está
// en pantalla. La otra queda `idle` y se recarga sola la próxima vez que se
// abra — sin framework de cache, sólo dos flags.
function commercialSetTipo(tipo) {
  if (tipo === commercialState.tipo) return;
  commercialState.tipo = tipo;
  commercialState.status = "idle";
  commercialState.summary = null;
  commercialState.pipeline = null;
  commercialState.activities = null;
  commercialState.activitiesStatus = "idle";
  // Los filtros locales de Actividades se reinician con el dataset: sus
  // opciones de Responsable se derivan de datos que ya no son los mismos.
  commercialState.actPeriod = "all";
  commercialState.actUser = "";
  commercialRender();
  if (commercialState.capability === "actividades") commercialLoadActivities();
  else commercialLoadData();
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("commercial-main-content");
  if (!root) return; // otra página del Hub — este script no aplica.
  root.addEventListener("click", event => {
    // Switch Horas | Ponderadas (ver commercialDistribucionHtml). Re-render
    // QUIRÚRGICO de `[data-commercial-dist]` y nada más: sin fetch (el dato ya
    // está en `commercialState.pipeline`, las dos dimensiones vienen en la
    // MISMA respuesta), sin `commercialRender()` completo y sin volver a
    // llamar `initEventBreakdown` (re-wirearlo duplicaría listeners sobre los
    // mismos triggers). Así el panel de Activas sigue abierto mientras se
    // conmuta la dimensión, y la tabla queda intacta. La delegación vive en
    // `root`, que NO se reemplaza, así que el switch re-renderizado sigue
    // respondiendo sin re-wirear nada.
    const dimButton = event.target.closest("[data-commercial-dim]");
    if (dimButton) {
      const dim = dimButton.dataset.commercialDim;
      if (dim === commercialState.horasDim) return;
      commercialState.horasDim = dim;
      const host = root.querySelector("[data-commercial-dist]");
      if (host) host.innerHTML = commercialDistribucionHtml(commercialState.pipeline, dim);
      return;
    }
    const capButton = event.target.closest("[data-commercial-capability]");
    if (capButton) {
      commercialSetCapability(capButton.dataset.commercialCapability);
      return;
    }
    // Filtros LOCALES de Actividades: sólo re-render del panel, nunca fetch
    // ni cambio del estado global.
    const periodButton = event.target.closest("[data-commercial-act-period]");
    if (periodButton) {
      if (periodButton.dataset.commercialActPeriod === commercialState.actPeriod) return;
      commercialState.actPeriod = periodButton.dataset.commercialActPeriod;
      commercialRenderCapability();
      return;
    }
    const button = event.target.closest("[data-commercial-tipo]");
    if (!button) return;
    commercialSetTipo(button.dataset.commercialTipo);
  });
  root.addEventListener("change", event => {
    const select = event.target.closest("[data-commercial-act-user]");
    if (!select) return;
    commercialState.actUser = select.value;
    commercialRenderCapability();
  });
  commercialLoadData();
});
