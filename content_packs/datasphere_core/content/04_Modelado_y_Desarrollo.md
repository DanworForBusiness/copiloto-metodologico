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
    label: Modelado de vistas
    heading: "Modelado de vistas"
  - id: semantico
    label: Semántico
    heading: "Tipos y Uso Semántico"
  - id: naming
    label: Naming
    heading: "Convención de Nombres"
  - id: flujos_datos
    label: Flujos de Datos
    heading: "Flujos de Datos"
roadmap:
  - stage: "Capas"
    tagline: "Por qué separamos responsabilidades, y qué hace cada una"
    sections:
      - "Evolución de Arquitectura por Capas"
      - "Aplicación de Arquitectura por Capas"
      - "Arquitectura por Capas: Guia Operativa"
  - stage: "Artefactos"
    tagline: "Qué construís en cada capa, y con qué herramienta"
    sections:
      - "Artefactos y uso Semántico"
      - "Modelado de vistas"
      - "Flujos de Datos"
  - stage: "Gobierno del Modelo"
    tagline: "Cómo lo nombrás y cómo validás que esté listo"
    sections:
      - "Convención de Nombres"
      - "Checklist de Desarrollo"

scope: |
  Cubre el modelo de desarrollo en SAP Datasphere: arquitectura por capas
  (ingesta, armonización, propagación, consumo), modelado de vistas (Gráfica,
  SQL con consulta estándar y evaluación de SQLScript), Flujos de Datos (Replication Flow,
  Data Flow y Transformation Flow: capacidades, carga delta y el puente
  HANA↔HDLF), tipos de uso semántico (Relacional,
  Dimensional, Fact), convención de nombres de artefactos, y buenas prácticas
  de desarrollo. No cubre configuración de espacios ni transportes (módulo 01),
  seguridad de roles o DAC (módulo 02), ni extracción desde sistemas origen
  (módulo 03).
guardrails:
  - "Respetar arquitectura por capas: no recomendar consumo directo desde objetos crudos cuando corresponde armonizar, propagar o exponer una capa semántica."
  - "Los Semantic Types y usos semánticos se deciden en el modelo de Datasphere; no trasladar esa decisión a extracción o conectividad."
  - "No usar Data Flows, SQL Views o Table Functions como atajo genérico: elegir el artefacto según semántica, mantenibilidad, performance y responsabilidad de capa."
quick_action_guidance:
  "Guía operativa del módulo":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 04:
      - La guía debe centrarse en modelado y desarrollo, no en conectividad, seguridad o reporting.
      - La decisión dominante es separar responsabilidades por capa: ingesta, armonización, propagación y consumo.
      - Usar la evolución LSA/Lakehouse/Datasphere como contexto conceptual y la guía operativa por capa como criterio práctico.
      - Incluir elección de artefacto: SQL estándar, vista gráfica, table function o data flow según mantenibilidad, performance y responsabilidad.
      - Incluir uso semántico: Relational, Dimension, Fact, textos y jerarquías.
      - Incluir convención de nombres como mecanismo de trazabilidad, linaje y gobierno.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 04 - GUÍA OPERATIVA:
      La decisión arquitectónica dominante debe formularse como: definir la arquitectura por capas y la responsabilidad de cada artefacto antes de construir modelos.

      Incluí obligatoriamente:
      - Separación entre ingesta, armonización, propagación y consumo.
      - Cuándo persistir y cuándo mantener lógica federada dentro del modelo.
      - Elección de tipo de vista/artefacto según complejidad, performance y mantenibilidad.
      - Asignación correcta de uso semántico.
      - Convención de nombres como práctica de gobierno.
      - Derivar extracción/conectividad al módulo 03, DAC al módulo 02 y estrategia de reporting al módulo 05.
  "Checklist de implementación":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 04:
      - El checklist debe validar que el modelo esté listo para desarrollo productivo y consumo gobernado.
      - Debe cubrir capas, semantic usage, nombres, claves, cardinalidades, asociaciones, linaje, performance y documentación.
      - No resolver seguridad DAC ni extracción desde origen: solo marcar dependencias.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 04 - CHECKLIST DE IMPLEMENTACIÓN:
      Construí un checklist de modelado y desarrollo.

      Debe validar:
      - Capa asignada a cada artefacto.
      - Responsabilidad de transformación por capa.
      - Tipo de vista/artefacto elegido y justificación.
      - Semantic Usage correcto.
      - Claves, cardinalidades y asociaciones.
      - Nombres según convención Danwor.
      - Reutilización vs duplicación de lógica.
      - Preview/pruebas de datos y validación funcional.
      - Performance: pushdown, filtros, granularidad y persistencia si aplica.
      - Documentación, linaje y ownership.
      - Dependencias de DAC/seguridad y reporting sin resolverlas desde este módulo.
      - Preparación para transporte/versionado.
  "Errores frecuentes":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 04:
      - Los errores deben estar asociados a modelado, capas, semántica, artefactos y convenciones.
      - No convertir la respuesta en errores de conectividad, extracción, seguridad o reporting.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 04 - ERRORES FRECUENTES:
      Priorizá errores como:
      - Consumir objetos crudos directamente sin capa de armonización/propagación.
      - Mezclar responsabilidades de capas en una única vista.
      - Elegir vista gráfica, table function o data flow como atajo sin justificación.
      - Asignar mal Semantic Usage.
      - No definir claves, cardinalidades o asociaciones.
      - Duplicar lógica en lugar de reutilizar artefactos gobernados.
      - Romper convención de nombres y trazabilidad.
      - Esperar que reporting corrija problemas del modelo.

      Para cada error, explicá causa raíz, impacto y corrección Danwor.
  "Ejemplos prácticos":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 04:
      - Los ejemplos deben salir de arquitectura por capas, guía operativa por capa, tipos de vistas SQL, tipos/uso semántico y convención de nombres.
      - Usar casos de modelado, no de extracción ni conectividad.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 04 - EJEMPLOS PRÁCTICOS:
      Construí ejemplos de desarrollo:
      - Modelo por capas desde datos crudos hasta consumo analítico.
      - Elección entre SQL estándar, vista gráfica, table function o data flow.
      - Asignación de Semantic Usage para facts, dimensiones, textos.
      - Aplicación de convención de nombres para trazabilidad.

      Para cada ejemplo, indicá fuente interna usada, decisión Danwor, por qué esa y no otra, y riesgo si se elige mal.
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
  - keywords: ["template CDS", "template vista CDS", "template extraccion CDS",
               "cds extractor template", "template dimensión", "ZCDS_DWC_DIM_TEMPLATE"]
    description: "Template CDS para extracción hacia SAP Datasphere"
    asset: "templates/cds_extractor_template.abap"
    language: "abap"
