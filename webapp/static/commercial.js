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

// Capacidades IMPLEMENTADAS del workspace. Bucket 1 (ver HANDOFF) sumó las
// TRES primeras tabs de tabla legacy — Licencias/Historial Perdidas/Detalle
// Oportunidades. Bucket 2 suma Clientes (con su sub-tab Ex Clientes, ver
// `commercialClientesHtml`) y Leads. Deliberadamente NO se lista Mis Tareas
// (próxima iteración, semántica propia de usuario/escritura): una tab
// muerta es una promesa rota, se agregan cuando exista contenido real.
const COMMERCIAL_CAPABILITIES = [
  { id: "dashboard", label: "Dashboard" },
  { id: "actividades", label: "Actividades" },
  { id: "licencias", label: "Licencias" },
  { id: "historial-perdidas", label: "Historial Perdidas" },
  { id: "detalle", label: "Detalle Oportunidades" },
  { id: "clientes", label: "Clientes" },
  { id: "leads", label: "Leads" },
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
  // Owner corrective — "SCOPED RETRY FOR PIPELINE ANALYTICS" (ver HANDOFF):
  // status PROPIO de "Analíticas del Pipeline", separado del `status`
  // general del landing (arriba). Antes esta sección sólo tenía el dato
  // (`analytics`, null si degradó) sin un status distinguible — no había
  // forma de mostrar "cargando" durante un reintento sin tocar el resto del
  // Dashboard. MISMO contrato idle|loading|ready|error que Forecast/Creadas/
  // pipeline-analytics-detail — la carga INICIAL (`commercialLoadData`) la
  // deja en ready|error junto con el resto del landing; SÓLO el reintento
  // (`commercialLoadAnalytics`) la pasa por loading.
  analyticsStatus: "idle",
  analyticsErrorState: "",
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
  // Drill de "Creadas" (Creadas Legacy Parity, ver HANDOFF — paridad con el
  // legacy `#created-drill-section`/`/crm/opp-by-month`) — LAZY, MISMO
  // criterio que Forecast: no entra al `Promise.all` inicial, se pide la
  // primera vez que se abre el desglose, se reusa mientras el `tipo` no
  // cambie. `idle` = todavía no se pidió, o quedó invalidado por un cambio
  // de Todos/NN/BI.
  // HIDRATACION POR AÑO (owner performance corrective). Antes había UN
  // `creadas` con todos los meses de la historia; ahora hay un estado
  // EXPLICITO por año, porque la existencia de una opción en el `<select>` no
  // es evidencia de que sus datos estén cargados:
  //
  //   creadasByYear = { "2026": { status, data }, "2023": { status, data } }
  //
  // `status`: idle | loading | ready | error. Un año sin entrada es COLD.
  creadasByYear: {},
  // Años SELECCIONABLES — metadata, del endpoint de descubrimiento. NO implica
  // que esos años estén hidratados (owner: "YEAR OPTION != YEAR HYDRATED").
  creadasSelectableYears: [],
  creadasAniosStatus: "idle",
  // Estado del AÑO ACTIVO, derivado de `creadasByYear` — lo que el panel mira
  // para decidir qué renderizar. Se mantiene por compatibilidad con los
  // renderers, que preguntan por UN dataset.
  creadas: null,
  creadasStatus: "idle", // idle | loading | ready | error
  creadasErrorState: "",
  // Año seleccionado del drill — `null` hasta que el payload resuelve (el
  // año por default es el más reciente CON datos, ver
  // `commercialCreadasDefaultYear`, nunca el año calendario si no hay
  // oportunidades ese año). Mes/semana expandidos — client-side puro,
  // nunca un fetch (MISMO dataset ya cargado, sólo cambia qué fila se ve
  // abierta). "" = ninguno expandido.
  creadasYear: null,
  // Meses expandidos — VARIOS a la vez. El legacy abría uno solo y cerraba el
  // anterior (`_createdExpandedMonth`); poder abrir dos para compararlos es
  // una mejora aprobada, no una desviación de paridad.
  creadasExpandedMonths: [],
  // Selección para el detalle — SEPARADA de la expansión. Antes el detalle se
  // derivaba del mes expandido, así que un mismo click hacía dos cosas
  // inconexas y no había forma de cerrar el detalle sin colapsar el mes.
  // `null` = sin detalle; `{ month, week }` con `week === null` para el mes
  // entero.
  creadasSelection: null,
  // Filtros LOCALES del drill — se resuelven sobre el payload ya cargado y
  // NUNCA disparan un fetch (el universo de negocio lo cambia sólo Tipo).
  creadasSeller: "",
  creadasSearch: "",
  // Orden del detalle — MISMO contrato que Actividades y Oportunidades:
  // `null` = el orden canonico (fecha de entrada ascendente, el del legacy),
  // y un click en una cabecera lo cambia. La primera columna que se clickea
  // arranca ascendente; volver a clickearla invierte.
  creadasSortKey: null,
  creadasSortDir: 1,
  // Actividades — LAZY (ver commercialSetCapability): Dashboard no paga el
  // costo de una capability que no se está viendo. `idle` = todavía no se
  // pidió, o quedó stale tras cambiar el filtro global.
  activities: null,
  activitiesStatus: "idle", // idle | loading | ready | error
  activitiesErrorState: "",
  // Bucket 1 — Detalle Oportunidades + Licencias (ver HANDOFF): MISMO
  // universo/endpoint compartido por las DOS tabs (el legacy ya las trata
  // como la misma fuente — Licencias es un filtro `licencias === "Si"`
  // client-side sobre EL MISMO dataset que Detalle Oportunidades). LAZY —
  // se pide la primera vez que se abre CUALQUIERA de las dos, la otra
  // reusa el mismo estado sin una segunda request.
  opportunitiesDetail: null,
  opportunitiesDetailStatus: "idle", // idle | loading | ready | error
  opportunitiesDetailErrorState: "",
  // Filtros LOCALES de Licencias — client-side sobre `opportunitiesDetail`
  // ya filtrado a licencias === "Si", nunca un round-trip propio.
  licSearchText: "",
  licStageFilter: "",
  licSellerFilter: "",
  licSortKey: null,
  licSortDir: 1,
  // Filtros LOCALES de Detalle Oportunidades — client-side sobre el
  // universo COMPLETO de `opportunitiesDetail` (sin el filtro de Licencias).
  detSearchText: "",
  detStageFilter: "",
  detSellerFilter: "",
  detSortKey: null,
  detSortDir: 1,
  // Bucket 1 — Historial de Perdidas (ver HANDOFF): universo propio
  // (`active=False, probability=0`, todos los años salvo el actual), LAZY,
  // endpoint propio.
  lostHistory: null,
  lostHistoryStatus: "idle", // idle | loading | ready | error
  lostHistoryErrorState: "",
  histSearchText: "",
  histYearFilter: "",
  histSellerFilter: "",
  histMotivoFilter: "",
  histSortKey: null,
  histSortDir: 1,
  // Bucket 2 — Leads (ver HANDOFF): universo propio (`type=lead`), LAZY,
  // endpoint propio. `leadsExpanded` — MISMO patrón que
  // `creadasExpandedMonths` (array de claves de empresa abiertas).
  leads: null,
  leadsStatus: "idle", // idle | loading | ready | error
  leadsErrorState: "",
  leadsExpanded: [],
  leadsSearchText: "",
  leadsSortKey: null,
  leadsSortDir: 1,
  // Bucket 2 — Clientes / Ex Clientes (ver HANDOFF): DOS datasets/estados
  // separados (universos distintos), un sub-tab LOCAL (`clientesSubTab`,
  // nunca una capability propia) que decide cuál se ve. Ex Clientes es
  // LAZY DENTRO de Clientes — se pide recién al abrir ese sub-tab.
  clients: null,
  clientsStatus: "idle",
  clientsErrorState: "",
  clientesSubTab: "clientes", // "clientes" | "ex-clientes"
  clientesExpanded: [],
  clientesSearchText: "",
  clientesSellerFilter: "",
  clientesStatusFilter: "all", // all | active | prospect | none
  clientesSortKey: null,
  clientesSortDir: 1,
  exClients: null,
  exClientsStatus: "idle",
  exClientsErrorState: "",
  exClientesSearchText: "",
  exClientesSellerFilter: "",
  // Filtros LOCALES de Actividades — client-side sobre el dataset ya
  // cargado, nunca un round-trip (ver commercialFilteredActivities).
  actPeriod: "all", // all | week:0 | week:1 | week:2
  actUser: "",
  // Búsqueda y orden de Actividades (C9, ver HANDOFF) — MISMO comportamiento
  // transversal que el detalle de Oportunidades. A diferencia de Activas, acá
  // la búsqueda SÍ entra en el conjunto filtrado: los dos KPIs de arriba son
  // literalmente los contadores de las secciones de abajo (no una vista
  // analítica aparte como el Funnel), así que dejarla afuera los haría
  // contradecir los encabezados. `null` = sin ordenar (orden de llegada).
  actSearchText: "",
  actSortKey: null,
  actSortDir: 1, // 1 = ascendente, -1 = descendente
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
  // Prospectos (Commercial Tile 5, ver HANDOFF) — LAZY, MISMO criterio que
  // Forecast/Creadas: NO entra al `Promise.all` inicial (owner: "initial
  // Commercial dashboard → NO Prospectos detail hydration"), se pide la
  // primera vez que se abre el desglose y se reusa mientras el `tipo` no
  // cambie. A diferencia de Activas (que SÍ hidrata con el resto del
  // landing), Prospectos es un universo de una sola etapa sin gráfico de
  // Funnel ni desglose por procedencia propios — sólo tabla + filtros
  // locales Vendedor/Buscar, sin cascada de Etapa (universo ya es una sola
  // etapa) ni chip de filtro activo (owner: "no hay cascada gráfico →
  // filtro acá").
  prospects: null,
  prospectsStatus: "idle", // idle | loading | ready | error
  prospectsErrorState: "",
  prospectsSellerFilter: "",
  prospectsSearchText: "",
  // Orden de la tabla — client-side puro, MISMO contrato que `oppSortKey`:
  // sobrevive el cambio de Todos/NN/BI (preferencia de lectura, no filtro).
  prospectsSortKey: null,
  prospectsSortDir: 1,
  // Ganadas (Commercial Tile 6, ver HANDOFF) — LAZY, MISMO criterio que
  // Forecast/Creadas/Prospectos. A diferencia de Creadas, Ganadas NO usa la
  // política de ventana caliente: sólo el año ACTIVO se hidrata al abrir el
  // desglose — nunca el anterior en background (owner: "Ganadas does NOT
  // use Creadas' hot-window policy"). El selector de año ofrece año actual
  // .. año actual−5 (rango ESTÁTICO, paridad legacy — nunca un endpoint de
  // descubrimiento: acá "opción de año" nunca significó "año con datos
  // garantizados" ni tampoco necesitó una consulta barata para poblarse).
  //
  // ganadasByYear = { "2026": { status, data, errorState } } — MISMO
  // contrato que `creadasByYear`, pero acá cada entrada es SÓLO la petición
  // explícita del usuario, nunca un prefetch de background.
  ganadasByYear: {},
  // Estado del AÑO ACTIVO, derivado de `ganadasByYear` — mismo patrón que
  // `creadas`/`creadasStatus`: los renderers preguntan por UN dataset.
  ganadas: null,
  ganadasStatus: "idle", // idle | loading | ready | error
  ganadasErrorState: "",
  ganadasYear: null, // "" hasta la primera hidratación; luego el año activo
  // Vendedor — MISMA función sirve al <select>, al click en el ranking y a
  // Limpiar (ver `commercialGanadasSetSeller`), MISMO criterio que Creadas.
  // Sin búsqueda: el legacy Ganadas nunca tuvo un buscador (owner: "Do NOT
  // add free-text Buscar in this increment").
  ganadasSellerFilter: "",
  // Cliente (owner corrective, post-smoke): SEGUNDO eje de filtro LOCAL,
  // compone en AND con Vendedor — MISMO criterio que Etapa+Vendedor en
  // Activas (`commercialAnalysisFilteredOpportunities`). Independiente del
  // ranking (que sigue siendo SÓLO por vendedor): filtrar por Cliente
  // acota el subconjunto que el ranking resume, nunca al revés.
  ganadasClientFilter: "",
  // Orden de la tabla — client-side puro, MISMA semántica que
  // `prospectsSortKey`: sobrevive el cambio de Todos/NN/BI.
  ganadasSortKey: null,
  ganadasSortDir: 1,
  // Perdidas (Commercial Tile 7, ver HANDOFF) — MISMO ciclo de vida por año
  // que Ganadas (lazy, sin ventana caliente, cache por año). Se diferencia
  // en tener TRES ejes de filtro local en vez de dos — Vendedor, Cliente y
  // Motivo — que componen en FACETADO, no en AND simple sobre todo: cada
  // eje analítico (ranking/desglose) IGNORA su PROPIO filtro activo al
  // calcular su universo, para no colapsarse a una sola barra cuando el
  // usuario ya lo seleccionó (owner §11 — "avoid self-filter collapse").
  // La tabla sí aplica los tres a la vez.
  perdidasByYear: {},
  perdidas: null,
  perdidasStatus: "idle", // idle | loading | ready | error
  perdidasErrorState: "",
  perdidasYear: null,
  perdidasSellerFilter: "",
  perdidasClientFilter: "",
  // Motivo — MISMA idea que Vendedor/Cliente pero con TOGGLE-TO-CLEAR
  // (clickear el motivo ya activo lo despeja): así se comportaba el
  // doughnut del legacy (`lostMotivoFilter = lostMotivoFilter === label ?
  // null : label`) y el owner no pidió cambiarlo — a diferencia del
  // ranking por vendedor (Ganadas/Perdidas), que nunca tuvo toggle.
  perdidasMotivoFilter: "",
  // Orden de la tabla — MISMA semántica que `ganadasSortKey`.
  perdidasSortKey: null,
  perdidasSortDir: 1,
  // Suspendidas (Commercial Tile 8, ver HANDOFF) — STOCK actual, SIN año
  // (a diferencia de Ganadas/Perdidas): MISMO ciclo de vida lazy-simple que
  // Prospectos — una sola petición al abrir, cache implícito mientras el
  // `tipo` no cambie, SIN caché por año (no aplica) y SIN Buscar (owner §10
  // — sólo Vendedor + Limpiar).
  suspendidas: null,
  suspendidasStatus: "idle", // idle | loading | ready | error
  suspendidasErrorState: "",
  suspendidasSellerFilter: "",
  suspendidasSortKey: null,
  suspendidasSortDir: 1,
  // Analítica del Pipeline — detalle (ver HANDOFF): CINCO tiles comparten UN
  // fetch/estado (`pipelineAnalyticsDetail*`, MISMO universo por debajo),
  // cada uno con su propio sort local. "Conversión de leads" es aparte
  // (universo propio, `leadConversionDetail*`).
  pipelineAnalyticsDetail: null,
  pipelineAnalyticsDetailStatus: "idle", // idle | loading | ready | error
  pipelineAnalyticsDetailErrorState: "",
  pipelineAgeSortKey: null,
  pipelineAgeSortDir: 1,
  closeTimeSortKey: null,
  closeTimeSortDir: 1,
  stagnantSortKey: null,
  stagnantSortDir: 1,
  // Owner corrective (ver HANDOFF — tabla minimalista con headers reales):
  // Tiempo por etapa/Conversión etapa a etapa pasaron de ranking `.bar-row`
  // sin sort a tabla ordenable, MISMO criterio que el resto de la familia.
  stageTimeSortKey: null,
  stageTimeSortDir: 1,
  stageConversionSortKey: null,
  stageConversionSortDir: 1,
  leadConversionDetail: null,
  leadConversionDetailStatus: "idle",
  leadConversionDetailErrorState: "",
  leadConversionSortKey: null,
  leadConversionSortDir: 1,
  // Atribución UTM (owner corrective post-trust-audit, ver HANDOFF §4/§5):
  // CINCO facetas locales del legacy, MISMA composición AND que
  // `_filterLeadConvTable`, + "Resultado" (owner follow-up mid-smoke, ver
  // HANDOFF) como sexta faceta — fallback pedido por el propio owner ante
  // la falta de relación 1:1 limpia entre mini-KPI y tabla.
  leadConversionCampanaFilter: "",
  leadConversionMedioFilter: "",
  leadConversionOrigenFilter: "",
  leadConversionSellerFilter: "",
  leadConversionOutcomeFilter: "",
  leadConversionSearchText: "",
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

// Tile clickeable de Actividades (owner follow-up, ver HANDOFF): "Retrasadas"/
// "Por vencer" arriba ya viven SIEMPRE visibles como `<details open>` más
// abajo en el mismo panel (`commercialStaticSection`) — a diferencia de
// `commercialRichKpi`/`data-event`, acá no hay nada que abrir/cerrar, sólo
// llevar la vista a la sección que ya está ahí. Reusa el mismo primitivo
// VISUAL `.activity-kpi--clickable` (cursor/hover/focus, tools.css) pero con
// su propio atributo (`data-commercial-act-jump`) y su propio delegado —
// enganchar esto al motor `[data-event]` sería forzar una sección
// hidden/toggle que acá no existe.
function commercialActClickableKpi(label, value, sectionId) {
  return `<div class="activity-kpi activity-kpi--clickable" data-commercial-act-jump="${commercialEsc(sectionId)}"`
    + ` role="button" tabindex="0" aria-label="Ir a ${commercialEsc(label)}">`
    + `<span class="activity-kpi__value">${commercialEsc(value)}</span>`
    + `<span class="activity-kpi__label">${commercialEsc(label)}</span></div>`;
}

// Scroll a una `<details data-section-id>` YA renderizada (nunca oculta) —
// fuerza `open` primero por si el usuario la había colapsado a mano (MISMO
// criterio que `commercialMaybeScrollToOpenedAnalysis`, sin su maquinaria de
// toggle/`.is-active`, que acá no aplica). Busca sobre `root`, nunca
// `document` — MISMO contrato que el resto de los hooks de este archivo.
function commercialActJumpToSection(sectionId, root) {
  const section = root.querySelector(`[data-section-id="${sectionId}"]`);
  if (!section) return;
  section.open = true;
  section.scrollIntoView({ block: "start" });
}

// Tile del landing con la riqueza informativa del CRM legacy: valor
// principal, una magnitud secundaria opcional (horas) y la composición por
// segmento. Sigue siendo el `.activity-kpi` COMPARTIDO — sólo suma líneas.
// `eventView` (C6b, ver HANDOFF): cuando VARIOS tiles comparten un mismo
// `data-event` —o sea abren el mismo análisis— este atributo declara QUÉ
// lectura representa cada uno ("total" | "ponderado"). Es una propiedad
// ESTÁTICA del tile (lo que el tile significa), no estado de runtime: el
// hecho dinámico —cuál está activo— lo sigue diciendo `.is-active` en el DOM.
// `hostAttr` (owner smoke — "Creadas esta semana" se achicó, ver HANDOFF):
// hook de re-render QUIRÚRGICO opcional, escrito DIRECTO en el `.activity-kpi`
// mismo — NUNCA un `<div>` wrapper alrededor. `.activity-kpi-grid` es CSS
// Grid con `align-items: stretch` implícito: el grid ESTIRA a su hijo
// DIRECTO a la altura de la fila. Un wrapper sin estilo propio recibe ESE
// estiramiento (invisible, sin bordes), pero el `.activity-kpi` real —
// ahora un NIETO, no un hijo — vuelve a su alto de contenido, más chico
// que sus hermanos con más líneas de texto. De ahí el tile visualmente
// "achicado" pese a que el layout seguía siendo correcto en los demás.
function commercialRichKpi(label, value, { sub = "", split = null, eventKey = "", eventView = "", hostAttr = "" } = {}) {
  const clickable = eventKey ? ` activity-kpi--clickable" data-event="${eventKey}"`
    + (eventView ? ` data-commercial-event-view="${eventView}"` : "")
    + ` role="button" tabindex="0"`
    + ` aria-label="Ver análisis de ${commercialEsc(label)}"` : '"';
  return `<div class="activity-kpi${clickable}${hostAttr ? ` ${hostAttr}` : ""}>`
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
        <thead><tr><th class="left">Mes</th><th>Opps.</th><th>${commercialEsc(metric.column)}</th><th>% del total</th></tr></thead>
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

// ── Creadas — drill mensual + ranking por vendedor (Creadas Legacy Parity,
// ver HANDOFF — paridad con el legacy `#created-drill-section`/
// `/crm/opp-by-month`) ──────────────────────────────────────────────────────
// Año por default: el más RECIENTE con datos (MISMO criterio que el legacy:
// el año actual si tiene oportunidades, si no el más reciente disponible —
// nunca el año calendario a secas, que podría no tener ningún dato).
// Años del `<select>`: los SELECCIONABLES del descubrimiento, NO los que están
// hidratados (owner: "YEAR OPTION != YEAR HYDRATED"). Antes se derivaban de
// `months`, o sea del payload histórico completo — por eso el drill tenía que
// traer toda la historia para poder ofrecer 2023.
//
// Fallback a los años del payload mientras el descubrimiento no resolvió: así
// el `<select>` nunca queda vacío, y el año activo siempre es ofrecible.
function commercialCreadasYears(months) {
  const descubiertos = commercialState.creadasSelectableYears || [];
  if (descubiertos.length) return descubiertos;
  const years = new Set();
  (months || []).forEach(m => years.add(m.month.slice(0, 4)));
  if (commercialState.creadasYear) years.add(String(commercialState.creadasYear));
  return [...years].sort().reverse();
}
function commercialCreadasDefaultYear(months) {
  const years = commercialCreadasYears(months);
  if (!years.length) return null;
  const current = String(new Date().getFullYear());
  return years.includes(current) ? current : years[0];
}
function commercialCreadasEffectiveYear(months) {
  return commercialState.creadasYear || commercialCreadasDefaultYear(months);
}

// Semana DENTRO del mes — MISMA fórmula que el legacy (ceil(día/7)), NUNCA
// la semana Monday-anchored que usa el KPI semanal: son DOS conceptos de
// "semana" DISTINTOS a propósito (owner directive — "do not correct or
// unify the two concepts in this increment").
function commercialCreadasWeekOfMonth(entryDate) {
  return Math.ceil(Number(entryDate.slice(8, 10)) / 7);
}

// Color del badge de estado. Se compara por RAIZ y sin distinguir
// mayusculas/acentos, no por igualdad exacta.
//
// El motivo es un problema de DATOS en Odoo, no de presentacion: el mismo
// desenlace llega escrito de dos formas ("Perdida" y "Perdido" conviven en el
// mismo mes), y hay estados que nunca tuvieron badge ("Suspendido"). Con la
// comparacion exacta —que es la que tenia el legacy, `status === "Perdida"`—
// una de las dos variantes se renderizaba como texto plano al lado de la otra
// con badge rojo, en la misma columna. No es una regresion del Hub: es un
// defecto heredado que los datos reales expusieron.
//
// La normalizacion es SOLO para elegir el color. El texto se muestra tal como
// viene de Odoo: corregirlo en el cliente escondería la inconsistencia de la
// fuente, que es un problema real y de negocio (ver el reporte del increment).
//
// Las etapas numeradas del pipeline ("1 - Inicial", "6 - Armando Propuestas
// Formales") NO llevan badge a proposito: un badge marca un DESENLACE, y una
// etapa intermedia no lo es. Van como texto plano, igual que antes.
function commercialCreadasStatusColor(status) {
  const s = (status || "").toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "").trim();
  if (s.startsWith("ganad")) return "green";      // Ganada / Ganado
  if (s.startsWith("perdid")) return "red";       // Perdida / Perdido
  if (s.startsWith("suspendid")) return "grey";   // Suspendida / Suspendido
  return null;                                    // etapas del pipeline: sin badge
}

const COMMERCIAL_MONTH_NAMES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
function commercialCreadasMonthLabel(month) {
  const [y, m] = month.split("-").map(Number);
  return `${COMMERCIAL_MONTH_NAMES[m - 1]} de ${y}`;
}

// ── FILTRADO LOCAL ────────────────────────────────────────────────────────
// Año, Vendedor y Buscar se resuelven SOBRE EL PAYLOAD YA CARGADO. Ninguno
// dispara un fetch: el universo de negocio lo cambia sólo Tipo (Todos/NN/BI),
// que sigue con su contrato de dataset intacto.
//
// La búsqueda usa `toLowerCase()` a secas — MISMO criterio que
// `commercialOppMatchesSearch`/`commercialActivityMatchesSearch`, las dos
// búsquedas que ya existen en la vertical. Un normalizado de acentos acá y no
// allá sería una tercera semántica de búsqueda.
function commercialCreadasMatchesSearch(opp, query) {
  if (!query) return true;
  return [opp.name, opp.client, opp.seller, opp.origen, opp.status]
    .filter(Boolean).join(" ").toLowerCase().includes(query);
}

// Vendedores DERIVADOS del payload ya cargado — MISMO criterio que
// `Responsable` en Actividades: no hay endpoint de usuarios y no hace falta.
// Se derivan del AÑO activo, así que el select nunca ofrece un vendedor que no
// puede producir ni una fila.
function commercialCreadasSellers(months, year) {
  const sellers = new Set();
  (months || []).filter(m => m.month.startsWith(year))
    .forEach(m => (m.opportunities || []).forEach(o => sellers.add(o.seller || "Sin vendedor")));
  return [...sellers].sort((a, b) => a.localeCompare(b, "es"));
}

// El universo filtrado: los meses del año con sus oportunidades pasadas por
// Vendedor + Buscar, y el `count` RECALCULADO sobre lo que queda. Un mes que
// se queda sin oportunidades desaparece — mostrarlo con 0 sería ruido.
//
// Es la ÚNICA fuente del ranking, la tabla y el detalle: si cada uno filtrara
// por su cuenta podrían discrepar, que es exactamente el tipo de
// inconsistencia que un drill no puede permitirse.
function commercialCreadasFiltered(months, year) {
  const seller = commercialState.creadasSeller;
  const query = (commercialState.creadasSearch || "").trim().toLowerCase();
  return (months || [])
    .filter(m => m.month.startsWith(year))
    .map(m => {
      const opps = (m.opportunities || []).filter(o =>
        (!seller || (o.seller || "Sin vendedor") === seller)
        && commercialCreadasMatchesSearch(o, query));
      return { month: m.month, count: opps.length, opportunities: opps };
    })
    .filter(m => m.count > 0)
    .sort((a, b) => b.month.localeCompare(a.month));
}

function commercialCreadasHasLocalFilters() {
  return !!(commercialState.creadasSeller || (commercialState.creadasSearch || "").trim());
}

// Horas de un conjunto de oportunidades. `horas` puede ser `null` ("sin horas
// cargadas", que el backend distingue a propósito de 0): se suma como 0 pero
// el "—" de la celda lo decide `commercialCreadasHoursCell`.
function commercialCreadasHours(opps) {
  return (opps || []).reduce((sum, o) => sum + (o.horas || 0), 0);
}
// Celda de Horas — MISMO criterio que el legacy: con oportunidades pero sin
// horas cargadas muestra "—"; sin oportunidades, "0".
function commercialCreadasHoursCell(opps) {
  const hs = commercialCreadasHours(opps);
  if (hs) return commercialFmtInt(hs);
  return (opps || []).length ? "—" : "0";
}

// ── BARRA DE FILTROS ──────────────────────────────────────────────────────
// Año / Vendedor / Buscar / Limpiar en UNA fila, con los MISMOS primitivos que
// la barra de Actividades y la de Activas (`.whatsnew-filters` +
// `--fill` + `.whatsnew-filter__clear`) — un solo lenguaje de filtros en toda
// la vertical, cero clase nueva. "Buscar" es `--fill` y se come el espacio del
// medio, así que Limpiar queda pegado a la derecha sin posicionamiento propio.
function commercialCreadasControlsHtml(months) {
  const years = commercialCreadasYears(months);
  const year = commercialCreadasEffectiveYear(months);
  const sellers = commercialCreadasSellers(months, year);
  return `<div class="whatsnew-filters" data-commercial-creadas-filters>
      <label class="whatsnew-filter">
        <span>Año</span>
        <select data-commercial-creadas-year>
          ${years.map(y => `<option value="${y}"${y === year ? " selected" : ""}>${y}</option>`).join("")}
        </select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-creadas-seller>
          <option value=""${commercialState.creadasSeller === "" ? " selected" : ""}>Todos</option>
          ${sellers.map(v => `<option value="${commercialEsc(v)}"${commercialState.creadasSeller === v ? " selected" : ""}>${commercialEsc(v)}</option>`).join("")}
        </select>
      </label>
      <label class="whatsnew-filter whatsnew-filter--fill">
        <span>Buscar</span>
        <input type="search" data-commercial-creadas-search aria-label="Buscar oportunidades creadas"
               placeholder="Buscar…" value="${commercialEsc(commercialState.creadasSearch)}">
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-creadas-clear>✕ Limpiar</button>
    </div>`;
}

// ── RANKING POR VENDEDOR — el ÚNICO gráfico del análisis ──────────────────
// Reusa `.bar-row` (tools.css), el primitivo ya promovido Hub-wide (ver
// test_bar_primitive_is_promoted_hub_wide...), MISMO que usa Reporting/
// Rentabilidad para su propio ranking. Descendente, y responde a los filtros
// activos como todo lo demás: si Vendedor filtra a uno solo, muestra uno solo
// —mantener las barras de los demás obligaría a una regla de filtrado
// especial para el ranking, y dos semánticas de filtro en la misma pantalla.
function commercialCreadasSellerRankingHtml(filtered) {
  const opps = (filtered || []).flatMap(m => m.opportunities || []);
  const counts = {};
  opps.forEach(o => { const v = o.seller || "Sin vendedor"; counts[v] = (counts[v] || 0) + 1; });
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!rows.length) {
    return commercialEmptyStateHtml("Sin resultados",
      commercialCreadasHasLocalFilters()
        ? "Ningún vendedor tiene oportunidades creadas con estos filtros."
        : "Ningún vendedor tiene oportunidades creadas en este año.");
  }
  const max = rows[0][1];
  // CLICK PARA FILTRAR EN CASCADA — MISMO contrato que Métricas de Uso
  // (`admin_activity_content.html` + `initModuleFilter` en app.js): la FILA
  // ENTERA es el target (etiqueta, barra y número), `role="button"` +
  // `tabindex="0"` para que Enter/Space funcionen, `aria-label` que dice qué
  // hace, y `.is-active` para el seleccionado. El CSS es el bloque compartido
  // de tools.css — cero clase nueva, cero copia de la lógica.
  //
  // Un `<button>` nativo sería lo ideal, pero `.bar-row` es un contenedor flex
  // de tres hijos y envolverlo rompería su layout; `role`+`tabindex`+`keydown`
  // es exactamente lo que Métricas de Uso ya hace por la misma razón.
  //
  // SIN toggle-to-clear: clickear el vendedor ya seleccionado lo mantiene.
  // Es la convención de `initModuleFilter`, donde limpiar es una acción
  // explícita y separada — acá, el dropdown en "Todos" o el botón Limpiar.
  const activo = commercialState.creadasSeller;
  return rows.map(([seller, count]) => {
    const sel = seller === activo;
    return `
    <div class="bar-row bar-row--clickable${sel ? " is-active" : ""}"
      data-commercial-creadas-seller-pick="${commercialEsc(seller)}"
      role="button" tabindex="0" aria-pressed="${sel ? "true" : "false"}"
      aria-label="Filtrar las oportunidades creadas por ${commercialEsc(seller)}">
      <span class="bar-label">${commercialEsc(seller)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(count / max * 100).toFixed(1)}%"></div></div>
      <span class="bar-value">${commercialFmtInt(count)}</span>
    </div>`;
  }).join("");
}

// ── LA ETIQUETA DE SEMANA — RECUPERADA DEL LEGACY, TEXTUAL ────────────────
// `frontend/js/dashboard.js::renderCreatedDrillTable` (legacy):
//
//     const dayStart = (wk - 1) * 7 + 1;
//     const dayEnd   = Math.min(wk * 7, daysInMonth);
//     const padZ     = n => String(n).padStart(2, "0");
//     const label    = `Sem. ${wk} (${padZ(dayStart)}/${padZ(m)}–${padZ(dayEnd)}/${padZ(m)})`;
//
// O sea: `Sem. 2 (08/07–14/07)`. El separador es EN DASH (–, U+2013), no un
// guión ASCII. El último bucket se recorta a `daysInMonth`, así que en un
// febrero de 28 días la semana 4 es `(22/02–28/02)` y no `(22/02–28/02)` por
// casualidad — se recorta de verdad en meses de 30/31.
//
// Las HORAS van en su propia COLUMNA, no dentro del label: el legacy las tenía
// así y meterlas en el texto haría la primera columna mucho más ancha sin
// ganar nada.
//
// El bucket sigue siendo `ceil(día/7)` — NO la semana Monday-anchored del KPI
// semanal. Son dos conceptos distintos a propósito.
function commercialCreadasWeekLabel(month, week) {
  const [, m] = month.split("-").map(Number);
  const daysInMonth = new Date(Number(month.slice(0, 4)), m, 0).getDate();
  const padZ = n => String(n).padStart(2, "0");
  const dayStart = (week - 1) * 7 + 1;
  const dayEnd = Math.min(week * 7, daysInMonth);
  return `Sem. ${week} (${padZ(dayStart)}/${padZ(m)}–${padZ(dayEnd)}/${padZ(m)})`;
}

// Cuántos buckets tiene un mes — `ceil(díasDelMes / 7)`, MISMO criterio que el
// legacy, que renderizaba TODAS las semanas del mes (las vacías atenuadas y no
// clickeables) para que el mes se leyera completo.
function commercialCreadasWeekCount(month) {
  const [y, m] = month.split("-").map(Number);
  return Math.ceil(new Date(y, m, 0).getDate() / 7);
}

// ── UNA SOLA TABLA JERÁRQUICA: MES → SEMANA ───────────────────────────────
// Antes eran dos tablas desconectadas (la mensual acá y la semanal apareciendo
// en otro lugar). Ahora las semanas son FILAS HIJAS de su mes, dentro de la
// misma tabla y el mismo `<tbody>`.
//
// ALINEACIÓN: la primera columna es TEXTO y va a la izquierda; Opps. y Horas
// son números y van a la derecha, que es el DEFAULT de `table.dashboard-table`
// (th y td) — o sea cero clase para las numéricas. Para las de TEXTO el
// primitivo es `.left`, NO `.row-label-col`: esa última sólo tiene regla para
// `<th>`, así que un `<td class="row-label-col">` cae en el `text-align:right`
// genérico. Ese es exactamente el desalineado que se ve hoy —header a la
// izquierda, celda a la derecha—. Nada
// centrado — un mes centrado no se puede escanear en una columna.
//
// DOS ACCIONES SEPARADAS en la fila de mes, que antes eran un solo click
// ambiguo:
//   * el CHEVRON (botón propio) expande/colapsa las semanas;
//   * el RESTO DE LA FILA selecciona el mes y abre su detalle.
// El chevron corta la propagación, así que expandir no toca el detalle.
function commercialCreadasWeekRowsHtml(bucket) {
  const byWeek = {};
  (bucket.opportunities || []).forEach(o => {
    const w = commercialCreadasWeekOfMonth(o.entry_date);
    (byWeek[w] || (byWeek[w] = [])).push(o);
  });
  const total = commercialCreadasWeekCount(bucket.month);
  // DENOMINADOR DE LA SEMANA: las HORAS DE SU MES PADRE, no la semana más
  // grande. La barra de una semana responde "¿qué parte del mes es?", así que
  // las cinco semanas de un mes suman 100% y se leen como composición.
  //
  // Antes se normalizaba contra `weekMax` (la semana con MÁS oportunidades),
  // heredado del legacy: la semana más grande daba 100% SIEMPRE, sin importar
  // qué fracción del mes fuera, y el resto se escalaba contra ella. Dos
  // semanas de 80 h y 420 h con una oportunidad cada una salían IDÉNTICAS.
  const parentHours = commercialCreadasHours(bucket.opportunities);
  const sel = commercialState.creadasSelection;
  const rows = [];
  for (let w = 1; w <= total; w += 1) {
    const opps = byWeek[w] || [];
    const has = opps.length > 0;
    // Sin horas en el mes no hay proporción que mostrar (y dividir por cero
    // daría NaN): la barra queda en 0 en vez de fabricarse una.
    const width = parentHours > 0
      ? (commercialCreadasHours(opps) / parentHours * 100).toFixed(1) : "0.0";
    const selected = !!(sel && sel.month === bucket.month && sel.week === w);
    // Sin oportunidades: se muestra igual (el mes se lee completo) pero
    // atenuada y NO seleccionable — abrir un detalle vacío no informa nada.
    const attrs = has
      ? ` data-commercial-creadas-week="${commercialEsc(bucket.month)}:${w}" tabindex="0" role="button"`
      : ' aria-disabled="true"';
    const row = `<tr class="commercial-creadas-week${has ? "" : " is-empty"}${selected ? " is-selected" : ""}"${attrs}>
      <td class="left commercial-creadas-week-label">${commercialEsc(commercialCreadasWeekLabel(bucket.month, w))}</td>
      <td>${commercialFmtInt(opps.length)}</td>
      <td>${commercialCreadasHoursCell(opps)}</td>
      <td class="commercial-creadas-dist"><span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span></td>
    </tr>`;
    rows.push(row);
  }
  return rows.join("");
}

