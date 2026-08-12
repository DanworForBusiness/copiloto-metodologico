"""
staffing_repository.py — Acceso a datos de Staffing en SharePoint (sitio
"Gestión"). Fase de aceleración post-Fase 2 (Batch 2/3; campos validados
contra Graph real en Batch 3).

Sabe DÓNDE viven las tres fuentes del primer slice de Staffing
(Asignaciones por proyecto / Proyectos / Colaboradores), cómo construir las
URLs de Graph, cómo pedirlas vía `core.microsoft365` y cómo unir las tres
listas por sus IDs de lookup de SharePoint. Devuelve una representación
normalizada y estable (`StaffingAllocationRecord`) — nunca el JSON crudo de
Graph (`{"id": ..., "fields": {...}}`) más allá de este módulo.

Clasificación: OPERATIONS / STAFFING DATA ACCESS. NOT PLATFORM / SHARED. NO
importa `webapp.main`, `fastapi`, templates ni nada de autenticación/sesión.
Cada operación de Graph recibe `access_token` explícito — este módulo NO lo
posee, no lo persiste, no lo loguea.

MAPEO DE CAMPOS — sin default de producción en el CÓDIGO, pero YA
VALIDADO contra el sitio Gestión real (Batch 3): los 8 internal names de
`StaffingFieldMapping` (`ColaboradorLookupId`, `ProyectoLookupId`,
`Horaspormes`, `Status`, `Fechainicio`, `FechaFin`, `Title`×2) se
confirmaron leyendo `/sites/{id}/lists/{id}/columns` con un token Graph
real — no son una inferencia por convención. Siguen sin default en
`config.py` (mismo mecanismo de la Fase 2 corrective pass: no hardcodear un
mapeo específico de un tenant como default de producción) — el entorno
local los provee vía `.env` (git-ignored, ver HANDOFF Batch 3). Si el
mapeo no está configurado, `StaffingRepository.get_allocations` sigue
fallando explícito, listando exactamente qué falta — construir el objeto
vacío no rompe el import de la app. Los tests inyectan su propio mapeo
fixture (ver `tests/webapp/test_staffing_repository.py`), estilísticamente
igual al real pero declarado como fixture, no como prueba del esquema.

CARGA INICIAL NO HISTÓRICA (Staffing Director Priority V2, ver HANDOFF):
`get_allocations` acepta `assignment_scope` ("operational" default |
"historical_inactive") — ver `_fetch_asignaciones` para el filtro Graph
(`$filter` + `Prefer` header) y su fallback automático a fetch completo +
filtro Python si Graph rechaza la query. **NO verificado contra el sitio
Gestión real** (sin credenciales Graph en este entorno) — el fallback
garantiza corrección sin importar el resultado real de esa verificación
pendiente.

DIAGNÓSTICO (pre-PR corrective, ver HANDOFF): cada `get_allocations` puebla
`self.last_diagnostics` — rows/pages/elapsed_ms por fuente (asignaciones/
proyectos/colaboradores/cliente) + `filter_used`/`fallback_used` para
asignaciones + `total_elapsed_ms`. NUNCA token/payload/PII — solo conteos y
tiempos. El caller (`StaffingService`/router) decide si loguearlo
(VERBOSE-gated, ver `webapp/routers/staffing.py`).

VACACIONES (Staffing Vacation Timeline Overlay, ver HANDOFF) — CONTRATO
FINAL, confirmado contra el schema real de Graph (owner live smoke #4,
autoritativo — el export offline XLSX, con labels Apellido/Nombre/Motivo/
Desde/Hasta/Estado, NUNCA fue el contrato real de la lista SharePoint, ver
HANDOFF): `get_vacations` lee la lista "Solicitud vacaciones" del MISMO
sitio Gestión, filtrando EXCLUSIVAMENTE `TipodeLicencia == "Vacaciones"`
AND `Approved is True` (booleano estricto — `False`/`None`/ausente
excluye; ningún otro Tipo de Licencia entra). Devuelve `consultant_name`
CRUDO (`fields["Colaborador"]`, un campo de TEXTO simple — NO lookup, NO
requiere resolver un ID de Colaboradores) — el join de identidad
determinístico contra el universo de Colaboradores de Staffing vive en
`StaffingService` (ver ahí, sin cambios: sigue siendo join por nombre
normalizado). `get_consultants` lee SOLO Colaboradores (id + nombre, sin
Asignaciones/Proyectos) — universo de identidad para ese join.

`¿Aprobado CCHH?` (`_x00bf_AprobadoCCHH_x003f_`, boolean) EXISTE en la
fuente real pero está DELIBERADAMENTE fuera del criterio de inclusión de
V1 — su rol de negocio (¿debe exigirse junto con `Approved`? ¿es
independiente?) no está confirmado; agregarlo ahora inventaría una regla.
Ver HANDOFF para el registro explícito de esta deuda conocida.

DISCOVERY DE CAMPOS EN RUNTIME (ver HANDOFF): a diferencia de
Asignaciones/Proyectos/Colaboradores (internal names vía `.env`, sin
default, requieren que un humano con acceso Graph los confirme una vez),
Vacaciones resuelve sus 5 internal names en RUNTIME contra
`/sites/{id}/lists/{id}/columns` — usando el MISMO `access_token` delegado
que ya llega en cada request. `resolve_vacation_field_mapping()` matchea
`displayName` EXACTO (whitespace normalizado, sin fuzzy, sin case-folding,
sin posición) contra los 5 labels REALES (`Colaborador`, `Fecha de
inicio`, `Fecha de finalización`, `Tipo de Licencia`, `¿Aprobado?`); si
falta alguno o hay ambigüedad (dos columnas con el mismo `displayName`),
levanta `VacationSchemaError` — nunca elige un candidato al azar.
`VacationSchemaError` NO es `RuntimeError` a propósito: es un problema de
schema/runtime de la fuente, NUNCA de configuración de deployment — el
router lo trata igual que cualquier otra falla de Graph (`data_load_
failed`/`graph_error`), nunca como `configuration_incomplete`. Solo el
List ID de Vacaciones (`SHAREPOINT_VACACIONES_LIST_ID`) sigue siendo
identidad de configuración — si el sitio/list ID faltan, `_require_site_
id()` sigue siendo el único `RuntimeError`/`configuration_incomplete`
real de esta fuente. La resolución exitosa se cachea in-process con TTL
(mismo patrón que `core/sharepoint_cases.py::_ttl_cache`, keyed por
fingerprint de token + site/list — NUNCA por el token crudo); fallas
(401/403/columna faltante/ambigua) NUNCA se cachean.

DIAGNÓSTICO SEGURO DE FALLAS DE GRAPH (ver HANDOFF): `_call_vacation_graph
(stage, fn)` envuelve TODA llamada Graph de Vacaciones (`stage="columns"`
para el discovery de schema, `stage="items"` para el fetch de filas) —
cualquier `HTTPError`/`URLError` de `urllib` se traduce a `VacationGraphError
(stage, status_code)`, NUNCA deja escapar la excepción cruda. `VacationSchemaError`
expone `stage`/`reason`/`missing_fields`/`ambiguous_fields` estructurados
— estos últimos dos SIEMPRE son labels del contrato semántico fijo, nunca
un internal name real. Ambos son seguros de loguear/devolver en la
respuesta HTTP tal cual. Las superficies TEMPORALES de discovery
(`available_columns`/`semantic_columns`, usadas para establecer este
contrato durante los smokes #2/#3) se retiraron una vez confirmado el
contrato final — ver HANDOFF.

Ambos (`get_vacations`/`get_consultants`)
fail-soft ante cualquier excepción — el router captura el error y omite
el overlay, Asignaciones nunca se rompe."""
from __future__ import annotations

