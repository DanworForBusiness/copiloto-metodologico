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
    heading: "Espacios & Transportes"
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
  Sin embargo, como módulo arquitectónico, sí debe relevar las condiciones de borde
  que habilitan o restringen decisiones posteriores: tipo de origen (ECC, S/4HANA,
  BW o no-SAP), versión/release, modalidad RISE/on-prem, conectividad disponible
  y restricciones que condicionen el uso posterior de APIs funcionales SAP,
  Cloud Connector, DP Agent, ODP, CDS o patrones de integración. La decisión técnica
  detallada se resuelve en el módulo 03, pero el diagnóstico arquitectónico nace aquí.
guardrails:
  - "No resolver decisiones de extracción, ODP, CDS o CDC desde este módulo: si aparecen, derivar al módulo 03."
  - "Si una pregunta de arquitectura toca extracción, ODP, CDS, APIs funcionales SAP o integración, usar este módulo solo para validar precondiciones de landscape, versión, conectividad y restricciones; no elegir el mecanismo final sin pasar por el módulo 03."
  - "La topología de espacios debe responder a dominios, ciclo de vida, transporte y operación; no recomendar espacios por conveniencia visual o por replicar organigramas sin criterio arquitectónico."
  - "No usar reporting, roles o duplicación de espacios como sustituto de una arquitectura base gobernada."
