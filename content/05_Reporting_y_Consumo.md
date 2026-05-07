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
| ☐ Herramienta | ¿Está definida la herramienta de reporting del cliente (SAC / Power BI / BO)? |
| ☐ Schema user | ¿Está creado el schema user con los DAC correspondientes? |
| ☐ Modo de conexión | ¿Está definido el modo de conexión (DirectQuery / Import / Live)? |
| ☐ Persistencia | ¿Las vistas de consumo (capa 3) tienen persistencia donde se justifica? |
| ☐ Pre-agregación | ¿Los datos están pre-agregados al nivel de granularidad que consume el reporte? |
| ☐ Workload class | ¿Está configurada la clase de workload para consultas de reporting? |
| ☐ Refresh schedule | ¿Está definida la frecuencia de actualización (para Import Mode o Data Flows)? |
| ☐ DAC en consumo | ¿Los filtros de acceso (DAC) aplican correctamente al perfil del schema user? |
