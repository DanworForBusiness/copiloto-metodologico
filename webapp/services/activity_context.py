"""
activity_context.py
====================
Vocabulario y normalización de CONTEXTO de plataforma para la actividad
(Batch A1 — Shared Activity / Observability, Context Foundation; extendido en
el corrective A2 — Transversal Module + Event Semantics).

Jerarquía canónica congelada por el corrective:

    scope → area → module (opcional) → event

- ``scope`` — a qué ÁMBITO de Danwor Hub pertenece el evento. La autenticación
  pertenece a la PLATAFORMA, no a un workspace, por eso ``platform`` es un valor
  de primera clase junto a ``copilot`` y ``operations``.
- ``area`` — el ÁREA funcional dentro de ese ámbito (auth, methodology,
  assistant, academy, catalog, staffing).
- ``module`` — dimensión TRANSVERSAL y OPCIONAL de navegación (Copilot Y
  Operations por igual, ver `MODULE_LABELS`) — antes se asumía "Copilot-only",
  eso quedó retirado por el corrective.
- ``event`` — QUÉ pasó. No debe repetir DÓNDE pasó si scope/area/module ya lo
  representan (ver `normalize_event`) — por eso `staffing_overview_viewed`
  pasa a `module_viewed` con `module=assignments`, en vez de seguir
  prefijando el nombre del evento con la ubicación.

Este módulo es una HOJA: no importa ``webapp.main``, ni FastAPI, ni Jinja, ni
``activity_service``. El sink de bajo nivel (``activity_service.log_activity``)
NO importa este módulo: solo almacena scope/area/module tal cual el productor
los pasa explícitamente. La CLASIFICACIÓN histórica (para registros viejos que
no traen estos campos, o que usaban nombres de evento con la ubicación
embebida) vive acá y la consume el lector de analítica — funciones canónicas
únicas, sin esparcir mapeos por el dashboard.

NO es un framework de taxonomía genérica: los mapas históricos enumeran SOLO
eventos que productores reales emitieron alguna vez. Un evento desconocido cae
a un fallback EXPLÍCITO (``unknown``/``unknown``, `event_class="unknown"``) —
nunca se clasifica en silencio como Copiloto ni como uso genuino.

IMPORTANTE: la normalización de NOMBRE de evento (`normalize_event`) y la
clasificación uso/diagnóstico (`event_class`) son semántica de READ-MODEL
únicamente — nunca se reescriben los JSONL históricos persistidos.
"""
from __future__ import annotations

# --- scope (ámbito) ---
SCOPE_PLATFORM = "platform"
SCOPE_COPILOT = "copilot"
SCOPE_OPERATIONS = "operations"
SCOPE_UNKNOWN = "unknown"

# --- area (área funcional) ---
AREA_AUTH = "auth"
AREA_METHODOLOGY = "methodology"
AREA_ASSISTANT = "assistant"
AREA_ACADEMY = "academy"
AREA_CATALOG = "catalog"
AREA_STAFFING = "staffing"
AREA_UNKNOWN = "unknown"

# Clasificación de eventos HISTÓRICOS (JSONL previos a esta fase, sin scope/area).
# Fuente de la verdad: los call sites reales descubiertos (auth/modules/academy/
# assistant/catalog/wizard/telemetry beacon/staffing). NO agregar entradas sin un
# productor real detrás.
_HISTORICAL: dict[str, tuple[str, str]] = {
    # Plataforma — autenticación (NO es Copiloto aunque "/" sirviera históricamente
    # al Copiloto: el login pertenece a la plataforma).
    "login": (SCOPE_PLATFORM, AREA_AUTH),
    # Copiloto — metodología / navegación de módulos (incluye el beacon de cliente).
    "module_selected": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "module_continue": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "module_reached_end": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "nav_jump": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "link_external": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "disclosure_toggle": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "theme_change": (SCOPE_COPILOT, AREA_METHODOLOGY),
    # Copiloto — árboles de decisión / wizard (viven dentro de la metodología).
    "tree_started": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "tree_step": (SCOPE_COPILOT, AREA_METHODOLOGY),
    "tree_outcome": (SCOPE_COPILOT, AREA_METHODOLOGY),
    # Copiloto — asistente (chat + análisis de sección; section_analyze es el
    # nombre histórico reemplazado por assistant_query, misma área).
    "assistant_query": (SCOPE_COPILOT, AREA_ASSISTANT),
    "section_analyze": (SCOPE_COPILOT, AREA_ASSISTANT),
    # Copiloto — Academy.
    "academy_selected": (SCOPE_COPILOT, AREA_ACADEMY),
    # Copiloto — catálogo.
    "catalog_view": (SCOPE_COPILOT, AREA_CATALOG),
    "catalog_regenerate": (SCOPE_COPILOT, AREA_CATALOG),
    # Operaciones — Staffing (diagnósticos existentes + primer evento de uso).
    "staffing_overview_viewed": (SCOPE_OPERATIONS, AREA_STAFFING),
    "staffing_overview_config_incomplete": (SCOPE_OPERATIONS, AREA_STAFFING),
    "staffing_overview_error": (SCOPE_OPERATIONS, AREA_STAFFING),
}


