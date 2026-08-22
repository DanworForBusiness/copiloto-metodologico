// commercial.js — Comercial / CRM (workspace propio del Hub, 100% lectura).
// MISMO patrón de hidratación que reporting.js (fetch + render manual sobre
// innerHTML, sin Alpine reactivo) — bootstrap en DOMContentLoaded, al final
// del archivo. Consume sus 5 endpoints propios (`/comercial/data/summary|
// pipeline|analytics|opportunities|activities`, `tipo=todos|nn|bi`).
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
  // Dimensión de horas del "Funnel por Etapa" (la segunda serie de barras).
  // NO es un filtro: no cambia universo, etapas, capability ni la tabla —
  // sólo qué magnitud dibuja esa serie y su eje. Por eso vive acá y NO
  // dispara fetch.
  horasDim: "hours", // hours | weighted
  status: "idle", // idle | loading | ready | error
  errorState: "", // "config_incomplete" | "source_error" | ""
  summary: null,
  pipeline: null,
  analytics: null,
  // Detalle por oportunidad (Active Opportunities Phase 2 / C4, ver
  // HANDOFF) — MISMO universo/gate que `pipeline`, pero degrada solo si
  // falla (MISMO criterio que `analytics`: es detalle adicional, nunca
  // bloquea el resto del landing).
  opportunities: null,
  // Orden de la tabla de detalle — client-side puro, nunca un fetch (MISMO
  // criterio que `horasDim`). `null` = sin ordenar (orden de llegada del
  // backend). Sobrevive a un cambio de Todos/NN/BI (MISMO criterio que
  // `horasDim`, que tampoco se resetea en `commercialSetTipo`).
  oppSortKey: null,
  oppSortDir: 1, // 1 = ascendente, -1 = descendente
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

// ── Funnel por Etapa ───────────────────────────────────────────────────────
// Restauración de la representación legacy (Active Opportunities Phase 2 /
// C3, ver HANDOFF) sobre el primitivo COMPARTIDO `.category-plot`
// (tools.css): GROUPED BARS — dos barras por etapa, Oportunidades y Horas/
// Ponderadas, sobre las MISMAS posiciones de etapa. Universo: Activas,
// etapas 1-8 (ver `_build_pipeline`/C1 — nunca el 0-8 de Pipeline Total/
// Forecast). Reemplaza la versión anterior (barra + línea con puntos, "A'"):
// el owner pidió el grouped bar real del legacy, no una variante Hub nueva.
// Ambas series son DOM puro — sin librería de charts ni elemento gráfico
// nativo del navegador.
//
// DOBLE ESCALA, A PROPÓSITO: cantidad y horas no comparten unidad ni orden
// de magnitud. Lo que se compara es la DISTRIBUCIÓN de cada serie a lo
// largo del embudo — nunca la altura de una barra de Oportunidades contra
// la de Horas, que sería un artefacto de las escalas elegidas. De ahí las
// mitigaciones obligatorias: ticks en AMBOS ejes, los dos arrancando en 0,
// techo redondeado (`commercialNiceMax`), unidad explícita en la leyenda y
// dos colores/swatches inequívocos (azul = Oportunidades/escala izq., oro =
// Horas/escala der.). Los valores REALES viven en el tooltip (por columna,
// las 4 métricas juntas) y en la tabla de detalle (C4); el gráfico nunca
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

  // La COLUMNA es la superficie accesible (no la barra individual): cubre
  // toda la banda de la etapa, lleva el `aria-label` plano con las CUATRO
  // magnitudes reales y ancla el tooltip compartido — un solo punto de
  // foco/hover por etapa, nunca uno por serie (evitaría duplicar el mismo
  // dato dos veces en el lector de pantalla). Las dos barras son
  // `aria-hidden` implícito (no son focusables ni tienen texto propio).
  const cols = stages.map((stage, index) => {
    const lines = commercialStageTooltipLines(stage);
    const countHeight = maxCount > 0 ? ((stage.count || 0) / maxCount * 100).toFixed(1) : "0.0";
    const hoursHeight = maxHours > 0 ? (hoursOf(stage) / maxHours * 100).toFixed(1) : "0.0";
    return `<div class="category-plot__col chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(lines.join(" · "))}">`
      + `<div class="category-plot__bar category-plot__bar--count" style="height:${countHeight}%"></div>`
      + `<div class="category-plot__bar category-plot__bar--hours" style="height:${hoursHeight}%"></div>`
      + `${commercialChartTooltip(lines)}</div>`;
  }).join("");

  const grid = COMMERCIAL_PLOT_TICKS.map(t => `<i style="bottom:${(t * 100).toFixed(0)}%"></i>`).join("");
  const xlab = stages.map((stage, index) =>
    `<span>${commercialEsc(commercialStageTick(stage, index))}</span>`).join("");

  return `
    <div class="category-plot-header">
      <p class="perspective-label">Funnel por Etapa</p>
      <div class="dashboard-view-switch dashboard-view-switch--compact" role="group" aria-label="Dimensión de horas">${switchButtons}</div>
    </div>
    <div class="category-plot-legend">
      <b><i class="swatch-bar swatch-bar--count"></i>Oportunidades <em>· escala izq.</em></b>
      <b><i class="swatch-bar swatch-bar--hours"></i>${commercialEsc(hoursLabel)} <em>· escala der.</em></b>
    </div>
    <div class="category-plot">
      ${commercialAxisHtml("left", maxCount)}
      <div class="category-plot__area">
        <div class="category-plot__grid">${grid}</div>
        ${cols}
      </div>
      ${commercialAxisHtml("right", maxHours)}
      <div class="category-plot__xlab">${xlab}</div>
    </div>`;
}

