---
module: "03"
title: "Integración y Estrategia de Datos"
nav_label: "03 · Integración y Estrategia de Datos"
tags: ["module:03", "domain:integration", "domain:data-strategy"]
key_topics:
  - id: integracion
    label: Integración
    heading: "Arquitectura de Integración"
  - id: extraccion
    label: Extracción
    heading: "Opciones de Extracción"
  - id: clean_core
    label: Clean Core
    heading: "✔️ Clean Core aplicado a extracción"
scope: |
  Cubre extracción e integración desde sistemas origen hacia SAP Datasphere:
  frameworks ODP y ABAP CDS, mecanismos de extracción (Full/Delta/CDC),
  estrategias de integración (federación vs persistencia vs híbrido),
  tipos de conexión (DP Agent, Cloud Connector, SDA), y clean core aplicado
  a extracción. No cubre modelado dentro de Datasphere ni configuración
  de espacios o conexiones en la UI de Datasphere.
  ODP-SAPI: extractores tradicionales de BW (ej: 0CUSTOMER_ATTR, 0MATERIAL_ATTR)
  reutilizables en Datasphere siempre que estén whitelisteados según nota SAP de
  compatibilidad. Son la API ODP para datasources BW existentes.
  ODP-CDS: vistas CDS ABAP con anotaciones @Analytics.dataExtraction habilitadas,
  enfoque moderno y clean core para integración con Datasphere vía ODP/ABAP_CDS.
  Ambos comparten el framework ODP/ODQ como mecanismo de transporte con soporte delta.
  La estrategia depende del origen y de la versión técnica: ECC vs S/4HANA,
  SAP_BASIS/SAP_ABAP, release S/4HANA (por ejemplo 2020/2021/2023) y nivel FPS
  pueden habilitar, limitar o cambiar las capacidades disponibles.
  En escenarios modernos de S/4HANA, el DP Agent debe evaluarse como patrón legacy
  cuando existen capacidades vía Cloud Connector/SCC, protocolo HTTPS y servicio
  ABAP SQL correctamente configurado; no asumir su eliminación sin validar release,
  conectividad y compatibilidad.
  Para extracción hacia Datasphere, priorizar fuentes liberadas/autorizadas: ODP-SAPI,
  ODP-CDS y CDS Views habilitadas para extracción. La extracción directa de tablas base
  NO es una recomendación Danwor: si el cliente la espera, explicar por qué compromete
  gobernanza, estabilidad, semántica, rendimiento y futuros upgrades, y proponer una
  alternativa equivalente mediante APIs/extractores liberados, CDS o patrones ODP.
guardrails:
  - "DP Agent legacy no significa obsoleto ni inválido: sigue siendo válido cuando el contexto lo requiere, pero no debe elegirse por defecto si existen capacidades nativas modernas vía SCC/HTTPS/ABAP SQL que reduzcan operación, mantenimiento y acoplamiento."
  - "Desacoplar y encapsular contratos de integración es una práctica recomendada. El anti-pattern no es desacoplar, sino consumir contratos SAP funcionales directamente sin una capa gobernada/customer-managed, o encapsular sin ownership, versionado y SLA."
  - "La extracción directa de tablas base no debe presentarse como recomendación. Si el cliente la espera, explicar por qué no conviene y ofrecer alternativas gobernadas mediante APIs/extractores liberados, CDS u ODP."
out_of_scope_redirect:
  - keywords: ["tipo semantico", "tipos semanticos", "uso semantico", "semantic type",
               "fact", "dimension", "texto dimension", "jerarquia", "capa business",
               "capa consumption", "business layer", "consumption layer",
               "modelado datasphere", "modelo analitico", "vista analitica",
               "entidad de negocio", "business entity", "data access control",
               "espacio datasphere", "space datasphere"]
    module: "04"
    label: "Modelado en SAP Datasphere"
    message: "Ese tema corresponde al módulo 04 · Modelado en SAP Datasphere, que cubre Semantic Types, capas Business/Consumption y modelado analítico."
intent_assets:
  - keywords: ["codigo CDS", "código CDS", "vista CDS", "template CDS", "ejemplo CDS",
               "crear CDS", "escribir CDS", "CDS extracción", "CDS datasphere",
               "ayuda CDS", "CDS view", "ABAP CDS extracción"]
    description: "Template CDS para extracción hacia SAP Datasphere"
    asset: "templates/cds_extractor_template.abap"
    language: "abap"
