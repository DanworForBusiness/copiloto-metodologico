"""
commercial_access.py — Access policy del workspace Comercial (Commercial
Hub Workspace Foundation, Batch 0 — plataforma/shell únicamente, ver
HANDOFF).

Regla: `is_commercial_authorized_user(user)` es la frontera de seguridad
REAL del workspace — MISMO contrato que `webapp.operations_access.
is_operations_authorized_user` (ver ese docstring para el razonamiento
completo), aplicado a Comercial. Dos condiciones, AMBAS requeridas:

1. Admisión BASE a la aplicación (``auth_service.is_authorized`` — el
   mismo login/allow-list de siempre, ALLOWED_USERS/ACADEMY_USERS). Esta
   política NO se modifica ni se debilita.
2. Autorización EXPLÍCITA de Comercial (``auth_service.is_commercial_user``
   — allowlist provisoria ``COMMERCIAL_ALLOWED_USERS``, ver HANDOFF).

Deliberadamente ORTOGONAL al tier de Copiloto (full/academy) y a
``OPERATIONS_ALLOWED_USERS`` — Comercial es un workspace INDEPENDIENTE:
estar autorizado a Operaciones no implica nada sobre Comercial, y
viceversa. Cada workspace declara su propia allowlist.

FAIL-CLOSED por diseño: ``COMMERCIAL_ALLOWED_USERS`` ausente/vacío ⇒
`auth_service.is_commercial_user` siempre False ⇒ nadie autorizado — sin
fallback a ALLOWED_USERS/ACADEMY_USERS/tier "full"/APP_OWNER/"todo
autenticado". `APP_OWNER` (``webapp/access.py``, diagnóstico del Copiloto)
NUNCA se referencia acá — sin bypass implícito; si el dueño de la app
necesita Comercial, su identidad debe estar explícita en
``COMMERCIAL_ALLOWED_USERS`` como cualquier otro usuario (auditable en
config) — nunca hardcodeada como default productivo.

NO es RBAC, NO es un motor de permisos general — allowlist explícita y
provisoria hasta que un mecanismo de entitlement central (Entra Groups/App
Roles) lo justifique (ver HANDOFF).

`is_commercial_authorized_user` es el predicado PURO (sin `Request`) —
usado por `webapp/workspaces.py` (descubrimiento) Y por
`webapp/routers/commercial.py` (enforcement server-side) — LA MISMA regla
gobierna ambos, nunca dos políticas separadas. `is_commercial_authorized(request)`
es el wrapper HTTP, sin cambio de comportamiento — delega en el predicado puro.
"""
from __future__ import annotations

from fastapi import Request

from webapp.services import auth_service


def is_commercial_authorized_user(user: dict | None) -> bool:
    """True si `user` (claims de sesión, o None) tiene admisión BASE a la
    app Y está en `COMMERCIAL_ALLOWED_USERS`. Predicado puro — sin
    `Request` — para que el resolver de workspaces (PLATFORM) y el
    enforcement HTTP de Comercial compartan LA MISMA regla."""
    if not user:
        return False
    email = str(user.get("sub") or "")
    return auth_service.is_authorized(email) and auth_service.is_commercial_user(email)


def is_commercial_authorized(request: Request) -> bool:
    """True si hay sesión válida y `is_commercial_authorized_user` la
    autoriza. Frontera de seguridad real de Comercial — cualquier ruta de
    Comercial debe pasar por acá."""
    return is_commercial_authorized_user(auth_service.session_user(request))


# ── Estrategia Comercial — VER vs EDITAR (Commercial Strategy edit
# authorization) ───────────────────────────────────────────────────────────
# Dos preguntas DELIBERADAMENTE separadas — estar autorizado a Comercial
# NUNCA implica poder editar Estrategia:
#
#   can_view_strategy_user(user) = is_commercial_authorized_user(user)
#   can_edit_strategy_user(user) = is_commercial_authorized_user(user)
#                                   AND auth_service.is_content_owner_user(email)
#
# FAIL-CLOSED por diseño (MISMO criterio que `is_commercial_authorized_user`
# arriba): `CONTENT_OWNER` ausente/vacío ⇒ `is_content_owner_user` siempre
# False ⇒ NADIE puede editar — sin fallback implícito a "todos los
# autorizados de Comercial" ni a `APP_OWNER` (`webapp/access.py`, ver su
# docstring — NUNCA se importa/referencia acá, mismo criterio que arriba: si
# el dueño de la app necesita editar Estrategia, su identidad debe estar
# explícita en `CONTENT_OWNER` como cualquier otro usuario).

def can_view_strategy_user(user: dict | None) -> bool:
    """Alias explícito del contrato pedido por el owner — MISMA regla que
    `is_commercial_authorized_user` (ver ahí), nunca una segunda política."""
    return is_commercial_authorized_user(user)


def can_edit_strategy_user(user: dict | None) -> bool:
    """True si `user` puede VER Estrategia Y además está en `CONTENT_OWNER`.
    Predicado PURO (sin `Request`) — usado tanto por el router (para decidir
    si renderizar el botón "Editar", nunca la única defensa) como por el
    futuro endpoint de escritura (enforcement server-side, autoritativo)."""
    if not can_view_strategy_user(user):
        return False
    email = str((user or {}).get("sub") or "")
    return auth_service.is_content_owner_user(email)