---
# 04 Modelado y Desarrollo

## {dw-black}Evolución de Arquitectura {dw-gold}por Capas
{{section_meta: keywords="arquitectura por capas, capas modelado, ingesta armonización propagación consumo, separación responsabilidades, capa ingesta, capa armonización, capa propagación, capa reporting, capa consumo, lakehouse SAP, principio capas datasphere, arquitectura modelado datasphere, modelo por capas, cómo modelar datasphere, diseño capas, capa 1 2 3, tablas remotas capa, vistas capa, persistencia capas, federación capas"}}

{{subtitle: no-gap}}
> El principio de separación de responsabilidades tal y como lo conocemos históricamente bajo el nombre LSA++ (Layer Scalability Architecture) no es un legado, sino un patrón estructural vigente en arquitecturas modernas -como Lakehouse- que aunque popularizan arquitecturas Bronze, Silver y Gold, el principio arquitectónico sigue siendo el mismo: separar responsabilidades entre ingesta, armonización, publicación y consumo.

| Principio | SAP BW (LSA) | Lakehouse (Databricks) | Datasphere/BDC |
|---|---|---|---|
| **Ingesta desacoplada del origen** | Staging Layer <br>Corporate Memory | Bronze | Tablas Remotas / Persistencia opcional / Data Lake (HDLF en BDC) |
| **Armonización** | EDW Layer | Silver | Business Builder / Data Builder |
| **Publicación** | Reporting Layer | Gold | Data Products / Modelos Analíticos |
| **Gobierno Semántico** | Infoobjects / BW Semantics | Unity Catalog + Delta | Semantic Layer |

{{callout: title="El principio no cambia, sino su forma de implementarlo" tone="punch" icon="⭐"}}
Hoy la escalabilidad ya no depende de multiplicar capas físicas, sino del diseño semántico, la separación entre almacenamiento y procesamiento, y la gobernanza de contratos y metadatos. En este contexto, Bronze/Silver/Gold describen la implementación física del Lakehouse, mientras que nuestras capas describen responsabilidades arquitectónicas. Ambos enfoques son complementarios.
{{/callout}}

## {dw-black}Aplicación de Arquitectura {dw-gold}por Capas
{{section_meta: keywords="arquitectura por capas, capas modelado, ingesta armonización propagación consumo, separación responsabilidades, capa ingesta, capa armonización, capa propagación, capa reporting, capa consumo, lakehouse SAP, principio capas datasphere, arquitectura modelado datasphere, modelo por capas, cómo modelar datasphere, diseño capas, capa 1 2 3, tablas remotas capa, vistas capa, persistencia capas, federación capas"}}

