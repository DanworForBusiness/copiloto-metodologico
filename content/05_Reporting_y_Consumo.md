---
module: "05"
title: "Reporting y Consumo"
nav_label: "05 · Reporting y Consumo"
tags: ["module:05", "domain:reporting", "domain:consumption"]
key_topics:
  - id: estrategia
    label: Estrategia
    heading: "Estrategia de Reporting"
  - id: herramientas
    label: Herramientas
    heading: "Herramientas de Reporting"
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

## {dw-black}Estrategia de {dw-gold}Reporting
{{section_meta: keywords="estrategia reporting, herramienta reporting, SAC, SAP Analytics Cloud, Power BI, BusinessObjects, BO, qué herramienta usar, SAC vs Power BI, Power BI datasphere, SAC datasphere, BO datasphere, herramienta BI, herramienta analítica, consumo datasphere, cómo consumir datasphere, herramientas externas datasphere, reporte datasphere, visualización datasphere, árbol herramienta reporting"}}
{{support: SAP Help ODBC JDBC="https://help.sap.com/docs/SUPPORT_CONTENT/datasphere/4576253867.html?locale=en-US&version=LATEST" SAP Help OData="https://help.sap.com/docs/SUPPORT_CONTENT/datasphere/4224677129.html?locale=en-US&version=LATEST"}}

{{subtitle: no-gap}}
>Para clientes que desean explotar los datos por fuera del ecosistema SAP nativo, existen distintas alternativas según el contexto tecnológico, la inversión existente y los requisitos de gobernanza:

{{html_embed: ../assets/danwor-estrategia-reporting.html height=860}}

### {dw-black}Herramientas de {dw-gold}Reporting
| Herramienta | Tipo | Integración con Datasphere | Cuándo usarla |
|---|---|---|---|
| **SAP Analytics Cloud (SAC)** | Nativa SAP | Live Data Connection directa — sin configuración de conexión adicional | Cuando el cliente ya tiene licencia SAC o prioriza el ecosistema SAP end-to-end |
| **Power BI** | Microsoft | Vía schema users (DirectQuery o Import Mode) | Cliente con inversión fuerte en ecosistema Microsoft o Power BI ya implantado |
| **BusinessObjects (BO)** | SAP legacy | Vía Universe o conexión JDBC/ODBC | Clientes con BO existente que no migran aún a SAC |
| **Herramientas genéricas** | OData/ODBC | Vía schema users | Acceso técnico ad-hoc, integraciones custom, notebooks de análisis |

{{subtitle: no-gap}}
> SAC es la herramienta recomendada dentro del ecosistema SAP: tiene Live Data Connection nativa con Datasphere sin necesidad de schema users ni configuración adicional de conectividad.

{{callout_row}}
{{callout: title="SAC — Integración nativa" tone="success" icon="🌿"}}
Contexto:
  - Consume modelos analíticos en tiempo real
  - Respeta DAC en forma nativa
  - No requiere exportar datos
Capacidad Nativa:
  - Las asociaciones en Datasphere se propagan automáticamente al modelo analítico, sin código adicional.
Error Frecuente:
  - Desarrolladores formados en PowerBI trasladan el patrón de derivar campos, eliminando los beneficios del modelo
Veredicto:
  - Usar asociaciones nativas es la práctica correcta. 
  - Trazabilidad, rendimiento, mantenibilidad y escalabilidad.      
{{/callout}}
{{callout: title="Power BI — Ecosistema Microsoft" tone="punch" icon="📊"}}
Contexto:
  - Compatibilidad con Modelos Analíticos muy limitada
  - Soporta DAC via usuario de base de datos
  - DirectQuery para tiempo real, Import Mode para snapshots
Restricción Técnica:
  - Power BI no puede resolver medidas calculadas, restringidas o navegar asociaciones ni jerarquías.
Work-around actual:
  - Derivar atributos y textos en las vistas es la única alternativa disponible
Veredicto:
  - Práctica aceptable bajo DirectQuery. 
  - Complejidad en escalabilidad y rendimiento bajo import mode.
{{/callout}}
{{/callout_row}}

{{tree_button: Herramienta de Reporting}}

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
