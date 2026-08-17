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

// HOT WINDOW / COLD HISTORY corrective (ver HANDOFF): a diferencia del
// resto de los facets <select> de Cartera (data-driven contra las filas YA
// cargadas), Año de Mantenimiento NO puede derivar sus opciones solo de
// `CARTERA_MANTENIMIENTO_ROWS` — con la ventana caliente por defecto (año
// actual + anterior) esas filas NUNCA incluyen los años fríos, y "DO NOT
// CONFUSE OPTIONS WITH HYDRATED FACTS" exige que un año frío siga siendo
// una opción SELECCIONABLE. Unión de la metadata liviana
// (`state.carteraMantenimientoAniosDisponibles`, ver
// loadCarteraMantenimientoAnios) con los años YA presentes en filas
// cargadas (defensivo: si la metadata todavía no llegó, los años calientes
// recién hidratados igual aparecen) — ambos faceteados por Estado, MISMO
// criterio que Año de Inicio de Proyecto arriba.
function carteraAnioOptions() {
  const fromMetadata = state.carteraMantenimientoAniosDisponibles
    .filter(p => state.carteraStatus[p.estado])
    .map(p => p.anio);
  const fromRows = CARTERA_MANTENIMIENTO_ROWS.filter(r => state.carteraStatus[r.status]).map(r => r.anio);
  return [...new Set([...fromMetadata, ...fromRows])].filter(Boolean).sort().reverse();
}
// Prune (MISMO criterio que pruneCarteraAnioInicio) — SOLO ante invalidez
// real (Estado ya no representa el año seleccionado), NUNCA por el mero
// cambio de perspectiva (ver setPerspective, PERSPECTIVE-LOCAL FILTER
// STATE corrective).
function pruneCarteraAnio() {
  const available = new Set(carteraAnioOptions());
  const current = (state.filters.cartera || {}).anio;
  if (current && !available.has(current)) state.filters.cartera.anio = "";
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

// SOURCE-PARITY RECONCILIATION (owner live comparison vs PBIP): Vendidas
// dejó de ser un escalar de portfolio — PBIP filtra Vendidas por Tipo
// Proyecto Dash/Status igual que Certificadas (ver page.json de "Analisis
// Rentabilidad"), y `HsVendidas` vive en la MISMA fila de "Horas por
// proyectos" que `Hs certificadas`, así que es igual de atribuible por
// proyecto — ver `vendByYear`/`vendMonthly` en `RENTABILIDAD_PROYECTOS`
// (mismo shape que certByYear/trabByYear) y `rentabilidadSelectedRows`.
// Solo "No asignadas a servicios" SIGUE siendo portfolio fijo (Time Logs de
// los 2 proyectos placeholder, fuera del universo de Proyectos — sin Tipo/
// Estado propios que filtrar, el PBIP tampoco lo filtra).
const RENTABILIDAD_NOASIGNADAS_MENSUAL = flatMonthly(RENTABILIDAD_RESUMEN.noAsignadas);
// Portfolio POR-AÑO (No asignadas) — poblado por loadRentabilidadData desde
// el service; el filtro Año elige el [12] del año (o colapsado si Todos).
let RENTABILIDAD_NOASIGNADAS_BY_YEAR = {};

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
  { idFrecuencia: "Mensual", permiteAdelanto: "Si", condicionPago: "30", cliente: "Andesmar", cp: "María López", horasVendidas: 140, horasCertificadas: 128, fecha: "2026-08-01" },
  { idFrecuencia: "Trimestral", permiteAdelanto: "No", condicionPago: "60", cliente: "Danone", cp: "María López", horasVendidas: 88, horasCertificadas: 96, fecha: "2026-07-01" },
  { idFrecuencia: "Mensual", permiteAdelanto: "No", condicionPago: "30", cliente: "Molinos", cp: "María López", horasVendidas: 60, horasCertificadas: 60, fecha: "2026-06-01" },
  { idFrecuencia: "Anual", permiteAdelanto: "No", condicionPago: "30", cliente: "AySA", cp: "Diego Ramos", horasVendidas: 208, horasCertificadas: 212, fecha: "2026-05-01" },
  { idFrecuencia: "Mensual", permiteAdelanto: "Si", condicionPago: "15", cliente: "YPF", cp: "Diego Ramos", horasVendidas: 360, horasCertificadas: 322, fecha: "2026-04-01" },
  { idFrecuencia: "Semestral", permiteAdelanto: "Si", condicionPago: "30", cliente: "Ternium", cp: "Diego Ramos", horasVendidas: 180, horasCertificadas: 166, fecha: "2026-03-01" },
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

// --- Semáforo de Salud de Cliente (primitive visual compartido) ------------
// Reconoce AMBOS vocabularios de la fuente: clasificación (Saludable/Regular/
// Crítico) Y color (Verde/Amarillo/Rojo) — el bug del owner ("casi todo el
// mismo estilo, solo cambia el texto") era que el mapeo viejo solo entendía
// "Verde"/"Rojo" y TODO lo demás caía a ámbar (si la fuente real usa Saludable/
// Regular/Crítico, todo salía ámbar). Score: Crítico=1, Regular=2, Saludable=3,
// vacío/no reconocido=null (no contamina el promedio del rollup). Normaliza
// acentos/case ("Crítico" → "critico").
function saludScore(value) {
  const v = String(value == null ? "" : value).trim().toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "");
  if (v === "saludable" || v === "verde") return 3;
  if (v === "regular" || v === "amarillo" || v === "amber") return 2;
  if (v === "critico" || v === "rojo") return 1;
  return null;
}
function saludColor(score) {
  return score === 3 ? "green" : score === 2 ? "amber" : score === 1 ? "red" : "grey";
}
const _SALUD_CLASS_LABEL = { 3: "Saludable", 2: "Regular", 1: "Crítico" };
// Badge semafórico: punto de color discreto (semáforo, `aria-hidden` — no se
// depende SOLO del color) + texto (accesible). Reusa el primitive compartido
// `dashboard-status-badge--{green|amber|red|grey}` (tools.css), nunca styling
// aislado. `label` opcional fuerza el texto (p. ej. la clase canónica del
// rollup); por defecto muestra el valor fuente tal cual (fidelidad en la hoja).
function saludBadge(score, label) {
  const text = label != null ? label : "Sin dato";
  return `<span class="dashboard-status-badge dashboard-status-badge--${saludColor(score)}">`
    + `<span class="salud-dot" aria-hidden="true">●</span>${esc(text)}</span>`;
}
function statusPillSalud(value) {
  const score = saludScore(value);
  const text = String(value == null ? "" : value).trim() || "Sin dato";
  return saludBadge(score, text);
}
// Rollup SEMÁNTICO para las filas padre COLAPSADAS (Objetivo 2): recibe las
// hojas del subárbol. Si todos los hijos no-null son el MISMO valor fuente →
// muestra ese valor. Si hay mezcla → promedio aritmético de los scores válidos
// mapeado a clase (>=2.5 Saludable, >=1.5 Regular, <1.5 Crítico). Si no hay
// datos válidos → "Sin dato". Para una hoja (1 fila) = statusPillSalud (mismo
// resultado, ver contrato de renderBusinessTreeRows).
function saludRollup(field) {
  return rows => {
    const scored = rows.map(r => ({ v: r[field], s: saludScore(r[field]) })).filter(x => x.s !== null);
    if (scored.length === 0) return saludBadge(null, "Sin dato");
    const distinctVals = [...new Set(scored.map(x => String(x.v).trim()))];
    if (distinctVals.length === 1) return statusPillSalud(scored[0].v);  // homogéneo: valor fuente
    const avg = scored.reduce((a, x) => a + x.s, 0) / scored.length;
    const cls = avg >= 2.5 ? 3 : (avg >= 1.5 ? 2 : 1);
    return saludBadge(cls, _SALUD_CLASS_LABEL[cls]);
  };
}

// Población canónica de Participación: Año + Estado (AMBOS checkbox
// multi-select, FUERA del motor de facets <select> — mismo criterio que Año
// de Inicio/Status de Cartera) proyectados a `horasVendidas`/
// `horasCertificadas` CRUDOS (suma sobre los años Y estados tildados de los
// mapas por-año-por-estado que entrega el backend, ver `_shape_
// participacion`). Año/Estado son SCOPE real de los datos (qué años están
// hidratados, qué proyectos cuentan) — nunca una de las dimensiones
// REMOVEFILTERS del pivotTable (esas son CP/ID Frecuencia/Permite Adelanto/
// Condición de Pago/Cliente, ver comentario de COMERCIAL_PARTICIPACION_
// COLUMNS más abajo). CP/Cliente se aplican DESPUÉS vía FACET_CONFIG.comercial
// (facetRowsExcept) — MISMO orden en dos pasos que Rentabilidad/Horas
// Vendidas (Estado primero, selects después). Alimenta la jerarquía (vía
// facetFilteredRows) Y el gran total fijo de abajo.
function comercialParticipacionSelectedRows() {
  const anios = state.comercialParticipacionAnios;
  const estados = state.comercialStatus;
  return COMERCIAL_PARTICIPACION_ROWS.map(r => {
    let vendidas = 0, certificadas = 0;
    anios.forEach(anio => {
      const vByEstado = (r.vendidasByYear || {})[anio] || {};
      const cByEstado = (r.certificadasByYear || {})[anio] || {};
      Object.keys(vByEstado).forEach(e => { if (estados[e]) vendidas += vByEstado[e] || 0; });
      Object.keys(cByEstado).forEach(e => { if (estados[e]) certificadas += cByEstado[e] || 0; });
    });
    return { ...r, horasVendidas: vendidas, horasCertificadas: certificadas };
  });
}
// Gran total FIJO (denominador REMOVEFILTERS) — FUNCIÓN, no `const`
// precalculada (corrective Batch A, ver HANDOFF): `COMERCIAL_PARTICIPACION_ROWS`
// se reemplaza IN PLACE con datos reales (`replaceRows`/`mergeComercial
// ParticipacionRows`, mismo patrón que Cartera/Horas Vendidas) — una
// constante calculada una sola vez al cargar el script habría quedado STALE
// contra el mock para siempre, rompiendo el % real. Recalculada en cada
// llamada contra `comercialParticipacionSelectedRows()` (Año+Estado YA
// proyectados, mock o real según el ciclo de vida vigente) — sigue siendo
// "fijo" en el sentido correcto: fijo por RENDER contra la población
// Año+Estado completa sin filtrar por CP/Cliente/etc, nunca contra el
// subtotal del grupo filtrado.
function comercialParticipacionTotalVendidas() { return comercialParticipacionSelectedRows().reduce((s, r) => s + r.horasVendidas, 0); }
function comercialParticipacionTotalCertificadas() { return comercialParticipacionSelectedRows().reduce((s, r) => s + r.horasCertificadas, 0); }
function comercialPctVendidas(horas) {
  const total = comercialParticipacionTotalVendidas();
  return (total ? (horas / total * 100) : 0).toFixed(2) + " %";
}
function comercialPctCertificadas(horas) {
  const total = comercialParticipacionTotalCertificadas();
  return (total ? (horas / total * 100) : 0).toFixed(2) + " %";
}
// Headers clickeables de % Participación (owner request — MISMO patrón que
// Horas/% Incidencia de Horas Productivas): clickear una columna la vuelve
// la clave activa (togglea asc/desc si ya era la activa). A diferencia de
// Horas Productivas, cada columna ordena por su PROPIO numerador crudo
// (`horasVendidas`/`horasCertificadas`) — nunca por el string "% Ya
// Formateado" ya renderizado, ni comparte dirección con la otra columna
// (Vendidas% y Certificadas% no son transform monótono una de la otra).
function toggleComercialParticipacionSort(key) {
  if (state.comercialParticipacionSortKey === key) {
    state.comercialParticipacionSortDir = state.comercialParticipacionSortDir === "asc" ? "desc" : "asc";
  } else {
    state.comercialParticipacionSortKey = key;
    state.comercialParticipacionSortDir = "desc";
  }
  render();
}
// PURO orden — nunca re-filtra ni re-agrega, solo reordena las filas hoja
// ANTES de `buildTree` (mismo mecanismo que `rowsFor` de Horas Productivas:
// reordenar las hojas cambia el orden en que sus grupos padre aparecen).
function comercialParticipacionSorted(rows) {
  const key = state.comercialParticipacionSortKey;
  if (!key) {
    // DEFAULT (primera carga, sin click en columnas): orden CRONOLÓGICO por
    // frecuencia (Mensual→Anual, `_frecuenciaCompare` compartido con
    // Calendario), NUNCA alfabético — el nivel superior de la jerarquía es
    // Frecuencia, así que ordenar las hojas por frecuencia hace que
    // `buildTree` emita los grupos en secuencia cronológica. Si el usuario
    // luego clickea una columna de %, gana ese orden (rama de abajo).
    return [...rows].sort((a, b) => _frecuenciaCompare(a.idFrecuencia, b.idFrecuencia));
  }
  const field = key === "vendidas" ? "horasVendidas" : "horasCertificadas";
  const sign = state.comercialParticipacionSortDir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => sign * (a[field] - b[field]));
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
// `rollup` (Objetivo 2 — filas padre COLAPSADas informativas): rollup SEMÁNTICO
// por columna (ver `saludRollup`). Para una hoja da lo mismo que `render` (el
// motor usa rollup cuando existe, ver renderBusinessTreeRows).
const COMERCIAL_SALUD_COLUMNS = [
  { label: "Financiera", numeric: false, render: r => statusPillSalud(r.saludFinanciera), rollup: saludRollup("saludFinanciera") },
  { label: "Relación", numeric: false, render: r => statusPillSalud(r.saludRelacion), rollup: saludRollup("saludRelacion") },
  { label: "Operativo", numeric: false, render: r => statusPillSalud(r.saludOperativo), rollup: saludRollup("saludOperativo") },
  { label: "Continuidad", numeric: false, render: r => statusPillSalud(r.saludContinuidad), rollup: saludRollup("saludContinuidad") },
  { label: "Estratégico", numeric: false, render: r => statusPillSalud(r.saludEstrategico), rollup: saludRollup("saludEstrategico") },
  { label: "Salud Final", numeric: false, render: r => statusPillSalud(r.saludFinal), rollup: saludRollup("saludFinal") },
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
// checkpoint programado ese mes, 0 = no) — NUNCA una cantidad. Bimestral
// significa "cada 2 meses tiene algo programado" (1 en 6 de los 12 meses),
// no una cuenta — la cantidad de eventos DENTRO del mes no es lo que esta
// tabla representa. Se renderiza como tilde (✓) cuando 1, blanco cuando 0
// — ver `renderCalendarioCell`. La columna Total (perspectiva Client
// Partner) SÍ sigue siendo una cuenta real (meses con checkpoint en el
// año), no booleana — un total de "12" es información genuina.
const CALENDARIO_ROWS = [
  { frecuencia: "Bimestral", cliente: "Andesmar", cp: "María López", monthly: [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0] },
  { frecuencia: "Bimestral", cliente: "AySA", cp: "Diego Ramos", monthly: [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0] },
  { frecuencia: "Mensual", cliente: "Danone", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Mensual", cliente: "Molinos", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Trimestral", cliente: "YPF", cp: "Diego Ramos", monthly: [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1] },
  { frecuencia: "Trimestral", cliente: "Ternium", cp: "Diego Ramos", monthly: [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0] },
];
// Orden real de negocio (ID Frecuencia) — CORREGIDO contra evidencia
// PRIMARIA del PBIP (owner live comparison: "la tabla Frecuencia no ordena
// por ID Frecuencia igual al PBIP"). `Cliente.tmdl`, columna calculada
// 'ID Frecuencia': SWITCH(Frecuencia Actualizacion, "Mensual",1,
// "Bimestral",2, "Trimestral",3, "Cuatrimestral",4, "Semestral",5,
// "Anual",6, "7  Sin especificar") — cadencia creciente REAL, nunca
// alfabético. La lista previa tenía "Semanal" en el índice 2 — un valor
// que ni existe en el SWITCH del PBIP y que rompía la cronología (semanal
// < mensual, nunca podría ir segundo).
const FRECUENCIA_ORDER = ["Mensual", "Bimestral", "Trimestral", "Cuatrimestral", "Semestral", "Anual"];
// Comparador compartido (Calendario + Participación/Comercial, ver
// `_RENT_SELECT_FIELDS`-style listas de facet fields abajo): un valor NO
// reconocido (fuera de las 6 categorías del SWITCH real, ver comentario
// arriba) ordena AL FINAL — MISMO criterio que el PBIP ("7  Sin
// especificar", siempre el índice más alto) — nunca primero, que es lo que
// `indexOf` (-1) produciría sin este guard.
function _frecuenciaCompare(a, b) {
  const ia = FRECUENCIA_ORDER.indexOf(a), ib = FRECUENCIA_ORDER.indexOf(b);
  return (ia === -1 ? FRECUENCIA_ORDER.length : ia) - (ib === -1 ? FRECUENCIA_ORDER.length : ib);
}
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
// Denominador REAL de "% Incidencia" — SOURCE-PARITY corrective (owner live
// comparison contra el PBI, ver capacidadProductivasTotal). `{anio: [horas
// x 12]}`, poblado por `loadBatchBData` (`data.horasProductivasDenominador`
// — ver `_shape_horas_productivas_denominador` en reporting_service.py).
// Objeto PLANO (no array, `replaceRows` no aplica) — se reemplaza entero en
// cada carga, MISMO criterio que `RENTABILIDAD_VENDIDAS_BY_YEAR`. `let`
// porque se reasigna, no se mutan sus keys in-place.
let CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR = {};

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
// Date(...).getFullYear()`: parsear un string de fecha como Date corre el
// riesgo de UTC shift de día/año cerca de fin de año en husos horarios
// negativos). "Hasta" SIEMPRE tiene un
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
  // Calendario/Capacidad quedan sin filtro de fecha (Todos) — ningún
  // corrective pidió uno ahí. Rentabilidad/Horas Vendidas/Participación
  // (Comercial) NO usan Desde/Hasta — migraron a Año/Mes/Estado (Rentabilidad/
  // HV) o Año/Estado (Participación, checkbox multi-select propio, ver
  // comercialParticipacionAnios abajo) — `filters.comercial` solo guarda
  // CP/Cliente.
  filters: { cartera: carteraDefaultFilters("Mantenimiento"), rentabilidad: {},
             horasvendidas: {}, calendario: {}, capacidad: {}, comercial: {} },
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
  // HOT WINDOW / COLD HISTORY corrective (ver HANDOFF): metadata de años
  // disponibles de Mantenimiento (año/estado, endpoint separado y liviano
  // — ver loadCarteraMantenimientoAnios) + qué año frío puntual está en
  // vuelo/falló (single-flight POR AÑO, `carteraMantenimientoYearLoading`
  // es un Set — más de un año nunca compite entre sí, aunque la UI actual
  // solo permite seleccionar uno a la vez).
  carteraMantenimientoAniosDisponibles: [],
  carteraMantenimientoAniosStatus: "idle",
  carteraMantenimientoYearLoading: new Set(),
  carteraMantenimientoYearError: "",
  // Rentabilidad (AMBAS perspectivas) — Batch B, ver loadRentabilidadData.
  // Fetch/status propio (Certificadas/Vendidas del backbone de Cartera +
  // Trabajadas/Ejecutadas del adapter de Time Logs, combinados en el
  // service — no comparte ciclo de vida con Batch A/B de las otras
  // experiencias). HOT WINDOW/COLD HISTORY (owner: "apply the SAME
  // temporal runtime principle to Rentabilidad") — MISMO patrón que Horas
  // Vendidas: ventana caliente (año actual + anterior) por defecto, año
  // frío lazy-load single-flight, metadata de años separada del fetch de
  // datos.
  rentabilidadDataStatus: "idle",
  rentabilidadAniosDisponibles: [],
  rentabilidadAniosStatus: "idle",
  rentabilidadYearLoading: new Set(),
  rentabilidadYearError: "",
  // Estado de Rentabilidad — checkbox-group propio (mismo patrón que
  // carteraStatus): default "En curso" únicamente (Finalizado disponible pero
  // no tildado de entrada). Migración Año/Mes/Estado (ver HANDOFF §-16 #8).
  rentabilidadStatus: { "En curso": true, "Finalizado": false },
  // Estado de Horas Vendidas — mismo patrón/pattern que Rentabilidad/Cartera.
  horasVendidasStatus: { "En curso": true, "Finalizado": false },
  // Horas Vendidas — ciclo de vida PROPIO con SCOPE TEMPORAL (owner directive:
  // "batch-a como frontera de runtime; visitar HV no debería cargar
  // Participación"). HV salió de `batchADataStatus`: pide su ventana caliente
  // (año actual + anterior) por su endpoint propio, y lazy-loadea un año FRÍO
  // al elegirlo (single-flight POR AÑO, mismo patrón que Cartera). Los rows ya
  // hidratados (HORASVENDIDAS_ROWS) SON la cache por scope en el cliente —
  // revisitar HV no re-fetchea (status queda ready).
  horasVendidasDataStatus: "idle",
  horasVendidasAniosDisponibles: [],
  horasVendidasAniosStatus: "idle",
  horasVendidasYearLoading: new Set(),
  horasVendidasYearError: "",
  // Backbone reuse de Cartera (Calendario/Salud de Cliente, ver HANDOFF):
  // MISMO ciclo de vida, UN solo status para las 2 FULL-HISTORY categóricas
  // que quedan (comparten UN fetch/endpoint consolidado — ver
  // loadBatchAData). Horas Vendidas Y Participación ya NO están acá (ver
  // horasVendidasDataStatus/comercialParticipacionDataStatus abajo). Horas
  // Productivas usa su PROPIO status (`batchBDataStatus`) — fuente CSV.
  batchADataStatus: "idle",
  // Estado de Participación — mismo patrón/pattern que Rentabilidad/HV.
  comercialStatus: { "En curso": true, "Finalizado": false },
  // Participación — ciclo de vida PROPIO con SCOPE TEMPORAL (owner directive:
  // "Do NOT keep Participación on full-history simply because old batch-a
  // did" — MISMO principio que Horas Vendidas/Rentabilidad). Pide su ventana
  // caliente (año actual + anterior) por su endpoint propio; un año FRÍO se
  // lazy-loadea al tildarlo (Año es MULTI-SELECT acá, a diferencia del
  // single-select de HV/Rentabilidad — puede haber VARIOS años frios
  // pendientes a la vez, `loadComercialParticipacionColdYears` los bundlea
  // en UN solo request `anios=y1,y2`, ver reporting.py).
  comercialParticipacionDataStatus: "idle",
  comercialParticipacionAniosDisponibles: [],
  comercialParticipacionAniosStatus: "idle",
  // Años tildados (Set) — arranca vacío, se puebla con la ventana caliente
  // REAL recién en `loadComercialParticipacionData` (nunca con un año
  // "adivinado" antes de que lleguen los datos, MISMO criterio que
  // rentabilidadDefaultAnio/horasVendidasDefaultAnio, aplicados post-fetch).
  comercialParticipacionAnios: new Set(),
  // Años YA hidratados — set EXPLÍCITO (a diferencia de Cartera/HV, que lo
  // derivan de las filas cargadas): Participación mantiene una fila por
  // Cliente SIEMPRE (ver `_shape_participacion`, población = `cliente_dim`
  // completa) — un año sin NINGÚN hecho real en NINGÚN cliente dejaría
  // `vendidasByYear`/`certificadasByYear` sin esa key en NINGUNA fila,
  // haciendo indistinguible "año hidratado sin datos" de "año todavía no
  // pedido" si se derivara de las filas (reintentaría el fetch en cada
  // render). Marcado explícito evita esa ambigüedad.
  comercialParticipacionLoadedYears: new Set(),
  comercialParticipacionYearLoading: new Set(),
  comercialParticipacionYearErrors: new Set(),
  // Headers clickeables de % Participación (owner request — MISMO patrón
  // que Horas/% Incidencia de Horas Productivas, ver `capacidadProductivas
  // SortDir`). A diferencia de ese caso, Vendidas% y Certificadas% NO son
  // transform monótono una de la otra (numeradores distintos) — cada
  // columna necesita su PROPIA clave activa, no una dirección compartida
  // ciega. `null` = sin orden explícito (orden de jerarquía/inserción de
  // siempre); clickear una columna la vuelve la clave activa en "desc".
  comercialParticipacionSortKey: null,
  comercialParticipacionSortDir: "desc",
  // Horas Productivas (AMBAS perspectivas) — Batch B, CSV de Time Logs
  // Reports vía `loadBatchBData`. MISMO ciclo de vida idle→loading→
  // ready|error que Batch A, endpoint/fetch propio (combinar CSV es costo
  // real, no se comparte con el backbone de Cartera).
  batchBDataStatus: "idle",
  // Mes de Horas Productivas — MULTI-SELECT (owner live QA: el PBIP
  // original permite tildar VARIOS meses a la vez sobre el visual, un
  // `<select>` de valor único no lo representa — MISMO principio que Año de
  // Inicio de Cartera, ver `carteraAnioInicio`). Fuera de `state.filters`
  // (igual que `carteraAnioInicio`) — un Set por panel, cada uno
  // independiente (mismo criterio panel-independiente que Año/Mes, ver
  // `capacidadPanelAnioKey`). Arranca vacío = "Todos" (ver `_maskMeses`) —
  // preserva el default histórico "Mes: Todos" del `<select>` retirado, a
  // diferencia de Año de Inicio/Estado (esos SÍ arrancan con una selección
  // default no vacía porque ahí vacío significa "nada", nunca "todo").
  capacidadMesesSeleccionados: { cliente: new Set(), clientePart: new Set() },
  // Estado de Horas Productivas — SOURCE-PARITY CORRECTIVE (owner live
  // comparison contra el PBI original): a diferencia de carteraStatus/
  // rentabilidadStatus (default SOLO "En curso" — filtros NUEVOS que el
  // PBIP original no tenía), acá el default es AMBOS tildados. El PBIP
  // original de Horas Productivas NO EXPONE un filtro de Estado en
  // absoluto — su baseline natural incluye TODOS los proyectos sin
  // importar status. Estado es una extensión Hub sobre ese reporte
  // (ver §9 del corrective — Status viene de `Proyectos.Status`
  // ACTUAL, aplicado retroactivamente sobre Time Logs históricos; el
  // owner la aprobó como extensión, no como reinterpretación del
  // reporte original), así que el default debe ser NEUTRAL respecto al
  // baseline del PBIP — ambos tildados reproduce exactamente "sin
  // filtro de Estado". Default distinto SOLO para Productivas — Cartera/
  // Rentabilidad/HV/Participación NO cambian (piden explícitamente "En
  // curso" como default operativo, ver sus propios comentarios).
  // Escopea la POBLACIÓN real (como Año/Mes, ver
  // capacidadProductivasSelectedRows) — nunca una dimensión REMOVEFILTERS
  // como Cliente/Proyecto — así que también participa de qué Años
  // aparecen como opción (capacidadProductivasAnioOptions). CAUSA RAÍZ
  // del bug "el Año dropdown solo ofrece 2026" (owner corrective): con el
  // default previo (solo "En curso"), los Time Logs históricos —
  // mayormente de proyectos YA "Finalizado" a esta altura — quedaban
  // excluidos de la población, y con ellos, sus años. Ambos tildados
  // restaura la historia completa sin ningún cambio adicional en
  // capacidadProductivasAnioOptions (esa función ya sigue el invariante
  // canónico correcto — el bug vivía acá, no ahí).
  capacidadStatus: { "En curso": true, "Finalizado": true },
  // Orden de Horas Productivas — CLICKEABLE en los headers Horas/%
  // Incidencia (owner live QA). % Incidencia es un transform MONÓTONO de
  // Horas contra el total FIJO del panel (`pct = horas/total*100`) — el
  // MISMO orden relativo resulta de ordenar por cualquiera de las dos
  // columnas, así que UN solo estado de dirección por panel alcanza (nunca
  // dos sorts independientes que puedan divergir). Default "desc" preserva
  // el Rank original (RANKX del PBIP, ya descendente) sin que el usuario
  // tenga que tocar nada.
  capacidadProductivasSortDir: { cliente: "desc", clientePart: "desc" },
  // Ranking Mejor/Peor Performance de Rentabilidad (owner feedback): Top 5
  // por default, con una afordancia liviana para revelar el resto — UI
  // pura, nunca dispara un fetch (los datos YA están cargados). Reset
  // implícito al recargar la página, mismo criterio que `treeExpanded`.
  rentabilidadRankingExpanded: { positivos: false, negativos: false },
  // SHARED REPORTING INITIAL-LOAD CONTRACT (owner corrective): flag de UNA
  // SOLA VÍA por tab principal — "esta pestaña mostró contenido usable
  // alguna vez". Se prende la PRIMERA vez que `reportingTabRawStatus(expId)`
  // ve "ready" (ver `render()`) y NUNCA se apaga después — ni al cambiar a
  // una perspectiva individualmente todavía no lista (Cartera/Proyecto,
  // Comercial/Salud), ni por un error incremental posterior (año frío
  // fallido). DISTINTO del status crudo por-perspectiva, que SÍ puede
  // volver a "loading" — necesario para que un cambio de perspectiva
  // dentro de un tab YA hidratado nunca vuelva a mostrar el modo
  // "solo tabs + loader inicial".
  reportingTabHydrated: { cartera: false, rentabilidad: false, horasvendidas: false, comercial: false, calendario: false, capacidad: false },
  // Feedback "¿Qué te pareció este reporte?" por MAIN TAB (ver
  // reportingFeedbackHtml): undefined | "sending" | "up" | "down" | "error".
  reportingFeedback: {},
};
// Año de Inicio default — se resuelve DESPUÉS del literal porque
// carteraDefaultAnioInicio() lee `state` (carteraStatus/filters).
state.carteraAnioInicio = carteraDefaultAnioInicio();

