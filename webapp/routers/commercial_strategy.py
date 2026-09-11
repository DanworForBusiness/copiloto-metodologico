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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
    EDITABLE_TOP_KEYS: el resto del documento es código+seed en esta fase,
    nunca se manda como editable al cliente aunque sea de solo lectura)."""
    objectives = strategy["objectives"]
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
