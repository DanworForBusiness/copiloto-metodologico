"""
app.py â€” Orquestador principal de la aplicaciÃ³n.

Responsabilidad Ãºnica: montar la pÃ¡gina, cargar datos y delegar a los mÃ³dulos UI.
Modularizacion bajo la siguiente estructura:
  ui/styles.py       â†’ CSS global
  ui/header.py       â†’ Logo + tÃ­tulo + roadmap + modo
  ui/sidebar.py      â†’ NavegaciÃ³n + acciones rÃ¡pidas
  ui/content_panel.py â†’ Panel izquierdo (contenido del mÃ³dulo)
  ui/tools_panel.py  â†’ Panel derecho (secciÃ³n / Ã¡rboles / asistente)
  core/state.py      â†’ Session state centralizado
  core/llm.py        â†’ LLMGateway
"""

import re
import streamlit as st

from config import APP_TITLE, CONTENT_DIR, TREES_DIR, STATUS, IA_ENABLED_MODULES
from core.content import (
    read_text_cached,
    split_front_matter,
    module_label_from_md,
    parse_markdown_headings,
    extract_section_by_heading,
)
from core.activity import log_activity
from core.state import AppState, K_MODULE_SELECTOR
from engine.tree_engine import load_tree_registry, load_all_trees, module_tag_from_selected, trees_for_module
from ui.styles import inject_styles
from ui.header import render_header, render_roadmap_modal
from ui.sidebar import APPENDICES_SENTINEL, K_APPENDIX_SELECTOR, render_sidebar
from ui.assessment_panel import render_assessment_panel
from ui.content_panel import render_content_panel
from ui.tools_panel import render_tools_panel
from auth import require_login, get_current_user, render_logout_button

st.set_page_config(page_title=APP_TITLE, layout="wide")

require_login()
render_logout_button()

# Bootstrap 
IS_DEV = STATUS.upper() == "DEV"

st.markdown("<div class='dw-app-spacer'></div>", unsafe_allow_html=True)

AppState.init(is_dev=IS_DEV)
inject_styles()

# Right panel collapse state 
if "tools_panel_collapsed" not in st.session_state:
    st.session_state["tools_panel_collapsed"] = False

# Cargar módulos core y apéndices técnicos.
#
# Los módulos core viven en content/*.md y son la navegación principal del
# copiloto. Los apéndices viven en content/apendices/*.md y se exponen como
# una categoría secundaria del sidebar para no mezclar material operativo con
# anexos técnicos.
paths           = sorted(CONTENT_DIR.glob("*.md"), key=lambda p: p.name.lower())
appendices_dir  = CONTENT_DIR / "apendices"
appendix_paths  = sorted(appendices_dir.glob("*.md"), key=lambda p: p.name.lower()) if appendices_dir.exists() else []

module_names    = [p.stem for p in paths]
appendix_names  = [p.stem for p in appendix_paths]
nav_names       = module_names + appendix_names
module_paths    = {p.stem: str(p) for p in [*paths, *appendix_paths]}
module_labels   = {
    stem: module_label_from_md(stem, read_text_cached(path))
    for stem, path in module_paths.items()
}
appendix_labels = {stem: module_labels.get(stem, stem) for stem in appendix_names}

from core.utils import slugify as _slugify

@st.cache_data(show_spinner=False)
def _make_slug_map(body_md: str) -> dict[str, str]:
    """Mapea slug → título para todos los headings del módulo activo."""
    return {
        _slugify(title): title
        for _, title, _ in parse_markdown_headings(body_md)
    }

# Links internos desde respuestas del asistente pueden pedir cambio de módulo
# antes de resolver el anchor de sección. Ej:
# ?module=01_Arquitectura_y_Conectividad&section=ratio-orientativo...
_query_module = str(st.query_params.get("module", "")).strip()
if _query_module and _query_module in nav_names:
    st.session_state["_pending_module_switch"] = _query_module
    st.session_state["_scroll_content_to_top"] = True
    try:
        del st.query_params["module"]
    except Exception:
        pass

