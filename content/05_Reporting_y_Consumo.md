---
module: "05"
title: "Reporting y Consumo"
nav_label: "05 · Reporting y Consumo"
tags: ["module:05", "domain:reporting", "domain:consumption"]
key_topics:
  - id: herramientas
    label: Herramientas
    heading: "Estrategia de Reporting"
  - id: conexion
    label: Conexión
    heading: "Conexión de Herramientas Externas"
  - id: performance
    label: Performance
    heading: "Performance en Reporting"
  - id: checklist
    label: Checklist
    heading: "✅ Checklist de Reporting"
scope: |
  Cubre la estrategia de consumo y reporting sobre SAP Datasphere: comparativa
  de herramientas (SAC, Power BI, BusinessObjects), modos de conexión para
  herramientas externas (DirectQuery vs Import, schema users, Live Data Connection),
  y consideraciones de performance. No cubre modelado dentro de Datasphere
  (módulo 04), seguridad DAC (módulo 02), ni configuración de espacios (módulo 01).
guardrails:
  - "No elegir la herramienta de reporting por preferencia aislada: decidir según modo de consumo, gobierno, performance, seguridad y operación."
  - "El reporting no debe corregir problemas de modelado o seguridad de base; si la consulta trata capas, semántica o DAC, derivar al módulo correspondiente."
  - "Con herramientas externas, no recomendar accesos sin gobierno: validar schema users, modo de conexión, permisos y comportamiento de performance."
quick_action_guidance:
  "Guía operativa del módulo":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 05:
      - La guía debe centrarse en estrategia de consumo y reporting, no en modelado, seguridad o configuración de espacios.
      - La decisión dominante es elegir herramienta y modo de consumo según gobierno, performance, seguridad, latencia y operación.
      - Usar SAC, Power BI, BusinessObjects y herramientas externas como alternativas condicionadas por contexto, no como ranking genérico.
      - Incluir DirectQuery, Import Mode, Live Data Connection, schema users, DAC y performance como criterios de decisión.
      - Si el problema real está en modelo, DAC o espacio, derivar al módulo correspondiente.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 05 - GUÍA OPERATIVA:
      La decisión arquitectónica dominante debe formularse como: definir la estrategia de consumo/reporting según herramienta, modo de conexión, gobierno, seguridad, performance y operación.

      Incluí obligatoriamente:
      - Cuándo elegir SAC, Power BI, BusinessObjects o herramientas externas.
      - Cuándo usar Live Data Connection, DirectQuery o Import Mode.
      - Validación de schema users cuando la herramienta externa lo requiere.
      - DAC y permisos como condición de consumo gobernado.
      - Performance: persistencia, pre-agregación, filtros, workload y concurrencia.
      - Derivar problemas de modelado al módulo 04, seguridad/DAC al módulo 02 y espacios/conectividad al módulo 01.
  "Checklist de implementación":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 05:
      - El checklist debe validar readiness de consumo antes de publicar reportes o abrir acceso externo.
      - Debe cubrir herramienta, modo de conexión, permisos, schema users, DAC, modelo expuesto, performance, refresh y operación.
      - No debe resolver modelado ni seguridad de base: solo marcar dependencias si bloquean el consumo.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 05 - CHECKLIST DE IMPLEMENTACIÓN:
      Construí un checklist operativo de reporting y consumo.

      Debe validar:
      - Herramienta de consumo definida: SAC, Power BI, BO u otra.
      - Modo de conexión: Live, DirectQuery, Import, JDBC/ODBC.
      - Modelo analítico o vista de consumo lista y validada.
      - Schema user cuando aplique.
      - DAC/permisos aplicados al perfil de consumo.
      - Persistencia o pre-agregación si el volumen lo requiere.
      - Performance esperada y concurrencia.
      - Refresh schedule o latencia aceptada.
      - Ownership del reporte y del modelo consumido.
      - Validación con usuarios consumidores.
      - Monitoreo y soporte post go-live.
  "Errores frecuentes":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 05:
      - Los errores deben enfocarse en reporting, consumo, conexión externa, gobierno y performance.
      - No convertirlos en errores de modelado o DAC salvo como dependencia no resuelta.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 05 - ERRORES FRECUENTES:
      Priorizá errores como:
      - Elegir herramienta por preferencia del equipo y no por modo de consumo/gobierno.
      - Usar Power BI DirectQuery sin validar performance y concurrencia.
      - Crear schema users sin DAC/permisos adecuados.
      - Usar reporting para corregir problemas del modelo.
      - Exponer datos crudos o vistas técnicas como consumo final.
      - Elegir Import Mode sin gobernar refresh, latencia y duplicación.
      - Ignorar BO/legacy constraints en clientes que aún dependen de BusinessObjects.
      - No definir ownership entre modelo, reporte y operación.

      Para cada error, explicá causa raíz, impacto y corrección Danwor.
  "Ejemplos prácticos":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 05:
      - Los ejemplos deben salir de estrategia de reporting, conexión de herramientas externas, performance y checklist de reporting.
      - Usar escenarios de SAC, Power BI, BO y herramientas genéricas cuando aplique.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 05 - EJEMPLOS PRÁCTICOS:
      Construí ejemplos concretos de consumo:
      - SAC con Live Data Connection sobre modelo analítico gobernado.
      - Power BI con schema user y decisión DirectQuery vs Import.
      - BusinessObjects/JDBC/ODBC en contexto legacy.
      - Herramienta externa o notebook con acceso SQL gobernado.

      Para cada ejemplo, indicá fuente interna usada, decisión Danwor, por qué esa y no otra, y riesgo si se elige mal.
