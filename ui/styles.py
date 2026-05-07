"""
ui/styles.py — CSS global de la aplicación.

Un único punto de verdad para todos los estilos.
Importar e invocar inject_styles() UNA sola vez en app.py.
"""

import streamlit as st

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,400&display=swap');

/* ============================================================
   PRIMARY COLOR OVERRIDE — reemplaza el rojo Streamlit (#E63946)
   ============================================================ */
div[data-testid="stAppViewContainer"] { font-size: 14px !important; }
div[data-testid="stAppViewContainer"] h1 { font-size: 24px !important; }
div[data-testid="stAppViewContainer"] h2 { font-size: 22px !important; }
div[data-testid="stAppViewContainer"] h3 { font-size: 18px !important; }
div[data-testid="stAppViewContainer"] h4 { font-size: 16px !important; }
div[data-testid="stAppViewContainer"] h3 {
  font-size: 16px !important; font-weight: 650 !important;
  margin-top: 0.2rem !important; margin-bottom: 0.4rem !important;
}
div[data-testid="stAppViewContainer"] strong { font-weight: 650 !important; }

/* ── Contenedor principal ── */
div[data-testid="stAppViewContainer"] .block-container { padding-top: 0.6rem !important; }
section.main > div.block-container { padding-top: 3.2rem !important; }
div[data-testid="stVerticalBlock"] { gap: 0.65rem !important; }
img { display: block; margin-top: 4px; }

/* ============================================================
   SIDEBAR
   ============================================================ */
section[data-testid="stSidebar"] { font-size: 14px !important; }
section[data-testid="stSidebar"] * { font-size: 14px !important; }
section[data-testid="stSidebar"] > div { padding-top: 0.6rem !important; }
section[data-testid="stSidebar"] div[data-baseweb="select"] * {
  font-size: 14px !important; line-height: 1.2 !important;
}
section[data-testid="stSidebar"] div[data-baseweb="select"] > div { min-height: 38px !important; }
section[data-testid="stSidebar"] div[data-testid="stButton"] button {
  min-height: 38px !important; font-size: 14px !important;
}
div[data-baseweb="popover"] * { font-size: 14px !important; line-height: 1.2 !important; }

/* ============================================================
   MAIN CONTAINER
   ============================================================ */
.stMainBlockContainer {
    padding-left: 2rem !important;
}
/* Dropdown Mode (Simulacion vs. API Real)
.st-key-mode_selector [data-baseweb="select"] > div {
    min-height: 36px !important;
    height: 36px !important;
}
/* Wrapper Roadmap
.dw-rm-wrap {
    display: grid;
    grid-template-columns: repeat(5, minmax(120px, 1fr));
    gap: 18px;
    margin-top: 10px;
}
/* Título Herramientas — flotar junto al botón toggle */
.st-key-toggle_tools_panel {
    float: left !important;
    margin-right: 8px !important;
}
[data-testid="stHorizontalBlock"]:has(.st-key-toggle_tools_panel) .dw-section-tools {
    margin-top: 0 !important;
    margin-bottom: 10px !important;
}

/* ============================================================
   DIALOG (st.dialog)
   ============================================================ */
div[data-testid="stDialog"] [role="dialog"] {
  width: min(1100px, 92vw) !important;
  max-width: 1100px !important;
}
div[data-testid="stDialog"] [role="dialog"] > div {
  max-height: 88vh !important; overflow: auto !important;
}
div[role="dialog"] > div { max-width: 1100px !important; width: 1100px !important; }
div[role="dialog"] div[data-testid="stVerticalBlock"] { gap: 0.35rem !important; }
div[role="dialog"] div[data-testid="stVerticalBlock"] > div:first-child { margin-top: -10px !important; }
div[role="dialog"] div[data-testid="stMarkdownContainer"] h1,
div[role="dialog"] div[data-testid="stMarkdownContainer"] h2,
div[role="dialog"] div[data-testid="stMarkdownContainer"] h3 {
  margin-top: 0 !important; padding-top: 0 !important;
}

/* ============================================================
   WIDGETS
   ============================================================ */
div[data-testid="stSelectbox"] > div { min-width: 170px !important; height: 38px !important; }
div[data-testid="stButton"] button { min-height: unset; }
.st-key-mode_selector,
.st-key-mode_selector div[data-testid="stSelectbox"],
.st-key-mode_selector div[data-testid="stSelectbox"] > div,
.st-key-mode_selector [data-baseweb="select"] {
  min-width: 0 !important;
  width: 100% !important;
  max-width: 100% !important;
}

/* ============================================================
   TYPOGRAPHY HELPERS
   ============================================================ */
