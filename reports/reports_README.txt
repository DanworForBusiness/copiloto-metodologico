================================================================================
REPORTS/ — GUÍA DE USO Y GESTIÓN ANALÍTICA
Copiloto Metodológico | Arquitecto Danwor
================================================================================

Este directorio contiene los reportes generados automáticamente por la
plataforma a partir de datos reales de uso: casos registrados por consultores,
feedback sobre outputs de IA y solicitudes de contenido faltante.

Los reportes son de SOLO LECTURA — no editarlos manualmente.
Son el input para decisiones de mejora, no documentos finales.

--------------------------------------------------------------------------------
CÓMO SE GENERAN
--------------------------------------------------------------------------------

Todos los reportes se generan desde la tab "🔬 Analítica" (visible solo en
modo diagnóstico) o ejecutando los scripts directamente desde la terminal.

El modo del dropdown (Simulación / API Real) determina el alcance:

  Simulación  → análisis estructurado local, sin LLM, sin consumo de API
  API Real    → análisis completo incluyendo componente semántico con IA
                (requiere OPENAI_API_KEY configurada, consume créditos)

En ambos casos los reportes se sobreescriben en cada ejecución. Si querés
conservar una versión anterior, copiá el archivo antes de volver a correr.

--------------------------------------------------------------------------------
ESTRUCTURA DE CARPETAS
--------------------------------------------------------------------------------

reports/
├── gap_nodes.yaml              ← de analyze_cases.py
├── route_patterns.yaml         ← de analyze_cases.py
├── leaf_outcomes.yaml          ← de analyze_cases.py
├── lessons.yaml                ← de analyze_cases.py
├── text_clusters.yaml          ← de analyze_cases.py --llm (API Real)
├── summary.txt                 ← de analyze_cases.py (resumen legible)
│
├── feedback/
│   ├── ia_feedback.yaml        ← de analyze_feedback.py
│   ├── tree_feedback.yaml      ← de analyze_feedback.py
│   ├── missing_trees.yaml      ← de analyze_feedback.py
│   └── summary.txt             ← de analyze_feedback.py (resumen legible)
│
└── content_proposals/
    ├── proposals.yaml          ← de propose_content_updates.py
    └── summary.txt             ← de propose_content_updates.py (resumen legible)

--------------------------------------------------------------------------------
ARCHIVOS — DESCRIPCIÓN DETALLADA
--------------------------------------------------------------------------------

── RAÍZ DE reports/ ──────────────────────────────────────────────────────────

gap_nodes.yaml
  Qué es:   Nodos del árbol desde donde los consultores reportaron no encontrar
            su camino (casos "uncharted"). Cada entrada indica el árbol, el nodo
            de quiebre y cuántas veces ocurrió.
  Para qué: Identifica qué preguntas del árbol no contemplan suficientes
            respuestas. Alta frecuencia en un nodo = señal de que falta una rama.
  Acción:   Revisar el YAML del árbol correspondiente y agregar opciones de
            respuesta o nodos derivados para cubrir los escenarios detectados.

route_patterns.yaml
  Qué es:   Rutas completas recorridas por los consultores, agrupadas por
            frecuencia y outcome (anduvo / no anduvo).
  Para qué: Revela qué caminos son los más usados y cuáles tienen mayor tasa
            de fracaso. Una ruta frecuente con mal outcome es prioridad de mejora.
  Acción:   Rutas con outcome negativo repetido → revisar la recomendación del
            leaf. Rutas muy frecuentes → asegurarse de que el contenido asociado
            esté completo y actualizado.

leaf_outcomes.yaml
  Qué es:   Resultados registrados por leaf (nodo hoja de decisión), con tasa
            de éxito, outcomes detallados y confianza promedio.
  Para qué: Permite ver qué decisiones finales están funcionando bien y cuáles
            están generando problemas en campo.
  Acción:   Leaves con tasa de éxito < 50% y al menos 2 casos → revisar la
            recomendación, agregar condiciones de uso o advertencias.

lessons.yaml
  Qué es:   Todas las lecciones de campo registradas por consultores al cerrar
            un caso, agrupadas por árbol.
  Para qué: Es conocimiento tácito capturado en tiempo real. Muchas lecciones
            sobre el mismo árbol o leaf indican que falta documentar algo.
  Acción:   Incorporar las lecciones relevantes como notas en el módulo .md
            correspondiente o como condiciones adicionales en el árbol.
  Nota:     No eliminar lecciones de la case_library/ aunque ya estén
            incorporadas al contenido — son el registro histórico.

