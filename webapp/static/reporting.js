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
//   Proyecto → Rows: Cliente, ID Proyecto Danwor, Proyecto (3 niveles)
//     Values: Diff Hs, Hs Trabajadas Acum, Hs Certif. Acum, Hs Vendidas,
//             Facturadas, Status
// KPI real de cada página es un card simple: "Cantidad Mantenimientos" /
// "Cantidad Proyectos" — NUNCA los KPIs inventados de la versión anterior
// de este V0 (Contratos visibles/Hs trabajadas/Hs certificadas/Con diff
// negativo), retirados en ese corrective. Status real de Proyectos.Status
// es "En curso" | "Finalizado" (owner screenshots) — NUNCA
// activo/vencido/cerrado (eso era una invención de la primera versión de
// este V0 para la Cartera vieja).
const CARTERA_MANTENIMIENTO_ROWS = [
  { cliente: "Andesmar", proyecto: "Mantenimiento Costeo Directo", diff: -6, trabajadas: 134, certificadas: 128, vendidasMensuales: 140, facturado: "100%", status: "En curso" },
  { cliente: "Andesmar", proyecto: "Soporte Infraestructura", diff: 3, trabajadas: 40, certificadas: 43, vendidasMensuales: 40, facturado: "100%", status: "En curso" },
  { cliente: "Danone", proyecto: "Soporte Analítica", diff: 4, trabajadas: 92, certificadas: 96, vendidasMensuales: 88, facturado: "100%", status: "En curso" },
  { cliente: "Molinos", proyecto: "Mantenimiento BI", diff: 0, trabajadas: 60, certificadas: 60, vendidasMensuales: 60, facturado: "100%", status: "Finalizado" },
];
const CARTERA_PROYECTO_ROWS = [
  { cliente: "YPF", idProyecto: "DAN-1042", proyecto: "Implementación SADI v2", diff: -18, trabajadas: 340, certificadas: 322, vendidas: 360, facturado: "90%", status: "En curso" },
  { cliente: "AySA", idProyecto: "DAN-1055", proyecto: "Costeo Federal", diff: 2, trabajadas: 210, certificadas: 212, vendidas: 208, facturado: "100%", status: "En curso" },
  { cliente: "Ternium", idProyecto: "DAN-0998", proyecto: "Rollout Reporting", diff: -9, trabajadas: 175, certificadas: 166, vendidas: 180, facturado: "85%", status: "Finalizado" },
];

function statusPillCartera(status) {
  const cls = status === "En curso" ? "en-curso" : "finalizado";
  return `<span class="status-pill ${cls}">${esc(status)}</span>`;
}

const CARTERA_CONFIG = {
  "Mantenimiento": {
    rows: CARTERA_MANTENIMIENTO_ROWS, keys: ["cliente", "proyecto"], labels: ["Cliente", "Proyecto"],
    kpiLabel: "Cantidad Mantenimientos",
    columns: [
      { label: "Diff Hs", numeric: true, render: r => diffCell(r.diff) },
      { label: "Hs Trabajadas Acum", numeric: true, render: r => r.trabajadas },
      { label: "Hs Certif. Acum", numeric: true, render: r => r.certificadas },
      { label: "Hs Vendidas Mensuales", numeric: true, render: r => r.vendidasMensuales },
      { label: "Facturadas", numeric: true, render: r => r.facturado },
      { label: "Status", numeric: false, render: r => statusPillCartera(r.status) },
    ],
  },
  "Proyecto": {
    rows: CARTERA_PROYECTO_ROWS, keys: ["cliente", "idProyecto", "proyecto"], labels: ["Cliente", "ID Proyecto Danwor", "Proyecto"],
    kpiLabel: "Cantidad Proyectos",
    columns: [
      { label: "Diff Hs", numeric: true, render: r => diffCell(r.diff) },
      { label: "Hs Trabajadas Acum", numeric: true, render: r => r.trabajadas },
      { label: "Hs Certif. Acum", numeric: true, render: r => r.certificadas },
      { label: "Hs Vendidas", numeric: true, render: r => r.vendidas },
      { label: "Facturadas", numeric: true, render: r => r.facturado },
      { label: "Status", numeric: false, render: r => statusPillCartera(r.status) },
    ],
  },
};

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
const RENTABILIDAD_RESUMEN = { vendidas: 969, certificadas: 940, trabajadas: 985, noAsignadas: 62 };
const RENTABILIDAD_PROYECTOS = [
  { tipo: "Mantenimiento", proyecto: "Andesmar - Mantenimiento Costeo Directo", certificadas: 128, trabajadas: 134 },
  { tipo: "Mantenimiento", proyecto: "Andesmar - Soporte Infraestructura", certificadas: 43, trabajadas: 40 },
  { tipo: "Mantenimiento", proyecto: "Danone - Soporte Analítica", certificadas: 96, trabajadas: 92 },
  { tipo: "Mantenimiento", proyecto: "Molinos - Mantenimiento BI", certificadas: 60, trabajadas: 60 },
  { tipo: "Proyecto", proyecto: "YPF - Implementación SADI v2", certificadas: 322, trabajadas: 340 },
  { tipo: "Proyecto", proyecto: "AySA - Costeo Federal", certificadas: 212, trabajadas: 210 },
  { tipo: "Proyecto", proyecto: "Ternium - Rollout Reporting", certificadas: 166, trabajadas: 175 },
];

