"""
workspaces.py — Modelo de workspaces de la plataforma Danwor (descubrimiento
+ metadata de ruteo). Fase de transición multi-workspace (post primer hito
real de Staffing).

Clasificación: PLATFORM / WORKSPACE DISCOVERY & ROUTING METADATA. NO importa
``webapp.main``. NO mantiene estado mutable. NO es un motor de autorización
general — cada `Workspace` apunta a un predicado YA EXISTENTE (Copiloto:
sesión válida; Operations: `webapp.operations_access.is_staffing_authorized_user`,
el mismo tier "full" que ya usa Staffing) — este módulo NO inventa una nueva
semántica de permisos, solo las agrega para poder listarlas.

El descubrimiento de workspaces queda deliberadamente desacoplado de la
FUENTE de autorización: hoy la fuente son los tiers de `auth_service`
(`ALLOWED_USERS`/`ACADEMY_USERS`); en el futuro podría ser grupos de Entra,
app roles, u otra fuente. Cambiar esa fuente no debería requerir cambiar
este módulo ni el router de `/platform` — solo el predicado que cada
`Workspace` referencia.

``platform_login_redirect`` es el análogo de `webapp.nav.login_redirect`
pero apuntando a `/platform` en vez de `/` — lo usan consumidores FUERA del
Copiloto (hoy: Operations/Staffing) para entrar por la puerta
platform-neutral. Deliberadamente NO transporta `pack` — `pack` es un
selector de contenido interno del Copiloto (ver `core.content_registry`),
sin significado para otros workspaces. `webapp.nav.login_redirect`
(Copilot-específico) NO se modifica ni se reemplaza — los deep links del
Copiloto siguen usándolo sin cambios.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from webapp import operations_access


@dataclass(frozen=True)
class Workspace:
    key: str
    label: str
    subtitle: str
    home_url: str
    is_authorized: Callable[[dict | None], bool]


def _copilot_authorized(user: dict | None) -> bool:
    # Misma semántica ya usada en toda la app (landing.py, modules.py,
    # wizard.py, etc.): sesión válida = acceso al Copiloto. El invariante de
    # auth_service garantiza que no existe sesión con tier None (la sesión
    # ni se crea si el email no resuelve a "full" ni "academy" — ver
    # auth_service.py) — por eso "sesión válida" alcanza como regla completa.
    return user is not None


COPILOT = Workspace(
    key="copilot",
    label="Copiloto Metodológico",
    subtitle="Arquitecto Danwor",
    home_url="/",
    is_authorized=_copilot_authorized,
)

OPERATIONS = Workspace(
    key="operations",
    label="Operaciones",
    subtitle="Staffing",
    home_url="/operations/staffing",
    is_authorized=operations_access.is_staffing_authorized_user,
)

_ALL_WORKSPACES: tuple[Workspace, ...] = (COPILOT, OPERATIONS)


def available_workspaces(user: dict | None) -> list[Workspace]:
    """Workspaces que `user` puede ver, en orden estable. `user` son los
    claims de sesión (o None si no hay sesión) — nunca un `Request`."""
    return [ws for ws in _ALL_WORKSPACES if ws.is_authorized(user)]


def platform_login_redirect(request: Request) -> RedirectResponse:
    """Redirige a `/platform` preservando el destino original en `next` —
    igual mecanismo que `webapp.nav.login_redirect`, pero sin `pack` (no
    aplica fuera del Copiloto) y apuntando a la puerta platform-neutral."""
    next_path = request.url.path
    if request.url.query:
        next_path += f"?{request.url.query}"
    return RedirectResponse(f"/platform?{urlencode({'next': next_path})}", status_code=307)
