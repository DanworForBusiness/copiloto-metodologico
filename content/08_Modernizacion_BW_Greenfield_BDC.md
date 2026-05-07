---
module: "08"
title: "Modernización BW Greenfield BDC"
nav_label: "08 · Modernización BW Greenfield BDC"
tags: ["module:08", "domain:modernization", "domain:bw"]
key_topics:
  - id: greenfield
    label: Greenfield
    heading: "Principio Greenfield"
  - id: relevamiento
    label: Relevamiento
    heading: "Relevamiento y Matriz Técnica"
  - id: alcance
    label: Alcance
    heading: "Árbol: Alcance de Modernización"
  - id: armonizacion
    label: Armonización
    heading: "Reglas de Armonización"
  - id: bdc
    label: BDC
    heading: "Visión BDC y Data Products"
scope: |
  Cubre la estrategia de modernización de SAP BW hacia SAP Datasphere y
  Business Data Cloud (BDC): principio Greenfield (sin Lift & Shift),
  relevamiento de uso y matriz técnica de objetos BW, árbol de decisión de
  alcance de modernización, reglas de armonización de entidades canónicas,
  y visión de data products compatible con BDC. No cubre extracción de datos
  (módulo 03), modelado detallado en Datasphere (módulo 04), ni configuración
  de espacios (módulo 01).
guardrails:
  - "Greenfield significa no hacer Lift & Shift: no recomendar migrar objetos BW por inventario sin validar uso, valor, deuda técnica y entidad canónica."
  - "La modernización debe priorizar capacidades futuras y data products; no conservar complejidad BW solo por continuidad histórica."
  - "No decidir extracción o modelado detallado desde este módulo: derivar a módulos 03 y 04 cuando la pregunta pase a implementación concreta."
out_of_scope_redirect:
  - keywords: ["extraccion", "extracción", "ODP", "ODP-SAPI", "ODP-CDS", "CDS extracción",
               "sistema fuente", "integración datos", "replicación", "federación", "persistencia"]
    module: "03"
    label: "Integración y Estrategia de Datos"
    message: "Ese tema corresponde al módulo 03 · Integración y Estrategia de Datos, que cubre extracción, ODP, CDS y estrategias de integración."
  - keywords: ["tipo semantico", "tipos semanticos", "semantic type", "fact", "dimension",
               "capas modelado", "vista analitica", "naming convention datasphere",
               "tabla remota", "tabla local", "vista SQL"]
    module: "04"
    label: "Modelado y Desarrollo"
    message: "Ese tema corresponde al módulo 04 · Modelado y Desarrollo, que cubre modelado por capas, tipos de vistas y naming."
  - keywords: ["espacio datasphere", "DEV QA PRD", "transporte datasphere", "BW bridge conectividad"]
    module: "01"
    label: "Arquitectura y Conectividad"
    message: "Ese tema corresponde al módulo 01 · Arquitectura y Conectividad, que cubre espacios, transportes y BW Bridge."
---
# 08 Modernizacion BW Greenfield BDC

## Principio Greenfield
{{section_meta: keywords="modernización BW, greenfield BW, lift and shift, no lift and shift, BW a Datasphere, migración BW, migrar BW datasphere, qué hacer con BW, dejar BW, modernizar BW, BW bridge, estrategia modernización, BW S4HANA datasphere, reutilizar BW, aprovechar BW, objetos BW útiles, ADSO datasphere, infocubo datasphere, DSO datasphere, BW greenfield approach, principio greenfield"}}

En modernizaciones hacia Datasphere (fuera del contexto Business Data Cloud), el principio rector es **no hacer Lift & Shift**: no trasladar objetos técnicos BW a Datasphere tal como están. En cambio, rediseñar Greenfield aprovechando del BW solo aquello que aporta valor real: **definiciones de negocio, semántica, reglas de calidad y conversión**.

