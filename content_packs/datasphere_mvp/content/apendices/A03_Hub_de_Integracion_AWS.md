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

## Principio
{{subtitle: no-gap}}
> Diferenciar en nuestro marco metodológico, aquellos escenarios donde Datasphere no actúe como plataforma de modelado o explotación analítica, sino como un hub de integración SAP para exponer datos desde S/4HANA hacia plataformas corporativas no-SAP (como un Data Lake en AWS) que concentran el gobierno, el catálogo y el linaje empresarial.

{{callout: title="Caso de uso" tone="punch" icon="💡"}}
Posicionar Datasphere como fuente SAP autorizada, el punto de calidad mínima y el generador de metadatos coherentes para integrarse con el gobierno corporativo existente. En línea con nuestra estrategia y compatibilidad con BDC, el foco está en data products, contratos de datos, estandarización semántica y trazabilidad.
{{/callout}}

## {dw-black}Arquitectura de {dw-gold}Referencia
{{subtitle: no-gap}}
> Una mirada a alto nivel de la arquitectura de integración, y el camino hacia AWS
{{html_embed: ../../assets/apendices/danwor-arquitectura-referencia-aws.html height=980}}

## ¿Por qué {dw-red}NO {dw-black}recomendamos extraer tablas base? 

- ✖ No respeta el principio clean-core
  - Las tablas base no están diseñadas como interfaz de integración
  - Sus estructuras pueden cambiar entre releases, feature pack stack, upgrades.
  - Consumirlas directamente compromete upgreadability, requisito critico de RISE

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

{{callout: title="¿Por qué importa?" tone="punch" icon="💡"}}
Este enfoque puede funcionar técnicamente, así como brindar simplicidad inmediata, pero no es sostenible, ni gobernable, ni alineado al roadmap S/4HANA (future-proofing), lo que termina generando herencia técnica que luego debe corregirse en AWS o Informática.
{{/callout}}

## ¿Por qué {dw-green}SI {dw-black}recomendamos extraer fuentes autorizadas? 

- ✔ Interfaz certificada para S/4HANA Private Edition (RISE).
  - SAP Garantiza estabilidad a través de CDS views, Extractores ODP y CDC nativo.
  - Fuentes autorizadas con compatibilidad ante upgrades.

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

{{callout: title="¿Por qué importa?" tone="punch" icon="💡"}}
Este enfoque no es una recomendación genérica, sino un claro habilitador técnico para capitalizar S/4HANA, asegurar gobernanza en Informática, minimizar herencia técnica en AWS y ofrecer un marco flexible y mas fácil de mantener a futuro.
{{/callout}}

## {dw-black}Vistas CDS = {dw-gold}Semántica & Metadatos
{{subtitle: no-gap}}
> Las vistas CDS proporcionan no solo la estructura de los datos, sino también una capa completa de semántica y metadatos completos, mejorando la trazabilidad, el linaje y el gobierno de datos. A través de las anotaciones, se describe el significa de los datos, sus relaciones y su contexto de negocio. 

- **Metadatos Técnicos (Estructurales):**
  - Estructura técnica: nombre del campo, tipo de dato, longitud, clave primaria/foránea, etc.
  - Ejemplos de anotaciones:
    - @EndUserText.label: para dar nombres legibles y comprensibles a las columnas
    - @Semantics.amount.currencyCode: para vincular un campo con otro que contiene el código de moneda.
    - @Semantics.unitOfMeasure: para especificar unidades de medida numéricos, como kilogramos o litros.
    - @ObjectModel.modellingPattern: para especificar el patron de modelado de la vista (Consumo, Analitico, etc)
    - @ObjectModel.supportedCapabilities: para definir capacidades soportadas (Lectura, Extraccion, etc)
    - @ObjectModel.text.element: para indicar la descripción textual asociado a un campo de la misma vista.
    - @AccessControl.authorizationCheck: para integrar con las políticas de seguridad.

> Algunos de estos metadatos estructurales se generan de forma automática al definir la CDS, pero deben revisarse y homogenizarse (ej. longitudes, tipos de dato, claves). Otros, deben ser incluidos lo que refuerza que no basta con lo ”que viene de fábrica”, sino que hay que armonizarlo.

