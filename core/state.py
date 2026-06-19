"""
state.py — Capa de abstracción sobre st.session_state.

Centraliza:
  - Definición de todas las keys usadas en la app
  - Inicialización con valores por defecto
  - Getters/setters tipados para las más importantes

Uso:
    from core.state import AppState
    AppState.init()                         # llamar UNA sola vez al inicio de app.py
    AppState.set_selected_topic("foo")
    topic = AppState.selected_topic()
"""

from __future__ import annotations
import re
import streamlit as st


# ── Key constants (evita typos) ───────────────────────────────────────────────
K_EXEC_MODE           = "execution_mode"
K_DEBUG_MODE          = "debug_mode"
K_DEBUG_TOGGLE        = "debug_mode_toggle"
K_SELECTED_TOPIC      = "selected_topic"
K_SELECTED_SECTION_MD = "selected_section_md"
K_DECISION_PANEL      = "decision_panel"
K_SECTION_PANEL_OPEN  = "section_panel_open"
K_TREE_PANEL_OPEN     = "tree_panel_open"
K_TREE_SELECTOR       = "tree_selector"
K_MODULE_SELECTOR     = "module_selector"
K_CHAT                = "chat"
K_ZOOM_IMG_PATH       = "zoom_img_path"
K_ZOOM_IMG_CAPTION    = "zoom_img_caption"
K_ACTIVE_TOOLS_TAB    = "active_tools_tab"   # 0=Sección, 1=Árbol, 2=Asistente
K_TOPIC_FROM_ANCHOR   = "selected_topic_from_anchor"
K_TOOLS_PANEL_COLLAPSED = "tools_panel_collapsed"

HEADING_TONE_RE = re.compile(r"\{dw-(?:black|gold|muted|accent|danger)\}", re.I)


class AppState:
    """Namespace estático. No instanciar."""

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    @staticmethod
    def init(is_dev: bool = True) -> None:
        """Inicializa todas las keys con sus defaults. Idempotente."""
        from config import DEFAULT_EXECUTION_MODE

        defaults: dict = {
            K_EXEC_MODE:           DEFAULT_EXECUTION_MODE,
            K_DEBUG_TOGGLE:        is_dev,
            K_DEBUG_MODE:          is_dev,
            K_SELECTED_TOPIC:      None,
            K_SELECTED_SECTION_MD: "",
            K_DECISION_PANEL:      "",
            K_SECTION_PANEL_OPEN:  False,
            K_TREE_PANEL_OPEN:     False,
            K_TREE_SELECTOR:       None,
            K_MODULE_SELECTOR:     None,
            K_CHAT:                [],
            K_ZOOM_IMG_PATH:       None,
            K_ZOOM_IMG_CAPTION:    None,
            K_TOPIC_FROM_ANCHOR:   None,
            K_ACTIVE_TOOLS_TAB:    0,      # 0=Sección, 1=Árbol, 2=Asistente
            K_TOOLS_PANEL_COLLAPSED: False,
        }
        for k, v in defaults.items():
            if k not in st.session_state:
                st.session_state[k] = v

    # ── Execution mode ─────────────────────────────────────────────────────────
    @staticmethod
    def execution_mode() -> str:
        from config import DEFAULT_EXECUTION_MODE
        return st.session_state.get(K_EXEC_MODE, DEFAULT_EXECUTION_MODE)

    @staticmethod
    def set_execution_mode(mode: str) -> None:
        st.session_state[K_EXEC_MODE] = mode
        st.session_state["_prev_execution_mode"] = mode

    @staticmethod
    def is_simulation() -> bool:
        return AppState.execution_mode() == "Simulación"

    @staticmethod
    def debug_mode() -> bool:
        return bool(st.session_state.get(K_DEBUG_MODE, False))

    # ── Selected topic ─────────────────────────────────────────────────────────
    @staticmethod
    def selected_topic() -> str | None:
        return st.session_state.get(K_SELECTED_TOPIC)

    @staticmethod
    def clean_topic_label(topic: str) -> str:
        """Quita tags visuales de headings antes de mostrarlos en UI."""
        return re.sub(r"\s+", " ", HEADING_TONE_RE.sub("", topic or "")).strip()

    @staticmethod
    def set_selected_topic(topic: str, section_md: str = "") -> None:
        st.session_state[K_SELECTED_TOPIC]      = AppState.clean_topic_label(topic)
        st.session_state[K_SELECTED_SECTION_MD] = section_md
        st.session_state[K_DECISION_PANEL]      = "section"
        st.session_state[K_SECTION_PANEL_OPEN]  = True
        st.session_state[K_TREE_PANEL_OPEN]     = False
        st.session_state[K_ACTIVE_TOOLS_TAB]    = 0   # → tab Sección
        AppState.open_tools_panel()

    @staticmethod
    def selected_section_md() -> str:
        return st.session_state.get(K_SELECTED_SECTION_MD, "")

    # ── Tree panel ─────────────────────────────────────────────────────────────
    @staticmethod
    def open_tree(tree_title: str) -> None:
        st.session_state[K_TREE_SELECTOR]      = tree_title
        st.session_state[K_DECISION_PANEL]     = "tree"
        st.session_state[K_TREE_PANEL_OPEN]    = True
        st.session_state[K_SECTION_PANEL_OPEN] = False
        st.session_state[K_ACTIVE_TOOLS_TAB]   = 1   # → tab Árbol
        AppState.open_tools_panel()

    @staticmethod
    def active_tools_tab() -> int:
        tab = int(st.session_state.get(K_ACTIVE_TOOLS_TAB, 0))
        # Tab 3 (Analítica) solo válido con modo diagnóstico activo
        max_tab = 3 if AppState.debug_mode() else 2
        if tab > max_tab:
            tab = 0
            st.session_state[K_ACTIVE_TOOLS_TAB] = 0
        return tab

    @staticmethod
    def set_active_tools_tab(idx: int) -> None:
        st.session_state[K_ACTIVE_TOOLS_TAB] = idx

    @staticmethod
    def open_tools_panel() -> None:
        """Garantiza que el panel derecho esté visible tras una acción."""
        st.session_state[K_TOOLS_PANEL_COLLAPSED] = False

    @staticmethod
    def tree_panel_open() -> bool:
        return bool(st.session_state.get(K_TREE_PANEL_OPEN, False))

    @staticmethod
    def section_panel_open() -> bool:
        return bool(st.session_state.get(K_SECTION_PANEL_OPEN, False))

    # ── Chat ──────────────────────────────────────────────────────────────────
    @staticmethod
    def chat() -> list[tuple[str, str]]:
        return st.session_state.get(K_CHAT, [])

    @staticmethod
    def append_chat(role: str, msg: str) -> None:
        if role == "assistant":
            msg = re.sub(r"(?m)^(\s*)(\d+)[\.)]\s*(?=\S)", r"\1\2. ", msg or "")
        st.session_state.setdefault(K_CHAT, []).append((role, msg))

    @staticmethod
    def clear_chat() -> None:
        st.session_state[K_CHAT] = []

    # ── Anchor navigation ─────────────────────────────────────────────────────
    @staticmethod
    def pop_topic_from_anchor() -> str | None:
        return st.session_state.pop(K_TOPIC_FROM_ANCHOR, None)
