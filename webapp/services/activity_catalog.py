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
import re
from dataclasses import dataclass

# --- scope (ámbito = vertical en el envelope v2) ---
SCOPE_PLATFORM = "platform"
SCOPE_COPILOT = "copilot"
SCOPE_OPERATIONS = "operations"
SCOPE_COMMERCIAL = "commercial"
# Capital Humano (CCHH): `page_view`/`section_view` de sus pantallas + las
# ACCIONES funcionales `cchh.*` (fase 2, sin PII: solo `outcome`, conteos y
# enums no identificatorios). Visible en Metrics SOLO para quien además pasa
# el predicado de acceso de CCHH (ver hub_metrics_access.metrics_visibility_for).
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

# `section_view.section`: slug DECLARATIVO del markup (`data-dw-section`).
# Regex estricta: minúsculas/dígitos/guion bajo, empieza con letra, acotado.
# Un slug que no matchea NO se escribe (el beacon se descarta): el cliente no
# puede meter texto libre en el campo `section` del envelope.
SECTION_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


def is_valid_section_slug(value) -> bool:
    return isinstance(value, str) and bool(SECTION_SLUG_RE.fullmatch(value))


# SECCIÓN POR RUTA (Métricas de Uso v2): las verticales cuyas pantallas son
# páginas completas SIN `module_id` (Capital Humano, Búsquedas de Operaciones,
# Reporting, Plataforma) no tienen módulo que desglosar — su estructura real
# son sus pantallas. Esta tabla, armada desde los decoradores REALES de los
# routers (human_capital.py, operations_busquedas.py, reporting.py,
# whats_new.py, platform.py, main.py), traduce la ruta de un `page_view` a un
# slug de sección del lado del servidor (telemetry.py). Prefijo más largo
# primero; una ruta con ids (detalle/edición de un candidato, una búsqueda…)
# cae en la sección PADRE: el slug nunca lleva ids, nombres ni query strings.
# (prefijo, vertical, slug, rótulo). Los eventos v1 históricos no se reescriben.
PAGE_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    # Capital Humano (human_capital.py)
    ("/cchh/candidatos", SCOPE_HUMAN_CAPITAL, "cchh_candidatos", "CCHH · Candidatos"),
    ("/cchh/consolidaciones", SCOPE_HUMAN_CAPITAL, "cchh_consolidaciones", "CCHH · Consolidaciones"),
    ("/cchh/solicitudes", SCOPE_HUMAN_CAPITAL, "cchh_solicitudes", "CCHH · Solicitudes"),
    ("/cchh/busquedas", SCOPE_HUMAN_CAPITAL, "cchh_busquedas", "CCHH · Búsquedas"),
    ("/cchh/entrevistas", SCOPE_HUMAN_CAPITAL, "cchh_entrevistas", "CCHH · Entrevistas"),
    ("/cchh/onboarding", SCOPE_HUMAN_CAPITAL, "cchh_onboarding", "CCHH · Onboarding"),
    ("/cchh/publicaciones", SCOPE_HUMAN_CAPITAL, "cchh_publicaciones", "CCHH · Publicaciones"),
    ("/cchh/indicadores", SCOPE_HUMAN_CAPITAL, "cchh_indicadores", "CCHH · Indicadores"),
    ("/cchh/catalogos", SCOPE_HUMAN_CAPITAL, "cchh_catalogos", "CCHH · Catálogos"),
    ("/cchh/diagnostico", SCOPE_HUMAN_CAPITAL, "cchh_diagnostico", "CCHH · SharePoint e importación de CVs"),
    ("/cchh", SCOPE_HUMAN_CAPITAL, "cchh_inicio", "CCHH · Inicio"),
    # Alias legados de CCHH (redirigen; se mapean igual por si llega el beacon).
    ("/human-capital/profiles", SCOPE_HUMAN_CAPITAL, "cchh_candidatos", "CCHH · Candidatos"),
    ("/human-capital", SCOPE_HUMAN_CAPITAL, "cchh_inicio", "CCHH · Inicio"),
    # Búsquedas de Operaciones (operations_busquedas.py): se atribuyen a CCHH (ver telemetry).
    ("/operaciones/busquedas", SCOPE_HUMAN_CAPITAL, "ops_busquedas", "Operaciones · Búsquedas"),
    # Reporting (reporting.py): Operaciones sin módulo propio.
    ("/operaciones/reporting/integral", SCOPE_OPERATIONS, "ops_reporting_integral", "Reporting · Integral"),
    # Plataforma (whats_new.py, platform.py, main.py)
    ("/whats-new", SCOPE_PLATFORM, "hub_novedades", "Plataforma · Novedades"),
    ("/operaciones/metricas", SCOPE_PLATFORM, "hub_metricas", "Plataforma · Métricas de uso"),
    ("/admin/activity", SCOPE_PLATFORM, "hub_metricas", "Plataforma · Métricas de uso"),
    ("/admin", SCOPE_PLATFORM, "hub_admin", "Plataforma · Administración"),
    ("/platform", SCOPE_PLATFORM, "hub_inicio", "Plataforma · Inicio"),
    ("/onedanwor", SCOPE_PLATFORM, "hub_inicio", "Plataforma · Inicio"),
)

