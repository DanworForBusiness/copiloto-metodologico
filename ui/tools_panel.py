"""
ui/tools_panel.py — Panel derecho: Herramientas de Decisión.

Estructura con st.tabs:
  Tab 1 — Sección seleccionada + análisis IA
  Tab 2 — Árboles de decisión
  Tab 3 — Asistente libre + conversación
"""

from __future__ import annotations

import json
import re
import html
import io
import zipfile
import datetime as _dt
import time as _time
from pathlib import Path

import streamlit as st
import yaml

from core.utils import norm_text as _norm, slugify as _slugify
from config import IA_ENABLED_MODULES, MODEL, FEEDBACK_LOG_DIR, ACTIVITY_LOG_DIR, CASE_LIBRARY_DIR, CONTENT_DIR, ia_enabled_for
from core.activity import log_activity

try:
    from auth import get_current_user as _get_auth_user
except ImportError:
    def _get_auth_user(): return {}

try:
    import markdown as _md_lib
    def _to_html(text: str) -> str:
        return _md_lib.markdown(_normalize_llm_spacing(text))
except ImportError:
    def _to_html(text: str) -> str:
        text = _normalize_llm_spacing(text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         text)
        return text
from core.content import (
    render_md_with_image_zoom,
    read_text_cached,
    split_front_matter,
    module_label_from_md,
    parse_section_meta,
    extract_section_by_heading,
)
from core.llm import LLMGateway
from core.prompts import (
    ALL_ACTIONS,
    ACTION_FULL,
    ACTION_CHECKLIST,
    ACTION_DECISIONS,
    ACTION_ANTIPATTERNS,
    ACTION_REVIEW,
    QUICK_ACTIONS,
    build_system_prompt,
    build_topic_prompt,
    build_tree_value_add_prompt,
    topic_instruction,
    get_loading_message,
    cases_available,
    _load_relevant_cases,
    DEPTH_MODES,
    AMBIGUITY_SYSTEM,
    build_ambiguity_prompt,
)
from core.state import AppState
from engine.tree_engine import (
    tree_engine_init,
    tree_engine_reset,
    tree_engine_render,
    render_leaf_feedback_form,
)

_MODULE01_STEM = "01_Arquitectura_y_Conectividad"
_QUOTA_RATIO_HEADING = "Ratio orientativo de distribución de quota"
_PRACTICAL_CASE_APPENDICES = (
    "A02_Modernizacion_BW_Greenfield_BDC.md",
    "A03_Hub_de_Integracion_AWS.md",
    "A05_Troubleshooting_y_Performance.md",
)


def _strip_md_for_case_context(text: str) -> str:
    """Compacta markdown/apéndices para usarlos como contexto semántico del LLM."""
    lines: list[str] = []
    skip_fence = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            skip_fence = not skip_fence
            continue
        if skip_fence:
            continue
        if not line:
            continue
        if line.startswith("{{") and line.endswith("}}"):
            continue
        if line.startswith("![](") or line.startswith("!["):
            continue
        line = re.sub(r"\{\{[^{}]*\}\}", " ", line)
        line = re.sub(r"\{dw-[^{}]+\}", "", line)
        line = re.sub(r"<[^>]+>", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _appendix_case_context(max_chars: int = 6500) -> str:
    """
    Devuelve un bloque corto con apéndices/casos internos para acciones rápidas.

    No reemplaza el contexto del módulo: lo enriquece para que "Ejemplos prácticos"
    use casos documentados (greenfield, modernización BW, hub de integración, etc.)
    antes de inventar escenarios hipotéticos.
    """
    appendix_dir = CONTENT_DIR / "apendices"
    if not appendix_dir.exists():
        return ""

    blocks: list[str] = []
    for name in _PRACTICAL_CASE_APPENDICES:
        path = appendix_dir / name
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = split_front_matter(raw)
        title = str(meta.get("title") or path.stem).strip()
        scope = str(meta.get("scope") or "").strip()
        headings = re.findall(r"(?m)^#{2,3}\s+(.+?)\s*$", body)
        clean = _strip_md_for_case_context(body)
        snippet = clean[:1600].rstrip()
        parts = [f"Apéndice interno: {title}"]
        if scope:
            parts.append(f"Alcance: {scope}")
        if headings:
            parts.append("Secciones: " + "; ".join(headings[:8]))
        if snippet:
            parts.append("Contenido útil:\n" + snippet)
        blocks.append("\n".join(parts))

    if not blocks:
        return ""

    text = "\n\n".join(blocks)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0].rstrip()
    return (
        "\n\nCONTEXTO ADICIONAL - APÉNDICES Y CASOS INTERNOS DANWOR:\n"
        "Usá este bloque como fuente complementaria para ejemplos prácticos documentados. "
        "Priorizá siempre el contrato semántico del módulo activo si define un foco específico. "
        "Si completás algo por inferencia, marcá [INFERENCIA].\n"
        f"{text}"
    )


def _quick_action_guidance_from_context(context: str, action: str) -> tuple[str, str]:
    """
    Lee instrucciones opcionales para una acción rápida desde el front matter.

    El panel no debe conocer semántica editorial por módulo. Si un módulo necesita
    matizar una acción rápida, lo declara en `quick_action_guidance` dentro del
    markdown; el código solo aplica ese contrato de forma genérica.
    """
    try:
        meta, _ = split_front_matter(context or "")
    except Exception:
        return "", ""

    guidance = meta.get("quick_action_guidance") or {}
    if not isinstance(guidance, dict):
        return "", ""

    item = guidance.get(action)
    if item is None:
        item = guidance.get(_norm(action))
    if item is None:
        return "", ""

    if isinstance(item, str):
        return item.strip(), ""
    if not isinstance(item, dict):
        return "", ""

    prompt = str(item.get("prompt") or item.get("instruction") or "").strip()
    context_note = str(item.get("context") or item.get("contexto") or "").strip()
    return prompt, context_note


def _should_suggest_quota_ratio(answer: str, *, quick_action: str, selected_stem: str) -> bool:
    """
    Detecta cuándo una acción rápida toca un artefacto interno navegable.

    La navegación se resuelve con el mismo patrón conversacional del asistente
    (_section_suggestion), no con links markdown generados por el LLM.
    """
    if quick_action not in {"Errores frecuentes", "Guía operativa del módulo"} or selected_stem != _MODULE01_STEM:
        return False

    text = answer or ""
    if not re.search(r"(distribuci[oó]n porcentual|quota|space quota|capacidad total del tenant)", text, re.I):
        return False
    return True


def _simulate_section_output(action: str, topic: str) -> str:
    t = topic
    placeholders = {
        ACTION_FULL: (
            f"🧪 *Simulación — estructura de salida para **{t}***\n\n"
            f"**¿Qué resuelve esta sección?**\n"
            f"*(2-3 líneas sobre el problema que aborda y por qué importa al proyecto)*\n\n"
            f"**Lo que el cliente necesita saber:**\n"
            f"- *(bullet 1)*\n- *(bullet 2)*\n- *(bullet 3)*\n- *(bullet 4)*\n\n"
            f"**Decisiones que se derivan:**\n"
            f"- *(decisión 1)*\n- *(decisión 2)*\n- *(decisión 3)*\n\n"
            f"**Riesgos si no se aborda correctamente:**\n"
            f"- *(riesgo 1)*\n- *(riesgo 2)*\n- *(riesgo 3)*"
        ),
        ACTION_CHECKLIST: (
            f"🧪 *Simulación — estructura de salida para **{t}***\n\n"
            f"**Checklist de relevamiento — {t}**\n\n"
            f"☐ *(ítem 1 — pregunta o acción directa)* *(riesgo si se omite)*\n"
            f"☐ *(ítem 2)* *(riesgo)*\n"
            f"☐ *(ítem 3)* *(riesgo)*\n"
            f"☐ *(ítem 4)* *(riesgo)*\n"
            f"☐ *(ítem 5)* *(riesgo)*\n"
            f"☐ *(ítem 6)* *(riesgo)*\n"
            f"☐ *(ítem 7)* *(riesgo)*\n"
            f"☐ *(ítem 8)* *(riesgo)*"
        ),
        ACTION_DECISIONS: (
            f"🧪 *Simulación — estructura de salida para **{t}***\n\n"
            f"**Mapa de decisiones — {t}**\n\n"
            f"**SI** *(condición 1)* → **ENTONCES** *(acción recomendada)* *(justificación)*\n\n"
            f"**SI** *(condición 2)* → **ENTONCES** *(acción recomendada)* *(justificación)*\n\n"
            f"**SI** *(condición 3)* → **ENTONCES** *(acción recomendada)* *(justificación)*\n\n"
            f"**SI** *(condición 4)* → **ENTONCES** *(acción recomendada)* *(justificación)*\n\n"
            f"**SI** *(condición 5)* → **ENTONCES** *(acción recomendada)* *(justificación)*"
        ),
        ACTION_ANTIPATTERNS: (
            f"🧪 *Simulación — estructura de salida para **{t}***\n\n"
            f"**Anti-patterns y riesgos — {t}**\n\n"
            f"❌ ***(anti-pattern 1)***\n↳ Por qué está mal: *(explicación)*\n↳ Riesgo concreto: *(consecuencia)*\n\n"
            f"❌ ***(anti-pattern 2)***\n↳ Por qué está mal: *(explicación)*\n↳ Riesgo concreto: *(consecuencia)*\n\n"
            f"❌ ***(anti-pattern 3)***\n↳ Por qué está mal: *(explicación)*\n↳ Riesgo concreto: *(consecuencia)*\n\n"
            f"❌ ***(anti-pattern 4)***\n↳ Por qué está mal: *(explicación)*\n↳ Riesgo concreto: *(consecuencia)*"
        ),
        ACTION_REVIEW: (
            f"🧪 *Simulación — estructura de salida para **{t}***\n\n"
            f"**Postura Danwor sobre {t}:**\n"
            f"*(2-4 líneas: qué exige el marco en este punto y por qué)*\n\n"
            f"**Evidencia mínima que debe estar presente:**\n"
            f"- *(evidencia 1)*\n- *(evidencia 2)*\n- *(evidencia 3)*\n- *(evidencia 4)*\n\n"
            f"**Señales de desvío del marco:**\n"
            f"- *(señal 1)*\n- *(señal 2)*\n- *(señal 3)*\n\n"
            f"**Veredicto rápido:**\n"
            f"*(1 línea: si el consultor no puede mostrar la evidencia mínima, qué implica)*"
        ),
    }
    return placeholders.get(action, f"🧪 *Simulación activa — output de **{action}** para **{t}***")


# ── Feedback de outputs IA ─────────────────────────────────────────────────────

def _feedback_log_dir() -> Path:
    return FEEDBACK_LOG_DIR


def _save_ia_feedback(
    rating: str,
    comment: str,
    action: str,
    topic: str,
    module: str,
    feedback_type: str,
) -> None:
    log_dir = _feedback_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    module_code = module.split("_")[0] if "_" in module else module
    entry = {
        "type":    feedback_type,
        "module":  module_code,
        "topic":   topic,
        "action":  action,
        "rating":  rating,
        "comment": comment.strip(),
        "ts":      ts,
    }
    out_path = log_dir / f"feedback_{ts}.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(entry, f, allow_unicode=True, sort_keys=False)


def _render_feedback_buttons(
    fb_key: str,
    action: str,
    topic: str,
    module: str,
    feedback_type: str = "section_action",
) -> None:
    state_key   = f"_fb_state_{fb_key}"
    comment_key = f"_fb_comment_{fb_key}"

    if state_key not in st.session_state:
        st.session_state[state_key] = "idle"

    state = st.session_state[state_key]

    if state == "idle":
        col1, col2, _ = st.columns([0.09, 0.09, 0.82], gap=None)
        with col1:
            if st.button("👍", key=f"btn_up_{fb_key}", help="Este output fue útil"):
                _save_ia_feedback("up", "", action, topic, module, feedback_type)
                st.session_state[state_key] = "done_up"
                _rerun_tools_panel()
        with col2:
            if st.button("👎", key=f"btn_down_{fb_key}", help="Este output necesita mejora"):
                st.session_state[state_key] = "awaiting_comment"
                _rerun_tools_panel()

    elif state == "awaiting_comment":
        comment = st.text_input(
            "¿Qué falló o qué mejorarías? (requerido)",
            key=comment_key,
            placeholder="Ej: los ítems son muy genéricos, no menciona ODP...",
        )
        if st.button("Enviar", key=f"fb_send_{fb_key}"):
            if not comment.strip():
                show_msg("Escribí al menos una línea antes de enviar.", kind="warning")
            else:
                _save_ia_feedback("down", comment, action, topic, module, feedback_type)
                st.session_state[state_key] = "done_down"
                _rerun_tools_panel()

    elif state == "done_up":
        show_msg("Feedback registrado. ¡Gracias!", kind="success")
        st.session_state[state_key] = "confirmed"

    elif state == "done_down":
        show_msg("Feedback registrado. Lo usaremos para mejorar.", kind="success")
        st.session_state[state_key] = "confirmed"