import hashlib
import threading
import time
import urllib.error
from dataclasses import dataclass, fields as dataclass_fields
from typing import Callable
from urllib.parse import quote

import config
from core import microsoft365

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Staffing Director Priority V2 (ver HANDOFF) — carga inicial NO histórica.
# `assignment_scope="operational"` (default de `get_allocations`) excluye
# Inactivo del fetch de Asignaciones; `"historical_inactive"` pide
# EXCLUSIVAMENTE Inactivo (el delta que el dashboard carga una sola vez,
# lazy, cuando el usuario tilda el filtro Inactivo por primera vez — ver
# `webapp/routers/staffing.py::staffing_historial` y `app.js::staffingDashboard`).
# Ninguno de los dos modos toca Proyectos/Colaboradores/Cliente — son tablas
# de dimensión (gente/proyectos/clientes VIGENTES), no el registro de ~3
# años de Asignaciones que motiva este parámetro; se piden completas en
# ambos casos, sin necesidad de optimización ahí.
INACTIVE_STATUS_VALUE = "Inactivo"

# Graph puede rechazar `$filter` sobre una columna Choice no indexada del
# sitio SharePoint real ("Gestión") — este header es el mecanismo
# DOCUMENTADO por Microsoft para pedirle a Graph que lo intente igual
# (`HonorNonIndexedQueriesWarningMayFailRandomly`, más lento pero
# soportado). **Nunca verificado contra el sitio Gestión real desde este
# entorno** (sin credenciales Graph disponibles) — por eso `_fetch_asignaciones`
# abajo SIEMPRE envuelve el intento filtrado en un fallback automático a la
# lista completa + filtro en Python: si Graph rechaza la query igual con
# este header, Staffing nunca se rompe, solo pierde la optimización de
# volumen en ese request puntual.
_HISTORY_PREFER_HEADER = {"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"}

# Staffing Vacation Timeline Overlay (ver HANDOFF) — valor EXACTO de negocio
# confirmado contra el schema real (`TipodeLicencia` es una columna Choice
# de la lista, no un internal name que necesite config por tenant — mismo
# criterio que `INACTIVE_STATUS_VALUE` arriba). Junto con `Approved is True`
# (booleano estricto, ver `get_vacations`) es el ÚNICO par que habilita una
# fila para el timeline — cualquier otro Tipo de Licencia, o Approved
# False/None/ausente, queda fuera de V1.
VACATION_LEAVE_TYPE_VALUE = "Vacaciones"


#  Campos OPCIONALES de StaffingFieldMapping — enriquecimiento, no requisito
# de carga. Su ausencia NUNCA debe aparecer en `missing_fields()` (eso
# bloquearía TODO Staffing por una feature de presentación adicional). Ver
# `colaborador_disponibilidad` (Asignaciones Dashboard v1).
_OPTIONAL_MAPPING_FIELDS = frozenset({
    "colaborador_disponibilidad", "proyecto_empresa_lookup", "cliente_title",
})


