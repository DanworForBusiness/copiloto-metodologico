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

AUSENCIAS APROBADAS (Staffing Approved Absence Overlay — generalización del
Vacation Timeline Overlay original, ver HANDOFF): `get_absences` lee el
MISMO XLSX autoritativo de siempre (`Solicitudes (2).xlsx`, sitio
Operaciones) filtrando ÚNICAMENTE `Estado == "Aprobada"` — a diferencia del
overlay original (que además exigía `Motivo == "Vacaciones"`), CUALQUIER
Motivo aprobado es elegible ahora. Cada fila elegible se CLASIFICA
server-side en uno de 4 conceptos canónicos (`_classify_absence_concept`):
`Vacaciones`/`Danwor Day`/`Cumpleaños` por comparación normalizada
(trim/colapso de espacios/casefold/folding de diacríticos — MISMO criterio
determinístico que la identidad de consultor, sin fuzzy-matching), o
`Otros` para cualquier otro Motivo. El Motivo CRUDO nunca sobrevive más
allá del parseo — `AbsencePeriod` solo retiene el concepto YA clasificado
(privacy boundary: un Motivo libre de "Otros" podría ser información
sensible del empleado; el browser nunca lo recibe, ni siquiera el
`StaffingService`). Devuelve `consultant_name` CRUDO ("Nombre Apellido") —
el join de identidad determinístico contra el universo de Colaboradores de
Staffing vive en `StaffingService` (sin cambios: sigue siendo join por
nombre normalizado). `get_consultants` lee SOLO Colaboradores (id +
nombre, sin Asignaciones/Proyectos) — universo de identidad para ese join.

FUENTE AUTORITATIVA — XLSX EN SHAREPOINT (sin cambios de ubicación desde el
corrective original): `Solicitudes (2).xlsx` en la biblioteca de
documentos del sitio Operaciones (`General/Template Dashboard/`).
`get_absences` descarga ese workbook vía Graph (`root:/{path}:/content`),
lo parsea EN MEMORIA con openpyxl (read-only/data-only, sin persistir).

CONTRATO DE UBICACIÓN (module constants, no config): `ABSENCE_SITE_PATH`/
`ABSENCE_XLSX_PATH`/`ABSENCE_WORKSHEET_NAME` son la ubicación estable
(sitio + ruta en el drive), NO una URL de descarga temporal ni un id de
driveItem. Ningún list ID de SharePoint — la fuente XLSX no lo necesita.

DIAGNÓSTICO SEGURO DE FALLAS (ver HANDOFF): `_call_absence_graph(stage,
fn)` envuelve las llamadas Graph de Ausencias (`stage="site"` para
resolver el sitio, `stage="download"` para bajar el workbook) — cualquier
`HTTPError`/`URLError` de `urllib` se traduce a `AbsenceGraphError(stage,
status_code)`, NUNCA deja escapar la excepción cruda. Un workbook
inválido / worksheet o headers faltantes levantan `AbsenceSchemaError`
(`stage="workbook"/"worksheet"/"headers"`, con labels de contrato, nunca
datos/PII). `AbsenceSchemaError` NO es `RuntimeError` a propósito: es un
problema de la fuente, NUNCA de configuración de deployment — el router lo
trata igual que cualquier otra falla de Graph (`graph_error`), nunca como
`configuration_incomplete`.