>Implementamos la arquitectura de modelado por capas con separación de responsabilidades, adaptada a un enfoque Lakehouse moderno en SAP Datasphere:
{{html_embed: ../assets/danwor-arquitectura-capas.html height=970}}

## {dw-black}Arquitectura por Capas: {dw-gold}Guia Operativa
{{section_meta: keywords="arquitectura por capas, responabilidad del dato"}}

> Patrón de separación de responsabilidades aplicado a nuestro marco.

| Capa | Nivel de Transformacion | Proposito | Cuando usar? |
|---|---|---|---|
| **(1) Ingesta** | Ninguno o muy bajo. Mapeo 1:1 y datos tal cual la fuente. | Capturar los datos crudos directamente desde las fuentes externas (sea S/4HANA, u otras bases de datos y/o sistemas externos) | - Ingestar datos directamente desde sistemas fuente. - Preservar datos originales para auditoría. |
| **(2) Armonizacion** | Según la semántica que necesita completarse: alta cuando hay que completar, homologar o enriquecer; baja cuando la fuente gobernada ya entrega la semántica requerida | Armonizar los datos completando y adaptando a las definiciones corporativas la semántica que el origen no resuelve, para asegurar consistencia y reutilización alineadas con las reglas de negocio, sin repetir transformaciones ya resueltas en una fuente gobernada o en un contrato reutilizable | - Unificar datos de sistemas heterogéneos. - Reutilizacion - Normalizar formatos, eliminar duplicados, enriquecer datos. |
| **(3) Propagacion** | Baja. Ajustes mínimos: joins, unions, agregación y filtros | Exponer **data products gobernados** para consumo externo, open sharing e integraciones, y como fuente del modelado analítico. Los data products **nacen acá** (no llevan una capa 4 encima). | - **Consumo externo / open sharing**: Delta Sharing → Databricks, SPM/SN Distribuidores, outbound. Si un destino exige una adaptación técnica de tipo (p. ej. `DecimalFloat` → `DECIMAL(38,6)`), **se hace acá**; la normalización corporativa de tipos, precisión y escala pertenece a Armonización. - **Consumo analítico por vista / DirectQuery**: capa de ownership `3V..._PBI` (vista plana ID+Descripción) gobernada para Power BI. - Reutilización por otras vistas y como input de capa 4. |
| **(4) Consumo** | Alta. Modelado analítico final. | **Modelar** la experiencia analítica final que consume el data product; no publica un data product ni un contrato nuevo. | - **SAC**: Modelo Analítico (`AM`, multidimensional nativo, sin número de capa). - Vista/tabla `4V/4T` con medidas y dimensiones **solo** como adaptador excepcional de un canal que no puede consumir el data product o el `AM` directamente. |

{{callout: title="La Publicación del principio se implementa en dos capas (3 + 4)" tone="punch" icon="🔗"}}
Antes hablamos de la etapa de **Publicación** — el momento en que el dato se vuelve consumible. En la operación esa misma responsabilidad se implementa en dos capas complementarias: **(3) Propagación**, que expone el data product gobernado hacia otros sistemas o consumo directo, y **(4) Consumo**, que arma los modelos analíticos finales para el negocio. No son dos conceptos nuevos: son la misma Publicación, desglosada según cuánto falta preparar el dato antes de que llegue a destino.
{{/callout}}

## {dw-black}Artefactos y uso {dw-gold}Semántico
{{section_meta: keywords="tipo semántico, tipos semanticos, uso semántico, semantic type, fact, dimension, texto dimension, relacional, jerarquía datasphere, capa business, capa consumption, business layer, consumption layer, semantic usage, modelo analitico, vista analitica, entidad de negocio, business entity, qué tipo semántico usar, cuándo usar fact, cuándo usar dimension"}}

> El **tipo semántico** (Semantic Usage) define cómo Datasphere interpreta y expone el objeto en la capa de modelado. Asignarlo correctamente habilita el autoservicio analítico y la navegación semántica en herramientas de consumo.

| Tipo Semántico | Uso | Notas |
|---|---|---|
| **Relacional** | Vistas de preparación o armonización que no son consumidas directamente en reportes | Es el tipo por defecto; reclasificar una vez que el objeto tiene semántica de negocio definida |
| **Dimension** | Maestros y atributos descriptivos (Clientes, Materiales, Centros de Costo) | Debe tener una clave única; asociable con Facts en el modelo analítico |
| **Text** | Textos de una entidad dimensional (descripción de Material en varios idiomas) | No apto para extracción directa desde origen; usar como capa intermedia de textos en CDS |
| **Hierarchy** | Jerarquías organizacionales o de producto | Requiere la dimensión base asociada |
| **Fact** | Hechos transaccionales (Ventas, Pedidos, Movimientos de Stock) | Con métricas y asociaciones a dimensiones |