- **Metadatos Semánticos (Lógicos):**
  - Centrados en la descripción lógica de los objetos, sus relaciones de negocio, contexto de interpretación
  - Ejemplos de anotaciones:
    - @ObjectModel.association: para definir las relaciones lógicas de negocio. Por ejemplo, una asociación entre un cliente y sus pedidos.
    - @ObjectModel.businessObject: para asociar la vista o entidad con un objeto de negocio. Por ejemplo, Cliente, Producto o Pedido.
    - @EndUserText.description: para describir el contexto y propósito claros de negocio de la vista o campo.
    - @Analytics.dataextraction.enabled: para habilitar la vista para ser extraída por Datasphere y herramientas externas (como AWS S3 o Informática)
    - @ObjectModel.foreignKey.association: Aporta semántica a relaciones FK. Aunque es técnico, aporta semántica de relaciones.
    - @ObjectModel.text.association: para indicar la descripción textual asociado a un campo externo (otra entidad)
    - @Semantics.date, @Semantics.quantity, @Semantics.currency, etc: ayudan a AWS e Informática a reconstruir contexto.

> Estos metadatos son clave para la trazabilidad, el linaje de los datos y la integridad con los procesos de negocio.

{{callout: title="¿Por qué importa?" tone="punch" icon="💡"}}
Extraer tablas ABAP solo mueve datos crudos. Extraer CDS mueve datos ricos en semántica y significado de negocio
{{/callout}}

## {dw-black}Alcance de {dw-gold}Integración
{{support: SAP Help CDS para Extraccion="https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/870d66c6bfc44d6c9f128c888f0c7957.html?utm_source=chatgpt.com&locale=en-US&version=LATEST" SAP Help Catalogo de Vistas CDS="https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/ee6ff9b281d8448f96b4fe6c89f2bdc8/5418de55938d1d22e10000000a44147b.html?locale=en-US&version=LATEST" Nota SAP Change Data Capture="https://me.sap.com/notes/3586160"}}

En el caso de uso de Datasphere como Hub corporativo o Data contract hub, la estrategia de integración y opciones de extracción son práctivamente las mismas a las ya cubiertas en el modulo de Integración y Estrategia de datos sólo que 100% enfocadas en asegurar y garantizar sin excepción contratos de datos estables ricos en semántica y metadatos que puedan utilizarse como base sólida. Te ayudamos a identificar los distintos mecanismos a través de un **árbol determinístico** de decisión guiada.
{{tree_button: id=extraction_options label="Opciones de Extracción"}}

## {dw-black}Roles CDS en {dw-gold}Arquitectura de Integración
{{subtitle: no-gap}}
> En escenarios SAP > Datasphere > AWS, no buscamos evitar CDS estándar ni minimizar las custom, sino apoyarnos en ellas según su propósito. Aunque muchas CDS estándar estén liberadas para extracción, su alcance responde al diseño y propósito SAP que representa, y no siempre constituyen un contrato de datos o alcance adecuado para el propósito de Data Lake corporativo.

- **Rol de una CDS estándar liberada para extracción | API Funcional SAP**
  - Primera opción a considerar, siempre que el caso de uso lo permita.
  - Representan un objeto de negocio o un proceso SAP (base para Fiori u otras apps).
  - Pueden incluir anotaciones de extracción (@Analytics.dataExtraction.enabled).
  - Son estables dentro del contexto funcional del objeto de negocio SAP.
  - Adecuadas para integración cuando:
    - El alcance funcional cubre el 100%.
    - No incluyen filtros/joins que distorsionen volumen o semántica.
    - Su semántica es suficiente para gobierno en Data Lake/Informática.

  >Si una CDS estándar liberada cubre la necesidad, la utilizamos como base funcional encapsulada*1 y desacoplada a través de una CDS de Integración.

- **Rol de una CDS de integración (custom) | API Corporativa estable**
  - Capa de API diseñada específicamente para el contrato SAP > DSP > AWS.
  - Encapsula la CDS estándar y define un contrato estable, explícito y versionable.
  - Aísla la integración de cambios funcionales futuros en vistas estándar.
  - Permite completar campos faltantes y homogeneizar semántica.
  - Expone solo lo necesario, con claves y reglas alineadas al Data Lake y gobierno corporativo.
  - Respeta el clean core: no afecta Fiori ni procesos estándar

  >La CDS custom desacopla las integraciones corporativas del modelo SAP y evita depender de cambios funcionales en artefactos estándar.

{{callout: title="¿Por qué encapsular & desacoplar?" tone="punch" icon="💡"}}
Encapsular no es duplicar; es proteger. Al desacoplar ambos modelos:
  - Preservamos el clean core y evitamos modificaciones innecesarias.
  - Aislamos las integraciones de cambios funcionales en CDS estándar.
  - Aseguramos estabilidad, versionado y gobernanza del contrato de datos.
  - Reducimos riesgo, acoplamiento y herencia técnica en SAP, DSP y AWS.

Una CDS de integración no reemplaza a la estándar: la encapsula, la protege y la convierte en una API segura y estable para integraciones corporativas
{{/callout}}


