---
module: "03"
title: "Integración y Estrategia"
nav_label: "03 · Integración y Estrategia de Datos"
tags: ["module:03", "domain:integration", "domain:data-strategy"]
key_topics:
  - id: data_contract
    label: Contratos
    heading: "Contrato de Datos vs. Data Product"
  - id: integracion
    label: Integración
    heading: "Arquitectura de Integración"
  - id: extraccion
    label: Extracción
    heading: "Estrategia de Extracción"
  - id: cds_views
    label: Vistas CDS
    heading: "Tipos de Vistas CDS"
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
  - "Extracción CDS/ODP: CDS DDIC-based es el baseline productivo por compatibilidad amplia con el framework ODP. La incrementalidad avanzada (CDC con mapping) requiere validar SAP_BASIS >= 7.56 y Nota SAP 3534086 antes de prometerla en Replication Flows; si no se cumple, usar delta byElement con timestamp confiable o full según criticidad, volumen y capacidad de recarga."
  - "Cloud Connector: no dar un procedimiento genérico sin aclarar primero el contexto operativo. Distinguir Cloud Connector administrado por el cliente vs conectividad bajo SAP RISE (que puede requerir Service Request o coordinación con SAP). SCC es túnel seguro y no procesa datos; para federación moderna con S/4HANA validar release, ABAP Platform, FPS y ABAP SQL Service antes de reemplazar DP Agent, y apoyarse en la guía oficial de configuración."
