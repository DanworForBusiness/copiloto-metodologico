// reporting.js — Reporting Integral V0 preview (Operations, ver HANDOFF).
//
// Align with Hub backbone corrective (ver HANDOFF): este archivo consume el
// MISMO backbone de dashboard ya usado por Staffing/Actividad en vez de
// reinventarlo — sticky filters (.dashboard-sticky-header/.whatsnew-filters/
// .staffing-filter-bar, ver tools.css), checkboxes verticales
// (.dashboard-checkbox-group), tiles KPI (.activity-kpi-grid/.activity-kpi),
// secciones draggable/colapsables (<details class="dashboard-section">
// + app.js::initDashboardSections) y el toolbar Expandir/Contraer
// (.staffing-timeline-toolbar, mismo motor treeExpandedSet/buildTree de
// abajo). CERO clases nuevas en tools.css — todo reusado tal cual (prioridad
// 1 de la regla de reuso, ver HANDOFF). Los ids de DOM target
// (experience-tabs/main-content) mantienen el prefijo "reporting-" del
// batch anterior para no colisionar con id="content" del shell del Hub.
// Funciones/variables globales verificadas SIN colisión con app.js — sin
// namespacing adicional a propósito (no introducir un framework/wrapper
// nuevo). Deduplicar contra el prototipo standalone
// (prototypes/reporting-integral-v0/index.html, que queda como referencia
// de diseño congelada) es tarea de V1, no de este preview.

// ============================================================================
// Dataset representativo (hardcoded, NO producción) — nombres reconocibles
// de estilo Danwor pero sin valores reales.
// ============================================================================
// Cartera / Proyectos (ver corrective — HANDOFF/task): las páginas fuente
// "Analisis Mantenimiento" y "Analisis Proyectos" NO son la misma fila
// reordenada (a diferencia de Horas Vendidas/Calendario) — son DOS
// conjuntos de filas distintos (contratos de mantenimiento vs. proyectos),
// confirmado por el pivotTable real de cada página:
//   Mantenimiento → Rows: Cliente, Proyecto (2 niveles)
//     Values: Diff Hs, Hs Trabajadas Acum, Hs Certif. Acum,
//             Hs Vendidas Mensuales, Facturadas, Status
//   Proyecto → Rows: Cliente, ID Proyecto Danwor, Proyecto (3 niveles en el
//     pivotTable FUENTE) — PERO owner live QA (corrective pre-Batch-A)
//     pidió explícitamente NO mostrar `idProyecto` como nivel visible (el
//     nodo mostraba el ID técnico, ej. "PROJ-0206", en vez de la
//     descripción de negocio) — la jerarquía RENDERIZADA es Cliente ›
//     Proyecto (2 niveles, ver CARTERA_CONFIG.Proyecto.keys); `idProyecto`
//     sigue viviendo en cada fila como campo interno (join key / futura
//     desambiguación si dos proyectos comparten descripción), nunca se
//     descartó del dato.
//     Values: Diff Hs, Hs Trabajadas Acum, Hs Certif. Acum, Hs Vendidas,
//             Facturadas, Status
// KPI real de cada página es un card simple: "Cantidad Mantenimientos" /
// "Cantidad Proyectos" — NUNCA los KPIs inventados de la versión anterior
// de este V0 (Contratos visibles/Hs trabajadas/Hs certificadas/Con diff
// negativo), retirados en ese corrective. Status real de Proyectos.Status
// es "En curso" | "Finalizado" (owner screenshots) — NUNCA
// activo/vencido/cerrado (eso era una invención de la primera versión de
// este V0 para la Cartera vieja).
//
// Cartera + Rentabilidad Completeness corrective (ver HANDOFF):
// - `anioInicio` (Proyecto) — slicer real confirmado en page_inventory.json
//   (`Proyectos.Año de Inicio` en Analisis Proyectos, SIN Meses.Mes ahí —
//   por eso Proyecto nunca muestra un filtro de mes/fecha).
// - `facturadas` — CORREGIDO: la versión anterior de este V0 mostraba un
//   "100%"/"90%"/"85%" inventado. El pivotTable real usa
//   `Comparativo[Total Horas Facturadas]` para esa columna (ver visual.json
//   de "Analisis Mantenimiento": Property "Total Horas Facturadas",
//   nativeQueryRef/displayName "Facturadas") — un número de HORAS aditivo,
//   MISMA naturaleza que Hs Trabajadas/Hs Certif. Acum, nunca un porcentaje.
//   Valores acá preservan la misma señal relativa que el % inventado
//   (100% pleno, YPF/Ternium por debajo) pero como horas reales.
//
// Cartera date-range filter corrective (ver HANDOFF, owner feedback):
// Mantenimiento reemplaza el combo Año+Mes por un rango Desde/Hasta — mismo
// `<input type="date">` que ya usa el dashboard de Actividad (Desde/Hasta,
// ver admin_activity_content.html), MISMA clase `.whatsnew-filter` +
// `input[type="date"]` (tools.css, sin CSS nuevo). `fecha` (ISO
// YYYY-MM-DD) reemplaza los `anio`/`mes` sueltos — una sola dimensión de
// fecha real en vez de dos facets categóricos separados.
// `cp` (owner feedback, ver HANDOFF: filtro de Client Partner faltante en
// Cartera) — MISMA asignación Cliente→CP ya usada en Comercial/Horas
// Vendidas (María López: Andesmar/Danone/Molinos; Diego Ramos: AySA/YPF/
// Ternium), consistencia cross-experiencia.
const CARTERA_MANTENIMIENTO_ROWS = [
  { cliente: "Andesmar", cp: "María López", proyecto: "Mantenimiento Costeo Directo", diff: -6, trabajadas: 134, certificadas: 128, vendidasMensuales: 140, facturadas: 128, status: "En curso", fecha: "2026-08-01" },
  { cliente: "Andesmar", cp: "María López", proyecto: "Soporte Infraestructura", diff: 3, trabajadas: 40, certificadas: 43, vendidasMensuales: 40, facturadas: 43, status: "En curso", fecha: "2026-08-01" },
  { cliente: "Danone", cp: "María López", proyecto: "Soporte Analítica", diff: 4, trabajadas: 92, certificadas: 96, vendidasMensuales: 88, facturadas: 96, status: "En curso", fecha: "2026-07-01" },
  { cliente: "Molinos", cp: "María López", proyecto: "Mantenimiento BI", diff: 0, trabajadas: 60, certificadas: 60, vendidasMensuales: 60, facturadas: 60, status: "Finalizado", fecha: "2026-06-01" },
];
const CARTERA_PROYECTO_ROWS = [
  { cliente: "YPF", cp: "Diego Ramos", idProyecto: "DAN-1042", proyecto: "Implementación SADI v2", diff: -18, trabajadas: 340, certificadas: 322, vendidas: 360, facturadas: 290, status: "En curso", anioInicio: "2026" },
  { cliente: "AySA", cp: "Diego Ramos", idProyecto: "DAN-1055", proyecto: "Costeo Federal", diff: 2, trabajadas: 210, certificadas: 212, vendidas: 208, facturadas: 212, status: "En curso", anioInicio: "2025" },
  { cliente: "Ternium", cp: "Diego Ramos", idProyecto: "DAN-0998", proyecto: "Rollout Reporting", diff: -9, trabajadas: 175, certificadas: 166, vendidas: 180, facturadas: 141, status: "Finalizado", anioInicio: "2026" },
];

// Badge de estado — primitivo compartido del Hub (Phase S, ver HANDOFF:
// `.dashboard-status-badge`/`--{color}` en tools.css, MISMO que Asignaciones)
// — el dominio solo elige la variante de COLOR, nunca dibuja su propio pill.
function statusPillCartera(status) {
  const color = status === "En curso" ? "green" : "grey";
  return `<span class="dashboard-status-badge dashboard-status-badge--${color}">${esc(status)}</span>`;
}

// Rollup jerárquico (ver corrective — HANDOFF: "Hierarchical Measure
// Rollup Backbone"): Diff Hs/Hs Trabajadas Acum/Hs Certif. Acum/Hs
// Vendidas(Mensuales) son ADITIVAS — SUM(hojas), vía `sumField` (contrato
// compartido, ver renderBusinessTreeRows). Diff Hs = Certificadas −
// Trabajadas por fila; SUM(diffs) == SUM(Certificadas) − SUM(Trabajadas)
// (la resta es lineal), así que sumar la columna Diff Hs YA da el diff
// correcto del grupo — no hace falta recalcularlo desde Certificadas/
// Trabajadas por separado. Facturadas/Status quedan SIN `rollup` a
// propósito: Facturadas es un % ya formateado en el dataset mock (sin un
// numerador/denominador propio verificado en el PBIP para este V0) y
// Status es categórico — ambos son Clase C (categórica), el padre queda en
// blanco, nunca un promedio/mayoría inventada.
// `key` por columna (owner feedback — ver HANDOFF): identificador estable
// para leer el mismo `rollup` ya definido acá desde OTRO consumidor (los
// tiles KPI de Resumen, ver `carteraKpiTiles`) sin repetir la fórmula de
// agregación ni depender del ORDEN posicional de `columns[]`.
const CARTERA_CONFIG = {
  "Mantenimiento": {
    rows: CARTERA_MANTENIMIENTO_ROWS, keys: ["cliente", "proyecto"], labels: ["Cliente", "Proyecto"],
    kpiLabel: "Cantidad Mantenimientos",
    columns: [
      { key: "diff", label: "Diff Hs", numeric: true, render: r => diffCell(r.diff), rollup: sumField("diff", diffCell) },
      { key: "trabajadas", label: "Hs Trabajadas Acum", numeric: true, render: r => r.trabajadas, rollup: sumField("trabajadas") },
      { key: "certificadas", label: "Hs Certif. Acum", numeric: true, render: r => r.certificadas, rollup: sumField("certificadas") },
      { key: "vendidas", label: "Hs Vendidas Mensuales", numeric: true, render: r => r.vendidasMensuales, rollup: sumField("vendidasMensuales") },
      // "Hs Facturadas" (antes "Facturadas" a secas, ver HANDOFF — owner
      // feedback: única columna del grupo Hs* sin el prefijo "Hs", ahora
      // consistente con sus columnas hermanas).
      { key: "facturadas", label: "Hs Facturadas", numeric: true, render: r => r.facturadas, rollup: sumField("facturadas") },
      { key: "estado", label: "Estado", numeric: false, statusTerminal: true, render: r => statusPillCartera(r.status), rollup: uniformValue("status", statusPillCartera) },
    ],
  },
  "Proyecto": {
    // Jerarquía visible = Cliente › Proyecto (owner live QA, corrective
    // pre-Batch-A): `idProyecto` YA NO es un nivel visible — mostraba el ID
    // técnico ("PROJ-0206") como si fuera el nodo de negocio, en vez de la
    // descripción real que el dropdown de Proyecto sí muestra
    // correctamente (mismo campo `proyecto`, ver facetSelectField).
    // `idProyecto` sigue viviendo en cada fila (identidad/join key interna,
    // `buildTree` no lo necesita para agrupar correctamente porque
    // `proyecto` ya es único por fila agregada en el service) — solo dejó
    // de ser un NIVEL de jerarquía separado.
    rows: CARTERA_PROYECTO_ROWS, keys: ["cliente", "proyecto"], labels: ["Cliente", "Proyecto"],
    kpiLabel: "Cantidad Proyectos",
    columns: [
      { key: "diff", label: "Diff Hs", numeric: true, render: r => diffCell(r.diff), rollup: sumField("diff", diffCell) },
      { key: "trabajadas", label: "Hs Trabajadas Acum", numeric: true, render: r => r.trabajadas, rollup: sumField("trabajadas") },
      { key: "certificadas", label: "Hs Certif. Acum", numeric: true, render: r => r.certificadas, rollup: sumField("certificadas") },
      { key: "vendidas", label: "Hs Vendidas", numeric: true, render: r => r.vendidas, rollup: sumField("vendidas") },
      { key: "facturadas", label: "Hs Facturadas", numeric: true, render: r => r.facturadas, rollup: sumField("facturadas") },
      { key: "estado", label: "Estado", numeric: false, statusTerminal: true, render: r => statusPillCartera(r.status), rollup: uniformValue("status", statusPillCartera) },
    ],
  },
};
// Tiles KPI adicionales de Resumen (owner feedback — ver HANDOFF): reusa el
// MISMO `rollup` ya definido por columna arriba — nunca una segunda
// fórmula de agregación repetida acá. `rows` son las filas YA filtradas
// (mismas que alimentan la tabla), así que el tile y la tabla nunca
// divergen.
const CARTERA_KPI_EXTRA = [
  { key: "diff", label: "Diff Hs" },
  { key: "trabajadas", label: "Hs Trabajadas" },
  { key: "certificadas", label: "Hs Certificadas" },
  { key: "vendidas", label: "Hs Vendidas" },
  { key: "facturadas", label: "Hs Facturadas" },
];
function carteraKpiTiles(cfg, rows) {
  return CARTERA_KPI_EXTRA.map(k => {
    const col = cfg.columns.find(c => c.key === k.key);
    // Diff Hs (owner live QA — corrective pre-Batch-A): `col.rollup` de esta
    // columna devuelve HTML YA formateado con signo/color (`diffCell`,
    // pensado para la celda de tabla) — pasar ESO por el primitivo KPI de
    // nuevo (que espera un número CRUDO) daba `NaN` → "—" aunque el
    // detalle filtrado tuviera valores reales (ej. Trabajadas=800/
    // Certificadas=1544 → el tile debía mostrar +744). El tile recalcula
    // el número CRUDO con el MISMO `sumField` aditivo (SUM(diffs) ==
    // SUM(Certificadas) − SUM(Trabajadas), ver comentario de
    // CARTERA_CONFIG) sin `diffCell` — el primitivo decide el signo/color.
    const value = k.key === "diff" ? sumField("diff")(rows) : col.rollup(rows);
    return renderDashboardKpi({ label: k.label, value, format: k.key === "diff" ? "signed" : "hours" });
  }).join("");
}