.dw-app-title {
  font-size: 18px !important; font-weight: 700 !important;
  letter-spacing: -0.2px !important; margin: 0 !important;
  padding: 0 !important; line-height: 1.2 !important;
}
.dw-section-title {
  font-size: 16px !important; font-weight: 700 !important;
  letter-spacing: -0.1px !important; margin: 0.2rem 0 0.4rem 0 !important;
  line-height: 1.2 !important;
  display: flex !important;
  align-items: center !important;
  height: 100% !important;
}
.dw-muted {
  font-size: 13px !important; opacity: 0.75 !important;
  margin-top: 0.1rem !important;
}

/* ============================================================
   CALLOUTS
   ============================================================ */
.dw-callout {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(13,43,69,0.10);
  border-radius: 14px;
  padding: 24px 26px;
  background: #ffffff;
  box-shadow: 0 1px 4px rgba(13,43,69,0.07);
  margin: 12px 0;
}
.dw-callout--punch::before {
  content: "";
  position: absolute;
  bottom: -72px;
  left: -72px;
  width: 210px;
  height: 210px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(var(--dw-gold-rgb),0.09) 0%, transparent 70%);
  pointer-events: none;
}
.dw-callout__head {
  position: relative;
  display: flex;
  flex-direction: row;
  gap: 10px;
  align-items: center;
  font-weight: 800;
  margin-bottom: 14px;
}
.dw-callout__icon {
  width: auto;
  text-align: left;
  font-size: 26px;
  line-height: 1;
}
.dw-callout__title {
  color: #1a2535;
  font-size: 18px;
  line-height: 1.2;
  letter-spacing: 0;
}
.dw-callout__body {
  position: relative;
  font-size: 14px;
  color: #475467;
  line-height: 1.65;
}
.dw-callout__body p {
  margin: 0 0 12px 0 !important;
  color: #475467 !important;
}
.dw-callout__body p:last-child { margin-bottom: 0 !important; }
.dw-callout-list {
  list-style: none !important;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0 !important;
  padding: 0 !important;
}
.dw-callout-list li {
  display: flex;
  align-items: baseline;
  gap: 9px;
  margin: 0 !important;
  padding: 0 !important;
  color: #1a2535 !important;
  line-height: 1.45 !important;
}
.dw-callout-list li::before {
  content: "✓";
  color: var(--dw-gold);
  font-weight: 800;
  font-size: 13px;
  flex: 0 0 auto;
}
.dw-callout--note    { background: #ffffff; }
.dw-callout--success { background: #f7fdf9; border-color: rgba(34,197,94,.25); }
.dw-callout--punch   { background: #fffdf7; border-color: rgba(var(--dw-gold-rgb),.36); }
.dw-callout--warn    { background: #fff8f8; border-color: rgba(244,63,94,.25); }

.dw-callout-row {
  display: flex; gap: 14px; align-items: stretch; margin: 12px 0 18px 0;
}
.dw-callout-col { flex: 1; display: flex; flex-direction: column; }
.dw-callout-col .dw-callout { flex: 1; margin: 0; }
@media (max-width: 900px) { .dw-callout-row { flex-direction: column; } }

/* ============================================================
   MARKDOWN CONTENT
   ============================================================ */
div[data-testid="stMarkdownContainer"] h4 {
  font-size: 16px !important; font-weight: 700 !important;
  margin-top: 16px !important; margin-bottom: 10px !important;
  padding-bottom: 6px !important; border-bottom: 2px solid rgba(0,0,0,0.08) !important;
}
div[data-testid="stMarkdownContainer"] h5 {
  font-size: 14px !important; font-weight: 650 !important;
  margin-top: 16px !important; margin-bottom: 8px !important;
}
div[data-testid="stMarkdownContainer"] p,
div[data-testid="stMarkdownContainer"] li,
div[data-testid="stMarkdownContainer"] blockquote,
div[data-testid="stMarkdownContainer"] td,
div[data-testid="stMarkdownContainer"] th {
  font-size: 14px !important; line-height: 1.45 !important;
}
div[data-testid="stMarkdownContainer"] ul,
div[data-testid="stMarkdownContainer"] ol { font-size: 14px !important; }
div[data-testid="stMarkdownContainer"] a {
  color: #2563eb; text-decoration: none; font-weight: 500;
}
div[data-testid="stMarkdownContainer"] a:hover { text-decoration: underline; }

/* Tables */
div[data-testid="stMarkdownContainer"] table {
  width: 100%; border-collapse: collapse !important;
  border: 2px solid #111 !important; font-size: 14px;
  border-radius: 5px; overflow: hidden;
}
div[data-testid="stMarkdownContainer"] thead th {
  font-weight: 700 !important; border-bottom: 2px solid #111 !important;
}
div[data-testid="stMarkdownContainer"] th,
div[data-testid="stMarkdownContainer"] td {
  border: 1px solid #111 !important; padding: 10px 12px !important; vertical-align: top;
}
div[data-testid="stMarkdownContainer"] tbody tr:hover td { background: rgba(0,0,0,0.02); }

/* ============================================================
   CUSTOM HEADING (dw-md-h2)
   ============================================================ */
.dw-md-h2 {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin: 18px 0 12px 0 !important;
  padding-bottom: 6px !important; border-bottom: 2px solid rgba(0,0,0,0.08) !important;
}
.dw-md-title   { font-size: 16px !important; font-weight: 700 !important; line-height: 1.25 !important; }
.dw-md-anchor  { text-decoration: none !important; opacity: 0; font-size: 13px !important; margin-left: 4px; }
.dw-md-h2:hover .dw-md-anchor { opacity: .85; }
.dw-md-links   { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: nowrap; }
.dw-md-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px !important; border-radius: 999px !important;
  border: 1px solid rgba(0,0,0,0.10) !important; background: #fff !important;
  font-size: 12px !important; line-height: 1 !important;
  text-decoration: none !important; white-space: nowrap; margin: 0 !important;
}
.dw-md-pill:hover  { border-color: rgba(0,0,0,0.18) !important; }
.dw-md-pill--icon  { gap: 0 !important; padding: 4px 9px !important; justify-content: center; }
.dw-md-inline-form { display: inline-block; margin: 0 !important; padding: 0 !important; }
.dw-md-inline-form .dw-md-pill {
  appearance: none; -webkit-appearance: none;
  border: 1px solid rgba(0,0,0,0.10) !important;
  background: #fff !important; cursor: pointer; font-family: inherit;
}

.dw-page-hero {
  position: relative;
  overflow: hidden;
  background: #ffffff;
  border: 0;
  border-bottom: 1px solid var(--dw-border);
  border-radius: var(--dw-radius);
  padding: 8px 20px 8px 22px;
  margin: 5px 0 5px 0;
  min-height: 72px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.dw-section-break {
  height: 18px;
  width: 100%;
  display: block;
  flex: 0 0 auto;
}
.dw-page-hero--first {
  margin-top: 0 !important;
}
.dw-page-hero::before {
  content: "";
  position: absolute;
  bottom: -80px;
  left: -80px;
  width: 260px;
  height: 260px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(var(--dw-gold-rgb),0.14) 0%, rgba(var(--dw-gold-rgb),0) 70%);
  pointer-events: none;
}
.dw-page-hero__dots {
  position: absolute;
  top: 14px;
  left: 8px;
  display: grid;
  grid-template-columns: repeat(3, 8px);
  gap: 8px;
  pointer-events: none;
  opacity: .45;
}
.dw-page-hero__dots span {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--dw-gold);
}
.dw-page-hero__content {
  position: relative;
  z-index: 1;
  padding-left: 24px;
}
.dw-page-hero__badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(var(--dw-gold-rgb),0.10);
  border: 1px solid rgba(var(--dw-gold-rgb),0.30);
  color: var(--dw-gold);
  font-size: 11px !important;
  font-weight: 700;
  padding: 4px 12px;
  border-radius: 999px;
  margin-bottom: 5px;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.dw-page-hero__badge::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dw-gold);
  flex-shrink: 0;
}
.dw-page-hero__title {
  display: flex;
  align-items: baseline;
  gap: 7px;
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  color: #1a2535 !important;
  font-size: 22px !important;
  font-weight: 800 !important;
  line-height: 1.1 !important;
  letter-spacing: 0 !important;
}
.dw-page-hero__title span {
  font: inherit !important;
  color: inherit !important;
  line-height: inherit !important;
}
.dw-page-hero__title .dw-page-hero__num {
  color: var(--dw-gold) !important;
}
.dw-heading-tone--black,
.dw-page-hero__title .dw-heading-tone--black { color: var(--dw-black) !important; }
.dw-heading-tone--gold,
.dw-page-hero__title .dw-heading-tone--gold { color: var(--dw-gold) !important; }
.dw-heading-tone--muted,
.dw-page-hero__title .dw-heading-tone--muted { color: var(--dw-muted) !important; }
.dw-heading-tone--accent,
.dw-page-hero__title .dw-heading-tone--accent { color: var(--dw-accent) !important; }
.dw-heading-tone--danger,
.dw-page-hero__title .dw-heading-tone--danger { color: var(--dw-danger) !important; }
.dw-page-hero .dw-md-links {
  position: relative;
  z-index: 1;
  flex: 0 0 auto;
}
.dw-page-hero:hover .dw-md-anchor { opacity: .85; }

