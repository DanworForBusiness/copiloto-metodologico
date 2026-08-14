// reporting.js — Reporting Integral V0 preview (Operations, ver HANDOFF).
// Portado del prototipo standalone (prototypes/reporting-integral-v0/index.html)
// con el MENOR cambio razonable: MISMA lógica verbatim, solo se renombraron
// los ids de DOM target (experience-tabs/main-content/drawer*) con prefijo
// "reporting-" para no colisionar con el id="content" ya usado por el shell
// del Hub (base.html/staffing.html) ni con nada del resto de la página.
// Funciones/variables globales (state/render/esc/buildTree/etc.) verificadas
// SIN colisión con app.js al momento de este batch — sin namespacing
// adicional a propósito (no introducir un framework/wrapper nuevo, ver
// HANDOFF). Deduplicar contra el prototipo standalone es tarea de V1, no de
// este preview.

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
// negativo), retirados en este corrective. Status real de Proyectos.Status
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

const COMERCIAL_PARTICIPACION = [
  { cliente: "Andesmar", cp: "María López", condicion: "T&M mensual", participacion: "22%" },
  { cliente: "Danone", cp: "María López", condicion: "Bolsa de horas", participacion: "15%" },
  { cliente: "YPF", cp: "Diego Ramos", condicion: "Fijo por hito", participacion: "31%" },
];
const COMERCIAL_STATUS = [
  { cliente: "Andesmar", status: "Vigente", ultimaRevision: "Jul-2026" },
  { cliente: "Danone", status: "Vigente", ultimaRevision: "Jun-2026" },
  { cliente: "YPF", status: "En renegociación", ultimaRevision: "Ago-2026" },
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
// gran total, no contra el subtotal del Cliente). `Rank` = RANKX por Horas
// descendente — representado acá por el ORDEN de las filas (Cliente
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
  { id: "comercial", label: "Comercial / Clientes", perspectives: ["Participación", "Status de Cliente"] },
  { id: "calendario", label: "Calendario", perspectives: ["Frecuencia", "Client Partner"] },
  { id: "capacidad", label: "Capacidad / Ejecución", perspectives: ["Productivas", "No Asignadas"] },
];

const state = {
  experience: "cartera",
  perspective: { cartera: "Mantenimiento", rentabilidad: "Resumen", horasvendidas: "Proyecto",
                 comercial: "Participación", calendario: "Frecuencia", capacidad: "Productivas" },
  // Estado del filtro Estado COMPARTIDO (decorativo salvo en la Cartera
  // vieja, ver HANDOFF) — sigue usado por `renderFilterBar()` para las
  // demás experiencias (Rentabilidad/Comercial/Capacidad), sin tocar.
  estadoFilter: { activo: true, vencido: true, cerrado: true },
  // Status de Cartera/Proyectos (ver corrective) — vocabulario real
  // "En curso"/"Finalizado" (Proyectos.Status), INDEPENDIENTE de
  // `estadoFilter` de arriba — Cartera tiene su propio filter bar
  // (`renderCarteraFilterBar`), nunca el compartido.
  carteraStatus: { "En curso": true, "Finalizado": true },
};

function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function statusPill(status) {
  const labels = { activo: "Activo", vencido: "Vencido", cerrado: "Cerrado" };
  return `<span class="status-pill ${status}">${labels[status]}</span>`;
}