// Detalle de Oportunidades Activas (Active Opportunities Phase 2 / C4, ver
// HANDOFF): reemplaza el placeholder "Detalle por Etapa" (agrupado, Batch
// 2A) por el detalle REAL por OPORTUNIDAD del legacy (`/relevamiento`) —
// mismas columnas, MENOS "Etiquetas" (contrato ya decidido en el backend,
// ver `_build_opportunities`/HANDOFF: "No Etiquetas in output UI contract").
// "Lic." y "Días resp." son intencionalmente NO ordenables en el legacy
// (`renderTable` — esas dos únicas columnas nunca reciben `makeSortTh`); se
// preserva esa misma decisión acá en vez de volver todo ordenable por
// default. Reusa el MISMO Table Spine ya establecido (`.row-label-col` +
// ancho default 88px de columnas métricas, `.matrix-scroll`) — cero CSS
// propio de Comercial para esto.
const COMMERCIAL_PRIORITY_LABELS = { "0": "—", "1": "⭐ Media", "2": "⭐⭐ Alta", "3": "⭐⭐⭐ Muy Alta" };

// Claves de orden — MISMA semántica que `SORT_KEYS` del legacy
// (`renderTable`), adaptada a los nombres de campo del contrato Hub
// (`_build_opportunities`). Ausente/false ordena al final en ascendente
// (`-Infinity`/`Infinity` según corresponda), nunca antes que un valor real.
const COMMERCIAL_OPP_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  hours: o => (o.hours != null && o.hours !== false) ? Number(o.hours) : -Infinity,
  weighted_hours: o => (o.weighted_hours != null && o.weighted_hours !== false) ? Number(o.weighted_hours) : -Infinity,
  probability: o => o.probability ?? -1,
  priority: o => Number(o.priority ?? 0),
  next_action: o => {
    const days = (o.activities || []).map(act => commercialDaysUntil(act.deadline)).filter(d => d !== null);
    return days.length ? Math.min(...days) : Infinity;
  },
  last_response: o => o.days_elapsed ?? Infinity,
};