function commercialCreadasMonthTableHtml(filtered) {
  if (!filtered.length) {
    return commercialEmptyStateHtml("Sin resultados",
      commercialCreadasHasLocalFilters()
        ? "Ninguna oportunidad coincide con estos filtros."
        : "Ninguna oportunidad entró al pipeline en este año.");
  }
  // DENOMINADOR DEL MES: el MÁXIMO de HORAS entre los meses actualmente
  // VISIBLES. Se recalcula sobre `filtered`, así que cambiar Año, Vendedor o
  // Buscar reescala la comparación — que es lo correcto: la pregunta es "¿cómo
  // se comparan los meses que estoy mirando?", no "¿cómo se comparaban antes
  // de filtrar?".
  //
  // Antes el denominador era el máximo de `count` (cantidad de oportunidades),
  // heredado del legacy, mientras la columna de al lado muestra HORAS: un mes
  // de 4.040 h con 10 oportunidades salía más corto que uno de 500 h con 15.
  const monthHours = filtered.map(m => commercialCreadasHours(m.opportunities));
  const max = Math.max(...monthHours, 1);
  const sel = commercialState.creadasSelection;
  const body = filtered.map((m, i) => {
    const width = (monthHours[i] / max * 100).toFixed(1);
    const expanded = commercialState.creadasExpandedMonths.indexOf(m.month) !== -1;
    const selected = !!(sel && sel.month === m.month && sel.week === null);
    const monthRow = `<tr class="commercial-creadas-month${selected ? " is-selected" : ""}"
        data-commercial-creadas-month="${commercialEsc(m.month)}">
      <td class="left">
        <button type="button" class="dashboard-table-toggle"
          data-commercial-creadas-toggle="${commercialEsc(m.month)}"
          aria-expanded="${expanded ? "true" : "false"}"
          aria-label="${expanded ? "Colapsar" : "Expandir"} las semanas de ${commercialEsc(commercialCreadasMonthLabel(m.month))}"><span class="caret">${expanded ? "▾" : "▸"}</span></button>
        <span class="commercial-creadas-month-label">${commercialEsc(commercialCreadasMonthLabel(m.month))}</span>
      </td>
      <td>${commercialFmtInt(m.count)}</td>
      <td>${commercialCreadasHoursCell(m.opportunities)}</td>
      <td class="commercial-creadas-dist"><span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span></td>
    </tr>`;
    return expanded ? monthRow + commercialCreadasWeekRowsHtml(m) : monthRow;
  }).join("");
  return `<div class="matrix-scroll forecast-months">
      <table class="dashboard-table commercial-creadas-table">
        <thead><tr>
          <th class="row-label-col">Mes / Semana</th>
          <th>Opps.</th>
          <th>Horas</th>
          <th class="left">Distribución</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

// Claves ordenables del detalle. `entry_date` se ordena por el ISO crudo (no
// por la fecha formateada dd/mm/aaaa, que ordenaria alfabeticamente y pondria
// el 01/12 antes del 02/01). El resto, texto normalizado a minusculas para que
// el orden no dependa de mayusculas.
const COMMERCIAL_CREADAS_SORT_KEYS = {
  entry_date: o => o.entry_date || "",
  name: o => (o.name || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  origen: o => (o.origen || "").toLowerCase(),
  status: o => (o.status || "").toLowerCase(),
};

// Orden client-side puro sobre el detalle ya filtrado. Sin clave activa
// devuelve el orden CANONICO del legacy (fecha de entrada ascendente): el
// ordenamiento es una capa encima, nunca reemplaza el default probado.
// Copia el array antes de ordenar — `.sort` muta.
function commercialCreadasSorted(opps) {
  const keyFn = commercialState.creadasSortKey
    && COMMERCIAL_CREADAS_SORT_KEYS[commercialState.creadasSortKey];
  const base = [...opps].sort((a, b) => a.entry_date.localeCompare(b.entry_date));
  if (!keyFn) return base;
  const dir = commercialState.creadasSortDir;
  return base.sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

// Cabecera ordenable — MISMO primitivo y MISMO contrato que Actividades y
// Oportunidades (`.dashboard-table-th--sortable`, tools.css): comportamiento
// transversal del Hub, no un mecanismo propio de esta tabla.
function commercialCreadasSortTh(key, label, cls) {
  const active = commercialState.creadasSortKey === key;
  const arrow = active ? (commercialState.creadasSortDir === 1 ? " ▲" : " ▼") : "";
  const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
  return `<th class="${classes}" data-commercial-creadas-sort="${key}"`
    + ` title="Ordenar por ${commercialEsc(label)}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
}

// Cambiar de vendedor — UNA sola función para las TRES puertas (el dropdown,
// el click en el ranking y Limpiar). Sin esto, tres caminos que hacen "lo
// mismo" divergen en cuanto uno agregue un paso.
//
// Lo que hace con el DETALLE ABIERTO es el punto delicado: si el mes/semana
// seleccionado sigue teniendo oportunidades bajo el filtro nuevo, el detalle se
// re-renderiza a ese subconjunto; si se queda vacío, se cierra SOLO el detalle.
// Los meses expandidos NO se tocan — colapsarlos sería castigar al usuario por
// filtrar.
function commercialCreadasSetSeller(seller) {
  commercialState.creadasSeller = seller || "";
  const sel = commercialState.creadasSelection;
  if (sel) {
    const months = (commercialState.creadas && commercialState.creadas.months) || [];
    const year = commercialCreadasEffectiveYear(months);
    const bucket = commercialCreadasFiltered(months, year).find(m => m.month === sel.month);
    const quedan = bucket && (sel.week === null
      ? bucket.opportunities.length
      : bucket.opportunities.filter(o => commercialCreadasWeekOfMonth(o.entry_date) === sel.week).length);
    if (!quedan) commercialState.creadasSelection = null;
  }
  commercialRenderCreadasPanel();
}

// Etiqueta de la selección activa, o "" si no hay ninguna. Deriva del MISMO
// estado que el detalle, así que no pueden decir cosas distintas.
function commercialCreadasSelectionLabel() {
  const sel = commercialState.creadasSelection;
  if (!sel) return "";
  const mes = commercialCreadasMonthLabel(sel.month);
  return sel.week === null ? mes : `${commercialCreadasWeekLabel(sel.month, sel.week)} · ${mes}`;
}

// ── CONTEXTO DE FILTRO ACTIVO ────────────────────────────────────────────
// El affordance de "qué estoy filtrando ahora, y cómo lo saco" del Hub. El
// primitivo (`.dashboard-subhead__filter` + `.dashboard-subhead__reset`,
// tools.css) ya existía y lo estrenó Métricas de Uso
// (`admin_activity_content.html`): "· Filtrado por <strong>X</strong> ✕", en
// dorado, con el ✕ (U+2715) como acción de descarte y un `title` que dice a
// dónde vuelve. Cero clase nueva, cero chip inventado para Comercial.
//
// Diferencia con Métricas de Uso, deliberada: allá el markup es estático y JS
// lo muestra/oculta con `hidden` porque hay UNA sola dimensión de filtro. Acá
// el panel se re-renderiza entero, así que se emite sólo cuando aplica — y se
// nombra la DIMENSIÓN ("Vendedor:", "Mes:") porque hay dos y sin el nombre no
// se distinguen entre sí.
function commercialCreadasFilterContextHtml(dimension, valor, hook, volverA) {
  if (!valor) return "";
  return ` <span class="dashboard-subhead__filter">· Filtrado por ${commercialEsc(dimension)}: `
    + `<strong>${commercialEsc(valor)}</strong> `
    + `<button type="button" class="dashboard-subhead__reset" ${hook}`
    + ` title="${commercialEsc(volverA)}">✕</button></span>`;
}

// ── DETALLE — CONTEXTUAL Y DESCARTABLE ────────────────────────────────────
// Sólo existe si hay una selección explícita. Antes se derivaba del mes
// expandido, así que aparecía sin pedirlo y no se podía cerrar sin colapsar el
// mes.
//
// El × cierra SÓLO el detalle: no toca año, vendedor, búsqueda, meses
// expandidos ni el dataset, y no dispara ningún fetch.
//
// Columnas y orden: los MISMOS 6 del legacy (Fecha entrada / Oportunidad /
// Cliente / Vendedor / Origen / Estado), ascendente por fecha de entrada.
function commercialCreadasDetailHtml(filtered) {
  const sel = commercialState.creadasSelection;
  if (!sel) return "";
  const bucket = (filtered || []).find(m => m.month === sel.month);
  // La selección puede quedar fuera del universo filtrado (se cambió el año o
  // se filtró un vendedor que no tiene ese mes): no se renderiza detalle
  // huérfano, y tampoco se pisa la selección — si el filtro se revierte,
  // vuelve.
  if (!bucket) return "";
  let opps = bucket.opportunities || [];
  let scope = commercialCreadasMonthLabel(sel.month);
  if (sel.week !== null) {
    opps = opps.filter(o => commercialCreadasWeekOfMonth(o.entry_date) === sel.week);
    scope = `${commercialCreadasWeekLabel(sel.month, sel.week)} · ${scope}`;
  }
  opps = commercialCreadasSorted(opps);
  const rows = opps.map(o => {
    const color = commercialCreadasStatusColor(o.status);
    const statusHtml = color
      ? `<span class="dashboard-status-badge dashboard-status-badge--${color}">${commercialEsc(o.status)}</span>`
      : commercialEsc(o.status);
    return `<tr>
      <td class="left">${commercialEsc(commercialFmtDate(o.entry_date))}</td>
      <td class="left">${commercialEsc(o.name)}</td>
      <td class="left">${commercialEsc(o.client) || "—"}</td>
      <td class="left">${commercialEsc(o.seller) || "—"}</td>
      <td class="left">${commercialEsc(o.origen) || "—"}</td>
      <td class="left">${statusHtml}</td>
    </tr>`;
  }).join("");
  const body = rows || `<tr><td colspan="6">${commercialEsc("Sin oportunidades para esta selección.")}</td></tr>`;
  return `<div data-commercial-creadas-detail style="margin-top:16px">
      <div class="commercial-creadas-detail-head">
        <p class="perspective-label">Detalle — ${commercialFmtInt(opps.length)} oportunidad(es)`
    + `${commercialCreadasFilterContextHtml(sel.week === null ? "Mes" : "Semana", scope,
      "data-commercial-creadas-detail-close", "Cerrar el detalle")}</p>
      </div>
      <div class="matrix-scroll">
        <table class="dashboard-table commercial-creadas-detail-table">
          <thead><tr>
            ${commercialCreadasSortTh("entry_date", "Fecha entrada", "left")}
            ${commercialCreadasSortTh("name", "Oportunidad", "row-label-col")}
            ${commercialCreadasSortTh("client", "Cliente", "left")}
            ${commercialCreadasSortTh("seller", "Vendedor", "left")}
            ${commercialCreadasSortTh("origen", "Origen", "left")}
            ${commercialCreadasSortTh("status", "Estado", "left")}
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </div>`;
}

// Conteo del header — "N oportunidad(es) · <año>", MISMO criterio que
// `commercialForecastCountLabel` (vacío mientras no hay payload, el `<span>`
// se emite igual para que el re-render quirúrgico siempre encuentre su host).
// Cuenta sobre el universo FILTRADO: si el header dijera el total del año
// mientras la tabla muestra un vendedor, los dos números se contradirían.
function commercialCreadasCountLabel() {
  if (commercialState.creadasStatus !== "ready") return "";
  const months = (commercialState.creadas && commercialState.creadas.months) || [];
  if (!months.length) return "";
  const year = commercialCreadasEffectiveYear(months);
  const total = commercialCreadasFiltered(months, year).reduce((sum, m) => sum + m.count, 0);
  return `${commercialFmtInt(total)} oportunidad(es) · ${year}`;
}

