---
module: "06"
title: "Troubleshooting y Performance"
nav_label: "06 · Troubleshooting y Performance"
tags: ["module:06", "domain:troubleshooting", "domain:performance"]
key_topics:
  - id: bw_bridge
    label: BW Bridge
    heading: "BW Bridge vs CDS Views"
  - id: adso_cdc
    label: ADSO CDC
    heading: "ADSO y CDC en BW Bridge"
  - id: performance
    label: Performance
    heading: "Performance y Runtime Metrics"
  - id: hana_triggers
    label: HANA Triggers
    heading: "HANA Triggers y CDC Buffer"
  - id: future_proof
    label: Future-Proof
    heading: "Future-Proof con ECC"
scope: |
  Cubre resolución de problemas y optimización de performance en SAP Datasphere:
  decisión BW Bridge vs CDS Views, arquitectura ADSO con CDC (Change Data Capture),
  herramientas de Runtime Metrics, HANA Triggers y su impacto en el buffer CDC
  (/1DH/*), gestión de ODQMON, y consideraciones future-proof para arquitecturas
  ECC en transición a S/4HANA. No cubre estrategia de extracción general (módulo 03)
  ni modelado por capas (módulo 04).
guardrails:
  - "No recomendar refactors o cambios de arquitectura sin evidencia operativa: pedir o usar métricas, ODQMON, Runtime Metrics y señales concretas antes de concluir."
  - "BW Bridge no debe presentarse como default universal: evaluarlo frente a CDS Views y modernización futura según contexto ECC/S/4HANA."
  - "CDC, HANA Triggers y ODQ tienen impacto operativo; no tratarlos como detalle invisible cuando afectan buffer, monitoreo o soporte."
out_of_scope_redirect:
  - keywords: ["extraccion", "extracción", "ODP", "ODP-SAPI", "ODP-CDS", "CDS extracción",
               "sistema fuente extraccion", "integración datos", "replicación", "federación",
               "whitelisted", "nota 2500202"]
    module: "03"
    label: "Integración y Estrategia de Datos"
    message: "Ese tema corresponde al módulo 03 · Integración y Estrategia de Datos, que cubre extracción, ODP, CDS y estrategias de integración."
  - keywords: ["capas modelado", "vista SQL", "tabla remota", "tabla local", "naming convention",
               "semantic type", "tipo semantico", "data flow", "replication flow modelado"]
    module: "04"
    label: "Modelado y Desarrollo"
    message: "Ese tema corresponde al módulo 04 · Modelado y Desarrollo, que cubre modelado por capas, tipos de vistas y naming."
  - keywords: ["espacio datasphere", "DEV QA PRD", "transporte datasphere", "arquitectura landscape"]
    module: "01"
    label: "Arquitectura y Conectividad"
    message: "Ese tema corresponde al módulo 01 · Arquitectura y Conectividad, que cubre espacios, transportes y conectividad."
---
# 06 Troubleshooting y Performance

## BW Bridge vs CDS Views
{{section_meta: keywords="BW Bridge vs CDS, BW bridge o CDS, cuándo usar BW Bridge, cuando usar BW Bridge, cuándo usar CDS, extractor BW Bridge, árbol BW Bridge CDS, decision BW Bridge, tabla BW Bridge, modelo BW Bridge, importar desde BW Bridge, ampliar BW Bridge, datasphere BW Bridge, ADSO datasphere, extractor estándar BW Bridge, CDS en lugar de BW Bridge, BW Bridge datasphere decision"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/97ec7b05e6d0461ab9dd3abf6d21c040.html" Pin=true}}

En proyectos con arquitectura BW Bridge, la decisión de dónde desarrollar (en el Bridge o directo en Datasphere via CDS) depende de si el origen ya existe en el Bridge y si hay necesidad de combinar con modelos existentes.

| Escenario | Recomendación |
|---|---|
| El origen (tabla/extractor) ya existe en BW Bridge | Ampliar el modelo existente en el BW Bridge |
| Existe extractor estándar y hay modelos BW Bridge relacionados | Capitalizar el estándar a través del BW Bridge |
| No hay modelos BW Bridge relacionados pero existe extractor estándar | Crear CDS View e importar directamente a Datasphere |
| No existe extractor estándar | Crear CDS View custom con template Danwor (compatibilidad delta incluida) |

> Al crear CDS Views en arquitecturas BW Bridge: incluir `@Analytics.dataExtraction.delta.changeDataCapture.automatic: true` si el extractor no tiene ya definido el mecanismo delta.

{{tree_button: BW Bridge o CDS Views}}

## ADSO y CDC en BW Bridge
{{section_meta: keywords="ADSO datasphere, ADSO CDC, change data capture BW Bridge, change log table, active table, CDC BW Bridge, transformation flow CDC, tabla activa, tabla change log, ADSO 2024.06, ADSO standard type, write change log, delta incremental BW Bridge, tabla local CDC, replicación incremental BW Bridge, ADSO clave primaria, ADSO master data check"}}

Desde la versión **2024.06** de Datasphere, la integración nativa entre BW Bridge y Datasphere genera **2 tablas** en lugar de 1 al importar entidades de un ADSO:

| Tabla | Propósito |
|---|---|
| **Active Table** | Snapshot actual de los datos — misma información que antes |
| **Change Log Table** | Registro de cambios para CDC — habilita extracción incremental real |

**Prerequisitos para usar CDC con Change Log:**
1. El ADSO es de tipo **Standard** con **Write Change Log** habilitado
2. El ADSO tiene al menos una **clave primaria** definida
3. Las propiedades de la clave tienen **Master Data Check** habilitado para reporting

**Patrón de implementación — Transformation Flow con CDC:**

```
ADSO (Change Log Table) → SQL View Transform → Transformation Flow → Tabla Local Datasphere
                                                         ↑
                                              Set Load Type: CDC/Incremental
```

Una vez que la tabla local CDC está activa y el Transformation Flow corre en la Task Chain, el ajuste en el modelo por capas es mínimo: **reemplazar la referencia** de la vista existente (que apuntaba a la tabla remota) por la nueva tabla local CDC.

{{callout: title="Verificar delta antes de usar Change Log" tone="punch" icon="💡"}}
Data Preview devuelve la misma información entre Active y Change Log. La diferencia es operacional: al usar Change Log en un Transformation Flow con tipo CDC, el procesamiento es incremental real. Si los prerequisitos del ADSO no se cumplen, el sistema cae en extracción FULL silenciosamente.
{{/callout}}

## Performance y Runtime Metrics
{{section_meta: keywords="performance datasphere, rendimiento vistas, runtime metrics, métricas ejecución, herramientas performance, optimización SQL, Expensive Statements, tracing datasphere, peak memoria datasphere, performance vistas SQL, analizar rendimiento, SQL analyzer datasphere, diagnostico performance, version 2025.9, herramientas rendimiento"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/9af04c990f294fd28c00f46763dd8b0a.html" Pin=true}}

Desde la versión **2025.9** es posible usar herramientas nativas de rendimiento directamente sobre las vistas SQL en Datasphere.

**Proceso de análisis de performance (3 pasos):**

| Paso | Acción |
|---|---|
| **1** | Identificar la vista o consulta con latencia elevada |
| **2** | Ejecutar el análisis de Runtime Metrics sobre la vista |
| **3** | Revisar el plan de ejecución y los indicadores de consumo de memoria |

> **Nota:** Los indicadores de **peak de memoria** requieren haber habilitado previamente **Expensive Statements Tracing** a nivel tenant en Datasphere. SAP **no recomienda** mantenerlo activo en forma permanente — activarlo puntualmente para diagnósticos y desactivarlo después.

{{callout_row}}
{{callout: title="Cuándo investigar performance" tone="warn" icon="🔸"}}
Si un reporte es lento, revisar en este orden: (1) ¿La vista de consumo tiene persistencia donde debería? (2) ¿Hay joins innecesarios en la capa de consumo? (3) ¿El volumen no está pre-agregado? Antes de activar Expensive Statements Tracing.
{{/callout}}
{{callout: title="Herramienta de análisis" tone="note" icon="🔹"}}
Runtime Metrics muestra el plan de ejecución HANA subyacente. Las vistas gráficas tienen planes menos optimizables que SQL Estándar — un argumento más para preferir SQL Estándar en objetos productivos.
{{/callout}}
{{/callout_row}}

## HANA Triggers y CDC Buffer
{{section_meta: keywords="HANA triggers, CDC buffer, /1DH/, tablas buffer ODP, ODQMON, delta queue, ODQ, cola delta, triggers HANA inválidos, triggers IS_VALID FALSE, DROP TRIGGER, CREATE TRIGGER, limpiar buffer CDC, desuscribir punteros ODQMON, buffer acumulado, PRCD_ELEMENTS, incidente ODP, nota SAP 3231210, nota SAP 3366715, troubleshooting ODP, problema delta ODP CDS"}}

El framework ODP/ODQ usa **HANA Triggers** para detectar cambios en las tablas origen. Cuando un extractor CDS tiene el CDC habilitado, HANA crea triggers que escriben en tablas buffer (`/1DH/SL*`) los registros modificados. La delta queue (ODQMON) luego consume esos buffers para armar el delta incremental.

**Caso real — Incidente en producción:**

> **Causa:** Extractores ODP-CDS inicializados como prueba pero no utilizados en extracción real. Los triggers escribían en las tablas buffer `/1DH/*`, pero los buffers **nunca eran vaciados** porque nadie los extraía. El buffer creció hasta impactar performance del sistema fuente.

> **Solución (Notas SAP 3231210 y 3366715):**

```sql
-- Paso 1: Verificar triggers inválidos
SELECT * FROM TRIGGERS WHERE IS_VALID = 'FALSE';

-- Paso 2: Eliminar triggers inválidos (si aplica)
DROP TRIGGER <Trigger_name>;

-- Paso 3: El sistema regenera automáticamente los triggers faltantes.
-- Paso Opcional: recrear manualmente si el problema persiste
CREATE OR REPLACE TRIGGER <Trigger_name> <Trigger_Definition_Scripts>;
```

**Proceso de limpieza en ODQMON:**
1. Desuscribir los punteros (subscribers) de las colas delta no utilizadas
2. Las tablas buffer se auto-eliminan una vez vacías
3. Validar y rectificar inconsistencias de triggers si las hubiera

{{callout: title="Regla preventiva" tone="warn" icon="🔸"}}
No inicializar extractores CDS en entornos productivos sin confirmar que van a ser extraídos. Un extractor con CDC activo sin consumidor acumula buffers indefinidamente. Auditar ODQMON periódicamente en proyectos con múltiples extractores CDS.
{{/callout}}

## Future-Proof con ECC
{{section_meta: keywords="future proof ECC, ECC a S4HANA, compatibilidad ECC S4, tablas ECC consolidadas, MATDOC S4, ACDOCA S4, vistas lógicas ECC, plug and play S4, extractores custom ECC, delta extracción ECC, extractores ODP ECC, CDS ECC NW7.5, compatibilidad extractores BW ECC, herencia técnica ECC, preparar migración S4, future proofing datasphere ECC"}}

Para arquitecturas con ECC en transición hacia S/4HANA, las decisiones de integración de hoy deben ser compatibles con el upgrade futuro. Principios clave:

**1 — No consumir tablas base directamente**

Tablas que se consolidan en S/4 (ej: `MATDOC`, `ACDOCA`) cambian su estructura al migrar. En lugar de consumirlas directamente, usar **vistas lógicas** que permitan hacer plug-and-play posterior: al migrar a S/4, solo se ajusta la vista, no todos los modelos aguas abajo.

**2 — Aprovechar CDS disponibles en ECC (NW7.5)**

Si existen CDS disponibles en el ECC actual (NW7.5), utilizarlas en lugar de ir por tablas o crear CDS propias. Son más estables ante el upgrade que las tablas base.

**3 — Minimizar extractores ABAP custom**

Reducir al máximo la creación de extractores ABAP custom. Usar mecanismos estándar (ODP-SAPI whitelisted) o CDS compatibles. Para extractores BW existentes — estándar o custom — asegurar su compatibilidad con Datasphere antes de usarlos.

**4 — Asegurar delta desde el inicio**

Las extracciones FULL no son soluciones de largo plazo ni escalables. Diseñar con delta desde el inicio (ODP delta, CDS CDC) reduce el impacto operativo al migrar a S/4.

{{callout: title="Principio de compatibilidad" tone="success" icon="🌿"}}
Estas consideraciones en materia de compatibilidad, integración y estrategia de extracción reducen la herencia técnica y facilitan las tareas del upgrade ECC → S/4HANA, permitiendo menor disrupción en todos los modelos provisionados en Datasphere.
{{/callout}}
