"""
hub_metrics_access.py — Access policy de Usage Metrics como capacidad de HUB
(owner ticket "HUB METRICS — CENTRALIZE ACCESS AND ENABLE BY VERTICAL").

Regla: `can_access_hub_metrics(user)` es la frontera de seguridad REAL de
Usage Metrics — deliberadamente INDEPENDIENTE de cualquier autorización de
vertical (Copiloto/Operations/Comercial). Dos preguntas ortogonales (owner
§1):

1. "¿puede este usuario entrar al vertical?" — autorización EXISTENTE de ESE
   vertical (``operations_access.is_operations_authorized``,
   ``commercial_access`` cuando exista, o la admisión propia del admin de
   Copiloto). Esta política NO se toca ni se debilita acá.
2. "¿puede este usuario ver Usage Metrics?" — acá, vía
   ``HUB_METRICS_ALLOWED_USERS``. Entrar a un vertical NUNCA implica ver sus
   métricas — nunca se acoplan una lista con otra.

CONTRATO DE MIGRACIÓN (owner §3), backward-compatible con el comportamiento
histórico (Usage Metrics = APP_OWNER-only):
- ``HUB_METRICS_ALLOWED_USERS`` ausente/vacío ⇒ preserva el comportamiento
  histórico: solo el dueño de la app (``webapp.access.owner_emails``).
- ``HUB_METRICS_ALLOWED_USERS`` configurado ⇒ se vuelve la ÚNICA autoridad;
  NUNCA hay un bypass oculto de APP_OWNER una vez configurada la lista
  explícita — si el dueño necesita seguir viendo Metrics, su identidad debe
  estar en ``HUB_METRICS_ALLOWED_USERS`` como cualquier otro usuario
  (auditable en config, MISMO criterio que ``operations_access.py`` ya
  documenta para ``OPERATIONS_ALLOWED_USERS``/APP_OWNER).

``can_access_hub_metrics(user)`` es el predicado PURO (sin ``Request``) —
reusado por: el enforcement server-side del propio Usage Metrics, y el
descubrimiento de navegación de CUALQUIER vertical que lo exponga (Operations
hoy; Comercial cuando ``HUB_METRICS_ENABLED_VERTICALS`` lo incluya) — LA
MISMA regla en todos, nunca dos políticas separadas ni un allowlist por
vertical. ``can_access_hub_metrics_request(request)`` es el wrapper HTTP —
MISMO patrón que ``operations_access.is_operations_authorized``.

Deliberadamente NO exige admisión BASE a la app (``auth_service.is_authorized``)
como sí hace ``operations_access``/``commercial_access``: un caller de
Metrics SIEMPRE corre DESPUÉS del gate del vertical que lo aloja (el admin de
Copiloto ya exige su propia sesión/owner; Operations ya exige
``is_operations_authorized`` ANTES de llegar acá, ver §6 del ticket) — nunca
en su lugar, así que duplicar esa admisión acá sería redundante, no un
segundo gate real.

TERCERA pregunta, ortogonal a las dos de arriba (owner ticket "HUB METRICS —
ENFORCE VERTICAL DATA VISIBILITY"): una vez adentro de Metrics, ¿QUÉ DATOS
puede ver? ``metrics_visibility_for(user)`` reusa los MISMOS predicados de
vertical (``operations_access``/``commercial_access``) para resolver eso —
ver esa función más abajo.
"""
from __future__ import annotations

from fastapi import Request

from webapp import access, commercial_access, operations_access
from webapp.services import auth_service
from webapp.services.activity_context import (
    AREA_ACADEMY, AREA_ASSISTANT, AREA_AUTH, AREA_CATALOG, AREA_CRM,
    AREA_METHODOLOGY, AREA_REPORTING, AREA_STAFFING, AREA_UNKNOWN,
    SCOPE_COMMERCIAL, SCOPE_COPILOT, SCOPE_OPERATIONS, SCOPE_PLATFORM, SCOPE_UNKNOWN,
)
from config import HUB_METRICS_ALLOWED_USERS, HUB_METRICS_ENABLED_VERTICALS


def _hub_metrics_configured() -> bool:
    return bool((HUB_METRICS_ALLOWED_USERS or "").strip())