// Cuerpo del panel — cubre los CUATRO estados del lazy load, MISMO criterio
// que `commercialForecastPanelBodyHtml`.
//
// JERARQUÍA (owner UX decision): filtros → ranking → UNA tabla Mes/Semana →
// detalle opcional. En VERTICAL, no lado a lado: ranking y tabla apilados eran
// dos capas visualmente desconectadas de la misma pregunta.
function commercialCreadasPanelBodyHtml() {
  if (commercialState.creadasStatus === "error") {
    const configIncomplete = commercialState.creadasErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar las oportunidades creadas desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("creadas", "Reintentar la carga de oportunidades creadas"));
  }
  if (commercialState.creadasStatus !== "ready") {
    return commercialLoadingHtml("Cargando oportunidades creadas…");
  }
  const months = (commercialState.creadas && commercialState.creadas.months) || [];
  if (!months.length) {
    return commercialEmptyStateHtml("Sin datos",
      "No hay oportunidades con fecha de entrada al pipeline para este segmento.");
  }
  const year = commercialCreadasEffectiveYear(months);
  const filtered = commercialCreadasFiltered(months, year);
  return commercialCreadasControlsHtml(months)
    + `<div class="commercial-creadas-analysis">
        <div>
          <h3 class="perspective-label">Ranking por vendedor</h3>
          ${commercialCreadasSellerRankingHtml(filtered)}
        </div>
        <div>
          <h3 class="perspective-label">Por mes y semana${commercialCreadasFilterContextHtml("Vendedor", commercialState.creadasSeller, "data-commercial-creadas-seller-clear", "Volver a todos los vendedores")}</h3>
          ${commercialCreadasMonthTableHtml(filtered)}
        </div>
      </div>`
    + commercialCreadasDetailHtml(filtered);
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
// Primitivo GENÉRICO (reusa `.urgency-legend`, ya compartido) — recibe sus
// propios tramos [color, label], nunca hardcodea una escala. Owner
// corrective (ver HANDOFF — faltaban las referencias de color de Edad del
// pipeline/Estancadas): esas dos escalas tienen umbrales DISTINTOS a los de
// Actividades/Suspendidas/Prospectos (ver `commercialAgeColor`/
// `commercialStagnantColor`), así que necesitan su PROPIA leyenda, no la de
// urgencia — mismo primitivo visual, tramos propios.
function commercialDaysLegendHtml(tiers) {
  return `<div class="urgency-legend">${tiers.map(([color, label]) =>
    `<b><i class="urgency-legend__dot urgency-legend__dot--${color}"></i>${label}</b>`).join("")}</div>`;
}
function commercialUrgencyLegendHtml() {
  return commercialDaysLegendHtml([["green", "0-7 días"], ["amber", "8-15 días"], ["orange", "16-30 días"], ["red", "+30 días"]]);
}
// Edad del pipeline — MISMOS umbrales que `commercialAgeColor`.
function commercialAgeLegendHtml() {
  return commercialDaysLegendHtml([["green", "0-30 días"], ["amber", "31-60 días"], ["orange", "61-90 días"], ["red", "+90 días"]]);
}
// Estancadas — MISMOS umbrales que `commercialStagnantColor`. SIN tramo
// verde: el universo entero ya es "más de 30 días sin movimiento" (filtro
// del propio backend), así que un tramo 0-30 acá sería inalcanzable.
function commercialStagnantLegendHtml() {
  return commercialDaysLegendHtml([["amber", "31-60 días"], ["orange", "61-90 días"], ["red", "+90 días"]]);
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

// ── Prospectos — detalle (Commercial Tile 5, ver HANDOFF) ──────────────────
// Tabla PARALELA a `commercialOpportunitiesTableHtml`, no una extensión de
// esa función: Prospectos necesita una columna que Activas decidió NO
// mostrar (Etiquetas — ver `test_commercial_opportunities_table_never_
// renders_an_etiquetas_column`) y vive en su propio host/estado/lifecycle
// (lazy, filtros propios, sin Etapa/Origen/Funnel — universo de una sola
// etapa). Ramificar la función de Activas con un flag hubiera acoplado dos
// contratos que el propio backend ya mantiene separados
// (`_build_opportunities` vs `_build_prospects`). Reusa todo lo que SÍ es
// genuinamente compartido: formatters (`commercialFmtInt`/`Percent`/`Esc`),
// las estrellas de Prioridad (`COMMERCIAL_PRIORITY_STARS/LABELS`), la
// urgencia de Próx. Acción (`commercialUrgencyColor`/`commercialDaysUntil`),
// la leyenda (`commercialUrgencyLegendHtml`), el estado vacío
// (`commercialEmptyStateHtml`), el badge compartido
// (`.dashboard-status-badge`, tools.css — reusado en gris neutro para
// Etiquetas, sin inventar un primitivo de "tag pill" propio) y el spine de
// tabla (`.matrix-scroll`/`.dashboard-table`, tools.css) — cero CSS propio
// de Prospectos.
const COMMERCIAL_PROSPECTS_SORT_KEYS = {
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

// Orden client-side puro sobre el dataset YA cargado — nunca un round-trip.
function commercialProspectsSorted(rows) {
  const keyFn = commercialState.prospectsSortKey && COMMERCIAL_PROSPECTS_SORT_KEYS[commercialState.prospectsSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.prospectsSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function commercialToggleProspectsSort(key) {
  if (commercialState.prospectsSortKey === key) {
    commercialState.prospectsSortDir *= -1;
  } else {
    commercialState.prospectsSortKey = key;
    commercialState.prospectsSortDir = 1;
  }
}

function commercialProspectsSellerOptions(rows) {
  return [...new Set(rows.map(o => o.seller).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}

// Búsqueda: MISMO alcance que `commercialOpportunitySearchableText` (nombre,
// cliente, vendedor, licencias, probabilidad, prioridad, próx. acción, últ.
// resp. — el contrato de búsqueda YA establecido para esta MISMA forma de
// fila) + Etiquetas, la única columna que Prospectos agrega sobre esa forma.
function commercialProspectSearchableText(o) {
  return [commercialOpportunitySearchableText(o), ...(o.tags || [])].join(" ").toLowerCase();
}

function commercialSearchProspects(rows, text) {
  const q = (text || "").trim().toLowerCase();
  if (!q) return rows;
  return rows.filter(o => commercialProspectSearchableText(o).includes(q));
}

function commercialProspectsTagsHtml(tags) {
  if (!tags || !tags.length) return "—";
  return tags.map(t => `<span class="dashboard-status-badge dashboard-status-badge--grey">${commercialEsc(t)}</span>`).join(" ");
}

function commercialProspectsTableHtml(prospects) {
  const rows = (prospects && prospects.opportunities) || [];
  if (!rows.length) {
    return commercialEmptyStateHtml("Sin resultados", "Sin prospectos para este filtro.");
  }
  const sorted = commercialProspectsSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.prospectsSortKey === key;
    const arrow = active ? (commercialState.prospectsSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-prospects-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => {
    const acts = o.activities || [];
    const nextAction = acts.length
      ? acts.map(act => {
          const days = commercialDaysUntil(act.deadline);
          const rel = days === null ? "" : days < 0 ? ` (venc. ${Math.abs(days)}d)` : days === 0 ? " (hoy)" : ` (${days}d)`;
          const datePrefix = commercialEsc(commercialFmtDate(act.deadline));
          if (!act.type) return datePrefix;
          const color = commercialUrgencyColor(days);
          const typeAndRel = `${commercialEsc(act.type)}${commercialEsc(rel)}`;
          const badge = color
            ? `<span class="dashboard-status-badge dashboard-status-badge--${color}">${typeAndRel}</span>`
            : typeAndRel;
          return `${datePrefix} · ${badge}`;
        }).join("<br>")
      : "—";
    const lic = o.licencias === "Si" ? "✓" : "";
    const priorityStars = commercialEsc(COMMERCIAL_PRIORITY_STARS[o.priority] || "—");
    const priorityLabel = COMMERCIAL_PRIORITY_LABELS[o.priority] || "Sin prioridad";
    return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialProspectsTagsHtml(o.tags)}</td>
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
  // Fila de totales — MISMO criterio que Activas: sólo Hs/Hs Pond. suman sin
  // ambigüedad, el resto queda en blanco en vez de una agregación inventada.
  const totals = rows.reduce((acc, o) => ({
    hours: acc.hours + (o.hours || 0),
    weighted_hours: acc.weighted_hours + (o.weighted_hours || 0),
  }), { hours: 0, weighted_hours: 0 });
  const totalRow = `
    <tr class="is-total">
      <td class="row-label" colspan="5">Total (${rows.length})</td>
      <td>${commercialFmtInt(totals.hours)}</td>
      <td>${commercialFmtInt(totals.weighted_hours)}</td>
      <td colspan="5"></td>
    </tr>`;
  // `<colgroup>` en % — MISMO criterio que Activas: ancho real por columna,
  // nunca un número inventado. Doce columnas (once de Activas + Etiquetas):
  // Oportunidad/Vendedor/Próx. Acción ceden unos puntos para darle a
  // Etiquetas un ancho legible sin forzar `.matrix-scroll` de más.
  return `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:18%"><col style="width:10%"><col style="width:9%">
          <col style="width:9%"><col style="width:4%"><col style="width:5%">
          <col style="width:6%"><col style="width:5%"><col style="width:4%">
          <col style="width:16%"><col style="width:7%"><col style="width:7%">
        </colgroup>
        <thead><tr>
          ${th("opportunity", "Oportunidad", "row-label-col")}
          ${th("client", "Cliente", "left")}
          ${th("seller", "Vendedor", "left")}
          <th class="left">Etiquetas</th>
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

function commercialProspectsBlockHtml(rows) {
  return `${rows.length ? commercialUrgencyLegendHtml() : ""}${commercialProspectsTableHtml({ opportunities: rows })}`;
}

// Vendedor + Buscar filtran (AND) sobre el dataset YA cargado — cero
// round-trip. Vendedor primero (mismo orden que la barra de filtros).
function commercialCurrentProspectsRows() {
  const all = (commercialState.prospects && commercialState.prospects.opportunities) || [];
  const bySeller = commercialState.prospectsSellerFilter
    ? all.filter(o => o.seller === commercialState.prospectsSellerFilter)
    : all;
  return commercialSearchProspects(bySeller, commercialState.prospectsSearchText);
}

// Barra de filtros LOCAL: Vendedor + Buscar + Limpiar — MISMO primitivo
// `.whatsnew-filters`/`.whatsnew-filter` que el resto de Comercial. Sin
// Etapa (universo de una sola etapa, no hay nada que particionar) y sin chip
// de filtro activo (owner §8: el `<select>`/`<input>` YA representan el
// estado activo — no hay cascada gráfico → filtro en este panel).
function commercialProspectsFiltersHtml(sellerOptions) {
  const sellerOpts = sellerOptions.map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.prospectsSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-prospects-filters>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-prospects-seller-filter>
          <option value=""${commercialState.prospectsSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <label class="whatsnew-filter whatsnew-filter--fill">
        <span>Buscar</span>
        <input type="search" data-commercial-prospects-search aria-label="Buscar prospectos" placeholder="Buscar…" value="${commercialEsc(commercialState.prospectsSearchText)}">
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-prospects-clear>✕ Limpiar</button>
    </div>`;
}

function commercialProspectsAnalysisHtml() {
  const all = (commercialState.prospects && commercialState.prospects.opportunities) || [];
  const rows = commercialCurrentProspectsRows();
  const filtersHtml = all.length ? commercialProspectsFiltersHtml(commercialProspectsSellerOptions(all)) : "";
  return `${filtersHtml}
    <div data-commercial-prospects-table>${commercialProspectsBlockHtml(rows)}</div>`;
}

// Cuerpo del panel — MISMO contrato de estados que Forecast (loading/error/
// ready), incluida la gate config_incomplete-vs-source_error para el botón
// Reintentar (owner §9: "No disponible" + Reintentar scopeado, nunca una
// versión propia de Prospectos de ese primitivo).
function commercialProspectsPanelBodyHtml() {
  if (commercialState.prospectsStatus === "error") {
    const configIncomplete = commercialState.prospectsErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los prospectos desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("prospectos", "Reintentar la carga de prospectos"));
  }
  if (commercialState.prospectsStatus !== "ready") {
    return commercialLoadingHtml("Cargando prospectos…");
  }
  const total = (commercialState.prospects && commercialState.prospects.total) || 0;
  if (!total) {
    return commercialEmptyStateHtml("Sin datos", "No hay prospectos (etapa 0) para este segmento.");
  }
  return commercialProspectsAnalysisHtml();
}

function commercialProspectsCountLabel() {
  if (commercialState.prospectsStatus !== "ready") return "";
  const total = (commercialState.prospects && commercialState.prospects.total) || 0;
  return `${commercialFmtInt(total)} prospecto(s)`;
}

// ── Ganadas — detalle por año (Commercial Tile 6, ver HANDOFF) ─────────────
// Familia DISTINTA de Activas/Prospectos: no es stock por etapa, es un
// desenlace histórico por año, con un contrato legacy mucho más chico (4
// columnas, sin Etapa/Motivo/Origen/Tags/Licencias/Próx. Acción/Últ. Resp.).
// Reusa lo genuinamente compartido — formatters, `.bar-row` (el MISMO
// primitivo de ranking que ya usa Creadas, promovido Hub-wide), `.dashboard-
// table`/`.matrix-scroll`, `commercialEmptyStateHtml`/`commercialRetryButtonHtml`/
// `commercialLoadingHtml` — pero NO la tabla de Activas/Prospectos (columnas
// distintas) ni la política de hidratación por año de Creadas (Ganadas nunca
// precalienta el año anterior en background — owner directive).
const COMMERCIAL_GANADAS_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  hours: o => (o.hours != null && o.hours !== false) ? Number(o.hours) : -Infinity,
};

function commercialGanadasSorted(rows) {
  const keyFn = commercialState.ganadasSortKey && COMMERCIAL_GANADAS_SORT_KEYS[commercialState.ganadasSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.ganadasSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function commercialToggleGanadasSort(key) {
  if (commercialState.ganadasSortKey === key) {
    commercialState.ganadasSortDir *= -1;
  } else {
    commercialState.ganadasSortKey = key;
    commercialState.ganadasSortDir = 1;
  }
}

function commercialGanadasSellerOptions(rows) {
  return [...new Set(rows.map(o => o.seller).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}

function commercialGanadasClientOptions(rows) {
  return [...new Set(rows.map(o => o.client).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}

// Vendedor + Cliente COMPONEN (AND) — MISMO criterio que Etapa+Vendedor en
// Activas (`commercialAnalysisFilteredOpportunities`): "" en cualquiera de
// los dos eje = ese eje sin filtrar.
function commercialGanadasFilteredRows() {
  const all = (commercialState.ganadas && commercialState.ganadas.opportunities) || [];
  return all.filter(o =>
    (!commercialState.ganadasSellerFilter || o.seller === commercialState.ganadasSellerFilter)
    && (!commercialState.ganadasClientFilter || o.client === commercialState.ganadasClientFilter));
}

// Ranking por vendedor — MISMO primitivo `.bar-row` que Creadas
// (`commercialCreadasSellerRankingHtml`), pero ordenado y dimensionado por
// HORAS (no por cantidad): así rankeaba el legacy
// (`_renderKpiDrillSellerRanking` — `rows.sort((a,b)=>b[1].horas-a[1].horas)`,
// ancho de barra `horas/maxHoras`), horas es la magnitud de negocio real.
// La cantidad viaja igual, entre paréntesis en `.bar-value` — mismo child,
// nunca una quinta columna que `.bar-row` no tiene.
//
// MISMA cascada que Creadas (owner §8): la fila entera es el target
// (`role="button"` + `tabindex="0"`, Enter/Espacio funcionan porque `.bar-
// row` no es un `<button>` nativo — envolverlo rompería su layout flex de
// tres hijos, MISMO motivo que Creadas), SIN toggle-to-clear (clickear el
// vendedor ya activo lo mantiene — Limpiar/"Todos" son las únicas salidas
// explícitas).
// Envoltorio `.commercial-ganadas-ranking` (owner corrective, screenshot:
// falta el margen inferior que separa Filtros→Ranking también entre
// Ranking→Detalle) — SOLO agrega el margen que `.bar-row`/`.dashboard-empty`
// no traen por default (6px de `.bar-row`, ninguno de `.dashboard-empty`),
// MISMO ritmo visual que el margen inferior ya establecido de
// `.whatsnew-filters` (18px). Scoped a Ganadas — no toca `.bar-row` ni
// ningún otro consumidor compartido.
function commercialGanadasSellerRankingHtml(rows) {
  if (!rows.length) {
    return `<div class="commercial-ganadas-ranking"><p class="perspective-label">Ranking por vendedor</p>` + commercialEmptyStateHtml("Sin resultados",
      commercialState.ganadasSellerFilter
        ? "Ningún vendedor tiene ganadas con este filtro."
        : "Ningún vendedor tiene ganadas en este año.") + `</div>`;
  }
  const map = {};
  rows.forEach(o => {
    const seller = o.seller || "Sin vendedor";
    const bucket = map[seller] || (map[seller] = { count: 0, hours: 0 });
    bucket.count += 1;
    bucket.hours += (o.hours || 0);
  });
  const ranked = Object.entries(map).sort((a, b) => b[1].hours - a[1].hours);
  const max = ranked[0][1].hours || 1;
  const activo = commercialState.ganadasSellerFilter;
  const rowsHtml = ranked.map(([seller, d]) => {
    const sel = seller === activo;
    return `
    <div class="bar-row bar-row--clickable${sel ? " is-active" : ""}"
      data-commercial-ganadas-seller-pick="${commercialEsc(seller)}"
      role="button" tabindex="0" aria-pressed="${sel ? "true" : "false"}"
      aria-label="Filtrar Ganadas por ${commercialEsc(seller)}">
      <span class="bar-label">${commercialEsc(seller)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(d.hours / max * 100).toFixed(1)}%"></div></div>
      <span class="bar-value">${commercialFmtInt(d.hours)} hs (${commercialFmtInt(d.count)})</span>
    </div>`;
  }).join("");
  return `<div class="commercial-ganadas-ranking"><p class="perspective-label">Ranking por vendedor</p>${rowsHtml}</div>`;
}

// Contexto de filtro activo — MISMO primitivo compartido que ya usa Creadas
// (`commercialCreadasFilterContextHtml`, tools.css `.dashboard-subhead__
// filter`/`__reset`): "· Filtrado por <dimensión>: <valor> ✕", MISMA
// experiencia funcional y visual (owner corrective, post-smoke). Un chip
// por eje activo — acá hay DOS (Vendedor/Cliente) donde Creadas sólo tiene
// uno, así que se emiten en secuencia, nunca una variante "multi-filtro"
// nueva.
function commercialGanadasFilterContextHtml() {
  return commercialCreadasFilterContextHtml("Vendedor", commercialState.ganadasSellerFilter,
      "data-commercial-ganadas-seller-clear", "Volver a todos los vendedores")
    + commercialCreadasFilterContextHtml("Cliente", commercialState.ganadasClientFilter,
      "data-commercial-ganadas-client-clear", "Volver a todos los clientes");
}

function commercialGanadasTableHtml(rows) {
  const sectionLabel = `<p class="perspective-label">Detalle de Ganadas${commercialGanadasFilterContextHtml()}</p>`;
  if (!rows.length) {
    return sectionLabel + commercialEmptyStateHtml("Sin resultados",
      commercialState.ganadasSellerFilter
        ? "Ninguna ganada coincide con este filtro."
        : "No hay oportunidades ganadas en este año.");
  }
  const sorted = commercialGanadasSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.ganadasSortKey === key;
    const arrow = active ? (commercialState.ganadasSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-ganadas-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td>${commercialFmtInt(o.hours)}</td>
    </tr>`).join("");
  const totalHoras = rows.reduce((s, o) => s + (o.hours || 0), 0);
  const totalRow = `
    <tr class="is-total">
      <td class="row-label" colspan="3">Total (${rows.length})</td>
      <td>${commercialFmtInt(totalHoras)}</td>
    </tr>`;
  return `${sectionLabel}
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:40%"><col style="width:25%"><col style="width:25%"><col style="width:10%">
        </colgroup>
        <thead><tr>
          ${th("opportunity", "Oportunidad", "row-label-col")}
          ${th("client", "Cliente", "left")}
          ${th("seller", "Vendedor", "left")}
          ${th("hours", "Horas")}
        </tr></thead>
        <tbody>${rowsHtml}${totalRow}</tbody>
      </table>
    </div>`;
}

// Ranking + tabla comparten UN solo host quirúrgico
// (`[data-commercial-ganadas-results]`): los dos se derivan del MISMO
// subconjunto filtrado por Vendedor, así que un cambio de filtro/orden
// siempre los re-dibuja juntos — nunca uno desincronizado del otro.
function commercialGanadasResultsHtml(rows) {
  return commercialGanadasSellerRankingHtml(rows) + commercialGanadasTableHtml(rows);
}

// Año actual .. año actual−5 — rango ESTÁTICO, MISMA paridad legacy que el
// `<select>` de `showKpiDrill` (`curY` a `curY-5`, 6 opciones). A propósito
// NO es un descubrimiento contra Odoo: ofrecer una opción de año nunca
// prometió que ese año tenga datos (mismo criterio ya establecido para
// Creadas), y acá ni siquiera hace falta la consulta barata de
// `creadas_selectable_years` — el rango del legacy YA es la fuente de
// verdad completa.
function commercialGanadasYearOptions() {
  const current = new Date().getFullYear();
  const years = [];
  for (let y = current; y >= current - 5; y -= 1) years.push(String(y));
  return years;
}

// Año / Vendedor / Cliente / Limpiar — MISMOS primitivos que el resto de
// Comercial (`.whatsnew-filters`), SIN "Buscar" (owner §7: "Do NOT add
// free-text Buscar in this increment" — el legacy Ganadas nunca tuvo uno).
// Cliente (owner corrective, post-smoke) va DESPUÉS de Vendedor — MISMO
// orden que Etapa→Vendedor en Activas: el eje que también alimenta un
// gráfico (acá, Vendedor→ranking) va primero.
function commercialGanadasControlsHtml() {
  const year = commercialState.ganadasYear;
  const allRows = (commercialState.ganadas && commercialState.ganadas.opportunities) || [];
  const sellerOpts = commercialGanadasSellerOptions(allRows).map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.ganadasSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  const clientOpts = commercialGanadasClientOptions(allRows).map(c =>
    `<option value="${commercialEsc(c)}"${commercialState.ganadasClientFilter === c ? " selected" : ""}>${commercialEsc(c)}</option>`).join("");
  const yearOpts = commercialGanadasYearOptions().map(y =>
    `<option value="${y}"${y === year ? " selected" : ""}>${y}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-ganadas-filters>
      <label class="whatsnew-filter">
        <span>Año</span>
        <select data-commercial-ganadas-year>${yearOpts}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-ganadas-seller-filter>
          <option value=""${commercialState.ganadasSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <label class="whatsnew-filter">
        <span>Cliente</span>
        <select data-commercial-ganadas-client-filter>
          <option value=""${commercialState.ganadasClientFilter === "" ? " selected" : ""}>Todos</option>
          ${clientOpts}
        </select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-ganadas-clear>✕ Limpiar</button>
    </div>`;
}

// Cambiar de vendedor — UNA sola función para las TRES puertas (dropdown,
// click en el ranking, Limpiar), MISMO contrato que
// `commercialCreadasSetSeller`. Re-render de panel ENTERO (no quirúrgico):
// a diferencia de Buscar (texto libre, perdería foco/cursor), acá no hay
// ningún `<input>` que proteger — un `<select>` no pierde nada al
// regenerarse, así que el mismo camino que ya usa Creadas es seguro y
// simple acá también. NO toca Cliente — los dos ejes son independientes.
function commercialGanadasSetSeller(seller) {
  commercialState.ganadasSellerFilter = seller || "";
  commercialRenderGanadasPanel();
}

// Cambiar de cliente — MISMO contrato que `commercialGanadasSetSeller`
// (owner corrective, post-smoke), sólo con el dropdown como única puerta
// (el ranking sigue siendo exclusivamente por vendedor). NO toca Vendedor.
function commercialGanadasSetClient(client) {
  commercialState.ganadasClientFilter = client || "";
  commercialRenderGanadasPanel();
}

// Limpiar — resetea la COMBINACIÓN de los dos ejes en un solo gesto, MISMO
// criterio que "✕ Limpiar" en Activas/Prospectos.
function commercialGanadasClearFilters() {
  commercialState.ganadasSellerFilter = "";
  commercialState.ganadasClientFilter = "";
  commercialRenderGanadasPanel();
}

function commercialGanadasCountLabel() {
  if (commercialState.ganadasStatus !== "ready") return "";
  const total = (commercialState.ganadas && commercialState.ganadas.total) || 0;
  return `${commercialFmtInt(total)} ganada(s)`;
}

function commercialGanadasAnalysisHtml() {
  const rows = commercialGanadasFilteredRows();
  return commercialGanadasControlsHtml()
    + `<div data-commercial-ganadas-results>${commercialGanadasResultsHtml(rows)}</div>`;
}

// Cuerpo del panel — MISMO contrato de estados que Forecast/Creadas/
// Prospectos (loading/error/ready), incluida la gate config_incomplete-vs-
// source_error para el botón Reintentar.
function commercialGanadasPanelBodyHtml() {
  if (commercialState.ganadasStatus === "error") {
    const configIncomplete = commercialState.ganadasErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar las oportunidades ganadas desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("ganadas", "Reintentar la carga de ganadas"));
  }
  if (commercialState.ganadasStatus !== "ready") {
    return commercialLoadingHtml("Cargando ganadas…");
  }
  return commercialGanadasAnalysisHtml();
}

// ── Perdidas — detalle por año, FACETADO (Commercial Tile 7, ver HANDOFF) ──
// Universo: el MISMO pipeline que ya usa el KPI —
// `active=False+probability=0+date_closed en año` → `_resolve_lost_motivos`
// → `_filter_excluded_lost` — resuelto server-side (ver
// `_build_perdidas_detail`, commercial_service.py); acá sólo se consume la
// fila ya válida. Reusa lo genuinamente compartido: formatters, `.bar-row`
// (MISMO primitivo que Ganadas), `commercialCreadasFilterContextHtml` (el
// chip de contexto ya genérico), `.dashboard-table`/`.matrix-scroll`,
// `.activity-grid-2` (layout de dos columnas YA promovido a tools.css, el
// MISMO que usa Reporting para Mantenimiento|Proyecto) — NO inventa un
// segundo primitivo de grilla. Sin librería de gráficos externa: el Hub no
// carga ninguna en ningún lado (verificado), así que el desglose por
// Motivo es OTRO `.bar-row`, mismo lenguaje visual que el ranking de al
// lado — nunca un canvas nuevo.
const COMMERCIAL_PERDIDAS_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  motivo: o => (o.motivo || "").toLowerCase(),
  hours: o => (o.hours != null && o.hours !== false) ? Number(o.hours) : -Infinity,
};

function commercialPerdidasSorted(rows) {
  const keyFn = commercialState.perdidasSortKey && COMMERCIAL_PERDIDAS_SORT_KEYS[commercialState.perdidasSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.perdidasSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function commercialTogglePerdidasSort(key) {
  if (commercialState.perdidasSortKey === key) {
    commercialState.perdidasSortDir *= -1;
  } else {
    commercialState.perdidasSortKey = key;
    commercialState.perdidasSortDir = 1;
  }
}

function commercialPerdidasSellerOptions(rows) {
  return [...new Set(rows.map(o => o.seller).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}
function commercialPerdidasClientOptions(rows) {
  return [...new Set(rows.map(o => o.client).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}

function commercialPerdidasBaseRows() {
  return (commercialState.perdidas && commercialState.perdidas.opportunities) || [];
}

// FACETADO (owner §11 — "avoid self-filter collapse"): cada llamador decide
// qué eje IGNORA de su PROPIO filtro activo. Sin esto, seleccionar un
// vendedor en el ranking dejaría el ranking mostrando UNA sola barra (la ya
// elegida) — imposible cambiar de opinión sin pasar por Limpiar. La tabla
// es la única vista que aplica los TRES ejes a la vez — es "el resultado
// final", no un panel de exploración.
function commercialPerdidasFilterRows(rows, { seller = true, client = true, motivo = true } = {}) {
  return rows.filter(o =>
    (!seller || !commercialState.perdidasSellerFilter || o.seller === commercialState.perdidasSellerFilter)
    && (!client || !commercialState.perdidasClientFilter || o.client === commercialState.perdidasClientFilter)
    && (!motivo || !commercialState.perdidasMotivoFilter || o.motivo === commercialState.perdidasMotivoFilter));
}
// Tabla: Año (ya implícito en el dataset cargado) ∧ Vendedor ∧ Cliente ∧ Motivo.
function commercialPerdidasTableRows() {
  return commercialPerdidasFilterRows(commercialPerdidasBaseRows());
}
// Ranking por vendedor: ∧ Cliente ∧ Motivo — ignora el propio Vendedor.
function commercialPerdidasRankingRows() {
  return commercialPerdidasFilterRows(commercialPerdidasBaseRows(), { seller: false });
}
// Desglose por Motivo: ∧ Vendedor ∧ Cliente — ignora el propio Motivo.
function commercialPerdidasMotivoRows() {
  return commercialPerdidasFilterRows(commercialPerdidasBaseRows(), { motivo: false });
}

// Ranking por vendedor — MISMA forma que Ganadas (ordenado/dimensionado por
// HORAS, valor "hs (cantidad)"), SIN toggle-to-clear (clickear el vendedor
// ya activo lo mantiene — MISMO criterio que Ganadas/Creadas). Envuelto en
// `.commercial-perdidas-ranking` por la MISMA razón que
// `.commercial-ganadas-ranking`: `.bar-value` en su ancho compartido de
// 60px partía "1,234 hs (3)" en dos líneas (ver HANDOFF — Ganadas pre-
// smoke gate) — acá el override es AÚN más necesario porque este ranking
// vive en la mitad de `.activity-grid-2`, más angosto que el de Ganadas.
function commercialPerdidasSellerRankingHtml(rows) {
  if (!rows.length) {
    return `<div class="commercial-perdidas-ranking"><p class="perspective-label">Ranking por vendedor</p>` + commercialEmptyStateHtml("Sin resultados",
      (commercialState.perdidasClientFilter || commercialState.perdidasMotivoFilter)
        ? "Ningún vendedor tiene perdidas con este filtro."
        : "Ningún vendedor tiene perdidas en este año.") + `</div>`;
  }
  const map = {};
  rows.forEach(o => {
    const seller = o.seller || "Sin vendedor";
    const bucket = map[seller] || (map[seller] = { count: 0, hours: 0 });
    bucket.count += 1;
    bucket.hours += (o.hours || 0);
  });
  const ranked = Object.entries(map).sort((a, b) => b[1].hours - a[1].hours);
  const max = ranked[0][1].hours || 1;
  const activo = commercialState.perdidasSellerFilter;
  const rowsHtml = ranked.map(([seller, d]) => {
    const sel = seller === activo;
    return `
    <div class="bar-row bar-row--clickable${sel ? " is-active" : ""}"
      data-commercial-perdidas-seller-pick="${commercialEsc(seller)}"
      role="button" tabindex="0" aria-pressed="${sel ? "true" : "false"}"
      aria-label="Filtrar Perdidas por ${commercialEsc(seller)}">
      <span class="bar-label">${commercialEsc(seller)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(d.hours / max * 100).toFixed(1)}%"></div></div>
      <span class="bar-value">${commercialFmtInt(d.hours)} hs (${commercialFmtInt(d.count)})</span>
    </div>`;
  }).join("");
  return `<div class="commercial-perdidas-ranking"><p class="perspective-label">Ranking por vendedor</p>${rowsHtml}</div>`;
}

// Desglose por Motivo — legacy lo mostraba en un doughnut con librería
// externa; portado como `.bar-row` (mismo primitivo, sin librería nueva —
// ver comentario de sección). Métrica = CANTIDAD (owner §10: "NOT hours" — a
// propósito distinto del ranking de al lado). CON toggle-to-clear
// (clickear el motivo ya activo lo despeja) — MISMA semántica que el click
// en el segmento del doughnut legacy, preservada a propósito. El valor
// ("12") nunca necesita el override de ancho: cabe sobrado en los 60px
// compartidos.
function commercialPerdidasMotivoBreakdownHtml(rows) {
  if (!rows.length) {
    return `<p class="perspective-label">Por motivo de pérdida</p>` + commercialEmptyStateHtml("Sin resultados",
      (commercialState.perdidasSellerFilter || commercialState.perdidasClientFilter)
        ? "Ningún motivo tiene perdidas con este filtro."
        : "Ningún motivo tiene perdidas en este año.");
  }
  const map = {};
  rows.forEach(o => {
    const motivo = o.motivo || "Sin definir";
    map[motivo] = (map[motivo] || 0) + 1;
  });
  const ranked = Object.entries(map).sort((a, b) => b[1] - a[1]);
  const max = ranked[0][1] || 1;
  const activo = commercialState.perdidasMotivoFilter;
  const rowsHtml = ranked.map(([motivo, count]) => {
    const sel = motivo === activo;
    return `
    <div class="bar-row bar-row--clickable${sel ? " is-active" : ""}"
      data-commercial-perdidas-motivo-pick="${commercialEsc(motivo)}"
      role="button" tabindex="0" aria-pressed="${sel ? "true" : "false"}"
      aria-label="Filtrar Perdidas por motivo ${commercialEsc(motivo)}">
      <span class="bar-label">${commercialEsc(motivo)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(count / max * 100).toFixed(1)}%"></div></div>
      <span class="bar-value">${commercialFmtInt(count)}</span>
    </div>`;
  }).join("");
  return `<p class="perspective-label">Por motivo de pérdida</p>${rowsHtml}`;
}

function commercialPerdidasTableHtml(rows) {
  const sectionLabel = `<p class="perspective-label">Detalle de Perdidas${commercialPerdidasFilterContextHtml()}</p>`;
  if (!rows.length) {
    return sectionLabel + commercialEmptyStateHtml("Sin resultados",
      (commercialState.perdidasSellerFilter || commercialState.perdidasClientFilter || commercialState.perdidasMotivoFilter)
        ? "Ninguna perdida coincide con estos filtros."
        : "No hay oportunidades perdidas en este año.");
  }
  const sorted = commercialPerdidasSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.perdidasSortKey === key;
    const arrow = active ? (commercialState.perdidasSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-perdidas-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialEsc(o.motivo)}</td>
      <td>${commercialFmtInt(o.hours)}</td>
    </tr>`).join("");
  const totalHoras = rows.reduce((s, o) => s + (o.hours || 0), 0);
  const totalRow = `
    <tr class="is-total">
      <td class="row-label" colspan="4">Total (${rows.length})</td>
      <td>${commercialFmtInt(totalHoras)}</td>
    </tr>`;
  return `${sectionLabel}
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:32%"><col style="width:20%"><col style="width:20%"><col style="width:20%"><col style="width:8%">
        </colgroup>
        <thead><tr>
          ${th("opportunity", "Oportunidad", "row-label-col")}
          ${th("client", "Cliente", "left")}
          ${th("seller", "Vendedor", "left")}
          ${th("motivo", "Motivo", "left")}
          ${th("hours", "Hs")}
        </tr></thead>
        <tbody>${rowsHtml}${totalRow}</tbody>
      </table>
    </div>`;
}

// Contexto de filtro activo — TRES chips posibles (Vendedor/Cliente/Motivo),
// MISMO primitivo genérico ya reusado por Ganadas
// (`commercialCreadasFilterContextHtml`) — cero chip nuevo, cero clase
// nueva, sólo se emiten cuando aplica cada eje.
function commercialPerdidasFilterContextHtml() {
  return commercialCreadasFilterContextHtml("Vendedor", commercialState.perdidasSellerFilter,
      "data-commercial-perdidas-seller-clear", "Volver a todos los vendedores")
    + commercialCreadasFilterContextHtml("Cliente", commercialState.perdidasClientFilter,
      "data-commercial-perdidas-client-clear", "Volver a todos los clientes")
    + commercialCreadasFilterContextHtml("Motivo", commercialState.perdidasMotivoFilter,
      "data-commercial-perdidas-motivo-clear", "Volver a todos los motivos");
}

// Año actual .. año actual−5 — MISMO rango estático que Ganadas (paridad
// legacy, `lost-panel-year-select`: `curY` a `curY-5`).
function commercialPerdidasYearOptions() {
  const current = new Date().getFullYear();
  const years = [];
  for (let y = current; y >= current - 5; y -= 1) years.push(String(y));
  return years;
}

// Año / Vendedor / Cliente / Limpiar — MISMOS primitivos que Ganadas, SIN
// Motivo (owner §10: "No Motivo dropdown is required... legacy used
// chart-click filtering") y SIN Buscar (legacy Perdidas tampoco tuvo uno).
function commercialPerdidasControlsHtml() {
  const year = commercialState.perdidasYear;
  const allRows = commercialPerdidasBaseRows();
  const sellerOpts = commercialPerdidasSellerOptions(allRows).map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.perdidasSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  const clientOpts = commercialPerdidasClientOptions(allRows).map(c =>
    `<option value="${commercialEsc(c)}"${commercialState.perdidasClientFilter === c ? " selected" : ""}>${commercialEsc(c)}</option>`).join("");
  const yearOpts = commercialPerdidasYearOptions().map(y =>
    `<option value="${y}"${y === year ? " selected" : ""}>${y}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-perdidas-filters>
      <label class="whatsnew-filter">
        <span>Año</span>
        <select data-commercial-perdidas-year>${yearOpts}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-perdidas-seller-filter>
          <option value=""${commercialState.perdidasSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <label class="whatsnew-filter">
        <span>Cliente</span>
        <select data-commercial-perdidas-client-filter>
          <option value=""${commercialState.perdidasClientFilter === "" ? " selected" : ""}>Todos</option>
          ${clientOpts}
        </select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-perdidas-clear>✕ Limpiar</button>
    </div>`;
}

// Cambiar Vendedor/Cliente/Motivo — CUATRO puertas por eje (dropdown +
// ranking/chart-click + Limpiar), MISMO contrato que
// `commercialGanadasSetSeller`. Re-render de panel ENTERO: sin `<input>`
// de texto que proteger, un `<select>`/`.bar-row` no pierde nada al
// regenerarse.
function commercialPerdidasSetSeller(seller) {
  commercialState.perdidasSellerFilter = seller || "";
  commercialRenderPerdidasPanel();
}
function commercialPerdidasSetClient(client) {
  commercialState.perdidasClientFilter = client || "";
  commercialRenderPerdidasPanel();
}
// Toggle-to-clear (ver comentario de `commercialPerdidasMotivoBreakdownHtml`).
function commercialPerdidasSetMotivo(motivo) {
  commercialState.perdidasMotivoFilter = commercialState.perdidasMotivoFilter === motivo ? "" : (motivo || "");
  commercialRenderPerdidasPanel();
}
// Limpiar — resetea los TRES ejes, NUNCA el año (owner §11: "Limpiar
// resets Vendedor/Cliente/Motivo but NOT the selected year").
function commercialPerdidasClearFilters() {
  commercialState.perdidasSellerFilter = "";
  commercialState.perdidasClientFilter = "";
  commercialState.perdidasMotivoFilter = "";
  commercialRenderPerdidasPanel();
}

function commercialPerdidasCountLabel() {
  if (commercialState.perdidasStatus !== "ready") return "";
  const total = (commercialState.perdidas && commercialState.perdidas.total) || 0;
  return `${commercialFmtInt(total)} perdida(s)`;
}

// Ranking | Motivo LADO A LADO — reusa `.activity-grid-2` (primitivo Hub YA
// promovido, MISMO que usa Reporting para Mantenimiento|Proyecto) — cero
// grilla nueva. Envuelto en `.commercial-perdidas-analysis` (margen
// inferior propio, MISMO criterio que `.commercial-ganadas-ranking`: sin
// esto el ritmo vertical hacia "Detalle de Perdidas" queda más apretado
// que el de Filtros→Análisis arriba).
function commercialPerdidasAnalysisHtml() {
  const rankingHtml = commercialPerdidasSellerRankingHtml(commercialPerdidasRankingRows());
  const motivoHtml = commercialPerdidasMotivoBreakdownHtml(commercialPerdidasMotivoRows());
  const tableRows = commercialPerdidasTableRows();
  return commercialPerdidasControlsHtml()
    + `<div class="commercial-perdidas-analysis"><div class="activity-grid-2">
        <div>${rankingHtml}</div>
        <div>${motivoHtml}</div>
      </div></div>`
    + `<div data-commercial-perdidas-table>${commercialPerdidasTableHtml(tableRows)}</div>`;
}

// Cuerpo del panel — MISMO contrato de estados que Ganadas.
function commercialPerdidasPanelBodyHtml() {
  if (commercialState.perdidasStatus === "error") {
    const configIncomplete = commercialState.perdidasErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar las oportunidades perdidas desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("perdidas", "Reintentar la carga de perdidas"));
  }
  if (commercialState.perdidasStatus !== "ready") {
    return commercialLoadingHtml("Cargando perdidas…");
  }
  return commercialPerdidasAnalysisHtml();
}

// ── Suspendidas — detalle (Commercial Tile 8, ver HANDOFF) ─────────────────
// Familia "relevamiento" (Activas/Prospectos/Suspendidas): STOCK actual por
// etapa, MISMAS fórmulas ya auditadas (Hs Pond./Últ. Resp./Días resp./
// actividades/urgencia) — reusa `commercialProspectsTagsHtml` TAL CUAL para
// Etiquetas (genérico: sólo recibe un array de strings, cero acoplamiento a
// Prospectos) en vez de duplicar la función. Se diferencia de Prospectos en
// DOS columnas (owner decisión explícita, Tile 8 §7): Etapa (universo
// genuinamente multi-etapa — pueden coexistir varias etapas "suspendido")
// y, ya heredado de Prospectos, Etiquetas. Tabla PARALELA a
// `commercialProspectsTableHtml`/`commercialOpportunitiesTableHtml` (owner
// §6: "small duplication is preferable to changing already-closed Activas/
// Prospectos behavior" — NO se refactoriza la familia en este incremento).
const COMMERCIAL_SUSPENDIDAS_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  stage: o => (o.stage || "").toLowerCase(),
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

function commercialSuspendidasSorted(rows) {
  const keyFn = commercialState.suspendidasSortKey && COMMERCIAL_SUSPENDIDAS_SORT_KEYS[commercialState.suspendidasSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.suspendidasSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function commercialToggleSuspendidasSort(key) {
  if (commercialState.suspendidasSortKey === key) {
    commercialState.suspendidasSortDir *= -1;
  } else {
    commercialState.suspendidasSortKey = key;
    commercialState.suspendidasSortDir = 1;
  }
}

function commercialSuspendidasSellerOptions(rows) {
  return [...new Set(rows.map(o => o.seller).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}

function commercialSuspendidasTableHtml(rows) {
  if (!rows.length) {
    return commercialEmptyStateHtml("Sin resultados",
      commercialState.suspendidasSellerFilter
        ? "Ninguna suspendida coincide con este filtro."
        : "No hay oportunidades suspendidas en este momento.");
  }
  const sorted = commercialSuspendidasSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.suspendidasSortKey === key;
    const arrow = active ? (commercialState.suspendidasSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-suspendidas-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => {
    const acts = o.activities || [];
    const nextAction = acts.length
      ? acts.map(act => {
          const days = commercialDaysUntil(act.deadline);
          const rel = days === null ? "" : days < 0 ? ` (venc. ${Math.abs(days)}d)` : days === 0 ? " (hoy)" : ` (${days}d)`;
          const datePrefix = commercialEsc(commercialFmtDate(act.deadline));
          if (!act.type) return datePrefix;
          const color = commercialUrgencyColor(days);
          const typeAndRel = `${commercialEsc(act.type)}${commercialEsc(rel)}`;
          const badge = color
            ? `<span class="dashboard-status-badge dashboard-status-badge--${color}">${typeAndRel}</span>`
            : typeAndRel;
          return `${datePrefix} · ${badge}`;
        }).join("<br>")
      : "—";
    const lic = o.licencias === "Si" ? "✓" : "";
    const priorityStars = commercialEsc(COMMERCIAL_PRIORITY_STARS[o.priority] || "—");
    const priorityLabel = COMMERCIAL_PRIORITY_LABELS[o.priority] || "Sin prioridad";
    return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialEsc(o.stage)}</td>
      <td class="left">${commercialProspectsTagsHtml(o.tags)}</td>
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
  const totals = rows.reduce((acc, o) => ({
    hours: acc.hours + (o.hours || 0),
    weighted_hours: acc.weighted_hours + (o.weighted_hours || 0),
  }), { hours: 0, weighted_hours: 0 });
  const totalRow = `
    <tr class="is-total">
      <td class="row-label" colspan="6">Total (${rows.length})</td>
      <td>${commercialFmtInt(totals.hours)}</td>
      <td>${commercialFmtInt(totals.weighted_hours)}</td>
      <td colspan="5"></td>
    </tr>`;
  // Trece columnas (once de Prospectos + Etapa): Oportunidad/Vendedor/
  // Próx. Acción ceden puntos para Etapa+Etiquetas — MISMO criterio de
  // "ancho real por columna" ya establecido para Prospectos.
  return `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:16%"><col style="width:9%"><col style="width:8%">
          <col style="width:9%"><col style="width:8%"><col style="width:4%">
          <col style="width:5%"><col style="width:6%"><col style="width:5%">
          <col style="width:4%"><col style="width:14%"><col style="width:7%">
          <col style="width:7%">
        </colgroup>
        <thead><tr>
          ${th("opportunity", "Oportunidad", "row-label-col")}
          ${th("client", "Cliente", "left")}
          ${th("seller", "Vendedor", "left")}
          ${th("stage", "Etapa", "left")}
          <th class="left">Etiquetas</th>
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

function commercialSuspendidasBlockHtml(rows) {
  return `${rows.length ? commercialUrgencyLegendHtml() : ""}${commercialSuspendidasTableHtml(rows)}`;
}

function commercialCurrentSuspendidasRows() {
  const all = (commercialState.suspendidas && commercialState.suspendidas.opportunities) || [];
  return commercialState.suspendidasSellerFilter
    ? all.filter(o => o.seller === commercialState.suspendidasSellerFilter)
    : all;
}

// Vendedor + Limpiar — MISMOS primitivos que el resto de Comercial. SIN
// Buscar (owner §10: "Do NOT add... search" — legacy Suspendidas nunca tuvo
// uno) y SIN chip de filtro activo (MISMO criterio que Prospectos §8: el
// `<select>` ya representa el estado activo).
function commercialSuspendidasFiltersHtml(sellerOptions) {
  const sellerOpts = sellerOptions.map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.suspendidasSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-suspendidas-filters>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-suspendidas-seller-filter>
          <option value=""${commercialState.suspendidasSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-suspendidas-clear>✕ Limpiar</button>
    </div>`;
}

function commercialSuspendidasAnalysisHtml() {
  const all = (commercialState.suspendidas && commercialState.suspendidas.opportunities) || [];
  const rows = commercialCurrentSuspendidasRows();
  const filtersHtml = all.length ? commercialSuspendidasFiltersHtml(commercialSuspendidasSellerOptions(all)) : "";
  return `${filtersHtml}
    <div data-commercial-suspendidas-table>${commercialSuspendidasBlockHtml(rows)}</div>`;
}

// Cuerpo del panel — MISMO contrato de estados que Prospectos.
function commercialSuspendidasPanelBodyHtml() {
  if (commercialState.suspendidasStatus === "error") {
    const configIncomplete = commercialState.suspendidasErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar las oportunidades suspendidas desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("suspendidas", "Reintentar la carga de suspendidas"));
  }
  if (commercialState.suspendidasStatus !== "ready") {
    return commercialLoadingHtml("Cargando suspendidas…");
  }
  const total = (commercialState.suspendidas && commercialState.suspendidas.total) || 0;
  if (!total) {
    return commercialEmptyStateHtml("Sin datos", "No hay oportunidades suspendidas para este segmento.");
  }
  return commercialSuspendidasAnalysisHtml();
}

function commercialSuspendidasCountLabel() {
  if (commercialState.suspendidasStatus !== "ready") return "";
  const total = (commercialState.suspendidas && commercialState.suspendidas.total) || 0;
  return `${commercialFmtInt(total)} suspendida(s)`;
}

// ═══════════════════════════════════════════════════════════════════════
// Bucket 1 (ver HANDOFF) — Licencias / Historial de Perdidas / Detalle
// Oportunidades. Licencias y Detalle comparten el MISMO universo backend
// (`commercialState.opportunitiesDetail`, ver `commercialLoadOpportunitiesDetail`
// — el legacy ya trata a las dos tabs como la misma fuente, filtrada
// `licencias === "Si"` client-side) y por eso comparten TAMBIÉN la misma
// tabla de 13 columnas — MISMA fila que ya usa Suspendidas
// (`commercialSuspendidasTableHtml`, ver arriba), pero PARALELA a propósito
// (owner §17: pequeña duplicación es preferible a tocar una tab ya cerrada
// — Suspendidas no se refactoriza acá) y compartida SÓLO entre estas dos
// tabs nuevas, que sí son el mismo incremento.
// ═══════════════════════════════════════════════════════════════════════

const COMMERCIAL_OPP_DETAIL_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  stage: o => (o.stage || "").toLowerCase(),
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

function commercialOppDetailSearchableText(o) {
  return [o.opportunity, o.client, o.seller].filter(Boolean).join(" ").toLowerCase();
}

// Fila — MISMAS 13 celdas que `commercialSuspendidasTableHtml` (Lic./Hs/Hs
// Pond./Prob./Prior.+tooltip/Próx. Acción multi-actividad/Últ. Resp./Días
// resp., ya auditadas y en producción para esa tab).
function commercialOppDetailRowHtml(o) {
  const acts = o.activities || [];
  const nextAction = acts.length
    ? acts.map(act => {
        const days = commercialDaysUntil(act.deadline);
        const rel = days === null ? "" : days < 0 ? ` (venc. ${Math.abs(days)}d)` : days === 0 ? " (hoy)" : ` (${days}d)`;
        const datePrefix = commercialEsc(commercialFmtDate(act.deadline));
        if (!act.type) return datePrefix;
        const color = commercialUrgencyColor(days);
        const typeAndRel = `${commercialEsc(act.type)}${commercialEsc(rel)}`;
        const badge = color
          ? `<span class="dashboard-status-badge dashboard-status-badge--${color}">${typeAndRel}</span>`
          : typeAndRel;
        return `${datePrefix} · ${badge}`;
      }).join("<br>")
    : "—";
  const lic = o.licencias === "Si" ? "✓" : "";
  const priorityStars = commercialEsc(COMMERCIAL_PRIORITY_STARS[o.priority] || "—");
  const priorityLabel = COMMERCIAL_PRIORITY_LABELS[o.priority] || "Sin prioridad";
  return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialEsc(o.stage)}</td>
      <td class="left">${commercialProspectsTagsHtml(o.tags)}</td>
      <td>${lic}</td>
      <td>${commercialFmtInt(o.hours)}</td>
      <td>${commercialFmtInt(o.weighted_hours)}</td>
      <td>${commercialFmtPercent(o.probability)}</td>
      <td class="chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(priorityLabel)}">${priorityStars}${commercialChartTooltip(priorityLabel)}</td>
      <td class="left">${nextAction}</td>
      <td>${o.last_response ? commercialEsc(commercialFmtDate(o.last_response)) : "—"}</td>
      <td>${o.days_elapsed != null ? `${commercialFmtInt(o.days_elapsed)}d` : "—"}</td>
    </tr>`;
}

// Tabla completa (colgroup+thead+tbody+totales) — compartida por Licencias
// y Detalle Oportunidades. `sortKey`/`sortDir` y el atributo de sort
// (`sortAttr`) vienen del LLAMADOR: cada tab tiene su PROPIO estado de
// orden (`licSortKey`/`detSortKey`), nunca uno compartido — ordenar
// Licencias no debe mover el orden de Detalle ni viceversa.
function commercialOppDetailTableHtml(rows, { sortKey, sortDir, sortAttr, emptyLabel }) {
  if (!rows.length) return commercialEmptyStateHtml("Sin resultados", emptyLabel);
  const keyFn = sortKey && COMMERCIAL_OPP_DETAIL_SORT_KEYS[sortKey];
  const sorted = keyFn ? [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -sortDir;
    if (av > bv) return sortDir;
    return 0;
  }) : rows;
  const th = (key, label, cls = "") => {
    const active = sortKey === key;
    const arrow = active ? (sortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" ${sortAttr}="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(commercialOppDetailRowHtml).join("");
  const totals = rows.reduce((acc, o) => ({
    hours: acc.hours + (o.hours || 0),
    weighted_hours: acc.weighted_hours + (o.weighted_hours || 0),
  }), { hours: 0, weighted_hours: 0 });
  const totalRow = `
    <tr class="is-total">
      <td class="row-label" colspan="6">Total (${rows.length})</td>
      <td>${commercialFmtInt(totals.hours)}</td>
      <td>${commercialFmtInt(totals.weighted_hours)}</td>
      <td colspan="5"></td>
    </tr>`;
  return `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:16%"><col style="width:9%"><col style="width:8%">
          <col style="width:9%"><col style="width:8%"><col style="width:4%">
          <col style="width:5%"><col style="width:6%"><col style="width:5%">
          <col style="width:4%"><col style="width:14%"><col style="width:7%">
          <col style="width:7%">
        </colgroup>
        <thead><tr>
          ${th("opportunity", "Oportunidad", "row-label-col")}
          ${th("client", "Cliente", "left")}
          ${th("seller", "Vendedor", "left")}
          ${th("stage", "Etapa", "left")}
          <th class="left">Etiquetas</th>
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

// ── Licencias (ver HANDOFF §4) ───────────────────────────────────────────
// Universo: `opportunitiesDetail.opportunities` (stages 0-8, active=True)
// filtrado `licencias === "Si"` — MISMO filtro CLIENT-SIDE que el legacy
// `loadLicenciasTab` (`.filter(o => o.licencias === "Si")`), nunca un
// dominio Odoo propio. Buscar es SÓLO por Oportunidad (MISMO alcance que
// `filterLicenciasOpps` — nunca Cliente/Vendedor, a diferencia de Detalle).
function commercialLicenciasAllRows() {
  const all = (commercialState.opportunitiesDetail && commercialState.opportunitiesDetail.opportunities) || [];
  return all.filter(o => o.licencias === "Si");
}
function commercialOppDetailFilterOptions(rows, key) {
  return [...new Set(rows.map(o => o[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}
function commercialLicenciasFilteredRows() {
  const all = commercialLicenciasAllRows();
  const { licStageFilter: fStage, licSellerFilter: fSeller } = commercialState;
  const search = (commercialState.licSearchText || "").toLowerCase().trim();
  return all.filter(o => {
    if (fStage && o.stage !== fStage) return false;
    if (fSeller && o.seller !== fSeller) return false;
    if (search && !(o.opportunity || "").toLowerCase().includes(search)) return false;
    return true;
  });
}
function commercialToggleLicenciasSort(key) {
  if (commercialState.licSortKey === key) commercialState.licSortDir *= -1;
  else { commercialState.licSortKey = key; commercialState.licSortDir = 1; }
}
function commercialLicenciasClearFilters() {
  commercialState.licSearchText = "";
  commercialState.licStageFilter = "";
  commercialState.licSellerFilter = "";
  commercialRenderCapability();
}
function commercialLicenciasFiltersHtml() {
  const all = commercialLicenciasAllRows();
  const opt = (key, current, allLabel) => {
    const values = commercialOppDetailFilterOptions(all, key);
    return `<option value=""${current === "" ? " selected" : ""}>${allLabel}</option>` +
      values.map(v => `<option value="${commercialEsc(v)}"${current === v ? " selected" : ""}>${commercialEsc(v)}</option>`).join("");
  };
  return `<div class="whatsnew-filters" data-commercial-licencias-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-licencias-search aria-label="Buscar en licencias"
          placeholder="Buscar…" value="${commercialEsc(commercialState.licSearchText)}">
      </label>
      <label class="whatsnew-filter">
        <span>Etapa</span>
        <select data-commercial-licencias-stage-filter>${opt("stage", commercialState.licStageFilter, "Todas")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-licencias-seller-filter>${opt("seller", commercialState.licSellerFilter, "Todos")}</select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-licencias-clear>✕ Limpiar</button>
    </div>`;
}
function commercialLicenciasTableHtml(rows) {
  return commercialOppDetailTableHtml(rows, {
    sortKey: commercialState.licSortKey, sortDir: commercialState.licSortDir,
    sortAttr: "data-commercial-licencias-sort",
    emptyLabel: "Ninguna oportunidad con licencias coincide con este filtro.",
  });
}
function commercialLicenciasHtml() {
  if (commercialState.opportunitiesDetailStatus === "loading") {
    return commercialLoadingHtml("Cargando licencias…");
  }
  if (commercialState.opportunitiesDetailStatus === "error") {
    const configIncomplete = commercialState.opportunitiesDetailErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar las licencias desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("opportunities-detail", "Reintentar la carga de licencias"));
  }
  // Owner follow-up (ver HANDOFF): MISMA estructura que Actividades — sin
  // título propio (`.commercial-bucket1-panel` es lo que le da a
  // `.whatsnew-filters` su top margin, ver commercial.css), filtros
  // PRIMERO, leyenda de urgencia después, tabla al final.
  const rows = commercialLicenciasFilteredRows();
  return `<div class="commercial-bucket1-panel">`
    + commercialLicenciasFiltersHtml()
    + commercialUrgencyLegendHtml()
    + `<div data-commercial-licencias-table>${commercialLicenciasTableHtml(rows)}</div>`
    + `</div>`;
}

// ── Detalle Oportunidades (ver HANDOFF §6) ───────────────────────────────
// Universo COMPLETO de `opportunitiesDetail` (stages 0-8, active=True) —
// SIN el filtro de Licencias. Buscar cubre Oportunidad+Cliente+Vendedor
// (MISMO alcance que `filterDetalleOpps` en el legacy).
function commercialDetalleAllRows() {
  return (commercialState.opportunitiesDetail && commercialState.opportunitiesDetail.opportunities) || [];
}
function commercialDetalleFilteredRows() {
  const all = commercialDetalleAllRows();
  const { detStageFilter: fStage, detSellerFilter: fSeller } = commercialState;
  const search = (commercialState.detSearchText || "").toLowerCase().trim();
  return all.filter(o => {
    if (fStage && o.stage !== fStage) return false;
    if (fSeller && o.seller !== fSeller) return false;
    if (search && !commercialOppDetailSearchableText(o).includes(search)) return false;
    return true;
  });
}
function commercialToggleDetalleSort(key) {
  if (commercialState.detSortKey === key) commercialState.detSortDir *= -1;
  else { commercialState.detSortKey = key; commercialState.detSortDir = 1; }
}
function commercialDetalleClearFilters() {
  commercialState.detSearchText = "";
  commercialState.detStageFilter = "";
  commercialState.detSellerFilter = "";
  commercialRenderCapability();
}
function commercialDetalleFiltersHtml() {
  const all = commercialDetalleAllRows();
  const opt = (key, current, allLabel) => {
    const values = commercialOppDetailFilterOptions(all, key);
    return `<option value=""${current === "" ? " selected" : ""}>${allLabel}</option>` +
      values.map(v => `<option value="${commercialEsc(v)}"${current === v ? " selected" : ""}>${commercialEsc(v)}</option>`).join("");
  };
  return `<div class="whatsnew-filters" data-commercial-detalle-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-detalle-search aria-label="Buscar en detalle de oportunidades"
          placeholder="Buscar…" value="${commercialEsc(commercialState.detSearchText)}">
      </label>
      <label class="whatsnew-filter">
        <span>Etapa</span>
        <select data-commercial-detalle-stage-filter>${opt("stage", commercialState.detStageFilter, "Todas")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-detalle-seller-filter>${opt("seller", commercialState.detSellerFilter, "Todos")}</select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-detalle-clear>✕ Limpiar</button>
    </div>`;
}
function commercialDetalleTableHtml(rows) {
  return commercialOppDetailTableHtml(rows, {
    sortKey: commercialState.detSortKey, sortDir: commercialState.detSortDir,
    sortAttr: "data-commercial-detalle-sort",
    emptyLabel: "Ninguna oportunidad coincide con este filtro.",
  });
}
function commercialDetalleHtml() {
  if (commercialState.opportunitiesDetailStatus === "loading") {
    return commercialLoadingHtml("Cargando detalle de oportunidades…");
  }
  if (commercialState.opportunitiesDetailStatus === "error") {
    const configIncomplete = commercialState.opportunitiesDetailErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar el detalle de oportunidades desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("opportunities-detail", "Reintentar la carga del detalle de oportunidades"));
  }
  // MISMA estructura que Licencias/Actividades (ver HANDOFF): sin título,
  // filtros primero, leyenda después.
  const rows = commercialDetalleFilteredRows();
  return `<div class="commercial-bucket1-panel">`
    + commercialDetalleFiltersHtml()
    + commercialUrgencyLegendHtml()
    + `<div data-commercial-detalle-table>${commercialDetalleTableHtml(rows)}</div>`
    + `</div>`;
}

// ── Historial de Perdidas (ver HANDOFF §5) ───────────────────────────────
// Universo/fila TOTALMENTE distintos de Licencias/Detalle — nunca comparte
// tabla/estado con ellas. Motivo/Fecha son propios de este contrato (MISMO
// criterio "sin dead fields" que ya aplica el KPI/drill Perdidas cerrado —
// ver `_build_lost_history`, sin Etapa/Revenue).
const COMMERCIAL_LOST_HISTORY_SORT_KEYS = {
  date: o => o.date_closed || "",
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  motivo: o => (o.motivo || "").toLowerCase(),
  hours: o => (o.hours != null && o.hours !== false) ? Number(o.hours) : -1,
};
function commercialLostHistoryAllRows() {
  return (commercialState.lostHistory && commercialState.lostHistory.opportunities) || [];
}
function commercialLostHistoryFilteredRows() {
  const all = commercialLostHistoryAllRows();
  const { histYearFilter: fYear, histSellerFilter: fSeller, histMotivoFilter: fMotivo } = commercialState;
  const search = (commercialState.histSearchText || "").toLowerCase().trim();
  return all.filter(o => {
    if (fYear && o.year !== fYear) return false;
    if (fSeller && o.seller !== fSeller) return false;
    if (fMotivo && o.motivo !== fMotivo) return false;
    if (search) {
      const hay = `${o.opportunity} ${o.client} ${o.motivo}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}
function commercialToggleLostHistorySort(key) {
  if (commercialState.histSortKey === key) commercialState.histSortDir *= -1;
  else { commercialState.histSortKey = key; commercialState.histSortDir = 1; }
}
function commercialLostHistoryClearFilters() {
  commercialState.histSearchText = "";
  commercialState.histYearFilter = "";
  commercialState.histSellerFilter = "";
  commercialState.histMotivoFilter = "";
  commercialRenderCapability();
}
function commercialLostHistoryFiltersHtml() {
  const all = commercialLostHistoryAllRows();
  const opt = (key, current, allLabel) => {
    const values = commercialOppDetailFilterOptions(all, key);
    return `<option value=""${current === "" ? " selected" : ""}>${allLabel}</option>` +
      values.map(v => `<option value="${commercialEsc(v)}"${current === v ? " selected" : ""}>${commercialEsc(v)}</option>`).join("");
  };
  // Año ordenado DESCENDENTE (más reciente primero) — MISMO criterio que el
  // legacy `_populateLostHistoryFilters` (`.sort().reverse()`).
  const years = [...new Set(all.map(o => o.year).filter(Boolean))].sort().reverse();
  const yearOpts = `<option value=""${commercialState.histYearFilter === "" ? " selected" : ""}>Todos</option>` +
    years.map(y => `<option value="${commercialEsc(y)}"${commercialState.histYearFilter === y ? " selected" : ""}>${commercialEsc(y)}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-historial-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-historial-search aria-label="Buscar en historial de perdidas"
          placeholder="Buscar…" value="${commercialEsc(commercialState.histSearchText)}">
      </label>
      <label class="whatsnew-filter">
        <span>Año</span>
        <select data-commercial-historial-year-filter>${yearOpts}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-historial-seller-filter>${opt("seller", commercialState.histSellerFilter, "Todos")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Motivo</span>
        <select data-commercial-historial-motivo-filter>${opt("motivo", commercialState.histMotivoFilter, "Todos")}</select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-historial-clear>✕ Limpiar</button>
    </div>`;
}
function commercialLostHistoryTableHtml(rows) {
  if (!rows.length) {
    return commercialEmptyStateHtml("Sin resultados", "Ninguna oportunidad perdida coincide con este filtro.");
  }
  const keyFn = commercialState.histSortKey && COMMERCIAL_LOST_HISTORY_SORT_KEYS[commercialState.histSortKey];
  const dir = commercialState.histSortDir;
  const sorted = keyFn ? [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  }) : rows;
  const th = (key, label, cls = "") => {
    const active = commercialState.histSortKey === key;
    const arrow = active ? (commercialState.histSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-historial-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => {
    const dateCell = o.date_closed
      ? `<span class="perspective-label" style="margin:0">${commercialEsc(o.year)}</span> ${commercialEsc(o.date_closed.slice(5).replace("-", "/"))}`
      : `<span class="left" style="font-style:italic">Sin fecha</span>`;
    const motivoBadge = o.motivo && o.motivo !== "Sin definir"
      ? `<span class="dashboard-status-badge dashboard-status-badge--red">${commercialEsc(o.motivo)}</span>`
      : `<span class="left">Sin definir</span>`;
    return `
    <tr>
      <td class="left">${dateCell}</td>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client) || "—"}</td>
      <td class="left">${commercialEsc(o.seller) || "—"}</td>
      <td class="left">${motivoBadge}</td>
      <td>${o.hours != null ? commercialFmtInt(o.hours) : "—"}</td>
    </tr>`;
  }).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:14%"><col style="width:26%"><col style="width:20%">
      <col style="width:16%"><col style="width:16%"><col style="width:8%"></colgroup>
    <thead><tr>
      ${th("date", "Fecha cierre", "left")}
      ${th("opportunity", "Oportunidad", "row-label-col")}
      ${th("client", "Cliente", "left")}
      ${th("seller", "Vendedor", "left")}
      ${th("motivo", "Motivo pérdida", "left")}
      ${th("hours", "Hs")}
    </tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table></div>`;
}
function commercialHistorialPerdidasHtml() {
  if (commercialState.lostHistoryStatus === "loading") {
    return commercialLoadingHtml("Cargando historial de perdidas…");
  }
  if (commercialState.lostHistoryStatus === "error") {
    const configIncomplete = commercialState.lostHistoryErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar el historial de perdidas desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("lost-history", "Reintentar la carga del historial de perdidas"));
  }
  // MISMA estructura que Licencias/Detalle/Actividades (ver HANDOFF): sin
  // título, filtros primero. SIN leyenda de urgencia — Historial no tiene
  // una dimensión de días/urgencia real (Fecha cierre/Motivo, no
  // Días resp.), así que no se inventa una que no corresponde al dato.
  const rows = commercialLostHistoryFilteredRows();
  return `<div class="commercial-bucket1-panel">`
    + commercialLostHistoryFiltersHtml()
    + `<div data-commercial-historial-table>${commercialLostHistoryTableHtml(rows)}</div>`
    + `</div>`;
}

// ═══════════════════════════════════════════════════════════════════════
// Bucket 2 (ver HANDOFF) — Leads / Clientes / Ex Clientes.
// ═══════════════════════════════════════════════════════════════════════

// ── Leads (ver HANDOFF §8-11) ─────────────────────────────────────────────
// Universo propio (`type=lead`) — NUNCA se confunde con Prospectos/
// Detalle/Licencias (esos son `type=opportunity`). Agrupado por empresa,
// fila expandible — MISMO mecanismo que Creadas (`creadasExpandedMonths`:
// array de claves abiertas, toggle por índice, re-render de panel
// completo — nunca un `<details>` nuevo ni un segundo motor de expandir).
function commercialLeadsAllCompanies() {
  return (commercialState.leads && commercialState.leads.companies) || [];
}
function commercialLeadsFilteredCompanies() {
  const all = commercialLeadsAllCompanies();
  const search = (commercialState.leadsSearchText || "").toLowerCase().trim();
  if (!search) return all;
  return all.filter(c =>
    commercialEsc(c.name).toLowerCase().includes(search) || (c.name || "").toLowerCase().includes(search)
    || c.leads.some(l => (l.lead || "").toLowerCase().includes(search) || (l.contact || "").toLowerCase().includes(search)));
}
function commercialLeadsClearFilters() {
  commercialState.leadsSearchText = "";
  commercialRenderCapability();
}
function commercialToggleLeadsSort(key) {
  if (commercialState.leadsSortKey === key) commercialState.leadsSortDir *= -1;
  else { commercialState.leadsSortKey = key; commercialState.leadsSortDir = 1; }
}
const COMMERCIAL_LEADS_SORT_KEYS = {
  name: c => (c.name || "").toLowerCase(),
  count: c => c.leads.length,
};
function commercialLeadsFiltersHtml() {
  return `<div class="whatsnew-filters" data-commercial-leads-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-leads-search aria-label="Buscar empresa o lead"
          placeholder="Buscar…" value="${commercialEsc(commercialState.leadsSearchText)}">
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-leads-clear>✕ Limpiar</button>
    </div>`;
}
function commercialLeadsTableHtml(companies) {
  if (!companies.length) return commercialEmptyStateHtml("Sin resultados", "Ninguna empresa/lead coincide con este filtro.");
  const keyFn = commercialState.leadsSortKey && COMMERCIAL_LEADS_SORT_KEYS[commercialState.leadsSortKey];
  const dir = commercialState.leadsSortDir;
  const sorted = keyFn ? [...companies].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  }) : companies;
  const th = (key, label, cls = "") => {
    const active = commercialState.leadsSortKey === key;
    const arrow = active ? (commercialState.leadsSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-leads-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rows = sorted.map(c => {
    const key = `${c.id}_${c.name}`;
    const isOpen = commercialState.leadsExpanded.indexOf(key) !== -1;
    const chevron = isOpen ? "▼" : "▶";
    const inner = isOpen ? `
      <tr class="is-total">
        <td></td>
        <td colspan="2">
          <div class="matrix-scroll"><table class="dashboard-table commercial-leads-inner">
            <thead><tr>
              <th class="row-label-col">Lead</th><th class="left">Nombre y Apellido</th>
              <th class="left">Email</th><th class="left">Vendedor</th><th>Fecha</th>
            </tr></thead>
            <tbody>${c.leads.map(l => `
              <tr>
                <td class="row-label">${commercialEsc(l.lead)}</td>
                <td class="left">${commercialEsc(l.contact) || "—"}</td>
                <td class="left">${l.email ? `<a href="mailto:${commercialEsc(l.email)}">${commercialEsc(l.email)}</a>` : "—"}</td>
                <td class="left">${commercialEsc(l.seller) || "—"}</td>
                <td>${l.create_date ? commercialEsc(commercialFmtDate(l.create_date)) : "—"}</td>
              </tr>`).join("")}
            </tbody>
          </table></div>
        </td>
      </tr>` : "";
    return `
    <tr class="commercial-leads-company-row" data-commercial-leads-toggle="${commercialEsc(key)}" role="button" tabindex="0">
      <td class="commercial-leads-chevron">${chevron}</td>
      <td class="row-label">${commercialEsc(c.name)}</td>
      <td>${commercialFmtInt(c.leads.length)}</td>
    </tr>${inner}`;
  }).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:4%"><col style="width:70%"><col style="width:26%"></colgroup>
    <thead><tr>
      <th></th>
      ${th("name", "Empresa", "row-label-col")}
      ${th("count", "Leads")}
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}
function commercialLeadsStatsHtml() {
  const data = commercialState.leads;
  if (!data) return "";
  const noCompany = (data.companies || []).filter(c => c.name === "Sin empresa")
    .reduce((s, c) => s + c.leads.length, 0);
  return `<div class="activity-kpi-grid">
      ${commercialKpi("Total leads", commercialFmtInt(data.total_leads))}
      ${commercialKpi("Empresas", commercialFmtInt(data.total_companies))}
      ${commercialKpi("Sin empresa", commercialFmtInt(noCompany))}
    </div>`;
}
function commercialLeadsHtml() {
  if (commercialState.leadsStatus === "loading") {
    return commercialLoadingHtml("Cargando leads…");
  }
  if (commercialState.leadsStatus === "error") {
    const configIncomplete = commercialState.leadsErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los leads desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("leads", "Reintentar la carga de leads"));
  }
  const rows = commercialLeadsFilteredCompanies();
  return `<div class="commercial-bucket1-panel">`
    + commercialLeadsFiltersHtml()
    + commercialLeadsStatsHtml()
    + `<div data-commercial-leads-table>${commercialLeadsTableHtml(rows)}</div>`
    + `</div>`;
}

// ── Clientes / Ex Clientes (ver HANDOFF §4-7) ────────────────────────────
// Sub-tab LOCAL (`clientesSubTab`), MISMO patrón visual que el legacy
// `switchClientesSubTab` — no es una capability nueva, así que no ocupa
// una entrada propia en la nav. Ex Clientes es LAZY DENTRO de Clientes:
// se pide la primera vez que se abre ese sub-tab, nunca junto con
// Clientes (MISMO criterio "primer open -> una request" que el resto de
// Comercial).
function commercialClientesAllRows() {
  return (commercialState.clients && commercialState.clients.clients) || [];
}
function commercialClientesFilteredRows() {
  const all = commercialClientesAllRows();
  const { clientesSellerFilter: fSeller, clientesStatusFilter: fStatus } = commercialState;
  const search = (commercialState.clientesSearchText || "").toLowerCase().trim();
  return all.filter(c => {
    if (search && !(c.name || "").toLowerCase().includes(search)) return false;
    if (fSeller && (c.sellers || []).indexOf(fSeller) === -1) return false;
    if (fStatus === "active" && c.active.length === 0) return false;
    if (fStatus === "prospect" && (c.prospects.length === 0 || c.active.length > 0)) return false;
    if (fStatus === "none" && (c.active.length > 0 || c.prospects.length > 0)) return false;
    return true;
  });
}
function commercialToggleClientesSort(key) {
  if (commercialState.clientesSortKey === key) commercialState.clientesSortDir *= -1;
  else { commercialState.clientesSortKey = key; commercialState.clientesSortDir = 1; }
}
function commercialClientesClearFilters() {
  commercialState.clientesSearchText = "";
  commercialState.clientesSellerFilter = "";
  commercialState.clientesStatusFilter = "all";
  commercialRenderCapability();
}
const COMMERCIAL_CLIENTES_SORT_KEYS = {
  name: c => (c.name || "").toLowerCase(),
  seller: c => (c.sellers[0] || "").toLowerCase(),
  active: c => c.active.length,
  prospect: c => c.prospects.length,
};
function commercialClientesSellerOptions(rows) {
  return [...new Set(rows.flatMap(c => c.sellers || []))].sort((a, b) => a.localeCompare(b, "es"));
}
function commercialClientesFiltersHtml() {
  const all = commercialClientesAllRows();
  const sellerOpts = commercialClientesSellerOptions(all).map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.clientesSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  const statusOpt = (value, label) =>
    `<option value="${value}"${commercialState.clientesStatusFilter === value ? " selected" : ""}>${label}</option>`;
  return `<div class="whatsnew-filters" data-commercial-clientes-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-clientes-search aria-label="Buscar cliente"
          placeholder="Buscar…" value="${commercialEsc(commercialState.clientesSearchText)}">
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-clientes-seller-filter>
          <option value=""${commercialState.clientesSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <label class="whatsnew-filter">
        <span>Estado</span>
        <select data-commercial-clientes-status-filter>
          ${statusOpt("all", "Todos")}
          ${statusOpt("active", "Con activas (1–8)")}
          ${statusOpt("prospect", "Sólo prospectos")}
          ${statusOpt("none", "Sin oportunidades abiertas")}
        </select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-clientes-clear>✕ Limpiar</button>
    </div>`;
}
function commercialClientesStatsHtml() {
  const rows = commercialClientesAllRows();
  const withActive = rows.filter(c => c.active.length > 0).length;
  const onlyProspect = rows.filter(c => c.active.length === 0 && c.prospects.length > 0).length;
  return `<div class="activity-kpi-grid">
      ${commercialKpi("Total clientes", commercialFmtInt(rows.length))}
      ${commercialKpi("Con activas (1–8)", commercialFmtInt(withActive))}
      ${commercialKpi("Sólo prospectos (0)", commercialFmtInt(onlyProspect))}
    </div>`;
}
// Owner corrective (ver HANDOFF): la fila padre (cliente) trae DOS
// columnas — Activas/Prospectos — así que el detalle expandido las
// espeja, en vez de un separador de fila de texto ("Activas — etapas
// 1–8 (N)") para distinguir el grupo. Cada oportunidad cae en UNA sola
// columna (nunca ambas — `kind` viene del propio bucket de origen,
// `c.active`/`c.prospects`, mutuamente excluyentes) y la otra queda "—",
// MISMO criterio "—" que ya usa la fila padre para el bucket vacío.
function commercialClienteOppRowHtml(o, kind) {
  const prob = o.probability != null ? commercialFmtPercent(o.probability) : "—";
  const horas = o.hours ? `${commercialFmtInt(o.hours)} hs` : "—";
  const val = `${prob} · ${horas}`;
  return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.seller) || "—"}</td>
      <td class="left">${commercialEsc(o.stage)}</td>
      <td>${kind === "active" ? val : "—"}</td>
      <td>${kind === "prospect" ? val : "—"}</td>
    </tr>`;
}
function commercialClientesTableHtml(rows) {
  if (!rows.length) return commercialEmptyStateHtml("Sin resultados", "Ningún cliente coincide con este filtro.");
  const keyFn = commercialState.clientesSortKey && COMMERCIAL_CLIENTES_SORT_KEYS[commercialState.clientesSortKey];
  const dir = commercialState.clientesSortDir;
  const sorted = keyFn ? [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  }) : rows;
  const th = (key, label, cls = "") => {
    const active = commercialState.clientesSortKey === key;
    const arrow = active ? (commercialState.clientesSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-clientes-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const body = sorted.map(c => {
    const hasOpps = c.active.length > 0 || c.prospects.length > 0;
    const isOpen = hasOpps && commercialState.clientesExpanded.indexOf(c.id) !== -1;
    const chevron = !hasOpps ? "" : (isOpen ? "▼" : "▶");
    const licBadge = c.has_licencias
      ? `<span class="dashboard-status-badge dashboard-status-badge--grey">Licencias</span>` : "";
    const rowAttrs = hasOpps
      ? `data-commercial-clientes-toggle="${c.id}" role="button" tabindex="0" class="commercial-leads-company-row"` : "";
    let inner = "";
    if (isOpen) {
      const rows = c.active.map(o => commercialClienteOppRowHtml(o, "active")).join("")
        + c.prospects.map(o => commercialClienteOppRowHtml(o, "prospect")).join("");
      inner = `
        <tr class="is-total"><td></td><td colspan="3">
          <div class="matrix-scroll"><table class="dashboard-table commercial-leads-inner">
            <thead><tr><th class="row-label-col">Oportunidad</th><th class="left">Vendedor</th>
              <th class="left">Etapa</th><th>Activas</th><th>Prospectos</th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>
        </td></tr>`;
    }
    return `
    <tr ${rowAttrs}>
      <td class="commercial-leads-chevron">${chevron}</td>
      <td class="row-label">${commercialEsc(c.name)} ${licBadge}</td>
      <td class="left">${commercialEsc((c.sellers || []).join(", ")) || "—"}</td>
      <td>${c.active.length ? commercialFmtInt(c.active.length) : "—"}</td>
      <td>${c.prospects.length ? commercialFmtInt(c.prospects.length) : "—"}</td>
    </tr>${inner}`;
  }).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:4%"><col style="width:38%"><col style="width:28%">
      <col style="width:15%"><col style="width:15%"></colgroup>
    <thead><tr>
      <th></th>
      ${th("name", "Cliente", "row-label-col")}
      ${th("seller", "Vendedor", "left")}
      ${th("active", "Activas")}
      ${th("prospect", "Prospectos")}
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}
function commercialClientesSubPanelHtml() {
  if (commercialState.clientsStatus === "loading") return commercialLoadingHtml("Cargando clientes…");
  if (commercialState.clientsStatus === "error") {
    const configIncomplete = commercialState.clientsErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los clientes desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("clients", "Reintentar la carga de clientes"));
  }
  const rows = commercialClientesFilteredRows();
  return commercialClientesFiltersHtml() + commercialClientesStatsHtml()
    + `<div data-commercial-clientes-table>${commercialClientesTableHtml(rows)}</div>`;
}

// ── Ex Clientes (sub-tab) ─────────────────────────────────────────────────
function commercialExClientesAllRows() {
  return (commercialState.exClients && commercialState.exClients.clients) || [];
}
function commercialExClientesFilteredRows() {
  const all = commercialExClientesAllRows();
  const fSeller = commercialState.exClientesSellerFilter;
  const search = (commercialState.exClientesSearchText || "").toLowerCase().trim();
  return all.filter(c => {
    if (search && !(c.name || "").toLowerCase().includes(search)) return false;
    if (fSeller && (c.sellers || []).indexOf(fSeller) === -1) return false;
    return true;
  });
}
function commercialExClientesClearFilters() {
  commercialState.exClientesSearchText = "";
  commercialState.exClientesSellerFilter = "";
  commercialRenderCapability();
}
function commercialExClientesStatsHtml() {
  const rows = commercialExClientesAllRows();
  const withHistory = rows.filter(c => (c.active.length + c.prospects.length + (c.history || []).length) > 0).length;
  return `<div class="activity-kpi-grid">
      ${commercialKpi("Total ex clientes", commercialFmtInt(rows.length))}
      ${commercialKpi("Con historial de oportunidades", commercialFmtInt(withHistory))}
    </div>`;
}
function commercialExClientesFiltersHtml() {
  const all = commercialExClientesAllRows();
  const sellerOpts = commercialClientesSellerOptions(all).map(s =>
    `<option value="${commercialEsc(s)}"${commercialState.exClientesSellerFilter === s ? " selected" : ""}>${commercialEsc(s)}</option>`).join("");
  return `<div class="whatsnew-filters" data-commercial-ex-clientes-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-ex-clientes-search aria-label="Buscar ex cliente"
          placeholder="Buscar…" value="${commercialEsc(commercialState.exClientesSearchText)}">
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-ex-clientes-seller-filter>
          <option value=""${commercialState.exClientesSellerFilter === "" ? " selected" : ""}>Todos</option>
          ${sellerOpts}
        </select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-ex-clientes-clear>✕ Limpiar</button>
    </div>`;
}
function commercialExClientesTableHtml(rows) {
  if (!rows.length) return commercialEmptyStateHtml("Sin resultados", "Ningún ex cliente coincide con este filtro.");
  const body = rows.map(c => `
    <tr>
      <td class="row-label">${commercialEsc(c.name)}</td>
      <td class="left">${commercialEsc((c.sellers || []).join(", ")) || "—"}</td>
      <td>${c.active.length ? commercialFmtInt(c.active.length) : "—"}</td>
      <td>${c.prospects.length ? commercialFmtInt(c.prospects.length) : "—"}</td>
    </tr>`).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:40%"><col style="width:30%"><col style="width:15%"><col style="width:15%"></colgroup>
    <thead><tr>
      <th class="row-label-col">Cliente</th>
      <th class="left">Vendedor/es</th>
      <th>Opps. activas (1–8)</th>
      <th>Prospectos (0)</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}
function commercialExClientesSubPanelHtml() {
  if (commercialState.exClientsStatus === "loading") return commercialLoadingHtml("Cargando ex clientes…");
  if (commercialState.exClientsStatus === "error") {
    const configIncomplete = commercialState.exClientsErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar los ex clientes desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("ex-clients", "Reintentar la carga de ex clientes"));
  }
  const rows = commercialExClientesFilteredRows();
  return commercialExClientesFiltersHtml() + commercialExClientesStatsHtml()
    + `<div data-commercial-ex-clientes-table>${commercialExClientesTableHtml(rows)}</div>`;
}

function commercialClientesSubTabsHtml() {
  const tab = (id, label) => `<button type="button" class="${commercialState.clientesSubTab === id ? "is-active" : ""}"
      data-commercial-clientes-subtab="${id}">${label}</button>`;
  return `<div class="dashboard-view-switch" role="group" aria-label="Clientes / Ex Clientes">
      ${tab("clientes", "Clientes")}${tab("ex-clientes", "Ex Clientes")}
    </div>`;
}
function commercialClientesHtml() {
  const body = commercialState.clientesSubTab === "ex-clientes"
    ? commercialExClientesSubPanelHtml()
    : commercialClientesSubPanelHtml();
  return `<div class="commercial-bucket1-panel">${commercialClientesSubTabsHtml()}${body}</div>`;
}

// ── Analítica del Pipeline — detalle (ver HANDOFF) ───────────────────────
// CINCO tiles (Edad del pipeline/Tiempo por etapa/Conversión etapa a etapa/
// Tiempo de cierre/Estancadas) comparten UN SOLO fetch lazy
// (`/comercial/data/pipeline-analytics-detail`) — MISMO universo por debajo
// (activas + ganadas del año + historial de etapa), así que partirlo en
// cinco peticiones sería 5x el tráfico Odoo por la MISMA data. Cada uno
// abre su PROPIA sección (`data-breakdown` propio, para que el usuario
// pueda ir directo a la que le interesa desde su tile), pero las cinco
// leen del MISMO `commercialState.pipelineAnalyticsDetail` y comparten
// status/error — la PRIMERA que se abre dispara el fetch, las otras cuatro
// salen gratis. "Conversión de leads" es la excepción (universo propio, ver
// más abajo).
//
// Escalas de color NUEVAS y DISTINTAS de `commercialUrgencyColor` (0-7/8-15/
// 16-30/+30 días de PRÓXIMA actividad) — acá el significado de negocio es
// otro (antigüedad/tiempo en etapa/tiempo de cierre/días sin movimiento,
// portados 1:1 de los umbrales del legacy), así que mezclar la escala de
// Actividades sería mentir sobre qué está midiendo el color. El PRIMITIVO
// visual (`.dashboard-status-badge--{color}`) sí se reusa tal cual.
function commercialAgeColor(days) {
  if (days == null) return null;
  if (days <= 30) return "green";
  if (days <= 60) return "amber";
  if (days <= 90) return "orange";
  return "red";
}
function commercialStageTimeColor(days) {
  if (days == null) return null;
  if (days <= 14) return "green";
  if (days <= 30) return "amber";
  if (days <= 60) return "orange";
  return "red";
}
function commercialConversionRateColor(rate) {
  if (rate == null) return null;
  if (rate >= 60) return "green";
  if (rate >= 30) return "amber";
  return "red";
}
function commercialCloseTimeColor(days) {
  if (days == null) return null;
  if (days <= 30) return "green";
  if (days <= 90) return "amber";
  if (days <= 180) return "orange";
  return "red";
}
function commercialStagnantColor(days) {
  if (days == null) return null;
  if (days <= 60) return "amber";
  if (days <= 90) return "orange";
  return "red";
}
function commercialDaysBadge(days, color) {
  if (days == null) return "—";
  const badge = color
    ? `<span class="dashboard-status-badge dashboard-status-badge--${color}">${commercialFmtInt(days)}d</span>`
    : `${commercialFmtInt(days)}d`;
  return badge;
}

// Estado + fetch compartidos de las cinco sub-vistas.
function commercialPipelineAnalyticsSharedStateHtml() {
  if (commercialState.pipelineAnalyticsDetailStatus === "error") {
    const configIncomplete = commercialState.pipelineAnalyticsDetailErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar la analítica del pipeline desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("pipeline-analytics", "Reintentar la carga de la analítica del pipeline"));
  }
  if (commercialState.pipelineAnalyticsDetailStatus !== "ready") {
    return commercialLoadingHtml("Cargando analítica del pipeline…");
  }
  return null;
}

// — Edad del pipeline: tabla ordenable, MISMAS columnas que el legacy.
const COMMERCIAL_PIPELINE_AGE_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  stage: o => (o.stage || "").toLowerCase(),
  create_date: o => o.create_date || "",
  age_days: o => o.age_days ?? -Infinity,
};
function commercialPipelineAgeSorted(rows) {
  const keyFn = commercialState.pipelineAgeSortKey && COMMERCIAL_PIPELINE_AGE_SORT_KEYS[commercialState.pipelineAgeSortKey];
  if (!keyFn) return [...rows].sort((a, b) => b.age_days - a.age_days);  // default: legacy — más vieja primero
  const dir = commercialState.pipelineAgeSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}
function commercialTogglePipelineAgeSort(key) {
  if (commercialState.pipelineAgeSortKey === key) commercialState.pipelineAgeSortDir *= -1;
  else { commercialState.pipelineAgeSortKey = key; commercialState.pipelineAgeSortDir = 1; }
}
function commercialPipelineAgeBlockHtml(rows) {
  const sorted = commercialPipelineAgeSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.pipelineAgeSortKey === key;
    const arrow = active ? (commercialState.pipelineAgeSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-pipeline-age-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialEsc(o.stage)}</td>
      <td>${commercialFmtDate(o.create_date)}</td>
      <td>${commercialDaysBadge(o.age_days, commercialAgeColor(o.age_days))}</td>
    </tr>`).join("");
  const avg = sorted.length ? Math.round(sorted.reduce((s, o) => s + (o.age_days || 0), 0) / sorted.length) : 0;
  return `${commercialAgeLegendHtml()}<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:22%"><col style="width:18%"><col style="width:16%">
      <col style="width:14%"><col style="width:14%"><col style="width:16%"></colgroup>
    <thead><tr>${th("opportunity", "Oportunidad", "row-label-col")}${th("client", "Cliente", "left")}${th("seller", "Vendedor", "left")}
      ${th("stage", "Etapa", "left")}${th("create_date", "Creación")}${th("age_days", "Edad")}</tr></thead>
    <tbody>${rowsHtml}
      <tr class="dashboard-table-totals"><td colspan="5">Promedio (${commercialFmtInt(sorted.length)})</td><td>${commercialFmtInt(avg)}d</td></tr>
    </tbody>
  </table></div>`;
}
function commercialPipelineAgePanelBodyHtml() {
  const shared = commercialPipelineAnalyticsSharedStateHtml();
  if (shared) return shared;
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.pipeline_age.opportunities) || [];
  if (!rows.length) return commercialEmptyStateHtml("Sin datos", "No hay oportunidades activas en este momento.");
  return `<div data-commercial-pipeline-age-table>${commercialPipelineAgeBlockHtml(rows)}</div>`;
}
function commercialPipelineAgeCountLabel() {
  if (commercialState.pipelineAnalyticsDetailStatus !== "ready") return "";
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.pipeline_age.opportunities) || [];
  return `${commercialFmtInt(rows.length)} oportunidad(es)`;
}

// — Tiempo por etapa: tabla minimalista ordenable (owner corrective, ver
// HANDOFF — "no me sirve imprimir los valores... si no va a haber un
// header"). El ranking `.bar-row` mostraba Días Prom./Muestras SIN
// encabezado — acá cada valor tiene su columna con `<th>` real, MISMO
// primitivo `dashboard-table` que las otras tres sub-vistas con tabla. El
// gráfico se conserva pero "achicado": una barra angosta bajo el nombre de
// la etapa (ver `.commercial-pipeline-mini-bar` en commercial.css) en vez
// de una fila completa — el diseño/funcionalidad de comparar etapas de un
// vistazo se mantiene, sólo con menos protagonismo que antes.
const COMMERCIAL_STAGE_TIME_SORT_KEYS = {
  stage: r => (r.stage || "").toLowerCase(),
  avg_days: r => r.avg_days ?? -Infinity,
  sample_count: r => r.sample_count ?? -Infinity,
};
function commercialStageTimeSorted(rows) {
  const keyFn = commercialState.stageTimeSortKey && COMMERCIAL_STAGE_TIME_SORT_KEYS[commercialState.stageTimeSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.stageTimeSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}
function commercialToggleStageTimeSort(key) {
  if (commercialState.stageTimeSortKey === key) commercialState.stageTimeSortDir *= -1;
  else { commercialState.stageTimeSortKey = key; commercialState.stageTimeSortDir = 1; }
}
function commercialStageTimeBlockHtml(rows) {
  if (!rows.length) return commercialEmptyStateHtml("Sin datos", "No hay historial de etapas para este segmento.");
  const sorted = commercialStageTimeSorted(rows);
  const max = Math.max(1, ...rows.map(r => r.avg_days || 0));
  const th = (key, label, cls = "") => {
    const active = commercialState.stageTimeSortKey === key;
    const arrow = active ? (commercialState.stageTimeSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-stage-time-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(r => {
    const color = commercialStageTimeColor(r.avg_days);
    const pct = r.avg_days ? (r.avg_days / max * 100).toFixed(1) : 0;
    return `
    <tr>
      <td class="row-label">${commercialEsc(r.stage)}
        <div class="commercial-pipeline-mini-bar"><div class="bar-fill" style="width:${pct}%"></div></div>
      </td>
      <td>${commercialDaysBadge(r.avg_days, color)}</td>
      <td>${commercialFmtInt(r.sample_count)}</td>
    </tr>`;
  }).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table commercial-pipeline-mini-bar-table">
    <colgroup><col style="width:52%"><col style="width:26%"><col style="width:22%"></colgroup>
    <thead><tr>${th("stage", "Etapa", "row-label-col")}${th("avg_days", "Días Prom.")}${th("sample_count", "Muestras")}</tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table></div>`;
}
function commercialStageTimePanelBodyHtml() {
  const shared = commercialPipelineAnalyticsSharedStateHtml();
  if (shared) return shared;
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stage_time) || [];
  return `<div data-commercial-stage-time-table>${commercialStageTimeBlockHtml(rows)}</div>`;
}
function commercialStageTimeCountLabel() {
  if (commercialState.pipelineAnalyticsDetailStatus !== "ready") return "";
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stage_time) || [];
  const withData = rows.filter(r => r.avg_days != null).length;
  return `${commercialFmtInt(withData)} etapa(s) con datos`;
}

// — Conversión etapa a etapa: MISMO corrective — tabla minimalista
// ordenable con las columnas reales del legacy (Etapa/Entraron/Avanzaron/
// Tasa, ver HANDOFF — auditoría de `analytics-conversion-body`), gráfico
// achicado a una barra angosta bajo la etapa. La última etapa no tiene
// "siguiente" — se muestra sin barra/tasa, nunca un 0% inventado.
const COMMERCIAL_STAGE_CONVERSION_SORT_KEYS = {
  stage: r => (r.stage || "").toLowerCase(),
  entered: r => r.entered ?? -Infinity,
  advanced: r => r.advanced ?? -Infinity,
  rate: r => r.rate ?? -Infinity,
};
function commercialStageConversionSorted(rows) {
  const keyFn = commercialState.stageConversionSortKey && COMMERCIAL_STAGE_CONVERSION_SORT_KEYS[commercialState.stageConversionSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.stageConversionSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}
function commercialToggleStageConversionSort(key) {
  if (commercialState.stageConversionSortKey === key) commercialState.stageConversionSortDir *= -1;
  else { commercialState.stageConversionSortKey = key; commercialState.stageConversionSortDir = 1; }
}
function commercialStageConversionBlockHtml(rows) {
  if (!rows.length) return commercialEmptyStateHtml("Sin datos", "No hay historial de etapas para este segmento.");
  const sorted = commercialStageConversionSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.stageConversionSortKey === key;
    const arrow = active ? (commercialState.stageConversionSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-stage-conversion-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(r => {
    const hasRate = r.rate != null;
    const color = commercialConversionRateColor(r.rate);
    const pct = hasRate ? r.rate.toFixed(1) : 0;
    const value = hasRate
      ? `<span class="dashboard-status-badge dashboard-status-badge--${color || "grey"}">${commercialFmtPercent(r.rate)}</span>`
      : "—";
    return `
    <tr>
      <td class="row-label">${commercialEsc(r.stage)}
        <div class="commercial-pipeline-mini-bar"><div class="bar-fill" style="width:${pct}%"></div></div>
      </td>
      <td>${commercialFmtInt(r.entered)}</td>
      <td>${r.advanced != null ? commercialFmtInt(r.advanced) : "—"}</td>
      <td>${value}</td>
    </tr>`;
  }).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table commercial-pipeline-mini-bar-table">
    <colgroup><col style="width:40%"><col style="width:20%"><col style="width:20%"><col style="width:20%"></colgroup>
    <thead><tr>${th("stage", "Etapa", "row-label-col")}${th("entered", "Entraron")}${th("advanced", "Avanzaron")}${th("rate", "Tasa")}</tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table></div>`;
}
function commercialStageConversionPanelBodyHtml() {
  const shared = commercialPipelineAnalyticsSharedStateHtml();
  if (shared) return shared;
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stage_conversion) || [];
  return `<div data-commercial-stage-conversion-table>${commercialStageConversionBlockHtml(rows)}</div>`;
}
function commercialStageConversionCountLabel() {
  if (commercialState.pipelineAnalyticsDetailStatus !== "ready") return "";
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stage_conversion) || [];
  const withRate = rows.filter(r => r.rate != null);
  const min = withRate.length ? Math.min(...withRate.map(r => r.rate)) : null;
  return min != null ? `mínima: ${commercialFmtPercent(min)}` : "sin datos";
}

// — Tiempo de cierre: tabla ordenable, sólo ganadas del año.
const COMMERCIAL_CLOSE_TIME_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  days: o => o.days ?? -Infinity,
};
function commercialCloseTimeSorted(rows) {
  const keyFn = commercialState.closeTimeSortKey && COMMERCIAL_CLOSE_TIME_SORT_KEYS[commercialState.closeTimeSortKey];
  if (!keyFn) return [...rows].sort((a, b) => b.days - a.days);
  const dir = commercialState.closeTimeSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}
function commercialToggleCloseTimeSort(key) {
  if (commercialState.closeTimeSortKey === key) commercialState.closeTimeSortDir *= -1;
  else { commercialState.closeTimeSortKey = key; commercialState.closeTimeSortDir = 1; }
}
function commercialCloseTimeBlockHtml(rows) {
  const sorted = commercialCloseTimeSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.closeTimeSortKey === key;
    const arrow = active ? (commercialState.closeTimeSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-close-time-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td>${commercialDaysBadge(o.days, commercialCloseTimeColor(o.days))}</td>
    </tr>`).join("");
  const avg = sorted.length ? Math.round(sorted.reduce((s, o) => s + (o.days || 0), 0) / sorted.length) : 0;
  return `<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:34%"><col style="width:26%"><col style="width:24%"><col style="width:16%"></colgroup>
    <thead><tr>${th("opportunity", "Oportunidad", "row-label-col")}${th("client", "Cliente", "left")}${th("seller", "Vendedor", "left")}${th("days", "Días")}</tr></thead>
    <tbody>${rowsHtml}
      <tr class="dashboard-table-totals"><td colspan="3">Promedio (${commercialFmtInt(sorted.length)})</td><td>${commercialFmtInt(avg)}d</td></tr>
    </tbody>
  </table></div>`;
}
function commercialCloseTimePanelBodyHtml() {
  const shared = commercialPipelineAnalyticsSharedStateHtml();
  if (shared) return shared;
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.close_time.opportunities) || [];
  if (!rows.length) return commercialEmptyStateHtml("Sin datos", "No hay oportunidades ganadas este año.");
  return `<div data-commercial-close-time-table>${commercialCloseTimeBlockHtml(rows)}</div>`;
}
function commercialCloseTimeCountLabel() {
  if (commercialState.pipelineAnalyticsDetailStatus !== "ready") return "";
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.close_time.opportunities) || [];
  return `${commercialFmtInt(rows.length)} oportunidad(es) ganada(s)`;
}

// — Estancadas: tabla ordenable, MISMAS columnas que el legacy (incluye
// Prioridad + Última respuesta del cliente, además de Días estancada).
const COMMERCIAL_STAGNANT_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  stage: o => (o.stage || "").toLowerCase(),
  priority: o => Number(o.priority || 0),
  last_move_date: o => o.last_move_date || "",
  last_response: o => o.last_response || "",
  days_stagnant: o => o.days_stagnant ?? -Infinity,
};
function commercialStagnantSorted(rows) {
  const keyFn = commercialState.stagnantSortKey && COMMERCIAL_STAGNANT_SORT_KEYS[commercialState.stagnantSortKey];
  if (!keyFn) return [...rows].sort((a, b) => b.days_stagnant - a.days_stagnant);
  const dir = commercialState.stagnantSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}
function commercialToggleStagnantSort(key) {
  if (commercialState.stagnantSortKey === key) commercialState.stagnantSortDir *= -1;
  else { commercialState.stagnantSortKey = key; commercialState.stagnantSortDir = 1; }
}
function commercialStagnantBlockHtml(rows) {
  const sorted = commercialStagnantSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.stagnantSortKey === key;
    const arrow = active ? (commercialState.stagnantSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-stagnant-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => {
    const priorityStars = commercialEsc(COMMERCIAL_PRIORITY_STARS[o.priority] || "—");
    const priorityLabel = COMMERCIAL_PRIORITY_LABELS[o.priority] || "Sin prioridad";
    return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialEsc(o.stage)}</td>
      <td class="chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(priorityLabel)}">${priorityStars}${commercialChartTooltip(priorityLabel)}</td>
      <td>${commercialFmtDate(o.last_move_date)}</td>
      <td>${o.last_response ? commercialFmtDate(o.last_response) : "—"}</td>
      <td>${commercialDaysBadge(o.days_stagnant, commercialStagnantColor(o.days_stagnant))}</td>
    </tr>`;
  }).join("");
  const avg = sorted.length ? Math.round(sorted.reduce((s, o) => s + (o.days_stagnant || 0), 0) / sorted.length) : 0;
  return `${commercialStagnantLegendHtml()}<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:18%"><col style="width:14%"><col style="width:13%"><col style="width:12%">
      <col style="width:8%"><col style="width:12%"><col style="width:12%"><col style="width:11%"></colgroup>
    <thead><tr>${th("opportunity", "Oportunidad", "row-label-col")}${th("client", "Cliente", "left")}${th("seller", "Vendedor", "left")}
      ${th("stage", "Etapa", "left")}${th("priority", "Prior.")}${th("last_move_date", "Últ. mov.")}
      ${th("last_response", "Últ. resp. cliente")}${th("days_stagnant", "Días estanc.")}</tr></thead>
    <tbody>${rowsHtml}
      <tr class="dashboard-table-totals"><td colspan="7">Promedio (${commercialFmtInt(sorted.length)})</td><td>${commercialFmtInt(avg)}d</td></tr>
    </tbody>
  </table></div>`;
}
function commercialStagnantPanelBodyHtml() {
  const shared = commercialPipelineAnalyticsSharedStateHtml();
  if (shared) return shared;
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stagnant.opportunities) || [];
  if (!rows.length) return commercialEmptyStateHtml("Sin datos", "No hay oportunidades estancadas 🎉");
  return `<div data-commercial-stagnant-table>${commercialStagnantBlockHtml(rows)}</div>`;
}
function commercialStagnantCountLabel() {
  if (commercialState.pipelineAnalyticsDetailStatus !== "ready") return "";
  const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stagnant.opportunities) || [];
  return `${commercialFmtInt(rows.length)} oportunidad(es)`;
}

// ── "Conversión de Leads" — detalle (ver HANDOFF) ────────────────────────
// Universo TOTALMENTE distinto (crm.lead type=lead + historial de `type`) —
// endpoint propio, LAZY, nunca comparte fetch con las cinco de arriba.
//
// Owner corrective (post-trust-audit, ver HANDOFF): completa la paridad de
// atribución (Campaña→Medio→Origen) + las CINCO facetas/búsqueda del
// legacy sobre el MISMO endpoint (`/comercial/data/lead-conversion-detail`
// enriquecido, NUNCA un segundo endpoint) — la tabla combinada de las
// cuatro convertidas sigue siendo UNA sola (decisión de UI propia de este
// batch: el legacy usa seis pestañas separadas, acá se prefirió una tabla
// con columna "Resultado" — MISMA información, forma distinta, ver
// HANDOFF), pero ahora sortea/filtra sobre la MISMA fila que ya trae
// `outcome`/`campana`/`medio`/`origen` del backend — un solo dataset
// canónico, sin una segunda copia filtrada en paralelo.
const COMMERCIAL_LEAD_CONV_OUTCOME_COLOR = {
  Prospecto: "grey", Activa: "amber", Ganada: "green", Perdida: "red", Archivada: "grey",
};
function commercialLeadConversionAllRows() {
  const d = commercialState.leadConversionDetail;
  if (!d) return [];
  return ["prospect", "opp", "won", "lost_opp"].flatMap(bucket => d[bucket].opportunities || []);
}
// Facetas — MISMA composición AND y MISMO scope de búsqueda (todos los
// campos de la fila) que el legacy `_filterLeadConvTable`. Las opciones de
// cada `<select>` se derivan de TODAS las filas (no facetadas entre sí — a
// diferencia de Perdidas, acá no hay ranking/desglose cuyo eje propio haya
// que ignorar, así que no aplica "avoid self-filter collapse").
function commercialLeadConversionFilterOptions(rows, key) {
  return [...new Set(rows.map(r => r[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
}
function commercialLeadConversionFilteredRows() {
  const all = commercialLeadConversionAllRows();
  const { leadConversionCampanaFilter: fCampana, leadConversionMedioFilter: fMedio,
          leadConversionOrigenFilter: fOrigen, leadConversionSellerFilter: fSeller,
          leadConversionOutcomeFilter: fOutcome } = commercialState;
  const search = (commercialState.leadConversionSearchText || "").toLowerCase().trim();
  return all.filter(r => {
    if (fCampana && r.campana !== fCampana) return false;
    if (fMedio && r.medio !== fMedio) return false;
    if (fOrigen && r.origen !== fOrigen) return false;
    if (fSeller && r.seller !== fSeller) return false;
    // "Resultado" (owner follow-up, mid-smoke): NO hay relación 1:1 limpia
    // entre los 7 mini-KPI y esta tabla — "Aún en lead"/"Lead perdido"/
    // "Total leads" ni siquiera tienen filas acá (la tabla sólo muestra el
    // universo CONVERTIDO: prospect/opp/won/lost_opp, ver
    // `commercialLeadConversionAllRows`). Por eso NO son clicables; en su
    // lugar, faceta más sobre `outcome` (Prospecto/Activa/Ganada/Perdida/
    // Archivada) con el MISMO patrón AND que las otras cuatro.
    if (fOutcome && r.outcome !== fOutcome) return false;
    if (search) {
      const hay = Object.values(r).map(v => String(v ?? "").toLowerCase()).join(" ");
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}
const COMMERCIAL_LEAD_CONV_SORT_KEYS = {
  opportunity: o => (o.opportunity || "").toLowerCase(),
  client: o => (o.client || "").toLowerCase(),
  seller: o => (o.seller || "").toLowerCase(),
  stage: o => (o.stage || "").toLowerCase(),
  campana: o => (o.campana || "").toLowerCase(),
  medio: o => (o.medio || "").toLowerCase(),
  origen: o => (o.origen || "").toLowerCase(),
  outcome: o => o.outcome || "",
  convert_date: o => o.convert_date || "",
};
function commercialLeadConversionSorted(rows) {
  const keyFn = commercialState.leadConversionSortKey && COMMERCIAL_LEAD_CONV_SORT_KEYS[commercialState.leadConversionSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.leadConversionSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}
function commercialToggleLeadConversionSort(key) {
  if (commercialState.leadConversionSortKey === key) commercialState.leadConversionSortDir *= -1;
  else { commercialState.leadConversionSortKey = key; commercialState.leadConversionSortDir = 1; }
}
function commercialLeadConversionClearFilters() {
  commercialState.leadConversionCampanaFilter = "";
  commercialState.leadConversionMedioFilter = "";
  commercialState.leadConversionOrigenFilter = "";
  commercialState.leadConversionSellerFilter = "";
  commercialState.leadConversionOutcomeFilter = "";
  commercialState.leadConversionSearchText = "";
  commercialRenderLeadConversionPanel();
}
function commercialLeadConversionFiltersHtml() {
  const all = commercialLeadConversionAllRows();
  const opt = (key, current, allLabel) => {
    const values = commercialLeadConversionFilterOptions(all, key);
    return `<option value=""${current === "" ? " selected" : ""}>${allLabel}</option>` +
      values.map(v => `<option value="${commercialEsc(v)}"${current === v ? " selected" : ""}>${commercialEsc(v)}</option>`).join("");
  };
  return `<div class="whatsnew-filters" data-commercial-lead-conversion-filters>
      <label class="whatsnew-filter">
        <span>Buscar</span>
        <input type="search" data-commercial-lead-conversion-search aria-label="Buscar en conversión de leads"
          placeholder="Buscar…" value="${commercialEsc(commercialState.leadConversionSearchText)}">
      </label>
      <label class="whatsnew-filter">
        <span>Campaña</span>
        <select data-commercial-lead-conversion-campana-filter>${opt("campana", commercialState.leadConversionCampanaFilter, "Todas")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Medio</span>
        <select data-commercial-lead-conversion-medio-filter>${opt("medio", commercialState.leadConversionMedioFilter, "Todos")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Origen</span>
        <select data-commercial-lead-conversion-origen-filter>${opt("origen", commercialState.leadConversionOrigenFilter, "Todos")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Vendedor</span>
        <select data-commercial-lead-conversion-seller-filter>${opt("seller", commercialState.leadConversionSellerFilter, "Todos")}</select>
      </label>
      <label class="whatsnew-filter">
        <span>Resultado</span>
        <select data-commercial-lead-conversion-outcome-filter>${opt("outcome", commercialState.leadConversionOutcomeFilter, "Todos")}</select>
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-lead-conversion-clear>✕ Limpiar</button>
    </div>`;
}
function commercialLeadConversionTableHtml(rows) {
  if (!rows.length) {
    return commercialEmptyStateHtml("Sin resultados", "Ningún lead convertido coincide con este filtro.");
  }
  const sorted = commercialLeadConversionSorted(rows);
  const th = (key, label, cls = "") => {
    const active = commercialState.leadConversionSortKey === key;
    const arrow = active ? (commercialState.leadConversionSortDir === 1 ? " ▲" : " ▼") : "";
    const classes = ["dashboard-table-th--sortable", cls].filter(Boolean).join(" ");
    return `<th class="${classes}" data-commercial-lead-conversion-sort="${key}">${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const rowsHtml = sorted.map(o => {
    const color = COMMERCIAL_LEAD_CONV_OUTCOME_COLOR[o.outcome] || "grey";
    return `
    <tr>
      <td class="row-label">${commercialEsc(o.opportunity)}</td>
      <td class="left">${commercialEsc(o.client)}</td>
      <td class="left">${commercialEsc(o.seller)}</td>
      <td class="left">${commercialEsc(o.stage)}</td>
      <td class="left">${commercialEsc(o.campana) || "—"}</td>
      <td class="left">${commercialEsc(o.medio) || "—"}</td>
      <td class="left">${commercialEsc(o.origen) || "—"}</td>
      <td><span class="dashboard-status-badge dashboard-status-badge--${color}">${commercialEsc(o.outcome)}</span></td>
      <td>${commercialFmtDate(o.convert_date)}</td>
    </tr>`;
  }).join("");
  return `<div class="matrix-scroll"><table class="dashboard-table">
    <colgroup><col style="width:16%"><col style="width:13%"><col style="width:11%">
      <col style="width:11%"><col style="width:11%"><col style="width:10%"><col style="width:10%">
      <col style="width:9%"><col style="width:9%"></colgroup>
    <thead><tr>${th("opportunity", "Oportunidad", "row-label-col")}${th("client", "Cliente", "left")}${th("seller", "Vendedor", "left")}
      ${th("stage", "Etapa", "left")}${th("campana", "Campaña", "left")}${th("medio", "Medio", "left")}${th("origen", "Origen", "left")}
      ${th("outcome", "Resultado")}${th("convert_date", "Convertido el")}</tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table></div>`;
}
// Jerarquía de atribución Campaña→Medio→Origen (owner §4/§9/§13): MISMO
// primitivo `.bar-row` que el resto de Comercial (nunca un árbol/canvas
// nuevo) — tres niveles anidados vía indentación, la barra de cada nodo
// escalada contra el máximo de SUS hermanos (no contra el total global,
// así cada nivel se lee por su propia proporción). La medida es el conteo
// CRUDO por nodo — MISMA que el legacy (sin empresas/%, que el legacy
// tampoco muestra a este nivel).
// ── Mini KPIs (owner UX corrective, ver HANDOFF §2/§3) ───────────────────
// Reemplaza la única oración plana de resumen por SIETE tarjetas — MISMO
// primitivo `commercialRichKpi` que el dashboard principal (Hub KPI visual
// language, owner §3), envueltas en un wrapper propio SCOPEADO que sólo
// achica densidad (padding/gap) — nunca se toca `.activity-kpi` global.
//
// Contrato EXACTO recuperado del legacy (`_renderLeadConvPanel`,
// `panel-analytics-lead-conv` — ver HANDOFF): SIETE métricas, no seis — el
// legacy nunca le dio a "Perdidos sin conv." (`lc-lost-lead`, Lead perdido)
// su propia tarjeta de primer nivel (vivía anidada dentro de "Aún en
// lead"), lo que la hacía fácil de pasar por alto — pero es un estado de
// negocio genuinamente DISTINTO de "Perdidas" (`lc-lost-opp-co`,
// Oportunidad perdida: una convertida que se perdió DESPUÉS de pasar a
// oportunidad). Colapsarlas por simetría visual escondería la diferencia
// real entre "nunca llegó a ser oportunidad" y "llegó y se perdió" — owner
// decision explícita: preservar las siete.
//
// Cabecera vs línea secundaria — asimetría real del legacy, preservada tal
// cual: Total leads/Aún en lead/Lead perdido muestran el CONTEO CRUDO de
// registros (`total`/`still_lead.count`/`lost_lead.count`) con "empresas
// únicas" como secundaria; Convertidas/Avanzaron/Ganadas/Perdidas muestran
// el CONTEO DE EMPRESAS (`*.companies`) como cabecera — el legacy nunca
// usó el mismo tipo de número en las siete tarjetas.
function commercialLeadConversionKpisHtml(d) {
  const cards = [
    commercialRichKpi("Total leads", commercialFmtInt(d.total),
      { sub: `${commercialFmtInt(d.companies)} empresas únicas · base %` }),
    commercialRichKpi("Aún en lead", commercialFmtInt(d.still_lead.count),
      { sub: `${commercialFmtInt(d.still_lead.companies)} empresas únicas` }),
    commercialRichKpi("Lead perdido", commercialFmtInt(d.lost_lead.count),
      { sub: `${commercialFmtInt(d.lost_lead.companies)} empresas únicas` }),
    commercialRichKpi("Conv. a algo", commercialFmtInt(d.any_conv.companies),
      { sub: `${commercialFmtPercent(d.any_conv.rate)} · prospecto o más` }),
    commercialRichKpi("Avanzaron a opp.", commercialFmtInt(d.adv_opp.companies),
      { sub: `${commercialFmtPercent(d.adv_opp.rate)} · etapa 1 o más` }),
    commercialRichKpi("Ganadas", commercialFmtInt(d.won.companies),
      { sub: commercialFmtPercent(d.won.rate) }),
    commercialRichKpi("Perdidas", commercialFmtInt(d.lost_opp.companies),
      { sub: commercialFmtPercent(d.lost_opp.rate) }),
  ].join("");
  return `<div class="activity-kpi-grid commercial-lc-kpis">${cards}</div>`;
}

// ── Leads por año / Origen de marketing (owner UX corrective §5/§6) ──────
// Reemplazan el árbol Campaña→Medio→Origen como visual PRINCIPAL — el dato
// de atribución (`campana`/`medio`/`origen` por fila) sigue intacto, sólo
// cambia qué se muestra primero (owner §4: "changing PRESENTATION, not
// deleting the attribution contract").
//
// Leads por año — MISMA semántica que el legacy `leads_per_year`
// (`_renderLeadsPerYearChart`): universo COMPLETO (las seis poblaciones —
// aún-lead/lead-perdido/las cuatro convertidas, NUNCA sólo convertidas),
// agrupado por el AÑO de `create_date` (creación del registro, no de
// conversión), orden CRONOLÓGICO ascendente. Sin fecha → excluido en
// silencio (MISMO criterio que el legacy: `if yr and yr.isdigit()`), nunca
// un bucket "sin fecha" inventado. Cien por ciento client-side sobre el
// payload YA cargado — cero endpoint nuevo.
function commercialLeadConversionAllUniverseRows() {
  const d = commercialState.leadConversionDetail;
  if (!d) return [];
  return ["still_lead", "lost_lead", "prospect", "opp", "won", "lost_opp"]
    .flatMap(bucket => d[bucket].opportunities || []);
}
function commercialLeadConversionYearBuckets() {
  const counts = {};
  commercialLeadConversionAllUniverseRows().forEach(r => {
    const yr = (r.create_date || "").slice(0, 4);
    if (!/^\d{4}$/.test(yr)) return;
    counts[yr] = (counts[yr] || 0) + 1;
  });
  return Object.keys(counts).sort().map(year => ({ year, count: counts[year] }));
}
function commercialLeadConversionYearRankingHtml() {
  const buckets = commercialLeadConversionYearBuckets();
  const label = `<p class="perspective-label">Leads por año</p>`;
  if (!buckets.length) return label + commercialEmptyStateHtml("Sin datos", "No hay fecha de creación para este segmento.");
  const max = Math.max(1, ...buckets.map(b => b.count));
  const rows = buckets.map(b => `
    <div class="bar-row">
      <span class="bar-label">${commercialEsc(b.year)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(b.count / max * 100).toFixed(1)}%"></div></div>
      <span class="bar-value">${commercialFmtInt(b.count)}</span>
    </div>`).join("");
  return label + rows;
}

// Origen de marketing — MISMA semántica que el legacy `origen_summary`
// (NO `utm_hierarchy`: ese árbol nunca calculó porcentaje por nodo — los
// ejemplos del owner con "|  %" sólo pueden salir de `origen_summary`,
// que SÍ lo trae — prueba desde la forma del dato, no del rótulo). Universo
// SOLO las cuatro convertidas (`prospect_ids`+`opp`+`won`+`lost_opp` —
// MISMO `all_conv` que ya usa `commercialLeadConversionAllRows`, nunca
// aún-lead/lead-perdido). Denominador = conteo CRUDO de esas filas (MISMO
// `n_total_conv` del legacy, NUNCA empresas). Ausencia de origen →
// "Sin definir" (MISMO placeholder que el resto del Hub — DISTINTO de
// "Sin origen", que es sólo de la jerarquía retirada). Orden por conteo
// descendente (ranking), no cronológico.
function commercialLeadConversionOriginBuckets() {
  const rows = commercialLeadConversionAllRows();
  const total = rows.length;
  const counts = {};
  rows.forEach(r => {
    const key = r.origen || "Sin definir";
    counts[key] = (counts[key] || 0) + 1;
  });
  return Object.entries(counts)
    .map(([origen, count]) => ({ origen, count, pct: total ? Math.round(count / total * 1000) / 10 : 0 }))
    .sort((a, b) => b.count - a.count);
}
function commercialLeadConversionOriginRankingHtml() {
  const buckets = commercialLeadConversionOriginBuckets();
  const label = `<p class="perspective-label">Origen de marketing — oportunidades generadas</p>`;
  if (!buckets.length) return label + commercialEmptyStateHtml("Sin datos", "No hay oportunidades convertidas para este segmento.");
  const max = Math.max(1, ...buckets.map(b => b.count));
  // Owner follow-up (cambió de opinión mid-smoke, ver HANDOFF): "count ·
  // pct%" en el `bar-value` conflictuaba con cómo se vienen presentando el
  // resto de los rankings del Hub (sólo el conteo crudo). El % sigue
  // disponible, ahora como tooltip mouseover sobre la barra — MISMO
  // primitivo compartido `.chart-tooltip-anchor`/`commercialChartTooltip`
  // que ya usa el resto de Comercial (Funnel, columna Prior.), cero
  // mecanismo nuevo.
  const rows = buckets.map(b => `
    <div class="bar-row">
      <span class="bar-label">${commercialEsc(b.origen)}</span>
      <div class="bar-track chart-tooltip-anchor" tabindex="0" aria-label="${commercialEsc(commercialFmtPercent(b.pct))} del total convertido">
        <div class="bar-fill" style="width:${(b.count / max * 100).toFixed(1)}%"></div>
        ${commercialChartTooltip(`${commercialFmtPercent(b.pct)} del total convertido`)}
      </div>
      <span class="bar-value">${commercialFmtInt(b.count)}</span>
    </div>`).join("");
  return label + rows;
}

// LADO A LADO — reusa `.activity-grid-2` (primitivo Hub YA promovido,
// MISMO que usa Perdidas para Ranking|Motivo y Reporting para
// Mantenimiento|Proyecto) — cero grilla nueva. Ninguno de los dos responde
// a las facetas de más abajo — MISMO criterio que el legacy (los
// paneles de año/origen/UTM se pintan UNA vez por carga, nunca los
// re-agrega `_filterLeadConvTable`, que sólo toca la tabla de detalle) —
// sí responden al Tipo global, porque leen del MISMO
// `commercialState.leadConversionDetail` que ya está scopeado por Tipo.
function commercialLeadConversionChartsHtml() {
  return `<div class="commercial-lc-analysis"><div class="activity-grid-2">
      <div class="commercial-lc-year-ranking">${commercialLeadConversionYearRankingHtml()}</div>
      <div class="commercial-lc-origin-ranking">${commercialLeadConversionOriginRankingHtml()}</div>
    </div></div>`;
}
function commercialLeadConversionAnalysisHtml() {
  const d = commercialState.leadConversionDetail;
  return commercialLeadConversionKpisHtml(d)
    + commercialLeadConversionChartsHtml()
    + commercialLeadConversionFiltersHtml()
    + `<div data-commercial-lead-conversion-table>${commercialLeadConversionTableHtml(commercialLeadConversionFilteredRows())}</div>`;
}
function commercialLeadConversionPanelBodyHtml() {
  if (commercialState.leadConversionDetailStatus === "error") {
    const configIncomplete = commercialState.leadConversionDetailErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos cargar la conversión de leads desde el CRM en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("lead-conversion", "Reintentar la carga de conversión de leads"));
  }
  if (commercialState.leadConversionDetailStatus !== "ready") {
    return commercialLoadingHtml("Cargando conversión de leads…");
  }
  const d = commercialState.leadConversionDetail;
  if (!d || !d.companies) {
    return commercialEmptyStateHtml("Sin datos", "No hay leads para este segmento.");
  }
  return commercialLeadConversionAnalysisHtml();
}
function commercialLeadConversionCountLabel() {
  if (commercialState.leadConversionDetailStatus !== "ready") return "";
  const d = commercialState.leadConversionDetail;
  return d ? `${commercialFmtInt(d.companies)} empresa(s)` : "";
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
        <thead><tr><th class="left">Origen</th><th>Opps.</th><th>Distribución</th><th>% del total</th></tr></thead>
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
// Texto buscable de una actividad — MISMO criterio que
// `commercialOpportunitySearchableText`: se busca sobre lo que la fila
// MUESTRA (incluida la fecha formateada y el relativo "venc. 5d"), no sobre
// los campos crudos, para que buscar lo que se ve funcione siempre.
function commercialActivitySearchableText(act) {
  const days = commercialDaysUntil(act.deadline);
  const rel = days === null ? "" : days < 0 ? `venc. ${Math.abs(days)}d` : days === 0 ? "hoy" : `${days}d`;
  return [act.type, act.opportunity, act.client, act.user, act.summary, act.note,
    commercialFmtDate(act.deadline), rel].filter(Boolean).join(" ").toLowerCase();
}

// Claves de orden — MISMA mecánica que `COMMERCIAL_OPP_SORT_KEYS`. Fecha
// ordena por `days` (el relativo, que es la señal de triage) y no por el
// string de la fecha; sin fecha va al final en ascendente, nunca antes que
// una actividad con vencimiento real.
const COMMERCIAL_ACT_SORT_KEYS = {
  deadline: a => (a.days != null ? Number(a.days) : Infinity),
  type: a => (a.type || "").toLowerCase(),
  opportunity: a => (a.opportunity || "").toLowerCase(),
  client: a => (a.client || "").toLowerCase(),
  user: a => (a.user || "").toLowerCase(),
  detail: a => [a.summary, a.note].filter(Boolean).join(" ").toLowerCase(),
};

function commercialToggleActSort(key) {
  if (commercialState.actSortKey === key) {
    commercialState.actSortDir *= -1;
  } else {
    commercialState.actSortKey = key;
    commercialState.actSortDir = 1;
  }
}

// Orden client-side puro, y SIEMPRE dentro de cada bucket: Retrasadas y
// Pendientes son dos tablas distintas, ordenar cruzándolas no significaría
// nada. Copia el array antes de ordenar — `.sort` muta.
function commercialActivitiesSorted(rows) {
  const keyFn = commercialState.actSortKey && COMMERCIAL_ACT_SORT_KEYS[commercialState.actSortKey];
  if (!keyFn) return rows;
  const dir = commercialState.actSortDir;
  return [...rows].sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function commercialFilteredActivities() {
  const all = (commercialState.activities && commercialState.activities.activities) || [];
  const period = commercialState.actPeriod;
  const user = commercialState.actUser;
  const query = (commercialState.actSearchText || "").trim().toLowerCase();
  const since = commercialDaysSinceMonday();
  const toEnd = commercialDaysToEndOfWeek();
  return all.filter(act => {
    if (user && act.user !== user) return false;
    if (query && !commercialActivitySearchableText(act).includes(query)) return false;
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
  // Cabeceras ORDENABLES — MISMO primitivo y MISMO contrato que el detalle de
  // Oportunidades (`.dashboard-table-th--sortable`, tools.css): comportamiento
  // transversal del Hub, no un mecanismo propio de esta tabla.
  const th = (key, label) => {
    const active = commercialState.actSortKey === key;
    const arrow = active ? (commercialState.actSortDir === 1 ? " ▲" : " ▼") : "";
    return `<th class="left dashboard-table-th--sortable" data-commercial-act-sort="${key}">`
      + `${commercialEsc(label)}${commercialEsc(arrow)}</th>`;
  };
  const body = commercialActivitiesSorted(rows).map(act => {
    // El relativo va como BADGE de color (C9), MISMO criterio que "Próx.
    // Acción" en el detalle de Oportunidades: la magnitud de `days` —no el
    // signo— define el tono (`commercialUrgencyColor`), porque una actividad
    // vencida hace 3 días es tan urgente como una que vence en 3. La fecha
    // queda como texto plano antes del badge, igual que allá. Sin fecha no hay
    // tono que asignar, así que no hay badge.
    const rel = act.days === null ? ""
      : act.days < 0 ? `venc. ${Math.abs(act.days)}d`
      : act.days === 0 ? "hoy" : `${act.days}d`;
    const color = commercialUrgencyColor(act.days);
    const badge = rel && color
      ? ` · <span class="dashboard-status-badge dashboard-status-badge--${color}">${commercialEsc(rel)}</span>`
      : "";
    const detail = [act.summary, act.note].filter(Boolean).join(" · ") || "—";
    return `<tr>
      <td class="left">${commercialEsc(commercialFmtDate(act.deadline))}${badge}</td>
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
  // Fecha va primera porque el relativo ("venc. 12d") es la señal de triage.
  //
  // REPARTO recalibrado con medición real (owner smoke: "sobra mucho espacio en
  // la columna fecha, prefiero que ese espacio lo gane Oportunidad"). Con
  // `table-layout:fixed` el navegador reparte el sobrante en PROPORCIÓN a los
  // anchos declarados, así que lo que manda es la RELACIÓN entre ellos, no el
  // valor absoluto. Medido sobre una tabla de 1231px: Fecha recibía 290px y su
  // contenido más largo ("03/06/2026 · venc. 80d") pide 180 — 110px de aire —
  // mientras Oportunidad recibía 235 pidiendo 485, con 5 de 9 filas truncadas.
  // Fecha baja de 160 a 105 y Oportunidad sube de 130 a 185: la suma queda
  // IGUAL (680px), así que el piso de scroll en mobile no cambia, y en ancho
  // real Fecha aterriza en ~190 (justo lo que necesita, con un margen para un
  // "venc. 100d") y Oportunidad gana ~100px. Sigue sin entrar todo —el total
  // pedido por las seis columnas supera cualquier panel—, pero el espacio va
  // ahora a la columna que el owner marcó como la más importante.
  return `<div class="matrix-scroll">
      <table class="dashboard-table">
        <colgroup>
          <col style="width:105px"><col style="width:70px"><col style="width:185px">
          <col style="width:80px"><col style="width:90px"><col style="width:150px">
        </colgroup>
        <thead><tr>${th("deadline", "Fecha")}${th("type", "Tipo")}${th("opportunity", "Oportunidad")}${th("client", "Cliente")}${th("user", "Responsable")}${th("detail", "Detalle")}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

// Buscar + "✕ Limpiar" (C9): MISMOS primitivos que la barra de Activas
// (`.whatsnew-filters`/`--fill` y el `.whatsnew-filter__clear` compartido del
// Hub) — un solo lenguaje de filtros en toda la vertical, cero clase nueva.
// "Buscar" es `--fill` y se come el espacio del medio, así que Limpiar queda
// pegado a la derecha sin posicionamiento propio.
//
// A diferencia de Activas, acá Limpiar SÍ regenera la barra (vía
// `commercialRenderCapability`): "Período" es un grupo de botones cuyo estado
// activo vive en el markup, así que un reset quirúrgico tendría que tocar
// cuatro botones + un select + un input. Regenerar es más simple y menos
// frágil, y un click no tiene el problema de foco que sí tiene teclear.
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
      <label class="whatsnew-filter whatsnew-filter--fill">
        <span>Buscar</span>
        <input type="search" data-commercial-act-search aria-label="Buscar actividades" placeholder="Buscar…" value="${commercialEsc(commercialState.actSearchText)}">
      </label>
      <button type="button" class="whatsnew-filter__clear" data-commercial-act-clear>✕ Limpiar</button>
    </div>`;
}

// Owner follow-up (ver HANDOFF): el título de esta capability se sacó (sólo
// queda Período + su botonera) — el wrapper propio (`.commercial-act-panel`)
// es lo que le da a `.whatsnew-filters` su top margin scopeado
// (commercial.css), ya que perdió el margen inferior que antes le daba
// gratis el `<p class="perspective-label">` de arriba.
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
  return `<div class="commercial-act-panel">
    ${commercialActivitiesControlsHtml()}
    <div data-commercial-activities-body>${commercialActivitiesBodyHtml()}</div>
  </div>`;
}

// KPIs + leyenda + las dos secciones, en UN host estable (C9): la búsqueda y
// el orden lo re-renderizan QUIRÚRGICAMENTE, sin tocar la barra de controles
// — si regeneraran el `<input>` en cada tecla, el foco se perdería después
// del primer carácter (MISMO motivo por el que la barra de Activas nunca se
// regenera al buscar). Período/Responsable sí siguen re-renderizando el panel
// entero: son un click y un `change`, y así la barra refleja su propio estado.
function commercialActivitiesBodyHtml() {
  const rows = commercialFilteredActivities();
  const buckets = commercialActivityBuckets(rows);
  // KPIs: los TRES que los datos sostienen directamente — "Por vencer" se
  // sumó (owner follow-up, ver HANDOFF) para que el total (155) se lea junto
  // a sus dos partes (23 retrasadas + 132 por vencer), MISMO desglose que ya
  // hacen las dos secciones de abajo, nunca un tercer conteo independiente.
  // Reflejan el conjunto FILTRADO, para que nunca contradigan los
  // contadores de las secciones. "Retrasadas"/"Por vencer" son clickeables
  // (llevan a su `<details>` ya visible más abajo) — "Total pendientes" no,
  // porque no tiene una sección propia a la que saltar.
  const kpis = `<div class="activity-kpi-grid">
      ${commercialKpi("Total pendientes", commercialFmtInt(rows.length))}
      ${commercialActClickableKpi("Retrasadas", commercialFmtInt(buckets.overdue.length), "act-retrasadas")}
      ${commercialActClickableKpi("Por vencer", commercialFmtInt(buckets.pending.length), "act-pendientes")}
    </div>`;
  // Leyenda de urgencia UNA sola vez, arriba de las dos secciones: los badges
  // de las dos tablas usan la misma escala, así que repetirla por sección
  // sería ruido. Sólo con filas — nunca una leyenda sobre la nada, MISMO
  // criterio que `commercialOpportunitiesBlockHtml`.
  const legend = rows.length ? commercialUrgencyLegendHtml() : "";
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
  // subconjunto, no el total.
  const sections = commercialStaticSection("act-retrasadas", "Retrasadas",
      `${commercialFmtInt(buckets.overdue.length)} actividad(es)`, commercialActivityTableHtml(buckets.overdue))
    + commercialStaticSection("act-pendientes", "Por vencer",
      `${commercialFmtInt(buckets.pending.length)} actividad(es)`, commercialActivityTableHtml(buckets.pending));
  return `${kpis}${legend}<div>${sections}</div>`;
}

// Re-render quirúrgico del cuerpo de Actividades — nunca del panel entero.
function commercialRenderActivitiesBody() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const host = root.querySelector("[data-commercial-activities-body]");
  if (host) host.innerHTML = commercialActivitiesBodyHtml();
}

// "Creadas esta semana" (tile del Resumen) — owner corrective "SCOPED
// RETRY... FINAL DEPENDENCY CHECK" (ver HANDOFF): auditado a mano contra el
// backend (`CommercialService.get_analytics`, `_view()`) — `creadas` sale
// del MISMO `_view()` que arma `pipeline_age`/`stage_time`/etc., dentro del
// MISMO payload `/comercial/data/analytics`. NO es un fetch propio (a
// diferencia de lo que su propio `eventKey: "creadas"` podría sugerir — ESE
// es el drill de detalle, `/comercial/data/creadas`, un endpoint totalmente
// distinto para el ANÁLISIS al abrir el tile, no para el número que muestra
// cerrado). Por eso este tile degrada a "—" exactamente cuando `analytics`
// es null, igual que los seis tiles de abajo — y por eso un reintento
// exitoso de Analíticas tiene que refrescarlo también: es el MISMO dato, no
// un placeholder que sobrevive a propósito.
//
// Hook de re-render vía `hostAttr` (owner smoke — el tile se veía
// "achicado", ver HANDOFF), NUNCA un `<div>` wrapper alrededor: ver el
// comentario de `commercialRichKpi` — un wrapper sin estilo propio rompe el
// `align-items: stretch` implícito de `.activity-kpi-grid` (CSS Grid), que
// hoy es lo que le da a este tile la MISMA altura que sus ocho hermanos
// pese a tener menos líneas de texto (una sola: "Semana Anterior", contra
// las tres de hs/NN/BI del resto). `eventKey`/`split` siguen sin depender
// de `a` fuera del propio valor mostrado, MISMO criterio que siempre (el
// drill puede abrir igual aunque el número esté en "—").
function commercialCreadasSummaryTileHtml() {
  const a = commercialState.analytics;
  return commercialRichKpi("Creadas esta semana", a ? commercialFmtInt(a.creadas.this_week) : "—",
    { split: a ? commercialKpiPairsHtml([["Semana Anterior", commercialFmtInt(a.creadas.last_week)]]) : "",
      eventKey: "creadas", hostAttr: "data-commercial-creadas-summary-tile" });
}

// Re-render QUIRÚRGICO de "Creadas esta semana" — SÓLO su host
// (`data-commercial-creadas-summary-tile`, hoy el `.activity-kpi` mismo),
// nunca el resto de Resumen: los otros ocho tiles no dependen de
// `analytics`, así que no tienen nada que recuperar. `outerHTML`, no
// `innerHTML`: el host YA ES el `.activity-kpi` completo (no un wrapper
// vacío alrededor), así que hay que reemplazar el elemento entero, no
// meter uno nuevo adentro del viejo. Llamado desde `commercialLoadAnalytics`
// junto con `commercialRenderAnalyticsBody` — MISMA respuesta ya en
// memoria, cero fetch adicional.
function commercialRenderCreadasSummaryTile() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const host = root.querySelector("[data-commercial-creadas-summary-tile]");
  if (host) host.outerHTML = commercialCreadasSummaryTileHtml();
}

// "Analíticas del Pipeline" — owner corrective "SCOPED RETRY FOR PIPELINE
// ANALYTICS" (ver HANDOFF): TRES estados sobre `analyticsStatus`, MISMO
// contrato que Forecast/Creadas/pipeline-analytics-detail (loading →
// "Cargando…"; error → "No disponible" + Reintentar, sólo si NO es
// `config_incomplete`; ready → los seis tiles con drilldown propio, sin
// tocar sus fórmulas). `loading` sólo ocurre durante un reintento explícito
// — la carga inicial (`commercialLoadData`) nunca pasa por acá en loading,
// va directo a ready|error junto con el resto del landing.
function commercialAnalyticsBodyHtml() {
  if (commercialState.analyticsStatus === "loading") {
    return commercialLoadingHtml("Cargando analíticas…");
  }
  if (commercialState.analyticsStatus === "error") {
    const configIncomplete = commercialState.analyticsErrorState === "config_incomplete";
    const label = configIncomplete
      ? "El CRM todavía no está configurado para este espacio."
      : "No pudimos calcular las analíticas del pipeline en este momento.";
    return commercialEmptyStateHtml("No disponible", label,
      configIncomplete ? "" : commercialRetryButtonHtml("analytics", "Reintentar la carga de la analítica del pipeline"));
  }
  const a = commercialState.analytics;
  if (!a) return commercialEmptyStateHtml("No disponible", "No pudimos calcular las analíticas del pipeline en este momento.");
  // Los seis titulares ganan drilldown propio — cinco comparten un fetch
  // (`pipeline_age`/`stage_time`/`stage_conversion`/`close_time`/`stagnant`,
  // MISMO universo por debajo) y "Conversión de leads" tiene el suyo
  // (universo propio, `crm.lead type=lead`).
  return `<div class="activity-kpi-grid analytics-grid">
      ${commercialRichKpi("Edad del pipeline", a.pipeline_age.avg_days != null ? `${a.pipeline_age.avg_days}d` : "—",
        { sub: `${commercialFmtInt(a.pipeline_age.count)} oportunidad(es) activa(s)`, eventKey: "pipeline_age" })}
      ${commercialRichKpi("Tiempo por etapa", a.stage_time.avg_days != null ? `${a.stage_time.avg_days}d` : "—",
        { sub: a.stage_time.stages_with_data ? `${commercialFmtInt(a.stage_time.stages_with_data)} etapa(s) con datos` : "sin datos de historial",
          eventKey: "stage_time" })}
      ${commercialRichKpi("Conversión etapa a etapa", a.stage_conversion.min_rate != null ? `${a.stage_conversion.min_rate}%` : "—",
        { sub: a.stage_conversion.min_rate != null ? "tasa mínima (cuello de botella)" : "sin datos", eventKey: "stage_conversion" })}
      ${commercialRichKpi("Tiempo de cierre", a.close_time.avg_days != null ? `${a.close_time.avg_days}d` : "—",
        { sub: `${commercialFmtInt(a.close_time.count)} oportunidad(es) ganada(s)`, eventKey: "close_time" })}
      ${commercialRichKpi("Estancadas", commercialFmtInt(a.stagnant.count),
        { sub: `+${commercialFmtInt(a.stagnant.threshold_days)} días sin movimiento`, eventKey: "stagnant" })}
      ${commercialRichKpi("Conversión de leads", commercialFmtPercent(a.lead_conversion.rate_any_conv),
        { sub: `${commercialFmtPercent(a.lead_conversion.rate_adv_opp)} avanzó a oportunidad`, eventKey: "lead_conversion" })}
    </div>`;
}

// Re-render QUIRÚRGICO de "Analíticas del Pipeline" — SÓLO su host estable
// (`data-commercial-analytics-body`), nunca `commercialRenderCapability()`:
// eso reconstruiría TODO `#commercial-main-content` (Resumen incluido) por
// una sección que ya estaba lista, y cerraría cualquier análisis abierto
// (Activas/Forecast, que viven en `data-breakdown-group` dentro del MISMO
// contenedor).
function commercialRenderAnalyticsBody() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const host = root.querySelector("[data-commercial-analytics-body]");
  if (host) host.innerHTML = commercialAnalyticsBodyHtml();
}

// Reintento ESCOPEADO de "Analíticas del Pipeline" (owner corrective, ver
// HANDOFF) — UN solo endpoint (`/comercial/data/analytics`), nunca
// summary/pipeline/opportunities/activities ni los dos detalles lazy
// (`pipeline-analytics-detail`/`lead-conversion-detail`, que siguen
// cargando sólo cuando se abre su propio tile). Nunca muta `tipo`,
// `summary`, `pipeline` ni `opportunities` — sólo lo suyo.
//
// Tipo-safety (§6): captura el `tipo` vigente ANTES del fetch y, al
// resolver, sólo aplica la respuesta si sigue siendo el `tipo` actual —
// si el usuario cambió Todos/NN/BI mientras esto viajaba, la respuesta
// vieja se descarta en silencio (ni pisa el estado ni re-renderiza). MISMO
// criterio que ya usa el resto de Comercial para invalidar por Tipo
// (`commercialSetTipo` deja todo en "idle" y dispara una carga fresca), acá
// explícito porque esta request puede seguir en vuelo cuando eso pasa.
async function commercialLoadAnalytics() {
  const requestedTipo = commercialState.tipo;
  commercialState.analyticsStatus = "loading";
  commercialRenderAnalyticsBody();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(requestedTipo)}`;
    const resp = await fetch(`/comercial/data/analytics?${qs}`).then(r => r.json());
    if (commercialState.tipo !== requestedTipo) return; // stale — Tipo cambió mientras viajaba
    if (resp.state === "ok") {
      commercialState.analytics = resp.analytics;
      commercialState.analyticsStatus = "ready";
      commercialState.analyticsErrorState = "";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.analyticsStatus = "error";
      commercialState.analyticsErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    if (commercialState.tipo !== requestedTipo) return; // stale — idem, camino de excepción
    commercialState.analyticsStatus = "error";
    commercialState.analyticsErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderAnalyticsBody();
  // "Creadas esta semana" (Resumen) depende del MISMO payload (ver HANDOFF
  // — dependency check): un reintento exitoso lo recupera de "—" al valor
  // real; uno que vuelve a fallar lo deja como estaba (ya en "—", no hay
  // nada que perder). CERO fetch adicional — usa `commercialState.analytics`
  // ya actualizado arriba.
  commercialRenderCreadasSummaryTile();
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
        // eventKey (Commercial Tile 5, ver HANDOFF): el drill es un fetch
        // PROPIO/independiente (/comercial/data/prospects), MISMO criterio
        // que Creadas — nunca condicional a otro bloque del summary.
        { sub: hs(s.prospectos.hours), split: seg("prospectos"), eventKey: "prospectos" })}
      ${commercialRichKpi("Pipeline total", commercialFmtInt(s.pipeline.hours),
        { sub: "horas", split: seg("pipeline"), eventKey: "forecast", eventView: "total" })}
      ${commercialRichKpi("Pipeline ponderado", commercialFmtInt(s.pipeline.weighted_hours),
        { sub: "horas ponderadas", eventKey: "forecast", eventView: "ponderado" })}
      ${commercialRichKpi("Ganadas", commercialFmtInt(s.ganadas.count),
        // eventKey (Commercial Tile 6, ver HANDOFF): el drill es un fetch
        // PROPIO/independiente (/comercial/data/ganadas), MISMO criterio
        // que Creadas/Prospectos — nunca condicional a otro bloque del
        // summary. El KPI en sí NO cambia: sigue siendo año calendario
        // actual, count+horas — promover el tile a clickeable no toca su
        // semántica.
        { sub: hs(s.ganadas.hours), split: seg("ganadas"), eventKey: "ganadas" })}
      ${commercialRichKpi("Perdidas", commercialFmtInt(s.perdidas.count),
        // Commercial Tile 7, ver HANDOFF: fetch propio/independiente
        // (/comercial/data/perdidas), MISMO criterio que Ganadas — el KPI
        // en sí NO cambia (año calendario actual, count+horas).
        { sub: hs(s.perdidas.hours), split: seg("perdidas"), eventKey: "perdidas" })}
      ${commercialRichKpi("Suspendidas", commercialFmtInt(s.suspendidas.count),
        // Commercial Tile 8, ver HANDOFF — el ÚLTIMO tile inerte del
        // landing: fetch propio/independiente (/comercial/data/
        // suspendidas), MISMO criterio que Prospectos. El KPI en sí NO
        // cambia (stock actual, count+horas, sin año).
        { sub: hs(s.suspendidas.hours), split: seg("suspendidas"), eventKey: "suspendidas" })}
      ${commercialCreadasSummaryTileHtml()}
      ${commercialRichKpi("Conversión", commercialFmtPercent(s.conversion),
        { split: commercialKpiPairsHtml([["Incluyendo suspendidas", commercialFmtPercent(s.conversion_with_suspended)]]) })}
    </div>`;

  // "Analíticas del Pipeline" — HOST ESTABLE (`data-commercial-analytics-body`,
  // owner corrective "SCOPED RETRY", ver HANDOFF): el contenido real lo arma
  // `commercialAnalyticsBodyHtml()` — MISMO desacople que Forecast/Creadas/
  // pipeline-analytics-detail, para poder re-renderizar SÓLO esta sección
  // (loading→ready|error de un reintento) sin tocar Resumen ni el resto del
  // Dashboard.
  const analyticsGrid = `<div data-commercial-analytics-body>${commercialAnalyticsBodyHtml()}</div>`;

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

  // Creadas (Creadas Legacy Parity, ver HANDOFF): TERCERA sección de
  // análisis, hermana de Activas/Forecast, mismo `[data-breakdown-group]`
  // — "un solo análisis abierto por vez" sale gratis del motor compartido,
  // MISMO criterio que Forecast. Lazy load + render quirúrgico también
  // idénticos (ver `commercialRenderCreadasPanel`) — los hosts estables son
  // `[data-commercial-creadas-count]`/`[data-commercial-creadas-body]`.
  const creadasSection = `<details class="dashboard-section" data-section-id="creadas-analisis" data-breakdown="creadas" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Oportunidades Creadas — Análisis</span>
        <span class="dashboard-section__count" data-commercial-creadas-count>${commercialEsc(commercialCreadasCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="creadas" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-creadas-body>${commercialCreadasPanelBodyHtml()}</div>
    </details>`;

  // Prospectos (Commercial Tile 5, ver HANDOFF): CUARTA sección de análisis,
  // hermana de Activas/Forecast/Creadas, MISMO `[data-breakdown-group]` — "un
  // solo análisis abierto por vez" sale gratis del motor compartido. Lazy
  // load + render quirúrgico MISMO criterio que Forecast (una sola request,
  // sin año/hot-cold): los hosts estables son
  // `[data-commercial-prospects-count]`/`[data-commercial-prospects-body]`.
  const prospectsSection = `<details class="dashboard-section" data-section-id="prospectos-analisis" data-breakdown="prospectos" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Prospectos — Detalle</span>
        <span class="dashboard-section__count" data-commercial-prospects-count>${commercialEsc(commercialProspectsCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="prospectos" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-prospects-body>${commercialProspectsPanelBodyHtml()}</div>
    </details>`;

  // Ganadas (Commercial Tile 6, ver HANDOFF): QUINTA sección de análisis,
  // hermana de Activas/Forecast/Creadas/Prospectos, MISMO
  // `[data-breakdown-group]`. Lazy load POR AÑO + render quirúrgico (ver
  // `commercialRenderGanadasPanel`) — los hosts estables son
  // `[data-commercial-ganadas-count]`/`[data-commercial-ganadas-body]`.
  const ganadasSection = `<details class="dashboard-section" data-section-id="ganadas-analisis" data-breakdown="ganadas" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Ganadas — Análisis</span>
        <span class="dashboard-section__count" data-commercial-ganadas-count>${commercialEsc(commercialGanadasCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="ganadas" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-ganadas-body>${commercialGanadasPanelBodyHtml()}</div>
    </details>`;

  // Perdidas (Commercial Tile 7, ver HANDOFF): SEXTA sección de análisis,
  // hermana de Activas/Forecast/Creadas/Prospectos/Ganadas, MISMO
  // `[data-breakdown-group]`. Lazy load POR AÑO + render quirúrgico (ver
  // `commercialRenderPerdidasPanel`) — los hosts estables son
  // `[data-commercial-perdidas-count]`/`[data-commercial-perdidas-body]`.
  const perdidasSection = `<details class="dashboard-section" data-section-id="perdidas-analisis" data-breakdown="perdidas" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Perdidas — Análisis</span>
        <span class="dashboard-section__count" data-commercial-perdidas-count>${commercialEsc(commercialPerdidasCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="perdidas" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-perdidas-body>${commercialPerdidasPanelBodyHtml()}</div>
    </details>`;

  // Suspendidas (Commercial Tile 8, ver HANDOFF): SÉPTIMA y ÚLTIMA sección
  // de análisis, hermana de Activas/Forecast/Creadas/Prospectos/Ganadas/
  // Perdidas, MISMO `[data-breakdown-group]`. Lazy load simple (SIN año) +
  // render quirúrgico — los hosts estables son
  // `[data-commercial-suspendidas-count]`/`[data-commercial-suspendidas-body]`.
  const suspendidasSection = `<details class="dashboard-section" data-section-id="suspendidas-analisis" data-breakdown="suspendidas" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Suspendidas — Detalle</span>
        <span class="dashboard-section__count" data-commercial-suspendidas-count>${commercialEsc(commercialSuspendidasCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="suspendidas" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-suspendidas-body>${commercialSuspendidasPanelBodyHtml()}</div>
    </details>`;

  // Analítica del Pipeline (ver HANDOFF): SEIS secciones más, MISMO
  // `[data-breakdown-group]` que las de arriba. Las CINCO primeras
  // comparten un solo fetch/estado (`pipelineAnalyticsDetail*`) — cada una
  // con su propio host estable de conteo/cuerpo, para que el re-render
  // quirúrgico de UNA no toque a las otras cuatro. "Conversión de leads" es
  // la última, universo propio.
  const pipelineAgeSection = `<details class="dashboard-section" data-section-id="pipeline-age-analisis" data-breakdown="pipeline_age" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Edad del Pipeline — Detalle</span>
        <span class="dashboard-section__count" data-commercial-pipeline-age-count>${commercialEsc(commercialPipelineAgeCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="pipeline_age" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-pipeline-age-body>${commercialPipelineAgePanelBodyHtml()}</div>
    </details>`;

  const stageTimeSection = `<details class="dashboard-section" data-section-id="stage-time-analisis" data-breakdown="stage_time" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Tiempo por Etapa — Detalle</span>
        <span class="dashboard-section__count" data-commercial-stage-time-count>${commercialEsc(commercialStageTimeCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="stage_time" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-stage-time-body>${commercialStageTimePanelBodyHtml()}</div>
    </details>`;

  const stageConversionSection = `<details class="dashboard-section" data-section-id="stage-conversion-analisis" data-breakdown="stage_conversion" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Conversión Etapa a Etapa — Detalle</span>
        <span class="dashboard-section__count" data-commercial-stage-conversion-count>${commercialEsc(commercialStageConversionCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="stage_conversion" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-stage-conversion-body>${commercialStageConversionPanelBodyHtml()}</div>
    </details>`;

  const closeTimeSection = `<details class="dashboard-section" data-section-id="close-time-analisis" data-breakdown="close_time" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Tiempo de Cierre — Detalle</span>
        <span class="dashboard-section__count" data-commercial-close-time-count>${commercialEsc(commercialCloseTimeCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="close_time" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-close-time-body>${commercialCloseTimePanelBodyHtml()}</div>
    </details>`;

  const stagnantSection = `<details class="dashboard-section" data-section-id="stagnant-analisis" data-breakdown="stagnant" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Estancadas — Detalle</span>
        <span class="dashboard-section__count" data-commercial-stagnant-count>${commercialEsc(commercialStagnantCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="stagnant" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-stagnant-body>${commercialStagnantPanelBodyHtml()}</div>
    </details>`;

  const leadConversionSection = `<details class="dashboard-section" data-section-id="lead-conversion-analisis" data-breakdown="lead_conversion" open hidden>
      <summary class="dashboard-section__head" draggable="true">
        <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
        <span class="dashboard-section__caret"></span>
        <span class="dashboard-section__title">Conversión de Leads — Detalle</span>
        <span class="dashboard-section__count" data-commercial-lead-conversion-count>${commercialEsc(commercialLeadConversionCountLabel())}</span>
        <button type="button" class="panel-close" data-commercial-close-analysis="lead_conversion" aria-label="Cerrar análisis">&times;</button>
      </summary>
      <div class="dashboard-section__body" data-commercial-lead-conversion-body>${commercialLeadConversionPanelBodyHtml()}</div>
    </details>`;

  // "Resumen" va SIN contador en el header: la sección agrupa nueve métricas
  // distintas, así que fijar ahí el conteo de una sola (Activas) le daba un
  // protagonismo que no le corresponde y sugería que el bloque entero hablaba
  // de eso. Cada tile ya declara su propia magnitud.
  const sections = commercialDashboardSection("resumen", "Resumen", "", kpiGrid)
    + commercialDashboardSection("analiticas", "Analíticas del Pipeline", "", analyticsGrid)
    + analysisSection
    + forecastSection
    + creadasSection
    + prospectsSection
    + ganadasSection
    + perdidasSection
    + suspendidasSection
    + pipelineAgeSection
    + stageTimeSection
    + stageConversionSection
    + closeTimeSection
    + stagnantSection
    + leadConversionSection;
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
  switch (commercialState.capability) {
    case "actividades": return commercialActividadesHtml();
    case "licencias": return commercialLicenciasHtml();
    case "historial-perdidas": return commercialHistorialPerdidasHtml();
    case "detalle": return commercialDetalleHtml();
    case "clientes": return commercialClientesHtml();
    case "leads": return commercialLeadsHtml();
    default: return commercialDashboardHtml();
  }
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
  } else if ((capability === "licencias" || capability === "detalle")
             && commercialState.opportunitiesDetailStatus === "idle") {
    // Bucket 1 (ver HANDOFF): CUALQUIERA de las dos tabs dispara la MISMA
    // carga — si la otra ya la dejó "ready"/"loading"/"error", este branch
    // ni se evalúa (el guard de arriba exige "idle").
    commercialLoadOpportunitiesDetail();
  } else if (capability === "historial-perdidas" && commercialState.lostHistoryStatus === "idle") {
    commercialLoadLostHistory();
  } else if (capability === "clientes" && commercialState.clientsStatus === "idle") {
    // Bucket 2 (ver HANDOFF): SOLO Clientes se pide al abrir la
    // capability — Ex Clientes es lazy DENTRO de este panel, se pide
    // recién al abrir ese sub-tab (ver `commercialSetClientesSubTab`).
    commercialLoadClients();
  } else if (capability === "leads" && commercialState.leadsStatus === "idle") {
    commercialLoadLeads();
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
      // Scoped retry (ver HANDOFF): status/errorState PROPIOS de esta
      // sección, MISMO criterio `config_incomplete` vs `source_error` que
      // el resto de los detalles lazy — decide si el botón Reintentar tiene
      // sentido (nunca para config incompleta).
      commercialState.analyticsStatus = analyticsResp.state === "ok" ? "ready" : "error";
      commercialState.analyticsErrorState = analyticsResp.state === "ok" ? ""
        : (analyticsResp.state === "config_incomplete" ? "config_incomplete" : "source_error");
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

// ── Bucket 1 — Detalle Oportunidades + Licencias (ver HANDOFF) ──────────────
// UN solo loader para las DOS tabs (mismo endpoint/estado compartido, ver
// `commercialState.opportunitiesDetail`) — cualquiera de las dos que se
// abra primero dispara la carga; la otra reusa el mismo dataset sin una
// segunda request (ver `commercialSetCapability`). Re-render vía
// `commercialRenderCapability()` — MISMO alcance "sólo el panel activo" que
// ya usan Dashboard/Actividades; acá no hay una sección hermana que
// preservar dentro del panel (a diferencia de Resumen/Analíticas), así que
// no hace falta un host más chico.
//
// Tipo-safety: captura el `tipo` vigente antes del fetch y descarta la
// respuesta si el usuario cambió Todos/NN/BI mientras viajaba (MISMO
// criterio explícito que `commercialLoadAnalytics`, ver HANDOFF).
async function commercialLoadOpportunitiesDetail() {
  const requestedTipo = commercialState.tipo;
  commercialState.opportunitiesDetailStatus = "loading";
  commercialRenderCapability();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(requestedTipo)}`;
    const resp = await fetch(`/comercial/data/opportunities-detail?${qs}`).then(r => r.json());
    if (commercialState.tipo !== requestedTipo) return; // stale — Tipo cambió mientras viajaba
    if (resp.state === "ok") {
      commercialState.opportunitiesDetail = resp.opportunities_detail;
      commercialState.opportunitiesDetailStatus = "ready";
      commercialState.opportunitiesDetailErrorState = "";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.opportunitiesDetailStatus = "error";
      commercialState.opportunitiesDetailErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    if (commercialState.tipo !== requestedTipo) return; // stale — idem, camino de excepción
    commercialState.opportunitiesDetailStatus = "error";
    commercialState.opportunitiesDetailErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderCapability();
}

// ── Bucket 1 — Historial de Perdidas (ver HANDOFF) ───────────────────────────
// Universo/endpoint propio (nunca comparte fetch con Detalle/Licencias ni
// con el KPI/drill "Perdidas" ya cerrado). MISMO criterio de tipo-safety y
// re-render que `commercialLoadOpportunitiesDetail`.
async function commercialLoadLostHistory() {
  const requestedTipo = commercialState.tipo;
  commercialState.lostHistoryStatus = "loading";
  commercialRenderCapability();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(requestedTipo)}`;
    const resp = await fetch(`/comercial/data/lost-history?${qs}`).then(r => r.json());
    if (commercialState.tipo !== requestedTipo) return;
    if (resp.state === "ok") {
      commercialState.lostHistory = resp.lost_history;
      commercialState.lostHistoryStatus = "ready";
      commercialState.lostHistoryErrorState = "";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.lostHistoryStatus = "error";
      commercialState.lostHistoryErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    if (commercialState.tipo !== requestedTipo) return;
    commercialState.lostHistoryStatus = "error";
    commercialState.lostHistoryErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderCapability();
}

// ── Bucket 2 — Leads (ver HANDOFF) ───────────────────────────────────────
async function commercialLoadLeads() {
  const requestedTipo = commercialState.tipo;
  commercialState.leadsStatus = "loading";
  commercialRenderCapability();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(requestedTipo)}`;
    const resp = await fetch(`/comercial/data/leads?${qs}`).then(r => r.json());
    if (commercialState.tipo !== requestedTipo) return;
    if (resp.state === "ok") {
      commercialState.leads = resp.leads;
      commercialState.leadsStatus = "ready";
      commercialState.leadsErrorState = "";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.leadsStatus = "error";
      commercialState.leadsErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    if (commercialState.tipo !== requestedTipo) return;
    commercialState.leadsStatus = "error";
    commercialState.leadsErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderCapability();
}

// ── Bucket 2 — Clientes ───────────────────────────────────────────────────
async function commercialLoadClients() {
  const requestedTipo = commercialState.tipo;
  commercialState.clientsStatus = "loading";
  commercialRenderCapability();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(requestedTipo)}`;
    const resp = await fetch(`/comercial/data/clients?${qs}`).then(r => r.json());
    if (commercialState.tipo !== requestedTipo) return;
    if (resp.state === "ok") {
      commercialState.clients = resp.clients;
      commercialState.clientsStatus = "ready";
      commercialState.clientsErrorState = "";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.clientsStatus = "error";
      commercialState.clientsErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    if (commercialState.tipo !== requestedTipo) return;
    commercialState.clientsStatus = "error";
    commercialState.clientsErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderCapability();
}

// ── Bucket 2 — Ex Clientes (LAZY dentro del sub-tab de Clientes) ─────────
async function commercialLoadExClients() {
  const requestedTipo = commercialState.tipo;
  commercialState.exClientsStatus = "loading";
  commercialRenderCapability();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(requestedTipo)}`;
    const resp = await fetch(`/comercial/data/ex-clients?${qs}`).then(r => r.json());
    if (commercialState.tipo !== requestedTipo) return;
    if (resp.state === "ok") {
      commercialState.exClients = resp.ex_clients;
      commercialState.exClientsStatus = "ready";
      commercialState.exClientsErrorState = "";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.exClientsStatus = "error";
      commercialState.exClientsErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    if (commercialState.tipo !== requestedTipo) return;
    commercialState.exClientsStatus = "error";
    commercialState.exClientsErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderCapability();
}

// Sub-tab Clientes/Ex Clientes — MISMO criterio LAZY que el resto: Ex
// Clientes se pide la PRIMERA vez que se abre ese sub-tab, nunca junto con
// Clientes. Re-entrar con el dataset ya `ready` no vuelve a pedir nada.
function commercialSetClientesSubTab(tab) {
  if (tab === commercialState.clientesSubTab) return;
  commercialState.clientesSubTab = tab;
  commercialRenderCapability();
  if (tab === "ex-clientes" && commercialState.exClientsStatus === "idle") {
    commercialLoadExClients();
  }
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

// ── Prospectos — lazy load + render quirúrgico (Commercial Tile 5, ver
// HANDOFF) ───────────────────────────────────────────────────────────────
// MISMA técnica que Forecast (owner §13 — performance guard): una sola
// request, disparada por la PRIMERA apertura del desglose para el `tipo`
// vigente, MISMO contrato fail-soft y MISMO loader canónico
// (`HubForegroundProgress`). El fallo queda AISLADO: sólo este panel avisa.
function commercialRenderProspectsPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const body = root.querySelector("[data-commercial-prospects-body]");
  if (body) body.innerHTML = commercialProspectsPanelBodyHtml();
  const count = root.querySelector("[data-commercial-prospects-count]");
  if (count) count.textContent = commercialProspectsCountLabel();
}

async function commercialLoadProspects() {
  commercialState.prospectsStatus = "loading";
  commercialRenderProspectsPanel();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/prospects?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.prospects = resp.prospects;
      commercialState.prospectsStatus = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.prospectsStatus = "error";
      commercialState.prospectsErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.prospectsStatus = "error";
    commercialState.prospectsErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderProspectsPanel();
}

// Puente entre el motor compartido y el lazy load — MISMO criterio que
// `commercialMaybeLoadForecast`: sólo en una APERTURA (trigger quedó activo)
// y sólo si todavía no se pidió nada para el `tipo` vigente. Cerrar y
// reabrir NO vuelve a pedir nada; un error tampoco reintenta solo (hasta que
// cambie el `tipo`).
function commercialMaybeLoadProspects(trigger) {
  if (trigger.dataset.event !== "prospectos") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.prospectsStatus !== "idle") return;
  commercialLoadProspects();
}

// ── Ganadas — lazy load POR AÑO, SIN ventana caliente (Commercial Tile 6,
// ver HANDOFF) ───────────────────────────────────────────────────────────
// MISMA técnica que `commercialHydrateCreadasYear` (cache por año, adopta y
// renderiza ANTES de resolver) pero SIN el paso 2 de Creadas (prefetch del
// año anterior en background) — owner directive explícita: "Ganadas does
// NOT use Creadas' hot-window policy... Do NOT warm previous year in
// background." Cada año sólo se pide cuando el usuario lo pide de verdad.
function commercialRenderGanadasPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const body = root.querySelector("[data-commercial-ganadas-body]");
  if (body) body.innerHTML = commercialGanadasPanelBodyHtml();
  const count = root.querySelector("[data-commercial-ganadas-count]");
  if (count) count.textContent = commercialGanadasCountLabel();
}

async function commercialHydrateGanadasYear(year, opts) {
  const key = String(year);
  const entry = commercialState.ganadasByYear[key];
  // Ya cargado o en vuelo: cero fetch — la SEGUNDA selección del mismo año
  // es gratis. Pero "no hay que pedirlo" no es "no hay nada que hacer": el
  // año pedido hay que publicarlo como activo igual (MISMO motivo que
  // Creadas — sin esto, un año ya cacheado dejaría `commercialState.ganadas`
  // apuntando al año ANTERIOR).
  if (entry && (entry.status === "ready" || entry.status === "loading")) {
    if (opts && opts.activo) commercialGanadasAdoptYear(key);
    return entry.status === "ready";
  }
  commercialState.ganadasByYear[key] = { status: "loading", data: null };
  if (opts && opts.activo) commercialState.ganadasStatus = "loading";
  commercialRenderGanadasPanel();
  try {
    const qs = `year=${encodeURIComponent(key)}&tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/ganadas?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.ganadasByYear[key] = { status: "ready", data: resp.ganadas };
      commercialGanadasAdoptYear(key);
      return true;
    }
    commercialState.ganadasByYear[key] = {
      status: "error", data: null,
      errorState: resp.state === "config_incomplete" ? "config_incomplete" : "source_error",
    };
  } catch (e) {
    commercialState.ganadasByYear[key] = { status: "error", data: null, errorState: "source_error" };
  }
  commercialGanadasAdoptYear(commercialState.ganadasYear || key);
  return false;
}

// Publica el año pedido como el ACTIVO si es el que el usuario está mirando
// — MISMO contrato que `commercialCreadasAdoptYear`.
function commercialGanadasAdoptYear(year) {
  const key = String(year);
  const activo = String(commercialState.ganadasYear || key);
  if (key !== activo) { commercialRenderGanadasPanel(); return; }
  const entry = commercialState.ganadasByYear[key] || { status: "idle" };
  commercialState.ganadas = entry.data;
  commercialState.ganadasStatus = entry.status === "ready" ? "ready"
    : entry.status === "error" ? "error" : "loading";
  commercialState.ganadasErrorState = entry.errorState || "";
  if (entry.status === "ready") HubForegroundProgress.complete("commercial");
  else if (entry.status === "error") HubForegroundProgress.fail("commercial");
  commercialRenderGanadasPanel();
}

// Primera apertura del drill: SÓLO el año activo (calendario actual, MISMA
// lectura que el KPI). Nada de ventana caliente — ver el comentario de la
// sección arriba.
async function commercialLoadGanadas() {
  const actual = new Date().getFullYear();
  commercialState.ganadasYear = String(actual);
  HubForegroundProgress.begin("commercial", { seedMs: 4500 });
  await commercialHydrateGanadasYear(actual, { activo: true });
}

// Puente entre el motor compartido y el lazy load — MISMO criterio que
// `commercialMaybeLoadForecast`/`commercialMaybeLoadProspects`: sólo en una
// APERTURA (trigger quedó activo) y sólo si todavía no se pidió nada para
// el `tipo` vigente.
function commercialMaybeLoadGanadas(trigger) {
  if (trigger.dataset.event !== "ganadas") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.ganadasStatus !== "idle") return;
  commercialLoadGanadas();
}

// ── Perdidas — lazy load POR AÑO, SIN ventana caliente (Commercial Tile 7,
// ver HANDOFF) ─────────────────────────────────────────────────────────────
// MISMA técnica que Ganadas — cache por año, adopta y renderiza ANTES de
// resolver, SIN prefetch del año anterior en background. El desglose por
// Motivo NUNCA dispara su propio fetch: se deriva de `commercialState.
// perdidas` (ver `commercialPerdidasMotivoRows`), el MISMO payload que ya
// trae la tabla — corrige el bug real del legacy (`/lost-reasons`
// hardcodeado a año actual) sin agregar ningún RPC.
function commercialRenderPerdidasPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const body = root.querySelector("[data-commercial-perdidas-body]");
  if (body) body.innerHTML = commercialPerdidasPanelBodyHtml();
  const count = root.querySelector("[data-commercial-perdidas-count]");
  if (count) count.textContent = commercialPerdidasCountLabel();
}

async function commercialHydratePerdidasYear(year, opts) {
  const key = String(year);
  const entry = commercialState.perdidasByYear[key];
  if (entry && (entry.status === "ready" || entry.status === "loading")) {
    if (opts && opts.activo) commercialPerdidasAdoptYear(key);
    return entry.status === "ready";
  }
  commercialState.perdidasByYear[key] = { status: "loading", data: null };
  if (opts && opts.activo) commercialState.perdidasStatus = "loading";
  commercialRenderPerdidasPanel();
  try {
    const qs = `year=${encodeURIComponent(key)}&tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/perdidas?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.perdidasByYear[key] = { status: "ready", data: resp.perdidas };
      commercialPerdidasAdoptYear(key);
      return true;
    }
    commercialState.perdidasByYear[key] = {
      status: "error", data: null,
      errorState: resp.state === "config_incomplete" ? "config_incomplete" : "source_error",
    };
  } catch (e) {
    commercialState.perdidasByYear[key] = { status: "error", data: null, errorState: "source_error" };
  }
  commercialPerdidasAdoptYear(commercialState.perdidasYear || key);
  return false;
}

function commercialPerdidasAdoptYear(year) {
  const key = String(year);
  const activo = String(commercialState.perdidasYear || key);
  if (key !== activo) { commercialRenderPerdidasPanel(); return; }
  const entry = commercialState.perdidasByYear[key] || { status: "idle" };
  commercialState.perdidas = entry.data;
  commercialState.perdidasStatus = entry.status === "ready" ? "ready"
    : entry.status === "error" ? "error" : "loading";
  commercialState.perdidasErrorState = entry.errorState || "";
  if (entry.status === "ready") HubForegroundProgress.complete("commercial");
  else if (entry.status === "error") HubForegroundProgress.fail("commercial");
  commercialRenderPerdidasPanel();
}

async function commercialLoadPerdidas() {
  const actual = new Date().getFullYear();
  commercialState.perdidasYear = String(actual);
  HubForegroundProgress.begin("commercial", { seedMs: 4500 });
  await commercialHydratePerdidasYear(actual, { activo: true });
}

function commercialMaybeLoadPerdidas(trigger) {
  if (trigger.dataset.event !== "perdidas") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.perdidasStatus !== "idle") return;
  commercialLoadPerdidas();
}

// ── Suspendidas — lazy load + render quirúrgico (Commercial Tile 8, ver
// HANDOFF) ───────────────────────────────────────────────────────────────
// MISMA técnica que Prospectos (owner §13 — "follows the newer Commercial
// lazy convention... NO year cache, NO background warming"): una sola
// request, disparada por la PRIMERA apertura para el `tipo` vigente.
function commercialRenderSuspendidasPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const body = root.querySelector("[data-commercial-suspendidas-body]");
  if (body) body.innerHTML = commercialSuspendidasPanelBodyHtml();
  const count = root.querySelector("[data-commercial-suspendidas-count]");
  if (count) count.textContent = commercialSuspendidasCountLabel();
}

async function commercialLoadSuspendidas() {
  commercialState.suspendidasStatus = "loading";
  commercialRenderSuspendidasPanel();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/suspendidas?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.suspendidas = resp.suspendidas;
      commercialState.suspendidasStatus = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.suspendidasStatus = "error";
      commercialState.suspendidasErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.suspendidasStatus = "error";
    commercialState.suspendidasErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderSuspendidasPanel();
}

function commercialMaybeLoadSuspendidas(trigger) {
  if (trigger.dataset.event !== "suspendidas") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.suspendidasStatus !== "idle") return;
  commercialLoadSuspendidas();
}

// ── Analítica del Pipeline — lazy load + render quirúrgico (ver HANDOFF) ──
// CINCO secciones, UN SOLO fetch/estado: re-renderiza las CINCO cada vez
// (cada una es liviana — un `if` de status + su propio slice del payload),
// nunca cinco funciones de render redundantes que repitan el mismo
// chequeo de estado compartido.
const COMMERCIAL_PIPELINE_ANALYTICS_HOSTS = [
  ["pipeline-age", commercialPipelineAgePanelBodyHtml, commercialPipelineAgeCountLabel],
  ["stage-time", commercialStageTimePanelBodyHtml, commercialStageTimeCountLabel],
  ["stage-conversion", commercialStageConversionPanelBodyHtml, commercialStageConversionCountLabel],
  ["close-time", commercialCloseTimePanelBodyHtml, commercialCloseTimeCountLabel],
  ["stagnant", commercialStagnantPanelBodyHtml, commercialStagnantCountLabel],
];
function commercialRenderPipelineAnalyticsPanels() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  COMMERCIAL_PIPELINE_ANALYTICS_HOSTS.forEach(([slug, bodyFn, countFn]) => {
    const body = root.querySelector(`[data-commercial-${slug}-body]`);
    if (body) body.innerHTML = bodyFn();
    const count = root.querySelector(`[data-commercial-${slug}-count]`);
    if (count) count.textContent = countFn();
  });
}

async function commercialLoadPipelineAnalyticsDetail() {
  commercialState.pipelineAnalyticsDetailStatus = "loading";
  commercialRenderPipelineAnalyticsPanels();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/pipeline-analytics-detail?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.pipelineAnalyticsDetail = resp.pipeline_analytics_detail;
      commercialState.pipelineAnalyticsDetailStatus = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.pipelineAnalyticsDetailStatus = "error";
      commercialState.pipelineAnalyticsDetailErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.pipelineAnalyticsDetailStatus = "error";
    commercialState.pipelineAnalyticsDetailErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderPipelineAnalyticsPanels();
}

const COMMERCIAL_PIPELINE_ANALYTICS_EVENT_KEYS = new Set([
  "pipeline_age", "stage_time", "stage_conversion", "close_time", "stagnant",
]);
function commercialMaybeLoadPipelineAnalyticsDetail(trigger) {
  if (!COMMERCIAL_PIPELINE_ANALYTICS_EVENT_KEYS.has(trigger.dataset.event)) return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.pipelineAnalyticsDetailStatus !== "idle") return;
  commercialLoadPipelineAnalyticsDetail();
}

// ── "Conversión de Leads" — lazy load + render quirúrgico (ver HANDOFF) ──
// Universo propio, endpoint propio — MISMA técnica que Suspendidas.
function commercialRenderLeadConversionPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const body = root.querySelector("[data-commercial-lead-conversion-body]");
  if (body) body.innerHTML = commercialLeadConversionPanelBodyHtml();
  const count = root.querySelector("[data-commercial-lead-conversion-count]");
  if (count) count.textContent = commercialLeadConversionCountLabel();
}

async function commercialLoadLeadConversionDetail() {
  commercialState.leadConversionDetailStatus = "loading";
  commercialRenderLeadConversionPanel();
  HubForegroundProgress.begin("commercial", { seedMs: 9000 });
  try {
    const qs = `tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/lead-conversion-detail?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.leadConversionDetail = resp.lead_conversion_detail;
      commercialState.leadConversionDetailStatus = "ready";
      HubForegroundProgress.complete("commercial");
    } else {
      commercialState.leadConversionDetailStatus = "error";
      commercialState.leadConversionDetailErrorState = resp.state === "config_incomplete" ? "config_incomplete" : "source_error";
      HubForegroundProgress.fail("commercial");
    }
  } catch (e) {
    commercialState.leadConversionDetailStatus = "error";
    commercialState.leadConversionDetailErrorState = "source_error";
    HubForegroundProgress.fail("commercial");
  }
  commercialRenderLeadConversionPanel();
}

function commercialMaybeLoadLeadConversionDetail(trigger) {
  if (trigger.dataset.event !== "lead_conversion") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.leadConversionDetailStatus !== "idle") return;
  commercialLoadLeadConversionDetail();
}

// ── Creadas — lazy load + render quirúrgico (Creadas Legacy Parity, ver
// HANDOFF) ─────────────────────────────────────────────────────────────────
// MISMA técnica que Forecast: re-render QUIRÚRGICO (SOLO el cuerpo y el
// conteo del panel) — nunca `commercialRender()`/`commercialRenderCapability()`,
// que reconstruirían el subárbol y cerrarían el propio panel que se está
// llenando.
function commercialRenderCreadasPanel() {
  const root = document.getElementById("commercial-main-content");
  if (!root) return;
  const body = root.querySelector("[data-commercial-creadas-body]");
  if (body) body.innerHTML = commercialCreadasPanelBodyHtml();
  const count = root.querySelector("[data-commercial-creadas-count]");
  if (count) count.textContent = commercialCreadasCountLabel();
}

// Una sola request, disparada por la PRIMERA apertura del desglose para el
// `tipo` vigente — MISMO criterio que Forecast (owner directive — "the KPI
// must remain lightweight... first click fetches the drill"). El fallo
// queda AISLADO: sólo este panel avisa.
// ── HIDRATACION POR AÑO ────────────────────────────────────────────────────
// Un año, un fetch. `commercialState.creadasByYear[year]` es la fuente de
// verdad de que esta cargado; la lista del `<select>` viene de otro lado y NO
// implica hidratacion.
async function commercialHydrateCreadasYear(year, opts) {
  const key = String(year);
  const entry = commercialState.creadasByYear[key];
  // Ya cargado o en vuelo: cero fetch. Es el guard que hace que la SEGUNDA
  // seleccion del mismo año sea gratis.
  //
  // Pero "no hay que pedirlo" NO es "no hay nada que hacer": el estado POR
  // AÑO y el dataset que leen los renderers son dos cosas DISTINTAS, y el año
  // pedido hay que publicarlo como activo igual. Sin esto, elegir un año que
  // ya estaba en cache dejaba `commercialState.creadas` apuntando al año
  // ANTERIOR; como la tabla filtra por prefijo de mes, no matcheaba ninguno y
  // el panel se veia VACIO — justo en los años de la ventana caliente,
  // mientras que un año frio andaba bien por pasar por el fetch, que si
  // adoptaba.
  if (entry && (entry.status === "ready" || entry.status === "loading")) {
    if (opts && opts.activo) commercialCreadasAdoptYear(key);
    return entry.status === "ready";
  }
  commercialState.creadasByYear[key] = { status: "loading", data: null };
  if (opts && opts.activo) commercialState.creadasStatus = "loading";
  commercialRenderCreadasPanel();
  try {
    const qs = `anio=${encodeURIComponent(key)}&tipo=${encodeURIComponent(commercialState.tipo)}`;
    const resp = await fetch(`/comercial/data/creadas?${qs}`).then(r => r.json());
    if (resp.state === "ok") {
      commercialState.creadasByYear[key] = { status: "ready", data: resp.creadas };
      commercialCreadasAdoptYear(key);
      return true;
    }
    commercialState.creadasByYear[key] = {
      status: "error", data: null,
      errorState: resp.state === "config_incomplete" ? "config_incomplete" : "source_error",
    };
  } catch (e) {
    commercialState.creadasByYear[key] = { status: "error", data: null, errorState: "source_error" };
  }
  commercialCreadasAdoptYear(commercialState.creadasYear || key);
  return false;
}

// Publica el año pedido como el ACTIVO si es el que el usuario esta mirando.
// Los renderers preguntan por UN dataset (`commercialState.creadas`), asi que
// aca se conecta el estado por año con esa lectura — sin que un año que termina
// en background pise la vista de otro.
function commercialCreadasAdoptYear(year) {
  const key = String(year);
  const activo = String(commercialState.creadasYear || key);
  if (key !== activo) { commercialRenderCreadasPanel(); return; }
  const entry = commercialState.creadasByYear[key] || { status: "idle" };
  commercialState.creadas = entry.data;
  commercialState.creadasStatus = entry.status === "ready" ? "ready"
    : entry.status === "error" ? "error" : "loading";
  commercialState.creadasErrorState = entry.errorState || "";
  if (entry.status === "ready") HubForegroundProgress.complete("commercial");
  else if (entry.status === "error") HubForegroundProgress.fail("commercial");
  commercialRenderCreadasPanel();
}

// Primera apertura del drill: descubrimiento + HOT WINDOW.
//
// Solo el año ACTUAL y el ANTERIOR se hidratan solos. Los mas viejos quedan
// COLD: aparecen en el `<select>` (metadata barata) y se piden al
// seleccionarse. Antes esta apertura reconstruia el historial canonico COMPLETO.
//
// ACTIVE-FIRST, no en paralelo. El año activo va SOLO; el previo arranca
// recien cuando el activo resolvio y ya pinto.
//
// Arrancarlos juntos parecia gratis —el render nunca esperaba al previo— pero
// medido contra el Odoo real no lo es: en 3 muestras pareadas el año activo
// tardo ~8.0s solo y ~9.3s mientras el previo corria (+17%, mismo signo en las
// 3). Compiten por el servidor, no por el event loop. La ventana caliente es
// trabajo OPCIONAL de background: no puede competir con el primer pintado.
async function commercialLoadCreadas() {
  const actual = new Date().getFullYear();
  commercialState.creadasYear = String(actual);
  HubForegroundProgress.begin("commercial", { seedMs: 4500 });

  // El descubrimiento va en paralelo con los datos: es una consulta con
  // `limit=1`, no puede ser el cuello de botella, y el `<select>` la necesita
  // para poder ofrecer los años frios.
  if (commercialState.creadasAniosStatus === "idle") {
    commercialState.creadasAniosStatus = "loading";
    fetch("/comercial/data/creadas/anios-disponibles")
      .then(r => r.json())
      .then(resp => {
        commercialState.creadasSelectableYears = resp.state === "ok" ? (resp.anios || []) : [];
        commercialState.creadasAniosStatus = resp.state === "ok" ? "ready" : "error";
        commercialRenderCreadasPanel();
      })
      .catch(() => { commercialState.creadasAniosStatus = "error"; });
  }

  // 1. El año activo, SOLO. `commercialHydrateCreadasYear` adopta y renderiza
  //    antes de resolver, asi que cuando este `await` vuelve ya hubo pintado.
  const ok = await commercialHydrateCreadasYear(actual, { activo: true });

  // 2. Recien ahora el previo, en background y sin `await`: nadie lo espera y
  //    cuando termine NO toca la vista (`commercialCreadasAdoptYear` solo
  //    publica el año que el usuario esta mirando).
  //
  //    Si el activo fallo no se prefetchea: el usuario va a reintentar, y ese
  //    reintento no deberia competir con un calentamiento opcional.
  if (ok) commercialHydrateCreadasYear(actual - 1);
}

// Puente entre el motor compartido y el lazy load — MISMO criterio que
// `commercialMaybeLoadForecast`: sólo en una APERTURA (trigger quedó
// activo) y sólo si todavía no se pidió nada para el `tipo` vigente. Cerrar
// y reabrir NO vuelve a pedir nada; un error tampoco reintenta solo (hasta
// que cambie el `tipo`).
function commercialMaybeLoadCreadas(trigger) {
  if (trigger.dataset.event !== "creadas") return;
  if (!trigger.classList.contains("is-active")) return;
  if (commercialState.creadasStatus !== "idle") return;
  commercialLoadCreadas();
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
  // Scoped retry (ver HANDOFF): MISMO criterio que el resto de los status
  // lazy de acá abajo — vuelve a "idle", la próxima `commercialLoadData()`
  // (que este cambio de Tipo dispara) la deja en ready|error de nuevo.
  commercialState.analyticsStatus = "idle";
  commercialState.analyticsErrorState = "";
  commercialState.opportunities = null;
  commercialState.activities = null;
  commercialState.activitiesStatus = "idle";
  // Bucket 1 (ver HANDOFF): MISMO criterio — invalida y NO re-pide acá,
  // vuelve a "idle" y lo carga la próxima apertura de la tab con el tipo
  // nuevo. Filtros locales se resetean con el dataset (MISMO criterio que
  // Actividades/Prospectos — un valor de Etapa/Vendedor/Año podría apuntar
  // a un universo que ya no existe tras cambiar Todos/NN/BI).
  commercialState.opportunitiesDetail = null;
  commercialState.opportunitiesDetailStatus = "idle";
  commercialState.opportunitiesDetailErrorState = "";
  commercialState.licSearchText = "";
  commercialState.licStageFilter = "";
  commercialState.licSellerFilter = "";
  commercialState.detSearchText = "";
  commercialState.detStageFilter = "";
  commercialState.detSellerFilter = "";
  commercialState.lostHistory = null;
  commercialState.lostHistoryStatus = "idle";
  commercialState.lostHistoryErrorState = "";
  commercialState.histSearchText = "";
  commercialState.histYearFilter = "";
  commercialState.histSellerFilter = "";
  commercialState.histMotivoFilter = "";
  // Bucket 2 (ver HANDOFF): MISMO criterio — invalida y NO re-pide acá.
  commercialState.leads = null;
  commercialState.leadsStatus = "idle";
  commercialState.leadsErrorState = "";
  commercialState.leadsExpanded = [];
  commercialState.leadsSearchText = "";
  commercialState.clients = null;
  commercialState.clientsStatus = "idle";
  commercialState.clientsErrorState = "";
  commercialState.clientesExpanded = [];
  commercialState.clientesSearchText = "";
  commercialState.clientesSellerFilter = "";
  commercialState.clientesStatusFilter = "all";
  commercialState.exClients = null;
  commercialState.exClientsStatus = "idle";
  commercialState.exClientsErrorState = "";
  commercialState.exClientesSearchText = "";
  commercialState.exClientesSellerFilter = "";
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
  // Creadas (Creadas Legacy Parity, ver HANDOFF): MISMO criterio que
  // Forecast — invalida y NO re-pide acá, vuelve a "idle" y lo carga la
  // próxima apertura del desglose con el tipo nuevo. Año/mes/semana
  // expandidos también se resetean: un año/mes válido bajo un `tipo`
  // podría no tener datos bajo el otro.
  commercialState.creadas = null;
  commercialState.creadasStatus = "idle";
  commercialState.creadasErrorState = "";
  commercialState.creadasYear = null;
  commercialState.creadasExpandedMonths = [];
  commercialState.creadasSelection = null;
  commercialState.creadasSeller = "";
  commercialState.creadasSearch = "";
  // Prospectos (Commercial Tile 5, ver HANDOFF): MISMO criterio que
  // Forecast/Creadas — invalida y NO re-pide acá, vuelve a "idle" y lo carga
  // la próxima apertura del desglose con el tipo nuevo. Vendedor/Buscar se
  // resetean: sus opciones/resultados se derivan de datos que ya no son los
  // mismos (MISMO criterio que `oppSearchText`/`actUser` arriba).
  commercialState.prospects = null;
  commercialState.prospectsStatus = "idle";
  commercialState.prospectsErrorState = "";
  commercialState.prospectsSellerFilter = "";
  commercialState.prospectsSearchText = "";
  // Ganadas (Commercial Tile 6, ver HANDOFF): invalida TODO el cache por año
  // — `ganadasByYear = {}`, no sólo el año activo. Un tipo nuevo cambia el
  // universo de CADA año cacheado (la key de cache es sólo el año, `tipo`
  // viaja aparte en la query string), así que dejar años "ready" del tipo
  // viejo serviría datos incorrectos sin volver a pedir nada al reabrir con
  // el tipo nuevo. Vendedor/Cliente/orden se resetean con el dataset —
  // MISMO criterio que Prospectos.
  commercialState.ganadasByYear = {};
  commercialState.ganadas = null;
  commercialState.ganadasStatus = "idle";
  commercialState.ganadasErrorState = "";
  commercialState.ganadasYear = null;
  commercialState.ganadasSellerFilter = "";
  commercialState.ganadasClientFilter = "";
  // Perdidas (Commercial Tile 7, ver HANDOFF): MISMO criterio que Ganadas
  // — invalida TODO el cache por año, resetea los TRES ejes de filtro.
  commercialState.perdidasByYear = {};
  commercialState.perdidas = null;
  commercialState.perdidasStatus = "idle";
  commercialState.perdidasErrorState = "";
  commercialState.perdidasYear = null;
  commercialState.perdidasSellerFilter = "";
  commercialState.perdidasClientFilter = "";
  commercialState.perdidasMotivoFilter = "";
  // Suspendidas (Commercial Tile 8, ver HANDOFF): MISMO criterio que
  // Prospectos — invalida y NO re-pide acá, vuelve a "idle" y lo carga la
  // próxima apertura del desglose con el tipo nuevo.
  commercialState.suspendidas = null;
  commercialState.suspendidasStatus = "idle";
  commercialState.suspendidasErrorState = "";
  commercialState.suspendidasSellerFilter = "";
  // Analítica del Pipeline (ver HANDOFF): MISMO criterio — invalida y NO
  // re-pide acá, vuelve a "idle" y lo carga la próxima apertura del
  // desglose con el tipo nuevo. El sort local NO se resetea (MISMO criterio
  // que el resto de Comercial — un orden elegido sobrevive al cambio de
  // Tipo, sólo el dataset se invalida).
  commercialState.pipelineAnalyticsDetail = null;
  commercialState.pipelineAnalyticsDetailStatus = "idle";
  commercialState.pipelineAnalyticsDetailErrorState = "";
  commercialState.leadConversionDetail = null;
  commercialState.leadConversionDetailStatus = "idle";
  commercialState.leadConversionDetailErrorState = "";
  commercialState.leadConversionCampanaFilter = "";
  commercialState.leadConversionMedioFilter = "";
  commercialState.leadConversionOrigenFilter = "";
  commercialState.leadConversionSellerFilter = "";
  commercialState.leadConversionOutcomeFilter = "";
  commercialState.leadConversionSearchText = "";
  // Los filtros locales de Actividades se reinician con el dataset: sus
  // opciones de Responsable se derivan de datos que ya no son los mismos.
  commercialState.actPeriod = "all";
  commercialState.actUser = "";
  commercialState.actSearchText = "";
  commercialState.actSortKey = null;
  commercialState.actSortDir = 1;
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
      } else if (retryBtn.dataset.commercialRetry === "creadas") {
        if (commercialState.creadasStatus !== "loading") commercialLoadCreadas();
      } else if (retryBtn.dataset.commercialRetry === "prospectos") {
        if (commercialState.prospectsStatus !== "loading") commercialLoadProspects();
      } else if (retryBtn.dataset.commercialRetry === "ganadas") {
        if (commercialState.ganadasStatus !== "loading") commercialLoadGanadas();
      } else if (retryBtn.dataset.commercialRetry === "perdidas") {
        if (commercialState.perdidasStatus !== "loading") commercialLoadPerdidas();
      } else if (retryBtn.dataset.commercialRetry === "suspendidas") {
        if (commercialState.suspendidasStatus !== "loading") commercialLoadSuspendidas();
      } else if (retryBtn.dataset.commercialRetry === "pipeline-analytics") {
        if (commercialState.pipelineAnalyticsDetailStatus !== "loading") commercialLoadPipelineAnalyticsDetail();
      } else if (retryBtn.dataset.commercialRetry === "lead-conversion") {
        if (commercialState.leadConversionDetailStatus !== "loading") commercialLoadLeadConversionDetail();
      } else if (retryBtn.dataset.commercialRetry === "analytics") {
        // Scoped retry (owner corrective, ver HANDOFF §5): guard contra el
        // propio `analyticsStatus` — nunca contra `commercialState.status`
        // general, que ya está "ready" en este escenario (Resumen/Pipeline
        // cargaron bien, sólo Analíticas falló) y jamás debe volver a
        // "loading" por esto.
        if (commercialState.analyticsStatus !== "loading") commercialLoadAnalytics();
      } else if (retryBtn.dataset.commercialRetry === "opportunities-detail") {
        // Bucket 1 (ver HANDOFF): compartido por Licencias Y Detalle
        // Oportunidades — reintentar desde CUALQUIERA de las dos arregla
        // las dos (mismo dataset/estado). Guard contra el propio status.
        if (commercialState.opportunitiesDetailStatus !== "loading") commercialLoadOpportunitiesDetail();
      } else if (retryBtn.dataset.commercialRetry === "lost-history") {
        if (commercialState.lostHistoryStatus !== "loading") commercialLoadLostHistory();
      } else if (retryBtn.dataset.commercialRetry === "leads") {
        if (commercialState.leadsStatus !== "loading") commercialLoadLeads();
      } else if (retryBtn.dataset.commercialRetry === "clients") {
        if (commercialState.clientsStatus !== "loading") commercialLoadClients();
      } else if (retryBtn.dataset.commercialRetry === "ex-clients") {
        if (commercialState.exClientsStatus !== "loading") commercialLoadExClients();
      } else if (commercialState.status !== "loading") {
        commercialLoadData();
      }
      return;
    }
    // Fila de mes/semana de Creadas (Creadas Legacy Parity, ver HANDOFF —
    // paridad con el drill mensual del legacy): expande/colapsa, client-side
    // puro sobre el payload YA cargado — nunca un fetch. Un mes se re-abre
    // limpio (semana/detalle previos no sobreviven a cambiar de mes); una
    // semana dentro del MISMO mes togglea sola. Re-render QUIRÚRGICO del
    // panel — MISMO camino que el resto de Creadas/Forecast.
    // ✕ DEL CONTEXTO DE VENDEDOR — limpia SOLO el vendedor. Año, Buscar y el
    // Tipo global quedan como estaban: descartar un filtro no es "resetear
    // todo", que es lo que hace Limpiar y por eso son dos acciones distintas.
    if (event.target.closest("[data-commercial-creadas-seller-clear]")) {
      commercialCreadasSetSeller("");
      return;
    }
    // Ganadas — ✕ DEL CONTEXTO (owner corrective, post-smoke: MISMA
    // experiencia funcional/visual que Creadas): cada eje limpia SOLO el
    // suyo — Año/Cliente quedan como estaban al limpiar Vendedor, y
    // viceversa. Distinto de Limpiar, que resetea la combinación completa.
    if (event.target.closest("[data-commercial-ganadas-seller-clear]")) {
      commercialGanadasSetSeller("");
      return;
    }
    if (event.target.closest("[data-commercial-ganadas-client-clear]")) {
      commercialGanadasSetClient("");
      return;
    }
    // RANKING — click en un vendedor lo selecciona en el dropdown y filtra en
    // cascada. MISMA puerta que el `<select>`: el `<select>` se regenera con su
    // `selected` correcto en el re-render, así que no hace falta tocarlo a mano.
    const sellerPick = event.target.closest("[data-commercial-creadas-seller-pick]");
    if (sellerPick) {
      commercialCreadasSetSeller(sellerPick.dataset.commercialCreadasSellerPick);
      return;
    }
    // CHEVRON — expande/colapsa las semanas del mes. VA PRIMERO y corta acá:
    // el chevron vive DENTRO de la fila de mes, así que sin este atajo un click
    // en él haría las DOS cosas (expandir y abrir detalle), que es justo la
    // ambigüedad que este corrective elimina. Varios meses pueden estar
    // abiertos a la vez.
    const creadasToggle = event.target.closest("[data-commercial-creadas-toggle]");
    if (creadasToggle) {
      event.stopPropagation();
      const month = creadasToggle.dataset.commercialCreadasToggle;
      const open = commercialState.creadasExpandedMonths;
      const at = open.indexOf(month);
      if (at === -1) open.push(month); else open.splice(at, 1);
      commercialRenderCreadasPanel();
      return;
    }
    // FILA DE MES (el resto del contenido) — selecciona el mes y abre su
    // detalle, SIN tocar la expansión: son dos preguntas distintas ("¿cómo se
    // reparte el mes por semana?" vs "¿qué oportunidades son?"). Re-clickear el
    // mes ya seleccionado cierra el detalle — mismo resultado que el ×.
    const creadasMonthRow = event.target.closest("[data-commercial-creadas-month]");
    if (creadasMonthRow) {
      const month = creadasMonthRow.dataset.commercialCreadasMonth;
      const sel = commercialState.creadasSelection;
      commercialState.creadasSelection = (sel && sel.month === month && sel.week === null)
        ? null : { month: month, week: null };
      commercialRenderCreadasPanel();
      return;
    }
    // FILA DE SEMANA — selecciona esa semana y abre su detalle.
    const creadasWeekRow = event.target.closest("[data-commercial-creadas-week]");
    if (creadasWeekRow) {
      const parts = creadasWeekRow.dataset.commercialCreadasWeek.split(":");
      const month = parts[0];
      const week = Number(parts[1]);
      const sel = commercialState.creadasSelection;
      commercialState.creadasSelection = (sel && sel.month === month && sel.week === week)
        ? null : { month: month, week: week };
      commercialRenderCreadasPanel();
      return;
    }
    // CABECERA ORDENABLE del detalle — MISMO contrato que Actividades: la
    // primera vez ordena ascendente, volver a clickear invierte. Cero red.
    const creadasSort = event.target.closest("[data-commercial-creadas-sort]");
    if (creadasSort) {
      const key = creadasSort.dataset.commercialCreadasSort;
      if (commercialState.creadasSortKey === key) {
        commercialState.creadasSortDir *= -1;
      } else {
        commercialState.creadasSortKey = key;
        commercialState.creadasSortDir = 1;
      }
      commercialRenderCreadasPanel();
      return;
    }
    // ✕ DEL CONTEXTO DEL DETALLE — cierra SÓLO el detalle. No toca año,
    // vendedor, búsqueda, meses expandidos ni el dataset, y no dispara ningún
    // fetch. Es el ✕ del tag "Filtrado por Mes/Semana" que el detalle hereda de
    // la tabla: antes había además un `.panel-close` propio haciendo lo mismo,
    // y dos ✕ para una sola acción es ruido.
    if (event.target.closest("[data-commercial-creadas-detail-close]")) {
      commercialState.creadasSelection = null;
      commercialRenderCreadasPanel();
      return;
    }
    // "✕ Limpiar" de Creadas — resetea SÓLO los filtros locales del drill
    // (Vendedor + Buscar). El Año se preserva a propósito: es la ventana que el
    // usuario eligió mirar, no un filtro que "ensucia" la vista. Y el selector
    // GLOBAL de Tipo (Todos/NN/BI) no se toca ni de casualidad: cambia el
    // universo de negocio y su dataset, no es un filtro de esta tabla.
    if (event.target.closest("[data-commercial-creadas-clear]")) {
      commercialState.creadasSearch = "";
      // Por la misma puerta que el dropdown y el ranking: Limpiar deja el
      // vendedor en "Todos" y la selección del ranking se cae sola (el render
      // deriva `.is-active` del estado, no de una clase pegada a mano).
      commercialCreadasSetSeller("");
      return;
    }
    // "✕ Limpiar" del Forecast (C6b): mismo primitivo y mismo gesto que el de
    // Activas, pero acá NO hace falta tocar los controles a mano — el panel de
    // Forecast SÍ se re-renderiza quirúrgicamente, así que los `<select>` se
    // regeneran con su valor neutro solos. Cero hooks de DOM propios y cero
    // red: el payload no cambia.
    // "✕ Limpiar" de Actividades (C9): resetea Período + Responsable + Buscar.
    // Regenera el panel a propósito (ver `commercialActivitiesControlsHtml`):
    // el estado activo de "Período" vive en el markup de sus botones.
    const actClear = event.target.closest("[data-commercial-act-clear]");
    if (actClear) {
      commercialState.actPeriod = "all";
      commercialState.actUser = "";
      commercialState.actSearchText = "";
      commercialRenderCapability();
      return;
    }
    // Orden por columna de Actividades (C9): MISMO contrato transversal que el
    // detalle de Oportunidades — client-side puro, sin fetch, y re-render
    // acotado al cuerpo (los KPIs siguen a las filas visibles).
    const actSortTh = event.target.closest("[data-commercial-act-sort]");
    if (actSortTh) {
      commercialToggleActSort(actSortTh.dataset.commercialActSort);
      commercialRenderActivitiesBody();
      return;
    }
    const forecastClear = event.target.closest("[data-commercial-forecast-clear]");
    if (forecastClear) {
      commercialState.forecastStageFilter = "";
      commercialState.forecastSellerFilter = "";
      commercialRenderForecastPanel();
      return;
    }
    // Prospectos — "✕ Limpiar" (Commercial Tile 5, ver HANDOFF): MISMO
    // criterio que Forecast — resetea Vendedor + Buscar de una sola vez y
    // re-renderiza el panel entero (la barra de filtros también refleja el
    // reset, a diferencia de Activas — acá no hay costo de foco porque
    // Limpiar nunca se clickea a mitad de tecleo).
    const prospectsClear = event.target.closest("[data-commercial-prospects-clear]");
    if (prospectsClear) {
      commercialState.prospectsSellerFilter = "";
      commercialState.prospectsSearchText = "";
      commercialRenderProspectsPanel();
      return;
    }
    // Prospectos — orden de columna: re-render QUIRÚRGICO de
    // `[data-commercial-prospects-table]`, MISMO criterio que el sort de
    // Activas — client-side puro sobre el resultado YA filtrado por
    // Vendedor/Buscar.
    const prospectsSortTh = event.target.closest("[data-commercial-prospects-sort]");
    if (prospectsSortTh) {
      commercialToggleProspectsSort(prospectsSortTh.dataset.commercialProspectsSort);
      const host = root.querySelector("[data-commercial-prospects-table]");
      if (host) host.innerHTML = commercialProspectsBlockHtml(commercialCurrentProspectsRows());
      return;
    }
    // Ganadas — "✕ Limpiar" (Commercial Tile 6, ver HANDOFF): resetea
    // Vendedor Y Cliente en un solo gesto.
    const ganadasClear = event.target.closest("[data-commercial-ganadas-clear]");
    if (ganadasClear) {
      commercialGanadasClearFilters();
      return;
    }
    // Ganadas — orden de columna: re-render QUIRÚRGICO de
    // `[data-commercial-ganadas-results]` (ranking + tabla, MISMO
    // subconjunto), MISMO criterio que Prospectos/Activas.
    const ganadasSortTh = event.target.closest("[data-commercial-ganadas-sort]");
    if (ganadasSortTh) {
      commercialToggleGanadasSort(ganadasSortTh.dataset.commercialGanadasSort);
      const host = root.querySelector("[data-commercial-ganadas-results]");
      if (host) host.innerHTML = commercialGanadasResultsHtml(commercialGanadasFilteredRows());
      return;
    }
    // Ganadas — click en una fila del ranking (Commercial Tile 6, ver
    // HANDOFF): MISMA cascada que Creadas — filtra por ese vendedor, SIN
    // toggle-to-clear (clickear el ya activo lo mantiene).
    const ganadasSellerPick = event.target.closest("[data-commercial-ganadas-seller-pick]");
    if (ganadasSellerPick) {
      commercialGanadasSetSeller(ganadasSellerPick.dataset.commercialGanadasSellerPick);
      return;
    }
    // Perdidas — "✕ Limpiar" (Commercial Tile 7, ver HANDOFF): resetea
    // Vendedor, Cliente Y Motivo — NUNCA el año (owner §11).
    const perdidasClear = event.target.closest("[data-commercial-perdidas-clear]");
    if (perdidasClear) {
      commercialPerdidasClearFilters();
      return;
    }
    // Perdidas — ✕ de un chip de contexto individual: limpia SÓLO ese eje
    // (MISMO criterio que Creadas — descartar un filtro no es "resetear
    // todo", eso es Limpiar).
    if (event.target.closest("[data-commercial-perdidas-seller-clear]")) {
      commercialPerdidasSetSeller("");
      return;
    }
    if (event.target.closest("[data-commercial-perdidas-client-clear]")) {
      commercialPerdidasSetClient("");
      return;
    }
    if (event.target.closest("[data-commercial-perdidas-motivo-clear]")) {
      commercialPerdidasSetMotivo("");
      return;
    }
    // Perdidas — orden de columna: re-render QUIRÚRGICO de
    // `[data-commercial-perdidas-table]` (ranking/motivo no cambian con el
    // orden de la tabla, así que no hace falta tocarlos).
    const perdidasSortTh = event.target.closest("[data-commercial-perdidas-sort]");
    if (perdidasSortTh) {
      commercialTogglePerdidasSort(perdidasSortTh.dataset.commercialPerdidasSort);
      const host = root.querySelector("[data-commercial-perdidas-table]");
      if (host) host.innerHTML = commercialPerdidasTableHtml(commercialPerdidasTableRows());
      return;
    }
    // Perdidas — click en una fila del ranking: MISMA cascada que Ganadas
    // (SIN toggle-to-clear).
    const perdidasSellerPick = event.target.closest("[data-commercial-perdidas-seller-pick]");
    if (perdidasSellerPick) {
      commercialPerdidasSetSeller(perdidasSellerPick.dataset.commercialPerdidasSellerPick);
      return;
    }
    // Perdidas — click en un motivo del desglose: CON toggle-to-clear (ver
    // `commercialPerdidasSetMotivo` — MISMA semántica que el doughnut legacy).
    const perdidasMotivoPick = event.target.closest("[data-commercial-perdidas-motivo-pick]");
    if (perdidasMotivoPick) {
      commercialPerdidasSetMotivo(perdidasMotivoPick.dataset.commercialPerdidasMotivoPick);
      return;
    }
    // Suspendidas — "✕ Limpiar" (Commercial Tile 8, ver HANDOFF): MISMO
    // criterio que Prospectos — única resetea Vendedor (es el único filtro:
    // owner §7 excluye Cliente/Buscar/ranking/chart para este tile).
    const suspendidasClear = event.target.closest("[data-commercial-suspendidas-clear]");
    if (suspendidasClear) {
      commercialState.suspendidasSellerFilter = "";
      commercialRenderSuspendidasPanel();
      return;
    }
    // Suspendidas — orden de columna: re-render QUIRÚRGICO de
    // `[data-commercial-suspendidas-table]`, MISMO criterio que Prospectos.
    const suspendidasSortTh = event.target.closest("[data-commercial-suspendidas-sort]");
    if (suspendidasSortTh) {
      commercialToggleSuspendidasSort(suspendidasSortTh.dataset.commercialSuspendidasSort);
      const host = root.querySelector("[data-commercial-suspendidas-table]");
      if (host) host.innerHTML = commercialSuspendidasBlockHtml(commercialCurrentSuspendidasRows());
      return;
    }
    // Bucket 1 — Licencias/Detalle/Historial Perdidas (ver HANDOFF): MISMO
    // criterio que Suspendidas — "✕ Limpiar" re-renderiza el panel ENTERO
    // (necesita reconstruir la barra de filtros, cuyos `<select>` pierden
    // su valor elegido), orden de columna re-renderiza SÓLO la tabla.
    const licenciasClear = event.target.closest("[data-commercial-licencias-clear]");
    if (licenciasClear) { commercialLicenciasClearFilters(); return; }
    const licenciasSortTh = event.target.closest("[data-commercial-licencias-sort]");
    if (licenciasSortTh) {
      commercialToggleLicenciasSort(licenciasSortTh.dataset.commercialLicenciasSort);
      const host = root.querySelector("[data-commercial-licencias-table]");
      if (host) host.innerHTML = commercialLicenciasTableHtml(commercialLicenciasFilteredRows());
      return;
    }
    const detalleClear = event.target.closest("[data-commercial-detalle-clear]");
    if (detalleClear) { commercialDetalleClearFilters(); return; }
    const detalleSortTh = event.target.closest("[data-commercial-detalle-sort]");
    if (detalleSortTh) {
      commercialToggleDetalleSort(detalleSortTh.dataset.commercialDetalleSort);
      const host = root.querySelector("[data-commercial-detalle-table]");
      if (host) host.innerHTML = commercialDetalleTableHtml(commercialDetalleFilteredRows());
      return;
    }
    const historialClear = event.target.closest("[data-commercial-historial-clear]");
    if (historialClear) { commercialLostHistoryClearFilters(); return; }
    const historialSortTh = event.target.closest("[data-commercial-historial-sort]");
    if (historialSortTh) {
      commercialToggleLostHistorySort(historialSortTh.dataset.commercialHistorialSort);
      const host = root.querySelector("[data-commercial-historial-table]");
      if (host) host.innerHTML = commercialLostHistoryTableHtml(commercialLostHistoryFilteredRows());
      return;
    }
    // Bucket 2 — Clientes: sub-tab Clientes/Ex Clientes (ver HANDOFF).
    const clientesSubtab = event.target.closest("[data-commercial-clientes-subtab]");
    if (clientesSubtab) { commercialSetClientesSubTab(clientesSubtab.dataset.commercialClientesSubtab); return; }
    // Leads — fila de empresa: expande/colapsa, MISMO patrón que Creadas
    // (array de claves abiertas) — re-render QUIRÚRGICO de la tabla.
    const leadsToggle = event.target.closest("[data-commercial-leads-toggle]");
    if (leadsToggle) {
      const key = leadsToggle.dataset.commercialLeadsToggle;
      const open = commercialState.leadsExpanded;
      const at = open.indexOf(key);
      if (at === -1) open.push(key); else open.splice(at, 1);
      const host = root.querySelector("[data-commercial-leads-table]");
      if (host) host.innerHTML = commercialLeadsTableHtml(commercialLeadsFilteredCompanies());
      return;
    }
    const leadsClear = event.target.closest("[data-commercial-leads-clear]");
    if (leadsClear) { commercialLeadsClearFilters(); return; }
    const leadsSortTh = event.target.closest("[data-commercial-leads-sort]");
    if (leadsSortTh) {
      commercialToggleLeadsSort(leadsSortTh.dataset.commercialLeadsSort);
      const host = root.querySelector("[data-commercial-leads-table]");
      if (host) host.innerHTML = commercialLeadsTableHtml(commercialLeadsFilteredCompanies());
      return;
    }
    // Clientes — fila de cliente: expande/colapsa. MISMO criterio.
    const clientesToggle = event.target.closest("[data-commercial-clientes-toggle]");
    if (clientesToggle) {
      const id = Number(clientesToggle.dataset.commercialClientesToggle);
      const open = commercialState.clientesExpanded;
      const at = open.indexOf(id);
      if (at === -1) open.push(id); else open.splice(at, 1);
      const host = root.querySelector("[data-commercial-clientes-table]");
      if (host) host.innerHTML = commercialClientesTableHtml(commercialClientesFilteredRows());
      return;
    }
    const clientesClear = event.target.closest("[data-commercial-clientes-clear]");
    if (clientesClear) { commercialClientesClearFilters(); return; }
    const clientesSortTh = event.target.closest("[data-commercial-clientes-sort]");
    if (clientesSortTh) {
      commercialToggleClientesSort(clientesSortTh.dataset.commercialClientesSort);
      const host = root.querySelector("[data-commercial-clientes-table]");
      if (host) host.innerHTML = commercialClientesTableHtml(commercialClientesFilteredRows());
      return;
    }
    const exClientesClear = event.target.closest("[data-commercial-ex-clientes-clear]");
    if (exClientesClear) { commercialExClientesClearFilters(); return; }
    // Analítica del Pipeline — orden de columna de las TRES sub-vistas con
    // tabla (Edad/Tiempo de cierre/Estancadas — Tiempo por etapa/Conversión
    // etapa a etapa son ranking `.bar-row`, sin sort): re-render QUIRÚRGICO
    // de su propia tabla, MISMO criterio que Suspendidas.
    const pipelineAgeSortTh = event.target.closest("[data-commercial-pipeline-age-sort]");
    if (pipelineAgeSortTh) {
      commercialTogglePipelineAgeSort(pipelineAgeSortTh.dataset.commercialPipelineAgeSort);
      const host = root.querySelector("[data-commercial-pipeline-age-table]");
      const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.pipeline_age.opportunities) || [];
      if (host) host.innerHTML = commercialPipelineAgeBlockHtml(rows);
      return;
    }
    const closeTimeSortTh = event.target.closest("[data-commercial-close-time-sort]");
    if (closeTimeSortTh) {
      commercialToggleCloseTimeSort(closeTimeSortTh.dataset.commercialCloseTimeSort);
      const host = root.querySelector("[data-commercial-close-time-table]");
      const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.close_time.opportunities) || [];
      if (host) host.innerHTML = commercialCloseTimeBlockHtml(rows);
      return;
    }
    const stagnantSortTh = event.target.closest("[data-commercial-stagnant-sort]");
    if (stagnantSortTh) {
      commercialToggleStagnantSort(stagnantSortTh.dataset.commercialStagnantSort);
      const host = root.querySelector("[data-commercial-stagnant-table]");
      const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stagnant.opportunities) || [];
      if (host) host.innerHTML = commercialStagnantBlockHtml(rows);
      return;
    }
    // Tiempo por etapa / Conversión etapa a etapa — owner corrective (ver
    // HANDOFF): pasaron de ranking `.bar-row` sin sort a tabla ordenable,
    // MISMO criterio de re-render QUIRÚRGICO que el resto de la familia.
    const stageTimeSortTh = event.target.closest("[data-commercial-stage-time-sort]");
    if (stageTimeSortTh) {
      commercialToggleStageTimeSort(stageTimeSortTh.dataset.commercialStageTimeSort);
      const host = root.querySelector("[data-commercial-stage-time-table]");
      const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stage_time) || [];
      if (host) host.innerHTML = commercialStageTimeBlockHtml(rows);
      return;
    }
    const stageConversionSortTh = event.target.closest("[data-commercial-stage-conversion-sort]");
    if (stageConversionSortTh) {
      commercialToggleStageConversionSort(stageConversionSortTh.dataset.commercialStageConversionSort);
      const host = root.querySelector("[data-commercial-stage-conversion-table]");
      const rows = (commercialState.pipelineAnalyticsDetail && commercialState.pipelineAnalyticsDetail.stage_conversion) || [];
      if (host) host.innerHTML = commercialStageConversionBlockHtml(rows);
      return;
    }
    // Conversión de Leads — orden de columna de la tabla combinada (YA
    // filtrada por las facetas): re-render QUIRÚRGICO, MISMO criterio.
    const leadConversionSortTh = event.target.closest("[data-commercial-lead-conversion-sort]");
    if (leadConversionSortTh) {
      commercialToggleLeadConversionSort(leadConversionSortTh.dataset.commercialLeadConversionSort);
      const host = root.querySelector("[data-commercial-lead-conversion-table]");
      if (host) host.innerHTML = commercialLeadConversionTableHtml(commercialLeadConversionFilteredRows());
      return;
    }
    // Conversión de Leads — "✕ Limpiar" (owner corrective, ver HANDOFF):
    // resetea todas las facetas de una sola vez y re-renderiza el panel
    // ENTERO (MISMO criterio que Perdidas — acá tampoco hay costo de foco,
    // Limpiar nunca se clickea a mitad de tecleo).
    const leadConversionClear = event.target.closest("[data-commercial-lead-conversion-clear]");
    if (leadConversionClear) {
      commercialLeadConversionClearFilters();
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
      commercialMaybeLoadCreadas(kpiTrigger);
      commercialMaybeLoadProspects(kpiTrigger);
      commercialMaybeLoadGanadas(kpiTrigger);
      commercialMaybeLoadPerdidas(kpiTrigger);
      commercialMaybeLoadSuspendidas(kpiTrigger);
      commercialMaybeLoadPipelineAnalyticsDetail(kpiTrigger);
      commercialMaybeLoadLeadConversionDetail(kpiTrigger);
      commercialMaybeScrollToOpenedAnalysis(kpiTrigger, root);
      return;
    }
    const capButton = event.target.closest("[data-commercial-capability]");
    if (capButton) {
      commercialSetCapability(capButton.dataset.commercialCapability);
      return;
    }
    // "Retrasadas"/"Por vencer" clickeables (owner follow-up, ver HANDOFF):
    // sólo scroll a la sección ya visible — ver `commercialActJumpToSection`.
    const actJump = event.target.closest("[data-commercial-act-jump]");
    if (actJump) {
      commercialActJumpToSection(actJump.dataset.commercialActJump, root);
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
  // Prospectos — Vendedor (Commercial Tile 5, ver HANDOFF): filtro LOCAL
  // sobre el payload ya cargado, MISMO criterio que Activas. Listener
  // SEPARADO (mismo motivo que los de arriba: una sola rama con `return`
  // temprano cortaría cualquier otro `<select>` que se agregue después).
  document.addEventListener("change", event => {
    const sellerSelect = event.target.closest("[data-commercial-prospects-seller-filter]");
    if (!sellerSelect) return;
    commercialState.prospectsSellerFilter = sellerSelect.value;
    const host = root.querySelector("[data-commercial-prospects-table]");
    if (host) host.innerHTML = commercialProspectsBlockHtml(commercialCurrentProspectsRows());
  });
  // Ganadas — Año (Commercial Tile 6, ver HANDOFF): COLD YEAR — si ese año
  // no está hidratado se pide SOLO ese año (`commercialHydrateGanadasYear`
  // corta sin fetch si ya está en cache, MISMO criterio que Creadas). A
  // diferencia de Creadas, el Vendedor NO se preserva entre años: Ganadas no
  // tiene mes/semana expandidos que justifiquen esa complejidad, y el owner
  // no pidió persistencia — resetear es el comportamiento más simple y
  // predecible ("un año nuevo empieza sin filtro").
  document.addEventListener("change", event => {
    const yearSelect = event.target.closest("[data-commercial-ganadas-year]");
    if (yearSelect) {
      commercialState.ganadasYear = yearSelect.value;
      commercialState.ganadasSellerFilter = "";
      commercialState.ganadasClientFilter = "";
      commercialHydrateGanadasYear(yearSelect.value, { activo: true });
      return;
    }
    // Ganadas — Vendedor: filtro LOCAL sobre el payload ya cargado. Cero red.
    const sellerSelect = event.target.closest("[data-commercial-ganadas-seller-filter]");
    if (sellerSelect) {
      commercialGanadasSetSeller(sellerSelect.value);
      return;
    }
    // Ganadas — Cliente (owner corrective, post-smoke): MISMO criterio que
    // Vendedor, filtro LOCAL, cero red.
    const clientSelect = event.target.closest("[data-commercial-ganadas-client-filter]");
    if (clientSelect) {
      commercialGanadasSetClient(clientSelect.value);
      return;
    }
  });
  // Perdidas — Año (Commercial Tile 7, ver HANDOFF): MISMO contrato COLD
  // YEAR que Ganadas. Vendedor/Cliente/Motivo NO se preservan entre años —
  // MISMO criterio que Ganadas (resetear es lo más simple y predecible).
  document.addEventListener("change", event => {
    const yearSelect = event.target.closest("[data-commercial-perdidas-year]");
    if (yearSelect) {
      commercialState.perdidasYear = yearSelect.value;
      commercialState.perdidasSellerFilter = "";
      commercialState.perdidasClientFilter = "";
      commercialState.perdidasMotivoFilter = "";
      commercialHydratePerdidasYear(yearSelect.value, { activo: true });
      return;
    }
    const sellerSelect = event.target.closest("[data-commercial-perdidas-seller-filter]");
    if (sellerSelect) {
      commercialPerdidasSetSeller(sellerSelect.value);
      return;
    }
    const clientSelect = event.target.closest("[data-commercial-perdidas-client-filter]");
    if (clientSelect) {
      commercialPerdidasSetClient(clientSelect.value);
      return;
    }
  });
  // Suspendidas — Vendedor (Commercial Tile 8, ver HANDOFF): filtro LOCAL
  // sobre el payload ya cargado, MISMO criterio que Prospectos (único
  // `<select>` del tile — owner §7). Listener SEPARADO, mismo motivo que los
  // de arriba.
  document.addEventListener("change", event => {
    const sellerSelect = event.target.closest("[data-commercial-suspendidas-seller-filter]");
    if (!sellerSelect) return;
    commercialState.suspendidasSellerFilter = sellerSelect.value;
    const host = root.querySelector("[data-commercial-suspendidas-table]");
    if (host) host.innerHTML = commercialSuspendidasBlockHtml(commercialCurrentSuspendidasRows());
  });
  // Bucket 1 — Licencias (ver HANDOFF): Etapa/Vendedor son filtros LOCALES,
  // MISMA composición AND que el resto de Comercial. Re-render QUIRÚRGICO
  // de `[data-commercial-licencias-table]`, nunca la barra (los otros
  // `<select>` perderían el valor recién elegido si se regenerara).
  document.addEventListener("change", event => {
    const stageSelect = event.target.closest("[data-commercial-licencias-stage-filter]");
    const sellerSelect = event.target.closest("[data-commercial-licencias-seller-filter]");
    if (!stageSelect && !sellerSelect) return;
    if (stageSelect) commercialState.licStageFilter = stageSelect.value;
    if (sellerSelect) commercialState.licSellerFilter = sellerSelect.value;
    const host = root.querySelector("[data-commercial-licencias-table]");
    if (host) host.innerHTML = commercialLicenciasTableHtml(commercialLicenciasFilteredRows());
  });
  // Bucket 1 — Detalle Oportunidades: MISMO criterio que Licencias.
  document.addEventListener("change", event => {
    const stageSelect = event.target.closest("[data-commercial-detalle-stage-filter]");
    const sellerSelect = event.target.closest("[data-commercial-detalle-seller-filter]");
    if (!stageSelect && !sellerSelect) return;
    if (stageSelect) commercialState.detStageFilter = stageSelect.value;
    if (sellerSelect) commercialState.detSellerFilter = sellerSelect.value;
    const host = root.querySelector("[data-commercial-detalle-table]");
    if (host) host.innerHTML = commercialDetalleTableHtml(commercialDetalleFilteredRows());
  });
  // Bucket 1 — Historial de Perdidas: Año/Vendedor/Motivo, MISMO criterio.
  document.addEventListener("change", event => {
    const yearSelect = event.target.closest("[data-commercial-historial-year-filter]");
    const sellerSelect = event.target.closest("[data-commercial-historial-seller-filter]");
    const motivoSelect = event.target.closest("[data-commercial-historial-motivo-filter]");
    if (!yearSelect && !sellerSelect && !motivoSelect) return;
    if (yearSelect) commercialState.histYearFilter = yearSelect.value;
    if (sellerSelect) commercialState.histSellerFilter = sellerSelect.value;
    if (motivoSelect) commercialState.histMotivoFilter = motivoSelect.value;
    const host = root.querySelector("[data-commercial-historial-table]");
    if (host) host.innerHTML = commercialLostHistoryTableHtml(commercialLostHistoryFilteredRows());
  });
  // Bucket 2 — Clientes: Vendedor/Estado, MISMO criterio.
  document.addEventListener("change", event => {
    const sellerSelect = event.target.closest("[data-commercial-clientes-seller-filter]");
    const statusSelect = event.target.closest("[data-commercial-clientes-status-filter]");
    if (!sellerSelect && !statusSelect) return;
    if (sellerSelect) commercialState.clientesSellerFilter = sellerSelect.value;
    if (statusSelect) commercialState.clientesStatusFilter = statusSelect.value;
    const host = root.querySelector("[data-commercial-clientes-table]");
    if (host) host.innerHTML = commercialClientesTableHtml(commercialClientesFilteredRows());
  });
  // Bucket 2 — Ex Clientes: Vendedor.
  document.addEventListener("change", event => {
    const sellerSelect = event.target.closest("[data-commercial-ex-clientes-seller-filter]");
    if (!sellerSelect) return;
    commercialState.exClientesSellerFilter = sellerSelect.value;
    const host = root.querySelector("[data-commercial-ex-clientes-table]");
    if (host) host.innerHTML = commercialExClientesTableHtml(commercialExClientesFilteredRows());
  });
  // Conversión de Leads (owner corrective, ver HANDOFF): las CUATRO
  // facetas de `<select>` (Campaña/Medio/Origen/Vendedor) son filtros
  // LOCALES sobre el payload ya cargado, MISMA composición AND que el
  // legacy `_filterLeadConvTable`. Re-render QUIRÚRGICO de
  // `[data-commercial-lead-conversion-table]` — nunca la barra de filtros,
  // que perdería el valor recién elegido en el resto de los `<select>` si
  // se regenerara. Listener SEPARADO, mismo motivo que los de arriba.
  document.addEventListener("change", event => {
    const campanaSelect = event.target.closest("[data-commercial-lead-conversion-campana-filter]");
    const medioSelect = event.target.closest("[data-commercial-lead-conversion-medio-filter]");
    const origenSelect = event.target.closest("[data-commercial-lead-conversion-origen-filter]");
    const sellerSelect = event.target.closest("[data-commercial-lead-conversion-seller-filter]");
    // "Resultado" (owner follow-up): sexta faceta, MISMO patrón AND — ver
    // `commercialLeadConversionFilteredRows`.
    const outcomeSelect = event.target.closest("[data-commercial-lead-conversion-outcome-filter]");
    if (!campanaSelect && !medioSelect && !origenSelect && !sellerSelect && !outcomeSelect) return;
    if (campanaSelect) commercialState.leadConversionCampanaFilter = campanaSelect.value;
    if (medioSelect) commercialState.leadConversionMedioFilter = medioSelect.value;
    if (origenSelect) commercialState.leadConversionOrigenFilter = origenSelect.value;
    if (sellerSelect) commercialState.leadConversionSellerFilter = sellerSelect.value;
    if (outcomeSelect) commercialState.leadConversionOutcomeFilter = outcomeSelect.value;
    const host = root.querySelector("[data-commercial-lead-conversion-table]");
    if (host) host.innerHTML = commercialLeadConversionTableHtml(commercialLeadConversionFilteredRows());
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
  // Año del drill de Creadas (Creadas Legacy Parity, ver HANDOFF). El año es
  // la UNICA interaccion del drill que puede hidratar: un año de la ventana
  // caliente responde del cache, uno frio se pide. Lo demas —mes, semana,
  // vendedor, busqueda— sigue siendo lectura local con cero round-trips.
  //
  // (Antes esto decia "el payload es el MISMO, cero round-trip", que era cierto
  // cuando la carga unica traia TODA la historia. Ya no.)
  //
  // Mes/semana expandidos se resetean: un mes expandido del año anterior no
  // tiene sentido bajo el año nuevo. Listener SEPARADO, mismo criterio que los
  // de arriba.
  document.addEventListener("change", event => {
    const yearSelect = event.target.closest("[data-commercial-creadas-year]");
    if (yearSelect) {
      commercialState.creadasYear = yearSelect.value;
      // COLD YEAR: si ese año no está hidratado, se pide SOLO ese año, con el
      // estado de carga acotado al panel de Creadas. Si ya está en cache
      // (`ready`), `commercialHydrateCreadasYear` corta sin fetch y la segunda
      // selección es inmediata.
      commercialHydrateCreadasYear(yearSelect.value, { activo: true });
      // Expansión y detalle se resetean: un mes del año anterior no existe bajo
      // el año nuevo. El VENDEDOR y la BÚSQUEDA se PRESERVAN — "quiero ver a
      // Juan en 2025 y en 2026" es la pregunta natural, y perder el filtro en
      // cada cambio de año obligaría a re-elegirlo cada vez. Pero si ese
      // vendedor no existe en el año destino se limpia solo: dejarlo puesto
      // mostraría una tabla vacía sin que se vea por qué.
      commercialState.creadasExpandedMonths = [];
      commercialState.creadasSelection = null;
      const months = (commercialState.creadas && commercialState.creadas.months) || [];
      if (commercialState.creadasSeller
          && commercialCreadasSellers(months, yearSelect.value).indexOf(commercialState.creadasSeller) === -1) {
        commercialState.creadasSeller = "";
      }
      commercialRenderCreadasPanel();
      return;
    }
    // VENDEDOR — filtro LOCAL sobre el payload ya cargado. Cero red.
    const sellerSelect = event.target.closest("[data-commercial-creadas-seller]");
    if (sellerSelect) {
      commercialCreadasSetSeller(sellerSelect.value);
      return;
    }
  });

  // BUSCAR — `input` y no `change`, para filtrar en cada tecla (UX esperada de
  // un buscador), MISMO criterio que las búsquedas de Activas y Actividades.
  // Cero red: filtra el payload ya cargado.
  //
  // El foco y el caret se restauran a mano porque el re-render del panel
  // reemplaza el `<input>` y se los llevaría en cada tecla — MISMO problema y
  // MISMA solución que la búsqueda de Actividades.
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-creadas-search]");
    if (!search) return;
    commercialState.creadasSearch = search.value;
    const caret = search.selectionStart;
    // El host del panel SOBREVIVE al re-render (sólo cambia su `innerHTML`), así
    // que se captura ANTES y se busca DENTRO de él: la consulta queda scopeada
    // al panel y nunca al documento, MISMO criterio que el resto de los hooks
    // de la vertical.
    const panel = search.closest("[data-commercial-creadas-body]");
    commercialRenderCreadasPanel();
    const again = panel && panel.querySelector("[data-commercial-creadas-search]");
    if (again) {
      again.focus();
      try { again.setSelectionRange(caret, caret); } catch (e) { /* type=search sin soporte */ }
    }
  });
  // Búsqueda libre (C5e, ver HANDOFF): `input`, no `change` — filtra en cada
  // tecla (UX esperada de un buscador), nunca espera blur/Enter. SÓLO toca
  // `[data-commercial-opportunities]` — jamás el Funnel (contrato explícito
  // del owner) — y, crucialmente, nunca la barra de filtros: si regenerara
  // el `<input>` en cada tecla, el foco/cursor se perdería después del
  // primer carácter.
  document.addEventListener("input", event => {
    // Búsqueda de Actividades (C9): re-render QUIRÚRGICO del cuerpo, así el
    // `<input>` no se recrea y no pierde el foco entre teclas. Rama primero,
    // antes del `return` temprano de la búsqueda de Oportunidades.
    const actSearch = event.target.closest("[data-commercial-act-search]");
    if (actSearch) {
      commercialState.actSearchText = actSearch.value;
      commercialRenderActivitiesBody();
      return;
    }
    const search = event.target.closest("[data-commercial-search]");
    if (!search) return;
    commercialState.oppSearchText = search.value;
    const host = root.querySelector("[data-commercial-opportunities]");
    if (host) host.innerHTML = commercialOpportunitiesBlockHtml(commercialCurrentTableRows());
  });
  // Prospectos — Buscar (Commercial Tile 5, ver HANDOFF): `input`, no
  // `change` — MISMO criterio que la búsqueda de Activas. SÓLO toca
  // `[data-commercial-prospects-table]`, nunca la barra de filtros (el
  // `<input>` perdería foco/cursor en cada tecla si se regenerara).
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-prospects-search]");
    if (!search) return;
    commercialState.prospectsSearchText = search.value;
    const host = root.querySelector("[data-commercial-prospects-table]");
    if (host) host.innerHTML = commercialProspectsBlockHtml(commercialCurrentProspectsRows());
  });
  // Conversión de Leads — Buscar (owner corrective, ver HANDOFF): `input`,
  // no `change` — MISMO criterio que Prospectos. SÓLO toca
  // `[data-commercial-lead-conversion-table]`, nunca la barra de filtros.
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-lead-conversion-search]");
    if (!search) return;
    commercialState.leadConversionSearchText = search.value;
    const host = root.querySelector("[data-commercial-lead-conversion-table]");
    if (host) host.innerHTML = commercialLeadConversionTableHtml(commercialLeadConversionFilteredRows());
  });
  // Bucket 1 — Buscar de Licencias/Detalle/Historial Perdidas (ver HANDOFF):
  // `input`, no `change` — MISMO criterio que Prospectos/Conversión de
  // Leads. SÓLO tocan su propia tabla, nunca la barra de filtros.
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-licencias-search]");
    if (!search) return;
    commercialState.licSearchText = search.value;
    const host = root.querySelector("[data-commercial-licencias-table]");
    if (host) host.innerHTML = commercialLicenciasTableHtml(commercialLicenciasFilteredRows());
  });
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-detalle-search]");
    if (!search) return;
    commercialState.detSearchText = search.value;
    const host = root.querySelector("[data-commercial-detalle-table]");
    if (host) host.innerHTML = commercialDetalleTableHtml(commercialDetalleFilteredRows());
  });
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-historial-search]");
    if (!search) return;
    commercialState.histSearchText = search.value;
    const host = root.querySelector("[data-commercial-historial-table]");
    if (host) host.innerHTML = commercialLostHistoryTableHtml(commercialLostHistoryFilteredRows());
  });
  // Bucket 2 — Buscar de Leads/Clientes/Ex Clientes, MISMO criterio.
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-leads-search]");
    if (!search) return;
    commercialState.leadsSearchText = search.value;
    const host = root.querySelector("[data-commercial-leads-table]");
    if (host) host.innerHTML = commercialLeadsTableHtml(commercialLeadsFilteredCompanies());
  });
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-clientes-search]");
    if (!search) return;
    commercialState.clientesSearchText = search.value;
    const host = root.querySelector("[data-commercial-clientes-table]");
    if (host) host.innerHTML = commercialClientesTableHtml(commercialClientesFilteredRows());
  });
  document.addEventListener("input", event => {
    const search = event.target.closest("[data-commercial-ex-clientes-search]");
    if (!search) return;
    commercialState.exClientesSearchText = search.value;
    const host = root.querySelector("[data-commercial-ex-clientes-table]");
    if (host) host.innerHTML = commercialExClientesTableHtml(commercialExClientesFilteredRows());
  });
  // Activación por teclado (Enter/Espacio) de un trigger `[data-event]`: el
  // motor compartido cablea su PROPIO `keydown` directo sobre el trigger
  // (nunca dispara un evento `click` sintético), así que el delegado de
  // click de arriba no lo ve — este delegado espeja el mismo criterio para
  // ese segundo camino de activación, sin duplicar el toggle/show en sí.
  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    // RANKING de Creadas — la fila es `role="button"` + `tabindex="0"`, no un
    // `<button>` nativo (envolver `.bar-row` rompería su layout flex de tres
    // hijos), así que Enter/Space van a mano — MISMO criterio que
    // `initModuleFilter` en Métricas de Uso, por la misma razón.
    // `preventDefault` evita que Space scrollee la página, que es su default
    // sobre un elemento no-botón.
    //
    // Va DENTRO de este delegado y no en uno nuevo: commercial.js mantiene UN
    // SOLO delegado por tipo de evento (hay un test que lo fija) — dos
    // listeners de `keydown` es justo el tipo de duplicación que ese contrato
    // existe para evitar.
    // "Retrasadas"/"Por vencer" — MISMO camino de teclado (role="button",
    // no `<button>` nativo).
    const actJumpKey = event.target.closest && event.target.closest("[data-commercial-act-jump]");
    if (actJumpKey) {
      event.preventDefault();
      commercialActJumpToSection(actJumpKey.dataset.commercialActJump, root);
      return;
    }
    const sellerPick = event.target.closest && event.target.closest("[data-commercial-creadas-seller-pick]");
    if (sellerPick) {
      event.preventDefault();
      commercialCreadasSetSeller(sellerPick.dataset.commercialCreadasSellerPick);
      return;
    }
    // Ganadas — MISMO camino de teclado que el ranking de Creadas (Enter/
    // Espacio sobre `.bar-row`, que no es un `<button>` nativo).
    const ganadasSellerPick = event.target.closest && event.target.closest("[data-commercial-ganadas-seller-pick]");
    if (ganadasSellerPick) {
      event.preventDefault();
      commercialGanadasSetSeller(ganadasSellerPick.dataset.commercialGanadasSellerPick);
      return;
    }
    // Perdidas — MISMO camino de teclado, para el ranking Y el desglose por
    // Motivo (los dos son `.bar-row`, no `<button>` nativo).
    const perdidasSellerPick = event.target.closest && event.target.closest("[data-commercial-perdidas-seller-pick]");
    if (perdidasSellerPick) {
      event.preventDefault();
      commercialPerdidasSetSeller(perdidasSellerPick.dataset.commercialPerdidasSellerPick);
      return;
    }
    const perdidasMotivoPick = event.target.closest && event.target.closest("[data-commercial-perdidas-motivo-pick]");
    if (perdidasMotivoPick) {
      event.preventDefault();
      commercialPerdidasSetMotivo(perdidasMotivoPick.dataset.commercialPerdidasMotivoPick);
      return;
    }
    const kpiTrigger = event.target.closest("[data-event]");
    if (!kpiTrigger) return;
    commercialNormalizeSharedTriggerActiveState(kpiTrigger, root);
    commercialMaybeLoadForecast(kpiTrigger);
    commercialMaybeRerenderForecastForMode(kpiTrigger);
    commercialMaybeLoadCreadas(kpiTrigger);
    commercialMaybeLoadProspects(kpiTrigger);
    commercialMaybeLoadGanadas(kpiTrigger);
    commercialMaybeLoadPerdidas(kpiTrigger);
    commercialMaybeLoadSuspendidas(kpiTrigger);
    commercialMaybeLoadPipelineAnalyticsDetail(kpiTrigger);
    commercialMaybeLoadLeadConversionDetail(kpiTrigger);
    commercialMaybeScrollToOpenedAnalysis(kpiTrigger, root);
  });
  commercialLoadData();
});