quick_action_guidance:
  "Guía operativa del módulo":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 01:
      - La guía operativa no debe limitarse a configuración de espacios. Debe partir de arquitectura y conectividad.
      - Priorizar: arquitecturas de referencia ya implementadas, patrón de landscape, topología de espacios, estrategia de transportes y configuración base.
      - Incluir como decisión de borde: validar versión/release del ERP origen y compatibilidad/restricciones de API funcionales SAP antes de asumir mecanismos de integración o extracción. Si corresponde, derivar la profundización al módulo 03.
      - Capitalizar la sección de Configuración de Espacios: Space Quota, Source Language, nombres funcionales/estables de conexiones, Time Data y Schema Users.
      - Cuando menciones Space Quota, referenciar el criterio de ratio orientativo de distribución de quota contra capacidad total del tenant.
      - Cerrar invitando a validar el Checklist de Configuración antes de avanzar al modelado.
      - Evitar presentar "nombres de conexiones" como problema de transporte moderno; el criterio es estandarización y evitar redundancia, no una limitación actual de transporte.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 01 - ARQUITECTURA Y CONECTIVIDAD:
      No centres la guía en Space Quota, Source Language o nombres de conexiones. Esos son puntos de configuración base, no la decisión arquitectónica dominante.

      La decisión dominante debe formularse como: capitalizar arquitecturas de referencia y definir el landscape/topología/conectividad según origen, versión, modalidad RISE/on-prem, restricciones técnicas y ciclo de vida.

      Incluí obligatoriamente:
      - Validar si el origen es ECC, S/4HANA, BW o no-SAP y qué versión/release tiene.
      - Validar si esa versión restringe o habilita capacidades posteriores de integración/extracción que se profundizan en el módulo 03.
      - Validar si el escenario requiere Cloud Connector, DP Agent, BW Bridge, SDA u otro mecanismo de conectividad.
      - Usar topología de espacios y estrategia de transporte como decisión operativa central.
      - Usar Space Quota solo como punto de sizing, mencionando el ratio orientativo contra capacidad total del tenant.
      - Cerrar con una recomendación de validar el checklist del módulo antes de avanzar al modelado.

      Evitá que la respuesta quede reducida a "configurar Space Quota, Source Language y conexiones".
  "Checklist de implementación":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 01:
      - El checklist debe apoyarse en la sección "Checklist de Configuración", pero sin limitarse a tareas administrativas.
      - El objetivo es confirmar readiness arquitectónico y de conectividad antes de avanzar a modelado.
      - Incluir validaciones de landscape: origen ECC, S/4HANA, BW o no-SAP; versión/release; modalidad RISE/on-prem; restricciones de conectividad.
      - Incluir configuración base de espacios: DEV/QA/PRD, Space Quota, Source Language, Time Data Dimension, Schema Users, conexiones funcionales/estables, estrategia de transporte y documentación.
      - Si aparece decisión técnica de extracción, ODP, CDS, CDC o API corporativa, marcar que la decisión final corresponde al módulo 03.
      - No agregar una sección de "ítems frecuentemente salteados" ni cierre de coaching.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 01 - CHECKLIST DE IMPLEMENTACIÓN:
      Construí un checklist de readiness para arquitectura y conectividad.

      Debe cubrir:
      - Definición de espacios y topología DEV/QA/PRD.
      - Estrategia de transporte y ciclo de vida.
      - Tipo de origen y versión/release del landscape.
      - Restricciones RISE/on-prem y conectividad disponible.
      - Space Quota, usando el ratio orientativo de distribución contra capacidad total del tenant cuando aplique.
      - Source Language.
      - Time Data Dimension.
      - Schema Users para consumo externo cuando aplique.
      - Nombres de conexiones funcionales/estables como criterio de estandarización, no como limitación moderna de transporte.
      - Conexiones activas y documentación de configuración.

      Si el checklist detecta que la decisión depende de ODP/CDS/CDC/federación/persistencia, dejá explícito que se valida en el módulo 03.
  "Errores frecuentes":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 01:
      - En Arquitectura y Conectividad, los errores frecuentes más relevantes son de readiness técnico y elección de conectividad, no de checklist administrativo.
      - Considerar como señales críticas: tipo de origen (ECC/S/4HANA/BW/no-SAP), tipo de conexión elegido, versión/release del origen, modalidad RISE/on-prem y restricciones de conectividad.
      - Service user, Note Analyzer / CNV_NA_DI y estado de notas SAP son precondiciones prácticas para evitar fallas de integración ABAP -> Datasphere.
      - Si el error deriva en decidir ODP/CDS/CDC/federación/persistencia, explicá que la decisión técnica final corresponde al módulo 03.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 01 - ERRORES FRECUENTES:
      No conviertas esta respuesta en una versión negativa de la guía operativa o del checklist de configuración.
      No centres la respuesta en Space Quota, Source Language o nombres de conexiones salvo que aparezcan como consecuencia secundaria.

      Priorizá errores frecuentes de arquitectura/conectividad y readiness técnico:
      - Elegir o asumir incorrectamente el tipo de conexión o conector según origen (ABAP, ECC, S/4HANA, BW Bridge, Cloud Connector, DP Agent, SDA).
      - No validar incompatibilidades por versión/release del origen frente a capacidades disponibles en Datasphere.
      - No configurar correctamente usuario de servicio para integración o asumir que un usuario funcional alcanza para conectividad productiva.
      - No ejecutar o no resolver Note Analyzer / CNV_NA_DI y notas SAP requeridas para escenarios ABAP -> Datasphere.
      - No distinguir entre conectividad disponible y mecanismo de extracción/integración autorizado; si hace falta, derivar la decisión final al módulo 03.

      Para cada error, explicá la causa raíz técnica, el impacto real en proyecto y la corrección Danwor.
  "Ejemplos prácticos":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 01:
      - Los ejemplos prácticos deben salir primero de la sección Referencias de Arquitectura y de sus escenarios de arquitectura/conectividad.
      - No priorizar apéndices internos si desplazan el foco del módulo. Los apéndices pueden complementar, pero no reemplazar el mapa de casos de uso del módulo 01.
      - Los casos base del módulo son:
        1. Sistemas origen ECC o S/4HANA que integran datos hacia SAP Datasphere.
        2. Modernización BW mediante BW Bridge o reutilización/migración gradual.
        3. Integración no-SAP, por ejemplo Oracle, SQL Server o Azure, mediante tablas remotas u otros patrones compatibles.
      - El Hub de Integración AWS puede mencionarse solo como extensión o caso complementario si aporta valor, pero no debe dominar la respuesta mientras no esté incorporado explícitamente al diagrama de referencia del módulo.
      - Mantener el límite de responsabilidad: este módulo explica el escenario arquitectónico y la conectividad; la elección técnica detallada de extracción/integración pertenece al módulo 03.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 01 - EJEMPLOS PRÁCTICOS:
      Construí ejemplos reales a partir de los escenarios cubiertos por el módulo, no escenarios hipotéticos inventados ni apéndices que desplacen el foco.

      Usá como mínimo estos tres ejemplos:
      - ECC o S/4HANA como sistema origen: qué patrón de arquitectura/conectividad se valida y qué decisión queda pendiente para el módulo 03.
      - Modernización BW: cuándo posicionar BW Bridge como camino de continuidad o transición.
      - Origen no-SAP (Oracle, SQL Server o Azure): cómo se integra dentro del landscape de Datasphere y qué validar antes de modelar.

      Para cada ejemplo, entregá:
      - Escenario.
      - Decisión Danwor.
      - Por qué esa y no otra.
      - Riesgo si se elige mal.

      Si mencionás un apéndice interno, hacelo al final como complemento y aclarando que no reemplaza los escenarios de referencia del módulo.
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
{{html_embed: ../assets/danwor-referencias-arquitectura.html height=660}}

