"""
platform.py — Entrada de plataforma multi-workspace: "One Danwor".

`GET /onedanwor` es la entrada canónica user-facing de la plataforma:
- SIN sesión → renderiza la misma landing histórica
  (`webapp.routers.landing.render_landing`) con copy propia de Danwor Hub
  (`_ONEDANWOR_LANDING_COPY`, ver Danwor Hub Login Identity Alignment en
  HANDOFF — el nombre interno de ruta/batch "One Danwor" NO cambia, solo
  el copy visible) — mismo auth, mismo mecanismo de `next`, mismo
  template, sin segundo OAuth/session/token.
- CON sesión → resuelve workspaces disponibles (`webapp.workspaces`) y
  decide: 0 → estado de sin-acceso controlado (la autoridad sigue siendo
  server-side — no hay redirect-loop); 1 → redirect directo a su
  `home_url`; 2+ → selector rico de workspaces. Si `next` es válido y
  explícito, tiene precedencia sobre la resolución de workspace (deep-link
  intent siempre gana) — la autorización real se sigue verificando en el
  destino, esto es solo una decisión de navegación.

`GET /platform` es un alias de COMPATIBILIDAD — nunca vuelve a renderizar
la landing directamente: SIEMPRE redirige 307 a `/onedanwor`, preservando
`next` si es seguro (mismas reglas que ya usa `render_landing`). Evita que
existan dos puntos de entrada capaces de divergir.

Clasificación: PLATFORM / ENTRY HTTP. NO importa `webapp.main`, NO conoce
Graph, NO conoce `pack` más allá de resolverlo para pasarlo al helper
compartido de landing (mismo mecanismo que `/`/`/copilot`, para que el
`data-home` del botón de login funcione igual). Delega TODA la
disponibilidad de workspaces a `webapp.workspaces` — no duplica ninguna
regla de autorización acá.

`_WORKSPACE_CARD_COPY` es presentación PURA (eyebrow/título/acento/
descripción/features) para el selector — deliberadamente separada de
`Workspace` (`webapp/workspaces.py`), que sigue siendo la ÚNICA fuente de
identidad/home/autorización. Si un workspace no tiene entrada acá, el
selector simplemente no muestra esos campos ricos (no rompe).
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp.routers.landing import render_landing
from webapp.services import auth_service
from webapp.templating import templates
from webapp.workspaces import available_workspaces

router = APIRouter()

# Copy visible de "Danwor Hub" — identidad del paraguas de plataforma
# (naming interno "platform"/"onedanwor" sin cambios; ver Danwor Hub Login
# en HANDOFF). `hub_brand_lockup: True` reemplaza el kicker/wordmark
# histórico de dos líneas por el MISMO bloque canónico (kicker + ícono +
# "Danwor Hub") que ya renderiza el hero autenticado
# (`platform_selector.html`) — ver `partials/platform_topbar.html::
# hub_brand_lockup()`. `_COPILOT_LANDING_COPY` (landing.py) NO define esta
# clave — su wordmark histórico de dos líneas ("Copiloto Metodológico" /
# "Arquitecto Danwor") sigue intacto, sin tocar.
_ONEDANWOR_LANDING_COPY = {
    "title": "Danwor Hub · Danwor",
    "eyebrow_ink": "Una plataforma",
    "eyebrow_gold": "Todos los servicios Danwor",
    "hub_brand_lockup": True,
    "brand_description": (
        "Un punto de encuentro donde se conectan necesidades, capacidades, "
        "conocimiento y operación."
    ),
    "feature_list": [
        "Un único acceso con tu cuenta Danwor",
        "Espacios de trabajo según tu perfil",
        "Navegación integrada entre servicios",
    ],
    "login_sub_lines": [
        "Ingresá con tu cuenta Danwor para acceder a los espacios de trabajo "
        "disponibles para vos."
    ],
}

# Presentación rica del selector, keyed por `Workspace.key`. Describe el
# ALCANCE del workspace (no una lista de módulos ya implementados) — ver
# docstring del módulo. `eyebrow` queda en `None` a propósito (revisión
# visual Danwor Hub, ver HANDOFF): "Data & AI for Business" repetido arriba
# de cada card era redundante una vez que la jerarquía visual del hero ya
# lo comunica. La capacidad de eyebrow por card SIGUE disponible — el
# template conserva `{% if card.meta.eyebrow %}` — esto es una decisión de
# CONTENIDO (ningún valor todavía), no una remoción de la feature.
_WORKSPACE_CARD_COPY = {
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
}


def _resolve_onedanwor(request: Request, pack: str, next: str):
    user = auth_service.session_user(request)
    if not user:
        selected = resolve_pack(pack)
        # default_home="/onedanwor": sin `next` explícito, tras el login el
        # browser vuelve a ESTE mismo endpoint (ahora autenticado), que
        # recién ahí resuelve qué workspace(s) corresponden.
        return render_landing(
            request, selected, next, default_home="/onedanwor", copy=_ONEDANWOR_LANDING_COPY,
        )

    safe_next = next if next.startswith("/") and not next.startswith("//") else ""
    if safe_next:
        return RedirectResponse(safe_next, status_code=307)

    available = available_workspaces(user)
    if len(available) == 1:
        return RedirectResponse(available[0].home_url, status_code=307)
    if not available:
        return templates.TemplateResponse(request=request, name="platform_no_access.html", context={})
    # Ojo: la clave NO puede llamarse "copy" — `dict` ya tiene un método
    # `.copy()`, y Jinja resuelve `card.copy` como atributo (ese método)
    # ANTES de probar `card["copy"]`, dejando el diccionario real
    # inalcanzable por dot-notation. "meta" evita la colisión.
    cards = [{"workspace": ws, "meta": _WORKSPACE_CARD_COPY.get(ws.key, {})} for ws in available]
    return templates.TemplateResponse(
        request=request, name="platform_selector.html", context={"cards": cards},
    )


@router.get("/onedanwor", response_class=HTMLResponse, include_in_schema=False)
def onedanwor_entry(request: Request, pack: str = Query(DEFAULT_PACK), next: str = Query("")):
    return _resolve_onedanwor(request, pack, next)


@router.get("/platform", include_in_schema=False)
def platform_compat_redirect(next: str = Query("")):
    """Alias de compatibilidad — SIEMPRE 307 a `/onedanwor`, preservando
    `next` si es un path relativo seguro (mismas reglas de `render_landing`).
    Nunca renderiza landing/selector directamente: un solo punto de entrada
    real evita que ambos diverjan.

    GET-only a propósito: `/platform` y `/onedanwor` son superficies de
    NAVEGACIÓN, no endpoints de acción/datos. Un POST no-autenticado hacia
    una acción (p. ej. `/operaciones/staffing/asignaciones/data`) nunca
    debería reaparecer acá como POST — `workspaces.platform_login_redirect`
    resuelve esa transición con 303 (no 307), que fuerza GET en el
    siguiente hop. Ver su docstring."""
    safe_next = next if next.startswith("/") and not next.startswith("//") else ""
    target = f"/onedanwor?{urlencode({'next': safe_next})}" if safe_next else "/onedanwor"
    return RedirectResponse(target, status_code=307)