function diffCell(diff) {
  const cls = diff < 0 ? "diff-neg" : (diff > 0 ? "diff-pos" : "");
  const sign = diff > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${diff}</span>`;
}

// --- filter bar (común, cosmético salvo Estado que sí filtra Cartera) ------
function renderFilterBar() {
  return `
  <div class="filter-bar">
    <div class="filter-field"><span>Proyecto</span><select><option>Todos</option><option>Implementación SADI v2</option><option>Costeo Federal</option></select></div>
    <div class="filter-field"><span>Empresa</span><select><option>Todos</option><option>Danwor</option></select></div>
    <div class="filter-field"><span>Año</span><select><option>2026</option><option>2025</option></select></div>
    <div class="filter-field"><span>Mes</span><select><option>Todos</option><option>Agosto</option><option>Julio</option></select></div>
    <div class="filter-field"><span>Client Partner</span><select><option>Todos</option><option>María López</option><option>Diego Ramos</option></select></div>
    <div class="filter-field">
      <span>Estado</span>
      <div class="checkbox-row">
        <label><input type="checkbox" ${state.estadoFilter.activo ? "checked" : ""} onchange="toggleEstado('activo')"> Activo</label>
        <label><input type="checkbox" ${state.estadoFilter.vencido ? "checked" : ""} onchange="toggleEstado('vencido')"> Vencido</label>
        <label><input type="checkbox" ${state.estadoFilter.cerrado ? "checked" : ""} onchange="toggleEstado('cerrado')"> Cerrado</label>
      </div>
    </div>
    <div class="filter-note">Filtros de Proyecto/Empresa/Año/Mes/Client Partner son decorativos en este V0 — Estado sí filtra la tabla de Cartera.</div>
  </div>`;
}

function toggleEstado(key) {
  state.estadoFilter[key] = !state.estadoFilter[key];
  render();
}

// Filter bar DEDICADO a Cartera/Proyectos (ver corrective) — MISMAS clases
// CSS que `renderFilterBar()` (.filter-bar/.filter-field/.checkbox-row,
// ningún componente nuevo), pero campos genuinamente distintos por fuente
// real: shell compartido Proyecto/Empresa/Client Partner + Status
// (En curso/Finalizado, único filtro realmente wired en este V0, igual que
// Estado lo era en la Cartera vieja) — Año/Mes SOLO en Mantenimiento, Año de
// Inicio SOLO en Proyecto (el PBIP fuente confirma que Proyecto NO tiene
// filtro de Mes). `render()` elige esta función en vez de la compartida
// SOLO cuando `state.experience === "cartera"` — las demás experiencias
// siguen recibiendo `renderFilterBar()` sin cambios.
function renderCarteraFilterBar() {
  const persp = state.perspective.cartera;
  const periodFields = persp === "Mantenimiento"
    ? `<div class="filter-field"><span>Año</span><select><option>2026</option><option>2025</option></select></div>
       <div class="filter-field"><span>Mes</span><select><option>Todos</option><option>Agosto</option><option>Julio</option></select></div>`
    : `<div class="filter-field"><span>Año de Inicio</span><select><option>Todos</option><option>2026</option><option>2025</option></select></div>`;
  return `
  <div class="filter-bar">
    <div class="filter-field"><span>Proyecto</span><select><option>Todos</option><option>Implementación SADI v2</option><option>Costeo Federal</option></select></div>
    <div class="filter-field"><span>Empresa</span><select><option>Todos</option><option>Danwor</option></select></div>
    ${periodFields}
    <div class="filter-field"><span>Client Partner</span><select><option>Todos</option><option>María López</option><option>Diego Ramos</option></select></div>
    <div class="filter-field">
      <span>Status</span>
      <div class="checkbox-row">
        <label><input type="checkbox" ${state.carteraStatus["En curso"] ? "checked" : ""} onchange="toggleCarteraStatus('En curso')"> En curso</label>
        <label><input type="checkbox" ${state.carteraStatus["Finalizado"] ? "checked" : ""} onchange="toggleCarteraStatus('Finalizado')"> Finalizado</label>
      </div>
    </div>
    <div class="filter-note">Filtros de Proyecto/Empresa/${persp === "Mantenimiento" ? "Año/Mes" : "Año de Inicio"}/Client Partner son decorativos en este V0 — Status sí filtra la tabla.</div>
  </div>`;
}

function toggleCarteraStatus(key) {
  state.carteraStatus[key] = !state.carteraStatus[key];
  render();
}

function perspectiveSwitch(expId) {
  const exp = EXPERIENCES.find(e => e.id === expId);
  const buttons = exp.perspectives.map(p => `
    <button class="${state.perspective[expId] === p ? "is-active" : ""}" onclick="setPerspective('${expId}', '${esc(p)}')">${esc(p)}</button>
  `).join("");
  return `<p class="perspective-label">Ver por</p><div class="perspective-switch">${buttons}</div>`;
}

function setPerspective(expId, p) {
  state.perspective[expId] = p;
  render();
}

function setExperience(id) {
  state.experience = id;
  render();
}

// --- Experience renderers ---------------------------------------------------

function renderCartera() {
  const persp = state.perspective.cartera;
  const cfg = CARTERA_CONFIG[persp];
  const rows = cfg.rows.filter(r => state.carteraStatus[r.status]);
  const tree = buildTree(rows, cfg.keys);
  return `
    ${perspectiveSwitch("cartera")}
    <div class="kpi-grid">
      <div class="kpi"><span class="value">${rows.length}</span><span class="label">${esc(cfg.kpiLabel)}</span></div>
    </div>
    <section class="panel">
      <h2>${esc(persp)} <span class="count">${cfg.labels.join(" › ")}</span></h2>
      ${rows.length ? `
      <div class="matrix-scroll">
        <table class="tree-matrix">
          <thead><tr>
            <th class="row-label-col">${cfg.labels.join(" / ")}</th>
            ${cfg.columns.map(c => `<th class="${c.numeric ? "" : "left"}">${esc(c.label)}</th>`).join("")}
          </tr></thead>
          <tbody>${renderBusinessTreeRows("cartera::" + persp, tree, 0, cfg.columns)}</tbody>
        </table>
      </div>` : `<p class="empty-note">Sin filas para los filtros actuales.</p>`}
    </section>`;
}

// Ranking de "Rentabilidad horas" (== Certificadas − Trabajadas) por
// proyecto — MISMA fórmula que Resumen y que Certificadas vs Ejecutadas,
// una sola función fuente de verdad.
function rentabilidadRanked() {
  return RENTABILIDAD_PROYECTOS
    .map(p => ({ ...p, rentabilidad: p.certificadas - p.trabajadas }))
    .sort((a, b) => b.rentabilidad - a.rentabilidad);
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
  // Power BI original tuviera una fila Total).
  const rentabilidadHoras = r.certificadas - r.trabajadas;

  const ranked = rentabilidadRanked();
  const positivos = ranked.filter(p => p.rentabilidad >= 0);
  const negativos = ranked.filter(p => p.rentabilidad < 0).slice().reverse(); // más negativo primero
  const maxAbs = Math.max(...ranked.map(p => Math.abs(p.rentabilidad)), 1);

  return `
    <div class="kpi-grid">
      <div class="kpi"><span class="value">${r.vendidas}</span><span class="label">Horas Vendidas</span></div>
      <div class="kpi"><span class="value">${r.certificadas}</span><span class="label">Horas Certificadas</span></div>
      <div class="kpi"><span class="value">${r.trabajadas}</span><span class="label">Horas Trabajadas</span></div>
      <div class="kpi"><span class="value">${diffCell(rentabilidadHoras)}</span><span class="label">Rentabilidad horas</span></div>
      <div class="kpi"><span class="value">${r.noAsignadas}</span><span class="label">No asignadas a servicios</span></div>
    </div>
    <section class="panel">
      <h2>Ranking por Rentabilidad horas <span class="count">Certificadas − Trabajadas</span></h2>
      <p class="perspective-label">Mejor performance</p>
      ${positivos.length ? positivos.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("") : `<p class="empty-note">Sin proyectos con rentabilidad ≥ 0.</p>`}
      <p class="perspective-label" style="margin-top:14px">Peor performance</p>
      ${negativos.length ? negativos.map(p => rentabilidadBarRow(p.proyecto, p.rentabilidad, maxAbs)).join("") : `<p class="empty-note">Sin proyectos con rentabilidad negativa.</p>`}
    </section>`;
}

function renderRentabilidadCertEjecutadas() {
  const groups = ["Mantenimiento", "Proyecto"].map(tipo => {
    const rows = RENTABILIDAD_PROYECTOS.filter(p => p.tipo === tipo);
    const certificadas = rows.reduce((s, r) => s + r.certificadas, 0);
    const trabajadas = rows.reduce((s, r) => s + r.trabajadas, 0);
    const margenAbsoluto = certificadas - trabajadas;
    const margenRelativo = certificadas ? (margenAbsoluto / certificadas * 100) : 0;
    return { tipo, rows, certificadas, trabajadas, margenAbsoluto, margenRelativo };
  });
  const maxVal = Math.max(...groups.flatMap(g => [g.certificadas, g.trabajadas]));

  return `
    <section class="panel">
      <h2>Certificadas vs Ejecutadas <span class="count">por Tipo Proyecto Dash</span></h2>
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
    </section>
    <section class="panel">
      <h2>Detalle por proyecto</h2>
      <table class="data-table">
        <thead><tr><th>Tipo</th><th>Proyecto</th><th class="num">Hs Certificadas</th><th class="num">Hs Ejecutadas</th><th class="num">Margen Absoluto</th><th class="num">Margen Relativo</th></tr></thead>
        <tbody>${RENTABILIDAD_PROYECTOS.map(p => {
          const margenAbsoluto = p.certificadas - p.trabajadas;
          const margenRelativo = p.certificadas ? Math.round((margenAbsoluto / p.certificadas) * 1000) / 10 : 0;
          return `<tr>
            <td>${esc(p.tipo)}</td><td>${esc(p.proyecto)}</td>
            <td class="num">${p.certificadas}</td><td class="num">${p.trabajadas}</td>
            <td class="num">${diffCell(margenAbsoluto)}</td><td class="num">${diffCell(margenRelativo)}%</td>
          </tr>`;
        }).join("")}</tbody>
      </table>
    </section>`;
}

function renderRentabilidad() {
  const persp = state.perspective.rentabilidad;
  return `
    ${perspectiveSwitch("rentabilidad")}
    ${persp === "Resumen" ? renderRentabilidadResumen() : renderRentabilidadCertEjecutadas()}`;
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
  const tree = buildTree(HORASVENDIDAS_ROWS, hierarchy.keys);
  const totalMonthly = sumMonthly(HORASVENDIDAS_ROWS);

  const annual = totalMonthly.reduce((s, v) => s + v, 0);
  const mant = HORASVENDIDAS_ROWS.filter(r => r.tipo === "Mantenimiento").reduce((s, r) => s + r.monthly.reduce((a, b) => a + b, 0), 0);
  const proy = HORASVENDIDAS_ROWS.filter(r => r.tipo === "Proyecto").reduce((s, r) => s + r.monthly.reduce((a, b) => a + b, 0), 0);
  const quarter = q => totalMonthly.slice(q * 3, q * 3 + 3).reduce((a, b) => a + b, 0);

  return `
    ${perspectiveSwitch("horasvendidas")}
    <div class="kpi-grid">
      <div class="kpi"><span class="value">${annual}</span><span class="label">Hs Estimadas anuales</span></div>
      <div class="kpi"><span class="value">${mant}</span><span class="label">Mantenimiento</span></div>
      <div class="kpi"><span class="value">${proy}</span><span class="label">Proyecto</span></div>
      <div class="kpi"><span class="value">${quarter(0)}</span><span class="label">Vendidas Q1</span></div>
      <div class="kpi"><span class="value">${quarter(1)}</span><span class="label">Vendidas Q2</span></div>
      <div class="kpi"><span class="value">${quarter(2)}</span><span class="label">Vendidas Q3</span></div>
      <div class="kpi"><span class="value">${quarter(3)}</span><span class="label">Vendidas Q4</span></div>
    </div>
    <section class="panel">
      <h2>${esc(persp)} <span class="count">${hierarchy.labels.join(" › ")}</span></h2>
      <div class="matrix-scroll">
        <table class="tree-matrix">
          <thead><tr><th class="row-label-col">${hierarchy.labels.join(" / ")}</th>${MESES.map(m => `<th>${m}</th>`).join("")}</tr></thead>
          <tbody>
            ${renderTreeRows("horasvendidas::" + persp, tree, 0)}
            <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v}</td>`).join("")}</tr>
          </tbody>
        </table>
      </div>
    </section>`;
}

