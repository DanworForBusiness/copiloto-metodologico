"""
ui/header.py — Header de la aplicación.

Renderiza: logo animado | título + subtítulo | navegación + Roadmap + selector de modo.
Devuelve si Roadmap fue clickeado y si se pidió navegación entre módulos.
"""

from __future__ import annotations

import textwrap
import streamlit as st
import streamlit.components.v1 as components

from config import APP_TITLE, APP_SUBTITLE, ASSETS_DIR, LOGO_DANCING, ROADMAP_ITEMS
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
        "<div class='dw-rm-help'>Roadmap sujeto a cambios y/o prioridades I+D</div>",
        unsafe_allow_html=True,
    )


#  Mode selector callback 
def _on_mode_change() -> None:
    mode = st.session_state.get("mode_selector", "Simulación")
    AppState.set_execution_mode(mode)


#  Main render
def render_header(
    is_dev: bool,
    *,
    module_names: list[str] | None = None,
    selected_stem: str = "",
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
        if is_dev:
            r1, r_prev, r_next, r2 = st.columns([0.42, 0.13, 0.13, 0.50], vertical_alignment="center")
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
            with r2:
                st.selectbox(
                    "Modo",
                    options=["Simulación", "API Real"],
                    index=0 if AppState.is_simulation() else 1,
                    key="mode_selector",
                    label_visibility="collapsed",
                    on_change=_on_mode_change,
                )
            # Toggle de debug movido a app.py para que aparezca siempre
        else:
            AppState.set_execution_mode("API Real")
            r1, r_prev, r_next = st.columns([0.60, 0.20, 0.20], vertical_alignment="center")
            with r1:
                roadmap_clicked = st.button(
                    "Roadmap",
                    use_container_width=True,
                    key="btn_roadmap",
                )
            with r_prev:
                if st.button("←", use_container_width=True, key="btn_module_prev", help="Módulo anterior", disabled=not can_prev):
                    module_nav_delta = -1
            with r_next:
                if st.button("→", use_container_width=True, key="btn_module_next", help="Módulo siguiente", disabled=not can_next):
                    module_nav_delta = 1

    return roadmap_clicked, module_nav_delta
