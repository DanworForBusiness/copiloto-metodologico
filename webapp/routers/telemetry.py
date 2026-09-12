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
from webapp.services.activity_service import is_verbose, log_verbose
from webapp.services.activity_context import SCOPE_COPILOT, SCOPE_OPERATIONS, SCOPE_COMMERCIAL, AREA_METHODOLOGY

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


@router.post("/telemetry/event", include_in_schema=False)
async def telemetry_event(request: Request):
    """Beacon liviano de interacción cliente (fin de módulo, uso del cue de
    continuar, saltos de nav). Solo registra en LOG_ACTIVITY=VERBOSE; en
    otro caso es no-op. Siempre responde 204 para no ensuciar la consola
    del navegador."""
    if not is_verbose():
        return Response(status_code=204)
    user = auth_service.session_user(request)
    if not user:
        return Response(status_code=204)
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=204)
    event = str((data or {}).get("event") or "").strip()
    if event in _TELEMETRY_EVENTS:
        # El contexto lo fija el servidor, NUNCA el cliente (scope/area/module
        # no existen como campos aceptados del body — se excluyen explícito
        # junto con event/module_id para que no colisionen con los keyword-only
        # de log_verbose). `module_id` SÍ es un campo aceptado, pero solo como
        # ENTRADA a `_resolve_beacon_origin` — el resultado resuelto es lo
        # único que se persiste. `None` (module_id inválido/no reconocido) ->
        # el beacon se descarta sin loguear nada, ver ATTRIBUTION HYGIENE.
        module_id = str((data or {}).get("module_id") or "").strip()
        origin = _resolve_beacon_origin(module_id)
        if origin is not None:
            scope, area, module = origin
            payload = {k: v for k, v in data.items() if k not in ("event", "scope", "area", "module_id")}
            log_verbose(event, user=user, scope=scope, area=area, module=module, **payload)
    return Response(status_code=204)