// Fecha local en YYYY-MM-DD (formato nativo de `<input type="date">`) — a
// propósito NO usa `toISOString()` (convierte a UTC, puede correr la fecha
// un día para atrás/adelante cerca de medianoche según el huso horario del
// browser); esto siempre refleja el día calendario LOCAL del usuario.
function isoDateLocal(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Default de Desde/Hasta/Año de Inicio (ver corrective — HANDOFF: "Cartera
// + Rentabilidad Completeness" + owner feedback posterior): resuelve al
// año/fecha calendario ACTUAL (`new Date()`, nunca un literal hardcodeado
// tipo "2026"/"Agosto"). Mantenimiento (UX1, owner live QA — vuelve de
// Desde/Hasta a Año+Mes, grano MENSUAL real de la fuente): default = año/
// mes calendario actual. Si ese año/mes deja de ser una opción válida bajo
// el Estado/filtros activos una vez lleguen los datos reales, el motor de
// reconciliación de facets YA existente (reconcileExperienceFacets, MISMO
// mecanismo que Client Partner/Proyecto/Cliente) lo corrige — Año participa
// del chequeo de opciones válidas (mismo criterio que Año de Inicio de
// Proyecto), Mes NUNCA se poda (predicate lo excluye del chequeo, ver
// FACET_CONFIG.cartera — el <select> de Mes siempre ofrece los 12 meses
// fijos). Proyecto: Año de Inicio SOLO si ese año existe de verdad en el
// dataset — si el mock no lo representa, cae a "Todos" ("") en vez de
// mostrar un filtro que garantiza 0 filas.
function carteraDefaultFilters(persp) {
  if (persp === "Mantenimiento") {
    const hoy = new Date();
    return { anio: String(hoy.getFullYear()), mes: String(hoy.getMonth() + 1) };
  }
  return {};
}
// Año de Inicio de Proyecto (owner feedback, ver HANDOFF): el slicer real
// de "Analisis Proyectos" (Proyectos.Año de Inicio) permite elegir MÁS de
// un año a la vez — un <select> de valor único no lo representa. Mismo
// patrón que Estado (checkbox-group con estado propio, `state.
// carteraAnioInicio`, FUERA del motor de facets de valor único) pero con
// opciones DINÁMICAS (años realmente presentes en el dataset, no un enum
// fijo de 2 valores como Estado). Default: año calendario actual tildado
// si está representado, si no ningún año tildado (== todo oculto hasta que
// el usuario elija, nunca una combinación imposible mostrada como si
// tuviera datos).
// Facets <select> de Cartera/Proyecto (cp/proyecto/cliente) — lista estable
// usada también para computar las opciones de Año de Inicio de forma faceteada.
// No se referencia FACET_CONFIG acá para no crear un ciclo de inicialización
// con `state`.
const _CARTERA_SELECT_FIELDS = [{ key: "cp", field: "cp" }, { key: "proyecto", field: "proyecto" }, { key: "cliente", field: "cliente" }];
function carteraAnioInicioOptions() {
  // Facet MULTIDIRECCIONAL (corrective): años disponibles = distinct(anioInicio)
  // sobre las filas que pasan TODOS los otros filtros activos (Estado +
  // CP/Proyecto/Cliente), excluyendo SOLO el propio Año de Inicio — nunca un año
  // imposible bajo el Estado/filtros vigentes. Reusa el mismo `facetRowsExcept`.
  const statusRows = CARTERA_PROYECTO_ROWS.filter(r => state.carteraStatus[r.status]);
  const rows = facetRowsExcept(statusRows, state.filters.cartera || {}, _CARTERA_SELECT_FIELDS, "");
  return distinctValues(rows, "anioInicio");
}
function carteraDefaultAnioInicio() {
  const anioActual = String(new Date().getFullYear());
  const options = carteraAnioInicioOptions();
  return new Set(options.includes(anioActual) ? [anioActual] : []);
}
// Prune de selección obsoleta (corrective): al cambiar Estado u otro facet,
// quita de la selección los años que ya no son opción válida; conserva los
// válidos; si no queda ninguno, respeta el comportamiento facet-empty existente
// (selección vacía ⇒ sin filas, igual que un checkbox-group sin tildar). Nunca
// deja un año oculto seleccionado ni auto-selecciona otro.
function pruneCarteraAnioInicio() {
  const available = new Set(carteraAnioInicioOptions());
  state.carteraAnioInicio = new Set([...state.carteraAnioInicio].filter(y => available.has(y)));
}
// Desde/Hasta compartido (owner feedback, ver HANDOFF: Rentabilidad/
// Participación/Horas Vendidas también necesitan un rango de fecha por
// defecto) — MISMO default que Cartera/Mantenimiento ya usa (primer día
// del año calendario en curso → hoy), un solo generador en vez de 3
// literales repetidos por experiencia.
function defaultYearToDateFilters() {
  const hoy = new Date();
  const desde = new Date(hoy.getFullYear(), 0, 1);
  return { desde: isoDateLocal(desde), hasta: isoDateLocal(hoy) };
}

// Rentabilidad / Performance (ver corrective — HANDOFF/task): a diferencia
// de Horas Vendidas/Calendario, "Analisis Rentabilidad" y "Hs Certificadas
// vs Hs Ejecutadas" NO son la misma pregunta de negocio reordenada — son DOS
// preguntas relacionadas del mismo dominio de Performance, unidas por la
// MISMA fórmula real (confirmada en TMDL, `Rentabilidad Unpivot.tmdl` +
// `Comparativo.tmdl`):
//   Rentabilidad horas == Margen Absoluto == Horas Certificadas − Horas Trabajadas
//   Margen Relativo (medida DAX `Comparativo[MR]`) == Margen Absoluto / Horas Certificadas
// "Hs Ejecutadas" (nombre de la 2da página) y "Horas Trabajadas" (nombre en
// la 1ra) son el MISMO dato — el propio PBIP anota el lineage: Certificadas
// viene de SharePoint, Ejecutadas/Trabajadas viene de Time (Time Logs
// Reports). Signo preservado tal cual sale de la fórmula — NUNCA se
// etiqueta "bueno"/"malo" (la métrica no lo determina de forma
// inequívoca), solo color/signo, mismo criterio que `diffCell()` ya usa en
// Cartera. Proyectos reusados 1:1 de Cartera/Proyectos (mismos
// certificadas/trabajadas → mismo Diff Hs ahí y acá, consistencia
// cross-experiencia).
// `cliente`/`cp`/`fecha` (owner feedback, ver HANDOFF: filtros de
// Cliente/Client Partner/Desde-Hasta faltantes en Rentabilidad) — MISMA
// asignación Cliente→CP ya usada en Cartera/Comercial/Horas Vendidas.
// `fecha` para los 4 proyectos de Mantenimiento reusa 1:1 la `fecha` real
// de Cartera/Mantenimiento (mismo proyecto, mismo dato); los 3 de Proyecto
// no tienen `fecha` en Cartera/Proyecto (solo `anioInicio`, ver ese
// dataset) — se asigna una fecha representativa dentro de ese Año de
// Inicio real.
const RENTABILIDAD_RESUMEN = { vendidas: 969, certificadas: 940, trabajadas: 985, noAsignadas: 62 };
const RENTABILIDAD_PROYECTOS = [
  { tipo: "Mantenimiento", cliente: "Andesmar", cp: "María López", proyecto: "Andesmar - Mantenimiento Costeo Directo", certificadas: 128, trabajadas: 134, fecha: "2026-08-01" },
  { tipo: "Mantenimiento", cliente: "Andesmar", cp: "María López", proyecto: "Andesmar - Soporte Infraestructura", certificadas: 43, trabajadas: 40, fecha: "2026-08-01" },
  { tipo: "Mantenimiento", cliente: "Danone", cp: "María López", proyecto: "Danone - Soporte Analítica", certificadas: 96, trabajadas: 92, fecha: "2026-07-01" },
  { tipo: "Mantenimiento", cliente: "Molinos", cp: "María López", proyecto: "Molinos - Mantenimiento BI", certificadas: 60, trabajadas: 60, fecha: "2026-06-01" },
  { tipo: "Proyecto", cliente: "YPF", cp: "Diego Ramos", proyecto: "YPF - Implementación SADI v2", certificadas: 322, trabajadas: 340, fecha: "2026-01-15" },
  { tipo: "Proyecto", cliente: "AySA", cp: "Diego Ramos", proyecto: "AySA - Costeo Federal", certificadas: 212, trabajadas: 210, fecha: "2025-03-10" },
  { tipo: "Proyecto", cliente: "Ternium", cp: "Diego Ramos", proyecto: "Ternium - Rollout Reporting", certificadas: 166, trabajadas: 175, fecha: "2026-02-20" },
];

// Matriz mensual por Concepto (ver corrective — HANDOFF: "Cartera +
// Rentabilidad Completeness", B2) — pivotTable PRINCIPAL real de "Analisis
// Rentabilidad" (visual d006eb329e24e5e3950b, page 27b8d56f76c40db0321d):
// Rows: Concepto — Columns: MesNombre — Values: Sum(Valor). Orden de
// Concepto es el real (`Rentabilidad Unpivot.tmdl`, columna `Orden`,
// `sortByColumn Orden`): 1 Horas Vendidas, 2 Horas Certificadas, 3 Horas
// Trabajadas, 4 Rentabilidad horas, 5 No asignadas a servicios — NUNCA
// alfabético. MesNombre ordena por `NumMes` (cronológico real, Ene…Dic),
// reusa el MISMO `MESES` que Horas Vendidas/Calendario.
//
// MENSUAL, no acumulado: el M-query de `Comparativo` agrupa
// (`GROUPBY`/`SUMX(CURRENTGROUP())`) por `[Mes]` específico — cada celda es
// el valor DE ESE mes, no una suma corrida desde enero (verificado en
// Comparativo.tmdl, partition source).
//
// Horas Certificadas/Horas Trabajadas/Rentabilidad horas SÍ responden a los
// filtros Tipo Proyecto/Proyecto de esta experiencia (se derivan de
// `rentabilidadFilteredProyectos()`, ver B5 — mismo contexto de filtro que
// el ranking). Horas Vendidas/No asignadas a servicios quedan a nivel
// portfolio FIJO — mismo criterio YA establecido para los 4 KPIs de
// Resumen (ninguno de los dos existe como campo por-proyecto en
// RENTABILIDAD_PROYECTOS; inventar una distribución por proyecto no
// verificada sería peor que ser explícito sobre el límite). `flatMonthly`
// reparte el total anual aprobado en partes iguales — simplificación de V0
// documentada (no hay granularidad mensual real disponible sin datos
// vivos); la relación SUMA-doce-meses-= total-anual sí es exacta.
function flatMonthly(total) {
  const base = Math.floor(total / 12);
  const remainder = total - base * 12;
  return Array.from({ length: 12 }, (_, i) => base + (i < remainder ? 1 : 0));
}

// Máscara de meses activos por el rango Desde/Hasta (Q2, owner live QA:
// Rentabilidad seguía mostrando Sep-Oct-Nov-Dic con Hasta=agosto — misma
// clase de bug en Horas Vendidas). Regla canónica: TODO visual/sección de
// UNA experiencia deriva de la MISMA población filtrada activa — si el
// grano de la fuente es mensual, el filtro de fecha se aplica a grano
// MENSUAL (nunca día: Desde/Hasta redondean al mes calendario que
// contienen). Los headers Ene-Dic se PRESERVAN siempre (geometría estable,
// nunca se ocultan columnas) — los meses fuera de rango quedan en blanco/0
// vía `maskMonthly`. Sin filtro activo (Desde/Hasta vacíos) ⇒ los 12 meses
// incluidos, comportamiento "Todos" sin cambio. Las matrices de este
// prototipo NO tienen eje de Año (12 columnas Ene-Dic fijas, mismo
// criterio en toda la app) — si Desde/Hasta cruza más de un año
// calendario, se incluye la UNIÓN de meses tocados en cualquiera de esos
// años (nunca se inventa un eje de Año que la matriz no tiene).
function monthsInRange(desde, hasta) {
  if (!desde && !hasta) return Array(12).fill(true);
  const startYear = desde ? parseInt(desde.slice(0, 4), 10) : parseInt(hasta.slice(0, 4), 10);
  const endYear = hasta ? parseInt(hasta.slice(0, 4), 10) : parseInt(desde.slice(0, 4), 10);
  const startMonth = desde ? parseInt(desde.slice(5, 7), 10) : 1;
  const endMonth = hasta ? parseInt(hasta.slice(5, 7), 10) : 12;
  const included = Array(12).fill(false);
  for (let y = startYear; y <= endYear; y++) {
    const mFrom = y === startYear ? startMonth : 1;
    const mTo = y === endYear ? endMonth : 12;
    for (let m = mFrom; m <= mTo; m++) included[m - 1] = true;
  }
  return included;
}

// Aplica la máscara de `monthsInRange` a un array mensual[12] — meses
// fuera de rango a 0 (nunca se recorta el array, el header Ene-Dic sigue
// intacto). `mask` null/undefined ⇒ passthrough (sin filtro de fecha en
// esa experiencia, ver Calendario/Capacidad — mismo comportamiento de
// siempre, sin cambio).
function maskMonthly(monthly, mask) {
  return mask ? monthly.map((v, i) => (mask[i] ? v : 0)) : monthly;
}

const RENTABILIDAD_VENDIDAS_MENSUAL = flatMonthly(RENTABILIDAD_RESUMEN.vendidas);
const RENTABILIDAD_NOASIGNADAS_MENSUAL = flatMonthly(RENTABILIDAD_RESUMEN.noAsignadas);

// Horas Vendidas (ver corrective — HANDOFF/task): campos reales del PBIP son
// "Hs vendidas Mant" / "Hs vendidas Proy" (Horas por proyectos) separadas por
// Tipo, más Client Partner/Cliente/Proyecto/Mes — NUNCA "consumidas"/
// "restantes" (eso pertenece a otra experiencia, no se reubica acá). Cada fila
// es un Proyecto (nivel más fino) con 12 valores mensuales de horas vendidas;
// los niveles de agrupación (Client Partner/Tipo/Cliente) se agregan sumando
// filas — nunca se hardcodea un total de grupo por separado.
const MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
// Nombres completos (UX1, owner live QA — Cartera/Mantenimiento vuelve a
// un selector Año+Mes en vez de Desde/Hasta): el <select> de Mes SIEMPRE
// ofrece los 12 meses fijos, nunca data-driven (ver carteraMesField) — a
// diferencia de MESES (abreviado, solo para headers de columna de matriz).
const MESES_LARGOS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
// `fecha` (owner feedback, ver HANDOFF: Desde/Hasta faltante en Horas
// Vendidas) — dentro del año calendario en curso para las 6 filas, mismo
// criterio que Participación/Rentabilidad.
const HORASVENDIDAS_ROWS = [
  { cp: "María López", tipo: "Mantenimiento", cliente: "Andesmar", proyecto: "Mantenimiento Costeo Directo", monthly: [12, 12, 12, 12, 12, 12, 10, 10, 10, 10, 10, 10], fecha: "2026-08-01" },
  { cp: "María López", tipo: "Mantenimiento", cliente: "Danone", proyecto: "Soporte Analítica", monthly: [8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8], fecha: "2026-07-01" },
  { cp: "María López", tipo: "Proyecto", cliente: "Molinos", proyecto: "Rollout BI", monthly: [0, 0, 15, 15, 15, 15, 15, 0, 0, 0, 0, 0], fecha: "2026-03-01" },
  { cp: "Diego Ramos", tipo: "Mantenimiento", cliente: "AySA", proyecto: "Mantenimiento Costeo Federal", monthly: [18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18], fecha: "2026-05-01" },
  { cp: "Diego Ramos", tipo: "Proyecto", cliente: "YPF", proyecto: "Implementación SADI v2", monthly: [30, 30, 30, 30, 30, 30, 30, 30, 30, 0, 0, 0], fecha: "2026-01-15" },
  { cp: "Diego Ramos", tipo: "Proyecto", cliente: "Ternium", proyecto: "Rollout Reporting", monthly: [0, 0, 0, 20, 20, 20, 20, 20, 20, 20, 20, 20], fecha: "2026-04-01" },
];

// Comercial / Clientes (ver corrective — HANDOFF: "Commercial / Clients
// source-aligned experience"). Las invenciones de la primera versión de
// este V0 — condición ("T&M mensual"/"Bolsa de horas"/"Fijo por hito"),
// participación % LIBRE (no derivada de horas), status ("Vigente"/"En
// renegociación") y "última revisión" — NO tienen soporte en el PBIP
// fuente y se retiraron por completo. Fuente real (2 páginas confirmadas
// vía artifacts/reporting-integral-v0/page_inventory.json +
// PBIR/TMDL — ver HANDOFF, NUNCA la misma pregunta de negocio reordenada,
// dos experiencias fuente distintas dentro de UNA sola experiencia
// Reporting, igual criterio que Rentabilidad/Capacidad):
//
//   "Analisis Participacion - Condiciones Comerciales -" → pivotTable
//   principal Rows: ID Frecuencia, Permite Adelanto, Condición de Pago,
//   Cliente part (4 niveles, EXACTO ese orden) — Values: `% Hs Vendidas 2`/
//   `% Hs Certificadas 2` (displayName real "% Participación Hs Vendidas"/
//   "% Participación Hs Certificadas"). Las otras 2 pivotTables de esa
//   página (rollup por ID Frecuencia solo, rollup por Condición de Pago
//   solo) son subconjuntos de ESTA MISMA jerarquía — expandir/contraer los
//   2 niveles superiores del árbol ya reproduce esas vistas, no se
//   duplican como paneles aparte.
//
//   "Client Partner - Status de Cliente detalle" → pivotTable Rows: Client
//   Partner, Cliente (2 niveles) — Values: Salud Financiera/Relación/
//   Operativo/Continuidad/Estrategico + Salud (displayName "Salud Final").
//   Son columnas de TEXTO categóricas (Cliente.tmdl: dataType string, sin
//   measure) — NUNCA el "Vigente/En renegociación" inventado. Dominio real
//   no verificable sin datos vivos (columna SharePoint) — Verde/Amarillo/
//   Rojo es la convención estándar de semáforo de salud de cliente, mock
//   representativo (ver HANDOFF).
//
// Ninguna de las 2 páginas fuente tiene visual tipo card/KPI
// (page_inventory.json: visual_types = {actionButton,image,pivotTable,
// slicer,textbox} en ambas) — NUNCA se inventan tiles KPI acá.

// % Participación Hs Vendidas/Certificadas (medidas reales `Horas por
// proyectos.[% Hs Vendidas 2]`/`[% Hs Certificadas 2]`, TMDL):
//   DIVIDE(SUM(HsVendidas), CALCULATE(SUM(HsVendidas),
//     REMOVEFILTERS(Client Partner, ID Frecuencia, Permite Adelanto,
//                    Condicion de Pago, Cliente part)))
// SIEMPRE contra el gran total FIJO (remueve exactamente las columnas que
// son las dimensiones de este árbol) — mismo criterio que `% Incidencia`
// de Capacidad/Productivas, nunca contra el subtotal del grupo padre.
// horasVendidas/horasCertificadas reusan 1:1 los valores reales ya
// establecidos en CARTERA_CONFIG para el mismo cliente (mismo dato,
// consistencia cross-experiencia — ver comentario de Rentabilidad).
// `fecha` (owner feedback, ver HANDOFF: Desde/Hasta faltante en
// Participación) — dentro del año calendario en curso para las 6 filas,
// mismo criterio que el resto del dataset mock (todo visible bajo el
// default Desde=1/ene→Hasta=hoy).
const COMERCIAL_PARTICIPACION_ROWS = [
  { idFrecuencia: "1 Mensual", permiteAdelanto: "Si", condicionPago: "30", cliente: "Andesmar", cp: "María López", horasVendidas: 140, horasCertificadas: 128, fecha: "2026-08-01" },
  { idFrecuencia: "3 Trimestral", permiteAdelanto: "No", condicionPago: "60", cliente: "Danone", cp: "María López", horasVendidas: 88, horasCertificadas: 96, fecha: "2026-07-01" },
  { idFrecuencia: "1 Mensual", permiteAdelanto: "No", condicionPago: "30", cliente: "Molinos", cp: "María López", horasVendidas: 60, horasCertificadas: 60, fecha: "2026-06-01" },
  { idFrecuencia: "6 Anual", permiteAdelanto: "No", condicionPago: "30", cliente: "AySA", cp: "Diego Ramos", horasVendidas: 208, horasCertificadas: 212, fecha: "2026-05-01" },
  { idFrecuencia: "1 Mensual", permiteAdelanto: "Si", condicionPago: "15", cliente: "YPF", cp: "Diego Ramos", horasVendidas: 360, horasCertificadas: 322, fecha: "2026-04-01" },
  { idFrecuencia: "5 Semestral", permiteAdelanto: "Si", condicionPago: "30", cliente: "Ternium", cp: "Diego Ramos", horasVendidas: 180, horasCertificadas: 166, fecha: "2026-03-01" },
];

// Salud de Cliente (ver corrective) — 5 sub-dimensiones + Salud Final,
// columnas categóricas puras (sin rollup: un grupo Client Partner no tiene
// un "promedio" de semáforos, mismo criterio que Status/Facturadas en
// Cartera — categórico no se agrega).
const COMERCIAL_SALUD_ROWS = [
  { cp: "María López", cliente: "Andesmar", saludFinanciera: "Verde", saludRelacion: "Verde", saludOperativo: "Amarillo", saludContinuidad: "Verde", saludEstrategico: "Verde", saludFinal: "Verde" },
  { cp: "María López", cliente: "Danone", saludFinanciera: "Amarillo", saludRelacion: "Verde", saludOperativo: "Verde", saludContinuidad: "Amarillo", saludEstrategico: "Verde", saludFinal: "Amarillo" },
  { cp: "María López", cliente: "Molinos", saludFinanciera: "Verde", saludRelacion: "Amarillo", saludOperativo: "Verde", saludContinuidad: "Verde", saludEstrategico: "Amarillo", saludFinal: "Amarillo" },
  { cp: "Diego Ramos", cliente: "AySA", saludFinanciera: "Verde", saludRelacion: "Verde", saludOperativo: "Verde", saludContinuidad: "Verde", saludEstrategico: "Verde", saludFinal: "Verde" },
  { cp: "Diego Ramos", cliente: "YPF", saludFinanciera: "Rojo", saludRelacion: "Amarillo", saludOperativo: "Amarillo", saludContinuidad: "Rojo", saludEstrategico: "Amarillo", saludFinal: "Rojo" },
  { cp: "Diego Ramos", cliente: "Ternium", saludFinanciera: "Amarillo", saludRelacion: "Verde", saludOperativo: "Verde", saludContinuidad: "Verde", saludEstrategico: "Verde", saludFinal: "Verde" },
];

function statusPillSalud(value) {
  const color = value === "Verde" ? "green" : (value === "Rojo" ? "red" : "amber");
  return `<span class="dashboard-status-badge dashboard-status-badge--${color}">${esc(value)}</span>`;
}

// Gran total FIJO (denominador REMOVEFILTERS) — FUNCIÓN, no `const`
// precalculada (corrective Batch A, ver HANDOFF): `COMERCIAL_PARTICIPACION_ROWS`
// se reemplaza IN PLACE con datos reales (`replaceRows`, mismo patrón que
// Cartera) — una constante calculada una sola vez al cargar el script
// habría quedado STALE contra el mock para siempre, rompiendo el % real.
// Recalculada en cada llamada contra el array ACTUAL (mock o real, según
// el ciclo de vida vigente) — sigue siendo "fijo" en el sentido correcto:
// fijo por RENDER contra el dataset completo sin filtrar, nunca contra el
// subtotal del grupo filtrado (REMOVEFILTERS).
function comercialParticipacionTotalVendidas() { return COMERCIAL_PARTICIPACION_ROWS.reduce((s, r) => s + r.horasVendidas, 0); }
function comercialParticipacionTotalCertificadas() { return COMERCIAL_PARTICIPACION_ROWS.reduce((s, r) => s + r.horasCertificadas, 0); }
function comercialPctVendidas(horas) {
  const total = comercialParticipacionTotalVendidas();
  return (total ? (horas / total * 100) : 0).toFixed(2) + " %";
}
function comercialPctCertificadas(horas) {
  const total = comercialParticipacionTotalCertificadas();
  return (total ? (horas / total * 100) : 0).toFixed(2) + " %";
}
// Rollup jerárquico (ver Hierarchical Measure Rollup Backbone corrective):
// Clase B, denominador FIJO (REMOVEFILTERS) — `sumField` re-suma el
// numerador crudo del subárbol y lo pasa por la MISMA función que ya
// calcula el % de una fila hoja contra el total fijo, nunca promedia/suma
// el % ya renderizado. Un padre con 100% de participación en TODOS sus
// hijos seguiría sumando < 100% si no representa el dataset entero —
// nunca se fuerza a 100%.
const COMERCIAL_PARTICIPACION_COLUMNS = [
  { label: "% Participación Hs Vendidas", numeric: true, render: r => comercialPctVendidas(r.horasVendidas), rollup: sumField("horasVendidas", comercialPctVendidas) },
  { label: "% Participación Hs Certificadas", numeric: true, render: r => comercialPctCertificadas(r.horasCertificadas), rollup: sumField("horasCertificadas", comercialPctCertificadas) },
];
const COMERCIAL_SALUD_COLUMNS = [
  { label: "Financiera", numeric: false, render: r => statusPillSalud(r.saludFinanciera) },
  { label: "Relación", numeric: false, render: r => statusPillSalud(r.saludRelacion) },
  { label: "Operativo", numeric: false, render: r => statusPillSalud(r.saludOperativo) },
  { label: "Continuidad", numeric: false, render: r => statusPillSalud(r.saludContinuidad) },
  { label: "Estratégico", numeric: false, render: r => statusPillSalud(r.saludEstrategico) },
  { label: "Salud Final", numeric: false, render: r => statusPillSalud(r.saludFinal) },
];

// Calendario (ver corrective — consolida "Calendario x Frecuencia" +
// "Calendario x CP" del PBIP fuente en UNA experiencia). Metadata real
// (artifacts/reporting-integral-v0/page_inventory.json + query.queryState
// del pivotTable de cada página): AMBAS páginas usan el MISMO pivotTable de
// 3 dimensiones (ID Frecuencia / Cliente / Client Partner) sobre la MISMA
// tabla `Calendario_Cliente` (12 meses + Total) — la única diferencia real
// es el ORDEN de esas 3 dimensiones en las filas:
//   Calendario x Frecuencia → Rows: ID Frecuencia, Cliente, Client Partner
//   Calendario x CP         → Rows: Client Partner, Cliente, ID Frecuencia (+ columna Total)
// Mismo dataset base, dos reordenamientos de UNA jerarquía — exactamente el
// patrón dual-perspectiva ya usado en Horas Vendidas (reusa buildTree, pero
// NO `renderTreeRows` sin más — ver `renderCalendarioCell` abajo).
//
// Valores BOOLEANOS (owner feedback, ver HANDOFF): cada celda mensual de
// `Calendario_Cliente` es un indicador de presencia (1 = tiene entrega/
// checkpoint programado ese mes, 0 = no) — NUNCA una cantidad. Semanal
// significa "todos los meses tiene algo programado" (1 en los 12 meses),
// no "4 veces por mes" — la cantidad de eventos DENTRO del mes no es lo
// que esta tabla representa. Se renderiza como tilde (✓) cuando 1, blanco
// cuando 0 — ver `renderCalendarioCell`. La columna Total (perspectiva
// Client Partner) SÍ sigue siendo una cuenta real (meses con checkpoint en
// el año), no booleana — un total de "12" es información genuina.
const CALENDARIO_ROWS = [
  { frecuencia: "Semanal", cliente: "Andesmar", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Semanal", cliente: "AySA", cp: "Diego Ramos", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Mensual", cliente: "Danone", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Mensual", cliente: "Molinos", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Trimestral", cliente: "YPF", cp: "Diego Ramos", monthly: [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1] },
  { frecuencia: "Trimestral", cliente: "Ternium", cp: "Diego Ramos", monthly: [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0] },
];
// Orden real de negocio (ID Frecuencia, ver TMDL de Cliente[ID Frecuencia]:
// SWITCH 1 Mensual…6 Anual) — cadencia creciente, nunca alfabético.
const FRECUENCIA_ORDER = ["Mensual", "Semanal", "Trimestral", "Cuatrimestral", "Semestral", "Anual"];
const CALENDARIO_HIERARCHY = {
  "Frecuencia": { keys: ["frecuencia", "cliente", "cp"], labels: ["ID Frecuencia", "Cliente", "Client Partner"] },
  "Client Partner": { keys: ["cp", "cliente", "frecuencia"], labels: ["Client Partner", "Cliente", "ID Frecuencia"] },
};

// Capacidad / Ejecución (ver corrective — HANDOFF/task): "Horas Productivas
// consumidas" y "Hs No Asignadas" son DOS preguntas de negocio distintas
// (una por Cliente/Proyecto, otra por Consultor) confirmadas por PBIP —
// NUNCA la misma tabla reordenada, y NUNCA una redefinición de disponibilidad
// de Staffing (ver nota de límite más abajo).
//
// Productivas: pivotTable real Rows=Cliente+Part>Project, Values=Rank/
// Horas/% Incidencia. `Horas` = Sum(Time Logs Reports.HORAS); `% Incidencia`
// = medida real `Time Logs Reports.[% Hs ejecutadas]` = Horas de esa fila ÷
// Horas TOTALES (REMOVEFILTERS de Cliente/Client Partner — SIEMPRE contra el
// gran total, no contra el subtotal del Cliente, INCLUSO si el filtro de
// Cliente del V0 está activo: el denominador NUNCA se recalcula por
// filtro, solo el numerador/las filas visibles cambian). `Rank` = RANKX por
// Horas descendente — representado acá por el ORDEN de las filas (Cliente
// ordenado de mayor a menor Horas), no como columna literal (evita una
// columna redundante con el orden visual).
const CAPACIDAD_PRODUCTIVAS_ROWS = [
  { cliente: "Andesmar", proyecto: "Mantenimiento Costeo Directo", horas: 134 },
  { cliente: "Andesmar", proyecto: "Soporte Infraestructura", horas: 40 },
  { cliente: "Danone", proyecto: "Soporte Analítica", horas: 92 },
  { cliente: "YPF", proyecto: "Implementación SADI v2", horas: 340 },
  { cliente: "AySA", proyecto: "Costeo Federal", horas: 210 },
  { cliente: "Ternium", proyecto: "Rollout Reporting", horas: 175 },
];

// No Asignadas: pivotTable real Columns=Mes, Rows="Consultor + Hs asig"
// (columna calculada = Consultor & " (" & [HS asignadas a proy] & " hs
// Asig)", ver Colaboradores.tmdl — "Hs Asig" es la capacidad DE REFERENCIA
// fija del consultor, NO la métrica mensual), Values=HORAS filtrado a
// Task="01. No Asignación a Servicio en Cliente" — horas NO PRODUCTIVAS que
// el propio colaborador registra en Time. `hsAsig` (referencia estática) y
// `monthly` (horas no asignadas REALES por mes) son dimensiones separadas a
// propósito — nunca se confunden.
const CAPACIDAD_NOASIGNADAS_ROWS = [
  { consultor: "Federico Bressi", hsAsig: 160, monthly: [8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8] },
  { consultor: "Franco Delucchi", hsAsig: 160, monthly: [0, 0, 4, 4, 0, 0, 4, 0, 0, 4, 0, 0] },
  { consultor: "Gabriela Mendez", hsAsig: 160, monthly: [12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12] },
  { consultor: "Martina Suárez", hsAsig: 120, monthly: [0, 0, 0, 6, 6, 6, 0, 0, 0, 0, 0, 0] },
];

// --- "Actualizar" — mantenimiento de origen SharePoint (ver corrective —
// HANDOFF: "8 — Source Maintenance / 'Actualizar' Action") -----------------
// Reporting es de SOLO LECTURA — "Actualizar" nunca escribe a SharePoint/
// Graph, es un atajo de navegación (target=_blank) hacia la lista fuente
// para que el usuario la edite AHÍ (SharePoint sigue siendo la autoridad de
// permisos de edición, ver HANDOFF: "no se pide un permiso nuevo, no se
// implementa edición dentro del Hub"). Estructura de site/list/view EXACTA
// tal como la proveyó el owner — Calendario/Comercial son estáticas;
// Cartera es la ÚNICA con sustitución dinámica (ver carteraUpdateLink), y
// solo `FilterValue1` varía — viewid y el resto de los parámetros nunca se
// tocan.
const CALENDARIO_COMERCIAL_UPDATE_URL = "https://danwor.sharepoint.com/sites/Gestion/Lists/Empresas/AllItems.aspx?viewid=84cf3861%2D2a7f%2D4d78%2D9946%2D473208c66e0e";
// Año dinámico de Cartera (owner feedback, ver HANDOFF): Mantenimiento ya no
// tiene un selector de Año discreto (reemplazado por Desde/Hasta, ver
// corrective "Cartera + Rentabilidad Completeness") — se toma el año de
// "Hasta" (string YYYY-MM-DD, se lee por substring y NUNCA vía `new
// Date(...).getFullYear()` — mismo motivo que isoDateLocal: parsear un
// string de fecha como Date corre el riesgo de UTC shift de día/año cerca
// de fin de año en husos horarios negativos). "Hasta" SIEMPRE tiene un
// valor (ver carteraDefaultFilters — nunca "Todos" para un rango de fecha),
// currentYear es solo el fallback defensivo. Proyecto ahora permite VARIOS
// años tildados (ver carteraDefaultAnioInicio) — se toma el más reciente de
// los tildados como representativo (mismo criterio que "current year" del
// resto del contrato: el año más relevante/actual disponible), currentYear
// si no hay ninguno tildado.
function carteraUpdateLink() {
  const persp = state.perspective.cartera;
  const filters = state.filters.cartera || {};
  const currentYear = new Date().getFullYear();
  let year = currentYear;
  if (persp === "Mantenimiento") {
    // UX1 (owner live QA): Mantenimiento reemplazó Desde/Hasta por Año/Mes
    // — el año seleccionado se lee directo de `filters.anio` (ya no hay
    // `filters.hasta` del cual derivarlo).
    year = filters.anio ? parseInt(filters.anio, 10) : currentYear;
  } else {
    const selected = [...state.carteraAnioInicio].map(Number);
    if (selected.length) year = Math.max(...selected);
  }
  return `https://danwor.sharepoint.com/sites/Gestion/Lists/Horas%20por%20Proyectos/Carga%20Horas%20vendidas%20CP.aspx?FilterField1=A%5Fx00f1%5Fo&FilterValue1=${year}&FilterType1=Text&viewid=04b28abf%2D3524%2D4242%2Da55f%2D24441197050c`;
}

// ============================================================================
// Experiencias — cada una define su render + (opcional) perspectivas
// ============================================================================
// Labels de tab renombrados (owner feedback, ver HANDOFF) — SOLO texto
// visible; `id` interno (usado en FACET_CONFIG/state.perspective/
// dashboardKey/stateKey en todo el archivo) queda igual a propósito, evita
// un refactor de identificadores sin beneficio de negocio.
// `updateLink` (opcional, ver "Actualizar" arriba): función () => url. Si
// una experiencia no la define, el shell de filtros simplemente no
// renderiza el botón — UNA sola propiedad de config decide, nunca un
// condicional por nombre de experiencia en el renderer (ver filterBarShell).
// Rentabilidad/Horas Vendidas/Horas Productivas quedan sin `updateLink` a
// propósito: ningún target de mantenimiento fue provisto para ellas (ver
// HANDOFF) — no se inventa una URL.
const EXPERIENCES = [
  { id: "cartera", label: "Cartera / Proyectos", perspectives: ["Mantenimiento", "Proyecto"], updateLink: carteraUpdateLink },
  { id: "rentabilidad", label: "Rentabilidad", perspectives: ["Resumen", "Certificadas vs Ejecutadas"] },
  { id: "horasvendidas", label: "Horas Vendidas", perspectives: ["Proyecto", "Client Partner"] },
  { id: "comercial", label: "Participación / Comercial", perspectives: ["Participación", "Salud de Cliente"], updateLink: () => CALENDARIO_COMERCIAL_UPDATE_URL },
  { id: "calendario", label: "Calendario", perspectives: ["Frecuencia", "Client Partner"], updateLink: () => CALENDARIO_COMERCIAL_UPDATE_URL },
  { id: "capacidad", label: "Horas Productivas", perspectives: ["Productivas", "No Asignadas"] },
];

const state = {
  experience: "cartera",
  perspective: { cartera: "Mantenimiento", rentabilidad: "Resumen", horasvendidas: "Proyecto",
                 comercial: "Participación", calendario: "Frecuencia", capacidad: "Productivas" },
  // Filtros seleccionables por experiencia (Align with Hub backbone
  // corrective, ver HANDOFF) — motor ÚNICO compartido por las 5 experiencias
  // filtrables (ver getFilter/setFilter/selectField más abajo), keyeado
  // "<experienceId>.<dimensión>". Comercial queda afuera a propósito (no se
  // revisita su diseño de negocio en este batch, ver HANDOFF).
  // Cartera arranca con Año/Mes en el valor calendario actual (ver
  // corrective — HANDOFF: "Cartera + Rentabilidad Completeness", A1) — las
  // otras 5 experiencias arrancan sin filtro (Todos).
  // Rentabilidad/Horas Vendidas/Comercial arrancan con Desde/Hasta = año
  // calendario en curso a la fecha (owner feedback, ver HANDOFF) — MISMO
  // default que Cartera/Mantenimiento ya usaba, generado por
  // `defaultYearToDateFilters()` en vez de repetido a mano. Calendario/
  // Capacidad quedan sin filtro de fecha (Todos) — ningún corrective pidió
  // uno ahí.
  filters: { cartera: carteraDefaultFilters("Mantenimiento"), rentabilidad: defaultYearToDateFilters(),
             horasvendidas: defaultYearToDateFilters(), calendario: {}, capacidad: {}, comercial: defaultYearToDateFilters() },
  // Status de Cartera/Proyectos — vocabulario real "En curso"/"Finalizado"
  // (Proyectos.Status). Checkbox-group propio (no el motor `filters`
  // genérico de arriba) porque su semántica default NO es "todo tildado":
  // arranca en "En curso" únicamente (lo operativamente relevante al
  // entrar) — "Finalizado" queda disponible para tildar, nunca prendido de
  // entrada.
  carteraStatus: { "En curso": true, "Finalizado": false },
  // Año de Inicio de Cartera/Proyecto — checkbox-group multi-select propio
  // (ver carteraDefaultAnioInicio arriba), independiente de `filters` por
  // el mismo motivo que carteraStatus: default NO es "todo tildado".
  // Inicializado tras el literal (ver abajo) — carteraDefaultAnioInicio() lee
  // `state`, que todavía no existe durante la evaluación de este objeto.
  carteraAnioInicio: new Set(),
  // Ciclo de vida de la carga de datos REALES de Cartera — UN status POR
  // PERSPECTIVA (P5, owner live QA de performance: Mantenimiento NUNCA
  // debe esperar el fetch+parse de la XLSX de Facturadas que solo Proyecto
  // usa, ver loadCarteraMantenimiento/loadCarteraProyecto). idle → loading
  // → ready | error, mismo criterio que el resto del Hub.
  carteraMantenimientoStatus: "idle",
  carteraProyectoStatus: "idle",
  // Rentabilidad (AMBAS perspectivas) — Batch B, ver loadRentabilidadData.
  // Fetch/status propio (Certificadas/Vendidas del backbone de Cartera +
  // Trabajadas/Ejecutadas del adapter de Time Logs, combinados en el
  // service — no comparte ciclo de vida con Batch A/B de las otras
  // experiencias).
  rentabilidadDataStatus: "idle",
  // Backbone reuse de Cartera (Horas Vendidas/Participación/Calendario/
  // Salud de Cliente, ver HANDOFF): MISMO ciclo de vida, UN solo status
  // para las 4 (comparten UN fetch/endpoint consolidado — ver
  // loadBatchAData). Horas Productivas (Productivas Y No Asignadas) usa su
  // PROPIO status (`batchBDataStatus`) — fuente CSV de Time Logs distinta
  // (Correction 1, ver HANDOFF/real-data-lineage.md §8bis).
  batchADataStatus: "idle",
  // Horas Productivas (AMBAS perspectivas) — Batch B, CSV de Time Logs
  // Reports vía `loadBatchBData`. MISMO ciclo de vida idle→loading→
  // ready|error que Batch A, endpoint/fetch propio (combinar CSV es costo
  // real, no se comparte con el backbone de Cartera).
  batchBDataStatus: "idle",
};
// Año de Inicio default — se resuelve DESPUÉS del literal porque
// carteraDefaultAnioInicio() lee `state` (carteraStatus/filters).
state.carteraAnioInicio = carteraDefaultAnioInicio();

function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

// --- Formateo numérico semántico compartido (Phase 0C) ----------------------
// UN solo Intl.NumberFormat por tipo (locale en-US, la MISMA convención de
// miles/decimales que el resto del Hub — ver Staffing). Elimina el ruido de
// punto flotante (52111.100000000006) y unifica la presentación en KPIs, tablas
// y tooltips. Tipos semánticos: count (entero), hours (hasta 1 decimal),
// signed (horas con signo +/−), percent (1 decimal + "%"). NUNCA cambia el valor
// ni el cálculo — solo la presentación.
const _NF = {
  count: new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }),
  hours: new Intl.NumberFormat("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 1 }),
  percent: new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
};
function fmtNum(value, type = "hours") {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!isFinite(n)) return "—";
  if (type === "count") return _NF.count.format(Math.round(n));
  if (type === "percent") return _NF.percent.format(n) + "%";
  if (type === "signed") return (n > 0 ? "+" : "") + _NF.hours.format(n);
  return _NF.hours.format(n);  // "hours" (default)
}