def _render_leaf_assets(leaf: dict) -> None:
    """Renderiza recursos opcionales declarados en una hoja de arbol.

    Ejemplo YAML:
      assets:
        - label: "Template Danwor"
          url: "https://..."
          description: "Plantilla de referencia"
    """
    assets = (leaf or {}).get("assets") or []
    if not isinstance(assets, list):
        return

    for asset in assets:
        if not isinstance(asset, dict):
            continue

        label = str(asset.get("label") or "Abrir recurso").strip()
        url = str(asset.get("url") or "").strip()
        desc = str(asset.get("description") or "").strip()
        if not url:
            continue

        desc_html = f"<span>{html.escape(desc)}</span>" if desc else ""
        st.markdown(
            f"""
            <div class="dw-leaf-assets">
              <a class="dw-leaf-asset-btn" href="{html.escape(url, quote=True)}"
                 target="_blank" rel="noopener noreferrer">🔗 {html.escape(label)}</a>
              {desc_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _ia_enabled(selected_stem: str) -> bool:
    return ia_enabled_for(selected_stem)


def _ia_disabled_msg() -> None:
    from config import IA_ENABLED_MODULES
    n = len(IA_ENABLED_MODULES)
    if n == 0:
        msg = "La IA no está habilitada para ningún módulo."
    elif n <= 3:
        def _label(stem):
            parts = stem.split("_", 1)
            num = parts[0].zfill(2) if parts[0].isdigit() else parts[0]
            label = parts[1].replace("_", " ") if len(parts) > 1 else stem
            return f"{num} · {label}"
        labels = [_label(s) for s in IA_ENABLED_MODULES]
        if n == 1:
            msg = f"La IA está habilitada solo para el módulo {labels[0]}."
        else:
            msg = f"La IA está habilitada solo para los módulos {', '.join(labels[:-1])} y {labels[-1]}."
    elif n <= 6:
        nums = ", ".join(s.split("_")[0].zfill(2) for s in IA_ENABLED_MODULES)
        msg = f"La IA está habilitada solo para los módulos {nums}."
    else:
        msg = "La IA no está habilitada para éste módulo."
    show_msg(msg, kind="tip", duration=6)


# ── Mensajes de validación ────────────────────────────────────────────────────
_MSG_STYLES: dict[str, tuple[str, str, str]] = {
    "success": ("#d4edda", "#28a745", "#155724"),
    "warning": ("#fff0c2", "#e6ac00", "#6b4f00"),
    "error":   ("#fcd9dc", "#dc3545", "#7b1a22"),
    "tip":     ("#d6eaf8", "#2e86c1", "#1a4f6e"),
}


def show_msg(text: str, kind: str = "tip", duration: float = 3.5) -> None:
    bg, border, color = _MSG_STYLES.get(kind, _MSG_STYLES["tip"])
    uid = f"danwor_msg_{kind}_{int(_time.time() * 1000)}"
    st.markdown(f"""
<style>
@keyframes {uid}_anim {{
  0%   {{ opacity: 1; max-height: 80px; margin-top: 6px; padding: 10px 14px; }}
  70%  {{ opacity: 1; max-height: 80px; margin-top: 6px; padding: 10px 14px; }}
  90%  {{ opacity: 0; max-height: 80px; margin-top: 6px; padding: 10px 14px; }}
  100% {{ opacity: 0; max-height: 0;   margin-top: 0;   padding: 0; overflow: hidden; }}
}}
.{uid} {{
  background: {bg};
  border-left: 4px solid {border};
  color: {color};
  border-radius: 4px;
  font-size: 0.9rem;
  animation: {uid}_anim {duration}s ease-in-out forwards;
  overflow: hidden;
}}
</style>
<div class="{uid}">{text}</div>
""", unsafe_allow_html=True)


_LLM_EXECUTED_KEY = "_llm_executed"

def _llm_guard(fingerprint: str) -> bool:
    """
    Protege contra doble ejecución de LLM para el mismo prompt/acción.
    Devuelve True si ya fue ejecutado (bloquear) → mostrar warning al caller.
    Devuelve False si es nuevo (permitir) → registra el fingerprint.
    El set se limpia en Limpiar chat y al cambiar de módulo.
    """
    if AppState.is_simulation():
        return False
    executed = st.session_state.setdefault(_LLM_EXECUTED_KEY, set())
    if fingerprint in executed:
        return True
    executed.add(fingerprint)
    return False


def _llm_guard_reset() -> None:
    """Limpia todos los fingerprints registrados (Limpiar chat, cambio de módulo)."""
    st.session_state.pop(_LLM_EXECUTED_KEY, None)


# ── LLM call con spinner animado ─────────────────────────────────────────────
#
# ARQUITECTURA DE LLAMADA AL LLM:
#   _llm_call_direct       → spinner animado, sin fase de casos.
#                            Usado por: "Explicame vos" de árbol, sección y asset.
#   _llm_call_with_cases   → igual pero con fase 1 de conteo de casos animada.
#                            Usado por: acciones de sección y chat libre.
#
# Ambas delegan al LLMGateway — nunca acceden al cliente OpenAI directamente.
#
_SPIN_MSGS = [
    "🔍 Obteniendo la mejor respuesta...",
    "🧠 Ya casi estamos...",
]

def _render_processing_bubble(slot, message: str) -> None:
    slot.markdown(
        "<div class='dw-chat-msg dw-chat-assistant'>"
        "<div class='dw-chat-label'>Arquitecto Danwor</div>"
        f"<span style='color:#8a95a3;font-size:0.9rem'>{html.escape(message)}</span>"
        "</div>",
        unsafe_allow_html=True,
    )


def _animated_spinner(slot, loading_msg: str, thread) -> None:
    """Anima el slot mientras el thread está vivo."""
    msgs = [loading_msg] + _SPIN_MSGS
    _render_processing_bubble(slot, msgs[0])
    i = 1
    while thread.is_alive():
        _render_processing_bubble(slot, msgs[i % len(msgs)])
        i += 1
        _time.sleep(3)
    slot.empty()


def _normalize_llm_spacing(answer: str) -> str:
    """
    Separa la intro de los encabezados principales cuando el LLM devuelve
    un solo salto de linea. Markdown necesita linea en blanco para respirar.
    """
    if not answer:
        return answer

    title_prefixes = (
        "guia operativa",
        "checklist",
        "errores frecuentes",
        "ejemplos practicos",
        "ejemplo ",
        "anti-patterns",
        "decisiones",
        "riesgos",
        "recomendacion danwor",
        "salida operativa",
        "mirada danwor",
    )

    lines = answer.splitlines()
    out: list[str] = []
    for line in lines:
        clean_line = re.sub(r"^[#*\s_`]+|[*\s_`]+$", "", line).strip()
        clean_norm = _norm(clean_line)
        if clean_norm.startswith(title_prefixes) and out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


def _llm_call_direct(
    prompt: str,
    context: str,
    loading_msg: str = "Consultando al Arquitecto Senior Danwor...",
    module: str | None = None,
    max_tokens: int = 1200,
) -> str:
    """
    Llama al LLM en un thread separado con spinner animado.
    El modo simulación/real se captura en el hilo principal antes de lanzar
    el thread, porque st.session_state no está disponible en threads secundarios.
    """
    import threading as _threading

    # Capturar en hilo principal — session_state no disponible en el thread
    _is_sim = AppState.is_simulation()
    _gw     = LLMGateway.get()
    _sys    = build_system_prompt(module)
    _result = {}

    def _call():
        if _is_sim:
            _result["answer"] = _gw._simulate(_sys, context, prompt)
        else:
            _result["answer"] = _gw._call_api(_sys, context, prompt, max_tokens=max_tokens)

    _slot = st.session_state.pop("_llm_spinner_slot", None) or st.empty()
    _t    = _threading.Thread(target=_call, daemon=True)
    _t.start()
    _animated_spinner(_slot, loading_msg, _t)
    _t.join()
    return _normalize_llm_spacing(_result.get("answer", "⚠ Sin respuesta."))

    def _call():
        _result["answer"] = _gw.ask(_sys, context, prompt)

    _slot = st.session_state.pop("_llm_spinner_slot", None) or st.empty()
    _t    = _threading.Thread(target=_call, daemon=True)
    _t.start()
    _animated_spinner(_slot, loading_msg, _t)
    _t.join()
    return _normalize_llm_spacing(_result.get("answer", "⚠ Sin respuesta."))


def _llm_call_with_cases(
    prompt: str,
    context: str,
    loading_msg: str,
    cases_count: int = 0,
    module: str | None = None,
    max_tokens: int = 1200,
) -> str:
    """
    Igual que _llm_call_direct pero con fase 1 animada de conteo de casos.
    El modo simulación/real se captura en el hilo principal antes de lanzar
    el thread, porque st.session_state no está disponible en threads secundarios.
    """
    import threading as _threading

    # Capturar en hilo principal — session_state no disponible en el thread
    _is_sim = AppState.is_simulation()
    _gw     = LLMGateway.get()
    _sys    = build_system_prompt(module)
    _result = {}

    def _call():
        if _is_sim:
            _result["answer"] = _gw._simulate(_sys, context, prompt)
        else:
            _result["answer"] = _gw._call_api(_sys, context, prompt, max_tokens=max_tokens)

    _slot = st.empty()

    # Fase 1 — conteo animado de casos (2 segundos fijos, cosmético)
    if cases_count > 0:
        _delay = 2.0 / cases_count
        for _n in range(1, cases_count + 1):
            _render_processing_bubble(_slot, f"📂 Recuperando casos reales ({_n})")
            _time.sleep(_delay)

    # Fase 2 — LLM en thread con spinner animado
    _t = _threading.Thread(target=_call, daemon=True)
    _t.start()
    _animated_spinner(_slot, loading_msg, _t)
    _t.join()
    return _normalize_llm_spacing(_result.get("answer", "⚠ Sin respuesta."))


_PENDING_KEY = "_pending_action"

def _has_pending_action() -> bool:
    return bool(st.session_state.get(_PENDING_KEY))


def _queue_action(label: str, callback, debug_prompt: str | None = None, cases_count: int = 0, skip_dialog: bool = False) -> None:
    st.session_state["_processing"] = True
    st.session_state["_hide_section_action_zone"] = True
    st.session_state[_PENDING_KEY] = {
        "label":        label,
        "callback":     callback,
        "debug_prompt": debug_prompt,
        "cases_count":  cases_count,
        "skip_dialog":  skip_dialog,
    }
    _rerun_tools_panel()


def _flush_pending_action() -> None:
    pending = st.session_state.get(_PENDING_KEY)
    if not pending:
        return

    action_label = pending["label"]
    debug_prompt = pending.get("debug_prompt")
    cases_count  = pending.get("cases_count", 0)
    skip_dialog  = pending.get("skip_dialog", False)
    pending_type = pending.get("_type", "callback")

    del st.session_state[_PENDING_KEY]

    if pending_type == "script":
        import subprocess as _sp, sys as _sys, os as _os
        from config import BASE_DIR as _BASE_DIR
        _script  = pending["script"]
        _args    = pending["args"]
        _out_key = pending["out_key"]
        def callback():
            script_path = _BASE_DIR / _script
            env = _os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            result = _sp.run(
                [_sys.executable, str(script_path)] + _args,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=str(_BASE_DIR), env=env,
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            st.session_state[_out_key] = out if out else (f"Error:\n{err}" if err else "(sin output)")
    else:
        callback = pending["callback"]

    if not hasattr(st, "dialog") or not AppState.is_simulation():
        try:
            callback()
        finally:
            st.session_state.pop("_processing", None)
            st.session_state.pop("_hide_section_action_zone", None)
        _rerun_tools_panel()
        return

    @st.dialog("Procesando...", width="small")
    def _dlg():
        if AppState.is_simulation():
            # Modo simulación — mostrar info y pedir confirmación
            st.info("🧪 **Simulación** — no se consumen créditos.")
            if debug_prompt:
                st.markdown("**Prompt que se enviaría:**")
                st.code(debug_prompt, language="markdown")
            sim_delay = st.selectbox(
                "Simular tiempo de procesamiento",
                options=[0, 5, 10, 15],
                format_func=lambda x: "Sin delay (instantáneo)" if x == 0 else f"{x} segundos",
                index=0,
                key="sim_delay_selector",
            )
            if cases_count > 0:
                st.info(f"📂 {cases_count} caso{'s' if cases_count > 1 else ''} real{'es' if cases_count > 1 else ''} incluido{'s' if cases_count > 1 else ''}.")
            else:
                st.caption("📂 Sin casos registrados aún.")
            c1, c2 = st.columns(2)
            if c1.button("Cancelar", width='stretch'):
                st.session_state.pop("_processing", None)
                st.session_state.pop("_hide_section_action_zone", None)
                _rerun_tools_panel()
            if c2.button("Confirmar", width='stretch'):
                if sim_delay > 0:
                    _slot = st.empty()
                    _msgs = ["⏳ Analizando...", "🔍 Obteniendo la mejor respuesta...", "🧠 Ya casi estamos..."]
                    _elapsed = 0
                    while _elapsed < sim_delay:
                        _slot.markdown(
                            f"<div style='color:#888;font-size:0.9rem;padding:6px 0;text-align:center;width:100%'>{_msgs[(_elapsed // 3) % len(_msgs)]}</div>",
                            unsafe_allow_html=True,
                        )
                        _time.sleep(3)
                        _elapsed += 3
                    _slot.empty()
                try:
                    callback()
                finally:
                    st.session_state.pop("_processing", None)
                    st.session_state.pop("_hide_section_action_zone", None)
                _rerun_tools_panel()
    _dlg()


def _flush_pending_classify(selected_stem: str) -> None:
    """Procesa una pregunta libre pendiente desde la superficie del chat."""
    _pending_classify = st.session_state.pop("_pending_classify", None)
    if not _pending_classify:
        return

    _pc_prompt      = _pending_classify["prompt"]
    _pc_user_prompt = _pending_classify.get("user_prompt")
    _pc_ctx         = _pending_classify["context"]
    _pc_loading     = _pending_classify["loading_msg"]

    if _pc_user_prompt:
        AppState.append_chat("user", _pc_user_prompt)

    st.session_state["_fq_counter"] = st.session_state.get("_fq_counter", 0) + 1

    if AppState.is_simulation():
        _is_ambiguous = False
    else:
        import threading as _threading

        _result = {"ambiguous": False}

        def _classify():
            try:
                gw  = LLMGateway.get()
                raw = gw._call_api(AMBIGUITY_SYSTEM, "", build_ambiguity_prompt(_pc_prompt), max_tokens=20)
                stripped = raw.strip()
                if stripped.startswith("{"):
                    _result["ambiguous"] = bool(json.loads(stripped).get("ambiguous", False))
            except Exception:
                _result["ambiguous"] = False

        _slot = st.empty()
        _t = _threading.Thread(target=_classify, daemon=True)
        _t.start()
        _animated_spinner(_slot, "Analizando tu consulta...", _t)
        _t.join()
        _is_ambiguous = _result["ambiguous"]

    if _is_ambiguous:
        st.session_state["_depth_selector"] = {
            "prompt":        _pc_prompt,
            "context":       _pc_ctx,
            "exclude_modes": ["metodologico"],
        }
        _llm_guard_reset()
    else:
        st.session_state["_pending_llm_direct"] = {
            "prompt": _pc_prompt,
            "context": _pc_ctx,
            "loading_msg": _pc_loading,
            "module": _pending_classify.get("module") or selected_stem,
        }
    st.session_state.pop("_expand_chat_open", None)
    _rerun_tools_panel()


_CHAT_SUGGESTION_KEYS = (
    "_section_suggestion",
    "_tree_suggestion",
    "_asset_suggestion",
    "_asset_section_suggestion",
    "_combined_suggestion",
    "_depth_selector",
    "_cds_delta_selector",
)


def _inject_inline_style(css: str) -> None:
    html_block = f"<style>{css}</style>"
    html_fn = getattr(st, "html", None)
    if callable(html_fn):
        html_fn(html_block)
    else:
        st.markdown(html_block, unsafe_allow_html=True)


def _clear_pending_suggestions() -> None:
    for key in _CHAT_SUGGESTION_KEYS:
        st.session_state.pop(key, None)


def _flush_pending_llm_direct(selected_stem: str) -> None:
    """Procesa una respuesta LLM directa desde la superficie del chat."""
    _pending_direct = st.session_state.pop("_pending_llm_direct", None)
    if not _pending_direct:
        return
    if _pending_direct.get("activity_event") != "logged":
        log_activity(
            _pending_direct.get("activity_event") or "llm_direct",
            module=_pending_direct.get("module") or selected_stem,
            prompt=_pending_direct.get("prompt", ""),
            source=_pending_direct.get("activity_source", "assistant"),
        )
    answer = _llm_call_direct(
        _pending_direct["prompt"],
        _pending_direct["context"],
        _pending_direct.get("loading_msg", get_loading_message("Explicación", False)),
        module=_pending_direct.get("module") or selected_stem,
    )
    AppState.append_chat("assistant", answer)
    st.session_state.pop("_expand_chat_open", None)
    _rerun_tools_panel()


def _flush_pending_free_question(
    selected_stem: str,
    selected_path: str,
    context: str,
    trees_this_module: list | None,
) -> None:
    """Procesa una pregunta libre completa desde el chat, incluyendo routing interno."""
    pending = st.session_state.pop("_pending_free_question", None)
    if not pending:
        return

    _prompt = (pending.get("prompt") or "").strip()
    if not _prompt:
        return
    log_activity("prompt_consulted", module=selected_stem, prompt=_prompt, source="assistant")

    _ctx = context
    AppState.append_chat("user", _prompt)
    st.session_state["_clear_fq"] = True
    st.session_state["_fq_counter"] = st.session_state.get("_fq_counter", 0) + 1
    _clear_pending_suggestions()

    matched_section = _find_matching_section(_prompt, _ctx)
    is_conceptual   = _is_conceptual_or_comparative_query(_prompt)
    is_asset_query  = _is_asset_seeking_query(_prompt)
    matched_asset   = _find_matching_asset(_prompt, selected_path, _ctx, allow_strong=True)
    cross_asset     = None if matched_asset else _find_cross_module_asset(_prompt, selected_stem, allow_strong=True)
    has_asset_match = bool(matched_asset or cross_asset)
    matched_tree    = None if ((is_conceptual and matched_section) or is_asset_query or has_asset_match) else _find_matching_tree(_prompt, trees_this_module or [])
    cross_section_for_asset = None
    if has_asset_match and not matched_section:
        cross_section_for_asset = _find_cross_module_section(_prompt, selected_stem)

    # Para consultas tipo how-to/documentacion/template o matches fuertes contra
    # activos curados, dejamos competir primero a la biblioteca interna. Si
    # tambien hay una seccion local amplia, ofrecemos ambos caminos en vez de
    # forzar una respuesta demasiado vaga.
    if has_asset_match:
        _asset = matched_asset or cross_asset
        _asset_desc = _asset.get("description", "recurso tecnico")
        _section_match = matched_section or cross_section_for_asset
        if _section_match:
            _sec_heading = _section_match["heading"]
            _sec_label = _display_heading(_sec_heading)
            _sec_module = _section_match.get("module") or selected_stem
            _sec_module_label = _section_match.get("module_label")
            _asset_module = _asset.get("_module") or selected_stem
            _asset_module_label = _asset.get("_module_label")
            if (
                _sec_module == _asset_module
                and _is_reference_library_module(_sec_module, _sec_module_label or _asset_module_label)
            ):
                _section_match = None
            elif not _should_pair_section_with_asset(_prompt, _section_match):
                _section_match = None
        if _section_match:
            _sec_heading = _section_match["heading"]
            _sec_label = _display_heading(_sec_heading)
            _sec_module = _section_match.get("module") or selected_stem
            _sec_module_label = _section_match.get("module_label")
            _sec_context = _section_match.get("section_md") or context
            _section_prefix = "Sección del módulo"
            if _sec_module_label and _sec_module != selected_stem:
                _section_prefix = f"Sección en {_sec_module_label}"
            AppState.append_chat(
                "assistant",
                "Encontré varias opciones que pueden ayudarte. ¿Cómo preferís continuar?\n\n"
                f"- **{_section_prefix}:** {_sec_label}\n"
                f"- **Biblioteca de referencia:** {_asset_desc}",
            )
            st.session_state["_asset_section_suggestion"] = {
                "asset": _asset,
                "section_heading": _sec_heading,
                "prompt": _prompt,
                "context": _sec_context,
                "module": _sec_module,
                "module_label": _sec_module_label,
            }
        else:
            _module_label = _asset.get("_module_label")
            _source = f" en **{_module_label}**" if _module_label else ""
            AppState.append_chat(
                "assistant",
                f"Encontré un recurso de la biblioteca de referencias{_source}: **{_asset_desc}**. ¿Querés verlo?",
            )
            st.session_state["_asset_suggestion"] = {"asset": _asset, "prompt": _prompt}
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()
        return

    if matched_section:
        _matched_label = AppState.clean_topic_label(_display_heading(matched_section.get("heading", "")))
        _active_label  = AppState.clean_topic_label(AppState.selected_topic() or "")
        if _active_label and _matched_label == _active_label:
            _active_ctx = AppState.selected_section_md() or _ctx
            st.session_state["_pending_llm_direct"] = {
                "prompt": _prompt,
                "context": _active_ctx,
                "loading_msg": get_loading_message("Explicación", False),
                "module": selected_stem,
                "activity_event": "logged",
            }
            st.session_state.pop("_expand_chat_open", None)
            _rerun_tools_panel()
            return

    if matched_tree:
        tree_title = matched_tree.get("title", matched_tree.id if hasattr(matched_tree, "id") else "")
        _resolved_section_heading = None
        if matched_section:
            _sec_heading    = matched_section["heading"]
            _sec_body_match = re.search(
                r"(?m)^#{1,3}[^\n]*" + re.escape(_sec_heading) + r"[^\n]*\n(.*?)(?=^#{1,3} |\Z)",
                _ctx, re.DOTALL | re.MULTILINE,
            )
            _has_tree_btn = bool(
                _sec_body_match and re.search(
                    r"\{\{tree_button:\s*(.+?)\}\}", _sec_body_match.group(1),
                )
            )
            if not _has_tree_btn:
                _resolved_section_heading = _sec_heading
        st.session_state["_last_tree_question"] = _prompt
        if _resolved_section_heading:
            AppState.append_chat("assistant", "Encontré recursos sobre este tema. ¿Cómo preferís abordarlo?")
            st.session_state["_combined_suggestion"] = {
                "tree": matched_tree, "tree_title": tree_title,
                "section_heading": _resolved_section_heading, "prompt": _prompt,
            }
        else:
            AppState.append_chat("assistant", f"Encontré un árbol de decisión que puede guiarte: **{tree_title}**. ¿Cómo preferís continuar?")
            st.session_state["_tree_suggestion"] = {"tree": matched_tree, "title": tree_title, "prompt": _prompt}
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()
        return

    if matched_asset is not None:
        AppState.append_chat("assistant", "Tengo un recurso técnico que puede ayudarte. ¿Querés verlo?")
        st.session_state["_asset_suggestion"] = {"asset": matched_asset, "prompt": _prompt}
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()
        return

    if matched_section:
        _sec_heading    = matched_section["heading"]
        _sec_label      = _display_heading(_sec_heading)
        _active_label   = AppState.clean_topic_label(AppState.selected_topic() or "")
        if _active_label and AppState.clean_topic_label(_sec_label) == _active_label:
            _active_ctx = AppState.selected_section_md() or _ctx
            st.session_state["_pending_llm_direct"] = {
                "prompt": _prompt,
                "context": _active_ctx,
                "loading_msg": get_loading_message("Explicación", False),
                "module": selected_stem,
                "activity_event": "logged",
            }
            st.session_state.pop("_expand_chat_open", None)
            _rerun_tools_panel()
            return
        _sec_body_match = re.search(
            r"(?m)^#{1,3}[^\n]*" + re.escape(_sec_heading) + r"[^\n]*\n(.*?)(?=^#{1,3} |\Z)",
            _ctx, re.DOTALL | re.MULTILINE,
        )
        _tree_btn_match = None
        if _sec_body_match:
            _tree_btn_match = re.search(
                r"\{\{tree_button:\s*(.+?)\}\}", _sec_body_match.group(1),
            )
        if _tree_btn_match:
            _tree_title       = _tree_btn_match.group(1).strip()
            _matched_tree_obj = next(
                (t for t in (trees_this_module or [])
                 if (t.get("title") if isinstance(t, dict) else getattr(t, "title", "")) == _tree_title),
                None,
            )
            if _matched_tree_obj:
                AppState.append_chat("assistant", f"Encontré un árbol de decisión que puede guiarte: **{_tree_title}**. ¿Cómo preferís continuar?")
                st.session_state["_tree_suggestion"]    = {"tree": _matched_tree_obj, "title": _tree_title, "prompt": _prompt}
                st.session_state["_last_tree_question"] = _prompt
            else:
                AppState.append_chat("assistant", f"Encontré una sección del módulo que puede ayudarte: **{_sec_label}**. ¿Cómo preferís continuar?")
                st.session_state["_section_suggestion"] = {"heading": _sec_heading, "prompt": _prompt}
        else:
            AppState.append_chat("assistant", f"Encontré una sección del módulo que puede ayudarte: **{_sec_label}**. ¿Cómo preferís continuar?")
            st.session_state["_section_suggestion"] = {"heading": _sec_heading, "prompt": _prompt}
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()
        return

    # Si el modulo activo no resolvio la consulta, buscamos solamente en
    # metadata transversal. Esto evita caer en un LLM generico para temas que
    # el copiloto ya cubre elsewhere, sin abrir todo el corpus como contexto.
    cross_section = _find_cross_module_section(_prompt, selected_stem)
    if cross_section:
        _sec_heading = cross_section["heading"]
        _sec_label = _display_heading(_sec_heading)
        _module_label = cross_section.get("module_label") or cross_section.get("module") or "otro modulo"
        AppState.append_chat(
            "assistant",
            f"Este tema parece estar cubierto con más precisión en el módulo **{_module_label}**, "
            f"sección **{_sec_label}**. ¿Cómo preferís continuar?",
        )
        st.session_state["_section_suggestion"] = {
            "heading": _sec_heading,
            "prompt": _prompt,
            "module": cross_section.get("module"),
            "module_label": _module_label,
            "context": cross_section.get("section_md") or "",
        }
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()

    from core.prompts import get_out_of_scope_redirect
    _oos = get_out_of_scope_redirect(_prompt, selected_stem)
    if _oos:
        AppState.append_chat("assistant", f"⚠️ {_oos.get('message', 'Esa consulta corresponde a otro módulo.')}")
        st.session_state["_oos_redirect"] = {
            "module": _oos.get("module"),
            "label":  _oos.get("label", f"Módulo {_oos.get('module')}"),
            "prompt": _prompt,
            "context": _ctx,
        }
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()

    if _needs_cds_delta_choice(_prompt):
        st.session_state["_cds_delta_selector"] = {
            "prompt":  _prompt,
            "context": _ctx,
            "module":  selected_stem,
        }
        st.session_state.pop("_expand_chat_open", None)
        _rerun_tools_panel()

    _cases_count = len(_load_relevant_cases(module=selected_stem, max_cases=5))
    _loading_msg = get_loading_message("Pregunta libre", _cases_count > 0)
    st.session_state["_pending_classify"] = {
        "prompt":       _prompt,
        "user_prompt":  None,
        "fq_key":       f"free_question_{st.session_state.get('_fq_counter', 0)}",
        "context":      _ctx,
        "module":       selected_stem,
        "loading_msg":  _loading_msg,
        "processing":   True,
    }
    st.session_state.pop("_expand_chat_open", None)
    _rerun_tools_panel()


def _confirm_and_run(action_label: str, callback, debug_prompt: str | None = None, cases_count: int = 0, skip_dialog: bool = False) -> None:
    if skip_dialog:
        _queue_action(action_label, callback, debug_prompt, cases_count=cases_count, skip_dialog=True)
    else:
        _queue_action(action_label, callback, debug_prompt, cases_count=cases_count)


# Tab 1: Sección ──────────────────────────────────────────────────────────────
def _render_section_tab(selected_stem: str, context: str, selected_path: str) -> None:
    selected_topic = AppState.selected_topic()
    section_md     = AppState.selected_section_md()
    md_dir         = Path(selected_path).parent

    if selected_topic:
        st.markdown(
            f"<div class='dw-selected-topic'><span>Sección activa</span><strong>{selected_topic}</strong></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='dw-empty-state'><strong>Seleccioná una sección</strong>"
            "<span>Elegí un key topic del contenido o abrí una sección desde el asistente.</span></div>",
            unsafe_allow_html=True,
        )

    if selected_topic and section_md.strip():
        with st.expander("Ver fragmento", expanded=False):
            st.markdown("<div class='dw-fragment-surface-marker'></div>", unsafe_allow_html=True)
            lines = section_md.strip().splitlines()
            body_only = "\n".join(
                lines[1:] if lines and lines[0].lstrip().startswith("#") else lines
            ).strip()
            if body_only:
                render_md_with_image_zoom(body_only, base_dir=md_dir, key_prefix=f"rsec_{selected_stem}")
            else:
                st.caption("El fragmento solo contiene el encabezado.")
    elif selected_topic:
        st.caption("No se encontró fragmento exacto para este heading.")

    with st.container(key="section_action_zone"):
        _ACTION_DESCRIPTIONS = {
        "Salida Operativa":
            "Síntesis ejecutiva en lenguaje no técnico, orientada a decisión. "
            "Para compartir con el cliente: qué necesita saber para aprobar o avanzar.",
        "Checklist ejecutable":
            "Convierte el contenido en lista de verificación accionable para trabajo de campo. "
            "Uso interno durante el relevamiento técnico.",
        "Decisiones si/entonces":
            "Extrae la lógica condicional implícita como reglas claras. "
            "Complementa los árboles operando sobre el texto libre de la sección.",
        "Anti-patterns y riesgos":
            "Extrae explícitamente qué NO hacer y por qué. "
            "Útil para preparar al consultor y para mostrarle al cliente qué errores evitar.",
        "Mirada Danwor":
            "Dado un entregable o decisión del proyecto, evalúa si está alineado al marco Danwor. "
            "Va en dirección inversa: no 'explicame el marco' sino 'revisá si lo que hice es correcto'.",
    }

        st.markdown("<div class='dw-field-label'>Acción</div>", unsafe_allow_html=True)
        action = st.radio(
            "Acción",
            ALL_ACTIONS,
            index=0,
            horizontal=False,
            key="topic_action_radio",
            label_visibility="collapsed",
        )

        with st.expander("¿Qué hace cada acción?", expanded=False):
            _help_items = "\n".join(
                f"<div class='dw-action-help-item'>"
                f"<strong>{html.escape(_a)}</strong>"
                f"<span>{html.escape(_desc)}</span>"
                f"</div>"
                for _a, _desc in _ACTION_DESCRIPTIONS.items()
            )
            st.markdown(
                f"<div class='dw-action-help'>{_help_items}</div>",
                unsafe_allow_html=True,
            )

        if st.button(
            "Analizar",
            width='stretch',
            key="analyze_topic_btn",
            disabled=_has_pending_action(),
        ):
            if not selected_topic:
                show_msg("Seleccioná primero un key topic o una sección del Módulo.", kind="tip")
            elif not _ia_enabled(selected_stem):
                _ia_disabled_msg()
            else:
                _fp = f"section_{selected_stem}_{selected_topic}_{action}"
                if _llm_guard(_fp):
                    show_msg("Ya analizaste esta acción. Elegí otra o el topic para un nuevo análisis.", kind="warning")
                else:
                    _topic        = selected_topic
                    _section      = section_md or "(No se encontró fragmento.)"
                    _action       = action
                    _ctx          = context
                    _instr        = topic_instruction(_action)
                    prompt        = build_topic_prompt(_ctx, _topic, _section, _action, _instr, module=selected_stem)
                    _cases_count  = len(_load_relevant_cases(module=selected_stem, max_cases=5))
                    _loading_msg  = get_loading_message(_action, _cases_count > 0)

                    def _run():
                        log_activity(
                            "section_action",
                            module=selected_stem,
                            topic=_topic,
                            action=_action,
                            prompt=prompt,
                        )
                        if AppState.is_simulation():
                            answer = _simulate_section_output(_action, _topic)
                        else:
                            answer = _llm_call_with_cases(prompt, _ctx, _loading_msg, _cases_count, module=selected_stem)
                        AppState.append_chat("user", f"📌 {_topic} → {_action}")
                        AppState.append_chat("assistant", answer)
                        st.session_state.pop("_last_section_output", None)
                        st.session_state.pop("_last_section_fb", None)
                        _fb_key = f"sec_{selected_stem}_{_topic}_{_action}"
                        st.session_state.pop(f"_fb_state_{_fb_key}", None)
                        AppState.set_active_tools_tab(2)

                    _confirm_and_run(f"Analizar: {_topic}", _run, debug_prompt=prompt, cases_count=_cases_count)

        if _ia_enabled(selected_stem):
            st.markdown(
                "<div style='font-size:0.75rem;color:#888;margin-top:4px;margin-bottom:16px;text-align:center'>"
                "⚠ La IA puede cometer errores. Revisá sus respuestas.</div>",
                unsafe_allow_html=True,
            )


# Tab 2: Árboles ──────────────────────────────────────────────────────────────
def _render_trees_tab(
    selected_stem: str,
    context: str,
    trees_this_module: list,
) -> None:
    if not trees_this_module:
        st.markdown("<div style='margin-top:0'></div>", unsafe_allow_html=True)
        st.caption("No hay árboles de decisión para este módulo.")
        return

    tree_titles   = [t.get("title", t["id"]) for t in trees_this_module]
    tree_by_title = {t.get("title", t["id"]): t for t in trees_this_module}

    if st.session_state.get("tree_selector") not in tree_by_title:
        st.session_state["tree_selector"] = tree_titles[0] if len(tree_titles) == 1 else None

    def _on_change():
        tree = tree_by_title.get(st.session_state.get("tree_selector"))
        if tree:
            tree_engine_reset(tree, tree_engine_init(tree))

    selected_title = st.selectbox(
        "Árbol",
        [None] + tree_titles,
        format_func=lambda x: "Seleccioná un árbol de decisión..." if x is None else x,
        key="tree_selector",
        on_change=_on_change,
        label_visibility="collapsed",
    )

    if not selected_title:
        n = len(tree_titles)
        cap_col, link_col = st.columns([0.65, 0.35], vertical_alignment="center")
        with cap_col:
            st.caption(f"Hay {n} árbol{'es' if n > 1 else ''} disponible{'s' if n > 1 else ''} para este módulo.")
        with link_col:
            _sel_unc_key  = f"tree_sel_uncharted_open_{selected_stem}"
            _sel_unc_open = st.session_state.get(_sel_unc_key, False)
            _toggle_label = "✕ Cerrar" if _sel_unc_open else "⚠ No está mi árbol"
            if st.button(_toggle_label, key=f"tree_sel_unc_btn_{selected_stem}", width='stretch'):
                st.session_state[_sel_unc_key] = not _sel_unc_open
                _rerun_tools_panel()
        if st.session_state.get(_sel_unc_key):
            from engine.tree_engine import _render_missing_tree_form
            _render_missing_tree_form(selected_stem, f"sel_{selected_stem}")
        return

    tree   = tree_by_title[selected_title]
    key    = tree_engine_init(tree)
    result = tree_engine_render(tree, key)

    # Registrar que el usuario está en este árbol (para detectar abandono vs completado)
    st.session_state["_active_tree_id"]    = tree.get("id", "")
    st.session_state["_active_tree_title"] = tree.get("title", "")

    if result.get("done") and result.get("leaf"):
        leaf     = result["leaf"]
        decision = leaf.get("decision", "(sin decisión)")
        post     = leaf.get("post_actions", {}) or {}
        mode     = (post.get("mode") or "resolved").strip()

        def _reset_tree_result() -> None:
            tree_engine_reset(tree, key)
            _leaf_state_key = f"_tree_leaf_result_{tree['id']}"
            for _k in (
                _leaf_state_key,
                _leaf_state_key + "_chat_added",
                _leaf_state_key + "_via_analizar",
            ):
                st.session_state.pop(_k, None)
            _feedback_prefix = f"tools_{tree['id']}_leaf_feedback"
            for _k in list(st.session_state.keys()):
                if str(_k).startswith(_feedback_prefix):
                    st.session_state.pop(_k, None)
            st.session_state.pop("_active_tree_id", None)
            st.session_state.pop("_active_tree_title", None)
            _rerun_tools_panel()

        if st.button("✕", key=f"tree_reset_result_{tree['id']}"):
            _reset_tree_result()

        if mode == "resolved":
            st.success(f"✅ {decision}")
        elif mode == "needs_human_review":
            reason = post.get("reason", "Requiere validación humana.")
            st.warning(f"⚠ {decision}\n\n**Intervención requerida:** {reason}")
            for step in (post.get("next_steps") or []):
                st.markdown(f"- {step}")
        elif mode == "llm_value_add":
            st.info(f"💡 {decision}")
        else:
            st.warning(f"Modo post_actions desconocido: {mode}")

        _render_leaf_assets(leaf)

        # Registrar resultado del árbol para que el tab Asistente lo incorpore al chat
        # Se actualiza en cada render del leaf — idempotente por run_id
        _run_id = result.get("run", {}).get("run_id") if result.get("export") else result.get("trail", [{}])[0].get("tree_id", "")
        _leaf_state_key = f"_tree_leaf_result_{tree['id']}"
        if not st.session_state.get(_leaf_state_key + "_chat_added"):
            log_activity(
                "tree_completed",
                module=selected_stem,
                tree=tree.get("title", ""),
                tree_id=tree.get("id", ""),
                decision=decision,
                mode=mode,
                leaf_id=result.get("leaf_id", ""),
            )
            st.session_state[_leaf_state_key] = {
                "tree_title": tree.get("title", ""),
                "decision":   decision,
                "mode":       mode,
                "leaf_id":    result.get("leaf_id", ""),
            }

        # Feedback form post-leaf
        case_id = result.get("case_id", "")
        leaf_id = result.get("leaf_id", "")
        if case_id and leaf_id:
            render_leaf_feedback_form(
                leaf    = leaf,
                case_id = case_id,
                key     = f"tools_{tree['id']}",
                leaf_id = leaf_id,
            )

        # IA value-add — con selector de modo (la pregunta admite múltiples enfoques)
        if mode == "llm_value_add":
            _orig_q = st.session_state.get("_last_tree_question", "")
            if _orig_q:
                st.markdown(
                    f"<div style='font-size:0.88rem;color:#555;margin:6px 0 10px 0'>"
                    f"💬 ¿Querés que profundice sobre <b>{tree.get('title', '')}</b> "
                    f"en relación a tu pregunta?</div>",
                    unsafe_allow_html=True,
                )
            if st.button("Analizar", width='stretch', key=f"vaa_{tree['id']}"):
                if not _ia_enabled(selected_stem):
                    _ia_disabled_msg()
                else:
                    _export   = result.get("export", {}) or {}
                    va_prompt = build_tree_value_add_prompt(
                        _export, module=selected_stem
                    )
                    log_activity(
                        "tree_action",
                        module=selected_stem,
                        tree=tree.get("title", ""),
                        action="Analizar",
                        prompt=va_prompt,
                    )
                    # Marcar que este árbol fue procesado via Analizar
                    # para que el tab Asistente no agregue la burbuja de decisión
                    _leaf_state_key = f"_tree_leaf_result_{tree['id']}"
                    st.session_state[_leaf_state_key + "_via_analizar"] = True
                    st.session_state["_depth_selector"] = {
                        "prompt":        va_prompt,
                        "context":       context,
                        "exclude_modes": [],
                        "silent":        True,
                        "tree_title":    tree.get("title", ""),  # para el evento en el chat
                    }
                    AppState.set_active_tools_tab(2)
                    _rerun_tools_panel()

        # Debug — siempre al final para no interrumpir el flujo de decisión
        if AppState.debug_mode():
            with st.expander("🔎 Run export (JSON)"):
                st.json(result.get("export", {}))
            with st.expander("🧾 Audit trail"):
                st.json(result.get("trail", []))


# Tab 3: Asistente ────────────────────────────────────────────────────────────

_ROUTING_STOPWORDS = {
    "que", "qué", "cual", "cuál", "como", "cómo", "hay", "entre", "para",
    "con", "una", "uno", "unos", "unas", "del", "las", "los", "por", "sobre",
    "esto", "este", "esta", "ese", "esa", "datasphere", "sap"
}


def _is_conceptual_or_comparative_query(question: str) -> bool:
    q = _norm(question)
    conceptual_terms = (
        "diferencia", "diferencias", "diferenciar", "comparar", "comparativa",
        "versus", " vs ", "que es", "qué es", "que significa", "qué significa",
        "explicame", "explicar", "relacion", "relación", "coexistencia",
        "hana vs", "hdlf vs", "hana db", "hana database", "hdlf"
    )
    decision_terms = (
        "que conviene", "qué conviene", "cual conviene", "cuál conviene",
        "que topologia", "qué topología", "que topologia usar", "qué topología usar",
        "recomendas", "recomendás", "decidir", "decidimos", "elegir",
        "cual uso", "cuál uso", "que uso", "qué uso"
    )
    return any(t in q for t in conceptual_terms) and not any(t in q for t in decision_terms)


def _significant_tokens(text: str) -> set[str]:
    tokens = set()
    for tok in re.findall(r"[\w/]+", _norm(text)):
        tok = tok.strip("_")
        if len(tok) < 3 or tok in _ROUTING_STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def _routing_token_variants(tokens: set[str]) -> set[str]:
    """Variantes livianas para matching editorial, no para texto final.

    Evita que diferencias simples como singular/plural o infinitivo/conjugado
    hagan invisible un asset curado: transporta <> transportar, chains <> chain.
    """
    variants = set(tokens)
    for token in tokens:
        if len(token) > 4 and token.endswith("s"):
            variants.add(token[:-1])
        if len(token) >= 8:
            variants.add(token[:8])
    return variants


_ASSET_GENERIC_TOKEN_ROOTS = (
    "configur", "instal", "conect", "transport", "import",
    "activ", "particion", "integr"
)

_ASSET_GENERIC_TOKENS = {
    "how", "danwor", "datasphere", "template", "plantilla",
    "documentacion", "documento", "referencia", "oficial",
    "guia", "paso"
}


def _asset_specific_token_hits(query_tokens: set[str], candidate_tokens: set[str]) -> set[str]:
    """Tokens que identifican el objeto real del asset, no el verbo generico.

    Ej: en "como conecto SAC con Datasphere", "conecto" y "datasphere" solo
    indican intencion/dominio; "sac" es lo que debe romper empates contra otros
    how-to de conexion.
    """
    hits = query_tokens & candidate_tokens
    return {
        token for token in hits
        if token not in _ASSET_GENERIC_TOKENS
        and not any(token.startswith(root) for root in _ASSET_GENERIC_TOKEN_ROOTS)
    }


def _is_reference_library_module(stem: str | None, label: str | None = None) -> bool:
    text = _norm(" ".join(part for part in (stem or "", label or "") if part))
    return "biblioteca" in text and "referencia" in text


def _section_candidate_text(section_match: dict) -> str:
    return " ".join(
        str(part or "")
        for part in (
            section_match.get("heading"),
            section_match.get("module_label"),
            " ".join(section_match.get("keywords") or []),
            section_match.get("section_md"),
        )
    )


def _should_pair_section_with_asset(question: str, section_match: dict | None) -> bool:
    """Decide si una seccion merece acompanar a un how-to concreto.

    Evita duplicidades como "Referencias de Arquitectura" + "How-to particionar
    tablas remotas": la seccion comparte vocabulario amplio, pero no la accion
    especifica. En preguntas mixtas/conceptuales, preserva la doble recomendacion.
    """
    if not section_match:
        return False
    if _is_conceptual_or_comparative_query(question):
        return True

    q_norm = _norm(question)
    section_text = _norm(_section_candidate_text(section_match))
    q_tokens = _significant_tokens(question)
    section_tokens = _significant_tokens(section_text)
    specific_hits = _asset_specific_token_hits(q_tokens, section_tokens)
    if len(specific_hits) < 2:
        return False

    action_roots = [root for root in _ASSET_GENERIC_TOKEN_ROOTS if root in q_norm]
    if not action_roots:
        return True
    return any(root in section_text for root in action_roots)


def _display_heading(heading: str) -> str:
    """Devuelve un heading legible para UI/chat, sin tags visuales del markdown."""
    text = heading or ""
    text = re.sub(r"\{dw-[^{}]+\}", "", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _semantic_summary_from_fragment(fragment: str) -> str:
    m = re.search(
        r"(?ms)^\s*\{\{\s*semantic_summary\s*\}\}\s*$\n?(.*?)^\s*\{\{\s*/semantic_summary\s*\}\}\s*$",
        fragment or "",
    )
    return (m.group(1) or "").strip() if m else ""


def _score_section_candidate(question: str, heading: str, keywords: list[str], section_md: str, module_scope: str = "") -> int:
    q_norm = _norm(question)
    q_tokens = _significant_tokens(question)
    heading_tokens = _significant_tokens(_display_heading(heading))
    summary = _semantic_summary_from_fragment(section_md)
    summary_tokens = _significant_tokens(summary)
    scope_tokens = _significant_tokens(module_scope)

    score = 0
    for kw in keywords or []:
        kw_norm = _norm(kw)
        if not kw_norm:
            continue
        if kw_norm in q_norm:
            score += 14 if len(kw_norm.split()) > 1 else 8
            continue
        kw_tokens = _significant_tokens(kw)
        hits = len(q_tokens & kw_tokens)
        if hits >= 2:
            score += hits * 3
            if hits == len(kw_tokens):
                score += 4

    heading_hits = len(q_tokens & heading_tokens)
    if heading_hits >= 2:
        score += heading_hits * 2

    summary_hits = len(q_tokens & summary_tokens)
    if summary_hits >= 2:
        score += summary_hits * 2

    # Module scope is a tie-breaker only; broad module vocabulary should not
    # route a question by itself.
    if score > 0:
        scope_hits = len(q_tokens & scope_tokens)
        if scope_hits >= 2:
            score += min(scope_hits, 4)

    return score


def _find_cross_module_section(question: str, current_stem: str) -> dict | None:
    """
    Busca una seccion relevante fuera del modulo activo sin abrir todo el corpus.

    Solo mira metadata editorial: frontmatter scope, section_meta,
    semantic_summary y headings. Si el match no es claro, deja que siga el
    fallback LLM actual.
    """
    candidates: list[tuple[int, dict]] = []
    content_paths = list(CONTENT_DIR.glob("*.md"))
    appendices_dir = CONTENT_DIR / "apendices"
    if appendices_dir.exists():
        content_paths.extend(appendices_dir.glob("*.md"))

    for path in sorted(content_paths, key=lambda p: p.name.lower()):
        stem = path.stem
        if stem == current_stem:
            continue
        try:
            md = read_text_cached(str(path))
            meta, body = split_front_matter(md)
            sections = parse_section_meta(body)
        except Exception:
            continue

        module_scope = str(meta.get("scope") or "")
        module_label = module_label_from_md(stem, md)
        for section in sections:
            heading = section.get("heading", "")
            if not heading:
                continue
            section_md = extract_section_by_heading(body, heading, level=(2, 3)) or ""
            score = _score_section_candidate(
                question,
                heading,
                section.get("keywords") or [],
                section_md,
                module_scope=module_scope,
            )
            if score >= 12:
                candidates.append((
                    score,
                    {
                        "module": stem,
                        "module_label": module_label,
                        "path": str(path),
                        "heading": heading,
                        "section_md": section_md,
                        "score": score,
                    },
                ))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    if second_score and best_score - second_score < 4:
        return None
    return best


# ── Intent Section Detection ──────────────────────────────────────────────────
def _find_matching_section(question: str, context: str) -> dict | None:
    """
    Busca una sección del módulo activo relevante para la pregunta.

    Nivel 1 — ACTIVO: {{section_meta: keywords="..."}} en el .md
      Matching explícito por keywords declaradas en el contenido.
      Máxima precisión — fuente de verdad para el routing de secciones.

    Nivel 2 — DESHABILITADO (plug-and-play, ver bloque comentado al final)
      Matching por texto libre sobre el cuerpo de cada sección.
      Deshabilitado porque en dominios técnicos con vocabulario denso
      (ej: "conexion", "abap", "datos" aparecen en casi todas las secciones)
      genera falsos positivos frecuentes que degradan la experiencia.
      Para rehabilitar: descomentar el bloque al final de esta función
      y ajustar MIN_SECTION_WORDS en config.py (valor sugerido: 4+).
    """
    try:
        from core.content import (
            parse_section_meta, parse_section_excludes,
            split_front_matter,
        )
        _, body = split_front_matter(context)
    except Exception:
        return None

    q_norm = _norm(question)

    try:
        section_excludes = parse_section_excludes(body)
    except Exception:
        section_excludes = {}

    def _is_excluded(heading: str) -> bool:
        excl = [_norm(k) for k in section_excludes.get(heading, [])]
        return bool(excl) and any(ex in q_norm for ex in excl)

    # ── Nivel 1: section_meta explícito ──────────────────────────────────────
    try:
        sections = parse_section_meta(body)
        q_tokens = _significant_tokens(question)
        scored_sections: list[tuple[int, dict]] = []
        for section in sections:
            if _is_excluded(section.get("heading", "")):
                continue
            keywords = [_norm(k) for k in section.get("keywords", [])]
            score = 0
            for kw in keywords:
                if not kw:
                    continue
                if kw in q_norm:
                    score += 10
                    continue
                kw_tokens = _significant_tokens(kw)
                if not kw_tokens:
                    continue
                hits = len(q_tokens & kw_tokens)
                if hits >= 2:
                    # Premia keywords específicas como "diferencia HANA HDLF"
                    # sin exigir que la frase aparezca exactamente igual.
                    score += hits * 2
                    if hits == len(kw_tokens):
                        score += 3
            if score > 0:
                scored_sections.append((score, section))
        if scored_sections:
            scored_sections.sort(key=lambda x: -x[0])
            return scored_sections[0][1]
    except Exception:
        pass

    # ── Nivel 2: texto libre — DESHABILITADO ─────────────────────────────────
    # Para rehabilitar: descomentar el bloque siguiente y ajustar
    # MIN_SECTION_WORDS en config.py (actualmente = 3, sugerido: 4+)
    #
    # from config import MIN_SECTION_WORDS as MIN_WORDS, SECTION_STOPWORDS as DOMAIN_STOPWORDS
    # from core.content import parse_markdown_headings, strip_section_meta
    # try:
    #     clean_body = strip_section_meta(body)
    #     headings   = parse_markdown_headings(clean_body)
    #     q_words = [re.sub(r'[^\w]', '', w) for w in q_norm.split() if len(w) > 3]
    #     q_words = [w for w in q_words if len(w) > 3 and w not in DOMAIN_STOPWORDS]
    #     if not q_words:
    #         return None
    #     for i, (lvl, title, hpos) in enumerate(headings):
    #         if _is_excluded(title):
    #             continue
    #         next_pos     = headings[i + 1][2] if i + 1 < len(headings) else len(clean_body)
    #         section_text = _norm(clean_body[hpos:next_pos])
    #         hits         = sum(1 for w in q_words if w in section_text)
    #         if hits >= MIN_WORDS:
    #             return {"heading": title, "keywords": q_words}
    # except Exception:
    #     pass

    return None


# ── Intent Asset Detection ────────────────────────────────────────────────────
_ASSET_INTENT_TERMS = (
    "template", "plantilla", "codigo", "código", "ejemplo", "ver recurso",
    "ver template", "mostrar template", "descargar", "copiar", "crear cds",
    "escribir cds", "implementacion", "implementación", "documentacion",
    "documentación", "link", "enlace", "sap help", "nota sap", "kba",
    "how-to", "guia", "guía", "paso a paso", "configurar", "configuro",
    "configuración", "configuracion", "instalar", "conectar", "transportar",
    "transporta", "transporte", "transportes",
    "activar", "fuente oficial", "documento", "referencia",
    "como integro", "cómo integro", "como integrar", "cómo integrar",
    "integrar process", "process chain", "process chains", "task chain", "task chains"
)


def _is_asset_seeking_query(question: str) -> bool:
    q_lower = _norm(question)
    if any(term in q_lower for term in _ASSET_INTENT_TERMS):
        return True
    # Raices operativas comunes. Evita depender de cada conjugacion:
    # conectar/conecto/conectamos, transportar/transporta, etc.
    return any(
        root in q_lower
        for root in ("configur", "instal", "conect", "transport", "import", "activ", "particion", "integr")
    )


def _score_asset_candidate(question: str, asset: dict) -> int:
    q_lower = _norm(question)
    q_tokens = _significant_tokens(question)
    q_tokens_loose = _routing_token_variants(q_tokens)
    keywords = [_norm(k) for k in (asset.get("keywords") or [])]
    desc = _norm(str(asset.get("description") or ""))
    score = 0

    for kw in keywords:
        if not kw:
            continue
        if kw in q_lower:
            score += 12
            continue
        if "bw bridge" in kw and "bw bridge" in q_lower:
            score += 6
        for root in ("configur", "instal", "conect", "transport", "import", "activ", "particion"):
            if root in kw and root in q_lower:
                score += 6
                break
        if "datasphere" in kw and "datasphere" in q_lower:
            score += 4
        kw_tokens = _significant_tokens(kw)
        kw_tokens_loose = _routing_token_variants(kw_tokens)
        specific_hits = _asset_specific_token_hits(q_tokens, kw_tokens)
        if specific_hits:
            # Premia el objeto consultado (SAC, PowerBI, BW Bridge, process/task
            # chain, etc.) por encima de verbos y dominios compartidos.
            score += 8 * len(specific_hits)
        hits = len(q_tokens_loose & kw_tokens_loose)
        if hits >= 2:
            score += hits * 3
            if hits == len(kw_tokens_loose):
                score += 4
        elif hits == 1:
            # Un token especifico del objeto consultado (SAC, OpenID, BWMT,
            # PowerBI, etc.) debe pesar mas que el verbo generico "conectar".
            exact_hits = q_tokens & kw_tokens
            if exact_hits:
                score += 6

    desc_tokens = _significant_tokens(desc)
    desc_tokens_loose = _routing_token_variants(desc_tokens)
    desc_specific_hits = _asset_specific_token_hits(q_tokens, desc_tokens)
    if desc_specific_hits:
        score += 4 * len(desc_specific_hits)
    desc_hits = len(q_tokens_loose & desc_tokens_loose)
    if desc_hits >= 2:
        score += desc_hits
    elif desc_hits == 1 and (q_tokens & desc_tokens):
        score += 3
    for root in ("configur", "instal", "conect", "transport", "import", "activ", "particion"):
        if root in desc and root in q_lower:
            score += 3
            break
    if "datasphere" in desc and "datasphere" in q_lower:
        score += 2
    if "bw bridge" in desc and "bw bridge" in q_lower:
        score += 3
    return score


def _find_best_asset(
    question: str,
    assets: list[dict],
    *,
    require_intent: bool = True,
    min_score: int = 8,
    min_gap: int = 3,
) -> dict | None:
    if require_intent and not _is_asset_seeking_query(question):
        return None
    scored: list[tuple[int, dict]] = []
    for asset in assets or []:
        score = _score_asset_candidate(question, asset)
        if score >= min_score:
            scored.append((score, asset))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if second_score and best_score - second_score < min_gap:
        return None
    return best


def _find_matching_asset(
    question: str,
    selected_path: str,
    context: str,
    *,
    allow_strong: bool = False,
) -> dict | None:
    try:
        from core.content import split_front_matter
        meta, _ = split_front_matter(context)
        assets = meta.get("intent_assets") or []
    except Exception:
        return None

    q_lower = _norm(question)
    conceptual_terms = (
        "diferencia", "comparar", "comparativa", "versus", " vs ", "mejor",
        "funciona mejor", "recomendas", "recomendás", "conviene", "por que",
        "por qué", "explicame", "explicar", "corrige", "corregir", "malinformando",
        "ddic", "view entity", "entity", "define view entity", "define view"
    )
    has_intent = _is_asset_seeking_query(question)
    if any(term in q_lower for term in conceptual_terms) and not (has_intent or allow_strong):
        return None
    return _find_best_asset(
        question,
        assets,
        require_intent=not allow_strong,
        min_score=8 if has_intent else 12,
        min_gap=3 if has_intent else 4,
    )


def _find_cross_module_asset(question: str, current_stem: str, *, allow_strong: bool = False) -> dict | None:
    has_intent = _is_asset_seeking_query(question)
    if not has_intent and not allow_strong:
        return None
    min_score = 8 if has_intent else 12
    min_gap = 3 if has_intent else 4
    candidates: list[tuple[int, dict]] = []
    content_paths = list(CONTENT_DIR.glob("*.md"))
    appendices_dir = CONTENT_DIR / "apendices"
    if appendices_dir.exists():
        content_paths.extend(appendices_dir.glob("*.md"))

    for path in sorted(content_paths, key=lambda p: p.name.lower()):
        if path.stem == current_stem:
            continue
        try:
            md = read_text_cached(str(path))
            meta, _body = split_front_matter(md)
            module_label = module_label_from_md(path.stem, md)
        except Exception:
            continue
        for asset in meta.get("intent_assets") or []:
            score = _score_asset_candidate(question, asset)
            if score >= min_score:
                asset = dict(asset)
                asset["_module"] = path.stem
                asset["_module_label"] = module_label
                candidates.append((score, asset))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    if second_score and best_score - second_score < min_gap:
        return None
    return best


def _needs_cds_delta_choice(question: str) -> bool:
    q = _norm(question)
    cds_terms = ("cds", "odp", "abap_cds", "datasphere")
    delta_terms = (
        "delta", "incremental", "incrementalidad", "puntero", "cdc",
        "change data capture", "cambios", "actualizados", "actualizacion",
        "actualización"
    )
    already_specific = (
        "byelement", "by element", "change data capture", "mapping",
        "join", "joins", "tabla principal", "role main", "release",
        "version", "versión", "s4hana 202", "abap 7", "rap"
    )
    asks_how = (
        "como", "cómo", "incluir", "agregar", "configurar", "implementar",
        "soporte", "soporta", "me conviene", "recomendas", "recomendás"
    )
    return (
        any(term in q for term in cds_terms)
        and any(term in q for term in delta_terms)
        and any(term in q for term in asks_how)
        and not any(term in q for term in already_specific)
    )


# ── Tree intent detection ─────────────────────────────────────────────────────
def _find_matching_tree(question: str, trees: list) -> object | None:
    """
    Nivel A: scoring local con filtros exclude_if y min_sources.

    Los arboles se ofrecen solo cuando la metadata local alcanza. No usamos
    LLM arbitro como fallback porque ante preguntas operativas/how-to tiende a
    fabricar afinidad con arboles amplios (ej. Topologia de Espacios).
    """
    if not trees:
        return None

    q_norm = _norm(question)
    if _is_conceptual_or_comparative_query(question):
        # Los árboles resuelven decisiones. Para preguntas conceptuales o
        # comparativas, una sección/LLM debe ganar salvo que el usuario pida
        # explícitamente decidir, elegir o recomendar una topología.
        return None
    scored = []

    for tree in trees:
        title = _norm(tree.get("title", "") or "")
        tags  = [_norm(t) for t in (tree.tags if hasattr(tree, "tags") else tree.get("tags", []) or [])]

        raw_spec   = getattr(tree, "source_path", "") or tree.get("_path", "") or ""
        keywords:   list[str] = []
        exclude_if: list[str] = []
        try:
            from pathlib import Path as _Path
            if raw_spec:
                _spec      = yaml.safe_load(_Path(raw_spec).read_text(encoding="utf-8"))
                keywords   = [_norm(k) for k in (_spec.get("keywords")   or [])]
                exclude_if = [_norm(k) for k in (_spec.get("exclude_if") or [])]
        except Exception:
            pass

        if any(ex in q_norm for ex in exclude_if):
            continue

        sources_hit = set()
        score = 0

        for kw in keywords:
            if kw in q_norm:
                score += 3
                sources_hit.add("keywords")
        for tag in tags:
            if tag in q_norm:
                score += 2
                sources_hit.add("tags")
        for word in title.split():
            if len(word) > 3 and word in q_norm:
                score += 1
                sources_hit.add("title")

        if score > 0 and len(sources_hit) >= 2:
            scored.append((score, tree))

    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    return None


def _render_assistant_tab(
    selected_stem: str,
    context: str,
    quick_action: str | None,
    trees_this_module: list | None = None,
    selected_path: str = "",
) -> None:
    # ── Procesar resultado de árbol al volver al tab Asistente ────────────────
    # Buscar si hay un árbol completado cuya decisión no fue volcada al chat aún
    for _tree in (trees_this_module or []):
        _tid = _tree.get("id") if isinstance(_tree, dict) else getattr(_tree, "id", "")
        _lsk = f"_tree_leaf_result_{_tid}"
        _leaf_result = st.session_state.get(_lsk)
        if _leaf_result and not st.session_state.get(_lsk + "_chat_added"):
            _t_title  = _leaf_result["tree_title"]
            _decision = _leaf_result["decision"]
            _mode     = _leaf_result["mode"]
            _via_analizar = st.session_state.get(_lsk + "_via_analizar", False)

            # Evento de sistema: árbol completado
            AppState.append_chat("event", f"🌳 Árbol completado: **{_t_title}**")

            if not _via_analizar:
                # Sin Analizar: volcar la decisión como burbuja del asistente + rating
                _icon = {"resolved": "✅", "needs_human_review": "⚠️", "llm_value_add": "💡"}.get(_mode, "💡")
                AppState.append_chat("assistant", f"{_icon} {_decision}")

            # Marcar como procesado para no duplicar
            st.session_state[_lsk + "_chat_added"] = True
            _rerun_tools_panel()

    # Detectar árbol abandonado (estaba activo pero sin hoja completada)
    _active_tid   = st.session_state.get("_active_tree_id", "")
    _active_title = st.session_state.get("_active_tree_title", "")
    if _active_tid and _active_title:
        _lsk = f"_tree_leaf_result_{_active_tid}"
        _has_result   = bool(st.session_state.get(_lsk))
        _already_noted = st.session_state.get(f"_tree_abandoned_noted_{_active_tid}", False)
        if not _has_result and not _already_noted:
            AppState.append_chat("event", f"🌳 Árbol interrumpido: **{_active_title}**")
            st.session_state[f"_tree_abandoned_noted_{_active_tid}"] = True
            # Limpiar tracking activo
            st.session_state.pop("_active_tree_id", None)
            st.session_state.pop("_active_tree_title", None)
            _rerun_tools_panel()
        elif _has_result:
            # Árbol completado — limpiar tracking activo
            st.session_state.pop("_active_tree_id", None)
            st.session_state.pop("_active_tree_title", None)

    # ── Acciones rápidas ──────────────────────────────────────────────────────
    _quick_action_blocked = False
    if quick_action:
        if not _ia_enabled(selected_stem):
            _quick_action_blocked = True
        else:
            _ctx    = context
            _prompt = QUICK_ACTIONS[quick_action].strip()
            _label  = quick_action
            _prompt_guidance, _context_guidance = _quick_action_guidance_from_context(context, _label)
            if _prompt_guidance:
                _prompt = f"{_prompt}\n\n{_prompt_guidance}"
            if _context_guidance:
                _ctx = f"{_ctx}\n\n{_context_guidance}"

            _cases_count = len(_load_relevant_cases(module=selected_stem, max_cases=5))
            if _label == "Ejemplos prácticos":
                _appendix_context = _appendix_case_context()
                if _appendix_context:
                    _ctx = _ctx + _appendix_context
                    _loading_msg = "Preparando ejemplos · cruzando apéndices internos..."
                else:
                    _loading_msg = get_loading_message(_label, _cases_count > 0)
            else:
                _loading_msg = get_loading_message(_label, _cases_count > 0)

            def _run_qa():
                log_activity("quick_action", module=selected_stem, action=_label, prompt=_prompt)
                answer = _llm_call_direct(
                    _prompt,
                    _ctx,
                    _loading_msg,
                    module=selected_stem,
                    max_tokens=1800 if _label == "Ejemplos prácticos" else 1200,
                )
                AppState.append_chat("user", f"Acción: {_label}")
                AppState.append_chat("assistant", answer)
                if _should_suggest_quota_ratio(answer, quick_action=_label, selected_stem=selected_stem):
                    st.session_state["_section_suggestion"] = {
                        "heading": _QUOTA_RATIO_HEADING,
                        "prompt": (
                            "Explicá cómo aplicar la guía de distribución porcentual de quota "
                            "para dimensionar espacios en SAP Datasphere."
                        ),
                    }
                st.session_state["_last_qa_fb"] = {
                    "action": _label, "topic": selected_stem, "module": selected_stem,
                }
                st.session_state.pop(f"_fb_state_qa_{selected_stem}_{_label}", None)

            _confirm_and_run(f"Acción rápida: {_label}", _run_qa, debug_prompt=_prompt, cases_count=_cases_count)

            _last_qa = st.session_state.get("_last_qa_fb")
            if _last_qa and _last_qa.get("module") == selected_stem:
                _fb_key_qa = f"qa_{_last_qa['module']}_{_last_qa['action']}"
                _render_feedback_buttons(
                    fb_key=_fb_key_qa,
                    action=_last_qa["action"],
                    topic=_last_qa["topic"],
                    module=_last_qa["module"],
                    feedback_type="quick_action",
                )

    # ── CSS ───────────────────────────────────────────────────────────────────
    st.markdown("""
<style>
.dw-chat-msg { margin-bottom: 10px !important; }
.st-key-depth_metodologico button p,
.st-key-depth_tecnico button p,
.st-key-depth_ejemplos button p,
.st-key-depth_decision button p { text-align: left !important; }
</style>
<script>
(function() {
  function disableAutocomplete() {
    document.querySelectorAll('input[type="text"]').forEach(function(el) {
      el.setAttribute('autocomplete', 'off');
    });
  }
  disableAutocomplete();
  new MutationObserver(disableAutocomplete).observe(document.body, { childList: true, subtree: true });
})();
</script>
""", unsafe_allow_html=True)

    chat       = AppState.chat()
    suggestion = st.session_state.get("_tree_suggestion")

    _depth_sel_active        = bool(st.session_state.get("_depth_selector"))
    _cds_delta_sel_active    = bool(st.session_state.get("_cds_delta_selector"))
    _pending_classify_active = bool(st.session_state.get("_pending_classify"))
    _pending_free_active     = bool(st.session_state.get("_pending_free_question"))
    _processing              = bool(st.session_state.get("_processing"))
    _pending_direct_active   = bool(st.session_state.get("_pending_llm_direct"))
    _assistant_busy          = (
        _has_pending_action()
        or _pending_free_active
        or _pending_classify_active
        or _processing
        or _pending_direct_active
    )

    # Resolver primero el router liviano antes de pintar el chat. Si la pregunta
    # matchea seccion/arbol/asset, evita un render intermedio vacio con apariencia
    # de llamada LLM. Si no matchea, este bloque agenda _pending_classify y el
    # siguiente render muestra el preloader real de LLM.
    if _pending_free_active:
        st.session_state.pop("_processing", None)
        _flush_pending_free_question(selected_stem, selected_path, context, trees_this_module)
        return

    _defer_pending_action = False

    if chat or suggestion or _depth_sel_active or _cds_delta_sel_active or _pending_free_active or _pending_classify_active or _pending_direct_active or _processing:
        with st.container(height=420, key="assistant_chat_surface"):
            _chat_surface_id = f"dw-chat-surface-{selected_stem}"
            st.markdown(f"<div id='{_chat_surface_id}' class='dw-chat-surface-marker'></div>", unsafe_allow_html=True)
            st.components.v1.html(
                f"""<script>
(function() {{
  function paintChatSurface(attempts) {{
    var marker = window.parent.document.getElementById('{_chat_surface_id}');
    if (!marker) {{
      if (attempts > 0) setTimeout(function() {{ paintChatSurface(attempts - 1); }}, 120);
      return;
    }}

    var node = marker.parentElement;
    var scrollBox = null;
    while (node && node !== window.parent.document.body) {{
      var style = window.parent.getComputedStyle(node);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') {{
        scrollBox = node;
        break;
      }}
      node = node.parentElement;
    }}

    if (!scrollBox) return;
    scrollBox.style.marginTop = '-10px';
    scrollBox.style.background = '#ffffff';
    scrollBox.style.backgroundColor = '#ffffff';
    scrollBox.style.borderRadius = '8px';
    Array.prototype.forEach.call(scrollBox.children, function(child) {{
      child.style.background = '#ffffff';
      child.style.backgroundColor = '#ffffff';
    }});
  }}
  paintChatSurface(12);
}})();
</script>""",
                height=0,
            )
            _chat_slice  = chat[-12:]
            _chat_offset = max(0, len(chat) - 12)
            _last_asst_abs = next(
                (_chat_offset + _i for _i, (r, _) in reversed(list(enumerate(_chat_slice))) if r == "assistant"),
                None,
            )

            # ── Historial ─────────────────────────────────────────────────────
            for _i, (role, msg) in enumerate(_chat_slice):
                if role == "user":
                    st.markdown(
                        f"<div class='dw-chat-msg dw-chat-user'>"
                        f"<div class='dw-chat-label'>{_get_auth_user().get('first_name', 'Tú')}</div>"
                        f"{_to_html(msg)}</div>",
                        unsafe_allow_html=True,
                    )
                elif role == "event":
                    # Evento de sistema — línea neutra sin burbuja ni rating
                    st.markdown(
                        f"<div style='text-align:center;color:#aaa;font-size:0.8rem;"
                        f"margin:6px 0;padding:4px 0;border-top:1px solid rgba(0,0,0,0.06);"
                        f"border-bottom:1px solid rgba(0,0,0,0.06)'>{_to_html(msg)}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"<div class='dw-chat-msg dw-chat-assistant'>"
                        f"<div class='dw-chat-label'>Arquitecto Danwor</div>"
                        f"{_to_html(msg)}</div>",
                        unsafe_allow_html=True,
                    )
                    _abs_idx     = _chat_offset + _i
                    _chat_fb_key = f"chat_{selected_stem}_{_abs_idx}"
                    _fb_state    = st.session_state.get(f"_fb_state_{_chat_fb_key}", "idle")
                    _comment_key = f"_fb_comment_{_chat_fb_key}"

                    if _fb_state == "confirmed":
                        pass
                    elif _fb_state == "done_up":
                        show_msg("Feedback registrado. ¡Gracias!", kind="success")
                        st.session_state[f"_fb_state_{_chat_fb_key}"] = "confirmed"
                    else:
                        _is_last = (_abs_idx == _last_asst_abs)
                        if not _is_last:
                            st.markdown(
                                """
                                <div style="display:flex;gap:5px;margin:4px 0 10px 0;">
                                  <span style="display:inline-flex;align-items:center;justify-content:center;width:38px;height:34px;border:1px solid #d8dee8;border-radius:7px;background:#f1f4f8;color:#a8b2c1;opacity:.72;">👍</span>
                                  <span style="display:inline-flex;align-items:center;justify-content:center;width:38px;height:34px;border:1px solid #d8dee8;border-radius:7px;background:#f1f4f8;color:#a8b2c1;opacity:.72;">👎</span>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                            continue
                        if _fb_state == "idle":
                            _col1, _col2, _ = st.columns([0.09, 0.09, 0.82], gap=None)
                            with _col1:
                                if st.button("👍", key=f"btn_up_{_chat_fb_key}", help="Fue útil"):
                                    _save_ia_feedback("up", "", "chat_libre", msg[:60], selected_stem, "chat")
                                    st.session_state[f"_fb_state_{_chat_fb_key}"] = "done_up"
                                    _rerun_tools_panel()
                            with _col2:
                                if st.button("👎", key=f"btn_down_{_chat_fb_key}", help="No fue útil"):
                                    st.session_state[f"_fb_state_{_chat_fb_key}"] = "awaiting_comment"
                                    _rerun_tools_panel()
                        elif _fb_state in ("awaiting_comment", "done_down"):
                            _col1, _col2, _ = st.columns([0.09, 0.09, 0.82], gap=None)
                            with _col1:
                                st.button("👍", key=f"btn_up_{_chat_fb_key}", disabled=True)
                            with _col2:
                                st.button("👎", key=f"btn_down_{_chat_fb_key}", disabled=True)
                            _exp_label = "✅ Feedback registrado" if _fb_state == "done_down" else "💬 Contanos qué mejorar"
                            with st.expander(_exp_label, expanded=(_fb_state == "awaiting_comment")):
                                if _fb_state == "done_down":
                                    show_msg("Feedback registrado. Lo usaremos para mejorar.", kind="success")
                                    st.session_state[f"_fb_state_{_chat_fb_key}"] = "confirmed"
                                else:
                                    _comment = st.text_input(
                                        "¿Qué falló o qué mejorarías?",
                                        key=_comment_key,
                                        placeholder="Ej: respuesta muy genérica, no mencionó ODP...",
                                    )
                                    if st.button("Enviar", key=f"fb_send_{_chat_fb_key}"):
                                        if not _comment.strip():
                                            show_msg("Escribí al menos una línea antes de enviar.", kind="warning")
                                        else:
                                            _save_ia_feedback("down", _comment, "chat_libre", msg[:60], selected_stem, "chat")
                                            st.session_state[f"_fb_state_{_chat_fb_key}"] = "done_down"
                                            _rerun_tools_panel()

            # ── Indicador de procesamiento ────────────────────────────────────
            if _has_pending_action():
                _defer_pending_action = True
                st.session_state["_llm_spinner_slot"] = st.empty()
            elif _pending_free_active:
                st.session_state.pop("_processing", None)
                _flush_pending_free_question(selected_stem, selected_path, context, trees_this_module)
            elif _pending_classify_active:
                st.session_state.pop("_processing", None)
                _flush_pending_classify(selected_stem)
            elif _pending_direct_active:
                st.session_state.pop("_processing", None)
                _flush_pending_llm_direct(selected_stem)
            elif _processing:
                if _has_pending_action():
                    _defer_pending_action = True
                    st.session_state["_llm_spinner_slot"] = st.empty()
                else:
                    st.session_state.pop("_processing", None)
                    st.markdown(
                        "<div class='dw-chat-msg dw-chat-assistant'>"
                        "<div class='dw-chat-label'>Arquitecto Danwor</div>"
                        "<span style='color:#8a95a3;font-size:0.9rem'>⏳ Procesando tu consulta...</span>"
                        "</div>",
                        unsafe_allow_html=True,
                    )

            # ── Out-of-scope redirect — dos botones ───────────────────────────
            _oos_r = st.session_state.get("_oos_redirect")
            if _oos_r:
                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button(f"📖 Ir al módulo {_oos_r['module']}", width="stretch", key="oos_goto_btn"):
                        from config import CONTENT_DIR
                        from core.state import K_MODULE_SELECTOR
                        _target_num = str(_oos_r["module"]).zfill(2)
                        _matches = sorted(CONTENT_DIR.glob(f"{_target_num}_*.md"))
                        if _matches:
                            st.session_state["_pending_module_switch"] = _matches[0].stem
                        st.session_state.pop("_oos_redirect", None)
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()
                with col_b:
                    if st.button("💬 Ayudame igual", width="stretch", key="oos_llm_btn"):
                        _oos_prompt = (
                            f"{_oos_r['prompt']}\n\n"
                            "[Nota: esta pregunta está fuera del módulo activo. "
                            "Respondé lo mejor que puedas con el contexto disponible "
                            "e incluí al final del mensaje un aviso breve de que el tema "
                            "pertenece a otro módulo y que los resultados pueden ser menos precisos.]"
                        )
                        st.session_state.pop("_oos_redirect", None)
                        st.session_state["_pending_llm_direct"] = {
                            "prompt":  _oos_prompt,
                            "context": _oos_r["context"],
                        }
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

            # ── Selector de modo (chat libre ambiguo o árbol → Analizar) ──────
            _cds_delta_sel = st.session_state.get("_cds_delta_selector")
            if _cds_delta_sel:
                if not _cds_delta_sel.get("_msg_persisted"):
                    AppState.append_chat("assistant",
                        "Tu pregunta sobre delta en CDS puede resolverse por distintos patrones. "
                        "¿Querés que compare todas las opciones o que vaya directo a la recomendación más moderna?"
                    )
                    _cds_delta_sel["_msg_persisted"] = True
                    st.session_state["_cds_delta_selector"] = _cds_delta_sel
                    _rerun_tools_panel()

                _sel_ctx = _cds_delta_sel["context"]
                _sel_prompt = _cds_delta_sel["prompt"]
                _sel_module = _cds_delta_sel.get("module") or selected_stem
                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("Comparar opciones", width="stretch", key="cds_delta_all"):
                        st.session_state.pop("_cds_delta_selector", None)
                        st.session_state["_pending_llm_direct"] = {
                            "prompt": (
                                f"{_sel_prompt}\n\n"
                                "[Instrucción de respuesta: compará los patrones delta para CDS ODP: "
                                "FULL, delta.byElement y delta.changeDataCapture.mapping. "
                                "Explicá cuándo aplica cada uno, limitaciones, compatibilidad/release y postura Danwor.]"
                            ),
                            "context": _sel_ctx,
                            "module": _sel_module,
                        }
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()
                with c2:
                    if st.button("Recomendación moderna", width="stretch", key="cds_delta_modern"):
                        st.session_state.pop("_cds_delta_selector", None)
                        st.session_state["_pending_llm_direct"] = {
                            "prompt": (
                                f"{_sel_prompt}\n\n"
                                "[Instrucción de respuesta: recomendá el patrón moderno Danwor para delta CDS hacia Datasphere. "
                                "Priorizá CDC con @Analytics.dataExtraction.delta.changeDataCapture.mapping cuando haya joins, "
                                "múltiples tablas o necesidad robusta de deletions/cambios; usá delta.byElement solo para vistas simples "
                                "con timestamp confiable. Incluí advertencia de compatibilidad por release.]"
                            ),
                            "context": _sel_ctx,
                            "module": _sel_module,
                        }
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()
                with c3:
                    if st.button("Validar versión", width="stretch", key="cds_delta_release"):
                        st.session_state.pop("_cds_delta_selector", None)
                        AppState.append_chat("assistant",
                            "Para recomendar sin asumir de más, decime: versión de S/4HANA/ABAP, "
                            "si la CDS es simple o tiene joins, y si necesitás detectar borrados."
                        )
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

            _depth_sel = st.session_state.get("_depth_selector")
            if _depth_sel:
                # Persistir el mensaje del asistente si aún no está en el chat
                # Requiere rerun para que el container lo renderice
                if not _depth_sel.get("_msg_persisted"):
                    _ds_tree_title = _depth_sel.get("tree_title")
                    if _ds_tree_title:
                        # Viene del botón Analizar — agregar evento + decisión del árbol
                        # (el tab Asistente no lo procesó porque _via_analizar=True)
                        AppState.append_chat("event", f"🌳 Árbol completado: **{_ds_tree_title}**")
                        # Buscar la decisión guardada
                        for _tree in (trees_this_module or []):
                            _tid = _tree.get("id") if isinstance(_tree, dict) else getattr(_tree, "id", "")
                            _lsk = f"_tree_leaf_result_{_tid}"
                            _lr  = st.session_state.get(_lsk)
                            if _lr and _lr.get("tree_title") == _ds_tree_title:
                                _icon = {"resolved": "✅", "needs_human_review": "⚠️", "llm_value_add": "💡"}.get(_lr["mode"], "💡")
                                AppState.append_chat("assistant", f"{_icon} {_lr['decision']}")
                                st.session_state[_lsk + "_chat_added"] = True
                                break
                        AppState.append_chat("assistant",
                            "Tu consulta puede abordarse de distintas formas. "
                            "¿Cómo preferís que te responda?"
                        )
                    elif not _depth_sel.get("silent"):
                        AppState.append_chat("assistant",
                            "Tu consulta puede abordarse de distintas formas. "
                            "¿Cómo preferís que te responda?"
                        )
                    _depth_sel["_msg_persisted"] = True
                    st.session_state["_depth_selector"] = _depth_sel
                    _rerun_tools_panel()
                st.markdown("""
<style>
.dw-mode-option {
    display: flex; align-items: flex-start; gap: 10px;
    padding: 10px 14px; margin: 4px 0;
    background: #fff; border: 1px solid #e5e5e5; border-radius: 10px;
    cursor: pointer; transition: border-color 0.15s, background 0.15s;
    font-family: "Source Sans", sans-serif;
}
.dw-mode-option:hover { border-color: #F5A800; background: #fffbf0; }
.dw-mode-icon  { font-size: 1.2rem; margin-top: 1px; }
.dw-mode-label { font-weight: 600; font-size: 0.92rem; color: #111; }
.dw-mode-desc  { font-size: 0.80rem; color: #777; margin-top: 1px; }
.st-key-depth_metodologico button p,
.st-key-depth_tecnico button p,
.st-key-depth_ejemplos button p,
.st-key-depth_decision button p,
button[data-testid="stBaseButton-secondary"] [data-testid="stMarkdownContainer"] p,
button[kind="secondary"] p { text-align: left !important; }
</style>
""", unsafe_allow_html=True)
                _exclude_modes = _depth_sel.get("exclude_modes", [])
                for _mode_key, _mode in DEPTH_MODES.items():
                    if _mode_key in _exclude_modes:
                        continue
                    if st.button(
                        f"{_mode['label']}  —  {_mode['desc']}",
                        key=f"depth_{_mode_key}",
                        use_container_width=True,
                    ):
                        _fp = f"depth_{_mode_key}_{hash(_depth_sel.get('prompt',''))}"
                        if _llm_guard(_fp):
                            show_msg("Ya obtuviste una respuesta para esta opción. Elegí otra o hacé una nueva pregunta.", kind="warning")
                        else:
                            _sel_prompt      = _depth_sel["prompt"]
                            _sel_ctx         = _depth_sel["context"]
                            _enriched_prompt = (
                                f"{_sel_prompt}\n\n"
                                f"[Instrucción de respuesta: {_mode['instruction']}]"
                            )
                            st.session_state.pop("_depth_selector", None)
                            st.session_state["_pending_llm_direct"] = {
                                "prompt":  _enriched_prompt,
                                "context": _sel_ctx,
                            }
                            st.session_state.pop("_expand_chat_open", None)
                            _rerun_tools_panel()

            # ── Sugerencia de sección ─────────────────────────────────────────
            section_suggestion = st.session_state.get("_section_suggestion")
            if section_suggestion:
                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("📖 Ir a la sección", width="stretch", key="section_nav_btn"):
                        slug = _slugify(section_suggestion["heading"])
                        _target_module = section_suggestion.get("module")
                        if _target_module and _target_module != selected_stem:
                            st.session_state["_preserve_chat_on_module_switch"] = True
                            st.query_params["module"] = _target_module
                        st.query_params["section"] = slug
                        st.query_params["section_nav"] = "1"
                        st.rerun()
                with col_b:
                    if st.button("💬 Explicame vos", width="stretch", key="section_llm_btn"):
                        st.session_state["_pending_llm_direct"] = {
                            "prompt": section_suggestion["prompt"],
                            "context": section_suggestion.get("context") or context,
                            "module": section_suggestion.get("module") or selected_stem,
                        }
                        st.session_state.pop("_section_suggestion", None)
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

            # ── Sugerencia de asset ───────────────────────────────────────────
            asset_suggestion = st.session_state.get("_asset_suggestion")
            if asset_suggestion:
                asset      = asset_suggestion["asset"]
                desc       = asset.get("description", "Recurso técnico disponible")
                asset_path = asset.get("asset", "")
                language   = asset.get("language", "abap")
                is_external_asset = str(asset_path).lower().startswith(("http://", "https://"))
                col_a, col_b = st.columns(2)
                with col_a:
                    if is_external_asset:
                        if st.button("📎 Abrir recurso", width="stretch", key="asset_open_external_btn"):
                            st.session_state["_open_external_asset"] = {
                                "url": str(asset_path),
                                "desc": desc,
                                "suggestion_key": "_asset_suggestion",
                            }
                            st.session_state.pop("_active_tree_id", None)
                            st.session_state.pop("_active_tree_title", None)
                            _rerun_tools_panel()
                    elif st.button("📎 Ver template", width="stretch", key="asset_view_btn"):
                        st.session_state["_asset_open"] = {
                            "path": asset_path, "desc": desc,
                            "language": language, "selected_path": selected_path,
                        }
                        st.session_state.pop("_asset_suggestion", None)
                        _rerun_tools_panel()
                with col_b:
                    if st.button("💬 Explicame vos", width="stretch", key="asset_llm_btn"):
                        st.session_state["_pending_llm_direct"] = {"prompt": asset_suggestion["prompt"], "context": context}
                        st.session_state.pop("_asset_suggestion", None)
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

            # ── Sugerencia combinada: árbol + sección ─────────────────────────
            asset_section_suggestion = st.session_state.get("_asset_section_suggestion")
            if asset_section_suggestion:
                asset = asset_section_suggestion["asset"]
                desc = asset.get("description", "Recurso tecnico disponible")
                asset_path = asset.get("asset", "")
                language = asset.get("language", "abap")
                is_external_asset = str(asset_path).lower().startswith(("http://", "https://"))
                col_a, col_b, col_c = st.columns(3, gap="small")
                with col_a:
                    if st.button("📖 Abrir sección", width="stretch", key="asset_section_nav_btn"):
                        slug = _slugify(asset_section_suggestion["section_heading"])
                        _target_module = asset_section_suggestion.get("module")
                        if _target_module and _target_module != selected_stem:
                            st.session_state["_preserve_chat_on_module_switch"] = True
                            st.query_params["module"] = _target_module
                        st.session_state.pop("_asset_section_suggestion", None)
                        st.query_params["section"] = slug
                        st.query_params["section_nav"] = "1"
                        st.rerun()
                with col_b:
                    if is_external_asset:
                        if st.button("📎 Abrir recurso", width="stretch", key="asset_section_open_external_btn"):
                            st.session_state["_open_external_asset"] = {
                                "url": str(asset_path),
                                "desc": desc,
                                "suggestion_key": "_asset_section_suggestion",
                            }
                            st.session_state.pop("_active_tree_id", None)
                            st.session_state.pop("_active_tree_title", None)
                            _rerun_tools_panel()
                    elif st.button("📎 Abrir recurso", width="stretch", key="asset_section_asset_btn"):
                        st.session_state["_asset_open"] = {
                            "path": asset_path, "desc": desc,
                            "language": language, "selected_path": selected_path,
                        }
                        st.session_state.pop("_asset_section_suggestion", None)
                        _rerun_tools_panel()
                with col_c:
                    if st.button("💬 Explicame vos", width="stretch", key="asset_section_llm_btn"):
                        st.session_state["_pending_llm_direct"] = {
                            "prompt": asset_section_suggestion["prompt"],
                            "context": asset_section_suggestion.get("context") or context,
                            "module": asset_section_suggestion.get("module") or selected_stem,
                        }
                        st.session_state.pop("_asset_section_suggestion", None)
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

            combined_suggestion = st.session_state.get("_combined_suggestion")
            if combined_suggestion:
                tree_title       = combined_suggestion["tree_title"]
                section_heading  = combined_suggestion["section_heading"]
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("📖 Ver sección", width="stretch", key="combined_section_btn"):
                        slug = _slugify(section_heading)
                        st.query_params["section"] = slug
                        st.query_params["section_nav"] = "1"
                        st.rerun()
                with col_b:
                    if st.button("🌳 Ir al árbol", width="stretch", key="combined_tree_btn"):
                        st.session_state["_pending_open_tree"] = {
                            "title":          tree_title,
                            "suggestion_key": "_combined_suggestion",
                        }
                        _rerun_tools_panel()
                with col_c:
                    if st.button("💬 Explicame vos", width="stretch", key="combined_llm_btn"):
                        st.session_state["_pending_llm_direct"] = {"prompt": combined_suggestion["prompt"], "context": context}
                        st.session_state.pop("_combined_suggestion", None)
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

            # ── Sugerencia de árbol solo ──────────────────────────────────────
            if suggestion:
                tree_title = suggestion["title"]
                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("🌳 Ir al árbol", width="stretch", key="suggestion_tree"):
                        st.session_state["_pending_open_tree"] = {
                            "title":   suggestion["title"],
                            "suggestion_key": "_tree_suggestion",
                        }
                        _rerun_tools_panel()
                with col_b:
                    if st.button("💬 Explicame vos", width="stretch", key="suggestion_llm"):
                        st.session_state["_pending_llm_direct"] = {"prompt": suggestion["prompt"], "context": context}
                        st.session_state.pop("_tree_suggestion", None)
                        st.session_state.pop("_expand_chat_open", None)
                        _rerun_tools_panel()

    # ── Dialog: asset viewer ──────────────────────────────────────────────────
    if st.session_state.get("_asset_open"):
        asset_info = st.session_state["_asset_open"]
        @st.dialog(f"📎 {asset_info['desc']}", width="large")
        def _asset_dialog():
            from config import BASE_DIR
            asset_path = BASE_DIR / asset_info["path"]
            if asset_path.exists():
                code = asset_path.read_text(encoding="utf-8")
                st.code(code, language=asset_info.get("language", "abap"))
                with open(str(asset_path), "rb") as f:
                    st.download_button(
                        label="⬇ Descargar", data=f,
                        file_name=asset_path.name, mime="text/plain", width="stretch",
                    )
            else:
                st.warning(f"Archivo no encontrado: `{asset_info['path']}`")
            if st.button("Cerrar", width="stretch", key="close_asset_dialog"):
                st.session_state["_asset_consumed"] = asset_info.get("desc", "Recurso técnico")
                st.session_state.pop("_asset_open", None)
                _rerun_tools_panel()
        _asset_dialog()
        # Cerrado con la X del dialog
        if not st.session_state.get("_asset_open"):
            pass  # el evento lo agrega el bloque _asset_consumed abajo
        else:
            st.session_state.pop("_asset_open", None)

    # Evento de sistema cuando se cierra el asset
    _asset_consumed = st.session_state.pop("_asset_consumed", None)
    if _asset_consumed:
        AppState.append_chat("event", f"📎 Recurso técnico consultado: **{_asset_consumed}**")

    # ── Dialog: chat expandido ────────────────────────────────────────────────
    if st.session_state.get("_expand_chat_open"):
        @st.dialog("💬 Arquitecto Danwor", width="large")
        def _expanded_chat():
            st.markdown("<style>.dw-chat-msg { margin-bottom: 10px !important; }</style>", unsafe_allow_html=True)

            # Aviso si hay botones de sugerencia activos que no se ven en el dialog
            _SUGGESTION_KEYS = ("_section_suggestion", "_tree_suggestion", "_asset_suggestion", "_asset_section_suggestion", "_combined_suggestion", "_depth_selector")
            if any(st.session_state.get(k) for k in _SUGGESTION_KEYS):
                st.info("💡 Hay opciones pendientes de selección en el chat. Cerrá este panel para interactuar con ellas.", icon=None)

            chat_d = AppState.chat()
            with st.container(height=480):
                for role, msg in chat_d:
                    if role == "user":
                        st.markdown(
                            f"<div class='dw-chat-msg dw-chat-user'>"
                            f"<div class='dw-chat-label'>{_get_auth_user().get('first_name', 'Tú')}</div>"
                            f"{_to_html(msg)}</div>",
                            unsafe_allow_html=True,
                        )
                    elif role == "event":
                        st.markdown(
                            f"<div style='text-align:center;color:#aaa;font-size:0.8rem;"
                            f"margin:6px 0;padding:4px 0;border-top:1px solid rgba(0,0,0,0.06);"
                            f"border-bottom:1px solid rgba(0,0,0,0.06)'>{_to_html(msg)}</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f"<div class='dw-chat-msg dw-chat-assistant'>"
                            f"<div class='dw-chat-label'>Arquitecto Danwor</div>"
                            f"{_to_html(msg)}</div>",
                            unsafe_allow_html=True,
                        )
            user_input_d = st.text_input(
                "Tu pregunta:", value="", key="free_question_dialog",
                placeholder="Escribí tu consulta...",
            )
            st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
            if st.button("Enviar", width='stretch', key="send_dialog", disabled=_assistant_busy):
                if user_input_d.strip() and _ia_enabled(selected_stem):
                    _cases_count = len(_load_relevant_cases(module=selected_stem, max_cases=5))
                    _loading_msg = get_loading_message("Pregunta libre", _cases_count > 0)
                    _dialog_prompt = user_input_d.strip()
                    log_activity("prompt_consulted", module=selected_stem, prompt=_dialog_prompt, source="expanded_chat")
                    AppState.append_chat("user", _dialog_prompt)
                    st.session_state["_pending_llm_direct"] = {
                        "prompt": _dialog_prompt,
                        "context": context,
                        "loading_msg": _loading_msg,
                        "module": selected_stem,
                        "activity_event": "logged",
                    }
                    st.session_state["_processing"] = True
                    st.session_state.pop("_expand_chat_open", None)
                    _rerun_tools_panel()
                elif not _ia_enabled(selected_stem):
                    _ia_disabled_msg()
                else:
                    show_msg("Escribí una pregunta.", kind="warning")
            st.markdown(
                "<div style='font-size:0.75rem;color:#888;margin-top:6px;text-align:center'>"
                "⚠ La IA puede cometer errores. Revisá sus respuestas.</div>",
                unsafe_allow_html=True,
            )
        _expanded_chat()

    # ── Input libre ───────────────────────────────────────────────────────────
    if _assistant_busy:
        st.markdown("<span class='dw-assistant-busy-marker'></span>", unsafe_allow_html=True)
    st.caption("Consultá y recibí recomendaciones en lenguaje natural")

    _fq_key    = f"free_question_{st.session_state.get('_fq_counter', 0)}"
    user_input = st.text_area(
        "Tu pregunta (sobre este módulo):",
        value="",
        key=_fq_key,
        height=90,
        disabled=_assistant_busy,
    )
    if not _assistant_busy:
        st.components.v1.html(
            """
            <script>
            (function() {
              setTimeout(function() {
                var doc = window.parent.document;
                var fields = Array.prototype.slice.call(doc.querySelectorAll('textarea'));
                var target = fields.reverse().find(function(el) {
                  var label = (el.getAttribute('aria-label') || '').toLowerCase();
                  var visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                  return visible && label.indexOf('tu pregunta') >= 0;
                });
                if (target && doc.activeElement !== target) {
                  target.focus({ preventScroll: true });
                }
              }, 120);
            })();
            </script>
            """,
            height=0,
        )

    if st.button("Enviar", width='stretch', key="send_free", disabled=_assistant_busy):
        if not _ia_enabled(selected_stem):
            _ia_disabled_msg()
        elif not user_input.strip():
            show_msg("Escribí una pregunta.", kind="warning")
        else:
            _ctx    = context
            _prompt = user_input.strip()

            if _prompt.strip().lower() in ("test", "prueba"):
                AppState.append_chat("user", _prompt)
                AppState.append_chat("assistant", "recibido")
                st.session_state["_clear_fq"] = True
                _rerun_tools_panel()
            else:

                st.session_state["_pending_free_question"] = {"prompt": _prompt}
                st.session_state["_processing"] = True
                st.session_state["_clear_fq"] = True
                _rerun_tools_panel()

    st.markdown(
        "<div style='font-size:0.75rem;color:#888;margin-top:4px;margin-bottom:8px;text-align:center'>"
        "⚠ La IA puede cometer errores. Revisá sus respuestas.</div>",
        unsafe_allow_html=True,
    )

    if _defer_pending_action and _has_pending_action():
        st.session_state.pop("_processing", None)
        _flush_pending_action()
        return

    if chat or suggestion or _depth_sel_active or _cds_delta_sel_active or _pending_classify_active:
        col_clear, col_expand = st.columns(2)
        with col_clear:
            if st.button("🗑 Limpiar", width='stretch', key="clear_chat_btn"):
                AppState.clear_chat()
                st.session_state.pop("_tree_suggestion", None)
                st.session_state.pop("_combined_suggestion", None)
                st.session_state.pop("_section_suggestion", None)
                st.session_state.pop("_asset_suggestion", None)
                st.session_state.pop("_asset_section_suggestion", None)
                st.session_state.pop("_asset_open", None)
                st.session_state.pop("_depth_selector", None)
                st.session_state.pop("_cds_delta_selector", None)
                _llm_guard_reset()
                _rerun_tools_panel()
        with col_expand:
            if st.button("⛶ Expandir chat", width='stretch', key="expand_chat_btn"):
                st.session_state["_expand_chat_open"] = True
                _rerun_tools_panel()

    if _quick_action_blocked:
        _ia_disabled_msg()


