"""
ui/header.py — Header de la aplicación.

Renderiza: logo animado | título + subtítulo | navegación + Roadmap + selector de contenido.
Devuelve si Roadmap fue clickeado y si se pidió navegación entre módulos.
"""

from __future__ import annotations

import textwrap
import streamlit as st
import streamlit.components.v1 as components

from config import APP_TITLE, APP_SUBTITLE, ASSETS_DIR, LOGO_DANCING, ROADMAP_ITEMS
from core.content_registry import (
    CONTENT_PACK_SESSION_KEY,
    active_content_pack,
    available_content_packs,
    content_pack_short_label,
)
from core.state import AppState


#  Roadmap
def _roadmap_cards_html() -> str:
    cards = []
    for title, done, icon, text in ROADMAP_ITEMS:
        dot = "dw-rm-dot--on" if done else "dw-rm-dot--off"
        cards.append(textwrap.dedent(f"""
        <div class="dw-rm-item">
          <div class="dw-rm-head">
            <span class="dw-rm-dot {dot}"></span>
            <span class="dw-rm-title">{title}</span>
          </div>
          <div class="dw-rm-card">
            <span class="dw-rm-icon">{icon}</span>
            <span>{text}</span>
          </div>
        </div>
        """).strip())
    return "<div class='dw-rm-wrap'>" + "\n".join(cards) + "</div>"


def render_roadmap_modal() -> None:
    """Abre el diálogo del roadmap. Llamar sólo si el botón fue clickeado."""
    if not hasattr(st, "dialog"):
        st.info("Tu versión de Streamlit no soporta st.dialog. Mostrando aquí:")
        _render_roadmap_body()
        return

    @st.dialog("Roadmap", width="large")
    def _dialog():
        _render_roadmap_body()

    _dialog()


def _render_roadmap_body() -> None:
    st.markdown(
        "<div class='dw-rm-intro'>Esta plataforma evoluciona desde una <b>PoC de copiloto metodológico</b> "
        "hacia una <b>plataforma de inteligencia arquitectónica para SAP BDC | Datasphere</b>.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(_roadmap_cards_html(), unsafe_allow_html=True)
    st.markdown(
        "<div class='dw-rm-help'>Roadmap sujeto a cambios y/o prioridades I+D · Danwor Transversal</div>",
        unsafe_allow_html=True,
    )


#  Content pack selector callback
def _on_content_pack_change() -> None:
    """Aisla la navegacion y el chat al cambiar de corpus metodologico."""
    for key in (
        "module_selector", "appendix_selector", "selected_topic",
        "selected_section_md", "decision_panel", "section_panel_open",
        "tree_panel_open", "tree_selector", "selected_topic_from_anchor",
        "_scroll_to_slug", "_tree_suggestion", "_requested_tree_id",
        "_requested_tree_title", "_combined_suggestion",
        "_section_suggestion", "_asset_suggestion",
        "_asset_multi_suggestion", "_asset_section_suggestion",
        "_pending_llm_direct", "_prev_module", "_tools_panel_module",
        "_activity_last_module",
    ):
        st.session_state.pop(key, None)
    AppState.clear_chat()
    st.session_state["_scroll_content_to_top"] = True


#  Main render
def render_header(
    is_dev: bool,
    *,
    module_names: list[str] | None = None,
    selected_stem: str = "",
    show_mode_selector: bool | None = None,
) -> tuple[bool, int | None]:
    """
    Renderiza el header completo.
    Devuelve:
      (roadmap_clicked, module_nav_delta)
    Donde module_nav_delta puede ser -1, +1 o None.
    """
    # Columnas: logo+título (combinado) | controles
    c1, c2 = st.columns([0.74, 0.26], vertical_alignment="center")

    #  Logo animado con título y subtítulo integrados
    with c1:
        if LOGO_DANCING.exists():
            components.html(
                LOGO_DANCING.read_text(encoding="utf-8"),
                height=90,
                scrolling=False,
            )
        else:
            st.markdown(
                f'<div style="font-size:18px;font-weight:700;line-height:1.2">{APP_TITLE}</div>'
                f'<div style="font-size:14px;opacity:.7;margin-top:2px">{APP_SUBTITLE}</div>',
                unsafe_allow_html=True,
            )

    #  Roadmap + Modo (misma fila, mismo alto)
    roadmap_clicked = False
    module_nav_delta: int | None = None
    module_names = module_names or []
    show_mode_selector = AppState.debug_mode() if show_mode_selector is None else show_mode_selector
    can_nav = bool(module_names and selected_stem in module_names)
    current_idx = module_names.index(selected_stem) if can_nav else 0
    can_prev = can_nav and current_idx > 0
    can_next = can_nav and current_idx < len(module_names) - 1

    with c2:
        st.markdown(
            """
            <style>
            /* Forzar mismo alto en botón y selectbox del header */
            div[data-testid="stHorizontalBlock"]:has(> div > div[data-testid="stButton"])
              button {
                height: 38px !important;
                line-height: 1 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        if show_mode_selector:
            r1, r_prev, r_next, r2 = st.columns([0.42, 0.13, 0.13, 0.50], vertical_alignment="center")
        else:
            r_spacer, r1, r_prev, r_next = st.columns([0.50, 0.42, 0.13, 0.13], vertical_alignment="center")
            with r_spacer:
                st.empty()
        with r1:
            roadmap_clicked = st.button(
                "Roadmap",
                use_container_width=True,
                key="btn_roadmap",
            )
        with r_prev:
            if st.button(
                "←",
                use_container_width=True,
                key="btn_module_prev",
                help="Módulo anterior",
                disabled=not can_prev,
            ):
                module_nav_delta = -1
        with r_next:
            if st.button(
                "→",
                use_container_width=True,
                key="btn_module_next",
                help="Módulo siguiente",
                disabled=not can_next,
            ):
                module_nav_delta = 1
        if show_mode_selector:
            with r2:
                packs = available_content_packs()
                if st.session_state.get(CONTENT_PACK_SESSION_KEY) not in packs:
                    st.session_state[CONTENT_PACK_SESSION_KEY] = active_content_pack()
                st.selectbox(
                    "Contenido",
                    options=packs,
                    format_func=content_pack_short_label,
                    key=CONTENT_PACK_SESSION_KEY,
                    label_visibility="collapsed",
                    on_change=_on_content_pack_change,
                )

    return roadmap_clicked, module_nav_delta
