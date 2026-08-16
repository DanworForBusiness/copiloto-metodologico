# Reporting Integral — Real-Data Lineage (Phase 1: CARTERA only)

> **ESTADO: CARTERA REAL-DATA = VALIDADA EN VIVO (owner smoke OK).** Ambas
> perspectivas cargan datos reales; jerarquía y rollups verificados (ej. rango
> 01/06–31/07/2026: Mantenimientos=71, Trabajadas=1761, Certificadas=8544,
> Vendidas=9862; Aluar rollup Trab=800/Cert=1544/Vend=1680/Estado=En curso). El
> cero de agosto 2026 es DATO real (planeadas/vendidas cargadas, trabajadas/
> certificadas/facturadas aún en 0), no un defecto de mapeo/render. Diagnósticos
> temporales del live-smoke ya RETIRADOS. Las otras 5 experiencias siguen en mock.

Fuente autoritativa de lineage: PBIP `E:\Joe\Metodologia\Refactorizacion\Tablero
Integral\Danwor - Tablero Integral.pbip` (SemanticModel TMDL, particiones M).
NO se scrapeó ninguna página de SharePoint UI. Este documento cubre SOLO Cartera
(Mantenimiento + Proyecto); las otras 5 experiencias siguen en mock.

## 1. Sitio + auth (reuso, sin mecanismo nuevo)

- **Sitio:** `https://danwor.sharepoint.com/sites/Gestion` → `config.SHAREPOINT_GESTION_SITE_ID`
  (ya configurado; **Staffing ya lee este sitio hoy**).
- **Auth:** token delegado de usuario (MSAL en el browser → `sessionStorage` →
  POST `graph_token` al backend), idéntico al patrón Staffing. Transporte:
  `core/microsoft365.py` (`graph_get_json`, `graph_get_paged_values` con
  `@odata.nextLink`, `graph_get_bytes`). **No** se crea un segundo cliente Graph.
- **Permiso:** `Sites.Read.All` (delegado). **Ya probado en producción**: las
  listas Proyectos y Cliente (abajo) ya son leídas hoy por `StaffingRepository`
  con este mismo token/scope. "Horas por proyectos" es una lista NUEVA dentro del
  MISMO sitio y scope → no requiere permiso adicional (a confirmar en el smoke).

## 2. Fuentes físicas (Power Query / M)

| Fuente | Tipo | Sitio | GUID / ruta | En config hoy |
|---|---|---|---|---|
| **Horas por proyectos** | SharePoint list (OData) | Gestion | `6c3a8689-e7ed-4a2c-b358-10c5ca1b37b8` | **NO (agregar)** |
| **Proyectos** | SharePoint list | Gestion | `efa2e279-593e-4321-867f-1ff982d800f6` | SÍ (`SHAREPOINT_PROYECTOS_LIST_ID`) |
| **Cliente** | SharePoint list (OData) | Gestion | `9c09571c-4fb8-4841-8f0d-4def03b3130d` | SÍ (`SHAREPOINT_CLIENTE_LIST_ID`) |
| **Horas Facturadas Proyectos-Período** | Excel en biblioteca de documentos | Gestion | `Documentos compartidos/General/Reportes Horas por Proyectos/Horas Facturadas Proyectos-Período.xlsx` | NO — ver GAP |

## 3. Campos fuente consumidos

### Horas por proyectos (grano: proyecto × mes) — el "hecho" de Cartera
- `ProyectoId` (lookup → une con Proyectos.Id)
- `EmpresaId`
- `Proyecto` (lookup expandido → `Proyecto/Title`)
- `Año`, `Mes`
- `HsTrabajadas`, `HsVendidas`, `HsFacturadas` (M lo renombra a **"Hs certificadas"**)

### Proyectos (dimensión de proyecto) — ya leída por Staffing
- `Id`, `EmpresaId`, `ID Proyecto Danwor`
- `TipodeProyecto` → `Tipo Proyecto Dash` = `if "Tiempo & Materiales" then "Mantenimiento" else TipodeProyecto` (define el split Mantenimiento vs Proyecto)
- `FechadeInicio` → `Año de Inicio` = `YEAR(FechadeInicio)`
- `Estado` (para el status del proyecto)

### Cliente (dimensión de cliente) — ya leída por Staffing
- `Cliente` (nombre)
- `Client Partner` (lookup expandido → `ClientPartner/Title`)

## 4. Joins / transformaciones

