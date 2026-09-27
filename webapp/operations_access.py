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

FAIL-CLOSED por diseño para todo usuario NO-owner: ``OPERATIONS_ALLOWED_USERS``
ausente/vacío ⇒ `auth_service.is_operations_user` siempre False ⇒ sin
allowlist explícita, no hay acceso -- sin fallback a ALLOWED_USERS/
ACADEMY_USERS/tier "full"/"todo autenticado".

CORRECTIVE post-#107 (owner decision -- reemplaza la política anterior de
este mismo docstring, que prohibía explícitamente un bypass de APP_OWNER):
`APP_OWNER` (``webapp/access.py::is_owner_user``) SÍ es superusuario de
este workspace ahora -- un usuario ya autenticado cuya sesión resuelve
`is_owner_user` entra sin necesitar estar en `OPERATIONS_ALLOWED_USERS`. La
excepción vive ÚNICAMENTE en `is_owner_user` (`webapp/access.py`) -- acá
sólo se CONSUME vía `or`, nunca se reimplementa la comparación contra
`APP_OWNER`. Sigue exigiendo autenticación PRIMERO (`is_owner_user` es
False sin sesión) -- `APP_OWNER` nunca es un atajo de login, sólo de
autorización posterior.

NO es RBAC, NO es un motor de permisos general -- allowlist explícita y
provisoria hasta que un mecanismo de entitlement central (Entra Groups/App
Roles) lo justifique (ver HANDOFF).

Distinto de ``webapp/access.py`` en alcance: ese módulo es la política del
Copiloto (``is_readonly == tier academy``, semántica de negocio de ESE
workspace, explícitamente no-generalizada) -- pero SU predicado de owner
(`is_owner_user`) SÍ se reutiliza acá (import explícito abajo), ver el
CORRECTIVE arriba.

`is_operations_authorized_user` es el predicado PURO (sin `Request`) --
usado por `webapp/workspaces.py` (descubrimiento) Y por
`webapp/routers/staffing.py` (enforcement server-side) -- LA MISMA regla
gobierna ambos, nunca dos políticas separadas. `is_operations_authorized(request)`
es el wrapper HTTP, sin cambio de comportamiento -- delega en el predicado puro.
"""
from __future__ import annotations

from fastapi import Request

from webapp import access
from webapp.services import auth_service


def is_operations_authorized_user(user: dict | None) -> bool:
    """True si `user` (claims de sesión, o None) es el dueño de la app
    (`access.is_owner_user`, ver corrective post-#107 arriba) O tiene
    admisión BASE a la app Y está en `OPERATIONS_ALLOWED_USERS`. Predicado
    puro — sin `Request` — para que el resolver de workspaces (PLATFORM) y
    el enforcement HTTP de Operations compartan LA MISMA regla."""
    if not user:
        return False
    if access.is_owner_user(user):
        return True
    email = str(user.get("sub") or "")
    return auth_service.is_authorized(email) and auth_service.is_operations_user(email)


def is_operations_authorized(request: Request) -> bool:
    """True si hay sesión válida y `is_operations_authorized_user` la
    autoriza. Frontera de seguridad real de Operations — GET y POST de
    Staffing (y cualquier futura área de Operations) deben pasar por acá."""
    return is_operations_authorized_user(auth_service.session_user(request))
