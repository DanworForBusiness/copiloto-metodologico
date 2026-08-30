"""
wizard.py — Trees & Wizard HTTP (feature COPILOT).

Router de la capacidad completa de árboles de decisión: iniciar un árbol o
el diagnóstico transversal (`POST /modules/{stem}/trees/start`, `POST
/modules/{stem}/diagnostics/orchestrator`), navegación del wizard (`POST
/wizards/{run_id}/answer|back|restart`), feedback sobre un recorrido (`POST
/wizards/{run_id}/feedback(+submit,+cancel)`) y la sugerencia de nuevo árbol
(`POST /trees/suggest`). Extraído de ``webapp/main.py`` (Fase 2J-C) como
relocación pura — sin rediseño de WizardStore, de formatos de feedback ni de
persistencia de sugerencias.

`/trees/suggest` no es stateful (no toca `store`), pero se incluye acá
porque su dueño de dominio es Trees/Wizard — Fase 2H (Feedback Family) ya la
excluyó explícitamente por este motivo. Moverla ahora le da a toda la
capacidad HTTP de Trees/Wizard un dueño único y elimina de `main.py` la
última responsabilidad de `FEEDBACK_LOG_DIR` ligada a Trees.

Feature COPILOT que consume capacidades compartidas desde sus dueños
explícitos (`core.content_registry`, `webapp.access`,
`webapp.services.auth_service`, `webapp.services.content_service`,
`webapp.services.activity_service`, `webapp.templating`, `config`) y el
estado de proceso del Copiloto desde `webapp.copilot_runtime.store` — SIN
importar ``webapp.main``.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

import yaml
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from config import FEEDBACK_LOG_DIR
from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp import access
from webapp.copilot_runtime import store
from webapp.services import auth_service
from webapp.services.activity_service import log_activity, log_verbose
from webapp.services.activity_context import SCOPE_COPILOT, AREA_METHODOLOGY
from webapp.services.content_service import load_module
from webapp.templating import templates

router = APIRouter()


# ── "¿Falta un árbol?" — sugerencia de nuevo árbol de decisión (recuperada) ──
def _save_tree_suggestion(request: Request, module: str, title: str, detail: str, pack: str, kind: str = "tree") -> None:
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "tree_suggestion", "kind": kind, "module": module, "title": title, "pack": pack,
            "user": str(user.get("username") or user.get("email") or ""), "ts": now.isoformat(),
        }
        d = (detail or "").strip()
        if d:
            payload["detail"] = d
        path = FEEDBACK_LOG_DIR / f"tree_suggestion_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        pass


@router.post("/trees/suggest", response_class=HTMLResponse, include_in_schema=False)
def trees_suggest(request: Request, title: str = Form(""), detail: str = Form(""),
                  module: str = Form(""), pack: str = Form(DEFAULT_PACK),
                  kind: str = Form("tree"), sg_id: str = Form("tree-suggest")):
    selected = resolve_pack(pack)
    clean = (title or "").strip()
    if kind not in {"tree", "option", "section"}:
        kind = "tree"
    if clean:
        _save_tree_suggestion(request, module, clean, detail, selected, kind)
    return templates.TemplateResponse(
        request=request, name="partials/tree_suggest.html",
        context={"suggested": bool(clean), "pack": selected, "stem": module,
                 "sg_kind": kind, "sg_id": sg_id})


def _norm_tokens(text: str) -> list[str]:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9]+", ascii_text) if t]


def _resolve_tree(trees: tuple, tree_id: str, tree_title: str, pack_name: str) -> dict | None:
    """Resuelve un árbol por id exacto; si no, por título (exacto, luego por
    subconjunto de tokens — p. ej. el botón 'Estrategia de Integración (federación
    vs persistencia)' matchea el árbol 'Federación vs Persistencia')."""
    if tree_id:
        tree = next((t for t in trees if str(t.get("id")) == tree_id), None)
        if tree:
            return tree
        tree = store.tree_by_id(tree_id, pack_name)
        if tree:
            return tree
    title = (tree_title or "").strip()
    if not title:
        return None
    low = title.lower()
    for t in trees:
        if str(t.get("title") or "").strip().lower() == low:
            return t
    button_tokens = set(_norm_tokens(title))
    best, best_len = None, 0
    for t in trees:
        tree_tokens = _norm_tokens(str(t.get("title") or ""))
        if tree_tokens and len(tree_tokens) > best_len and all(tok in button_tokens for tok in tree_tokens):
            best, best_len = t, len(tree_tokens)
    return best