# Tab bar ─────────────────────────────────────────────────────────────────────
_TAB_LABELS = {0: "📌 Sección", 1: "🌳 Árbol", 2: "💬 Asistente"}

_TAB_CSS = """
<style>
div[data-testid="stVerticalBlock"]:has(> div > .dw-section-title) {
  border-left: 1px solid rgba(0,0,0,0.10);
  padding-left: 16px !important;
}
.dw-tab-active-pill {
  height: 36px;
  min-height: 36px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 8px;
  border: 1px solid #dfe4ea;
  border-bottom: 3px solid #F5A800;
  background: #ffffff;
  color: #17202a;
  font-size: 14px;
  font-weight: 650;
  line-height: 1;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
  user-select: none;
}
</style>
"""


_RUNTIME_EXPORT_DIRS = (
    ("feedback_log", FEEDBACK_LOG_DIR),
    ("activity_log", ACTIVITY_LOG_DIR),
    ("case_library", CASE_LIBRARY_DIR),
)


def _format_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{num_bytes} B"


def _iter_runtime_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _runtime_export_inventory() -> tuple[list[dict], int, int, float | None]:
    rows: list[dict] = []
    total_files = 0
    total_size = 0
    latest_mtime: float | None = None

    for label, root in _RUNTIME_EXPORT_DIRS:
        files = _iter_runtime_files(root)
        size = sum(p.stat().st_size for p in files)
        mtimes = [p.stat().st_mtime for p in files]
        latest = max(mtimes) if mtimes else None
        rows.append({
            "carpeta": label,
            "path": str(root),
            "archivos": len(files),
            "tamano": _format_bytes(size),
            "ultimo_cambio": (
                _dt.datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S")
                if latest else "-"
            ),
        })
        total_files += len(files)
        total_size += size
        if latest is not None:
            latest_mtime = latest if latest_mtime is None else max(latest_mtime, latest)

    return rows, total_files, total_size, latest_mtime


