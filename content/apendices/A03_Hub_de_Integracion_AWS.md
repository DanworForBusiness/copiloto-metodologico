---
module: "09"
title: "Hub de Integración AWS"
nav_label: "09 · Hub de Integración AWS"
tags: ["module:09", "domain:integration", "domain:aws"]
key_topics:
  - id: hub
    label: Hub
    heading: "Hub de Integración"
  - id: arquitectura
    label: Arquitectura
    heading: "Arquitectura"
  - id: conectividad
    label: Conectividad
    heading: "Conectividad"
---
# 09 Hub de Integracion AWS

## Caso de uso: Hub de Integración

- Puente S/4HANA Sistemas no-SAP (AWS)
- Version no productiva

## 💡Principio

- Diferenciar en nuestro marco metodológico, aquellos escenarios donde Datasphere no actúe como plataforma de modelado o explotación analítica, sino como un hub de integración SAP para exponer datos desde S/4HANA hacia plataformas corporativas no-SAP (como un Data Lake en AWS) que concentran el gobierno, el catálogo y el linaje empresarial.
- Posicionar Datasphere como fuente SAP autorizada, el punto de calidad mínima y el generador de metadatos coherentes para integrarse con el gobierno corporativo existente. En línea con nuestra estrategia y compatibilidad con BDC, el foco está en data products, contratos de datos, estandarización semántica y trazabilidad.
- Version no productiva

## Arquitectura de

- Referencia
- Hub de Integración | AWS
- Version no productiva
- (*1) Replication flows hacia Amazon S3 require Premium Outbound Integration en SAP Datasphere | Link
- SAP S/4 HANA
- SAP Datasphere
- AWS
- DataLake
- S3-based datalake
- Origen
- Hub de Integración
- Plataforma Corporativa
- Informatica
- Autoridad de Gobierno
- CDS / ODP / CDC
  - Fuentes autorizadas
  - Clean Core
  - Semántica SAP
- Estandarización mínima
  - Metadatos SAP
  - Publicación
- IDMC
  - Catalogo
  - Glosario + Linake
  - Calidad
- Raw/Cleansed/Curated
  - Analítica
  - Seguridad
- Red Corporativa
- Red Publica
- Tunel Seguro y Cifrado
- Movimiento de Datos
- Firewall
- Cloud Connector
- DP Agent
- Premium Outbound Integration a AWS S3
- Raw
- Datos Federados
- Datos Persistidos
- (*1)
- (*2)
  - JDBC/ODBC
- (*2) Lectura opcional vía JDBC/ODBC desde AWS (Glue/Spark/dbt)
- Escaneo nativo de metadatos
- (*3) Escaneo nativo de metadatos técnicos (S3/Glue/Redshift)
- (*3)
- Una mirada a alto nivel de la arquitectura de integración, y el camino hacia AWS
- (*4)
  - Pendiente
- (*4) Pendiente de investigación

## ✖ No respeta el principio clean-core. Las tablas base no están diseñadas como interfaz de integración Sus estructuras...

- Este enfoque puede funcionar técnicamente, así como brindar simplicidad inmediata, pero no es sostenible, ni gobernable, ni alineado al roadmap S/4HANA (future-proofing), lo que termina generando herencia técnica que luego debe corregirse en AWS o Informática.
- Version no productiva
- ✖ Linaje y Trazabilidad
  - Las tablas no traen metadatos de negocio, solo estructuras técnicas.
  - Se pierden dominios, unidades, textos de negocio, claves compuestas, relaciones, dependencias de tiempo.
  - Informatica solo puede gobernar correctamente si los datasets vienen con semántica SAP.
- ✖ Desalineación con el modelo S/4HANA
  - Extraer tablas para reproducir un “ECC clásico”
  - No se aprovecha marco Core Data Services, diseño API ni compatibilidad delta (CDC) nativa.
  - Muchas tablas requieren lógica combinada, capitalizado únicamente a través de vistas CDS.
- ✖ Compatibilidad Premium Outbound Integration
  - Los flujos de replicación (o replication flows) están pensados para CDS/ODP, no así para tablas.
  - Las tablas base obligan a usar Data Flows→ más staging, más costo, más lógica manual, menos delta real.
- ✖ Escalabiliad y Compatibilidad Roadmap BDC
  - Las tablas base no son compatibles con Data Products.
- Hub de Integración | AWS
- ¿Por qué NO recomendamos extraer tablas base?

## ✔ Interfaz certificada para S/4HANA Private Edition (RISE). SAP Garantiza estabilidad a través de CDS views, Extracto...

- Version no productiva
- ✔ Linaje y Trazabilidad
  - Semantica SAP lista para gobierno
  - Se incluyen dominios, unidades, textos de negocio, claves compuestas, relaciones, etc.
  - Informatica puede gobernar correctamente y reconstruir el linaje, catalogo, glosario de forma consistente
  - Facilita contratos de datos SAP > AWS > Informatica (granularidad, PK/FK, frecuencia, delta, reglas)