// --- Primitivo KPI compartido del Hub (CRITICAL HUB STANDARDIZATION
// CORRECTIVE, owner live QA — Horas Productivas/Resumen/TOTAL HORAS
// mostraba "184613.24999999997" crudo: la clase `.activity-kpi-grid`/
// `.activity-kpi` YA era compartida, pero cada renderer seguía decidiendo
// a mano si pasaba el valor por `fmtNum` antes de interpolarlo — un
// bypass silencioso posible en CUALQUIER tile nuevo). Este constructor es
// la ÚNICA función que arma un tile KPI en todo el archivo — la ÚNICA
// ocurrencia de `class="activity-kpi__value"` en el source debe vivir
// ACÁ (ver test de costura estructural,
// test_reporting_kpi_primitive.py). Un dominio declara SOLO label/value/
// format(/accent); numérico (miles/decimales/signo/null/0), markup y
// tipografía son responsabilidad EXCLUSIVA del primitivo — nunca más se
// decide de nuevo en cada sitio de llamada.
//
// `value` es SIEMPRE el número semántico CRUDO — NUNCA HTML ya formateado
// (`diffCell`) ni un string con separador de miles ya aplicado (mismo
// error que dejó en blanco el KPI Diff Hs de Cartera, ver corrective
// S.3: pasar HTML por un formateador que espera un número da NaN → "—").
// `format` = mismos tipos semánticos que `fmtNum` ("count"/"hours"/
// "signed"/"percent", default "hours"). "signed" además colorea el valor
// (verde/rojo) reusando `.diff-pos`/`.diff-neg` de reporting.css — MISMA
// convención que `diffCell` en celdas de tabla, sin duplicar la paleta.
// `accent` (opcional): clase adicional en el CARD, mismo patrón que
// `.activity-kpi--warning` de Asignaciones — ningún KPI de Reporting la
// necesita hoy, queda disponible para el próximo caso real.
function renderDashboardKpi({ label, value, format = "hours", accent }) {
  const display = fmtNum(value, format);
  let valueClass = "activity-kpi__value";
  if (format === "signed") {
    const n = typeof value === "number" ? value : Number(value);
    if (isFinite(n) && n > 0) valueClass += " diff-pos";
    else if (isFinite(n) && n < 0) valueClass += " diff-neg";
  }
  const cardClass = accent ? `activity-kpi activity-kpi--${accent}` : "activity-kpi";
  return `<div class="${cardClass}"><span class="${valueClass}">${display}</span><span class="activity-kpi__label">${esc(label)}</span></div>`;
}