function renderComercial() {
  const persp = state.perspective.comercial;
  if (persp === "Participación") {
    return `${perspectiveSwitch("comercial")}
    <section class="panel"><h2>Participación / Condiciones Comerciales</h2>
      <table class="data-table">
        <thead><tr><th>Cliente</th><th>Client Partner</th><th>Condición</th><th class="num">Participación</th></tr></thead>
        <tbody>${COMERCIAL_PARTICIPACION.map(r => `<tr><td>${esc(r.cliente)}</td><td>${esc(r.cp)}</td><td>${esc(r.condicion)}</td><td class="num">${r.participacion}</td></tr>`).join("")}</tbody>
      </table></section>`;
  }
  return `${perspectiveSwitch("comercial")}
  <section class="panel"><h2>Status de Cliente — detalle</h2>
    <table class="data-table">
      <thead><tr><th>Cliente</th><th>Status</th><th>Última revisión</th></tr></thead>
      <tbody>${COMERCIAL_STATUS.map(r => `<tr><td>${esc(r.cliente)}</td><td>${esc(r.status)}</td><td>${esc(r.ultimaRevision)}</td></tr>`).join("")}</tbody>
    </table></section>`;
}

function renderCalendario() {
  const persp = state.perspective.calendario;
  const hierarchy = CALENDARIO_HIERARCHY[persp];
  const tree = buildTree(CALENDARIO_ROWS, hierarchy.keys);
  const totalMonthly = sumMonthly(CALENDARIO_ROWS);
  const grandTotal = totalMonthly.reduce((a, b) => a + b, 0);

  return `
    ${perspectiveSwitch("calendario")}
    <div class="kpi-grid">
      <div class="kpi"><span class="value">${CALENDARIO_ROWS.length}</span><span class="label">Clientes con calendario</span></div>
      <div class="kpi"><span class="value">${grandTotal}</span><span class="label">Entregas/checkpoints (año)</span></div>
    </div>
    <section class="panel">
      <h2>${esc(persp)} <span class="count">${hierarchy.labels.join(" › ")}</span></h2>
      <div class="matrix-scroll">
        <table class="tree-matrix">
          <thead><tr><th class="row-label-col">${hierarchy.labels.join(" / ")}</th>${MESES.map(m => `<th>${m}</th>`).join("")}<th>Total</th></tr></thead>
          <tbody>
            ${renderTreeRows("calendario::" + persp, tree, 0, true)}
            <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v}</td>`).join("")}<td>${grandTotal}</td></tr>
          </tbody>
        </table>
      </div>
    </section>`;
}

// Filtros por subvista — genuinamente distintos por fuente real (ver
// HANDOFF): Productivas usa los slicers reales de "Horas Productivas
// consumidas" (Client Partner/Cliente/Año/Mes); No Asignadas usa los
// slicers reales de "Hs No Asignadas" (Task/Consultor/Año/Mes/Semana/
// Estado) — nunca se copian mecánicamente los 6 slicers de Power BI a
// ambas subvistas solo para que luzcan iguales.
const CAPACIDAD_FILTERS_BY_PERSPECTIVE = {
  "Productivas": [
    { label: "Client Partner", options: ["Todos", "María López", "Diego Ramos"] },
    { label: "Cliente", options: ["Todos", "Andesmar", "Danone", "YPF", "AySA", "Ternium"] },
    { label: "Año", options: ["2026", "2025"] },
    { label: "Mes", options: ["Todos", "Agosto", "Julio"] },
  ],
  "No Asignadas": [
    { label: "Task", options: ["Todos", "01. No Asignación a Servicio en Cliente"] },
    { label: "Consultor", options: ["Todos", "Federico Bressi", "Franco Delucchi", "Gabriela Mendez", "Martina Suárez"] },
    { label: "Año", options: ["2026", "2025"] },
    { label: "Mes", options: ["Todos", "Agosto", "Julio"] },
    { label: "Semana", options: ["Todas"] },
    { label: "Estado", options: ["Todos", "Activo", "Inactivo"] },
  ],
};

function renderCapacidadFilterBar() {
  const fields = CAPACIDAD_FILTERS_BY_PERSPECTIVE[state.perspective.capacidad];
  return `
  <div class="filter-bar">
    ${fields.map(f => `<div class="filter-field"><span>${esc(f.label)}</span><select>${f.options.map(o => `<option>${esc(o)}</option>`).join("")}</select></div>`).join("")}
    <div class="filter-note">Filtros decorativos en este V0 — la jerarquía/matriz es la interacción principal.</div>
  </div>`;
}

const CAPACIDAD_PRODUCTIVAS_TOTAL = CAPACIDAD_PRODUCTIVAS_ROWS.reduce((s, r) => s + r.horas, 0);

function capacidadPctIncidencia(horas) {
  // % Incidencia real (medida `Time Logs Reports.[% Hs ejecutadas]`) =
  // horas de la fila ÷ horas TOTALES (REMOVEFILTERS Cliente/Client
  // Partner) — SIEMPRE contra el gran total, nunca contra el subtotal del
  // grupo padre, tanto en filas hoja como en el rollup de Cliente.
  return (CAPACIDAD_PRODUCTIVAS_TOTAL ? (horas / CAPACIDAD_PRODUCTIVAS_TOTAL * 100) : 0).toFixed(1) + "%";
}

const CAPACIDAD_PRODUCTIVAS_COLUMNS = [
  { label: "Horas", numeric: true, render: r => r.horas, rollup: rows => rows.reduce((s, r) => s + r.horas, 0) },
  { label: "% Incidencia", numeric: true, render: r => capacidadPctIncidencia(r.horas), rollup: rows => capacidadPctIncidencia(rows.reduce((s, r) => s + r.horas, 0)) },
];

function renderCapacidadProductivas() {
  // Orden por Horas de Cliente descendente == el Rank real del PBIP
  // (RANKX por Horas), representado vía orden visual (ver comentario del
  // dataset) — nunca una columna Rank redundante con el orden.
  const clientTotals = new Map();
  CAPACIDAD_PRODUCTIVAS_ROWS.forEach(r => clientTotals.set(r.cliente, (clientTotals.get(r.cliente) || 0) + r.horas));
  const rowsSorted = [...CAPACIDAD_PRODUCTIVAS_ROWS].sort((a, b) => clientTotals.get(b.cliente) - clientTotals.get(a.cliente));
  const tree = buildTree(rowsSorted, ["cliente", "proyecto"]);
  return `
    <div class="kpi-grid"><div class="kpi"><span class="value">${CAPACIDAD_PRODUCTIVAS_TOTAL}</span><span class="label">Total Horas</span></div></div>
    <section class="panel">
      <h2>Horas consumidas por Cliente <span class="count">Cliente › Proyecto, ordenado por Rank (Horas desc.)</span></h2>
      <div class="matrix-scroll">
        <table class="tree-matrix">
          <thead><tr><th class="row-label-col">Cliente / Proyecto</th><th>Horas</th><th>% Incidencia</th></tr></thead>
          <tbody>${renderBusinessTreeRows("capacidad::Productivas", tree, 0, CAPACIDAD_PRODUCTIVAS_COLUMNS)}</tbody>
        </table>
      </div>
    </section>`;
}

function renderCapacidadNoAsignadas() {
  // Row label = "Consultor (Hs Asig)" — MISMA columna calculada que la
  // fuente real (Colaboradores["Consultor + Hs asig"]), no una etiqueta
  // inventada. `hsAsig` es la capacidad de referencia estática del
  // consultor, separada de `monthly` (horas NO asignadas reales por mes,
  // ver dataset).
  const rows = CAPACIDAD_NOASIGNADAS_ROWS.map(r => ({ ...r, consultorLabel: `${r.consultor} (${r.hsAsig} hs Asig)` }));
  const tree = buildTree(rows, ["consultorLabel"]);
  const totalMonthly = sumMonthly(rows);
  const stateKey = "capacidad::No Asignadas";
  return `
    <section class="panel">
      <h2>Horas No Asignadas <span class="count">Consultor — Task: No Asignación a Servicio en Cliente</span></h2>
      <div class="matrix-scroll">
        <table class="tree-matrix">
          <thead><tr><th class="row-label-col">Consultor</th>${MESES.map(m => `<th>${m}</th>`).join("")}</tr></thead>
          <tbody>
            ${renderTreeRows(stateKey, tree, 0)}
            <tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${v}</td>`).join("")}</tr>
          </tbody>
        </table>
      </div>
      <p class="empty-note" style="text-align:left">Horas No Productivas — registradas en Time por cada colaborador (ver HANDOFF). Potential overlap with Staffing — integration decision pending.</p>
    </section>`;
}

