---
module: "01"
title: "Arquitectura y Conectividad"
nav_label: "01 · Arquitectura y Conectividad"
tags: ["module:01", "domain:architecture", "domain:connectivity"]
key_topics:
  - id: arquitectura
    label: Arquitectura
    heading: "Referencias de Arquitectura"
  - id: espacios
    label: Espacios
    heading: "Espacios y Transportes"
  - id: configuracion
    label: Configuración
    heading: "Configuración de Espacios"

scope: |
  Cubre la arquitectura de referencia de SAP Datasphere y Business Data Cloud (BDC):
  landscape de sistemas fuente (SAP y no-SAP), mecanismos de conexión (DP Agent,
  Cloud Connector, SDA, BW Bridge), diseño de espacios (topologías 1-Tier / 2-Tier /
  Multi-Tier), estrategia de transportes, y configuración base de espacios (quota,
  source language, conexiones, Time Data). No cubre extracción de datos ni integración
  con sistemas origen (módulo 03), modelado dentro de Datasphere (módulo 04), ni
  seguridad de roles o DAC (módulo 02).
guardrails:
  - "No resolver decisiones de extracción, ODP, CDS o CDC desde este módulo: si aparecen, derivar al módulo 03."
  - "La topología de espacios debe responder a dominios, ciclo de vida, transporte y operación; no recomendar espacios por conveniencia visual o por replicar organigramas sin criterio arquitectónico."
  - "No usar reporting, roles o duplicación de espacios como sustituto de una arquitectura base gobernada."
out_of_scope_redirect:
  - keywords: ["DAC", "data access control", "roles", "permisos", "seguridad roles",
               "seguridad datos", "valores individuales", "operadores y valores"]
    module: "02"
    label: "Seguridad y Gobernanza"
    message: "Ese tema corresponde al módulo 02 · Seguridad y Gobernanza, que cubre roles, DAC y principios de gobernanza."
  - keywords: ["extraccion", "extracción", "ODP", "CDS extracción", "sistema fuente extraccion",
               "integración datos", "replicación", "federación", "persistencia", "ODP-SAPI", "ODP-CDS"]
    module: "03"
    label: "Integración y Estrategia de Datos"
    message: "Ese tema corresponde al módulo 03 · Integración y Estrategia de Datos, que cubre extracción, ODP, CDS y estrategias de integración."
  - keywords: ["tipo semantico", "tipos semanticos", "semantic type", "fact", "dimension",
               "capa business", "capa consumption", "modelado datasphere", "vista analitica",
               "entidad de negocio", "business entity", "analytical dataset"]
    module: "04"
    label: "Modelado en SAP Datasphere"
    message: "Ese tema corresponde al módulo 04 · Modelado en SAP Datasphere, que cubre Semantic Types, capas Business/Consumption y modelado analítico."
---
# 01 Arquitectura y Conectividad

## {dw-black}Referencias de {dw-gold}Arquitectura
{{section_meta: keywords="arquitectura datasphere, arquitectura de referencia, landscape SAP, sistemas fuente, sistemas origen, diagrama arquitectura, componentes datasphere, BW bridge, tablas remotas, tablas locales, data flows, replication flows, conexión SAP, fuentes SAP, fuentes no-SAP, Oracle SQL Server Azure, CDS views extractores, arquitectura BDC, business data cloud arquitectura, cómo conecta datasphere, qué sistemas conecta datasphere"}}

> Patrones de integración entre sistemas de origen y SAP Datasphere, clasificados por tipo de fuente y mecanismo de carga. Hacé click en un escenario para destacarlo en el diagrama.
{{html_embed: ../assets/danwor-referencias-arquitectura.html height=710}}

## {dw-black}Coexistencia de {dw-gold}Paradigmas
{{section_meta: keywords="HANA DB vs HDLF, HANA Database vs HDLF, HANA space, HDLF space, HANA Data Lake Files, diferencia HANA y HDLF, diferencia espacio HANA HDLF, coexistencia HANA HDLF, HANA DB object store, object store datasphere, parquet delta tables, ciclo de vida HANA DB, ciclo de vida HDLF, DEV QA PRD HANA, storage HDLF, arquitectura BDC ready, HANA Database y HANA Data Lake Files, comparar HANA DB HDLF, cuándo usar HANA DB, cuándo usar HDLF"}}

{{support: HANA Space="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/bbd41b82ad4d4d9ba91341545f0b37e7.html?locale=es-ES&version=LATEST" HDLF Space= "https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/947444683e524cfd9169d7671b72ba0c.html?locale=es-ES&version=LATEST"}}
{{subtitle: no-gap}}
> HANA DB y HDLF (HANA Data Lake Files) comparten la plataforma, pero tienen reglas distintas de ciclo de vida, modelado y gobernanza. El marco Danwor está diseñado para operar en ambos y evolucionar entre ellos

| HANA Database >> 📌 lo que construimos hoy | HDLF - Object Store >> BDC-Ready 📌 |
|---|---|
| Objetos Transportables (vistas, flujos, modelos, tablas)  | Datos como archivos físicos (Parquet, Delta tables) |
| Ciclo de vida gestionado por Datasphere | Ciclo de vida en el flujo, no en el objeto |
| DEV-> QA-> PRD via transportes | Sin DEV-> QA-> PRD en storage |
| Cross-Sharing nativo hacia espacios de negocio | Spark como motor de transformación |
| Capas: Ingesta, Armonizacion, Propagacion y Reporting | Capas: Bronze, Silver, Gold via Transformation Flows |

