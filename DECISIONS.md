# Architectural Decision Records — Copiloto Metodológico Danwor

Este documento registra las decisiones arquitecturales no obvias del sistema.
Antes de modificar cualquier componente mencionado acá, leer la decisión correspondiente.
Última actualización: Marzo 2025

---

## ADR-001 — session_state en handlers de botón con st.rerun()

**Contexto**
Streamlit descarta los cambios de st.session_state realizados dentro del handler
de un st.button() cuando ese mismo handler llama st.rerun(). Esto es un
comportamiento documentado en versiones recientes de Streamlit.

**Decisión**
Nunca modificar session_state en un handler de botón si ese handler también
llama st.rerun(). En su lugar, usar dicts persistentes como vectores entre renders:
- _pending_classify — transporta prompt + contexto al ciclo de clasificación
- _pending_open_tree — transporta el árbol seleccionado al ciclo de navegación
- _pending_llm_direct — transporta prompt al ciclo de respuesta LLM

**Consecuencia**
Todo cambio de estado que necesite sobrevivir un rerun debe viajar dentro de uno
de estos dicts, que se procesan en el bloque final de render_tools_panel() donde
session_state sí persiste correctamente.

**No revertir:** Cualquier simplificación que ponga lógica de estado directamente
en handlers de botón va a producir bugs silenciosos de estado perdido.

---

## ADR-002 — st.query_params como vector de navegación

**Contexto**
st.session_state tampoco persiste confiablemente para cambios de tab/sección
iniciados desde un handler de botón.

**Decisión**
Usar st.query_params para transportar información de navegación entre el handler
del botón y el próximo render. Específicamente:
- ?section=slug — navega a una sección del contenido
- ?section_nav=1 — indica que la navegación viene del asistente

app.py procesa y limpia estos params en cada render.

**No revertir:** No reemplazar este mecanismo por session_state directo sin
validar exhaustivamente los 4 paths de navegación.

---

## ADR-003 — LLM en thread secundario sin session_state

**Contexto**
El LLM corre en un thread secundario para mostrar un spinner animado durante
la espera. Streamlit no provee ScriptRunContext a threads secundarios.

**Decisión**
LLMGateway._call_api() es completamente thread-safe: no accede a session_state.
El modo simulación/real se evalúa en el hilo principal antes de lanzar el thread.

**No revertir:** No mover lógica que dependa de session_state dentro de _call_api().

---

## ADR-004 — Nivel 2 de detección de sección deshabilitado

**Contexto**
El nivel 2 (búsqueda por texto libre) fue deshabilitado luego de validación
empírica — genera falsos positivos frecuentes en dominios técnicos.

**Decisión**
El código está comentado (no eliminado) en _find_matching_section() de
ui/tools_panel.py con instrucciones para reactivarlo.

**Para reactivar:** Descomentar el bloque y subir MIN_SECTION_WORDS a 4+.
Validar con 20+ queries representativas por módulo antes de deploy.

---

## ADR-005 — AppState como única fuente de verdad del estado

**Decisión**
Todo acceso a session_state pasa por AppState en core/state.py.
No acceder directamente a st.session_state desde ui/.

---

## ADR-006 — section_meta como mecanismo principal de routing

**Decisión**
section_meta es inclusivo — suma precisión sobre el contenido existente.
section_exclude debe usarse con moderación. Si se necesitan muchos excludes,
los keywords del section_meta son demasiado amplios.

**Extensión 2026 — assets visuales y semántica**
Los assets embebidos con `{{html_embed: ...}}` son vistas visuales, no la fuente
semántica principal del copiloto. Para evitar que el conocimiento quede encerrado
en HTML, imagen o canvas, usar este contrato:

- `{{section_meta: keywords="..."}}`: señales cortas para routing/detección.
- `{{semantic_summary}}...{{/semantic_summary}}`: conocimiento invisible en UI,
  pero consumible por el LLM cuando un visual contiene contenido técnico.
- `{{section_mode: visual_only}}`: marca editorial para contenido institucional
  o introductorio que no debe intervenir en la conversación IA.
- `guardrails` en frontmatter: posturas normativas Danwor que deben entrar en
  conversación libre y acciones de sección. Usarlo para evitar que un anti-pattern
  sea suavizado como alternativa viable.

**No revertir:** No parsear HTML embebido como fuente de verdad del asistente.
Si un visual contiene conocimiento técnico, agregar `semantic_summary` en el
Markdown que lo invoca. Si el feedback corrige una postura metodológica, evaluar
primero `scope` o `guardrails` antes de endurecer prompts globales.

---

## ADR-007 — Eventos de sistema en el historial del chat

**Decisión**
Rol event para acciones del usuario (árbol completado/interrumpido, asset consultado).
Se renderizan como líneas neutras sin burbuja ni rating.
No convertir en burbujas de asistente.

---

## ADR-008 — Watchdog file watcher en Windows + Python 3.14

**Decisión**
Configurar .streamlit/config.toml con fileWatcherType = poll para evitar deadlock.
Para producción: fileWatcherType = none.

---

## ADR-009 — Numeración visual automática global de secciones

**Decisión**
Los números visibles de secciones (`[01]`, `[02]`, etc.) son presentación, no
identidad semántica del Markdown. Los headings `##` se numeran globalmente desde
`00_Intro` en adelante durante el render. El ancho del número se controla con
`SECTION_NUMBER_WIDTH` en `config.py` (`2` => `[01]`, `3` => `[001]`).

**Extensión**
Los headings viejos que ya traen `[NN]` siguen siendo compatibles para encontrar
fragmentos, pero esos números se ignoran visualmente. `{{section_id: NN}}` queda
como tag legacy y tampoco gobierna la numeración visible.

**No revertir:** No usar el número manual como clave lógica del copiloto. El
routing debe seguir apoyándose en título normalizado, `section_meta`,
`semantic_summary`, `scope` y `guardrails`.

---

## ADR-010 — Router transversal liviano antes del fallback LLM

**Contexto**
La separación modular reduce alucinaciones y mantiene al copiloto enfocado, pero
el usuario puede preguntar un tema desde el módulo equivocado. Si el módulo
activo no contiene el tema y el sistema cae directamente al LLM, se pierde valor
metodológico y aparecen respuestas genéricas.

**Decisión**
Mantener el módulo activo como primera fuente de verdad. Si no hay árbol, asset
o sección local relevante, ejecutar un router transversal liviano sobre metadata
de otros módulos/apéndices antes del fallback LLM.

El router transversal puede mirar:
- `scope` del frontmatter
- headings normalizados
- `section_meta`
- `semantic_summary`

El router transversal no debe enviar todo el corpus al LLM ni usar el cuerpo
completo de todos los módulos como contexto. Si encuentra un match claro, ofrece
navegar al módulo/sección correspondiente o responder usando esa sección como
marco contextual.

**Principio rector**
El usuario puede preguntar desde cualquier módulo; el copiloto debe saber cuándo
contestar desde el módulo actual, cuándo derivar a otro módulo y cuándo admitir
que está fuera del marco.

**Consecuencia**
Las mejoras de conocimiento deben capitalizarse primero en metadata y contenido:
`section_meta`, `semantic_summary`, `scope`, `guardrails` e `intent_assets`.
Solo después evaluar cambios en prompts globales o lógica de ranking.

**No revertir:** No reemplazar este router por un RAG global indiscriminado ni
por fallback LLM directo. La modularidad sigue siendo una barrera deliberada de
precisión; el router transversal es una capa de derivación, no una fusión de
todos los módulos.
