"""
workspaces.py — Modelo de workspaces de la plataforma Danwor (descubrimiento
+ metadata de ruteo). Fase de transición multi-workspace (post primer hito
real de Staffing).

Clasificación: PLATFORM / WORKSPACE DISCOVERY & ROUTING METADATA. NO importa
``webapp.main``. NO mantiene estado mutable. NO es un motor de autorización
general — cada `Workspace` apunta a un predicado YA EXISTENTE (Copiloto:
sesión válida; Operations: `webapp.operations_access.is_operations_authorized_user`,
admisión base + allowlist explícita `OPERATIONS_ALLOWED_USERS`, ORTOGONAL
al tier de Copiloto — ver Operations Workspace Access Gate en HANDOFF;
Comercial: `webapp.commercial_access.is_commercial_authorized_user`, MISMO
contrato con su propia allowlist `COMMERCIAL_ALLOWED_USERS` — Commercial
Hub Workspace Foundation, Batch 0) — este módulo NO inventa una nueva
semántica de permisos, solo las agrega para poder listarlas.

El descubrimiento de workspaces queda deliberadamente desacoplado de la
FUENTE de autorización: hoy la fuente de Copiloto son los tiers de
`auth_service` (`ALLOWED_USERS`/`ACADEMY_USERS`) y la de Operations es su
propia allowlist explícita (`OPERATIONS_ALLOWED_USERS`); en el futuro
podría ser grupos de Entra, app roles, u otra fuente. Cambiar esa fuente
no debería requerir cambiar este módulo ni el router de `/platform` —
solo el predicado que cada `Workspace` referencia.

``platform_login_redirect`` es el análogo de `webapp.nav.login_redirect`
pero apuntando a `/platform` en vez de `/` — lo usan consumidores FUERA del
Copiloto (hoy: Operations/Staffing) para entrar por la puerta
platform-neutral. Deliberadamente NO transporta `pack` — `pack` es un
selector de contenido interno del Copiloto (ver `core.content_registry`),
sin significado para otros workspaces. `webapp.nav.login_redirect`
(Copilot-específico) NO se modifica ni se reemplaza — los deep links del
Copiloto siguen usándolo sin cambios.

Usa 303 (no 307): esta es una transición de AUTENTICACIÓN, no una
canonicalización de recurso — la siguiente request SIEMPRE debe volverse
GET, incluso si la request original que disparó el redirect era un POST
(p. ej. el POST de datos de Staffing sin sesión). Reemplazar el body de
un POST por una navegación GET a la entrada de login es exactamente lo
que 303 See Other significa — 307/308 hubieran preservado el método,
propagando un POST de datos hacia una superficie de navegación que nunca
debería recibirlo. `next_path`, si se pasa explícito, permite que el
caller apunte a la PÁGINA (no al endpoint de acción/datos que disparó el
redirect) — por default usa `request.url.path` (+query), correcto para
deep-links GET normales, donde la página y la request son la misma cosa.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from webapp import commercial_access, human_capital_access, operations_access


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
    home_url="/operaciones/staffing/asignaciones",
    is_authorized=operations_access.is_operations_authorized_user,
)

COMMERCIAL = Workspace(
    key="commercial",
    label="Gestión Comercial",
    subtitle="CRM Danwor",
    home_url="/comercial",
    is_authorized=commercial_access.is_commercial_authorized_user,
)

# Capital Humano (Phase 1 — Hub Walking Skeleton): CUARTO workspace, MISMO
# contrato exacto que Operations/Commercial (allowlist `HUMAN_CAPITAL_
# ALLOWED_USERS`, fail-closed, ortogonal a los otros tres). Landing única,
# sin capabilities todavía — ver `webapp/routers/human_capital.py`.
HUMAN_CAPITAL = Workspace(
    key="human-capital",
    label="Capital Humano",
    subtitle="Selección e Incorporación",
    home_url="/cchh",
    is_authorized=human_capital_access.is_human_capital_authorized_user,
)

_ALL_WORKSPACES: tuple[Workspace, ...] = (COPILOT, OPERATIONS, COMMERCIAL, HUMAN_CAPITAL)

# Presentación de cada vertical (tarjeta del selector del Hub: eyebrow/título/acento/descripción/capacidades), keyed por `Workspace.key`.
# Vive acá — el registro compartido de verticales — para que la consuman TANTO el selector (`routers/platform.py`) como otras superficies
# que necesitan la identidad de la vertical sin depender de rutas ni plantillas de páginas (p. ej. los correos de `services/notifications.py`).
WORKSPACE_CARD_COPY = {
    "copilot": {
        "eyebrow": None,
        "title": "Copiloto Metodológico",
        "accent": "Arquitecto Danwor",
        "description": (
            "Un entorno guiado para recorrer nuestro marco metodológico, consultar "
            "decisiones técnicas y acelerar entregables con criterio arquitectónico "
            "y mirada Danwor."
        ),
        "features": [
            "Marco metodológico navegable",
            "Árboles de decisión y asistencia IA",
            "Criterios técnicos para proyectos SAP",
        ],
    },
    "operations": {
        "eyebrow": None,
        "title": "Gestión Inteligente",
        "accent": "Operaciones",
        "description": (
            "El espacio operativo de Danwor: gestión y analítica interna, hoy con "
            "Staffing y asignaciones como primera capacidad implementada."
        ),
        "features": [
            "Staffing y asignaciones",
            "Gestión de proyectos",
            "Analítica operativa",
        ],
    },
    "commercial": {
        "eyebrow": None,
        "title": "Gestión Comercial",
        "accent": "CRM Danwor",
        "description": (
            "Un espacio para gestionar oportunidades, seguir la actividad del CRM y "
            "convertir la información comercial en decisiones accionables."
        ),
        "features": [
            "Pipeline y oportunidades",
            "Seguimiento de actividad comercial",
            "Indicadores y analítica CRM",
        ],
    },
    # Capital Humano: se anuncian SOLO las capacidades ya integradas en main
    # (Base de CVs/Candidatos, Búsquedas, Entrevistas, Onboarding y Avisos),
    # agrupadas en tres frases al estilo de los demás tiles (descripción de una
    # oración + 3 capacidades). Indicadores NO se menciona mientras no esté
    # publicado en main: este dict es presentación pura, nunca una promesa de
    # funcionalidad (MISMO criterio que el resto de las entradas).
    "human-capital": {
        "eyebrow": None,
        "title": "Capital Humano",
        "accent": "Selección e Incorporación",
        "description": (
            "Un espacio para gestionar el ciclo de selección: banco de CVs, "
            "búsquedas, entrevistas e incorporación."
        ),
        "features": [
            "Banco de CVs y candidatos",
            "Búsquedas y entrevistas",
            "Onboarding y avisos",
        ],
    },
}


def brand_identity(key: str) -> dict:
    """Identidad visual de una vertical para superficies externas al Hub (correos), tomada de la MISMA tarjeta del selector: `title` (texto
    principal), `accent` (destacado en el acento institucional), `description` y `features` (capacidades). Sin tarjeta, del propio `Workspace`
    (label/subtitle), sin descripción ni capacidades."""
    workspace = next((ws for ws in _ALL_WORKSPACES if ws.key == key), None)
    card = WORKSPACE_CARD_COPY.get(key, {})
    return {"key": key,
            "title": card.get("title") or (workspace.label if workspace else ""),
            "accent": card.get("accent") or (workspace.subtitle if workspace else ""),
            "description": card.get("description") or "",
            "features": list(card.get("features") or [])}


def available_workspaces(user: dict | None) -> list[Workspace]:
    """Workspaces que `user` puede ver, en orden estable. `user` son los
    claims de sesión (o None si no hay sesión) — nunca un `Request`."""
    return [ws for ws in _ALL_WORKSPACES if ws.is_authorized(user)]


def platform_login_redirect(request: Request, *, next_path: str | None = None) -> RedirectResponse:
    """Redirige a `/platform` preservando el destino en `next` — 303
    (transición de autenticación, no canonicalización de recurso; ver
    docstring del módulo). `next_path` explícito permite al caller apuntar
    a la PÁGINA en vez del endpoint de acción/datos que disparó el
    redirect; sin él, usa `request.url.path` (+query) como hasta ahora."""
    if next_path is not None:
        target_next = next_path
    else:
        target_next = request.url.path
        if request.url.query:
            target_next += f"?{request.url.query}"
    return RedirectResponse(f"/platform?{urlencode({'next': target_next})}", status_code=303)