text_clusters.yaml  [solo en API Real]
  Qué es:   Agrupamiento semántico de los contextos libres escritos por
            consultores (campo "context_description" de los casos), generado
            por LLM.
  Para qué: Detecta patrones implícitos que los análisis estructurados no ven.
            Por ejemplo: varios consultores describen distintas situaciones que
            en realidad son variantes del mismo problema.
  Acción:   Usar como input para proponer nuevas ramas en árboles o secciones
            en módulos. No actuar directamente — requiere revisión humana.

summary.txt  (en raíz)
  Qué es:   Resumen legible de todos los reportes de case_library en un solo
            texto. Generado automáticamente, formato para lectura rápida.
  Para qué: Vista ejecutiva del estado de la case_library. Apto para compartir
            en una revisión de equipo sin necesidad de abrir los YAMLs.
  Acción:   Solo lectura. Si necesitás compartirlo, copiá el contenido o el
            archivo.

── reports/feedback/ ─────────────────────────────────────────────────────────

ia_feedback.yaml
  Qué es:   Consolidado de likes (👍) y dislikes (👎) sobre outputs generados
            por la IA (acciones de sección, acciones rápidas y chat libre).
            Incluye tasa de satisfacción por módulo y por acción, y lista de
            tópicos con alto índice de dislike.
  Para qué: Señala qué contenido o tipo de análisis no está siendo útil para
            los consultores.
  Acción:   Tópicos con dislike >= 40% y al menos 2 ratings → candidatos a
            mejorar el contenido del .md o ajustar el prompt de esa acción.
            Los comentarios negativos son el input más valioso — leerlos antes
            de decidir qué cambiar.

tree_feedback.yaml
  Qué es:   Feedback registrado después de completar un árbol de decisión:
            rating (👍/👎), outcome ("anduvo" / "no anduvo") y lecciones,
            agrupado por árbol y leaf.
  Para qué: Complementa leaf_outcomes.yaml con la percepción subjetiva del
            consultor, no solo el resultado objetivo.
  Acción:   Leaves con flag "revisar" (tasa de éxito < 70%) → prioridad de
            revisión del árbol. Las lecciones registradas acá también deben
            cruzarse con lessons.yaml.

missing_trees.yaml
  Qué es:   Árboles solicitados por consultores que no existen en la
            plataforma, agrupados por módulo y ordenados por frecuencia.
            Incluye el contexto descrito por el consultor en cada solicitud.
  Para qué: Es la señal más directa de qué árboles construir primero.
            Prioridad "alta" = 3+ solicitudes, "media" = 2, "baja" = 1.
  Acción:   Las solicitudes de alta prioridad deberían traducirse en nuevos
            YAMLs de árboles en la carpeta trees/. Las de baja prioridad pueden
            cubrirse con contenido en el .md mientras se construye el árbol.
  Nota:     Una solicitud no implica que el árbol sea viable — requiere
            validación metodológica antes de construirlo.

summary.txt  (en feedback/)
  Qué es:   Resumen legible de los tres reportes de feedback en un solo texto.
  Para qué: Vista rápida del estado del feedback. Apto para revisión de equipo.
  Acción:   Solo lectura.

── reports/content_proposals/ ───────────────────────────────────────────────

proposals.yaml
  Qué es:   Propuestas concretas de mejora al contenido de los módulos (.md),
            generadas cruzando señales de feedback_log/ y case_library/.
            Cada propuesta incluye: módulo, sección/tópico, tipo de acción,
            prioridad, descripción de la mejora y señales que la originaron.
            En modo API Real también incluye un borrador de texto redactado
            por el LLM listo para revisar.
  Para qué: Es el puente entre los datos analíticos y la acción editorial.
            Convierte señales dispersas en tareas concretas de mejora.
  Acción:   Revisar cada propuesta, validar si el contexto es correcto,
            y aplicar los cambios al .md correspondiente manualmente.
            El campo "draft_text" (cuando existe) es un borrador — siempre
            revisar antes de incorporar, el LLM puede equivocarse en detalles
            técnicos específicos de SAP.
  Importante: Las propuestas son sugerencias, no instrucciones automáticas.
            Nunca se aplican solas al contenido — requieren revisión humana.

