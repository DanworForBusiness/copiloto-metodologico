"""
human_capital.py — Capital Humano HTTP (Capital Humano Phase 1 — Hub
Walking Skeleton).

Objetivo de esta fase: probar el VERTICAL BOUNDARY antes de portar
comportamiento funcional. `GET /human-capital` — shell autenticado, sin
dato de negocio, sin Supabase, sin lógica legacy, sin roles internos
(hrbp/hiring_manager, NO implementados) — la MISMA arquitectura Hub que ya
demostraron Operations y Commercial (auth/sesión, topbar, switcher,
sidebar, theme, CSS/JS compartido, loader canónico, permissions pattern),
aplicada a un CUARTO workspace.

Consume `webapp.human_capital_access.is_human_capital_authorized`
(allowlist explícita `HUMAN_CAPITAL_ALLOWED_USERS` del workspace Capital
Humano, INDEPENDIENTE de `OPERATIONS_ALLOWED_USERS`/
`COMMERCIAL_ALLOWED_USERS`) — MISMO patrón que
`webapp/routers/commercial.py` con `webapp.commercial_access` y
`webapp/routers/staffing.py` con `webapp.operations_access`.

El deep-link no autenticado entra por
`webapp.workspaces.platform_login_redirect` (apunta a `/platform`) — NO
por `webapp.nav.login_redirect` (Copilot-específico): Capital Humano es un
workspace distinto del Copiloto, mismo criterio que Operations/Commercial.

Deliberadamente SIN `log_activity`/module id: `activity_service.
log_activity`'s `module=` es OPCIONAL (ver su firma) — Commercial pasa
`CM01_MODULE_ID` por elección propia, no porque el contrato lo exija. El
namespace `CHxx` de `module_registry.py` queda RESERVADO, sin asignar,
tal como estaba antes de esta fase (ver Phase 1 forensic, "no forzar un
module ID solo por crear el workspace")."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, workspaces
from webapp.services import auth_service
from webapp.templating import templates

router = APIRouter()


def _require_human_capital_session(request: Request, *, next_path: str | None = None):
    """Redirect (sin sesión) o 403 (sesión sin autorización de Capital
    Humano) — o None si OK. MISMO patrón que
    `commercial._require_commercial_session`/
    `staffing._require_operations_session`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not human_capital_access.is_human_capital_authorized(request):
        raise HTTPException(403, "No autorizado para Capital Humano")
    return None


@router.get("/human-capital", response_class=HTMLResponse, include_in_schema=False)
def human_capital_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/landing.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            # Capital Humano no tiene concepto de "pack" propio — MISMO
            # criterio que Commercial/Operations: la identidad animada
            # compartida se resuelve contra el pack por defecto.
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "inicio",
        },
    )