@dataclass(frozen=True)
class StaffingFieldMapping:
    """Internal names de columna Graph para las 3 listas de Staffing. Los 8
    campos REQUERIDOS no tienen default — deben proveerse explícitos: en
    producción, confirmados contra el sitio Gestión real (`from_config()`,
    hoy vacío); en tests, por un fixture documentado como tal. Construir
    esta clase con valores vacíos NO falla (permite `from_config()` sin
    romper el import de la app); lo que falla es
    `StaffingRepository.get_allocations` si intenta usarla incompleta.

    `colaborador_disponibilidad` es OPCIONAL (ver `_OPTIONAL_MAPPING_FIELDS`)
    — enriquecimiento FT/PT del Asignaciones Dashboard v1, confirmado contra
    Graph en Batch 3 pero deliberadamente sin default de producción (mismo
    mecanismo que los 8 de arriba). Su ausencia NUNCA bloquea la carga de
    Staffing — degrada a `None`/"Sin dato", nunca a `RuntimeError`."""

    colaborador_lookup_id: str
    proyecto_lookup_id: str
    allocation: str
    status: str
    start_date: str
    end_date: str
    proyecto_title: str
    colaborador_title: str
    colaborador_disponibilidad: str = ""
    # Cliente (revisión PBI, ver HANDOFF) — OPCIONAL, mismo mecanismo que
    # `colaborador_disponibilidad`: confirmado contra Graph real (Proyectos
    # lookup real name "Empresa", `fieldValueSet` property "EmpresaLookupId"
    # → Cliente.Title), pero sin default de producción. Ambos deben estar
    # configurados para resolver cliente; si falta cualquiera, degrada a
    # client_id/client_name = None (nunca bloquea Staffing).
    proyecto_empresa_lookup: str = ""
    cliente_title: str = ""

    @classmethod
    def from_config(cls) -> "StaffingFieldMapping":
        return cls(
            colaborador_lookup_id=config.SHAREPOINT_ASIGNACIONES_FIELD_COLABORADOR_LOOKUP,
            proyecto_lookup_id=config.SHAREPOINT_ASIGNACIONES_FIELD_PROYECTO_LOOKUP,
            allocation=config.SHAREPOINT_ASIGNACIONES_FIELD_ALLOCATION,
            status=config.SHAREPOINT_ASIGNACIONES_FIELD_STATUS,
            start_date=config.SHAREPOINT_ASIGNACIONES_FIELD_START_DATE,
            end_date=config.SHAREPOINT_ASIGNACIONES_FIELD_END_DATE,
            proyecto_title=config.SHAREPOINT_PROYECTOS_FIELD_TITLE,
            colaborador_title=config.SHAREPOINT_COLABORADORES_FIELD_TITLE,
            colaborador_disponibilidad=config.SHAREPOINT_COLABORADORES_FIELD_DISPONIBILIDAD,
            proyecto_empresa_lookup=config.SHAREPOINT_PROYECTOS_FIELD_EMPRESA_LOOKUP,
            cliente_title=config.SHAREPOINT_CLIENTE_FIELD_TITLE,
        )

    def missing_fields(self) -> list[str]:
        return [
            f.name for f in dataclass_fields(self)
            if f.name not in _OPTIONAL_MAPPING_FIELDS and not getattr(self, f.name)
        ]


@dataclass(frozen=True)
class StaffingAllocationRecord:
    """Representación normalizada y estable de una fila de Asignaciones por
    proyecto, ya resuelta contra Proyectos/Colaboradores. `allocation_unit`
    es "hours_per_month" — validado contra el sitio Gestión real (Batch 3):
    el campo `Horaspormes` (displayName "Horas por mes", type `number`) es
    la fuente de `allocation_value`. Confirmado, no una inferencia."""

    assignment_id: str
    consultant_id: str | None
    consultant_name: str | None
    project_id: str | None
    project_name: str | None
    allocation_value: float | None
    allocation_unit: str
    status: str | None
    start_date: str | None
    end_date: str | None
    # Enriquecimiento OPCIONAL (Asignaciones Dashboard v1) — passthrough puro
    # del valor real de Colaboradores. None si el mapeo no está configurado
    # O el valor fuente viene vacío — nunca deriva una regla de capacidad.
    consultant_availability: str | None = None
    # Enriquecimiento OPCIONAL (revisión PBI) — resuelto vía el Proyecto de
    # esta asignación (Proyecto.EmpresaLookupId → Cliente.Title), NUNCA vía
    # el nombre de proyecto. `client_id` conserva el id crudo del lookup aun
    # si `client_name` no resuelve (mismo criterio de degradación
    # determinística que consultant_id/project_id) — nunca se fabrica una
    # identidad de cliente sintética.
    client_id: str | None = None
    client_name: str | None = None


@dataclass(frozen=True)
class VacationFieldMapping:
    """Internal names de columna Graph para la lista de Vacaciones — a
    diferencia de `StaffingFieldMapping` (config `.env`, sin default), esta
    se resuelve en RUNTIME contra `/columns` (ver `resolve_vacation_field_
    mapping` abajo). Nunca se construye incompleta: el resolver o devuelve
    una instancia completa, o levanta `VacationSchemaError` — no existe un
    estado "parcialmente resuelto" que un caller pueda inspeccionar.

    Contrato FINAL (owner live smoke #4, autoritativo — ver HANDOFF):
    `consultant_name` → displayName "Colaborador" (TEXTO simple, no lookup);
    `leave_type` → "Tipo de Licencia"; `start_date` → "Fecha de inicio";
    `end_date` → "Fecha de finalización"; `approved` → "¿Aprobado?"
    (booleano). `¿Aprobado CCHH?` existe en la fuente pero NO forma parte
    de este mapeo — fuera de V1 a propósito (ver HANDOFF)."""

    consultant_name: str
    leave_type: str
    start_date: str
    end_date: str
    approved: str