function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

// HUB TRANSVERSAL PRIMITIVE TEST (owner directive): Copilot renderiza
// `{{callout:...}}...{{/callout}}` desde Markdown vía `content_service.py::
// callout()` — ese parser es 100% de Copilot y NO se toca ni se mueve acá.
// Lo que SÍ es un primitivo genérico de presentación compartido es el HTML/
// CSS resultante (`.callout`/`.callout__head`/`.callout__icon`/
// `.callout__title`/`.callout__body`, ya movido a tools.css — ver comentario
// ahí). `renderHubCallout` arma esa MISMA forma directo desde datos
// estructurados (nunca Markdown) — Reporting no necesita fingir que su
// contenido vino de un parser de texto solo para reusar la presentación.
// `bodyHtml` ya viene armado (nunca markdown crudo) — el caller decide su
// propio marcado (memorable: acá SIEMPRE texto plano vía `esc`, ningún
// experience de Reporting necesita bullets/links dentro del callout todavía).
function renderHubCallout({ tone = "note", icon = "", title = "", bodyHtml = "" }) {
  return `<aside class="callout callout--${esc(tone)}">
    <div class="callout__head">${icon ? `<span class="callout__icon">${esc(icon)}</span>` : ""}<strong class="callout__title">${esc(title)}</strong></div>
    <div class="callout__body">${bodyHtml}</div>
  </aside>`;
}

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
  // hours3 (owner request — Horas Productivas ÚNICAMENTE, ver
  // capacidadProductivasColumns/renderCapacidadProductivas): PBIP muestra
  // más precisión que el resto del Hub (5.422 vs 5.4) — nuevo tipo
  // semántico en vez de tocar "hours" (usado por Cartera/Rentabilidad/
  // Horas Vendidas/Participación, que deben seguir en 1 decimal).
  hours3: new Intl.NumberFormat("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 3 }),
  percent: new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
};
function fmtNum(value, type = "hours") {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!isFinite(n)) return "—";
  if (type === "count") return _NF.count.format(Math.round(n));
  if (type === "percent") return _NF.percent.format(n) + "%";
  if (type === "hours3") return _NF.hours3.format(n);
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
// `smartAccent` (SMART KPI TILE ACCENT OVERRIDE): capa OPCIONAL de override
// semántico del accent/borde de la tile. Default = derivado de la metadata
// semántica ya existente (`format === "signed"` = KPI firmado bueno/malo, p. ej.
// Rentabilidad horas / Diff HS) — el caller puede forzarlo con `smartAccent:
// true/false`. Cuando aplica, el accent refleja el SIGNO del valor CRUDO (nunca
// el texto renderizado): >0 verde, <0 rojo, 0/null/no-finito conserva el accent
// por defecto (patrón nth-child o el `accent` explícito). KPIs neutros (hours/
// count/totales) usan `format` no-"signed" ⇒ nunca se overridean. Lógica ÚNICA
// acá (primitivo compartido), nunca duplicada por experiencia.
function renderDashboardKpi({ label, value, format = "hours", accent, smartAccent }) {
  const display = fmtNum(value, format);
  const n = typeof value === "number" ? value : Number(value);
  let valueClass = "activity-kpi__value";
  if (format === "signed") {
    if (isFinite(n) && n > 0) valueClass += " diff-pos";
    else if (isFinite(n) && n < 0) valueClass += " diff-neg";
  }
  const semanticSigned = smartAccent === undefined ? (format === "signed") : smartAccent;
  let effectiveAccent = accent;
  if (semanticSigned && isFinite(n) && n !== 0) {
    effectiveAccent = n > 0 ? "positive" : "negative";
  }
  const cardClass = effectiveAccent ? `activity-kpi activity-kpi--${effectiveAccent}` : "activity-kpi";
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
    // grano MENSUAL real de la fuente): Año y Mes usan `predicate`
    // (igualdad exacta) — AMBOS quedan FUERA de la reconciliación genérica
    // (HOT WINDOW / COLD HISTORY corrective, ver HANDOFF: Año dejó de ser
    // reconciliable contra `baseRows` porque con la ventana caliente por
    // defecto esas filas NUNCA incluyen años fríos — reconciliarlo acá lo
    // podría a "Todos" apenas el usuario elige un año que todavía no
    // hidrató, exactamente lo que "DO NOT CONFUSE OPTIONS WITH HYDRATED
    // FACTS" prohíbe). Año tiene su PROPIA reconciliación
    // (`pruneCarteraAnio`, ver `reconcileExperienceFacets` — metadata-
    // driven, no rows-driven) — el `predicate` de acá SIGUE aplicando el
    // filtro real a la tabla (igual que siempre), solo cambia de dónde
    // salen las OPCIONES del <select> (ver `carteraAnioField`/
    // `carteraAnioOptions`). Mes sigue exento por el motivo de siempre: el
    // <select> de Mes SIEMPRE ofrece los 12 meses fijos (ver
    // carteraMesField), nunca se poda por falta de datos ese mes. Proyecto
    // NO agrega un facet de Año de Inicio acá (se convirtió en checkbox
    // multi-select, ver arriba — ya no es un <select> de valor único).
    const dateFields = persp === "Mantenimiento"
      ? [
          { key: "anio", field: "anio", predicate: (anio, selected) => anio === selected },
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
    // baseRows escopeada por Estado (checkbox-group, fuera del motor <select>)
    // para que las opciones de CP/Cliente/Tipo/Proyecto respeten Estado. Año/Mes
    // se manejan aparte (ver rentabilidadAnioOptions / carteraMesField-style).
    baseRows: RENTABILIDAD_PROYECTOS.filter(p => state.rentabilidadStatus[p.estado]),
    fields: [
      { key: "cp", field: "cp" }, { key: "cliente", field: "cliente" },
      { key: "tipo", field: "tipo" }, { key: "proyecto", field: "proyecto" },
    ],
  }),
  horasvendidas: () => ({
    baseRows: HORASVENDIDAS_ROWS.filter(r => state.horasVendidasStatus[r.estado]),
    fields: [
      { key: "cp", field: "cp" }, { key: "tipo", field: "tipo" }, { key: "cliente", field: "cliente" }, { key: "proyecto", field: "proyecto" },
    ],
  }),
  calendario: () => ({
    baseRows: CALENDARIO_ROWS,
    // Frecuencia agregado (owner feedback, ver HANDOFF) — orden real de
    // negocio (FRECUENCIA_ORDER), nunca alfabético.
    fields: [
      { key: "cp", field: "cp" }, { key: "cliente", field: "cliente" },
      { key: "frecuencia", field: "frecuencia", compareFn: _frecuenciaCompare },
    ],
  }),
  // Productivas: Cliente/Proyecto viven en este motor genérico de <select>
  // (owner corrective — Proyecto agregado, faceteado contra Cliente:
  // elegir un Cliente narrows las opciones de Proyecto a las de ESE
  // cliente, MISMO motor multi-direccional que Cartera/Rentabilidad, cero
  // código nuevo). `baseRows` escopeada por Estado (checkbox-group, fuera
  // de este motor — MISMO criterio que Cartera/Rentabilidad) para que las
  // opciones de Cliente/Proyecto respeten Estado. Año/Mes son PANEL-
  // INDEPENDIENTES (owner corrective: dos pares de slicers en el PBIP
  // original, ver §8ter item 1) y se aplican DENTRO de
  // renderCapacidadProductivas vía capacidadProductivasSelectedRows(panel),
  // nunca acá (no hay un único "baseRows" Año/Mes-escopeado compartido por
  // ambos paneles).
  capacidad: () => state.perspective.capacidad === "Productivas"
    ? { baseRows: CAPACIDAD_PRODUCTIVAS_ROWS.filter(r => state.capacidadStatus[r.estado]), fields: [{ key: "cliente", field: "cliente" }, { key: "proyecto", field: "proyecto" }] }
    : { baseRows: CAPACIDAD_NOASIGNADAS_ROWS, fields: [{ key: "consultor", field: "consultor" }] },
  // Client Partner/Cliente son los ÚNICOS slicers <select> de ambas páginas
  // fuente. Participación: Año/Estado (owner directive — "Do NOT keep
  // Participación on full-history", reusar la infraestructura temporal de
  // HV/Rentabilidad) son checkbox multi-select FUERA de este motor, ver
  // `comercialParticipacionSelectedRows` — Desde/Hasta (full-history) se
  // RETIRÓ, superado por Año+Estado. Salud de Cliente: sin dimensión de
  // fecha propia (columnas categóricas puras), no se inventa una.
  // Frecuencia (owner request, Participación ÚNICAMENTE — Salud de Cliente
  // no tiene ese campo en sus filas, ver COMERCIAL_SALUD_ROWS): orden real
  // de negocio (`_frecuenciaCompare`, MISMO criterio que Calendario), nunca
  // alfabético.
  comercial: () => state.perspective.comercial === "Participación"
    ? { baseRows: comercialParticipacionSelectedRows(), fields: [
        { key: "cp", field: "cp" }, { key: "cliente", field: "cliente" },
        { key: "frecuencia", field: "idFrecuencia", compareFn: _frecuenciaCompare },
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
  // Año (Cartera/Mantenimiento, HOT WINDOW/COLD HISTORY corrective) usa su
  // propia reconciliación metadata-driven (ver pruneCarteraAnio) por el
  // mismo motivo — exento del motor genérico vía `predicate`.
  if (expId === "cartera") { pruneCarteraAnioInicio(); pruneCarteraAnio(); }
  // Rentabilidad/Horas Vendidas: Año se reconcilia PRIMERO (puede cambiar
  // el año activo), Mes SEGUNDO (su validez — CURRENT-YEAR FUTURE-MONTH
  // CAP, ver TEMPORAL DOMAIN CONTRACT — depende del Año YA reconciliado).
  if (expId === "rentabilidad") { pruneRentabilidadAnio(); pruneRentabilidadMes(); }
  if (expId === "horasvendidas") { pruneHorasVendidasAnio(); pruneHorasVendidasMes(); }
  // Participación: Año es checkbox multi-select (como Año de Inicio de
  // Cartera/Proyecto) — se reconcilia con el MISMO criterio faceteado.
  if (expId === "comercial") pruneComercialParticipacionAnios();
  // Capacidad/Productivas: Año/Mes PANEL-INDEPENDIENTE (owner corrective —
  // ver bloque `CAPACIDAD_PRODUCTIVAS_PANELS`) — se reconcilian los DOS
  // paneles, cada uno contra su PROPIA key, nunca una selección del otro
  // panel. No-op en No Asignadas (sin filtro.anio/mes ahí).
  if (expId === "capacidad" && state.perspective.capacidad === "Productivas") {
    CAPACIDAD_PRODUCTIVAS_PANELS.forEach(panel => {
      pruneCapacidadProductivasAnio(panel);
      pruneCapacidadProductivasMeses(panel);
    });
  }
}
// Mes queda fuera del año en curso tras un cambio de Año (ej. Mes=Octubre
// bajo 2025, usuario cambia Año a 2026 en Agosto-2026 ⇒ Octubre ya no es
// una opción válida) — cae a "" (Todos), NUNCA queda un mes futuro
// seleccionado sin opción visible que lo respalde. El PIPELINE de datos ya
// es seguro sin esto (`_applyMonthCap` capa igual), esto es solo higiene
// de estado para que el <select> nunca muestre una selección fantasma.
function pruneRentabilidadMes() {
  const anio = getFilter("rentabilidad", "anio");
  const mes = getFilter("rentabilidad", "mes");
  if (!mes) return;
  if (!_validMesOptions(anio).some(m => m.value === mes)) state.filters.rentabilidad.mes = "";
}
function pruneHorasVendidasMes() {
  const anio = getFilter("horasvendidas", "anio");
  const mes = getFilter("horasvendidas", "mes");
  if (!mes) return;
  if (!_validMesOptions(anio).some(m => m.value === mes)) state.filters.horasvendidas.mes = "";
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
  reportingTelemetry("reporting.filter_change", { report_tab: expId, filter_name: key });
  // `key` es el facet que el usuario ELIGIÓ recién — nunca se resetea a sí
  // mismo (ver reconcileFacetFilters); si entra en conflicto con una
  // selección vieja de otro facet, la vieja es la que cae a Todos.
  reconcileExperienceFacets(expId, key);
  render();
}
// Experiencias con default de fecha SENSATO (año en curso a la fecha) en
// vez de blanco — mismo criterio ya establecido para Cartera/Estado.
// "rentabilidad"/"horasvendidas"/"comercial" SALIERON de este Set (migración
// Año/Mes/Estado — Participación converge al mismo mecanismo scoped, owner
// directive: "Do NOT keep Participación on full-history") — ya NO tienen
// Desde/Hasta, "Limpiar" las resetea explícito abajo (Año(es) + Estado En
// curso, MISMO criterio que Cartera).
const YEAR_TO_DATE_EXPERIENCES = new Set();
function resetFilters(expId) {
  reportingTelemetry("reporting.filter_clear", { report_tab: expId });
  if (expId === "cartera") {
    // "Limpiar" vuelve al default SENSATO (año/mes actual, Estado = En
    // curso, Año de Inicio = año en curso si está representado), no a
    // blanco — mismo criterio ya establecido para Estado.
    state.filters.cartera = carteraDefaultFilters(state.perspective.cartera);
    state.carteraStatus = { "En curso": true, "Finalizado": false };
    state.carteraAnioInicio = carteraDefaultAnioInicio();
  } else if (expId === "rentabilidad") {
    // TEMPORAL DOMAIN CONTRACT: default SENSATO (año actual válido + Mes
    // Todos + Estado En curso), NUNCA Desde/Hasta (retirado) ni blanco.
    state.rentabilidadStatus = { "En curso": true, "Finalizado": false };
    state.filters.rentabilidad = { anio: rentabilidadDefaultAnio(), mes: "" };
  } else if (expId === "horasvendidas") {
    state.horasVendidasStatus = { "En curso": true, "Finalizado": false };
    state.filters.horasvendidas = { anio: horasVendidasDefaultAnio(), mes: "" };
  } else if (expId === "comercial") {
    // Default SENSATO (ventana caliente tildada + Estado En curso), NUNCA
    // Desde/Hasta (retirado) ni blanco — MISMO criterio que Rentabilidad/HV.
    state.comercialStatus = { "En curso": true, "Finalizado": false };
    state.filters.comercial = {};
    state.comercialParticipacionAnios = comercialParticipacionDefaultAnios();
  } else if (expId === "capacidad") {
    // Mes de Horas Productivas vive FUERA de `filters` (Set por panel, ver
    // `capacidadMesesSeleccionados`) — "Limpiar" lo vuelve a vacío (==
    // "Todos"). Estado vuelve a AMBOS tildados (SOURCE-PARITY corrective —
    // ver comentario en `state` arriba: el PBIP original no tiene filtro
    // de Estado, "Limpiar" debe reproducir ESE baseline neutral, nunca el
    // criterio "En curso únicamente" de Cartera/Rentabilidad, que SÍ son
    // filtros nuevos con default operativo propio). Año — owner
    // corrective — vuelve al default SENSATO (año actual válido) de CADA
    // panel, nunca a blanco: se reasigna DESPUÉS de vaciar
    // `filters.capacidad` (capacidadProductivasDefaultAnio lee
    // `capacidadProductivasAnioOptions`, que a su vez lee
    // `state.capacidadStatus` — debe estar ya reseteado para que el
    // default salga del universo correcto).
    state.filters.capacidad = {};
    state.capacidadMesesSeleccionados = { cliente: new Set(), clientePart: new Set() };
    state.capacidadStatus = { "En curso": true, "Finalizado": true };
    CAPACIDAD_PRODUCTIVAS_PANELS.forEach(panel => {
      state.filters.capacidad[capacidadPanelAnioKey(panel)] = capacidadProductivasDefaultAnio();
    });
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
// Año de Cartera/Mantenimiento (HOT WINDOW / COLD HISTORY corrective, ver
// HANDOFF): opciones vía `carteraAnioOptions()` (metadata + filas
// cargadas, NUNCA solo `CARTERA_MANTENIMIENTO_ROWS` — ver comentario ahí)
// en vez de `facetSelectField`, para que un año frío siga siendo
// seleccionable aunque sus filas todavía no se hayan hidratado. Elegir un
// año frío dispara su lazy-load (ver renderCartera/
// loadCarteraMantenimientoColdYear) — este <select> en sí no sabe ni le
// importa si el año elegido ya está tibio o no, solo expone la opción.
function carteraAnioField() {
  const current = getFilter("cartera", "anio");
  const options = carteraAnioOptions();
  return `<label class="whatsnew-filter whatsnew-filter--narrow">
    <span>Año</span>
    <select onchange="setFilter('cartera','anio', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${options.map(a => `<option value="${esc(a)}" ${current === a ? "selected" : ""}>${esc(a)}</option>`).join("")}
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
  return `<label class="whatsnew-filter whatsnew-filter--narrow">
    <span>Mes</span>
    <select onchange="setFilter('cartera','mes', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${MESES_LARGOS.map((nombre, i) => `<option value="${i + 1}" ${current === String(i + 1) ? "selected" : ""}>${esc(nombre)}</option>`).join("")}
    </select>
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
  //
  // "↻ Actualizar datos" (EXPLICIT REFRESH corrective, sesión anterior) —
  // RETIRADO por feedback directo del owner: el "Actualizar↗" existente
  // (navega a SharePoint) ya alcanza, el botón extra rompía el layout de
  // acciones (empujaba a una segunda fila) y resultaba disruptivo sin
  // aportar valor suficiente. Todo el mecanismo de invalidación por-tab que
  // ese botón disparaba se retiró entero (código muerto sin él, ver
  // test_reporting_main_tab_hydration.py) — el refresh de datos reales
  // sigue disponible implícitamente al recargar la página (comportamiento
  // de siempre antes de ese corrective).
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
  // INITIAL HYDRATION CONTRACT (owner): SOLO la PRIMERA hidratación puede
  // ocultar la barra de filtros. Una vez que el tab hidrató
  // (`reportingTabHydrated.cartera`), conmutar a una perspectiva hermana cuyo
  // dataset todavía carga NUNCA vuelve a ocultar los filtros/acciones — la
  // barra permanece y el CONTENIDO muestra su loader contextual. Antes gateaba
  // SOLO por `carteraStatusFor(persp) === "ready"`, lo que la escondía al
  // conmutar a una perspectiva en carga aun después de hidratar.
  if (!state.reportingTabHydrated.cartera && carteraStatusFor(persp) !== "ready") return "";
  // Mantenimiento (UX1, owner live QA): Año (facet <select> normal,
  // data-driven) + Mes (fijo, 12 meses siempre — ver carteraMesField).
  // Año de Inicio (Proyecto, owner feedback ver HANDOFF): el slicer real
  // permite VARIOS años tildados a la vez — checkbox-group DINÁMICO (años
  // realmente presentes en el dataset), no un <select> de valor único.
  const dateFields = persp === "Mantenimiento"
    ? carteraAnioField() + carteraMesField()
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

// Año de Rentabilidad — single-select faceteado (mismo patrón que
// carteraAnioField); "Todos" = vista colapsada de todos los años.
// Año — SINGLE-SELECT, NUNCA "Todos" (owner: "Año as a SINGLE YEAR
// selector — 'Todos' belongs to Mes, not Año", ver TEMPORAL DOMAIN
// CONTRACT). `options` YA viene filtrado a /^\d{4}$/ (rentabilidadAnioOptions).
function rentabilidadAnioField() {
  const current = getFilter("rentabilidad", "anio");
  const options = rentabilidadAnioOptions();
  return `<label class="whatsnew-filter whatsnew-filter--narrow"><span>Año</span>
    <select onchange="setFilter('rentabilidad','anio', this.value)">
      ${options.map(a => `<option value="${esc(a)}" ${current === a ? "selected" : ""}>${esc(a)}</option>`).join("")}
    </select></label>`;
}
// Mes — Todos + 12 fijos, PERO domain-aware del CURRENT-YEAR FUTURE-MONTH
// CAP (owner: "available individual months should reflect actual valid
// operational months" — nunca ofrecer Septiembre como mes seleccionable
// en Agosto-2026 bajo el año en curso). Año histórico: los 12 completos,
// sin cap (`_validMesOptions`, TEMPORAL DOMAIN CONTRACT).
function rentabilidadMesField() {
  const current = getFilter("rentabilidad", "mes");
  const anio = getFilter("rentabilidad", "anio");
  return `<label class="whatsnew-filter whatsnew-filter--narrow"><span>Mes</span>
    <select onchange="setFilter('rentabilidad','mes', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${_validMesOptions(anio).map(m => `<option value="${m.value}" ${current === m.value ? "selected" : ""}>${esc(m.label)}</option>`).join("")}
    </select></label>`;
}
function renderRentabilidadFilterBar() {
  const fields = facetSelectField("rentabilidad", "cp", "Client Partner")
    + facetSelectField("rentabilidad", "cliente", "Cliente")
    + facetSelectField("rentabilidad", "tipo", "Tipo Proyecto")
    + facetSelectField("rentabilidad", "proyecto", "Proyecto")
    + rentabilidadAnioField()
    + rentabilidadMesField()
    + checkboxGroupField("Estado", [
        { value: "En curso", checked: state.rentabilidadStatus["En curso"], onclick: "toggleRentabilidadStatus('En curso')" },
        { value: "Finalizado", checked: state.rentabilidadStatus["Finalizado"], onclick: "toggleRentabilidadStatus('Finalizado')" },
      ]);
  return filterBarShell(fields, "rentabilidad");
}

// Año — SINGLE-SELECT, NUNCA "Todos" (MISMO contrato que Rentabilidad,
// ver TEMPORAL DOMAIN CONTRACT).
function horasVendidasAnioField() {
  const current = getFilter("horasvendidas", "anio");
  const options = horasVendidasAnioOptions();
  return `<label class="whatsnew-filter whatsnew-filter--narrow"><span>Año</span>
    <select onchange="setFilter('horasvendidas','anio', this.value)">
      ${options.map(a => `<option value="${esc(a)}" ${current === a ? "selected" : ""}>${esc(a)}</option>`).join("")}
    </select></label>`;
}
// Mes — MISMO contrato domain-aware que Rentabilidad (`_validMesOptions`).
function horasVendidasMesField() {
  const current = getFilter("horasvendidas", "mes");
  const anio = getFilter("horasvendidas", "anio");
  return `<label class="whatsnew-filter whatsnew-filter--narrow"><span>Mes</span>
    <select onchange="setFilter('horasvendidas','mes', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${_validMesOptions(anio).map(m => `<option value="${m.value}" ${current === m.value ? "selected" : ""}>${esc(m.label)}</option>`).join("")}
    </select></label>`;
}
function renderHorasVendidasFilterBar() {
  const fields = facetSelectField("horasvendidas", "cp", "Client Partner")
    + facetSelectField("horasvendidas", "tipo", "Tipo Proyecto")
    + facetSelectField("horasvendidas", "cliente", "Cliente")
    + facetSelectField("horasvendidas", "proyecto", "Proyecto")
    + horasVendidasAnioField()
    + horasVendidasMesField()
    + checkboxGroupField("Estado", [
        { value: "En curso", checked: state.horasVendidasStatus["En curso"], onclick: "toggleHorasVendidasStatus('En curso')" },
        { value: "Finalizado", checked: state.horasVendidasStatus["Finalizado"], onclick: "toggleHorasVendidasStatus('Finalizado')" },
      ]);
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
// Año/Mes de Capacidad/Productivas: PANEL-INDEPENDIENTE (owner corrective —
// ver `CAPACIDAD_PRODUCTIVAS_PANELS`/`capacidadProductivasAnioField(panel)`
// más abajo, cerca de `renderCapacidadProductivas`) — NUNCA en este filter
// bar compartido, cada panel monta su PROPIO Año/Mes en su propio toolbar
// (posición fiel al PBIP original: dos pares de slicers independientes, uno
// sobre cada visual, ver real-data-lineage.md §8ter item 1).
function renderCapacidadFilterBar() {
  const persp = state.perspective.capacidad;
  // Owner corrective — filtros faltantes en Productivas: Proyecto a la
  // derecha de Cliente (faceteado — elegir un Cliente narrows Proyecto a
  // los de ESE cliente, MISMO motor multi-direccional que Cartera, ver
  // FACET_CONFIG.capacidad), Estado a la derecha de Proyecto (checkbox-
  // group, MISMO criterio que Cartera/Rentabilidad — filtra la población
  // real Y qué Años aparecen como opción, ver capacidadProductivasAnioOptions).
  const fields = persp === "Productivas"
    ? facetSelectField("capacidad", "cliente", "Cliente")
      + facetSelectField("capacidad", "proyecto", "Proyecto")
      + checkboxGroupField("Estado", [
          { value: "En curso", checked: state.capacidadStatus["En curso"], onclick: "toggleCapacidadStatus('En curso')" },
          { value: "Finalizado", checked: state.capacidadStatus["Finalizado"], onclick: "toggleCapacidadStatus('Finalizado')" },
        ])
    : facetSelectField("capacidad", "consultor", "Consultor");
  return filterBarShell(fields, "capacidad");
}

// Año/Estado (owner directive — "Do NOT keep Participación on full-history",
// checkbox multi-select propio, MISMO criterio que Año de Inicio/Status de
// Cartera) SOLO en Participación — Salud de Cliente no tiene grano temporal
// en el dataset (columnas categóricas puras, ver comentario de COMERCIAL_
// SALUD_ROWS), no se inventa uno.
function renderComercialFilterBar() {
  const persp = state.perspective.comercial;
  const temporalFields = persp === "Participación"
    ? checkboxGroupField("Año", comercialParticipacionAnioOptions().map(a =>
        ({ value: a, checked: state.comercialParticipacionAnios.has(a), onclick: `toggleComercialParticipacionAnio('${a}')` })))
      + checkboxGroupField("Estado", [
          { value: "En curso", checked: state.comercialStatus["En curso"], onclick: "toggleComercialStatus('En curso')" },
          { value: "Finalizado", checked: state.comercialStatus["Finalizado"], onclick: "toggleComercialStatus('Finalizado')" },
        ])
    : "";
  // Frecuencia (owner request — "agreguemos frecuencia como filtro también
  // al lado de cliente y antes de año"): SOLO Participación, el facet
  // "frecuencia" no existe en el FACET_CONFIG de Salud de Cliente (esas
  // filas no tienen ese campo, ver COMERCIAL_SALUD_ROWS) — llamar
  // facetSelectField acá para Salud rompería contra un field inexistente.
  const frecuenciaField = persp === "Participación" ? facetSelectField("comercial", "frecuencia", "Frecuencia") : "";
  const fields = facetSelectField("comercial", "cp", "Client Partner")
    + facetSelectField("comercial", "cliente", "Cliente")
    + frecuenciaField
    + temporalFields;
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
  // PERSPECTIVE-LOCAL FILTER STATE corrective (owner live QA — Mantenimiento
  // perdía Año/Mes al ir y volver de Proyecto): cambiar de perspectiva
  // NUNCA reinicializa/toca `state.filters[expId]` — los defaults corren
  // SOLO en la inicialización real de `state` (arriba) o en "Limpiar"
  // (`resetFilters`, acción explícita del usuario), nunca en cada
  // `setPerspective`. Cada perspectiva de una experiencia usa SUS PROPIAS
  // keys de filtro (Cartera: anio/mes de Mantenimiento vs. cp/proyecto/
  // cliente compartidos; Capacidad: cliente de Productivas vs. consultor de
  // No Asignadas) — conviven en el MISMO objeto `state.filters[expId]` sin
  // colisión porque `FACET_CONFIG` ya selecciona los `fields` correctos
  // según la perspectiva activa (ver FACET_CONFIG.cartera/capacidad/
  // comercial); una key que no aplica a la vista actual simplemente queda
  // sin usar hasta que la perspectiva vuelve a necesitarla — exactamente el
  // comportamiento que el owner pidió.
  state.perspective[expId] = p;
  reportingTelemetry("reporting.perspective_change", { report_tab: expId, perspective: p });
  render();
}

function setExperience(id) {
  state.experience = id;
  // Activity (Hub pipeline): activación de main tab. El contexto operations/
  // reporting lo fija el servidor (ver reporting_telemetry) — acá solo el hecho.
  reportingTelemetry("reporting.tab_view", { report_tab: id });
  render();
}

// --- Activity / telemetría de Reporting (beacon liviano, MISMO patrón que
// app.js::sendTelemetry) — el servidor (reporting_telemetry) whitelistea el
// evento y fija scope=operations/area=reporting; el cliente NUNCA fija el
// contexto de plataforma. Semántico, nunca "cada click del DOM". Solo se
// persiste en LOG_ACTIVITY=VERBOSE (gate server-side). ---------------------
function reportingTelemetry(event, payload) {
  try {
    const body = JSON.stringify({ event, ...(payload || {}) });
    const url = "/operaciones/reporting/integral/telemetry";
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    } else {
      fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
    }
  } catch (e) { /* la telemetría nunca rompe la UI */ }
}

// --- Feedback "¿Qué te pareció este reporte?" (Shared Hub Feedback, ver
// ticket §A) — UN control por MAIN TAB, SIEMPRE el último elemento del tab.
// Reusa la capacidad compartida vía POST JSON a /feedback (mismo store +
// mismo pipeline de Activity que Copilot); no es un almacenamiento propio de
// Reporting. Estado por tab: undefined | "sending" | "up" | "down" | "error".
const REPORT_TAB_TITLES = {
  cartera: "Cartera / Proyectos", rentabilidad: "Rentabilidad", horasvendidas: "Horas Vendidas",
  comercial: "Participación / Comercial", calendario: "Calendario", capacidad: "Horas Productivas",
};
function reportingFeedbackHtml(expId) {
  // Reusa la MISMA botonera + formulario compartidos que el feedback de sección
  // del Copiloto (clases `.section-feedback*` de tools.css): discreta, ALINEADA
  // A LA DERECHA, sin línea divisoria, opacity .4→1 en hover del contenido,
  // tipografía mono, react greyed/gold. Mismo flujo transversal: 👍 registra al
  // toque; 👎 despliega la línea de comentario OPCIONAL (no un "Gracias" seco).
  // Copy fijo del ticket §A1. Estado por tab: undefined|"form"|"sending"|"up"|"down".
  const st = state.reportingFeedback[expId];
  if (st === "up" || st === "down") {
    return `<div class="section-feedback" data-report-tab="${esc(expId)}">
      <p class="section-feedback__thanks" role="status">
        <span class="section-feedback__emoji">${st === "up" ? "👍" : "👎"}</span> ¡Gracias!
      </p>
    </div>`;
  }
  if (st === "form") {
    return `<div class="section-feedback" data-report-tab="${esc(expId)}">
      <div class="section-feedback__form">
        <label class="section-feedback__formlabel" for="rfc-${esc(expId)}">¿Qué esperabas o qué faltó acá? <span>(opcional)</span></label>
        <textarea id="rfc-${esc(expId)}" rows="2" placeholder="Una línea alcanza…"></textarea>
        <div class="section-feedback__formactions">
          <button type="button" class="section-feedback__ghost" onclick="cancelReportingFeedback('${esc(expId)}')">Cancelar</button>
          <button type="button" class="section-feedback__send" onclick="submitReportingFeedback('${esc(expId)}','down')">Enviar</button>
        </div>
      </div>
    </div>`;
  }
  const disabled = st === "sending" ? "disabled" : "";
  return `<div class="section-feedback" data-report-tab="${esc(expId)}">
    <div class="section-feedback__bar">
      <span class="section-feedback__prompt">¿Qué te pareció este reporte?</span>
      <button type="button" class="section-feedback__react" ${disabled} aria-label="Útil" title="Útil"
        onclick="submitReportingFeedback('${esc(expId)}','up')">👍</button>
      <button type="button" class="section-feedback__react" ${disabled} aria-label="Puede mejorar" title="Puede mejorar"
        onclick="openReportingFeedbackForm('${esc(expId)}')">👎</button>
      ${st === "error" ? `<span class="section-feedback__prompt">reintentá</span>` : ""}
    </div>
  </div>`;
}
// El 👎 despliega la línea de comentario OPCIONAL (todavía no persiste — igual
// que el section-feedback del Copiloto), Cancelar vuelve a la botonera.
function openReportingFeedbackForm(expId) {
  state.reportingFeedback[expId] = "form";
  render();
}
function cancelReportingFeedback(expId) {
  state.reportingFeedback[expId] = undefined;
  render();
}
async function submitReportingFeedback(expId, rating) {
  if (state.reportingFeedback[expId] === "sending") return;
  // El comentario (opcional) del 👎 se lee del textarea ANTES de re-renderizar.
  let comment = "";
  if (rating === "down") {
    const ta = document.getElementById("rfc-" + expId);
    if (ta) comment = ta.value || "";
  }
  state.reportingFeedback[expId] = "sending";
  render();
  try {
    const body = { report_tab: expId, rating };
    if (comment.trim()) body.comment = comment.trim();
    const resp = await fetch("/operaciones/reporting/integral/feedback", {
      method: "POST",
      body: new URLSearchParams(body),
    });
    const data = await resp.json();
    state.reportingFeedback[expId] = data.state === "ok" ? rating : "error";
  } catch (e) {
    state.reportingFeedback[expId] = "error";
  }
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
// HOT WINDOW / COLD HISTORY corrective (ver HANDOFF) — a diferencia de
// `replaceRows` (hidratación inicial, reemplaza TODO), un año frío
// lazy-loaded se AGREGA a lo ya hidratado (la ventana caliente sigue
// siendo válida, nunca se descarta). Nunca duplica: cada año se pide una
// sola vez (single-flight por año, ver loadCarteraMantenimientoColdYear).
function mergeRows(arr, rows) {
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
      // Reconcilia Año/Mes (y Año de Inicio, no-op si Proyecto no está
      // activo) contra los datos REALES recién llegados — el default
      // calendario (ver carteraDefaultFilters) pudo no tener ningún hecho
      // real bajo el Estado activo; nunca se deja una selección imposible
      // sin podar solo porque nadie tocó un filtro a mano todavía.
      reconcileExperienceFacets("cartera");
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
// MAIN-TAB HYDRATION CONTRACT (owner corrective — ver HANDOFF): Cartera
// es un MAIN TAB con 2 perspectivas internas (Mantenimiento/Proyecto).
// Antes, cada perspectiva se hidrataba LAZY e INDEPENDIENTE — recién al
// activarla (ver el viejo gate `if (status === "idle") loadCarteraFor
// (persp)` dentro de renderCartera). Eso daba un segundo ~15s de espera
// completo la PRIMERA vez que el usuario cambiaba a Proyecto, aunque ya
// llevara un rato mirando Mantenimiento. `hydrateCarteraTab()` dispara
// AMBOS fetches (+ la metadata de años, ver abajo) en la PRIMERA
// activación del tab — progresivo, nunca bloqueante: cada `load*` sigue
// gateado por su PROPIO status (single-flight, ver P2 del corrective
// anterior), así que llamar esto en cada render es gratis una vez que las
// 3 cargas ya no están "idle". El render de la perspectiva ACTIVA sigue
// gateado SOLO por su propio status (`carteraStatusFor(persp)`, ver
// renderCartera) — Mantenimiento nunca espera a que Proyecto (más lento,
// XLSX incluida) termine, y viceversa.
function hydrateCarteraTab() {
  if (state.carteraMantenimientoStatus === "idle") loadCarteraMantenimiento();
  if (state.carteraProyectoStatus === "idle") loadCarteraProyecto();
  if (state.carteraMantenimientoAniosStatus === "idle") loadCarteraMantenimientoAnios();
}
// HOT WINDOW / COLD HISTORY corrective: años que YA están en el array
// hidratado (ventana caliente inicial o un año frío ya mergeado) — fuente
// de verdad ÚNICA (nunca un Set paralelo que pueda desincronizarse del
// array real).
function carteraMantenimientoLoadedYears() {
  return new Set(CARTERA_MANTENIMIENTO_ROWS.map(r => r.anio));
}
// Metadata liviana de año/estado (ver /data/cartera/anios-disponibles) —
// disparada UNA vez por `hydrateCarteraTab`, en PARALELO con los 2 fetches
// de datos (nunca bloquea el primer render de Mantenimiento/Proyecto).
async function loadCarteraMantenimientoAnios() {
  state.carteraMantenimientoAniosStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/cartera/anios-disponibles", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.aniosDisponibles) {
      state.carteraMantenimientoAniosDisponibles = data.aniosDisponibles;
      state.carteraMantenimientoAniosStatus = "ready";
    } else {
      state.carteraMantenimientoAniosStatus = "error";
    }
  } catch (e) {
    state.carteraMantenimientoAniosStatus = "error";
  }
  render();
}
// Lazy-load de un año FRÍO puntual (HOT WINDOW / COLD HISTORY corrective)
// — disparado desde renderCartera cuando el Año seleccionado todavía no
// está en `carteraMantenimientoLoadedYears()`. Single-flight POR AÑO
// (`carteraMantenimientoYearLoading`, un Set — nunca dos requests para el
// mismo año en vuelo a la vez). `mergeRows` (nunca `replaceRows`): la
// ventana caliente ya hidratada se PRESERVA, el año frío se agrega.
async function loadCarteraMantenimientoColdYear(anio) {
  if (state.carteraMantenimientoYearLoading.has(anio)) return;
  state.carteraMantenimientoYearLoading.add(anio);
  reportingTelemetry("reporting.cold_year_load", { report_tab: "cartera", load_scope: anio });
  state.carteraMantenimientoYearError = "";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/cartera", {
      method: "POST",
      body: new URLSearchParams({ graph_token: token, perspectiva: "mantenimiento", anios: anio }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.cartera && data.cartera.mantenimiento) {
      mergeRows(CARTERA_MANTENIMIENTO_ROWS, data.cartera.mantenimiento);
      reconcileExperienceFacets("cartera");
    } else {
      state.carteraMantenimientoYearError = anio;
    }
  } catch (e) {
    state.carteraMantenimientoYearError = anio;
  }
  state.carteraMantenimientoYearLoading.delete(anio);
  render();
}
function renderCarteraDataState() {
  const persp = state.perspective.cartera;
  if (carteraStatusFor(persp) === "error") {
    return reportingSourceErrorHtml("los datos de Cartera");
  }
  if (persp === "Mantenimiento") {
    const anio = getFilter("cartera", "anio");
    if (anio && state.carteraMantenimientoYearError === anio) {
      return reportingSourceErrorHtml(`los datos de Cartera del año ${anio}`);
    }
    if (anio && !carteraMantenimientoLoadedYears().has(anio)) {
      return reportingLoadingHtml(`Cargando datos de Cartera del año ${anio}…`);
    }
  }
  return reportingLoadingHtml("Cargando datos de Cartera…");
}

// Rentabilidad (AMBAS perspectivas, Resumen Y Certificadas vs Ejecutadas —
// Batch B, ver HANDOFF/real-data-lineage.md §10) — HOT WINDOW / COLD
// HISTORY (owner: "apply the SAME temporal runtime principle to
// Rentabilidad") — MISMO patrón que Horas Vendidas: ventana caliente (año
// actual + anterior, resuelta server-side) en la primera hidratación,
// `replaceRows`/`replaceMonthly` pueblan EN EL LUGAR, metadata de años en
// paralelo (loadRentabilidadAnios), año frío lazy-load single-flight
// (loadRentabilidadColdYear).
function hydrateRentabilidadTab() {
  if (state.rentabilidadDataStatus === "idle") loadRentabilidadData();
  if (state.rentabilidadAniosStatus === "idle") loadRentabilidadAnios();
}
// Años YA hidratados — fuente de verdad ÚNICA: las claves de
// RENTABILIDAD_NOASIGNADAS_BY_YEAR (portfolio, poblado en CADA hidratación
// exitosa — hot o fría — sin importar si esa carga trajo 0 proyectos; era
// RENTABILIDAD_VENDIDAS_BY_YEAR antes del SOURCE-PARITY corrective —
// Vendidas dejó de ser portfolio, No asignadas sigue siéndolo y cumple el
// MISMO rol de centinela).
function rentabilidadLoadedYears() {
  return new Set(Object.keys(RENTABILIDAD_NOASIGNADAS_BY_YEAR));
}
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
      replaceMonthly(RENTABILIDAD_NOASIGNADAS_MENSUAL, data.rentabilidad.noAsignadasMonthly);
      RENTABILIDAD_NOASIGNADAS_BY_YEAR = data.rentabilidad.noAsignadasByYear || {};
      // Default Año = año actual válido (faceted fallback) — SOLO en hidratación
      // (nunca en cambio de perspectiva; ver §10 del batch).
      if (!getFilter("rentabilidad", "anio")) {
        state.filters.rentabilidad.anio = rentabilidadDefaultAnio();
      }
      state.rentabilidadDataStatus = "ready";
    } else {
      state.rentabilidadDataStatus = "error";
    }
  } catch (e) {
    state.rentabilidadDataStatus = "error";
  }
  render();
}
// Metadata liviana de año/estado — disparada UNA vez por
// hydrateRentabilidadTab, en PARALELO con el fetch de datos (nunca bloquea
// el primer render de la ventana caliente). MISMO endpoint/dominio que
// Horas Vendidas (todos los tipos de proyecto).
async function loadRentabilidadAnios() {
  state.rentabilidadAniosStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/rentabilidad/anios-disponibles", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.aniosDisponibles) {
      state.rentabilidadAniosDisponibles = data.aniosDisponibles;
      state.rentabilidadAniosStatus = "ready";
    } else {
      state.rentabilidadAniosStatus = "error";
    }
  } catch (e) {
    state.rentabilidadAniosStatus = "error";
  }
  render();
}
// Fusión por PROYECTO (owner directive — cache por scope en el cliente,
// MISMO criterio que `mergeHorasVendidasRows`): Rentabilidad es UNA fila
// por proyecto (clave natural: `proyecto`, único en `RENTABILIDAD_
// PROYECTOS`) con certByYear/trabByYear/vendByYear que abarcan años — un
// año frío se fusiona en ESOS mapas, nunca crea una fila duplicada.
// Portfolio (No asignadas por año — Vendidas dejó de ser portfolio, ver
// SOURCE-PARITY corrective arriba) se fusiona directo por Object.assign.
function mergeRentabilidadRows(incoming) {
  const byKey = new Map(RENTABILIDAD_PROYECTOS.map(p => [p.proyecto, p]));
  (incoming.proyectos || []).forEach(inc => {
    const existing = byKey.get(inc.proyecto);
    if (existing) {
      Object.assign(existing.certByYear || (existing.certByYear = {}), inc.certByYear || {});
      Object.assign(existing.trabByYear || (existing.trabByYear = {}), inc.trabByYear || {});
      Object.assign(existing.vendByYear || (existing.vendByYear = {}), inc.vendByYear || {});
    } else {
      RENTABILIDAD_PROYECTOS.push(inc);
      byKey.set(inc.proyecto, inc);
    }
  });
  Object.assign(RENTABILIDAD_NOASIGNADAS_BY_YEAR, incoming.noAsignadasByYear || {});
}
// Lazy-load de un año FRÍO puntual — disparado desde renderRentabilidad
// cuando el Año seleccionado todavía no está en `rentabilidadLoadedYears()`.
// Single-flight POR AÑO (MISMO criterio que Horas Vendidas/Cartera).
async function loadRentabilidadColdYear(anio) {
  if (state.rentabilidadYearLoading.has(anio)) return;
  state.rentabilidadYearLoading.add(anio);
  reportingTelemetry("reporting.cold_year_load", { report_tab: "rentabilidad", load_scope: anio });
  state.rentabilidadYearError = "";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/rentabilidad", {
      method: "POST", body: new URLSearchParams({ graph_token: token, anios: anio }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.rentabilidad) {
      mergeRentabilidadRows(data.rentabilidad);
      reconcileExperienceFacets("rentabilidad");
    } else {
      state.rentabilidadYearError = anio;
    }
  } catch (e) {
    state.rentabilidadYearError = anio;
  }
  state.rentabilidadYearLoading.delete(anio);
  render();
}
function renderRentabilidadDataState() {
  if (state.rentabilidadDataStatus === "error") {
    return reportingSourceErrorHtml("los datos de Rentabilidad");
  }
  const anio = getFilter("rentabilidad", "anio");
  if (anio && state.rentabilidadYearError === anio) {
    return reportingSourceErrorHtml(`los datos de Rentabilidad del año ${anio}`);
  }
  if (anio && !rentabilidadLoadedYears().has(anio)) {
    return reportingLoadingHtml(`Cargando datos de Rentabilidad del año ${anio}…`);
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
      // Horas Vendidas Y Participación ya NO viajan acá (scope decoupling —
      // cada una tiene su propio endpoint con ventana temporal, ver
      // loadHorasVendidasData/loadComercialParticipacionData).
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

// Horas Vendidas — fetch PROPIO con SCOPE TEMPORAL (owner directive: HV pide al
// mismo source-family SÓLO su ventana caliente, sin arrastrar la historia
// completa de Participación; "batch-a como frontera de runtime"). MISMO patrón
// que loadCarteraMantenimiento: ventana caliente por defecto (año actual +
// anterior, resuelto server-side), `replaceRows` en la hidratación, status
// propio. La metadata de años (para años fríos seleccionables) va en paralelo
// (loadHorasVendidasAnios), un año frío se lazy-loadea al elegirlo
// (loadHorasVendidasColdYear).
function hydrateHorasVendidasTab() {
  if (state.horasVendidasDataStatus === "idle") loadHorasVendidasData();
  if (state.horasVendidasAniosStatus === "idle") loadHorasVendidasAnios();
}
// Años YA hidratados (ventana caliente inicial + años fríos ya mergeados) —
// fuente de verdad ÚNICA: las claves de vendidasByYear presentes en los rows
// (nunca un Set paralelo que pueda desincronizarse del array real).
function horasVendidasLoadedYears() {
  const years = new Set();
  HORASVENDIDAS_ROWS.forEach(r => Object.keys(r.vendidasByYear || {}).forEach(y => years.add(y)));
  return years;
}
async function loadHorasVendidasData() {
  state.horasVendidasDataStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/horas-vendidas", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.horasVendidas) {
      replaceRows(HORASVENDIDAS_ROWS, data.horasVendidas);
      // Default Año = año actual válido (faceted fallback) — SOLO en hidratación
      // (nunca en cambio de perspectiva). Mismo criterio que Rentabilidad.
      if (!getFilter("horasvendidas", "anio")) {
        state.filters.horasvendidas.anio = horasVendidasDefaultAnio();
      }
      state.horasVendidasDataStatus = "ready";
    } else {
      state.horasVendidasDataStatus = "error";
    }
  } catch (e) {
    state.horasVendidasDataStatus = "error";
  }
  render();
}
// Metadata liviana de año/estado (todos los tipos) — disparada UNA vez por
// hydrateHorasVendidasTab, en PARALELO con el fetch de datos (nunca bloquea el
// primer render de la ventana caliente).
async function loadHorasVendidasAnios() {
  state.horasVendidasAniosStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/horas-vendidas/anios-disponibles", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.aniosDisponibles) {
      state.horasVendidasAniosDisponibles = data.aniosDisponibles;
      state.horasVendidasAniosStatus = "ready";
    } else {
      state.horasVendidasAniosStatus = "error";
    }
  } catch (e) {
    state.horasVendidasAniosStatus = "error";
  }
  render();
}
// Lazy-load de un año FRÍO puntual — disparado desde renderHorasVendidas cuando
// el Año seleccionado todavía no está en `horasVendidasLoadedYears()`.
// Single-flight POR AÑO. `mergeHorasVendidasRows` (no `replaceRows`): la
// ventana caliente ya hidratada se PRESERVA, el año frío se fusiona en el
// vendidasByYear de cada proyecto.
async function loadHorasVendidasColdYear(anio) {
  if (state.horasVendidasYearLoading.has(anio)) return;
  state.horasVendidasYearLoading.add(anio);
  reportingTelemetry("reporting.cold_year_load", { report_tab: "horasvendidas", load_scope: anio });
  state.horasVendidasYearError = "";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/horas-vendidas", {
      method: "POST", body: new URLSearchParams({ graph_token: token, anios: anio }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.horasVendidas) {
      mergeHorasVendidasRows(data.horasVendidas);
      reconcileExperienceFacets("horasvendidas");
    } else {
      state.horasVendidasYearError = anio;
    }
  } catch (e) {
    state.horasVendidasYearError = anio;
  }
  state.horasVendidasYearLoading.delete(anio);
  render();
}
// Fusión por PROYECTO (owner directive — cache por scope en el cliente): a
// diferencia de Cartera (grano proyecto×mes×año, donde un año frío son filas
// NUEVAS), HV es UNA fila por proyecto con un mapa vendidasByYear que abarca
// años — un año frío debe fusionarse en ESE mapa, nunca crear una fila
// duplicada del proyecto. Fila nueva (proyecto que no estaba en la ventana
// caliente) se agrega entera.
function mergeHorasVendidasRows(incoming) {
  const keyOf = r => [r.cp, r.tipo, r.cliente, r.proyecto].join("|");
  const byKey = new Map(HORASVENDIDAS_ROWS.map(r => [keyOf(r), r]));
  incoming.forEach(inc => {
    const existing = byKey.get(keyOf(inc));
    if (existing) {
      Object.assign(existing.vendidasByYear || (existing.vendidasByYear = {}), inc.vendidasByYear || {});
    } else {
      HORASVENDIDAS_ROWS.push(inc);
      byKey.set(keyOf(inc), inc);
    }
  });
}
function renderHorasVendidasDataState() {
  if (state.horasVendidasDataStatus === "error") {
    return reportingSourceErrorHtml("los datos de Horas Vendidas");
  }
  const anio = getFilter("horasvendidas", "anio");
  if (anio && state.horasVendidasYearError === anio) {
    return reportingSourceErrorHtml(`los datos de Horas Vendidas del año ${anio}`);
  }
  if (anio && !horasVendidasLoadedYears().has(anio)) {
    return reportingLoadingHtml(`Cargando datos de Horas Vendidas del año ${anio}…`);
  }
  return reportingLoadingHtml("Cargando datos de Horas Vendidas…");
}

