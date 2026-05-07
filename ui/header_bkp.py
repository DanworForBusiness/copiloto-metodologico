"""
ui/header.py — Header de la aplicación.

Estrategia visual:
  - Banner oscuro full-width: st.markdown() con CSS inyectado en el DOM de Streamlit.
    position:fixed al top → ocupa el ancho completo del viewport.
  - Logo animado: components.html() en su propia fila, exactamente igual que antes.
  - Controles (Roadmap + Modo): fila nativa Streamlit debajo del logo.

La separación logo / banner evita iframe-dentro-de-iframe.
"""

from __future__ import annotations

import textwrap
import streamlit as st
import streamlit.components.v1 as components

from config import APP_TITLE, APP_SUBTITLE, ASSETS_DIR, LOGO_DANCING, ROADMAP_ITEMS
from core.state import AppState


# ── BANNER ───────────────────────────────────────────────────────────────────
# Barra oscura fixed, 48px de alto, ancho real del viewport.
# Tokens alineados con danwor.com/Metodologia/*.html

_BANNER_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
.site-header {
  background: var(--dw-black);
  padding: 0 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  position: sticky;
  top: 0;
  z-index: 100;
  border-bottom: 1px solid rgba(245,166,35,0.12);
}

.logo { display: flex; align-items: center; gap: 10px; text-decoration: none; height: 100%;}
.logo-img {
  height: 36px;
  width: auto;
  object-fit: contain;
  display: block;
}
.dw-topbar {
  position: fixed;
  top: 0; left: 0; right: 0;
  height: 48px;
  z-index: 999999;
  background: #0a0a0a;
  border-bottom: 1px solid rgba(245,166,35,0.18);
  display: flex;
  align-items: center;
  padding: 0 20px 0 16px;
  font-family: 'DM Sans', 'Source Sans 3', sans-serif;
  /* gradient line at bottom */
  box-shadow: 0 2px 0 0 rgba(201,125,16,0.35);
}

.dw-topbar-mark {
  width: 26px; height: 26px;
  background: linear-gradient(135deg, #f5a623, #c97d10);
  border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 11px; color: #000;
  letter-spacing: -0.3px; flex-shrink: 0; margin-right: 9px;
}

.dw-topbar-wordmark {
  display: flex; flex-direction: column; line-height: 1;
  margin-right: 18px; flex-shrink: 0;
}
.dw-topbar-name {
  color: #fff; font-size: 12.5px; font-weight: 700;
  letter-spacing: 0.4px; line-height: 1;
}
.dw-topbar-tagline {
  color: rgba(245,166,35,0.6); font-size: 8px; font-weight: 400;
  letter-spacing: 1.8px; text-transform: uppercase; margin-top: 2.5px;
}

.dw-topbar-sep {
  width: 1px; height: 26px;
  background: rgba(255,255,255,0.1);
  margin: 0 16px; flex-shrink: 0;
}

.dw-topbar-title {
  display: flex; flex-direction: column; flex: 1;
  min-width: 0; overflow: hidden; line-height: 1;
}
.dw-topbar-app {
  color: #fff; font-size: 13px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  letter-spacing: -0.1px;
}
.dw-topbar-sub {
  color: rgba(255,255,255,0.38); font-size: 10.5px; font-weight: 400;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  letter-spacing: 0.15px; margin-top: 3px;
}

.dw-topbar-badge {
  flex-shrink: 0; margin-left: 16px;
  display: inline-flex; align-items: center; gap: 5px;
  background: rgba(245,166,35,0.1);
  border: 1px solid rgba(245,166,35,0.22);
  color: #f5a623; font-size: 9px; font-weight: 600;
  padding: 3px 9px; border-radius: 20px;
  letter-spacing: 0.6px; text-transform: uppercase;
}
.dw-topbar-badge::before {
  content: ''; width: 5px; height: 5px;
  border-radius: 50%; background: #f5a623; flex-shrink: 0;
}

/* Push Streamlit content below the bar */
.dw-topbar-spacer { height: 52px; width: 100%; }
</style>
"""

_BANNER_HTML = f"""
<div class="dw-topbar">
  <div class="logo">
    <img 
    src="https://danwor.com/logo_dfb" 
    onerror="this.src='logo_local.png'"
    alt="Danwor Data & AI for Business"
    class="logo-img"
  />
  </div>
  <div class="dw-topbar-sep"></div>
  <div class="dw-topbar-title">
    <span class="dw-topbar-app">{APP_TITLE}</span>
    <span class="dw-topbar-sub">{APP_SUBTITLE}</span>
  </div>
  <div class="dw-topbar-badge">Beta interna</div>
</div>
"""


# ── ROADMAP ──────────────────────────────────────────────────────────────────

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
    if not hasattr(st, "dialog"):
        st.info("Tu versión de Streamlit no soporta st.dialog.")
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


# ── MODE SELECTOR ─────────────────────────────────────────────────────────────

def _on_mode_change() -> None:
    mode = st.session_state.get("mode_selector", "Simulación")
    AppState.set_execution_mode(mode)


# ── MAIN RENDER ───────────────────────────────────────────────────────────────

def render_header(is_dev: bool) -> bool:
    """
    Renderiza el header.

    Estructura:
      1. Banner oscuro fixed top — st.markdown() → DOM nativo → full-width real
      2. Logo animado + controles — igual que antes (components.html + columns)

    Devuelve True si el botón Roadmap fue clickeado.
    """

    # ── 1. BANNER FIXED TOP ───────────────────────────────────────────────────
    # CSS + HTML en el DOM de Streamlit. position:fixed → width = viewport real.
    # El spacer empuja el contenido debajo de la barra.
    st.markdown(_BANNER_CSS + _BANNER_HTML, unsafe_allow_html=True)

    # ── 2. LOGO ANIMADO + CONTROLES ───────────────────────────────────────────
    # Exactamente igual que el header original — no se cambia nada aquí.
    c1, c2 = st.columns([0.80, 0.20], vertical_alignment="center")

    with c1:
        if LOGO_DANCING.exists():
            components.html(
                LOGO_DANCING.read_text(encoding="utf-8"),
                height=90,
                scrolling=False,
            )
        else:
            st.markdown(
                f'<div style="font-size:17px;font-weight:700;color:#1a2535;line-height:1.2">{APP_TITLE}</div>'
                f'<div style="font-size:13px;opacity:.6;margin-top:2px;color:#6b7a8d">{APP_SUBTITLE}</div>',
                unsafe_allow_html=True,
            )

    # ── 3. CONTROLES ──────────────────────────────────────────────────────────
    roadmap_clicked = False
    with c2:
        st.markdown("""
        <style>
        div[data-testid="stHorizontalBlock"]:has(> div > div[data-testid="stButton"])
          button { height: 36px !important; line-height: 1 !important; }
        </style>
        """, unsafe_allow_html=True)

        if is_dev:
            r1, r2 = st.columns([0.52, 0.48], vertical_alignment="center")
            with r1:
                roadmap_clicked = st.button(
                    "Roadmap",
                    use_container_width=True,
                    key="btn_roadmap",
                )
            with r2:
                st.selectbox(
                    "Modo",
                    options=["Simulación", "API Real"],
                    index=0 if AppState.is_simulation() else 1,
                    key="mode_selector",
                    label_visibility="collapsed",
                    on_change=_on_mode_change,
                )
        else:
            AppState.set_execution_mode("API Real")
            roadmap_clicked = st.button(
                "Roadmap",
                use_container_width=True,
                key="btn_roadmap",
            )

    return roadmap_clicked