out_of_scope_redirect:
  - keywords: ["tipo semantico", "tipos semanticos", "semantic type", "fact", "dimension",
               "capa business", "capa consumption", "modelado datasphere", "vista analitica",
               "entidad de negocio", "business entity", "analytical dataset", "capas modelado"]
    module: "04"
    label: "Modelado y Desarrollo"
    message: "Ese tema corresponde al módulo 04 · Modelado y Desarrollo, que cubre Semantic Types, capas y modelado analítico."
  - keywords: ["DAC", "data access control", "roles", "permisos", "seguridad roles",
               "seguridad datos", "schema user seguridad"]
    module: "02"
    label: "Seguridad y Gobernanza"
    message: "Ese tema corresponde al módulo 02 · Seguridad y Gobernanza, que cubre roles, DAC y principios de gobernanza."
  - keywords: ["espacio datasphere", "space datasphere", "transporte", "DEV QA PRD",
               "DP agent", "cloud connector", "configuración datasphere"]
    module: "01"
    label: "Arquitectura y Conectividad"
    message: "Ese tema corresponde al módulo 01 · Arquitectura y Conectividad, que cubre espacios, transportes y conectividad."
---
# 05 Reporting y Consumo

## Estrategia de Reporting
{{section_meta: keywords="estrategia reporting, herramienta reporting, SAC, SAP Analytics Cloud, Power BI, BusinessObjects, BO, qué herramienta usar, SAC vs Power BI, Power BI datasphere, SAC datasphere, BO datasphere, herramienta BI, herramienta analítica, consumo datasphere, cómo consumir datasphere, herramientas externas datasphere, reporte datasphere, visualización datasphere, árbol herramienta reporting"}}

Para clientes que desean explotar los datos por fuera del ecosistema SAP nativo, existen distintas alternativas según el contexto tecnológico, la inversión existente y los requisitos de gobernanza:

| Herramienta | Tipo | Integración con Datasphere | Cuándo usarla |
|---|---|---|---|
| **SAP Analytics Cloud (SAC)** | Nativa SAP | Live Data Connection directa — sin configuración de conexión adicional | Cuando el cliente ya tiene licencia SAC o prioriza el ecosistema SAP end-to-end |
| **Power BI** | Microsoft | Vía schema users (DirectQuery o Import Mode) | Cliente con inversión fuerte en ecosistema Microsoft o Power BI ya implantado |
| **BusinessObjects (BO)** | SAP legacy | Vía Universe o conexión JDBC/ODBC | Clientes con BO existente que no migran aún a SAC |
| **Herramientas genéricas** | SQL/ODBC | Vía schema users | Acceso técnico ad-hoc, integraciones custom, notebooks de análisis |

> SAC es la herramienta recomendada dentro del ecosistema SAP: tiene Live Data Connection nativa con Datasphere sin necesidad de schema users ni configuración adicional de conectividad.

{{callout_row}}
{{callout: title="SAC — Integración nativa" tone="success" icon="🌿"}}
La Live Data Connection de SAC con Datasphere es la más directa: consume modelos analíticos en tiempo real, respeta DAC automáticamente y no requiere exportar datos.
{{/callout}}
{{callout: title="Power BI — Ecosistema Microsoft" tone="punch" icon="📊"}}
Requiere schema users a nivel espacio. Elegir DirectQuery para tiempo real (con impacto en performance del origen) o Import Mode para snapshots (con latencia de actualización).
{{/callout}}
{{/callout_row}}

