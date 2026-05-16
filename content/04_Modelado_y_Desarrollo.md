---
module: "04"
title: "Modelado y Desarrollo"
nav_label: "04 · Modelado y Desarrollo"
tags: ["module:04", "domain:modeling", "domain:development"]
key_topics:
  - id: capas
    label: Capas & Roles
    heading: "Arquitectura por Capa: Guía Operativa"
  - id: semantico
    label: Uso Semántico
    heading: "Artefactos y uso Semántico"    
  - id: naming
    label: Nomenclatura
    heading: "Convención de Nombres"    
  - id: vistas_sql
    label: Vistas SQL
    heading: "Tipos de Vistas SQL"

scope: |
  Cubre el modelo de desarrollo en SAP Datasphere: arquitectura por capas
  (ingesta, armonización, propagación, consumo), tipos de vistas SQL (Estándar,
  Gráfica, Table Function), tipos de uso semántico (Relacional,
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

## {dw-black}Evolución de Arquitectura {dw-gold}por Capas
{{section_meta: keywords="evolución arquitectura por capas, evolución LSA, LSA++, layered scalable architecture, BW LSA, BW LSA++ a Datasphere, equivalencia LSA Datasphere, equivalencia LSA Databricks, medallion architecture, bronze silver gold, lakehouse por capas, arquitectura por capas conceptual, historia arquitectura por capas, separación de responsabilidades conceptual, continuidad conceptual BW Datasphere Databricks, paradigma por capas, arquitectura híbrida moderna, evolución data warehouse a lakehouse, marco conceptual Danwor"}}

{{subtitle: no-gap}}
>La arquitectura por capas no nace con Datasphere ni con Databricks: es la evolución de un principio conocido en BW/LSA++, donde se separan responsabilidades para controlar ingesta, armonización, propagación y consumo. Cambian los objetos técnicos; el criterio arquitectónico permanece.


| Principio | SAP BW (LSA) | Lakehouse (Databricks) | Datasphere/BDC |
|---|---|---|---|
| **Ingesta o persistencia desacoplada del origen** | Staging Layer <br>Corporate Memory | Bronze (raw zone) | Tablas Remotas / Persistencia opcional / Data Lake (HDLF en BDC) |
| **Armonización y Propagacion** | EDW Layer | Silver (refined zone) | Armonización / Propagación |
| **Exposición para consumo** | Reporting Layer | Gold (serving zone) | Consumo / Modelos Analíticos |
| **Gobierno Semántico** | Infoobjects / BW Semantics | Unity Catalog + Delta | Semantic Layer + Data Products |

{{callout: title="El principio no cambia, sino su forma de implementarlo" tone="punch" icon="⭐"}}
Hoy la escalabilidad proviene del diseño semántico y separación entre almacenamiento y procesamiento, la gobernanza de contratos y metadatos,  y la performance (pushdown, delta y storage optimizado). Las capas ya no son rígidas ni necesariamente físicas: pueden ser lógicas o persistentes según el caso.
{{/callout}}

## {dw-black}Aplicación de Arquitectura {dw-gold}por Capas
{{section_meta: keywords="aplicación arquitectura por capas, implementar capas Datasphere, diseño capas Datasphere, ingesta armonización propagación consumo, capa ingesta Datasphere, capa armonización Datasphere, capa propagación Datasphere, capa reporting Datasphere, tablas remotas por capa, vistas SQL por capa, data flows por capa, replication flows por capa, analytical datasets, federación por capa, persistencia por capa, filtros estáticos, filtros dinámicos, HANA DB, HDLF, cross sharing, dominios Datasphere, objetos por capa, criterios de persistencia, modelo por capas Danwor"}}

{{subtitle: no-gap}}
>Implementamos la arquitectura de modelado por capas con separación de responsabilidades, adaptada a un enfoque Lakehouse moderno en SAP Datasphere:
{{html_embed: ../assets/danwor-arquitectura-capas.html height=830}}

## {dw-black}Arquitectura por Capa: {dw-gold}Guía Operativa
{{section_meta: keywords="guía operativa arquitectura por capas, arquitectura por capa operativa, criterio por capa, cómo usar cada capa, capa ingesta propósito, capa armonización propósito, capa propagación propósito, capa reporting propósito, prefijos por capa, nivel de transformación por capa, transformación por capa, propósito de cada capa, cuándo usar ingesta, cuándo usar armonización, cuándo usar propagación, cuándo usar reporting, qué va en cada capa, responsabilidades por capa, separación responsabilidades operativa, preservar datos originales, auditoría datos crudos, normalizar datos, enriquecer datos, reutilización vistas, modelos analíticos finales, optimización reporting, consumo Power BI, consumo SAC, vistas planas Power BI, modelos analíticos SAC, evitar mezclar responsabilidades entre capas"}}

{{subtitle: no-gap}}
>Patrón de separación de responsabilidades aplicado a nuestro marco.

| Capa | Prefijo | Nivel de Transformación | Propósito | ¿Cómo usar? |
|---|---|---|---|---|
| **Ingesta** | `1` | Ninguno o muy bajo. Mapeo 1:1 y datos tal cual la fuente | Capturar los datos crudos directamente desde las fuentes externas (sea S/4HANA, u otras bases de datos y/o sistemas externos) | - Ingestar datos directamente desde sistemas fuente<br> - Preservar datos originales para auditoría |
| **Armonización** | `2` | Alta. Limpieza y estandarización | Transformar, limpiar, y unificar los datos para asegurar consistencia semántica y su re-utilización alineado con las reglas de negocio | - Unificar datos de sistemas heterogéneos<br> - Reutilizacion<br> - Normalizar formatos, eliminar duplicados, enriquecer datos |
| **Propagacion** | `3` |  Baja. Ajustes mínimos. Limitado a joins, unions, agregacion y filtros | Exponer datos consistentes y ligeramente transformados para consumo directo (Ej: PBI) o integraciones con otros sistemas (Ej: SPM/SN Distribuidores,etc.) | - Publicar datos listos para consumo por otras aplicaciones- Permitir reutilización en otras vistas<br> - Una capa 3V* extra (sufijo *_PBI) como fuente gobernada para Power BI (vistas planas con columnas duplicadas de ID+Descripción) |
| **Reporting** | `4` | Alta. Optimización para consumo | Preparar modelos analíticos finales optimizados para reporting y visualización | - Crear vistas con medidas y dimensiones<br> - Proveer datos listos para dashboards y herramientas BI<br> - Para SAC con Modelos Analíticos (multidimensional nativo) |

## {dw-black}Artefactos y uso {dw-gold}Semántico
{{section_meta: keywords="artefactos y uso semántico, artefactos datasphere, uso semántico datasphere, semantic usage, semantic type, tipo semántico, fact, dimension, relacional, analytical dataset, tablas locales, tablas remotas, vistas SQL, vistas gráficas, table functions, data flows, capa business, capa consumption, modelo analítico, medidas, dimensiones, atributos, jerarquías, asociaciones semánticas, exposición semántica, consumo analítico, autoservicio BI, SAP Analytics Cloud, Power BI, cuándo usar fact, cuándo usar dimension, cuándo usar relacional, cuándo usar analytical dataset, qué artefacto usar, criterio de modelado semántico"}}

{{subtitle: no-gap}}
>El uso semántico define cómo Datasphere interpreta y expone el objeto en la capa de modelado. Asignarlo correctamente habilita el autoservicio analítico y la navegación semántica en herramientas de consumo.

| Uso Semántico | Prefijo | Propósito | ¿Cómo usar? |
|---|---|---|---|
| Fact | `F` | Diseñado para contener medidas (datos cuantitativos) | - Tablas principales de métricas (ej.: ventas, costos)<br> - Centro de un esquema estrella enlazado a dimensiones<br> - Datos de KPI o valores agregables |
| Relacional | `R` | Uso Generico para datos sin un rol especifico (no distingue entre medidas y dimensiones) | - Datos sin rol definido o en etapa de transformación<br> - Tablas intermedias o de staging<br> - Datos transaccionales sin optimización analítica inmediata |
| Dimension | `D` | Representa datos categoricos o descriptivos para analizar hechos | - Datos descriptivos como clientes, productos, regiones<br> - Tablas referenciales reutilizables<br> - En esquemas estrella como tabla complementaria |
| Analítica | `A` | Combina medidas y dimensiones optimizado para herramientas analiticas | - Repositorios completos para análisis<br> - Cuando necesitas jerarquías y atributos<br> - Conexión directa con herramientas como SAP Analytics Cloud |
| Texto | `T` | Contiene descripciones textuales, o especificaciones multilenguaje | - RDatos de etiquetas o descripciones multilingües<br> -Tablas de textos vinculadas a claves |
| Jerarquía | `H` | Estructura jerarquica de datos para analisis basados en niveles (ej.: organizacion, categorias) | - Cuando necesitas modelar jerarquías (Ej.: niveles gerenciales, categorías de productos)<br> -Para estructuras complejas con relaciones padre-hijo |

{{callout: title="Predeterminado" tone="warn" icon="⚠️"}}
Tené en cuenta que el uso semántico "Relacional" es utilizado por defecto al crear tablas y vistas, al ser parte de la convención de nombres es importante que te asegures de reclasificarlo para no tenerminar teniendo inconsistencias entre el nombre y el uso semántico por detrás.
{{/callout}}

## {dw-black}Convención de {dw-gold}Nombres
{{section_meta: keywords="convención nombres, naming convention, convención de nombres datasphere, prefijo artefacto, nomenclatura datasphere, naming artefactos, nombre vista, nombre tabla, nombre objeto datasphere, SAP DSP EDB TXT API, notación camello, CamelCase datasphere, prefijo 1 2 3, cómo nombrar objetos, estándar nombres, convencion nombres danwor"}}

{{support: KBA Naming="https://community.sap.com/t5/technology-blog-posts-by-sap/sap-datasphere-naming-convention-in-action/ba-p/13567336"}}

>Si bien la interfaz de usuario en Datasphere ofrece capacidades de navegación y filtro que permiten identificar objetos fácilmente a través de sus atributos técnicos y metadata (tales como tipo de objeto, uso semántico, etc.) permitiendo así separar tablas de vistas, o flujos de datos de replicación, o inclusive los modelos analíticos creados, entendemos sigue siendo una buena idea el uso de prefijos en la convención de nombres por alguno de los siguientes motivos:

{{html_embed: ../assets/danwor-convencion-nombres.html height=750 default_mode=porque}}
>El patrón de nomenclatura Danwor garantiza trazabilidad, linaje y consistencia entre ambientes

### ¿Como usarla?
{{html_embed: ../assets/danwor-convencion-nombres.html height=550 default_mode=como}}

### Otros Artefactos
| Artefacto | Prefijo | Ejemplo |
|---|---|---|
| **Data Flow** | `DF` | DF_SND_PEDIDOS |
| **Replication Flow** | `RF` | RF_SND_PEDIDOS |
| **Transformation Flow** | `FT` | FT_SND_PEDIDOS |
| **Intelligent Look-up** | `IL` | IL_SND_PEDIDOS |
| **Task Chain** | `TC` | TC_SND |
| **Analytic Model** | `AM` | AM_SND_FACTURACION |
| **Entity Relationship Model** | `EM` | EM_SND_ENTREGAS |
| **Data Access Control** | `DAC` | DAC_SND |

### CDS Views
| Objeto | Convencion de nombre | Ejemplo | Comentarios |
|---|---|---|---|
| CDS View | `ZCDS_DSP_`<libre> | ZCDS_DSP_DIM_MATERIAL | Vista logica de extracción (hasta 30 caracteres) |
| Vista SQL para CDS | `ZCDS_`<libre> | ZCDS_DMATERIAL | Nombre físico asociado (hasta 16 caracteres)(*) |

{{subtitle: no-gap}}
> (*) Dado que la vista SQL ya se encuentra vinculada a la CDS (que sigue la convencion correspondiente), y la longitude maxima para las vistas siendo 16 caracteres, se eliminan de la convencion de la vista SQL (CDS_) dando lugar a 4 caracteres extras por ser utilizados para el nombre de la vista, cuyo proposito es pura y exclusivamente datasphere, dando sentido a la convencion/nomenclatura DWC_ para futuras referencias, analisis e identificacion.

## {dw-black}Tipos de {dw-gold}Vistas SQL
{{section_meta: keywords="tipos vistas SQL, vista estándar, vista gráfica, table function, SQLScript, data flow, replication flow, qué tipo de vista usar, cuándo usar vista gráfica, cuándo usar SQLScript, cuándo usar data flow, vista SQL datasphere, vistas datasphere, crear vista, tipo vista datasphere, SQL standard view, graphical view, table function datasphere, vista calculada, vista analitica tipo, árbol vistas SQL"}}
{{section_exclude: "codigo, código, template, ejemplo, ayudame con el codigo, como hago, como escribo, escribir, generar codigo, crearme, dame un, mostrame, sample, snippet"}}

{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/f2e359c899fa4351b5f514d1d86ed9e2.html?locale=en-US&version=LATEST" SAP Help SQL Reference="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/bc433adac03d4ac18bd0949ecbf961bc.html?locale=en-US&version=LATEST"}}

{{subtitle: no-gap}}
>En SAP Datasphere conviven distintos tipos de vistas y artefactos de transformación, cada uno con ventajas según el caso de uso. La distribución esperada en un proyecto sano: **~80% SQL Estándar, <15% Gráfica, ≤5% Table Function**.

| Tipo | Cuándo usarlo | Cuándo evitarlo |
|---|---|---|
| **Vista SQL Estándar** | Filtros, JOINs, agregaciones, cálculos. Opción recomendada por defecto | Rara vez; casi siempre es la respuesta adecuada |
| **Vista Gráfica** | Prototipos, exploración inicial, casos donde la claridad visual pesa más que la eficiencia | Modelos reutilizables — plan de ejecución HANA es menos optimizable, dificulta versionado |
| **Table Function (SQLScript)** | Lógica con pasos secuenciales, columnas/tablas dinámicas en ejecución, librerías especiales | Si puede resolverse con SQL Estándar — añadir wrapper de consumo para linaje |

{{callout: title="Cada vista debe justificarse" tone="punch" icon="⚠️"}}
Las vistas gráficas tal vez simplifiquen la lectura visual y aceleren tu proceso inicial, pero no sustituyen la precisión técnica del SQL Estándar ni ofrecen igual escalabilidad y flexibilidad. Además, dificultan su mantenimiento a largo plazo al limitar la revisión técnica directa del código SQL.
{{/callout}}

{{tree_button: Tipos de Vistas SQL}}

## Checklist de Desarrollo
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
