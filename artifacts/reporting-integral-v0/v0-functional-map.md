# Reporting Integral — V0 Functional Map

## Functional status

**FROZEN FOR REAL-DATA INTEGRATION.**

The six experiences below, their filter/hierarchy/rollup semantics, and the
"Actualizar" maintenance-link contract are validated against the real PBIP
source (`Danwor - Tablero Integral.SemanticModel`/`.Report`) and are not to
be redesigned without new owner input. Data is still a hardcoded, in-memory
mock in `webapp/static/reporting.js` — see "Known V0 limitations" below.

## Final experiences

### 1. Cartera / Proyectos

- **Source pages:** "Analisis Mantenimiento" (perspective *Mantenimiento*),
  "Analisis Proyectos" (perspective *Proyecto*).
- **Hierarchy:** Cliente › Proyecto (both perspectives).
- **Filters:** Proyecto, Cliente, Estado (checkbox — default "En curso"
  only); Mantenimiento adds Desde/Hasta (range over `fecha`, defaults to
  first-of-current-month → today); Proyecto adds Año de Inicio instead
  (Mantenimiento's source page has no Mes/Año field at all).
- **Measures/columns:** Diff Hs, Hs Trabajadas Acum., Hs Certif. Acum., Hs
  Vendidas Mensuales, Hs Facturadas (real additive hours, not a %), Estado.
- **Special rules:** Estado column rollup is categorical-uniform (parent
  shows a value only if every leaf agrees); KPI tiles mirror the 5 numeric
  columns via the same `rollup` formula (single source of truth).
- **Maintenance surface:** SharePoint — accessible through "Actualizar",
  dynamic year (Hasta's year for Mantenimiento, Año de Inicio for Proyecto,
  current year as fallback).

### 2. Rentabilidad

- **Source page:** "Analisis Rentabilidad" ("Rentabilidad Unpivot" table).
- **Perspectives:** *Resumen* (ranking by "Rentabilidad horas" +  monthly
  Concepto matrix), *Certificadas vs Ejecutadas* (grouped bars by Tipo
  Proyecto Dash + detail table).
- **Filters:** Tipo Proyecto, Proyecto.
- **Measures:** Rentabilidad horas = Horas Certificadas − Horas Trabajadas
  (never the sum of the 5 Concepto rows). Concepto order is the real
  `Orden` sortByColumn (1 Horas Vendidas, 2 Horas Certificadas, 3 Horas
  Trabajadas, 4 Rentabilidad horas, 5 No asignadas a servicios); monthly
  values are genuine monthly figures, not cumulative.
- **Special rules:** Resumen's 4 top KPI tiles are fixed portfolio totals
  (unfiltered); only the ranking and monthly matrix respond to filters.
  Native `title` tooltips expose the full comparison behind each bar.
- **Maintenance surface:** none — no target supplied.

### 3. Horas Vendidas

- **Source:** "HorasVendidas" measure family (Proyectos/Meses).
- **Perspectives:** *Proyecto* (Tipo › Cliente › Proyecto), *Client
  Partner* (Client Partner › Tipo › Cliente › Proyecto).
- **Filters:** Client Partner, Tipo Proyecto, Cliente, Proyecto.
- **Measures:** 12-month matrix + Total row (real numeric sum, always).
- **Maintenance surface:** none — no target supplied.

### 4. Participación / Comercial

- **Source pages:** "Analisis Participacion - Condiciones Comerciales -"
  (perspective *Participación*), "Client Partner - Status de Cliente
  detalle" (perspective *Salud de Cliente*).
- **Hierarchy:** Participación = ID Frecuencia › Permite Adelanto ›
  Condición de Pago › Cliente (exact Rows order of the source pivotTable).
  Salud = Client Partner › Cliente.
- **Filters:** Client Partner, Cliente (both perspectives; Año/Status
  slicers hidden — no clean dataset dimension backs them yet).
- **Measures:** % Participación Hs Vendidas / % Participación Hs
  Certificadas — `DIVIDE` against a grand total with
  `REMOVEFILTERS(Client Partner, ID Frecuencia, Permite Adelanto,
  Condición de Pago, Cliente)`, always fixed, never the visible subtotal.
  Salud: 5 categorical health columns + Salud Final (no rollup — text, not
  a measure).
- **Special rules:** no KPI tiles (neither source page has a card visual).
- **Maintenance surface:** SharePoint — accessible through "Actualizar"
  (same Empresas view as Calendario, static).

### 5. Calendario

- **Source:** `Calendario_Cliente` table ("Calendario x Frecuencia"/"Calendario
  x CP" — same pivotTable, row order only differs).
- **Perspectives:** *Frecuencia* (ID Frecuencia › Cliente), *Client
  Partner* (Client Partner › Cliente).
- **Filters:** Cliente, Client Partner, Frecuencia (real business order,
  not alphabetical).
- **Measures:** monthly cells are BOOLEAN presence indicators (✓/blank),
  never a count — "Semanal" means "present every month," not "4×/month."
  Total column/row stays a genuine numeric count.
- **Maintenance surface:** SharePoint — accessible through "Actualizar"
  (Empresas view, static).

### 6. Horas Productivas

- **Source pages:** "Horas Productivas consumidas" (perspective
  *Productivas*), "Hs No Asignadas" (perspective *No Asignadas*).
- **Hierarchy:** Productivas = Cliente › Proyecto, ordered by Cliente Horas
  descending (represents the real RANKX, no redundant Rank column). No
  Asignadas is flat (Consultor + Hs Asig reference capacity, no children).
- **Filters:** Productivas → Cliente; No Asignadas → Consultor (the only
  real slicers on each source page).
- **Measures:** % Incidencia = row Horas ÷ grand total, fixed denominator
  (`REMOVEFILTERS` Cliente/Client Partner), same contract as Comercial's
  Participación %. No Asignadas: HORAS filtered to Task "01. No Asignación
  a Servicio en Cliente" — distinct from Staffing's availability view.
- **Maintenance surface:** none — no target supplied.

## Important formulas

- **Rentabilidad horas** = Horas Certificadas − Horas Trabajadas.
- **% Participación (Vendidas/Certificadas)** = `DIVIDE(numerador,
  CALCULATE(numerador, REMOVEFILTERS(<dimensiones del árbol>)))` — always
  against the grand total, recomputed from raw leaf sums under a rollup,
  never by summing/averaging the already-rendered %.
- **% Incidencia (Horas Productivas)** = row Horas ÷ grand total
  (`REMOVEFILTERS` Cliente/Client Partner) — same fixed-denominator
  contract as Participación.
- **Margen Absoluto/Relativo (Rentabilidad Cert. vs Ejec.)** = Certificadas
  − Trabajadas, and that difference ÷ Certificadas.

## Rollup rules

Three measure classes, one contract (`sumField`/`uniformValue` in
`reporting.js`), reused by Cartera, Horas Productivas, and Comercial/
Participación — never a per-experience aggregation:

- **A. Additive** — `SUM` of filtered leaf rows.
- **B. Derived/ratio against a fixed denominator** — re-sum the raw
  numerator across the subtree, then run it through the same function that
  computes a leaf row's % against the fixed grand total. Never sums or
  averages the already-rendered %.
- **C. Categorical** — no rollup (blank parent) by default, or
  `uniformValue` when the parent's value is objectively derivable: all
  leaves under the subtree share exactly one distinct value. Two-or-more
  distinct values, or zero: blank. Never "worst/best/majority wins."

Collapsing a node only hides its children — it never changes what the
parent shows; parent aggregates are computed from the full leaf set
regardless of expand/collapse state.

## Source-to-target map

| Reporting experience | Source PBI page(s) |
|---|---|
| Cartera / Proyectos | Analisis Mantenimiento, Analisis Proyectos |
| Rentabilidad | Analisis Rentabilidad |
| Horas Vendidas | (HorasVendidas measure family, no dedicated single page) |
| Participación / Comercial | Analisis Participacion - Condiciones Comerciales -, Client Partner - Status de Cliente detalle |
| Calendario | Calendario x Frecuencia, Calendario x CP |
| Horas Productivas | Horas Productivas consumidas, Hs No Asignadas |

Full page/visual inventory: `artifacts/reporting-integral-v0/page_inventory.json`.

## Known V0 limitations

- All data is hardcoded/in-memory mock in `reporting.js` — no SharePoint/
  Graph connection, no persistence.
- Comercial/Salud de Cliente's Verde/Amarillo/Rojo domain is a
  representative convention (real SharePoint column, value domain not
  verifiable without live data).
- Some source-page slicers (e.g. Comercial's Año, Salud's Status) are
  hidden because no clean dataset dimension backs them in the current mock.
- "Actualizar" is only wired for Cartera, Calendario, and Participación/
  Comercial — Rentabilidad, Horas Vendidas, and Horas Productivas have no
  maintenance target supplied yet.
- No automated test coverage for `reporting.js` (validated manually/live
  per batch, consistent with this being a preview surface).

## Next phase — real-data integration

Real data should reach these six experiences through a normalized
service/repository contract (mirroring Staffing's `StaffingService`
pattern: one service resolving Graph/SharePoint data into the same row
shapes `reporting.js` already consumes), not direct Graph calls scattered
across individual renderers. The facet/rollup/tree backbone in
`reporting.js` is source-agnostic already — swapping the mock arrays for
service-backed data should not require touching the rendering contract.
