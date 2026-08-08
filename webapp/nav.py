"""
nav.py — Navegación de la web actual.

Helper de redirección de la webapp. Se extrajo de ``webapp/main.py`` (gate
distribuido pre-2D) para que los routers con templates puedan usarlo sin importar
``webapp.main``.

Clasificación: PRESENTATION / CURRENT WEB NAVIGATION. ``login_redirect`` todavía
transporta ``pack`` (contexto del Copiloto); por eso NO se afirma que sea
PLATFORM-pure ni reutilizable por futuros workspaces. La navegación transversal
de plataforma se diseñará recién cuando exista un login/workspace flow real. NO
se generaliza todavía el contrato de redirect/next/pack.

NO importa ``webapp.main``. NO contiene rutas, servicios ni access policy.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from webapp.services.content_service import list_modules


def home_url(pack: str) -> str:
    """URL de entrada a la app tras el login (perfil full): el 00 Intro.

    La intro inmersiva es la puerta narrativa del copiloto; el perfil de solo
    lectura (ACADEMY_USERS) no pasa por acá — aterriza en Danwor Academy (/home)."""
    modules = list_modules(pack)
    if not modules:
        raise HTTPException(503, "No hay módulos configurados")
    module = next(
        (item for item in modules if item.number == "00"),
        next((item for item in modules if item.number == "01"), modules[0]),
    )
    return f"/modules/{module.stem}?pack={pack}"


def login_redirect(request: Request, pack: str) -> RedirectResponse:
    """Redirige a la sección de acceso preservando la URL original en `next`,
    para que tras el login el usuario vuelva a donde quería ir (en vez de
    aterrizar siempre en el destino por defecto de su perfil)."""
    next_path = request.url.path
    if request.url.query:
        next_path += f"?{request.url.query}"
    params = {"pack": pack, "next": next_path}
    return RedirectResponse(f"/?{urlencode(params)}#acceso", status_code=307)
