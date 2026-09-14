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
  - id: tablas_remotas
    label: Tablas Remotas
    heading: "Tablas Remotas"
roadmap:
  - stage: "Contrato"
    tagline: "Qué te comprometés a entregar y bajo qué arquitectura"
    sections:
      - "Modelo de Integración Danwor"
      - "Contrato de Datos vs. Data Product"
      - "Arquitectura de Integración"
      - "Integración Legacy vs. Moderna"
  - stage: "Extracción"
    tagline: "Cómo sacás el dato de origen sin romper clean core"
    sections:
      - "Estrategia de Extracción"
      - "Tipos de Vistas CDS"
      - "Extracción vía API Corporativa"
      - "Clean Core aplicado a extracción"
  - stage: "Materialización"
    tagline: "Dónde vive el dato: federar, persistir o híbrido, y por qué conexión"
    sections:
      - "Tablas Remotas"
      - "Federación vs. Persistencia"
      - "Modelo de Integración Híbrido"
      - "Tipos de Conexión"
  - stage: "Referencia"
    tagline: "Patrones de código y templates listos para bajar"
    optional: true
    sections:
      - "Referencia Técnica"
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
  - "En Replication Flows con CDC, las columnas internas Change_Type/Change_Date que Datasphere agrega a la tabla local NO son un contrato soportado: no construir transformaciones, Data Products ni lógica de negocio que dependa de ellas fuera de Premium Outbound Integration (Notas SAP 3725855 y 3634750)."
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
      - Para delta, no limitarse a timestamp: diferenciar delta byElement vs CDC con mapping. CDC avanzado depende de release/SAP_BASIS, tipo de CDS y Nota SAP 3534086. Referenciar la sección "Extracción vía API Corporativa".
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

## {dw-black}Extracción vía {dw-gold}API Corporativa
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
| **@Analytics.dataCategory: #XXX** | Define el tipo semántico de objetos transaccionales o dimensionales (#FACT, #DIMENSION). ODP genera sufijos: $F \| $P |
| **@ObjectModel.dataCategory: #X** | Define el tipo semántico de objetos de texto o jerarquía (#TEXT, #HIERARCHY). ODP genera sufijos: $T \| $H |
| **@ObjectModel.supportedCapabilities:** | #EXTRACTION_DATA_SOURCE > No habilita funcionalidad, sino metadata declarativa/capability |
| **@ObjectModel.usageType.sizeCategory** | Define la categoría de tamaño del objeto: #S (<1000), #M (<100,000), #L (<10,000,000), #XL (<100,000,000), #XXL (>= 100M) |
| **@OData.publish: false** | Desactiva servicios Odata (no requeridos para uso en SAP Datasphere) (*2) |

>📌Tip! No forma parte del template, pero en casos donde NO se quiera heredar anotaciones de las capas inferiores: @Metadata.ignorePropagatedAnnotations: true

Del lado del Replication Flow que consume esta CDS: en modo Initial + Delta, Datasphere agrega dos columnas internas a la tabla local (`Change_Type` — L=carga inicial, A=upsert, D=delete — y `Change_Date`) para representar el ciclo de vida del registro. Son un objeto interno, no un contrato: no conviene construir transformaciones ni Data Products que dependan de ellas fuera de Premium Outbound Integration (Notas SAP 3725855, 3634750). La frecuencia con la que el flujo reintenta el delta también es configurable — default 1 hora, bajable a minutos o a modo near-real-time con 0/0 — conviene ajustarla al SLA real de consumo en vez de dejarla por inercia.

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

## {dw-black}Tablas {dw-gold}Remotas
{{section_meta: keywords="tabla remota, remote table, SDA, SDI, smart data access, smart data integration, acceso federado, replicar tabla remota, replicación tabla remota, filtros estáticos, filtro tabla remota, storage disco memoria, in-memory tabla, diferencia tabla remota tabla local, puente acceso federado, tabla virtual datasphere"}}
{{section_exclude: "tipo semántico, tipo semantico, uso semántico, uso semantico, fact o dimension, fact o dimensión, semantic usage, qué tipo semántico"}}
{{support: Importar Tablas Remotas="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/fd04efbac29c44fb8cfeaf2166b3d882.html" Replicar Datos de Tabla Remota="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/7e258a7554f84bf386c1bb029df5413f.html" Restringir Cargas="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/bd1ece5c9f78444c87708ef11eed0a31.html" Propiedades de Tabla="https://help.sap.com/docs/SAP_DATASPHERE/c8a54ee704e94e15926551293243fd1d/75cea7b1cbeb4c9dba0ac13605c527b3.html"}}