function renderCapacidad() {
  const persp = state.perspective.capacidad;
  return `
    ${perspectiveSwitch("capacidad")}
    ${persp === "Productivas" ? renderCapacidadProductivas() : renderCapacidadNoAsignadas()}`;
}

const RENDERERS = {
  cartera: renderCartera, rentabilidad: renderRentabilidad, horasvendidas: renderHorasVendidas,
  comercial: renderComercial, calendario: renderCalendario, capacidad: renderCapacidad,
};

function render() {
  document.getElementById("reporting-experience-tabs").innerHTML = EXPERIENCES.map(e => `
    <button class="${state.experience === e.id ? "is-active" : ""}" onclick="setExperience('${e.id}')">${esc(e.label)}</button>
  `).join("");
  // Cartera y Capacidad tienen su propio filter bar (campos reales de su
  // fuente, distintos entre sí y del resto — ver correctives) — las demás
  // experiencias siguen recibiendo el filter bar compartido, sin cambios.
  let filterBarHtml;
  if (state.experience === "cartera") filterBarHtml = renderCarteraFilterBar();
  else if (state.experience === "capacidad") filterBarHtml = renderCapacidadFilterBar();
  else filterBarHtml = renderFilterBar();
  document.getElementById("reporting-main-content").innerHTML = filterBarHtml + RENDERERS[state.experience]();
}

