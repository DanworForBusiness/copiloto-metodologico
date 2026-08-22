// commercial.js — Comercial / CRM (workspace propio del Hub, 100% lectura).
// MISMO patrón de hidratación que reporting.js (fetch + render manual sobre
// innerHTML, sin Alpine reactivo) — bootstrap en DOMContentLoaded, al final
// del archivo. Consume sus 6 endpoints propios (`/comercial/data/summary|
// pipeline|analytics|opportunities|activities|forecast`, `tipo=todos|nn|bi`).
// Sólo CUATRO se piden en el load inicial: `activities` (capability
// Actividades) y `forecast` (análisis Forecast) son LAZY — se piden la
// primera vez que se los abre, nunca en cada carga del landing (ver
// `commercialLoadActivities`/`commercialLoadForecast`).
//
// Reusa primitives visuales COMPARTIDOS del Hub (.activity-kpi-grid/
// .activity-kpi, .experience-tabs, .dashboard-view-switch, .dashboard-section,
// table.dashboard-table, .matrix-scroll, .chart-tooltip, .dashboard-empty,
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
// KPI → Contextual Drilldown: hoy hay DOS análisis accionables —
// "Oportunidades activas" (1 tile) y "Forecast" (C6: DOS tiles, "Pipeline
// total" y "Pipeline ponderado", que son la misma población medida con y sin
// probabilidad y por eso abren el MISMO panel). El resto de los tiles
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
  // Orden de la tabla de detalle — client-side puro, nunca un fetch. `null`
  // = sin ordenar (orden de llegada del backend). Sobrevive a un cambio de
  // Todos/NN/BI — no se resetea en `commercialSetTipo`.
  oppSortKey: null,
  oppSortDir: 1, // 1 = ascendente, -1 = descendente
  // Forecast por mes de cierre (C6, ver HANDOFF) — LAZY, igual que
  // Actividades: NO entra al `Promise.all` inicial, porque es un análisis
  // opcional y no debe agregar una request remota a CADA carga del landing.
  // Se pide la primera vez que se abre el desglose, y se reusa mientras el
  // `tipo` no cambie (ver `commercialLoadForecast`). `idle` = todavía no se
  // pidió, o quedó invalidado por un cambio de Todos/NN/BI. NO es un cache
  // por tipo: es el payload del tipo VIGENTE y nada más.
  forecast: null,
  forecastStatus: "idle", // idle | loading | ready | error
  forecastErrorState: "",
  // Filtros del panel de Forecast (C6b, ver HANDOFF — paridad con el
  // `#pipeline-drill-section` legacy): Etapa y Vendedor, client-side puros
  // sobre `months[].opportunities[]`, que el payload YA trae — cero
  // round-trip. Universo 0-8, así que sus opciones NO son las de Activas
  // (1-8): se derivan del propio payload de forecast, nunca de
  // `pipeline.stages`. Son estado de FILTRO, no de "qué tile abrió el
  // panel" (eso sigue viviendo sólo en el DOM, ver
  // `commercialNormalizeSharedTriggerActiveState`): tienen que sobrevivir al
  // re-render quirúrgico del panel, que es exactamente lo que un filtro
  // necesita. "" = sin filtro.
  forecastStageFilter: "",
  forecastSellerFilter: "",
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
  // Filtros de ANÁLISIS del detalle de Oportunidades (C5e, ver HANDOFF):
  // Etapa/Vendedor afectan Funnel + tabla (ver
  // `commercialAnalysisFilteredOpportunities`); el texto libre es SÓLO de
  // tabla (ver `commercialSearchOpportunities`) — nunca toca el Funnel. Los
  // tres son client-side puros sobre `opportunities`, ya cargado — cero
  // round-trip. "" = sin filtro (Todas/Todos/sin buscar). A diferencia de
  // `oppSortKey` (sobrevive el cambio de tipo), estos SÍ se resetean — ver
  // `commercialResetAnalysisFiltersForNewTipo`: mismo criterio que
  // `actPeriod`/`actUser` (arriba), un filtro por valor podría apuntar a un
  // universo que ya no existe tras cambiar Todos/NN/BI.
  oppStageFilter: "",
  oppSellerFilter: "",
  oppSearchText: "",
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
// `eventView` (C6b, ver HANDOFF): cuando VARIOS tiles comparten un mismo
// `data-event` —o sea abren el mismo análisis— este atributo declara QUÉ
// lectura representa cada uno ("total" | "ponderado"). Es una propiedad
// ESTÁTICA del tile (lo que el tile significa), no estado de runtime: el
// hecho dinámico —cuál está activo— lo sigue diciendo `.is-active` en el DOM.
function commercialRichKpi(label, value, { sub = "", split = null, eventKey = "", eventView = "" } = {}) {
  const clickable = eventKey ? ` activity-kpi--clickable" data-event="${eventKey}"`
    + (eventView ? ` data-commercial-event-view="${eventView}"` : "")
    + ` role="button" tabindex="0"`
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
// Línea secundaria con el MISMO formato que la composición por segmento
// (`.kpi-split`/`.kpi-split__item`, ver `commercialSplitHtml` abajo) — owner
// smoke: los dos últimos tiles del Resumen la traían como `.kpi-sub` (mono
// 600, .72rem) y rompían la homogeneidad de la grilla contra los seis que
// muestran "New Name / Base Instalada". Ahora comparten tipografía y ritmo.
// NO lleva tooltip ni `chart-tooltip-anchor`: no hay nada que aclarar, a
// diferencia de NN/BI, donde el tooltip explica que los segmentos se
// superponen y no suman el total. Por eso tampoco es foco-able — un foco sin
// contenido detrás sería una promesa vacía.
function commercialKpiPairsHtml(pairs) {
  const items = (pairs || []).filter(pair => pair && pair[1] !== "" && pair[1] != null);
  if (!items.length) return "";
  return `<div class="kpi-split kpi-split--pairs">`
    + items.map(([label, value]) =>
      `<span class="kpi-split__item">${commercialEsc(label)} <b>${commercialEsc(value)}</b></span>`).join("")
    + `</div>`;
}

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

// Estado vacío/no-disponible Hub-consistente (visual polish, ver HANDOFF —
// referencia: "Sin selección" en Copiloto/Métricas de Uso). Reusa
// `.dashboard-empty`/`.dashboard-empty__label` (tools.css) — primitivo YA
// compartido (Staffing, Activity), nunca uno inventado para Comercial: caja
// con borde punteado, eyebrow dorado + texto centrado, en vez de un `<p
// class="empty-panel">` suelto flotando bajo tabs/tabla. El eyebrow separa
// la SEMÁNTICA sin cambiar la gramática visual: "No disponible" para fallas
// de carga/cálculo (config incompleta, error de transporte — un estado que
// se PARECE a un error pero nunca en rojo, el Hub no usa ese tratamiento
// acá) vs "Sin resultados" para cero filas legítimas — nunca el mismo texto
// para los dos casos.
// `action` (owner request — "Reintentar"): HTML ya armado que va DENTRO del
// `<p>`, a continuación del mensaje. MISMA forma que el único precedente del
// Hub para una acción dentro de un estado vacío (`staffing_dashboard.html`:
// `.dashboard-empty` > `<p>` > `.dashboard-subhead__reset`) — cero CSS propio
// y cero clase nueva. Vacío por default: la enorme mayoría de los estados
// vacíos no tienen acción, y no se les inventa una.
function commercialEmptyStateHtml(eyebrow, message, action = "") {
  return `<div class="dashboard-empty"><span class="dashboard-empty__label">${commercialEsc(eyebrow)}</span>`
    + `<p>${commercialEsc(message)}${action}</p></div>`;
}

// Botón de reintento. SÓLO tiene sentido para `source_error` —una falla de
// transporte contra Odoo, que la próxima request puede resolver—, nunca para
// `config_incomplete`: ahí falta configuración del espacio y reintentar sería
// mentirle al usuario (mismo resultado, N veces). De ahí que el llamador
// decida, en vez de que este helper lo ponga siempre.
//
// `key` DISCRIMINA qué se reintenta (corrective, ver HANDOFF): el mismo
// primitivo visual para los dos estados de no-disponible, pero cada uno
// reintenta LO SUYO. Sin esta key los dos botones caían en la misma rama del
// delegado y el del Forecast terminaba recargando el Dashboard entero —
// pidiendo cuatro endpoints que no habían fallado, sin pedir el forecast, y
// cerrando el propio panel de paso. Compartir la caja no es compartir el
// callback. Mismo patrón que `data-commercial-close-analysis`, que ya lleva
// la key de su análisis.
function commercialRetryButtonHtml(key, label) {
  return ` <button type="button" class="dashboard-subhead__reset" data-commercial-retry="${key}"`
    + ` aria-label="${commercialEsc(label)}">Reintentar</button>`;
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
// TRES barras por etapa simultáneas (Active Opportunities Phase 2 / C5a, ver
// HANDOFF): Oportunidades, Horas y Hs Ponderadas, sobre las MISMAS
// posiciones de etapa. Reemplaza el switch Horas|Ponderadas (C3/C3.1) — el
// payload de `_build_pipeline` YA traía las tres magnitudes por etapa desde
// C1/C3, así que mostrarlas juntas fue puramente frontend: nunca se tocó el
// backend ni el contrato del endpoint. Universo: Activas, etapas 1-8 (ver
// `_build_pipeline`/C1 — nunca el 0-8 de Pipeline Total/Forecast). Las tres
// series son DOM puro — sin librería de charts ni elemento gráfico nativo
// del navegador.
//
// DOS EJES, NO TRES: Oportunidades tiene su propia escala (izq.); Horas y Hs
// Ponderadas COMPARTEN la escala derecha (misma unidad — horas — nunca dos
// unidades distintas en el mismo eje). `maxHours` sale del máximo entre
// AMBAS series para que ninguna de las dos toque el techo. Cantidad y horas
// no comparten unidad ni orden de magnitud entre sí — se compara la
// DISTRIBUCIÓN de cada serie a lo largo del embudo, nunca la altura de una
// barra de Oportunidades contra una de Horas, que sería un artefacto de las
// escalas elegidas. De ahí las mitigaciones obligatorias: ticks en AMBOS
// ejes, los dos arrancando en 0, techo redondeado (`commercialNiceMax`),
// unidad explícita en la leyenda y tres colores/swatches inequívocos
// (neutral = Oportunidades/escala izq., oro = Horas/escala der., un segundo
// tono dorado ya usado en este archivo para el eje derecho = Ponderadas/
// escala der.). Los valores REALES viven en el tooltip (por columna, las 4
// métricas juntas) y en la tabla de detalle (C4); el gráfico nunca
// normaliza a porcentaje.
function commercialChartTooltip(lines) {
  // Copia local mínima del helper homónimo de reporting.js — MISMA convención
  // ya establecida para `dashboardSection` (cada vertical define su copia
  // chica contra las clases compartidas; no existe renderer global). Con un
  // tercer consumidor real corresponde extraerlo, no antes.
  const arr = Array.isArray(lines) ? lines : [lines];
  return `<div class="chart-tooltip" aria-hidden="true">${arr.map(l => `<p>${commercialEsc(l)}</p>`).join("")}</div>`;
}

// Posicionamiento dinámico del tooltip (corrective, ver HANDOFF — owner
// smoke original: el tooltip del Funnel se recortaba cerca del borde
// superior del panel; segunda vuelta: el MISMO recorte apareció en el
// tooltip de Prior., dentro de la tabla). CAUSA REAL auditada, dos
// boundaries distintos, mismo mecanismo: `.chart-tooltip` (tools.css) es
// puramente CSS, `position:absolute; bottom:calc(100% + 7px)` — SIEMPRE
// arriba, sin lógica de flip — y el recorte NO es un problema de
// z-index/stacking (`.chart-tooltip` ya tiene z-index:30). Es un boundary
// real: `.dashboard-section` (tools.css) tiene `overflow:hidden`, y
// `.matrix-scroll` (tools.css, envuelve la tabla) tiene `overflow-x:auto`
// — que por espec de CSS computa `overflow-y` a `auto` también cuando el
// otro eje no es `visible`, así que TAMBIÉN recorta. Cualquier
// `position:absolute` que se salga de la caja que corresponda se recorta
// ahí, sin importar el z-index.
//
// Generalizado sobre CUALQUIER `.chart-tooltip-anchor` (el marcador que ya
// comparten el Funnel y la celda de Prior. — antes esto sólo escuchaba
// `.category-plot__col`, así que la tabla nunca se beneficiaba del flip):
// mide el espacio real disponible ARRIBA dentro del boundary que
// corresponda (`.matrix-scroll` si el anchor vive en una tabla scrolleable,
// si no `.dashboard-section`) contra la altura real del tooltip (medible
// con getBoundingClientRect() aunque esté con `visibility:hidden` — sigue
// en el flujo de layout, a diferencia de `display:none`) y alterna una
// clase (`is-tooltip-below`, ver `.chart-tooltip-anchor.is-tooltip-below`
// en commercial.css) que invierte `bottom`→`top` — nunca se toca
// `.chart-tooltip`/`.chart-tooltip-anchor` en sí (tools.css).
// Decisión pura, sin DOM (testable directo con números — ver HANDOFF: "no
// pinnear valores de píxeles arbitrarios"): ¿el espacio libre arriba
// alcanza para el tooltip + el mismo gap que ya usa el CSS?
function commercialTooltipNeedsFlip(spaceAbove, tooltipHeight, gap) {
  return spaceAbove < tooltipHeight + gap;
}

const COMMERCIAL_TOOLTIP_GAP = 7; // mismo separador que ya usa `.chart-tooltip` (tools.css)

// Corrimiento HORIZONTAL del tooltip (C6, ver HANDOFF): decisión pura, sin
// DOM, misma filosofía que `commercialTooltipNeedsFlip` — cuánto hay que mover
// el tooltip para que entre completo dentro de su boundary. Necesario SÓLO
// donde el boundary es estrecho en el eje X: el scroll horizontal del Forecast
// (`.forecast-plot__scroll`), donde un tooltip de 200px sobre una columna de
// ~80px quedaba ilegible en los períodos de los extremos.
//
// Recibe los BORDES REALES del tooltip en reposo, no el centro del ancla y su
// ancho (owner smoke): así es agnóstico de CÓMO lo ancló el CSS. Hace falta
// porque el último período ya no centra su tooltip —lo alinea al borde, para
// no generar scroll fantasma (ver commercial.css)— y una fórmula que asumiera
// centrado lo movería mal justo ahí. Devuelve px (positivo = a la derecha), 0
// cuando ya entra tal cual.
function commercialTooltipShift(left, right, boundaryLeft, boundaryRight, margin) {
  if (left < boundaryLeft + margin) return (boundaryLeft + margin) - left;
  if (right > boundaryRight - margin) return (boundaryRight - margin) - right;
  return 0;
}

function commercialPositionChartTooltip(anchor) {
  const tooltip = anchor.querySelector(".chart-tooltip");
  if (!tooltip) return;
  // Boundary del Forecast: `.forecast-plot__scroll` (overflow-x:auto → por
  // espec `overflow-y` computa `auto` también, o sea recorta en AMBOS ejes).
  // Ahí el tooltip NO puede vivir fuera del área del gráfico — de eso se
  // encarga el CSS (`.forecast-plot .chart-tooltip`, commercial.css: dentro
  // del área, no encima), así que el flip vertical no aplica y lo que hace
  // falta es el clamp horizontal. El camino del Funnel y de la tabla queda
  // EXACTAMENTE como estaba (mismo boundary, mismo flip, cero clamp).
  const scroller = anchor.closest(".forecast-plot__scroll");
  if (scroller) {
    const box = scroller.getBoundingClientRect();
    // Se mide la posición de REPOSO (sin el corrimiento de un hover anterior)
    // y se corrige desde ahí: el CSS es el que decide cómo se ancla cada
    // columna, y esto sólo lo empuja adentro si hace falta.
    tooltip.style.transform = "none";
    const rest = tooltip.getBoundingClientRect();
    const shift = commercialTooltipShift(rest.left, rest.right,
      box.left, box.right, COMMERCIAL_TOOLTIP_GAP);
    if (shift) tooltip.style.transform = `translateX(${Math.round(shift)}px)`;
    return;
  }
  const boundary = anchor.closest(".matrix-scroll, .dashboard-section");
  const boundaryTop = boundary ? boundary.getBoundingClientRect().top : 0;
  const anchorRect = anchor.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();
  const spaceAbove = anchorRect.top - boundaryTop;
  anchor.classList.toggle("is-tooltip-below",
    commercialTooltipNeedsFlip(spaceAbove, tooltipRect.height, COMMERCIAL_TOOLTIP_GAP));
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

// Contenido INTERNO de `[data-commercial-dist]` — el gráfico. Se devuelve
// aparte del wrapper por el MISMO motivo de siempre (ver HANDOFF): un
// `commercialRender()` completo reconstruiría todo `#commercial-main-content`
// y, como `initEventBreakdown` guarda el estado abierto/cerrado únicamente
// en el DOM (`hidden` + `.is-active`), cerraría el panel de Activas. Ya no
// hay switch que re-renderice este subárbol por sí solo (C5a lo retiró) —
// el subárbol se re-renderiza igual cuando cambia `pipeline` (Todos/NN/BI).
function commercialDistribucionHtml(pipeline) {
  const stages = (pipeline && pipeline.stages) || [];
  if (!stages.length) return "";
  // Sólo las etapas PRESENTES en la respuesta y en el orden recibido (el
  // service ya las entrega ordenadas 1→8 y agrupa únicamente las que tienen
  // oportunidades) — nunca 8 columnas hardcodeadas, nunca la etapa 0.
  const maxCount = commercialNiceMax(Math.max(...stages.map(s => s.count || 0)));
  // Horas y Ponderadas comparten la MISMA escala derecha — el techo es el
  // máximo entre las DOS series, nunca solo una (si no, la serie más alta
  // de las dos podría tocar o exceder el techo del eje).
  const maxHours = commercialNiceMax(Math.max(...stages.map(s => Math.max(s.hours || 0, s.weighted_hours || 0))));

  // La COLUMNA es la superficie accesible (no la barra individual): cubre
  // toda la banda de la etapa, lleva el `aria-label` plano con las CUATRO
  // magnitudes reales y ancla el tooltip compartido — un solo punto de
  // foco/hover por etapa, nunca uno por serie (evitaría duplicar el mismo
  // dato dos veces en el lector de pantalla). Las tres barras son
  // `aria-hidden` implícito (no son focusables ni tienen texto propio).
  const cols = stages.map((stage, index) => {
    const lines = commercialStageTooltipLines(stage);
    const countHeight = maxCount > 0 ? ((stage.count || 0) / maxCount * 100).toFixed(1) : "0.0";
    const hoursHeight = maxHours > 0 ? ((stage.hours || 0) / maxHours * 100).toFixed(1) : "0.0";
    const weightedHeight = maxHours > 0 ? ((stage.weighted_hours || 0) / maxHours * 100).toFixed(1) : "0.0";
    return `<div class="category-plot__col chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(lines.join(" · "))}">`
      + `<div class="category-plot__bar category-plot__bar--count" style="height:${countHeight}%"></div>`
      + `<div class="category-plot__bar category-plot__bar--hours" style="height:${hoursHeight}%"></div>`
      + `<div class="category-plot__bar category-plot__bar--weighted" style="height:${weightedHeight}%"></div>`
      + `${commercialChartTooltip(lines)}</div>`;
  }).join("");

  const grid = COMMERCIAL_PLOT_TICKS.map(t => `<i style="bottom:${(t * 100).toFixed(0)}%"></i>`).join("");
  const xlab = stages.map((stage, index) =>
    `<span>${commercialEsc(commercialStageTick(stage, index))}</span>`).join("");

  return `
    <div class="category-plot-header">
      <p class="perspective-label">Funnel por Etapa</p>
    </div>
    <div class="category-plot-legend">
      <b><i class="swatch-bar swatch-bar--count"></i>Oportunidades <em>· escala izq.</em></b>
      <b><i class="swatch-bar swatch-bar--hours"></i>Horas <em>· escala der.</em></b>
      <b><i class="swatch-bar swatch-bar--weighted"></i>Hs Ponderadas <em>· escala der.</em></b>
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

// ── Forecast — Pipeline por Mes de Cierre (C6, ver HANDOFF) ─────────────────
// DOS series simultáneas por período (Horas y Hs Ponderadas) y UN solo eje:
// las dos magnitudes son horas, así que compartir escala es lo único
// correcto — nunca dos ejes para la misma unidad. NO se implementa el switch
// Horas|Ponderadas del legacy: el delta total↔ponderado ES lo que el
// forecast comunica (con switch hay que recordar la vista anterior), y
// además reintroduciría el estado que C5a ya eliminó del Funnel. Sin barra
// de Oportunidades y sin barra de Revenue: las dos viven en el tooltip
// (Revenue es otra unidad; el conteo es el desempate — 400 hs pueden ser 2
// oportunidades o 40).
//
// UNIVERSO: etapas 0-8 (`_build_forecast` — Pipeline Total/Ponderado,
// prospectos incluidos), NUNCA el 1-8 de Oportunidades Activas. Es semántica
// legacy intencional y el panel la declara explícitamente (`.forecast-scope`)
// justo donde puede confundirse.
//
// GEOMETRÍA: variante LOCAL mínima (`.forecast-plot`, commercial.css) sobre
// la MISMA familia `.category-plot` — un eje (sin la columna derecha vacía
// que tendría sentido sólo con dos escalas) y el área + las etiquetas dentro
// de un scroll horizontal compartido. Los buckets NO tienen techo (son los
// `YYYY-MM` presentes en los datos más "Sin fecha"), a diferencia de las <=8
// etapas del Funnel: cada período recibe un ancho mínimo y el gráfico
// scrollea cuando no entran. Nunca se truncan períodos en silencio, nunca se
// esconden etiquetas alternadas dejando barras ambiguas.
const COMMERCIAL_MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun",
  "jul", "ago", "sep", "oct", "nov", "dic"];

// Etiqueta del período. "Sin fecha" (y cualquier clave que no sea `YYYY-MM`)
// se muestra TAL CUAL: es un bucket válido del backend, no un error. Tabla
// fija en vez de `toLocaleDateString`: el resultado no depende del ICU
// disponible en el runtime, así que es idéntico en el navegador y en el
// harness de test.
function commercialForecastPeriodLabel(month) {
  const raw = String(month == null ? "" : month);
  const parts = raw.split("-");
  if (parts.length !== 2) return raw;
  const idx = Number(parts[1]) - 1;
  if (!(idx >= 0 && idx < 12) || !/^\d{4}$/.test(parts[0])) return raw;
  return `${COMMERCIAL_MONTHS_SHORT[idx]} ${parts[0].slice(2)}`;
}

const COMMERCIAL_MONTHS_LONG = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
  "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];

// Etiqueta LARGA — "Agosto de 2026" (owner smoke: "la convención del
// original"). El legacy usa LAS DOS formas, cada una donde corresponde:
// `fmtMonthShort` en el eje del gráfico (dashboard.js:2272) y `fmtMonth` en la
// tabla del drill (dashboard.js:2450). Se preserva ese reparto: el eje X tiene
// ~52px por período y un "Septiembre de 2026" ahí sería ilegible.
// Mismo criterio que la corta: tabla fija en vez de `toLocaleDateString`, para
// que el resultado no dependa del ICU del runtime.
function commercialForecastPeriodLongLabel(month) {
  const raw = String(month == null ? "" : month);
  const parts = raw.split("-");
  if (parts.length !== 2) return raw;
  const idx = Number(parts[1]) - 1;
  if (!(idx >= 0 && idx < 12) || !/^\d{4}$/.test(parts[0])) return raw;
  return `${COMMERCIAL_MONTHS_LONG[idx]} de ${parts[0]}`;
}

// Tooltip por período: SIEMPRE el cuadro completo (período + las 4
// magnitudes). MISMA composición que `commercialStageTooltipLines`. Acá va la
// etiqueta LARGA: el eje X se queda con la corta por espacio, y este es
// justamente el lugar donde se recupera la lectura completa — igual que la
// tabla de al lado.
function commercialForecastTooltipLines(bucket) {
  return [
    commercialForecastPeriodLongLabel(bucket.month),
    `Oportunidades: ${commercialFmtInt(bucket.count)}`,
    `Horas: ${commercialFmtInt(bucket.hours)}`,
    `Ponderadas: ${commercialFmtInt(bucket.weighted_hours)} hs`,
    `Revenue: ${commercialFmtMoney(bucket.revenue)}`,
  ];
}

function commercialForecastChartHtml(months, mode) {
  // ORDEN DEL BACKEND, intacto: `_build_forecast` ya entrega los meses
  // ascendentes con "Sin fecha" SIEMPRE al final. Ordenar acá lo metería
  // entre otros períodos (es alfabéticamente una "S"), así que el cliente
  // NUNCA reordena — sólo dibuja.
  const buckets = Array.isArray(months) ? months : [];
  if (!buckets.length) return "";
  // UNA sola serie, la del tile de entrada (owner smoke): el gráfico y la
  // tabla hablan de la MISMA lectura — Horas con "Pipeline total", Hs
  // Ponderadas con "Pipeline ponderado". Mostrar las dos acá contradecía a la
  // tabla de al lado, que siempre tuvo una sola columna de métrica.
  const metric = COMMERCIAL_FORECAST_METRIC[mode] || COMMERCIAL_FORECAST_METRIC.total;
  // Techo de la serie VISIBLE (no el máximo entre las dos): con una sola
  // serie, escalar contra la otra desperdiciaría alto en el modo ponderado,
  // que siempre es menor o igual.
  const max = commercialNiceMax(Math.max(...buckets.map(b => b[metric.key] || 0)));

  // La COLUMNA es la superficie accesible (no la barra): un solo punto de
  // foco/hover por período, con el `aria-label` plano de las 4 magnitudes —
  // el tooltip sigue mostrando TODAS, aunque se dibuje una: la lectura
  // completa del período nunca se pierde.
  // NO lleva ni handler, ni identificador de mes, ni cursor:pointer — el
  // drill mensual es C7, y una afordancia falsa es peor que ninguna.
  const cols = buckets.map(bucket => {
    const lines = commercialForecastTooltipLines(bucket);
    const height = max > 0 ? ((bucket[metric.key] || 0) / max * 100).toFixed(1) : "0.0";
    return `<div class="category-plot__col chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(lines.join(" · "))}">`
      + `<div class="category-plot__bar category-plot__bar--${metric.series}" style="height:${height}%"></div>`
      + `${commercialChartTooltip(lines)}</div>`;
  }).join("");

  const grid = COMMERCIAL_PLOT_TICKS.map(t => `<i style="bottom:${(t * 100).toFixed(0)}%"></i>`).join("");
  const xlab = buckets.map(bucket =>
    `<span>${commercialEsc(commercialForecastPeriodLabel(bucket.month))}</span>`).join("");

  // El preámbulo (universo + serie) NO va acá: es del PANEL, no del gráfico —
  // ver `commercialForecastPreambleHtml`.
  return `
    <div class="forecast-plot">
      ${commercialAxisHtml("left", max)}
      <div class="forecast-plot__scroll">
        <div class="category-plot__area">
          <div class="category-plot__grid">${grid}</div>
          ${cols}
        </div>
        <div class="category-plot__xlab">${xlab}</div>
      </div>
    </div>`;
}

// ── Forecast: modo, filtros y tabla (C6b, ver HANDOFF) ─────────────────────
// El legacy abría desde "Pipeline Total"/"Pipeline Ponderado" un
// `#pipeline-drill-section` cuya TABLA mostraba una sola columna de métrica
// según el tile de entrada, con el título conmutando entre "Forecast" y
// "Forecast Ponderado". Decisión del owner: se preserva esa paridad.
//
// El modo NO se guarda en ninguna parte: se LEE del DOM, del tile que quedó
// `.is-active` (ver `commercialNormalizeSharedTriggerActiveState`, que
// garantiza que sea exactamente uno). El tile declara qué lectura representa
// con `data-commercial-event-view`, que es una propiedad estática suya. Cero
// `forecastOpenedBy`, cero estado paralelo: si mañana el panel se abre de
// otra forma, el modo sigue saliendo del mismo lugar.
function commercialForecastMode(root) {
  if (!root) return "total"; // render completo: el panel arranca oculto, el modo es irrelevante
  const active = root.querySelector('[data-event="forecast"].is-active');
  return active && active.dataset.commercialEventView === "ponderado" ? "ponderado" : "total";
}

// Todo lo que cambia entre las dos lecturas, en UN solo lugar: el campo del
// payload, el encabezado de la columna, el título del panel, y —desde el owner
// smoke— el rótulo y el color de la única serie que dibuja el gráfico.
// `series` es el sufijo de las clases COMPARTIDAS que ya existen
// (`.category-plot__bar--hours` / `--weighted` y sus swatches): cero color
// nuevo, y la barra queda del mismo tono que la lectura que representa.
const COMMERCIAL_FORECAST_METRIC = {
  total: { key: "hours", column: "Hs Total", legend: "Horas", series: "hours",
    title: "Forecast — Pipeline por Mes de Cierre" },
  ponderado: { key: "weighted_hours", column: "Hs Pond.", legend: "Hs Ponderadas", series: "weighted",
    title: "Forecast Ponderado — Pipeline por Mes de Cierre" },
};

// Opciones de los filtros: se derivan del PROPIO payload de forecast, nunca de
// `pipeline.stages` — el universo acá es 0-8 y el de Activas 1-8, así que las
// etapas no son las mismas (Forecast incluye prospectos). Orden alfabético
// para Vendedor y por nombre de etapa para Etapa: el prefijo numérico del
// nombre ("0 Prospecto", "4 Propuesta") ya las ordena por avance del embudo,
// sin depender del orden en que Odoo devolvió las oportunidades.
function commercialForecastFilterOptions(months) {
  const stages = new Set();
  const sellers = new Set();
  (months || []).forEach(m => (m.opportunities || []).forEach(o => {
    if (o.stage) stages.add(o.stage);
    if (o.seller) sellers.add(o.seller);
  }));
  return {
    stages: [...stages].sort((a, b) => a.localeCompare(b, "es")),
    sellers: [...sellers].sort((a, b) => a.localeCompare(b, "es")),
  };
}

// Re-bucketing por Etapa/Vendedor. SIN filtros devuelve los meses TAL CUAL
// llegaron —o sea los valores que calculó el service, que son la referencia—
// y sólo recalcula cuando hay un filtro activo: MISMO criterio que
// `commercialDeriveFunnelFromOpportunities` en Activas. `.map`/`.filter`
// siempre devuelven estructuras nuevas: cero mutación del payload.
//
// `weighted_hours` se recalcula por oportunidad (`hours * probability / 100`,
// redondeado por opp) igual que el legacy — el service suma y redondea al
// final, así que un filtrado puede diferir en decimales del total sin filtrar.
// Es la aritmética del legacy, preservada a propósito.
function commercialForecastFilteredMonths(months) {
  const source = Array.isArray(months) ? months : [];
  const stageF = commercialState.forecastStageFilter;
  const sellerF = commercialState.forecastSellerFilter;
  if (!stageF && !sellerF) return source;
  return source.map(m => {
    const opps = (m.opportunities || []).filter(o =>
      (!stageF || o.stage === stageF) && (!sellerF || o.seller === sellerF));
    const hours = opps.reduce((sum, o) => sum + (o.hours || 0), 0);
    const weighted = opps.reduce((sum, o) => sum + Math.round((o.hours || 0) * (o.probability || 0) / 100), 0);
    return { ...m, count: opps.length, hours: Math.round(hours * 10) / 10,
      weighted_hours: Math.round(weighted * 10) / 10, opportunities: opps };
  });
}

// Filas visibles de la TABLA: sólo los períodos con valor en la métrica
// ACTIVA, igual que el legacy (`relevant`). Consecuencia aceptada por el
// owner: la tabla puede mostrar menos filas que columnas tenga el gráfico
// —que sigue dibujando las dos series sobre todos los buckets— porque un mes
// puede tener horas y cero ponderadas.
function commercialForecastRelevantMonths(months, mode) {
  const metric = COMMERCIAL_FORECAST_METRIC[mode].key;
  return (months || []).filter(m => (m[metric] || 0) > 0);
}

// Preámbulo del PANEL: el universo (0-8) y qué serie se está leyendo. Vivía
// dentro del gráfico y por eso empujaba el área de plot 44px hacia abajo,
// dejando la tabla de al lado "más arriba" (owner smoke). Pero las dos líneas
// describen el panel ENTERO, no sólo el gráfico: el universo vale igual para
// la tabla, y el rótulo de la serie es el mismo que encabeza su columna de
// métrica. Sacarlo de la columna izquierda es a la vez lo estructuralmente
// correcto y lo que alinea el arranque de las dos mitades.
function commercialForecastPreambleHtml(mode) {
  const metric = COMMERCIAL_FORECAST_METRIC[mode] || COMMERCIAL_FORECAST_METRIC.total;
  return `<p class="forecast-scope">Pipeline total · etapas 0–8</p>
    <div class="category-plot-legend">
      <b><i class="swatch-bar swatch-bar--${metric.series}"></i>${commercialEsc(metric.legend)}</b>
    </div>`;
}

function commercialForecastFiltersHtml(options) {
  const opt = (value, selected) =>
    `<option value="${commercialEsc(value)}"${selected === value ? " selected" : ""}>${commercialEsc(value)}</option>`;
  // MISMOS primitivos que la barra de Activas (`.whatsnew-filters`) y el MISMO
  // "✕ Limpiar" compartido del Hub — un solo lenguaje de filtros en toda la
  // vertical, cero clase nueva.
  // Ninguno de los dos `<select>` es `--fill` (owner smoke): los dos cortan en
  // el ancho que les da `.whatsnew-filter select` (190px), sin que Vendedor se
  // estire hasta el borde. A "Limpiar" lo empuja a la derecha un
  // `margin-left:auto` (commercial.css) — MISMO recurso que ya usa el Limpiar
  // suelto de Staffing, en vez de un campo elástico de relleno.
  return `<div class="whatsnew-filters" data-commercial-forecast-filters>
      <label class="whatsnew-filter">
        <span>Etapa</span>
        <select data-commercial-forecast-stage-filter>
          <option value=""${commercialState.forecastStageFilter === "" ? " selected" : ""}>Todas las etapas</option>
          ${options.stages.map(s => opt(s, commercialState.forecastStageFilter)).join("")}
        </select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-forecast-seller-filter>
          <option value=""${commercialState.forecastSellerFilter === "" ? " selected" : ""}>Todos los vendedores</option>
          ${options.sellers.map(s => opt(s, commercialState.forecastSellerFilter)).join("")}
        </select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-forecast-clear>✕ Limpiar</button>
    </div>`;
}

// Tabla por período. Cuatro columnas, como el legacy: Mes, Opps., la métrica
// ACTIVA (una sola, según el tile de entrada) y su % del total. La barra
// in-row es relativa al MÁXIMO de la métrica —no al total— para que la
// comparación entre meses se lea; el % sí es sobre el total. Reusa los
// primitivos COMPARTIDOS `.bar-track`/`.bar-fill`/`.bar-value` (tools.css) y
// el `.dashboard-table` de siempre.
function commercialForecastTableHtml(months, mode) {
  const metric = COMMERCIAL_FORECAST_METRIC[mode];
  const rows = commercialForecastRelevantMonths(months, mode);
  if (!rows.length) {
    return commercialEmptyStateHtml("Sin resultados",
      "Ningún período tiene horas para esta combinación de filtros.");
  }
  const total = rows.reduce((sum, m) => sum + (m[metric.key] || 0), 0);
  const max = Math.max(...rows.map(m => m[metric.key] || 0), 1);
  const body = rows.map(m => {
    const value = m[metric.key] || 0;
    const pct = total > 0 ? (value / total * 100).toFixed(1) : "0.0";
    const width = (value / max * 100).toFixed(1);
    // "Sin fecha" se explicita como "Sin fecha de cierre" — igual que el
    // legacy: en una tabla de meses, "Sin fecha" solo es ambiguo. El resto va
    // con el mes COMPLETO, la convención que el original usa en esta misma
    // tabla (dashboard.js:2450 — `fmtMonth`, no `fmtMonthShort`).
    const label = m.month === "Sin fecha" ? "Sin fecha de cierre" : commercialForecastPeriodLongLabel(m.month);
    return `<tr>
      <td class="row-label-col">${commercialEsc(label)}</td>
      <td>${commercialFmtInt(m.count)}</td>
      <td><span class="forecast-bar-cell"><span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>`
      + `<span class="bar-value">${commercialFmtInt(value)} hs</span></span></td>
      <td>${pct}%</td>
    </tr>`;
  }).join("");
  // Fila de TOTALES (owner smoke). Reusa `tr.is-total` (tools.css), el MISMO
  // primitivo que ya cierra la tabla de detalle de Oportunidades — cero clase
  // nueva y cero número de estilo propio. `Total (N)` cuenta los períodos
  // VISIBLES, así que siempre coincide con las filas de arriba y con el
  // conteo del header. El % es 100 por construcción (cada fila es su parte de
  // este mismo total) y va explícito en vez de en blanco: cierra la columna.
  // La celda de la métrica va sin barra a propósito: una barra al 100% junto a
  // barras escaladas contra el MÁXIMO se leería como "un mes más" y no como el
  // agregado.
  const totalCount = rows.reduce((sum, m) => sum + (m.count || 0), 0);
  const totalRow = `<tr class="is-total">
      <td class="row-label-col">Total (${commercialFmtInt(rows.length)})</td>
      <td>${commercialFmtInt(totalCount)}</td>
      <td>${commercialFmtInt(total)} hs</td>
      <td>100%</td>
    </tr>`;
  // `<colgroup>` en % y no en px — MISMA convención que la tabla de detalle de
  // Oportunidades (ver su comentario: "más legible que una tabla de px que
  // sólo tiene sentido si suman un total implícito"), y además necesaria acá:
  // `table.dashboard-table` ya es `table-layout:fixed` (tools.css), que
  // reparte el sobrante en PROPORCIÓN a los anchos declarados — con px, Mes
  // terminaba en 116px y recortaba "Septiembre de 2026" (que necesita 118px).
  // En %, Mes se queda con ~38% del panel y entra en una línea siempre.
  return `<div class="matrix-scroll forecast-months">
      <table class="dashboard-table">
        <colgroup><col style="width:38%"><col style="width:15%"><col style="width:32%"><col style="width:15%"></colgroup>
        <thead><tr><th>Mes</th><th>Opps.</th><th>${commercialEsc(metric.column)}</th><th>% del total</th></tr></thead>
        <tbody>${body}${totalRow}</tbody>
      </table>
    </div>`;
}

// Cuerpo del panel de Forecast. Cubre los CUATRO estados del lazy load con
// los MISMOS primitivos que ya usa el resto de Comercial — `idle` incluido:
// el panel puede quedar montado y oculto antes del primer fetch, y en ese
// caso muestra lo mismo que mientras carga (nunca un gráfico vacío ni un
// hueco sin explicar). Tras un error o un payload vacío NO queda ningún
// gráfico viejo: este cuerpo se reemplaza completo.
function commercialForecastPanelBodyHtml(mode) {
  if (commercialState.forecastStatus === "error") {
    const configIncomplete = commercialState.forecastErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar el forecast desde el CRM en este momento.";
    // MISMO criterio que el Dashboard: reintentar sólo si la falla puede
    // resolverse en la próxima request.
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("forecast", "Reintentar la carga del forecast"));
  }
  if (commercialState.forecastStatus !== "ready") {
    return commercialLoadingHtml("Cargando forecast…");
  }
  const months = (commercialState.forecast && commercialState.forecast.months) || [];
  if (!months.length) {
    return commercialEmptyStateHtml("Sin datos",
      "No hay oportunidades con fecha de cierre para este segmento.");
  }
  // Etapa/Vendedor son ANALÍTICOS: el MISMO universo filtrado alimenta el
  // gráfico Y la tabla — igual que Etapa/Vendedor en Activas, e igual que el
  // legacy, que re-agrupaba todo el bloque. Las OPCIONES salen del payload
  // completo (`months`), nunca del filtrado: una opción no puede
  // desaparecer por haberla elegido.
  const filtered = commercialForecastFilteredMonths(months);
  // Gráfico y tabla LADO A LADO (owner smoke): el patrón a la izquierda, la
  // precisión a la derecha — las dos lecturas del mismo período a la misma
  // altura, sin scrollear entre una y otra. En viewport angosto la grilla
  // vuelve a una columna (commercial.css), nunca se comprimen las dos a un
  // ancho inútil.
  return commercialForecastFiltersHtml(commercialForecastFilterOptions(months))
    + commercialForecastPreambleHtml(mode)
    + `<div class="analysis-split">
        <div class="analysis-split__chart">${commercialForecastChartHtml(filtered, mode)}</div>
        <div class="analysis-split__table">${commercialForecastTableHtml(filtered, mode)}</div>
      </div>`;
}

// Conteo del header. "períodos", NUNCA "meses": el dataset puede incluir el
// bucket VÁLIDO "Sin fecha", que no es un mes. Vacío mientras no hay payload
// — el `<span>` se emite igual para que el re-render quirúrgico siempre
// encuentre su host (MISMO criterio que `[data-commercial-funnel-area]`).
// Refleja lo que la tabla muestra REALMENTE (períodos con valor en la métrica
// activa, ya filtrados) más el total de esa métrica — el equivalente del
// "N meses · N hs totales" legacy, pero sin llamarle "mes" a "Sin fecha".
function commercialForecastCountLabel(mode) {
  if (commercialState.forecastStatus !== "ready") return "";
  const months = (commercialState.forecast && commercialState.forecast.months) || [];
  if (!months.length) return "";
  const rows = commercialForecastRelevantMonths(commercialForecastFilteredMonths(months), mode);
  if (!rows.length) return "0 períodos";
  const total = rows.reduce((sum, m) => sum + (m[COMMERCIAL_FORECAST_METRIC[mode].key] || 0), 0);
  return `${commercialFmtInt(rows.length)} período(s) · ${commercialFmtInt(total)} hs`;
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
// Prior. (visual polish, ver HANDOFF): la celda visible queda SOLO con las
// estrellas — el texto ("Media"/"Alta"/"Muy Alta") se mueve al tooltip
// compartido (`.chart-tooltip`, tools.css — MISMA caja que ya usa el
// Funnel de este archivo, nunca un `title=` nativo) para reducir el
// apriete horizontal sin perder el significado semántico.
const COMMERCIAL_PRIORITY_STARS = { "0": "—", "1": "⭐", "2": "⭐⭐", "3": "⭐⭐⭐" };
const COMMERCIAL_PRIORITY_LABELS = { "0": "Sin prioridad", "1": "Media", "2": "Alta", "3": "Muy Alta" };

// Urgencia de "Próx. Acción" (owner corrective, ver HANDOFF — screenshot con
// leyenda de 4 colores "0-7/8-15/16-30/+30 días"): la magnitud (no el signo)
// de `days` es lo que importa — una actividad vencida hace 3 días es tan
// urgente como una que vence en 3 — de ahí `Math.abs`. Reusa el primitivo
// COMPARTIDO `.dashboard-status-badge` (tools.css, mismo que Salud de
// Cliente en el tablero integral) — sólo se agrega la variante `--orange`
// que faltaba (green/amber/red ya existían pero cubren 3 tonos, esta
// leyenda necesita 4). `null` (sin fecha) no es un tono: no hay actividad
// que clasificar, así que no hay badge.
function commercialUrgencyColor(days) {
  if (days == null) return null;
  const abs = Math.abs(days);
  if (abs <= 7) return "green";
  if (abs <= 15) return "amber";
  if (abs <= 30) return "orange";
  return "red";
}
function commercialUrgencyLegendHtml() {
  const tiers = [["green", "0-7 días"], ["amber", "8-15 días"], ["orange", "16-30 días"], ["red", "+30 días"]];
  return `<div class="urgency-legend">${tiers.map(([color, label]) =>
    `<b><i class="urgency-legend__dot urgency-legend__dot--${color}"></i>${label}</b>`).join("")}</div>`;
}

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
    return commercialEmptyStateHtml("Sin resultados", "Sin oportunidades activas para este filtro.");
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
          const datePrefix = commercialEsc(commercialFmtDate(act.deadline));
          // El badge SÓLO envuelve tipo+relativo ("To-Do (venc. 5d)") — la
          // fecha queda como texto plano antes, igual que el legacy (owner
          // corrective, ver HANDOFF: color por urgencia sobre el primitivo
          // compartido, nunca sobre la fecha).
          if (!act.type) return datePrefix;
          const color = commercialUrgencyColor(days);
          const typeAndRel = `${commercialEsc(act.type)}${commercialEsc(rel)}`;
          const badge = color
            ? `<span class="dashboard-status-badge dashboard-status-badge--${color}">${typeAndRel}</span>`
            : typeAndRel;
          return `${datePrefix} · ${badge}`;
        }).join("<br>")
      : "—";
    // Celda booleana (MISMO patrón que reporting.js::renderCalendarioCell —
    // Integral/Calendario, ver HANDOFF): "Si" → tilde, cualquier otra cosa
    // (No/Sin definir/ausente) → blanco. Nunca "Sí"/"No" como texto.
    const lic = o.licencias === "Si" ? "✓" : "";
    const priorityStars = commercialEsc(COMMERCIAL_PRIORITY_STARS[o.priority] || "—");
    const priorityLabel = COMMERCIAL_PRIORITY_LABELS[o.priority] || "Sin prioridad";
    return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td>${lic}</td>
      <td>${commercialFmtInt(o.hours)}</td>
      <td>${commercialFmtInt(o.weighted_hours)}</td>
      <td>${commercialFmtPercent(o.probability)}</td>
      <td class="chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(priorityLabel)}">${priorityStars}${commercialChartTooltip(priorityLabel)}</td>
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
  // `<colgroup>` propio en %, no en px (visual polish, ver HANDOFF — owner
  // screenshot: "no responsive... incoherencia entre columnas... no tan
  // hardcodeada que ante cualquier cambio mueva todos los márgenes"):
  // con `table-layout:fixed` + `width:100%` (tools.css) un `<col>` en px
  // YA se reescala proporcionalmente al ancho real del panel — verificado
  // en vivo, las 11 columnas escalan con el MISMO factor — así que "px vs
  // %" no era el bug. El problema real era que los px estaban calibrados
  // para el contenido de C4/antes de C5b: Prior. reservaba espacio para
  // "⭐⭐⭐ Muy Alta" (texto que C5b movió al tooltip) y le sobraba hueco
  // vacío, mientras Oportunidad truncaba nombres reales largos ("Loma
  // Negra - Mantenimiento Workzone"). Se expresa en % (más legible que una
  // tabla de px que sólo tiene sentido si suman un total implícito) y se
  // recalibra con evidencia real: Prior. baja a compacto (ya es sólo
  // estrellas), Oportunidad y Próx. Acción — las dos columnas de contenido
  // real más largo — suben. El `<col>` explícito sigue ganando sobre la
  // regla compartida en `table-layout:fixed` (ver comentario de
  // `commercialActivityTableHtml`) — no se toca ningún primitivo de
  // tools.css. `min-width` sobre esta tabla puntual (ver regla en
  // commercial.css, colgada de `[data-commercial-opportunities]` — el
  // wrapper que ya existe, sin clase nueva) evita que esas proporciones
  // se aprieten hasta ser ilegibles en viewports angostos — de ahí en
  // más entra `.matrix-scroll`.
  // Rebalanceo puntual (owner screenshot — "Días resp." partiendo su
  // título en dos líneas mientras Próx. Acción sobraba espacio muerto):
  // Próx. Acción 21%→18%, Días resp. 4%→7%. Misma evidencia que el resto
  // de esta tabla — ancho real necesario por columna, no un número
  // inventado (18% le sobra de sobra a un badge + fecha corta; 7% alcanza
  // para "Días resp." en una sola línea, mismo orden de magnitud que "Hs
  // Pond." a 6%/54px, que ya entra sin partirse).
  return `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:22%"><col style="width:11%"><col style="width:10%">
          <col style="width:4%"><col style="width:5%"><col style="width:6%">
          <col style="width:5%"><col style="width:4%"><col style="width:18%">
          <col style="width:8%"><col style="width:7%">
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
  const section = root.querySelector(`[data-breakdown="${trigger.dataset.event}"]`);
  // `open = true` (owner corrective, ver HANDOFF: la X de cierre ahora vive
  // en `<summary>` y sobrevive el colapso nativo del `<details>` — pero eso
  // deja la sección en un estado "visible pero colapsada" si el owner la
  // había colapsado a mano antes de cerrarla: `initEventBreakdown` sólo
  // gobierna `hidden` (mostrar/ocultar el bloque entero), nunca `open`
  // (expandido/colapsado DENTRO de ese bloque) — son dos ejes
  // independientes. Sin esto, reabrir el tile mostraría sólo la barra del
  // `<summary>`, sin contenido, hasta el próximo render completo. El
  // template siempre emite `open` fresco (commercialDashboardHtml), así
  // que esto sólo importa para la re-apertura SIN un fetch/render de por
  // medio — exactamente este camino.
  if (section) section.open = true;
  section?.scrollIntoView({ block: "start" });
}

// Marcador visual único cuando VARIOS triggers comparten un mismo
// `data-event` (C6 visual corrective, ver HANDOFF): el motor compartido
// resuelve por VALOR del atributo, así que su `show(ev)` marca `.is-active`
// en TODOS los triggers de ese valor a la vez — con "Pipeline total" y
// "Pipeline ponderado" abriendo el MISMO Forecast, eso se leía en pantalla
// como una doble selección accidental. Acá se normaliza DESPUÉS de que el
// motor corrió (su listener cuelga directo del trigger y corre antes de que
// el click burbujee hasta el delegado que llama a esto): se le saca
// `.is-active` a los hermanos y queda sólo en el nodo que el usuario
// realmente activó.
//
// SIN estado propio: el nodo activador llega como argumento del evento y la
// única fuente de verdad sigue siendo el DOM (`.is-active` en el trigger +
// `hidden` en el panel), exactamente como antes. No se guarda en ninguna
// parte "cuál tile abrió" — cambiar de tile de entrada es sólo mover una
// clase, nunca un cambio de estado ni de datos.
//
// Sólo actúa en una APERTURA (`is-active` post-toggle), MISMO criterio que
// `commercialMaybeScrollToOpenedAnalysis`: al cerrar, el motor ya dejó a
// todos inactivos y acá no hay nada que preservar.
//
// GENÉRICO a propósito, no un caso especial de "forecast": cualquier análisis
// del Hub con varios triggers equivalentes hereda el mismo comportamiento, y
// para un análisis con un solo trigger (Activas) es un no-op exacto.
function commercialNormalizeSharedTriggerActiveState(trigger, root) {
  if (!trigger.classList.contains("is-active")) return;
  root.querySelectorAll(`[data-event="${trigger.dataset.event}"]`).forEach(sibling => {
    if (sibling !== trigger) sibling.classList.remove("is-active");
  });
}

// ── Filtros de análisis del detalle de Oportunidades (C5e, ver HANDOFF) ─────
// Etapa/Vendedor son ANALÍTICOS: el mismo universo filtrado alimenta el
// Funnel Y la tabla (ambos ya comparten universo desde C4 — mismo gate que
// `_build_opportunities`). El texto libre es SÓLO de tabla — nunca toca el
// Funnel (contrato explícito del owner: "Buscar 'Loma Negra' con Todas/Todos
// no cambia lo que el Funnel representa"). Todo pure sobre el array YA
// cargado (`commercialState.opportunities`) — `.filter()` siempre devuelve
// un array nuevo, cero mutación del payload fuente, cero round-trip.

function commercialOppStageOptions(pipeline, opportunities) {
  // Orden CANÓNICO = el que ya trae `pipeline.stages` (el backend lo entrega
  // 1→8, ver `_build_pipeline`) — nunca alfabético ni de aparición, que
  // dependería del orden de retorno de Odoo. Sólo etapas REALMENTE
  // presentes en `opportunities` (mismo criterio que el propio Funnel:
  // nunca una opción fantasma sin datos detrás). El fallback (`extra`) es
  // defensivo — mismo universo que `pipeline`, no debería divergir nunca,
  // pero una etapa real nunca queda inseleccionable si divergiera.
  const canonical = ((pipeline && pipeline.stages) || []).map(s => s.stage);
  const present = new Set(opportunities.map(o => o.stage).filter(Boolean));
  const ordered = canonical.filter(s => present.has(s));
  const extra = [...present].filter(s => !canonical.includes(s));
  return [...ordered, ...extra];
}

function commercialOppSellerOptions(opportunities) {
  // Alfabético — a diferencia de Etapa, acá no existe una secuencia
  // canónica que preservar, así que el orden determinístico más simple es
  // el correcto.
  return [...new Set(opportunities.map(o => o.seller).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}

// Etapa + Vendedor COMPONEN (AND): "Etapa 6 + Roberto" = sólo las de Roberto
// EN etapa 6, nunca una unión de ambos criterios. "" en cualquiera de los
// dos = ese eje sin filtrar (Todas/Todos).
function commercialAnalysisFilteredOpportunities(opportunities, stageFilter, sellerFilter) {
  return opportunities.filter(o =>
    (!stageFilter || o.stage === stageFilter) && (!sellerFilter || o.seller === sellerFilter));
}

// Deriva el Funnel LOCAL desde filas YA filtradas por Etapa/Vendedor — MISMA
// forma que `/comercial/data/pipeline` (`{opportunities, stages:[{stage,
// count, hours, weighted_hours, revenue}]}`) para que
// `commercialDistribucionHtml` lo consuma SIN CAMBIOS — cero fork del
// renderer del Funnel, cero segunda implementación de sus 3 series/2 ejes.
// Sólo etapas con al menos una oportunidad aparecen (mismo criterio que el
// propio `commercialDistribucionHtml`) — nunca se renumeran: "etapa 6"
// sigue siendo "etapa 6" aunque sea la única presente, nunca pasa a "etapa
// 1" por ser la única barra del gráfico.
function commercialDeriveFunnelFromOpportunities(opportunities, referencePipeline) {
  const buckets = new Map();
  for (const o of opportunities) {
    if (!o.stage) continue;
    if (!buckets.has(o.stage)) buckets.set(o.stage, { stage: o.stage, count: 0, hours: 0, weighted_hours: 0, revenue: 0 });
    const b = buckets.get(o.stage);
    b.count += 1;
    b.hours += o.hours || 0;
    b.weighted_hours += o.weighted_hours || 0;
    b.revenue += o.revenue || 0;
  }
  const order = commercialOppStageOptions(referencePipeline, opportunities);
  return { opportunities: opportunities.length, stages: order.filter(s => buckets.has(s)).map(s => buckets.get(s)) };
}

// Texto buscable: SOLO valores RAW/DISPLAY del objeto, nunca el HTML
// generado (evita falsos positivos con clases/atributos internos, y evita
// que un cambio de markup rompa la búsqueda). Campos auditados 1:1 contra
// las columnas VISIBLES de la tabla (ver `commercialOpportunitiesTableHtml`)
// — fuente/tag_ids/probabilidad numérica cruda quedan afuera a propósito,
// no son columnas de esta tabla.
function commercialOpportunitySearchableText(o) {
  const acts = o.activities || [];
  const nextActionText = acts.map(act => {
    const days = commercialDaysUntil(act.deadline);
    const rel = days === null ? "" : days < 0 ? `venc. ${Math.abs(days)}d` : days === 0 ? "hoy" : `${days}d`;
    return `${act.type || ""} ${commercialFmtDate(act.deadline)} ${rel}`;
  }).join(" ");
  return [
    o.opportunity, o.client, o.seller,
    o.licencias === "Si" ? "si" : "",
    commercialFmtPercent(o.probability),
    COMMERCIAL_PRIORITY_LABELS[o.priority] || "",
    nextActionText,
    o.last_response ? commercialFmtDate(o.last_response) : "",
    o.days_elapsed != null ? `${o.days_elapsed}d` : "",
  ].join(" ").toLowerCase();
}

function commercialSearchOpportunities(opportunities, text) {
  const q = (text || "").trim().toLowerCase();
  if (!q) return opportunities;
  return opportunities.filter(o => commercialOpportunitySearchableText(o).includes(q));
}

// Composición completa del pipeline de derivación (ver diagrama en HANDOFF:
// tipo → Etapa → Vendedor → [Funnel | búsqueda → tabla → totales]) leída
// SIEMPRE desde `commercialState` — un solo lugar que la calcula, para que
// los 4 call sites (render inicial, sort, Etapa/Vendedor, búsqueda) nunca
// diverjan entre sí sobre qué filas son "las visibles ahora".
function commercialCurrentAnalysisFilteredOpportunities() {
  const allOpps = (commercialState.opportunities && commercialState.opportunities.opportunities) || [];
  return commercialAnalysisFilteredOpportunities(allOpps, commercialState.oppStageFilter, commercialState.oppSellerFilter);
}
// ── Oportunidades por Origen (C8, ver HANDOFF) ─────────────────────────────
// Agregación DERIVADA PURA del subset analítico — cero estado nuevo, cero
// endpoint, cero fetch: `source` ya viaja en cada oportunidad desde C4
// (`_build_opportunities`), con el MISMO fallback `"Sin definir"` que usaba el
// `/crm/origen` legacy. El Hub ya tiene el dato en memoria.
//
// Recibe el subset EXPLÍCITAMENTE en vez de leer el estado, para que el
// llamador sea el que declara qué universo está agregando — el mismo criterio
// que `commercialDeriveFunnelFromOpportunities`. Nunca muta la entrada:
// `Map` propio + `map`/`sort` sobre un array nuevo.
//
// `pct` es sobre el TOTAL del subset (no sobre el máximo): el texto dice
// "% del total", así que la barra tiene que significar lo mismo. Se devuelve
// como número y se formatea al renderizar, para que el mismo valor alimente el
// ancho de la barra y su rótulo — nunca dos cálculos que puedan divergir.
//
// Orden determinístico: `count` desc y `source` asc como desempate estable —
// sin el segundo criterio, dos orígenes con el mismo conteo podían alternar
// posición entre renders según el orden de llegada de Odoo.
function commercialDeriveOrigins(opportunities) {
  const rows = Array.isArray(opportunities) ? opportunities : [];
  const total = rows.length;
  const bySource = new Map();
  rows.forEach(opp => {
    const source = (opp && opp.source && String(opp.source).trim()) || "Sin definir";
    bySource.set(source, (bySource.get(source) || 0) + 1);
  });
  return [...bySource.entries()]
    .map(([source, count]) => ({ source, count, pct: total > 0 ? (count / total) * 100 : 0 }))
    .sort((a, b) => (b.count - a.count) || a.source.localeCompare(b.source, "es"));
}

// Tabla compacta `Origen | Opps. | Distribución | %`. Las filas son
// INFORMATIVAS: sin handler, sin `data-source`, sin `cursor:pointer`, sin
// `tabindex` — el drill por origen que tenía el legacy es un incremento
// aparte, y una afordancia falsa es peor que ninguna.
//
// La distribución NO depende sólo del ancho/color de la barra: el porcentaje
// existe como TEXTO en su propia columna, así que la tabla se lee igual sin
// CSS y sin color. La barra queda `aria-hidden`: es la misma magnitud que la
// columna de al lado, y anunciarla dos veces sólo ensucia el lector.
//
// Subset vacío → NO se dibuja nada (ni rótulo, ni cabeceras, ni 0%): MISMO
// lenguaje que el Funnel hermano, que también desaparece cuando Etapa/Vendedor
// no dejan oportunidades (ver `commercialActivasAnalysisHtml`) — el único
// mensaje lo da el `.dashboard-empty` de la tabla de detalle, nunca un rótulo
// huérfano sobre la nada.
function commercialOriginsHtml(opportunities) {
  const rows = commercialDeriveOrigins(opportunities);
  if (!rows.length) return "";
  const body = rows.map(row => `<tr>
      <td class="row-label-col">${commercialEsc(row.source)}</td>
      <td>${commercialFmtInt(row.count)}</td>
      <td><span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${row.pct.toFixed(1)}%"></span></span></td>
      <td>${commercialEsc(commercialFmtPercent(row.pct))}</td>
    </tr>`).join("");
  // `colgroup` en % — MISMA convención que el resto de las tablas de la
  // vertical (`table.dashboard-table` es `table-layout:fixed`, tools.css).
  return `<p class="perspective-label">Oportunidades por Origen</p>
    <div class="origin-breakdown matrix-scroll">
      <table class="dashboard-table">
        <colgroup><col style="width:40%"><col style="width:15%"><col style="width:30%"><col style="width:15%"></colgroup>
        <thead><tr><th>Origen</th><th>Opps.</th><th>Distribución</th><th>% del total</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

// Contenido de `[data-commercial-funnel-area]`, en UN solo lugar (C8): lo
// arman el render completo y el re-render quirúrgico, y tenerlo duplicado era
// la vía por la que dos bloques del mismo host se desincronizaban (ver el
// precedente de la leyenda de urgencia en `commercialOpportunitiesBlockHtml`).
// Origen vive ACÁ a propósito: este host se re-renderiza con Etapa/Vendedor y
// con Limpiar, y NO con la búsqueda — exactamente el lifecycle que el contrato
// pide para Origen, sin un hook de DOM nuevo.
function commercialFunnelAreaHtml(dist, analysisFiltered) {
  if (!dist) return "";
  const origins = commercialOriginsHtml(analysisFiltered);
  // Funnel | Origen LADO A LADO (C8 visual corrective): son dos lecturas
  // complementarias del MISMO subset Stage+Seller, así que apiladas gastaban
  // alto sin agregar información. Reusa `.analysis-split`, la composición que
  // ya armaba Forecast (gráfico de alto fijo | tabla que crece) — misma
  // figura, mismas proporciones, cero layout nuevo. Con `--top`: acá las dos
  // columnas ARRANCAN juntas (owner smoke), al revés de Forecast, que comparte
  // el piso — misma composición, dos variantes explícitas.
  //
  // Sin Origen (no debería pasar: los dos salen del mismo subset, pero si
  // `opportunities` falló y `pipeline` no, el Funnel existe y Origen no) el
  // Funnel va a ANCHO COMPLETO en vez de dejar media grilla vacía. Nunca dos
  // tarjetas huérfanas: el estado de cero lo sigue dando la tabla de detalle.
  const analysis = origins
    ? `<div class="analysis-split analysis-split--top">
        <div class="analysis-split__chart"><div data-commercial-dist>${dist}</div></div>
        <div class="analysis-split__table">${origins}</div>
      </div>`
    : `<div data-commercial-dist>${dist}</div>`;
  // El Detalle queda FUERA de las dos columnas, a ancho completo: la jerarquía
  // es análisis arriba (Funnel + Origen), detalle transaccional abajo.
  return `${analysis}
      <p class="perspective-label">Detalle de Oportunidades</p>`;
}

function commercialCurrentTableRows() {
  return commercialSearchOpportunities(commercialCurrentAnalysisFilteredOpportunities(), commercialState.oppSearchText);
}

// Tras un cambio de Todos/NN/BI (C5e, ver HANDOFF): preserva Etapa/Vendedor
// SÓLO si siguen siendo valores válidos en el universo recién cargado —
// nunca un filtro apuntando a una etapa/vendedor que ya no existe en este
// `tipo`, dejando una tabla vacía sin explicación aparente. A diferencia de
// Etapa/Vendedor (preserva-si-válido), el texto libre se resetea SIEMPRE en
// `commercialSetTipo` — mismo criterio que `actPeriod`/`actUser` (arriba en
// este archivo: "sus opciones... se derivan de datos que ya no son los
// mismos"), la convención MÁS SIMPLE ya establecida en este archivo para
// filtros locales que sobreviven un cambio de dataset.
function commercialResetInvalidAnalysisFilters() {
  const rows = (commercialState.opportunities && commercialState.opportunities.opportunities) || [];
  const stages = new Set(rows.map(o => o.stage).filter(Boolean));
  const sellers = new Set(rows.map(o => o.seller).filter(Boolean));
  if (commercialState.oppStageFilter && !stages.has(commercialState.oppStageFilter)) commercialState.oppStageFilter = "";
  if (commercialState.oppSellerFilter && !sellers.has(commercialState.oppSellerFilter)) commercialState.oppSellerFilter = "";
}

// Barra de filtros compacta (owner corrective, ver HANDOFF: "no crear un
// toolbar grande, usar el lenguaje visual del Hub"). Reusa
// `.whatsnew-filters`/`.whatsnew-filter` TAL CUAL — el mismo primitivo que
// `commercialActivitiesControlsHtml` (arriba en este archivo) ya usa para
// Período/Responsable — nunca una clase nueva de toolbar. `data-commercial-
// analysis-filters` es sólo un hook de posicionamiento CSS (ver
// commercial.css) — nunca se re-renderiza a sí misma (ver los delegados de
// `change`/`input`, más abajo): sus `<select>` ya reflejan su propio valor
// tras el evento nativo, y regenerarla en cada tecla del buscador le haría
// perder el foco al `<input>`.
//
// "✕ Limpiar" (owner request): resetea la COMBINACIÓN de los tres filtros en
// un solo gesto. Reusa el primitivo COMPARTIDO del Hub
// `.whatsnew-filter__clear` (tools.css) — el MISMO que ya usan What's New, el
// dashboard de Actividad, Staffing y Reporting Integral: cero clase nueva,
// cero CSS de Comercial. Va como ÚLTIMO hijo de `.whatsnew-filters` (misma
// forma que What's New y Actividad, que también lo cuelgan directo de la
// barra) y queda pegado a la derecha porque "Buscar" es
// `.whatsnew-filter--fill` y se come el espacio del medio; `align-self:
// flex-end` del propio primitivo lo alinea con la base de los campos.
//
// SIEMPRE visible, nunca condicionado a que haya algún filtro activo. Dos
// razones, y la segunda es la que decide: (1) es el comportamiento del
// tablero integral, que es el referido; (2) mostrarlo/esconderlo según el
// estado obligaría a regenerar la barra en cada tecla del buscador — que es
// exactamente lo que este bloque documenta que NO se puede hacer (el
// `<input>` perdería el foco). La alternativa sería un tercer camino de
// re-render sólo para togglear un botón; con nada seleccionado, "Limpiar" es
// un no-op inofensivo, no una promesa falsa.
function commercialAnalysisFiltersHtml(stageOptions, sellerOptions) {
  const stageOpts = stageOptions.map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.oppStageFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  const sellerOpts = sellerOptions.map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.oppSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-analysis-filters>
      <label class="whatsnew-filter">
        <span>Etapa</span>
        <select data-commercial-stage-filter>
          <option value=""${commercialState.oppStageFilter === "" ? " selected" : ""}>Todas</option>
          ${stageOpts}
        </select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-seller-filter>
          <option value=""${commercialState.oppSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <label class="whatsnew-filter whatsnew-filter--fill">
        <span>Buscar</span>
        <input type="search" data-commercial-search aria-label="Buscar oportunidades" placeholder="Buscar…" value="${commercialEsc(commercialState.oppSearchText)}">
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-clear-filters>✕ Limpiar</button>
    </div>`;
}

// Leyenda de urgencia + tabla, combinadas (C5e, ver HANDOFF): antes la
// leyenda vivía como hermana suelta entre el Funnel y `[data-commercial-
// opportunities]` — un re-render quirúrgico de SÓLO la tabla (búsqueda,
// Etapa/Vendedor) la dejaba desincronizada (mostraba/escondía la leyenda
// del estado ANTERIOR al filtro). Ahora es parte del MISMO bloque
// re-renderizable: una sola asignación de `innerHTML` mantiene ambas en
// sync, siempre.
function commercialOpportunitiesBlockHtml(rows) {
  return `${rows.length ? commercialUrgencyLegendHtml() : ""}${commercialOpportunitiesTableHtml({ opportunities: rows })}`;
}

// Re-render quirúrgico de Funnel + tabla tras un cambio de Etapa/Vendedor
// (C5e, ver HANDOFF) — nunca la barra de filtros (ver comentario de
// `commercialAnalysisFiltersHtml`). Mismo criterio que el resto del archivo:
// sin fetch, sin volver a cablear `initEventBreakdown`/`initDashboardSections`.
function commercialRerenderAnalysisFunnelAndTable(root) {
  const analysisFiltered = commercialCurrentAnalysisFilteredOpportunities();
  const hasAnalysisFilters = commercialState.oppStageFilter !== "" || commercialState.oppSellerFilter !== "";
  const effectivePipeline = hasAnalysisFilters
    ? commercialDeriveFunnelFromOpportunities(analysisFiltered, commercialState.pipeline)
    : commercialState.pipeline;
  const dist = commercialDistribucionHtml(effectivePipeline);
  const funnelHost = root.querySelector("[data-commercial-funnel-area]");
  if (funnelHost) funnelHost.innerHTML = commercialFunnelAreaHtml(dist, analysisFiltered);
  const tableRows = commercialSearchOpportunities(analysisFiltered, commercialState.oppSearchText);
  const tableHost = root.querySelector("[data-commercial-opportunities]");
  if (tableHost) tableHost.innerHTML = commercialOpportunitiesBlockHtml(tableRows);
}

// Cuerpo del panel de "Oportunidades Activas — Análisis": filtros → gráfico
// (patrón) → tabla (precisión). Todo en la MISMA `.dashboard-section` — no
// una sección nueva, que rompería la relación contextual con el KPI que
// abre este panel. `[data-commercial-funnel-area]` envuelve el Funnel
// (contenedor ESTABLE incluso cuando `dist` es "" — sin etapas — para que
// el re-render quirúrgico de Etapa/Vendedor siempre encuentre un host, ver
// `commercialRerenderAnalysisFunnelAndTable`).
// Sin filtros de análisis activos, `pipeline` llega SIN TOCAR (misma
// referencia que `commercialState.pipeline`) — cero cambio de
// comportamiento respecto de antes de C5e. Sin etapas, `commercialDistribucionHtml`
// devuelve "" y el único mensaje lo da el `.dashboard-empty` de la tabla —
// nunca rótulos huérfanos sobre la nada.
function commercialActivasAnalysisHtml(pipeline) {
  const allOpps = (commercialState.opportunities && commercialState.opportunities.opportunities) || [];
  const analysisFiltered = commercialCurrentAnalysisFilteredOpportunities();
  const hasAnalysisFilters = commercialState.oppStageFilter !== "" || commercialState.oppSellerFilter !== "";
  const effectivePipeline = hasAnalysisFilters ? commercialDeriveFunnelFromOpportunities(analysisFiltered, pipeline) : pipeline;
  const dist = commercialDistribucionHtml(effectivePipeline);
  // El rótulo "Funnel por Etapa" lo emite el propio gráfico, así que acá NO
  // se repite — sólo el de la tabla. C4 (ver HANDOFF) reemplazó el
  // placeholder "Detalle por Etapa" (agrupado, Batch 2A) por el detalle
  // real por OPORTUNIDAD (`commercialOpportunitiesTableHtml`, universo
  // `_build_opportunities`) — no se toca en C3, que fue exclusivamente el
  // chart.
  // Cierre explícito (Active Opportunities Phase 2 / C5d, ver HANDOFF): el
  // botón vive en el `<summary>` (ver `commercialDashboardHtml` —
  // owner corrective: debe sobrevivir el colapso nativo del `<details>`,
  // que es justo lo que este body deja de renderizar). NO tiene estado
  // propio — `[data-commercial-close-analysis]` (delegado de click, más
  // abajo) hace `.click()` sobre el MISMO trigger `[data-event="activas"]`
  // que el motor compartido `initEventBreakdown` ya wireó (ver HANDOFF —
  // KPI → Contextual Drilldown), exactamente como si el owner hubiera
  // clickeado el tile de nuevo. Cero flag nuevo, cero segundo lifecycle: la
  // única fuente de verdad sigue siendo `.is-active` en el tile + `hidden`
  // en este panel, ambos gobernados por ese motor.
  const tableRows = commercialSearchOpportunities(analysisFiltered, commercialState.oppSearchText);
  const filtersHtml = allOpps.length
    ? commercialAnalysisFiltersHtml(commercialOppStageOptions(pipeline, allOpps), commercialOppSellerOptions(allOpps))
    : "";
  return `${filtersHtml}
    <div data-commercial-funnel-area>${commercialFunnelAreaHtml(dist, analysisFiltered)}</div>
    <div data-commercial-opportunities>${commercialOpportunitiesBlockHtml(tableRows)}</div>`;
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
  if (!rows.length) return commercialEmptyStateHtml("Sin resultados", "Sin actividades para este filtro.");
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
    return commercialEmptyStateHtml("No disponible", label);
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
  // Retrasadas SIEMPRE antes que "Por vencer" — orden semántico, ver
  // commercialStaticSection.
  //
  // "Por vencer", no "Pendientes" (owner smoke): el tile de arriba dice "Total
  // pendientes" y cuenta el conjunto ENTERO (158 = 39 retrasadas + 119 por
  // vencer), mientras esta sección tiene sólo las que todavía no vencieron. La
  // aritmética siempre estuvo bien; lo que colisionaba era la palabra —
  // "Pendientes" leído al lado de "Total pendientes" invita a compararlos como
  // si midieran lo mismo. En el dominio TODAS son pendientes (`mail.activity`
  // sólo devuelve pendientes), así que el que tenía que desambiguarse era el
  // subconjunto, no el total. Cambio de COPY: cero cambio de números.
  const sections = commercialStaticSection("act-retrasadas", "Retrasadas",
      `${commercialFmtInt(buckets.overdue.length)} actividad(es)`, commercialActivityTableHtml(buckets.overdue))
    + commercialStaticSection("act-pendientes", "Por vencer",
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
    const configIncomplete = commercialState.errorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los datos comerciales desde el CRM en este momento.";
    // Reintentar sólo si la falla es de transporte (owner request): con el CRM
    // sin configurar, el botón prometería algo que no puede pasar.
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("dashboard", "Reintentar la carga de datos comerciales"));
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

  // Los NUEVE tiles del landing legacy. TRES son clickeables, sobre DOS
  // análisis: "Oportunidades activas" abre el suyo, y "Pipeline total" +
  // "Pipeline ponderado" abren el MISMO Forecast (C6, ver HANDOFF) — no son
  // dos métricas distintas, son la misma población (0-8) medida con y sin
  // probabilidad, y el panel presenta las dos series juntas, así que darle
  // el análisis a una sola de las dos sería arbitrario. Los otros seis
  // conservan su información pero NO simulan una acción que todavía no
  // existe — una afordancia falsa es peor que ninguna.
  // Consecuencia ACEPTADA de que los dos compartan `data-event`: mientras
  // Forecast está abierto los DOS tiles quedan `.is-active`. El motor
  // compartido resuelve por VALOR del atributo, no por nodo, así que no
  // puede (ni debe) distinguir cuál lo abrió — y "los dos KPIs están siendo
  // analizados ahora" es exactamente lo que pasa.
  const kpiGrid = `<div class="activity-kpi-grid">
      ${commercialRichKpi("Oportunidades activas", commercialFmtInt(s.activas.count),
        { sub: hs(s.activas.hours), split: seg("activas"), eventKey: "activas" })}
      ${commercialRichKpi("Prospectos", commercialFmtInt(s.prospectos.count),
        { sub: hs(s.prospectos.hours), split: seg("prospectos") })}
      ${commercialRichKpi("Pipeline total", commercialFmtInt(s.pipeline.hours),
        { sub: "horas", split: seg("pipeline"), eventKey: "forecast", eventView: "total" })}
      ${commercialRichKpi("Pipeline ponderado", commercialFmtInt(s.pipeline.weighted_hours),
        { sub: "horas ponderadas", eventKey: "forecast", eventView: "ponderado" })}
      ${commercialRichKpi("Ganadas", commercialFmtInt(s.ganadas.count),
        { sub: hs(s.ganadas.hours), split: seg("ganadas") })}
      ${commercialRichKpi("Perdidas", commercialFmtInt(s.perdidas.count),
        { sub: hs(s.perdidas.hours), split: seg("perdidas") })}
      ${commercialRichKpi("Suspendidas", commercialFmtInt(s.suspendidas.count),
        { sub: hs(s.suspendidas.hours), split: seg("suspendidas") })}
      ${commercialRichKpi("Creadas esta semana", a ? commercialFmtInt(a.creadas.this_week) : "—",
        { split: a ? commercialKpiPairsHtml([["Semana Anterior", commercialFmtInt(a.creadas.last_week)]]) : "" })}
      ${commercialRichKpi("Conversión", commercialFmtPercent(s.conversion),
        { split: commercialKpiPairsHtml([["Incluyendo suspendidas", commercialFmtPercent(s.conversion_with_suspended)]]) })}
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
    </div>` : commercialEmptyStateHtml("No disponible", "No pudimos calcular las analíticas del pipeline en este momento.");

  // El análisis contextual NO ocupa lugar hasta que se acciona su KPI: el
  // atributo `data-breakdown` va sobre la SECCIÓN entera, así que
  // `initEventBreakdown` la muestra y la esconde completa con el mismo motor
  // de siempre. Sin panel placeholder, sin espacio reservado, sin un segundo
  // mecanismo de breakdown.
  // Botón de cierre DENTRO del `<summary>` (owner corrective, ver HANDOFF:
  // "la X debe permanecer aunque colapses el grupo"): viviendo en
  // `.dashboard-section__body` desaparecía al colapsar el `<details>`
  // nativamente (el body es exactamente lo que ese toggle esconde) — el
  // `<summary>`, en cambio, SIEMPRE es visible, abierto o cerrado. También
  // resuelve gratis la alineación con "XX oportunidad(es)" (owner
  // corrective: "la X debe estar alineada al texto"): mismo `<span>` flex
  // row, mismo baseline, sin un `top` en px calibrado a mano que se
  // desalinea apenas cambia la tipografía real. El click delegado
  // (`data-commercial-close-analysis`, más abajo) hace `preventDefault` +
  // `stopPropagation` ANTES de togglear `.click()` sobre el trigger — sin
  // eso, cualquier click acá también dispararía el toggle nativo de
  // `<summary>` (abrir/cerrar el `<details>`), que es una acción DISTINTA
  // de "cerrar el análisis" (deseleccionar el tile).
  const analysisSection = `<details class="dashboard-section" data-section-id="activas-analisis" data-breakdown="activas" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Oportunidades Activas — Análisis</span>
        <span class="dashboard-section__count">${commercialFmtInt(p.opportunities)} oportunidad(es)</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="activas" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body">${commercialActivasAnalysisHtml(p)}</div>
    </details>`;

  // Forecast (C6, ver HANDOFF): SEGUNDA sección de análisis, hermana de la
  // de Activas y DENTRO DEL MISMO `[data-breakdown-group]` — a propósito. El
  // motor compartido esconde todos los paneles del grupo salvo el que
  // coincide, así que "un solo análisis abierto por vez" (abrir Forecast
  // cierra Activas y viceversa) sale gratis y es el contrato elegido: los
  // dos universos (0-8 y 1-8) nunca se ven simultáneamente. Cero grupo
  // nuevo, cero lifecycle paralelo.
  // El cuerpo y el conteo se actualizan QUIRÚRGICAMENTE cuando resuelve el
  // lazy load (ver `commercialRenderForecastPanel`) — nunca con un
  // `commercialRender()` completo, que reconstruiría
  // `#commercial-main-content` y cerraría este mismo panel (el estado
  // abierto/cerrado vive SÓLO en el DOM: `hidden` + `.is-active`). De ahí
  // los dos hosts estables: `[data-commercial-forecast-count]` y
  // `[data-commercial-forecast-body]`.
  const forecastSection = `<details class="dashboard-section" data-section-id="forecast-analisis" data-breakdown="forecast" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title" data-commercial-forecast-title>${commercialEsc(COMMERCIAL_FORECAST_METRIC.total.title)}</span>
        <span class="dashboard-section__count" data-commercial-forecast-count>${commercialEsc(commercialForecastCountLabel("total"))}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="forecast" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-forecast-body>${commercialForecastPanelBodyHtml("total")}</div>
    </details>`;

  // "Resumen" va SIN contador en el header: la sección agrupa nueve métricas
  // distintas, así que fijar ahí el conteo de una sola (Activas) le daba un
  // protagonismo que no le corresponde y sugería que el bloque entero hablaba
  // de eso. Cada tile ya declara su propia magnitud.
  const sections = commercialDashboardSection("resumen", "Resumen", "", kpiGrid)
    + commercialDashboardSection("analiticas", "Analíticas del Pipeline", "", analyticsGrid)
    + analysisSection
    + forecastSection;
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
      commercialResetInvalidAnalysisFilters();
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

// ── Forecast — lazy load + render quirúrgico (C6, ver HANDOFF) ──────────────
// Re-render QUIRÚRGICO del panel de Forecast: SÓLO su cuerpo y su conteo.
// Nunca `commercialRender()`/`commercialRenderCapability()`, que
// reconstruirían el subárbol y cerrarían el propio panel que se está
// llenando (el estado abierto/cerrado del desglose vive únicamente en el DOM
// — `hidden` + `.is-active`, gobernados por `initEventBreakdown`). MISMA
// técnica que ya usan el sort y la búsqueda del detalle de Oportunidades:
// buscar el host estable y reemplazar su `innerHTML`. El `root` se resuelve
// acá (esta función también corre desde el resolve de un fetch, fuera de
// cualquier delegado) con el MISMO `getElementById` que ya usa
// `commercialRender` — nunca una búsqueda global sobre `document`, que
// saldría a buscar nodos por todo el documento.
function commercialRenderForecastPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  // El modo se resuelve UNA vez y se pasa: los tres hosts tienen que hablar
  // de la misma lectura, y releerlo por host sería tres búsquedas de DOM para
  // el mismo dato.
  const mode = commercialForecastMode(root);
  const body = root.querySelector("[data-commercial-forecast-body]");
  if (body) body.innerHTML = commercialForecastPanelBodyHtml(mode);
  const count = root.querySelector("[data-commercial-forecast-count]");
  if (count) count.textContent = commercialForecastCountLabel(mode);
  // Título conmutable (C6b): "Forecast" / "Forecast Ponderado" según el tile
  // de entrada, como el legacy. Va por el MISMO camino quirúrgico que el
  // resto del panel — nunca un render completo, que lo cerraría.
  const title = root.querySelector("[data-commercial-forecast-title]");
  if (title) title.textContent = COMMERCIAL_FORECAST_METRIC[mode].title;
}

// Una sola request, disparada por la PRIMERA apertura del desglose para el
// `tipo` vigente. Reusa el MISMO contrato fail-soft que el resto de
// Comercial y el MISMO loader canónico (`HubForegroundProgress`, key
// "commercial"). El fallo queda AISLADO: sólo este panel avisa — el
// Dashboard, Activas, las analíticas y el resto de los datos no se enteran.
async function commercialLoadForecast() {
  commercialState.forecastStatus = "loading";
  commercialRenderForecastPanel();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/forecast?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.forecast = resp.forecast;
      commercialState.forecastStatus = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.forecastStatus = "error";
      commercialState.forecastErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.forecastStatus = "error";
    commercialState.forecastErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderForecastPanel();
}

// Puente entre el motor compartido y el lazy load. Se llama DESPUÉS de que
// `initEventBreakdown` procesó el click/keydown (su listener cuelga directo
// del trigger, así que `.is-active` ya refleja el estado post-click), con el
// MISMO criterio que `commercialMaybeScrollToOpenedAnalysis`: sólo cuando el
// trigger QUEDÓ activo, o sea sólo en una APERTURA. Cerrar y reabrir con el
// mismo `tipo` NO vuelve a pedir nada — `forecastStatus` ya no es "idle".
// Tampoco reintenta solo tras un error: el estado queda "error" hasta que
// cambie el `tipo` (un reintento automático por cada apertura convertiría un
// CRM caído en una tormenta de requests).
function commercialMaybeLoadForecast(trigger) {
  if (trigger.dataset.event !== "forecast") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.forecastStatus !== "idle") return;
  commercialLoadForecast();
}

// Cambiar el tile de entrada con el panel YA abierto (C6b): el payload es el
// MISMO, cambia sólo la lectura (columna de la tabla, título y total). Se
// re-renderiza quirúrgicamente y CERO red — el modo lo lee del DOM, así que
// alcanza con volver a dibujar. Sólo cuando el trigger quedó activo (nunca al
// cerrar) y sólo con payload en mano: si todavía está cargando o falló, el
// propio `commercialLoadForecast` va a renderizar cuando corresponda.
function commercialMaybeRerenderForecastForMode(trigger) {
  if (trigger.dataset.event !== "forecast") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.forecastStatus !== "ready") return;
  commercialRenderForecastPanel();
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
  // Forecast (C6, ver HANDOFF): se invalida igual que el resto, y NO se
  // re-pide acá — vuelve a "idle" y lo carga la próxima apertura del
  // desglose, ya con el tipo nuevo. MISMO criterio que Actividades: cero
  // cache por tipo, cero request por un análisis que quizá no se abra.
  commercialState.forecast = null;
  commercialState.forecastStatus = "idle";
  commercialState.forecastErrorState = "";
  // Sus filtros también: un valor de Etapa/Vendedor podría apuntar a un
  // universo que ya no existe tras cambiar Todos/NN/BI (MISMO criterio que
  // los filtros de Actividades y el texto libre de Oportunidades).
  commercialState.forecastStageFilter = "";
  commercialState.forecastSellerFilter = "";
  // Los filtros locales de Actividades se reinician con el dataset: sus
  // opciones de Responsable se derivan de datos que ya no son los mismos.
  commercialState.actPeriod = "all";
  commercialState.actUser = "";
  // Texto libre del detalle de Oportunidades (C5e, ver HANDOFF): mismo
  // criterio que Actividades arriba — se resetea siempre, nunca sobrevive a
  // un dataset distinto. Etapa/Vendedor NO se tocan acá: su reset es
  // condicional ("preserva si sigue siendo válido") y depende del universo
  // NUEVO, que todavía no llegó — ver `commercialResetInvalidAnalysisFilters`,
  // llamado dentro de `commercialLoadData` una vez que el fetch resuelve.
  commercialState.oppSearchText = "";
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
    // Cerrar el análisis (Active Opportunities Phase 2 / C5d, ver HANDOFF):
    // NO hay estado propio — se dispara un click REAL sobre el MISMO tile
    // `[data-event="activas"]` que abrió el panel, exactamente el camino
    // que ya maneja `initEventBreakdown` (app.js) para des-seleccionar. Si
    // por lo que sea el tile no está montado, no rompe (optional chaining).
    // `preventDefault`+`stopPropagation` (owner corrective: el botón ahora
    // vive DENTRO de `<summary>`, ver `commercialDashboardHtml`) — sin
    // esto, el click también dispara el toggle NATIVO de `<summary>`
    // (abrir/cerrar el `<details>`), una acción distinta de "cerrar el
    // análisis" que además dejaría el panel colapsado la próxima vez que
    // se reabra el tile (el template siempre emite `open`, pero el toggle
    // nativo lo pisa hasta el próximo render completo).
    // Parametrizado en C6 (ver HANDOFF): el atributo LLEVA la key del
    // análisis (`activas` | `forecast`), así que los dos paneles usan este
    // MISMO camino y no hay un segundo mecanismo de cierre.
    //
    // Resuelve el trigger ACTIVO de ese análisis (`.is-active`), no el
    // primero del DOM (C6 visual corrective, ver HANDOFF). Antes daba igual
    // porque los dos tiles de Pipeline quedaban activos a la vez; desde que
    // sólo queda activo el que el usuario apretó (ver
    // `commercialNormalizeSharedTriggerActiveState`), tomar el primero
    // rompería el cierre en el caso real: con Forecast abierto desde
    // "Pipeline ponderado", el primer `[data-event="forecast"]` del DOM es
    // "Pipeline total", que está INACTIVO — su toggle lo leería como una
    // apertura y el panel se quedaría abierto en vez de cerrarse.
    //
    // Sin fallback al primero a propósito: ése es exactamente el bug. Si no
    // hay trigger activo, el panel no puede estar visible (el motor pone
    // `hidden` y `.is-active` en la MISMA operación), así que no hay nada que
    // cerrar y el optional chaining lo vuelve un no-op. Para Activas —un solo
    // trigger, activo siempre que su panel esté abierto— el selector resuelve
    // al mismo nodo de siempre: comportamiento idéntico, sin caso especial.
    const closeBtn = event.target.closest("[data-commercial-close-analysis]");
    if (closeBtn) {
      event.preventDefault();
      event.stopPropagation();
      const key = closeBtn.dataset.commercialCloseAnalysis;
      root.querySelector(`[data-event="${key}"].is-active`)?.click();
      return;
    }
    // Ordenar la tabla de detalle (Active Opportunities Phase 2 / C4, ver
    // HANDOFF): re-render QUIRÚRGICO de `[data-commercial-opportunities]`,
    // sin fetch (el orden es client-side puro) y sin tocar el gráfico ni
    // volver a cablear el motor de desglose compartido. Ordena sobre el
    // resultado YA filtrado por Etapa/Vendedor/búsqueda (C5e, ver HANDOFF)
    // — nunca sobre `commercialState.opportunities` crudo, que ignoraría
    // cualquier filtro activo y volvería a mostrar TODAS las filas al
    // ordenar.
    // "✕ Limpiar" (owner request): resetea la COMBINACIÓN de filtros de
    // análisis — Etapa, Vendedor y Buscar, los tres de una vez. NO toca el
    // filtro GLOBAL Todos/New Name/Base Instalada (vive en el hero, es
    // transversal a todo el workspace y tiene otro alcance) ni el orden de la
    // tabla (`oppSortKey`, que a propósito sobrevive incluso a un cambio de
    // tipo — es una preferencia de lectura, no un filtro).
    // La barra NO se regenera, MISMO contrato que Etapa/Vendedor/Buscar (ver
    // `commercialAnalysisFiltersHtml`): se ponen en su valor neutro los tres
    // controles YA montados, buscándolos desde el botón (`closest` a la barra)
    // y nunca por todo el documento. Así el `<input>` no se recrea y el foco
    // se queda en "Limpiar" — un `outerHTML` de la barra destruiría el propio
    // botón recién clickeado y el foco se caería al `<body>`.
    // "Reintentar" (owner request + corrective, ver HANDOFF): cada estado de
    // no-disponible reintenta LO SUYO, despachando por la key del atributo.
    // Ninguno de los dos caminos duplica lifecycle: los dos reusan el loader
    // que ya existe, que se ocupa del estado de carga, del loader canónico,
    // del fail-soft y de su propio render.
    //
    // FORECAST: `commercialLoadForecast` directo — NO `commercialMaybeLoadForecast`,
    // que exige `forecastStatus === "idle"` justamente para no reintentar solo
    // en cada apertura; acá el reintento es explícito, lo pidió el usuario. El
    // loader arranca poniendo `loading` y renderizando el panel, así que no
    // hace falta preparar nada más: la transición es error → loading →
    // ready|error, siempre por render QUIRÚRGICO (el panel no se cierra) y sin
    // tocar summary/pipeline/analytics/opportunities ni los filtros del propio
    // Forecast. El `tipo` lo lee el loader del estado global, como siempre.
    //
    // Los dos guards son contra `loading` —no contra `error`— para que un
    // doble click no dispare dos tandas concurrentes, y cada uno mira SU
    // propio status: un Forecast cargando no debe bloquear el reintento del
    // Dashboard ni al revés.
    const retryBtn = event.target.closest("[data-commercial-retry]");
    if (retryBtn) {
      if (retryBtn.dataset.commercialRetry === "forecast") {
        if (commercialState.forecastStatus !== "loading") commercialLoadForecast();
      } else if (commercialState.status !== "loading") {
        commercialLoadData();
      }
      return;
    }
    // "✕ Limpiar" del Forecast (C6b): mismo primitivo y mismo gesto que el de
    // Activas, pero acá NO hace falta tocar los controles a mano — el panel de
    // Forecast SÍ se re-renderiza quirúrgicamente, así que los `<select>` se
    // regeneran con su valor neutro solos. Cero hooks de DOM propios y cero
    // red: el payload no cambia.
    const forecastClear = event.target.closest("[data-commercial-forecast-clear]");
    if (forecastClear) {
      commercialState.forecastStageFilter = "";
      commercialState.forecastSellerFilter = "";
      commercialRenderForecastPanel();
      return;
    }
    const clearBtn = event.target.closest("[data-commercial-clear-filters]");
    if (clearBtn) {
      commercialState.oppStageFilter = "";
      commercialState.oppSellerFilter = "";
      commercialState.oppSearchText = "";
      const bar = clearBtn.closest("[data-commercial-analysis-filters]");
      if (bar) {
        const stage = bar.querySelector("[data-commercial-stage-filter]");
        const seller = bar.querySelector("[data-commercial-seller-filter]");
        const search = bar.querySelector("[data-commercial-search]");
        if (stage) stage.value = "";
        if (seller) seller.value = "";
        if (search) search.value = "";
      }
      commercialRerenderAnalysisFunnelAndTable(root);
      return;
    }
    const sortTh = event.target.closest("[data-commercial-sort]");
    if (sortTh) {
      commercialToggleOppSort(sortTh.dataset.commercialSort);
      const host = root.querySelector("[data-commercial-opportunities]");
      if (host) host.innerHTML = commercialOpportunitiesBlockHtml(commercialCurrentTableRows());
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
      // Lazy load del Forecast (C6, ver HANDOFF): mismo punto de enganche y
      // mismo criterio "sólo si quedó activo" que la navegación. No-op para
      // cualquier otro trigger y para las reaperturas del mismo tipo.
      commercialNormalizeSharedTriggerActiveState(kpiTrigger, root);
      commercialMaybeLoadForecast(kpiTrigger);
      commercialMaybeRerenderForecastForMode(kpiTrigger);
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
  // Flip dinámico del tooltip (corrective, ver HANDOFF — generalizado a
  // CUALQUIER `.chart-tooltip-anchor`, no sólo las columnas del Funnel: el
  // mismo recorte apareció en el tooltip de Prior., dentro de la tabla, que
  // nunca escuchaba este flip). Se recalcula en CADA hover/foco — barato
  // (dos getBoundingClientRect() + un toggle de clase) y determinístico,
  // nunca depende de un cálculo cacheado que pueda quedar stale si la
  // sección se reordena o el viewport cambia entre una entrada y la
  // siguiente. `mouseover`/`focusin` (burbujean, a diferencia de
  // `mouseenter`/`focus`) para poder delegar sobre `document`, MISMO
  // criterio que el resto del archivo — cero listener por anchor.
  document.addEventListener("mouseover", event => {
    const anchor = event.target.closest(".chart-tooltip-anchor");
    if (anchor) commercialPositionChartTooltip(anchor);
  });
  document.addEventListener("focusin", event => {
    const anchor = event.target.closest(".chart-tooltip-anchor");
    if (anchor) commercialPositionChartTooltip(anchor);
  });
  document.addEventListener("change", event => {
    const select = event.target.closest("[data-commercial-act-user]");
    if (!select) return;
    commercialState.actUser = select.value;
    commercialRenderCapability();
  });
  // Filtros de análisis Etapa/Vendedor (C5e, ver HANDOFF): re-render
  // QUIRÚRGICO — Etapa/Vendedor tocan Funnel + tabla (`[data-commercial-
  // funnel-area]` + `[data-commercial-opportunities]`), nunca la barra de
  // filtros en sí (sus `<select>` ya reflejan su propio valor nativo tras el
  // `change` — regenerarla igual no rompería nada, pero es trabajo de más).
  // Listener SEPARADO del de arriba (no ramas encadenadas en el mismo
  // callback): ese usa `if (!select) return` — con una sola rama, agregar
  // más ahí cortaría la ejecución para cualquier otro `<select>`.
  document.addEventListener("change", event => {
    const stageSelect = event.target.closest("[data-commercial-stage-filter]");
    const sellerSelect = event.target.closest("[data-commercial-seller-filter]");
    if (!stageSelect && !sellerSelect) return;
    if (stageSelect) commercialState.oppStageFilter = stageSelect.value;
    if (sellerSelect) commercialState.oppSellerFilter = sellerSelect.value;
    commercialRerenderAnalysisFunnelAndTable(root);
  });
  // Filtros del Forecast (C6b, ver HANDOFF): Etapa/Vendedor son ANALÍTICOS —
  // el mismo universo filtrado alimenta el gráfico Y la tabla, igual que en
  // Activas y igual que el legacy, que re-agrupaba todo el bloque. Todo
  // client-side sobre `months[].opportunities[]`, que el payload ya trae:
  // cero round-trip. Listener SEPARADO, mismo criterio que el de arriba (con
  // una sola rama, un `return` temprano cortaría la ejecución para el otro).
  document.addEventListener("change", event => {
    const stageSelect = event.target.closest("[data-commercial-forecast-stage-filter]");
    const sellerSelect = event.target.closest("[data-commercial-forecast-seller-filter]");
    if (!stageSelect && !sellerSelect) return;
    if (stageSelect) commercialState.forecastStageFilter = stageSelect.value;
    if (sellerSelect) commercialState.forecastSellerFilter = sellerSelect.value;
    commercialRenderForecastPanel();
  });
  // Búsqueda libre (C5e, ver HANDOFF): `input`, no `change` — filtra en cada
  // tecla (UX esperada de un buscador), nunca espera blur/Enter. SÓLO toca
  // `[data-commercial-opportunities]` — jamás el Funnel (contrato explícito
  // del owner) — y, crucialmente, nunca la barra de filtros: si regenerara
  // el `<input>` en cada tecla, el foco/cursor se perdería después del
  // primer carácter.
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-search]");
    if (!search) return;
    commercialState.oppSearchText = search.value;
    const host = root.querySelector("[data-commercial-opportunities]");
    if (host) host.innerHTML = commercialOpportunitiesBlockHtml(commercialCurrentTableRows());
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
    commercialNormalizeSharedTriggerActiveState(kpiTrigger, root);
    commercialMaybeLoadForecast(kpiTrigger);
    commercialMaybeRerenderForecastForMode(kpiTrigger);
    commercialMaybeScrollToOpenedAnalysis(kpiTrigger, root);
  });
  commercialLoadData();
});
