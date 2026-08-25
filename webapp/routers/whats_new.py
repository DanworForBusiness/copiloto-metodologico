"""
whats_new.py — Novedades del Copiloto ("What's New").

Router del FEATURE COPILOT "Novedades": `GET /whats-new` (página) y
`POST /whats-new/dismiss` (cierre del banner). Extraído de ``webapp/main.py``
(Fase 2D) como RELOCACIÓN pura — sin rediseño de comportamiento.

Es un feature COPILOT y sus entradas son pack-scoped de punta a punta vía
``whats_new_service.entries(pack)``. Esa afirmación ERA FALSA hasta el
corrective de propiedad editorial: la fuente manual respetaba el pack, pero
una segunda fuente leía ``git log`` sobre TODO el repositorio y publicaba
cualquier commit con trailer ``Novedad:`` — así un fix de autenticación de
Graph terminó anunciado como novedad del Copiloto. Hoy la única fuente es
``content_packs/<pack>/whats_new.yaml``. CONSUME capacidades compartidas sin
volverse él mismo una capacidad de plataforma: renderiza con la env Jinja única
(``webapp.templating``), resuelve pack desde ``core.content_registry`` y usa la
access policy / navegación de la web (``webapp.access`` / ``webapp.nav``).

Regla de dependencia: NO importa ``webapp.main`` (es main quien incluye este
router). Importa cada dependencia de su dueño explícito; no usa los aliases de
``main``. Sin runtime/bootstrap, sin ``app.state``, sin Jinja env nueva.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from webapp.services import auth_service
from webapp.services import whats_new_service as whatsnew
from webapp.services.content_service import (
    academy_asset_for,
    list_academy,
    list_appendices,
    list_modules,
)
from webapp import access, nav, workspaces
from webapp.templating import templates
from core.content_registry import (
    DEFAULT_PACK,
    content_pack_short_label,
    resolve_pack,
)
from config import SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE

router = APIRouter()

_WHATSNEW_COOKIE_MAXAGE = 60 * 60 * 24 * 365


def _whatsnew_mark_seen(response, hash_: str) -> None:
    if hash_:
        response.set_cookie(
            whatsnew.COOKIE_NAME, hash_, max_age=_WHATSNEW_COOKIE_MAXAGE, path="/",
            httponly=False, samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
        )


@router.get("/whats-new", response_class=HTMLResponse, include_in_schema=False)
def whats_new_page(request: Request, pack: str = Query(DEFAULT_PACK)):
    """Página de Novedades: los commits marcados con trailer `Novedad:`, renderizados
    con el pipeline de contenido. Visitarla marca todo como visto (avanza la cookie)."""
    user = auth_service.session_user(request)
    if not user:
        return nav.login_redirect(request, resolve_pack(pack))
    selected = resolve_pack(pack) if access.is_diagnostic(request) else DEFAULT_PACK
    items = whatsnew.entries(selected)
    categorias = sorted({e["categoria"] for e in items if e.get("categoria")})
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    resp = templates.TemplateResponse(
        request=request, name="whats_new.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": access.is_readonly(request), "pack": selected,
            "pack_label": content_pack_short_label(selected), "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            # El detalle lo arma Alpine desde este JSON (filtros + colapsables por fecha).
            "whatsnew_items": items, "categorias": categorias, "n_novedades": len(items),
        },
    )
    _whatsnew_mark_seen(resp, items[0]["ref"] if items else "")
    return resp


@router.post("/whats-new/dismiss", response_class=HTMLResponse, include_in_schema=False)
def whats_new_dismiss(request: Request):
    """Cierra el banner y no lo vuelve a mostrar hasta que haya una novedad más nueva
    (avanza la cookie al anuncio más reciente). Devuelve vacío → HTMX quita el banner."""
    resp = HTMLResponse("")
    _whatsnew_mark_seen(resp, whatsnew.latest_ref())
    return resp