---
# 03 Integracion y Estrategia de Datos

## {dw-black}Modelo de Integración {dw-gold}Danwor
{{section_meta: keywords="API extracción, API corporativa, data product, contrato de datos"}}
{{subtitle: no-gap}}
> La integración se define por el contrato de datos, no por la tecnología.

  - Toda integración se expone como una API
  - Nunca se consumen tablas como contrato
  - Se separa API funcional SAP (CDS estándar) de API Corporativa (CDS integración)
  - El contrato de datos tiene un dueño. Ese dueño define su alcance, ciclo de vida y compatibilidad:

{{html_embed: ../assets/danwor-modelo-integracion-dataproduct.html height=650 default_mode=modelo}}

{{subtitle: no-gap}}
> Una API de integración no siempre debe resolver todo: Puede cubrir uno o más de estos roles:

  - Extracción (delta / CDC)
  - Contrato de datos
  - Enriquecimiento

{{callout: title="Contrato Propio" tone="punch" icon="🛡️"}}
La API Corporativa es el momento en que el dato pasa de pertenecer a SAP a pertenecer al cliente.
{{/callout}}

## {dw-black}Contrato de Datos vs. {dw-gold}Data Product
{{section_meta: keywords="API extracción, API corporativa, data product, contrato de datos"}}
{{subtitle: no-gap}}
> En el modelo Danwor, ambos conceptos se utilizan de forma equivalente en el contexto de consumo.

  - El contrato de datos define estructura, semantica y reglas de acceso 	**¿Qué prometemos entregar?**
  - El Data Product representa su materialización lista para ser consumida	**¿Cómo lo entregamos?**

### Data Products:
{{subtitle: no-gap}}
> El resultado del modelo de integración es la construcción de Data Products gobernados, reutilizables y orientados a negocio
{{html_embed: ../assets/danwor-modelo-integracion-dataproduct.html height=390 default_mode=dp}}

{{callout: title="Contrato Propio" tone="punch" icon="🤝"}}
En la práctica, el Data Product es el contrato de datos expuesto al negocio.
{{/callout}}

{{tree_button: Resolución de Contrato}}

## {dw-black}Arquitectura de {dw-gold}Integración
{{section_meta: keywords="arquitectura integración, arquitectura de integración, diagrama integración, componentes integración, flujo integración, cómo integra datasphere, landscape integración, visión general integración"}}
> En escenarios de origen S/4HANA (*) donde no solo se elimina el DP Agent, se desacopla el canal y el procesamiento. Para escenarios de origen ECC o cuando el DP Agent cumple un rol de integración crítico o única opción → ver slide legacy.
{{semantic_summary}}
La Arquitectura de Integración muestra cómo SAP Datasphere se conecta con sistemas origen SAP mediante capacidades modernas y legacy. Para S/4HANA moderno se prioriza el acceso desacoplado vía Cloud Connector/SCC, HTTPS y servicio ABAP SQL cuando el release y FPS lo soportan. Para escenarios ECC, S/4HANA con restricciones de versión, o casos donde el DP Agent sigue siendo requerido, se valida el patrón legacy antes de reemplazarlo. Legacy no significa obsoleto: el riesgo es elegir DP Agent por defecto cuando existen capacidades nativas que resuelven el mismo objetivo con menor operación y acoplamiento. La decisión depende de origen, release, SAP_BASIS/SAP_ABAP, conectividad, protocolo disponible, compatibilidad de extracción y necesidad de federación, replicación o persistencia. Desacoplar y encapsular contratos de integración es deseable; el anti-pattern es consumir contratos SAP funcionales directamente sin capa customer-managed o encapsular sin ownership, versionado y SLA. Las fuentes recomendadas para extracción gobernada son ODP-SAPI, ODP-CDS y CDS Views habilitadas para extracción. La extracción directa de tablas base no debe presentarse como recomendación: ante esa expectativa, explicar por qué no conviene y ofrecer alternativas gobernadas que cumplan el mismo objetivo sin comprometer escalabilidad, gobernanza ni upgrades.
{{/semantic_summary}}
{{html_embed: ../assets/danwor-arquitectura-integracion.html height=780}}

{{callout_row}}
{{callout: title="Federación via Cloud Connector" tone="punch" icon="🔗"}}
Esta capacidad requiere ABAP 2021 FPS01 o superior, donde el servicio ABAP SQL se encuentra disponible de acuerdo a la nota SAP 3691070
{{/callout}}
{{callout: title="Federación via DP Agent" tone="punch" icon="🖥️"}}
El movimiento de datos puede resolverse tanto on-premise (donde el cliente provisiona un servidor propio) como RISE (donde lo provisiona SAP si existe un acuerdo comercial/instancia en el contrato)
{{/callout}}
{{/callout_row}}