class VacationSchemaError(Exception):
    """El schema real de la lista de Vacaciones no matchea el contrato
    esperado (columna requerida faltante o ambigua) — problema de RUNTIME/
    SOURCE-SCHEMA, NUNCA de configuración de deployment. Deriva de
    `Exception`, NO de `RuntimeError`, a propósito: el router debe tratarlo
    igual que cualquier otra falla de Graph/transporte
    (`data_load_failed`/`graph_error`), nunca como `configuration_incomplete`
    — la única falla de configuración real de Vacaciones sigue siendo
    `SHAREPOINT_GESTION_SITE_ID` ausente (ver `_require_site_id`).

    `missing_fields`/`ambiguous_fields` (ver HANDOFF) contienen
    EXCLUSIVAMENTE labels del contrato semántico fijo REAL
    (`_VACATION_DISPLAY_NAMES` — "Colaborador", "Fecha de inicio", etc.)
    — NUNCA un internal name real ni un valor de dato. Seguros de loguear y
    de devolver en la respuesta HTTP del endpoint (ver router).

    Superficies TEMPORALES de discovery ya retiradas (ver HANDOFF):
    `available_columns`/`semantic_columns` existieron mientras el contrato
    real todavía no se conocía (owner smokes #2/#3) — con el contrato final
    confirmado (smoke #4), se retiraron del contrato de respuesta protegido
    para no dejar superficie de descubrimiento innecesaria; el contrato de
    error productivo final es solo `stage`/`missing_fields`/
    `ambiguous_fields`."""

    def __init__(self, stage: str, missing_fields: list[str], ambiguous_fields: list[str]):
        self.stage = stage
        self.reason = "schema_error"
        self.missing_fields = missing_fields
        self.ambiguous_fields = ambiguous_fields
        parts = []
        if missing_fields:
            parts.append(f"faltantes: {', '.join(missing_fields)}")
        if ambiguous_fields:
            parts.append(f"ambiguas (más de una columna con el mismo displayName): {', '.join(ambiguous_fields)}")
        super().__init__(
            f"No se pudo resolver el schema de la lista de Vacaciones contra Graph "
            f"(stage={stage}) — " + "; ".join(parts) + "."
        )


class VacationGraphError(Exception):
    """Envoltorio SEGURO de una falla de transporte Graph
    (`urllib.error.HTTPError`/`URLError`) durante Vacaciones — expone
    ÚNICAMENTE `stage`/`status_code`/`reason`, NUNCA el texto crudo de la
    excepción original de `urllib` (que puede incluir la URL completa con
    query string, y en teoría headers de la request/response). El
    mensaje de `Exception` (`str(self)`) tampoco incluye nada de eso —
    solo los 3 campos seguros."""

    def __init__(self, stage: str, status_code: int | None):
        self.stage = stage
        self.status_code = status_code
        self.reason = "http_error"
        super().__init__(f"Vacation Graph error — stage={stage} status_code={status_code}")


def _call_vacation_graph(stage: str, fn: Callable[[], list[dict]]) -> list[dict]:
    """Ejecuta una llamada Graph de Vacaciones (`stage` = "columns" |
    "items") envolviendo CUALQUIER falla de transporte en
    `VacationGraphError` — nunca deja escapar el `HTTPError`/`URLError`
    crudo de `urllib` (ver docstring de `VacationGraphError`). `from None`
    corta la cadena de excepción explícitamente: nada downstream que
    inspeccione `__cause__`/`__context__` puede llegar al objeto original."""
    try:
        return fn()
    except urllib.error.HTTPError as exc:
        raise VacationGraphError(stage=stage, status_code=exc.code) from None
    except urllib.error.URLError:
        raise VacationGraphError(stage=stage, status_code=None) from None


# Staffing Vacation Timeline Overlay — discovery de campos en runtime (ver
# HANDOFF). Los 5 labels EXACTOS del contrato REAL (owner live smoke #4,
# autoritativo — reemplaza los 6 labels del export offline XLSX, que NUNCA
# fueron el contrato real de la lista SharePoint) — el resolver los
# matchea contra `displayName` de Graph, NUNCA infiere el internal name
# por posición/convención.
_VACATION_DISPLAY_NAMES = ("Colaborador", "Fecha de inicio", "Fecha de finalización", "Tipo de Licencia", "¿Aprobado?")

# Metadata de columnas estable durante el uso normal de la app — TTL chico
# alcanza (mismo orden de magnitud que `SHAREPOINT_CASES_CACHE_TTL`, ver
# config.py), sin necesidad de un config nuevo para un detalle de
# implementación interno.
_VACATION_SCHEMA_CACHE_TTL_SECONDS = 300


def _ttl_cache(ttl_seconds: int) -> Callable:
    """Cache in-process con TTL — mismo patrón que
    `core/sharepoint_cases.py::_ttl_cache` (reimplementado acá, no importado:
    es un helper privado de ese módulo, sin acoplar dos features via un
    símbolo `_`-prefijado). Única diferencia deliberada: la key es la TUPLA
    COMPLETA de args posicionales (nunca solo el primero) — `access_token`
    viaja SIEMPRE por kwarg (`access_token=...`), así que nunca entra a la
    key ni se guarda en el diccionario de cache. Solo cachea el resultado
    EXITOSO de `fn` — si `fn` lanza, la excepción se propaga antes de
    escribir al cache, así que fallas (401/403/columna faltante/ambigua)
    nunca quedan cacheadas."""
    def decorator(fn: Callable) -> Callable:
        store: dict = {}
        lock = threading.Lock()

        def wrapper(*args, **kwargs):
            now = time.monotonic()
            with lock:
                hit = store.get(args)
                if hit is not None and (now - hit[0]) < ttl_seconds:
                    return hit[1]
            value = fn(*args, **kwargs)
            with lock:
                store[args] = (now, value)
            return value

        def clear() -> None:
            with lock:
                store.clear()

        wrapper.clear = clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _resolve_vacation_field_mapping_from_columns(columns: list[dict]) -> VacationFieldMapping:
    """Matchea `displayName` EXACTO (whitespace normalizado únicamente —
    sin case-folding, sin fuzzy, sin posición) contra los 5 labels REALES
    (`_VACATION_DISPLAY_NAMES`). Un `displayName` con CERO columnas
    candidatas es "faltante"; con DOS O MÁS es "ambiguo" — ninguno de los
    dos elige un candidato al azar, ambos levantan `VacationSchemaError`
    con el detalle exacto (solo labels, ver docstring de la excepción)."""
    candidates: dict[str, list[str]] = {}
    for column in columns:
        display_name = (column.get("displayName") or "").strip()
        technical_name = column.get("name")
        if display_name and technical_name:
            candidates.setdefault(display_name, []).append(technical_name)

    resolved: dict[str, str] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for display_name in _VACATION_DISPLAY_NAMES:
        found = candidates.get(display_name, [])
        if len(found) == 1:
            resolved[display_name] = found[0]
        elif len(found) == 0:
            missing.append(display_name)
        else:
            ambiguous.append(display_name)

    if missing or ambiguous:
        raise VacationSchemaError(stage="columns", missing_fields=missing, ambiguous_fields=ambiguous)

    return VacationFieldMapping(
        consultant_name=resolved["Colaborador"], leave_type=resolved["Tipo de Licencia"],
        start_date=resolved["Fecha de inicio"], end_date=resolved["Fecha de finalización"],
        approved=resolved["¿Aprobado?"],
    )