> Una tabla remota es el artefacto en Datasphere que materializa la federación: actúa como un objeto virtual que expone datos del sistema origen sin copiarlos. Existen dos adaptadores primarios para tablas remotas (SDA/SDI), explicados gráficamente y tooltips en [Arquitectura de Integración →](#arquitectura-de-integracion) si querés profundizar.

Una misma tabla remota puede vivir en cualquiera de estos tres estados — se cambia desde el editor, sin necesidad de recrear el objeto:

| Estado | Qué significa |
|---|---|
| **Remoto** | Federado: se lee la tabla virtual, los datos nunca salen del origen (predeterminado al importar) |
| **Replicado (Snapshot)** | Los datos se copian una vez (o por schedule) a una tabla de réplica en Datasphere — no se actualizan solos |
| **Replicado (Real-time)** | Los datos se copian y se mantienen sincronizados en tiempo real |

{{callout: title="Replicar la tabla remota vs. Replication Flow" tone="warn" icon="🔀"}}
La gestión de "Replicado" en una tabla remota (vía SDI) es el mecanismo inicial del producto — previo flujos de replicación. Se gestiona una tabla a la vez, sin las capacidades de Replication Flow (como carga masiva de objetos, conectores, recuperación desde el punto donde falló, etc.). Para traer datos con delta hacia Datasphere, recomendamos te apoyes en Replication Flows. El toggle de la tabla remota sigue vigente para casos puntuales o legados, no como opción preferencial.
{{/callout}}

### Controlar volumen y performance

- **Filtros estáticos**: reducen lo que se carga a la tabla remota — solo admiten condiciones *Igual a* y *Entre* (nada dinámico como "últimos 3 años"), y qué columnas se pueden filtrar depende del tipo de fuente (por ejemplo, con conexiones CDI solo se filtra sobre NVARCHAR, Date, Boolean, Integer, BigInt). Un registro con valor nulo en una columna filtrada nunca se replica. No es buena práctica abusar de esta capacidad: el dato debe gobernarse desde su contrato, no desde un filtro escondido en la capa de conectividad.
- **Storage — Disco vs. En Memoria**: por default los datos viven en disco; pasar a En Memoria mejora performance de lectura a costa de consumo de memoria del espacio — mismo toggle que ya se ve como "HANA DB Disk | In-Memory" en los diagramas de arquitectura.
- **Uso semántico**: una tabla remota se clasifica igual que cualquier otro artefacto de modelado (Relacional, Fact, Dimensión, Jerarquía, Texto) — ver [Modelado y Desarrollo →](/modules/04_Modelado_y_Desarrollo?pack={{pack}}#artefactos-y-uso-semantico)

{{callout: title="Tabla Remota vs. Tabla Local" tone="punch" icon="🔲"}}
La Tabla Remota es el artefacto de **Ingesta federada**: apunta al origen, con la opción de replicarse. La Tabla Local es el resultado de **persistir** — ya sea por Replication Flow, Transformation Flow o Data Flow (ver [Modelado y Desarrollo →](/modules/04_Modelado_y_Desarrollo?pack={{pack}}#flujos-de-datos)). Una vez replicada, una tabla remota deja de comportarse como tal — en el sentido estricto — pero el objeto sigue siendo el mismo, con su vínculo al origen. 
{{/callout}}

### Streaming Read — Remote Tables vía DPA/ABAPAdapter
{{section_meta: keywords="streaming read, rfc streaming, non-rfc streaming, dialog mode, dialogue mode, abapadapter, dp agent memoria, memoria dp agent, gateway host, gateway port, gateway service, rfc destination, batch size, out of memory dp agent, oom abapadapter, oom data provisioning agent, streaming read on off, sm59 abapadapter, ecc dialog mode timeout"}}
{{support: SAP ABAP Connections="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/a75c1aacf951449ba3b740c7e46da3a9.html" Prerequisites for ABAP RFC Streaming="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/62adb440e4214c53a3028a4fdb5e1156.html" Using RFC Streaming with Tables="https://help.sap.com/docs/HANA_SMART_DATA_INTEGRATION/7952ef28a6914997abc01745fef1b607/960a1e7197a94da7ae379caf305bdbc7.html" SAP ABAP Adapter Remote Source Configuration="https://help.sap.com/docs/HANA_SMART_DATA_INTEGRATION/7952ef28a6914997abc01745fef1b607/8575941eb7a344abadc21721418e28cb.html"}}

> **Alcance**: esto describe el parámetro *Streaming Read* de una tabla remota cuando el **Data Provisioning Option** de la conexión ABAP/S4HANA es **Data Provisioning Agent** (SAP HANA SDI, vía **ABAPAdapter**) — no es una propiedad genérica de "una conexión S/4HANA". Remote Tables vía **Cloud Connector / ABAP SQL Service** son otro stack tecnológico (SDA) y no tienen este parámetro, y tampoco aplica a **Replication Flows**. Que una conexión tenga Cloud Connector configurado para otra capacidad (por ejemplo ABAP SQL Service o Replication Flows) no implica que el ABAPAdapter o su RFC Streaming estén participando: son mecanismos independientes. Regla general: identificar primero el mecanismo técnico efectivo de acceso/provisión, nunca inferirlo por el nombre lógico de la conexión.

**Off no significa ausencia de streaming:** SAP denomina este modo *non-RFC streaming*.

| | Streaming Read = Off (non-RFC streaming) | Streaming Read = On (RFC Streaming Read) |
|---|---|---|
| **Transferencia** | El recordset objetivo se extrae completo, como un único batch | Transferencia progresiva en paquetes (*Batch Size*, configurable) vía callback RFC hacia el ABAPAdapter/DPA |
| **Ejecución en el origen** | Corre en modo *dialog* de ABAP/ECC — sujeto a sus límites (por ejemplo, timeout de dialog mode) | Requiere registrar un server RFC (Gateway Host, Gateway Port/Gateway Service, RFC Destination vía SM59) |
| **Configuración adicional** | Ninguna | Gateway Host/Port, RFC Destination — ver *Prerequisites for ABAP RFC Streaming* |
| **Memoria en DPA** | Debe sostener el recordset completo — depende de volumen, ancho de registro y concurrencia; SAP publica una referencia orientativa (no una fórmula de sizing) de ~1 GB de RAM por millón de registros | Acotada al paquete en tránsito; SAP documenta una referencia aproximada de ~2× el Batch Size configurado para ese proceso puntual — tampoco es una fórmula de sizing total del DPA |

{{callout_row}}
{{callout: title="FACT — documentado por SAP" tone="punch" icon="📖"}}
La diferencia entre extraer el recordset completo con Off y transferirlo por paquetes con On, así como las referencias de memoria de la fila **Memoria en DPA** de arriba, están documentadas por SAP — no son supuestos de arquitectura Danwor.
{{/callout}}
{{callout: title="Interpretación arquitectónica Danwor" tone="success" icon="🌿"}}
RFC Streaming reduce la presión de memoria de una lectura puntual al procesar el resultado en paquetes. Encontrar Streaming Read = Off puede ser consistente con mayor presión de memoria, pero no demuestra que sea la causa de un OOM. Si la carga es pesada, recurrente o altamente concurrente, además de revisar esta configuración conviene cuestionar si debe seguir federada o requiere materialización o un diseño híbrido.
{{/callout}}
{{/callout_row}}

La documentación actual de Datasphere marca la opción *Data Provisioning Agent* (SDI ABAPAdapter) como **legacy** — sigue soportada, no deprecada ni obsoleta — y para escenarios nuevos orienta hacia **ABAP SQL Service** para federación (cuando esté disponible) y **Replication Flows** para replicación. RFC Streaming Read sigue siendo relevante para landscapes que ya usan DPA/ABAPAdapter; para decisiones nuevas, el mecanismo apropiado se evalúa por federación vs. replicación, capacidades/versión del origen (ODP/CDS/Dictionary), delta, volumen, latencia, operación y restricciones — nunca por el nombre lógico de la conexión.

{{callout: title="Regla Danwor" tone="success" icon="🌿"}}
No inferir comportamiento por el nombre lógico de la conexión. Identificar primero el mecanismo técnico efectivo de acceso/provisión.
{{/callout}}

## {dw-black}Federación vs. {dw-gold}Persistencia
{{section_meta: keywords="federación, persistencia, federación vs persistencia, tablas remotas, tablas locales, replicación datos, tiempo real datasphere, cuándo federar, cuándo persistir, remote tables, local tables, estrategia datos datasphere, federo, persisto, federo o persisto, federar o persistir, conviene federar o persistir"}}

{{subtitle: no-gap}}
> La mecánica de cada una ya la vimos en Tablas Remotas — acá el foco es cuándo conviene una sobre la otra: empezar con vistas federadas y complementar con persistencia según volumen y rendimiento

| Federación | Persistencia |
|---|---|
| ✅ Siempre actualizado (consulta en vivo, sin latencia de sincronización) | ✅ Performance (ya no golpean al origen, corren en Datasphere) |
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

{{callout: title="Portabilidad HDLF/BDC" tone="warn" icon="⚠️"}}
Cuando el Data Product aterriza en Object Store / HDLF (o se comparte vía Delta Sharing hacia Databricks), **un tipo válido en HANA no es necesariamente un contrato portable confiable cross-runtime**. Aprendizajes de campo Danwor:

- Campos monetarios `DecimalFloat` pueden convertirse de forma impredecible (String(10)) al publicar → **normalizar a `DECIMAL(38,6)` en la capa contractual**.
- Un KPI con escala inferida alta (ej. `DECIMAL(37,19)`) puede romper el Transformation Flow → **castear `CAST` en la misma expresión donde nace el campo**.
- Regla: el tipo del campo derivado se fija **donde nace** (capa contractual / expresión de origen), no en la publicación. 
{{/callout}}

Te ayudamos a identificar el enfoque de Integración en SAP Datasphere a través de un **árbol determinístico** de decisión guiada.
{{tree_button: Federación vs Persistencia vs Híbrido}}

## {dw-black}Tipos de {dw-gold}Conexión
{{section_meta: keywords="tipos de conexiones, qué conexión usar, conexión datasphere, DP agent cloud connector SDA, SQL service RFC, conexión SAP datasphere, cómo conectar datasphere, árbol conexiones"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/94562426f30c475286f50a1e2b45e743.html?locale=en-US"}}
La elección del **tipo de conexión** (DP Agent, Cloud Connector, SDA, ABAP SQL service, RFC) depende de compatibilidades y limitaciones técnicas según origen, release y modalidad. La matriz oficial de tipos de conexión soportados está en el 🔗<link url="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/94562426f30c475286f50a1e2b45e743.html?locale=en-US">SAP Help</link>. Para decidir el **mecanismo de extracción** asociado, usá el árbol de la sección *Estrategia de Extracción*. Un parámetro puntual de una tabla remota (por ejemplo, *Streaming Read* del ABAPAdapter, ver [Tablas Remotas →](#tablas-remotas)) depende siempre del mecanismo de provisión efectivamente elegido, nunca del nombre lógico de la conexión.

{{tree_button: Opciones de Extracción}}

## {dw-black}Referencia {dw-gold}Técnica
Las vistas CDS habilitadas para extracción son la API estándar clean core para integrar S/4HANA con SAP Datasphere vía ODP/ABAP_CDS. Lo primero es decidir **qué patrón** aplica según la tabla origen; el código es referencia ilustrativa que baja del template mantenido.

### Cuándo usar cada patrón

| Escenario | Patrón recomendado |
|---|---|
| Tabla sin campo de cambio confiable | Full únicamente |
| Tabla con `changed_at` o `last_changed_at` | Delta byElement |
| Vista con joins entre tablas | CDC con mapping |
| Volumen bajo, sin requisito de incremental | Full |

Los **templates versionados** (DDIC-based y CDS View Entity) viven en SharePoint y son la fuente autoritativa: se mantienen una sola vez y el asistente te los ofrece cuando pedís código CDS. Los ejemplos de abajo son ilustrativos para entender la anatomía de cada patrón.

{{support: Template CDS DDIC="https://danwor.sharepoint.com/:t:/s/PrcticaAnalytics/IQCpgsn98NNWQLd3WDj4SsNkAWEQMUwcLRS0EtKL6Y1oiew?e=n2OdL4" Template CDS View Entity="https://danwor.sharepoint.com/:t:/s/PrcticaAnalytics/IQBVd62iU6Q5RYWiHrFkDxuPAbAfk-hkIFCeAoBmeGUC7Vc?e=cPFjN7"}}

{{collapsible: title="Ver los 3 patrones de código (referencia ilustrativa)"}}

#### Patrón 1 — Extracción FULL
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

#### Patrón 2 — Extracción FULL + Delta por campo timestamp
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

#### Patrón 3 — CDC con join (Change Data Capture)

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

{{/collapsible}}

{{subtitle: no-gap}}
> La elección entre Full y Delta impacta directamente en la estrategia de replicación en Datasphere (Replication Flow vs Data Flow) y en la capacidad de mantener datos actualizados sin recargas completas.