// Loader Hub COMPARTIDO (Phase 0A) — reusa el MISMO primitivo visual que
// Asignaciones (`section-analysis-loading` + spinner + copy), nunca una
// imitación propia de Reporting. Copy contextual por experiencia.
function reportingLoadingHtml(label) {
  return `<p class="section-analysis-loading"><span class="section-analysis-spinner"></span>`
    + `<span class="section-analysis-loading-copy">${esc(label)}</span></p>`;
}
function reportingSourceErrorHtml(label) {
  return `<p class="empty-panel">No pudimos cargar ${esc(label)} desde SharePoint en este momento. `
    + `Las demás vistas de Reporting siguen disponibles.</p>`;
}

function diffCell(diff) {
  const cls = diff < 0 ? "diff-neg" : (diff > 0 ? "diff-pos" : "");
  return `<span class="${cls}">${fmtNum(diff, "signed")}</span>`;
}
// Versión texto plano de diffCell — para atributos `title` (tooltip
// nativo), que no pueden contener HTML (ver corrective — HANDOFF: "Cartera
// + Rentabilidad Completeness", B1/B4).
function diffPlain(diff) {
  return fmtNum(diff, "signed");
}

// --- Motor de filtros compartido (Align with Hub backbone corrective) ------
// UN solo mecanismo de estado/lectura/escritura para los filtros <select> de
// las 5 experiencias filtrables — nunca seis engines de filtro separados.
// "Todos" == "" (sin filtrar). Los checkbox-group (Status de Cartera) tienen
// su propio estado dedicado (`state.carteraStatus`) por la razón de arriba,
// pero se renderizan con el MISMO helper visual `checkboxGroupField`.
// `compareFn` opcional (ver corrective — HANDOFF: "Cartera + Rentabilidad
// Completeness", A2 — "ordered date/month facets"): default alfabético
// ("es"); un facet como Mes necesita orden de NEGOCIO (Ene…Dic), nunca
// alfabético ("Agosto" < "Enero" alfabéticamente sería incorrecto acá).
function distinctValues(rows, key, compareFn) {
  const values = [...new Set(rows.map(r => r[key]))]
    .filter(v => v !== undefined && v !== null && v !== "");
  return values.sort(compareFn || ((a, b) => String(a).localeCompare(String(b), "es")));
}

// --- Faceted/cascading filter helper (ver corrective — HANDOFF) -----------
// Regla canónica: availableOptions(F) = valores distintos de BASE DATA
// después de aplicar todos los filtros ACTIVOS salvo F mismo. `fields` es
// la config declarativa por experiencia: [{ key, field, compareFn? }] —
// `key` es la clave en `state.filters[expId]`, `field` es la propiedad
// real de la fila, `compareFn` opcional fuerza un orden de negocio en vez
// del alfabético default (ver Mes en Cartera/Mantenimiento). `baseRows` ya
// viene escopeada por lo que NO es un facet <select> (perspectiva,
// checkboxes de Status) — el motor de acá solo resuelve la
// interdependencia ENTRE los <select>, nunca decide perspectiva/Status.
// `predicate` opcional (owner feedback — ver HANDOFF: filtro de rango
// Desde/Hasta en Cartera): en vez de exact-match (`r[field] === val`), un
// facet de RANGO declara `predicate(rowValue, filterValue) => boolean` —
// mismo motor de faceting, sin lógica de fecha aparte.
function facetRowsExcept(baseRows, filters, fields, exceptKey) {
  return fields.reduce((rows, f) => {
    if (f.key === exceptKey) return rows;
    const val = filters[f.key];
    if (!val) return rows;
    return f.predicate ? rows.filter(r => f.predicate(r[f.field], val)) : rows.filter(r => r[f.field] === val);
  }, baseRows);
}
function facetOptions(baseRows, filters, fields, key) {
  const f = fields.find(f => f.key === key);
  return distinctValues(facetRowsExcept(baseRows, filters, fields, key), f.field, f.compareFn);
}
// Invalid-selection reset (ver corrective): si un valor ya seleccionado deja
// de ser una opción válida (recalculada EXCLUYÉNDOLO a él mismo, igual que
// facetOptions), cae a "Todos" — nunca se retiene una combinación imposible
// ni se auto-selecciona otro valor. `justChangedKey` (opcional) NUNCA se
// resetea a sí mismo: es la elección explícita que el usuario acaba de
// hacer, y es la que debe ganar cuando entra en conflicto con una
// selección VIEJA de otro facet — ver "CURRENT CARTERA EXAMPLE" del
// corrective (Proyecto recién elegido debe sobrevivir; Cliente, si ya no
// es compatible, es el que cae a Todos — nunca al revés). Sin
// `justChangedKey` (ver toggleCarteraStatus: Status no es un facet
// <select>, no hay "el que se acaba de tocar" entre proyecto/cliente) los
// facets se evalúan simétricamente. Itera a punto fijo (bounded por
// fields.length) porque resetear un facet puede a su vez habilitar/
// deshabilitar otro (ver HANDOFF: "recompute once again if necessary").
function reconcileFacetFilters(expId, baseRows, fields, justChangedKey) {
  const filters = state.filters[expId] || (state.filters[expId] = {});
  for (let i = 0; i < fields.length + 1; i++) {
    let changed = false;
    fields.forEach(f => {
      // Un facet de RANGO (`predicate`, ver Desde/Hasta de Cartera) nunca
      // participa de este chequeo — no tiene una lista discreta de
      // "opciones válidas" contra la cual comparar (cualquier fecha del
      // calendario es un valor legítimo), así que "reconciliarlo" contra
      // facetOptions lo resetearía a Todos aunque el usuario haya elegido
      // una fecha perfectamente razonable.
      if (f.key === justChangedKey || f.predicate) return;
      const val = filters[f.key];
      if (!val) return;
      if (!facetOptions(baseRows, filters, fields, f.key).includes(val)) { filters[f.key] = ""; changed = true; }
    });
    if (!changed) break;
  }
}
// Registro config-driven por experiencia (ver "IMPORTANT MAINTAINABILITY
// INVARIANT" del corrective): una experiencia nueva agrega UNA entrada acá
// — `baseRows`/`fields` — y obtiene opciones faceteadas + reset de
// selección inválida gratis, sin código Proyecto→Cliente a medida. Función
// (no objeto estático) porque `baseRows`/`fields` pueden depender de la
// perspectiva/Status vigentes (ver Capacidad, Cartera). Comercial queda
// afuera a propósito (sin filtros, fuera de alcance, ver HANDOFF).
const FACET_CONFIG = {
  cartera: () => {
    const persp = state.perspective.cartera;
    const cfg = CARTERA_CONFIG[persp];
    // Año de Inicio (Proyecto, ver toggleCarteraAnioInicio) se aplica ACÁ
    // igual que Status — checkbox multi-select FUERA del motor de facets de
    // valor único, pre-filtra `baseRows` antes de que Client Partner/
    // Proyecto/Cliente faceteen sobre lo que queda.
    const baseRows = cfg.rows.filter(r => state.carteraStatus[r.status])
      .filter(r => persp !== "Proyecto" || state.carteraAnioInicio.has(r.anioInicio));
    // Mantenimiento (UX1, owner live QA — vuelve de Desde/Hasta a Año+Mes,
    // grano MENSUAL real de la fuente): Año es un facet <select> normal
    // (SIN predicate) — participa de la reconciliación de opciones válidas
    // igual que Client Partner/Proyecto/Cliente (canonical:
    // availableOptions(Año) = filas por todos los demás filtros activos,
    // excluyendo Año). Mes usa `predicate` (igualdad exacta) para quedar
    // FUERA de esa reconciliación — el <select> de Mes SIEMPRE ofrece los
    // 12 meses fijos (ver carteraMesField), nunca se poda por falta de
    // datos ese mes. Proyecto NO agrega un facet de Año de Inicio acá (se
    // convirtió en checkbox multi-select, ver arriba — ya no es un
    // <select> de valor único).
    const dateFields = persp === "Mantenimiento"
      ? [
          { key: "anio", field: "anio" },
          { key: "mes", field: "mes", predicate: (mes, selected) => mes === selected },
        ]
      : [];
    // Client Partner (owner feedback, ver HANDOFF: filtro faltante en
    // Cartera) — PRIMERO en el orden, mismo criterio que Rentabilidad/
    // Comercial/Horas Vendidas de acá en más.
    return {
      baseRows,
      fields: [{ key: "cp", field: "cp" }, { key: "proyecto", field: "proyecto" }, { key: "cliente", field: "cliente" }, ...dateFields],
    };
  },
  rentabilidad: () => ({
    baseRows: RENTABILIDAD_PROYECTOS,
    fields: [
      { key: "cp", field: "cp" }, { key: "cliente", field: "cliente" },
      { key: "tipo", field: "tipo" }, { key: "proyecto", field: "proyecto" },
      { key: "desde", field: "fecha", predicate: (fecha, desde) => fecha >= desde },
      { key: "hasta", field: "fecha", predicate: (fecha, hasta) => fecha <= hasta },
    ],
  }),
  horasvendidas: () => ({
    baseRows: HORASVENDIDAS_ROWS,
    fields: [
      { key: "cp", field: "cp" }, { key: "tipo", field: "tipo" }, { key: "cliente", field: "cliente" }, { key: "proyecto", field: "proyecto" },
      { key: "desde", field: "fecha", predicate: (fecha, desde) => fecha >= desde },
      { key: "hasta", field: "fecha", predicate: (fecha, hasta) => fecha <= hasta },
    ],
  }),
  calendario: () => ({
    baseRows: CALENDARIO_ROWS,
    // Frecuencia agregado (owner feedback, ver HANDOFF) — orden real de
    // negocio (FRECUENCIA_ORDER), nunca alfabético.
    fields: [
      { key: "cp", field: "cp" }, { key: "cliente", field: "cliente" },
      { key: "frecuencia", field: "frecuencia", compareFn: (a, b) => FRECUENCIA_ORDER.indexOf(a) - FRECUENCIA_ORDER.indexOf(b) },
    ],
  }),
  capacidad: () => state.perspective.capacidad === "Productivas"
    ? { baseRows: CAPACIDAD_PRODUCTIVAS_ROWS, fields: [{ key: "cliente", field: "cliente" }] }
    : { baseRows: CAPACIDAD_NOASIGNADAS_ROWS, fields: [{ key: "consultor", field: "consultor" }] },
  // Client Partner/Cliente son los ÚNICOS slicers reales de ambas páginas
  // fuente (page_inventory.json: slicer_fields) representables en el mock
  // actual — Año (Participación) y el slicer de Status de
  // "Empresas con proyectos activos" (Salud) no tienen una dimensión
  // limpia en este dataset todavía, se ocultan (ver corrective: "si un
  // filtro fuente no se puede representar con fidelidad, ocultarlo").
  // Desde/Hasta (owner feedback, ver HANDOFF) SOLO en Participación —
  // Salud de Cliente no tiene una dimensión de fecha propia (columnas
  // categóricas puras, ver comentario del dataset), no se inventa una.
  comercial: () => state.perspective.comercial === "Participación"
    ? { baseRows: COMERCIAL_PARTICIPACION_ROWS, fields: [
        { key: "cp", field: "cp" }, { key: "cliente", field: "cliente" },
        { key: "desde", field: "fecha", predicate: (fecha, desde) => fecha >= desde },
        { key: "hasta", field: "fecha", predicate: (fecha, hasta) => fecha <= hasta },
      ] }
    : { baseRows: COMERCIAL_SALUD_ROWS, fields: [{ key: "cp", field: "cp" }, { key: "cliente", field: "cliente" }] },
};
function reconcileExperienceFacets(expId, justChangedKey) {
  const cfg = FACET_CONFIG[expId];
  if (!cfg) return;
  const { baseRows, fields } = cfg();
  reconcileFacetFilters(expId, baseRows, fields, justChangedKey);
  // Año de Inicio (Cartera/Proyecto) es multi-select fuera del motor <select>;
  // se reconcilia su selección obsoleta acá con el MISMO criterio faceteado.
  if (expId === "cartera") pruneCarteraAnioInicio();
}
// Select faceteado — reemplaza `selectField(expId, key, label,
// distinctValues(dataset, dim))` en las 5 experiencias filtrables: las
// opciones YA vienen excluyendo el propio facet (ver facetOptions), nunca
// el dataset completo sin escopear.
function facetSelectField(expId, key, label) {
  const cfg = FACET_CONFIG[expId];
  if (!cfg) return "";
  const { baseRows, fields } = cfg();
  return selectField(expId, key, label, facetOptions(baseRows, state.filters[expId] || {}, fields, key));
}
// Filas resultantes con TODOS los facets activos aplicados — misma fuente
// de verdad que `facetSelectField` (mismo `baseRows`/`fields` por
// experiencia), para que la tabla/KPI y las opciones de los <select> nunca
// diverjan.
function facetFilteredRows(expId) {
  const cfg = FACET_CONFIG[expId];
  if (!cfg) return [];
  const { baseRows, fields } = cfg();
  return facetRowsExcept(baseRows, state.filters[expId] || {}, fields, null);
}

