"""
operations_access.py — Access policy del workspace Operations (Operations
Workspace Access Gate, post Staffing V1 CLOSED/GREEN + Danwor Hub CLOSED/GREEN).

Regla: `is_operations_authorized_user(user)` es la frontera de seguridad
REAL del workspace — "¿puede este usuario YA admitido entrar a
Operaciones?" — deliberadamente ya NO es Staffing-específica: Operaciones
es más ancho que Staffing (ver HANDOFF), así que el predicado permanente
vive a nivel de WORKSPACE, no de área interna. Dos condiciones, AMBAS
requeridas:

1. Admisión BASE a la aplicación (``auth_service.is_authorized`` — el
   mismo login/allow-list de siempre, ALLOWED_USERS/ACADEMY_USERS). Esta
   política NO se modifica ni se debilita.
2. Autorización EXPLÍCITA de Operations (``auth_service.is_operations_user``
   — allowlist provisoria ``OPERATIONS_ALLOWED_USERS``, ver HANDOFF).

Deliberadamente ORTOGONAL al tier de Copiloto (full/academy):
``user_tier`` NUNCA se usa acá — un usuario Academy admitido y listado en
``OPERATIONS_ALLOWED_USERS`` SÍ entra a Operaciones; un usuario full NO
listado, NO entra. El tier de Copiloto es semántica de OTRO workspace, no
el modelo de autorización de este.

FAIL-CLOSED por diseño: ``OPERATIONS_ALLOWED_USERS`` ausente/vacío ⇒
`auth_service.is_operations_user` siempre False ⇒ nadie autorizado — sin
fallback a ALLOWED_USERS/ACADEMY_USERS/tier "full"/APP_OWNER/"todo
autenticado". `APP_OWNER` (``webapp/access.py``, diagnóstico del Copiloto)
NUNCA se referencia acá — sin bypass implícito; si el dueño de la app
necesita Operations, su identidad debe estar explícita en
``OPERATIONS_ALLOWED_USERS`` como cualquier otro usuario (auditable en
config).

NO es RBAC, NO es un motor de permisos general — allowlist explícita y
provisoria hasta que un mecanismo de entitlement central (Entra Groups/App
Roles) lo justifique (ver HANDOFF).

Distinto de ``webapp/access.py``: ese módulo es política del Copiloto
(``is_readonly == tier academy``, semántica de negocio de ESE workspace,
explícitamente no-generalizada). Acá se reutiliza el primitivo de
identidad de `auth_service` (capacidad PLATFORM / SHARED) con la regla que
Operations necesita — sin importar `webapp.access` ni `webapp.main`.

`is_operations_authorized_user` es el predicado PURO (sin `Request`) —
usado por `webapp/workspaces.py` (descubrimiento) Y por
`webapp/routers/staffing.py` (enforcement server-side) — LA MISMA regla
gobierna ambos, nunca dos políticas separadas. `is_operations_authorized(request)`
es el wrapper HTTP, sin cambio de comportamiento — delega en el predicado puro.
"""
from __future__ import annotations

from fastapi import Request

from webapp.services import auth_service


def is_operations_authorized_user(user: dict | None) -> bool:
    """True si `user` (claims de sesión, o None) tiene admisión BASE a la
    app Y está en `OPERATIONS_ALLOWED_USERS`. Predicado puro — sin
    `Request` — para que el resolver de workspaces (PLATFORM) y el
    enforcement HTTP de Operations compartan LA MISMA regla."""
    if not user:
        return False
    email = str(user.get("sub") or "")
    return auth_service.is_authorized(email) and auth_service.is_operations_user(email)


def is_operations_authorized(request: Request) -> bool:
    """True si hay sesión válida y `is_operations_authorized_user` la
    autoriza. Frontera de seguridad real de Operations — GET y POST de
    Staffing (y cualquier futura área de Operations) deben pasar por acá."""
    return is_operations_authorized_user(auth_service.session_user(request))