def _build_runtime_export_zip() -> bytes:
    rows, total_files, total_size, latest_mtime = _runtime_export_inventory()
    generated_at = _dt.datetime.now().isoformat(timespec="seconds")
    manifest = {
        "generated_at": generated_at,
        "total_files": total_files,
        "total_size_bytes": total_size,
        "latest_mtime": latest_mtime,
        "folders": rows,
        "note": "Export manual de archivos runtime locales/efimeros antes de redeploy o reinicio.",
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for label, root in _RUNTIME_EXPORT_DIRS:
            for path in _iter_runtime_files(root):
                zf.write(path, arcname=str(Path(label) / path.relative_to(root)))
    return buffer.getvalue()


def _runtime_export_preview(rows: list[dict], total_files: int, total_size: int) -> str:
    lines = [
        "Export runtime Danwor",
        f"Archivos: {total_files}",
        f"Tamaño: {_format_bytes(total_size)}",
        "",
    ]
    for row in rows:
        lines.append(
            f"- {row['carpeta']}: {row['archivos']} archivos, "
            f"{row['tamano']}, último cambio {row['ultimo_cambio']}"
        )
    return "\n".join(lines)


def _render_runtime_export_section() -> None:
    rows, total_files, total_size, _latest = _runtime_export_inventory()

    st.markdown("**📦 Export runtime**")
    st.caption(
        "Descarga una copia manual de feedback, actividad y case library antes de un redeploy/reinicio. "
        "En DigitalOcean estos archivos viven en el filesystem efímero del contenedor."
    )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "carpeta": "Carpeta",
            "path": "Path",
            "archivos": "Archivos",
            "tamano": "Tamaño",
            "ultimo_cambio": "Último cambio",
        },
    )

    if total_files == 0:
        st.info("No hay archivos runtime para exportar todavía.")
        return

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes = _build_runtime_export_zip()
    st.download_button(
        "📥 Descargar ZIP runtime",
        data=zip_bytes,
        file_name=f"danwor-runtime-export-{timestamp}.zip",
        mime="application/zip",
        key="download_runtime_export_zip",
        width="stretch",
    )
    with st.expander("Resumen copiable", expanded=False):
        st.code(_runtime_export_preview(rows, total_files, total_size), language=None)