function getFilter(expId, key) { return (state.filters[expId] && state.filters[expId][key]) || ""; }
function setFilter(expId, key, value) {
  if (!state.filters[expId]) state.filters[expId] = {};
  state.filters[expId][key] = value;
  // `key` es el facet que el usuario ELIGIÓ recién — nunca se resetea a sí
  // mismo (ver reconcileFacetFilters); si entra en conflicto con una
  // selección vieja de otro facet, la vieja es la que cae a Todos.
  reconcileExperienceFacets(expId, key);
  render();
}
// Experiencias con default de fecha SENSATO (año en curso a la fecha) en
// vez de blanco — mismo criterio ya establecido para Cartera/Estado, ahora
// extendido (owner feedback, ver HANDOFF) a las 3 experiencias que también
// arrancan con Desde/Hasta.
const YEAR_TO_DATE_EXPERIENCES = new Set(["rentabilidad", "horasvendidas", "comercial"]);
function resetFilters(expId) {
  if (expId === "cartera") {
    // "Limpiar" vuelve al default SENSATO (año/mes actual, Estado = En
    // curso, Año de Inicio = año en curso si está representado), no a
    // blanco — mismo criterio ya establecido para Estado.
    state.filters.cartera = carteraDefaultFilters(state.perspective.cartera);
    state.carteraStatus = { "En curso": true, "Finalizado": false };
    state.carteraAnioInicio = carteraDefaultAnioInicio();
  } else if (YEAR_TO_DATE_EXPERIENCES.has(expId)) {
    state.filters[expId] = defaultYearToDateFilters();
  } else {
    state.filters[expId] = {};
  }
  render();
}
// Año de Inicio (Cartera/Proyecto, ver carteraDefaultAnioInicio) — toggle
// multi-select, MISMO criterio de reconciliación que Status (participa del
// mismo contexto de faceting que Proyecto/Cliente/Client Partner).
function toggleCarteraAnioInicio(year) {
  if (state.carteraAnioInicio.has(year)) state.carteraAnioInicio.delete(year);
  else state.carteraAnioInicio.add(year);
  reconcileExperienceFacets("cartera");
  render();
}
function selectField(expId, key, label, options) {
  const current = getFilter(expId, key);
  return `<label class="whatsnew-filter">
    <span>${esc(label)}</span>
    <select onchange="setFilter('${expId}','${key}', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${options.map(o => `<option value="${esc(o)}" ${current === o ? "selected" : ""}>${esc(o)}</option>`).join("")}
    </select>
  </label>`;
}
// Mes de Cartera/Mantenimiento (UX1, owner live QA): SIEMPRE los 12 meses
// fijos (a diferencia del resto de los facets <select>, que son
// data-driven vía `facetSelectField` — un mes sin datos bajo el Año/Estado
// activo sigue siendo una opción VÁLIDA, ver FACET_CONFIG.cartera). MISMA
// marca `.whatsnew-filter`/`<select>` que `selectField`, pero con
// value(1-12) != label(nombre completo) — el único facet del Hub con esa
// necesidad (el resto son enums donde value==label), por eso no reusa
// `selectField` tal cual.
function carteraMesField() {
  const current = getFilter("cartera", "mes");
  return `<label class="whatsnew-filter">
    <span>Mes</span>
    <select onchange="setFilter('cartera','mes', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${MESES_LARGOS.map((nombre, i) => `<option value="${i + 1}" ${current === String(i + 1) ? "selected" : ""}>${esc(nombre)}</option>`).join("")}
    </select>
  </label>`;
}
// Filtro de fecha nativo — MISMO `<input type="date">` + `.whatsnew-filter`
// que ya usa el dashboard de Actividad (Desde/Hasta, ver
// admin_activity_content.html), CERO CSS nuevo (`.whatsnew-filter
// input[type="date"]` ya existe en tools.css). No pasa por `facetSelectField`
// (no hay una lista de opciones discretas que ofrecer — es un date picker
// nativo, no un `<select>`); el FILTRADO real sí corre por el mismo motor
// de facets vía `predicate` (ver facetRowsExcept).
function dateField(expId, key, label) {
  const current = getFilter(expId, key);
  return `<label class="whatsnew-filter">
    <span>${esc(label)}</span>
    <input type="date" value="${esc(current)}" onchange="setFilter('${expId}','${key}', this.value)">
  </label>`;
}
// items: [{ value, checked, onclick }] — MISMA marca visual que el Estado/
// Concepto de Staffing (.whatsnew-filter--compact + .dashboard-checkbox-group,
// ver staffing_dashboard.html), reusada tal cual.
function checkboxGroupField(label, items) {
  return `<label class="whatsnew-filter whatsnew-filter--compact">
    <span>${esc(label)}</span>
    <div class="dashboard-checkbox-group">
      ${items.map(it => `<label class="dashboard-checkbox-option">
        <input type="checkbox" ${it.checked ? "checked" : ""} onchange="${it.onclick}" aria-label="${esc(label)} ${esc(it.value)}">
        <span>${esc(it.value)}</span>
      </label>`).join("")}
    </div>
  </label>`;
}
// `.dashboard-sticky-header`/`.staffing-filter-bar`/`.staffing-filter-bar__clear`/
// `.whatsnew-filters`/`.whatsnew-filter__clear` — MISMAS clases que Staffing
// (staffing_dashboard.html: `class="whatsnew-filter__clear
// staffing-filter-bar__clear"`), CERO CSS nuevo en tools.css (prioridad 1 de
// la regla de reuso, ver HANDOFF). `whatsnew-filter__clear` es la que
// aporta el look (dorado, hover subrayado) — `staffing-filter-bar__clear`
// SOLO reposiciona (margin-left:auto); usar una sin la otra deja el botón
// sin el tratamiento gold del Hub (Hub Visual Consistency corrective, ver
// HANDOFF: ese fue exactamente el bug de este control).
// "Actualizar" (ver "Actualizar" arriba de EXPERIENCES) — SIEMPRE
// inmediatamente a la derecha de "✕ Limpiar", nunca dentro de un header de
// sección analítica (pertenece al contexto de la EXPERIENCIA/fuente de
// datos, no a un resultado puntual). `.dashboard-action-link` (tools.css) es
// el MISMO lenguaje visual que `.support-link` del pipeline de contenido —
// ver comentario de esa clase para por qué no se reusa la clase original
// tal cual. `target="_blank" rel="noopener noreferrer"`: atajo de
// navegación de solo lectura, Reporting nunca escribe a SharePoint.
function filterBarShell(fieldsHtml, expId) {
  const exp = EXPERIENCES.find(e => e.id === expId);
  const updateHtml = exp && exp.updateLink
    ? `<a class="dashboard-action-link" href="${esc(exp.updateLink())}" target="_blank" rel="noopener noreferrer">Actualizar<span>↗</span></a>`
    : "";
  // Limpiar+Actualizar viven DENTRO de un wrapper propio (`.staffing-
  // filter-bar__actions`, tools.css) — owner feedback: con flex-wrap en
  // `.staffing-filter-bar` y `margin-left:auto` en Limpiar suelto, en
  // viewports angostos Actualizar podía envolver a una línea DISTINTA que
  // Limpiar (cada uno como ítem de flex independiente). Agrupados como UNA
  // sola unidad, envuelven juntos o no envuelven, nunca separados.
  return `<div class="dashboard-sticky-header">
    <div class="staffing-filter-bar">
      <div class="whatsnew-filters">${fieldsHtml}</div>
      <div class="staffing-filter-bar__actions">
        <button type="button" class="whatsnew-filter__clear staffing-filter-bar__clear" onclick="resetFilters('${expId}')">✕ Limpiar</button>
        ${updateHtml}
      </div>
    </div>
  </div>`;
}

function toggleCarteraStatus(key) {
  state.carteraStatus[key] = !state.carteraStatus[key];
  // Status participa del mismo contexto de faceting que Proyecto/Cliente
  // (ver corrective) — semántica del checkbox intacta, solo dispara la
  // misma reconciliación de selección inválida que un <select> normal.
  reconcileExperienceFacets("cartera");
  render();
}

// Filtros reales por experiencia — SOLO dimensiones que genuinamente existen
// en el dataset de esa experiencia/subvista (ver corrective: "si un filtro
// es irrelevante, ocultarlo" — nunca un control decorativo). Empresa/Año/Mes
// no aparecen acá porque ningún dataset mock los modela como campo real.
// Schema por perspectiva: Mantenimiento agrega Desde/Hasta (rango de fecha,
// reemplaza el combo Año+Mes original — owner feedback, ver HANDOFF);
// Proyecto agrega Año de Inicio (esa página fuente no tiene un campo de
// fecha/mes en absoluto, ver page_inventory.json). Orden: Proyecto,
// Cliente, Desde/Hasta (o Año de Inicio), Estado.
function renderCarteraFilterBar() {
  const persp = state.perspective.cartera;
  // Sin barra de filtros hasta que los datos reales de ESTA perspectiva
  // estén listos — evita mostrar opciones de un dataset que todavía no
  // llegó (o que falló).
  if (carteraStatusFor(persp) !== "ready") return "";
  // Mantenimiento (UX1, owner live QA): Año (facet <select> normal,
  // data-driven) + Mes (fijo, 12 meses siempre — ver carteraMesField).
  // Año de Inicio (Proyecto, owner feedback ver HANDOFF): el slicer real
  // permite VARIOS años tildados a la vez — checkbox-group DINÁMICO (años
  // realmente presentes en el dataset), no un <select> de valor único.
  const dateFields = persp === "Mantenimiento"
    ? facetSelectField("cartera", "anio", "Año") + carteraMesField()
    : checkboxGroupField("Año de Inicio", carteraAnioInicioOptions().map(a =>
        ({ value: a, checked: state.carteraAnioInicio.has(a), onclick: `toggleCarteraAnioInicio('${a}')` })));
  const fields = facetSelectField("cartera", "cp", "Client Partner")
    + facetSelectField("cartera", "proyecto", "Proyecto")
    + facetSelectField("cartera", "cliente", "Cliente")
    + dateFields
    + checkboxGroupField("Estado", [
        { value: "En curso", checked: state.carteraStatus["En curso"], onclick: "toggleCarteraStatus('En curso')" },
        { value: "Finalizado", checked: state.carteraStatus["Finalizado"], onclick: "toggleCarteraStatus('Finalizado')" },
      ]);
  return filterBarShell(fields, "cartera");
}

function renderRentabilidadFilterBar() {
  const fields = facetSelectField("rentabilidad", "cp", "Client Partner")
    + facetSelectField("rentabilidad", "cliente", "Cliente")
    + facetSelectField("rentabilidad", "tipo", "Tipo Proyecto")
    + facetSelectField("rentabilidad", "proyecto", "Proyecto")
    + dateField("rentabilidad", "desde", "Desde") + dateField("rentabilidad", "hasta", "Hasta");
  return filterBarShell(fields, "rentabilidad");
}

function renderHorasVendidasFilterBar() {
  const fields = facetSelectField("horasvendidas", "cp", "Client Partner")
    + facetSelectField("horasvendidas", "tipo", "Tipo Proyecto")
    + facetSelectField("horasvendidas", "cliente", "Cliente")
    + facetSelectField("horasvendidas", "proyecto", "Proyecto")
    + dateField("horasvendidas", "desde", "Desde") + dateField("horasvendidas", "hasta", "Hasta");
  return filterBarShell(fields, "horasvendidas");
}

// Client Partner primero (owner feedback — "alinearlos en todas las
// tabs"): antes Cliente/Client Partner, ahora MISMO orden que Rentabilidad/
// Comercial/Horas Vendidas/Cartera.
function renderCalendarioFilterBar() {
  const fields = facetSelectField("calendario", "cp", "Client Partner")
    + facetSelectField("calendario", "cliente", "Cliente")
    + facetSelectField("calendario", "frecuencia", "Frecuencia");
  return filterBarShell(fields, "calendario");
}

// Filtros por subvista — genuinamente distintos por fuente real (ver
// HANDOFF): Productivas usa el slicer real de Cliente de "Horas Productivas
// consumidas"; No Asignadas usa el slicer real de Consultor de "Hs No
// Asignadas" — Client Partner/Año/Mes/Task/Semana/Estado NO son campos del
// dataset mock de ninguna de las dos subvistas, así que se ocultan (nunca se
// copian mecánicamente los slicers de Power BI solo para que luzcan iguales).
function renderCapacidadFilterBar() {
  const persp = state.perspective.capacidad;
  const fields = persp === "Productivas"
    ? facetSelectField("capacidad", "cliente", "Cliente")
    : facetSelectField("capacidad", "consultor", "Consultor");
  return filterBarShell(fields, "capacidad");
}

// Desde/Hasta (owner feedback, ver HANDOFF) SOLO en Participación — Salud
// de Cliente no tiene `fecha` en el dataset (columnas categóricas puras,
// ver comentario de COMERCIAL_SALUD_ROWS), no se inventa una.
function renderComercialFilterBar() {
  const persp = state.perspective.comercial;
  const dateFields = persp === "Participación"
    ? dateField("comercial", "desde", "Desde") + dateField("comercial", "hasta", "Hasta")
    : "";
  const fields = facetSelectField("comercial", "cp", "Client Partner")
    + facetSelectField("comercial", "cliente", "Cliente")
    + dateFields;
  return filterBarShell(fields, "comercial");
}

// `withLabel` (Hub Visual Consistency corrective, ver HANDOFF): las 6
// experiencias montan el switch DENTRO del toolbar de la sección analítica
// a la que controla (ver sectionToolbar) — ahí el rótulo "Ver por" es
// redundante (el breadcrumb de la sección ya da contexto), así que se
// omite (`withLabel=false`). `withLabel=true` (default) queda disponible
// para el caso standalone, sin uso actual tras alinear Comercial (ver
// Commercial/Clients corrective).
// `.dashboard-view-switch` (Hub Dashboard Contextual View Control
// corrective, ver HANDOFF) — primitivo NEUTRAL en tools.css, compartido
// con el switch Consultor/Proyecto de Staffing (staffing_dashboard.html)
// — CERO CSS propio de Reporting acá, el viejo `.perspective-switch`
// scopeado bajo `.reporting-integral-preview` se retiró.
function perspectiveSwitch(expId, withLabel = true) {
  const exp = EXPERIENCES.find(e => e.id === expId);
  const buttons = exp.perspectives.map(p => `
    <button class="${state.perspective[expId] === p ? "is-active" : ""}" onclick="setPerspective('${expId}', '${esc(p)}')">${esc(p)}</button>
  `).join("");
  const label = withLabel ? `<p class="perspective-label">Ver por</p>` : "";
  return `${label}<div class="dashboard-view-switch">${buttons}</div>`;
}

function setPerspective(expId, p) {
  state.perspective[expId] = p;
  // Cada perspectiva puede tener dimensiones de filtro distintas (ver
  // Capacidad Productivas↔No Asignadas) — se resetea el filtro de la
  // experiencia al cambiar de perspectiva para no arrastrar un valor que ya
  // no aplica a la nueva vista. Cartera resetea al default sensato de la
  // NUEVA perspectiva (año/mes actual o año de inicio actual, ver
  // carteraDefaultFilters) en vez de blanco.
  if (expId === "cartera") state.filters.cartera = carteraDefaultFilters(p);
  // Comercial: Desde/Hasta solo tiene sentido en Participación (Salud no
  // tiene `fecha`, ver renderComercialFilterBar) — blanco en Salud, default
  // año-a-la-fecha al volver a Participación.
  else if (expId === "comercial") state.filters.comercial = p === "Participación" ? defaultYearToDateFilters() : {};
  else if (state.filters[expId]) state.filters[expId] = {};
  render();
}

function setExperience(id) {
  state.experience = id;
  render();
}

// --- Backbone de secciones draggable/colapsables (Align with Hub backbone
// corrective) — MISMA marca que Staffing/Actividad: <details
// class="dashboard-section"> dentro de [data-dashboard-key], wireado por
// app.js::initDashboardSections (drag-and-drop + persistencia de orden en
// sessionStorage) — CERO motor propio nuevo. `dashboardKey` es único por
// experiencia ("reporting-<id>") para que el orden guardado de una
// experiencia no choque con el de otra. -----------------------------------
function dashboardSection(id, title, countLabel, bodyHtml) {
  return `<details class="dashboard-section" data-section-id="${id}" open>
    <summary class="dashboard-section__head" draggable="true">
      <span class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>
      <span class="dashboard-section__caret"></span>
      <span class="dashboard-section__title">${esc(title)}</span>
      ${countLabel ? `<span class="dashboard-section__count">${esc(countLabel)}</span>` : ""}
    </summary>
    <div class="dashboard-section__body">${bodyHtml}</div>
  </details>`;
}
function dashboardSections(dashboardKey, sectionsHtml) {
  return `<div class="dashboard-sections" data-dashboard-key="${dashboardKey}">${sectionsHtml}</div>`;
}

// --- Experience renderers ---------------------------------------------------

// Carga LAZY de los datos REALES de Cartera desde el backend, POR
// PERSPECTIVA (P5, owner live QA de performance — ver
// loadCarteraMantenimiento/loadCarteraProyecto abajo). Mismo token
// delegado del browser que Staffing (sessionStorage "dw-graph-token").
// Popula EN EL LUGAR los arrays que CARTERA_CONFIG ya referencia (nunca
// reasigna) — el motor de facets/jerarquía/rollups no se entera de que las
// filas ahora son reales. Fail-soft: una falla deja esa perspectiva de
// Cartera en estado de error localizado; las otras experiencias siguen
// con sus mocks. NUNCA mezcla real + mock (el render está gateado por
// `carteraStatusFor(persp)`, así que el mock inicial nunca se muestra).
function replaceRows(arr, rows) {
  arr.length = 0;
  (rows || []).forEach(r => arr.push(r));
}
// MISMO patrón que `replaceRows`, para arrays mensuales[12] planos (no
// filas) — Rentabilidad/Vendidas y No asignadas a servicios (portfolio
// fijo), ver loadRentabilidadData.
function replaceMonthly(arr, values) {
  arr.length = 0;
  (values || []).forEach(v => arr.push(v));
}
// P5 (owner live QA de performance — Cartera ~17s): Mantenimiento y
// Proyecto tienen fetch/status INDEPENDIENTES — Mantenimiento NUNCA
// dispara la descarga/parseo de la XLSX de Facturadas (solo Proyecto la
// necesita, `facturadasProyecto` no se lee en `_mantenimiento_rows`, ver
// service). Mismo endpoint `/data/cartera`, parámetro `perspectiva` le
// dice al backend CUÁL fetch hacer (`ReportingService.
// get_cartera_mantenimiento`/`get_cartera_proyecto`) — si el usuario
// nunca abre Proyecto, la XLSX nunca se toca.
async function loadCarteraMantenimiento() {
  state.carteraMantenimientoStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/cartera", {
      method: "POST", body: new URLSearchParams({ graph_token: token, perspectiva: "mantenimiento" }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.cartera) {
      replaceRows(CARTERA_MANTENIMIENTO_ROWS, data.cartera.mantenimiento);
      state.carteraMantenimientoStatus = "ready";
    } else {
      state.carteraMantenimientoStatus = "error";
    }
  } catch (e) {
    state.carteraMantenimientoStatus = "error";
  }
  render();
}
async function loadCarteraProyecto() {
  state.carteraProyectoStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/cartera", {
      method: "POST", body: new URLSearchParams({ graph_token: token, perspectiva: "proyecto" }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.cartera) {
      replaceRows(CARTERA_PROYECTO_ROWS, data.cartera.proyecto);
      // Re-derivar el default data-dependiente (Año de Inicio de Proyecto) con
      // los años REALES — el default calculado al init salía del mock.
      state.carteraAnioInicio = carteraDefaultAnioInicio();
      state.carteraProyectoStatus = "ready";
    } else {
      state.carteraProyectoStatus = "error";
    }
  } catch (e) {
    state.carteraProyectoStatus = "error";
  }
  render();
}
function carteraStatusFor(persp) {
  return persp === "Mantenimiento" ? state.carteraMantenimientoStatus : state.carteraProyectoStatus;
}
function loadCarteraFor(persp) {
  if (persp === "Mantenimiento") loadCarteraMantenimiento(); else loadCarteraProyecto();
}
function renderCarteraDataState() {
  if (carteraStatusFor(state.perspective.cartera) === "error") {
    return reportingSourceErrorHtml("los datos de Cartera");
  }
  return reportingLoadingHtml("Cargando datos de Cartera…");
}

