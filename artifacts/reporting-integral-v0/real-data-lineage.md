# Reporting Integral — Real-Data Lineage (Phase 1: CARTERA only)

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

## 6. Gaps de fuente sin resolver (NO fabricar)

1. **`facturadas` de la perspectiva Proyecto** difiere de `certificadas` (mock YPF:
   facturadas 290 vs certificadas 322). Su fuente NO es la lista "Horas por
   proyectos" sino el Excel `Horas Facturadas Proyectos-Período.xlsx` en la
   biblioteca de documentos (grano/tabla distintos). En Mantenimiento el mock tiene
   `facturadas == certificadas` (= HsFacturadas), así que ahí sí mapea a
   `hsCertificadas`. **Decisión pendiente del owner:** conectar el XLSX de
   facturadas para Proyecto, o dejar `facturadas` explícitamente marcado como
   pendiente en esa perspectiva. No se inventa.
2. **Claves de join exactas** `Horas por proyectos.ProyectoId ↔ Proyectos.Id` y
   `EmpresaId ↔ Cliente.<Id/lookup>` a confirmar contra datos reales en el smoke
   (los internal names del lookup pueden requerir `$expand`).
3. **Estado por-mes vs por-proyecto:** el status de Cartera sale de `Proyectos.Estado`
   (por proyecto), no de la fila mensual — confirmar que el default "En curso"
   corresponde al valor real de `Estado`.

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