quick_action_guidance:
  "Guía operativa del módulo":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 03:
      - La guía operativa debe tratar integración y extracción como decisión arquitectónica de datos, no como lista genérica de conectores.
      - La decisión dominante debe partir de origen, versión/release, SAP_BASIS/SAP_ABAP, FPS, modalidad RISE/on-prem, conectividad disponible y fuente autorizada de extracción.
      - Para S/4HANA moderno, priorizar ODP-CDS / ABAP_CDS con CDS DDIC-based como patrón conservador para extracción hacia Datasphere, porque conserva compatibilidad productiva con ODP y muchas APIs CDS liberadas por SAP siguen este modelo.
      - Mencionar que CDS View Entity es una alternativa más moderna, pero no asumirla como default para ODP/CDS de extracción sin validar release, notas SAP y compatibilidad. Referenciar la sección "Tipos de Vistas CDS" cuando aparezca esta decisión.
      - No reducir DP Agent vs Cloud Connector a "nuevo vs obsoleto": DP Agent es legacy válido si el contexto lo exige; Cloud Connector/SCC + HTTPS + ABAP SQL Service puede reducir operación y acoplamiento si el landscape lo soporta. Referenciar la sección "Integración Legacy vs. Moderna".
      - Incluir readiness técnico: Note Analyzer / CNV_NA_DI, estado de notas SAP, usuario/servicio de integración y compatibilidad ABAP -> Datasphere antes de prometer Replication Flows o conectividad productiva.
      - Para delta, no limitarse a timestamp: diferenciar delta byElement vs CDC con mapping. CDC avanzado depende de release/SAP_BASIS, tipo de CDS y Nota SAP 3534086. Referenciar la sección "Template de Extracción vía API Corporativa".
      - Mantener como guardrail fuerte que la extracción directa de tablas base no es recomendación Danwor; si aparece, ofrecer alternativa gobernada mediante ODP-SAPI, ODP-CDS, CDS Views liberadas o API corporativa.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 03 - GUÍA OPERATIVA:
      La decisión dominante debe formularse como: elegir el mecanismo de integración/extracción autorizado según origen, release y compatibilidad técnica, priorizando ODP-CDS/CDS DDIC-based como patrón conservador para APIs de extracción hacia Datasphere cuando aplique.

      Incluí obligatoriamente:
      - Validación de origen y versión: ECC, S/4HANA, SAP_BASIS/SAP_ABAP, release/FPS y modalidad RISE/on-prem.
      - Referencia conceptual a "Tipos de Vistas CDS" para explicar DDIC-based vs CDS View Entity y por qué DDIC-based sigue siendo conservador para ODP/CDS de extracción.
      - Validación DP Agent vs Cloud Connector/SCC con ABAP SQL Service, sin llamar obsoleto al DP Agent.
      - Readiness de integración: Note Analyzer / CNV_NA_DI, estado de notas SAP y usuario/servicio de integración.
      - Delta: timestamp/byElement solo como patrón clásico; CDC con mapping como patrón avanzado sujeto a compatibilidad y Nota SAP 3534086.
      - Rechazo explícito de extracción directa de tablas base como recomendación.

      La recomendación Danwor final debe incluir esta idea explícitamente:
      "validando siempre la compatibilidad técnica, estado de Note Analyzer (CNV_NA_DI) y evitando la extracción directa de tablas base."

      Evitá una guía liviana centrada solo en "usar ODP-CDS". La salida debe sonar como criterio operativo de arquitectura de integración.
  "Checklist de implementación":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 03:
      - El checklist debe confirmar readiness técnico de integración/extracción antes de construir o activar flujos.
      - Debe cubrir origen, release, SAP_BASIS/SAP_ABAP, FPS, modalidad RISE/on-prem y conectividad.
      - Debe validar fuentes autorizadas: ODP-SAPI, ODP-CDS, CDS Views habilitadas para extracción y API corporativa.
      - Debe incluir Note Analyzer / CNV_NA_DI, notas SAP aplicables y usuario/servicio de integración.
      - Debe distinguir DP Agent vs Cloud Connector/SCC + HTTPS + ABAP SQL Service sin tratar DP Agent como obsoleto.
      - Debe incluir delta clásico byElement y CDC con mapping como alternativas sujetas a compatibilidad.
      - Debe rechazar extracción directa de tablas base como práctica recomendada.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 03 - CHECKLIST DE IMPLEMENTACIÓN:
      Construí un checklist operativo de integración/extracción hacia SAP Datasphere.

      Debe validar:
      - Origen: ECC, S/4HANA, BW o no-SAP.
      - Release/FPS y componentes técnicos SAP_BASIS/SAP_ABAP.
      - Modalidad RISE/on-prem y restricciones de conectividad.
      - Fuente autorizada: ODP-SAPI, ODP-CDS, CDS View liberada o API corporativa.
      - Whitelist/notas SAP aplicables.
      - Note Analyzer / CNV_NA_DI y estado de notas.
      - Usuario/servicio de integración.
      - Cloud Connector/SCC, ABAP SQL Service y protocolo HTTPS cuando aplique.
      - DP Agent solo si el contexto lo requiere.
      - Estrategia delta: FULL, byElement o CDC con mapping.
      - Pruebas de ODQ, Replication Flow/Data Flow y monitoreo.
      - Ownership, versionado y SLA del contrato/API corporativa.

      Si aparece expectativa de extracción directa de tablas base, convertirla en punto de bloqueo y proponer alternativa gobernada.
  "Errores frecuentes":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 03:
      - Los errores deben centrarse en decisiones de integración/extracción, no en modelado ni reporting.
      - Evitar lenguaje alarmista: DP Agent es legacy, no obsoleto.
      - Tratar la extracción directa de tablas base como anti-pattern fuerte.
      - Incluir errores de compatibilidad de release, Note Analyzer, usuario de servicio, notas SAP y delta/CDC.
      - Tratar desacoplamiento/encapsulación como buena práctica; el error es consumir contratos funcionales SAP sin contrato propio, ownership o versionado.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 03 - ERRORES FRECUENTES:
      Priorizá estos errores:
      - Extraer tablas base directamente en lugar de fuentes autorizadas.
      - Asumir CDS View Entity como default para ODP/CDS de extracción sin validar compatibilidad.
      - No validar release, SAP_BASIS/SAP_ABAP, FPS o notas SAP.
      - No ejecutar o no resolver Note Analyzer / CNV_NA_DI.
      - Llamar obsoleto al DP Agent o elegirlo por defecto sin evaluar SCC/HTTPS/ABAP SQL.
      - Configurar Cloud Connector sin ABAP SQL Service, usuario de integración o permisos adecuados.
      - Prometer delta avanzado/CDC mapping sin validar compatibilidad técnica y Nota SAP 3534086.
      - Consumir contratos SAP funcionales directamente sin API corporativa/customer-managed cuando el caso requiere encapsulación.
      - Elegir federación o persistencia sin evaluar volumen, latencia, SLA y costo operativo.

      Para cada error, explicá causa raíz, impacto y corrección Danwor.
  "Ejemplos prácticos":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 03:
      - Los ejemplos deben salir de las secciones del módulo: arquitectura de integración, estrategia de extracción, integración legacy vs moderna, federación vs persistencia y templates CDS/API corporativa.
      - No inventar casos si el módulo ya ofrece escenarios suficientes.
      - Usar apéndices o biblioteca solo como complemento, no como sustituto del criterio del módulo.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 03 - EJEMPLOS PRÁCTICOS:
      Construí ejemplos concretos de integración/extracción:
      - S/4HANA moderno: ODP-CDS/CDS DDIC-based como API de extracción conservadora, con validación de release y notas.
      - ECC/BW: reutilización ODP-SAPI/ODQ cuando el extractor está liberado/whitelisteado.
      - DP Agent vs Cloud Connector/SCC: cuándo mantener patrón legacy y cuándo evolucionar a HTTPS/ABAP SQL.
      - API corporativa CDS: cuándo encapsular una fuente funcional SAP en contrato propio.
      - Federación vs persistencia: cuándo priorizar acceso remoto y cuándo materializar.

      Para cada ejemplo, indicá fuente interna usada, decisión Danwor, por qué esa y no otra, y riesgo si se elige mal.
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
               "ayuda CDS", "CDS view", "ABAP CDS extracción", "DDIC CDS",
               "define view", "sqlViewName", "ODP CDS DDIC"]
    description: "Template CDS DDIC-based para extracción hacia SAP Datasphere"
    asset: "https://danwor.sharepoint.com/:t:/s/PrcticaAnalytics/IQCpgsn98NNWQLd3WDj4SsNkAWEQMUwcLRS0EtKL6Y1oiew?e=n2OdL4"
    language: "abap"
  - keywords: ["codigo CDS", "código CDS", "vista CDS", "template CDS", "ejemplo CDS",
               "crear CDS", "escribir CDS", "CDS view entity", "view entity",
               "define view entity", "CDS entity", "template CDS entity"]
    description: "Template CDS View Entity-based para extracción hacia SAP Datasphere"
    asset: "https://danwor.sharepoint.com/:t:/s/PrcticaAnalytics/IQBVd62iU6Q5RYWiHrFkDxuPAbAfk-hkIFCeAoBmeGUC7Vc?e=cPFjN7"
    language: "abap"
