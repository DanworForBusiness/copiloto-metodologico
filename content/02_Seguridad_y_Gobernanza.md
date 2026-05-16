---
module: "02"
title: "Seguridad y Gobernanza"
nav_label: "02 · Seguridad y Gobernanza"
tags: ["module:02", "domain:security", "domain:governance"]
key_topics:
  - id: roles
    label: Seguridad de Roles
    heading: "Seguridad de Roles"
  - id: dac
    label: Seguridad de Datos
    heading: "Seguridad de Datos"
  - id: gobernanza
    label: Gobernanza
    heading: "Seguridad de Roles & Datos"
scope: |
  Cubre el diseño de seguridad en SAP Datasphere y Business Data Cloud (BDC):
  modelo de roles (estándar vs custom), Data Access Control (DAC) a nivel fila
  y columna, tipos de permisos (Valores Individuales, Operadores y Valores,
  Jerarquía), y principios de gobernanza derivados del diseño de dominios.
  No cubre modelado de datos ni definición de Semantic Types (módulo 04),
  configuración de espacios o transportes (módulo 01), ni integración con
  sistemas origen (módulo 03).
guardrails:
  - "La seguridad debe diseñarse desde roles, DAC, dominios y principio de menor privilegio; no resolverla duplicando datasets, vistas o espacios sin gobierno."
  - "Los filtros de reporting no reemplazan controles de seguridad en Datasphere cuando el acceso a datos sensibles debe gobernarse en la plataforma."
  - "No mezclar semántica de negocio con permisos: si la consulta trata tipos semánticos, capas o modelado analítico, derivar al módulo 04."
out_of_scope_redirect:
  - keywords: ["espacio datasphere", "space datasphere", "transporte", "workload class",
               "configuración datasphere", "setup datasphere", "conexión datasphere",
               "DP agent", "cloud connector"]
    module: "01"
    label: "Arquitectura y Conectividad"
    message: "Ese tema corresponde al módulo 01 · Arquitectura y Conectividad, que cubre configuración de espacios, transportes y tipos de conexión."
  - keywords: ["tipo semantico", "tipos semanticos", "semantic type", "fact", "dimension",
               "capa business", "capa consumption", "modelado datasphere", "vista analitica",
               "entidad de negocio", "business entity"]
    module: "04"
    label: "Modelado en SAP Datasphere"
    message: "Ese tema corresponde al módulo 04 · Modelado en SAP Datasphere, que cubre Semantic Types, capas Business/Consumption y modelado analítico."
  - keywords: ["extraccion", "extracción", "ODP", "CDS extracción", "sistema fuente",
               "integración datos", "replicación", "federación", "persistencia"]
    module: "03"
    label: "Integración y Estrategia de Datos"
    message: "Ese tema corresponde al módulo 03 · Integración y Estrategia de Datos, que cubre extracción, ODP, CDS y estrategias de integración."
---
# 02 Seguridad y Gobernanza

## {dw-black}Seguridad de {dw-gold}Roles
{{section_meta: keywords="roles, permisos, roles estándar, roles estandar, roles custom, seguridad roles, permisos datasphere, roles datasphere, estándar vs custom, estandar vs custom, crear roles, diseño roles, roles SAP, marco de roles, objeto de seguridad, objetos seguridad, segregación funciones, segregacion funciones, qué rol usar, roles PoC, roles piloto, roles MVP, escalabilidad roles, Danwor RDS roles, marco Danwor roles, roles BDC"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/9f804b8efa8043539289f42f372c4862/d7350c6823a14733a7a5727bad8371aa.html?locale=es-ES&version=LATEST"}}

> A la fecha, no existe práctica recomendada por SAP respecto al uso directo de los objetos de seguridad estándar vs. la creación de un marco propio apoyado en el estándar. Dado que ambos casos de uso podrían ser técnicamente válidos, detallamos sus virtudes y desventajas.
{{html_embed: ../assets/danwor-seguridad-roles-dac.html height=890 default_mode=roles}}
{{tree_button: Seguridad de Roles}}

## {dw-black}Seguridad de {dw-gold}Datos 
{{section_meta: keywords="DAC, data access control, seguridad datos, seguridad de datos, seguridad fila, seguridad columna, row level security, column level security, valores individuales, operadores y valores, jerarquía, jerarquia, tipos DAC, tipos de permisos, permisos DAC, acceso a datos, restricción de datos, qué tipo de DAC, cómo restringir datos, acceso dinámico, regla acceso, reglas acceso, seguridad SAP Datasphere, BDC seguridad, filtro datos, filtro por usuario, jerarquía organizacional, centro de costos, organigrama"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/a032e51c730147c7a1fcac125b4cfe14.html?locale=en-US&version=LATEST&state=PRODUCTION"}}

> A la fecha, Datasphere soporta seguridad a nivel fila/columna vía DAC. Con Business Data Cloud (BDC) se prevé integración nativa cross-system, aún no disponible. El diseño de seguridad debe derivar siempre de los dominios y data products definidos a nivel conceptual.
{{html_embed: ../assets/danwor-seguridad-roles-dac.html height=880 default_mode=dac}}
{{tree_button: Seguridad de Datos}}

## {dw-black}Seguridad de {dw-gold}Roles & Datos
{{section_meta: keywords="seguridad diseño dominios, data product seguridad, gobernanza datos, principio seguridad, modelo seguridad, dominios y seguridad, data governance, roles responsabilidades, controles acceso, activo confiable, seguridad bien diseñada, diseño conceptual seguridad, dominios Danwor"}}

{{subtitle: no-gap}}
> Seguridad como extensión del diseño de dominios
{{callout: title="Recordá..." tone="punch" icon="🔐"}}
La seguridad bien diseñada no debiera retrasar al negocio, sino asegurar que los datos sean un activo confiable. Definir claramente roles, responsabilidades y controles de acceso **derivados del diseño de dominios y data products** asegura que las decisiones estratégicas se apoyen en información íntegra, protegida y gobernada.
{{/callout}}