# Verticales estructuradas por SECCIONES (sin módulos en `module_registry`):
# en el tablero muestran «Secciones y usuarios» y su desglose baja directo a
# secciones. Las demás (Copiloto, Operaciones, Comercial) siguen por módulo.
SECTION_VERTICALS = frozenset({SCOPE_HUMAN_CAPITAL, SCOPE_PLATFORM})


def page_section(path: str, scope: str) -> str | None:
    """Slug de sección de una ruta (ver `PAGE_SECTIONS`), o `None`. Solo
    devuelve una sección de la MISMA vertical a la que se atribuyó la vista
    (nunca cruza verticales). Ignora query/fragmento por las dudas."""
    clean = str(path or "").split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    for prefix, vertical, slug, _label in PAGE_SECTIONS:
        if clean == prefix or clean.startswith(prefix + "/"):
            return slug if vertical == scope else None
    return None


def page_section_inventory() -> dict[str, tuple[str, str]]:
    """{slug: (vertical, rótulo)} de las secciones por ruta (sin duplicados)."""
    out: dict[str, tuple[str, str]] = {}
    for _prefix, vertical, slug, label in PAGE_SECTIONS:
        out.setdefault(slug, (vertical, label))
    return out


# --- outcome (resultado normalizado de una operación larga o una acción) ---
OUTCOME_SUCCESS = "success"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"
OUTCOME_REJECTED = "rejected"
OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_PARTIAL, OUTCOME_FAILED, OUTCOME_REJECTED})
# Estados que YA producen los servicios (mayúsculas: conectividad/assessment/
# publicación; minúsculas: sincronización de APIs) → outcome. Un estado que no
# esté acá es `failed` (fail-closed: nunca se infla el éxito).
_STATE_OUTCOME = {
    "SUCCESS": OUTCOME_SUCCESS, "PARTIAL": OUTCOME_PARTIAL, "FAILED": OUTCOME_FAILED, "BLOCKED": OUTCOME_REJECTED,
    "ok": OUTCOME_SUCCESS, "nothing": OUTCOME_SUCCESS, "local_only": OUTCOME_SUCCESS, "rolled_back": OUTCOME_SUCCESS,
    "incomplete": OUTCOME_PARTIAL, "aborted": OUTCOME_REJECTED,
}