```
Horas por proyectos (hecho, mensual)
   ├─ ProyectoId ─→ Proyectos.Id      ⇒ Tipo Proyecto Dash, Estado, ID Proyecto Danwor, Año de Inicio
   └─ EmpresaId  ─→ Cliente.<key>     ⇒ Cliente (nombre), Client Partner
```

- **Split de perspectiva:** `Tipo Proyecto Dash == "Mantenimiento"` → perspectiva
  Mantenimiento; caso contrario → perspectiva Proyecto.
- **`diff` (derivado, NO fuente):** `certificadas − trabajadas` (verificado 1:1
  contra los 7 mocks V0). Vive en el service/presentación, no en el repositorio.
- **Mantenimiento** conserva el grano mensual (`Año`/`Mes`, filtro por rango de
  fechas). **Proyecto** agrega sobre la vida del proyecto (sin `Mes`; filtra por
  `Año de Inicio`).

## 5. Contrato normalizado (Reporting/negocio, sin nombres internos SharePoint)

Leaf **Mantenimiento** (perspectiva `"Mantenimiento"`):
```
{ cliente, clientPartner, proyecto, estado,
  anio, mes, fecha,                 // fecha = primer día del mes (rango Desde/Hasta)
  hsTrabajadas, hsCertificadas, hsVendidas, facturadas }
  // diff se deriva en el service: hsCertificadas - hsTrabajadas
```
Leaf **Proyecto** (perspectiva `"Proyecto"`):
```
{ cliente, clientPartner, idProyecto, proyecto, estado,
  anioInicio,
  hsTrabajadas, hsCertificadas, hsVendidas, facturadas }
```
Mapeo renderer V0 → contrato: `trabajadas←hsTrabajadas`, `certificadas←hsCertificadas`,
`vendidasMensuales`/`vendidas←hsVendidas`, `status←estado`, `cp←clientPartner`.
El adapter mapea nombres crudos (`A_x00f1_o`, `HsFacturadas`, `Proyecto/Title`, …)
UNA sola vez en el boundary repositorio; nada de eso llega a `reporting.js`.

## 6. XLSX Facturadas — lineage resuelta (perspectiva Proyecto)

Fuente PBIP: tabla `Horas Facturadas` (partición M).

- **Origen:** `SharePoint.Files("https://danwor.sharepoint.com/sites/Gestion")`
  → archivo `Horas Facturadas Proyectos-Período.xlsx`, carpeta
  `Documentos compartidos/General/Reportes Horas por Proyectos/`.
  Drive-relative path: `General/Reportes Horas por Proyectos/Horas Facturadas Proyectos-Período.xlsx`
  (biblioteca por defecto "Documentos compartidos"/"Shared Documents").
- **Tabla del workbook:** Excel Table **`Tabla24`** (`Kind="Table"`), no una hoja plana.
- **Columnas fuente:** `Proyecto Nombre`, `Proyecto Base`, `Año` (int),
  `Mes Facturado` (text), `Suma de Cantidad` (number = HORAS facturadas),
  `Fecha Factura`, `Periodo Desde`, `Periodo Hasta`, `ID`.
- **Renames M:** `Proyecto Nombre`→`Proyecto Nombre Original`;
  `Proyecto Base`→`Proyecto Nombre`.
- **Clave de join (derivada):** `Key_proyect_desc = Text.Upper(Replace(" ","", [Proyecto Nombre]))`
  (es decir, `Proyecto Base` en MAYÚSCULAS sin espacios). Coincide EXACTAMENTE con
  `Horas por proyectos.Proyecto_Key` (misma transformación `Upper(Replace(" ",""))`
  sobre `Proyecto`). **⇒ join por nombre-de-proyecto normalizado, NO por Año/Mes.**
- **Agregación:** para la perspectiva **Proyecto**, `facturadas` =
  `SUM('Horas Facturadas'[Suma de Cantidad])` agrupado por `Key_proyect_desc`
  (suma sobre todos los períodos del proyecto). Año/Mes NO participan del join de
  Proyecto (esa perspectiva agrega sobre la vida del proyecto).
- **Nulls:** un proyecto sin filas en el XLSX ⇒ `facturadas = 0` (aditivo).
  Errores de import de `ID` → `PROJ-ERRORIMP` (no relevante para la medida).

**Regla de `facturadas` por perspectiva (source-faithful):**
- **Proyecto:** desde el XLSX (`Suma de Cantidad` sumado por `Proyecto_Key`).
- **Mantenimiento:** el mock V0 tiene `facturadas == certificadas`; en PBIP
  ambas derivan de `HsFacturadas` de la lista mensual, así que Mantenimiento
  mapea `facturadas ← hsCertificadas` (= HsFacturadas). NO usa el XLSX.

