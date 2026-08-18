// commercial.js — Comercial / CRM (workspace propio del Hub, 100% lectura).
// MISMO patrón de hidratación que reporting.js (fetch + render manual sobre
// innerHTML, sin Alpine reactivo) — bootstrap en DOMContentLoaded, al final
// del archivo. Consume sus 4 endpoints propios (`/comercial/data/summary|
// pipeline|analytics|activities`, `tipo=todos|nn|bi`).
//
// Reusa primitives visuales COMPARTIDOS del Hub (.activity-kpi-grid/
// .activity-kpi, .experience-tabs, .dashboard-view-switch, .dashboard-section,
// table.dashboard-table, .matrix-scroll, .chart-tooltip, .empty-panel,
// .section-analysis-loading — todos en app.css/tools.css) y aporta su CSS
// propio en commercial.css, linkeado desde su template: ahí viven la
// composición del gráfico (`.category-plot`) y el layout del landing, que
// son de esta vertical y no primitivos del Hub (ver el encabezado de ese
// archivo). Cero librería de charts, cero canvas, cero dependencia nueva.
//
// LANDING (ver HANDOFF — Landing Homologation): el Dashboard homologa las dos
// familias de tiles del CRM legacy — 9 principales (Resumen) + 6 derivadas
// (Analíticas del Pipeline) — preservando su riqueza informativa: valor,
// horas y composición por segmento. El filtro global Todos/New Name/Base
// Instalada vive en el hero, no en una franja propia.
//
// Loader: el ÚNICO progress bar del Hub es `HubForegroundProgress` (app.js) —
// este archivo NUNCA toca su DOM, sólo notifica lifecycle (begin/complete/
// fail) con la key "commercial".
//
// KPI → Contextual Drilldown: "Oportunidades activas" es HOY el único KPI
// clickeable, porque es el único con un drilldown ya homologado; el resto
// conserva su información pero NO simula una acción inexistente. Reusa el
// motor COMPARTIDO `initEventBreakdown` (app.js) vía `[data-breakdown-group]`/
// `[data-event]`/`[data-breakdown]` — nunca un handler propio. El atributo
// `data-breakdown` va sobre la SECCIÓN entera, así que el análisis no ocupa
// espacio hasta que se lo acciona y desaparece completo al cerrarlo.

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
  analytics: null,
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

// Tile del landing con la riqueza informativa del CRM legacy: valor
// principal, una magnitud secundaria opcional (horas) y la composición por
// segmento. Sigue siendo el `.activity-kpi` COMPARTIDO — sólo suma líneas.
function commercialRichKpi(label, value, { sub = "", split = null, eventKey = "" } = {}) {
  const clickable = eventKey ? ` activity-kpi--clickable" data-event="${eventKey}" role="button" tabindex="0"`
    + ` aria-label="Ver análisis de ${commercialEsc(label)}"` : '"';
  return `<div class="activity-kpi${clickable}>`
    + `<span class="activity-kpi__value">${commercialEsc(value)}</span>`
    + `<span class="activity-kpi__label">${commercialEsc(label)}</span>`
    + (sub ? `<span class="kpi-sub">${commercialEsc(sub)}</span>` : "")
    + (split || "")
    + `</div>`;
}

