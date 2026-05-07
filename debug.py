"""
debug.py — Snippets de diagnóstico para el Copiloto Arquitecto Danwor.

USO: copiar el snippet relevante al lugar indicado en el código fuente,
     ejecutar, observar output en la UI, luego remover.

Cada snippet indica:
  - ARCHIVO     → dónde pegarlo
  - UBICACIÓN   → línea / función de referencia
  - QUÉ muestra → qué información expone
"""

# ══════════════════════════════════════════════════════════════════════════════
# SNIPPET 1 — Diagnóstico de detección de intent en _run_chat
# ══════════════════════════════════════════════════════════════════════════════
#
# ARCHIVO:    ui/tools_panel.py
# UBICACIÓN:  dentro de _run_chat(), justo después de las dos líneas:
#               matched_tree    = _find_matching_tree(...)
#               matched_section = _find_matching_section(...)
#
# QUÉ muestra: qué árbol y sección matchearon (o None) para la pregunta enviada.
#              Útil para calibrar keywords de árboles y section_meta de secciones.
#
# --- PEGAR AQUÍ ---
#
# import streamlit as st
# st.session_state["_debug_match"] = {
#     "tree":    matched_tree.get("title", "") if matched_tree else None,
#     "section": matched_section.get("heading", "") if matched_section else None,
# }
#
# --- Y EN EL RENDER (dentro del st.container(height=420), antes de _combined_suggestion) ---
#
# if st.session_state.get("_debug_match"):
#     st.json(st.session_state["_debug_match"])
#
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# SNIPPET 2 — Inspeccionar scoring de árboles candidatos
# ══════════════════════════════════════════════════════════════════════════════
#
# ARCHIVO:    ui/tools_panel.py
# UBICACIÓN:  dentro de _find_matching_tree(), al final del loop de scoring,
#             antes del `if scored:` final
#
# QUÉ muestra: score y fuentes de cada árbol evaluado para la pregunta.
#              Útil para entender por qué un árbol gana sobre otro.
#
# --- PEGAR AQUÍ (reemplaza temporalmente el `if scored:`) ---
#
# import streamlit as st
# st.session_state["_debug_tree_scores"] = [
#     {"title": t.get("title",""), "score": s, "sources": list(src)}
#     for s, t, src in [(sc, tr, set()) for sc, tr in scored]  # ajustar si cambia estructura
# ]
#
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# SNIPPET 3 — Inspeccionar palabras extraídas de la pregunta (Nivel 2 sección)
# ══════════════════════════════════════════════════════════════════════════════
#
# ARCHIVO:    ui/tools_panel.py
# UBICACIÓN:  dentro de _find_matching_section(), después de `q_words = [...]`
#
# QUÉ muestra: las palabras que el fallback de texto usa para buscar en secciones.
#              Útil para detectar si palabras clave están siendo filtradas por
#              SECTION_STOPWORDS o por el filtro de longitud mínima.
#
# --- PEGAR AQUÍ ---
#
# import streamlit as st
# st.session_state["_debug_section_words"] = {
#     "q_words": q_words,
#     "min_words_threshold": MIN_WORDS,
# }
#
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# SNIPPET 4 — Ver prompt completo enviado al LLM
# ══════════════════════════════════════════════════════════════════════════════
#
# ARCHIVO:    ui/tools_panel.py
# UBICACIÓN:  dentro de _llm_call_direct(), justo antes de `_t.start()`
#
# QUÉ muestra: el prompt y contexto completos que se envían al modelo.
#              Útil para detectar truncamiento, contexto incorrecto o
#              instrucciones de modo mal formadas.
#
# --- PEGAR AQUÍ ---
#
# import streamlit as st
# with st.expander("🔎 DEBUG — prompt enviado al LLM", expanded=False):
#     st.code(f"SYSTEM:\n{_sys}\n\nCONTEXT (primeros 500 chars):\n{context[:500]}\n\nPROMPT:\n{prompt}", language="markdown")
#
# ══════════════════════════════════════════════════════════════════════════════