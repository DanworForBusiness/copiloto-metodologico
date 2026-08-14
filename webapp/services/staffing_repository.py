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
AND `¿Aprobado CCHH? is True` (booleano estricto — `False`/`None`/ausente
excluye; ningún otro Tipo de Licencia entra). Devuelve `consultant_name`
CRUDO (`fields["Colaborador"]`, un campo de TEXTO simple — NO lookup, NO
requiere resolver un ID de Colaboradores) — el join de identidad
determinístico contra el universo de Colaboradores de Staffing vive en
`StaffingService` (ver ahí, sin cambios: sigue siendo join por nombre
normalizado). `get_consultants` lee SOLO Colaboradores (id + nombre, sin
Asignaciones/Proyectos) — universo de identidad para ese join.

FUENTE AUTORITATIVA — XLSX EN SHAREPOINT (owner authoritative source
corrective, ver HANDOFF): la suposición previa de una LISTA SharePoint
(`/items` + discovery de `/columns` + flag `¿Aprobado CCHH?`) era
INCORRECTA y quedó RETIRADA por completo. La fuente real es el archivo
`Solicitudes (2).xlsx` en la biblioteca de documentos del sitio Operaciones
(`General/Template Dashboard/`). `get_vacations` descarga ese workbook vía
Graph (`root:/{path}:/content`), lo parsea EN MEMORIA con openpyxl
(read-only/data-only, sin persistir) y aplica la semántica de negocio del
propio archivo: `Motivo == "Vacaciones"` AND `Estado == "Aprobada"`. Los
flags `Approved`/`¿Aprobado CCHH?` de la fuente-lista quedaron SUPERADOS —
no se exigen, no se OR-ean, no se invierten, no son fallback.

CONTRATO DE UBICACIÓN (module constants, no config — `config.py` no se
edita este batch): `VACATION_SITE_PATH`/`VACATION_XLSX_PATH`/
`VACATION_WORKSHEET_NAME` son la ubicación estable (sitio + ruta en el
drive), NO una URL de descarga temporal ni un id de driveItem.
`SHAREPOINT_VACACIONES_LIST_ID` quedó sin uso.

DIAGNÓSTICO SEGURO DE FALLAS (ver HANDOFF): `_call_vacation_graph(stage,
fn)` envuelve las llamadas Graph de Vacaciones (`stage="site"` para
resolver el sitio, `stage="download"` para bajar el workbook) — cualquier
`HTTPError`/`URLError` de `urllib` se traduce a `VacationGraphError(stage,
status_code)`, NUNCA deja escapar la excepción cruda. Un workbook
inválido / worksheet o headers faltantes levantan `VacationSchemaError`
(`stage="workbook"/"worksheet"/"headers"`, con labels de contrato, nunca
datos/PII). `VacationSchemaError` NO es `RuntimeError` a propósito: es un
problema de la fuente, NUNCA de configuración de deployment — el router lo
trata igual que cualquier otra falla de Graph (`graph_error`), nunca como
`configuration_incomplete`.

Ambos (`get_vacations`/`get_consultants`)
fail-soft ante cualquier excepción — el router captura el error y omite
el overlay, Asignaciones nunca se rompe."""
from __future__ import annotations

import io
import re
import time
import urllib.error
from datetime import date, datetime
from dataclasses import dataclass, fields as dataclass_fields
from typing import Callable
from urllib.parse import quote

import openpyxl

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
# criterio que `INACTIVE_STATUS_VALUE` arriba). Junto con
# `¿Aprobado CCHH? is True` (booleano estricto, ver `get_vacations`) es el
# ÚNICO par que habilita una fila para el timeline — cualquier otro Tipo de
# Licencia, o CCHH False/None/ausente, queda fuera.
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


class VacationSchemaError(Exception):
    """El workbook XLSX de Vacaciones no matchea el contrato esperado
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