def _render_analytics_tab() -> None:
    import subprocess, sys as _sys, os as _os
    from config import BASE_DIR

    def _trigger(script: str, args: list[str], out_key: str, label: str) -> None:
        if AppState.is_simulation():
            safe_args = [a for a in args if a != "--llm"]
            import subprocess as _sp, sys as _sys, os as _os
            from config import BASE_DIR as _BASE_DIR
            script_path = _BASE_DIR / script
            env = _os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            with st.spinner(f"Ejecutando {label}..."):
                result = _sp.run(
                    [_sys.executable, str(script_path)] + safe_args,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", cwd=str(_BASE_DIR), env=env,
                )
            out = result.stdout.strip()
            err = result.stderr.strip()
            st.session_state[out_key] = out if out else (f"Error:\n{err}" if err else "(sin output)")
            _rerun_tools_panel()
        else:
            st.session_state[_PENDING_KEY] = {
                "_type": "script", "label": label,
                "script": script, "args": args, "out_key": out_key,
            }
            _rerun_tools_panel()

    _GROUP_SEP = "<div style='margin: 10px 0'></div>"
    st.markdown("<h4 style='margin-top:0'>🔬 Analítica interna</h4>", unsafe_allow_html=True)
    st.caption("Herramientas administrativas — solo visible en modo diagnóstico.")

    _render_runtime_export_section()

    st.markdown(_GROUP_SEP, unsafe_allow_html=True)
    st.markdown("**📚 Case Library**")
    with st.expander("¿Qué hace cada acción?", expanded=False):
        st.markdown("**Analizar casos**  \nEscanea `case_library/` y genera reportes estructurados.")
        if not AppState.is_simulation():
            st.warning("⚠ Esta acción consumirá créditos de tu API.")
    if st.button("Analizar casos", key="analytics_cases", width="stretch"):
        _trigger("analyze_cases.py", ["--llm"] if not AppState.is_simulation() else [], "_analytics_cases_out", "Analizar casos")
    if st.session_state.get("_analytics_cases_out"):
        with st.expander("Resultado — Case Library", expanded=True):
            st.code(st.session_state["_analytics_cases_out"], language=None)

    st.markdown(_GROUP_SEP, unsafe_allow_html=True)
    st.markdown("**💬 Feedback**")
    with st.expander("¿Qué hace cada acción?", expanded=False):
        st.markdown("**Todo el feedback**  \nAnaliza todas las entradas de `feedback_log/`.")
        st.markdown("**Solo faltantes**  \nFiltra solicitudes de árboles faltantes.")
        st.markdown("**Solo IA feedback**  \nFiltra los 👍/👎 de outputs de IA.")
    col3, col4, col5 = st.columns(3)
    if col3.button("Todo el feedback",  key="analytics_fb_all",     width="stretch"):
        _trigger("analyze_feedback.py", [],                        "_analytics_fb_out", "Analizar feedback")
    if col4.button("Solo faltantes",    key="analytics_fb_missing", width="stretch"):
        _trigger("analyze_feedback.py", ["--type", "missing"],     "_analytics_fb_out", "Feedback: faltantes")
    if col5.button("Solo IA feedback",  key="analytics_fb_ia",      width="stretch"):
        _trigger("analyze_feedback.py", ["--type", "ia"],          "_analytics_fb_out", "Feedback: IA")
    if st.session_state.get("_analytics_fb_out"):
        with st.expander("Resultado — Feedback", expanded=True):
            st.code(st.session_state["_analytics_fb_out"], language=None)

    st.markdown(_GROUP_SEP, unsafe_allow_html=True)
    st.markdown("**🌳 Propuestas de árbol**")
    with st.expander("¿Qué hace cada acción?", expanded=False):
        st.markdown("**Proponer actualizaciones**  \nGenera propuestas de ramas nuevas.")
        st.markdown("**Aplicar borradores**  \nCrea versiones borrador en `trees/proposed/`.")
    col6, col7 = st.columns(2)
    if col6.button("Proponer actualizaciones", key="analytics_propose",       width="stretch"):
        _trigger("propose_tree_updates.py", [],          "_analytics_propose_out", "Proponer actualizaciones")
    if col7.button("Aplicar borradores",       key="analytics_propose_apply", width="stretch"):
        _trigger("propose_tree_updates.py", ["--apply"], "_analytics_propose_out", "Aplicar borradores")
    if st.session_state.get("_analytics_propose_out"):
        with st.expander("Resultado — Propuestas", expanded=True):
            st.code(st.session_state["_analytics_propose_out"], language=None)

    st.markdown(_GROUP_SEP, unsafe_allow_html=True)
    st.markdown("**📝 Contenido**")
    with st.expander("¿Qué hace cada acción?", expanded=False):
        st.markdown("**Proponer mejoras**  \nCruza feedback y cases para identificar secciones a mejorar.")
        if not AppState.is_simulation():
            st.warning("⚠ Esta acción consumirá créditos de tu API.")
    if st.button("Proponer mejoras", key="analytics_content", width="stretch"):
        _trigger("propose_content_updates.py", ["--llm"] if not AppState.is_simulation() else [], "_analytics_content_out", "Proponer mejoras de contenido")
    if st.session_state.get("_analytics_content_out"):
        with st.expander("Resultado — Contenido", expanded=True):
            st.code(st.session_state["_analytics_content_out"], language=None)