@_ttl_cache(_VACATION_SCHEMA_CACHE_TTL_SECONDS)
def _fetch_vacation_field_mapping_cached(
    token_fingerprint: str, site_id: str, list_id: str, *, access_token: str,
) -> VacationFieldMapping:
    del token_fingerprint  # solo participa de la cache key, ver _ttl_cache
    columns = _call_vacation_graph("columns", lambda: microsoft365.graph_get_paged_values(
        f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/columns", access_token,
    ))
    return _resolve_vacation_field_mapping_from_columns(columns)


def resolve_vacation_field_mapping(site_id: str, list_id: str, access_token: str) -> VacationFieldMapping:
    """Punto de entrada público del discovery en runtime — key de cache
    (fingerprint de token + site/list, NUNCA el token crudo)."""
    fingerprint = hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:16]
    return _fetch_vacation_field_mapping_cached(fingerprint, site_id, list_id, access_token=access_token)


@dataclass(frozen=True)
class VacationPeriod:
    """Un período de vacaciones APROBADO, ya filtrado por Tipo de Licencia +
    Approved, con `consultant_name` CRUDO (`fields["Colaborador"]`, texto
    simple tal cual vino de la fuente, sin normalizar ni resolver contra
    Colaboradores todavía — ese join determinístico vive en
    `StaffingService`, ver ahí). `start_date`/
    `end_date` son passthrough puro (mismo criterio que
    `StaffingAllocationRecord`) — ninguna conversión de fecha ocurre en este
    módulo; el parseo/validación vive en el servicio, igual que las fechas
    de Asignaciones."""

    source_id: str
    consultant_name: str
    start_date: str | None
    end_date: str | None


def _list_items_url(
    site_id: str, list_id: str, select_fields: list[str] | None = None, *, filter_expr: str | None = None,
) -> str:
    """`select_fields`, si se pasa, restringe explícitamente qué internal
    names de `fields` devuelve Graph (`$expand=fields($select=...)`) en vez
    de depender de la proyección default de `$expand=fields` sin selección.
    Ver `_colaboradores_url` — Graph puede omitir columnas custom (p. ej.
    tipo Choice) de esa proyección default aun cuando otras columnas custom
    de la MISMA lista sí aparecen; pedir el campo explícito lo hace robusto
    sin depender de un comportamiento no documentado.

    `filter_expr` (Staffing Director Priority V2) — expresión OData cruda
    (p. ej. `"fields/Status eq 'Inactivo'"`), URL-encodeada acá; `None`
    (default) no agrega `$filter` — mismo comportamiento de siempre."""
    url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items?$expand=fields"
    if select_fields:
        url += f"($select={','.join(select_fields)})"
    if filter_expr:
        url += f"&$filter={quote(filter_expr, safe='')}"
    return url


def _odata_quote(value: str) -> str:
    """Escape mínimo de literal OData (comilla simple → doble comilla
    simple) — `value` siempre es una constante interna nuestra (nunca input
    de usuario), pero escapar es gratis y correcto igual."""
    return value.replace("'", "''")