// Participación — fetch PROPIO con SCOPE TEMPORAL (owner directive: "Do NOT
// keep Participación on full-history simply because old batch-a did" —
// converge al MISMO principio que Horas Vendidas/Rentabilidad, "batch-a como
// frontera de runtime"). MISMO patrón que loadHorasVendidasData: ventana
// caliente por defecto (año actual + anterior, resuelto server-side),
// `replaceRows` en la hidratación, status propio. La metadata de años va en
// paralelo (loadComercialParticipacionAnios); a diferencia de HV (Año
// single-select, UN año frío a la vez), Participación es MULTI-SELECT — un
// año frío se lazy-loadea al tildarlo, y VARIOS años fríos tildados de un
// saque se bundlean en UN solo request (loadComercialParticipacionColdYears
// acepta un array).
function hydrateComercialParticipacionTab() {
  if (state.comercialParticipacionDataStatus === "idle") loadComercialParticipacionData();
  if (state.comercialParticipacionAniosStatus === "idle") loadComercialParticipacionAnios();
}
async function loadComercialParticipacionData() {
  state.comercialParticipacionDataStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/participacion", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.participacion) {
      replaceRows(COMERCIAL_PARTICIPACION_ROWS, data.participacion);
      const current = String(new Date().getFullYear());
      const previous = String(new Date().getFullYear() - 1);
      state.comercialParticipacionLoadedYears.add(current);
      state.comercialParticipacionLoadedYears.add(previous);
      // Default Año = ventana caliente tildada (SOLO en la primera
      // hidratación, Set vacío) — MISMO criterio "SOLO en hidratación, nunca
      // en cambio de perspectiva" que Rentabilidad/HV, pero con 2 años
      // (Año es MULTI-SELECT acá, owner directive).
      if (state.comercialParticipacionAnios.size === 0) {
        state.comercialParticipacionAnios = comercialParticipacionDefaultAnios();
      }
      state.comercialParticipacionDataStatus = "ready";
    } else {
      state.comercialParticipacionDataStatus = "error";
    }
  } catch (e) {
    state.comercialParticipacionDataStatus = "error";
  }
  render();
}
// Metadata liviana de año/estado (todos los tipos) — disparada UNA vez por
// hydrateComercialParticipacionTab, en PARALELO con el fetch de datos (nunca
// bloquea el primer render de la ventana caliente). MISMO endpoint/dominio
// que Horas Vendidas/Rentabilidad (reuso, ver get_participacion_anios).
async function loadComercialParticipacionAnios() {
  state.comercialParticipacionAniosStatus = "loading";
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/participacion/anios-disponibles", {
      method: "POST", body: new URLSearchParams({ graph_token: token }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.aniosDisponibles) {
      state.comercialParticipacionAniosDisponibles = data.aniosDisponibles;
      state.comercialParticipacionAniosStatus = "ready";
    } else {
      state.comercialParticipacionAniosStatus = "error";
    }
  } catch (e) {
    state.comercialParticipacionAniosStatus = "error";
  }
  render();
}
// Fusión por CLIENTE (owner directive — cache por scope en el cliente, MISMO
// criterio que mergeHorasVendidasRows/mergeRentabilidadRows): Participación
// es UNA fila por Cliente con vendidasByYear/certificadasByYear que abarcan
// años — un año frío se fusiona en ESOS mapas, nunca crea una fila
// duplicada del cliente. `cliente` (nombre) es la clave natural — MISMO
// criterio que el resto del Hub (campos de negocio visibles, no un ID
// interno que el contrato de datos no expone).
function mergeComercialParticipacionRows(incoming) {
  const byKey = new Map(COMERCIAL_PARTICIPACION_ROWS.map(r => [r.cliente, r]));
  (incoming || []).forEach(inc => {
    const existing = byKey.get(inc.cliente);
    if (existing) {
      Object.assign(existing.vendidasByYear || (existing.vendidasByYear = {}), inc.vendidasByYear || {});
      Object.assign(existing.certificadasByYear || (existing.certificadasByYear = {}), inc.certificadasByYear || {});
    } else {
      COMERCIAL_PARTICIPACION_ROWS.push(inc);
      byKey.set(inc.cliente, inc);
    }
  });
}
// Lazy-load de uno o VARIOS años FRÍOS a la vez (Año MULTI-SELECT, owner
// directive — a diferencia del single-select de HV/Rentabilidad, tildar
// varios años fríos de un saque no debe disparar N requests separados) —
// bundleados en UN solo `anios=y1,y2` (ver reporting.py, MISMO parseo CSV
// que el resto de los endpoints con scope temporal). Single-flight POR AÑO
// vía `comercialParticipacionYearLoading` (un Set — el caller ya filtra los
// años en vuelo antes de armar el batch, ver renderComercialParticipacion).
async function loadComercialParticipacionColdYears(anios) {
  anios.forEach(y => state.comercialParticipacionYearLoading.add(y));
  anios.forEach(y => state.comercialParticipacionYearErrors.delete(y));
  try {
    const token = sessionStorage.getItem("dw-graph-token") || "";
    const resp = await fetch("/operaciones/reporting/integral/data/participacion", {
      method: "POST", body: new URLSearchParams({ graph_token: token, anios: anios.join(",") }),
    });
    const data = await resp.json();
    if (data.state === "ok" && data.participacion) {
      mergeComercialParticipacionRows(data.participacion);
      anios.forEach(y => state.comercialParticipacionLoadedYears.add(y));
      reconcileExperienceFacets("comercial");
    } else {
      anios.forEach(y => state.comercialParticipacionYearErrors.add(y));
    }
  } catch (e) {
    anios.forEach(y => state.comercialParticipacionYearErrors.add(y));
  }
  anios.forEach(y => state.comercialParticipacionYearLoading.delete(y));
  render();
}
function renderParticipacionDataState() {
  if (state.comercialParticipacionDataStatus === "error") {
    return reportingSourceErrorHtml("los datos de Participación");
  }
  const erroredChecked = [...state.comercialParticipacionAnios].filter(y => state.comercialParticipacionYearErrors.has(y));
  if (erroredChecked.length) {
    return reportingSourceErrorHtml(`los datos de Participación del año ${erroredChecked.join(", ")}`);
  }
  return reportingLoadingHtml("Cargando datos de Participación…");
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
      // SOURCE-PARITY corrective: denominador REAL de "% Incidencia" —
      // objeto plano, se reemplaza entero (ver capacidadProductivasTotal).
      CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR = data.horasProductivasDenominador || {};
      // Default Año = año actual válido — SOLO en hidratación (owner
      // corrective, MISMO criterio que Rentabilidad/HV), un panel a la vez,
      // nunca pisa una elección ya hecha por el usuario (`getFilter` vacío
      // ⇒ todavía sin elegir).
      CAPACIDAD_PRODUCTIVAS_PANELS.forEach(panel => {
        const key = capacidadPanelAnioKey(panel);
        if (!getFilter("capacidad", key)) {
          state.filters.capacidad[key] = capacidadProductivasDefaultAnio();
        }
      });
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
  // MAIN-TAB HYDRATION CONTRACT (owner corrective, ver HANDOFF): la
  // hidratación de AMBAS perspectivas (+ metadata de años) la dispara el
  // SHELL (`TAB_HYDRATION_TRIGGERS`, ver `render()` — SHARED REPORTING
  // INITIAL-LOAD CONTRACT), nunca este dispatcher — acá solo queda el GATE
  // de qué mostrar según el status de la perspectiva ACTIVA (progresivo —
  // Mantenimiento no espera a Proyecto ni viceversa).
  const persp = state.perspective.cartera;
  const status = carteraStatusFor(persp);
  if (status !== "ready") return renderCarteraDataState();
  // HOT WINDOW / COLD HISTORY corrective: si Mantenimiento tiene un Año
  // seleccionado que todavía no está hidratado (año frío), dispara su
  // lazy-load (single-flight, ver loadCarteraMantenimientoColdYear) y
  // muestra el loader compartido SOLO para ese trabajo pendiente — el
  // filter bar (render aparte, ver FILTER_BARS) sigue interactivo. Un
  // cambio de perspectiva NUNCA llega acá con un año inválido para la
  // OTRA perspectiva (Año/anioInicio son namespaces de filtro distintos).
  if (persp === "Mantenimiento") {
    const anio = getFilter("cartera", "anio");
    if (anio && !carteraMantenimientoLoadedYears().has(anio) && state.carteraMantenimientoYearError !== anio) {
      loadCarteraMantenimientoColdYear(anio);
      return renderCarteraDataState();
    }
  }
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

// ============================================================================
// TEMPORAL DOMAIN CONTRACT (owner live QA — "REPORTING TEMPORAL CONTRACT +
// PERFORMANCE CORRECTIVE"): un ÚNICO mecanismo compartido, reusado por
// Horas Vendidas Y Rentabilidad (nunca dos copias) — ver HANDOFF.
//
// YEAR DOMAIN: SOLO años de 4 dígitos (`/^\d{4}$/`) — nunca "Todos", nunca
// un nombre de mes, nunca una string arbitraria. `_isValidAnioValue` es el
// ÚNICO punto que decide (mismo criterio/regex que `_is_valid_anio` del
// backend, reporting_repository.py — DOS lenguajes, UN contrato). Owner
// live QA: "Septiembre" apareció como opción del <select> de Año — el
// root cause real era que NINGÚN punto (ni backend ni frontend) validaba
// el dominio antes de este corrective; un valor no-numérico en la columna
// fuente `A_x00f1_o` fluía sin filtro. El backend ya lo descarta en la
// fuente (nunca llega en la respuesta); este guard frontend es la segunda
// línea de defensa (nunca confiar en que el payload sea limpio a ciegas).
//
// MONTH DOMAIN: "" (Todos) + "1".."12" — nunca cruza con el YEAR DOMAIN.
//
// CURRENT-YEAR FUTURE-MONTH CAP (owner product rule — reporting operacional
// "actual a la fecha"): cuando el Año seleccionado ES el año calendario
// real en curso, los meses POSTERIORES al mes calendario actual quedan
// `null` (NO 0 — 0 afirmaría "se observó cero", cuando la realidad es "el
// período todavía no aplica"). Años históricos (≠ año actual) NUNCA se
// acotan — grano completo, source-driven. `null` se propaga limpio por
// TODO el pipeline existente sin tocar los formateadores: `fmtNum`/
// `diffCell`/`renderDashboardKpi` YA tratan null como "—" (contrato
// establecido en la corrective de formateo transversal), y `null + number`
// coerciona a `number` en JS — sumar un mes capado nunca ensucia un total
// aritmético con NaN, solo hace falta preservar el `null` cuando TODAS las
// filas que contribuyen a un índice están capadas (ver `_sumMonthlyArrays`
// abajo — un acumulador ingenuo con `+=` perdería la señal).
function _isValidAnioValue(v) { return /^\d{4}$/.test(String(v || "")); }
function _currentYearStr() { return String(new Date().getFullYear()); }
// Mes calendario ACTUAL 1-12, o `null` si `anio` no es el año en curso
// (histórico/futuro-no-soportado ⇒ sin cap).
function _operationalMonthCap(anio) {
  return anio === _currentYearStr() ? (new Date().getMonth() + 1) : null;
}
// `cap` es 1-based (Agosto=8 ⇒ índices 0..7 válidos, 8..11 ⇒ null).
// `cap` null/falsy ⇒ passthrough sin cambios (año histórico).
function _applyMonthCap(arr, cap) {
  if (!cap) return arr;
  return arr.map((v, i) => (i < cap ? v : null));
}
// Acumulación NULL-AWARE: un índice queda `null` en el resultado SOLO si
// NINGÚN array de entrada aportó un valor real ahí (todas las filas
// contribuyentes están capadas/ausentes en ese mes) — a diferencia de un
// `+=` ingenuo partiendo de 0, que perdería la distinción "0 real" vs "sin
// dato" apenas UNA fila con `null` se sume con `+`. Reusado por
// `sumMonthly` (rows con `.monthly`) y por acumulaciones manuales
// (`renderRentabilidadMensual`).
function _sumMonthlyArrays(arrays) {
  const totals = new Array(12).fill(null);
  arrays.forEach(arr => (arr || _z12()).forEach((v, i) => {
    if (v === null || v === undefined) return;
    totals[i] = (totals[i] || 0) + v;
  }));
  return totals;
}
// Diferencia elemento-a-elemento NULL-AWARE (ej. Rentabilidad horas =
// Certificadas − Trabajadas por mes): si CUALQUIERA de los dos operandos
// está capado en un índice, el resultado también lo está — no se puede
// calcular un margen contra un período "no aplicable".
function _diffMonthly(a, b) {
  return a.map((v, i) => (v === null || b[i] === null) ? null : v - b[i]);
}
// Suma de un trimestre (0=Q1..3=Q4) NULL-AWARE: si el trimestre ENTERO cae
// fuera del cap (los 3 meses capados), el resultado es `null` ("no
// contribution / not applicable", nunca un 0 numérico que sugeriría "ya
// sabemos que da cero"). Parcialmente capado (ej. Jul+Ago válidos, Sep
// capado): suma los meses válidos, ignora los capados (coerción null→0).
function _quarterValue(monthlyArr, q) {
  const slice = monthlyArr.slice(q * 3, q * 3 + 3);
  if (slice.every(v => v === null)) return null;
  return slice.reduce((s, v) => s + (v || 0), 0);
}
// Mes domain-aware: SIEMPRE Todos + 12 fijos, PERO si `anio` es el año
// calendario en curso, los meses POSTERIORES al actual se omiten de las
// opciones (owner: "available individual months should reflect actual
// valid operational months" — nunca ofrecer Septiembre como mes
// seleccionable en Agosto-2026). `Todos` SIEMPRE queda disponible (significa
// "todos los meses VÁLIDOS hasta el actual", ver `_operationalMonthCap`).
function _validMesOptions(anio) {
  const cap = _operationalMonthCap(anio);
  return MESES_LARGOS
    .map((nombre, i) => ({ value: String(i + 1), label: nombre }))
    .filter((_, i) => !cap || i < cap);
}
// ============================================================================

// --- Rentabilidad Año/Mes/Estado (migración, HANDOFF §-16 #8) ----------------
// Facets <select> de Rentabilidad — para computar las opciones de Año de forma
// faceteada (mismo criterio que _CARTERA_SELECT_FIELDS).
const _RENT_SELECT_FIELDS = [{ key: "cp", field: "cp" }, { key: "cliente", field: "cliente" }, { key: "tipo", field: "tipo" }, { key: "proyecto", field: "proyecto" }];
function _z12() { return new Array(12).fill(0); }
// Máscara de Mes (valor "" = Todos; "1".."12"): deja solo el mes elegido.
function _maskMes(arr, mes) {
  const a = (arr || _z12()).slice();
  if (!mes) return a;
  const idx = Number(mes) - 1;
  return a.map((v, i) => (i === idx ? v : 0));
}
// Máscara de MESES multi-select (Horas Productivas, owner corrective — ver
// `capacidadMesesSeleccionados`/`toggleCapacidadMes`): Set vacío = "Todos"
// (ningún mes excluido), Set no vacío deja SOLO los meses tildados en 0. NO
// reusa `_maskMes` (valor único) ni lo modifica — semántica distinta
// (`mes` ahí es un string, acá un Set), nunca condicional en el mismo
// helper.
function _maskMeses(arr, mesesSet) {
  const a = (arr || _z12()).slice();
  if (!mesesSet || mesesSet.size === 0) return a;
  return a.map((v, i) => (mesesSet.has(String(i + 1)) ? v : 0));
}
// Años disponibles (facet MULTIDIRECCIONAL): unión de años presentes en los
// proyectos que pasan Estado + CP/Cliente/Tipo/Proyecto (excluye el propio Año).
function rentabilidadAnioOptions() {
  const statusRows = RENTABILIDAD_PROYECTOS.filter(p => state.rentabilidadStatus[p.estado]);
  const rows = facetRowsExcept(statusRows, state.filters.rentabilidad || {}, _RENT_SELECT_FIELDS, "");
  const years = new Set();
  rows.forEach(p => {
    Object.keys(p.certByYear || {}).forEach(y => years.add(y));
    Object.keys(p.trabByYear || {}).forEach(y => years.add(y));
    Object.keys(p.vendByYear || {}).forEach(y => years.add(y));
  });
  (state.rentabilidadAniosDisponibles || []).forEach(p => {
    if (state.rentabilidadStatus[p.estado]) years.add(p.anio);
  });
  // TEMPORAL DOMAIN CONTRACT: segunda línea de defensa (el backend YA
  // descarta valores no-4-dígitos en la fuente) — Año NUNCA muestra un
  // valor fuera de /^\d{4}$/, sea cual sea el origen del dato.
  return [...years].filter(_isValidAnioValue).sort();
}
function rentabilidadDefaultAnio() {
  const anioActual = String(new Date().getFullYear());
  const options = rentabilidadAnioOptions();
  if (options.includes(anioActual)) return anioActual;
  return options.length ? options[options.length - 1] : "";  // más reciente válido
}
// Prune de Año obsoleto al cambiar Estado u otro facet: si el Año elegido deja
// de ser válido, cae al default; "" (Todos) se respeta. Nunca año imposible.
function pruneRentabilidadAnio() {
  const anio = getFilter("rentabilidad", "anio");
  if (!anio) return;
  if (!rentabilidadAnioOptions().includes(anio)) {
    state.filters.rentabilidad.anio = rentabilidadDefaultAnio();
  }
}
function toggleRentabilidadStatus(key) {
  state.rentabilidadStatus[key] = !state.rentabilidadStatus[key];
  reconcileExperienceFacets("rentabilidad");
  render();
}
// UNA población canónica: Estado + CP/Cliente/Tipo/Proyecto, proyectada al Año
// elegido (o colapsada si Todos) y enmascarada por Mes. TODAS las secciones
// (KPIs, ranking, matriz, Cert-vs-Ejec, detalle) consumen ESTO.
function rentabilidadSelectedRows() {
  const anio = getFilter("rentabilidad", "anio");
  const mes = getFilter("rentabilidad", "mes");
  const cap = _operationalMonthCap(anio);
  const base = RENTABILIDAD_PROYECTOS.filter(p => state.rentabilidadStatus[p.estado]);
  const rows = facetRowsExcept(base, state.filters.rentabilidad || {}, _RENT_SELECT_FIELDS, "");
  return rows.map(p => {
    const certArr = anio ? ((p.certByYear || {})[anio] || _z12()) : (p.certMonthly || _z12());
    const trabArr = anio ? ((p.trabByYear || {})[anio] || _z12()) : (p.trabMonthly || _z12());
    // Vendidas ahora POR PROYECTO (SOURCE-PARITY corrective — ver comentario
    // de RENTABILIDAD_NOASIGNADAS_MENSUAL arriba), MISMO tratamiento que
    // Certificadas/Trabajadas: responde a Estado + CP/Cliente/Tipo/Proyecto,
    // nunca un escalar de portfolio fijo.
    const vendArr = anio ? ((p.vendByYear || {})[anio] || _z12()) : (p.vendMonthly || _z12());
    // CURRENT-YEAR FUTURE-MONTH CAP aplicado DESPUÉS de la máscara de Mes
    // (orden defensivo: si Año=año actual, el cap gana incluso si algún
    // día un Mes futuro llegara seleccionado — ver `_validMesOptions`, que
    // ya evita ofrecerlo como opción).
    const cm = _applyMonthCap(_maskMes(certArr, mes), cap);
    const tm = _applyMonthCap(_maskMes(trabArr, mes), cap);
    const vm = _applyMonthCap(_maskMes(vendArr, mes), cap);
    return { ...p, certMonthly: cm, trabMonthly: tm, vendMonthly: vm,
      certificadas: cm.reduce((a, b) => a + (b || 0), 0), trabajadas: tm.reduce((a, b) => a + (b || 0), 0),
      vendidas: vm.reduce((a, b) => a + (b || 0), 0) };
  });
}
// Portfolio (SOLO No asignadas — Vendidas dejó de ser portfolio, ver
// rentabilidadSelectedRows) para el Año+Mes seleccionados.
function rentabilidadSelectedPortfolio() {
  const anio = getFilter("rentabilidad", "anio");
  const mes = getFilter("rentabilidad", "mes");
  const cap = _operationalMonthCap(anio);
  const noas = anio ? (RENTABILIDAD_NOASIGNADAS_BY_YEAR[anio] || _z12()) : RENTABILIDAD_NOASIGNADAS_MENSUAL;
  return {
    noAsignadas: _applyMonthCap(_maskMes(noas, mes), cap),
  };
}
function rentabilidadFilteredProyectos() {
  return rentabilidadSelectedRows();
}

// --- Horas Vendidas Año/Mes/Estado (MISMO patrón que Rentabilidad) -----------
const _HV_SELECT_FIELDS = [{ key: "cp", field: "cp" }, { key: "tipo", field: "tipo" }, { key: "cliente", field: "cliente" }, { key: "proyecto", field: "proyecto" }];
function horasVendidasAnioOptions() {
  const statusRows = HORASVENDIDAS_ROWS.filter(r => state.horasVendidasStatus[r.estado]);
  const rows = facetRowsExcept(statusRows, state.filters.horasvendidas || {}, _HV_SELECT_FIELDS, "");
  const years = new Set();
  rows.forEach(r => Object.keys(r.vendidasByYear || {}).forEach(y => years.add(y)));
  // Años FRÍOS seleccionables desde la metadata (HOT WINDOW / COLD HISTORY:
  // "no confundir OPCIONES con años HIDRATADOS" — un año fuera de la ventana
  // caliente sigue siendo elegible, y al elegirlo se lazy-loadea). Respeta el
  // facet Estado (mismo criterio que las filas: estado activo en el map).
  (state.horasVendidasAniosDisponibles || []).forEach(p => {
    if (state.horasVendidasStatus[p.estado]) years.add(p.anio);
  });
  // TEMPORAL DOMAIN CONTRACT (owner live QA — "Septiembre" en el <select>
  // de Año): segunda línea de defensa, el backend YA descarta valores
  // no-4-dígitos en la fuente (`_is_valid_anio`) — Año NUNCA muestra algo
  // fuera de /^\d{4}$/ sea cual sea el origen del dato.
  return [...years].filter(_isValidAnioValue).sort();
}
function horasVendidasDefaultAnio() {
  const anioActual = String(new Date().getFullYear());
  const options = horasVendidasAnioOptions();
  if (options.includes(anioActual)) return anioActual;
  return options.length ? options[options.length - 1] : "";
}
function pruneHorasVendidasAnio() {
  const anio = getFilter("horasvendidas", "anio");
  if (!anio) return;
  if (!horasVendidasAnioOptions().includes(anio)) {
    state.filters.horasvendidas.anio = horasVendidasDefaultAnio();
  }
}
function toggleHorasVendidasStatus(key) {
  state.horasVendidasStatus[key] = !state.horasVendidasStatus[key];
  reconcileExperienceFacets("horasvendidas");
  render();
}
// Población canónica: Estado + CP/Tipo/Cliente/Proyecto, proyectada al Año elegido
// (o colapsada si Todos) y enmascarada por Mes. Alimenta jerarquía/matriz/KPIs/Q.
function horasVendidasSelectedRows() {
  const anio = getFilter("horasvendidas", "anio");
  const mes = getFilter("horasvendidas", "mes");
  const cap = _operationalMonthCap(anio);
  const base = HORASVENDIDAS_ROWS.filter(r => state.horasVendidasStatus[r.estado]);
  const rows = facetRowsExcept(base, state.filters.horasvendidas || {}, _HV_SELECT_FIELDS, "");
  return rows.map(r => {
    const arr = anio ? ((r.vendidasByYear || {})[anio] || _z12()) : (r.monthly || _z12());
    return { ...r, monthly: _applyMonthCap(_maskMes(arr, mes), cap) };
  });
}

// --- Participación Año/Estado (owner directive — "Do NOT keep Participación
// on full-history", reusar la infraestructura temporal de HV/Rentabilidad) --
// Año es MULTI-SELECT (a diferencia del single-select de HV/Rentabilidad —
// el pivotTable de Participación agrega SOBRE el conjunto de años tildados,
// nunca muestra un año a la vez) — checkbox-group con estado propio
// (`state.comercialParticipacionAnios`, un Set), MISMO patrón que Año de
// Inicio de Cartera/Proyecto pero con semántica de hot/cold FETCH además de
// filtro visual (ver hydrateComercialParticipacionTab/loadComercial
// ParticipacionColdYears abajo).
const _COMERCIAL_PART_SELECT_FIELDS = [{ key: "cp", field: "cp" }, { key: "cliente", field: "cliente" }];
// Años disponibles: unión de años presentes en los mapas por-año-por-estado
// de las filas que pasan CP/Cliente activos (excepto Año, que no es un facet
// <select>) CON AL MENOS UN estado tildado + metadata de años FRÍOS no
// hidratados todavía (HOT WINDOW / COLD HISTORY: "no confundir OPCIONES con
// años HIDRATADOS" — MISMO criterio que Cartera/HV/Rentabilidad).
function comercialParticipacionAnioOptions() {
  const rows = facetRowsExcept(COMERCIAL_PARTICIPACION_ROWS, state.filters.comercial || {}, _COMERCIAL_PART_SELECT_FIELDS, "");
  const years = new Set();
  rows.forEach(r => {
    [r.vendidasByYear, r.certificadasByYear].forEach(byYear => {
      Object.entries(byYear || {}).forEach(([anio, byEstado]) => {
        if (Object.keys(byEstado || {}).some(e => state.comercialStatus[e])) years.add(anio);
      });
    });
  });
  (state.comercialParticipacionAniosDisponibles || []).forEach(p => {
    if (state.comercialStatus[p.estado]) years.add(p.anio);
  });
  // TEMPORAL DOMAIN CONTRACT: segunda línea de defensa, MISMO criterio que
  // el resto de los <select>/checkbox-group de Año del Hub.
  return [...years].filter(_isValidAnioValue).sort();
}
// Default: ventana caliente (año actual + anterior) tildada — MISMO criterio
// que `_default_hot_years` del backend, filtrado a lo que realmente resultó
// disponible (nunca un año tildado sin representación real bajo el Estado
// activo).
function comercialParticipacionDefaultAnios() {
  const current = String(new Date().getFullYear());
  const previous = String(new Date().getFullYear() - 1);
  const options = new Set(comercialParticipacionAnioOptions());
  return new Set([current, previous].filter(y => options.has(y)));
}
// Prune de selección obsoleta (MISMO criterio que pruneCarteraAnioInicio):
// al cambiar Estado u otro facet, quita de la selección los años que ya no
// son opción válida.
function pruneComercialParticipacionAnios() {
  const available = new Set(comercialParticipacionAnioOptions());
  state.comercialParticipacionAnios = new Set([...state.comercialParticipacionAnios].filter(y => available.has(y)));
}
function toggleComercialParticipacionAnio(anio) {
  if (state.comercialParticipacionAnios.has(anio)) state.comercialParticipacionAnios.delete(anio);
  else state.comercialParticipacionAnios.add(anio);
  reconcileExperienceFacets("comercial");
  render();
}
function toggleComercialStatus(key) {
  state.comercialStatus[key] = !state.comercialStatus[key];
  reconcileExperienceFacets("comercial");
  render();
}

// HUB CHART TOOLTIP PRIMITIVE (owner request — "los tooltip de Rentabilidad
// debieran ser una experiencia transversal... en asignaciones renderizamos
// un tooltip propio que amerita respetar en casos de gráficos"): MISMO
// diseño/mecanismo visual que `.staffing-timeline__tooltip` (caja flotante
// CSS puro, :hover/:focus, sin librería — ver `.chart-tooltip`/
// `.chart-tooltip-anchor` en tools.css) — reemplaza el `title` nativo del
// navegador. `aria-label` en el contenedor foco-able repite el MISMO texto
// en plano (MISMO patrón que `barAriaLabel` de Staffing) para lectores de
// pantalla; el tooltip visual queda `aria-hidden` para no duplicar el
// anuncio. `lines`: un string (una sola línea) o array de strings (una
// `<p>` por línea — MISMO patrón que Staffing separa Consultor/Estado/
// Cliente/Proyecto/Fechas en párrafos propios).
function chartTooltip(lines) {
  const arr = Array.isArray(lines) ? lines : [lines];
  return `<div class="chart-tooltip" aria-hidden="true">${arr.map(l => `<p>${esc(l)}</p>`).join("")}</div>`;
}
function rentabilidadBarRow(label, value, maxAbs) {
  const cls = value < 0 ? "negativo" : "positivo";
  const width = maxAbs ? (Math.abs(value) / maxAbs * 100).toFixed(1) : 0;
  const tooltipText = `${label} — Rentabilidad horas: ${diffPlain(value)} (${width}% del máximo del ranking)`;
  return `<div class="bar-row chart-tooltip-anchor" tabindex="0" aria-label="${esc(tooltipText)}"><span class="bar-label">${esc(label)}</span>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${width}%"></div></div>
    <span class="bar-value">${diffCell(value)}</span>
    ${chartTooltip(tooltipText)}
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
function renderRentabilidadMensual(rows, portfolio) {
  // Batch B (ver HANDOFF/real-data-lineage.md §10): `certMonthly`/
  // `trabMonthly` por proyecto son AHORA grano mensual REAL (Horas por
  // proyectos/Time Logs Reports, ambos proyecto×mes en la fuente) — se
  // suman directo, NUNCA `flatMonthly` de un escalar (esa era la
  // simplificación de V0 sin datos vivos, ya retirada).
  // NULL-AWARE (TEMPORAL DOMAIN CONTRACT): `_sumMonthlyArrays`, no un `+=`
  // ingenuo — un mes CAPADO (current-year future-month cap) en TODAS las
  // filas preserva `null` (blanco), nunca colapsa a "0" en la matriz.
  const certMonthly = _sumMonthlyArrays(rows.map(p => p.certMonthly));
  const trabMonthly = _sumMonthlyArrays(rows.map(p => p.trabMonthly));
  const vendMonthly = _sumMonthlyArrays(rows.map(p => p.vendMonthly));
  const rentMonthly = _diffMonthly(certMonthly, trabMonthly);
  // Orden real (Rentabilidad Unpivot.tmdl, columna Orden 1→5). SOURCE-PARITY
  // corrective: Horas Vendidas ahora responde a los MISMOS filtros que
  // Certificadas/Trabajadas (por-proyecto, ver rentabilidadSelectedRows) —
  // solo "No asignadas a servicios" queda a nivel portfolio fijo (Time Logs
  // de los proyectos placeholder, fuera del universo de Proyectos). Q2
  // (owner live QA): los 5 conceptos pasan por la MISMA `monthMask` de
  // Desde/Hasta — meses fuera del rango activo quedan en blanco en TODAS,
  // incluida la fija de portfolio.
  // `rows` ya vienen proyectadas al Año+Mes seleccionados (certMonthly/
  // trabMonthly/vendMonthly enmascarados en rentabilidadSelectedRows);
  // `portfolio.noAsignadas` también — nada de máscara Desde/Hasta acá.
  const conceptos = [
    { concepto: "Horas Vendidas", monthly: vendMonthly, signed: false },
    { concepto: "Horas Certificadas", monthly: certMonthly, signed: false },
    { concepto: "Horas Trabajadas", monthly: trabMonthly, signed: false },
    { concepto: "Rentabilidad horas", monthly: rentMonthly, signed: true },
    { concepto: "No asignadas a servicios", monthly: portfolio.noAsignadas, signed: false },
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

// Top 5 por default (owner feedback: el ranking completo sin acotar
// resultaba desordenado con muchos proyectos) — `+ Ver N más`/`Ver menos`
// como afordancia liviana, reusa `.dashboard-action-link` tal cual (MISMO
// lenguaje visual que "Actualizar↗", CERO CSS nuevo) en vez de inventar un
// componente de paginación.
const RENTABILIDAD_RANKING_TOP_N = 5;
function toggleRentabilidadRanking(side) {
  state.rentabilidadRankingExpanded[side] = !state.rentabilidadRankingExpanded[side];
  render();
}
function rentabilidadRankingList(items, side, maxAbs) {
  if (!items.length) return "";
  const expanded = state.rentabilidadRankingExpanded[side];
  const visible = expanded ? items : items.slice(0, RENTABILIDAD_RANKING_TOP_N);
  // `maxAbs` SIEMPRE viene del ranking COMBINADO (Mejor+Peor, ver caller) —
  // nunca recalculado por lista: las barras de ambos paneles comparten UNA
  // sola escala relativa, para que el ancho siga siendo comparable entre
  // Mejor y Peor (si cada panel escalara a su propio máximo, un proyecto
  // "peor" con rentabilidad -50 podría verse con la MISMA barra que un
  // "mejor" de +500, perdiendo la comparación real).
  const rows = visible.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("");
  const remaining = items.length - RENTABILIDAD_RANKING_TOP_N;
  const toggle = remaining > 0
    ? `<button type="button" class="dashboard-action-link" onclick="toggleRentabilidadRanking('${side}')">${expanded ? "Ver menos" : `+ Ver ${remaining} más`}</button>`
    : "";
  return rows + toggle;
}

function renderRentabilidadResumen() {
  // Gate del ciclo de vida real-data — el MAIN-TAB HYDRATION CONTRACT ya
  // corrió en `renderRentabilidad()` (dispatcher, ver ahí); acá solo se
  // verifica el status (MISMO que Certificadas vs Ejecutadas, comparten UN
  // fetch, ver HANDOFF/real-data-lineage.md §10).
  if (state.rentabilidadDataStatus !== "ready") return renderRentabilidadDataState();
  const rows = rentabilidadFilteredProyectos();
  // Horas Vendidas/Certificadas/Trabajadas/Rentabilidad horas (owner
  // feedback: los tiles quedaban estáticos al filtrar) — se recalculan de
  // `rows` YA filtradas (mismo criterio que la matriz mensual de abajo, ver
  // renderRentabilidadMensual). SOURCE-PARITY corrective: Vendidas se sumó
  // acá también (dejó de ser un escalar de portfolio, ver comentario de
  // RENTABILIDAD_NOASIGNADAS_MENSUAL arriba) — responde a Tipo Proyecto/
  // Estado/Cliente/Proyecto igual que Certificadas/Trabajadas, MISMO
  // criterio que el PBIP (slicers "Tipo Proyecto Dash"/"Status" en la
  // página "Analisis Rentabilidad" aplican a las 3 medidas por igual).
  const vendidas = rows.reduce((s, p) => s + p.vendidas, 0);
  const certificadas = rows.reduce((s, p) => s + p.certificadas, 0);
  const trabajadas = rows.reduce((s, p) => s + p.trabajadas, 0);
  const rentabilidadHoras = certificadas - trabajadas;
  const ranked = rentabilidadRanked(rows);
  const positivos = ranked.filter(p => p.rentabilidad >= 0);
  const negativos = ranked.filter(p => p.rentabilidad < 0).slice().reverse(); // más negativo primero
  const maxAbs = Math.max(...ranked.map(p => Math.abs(p.rentabilidad)), 1);

  // "No asignadas a servicios" SIGUE siendo portfolio fijo (Time Logs de
  // los proyectos placeholder, fuera del universo de Proyectos — sin Tipo/
  // Estado propios que filtrar) pero SÍ responde al filtro de FECHA como el
  // resto de la sección — antes quedaba fija al total anual completo aunque
  // Desde/Hasta acotara a unos pocos meses (misma matriz `flatMonthly` que
  // usa la sección "Detalle mensual por Concepto" de abajo, MISMA máscara).
  const portfolio = rentabilidadSelectedPortfolio();
  const noAsignadasPortfolio = portfolio.noAsignadas.reduce((a, b) => a + b, 0);

  const resumen = dashboardSection("resumen", "Resumen", "", `
    ${sectionToolbar(perspectiveSwitch("rentabilidad", false))}
    <div class="activity-kpi-grid">
      ${renderDashboardKpi({ label: "Horas Vendidas", value: vendidas, format: "hours" })}
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
        ${positivos.length ? rentabilidadRankingList(positivos, "positivos", maxAbs) : `<p class="empty-note">Sin proyectos con rentabilidad ≥ 0.</p>`}
      </div>
      <div>
        <p class="perspective-label">Peor performance</p>
        ${negativos.length ? rentabilidadRankingList(negativos, "negativos", maxAbs) : `<p class="empty-note">Sin proyectos con rentabilidad negativa.</p>`}
      </div>
    </div>
  ` : `<p class="empty-note">Sin proyectos para los filtros actuales.</p>`;
  const ranking = dashboardSection("ranking", "Ranking por Rentabilidad horas", "Certificadas − Trabajadas", rankingBody);

  const mensual = renderRentabilidadMensual(rows, portfolio);

  return resumen + ranking + mensual;
}

// `title` nativo (ver corrective — HANDOFF: "Cartera + Rentabilidad
// Completeness", B4): mismo contenido en AMBAS barras del par
// (Certificadas/Ejecutadas) del grupo — "hovering either relevant mark may
// show the complete comparison" — con el agregado del Tipo Y el desglose
// por proyecto (Hs Certificadas/Hs Ejecutadas/Margen Absoluto/Margen
// Relativo, mismas fórmulas ya aprobadas, sin etiquetas cualitativas).
function certEjecGroupTooltip(g) {
  const lines = [`${g.tipo} — Certificadas: ${fmtNum(g.certificadas, "hours")} · Ejecutadas: ${fmtNum(g.trabajadas, "hours")} · Margen Absoluto: ${diffPlain(g.margenAbsoluto)} · Margen Relativo: ${fmtNum(g.margenRelativo, "percent")}`];
  g.rows.forEach(p => {
    const ma = p.certificadas - p.trabajadas;
    const mr = p.certificadas ? (ma / p.certificadas * 100) : 0;
    lines.push(`· ${p.proyecto} (${p.tipo}) — Certificadas: ${fmtNum(p.certificadas, "hours")} · Ejecutadas: ${fmtNum(p.trabajadas, "hours")} · Margen Absoluto: ${diffPlain(ma)} · Margen Relativo: ${fmtNum(mr, "percent")}`);
  });
  return lines.join("\n");
}

function renderRentabilidadCertEjecutadas() {
  // Gate del ciclo de vida real-data — MISMO fetch que Resumen, MAIN-TAB
  // HYDRATION CONTRACT ya corrió en `renderRentabilidad()` (dispatcher).
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

  // UX (owner): Mantenimiento | Proyecto LADO A LADO — reusa `.activity-grid-2`,
  // el MISMO primitivo Hub de 2 columnas que Mejor|Peor Performance (stack
  // vertical automático en viewport angosto, misma media query). Todo valor
  // numérico pasa por `fmtNum` (sin ruido de punto flotante 1.9500…/3.599…).
  const groupBlock = (g) => {
    // HUB CHART TOOLTIP PRIMITIVE (ver rentabilidadBarRow/chartTooltip) —
    // reemplaza el `title` nativo. `certEjecGroupTooltip` sigue devolviendo
    // UN string `\n`-joined (aria-label lo necesita plano); `chartTooltip`
    // lo parte en líneas para el `<p>` por línea del tooltip visual, MISMO
    // patrón que Staffing.
    const tooltipText = certEjecGroupTooltip(g);
    const tooltipHtml = chartTooltip(tooltipText.split("\n"));
    return `
      <div class="bar-row chart-tooltip-anchor" tabindex="0" aria-label="${esc(tooltipText)}"><span class="bar-label">Certificadas</span>
        <div class="bar-track"><div class="bar-fill certificadas" style="width:${(g.certificadas / maxVal * 100).toFixed(1)}%"></div></div>
        <span class="bar-value">${fmtNum(g.certificadas, "hours")}</span>
        ${tooltipHtml}
      </div>
      <div class="bar-row chart-tooltip-anchor" tabindex="0" aria-label="${esc(tooltipText)}"><span class="bar-label">Ejecutadas</span>
        <div class="bar-track"><div class="bar-fill ejecutadas" style="width:${(g.trabajadas / maxVal * 100).toFixed(1)}%"></div></div>
        <span class="bar-value">${fmtNum(g.trabajadas, "hours")}</span>
        ${tooltipHtml}
      </div>
      <p style="margin:4px 0 0;font-size:.76rem;color:var(--ink-soft)">
        Margen Absoluto ${diffCell(g.margenAbsoluto)} · Margen Relativo ${fmtNum(g.margenRelativo, "percent")}
      </p>`;
  };
  const panel = (tipo) => {
    const g = groups.find(x => x.tipo === tipo);
    return `<div><p class="perspective-label">${esc(tipo)}</p>`
      + (g ? groupBlock(g) : `<p class="empty-note">Sin datos para los filtros actuales.</p>`)
      + `</div>`;
  };
  const analysisContent = groups.length ? `
    <p class="perspective-label" style="margin-bottom:2px">Hs Certificadas → SharePoint · Hs Ejecutadas → Time Logs</p>
    <div class="legend" style="margin-top:8px"><span class="l-certificadas">Certificadas</span><span class="l-ejecutadas">Ejecutadas</span></div>
    <div class="activity-grid-2" style="margin-top:12px">${panel("Mantenimiento")}${panel("Proyecto")}</div>
  ` : `<p class="empty-note">Sin datos para los filtros actuales.</p>`;
  const analysisBody = `${sectionToolbar(perspectiveSwitch("rentabilidad", false))}${analysisContent}`;
  const analysis = dashboardSection("analisis", "Certificadas vs Ejecutadas", "por Tipo Proyecto Dash", analysisBody);

  // Layout (owner feedback: la tabla "quedaba apretada a la derecha, con
  // demasiado margen a la izquierda") — a diferencia de TODAS las demás
  // `table.dashboard-table` de este archivo, a esta le faltaba el wrapper
  // `.matrix-scroll` (`overflow-x:auto`, reporting.css): con `table-layout:
  // fixed` + 4 columnas numéricas de ancho fijo (88px c/u, ver tools.css)
  // + Tipo compacta (100px), la columna Proyecto (única sin ancho
  // declarado) se lleva TODO el resto del ancho del contenedor — sin
  // `.matrix-scroll`, en un contenedor angosto eso fuerza el layout entero
  // a comprimirse en vez de habilitar scroll horizontal, que es el
  // comportamiento que CUALQUIER otra tabla ancha de Reporting ya tiene.
  // Corrective: mismo wrapper compartido, cero CSS nuevo/one-off.
  // Fila Total (owner request — "así como calendario tiene en su tabla al
  // final un total calculado... me gustaría incluir lo mismo en
  // rentabilidad"): MISMO criterio que la fila Total de Calendario
  // (`tr.is-total`, primitivo YA compartido en tools.css) — suma cruda de
  // Certificadas/Trabajadas sobre TODOS los proyectos listados (Mantenimiento
  // + Proyecto juntos, la MISMA población que ya alimenta esta tabla), Margen
  // Absoluto/Relativo recalculados sobre esos totales (nunca un promedio de
  // los % ya redondeados por fila).
  const totalCertificadas = rows.reduce((s, p) => s + p.certificadas, 0);
  const totalTrabajadas = rows.reduce((s, p) => s + p.trabajadas, 0);
  const totalMargenAbsoluto = totalCertificadas - totalTrabajadas;
  const totalMargenRelativo = totalCertificadas ? Math.round((totalMargenAbsoluto / totalCertificadas) * 1000) / 10 : 0;
  const detailBody = rows.length ? `
    <div class="matrix-scroll">
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
      }).join("")}
      <tr class="is-total">
        <td class="left">Total</td><td class="left"></td>
        <td>${fmtNum(totalCertificadas, "hours")}</td><td>${fmtNum(totalTrabajadas, "hours")}</td>
        <td>${diffCell(totalMargenAbsoluto)}</td><td>${diffCell(totalMargenRelativo)}%</td>
      </tr></tbody>
    </table>
    </div>
  ` : `<p class="empty-note">Sin proyectos para los filtros actuales.</p>`;
  const detail = dashboardSection("detalle", "Detalle por proyecto", rows.length + " proyecto(s)", detailBody);

  return analysis + detail;
}

function renderRentabilidad() {
  // MAIN-TAB HYDRATION CONTRACT (MISMO patrón que renderCartera/
  // renderHorasVendidas): la hidratación (ventana caliente + metadata de
  // años) la dispara el SHELL (`TAB_HYDRATION_TRIGGERS`, ver `render()` —
  // SHARED REPORTING INITIAL-LOAD CONTRACT), nunca este dispatcher.
  const anio = getFilter("rentabilidad", "anio");
  if (anio && !rentabilidadLoadedYears().has(anio) && state.rentabilidadYearError !== anio) {
    loadRentabilidadColdYear(anio);
    return dashboardSections("reporting-rentabilidad", renderRentabilidadDataState());
  }
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
// "Expandir"/"Contraer" (Hub Dashboard Contextual View Control corrective,
// ver HANDOFF) — MISMO texto y MISMO orden que Staffing (ver
// staffing_dashboard.html): estos botones afectan la jerarquía COMPLETA,
// no solo la fila actual. Owner corrective (2da pasada, Horas Productivas
// — "cambiemos los textos... para que haya mas espacio"): "todo" se
// retiró del rótulo — el toolbar de Horas Productivas es el más apretado
// del Hub (dos paneles a mitad de ancho, Año+12 botones de Mes+Expandir+
// Contraer en UNA sola fila), pero el texto es COMPARTIDO por las 6
// experiencias de Reporting Y Staffing (MISMO criterio de consistencia
// Hub-wide que ya regía "Expandir todo"/"Contraer todo") — se acortó en
// AMBOS lados, nunca solo acá.
function treeToolbarButtons(stateKey) {
  return `<button type="button" onclick="expandAllTree('${stateKey}')">Expandir</button>
    <button type="button" onclick="collapseAllTree('${stateKey}')">Contraer</button>`;
}
// `.staffing-timeline-toolbar` — MISMA clase que el "Expandir/Contraer"
// de Staffing (ver staffing_dashboard.html), reusada tal cual (Align
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
// NULL-AWARE (ver TEMPORAL DOMAIN CONTRACT): reusa `_sumMonthlyArrays` en
// vez de un `+=` ingenuo — un índice de mes CAPADO (owner: current-year
// future-month cap) en TODAS las filas contribuyentes preserva `null` en
// el total (nunca colapsa a 0), así el Total/rollup de CUALQUIER nodo del
// árbol (Calendario/Capacidad-No-Asignadas/Horas Vendidas — todos pasan
// por acá) renderiza "—" para un mes no aplicable, nunca "0". Sin filas
// con `null` (Calendario/Capacidad, que nunca lo usan), el comportamiento
// es IDÉNTICO al de siempre (`(totals[i]||0)+v` con `totals[i]` empezando
// en `null` es matemáticamente igual a empezar en 0).
function sumMonthly(rows, monthMask) {
  return maskMonthly(_sumMonthlyArrays(rows.map(r => r.monthly)), monthMask);
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
// `comparators` (owner corrective — "la tabla frecuencia no ordena por ID
// Frecuencia igual al PBIP"): mapa OPCIONAL `{ key: compareFn }`. La
// primera pasada de este corrective solo reordenó las OPCIONES del <select>
// de Frecuencia (`facetOptions`/`distinctValues`, que sí acepta compareFn) —
// nunca tocó el orden de los GRUPOS del árbol en sí, que `buildTree`
// determina por orden de PRIMERA APARICIÓN en `rows` (un `Map` preserva
// orden de inserción), ciego a cualquier criterio de negocio. Default
// `{}` (ningún caller existente lo pasaba) preserva EXACTO el
// comportamiento de siempre para Cartera/Capacidad/Comercial — SOLO
// Calendario ahora pasa `{ frecuencia: _frecuenciaCompare }`.
function buildTree(rows, keys, path = [], comparators = {}) {
  if (keys.length === 0) return [];
  const [key, ...restKeys] = keys;
  const groups = new Map();
  rows.forEach(r => {
    const val = r[key];
    if (!groups.has(val)) groups.set(val, []);
    groups.get(val).push(r);
  });
  let entries = [...groups.entries()];
  if (comparators[key]) entries = entries.sort((a, b) => comparators[key](a[0], b[0]));
  return entries.map(([val, groupRows]) => {
    const nodePath = [...path, val];
    return {
      // "Sin dato" (owner live QA — Participación real mostraba filas de
      // jerarquía con TEXTO VACÍO, ver corrective: los 3 niveles superiores
      // de Participación — ID Frecuencia/Permite Adelanto/Condición de
      // Pago — son opcionales en la fuente real, a diferencia del mock
      // donde SIEMPRE estaban poblados). `pathKey`/agrupación siguen
      // usando el valor CRUDO (`val`, incluso vacío) — filas sin dato
      // agrupan correctamente entre sí, esto SOLO cambia qué texto ve el
      // usuario en el nodo, nunca la agrupación/el conteo.
      label: (val === "" || val === null || val === undefined) ? "Sin dato" : val,
      pathKey: nodePath.join("|"),
      rows: groupRows,
      children: buildTree(groupRows, restKeys, nodePath, comparators),
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
  // `title` = nombre COMPLETO (owner live QA — Horas Vendidas: nombres de
  // proyecto largos que exceden el ancho reservado de la columna de label se
  // recortan con ellipsis; el tooltip los deja leer completos al hover). El
  // ancho reservado ya vive en tools.css (`th.row-label-col`), esto es la red
  // para la cola de nombres larguísimos. `esc` aplica también al atributo.
  // `esc` sólo cubre &<> — para un ATRIBUTO hay que escapar la comilla doble
  // también (un nombre con `"` rompería el `title` si no).
  const titleAttr = ` title="${esc(label).replace(/"/g, "&quot;")}"`;
  if (!hasChildren) {
    return `<span class="dashboard-table-toggle dashboard-table-toggle--leaf"${titleAttr}>${caretHtml}${esc(label)}</span>`;
  }
  const escapedPath = esc(pathKey).replace(/'/g, "\\'");
  return `<button type="button" class="dashboard-table-toggle"${titleAttr} onclick="toggleTreeNode('${stateKey}', '${escapedPath}')" aria-expanded="${isOpen}">${caretHtml}${esc(label)}</button>`;
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
          // Formateo transversal (owner live QA — artefacto crudo de punto
          // flotante visible en Horas Productivas, ej. "3.5999999999999996"):
          // `sumField`/`render` de columnas de horas puras (Trabajadas/
          // Certificadas/Vendidas/Facturadas/Horas — ver CARTERA_CONFIG/
          // CAPACIDAD_PRODUCTIVAS_COLUMNS) devuelven el NÚMERO crudo a
          // propósito (`sumField` sin `formatFn`) — ese mismo raw number lo
          // consumen los tiles KPI vía `renderDashboardKpi`/`fmtNum`, que
          // formatea UNA sola vez (pasarlo ya formateado ahí reproduciría el
          // bug de doble-formateo de la corrective S.3, "Diff Hs KPI en
          // blanco"). Acá, en cambio, el número crudo llega directo al DOM
          // sin pasar por ningún formateador — el boundary correcto para
          // cerrar la clase de bug ENTERA (transversal a cualquier columna
          // futura, no solo Horas Productivas) es ACÁ: cualquier `content`
          // que siga siendo un `number` (columnas ya formateadas como
          // diff/status/porcentaje devuelven string, nunca entran acá) se
          // formatea recién en el último paso antes de tocar el HTML.
          // `c.format` (owner request, Horas Productivas — ver
          // capacidadProductivasColumns): opcional, default "hours" (MISMO
          // comportamiento de siempre para cualquier columna que no lo
          // declare — Cartera/Rentabilidad/Horas Vendidas/Participación
          // nunca lo setean, cero cambio para ellas).
          if (typeof content === "number") content = fmtNum(content, c.format || "hours");
          return `<td class="${c.statusTerminal ? "dashboard-table-cell--status" : (c.numeric ? "" : "left")}">${content}</td>`;
        }).join("")}
      </tr>`;
    const childrenHtml = (hasChildren && isOpen) ? renderBusinessTreeRows(stateKey, node.children, depth + 1, columns) : "";
    return rowHtml + childrenHtml;
  }).join("");
}

function renderHorasVendidas() {
  // Gate del ciclo de vida PROPIO de HV (scope temporal — ver
  // loadHorasVendidasData). El trigger de hidratación (ventana caliente +
  // metadata de años en paralelo) vive en el SHELL (`TAB_HYDRATION_TRIGGERS`,
  // ver `render()` — SHARED REPORTING INITIAL-LOAD CONTRACT); acá solo el
  // gate — NO carga batch-a (Participación no se toca al visitar HV).
  if (state.horasVendidasDataStatus !== "ready") return renderHorasVendidasDataState();
  // Año FRÍO seleccionado que aún no está hidratado ⇒ lazy-load y estado de
  // carga (single-flight por año). La ventana caliente ya hidratada se
  // preserva (mergeHorasVendidasRows).
  const anioSel = getFilter("horasvendidas", "anio");
  if (anioSel && !horasVendidasLoadedYears().has(anioSel) && state.horasVendidasYearError !== anioSel) {
    loadHorasVendidasColdYear(anioSel);
    return renderHorasVendidasDataState();
  }
  const persp = state.perspective.horasvendidas;
  const hierarchy = HORASVENDIDAS_HIERARCHY[persp];
  // Población canónica ya proyectada al Año+Mes seleccionados (Estado + selects);
  // cada fila trae `monthly[12]` del año elegido, enmascarado por Mes. Sin
  // máscara Desde/Hasta — TODA la sección (matriz, KPIs, Q1-Q4) usa esta misma
  // población. (Migración Año/Mes/Estado, HANDOFF §-16 #8.)
  const rows = horasVendidasSelectedRows();
  const stateKey = "horasvendidas::" + persp;
  const tree = buildTree(rows, hierarchy.keys);
  registerTreeContext(stateKey, rows, hierarchy.keys);
  const totalMonthly = sumMonthly(rows);

  // "Hs Estimadas anuales" = total de Hs Vendidas del período seleccionado
  // (= Mantenimiento + Proyecto = ΣQ). Interpretación B (SUM sensible a filtros),
  // preservada del contrato V0 — NO es Proyectos.HorasEstimadasalCierredeProyecto
  // (columna distinta, no cableada a este KPI).
  const annual = totalMonthly.reduce((s, v) => s + v, 0);
  const mant = rows.filter(r => r.tipo === "Mantenimiento").reduce((s, r) => s + r.monthly.reduce((a, b) => a + b, 0), 0);
  const proy = rows.filter(r => r.tipo === "Proyecto").reduce((s, r) => s + r.monthly.reduce((a, b) => a + b, 0), 0);
  // NULL-AWARE (TEMPORAL DOMAIN CONTRACT): un trimestre ENTERO capado (ej.
  // Q4 en Agosto-2026) resuelve a `null` ("no contribution / not
  // applicable", renderDashboardKpi ya lo muestra como "—") — nunca un "0"
  // numérico que sugeriría una medición real de cero.
  const quarter = q => _quarterValue(totalMonthly, q);

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
          ${renderTreeRows(stateKey, tree, 0, false, undefined)}
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
  // Gate del ciclo de vida PROPIO de Participación (scope temporal — ver
  // loadComercialParticipacionData). La hidratación YA corrió en
  // renderComercial() (dispatcher, MAIN-TAB HYDRATION CONTRACT) — acá solo
  // se verifica el status, nunca se dispara un load propio.
  if (state.comercialParticipacionDataStatus !== "ready") return renderParticipacionDataState();
  // Años tildados que todavía no están hidratados/en vuelo/errados ⇒
  // lazy-load bundleado (HOT WINDOW / COLD HISTORY, Año MULTI-SELECT) — la
  // ventana caliente + los años fríos ya mergeados se PRESERVAN
  // (mergeComercialParticipacionRows).
  const missingYears = [...state.comercialParticipacionAnios].filter(y =>
    !state.comercialParticipacionLoadedYears.has(y)
    && !state.comercialParticipacionYearErrors.has(y)
    && !state.comercialParticipacionYearLoading.has(y));
  if (missingYears.length) {
    loadComercialParticipacionColdYears(missingYears);
    return renderParticipacionDataState();
  }
  // Header clickeable de % Participación (owner request — MISMO patrón que
  // Horas/% Incidencia de Horas Productivas): reordena las filas HOJA antes
  // de `buildTree` (ver `comercialParticipacionSorted`), nunca re-agrega ni
  // re-filtra.
  const rows = comercialParticipacionSorted(facetFilteredRows("comercial"));
  const keys = ["idFrecuencia", "permiteAdelanto", "condicionPago", "cliente"];
  const stateKey = "comercial::Participación";
  const tree = buildTree(rows, keys);
  registerTreeContext(stateKey, rows, keys);

  const sortHeader = (key, label) => {
    const active = state.comercialParticipacionSortKey === key;
    const arrow = active ? (state.comercialParticipacionSortDir === "asc" ? " ▲" : " ▼") : "";
    return `<th class="dashboard-table-th--sortable" onclick="toggleComercialParticipacionSort('${key}')" title="Ordenar por ${esc(label)}">${esc(label)}${arrow}</th>`;
  };

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("comercial", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="dashboard-table">
        <thead><tr>
          <th class="row-label-col">Frecuencia / Permite Adelanto / Condición de Pago / Cliente</th>
          ${sortHeader("vendidas", COMERCIAL_PARTICIPACION_COLUMNS[0].label)}${sortHeader("certificadas", COMERCIAL_PARTICIPACION_COLUMNS[1].label)}
        </tr></thead>
        <tbody>
          ${renderBusinessTreeRows(stateKey, tree, 0, COMERCIAL_PARTICIPACION_COLUMNS)}
          <tr class="is-total"><td class="row-label">Total</td>${COMERCIAL_PARTICIPACION_COLUMNS.map(c => `<td>${c.rollup(rows)}</td>`).join("")}</tr>
        </tbody>
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
  // La hidratación YA corrió en renderComercial() (dispatcher) — acá solo
  // se verifica el status, nunca se dispara un load propio.
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
  // MAIN-TAB HYDRATION CONTRACT (MISMO patrón que renderCartera/
  // renderRentabilidad/renderHorasVendidas): el trigger de AMBOS ciclos de
  // vida — Participación (scope temporal propio) Y Salud de Cliente
  // (categórica pura, backbone full-history de batch-a) — vive en el SHELL
  // (`TAB_HYDRATION_TRIGGERS`, ver `render()` — SHARED REPORTING INITIAL-
  // LOAD CONTRACT), nunca este dispatcher.
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
  // Gate del ciclo de vida real-data Batch A — el trigger vive en el SHELL
  // (`TAB_HYDRATION_TRIGGERS`, ver `render()`), acá solo el gate.
  if (state.batchADataStatus !== "ready") return renderBatchADataState("los datos de Calendario");
  const persp = state.perspective.calendario;
  const hierarchy = CALENDARIO_HIERARCHY[persp];
  const rows = facetFilteredRows("calendario");
  const stateKey = "calendario::" + persp;
  // `{ frecuencia: _frecuenciaCompare }` (owner corrective — el fix previo
  // solo reordenaba las OPCIONES del <select> de Frecuencia, nunca los
  // GRUPOS de la tabla en sí, ver comentario de `buildTree`): aplica en
  // AMBAS perspectivas — "Frecuencia" (nivel 1, ordena los grupos raíz) Y
  // "Client Partner" (nivel 3, ordena los hijos de cada Cliente) — mismo
  // criterio cronológico real en cualquier profundidad de la jerarquía.
  const tree = buildTree(rows, hierarchy.keys, [], { frecuencia: _frecuenciaCompare });
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
  const detalle = dashboardSection("detalle", persp, rows.length + " fila(s)", detalleBody);

  return dashboardSections("reporting-calendario", resumen + detalle);
}

// --- Capacidad/Productivas Año/Mes — PANEL-INDEPENDIENTE (owner corrective:
// "Preserve source-faithful panel-independent interaction contract" —
// evidencia PRIMARIA confirmó que el PBI original tiene DOS PARES de
// slicers Año/Mes independientes, uno posicionado sobre cada panel, ver
// real-data-lineage.md §8ter item 1. La simplificación previa a un único
// Año/Mes COMPARTIDO no estaba autorizada — se retira acá. "Shared
// implementation != shared state": las funciones de abajo son la MISMA
// implementación del TEMPORAL DOMAIN CONTRACT (`_operationalMonthCap`/
// `_applyMonthCap`/`_validMesOptions`/`_sumMonthlyArrays`) que
// Rentabilidad/HV/Cartera — nunca duplicada — pero PARAMETRIZADAS por
// `panel` ("cliente" | "clientePart"), cada una leyendo/escribiendo su
// PROPIO estado (Año: key `anio_<panel>` en `state.filters.capacidad`; Mes:
// multi-select, Set propio en `state.capacidadMesesSeleccionados[panel]`,
// ver `_maskMeses` — nunca el mismo estado para ambos paneles). SIN hot/cold (el CSV completo de
// Time Logs ya se carga entero) — Año SÍ ofrece "Todos" (colapsa todos los
// años vía `_sumMonthlyArrays`), MISMO criterio que Cartera/Mantenimiento,
// a diferencia de Rentabilidad/HV que lo retiraron junto con el hot-window.
const CAPACIDAD_PRODUCTIVAS_PANELS = ["cliente", "clientePart"];
function capacidadPanelAnioKey(panel) { return `anio_${panel}`; }
// Opciones de Año — SIN dependencia de panel (años distintos presentes en
// el dataset, compartido entre ambos <select> por construcción: ambos
// paneles ofrecen el MISMO rango de años seleccionables, aunque cada uno
// elige el suyo de forma independiente).
// Facets <select> de Capacidad/Productivas (Cliente/Proyecto) — para
// computar las opciones de Año de forma faceteada, MISMO criterio que
// _RENT_SELECT_FIELDS/_CARTERA_SELECT_FIELDS.
const _CAPACIDAD_SELECT_FIELDS = [{ key: "cliente", field: "cliente" }, { key: "proyecto", field: "proyecto" }];
// Owner corrective — filtro de Estado faltante: los Años ofrecidos ahora
// respetan Estado + Cliente/Proyecto activos, MISMO criterio que
// rentabilidadAnioOptions/horasVendidasAnioOptions ("como en las demas
// tabs").
function capacidadProductivasAnioOptions() {
  const statusRows = CAPACIDAD_PRODUCTIVAS_ROWS.filter(r => state.capacidadStatus[r.estado]);
  const rows = facetRowsExcept(statusRows, state.filters.capacidad || {}, _CAPACIDAD_SELECT_FIELDS, "");
  const years = new Set();
  rows.forEach(r => Object.keys(r.horasByYear || {}).forEach(y => years.add(y)));
  return [...years].filter(_isValidAnioValue).sort();
}
// Default Año = año actual válido (owner corrective) — MISMO criterio que
// rentabilidadDefaultAnio/horasVendidasDefaultAnio, aplicado UNA vez por
// panel en la primera hidratación (ver loadBatchBData), nunca pisa una
// elección ya hecha por el usuario.
function capacidadProductivasDefaultAnio() {
  const anioActual = String(new Date().getFullYear());
  const options = capacidadProductivasAnioOptions();
  if (options.includes(anioActual)) return anioActual;
  return options.length ? options[options.length - 1] : "";
}
// Estado de Horas Productivas — MISMO patrón que toggleCarteraStatus/
// toggleRentabilidadStatus: participa del mismo contexto de faceting que
// Cliente/Proyecto (ver FACET_CONFIG.capacidad) y de qué Años aparecen
// como opción (capacidadProductivasAnioOptions).
function toggleCapacidadStatus(key) {
  state.capacidadStatus[key] = !state.capacidadStatus[key];
  reconcileExperienceFacets("capacidad");
  render();
}
// Prune (MISMO criterio que pruneRentabilidadAnio, un panel a la vez): si
// el año elegido de ESE panel deja de ser válido, cae al default SENSATO
// (año actual válido, owner corrective — nunca a blanco/"Todos"), y NUNCA
// toca la key del otro panel.
function pruneCapacidadProductivasAnio(panel) {
  const key = capacidadPanelAnioKey(panel);
  const available = new Set(capacidadProductivasAnioOptions());
  const anio = (state.filters.capacidad || {})[key];
  if (anio && !available.has(anio)) state.filters.capacidad[key] = capacidadProductivasDefaultAnio();
}
// Prune de Mes (multi-select, owner corrective — MISMO criterio que
// `pruneCarteraAnioInicio`): al cambiar de Año, los meses tildados que
// dejan de ser válidos bajo el cap de mes futuro de ESE Año se retiran de
// la selección — nunca queda un mes futuro tildado ni se auto-tilda otro.
function pruneCapacidadProductivasMeses(panel) {
  const anioKey = capacidadPanelAnioKey(panel);
  const anio = getFilter("capacidad", anioKey);
  const valid = new Set(_validMesOptions(anio).map(m => m.value));
  const set = state.capacidadMesesSeleccionados[panel];
  state.capacidadMesesSeleccionados[panel] = new Set([...set].filter(m => valid.has(m)));
}
// Población canónica de UN panel: Estado (owner corrective, MISMO scope
// real que Año/Mes — ver comentario de capacidadProductivasTotal abajo) +
// Año+Mes(es) de ESE panel proyectados a `horas` (recalculado desde
// `horasByYear`, nunca el `horas` colapsado retrocompat) — alimenta ese
// panel Y su propio gran total fijo. Cliente/Proyecto (los <select>
// compartidos, ver renderCapacidadFilterBar) se aplican DESPUÉS, igual en
// ambos paneles (ver renderCapacidadProductivas) — esos SÍ son
// REMOVEFILTERS, nunca escopean el total. Año="" (Todos) colapsa TODOS los
// años presentes (`_sumMonthlyArrays`), MISMO motor null-aware que
// Rentabilidad/HV — sin cap de mes futuro (`_operationalMonthCap("")` =
// null, "Todos" no es "el año actual").
function capacidadProductivasSelectedRows(panel) {
  const anioKey = capacidadPanelAnioKey(panel);
  const anio = getFilter("capacidad", anioKey);
  const meses = state.capacidadMesesSeleccionados[panel];
  const cap = _operationalMonthCap(anio);
  return CAPACIDAD_PRODUCTIVAS_ROWS.filter(r => state.capacidadStatus[r.estado]).map(r => {
    const arr = anio ? ((r.horasByYear || {})[anio] || _z12()) : _sumMonthlyArrays(Object.values(r.horasByYear || {}));
    const monthly = _applyMonthCap(_maskMeses(arr, meses), cap);
    return { ...r, horas: monthly.reduce((a, b) => a + (b || 0), 0) };
  });
}

// Gran total FIJO de UN panel — DENOMINADOR REAL de "% Incidencia" —
// SOURCE-PARITY corrective (owner live comparison: LEFT PBI ≈50.5 vs KPI
// visible 42.182 para el MISMO Cliente/Año — ver evidencia PRIMARIA en
// `_shape_horas_productivas_denominador`, reporting_service.py).
//
// ANTES: sumaba `capacidadProductivasSelectedRows(panel)` — las MISMAS
// filas que alimentan la tabla/KPI (Cliente-placeholder interno YA
// excluido en el backend, Estado ya filtrado). Eso reproduce el "Total
// Horas" VISIBLE, NO el denominador real de la medida DAX — evidencia
// PRIMARIA (`Time Logs Reports.tmdl`, medidas `% Hs ejecutadas`/`% Hs
// ejecutadas2`) prueba que el denominador es:
//     DIVIDE(SUM(HORAS), CALCULATE(SUM(HORAS),
//       REMOVEFILTERS(Cliente[Client Partner], Cliente[Frecuencia
//         Actualizacion], Cliente[Permite Adelanto], Cliente[Condicion de
//         Pago], Cliente[Cliente]),
//       REMOVEFILTERS('Time Logs Reports'[<propia columna de identidad>])))
// — que REMUEVE (entre otros) el filtro de página que excluye el cliente
// placeholder interno de "No Asignadas" (mismo campo, `Cliente[Cliente]`/
// `'Time Logs Reports'[Client]`), REINTRODUCIENDO esas horas al
// denominador — la causa RAÍZ exacta del ≈50.5 vs 42.182 (ver HANDOFF).
// REMOVEFILTERS NUNCA toca Año/Mes/Proyecto — el denominador SIGUE
// escopeado por Año/Mes de ESE panel (por eso reusa el MISMO motor
// `_sumMonthlyArrays`/`_maskMeses`/`_applyMonthCap` que
// `capacidadProductivasSelectedRows`, nunca una copia), pero es AGNÓSTICO
// de Cliente/Client Partner/Proyecto/Estado — ninguno de estos tiene
// slicer/filtro equivalente en la página real del PBIP (confirmado por
// `page.json`: solo hay slicers de Año/Mes/Cliente/Client Partner, CERO
// de Proyecto; Estado no existe en el modelo original en absoluto).
// FUNCIÓN, no `const` precalculada (mismo corrective que
// comercialParticipacionTotalVendidas) — cada panel tiene su PROPIO gran
// total vía su PROPIO Año/Mes, nunca uno compartido.
function capacidadProductivasTotal(panel) {
  const anioKey = capacidadPanelAnioKey(panel);
  const anio = getFilter("capacidad", anioKey);
  const meses = state.capacidadMesesSeleccionados[panel];
  const cap = _operationalMonthCap(anio);
  const arr = anio
    ? (CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR[anio] || _z12())
    : _sumMonthlyArrays(Object.values(CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR));
  const monthly = _applyMonthCap(_maskMeses(arr, meses), cap);
  return monthly.reduce((a, b) => a + (b || 0), 0);
}

// Rollup jerárquico (ver Hierarchical Measure Rollup Backbone corrective):
// Horas es Clase A (aditiva, `sumField` sin formatFn); % Incidencia es
// Clase B contra el total FIJO del panel (`sumField` con un formatFn
// cerrado sobre `capacidadProductivasTotal(panel)`). FUNCIÓN (no un array
// `const` compartido, a diferencia de antes): cada panel necesita su
// PROPIO total en el cierre — MISMA definición de columnas/measure family
// para ambos paneles (Horas/% Incidencia, ver §8ter item 1: "Cliente +
// Part" reusa el MISMO Rank/Horas/%Incidencia), solo el total contra el
// que se divide difiere por panel.
function capacidadProductivasColumns(panel) {
  const total = capacidadProductivasTotal(panel);
  // Precisión (owner request): Horas Productivas muestra más decimales que
  // el resto del Hub — PBIP también lo hace (ej. 5.422 vs 5.4 en otras
  // experiencias), ver `hours3` en fmtNum. % Incidencia a 2 decimales
  // (antes 1) — este `pct` ya construye su propio string, nunca pasa por
  // fmtNum, así que el cambio queda 100% local a esta función.
  const pct = horas => (total ? (horas / total * 100) : 0).toFixed(2) + "%";
  return [
    { label: "Horas", numeric: true, format: "hours3", render: r => r.horas, rollup: sumField("horas") },
    { label: "% Incidencia", numeric: true, render: r => pct(r.horas), rollup: sumField("horas", pct) },
  ];
}

// Año/Mes de UN panel — MISMA marca `<select>`/`.whatsnew-filter` que el
// resto del Hub (`carteraAnioField`/`horasVendidasAnioField`), pero
// montados DENTRO del toolbar de CADA panel (ver renderCapacidadProductivas)
// en vez del filter bar compartido — posición fiel al PBIP original (dos
// pares de slicers, uno sobre cada visual), no una elección arbitraria.
function capacidadProductivasAnioField(panel) {
  const key = capacidadPanelAnioKey(panel);
  const current = getFilter("capacidad", key);
  const options = capacidadProductivasAnioOptions();
  return `<label class="whatsnew-filter whatsnew-filter--narrow">
    <span>Año</span>
    <select onchange="setFilter('capacidad','${key}', this.value)">
      <option value="" ${current === "" ? "selected" : ""}>Todos</option>
      ${options.map(a => `<option value="${esc(a)}" ${current === a ? "selected" : ""}>${esc(a)}</option>`).join("")}
    </select>
  </label>`;
}
// Mes de UN panel — BOTONES multi-select (owner live QA corrective: el
// PBIP original permite tildar VARIOS meses a la vez sobre el visual vía
// una fila de botones, un `<select>` de valor único no lo representa —
// "si no es muy complejo" pidió emularlo con un botón cuadrado minimalista
// por mes, claro cuando inactivo, oscuro/dorado cuando tildado). Reusa el
// primitivo COMPARTIDO `.dashboard-view-switch` (mismo look que el
// perspective-switch — MISMO look "claro inactivo / dorado activo" que ya
// pedía el owner) con el modifier `--multi`, que solo relaja `border-right`
// para que cualquier botón (no solo el último) pueda leerse activo sin una
// costura visual — NUNCA una clase de botón nueva. `_validMesOptions(anio)`
// (MISMO domain-aware cap de mes futuro que Rentabilidad/HV) decide qué
// meses son opciones válidas bajo el Año de ESE panel.
function capacidadProductivasMesButtons(panel) {
  const anioKey = capacidadPanelAnioKey(panel);
  const anio = getFilter("capacidad", anioKey);
  const selected = state.capacidadMesesSeleccionados[panel];
  // Owner corrective — "que la posicion del dropdown de año no cambie de
  // lugar": SIEMPRE los 12 botones en el DOM (nunca `_validMesOptions`
  // recortando la lista) — con `.staffing-timeline-toolbar` alineado a la
  // derecha (`justify-content: flex-end`), un ancho de grupo VARIABLE
  // (menos botones bajo el año en curso) corría el Año hacia la derecha
  // cada vez que cambiaba de año. Ancho CONSTANTE = posición constante. Los
  // meses futuros (más allá del cap operativo del Año elegido) quedan
  // DESHABILITADOS en su lugar en vez de desaparecer — mismo dato, misma
  // estructura, solo no clickeables.
  const cap = _operationalMonthCap(anio);
  return `<label class="whatsnew-filter whatsnew-filter--compact">
    <span>Mes</span>
    <div class="dashboard-view-switch dashboard-view-switch--multi">
      ${MESES_LARGOS.map((nombre, i) => {
        const value = String(i + 1);
        const isFuture = cap && i >= cap;
        return `<button type="button" class="${selected.has(value) ? "is-active" : ""}" ${isFuture ? "disabled" : ""} onclick="toggleCapacidadMes('${panel}','${value}')" title="${esc(nombre)}">${esc(MESES[i])}</button>`;
      }).join("")}
    </div>
  </label>`;
}
function toggleCapacidadMes(panel, mes) {
  const set = state.capacidadMesesSeleccionados[panel];
  if (set.has(mes)) set.delete(mes); else set.add(mes);
  render();
}
// Orden clickeable de Horas/% Incidencia (owner live QA) — togglea
// asc↔desc, PURO UI (los datos ya están cargados, nunca dispara un fetch).
function toggleCapacidadProductivasSort(panel) {
  state.capacidadProductivasSortDir[panel] = state.capacidadProductivasSortDir[panel] === "asc" ? "desc" : "asc";
  render();
}

// DOS paneles simultáneos, cada uno con su PROPIO Año/Mes (owner corrective
// — ver bloque de arriba) — evidencia PRIMARIA confirmó que "Cliente + Part"
// reusa el MISMO Rank/Horas/%Incidencia del panel izquierdo, solo con un
// rótulo de fila enriquecido con el % de referencia ESTÁTICO de cada
// cliente (real-data-lineage.md §8ter item 1). `.activity-grid-2` (MISMO
// primitivo compartido que Rentabilidad Mejor/Peor) — nunca un grid nuevo.
// Cliente (el ÚNICO <select> del filter bar compartido) se aplica IGUAL a
// ambos paneles — nunca duplicado, eso NO forma parte del corrective de
// panel-independencia (solo Año/Mes lo hacen, ver ticket).
function renderCapacidadProductivas() {
  // Gate del ciclo de vida real-data Batch B — Correction 1 CERRADA:
  // Productivas es CSV de Time Logs, no backbone de Cartera (ver HANDOFF/
  // real-data-lineage.md §8bis). El trigger vive en el SHELL
  // (`TAB_HYDRATION_TRIGGERS`, ver `render()`), acá solo el gate.
  if (state.batchBDataStatus !== "ready") return renderBatchBDataState("los datos de Horas Productivas");

  const clienteFilter = getFilter("capacidad", "cliente");
  // Proyecto (owner corrective) — REMOVEFILTERS, MISMO criterio que
  // Cliente: narrows las filas MOSTRADAS, nunca el gran total fijo (ese
  // vive en capacidadProductivasTotal, que no conoce Cliente/Proyecto).
  const proyectoFilter = getFilter("capacidad", "proyecto");
  // Orden por Horas de Cliente == el Rank real del PBIP (RANKX por Horas),
  // representado vía orden visual — nunca una columna Rank redundante con
  // el orden. Descendente por default (Rank original); CLICKEABLE en los
  // headers Horas/% Incidencia de la tabla (owner live QA — ver
  // `toggleCapacidadProductivasSort`/`capacidadProductivasSortDir`).
  // Calculado POR PANEL (cada uno con su propia población Año/Mes-escopeada
  // Y su propia dirección de orden, pueden divergir).
  const rowsFor = panel => {
    const rows = capacidadProductivasSelectedRows(panel)
      .filter(r => !clienteFilter || r.cliente === clienteFilter)
      .filter(r => !proyectoFilter || r.proyecto === proyectoFilter);
    const clientTotals = new Map();
    rows.forEach(r => clientTotals.set(r.cliente, (clientTotals.get(r.cliente) || 0) + r.horas));
    const sign = state.capacidadProductivasSortDir[panel] === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => sign * (clientTotals.get(a.cliente) - clientTotals.get(b.cliente)));
  };

  const rowsCliente = rowsFor("cliente");
  const stateKeyCliente = "capacidad::Productivas::Cliente";
  const treeCliente = buildTree(rowsCliente, ["cliente", "proyecto"]);
  registerTreeContext(stateKeyCliente, rowsCliente, ["cliente", "proyecto"]);

  // Rótulo compuesto "{Cliente} {Porcentaje de Part.}%" — NUNCA una segunda
  // agregación (mismo motor `rowsFor`, agrupadas por un campo distinto).
  const rowsPart = rowsFor("clientePart").map(r => ({ ...r, clientePart: `${r.cliente} ${fmtNum(r.porcentajePart, "percent")}` }));
  const stateKeyPart = "capacidad::Productivas::ClientePart";
  const treePart = buildTree(rowsPart, ["clientePart", "proyecto"]);
  registerTreeContext(stateKeyPart, rowsPart, ["clientePart", "proyecto"]);

  const totalHorasCliente = rowsCliente.reduce((s, r) => s + r.horas, 0);
  const totalHorasPart = rowsPart.reduce((s, r) => s + r.horas, 0);

  // COMPOSITION CORRECTIVE (owner live QA — "the two analytical units do
  // not visually read as two equivalent panels"): cada panel es ahora una
  // TARJETA AUTOCONTENIDA — título → controles temporales + acciones de
  // jerarquía → KPI Total Horas → tabla — en vez de un Total Horas
  // "desprendido" arriba de ambos y Año/Mes flotando sobre la tabla.
  // Reusa `.dashboard-section`/`.dashboard-section__body` (borde/radio/
  // padding YA existentes, ver tools.css) en un <div> PLANO — NUNCA
  // `<details>`/`draggable="true"`/`data-section-id` — visualmente una
  // tarjeta, pero jamás capturada por el drag-and-drop de secciones
  // (`initDashboardSections` filtra por `.dashboard-section__head`, que
  // este panel no usa — ver `.perspective-label` como título en su lugar,
  // MISMO primitivo ya usado por Rentabilidad Mejor/Peor). AMBOS paneles
  // comparten ESTA MISMA función — nunca una estructura DOM bespoke por
  // lado, la única diferencia es contenido de negocio (título/columna).
  // Header clickeable de Horas/% Incidencia (owner live QA: "falta que los
  // header de las columnas sean clickeables para ordenar ascendente/
  // descendente"). Horas y % Incidencia comparten UN solo estado de
  // dirección por panel (ver comentario de `capacidadProductivasSortDir`
  // en `state` — % es transform monótono de Horas, ordenar por cualquiera
  // de las dos produce el MISMO orden) — clickear cualquiera de las dos
  // togglea la MISMA dirección, la flecha se muestra en ambas.
  const sortHeader = (panelKey, label) => {
    const arrow = state.capacidadProductivasSortDir[panelKey] === "asc" ? "▲" : "▼";
    return `<th class="dashboard-table-th--sortable" onclick="toggleCapacidadProductivasSort('${panelKey}')" title="Ordenar por ${esc(label)}">${esc(label)} ${arrow}</th>`;
  };
  // Toolbar de panel: Año | Mes | acciones de jerarquía como TRES hijos
  // directos del grid `.staffing-timeline-toolbar--panel` (owner corrective
  // — "FINAL VISUAL COMPOSITION"). Ya NO pasa por `sectionToolbar` (esa
  // función CONCATENA sus partes en un solo blob — acá cada parte necesita
  // ser su PROPIO hijo de grid para caer en su propia columna auto/1fr/
  // auto). Las acciones (2 `<button>` sueltos) se envuelven en UN `<div>`
  // para que caigan JUNTAS en la columna derecha, nunca cada botón en su
  // propia columna.
  const panelToolbar = (panelKey, stateKey) => `
    <div class="staffing-timeline-toolbar staffing-timeline-toolbar--panel">
      ${capacidadProductivasAnioField(panelKey)}
      ${capacidadProductivasMesButtons(panelKey)}
      <div class="staffing-timeline-toolbar__actions">${treeToolbarButtons(stateKey)}</div>
    </div>`;
  const panel = (panelKey, stateKey, tree, rowsForPanel, columnLabel, title, totalHoras) => `
    <div class="dashboard-section">
      <div class="dashboard-section__body">
        <p class="perspective-label">${esc(title)}</p>
        ${panelToolbar(panelKey, stateKey)}
        <div class="activity-kpi-grid">${renderDashboardKpi({ label: "Total Horas", value: totalHoras, format: "hours3" })}</div>
        ${rowsForPanel.length ? `
        <div class="matrix-scroll">
          <table class="dashboard-table dashboard-table--compact">
            <colgroup>
              <col class="dashboard-table-col--flex">
              <col class="dashboard-table-col--compact">
              <col class="dashboard-table-col--compact">
            </colgroup>
            <thead><tr><th class="row-label-col">${columnLabel}</th>${sortHeader(panelKey, "Horas")}${sortHeader(panelKey, "% Incidencia")}</tr></thead>
            <tbody>${renderBusinessTreeRows(stateKey, tree, 0, capacidadProductivasColumns(panelKey))}</tbody>
          </table>
        </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}
      </div>
    </div>`;

  // El perspective-switch (Productivas ↔ No Asignadas) es navegación de
  // EXPERIENCIA, no pertenece a ningún panel — vive en su propio toolbar
  // arriba de la grilla, MISMO criterio que `renderCapacidadNoAsignadas`.
  // `.activity-grid-2` (MISMO primitivo compartido que Rentabilidad Mejor/
  // Peor, `min-width:0` reforzado en tools.css) — nunca un grid nuevo; al
  // angostarse (responsive) colapsa a una columna y las tarjetas se apilan
  // solas, sin mecanismo propio.
  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("capacidad", false))}
    <div class="activity-grid-2">
      ${panel("cliente", stateKeyCliente, treeCliente, rowsCliente, "Cliente / Proyecto", "Cliente", totalHorasCliente)}
      ${panel("clientePart", stateKeyPart, treePart, rowsPart, "Cliente + Participación / Proyecto", "Cliente + Participación", totalHorasPart)}
    </div>`;
  // countLabel = "N fila(s)" (owner feedback) — MISMO criterio que Cartera/
  // Proyectos, nunca la descripción de la jerarquía repetida acá (esa ya
  // vive en el título de cada tarjeta/columna de la tabla). Ambos paneles
  // comparten la MISMA población de Clientes (solo el rótulo de fila
  // difiere), así que `rowsCliente.length` representa a los dos.
  const detalle = dashboardSection("detalle", "Horas consumidas por Cliente", rowsCliente.length + " fila(s)", detalleBody);

  // HUB TRANSVERSAL PRIMITIVE TEST (owner corrective — live smoke encontró
  // 2 defectos en la primera pasada): describe el contrato semántico de
  // TODA la experiencia Horas Productivas (Ejecución = Time, Participación
  // = facturación) — vive AFUERA de "Horas consumidas por Cliente"
  // (independiente visualmente del grupo analítico), pero AL FINAL — es un
  // footnote/aclaración de cierre para TODA la superficie analítica, no una
  // introducción — jerarquía real: filtros → contenido → callout, NUNCA
  // callout → contenido. SOLO esta perspectiva (Horas Productivas) —
  // `renderCapacidadNoAsignadas` no lo llama.
  //
  // Variante `punch` (owner: "locate the ACTUAL existing Punch semantics
  // used by Copilot... do NOT create a Reporting-specific yellow/gold
  // callout"): evidencia PRIMARIA en `content_service.py::callout()` — el
  // tono `punch` (alias Markdown "yellow"/"amarillo") resuelve a la clase
  // `callout--punch`, que YA vive en tools.css (`--callout-accent:
  // #d3a000`, movida ahí junto con el resto del primitivo genérico en el
  // corrective anterior — nunca quedó huérfana en content.css). Pasar
  // `tone: "punch"` acá reusa ESA MISMA regla compartida — nunca una
  // paleta local nueva. `renderHubCallout` sigue siendo el MISMO
  // primitivo/nunca una imitación.
  const notaMetodologica = renderHubCallout({
    tone: "punch",
    title: "Nota metodológica",
    bodyHtml: `<p>${esc("Las horas visualizadas corresponden a la “Ejecución” declarada en Time. La participación indicada junto al nombre del cliente corresponde a la declarada sobre la facturación.")}</p>`,
  });

  return detalle + notaMetodologica;
}

function renderCapacidadNoAsignadas() {
  // Gate del ciclo de vida real-data Batch B — MISMO fetch/endpoint que
  // Productivas (Correction 1 CERRADA, ver HANDOFF/real-data-lineage.md
  // §8bis). El trigger vive en el SHELL (`TAB_HYDRATION_TRIGGERS`, ver
  // `render()`), acá solo el gate.
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
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", "Horas No Asignadas", "Consultor — Task: No Asignación a Servicio en Cliente", body);

  // PUNCH compartido (§8, owner): la nota metodológica salió del section
  // analítico (antes era un <p class="empty-note"> INLINE dentro de la tabla) y
  // pasa a reusar el MISMO primitivo `punch` que Productivas (renderHubCallout,
  // callout--punch de tools.css) — AFUERA y DESPUÉS del grupo analítico. El
  // feedback (¿Qué te pareció este reporte?) queda como último elemento del tab
  // (lo agrega render() a nivel main-tab, después de esto). Wording business-
  // friendly (sin el prefijo "01." del Task fuente — la presentación NUNCA
  // maneja el predicado de filtrado, §9).
  const notaMetodologica = renderHubCallout({
    tone: "punch",
    title: "Nota metodológica",
    bodyHtml: `<p>${esc("Horas No Productivas registradas por cada colaborador en Time (Task: No Asignación a Servicio en Cliente) — indicador distinto de la disponibilidad de Asignaciones.")}</p>`,
  });

  return detalle + notaMetodologica;
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

// --- SHARED REPORTING INITIAL-LOAD CONTRACT (owner corrective) -------------
// Antes, cada dispatcher de experiencia (`renderCartera`/`renderRentabilidad`/
// etc.) disparaba SU PROPIA hidratación como primera línea — pero el filter
// bar (`FILTER_BARS[expId]()`) se renderizaba SIEMPRE, sin importar si esa
// hidratación ya terminó. Resultado visible: tabs + filtros ya interactivos
// + página de contenido vacía + el loader recién aparecía DEBAJO de los
// filtros (nunca reemplazándolos). El owner pidió que la PRIMERA hidratación
// de un tab principal muestre SOLO tabs + loader — filtros/KPIs/secciones/
// tablas entran TODOS JUNTOS recién cuando hay contenido usable.
//
// El trigger de hidratación se CENTRALIZA acá (nunca condicionado a si se
// termina mostrando el contenido completo o solo el loader — si no se
// dispara, el tab quedaría "idle" para siempre). Los dispatchers de
// experiencia YA NO llaman `hydrateXTab()`/`loadBatchXData()` ellos mismos
// (ver comentario en cada uno) — siguen siendo responsables SOLO de decidir
// qué mostrar dado el status actual (su propio "gate" interno, necesario
// para años fríos/perspectivas individuales todavía cargando DENTRO de un
// tab ya hidratado).
const TAB_HYDRATION_TRIGGERS = {
  cartera: hydrateCarteraTab,
  rentabilidad: hydrateRentabilidadTab,
  horasvendidas: hydrateHorasVendidasTab,
  comercial: () => { hydrateComercialParticipacionTab(); if (state.batchADataStatus === "idle") loadBatchAData(); },
  calendario: () => { if (state.batchADataStatus === "idle") loadBatchAData(); },
  capacidad: () => { if (state.batchBDataStatus === "idle") loadBatchBData(); },
};
// Status CRUDO del tab para el propósito ÚNICO de "primera hidratación" —
// reusa los MISMOS campos que cada experiencia ya usa internamente para su
// propio gate (nunca un campo nuevo duplicado, ver `carteraStatusFor` etc.
// arriba). Para tabs con más de una fuente/perspectiva (Cartera: Mantenimiento
// + Proyecto; Comercial: Participación + Salud, comparten `batchADataStatus`
// con Calendario) alcanza con que UNA esté lista para tener contenido usable
// ("ready" gana); "error" solo si TODAS fallaron (nunca error solo porque
// una fuente todavía no se disparó — sería "loading"/"idle").
function reportingTabRawStatus(expId) {
  switch (expId) {
    // INITIAL HYDRATION CONTRACT: la PRIMERA hidratación gatea por el dataset de
    // la PERSPECTIVA ACTIVA, nunca por "alguna fuente ready" (eliminado el
    // `combine`, que asumía un ORDEN de resolución — Mantenimiento antes que
    // Proyecto porque Proyecto espera la XLSX; eso era un supuesto de timing, no
    // un contrato). Mismo principio que Comercial. El flag reportingTabHydrated
    // es one-way: conmutar perspectivas tras hidratar NUNCA re-entra al loader.
    case "cartera": return state.perspective.cartera === "Proyecto"
      ? state.carteraProyectoStatus
      : state.carteraMantenimientoStatus;
    case "rentabilidad": return state.rentabilidadDataStatus;
    case "horasvendidas": return state.horasVendidasDataStatus;
    // INITIAL HYDRATION CONTRACT (owner live smoke): la PRIMERA hidratación de
    // un main tab depende del dataset de su PERSPECTIVA ACTIVA, NUNCA de "alguna
    // fuente ready". Comercial tiene 2 familias (Participación = endpoint propio
    // scopeado; Salud = batch-a). Antes `combine(...)` devolvía "ready" si
    // CUALQUIERA estaba lista → batch-a/Salud hidrataba primero y revelaba
    // filtros+feedback con Participación (perspectiva por defecto) todavía en
    // "Cargando…". Ahora gatea por la perspectiva activa; la hermana warmea en
    // background y el flag de hidratación es one-way (no re-entra al loader al
    // conmutar perspectivas — ver render()).
    case "comercial": return state.perspective.comercial === "Salud de Cliente"
      ? state.batchADataStatus
      : state.comercialParticipacionDataStatus;
    case "calendario": return state.batchADataStatus;
    case "capacidad": return state.batchBDataStatus;
    default: return "ready";
  }
}

function render() {
  document.getElementById("reporting-experience-tabs").innerHTML = EXPERIENCES.map(e => `
    <button class="${state.experience === e.id ? "is-active" : ""}" onclick="setExperience('${e.id}')">${esc(e.label)}</button>
  `).join("");

  const expId = state.experience;
  if (TAB_HYDRATION_TRIGGERS[expId]) TAB_HYDRATION_TRIGGERS[expId]();
  // Marca de UNA SOLA VÍA — ver comentario del campo en `state` arriba.
  if (!state.reportingTabHydrated[expId] && reportingTabRawStatus(expId) === "ready") {
    state.reportingTabHydrated[expId] = true;
  }

  const mainContent = document.getElementById("reporting-main-content");
  if (state.reportingTabHydrated[expId]) {
    // READY (incluye "hidratado con un error incremental posterior" — el
    // flag NUNCA se apaga, ver arriba): filtros + contenido entran JUNTOS
    // como una sola superficie estable. El contenido de la experiencia
    // sigue gateando SU PROPIO status para años fríos/perspectivas
    // individuales todavía cargando (ver cada dispatcher).
    const filterBarHtml = FILTER_BARS[expId] ? FILTER_BARS[expId]() : "";
    // Feedback SIEMPRE como ÚLTIMO elemento del main tab (ver ticket §A1: para
    // Horas Productivas queda después del contenido Y de la nota Punch, que el
    // renderer ya emite). Un solo control por MAIN TAB, nunca por perspectiva.
    mainContent.innerHTML = filterBarHtml + RENDERERS[expId]() + reportingFeedbackHtml(expId);
  } else {
    // INITIAL LOADER MODE — SOLO tabs + loader inicial (o el estado de
    // error compartido si la PRIMERA hidratación falló). Filtros/KPIs/
    // secciones/tablas NUNCA se renderizan todavía — nunca el flash
    // "filtros visibles + página vacía + loader debajo" que reportó el
    // owner. Deliberadamente NO se renderiza un filter bar "vacío"/
    // deshabilitado — se prefiere no renderizarlo en absoluto.
    const rawStatus = reportingTabRawStatus(expId);
    mainContent.innerHTML = rawStatus === "error"
      ? reportingSourceErrorHtml("los datos de esta pestaña")
      : reportingLoadingHtml("Cargando…");
  }
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