## 6b. Extracción de `Tabla24` — hardening (live-smoke prep)

Hecho confirmado de openpyxl (no una suposición): en modo `read_only` **NO se
parsea la metadata de Excel Tables** (`ws.tables` queda vacío), así que localizar
`Tabla24` por su rango de tabla es imposible en ese modo. El lector se cambió de
"primera hoja / A1" a **anclaje al header real**: escanea cada hoja y toma como
header la primera fila que contenga TODAS las columnas requeridas
(`Proyecto Base` + `Suma de Cantidad`), leyendo las filas siguientes como datos.
Robusto a filas de relleno previas y a que la tabla no esté en la primera hoja
(cubierto por regresión). Header ausente en todas las hojas ⇒
`ReportingSourceError(stage="table")`.

## 6c. Convención de lookups de Graph (root cause del resultado vacío)

El primer smoke con auth OK devolvió `mantenimiento:[] , proyecto:[]`. Causa:
en Graph `$expand=fields`, las columnas **lookup** de "Horas por proyectos"
aparecen como **`ProyectoLookupId`/`EmpresaLookupId`** (el ID del item target),
NO como `ProyectoId`/`EmpresaId` (que es la forma aplanada que muestra el M/PBIP),
y el **título del proyecto NO viaja en la fila de Horas** (Graph no expande el
Title del lookup). El adapter original leía `ProyectoId`/`EmpresaId` y el título
desde Horas ⇒ join fallaba ⇒ `tipoProyecto=""` y título vacío ⇒ ambas
perspectivas vacías.

**Fix (solo repositorio, robusto a la convención, no una suposición puntual):**
- lookup key = primer valor no vacío entre `ProyectoLookupId`→`ProyectoId`
  (idem Empresa), contra `item["id"]` del target (no `fields["id"]`);
- título del proyecto = `Proyectos.Title` del item unido (fallback al aplanado);
- diagnóstico temporal en la respuesta (`diagnostics`) + log server-side con
  conteos por etapa + `horas_field_keys`/`proyectos_field_keys`/`cliente_field_keys`
  (solo NOMBRES de clave, nunca valores) para confirmar el internal name real.

Pendiente de confirmar en el re-smoke del owner: que `horas_project_match` y
`horas_cliente_match` sean > 0 y que el `Title` sea efectivamente el nombre del
proyecto (si el nombre viviera en otra columna de Proyectos, es un ajuste de
constante en el repositorio).

## 6d. Nombres internos REALES (live smoke, re-smoke tras ed68645)

Conteos del re-smoke: proyectos_raw=300, clientes_raw=127, horas_raw=1784,
facturadas_xlsx_keys=116; horas_project_match=1767/1767 (100% de referenciados);
horas_cliente_match=1723 (44 unmatched); tipo_counts Mantenimiento=1518 /
Proyecto=249 / ""=17 (los 17 = filas de Horas sin referencia de proyecto).

Los valores salían en blanco porque el adapter usaba los nombres "lindos" del
PBIP. Internal names REALES corregidos (SharePoint codifica ñ/espacios):

| concepto | internal name real | antes (mal) |
|---|---|---|
| Año (Horas) | `A_x00f1_o` | `Año` |
| Estado (Proyectos) | `Status` | `Estado` |
| ID Proyecto Danwor | `ID_x0020_Proyecto_x0020_Danwor` | `ID Proyecto Danwor` |
| Cliente (nombre) | `Cliente.Title` | `Cliente` |
| lookup proyecto (Horas) | `ProyectoLookupId` | `ProyectoId` |
| lookup empresa (Horas) | `EmpresaLookupId` | `EmpresaId` |
| nombre proyecto | `Proyectos.Title` (join) | Horas.`Proyecto` |

**Client Partner** — es un LOOKUP de la lista Cliente (`ClientPartnerLookupId`).
El target NO se asume: la relación de PMs en el modelo es con `Proyectos.PMId`
(no con Cliente), así que PMs quedó DESCARTADO como target. El target se
DESCUBRE en runtime de la metadata de columnas de Cliente
(`GET /lists/{cliente}/columns` → columna lookup `ClientPartner` → `lookup.listId`),
se carga una vez y se mapea `ClientPartnerLookupId → Title`. `client_partner_
target_found`/`client_partner_resolved` en diagnósticos lo confirman.

