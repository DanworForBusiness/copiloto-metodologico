"""
activity_context.py
====================
Vocabulario y normalización de CONTEXTO de plataforma para la actividad
(Batch A1 — Shared Activity / Observability, Context Foundation).

Dos dimensiones aditivas sobre el envelope de ``activity_service``:

- ``scope`` — a qué ÁMBITO de Danwor Hub pertenece el evento. La autenticación
  pertenece a la PLATAFORMA, no a un workspace, por eso ``platform`` es un valor
  de primera clase junto a ``copilot`` y ``operations``.
- ``area`` — el ÁREA funcional dentro de ese ámbito (auth, methodology,
  assistant, academy, catalog, staffing).

`event` sigue siendo el identificador de acción; scope/area son contexto.

Este módulo es una HOJA: no importa ``webapp.main``, ni FastAPI, ni Jinja, ni
``activity_service``. El sink de bajo nivel (``activity_service.log_activity``)
NO importa este módulo: solo almacena el scope/area que el productor le pasa
explícitamente. La CLASIFICACIÓN histórica (para registros viejos que no traen
estos campos) vive acá y la consume el lector de analítica — una única función
canónica, sin esparcir mapeos ``event → scope/area`` por el dashboard.

NO es un framework de taxonomía genérica: el mapa histórico enumera SOLO los
eventos que productores reales emitieron alguna vez. Un evento desconocido cae a
un fallback EXPLÍCITO (``unknown``/``unknown``) — nunca se clasifica en silencio
como Copiloto.
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