// --- V0 assessment drawer ---------------------------------------------------
const MAPPING = [
  { from: ["Analisis Mantenimiento", "Analisis Proyectos"], to: "Cartera / Proyectos" },
  { from: ["Analisis Rentabilidad", "Hs Certificadas vs Hs Ejecutadas"], to: "Rentabilidad / Performance" },
  { from: ["Estimación Horas Vendidas", "Estimación Horas Vendidas x CP"], to: "Horas Vendidas" },
  { from: ["Analisis Participacion - Condiciones Comerciales", "Client Partner - Status de Cliente detalle"], to: "Comercial / Clientes" },
  { from: ["Calendario x Frecuencia", "Calendario x CP"], to: "Calendario" },
  { from: ["Hs No Asignadas", "Horas Productivas consumidas"], to: "Capacidad / Ejecución" },
];

function openDrawer() {
  document.getElementById("reporting-drawer-map").innerHTML = MAPPING.map(m => `
    <div class="map-row">
      <div class="from">${m.from.map(esc).join("<br>")}</div>
      <div class="arrow">↓</div>
      <div class="to">${esc(m.to)}</div>
    </div>`).join("");
  document.getElementById("reporting-drawer").classList.add("is-open");
  document.getElementById("reporting-drawer-overlay").classList.add("is-open");
}
function closeDrawer() {
  document.getElementById("reporting-drawer").classList.remove("is-open");
  document.getElementById("reporting-drawer-overlay").classList.remove("is-open");
}

render();
