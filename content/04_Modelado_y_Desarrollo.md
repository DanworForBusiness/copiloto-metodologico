---
module: "04"
title: "Modelado y Desarrollo"
nav_label: "04 · Modelado y Desarrollo"
tags: ["module:04", "domain:modeling", "domain:development"]
key_topics:
  - id: capas
    label: Capas
    heading: "Arquitectura por Capas"
  - id: vistas_sql
    label: Vistas SQL
    heading: "Tipos de Vistas SQL"
  - id: semantico
    label: Semántico
    heading: "Tipos y Uso Semántico"
  - id: naming
    label: Naming
    heading: "Convención de Nombres"
  - id: checklist
    label: Checklist
    heading: "✅ Checklist de Desarrollo"
scope: |
  Cubre el modelo de desarrollo en SAP Datasphere: arquitectura por capas
  (ingesta, armonización, propagación, consumo), tipos de vistas SQL (Estándar,
  Gráfica, Table Function, Data Flow), tipos de uso semántico (Relacional,
  Dimensional, Fact), convención de nombres de artefactos, y buenas prácticas
  de desarrollo. No cubre configuración de espacios ni transportes (módulo 01),
  seguridad de roles o DAC (módulo 02), ni extracción desde sistemas origen
  (módulo 03).
guardrails:
  - "Respetar arquitectura por capas: no recomendar consumo directo desde objetos crudos cuando corresponde armonizar, propagar o exponer una capa semántica."
  - "Los Semantic Types y usos semánticos se deciden en el modelo de Datasphere; no trasladar esa decisión a extracción o conectividad."
  - "No usar Data Flows, SQL Views o Table Functions como atajo genérico: elegir el artefacto según semántica, mantenibilidad, performance y responsabilidad de capa."
out_of_scope_redirect:
  - keywords: ["espacio datasphere", "space datasphere", "transporte", "DEV QA PRD",
               "DP agent", "cloud connector", "BW bridge", "topología espacios"]
    module: "01"
    label: "Arquitectura y Conectividad"
    message: "Ese tema corresponde al módulo 01 · Arquitectura y Conectividad, que cubre topología de espacios, transportes y conectividad."
  - keywords: ["DAC", "data access control", "roles", "permisos", "seguridad roles",
               "seguridad datos", "valores individuales", "operadores y valores"]
    module: "02"
    label: "Seguridad y Gobernanza"
    message: "Ese tema corresponde al módulo 02 · Seguridad y Gobernanza, que cubre roles, DAC y principios de gobernanza."
  - keywords: ["extraccion", "extracción", "ODP", "ODP-SAPI", "ODP-CDS", "CDS extracción",
               "sistema fuente", "integración datos", "replicación", "federación", "persistencia"]
    module: "03"
    label: "Integración y Estrategia de Datos"
    message: "Ese tema corresponde al módulo 03 · Integración y Estrategia de Datos, que cubre extracción, ODP, CDS y estrategias de integración."
intent_assets:
  - keywords: ["template CDS", "template vista", "naming template", "convención nombres ejemplo",
               "ejemplo nombre artefacto", "template dimensión", "ZCDS_DWC_DIM_TEMPLATE"]
    description: "Template CDS para extracción hacia SAP Datasphere"
    asset: "templates/cds_extractor_template.abap"
    language: "abap"
---
# 04 Modelado y Desarrollo

## Arquitectura por Capas
{{section_meta: keywords="arquitectura por capas, capas modelado, ingesta armonización propagación consumo, separación responsabilidades, capa ingesta, capa armonización, capa propagación, capa reporting, capa consumo, lakehouse SAP, principio capas datasphere, arquitectura modelado datasphere, modelo por capas, cómo modelar datasphere, diseño capas, capa 1 2 3, tablas remotas capa, vistas capa, persistencia capas, federación capas"}}

El principio de **separación de responsabilidades** (ingesta → armonización → propagación → consumo) es la base del modelo de desarrollo Danwor en SAP Datasphere. Implementa un enfoque Lakehouse moderno con capas bien definidas:

| Capa | Prefijo | Responsabilidad | Persistencia |
|---|---|---|---|
| **Ingesta** | `1` | Tablas remotas o locales con los datos tal como vienen del origen. Sin transformaciones de negocio. | Federada (bajo volumen / real-time) o persistida (alto volumen) |
| **Armonización** | `2` | Vistas reutilizables que armonizan, normalizan y enriquecen. Joins, filtros, cálculos base. | Opcional — sin persistir salvo necesidad de performance |
| **Propagación** | `3` | Vistas orientadas al consumo final: agregaciones, KPIs, modelos analíticos por área de negocio. | Opcional — persisitir cuando la transformación es costosa |
| **Reporting** | `3`+ | Vistas finales optimizadas para herramientas de consumo (Power BI, SAC, etc.). | Opcional — según SLA de actualización |

