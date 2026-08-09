"""
platform.py — Entrada de plataforma multi-workspace (batch de transición,
posterior al primer hito real de Staffing).

`GET /platform`:
- SIN sesión → renderiza la misma landing histórica
  (`webapp.routers.landing.render_landing`) con copy platform-neutral —
  mismo auth, mismo mecanismo de `next`, mismo template, sin segundo
  OAuth/session/token.
- CON sesión → resuelve workspaces disponibles (`webapp.workspaces`) y
  decide: 0 → estado de sin-acceso controlado (la autoridad sigue siendo
  server-side — no hay redirect-loop); 1 → redirect directo a su
  `home_url`; 2+ → selector de workspaces. Si `next` es válido y
  explícito, tiene precedencia sobre la resolución de workspace (deep-link
  intent siempre gana) — la autorización real se sigue verificando en el
  destino, esto es solo una decisión de navegación.

Clasificación: PLATFORM / ENTRY HTTP. NO importa `webapp.main`, NO conoce
Graph, NO conoce `pack` más allá de resolverlo para pasarlo al helper
compartido de landing (mismo mecanismo que `/`/`/copilot`, para que el
`data-home` del botón de login funcione igual). Delega TODA la
disponibilidad de workspaces a `webapp.workspaces` — no duplica ninguna
regla de autorización acá.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp.routers.landing import render_landing
from webapp.services import auth_service
from webapp.templating import templates
from webapp.workspaces import available_workspaces

router = APIRouter()

# Copy platform-neutral — deliberadamente SIN mencionar "Copiloto
# Metodológico"/"Arquitecto Danwor" como identidad de plataforma (ver
# HANDOFF): "Plataforma Danwor" es un nombre EXPLÍCITAMENTE provisorio, no
# una decisión de branding final.
_PLATFORM_LANDING_COPY = {
    "title": "Plataforma Danwor",
    "eyebrow_ink": "Plataforma",
    "eyebrow_gold": "Danwor",
    "brand_title": "Plataforma",
    "brand_subtitle": "Danwor",
    "brand_description": "Un único acceso a los espacios de trabajo y herramientas internas Danwor.",
    "feature_list": None,
    "login_sub_lines": ["Plataforma Danwor"],
}


@router.get("/platform", response_class=HTMLResponse, include_in_schema=False)
def platform_entry(request: Request, pack: str = Query(DEFAULT_PACK), next: str = Query("")):
    user = auth_service.session_user(request)
    if not user:
        selected = resolve_pack(pack)
        # default_home="/platform": sin `next` explícito, tras el login el
        # browser vuelve a ESTE mismo endpoint (ahora autenticado), que
        # recién ahí resuelve qué workspace(s) corresponden.
        return render_landing(
            request, selected, next, default_home="/platform", copy=_PLATFORM_LANDING_COPY,
        )

    safe_next = next if next.startswith("/") and not next.startswith("//") else ""
    if safe_next:
        return RedirectResponse(safe_next, status_code=307)

    workspaces = available_workspaces(user)
    if len(workspaces) == 1:
        return RedirectResponse(workspaces[0].home_url, status_code=307)
    if not workspaces:
        return templates.TemplateResponse(request=request, name="platform_no_access.html", context={})
    return templates.TemplateResponse(
        request=request, name="platform_selector.html", context={"workspaces": workspaces},
    )