// Rentabilidad (AMBAS perspectivas, Resumen Y Certificadas vs Ejecutadas —
// Batch B, ver HANDOFF/real-data-lineage.md §10) — MISMO patrón que
// loadCarteraData: fetch propio (Certificadas/Vendidas vía backbone de
// Cartera + Trabajadas/Ejecutadas vía el MISMO adapter de Time Logs que
// Horas Productivas), `replaceRows`/`replaceMonthly` pueblan EN EL LUGAR
// `RENTABILIDAD_PROYECTOS` + los 2 arrays mensuales de portfolio (Vendidas/
// No asignadas), gateado por `rentabilidadDataStatus` propio.
async function loadRentabilidadData() {
  state.rentabilidadDataStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/rentabilidad", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.rentabilidad) {
      replaceRows(RENTABILIDAD_PROYECTOS, data.rentabilidad.proyectos);
      replaceMonthly(RENTABILIDAD_VENDIDAS_MENSUAL, data.rentabilidad.vendidasMonthly);
      replaceMonthly(RENTABILIDAD_NOASIGNADAS_MENSUAL, data.rentabilidad.noAsignadasMonthly);
      state.rentabilidadDataStatus = "ready";
    } else {
      state.rentabilidadDataStatus = "error";
    }
  } catch (e) {
    state.rentabilidadDataStatus = "error";
  }
  render();
}
function renderRentabilidadDataState() {
  if (state.rentabilidadDataStatus === "error") {
    return reportingSourceErrorHtml("los datos de Rentabilidad");
  }
  return reportingLoadingHtml("Cargando datos de Rentabilidad…");
}

// Backbone reuse de Cartera (Horas Vendidas/Participación/Calendario/Salud
// de Cliente, ver HANDOFF) — MISMO patrón que loadCarteraData:
// `replaceRows` puebla EN EL LUGAR los 4 arrays que ya referencian
// FACET_CONFIG/render*, UN solo fetch/endpoint consolidado (comparten
// origen — Horas por proyectos × Proyectos × Cliente), gateado por
// `batchADataStatus` (nunca muestra el mock inicial una vez que arrancó la
// carga real). Horas Productivas (AMBAS perspectivas) usa `loadBatchBData`
// — fuente CSV de Time Logs distinta (Correction 1, ver HANDOFF/
// real-data-lineage.md §8bis).
async function loadBatchAData() {
  state.batchADataStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/batch-a", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok") {
      replaceRows(HORASVENDIDAS_ROWS, data.horasVendidas);
      replaceRows(COMERCIAL_PARTICIPACION_ROWS, data.participacion);
      replaceRows(CALENDARIO_ROWS, data.calendario);
      replaceRows(COMERCIAL_SALUD_ROWS, data.salud);
      state.batchADataStatus = "ready";
    } else {
      state.batchADataStatus = "error";
    }
  } catch (e) {
    state.batchADataStatus = "error";
  }
  render();
}
function renderBatchADataState(label) {
  if (state.batchADataStatus === "error") {
    return reportingSourceErrorHtml(label);
  }
  return reportingLoadingHtml(`Cargando ${label}…`);
}

// Horas Productivas (AMBAS perspectivas, Productivas Y No Asignadas) —
// Batch B, CSV de Time Logs Reports (Correction 1 CERRADA, ver HANDOFF/
// real-data-lineage.md §8bis). MISMO patrón que loadBatchAData: UN
// fetch/endpoint consolidado (combinar TODOS los CSV es costo real, nunca
// 2 descargas separadas), `replaceRows` puebla EN EL LUGAR los 2 arrays,
// gateado por `batchBDataStatus` propio (independiente de Batch A —
// fuente física distinta).
async function loadBatchBData() {
  state.batchBDataStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/batch-b", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok") {
      replaceRows(CAPACIDAD_PRODUCTIVAS_ROWS, data.horasProductivas);
      replaceRows(CAPACIDAD_NOASIGNADAS_ROWS, data.horasProductivasNoAsignadas);
      state.batchBDataStatus = "ready";
    } else {
      state.batchBDataStatus = "error";
    }
  } catch (e) {
    state.batchBDataStatus = "error";
  }
  render();
}
function renderBatchBDataState(label) {
  if (state.batchBDataStatus === "error") {
    return reportingSourceErrorHtml(label);
  }
  return reportingLoadingHtml(`Cargando ${label}…`);
}

