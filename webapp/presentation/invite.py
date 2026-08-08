"""
invite.py — Presentación de la invitación "Desafío" del Copiloto.

Construye el `src` del iframe del diálogo de invitación (o "" si no corresponde
mostrarlo). Extraído de ``webapp/main.py`` (Fase 2E): lo consumen hoy tanto la
página de módulo (que sigue en ``main``) como la página de Academy
(`webapp/routers/academy.py`) — por eso vive fuera del router de Academy.

Clasificación: COPILOT / SHARED PRESENTATION ("shared" = compartido HOY por
features del Copiloto; reuso cross-workspace NOT YET DECIDED). NO importa
``webapp.main``, NO define rutas, NO contiene auth/session policy y NO resuelve
packs (recibe el pack ya resuelto).
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request

from webapp.services.content_service import academy_asset_for

INVITE_COOKIE = "dw-desafio"
INVITE_STEM = "ACAD05_Desafio"


def invite_src(request: Request, pack: str) -> str:
    """src del iframe del dialog de invitación, o "" si no corresponde mostrarlo."""
    if request.cookies.get(INVITE_COOKIE) != "1":
        return ""
    asset = academy_asset_for(INVITE_STEM, pack)
    if not asset:
        return ""
    query = urlencode({"branding": "danwor", "embed": "1", "academy_feedback": INVITE_STEM})
    return f"/pack-assets/{pack}/academy/{asset}?{query}"
