# Danwor — Tablero Integral: Assessment V0

Fuente: `E:\Joe\Metodologia\Refactorizacion\Tablero Integral\Danwor - Tablero Integral.pbip`
(read-only, no copiado a git). Generado con `scripts/pbip_report_assessment.py`
contra `.Report` (PBIR) + `.SemanticModel` (TMDL).

## Inventario

- **14 páginas** en el reporte. 2 no son analíticas: `Inicio` (home/navegación,
  0 campos, solo botones/imagen) y `Page 1` (página suelta sin uso, 1 tabla, 0
  slicers). **12 páginas funcionales** — coincide con el conteo aproximado del
  owner.
- **57 tablas** en el modelo (incluye ~30 `LocalDateTable_*`/`DateTableTemplate_*`
  auto-generadas por Power BI, ocultas — ruido normal, no responsabilidades
  propias).
- Fuente confirmada por lineage de Power Query M: `Proyectos` y `Colaboradores`
  vienen de **`SharePoint.Tables("https://danwor.sharepoint.com/sites/Gestion")`**
  — el MISMO sitio "Gestión" que ya lee `webapp/services/staffing_repository.py`.
  `Cliente`/`Hitos`/`PMs` vienen de `OData.Feed` (probablemente Dataverse/otro
  feed). `KPIs`/`NombreMeses` son `Json.Document` (tablas de configuración
  estáticas). Otras tablas (`Time Logs Reports`, `Horas por proyectos`, etc.)
  son derivadas de otras queries del propio modelo (sin función de origen
  externo directa detectada — pragmático, no se rastreó transitivamente).
- **Nota de medida:** `measure_overlap` da 0.0 en casi todos los pares — la
  mayoría de los visuales usan agregaciones ad-hoc sobre columnas
  (`Aggregation` sobre `Column`), no referencias directas a medidas DAX
  nombradas del modelo (rol `Measure`). El modelo SÍ tiene medidas reales
  (ver `source_inventory.json`, campo `measures` por tabla) — simplemente los
  visuales no las referencian por ese rol explícito. Señal honesta, no un bug.

## Matriz de páginas

| CURRENT PAGE | LIKELY RESPONSIBILITY | SIMILAR TO | WHY | CONSOLIDATION CANDIDATE | CONFIDENCE / NOTES |
|---|---|---|---|---|---|
| Estimación Horas Vendidas | Estimación comercial de horas vendidas | Estimación Horas Vendidas x CP | field_overlap **1.0**, visual_types 1.0, slicers 1.0 — mismos 14 campos exactos | **SÍ — misma experiencia**, perspectiva "Ver por: Proyecto \| Client Partner" | **ALTA** — literalmente el mismo dataset/campos, solo cambia agrupación |
| Estimación Horas Vendidas x CP | (idem, vista por Client Partner) | Estimación Horas Vendidas | idem | idem | **ALTA** |
| Calendario x Frecuencia | Calendario de entregas/hitos por frecuencia | Calendario x CP | field_overlap **0.94** — casi idénticos | **SÍ — misma experiencia**, perspectiva "Ver por: Frecuencia \| Client Partner" | **ALTA** |
| Calendario x CP | (idem, vista por Client Partner) | Calendario x Frecuencia | idem | idem | **ALTA** |
| Analisis Mantenimiento | Cartera de contratos de mantenimiento | Analisis Proyectos | field_overlap 0.55, mismos visual_types, slicers 0.5 | **SÍ — candidata**, perspectiva "Mantenimiento \| Proyecto" | **MEDIA-ALTA** — estructura y campos base compartidos (Proyectos/Horas por proyectos), difieren en filtro fijo de tipo de proyecto |
| Analisis Proyectos | Cartera de proyectos (no-mantenimiento) | Analisis Mantenimiento | idem | idem | **MEDIA-ALTA** |
| Analisis Participacion - Condiciones Comerciales | Vista comercial por cliente (participación/condiciones) | Client Partner - Status de Cliente detalle | field_overlap 0.13 (bajo) pero comparten `Cliente.Client Partner`/`Cliente.Cliente`, visual_types 1.0 | Candidata a **sub-vistas de una misma experiencia Comercial/Clientes**, NO fusión total | **MEDIA** — dimensión Cliente compartida, pero preguntas de negocio distintas (condiciones comerciales vs. status operativo) |
| Client Partner - Status de Cliente detalle | Detalle operativo de status por cliente/Client Partner | Analisis Participacion - Condiciones Comerciales | idem | idem | **MEDIA** |
| Analisis Rentabilidad | Rentabilidad/margen por proyecto | Hs Certificadas vs Hs Ejecutadas | field_overlap 0.11 (bajo), comparten `Cliente.Client Partner`/`Proyectos.Tipo Proyecto Dash` | Candidata a **sub-vistas de Performance**, NO fusión de datos | **BAJA-MEDIA** — pregunta de negocio distinta (margen $ vs. horas certificadas/ejecutadas); agrupar por tema, no por dataset |
| Hs Certificadas vs Hs Ejecutadas | Variación horas certificadas vs. ejecutadas | Analisis Rentabilidad | idem | idem | **BAJA-MEDIA** |
| Hs No Asignadas | Gap de horas sin asignar (capacidad) | Horas Productivas consumidas | field_overlap 0.13 (bajo), comparten `Meses.Año`/`Time Logs Reports.HORAS` | Candidata a **sub-vistas de Capacidad/Ejecución**, NO fusión de datos | **BAJA-MEDIA** — conceptualmente solapa con Staffing (capacidad); NO tocar Staffing en V0 |
| Horas Productivas consumidas | Consumo de horas productivas | Hs No Asignadas | idem | idem | **BAJA-MEDIA** |
| Inicio | Home / navegación | — | 0 campos, solo botones+imagen | No es una responsabilidad analítica — se reemplaza por navegación nativa del Hub | N/A |
| Page 1 | Página suelta sin uso aparente | — | 1 tabla, 0 slicers, no referenciada desde bookmarks | Descartar en V1 | N/A — verificar con el owner antes de eliminar |