@router.post("/modules/{stem}/trees/start", response_class=HTMLResponse)
def start_tree(
    request: Request,
    stem: str,
    tree_id: str = Form(""),
    tree_title: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
    panel: str = Form("tree"),
):
    selected = resolve_pack(pack)
    trees = store.trees_for_module(stem, selected, include_assessment=True)
    tree = _resolve_tree(trees, tree_id, tree_title, selected)
    if tree is None:
        raise HTTPException(404, "Árbol no encontrado")
    run = store.start(tree, selected)
    log_verbose(
        "tree_started", user=(auth_service.session_user(request) or {}),
        scope=SCOPE_COPILOT, area=AREA_METHODOLOGY,
        stem=stem, tree_id=str(tree.get("id") or ""), tree_title=str(tree.get("title") or tree_title or ""),
        panel=panel,
    )
    partial_name = "partials/diagnostic_tab.html" if panel == "diagnostic" else "partials/tree_tab.html"
    return templates.TemplateResponse(
        request=request,
        name=partial_name,
        context={
            "trees": trees,
            "run": run,
            "node": run.node,
            "leaf": run.leaf,
            "selected_tree_id": tree.get("id"),
            "document": load_module(stem, selected),
            "pack": selected,
        },
    )


@router.post("/modules/{stem}/diagnostics/orchestrator", response_class=HTMLResponse)
def start_diagnostic_orchestrator(
    request: Request,
    stem: str,
    pack: str = Form(DEFAULT_PACK),
):
    selected = resolve_pack(pack)
    trees = store.trees_for_module(stem, selected, include_routing_only=True, include_assessment=True)
    tree = next((item for item in trees if str(item.get("id")) == "framework_assessment"), None)
    if tree is None:
        raise HTTPException(404, "Diagnóstico transversal no encontrado")
    run = store.start(tree, selected)
    log_verbose(
        "tree_started", user=(auth_service.session_user(request) or {}),
        scope=SCOPE_COPILOT, area=AREA_METHODOLOGY,
        stem=stem, tree_id="framework_assessment", tree_title=str(tree.get("title") or ""),
        panel="diagnostic-orchestrator",
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/wizard.html",
        context={"run": run, "node": run.node, "leaf": run.leaf, "pack": selected},
    )


def _follow_wizard_redirect(run):
    leaf = run.leaf or {}
    post_actions = leaf.get("post_actions") or {}
    target_tree_id = str(leaf.get("redirect_tree") or "").strip()
    if post_actions.get("mode") == "assessment_orchestration":
        sequence = [str(item).strip() for item in (leaf.get("next_tree_sequence") or []) if str(item).strip()]
        target_tree_id = sequence[0] if sequence else ""
        target_tree = store.tree_by_id(target_tree_id, run.pack_name) if target_tree_id else None
        if target_tree is None:
            return run
        return store.start(
            target_tree,
            run.pack_name,
            orchestration_sequence=sequence,
            orchestration_index=0,
            orchestration_scores=[],
        )
    if target_tree_id:
        target_tree = store.tree_by_id(target_tree_id, run.pack_name)
        if target_tree is None:
            return run
        return store.start(target_tree, run.pack_name)
    if run.is_orchestrated and run.leaf is not None and run.orchestration_index < len(run.orchestration_sequence) - 1:
        scores = list(run.orchestration_scores)
        current_score = run.assessment_score
        if current_score:
            item = dict(current_score)
            item["tree_id"] = str(run.tree.get("id") or "")
            item["title"] = run.display_title
            scores.append(item)
        next_index = run.orchestration_index + 1
        next_tree_id = run.orchestration_sequence[next_index]
        next_tree = store.tree_by_id(next_tree_id, run.pack_name)
        if next_tree is None:
            return run
        return store.start(
            next_tree,
            run.pack_name,
            orchestration_sequence=run.orchestration_sequence,
            orchestration_index=next_index,
            orchestration_scores=scores,
        )
    return run


def _fragment(request, run):
    return templates.TemplateResponse(
        request=request,
        name="partials/wizard.html",
        context={"run": run, "node": run.node, "leaf": run.leaf, "pack": run.pack_name},
    )