## {dw-black}Estrategia de {dw-gold}Extracción
{{section_meta: keywords="ODP, ODP-SAPI, ODP-CDS, extractores BW, extractores estándar, extractor SAP, SAPI, CDS extracción, mecanismo extracción, opciones extracción, whitelisteo, whitelist extractores, nota SAP compatibilidad, 0CUSTOMER_ATTR, 0MATERIAL_ATTR, datasource BW, BW extractor, ODP framework, ODQ, Operational Delta Queue, diferencia SAPI CDS, qué es ODP, cómo extrae ODP, ECC extracción, S4HANA extracción, BW modernización, origen datos SAP"}}

La estrategia de integración no es la misma si el origen de los datos es ECC, S/4HANA o si el proyecto en el que estamos trabajando busca modernizar un BW existente. Te ayudamos a identificar los distintos mecanismos de extracción y su implementación a través de un **árbol determinístico** de decisión guiada.
{{tree_button: Opciones de Extracción}}

### Clean Core aplicado a extracción
{{section_meta: keywords="clean core, qué es clean core, clean core sap, clean core extracción, principio clean core, clean core datasphere, apis liberadas, ODP clean core, CDS clean core, extensiones permitidas, evitar lógica SAP, exits simples, clean core integración"}}
{{section_exclude: "codigo, código, vista CDS, ejemplo CDS, crear CDS, escribir CDS, anotaciones CDS, patrón CDS, implementar CDS, template CDS, ayuda codigo, ayuda código"}}
{{support: SAP Help="https://help.sap.com/" Nota SAP="https://me.sap.com/notes/2232584"}}
Clean core no significa eliminar extractores SAP, sino apoyarse en mecanismos liberados (ODP/CDS) y mantener la lógica fuera del core.
  - APIs liberadas: ODP_SAPI, ODP_CDS
  - Extensiones: Exits simples
  - Evitar: lógica de negocio y cálculos complejos en SAP

{{callout_row}}
{{callout: title="Operational Data Provisioning" tone="punch" icon="💡"}}
ODP es un mecanismo estándar para extracción con colas delta, compatible con Datasphere de forma nativa, sin violar el principio clean core: estás consumiendo una API liberada.
{{/callout}}
{{callout: title="Principio Clean Core" tone="success" icon="🌿"}}
- Usar CDS como APIs
- Evitar lógica compleja en SAP
- Centralizar transformaciones en Datasphere
{{/callout}}
{{/callout_row}}

## {dw-black}Integración Legacy vs. {dw-gold}Moderna
{{section_meta: keywords="DP agent, cloud connector, diferencia DP agent cloud connector, DP agent vs cloud connector, qué es DP agent, qué es cloud connector, cuándo usar DP agent, cuándo usar cloud connector, configurar cloud connector, configuración cloud connector, cloud connector datasphere, cloud connector on premise, cloud connector RISE, SAP RISE cloud connector, ABAP SQL service, cloud to on premise, tipos conexión datasphere, conexión segura datasphere, ETL datasphere, tunnel SAP"}}
{{support: SAP Help DP Agent="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/f1a39d1a763e48c8872f45c110a5a4e2.html?locale=es-ES&q=DP+Agent&version=LATEST" SAP Help Cloud Connector="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/35141e7668774958b5e59495f7a7828e.html?locale=es-ES&q=DP+Agent&version=LATEST" Config Cloud Connector="https://help.sap.com/docs/SUPPORT_CONTENT/datasphere/4576253846.html?locale=en-US&version=LATEST"}}
{{semantic_summary}}
Para preguntas de configuración de Cloud Connector, no responder con un procedimiento genérico sin aclarar primero el contexto operativo. Distinguir si el escenario es Cloud Connector administrado por el cliente en su red/on-premise o conectividad bajo SAP RISE, donde ciertas tareas pueden requerir Service Request o coordinación con SAP. En SAP Datasphere, Cloud Connector/SCC actúa como túnel seguro y no procesa datos; la configuración depende del tipo de conexión, protocolo expuesto, recurso permitido en Cloud To On-Premise y capacidades del sistema fuente. Para federación moderna con S/4HANA, validar release, ABAP Platform, FPS y disponibilidad/configuración de ABAP SQL Service antes de reemplazar DP Agent. Si el usuario pide "cómo configuro Cloud Connector", apoyar la respuesta en la guía oficial "Config Cloud Connector" y advertir qué debe validar antes de ejecutar pasos.
{{/semantic_summary}}