---

# 03 Integracion y Estrategia

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

  - El contrato de datos define estructura, semantica y reglas de acceso. Responde a **¿Qué prometemos entregar?**
  - El Data Product representa su materialización lista para ser consumida. Responde a **¿Cómo lo entregamos?**

### Data Products:
{{subtitle: no-gap}}
> El resultado del modelo de integración es la construcción de Data Products gobernados, reutilizables y orientados a negocio
{{html_embed: ../assets/danwor-modelo-integracion-dataproduct.html height=420 default_mode=dp}}

{{callout: title="Contrato Propio" tone="punch" icon="🤝"}}
En la práctica, el Data Product es el contrato de datos expuesto al negocio.
{{/callout}}

{{tree_button: Resolución de Contrato}}

## {dw-black}Arquitectura de {dw-gold}Integración
{{section_meta: keywords="arquitectura integración, arquitectura de integración, diagrama integración, componentes integración, flujo integración, cómo integra datasphere, landscape integración, visión general integración"}}
{{semantic_summary}}
La Arquitectura de Integración muestra cómo SAP Datasphere se conecta con sistemas origen SAP mediante capacidades modernas y legacy. Para S/4HANA moderno se prioriza el acceso desacoplado vía Cloud Connector/SCC, HTTPS y servicio ABAP SQL cuando el release y FPS lo soportan. Para escenarios ECC, S/4HANA con restricciones de versión, o casos donde el DP Agent sigue siendo requerido, se valida el patrón legacy antes de reemplazarlo. Legacy no significa obsoleto: el riesgo es elegir DP Agent por defecto cuando existen capacidades nativas que resuelven el mismo objetivo con menor operación y acoplamiento. La decisión depende de origen, release, SAP_BASIS/SAP_ABAP, conectividad, protocolo disponible, compatibilidad de extracción y necesidad de federación, replicación o persistencia. Desacoplar y encapsular contratos de integración es deseable; el anti-pattern es consumir contratos SAP funcionales directamente sin capa customer-managed o encapsular sin ownership, versionado y SLA. Las fuentes recomendadas para extracción gobernada son ODP-SAPI, ODP-CDS y CDS Views habilitadas para extracción. La extracción directa de tablas base no debe presentarse como recomendación: ante esa expectativa, explicar por qué no conviene y ofrecer alternativas gobernadas que cumplan el mismo objetivo sin comprometer escalabilidad, gobernanza ni upgrades.
{{/semantic_summary}}

