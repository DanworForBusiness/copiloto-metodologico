"""Perfil del usuario del Hub (transversal): identidad corporativa de Entra ID (solo lectura) + foto personalizada propia.

  - `GET  /perfil/yo`                 → identidad de la sesión y foto efectiva (para el menú del header).
  - `GET  /perfil/avatar/{key}`       → imagen efectiva de un colaborador (cualquier usuario autorizado del Hub: los avatares se ven en
                                         Comunidad y en el header). Nunca URLs de Spaces.
  - `POST /perfil/foto`               → cargar/sustituir la foto PERSONALIZADA propia (multipart).
  - `POST /perfil/foto/quitar`        → quitarla (vuelve la corporativa o las iniciales).
  - `POST /perfil/foto/corporativa`   → sincronizar la foto corporativa PROPIA con el token delegado (`User.Read`, ya consentido).
Cada colaborador solo administra la suya: la identidad sale SIEMPRE de la sesión, nunca del pedido. Escrituras con origen verificado.
Nombre, correo y roles los gobierna Entra ID: no se editan acá.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from webapp import same_origin_guard
from webapp.services import auth_service, cartelera_media as media, user_avatars
from webapp.templating import templates

router = APIRouter()
_NO_STORE = {"Cache-Control": "no-store"}
_KEY = re.compile(r"^[0-9a-f]{32}$")
templates.env.globals["avatar_url"] = user_avatars.avatar_url        # componente transversal: cualquier plantilla puede pedir la foto efectiva


def _user(request: Request, *, write: bool = False) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not auth_service.is_authorized(str(user.get("sub") or "")):
        raise HTTPException(403, "No autorizado")
    if write and not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no verificado")
    return user


def _me(user: dict) -> dict:
    email = str(user.get("sub") or "").strip().lower()
    eff = user_avatars.effective(email)
    name = str(user.get("name") or email)
    return {"name": name, "email": email, "initials": "".join(w[0] for w in name.split()[:2]).upper() or "?",
            "source": eff["source"], "avatar": user_avatars.avatar_url(email), "has_custom": eff["source"] == "custom"}


@router.get("/perfil/yo", include_in_schema=False)
def profile_me(request: Request):
    return JSONResponse(_me(_user(request)), headers=_NO_STORE)


@router.get("/perfil/avatar/{avatar_key}", include_in_schema=False)
def profile_avatar(request: Request, avatar_key: str):
    _user(request)
    data = user_avatars.image_bytes(avatar_key) if _KEY.match(avatar_key or "") else None
    if data is None:
        raise HTTPException(404, "Sin foto")
    return Response(content=data, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
                                                                    "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/perfil/foto", include_in_schema=False)
async def profile_photo_upload(request: Request):
    user = _user(request, write=True)
    if int(request.headers.get("content-length") or 0) > media.MAX_UPLOAD_BYTES + 1024 * 1024:
        return JSONResponse({"error": "La imagen supera 10 MB."}, status_code=422, headers=_NO_STORE)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "Elegí una imagen."}, status_code=422, headers=_NO_STORE)
    try:
        user_avatars.set_custom(user["sub"], await upload.read(media.MAX_UPLOAD_BYTES + 1))
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422, headers=_NO_STORE)
    return JSONResponse(_me(user), headers=_NO_STORE)


@router.post("/perfil/foto/quitar", include_in_schema=False)
def profile_photo_remove(request: Request):
    user = _user(request, write=True)
    user_avatars.remove_custom(user["sub"])
    return JSONResponse(_me(user), headers=_NO_STORE)


@router.post("/perfil/foto/corporativa", include_in_schema=False)
async def profile_photo_corporate(request: Request):
    user = _user(request, write=True)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    user_avatars.sync_corporate(user["sub"], str((body or {}).get("graph_token") or ""), force=bool((body or {}).get("force")))
    return JSONResponse(_me(user), headers=_NO_STORE)