Ambos (`get_absences`/`get_consultants`)
fail-soft ante cualquier excepción — el router captura el error y omite
el overlay, Asignaciones nunca se rompe."""
from __future__ import annotations

import io
import re
import time
import unicodedata
import urllib.error
from datetime import date, datetime
from dataclasses import dataclass, fields as dataclass_fields
from typing import Callable
from urllib.parse import quote

import openpyxl

import config
from core import microsoft365
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# SHARED SOURCE ACQUISITION corrective (owner ticket, ver HANDOFF) — Staffing
# lee las MISMAS 2 fuentes ya probadas de Reporting (`DeltaCache` para
# Proyectos/Colaboradores/Cliente vía SharePoint List Delta, `TimeLogsHistoricalCache`
# para el XLSX de Ausencias vía eTag) para eliminar la re-lectura remota de
# fuentes estables ya adquiridas en la MISMA sesión (Initial → Historial →
# Ausencias). Reusa las CLASES, nunca un framework nuevo. Instancias PROPIAS
# de Staffing (`_STAFFING_DELTA_CACHE`/`_STAFFING_XLSX_CACHE`, ver abajo),
# NUNCA compartidas con `reporting_repository._DELTA_CACHE`/
# `_TIME_LOGS_HISTORICAL_CACHE`/`_FACTURADAS_CACHE` — aunque
# `config.SHAREPOINT_GESTION_SITE_ID`/`SHAREPOINT_PROYECTOS_LIST_ID`/
# `SHAREPOINT_CLIENTE_LIST_ID` son LITERALMENTE los mismos valores que usa
# Reporting (confirmado leyendo `ReportingRepository.__init__`), el `$select`
# efectivo de cada select depende de `StaffingFieldMapping.from_config()`
# (internal names resueltos en RUNTIME vía `.env`, a diferencia de las
# constantes Python hardcodeadas de Reporting como `PROY_TITLE`) — no hay
# forma de probar ESTÁTICAMENTE que ambos módulos siempre pedirían el mismo
# superset de columnas. Compartir la MISMA instancia arriesgaría que un
# estado cacheado por Reporting bajo esa key le sirva a Staffing filas sin
# el campo que necesita (o viceversa) — exactamente el riesgo que el "CLIENTE_SELECT_FIELDS
# mistake" ya probó en Reporting, ahora entre dos MÓDULOS en vez de dos
# consumidores del mismo módulo. Instancias separadas eliminan el riesgo por
# construcción: la key `(cache_scope, site_id, list_id)` puede coincidir
# textualmente sin que los dos diccionarios internos jamás se toquen.

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


class AbsenceSchemaError(Exception):
    """El workbook XLSX de Ausencias no matchea el contrato esperado
    (`stage="workbook"` inválido/corrupto, `"worksheet"` faltante, `"headers"`
    requeridos ausentes, o `"site"` sin id resuelto) — problema de RUNTIME/
    SOURCE, NUNCA de configuración de deployment. Deriva de `Exception`, NO de
    `RuntimeError`, a propósito: el router debe tratarlo igual que cualquier
    otra falla de Graph/transporte (`graph_error`), nunca como
    `configuration_incomplete`.

    `missing_fields`/`ambiguous_fields` (ver HANDOFF) contienen
    EXCLUSIVAMENTE labels del contrato fijo (nombres de worksheet/headers
    requeridos como "Apellido"/"Motivo"/"Estado", o marcadores como
    "workbook"/"site_id") — NUNCA un valor de dato ni PII. Seguros de loguear
    y de devolver en la respuesta HTTP del endpoint (ver router).
    `ambiguous_fields` se conserva en el contrato (siempre vacío hoy: el
    worksheet plano no produce ambigüedad de header) para no romper el
    contrato de error ya emitido por el router."""

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
            f"No se pudo resolver el schema de la lista de Ausencias contra Graph "
            f"(stage={stage}) — " + "; ".join(parts) + "."
        )


class AbsenceGraphError(Exception):
    """Envoltorio SEGURO de una falla de transporte Graph
    (`urllib.error.HTTPError`/`URLError`) durante Ausencias — expone
    ÚNICAMENTE `stage`/`status_code`/`reason`, NUNCA el texto crudo de la
    excepción original de `urllib` (que puede incluir la URL completa con
    query string, y en teoría headers de la request/response). El
    mensaje de `Exception` (`str(self)`) tampoco incluye nada de eso —
    solo los 3 campos seguros."""

    def __init__(self, stage: str, status_code: int | None):
        self.stage = stage
        self.status_code = status_code
        self.reason = "http_error"
        super().__init__(f"Absence Graph error — stage={stage} status_code={status_code}")


def _call_absence_graph(stage: str, fn: Callable[[], object]) -> object:
    """Ejecuta una llamada Graph de Ausencias (`stage` = "site" | "metadata" |
    "download") envolviendo CUALQUIER falla de transporte en
    `AbsenceGraphError` — nunca deja escapar el `HTTPError`/`URLError`
    crudo de `urllib` (ver docstring de `AbsenceGraphError`). `from None`
    corta la cadena de excepción explícitamente: nada downstream que
    inspeccione `__cause__`/`__context__` puede llegar al objeto original.

    RESILIENCY corrective (SHARED SOURCE ACQUISITION, ver HANDOFF — MISMO fix
    ya probado en `reporting_repository._call_graph`, Reporting Fase 3B): un
    timeout de LECTURA (`response.read()` dentro de `urlopen(...)`, ya con la
    conexión establecida) llega acá como `TimeoutError` CRUDO — `urllib`
    NUNCA lo envuelve en `URLError` (eso solo pasa para fallos en la fase de
    conexión). Antes de este fix se propagaba sin clasificar, con el mismo
    riesgo de tumbar el request completo que ya se probó en producción para
    Reporting. MISMO `status_code=None` que `URLError` — "sin respuesta HTTP
    recibida", la señal que el sync delta/version-cache de más abajo usa
    para decidir su fallback."""
    try:
        return fn()
    except urllib.error.HTTPError as exc:
        raise AbsenceGraphError(stage=stage, status_code=exc.code) from None
    except urllib.error.URLError:
        raise AbsenceGraphError(stage=stage, status_code=None) from None
    except TimeoutError:
        raise AbsenceGraphError(stage=stage, status_code=None) from None


# ── Staffing Approved Absence Overlay — FUENTE AUTORITATIVA: XLSX en SharePoint
# (misma ubicación que el corrective original de Vacaciones, ver HANDOFF). La
# fuente real es "Solicitudes (2).xlsx" en la biblioteca de documentos del sitio
# Operaciones. Estas son la UBICACIÓN autoritativa y estable (sitio + ruta en el
# drive), NO una URL de descarga temporal ni un id de driveItem.
ABSENCE_SITE_PATH = "danwor.sharepoint.com:/sites/Operaciones"
ABSENCE_XLSX_PATH = "General/Template Dashboard/Solicitudes (2).xlsx"
ABSENCE_WORKSHEET_NAME = "Solicitudes"

# Semántica de negocio autoritativa del propio XLSX: elegible si
# Estado == "Aprobada" — a diferencia del overlay original (Vacaciones-only),
# el Motivo YA NO es un gate de elegibilidad, es lo que se CLASIFICA (ver
# `_classify_absence_concept`).
ABSENCE_ESTADO_VALUE = "Aprobada"

# Horas equivalentes por día de ausencia — regla de negocio de DISPLAY
# autorizada por el owner (post-live-smoke UX corrective, generalizada del
# batch original de Vacaciones): la etiqueta del overlay muestra `Días × 8`.
# SOLO informativo — nunca toca allocation/Horaspormes/KPIs (ver
# `AbsencePeriod.hours`).
ABSENCE_HOURS_PER_DAY = 8

# Headers mínimos requeridos del worksheet. Del set completo (14 columnas) solo
# se usan Apellido/Nombre/Motivo/Desde/Hasta/Estado como contrato de runtime, más
# `Días` OPCIONAL (solo para la etiqueta de horas; su ausencia NO invalida el
# período). CUIL/Legajo/comentarios/etiquetas NUNCA se retienen ni exponen; el
# valor crudo de `Días` tampoco (solo su producto ×8 sale al payload). `Motivo`
# se retiene solo para clasificar el concepto EN el parseo — el valor crudo
# nunca sobrevive más allá de eso (ver `AbsencePeriod`).
_ABSENCE_REQUIRED_HEADERS = ("Apellido", "Nombre", "Motivo", "Desde", "Hasta", "Estado")
_ABSENCE_DIAS_HEADER = "Días"

# Approved Absence Overlay — Concept Classification (ver HANDOFF): TODO
# Motivo aprobado se clasifica server-side en exactamente UNO de estos 4
# conceptos canónicos — nunca se expone el Motivo crudo al browser (privacy
# boundary: un Motivo libre de "Otros" podría ser información sensible del
# empleado). Comparación NORMALIZADA (trim/colapso de espacios/casefold/
# folding de diacríticos — mismo criterio determinístico que la identidad de
# consultor, ver `staffing_service._normalize_consultant_name`), SIN
# fuzzy-matching: cualquier Motivo que no normalice EXACTO a Vacaciones/
# Danwor Day/Cumpleaños cae en Otros.
ABSENCE_CONCEPT_VACACIONES = "Vacaciones"
ABSENCE_CONCEPT_DANWOR_DAY = "Danwor Day"
ABSENCE_CONCEPT_CUMPLEANOS = "Cumpleaños"
ABSENCE_CONCEPT_OTROS = "Otros"


def _normalize_label(value: str | None) -> str:
    """Identidad determinística para comparar labels de negocio (Motivo) —
    trim + colapso de espacios repetidos + casefold + folding de diacríticos
    (NFKD, se descartan los combining marks). MISMO algoritmo que
    `staffing_service._normalize_consultant_name` (duplicado deliberado, no
    importado: dominios distintos — nombre de persona vs. concepto de
    ausencia — que no deben acoplarse solo por compartir 4 líneas de
    normalización). SIN fuzzy-matching."""
    collapsed = " ".join((value or "").split())
    decomposed = unicodedata.normalize("NFKD", collapsed.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_ABSENCE_KNOWN_CONCEPTS = {
    _normalize_label(ABSENCE_CONCEPT_VACACIONES): ABSENCE_CONCEPT_VACACIONES,
    _normalize_label(ABSENCE_CONCEPT_DANWOR_DAY): ABSENCE_CONCEPT_DANWOR_DAY,
    _normalize_label(ABSENCE_CONCEPT_CUMPLEANOS): ABSENCE_CONCEPT_CUMPLEANOS,
}


def _classify_absence_concept(raw_motivo: str) -> str:
    """Clasifica un Motivo APROBADO en uno de los 4 conceptos canónicos —
    match normalizado exacto contra Vacaciones/Danwor Day/Cumpleaños, o
    `ABSENCE_CONCEPT_OTROS` para cualquier otro valor (incluido vacío). El
    valor de entrada (`raw_motivo`) NUNCA se retiene más allá de esta
    llamada — el caller (`_parse_absence_workbook`) solo guarda el
    resultado clasificado en `AbsencePeriod.concept`."""
    return _ABSENCE_KNOWN_CONCEPTS.get(_normalize_label(raw_motivo), ABSENCE_CONCEPT_OTROS)


def _cell_to_iso_date(value) -> str:
    """Normaliza una celda de fecha a ISO `YYYY-MM-DD` (lo que espera el service
    `_parse_date`). Soporta celdas Excel nativas (date/datetime) y texto
    `dd/mm/yyyy`. Cualquier otra cosa → "" (el período se descarta fail-soft)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _absence_hours_from_dias(value) -> int | None:
    """Traduce la celda `Días` a HORAS de display (`Días × 8`), robustamente y
    SIN retener el string crudo. Acepta int (`10`), float integral (`10.0`),
    texto numérico (`"10"`) y texto con sufijo (`"10 días"`); toma el primer
    número no-negativo (coma o punto decimal). Un valor ausente/malformado/
    negativo → `None` (el período sigue siendo válido; la etiqueta cae al
    concepto pelado, sin horas). NUNCA se deriva de días hábiles del rango
    Desde/Hasta — `Días` ya puede reflejar el calendario de negocio de la
    fuente. Devuelve int (horas), integral por construcción salvo Días
    fraccionario, donde se redondea (display-only)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        days = float(value)
    else:
        match = re.search(r"\d+(?:[.,]\d+)?", str(value))
        if match is None:
            return None
        days = float(match.group(0).replace(",", "."))
    if days < 0:
        return None
    return int(round(days * ABSENCE_HOURS_PER_DAY))


def _resolve_absence_site_id(access_token: str) -> str:
    """Resuelve SOLO el `site_id` del sitio Operaciones — extraído de
    `_download_absence_workbook` (SHARED SOURCE ACQUISITION corrective, ver
    HANDOFF) para que el camino con eTag cache pueda resolverlo UNA sola vez
    y reusarlo tanto para la metadata (`stage="metadata"`) como para el
    download (`stage="download"`), nunca dos resoluciones de sitio por
    adquisición."""
    site = _call_absence_graph("site", lambda: microsoft365.graph_get_json(
        f"{GRAPH_BASE}/sites/{ABSENCE_SITE_PATH}", access_token,
    ))
    site_id = str((site or {}).get("id") or "")
    if not site_id:
        raise AbsenceSchemaError(stage="site", missing_fields=["site_id"], ambiguous_fields=[])
    return site_id


def _absence_workbook_metadata(site_id: str, access_token: str) -> dict | None:
    """GET de METADATA del driveItem del XLSX de Ausencias — SIN `:content`
    (no descarga el archivo), 1 llamada Graph barata que expone
    `eTag`/`lastModifiedDateTime`/`size`/`id` (SHARED SOURCE ACQUISITION
    corrective §5, ver HANDOFF — MISMO patrón que
    `reporting_repository._facturadas_workbook_metadata`, instancia propia
    de Staffing, nunca cross-import). Fail-soft: cualquier falla de
    transporte devuelve `None` — el caller cae al camino SIN cache
    (descarga siempre fresca), MISMO comportamiento que existía antes de
    esta fase."""
    try:
        return _call_absence_graph("metadata", lambda: microsoft365.graph_get_json(
            f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{quote(ABSENCE_XLSX_PATH)}", access_token,
        ))
    except AbsenceGraphError:
        return None


def _download_absence_workbook_bytes(site_id: str, access_token: str) -> bytes:
    """Descarga los bytes del XLSX autoritativo vía Graph, dado un
    `site_id` YA resuelto — `root:/{path}:/content`. `urllib` sigue el 302 a
    la URL pre-autenticada (ver `microsoft365.graph_get_bytes`). NO persiste
    el archivo. Fallas de transporte se envuelven en `AbsenceGraphError`."""
    return _call_absence_graph("download", lambda: microsoft365.graph_get_bytes(
        f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{quote(ABSENCE_XLSX_PATH)}:/content",
        access_token,
    ))


def _download_absence_workbook(access_token: str) -> bytes:
    """Descarga los bytes del XLSX autoritativo vía Graph: resuelve el sitio
    Operaciones (mecanismo de sitio establecido) → drive de documentos por default →
    `root:/{path}:/content`. `urllib` sigue el 302 a la URL pre-autenticada (ver
    `microsoft365.graph_get_bytes`). NO persiste el archivo. Fallas de transporte se
    envuelven en `AbsenceGraphError`.

    Delega en `_resolve_absence_site_id`/`_download_absence_workbook_bytes`
    (SHARED SOURCE ACQUISITION corrective, ver HANDOFF) — comportamiento
    EXACTO de siempre, sin cache: sigue siendo el camino que usa
    `get_absences(cache_scope=None)`."""
    site_id = _resolve_absence_site_id(access_token)
    return _download_absence_workbook_bytes(site_id, access_token)


def _absence_workbook_version(item: dict) -> str:
    """Clave de versión ESTABLE de un driveItem — `eTag` (preferido) o, si
    faltara, `lastModifiedDateTime:size` (fallback conservador — nunca se
    asume inmutabilidad sin chequeo de versión). MISMA fórmula que
    `reporting_repository._drive_item_version` — duplicada deliberadamente
    (instancia propia de Staffing, ver comentario de
    `_STAFFING_DELTA_CACHE`/`_STAFFING_XLSX_CACHE` más arriba y el mismo
    criterio ya usado para `_odata_quote`/`_list_items_url`: nunca un import
    cruzado de un helper privado del otro módulo)."""
    etag = str(item.get("eTag") or "").strip()
    if etag:
        return f"etag:{etag}"
    lm = str(item.get("lastModifiedDateTime") or "").strip()
    size = str(item.get("size") or "").strip()
    return f"lm:{lm}:{size}"


def _parse_absence_workbook(data: bytes) -> tuple[list, dict]:
    """Parsea el workbook EN MEMORIA (openpyxl read-only/data-only) → períodos
    elegibles + funnel de conteos. Worksheet plano (header row + data rows), sin
    requerir una Tabla de Excel. Fail-soft: workbook inválido / worksheet o headers
    faltantes → `AbsenceSchemaError` (el router lo trata como falla no-bloqueante,
    Staffing sigue funcionando); una fila inválida (sin nombre/fecha) se descarta sin
    romper el resto. `consultant_name` = "Nombre Apellido" (el join normaliza, no
    reordena). Elegibilidad = SOLO `Estado == "Aprobada"` — el Motivo YA NO es un
    gate (a diferencia del overlay original de Vacaciones): se CLASIFICA en un
    concepto canónico (`_classify_absence_concept`) y el valor crudo se descarta de
    inmediato. NUNCA retiene CUIL/Legajo/Días/comentarios/etiquetas/Motivo crudo."""
    funnel = {"worksheet_found": 0, "xlsx_rows": 0, "aprobadas_rows": 0, "eligible_periods": 0}
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — workbook inválido/corrupto
        raise AbsenceSchemaError(stage="workbook", missing_fields=["workbook"], ambiguous_fields=[]) from None
    try:
        if ABSENCE_WORKSHEET_NAME not in workbook.sheetnames:
            raise AbsenceSchemaError(stage="worksheet", missing_fields=[ABSENCE_WORKSHEET_NAME], ambiguous_fields=[])
        worksheet = workbook[ABSENCE_WORKSHEET_NAME]
        funnel["worksheet_found"] = 1
        row_iter = worksheet.iter_rows(values_only=True)
        try:
            header_row = next(row_iter)
        except StopIteration:
            raise AbsenceSchemaError(stage="headers", missing_fields=list(_ABSENCE_REQUIRED_HEADERS), ambiguous_fields=[])
        header_index: dict[str, int] = {}
        for idx, cell in enumerate(header_row or ()):
            name = "" if cell is None else str(cell).strip()
            if name and name not in header_index:
                header_index[name] = idx
        missing = [h for h in _ABSENCE_REQUIRED_HEADERS if h not in header_index]
        if missing:
            raise AbsenceSchemaError(stage="headers", missing_fields=missing, ambiguous_fields=[])

        i_ape, i_nom = header_index["Apellido"], header_index["Nombre"]
        i_mot, i_est = header_index["Motivo"], header_index["Estado"]
        i_desde, i_hasta = header_index["Desde"], header_index["Hasta"]
        i_dias = header_index.get(_ABSENCE_DIAS_HEADER)  # OPCIONAL (solo horas)
        periods: list = []
        for row_num, row in enumerate(row_iter, start=1):
            if row is None or not any(cell is not None and str(cell).strip() for cell in row):
                continue
            funnel["xlsx_rows"] += 1

            def _cell(idx):
                value = row[idx] if idx < len(row) else None
                return "" if value is None else str(value).strip()

            if _cell(i_est) != ABSENCE_ESTADO_VALUE:
                continue
            funnel["aprobadas_rows"] += 1

            consultant_name = " ".join(part for part in (_cell(i_nom), _cell(i_ape)) if part)
            start = _cell_to_iso_date(row[i_desde] if i_desde < len(row) else None)
            end = _cell_to_iso_date(row[i_hasta] if i_hasta < len(row) else None)
            if not consultant_name or not start or not end:
                continue
            funnel["eligible_periods"] += 1
            # `Días` es SOLO para la etiqueta de horas (×8); su ausencia o
            # malformación NO invalida el período (fail-soft → horas None). La
            # geometría de la barra sigue siendo exclusivamente start/end.
            dias_cell = row[i_dias] if (i_dias is not None and i_dias < len(row)) else None
            # `_cell(i_mot)` se usa UNA vez, acá, solo para clasificar — el
            # string crudo nunca llega a `AbsencePeriod` (privacy boundary).
            periods.append(AbsencePeriod(
                source_id=f"xlsx:{row_num}", consultant_name=consultant_name,
                start_date=start, end_date=end,
                concept=_classify_absence_concept(_cell(i_mot)),
                hours=_absence_hours_from_dias(dias_cell),
            ))
        return periods, funnel
    finally:
        workbook.close()


@dataclass(frozen=True)
class AbsencePeriod:
    """Un período de ausencia ELEGIBLE (Estado == "Aprobada"), con
    `consultant_name` construido como "Nombre Apellido" tal cual vino del
    XLSX, sin normalizar ni resolver contra Colaboradores todavía — ese join
    determinístico vive en `StaffingService`, ver ahí. `start_date`/
    `end_date` ya llegan como ISO `YYYY-MM-DD` (el service las parsea a
    `date` y define la geometría de la barra, fin inclusive). `concept` es
    el resultado YA CLASIFICADO de Motivo (`Vacaciones`/`Danwor Day`/
    `Cumpleaños`/`Otros`, ver `_classify_absence_concept`) — el Motivo crudo
    NUNCA llega acá (privacy boundary). `hours` es metadata de DISPLAY
    (`Días × 8`, ver `_absence_hours_from_dias`): `None` cuando `Días` falta
    o es malformado — el período sigue siendo válido y la barra igual se
    dibuja, solo cae la etiqueta de horas. NUNCA participa de
    allocation/Horaspormes/KPIs."""

    source_id: str
    consultant_name: str
    start_date: str | None
    end_date: str | None
    concept: str
    hours: int | None = None


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


def _list_items_delta_url(site_id: str, list_id: str, select_fields: list[str] | None) -> str:
    """MISMO patrón de construcción que `_list_items_url`
    (`$expand=fields($select=...)`) pero contra `/items/delta` — SOLO para
    el sync frío/resync (SHARED SOURCE ACQUISITION corrective, ver HANDOFF).
    Un `@odata.deltaLink` ya devuelto por Graph se reusa TAL CUAL para el
    sync warm — nunca se reconstruye. MISMA fórmula que
    `reporting_repository._list_items_delta_url`, duplicada acá (instancia
    propia de Staffing, ver comentario de `_STAFFING_DELTA_CACHE`)."""
    url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items/delta?$expand=fields"
    if select_fields:
        cols = ",".join(select_fields)
        url += f"($select={quote(cols)})"
    return url


def _odata_quote(value: str) -> str:
    """Escape mínimo de literal OData (comilla simple → doble comilla
    simple) — `value` siempre es una constante interna nuestra (nunca input
    de usuario), pero escapar es gratis y correcto igual."""
    return value.replace("'", "''")


class StaffingGraphError(Exception):
    """Envoltorio SEGURO de una falla de transporte Graph durante el sync
    delta de fuentes compartidas de Staffing (Proyectos/Colaboradores/
    Cliente, SHARED SOURCE ACQUISITION corrective, ver HANDOFF) — expone
    SOLO `stage`/`status_code`, nunca la excepción cruda de `urllib`.
    Instancia PROPIA de Staffing, análoga a
    `reporting_repository.ReportingGraphError` — nunca importada de ahí
    (mismo criterio ya usado para `_odata_quote`/`_list_items_url`: evita
    acoplar un módulo al privado del otro). Distinta de `AbsenceGraphError`
    (Ausencias mantiene su propio vocabulario, sin cambios) — esta es SOLO
    para el camino delta de dimensiones compartidas."""

    def __init__(self, stage: str, status_code: int | None):
        self.stage = stage
        self.status_code = status_code
        super().__init__(f"Staffing Graph error — stage={stage} status_code={status_code}")


class StaffingSourceError(Exception):
    """Falla NO transitoria del sync delta (p. ej. Graph nunca devolvió
    `@odata.deltaLink` tras completar un sync — fuente rota/vacía). `stage`
    es un label seguro, nunca datos de negocio."""

    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        super().__init__(f"Staffing source error — stage={stage}" + (f": {detail}" if detail else ""))


def _call_staffing_graph(stage: str, fn: Callable[[], object]) -> object:
    """MISMO criterio que `reporting_repository._call_graph` (instancia
    propia, ver `StaffingGraphError`) — incluye desde el día uno el fix de
    resiliencia ya probado ahí (Reporting Fase 3B, ver HANDOFF §9): un
    timeout de LECTURA llega como `TimeoutError` crudo, `urllib` nunca lo
    envuelve en `URLError`. `status_code=None` (URLError/TimeoutError) es la
    señal de "sin respuesta HTTP recibida en absoluto" que `_delta_sync_list`
    usa para decidir su fallback — nunca un status code HTTP real
    reclasificado."""
    try:
        return fn()
    except urllib.error.HTTPError as exc:
        raise StaffingGraphError(stage=stage, status_code=exc.code) from None
    except urllib.error.URLError:
        raise StaffingGraphError(stage=stage, status_code=None) from None
    except TimeoutError:
        raise StaffingGraphError(stage=stage, status_code=None) from None


# Instancias PROPIAS de Staffing (SHARED SOURCE ACQUISITION corrective, ver
# comentario extenso junto a los imports arriba) — proceso-lifetime, módulo-
# level a propósito: los tests las reemplazan vía
# `monkeypatch.setattr(repo_mod, "_STAFFING_DELTA_CACHE", ...)`, MISMO
# criterio testeable que `reporting_repository._DELTA_CACHE`/
# `_TIME_LOGS_HISTORICAL_CACHE`. `max_entries` por defecto (40/40) — el
# universo de Staffing (Proyectos/Colaboradores/Cliente/Ausencias del sitio
# Gestión/Operaciones) es de un orden de magnitud comparable o menor al de
# Reporting, sin necesidad demostrada de un tope mayor.
_STAFFING_DELTA_CACHE = DeltaCache()
_STAFFING_XLSX_CACHE = TimeLogsHistoricalCache()


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
    ):
        self.site_id = site_id if site_id is not None else config.SHAREPOINT_GESTION_SITE_ID
        self.asignaciones_list_id = asignaciones_list_id or config.SHAREPOINT_ASIGNACIONES_LIST_ID
        self.proyectos_list_id = proyectos_list_id or config.SHAREPOINT_PROYECTOS_LIST_ID
        self.colaboradores_list_id = colaboradores_list_id or config.SHAREPOINT_COLABORADORES_LIST_ID
        self.cliente_list_id = cliente_list_id or config.SHAREPOINT_CLIENTE_LIST_ID
        self.field_mapping = field_mapping if field_mapping is not None else StaffingFieldMapping.from_config()
        # Diagnóstico de la ÚLTIMA llamada a `get_allocations` (pre-PR
        # corrective, ver HANDOFF) — rows/pages/elapsed_ms/filter_used/
        # fallback_used por fuente, NUNCA token/payload/PII. Instancia
        # nueva por request (ver `webapp/routers/staffing.py`), así que no
        # hay estado compartido entre requests concurrentes. `get_absences`/
        # `get_consultants` reusan el MISMO diccionario (keys distintas,
        # "ausencias"/"colaboradores_identity") — nunca lo resetean, para
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

    # --- DELTA-SYNC genérico (SHARED SOURCE ACQUISITION corrective, ver ------
    # HANDOFF) — Proyectos/Colaboradores/Cliente, reusado por
    # `get_allocations` (Initial/Historial) Y `get_consultants` (identidad
    # de Ausencias). MISMO patrón probado en
    # `reporting_repository._delta_apply_pages`/`_delta_cold_sync`/
    # `_delta_warm_sync`/`_delta_sync_list` — transporte+estado puro, CERO
    # canonicalización de fila de negocio (cada caller sigue leyendo
    # `it.get("fields")` a su manera). Reimplementado acá (nunca importado
    # de `reporting_repository`, ver comentario junto a `_STAFFING_DELTA_CACHE`)
    # sobre `StaffingGraphError`/`_call_staffing_graph` propios de Staffing.

    def _delta_apply_pages(
        self, url: str, working: dict[str, dict], access_token: str, *, stage: str,
    ) -> tuple[str, int, int, int]:
        """Sigue `@odata.nextLink` aplicando cada página sobre `working`
        (mutado IN-PLACE — el caller decide si es un dict nuevo vacío (cold)
        o una COPIA del estado previo (warm)). Un item con `@removed` se
        remueve; cualquier otro se agrega/reemplaza. Devuelve
        `(delta_link, pages, changes, deletes)`."""
        pages = changes = deletes = 0
        delta_link = ""
        while url:
            payload = _call_staffing_graph(
                f"{stage}_delta", lambda u=url: microsoft365.graph_get_json(u, access_token),
            )
            pages += 1
            for item in payload.get("value") or []:
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                if "@removed" in item:
                    if working.pop(item_id, None) is not None:
                        deletes += 1
                else:
                    working[item_id] = item
                    changes += 1
            next_link = str(payload.get("@odata.nextLink") or "").strip()
            if next_link:
                url = next_link
                continue
            delta_link = str(payload.get("@odata.deltaLink") or "").strip()
            url = ""
        if not delta_link:
            raise StaffingSourceError(
                f"{stage}_delta", "Graph no devolvió @odata.deltaLink tras completar el sync",
            )
        return delta_link, pages, changes, deletes

    def _delta_cold_sync(
        self, site_id: str, list_id: str, select_fields: list[str] | None, access_token: str, *, stage: str,
    ) -> tuple[dict, int]:
        """Sync COMPLETO desde cero — ante CUALQUIER falla del intento con
        `$select`, un único reintento completo sin `$select` (MISMO
        fallback que `_fetch_asignaciones`/Reporting). El resultado recién
        se arma en variables LOCALES — nunca se publica un estado
        parcialmente sincronizado (`DeltaCache.sync` solo adopta el
        `return` de esta función si termina sin excepción)."""
        url = _list_items_delta_url(site_id, list_id, select_fields)
        try:
            working: dict[str, dict] = {}
            delta_link, pages, _changes, _deletes = self._delta_apply_pages(url, working, access_token, stage=stage)
        except Exception:  # noqa: BLE001 — fallback de transporte (select inválido), nunca una regla de negocio
            url = _list_items_delta_url(site_id, list_id, None)
            working = {}
            delta_link, pages, _changes, _deletes = self._delta_apply_pages(url, working, access_token, stage=stage)
        return {"rows": working, "delta_link": delta_link}, pages

    def _delta_warm_sync(self, current: dict, access_token: str, *, stage: str) -> tuple[dict, int]:
        """Aplica el checkpoint sobre una COPIA del estado previo — el dict
        original de `current["rows"]` nunca se muta hasta que este método
        retorna con éxito (una excepción acá deja el estado previo
        intacto, ver `DeltaCache.sync`)."""
        working = dict(current["rows"])
        delta_link, pages, _changes, _deletes = self._delta_apply_pages(
            current["delta_link"], working, access_token, stage=stage,
        )
        return {"rows": working, "delta_link": delta_link}, pages

    def _delta_sync_list(
        self, *, site_id: str, list_id: str, select_fields: list[str] | None, cache_scope: str, stage: str,
        access_token: str, legacy_fetch_fn: Callable[[], list[dict]],
    ) -> tuple[list[dict], str, int]:
        """Punto de entrada COMPARTIDO del sync delta — single-flight por
        `(cache_scope, site_id, list_id)` vía `_STAFFING_DELTA_CACHE`.
        Primera llamada para esta key ⇒ sync completo. Llamadas siguientes
        ⇒ checkpoint sobre el `deltaLink` guardado; ante `410 Gone` (delta
        token expirado) se descarta SOLO este estado y se hace UN resync
        completo controlado (nunca un loop).

        RESILIENCY: `StaffingGraphError.status_code is None` (sin respuesta
        HTTP recibida en absoluto — ver `_call_staffing_graph`) es la ÚNICA
        señal que dispara el fallback a `legacy_fetch_fn` (el fetch completo
        pre-delta EXACTO de esta fuente, provisto por el caller). Un 410 va
        a resync (arriba); cualquier otro status code real o excepción que
        no sea `StaffingGraphError` se propaga TAL CUAL. El estado delta
        cacheado NUNCA se toca en el camino de fallback: `DeltaCache.sync`
        ya garantiza que una excepción de `sync_fn` deja el estado previo
        exactamente como estaba.

        Devuelve `(rows, mode, pages)` — `mode` ∈
        {"delta_cold", "delta_warm", "delta_fallback"} para `last_diagnostics`
        (§10 del ticket). Un caller que solo ESPERA el resultado de un sync
        concurrente ya en curso (single-flight "waiter") nunca dispara una
        lectura propia — se etiqueta `"delta_warm"` (comparte el mismo
        criterio que "no pagó una lectura completa")."""
        key = (cache_scope, site_id, list_id)
        diag = {"mode": "", "pages": 0}

        def sync_fn(current):
            if current is None:
                state, pages = self._delta_cold_sync(site_id, list_id, select_fields, access_token, stage=stage)
                diag["mode"], diag["pages"] = "delta_cold", pages
                return state
            try:
                state, pages = self._delta_warm_sync(current, access_token, stage=stage)
                diag["mode"], diag["pages"] = "delta_warm", pages
                return state
            except StaffingGraphError as exc:
                if exc.status_code == 410:
                    state, pages = self._delta_cold_sync(
                        site_id, list_id, select_fields, access_token, stage=stage,
                    )
                    diag["mode"], diag["pages"] = "delta_cold", pages
                    return state
                raise

        try:
            state = _STAFFING_DELTA_CACHE.sync(key, sync_fn)
        except StaffingGraphError as exc:
            if exc.status_code is not None:
                raise
            rows = legacy_fetch_fn()
            return rows, "delta_fallback", 0
        mode = diag["mode"] or "delta_warm"
        return list(state["rows"].values()), mode, diag["pages"]

    def _timed_delta_fetch(
        self, diagnostics_key: str, *, site_id: str, list_id: str, select_fields: list[str],
        cache_scope: str, stage: str, access_token: str, legacy_fetch_fn: Callable[[], list[dict]],
    ) -> list[dict]:
        """Wrapper de diagnóstico ANÁLOGO a `_timed_fetch` para el camino
        delta — MISMAS keys (`rows`/`pages`/`elapsed_ms`, §10 del ticket:
        "existing elapsed_ms metrics must remain comparable before/after"),
        más `acquisition` para hacer visible el modo sin reemplazar el
        contrato existente."""
        started = time.monotonic()
        items, mode, pages = self._delta_sync_list(
            site_id=site_id, list_id=list_id, select_fields=select_fields, cache_scope=cache_scope,
            stage=stage, access_token=access_token, legacy_fetch_fn=legacy_fetch_fn,
        )
        self.last_diagnostics[diagnostics_key] = {
            "rows": len(items), "pages": pages,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "acquisition": mode,
        }
        return items

    def _fetch_proyectos_delta(
        self, site_id: str, mapping: StaffingFieldMapping, access_token: str, *, cache_scope: str,
    ) -> list[dict]:
        """Proyectos vía delta — field-select union: Proyectos SOLO tiene UN
        consumidor en Staffing (`get_allocations`, que únicamente lee
        `proyecto_title`/`proyecto_empresa_lookup`), así que no hay
        ambigüedad de superset. `legacy_fetch_fn` reproduce EXACTO el fetch
        pre-delta de siempre (con o sin `$select`, según si
        `proyecto_empresa_lookup` está configurado) para el fallback
        transitorio."""
        select_fields = [mapping.proyecto_title]
        if mapping.proyecto_empresa_lookup:
            select_fields.append(mapping.proyecto_empresa_lookup)

        def legacy_fetch_fn() -> list[dict]:
            if mapping.proyecto_empresa_lookup:
                return microsoft365.graph_get_paged_values(
                    _list_items_url(site_id, self.proyectos_list_id, select_fields), access_token,
                )
            return microsoft365.graph_get_paged_values(
                _list_items_url(site_id, self.proyectos_list_id), access_token,
            )

        return self._timed_delta_fetch(
            "proyectos", site_id=site_id, list_id=self.proyectos_list_id, select_fields=select_fields,
            cache_scope=cache_scope, stage="proyectos", access_token=access_token, legacy_fetch_fn=legacy_fetch_fn,
        )

    def _fetch_colaboradores_delta(
        self, diagnostics_key: str, site_id: str, mapping: StaffingFieldMapping, access_token: str,
        *, cache_scope: str,
    ) -> list[dict]:
        """Colaboradores vía delta — field-select UNION corrective (§3 del
        ticket, "no repetir el CLIENTE_SELECT_FIELDS mistake"): la MISMA
        lista se lee desde `get_allocations` (title + `disponibilidad`
        OPCIONAL) Y desde `get_consultants`/identidad de Ausencias (SOLO
        title). El select de `get_allocations` YA ES el superset exacto de
        ambos (title, más `disponibilidad` si está configurado) — reusado
        acá TAL CUAL para los dos consumidores, así que ninguno pierde un
        campo que necesita por el select del otro (`$select` solo puede
        NARROWEAR columnas, nunca filas — probado leyendo cada línea que
        consume campos de Colaboradores en ambos consumidores, no asumido).
        `diagnostics_key` distingue `"colaboradores"` (get_allocations) de
        `"colaboradores_identity"` (get_consultants) — MISMAS 2 keys que ya
        usaba `_timed_fetch`, comportamiento de logging sin cambios."""
        select_fields = [mapping.colaborador_title]
        if mapping.colaborador_disponibilidad:
            select_fields.append(mapping.colaborador_disponibilidad)

        def legacy_fetch_fn() -> list[dict]:
            return microsoft365.graph_get_paged_values(
                _list_items_url(site_id, self.colaboradores_list_id, select_fields), access_token,
            )

        return self._timed_delta_fetch(
            diagnostics_key, site_id=site_id, list_id=self.colaboradores_list_id, select_fields=select_fields,
            cache_scope=cache_scope, stage="colaboradores", access_token=access_token,
            legacy_fetch_fn=legacy_fetch_fn,
        )

    def _fetch_cliente_delta(
        self, site_id: str, mapping: StaffingFieldMapping, access_token: str, *, cache_scope: str,
    ) -> list[dict]:
        """Cliente vía delta — SOLO `get_allocations` lee Cliente en
        Staffing (`cliente_title` únicamente), sin ambigüedad de superset."""
        select_fields = [mapping.cliente_title]

        def legacy_fetch_fn() -> list[dict]:
            return microsoft365.graph_get_paged_values(
                _list_items_url(site_id, self.cliente_list_id, select_fields), access_token,
            )

        return self._timed_delta_fetch(
            "cliente", site_id=site_id, list_id=self.cliente_list_id, select_fields=select_fields,
            cache_scope=cache_scope, stage="cliente", access_token=access_token, legacy_fetch_fn=legacy_fetch_fn,
        )

    def get_allocations(
        self, access_token: str, *, assignment_scope: str = "operational", cache_scope: str | None = None,
    ) -> list[StaffingAllocationRecord]:
        """`cache_scope` (SHARED SOURCE ACQUISITION corrective, ver HANDOFF)
        — `None` (default) preserva el comportamiento EXACTO de siempre:
        cada llamada relee Proyectos/Colaboradores/Cliente completos, sin
        cache. Con un `cache_scope` real, esas 3 dimensiones se leen vía
        `DeltaCache` (cold la primera vez por `(cache_scope, site_id,
        list_id)`, checkpoint incremental después) — Asignaciones NUNCA
        pasa por acá (§7 del ticket: Initial y Historial son adquisiciones
        de hecho separadas, nunca cacheadas/reusadas entre sí)."""
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
        if cache_scope is not None:
            proyectos = self._fetch_proyectos_delta(site_id, mapping, access_token, cache_scope=cache_scope)
        elif mapping.proyecto_empresa_lookup:
            proyectos = self._timed_fetch(
                "proyectos",
                _list_items_url(
                    site_id, self.proyectos_list_id,
                    [mapping.proyecto_title, mapping.proyecto_empresa_lookup],
                ), access_token,
            )
            self.last_diagnostics["proyectos"]["acquisition"] = "legacy"
        else:
            proyectos = self._timed_fetch(
                "proyectos", _list_items_url(site_id, self.proyectos_list_id), access_token,
            )
            self.last_diagnostics["proyectos"]["acquisition"] = "legacy"
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
        if cache_scope is not None:
            colaboradores = self._fetch_colaboradores_delta(
                "colaboradores", site_id, mapping, access_token, cache_scope=cache_scope,
            )
        else:
            colaboradores_fields = [mapping.colaborador_title]
            if mapping.colaborador_disponibilidad:
                colaboradores_fields.append(mapping.colaborador_disponibilidad)
            colaboradores = self._timed_fetch(
                "colaboradores",
                _list_items_url(site_id, self.colaboradores_list_id, colaboradores_fields), access_token,
            )
            self.last_diagnostics["colaboradores"]["acquisition"] = "legacy"

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
            if cache_scope is not None:
                clientes = self._fetch_cliente_delta(site_id, mapping, access_token, cache_scope=cache_scope)
            else:
                clientes = self._timed_fetch(
                    "cliente",
                    _list_items_url(site_id, self.cliente_list_id, [mapping.cliente_title]), access_token,
                )
                self.last_diagnostics["cliente"]["acquisition"] = "legacy"
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

    def get_absences(self, access_token: str, *, cache_scope: str | None = None) -> list[AbsencePeriod]:
        """Ausencias APROBADAS desde el XLSX autoritativo en SharePoint
        (Approved Absence Overlay, generalización del Vacation Timeline
        Overlay original — ver HANDOFF): descarga "Solicitudes (2).xlsx" del
        sitio Operaciones y filtra `Estado == "Aprobada"` — CUALQUIER Motivo
        aprobado es elegible, clasificado server-side en uno de 4 conceptos
        canónicos (ver `_classify_absence_concept`). `consultant_name` =
        "Nombre Apellido" (el join contra Colaboradores en `StaffingService`
        normaliza, no reordena). Filas sin nombre o sin ambas fechas se
        descartan fail-soft por fila. NO persiste el archivo.

        `cache_scope` (SHARED SOURCE ACQUISITION corrective §5, ver HANDOFF)
        — `None` (default) preserva el comportamiento EXACTO de siempre: 1
        GET de sitio + 1 download+parse SIEMPRE. Con un `cache_scope` real:
        1 GET de sitio + 1 GET de metadata SIEMPRE (barato, necesario para
        saber si hay HIT) — el download+parse (caro) SOLO ocurre en un MISS
        de versión (`eTag` preferido, `lastModifiedDateTime:size` fallback,
        ver `_absence_workbook_version`). El VALOR cacheado es el resultado
        YA PARSEADO (`periods`/`funnel`) — nunca los bytes crudos del XLSX,
        ni una fórmula de negocio nueva (el parseo sigue siendo
        `_parse_absence_workbook`, sin cambios). Semántica de negocio
        idéntica cold vs. warm (§8/§11 del ticket) — solo cambia CÓMO se
        adquiere la fuente.

        Embudo de diagnóstico (`self.last_diagnostics["ausencias"]`) — conteos
        PUROS por etapa (worksheet/xlsx_rows/Estado=Aprobada/períodos elegibles)
        para que UN refresh diga en qué etapa se cae a cero, sin loguear
        nombres/fechas/PII. Observación pura, sin regla de negocio. Más
        `workbook_hit` (`cache_scope` no-`None` únicamente) — `True` cuando
        NO se disparó una descarga de contenido para esta llamada (hit de
        cache o esperó una descarga concurrente de OTRO caller), `False`
        cuando esta llamada fue la que efectivamente descargó."""
        started = time.monotonic()
        site_id = _resolve_absence_site_id(access_token)
        if cache_scope is None:
            data = _download_absence_workbook_bytes(site_id, access_token)
            periods, funnel = _parse_absence_workbook(data)
            self.last_diagnostics["ausencias"] = {
                **funnel, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            }
            return periods

        metadata = _absence_workbook_metadata(site_id, access_token)
        if metadata is None:
            # Fail-soft (MISMO criterio que Facturadas/Reporting Fase 3):
            # sin metadata confiable, nunca se inventa una versión — camino
            # de siempre, sin cache.
            data = _download_absence_workbook_bytes(site_id, access_token)
            periods, funnel = _parse_absence_workbook(data)
            self.last_diagnostics["ausencias"] = {
                **funnel, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "workbook_hit": False,
            }
            return periods

        key = (cache_scope, str(metadata.get("id") or "") or ABSENCE_XLSX_PATH, _absence_workbook_version(metadata))

        def _compute() -> tuple[list, dict]:
            data = _download_absence_workbook_bytes(site_id, access_token)
            return _parse_absence_workbook(data)

        (periods, funnel), outcome = _STAFFING_XLSX_CACHE.get_or_compute(key, _compute)
        self.last_diagnostics["ausencias"] = {
            **funnel, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "workbook_hit": outcome != "computed",
        }
        return periods

    def get_consultants(self, access_token: str, *, cache_scope: str | None = None) -> list[tuple[str, str]]:
        """SOLO Colaboradores (id, nombre) — universo de identidad para el
        join determinístico de Ausencias (ver `StaffingService`), sin forzar
        una lectura completa de Asignaciones/Proyectos solo para resolver
        nombres. Reusa `colaborador_title` de `StaffingFieldMapping` — el
        mismo mapeo que ya requiere `get_allocations`, sin un campo nuevo.

        `cache_scope` (SHARED SOURCE ACQUISITION corrective §3, ver HANDOFF)
        — `None` preserva el comportamiento EXACTO de siempre. Con un
        `cache_scope` real, reusa `_fetch_colaboradores_delta` — la MISMA
        key `(cache_scope, site_id, colaboradores_list_id)` que
        `get_allocations` ya pudo haber calentado en la MISMA sesión, así
        que un consultor que ya vio Initial no vuelve a pagar una lectura
        completa de Colaboradores para Ausencias."""
        site_id = self._require_site_id()
        title_field = self.field_mapping.colaborador_title
        if not title_field:
            raise RuntimeError(
                "Falta configurar el mapeo de campos de Staffing (Batch 2, ver "
                "HANDOFF) — colaborador_title es requisito de get_consultants "
                "igual que de get_allocations."
            )
        mapping = self.field_mapping
        if cache_scope is not None:
            items = self._fetch_colaboradores_delta(
                "colaboradores_identity", site_id, mapping, access_token, cache_scope=cache_scope,
            )
        else:
            items = self._timed_fetch(
                "colaboradores_identity",
                _list_items_url(site_id, self.colaboradores_list_id, [title_field]), access_token,
            )
            self.last_diagnostics["colaboradores_identity"]["acquisition"] = "legacy"
        result: list[tuple[str, str]] = []
        for item in items:
            cid = str(item.get("id") or "")
            name = _resolved_title(item, title_field)
            if cid and name:
                result.append((cid, name))
        return result