def _render_tab_bar(active: int, tab_labels: dict | None = None) -> int:
    labels       = tab_labels if tab_labels is not None else _TAB_LABELS
    _mode_suffix = "dbg" if AppState.debug_mode() else "std"
    st.markdown(_TAB_CSS, unsafe_allow_html=True)
    cols = st.columns(len(labels))
    current_active = AppState.active_tools_tab()

    for idx, label in labels.items():
        with cols[idx]:
            if idx == current_active:
                st.markdown(f'<div class="dw-tab-active-pill">{label}</div>', unsafe_allow_html=True)
            else:
                st.button(
                    label,
                    key=f"tab_btn_{idx}_{_mode_suffix}",
                    width='stretch',
                    on_click=AppState.set_active_tools_tab,
                    args=(idx,),
                )

    return current_active


# Entry point ─────────────────────────────────────────────────────────────────
_streamlit_fragment = getattr(st, "fragment", lambda fn: fn)


def _rerun_tools_panel() -> None:
    """Rerenderiza solo el panel derecho cuando Streamlit lo permite.

    Mantiene el fallback completo para llamadas ejecutadas fuera del fragmento o
    en versiones de Streamlit sin rerun scoped.
    """
    try:
        st.rerun(scope="fragment")
    except Exception:
        st.rerun()


