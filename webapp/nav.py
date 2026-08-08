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

from fastapi import Request
from fastapi.responses import RedirectResponse


def login_redirect(request: Request, pack: str) -> RedirectResponse:
    """Redirige a la sección de acceso preservando la URL original en `next`,
    para que tras el login el usuario vuelva a donde quería ir (en vez de
    aterrizar siempre en el destino por defecto de su perfil)."""
    next_path = request.url.path
    if request.url.query:
        next_path += f"?{request.url.query}"
    params = {"pack": pack, "next": next_path}
    return RedirectResponse(f"/?{urlencode(params)}#acceso", status_code=307)