def _stringify(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolved_title(item: dict | None, title_field: str) -> str | None:
    if not item:
        return None
    fields = item.get("fields") or {}
    return _stringify(fields.get(title_field))


class StaffingRepository:
    """Lee las 3 listas de Staffing del sitio Gestión y devuelve
    `StaffingAllocationRecord`s ya unidos. Sin estado mutable de proceso:
    cada llamada a `get_allocations` es independiente, `access_token` viaja
    explícito por parámetro."""

    def __init__(
        self,
        *,
        site_id: str | None = None,
        asignaciones_list_id: str | None = None,
        proyectos_list_id: str | None = None,
        colaboradores_list_id: str | None = None,
        cliente_list_id: str | None = None,
        field_mapping: StaffingFieldMapping | None = None,
        vacaciones_list_id: str | None = None,
        vacation_field_mapping: VacationFieldMapping | None = None,
    ):
        self.site_id = site_id if site_id is not None else config.SHAREPOINT_GESTION_SITE_ID
        self.asignaciones_list_id = asignaciones_list_id or config.SHAREPOINT_ASIGNACIONES_LIST_ID
        self.proyectos_list_id = proyectos_list_id or config.SHAREPOINT_PROYECTOS_LIST_ID
        self.colaboradores_list_id = colaboradores_list_id or config.SHAREPOINT_COLABORADORES_LIST_ID
        self.cliente_list_id = cliente_list_id or config.SHAREPOINT_CLIENTE_LIST_ID
        self.field_mapping = field_mapping if field_mapping is not None else StaffingFieldMapping.from_config()
        # Vacaciones (Staffing Vacation Timeline Overlay, ver HANDOFF) —
        # MISMO site_id de arriba, list ID configurado igual que las otras 4
        # listas. `vacation_field_mapping` es un SEAM DE TEST ÚNICAMENTE
        # (override explícito) — en producción (`None`, default) el mapeo se
        # resuelve en RUNTIME contra Graph (`resolve_vacation_field_mapping`,
        # ver `get_vacations`), nunca desde config.
        self.vacaciones_list_id = vacaciones_list_id or config.SHAREPOINT_VACACIONES_LIST_ID
        self._vacation_field_mapping_override = vacation_field_mapping
        # Diagnóstico de la ÚLTIMA llamada a `get_allocations` (pre-PR
        # corrective, ver HANDOFF) — rows/pages/elapsed_ms/filter_used/
        # fallback_used por fuente, NUNCA token/payload/PII. Instancia
        # nueva por request (ver `webapp/routers/staffing.py`), así que no
        # hay estado compartido entre requests concurrentes. `get_vacations`/
        # `get_consultants` reusan el MISMO diccionario (keys distintas,
        # "vacaciones"/"colaboradores_identity") — nunca lo resetean, para
        # no perder el diagnóstico de `get_allocations` si ambos se llaman
        # sobre la misma instancia.
        self.last_diagnostics: dict = {}

    def _require_site_id(self) -> str:
        if not self.site_id:
            raise RuntimeError(
                "SHAREPOINT_GESTION_SITE_ID no está configurado. Es un prerequisito "
                "de despliegue pendiente (Batch 2, ver HANDOFF) antes de que Staffing "
                "pueda leer el sitio Gestión — no se degrada a una lista vacía a "
                "propósito, para no esconder un dato de negocio real detrás de un "
                "'sin resultados'."
            )
        return self.site_id

    def _require_field_mapping(self) -> StaffingFieldMapping:
        missing = self.field_mapping.missing_fields()
        if missing:
            raise RuntimeError(
                "Falta configurar el mapeo de campos de Staffing (Batch 2, ver "
                "HANDOFF) — ninguno tiene default de producción a propósito, para "
                "no leer un campo equivocado sin que nadie lo note. Faltan: "
                + ", ".join(missing)
            )
        return self.field_mapping

    def _resolve_vacation_field_mapping(self, site_id: str, access_token: str) -> VacationFieldMapping:
        """Override de test (constructor) tiene prioridad; si no se pasó
        ninguno, resuelve en RUNTIME contra Graph — ver
        `resolve_vacation_field_mapping` module-level. Puede levantar
        `VacationSchemaError` (schema/runtime, no config) o cualquier
        excepción de transporte de `core.microsoft365` — ninguna se
        atrapa acá, el caller (`get_vacations`) las deja propagar."""
        if self._vacation_field_mapping_override is not None:
            return self._vacation_field_mapping_override
        return resolve_vacation_field_mapping(site_id, self.vacaciones_list_id, access_token)

    def _fetch_asignaciones(
        self, site_id: str, mapping: StaffingFieldMapping, access_token: str, assignment_scope: str,
    ) -> list[dict]:
        """Asignaciones filtradas por `assignment_scope` — ver constantes del
        módulo. `"operational"` (default) excluye Inactivo del fetch inicial;
        `"historical_inactive"` pide EXCLUSIVAMENTE Inactivo (el delta lazy).
        Intenta `$filter` + el header `Prefer` primero; si Graph lo rechaza
        (columna no indexada, `HTTPError`/`URLError`/JSON inválido — nunca
        confirmado contra el sitio real desde este entorno), cae a pedir la
        lista COMPLETA sin `$filter` y filtrar en Python — más caro en el
        camino de excepción, pero SIEMPRE correcto; Staffing nunca se rompe
        por una query no soportada."""
        wants_inactive = assignment_scope == "historical_inactive"
        quoted = _odata_quote(INACTIVE_STATUS_VALUE)
        filter_expr = f"fields/{mapping.status} eq '{quoted}'" if wants_inactive else f"fields/{mapping.status} ne '{quoted}'"
        pages = 0

        def _count_page(page_len: int) -> None:
            nonlocal pages
            pages += 1

        started = time.monotonic()
        try:
            items = microsoft365.graph_get_paged_values(
                _list_items_url(site_id, self.asignaciones_list_id, filter_expr=filter_expr),
                access_token, extra_headers=_HISTORY_PREFER_HEADER, on_page=_count_page,
            )
            self.last_diagnostics["asignaciones"] = {
                "rows": len(items), "pages": pages,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "filter_used": True, "fallback_used": False,
            }
            return items
        except Exception:  # noqa: BLE001 — fallback de transporte, nunca una regla de negocio
            pages = 0
            all_items = microsoft365.graph_get_paged_values(
                _list_items_url(site_id, self.asignaciones_list_id), access_token, on_page=_count_page,
            )
            filtered = [
                item for item in all_items
                if (_stringify((item.get("fields") or {}).get(mapping.status)) == INACTIVE_STATUS_VALUE) == wants_inactive
            ]
            self.last_diagnostics["asignaciones"] = {
                "rows": len(filtered), "pages": pages,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "filter_used": False, "fallback_used": True,
            }
            return filtered

    def _timed_fetch(self, diagnostics_key: str, url: str, access_token: str) -> list[dict]:
        """Wrapper de diagnóstico (pre-PR corrective, ver HANDOFF) para las
        3 tablas de dimensión (Proyectos/Colaboradores/Cliente) — sin
        `$filter`, sin fallback: solo mide rows/pages/elapsed_ms. NUNCA
        loguea token/payload — solo cuenta."""
        pages = 0

        def _count_page(page_len: int) -> None:
            nonlocal pages
            pages += 1

        started = time.monotonic()
        items = microsoft365.graph_get_paged_values(url, access_token, on_page=_count_page)
        self.last_diagnostics[diagnostics_key] = {
            "rows": len(items), "pages": pages,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
        return items

    def get_allocations(
        self, access_token: str, *, assignment_scope: str = "operational",
    ) -> list[StaffingAllocationRecord]:
        self.last_diagnostics = {}
        total_started = time.monotonic()
        site_id = self._require_site_id()
        mapping = self._require_field_mapping()

        asignaciones = self._fetch_asignaciones(site_id, mapping, access_token, assignment_scope)
        # Proyectos: bare `$expand=fields` mientras Cliente no está
        # configurado (comportamiento sin cambios, sin necesidad comprobada
        # de `$select` para Title). Si `proyecto_empresa_lookup` SÍ está
        # configurado, se selecciona explícito junto con Title — mismo
        # motivo defensivo que Colaboradores (columnas lookup/choice custom
        # pueden quedar fuera de la proyección default de Graph).
        if mapping.proyecto_empresa_lookup:
            proyectos = self._timed_fetch(
                "proyectos",
                _list_items_url(
                    site_id, self.proyectos_list_id,
                    [mapping.proyecto_title, mapping.proyecto_empresa_lookup],
                ), access_token,
            )
        else:
            proyectos = self._timed_fetch(
                "proyectos", _list_items_url(site_id, self.proyectos_list_id), access_token,
            )
        # Colaboradores SÍ pide `$select` explícito (a diferencia de
        # Asignaciones/Proyectos, que ya funcionan correctamente con
        # `$expand=fields` sin selección — no se tocan, sin necesidad
        # comprobada). Hallazgo real-data (corrective pass, ver HANDOFF):
        # Disponibilidad de asignación devolvía "Sin dato" para el 100% de
        # las filas pese a mapeo correcto — hipótesis más probable: la
        # proyección default de `$expand=fields` de Graph para SharePoint
        # puede omitir columnas custom tipo Choice aunque otras columnas
        # custom de la MISMA lista (Title) sí aparezcan. `colaborador_title`
        # siempre se selecciona; `colaborador_disponibilidad` solo si está
        # configurado (campo OPCIONAL — nunca se selecciona una columna
        # vacía/inexistente).
        colaboradores_fields = [mapping.colaborador_title]
        if mapping.colaborador_disponibilidad:
            colaboradores_fields.append(mapping.colaborador_disponibilidad)
        colaboradores = self._timed_fetch(
            "colaboradores",
            _list_items_url(site_id, self.colaboradores_list_id, colaboradores_fields), access_token,
        )

        proyectos_by_id = {str(item.get("id")): item for item in proyectos if item.get("id") is not None}
        colaboradores_by_id = {str(item.get("id")): item for item in colaboradores if item.get("id") is not None}

        # Cliente (revisión PBI, ver HANDOFF) — lectura ADICIONAL de Graph
        # durante la carga inicial, SOLO si el mapeo está completo (lookup +
        # título del destino). Resuelto una vez POR PROYECTO (no por
        # asignación) y cacheado en `project_client_by_id`, reusado en el
        # loop de abajo — nunca una segunda llamada por asignación. Cero
        # llamadas si Cliente no está configurado (comportamiento histórico
        # intacto).
        project_client_by_id: dict[str, tuple[str | None, str | None]] = {}
        if mapping.proyecto_empresa_lookup and mapping.cliente_title:
            clientes = self._timed_fetch(
                "cliente",
                _list_items_url(site_id, self.cliente_list_id, [mapping.cliente_title]), access_token,
            )
            clientes_by_id = {str(item.get("id")): item for item in clientes if item.get("id") is not None}
            for pid, pitem in proyectos_by_id.items():
                pfields = pitem.get("fields") or {}
                empresa_id = _stringify(pfields.get(mapping.proyecto_empresa_lookup))
                cliente_item = clientes_by_id.get(empresa_id) if empresa_id else None
                project_client_by_id[pid] = (empresa_id, _resolved_title(cliente_item, mapping.cliente_title))

        records: list[StaffingAllocationRecord] = []
        for item in asignaciones:
            fields = item.get("fields") or {}
            consultor_id = _stringify(fields.get(mapping.colaborador_lookup_id))
            proyecto_id = _stringify(fields.get(mapping.proyecto_lookup_id))
            # Degradación determinística (sección 8): una referencia de lookup
            # sin resolver NO descarta la fila — el id crudo se conserva, el
            # nombre resuelto queda en None (el consumidor decide cómo
            # mostrarlo, p. ej. "(sin resolver)").
            consultor_item = colaboradores_by_id.get(consultor_id) if consultor_id else None
            proyecto_item = proyectos_by_id.get(proyecto_id) if proyecto_id else None
            # Cliente se resuelve vía el PROYECTO, nunca vía el nombre de
            # proyecto — si el proyecto mismo no resolvió, tampoco hay
            # cliente (sin identidad sintética).
            client_id, client_name = project_client_by_id.get(proyecto_id, (None, None)) if proyecto_id else (None, None)
            records.append(
                StaffingAllocationRecord(
                    assignment_id=str(item.get("id") or ""),
                    consultant_id=consultor_id,
                    consultant_name=_resolved_title(consultor_item, mapping.colaborador_title),
                    project_id=proyecto_id,
                    project_name=_resolved_title(proyecto_item, mapping.proyecto_title),
                    allocation_value=_to_float(fields.get(mapping.allocation)),
                    allocation_unit="hours_per_month",
                    status=_stringify(fields.get(mapping.status)),
                    start_date=_stringify(fields.get(mapping.start_date)),
                    end_date=_stringify(fields.get(mapping.end_date)),
                    # `_resolved_title` ya maneja item=None y field="" con
                    # gracia (devuelve None) — mismo helper que resuelve
                    # consultant_name, sin lógica especial para el campo
                    # opcional.
                    consultant_availability=_resolved_title(consultor_item, mapping.colaborador_disponibilidad),
                    client_id=client_id,
                    client_name=client_name,
                )
            )
        self.last_diagnostics["total_elapsed_ms"] = round((time.monotonic() - total_started) * 1000, 1)
        return records

    def get_vacations(self, access_token: str) -> list[VacationPeriod]:
        """Vacaciones APROBADAS ("Solicitud vacaciones", ver HANDOFF) — filtra
        `TipodeLicencia == "Vacaciones"` AND `Approved is True` en Python (no
        `$filter` de Graph: la lista es chica, sin necesidad de la
        optimización que sí justifica `_fetch_asignaciones`). `Approved` se
        compara con identidad booleana estricta (`is True`) — Graph devuelve
        un booleano JSON real para esta columna, nunca un string; `False`,
        `None` o el campo ausente excluyen la fila igual, sin necesidad de
        distinguirlos. Devuelve `consultant_name` CRUDO
        (`fields["Colaborador"]`, texto simple) — el join contra
        Colaboradores vive en `StaffingService`. Filas sin nombre o sin
        AMBAS fechas se excluyen acá — sin consultor no hay nada que
        overlayar, y una fecha faltante no es un período válido (fail-soft
        por fila, nunca rompe el resto).

        Embudo de diagnóstico (Empty Success — Repository Filter Funnel
        Diagnostics corrective, ver HANDOFF) — `self.last_diagnostics
        ["vacaciones"]` gana conteos PUROS por etapa del filtro (raw/tipo/
        aprobación/presencia de campos) para que UN refresh del owner
        diga en qué etapa exacta las filas desaparecen, sin necesitar otra
        llamada a Graph ni loguear ningún valor de fila/PII. El desglose de
        aprobación se mide SOLO sobre el subconjunto `TipodeLicencia ==
        "Vacaciones"` (la población de negocio relevante). NO cambia
        ningún filtro/regla de negocio — observación pura."""
        site_id = self._require_site_id()
        mapping = self._resolve_vacation_field_mapping(site_id, access_token)
        pages = 0

        def _count_page(page_len: int) -> None:
            nonlocal pages
            pages += 1

        started = time.monotonic()
        items = _call_vacation_graph("items", lambda: microsoft365.graph_get_paged_values(
            _list_items_url(
                site_id, self.vacaciones_list_id,
                [mapping.consultant_name, mapping.leave_type, mapping.start_date, mapping.end_date, mapping.approved],
            ),
            access_token, on_page=_count_page,
        ))

        leave_type_vacaciones_rows = 0
        approved_true_rows = 0
        approved_false_rows = 0
        approved_missing_or_null_rows = 0
        approved_other_rows = 0
        required_fields_present_rows = 0
        missing_consultant_rows = 0
        missing_start_date_rows = 0
        missing_end_date_rows = 0

        periods: list[VacationPeriod] = []
        for item in items:
            fields = item.get("fields") or {}
            if _stringify(fields.get(mapping.leave_type)) != VACATION_LEAVE_TYPE_VALUE:
                continue
            leave_type_vacaciones_rows += 1

            # Desglose de aprobación — SOLO sobre el subconjunto Vacaciones
            # (población de negocio relevante). `approved_other_rows` no
            # asume que el runtime de Graph siempre entrega un `bool`
            # Python real para esta columna — si algún día llega otra cosa
            # (p. ej. un string), queda contado acá, NUNCA aceptado como
            # aprobación (el filtro de negocio sigue siendo `is True`
            # estricto, sin cambios).
            approved_value = fields.get(mapping.approved)
            if approved_value is True:
                approved_true_rows += 1
            elif approved_value is False:
                approved_false_rows += 1
            elif approved_value is None:
                approved_missing_or_null_rows += 1
            else:
                approved_other_rows += 1

            if approved_value is not True:
                continue

            consultant_name = _stringify(fields.get(mapping.consultant_name)) or ""
            start = _stringify(fields.get(mapping.start_date))
            end = _stringify(fields.get(mapping.end_date))
            if not consultant_name:
                missing_consultant_rows += 1
            if not start:
                missing_start_date_rows += 1
            if not end:
                missing_end_date_rows += 1
            if not consultant_name or not start or not end:
                continue
            required_fields_present_rows += 1
            periods.append(VacationPeriod(
                source_id=str(item.get("id") or ""), consultant_name=consultant_name,
                start_date=start, end_date=end,
            ))

        self.last_diagnostics["vacaciones"] = {
            "rows": len(periods), "pages": pages,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "raw_rows": len(items),
            "leave_type_vacaciones_rows": leave_type_vacaciones_rows,
            "approved_true_rows": approved_true_rows,
            "approved_false_rows": approved_false_rows,
            "approved_missing_or_null_rows": approved_missing_or_null_rows,
            "approved_other_rows": approved_other_rows,
            "required_fields_present_rows": required_fields_present_rows,
            "missing_consultant_rows": missing_consultant_rows,
            "missing_start_date_rows": missing_start_date_rows,
            "missing_end_date_rows": missing_end_date_rows,
        }
        return periods

    def get_consultants(self, access_token: str) -> list[tuple[str, str]]:
        """SOLO Colaboradores (id, nombre) — universo de identidad para el
        join determinístico de Vacaciones (ver `StaffingService`), sin forzar
        una lectura completa de Asignaciones/Proyectos solo para resolver
        nombres. Reusa `colaborador_title` de `StaffingFieldMapping` — el
        mismo mapeo que ya requiere `get_allocations`, sin un campo nuevo."""
        site_id = self._require_site_id()
        title_field = self.field_mapping.colaborador_title
        if not title_field:
            raise RuntimeError(
                "Falta configurar el mapeo de campos de Staffing (Batch 2, ver "
                "HANDOFF) — colaborador_title es requisito de get_consultants "
                "igual que de get_allocations."
            )
        items = self._timed_fetch(
            "colaboradores_identity", _list_items_url(site_id, self.colaboradores_list_id, [title_field]), access_token,
        )
        result: list[tuple[str, str]] = []
        for item in items:
            cid = str(item.get("id") or "")
            name = _resolved_title(item, title_field)
            if cid and name:
                result.append((cid, name))
        return result