{{tree_button: Herramienta de Reporting}}

## Conexión de Herramientas Externas
{{section_meta: keywords="conexión Power BI datasphere, schema user, usuario esquema, DirectQuery datasphere, import mode datasphere, live data connection, conexión SAC datasphere, cómo conectar Power BI, cómo conectar herramienta externa, JDBC ODBC datasphere, conexión BO datasphere, configurar Power BI datasphere, conexión externa datasphere, tipo conexión reporting"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/4db9e1e21c814cda933c5e1b5a6e6e30.html" Pin=true}}

Para conectar herramientas externas como Power BI a Datasphere se requiere un **schema user** (usuario de base de datos a nivel espacio). Este usuario está sujeto a DAC — los filtros de acceso configurados en Datasphere se aplican automáticamente al consumo externo.

| Modo | Descripción | Ventajas | Consideraciones |
|---|---|---|---|
| **DirectQuery** | Power BI consulta Datasphere en tiempo real en cada interacción | Datos siempre actualizados — sin latencia de refresh | Impacto de performance en Datasphere con consultas concurrentes |
| **Import Mode** | Power BI importa los datos y los almacena en su propio motor | Performance óptima en Power BI | Latencia de actualización — requiere schedule de refresh |
| **Live Data Connection (SAC)** | SAC consulta el modelo analítico directamente en Datasphere | Nativo, sin schema users, respeta DAC | Solo disponible con SAC — no aplica a Power BI |

{{callout: title="Schema user — patrón recomendado" tone="note" icon="🔹"}}
Crear un schema user por espacio y ambiente (DEV/QA/PRD). No compartir el mismo usuario entre herramientas distintas. El schema user debe tener asignados los DAC correspondientes al perfil de consumo externo.
{{/callout}}

## Performance en Reporting
{{section_meta: keywords="performance reporting, performance datasphere, rendimiento reporting, lentitud Power BI, lentitud SAC, optimizar reporte, performance consultas, índices datasphere, persistencia performance, DirectQuery lento, capacidad datasphere, CU datasphere, capacity units, optimización reporting, latencia reporte"}}

La performance del reporting depende de decisiones tomadas desde el modelo, no solo desde la herramienta de consumo.

| Palanca | Recomendación |
|---|---|
| **Persistencia selectiva** | Persistir vistas en capas de consumo (capa 3) cuando el volumen es alto o la transformación es costosa |
| **Aggregation views** | Pre-agregar en Datasphere antes de exponer al reporte — reducir granularidad innecesaria |
| **Filtros en el modelo** | Aplicar filtros en capas internas — no delegar toda la selectividad a la herramienta de reporting |
| **DirectQuery vs Import** | Evaluar Import Mode para dashboards con alto volumen y baja necesidad de tiempo real |
| **Workload class** | Configurar clases de workload para priorizar consultas de reporting sobre procesos de carga |
| **Capacity Units (CU)** | Dimensionar CUs según el volumen de consultas concurrentes esperadas — no subestimar en go-live |

{{callout: title="Regla de oro de performance" tone="punch" icon="💡"}}
Si el reporte es lento, el problema suele estar en el modelo — no en la herramienta. Revisar primero si hay vistas sin persistir con alto volumen, joins innecesarios en la capa de consumo, o datos que no están pre-agregados.
{{/callout}}

## ✅ Checklist de Reporting
{{section_meta: keywords="checklist reporting, lista verificación reporting, checklist consumo, revisión reporting, buenas practicas reporting, puntos control reporting, verificar herramienta, verificar conexión, verificar performance, checklist Power BI datasphere, checklist SAC datasphere"}}

| Item | Pregunta de control |
|---|---|
| **Herramienta** | ¿Está definida la herramienta de reporting del cliente (SAC / Power BI / BO)? |
| **Schema user** | ¿Está creado el schema user con los DAC correspondientes? |
| **Modo de conexión** | ¿Está definido el modo de conexión (DirectQuery / Import / Live)? |
| **Persistencia** | ¿Las vistas de consumo (capa 3) tienen persistencia donde se justifica? |
| **Pre-agregación** | ¿Los datos están pre-agregados al nivel de granularidad que consume el reporte? |
| **Workload class** | ¿Está configurada la clase de workload para consultas de reporting? |
| **Refresh schedule** | ¿Está definida la frecuencia de actualización (para Import Mode o Data Flows)? |
| **DAC en consumo** | ¿Los filtros de acceso (DAC) aplican correctamente al perfil del schema user? |
