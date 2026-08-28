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
    ACADEMY_USERS,
    ALLOWED_USERS,
    AUTH_DEFAULT_DOMAIN,
    AZURE_CLIENT_ID,
    AZURE_TENANT_ID,
    COMMERCIAL_ALLOWED_USERS,
    OPERATIONS_ALLOWED_USERS,
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


# ── Allow-list + perfiles de acceso ────────────────────────────────────────────
def _csv_users(raw: str | None, *, empty_is_all: bool) -> set[str] | None:
    """Parsea una lista de usuarios. None ⇒ 'ALL' (cualquier autenticado del tenant).
    Un set ⇒ mails permitidos. `empty_is_all` define cómo interpretar la lista vacía:
    ALLOWED_USERS vacío = ALL (histórico); ACADEMY_USERS vacío = nadie."""
    raw = (raw or "").strip()
    if not raw:
        return None if empty_is_all else set()
    if raw.upper() == "ALL":
        return None
    domain = (AUTH_DEFAULT_DOMAIN or "danwor.com").strip().lower().lstrip("@")
    return {
        user if "@" in user else f"{user}@{domain}"
        for user in (item.strip().lower() for item in raw.split(",")) if user
    }


def _allowed_set() -> set[str] | None:
    """Perfil FULL. None ⇒ 'ALL' (todos full); set() ⇒ nadie full explícito; o set de mails.
    Vacío = nadie full (NO 'ALL'): así, con ACADEMY_USERS, el resto cae a solo-lectura."""
    return _csv_users(ALLOWED_USERS, empty_is_all=False)


def _academy_set() -> set[str] | None:
    """Perfil SOLO LECTURA. None ⇒ 'ALL' (cualquier autenticado no-full); set() ⇒ nadie."""
    return _csv_users(ACADEMY_USERS, empty_is_all=False)


def user_tier(email: str) -> str | None:
    """Perfil del usuario: 'full' (copiloto completo + IA), 'academy' (solo lectura,
    sin IA) o None (no autorizado). FULL gana si está en ambas listas."""
    email = (email or "").strip().lower()
    if not email:
        return None
    full = _allowed_set()       # None='ALL', set() vacío = nadie, o set de mails
    academy = _academy_set()    # idem
    # Sin NINGUNA lista configurada (ambas vacías) → abierto: todos full.
    # Evita un lockout accidental y preserva el "sin config = abierto" histórico.
    if full == set() and academy == set():
        return "full"
    if full is None or email in full:
        return "full"
    if academy is None or email in academy:
        return "academy"
    return None


def is_authorized(email: str) -> bool:
    return user_tier(email) is not None


def _operations_set() -> set[str]:
    """Allowlist EXPLÍCITA y PROVISORIA de Operations (Operations Workspace
    Access Gate). Reusa `_csv_users` para la normalización — MISMA que
    ALLOWED_USERS (usernames compactos → `@AUTH_DEFAULT_DOMAIN`, CSV,
    case-insensitive) — nunca un segundo algoritmo de identidad.

    SIEMPRE devuelve un `set[str]` concreto — NUNCA `None`. A DIFERENCIA
    de `_allowed_set()`/`_academy_set()`: acá se pide `empty_is_all=False`
    (ausente/vacío -> `set()`, nadie) Y ADEMÁS se descarta el literal
    "ALL" que `_csv_users` sí soporta para esas otras dos listas. Con
    `empty_is_all=False`, `_csv_users` solo devuelve `None` para ese
    literal "ALL" (case-insensitive, espacios ya recortados) — nunca por
    otro motivo — así que convertir `None -> set()` acá rechaza el
    wildcard sin duplicar su detección. Contrato de seguridad explícito:
    Operations NUNCA puede abrirse a "todos" ni por accidente de reuso del
    parser compartido ni por configurar `OPERATIONS_ALLOWED_USERS=ALL`."""
    result = _csv_users(OPERATIONS_ALLOWED_USERS, empty_is_all=False)
    return result if result is not None else set()


def is_operations_user(email: str) -> bool:
    """True si `email` (o su alias compacto `usuario@` + `AUTH_DEFAULT_DOMAIN`)
    está en `OPERATIONS_ALLOWED_USERS`. Deliberadamente ORTOGONAL a
    `user_tier`/`is_authorized`: NO es "admisión a la app", es la
    autorización de UN workspace adicional — un caller debe seguir
    exigiendo `is_authorized(email)` por separado (ver
    `webapp.operations_access.is_operations_authorized_user`). Nunca
    "todos" — ver `_operations_set()`."""
    email = (email or "").strip().lower()
    if not email:
        return False
    return email in _operations_set()


def _commercial_set() -> set[str]:
    """Allowlist EXPLÍCITA y PROVISORIA de Comercial — MISMO contrato que
    `_operations_set()` (ver ese docstring): SIEMPRE devuelve un `set[str]`
    concreto, nunca `None`, y el literal "ALL" queda rechazado — Comercial
    NUNCA puede abrirse a "todos" por accidente de reuso del parser
    compartido ni por configurar `COMMERCIAL_ALLOWED_USERS=ALL`."""
    result = _csv_users(COMMERCIAL_ALLOWED_USERS, empty_is_all=False)
    return result if result is not None else set()


def is_commercial_user(email: str) -> bool:
    """True si `email` (o su alias compacto `usuario@` + `AUTH_DEFAULT_DOMAIN`)
    está en `COMMERCIAL_ALLOWED_USERS`. Deliberadamente ORTOGONAL a
    `user_tier`/`is_authorized` — MISMO criterio que `is_operations_user` (ver
    ese docstring): un caller debe seguir exigiendo `is_authorized(email)`
    por separado (ver `webapp.commercial_access.is_commercial_authorized_user`).
    Nunca "todos" — ver `_commercial_set()`."""
    email = (email or "").strip().lower()
    if not email:
        return False
    return email in _commercial_set()


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
