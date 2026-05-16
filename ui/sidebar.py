"""
ui/sidebar.py - Sidebar de navegación y acciones rápidas.

Renderiza:
  - Selector de módulo
  - Selector secundario de apéndices técnicos
  - Acciones rápidas (IA)

Devuelve:
  - stem del módulo/apéndice seleccionado
  - nombre de la acción rápida clickeada (o None)
"""

from __future__ import annotations

import streamlit as st

from config import IA_ENABLED_MODULES, SECTION_NUMBER_WIDTH, ia_enabled_for
from core.prompts import QUICK_ACTIONS
from core.state import AppState, K_MODULE_SELECTOR


APPENDICES_SENTINEL = "__appendices__"
APPENDICES_LABEL = "Apéndices Técnicos"
K_APPENDIX_SELECTOR = "appendix_selector"


def render_sidebar(
    module_names: list[str],
    module_labels: dict[str, str],
    appendix_names: list[str] | None = None,
    appendix_labels: dict[str, str] | None = None,
) -> tuple[str, str | None, str | None]:
    """
    Returns:
        (selected_stem, quick_action_clicked_or_None, assessment_clicked_or_None)
    """
    st.sidebar.markdown(
        """
        <div class="dw-side-hero">
          <div class="dw-side-eyebrow">Arquitecto Danwor</div>
          <div class="dw-side-title">Centro de decisión</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    appendix_names = appendix_names or []
    appendix_labels = appendix_labels or {}
    main_options = list(module_names)
    if appendix_names:
        main_options.append(APPENDICES_SENTINEL)
    appendices_label = f"{len(module_names):0{SECTION_NUMBER_WIDTH}d} · {APPENDICES_LABEL}"

    # Si el módulo activo real es un apéndice, el selector principal muestra
    # la categoría editorial y el selector secundario conserva el archivo real.
    current_module = st.session_state.get(K_MODULE_SELECTOR)
    if current_module in appendix_names:
        st.session_state[K_MODULE_SELECTOR] = APPENDICES_SENTINEL
        st.session_state[K_APPENDIX_SELECTOR] = current_module

    # Default al primer módulo si no hay nada guardado o si cambió la colección.
    if K_MODULE_SELECTOR not in st.session_state or st.session_state[K_MODULE_SELECTOR] not in main_options:
        st.session_state[K_MODULE_SELECTOR] = module_names[0]

    selected_main = st.sidebar.selectbox(
        "Módulo activo",
        main_options,
        format_func=lambda s: appendices_label if s == APPENDICES_SENTINEL else module_labels.get(s, s),
        key=K_MODULE_SELECTOR,
    )

    if selected_main == APPENDICES_SENTINEL and appendix_names:
        if st.session_state.get(K_APPENDIX_SELECTOR) not in appendix_names:
            st.session_state[K_APPENDIX_SELECTOR] = appendix_names[0]
        selected = st.sidebar.selectbox(
            "Apéndices disponibles",
            appendix_names,
            format_func=lambda s: appendix_labels.get(s, s),
            key=K_APPENDIX_SELECTOR,
        )
    else:
        selected = selected_main

    ia_enabled = ia_enabled_for(selected)

    st.sidebar.markdown("<div class='dw-side-section'>Acciones rápidas</div>", unsafe_allow_html=True)
    action_clicked: str | None = None
    for label in QUICK_ACTIONS:
        if st.sidebar.button(label, use_container_width=True, disabled=not ia_enabled):
            action_clicked = label
            AppState.set_active_tools_tab(2)
            AppState.open_tools_panel()

    if st.session_state.get("debug_mode", False):
        st.sidebar.markdown("<div class='dw-side-section'>Assessments</div>", unsafe_allow_html=True)
    assessment_clicked: str | None = None
    if not st.session_state.get("debug_mode", False):
        return selected, action_clicked, assessment_clicked
    if st.sidebar.button("📋 BDC", use_container_width=True, key="sidebar_assess_bdc", disabled=not ia_enabled):
        assessment_clicked = "bdc"
        AppState.open_tools_panel()

    return selected, action_clicked, assessment_clicked
