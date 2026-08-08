"""
auth.py — Autenticación Microsoft (Entra ID) + sesión propia (API JSON).

Router de la capacidad PLATFORM / SHARED "identity + session": los endpoints
`/api/auth/*` y `/api/diagnostic`. Extraído de ``webapp/main.py`` (Fase 2B) como
RELOCACIÓN pura — sin rediseño de OAuth/PKCE/verificación de id_token/cookie de
sesión/atributos de cookie. Responde JSON (no usa templates).

Regla de dependencia: NO importa ``webapp.main`` (es main quien incluye este
router). Usa el MISMO ``auth_service`` y la misma config/logging que antes; no
crea instancias nuevas ni runtime/bootstrap.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from webapp.services import auth_service
from webapp.services.activity_service import log_activity
from config import (
    AZURE_CLIENT_ID, AZURE_TENANT_ID,
    SESSION_MAX_AGE, SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE,
)

router = APIRouter()


class _MsTokenBody(BaseModel):
    id_token: str


@router.get("/api/auth/microsoft/config", include_in_schema=False)
def microsoft_config():
    return {
        "enabled": auth_service.microsoft_configured(),
        "tenant_id": AZURE_TENANT_ID,
        "client_id": AZURE_CLIENT_ID,
    }


@router.post("/api/auth/microsoft/token", include_in_schema=False)
def microsoft_token(body: _MsTokenBody):
    try:
        claims = auth_service.verify_ms_id_token(body.id_token)
        user = auth_service.user_from_claims(claims)
    except auth_service.AuthError as exc:
        detail = {"message": str(exc)}
        if exc.denied_user:
            detail = {"code": "microsoft_user_not_authorized", "denied_user": exc.denied_user, "message": str(exc)}
        return JSONResponse({"detail": detail}, status_code=403 if exc.denied_user else 401)
    except Exception as exc:  # noqa: BLE001 — fallo de red/JWKS u otro
        return JSONResponse({"detail": {"message": f"No se pudo validar el login: {exc}"}}, status_code=502)

    log_activity("login", user=user, job_title=str(claims.get("jobTitle") or ""))
    response = JSONResponse({"ok": True, "first_name": user["first_name"], "name": user["name"]})
    response.set_cookie(
        auth_service.COOKIE_NAME, auth_service.create_session(user),
        max_age=SESSION_MAX_AGE, httponly=True, path="/",
        samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


@router.get("/api/auth/me", include_in_schema=False)
def auth_me(request: Request):
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return {"name": user.get("name"), "first_name": user.get("first_name"), "email": user.get("sub")}


@router.post("/api/diagnostic", include_in_schema=False)
def diagnostic_toggle(request: Request, on: str = Form("")):
    """Persiste el modo diagnóstico en cookie, emitida por el SERVER.

    Desde el JS del iframe enmascarado (danwor.com) una cookie SameSite=Lax no
    viaja en requests cross-site y el modo se "apagaba solo" en producción; acá
    se emite con los mismos atributos que la cookie de sesión (None+Secure en
    prod). El gate real sigue siendo _diagnostic() (cookie + APP_OWNER)."""
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    enabled = on == "on"
    response = JSONResponse({"ok": True, "diagnostic": enabled})
    response.set_cookie(
        "dw-diagnostic", "on" if enabled else "off", max_age=31536000, path="/",
        httponly=False, samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


@router.post("/api/auth/logout", include_in_schema=False)
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        auth_service.COOKIE_NAME, path="/",
        samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response
