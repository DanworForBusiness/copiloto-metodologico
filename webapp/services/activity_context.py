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

Este módulo es casi una HOJA: no importa ``webapp.main``, ni FastAPI, ni Jinja,
ni ``activity_service``; solo ``activity_catalog`` (la taxonomía central de
Métricas de Uso v2, de donde se re-exportan constantes y se derivan los sets
de clase/labels/renombres). El sink (``activity_service.log_activity``) NO
importa este módulo: usa el catálogo directo para su whitelist. La CLASIFICACIÓN histórica (para registros viejos que
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

# --- scope / area: re-exportados del catálogo central (Métricas de Uso v2) ---
# Las constantes viven en `activity_catalog` (hoja sin dependencias) para que
# el sink, el read-model, la analítica y los agregados compartan UNA fuente;
# acá se re-exportan con los mismos nombres de siempre (todo import existente
# `from activity_context import SCOPE_X` sigue funcionando sin cambios).
from webapp.services import activity_catalog as _catalog
from webapp.services.activity_catalog import (  # noqa: F401 — re-export público
    SCOPE_PLATFORM, SCOPE_COPILOT, SCOPE_OPERATIONS, SCOPE_COMMERCIAL, SCOPE_HUMAN_CAPITAL, SCOPE_UNKNOWN,
    AREA_AUTH, AREA_HUB, AREA_METHODOLOGY, AREA_ASSISTANT, AREA_ACADEMY, AREA_CATALOG, AREA_STAFFING,
    AREA_REPORTING, AREA_CRM, AREA_STRATEGY, AREA_PRESENTATIONS, AREA_CCHH, AREA_UNKNOWN,
    EVENT_MODULE_VIEWED, EVENT_PAGE_VIEW, EVENT_SECTION_VIEW, EVENT_DATA_LOAD_FAILED,
    EVENT_DATA_LOAD_DIAGNOSTICS, EVENT_CONFIGURATION_INCOMPLETE, EVENT_TOKEN_ACQUISITION_FAILED,
    MODULE_ASSIGNMENTS, VISIT_NAV_TYPES,
)

# Clasificación de eventos HISTÓRICOS (registros sin scope/area): derivada del
# catálogo (default de cada evento catalogado + los nombres crudos v1
# renombrados). Mismo contrato que antes: SOLO eventos con productor real, y
# un desconocido cae a (unknown, unknown) — nunca a Copiloto en silencio.
_HISTORICAL: dict[str, tuple[str, str]] = {
    name: _catalog.default_context(name)
    for name in (*_catalog.SPECS, *_catalog.RENAME)
}


# Labels visibles en español (los keys persistidos quedan estables en inglés).
# Fuente única del vocabulario para que servicio y templates no diverjan.
SCOPE_LABELS: dict[str, str] = {
    SCOPE_PLATFORM: "Plataforma",
    SCOPE_COPILOT: "Copiloto",
    SCOPE_OPERATIONS: "Operaciones",
    SCOPE_COMMERCIAL: "Comercial",
    SCOPE_HUMAN_CAPITAL: "Capital Humano",
    SCOPE_UNKNOWN: "Sin clasificar",
}
AREA_LABELS: dict[str, str] = {
    AREA_AUTH: "Autenticación",
    AREA_HUB: "Hub",
    AREA_METHODOLOGY: "Metodología",
    AREA_ASSISTANT: "Asistente",
    AREA_ACADEMY: "Academy",
    AREA_CATALOG: "Catálogo",
    AREA_STAFFING: "Staffing",
    AREA_REPORTING: "Reporting",
    AREA_CRM: "CRM",
    AREA_STRATEGY: "Estrategia",
    AREA_PRESENTATIONS: "Presentaciones",
    AREA_CCHH: "CCHH",
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
MODULE_LABELS: dict[str, str] = {
    MODULE_ASSIGNMENTS: "Asignaciones",
}


def module_label(key: str) -> str:
    """Label visible para un `module` (legacy stem, `"assignments"`, o ya
    canónico) — Fase 2 (Canonical Module Adoption): resuelve PRIMERO contra
    el registro canónico (`module_registry`, dueño único de la identidad de
    módulo — ver "Registry Ownership" en HANDOFF), que ya sabe labelear
    tanto Copiloto (reusa `content_service.title`) como Operations. Cae a
    `MODULE_LABELS`/la key cruda solo si el registro no reconoce la key —
    fallback seguro, nunca rompe ante un módulo desconocido. NO se borra
    `MODULE_LABELS` (`_EVENT_RENAME` de más abajo sigue necesitando
    `MODULE_ASSIGNMENTS` como valor histórico inyectado) — solo deja de ser
    la PRIMERA fuente de verdad."""
    from webapp.services import module_registry
    resolved = module_registry.resolve(key) if key else None
    if resolved:
        return resolved.visible_label
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
# (EVENT_MODULE_VIEWED / EVENT_DATA_LOAD_* / EVENT_CONFIGURATION_INCOMPLETE /
# EVENT_TOKEN_ACQUISITION_FAILED: re-exportados del catálogo, ver arriba.)

# event → (nuevo event canónico, module inyectado o None, operation inyectada
# o None). Ahora vive en el catálogo (`activity_catalog.RENAME`) como
# compatibilidad de LECTURA v1; acá queda el mismo nombre por compatibilidad.
# NUNCA se reescribe el JSONL persistido.
_EVENT_RENAME: dict[str, tuple[str, str | None, str | None]] = dict(_catalog.RENAME)


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

# Derivados del catálogo central (`activity_catalog.SPECS[*].cls`). Siguen
# siendo sets de módulo (no funciones) para que `event_class` lea el global —
# hay tests que extienden `_USAGE_EVENTS` con monkeypatch.
# v2: los 11 eventos que v1 escribía sin clasificar (retiros de tiles,
# sync de APIs, assessment, conectividad, publicación, contratos gobernados)
# pasan a `usage`; `page_view`/`section_view` entran como navegación.
_USAGE_EVENTS: frozenset[str] = _catalog.events_by(cls=_catalog.CLS_USAGE)
_DIAGNOSTIC_EVENTS: frozenset[str] = _catalog.events_by(cls=_catalog.CLS_DIAGNOSTIC)


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


# --- labels visibles de evento (USAGE DASHBOARD SEMANTIC CLEANUP) ---
# Mismo patrón que MODULE_LABELS/module_label: una key/value chica, sin
# framework de localización. El dashboard de uso nunca debe mostrar la key
# técnica cuando existe un label — solo cubre eventos de USO (los de
# diagnóstico no se renderizan en este dashboard, ver
# activity_analytics.build_summary). Un evento sin entrada cae a su nombre
# canónico crudo — fallback seguro, nunca rompe la UI ante un evento nuevo
# todavía sin label.
EVENT_LABELS: dict[str, str] = {
    # Solo eventos de USO (los de diagnóstico no se renderizan en este
    # dashboard). Fuente: `activity_catalog.SPECS[*].label`.
    name: _catalog.SPECS[name].label for name in sorted(_USAGE_EVENTS)
}


def event_label(event: str) -> str:
    return EVENT_LABELS.get(event, event)


# ── Read-model v2 (Métricas de Uso v2 — fase 1) ──────────────────────────────
# Todo registro (v1 histórico o v2) se lleva EN MEMORIA a la misma forma v2
# antes de agregarse. NUNCA se reescribe el JSONL: v1 se queda como está en
# disco/Spaces, y la compatibilidad vive solo en esta lectura.
#
# v1 = registro sin campo `v` (envelope `ts_utc,event,user,name,scope?,area?,
# module?,payload`). v2 = `v: 2` (envelope `id,ts_utc,kind,event,user,
# vertical,scope,area,module?,section?,surface?,attrs`).

# Eventos v1 que llevaban el módulo DENTRO del payload (`module_selected` →
# payload.module; el resto → payload.stem). Se evalúa sobre el nombre CRUDO.
_LEGACY_MODULE_KEY_EVENTS = frozenset({"module_selected", "section_analyze", "assistant_query", "tree_started"})


def legacy_payload_module(raw_event: str, payload: dict) -> str:
    if raw_event not in _LEGACY_MODULE_KEY_EVENTS or not isinstance(payload, dict):
        return ""
    return str(payload.get("module") or payload.get("stem") or "").strip()


def normalize_record(raw: dict) -> dict | None:
    """Registro crudo (v1 o v2) → forma v2 normalizada en memoria, o None si
    no es un evento (sin nombre). Campos:

    `v, id, ts_utc, kind, event (canónico), raw_event, user, name, vertical,
    scope, area, module (canónico vía module_registry si resuelve), raw_module,
    section, surface, attrs (SIEMPRE filtrados por la whitelist del catálogo),
    event_class, legacy_payload`.

    `legacy_payload` es el payload v1 CRUDO (o None para v2). Existe SOLO para
    el panel "Historia de preguntas" del dashboard actual, que muestra las
    conversaciones históricas v1 tal como se guardaron; ningún agregado nuevo
    lo lee — los agregados usan `attrs`, así que el texto libre de v1
    (pregunta/respuesta) nunca llega a un número nuevo.

    `name` (display) en v2 sale de `attrs.display_name` del `login` — el
    envelope v2 ya no repite el nombre en cada línea."""
    if not isinstance(raw, dict):
        return None
    raw_event = str(raw.get("event") or "").strip()
    if not raw_event:
        return None
    is_v2 = raw.get("v") == 2
    canonical_event, injected_module, injected_operation = normalize_event(raw_event)
    scope, area = context_of(raw)
    if is_v2:
        source = raw.get("attrs") if isinstance(raw.get("attrs"), dict) else {}
        legacy_payload = None
        name = str(source.get("display_name") or "").strip() if canonical_event == "login" else ""
        section = str(raw.get("section") or "").strip()
    else:
        source = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        legacy_payload = source
        name = str(raw.get("name") or "").strip()
        section = str(source.get("section") or "").strip() if isinstance(source.get("section"), str) else ""
    attrs = _catalog.filter_attrs(canonical_event, source)
    if injected_operation and not attrs.get("operation"):
        attrs["operation"] = injected_operation
    explicit_module = str(raw.get("module") or "").strip()
    raw_module = explicit_module or injected_module or ("" if is_v2 else legacy_payload_module(raw_event, source))
    module = raw_module
    if raw_module:
        from webapp.services import module_registry
        resolved = module_registry.resolve(raw_module)
        if resolved:
            module = resolved.module_id
    spec = _catalog.spec_of(canonical_event)
    return {
        "v": 2 if is_v2 else 1,
        "id": str(raw.get("id") or "") if is_v2 else "",
        "ts_utc": str(raw.get("ts_utc") or ""),
        "kind": (str(raw.get("kind") or "") if is_v2 else "") or (spec.kind if spec else _catalog.KIND_UNKNOWN),
        "event": canonical_event,
        "raw_event": raw_event,
        "user": str(raw.get("user") or "").strip(),
        "name": name,
        "vertical": _catalog.vertical_of(scope),
        "scope": scope,
        "area": area,
        "module": module,
        "raw_module": raw_module,
        "section": section,
        "surface": (str(raw.get("surface") or "") if is_v2 else "") or (spec.surface if spec else ""),
        "attrs": attrs,
        "event_class": event_class(canonical_event),
        "legacy_payload": legacy_payload,
    }


def is_visit(record: dict) -> bool:
    """¿Este registro NORMALIZADO es una visita?

    - v2: `page_view` con `nav_type` en navigate/back_forward/prerender.
      `reload` NO es visita (es la misma vista recargada). Se cuenta por
      `view_id` DISTINTO en la agregación, así que un beacon duplicado nunca
      suma dos veces.
    - v1: `module_viewed` (incluye los crudos `module_selected` y
      `staffing_overview_viewed`, ya renombrados). Era la única señal de
      visita que existía; se cuenta por evento (v1 no tiene view_id).
    - v2 `module_viewed` NO es visita: el servidor lo sigue emitiendo en esta
      fase, pero la visita v2 es `page_view` — contarlo también duplicaría.
    """
    event = record.get("event")
    if record.get("v") == 2:
        return event == EVENT_PAGE_VIEW and (record.get("attrs") or {}).get("nav_type") in VISIT_NAV_TYPES
    return event == EVENT_MODULE_VIEWED
