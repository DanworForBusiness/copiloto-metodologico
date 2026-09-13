"""
human_capital_access.py — Access policy del workspace Capital Humano
(Capital Humano Phase 1 — Hub Walking Skeleton).

Regla: `is_human_capital_authorized_user(user)` es la frontera de
seguridad REAL del workspace — MISMO contrato que
`webapp.commercial_access.is_commercial_authorized_user`/
`webapp.operations_access.is_operations_authorized_user` (ver esos
docstrings para el razonamiento completo), aplicado a Capital Humano. Dos
condiciones, AMBAS requeridas:

1. Admisión BASE a la aplicación (``auth_service.is_authorized`` — el
   mismo login/allow-list de siempre, ALLOWED_USERS/ACADEMY_USERS). Esta
   política NO se modifica ni se debilita.
2. Autorización EXPLÍCITA de Capital Humano (``auth_service.
   is_human_capital_user`` — allowlist provisoria
   ``HUMAN_CAPITAL_ALLOWED_USERS``).

Deliberadamente ORTOGONAL al tier de Copiloto (full/academy) y a
``OPERATIONS_ALLOWED_USERS``/``COMMERCIAL_ALLOWED_USERS`` — Capital Humano
es un workspace INDEPENDIENTE: estar autorizado a otro workspace no
implica nada sobre Capital Humano, y viceversa. Cada workspace declara su
propia allowlist.

FAIL-CLOSED por diseño: ``HUMAN_CAPITAL_ALLOWED_USERS`` ausente/vacío ⇒
`auth_service.is_human_capital_user` siempre False ⇒ nadie autorizado —
sin fallback a ALLOWED_USERS/ACADEMY_USERS/tier "full"/APP_OWNER/"todo
autenticado". `APP_OWNER` (``webapp/access.py``, diagnóstico del Copiloto)
NUNCA se referencia acá — sin bypass implícito; si el dueño de la app
necesita Capital Humano, su identidad debe estar explícita en
``HUMAN_CAPITAL_ALLOWED_USERS`` como cualquier otro usuario (auditable en
config) — nunca hardcodeada como default productivo.

NO es RBAC, NO es un motor de permisos general — allowlist explícita y
provisoria hasta que un mecanismo de entitlement central (Entra Groups/App
Roles) lo justifique. Esta es ÚNICAMENTE la frontera A (workspace
admission, "¿puede el usuario entrar a Capital Humano?") de las tres
fronteras del dominio (A. admission / B. capability authorization / C.
data scope, ver Phase 1 output) — B y C NO se implementan en esta fase:
los roles legacy `hrbp`/`hiring_manager` NO existen todavía en el Hub, y
no hay ningún scoping de datos (no hay datos todavía).

`is_human_capital_authorized_user` es el predicado PURO (sin `Request`) —
usado por `webapp/workspaces.py` (descubrimiento) Y por
`webapp/routers/human_capital.py` (enforcement server-side) — LA MISMA
regla gobierna ambos, nunca dos políticas separadas.
`is_human_capital_authorized(request)` es el wrapper HTTP, sin cambio de
comportamiento — delega en el predicado puro.
"""
from __future__ import annotations

from fastapi import Request

from webapp.services import auth_service


def is_human_capital_authorized_user(user: dict | None) -> bool:
    """True si `user` (claims de sesión, o None) tiene admisión BASE a la
    app Y está en `HUMAN_CAPITAL_ALLOWED_USERS`. Predicado puro — sin
    `Request` — para que el resolver de workspaces (PLATFORM) y el
    enforcement HTTP de Capital Humano compartan LA MISMA regla."""
    if not user:
        return False
    email = str(user.get("sub") or "")
    return auth_service.is_authorized(email) and auth_service.is_human_capital_user(email)


def is_human_capital_authorized(request: Request) -> bool:
    """True si hay sesión válida y `is_human_capital_authorized_user` la
    autoriza. Frontera de seguridad real de Capital Humano — cualquier
    ruta de Capital Humano debe pasar por acá."""
    return is_human_capital_authorized_user(auth_service.session_user(request))