// Composición New Name / Base Instalada.
//
// NO es una partición: `tag_ids` es many2many, así que una oportunidad puede
// tener los dos tags o ninguno, y por eso New Name + Base Instalada no tiene
// por qué dar el total. Mostrar "otros = total − nn − bi" sería inventar
// aritmética. La lectura visible replica la del legacy (dos cifras) y la
// explicación honesta —cuántas comparten ambos segmentos y cuántas no tienen
// ninguno— viaja en el tooltip compartido, que además es foco-able para que
// no quede sólo al alcance del mouse.
function commercialSplitHtml(segment, labels) {
  if (!segment) return "";
  const nnLabel = (labels && labels.nn) || "New Name";
  const biLabel = (labels && labels.bi) || "Base Instalada";
  const lines = [
    `${nnLabel}: ${commercialFmtInt(segment.nn.count)}`,
    `${biLabel}: ${commercialFmtInt(segment.bi.count)}`,
    `Con ambos segmentos: ${commercialFmtInt(segment.both.count)}`,
    `Sin segmento asignado: ${commercialFmtInt(segment.neither.count)}`,
    "Los segmentos se superponen: no suman el total.",
  ];
  return `<div class="kpi-split chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(lines.join(" · "))}">`
    + `<span class="kpi-split__item">${commercialEsc(nnLabel)} <b>${commercialFmtInt(segment.nn.count)}</b></span>`
    + `<span class="kpi-split__item">${commercialEsc(biLabel)} <b>${commercialFmtInt(segment.bi.count)}</b></span>`
    + commercialChartTooltip(lines)
    + `</div>`;
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
  // Landing Homologation: el filtro pasó a compartir línea con el subtítulo
  // del hero (ver `hero-lead-row` en crm.html/commercial.css). Sigue siendo
  // el MISMO view control transversal — cambió dónde se dibuja, no qué hace:
  // filtra la población de TODAS las capacidades y mantiene su semántica de
  // invalidación. Ya no necesita `.dashboard-sticky-header`, que era el
  // contenedor de una franja propia que el hero absorbió.
  return `<div class="dashboard-view-switch" role="group" aria-label="Tipo">${buttons}</div>`;
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
  const a = commercialState.analytics;
  // Las horas van como magnitud secundaria, nunca pegadas al valor: el valor
  // principal es `white-space:nowrap`, y "50,963 hs" desbordaba el tile hacia
  // el de al lado en la grilla de nueve.
  const hs = value => `${commercialFmtInt(value)} hs`;
  // `segments` sólo viene en la vista sin filtrar: dentro de New Name o Base
  // Instalada el desglose sería redundante, así que el service no lo manda y
  // acá simplemente no hay split que dibujar.
  const seg = name => commercialSplitHtml(s.segments && s.segments[name], s.labels);

  // Los NUEVE tiles del landing legacy. "Oportunidades activas" es el único
  // clickeable: es el único con un drilldown Hub ya homologado. Los demás
  // conservan su información pero NO simulan una acción que todavía no
  // existe — una afordancia falsa es peor que ninguna.
  const kpiGrid = `<div class="activity-kpi-grid">
      ${commercialRichKpi("Oportunidades activas", commercialFmtInt(s.activas.count),
        { sub: hs(s.activas.hours), split: seg("activas"), eventKey: "activas" })}
      ${commercialRichKpi("Prospectos", commercialFmtInt(s.prospectos.count),
        { sub: hs(s.prospectos.hours), split: seg("prospectos") })}
      ${commercialRichKpi("Pipeline total", commercialFmtInt(s.pipeline.hours),
        { sub: "horas", split: seg("pipeline") })}
      ${commercialRichKpi("Pipeline ponderado", commercialFmtInt(s.pipeline.weighted_hours),
        { sub: "horas ponderadas" })}
      ${commercialRichKpi("Ganadas", commercialFmtInt(s.ganadas.count),
        { sub: hs(s.ganadas.hours), split: seg("ganadas") })}
      ${commercialRichKpi("Perdidas", commercialFmtInt(s.perdidas.count),
        { sub: hs(s.perdidas.hours), split: seg("perdidas") })}
      ${commercialRichKpi("Suspendidas", commercialFmtInt(s.suspendidas.count),
        { sub: hs(s.suspendidas.hours), split: seg("suspendidas") })}
      ${commercialRichKpi("Creadas esta semana", a ? commercialFmtInt(a.creadas.this_week) : "—",
        { sub: a ? `${commercialFmtInt(a.creadas.last_week)} la semana pasada` : "" })}
      ${commercialRichKpi("Conversión", commercialFmtPercent(s.conversion),
        { sub: `${commercialFmtPercent(s.conversion_with_suspended)} con suspendidas` })}
    </div>`;

  // "Analíticas del Pipeline" — la segunda familia del landing legacy. Seis
  // métricas derivadas, todas read-only por ahora: sus paneles de detalle son
  // drilldowns propios todavía no homologados.
  const analyticsGrid = a ? `<div class="activity-kpi-grid analytics-grid">
      ${commercialRichKpi("Edad del pipeline", a.pipeline_age.avg_days != null ? `${a.pipeline_age.avg_days}d` : "—",
        { sub: `${commercialFmtInt(a.pipeline_age.count)} oportunidad(es) activa(s)` })}
      ${commercialRichKpi("Tiempo por etapa", a.stage_time.avg_days != null ? `${a.stage_time.avg_days}d` : "—",
        { sub: a.stage_time.stages_with_data ? `${commercialFmtInt(a.stage_time.stages_with_data)} etapa(s) con datos` : "sin datos de historial" })}
      ${commercialRichKpi("Conversión etapa a etapa", a.stage_conversion.min_rate != null ? `${a.stage_conversion.min_rate}%` : "—",
        { sub: a.stage_conversion.min_rate != null ? "tasa mínima (cuello de botella)" : "sin datos" })}
      ${commercialRichKpi("Tiempo de cierre", a.close_time.avg_days != null ? `${a.close_time.avg_days}d` : "—",
        { sub: `${commercialFmtInt(a.close_time.count)} oportunidad(es) ganada(s)` })}
      ${commercialRichKpi("Estancadas", commercialFmtInt(a.stagnant.count),
        { sub: `+${commercialFmtInt(a.stagnant.threshold_days)} días sin movimiento` })}
      ${commercialRichKpi("Conversión de leads", commercialFmtPercent(a.lead_conversion.rate_any_conv),
        { sub: `${commercialFmtPercent(a.lead_conversion.rate_adv_opp)} avanzó a oportunidad` })}
    </div>` : `<p class="empty-panel">No pudimos calcular las analíticas del pipeline en este momento.</p>`;

  // El análisis contextual NO ocupa lugar hasta que se acciona su KPI: el
  // atributo `data-breakdown` va sobre la SECCIÓN entera, así que
  // `initEventBreakdown` la muestra y la esconde completa con el mismo motor
  // de siempre. Sin panel placeholder, sin espacio reservado, sin un segundo
  // mecanismo de breakdown.
  const analysisSection = `<details class="dashboard-section" data-section-id="activas-analisis" data-breakdown="activas" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Oportunidades Activas — Análisis</span>
        <span class="dashboard-section__count">${commercialFmtInt(p.opportunities)} oportunidad(es)</span>
      </summary>
      <div class="dashboard-section__body">${commercialActivasAnalysisHtml(p)}</div>
    </details>`;

  // "Resumen" va SIN contador en el header: la sección agrupa nueve métricas
  // distintas, así que fijar ahí el conteo de una sola (Activas) le daba un
  // protagonismo que no le corresponde y sugería que el bloque entero hablaba
  // de eso. Cada tile ya declara su propia magnitud.
  const sections = commercialDashboardSection("resumen", "Resumen", "", kpiGrid)
    + commercialDashboardSection("analiticas", "Analíticas del Pipeline", "", analyticsGrid)
    + analysisSection;
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
    <div id="commercial-capability-panel">${commercialCapabilityHtml()}</div>
  `;
  // El filtro global se dibuja FUERA de `#commercial-main-content`, en el
  // hero: es transversal a las capacidades, así que no puede vivir dentro del
  // contenedor que se reemplaza al conmutar.
  const heroFilter = document.getElementById("commercial-hero-filter");
  if (heroFilter) heroFilter.innerHTML = commercialFilterHtml();
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
    const [summaryResp, pipelineResp, analyticsResp] = await Promise.all([
      fetch(`/comercial/data/summary?${qs}`).then(r => r.json()),
      fetch(`/comercial/data/pipeline?${qs}`).then(r => r.json()),
      fetch(`/comercial/data/analytics?${qs}`).then(r => r.json()),
    ]);
    if (summaryResp.state === "ok" && pipelineResp.state === "ok") {
      commercialState.summary = summaryResp.summary;
      commercialState.pipeline = pipelineResp.pipeline;
      // Las analíticas degradan SOLAS: dependen del historial de Odoo, que es
      // la consulta más pesada del dashboard. Si fallan, el resto del landing
      // se muestra igual y sólo ese bloque avisa — nunca se cae la página
      // entera por una métrica derivada.
      commercialState.analytics = analyticsResp.state === "ok" ? analyticsResp.analytics : null;
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
  commercialState.analytics = null;
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
  // La delegación cuelga de `document`, no de `root`: desde este batch el
  // filtro global vive en el hero, fuera de `#commercial-main-content`.
  document.addEventListener("click", event => {
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
  document.addEventListener("change", event => {
    const select = event.target.closest("[data-commercial-act-user]");
    if (!select) return;
    commercialState.actUser = select.value;
    commercialRenderCapability();
  });
  commercialLoadData();
});