{{support: SAP Help DP Agent="https://help.sap.com/docs/SUPPORT_CONTENT/datasphere/4576253840.html?locale=en-US&version=LATEST" SAP Help Cloud Connector="https://help.sap.com/docs/SUPPORT_CONTENT/datasphere/4576253846.html?locale=en-US&version=LATEST"}}

> En escenarios de origen S/4HANA (*) donde no solo se elimina el DP Agent, se desacopla el canal y el procesamiento. Para escenarios de origen ECC o cuando el DP Agent cumple un rol de integración crítico o única opción → ver slide legacy.

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

## {dw-black}Tipos de {dw-gold}Vistas CDS
{{section_meta: keywords="tipos vistas CDS, vista CDS estándar, vista CDS custom, API Funcional SAP, wrapper, core-data services, core data services, CDS Views, DDL, DDIC, CDS, vista CDS, CDS view, CDS DDIC, DDIC-based CDS, CDS view entity, define view entity, define view, diferencia DDIC entity, DDIC vs entity, código CDS, crear CDS, escribir CDS, ayuda CDS, ejemplo CDS, CDS extracción, CDS datasphere, CDS S4HANA, ABAP CDS, anotaciones CDS, @Analytics, dataExtraction, vista extracción, cómo crear CDS, patrón CDS, referencia CDS, CDS FULL, CDS delta, CDC CDS, CDS change data capture, CDS timestamp, ODP CDS, CDS ODP extracción, CDS para datasphere, CDS hacia datasphere, estructura CDS, código extracción SAP, vista ABAP extracción"}}

{{support: SAP Help CDS Entity="https://help.sap.com/docs/ABAP_PLATFORM_NEW/f2e545608079437ab165c105649b89db/7c078765ec6d4e6b88b71bdaf8a2bd9f.html?locale=en-US&version=LATEST" SAP Help CDS DDIC-based="https://help.sap.com/docs/ABAP_PLATFORM_NEW/f2e545608079437ab165c105649b89db/7c078765ec6d4e6b88b71bdaf8a2bd9f.html?locale=en-US&version=LATEST#loioe3a611dc5f25472481a9cf4ad5066ba4" CDS para Extraccion="https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/870d66c6bfc44d6c9f128c888f0c7957.html?utm_source=chatgpt.com&locale=en-US&version=LATEST"}}

{{subtitle: no-gap}}
>Desde ABAP 7.55 existen dos generaciones de vistas CDS, y si bien es la evolución natural de SAP (modernización por sobre legado), hay consideraciones importantes en nuestro contexto metodológico de cara a la extracción y framework ODP por los cuales inclusive SAP continúa apoyándose en CDS DDIC-based para sus propias API de extracción liberadas.

| Aspecto | CDS View Entity<br>(DDL Moderno) | CDS DDIC-based View<br>(DDIC Clásico) |
|---|---|---|
| **Naturaleza** | Modelo moderno ABAP CDS | Modelo clásico basado en DDIC |
| **SQL View** | ❌ No requiere | ✅ Requiere (`@AbapCatalog.sqlViewName`) |
| **Integración ABAP** | Alta (RAP, OData, Fiori) | Alta (frameworks legacy/estables) |
| **Capacidad DDL** |Avanzadas | Estándar |
| **Dependencia DDIC** | Baja | Alta |
| **Uso típico SAP** | Desarrollo moderno | Extracción / integración |
| **Compatibilidad** | Depende del release | Amplia entre releases |

{{subtitle: no-gap}}
>El tipo de vista no es una decisión de modernidad, sino de propósito. Elegir mal tiene consecuencias en compatibilidad ODP diseño SAP.

| Propósito Técnico | Tipo de Vista CDS |
|---|---|
| RAP/OData/Fiori | View Entity |
| Virtual Data Model (VDM) | View Entity |
| Integración Técnica | DDIC-based |
| Extracción de Datos | DDIC-based |

{{callout: title="Señal Arquitectónica" tone="punch" icon="💡"}}
SAP no migró sus propias APIs de extracción a View Entity. Ni omisión, ni olvido: sino una decisión técnica deliberada.
{{/callout}}

{{subtitle: no-gap}}
> Postura Danwor: para extractores ODP-CDS orientados a Datasphere, usar CDS DDIC-based como patrón conservador por compatibilidad amplia y porque muchas CDS estándar liberadas para extracción siguen ese modelo. Usar `define view entity` solo si el release ABAP/SAP, la nota aplicable y el mecanismo de consumo confirman soporte para ese escenario.