# Labels visibles en español (los keys persistidos quedan estables en inglés).
# Fuente única del vocabulario para que servicio y templates no diverjan.
SCOPE_LABELS: dict[str, str] = {
    SCOPE_PLATFORM: "Plataforma",
    SCOPE_COPILOT: "Copiloto",
    SCOPE_OPERATIONS: "Operaciones",
    SCOPE_UNKNOWN: "Sin clasificar",
}
AREA_LABELS: dict[str, str] = {
    AREA_AUTH: "Autenticación",
    AREA_METHODOLOGY: "Metodología",
    AREA_ASSISTANT: "Asistente",
    AREA_ACADEMY: "Academy",
    AREA_CATALOG: "Catálogo",
    AREA_STAFFING: "Staffing",
    AREA_UNKNOWN: "Sin clasificar",
}


def scope_label(key: str) -> str:
    """Label visible del ámbito; devuelve el key crudo si no está mapeado (un
    valor nuevo aún sin label se ve explícito, nunca se esconde)."""
    return SCOPE_LABELS.get(key, key)


def area_label(key: str) -> str:
    return AREA_LABELS.get(key, key)


def classify(event: str) -> tuple[str, str]:
    """(scope, area) para un `event` HISTÓRICO sin campos explícitos. Fallback
    EXPLÍCITO a (unknown, unknown) si no hay evidencia — nunca asume Copiloto."""
    return _HISTORICAL.get((event or "").strip(), (SCOPE_UNKNOWN, AREA_UNKNOWN))


def context_of(record: dict) -> tuple[str, str]:
    """Normalización canónica: (scope, area) para CUALQUIER registro —
    prefiere los campos explícitos del registro (eventos nuevos); si faltan o
    están vacíos, clasifica por `event` (registros históricos). Único punto de
    verdad para que el lector no esparza lógica `event → scope/area`."""
    scope = str(record.get("scope") or "").strip()
    area = str(record.get("area") or "").strip()
    if scope and area:
        return scope, area
    fallback_scope, fallback_area = classify(str(record.get("event") or ""))
    return (scope or fallback_scope), (area or fallback_area)


# --- module (dimensión transversal opcional — corrective A2) ---
# Identificadores estables (nunca el label visible en español). Los módulos de
# Copiloto YA tienen su propio identificador estable (el `stem` del contenido,
# p. ej. "01_arquitectura_y_conectividad") y NO se tocan acá — este mapa de
# labels es SOLO para módulos compartidos nuevos que no vienen de un content
# pack (hoy, únicamente Asignaciones). Un módulo sin label mapeado muestra su
# key cruda (mismo criterio que scope_label/area_label).
MODULE_ASSIGNMENTS = "assignments"
MODULE_LABELS: dict[str, str] = {
    MODULE_ASSIGNMENTS: "Asignaciones",
}


def module_label(key: str) -> str:
    return MODULE_LABELS.get(key, key)


# --- nombres de evento CANÓNICOS (corrective A2) ---
# Regla congelada: el EVENTO describe QUÉ pasó, nunca DÓNDE — eso ya lo dicen
# scope/area/module. `module_viewed` reemplaza tanto al histórico Copilot
# `module_selected` como al histórico Staffing `staffing_overview_viewed`
# (mismo hecho: se entró a un módulo). `data_load_failed`/
# `data_load_diagnostics` reemplazan los nombres de Staffing con la ubicación
# repetida en el propio nombre (`staffing_overview_error`,
# `staffing_data_diagnostics`, etc.) — el contexto de FASE (carga inicial vs.
# historial) se preserva en `payload.operation`, no en el nombre del evento.
EVENT_MODULE_VIEWED = "module_viewed"
EVENT_DATA_LOAD_FAILED = "data_load_failed"
EVENT_DATA_LOAD_DIAGNOSTICS = "data_load_diagnostics"

