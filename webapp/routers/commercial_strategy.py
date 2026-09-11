"""
commercial_strategy.py — Estrategia Comercial HTTP (Commercial Strategy).

Segunda capability top-level de Comercial (`02 · Estrategia`, junto a
`01 · CRM` en `commercial.py`) — router DEDICADO en vez de agregar a
`commercial.py` (owner decision explícita: ese archivo ya crece por trabajo
en curso no relacionado). MISMO gate de sesión que `commercial.py`
(`commercial_access.is_commercial_authorized`) — Estrategia es una vista más
del MISMO workspace Comercial, nunca un segundo criterio de autorización.

LECTURA (`GET /comercial/estrategia`): cualquier usuario autorizado a
Comercial (`can_view_strategy_user`).

ESCRITURA (`POST /comercial/estrategia`) — habilitada tras confirmación de
topología del owner (DigitalOcean: 1 contenedor / 1 proceso uvicorn para
esta release — ver `commercial_strategy_repository.py` para el contrato
exacto que esa confirmación habilita, y qué cambiaría si el número de
instancias sube). Capas de enforcement, EN ORDEN:

  1. Sesión válida (redirect si no).
  2. `commercial_access.can_edit_strategy_user(user)` — SOLO `CONTENT_OWNER`
     llega más allá de este punto. 403 si no. Autoritativo: el botón
     "Editar" oculto en el template NUNCA es la defensa real.
  3. `same_origin_guard.is_same_origin_write(request)` — protección mínima
     anti-CSRF (ver ese módulo). 403 si el request no declara el MISMO
     origen que sirvió la página.
  4. JSON parseable, con `revision` entero — 400 si no.
  5. `commercial_strategy_editing.merge_editable_fields(body)` — valida
     TODO el payload antes de tocar cualquier cosa; rechazo atómico
     (422) — nunca una persistencia parcial.
  6. `commercial_strategy_repository.save_strategy(...)` — revision
     optimista (409 si desactualizada, SIN escribir nada), escritura
     atómica local + push SÍNCRONO a Spaces (503 + rollback local si el
     push falla — ver ese módulo). 200 SOLO si terminó durable.

Fail-soft en lectura (MISMO contrato que `commercial.py`/`reporting.py`): un
documento ausente o corrupto (`CommercialStrategyUnavailable`) nunca produce
un 500 crudo — la página renderiza en un estado "no disponible" explícito.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

import yaml
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from config import FEEDBACK_LOG_DIR
from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, same_origin_guard, workspaces
from webapp.services import auth_service, commercial_strategy_editing, commercial_strategy_markup
from webapp.services.activity_context import AREA_STRATEGY, SCOPE_COMMERCIAL, EVENT_MODULE_VIEWED
from webapp.services.activity_service import log_activity
from webapp.services.commercial_strategy_editing import ValidationError
from webapp.services.commercial_strategy_repository import (
    CommercialStrategyUnavailable,
    DurablePushFailedError,
    StaleRevisionError,
    load_strategy,
    save_strategy,
)
from webapp.services.module_registry import CM02_MODULE_ID
from webapp.templating import templates

# Identidades TÉCNICAS estables de sección (ver el reporte del owner: "usar
# identificadores técnicos estables ya presentes en el modelo de Estrategia,
# nunca el título mutable") — son LITERAL las claves de nivel superior del
# documento (`webapp.services.commercial_strategy_data.strategy.yaml`), la
# MISMA fuente que ya usa el template para renderizar cada sección — nunca
# una segunda lista inventada aparte. `hero`/`revision`/`meta`/`nav`/
# `version` quedan fuera a propósito (no son "secciones" con feedback propio
# — la portada no tiene botonera, MISMO criterio que Copilot no le pone
# feedback al `#__module__`/hero de un módulo).
_FEEDBACK_SECTIONS = (
    "objectives", "demand_generation", "segments", "focus", "capacity",
    "cadence", "exclusions", "next_steps", "annex_map", "annex_roles",
    "annex_process", "annex_rules",
)

router = APIRouter()
_logger = logging.getLogger("danwor.commercial_strategy")

# Filtro Jinja de la vertical (MISMO patrón que main.py registrando
# `catalog_verdict_*` sobre `templates.env` — este router no importa
# `webapp.main`, así que registra el suyo directamente, igual que
# `templating.py` anticipa para el "segundo workspace", ver su docstring).
templates.env.filters["strategy_markup"] = commercial_strategy_markup.render
templates.env.filters["strategy_status_class"] = commercial_strategy_markup.status_badge_class
templates.env.filters["strategy_nav_title"] = commercial_strategy_markup.nav_title


def _require_commercial_session(request: Request, *, next_path: str | None = None):
    """MISMO patrón que `commercial._require_commercial_session` —
    deliberadamente duplicado acá (no importado desde `commercial.py`):
    este Hub ya trata este helper de 3 líneas como duplicación aceptada por
    router (ver `staffing._require_operations_session`/`commercial.
    _require_commercial_session`), nunca extraído a un módulo compartido."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not commercial_access.is_commercial_authorized(request):
        raise HTTPException(403, "No autorizado para Comercial")
    return None