## {dw-black}Template de Extracción vía {dw-gold}API Corporativa
{{section_meta: keywords="template extracción, template CDS, API corporativa, CDS extracción, anotaciones extracción, @Analytics.dataExtraction, delta byElement, change data capture, CDC mapping, SAP_BASIS 7.56, nota SAP 3534086, compatibilidad CDC, delta avanzado, extractor CDS, ABAP CDS Datasphere"}}
{{support: Template CDS DDIC="https://danwor.sharepoint.com/:t:/s/PrcticaAnalytics/IQCpgsn98NNWQLd3WDj4SsNkAWEQMUwcLRS0EtKL6Y1oiew?e=n2OdL4" Template CDS View Entity="https://danwor.sharepoint.com/:t:/s/PrcticaAnalytics/IQBVd62iU6Q5RYWiHrFkDxuPAbAfk-hkIFCeAoBmeGUC7Vc?e=cPFjN7" SAP Help CDS Annotations="https://help.sap.com/docs/ABAP_PLATFORM_BW4HANA/cc0c305d2fab47bd808adcad3ca7ee9d/630ce9b386b84e80bfade96779fbaeec.html?locale=en-US&version=LATEST" Nota SAP="https://me.sap.com/notes/3534086"}}
{{subtitle: no-gap}}
> Una vez identificada y validada la fuente de extracción según los criterios de evaluación, se implementa una CDS como API corporativa hacia Datasphere, utilizando CDS DDIC-based como mecanismo alineado al stack SAP y el marco ODP.

