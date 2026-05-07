"""
ui/sidebar.py — Sidebar de navegación y acciones rápidas.

Renderiza:
  - Selector de módulo
  - Acciones rápidas (IA)

Devuelve:
  - stem del módulo seleccionado
  - nombre de la acción rápida clickeada (o None)
"""

from __future__ import annotations

import streamlit as st

from config import IA_ENABLED_MODULES
from core.prompts import QUICK_ACTIONS
from core.state import AppState, K_MODULE_SELECTOR


def render_sidebar(module_names: list[str], module_labels: dict[str, str]) -> tuple[str, str | None, str | None]:
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

    # Default al primer módulo si no hay nada guardado
    if K_MODULE_SELECTOR not in st.session_state or st.session_state[K_MODULE_SELECTOR] not in module_names:
        st.session_state[K_MODULE_SELECTOR] = module_names[0]

    selected = st.sidebar.selectbox(
        "Módulo activo",
        module_names,
        format_func=lambda s: module_labels.get(s, s),
        key=K_MODULE_SELECTOR,
    )
    ia_enabled = selected in IA_ENABLED_MODULES

    st.sidebar.markdown("<div class='dw-side-section'>Acciones rápidas</div>", unsafe_allow_html=True)
    action_clicked: str | None = None
    for label in QUICK_ACTIONS:
        if st.sidebar.button(label, use_container_width=True, disabled=not ia_enabled):
            action_clicked = label
            AppState.set_active_tools_tab(2)

    st.sidebar.markdown("<div class='dw-side-section'>Assessments</div>", unsafe_allow_html=True)
    assessment_clicked: str | None = None
    if st.sidebar.button("📋 BDC", use_container_width=True, key="sidebar_assess_bdc", disabled=not ia_enabled):
        assessment_clicked = "bdc"

    return selected, action_clicked, assessment_clicked