# Cambio de módulo pendiente (desde out-of-scope redirect, header o tools_panel)
_pending_switch = st.session_state.pop("_pending_module_switch", None)
if _pending_switch and _pending_switch in nav_names:
    if _pending_switch in appendix_names:
        st.session_state[K_MODULE_SELECTOR] = APPENDICES_SENTINEL
        st.session_state[K_APPENDIX_SELECTOR] = _pending_switch
    else:
        st.session_state[K_MODULE_SELECTOR] = _pending_switch

# Sidebar → módulo/apéndice seleccionado + acción rápida
selected_stem, quick_action, assessment_clicked = render_sidebar(
    module_names,
    module_labels,
    appendix_names=appendix_names,
    appendix_labels=appendix_labels,
)
tools_enabled_for_module = selected_stem in IA_ENABLED_MODULES

if st.session_state.get("_activity_last_module") != selected_stem:
    log_activity(
        "module_selected",
        module=selected_stem,
        label=module_labels.get(selected_stem, selected_stem),
        ia_enabled=tools_enabled_for_module,
    )
    st.session_state["_activity_last_module"] = selected_stem

# El panel derecho solo aporta valor en módulos habilitados para IA.
# Al cambiar de módulo, se abre/cierra automáticamente según config.py.
_tools_panel_module = st.session_state.get("_tools_panel_module")
if _tools_panel_module != selected_stem:
    st.session_state["tools_panel_collapsed"] = not tools_enabled_for_module
    st.session_state["_tools_panel_module"] = selected_stem

# Assessment clickeado â†’ activar vista
if assessment_clicked:
    st.session_state["_active_assessment"] = assessment_clicked
    st.rerun()

# Assessment view â€” interrumpir antes del header y contenido principal
if st.session_state.get("_active_assessment"):
    st.markdown("<div style='margin-top:1rem'></div>", unsafe_allow_html=True)
    if st.button("✕ Exit", key="assess_back_main"):
        for k in [k for k in st.session_state if k.startswith("_assess_")]:
            del st.session_state[k]
        del st.session_state["_active_assessment"]
        st.rerun()
    render_assessment_panel()
    st.stop()

# Modo Diagnóstico
def _on_debug_mode_change() -> None:
    debug_on = bool(st.session_state.get("debug_mode_toggle", False))
    st.session_state["debug_mode"] = debug_on
    st.session_state["execution_mode"] = "Simulación" if debug_on else "API Real"
    if debug_on:
        st.session_state["mode_selector"] = "Simulación"
    elif st.session_state.get("active_tools_tab") == 3:
        st.session_state["active_tools_tab"] = 0


if IS_DEV:
    st.sidebar.toggle(
        "Modo Diagnóstico (interno)",
        key="debug_mode_toggle",
        on_change=_on_debug_mode_change,
    )
if not st.session_state.get("debug_mode", False):
    AppState.set_execution_mode("API Real")

# Header
roadmap_clicked, module_nav_delta = render_header(
    is_dev=IS_DEV,
    module_names=nav_names,
    selected_stem=selected_stem,
    show_mode_selector=AppState.debug_mode(),
)
if module_nav_delta:
    current_idx = nav_names.index(selected_stem)
    target_idx = max(0, min(len(nav_names) - 1, current_idx + module_nav_delta))
    target_stem = nav_names[target_idx]
    if target_stem != selected_stem:
        # El selectbox del sidebar ya fue instanciado en este run, por eso no
        # podemos tocar K_MODULE_SELECTOR directamente acá. Dejamos el cambio
        # pendiente para aplicarlo arriba, antes de render_sidebar().
        st.session_state["_pending_module_switch"] = target_stem
        st.session_state["_scroll_content_to_top"] = True
        for _key in (
            "selected_topic",
            "selected_section_md",
            "decision_panel",
            "section_panel_open",
            "tree_panel_open",
            "tree_selector",
            "selected_topic_from_anchor",
            "_scroll_to_slug",
            "_tree_suggestion",
            "_combined_suggestion",
            "_section_suggestion",
            "_asset_suggestion",
            "_pending_llm_direct",
        ):
            st.session_state.pop(_key, None)
        AppState.clear_chat()
        st.rerun()
if roadmap_clicked:
    render_roadmap_modal()