{{subtitle: no-gap}}
> Comparación de propósito, despliegue y alcance en SAP Datasphere

| Característica | DP Agent | Cloud Connector |
|---|---|---|
| **Propósito Principal** | Proveer datos desde sistemas fuente hacia SAP Datasphere | Habilitar comunicación segura entre sistemas locales y la nube |
| **Manejo de Datos** | Extracción, transformación y carga (ETL) | No procesa datos (*), solo comunicación segura |
| **Despliegue** | Se instala en la red del sistema fuente para ejecutar integración y ETL. En SAP on RISE esto lo hace SAP vía Service Request | Se instala en la red corporativa como “túnel seguro” hacia la nube, sin procesar datos |
| **Protocolos de Conexión** | Soporta ODP, RFC, SOAP para la extracción de datos | Soporta HTTP/HTTPS (ABAP SQL Service), RFC para la comunicación segura |
| **Alcance** | Integración de datos, operaciones ETL | Seguridad y comunicación para integración entre nube y sistemas locales |

(*) acceso federado vía servicios ABAP (HTTPS) requiere S/4HANA (ABAP Platform 2021 FPS01 o superior)

## {dw-black}Federación vs. {dw-gold}Persistencia
{{section_meta: keywords="federación, persistencia, federación vs persistencia, tablas remotas, tablas locales, replicación datos, tiempo real datasphere, cuándo federar, cuándo persistir, remote tables, local tables, estrategia datos datasphere"}}

{{subtitle: no-gap}}
> Diferencias prácticas según escenario de integración: empezar con vistas federadas y complementar con persistencia según volumen y rendimiento

| Federación | Persistencia |
|---|---|
| Al crear tablas remotas en SAP Datasphere desde sistemas SAP (ECC, S/4HANA, BW, etc.) el método de acceso a la información predeterminado es a través de la federación de datos.  Es decir, que los datos se alojan en el sistema fuente, y datasphere funciona como capa de modelado y reporting en tiempo real | En contextos donde se requiera un fuerte reproceso de volúmenes grandes de datos que no impacten el rendimiento del sistema fuente, el método de acceso a la información es a través de la persistencia de datos.  Es decir, que los datos se replican desde el sistema fuente y alojan físicamente en datasphere |
| ✅ Real-time | ✅ Performance (ya no golpean al origen, corren en Datasphere) |
| ✅ No-Redundancia de datos  | ✅ Escalabilidad |
| ✅ Costos (Licenciamiento Datapshere a través de CU) | ✅ Pre-agregación y modelado (optimizados para reporting) | 
| ❌ Performance (depende de los recursos en el sistema fuente) | ❌ Redundancia |
| ❌ Escalabilidad limitada para escenarios complejos de analitica | ❌ Costos (por almacenamiento) |

{{callout: title="Híbrido" tone="punch" icon="💡"}}
Es fundamental no verlas como alternativas (una u otra), sino que coexisten y trabajen en conjunto para una mejor solución de escalabilidad, performance y flexibilidad a través del uso de método Hibrido.
{{/callout}}

## 🌟¿Qué nos funcionó hasta acá?
{{section_meta: keywords="qué funcionó, lecciones aprendidas, buenas prácticas integración, extractores disponibles, capitalizar extractores, idioma cliente, dimensión maestra, atributos textos SQL, compatibilidad extractores, extractores custom"}}

Capitalizarnos de lo que existe, apoyarnos en lo que ya conocen: no reinventar la rueda – al menos como punto de partida:
  - Capitalizarnos de exits / extractores disponibles y compatibles
  - Respetar el idioma en el que se apoya el cliente (Centro vs. Plant)
  - Integrar Dimensión Maestra (atributos + textos) via SQL Estandar

{{callout: title="Compatibilidad" tone="punch" icon="💡"}}
Para extractores estándar, tener en cuenta su compatibilidad/whitelisteo. Para los custom, considerar su tabla continue siendo compatible en S/4HANA. En cualquiera de los dos casos, tener en cuenta que data sources sin clave primaria pueden utilizarse únicamente en extracción FULL (sin compatibilidad delta nativa)
{{/callout}}