@router.post("/wizards/{run_id}/answer", response_class=HTMLResponse)
def answer(request: Request, run_id: str, answer: str = Form("")):
    try:
        run = store.answer(run_id, answer)
        leaf = run.leaf or {}
        log_verbose(
            "tree_outcome" if leaf else "tree_step",
            user=(auth_service.session_user(request) or {}),
            scope=SCOPE_COPILOT, area=AREA_METHODOLOGY,
            run_id=run_id, answer=answer,
            tree_id=str(run.tree.get("id") or "") if getattr(run, "tree", None) else "",
            reached_leaf=bool(leaf),
            # El resultado del árbol vive en `leaf.decision` (el <h3> "Decisión
            # recomendada" en wizard.html), NO en id/title — las hojas no tienen
            # esos campos, por eso el dashboard mostraba "Resultado: —". Fallback
            # a title/id por si alguna hoja vieja los usara. (Los árboles de
            # assessment no tienen `decision`: su resultado es el `score`.)
            leaf=str(leaf.get("decision") or leaf.get("title") or leaf.get("id") or "") if leaf else "",
            score=(getattr(run, "assessment_score", None) if leaf else None),
        )
        return _fragment(request, _follow_wizard_redirect(run))
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc


@router.post("/wizards/{run_id}/back", response_class=HTMLResponse)
def back(request: Request, run_id: str):
    try:
        return _fragment(request, store.back(run_id))
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc


@router.post("/wizards/{run_id}/restart", response_class=HTMLResponse)
def restart(request: Request, run_id: str):
    try:
        return _fragment(request, store.restart(run_id))
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc


def _thanks(request, run):
    return templates.TemplateResponse(
        request=request,
        name="partials/tree_feedback.html",
        context={"run": run, "diagnostic_mode": access.is_diagnostic(request)},
    )


def _feedback_form(request, run, rating, error=""):
    return templates.TemplateResponse(
        request=request, name="partials/tree_feedback_form.html",
        context={"run": run, "rating": rating, "error": error},
    )


def _log_tree_feedback_reaction(request: Request, run, rating: str) -> None:
    """USAGE METRICS — REACCIONES (owner: cobertura completa de Copiloto, ver
    AUDIT): MISMO criterio que module.feedback/section.feedback — evento del
    pipeline de Activity, `log_activity` (NUNCA `log_verbose`: MISMO criterio
    que `_log_assistant_feedback_reaction` en assistant.py — una reacción es
    señal de negocio, no telemetría fina, a diferencia de tree_started/
    tree_step/tree_outcome de arriba, que SÍ son verbose). MISMA área/
    identidad de árbol que esos tres eventos (`answer()`, arriba: `run_id`/
    `tree_id`, área methodology) — nunca una taxonomía nueva. NUNCA toca
    CaseLibrary/WizardStore (eso sigue en `store.submit_feedback`, sin
    cambios) — esto es SOLO la señal adicional hacia Activity Log. Best-
    effort: nunca puede hacer que el feedback ya guardado se pierda."""
    try:
        log_activity(
            "tree.feedback", user=(auth_service.session_user(request) or {}),
            scope=SCOPE_COPILOT, area=AREA_METHODOLOGY,
            run_id=run.id, tree_id=str(run.tree.get("id") or "") if getattr(run, "tree", None) else "",
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


@router.post("/wizards/{run_id}/feedback", response_class=HTMLResponse)
def tree_feedback(request: Request, run_id: str, rating: str = Form("")):
    try:
        run = store.get(run_id)
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    if rating == "down":
        return _feedback_form(request, run, "down")          # 👎 abre el formulario
    try:
        run = store.submit_feedback(run_id, rating="up")      # 👍 finaliza directo
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _log_tree_feedback_reaction(request, run, "up")
    return _thanks(request, run)


@router.post("/wizards/{run_id}/feedback/submit", response_class=HTMLResponse)
def tree_feedback_submit(
    request: Request, run_id: str,
    rating: str = Form("down"), outcome: str = Form(""), context: str = Form(""),
    detail: str = Form(""), lesson: str = Form(""),
):
    try:
        run = store.submit_feedback(
            run_id, rating=rating, outcome=outcome, context=context, detail=detail, lesson=lesson,
        )
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    except ValueError as exc:                                  # falta lección → re-mostrar form con error
        return _feedback_form(request, store.get(run_id), rating, str(exc))
    _log_tree_feedback_reaction(request, run, rating)
    return _thanks(request, run)


@router.post("/wizards/{run_id}/feedback/cancel", response_class=HTMLResponse)
def tree_feedback_cancel(request: Request, run_id: str):
    try:
        run = store.get(run_id)
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    return _thanks(request, run)