// Horas Vendidas (ver corrective — HANDOFF/task): campos reales del PBIP son
// "Hs vendidas Mant" / "Hs vendidas Proy" (Horas por proyectos) separadas por
// Tipo, más Client Partner/Cliente/Proyecto/Mes — NUNCA "consumidas"/
// "restantes" (eso pertenece a otra experiencia, no se reubica acá). Cada fila
// es un Proyecto (nivel más fino) con 12 valores mensuales de horas vendidas;
// los niveles de agrupación (Client Partner/Tipo/Cliente) se agregan sumando
// filas — nunca se hardcodea un total de grupo por separado.
const MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
const HORASVENDIDAS_ROWS = [
  { cp: "María López", tipo: "Mantenimiento", cliente: "Andesmar", proyecto: "Mantenimiento Costeo Directo", monthly: [12, 12, 12, 12, 12, 12, 10, 10, 10, 10, 10, 10] },
  { cp: "María López", tipo: "Mantenimiento", cliente: "Danone", proyecto: "Soporte Analítica", monthly: [8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8] },
  { cp: "María López", tipo: "Proyecto", cliente: "Molinos", proyecto: "Rollout BI", monthly: [0, 0, 15, 15, 15, 15, 15, 0, 0, 0, 0, 0] },
  { cp: "Diego Ramos", tipo: "Mantenimiento", cliente: "AySA", proyecto: "Mantenimiento Costeo Federal", monthly: [18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18] },
  { cp: "Diego Ramos", tipo: "Proyecto", cliente: "YPF", proyecto: "Implementación SADI v2", monthly: [30, 30, 30, 30, 30, 30, 30, 30, 30, 0, 0, 0] },
  { cp: "Diego Ramos", tipo: "Proyecto", cliente: "Ternium", proyecto: "Rollout Reporting", monthly: [0, 0, 0, 20, 20, 20, 20, 20, 20, 20, 20, 20] },
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
const COMERCIAL_PARTICIPACION_ROWS = [
  { idFrecuencia: "1 Mensual", permiteAdelanto: "Si", condicionPago: "30", cliente: "Andesmar", cp: "María López", horasVendidas: 140, horasCertificadas: 128 },
  { idFrecuencia: "3 Trimestral", permiteAdelanto: "No", condicionPago: "60", cliente: "Danone", cp: "María López", horasVendidas: 88, horasCertificadas: 96 },
  { idFrecuencia: "1 Mensual", permiteAdelanto: "No", condicionPago: "30", cliente: "Molinos", cp: "María López", horasVendidas: 60, horasCertificadas: 60 },
  { idFrecuencia: "6 Anual", permiteAdelanto: "No", condicionPago: "30", cliente: "AySA", cp: "Diego Ramos", horasVendidas: 208, horasCertificadas: 212 },
  { idFrecuencia: "1 Mensual", permiteAdelanto: "Si", condicionPago: "15", cliente: "YPF", cp: "Diego Ramos", horasVendidas: 360, horasCertificadas: 322 },
  { idFrecuencia: "5 Semestral", permiteAdelanto: "Si", condicionPago: "30", cliente: "Ternium", cp: "Diego Ramos", horasVendidas: 180, horasCertificadas: 166 },
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
  const cls = value === "Verde" ? "verde" : (value === "Rojo" ? "rojo" : "amarillo");
  return `<span class="status-pill ${cls}">${esc(value)}</span>`;
}

const COMERCIAL_PARTICIPACION_TOTAL_VENDIDAS = COMERCIAL_PARTICIPACION_ROWS.reduce((s, r) => s + r.horasVendidas, 0);
const COMERCIAL_PARTICIPACION_TOTAL_CERTIFICADAS = COMERCIAL_PARTICIPACION_ROWS.reduce((s, r) => s + r.horasCertificadas, 0);
function comercialPctVendidas(horas) {
  return (COMERCIAL_PARTICIPACION_TOTAL_VENDIDAS ? (horas / COMERCIAL_PARTICIPACION_TOTAL_VENDIDAS * 100) : 0).toFixed(2) + " %";
}
function comercialPctCertificadas(horas) {
  return (COMERCIAL_PARTICIPACION_TOTAL_CERTIFICADAS ? (horas / COMERCIAL_PARTICIPACION_TOTAL_CERTIFICADAS * 100) : 0).toFixed(2) + " %";
}
const COMERCIAL_PARTICIPACION_COLUMNS = [
  { label: "% Participación Hs Vendidas", numeric: true, render: r => comercialPctVendidas(r.horasVendidas), rollup: rows => comercialPctVendidas(rows.reduce((s, r) => s + r.horasVendidas, 0)) },
  { label: "% Participación Hs Certificadas", numeric: true, render: r => comercialPctCertificadas(r.horasCertificadas), rollup: rows => comercialPctCertificadas(rows.reduce((s, r) => s + r.horasCertificadas, 0)) },
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
// patrón dual-perspectiva ya usado en Horas Vendidas (reusa buildTree/
// renderTreeRows sin cambios). Valores representativos = cantidad de
// entregas/checkpoints programados ese mes (no horas).
const CALENDARIO_ROWS = [
  { frecuencia: "Semanal", cliente: "Andesmar", cp: "María López", monthly: [4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4] },
  { frecuencia: "Semanal", cliente: "AySA", cp: "Diego Ramos", monthly: [4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4] },
  { frecuencia: "Mensual", cliente: "Danone", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Mensual", cliente: "Molinos", cp: "María López", monthly: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] },
  { frecuencia: "Trimestral", cliente: "YPF", cp: "Diego Ramos", monthly: [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1] },
  { frecuencia: "Trimestral", cliente: "Ternium", cp: "Diego Ramos", monthly: [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0] },
];
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

// ============================================================================
// Experiencias — cada una define su render + (opcional) perspectivas
// ============================================================================
const EXPERIENCES = [
  { id: "cartera", label: "Cartera / Proyectos", perspectives: ["Mantenimiento", "Proyecto"] },
  { id: "rentabilidad", label: "Rentabilidad / Performance", perspectives: ["Resumen", "Certificadas vs Ejecutadas"] },
  { id: "horasvendidas", label: "Horas Vendidas", perspectives: ["Proyecto", "Client Partner"] },
  { id: "comercial", label: "Comercial / Clientes", perspectives: ["Participación", "Salud de Cliente"] },
  { id: "calendario", label: "Calendario", perspectives: ["Frecuencia", "Client Partner"] },
  { id: "capacidad", label: "Capacidad / Ejecución", perspectives: ["Productivas", "No Asignadas"] },
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
  filters: { cartera: {}, rentabilidad: {}, horasvendidas: {}, calendario: {}, capacidad: {}, comercial: {} },
  // Status de Cartera/Proyectos — vocabulario real "En curso"/"Finalizado"
  // (Proyectos.Status). Checkbox-group propio (no el motor `filters`
  // genérico de arriba) porque su semántica default es "todo tildado",
  // distinta de "Todos" en un <select>.
  carteraStatus: { "En curso": true, "Finalizado": true },
};

function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function diffCell(diff) {
  const cls = diff < 0 ? "diff-neg" : (diff > 0 ? "diff-pos" : "");
  const sign = diff > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${diff}</span>`;
}

// --- Motor de filtros compartido (Align with Hub backbone corrective) ------
// UN solo mecanismo de estado/lectura/escritura para los filtros <select> de
// las 5 experiencias filtrables — nunca seis engines de filtro separados.
// "Todos" == "" (sin filtrar). Los checkbox-group (Status de Cartera) tienen
// su propio estado dedicado (`state.carteraStatus`) por la razón de arriba,
// pero se renderizan con el MISMO helper visual `checkboxGroupField`.
function distinctValues(rows, key) {
  return [...new Set(rows.map(r => r[key]))]
    .filter(v => v !== undefined && v !== null && v !== "")
    .sort((a, b) => String(a).localeCompare(String(b), "es"));
}

// --- Faceted/cascading filter helper (ver corrective — HANDOFF) -----------
// Regla canónica: availableOptions(F) = valores distintos de BASE DATA
// después de aplicar todos los filtros ACTIVOS salvo F mismo. `fields` es
// la config declarativa por experiencia: [{ key, field }] — `key` es la
// clave en `state.filters[expId]`, `field` es la propiedad real de la fila.
// `baseRows` ya viene escopeada por lo que NO es un facet <select>
// (perspectiva, checkboxes de Status) — el motor de acá solo resuelve la
// interdependencia ENTRE los <select>, nunca decide perspectiva/Status.
function facetRowsExcept(baseRows, filters, fields, exceptKey) {
  return fields.reduce((rows, f) => {
    if (f.key === exceptKey) return rows;
    const val = filters[f.key];
    return val ? rows.filter(r => r[f.field] === val) : rows;
  }, baseRows);
}
function facetOptions(baseRows, filters, fields, key) {
  const field = fields.find(f => f.key === key).field;
  return distinctValues(facetRowsExcept(baseRows, filters, fields, key), field);
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
      if (f.key === justChangedKey) return;
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
    const cfg = CARTERA_CONFIG[state.perspective.cartera];
    return {
      baseRows: cfg.rows.filter(r => state.carteraStatus[r.status]),
      fields: [{ key: "proyecto", field: "proyecto" }, { key: "cliente", field: "cliente" }],
    };
  },
  rentabilidad: () => ({
    baseRows: RENTABILIDAD_PROYECTOS,
    fields: [{ key: "tipo", field: "tipo" }, { key: "proyecto", field: "proyecto" }],
  }),
  horasvendidas: () => ({
    baseRows: HORASVENDIDAS_ROWS,
    fields: [{ key: "cp", field: "cp" }, { key: "tipo", field: "tipo" }, { key: "cliente", field: "cliente" }, { key: "proyecto", field: "proyecto" }],
  }),
  calendario: () => ({
    baseRows: CALENDARIO_ROWS,
    fields: [{ key: "cp", field: "cp" }, { key: "cliente", field: "cliente" }],
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
  comercial: () => state.perspective.comercial === "Participación"
    ? { baseRows: COMERCIAL_PARTICIPACION_ROWS, fields: [{ key: "cp", field: "cp" }, { key: "cliente", field: "cliente" }] }
    : { baseRows: COMERCIAL_SALUD_ROWS, fields: [{ key: "cp", field: "cp" }, { key: "cliente", field: "cliente" }] },
};
function reconcileExperienceFacets(expId, justChangedKey) {
  const cfg = FACET_CONFIG[expId];
  if (!cfg) return;
  const { baseRows, fields } = cfg();
  reconcileFacetFilters(expId, baseRows, fields, justChangedKey);
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
function resetFilters(expId) {
  state.filters[expId] = {};
  if (expId === "cartera") state.carteraStatus = { "En curso": true, "Finalizado": true };
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
function filterBarShell(fieldsHtml, expId) {
  return `<div class="dashboard-sticky-header">
    <div class="staffing-filter-bar">
      <div class="whatsnew-filters">${fieldsHtml}</div>
      <button type="button" class="whatsnew-filter__clear staffing-filter-bar__clear" onclick="resetFilters('${expId}')">✕ Limpiar</button>
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
function renderCarteraFilterBar() {
  const fields = facetSelectField("cartera", "proyecto", "Proyecto")
    + facetSelectField("cartera", "cliente", "Cliente")
    + checkboxGroupField("Status", [
        { value: "En curso", checked: state.carteraStatus["En curso"], onclick: "toggleCarteraStatus('En curso')" },
        { value: "Finalizado", checked: state.carteraStatus["Finalizado"], onclick: "toggleCarteraStatus('Finalizado')" },
      ]);
  return filterBarShell(fields, "cartera");
}

function renderRentabilidadFilterBar() {
  const fields = facetSelectField("rentabilidad", "tipo", "Tipo Proyecto")
    + facetSelectField("rentabilidad", "proyecto", "Proyecto");
  return filterBarShell(fields, "rentabilidad");
}

function renderHorasVendidasFilterBar() {
  const fields = facetSelectField("horasvendidas", "cp", "Client Partner")
    + facetSelectField("horasvendidas", "tipo", "Tipo Proyecto")
    + facetSelectField("horasvendidas", "cliente", "Cliente")
    + facetSelectField("horasvendidas", "proyecto", "Proyecto");
  return filterBarShell(fields, "horasvendidas");
}

function renderCalendarioFilterBar() {
  const fields = facetSelectField("calendario", "cp", "Client Partner")
    + facetSelectField("calendario", "cliente", "Cliente");
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

function renderComercialFilterBar() {
  const fields = facetSelectField("comercial", "cp", "Client Partner")
    + facetSelectField("comercial", "cliente", "Cliente");
  return filterBarShell(fields, "comercial");
}

// `withLabel` (Hub Visual Consistency corrective, ver HANDOFF): las 6
// experiencias montan el switch DENTRO del toolbar de la sección analítica
// a la que controla (ver sectionToolbar) — ahí el rótulo "Ver por" es
// redundante (el breadcrumb de la sección ya da contexto), así que se
// omite (`withLabel=false`). `withLabel=true` (default) queda disponible
// para el caso standalone, sin uso actual tras alinear Comercial (ver
// Commercial/Clients corrective).
function perspectiveSwitch(expId, withLabel = true) {
  const exp = EXPERIENCES.find(e => e.id === expId);
  const buttons = exp.perspectives.map(p => `
    <button class="${state.perspective[expId] === p ? "is-active" : ""}" onclick="setPerspective('${expId}', '${esc(p)}')">${esc(p)}</button>
  `).join("");
  const label = withLabel ? `<p class="perspective-label">Ver por</p>` : "";
  return `${label}<div class="perspective-switch">${buttons}</div>`;
}

function setPerspective(expId, p) {
  state.perspective[expId] = p;
  // Cada perspectiva puede tener dimensiones de filtro distintas (ver
  // Capacidad Productivas↔No Asignadas) — se resetea el filtro de la
  // experiencia al cambiar de perspectiva para no arrastrar un valor que ya
  // no aplica a la nueva vista.
  if (state.filters[expId]) state.filters[expId] = {};
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

function renderCartera() {
  const persp = state.perspective.cartera;
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
      <div class="activity-kpi"><span class="activity-kpi__value">${rows.length}</span><span class="activity-kpi__label">${esc(cfg.kpiLabel)}</span></div>
    </div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("cartera", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="tree-matrix">
        <thead><tr>
          <th class="row-label-col">${cfg.labels.join(" / ")}</th>
          ${cfg.columns.map(c => `<th class="${c.numeric ? "" : "left"}">${esc(c.label)}</th>`).join("")}
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

function rentabilidadBarRow(label, value, maxAbs) {
  const cls = value < 0 ? "negativo" : "positivo";
  const width = maxAbs ? (Math.abs(value) / maxAbs * 100).toFixed(1) : 0;
  return `<div class="bar-row"><span class="bar-label">${esc(label)}</span>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${width}%"></div></div>
    <span class="bar-value">${diffCell(value)}</span>
  </div>`;
}

function renderRentabilidadResumen() {
  const r = RENTABILIDAD_RESUMEN;
  // Rentabilidad horas = Horas Certificadas − Horas Trabajadas (fórmula real,
  // ver `Rentabilidad Unpivot.tmdl` VAR Rentabilidad) — NUNCA la suma de los
  // 5 conceptos (esa suma no tiene significado de negocio, aunque la tabla
  // Power BI original tuviera una fila Total). Estos 4 KPIs son el card
  // resumen REAL de la página fuente (totales de portfolio) — no se derivan
  // de RENTABILIDAD_PROYECTOS (esa lista no suma 1:1 contra ellos, ver
  // comentario del dataset) y por eso NO se filtran; solo el ranking de
  // abajo sí responde a Tipo Proyecto/Proyecto.
  const rentabilidadHoras = r.certificadas - r.trabajadas;
  const rows = rentabilidadFilteredProyectos();
  const ranked = rentabilidadRanked(rows);
  const positivos = ranked.filter(p => p.rentabilidad >= 0);
  const negativos = ranked.filter(p => p.rentabilidad < 0).slice().reverse(); // más negativo primero
  const maxAbs = Math.max(...ranked.map(p => Math.abs(p.rentabilidad)), 1);

  const resumen = dashboardSection("resumen", "Resumen", "", `
    ${sectionToolbar(perspectiveSwitch("rentabilidad", false))}
    <div class="activity-kpi-grid">
      <div class="activity-kpi"><span class="activity-kpi__value">${r.vendidas}</span><span class="activity-kpi__label">Horas Vendidas</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${r.certificadas}</span><span class="activity-kpi__label">Horas Certificadas</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${r.trabajadas}</span><span class="activity-kpi__label">Horas Trabajadas</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${diffCell(rentabilidadHoras)}</span><span class="activity-kpi__label">Rentabilidad horas</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${r.noAsignadas}</span><span class="activity-kpi__label">No asignadas a servicios</span></div>
    </div>`);

  const rankingBody = rows.length ? `
    <p class="perspective-label">Mejor performance</p>
    ${positivos.length ? positivos.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("") : `<p class="empty-note">Sin proyectos con rentabilidad ≥ 0.</p>`}
    <p class="perspective-label" style="margin-top:14px">Peor performance</p>
    ${negativos.length ? negativos.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("") : `<p class="empty-note">Sin proyectos con rentabilidad negativa.</p>`}
  ` : `<p class="empty-note">Sin proyectos para los filtros actuales.</p>`;
  const ranking = dashboardSection("ranking", "Ranking por Rentabilidad horas", "Certificadas − Trabajadas", rankingBody);

  return resumen + ranking;
}

function renderRentabilidadCertEjecutadas() {
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
    ${groups.map(g => `
      <p class="perspective-label" style="margin-top:14px">${esc(g.tipo)}</p>
      <div class="bar-row"><span class="bar-label">Certificadas</span>
        <div class="bar-track"><div class="bar-fill certificadas" style="width:${(g.certificadas / maxVal * 100).toFixed(1)}%"></div></div>
        <span class="bar-value">${g.certificadas}</span>
      </div>
      <div class="bar-row"><span class="bar-label">Ejecutadas</span>
        <div class="bar-track"><div class="bar-fill ejecutadas" style="width:${(g.trabajadas / maxVal * 100).toFixed(1)}%"></div></div>
        <span class="bar-value">${g.trabajadas}</span>
      </div>
      <p style="margin:4px 0 0;font-size:.76rem;color:var(--ink-soft)">
        Margen Absoluto ${diffCell(g.margenAbsoluto)} · Margen Relativo ${diffCell(Math.round(g.margenRelativo * 10) / 10)}%
      </p>`).join("")}
  ` : `<p class="empty-note">Sin datos para los filtros actuales.</p>`;
  const analysisBody = `${sectionToolbar(perspectiveSwitch("rentabilidad", false))}${analysisContent}`;
  const analysis = dashboardSection("analisis", "Certificadas vs Ejecutadas", "por Tipo Proyecto Dash", analysisBody);

  const detailBody = rows.length ? `
    <table class="data-table">
      <thead><tr><th>Tipo</th><th>Proyecto</th><th class="num">Hs Certificadas</th><th class="num">Hs Ejecutadas</th><th class="num">Margen Absoluto</th><th class="num">Margen Relativo</th></tr></thead>
      <tbody>${rows.map(p => {
        const margenAbsoluto = p.certificadas - p.trabajadas;
        const margenRelativo = p.certificadas ? Math.round((margenAbsoluto / p.certificadas) * 1000) / 10 : 0;
        return `<tr>
          <td>${esc(p.tipo)}</td><td>${esc(p.proyecto)}</td>
          <td class="num">${p.certificadas}</td><td class="num">${p.trabajadas}</td>
          <td class="num">${diffCell(margenAbsoluto)}</td><td class="num">${diffCell(margenRelativo)}%</td>
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
function treeToolbarButtons(stateKey) {
  return `<button type="button" onclick="collapseAllTree('${stateKey}')">Contraer</button>
    <button type="button" onclick="expandAllTree('${stateKey}')">Expandir</button>`;
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

function sumMonthly(rows) {
  const totals = new Array(12).fill(0);
  rows.forEach(r => r.monthly.forEach((v, i) => { totals[i] += v; }));
  return totals;
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

// `showTotal` (opcional, default false — Horas Vendidas no lo usa, sin
// cambio de comportamiento ahí): agrega una columna Total por fila, tal como
// trae "Calendario x CP" en el PBIP fuente (Values incluye "Total" además de
// los 12 meses).
function renderTreeRows(stateKey, nodes, depth, showTotal = false) {
  const set = treeExpandedSet(stateKey);
  return nodes.map(node => {
    const hasChildren = node.children && node.children.length > 0;
    const isOpen = set.has(node.pathKey);
    const caretChar = !hasChildren ? "" : (isOpen ? "▾" : "▸");
    const indentPx = depth * 20;
    const monthly = sumMonthly(node.rows);
    const rowTotal = monthly.reduce((a, b) => a + b, 0);
    const rowHtml = `
      <tr class="depth-${depth}">
        <td class="row-label">
          <span class="tree-indent" style="width:${indentPx}px"></span>
          <span class="tree-toggle" ${hasChildren ? `onclick="toggleTreeNode('${stateKey}', '${esc(node.pathKey).replace(/'/g, "\\'")}')"` : ""}>
            <span class="caret ${hasChildren ? "" : "leaf"}">${caretChar}</span>
            ${esc(node.label)}
          </span>
        </td>
        ${monthly.map(v => `<td>${v || "—"}</td>`).join("")}
        ${showTotal ? `<td>${rowTotal}</td>` : ""}
      </tr>`;
    const childrenHtml = (hasChildren && isOpen) ? renderTreeRows(stateKey, node.children, depth + 1, showTotal) : "";
    return rowHtml + childrenHtml;
  }).join("");
}

// Renderer alternativo para tablas de negocio NO mensuales (Cartera/
// Proyectos, Capacidad/Ejecución → Productivas) — MISMO motor de
// agrupación (buildTree) y MISMAS afordancias visuales (tree-toggle/caret/
// tree-indent/depth-N), pero las columnas son arbitrarias (Diff Hs/Status/
// Horas/% Incidencia/etc., ver `columns`) en vez de 12 meses fijos.
// Nodos hoja (sin hijos, `node.rows` tiene exactamente 1 fila real)
// muestran los valores reales vía `columns[].render(row)`. Nodos
// intermedios (con hijos): por default quedan en blanco — Cartera nunca
// define `columns[].rollup` porque sus columnas son categóricas (Status/
// Facturadas no se pueden sumar); si una columna SÍ define `rollup(rows)`
// (medida real agregable, ver Capacidad/Productivas: Horas/% Incidencia),
// se usa para mostrar el agregado del grupo — extensión aditiva, ningún
// caller existente cambia de comportamiento sin optar explícitamente.
function renderBusinessTreeRows(stateKey, nodes, depth, columns) {
  const set = treeExpandedSet(stateKey);
  return nodes.map(node => {
    const hasChildren = node.children && node.children.length > 0;
    const isOpen = set.has(node.pathKey);
    const caretChar = !hasChildren ? "" : (isOpen ? "▾" : "▸");
    const indentPx = depth * 20;
    const leafRow = hasChildren ? null : node.rows[0];
    const rowHtml = `
      <tr class="depth-${depth}">
        <td class="row-label">
          <span class="tree-indent" style="width:${indentPx}px"></span>
          <span class="tree-toggle" ${hasChildren ? `onclick="toggleTreeNode('${stateKey}', '${esc(node.pathKey).replace(/'/g, "\\'")}')"` : ""}>
            <span class="caret ${hasChildren ? "" : "leaf"}">${caretChar}</span>
            ${esc(node.label)}
          </span>
        </td>
        ${columns.map(c => {
          let content = "";
          if (leafRow) content = c.render(leafRow);
          else if (c.rollup) content = c.rollup(node.rows);
          return `<td class="${c.numeric ? "" : "left"}">${content}</td>`;
        }).join("")}
      </tr>`;
    const childrenHtml = (hasChildren && isOpen) ? renderBusinessTreeRows(stateKey, node.children, depth + 1, columns) : "";
    return rowHtml + childrenHtml;
  }).join("");
}

function renderHorasVendidas() {
  const persp = state.perspective.horasvendidas;
  const hierarchy = HORASVENDIDAS_HIERARCHY[persp];
  const rows = facetFilteredRows("horasvendidas");
  const stateKey = "horasvendidas::" + persp;
  const tree = buildTree(rows, hierarchy.keys);
  registerTreeContext(stateKey, rows, hierarchy.keys);
  const totalMonthly = sumMonthly(rows);

  const annual = totalMonthly.reduce((s, v) => s + v, 0);
  const mant = rows.filter(r => r.tipo === "Mantenimiento").reduce((s, r) => s + r.monthly.reduce((a, b) => a + b, 0), 0);
  const proy = rows.filter(r => r.tipo === "Proyecto").reduce((s, r) => s + r.monthly.reduce((a, b) => a + b, 0), 0);
  const quarter = q => totalMonthly.slice(q * 3, q * 3 + 3).reduce((a, b) => a + b, 0);

  const resumen = dashboardSection("resumen", "Resumen", rows.length + " proyecto(s)", `
    <div class="activity-kpi-grid">
      <div class="activity-kpi"><span class="activity-kpi__value">${annual}</span><span class="activity-kpi__label">Hs Estimadas anuales</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${mant}</span><span class="activity-kpi__label">Mantenimiento</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${proy}</span><span class="activity-kpi__label">Proyecto</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${quarter(0)}</span><span class="activity-kpi__label">Vendidas Q1</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${quarter(1)}</span><span class="activity-kpi__label">Vendidas Q2</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${quarter(2)}</span><span class="activity-kpi__label">Vendidas Q3</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${quarter(3)}</span><span class="activity-kpi__label">Vendidas Q4</span></div>
    </div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("horasvendidas", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="tree-matrix">
        <thead><tr><th class="row-label-col">${hierarchy.labels.join(" / ")}</th>${MESES.map(m => `<th>${m}</th>`).join("")}</tr></thead>
        <tbody>
          ${renderTreeRows(stateKey, tree, 0)}
          <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v}</td>`).join("")}</tr>
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
  const rows = facetFilteredRows("comercial");
  const keys = ["idFrecuencia", "permiteAdelanto", "condicionPago", "cliente"];
  const stateKey = "comercial::Participación";
  const tree = buildTree(rows, keys);
  registerTreeContext(stateKey, rows, keys);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("comercial", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="tree-matrix">
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
  const rows = facetFilteredRows("comercial");
  const keys = ["cp", "cliente"];
  const stateKey = "comercial::Salud de Cliente";
  const tree = buildTree(rows, keys);
  registerTreeContext(stateKey, rows, keys);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("comercial", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="tree-matrix">
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

function renderCalendario() {
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
      <div class="activity-kpi"><span class="activity-kpi__value">${rows.length}</span><span class="activity-kpi__label">Clientes con calendario</span></div>
      <div class="activity-kpi"><span class="activity-kpi__value">${grandTotal}</span><span class="activity-kpi__label">Entregas/checkpoints (año)</span></div>
    </div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("calendario", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="tree-matrix">
        <thead><tr><th class="row-label-col">${hierarchy.labels.join(" / ")}</th>${MESES.map(m => `<th>${m}</th>`).join("")}<th>Total</th></tr></thead>
        <tbody>
          ${renderTreeRows(stateKey, tree, 0, true)}
          <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v}</td>`).join("")}<td>${grandTotal}</td></tr>
        </tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", persp, hierarchy.labels.join(" › "), detalleBody);

  return dashboardSections("reporting-calendario", resumen + detalle);
}

const CAPACIDAD_PRODUCTIVAS_TOTAL = CAPACIDAD_PRODUCTIVAS_ROWS.reduce((s, r) => s + r.horas, 0);

function capacidadPctIncidencia(horas) {
  // % Incidencia real (medida `Time Logs Reports.[% Hs ejecutadas]`) =
  // horas de la fila ÷ horas TOTALES (REMOVEFILTERS Cliente/Client
  // Partner) — SIEMPRE contra el gran total FIJO (CAPACIDAD_PRODUCTIVAS_TOTAL,
  // nunca recalculado por el filtro de Cliente del V0), tanto en filas hoja
  // como en el rollup de Cliente — así se preserva la semántica real de la
  // medida DAX incluso cuando el filtro deja visible un subconjunto.
  return (CAPACIDAD_PRODUCTIVAS_TOTAL ? (horas / CAPACIDAD_PRODUCTIVAS_TOTAL * 100) : 0).toFixed(1) + "%";
}

const CAPACIDAD_PRODUCTIVAS_COLUMNS = [
  { label: "Horas", numeric: true, render: r => r.horas, rollup: rows => rows.reduce((s, r) => s + r.horas, 0) },
  { label: "% Incidencia", numeric: true, render: r => capacidadPctIncidencia(r.horas), rollup: rows => capacidadPctIncidencia(rows.reduce((s, r) => s + r.horas, 0)) },
];

function renderCapacidadProductivas() {
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
    <div class="activity-kpi-grid"><div class="activity-kpi"><span class="activity-kpi__value">${filteredTotal}</span><span class="activity-kpi__label">Total Horas</span></div></div>`);

  const detalleBody = `
    ${sectionToolbar(perspectiveSwitch("capacidad", false), treeToolbarButtons(stateKey))}
    ${rows.length ? `
    <div class="matrix-scroll">
      <table class="tree-matrix">
        <thead><tr><th class="row-label-col">Cliente / Proyecto</th><th>Horas</th><th>% Incidencia</th></tr></thead>
        <tbody>${renderBusinessTreeRows(stateKey, tree, 0, CAPACIDAD_PRODUCTIVAS_COLUMNS)}</tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}`;
  const detalle = dashboardSection("detalle", "Horas consumidas por Cliente", "Cliente › Proyecto, ordenado por Rank (Horas desc.)", detalleBody);

  return resumen + detalle;
}

function renderCapacidadNoAsignadas() {
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
      <table class="tree-matrix">
        <thead><tr><th class="row-label-col">Consultor</th>${MESES.map(m => `<th>${m}</th>`).join("")}</tr></thead>
        <tbody>
          ${renderTreeRows(stateKey, tree, 0)}
          <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v}</td>`).join("")}</tr>
        </tbody>
      </table>
    </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}
    <p class="empty-note" style="text-align:left">Horas No Productivas — registradas en Time por cada colaborador (ver HANDOFF). Potential overlap with Staffing — integration decision pending.</p>`;
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