def outcome_of(state) -> str:
    """Estado crudo de un servicio → `outcome` normalizado (ver `_STATE_OUTCOME`)."""
    return _STATE_OUTCOME.get(str(state or "").strip(), OUTCOME_FAILED)


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
_RUN = ("state", "error_code", "duration_s", "operation_id", "outcome")
# Acciones de Capital Humano: SOLO resultado normalizado + conteos/enums no
# identificatorios. NUNCA nombres, emails, teléfonos, texto de CV, nombres de
# archivo, contenido de búsquedas ni valores de formulario (ver
# tests/webapp/test_activity_v2_instrumentation.py: claves prohibidas).
_CCHH = ("outcome",)
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
    # section_view: una vista de SECCIÓN cargada por HTMX dentro de una página
    # (detalle del Catálogo, Disponibilidad SAP, Tenants y ambientes…). La
    # emite app.js SOLO ante un disparo humano real (evento `isTrusted`); el
    # slug viaja en `section` (campo de primera clase, validado con
    # SECTION_SLUG_RE). `view_id` = la `page_view` que la contiene. Transversal:
    # scope/area/module los resuelve /telemetry/event. NO es visita.
    _spec(EVENT_SECTION_VIEW, KIND_NAVIGATION, SCOPE_PLATFORM, AREA_HUB, "Vista de sección",
          ("view_id", "path"), surface="partial"),
    # ── Copiloto · Metodología ──
    # module_viewed: SOLO compatibilidad de lectura v1 (y los v2 escritos
    # antes de la fase 2). Sin productor desde la fase 2: la visita con su
    # módulo la lleva `page_view` (`DW_MODULE_ID`).
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
    # catalog_view: SOLO compatibilidad de lectura v1 — sin productor desde la
    # fase 2 (lo reemplaza `section_view` con slug `catalog_*`).
    _spec("catalog_view", KIND_NAVIGATION, SCOPE_COPILOT, AREA_CATALOG, "Vista de catálogo",
          ("kind", "ref", "assoc", "lineage")),
    _spec("catalog_regenerate", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Regeneración de catálogo",
          ("ran", "ok", "outcome", "duration_s")),
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
          ("step", "state", "outcome", "duration_s")),
    _spec("catalog_assessment_generate", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Catálogo — informe de assessment",
          ("flow", "state", "error_code")),
    _spec("connectivity_inventory_run", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Conectividad — extracción de conexiones",
          ("client", "spaces", "inventory_status", "exhaustiveness", "extracted") + _RUN),
    _spec("connectivity_catalog_publish", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Conectividad — publicación al catálogo",
          ("error", "published", "durability") + _RUN),
    _spec("datasphere_assessment_run", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Assessment de modelo — ejecución",
          ("client", "space", "root_object", "stage") + _RUN),
    _spec("datasphere_assessment_refresh_catalog", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG,
          "Assessment de modelo — refresco de catálogo", _RUN),
    _spec("datasphere_publication_run", KIND_OUTCOME, SCOPE_COPILOT, AREA_CATALOG, "Publicación de modelo",
          ("client", "space", "root_object", "stage", "status", "durability", "change") + _RUN),
    # governed_contracts_*: SOLO compatibilidad de lectura — sin productor desde
    # la fase 2 (`section_view` governed_contracts_list/detail).
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
    # ── Capital Humano (fase 2) ── acciones funcionales, sin PII (ver `_CCHH`).
    _spec("cchh.profile_created", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — candidato creado",
          _CCHH + ("with_cv",)),
    _spec("cchh.profile_updated", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — candidato editado", _CCHH),
    _spec("cchh.profile_deleted", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — candidato dado de baja",
          _CCHH + ("documents_deleted",)),
    _spec("cchh.cv_uploaded", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — CV subido", _CCHH + ("origin",)),
    _spec("cchh.profiles_imported", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — importación de candidatos",
          _CCHH + ("total_rows", "created", "skipped", "failed", "invalid")),
    _spec("cchh.profiles_consolidated", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — perfiles consolidados",
          _CCHH + ("status",)),
    _spec("cchh.search_created", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — búsqueda creada", _CCHH),
    _spec("cchh.search_closed", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — búsqueda cerrada",
          _CCHH + ("finalized",)),
    _spec("cchh.search_requested", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH,
          "CCHH — solicitud de búsqueda desde Operaciones", _CCHH),
    _spec("cchh.candidate_added", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — candidato incorporado a búsqueda",
          _CCHH),
    _spec("cchh.stage_changed", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — cambio de etapa", _CCHH),
    _spec("cchh.interview_created", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — entrevista creada",
          _CCHH + ("origin",)),
    _spec("cchh.onboarding_item_toggled", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — ítem de onboarding",
          _CCHH + ("checked",)),
    _spec("cchh.publication_created", KIND_ACTION, SCOPE_HUMAN_CAPITAL, AREA_CCHH, "CCHH — publicación creada", _CCHH),
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


def feedback_verticals() -> frozenset[str]:
    """Verticales con un mecanismo de reacción (👍/👎) catalogado: las que
    tienen algún evento con `feedback_sentiment` en su whitelist. Derivado del
    catálogo, nunca una lista a mano: una vertical que sume feedback entra sola."""
    return frozenset(s.scope for s in _SPECS if "feedback_sentiment" in s.attrs)


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