| Anotación | Proposito |
|---|---|
| **@Analytics.dataExtraction.enabled** | Habilita la vista CDS para extracción vía ODP |
| **@Analytics.dataExtraction.delta.byElement** | Define el campo lógico como puntero delta (extractor clásico) (usar solo si no se implementa CDC) (*1) | 
| **@Analytics.dataExtraction.delta.changeDataCapture** | Habilita CDC automático o manual (sujeto a compatibilidad de release y tipo de CDS) (*1) | 
| **@AccessControl.authorizationCheck:** | #NOT_REQUIRED > El control de acceso se gestiona desde Datasphere | 
| **@Analytics.dataCategory: #XXX** | Define el tipo semántico de objetos transaccionales o dimensionales (#FACT, #DIMENSION). ODP genera sufijos: $F | $P |
| **@ObjectModel.dataCategory: #X** | #S (<1000), #M (<100,000), #L (<10,000,000), #XL (<100,000,000), #XXL (>= 100M) |
| **@ObjectModel.supportedCapabilities:** | #EXTRACTION_DATA_SOURCE > No habilita funcionalidad, sino metadata declarativa/capability |
| **@ObjectModel.usageType.sizeCategory** | Define el tipo semántico de objetos de texto o jerarquia (#TEXT, #HIERARCHY). ODP genera sufijos: $T | $H |
| **@OData.publish: false** | Desactiva servicios Odata (no requeridos para uso en SAP Datasphere) (*2) |

>📌Tip! No forma parte del template, pero en casos donde NO se quiera heredar anotaciones de las capas inferiores: @Metadata.ignorePropagatedAnnotations: true

{{callout: title="CDC Avanzado" tone="punch" icon="💡"}}
El soporte de delta avanzado (Change Data Capture con mapping) depende del SAP_BASIS (>= 7.56) y no está garantizado en todos los escenarios de extracción. Para mas información ver Nota SAP 3534086
{{/callout}}

## Clean Core aplicado a extracción
{{section_meta: keywords="clean core, qué es clean core, clean core sap, clean core extracción, principio clean core, clean core datasphere, apis liberadas, ODP clean core, CDS clean core, extensiones permitidas, evitar lógica SAP, exits simples, clean core integración"}}
{{section_exclude: "codigo, código, vista CDS, ejemplo CDS, crear CDS, escribir CDS, anotaciones CDS, patrón CDS, implementar CDS, template CDS, ayuda codigo, ayuda código"}}
{{support: CDS para Extraccion="https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/870d66c6bfc44d6c9f128c888f0c7957.html?utm_source=chatgpt.com&locale=en-US&version=LATEST" Nota SAP ODP-SAPI="https://me.sap.com/notes/2232584"}}

> Clean core no significa eliminar extractores SAP, sino apoyarse en mecanismos liberados (ODP/CDS) y mantener la lógica fuera del core.

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
{{section_meta: keywords="federación, persistencia, federación vs persistencia, tablas remotas, tablas locales, replicación datos, tiempo real datasphere, cuándo federar, cuándo persistir, remote tables, local tables, estrategia datos datasphere, federo, persisto, federo o persisto, federar o persistir, conviene federar o persistir"}}

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

## {dw-black}Modelo de Integración {dw-gold}Híbrido
{{section_meta: keywords="modelo híbrido, integración híbrida, híbrido federación persistencia, tablas remotas locales combinadas, evolución integración, remote tables local tables, data flow replication flow, cuándo usar híbrido"}}

{{subtitle: no-gap}}
> La decisión entre federar y persistir no cambia el modelo de integración, sino el modo de consumo del Data Product.

| Aspecto | Federacion / Tablas Remotas | Persistencia / Tablas Locales | Object Store / HDLF
|---|---|---|---|
| **Definición** | Tablas virtuales que acceden a datos en tiempo real desde sistemas origen (Ej.: CDS Views). | Tablas persistidas en Datasphere mediante flujos de datos o replicación. | Archivos físicos (Parquet + transaction log) persistidos en HANA Data Lake Files mediante Replication o Transformation Flows |
| **Propósito** | Evitar replicar datos innecesarios y mantenerlos en el sistema origen para garantizar consistencia. | Centralizar datos para un acceso más rápido y personalizado en Datasphere. | Almacenar grandes volúmenes de datos históricos a bajo costo, habilitando procesamiento Spark y consumo analítico vía SQL on Files o HANA Space | 
| **Casos de Uso Típicos** | Acceso en tiempo real. Datos cambiantes. Evitar costos de replicacion | Análisis de alto volumen. Combinar múltiples fuentes. Disponibilidad independiente del origen. | Alto volumen de datos históricos. Procesamiento medallion (Bronze/Silver/Gold). Datos compartidos vía Delta Sharing (BDC). Casos donde el costo HANA in-memory no se justifica |
| **Ventajas** | Datos siempre actualizados. Reducción de costos de almacenamiento. | Rendimiento optimizado para análisis. Menor latencia en consultas. Personalización de datos según los modelos analíticos | Costo de storage significativamente menor. Escalabilidad sin impacto en memoria HANA. Procesamiento distribuido con Spark. Compatibilidad nativa con ecosistemas lakehouse (Databricks, Delta Sharing) |
| **Limitaciones** | Dependencia del sistema origen (latencia, disponibilidad). Limitaciones en las transformaciones y uniones. Consultas complejas pueden ser lentas. | Almacenamiento (Licenciamiento/Capacity Units). Necesidad de sincronización y monitoreo. Potencial consistencia si los datos cambian rápidamente. | Requiere mínimo 128GB de memoria HANA habilitados (Nota SAP 3648786). Sin separación de ambientes DEV/QA/PRD en el storage (el lifecycle se gestiona desde los flujos). Mayor latencia que HANA in-memory para consultas analíticas frecuentes. El ciclo de vida del dato depende del flujo que lo genera, no del objeto en sí |
| **Role en Danwor** | Acceso al Dato | Construccion del Data Product | Lakehouse / Aterrizaje BDC-ready |

Te ayudamos a identificar el enfoque de Integración en SAP Datasphere a través de un **árbol determinístico** de decisión guiada.
{{tree_button: Federación vs Persistencia vs Híbrido}}

## {dw-black}Tipos de {dw-gold}Conexión
{{section_meta: keywords="tipos de conexiones, qué conexión usar, conexión datasphere, DP agent cloud connector SDA, SQL service RFC, conexión SAP datasphere, cómo conectar datasphere, árbol conexiones"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/94562426f30c475286f50a1e2b45e743.html?locale=en-US"}}
El siguiente **árbol determinístico** de decisión guiada puede servir como soporte para distintos casos de uso y contexto técnico en cuanto al tipo de conexión a utilizar, considerando sus compatibilidades y limitaciones de acuerdo al 🔗<link url="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/94562426f30c475286f50a1e2b45e743.html?locale=en-US">SAP Help</link>
{{tree_button: Opciones de Extracción}}

## {dw-black}Referencia {dw-gold}Técnica
Las vistas CDS habilitadas para extracción son la API estándar clean core para integrar S/4HANA con SAP Datasphere vía ODP/ABAP_CDS. A continuación, los patrones de referencia.

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