**Hs Certificadas (Mantenimiento)** — lineage PBIP: el M renombra la columna
fuente `HsFacturadas` → "Hs certificadas" (Horas por proyectos.tmdl línea 313) =
**Outcome A** (campo interno de la lista, no otra query ni derivación). Graph
puede omitir el campo por-item cuando es null; el diagnóstico `horas_with_
hsfacturadas` (+ `horas_field_keys` como UNIÓN sobre todos los items) confirma su
presencia real. NO se sustituye por HsVendidas/Auditado/XLSX (prohibido).

**44 Cliente unmatched** — quedan visibles como conteo (`horas_cliente_unmatched`).
Pendiente de clasificar en el re-smoke: referencias de Empresa stale/borradas vs
items de Cliente faltantes. No se fabrican valores para ellas.

## 8. Phase 2 — Lineage de las 5 experiencias restantes (discovery)

Fuente: PBIP SemanticModel TMDL (particiones M + tablas calculadas DAX). Hallazgo
central: **casi todo reusa el backbone de Cartera** (listas Horas por proyectos /
Proyectos / Cliente + resolución Client Partner + XLSX Facturadas, ya construidos).
Solo hay **2 fuentes físicas nuevas**.

### Dimensiones/columnas ya disponibles (reuso, sin fuente nueva)
- Lista **Cliente** (`SHAREPOINT_CLIENTE_LIST_ID`) ya trae — además de Title/
  ClientPartnerLookupId — las columnas de Participación/Calendario:
  `Frecuencia Actualizacion`, `Permite Adelanto`, `Condicion de Pago`,
  `Calendario_Act` (texto con nombres de mes), `ID Frecuencia` (derivada).
- **Horas por proyectos** ya trae HsVendidas/HsTrabajadas/Hs certificadas por
  proyecto×mes; **Proyectos** trae Tipo/Estado/ID/FechaInicio; **Client Partner**
  se resuelve por `/columns`. Todo esto es reutilizable tal cual.

### Por experiencia
| Experiencia | Perspectivas | Fuente física | Reuso | Nuevo |
|---|---|---|---|---|
| **Horas Vendidas** | Proyecto / Client Partner | Horas por proyectos + Proyectos + Cliente | 100% backbone Cartera | — |
| **Participación/Comercial** | Participación / Salud | Cliente (Frecuencia/Permite Adelanto/Condicion de Pago) + Horas por proyectos (%) | backbone | Salud: columnas categóricas de Cliente |
| **Calendario** | Frecuencia / Client Partner | `Cliente.Calendario_Act` (booleano por mes vía CONTAINSSTRING) | backbone Cliente | — |

**Grano confirmado del "Total" (Q3, owner live QA — ver HANDOFF)**: la
tabla CALCULADA `Calendario_Cliente` del PBIP (`definition/tables/
Calendario_Cliente.tmdl`) es UNA fila por Cliente (`VALUES(Cliente[Cliente])`),
12 columnas booleanas 0/1 (`IF(CALCULATE(COUNTROWS(Cliente),
CONTAINSSTRING(Cliente[Calendario_Act], "<mes>")) > 0, 1, 0)` — EXACTA la
misma regla ya implementada en `_calendario_monthly`), y
`Total = Ene+Feb+...+Dic` POR FILA (grano Cliente, no un gran total de
compañía). El footer "Total" de la matriz del Hub (`sumMonthly(rows)`
sobre la lista PLANA de Cliente, calculado UNA vez, nunca por nodo de
árbol) reproduce el subtotal de columna que Power BI calcularía sobre
`Calendario_Cliente` — SIEMPRE al grano de hoja, independiente del estado
expandido/colapsado de las filas (mismo comportamiento que un subtotal de
matriz real, que nunca cambia por interacción de UI). La aparente
"no reconciliación" reportada por el owner es la consecuencia ESPERADA de
mostrar presencia BOOLEANA (✓) en filas padre colapsadas mientras el
footer es una cuenta real de hojas — no es un bug, no hay doble conteo
(verificado con evidencia de ejecución, ver
`tests/webapp/reporting_calendario_total_grain.node.js`). Sin cambio de
código; el comportamiento actual ya coincide con el grano PBIP.
| **Horas Productivas** | Productivas / No asignadas | **AMBAS perspectivas: `Time Logs Reports` CSV** (`SUM(HORAS)`, misma tabla, split por filtro — ver §8bis) | — | **CSV en /sites/Operaciones/…/Template Dashboard** |
| **Rentabilidad** | Resumen / Certif vs Ejec | Horas por proyectos + XLSX Facturadas; "Ejecutadas" vía `Comparativo`/`Resumen Horas x Proyecto Fac y Ejec` (DAX) | backbone | semántica **Ejecutadas** + matriz mensual acumulada de Conceptos |