- ✔ Compatible con Premium Outbound Integration
  - Los flujos de replication (o replication flows) son compatibles entre S/4 > DSP y S3
  - Se mueven datos “SAP-like” sin necesidad de staging adicional
- ✔ Reduce el volumen a procesar en AWS
  - CDS ya filtran, normaliza y enriquece los datos.
  - Permite landing curada en Raw sin explosión de columnas y sin duplicados funcionales.
- ✔ Escalabiliad y Compatibilidad Roadmap BDC
  - Compatibilidad con Data Products.
- Este enfoque no es una recomendación genérica, sino un claro habilitador técnico para capitalizar S/4HANA, asegurar gobernanza en Informática, minimizar herencia técnica en AWS y ofrecer un marco flexible y mas fácil de mantener a futuro.
- Hub de Integración | AWS
- ¿Por qué SI recomendamos extraer fuentes autorizadas?

## Las vistas CDS proporcionan no solo la estructura de los datos, sino también una capa completa de semántica y metadat...

- CDS =
- Semántica & Metadatos
- @EndUserText.label: para dar nombres legibles y comprensibles a las columnas
  - @Semantics.amount.currencyCode: para vincular un campo con otro que contiene el código de moneda.
  - @Semantics.unitOfMeasure: para especificar unidades de medida numéricos, como kilogramos o litros.
  - @ObjectModel.modellingPattern: para especificar el patron de modelado de la vista (Consumo, Analitico, etc)
  - @ObjectModel.supportedCapabilities: para definir capacidades soportadas (Lectura, Extraccion, etc)
  - @ObjectModel.text.element: para indicar la descripción textual asociado a un campo de la misma vista.
  - @AccessControl.authorizationCheck: para integrar con las políticas de seguridad.
- Algunos de estos metadatos estructurales se generan de forma automática al definir la CDS, pero deben revisarse y homogenizarse (ej. longitudes, tipos de dato, claves). Otros, deben ser incluidos lo que refuerza que no basta con lo ”que viene de fábrica”, sino que hay que armonizarlo.
- Estos metadatos son clave para la trazabilidad, el linaje de los datos y la integridad con los procesos de negocio.
- Hub de Integración | AWS
- @ObjectModel.association: para definir las relaciones lógicas de negocio. Por ejemplo, una asociación entre un cliente y sus pedidos.
  - @ObjectModel.businessObject: para asociar la vista o entidad con un objeto de negocio. Por ejemplo, Cliente, Producto o Pedido.
  - @EndUserText.description: para describir el contexto y propósito claros de negocio de la vista o campo.
  - @Analytics.dataextraction.enabled: para habilitar la vista para ser extraída por Datasphere y herramientas externas (como AWS S3 o Informática)
  - @ObjectModel.foreignKey.association: Aporta semántica a relaciones FK. Aunque es técnico, aporta semántica de relaciones.
  - @ObjectModel.text.association: para indicar la descripción textual asociado a un campo externo (otra entidad)
  - @Semantics.date, @Semantics.quantity, @Semantics.currency, etc: ayudan a AWS e Informática a reconstruir contexto.
- Extraer tablas ABAP solo mueve datos crudos. Extraer CDS mueve datos ricos en semántica y significado de negocio
- Version no productiva

## Alcance de

- Integración
- El siguiente árbol de decisión facilita el camino a seguir entre CDS estándar vs. marco “custom” de integración.
- ¿Existe un extractor CDS estandar liberado, cuyo alcance y semantica cubra 100% la necesidad?
- SI
- NO
- Crear una CDS de integracion
  - (API Corporativa) utilizando el alcance y estandarizacion de nuestro template.
- Utilizar nuestro template CDS (acá)
- SI
- NO
- Copiar CDS / Crear una CDS de integracion
  - (cuando la estructura difiere significativamente, pero la API sigue siendo útil como base)
- Apoyarse en la CDS estándar
  - (cuando su alcance es completo y estable, pero siempre encapsulada en una CDS de Integración)
- Hub de Integración | AWS
- Ejemplo:
  - Una vista CDS basada en la tabla ABAP T024A (I_ResponsiblePlannerGroup)
- ¿La CDS cuenta con anotaciones,  estructuras y filtros compatibles con el contrato de datos?
  - Cabecera: compatible c/Datasphere>AWS
  - Semántica suficiente (metadatos de elementos)
  - Filtros: sin WHERE inesperados
- 💡Tip: Usar CDS/API liberadas como base cuando representan bien el objeto de negocio. Complementar con tabla solo para campos faltantes. Cuando la API filtre datos necesarios o se aleje del modelo requerido → usar tabla como fuente principal.
- ❌ No existe extractor apropiado
- Version no productiva
- ✅️ Ver nota SAP 3586160

## Version no productiva