## {dw-black}Modelo de Integración {dw-gold}Híbrido
{{section_meta: keywords="modelo híbrido, integración híbrida, híbrido federación persistencia, tablas remotas locales combinadas, evolución integración, remote tables local tables, data flow replication flow, cuándo usar híbrido"}}

{{subtitle: no-gap}}
> En la práctica, se puede comenzar con tablas remotas y, según necesidades de rendimiento, evolucionar hacia persistencia mediante replication/data flows.

| Aspecto | Tablas Remotas | Tablas Locales |
|---|---|---|
| **Definición** | Tablas virtuales que acceden a datos en tiempo real desde sistemas origen (Ej.: CDS Views). | Tablas persistidas en Datasphere mediante flujos de datos o replicación. |
| **Propósito** | Evitar replicar datos innecesarios y mantenerlos en el sistema origen para garantizar consistencia. | Centralizar datos para un acceso más rápido y personalizado en Datasphere. |
| **Casos de Uso Típicos** | Acceso en tiempo real. Datos cambiantes. Evitar costos de replicacion | Análisis de alto volumen. Combinar múltiples fuentes. Disponibilidad independiente del origen. |
| **Ventajas** | Datos siempre actualizados. Reducción de costos de almacenamiento. | Rendimiento optimizado para análisis. Menor latencia en consultas. Personalización de datos según los modelos analíticos |
| **Limitaciones** | Dependencia del sistema origen (latencia, disponibilidad). Limitaciones en las transformaciones y uniones. Consultas complejas pueden ser lentas. | Almacenamiento (Licenciamiento/Capacity Units). Necesidad de sincronización y monitoreo. Potencial consistencia si los datos cambian rápidamente. |

## {dw-black}Federación, Persistencia o {dw-gold}Híbrido?
{{section_meta: keywords="federación persistencia híbrido, qué estrategia elegir, cómo decidir integración, árbol integración, estrategia integración datasphere, decidir federación o persistencia"}}

Te ayudamos a identificar el enfoque de Integración en SAP Datasphere a través de un **árbol determinístico** de decisión guiada.
{{tree_button: Estrategia de Integración (federación vs persistencia)}}

