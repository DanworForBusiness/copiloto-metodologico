from pathlib import Path
import uuid
import yaml
import streamlit as st
from datetime import datetime, timezone

from .tree_registry import build_registry_from_folder
from core.state import AppState


# =========================================================
# Registry / loader helpers
# =========================================================

@st.cache_data(show_spinner=False)
def load_tree_registry(trees_dir: Path):
    return build_registry_from_folder(trees_dir)


@st.cache_data(show_spinner=False)
def load_all_trees(trees_dir: Path):
    trees = []
    if not trees_dir.exists():
        return trees
    for p in sorted(trees_dir.glob("*.yaml")):
        try:
            t = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(t, dict) and t.get("id") and t.get("start") and t.get("nodes") and t.get("leaves"):
                t["_path"] = str(p)
                trees.append(t)
        except Exception:
            continue
    return trees


def module_tag_from_selected(selected_module: str) -> str:
    prefix = (selected_module or "").split("_", 1)[0]
    return f"module:{prefix}" if prefix.isdigit() else ""


def trees_for_module(all_trees: list[dict], module_tag: str) -> list[dict]:
    out = []
    for t in all_trees:
        tags = t.get("tags", [])
        if module_tag and module_tag in tags:
            out.append(t)
    return out


# =========================================================
# State helpers
# =========================================================

def tree_state_key(tree_id: str) -> str:
    return f"tree_state_{tree_id}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_state(start_node: str) -> dict:
    return {
        "current":        start_node,
        "route":          [],
        "trail":          [],
        "run_id":         str(uuid.uuid4()),
        "started_at":     _utc_now_iso(),
        "ended_at":       None,
        "uncharted_open": False,
    }


def tree_engine_init(tree: dict) -> str:
    key = tree_state_key(tree["id"])
    if key not in st.session_state:
        st.session_state[key] = _fresh_state(tree["start"])
    return key


def tree_engine_reset(tree: dict, key: str) -> None:
    st.session_state[key] = _fresh_state(tree["start"])


def tree_engine_replay(tree: dict, route: list[str]) -> dict:
    """
    Reconstruye el estado a partir de una ruta previa.
    BUG FIX: 'question' se asigna desde el nodo antes de usarse en el trail.
    """
    state   = _fresh_state(tree["start"])
    current = tree["start"]

    for step in route:
        if ": " not in step:
            break
        node_id, ans = step.split(": ", 1)
        node = tree["nodes"].get(node_id)
        if not node:
            break

        question = node.get("question", f"Pregunta {node_id}")  # FIX: antes de usarla
        nxt      = node.get("next", {}).get(ans)
        if not nxt:
            break

        state["route"].append(step)
        state["trail"].append({
            "ts":                "replay",
            "tree_id":           tree.get("id"),
            "node_id":           node_id,
            "question":          question,
            "answer_raw":        ans,
            "answer_normalized": ans,
            "next_id":           nxt,
            "rule_applied":      f"next['{ans}'] -> '{nxt}'",
        })
        current = nxt

    state["current"] = current
    return state


# =========================================================
# Case library — persistencia local
# =========================================================

def _case_library_dir() -> Path:
    """Directorio de casos. Fuente de verdad: config.CASE_LIBRARY_DIR."""
    from config import CASE_LIBRARY_DIR
    CASE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    return CASE_LIBRARY_DIR