# Limpiar chat al cambiar de mÃ³dulo
prev_module = st.session_state.get("_prev_module")
preserve_chat = bool(st.session_state.pop("_preserve_chat_on_module_switch", False))
if prev_module and prev_module != selected_stem:
    if not preserve_chat:
        AppState.clear_chat()
    st.session_state["_scroll_content_to_top"] = True
st.session_state["_prev_module"] = selected_stem

# Cargar contexto del mÃ³dulo 
selected_path = module_paths[selected_stem]
context       = read_text_cached(selected_path)
_, body_md    = split_front_matter(context)

# Query param: navegaciÃ³n desde anchor (?section=slug) y pin (?pin=slug)
# Pin desde support pill â€” ?pin=slug
_pin_slug = str(st.query_params.get("pin", "")).strip()
if _pin_slug:
    slug_map   = _make_slug_map(body_md)
    _pin_target = slug_map.get(_pin_slug)
    if _pin_target:
        section_md = extract_section_by_heading(body_md, _pin_target, level=(2, 3)) or ""
        AppState.set_selected_topic(_pin_target, section_md)
        AppState.set_active_tools_tab(0)
    try:
        del st.query_params["pin"]
    except Exception:
        pass
    st.rerun()

section_slug = str(st.query_params.get("section", "")).strip()
if section_slug:
    slug_map = _make_slug_map(body_md)
    target   = slug_map.get(section_slug)
    if target:
        section_md = extract_section_by_heading(body_md, target, level=(2, 3)) or ""
        AppState.set_selected_topic(target, section_md)
        st.session_state["_scroll_to_slug"] = section_slug

    # Si viene de "Ir a secciÃ³n" desde el asistente, limpiar estado que
    # no se puede limpiar desde el handler del botÃ³n (session_state + rerun)
    if st.query_params.get("section_nav"):
        st.session_state.pop("_section_suggestion", None)
        st.session_state.pop("_combined_suggestion", None)
        AppState.set_active_tools_tab(0)
        if target:
            AppState.append_chat("assistant", f"📖 Abriendo sección: **{AppState.clean_topic_label(target)}**")
        try:
            del st.query_params["section_nav"]
        except Exception:
            pass

    try:
        del st.query_params["section"]
    except Exception:
        pass
    st.rerun()

# Cargar Ã¡rboles del mÃ³dulo
registry    = load_tree_registry(TREES_DIR)
all_trees   = load_all_trees(TREES_DIR)
module_tag  = module_tag_from_selected(selected_stem)

trees_new      = registry.list(module_stem=selected_stem)
trees_legacy   = [
    ct for t in trees_for_module(all_trees, module_tag)
    if (ct := registry.get(t.get("id")))
]
merged         = {t.id: t for t in trees_new}
for t in trees_legacy:
    merged.setdefault(t.id, t)
trees_this_module = list(merged.values())

# Layout principal 
collapsed = st.session_state["tools_panel_collapsed"]

if not tools_enabled_for_module:
    render_content_panel(selected_stem, selected_path, context)
else:
    col_ratios = [1.45, 0.70] if not collapsed else [1.98, 0.02]
    col_content, col_tools = st.columns(col_ratios, vertical_alignment="top")

    with col_content:
        render_content_panel(selected_stem, selected_path, context)

    with col_tools:
        st.markdown("<div class='dw-tools-shell-marker'></div>", unsafe_allow_html=True)

        toggle_label = "▶" if not collapsed else "◀"
        toggle_help  = "Ocultar panel de herramientas" if not collapsed else "Mostrar panel de herramientas"
        
        cb, ct = st.columns([0.08, 0.92], vertical_alignment="center")
        with cb:
            if st.button(toggle_label, help=toggle_help, key="toggle_tools_panel"):
                st.session_state["tools_panel_collapsed"] = not collapsed
                st.rerun()
        with ct:
            if not collapsed:
                st.markdown(
                        "<div class='dw-section-title dw-section-tools'>Herramientas de Decisión</div>",
                        unsafe_allow_html=True)
                st.caption("Seleccioná un key topic o una sección del módulo.")

        if not collapsed:
            render_tools_panel(
                selected_stem=selected_stem,
                selected_path=selected_path,
                context=context,
                trees_this_module=trees_this_module,
                quick_action=quick_action,
            )