def can_access_hub_metrics(user: dict | None) -> bool:
    """True si `user` (claims de sesión, o None) puede ver Usage Metrics.
    Ver el contrato de migración en el docstring del módulo."""
    if not user:
        return False
    email = str(user.get("sub") or "").strip().lower()
    if not email:
        return False
    if not _hub_metrics_configured():
        # Sin lista explícita configurada -- preserva el comportamiento
        # HISTÓRICO (APP_OWNER-only), MISMA fuente que webapp.access ya usa
        # para el resto de sus decisiones de dueño.
        return email in access.owner_emails()
    return auth_service.is_hub_metrics_user(email)


def can_access_hub_metrics_request(request: Request) -> bool:
    """Wrapper HTTP — MISMO patrón que operations_access.is_operations_authorized."""
    return can_access_hub_metrics(auth_service.session_user(request))


def metrics_enabled_verticals() -> set[str]:
    """Slugs canónicos de vertical con Usage Metrics expuesto, normalizados
    (lower/strip, CSV) — NUNCA valida contra una lista cerrada de verticales
    conocidos acá (ese conocimiento vive en cada vertical, que decide si le
    interesa consultar este set; agregar un slug nuevo acá no habilita nada
    por sí solo sin que ESE vertical también lo consulte)."""
    raw = (HUB_METRICS_ENABLED_VERTICALS or "").strip()
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_vertical_metrics_enabled(vertical: str) -> bool:
    """True si `vertical` (slug canónico, p. ej. "operations") está listado
    en HUB_METRICS_ENABLED_VERTICALS — SOLO la superficie/flag, nunca
    reemplaza `can_access_hub_metrics`: un caller necesita AMBAS cosas
    (owner §6/§8) — la superficie habilitada Y el usuario autorizado."""
    vertical = (vertical or "").strip().lower()
    if not vertical:
        return False
    return vertical in metrics_enabled_verticals()


# ── VERTICAL DATA VISIBILITY (owner ticket "HUB METRICS — ENFORCE VERTICAL
# DATA VISIBILITY") ──────────────────────────────────────────────────────
# Tercera pregunta, ortogonal a las dos de arriba (owner §1): "¿puede este
# usuario ACCEDER al vertical?" ya lo deciden operations_access/
# commercial_access — acá se REUSAN esos MISMOS predicados puros (nunca un
# parser nuevo de OPERATIONS_ALLOWED_USERS/COMMERCIAL_ALLOWED_USERS, owner
# §3) para resolver QUÉ DATOS de Reacciones/Usage Metrics puede VER ese
# usuario, una vez que ya pasó `can_access_hub_metrics`. Compartido/no-
# vertical (platform/auth, TODO Copiloto, unknown) siempre visible — owner
# §9: "Metrics-authorized users may see Copiloto metrics regardless of
# whether they entered Metrics from Operations", sin exigir una allowlist de
# vertical adicional para Copiloto.
def metrics_visibility_for(user: dict | None) -> tuple[frozenset[str], frozenset[str]]:
    """(allowed_scopes, allowed_areas): el universo de datos que `user` puede
    ver DENTRO de Usage Metrics. Devuelto a `activity_analytics.build_summary`
    (vía webapp/main.py) para recortar la población de eventos ANTES de
    cualquier agregación — ver ese módulo para el punto de aplicación único.
    Nunca decide SI el usuario puede entrar a Metrics (eso es
    `can_access_hub_metrics`, chequeado por separado en cada caller)."""
    allowed_scopes = {SCOPE_PLATFORM, SCOPE_COPILOT, SCOPE_UNKNOWN}
    allowed_areas = {AREA_AUTH, AREA_METHODOLOGY, AREA_ASSISTANT, AREA_ACADEMY, AREA_CATALOG, AREA_UNKNOWN}
    if operations_access.is_operations_authorized_user(user):
        allowed_scopes.add(SCOPE_OPERATIONS)
        allowed_areas.update({AREA_REPORTING, AREA_STAFFING})
    if commercial_access.is_commercial_authorized_user(user):
        allowed_scopes.add(SCOPE_COMMERCIAL)
        allowed_areas.add(AREA_CRM)
    return frozenset(allowed_scopes), frozenset(allowed_areas)