def _build_case(
    tree:           dict,
    state:          dict,
    *,
    leaf_id:        str | None,
    leaf:           dict | None,
    outcome:        str,
    project_ctx:    str,
    lessons:        list[str],
    uncharted:      bool,
    gap_node:       str | None,
    decision_taken: str | None = None,
) -> dict:
    """Construye el dict canónico del caso."""

    # Alternativas descartadas: opciones NO elegidas en cada nodo del trail
    nodes_raw    = tree.get("nodes", {})
    trail        = state.get("trail", [])
    alternatives = []
    for event in trail:
        nid     = event.get("node_id", "")
        chosen  = event.get("answer_normalized", "")
        node    = nodes_raw.get(nid, {})
        options = node.get("options", [])
        for opt in options:
            if str(opt) != str(chosen):
                alternatives.append({"node": nid, "option": opt, "reason": None})

    # Lecciones y post_actions del leaf (auto-pobladas)
    leaf_notes   = []
    post_actions = None
    if leaf:
        leaf_notes = leaf.get("notes") or []
        pa = leaf.get("post_actions")
        if pa:
            post_actions = {
                "mode":       pa.get("mode"),
                "next_steps": pa.get("next_steps") or [],
                "reason":     pa.get("reason"),
                "questions":  pa.get("questions") or [],
            }

    date_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id   = state.get("run_id", "")
    suffix   = "uncharted" if uncharted else run_id[:8]

    return {
        "case_id": f"case_{date_tag}_{suffix}",
        "version": "1.0",
        "source": {
            "tree_id":    tree.get("id"),
            "tree_title": tree.get("title", ""),
            "module":     (tree.get("links") or {}).get("module_stem")
                          or (tree.get("links") or {}).get("module", ""),
            "run_id":     run_id,
            "timestamp":  state.get("started_at", _utc_now_iso()),
        },
        "route": [
            {
                "node":     e.get("node_id"),
                "question": e.get("question"),
                "answer":   e.get("answer_normalized"),
            }
            for e in trail
        ],
        "leaf_id":               leaf_id,
        "decision":              (leaf.get("decision") if leaf else None),
        "post_actions":          post_actions,
        "leaf_notes":            leaf_notes,
        "uncharted":             uncharted,
        "gap_node":              gap_node,
        "context_description":   project_ctx,
        "decision_taken":        decision_taken,
        "outcome":               outcome,
        "lessons":               [l for l in lessons if l],
        "alternatives_discarded": alternatives,
        "confidence":            None,
        "reviewed":              False,
    }