# event → (nuevo event canónico, module inyectado o None, operation inyectada
# o None). SOLO para eventos que renombramos — un evento no listado acá pasa
# tal cual (incluidos los `_config_incomplete`, que NO forman parte de este
# corrective: no repiten ubicación de forma escalable-riesgosa y no fueron
# pedidos explícitamente). NUNCA se reescribe el JSONL persistido — esto es
# pura normalización de READ-MODEL, consumida por activity_analytics.
_EVENT_RENAME: dict[str, tuple[str, str | None, str | None]] = {
    # Copiloto — mismo hecho (vista de módulo), nombre nuevo. El module real
    # sigue viniendo del propio registro (payload.module para históricos,
    # el campo module de primera clase para nuevos) — nunca se inyecta acá.
    "module_selected": (EVENT_MODULE_VIEWED, None, None),
    # Operaciones · Staffing — Asignaciones. Antes la ubicación estaba en el
    # NOMBRE del evento; ahora en `module`, con `event` genérico + reusable.
    "staffing_overview_viewed": (EVENT_MODULE_VIEWED, MODULE_ASSIGNMENTS, None),
    "staffing_overview_error": (EVENT_DATA_LOAD_FAILED, MODULE_ASSIGNMENTS, "initial"),
    "staffing_historial_error": (EVENT_DATA_LOAD_FAILED, MODULE_ASSIGNMENTS, "history"),
    "staffing_data_diagnostics": (EVENT_DATA_LOAD_DIAGNOSTICS, MODULE_ASSIGNMENTS, "initial"),
    # `staffing_historial_diagnostics` no está en el corrective original palabra
    # por palabra, pero es el análogo exacto de `staffing_data_diagnostics`
    # para la fase de historial — dejarlo sin normalizar sería inconsistente
    # (diagnóstico de carga inicial normalizado, diagnóstico de historial no).
    "staffing_historial_diagnostics": (EVENT_DATA_LOAD_DIAGNOSTICS, MODULE_ASSIGNMENTS, "history"),
}


def normalize_event(raw_event: str) -> tuple[str, str | None, str | None]:
    """(event canónico, module inyectado o None, operation inyectada o None)
    para un `raw_event` — pasa tal cual (module=None, operation=None) si no
    está en el mapa de renombre. Nunca infiere de `payload`; eso lo hace el
    lector (activity_analytics), que sabe combinar esto con lo que el propio
    registro ya trae explícito (el registro explícito siempre gana)."""
    event, module, operation = _EVENT_RENAME.get((raw_event or "").strip(), (raw_event, None, None))
    return event, module, operation


# --- event_class (uso vs. diagnóstico — corrective A2, solo READ-MODEL) ---
# Clasificación sobre el EVENTO CANÓNICO (post-`normalize_event`), no sobre el
# raw — así `staffing_overview_error` (histórico) y `data_load_failed`
# (nuevo) clasifican igual, sin duplicar el mapa. `unknown` es el default
# explícito: un evento no reconocido nunca cae en "usage" en silencio.
EVENT_CLASS_USAGE = "usage"
EVENT_CLASS_DIAGNOSTIC = "diagnostic"
EVENT_CLASS_UNKNOWN = "unknown"

_USAGE_EVENTS: frozenset[str] = frozenset({
    "login",
    EVENT_MODULE_VIEWED,
    "module_continue",
    "module_reached_end",
    "nav_jump",
    "link_external",
    "disclosure_toggle",
    "theme_change",
    "tree_started",
    "tree_step",
    "tree_outcome",
    "assistant_query",
    "section_analyze",  # histórico, reemplazado por assistant_query — misma clase
    "academy_selected",
    "catalog_view",
    "catalog_regenerate",
})
_DIAGNOSTIC_EVENTS: frozenset[str] = frozenset({
    EVENT_DATA_LOAD_FAILED,
    EVENT_DATA_LOAD_DIAGNOSTICS,
    # Config incompleta no es "uso" (no es adopción real) — tampoco se pidió
    # normalizar su NOMBRE en este corrective, pero igual necesita una
    # clasificación explícita para no colar en las métricas de adopción.
    "staffing_overview_config_incomplete",
    "staffing_historial_config_incomplete",
})


def event_class(event: str) -> str:
    """Clasificación de READ-MODEL para métricas de adopción — nunca se
    persiste en el envelope. `event` debe ser el YA NORMALIZADO (canónico),
    no el raw — ver `normalize_event`."""
    key = (event or "").strip()
    if key in _USAGE_EVENTS:
        return EVENT_CLASS_USAGE
    if key in _DIAGNOSTIC_EVENTS:
        return EVENT_CLASS_DIAGNOSTIC
    return EVENT_CLASS_UNKNOWN
