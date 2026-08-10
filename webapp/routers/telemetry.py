"""
telemetry.py — beacon liviano de telemetría de interacción del cliente.

Segundo router extraído de ``webapp/main.py`` (Fase 2A del refactor). Primer
router respaldado por un servicio compartido (``activity_service``) + auth: su
única dependencia de dominio es la observabilidad, así que no arrastra
templates, singletons de runtime ni helpers de main.

Regla de dependencia: NO importa ``webapp.main`` (es main quien incluye este
router). Comportamiento preservado verbatim respecto del handler original
(best-effort, 204 siempre, gate VERBOSE, no-op sin sesión).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from webapp.services import auth_service
from webapp.services.activity_service import is_verbose, log_verbose
from webapp.services.activity_context import SCOPE_COPILOT, AREA_METHODOLOGY

router = APIRouter()

_TELEMETRY_EVENTS = {
    "module_reached_end", "module_continue",
    "nav_jump", "link_external", "disclosure_toggle", "theme_change",
}


@router.post("/telemetry/event", include_in_schema=False)
async def telemetry_event(request: Request):
    """Beacon liviano de interacción cliente (fin de módulo, uso del cue de
    continuar). Solo registra en LOG_ACTIVITY=VERBOSE; en otro caso es no-op.
    Siempre responde 204 para no ensuciar la consola del navegador."""
    if not is_verbose():
        return Response(status_code=204)
    user = auth_service.session_user(request)
    if not user:
        return Response(status_code=204)
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=204)
    event = str((data or {}).get("event") or "").strip()
    if event in _TELEMETRY_EVENTS:
        # El contexto lo fija el servidor (beacon del Copiloto = copilot/methodology);
        # se excluyen scope/area/event del body para que el cliente NO pueda inyectar
        # ni colisionar el contexto de plataforma (son keyword-only en log_verbose).
        payload = {k: v for k, v in data.items() if k not in ("event", "scope", "area")}
        log_verbose(event, user=user, scope=SCOPE_COPILOT, area=AREA_METHODOLOGY, **payload)
    return Response(status_code=204)