{{callout_row}}
{{callout: title="Regla de oro" tone="punch" icon="💡"}}
Cada capa consume solo de la capa inmediatamente anterior. Una vista de propagación no apunta directamente a una tabla de ingesta. Esto garantiza trazabilidad, reuso y mantenibilidad.
{{/callout}}
{{callout: title="Persistencia selectiva" tone="note" icon="🗄️"}}
No persistir por defecto. Persistir solo cuando: (1) el origen no puede ser golpeado constantemente, (2) la transformación es computacionalmente costosa, o (3) el SLA de actualización lo requiere.
{{/callout}}
{{/callout_row}}

> La vista `ZCDS_DSP_DIM_MATERIAL` (y el template `ZCDS_DWC_DIM_TEMPLATE`) construyen la dimensión maestra integral: atributos y textos unificados en una sola CDS. En CDS, la separación entre vistas de atributos y textos pierde sentido — la anotación `dataCategory: #TEXT` no es compatible con extracción directa hacia Datasphere.

## Tipos de Vistas SQL
{{section_meta: keywords="tipos vistas SQL, vista estándar, vista gráfica, table function, SQLScript, data flow, replication flow, qué tipo de vista usar, cuándo usar vista gráfica, cuándo usar SQLScript, cuándo usar data flow, vista SQL datasphere, vistas datasphere, crear vista, tipo vista datasphere, SQL standard view, graphical view, table function datasphere, vista calculada, vista analitica tipo, árbol vistas SQL"}}
{{section_exclude: "codigo, código, template, ejemplo, ayudame con el codigo, como hago, como escribo, escribir, generar codigo, crearme, dame un, mostrame, sample, snippet"}}

En SAP Datasphere conviven distintos tipos de vistas y artefactos de transformación, cada uno con ventajas según el caso de uso. La distribución esperada en un proyecto sano: **~80% SQL Estándar, <15% Gráfica, ≤5% Table Function**.

| Tipo | Cuándo usarlo | Cuándo evitarlo |
|---|---|---|
| **Vista SQL Estándar** | Filtros, JOINs, agregaciones, cálculos. Opción recomendada por defecto | Rara vez; casi siempre es la respuesta correcta |
| **Vista Gráfica** | Prototipos, exploración inicial, casos donde la claridad visual pesa más que la eficiencia | Modelos productivos reutilizables — plan de ejecución HANA es menos optimizable, dificulta versionado |
| **Table Function (SQLScript)** | Lógica con pasos secuenciales, columnas/tablas dinámicas en ejecución, librerías especiales | Si puede resolverse con SQL Estándar — añadir wrapper de consumo para linaje |
| **Data Flow** | Snapshot, persistencia, transformaciones pesadas, particionar por rendimiento | Acceso federado real-time — no es una vista, es un proceso de carga |

{{callout: title="Cada vista debe justificarse" tone="warn" icon="🔸"}}
Las vistas gráficas simplifican la lectura visual pero no sustituyen la precisión técnica del SQL Estándar. Dificultan el mantenimiento, limitan la revisión del SQL subyacente, y escalan peor en modelos reutilizables o críticos. Elegir vista gráfica solo para prototipos o casos excepcionales.
{{/callout}}

{{tree_button: Tipos de Vistas SQL}}

## Tipos y Uso Semántico
{{section_meta: keywords="tipo semántico, tipos semanticos, uso semántico, semantic type, fact, dimension, texto dimension, relacional, analytical dataset, jerarquía datasphere, capa business, capa consumption, business layer, consumption layer, semantic usage, modelo analitico, vista analitica, entidad de negocio, business entity, qué tipo semántico usar, cuándo usar fact, cuándo usar dimension"}}

El **tipo semántico** (Semantic Usage) define cómo Datasphere interpreta y expone el objeto en la capa de modelado. Asignarlo correctamente habilita el autoservicio analítico y la navegación semántica en herramientas de consumo.

