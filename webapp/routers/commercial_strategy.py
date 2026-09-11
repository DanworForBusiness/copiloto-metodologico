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

ESCRITURA (`POST /comercial/estrategia`) — pre-PR correction, ver HANDOFF/
storage gate:
  1. Sesión válida (redirect si no).
  2. `commercial_access.can_edit_strategy_user(user)` — SOLO `CONTENT_OWNER`
     puede llegar más allá de este punto. 403 si no. Autoritativo: el botón
     "Editar" oculto en el template NUNCA es la defensa real (ver template).
  3. `same_origin_guard.is_same_origin_write(request)` — protección mínima
     anti-CSRF (ver ese módulo para el razonamiento completo: por qué esto y
     no un framework CSRF nuevo). 403 si el request no declara el MISMO
     origen que sirvió la página.
  4. Con (2) y (3) satisfechos: 503 explícito, `unavailable_reason=
     "concurrency_unconfirmed"`. NO es un placeholder temporal que "ya va a
     andar" — es la respuesta HONESTA mientras la topología de escritura en
     producción (¿una instancia o múltiples contenedores concurrentes?) no
     esté confirmada por el owner (ver storage gate, Gate B / pre-PR
     correction §10): un revision-check + lock en memoria de proceso SOLO es
     correcto bajo una única instancia — inventar esa garantía sin
     confirmarla sería fingir un contrato de escritura que en producción
     podría perder datos silenciosamente. Cuando esa confirmación llegue,
     esta rama es el ÚNICO lugar que cambia — el resto del enforcement (2)
     y (3) ya queda listo.

Fail-soft en lectura (MISMO contrato que `commercial.py`/`reporting.py`): un
documento ausente o corrupto (`CommercialStrategyUnavailable`) nunca produce
un 500 crudo — la página renderiza en un estado "no disponible" explícito.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, same_origin_guard, workspaces
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
            # `can_edit`: SOLO gobierna si el botón "Editar" se RENDERIZA —
            # nunca la defensa real (ver POST abajo, que repite el mismo
            # chequeo server-side de forma autoritativa).
            "can_edit": commercial_access.can_edit_strategy_user(user),
        },
    )


@router.post("/comercial/estrategia", include_in_schema=False)
def commercial_strategy_save(request: Request):
    """Endpoint de escritura — ver docstring del módulo para el contrato
    completo de las 4 capas de enforcement. Autoritativo: nunca confía en
    que el botón "Editar" estuviera oculto en el cliente."""
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    user = auth_service.session_user(request)
    if not commercial_access.can_edit_strategy_user(user):
        raise HTTPException(403, "No autorizado para editar Estrategia")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    # Autorización y origen verificados — pero la topología de escritura en
    # producción (¿instancia única o múltiples contenedores concurrentes?)
    # todavía no está confirmada por el owner (ver docstring del módulo).
    # 503, NUNCA 200: un revision-check + lock en memoria de proceso sin esa
    # confirmación podría perder ediciones de forma silenciosa bajo múltiples
    # instancias — reportar éxito acá sería fingir un contrato que no existe.
    return JSONResponse(
        {"state": "unavailable", "reason": "concurrency_unconfirmed",
         "message": "Guardado no disponible todavía: pendiente de confirmar la topología de "
                    "escritura en producción (ver storage gate). El documento canónico no fue modificado."},
        status_code=503,
    )