def _call_vacation_graph(stage: str, fn: Callable[[], object]) -> object:
    """Ejecuta una llamada Graph de Vacaciones (`stage` = "site" |
    "download") envolviendo CUALQUIER falla de transporte en
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


# ── Staffing Vacation Timeline Overlay — FUENTE AUTORITATIVA: XLSX en SharePoint
# (owner authoritative source corrective, ver HANDOFF). La suposición previa de una
# LISTA SharePoint (`/items`+`/columns`) era INCORRECTA y quedó RETIRADA. La fuente
# real es "Solicitudes (2).xlsx" en la biblioteca de documentos del sitio Operaciones.
# Estas son la UBICACIÓN autoritativa y estable (sitio + ruta en el drive), NO una URL
# de descarga temporal ni un id de driveItem.
VACATION_SITE_PATH = "danwor.sharepoint.com:/sites/Operaciones"
VACATION_XLSX_PATH = "General/Template Dashboard/Solicitudes (2).xlsx"
VACATION_WORKSHEET_NAME = "Solicitudes"

# Semántica de negocio autoritativa del propio XLSX (reemplaza la investigación de
# flags Approved/¿Aprobado CCHH? de la fuente-lista, superada por el descubrimiento
# de la fuente real): elegible si Motivo == "Vacaciones" AND Estado == "Aprobada".
VACATION_ESTADO_VALUE = "Aprobada"

# Horas equivalentes por día de vacaciones — regla de negocio de DISPLAY
# autorizada por el owner (post-live-smoke UX corrective): la etiqueta del
# overlay muestra `Días × 8`. SOLO informativo — nunca toca allocation/
# Horaspormes/KPIs (ver `VacationPeriod.vacation_hours`).
VACATION_HOURS_PER_DAY = 8

# Headers mínimos requeridos del worksheet. Del set completo (14 columnas) solo
# se usan Apellido/Nombre/Motivo/Desde/Hasta/Estado como contrato de runtime, más
# `Días` OPCIONAL (solo para la etiqueta de horas; su ausencia NO invalida el
# período). CUIL/Legajo/comentarios/etiquetas NUNCA se retienen ni exponen; el
# valor crudo de `Días` tampoco (solo su producto ×8 sale al payload).
_VACATION_REQUIRED_HEADERS = ("Apellido", "Nombre", "Motivo", "Desde", "Hasta", "Estado")
_VACATION_DIAS_HEADER = "Días"


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


def _vacation_hours_from_dias(value) -> int | None:
    """Traduce la celda `Días` a HORAS de display (`Días × 8`), robustamente y
    SIN retener el string crudo. Acepta int (`10`), float integral (`10.0`),
    texto numérico (`"10"`) y texto con sufijo (`"10 días"`); toma el primer
    número no-negativo (coma o punto decimal). Un valor ausente/malformado/
    negativo → `None` (el período sigue siendo válido; la etiqueta cae a
    "Vacaciones" sin horas). NUNCA se deriva de días hábiles del rango
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
    return int(round(days * VACATION_HOURS_PER_DAY))


def _download_vacation_workbook(access_token: str) -> bytes:
    """Descarga los bytes del XLSX autoritativo vía Graph: resuelve el sitio
    Operaciones (mecanismo de sitio establecido) → drive de documentos por default →
    `root:/{path}:/content`. `urllib` sigue el 302 a la URL pre-autenticada (ver
    `microsoft365.graph_get_bytes`). NO persiste el archivo. Fallas de transporte se
    envuelven en `VacationGraphError`."""
    site = _call_vacation_graph("site", lambda: microsoft365.graph_get_json(
        f"{GRAPH_BASE}/sites/{VACATION_SITE_PATH}", access_token,
    ))
    site_id = str((site or {}).get("id") or "")
    if not site_id:
        raise VacationSchemaError(stage="site", missing_fields=["site_id"], ambiguous_fields=[])
    return _call_vacation_graph("download", lambda: microsoft365.graph_get_bytes(
        f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{quote(VACATION_XLSX_PATH)}:/content",
        access_token,
    ))


