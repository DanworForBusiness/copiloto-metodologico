---
module: "01"
title: "Estrategia y Readiness BDC"
nav_label: "01 · Estrategia y Readiness BDC"
tags: ["module:01", "domain:bdc", "domain:data-strategy", "domain:ai-readiness"]
key_topics:
  - id: punto_partida
    label: Punto de Partida
    heading: "Punto de Partida"
  - id: readiness
    label: Readiness
    heading: "Readiness de Datos para BDC"
  - id: valor
    label: Valor
    heading: "Arquitectura Orientada a Valor"
  - id: diagnostico
    label: Diagnóstico
    heading: "Diagnóstico Inicial"
scope: |
  Cubre el encuadre metodológico inicial para SAP Business Data Cloud: por qué BDC debe abordarse como una estrategia de datos
  y no como una instalación tecnológica, qué condiciones mínimas debe validar el consultor antes de avanzar, y cómo conectar
  arquitectura de datos con valor de negocio. No cubre detalle técnico de integración, modelado, data products o frameworks;
  esos temas se desarrollan en módulos posteriores y apéndices.
guardrails:
  - "No posicionar BDC como un reemplazo automático de plataformas existentes: primero diagnosticar estrategia, ownership, gobierno, arquitectura y consumo."
  - "AI readiness no significa tener casos de IA, sino contar con datos confiables, gobernados, trazables y semánticamente útiles."
  - "Toda recomendación debe conectar decisión técnica con capacidad de negocio, riesgo reducido o valor medible."
---

# 01 Estrategia y Readiness BDC

## {dw-black}Punto de {dw-gold}Partida
{{section_meta: keywords="BDC estrategia, business data cloud estrategia, punto de partida BDC, readiness BDC, estrategia de datos BDC, datos como activo, data strategy, agentic AI, inteligencia empresarial"}}
{{semantic_summary}}
Esta sección encuadra BDC como una iniciativa de estrategia de datos, no como una adopción tecnológica aislada. La conversación debe llevar al consultor a validar si la organización tiene claridad de objetivos, ownership, gobierno, calidad, semántica y capacidad de consumo antes de discutir componentes técnicos. Sirve para preguntas ejecutivas sobre por qué BDC importa y cómo iniciar un assessment.
{{/semantic_summary}}

{{subtitle: no-gap}}
> BDC no empieza por la plataforma, sino por una pregunta crucial: si el negocio quisiera operar con datos confiables, productos reutilizables e IA asistiendo decisiones, ¿la arquitectura actual lo soportaría?

El punto de partida metodológico es tratar los datos como un **activo estratégico**. No como residuo de procesos, no como tablas aisladas, no como reportes heredados. En BDC, el dato debe poder convertirse en una capacidad reutilizable: gobernada, trazable, semántica y lista para consumo analítico, operacional o de IA.

Para Danwor, una estrategia BDC madura debe ordenar cinco conversaciones desde el inicio:

| Conversación | Pregunta guía |
|---|---|
| **Negocio** | Qué capacidades, decisiones o procesos deben mejorar con datos |
| **Datos** | Qué entidades, métricas y dominios son críticos para el negocio |
| **Gobierno** | Quién define, aprueba, protege y mantiene esos datos |
| **Arquitectura** | Qué patrón permite integrar, modelar, compartir y consumir sin duplicar innecesariamente |
| **Valor** | Cómo se mide el impacto y cómo se evita construir arquitectura sin adopción |

{{callout: title="Postura Danwor" tone="punch" icon="💡"}}
La IA no corrige una arquitectura de datos débil. La expone. BDC debe diseñarse para que los datos puedan sostener decisiones, automatización e IA con contexto confiable.
{{/callout}}

## {dw-black}Readiness de Datos para {dw-gold}BDC
{{section_meta: keywords="readiness datos BDC, AI readiness, preparación para BDC, calidad datos, linaje, gobierno datos, data literacy, stewardship, ownership datos, datos confiables IA"}}

Antes de definir un roadmap BDC, el consultor debe evaluar si la organización tiene condiciones mínimas de readiness. Esta evaluación no busca frenar el proyecto: busca evitar que BDC herede el desorden actual con una capa nueva encima.

| Dimensión | Qué validar |
|---|---|
| **Calidad** | Datos completos, consistentes, oportunos y con reglas de validación explícitas |
| **Linaje** | Capacidad de explicar origen, transformación, consumo e impacto de cambios |
| **Semántica** | Definiciones de negocio claras para entidades, KPIs, atributos y métricas |
| **Ownership** | Responsables por dominio, producto de datos, calidad y decisiones de cambio |
| **Acceso y seguridad** | Políticas de consumo, clasificación, privacidad y segregación aplicables |
| **Consumo** | Casos de uso priorizados: reporting, self-service, data products, IA o integración externa |