def _editable_doc(strategy: dict) -> dict:
    """Subconjunto EDITABLE del documento canónico — lo único que el cliente
    recibe como estado mutable de EDIT (ver commercial_strategy_editing.
    EDITABLE_TOP_KEYS: el resto del documento es código+seed, nunca se
    manda como editable al cliente aunque sea de solo lectura).

    `annex_map.sap_tree`/`lob_tree` viajan TAL CUAL (ya tienen exactamente
    la forma `{root, branches}` que `commercial_strategy_editing.
    _validate_tree` espera) — el mapa de contactos (release blocker del
    gate "PIVOT EDIT UX") es el único anexo editable; `eyebrow`/`title`/
    `intro`/`lob_intro` de esa sección quedan código+seed, igual que el
    resto de anexos."""
    objectives = strategy["objectives"]
    annex_map = strategy["annex_map"]
    return {
        "revision": strategy["revision"],
        "hero": strategy["hero"],
        "objectives": {
            "result_targets": objectives["result_targets"],
            "demand_targets": objectives["demand_targets"],
            "calc_note": objectives["calc_note"],
            "coverage_note": objectives["coverage_note"],
            "sensitivity_note": objectives["sensitivity_note"],
            "assumptions_pending": objectives["assumptions_pending"],
        },
        "next_steps": {"tasks": strategy["next_steps"]["tasks"]},
        "annex_map": {
            "sap_tree": annex_map["sap_tree"],
            "lob_tree": annex_map["lob_tree"],
            "notes": annex_map["notes"],
        },
    }


@router.get("/comercial/estrategia", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_page(request: Request):
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_STRATEGY, module=CM02_MODULE_ID)

    try:
        strategy = load_strategy()
        error_state = ""
        editable_doc = _editable_doc(strategy)
    except CommercialStrategyUnavailable as exc:
        _logger.warning("commercial_strategy: documento no disponible: %s", exc)
        strategy = None
        error_state = "unavailable"
        editable_doc = None

    return templates.TemplateResponse(
        request=request, name="commercial/strategy.html",
        context={
            "current_workspace": workspaces.COMMERCIAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "estrategia",
            "strategy": strategy,
            "error_state": error_state,
            # `can_edit`: SOLO gobierna si el botón "Editar" se RENDERIZA —
            # nunca la defensa real (ver POST abajo, que repite el mismo
            # chequeo server-side de forma autoritativa).
            "can_edit": commercial_access.can_edit_strategy_user(user),
            "editable_doc": editable_doc,
        },
    )


