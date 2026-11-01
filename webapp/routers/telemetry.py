"""
telemetry.py — beacon liviano de telemetría de interacción del cliente.

Segundo router extraído de ``webapp/main.py`` (Fase 2A del refactor). Primer
router respaldado por un servicio compartido (``activity_service``) + auth: su
única dependencia de dominio es la observabilidad, así que no arrastra
templates, singletons de runtime ni helpers de main.

Regla de dependencia: NO importa ``webapp.main`` (es main quien incluye este
router). Comportamiento preservado verbatim respecto del handler original
(best-effort, 204 siempre, gate VERBOSE, no-op sin sesión).

ORIGIN ATTRIBUTION (Hub Telemetry Origin Attribution gate, ver HANDOFF):
este beacon es TRANSVERSAL (`.section-index a` -> `nav_jump`, ver app.js,
ya lo usan Copiloto Y Estrategia Comercial por igual) — antes atribuía
CUALQUIER evento a `scope=copilot, area=methodology` sin importar de qué
página vino, así que un `nav_jump` de Estrategia quedaba mal clasificado
como Copiloto. El cliente puede mandar `module_id` (identidad ESTABLE de
`module_registry`, ver base.html `window.DW_MODULE_ID`) — el servidor lo
RESUELVE acá contra el registro canónico, nunca confía en un scope/area
mandado directo por el cliente (ese campo ni siquiera existe en el payload
aceptado, ver el filtro de `payload` más abajo, sin cambios respecto de
antes). Sin lista de módulos Estrategia/CRM/Academy acá: cualquier módulo
que `module_registry` ya reconozca se resuelve solo.

ATTRIBUTION HYGIENE (Hub Telemetry Invalid Module Context gate, ver
HANDOFF): un `module_id` AUSENTE/vacío (caller legacy que todavía no lo
manda) y un `module_id` PRESENTE pero que `module_registry` no reconoce
(desconocido, spoofeado, de un módulo dado de baja) NO son el mismo caso —
antes se colapsaban en el mismo fallback, lo que convertía un beacon
moderno malformado en actividad de Copiloto legítima. Ahora: legacy (sin
`module_id`) SÍ cae al fallback histórico exacto (Copiloto/Metodología, sin
`module`); inválido (`module_id` presente pero no resuelve) NO se loguea en
absoluto — el beacon se descarta silenciosamente (sigue respondiendo 204,
sin UI de error), nunca contamina la métrica de otro módulo.

TRUST BOUNDARY: `module_id` es metadata de atribución mandada por el
cliente — el servidor la valida contra `module_registry` antes de usarla
para nada; el cliente nunca controla `scope`/`area` directo (esos campos ni
existen en el payload aceptado). Este endpoint es telemetría de producto,
no una frontera de autorización: NO verifica si el usuario autenticado
tiene acceso al workspace del módulo resuelto (misma falta de chequeo que
tenía antes de este gate) -- solo exige sesión válida, igual que siempre.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from webapp.services import auth_service, module_registry
from webapp.services.activity_service import RESERVED_KEYS, is_activity_logging_enabled, is_verbose, log_activity, log_verbose
from webapp.services.activity_context import (
    AREA_ACADEMY, AREA_CATALOG, AREA_CCHH, AREA_CRM, AREA_HUB, AREA_METHODOLOGY, AREA_PRESENTATIONS,
    AREA_REPORTING, AREA_STAFFING, AREA_STRATEGY, EVENT_PAGE_VIEW,
    SCOPE_COMMERCIAL, SCOPE_COPILOT, SCOPE_HUMAN_CAPITAL, SCOPE_OPERATIONS, SCOPE_PLATFORM,
)
from webapp.services.activity_catalog import NAV_TYPES

router = APIRouter()

_TELEMETRY_EVENTS = {
    "module_reached_end", "module_continue",
    "nav_jump", "link_external", "disclosure_toggle", "theme_change",
}

# workspace (module_registry.ModuleDescriptor.workspace) -> scope de Activity
# (activity_context.SCOPE_*) — traducción chica y estable, MISMO criterio que
# hub_metrics_access._WORKSPACE_SCOPE (nunca importada de ahí: ese módulo es
# policy/autorización de Metrics, este es un sink transversal que no debe
# depender de él — misma dirección de dependencia que el resto de este
# archivo, que tampoco conoce Jinja/templates). Un workspace nuevo sin
# entrada acá cae al fallback histórico (Copiloto), nunca a un scope
# inventado.
_WORKSPACE_SCOPE = {
    "copilot": SCOPE_COPILOT,
    "operations": SCOPE_OPERATIONS,
    "commercial": SCOPE_COMMERCIAL,
}


def _resolve_beacon_origin(module_id: str) -> tuple[str, str, str | None] | None:
    """(scope, area, module) para un beacon, o `None` si `module_id` vino
    INVÁLIDO y el beacon debe descartarse sin loguear nada.

    Tres casos, ver ATTRIBUTION HYGIENE arriba:
    - `module_id` vacío (caller legacy, sin contexto) -> fallback histórico
      exacto (Copiloto/Metodología, sin `module`).
    - `module_id` presente y `module_registry` lo reconoce -> origen
      resuelto del registro canónico.
    - `module_id` presente pero NO reconocido (desconocido/spoofeado) ->
      `None` — el caller NUNCA debe loguearlo como Copiloto ni como
      cualquier otro origen inventado."""
    if not module_id:
        return SCOPE_COPILOT, AREA_METHODOLOGY, None
    resolved = module_registry.resolve(module_id)
    if resolved is None:
        return None
    return _WORKSPACE_SCOPE.get(resolved.workspace, SCOPE_COPILOT), resolved.area, resolved.module_id


# PAGE VIEW (Métricas de Uso v2): origen por RUTA para páginas que no exponen
# `DW_MODULE_ID` (inicio, catálogo, Reporting, CCHH, admin…). Prefijo más
# largo primero; mismo criterio de confianza que `module_id`: el cliente manda
# SOLO `location.pathname` y el servidor decide el contexto con esta tabla
# (el cliente nunca fija scope/area). Ruta no reconocida → Plataforma/Hub.
# CCHH se atribuye a su propia vertical (`human_capital`), que ningún viewer de
# Metrics tiene en su universo visible todavía — fail-closed hasta que se
# defina esa política (ver hub_metrics_access.metrics_visibility_for).
_PAGE_ORIGIN_BY_PREFIX: tuple[tuple[str, str, str], ...] = (
    ("/operaciones/reporting", SCOPE_OPERATIONS, AREA_REPORTING),
    ("/operaciones/staffing", SCOPE_OPERATIONS, AREA_STAFFING),
    ("/operations/staffing", SCOPE_OPERATIONS, AREA_STAFFING),
    ("/operaciones/busquedas", SCOPE_HUMAN_CAPITAL, AREA_CCHH),
    ("/operaciones/metricas", SCOPE_PLATFORM, AREA_HUB),
    ("/cchh", SCOPE_HUMAN_CAPITAL, AREA_CCHH),
    ("/human-capital", SCOPE_HUMAN_CAPITAL, AREA_CCHH),
    ("/comercial/estrategia", SCOPE_COMMERCIAL, AREA_STRATEGY),
    ("/comercial/presentaciones", SCOPE_COMMERCIAL, AREA_PRESENTATIONS),
    ("/comercial/componentes", SCOPE_COMMERCIAL, AREA_PRESENTATIONS),
    ("/presentaciones", SCOPE_COMMERCIAL, AREA_PRESENTATIONS),
    ("/comercial", SCOPE_COMMERCIAL, AREA_CRM),
    ("/academy", SCOPE_COPILOT, AREA_ACADEMY),
    ("/catalog", SCOPE_COPILOT, AREA_CATALOG),
    ("/governed-contracts", SCOPE_COPILOT, AREA_CATALOG),
    ("/modules", SCOPE_COPILOT, AREA_METHODOLOGY),
    ("/copilot", SCOPE_COPILOT, AREA_METHODOLOGY),
    ("/desafio", SCOPE_COPILOT, AREA_METHODOLOGY),
)
_MAX_PATH_LEN = 300


def _clean_path(raw) -> str:
    """Solo el pathname (sin query ni fragmento), acotado. El cliente ya manda
    `location.pathname`; esto es defensa por si llega otra cosa."""
    path = str(raw or "").split("?", 1)[0].split("#", 1)[0].strip()
    return path[:_MAX_PATH_LEN] if path.startswith("/") else ""


def _page_origin(path: str) -> tuple[str, str]:
    if path in ("", "/"):
        return SCOPE_COPILOT, AREA_METHODOLOGY  # "/" sirve históricamente al Copiloto
    for prefix, scope, area in _PAGE_ORIGIN_BY_PREFIX:
        if path == prefix or path.startswith(prefix + "/"):
            return scope, area
    return SCOPE_PLATFORM, AREA_HUB


def _log_page_view(user: dict, data: dict) -> None:
    """`page_view` v2: UNA por carga de documento (app.js), con `view_id`
    generado en el render (templating.dw_view_id). Se escribe con
    `log_activity` (no verbose): es la métrica básica de visitas. Sin
    idempotencia en memoria: la agregación cuenta `view_id` DISTINTOS, así
    que un beacon duplicado nunca suma dos visitas. `reload` se escribe pero
    no cuenta como visita (ver activity_context.is_visit)."""
    view_id = str(data.get("view_id") or "").strip()
    if not view_id or len(view_id) > 64 or not view_id.isalnum():
        return
    nav_type = str(data.get("nav_type") or "").strip()
    if nav_type not in NAV_TYPES:
        nav_type = "unknown"
    path = _clean_path(data.get("path"))
    module_id = str(data.get("module_id") or "").strip()
    module = None
    if module_id:
        origin = _resolve_beacon_origin(module_id)
        if origin is None:
            return  # module_id inválido: MISMA higiene que el resto de los beacons
        scope, area, module = origin
    else:
        scope, area = _page_origin(path)
    log_activity(EVENT_PAGE_VIEW, user=user, scope=scope, area=area, module=module,
                 view_id=view_id, nav_type=nav_type, path=path)


@router.post("/telemetry/event", include_in_schema=False)
async def telemetry_event(request: Request):
    """Beacon liviano de interacción cliente (fin de módulo, uso del cue de
    continuar, saltos de nav) + `page_view` (Métricas de Uso v2). Los eventos
    finos solo registran en LOG_ACTIVITY=VERBOSE; `page_view` desde BASIC
    (métrica básica). Siempre responde 204 para no ensuciar la consola del
    navegador — nunca 500, aunque el body traiga claves reservadas."""
    if not is_activity_logging_enabled():
        return Response(status_code=204)
    user = auth_service.session_user(request)
    if not user:
        return Response(status_code=204)
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=204)
    if not isinstance(data, dict):
        return Response(status_code=204)
    event = str(data.get("event") or "").strip()
    if event == EVENT_PAGE_VIEW:
        _log_page_view(user, data)
        return Response(status_code=204)
    if not is_verbose():
        return Response(status_code=204)
    if event in _TELEMETRY_EVENTS:
        # El contexto lo fija el servidor, NUNCA el cliente. `RESERVED_KEYS`
        # saca del body todo lo que chocaría con los keyword-only de
        # log_verbose (event/user/scope/area/module/section…): antes un
        # beacon con `module` o `user` en el body levantaba TypeError → 500.
        # `module_id` SÍ es un campo aceptado, pero solo como ENTRADA a
        # `_resolve_beacon_origin` — el resultado resuelto es lo único que se
        # persiste. `None` (module_id inválido/no reconocido) -> el beacon se
        # descarta sin loguear nada, ver ATTRIBUTION HYGIENE. El resto del
        # body pasa por la whitelist del catálogo en el sink.
        module_id = str(data.get("module_id") or "").strip()
        origin = _resolve_beacon_origin(module_id)
        if origin is not None:
            scope, area, module = origin
            payload = {k: v for k, v in data.items() if k not in RESERVED_KEYS}
            log_verbose(event, user=user, scope=scope, area=area, module=module, **payload)
    return Response(status_code=204)