> Una organización no está lista para BDC porque tenga muchos datos. Está lista cuando puede explicar cuáles importan, quién responde por ellos, cómo se gobiernan y qué valor habilitan.

{{callout_row}}
{{callout: title="Señales de madurez" tone="success" icon="🌿"}}
- Dominios de datos identificados
- Métricas críticas consensuadas
- Owners y stewards definidos
- Reglas de calidad visibles
- Catálogo o inventario útil
- Casos de consumo priorizados
{{/callout}}
{{callout: title="Señales de riesgo" tone="warn" icon="🚫"}}
- Reportes como única fuente de verdad
- KPIs con múltiples definiciones
- Ownership informal o inexistente
- Linaje no documentado
- Seguridad resuelta caso por caso
- IA planteada sin datos confiables
{{/callout}}
{{/callout_row}}

## {dw-black}Arquitectura Orientada a {dw-gold}Valor
{{section_meta: keywords="arquitectura orientada a valor, valor negocio BDC, value chain mapping, métricas BDC, business outcomes, estrategia datos valor, decisiones arquitectura"}}
{{semantic_summary}}
Esta sección guía al consultor a traducir decisiones de arquitectura BDC en valor observable: capacidades habilitadas, riesgos reducidos, métricas de adopción y resultados de negocio. Debe evitar respuestas centradas solo en tecnología; la arquitectura se justifica por impacto, no por elegancia técnica.
{{/semantic_summary}}

Una arquitectura BDC debe poder defenderse con lenguaje de negocio. Para cada decisión relevante, el consultor debería completar una cadena de valor simple:

| Paso | Pregunta |
|---|---|
| **Decisión técnica** | Qué patrón, componente o capacidad se propone |
| **Capacidad habilitada** | Qué puede hacer el negocio que antes no podía hacer bien |
| **Riesgo reducido** | Qué riesgo operativo, regulatorio, de seguridad o calidad se reduce |
| **Métrica observable** | Cómo sabremos que la decisión funcionó |
| **Valor esperado** | Qué resultado de negocio se acelera, protege o mejora |

Ejemplo de lectura Danwor:

| Decisión | Traducción a valor |
|---|---|
| Gobernar métricas críticas como productos de datos | Reduce reconciliación manual, mejora confianza ejecutiva y acelera cierre/reporting |
| Mantener linaje de entidades críticas | Reduce riesgo de impacto no controlado y mejora auditabilidad |
| Diseñar dominios con ownership explícito | Evita cuello de botella central y mejora calidad desde la fuente |
| Priorizar semántica antes de IA | Reduce respuestas inconsistentes, alucinaciones y baja adopción |

{{callout: title="Regla práctica" tone="punch" icon="💡"}}
Si una decisión de arquitectura no puede conectarse con una capacidad, un riesgo o una métrica, todavía no está lista para el roadmap.
{{/callout}}

## {dw-black}Diagnóstico {dw-gold}Inicial
{{section_meta: keywords="diagnóstico BDC, assessment BDC, checklist readiness BDC, evaluación estrategia datos, roadmap BDC, madurez datos, discovery BDC"}}

El primer diagnóstico BDC debe ser liviano, ejecutivo y suficiente para orientar el roadmap. No es una auditoría exhaustiva: es una lectura de madurez para decidir por dónde empezar.

| Frente | Preguntas de diagnóstico |
|---|---|
| **Estrategia** | Qué objetivos de negocio justifican BDC; qué decisiones o procesos deben mejorar |
| **Casos de uso** | Qué escenarios tienen valor claro: reporting, planificación, IA, sharing, modernización o integración |
| **Datos críticos** | Qué entidades y métricas sostienen esos casos |
| **Arquitectura actual** | Dónde viven los datos, cómo se integran, qué duplicaciones o dependencias existen |
| **Gobierno** | Quién decide definiciones, accesos, calidad, cambios y publicación |
| **Adopción** | Quién consume, con qué frecuencia, con qué dolor actual y qué cambio espera |

Resultado esperado del diagnóstico:

- Una lista priorizada de dominios y casos de uso.
- Riesgos principales de datos, gobierno e integración.
- Decisiones iniciales de arquitectura.
- Criterios para decidir qué va a módulo principal, qué va a apéndice técnico y qué queda fuera del alcance inmediato.

> Para profundizar el porqué conceptual detrás de este diagnóstico, usar el apéndice `A01 · Fundamentos Data Strategy AI`.