@router.post("/comercial/estrategia", include_in_schema=False)
async def commercial_strategy_save(request: Request):
    """Endpoint de escritura — ver docstring del módulo para las 6 capas de
    enforcement en orden. Autoritativo: nunca confía en que el botón
    "Editar" estuviera oculto en el cliente."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not commercial_access.can_edit_strategy_user(user):
        raise HTTPException(403, "No autorizado para editar Estrategia")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")

    raw_body = await request.body()
    try:
        body = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(body, dict) or not isinstance(body.get("revision"), int):
        raise HTTPException(400, "Falta 'revision' (entero) en el payload")
    expected_revision = body["revision"]

    try:
        updated_fields = commercial_strategy_editing.merge_editable_fields(body)
    except ValidationError as exc:
        raise HTTPException(422, str(exc))

    updated_by_id = str(user.get("sub") or "")
    updated_by_label = str(user.get("username") or user.get("email") or user.get("sub") or "")

    try:
        saved = save_strategy(
            updated_fields, expected_revision=expected_revision,
            updated_by_id=updated_by_id, updated_by_label=updated_by_label,
        )
    except StaleRevisionError as exc:
        return JSONResponse(
            {"state": "stale", "current_revision": exc.current_revision,
             "message": "La Estrategia cambió desde que la cargaste — recargá la página antes de "
                        "volver a guardar. Nada se sobrescribió."},
            status_code=409,
        )
    except DurablePushFailedError:
        _logger.error("commercial_strategy: push durable falló, save abortado (revision %s)", expected_revision)
        return JSONResponse(
            {"state": "unavailable", "reason": "durable_push_failed",
             "message": "No se pudo guardar de forma durable — el documento anterior no fue modificado. "
                        "Reintentá en unos minutos; tus cambios en pantalla no se perdieron."},
            status_code=503,
        )
    except CommercialStrategyUnavailable as exc:
        _logger.warning("commercial_strategy: save sin documento base: %s", exc)
        raise HTTPException(503, "Estrategia no disponible")

    log_activity(
        "strategy.saved", user=user, scope=SCOPE_COMMERCIAL, area=AREA_STRATEGY,
        module=CM02_MODULE_ID, revision=saved["revision"],
    )
    return JSONResponse({"state": "ok", "document": _editable_doc(saved)})


# ── Feedback por sección — REUSA el primitivo transversal de Copilot ───────
# (`partials/section_feedback.html`, `.section-feedback*` en tools.css,
# `feedback_store`/`type="section_content"`/evento `"section.feedback"` del
# pipeline de Activity — ver docstrings de webapp/routers/feedback.py y
# webapp/services/feedback_store.py) — CERO componente/endpoint/storage/
# esquema de evento nuevo. Lo único que cambia respecto de Copilot:
#   - el namespace de ruta (`/comercial/estrategia/sections/<id>` en vez de
#     `/modules/<stem>/sections/<slug>`, porque Estrategia no tiene `stem`)
#   - `scope=SCOPE_COMMERCIAL`/`area=AREA_STRATEGY` en vez de
#     SCOPE_COPILOT/AREA_METHODOLOGY (scope/area SIEMPRE reflejan el
#     workspace real — nunca se le pone la etiqueta de otro)
#   - la autorización: `can_view_strategy_user` (VER Estrategia), NUNCA
#     `can_edit_strategy_user` — dejar feedback de sección es una acción de
#     LECTURA/reacción del Hub, no una edición de contenido de negocio;
#     acoplarla a CONTENT_OWNER sería inventar un requisito que ni Copilot
#     ni Comercial exigen para reaccionar a contenido que ya podés ver.


def _require_can_view_strategy(request: Request):
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not commercial_access.can_view_strategy_user(user):
        raise HTTPException(403, "No autorizado para Comercial")
    return user


def _section_or_404(section_id: str) -> dict:
    if section_id not in _FEEDBACK_SECTIONS:
        raise HTTPException(404, "Sección no encontrada")
    try:
        strategy = load_strategy()
    except CommercialStrategyUnavailable as exc:
        raise HTTPException(503, "Estrategia no disponible") from exc
    return strategy[section_id]


def _strategy_section_feedback_ctx(section_id: str, section: dict, **extra) -> dict:
    return {
        "feedback_id": section_id,
        "feedback_url_base": f"/comercial/estrategia/sections/{section_id}",
        "pack": DEFAULT_PACK,
        **extra,
    }


def _save_strategy_section_feedback(request, user, section_id: str, section_title: str, rating: str, comment: str = "") -> None:
    """MISMA implementación que `feedback._save_section_feedback` —
    `type="section_content"` (encaja tal cual: `module`/`section_slug`/
    `section_title` es exactamente la forma que ese tipo ya espera, ver su
    docstring — nunca una taxonomía nueva), mismo store, mismo formato de
    archivo. Deliberadamente duplicada acá en vez de importada de
    `feedback.py` — MISMO criterio que ese módulo documenta para sus propias
    4 implementaciones ("duplicadas a propósito, no una plataforma de
    feedback genérica")."""
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "section_content",
            "module": CM02_MODULE_ID,
            "module_title": "Estrategia Comercial",
            "section_slug": section_id,
            "section_title": section_title,
            "pack": DEFAULT_PACK,
            "rating": rating,
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
            "ts": now.isoformat(),
        }
        clean_comment = (comment or "").strip()
        if clean_comment:
            payload["comment"] = clean_comment
        path = FEEDBACK_LOG_DIR / f"section_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass
    try:
        log_activity(
            "section.feedback", user=user, scope=SCOPE_COMMERCIAL, area=AREA_STRATEGY,
            module=CM02_MODULE_ID, section=section_id, section_title=section_title,
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001
        pass


@router.post("/comercial/estrategia/sections/{section_id}/feedback", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_section_feedback(request: Request, section_id: str, rating: str = Form("")):
    user = _require_can_view_strategy(request)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    section = _section_or_404(section_id)
    if rating == "down":
        return templates.TemplateResponse(
            request=request, name="partials/section_feedback.html",
            context=_strategy_section_feedback_ctx(section_id, section, form=True),
        )
    _save_strategy_section_feedback(request, user, section_id, section["title"], "up")
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_strategy_section_feedback_ctx(section_id, section, submitted=True, rating="up"),
    )


@router.post("/comercial/estrategia/sections/{section_id}/feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_section_feedback_submit(request: Request, section_id: str, comment: str = Form("")):
    user = _require_can_view_strategy(request)
    section = _section_or_404(section_id)
    _save_strategy_section_feedback(request, user, section_id, section["title"], "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_strategy_section_feedback_ctx(section_id, section, submitted=True, rating="down"),
    )


@router.post("/comercial/estrategia/sections/{section_id}/feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_section_feedback_cancel(request: Request, section_id: str):
    _require_can_view_strategy(request)
    section = _section_or_404(section_id)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_strategy_section_feedback_ctx(section_id, section),
    )
