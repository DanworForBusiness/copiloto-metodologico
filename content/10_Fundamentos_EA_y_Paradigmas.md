---
module: "10"
title: "Fundamentos EA y Paradigmas"
nav_label: "10 · Fundamentos EA y Paradigmas"
tags: ["module:10", "domain:ea", "domain:principles"]
key_topics:
  - id: ea
    label: EA
    heading: "Marco EA y Diseño"
  - id: modelos
    label: Modelos
    heading: "Diseño de Modelos de Datos"
  - id: paradigmas
    label: Paradigmas
    heading: "Paradigmas Arquitectónicos"
  - id: data_products
    label: Data Products
    heading: "Datos como Producto"
scope: |
  Cubre los fundamentos conceptuales y el marco mental detrás del trabajo
  operativo en Datasphere: Enterprise Architecture (EA) aplicada a modelado
  y decisiones de diseño, ciclo Conceptual → Lógico → Físico, clasificación
  de metadatos, paradigmas arquitectónicos (Warehouse, Lakehouse, Data Mesh,
  Data Fabric) y concepto de Data Products. Es el apéndice estratégico que
  conecta la práctica diaria de Datasphere con la arquitectura moderna y la
  evolución hacia BDC. No cubre configuración operativa — ver módulos 01 a 08.
guardrails:
  - "Usar EA y paradigmas como lentes de decisión, no como etiquetas decorativas: toda recomendación debe conectar concepto, responsabilidad y consecuencia práctica."
  - "Un data product no es solo un dataset publicado: requiere ownership, contrato, semántica, gobierno y criterio de consumo."
  - "No convertir fundamentos estratégicos en configuración operativa; cuando la consulta pide pasos concretos, derivar al módulo operativo correspondiente."
out_of_scope_redirect:
  - keywords: ["extraccion", "extracción", "ODP", "CDS extracción", "sistema fuente",
               "integración datos", "replicación", "federación técnica", "DP agent"]
    module: "03"
    label: "Integración y Estrategia de Datos"
    message: "Ese tema corresponde al módulo 03 · Integración y Estrategia de Datos, que cubre extracción, ODP, CDS y estrategias de integración."
  - keywords: ["tipo semantico", "tipos semanticos", "semantic type", "fact dimension",
               "capas modelado", "vista analitica", "naming convention", "tabla remota"]
    module: "04"
    label: "Modelado y Desarrollo"
    message: "Ese tema corresponde al módulo 04 · Modelado y Desarrollo, que cubre modelado por capas, tipos de vistas y naming."
  - keywords: ["modernización BW", "migrar BW", "greenfield BW", "BW datasphere",
               "data product BDC", "activar data product"]
    module: "08"
    label: "Modernización BW Greenfield BDC"
    message: "Ese tema corresponde al módulo 08 · Modernización BW Greenfield BDC, que cubre la estrategia de migración y data products."
---
# 10 Fundamentos EA y Paradigmas

## Marco EA y Diseño
{{section_meta: keywords="EA, enterprise architecture, arquitectura empresarial, marco EA, diseño previo, diseño conceptual, diseño lógico, diseño físico, arquitectura datasphere, decisiones diseño, gobernanza diseño, hands-on sin diseño, evitar hands-on, lenguaje común negocio tecnología, dominios datasphere, capacidades negocio, roadmap modernización, EA datasphere BDC, fundamentos arquitectura"}}

La arquitectura empresarial (EA) garantiza que cada decisión de diseño en Datasphere — modelado, integración, dominios, data products — esté **alineada con las capacidades del negocio**, con el roadmap de modernización y con la transición natural hacia BDC.

{{callout: title="Por qué importa el marco EA" tone="punch" icon="💡"}}
Un diseño adecuado evita desarrollos *hands-on* sin dirección. Antes de construir, definimos: qué modelo necesitamos, en qué paradigma se apoya (Warehouse, Lakehouse, Mesh, Fabric) y cómo se relaciona con las capacidades del negocio. Esto garantiza un **lenguaje común** entre negocio y tecnología y prepara la arquitectura para evolucionar naturalmente hacia BDC.
{{/callout}}

La arquitectura Danwor para Datasphere integra cuatro dominios del marco EA:

| Dominio EA | Aplicación en Datasphere |
|---|---|
| **Arquitectura de Negocio** | Entender capacidades y procesos para definir dominios y data products |
| **Arquitectura de Datos** | Diseño conceptual → lógico → físico; metadatos, linaje, gobernanza |
| **Arquitectura de Aplicación** | Integración entre Datasphere, SAP ERP, BDC y herramientas de consumo |
| **Arquitectura Técnica** | Espacios, transportes, conectividad, performance, seguridad |

## Diseño de Modelos de Datos
{{section_meta: keywords="diseño modelos datos, modelo conceptual, modelo lógico, modelo físico, conceptual lógico físico, ERD, entidad relación, modelo de datos datasphere, trazabilidad modelo, modelado efectivo, diseño colaborativo negocio, semántica modelo, cardinalidad, claves modelo, normalización, iteración modelo, gobernanza modelo, modelo no estático, prácticas modelado"}}

Los tres modelos forman un ciclo: mantener trazabilidad entre ellos es lo que distingue una arquitectura madura de un desarrollo aislado.