### Semántica (de V0 + PBIP, a preservar)
- **Rentabilidad**: `Rentabilidad horas = Certificadas − Trabajadas`; `Margen Absoluto
  = Certificadas − Ejecutadas`; `Margen Relativo = Margen Abs / Certificadas`
  (denominador 0 → sin ratio). Conceptos: Vendidas/Certificadas/Trabajadas/
  Rentabilidad horas/No asignadas. Agregar y LUEGO recomputar el ratio (nunca
  promediar ratios de hijos).
- **Participación**: `% Hs Vendidas 2` / `% Hs Certificadas 2` con denominador
  **GRAN TOTAL FIJO** (`REMOVEFILTERS(Client Partner, ID Frecuencia, Permite
  Adelanto, Condicion de Pago, Cliente)`) — los grupos visibles NO suman 100%,
  NO renormalizar. **Salud de Cliente**: 5 componentes categóricos + Salud Final,
  sin agregación numérica.
- **Calendario**: celda mensual = PRESENCIA BOOLEANA (✓/blank), NUNCA conteo.
- **Horas Productivas**: horas aditivas; `% Incidencia` con denominador gran-total
  fijo; **AMBAS perspectivas (Productivas y No asignadas) leen `Time Logs
  Reports` (CSV)** — ver §8bis, corrección de lineage. No asignadas = matriz
  consultor×mes (NO es disponibilidad de Staffing — responsabilidad analítica
  distinta).

## 8bis. CORRECCIÓN DE LINEAGE — Horas Productivas/Productivas (evidencia PRIMARIA del PBIP)

**Contexto:** Batch A (commit `5dcbb58`) implementó Productivas reusando el
backbone de Cartera (`SUM(hsTrabajadas)` de "Horas por proyectos"), justificado
SOLO por correlación de valores entre el mock `CAPACIDAD_PRODUCTIVAS_ROWS.horas`
y `trabajadas` de Cartera (los 6 valores coincidían exactamente). El owner
marcó esto correctamente como **evidencia corroborante fuerte pero NO
suficiente** — exigió verificación contra evidencia PRIMARIA del PBIP (TMDL/
DAX/M/relaciones/bindings de visual), no solo coincidencia de mock.

**Verificación PRIMARIA realizada** (`Danwor - Tablero Integral.pbip`, sitio
E:\Joe\Metodologia\Refactorizacion\Tablero Integral):
- **Página del reporte "Horas Productivas consumidas"** (`definition/pages/
  7d1191c1e755ac3abe70/page.json`): TODOS los visuals de medida (Horas, Rank,
  % Hs ejecutadas) bindean a `Entity: "Time Logs Reports"`, `Property: "HORAS"`
  — CERO bindings a `Horas por proyectos`/`hsTrabajadas` en esta página.
  Filtro de página: `NOT Cliente[Cliente] CONTAINS "Danwor"` (excluye el/los
  cliente(s) placeholder interno(s) donde se cargan las horas no asignadas).
- **Página "Hs No Asignadas"** (`definition/pages/5685cf18d9eca3f61e36/
  page.json`): TAMBIÉN bindea a `Time Logs Reports`/`HORAS`, agrupado por
  Consultor (`Colaboradores`) en vez de Cliente/Proyecto. Filtro de página:
  `'Time Logs Reports'[Project] IN {"Danwor - Horas No Productivas (#1)",
  "Danwor - Horas No Productivas (#2)"}` — dos proyectos PLACEHOLDER internos.
- **Conclusión — Outcome (B) de las 3 hipótesis planteadas por el owner**:
  "Horas Productivas" (AMBAS perspectivas) = `SUM('Time Logs Reports'[HORAS])`,
  la MISMA tabla/medida, diferenciadas SOLO por filtro (excluye vs. incluye los
  2 proyectos placeholder) y por dimensión de agrupamiento (Cliente×Proyecto vs.
  Consultor). El comentario original de V0 en `reporting.js`
  (`// Horas = Sum(Time Logs Reports.HORAS)`) era CORRECTO; el resumen de Batch
  A en este documento (ahora corregido arriba) y la implementación de `5dcbb58`
  estaban equivocados — la correlación con `hsTrabajadas` era COINCIDENCIA (o
  refleja que en la práctica ambas fuentes tienden a converger para estos 6
  proyectos puntuales), NO la misma fuente física.
