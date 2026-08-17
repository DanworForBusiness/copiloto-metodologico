"""
commercial.py — Comercial / CRM HTTP (Commercial Hub Workspace Foundation,
Batch 0).

Alcance DELIBERADAMENTE mínimo: `GET /comercial` (shell autenticado, sin
Odoo/Graph/dato real) — la MISMA arquitectura Hub que ya demostró
Operations (auth/sesión, topbar, switcher, sidebar, theme, CSS/JS
compartido, loader canónico, permissions pattern), aplicada a un TERCER
workspace. Este batch demuestra únicamente "Comercial ya es una vertical
nativa del Hub" — NO integra el CRM (Odoo) real, NO agrega endpoints de
datos, NO crea un segundo mecanismo de auth.

Consume `webapp.commercial_access.is_commercial_authorized` (allowlist
explícita `COMMERCIAL_ALLOWED_USERS` del workspace Comercial, INDEPENDIENTE
de `OPERATIONS_ALLOWED_USERS` — ver Commercial Hub Workspace Foundation en
HANDOFF), MISMO patrón que `webapp/routers/staffing.py` con
`webapp.operations_access`.

El deep-link no autenticado entra por `webapp.workspaces.platform_login_redirect`
(apunta a `/onedanwor`, vía el alias de compatibilidad `/platform`) — NO por
`webapp.nav.login_redirect` (Copilot-específico): Comercial es un workspace
distinto del Copiloto, mismo criterio que Operations.

Clasificación: COMMERCIAL / CRM HTTP. NOT PLATFORM / SHARED. NO importa
`webapp.main`, no conoce Graph/Odoo, no posee transporte ni cálculos de
negocio — no existen todavía (Batch 0 es plataforma/shell puro).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, workspaces
from webapp.services import auth_service
from webapp.services.activity_context import SCOPE_COMMERCIAL, AREA_CRM, EVENT_MODULE_VIEWED
from webapp.services.activity_service import log_activity
from webapp.services.module_registry import CM01_MODULE_ID
from webapp.templating import templates

router = APIRouter()


def _require_commercial_session(request: Request, *, next_path: str | None = None):
    """Redirect (sin sesión) o 403 (sesión sin autorización de Comercial) —
    o None si OK. MISMO patrón que `staffing._require_operations_session`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not commercial_access.is_commercial_authorized(request):
        raise HTTPException(403, "No autorizado para Comercial")
    return None


@router.get("/comercial", response_class=HTMLResponse, include_in_schema=False)
def commercial_page(request: Request):
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    # Primer evento de USO real de Comercial — MISMO criterio que
    # Operations/Asignaciones (module_viewed, ver staffing.staffing_page):
    # un acceso exitoso a la página. Sin clickstream (el GET, no
    # interacciones client-side).
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_CRM, module=CM01_MODULE_ID)
    return templates.TemplateResponse(
        request=request, name="commercial/crm.html",
        context={
            "current_workspace": workspaces.COMMERCIAL,
            "available_workspaces": workspaces.available_workspaces(user),
            # Comercial no tiene concepto de "pack" propio — MISMO criterio
            # que Operations (ver staffing.staffing_page): la identidad
            # animada compartida se resuelve contra el pack por defecto.
            "pack": DEFAULT_PACK,
            "user": user,
            # Resalta "01 CRM" en commercial_nav.html.
            "active_page": "crm",
        },
    )