// Orden client-side puro sobre el dataset YA cargado — nunca un round-trip
// (MISMO criterio que el switch Horas/Ponderadas). Copia el array antes de
// ordenar: el payload de `commercialState.opportunities` nunca se muta.
function commercialOpportunitiesSorted(rows) {
  const keyFn = commercialState.oppSortKey && COMMERCIAL_OPP_SORT_KEYS[commercialState.oppSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.oppSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

// Toggle de orden — MISMO contrato que `onColSort` del legacy: mismo header
// invierte dirección, header distinto vuelve a ascendente (dir=1).
function commercialToggleOppSort(key) {
  if (commercialState.oppSortKey === key) {
    commercialState.oppSortDir *= -1;
  } else {
    commercialState.oppSortKey = key;
    commercialState.oppSortDir = 1;
  }
}

function commercialOpportunitiesTableHtml(opportunities) {
  const rows = (opportunities && opportunities.opportunities) || [];
  if (!rows.length) {
    return `<p class="empty-panel">Sin oportunidades activas para este filtro.</p>`;
  }
  const sorted = commercialOpportunitiesSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.oppSortKey === key;
    const arrow = active ? (commercialState.oppSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => {
    const acts = o.activities || [];
    // MISMO sufijo relativo que `commercialActivityTableHtml` (venc./hoy/Nd)
    // — nunca un segundo formato de "días hasta" en el mismo archivo.
    const nextAction = acts.length
      ? acts.map(act => {
          const days = commercialDaysUntil(act.deadline);
          const rel = days === null ? "" : days < 0 ? ` (venc. ${Math.abs(days)}d)` : days === 0 ? " (hoy)" : ` (${days}d)`;
          const label = act.type ? `${commercialFmtDate(act.deadline)} · ${act.type}` : commercialFmtDate(act.deadline);
          return `${commercialEsc(label)}${commercialEsc(rel)}`;
        }).join("<br>")
      : "—";
    const lic = o.licencias === "Si" ? "Sí" : o.licencias === "No" ? "No" : "—";
    return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td>${lic}</td>
      <td>${commercialFmtInt(o.hours)}</td>
      <td>${commercialFmtInt(o.weighted_hours)}</td>
      <td>${commercialFmtPercent(o.probability)}</td>
      <td>${commercialEsc(COMMERCIAL_PRIORITY_LABELS[o.priority] || "—")}</td>
      <td class="left">${nextAction}</td>
      <td>${o.last_response ? commercialEsc(commercialFmtDate(o.last_response)) : "—"}</td>
      <td>${o.days_elapsed != null ? `${commercialFmtInt(o.days_elapsed)}d` : "—"}</td>
    </tr>`;
  }).join("");
  // Fila de totales — SOLO Hs/Hs Pond. tienen semántica aditiva sin
  // ambigüedad (MISMO criterio que Reporting/el placeholder de Batch 2A ya
  // retirado: rollup client-side, nunca un campo de total del backend).
  // Prob./Prior./Días resp. son columnas por-oportunidad sin una agregación
  // obvia (promedio? mínimo?) — inventar esa semántica no es parte de este
  // corrective, así que quedan en blanco en vez de un número fabricado.
  const totals = rows.reduce((acc, o) => ({
    hours: acc.hours + (o.hours || 0),
    weighted_hours: acc.weighted_hours + (o.weighted_hours || 0),
  }), { hours: 0, weighted_hours: 0 });
  const totalRow = `
    <tr class="is-total">
      <td class="row-label" colspan="4">Total (${rows.length})</td>
      <td>${commercialFmtInt(totals.hours)}</td>
      <td>${commercialFmtInt(totals.weighted_hours)}</td>
      <td colspan="5"></td>
    </tr>`;
  // `<colgroup>` propio (visual polish, ver HANDOFF): con 11 columnas el
  // default compartido (`.row-label-col` 340px + 88px por columna sin
  // clase) sobre-reserva Oportunidad y deja a las `.left` flexibles (Cliente/
  // Vendedor/Próx. Acción) peleando por las migas — Próx. Acción, la más
  // pesada de contenido, terminaba más comprimida que columnas puramente
  // numéricas. MISMO recurso ya usado por `commercialActivityTableHtml`
  // (Actividades, más arriba en este archivo) para una tabla de varias
  // columnas de texto: el `<col>` explícito gana sobre la regla compartida
  // en `table-layout:fixed` (ver comentario de esa función) — no se toca
  // ningún primitivo de tools.css, así que ninguna otra tabla del Hub se
  // ve afectada. Compactas de verdad (Lic./Hs/Hs Pond./Prob.) quedan
  // angostas; Prior./Próx. Acción/Últ. Resp./Días resp. reciben el espacio
  // que su contenido (estrellas, fecha+tipo+badge, fecha) necesita.
  return `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:170px"><col style="width:120px"><col style="width:110px">
          <col style="width:50px"><col style="width:60px"><col style="width:70px">
          <col style="width:60px"><col style="width:90px"><col style="width:200px">
          <col style="width:90px"><col style="width:65px">
        </colgroup>
        <thead><tr>
          ${th("opportunity", "Oportunidad", "row-label-col")}
          ${th("client", "Cliente", "left")}
          ${th("seller", "Vendedor", "left")}
          <th>Lic.</th>
          ${th("hours", "Hs")}
          ${th("weighted_hours", "Hs Pond.")}
          ${th("probability", "Prob.")}
          ${th("priority", "Prior.")}
          ${th("next_action", "Próx. Acción", "left")}
          ${th("last_response", "Últ. Resp.")}
          <th>Días resp.</th>
        </tr></thead>
        <tbody>${rowsHtml}${totalRow}</tbody>
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

// KPI → Contextual Drilldown, navegación al abrir (Active Opportunities
// Phase 2 / C3.1, ver HANDOFF): sólo cuando el trigger QUEDÓ activo (el
// motor compartido `initEventBreakdown` ya corrió su show()/toggle antes de
// que esto se llame — ver los dos delegados que lo invocan) se hace scroll
// a la sección — nunca al cerrar, cambiar Horas/Ponderadas o filtrar.
// `scrollIntoView` SIN `behavior` explícito hereda `scroll-behavior` de
// `<html>` (app.css): "smooth" normalmente, "auto" bajo
// `prefers-reduced-motion:reduce` — MISMO mecanismo que ya cubre el resto
// del Hub (`html{scroll-behavior:smooth}` + su media query), cero chequeo
// de motion propio. El "heading debajo del topbar" lo resuelve
// `scroll-margin-top` en `.dashboard-section` (commercial.css) — nunca un
// `scrollTo` con offset numérico. La búsqueda cuelga de `root` (nunca de
// `document`), MISMO contrato que el resto de las búsquedas puntuales de
// este archivo (ver Loader canónico — cero exploración arbitraria del DOM).
function commercialMaybeScrollToOpenedAnalysis(trigger, root) {
  if (!trigger.classList.contains("is-active")) return;
  root.querySelector(`[data-breakdown="${trigger.dataset.event}"]`)?.scrollIntoView({ block: "start" });
}

// Cuerpo del panel de "Oportunidades Activas — Análisis": gráfico ARRIBA
// (patrón), tabla ABAJO (precisión). Ambos en la MISMA `.dashboard-section`
// — no una sección nueva, que rompería la relación contextual con el KPI que
// abre este panel. La tabla SÍ se re-renderiza al ordenar una columna (ver
// `[data-commercial-opportunities]` y el delegado de click), pero NUNCA con
// el switch Horas/Ponderadas: ese sigue tocando sólo `[data-commercial-dist]`
// (el gráfico), la tabla es la referencia numérica completa en cualquier
// modo del gráfico y no depende de esa dimensión.
// Sin etapas, `commercialDistribucionHtml` devuelve "" y el único mensaje lo
// da el `.empty-panel` de la tabla — nunca rótulos huérfanos sobre la nada.
function commercialActivasAnalysisHtml(pipeline) {
  const dist = commercialDistribucionHtml(pipeline, commercialState.horasDim);
  // El rótulo "Funnel por Etapa" lo emite el propio gráfico (vive dentro
  // del subárbol que re-renderiza el switch), así que acá NO se repite —
  // sólo el de la tabla. C4 (ver HANDOFF) reemplazó el placeholder "Detalle
  // por Etapa" (agrupado, Batch 2A) por el detalle real por OPORTUNIDAD
  // (`commercialOpportunitiesTableHtml`, universo `_build_opportunities`) —
  // no se toca en C3, que fue exclusivamente el chart.
  return `${dist ? `<div data-commercial-dist>${dist}</div>
      <p class="perspective-label">Detalle de Oportunidades</p>` : ""}
    <div data-commercial-opportunities>${commercialOpportunitiesTableHtml(commercialState.opportunities)}</div>`;
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
    const [summaryResp, pipelineResp, analyticsResp, opportunitiesResp] = await Promise.all([
      fetch(`/comercial/data/summary?${qs}`).then(r => r.json()),
      fetch(`/comercial/data/pipeline?${qs}`).then(r => r.json()),
      fetch(`/comercial/data/analytics?${qs}`).then(r => r.json()),
      fetch(`/comercial/data/opportunities?${qs}`).then(r => r.json()),
    ]);
    if (summaryResp.state === "ok" && pipelineResp.state === "ok") {
      commercialState.summary = summaryResp.summary;
      commercialState.pipeline = pipelineResp.pipeline;
      // Las analíticas y el detalle por oportunidad degradan SOLOS: analytics
      // depende del historial de Odoo (la consulta más pesada) y
      // opportunities es detalle adicional (C4) sobre el MISMO universo que
      // `pipeline` — si cualquiera de los dos falla, el resto del landing se
      // muestra igual y sólo ese bloque avisa, nunca se cae la página entera
      // por una vista derivada/de detalle.
      commercialState.analytics = analyticsResp.state === "ok" ? analyticsResp.analytics : null;
      commercialState.opportunities = opportunitiesResp.state === "ok" ? opportunitiesResp.opportunities : null;
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
  commercialState.opportunities = null;
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
    // Ordenar la tabla de detalle (Active Opportunities Phase 2 / C4, ver
    // HANDOFF): MISMO patrón que el switch de arriba — re-render QUIRÚRGICO
    // de `[data-commercial-opportunities]`, sin fetch (el orden es client-
    // side puro sobre `commercialState.opportunities`, ya cargado) y sin
    // tocar el gráfico ni volver a cablear el motor de desglose compartido.
    const sortTh = event.target.closest("[data-commercial-sort]");
    if (sortTh) {
      commercialToggleOppSort(sortTh.dataset.commercialSort);
      const host = root.querySelector("[data-commercial-opportunities]");
      if (host) host.innerHTML = commercialOpportunitiesTableHtml(commercialState.opportunities);
      return;
    }
    // KPI → Contextual Drilldown (ver HANDOFF — C3.1, navegación al abrir):
    // el trigger `[data-event]` ya fue procesado por el motor compartido de
    // desglose (app.js — su listener cuelga directo del trigger y corre
    // ANTES de que el click burbujee hasta `document` — para cuando este
    // delegado se ejecuta, `.is-active` ya refleja el estado POST-click).
    // Si quedó activo, este click fue una APERTURA — nunca al cerrar, al
    // cambiar Horas/Ponderadas ni al filtrar. `commercialMaybeScrollToOpenedAnalysis`
    // también la usa el delegado de teclado más abajo (mismo criterio, sin
    // duplicar lógica).
    const kpiTrigger = event.target.closest("[data-event]");
    if (kpiTrigger) {
      commercialMaybeScrollToOpenedAnalysis(kpiTrigger, root);
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
  // Activación por teclado (Enter/Espacio) de un trigger `[data-event]`: el
  // motor compartido cablea su PROPIO `keydown` directo sobre el trigger
  // (nunca dispara un evento `click` sintético), así que el delegado de
  // click de arriba no lo ve — este delegado espeja el mismo criterio para
  // ese segundo camino de activación, sin duplicar el toggle/show en sí.
  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const kpiTrigger = event.target.closest("[data-event]");
    if (!kpiTrigger) return;
    commercialMaybeScrollToOpenedAnalysis(kpiTrigger, root);
  });
  commercialLoadData();
});