- **Join Time Logs → Cliente** (para resolver el nombre real de Cliente por
  fila de CSV, ya que el CSV no trae ningún lookup): `'Time Logs
  Reports'.Key_proyect_desc → Proyectos.Key_proyect_desc` (join por texto
  normalizado Upper/sin-espacios — MISMO criterio ya usado por el join de la
  XLSX de Facturadas, `_norm_key`), luego `Proyectos.EmpresaLookupId →
  Cliente.id` (MISMO lookup ya CONFIRMADO contra Graph real por
  `staffing_repository.py` — comentario: "Proyectos lookup real name
  'Empresa', fieldValueSet property 'EmpresaLookupId' → Cliente.Title").
  Reusa las MISMAS listas Proyectos/Cliente ya fetcheadas por Cartera/Batch A
  — sin lista nueva para la dimensión, solo el hecho (CSV) es nuevo.
- **M query de la partición `Time Logs Reports`** (fuente física CONFIRMADA):
  `SharePoint.Files("https://danwor.sharepoint.com/sites/Operaciones")` →
  filtra `[Extension] = ".csv"` y `Text.Contains([Folder Path], "Template
  Dashboard")` y `not Text.Contains([Name], "OLD")` → combina todos los CSV
  matcheados (`Csv.Document(...,[Delimiter=",", Encoding=65001,
  QuoteStyle=QuoteStyle.Csv])`, header promovido) → columnas: Date/From/To/
  Description/Duration/`Duration Decimal`/Client/Project/Task/User/Email/
  `Billable Amount`/Billable/Cost. `HORAS = [Duration Decimal] / 1000`. La
  MISMA carpeta `General/Template Dashboard` en el sitio Operaciones que ya usa
  `staffing_repository.ABSENCE_XLSX_PATH` (confirmada existente/alcanzable).

**Impacto:** el estado "BATCH A IMPLEMENTATION READY FOR LIVE VALIDATION" para
Horas Productivas/Productivas (HANDOFF, sección "-3.") queda **BLOQUEADO
específicamente en esa perspectiva** hasta que Batch B reimplemente
`get_horas_productivas` sobre el adapter de Time Logs — las otras 4
experiencias de Batch A (Horas Vendidas/Participación/Calendario/Salud) NO
están afectadas (fuente/join distintos, ya verificados independientemente).
Re-implementación: ver "Batch B" abajo — tarea pendiente, NO ejecutada en este
pase (este pase es SOLO la verificación de lineage exigida por el owner).

## 8ter. GAPS DE LINEAGE ABIERTOS — Horas Productivas "Cliente + Part" / grano temporal de Participación (owner live QA, NO resueltos — próximo agente)

Dos preguntas de lineage/semántica que el owner planteó en el smoke live y
que este pase de trabajo dejó EXPLÍCITAMENTE sin resolver (evidencia
primaria del PBIP todavía no revisada para estos dos casos puntuales — ver
HANDOFF.md sección "-17." para el contexto completo de owner-QA):

1. **RESUELTO (ticket "CONTINUE REPORTING", evidencia PRIMARIA leída
   directamente de `Danwor - Tablero Integral.Report/definition/pages/
   7d1191c1e755ac3abe70/visuals/` + `.SemanticModel/definition/tables/
   Time Logs Reports.tmdl` + `Cliente.tmdl`) — Horas Productivas —
   segunda vista "Cliente + Part"**:
   - La página "Horas Productivas consumidas" (`7d1191c1e755ac3abe70`)
     tiene DOS pivotTable visuals lado a lado: `ccdefff9e63140f74665`
     (izquierda, Rows: `Time Logs Reports.Client` (nativeQueryRef
     "Cliente") › `Project`, Values: `Rank_T2`/`SUM(HORAS)`/`% Hs
     ejecutadas2`... — este es el YA implementado `renderCapacidadProductivas`)
     y `789c200b688d20a418d8` (derecha, Rows: `Time Logs Reports.'Cliente
     + Part'` › `Project`, Values: `Rank_T3`/`SUM(HORAS)`/`% Hs
     ejecutadas2`).
   - **`'Cliente + Part'` es una COLUMNA CALCULADA** (Time Logs Reports.tmdl
     línea 300): `'Time Logs Reports'[Client] &" "& 'Time Logs Reports'[%
     part (Fact)]`, donde `'% part (Fact)'` (línea 294) =
     `ROUND(RELATED(Cliente[Porcentaje de Part.]) * 100, 2) & " %"` — un
     RELATED() que trae, para cada fila, el valor ESTÁTICO de referencia
     `Cliente.'Porcentaje de Part.'` (columna FÍSICA de la lista Cliente,
     `sourceColumn: Porcentaje de Part.`, `dataType: double`,
     `Percentage.Type` en el M — un número de referencia asignado por
     Cliente en SharePoint, NO derivado de Horas Vendidas/Certificadas).
   - **Conclusión de negocio**: "Cliente + Part" NO es una dimensión de
     agrupamiento distinta — es la MISMA agrupación por Cliente que el
     panel izquierdo (mismo `RANKX`/`REMOVEFILTERS` sobre `SUM(HORAS)`,
     solo con el contexto de `ALLSELECTED` apuntado a la columna calculada
     en vez de `[Client]`, patrón típico de PBI para que RANKX calce con
     el contexto de fila del visual). La ÚNICA diferencia visible es el
     RÓTULO de cada fila: "{Cliente} {Porcentaje de Part.}%" en vez de
     "{Cliente}" — el panel derecho muestra el mismo ranking/horas/%
     incidencia que el izquierdo, pero con el % de referencia estático de
     cada cliente pegado al nombre para comparación visual rápida.
   - **Internal name físico NO confirmado todavía** (gap real, documentado
     — no adivinado a ciegas): el M de Cliente.tmdl solo muestra el nombre
     YA RENOMBRADO por Power Query (`"PorcentajedePart."` → `"Porcentaje de
     Part."`), no el internal name crudo que Graph devolvería. Se prueban
     candidatos siguiendo la MISMA convención ya confirmada para el resto
     de columnas opcionales de Cliente. **CORREGIDO (ticket "HORAS
     PRODUCTIVAS CORRECTIVE")**: el internal name YA NO se adivina —
     `ReportingRepository._resolve_porcentaje_part_field(site_id)`
     descubre el internal name real en vivo desde
     `/lists/{cliente}/columns` → `displayName`, MISMO mecanismo que
     `_resolve_client_partners`. `CLI_PORCENTAJE_PART_DISPLAY_NAMES`
     (`"Porcentaje de Part."`/`"PorcentajedePart."`) son los `displayName`
     candidatos a matchear, NUNCA un internal name codificado a mano — a
     confirmar en el smoke real del owner que el `displayName` real
     coincide con uno de estos dos.
   - **Año/Mes**: NINGÚN pivotTable de esta página tiene Año/Mes en
     Rows/Values — Año/Mes viven como DOS PARES de slicers INDEPENDIENTES
     posicionados directamente sobre cada panel (`acd4d653693bc72a6417`/
     `fad86226a51d715e75a2` para Año, en x=756 sobre el panel derecho y
     x=72.5 sobre el izquierdo respectivamente — posiciones que NO se
     solapan). **CORREGIDO (ticket "HORAS PRODUCTIVAS CORRECTIVE" —
     owner: "PRESERVE PBIP PANEL-INDEPENDENT TEMPORAL STATE")**: la
     simplificación previa a UN filtro Año/Mes compartido NO estaba
     autorizada — se retiró. El Hub ahora replica fielmente los DOS PARES
     de slicers independientes: cada panel (Cliente/Cliente + Part) tiene
     su PROPIO Año/Mes, montado en el toolbar de SU PROPIA sección (posición
     fiel a la del PBIP original, uno sobre cada visual) — reusando la
     MISMA implementación del TEMPORAL DOMAIN CONTRACT ("single-select Año
     + Mes Todos", `_operationalMonthCap`/`_applyMonthCap`/`_maskMes`/
     `_validMesOptions`, ya establecida para Rentabilidad/Horas Vendidas/
     Cartera) pero con DOS instancias de estado independientes — "shared
     implementation != shared state" (ver
     `capacidadProductivasSelectedRows(panel)`/`capacidadPanelAnioKey(panel)`
     en reporting.js, y HANDOFF.md sección "-17." para el detalle completo).

2. **RESUELTO (ticket "CONTINUE REPORTING", commit `972b7e5`) — Participación
   — grano temporal (Año/Estado)**: el owner tomó la decisión de producto
   pendiente explícitamente — Año como checkbox multi-select + Estado
   (default En curso), reusando la MISMA infraestructura temporal hot/cold
   de Rentabilidad/HV, es una adición del Hub por encima del PBIP fuente
   (que en efecto no tiene ese eje), no una re-derivación de una medida
   real. Como Estado vive en `Proyectos.Status` (por PROYECTO) pero
   Participación agrega por CLIENTE, `_shape_participacion` bucketiza el
   numerador por `(año, estado)` — `vendidasByYear`/`certificadasByYear`
   son mapas `{anio: {estado: horas}}` — así "sumar solo horas de
   proyectos En curso" (la interpretación aquí escogida) se puede aplicar
   selectivamente por cliente sin re-fetchear al tildar/destildar Estado.
   Ver HANDOFF.md sección "-17." (bloque top "✅ PARTICIPACIÓN") para el
   detalle completo (root cause de la población dispersa + Año/Estado +
   hot/cold + endpoints).

## 9. Plan de batches (evidence-based)

- **Batch A — "backbone reuse"** (Horas Vendidas, Participación, Calendario,
  Salud de Cliente): reusan el repositorio de Cartera; se agregan lecturas de
  columnas ya presentes en Cliente + shaping en el service. UN endpoint por
  experiencia (o uno consolidado), sin fuente física nueva. **Horas
  Productivas/Productivas se RETIRA de este grupo** (ver §8bis — corrección de
  lineage: es CSV de Time Logs, no backbone) y pasa a Batch B.
- **Batch B — "fuentes/semántica nuevas"** (Rentabilidad + Horas Productivas
  AMBAS perspectivas): (1) semántica Ejecutadas + matriz mensual de Conceptos;
  (2) adapter nuevo para el **CSV de Time Logs** en /sites/Operaciones (parseo
  CSV en memoria, mismo patrón fail-soft; site distinto — reusar
  `graph_get_bytes`) que alimenta Productivas (Cliente×Proyecto, excluye
  Cliente CONTAINS "Danwor") y No Asignadas (Consultor×mes, Project IN los 2
  placeholder) desde UN solo fetch/combine — ver §8bis para el contrato
  físico completo (sitio/carpeta/columnas/M).
- **Phase 0 (transversal, previo o en paralelo)**: (0A) loader Hub compartido
  reusando el de Asignaciones; (0B) geometría estable de matrices jerárquicas
  (`<colgroup>` + `table-layout:fixed` + clases semánticas de ancho); (0C)
  formateo/overflow de KPIs (Intl.NumberFormat con tipos semánticos
  count/hours/signed-hours/percentage; sin ruido de punto flotante).

**Riesgos semánticos abiertos:** (1) definición exacta de "Ejecutadas" vs
"Trabajadas" en `Resumen Horas x Proyecto Fac y Ejec` (DAX measure-sourced) — a
resolver leyendo esa medida antes de implementar Rentabilidad; (2) estructura
EXACTA de las filas del CSV de Time Logs (formato de fecha, valores reales de
`Task` dentro de los 2 proyectos placeholder) — contrato de COLUMNAS ya
confirmado por el M (§8bis), pero el FORMATO de celda (fecha DD/MM/YYYY vs
ISO, etc.) se confirma en el smoke real (mismo criterio que la XLSX); (3) `Hs
Asig` (capacidad de referencia estática por consultor, mock `hsAsig`) requiere
`Colaboradores['HS asignadas a proy'] = RELATED('Resumen Asignaciones'[Hs
Asignadas])` — una lista SharePoint adicional (`Resumen Asignaciones`) sin ID
confirmado en este repositorio; a resolver en Batch B o documentar como
gap explícito si no se puede confirmar sin adivinar el list ID.

## 7. Gaps residuales (confirmar en el smoke con token real, NO fabricar)

1. **Internal names de lookup** `ProyectoId ↔ Proyectos.Id` y `EmpresaId ↔ Cliente`:
   los valores de lookup en `fields` de Graph pueden venir como `ProyectoLookupId`
   / requerir `$expand=fields($select=...)`; a confirmar contra datos reales.
2. **`Proyecto_Key` vs lookup:** el join list↔XLSX usa el NOMBRE de proyecto
   normalizado (`Upper/no-space`); confirmar que `Horas por proyectos.Proyecto`
   (Title del lookup) y `Horas Facturadas.Proyecto Base` produzcan la misma clave.
3. **Estado por-proyecto:** sale de `Proyectos.Estado`; confirmar el valor real que
   corresponde al default "En curso".

## 7. Estado de implementación

- **HECHO (discovery/arquitectura):** lineage físico, contrato normalizado,
  transformaciones, gaps. Permisos/auth confirmados por reuso (Proyectos/Cliente ya
  se leen hoy).
- **PENDIENTE (implementación, próximo paso):** `reporting_repository.py`
  (adapter Graph→normalizado, reusando `microsoft365` + `_list_items_url`),
  `reporting_service.py` (ensamble + `diff`), endpoint
  `POST /operaciones/reporting/integral/data/cartera` (session-gated, fail-soft),
  y cablear SOLO el renderer Cartera de `reporting.js` a los datos del server
  (mock aislado tras el mismo contrato). Requiere un token delegado real para el
  smoke de lectura (igual que Staffing).
