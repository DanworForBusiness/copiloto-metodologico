"""Autenticación Microsoft (Entra ID) para la webapp.

Flujo: el navegador hace el OAuth2 + PKCE contra Microsoft (plataforma SPA, el
mismo registro que ya usa el Streamlit original vía streamlit-msal) y nos manda
el id_token. Acá lo verificamos contra el JWKS del tenant, chequeamos la
allow-list y emitimos una cookie de sesión firmada propia (HttpOnly).

Portado del backend de Odoo (C:\\Users\\Joe\\Odoo) + la allow-list del auth.py
original del copiloto. Sin client_secret: es un public client (token delegado).
"""
from __future__ import annotations

from functools import lru_cache
import time

import httpx
from jose import JWTError, jwt

from config import (
    ALLOWED_USERS,
    AUTH_DEFAULT_DOMAIN,
    AZURE_CLIENT_ID,
    AZURE_TENANT_ID,
    SESSION_MAX_AGE,
    SESSION_SECRET,
)

COOKIE_NAME = "dw_session"
_AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/v2.0"


class AuthError(Exception):
    """Falla de autenticación; `denied_user` se setea si autenticó pero no está autorizado."""

    def __init__(self, message: str, *, denied_user: str = ""):
        super().__init__(message)
        self.denied_user = denied_user


# ── Allow-list (misma lógica que el auth.py original del copiloto) ──────────────
def _allowed_set() -> set[str] | None:
    """None ⇒ 'ALL' (cualquier usuario del tenant). Si no, set de mails permitidos."""
    raw = (ALLOWED_USERS or "").strip()
    if not raw or raw.upper() == "ALL":
        return None
    domain = (AUTH_DEFAULT_DOMAIN or "danwor.com").strip().lower().lstrip("@")
    return {
        user if "@" in user else f"{user}@{domain}"
        for user in (item.strip().lower() for item in raw.split(",")) if user
    }


def is_authorized(email: str) -> bool:
    allowed = _allowed_set()
    if allowed is None:  # 'ALL' → el tenant ya restringe quién puede autenticar
        return True
    return (email or "").strip().lower() in allowed


# ── Verificación del id_token de Microsoft (JWKS del tenant) ───────────────────
@lru_cache(maxsize=1)
def _openid_config() -> dict:
    resp = httpx.get(f"{_AUTHORITY}/.well-known/openid-configuration", timeout=10)
    resp.raise_for_status()
    return resp.json()


@lru_cache(maxsize=1)
def _jwks() -> dict:
    resp = httpx.get(_openid_config()["jwks_uri"], timeout=10)
    resp.raise_for_status()
    return resp.json()


def _signing_key(id_token: str) -> dict:
    kid = jwt.get_unverified_header(id_token).get("kid")
    for keys in (_jwks().get("keys", []), None):
        if keys is None:                       # no encontró el kid → refrescar JWKS una vez
            _jwks.cache_clear()
            keys = _jwks().get("keys", [])
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key:
            return key
    raise AuthError("No se encontró la clave pública para validar el token de Microsoft")


def verify_ms_id_token(id_token: str) -> dict:
    """Devuelve los claims del id_token tras validar firma, audiencia e issuer.

    La audiencia se compara case-insensitive: los GUID de Azure no distinguen
    mayúsculas y el token llega en minúsculas, mientras que AZURE_CLIENT_ID puede
    estar configurado con otra capitalización (el /authorize de Microsoft tolera
    la diferencia, pero python-jose haría un match exacto y fallaría).
    """
    try:
        claims = jwt.decode(
            id_token,
            _signing_key(id_token),
            algorithms=["RS256"],
            issuer=_openid_config()["issuer"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise AuthError(f"Microsoft devolvió un token no válido: {exc}") from exc

    audience = claims.get("aud", "")
    audiences = audience if isinstance(audience, list) else [audience]
    if AZURE_CLIENT_ID.lower() not in {str(a).lower() for a in audiences}:
        raise AuthError("Microsoft devolvió un token con audiencia inesperada")
    return claims


# ── Sesión propia (cookie firmada HS256) ───────────────────────────────────────
def user_from_claims(claims: dict) -> dict:
    email = (
        claims.get("preferred_username") or claims.get("email") or claims.get("upn") or ""
    ).strip().lower()
    if not email or "@" not in email:
        raise AuthError("Microsoft no devolvió un email válido")
    if not is_authorized(email):
        raise AuthError(
            f"El usuario {email} autenticó correctamente pero no está autorizado "
            "para ingresar a esta plataforma.",
            denied_user=email,
        )
    full_name = (claims.get("name") or email.split("@")[0]).strip()
    given = (claims.get("given_name") or "").strip()
    family = (claims.get("family_name") or "").strip()
    first = given or (full_name.split()[0] if full_name else "Usuario")
    return {"email": email, "name": full_name, "first_name": first, "family_name": family}


def create_session(user: dict) -> str:
    payload = {
        "sub": user["email"],
        "name": user["name"],
        "first_name": user["first_name"],
        "family_name": user.get("family_name", ""),
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def decode_session(token: str) -> dict | None:
    try:
        return jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except JWTError:
        return None


def session_user(request) -> dict | None:
    """Usuario de la cookie de sesión, o None si no hay/expiró/es inválida."""
    token = request.cookies.get(COOKIE_NAME)
    return decode_session(token) if token else None


def microsoft_configured() -> bool:
    return bool(AZURE_TENANT_ID and AZURE_CLIENT_ID)