## {dw-black}Tipos de {dw-gold}Conexión
{{section_meta: keywords="tipos de conexiones, qué conexión usar, conexión datasphere, DP agent cloud connector SDA, SQL service RFC, conexión SAP datasphere, cómo conectar datasphere, árbol conexiones"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/94562426f30c475286f50a1e2b45e743.html?locale=en-US"}}
El siguiente **árbol determinístico** de decisión guiada puede servir como soporte para distintos casos de uso y contexto técnico en cuanto al tipo de conexión a utilizar, considerando sus compatibilidades y limitaciones de acuerdo al 🔗<link url="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/94562426f30c475286f50a1e2b45e743.html?locale=en-US">SAP Help</link>

## Referencia técnica: CDS Views para extracción hacia Datasphere
{{section_meta: keywords="CDS, vista CDS, CDS view, CDS DDIC, DDIC-based CDS, CDS view entity, define view entity, define view, diferencia DDIC entity, DDIC vs entity, código CDS, crear CDS, escribir CDS, ayuda CDS, ejemplo CDS, CDS extracción, CDS datasphere, CDS S4HANA, ABAP CDS, anotaciones CDS, @Analytics, dataExtraction, vista extracción, cómo crear CDS, patrón CDS, referencia CDS, CDS FULL, CDS delta, CDC CDS, CDS change data capture, CDS timestamp, ODP CDS, CDS ODP extracción, CDS para datasphere, CDS hacia datasphere, estructura CDS, código extracción SAP, vista ABAP extracción"}}
Las vistas CDS habilitadas para extracción son la API estándar clean core para integrar S/4HANA con SAP Datasphere vía ODP/ABAP_CDS. A continuación, los patrones de referencia.

### DDIC-based CDS vs. CDS view entity para ODP

Existen dos generaciones de CDS ABAP: las CDS DDIC-based, definidas con `define view` y `@AbapCatalog.sqlViewName`, y las CDS view entity, definidas con `define view entity`. Ambas comparten muchas anotaciones, pero en escenarios ODP/ABAP_CDS la compatibilidad productiva no debe asumirse como equivalente.

Postura Danwor: para extractores ODP-CDS orientados a Datasphere, usar CDS DDIC-based como patrón conservador por compatibilidad amplia y porque muchas CDS estándar liberadas para extracción siguen ese modelo. Usar `define view entity` solo si el release ABAP/SAP, la nota aplicable y el mecanismo de consumo confirman soporte para ese escenario.

### Anotaciones mínimas requeridas

| Anotación | Valor recomendado | Propósito |
|---|---|---|
| `@Analytics.dataExtraction.enabled` | `true` | Habilita la vista para extracción vía ODP |
| `@Analytics.dataCategory` | `#FACT` / `#DIMENSION` | Clasifica el tipo de objeto analítico |
| `@AccessControl.authorizationCheck` | `#NOT_REQUIRED` | Estándar para vistas de extracción |
| `@EndUserText.label` | Texto descriptivo | Descripción visible en catálogo |

**No usar** `@OData.publish: true` para integración con Datasphere — esa anotación es para servicios OData, no para extracción vía ABAP_CDS/ODP.

### Patrón 1 — Extracción FULL

```abap
@EndUserText.label: 'Ventas para Datasphere - Full'
@AbapCatalog.sqlViewName: 'ZDSVTAFULL'
@AccessControl.authorizationCheck: #NOT_REQUIRED
@Analytics.dataCategory: #FACT
@Analytics.dataExtraction.enabled: true

define view ZI_DS_VENTAS
  as select from zsales_hdr as H
{
  key H.mandt          as Client,
  key H.sales_doc      as SalesDocument,
      H.sales_org      as SalesOrg,
      H.customer       as Customer,
      H.doc_date       as DocumentDate,
      H.net_amount     as NetAmount,
      H.currency       as Currency
}
```

### Patrón 2 — Extracción FULL + Delta por campo timestamp

Requiere un campo `last_changed_at` confiable que se actualice en cada modificación del registro.

```abap
@EndUserText.label: 'Ventas para Datasphere - Delta'
@AbapCatalog.sqlViewName: 'ZDSVTADELTA'
@AccessControl.authorizationCheck: #NOT_REQUIRED
@Analytics.dataCategory: #FACT
@Analytics.dataExtraction.enabled: true
@Analytics.dataExtraction.delta.byElement.name: 'LastChangedAt'
@Analytics.dataExtraction.delta.byElement.maxDelayInSeconds: 60
@Analytics.dataExtraction.delta.byElement.detectDeletedRecords: false

define view ZI_DS_VENTAS_DELTA
  as select from zsales_hdr as H
{
  key H.mandt          as Client,
  key H.sales_doc      as SalesDocument,
      H.sales_org      as SalesOrg,
      H.customer       as Customer,
      H.net_amount     as NetAmount,
      H.currency       as Currency,

      @Semantics.systemDateTime.lastChangedAt: true
      H.changed_at     as LastChangedAt
}
```

### Patrón 3 — CDC con join (Change Data Capture)

Para vistas con joins, el delta se configura con mapeo explícito de tablas base.

```abap
@EndUserText.label: 'Ventas + Cliente para Datasphere - CDC'
@AbapCatalog.sqlViewName: 'ZDSVTACDC'
@AccessControl.authorizationCheck: #NOT_REQUIRED
@Analytics.dataCategory: #FACT
@Analytics.dataExtraction.enabled: true
@Analytics.dataExtraction.delta.changeDataCapture.mapping: [
  {
    table        : 'ZSALES_HDR',
    role         : #MAIN,
    viewElement  : [ 'Client', 'SalesDocument' ],
    tableElement : [ 'MANDT', 'SALES_DOC' ]
  }
]

define view ZI_DS_VENTAS_CDC
  as select from zsales_hdr as H
    left outer join zcustomer as C
      on  C.mandt = H.mandt
      and C.kunnr = H.customer
{
  key H.mandt          as Client,
  key H.sales_doc      as SalesDocument,
      H.customer       as Customer,
      C.name1          as CustomerName,
      H.net_amount     as NetAmount,
      H.changed_at     as LastChangedAt
}
```

### Cuándo usar cada patrón

| Escenario | Patrón recomendado |
|---|---|
| Tabla sin campo de cambio confiable | Full únicamente |
| Tabla con `changed_at` o `last_changed_at` | Delta byElement |
| Vista con joins entre tablas | CDC con mapping |
| Volumen bajo, sin requisito de incremental | Full |

{{subtitle: no-gap}}
> La elección entre Full y Delta impacta directamente en la estrategia de replicación en Datasphere (Replication Flow vs Data Flow) y en la capacidad de mantener datos actualizados sin recargas completas.