## Verificación de hipótesis (A–E del batch)

- **(A) Mantenimiento + Proyectos → Cartera/Proyectos:** CONFIRMADA, confianza media-alta (0.51 combinado, mismo patrón de página).
- **(B) Horas Vendidas + x CP → una experiencia con selector Proyecto\|CP:** CONFIRMADA con máxima evidencia (field_overlap 1.0).
- **(C) Calendario x Frecuencia + x CP → una experiencia con selector Frecuencia\|CP:** CONFIRMADA (field_overlap 0.94).
- **(D) Rentabilidad + Certificadas/Ejecutadas → experiencia Performance compartida:** PARCIAL — comparten tema y 2 campos de contexto, pero el dataset base difiere sustancialmente (0.21 combinado). Se agrupan como **sub-vistas** de una misma experiencia (selector "Resumen \| Certificadas vs Ejecutadas"), NO como un dataset único.
- **(E) Hs No Asignadas + Horas Productivas → Capacidad/Ejecución:** PARCIAL — mismo patrón que (D): tema compartido, dataset distinto (0.27 combinado). Se representa como experiencia coherente con 2 sub-vistas. **NO se fusiona técnicamente con Staffing en V0** (conceptualmente solapa con capacidad de consultores, pero es un dataset de Proyectos/Time Logs, no de Asignaciones).

## Experiencias V0 propuestas (12 páginas → 6 experiencias)

1. **Cartera / Proyectos** — Mantenimiento \| Proyecto
2. **Rentabilidad / Performance** — Resumen \| Certificadas vs Ejecutadas
3. **Horas Vendidas** — Proyecto \| Client Partner
4. **Comercial / Clientes** — Participación/Condiciones \| Status de Cliente
5. **Calendario** — Frecuencia \| Client Partner
6. **Capacidad / Ejecución** — No Asignadas \| Productivas consumidas

`Inicio` se reemplaza por navegación nativa del Hub. `Page 1` queda para
confirmar con el owner si tiene contenido vigente antes de descartarla.

## Artifacts generados

`report_manifest.json` · `page_inventory.json` · `page_similarity.json` ·
`source_inventory.json` — ver este mismo directorio.