{{callout_row}}
{{callout: title="Qué aprovechar del BW" tone="success" icon="✅"}}
- Definiciones de negocio validadas con el negocio
- Reglas de calidad y conversión consolidadas
- Extractores ODP-SAPI whitelisted (Nota SAP 2500202)
- Métricas de uso reales (reportes usados vs. obsoletos)
{{/callout}}
{{callout: title="Qué dejar atrás" tone="warn" icon="🚫"}}
- Objetos técnicos obsoletos: DSOs clásicos, infocubos, MultiProviders
- Lógica de negocio embebida en rutinas de transformación BW
- Modelos centrados en BEx queries o reportes (los reportes pasan a ser consumidores, no drivers de modelado)
- Prefijos Z/Y heredados sin semántica de negocio clara
{{/callout}}
{{/callout_row}}

> El objetivo estratégico: pasar de un modelado centrado en reportes (BEx, BO) a una **fundación de datos gobernada**: data products, catálogo y gobernanza. Compatible y preparado para la evolución natural hacia BDC.

## Relevamiento y Matriz Técnica
{{section_meta: keywords="relevamiento BW, matriz técnica BW, métricas uso BW, objetos BW uso, candidatos baja BW, 90 días uso, ADSO uso, infocubo uso, DSO uso, whitelisted BW, nota SAP 2500202 BW, extractor BW whitelisted, redundancias BW, duplicidades BW, cadenas proceso BW, complejidad ETL BW, qué objeto modernizar, qué objeto decomisisar, inventario BW"}}

Antes de decidir qué objetos modernizar, relevar uso y criticidad mediante una **Matriz Técnica** que se enriquece iterativamente:

| Dimensión | Qué relevar |
|---|---|
| **Uso y criticidad** | Métricas de ejecución últimos 90 días — promedio y pico |
| **Redundancias** | Mismo reporte alimentado por 1:n fuentes/objetos |
| **Origen de datos** | Tipo de objeto fuente (Composite, Multi, ADSO, InfoCubo, etc.) |
| **Extractor base** | ODP-SAPI, CDS, carga manual — identificar el mecanismo actual |
| **Whitelisted** | Indicador Y/N según Nota SAP 2500202 — si N, identificar CDS estándar disponible o necesidad de CDS custom |
| **Complejidad ETL** | Nivel L/M/H de complejidad de transformación (DataSource y modelo) |
| **Cadenas de proceso** | Qué procesos de carga dependen del objeto |
| **Seguridad** | Objetos de autorización asociados |

{{callout: title="Uso como herramienta de priorización — no de verdad semántica" tone="punch" icon="💡"}}
Las métricas de uso sirven para **ponderar y priorizar** qué modernizar primero, no como criterio único de decisión. Que un objeto tenga uso no significa que deba seguir existiendo tal como está. Que no tenga uso no significa automáticamente que sea candidato a baja — puede ser crítico por regulación o cierre de período.
{{/callout}}

## Árbol: Alcance de Modernización
{{section_meta: keywords="árbol modernización BW, árbol alcance BW, decidir qué modernizar, qué hacer con objeto BW, rediseñar BW, decomisisar BW, canonizar BW, migrar lógica BW, extractor soportado BW, lógica negocio core SAP, árbol migración BW datasphere, entidad canónica BW, árbol BW datasphere"}}

El árbol de decisión a continuación guía el tratamiento de cada objeto BW en la modernización:

{{tree_button: Alcance de Modernización BW}}