> La **capa Business** (Business Layer) agrega la semántica de negocio sobre los objetos técnicos. La **capa Consumption** (Consumption Layer) expone los objetos analíticos finales. Las reglas de normalización, claves y cardinalidades se derivan del modelo lógico.

## {dw-black}Convención de {dw-gold}Nombres
{{section_meta: keywords="convención nombres, naming convention, convención de nombres datasphere, prefijo artefacto, nomenclatura datasphere, naming artefactos, nombre vista, nombre tabla, nombre objeto datasphere, SAP DSP EDB TXT API, notación camello, CamelCase datasphere, prefijo 1 2 3, cómo nombrar objetos, estándar nombres, convencion nombres danwor, prefijo de vista, prefijo de tabla, prefijo para una vista, que prefijo, qué prefijo, prefijo vista, prefijo tabla, como llamo, cómo llamo, como se llama, cómo se llama un objeto, llamar objeto, llamar una tabla, sufijo, sufijo PBI, sufijo power bi, sufijo _PBI, 3V, 2V, 1T, prefijo data flow, prefijo de data flow, nombre data flow"}}

{{subtitle: no-gap}}
> El patrón de nomenclatura Danwor garantiza trazabilidad, linaje y consistencia entre ambientes:

```<Capa><Artefacto><Variante>_<Origen>_<libreConLineamientosPosibles>```

**Capa** (prefijo numérico):