def _parse_vacation_workbook(data: bytes) -> tuple[list, dict]:
    """Parsea el workbook EN MEMORIA (openpyxl read-only/data-only) → períodos
    elegibles + funnel de conteos. Worksheet plano (header row + data rows), sin
    requerir una Tabla de Excel. Fail-soft: workbook inválido / worksheet o headers
    faltantes → `VacationSchemaError` (el router lo trata como falla no-bloqueante,
    Staffing sigue funcionando); una fila inválida (sin nombre/fecha) se descarta sin
    romper el resto. `consultant_name` = "Nombre Apellido" (el join normaliza, no
    reordena). NUNCA retiene CUIL/Legajo/Días/comentarios/etiquetas."""
    funnel = {"worksheet_found": 0, "xlsx_rows": 0, "vacaciones_rows": 0,
              "aprobadas_rows": 0, "eligible_periods": 0}
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — workbook inválido/corrupto
        raise VacationSchemaError(stage="workbook", missing_fields=["workbook"], ambiguous_fields=[]) from None
    try:
        if VACATION_WORKSHEET_NAME not in workbook.sheetnames:
            raise VacationSchemaError(stage="worksheet", missing_fields=[VACATION_WORKSHEET_NAME], ambiguous_fields=[])
        worksheet = workbook[VACATION_WORKSHEET_NAME]
        funnel["worksheet_found"] = 1
        row_iter = worksheet.iter_rows(values_only=True)
        try:
            header_row = next(row_iter)
        except StopIteration:
            raise VacationSchemaError(stage="headers", missing_fields=list(_VACATION_REQUIRED_HEADERS), ambiguous_fields=[])
        header_index: dict[str, int] = {}
        for idx, cell in enumerate(header_row or ()):
            name = "" if cell is None else str(cell).strip()
            if name and name not in header_index:
                header_index[name] = idx
        missing = [h for h in _VACATION_REQUIRED_HEADERS if h not in header_index]
        if missing:
            raise VacationSchemaError(stage="headers", missing_fields=missing, ambiguous_fields=[])

        i_ape, i_nom = header_index["Apellido"], header_index["Nombre"]
        i_mot, i_est = header_index["Motivo"], header_index["Estado"]
        i_desde, i_hasta = header_index["Desde"], header_index["Hasta"]
        i_dias = header_index.get(_VACATION_DIAS_HEADER)  # OPCIONAL (solo horas)
        periods: list = []
        for row_num, row in enumerate(row_iter, start=1):
            if row is None or not any(cell is not None and str(cell).strip() for cell in row):
                continue
            funnel["xlsx_rows"] += 1

            def _cell(idx):
                value = row[idx] if idx < len(row) else None
                return "" if value is None else str(value).strip()

            if _cell(i_mot) != VACATION_LEAVE_TYPE_VALUE:
                continue
            funnel["vacaciones_rows"] += 1
            if _cell(i_est) != VACATION_ESTADO_VALUE:
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
            periods.append(VacationPeriod(
                source_id=f"xlsx:{row_num}", consultant_name=consultant_name,
                start_date=start, end_date=end,
                vacation_hours=_vacation_hours_from_dias(dias_cell),
            ))
        return periods, funnel
    finally:
        workbook.close()


@dataclass(frozen=True)
class VacationPeriod:
    """Un período de vacaciones ELEGIBLE (Motivo == "Vacaciones" AND Estado ==
    "Aprobada"), con `consultant_name` construido como "Nombre Apellido" tal
    cual vino del XLSX, sin normalizar ni resolver contra Colaboradores todavía
    — ese join determinístico vive en `StaffingService`, ver ahí. `start_date`/
    `end_date` ya llegan como ISO `YYYY-MM-DD` (el service las parsea a `date` y
    define la geometría de la barra, fin inclusive). `vacation_hours` es
    metadata de DISPLAY (`Días × 8`, ver `_vacation_hours_from_dias`): `None`
    cuando `Días` falta o es malformado — el período sigue siendo válido y la
    barra igual se dibuja, solo cae la etiqueta de horas. NUNCA participa de
    allocation/Horaspormes/KPIs."""

    source_id: str
    consultant_name: str
    start_date: str | None
    end_date: str | None
    vacation_hours: int | None = None


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
        """Vacaciones APROBADAS desde el XLSX autoritativo en SharePoint (owner
        authoritative source corrective, ver HANDOFF): descarga "Solicitudes (2).xlsx"
        del sitio Operaciones y filtra `Motivo == "Vacaciones"` AND
        `Estado == "Aprobada"` (semántica de negocio del propio archivo — la
        investigación de flags Approved/CCHH de la fuente-lista quedó superada).
        `consultant_name` = "Nombre Apellido" (el join contra Colaboradores en
        `StaffingService` normaliza, no reordena). Filas sin nombre o sin ambas
        fechas se descartan fail-soft por fila. NO persiste el archivo.

        Embudo de diagnóstico (`self.last_diagnostics["vacaciones"]`) — conteos PUROS
        por etapa (worksheet/xlsx_rows/Motivo=Vacaciones/Estado=Aprobada/períodos
        elegibles) para que UN refresh diga en qué etapa se cae a cero, sin loguear
        nombres/fechas/PII. Observación pura, sin regla de negocio."""
        started = time.monotonic()
        data = _download_vacation_workbook(access_token)
        periods, funnel = _parse_vacation_workbook(data)
        self.last_diagnostics["vacaciones"] = {
            **funnel,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
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