summary.txt  (en content_proposals/)
  Qué es:   Resumen legible de proposals.yaml, organizado por módulo y
            prioridad. Incluye los borradores de texto si fueron generados.
  Para qué: Vista rápida para priorizar el trabajo editorial.
  Acción:   Solo lectura. Empezar siempre por las propuestas de prioridad
            "alta" antes de avanzar con las de "media" o "baja".

--------------------------------------------------------------------------------
TIPOS DE SEÑAL Y QUÉ SIGNIFICAN
--------------------------------------------------------------------------------

  high_dislike       → Sección con >= 40% de dislikes en outputs IA.
                       Acción: reescribir o enriquecer la sección.

  negative_comment   → Comentarios negativos explícitos aunque el ratio
                       no sea crítico. Acción: mejorar la sección según
                       el feedback recibido.

  missing_tree_gap   → Árbol solicitado frecuentemente que no existe.
                       Acción: agregar sección conceptual como cobertura
                       provisional y planificar el árbol.

  lesson_to_add      → Lección registrada en un caso real que no está
                       documentada en el módulo. Acción: incorporarla como
                       nota o advertencia en la sección relevante.

  uncharted_gap      → Ruta no contemplada en un árbol con contexto descrito.
                       Acción: documentar el escenario en el módulo y evaluar
                       agregar la rama al árbol.

  bad_outcome_leaf   → Leaf con outcome negativo registrado.
                       Acción: revisar la recomendación del leaf y agregar
                       condiciones o advertencias en el módulo.

--------------------------------------------------------------------------------
FLUJO DE TRABAJO RECOMENDADO
--------------------------------------------------------------------------------

1. RECOLECTAR
   Usar la plataforma normalmente. Los consultores registran casos, dejan
   feedback y reportan árboles faltantes durante el uso cotidiano.
   No hay carga manual — los datos se acumulan solos.

2. ANALIZAR  (periódico — sugerido: mensual o cuando haya 5+ casos nuevos)
   Desde tab Analítica → ejecutar en orden:
     a. "Analizar casos"    → genera reportes de case_library
     b. "Analizar feedback" → genera reportes de feedback_log
     c. "Proponer mejoras"  → cruza ambas fuentes y genera propuestas

   En Simulación: análisis local, sin LLM.
   En API Real:   análisis completo con componente semántico (consume créditos).

3. REVISAR
   Leer reports/summary.txt y reports/feedback/summary.txt primero.
   Luego abrir reports/content_proposals/summary.txt para ver las propuestas
   priorizadas.

4. ACTUAR
   Para cada propuesta de prioridad "alta":
     - Validar el contexto y la señal que la originó
     - Aplicar el cambio al .md correspondiente en content/
     - Si hay draft_text, usarlo como punto de partida — no copiar ciego
     - Si la señal viene de un árbol, evaluar también el YAML en trees/

   Para missing_trees de prioridad "alta":
     - Crear el YAML del árbol en trees/ siguiendo el schema existente
     - O agregar una sección provisional en el .md hasta que el árbol esté listo

5. ITERAR
   Volver al paso 1. Con el tiempo los reportes reflejan mejoras reales:
   bajan los dislikes, bajan los uncharted, aparecen nuevas lecciones.

--------------------------------------------------------------------------------
QUÉ NO HACER
--------------------------------------------------------------------------------

  - No editar los archivos de reports/ manualmente. Se sobreescriben en
    la próxima ejecución y los cambios se pierden.

  - No copiar draft_text de proposals.yaml al .md sin revisión. El LLM
    puede cometer errores técnicos en contextos SAP específicos.

  - No borrar case_library/ ni feedback_log/ para "limpiar". Son el
    historial que alimenta todos los análisis. Si querés archivar casos
    viejos, moverlos a una subcarpeta case_library/archive/.

  - No actuar sobre propuestas de prioridad "baja" antes de resolver
    las de "alta" y "media". El sistema prioriza por frecuencia e impacto.

  - No ignorar los comentarios negativos en ia_feedback.yaml aunque el
    ratio total sea aceptable. Un comentario preciso vale más que diez
    likes genéricos.

================================================================================