@_streamlit_fragment
def render_tools_panel(
    selected_stem: str,
    selected_path: str,
    context: str,
    trees_this_module: list,
    quick_action: str | None = None,
) -> None:
    # Avanzar el counter para recrear el text_area vacío en este render.
    # No tocamos el widget existente — Streamlit no lo permite post-instanciación.
    if st.session_state.pop("_clear_fq", False):
        st.session_state["_fq_counter"] = st.session_state.get("_fq_counter", 0) + 1

    _tab_labels = dict(_TAB_LABELS)
    if AppState.debug_mode():
        _tab_labels[3] = "🔬 Analítica"

    active_tab = AppState.active_tools_tab()
    if active_tab == 2 or st.session_state.get("_hide_section_action_zone"):
        _inject_inline_style(".st-key-section_action_zone{display:none!important;}")
    active_tab = _render_tab_bar(active_tab, _tab_labels)
    if active_tab == 2:
        st.markdown("<span class='dw-active-assistant-marker'></span>", unsafe_allow_html=True)

    _external_asset = st.session_state.pop("_open_external_asset", None)
    if _external_asset:
        _desc = _external_asset.get("desc", "recurso")
        _url = _external_asset.get("url", "")
        _sug_key = _external_asset.get("suggestion_key")
        AppState.append_chat("event", f"📖 Abriendo recurso: **{_desc}**")
        if _sug_key:
            st.session_state.pop(_sug_key, None)
        if _url:
            st.components.v1.html(
                f"<script>window.open({json.dumps(_url)}, '_blank', 'noopener,noreferrer');</script>",
                height=0,
            )
        active_tab = 2

    anchor_topic = AppState.pop_topic_from_anchor()
    if anchor_topic:
        _, body_md = split_front_matter(context)
        section_md = extract_section_by_heading(body_md, anchor_topic, level=(2, 3)) or ""
        AppState.set_selected_topic(anchor_topic, section_md)
        active_tab = 0

    if active_tab == 0:
        _render_section_tab(selected_stem, context, selected_path)
    elif active_tab == 1:
        _render_trees_tab(selected_stem, context, trees_this_module)
    elif active_tab == 2:
        _render_assistant_tab(selected_stem, context, quick_action, trees_this_module, selected_path)
    elif active_tab == 3 and AppState.debug_mode():
        _render_analytics_tab()

    if active_tab != 2:
        _flush_pending_action()
        _flush_pending_free_question(selected_stem, selected_path, context, trees_this_module)
        _flush_pending_classify(selected_stem)
        _flush_pending_llm_direct(selected_stem)

    # ── LLM directo — para chat libre y "Explicame vos" ─────────────────────
    # ── Abrir árbol desde sugerencia — procesado aquí donde session_state persiste
    _pending_tree = st.session_state.pop("_pending_open_tree", None)
    if _pending_tree:
        _tree_title = _pending_tree["title"]
        _sug_key    = _pending_tree.get("suggestion_key")
        # Reemplazar la última burbuja del asistente ("¿Cómo preferís continuar?")
        # por un evento de sistema que refleje lo que realmente pasó
        chat = AppState.chat()
        if chat and chat[-1][0] == "assistant":
            chat[-1] = ("event", f"🌳 El usuario navigó al árbol: **{_tree_title}**")
        else:
            AppState.append_chat("event", f"🌳 El usuario navegó al árbol: **{_tree_title}**")
        if _sug_key:
            st.session_state.pop(_sug_key, None)
        st.session_state["tree_selector"] = _tree_title
        AppState.open_tree(_tree_title)
        _rerun_tools_panel()
