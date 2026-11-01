"""
activity_catalog.py
===================
Catálogo CENTRAL de eventos de actividad (Métricas de Uso v2 — fase 1).

Una sola tabla responde, para CADA evento que el Hub emite hoy (call sites
`log_activity`/`log_verbose` + las whitelists de los beacons de cliente
`/telemetry/event` y `/operaciones/reporting/integral/telemetry`):

- ``kind``   — QUÉ tipo de hecho es: ``navigation`` (entró/miró algo),
  ``action`` (hizo algo), ``outcome`` (resultado de un proceso, con éxito o
  falla) o ``session`` (inicio de sesión).
- ``scope``/``area`` por DEFAULT — se usan solo cuando el registro no trae los
  suyos (históricos v1 sin contexto, o un productor que no los pasa). El
  ``vertical`` del envelope v2 es el ``scope`` (ver `vertical_of`).
- ``cls``    — ``usage`` (adopción real, entra al dashboard) o ``diagnostic``
  (observabilidad operativa, nunca cuenta como uso).
- ``label``  — texto visible en español (los keys persistidos quedan estables
  en inglés).
- ``attrs``  — WHITELIST EXPLÍCITA de claves de payload que pueden llegar al
  JSONL. Todo lo que no esté listado se descarta en el sink ANTES de escribir
  (y otra vez al leer, para que un v1 con texto libre — p. ej. la pregunta y
  la respuesta del asistente — nunca llegue a un agregado nuevo).

Por qué una whitelist y no una blacklist: el log se persiste en Spaces y lo
leen dashboards; una blacklist por nombre de clave (lo que había hasta v1)
deja pasar cualquier campo nuevo que un productor agregue sin pensarlo. Con
whitelist, agregar un atributo es una decisión explícita en ESTE archivo.

Este módulo es una HOJA (no importa FastAPI, Jinja, ni otros services): lo
importan el sink (`activity_service`), el read-model (`activity_context`), la
analítica y los agregados. Por eso las constantes de scope/area viven acá y
`activity_context` las re-exporta (compatibilidad con todos los imports
existentes).

NO es un framework de taxonomía genérica: cada entrada tiene un productor
real detrás (verificado por grep al escribir esta fase). Un evento que no
esté acá se sigue escribiendo (no se pierde nada), pero con ``attrs={}`` y
clase ``unknown`` — nunca entra al dashboard en silencio.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# --- scope (ámbito = vertical en el envelope v2) ---
SCOPE_PLATFORM = "platform"
SCOPE_COPILOT = "copilot"
SCOPE_OPERATIONS = "operations"
SCOPE_COMMERCIAL = "commercial"
# Capital Humano (CCHH): hoy NO emite eventos de servidor; existe solo para
# atribuir `page_view` de sus pantallas. Ningún caller de Metrics lo incluye
# todavía en su universo visible (ver hub_metrics_access) — fail-closed: sus
# vistas quedan registradas pero no se muestran hasta que se decida la
# política de visibilidad de esa vertical.
SCOPE_HUMAN_CAPITAL = "human_capital"
SCOPE_UNKNOWN = "unknown"

# --- area (área funcional) ---
AREA_AUTH = "auth"
# Pantallas propias de la plataforma (inicio, novedades, admin) — solo para
# `page_view`; compartida como `auth` (visible para todo viewer de Metrics).
AREA_HUB = "hub"
AREA_METHODOLOGY = "methodology"
AREA_ASSISTANT = "assistant"
AREA_ACADEMY = "academy"
AREA_CATALOG = "catalog"
AREA_STAFFING = "staffing"
AREA_REPORTING = "reporting"
AREA_CRM = "crm"
# Estrategia Comercial: capability separada de CRM (ver commercial_strategy.py).
AREA_STRATEGY = "strategy"
# Comercial/Presentaciones (V1): capability propia, MISMO criterio que Estrategia.
AREA_PRESENTATIONS = "presentations"
AREA_CCHH = "cchh"
AREA_UNKNOWN = "unknown"

# --- kind ---
KIND_NAVIGATION = "navigation"
KIND_ACTION = "action"
KIND_OUTCOME = "outcome"
KIND_SESSION = "session"
KIND_UNKNOWN = "unknown"

# --- clase (uso vs. diagnóstico) ---
CLS_USAGE = "usage"
CLS_DIAGNOSTIC = "diagnostic"
CLS_UNKNOWN = "unknown"

# --- nombres canónicos que otros módulos referencian por constante ---
EVENT_MODULE_VIEWED = "module_viewed"
EVENT_PAGE_VIEW = "page_view"
EVENT_SECTION_VIEW = "section_view"
EVENT_DATA_LOAD_FAILED = "data_load_failed"
EVENT_DATA_LOAD_DIAGNOSTICS = "data_load_diagnostics"
EVENT_CONFIGURATION_INCOMPLETE = "configuration_incomplete"
EVENT_TOKEN_ACQUISITION_FAILED = "graph_token_acquisition_failed"

# Módulo histórico inyectado por el rename v1 de Staffing (ver RENAME).
MODULE_ASSIGNMENTS = "assignments"

# `page_view.nav_type` (Navigation Timing L2). `reload` se escribe pero NO
# cuenta como visita (ver activity_context.is_visit). `prerender` SÍ cuenta:
# app.js difiere el beacon hasta que la página prerenderizada se ACTIVA
# (`prerenderingchange`), así que un prerender que nunca se mostró no llega.
NAV_TYPES = frozenset({"navigate", "reload", "back_forward", "prerender"})
VISIT_NAV_TYPES = frozenset({"navigate", "back_forward", "prerender"})


@dataclass(frozen=True)
class EventSpec:
    name: str
    kind: str
    scope: str
    area: str
    cls: str
    label: str
    attrs: frozenset[str]
    surface: str = "server"


def _spec(name, kind, scope, area, label, attrs=(), *, cls=CLS_USAGE, surface="server") -> EventSpec:
    return EventSpec(name, kind, scope, area, cls, label, frozenset(attrs), surface)


# Atributos de los beacons de cliente: `stem` lo agrega `dwBeacon` siempre
# (app.js); `module_id` NO — se resuelve server-side y viaja como `module`.
_BEACON = ("stem",)
_FEEDBACK = ("feedback_sentiment",)
_TREE_PATH = ("run_id", "answer", "tree_id", "reached_leaf", "leaf", "score")
# `assistant_query`: SIN `question`/`answer` (texto libre del usuario y del
# LLM). La conversación vive en `assistant_store` (por `reply_id`); el log
# solo guarda metadatos + tamaños derivados (`chars_in`/`chars_out`, ver
# `derive_attrs`). `section` NO va acá: es campo de primera clase del envelope.
_ASSISTANT = (
    "stem", "action", "routed", "route_module", "route_heading", "intent",
    "bypass", "readonly", "ia_blocked", "llm", "reply_id", "conversation_id",
    "api_cost", "error", "chars_in", "chars_out",
)
_RUN = ("state", "error_code", "duration_s", "operation_id")
# Diagnóstico de carga de Staffing: `_flatten_staffing_diagnostics` aplana
# `{fuente: {métrica: valor}}` a `fuente_métrica`. Se enumeran las fuentes y
# métricas REALES de `StaffingRepository.last_diagnostics` (solo conteos/
# tiempos/booleanos) — una fuente nueva tiene que sumarse acá a propósito.
_STAFFING_SOURCES = ("asignaciones", "proyectos", "colaboradores", "colaboradores_identity", "cliente", "ausencias")
_STAFFING_METRICS = ("rows", "pages", "elapsed_ms", "filter_used", "fallback_used", "acquisition")
_STAFFING_DIAG = tuple(f"{s}_{m}" for s in _STAFFING_SOURCES for m in _STAFFING_METRICS) + ("total_elapsed_ms",)
# Diagnóstico de token de Graph: MISMA allowlist que `staffing._TOKEN_DIAGNOSTIC_FIELDS`.
_TOKEN_DIAG = (
    "stage", "msalErrorCode", "msalErrorName", "browserFamily", "platform", "cookieEnabled",
    "sessionStorageAvailable", "localStorageAvailable", "isSecureContext", "hadAccount", "route",
)

_SPECS: tuple[EventSpec, ...] = (
    # ── Plataforma ──
    _spec("login", KIND_SESSION, SCOPE_PLATFORM, AREA_AUTH, "Inicio de sesión", ("job_title", "display_name")),
    # page_view: métrica de VISITA v2 (una por carga de documento, ver app.js).
    # Transversal: scope/area/module los resuelve /telemetry/event.
    _spec(EVENT_PAGE_VIEW, KIND_NAVIGATION, SCOPE_PLATFORM, AREA_HUB, "Vista de página",
          ("view_id", "nav_type", "path"), surface="page"),
    # section_view: reservado para la fase de instrumentación (sin productor todavía).
    _spec(EVENT_SECTION_VIEW, KIND_NAVIGATION, SCOPE_COPILOT, AREA_METHODOLOGY, "Vista de sección",
          ("view_id",), surface="section"),
    # ── Copiloto · Metodología ──
    # module_viewed: transversal (Copiloto/Operaciones/Comercial) — el default
    # solo aplica a un registro sin contexto. En v2 NO es la métrica de visita
    # (eso es page_view); se mantiene hasta la fase de instrumentación.
    _spec(EVENT_MODULE_VIEWED, KIND_NAVIGATION, SCOPE_COPILOT, AREA_METHODOLOGY, "Vista de módulo", ("pack",)),
    _spec("module_continue", KIND_NAVIGATION, SCOPE_COPILOT, AREA_METHODOLOGY, "Continuó módulo",
          _BEACON + ("via", "href"), surface="client"),
    _spec("module_reached_end", KIND_NAVIGATION, SCOPE_COPILOT, AREA_METHODOLOGY, "Módulo completado",
          _BEACON, surface="client"),
    _spec("nav_jump", KIND_NAVIGATION, SCOPE_COPILOT, AREA_METHODOLOGY, "Salto de navegación",
          _BEACON + ("href",), surface="client"),
    _spec("link_external", KIND_NAVIGATION, SCOPE_COPILOT, AREA_METHODOLOGY, "Enlace externo",
          _BEACON + ("href", "cls"), surface="client"),
    _spec("disclosure_toggle", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Uso de desplegable",
          _BEACON + ("kind", "open"), surface="client"),
    _spec("theme_change", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Cambio de tema",
          _BEACON + ("theme",), surface="client"),
    _spec("tree_started", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Inicio de recorrido",
          ("stem", "tree_id", "tree_title", "panel")),
    _spec("tree_step", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Paso de recorrido", _TREE_PATH),
    _spec("tree_outcome", KIND_OUTCOME, SCOPE_COPILOT, AREA_METHODOLOGY, "Resultado de recorrido", _TREE_PATH),
    _spec("module.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Metodología — feedback de módulo", _FEEDBACK),
    # section.feedback: también lo emite Estrategia Comercial (con su scope);
    # `section` viaja como campo de primera clase del envelope.
    _spec("section.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Metodología — feedback de sección",
          ("section_title",) + _FEEDBACK),
    _spec("tree.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_METHODOLOGY, "Metodología — feedback de recorrido",
          ("run_id", "tree_id", "tree_title") + _FEEDBACK),
    # ── Copiloto · Asistente ──
    _spec("assistant_query", KIND_ACTION, SCOPE_COPILOT, AREA_ASSISTANT, "Consulta al asistente", _ASSISTANT),
    # Histórico (reemplazado por assistant_query) — misma whitelist.
    _spec("section_analyze", KIND_ACTION, SCOPE_COPILOT, AREA_ASSISTANT, "Análisis de sección", _ASSISTANT),
    _spec("assistant.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_ASSISTANT, "Asistente — feedback de respuesta",
          ("reply_id", "conversation_id") + _FEEDBACK),
    # ── Copiloto · Academy ──
    _spec("academy_selected", KIND_NAVIGATION, SCOPE_COPILOT, AREA_ACADEMY, "Vista de Academy", ("page", "pack")),
    _spec("academy.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_ACADEMY, "Academy — feedback de recorrido",
          ("academy_title",) + _FEEDBACK),
    # ── Copiloto · Catálogo ──
    _spec("catalog_view", KIND_NAVIGATION, SCOPE_COPILOT, AREA_CATALOG, "Vista de catálogo",
          ("kind", "ref", "assoc", "lineage")),
    _spec("catalog_regenerate", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Regeneración de catálogo", ("ran", "ok")),
    _spec("catalog.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — feedback de cierre",
          ("kind", "ref") + _FEEDBACK),
    _spec("catalog_section.feedback", KIND_ACTION, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — feedback de sección",
          ("cliente", "dominio", "section_slug") + _FEEDBACK),
    # Los 11 eventos que v1 escribía pero el dashboard descartaba (clase
    # `unknown`): son acciones/resultados REALES de uso del Catálogo.
    _spec("catalog_entry_retired", KIND_ACTION, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — tile retirado",
          ("entry_id", "cliente", "dominio", "files_requested", "files_deleted")),
    _spec("catalog_connection_retired", KIND_ACTION, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — conexiones retiradas",
          ("cliente", "connections")),
    _spec("catalog_apis_sync", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — sincronización de APIs",
          ("step", "state")),
    _spec("catalog_assessment_generate", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — informe de assessment",
          ("flow", "state", "error_code")),
    _spec("connectivity_inventory_run", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Conectividad — extracción de conexiones",
          ("client", "spaces", "inventory_status", "exhaustiveness", "extracted") + _RUN),
    _spec("connectivity_catalog_publish", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Conectividad — publicación al catálogo",
          ("error", "published", "durability", "rationale") + _RUN),
    _spec("datasphere_assessment_run", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Assessment de modelo — ejecución",
          ("client", "space", "root_object", "stage") + _RUN),
    _spec("datasphere_assessment_refresh_catalog", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG,
          "Assessment de modelo — refresco de catálogo", _RUN),
    _spec("datasphere_publication_run", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Publicación de modelo",
          ("client", "space", "root_object", "stage", "status", "durability", "change") + _RUN),
    _spec("governed_contracts_list", KIND_NAVIGATION, SCOPE_COPILOT, AREA_CATALOG, "Contratos gobernados — listado",
          ("kind", "ref")),
    _spec("governed_contracts_detail", KIND_NAVIGATION, SCOPE_COPILOT, AREA_CATALOG, "Contratos gobernados — detalle",
          ("kind", "ref")),
    # ── Operaciones · Staffing ──
    _spec("staffing.feedback", KIND_ACTION, SCOPE_OPERATIONS, AREA_STAFFING, "Asignaciones — feedback de reporte", _FEEDBACK),
    _spec(EVENT_DATA_LOAD_FAILED, KIND_OUTCOME, SCOPE_OPERATIONS, AREA_STAFFING, "Falla de carga de datos",
          ("operation", "error_type", "stage", "reason", "missing_fields", "ambiguous_fields", "http_status"),
          cls=CLS_DIAGNOSTIC),
    _spec(EVENT_DATA_LOAD_DIAGNOSTICS, KIND_OUTCOME, SCOPE_OPERATIONS, AREA_STAFFING, "Métricas de carga de datos",
          ("operation", "unmatched_count", "ambiguous_count") + _STAFFING_DIAG, cls=CLS_DIAGNOSTIC),
    _spec(EVENT_CONFIGURATION_INCOMPLETE, KIND_OUTCOME, SCOPE_OPERATIONS, AREA_STAFFING, "Configuración incompleta",
          ("operation",), cls=CLS_DIAGNOSTIC),
    _spec(EVENT_TOKEN_ACQUISITION_FAILED, KIND_OUTCOME, SCOPE_OPERATIONS, AREA_STAFFING, "Falla de token de Graph",
          ("operation",) + _TOKEN_DIAG, cls=CLS_DIAGNOSTIC),
    # ── Operaciones · Reporting Integral (beacon propio) ──
    # Solo los 5 que reporting.js emite de verdad: hierarchy_toggle/expand_all/
    # collapse_all/sort_change/refresh estaban en la whitelist pero ningún JS
    # los mandaba nunca (verificado por grep) — retirados en v2.
    _spec("reporting.tab_view", KIND_NAVIGATION, SCOPE_OPERATIONS, AREA_REPORTING, "Reporting — vista de pestaña",
          ("report_tab",), surface="client"),
    _spec("reporting.perspective_change", KIND_ACTION, SCOPE_OPERATIONS, AREA_REPORTING, "Reporting — cambio de perspectiva",
          ("report_tab", "perspective"), surface="client"),
    _spec("reporting.filter_change", KIND_ACTION, SCOPE_OPERATIONS, AREA_REPORTING, "Reporting — cambio de filtro",
          ("report_tab", "filter_name"), surface="client"),
    _spec("reporting.filter_clear", KIND_ACTION, SCOPE_OPERATIONS, AREA_REPORTING, "Reporting — limpiar filtros",
          ("report_tab",), surface="client"),
    _spec("reporting.cold_year_load", KIND_ACTION, SCOPE_OPERATIONS, AREA_REPORTING, "Reporting — carga de año histórico",
          ("report_tab", "load_scope"), surface="client"),
    _spec("reporting.feedback", KIND_ACTION, SCOPE_OPERATIONS, AREA_REPORTING, "Reporting — feedback de reporte",
          ("report_tab", "report_title") + _FEEDBACK),
    # ── Comercial ──
    _spec("strategy.saved", KIND_ACTION, SCOPE_COMMERCIAL, AREA_STRATEGY, "Estrategia — guardado exitoso", ("revision",)),
    _spec("presentation.copied", KIND_ACTION, SCOPE_COMMERCIAL, AREA_PRESENTATIONS, "Presentaciones — copia creada", ("revision",)),
    _spec("presentation.saved", KIND_ACTION, SCOPE_COMMERCIAL, AREA_PRESENTATIONS, "Presentaciones — guardado exitoso", ("revision",)),
    _spec("presentation.deleted", KIND_ACTION, SCOPE_COMMERCIAL, AREA_PRESENTATIONS, "Presentaciones — presentación eliminada"),
    _spec("component.created", KIND_ACTION, SCOPE_COMMERCIAL, AREA_PRESENTATIONS,
          "Presentaciones — componente duplicado como propio", ("revision",)),
    _spec("component.saved", KIND_ACTION, SCOPE_COMMERCIAL, AREA_PRESENTATIONS, "Presentaciones — componente editado", ("revision",)),
    _spec("component.deleted", KIND_ACTION, SCOPE_COMMERCIAL, AREA_PRESENTATIONS, "Presentaciones — componente eliminado"),
    _spec("commercial.feedback", KIND_ACTION, SCOPE_COMMERCIAL, AREA_CRM, "Comercial — feedback de capability",
          ("capability",) + _FEEDBACK),
    _spec("commercial.data_error", KIND_OUTCOME, SCOPE_COMMERCIAL, AREA_CRM, "Comercial — falla de datos",
          ("endpoint", "stage"), cls=CLS_DIAGNOSTIC),
)

SPECS: dict[str, EventSpec] = {s.name: s for s in _SPECS}

# Compatibilidad de LECTURA v1 (nunca se reescribe el JSONL): nombre crudo
# histórico → (evento canónico, module inyectado, operation inyectada). Los
# v1 llevaban la ubicación en el nombre (`staffing_overview_viewed`); el
# canónico describe QUÉ pasó y deja el DÓNDE a scope/area/module.
RENAME: dict[str, tuple[str, str | None, str | None]] = {
    "module_selected": (EVENT_MODULE_VIEWED, None, None),
    "staffing_overview_viewed": (EVENT_MODULE_VIEWED, MODULE_ASSIGNMENTS, None),
    "staffing_overview_error": (EVENT_DATA_LOAD_FAILED, MODULE_ASSIGNMENTS, "initial"),
    "staffing_historial_error": (EVENT_DATA_LOAD_FAILED, MODULE_ASSIGNMENTS, "history"),
    "staffing_data_diagnostics": (EVENT_DATA_LOAD_DIAGNOSTICS, MODULE_ASSIGNMENTS, "initial"),
    "staffing_historial_diagnostics": (EVENT_DATA_LOAD_DIAGNOSTICS, MODULE_ASSIGNMENTS, "history"),
    "staffing_overview_config_incomplete": (EVENT_CONFIGURATION_INCOMPLETE, MODULE_ASSIGNMENTS, "initial"),
    "staffing_historial_config_incomplete": (EVENT_CONFIGURATION_INCOMPLETE, MODULE_ASSIGNMENTS, "history"),
}

# Contexto por default de los nombres CRUDOS v1 renombrados (un v1 sin scope/
# area se clasifica por su nombre crudo, antes del rename).
_RAW_DEFAULTS: dict[str, tuple[str, str]] = {
    "module_selected": (SCOPE_COPILOT, AREA_METHODOLOGY),
    **{raw: (SCOPE_OPERATIONS, AREA_STAFFING) for raw in RENAME if raw.startswith("staffing_")},
}


def spec_of(event: str) -> EventSpec | None:
    """Spec del evento CANÓNICO (o crudo v1 renombrado); None si no está catalogado."""
    key = (event or "").strip()
    if key in SPECS:
        return SPECS[key]
    renamed = RENAME.get(key)
    return SPECS.get(renamed[0]) if renamed else None


def canonical(event: str) -> str:
    key = (event or "").strip()
    return RENAME.get(key, (key, None, None))[0]


def default_context(raw_event: str) -> tuple[str, str]:
    """(scope, area) por default de un nombre CRUDO; (unknown, unknown) si no hay evidencia."""
    key = (raw_event or "").strip()
    if key in _RAW_DEFAULTS:
        return _RAW_DEFAULTS[key]
    spec = SPECS.get(key)
    return (spec.scope, spec.area) if spec else (SCOPE_UNKNOWN, AREA_UNKNOWN)


def vertical_of(scope: str | None) -> str:
    """La vertical del envelope v2 ES el scope (platform/copilot/operations/
    commercial/human_capital). Se escribe aparte para que la fase de
    dashboard pueda filtrar por vertical sin re-derivar nada."""
    return (scope or "").strip() or SCOPE_UNKNOWN


def allowed_attrs(event: str) -> frozenset[str]:
    spec = spec_of(event)
    return spec.attrs if spec else frozenset()


def derive_attrs(event: str, payload: dict) -> dict:
    """Atributos DERIVADOS antes de filtrar: hoy solo `assistant_query`
    (y su histórico `section_analyze`), que convierte `question`/`answer` en
    tamaños (`chars_in`/`chars_out`) — así se conserva la señal de volumen
    sin persistir el texto."""
    if canonical(event) not in ("assistant_query", "section_analyze"):
        return {}
    out = {}
    if isinstance(payload.get("question"), str) and "chars_in" not in payload:
        out["chars_in"] = len(payload["question"])
    if isinstance(payload.get("answer"), str) and "chars_out" not in payload:
        out["chars_out"] = len(payload["answer"])
    return out


def filter_attrs(event: str, payload: dict | None) -> dict:
    """Payload → attrs: solo las claves de la whitelist del evento (más las
    derivadas). Evento no catalogado → {}. NO sanea valores (eso lo hace el
    sink); acá solo se decide QUÉ claves pueden existir."""
    if not isinstance(payload, dict):
        return {}
    allowed = allowed_attrs(event)
    if not allowed:
        return {}
    merged = {**payload, **derive_attrs(event, payload)}
    return {k: v for k, v in merged.items() if k in allowed}


def label_of(event: str) -> str:
    spec = spec_of(event)
    return spec.label if spec else event


def events_by(*, cls: str | None = None) -> frozenset[str]:
    return frozenset(s.name for s in _SPECS if cls is None or s.cls == cls)


def version() -> str:
    """Huella del catálogo: los agregados diarios la guardan en su firma, así
    un cambio de taxonomía (clase/kind/whitelist) invalida los agregados
    viejos en vez de dejar números calculados con reglas distintas."""
    blob = json.dumps(
        [(s.name, s.kind, s.scope, s.area, s.cls, sorted(s.attrs)) for s in _SPECS] + sorted(RENAME.items()),
        ensure_ascii=False,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def summary() -> dict[str, dict[str, int]]:
    """{scope: {kind: cantidad}} — documentación/auditoría del catálogo."""
    out: dict[str, dict[str, int]] = {}
    for s in _SPECS:
        bucket = out.setdefault(s.scope, {})
        bucket[s.kind] = bucket.get(s.kind, 0) + 1
    return out