| Modelo | Qué define | Herramientas |
|---|---|---|
| **Conceptual** | Entidades, relaciones y reglas de negocio — sin tecnicismos. Validado con el negocio antes de avanzar | ERD, mapas de entidades, diagramas simples |
| **Lógico** | Atributos, claves, cardinalidades, dependencias y semántica. Base de la arquitectura por capas en Datasphere | Modelo lógico normalizado, diccionario de datos |
| **Físico** | Implementación concreta: vistas SQL, tablas, naming, tipos de dato, índices | Datasphere Data Builder, objetos deployados |

> Las reglas de normalización, claves y cardinalidades se derivan del modelo **lógico** — no del físico. Construir el modelo físico sin pasar por el lógico es la causa más frecuente de retrabajo en proyectos de datos.

{{callout_row}}
{{callout: title="Diseño colaborativo con negocio" tone="success" icon="🌿"}}
Validar entidades, relaciones y reglas clave con el negocio antes de implementar. Los artefactos visuales simples (ERD, mapas de entidades) facilitan el entendimiento común entre consultores y áreas. Reduce retrabajos y desalineamientos tardíos.
{{/callout}}
{{callout: title="Iteración y gobernanza continua" tone="note" icon="🔹"}}
El modelo no es estático: se ajusta al aprendizaje del proyecto. Evitar modelos rígidos que no absorben el cambio. La gobernanza continua asegura que el modelo físico siga reflejando el lógico y el conceptual.
{{/callout}}
{{/callout_row}}

**Clasificación de Metadatos**

Los metadatos proveen la semántica que conecta el diseño conceptual, lógico y físico. Son el puente entre cómo se modelan los datos y cómo se gobiernan, descubren y trazan en Datasphere / BDC.

| Tipo | Ejemplos | Propósito |
|---|---|---|
| **Estructurales** | Tipos de dato, longitudes, claves, relaciones | Garantizan consistencia técnica del modelo |
| **Semánticos** | `@Semantics.amount`, `@EndUserText.label`, Semantic Usage | Conectan el objeto técnico con el significado de negocio |
| **Operacionales** | Frecuencia de refresh, linaje, propietario del dato | Habilitan gobernanza, trazabilidad y catálogo en BDC |

## Paradigmas Arquitectónicos
{{section_meta: keywords="paradigmas arquitectónicos, paradigmas datos, data warehouse, lakehouse, data mesh, data fabric, qué paradigma usar, cuándo usar lakehouse, cuándo usar data mesh, cuándo usar data fabric, data warehouse datasphere, lakehouse datasphere, data mesh datasphere, arquitectura híbrida, paradigma arquitectónico SAP, decisión paradigma, árbol paradigma, federación paradigma, dominios autónomos"}}

Cinco paradigmas con un mismo objetivo: transformar datos distribuidos en capacidades de negocio gobernadas. El rol del arquitecto es diseñar un **ecosistema híbrido gobernado**, no apostar a uno solo.

| Paradigma | Qué resuelve | En Datasphere |
|---|---|---|
| **Data Warehouse / Lakehouse tabular** | Reporting confiable, regulatorio y KPIs críticos | Modelado por capas, tablas persistentes, modelos analíticos, DAC, semántica SAP. **Base predeterminada.** |
| **Data Lake / Lakehouse** | Exploración, ML, granularidad masiva, datos semiestructurados | Data lake externo (HDFS/S3) + Lakehouse BDC para integración |
| **Data Mesh** | Múltiples dominios con autonomía, equipos distintos, data products distribuidos | Espacios por dominio, data products publicados, ownership distribuido |
| **Data Fabric** | Landscape híbrido/multi-cloud sin mover datos | Federación, tablas virtuales, catálogo unificado, linaje técnico |

> **Trade-offs a considerar:** Flexibilidad vs. estructura · Centralización vs. autonomía · Performance vs. virtualización · Costo total vs. duplicación de datos.

{{tree_button: Decisión de Paradigma Arquitectónico}}

## Datos como Producto
{{section_meta: keywords="data product, datos como producto, data products datasphere, data product BDC, DPD, data product descriptor, qué es un data product, definición data product, data product SAP, tratar datos como producto, unidad de valor datos, sales order data product, customer 360 data product, data product autocontenido, data product reutilizable, linaje data product, contrato datos"}}

Tratar los datos como productos alinea la arquitectura con el negocio: se pasa de construir pipelines a diseñar **capacidades reutilizables** que viven en Datasphere, el data lake o BDC.

Un data product es una unidad de valor reutilizable construida a partir de datos: combina dataset curado, metadatos, reglas de negocio y mecanismos de acceso, diseñada para resolver un **problema concreto de negocio**.

| Atributo | Descripción |
|---|---|
| **Reutilizable y acotado** | Resuelve un caso de negocio claro — no un objeto técnico genérico |
| **Autocontenido** | Incluye datos, metadatos, semántica y contratos de acceso |
| **Con contrato definido** | El consumidor conoce el esquema, la frecuencia de actualización y el SLA |
| **Con linaje trazable** | Tabla → CDS básica → CDS compuesta → vista de consumo → Data Product |
| **Gobernado** | Tiene propietario (owner), política de acceso y clasificación en el catálogo |

{{callout: title="Lo que ya hacemos es arquitectura híbrida moderna" tone="success" icon="🌿"}}
Diseñamos con federación y persistencia selectiva. Organizamos por dominios. Gobernamos con semántica y contratos. Respetamos clean core. Esto que hacemos todos los días **ya es** arquitectura híbrida moderna — el apéndice de paradigmas muestra cómo se conectan estos conceptos con nuestro marco operativo y prepara la evolución natural hacia BDC.
{{/callout}}