function renderCartera() {
  // Gate del ciclo de vida real-data — POR PERSPECTIVA (P5, ver
  // loadCarteraMantenimiento/loadCarteraProyecto): dispara SOLO el fetch de
  // la perspectiva activa (idle) y muestra loading/error hasta tener filas
  // reales; recién ahí corre el renderer/motor existente sin cambios.
  const persp = state.perspective.cartera;
  const status = carteraStatusFor(persp);
  if (status === "idle") loadCarteraFor(persp);
  if (status !== "ready") return renderCarteraDataState();
  const cfg = CARTERA_CONFIG[persp];
  // `facetFilteredRows` es la MISMA fuente de verdad que las opciones
  // faceteadas del filter bar (ver FACET_CONFIG.cartera) — tabla/KPI y
  // <select> nunca divergen (ver corrective).
  const rows = facetFilteredRows("cartera");
  const stateKey = "cartera::" + persp;
  const tree = buildTree(rows, cfg.keys);
  registerTreeContext(stateKey, rows, cfg.keys);

  const resumen = dashboardSection("resumen", "Resumen", "", `
    <div class="activity-kpi-grid">
      ${renderDashboardKpi({ label: cfg.kpiLabel, value: rows.length, format: "count" })}
      ${carteraKpiTiles(cfg, rows)}
    </div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("cartera", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr>
          <th class="row-label-col">${cfg.labels.join(" / ")}</th>
          ${cfg.columns.map(c => `<th class="${c.statusTerminal ? "dashboard-table-col--status" : (c.numeric ? "" : "left")}">${esc(c.label)}</th>`).join("")}
        </tr></thead>
        <tbody>${renderBusinessTreeRows(stateKey, tree, 0, cfg.columns)}</tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", persp + " — " + cfg.labels.join(" › "), rows.length + " fila(s)", detalleBody);

  return dashboardSections("reporting-cartera", resumen + detalle);
}

// Ranking de "Rentabilidad horas" (== Certificadas − Trabajadas) por
// proyecto — MISMA fórmula que Resumen y que Certificadas vs Ejecutadas,
// una sola función fuente de verdad. Opera sobre el subconjunto YA filtrado
// (ver rentabilidadFilteredProyectos) — nunca sobre el dataset completo.
function rentabilidadRanked(rows) {
  return rows
    .map(p => ({ ...p, rentabilidad: p.certificadas - p.trabajadas }))
    .sort((a, b) => b.rentabilidad - a.rentabilidad);
}

function rentabilidadFilteredProyectos() {
  return facetFilteredRows("rentabilidad");
}

// `title` nativo (ver corrective — HANDOFF: "Cartera + Rentabilidad
// Completeness", B1): el ancho de la barra es una representación RELATIVA
// (% del máximo del ranking) — el tooltip expone el proyecto, ese % relativo
// Y el valor numérico real de Rentabilidad horas detrás, sin librería de
// tooltips nueva (atributo `title` nativo, accesible).
function rentabilidadBarRow(label, value, maxAbs) {
  const cls = value < 0 ? "negativo" : "positivo";
  const width = maxAbs ? (Math.abs(value) / maxAbs * 100).toFixed(1) : 0;
  const tooltip = `${label} — Rentabilidad horas: ${diffPlain(value)} (${width}% del máximo del ranking)`;
  return `<div class="bar-row" title="${esc(tooltip)}"><span class="bar-label">${esc(label)}</span>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${width}%"></div></div>
    <span class="bar-value">${diffCell(value)}</span>
  </div>`;
}

// Matriz mensual por Concepto (ver corrective — HANDOFF: "Cartera +
// Rentabilidad Completeness", B2/B5) — MISMO `rows` YA filtrado que el
// ranking (`rentabilidadFilteredProyectos()`, nunca un segundo estado de
// filtro independiente). Tabla plana (Concepto es un solo nivel en la
// pivotTable real, sin jerarquía) — reusa `table.dashboard-table` por
// consistencia visual con las demás matrices mensuales, pero SIN el motor
// de árbol (sin toggle/caret/Expandir/Contraer): "si la fuente no tiene
// expansión jerárquica, no se inventa una".
function renderRentabilidadMensual(rows, monthMask) {
  // Batch B (ver HANDOFF/real-data-lineage.md §10): `certMonthly`/
  // `trabMonthly` por proyecto son AHORA grano mensual REAL (Horas por
  // proyectos/Time Logs Reports, ambos proyecto×mes en la fuente) — se
  // suman directo, NUNCA `flatMonthly` de un escalar (esa era la
  // simplificación de V0 sin datos vivos, ya retirada).
  const certMonthly = new Array(12).fill(0);
  const trabMonthly = new Array(12).fill(0);
  rows.forEach(p => {
    (p.certMonthly || []).forEach((v, i) => { certMonthly[i] += v; });
    (p.trabMonthly || []).forEach((v, i) => { trabMonthly[i] += v; });
  });
  const rentMonthly = certMonthly.map((v, i) => v - trabMonthly[i]);
  // Orden real (Rentabilidad Unpivot.tmdl, columna Orden 1→5) — Horas
  // Vendidas/No asignadas a servicios quedan a nivel portfolio fijo (ver
  // comentario del dataset), Certificadas/Trabajadas/Rentabilidad horas sí
  // responden a los filtros vigentes de esta experiencia. Q2 (owner live
  // QA): las 5 conceptos pasan por la MISMA `monthMask` de Desde/Hasta —
  // meses fuera del rango activo quedan en blanco en TODAS, incluidas las
  // 2 fijas de portfolio (antes solo el filtro de Tipo/Proyecto llegaba
  // acá, la fecha nunca se aplicaba a esta matriz).
  const conceptos = [
    { concepto: "Horas Vendidas", monthly: maskMonthly(RENTABILIDAD_VENDIDAS_MENSUAL, monthMask), signed: false },
    { concepto: "Horas Certificadas", monthly: maskMonthly(certMonthly, monthMask), signed: false },
    { concepto: "Horas Trabajadas", monthly: maskMonthly(trabMonthly, monthMask), signed: false },
    { concepto: "Rentabilidad horas", monthly: maskMonthly(rentMonthly, monthMask), signed: true },
    { concepto: "No asignadas a servicios", monthly: maskMonthly(RENTABILIDAD_NOASIGNADAS_MENSUAL, monthMask), signed: false },
  ];
  const body = `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr><th class="row-label-col">Concepto</th>${MESES.map(m => `<th>${m}</th>`).join("")}<th>Total</th></tr></thead>
        <tbody>
          ${conceptos.map(c => {
            const total = c.monthly.reduce((s, v) => s + v, 0);
            return `<tr class="depth-0">
              <td class="row-label"><span class="dashboard-table-indent" style="width:0px"></span>${esc(c.concepto)}</td>
              ${c.monthly.map(v => `<td>${c.signed ? diffCell(v) : (v ? fmtNum(v, "hours") : "—")}</td>`).join("")}
              <td>${c.signed ? diffCell(total) : fmtNum(total, "hours")}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>`;
  return dashboardSection("mensual", "Detalle mensual por Concepto", MESES[0] + " – " + MESES[11], body);
}

function renderRentabilidadResumen() {
  // Gate del ciclo de vida real-data (ver loadRentabilidadData) — MISMO
  // status que Certificadas vs Ejecutadas (comparten UN fetch, ver
  // HANDOFF/real-data-lineage.md §10).
  if (state.rentabilidadDataStatus === "idle") loadRentabilidadData();
  if (state.rentabilidadDataStatus !== "ready") return renderRentabilidadDataState();
  const rows = rentabilidadFilteredProyectos();
  // Horas Certificadas/Trabajadas/Rentabilidad horas (owner feedback: los
  // tiles quedaban estáticos al filtrar) — se recalculan de `rows` YA
  // filtradas (mismo criterio que la matriz mensual de abajo, ver
  // renderRentabilidadMensual), nunca del total fijo de portfolio.
  const certificadas = rows.reduce((s, p) => s + p.certificadas, 0);
  const trabajadas = rows.reduce((s, p) => s + p.trabajadas, 0);
  const rentabilidadHoras = certificadas - trabajadas;
  const ranked = rentabilidadRanked(rows);
  const positivos = ranked.filter(p => p.rentabilidad >= 0);
  const negativos = ranked.filter(p => p.rentabilidad < 0).slice().reverse(); // más negativo primero
  const maxAbs = Math.max(...ranked.map(p => Math.abs(p.rentabilidad)), 1);

  // Q2 (owner live QA): Horas Vendidas/No asignadas a servicios no existen
  // como campo por-proyecto (no responden al filtro de Tipo/Proyecto —
  // inventar una distribución filtrable sería peor que ser explícito sobre
  // ese límite), pero SÍ deben responder al filtro de FECHA como el resto
  // de la sección — antes quedaban fijas al total anual completo aunque
  // Desde/Hasta acotara a unos pocos meses (misma matriz `flatMonthly` que
  // usa la sección "Detalle mensual por Concepto" de abajo, MISMA máscara).
  const monthMask = monthsInRange(getFilter("rentabilidad", "desde"), getFilter("rentabilidad", "hasta"));
  const vendidasPortfolio = maskMonthly(RENTABILIDAD_VENDIDAS_MENSUAL, monthMask).reduce((a, b) => a + b, 0);
  const noAsignadasPortfolio = maskMonthly(RENTABILIDAD_NOASIGNADAS_MENSUAL, monthMask).reduce((a, b) => a + b, 0);

  const resumen = dashboardSection("resumen", "Resumen", "", `
    ${sectionToolbar(perspectiveSwitch("rentabilidad", false))}
    <div class="activity-kpi-grid">
      ${renderDashboardKpi({ label: "Horas Vendidas", value: vendidasPortfolio, format: "hours" })}
      ${renderDashboardKpi({ label: "Horas Certificadas", value: certificadas, format: "hours" })}
      ${renderDashboardKpi({ label: "Horas Trabajadas", value: trabajadas, format: "hours" })}
      ${renderDashboardKpi({ label: "Rentabilidad horas", value: rentabilidadHoras, format: "signed" })}
      ${renderDashboardKpi({ label: "No asignadas a servicios", value: noAsignadasPortfolio, format: "hours" })}
    </div>`);

  // UX4 (owner live QA): Mejor/Peor Performance lado a lado en desktop —
  // reusa `.activity-grid-2`, el primitivo Hub de 2 columnas YA compartido
  // (Activity: stat+timeline; Asignaciones Dashboard v1: timeline+
  // disponibilidad; ver tools.css) en vez de crear un grid propio de
  // Rentabilidad. Stack vertical automático en viewport angosto (MISMA
  // media query compartida, sin override local). Cálculo/orden/colores/
  // población SIN cambios — solo composición.
  const rankingBody = rows.length ? `
    <div class="activity-grid-2">
      <div>
        <p class="perspective-label">Mejor performance</p>
        ${positivos.length ? positivos.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("") : `<p class="empty-note">Sin proyectos con rentabilidad ≥ 0.</p>`}
      </div>
      <div>
        <p class="perspective-label">Peor performance</p>
        ${negativos.length ? negativos.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("") : `<p class="empty-note">Sin proyectos con rentabilidad negativa.</p>`}
      </div>
    </div>
  ` : `<p class="empty-note">Sin proyectos para los filtros actuales.</p>`;
  const ranking = dashboardSection("ranking", "Ranking por Rentabilidad horas", "Certificadas − Trabajadas", rankingBody);

  const mensual = renderRentabilidadMensual(rows, monthMask);

  return resumen + ranking + mensual;
}

// `title` nativo (ver corrective — HANDOFF: "Cartera + Rentabilidad
// Completeness", B4): mismo contenido en AMBAS barras del par
// (Certificadas/Ejecutadas) del grupo — "hovering either relevant mark may
// show the complete comparison" — con el agregado del Tipo Y el desglose
// por proyecto (Hs Certificadas/Hs Ejecutadas/Margen Absoluto/Margen
// Relativo, mismas fórmulas ya aprobadas, sin etiquetas cualitativas).
function certEjecGroupTooltip(g) {
  const lines = [`${g.tipo} — Certificadas: ${g.certificadas} · Ejecutadas: ${g.trabajadas} · Margen Absoluto: ${diffPlain(g.margenAbsoluto)} · Margen Relativo: ${diffPlain(Math.round(g.margenRelativo * 10) / 10)}%`];
  g.rows.forEach(p => {
    const ma = p.certificadas - p.trabajadas;
    const mr = p.certificadas ? Math.round((ma / p.certificadas) * 1000) / 10 : 0;
    lines.push(`· ${p.proyecto} (${p.tipo}) — Certificadas: ${p.certificadas} · Ejecutadas: ${p.trabajadas} · Margen Absoluto: ${diffPlain(ma)} · Margen Relativo: ${diffPlain(mr)}%`);
  });
  return lines.join("\n");
}

function renderRentabilidadCertEjecutadas() {
  // Gate del ciclo de vida real-data (ver loadRentabilidadData) — MISMO
  // fetch que Resumen, gateado independiente por si el usuario entra
  // directo a esta perspectiva.
  if (state.rentabilidadDataStatus === "idle") loadRentabilidadData();
  if (state.rentabilidadDataStatus !== "ready") return renderRentabilidadDataState();
  const rows = rentabilidadFilteredProyectos();
  const groups = ["Mantenimiento", "Proyecto"].map(tipo => {
    const gRows = rows.filter(p => p.tipo === tipo);
    const certificadas = gRows.reduce((s, r) => s + r.certificadas, 0);
    const trabajadas = gRows.reduce((s, r) => s + r.trabajadas, 0);
    const margenAbsoluto = certificadas - trabajadas;
    const margenRelativo = certificadas ? (margenAbsoluto / certificadas * 100) : 0;
    return { tipo, rows: gRows, certificadas, trabajadas, margenAbsoluto, margenRelativo };
  }).filter(g => g.rows.length > 0);
  const maxVal = Math.max(...groups.flatMap(g => [g.certificadas, g.trabajadas]), 1);

  const analysisContent = groups.length ? `
    <p class="perspective-label" style="margin-bottom:2px">Hs Certificadas → SharePoint · Hs Ejecutadas → Time Logs</p>
    <div class="legend" style="margin-top:8px"><span class="l-certificadas">Certificadas</span><span class="l-ejecutadas">Ejecutadas</span></div>
    ${groups.map(g => {
      const tooltip = esc(certEjecGroupTooltip(g));
      return `
      <p class="perspective-label" style="margin-top:14px">${esc(g.tipo)}</p>
      <div class="bar-row" title="${tooltip}"><span class="bar-label">Certificadas</span>
        <div class="bar-track"><div class="bar-fill certificadas" style="width:${(g.certificadas / maxVal * 100).toFixed(1)}%"></div></div>
        <span class="bar-value">${g.certificadas}</span>
      </div>
      <div class="bar-row" title="${tooltip}"><span class="bar-label">Ejecutadas</span>
        <div class="bar-track"><div class="bar-fill ejecutadas" style="width:${(g.trabajadas / maxVal * 100).toFixed(1)}%"></div></div>
        <span class="bar-value">${g.trabajadas}</span>
      </div>
      <p style="margin:4px 0 0;font-size:.76rem;color:var(--ink-soft)">
        Margen Absoluto ${diffCell(g.margenAbsoluto)} · Margen Relativo ${diffCell(Math.round(g.margenRelativo * 10) / 10)}%
      </p>`;
    }).join("")}
  ` : `<p class="empty-note">Sin datos para los filtros actuales.</p>`;
  const analysisBody = `${sectionToolbar(perspectiveSwitch("rentabilidad", false))}${analysisContent}`;
  const analysis = dashboardSection("analisis", "Certificadas vs Ejecutadas", "por Tipo Proyecto Dash", analysisBody);

  const detailBody = rows.length ? `
    <table class="dashboard-table">
      <thead><tr><th class="left dashboard-table-col--compact">Tipo</th><th class="left">Proyecto</th><th>Hs Certificadas</th><th>Hs Ejecutadas</th><th>Margen Absoluto</th><th>Margen Relativo</th></tr></thead>
      <tbody>${rows.map(p => {
        const margenAbsoluto = p.certificadas - p.trabajadas;
        const margenRelativo = p.certificadas ? Math.round((margenAbsoluto / p.certificadas) * 1000) / 10 : 0;
        return `<tr>
          <td class="left">${esc(p.tipo)}</td><td class="left">${esc(p.proyecto)}</td>
          <td>${fmtNum(p.certificadas, "hours")}</td><td>${fmtNum(p.trabajadas, "hours")}</td>
          <td>${diffCell(margenAbsoluto)}</td><td>${diffCell(margenRelativo)}%</td>
        </tr>`;
      }).join("")}</tbody>
    </table>
  ` : `<p class="empty-note">Sin proyectos para los filtros actuales.</p>`;
  const detail = dashboardSection("detalle", "Detalle por proyecto", rows.length + " proyecto(s)", detailBody);

  return analysis + detail;
}

function renderRentabilidad() {
  const persp = state.perspective.rentabilidad;
  const sectionsHtml = persp === "Resumen" ? renderRentabilidadResumen() : renderRentabilidadCertEjecutadas();
  return dashboardSections("reporting-rentabilidad", sectionsHtml);
}

// Hierarchía por perspectiva (ver corrective): Client Partner reproduce el
// eje CP > Tipo > Cliente del reporte original; Proyecto reagrupa SIN CP como
// eje primario (Tipo > Cliente > Proyecto es la jerarquía más cercana al
// comportamiento real soportada por los campos del PBIP: Tipo Proyecto Dash,
// Cliente, Proyecto). Son DOS perspectivas de UNA sola experiencia — mismo
// grammar visual, misma matriz mensual, mismo dataset base (HORASVENDIDAS_ROWS).
const HORASVENDIDAS_HIERARCHY = {
  "Client Partner": { keys: ["cp", "tipo", "cliente", "proyecto"], labels: ["Client Partner", "Tipo", "Cliente", "Proyecto"] },
  "Proyecto": { keys: ["tipo", "cliente", "proyecto"], labels: ["Tipo", "Cliente", "Proyecto"] },
};

// Estado de expansión — separado por EXPERIENCIA+perspectiva (nunca solo por
// perspectiva: "Client Partner" existe como label en más de una experiencia —
// Horas Vendidas Y Calendario — y colisionaría si se keyeara solo por label).
// `stateKey` = "<experienceId>::<perspectiva>". Sets creados on-demand.
// Vacío = todo colapsado (default: solo el nivel superior visible).
const treeExpanded = {};
function treeExpandedSet(stateKey) {
  if (!treeExpanded[stateKey]) treeExpanded[stateKey] = new Set();
  return treeExpanded[stateKey];
}

// Contexto vivo (rows/keys YA filtrados) por stateKey — registrado en cada
// render() por el renderer de la experiencia (registerTreeContext), leído
// SOLO por expandAllTree para poder recorrer el árbol completo sin que el
// botón de la toolbar tenga que cargar el dataset entero en el atributo
// onclick. Nunca un segundo modelo de expansión — sigue siendo
// treeExpandedSet/buildTree, esto es solo memoria de "qué se está mostrando
// ahora mismo".
const treeContext = {};
function registerTreeContext(stateKey, rows, keys) { treeContext[stateKey] = { rows, keys }; }
function collapseAllTree(stateKey) {
  treeExpandedSet(stateKey).clear();
  render();
}
function expandAllTree(stateKey) {
  const ctx = treeContext[stateKey];
  if (!ctx) return;
  const set = treeExpandedSet(stateKey);
  (function walk(nodes) {
    nodes.forEach(n => { if (n.children && n.children.length) { set.add(n.pathKey); walk(n.children); } });
  })(buildTree(ctx.rows, ctx.keys));
  render();
}
// Botones sueltos (sin wrapper) — Contraer→Expandir, orden tal como lo
// especifica el corrective. Se combinan con el perspective-switch de la
// sección (si corresponde) dentro de UN solo `sectionToolbar(...)`, nunca
// dos toolbars apilados.
// "Expandir todo"/"Contraer todo" (Hub Dashboard Contextual View Control
// corrective, ver HANDOFF) — MISMO texto y MISMO orden que Staffing (antes
// "Contraer"/"Expandir", sin "todo" y en el orden inverso): estos botones
// afectan la jerarquía COMPLETA, no solo la fila actual.
function treeToolbarButtons(stateKey) {
  return `<button type="button" onclick="expandAllTree('${stateKey}')">Expandir todo</button>
    <button type="button" onclick="collapseAllTree('${stateKey}')">Contraer todo</button>`;
}
// `.staffing-timeline-toolbar` — MISMA clase que el "Expandir todo/Contraer
// todo" de Staffing (ver staffing_dashboard.html), reusada tal cual (Align
// with Hub backbone corrective, ver HANDOFF: "reusar el motor de árbol
// existente, nunca un segundo modelo de expansión"). Hub Visual Consistency
// corrective (ver HANDOFF): ahora también aloja el perspective-switch de la
// sección cuando corresponde (Cartera/Rentabilidad/Horas Vendidas/
// Calendario/Capacidad) — mismo criterio que el toolbar "Agrupar por
// Proyecto/Consultor" de Staffing, que ya combina un toggle de vista con
// Expandir/Contraer en una sola fila. Sin partes vacías: si ninguna parte
// aplica, no se renderiza el wrapper.
function sectionToolbar(...partsHtml) {
  const inner = partsHtml.filter(Boolean).join("");
  return inner ? `<div class="staffing-timeline-toolbar">${inner}</div>` : "";
}

// `monthMask` (opcional, Q2 — ver `monthsInRange`): meses fuera del rango
// Desde/Hasta activo quedan en 0. `undefined` (default — Calendario/
// Capacidad No Asignadas no lo pasan, sin filtro de fecha en esas
// experiencias) preserva el comportamiento exacto de siempre.
function sumMonthly(rows, monthMask) {
  const totals = new Array(12).fill(0);
  rows.forEach(r => r.monthly.forEach((v, i) => { totals[i] += v; }));
  return maskMonthly(totals, monthMask);
}

// Agrupa `rows` recursivamente según `keys` — GENÉRICO sobre la forma del
// dato (cada nodo trae `rows`: las filas crudas que agrupa, nunca un
// resumen hardcodeado) y sus hijos (grupo siguiente), hasta agotar `keys`
// (el último nivel son las filas hoja individuales, sin más hijos). Reusado
// por matrices mensuales (Horas Vendidas/Calendario/Capacidad-No Asignadas,
// vía `sumMonthly(node.rows)` en el renderer) Y por tablas de negocio
// no-mensuales (Cartera/Proyectos, Capacidad-Productivas, vía
// `renderBusinessTreeRows`) — un solo motor de agrupación para los dos
// tipos de tabla jerárquica del prototipo.
function buildTree(rows, keys, path = []) {
  if (keys.length === 0) return [];
  const [key, ...restKeys] = keys;
  const groups = new Map();
  rows.forEach(r => {
    const val = r[key];
    if (!groups.has(val)) groups.set(val, []);
    groups.get(val).push(r);
  });
  return [...groups.entries()].map(([val, groupRows]) => {
    const nodePath = [...path, val];
    return {
      label: val,
      pathKey: nodePath.join("|"),
      rows: groupRows,
      children: buildTree(groupRows, restKeys, nodePath),
    };
  });
}

function toggleTreeNode(stateKey, pathKey) {
  const set = treeExpandedSet(stateKey);
  if (set.has(pathKey)) set.delete(pathKey); else set.add(pathKey);
  render();
}

// Control de disclosure jerárquico compartido (corrective S.4, ver HANDOFF
// "REPORTING INTEGRAL — REAL DATA": "shared Hub hierarchy disclosure
// interaction") — UN solo helper consumido por AMBOS renderers de árbol
// (renderTreeRows/renderBusinessTreeRows), nunca duplicado. Con hijos: UN
// control accesible real (`<button>`, focoable, Enter/Space nativo del
// browser — sin JS de teclado a mano) que envuelve caret+label, así clic en
// CUALQUIERA de los dos dispara el MISMO toggle (antes solo el caret
// reaccionaba en Asignaciones — ver `staffing_dashboard.html`, MISMA clase
// acá). Sin hijos (hoja): `<span>` plano, NO botón — sin affordance de
// puntero/expansión engañosa, clic no hace nada (owner live QA: "leaf rows
// must not appear expandable").
function hierarchyToggleHtml(stateKey, pathKey, label, hasChildren, isOpen, caretChar) {
  const caretHtml = `<span class="caret ${hasChildren ? "" : "leaf"}">${caretChar}</span>`;
  if (!hasChildren) {
    return `<span class="dashboard-table-toggle dashboard-table-toggle--leaf">${caretHtml}${esc(label)}</span>`;
  }
  const escapedPath = esc(pathKey).replace(/'/g, "\\'");
  return `<button type="button" class="dashboard-table-toggle" onclick="toggleTreeNode('${stateKey}', '${escapedPath}')" aria-expanded="${isOpen}">${caretHtml}${esc(label)}</button>`;
}

// `showTotal` (opcional, default false — Horas Vendidas no lo usa, sin
// cambio de comportamiento ahí): agrega una columna Total por fila, tal como
// trae "Calendario x CP" en el PBIP fuente (Values incluye "Total" además de
// los 12 meses). La columna Total SIEMPRE es la suma cruda (nunca pasa por
// `cellFormatter`) — es una cuenta real incluso cuando las celdas mensuales
// son booleanas (ver Calendario/HANDOFF: "Total" = meses con checkpoint en
// el año, información genuina, no un booleano).
//
// `cellFormatter` (opcional, default `v => v || "—"` — Horas Vendidas/
// Capacidad No Asignadas no lo pasan, sin cambio de comportamiento ahí):
// cómo se renderiza CADA celda mensual. Calendario pasa un formateador de
// tilde (ver corrective — HANDOFF: "las celdas de Calendario_Cliente son
// booleanas, 1 = tilde, 0 = blanco — nunca la cantidad cruda").
// `monthMask` (opcional, Q2 — ver `monthsInRange`): SOLO Horas Vendidas lo
// pasa (única de las 3 experiencias que consumen este motor con filtro de
// fecha activo — Calendario/Capacidad No Asignadas no tienen Desde/Hasta,
// ver HANDOFF). `undefined` preserva el comportamiento exacto de siempre.
function renderTreeRows(stateKey, nodes, depth, showTotal = false, cellFormatter, monthMask) {
  const format = cellFormatter || (v => (v === 0 || v ? fmtNum(v, "hours") : "—"));
  const set = treeExpandedSet(stateKey);
  return nodes.map(node => {
    const hasChildren = node.children && node.children.length > 0;
    const isOpen = set.has(node.pathKey);
    const caretChar = !hasChildren ? "" : (isOpen ? "▾" : "▸");
    const indentPx = depth * 20;
    const monthly = sumMonthly(node.rows, monthMask);
    const rowTotal = monthly.reduce((a, b) => a + b, 0);
    const rowHtml = `
      <tr class="depth-${depth}">
        <td class="row-label">
          <span class="dashboard-table-indent" style="width:${indentPx}px"></span>
          ${hierarchyToggleHtml(stateKey, node.pathKey, node.label, hasChildren, isOpen, caretChar)}
        </td>
        ${monthly.map(v => `<td>${format(v)}</td>`).join("")}
        ${showTotal ? `<td>${fmtNum(rowTotal, "count")}</td>` : ""}
      </tr>`;
    const childrenHtml = (hasChildren && isOpen) ? renderTreeRows(stateKey, node.children, depth + 1, showTotal, cellFormatter, monthMask) : "";
    return rowHtml + childrenHtml;
  }).join("");
}

// --- Contrato de agregación jerárquica (ver corrective — HANDOFF:
// "Hierarchical Measure Rollup Backbone") -----------------------------------
// Un nodo colapsado NUNCA debe ocultar información — solo hijos. Cada
// columna de una tabla de negocio (`columns[]`, consumida por
// `renderBusinessTreeRows`) declara CÓMO se agrega hacia el padre vía
// `rollup(rows)`, donde `rows` son SIEMPRE las filas hoja reales de ese
// subárbol (`node.rows`, ver `buildTree` — independiente del estado
// expandido/colapsado, así que el valor del padre nunca cambia al
// expandir/contraer). Tres clases de medida (ver corrective):
//   A. ADITIVA — SUM(hojas), `sumField(field)` sin formatFn.
//   B. DERIVADA/RATIO contra un denominador FIJO (semántica REMOVEFILTERS
//      — Capacidad/% Incidencia, Comercial/Participación): NUNCA se suma/
//      promedia el % ya renderizado — se re-suma el NUMERADOR crudo del
//      subárbol y se lo pasa por la MISMA función que ya calcula el % de
//      una fila hoja contra el total fijo (`capacidadPctIncidencia`/
//      `comercialPctVendidas`/etc. — esas funciones YA encapsulan "dividir
//      por el total fijo y formatear"), vía `sumField(field, esaFuncion)`
//      — un solo primitivo cubre las clases A y B, sin un segundo helper
//      de ratio.
//   C. CATEGÓRICA — sin `rollup` (el default ya existente, padre en
//      blanco — Salud de Comercial, columnas sin equivalente único) O
//      `uniformValue(field, formatFn)` (Cartera + Rentabilidad
//      Completeness corrective, ver HANDOFF) cuando el valor del padre ES
//      objetivamente derivable: si TODAS las hojas del subárbol comparten
//      el mismo valor, se propaga (Cliente con 1 solo Proyecto "En curso"
//      → el Cliente muestra "En curso"); si hay más de un valor distinto,
//      blanco — NUNCA "peor gana"/"mejor gana"/mayoría/primer hijo
//      arbitrario.
// Reusado por Cartera/Proyectos, Capacidad/Productivas y Comercial/
// Participación — un solo contrato, cero lógica de agregación repetida a
// mano por experiencia (ver HANDOFF: "no debe convertirse en agregación
// específica de renderCartera/renderHorasVendidas/...").
function sumField(field, formatFn) {
  return rows => {
    const total = rows.reduce((s, r) => s + (r[field] || 0), 0);
    return formatFn ? formatFn(total) : total;
  };
}
// Rollup conservador para columnas categóricas (ver corrective — HANDOFF:
// "Cartera + Rentabilidad Completeness", regla A3): NUNCA promedia/vota —
// solo propaga cuando el valor es objetivamente inequívoco (un único valor
// distinto entre las hojas del subárbol). Dos o más valores distintos, o
// cero hojas con valor no vacío, caen al blanco por defecto (mismo
// comportamiento que "sin rollup").
function uniformValue(field, formatFn) {
  return rows => {
    const distinct = [...new Set(rows.map(r => r[field]).filter(v => v !== undefined && v !== null && v !== ""))];
    if (distinct.length !== 1) return "";
    return formatFn ? formatFn(distinct[0]) : distinct[0];
  };
}

// Renderer alternativo para tablas de negocio NO mensuales (Cartera/
// Proyectos, Capacidad/Ejecución → Productivas, Comercial/Participación/
// Salud) — MISMO motor de agrupación (buildTree) y MISMAS afordancias
// visuales (dashboard-table-toggle/caret/dashboard-table-indent/depth-N),
// pero las columnas son arbitrarias (Diff Hs/Status/Horas/% Incidencia/
// etc., ver `columns`) en vez de 12 meses fijos.
//
// CORRECTIVE (owner live QA — "Avanti", ver HANDOFF): el supuesto de que
// un nodo SIN hijos declarados siempre tiene EXACTAMENTE 1 fila fuente
// (`node.rows[0]`) era FALSO con datos reales — Cartera/Mantenimiento
// conserva grano MENSUAL (`_mantenimiento_rows`, ver reporting_service.py:
// "Mantenimiento conserva el grano mensual"), así que un proyecto activo
// en 2+ meses aporta 2+ filas que comparten la MISMA clave de hoja
// (cliente+proyecto — no hay nivel de Mes en la jerarquía). `buildTree`
// las agrupa en UN nodo sin hijos declarados (`node.children.length===0`),
// pero `node.rows` sigue teniendo TODAS esas filas — usar solo `[0]`
// descartaba los meses restantes, mientras el PADRE (que sí agregaba con
// `c.rollup` sobre TODAS las filas del subárbol) mostraba el total
// correcto → parent ≠ único-hijo-visible, exactamente el síntoma
// reportado.
//
// Fix: SIEMPRE usar `c.rollup(node.rows)` cuando la columna lo define,
// sin importar si el nodo tiene hijos — matemáticamente `rollup` sobre
// una única fila da EL MISMO resultado que `render` de esa fila
// (`sumField`/`uniformValue` con 1 elemento == esa fila), así que unificar
// el camino es seguro y elimina la clase entera de bug (nunca más un
// nivel "hoja" con >1 fila fuente silenciosamente truncado a la primera).
// Columnas genuinamente categóricas SIN `rollup` (ver contrato de
// agregación arriba) solo pueden mostrarse de forma inequívoca cuando hay
// EXACTAMENTE 1 fila — con más de una, blanco a propósito (nunca un valor
// arbitrario "de la primera fila que aparezca").
function renderBusinessTreeRows(stateKey, nodes, depth, columns) {
  const set = treeExpandedSet(stateKey);
  return nodes.map(node => {
    const hasChildren = node.children && node.children.length > 0;
    const isOpen = set.has(node.pathKey);
    const caretChar = !hasChildren ? "" : (isOpen ? "▾" : "▸");
    const indentPx = depth * 20;
    const rowHtml = `
      <tr class="depth-${depth}">
        <td class="row-label">
          <span class="dashboard-table-indent" style="width:${indentPx}px"></span>
          ${hierarchyToggleHtml(stateKey, node.pathKey, node.label, hasChildren, isOpen, caretChar)}
        </td>
        ${columns.map(c => {
          let content = "";
          if (c.rollup) content = c.rollup(node.rows);
          else if (node.rows.length === 1) content = c.render(node.rows[0]);
          return `<td class="${c.statusTerminal ? "dashboard-table-cell--status" : (c.numeric ? "" : "left")}">${content}</td>`;
        }).join("")}
      </tr>`;
    const childrenHtml = (hasChildren && isOpen) ? renderBusinessTreeRows(stateKey, node.children, depth + 1, columns) : "";
    return rowHtml + childrenHtml;
  }).join("");
}

function renderHorasVendidas() {
  // Gate del ciclo de vida real-data Batch A (ver loadBatchAData) — MISMO
  // criterio que renderCartera.
  if (state.batchADataStatus === "idle") loadBatchAData();
  if (state.batchADataStatus !== "ready") return renderBatchADataState("los datos de Horas Vendidas");
  const persp = state.perspective.horasvendidas;
  const hierarchy = HORASVENDIDAS_HIERARCHY[persp];
  const rows = facetFilteredRows("horasvendidas");
  const stateKey = "horasvendidas::" + persp;
  const tree = buildTree(rows, hierarchy.keys);
  registerTreeContext(stateKey, rows, hierarchy.keys);
  // Q2 (owner live QA): cada fila YA filtrada por `fecha` (mes más
  // temprano con datos, ver facetFilteredRows) puede tener meses REALES
  // fuera del rango Desde/Hasta activo dentro de su propio `monthly[12]`
  // (un proyecto activo Ene-Dic pasa el filtro por su `fecha`=Ene, pero
  // seguía aportando Sep-Dic a la matriz/KPIs aunque Hasta=agosto). La
  // máscara se aplica DESPUÉS de agregar (matriz, KPIs, Q1-Q4) — misma
  // población filtrada para toda la sección, nunca un cálculo que la
  // ignore.
  const monthMask = monthsInRange(getFilter("horasvendidas", "desde"), getFilter("horasvendidas", "hasta"));
  const totalMonthly = sumMonthly(rows, monthMask);

  const annual = totalMonthly.reduce((s, v) => s + v, 0);
  const mant = rows.filter(r => r.tipo === "Mantenimiento").reduce((s, r) => s + maskMonthly(r.monthly, monthMask).reduce((a, b) => a + b, 0), 0);
  const proy = rows.filter(r => r.tipo === "Proyecto").reduce((s, r) => s + maskMonthly(r.monthly, monthMask).reduce((a, b) => a + b, 0), 0);
  const quarter = q => totalMonthly.slice(q * 3, q * 3 + 3).reduce((a, b) => a + b, 0);

  const resumen = dashboardSection("resumen", "Resumen", rows.length + " proyecto(s)", `
    <div class="activity-kpi-grid">
      ${renderDashboardKpi({ label: "Hs Estimadas anuales", value: annual, format: "hours" })}
      ${renderDashboardKpi({ label: "Mantenimiento", value: mant, format: "hours" })}
      ${renderDashboardKpi({ label: "Proyecto", value: proy, format: "hours" })}
      ${renderDashboardKpi({ label: "Vendidas Q1", value: quarter(0), format: "hours" })}
      ${renderDashboardKpi({ label: "Vendidas Q2", value: quarter(1), format: "hours" })}
      ${renderDashboardKpi({ label: "Vendidas Q3", value: quarter(2), format: "hours" })}
      ${renderDashboardKpi({ label: "Vendidas Q4", value: quarter(3), format: "hours" })}
    </div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("horasvendidas", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr><th class="row-label-col">${hierarchy.labels.join(" / ")}</th>${MESES.map(m => `<th>${m}</th>`).join("")}</tr></thead>
        <tbody>
          ${renderTreeRows(stateKey, tree, 0, false, undefined, monthMask)}
          <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v === 0 || v ? fmtNum(v, "hours") : "—"}</td>`).join("")}</tr>
        </tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", persp, hierarchy.labels.join(" › "), detalleBody);

  return dashboardSections("reporting-horasvendidas", resumen + detalle);
}

// Participación por condición comercial (ver corrective — HANDOFF):
// jerarquía REAL de la pivotTable principal de "Analisis Participacion -
// Condiciones Comerciales -" — ID Frecuencia > Permite Adelanto > Condición
// de Pago > Cliente, EXACTO ese orden de Rows. Reusa buildTree/
// renderBusinessTreeRows sin cambios — mismo motor que Cartera/Capacidad.
function renderComercialParticipacion() {
  // Gate del ciclo de vida real-data Batch A — MISMO status que Salud de
  // Cliente (ambas perspectivas de Comercial ya conectadas, ver HANDOFF).
  if (state.batchADataStatus === "idle") loadBatchAData();
  if (state.batchADataStatus !== "ready") return renderBatchADataState("los datos de Participación");
  const rows = facetFilteredRows("comercial");
  const keys = ["idFrecuencia", "permiteAdelanto", "condicionPago", "cliente"];
  const stateKey = "comercial::Participación";
  const tree = buildTree(rows, keys);
  registerTreeContext(stateKey, rows, keys);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("comercial", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr>
          <th class="row-label-col">ID Frecuencia / Permite Adelanto / Condición de Pago / Cliente</th>
          ${COMERCIAL_PARTICIPACION_COLUMNS.map(c => `<th>${esc(c.label)}</th>`).join("")}
        </tr></thead>
        <tbody>${renderBusinessTreeRows(stateKey, tree, 0, COMERCIAL_PARTICIPACION_COLUMNS)}</tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", "Participación por condición comercial", rows.length + " cliente(s)", detalleBody);

  return dashboardSections("reporting-comercial", detalle);
}

// Salud de Cliente (ver corrective) — jerarquía REAL de "Client Partner -
// Status de Cliente detalle": Client Partner > Cliente, columnas
// categóricas (sin rollup, ver comentario del dataset).
function renderComercialSalud() {
  // Gate del ciclo de vida real-data (ver loadBatchAData) — internal names
  // reales de Cliente confirmados por diagnóstico Graph en vivo del owner.
  if (state.batchADataStatus === "idle") loadBatchAData();
  if (state.batchADataStatus !== "ready") return renderBatchADataState("los datos de Salud de Cliente");
  const rows = facetFilteredRows("comercial");
  const keys = ["cp", "cliente"];
  const stateKey = "comercial::Salud de Cliente";
  const tree = buildTree(rows, keys);
  registerTreeContext(stateKey, rows, keys);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("comercial", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr>
          <th class="row-label-col">Client Partner / Cliente</th>
          ${COMERCIAL_SALUD_COLUMNS.map(c => `<th class="left">${esc(c.label)}</th>`).join("")}
        </tr></thead>
        <tbody>${renderBusinessTreeRows(stateKey, tree, 0, COMERCIAL_SALUD_COLUMNS)}</tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", "Salud de Cliente", rows.length + " cliente(s)", detalleBody);

  return dashboardSections("reporting-comercial", detalle);
}

function renderComercial() {
  const persp = state.perspective.comercial;
  return persp === "Participación" ? renderComercialParticipacion() : renderComercialSalud();
}

// Celda booleana (ver corrective — HANDOFF, owner screenshot): 1 → tilde,
// 0/vacío → blanco. La fila hoja SIEMPRE es 0 o 1 (dataset); un nodo padre
// puede sumar >1 (varios hijos con checkpoint ese mes) — sigue mostrando
// tilde (¿"al menos un hijo tiene programado algo ese mes?"), nunca la
// cuenta cruda en la celda mensual. La columna Total (aparte, ver abajo)
// SÍ es la cuenta real.
function renderCalendarioCell(v) {
  return v ? "✓" : "";
}

function renderCalendario() {
  // Gate del ciclo de vida real-data Batch A (ver loadBatchAData).
  if (state.batchADataStatus === "idle") loadBatchAData();
  if (state.batchADataStatus !== "ready") return renderBatchADataState("los datos de Calendario");
  const persp = state.perspective.calendario;
  const hierarchy = CALENDARIO_HIERARCHY[persp];
  const rows = facetFilteredRows("calendario");
  const stateKey = "calendario::" + persp;
  const tree = buildTree(rows, hierarchy.keys);
  registerTreeContext(stateKey, rows, hierarchy.keys);
  const totalMonthly = sumMonthly(rows);
  const grandTotal = totalMonthly.reduce((a, b) => a + b, 0);

  const resumen = dashboardSection("resumen", "Resumen", "", `
    <div class="activity-kpi-grid">
      ${renderDashboardKpi({ label: "Clientes con calendario", value: rows.length, format: "count" })}
      ${renderDashboardKpi({ label: "Entregas/checkpoints (año)", value: grandTotal, format: "count" })}
    </div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("calendario", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr><th class="row-label-col">${hierarchy.labels.join(" / ")}</th>${MESES.map(m => `<th>${m}</th>`).join("")}<th>Total</th></tr></thead>
        <tbody>
          ${renderTreeRows(stateKey, tree, 0, true, renderCalendarioCell)}
          <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${fmtNum(v, "count")}</td>`).join("")}<td>${fmtNum(grandTotal, "count")}</td></tr>
        </tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", persp, hierarchy.labels.join(" › "), detalleBody);

  return dashboardSections("reporting-calendario", resumen + detalle);
}

// FUNCIÓN, no `const` precalculada (mismo corrective que
// comercialParticipacionTotalVendidas — ver HANDOFF Batch A):
// `CAPACIDAD_PRODUCTIVAS_ROWS` se reemplaza IN PLACE con datos reales
// (`replaceRows`), una constante calculada una sola vez al cargar el
// script habría quedado stale contra el mock para siempre.
function capacidadProductivasTotal() { return CAPACIDAD_PRODUCTIVAS_ROWS.reduce((s, r) => s + r.horas, 0); }

function capacidadPctIncidencia(horas) {
  // % Incidencia real (medida `Time Logs Reports.[% Hs ejecutadas]`) =
  // horas de la fila ÷ horas TOTALES (REMOVEFILTERS Cliente/Client
  // Partner) — SIEMPRE contra el gran total FIJO, nunca recalculado por el
  // filtro de Cliente del V0, tanto en filas hoja como en el rollup de
  // Cliente — así se preserva la semántica real de la medida DAX incluso
  // cuando el filtro deja visible un subconjunto.
  const total = capacidadProductivasTotal();
  return (total ? (horas / total * 100) : 0).toFixed(1) + "%";
}

// Rollup jerárquico (ver Hierarchical Measure Rollup Backbone corrective):
// Horas es Clase A (aditiva, `sumField` sin formatFn); % Incidencia es
// Clase B contra el total FIJO (`sumField` con `capacidadPctIncidencia`
// como formatFn) — mismo contrato que Comercial/Participación, cero
// duplicación de la fórmula de ratio.
const CAPACIDAD_PRODUCTIVAS_COLUMNS = [
  { label: "Horas", numeric: true, render: r => r.horas, rollup: sumField("horas") },
  { label: "% Incidencia", numeric: true, render: r => capacidadPctIncidencia(r.horas), rollup: sumField("horas", capacidadPctIncidencia) },
];

function renderCapacidadProductivas() {
  // Gate del ciclo de vida real-data Batch B (ver loadBatchBData) —
  // Correction 1 CERRADA: Productivas es CSV de Time Logs, no backbone de
  // Cartera (ver HANDOFF/real-data-lineage.md §8bis).
  if (state.batchBDataStatus === "idle") loadBatchBData();
  if (state.batchBDataStatus !== "ready") return renderBatchBDataState("los datos de Horas Productivas");
  const rows = facetFilteredRows("capacidad");
  const filteredTotal = rows.reduce((s, r) => s + r.horas, 0);

  // Orden por Horas de Cliente descendente == el Rank real del PBIP
  // (RANKX por Horas), representado vía orden visual (ver comentario del
  // dataset) — nunca una columna Rank redundante con el orden.
  const clientTotals = new Map();
  rows.forEach(r => clientTotals.set(r.cliente, (clientTotals.get(r.cliente) || 0) + r.horas));
  const rowsSorted = [...rows].sort((a, b) => clientTotals.get(b.cliente) - clientTotals.get(a.cliente));
  const stateKey = "capacidad::Productivas";
  const tree = buildTree(rowsSorted, ["cliente", "proyecto"]);
  registerTreeContext(stateKey, rowsSorted, ["cliente", "proyecto"]);

  const resumen = dashboardSection("resumen", "Resumen", "", `
    <div class="activity-kpi-grid">${renderDashboardKpi({ label: "Total Horas", value: filteredTotal, format: "hours" })}</div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("capacidad", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr><th class="row-label-col">Cliente / Proyecto</th><th>Horas</th><th>% Incidencia</th></tr></thead>
        <tbody>${renderBusinessTreeRows(stateKey, tree, 0, CAPACIDAD_PRODUCTIVAS_COLUMNS)}</tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", "Horas consumidas por Cliente", "Cliente › Proyecto, ordenado por Rank (Horas desc.)", detalleBody);

  return resumen + detalle;
}

function renderCapacidadNoAsignadas() {
  // Gate del ciclo de vida real-data Batch B (ver loadBatchBData) — MISMO
  // fetch/endpoint que Productivas (Correction 1 CERRADA, ver HANDOFF/
  // real-data-lineage.md §8bis), gateado independiente por si el usuario
  // entra directo a esta perspectiva.
  if (state.batchBDataStatus === "idle") loadBatchBData();
  if (state.batchBDataStatus !== "ready") return renderBatchBDataState("los datos de Horas No Asignadas");
  const baseRows = facetFilteredRows("capacidad");
  // Row label = "Consultor (Hs Asig)" — MISMA columna calculada que la
  // fuente real (Colaboradores["Consultor + Hs asig"]), no una etiqueta
  // inventada. `hsAsig` es la capacidad de referencia estática del
  // consultor, separada de `monthly` (horas NO asignadas reales por mes,
  // ver dataset).
  const rows = baseRows.map(r => ({ ...r, consultorLabel: `${r.consultor} (${r.hsAsig} hs Asig)` }));
  const tree = buildTree(rows, ["consultorLabel"]);
  const totalMonthly = sumMonthly(rows);
  const stateKey = "capacidad::No Asignadas";

  // Sin Expandir/Contraer a propósito: jerarquía de UN solo nivel
  // (Consultor), flat — el corrective es explícito en que las tablas planas
  // no lo necesitan. El perspective-switch SÍ va acá (mismo criterio que
  // Productivas: "Detalle" es la sección estable en ambas subvistas de
  // Capacidad, ver HANDOFF).
  const body = `
    ${sectionToolbar(perspectiveSwitch("capacidad", false))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr><th class="row-label-col">Consultor</th>${MESES.map(m => `<th>${m}</th>`).join("")}</tr></thead>
        <tbody>
          ${renderTreeRows(stateKey, tree, 0)}
          <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v === 0 || v ? fmtNum(v, "hours") : "—"}</td>`).join("")}</tr>
        </tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}
    <p class="empty-note" style="text-align:left">Horas No Productivas registradas por cada colaborador en Time (Task: No Asignación a Servicio en Cliente) — indicador distinto de la disponibilidad de Asignaciones.</p>`;
  const detalle = dashboardSection("detalle", "Horas No Asignadas", "Consultor — Task: No Asignación a Servicio en Cliente", body);

  return detalle;
}

function renderCapacidad() {
  const persp = state.perspective.capacidad;
  const sectionsHtml = persp === "Productivas" ? renderCapacidadProductivas() : renderCapacidadNoAsignadas();
  return dashboardSections("reporting-capacidad", sectionsHtml);
}

const RENDERERS = {
  cartera: renderCartera, rentabilidad: renderRentabilidad, horasvendidas: renderHorasVendidas,
  comercial: renderComercial, calendario: renderCalendario, capacidad: renderCapacidad,
};
// `render()` trata cualquier experiencia sin entrada acá como "" (sin
// barra) — ya no aplica a ninguna de las 6 (ver Commercial/Clients
// corrective, HANDOFF).
const FILTER_BARS = {
  cartera: renderCarteraFilterBar, rentabilidad: renderRentabilidadFilterBar,
  horasvendidas: renderHorasVendidasFilterBar, calendario: renderCalendarioFilterBar,
  capacidad: renderCapacidadFilterBar, comercial: renderComercialFilterBar,
};

function render() {
  document.getElementById("reporting-experience-tabs").innerHTML = EXPERIENCES.map(e => `
    <button class="${state.experience === e.id ? "is-active" : ""}" onclick="setExperience('${e.id}')">${esc(e.label)}</button>
  `).join("");
  const filterBarHtml = FILTER_BARS[state.experience] ? FILTER_BARS[state.experience]() : "";
  document.getElementById("reporting-main-content").innerHTML = filterBarHtml + RENDERERS[state.experience]();
  // Backbone compartido de drag-and-drop/colapso (app.js) — se re-aplica en
  // cada render() porque acá el contenido se reemplaza por innerHTML
  // completo (sin htmx), a diferencia de Staffing/Actividad que lo disparan
  // vía el evento htmx:load. `initDashboardSections` es idempotente por
  // contenedor (ver app.js) — reordenamiento anterior en sessionStorage se
  // reaplica solo, sin duplicar listeners.
  initDashboardSections(document);
}

// El primer render() debe esperar a DOMContentLoaded: este <script> se
// ejecuta ANTES que app.js (que se carga al final de <body> en base.html,
// después del {% block body %} que contiene este archivo) — llamar
// initDashboardSections/render() de forma síncrona acá arriba fallaría
// (función todavía no definida). DOMContentLoaded solo dispara después de
// que TODOS los <script src> síncronos previos —incluido app.js— terminaron
// de cargar y ejecutar, así que para ese momento ya existe.
document.addEventListener("DOMContentLoaded", render);