def _save_case(case: dict) -> Path:
    """Escribe el caso como YAML en case_library/ y devuelve el path."""
    dest = _case_library_dir() / f"{case['case_id']}.yaml"
    dest.write_text(
        yaml.dump(case, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return dest


def _leaf_mode(leaf: dict) -> str:
    """
    Devuelve el mode del leaf para determinar comportamiento del form post-leaf.
    resolved | llm_value_add | needs_human_review | none
    """
    pa = (leaf or {}).get("post_actions") or {}
    return pa.get("mode") or "none"


def _default_outcome_for_mode(mode: str) -> str:
    mapping = {
        "resolved":          "anduvo",
        "llm_value_add":     "en curso",
        "needs_human_review": "inconcluso",
        "none":              "anduvo",
    }
    return mapping.get(mode, "anduvo")


def _feedback_form_expanded(mode: str) -> bool:
    """Expandido automáticamente solo para modos que requieren input del consultor."""
    return mode in ("llm_value_add", "needs_human_review")


def _update_case(case_id: str, updates: dict) -> bool:
    """
    Actualiza un caso existente en case_library/ con los campos de feedback.
    Devuelve True si tuvo éxito.
    """
    dest = _case_library_dir() / f"{case_id}.yaml"
    if not dest.exists():
        return False
    try:
        case = yaml.safe_load(dest.read_text(encoding="utf-8")) or {}
        case.update(updates)
        case["reviewed"] = True
        case["feedback_at"] = _utc_now_iso()
        dest.write_text(
            yaml.dump(case, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def render_leaf_feedback_form(leaf: dict, case_id: str, key: str, leaf_id: str) -> None:
    """
    Form de feedback post-leaf. Se adapta al mode del leaf.
    Entrada via botones 👍/👎 — like abre form opcional, dislike abre form mandatorio.
    Actualiza el caso ya guardado en lugar de crear uno nuevo.
    """
    mode          = _leaf_mode(leaf)
    default_out   = _default_outcome_for_mode(mode)
    feedback_key  = f"{key}_leaf_feedback_saved_{leaf_id}"
    rating_key    = f"{key}_leaf_feedback_rating_{leaf_id}"
    expanded_key  = f"{key}_leaf_feedback_open_{leaf_id}"

    # Confirmación si ya se envió
    if st.session_state.get(feedback_key):
        st.success("✅ Feedback guardado.")
        st.caption(f"📄 `case_library/{case_id}.yaml`")
        if st.button("✕ Cerrar", key=f"{key}_leaf_fb_close_{leaf_id}", width='stretch'):
            st.session_state[feedback_key] = False
            st.session_state[expanded_key] = False
            st.session_state.pop(rating_key, None)
            st.rerun()
        return

    is_open = st.session_state.get(expanded_key, False)
    rating  = st.session_state.get(rating_key, None)

    # Botones 👍/👎 — solo visibles si el form no está abierto
    if not is_open:
        col1, col2, _ = st.columns([0.09, 0.09, 0.82], gap=None)
        with col1:
            if st.button("👍", key=f"{key}_leaf_fb_up_{leaf_id}", help="Fue útil — agregar feedback opcional"):
                st.session_state[rating_key]   = "up"
                st.session_state[expanded_key] = True
                st.rerun()
        with col2:
            if st.button("👎", key=f"{key}_leaf_fb_down_{leaf_id}", help="No fue útil — contanos qué falló"):
                st.session_state[rating_key]   = "down"
                st.session_state[expanded_key] = True
                st.rerun()
        return

    # Form abierto
    if st.button("▲ Cerrar", key=f"{key}_leaf_fb_toggle_{leaf_id}"):
        st.session_state[expanded_key] = False
        st.session_state.pop(rating_key, None)
        st.rerun()

    # Labels adaptados al mode
    outcome_hint = {
        "resolved":           "¿Cómo resultó la implementación?",
        "llm_value_add":      "¿Cómo resultó tras consultar al asistente?",
        "needs_human_review": "¿Qué se decidió tras la revisión?",
        "none":               "¿Cómo resultó?",
    }.get(mode, "¿Cómo resultó?")

    decision_hint = {
        "llm_value_add":      "¿Qué respondió el asistente? ¿Fue útil?",
        "needs_human_review": "¿Qué decidió el equipo técnico?",
    }.get(mode, "¿Qué decidiste hacer? (opcional)")

    # Pre-selección de outcome según rating
    outcome_options = ["anduvo", "no anduvo", "inconcluso", "en curso"]
    if rating == "up":
        default_idx = outcome_options.index("anduvo")
    elif rating == "down":
        default_idx = outcome_options.index("no anduvo")
    else:
        default_idx = outcome_options.index(default_out) if default_out in outcome_options else 0

    # Indicador visual del rating elegido
    rating_label = "👍 Fue útil" if rating == "up" else "👎 No fue útil" if rating == "down" else ""
    if rating_label:
        st.caption(rating_label)

    with st.form(key=f"{key}_leaf_fb_form_{leaf_id}"):
        outcome = st.selectbox(outcome_hint, outcome_options, index=default_idx)
        project_ctx = st.text_area(
            "Contexto del proyecto (opcional)",
            placeholder="Ej: ECC 6.07, cliente retail, migración a BDC 2026...",
            height=60,
        )
        if mode in ("llm_value_add", "needs_human_review"):
            decision_taken = st.text_area(
                decision_hint,
                placeholder="Describí qué pasó después...",
                height=70,
            )
        else:
            decision_taken = ""

        lesson_label = "Lección clave (requerida)" if rating == "down" else "Lección clave (opcional)"
        lesson_placeholder = "Ej: Verificar versión ABAP antes de proponer SLT"
        lesson = st.text_input(lesson_label, placeholder=lesson_placeholder)

        submitted = st.form_submit_button("💾 Guardar feedback", width='stretch')

    if submitted:
        if rating == "down" and not lesson.strip():
            st.warning("Contanos al menos una lección o qué falló antes de guardar.")
        else:
            updates = {
                "outcome":             outcome,
                "context_description": project_ctx.strip() or None,
                "decision_taken":      decision_taken.strip() or None,
                "lessons":             [lesson.strip()] if lesson.strip() else [],
                "rating":              rating,
            }
            ok = _update_case(case_id, updates)
            if ok:
                st.session_state[feedback_key] = True
                st.rerun()
            else:
                st.error(f"No se pudo actualizar el caso `{case_id}`. Verificá que exista en case_library/.")



# =========================================================
# Renderer
# =========================================================

def tree_engine_render(tree: dict, key: str) -> dict:
    state = st.session_state[key]

    if state["current"] is None:
        state["current"] = tree["start"]

    current = state["current"]

    # ── LEAF ──────────────────────────────────────────────────────────────────
    if current in tree.get("leaves", {}):
        leaf = tree["leaves"][current]
        state["ended_at"] = _utc_now_iso()

        export = {
            "export_version": "0.1",
            "tree": {
                "id":    tree.get("id"),
                "title": tree.get("title", ""),
                "links": tree.get("links", {}),
                "tags":  tree.get("tags", []),
                "_path": tree.get("_path", ""),
            },
            "run": {
                "run_id":     state.get("run_id"),
                "started_at": state.get("started_at"),
                "ended_at":   state.get("ended_at"),
                "route":      list(state.get("route", [])),
                "trail":      list(state.get("trail", [])),
            },
            "result": {
                "leaf_id": current,
                "leaf":    leaf,
            },
        }

        # Guardado automático — una sola vez por run_id
        # outcome inicial según el mode del leaf, no siempre "success"
        save_key = f"{key}_saved_{state.get('run_id', current)}"
        mode     = _leaf_mode(leaf)
        if not st.session_state.get(save_key):
            case = _build_case(
                tree,
                state,
                leaf_id=current,
                leaf=leaf,
                outcome=_default_outcome_for_mode(mode),
                project_ctx="",
                lessons=[],
                uncharted=False,
                gap_node=None,
            )
            _save_case(case)
            st.session_state[save_key] = case["case_id"]

        case_id = st.session_state.get(save_key, "")

        return {
            "done":    True,
            "leaf_id": current,
            "leaf":    leaf,
            "leaf_mode": mode,
            "case_id": case_id,         # expuesto para que el panel renderice el form
            "route":   state["route"],
            "trail":   state.get("trail", []),
            "export":  export,
        }

    # ── NODE ──────────────────────────────────────────────────────────────────
    node = tree["nodes"].get(current)
    if not node:
        return {
            "done":    True,
            "leaf_id": None,
            "leaf":    {"decision": "Nodo no encontrado en YAML."},
            "route":   state["route"],
            "trail":   state.get("trail", []),
        }

    question = node.get("question", f"Pregunta {current}")
    options  = node.get("options", [])

    if node.get("type", "single_choice") != "single_choice":
        st.warning("PoC: sólo soporta nodos single_choice.")
        return {"done": False, "route": state["route"], "trail": state.get("trail", [])}

    # ── Pregunta + toggle ─────────────────────────────────────────────────────
    st.markdown("")  # padding entre selector de árbol y primera pregunta
    st.markdown(f"**{question}**")

    uncharted_open = state.get("uncharted_open", False)

    # Caption y botón alineados verticalmente en la misma fila
    cap_col, link_col = st.columns([0.55, 0.45], vertical_alignment="center")
    with cap_col:
        st.caption("Seleccioná una opción")
    with link_col:
        toggle_label = "✕ Cerrar" if uncharted_open else "⚠ Mi opción no está acá"
        if st.button(toggle_label, key=f"{key}_unc_toggle_{current}", width='stretch'):
            state["uncharted_open"] = not uncharted_open
            st.rerun()

    # ── Form uncharted: reemplaza selectbox + botones cuando está abierto ─────
    if state.get("uncharted_open"):
        _render_uncharted_form(tree, state, key, current)
        return {"done": False, "route": state["route"], "trail": state.get("trail", [])}

    # ── Selectbox (solo visible cuando el form está cerrado) ──────────────────
    select_key = f"{key}_sel_{current}"
    if select_key not in st.session_state:
        st.session_state[select_key] = options[0] if options else ""
    answer = st.selectbox(
        "Seleccioná una opción", options,
        key=select_key, label_visibility="collapsed",
    )

    # ── Atrás / Siguiente ─────────────────────────────────────────────────────
    colA, colB = st.columns([0.5, 0.5])
    at_start = len(state["route"]) == 0
    back     = colA.button("↩ Atrás",     width='stretch', key=f"{key}_back_{current}", disabled=at_start)
    next_btn = colB.button("Siguiente ↪", width='stretch', key=f"{key}_next_{current}")

    # ── Lógica Siguiente ──────────────────────────────────────────────────────
    if next_btn:
        def _norm(s: str) -> str:
            return " ".join(str(s).strip().split())

        next_map = node.get("next", {}) or {}
        nxt      = next_map.get(answer)

        if not nxt:
            norm_map = {_norm(k): v for k, v in next_map.items()}
            nxt      = norm_map.get(_norm(answer))
            st.error("El árbol no define transición para esta respuesta. Revisar YAML.")
            st.caption(f"DEBUG answer={repr(answer)} next_keys={[repr(k) for k in next_map.keys()]}")
            return {"done": False, "route": state["route"], "trail": state.get("trail", [])}

        if nxt == current:
            st.error("El árbol vuelve al mismo nodo (loop). Revisar YAML.")
            return {"done": False, "route": state["route"], "trail": state.get("trail", [])}

        state.setdefault("trail", []).append({
            "ts":                _utc_now_iso(),
            "tree_id":           tree["id"],
            "node_id":           current,
            "question":          question,
            "answer_raw":        answer,
            "answer_normalized": answer,
            "next_id":           nxt,
            "rule_applied":      f"next[\'{answer}\'] -> \'{nxt}\'",
        })
        state["route"].append(f"{current}: {answer}")
        state["current"]        = nxt
        state["uncharted_open"] = False
        st.rerun()

    # ── Lógica Atrás ──────────────────────────────────────────────────────────
    if back and state["route"]:
        st.session_state[key] = tree_engine_replay(tree, state["route"][:-1])
        st.rerun()

    return {"done": False, "route": state["route"], "trail": state.get("trail", [])}


# =========================================================
# Uncharted form
# =========================================================

def _render_missing_tree_form(module_stem: str, form_key: str) -> None:
    """Form para capturar un árbol de decisión que falta en el módulo."""
    st.markdown("#### ⚠ Oops... nos faltó un árbol")
    st.caption("Tu consulta ayuda a priorizar nuevos árboles. Completá lo que puedas.")

    saved_key = f"{form_key}_saved"

    if st.session_state.get(saved_key):
        import time as _t
        _uid = f"mt_msg_{int(_t.time()*1000)}"
        st.markdown(f"""
<style>
@keyframes {_uid}_anim {{
  0%   {{ opacity: 1; max-height: 80px; margin-top: 6px; padding: 10px 14px; }}
  70%  {{ opacity: 1; max-height: 80px; margin-top: 6px; padding: 10px 14px; }}
  90%  {{ opacity: 0; max-height: 80px; margin-top: 6px; padding: 10px 14px; }}
  100% {{ opacity: 0; max-height: 0;   margin-top: 0;   padding: 0; overflow: hidden; }}
}}
.{_uid} {{
  background: #d4edda; border-left: 4px solid #28a745; color: #155724;
  border-radius: 4px; font-size: 0.9rem;
  animation: {_uid}_anim 3.5s ease-in-out forwards; overflow: hidden;
}}
</style><div class="{_uid}">Registrado. Lo tendremos en cuenta.</div>""", unsafe_allow_html=True)
        if AppState.is_simulation():
            case_id = st.session_state.get(saved_key + "_id", "")
            if case_id:
                st.caption(f"📄 `feedback_log/{case_id}.yaml`")
        if st.button("Registrar otro", key=f"{form_key}_reset", width='stretch'):
            st.session_state[saved_key]         = False
            st.session_state[saved_key + "_id"] = None
            st.rerun()
        return

    with st.form(key=f"{form_key}_form"):
        tree_needed = st.text_area(
            "¿Qué árbol de decisión necesitabas? *",
            placeholder="Ej: Árbol para decidir estrategia de replicación en tiempo real vs batch...",
            height=90,
        )
        context_desc = st.text_area(
            "Contexto breve (opcional)",
            placeholder="Ej: Cliente con SAP BW on HANA, querían mover datos a Datasphere...",
            height=70,
        )
        submitted = st.form_submit_button("💾 Guardar", width='stretch')

    if submitted:
        if not tree_needed.strip():
            st.warning("Describí al menos qué árbol necesitabas antes de guardar.")
            return

        from config import FEEDBACK_LOG_DIR
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        case_id = f"missing_tree_{module_stem.split('_')[0]}_{ts}"
        entry   = {
            "type":        "missing_tree",
            "module":      module_stem.split("_")[0],
            "tree_needed": tree_needed.strip(),
            "context":     context_desc.strip() or None,
            "ts":          ts,
        }
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_LOG_DIR / f"{case_id}.yaml", "w", encoding="utf-8") as f:
            yaml.dump(entry, f, allow_unicode=True, sort_keys=False)

        st.session_state[saved_key]         = True
        st.session_state[saved_key + "_id"] = case_id
        st.rerun()


def _render_uncharted_form(tree: dict, state: dict, key: str, gap_node: str) -> None:
    """Form inline para capturar un camino no contemplado."""
    st.markdown("#### ⚠ Camino no contemplado")
    st.caption("Tu contexto ayuda a mejorar el árbol. Completá lo que puedas.")

    saved_key = f"{key}_unc_saved_{gap_node}"

    if st.session_state.get(saved_key):
        case_id = st.session_state.get(saved_key + "_id", "")
        st.success("✅ Caso guardado correctamente.")
        if case_id:
            st.caption(f"📄 `case_library/{case_id}.yaml`")
        col_new, col_close = st.columns([0.5, 0.5])
        if col_new.button("Registrar otro caso", key=f"{key}_unc_reset_{gap_node}", width='stretch'):
            st.session_state[saved_key]         = False
            st.session_state[saved_key + "_id"] = None
            # uncharted_open queda True → muestra el form vacío
            st.rerun()
        if col_close.button("✕ Cerrar", key=f"{key}_unc_close_{gap_node}", width='stretch'):
            st.session_state[saved_key]         = False
            st.session_state[saved_key + "_id"] = None
            state["uncharted_open"]             = False
            st.rerun()
        return

    with st.form(key=f"{key}_unc_form_{gap_node}"):
        context_desc = st.text_area(
            "¿Qué caracteriza tu caso técnico? *",
            placeholder="Ej: ECC 4.7 con extractores legacy sin soporte ODP...",
            height=80,
        )
        decision_taken = st.text_area(
            "¿Qué decidiste hacer? (opcional)",
            placeholder="Ej: Usamos SLT como puente hacia Datasphere...",
            height=80,
        )
        outcome = st.selectbox(
            "¿Cómo resultó?",
            ["anduvo", "no anduvo", "inconcluso", "en curso"],
        )
        lesson = st.text_input(
            "Lección clave (opcional)",
            placeholder="Ej: Verificar versión ABAP antes de proponer SLT",
        )
        submitted = st.form_submit_button("💾 Guardar caso", width='stretch')

    if submitted:
        if not context_desc.strip():
            st.warning("Describí al menos el contexto técnico antes de guardar.")
            return

        case = _build_case(
            tree,
            state,
            leaf_id=None,
            leaf=None,
            outcome=outcome,
            project_ctx=context_desc.strip(),
            lessons=[lesson.strip()] if lesson.strip() else [],
            uncharted=True,
            gap_node=gap_node,
            decision_taken=decision_taken.strip() or None,
        )
        _save_case(case)
        st.session_state[saved_key]            = True
        st.session_state[saved_key + "_id"]    = case["case_id"]
        # No cerramos uncharted_open aquí — el próximo ciclo muestra la confirmación
        st.rerun()
