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
