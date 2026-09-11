"""
commercial_strategy.py — Estrategia Comercial HTTP (Commercial Strategy —
Phase 1, READ-ONLY, ver storage gate).

Segunda capability top-level de Comercial (`02 · Estrategia`, junto a
`01 · CRM` en `commercial.py`) — router DEDICADO en vez de agregar a
`commercial.py` (owner decision explícita: ese archivo ya crece por trabajo
en curso no relacionado). MISMO gate de sesión que `commercial.py`
(`commercial_access.is_commercial_authorized`) — Estrategia es una vista más
del MISMO workspace Comercial, nunca un segundo criterio de autorización.

Fase 1 es SOLO LECTURA: expone únicamente `GET /comercial/estrategia`. NO
existe (ni debe agregarse) ningún endpoint `POST`/`PUT`/`DELETE` de
Estrategia en esta fase — el storage gate encontró que ni la durabilidad
síncrona (`core/remote_store.py`) ni el contrato CSRF actual del Hub
alcanzan para aceptar escritura de usuario todavía (ver HANDOFF). Ese
trabajo es Phase 2, explícitamente no autorizado acá.

Fail-soft (MISMO contrato que `commercial.py`/`reporting.py`): un documento
ausente o corrupto (`CommercialStrategyUnavailable`) nunca produce un 500
crudo — la página renderiza en un estado "no disponible" explícito.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, workspaces
from webapp.services import auth_service, commercial_strategy_markup
from webapp.services.activity_context import AREA_STRATEGY, SCOPE_COMMERCIAL, EVENT_MODULE_VIEWED
from webapp.services.activity_service import log_activity
from webapp.services.commercial_strategy_repository import CommercialStrategyUnavailable, load_strategy
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
        from fastapi import HTTPException

        raise HTTPException(403, "No autorizado para Comercial")
    return None


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
    except CommercialStrategyUnavailable as exc:
        _logger.warning("commercial_strategy: documento no disponible: %s", exc)
        strategy = None
        error_state = "unavailable"

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
        },
    )