## {dw-black}Coexistencia de {dw-gold}Paradigmas
{{subtitle: no-gap}}
{{support: SAP Help HDLF="https://help.sap.com/docs/hana-cloud-data-lake/welcome-guide/data-lake-files?locale=en-US&version=LATEST"}}

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
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/6a396f6cf7ad41faa3a8cdd24f4e728a.html?locale=en-US&version=LATEST"}}
> En Arquitectura Datasphere / BDC. Seleccioná el modelo de tenancy para explorar la topología de espacios y el flujo de transporte DEV → QA → PRD.
{{html_embed: ../assets/danwor-espacios-transportes.html height=640}}

## {dw-black}Configuración de {dw-gold}Espacios
{{section_meta: keywords="configuración espacios datasphere, space configuration, quota espacio, space quota, source language, idioma espacio, idioma datasphere, multi-language datasphere, conexiones datasphere, nombre conexiones, naming conexiones, time data, dimensión tiempo datasphere, usuarios esquema, schema users, power bi datasphere, usuarios base de datos, configuración inicial datasphere, setup datasphere, buenas practicas espacios, workload class, tenant quota, disk quota, memory quota, distribución quota, ratio quota, sizing tenant, capacidad tenant, quota por ambiente, cuota por tenant, cuota memoria, cuota disk, distribución de recursos"}}

> Una configuración correcta del espacio desde el inicio evita problemas de rendimiento, transporte y consumo externo.

| Parámetro | Recomendación Danwor | Razón |
|---|---|---|
| **Space Quota** | Dimensionar por caso — no usar valor genérico | La quota impacta directamente en el sizing del tenant; ajustar post-PoC |
| **Source Language** | Definir explícitamente (ej: Spanish) | Datasphere es multi-idioma; los objetos semánticos importados se interpretan según el idioma del espacio destino |
| **Nombre de Conexiones** | Usar nombres funcionales y estables: `S4`, `ECC`, `BW` (evitar codificar el ambiente, ej: `S4DEV`) | El ambiente ya queda implícito por el espacio y por los parámetros técnicos de la conexión (application server/endpoint). El criterio busca estandarización y evitar redundancia, no resolver una limitación de transporte |
| **Time Data Dimension** | Configurar en el espacio antes de crear modelos | Habilita el soporte nativo de dimensiones de tiempo en vistas analíticas |
| **Schema Users** | Crear usuarios de base de datos a nivel espacio para consumo externo (ej: Power BI) | Los schema users están sujetos a DAC; requeridos para conexiones DirectQuery desde herramientas externas |

### Ratio orientativo de distribución de quota

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

## {dw-black}Checklist de {dw-gold}Configuración
{{section_meta: keywords="checklist configuración, checklist datasphere, lista verificación, revisión proyecto, setup inicial, buenas practicas, oportunidad mejora, verificar espacios, verificar conexiones, verificar transporte, puntos de control arquitectura, configuración base datasphere, lista chequeo"}}

Orientativo para revisar que la configuración base del proyecto esté completa antes de avanzar al modelado:

| Item | Pregunta de control |
|---|---|
| **Ambientes** | ¿Definiste espacios DEV / QA / PRD? |
| **Source Language** | ¿Configuraste el idioma del espacio (Source Language)? |
| **Space Quota** | ¿Alocaste cuota de almacenamiento (Space Quota)? |
| **Schema Users** | ¿Definiste usuarios a nivel espacio/schema para consumo externo? |
| **Time Data** | ¿Configuraste la dimensión de Time Data? |
| **Conexiones** | ¿Usaste nombres funcionales y estables, evitando codificar DEV/QA/PRD en el nombre técnico? |
| **Cross-Sharing** | ¿Alineaste la estrategia de Cross-Sharing y gobierno de datos? |
| **Transporte** | ¿Definiste la estrategia de transporte entre ambientes? |

{{callout: title="Momento recomendado" tone="success" icon="🌿"}}
Este checklist es más efectivo al cierre del setup inicial (antes del primer modelo analítico) y en las revisiones de arquitectura de proyecto. Un ítem sin marcar a tiempo puede implicar trabajo de refactor en etapas avanzadas.
{{/callout}}