| Tipo Semántico | Uso | Notas |
|---|---|---|
| **Relacional** | Vistas de preparación o armonización que no son consumidas directamente en reportes | Es el tipo por defecto; reclasificar una vez que el objeto tiene semántica de negocio definida |
| **Dimension** | Maestros y atributos descriptivos (Clientes, Materiales, Centros de Costo) | Debe tener una clave única; asociable con Facts en el modelo analítico |
| **Text** | Textos de una entidad dimensional (descripción de Material en varios idiomas) | No apto para extracción directa desde origen; usar como capa intermedia de textos en CDS |
| **Hierarchy** | Jerarquías organizacionales o de producto | Requiere la dimensión base asociada |
| **Fact** | Hechos transaccionales (Ventas, Pedidos, Movimientos de Stock) | Con métricas y asociaciones a dimensiones |
| **Analytical Dataset** | Set analítico que combina Facts y Dimensions en un objeto único de consumo | Usado en la capa de propagación/consumo; base para reportes en SAC/Power BI |

> La **capa Business** (Business Layer) agrega la semántica de negocio sobre los objetos técnicos. La **capa Consumption** (Consumption Layer) expone los objetos analíticos finales. Las reglas de normalización, claves y cardinalidades se derivan del modelo lógico.

## Convención de Nombres
{{section_meta: keywords="convención nombres, naming convention, convención de nombres datasphere, prefijo artefacto, nomenclatura datasphere, naming artefactos, nombre vista, nombre tabla, nombre objeto datasphere, SAP DSP EDB TXT API, notación camello, CamelCase datasphere, prefijo 1 2 3, cómo nombrar objetos, estándar nombres, convencion nombres danwor"}}

El patrón de nomenclatura Danwor garantiza trazabilidad, linaje y consistencia entre ambientes:

```
<Capa><Tipo>_<Origen>_<LibreConLineamientos>
```

**Capa** (prefijo numérico):
- `1` — Ingesta (tablas remotas o locales desde origen)
- `2` — Armonización (vistas reutilizables)
- `3` — Propagación / Consumo (vistas finales, modelos analíticos)

**Tipo** (letra):
- `T` — Tabla (local o remota)
- `V` — Vista (SQL Estándar o Gráfica)
- `F` — Fact / Analytical Dataset

**Origen** (sufijo de 3 letras):
| Código | Origen |
|---|---|
| `SAP` | Ingesta desde SAP (ECC, S/4HANA, BW) |
| `DSP` | Generado dentro de Datasphere (DF, RF, etc.) |
| `EDB` | Base de datos externa (Oracle, SQL Server) |
| `TXT` | Archivo plano (CSV, TXT) |
| `API` | Ingesta vía API |

**Ejemplos:**

| Objeto | Interpretación |
|---|---|
| `1TT_SAP_T151T` | Capa 1 · Tabla · Origen SAP · T151T (textos grupos cliente) |
| `2VD_CLIENTES_CABECERA` | Capa 2 · Vista · Dimensión Clientes Cabecera (armonización) |
| `3VF_TOPLINE_SPM` | Capa 3 · Vista Fact · Topline para SPM (consumo) |
| `3VD_CUSTOMER_PBI` | Capa 3 · Vista · Dimensión Customer para Power BI |

> Usar **CamelCase** para la parte libre (`<LibreConLineamientos>`). Usar nombres genéricos de origen (`S4`, `ECC`) en las conexiones para que los objetos transporten sin modificación.

## ✅ Checklist de Desarrollo
{{section_meta: keywords="checklist desarrollo, checklist modelado, lista verificación modelado, oportunidad mejora modelado, revisión modelo, buenas practicas desarrollo datasphere, puntos control desarrollo, checklist capas, checklist naming, checklist semántico, checklist vistas, verificar modelo"}}

Orientativo para revisar la calidad del modelo antes de promover al siguiente ambiente:

| Item | Pregunta de control |
|---|---|
| ☐ Separación de capas | ¿Cada vista consume solo de la capa inmediatamente anterior? |
| ☐ Tipo semántico | ¿Todos los objetos finales tienen el tipo semántico correcto (Fact / Dimension / Analytical Dataset)? |
| ☐ Naming | ¿Los nombres siguen el patrón `<Capa><Tipo>_<Origen>_<Libre>`? |
| ☐ Persistencia | ¿La persistencia está justificada (alto volumen, SLA, transformación costosa)? |
| ☐ Tipo de vista | ¿El tipo de vista está justificado (SQL Estándar por defecto, Gráfica solo si aplica)? |
| ☐ Claves | ¿Las vistas dimensionales tienen clave única definida? |
| ☐ Textos | ¿Las dimensiones maestras integran atributos y textos en un solo objeto (sin separar)? |
| ☐ Linaje | ¿Las Table Functions tienen wrapper de consumo para preservar linaje? |

{{callout: title="Principio de diseño Danwor" tone="success" icon="🌿"}}
Las reglas de normalización, claves y cardinalidades se derivan del modelo lógico — no al revés. El checklist es más efectivo en el cierre de cada sprint de desarrollo y antes de transportar a QA.
{{/callout}}
