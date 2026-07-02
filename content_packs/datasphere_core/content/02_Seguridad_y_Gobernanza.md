---
module: "02"
title: "Seguridad y Gobernanza"
nav_label: "02 · Seguridad y Gobernanza"
tags: ["module:02", "domain:security", "domain:governance"]
key_topics:
  - id: roles
    label: Roles
    heading: "Seguridad de Roles"
  - id: dac
    label: DAC
    heading: "Seguridad de Datos (DAC)"
  - id: gobernanza
    label: Gobernanza
    heading: "Seguridad como extensión del diseño de dominios"
scope: |
  Cubre el diseño de seguridad en SAP Datasphere y Business Data Cloud (BDC):
  modelo de roles (estándar vs custom), Data Access Control (DAC) a nivel fila y columna, tipos de permisos (Valores Individuales, Operadores y Valores, Jerarquía), 
  y principios de gobernanza derivados del diseño de dominios. No cubre modelado de datos ni definición de Semantic Types (módulo 04), configuración de espacios o 
  transportes (módulo 01), ni integración con sistemas origen (módulo 03).
guardrails:
  - "La seguridad debe diseñarse desde roles, DAC, dominios (espacios) y principio de menor privilegio; no resolverla duplicando datasets, vistas o espacios sin gobierno."
  - "Los filtros de reporting no reemplazan controles de seguridad en Datasphere cuando el acceso a datos sensibles debe gobernarse en la plataforma."
  - "No mezclar semántica de negocio con permisos: si la consulta trata tipos semánticos, capas o modelado analítico, derivar al módulo 04."
quick_action_guidance:
  "Guía operativa del módulo":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 02:
      - La guía operativa debe partir de una decisión de diseño: cómo gobernar acceso desde roles, DAC, dominios (espacios) y principio de menor privilegio.
      - No reducir la guía a una lista técnica de permisos. El foco es evitar que la seguridad quede como ajuste posterior al modelado.
      - Capitalizar las secciones Seguridad de Roles, Seguridad de Datos (DAC) y Seguridad como extensión del diseño de dominios.
      - Diferenciar claramente seguridad de roles (quién puede operar objetos/capacidades) de DAC (qué datos puede ver una persona o grupo).
      - No recomendar duplicar datasets, vistas, modelos o espacios para resolver seguridad salvo como excepción justificada y gobernada.
      - Si aparece modelado semántico, capas, entidades o data products, usarlo solo como dependencia conceptual y derivar el detalle al módulo 04.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 02 - SEGURIDAD Y GOBERNANZA:
      La decisión dominante debe formularse como: diseñar un modelo de acceso gobernado que combine roles, DAC y dominios, evitando que reporting, duplicación de objetos o espacios sustituyan controles de seguridad.

      Incluí obligatoriamente:
      - Cuándo usar roles estándar y cuándo evaluar roles custom.
      - Cómo decidir si DAC aplica por fila, columna, valores individuales, operadores/valores o jerarquía.
      - Qué validar sobre dominios, data products y sensibilidad del dato antes de cerrar permisos.
      - Qué pruebas mínimas ejecutar con usuarios/personas representativas.
      - Qué riesgos aparecen si la seguridad se define tarde o fuera del diseño de dominios.

      Evitá respuestas genéricas de "asignar permisos". La salida debe ayudar a tomar decisiones de arquitectura de seguridad.
  "Checklist de implementación":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 02:
      - El checklist debe validar que el modelo de seguridad esté listo para implementar, no solo que existan roles.
      - Incluir controles sobre roles, DAC, dominios, data products, usuarios/grupos, pruebas y auditoría.
      - Distinguir validaciones de diseño de seguridad frente a validaciones de modelado, que pertenecen al módulo 04.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 02 - CHECKLIST:
      Armá el checklist como una lista ejecutable para una reunión técnica de seguridad/gobernanza.

      Cubrí como mínimo:
      - Roles estándar vs roles custom.
      - Objetos/capacidades que gobierna cada rol.
      - Datos sensibles y granularidad requerida.
      - Tipo de DAC aplicable: valores individuales, operadores/valores o jerarquía.
      - Asignación por usuario, grupo o estructura organizacional.
      - Pruebas con usuarios/personas representativas.
      - Evidencia de auditoría y trazabilidad.
      - Dependencias con dominios/data products y, si aplica, derivación al módulo 04.
  "Errores frecuentes":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 02:
      - Los errores frecuentes deben enfocarse en seguridad mal diseñada, no en pasos administrativos.
      - Priorizar anti-patterns de acceso: usar filtros de reporting como seguridad, duplicar objetos para restringir datos, sobredimensionar roles custom o usar roles estándar sin gobierno.
      - DAC debe tratarse como control de plataforma, no como filtro visual.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 02 - ERRORES FRECUENTES:
      Priorizá errores frecuentes de diseño de seguridad y gobernanza:
      - Resolver seguridad con filtros en reporting en lugar de controles en Datasphere.
      - Duplicar datasets, modelos, vistas o espacios para separar accesos sin criterio de gobierno.
      - Usar roles estándar de forma indiscriminada sin revisar segregación, operación y escalabilidad.
      - Crear roles custom demasiado pronto, sin matriz de responsabilidades ni evidencia de necesidad.
      - Elegir mal el tipo de DAC o mezclar seguridad de filas/columnas con semántica de negocio.
      - No probar con usuarios/personas reales antes de avanzar a consumo.
      - No asignar owner ni trazabilidad sobre cambios de permisos.

      Para cada error, explicá causa raíz, impacto en proyecto y corrección Danwor.
  "Ejemplos prácticos":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - MÓDULO 02:
      - Los ejemplos deben salir de los tres escenarios del módulo: roles, DAC y seguridad derivada del diseño de dominios.
      - No inventar casos externos si el módulo ya ofrece suficiente estructura.
      - Si se usa inferencia para completar contexto de cliente, marcarla como [INFERENCIA].
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA MÓDULO 02 - EJEMPLOS PRÁCTICOS:
      Usá como mínimo estos tres ejemplos:
      - Roles estándar vs roles custom: cuándo aceptar el estándar y cuándo justificar un marco propio.
      - DAC por valores individuales, operadores/valores o jerarquía: cómo elegir según la organización del acceso.
      - Seguridad como extensión de dominios/data products: cómo evitar que la seguridad se resuelva tarde o en reporting.

      Para cada ejemplo, entregá:
      - Escenario.
      - Decisión Danwor.
      - Por qué esa y no otra.
      - Riesgo si se elige mal.
