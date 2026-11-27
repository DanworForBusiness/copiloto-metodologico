"""Centro de Notificaciones del Hub (plataforma): bandeja unificada para la campana del header y el portal de Cartelera.

  - `GET  /notificaciones`               → ítems del usuario (más nuevo primero) + contador de no leídas.
  - `POST /notificaciones/leida`         → marcar UNA como leída (`{"id": …}`).
  - `POST /notificaciones/todas`         → marcar todas como leídas.
  - `POST /notificaciones/descartar`     → quitar UNA de la lista del usuario (`{"id": …}`); leer ≠ descartar, el origen no se toca.
  - `GET  /notificaciones/abrir/{id}`    → la marca leída y navega a su contexto; solo ítems de la PROPIA bandeja (sus autorizaciones).
Identidad = la sesión; escrituras con origen verificado. No envía correos (canal aparte).
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from webapp import same_origin_guard
from webapp.services import auth_service, hub_inbox

try:                                                                              # MISMA zona de negocio que el resto del Hub
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:                                                                 # noqa: BLE001 — sin base tz: UTC−3 fijo
    _TZ = timezone(timedelta(hours=-3))

router = APIRouter()
_NO_STORE = {"Cache-Control": "no-store"}


def _user(request: Request, *, write: bool = False) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not auth_service.is_authorized(str(user.get("sub") or "")):
        raise HTTPException(403, "No autorizado")
    if write and not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no verificado")
    return user


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(_TZ).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return ""


def _view(n: dict) -> dict:
    return {"id": n["id"], "source": n["source"], "source_label": n["source_label"], "feature": n.get("feature", ""), "text": n["text"],
            "when": _when(n["at"]) if n.get("at") else "", "unread": n["unread"],
            "href": "/notificaciones/abrir/" + urllib.parse.quote(n["id"], safe="")}


@router.get("/notificaciones", include_in_schema=False)
def inbox(request: Request):
    rows = hub_inbox.items(_user(request))
    return JSONResponse({"items": [_view(n) for n in rows], "unread": sum(1 for n in rows if n["unread"])}, headers=_NO_STORE)


@router.post("/notificaciones/leida", include_in_schema=False)
async def inbox_read(request: Request):
    user = _user(request, write=True)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not hub_inbox.mark_read(user, str((body or {}).get("id") or "")):
        raise HTTPException(404, "Notificación no disponible")
    return JSONResponse({"unread": hub_inbox.unread_count(user)}, headers=_NO_STORE)


@router.post("/notificaciones/descartar", include_in_schema=False)
async def inbox_dismiss(request: Request):
    user = _user(request, write=True)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not hub_inbox.dismiss(user, str((body or {}).get("id") or "")):
        raise HTTPException(404, "Notificación no disponible")
    return JSONResponse({"unread": hub_inbox.unread_count(user)}, headers=_NO_STORE)


@router.post("/notificaciones/todas", include_in_schema=False)
def inbox_read_all(request: Request):
    user = _user(request, write=True)
    hub_inbox.mark_all_read(user)
    return JSONResponse({"unread": 0}, headers=_NO_STORE)


@router.get("/notificaciones/abrir/{notification_id:path}", include_in_schema=False)
def inbox_open(request: Request, notification_id: str):
    user = _user(request)
    n = hub_inbox.resolve(user, notification_id)
    if not n or not str(n.get("url") or "").startswith("/"):                    # solo destinos internos del Hub
        raise HTTPException(404, "Notificación no disponible")
    hub_inbox.mark_read(user, notification_id)
    return RedirectResponse(n["url"], status_code=303)