## Reglas de Armonización
{{section_meta: keywords="reglas armonización, armonización BW, entidades canónicas, naming canónico, convención nombres BW, normalizar BW, homogeneidad datos, eliminar prefijos Z, eliminar prefijos Y, objetos Z BW, ZACSECUND, idioma canónico, nombres técnicos inglés español, guion bajo naming, CENTRO_ID CLIENTE_DESC, armonización semántica, lenguaje técnico único, governance datos BW"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE" Pin=true}}

Al modernizar objetos BW hacia Datasphere se aplican reglas de armonización para garantizar homogeneidad, claridad y gobierno:

**Regla 1 — Idioma y caracteres**
- Todos los nombres técnicos en el idioma relevante para el cliente (inglés para casos globales, español para empresas locales)
- Sin tildes ni caracteres especiales — usar guion bajo `_` como separador
- Patrón: `<SUSTANTIVO>_<SUFIJO>`

| Ejemplo BW | Entidad Canónica |
|---|---|
| `ZACSECUND` | `ACOND_SECUNDARIO_ID` |
| `ZDESACSEC` | `ACOND_SECUNDARIO_DESC` |
| `ZKUNNR` | `CLIENTE_ID` |
| `ZMATNR` | `MATERIAL_ID` |

**Regla 2 — Tratamiento de objetos Z/Y**
- Eliminar herencias técnicas: prefijos `Z`, `Y`, numeraciones BW
- Normalizar al concepto en el idioma canónico — respetar el **significado**, no el código técnico
- Resultado: modelo armonizado con lenguaje técnico único ("menos es más")

**Regla 3 — Entidades canónicas**
- Si el objeto integra solo fuentes SAP: usar el diccionario SAP como referencia y armonizar según la convención corporativa de Datasphere
- Si integra múltiples fuentes (SAP y no-SAP): crear una nueva **Entidad Canónica** bajo la convención corporativa unificada

{{callout: title="Principio rector" tone="success" icon="🌿"}}
Menos es más — solo lo que agrega valor se moderniza. La armonización no es renombrar por renombrar: es definir un lenguaje de datos único que el negocio y la tecnología comparten.
{{/callout}}

## Visión BDC y Data Products
{{section_meta: keywords="BDC, business data cloud, data product, data products, visión BDC, compatibilidad BDC, DPD, data product descriptor, catálogo BDC, metadatos BDC, linaje BDC, separación capas BDC, VDM BDC, capas lógicas BDC, activar data product, SAP managed data product, evolución BDC, datasphere hacia BDC, cómo preparar BDC, roadmap BDC"}}

El diseño actual en Datasphere es **compatible con BDC por construcción** si se siguen los principios del marco Danwor: capas, clean core, semántica y contratos de consumo definidos.

**Data Products en BDC**

Un data product es una unidad de valor reutilizable construida a partir de datos: combina dataset curado, metadatos, reglas de negocio y mecanismos de acceso, diseñada para resolver un problema concreto de negocio.

| Característica | Descripción |
|---|---|
| **Reutilizable y acotado** | Resuelve un caso de negocio claro (ej: Sales Order, Customer 360) |
| **Autocontenido** | Incluye datos, metadatos, reglas y contratos de acceso |
| **Con linaje trazable** | Tabla base → CDS básica → CDS compuesta → consumo → Data Product |
| **Compatible con separación de capas** | Las vistas de consumo (capa 3) son los candidatos naturales a Data Products |

**Anotaciones CDS clave para BDC**

| Anotación | Propósito |
|---|---|
| `@EndUserText.label` | Nombres legibles para columnas en el catálogo |
| `@Semantics.amount.currencyCode` | Unidades monetarias |
| `@Semantics.unitOfMeasure` | Unidades de medida |
| `@ObjectModel.text.element` | Campos de texto descriptivos |
| `@AccessControl.authorizationCheck` | Integración con políticas de seguridad |

{{callout_row}}
{{callout: title="Separación de capas → contratos BDC" tone="punch" icon="💡"}}
La separación en capas garantiza que cambios en la lógica interna o en la publicación sean transparentes. Las vistas de consumo exponen el **contrato final** para usuarios y aplicaciones — ese contrato es el Data Product en BDC.
{{/callout}}
{{callout: title="Nuestra guía de hoy, compatible con BDC mañana" tone="success" icon="🌿"}}
Lo que aplicamos en cada proyecto ya es arquitectura híbrida moderna: federación y persistencia selectiva, organización por dominios, gobernanza con semántica, clean core. No cambia cómo trabajamos — prepara la transición natural a BDC.
{{/callout}}
{{/callout_row}}