- Roles CDS en
- Arquitectura de Integración
- Hub de Integración | AWS
- En escenarios SAP > Datasphere > AWS, no buscamos evitar CDS estándar ni minimizar las custom, sino apoyarnos en ellas según su propósito. Aunque muchas CDS estándar estén liberadas para extracción, su alcance responde al diseño y propósito SAP que representa, y no siempre constituyen un contrato de datos o alcance adecuado para el propósito de Data Lake corporativo.
- Rol de una CDS estándar liberada para extracción | API Funcional SAP
  - Primera opción a considerar, siempre que el caso de uso lo permita.
  - Representan un objeto de negocio o un proceso SAP (base para Fiori u otras apps).
  - Pueden incluir anotaciones de extracción (@Analytics.dataExtraction.enabled).
  - Son estables dentro del contexto funcional del objeto de negocio SAP.
  - Adecuadas para integración cuando:
- Si una CDS estándar liberada cubre la necesidad, la utilizamos como base funcional encapsulada*1 y desacoplada a través de una CDS de Integración.
- El alcance funcional cubre el 100%.
  - No incluyen filtros/joins que distorsionen volumen o semántica.
  - Su semántica es suficiente para gobierno en Data Lake/Informática.
- Rol de una CDS de integración (custom) | API Corporativa estable
  - Capa de API diseñada específicamente para el contrato SAP > DSP > AWS.
  - Encapsula la CDS estándar y define un contrato estable, explícito y versionable.
  - Aísla la integración de cambios funcionales futuros en vistas estándar.
  - Permite completar campos faltantes y homogeneizar semántica.
  - Expone solo lo necesario, con claves y reglas alineadas al Data Lake y gobierno corporativo.
  - Respeta el clean core: no afecta Fiori ni procesos estándar
- La CDS custom desacopla las integraciones corporativas del modelo SAP y evita depender de cambios funcionales en artefactos estándar.

## 💡¿Por qué encapsular & desacoplar?

- Encapsular no es duplicar; es proteger.
  - Al desacoplar ambos modelos:
  - Preservamos el clean core y evitamos modificaciones innecesarias.
  - Aislamos las integraciones de cambios funcionales en CDS estándar.
  - Aseguramos estabilidad, versionado y gobernanza del contrato de datos.
  - Reducimos riesgo, acoplamiento y herencia técnica en SAP, DSP y AWS.
- Una CDS de integración no reemplaza a la estándar:
  - la encapsula, la protege y la convierte en una API segura y estable para integraciones corporativas
- Version no productiva

## Para este caso de uso, conseguir vistas CDS estándar que cubran todos los campos de las tablas base necesarias, y al ...

- Template de Extracción via
- CDS Views
- Cabecera mínima recomendada:
- Uno u otro
- Hub de Integración | AWS
- Version no productiva
- 📌Tip! No forma parte del template, pero en casos excepcionales donde NO se quiera heredar anotaciones de las capas inferiores/API: @Metadata.ignorePropagatedAnnotations: true

## Aunque en el encabezado seamos minimalistas, en los elementos conviene capitalizarse de todas las opciones que agrega...

- Template de anotaciones
- a nivel campo
- Anotaciones a nivel “Campo” mínimas recomendadas:
- Hub de Integración | AWS
- Version no productiva
- Uno u otro

## Alcance

- Relevamiento
- En este caso de uso, el cliente se encuentra actualmente en la versión ECC NW 7.* donde las tablas en alcance podrán o no sufrir un proceso de simplificación una vez en S/4HANA, por lo que el primer paso siempre es relevar alcance y compatibilidad. Proponemos una matriz tecnica cuyo contenido se vaya enriqueciendo a través de diferentes ciclos:
- Clasificacion por alcance (actual vs. previsto)
  - Clasificacion por sistema (BPC, ECC, SRM, BW)
  - Clasificacion por tipo de objeto (Vista, Tabla)
- Incluir en la Matriz:
- Compatibilidad en S/4 (permanece, no se actualiza, obsoleto, etc.)
  - Referencia a alto nivel de reemplazo o vista de compatibilidad nativa
  - Identificar extractor estándar base (ODP-SAPI, ODP-CDS, etc)
  - Indicador “Whitelisted” (Y/N)(en base al Excel de Nota SAP 2500202)
- Incluir en la Matriz:
- Validación exhaustiva de alcance CDS
  - Alcance y compatibilidad de anotaciones
  - Alcance de campos (vs. actual)
  - Volumetría y capacidades técnicas necesarias
- Incluir en la Matriz:
- La ingesta de valores a la matriz aquí representada es en carácter representativo y referencia. Esta claro que será un proceso de mejora constante, donde conforme avanzamos con cualquier Discovery nos capitalizamos del hecho que contamos con la información para volcarla en la matriz y tener cada vez mas herramientas para la topa de decisiones técnicas y arquitectura con una mirada estratégica.
- CONCLUSIONES
- Alternativas técnicas y recomendaciones
- Hub de Integración | AWS
- Version no productiva