intent_assets:
  - keywords: ["template seguridad", "template roles", "template DAC", "matriz roles",
               "matriz seguridad", "plantilla seguridad", "plantilla roles",
               "roles custom", "roles estándar", "roles estandar", "modelo seguridad",
               "seguridad datasphere", "data access control", "DAC"]
    description: "Template Danwor de Seguridad, Roles y DAC"
    asset: "https://danwor.sharepoint.com/:x:/s/PrcticaAnalytics/IQC_KaECHiU5TrOjJBpDhkC7AeH7NJUQhq3AACTGOSpltG8?e=ItlfCX"
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

> A la fecha, no existe práctica recomendada por SAP respecto al uso directo de los objetos de seguridad estándar vs. la creación de un marco propio apoyado en el estándar. Dado que ambos casos de uso podrían ser técnicamente válidos, detallamos sus virtudes y desventajas.
{{html_embed: ../assets/danwor-seguridad-roles-dac.html height=730 default_mode=roles}}

{{tree_button: Seguridad de Roles}}

## {dw-black}Seguridad de {dw-gold}Datos 
{{section_meta: keywords="DAC, data access control, seguridad datos, seguridad de datos, seguridad fila, seguridad columna, row level security, column level security, valores individuales, operadores y valores, jerarquía, jerarquia, tipos DAC, tipos de permisos, permisos DAC, acceso a datos, restricción de datos, qué tipo de DAC, cómo restringir datos, acceso dinámico, regla acceso, reglas acceso, seguridad SAP Datasphere, BDC seguridad, filtro datos, filtro por usuario, jerarquía organizacional, centro de costos, organigrama"}}
{{support: SAP Help="https://help.sap.com/docs/SAP_DATASPHERE/be5967d099974c69b77f4549425ca4c0/a032e51c730147c7a1fcac125b4cfe14.html" Pin=true}}

SAP Datasphere soporta seguridad a nivel fila y columna vía **Data Access Control (DAC)**. Con Business Data Cloud (BDC) se prevé integración de seguridad nativa cross-system, aunque aún no está disponible en todas las versiones.

{{html_embed: ../assets/danwor-seguridad-roles-dac.html height=796 default_mode=dac}}

Existen tres tipos de estructuras de permisos. La elección depende de cómo se organiza el acceso en el cliente:

| Tipo | Descripción | Cuándo usarlo |
|---|---|---|
| **Valores Individuales** | Asignación directa de valores fijos por usuario (ej: `SalesOrg = AR01`) | Accesos estáticos o de baja frecuencia de cambio. Ideal para PoC y MVP |
| **Operadores y Valores** | Reglas con condiciones lógicas: rangos, "mayor que", "contiene", combinaciones | Accesos con lógica condicional o rangos de valores (ej: períodos, montos) |
| **Jerarquía** | El acceso se hereda por niveles de una estructura jerárquica (ej: organigrama, centros de costo) | Modelos de acceso donde el padre da acceso a todos sus hijos en la jerarquía |

{{callout_row}}
{{callout: title="Valores Individuales" tone="note" icon="🔹"}}
El punto de partida más seguro. Simple de configurar, auditable, y suficiente para la mayoría de los escenarios en fase de proyecto.
{{/callout}}
{{callout: title="Operadores y Valores" tone="punch" icon="⚙️"}}
Ideal cuando los accesos tienen lógica de negocio: rangos de período, umbrales monetarios, o combinaciones de condiciones por área.
{{/callout}}
{{callout: title="Jerarquía" tone="success" icon="🌿"}}
El tipo más potente para estructuras organizacionales. El acceso al nodo padre propaga automáticamente a todos los descendientes.
{{/callout}}
{{/callout_row}}

{{tree_button: Seguridad de Datos}}

## {dw-black}Seguridad como {dw-gold}extensión del diseño de dominios
{{section_meta: keywords="seguridad diseño dominios, data product seguridad, gobernanza datos, principio seguridad, modelo seguridad, dominios y seguridad, data governance, roles responsabilidades, controles acceso, activo confiable, seguridad bien diseñada, diseño conceptual seguridad, dominios Danwor"}}

{{callout: title="Principio Danwor — Seguridad desde el diseño" tone="success" icon="🔒"}}
La seguridad bien diseñada no debería retrasar al negocio, sino garantizar que los datos sean un activo confiable.

Definir claramente roles, responsabilidades y controles de acceso **derivados del diseño de dominios y data products** asegura que las decisiones estratégicas se apoyen en información íntegra, protegida y gobernada.
{{/callout}}

El modelo de seguridad no es un paso posterior al modelado: se deriva de él. Cada dominio y data product definidos a nivel conceptual tienen implicancias directas sobre:

- **¿Quién puede ver qué?** — Roles y permisos a nivel espacio y objeto
- **¿Con qué granularidad?** — DAC a nivel fila/columna según la sensibilidad del dato
- **¿Bajo qué condiciones?** — Reglas dinámicas alineadas a la estructura organizacional del cliente