/* ============================================================
   ROADMAP MODAL
   ============================================================ */
.dw-rm-wrap {
  display: grid; grid-template-columns: repeat(5, 1fr);
  gap: 18px; margin-top: 10px;
}   
.st-key-btn_roadmap button {
    max-width: 120px !important;
}   
.st-key-btn_roadmap [data-testid="stButton"] {
    display: flex !important;
    justify-content: flex-end !important;
}
.st-key-btn_module_prev button,
.st-key-btn_module_next button {
    min-width: 38px !important;
    max-width: 42px !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
    font-size: 16px !important;
    font-weight: 700 !important;
}
.st-key-btn_module_prev button:disabled,
.st-key-btn_module_next button:disabled {
    opacity: .42 !important;
    cursor: not-allowed !important;
}
.dw-rm-intro   { font-size: 14px; margin: 20px 0 30px 0; }
.dw-rm-item    { width: 100%; text-align: center; }
.dw-rm-head    { display: flex; align-items: center; gap: 8px; margin: 0 0 8px 0; }
.dw-rm-title   { font-weight: 700; font-size: 13px; line-height: 1.15; margin: 0; }
.dw-rm-dot     { width: 14px; height: 14px; border-radius: 50%; display: inline-block; margin-right: 8px; vertical-align: middle; }
.dw-rm-dot--on  { background: #22c55e; }
.dw-rm-dot--off { background: #cbd5e1; }
.dw-rm-icon    { font-size: 16px; margin-right: 6px; }
.dw-rm-card {
  background: #fff; border: 1px solid rgba(0,0,0,0.08);
  border-radius: 14px; padding: 10px 12px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  font-size: 12px; line-height: 1.35;
}
.dw-rm-help { opacity: .7; font-size: 12px; margin-top: 10px; text-align: center; padding-top: 10px; }

/* ============================================================
   PANEL TOGGLE BUTTON
   ============================================================ */
/* Botón ◀ Contraer / ▶ Expandir — nunca debe romper el texto */
div[data-testid="stButton"]:has(> button[aria-label="Ocultar panel de herramientas"]) button,
div[data-testid="stButton"]:has(> button[aria-label="Mostrar panel de herramientas"]) button {
  min-width: 90px !important;
  white-space: nowrap !important;
  overflow: hidden !important;
  background: transparent !important;
  border: 1px solid rgba(0,0,0,0.12) !important;
  border-radius: 6px !important;
  color: #555 !important;
  font-size: 12px !important;
  padding: 4px 10px !important;
  min-height: unset !important;
  transition: background .15s;
}
div[data-testid="stButton"]:has(> button[aria-label="Ocultar panel de herramientas"]) button:hover,
div[data-testid="stButton"]:has(> button[aria-label="Mostrar panel de herramientas"]) button:hover {
  background: rgba(0,0,0,0.04) !important;
}

.dw-chat-msg { padding: 8px 12px; border-radius: 10px; margin: 4px 0; font-size: 14px; line-height: 1.45; }
.dw-chat-user      { background: #f0f4ff; border-left: 3px solid #2563eb; }
.dw-chat-assistant { background: #f8fafc; border-left: 3px solid var(--dw-gold); }
.dw-chat-label     { font-size: 11px; font-weight: 700; opacity: .6; margin-bottom: 3px; text-transform: uppercase; letter-spacing: .5px; }

/* ── Tooltips para radiobuttons de Acción ─────────────────────────────────── */
/* El JS en tools_panel agrega data-dw-tip="..." a cada label.                */
/* CSS puro, sin JS extra para mostrar/ocultar.                                */
label[data-dw-tip] {
  position: relative;
  cursor: default;
}
label[data-dw-tip]::after {
  content: attr(data-dw-tip);
  position: absolute;
  left: 110%;
  top: 50%;
  transform: translateY(-50%);
  background: #1e293b;
  color: #f8fafc;
  font-size: 12px;
  line-height: 1.4;
  padding: 6px 10px;
  border-radius: 6px;
  white-space: normal;
  width: 220px;
  z-index: 9999;
  pointer-events: none;
  opacity: 0;
  transition: opacity .15s ease;
  box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
label[data-dw-tip]:hover::after {
  opacity: 1;
}
/* Pin button — emular dw-md-pill lo más posible */
div[data-testid="stHorizontalBlock"]:has([class*="_pin_"]) {
    background: #fff !important;
    border-bottom: 1px solid var(--dw-border) !important;
    border-radius: var(--dw-radius) var(--dw-radius) 0 0 !important;
    margin: 5px 0 !important;
}
div[data-testid="stHorizontalBlock"]:has(.dw-page-hero--first):has([class*="_pin_"]) {
    margin-top: -10px !important;
}
div[data-testid="stHorizontalBlock"]:has([class*="_pin_"]) .dw-page-hero {
    border-bottom: 0 !important;
    margin: 0 !important;
}
.dw-page-hero + div [class*="st-key-"][class*="_pin_"],
[class*="st-key-"][class*="_pin_"] {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    min-height: 72px !important;
    margin: 0 !important;
    background: #fff !important;
    border-bottom: 0 !important;
}
.dw-page-hero + div [class*="st-key-"][class*="_pin_"] > div,
[class*="st-key-"][class*="_pin_"] > div {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
}
.dw-md-h2--pinrow + div [class*="st-key-"][class*="_pin_"] button,
[class*="st-key-"][class*="_pin_"] button {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    padding: 4px 9px !important;
    border-radius: 999px !important;
    border: 1px solid rgba(0,0,0,0.10) !important;
    background: #fff !important;
    font-size: 10px !important;
    line-height: 0.5 !important;
    min-height: unset !important;
    height: 22px !important;
    width: auto !important;
    box-shadow: none !important;
    margin-left: 0 !important;
    margin-top: 0 !important;
    vertical-align: middle !important;
}
[class*="st-key-"][class*="_pin_"] button:hover {
    border-color: rgba(0,0,0,0.18) !important;
    background: #fff !important;
}

/* ============================================================
   UX REFRESH 2026 - PRODUCT SHELL
   ============================================================ */
:root {
  --dw-bg: #f6f7f9;
  --dw-surface: #ffffff;
  --dw-surface-soft: #f9fafb;
  --dw-border: #dfe4ea;
  --dw-border-strong: #c9d1dc;
  --dw-black: #0a0a0a;
  --dw-text: #17202a;
  --dw-muted: #6b7a8d;
  --dw-muted-text: #667085;
  --dw-gold: #EAB308;
  --dw-gold-rgb: 234, 179, 8;
  --dw-accent: #0f766e;
  --dw-accent-2: #2563eb;
  --dw-warm: var(--dw-gold);
  --dw-danger: #b42318;
  --dw-radius: 8px;
  --dw-shadow: 0 10px 24px rgba(16, 24, 40, .06);
}

div[data-testid="stAppViewContainer"] {
  background: var(--dw-bg) !important;
  color: var(--dw-text) !important;
}
section[data-testid="stMain"],
div[data-testid="stMain"],
div[data-testid="stMainBlockContainer"],
div[data-testid="stAppViewContainer"] > .main,
div[data-testid="stAppViewContainer"] .block-container {
  background: var(--dw-bg) !important;
}

header[data-testid="stHeader"] {
  display: block !important;
  height: 2.75rem !important;
  min-height: 2.75rem !important;
  background: transparent !important;
  overflow: visible !important;
  z-index: 999999 !important;
  pointer-events: none !important;
}
header[data-testid="stHeader"] > div {
  height: 2.75rem !important;
  min-height: 2.75rem !important;
  background: transparent !important;
  overflow: visible !important;
}
header[data-testid="stHeader"] button,
header[data-testid="stHeader"] [role="button"],
div[data-testid="collapsedControl"],
div[data-testid="stSidebarCollapsedControl"],
button[data-testid="collapsedControl"],
button[data-testid="stSidebarCollapsedControl"] {
  pointer-events: auto !important;
  visibility: visible !important;
  opacity: 1 !important;
  z-index: 1000000 !important;
}
div[data-testid="stDecoration"],
div[data-testid="stStatusWidget"] {
  display: none !important;
}

div[data-testid="stAppViewContainer"] .block-container {
  max-width: none !important;
  width: 100% !important;
  padding-top: 1rem !important;
  padding-left: 1.55rem !important;
  padding-right: 1.55rem !important;
  padding-bottom: 2rem !important;
}

.dw-app-spacer { height: .85rem; }
section[data-testid="stSidebar"] {
  background: #101820 !important;
  border-right: 1px solid rgba(255,255,255,.08) !important;
}
section[data-testid="stSidebar"] * {
  color: #eef2f6 !important;
}
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {
  color: rgba(238,242,246,.72) !important;
}
.dw-side-hero {
  padding: 14px 4px 18px 4px;
  border-bottom: 1px solid rgba(255,255,255,.10);
  margin-bottom: 16px;
}
.dw-side-eyebrow {
  font-size: 11px !important;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: rgba(238,242,246,.62) !important;
  margin-bottom: 4px;
}
.dw-side-title {
  font-size: 19px !important;
  font-weight: 750 !important;
  line-height: 1.15 !important;
}
.dw-side-section {
  margin: 18px 0 8px 0;
  padding-top: 14px;
  border-top: 1px solid rgba(255,255,255,.10);
  color: rgba(238,242,246,.70) !important;
  font-size: 11px !important;
  font-weight: 750 !important;
  letter-spacing: .08em;
  text-transform: uppercase;
}
section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
  background: rgba(255,255,255,.08) !important;
  border: 1px solid rgba(255,255,255,.16) !important;
  border-radius: var(--dw-radius) !important;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] button {
  justify-content: flex-start !important;
  border-radius: var(--dw-radius) !important;
  border: 1px solid rgba(255,255,255,.10) !important;
  background: rgba(255,255,255,.055) !important;
  color: #f8fafc !important;
  font-weight: 600 !important;
  padding: 9px 11px !important;
  transition: background .15s ease, border-color .15s ease, transform .15s ease;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
  background: rgba(255,255,255,.12) !important;
  border-color: rgba(255,255,255,.22) !important;
  transform: translateY(-1px);
}
section[data-testid="stSidebar"] div[data-testid="stButton"] button:disabled,
section[data-testid="stSidebar"] div[data-testid="stButton"] button[disabled] {
  background: rgba(255,255,255,.025) !important;
  border-color: rgba(255,255,255,.065) !important;
  color: rgba(255,255,255,.34) !important;
  box-shadow: none !important;
  cursor: not-allowed !important;
  transform: none !important;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] button:disabled *,
section[data-testid="stSidebar"] div[data-testid="stButton"] button[disabled] * {
  color: rgba(255,255,255,.34) !important;
}

div[data-testid="stButton"] button {
  border-radius: var(--dw-radius) !important;
  border: 1px solid var(--dw-border) !important;
  background: var(--dw-surface) !important;
  color: var(--dw-text) !important;
  font-weight: 650 !important;
  min-height: 36px !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04) !important;
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
div[data-testid="stButton"] button:hover {
  border-color: var(--dw-border-strong) !important;
  box-shadow: 0 5px 14px rgba(16,24,40,.08) !important;
  transform: translateY(-1px);
}

.st-key-analyze_topic_btn button,
.st-key-send_free button,
.st-key-send_dialog button,
[class*="st-key-vaa_"] button {
  background: #ffffff !important;
  border-color: rgba(15,118,110,.34) !important;
  color: #0f766e !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04) !important;
}
.st-key-analyze_topic_btn button:hover,
.st-key-send_free button:hover,
.st-key-send_dialog button:hover,
[class*="st-key-vaa_"] button:hover {
  background: #ecfdf5 !important;
  border-color: #0f766e !important;
  color: #0b4f4a !important;
  box-shadow: 0 5px 14px rgba(15,118,110,.14) !important;
}
.st-key-analyze_topic_btn button *,
.st-key-send_free button *,
.st-key-send_dialog button *,
[class*="st-key-vaa_"] button * {
  color: inherit !important;
}
.st-key-analyze_topic_btn button:active,
.st-key-send_free button:active,
.st-key-send_dialog button:active,
[class*="st-key-vaa_"] button:active {
  background: #d1fae5 !important;
  color: #064e3b !important;
}
.st-key-analyze_topic_btn button:disabled,
.st-key-send_free button:disabled,
.st-key-send_dialog button:disabled,
[class*="st-key-vaa_"] button:disabled {
  background: #f3f4f6 !important;
  border-color: #e5e7eb !important;
  color: #98a2b3 !important;
  box-shadow: none !important;
}

div[data-baseweb="select"] > div,
div[data-testid="stTextArea"] textarea,
div[data-testid="stTextInput"] input {
  border-radius: var(--dw-radius) !important;
  border-color: var(--dw-border) !important;
  background: var(--dw-surface) !important;
}
div[data-testid="stTextArea"] textarea:focus,
div[data-testid="stTextInput"] input:focus {
  border-color: var(--dw-accent) !important;
  box-shadow: 0 0 0 3px rgba(15,118,110,.12) !important;
}

.dw-panel-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: 0 0 8px 0;
  padding: 0 2px;
}
.dw-panel-heading span {
  font-size: 17px !important;
  font-weight: 780 !important;
  letter-spacing: 0 !important;
}
.dw-panel-heading small {
  color: var(--dw-muted-text);
  font-size: 12px !important;
  font-weight: 650;
}
.dw-topic-label,
.dw-field-label {
  margin: 8px 0 6px 2px;
  color: var(--dw-muted-text);
  font-size: 11px !important;
  font-weight: 780 !important;
  letter-spacing: .08em;
  text-transform: uppercase;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: var(--dw-radius) !important;
  border-color: var(--dw-border) !important;
  background: transparent !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
div[data-testid="stVerticalBlockBorderWrapper"] > div,
div[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"] {
  background: transparent !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dw-content-surface-marker),
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dw-content-surface-marker) > div,
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dw-content-surface-marker) [data-testid="stVerticalBlock"] {
  background: #ffffff !important;
}
.dw-content-surface-marker {
  display: none;
}
.dw-content-shell-marker {
  display: none;
}
div[data-testid="stColumn"]:has(.dw-content-shell-marker),
div[data-testid="column"]:has(.dw-content-shell-marker),
div[data-testid="stColumn"]:has(.dw-content-shell-marker) > div,
div[data-testid="column"]:has(.dw-content-shell-marker) > div,
div[data-testid="stColumn"]:has(.dw-content-shell-marker) > div > div,
div[data-testid="column"]:has(.dw-content-shell-marker) > div > div {
  background: var(--dw-bg) !important;
}
div[style*="overflow"]:has(.dw-content-surface-marker),
div[style*="overflow"]:has(.dw-content-surface-marker) > div {
  background: #ffffff !important;
}

div[data-testid="stMarkdownContainer"] p,
div[data-testid="stMarkdownContainer"] li {
  color: #243141;
}
div[data-testid="stMarkdownContainer"] h2,
div[data-testid="stMarkdownContainer"] h3,
div[data-testid="stMarkdownContainer"] h4 {
  color: #111827 !important;
}
div[data-testid="stMarkdownContainer"] blockquote {
  border-left: 3px solid var(--dw-gold) !important;
  box-shadow: none !important;
  background: transparent !important;
  max-width: 620px !important;
  padding: 0 0 0 16px !important;
  margin: 18px 0 22px 54px !important;
  border-radius: 0 !important;
  color: var(--dw-muted) !important;
  font-family: 'DM Sans', sans-serif !important;
  font-size: 15px !important;
  line-height: 1.65 !important;
}
div[data-testid="stMarkdownContainer"] blockquote p,
div[data-testid="stMarkdownContainer"] blockquote p span,
div[data-testid="stMarkdownContainer"] blockquote * {
  margin: 0 !important;
  color: var(--dw-muted) !important;
  font-family: 'DM Sans', sans-serif !important;
  font-size: 15px !important;
  line-height: 1.65 !important;
  font-weight: 400 !important;
}
.dw-hero-desc {
  font-family: 'DM Sans', sans-serif !important;
  font-size: 15px !important;
  color: var(--dw-muted) !important;
  line-height: 1.65 !important;
  width: calc(100% - 54px) !important;
  max-width: none !important;
  padding-left: 16px !important;
  border-left: 3px solid var(--dw-gold) !important;
  margin: 18px 0 22px 54px !important;
  font-weight: 400 !important;
  white-space: normal !important;
  overflow-wrap: anywhere !important;
  word-break: normal !important;
}
div[data-testid="stMarkdownContainer"] table {
  border: 1px solid var(--dw-border-strong) !important;
  border-radius: var(--dw-radius) !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
div[data-testid="stMarkdownContainer"] th {
  background: #f8fafc !important;
}
div[data-testid="stMarkdownContainer"] th,
div[data-testid="stMarkdownContainer"] td {
  border-color: var(--dw-border) !important;
}

.dw-md-h2 {
  margin-top: 22px !important;
  padding: 10px 0 8px 0 !important;
  border-bottom: 1px solid var(--dw-border) !important;
}
.dw-md-title {
  font-size: 17px !important;
  font-weight: 780 !important;
}
.dw-md-pill,
.dw-md-inline-form .dw-md-pill {
  border-radius: 999px !important;
  border-color: var(--dw-border) !important;
  background: #f8fafc !important;
  color: #334155 !important;
  font-weight: 650 !important;
}
.dw-md-pill:hover {
  background: #eef6f5 !important;
  border-color: rgba(15,118,110,.28) !important;
}

.dw-selected-topic {
  border: 1px solid rgba(15,118,110,.20);
  border-left: 4px solid var(--dw-accent);
  background: #f0fdfa;
  border-radius: var(--dw-radius);
  padding: 10px 12px;
  margin: 0 0 10px 0;
}
.dw-selected-topic span {
  display: block;
  color: #0f766e;
  font-size: 11px !important;
  font-weight: 780;
  letter-spacing: .07em;
  text-transform: uppercase;
  margin-bottom: 2px;
}
.dw-selected-topic strong {
  display: block;
  color: #102a2a;
  font-size: 14px !important;
  line-height: 1.25 !important;
}
.dw-empty-state {
  border: 1px dashed var(--dw-border-strong);
  background: #ffffff;
  border-radius: var(--dw-radius);
  padding: 14px;
  margin-bottom: 12px;
}
.dw-empty-state strong,
.dw-empty-state span {
  display: block;
}
.dw-empty-state strong {
  font-size: 14px !important;
  margin-bottom: 4px;
}
.dw-empty-state span {
  color: var(--dw-muted-text);
  font-size: 13px !important;
  line-height: 1.35 !important;
}
.dw-output-card {
  background: #fff;
  border: 1px solid var(--dw-border);
  border-radius: var(--dw-radius);
  box-shadow: var(--dw-shadow);
  padding: 14px 16px;
  margin: 12px 0 8px 0;
}
.dw-output-card p,
.dw-output-card li {
  font-size: 14px !important;
  line-height: 1.5 !important;
}
.dw-output-card h1,
.dw-output-card h2,
.dw-output-card h3,
.dw-output-card h4 {
  margin-top: 0 !important;
}

div[data-testid="stExpander"] {
  background: #ffffff !important;
  border: 1px solid var(--dw-border) !important;
  border-radius: var(--dw-radius) !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04) !important;
}
div[data-testid="stExpander"] details,
div[data-testid="stExpander"] summary,
div[data-testid="stExpander"] div[role="region"] {
  background: #ffffff !important;
}
div[data-testid="stExpander"] summary {
  border-radius: var(--dw-radius) var(--dw-radius) 0 0 !important;
}
div[data-testid="stExpander"] summary:hover {
  background: #fafafa !important;
}

.dw-chat-msg {
  border-radius: var(--dw-radius) !important;
  border: 1px solid var(--dw-border) !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
.dw-chat-surface-marker {
  display: none;
}
div[style*="overflow"]:has(.dw-chat-surface-marker),
div[style*="overflow"]:has(.dw-chat-surface-marker) > div {
  background: #ffffff !important;
}
.dw-chat-user {
  background: #eff6ff !important;
  border-left: 4px solid var(--dw-accent-2) !important;
}
.dw-chat-assistant {
  background: #ffffff !important;
  border-left: 4px solid var(--dw-warm) !important;
}
.dw-chat-label {
  letter-spacing: .07em !important;
}

.dw-rm-wrap {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)) !important;
}
.dw-rm-card {
  border-radius: var(--dw-radius) !important;
  border-color: var(--dw-border) !important;
  box-shadow: 0 4px 14px rgba(16,24,40,.06) !important;
}

/* Shell del panel derecho: el fondo pertenece al layout gris; solo los
   componentes internos deben decidir cuándo son blancos. */
.dw-tools-shell-marker {
  display: none;
}
div[data-testid="stHorizontalBlock"]:has(.dw-content-shell-marker):has(.dw-tools-shell-marker),
div[data-testid="stHorizontalBlock"]:has(.dw-content-shell-marker):has(.dw-tools-shell-marker) > div {
  background: var(--dw-bg) !important;
}
div[data-testid="column"]:has(.dw-tools-shell-marker),
div[data-testid="stColumn"]:has(.dw-tools-shell-marker),
div[data-testid="column"]:has(.st-key-toggle_tools_panel),
div[data-testid="stColumn"]:has(.st-key-toggle_tools_panel),
div[data-testid="column"]:has(.dw-tools-shell-marker) > div,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) > div,
div[data-testid="column"]:has(.st-key-toggle_tools_panel) > div,
div[data-testid="stColumn"]:has(.st-key-toggle_tools_panel) > div,
div[data-testid="column"]:has(.dw-tools-shell-marker) > div > div,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) > div > div,
div[data-testid="column"]:has(.st-key-toggle_tools_panel) > div > div,
div[data-testid="stColumn"]:has(.st-key-toggle_tools_panel) > div > div,
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stVerticalBlock"],
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stVerticalBlock"],
div[data-testid="column"]:has(.st-key-toggle_tools_panel) div[data-testid="stVerticalBlock"],
div[data-testid="stColumn"]:has(.st-key-toggle_tools_panel) div[data-testid="stVerticalBlock"],
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stHorizontalBlock"],
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stHorizontalBlock"],
div[data-testid="column"]:has(.st-key-toggle_tools_panel) div[data-testid="stHorizontalBlock"],
div[data-testid="stColumn"]:has(.st-key-toggle_tools_panel) div[data-testid="stHorizontalBlock"],
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stElementContainer"],
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stElementContainer"],
div[data-testid="column"]:has(.st-key-toggle_tools_panel) div[data-testid="stElementContainer"],
div[data-testid="stColumn"]:has(.st-key-toggle_tools_panel) div[data-testid="stElementContainer"],
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stMarkdown"],
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stMarkdown"] {
  background: var(--dw-bg) !important;
}
div[data-testid="column"]:has(.dw-tools-shell-marker) .dw-empty-state,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) .dw-empty-state,
div[data-testid="column"]:has(.dw-tools-shell-marker) .dw-selected-topic,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) .dw-selected-topic,
div[data-testid="column"]:has(.dw-tools-shell-marker) .dw-output-card,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) .dw-output-card,
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"],
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"],
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"] details,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"] details,
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"] summary,
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"] summary,
div[data-testid="column"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"] div[role="region"],
div[data-testid="stColumn"]:has(.dw-tools-shell-marker) div[data-testid="stExpander"] div[role="region"] {
  background: #ffffff !important;
}

@media (max-width: 900px) {
  div[data-testid="stAppViewContainer"] .block-container {
    padding-left: .8rem !important;
    padding-right: .8rem !important;
  }
  .dw-app-spacer { height: .35rem; }
  .dw-panel-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }
}
"""


def inject_styles() -> None:
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