## {dw-black}Espacios & {dw-gold}Transportes
{{section_meta: keywords="espacios datasphere, spaces datasphere, transporte datasphere, DEV QA PRD, desarrollo calidad produccion, topología espacios, 1-tier, 2-tier, multi-tier, core-only, cross-sharing, gobierno datos espacio, espacio por línea de negocio, espacio de integración, espacio raíz, espacio compartido, design spaces, transport packages, promote objects, cómo organizar espacios, cuántos espacios, qué topología usar, landscape datasphere spaces, espacios y ambientes"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/f697e93e4d06441b99b9e8ce51682c9c.html"}}
> En Arquitectura Datasphere / BDC. Seleccioná el modelo de tenancy para explorar la topología de espacios y el flujo de transporte DEV → QA → PRD.
{{html_embed: ../assets/danwor-espacios-transportes.html height=665}}

## {dw-black}Configuración de {dw-gold}Espacios
{{section_meta: keywords="configuración espacios datasphere, space configuration, quota espacio, space quota, source language, idioma espacio, idioma datasphere, multi-language datasphere, conexiones datasphere, nombre conexiones, naming conexiones, time data, dimensión tiempo datasphere, usuarios esquema, schema users, power bi datasphere, usuarios base de datos, configuración inicial datasphere, setup datasphere, buenas practicas espacios, workload class, tenant quota, disk quota, memory quota, distribución quota, ratio quota, sizing tenant, capacidad tenant, quota por ambiente, cuota por tenant, cuota memoria, cuota disk, distribución de recursos"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/bbd41b82ad4d4d9ba91341545f0b37e7.html?locale=es-ES&version=LATEST"}}

Una configuración correcta del espacio desde el inicio evita problemas de rendimiento, transporte y consumo externo.

| Parámetro | Recomendación Danwor | Razón |
|---|---|---|
| **Space Quota** | Dimensionar por caso — no usar valor genérico | La quota impacta directamente en el sizing del tenant; ajustar post-PoC |
| **Source Language** | Definir explícitamente (ej: Spanish) | Datasphere es multi-idioma; los objetos semánticos importados se interpretan según el idioma del espacio destino |
| **Nombre de Conexiones** | Usar nombres funcionales y estables: `S4`, `ECC`, `BW` (evitar codificar el ambiente, ej: `S4DEV`) | El ambiente ya queda implícito por el espacio y por los parámetros técnicos de la conexión (application server/endpoint). El criterio busca estandarización y evitar redundancia, no resolver una limitación de transporte |
| **Time Data Dimension** | Configurar en el espacio antes de crear modelos | Habilita el soporte nativo de dimensiones de tiempo en vistas analíticas |
| **Schema Users** | Crear usuarios de base de datos a nivel espacio para consumo externo (ej: Power BI) | Los schema users están sujetos a DAC; requeridos para conexiones DirectQuery desde herramientas externas |

### Ratio orientativo de distribución de quota
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/f414c3d62bfe49b38e2cfdd7b4e7d786.html?locale=es-ES&version=LATEST"}}

La quota de espacios debe dimensionarse contra la capacidad total del tenant, no como valor fijo por espacio. Como regla de trabajo, partir de una distribución porcentual ayuda a reservar capacidad para ambientes, operación y crecimiento.

Definir:

- `D` = capacidad total de Disk / In-Memory Storage del tenant
- `M` = capacidad total de Memory del tenant
- `N` = cantidad de espacios funcionales o de dominio

| Tipo de espacio | Disk sugerido | Memory sugerida | Criterio |
|---|---:|---:|---|
| **DEV / Desarrollo** | `D x 10%` | `M x 10%` | Desarrollo, pruebas unitarias y primeros modelos |
| **QA / Calidad** | `D x 20%` | `M x 20%` | Validación, pruebas integradas y regresión |
| **PRD / Producción** | `D x 25%` | `M x 25%` | Operación productiva y consumo estable |
| **Espacios de dominio / negocio** | `(D x 35%) / N` | `(M x 25%) / N` | Distribución por dominio, área o producto de datos |
| **Reserva tenant** | No asignar inicialmente | No asignar inicialmente | Margen para crecimiento, performance tuning y contingencia |

{{subtitle: no-gap}}
> Los porcentajes son un punto de partida metodológico. Deben ajustarse según volumen real, criticidad, persistencia, concurrencia, estrategia de federación y evolución post-PoC.

## 🌟 Checklist de Configuración
{{section_meta: keywords="checklist configuración, checklist datasphere, lista verificación, revisión proyecto, setup inicial, buenas practicas, oportunidad mejora, verificar espacios, verificar conexiones, verificar transporte, puntos de control arquitectura, configuración base datasphere, lista chequeo"}}

Orientativo para revisar que la configuración base del proyecto esté completa antes de avanzar al modelado:

| Item | Pregunta de control |
|---|---|
| ☐ Ambientes | ¿Definiste espacios DEV / QA / PRD? |
| ☐ Source Language | ¿Configuraste el idioma del espacio (Source Language)? |
| ☐ Space Quota | ¿Alocaste cuota de almacenamiento (Space Quota)? |
| ☐ Schema Users | ¿Definiste usuarios a nivel espacio/schema para consumo externo? |
| ☐ Time Data | ¿Configuraste la dimensión de Time Data? |
| ☐ Conexiones | ¿Usaste nombres funcionales y estables, evitando codificar DEV/QA/PRD en el nombre técnico? |
| ☐ Cross-Sharing | ¿Alineaste la estrategia de Cross-Sharing y gobierno de datos? |
| ☐ Transporte | ¿Definiste la estrategia de transporte entre ambientes? |

{{callout: title="Momento recomendado" tone="success" icon="🌿"}}
Este checklist es más efectivo al cierre del setup inicial (antes del primer modelo analítico) y en las revisiones de arquitectura de proyecto. Un ítem sin marcar a tiempo puede implicar trabajo de refactor en etapas avanzadas.
{{/callout}}
