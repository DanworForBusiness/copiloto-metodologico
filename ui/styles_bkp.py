"""
ui/styles.py — CSS global de la aplicación.

Un único punto de verdad para todos los estilos.
Importar e invocar inject_styles() UNA sola vez en app.py.

Versión: Danwor visual identity — alineada con marcos metodológicos y guías web.
"""

import streamlit as st

# ── TOKENS ───────────────────────────────────────────────────────────────────
# Mismos valores que css/styles.css del sitio web Danwor.
# Cambiar aquí se propaga a toda la app.
DW_GOLD   = "#f5a623"
DW_GOLD2  = "#c9960a"
DW_ORANGE = "#e8722a"
DW_BLACK  = "#0a0a0a"
DW_NAVY   = "#0d2b45"
DW_TEXT   = "#1a2535"
DW_MUTED  = "#6b7a8d"
DW_BORDER = "rgba(13,43,69,0.12)"
DW_LIGHT  = "#f7f8fa"
DW_WHITE  = "#ffffff"

_CSS = f"""
/* ============================================================
   DANWOR COPILOTO — Global Styles
   Tokens alineados con css/styles.css del sitio web.
   ============================================================ */

/* ── GOOGLE FONTS ── */
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');

/* ── ROOT OVERRIDES ── */
div[data-testid="stAppViewContainer"] {{
  font-family: 'DM Sans', 'Source Sans 3', sans-serif !important;
  font-size: 14px !important;
  background: {DW_LIGHT} !important;
}}

/* ── HEADINGS ── */
div[data-testid="stAppViewContainer"] h1 {{ font-size: 22px !important; font-weight: 700 !important; color: {DW_TEXT} !important; }}
div[data-testid="stAppViewContainer"] h2 {{ font-size: 19px !important; font-weight: 700 !important; color: {DW_TEXT} !important; }}
div[data-testid="stAppViewContainer"] h3 {{ font-size: 15px !important; font-weight: 600 !important; color: {DW_TEXT} !important; margin-top: 0.2rem !important; margin-bottom: 0.4rem !important; }}
div[data-testid="stAppViewContainer"] h4 {{ font-size: 14px !important; font-weight: 600 !important; color: {DW_TEXT} !important; }}
div[data-testid="stAppViewContainer"] strong {{ font-weight: 650 !important; }}

/* ── MAIN CONTAINER ── */
div[data-testid="stAppViewContainer"] .block-container {{ padding-top: 0.6rem !important; }}
section.main > div.block-container {{ padding-top: 3.2rem !important; }}
div[data-testid="stVerticalBlock"] {{ gap: 0.65rem !important; }}
.stMainBlockContainer {{ padding-left: 2rem !important; }}
img {{ display: block; margin-top: 4px; }}

/* ── SIDEBAR ── */
section[data-testid="stSidebar"] {{
  background: {DW_WHITE} !important;
  border-right: 1px solid {DW_BORDER} !important;
  font-size: 14px !important;
}}
section[data-testid="stSidebar"] * {{ font-size: 14px !important; }}
section[data-testid="stSidebar"] > div {{ padding-top: 0.6rem !important; }}
section[data-testid="stSidebar"] div[data-baseweb="select"] * {{
  font-size: 14px !important; line-height: 1.2 !important;
}}
section[data-testid="stSidebar"] div[data-baseweb="select"] > div {{ min-height: 38px !important; }}
section[data-testid="stSidebar"] div[data-testid="stButton"] button {{
  min-height: 38px !important; font-size: 14px !important;
  border-radius: 8px !important;
  transition: background 0.15s, border-color 0.15s !important;
}}
div[data-baseweb="popover"] * {{ font-size: 14px !important; line-height: 1.2 !important; }}

/* ── WIDGETS ── */
div[data-testid="stSelectbox"] > div {{ min-width: 170px !important; height: 38px !important; }}
div[data-testid="stButton"] button {{
  min-height: unset;
  border-radius: 8px !important;
  font-family: 'DM Sans', sans-serif !important;
  font-weight: 500 !important;
  font-size: 13.5px !important;
  transition: all 0.15s !important;
}}

/* Primary accent: reemplaza el rojo Streamlit con el dorado Danwor */
div[data-testid="stButton"] button[kind="primary"] {{
  background: {DW_GOLD} !important;
  border-color: {DW_GOLD} !important;
  color: #000 !important;
  font-weight: 600 !important;
}}
div[data-testid="stButton"] button[kind="primary"]:hover {{
  background: {DW_GOLD2} !important;
  border-color: {DW_GOLD2} !important;
}}

/* ── DIALOG (st.dialog) ── */
div[data-testid="stDialog"] [role="dialog"] {{
  width: min(1100px, 92vw) !important;
  max-width: 1100px !important;
  border-radius: 16px !important;
  border: 1px solid {DW_BORDER} !important;
}}
div[data-testid="stDialog"] [role="dialog"] > div {{
  max-height: 88vh !important; overflow: auto !important;
}}
div[role="dialog"] > div {{ max-width: 1100px !important; width: 1100px !important; }}
div[role="dialog"] div[data-testid="stVerticalBlock"] {{ gap: 0.35rem !important; }}
div[role="dialog"] div[data-testid="stVerticalBlock"] > div:first-child {{ margin-top: -10px !important; }}
div[role="dialog"] div[data-testid="stMarkdownContainer"] h1,
div[role="dialog"] div[data-testid="stMarkdownContainer"] h2,
div[role="dialog"] div[data-testid="stMarkdownContainer"] h3 {{
  margin-top: 0 !important; padding-top: 0 !important;
}}

/* ── TYPOGRAPHY HELPERS ── */
.dw-app-title {{
  font-size: 18px !important; font-weight: 700 !important;
  letter-spacing: -0.2px !important; margin: 0 !important;
  padding: 0 !important; line-height: 1.2 !important;
  color: {DW_TEXT} !important;
}}
.dw-section-title {{
  font-size: 15px !important; font-weight: 700 !important;
  letter-spacing: -0.1px !important; margin: 0.2rem 0 0.4rem 0 !important;
  line-height: 1.2 !important; display: flex !important;
  align-items: center !important; height: 100% !important;
  color: {DW_TEXT} !important;
}}
.dw-muted {{
  font-size: 13px !important; opacity: 0.65 !important;
  margin-top: 0.1rem !important; color: {DW_MUTED} !important;
}}

/* ── SECTION LABEL (uppercase overline) ── */
.dw-label {{
  font-size: 11px !important; font-weight: 600 !important;
  letter-spacing: 0.8px !important; text-transform: uppercase !important;
  color: {DW_MUTED} !important; margin-bottom: 10px !important;
}}

/* ── CALLOUTS ── */
.dw-callout {{
  border: 1px solid {DW_BORDER};
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 1px 4px rgba(13,43,69,0.06);
  margin: 10px 0;
  font-family: 'DM Sans', sans-serif;
}}
.dw-callout__head {{
  display: flex; gap: 8px; align-items: center;
  font-weight: 600; margin-bottom: 6px; font-size: 14px;
}}
.dw-callout__icon  {{ width: 18px; text-align: center; }}
.dw-callout__body  {{ font-size: 13.5px; line-height: 1.5; white-space: pre-wrap; }}
.dw-callout--note    {{ background: {DW_LIGHT}; }}
.dw-callout--success {{ background: #f0fdf4; border-color: rgba(34,197,94,.2); }}
.dw-callout--punch   {{ background: #fffbf0; border-color: rgba(245,166,35,.3); }}
.dw-callout--warn    {{ background: #fff8f8; border-color: rgba(244,63,94,.2); }}

.dw-callout-row {{
  display: flex; gap: 12px; align-items: stretch; margin: 10px 0;
}}
.dw-callout-col {{ flex: 1; display: flex; flex-direction: column; }}
.dw-callout-col .dw-callout {{ flex: 1; margin: 0; }}
@media (max-width: 900px) {{ .dw-callout-row {{ flex-direction: column; }} }}

/* ── CARDS (HTML custom cards) ── */
.dw-card {{
  background: {DW_WHITE};
  border: 1px solid {DW_BORDER};
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 1px 4px rgba(13,43,69,0.07);
  margin: 6px 0;
}}
.dw-card-accent {{
  border-left: 3px solid {DW_GOLD};
  padding-left: 15px;
}}
.dw-card h4 {{
  font-size: 13.5px; font-weight: 600; margin-bottom: 6px;
  color: {DW_TEXT};
}}
.dw-card p {{
  font-size: 13px; color: {DW_MUTED}; line-height: 1.5; margin: 0;
}}

/* ── MARKDOWN CONTENT ── */
div[data-testid="stMarkdownContainer"] h4 {{
  font-size: 15px !important; font-weight: 700 !important;
  margin-top: 16px !important; margin-bottom: 10px !important;
  padding-bottom: 6px !important;
  border-bottom: 1.5px solid {DW_BORDER} !important;
  color: {DW_TEXT} !important;
}}
div[data-testid="stMarkdownContainer"] h5 {{
  font-size: 13.5px !important; font-weight: 650 !important;
  margin-top: 14px !important; margin-bottom: 8px !important;
  color: {DW_TEXT} !important;
}}
div[data-testid="stMarkdownContainer"] p,
div[data-testid="stMarkdownContainer"] li,
div[data-testid="stMarkdownContainer"] blockquote,
div[data-testid="stMarkdownContainer"] td,
div[data-testid="stMarkdownContainer"] th {{
  font-size: 13.5px !important; line-height: 1.6 !important;
  color: {DW_TEXT} !important;
}}
div[data-testid="stMarkdownContainer"] ul,
div[data-testid="stMarkdownContainer"] ol {{ font-size: 13.5px !important; }}
div[data-testid="stMarkdownContainer"] a {{
  color: {DW_GOLD2}; text-decoration: none; font-weight: 500;
}}
div[data-testid="stMarkdownContainer"] a:hover {{ text-decoration: underline; }}

/* Tables */
div[data-testid="stMarkdownContainer"] table {{
  width: 100%; border-collapse: collapse !important;
  border: 1px solid {DW_BORDER} !important;
  font-size: 13.5px; border-radius: 8px; overflow: hidden;
}}
div[data-testid="stMarkdownContainer"] thead th {{
  font-weight: 600 !important;
  background: {DW_LIGHT} !important;
  border-bottom: 2px solid {DW_BORDER} !important;
  color: {DW_TEXT} !important;
  font-size: 12px !important;
  letter-spacing: 0.4px !important;
  text-transform: uppercase !important;
}}
div[data-testid="stMarkdownContainer"] th,
div[data-testid="stMarkdownContainer"] td {{
  border: 1px solid {DW_BORDER} !important;
  padding: 10px 14px !important;
  vertical-align: top;
}}
div[data-testid="stMarkdownContainer"] tbody tr:hover td {{
  background: rgba(245,166,35,0.04) !important;
}}

/* ── CUSTOM HEADING (dw-md-h2) ── */
.dw-md-h2 {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin: 18px 0 12px 0 !important;
  padding-bottom: 6px !important;
  border-bottom: 1.5px solid {DW_BORDER} !important;
}}
.dw-md-title   {{ font-size: 15px !important; font-weight: 700 !important; line-height: 1.25 !important; color: {DW_TEXT} !important; }}
.dw-md-anchor  {{ text-decoration: none !important; opacity: 0; font-size: 13px !important; margin-left: 4px; }}
.dw-md-h2:hover .dw-md-anchor {{ opacity: .7; }}
.dw-md-links   {{ display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: nowrap; }}
.dw-md-pill {{
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px !important; border-radius: 20px !important;
  border: 1px solid {DW_BORDER} !important; background: {DW_WHITE} !important;
  font-size: 12px !important; line-height: 1 !important;
  text-decoration: none !important; white-space: nowrap; margin: 0 !important;
  color: {DW_MUTED} !important; font-weight: 500 !important;
  transition: border-color 0.15s, color 0.15s !important;
}}
.dw-md-pill:hover  {{
  border-color: {DW_GOLD} !important;
  color: {DW_GOLD2} !important;
}}
.dw-md-pill--icon  {{ gap: 0 !important; padding: 4px 9px !important; justify-content: center; }}
.dw-md-inline-form {{ display: inline-block; margin: 0 !important; padding: 0 !important; }}
.dw-md-inline-form .dw-md-pill {{
  appearance: none; -webkit-appearance: none;
  border: 1px solid {DW_BORDER} !important;
  background: {DW_WHITE} !important; cursor: pointer; font-family: inherit;
}}

/* ── ROADMAP MODAL ── */
.dw-rm-wrap {{
  display: grid; grid-template-columns: repeat(5, 1fr);
  gap: 18px; margin-top: 10px;
}}
.st-key-btn_roadmap button {{ max-width: 120px !important; }}
.st-key-btn_roadmap [data-testid="stButton"] {{
  display: flex !important; justify-content: flex-end !important;
}}
.dw-rm-intro   {{ font-size: 14px; margin: 20px 0 30px 0; color: {DW_MUTED}; }}
.dw-rm-item    {{ width: 100%; text-align: center; }}
.dw-rm-head    {{ display: flex; align-items: center; gap: 8px; margin: 0 0 8px 0; }}
.dw-rm-title   {{ font-weight: 700; font-size: 13px; line-height: 1.15; margin: 0; }}
.dw-rm-dot     {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; vertical-align: middle; flex-shrink: 0; }}
.dw-rm-dot--on  {{ background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.4); }}
.dw-rm-dot--off {{ background: #cbd5e1; }}
.dw-rm-icon    {{ font-size: 16px; margin-right: 6px; }}
.dw-rm-card {{
  background: {DW_WHITE}; border: 1px solid {DW_BORDER};
  border-radius: 12px; padding: 10px 14px;
  box-shadow: 0 1px 4px rgba(13,43,69,0.07);
  font-size: 12.5px; line-height: 1.45; color: {DW_TEXT};
}}
.dw-rm-help {{ opacity: .6; font-size: 12px; margin-top: 10px; text-align: center; padding-top: 10px; }}

/* ── PANEL TOGGLE BUTTON ── */
div[data-testid="stButton"]:has(> button[aria-label="Ocultar panel de herramientas"]) button,
div[data-testid="stButton"]:has(> button[aria-label="Mostrar panel de herramientas"]) button {{
  min-width: 90px !important; white-space: nowrap !important;
  overflow: hidden !important; background: transparent !important;
  border: 1px solid {DW_BORDER} !important; border-radius: 8px !important;
  color: {DW_MUTED} !important; font-size: 12px !important;
  padding: 4px 10px !important; min-height: unset !important;
  transition: background .15s !important;
}}
div[data-testid="stButton"]:has(> button[aria-label="Ocultar panel de herramientas"]) button:hover,
div[data-testid="stButton"]:has(> button[aria-label="Mostrar panel de herramientas"]) button:hover {{
  background: {DW_LIGHT} !important;
}}
.st-key-toggle_tools_panel {{ float: left !important; margin-right: 8px !important; }}
[data-testid="stHorizontalBlock"]:has(.st-key-toggle_tools_panel) .dw-section-tools {{
  margin-top: 0 !important; margin-bottom: 10px !important;
}}

/* ── CHAT BUBBLES ── */
.dw-chat-msg {{
  padding: 10px 14px; border-radius: 10px; margin: 4px 0;
  font-size: 13.5px; line-height: 1.55;
  font-family: 'DM Sans', sans-serif;
}}
.dw-chat-user      {{ background: #f0f4ff; border-left: 3px solid #2563eb; }}
.dw-chat-assistant {{ background: {DW_WHITE}; border-left: 3px solid {DW_GOLD}; border: 1px solid {DW_BORDER}; }}
.dw-chat-label     {{
  font-size: 10.5px; font-weight: 700; opacity: .55; margin-bottom: 4px;
  text-transform: uppercase; letter-spacing: .5px;
}}

/* ── TOOLTIPS ── */
label[data-dw-tip] {{ position: relative; cursor: default; }}
label[data-dw-tip]::after {{
  content: attr(data-dw-tip); position: absolute;
  left: 110%; top: 50%; transform: translateY(-50%);
  background: {DW_TEXT}; color: #f8fafc;
  font-size: 12px; line-height: 1.4; padding: 6px 10px;
  border-radius: 8px; white-space: normal; width: 220px;
  z-index: 9999; pointer-events: none; opacity: 0;
  transition: opacity .15s ease; box-shadow: 0 2px 10px rgba(0,0,0,.2);
}}
label[data-dw-tip]:hover::after {{ opacity: 1; }}

/* ── PIN BUTTON ── */
.dw-md-h2--pinrow + div [class*="st-key-"][class*="_pin_"] button,
[class*="st-key-"][class*="_pin_"] button {{
  display: inline-flex !important; align-items: center !important;
  justify-content: center !important; padding: 4px 9px !important;
  border-radius: 20px !important; border: 1px solid {DW_BORDER} !important;
  background: {DW_WHITE} !important; font-size: 10px !important;
  line-height: 0.5 !important; min-height: unset !important;
  height: 22px !important; width: auto !important;
  box-shadow: none !important; margin-left: 15px !important; margin-top: 15px !important;
  vertical-align: middle !important;
}}
[class*="st-key-"][class*="_pin_"] button:hover {{
  border-color: {DW_GOLD} !important; background: {DW_WHITE} !important;
}}

/* ── STATUS BADGES ── */
.dw-badge {{
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11px; font-weight: 600; padding: 3px 10px;
  border-radius: 20px; letter-spacing: 0.3px;
}}
.dw-badge--available {{ background: rgba(34,197,94,0.1); color: #15803d; border: 1px solid rgba(34,197,94,0.25); }}
.dw-badge--dev       {{ background: {DW_LIGHT}; color: {DW_MUTED}; border: 1px solid {DW_BORDER}; }}
.dw-badge--gold      {{ background: rgba(245,166,35,0.1); color: {DW_GOLD2}; border: 1px solid rgba(245,166,35,0.3); }}
"""


def inject_styles() -> None:
    """Inyectar CSS global Danwor. Llamar UNA sola vez en app.py."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


# ── HELPER FUNCTIONS ──────────────────────────────────────────────────────────

def dw_card(title: str, body: str, accent: bool = False) -> str:
    """Retorna HTML de una card Danwor. Usar con st.markdown(..., unsafe_allow_html=True)."""
    cls = "dw-card dw-card-accent" if accent else "dw-card"
    return f"""
    <div class="{cls}">
      <h4>{title}</h4>
      <p>{body}</p>
    </div>
    """


def dw_badge(text: str, variant: str = "available") -> str:
    """Retorna HTML de un badge de estado. variant: available | dev | gold"""
    return f'<span class="dw-badge dw-badge--{variant}">{text}</span>'


def dw_callout(body: str, title: str = "", kind: str = "note", icon: str = "💡") -> str:
    """
    Retorna HTML de un callout Danwor.
    kind: note | success | punch | warn
    """
    head = f"""
      <div class="dw-callout__head">
        <span class="dw-callout__icon">{icon}</span>
        {title}
      </div>
    """ if title else ""
    return f"""
    <div class="dw-callout dw-callout--{kind}">
      {head}
      <div class="dw-callout__body">{body}</div>
    </div>
    """