- `1` — **Ingesta**: datos crudos del origen, sin transformar (tablas remotas o locales replicadas).
- `2` — **Armonización**: donde se completa el contrato de datos, reutilizando la semántica funcional del origen y adaptando o incorporando las definiciones corporativas necesarias, mediante entidades limpias, estandarizadas, gobernadas y reutilizables (ver [Contrato de Datos vs. Data Product](/modules/03_Integracion_y_Estrategia_de_Datos?pack={{pack}}#contrato-de-datos-vs-data-product)).
- `3` — **Propagación**: el contrato materializado y expuesto como data product — composición (cruces/joins), reagregación y subsets orientados a casos de consumo. 
- `4` — **Consumo**: adaptadores finales y modelos. 

> Los **modelos analíticos** se nombran como `AM` (artefacto tipado, **sin número de capa**). Un `4V`/`4T` es el **adaptador excepcional y exclusivo** de un canal que no puede consumir el data product o el `AM` directamente; si la salida es reutilizable, es Capa 3. Consume; no introduce un contrato nuevo.

**Artefacto** (letra):

- `T` — Tabla (local o remota). 
- `V` — Vista (SQL Estándar o Gráfica). 

**Variante** (letra):

- `F` — Fact 
- `R` — Relacional
- `D` — Dimension
- `A` — Analítica
- `T` — Texto
- `H` — Jerarquia

> Regla Danwor: la ingesta (`1`) mantiene el dato crudo como tabla remota o local. Si hay renombrado semántico, joins, limpieza, armonización o preparación, ya no es Capa 1: corresponde a Capa 2 o superior.

**Origen:** (sufijo de 3 letras, solo para capa de ingesta)

| Código | Origen |
|---|---|
| `SAP` | Ingesta desde SAP (ECC, S/4HANA, BW) |
| `DSP` | Generado dentro de Datasphere (DF, RF, etc.) |
| `EDB` | Base de datos externa (Oracle, SQL Server) |
| `TXT` | Archivo plano (CSV, TXT) |
| `API` | Ingesta vía API |

**Texto Libre con Lineamientos:**
Salvo por la capa de ingesta donde se mantiene la convencion de nombre del origen, es un texto libre que describe el objeto en cuestión pero con ciertos lineamientos para darle mayor robustez

- Sufijos (consumidor exclusivo — lista gobernada y extensible; un consumidor nuevo se aprueba antes de usarlo): 
    - *_PBI (exclusivo consumo Power BI)
    - *_SPM (exclusivo outbound SPM)
    - *_DBX (exclusivo Databricks / lakehouse externo)
  
- Prefijos: 
    - MD_* (Master Data)
    - FNC_* (Finanzas)
    - IVT_* (Inventarios)
    - PUR_* (Compras)
    - SND_* (Ventas y Distribucion)

**Secuencial:** (solo si desean usar ciclo de vida/versionado)
> llegado el caso de querer utilizar versionado (ciclo de vida de vistas para facilitar el uso/manejo de versionado y ciclo de vida sobre todo desde la capa de armonizacion en adelante).

**Ejemplos:**

| Objeto | Interpretación |
|---|---|
| `1TT_SAP_T151T` | Capa 1 · Tabla · Origen SAP · T151T (textos grupos cliente) |
| `2VD_CLIENTES_CABECERA` | Capa 2 · Vista · Dimensión Clientes Cabecera (armonización) |
| `3VF_TOPLINE_SPM` | Capa 3 · Vista Fact · Topline para SPM (data product reutilizable) |
| `3VD_CUSTOMER_PBI` | Capa 3 · Vista · Dimensión Customer exclusiva de Power BI (gobernada) |
| `4VF_SND_DESPACHOS_DBX` | Capa 4 · Vista Fact · adaptador **exclusivo** de Databricks (no reutilizable) |

{{subtitle: no-gap}}
> Aunque no estemos del todo acostumbrados, usar CamelCase para la parte libre (`<LibreConLineamientos>`) ayuda/mejora la legibilidad por sobre nombres extensos separados con guion bajo.

### Otros Artefactos
A diferencia de **tablas y vistas** (que usan el patrón por capas de más arriba), estos artefactos **no llevan prefijo de capa**. El número de capa responde a una pregunta que solo tiene sentido para un contenedor genérico — *"¿qué rol juega esta tabla/vista?"* — y un artefacto tipado ya lo responde con su abreviación. Siguen un patrón simple: `<Abreviación>_<Prefijo LoB>_<TextoLibre>` — la abreviación del artefacto, el prefijo de línea de negocio (MD/FNC/IVT/PUR/SND…) y el texto libre con lineamientos. Por ejemplo, un Replication Flow de Ventas se nombra `RF_SND_ENTREGAS` (no `1RF...`), y un Analytic Model `AM_SND_FACTURACION` (no `4AM...`). Referencia:

| Artefacto | Abreviación | Ejemplo
|---|---|---|
| **Data Flow** | DF | DF_SND_PEDIDOS |
| **Replication Flow** | RF | RF_SND_ENTREGAS |
| **Transformation Flow** | FT | FT_SND_CONDICIONES |
| **Intelligent Look-up** | IL | IL_MNF_NOTIFICACIONES |
| **Task Chain** | TC | TC_SND |
| **Analytic Model** | AM | AM_SND_FACTURACION |
| **Entity Rel'ship Model** | EM | EM_SND_ENTREGAS |
| **Data Access Controls** | DAC | DAC_SND |

{{callout: title="Frontera de consumo: dónde vive cada salida" tone="note" icon="🎯"}}
Una misma información puede exponerse de tres formas, y el naming lo refleja:
- **Modelo semántico** → `AM_<LoB>_<Nombre>` (sin número; es la vía preferida).
- **Producto reutilizable y gobernado** → Capa 3 `3V/3T`, con sufijo de consumidor solo si la salida es exclusiva de ese consumidor (`3VF_SND_Despachos` reutilizable o `3VF_SND_Despachos_PBI` exclusivo).
- **Adaptador exclusivo e irreusable de un canal** que no puede consumir el `AM` o el data product directamente → Capa 4 `4V/4T` con sufijo *obligatorio* (`4VF_SND_Despachos_DBX`). Si resultara reutilizable, es Capa 3.

Clave: el **shaping de negocio** (reagrupaciones, filtros, subsets) para un consumidor **sigue siendo Capa 3** — una capa de *ownership* por consumidor (`3V..._PBI`), gobernada y con dueño. La Capa 4 es solo el último eslabón **técnico e irreusable**; nunca una Capa 4 "vacía" por completitud.
{{/callout}}


## {dw-black}Modelado de {dw-gold}vistas
{{section_meta: keywords="modelado de vistas, tipos vistas SQL, vista estándar, vista gráfica, table function, SQLScript, qué tipo de vista usar, cuándo usar vista gráfica, cuándo usar SQLScript, vista SQL datasphere, vistas datasphere, crear vista, SQL standard view, graphical view, qué vista elegir, expresividad mantenibilidad, patrón metodológico gobernado, persistencia de vistas"}}
{{section_exclude: "codigo, código, template, ejemplo, ayudame con el codigo, como hago, como escribo, escribir, generar codigo, crearme, dame un, mostrame, sample, snippet, data flow, replication flow, transformation flow"}}

> Elegimos cómo expresar y mantener una transformación en una vista de SAP Datasphere. **Vista gráfica y vista SQL con consulta estándar son modalidades legítimas para modelos productivos y reutilizables**. SQLScript (Table Function) es un candidato excepcional que requiere validar una capacidad concreta; no es la consecuencia automática de una transformación compleja.

<span id="tipos-de-vistas-sql" aria-hidden="true"></span>

| Modalidad | Cuándo elegirla o evaluarla | Condiciones para reconsiderar |
|---|---|---|
| **Vista gráfica** | El equipo necesita diseñar, revisar y mantener fuentes, relaciones y operaciones mediante un modelo visual claro. | Una operación necesaria no está admitida o el modelo deja de ser mantenible: reconsiderar SQL o revisar el caso. Pasar a producción no obliga a migrar a SQL. |
| **Vista SQL con consulta estándar** | Se necesitan expresiones SQL explícitas o el patrón gobernado de desarrollo, revisión y evolución del equipo utiliza esa modalidad. | Verificar expresiones y fuentes compatibles con Datasphere. No se promete mejor rendimiento universal ni se prescribe SQL para todo caso. |
| **SQLScript (Table Function)** | Hay una capacidad algorítmica concreta imprescindible que no se resuelve razonablemente mediante composición declarativa. | Validar necesidad, compatibilidad, restricciones técnicas y contrato de salida antes de implementar; no certificar cualquier función o librería de HANA. |

{{callout: title="Elegir por expresividad y mantenibilidad" tone="punch" icon="🔎"}}
Si gráfica y SQL son técnicamente viables, favorecer el **patrón metodológico gobernado del equipo**, considerando convenciones de desarrollo, revisión, reutilización y evolución del proyecto. Si faltan criterios suficientes, acordarlos antes de elegir: no inventar una preferencia universal ni cuotas de uso. El rendimiento se verifica en el objeto y el plan concreto; el editor gráfico permite inspeccionar el SQL generado.
{{/callout}}

**SQLScript exige una justificación concreta.** Varias etapas, joins, CASE, CTE o una transformación compleja no demuestran por sí mismos su necesidad. La admisibilidad de cada construcción se verifica en Datasphere; no se extrapola toda capacidad de HANA. Definir y validar las columnas y tipos de salida, sin prometer un esquema dinámico arbitrario. Una vista wrapper puede justificarse por contrato de consumo, semántica o encapsulación; su sola existencia no garantiza el linaje.

**Modelado, carga y optimización son decisiones distintas.** Para cargas, deltas o Python, continuar en [Flujos de Datos](#flujos-de-datos), donde se evalúan las alternativas aprobadas. Para acelerar o persistir una vista existente, revisar primero mediciones y restricciones de fuentes, parámetros, permisos y DAC. La persistencia de una vista no conduce automáticamente a Data Flow ni equivale a diseñar una carga a tabla.

**Responsabilidad de capa y consumo.** La capa **1 — Ingesta** conserva el contrato del origen; **2 — Armonización** completa la semántica faltante; **3 — Propagación** expone el contrato gobernado; **4 — Consumo** resuelve el modelado o adaptador final que corresponda. Modalidad de edición, uso semántico y canal de consumo son decisiones diferentes. Un **Analytic Model** se justifica para el consumo analítico que lo requiera, no para toda vista relacional.

El Quick Wizard orienta normalmente en **dos preguntas**. Si falta definir la transformación, propone acordar entrada, salida y regla de negocio; si la necesidad es rendimiento/persistencia, propone diagnosticar la vista; si falta elegir modalidad, propone acordar expresividad, responsable y patrón de mantenimiento. La recomendación metodológica no certifica la compatibilidad de un objeto concreto ni sustituye una validación técnica.

{{support: Vista gráfica="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/27efb479c4814252964d3fbc6ca2dfc3.html" Vista SQL y SQLScript="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/81920e4d583f45fd8761c662d3c8abab.html" Persistencia de vistas="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/9bd12cf116ae40e09cdba8b60cf75e11.html" SQL admitido en Datasphere="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/6a37cc58322548a986496d5e139e9201.html" SAP Learning="https://learning.sap.com/courses/exploring-sap-datasphere-1/working-with-views-for-propagation_e954780f-3b00-4d69-a29a-eecdff6cf192"}}

{{tree_button: id=m04_tipos_vistas_sql label="Modelado de vistas"}}


## {dw-black}Flujos de {dw-gold}Datos
{{section_meta: keywords="replication flow, data flow, transformation flow, flujos de datos datasphere, diferencia replication flow data flow transformation flow, carga delta, captura delta, delta capture, vista SQL en flujo de transformación, runtime HANA Spark, materializar tabla local, cuándo usar transformation flow, transformation flow vs data flow, tabla local delta, transformation flow HDLF, movimiento HANA HDLF, mover datos hacia HDLF, integración HANA data lake, flujo de transformación SQL, data flow no soporta delta, data flow HDLF, replication flow delta, qué flujo usar"}}
{{section_exclude: "nombro, nombre, como se llama, cómo se llama, como llamo, cómo llamo, llamar objeto, prefijo, naming, convención, puntero delta, delta byelement, delta.byelement"}}
{{support: Replication Flow="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/25e2bd7a70d44ac5b05e844f9e913471.html?locale=es-ES&version=LATEST" Data Flow="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/e30fd1417e954577baae3246ea470c3f.html?locale=es-ES&version=LATEST" Transformation Flow="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/f7161e6c20204672ac4a6d90c81762e4.html?locale=es-ES&version=LATEST" Comparativa oficial="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/34ae0a2ea6e94483b19f632a2843d56d.html?locale=en-US&version=LATEST"}}

> Datasphere ofrece tres flujos de datos, cada uno resolviendo una necesidad diferente: **Replication Flow** para el movimiento de datos desde sistemas origen con delta nativo, **Data Flow** transforma con la interfaz gráfica más completa pero sin capacidad delta. Por último, **Transformation Flow** persiste con transformación (gráfica o SQL) y compatibilidad delta.

{{callout: title="SAP ya no recomienda Data Flow como primera opción" tone="punch" icon="📣"}}
La documentación oficial es explícita: Replication Flow y Transformation Flow **"son ahora las formas preferidas de cargar y escribir datos"** en tablas locales, incluido el object store — por encima de Data Flow. Cuando un Data Flow existente es candidato a convertirse en Transformation Flow (o en la combinación Replication + Transformation Flow), el propio editor de Data Flow muestra un banner sugiriendo la migración.
{{/callout}}

| Dimensión | Replication Flow | Data Flow | Transformation Flow |
|---|---|---|---|
| **Qué hace** | Mueve datos para uno o más objetos de un origen a un destino | Mueve y transforma datos en una interfaz gráfica (drag & drop) | Carga desde 1+ fuentes, transforma y escribe a **una** tabla local |
| **Fuentes** | Vistas CDS habilitadas para extracción, objetos ODP (BW/extractores), tablas con clave primaria — más una amplia matriz de conectores no-SAP | Objetos admitidos del navegador de fuentes (tablas, vistas locales/remotas y Open SQL), según conexión y restricciones del objeto | Objetos admitidos de Datasphere: tablas locales, vistas y Open SQL admitidos, y tablas BW bridge compartidas o vistas sobre ellas; sujeto a dependencias, DAC y al tipo de espacio |
| **Transformación** | Mínima: expresiones de filtro, mapeo de columnas, proyección personalizada — sin joins | Completa: Join, Unión, Proyección, Columnas Calculadas, Agregación, Script Python | Join, Agregación, Filtro, Proyección, Columnas Calculadas, Vista SQL — **y Script Python si el espacio destino es de archivos (HDLF)** |
| **Carga delta** | Sí — nativa, es la razón de ser del artefacto | **No soportada** (Initial, Batch load) | Sí (Initial Only / Initial and Delta), pero requiere Captura Delta encendido en **origen y destino**; no soporta delta desde vistas |
| **Destino** | Tabla local, o destino externo — amplia matriz de conectores SAP y no-SAP (S3, Kafka, Azure, GCS, HDLFS, etc.) | Solo tabla local | Solo una tabla local |
| **Runtime / HDLF** | No selecciona runtime — pero puede exportar hacia HDLF vía una conexión dedicada (`SAP HANA Cloud, Data Lake, Files`), como Parquet, con delta. Hacia una Local Table (File) convierte `DecimalFloat` a `DECIMAL(38,6)` (documentado por SAP) | **No compatible con espacios de archivo (HDLF)** — restricción explícita | HANA o Spark, según storage del espacio destino — materializa directo en el HDLF del espacio |
| **Paralelización** | Sí — particiona la carga inicial para paralelizarla | Parcial | Sí — batches en runtime HANA; no disponibles en File Space/Spark |
| **Recuperación ante fallos** | Sí — reinicia desde el punto de falla; permite pausar/reanudar a nivel de flujo u objeto | No — reinicia desde el principio (salvo "Automatic Restart on Failure" activado) | No |
| **Parámetros de entrada** | No soportados | Sí | Sí (nombre técnico con restricciones); no utilizables dentro del operador Python de Spark |
| **Cuándo usarlo** | Ingesta desde orígenes soportados y necesidad incremental; también exportar hacia HDLFS externo (Parquet) para archiving o interoperabilidad | Python en HANA o fuentes no admitidas por TF cuando DF directo es viable; sin delta y sin imponer una adquisición separada | Persistencia con transformación mantenible + delta, incluida materialización hacia el HDLF del propio espacio |

> Para adquisición de Replication Flow y captura de cambios (CDC) desde el sistema origen — puntero delta, ODP-CDS, Change_Type/Change_Date y la semántica de los cambios — ver [Extracción vía API Corporativa →](/modules/03_Integracion_y_Estrategia_de_Datos?pack={{pack}}#extraccion-via-api-corporativa) en el módulo 03.

### Transformation Flow

**Fuentes y aplicabilidad:** las vistas y objetos Open SQL que consumen tablas remotas generales, directa o indirectamente, no son fuentes admitidas. SAP documenta la excepción de vistas sobre remotas BW bridge compartidas. También hay restricciones de DAC aplicado a vistas o a sus dependencias. La disponibilidad debe verificarse para el tipo de espacio: las capacidades de HANA no se trasladan automáticamente a File Space. La recomendación metodológica no certifica la compatibilidad de un objeto concreto.

**Límites a tener en cuenta:**

| Límite | Detalle |
|---|---|
| Timeout | Falla si una ejecución supera 48 horas |
| Deletes + joins internos | Un registro eliminado en el origen no se propaga al destino — el join no puede matchear el delete marker contra un registro que ya no existe |
| Deletes + left join filtrado | Si la vista de registros activos está del lado derecho de un LEFT JOIN con filtro NOT NULL, el delete tampoco se propaga |
| Destino | Solo tablas locales del repositorio de Datasphere — no compatible con objetos externos / outbound |

{{callout: title="¿Por qué Transformation Flow sobre Data Flow?" tone="note" icon="🔀"}}
Para transformaciones con persistencia, Transformation Flow es la preferencia metodológica cuando las fuentes, el procesamiento y el destino son compatibles. Admite delta bajo sus condiciones y puede escribir en File Space con runtime Spark, donde ofrece Python. No recupera desde el punto de falla: una ejecución fallida debe volver a ejecutarse. Data Flow conserva su nicho para Python en HANA y para fuentes que TF no puede consumir, si el objeto concreto es admitido por DF; no escribe en File Space ni soporta delta. RF Initial Only + TF es una alternativa cuando hace falta adquirir primero o se justifica separar la adquisición, conservando el procesamiento y el destino requeridos; no equivale a ejecutar Python en HANA ni garantiza recuperación de toda la cadena.
{{/callout}}

{{tree_button: Flujos de Datos}}

## {dw-black}Checklist de {dw-gold}Desarrollo
{{section_meta: keywords="checklist desarrollo, checklist modelado, lista verificación modelado, oportunidad mejora modelado, revisión modelo, buenas practicas desarrollo datasphere, puntos control desarrollo, checklist capas, checklist naming, checklist semántico, checklist vistas, verificar modelo"}}

Orientativo para revisar la calidad del modelo antes de promover al siguiente ambiente:

| Item | Pregunta de control |
|---|---|
| **Separación de capas** | ¿Cada vista consume solo de la capa inmediatamente anterior? |
| **Reutilización** | ¿Las derivaciones, conversiones, textos y reglas comunes se resuelven en un contrato gobernado reutilizable, evitando duplicaciones innecesarias aguas abajo? |
| **Tipo semántico** | ¿Todos los objetos finales tienen el tipo semántico correcto (Fact / Dimension / Relacional)? |
| **Naming** | ¿Los nombres siguen el patrón `<Capa><Artefacto><Variante>_<Origen>_<libreConLineamientosPosibles>`? |
| **Persistencia** | ¿La persistencia está justificada (alto volumen, SLA, transformación costosa) y es compatible con los DAC del linaje ([ver Dónde aplicar DAC](/modules/02_Seguridad_y_Gobernanza?pack={{pack}}#donde-aplicar-dac))? |
| **Tipo de vista** | ¿La modalidad gráfica o SQL se justifica por expresividad y mantenibilidad, favoreciendo el patrón gobernado del equipo si ambas son viables? |
| **Claves** | ¿Las vistas dimensionales tienen clave única definida? |
| **Textos** | ¿Las dimensiones maestras integran atributos y textos en un solo objeto (sin separar)? |
| **Linaje** | ¿La necesidad de SQLScript y su contrato de salida están validados, y el linaje comprobado sin asumir que un wrapper lo garantiza? |

{{callout: title="Principio de diseño Danwor" tone="success" icon="🌿"}}
Las reglas de normalización, claves y cardinalidades se derivan del modelo lógico — no al revés. El checklist es más efectivo en el cierre de cada sprint de desarrollo y antes de transportar a QA.
{{/callout}}
