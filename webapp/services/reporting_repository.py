"""
reporting_repository.py — Adapter de acceso a datos de Reporting Integral
(Fase 1: SOLO Cartera). Boundary ÚNICO donde viven los nombres internos de
SharePoint / la mecánica de Graph — nada de esto sale de este módulo (ver
HANDOFF + artifacts/reporting-integral-v0/real-data-lineage.md).

Reusa la MISMA plomería que Staffing (`core/microsoft365.py`:
`graph_get_paged_values` con paginación `@odata.nextLink`, `graph_get_bytes`
para descargar el XLSX) y el MISMO token delegado del browser — NUNCA un
segundo cliente Graph, auth ni modelo de token.

Fuentes físicas de Cartera (todas en el sitio "Gestión",
`config.SHAREPOINT_GESTION_SITE_ID`):
  - Lista "Horas por proyectos" (hecho mensual): HsTrabajadas/HsVendidas/
    HsFacturadas por proyecto×mes. GUID en `config.SHAREPOINT_HORAS_PROYECTOS_LIST_ID`.
  - Lista "Proyectos" (`config.SHAREPOINT_PROYECTOS_LIST_ID`, ya usada por
    Staffing): TipodeProyecto→tipo, Estado, ID Proyecto Danwor, Año de Inicio.
  - Lista "Cliente" (`config.SHAREPOINT_CLIENTE_LIST_ID`, ya usada por
    Staffing): Cliente (nombre) + Client Partner.
  - XLSX "Horas Facturadas Proyectos-Período.xlsx" (biblioteca de documentos):
    tabla `Tabla24`, medida `Suma de Cantidad` = horas facturadas; join por
    nombre de proyecto normalizado (Upper/sin espacios) para la perspectiva
    Proyecto.

Este módulo SOLO accede + normaliza + une a nivel de fuente. Las medidas
derivadas de negocio (p. ej. `diff = certificadas - trabajadas`) y el split
Mantenimiento/Proyecto para el contrato del renderer viven en
`reporting_service.py`. NO devuelve JSON crudo de Graph.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import config
from core import microsoft365
from webapp.services import reporting_time_logs_durable
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# --- P0: waterfall de performance (owner live QA — Cartera ~17s, Horas
# Vendidas >40s) ------------------------------------------------------------
# INFO, temporal, para el smoke de performance del owner (ver HANDOFF).
# Loguea SOLO stage/elapsed_ms/item_count — NUNCA token, valores de
# negocio, ni JSON crudo de Graph (mismo criterio de seguridad que
# ReportingGraphError, que ya nunca expone la excepción cruda de urllib).
_perf_logger = logging.getLogger("danwor.reporting.perf")


class _StageTimer:
    """Cronómetro de etapas de UN fetch (Cartera/Batch A/Batch B/Time
    Logs/Rentabilidad). `stage(name, count)` marca el fin de una etapa y
    loguea su duración desde la marca ANTERIOR (nunca desde el inicio
    total — cada línea es el costo INCREMENTAL de esa etapa sola).
    `total()` loguea la duración completa del fetch."""

    def __init__(self, label: str):
        self.label = label
        self._start = time.perf_counter()
        self._stage_start = self._start

    def stage(self, name: str, count: int | None = None) -> None:
        now = time.perf_counter()
        elapsed_ms = round((now - self._stage_start) * 1000)
        suffix = f" items={count}" if count is not None else ""
        _perf_logger.info("reporting.perf %s.%s elapsed_ms=%s%s", self.label, name, elapsed_ms, suffix)
        self._stage_start = now

    def total(self) -> int:
        elapsed_ms = round((time.perf_counter() - self._start) * 1000)
        _perf_logger.info("reporting.perf %s.total elapsed_ms=%s", self.label, elapsed_ms)
        return elapsed_ms

# XLSX de Facturadas — ubicación estable como module constants (mismo criterio
# que los ABSENCE_* de staffing_repository: ruta en el drive, no config nueva).
FACTURADAS_XLSX_PATH = "General/Reportes Horas por Proyectos/Horas Facturadas Proyectos-Período.xlsx"
FACTURADAS_TABLE_NAME = "Tabla24"

# Nombres de columna FUENTE (derivados del M/TMDL del PBIP). Únicos internal
# names de SharePoint en toda la app de Reporting — si el runtime de Graph
# entrega otro internal name para un lookup (p. ej. `ProyectoLookupId`), se
# ajusta ACÁ, nunca en el service/JS (ver gaps en real-data-lineage.md).
# Graph expone las columnas lookup en `fields` como `<Name>LookupId` (id del
# target, confirmado en el live smoke); el aplanado del PBIP/Power Query mostraba
# `<Name>Id`. Se prueban AMBAS formas (convención DOCUMENTADA de Graph, no una
# suposición de un nombre puntual).
HP_PROYECTO_LOOKUP = ("ProyectoLookupId", "ProyectoId")   # Horas → Proyectos.id
HP_EMPRESA_LOOKUP = ("EmpresaLookupId", "EmpresaId")      # Horas → Cliente.id
HP_PROYECTO_TITLE = "Proyecto"      # fallback si Horas trajera el título aplanado
# Internal names REALES confirmados por el live smoke (SharePoint codifica ñ y
# espacios). Antes se usaban los nombres "lindos" del PBIP → salían en blanco.
HP_ANIO = "A_x00f1_o"               # "Año" codificado
HP_MES = "Mes"
HP_HS_TRABAJADAS = "HsTrabajadas"
HP_HS_VENDIDAS = "HsVendidas"
# certificadas ← HsFacturadas: el M del PBIP renombra `HsFacturadas` →
# "Hs certificadas" (línea 313 de Horas por proyectos.tmdl). Graph puede omitir
# el campo por-item cuando es null (no significa que la columna no exista). NO se
# inventa un fallback (nunca HsVendidas/Auditado/XLSX/etc.).
HP_HS_FACTURADAS = "HsFacturadas"

# Proyección exacta que `_cartera_facts_and_dimension` necesita de "Horas
# por proyectos" — extraído a constante de módulo (Fase 3A, ver HANDOFF)
# para que el sync delta (`_horas_delta_cold_sync`) use la MISMA lista,
# nunca una duplicada que pueda divergir.
HORAS_PROYECTOS_SELECT_FIELDS = [
    HP_ANIO, HP_MES, HP_HS_TRABAJADAS, HP_HS_VENDIDAS, HP_HS_FACTURADAS,
    *HP_PROYECTO_LOOKUP, *HP_EMPRESA_LOOKUP, HP_PROYECTO_TITLE,
]

PROY_ID_DANWOR = "ID_x0020_Proyecto_x0020_Danwor"   # "ID Proyecto Danwor" codificado
PROY_TIPO = "TipodeProyecto"        # "Tiempo & Materiales" ⇒ Mantenimiento
PROY_FECHA_INICIO = "FechadeInicio"
PROY_ESTADO = "Status"              # Proyectos expone `Status` (no "Estado")
PROY_EMPRESA_ID = "EmpresaId"
PROY_TITLE = "Title"                # nombre del proyecto (join desde Proyectos)
# MISMA proyección que `_cartera_facts_and_dimension` ya usaba inline —
# extraída a constante de módulo (Fase 3B, ver HANDOFF) para que el sync
# delta de Proyectos (`_fetch_proyectos_delta`) use la MISMA lista, nunca
# una duplicada que pueda divergir.
PROYECTOS_SELECT_FIELDS = [PROY_TITLE, PROY_TIPO, PROY_ESTADO, PROY_FECHA_INICIO, PROY_ID_DANWOR]

CLI_NOMBRE = "Title"                # nombre del cliente = Cliente.Title
# Client Partner es un LOOKUP de la lista Cliente (`ClientPartnerLookupId`). El
# target NO se asume por nombre (la relación de PMs es con Proyectos.PMId, no con
# Cliente) — se descubre de la metadata de columnas de Cliente (`lookup.listId`).
CLI_CP_LOOKUP = ("ClientPartnerLookupId", "ClientPartnerId")
CLI_CP_COLUMN_NAMES = ("ClientPartner",)
CLI_CP_DISPLAY_NAMES = ("Client Partner",)
PM_TITLE = "Title"                  # display del Client Partner = Title del target

# Batch A (Horas Vendidas/Participación/Calendario, ver artifacts/
# reporting-integral-v0/real-data-lineage.md sección 8) — columnas
# ADICIONALES de la MISMA lista Cliente ya leída arriba, ninguna lista
# nueva. Internal names codificados vía la MISMA convención SharePoint ya
# CONFIRMADA en este repositorio (espacio → `_x0020_`, ver PROY_ID_DANWOR/
# HP_ANIO arriba) — mecánica documentada, no una suposición semántica. Se
# prueban candidatos codificado + sin codificar (mismo criterio que
# `_first`) por si el runtime de Graph difiere. `ID Frecuencia` es una
# columna DERIVADA en el PBIP (no física) — el repositorio SOLO expone el
# físico `Frecuencia Actualizacion`; la derivación (`"1 Mensual"`, etc.)
# vive en el service (business shaping).
CLI_FRECUENCIA_ACT = ("Frecuencia_x0020_Actualizacion", "FrecuenciaActualizacion")
CLI_PERMITE_ADELANTO = ("Permite_x0020_Adelanto", "PermiteAdelanto")
CLI_CONDICION_PAGO = ("Condicion_x0020_de_x0020_Pago", "CondicionDePago")
CLI_CALENDARIO_ACT = ("Calendario_Act",)

# Salud de Cliente — internal names REALES confirmados por diagnóstico Graph
# en vivo del owner (no codificados a mano por convención, DADOS
# directamente — ver HANDOFF): columnas de texto categóricas puras de la
# MISMA lista Cliente, sin lookup, sin lista nueva.
CLI_SALUD_FINANCIERA = ("Salud_x0020_Financiera",)
CLI_SALUD_RELACION = ("Salud_x0020_Relaci_x00f3_n",)
CLI_SALUD_OPERATIVO = ("Salud_x0020_Operativo",)
CLI_SALUD_CONTINUIDAD = ("Salud_x0020_Continuidad",)
CLI_SALUD_ESTRATEGICO = ("Salud_x0020_Estrategico",)
CLI_SALUD_FINAL = ("Salud",)

# REUSO DE ADQUISICIÓN DELTA corrective (Reporting/Horas Vendidas, ver
# HANDOFF §4/§5/§6): Cliente pasa a compartir el MISMO canonical delta
# state entre Cartera Y Batch A (Horas Vendidas/Participación/Calendario/
# Salud) — MISMA key `(cache_scope, site_id, cliente_list_id)`, así que el
# `$select` de cold-sync debe ser la UNIÓN de lo que CUALQUIER consumidor
# necesita, nunca el subconjunto más chico de uno solo (Cartera solo
# leía `CLI_NOMBRE`/`CLI_CP_LOOKUP` — agregar más columnas es SIEMPRE
# seguro, ver `_items_narrowed_or_full`: un `$select` nunca filtra filas,
# solo columnas, y el fallback sin `$select` ya cubre cualquier nombre de
# campo candidato que no exista en el schema real). Cartera sigue
# ignorando las columnas que no lee — CERO cambio de comportamiento ahí.
CLIENTE_SELECT_FIELDS = [
    CLI_NOMBRE, *CLI_CP_LOOKUP,
    *CLI_FRECUENCIA_ACT, *CLI_PERMITE_ADELANTO, *CLI_CONDICION_PAGO, *CLI_CALENDARIO_ACT,
    *CLI_SALUD_FINANCIERA, *CLI_SALUD_RELACION, *CLI_SALUD_OPERATIVO,
    *CLI_SALUD_CONTINUIDAD, *CLI_SALUD_ESTRATEGICO, *CLI_SALUD_FINAL,
]
# Horas Productivas — panel "Cliente + Part" (ver real-data-lineage.md §8ter
# item 1, ticket "CONTINUE REPORTING"): Time Logs Reports.tmdl línea 300
# define la columna calculada `'Cliente + Part'` = `[Client] &" "&
# RELATED(Cliente[Porcentaje de Part.])*100` — un valor de referencia
# ESTÁTICO por Cliente (SharePoint, `Percentage.Type`), NUNCA derivado de
# Horas. Owner corrective ("Do not rely indefinitely on guessed candidate
# names"): el internal name YA NO se adivina — se DESCUBRE en vivo de la
# metadata de columnas de Cliente (`_resolve_porcentaje_part_field`, MISMO
# mecanismo que `_resolve_client_partners` abajo, `/lists/{cliente}/columns`
# → `displayName`), nunca un candidato codificado a mano.
CLI_PORCENTAJE_PART_DISPLAY_NAMES = ("Porcentaje de Part.", "PorcentajedePart.")

# XLSX Facturadas (Tabla24)
FAC_PROYECTO_BASE = "Proyecto Base"
FAC_CANTIDAD = "Suma de Cantidad"

# Colaboradores (MISMA lista que Staffing, `config.SHAREPOINT_COLABORADORES_
# LIST_ID`) — usada acá SOLO para resolver Email→Consultor de Horas
# Productivas/No Asignadas (Batch B). `Title` confirmado por el M del PBIP
# (rename `{"Title","Consultor"}`). El email NO está en un campo directo de
# Colaboradores (LIVE GRAPH VALIDATED, ver `get_colaboradores_email_map`) —
# se resuelve vía `COLAB_LOOKUP_ID` → User Information List (`USER_INFO_NAME`).
COLAB_TITLE = ("Title",)
COLAB_LOOKUP_ID = "Colaborador_x002e_LookupId"
USER_INFO_NAME = "Name"

# Time Logs Reports (CSV, sitio Operaciones) — Batch B, ÚNICA fuente física
# nueva (junto con "Ejecutadas" de Rentabilidad, que reusa este MISMO
# adapter). Verificado contra evidencia PRIMARIA del PBIP (M de la
# partición 'Time Logs Reports', TMDL de 'Horas  Trabajadas por Mes'/
# Comparativo, page.json de "Horas Productivas consumidas"/"Hs No
# Asignadas" — ver real-data-lineage.md §8bis/§10). MISMO sitio/carpeta que
# la XLSX de Vacaciones de Staffing (`staffing_repository.ABSENCE_SITE_
# PATH`/`ABSENCE_XLSX_PATH`, confirmada existente/alcanzable).
TIME_LOGS_SITE_PATH = "danwor.sharepoint.com:/sites/Operaciones"
TIME_LOGS_FOLDER_PATH = "General/Template Dashboard"
TIME_LOGS_REQUIRED_HEADERS = ("Date", "Client", "Project", "Task", "Email", "Duration Decimal")

# PERF PHASE 1A (owner corrective, ver HANDOFF — root cause confirmado: 6
# descargas CSV secuenciales ~24s de los ~26.5s de `time_logs.total`):
# tope EXPLÍCITO de concurrencia I/O para las descargas de Time Logs — cada
# archivo se descarga+parsea en su propio worker (`_download_one_time_logs_file`),
# nunca un fan-out sin límite (cap de presión sobre Graph, ver Fase 1B para
# el segundo nivel de concurrencia con el que este tope convive).
_TIME_LOGS_MAX_WORKERS = 3

# CACHE VERSIONED HISTORICAL TIME LOGS corrective (Reporting Cartera/
# Proyecto Fase 2, ver HANDOFF + reporting_time_logs_cache.py) — instancia
# ÚNICA, proceso-lifetime (nunca por-request, nunca global entre procesos:
# sin Redis/DB/disco, ver HANDOFF §8). Módulo-level a propósito: los tests
# la reemplazan vía `monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_
# CACHE", ...)` para aislamiento entre casos (ver
# test_reporting_time_logs_historical_cache.py) — el código de producción
# SIEMPRE hace el lookup vía este nombre de módulo, nunca una referencia
# capturada en el momento de import.
_TIME_LOGS_HISTORICAL_CACHE = TimeLogsHistoricalCache()

# CACHE VERSIONED HISTORICAL TIME LOGS corrective, Fase 3 (ver HANDOFF §2 —
# Facturadas XLSX clasificada VERSION-CACHE SAFE, mismo driveItem/eTag que
# Time Logs): instancia SEPARADA de `_TIME_LOGS_HISTORICAL_CACHE` — presión
# de LRU/eviction independiente por fuente (§8: "source invalidation
# remains independent"), aunque la clase es la MISMA (nombre histórico, ver
# reporting_time_logs_cache.py).
_FACTURADAS_CACHE = TimeLogsHistoricalCache()

# DELTA-SYNC corrective — Horas (Fase 3A) + Proyectos/Cliente/Client
# Partner (Fase 3B, ver HANDOFF) — instancia ÚNICA COMPARTIDA por los 4
# consumidores (`_delta_sync_list`), proceso-lifetime. Compartir UNA cache
# es seguro: la key SIEMPRE incluye `list_id`, así que dos fuentes nunca
# colisionan aunque compartan `cache_scope`/`site_id`. Estado MUTABLE
# (rows + deltaLink), nunca versionado por eTag como Time Logs/Facturadas
# — ver docstring de `DeltaCache` para por qué es un primitivo distinto.
# `max_entries=100` (vs. el default 40 de Fase 3A): ahora son hasta 4
# fuentes × N scopes compartiendo el mismo pool LRU — Proyectos/Cliente/
# Client Partner son listas de referencia bastante más chicas que Horas
# (1786 filas), así que el footprint total sigue siendo un orden de
# magnitud menor que la preocupación original de Time Logs histórico.
# Módulo-level a propósito, MISMO criterio testeable que
# `_TIME_LOGS_HISTORICAL_CACHE`/`_FACTURADAS_CACHE` (tests la reemplazan
# vía `monkeypatch.setattr(repo_mod, "_DELTA_CACHE", ...)`).
_DELTA_CACHE = DeltaCache(max_entries=100)

# EXPLICIT SOURCE TOPOLOGY corrective (owner 2026-08-18 — "este método es un
# adapter de una fuente conocida, NO un crawler genérico de SharePoint"): la
# recursión genérica introducida por `f7a9d9b` entraba a CUALQUIER subcarpeta
# de `TIME_LOGS_FOLDER_PATH` — el diagnóstico real probó que eso incluye
# carpetas no relacionadas con Time Logs (root tiene ≥5 subcarpetas, alguna
# con anidamiento propio), causando ~55s de llamadas Graph invisibles/
# irrelevantes. El contrato de origen real es SOLO estas 2 ubicaciones — el
# histórico conocido vive DIRECTO adentro de esta carpeta, nunca más
# profundo.
TIME_LOGS_HISTORY_FOLDER_NAME = "Times previos"


def _time_logs_mes(date_value) -> str:
    """'YYYY-MM' desde la columna Date del CSV — reproduce Time Logs
    Reports[Mes] del PBIP (`if(MONTH(Date)<10, YEAR&"-0"&MONTH, YEAR&"-"&
    MONTH)`). El FORMATO exacto de celda del CSV real (ISO vs DD/MM/YYYY)
    no está confirmado todavía (a confirmar en el smoke, mismo criterio que
    la XLSX) — se intenta ISO (YYYY-MM-DD) primero, luego DD/MM/YYYY
    (convención AR, mismo criterio que el resto de esta app). Sin match ⇒
    "" (la fila no aporta a ningún mes — nunca se inventa una fecha)."""
    text = _text(date_value)
    if not text:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}"
    return ""


def _time_logs_anio(date_value) -> str:
    mes = _time_logs_mes(date_value)
    return mes[:4] if mes else ""


# HOT WINDOW / COLD HISTORY corrective (Horas Productivas, owner decision
# 2026-08-18 — "la optimización debe ocurrir en source loading, no filtrar
# DESPUÉS de descargar todos los CSV"): para elegir QUÉ archivos descargar
# sin abrir ninguno, se infiere el año de cada item por su NOMBRE — la
# convención observada en `Times previos/` es un año de 4 dígitos en el
# nombre para cada archivo histórico ("Time Logs Reports 2025.csv"); el
# archivo SIN año en el nombre es el CSV "vivo" de la raíz (owner-confirmed:
# su rango real es 01/01–hoy del año calendario en curso), tratado como el
# año actual. Es una inferencia de NOMBRE, nunca de contenido — no abre/
# parsea el archivo para decidir si lo descarga.
_CSV_NAME_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _infer_time_logs_csv_year(item: dict) -> str:
    m = _CSV_NAME_YEAR_RE.search(_text(item.get("name")))
    if m:
        return m.group(1)
    return str(datetime.now().year)


# CACHE VERSIONED HISTORICAL TIME LOGS corrective (Reporting Cartera/
# Proyecto Fase 2, ver HANDOFF + reporting_time_logs_cache.py) — MISMA
# señal de nombre que `_infer_time_logs_csv_year` (nunca una heurística
# nueva): un archivo es "histórico" sii su nombre trae un año explícito
# (`Time Logs Reports YYYY.csv`, siempre en `Times previos/`); el archivo
# SIN año (`Time Logs Reports.csv`, la raíz) es el "actual" — NUNCA se
# cachea en esta fase (política conservadora, ver HANDOFF §7).
def _is_historical_time_logs_item(item: dict) -> bool:
    return bool(_CSV_NAME_YEAR_RE.search(_text(item.get("name"))))


def _drive_item_version(item: dict) -> str:
    """Clave de versión ESTABLE de un driveItem — `eTag` (preferido) o, si
    faltara, `lastModifiedDateTime:size` (fallback conservador — nunca se
    asume inmutabilidad sin chequeo de versión). GENÉRICO — no específico
    de Time Logs (nombre original `_time_logs_file_version`, renombrado
    Fase 3 al reusarse también para Facturadas XLSX, ver
    `_facturadas_workbook_version` — MISMA fórmula, un solo lugar que la
    define, nunca duplicada)."""
    etag = _text(item.get("eTag"))
    if etag:
        return f"etag:{etag}"
    return f"lm:{_text(item.get('lastModifiedDateTime'))}:{_text(item.get('size'))}"


class ReportingConfigurationError(Exception):
    """Falta configuración de deployment de Reporting (p. ej. site ID sin
    setear) — NO es una falla de fuente ni de transporte. El router la mapea a
    `config_incomplete`. Es la ÚNICA condición que debe leerse como config
    incompleta: NUNCA un `RuntimeError` genérico surgido más abajo (parseo,
    join, etc.), que es un `source_error`."""


class ReportingSourceError(Exception):
    """Falla NO transitoria de una fuente de Reporting (workbook inválido,
    tabla faltante, sitio sin id). El router la trata fail-soft/localizada,
    nunca tumba el resto de Reporting. `stage` es un label seguro
    (site/workbook/table), nunca datos/PII."""

    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.reason = "source_error"
        super().__init__(f"Reporting source error — stage={stage}" + (f": {detail}" if detail else ""))


class ReportingGraphError(Exception):
    """Envoltorio SEGURO de una falla de transporte Graph durante Reporting —
    expone SOLO stage/status_code, nunca la excepción cruda de urllib (que
    puede traer la URL con query string). Mismo criterio que Staffing."""

    def __init__(self, stage: str, status_code: int | None):
        self.stage = stage
        self.status_code = status_code
        self.reason = "http_error"
        super().__init__(f"Reporting Graph error — stage={stage} status_code={status_code}")


def _call_graph(stage: str, fn):
    import urllib.error
    try:
        return fn()
    except urllib.error.HTTPError as exc:
        raise ReportingGraphError(stage=stage, status_code=exc.code) from None
    except urllib.error.URLError:
        raise ReportingGraphError(stage=stage, status_code=None) from None
    except TimeoutError:
        # RESILIENCY corrective (Fase 3B, ver HANDOFF §9): un timeout de
        # LECTURA (`response.read()` dentro de `urlopen(...)`, ya con la
        # conexión establecida) llega acá como `TimeoutError`/`socket.
        # timeout` CRUDO — `urllib` NUNCA lo envuelve en `URLError` (eso
        # solo pasa para fallos en la fase de conexión). Antes de este fix
        # se propagaba sin clasificar, tumbando el request completo en vez
        # de degradar igual que cualquier otro fallo de transporte. MISMO
        # `status_code=None` que `URLError` — "sin respuesta HTTP
        # recibida", la señal que el sync delta usa para decidir su
        # fallback (ver AUDIT HANDOFF §9: nunca se reclasifica un status
        # code HTTP real como transitorio, solo la ausencia de respuesta).
        raise ReportingGraphError(stage=stage, status_code=None) from None


def _list_items_url(
    site_id: str, list_id: str, select_fields: list[str] | None = None, *, filter_expr: str | None = None,
) -> str:
    """Items de una lista con `fields` expandido. Si se pasan `select_fields`,
    se limita el $select a esas columnas (nunca baja toda la lista). Mismo
    patrón que `staffing_repository._list_items_url`, replicado acá para no
    acoplar Reporting a un helper privado de Staffing.

    `filter_expr` (HOT WINDOW / COLD HISTORY corrective, ver HANDOFF):
    expresión OData cruda, URL-encodeada acá — MISMO parámetro/mecanismo que
    `staffing_repository._list_items_url` ya usa en producción para el
    scope operational/historical_inactive de Asignaciones."""
    url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items?$expand=fields"
    if select_fields:
        from urllib.parse import quote
        cols = ",".join(select_fields)
        url += f"($select={quote(cols)})"
    if filter_expr:
        from urllib.parse import quote
        url += f"&$filter={quote(filter_expr, safe='')}"
    return url


def _list_items_delta_url(site_id: str, list_id: str, select_fields: list[str] | None) -> str:
    """MISMO patrón de construcción que `_list_items_url`
    (`$expand=fields($select=...)`) pero contra `/items/delta` — SOLO para
    el sync INICIAL (frío), Fase 3A (ver HANDOFF). Un `@odata.deltaLink`
    ya devuelto por Graph se reusa TAL CUAL para el sync warm — nunca se
    reconstruye ni se re-parsea (AUDIT §1: la transport actual
    (`microsoft365.graph_get_json`) ya acepta cualquier URL absoluta de
    Graph sin reescribirla, cero cambio de transporte necesario)."""
    url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items/delta?$expand=fields"
    if select_fields:
        from urllib.parse import quote
        cols = ",".join(select_fields)
        url += f"($select={quote(cols)})"
    return url


def _odata_quote(value: str) -> str:
    """Escape mínimo de literal OData (comilla simple → doble comilla
    simple) — mismo helper que `staffing_repository._odata_quote`,
    replicado acá para no acoplar Reporting a un privado de Staffing.
    `value` siempre es un año interno nuestro (nunca input de usuario sin
    validar), pero escapar es gratis y correcto igual."""
    return value.replace("'", "''")


# Prefer HonorNonIndexedQueriesWarningMayFailRandomly (HOT WINDOW/COLD
# HISTORY corrective) — MISMO header que `staffing_repository.
# _HISTORY_PREFER_HEADER` ya usa en producción para el `$filter` de
# Asignaciones (columna Choice no indexada). Reusado tal cual, replicado
# acá por el mismo motivo que `_odata_quote`/`_list_items_url` de arriba.
_HOT_WINDOW_PREFER_HEADER = {"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"}


def _norm_key(name) -> str:
    """Clave de proyecto normalizada (Upper + sin espacios) — reproduce la
    transformación del PBIP (`Text.Upper(Replace(' ',''))`) que une
    `Horas por proyectos.Proyecto_Key` con `Horas Facturadas.Key_proyect_desc`."""
    return "".join(str(name or "").split()).upper()


# TEMPORAL DOMAIN CONTRACT (owner live QA — "Septiembre" apareció como
# opción del <select> de Año en Horas Vendidas): NINGÚN punto de este
# módulo validaba que `A_x00f1_o` (Horas por proyectos) contuviera
# realmente un año de 4 dígitos antes de este corrective — se confiaba
# ciegamente en el valor crudo de SharePoint. Es un campo de texto/Choice
# sin schema estricto del lado de Graph; un error de carga humano en la
# fuente (o cualquier valor no numérico) fluía sin filtro hasta el YEAR
# DOMAIN del browser. `_is_valid_anio` es el ÚNICO punto que decide si un
# valor de año es válido — reusado por CADA lugar de este archivo (y de
# `reporting_service.py`) que arma un año-como-clave/año-como-opción,
# nunca reimplementado ad-hoc. Mes NUNCA participa de este dominio (dominio
# separado, ver reporting.js — MESES_LARGOS).
_VALID_ANIO_RE = re.compile(r"^\d{4}$")


def _is_valid_anio(value) -> bool:
    return bool(_VALID_ANIO_RE.match(_text(value)))


def _num(value) -> float:
    """Coerción numérica robusta (celdas/text de SharePoint pueden venir como
    str). Vacío/None/no-numérico ⇒ 0.0 (medidas aditivas)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _first(fields: dict, *candidates) -> str:
    """Primer valor NO vacío entre varios nombres de campo candidatos (maneja la
    variación de convención de Graph, p. ej. `ProyectoLookupId` vs `ProyectoId`)."""
    for name in candidates:
        if name in fields and fields[name] not in (None, ""):
            return _text(fields[name])
    return ""


def _filter_horas_items_by_years(items: list[dict], hot_years: list[str]) -> list[dict]:
    """Filtro Python EXACTO que ya usaba el fallback de
    `_fetch_horas_proyectos` (extraído, Reporting/Horas Vendidas corrective
    — ver HANDOFF) — reusado TAMBIÉN cuando el canonical Horas delta state
    (historia completa) se proyecta LOCALMENTE a una ventana `hot_years`
    (`get_batch_a_facts`, ver ahí), nunca una segunda implementación del
    mismo criterio."""
    wanted = set(hot_years)
    return [it for it in items if _text((it.get("fields") or {}).get(HP_ANIO)) in wanted]


class ReportingRepository:
    """Acceso a fuentes SharePoint de Cartera. Inyectable para tests (site/list
    IDs por parámetro). NO posee token: cada método recibe `access_token`
    delegado explícito (igual que StaffingRepository)."""

    def __init__(
        self,
        *,
        site_id: str | None = None,
        horas_proyectos_list_id: str | None = None,
        proyectos_list_id: str | None = None,
        cliente_list_id: str | None = None,
        colaboradores_list_id: str | None = None,
        user_information_list_id: str | None = None,
    ):
        self.site_id = site_id if site_id is not None else config.SHAREPOINT_GESTION_SITE_ID
        self.horas_proyectos_list_id = horas_proyectos_list_id or config.SHAREPOINT_HORAS_PROYECTOS_LIST_ID
        self.proyectos_list_id = proyectos_list_id or config.SHAREPOINT_PROYECTOS_LIST_ID
        self.cliente_list_id = cliente_list_id or config.SHAREPOINT_CLIENTE_LIST_ID
        self.colaboradores_list_id = colaboradores_list_id or config.SHAREPOINT_COLABORADORES_LIST_ID
        self.user_information_list_id = user_information_list_id or config.SHAREPOINT_USER_INFORMATION_LIST_ID

    def _require_site_id(self) -> str:
        if not self.site_id:
            raise ReportingConfigurationError("SHAREPOINT_GESTION_SITE_ID no configurado")
        return self.site_id

    # --- lecturas de lista (paginadas) --------------------------------------

    def _items(self, site_id: str, list_id: str, stage: str, select: list[str] | None = None) -> list[dict]:
        """Items crudos de Graph (`{id, fields}`) — se conserva `item["id"]`
        (target de los lookups), no solo `fields`."""
        return _call_graph(stage, lambda: microsoft365.graph_get_paged_values(
            _list_items_url(site_id, list_id, select), access_token=self._token,
        ))

    def _items_narrowed_or_full(self, site_id: str, list_id: str, stage: str, select: list[str]) -> list[dict]:
        """Igual que `_items`, pero con `$select` — SIEMPRE seguro contra
        pérdida de dato (nunca filtra filas, solo reduce columnas), pero
        alguno de los internal names candidatos de un lookup (ver
        `HP_PROYECTO_LOOKUP`/`CLI_CP_LOOKUP`, tuplas "prueba ambas formas")
        podría no existir en el schema real de esta lista — sin acceso Graph
        en este entorno para confirmarlo, se intenta el `$select` acotado
        primero y, ante CUALQUIER falla (mismo criterio que
        `_fetch_horas_proyectos`/`staffing_repository._fetch_asignaciones`),
        cae a la lectura COMPLETA sin `$select` — nunca rompe el fetch por
        un nombre de campo no soportado."""
        try:
            return microsoft365.graph_get_paged_values(
                _list_items_url(site_id, list_id, select), access_token=self._token,
            )
        except Exception:  # noqa: BLE001 — fallback de transporte, nunca una regla de negocio
            return self._items(site_id, list_id, stage)

    def _fetch_horas_proyectos(
        self, site_id: str, *, hot_years: list[str] | None = None, select: list[str] | None = None,
    ) -> tuple[list[dict], bool]:
        """Lee "Horas por proyectos" (HOT WINDOW / COLD HISTORY corrective,
        ver HANDOFF — owner live QA: Cartera hidrataba ~2023→2026 completo
        aunque el default aterriza en el mes/año calendario actual).

        `hot_years=None` (default): SIN `$filter`, comportamiento de
        SIEMPRE — usado por Proyecto (agregado de VIDA del proyecto, nunca
        se le puede aplicar una ventana sin romper su contrato de totales) y
        por cualquier lectura que necesite la historia completa.

        `hot_years=[...]`: intenta `$filter` OData (`A_x00f1_o eq 'Y1' or
        A_x00f1_o eq 'Y2' or ...`) + header `Prefer:
        HonorNonIndexedQueriesWarningMayFailRandomly` — MISMO patrón ya
        probado en producción por
        `staffing_repository._fetch_asignaciones` (Staffing Director
        Priority V2: operational vs historical_inactive). Si Graph rechaza
        el filtro (columna no indexada, o cualquier fallo de transporte —
        nunca una regla de negocio la que decide) cae automáticamente a la
        lectura COMPLETA + filtro en Python — SIEMPRE correcto, Cartera
        nunca se rompe por una query no soportada; el `bool` devuelto indica
        si el `$filter` remoto fue el que efectivamente acotó la lectura
        (`True`) o si fue el fallback (`False`), solo para logging/P0.
        NUNCA se aplica a `hot_years=None` — ninguna ambigüedad posible.

        `select`, si se pasa, TAMBIÉN puede fallar (candidato de lookup
        inexistente en el schema real, ver `_items_narrowed_or_full`) —
        cualquier fallback de acá abajo cae al `select=None` (item
        completo), nunca repite el MISMO `select` que puede haber sido la
        causa de la falla."""
        if not hot_years:
            try:
                return microsoft365.graph_get_paged_values(
                    _list_items_url(site_id, self.horas_proyectos_list_id, select), access_token=self._token,
                ), False
            except Exception:  # noqa: BLE001 — fallback de transporte (select inválido), nunca una regla de negocio
                return self._items(site_id, self.horas_proyectos_list_id, "horas_proyectos"), False
        filter_expr = " or ".join(f"fields/{HP_ANIO} eq '{_odata_quote(y)}'" for y in hot_years)
        try:
            items = microsoft365.graph_get_paged_values(
                _list_items_url(site_id, self.horas_proyectos_list_id, select, filter_expr=filter_expr),
                access_token=self._token, extra_headers=_HOT_WINDOW_PREFER_HEADER,
            )
            return items, True
        except Exception:  # noqa: BLE001 — fallback de transporte, nunca una regla de negocio (mismo criterio que Staffing)
            all_items = self._items(site_id, self.horas_proyectos_list_id, "horas_proyectos")
            return _filter_horas_items_by_years(all_items, hot_years), False

    # --- DELTA-SYNC genérico (Horas Fase 3A, Proyectos/Cliente/Client -----
    # Partner Fase 3B, ver HANDOFF) — SOLO para el camino Cartera/Proyecto
    # con un `cache_scope` real (Mantenimiento y cualquier caller que
    # nunca pase `cache_scope` siguen en sus lecturas de siempre, sin
    # cambios). Primitivo COMPARTIDO por los 4 consumidores — la
    # canonicalización de fila NUNCA vive acá (cada caller sigue leyendo
    # `it.get("fields")` a su manera, MISMO criterio de siempre); esto es
    # PURAMENTE transporte+estado (paginación, aplicación de cambios,
    # single-flight, resync ante 410) — cero shaping de negocio.

    def _delta_apply_pages(self, url: str, working: dict[str, dict], *, stage: str) -> tuple[str, int, int, int]:
        """Sigue `@odata.nextLink` aplicando cada página sobre `working`
        (mutado IN-PLACE — el caller decide si `working` es un dict nuevo
        vacío (cold) o una COPIA del estado previo (warm), nunca el
        original en cache directamente). Un item con `@removed` se
        remueve; cualquier otro se agrega/reemplaza — mismo item repetido
        en la secuencia ⇒ la ÚLTIMA ocurrencia gana (asignación de dict en
        orden de llegada, tanto dentro de una página como entre páginas).
        Devuelve `(delta_link, pages, changes, deletes)` — `changes`/
        `deletes` cuentan EVENTOS vistos (no deduplicados por id), solo
        para observabilidad, nunca afectan el estado final. `stage`
        rotula el error de transporte (`ReportingGraphError.stage`) por
        fuente — `"horas_delta"`/`"proyectos_delta"`/`"cliente_delta"`/
        `"client_partner_delta"`."""
        pages = changes = deletes = 0
        delta_link = ""
        while url:
            payload = _call_graph(f"{stage}_delta", lambda u=url: microsoft365.graph_get_json(u, self._token))
            pages += 1
            for item in payload.get("value") or []:
                item_id = _text(item.get("id"))
                if not item_id:
                    continue
                if "@removed" in item:
                    if working.pop(item_id, None) is not None:
                        deletes += 1
                else:
                    working[item_id] = item
                    changes += 1
            next_link = _text(payload.get("@odata.nextLink"))
            if next_link:
                url = next_link
                continue
            delta_link = _text(payload.get("@odata.deltaLink"))
            url = ""
        if not delta_link:
            raise ReportingSourceError(
                f"{stage}_delta", "Graph no devolvió @odata.deltaLink tras completar el sync",
            )
        return delta_link, pages, changes, deletes

    def _delta_cold_sync(
        self, site_id: str, list_id: str, select_fields: list[str] | None, *, stage: str, mode: str = "cold",
    ) -> dict:
        """Sync COMPLETO desde cero — MISMO fallback de `$select` que
        `_fetch_horas_proyectos`/`_items_narrowed_or_full` (ante CUALQUIER
        falla del intento con `$select`, un único reintento completo sin
        `$select` — nunca dos veces, nunca repite la MISMA causa de
        falla). El resultado (`rows`/`delta_link`) recién se arma en
        variables LOCALES — nunca se publica un estado parcialmente
        sincronizado (`DeltaCache.sync` solo adopta el `return` de esta
        función si termina sin excepción, ver reporting_delta_cache.py)."""
        url = _list_items_delta_url(site_id, list_id, select_fields)
        try:
            working: dict[str, dict] = {}
            delta_link, pages, changes, deletes = self._delta_apply_pages(url, working, stage=stage)
        except Exception:  # noqa: BLE001 — fallback de transporte (select inválido), MISMO criterio que _items_narrowed_or_full
            url = _list_items_delta_url(site_id, list_id, None)
            working = {}
            delta_link, pages, changes, deletes = self._delta_apply_pages(url, working, stage=stage)
        _perf_logger.info(
            "reporting.perf cartera.%s_delta mode=%s pages=%s changes=%s deletes=%s rows=%s",
            stage, mode, pages, changes, deletes, len(working),
        )
        return {"rows": working, "delta_link": delta_link}

    def _delta_warm_sync(self, current: dict, *, stage: str) -> dict:
        """Aplica el checkpoint sobre una COPIA del estado previo — el
        dict original de `current["rows"]` nunca se muta hasta que este
        método retorna con éxito (ver `DeltaCache.sync`: una excepción acá
        deja el estado previo intacto)."""
        working = dict(current["rows"])
        delta_link, pages, changes, deletes = self._delta_apply_pages(current["delta_link"], working, stage=stage)
        _perf_logger.info(
            "reporting.perf cartera.%s_delta mode=warm pages=%s changes=%s deletes=%s rows=%s",
            stage, pages, changes, deletes, len(working),
        )
        return {"rows": working, "delta_link": delta_link}

    def _delta_sync_list(
        self, *, site_id: str, list_id: str, select_fields: list[str] | None, cache_scope: str, stage: str,
        legacy_fetch_fn,
    ) -> list[dict]:
        """Punto de entrada COMPARTIDO del sync delta — single-flight por
        `(cache_scope, site_id, list_id)` vía `_DELTA_CACHE` (ver
        reporting_delta_cache.py). Primera llamada para esta key ⇒ sync
        completo. Llamadas siguientes ⇒ checkpoint sobre el `deltaLink`
        guardado; si Graph lo invalida (`410 Gone` — señal documentada de
        Microsoft Graph para delta tokens expirados, ver AUDIT HANDOFF
        Fase 3A §4), se descarta SOLO este estado y se hace UN resync
        completo controlado (nunca un loop). `list_id` es SIEMPRE el ID
        efectivo/resuelto — para Client Partner (target descubierto
        dinámicamente vía `/columns`) el caller ya lo resolvió ANTES de
        llamar acá, nunca un id adivinado/estático (ver
        `_resolve_client_partners`).

        RESILIENCY corrective (Fase 3B, ver HANDOFF — owner smoke real:
        un timeout de lectura en el `deltaLink` tumbó Cartera completo).
        `ReportingGraphError.status_code is None` (URLError/TimeoutError —
        ver `_call_graph` — "sin respuesta HTTP recibida en absoluto",
        NUNCA un status code HTTP real reclasificado) es la ÚNICA señal
        que dispara el fallback — un 410 sigue yendo a resync (arriba); un
        403/404/500/etc. (CUALQUIER respuesta HTTP real) o cualquier
        excepción que NO sea `ReportingGraphError` (bug de parseo,
        `ReportingSourceError` por deltaLink faltante, etc.) se propaga
        TAL CUAL, sin fallback — nunca se convierte un bug de negocio en
        una degradación silenciosa. El fallback SIEMPRE corre
        `legacy_fetch_fn` (el fetch completo pre-delta EXACTO de esta
        fuente, provisto por el caller — CERO shaping/filtro nuevo acá) UNA
        sola vez — si también falla, esa excepción se propaga (MISMO
        contrato de error de Cartera de siempre, nunca silenciado). El
        estado delta cacheado NUNCA se toca en este camino: `DeltaCache.
        sync` ya garantiza que una excepción de `sync_fn` deja
        `self._data[key]` exactamente como estaba (ver
        reporting_delta_cache.py) — el próximo request reintenta desde el
        MISMO `deltaLink` previo, nunca uno inventado ni una key borrada."""
        key = (cache_scope, site_id, list_id)

        def sync_fn(current):
            if current is None:
                return self._delta_cold_sync(site_id, list_id, select_fields, stage=stage)
            try:
                return self._delta_warm_sync(current, stage=stage)
            except ReportingGraphError as exc:
                if exc.status_code == 410:
                    return self._delta_cold_sync(site_id, list_id, select_fields, stage=stage, mode="resync")
                raise

        try:
            state = _DELTA_CACHE.sync(key, sync_fn)
        except ReportingGraphError as exc:
            if exc.status_code is not None:
                raise
            rows = legacy_fetch_fn()
            _perf_logger.info(
                "reporting.perf cartera.%s_delta mode=fallback reason=transport pages=0 changes=0 deletes=0 rows=%s",
                stage, len(rows),
            )
            return rows
        return list(state["rows"].values())

    def _fetch_horas_proyectos_delta(self, site_id: str, *, cache_scope: str) -> list[dict]:
        """Horas por proyectos vía delta (Fase 3A) — wrapper delgado sobre
        `_delta_sync_list` (Fase 3B factorizó el transporte/estado a un
        primitivo compartido; comportamiento IDÉNTICO, ver
        test_reporting_horas_delta_sync.py). `legacy_fetch_fn`: el MISMO
        fetch completo (`hot_years=None`) que este scope usaba ANTES de
        delta — único camino de fallback ante un fallo transitorio."""
        def legacy_fetch_fn():
            items, _filter_used = self._fetch_horas_proyectos(
                site_id, hot_years=None, select=HORAS_PROYECTOS_SELECT_FIELDS,
            )
            return items

        return self._delta_sync_list(
            site_id=site_id, list_id=self.horas_proyectos_list_id,
            select_fields=HORAS_PROYECTOS_SELECT_FIELDS, cache_scope=cache_scope, stage="horas",
            legacy_fetch_fn=legacy_fetch_fn,
        )

    def _fetch_proyectos_delta(self, site_id: str, *, cache_scope: str) -> list[dict]:
        """Proyectos vía delta (Fase 3B) — MISMA proyección
        (`PROYECTOS_SELECT_FIELDS`) que la lectura completa de siempre.
        `legacy_fetch_fn`: EXACTAMENTE `_items_narrowed_or_full`, el
        fetch que este método usaba antes de delta."""
        return self._delta_sync_list(
            site_id=site_id, list_id=self.proyectos_list_id,
            select_fields=PROYECTOS_SELECT_FIELDS, cache_scope=cache_scope, stage="proyectos",
            legacy_fetch_fn=lambda: self._items_narrowed_or_full(
                site_id, self.proyectos_list_id, "proyectos", PROYECTOS_SELECT_FIELDS,
            ),
        )

    def _fetch_cliente_delta(self, site_id: str, *, cache_scope: str) -> list[dict]:
        """Cliente vía delta (Fase 3B) — MISMA proyección
        (`CLIENTE_SELECT_FIELDS`) que la lectura completa de siempre.
        `legacy_fetch_fn`: EXACTAMENTE `_items_narrowed_or_full`, el
        fetch que este método usaba antes de delta."""
        return self._delta_sync_list(
            site_id=site_id, list_id=self.cliente_list_id,
            select_fields=CLIENTE_SELECT_FIELDS, cache_scope=cache_scope, stage="cliente",
            legacy_fetch_fn=lambda: self._items_narrowed_or_full(
                site_id, self.cliente_list_id, "cliente", CLIENTE_SELECT_FIELDS,
            ),
        )

    # --- XLSX Facturadas ----------------------------------------------------

    def _download_facturadas_workbook(self, site_id: str) -> bytes:
        return _call_graph("facturadas_download", lambda: microsoft365.graph_get_bytes(
            f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{_quote(FACTURADAS_XLSX_PATH)}:/content",
            self._token,
        ))

    def _facturadas_workbook_metadata(self, site_id: str) -> dict | None:
        """GET de METADATA del driveItem de Facturadas — SIN `:content`
        (no descarga el archivo), 1 llamada Graph barata que expone
        `eTag`/`lastModifiedDateTime`/`size`/`id` (CACHE VERSIONED
        HISTORICAL TIME LOGS corrective Fase 3, ver HANDOFF §2 — VERSION-
        CACHE SAFE: mismo tipo de recurso driveItem que Time Logs, Fase 2,
        pero acá vía un GET dedicado — Facturadas nunca lista una carpeta,
        se conoce el PATH exacto de antemano, a diferencia de Time Logs).

        Fail-soft: cualquier falla de transporte devuelve `None` — el
        caller cae al camino SIN cache (descarga siempre fresca), MISMO
        comportamiento que existía antes de esta fase. Nunca rompe el
        fetch por un problema de la metadata."""
        try:
            return _call_graph("facturadas_metadata", lambda: microsoft365.graph_get_json(
                f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{_quote(FACTURADAS_XLSX_PATH)}", self._token,
            ))
        except ReportingGraphError:
            return None

    def _facturadas_by_project(self, site_id: str) -> dict[str, float]:
        """SUM(`Suma de Cantidad`) por clave de proyecto normalizada, desde la
        tabla `Tabla24` del XLSX. Un proyecto ausente ⇒ (implícitamente) 0."""
        data = self._download_facturadas_workbook(site_id)
        headers, rows = _read_facturadas_table(data, (FAC_PROYECTO_BASE, FAC_CANTIDAD))
        i_proj, i_cant = headers[FAC_PROYECTO_BASE], headers[FAC_CANTIDAD]
        by_key: dict[str, float] = {}
        for row in rows:
            key = _norm_key(row[i_proj] if i_proj < len(row) else None)
            if not key:
                continue
            by_key[key] = by_key.get(key, 0.0) + _num(row[i_cant] if i_cant < len(row) else None)
        return by_key

    def _facturadas_by_project_cached(self, site_id: str, *, cache_scope: str | None) -> tuple[dict[str, float], str]:
        """Wrapper de `_facturadas_by_project` con cache versionada
        (Fase 3, VERSION-CACHE SAFE — ver AUDIT en HANDOFF §2). El VALOR
        cacheado es el AGREGADO YA PARSEADO (`dict[str, float]` por
        proyecto) — MÁS CHICO que los bytes crudos del XLSX y CERO
        fórmula de negocio duplicada acá (la suma por proyecto sigue
        viviendo, sin cambios, en `_facturadas_by_project`).

        `cache_scope=None` preserva el comportamiento EXACTO de siempre —
        sin cache, sin la llamada de metadata extra. Con `cache_scope`
        no-`None`: 1 GET de metadata SIEMPRE (barato, necesario para saber
        si hay HIT), download+parse SOLO en un MISS.

        Devuelve `(by_key, cache_outcome)` — `cache_outcome` en
        `{"n/a", "hit", "computed", "waited"}`, MISMA semántica que
        `_download_one_time_logs_file_cached` (Fase 2), para el agregado
        `reporting.perf cartera.cache` (ver `_cartera_facts_and_dimension`)."""
        if cache_scope is None:
            return self._facturadas_by_project(site_id), "n/a"
        metadata = self._facturadas_workbook_metadata(site_id)
        if metadata is None:
            # Fail-soft: sin metadata confiable, nunca se inventa una
            # versión — camino de siempre, sin cache.
            return self._facturadas_by_project(site_id), "n/a"
        key = (cache_scope, _text(metadata.get("id")) or FACTURADAS_XLSX_PATH, _drive_item_version(metadata))
        value, outcome = _FACTURADAS_CACHE.get_or_compute(key, lambda: self._facturadas_by_project(site_id))
        return value, outcome

    # --- Client Partner (lookup de Cliente → lista target descubierta) -------

    def _resolve_client_partners(self, site_id: str, *, cache_scope: str | None = None) -> dict[str, str]:
        """Mapea `id de item del target → Title` para el lookup Client Partner de
        la lista Cliente. El target se DESCUBRE de la metadata de columnas
        (`/lists/{cliente}/columns` → `lookup.listId`), NUNCA se asume por nombre
        (la relación de PMs es con Proyectos.PMId, no con Cliente). Se carga UNA
        sola vez (sin N+1). Sin target discoverable ⇒ `{}` (CP en blanco).

        `cache_scope` (DELTA-SYNC corrective, Fase 3B, ver HANDOFF §6): el
        descubrimiento del target (`/columns`, arriba) NUNCA se cachea —
        sigue siendo un GET de metadata liviano, siempre fresco, MISMO
        contrato de siempre (preserva el discovery contract tal cual). Una
        vez conocido el `target` REAL/resuelto, la LECTURA de items de ESE
        target sí opta por delta cuando `cache_scope` no es `None` — la key
        de cache usa el id EFECTIVO descubierto (`_delta_sync_list`,
        `list_id=target`), nunca un id adivinado/estático (si el target
        cambiara de un request a otro, cosa que no debería pasar en
        producción pero que el código nunca asume, cada `target` distinto
        simplemente cae en su propia key de cache)."""
        columns = _call_graph("cliente_columns", lambda: microsoft365.graph_get_paged_values(
            f"{GRAPH_BASE}/sites/{site_id}/lists/{self.cliente_list_id}/columns", access_token=self._token,
        ))
        target = ""
        for col in columns:
            if not col.get("lookup"):
                continue
            if _text(col.get("name")) in CLI_CP_COLUMN_NAMES or _text(col.get("displayName")) in CLI_CP_DISPLAY_NAMES:
                target = _text((col.get("lookup") or {}).get("listId"))
                break
        if not target:
            return {}
        if cache_scope is not None:
            items = self._delta_sync_list(
                site_id=site_id, list_id=target, select_fields=None,
                cache_scope=cache_scope, stage="client_partner",
                legacy_fetch_fn=lambda: self._items(site_id, target, "client_partner"),
            )
        else:
            items = self._items(site_id, target, "client_partner")
        return {_text(it.get("id")): _first(it.get("fields") or {}, PM_TITLE) for it in items}

    def _resolve_porcentaje_part_field(self, site_id: str) -> str:
        """Internal name REAL de `Cliente['Porcentaje de Part.']` — DESCUBIERTO
        en vivo de la metadata de columnas (`/lists/{cliente}/columns` →
        `displayName`), MISMO mecanismo que `_resolve_client_partners` arriba
        (owner corrective: "Do not rely indefinitely on guessed candidate
        names" — el M del PBIP solo expone el nombre YA renombrado por Power
        Query, nunca el internal name crudo, ver real-data-lineage.md §8ter
        item 1). Sin match ⇒ "" (fail-soft — "Cliente + Part" queda sin el %
        de referencia en el rótulo, NUNCA un valor inventado; ver caller en
        `get_time_logs_facts`)."""
        columns = _call_graph("cliente_columns_porcentaje_part", lambda: microsoft365.graph_get_paged_values(
            f"{GRAPH_BASE}/sites/{site_id}/lists/{self.cliente_list_id}/columns", access_token=self._token,
        ))
        for col in columns:
            if _text(col.get("displayName")) in CLI_PORCENTAJE_PART_DISPLAY_NAMES:
                return _text(col.get("name"))
        return ""

    # --- ensamble de hechos normalizados ------------------------------------

    def get_cartera_facts(
        self, access_token: str, *, include_facturadas: bool = True, hot_years: list[str] | None = None,
    ) -> list[dict]:
        """Hechos de Cartera normalizados a grano proyecto×mes — ver
        `_cartera_facts_and_dimension` para el contrato completo. Wrapper
        delgado que descarta la dimensión de proyecto (UNION GRAIN
        corrective) para preservar el contrato EXACTO de siempre a los
        callers que no la necesitan (Proyecto, tests existentes)."""
        facts, _project_dim = self._cartera_facts_and_dimension(
            access_token, include_facturadas=include_facturadas, hot_years=hot_years,
        )
        return facts

    def get_cartera_facts_with_project_dimension(
        self, access_token: str, *, include_facturadas: bool = True, hot_years: list[str] | None = None,
        cache_scope: str | None = None,
    ) -> tuple[list[dict], dict[str, dict]]:
        """MISMO fetch que `get_cartera_facts` (CERO llamadas Graph
        adicionales — UNION GRAIN corrective, owner 2026-08-18) más la
        dimensión de proyecto (`project_dim`, keyed por `_norm_key(título)`)
        — necesaria para que Cartera/Mantenimiento pueda producir una fila
        para una key (proyecto, año, mes) que existe en Time Logs pero
        TODAVÍA no tiene fila en "Horas por proyectos" (ver
        `reporting_service._mantenimiento_rows`).

        `project_dim[key]`: `estado`/`tipoProyecto`/`idProyecto`/
        `anioInicio` SIEMPRE resolubles contra Proyectos (`proy_by_id`, ya
        fetcheado acá — independiente de si ese proyecto tiene hechos de
        "Horas por proyectos" en ESTE `hot_years`). `cliente`/
        `clientPartner` son "best effort" — se completan por co-ocurrencia
        con los hechos de "Horas por proyectos" YA traídos en este mismo
        `hot_years`; un proyecto sin NINGÚN hecho en el scope actual queda
        con `cliente`/`clientPartner` = "" (fail-soft, NUNCA inventado —
        MISMO criterio que el resto de este módulo). Un proyecto de Time
        Logs cuyo título no resuelve contra Proyectos en absoluto no tiene
        entrada en `project_dim` — el caller debe tratarlo como
        "unmatched", nunca fabricar su dimensión.

        `cache_scope` (Fase 3B + RENTABILIDAD SHARED SOURCE REUSE corrective,
        ver HANDOFF): pasado tal cual a `_cartera_facts_and_dimension` —
        real ⇒ Proyectos/Cliente/Client Partner/Horas por proyectos/
        Facturadas XLSX todos optan por su primitivo compartido (Delta o
        version-cache según la fuente, ver ese método); `hot_years` sigue
        acotando Horas por proyectos, pero ahora como proyección LOCAL sobre
        el canonical Delta state (nunca un segundo `$filter` remoto) cuando
        `cache_scope` es real. `None` (default) preserva el comportamiento
        EXACTO de siempre."""
        return self._cartera_facts_and_dimension(
            access_token, include_facturadas=include_facturadas, hot_years=hot_years, cache_scope=cache_scope,
        )

    def _cartera_facts_and_dimension(
        self, access_token: str, *, include_facturadas: bool = True, hot_years: list[str] | None = None,
        cache_scope: str | None = None,
    ) -> tuple[list[dict], dict[str, dict]]:
        """Hechos de Cartera normalizados a grano proyecto×mes, con la dimensión
        de proyecto/cliente ya unida y `facturadasProyecto` (SUM del XLSX por
        proyecto) adjunto. Nombres de negocio, NUNCA internal names SharePoint.
        El split Mantenimiento/Proyecto y las medidas derivadas viven en el
        service.

        `include_facturadas` (P5, owner live QA de performance — Cartera
        ~17s): la XLSX de Facturadas es un download+parse REAL (costo
        propio, no un GET de lista) que Mantenimiento NUNCA usa
        (`facturadas == certificadas` ahí, ver service — `facturadasProyecto`
        solo lo lee la perspectiva Proyecto). `False` la SALTEA por
        completo — cada fact queda con `facturadasProyecto=0.0` (nunca leído
        en ese camino). Default `True` preserva el contrato de siempre para
        callers que necesitan AMBAS perspectivas.

        `hot_years` (HOT WINDOW / COLD HISTORY corrective, ver HANDOFF —
        owner live QA: el ~15-17s de Cartera venía en gran parte de
        hidratar TODA la historia de "Horas por proyectos" (~2023→2026) aun
        cuando el default aterriza en el mes/año calendario actual). `None`
        (default) = SIN acotar, comportamiento de SIEMPRE — usado por
        Proyecto (agrega la VIDA COMPLETA del proyecto, nunca se le puede
        aplicar ventana) y por `get_cartera()`/tests existentes
        (retrocompat exacta). Una lista de años = intenta `$filter` OData
        con fallback automático (ver `_fetch_horas_proyectos`) — usado por
        Mantenimiento (ventana caliente por defecto, o un año frío puntual
        vía lazy-load, ver `reporting_service.get_cartera_mantenimiento`)."""
        self._token = access_token
        site_id = self._require_site_id()
        timer = _StageTimer("cartera")

        # `$select` acotado (P3, ver HANDOFF — narrowing de campos, SIEMPRE
        # seguro: nunca filtra filas, solo reduce qué columnas devuelve
        # Graph por item) a EXACTAMENTE los campos que este método lee más
        # abajo — Proyectos/Cliente por default devuelven el body completo
        # del item (incluye metadata de sistema que esta lectura nunca usa).
        # `get_batch_a_facts` NO se toca (necesita más columnas de Cliente
        # para Participación/Calendario/Salud, ver ese método).
        # DELTA-SYNC corrective, Fase 3B (ver HANDOFF — extiende Fase 3A a
        # Proyectos/Cliente): SOLO con `cache_scope` real (Cartera/Proyecto
        # — combo/Mantenimiento nunca pasan `cache_scope`, quedan en la
        # lectura completa de siempre). A diferencia de Horas, Proyectos/
        # Cliente NUNCA tuvieron `hot_years` — el gate acá es únicamente
        # `cache_scope is not None`.
        if cache_scope is not None:
            proyectos_items = self._fetch_proyectos_delta(site_id, cache_scope=cache_scope)
        else:
            proyectos_items = self._items_narrowed_or_full(
                site_id, self.proyectos_list_id, "proyectos", PROYECTOS_SELECT_FIELDS,
            )
        timer.stage("proyectos", len(proyectos_items))
        if cache_scope is not None:
            clientes_items = self._fetch_cliente_delta(site_id, cache_scope=cache_scope)
        else:
            clientes_items = self._items_narrowed_or_full(site_id, self.cliente_list_id, "cliente", CLIENTE_SELECT_FIELDS)
        timer.stage("cliente", len(clientes_items))
        # DELTA-SYNC CARTERA PROJECT HOURS corrective, Fase 3A + RENTABILIDAD
        # SHARED SOURCE REUSE corrective (ver HANDOFF): el camino `cache_scope`
        # real reemplaza la lectura completa/`$filter` (Cartera/Proyecto,
        # Rentabilidad — combo/Mantenimiento/tests que nunca pasan
        # `cache_scope` quedan en el camino de siempre) por `/items/delta` —
        # full sync solo la primera vez por scope, checkpoint incremental
        # después. `hot_years=[...]` (Mantenimiento, o Rentabilidad con
        # ventana caliente) NUNCA cambia el FETCH remoto bajo `cache_scope`
        # real: el canonical Delta state SIEMPRE trae la historia COMPLETA
        # (delta no tiene noción de `$filter`) — `hot_years` se aplica
        # LOCALMENTE después (`_filter_horas_items_by_years`, MISMA
        # proyección que `get_batch_a_facts` ya usa para Horas Vendidas,
        # nunca una segunda implementación del criterio). Antes de este
        # corrective, Rentabilidad (que SIEMPRE pasa `hot_years` explícito,
        # nunca `None`) caía al `else` de abajo aun con `cache_scope` real —
        # una segunda lectura remota de Horas por proyectos, redundante con
        # la que Cartera/Proyecto (o la propia Rentabilidad/anios-disponibles)
        # ya sincronizó bajo la MISMA key `(cache_scope, site_id, list_id)`.
        if cache_scope is not None:
            horas_items = self._fetch_horas_proyectos_delta(site_id, cache_scope=cache_scope)
            if hot_years is not None:
                horas_items = _filter_horas_items_by_years(horas_items, hot_years)
            filter_used = False  # delta no usa $filter OData — campo retenido por compat de logging
        else:
            horas_items, filter_used = self._fetch_horas_proyectos(
                site_id, hot_years=hot_years, select=HORAS_PROYECTOS_SELECT_FIELDS,
            )
        timer.stage("horas_proyectos", len(horas_items))
        if hot_years is not None:
            _perf_logger.info(
                "reporting.perf cartera.horas_proyectos_filter hot_years=%s filter_used=%s",
                ",".join(hot_years), filter_used,
            )
        if include_facturadas:
            facturadas_by_key, _facturadas_cache_outcome = self._facturadas_by_project_cached(
                site_id, cache_scope=cache_scope,
            )
        else:
            facturadas_by_key, _facturadas_cache_outcome = {}, "n/a"
        timer.stage("facturadas_xlsx", len(facturadas_by_key))
        cp_by_id = self._resolve_client_partners(site_id, cache_scope=cache_scope)
        timer.stage("client_partner", len(cp_by_id))

        # CACHE VERSIONED HISTORICAL TIME LOGS corrective, Fase 3 (ver
        # HANDOFF §10): agregado de bajo costo — SOLO Facturadas XLSX es
        # VERSION-CACHE SAFE en esta fase (ver AUDIT §3/§4 en HANDOFF —
        # Proyectos/Cliente/Horas por proyectos/Client Partner quedan
        # "n/a" hasta que el owner confirme un marcador de versión a nivel
        # de LISTA, o apruebe un TTL explícito). `hit`/`computed`/`waited`
        # colapsan a un booleano 0/1 para el contrato compacto pedido —
        # `waited` cuenta como hit=0 (SÍ implicó trabajo remoto real en
        # OTRO thread, nunca cero costo), mismo criterio que
        # `historical_misses` de Fase 2.
        if cache_scope is not None:
            _perf_logger.info(
                "reporting.perf cartera.cache proyectos_hit=n/a cliente_hit=n/a horas_hit=n/a "
                "facturadas_hit=%s client_partner_hit=n/a",
                1 if _facturadas_cache_outcome == "hit" else (0 if _facturadas_cache_outcome != "n/a" else "n/a"),
            )

        # Los lookups de Horas apuntan al `item["id"]` del target (NO a fields["id"]).
        proy_by_id = {_text(it.get("id")): (it.get("fields") or {}) for it in proyectos_items}
        cli_by_id = {_text(it.get("id")): (it.get("fields") or {}) for it in clientes_items}

        facts: list[dict] = []
        for it in horas_items:
            h = it.get("fields") or {}
            proy = proy_by_id.get(_first(h, *HP_PROYECTO_LOOKUP), {})
            cli = cli_by_id.get(_first(h, *HP_EMPRESA_LOOKUP), {})
            # Client Partner: lookup de la fila Cliente unida → Title del target.
            client_partner = cp_by_id.get(_first(cli, *CLI_CP_LOOKUP), "")
            proyecto_title = _first(proy, PROY_TITLE) or _text(h.get(HP_PROYECTO_TITLE)) or _text(proy.get(PROY_ID_DANWOR))
            tipo_raw = _text(proy.get(PROY_TIPO))
            tipo = "Mantenimiento" if tipo_raw == "Tiempo & Materiales" else tipo_raw
            fecha_inicio = _text(proy.get(PROY_FECHA_INICIO))
            anio_inicio = fecha_inicio[:4] if fecha_inicio else ""
            facts.append({
                "cliente": _text(cli.get(CLI_NOMBRE)),
                "clientPartner": client_partner,
                "proyecto": proyecto_title,
                "idProyecto": _text(proy.get(PROY_ID_DANWOR)),
                "tipoProyecto": tipo,
                "estado": _text(proy.get(PROY_ESTADO)),
                "anio": _text(h.get(HP_ANIO)),
                "mes": _text(h.get(HP_MES)),
                "anioInicio": anio_inicio,
                "hsTrabajadas": _num(h.get(HP_HS_TRABAJADAS)),
                "hsVendidas": _num(h.get(HP_HS_VENDIDAS)),
                "hsCertificadas": _num(h.get(HP_HS_FACTURADAS)),
                # Facturadas source-faithful: perspectiva Proyecto = XLSX (por
                # proyecto). Mantenimiento la resuelve el service (= certificadas).
                "facturadasProyecto": facturadas_by_key.get(_norm_key(proyecto_title), 0.0),
            })
        timer.stage("shaping", len(facts))

        # UNION GRAIN corrective (owner 2026-08-18, ver docstring de
        # `get_cartera_facts_with_project_dimension`): dimensión de proyecto
        # resoluble por SU PROPIO título — Estado/Tipo/idProyecto/anioInicio
        # vienen de `proy_by_id` (YA fetcheado arriba, CERO llamadas nuevas)
        # y son válidos INDEPENDIENTEMENTE de si ese proyecto tiene hechos
        # de "Horas por proyectos" en el `hot_years` de este request.
        # Cliente/ClientPartner son "best effort": solo se conocen si ALGÚN
        # hecho de Horas por proyectos de este mismo fetch los trae — un
        # proyecto sin ningún hecho en el scope actual queda con "" (fail-
        # soft, nunca inventado).
        project_dim: dict[str, dict] = {}
        for proy in proy_by_id.values():
            title = _first(proy, PROY_TITLE)
            if not title:
                continue
            key = _norm_key(title)
            tipo_raw = _text(proy.get(PROY_TIPO))
            tipo = "Mantenimiento" if tipo_raw == "Tiempo & Materiales" else tipo_raw
            fecha_inicio = _text(proy.get(PROY_FECHA_INICIO))
            project_dim[key] = {
                "proyecto": title,
                "idProyecto": _text(proy.get(PROY_ID_DANWOR)),
                "tipoProyecto": tipo,
                "estado": _text(proy.get(PROY_ESTADO)),
                "anioInicio": fecha_inicio[:4] if fecha_inicio else "",
                "cliente": "", "clientPartner": "",
            }
        for f in facts:
            dim = project_dim.get(_norm_key(f["proyecto"]))
            if dim is not None and not dim["cliente"] and f["cliente"]:
                dim["cliente"] = f["cliente"]
                dim["clientPartner"] = f["clientPartner"]

        timer.total()
        return facts, project_dim

    def get_cartera_mantenimiento_anio_estado_pairs(self, access_token: str) -> list[dict]:
        """Pares distintos (año, estado) de Mantenimiento — metadata MÍNIMA
        para poblar el <select> de Año de Cartera (HOT WINDOW / COLD HISTORY
        corrective). Delega en `_anio_estado_pairs(mantenimiento_only=True)`
        — Cartera Mantenimiento SOLO muestra `Tiempo & Materiales`
        (Proyecto agrega la vida completa y nunca usa ventana). SIN
        `cache_scope` a propósito (§13 HANDOFF Reporting/Horas Vendidas —
        "do not touch Mantenimiento") — sigue en la lectura completa de
        siempre."""
        return self._anio_estado_pairs(access_token, mantenimiento_only=True)

    def get_horas_anio_estado_pairs(self, access_token: str, *, cache_scope: str | None = None) -> list[dict]:
        """Pares distintos (año, estado) de TODOS los tipos de proyecto —
        metadata del <select> de Año de Horas Vendidas (que abarca
        Mantenimiento + Proyecto, a diferencia de Cartera Mantenimiento).
        Delega en `_anio_estado_pairs(mantenimiento_only=False)` — MISMA
        lectura mínima, sin duplicar la lógica Graph.

        `cache_scope` (REUSO DE ADQUISICIÓN DELTA corrective, ver HANDOFF):
        `None` (default) preserva el comportamiento EXACTO de siempre —
        Rentabilidad/Participación (`get_rentabilidad_anios`/
        `get_participacion_anios`, MISMO método compartido) NUNCA lo pasan,
        cero cambio para ellos. Solo `get_horas_vendidas_anios` (service)
        opta por un `cache_scope` real."""
        return self._anio_estado_pairs(access_token, mantenimiento_only=False, cache_scope=cache_scope)

    def _anio_estado_pairs(
        self, access_token: str, *, mantenimiento_only: bool, cache_scope: str | None = None,
    ) -> list[dict]:
        """Core compartido de la metadata de años (HOT WINDOW / COLD HISTORY
        corrective, ver HANDOFF: "DO NOT CONFUSE OPTIONS WITH HYDRATED
        FACTS" — un año frío, no hidratado por la ventana caliente por
        defecto, debe seguir siendo una opción SELECCIONABLE, nunca
        escondida). `mantenimiento_only` recorta a `Tiempo & Materiales`
        (Cartera) o incluye todos los tipos (Horas Vendidas).

        `$select` acotado a `A_x00f1_o`+lookup de Proyecto en Horas por
        proyectos (NUNCA Cliente/HsTrabajadas/HsVendidas/HsFacturadas —
        no hacen falta para esto) + Tipo/Status en Proyectos (lista
        pequeña, ya barata de por sí). Camino de siempre (`cache_scope=None`):
        SIGUE pagando una lectura paginada completa de Horas por proyectos
        (Graph no permite "años distintos" sin bajar los items).

        `cache_scope` no-`None` (REUSO DE ADQUISICIÓN DELTA corrective, ver
        HANDOFF — Reporting/Horas Vendidas: "/anios-disponibles hacía una
        SEGUNDA lectura full-history de Horas por proyectos, redundante con
        la de Batch A"): Proyectos y Horas por proyectos se leen del MISMO
        canonical delta state que Cartera/Proyecto y Batch A YA sincronizan
        — key `(cache_scope, site_id, list_id)`, sin distinguir caller. El
        `$select` acotado de arriba deja de aplicar (el delta cold-sync usa
        la proyección UNIÓN de `PROYECTOS_SELECT_FIELDS`/
        `HORAS_PROYECTOS_SELECT_FIELDS`, más ancha pero SIEMPRE superset —
        ver AUDIT en HANDOFF) — el shaping de abajo solo lee los mismos 4
        campos de siempre, el resto de las columnas extra se ignora."""
        self._token = access_token
        site_id = self._require_site_id()
        timer = _StageTimer("anios_disponibles")
        if cache_scope is not None:
            proyectos_items = self._fetch_proyectos_delta(site_id, cache_scope=cache_scope)
        else:
            proyectos_items = self._items(
                site_id, self.proyectos_list_id, "proyectos", [PROY_TITLE, PROY_TIPO, PROY_ESTADO],
            )
        timer.stage("proyectos", len(proyectos_items))
        if cache_scope is not None:
            horas_items = self._fetch_horas_proyectos_delta(site_id, cache_scope=cache_scope)
        else:
            horas_items = self._items(
                site_id, self.horas_proyectos_list_id, "horas_proyectos_anios",
                [HP_ANIO, *HP_PROYECTO_LOOKUP],
            )
        timer.stage("horas_proyectos_anios", len(horas_items))

        tipo_by_id = {_text(it.get("id")): _text((it.get("fields") or {}).get(PROY_TIPO)) for it in proyectos_items}
        estado_by_id = {_text(it.get("id")): _text((it.get("fields") or {}).get(PROY_ESTADO)) for it in proyectos_items}
        pairs: set[tuple[str, str]] = set()
        for it in horas_items:
            h = it.get("fields") or {}
            proy_id = _first(h, *HP_PROYECTO_LOOKUP)
            if mantenimiento_only and tipo_by_id.get(proy_id) != "Tiempo & Materiales":
                continue  # Cartera Mantenimiento SOLO — Proyecto no usa ventana, no aporta acá.
            anio = _text(h.get(HP_ANIO))
            estado = estado_by_id.get(proy_id, "")
            # TEMPORAL DOMAIN CONTRACT — `_is_valid_anio` (nunca solo `if
            # anio:`, ver comentario del helper): un valor no-4-dígitos en
            # `A_x00f1_o` (dato de origen, no derivable del código) nunca
            # debe alcanzar el <select> de Año del browser.
            if _is_valid_anio(anio):
                pairs.add((anio, estado))
        timer.stage("shaping", len(pairs))
        timer.total()
        return [{"anio": a, "estado": e} for a, e in sorted(pairs)]

    # --- Batch A: Horas Vendidas / Participación / Calendario ---------------

    def get_batch_a_facts(
        self, access_token: str, *, hot_years: list[str] | None = None, cache_scope: str | None = None,
    ) -> dict:
        """UN solo fetch (adapter COMPARTIDO) para las experiencias que reusan
        el backbone de Cartera (Horas Vendidas, Participación, Calendario,
        Salud de Cliente — ver real-data-lineage.md sección 8/9). Reusa las
        MISMAS listas/joins que `get_cartera_facts` (Horas por proyectos ×
        Proyectos × Cliente + Client Partner) en UN fetch — nunca fetches
        redundantes de las mismas listas. Devuelve:
          - `project_facts`: hechos proyecto×mes (cliente/clientPartner/
            proyecto/tipoProyecto/mes/anio/horas*) — alimenta Horas
            Vendidas y Participación (agregado por Cliente).
          - `cliente_dim`: una fila por Cliente con las columnas de
            Participación/Calendario/Salud (frecuenciaActualizacion/
            permiteAdelanto/condicionPago/calendarioAct/salud*/
            clientPartner). Los 6 campos de Salud son internal names REALES
            confirmados por diagnóstico Graph en vivo del owner (no
            codificados a mano).

        `hot_years` (SCOPE TEMPORAL OPCIONAL — owner directive: "el mismo
        repository/fetch debe aceptar un scope temporal opcional", separar
        "source reuse" de "one giant shared fetch"): `None` = historia
        COMPLETA (comportamiento de SIEMPRE — lo consume Participación, que
        agrega el gran total sobre TODOS los años hasta que migre a Año/
        Estado). Una lista (`["2026","2025"]`) acota SOLO la lectura de
        "Horas por proyectos" (la tabla de hechos, la cara) — Horas Vendidas
        pide únicamente su ventana caliente sin pagar el volumen de
        Participación. Las listas de dimensión (Proyectos/Cliente/Client
        Partner) NO se acotan por año (son el join, no el hecho — igual que
        en Cartera).

        `cache_scope` (REUSO DE ADQUISICIÓN DELTA corrective, Reporting/
        Horas Vendidas, ver HANDOFF): `None` (default) preserva el
        comportamiento EXACTO de siempre — lecturas completas frescas de
        Proyectos/Cliente/Client Partner + `_fetch_horas_proyectos(hot_years=...)`
        con su propio `$filter` OData. Un `cache_scope` no-`None` reusa el
        MISMO canonical delta state que Cartera/Proyecto YA sincroniza para
        estas 4 fuentes (`_fetch_proyectos_delta`/`_fetch_cliente_delta`/
        `_resolve_client_partners(cache_scope=...)`/`_fetch_horas_proyectos_delta`
        — key `(cache_scope, site_id, list_id)`, SIN distinguir caller: si
        Cartera/Proyecto ya sincronizó esta sesión, Batch A hereda el
        checkpoint tibio SIN una sola llamada Graph propia). Horas por
        proyectos delta SIEMPRE trae la historia COMPLETA (delta no tiene
        noción de `$filter` — es un primitivo de colección completa +
        checkpoint incremental) — `hot_years`, si se pasa, se aplica DESPUÉS
        como una proyección LOCAL en Python (`_filter_horas_items_by_years`,
        el MISMO criterio exacto que ya usaba el fallback de
        `_fetch_horas_proyectos`, nunca una segunda implementación) — jamás
        trunca la vida del proyecto ni cambia semántica de año, solo evita
        el segundo viaje a Graph."""
        self._token = access_token
        site_id = self._require_site_id()
        timer = _StageTimer("batch_a")

        if cache_scope is not None:
            proyectos_items = self._fetch_proyectos_delta(site_id, cache_scope=cache_scope)
        else:
            proyectos_items = self._items(site_id, self.proyectos_list_id, "proyectos")
        timer.stage("proyectos", len(proyectos_items))
        if cache_scope is not None:
            clientes_items = self._fetch_cliente_delta(site_id, cache_scope=cache_scope)
        else:
            clientes_items = self._items(site_id, self.cliente_list_id, "cliente")
        timer.stage("cliente", len(clientes_items))
        # Scope temporal: SOLO acota el hecho (Horas por proyectos). Camino
        # delta: canonical FULL-HISTORY, `hot_years` se aplica LOCALMENTE
        # después (ver docstring). Camino de siempre: `$filter` OData +
        # fallback Python, MISMO helper que Cartera.
        if cache_scope is not None:
            horas_items = self._fetch_horas_proyectos_delta(site_id, cache_scope=cache_scope)
            if hot_years is not None:
                horas_items = _filter_horas_items_by_years(horas_items, hot_years)
            _filter_used = "delta"
        else:
            horas_items, _filter_used = self._fetch_horas_proyectos(site_id, hot_years=hot_years)
        if hot_years is not None:
            _perf_logger.info(
                "reporting.perf batch_a.horas_proyectos_filter hot_years=%s filter_used=%s",
                ",".join(hot_years), _filter_used,
            )
        timer.stage("horas_proyectos", len(horas_items))
        cp_by_id = self._resolve_client_partners(site_id, cache_scope=cache_scope)
        timer.stage("client_partner", len(cp_by_id))

        proy_by_id = {_text(it.get("id")): (it.get("fields") or {}) for it in proyectos_items}
        cli_by_id = {_text(it.get("id")): (it.get("fields") or {}) for it in clientes_items}

        project_facts = []
        for it in horas_items:
            h = it.get("fields") or {}
            cli_id = _first(h, *HP_EMPRESA_LOOKUP)
            proy = proy_by_id.get(_first(h, *HP_PROYECTO_LOOKUP), {})
            cli = cli_by_id.get(cli_id, {})
            client_partner = cp_by_id.get(_first(cli, *CLI_CP_LOOKUP), "")
            proyecto_title = _first(proy, PROY_TITLE) or _text(h.get(HP_PROYECTO_TITLE)) or _text(proy.get(PROY_ID_DANWOR))
            tipo_raw = _text(proy.get(PROY_TIPO))
            tipo = "Mantenimiento" if tipo_raw == "Tiempo & Materiales" else tipo_raw
            project_facts.append({
                "clienteId": cli_id,
                "cliente": _text(cli.get(CLI_NOMBRE)),
                "clientPartner": client_partner,
                "proyecto": proyecto_title,
                "tipoProyecto": tipo,
                # Estado (Proyectos.Status) proyectado al fact — insumo del facet
                # Estado de Horas Vendidas/Rentabilidad (migración Año/Mes/Estado,
                # HANDOFF §-16 #8). Sin esto el shape recibe estado="".
                "estado": _text(proy.get(PROY_ESTADO)),
                "anio": _text(h.get(HP_ANIO)),
                "mes": _text(h.get(HP_MES)),
                "hsTrabajadas": _num(h.get(HP_HS_TRABAJADAS)),
                "hsVendidas": _num(h.get(HP_HS_VENDIDAS)),
                "hsCertificadas": _num(h.get(HP_HS_FACTURADAS)),
            })

        cliente_dim = self._build_cliente_dim(clientes_items, cp_by_id)

        timer.stage("shaping", len(project_facts))
        timer.total()
        return {"project_facts": project_facts, "cliente_dim": cliente_dim}

    def _build_cliente_dim(self, clientes_items: list[dict], cp_by_id: dict) -> list[dict]:
        """Construye `cliente_dim` (una fila por Cliente con Client Partner +
        columnas de Participación/Calendario/Salud) desde la lista Cliente ya
        leída. Extraído para reuso EXACTO entre `get_batch_a_facts` (que además
        necesita el hecho) y `get_cliente_dim` (que NO — Calendario/Salud son
        categóricas puras). Sin lógica Graph acá: solo shaping de items ya
        traídos."""
        dim = []
        for it in clientes_items:
            c = it.get("fields") or {}
            dim.append({
                "clienteId": _text(it.get("id")),
                "cliente": _text(c.get(CLI_NOMBRE)),
                "clientPartner": cp_by_id.get(_first(c, *CLI_CP_LOOKUP), ""),
                "frecuenciaActualizacion": _first(c, *CLI_FRECUENCIA_ACT),
                "permiteAdelanto": _first(c, *CLI_PERMITE_ADELANTO),
                "condicionPago": _first(c, *CLI_CONDICION_PAGO),
                "calendarioAct": _first(c, *CLI_CALENDARIO_ACT),
                "saludFinanciera": _first(c, *CLI_SALUD_FINANCIERA),
                "saludRelacion": _first(c, *CLI_SALUD_RELACION),
                "saludOperativo": _first(c, *CLI_SALUD_OPERATIVO),
                "saludContinuidad": _first(c, *CLI_SALUD_CONTINUIDAD),
                "saludEstrategico": _first(c, *CLI_SALUD_ESTRATEGICO),
                "saludFinal": _first(c, *CLI_SALUD_FINAL),
            })
        return dim

    def get_cliente_dim(self, access_token: str) -> list[dict]:
        """SOLO la dimensión Cliente (+ Client Partner) — PERF corrective
        (owner: Calendario "preloaded"/comercial ~10s). Calendario y Salud de
        Cliente son categóricas PURAS sobre Cliente: NO necesitan el hecho
        "Horas por proyectos" ni la lista Proyectos. Este fetch lean evita la
        lectura full-history más cara (Horas por proyectos) que
        `get_batch_a_facts` hacía y cuyo `project_facts` ya NO consume nadie en
        el endpoint batch-a (Participación migró a su endpoint scopeado). Lee
        únicamente Cliente + resuelve Client Partner."""
        self._token = access_token
        site_id = self._require_site_id()
        timer = _StageTimer("cliente_dim")
        clientes_items = self._items(site_id, self.cliente_list_id, "cliente")
        timer.stage("cliente", len(clientes_items))
        cp_by_id = self._resolve_client_partners(site_id)
        timer.stage("client_partner", len(cp_by_id))
        dim = self._build_cliente_dim(clientes_items, cp_by_id)
        timer.stage("shaping", len(dim))
        timer.total()
        return dim

    # --- Batch B: Time Logs Reports (CSV) -----------------------------------

    def _resolve_time_logs_site_id(self) -> str:
        # PERF instrumentation permanente (bajo costo, MISMO contrato
        # `reporting.perf` que `_StageTimer` — ver HANDOFF Fase 1 §9):
        # queda como observabilidad stage-level, nunca por-item.
        _tl_site_start = time.perf_counter()
        site = _call_graph("time_logs_site", lambda: microsoft365.graph_get_json(
            f"{GRAPH_BASE}/sites/{TIME_LOGS_SITE_PATH}", self._token,
        ))
        site_id = _text((site or {}).get("id"))
        if not site_id:
            raise ReportingSourceError("time_logs_site", "no se pudo resolver el sitio Operaciones")
        _perf_logger.info(
            "reporting.perf time_logs.resolve_site elapsed_ms=%s",
            round((time.perf_counter() - _tl_site_start) * 1000),
        )
        return site_id

    @staticmethod
    def _is_time_logs_csv(item: dict) -> bool:
        """MISMO filtro que el M del PBIP (`[Extension]=".csv"` y
        `not Text.Contains([Name],"OLD")`), factorizado — se aplica
        IDÉNTICO en la raíz y en `Times previos/`, nunca dos reglas
        distintas."""
        name_lower = _text(item.get("name")).lower()
        return name_lower.endswith(".csv") and "old" not in name_lower

    def _list_time_logs_csv_items(self, site_id: str, *, hot_years: list[str] | None = None) -> list[dict]:
        """Adapter de UNA fuente conocida — NO un crawler genérico de
        SharePoint (EXPLICIT SOURCE TOPOLOGY corrective, owner 2026-08-18).

        HISTORIA (para contexto, ya no vigente): la versión anterior de
        este método recorría RECURSIVAMENTE cualquier subcarpeta que
        encontrara bajo `TIME_LOGS_FOLDER_PATH` — introducido para llegar a
        `Times previos/` (owner incident, "Horas Productivas solo muestra
        2026 aunque hay históricos"). Un diagnóstico real (owner
        2026-08-19, page-by-page timing) probó que esa recursión genérica
        entraba a subcarpetas NO relacionadas con Time Logs — la raíz tiene
        ≥5 subcarpetas, y al menos una tiene anidamiento propio — sumando
        ~55s de llamadas Graph reales pero completamente irrelevantes para
        esta fuente. El contrato de origen real es SOLO 2 ubicaciones
        conocidas y planas:

            Template Dashboard/                    → Time Logs Reports.csv (actual)
            Template Dashboard/Times previos/       → Time Logs Reports YYYY.csv (histórico)

        Nunca más profundo, nunca otra carpeta. `TIME_LOGS_HISTORY_FOLDER_
        NAME` es la ÚNICA subcarpeta que este método conoce — matcheada por
        nombre (case-insensitive), no por posición ni por ser "la primera
        carpeta encontrada". Si en el futuro el histórico se reorganiza a
        otra ubicación, este método necesita un cambio explícito — nunca
        vuelve a "caminar todo y filtrar después".

        FILE DISCOVERY OPTIMIZADO (CALL BUDGET corrective, 2026-08-18):
        `hot_years` (`None` = listado completo — usado por
        `get_time_logs_years_available`/Rentabilidad/wrappers standalone,
        que necesitan el catálogo/historial completo) — con una lista
        EXPLÍCITA, primero se lista SOLO la raíz (1 llamada); si esos
        archivos YA cubren TODOS los años pedidos (el caso típico: pedir
        el año actual, que SIEMPRE vive en la raíz sin sufijo, ver
        `_infer_time_logs_csv_year`), se devuelve sin listar `Times
        previos/` en absoluto — esa carpeta solo se abre cuando algún año
        pedido no aparece en la raíz (años fríos históricos), y esa
        apertura es SIEMPRE un único listado de sus children directos,
        nunca una recursión."""
        # PERF instrumentation permanente (bajo costo, MISMO contrato
        # `reporting.perf` que `_StageTimer` — ver HANDOFF Fase 1 §9).
        _tl_list_start = time.perf_counter()
        root_url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{_quote(TIME_LOGS_FOLDER_PATH)}:/children"
        root_children = _call_graph("time_logs_folder", lambda: microsoft365.graph_get_paged_values(
            root_url, access_token=self._token,
        ))
        root_files = [it for it in root_children if not it.get("folder") and self._is_time_logs_csv(it)]
        _perf_logger.info(
            "reporting.perf time_logs.list_root elapsed_ms=%s items=%s",
            round((time.perf_counter() - _tl_list_start) * 1000), len(root_files),
        )
        if hot_years is not None:
            wanted = set(hot_years)
            have = {_infer_time_logs_csv_year(it) for it in root_files}
            if wanted <= have:
                return root_files

        # Buscar `Times previos/` POR NOMBRE — nunca "la primera carpeta
        # encontrada" ni una recursión sobre TODAS las carpetas de la raíz.
        history_id = ""
        for it in root_children:
            if it.get("folder") and _text(it.get("name")).strip().casefold() == TIME_LOGS_HISTORY_FOLDER_NAME.casefold():
                history_id = _text(it.get("id"))
                break
        if not history_id:
            # Sin `Times previos/` en la raíz: solo hay lo que ya está en
            # `root_files` — nunca se inventa una carpeta ni se recorre
            # ninguna otra buscándola.
            return root_files

        # Sin `_quote`: MISMO criterio que `_download_time_logs_rows`
        # (`/drive/items/{item_id}/content`) — el id de Graph nunca se
        # url-encodea acá.
        _tl_prev_start = time.perf_counter()
        history_url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{history_id}/children"
        history_children = _call_graph("time_logs_folder", lambda: microsoft365.graph_get_paged_values(
            history_url, access_token=self._token,
        ))
        history_files = [it for it in history_children if not it.get("folder") and self._is_time_logs_csv(it)]
        _perf_logger.info(
            "reporting.perf time_logs.list_previous elapsed_ms=%s items=%s",
            round((time.perf_counter() - _tl_prev_start) * 1000), len(history_files),
        )
        return root_files + history_files

    def _download_time_logs_rows(
        self, site_id: str, *, hot_years: list[str] | None = None, cache_scope: str | None = None,
    ) -> list[dict]:
        """Descarga + combina CSV de la carpeta (MISMA semántica que
        `Table.Combine`/header promovido del M: delimiter=",", UTF-8). Un
        archivo individual inválido (headers requeridos faltantes, o no
        decodifica) se SALTEA sin romper el combine completo (fail-soft
        por-archivo — un CSV legacy/corrupto no debe tumbar toda la
        experiencia). Si NINGÚN archivo aporta filas válidas,
        `ReportingSourceError` (falla real de fuente, no un dataset vacío
        legítimo).

        `hot_years` (HOT WINDOW / COLD HISTORY corrective, Horas
        Productivas — owner decision 2026-08-18): `None` (default,
        preserva el contrato previo EXACTO — Rentabilidad y los wrappers
        standalone de Productivas siguen sin acotar) descarga TODOS los
        archivos encontrados. Una lista explícita SELECCIONA los archivos
        ANTES de descargar (`_infer_time_logs_csv_year`, por nombre — nunca
        post-filtro después de bajar todo) — si ningún archivo mapea a esos
        años (p. ej. un año futuro sin export todavía), es un dataset
        VACÍO legítimo (`[]`), no un `ReportingSourceError` — ese error
        sigue reservado a la fuente genuinamente vacía/rota.

        PERF PHASE 1A (owner corrective, ver HANDOFF — root cause
        confirmado por timing real: 6 descargas CSV secuenciales ~24s de
        los ~26.5s de `time_logs.total`): cada archivo se descarga+parsea
        en su PROPIO worker (`_download_one_time_logs_file_cached`),
        acotado a `_TIME_LOGS_MAX_WORKERS` concurrentes — SOLO cambia la
        topología de ejecución (secuencial → pool acotado), CERO cambio de
        selección de archivos/parseo/agregación/semántica de error (ver
        `_download_one_time_logs_file` para el criterio fail-soft
        por-archivo, IDÉNTICO al que tenía el loop secuencial). Resultados
        recolectados por índice y mergeados en el ORDEN ORIGINAL de
        `items` (determinismo — MISMO orden final que la versión
        secuencial, nunca por orden de finalización del pool). Una
        excepción inesperada de un worker (cualquier cosa que no sea el
        fail-soft ya cubierto adentro) se propaga tal cual lo hacía la
        versión secuencial — se cancelan los futures que todavía no
        arrancaron y se re-lanza, sin swallowear nada.

        `cache_scope` (CACHE VERSIONED HISTORICAL TIME LOGS corrective,
        Fase 2 histórico + Fase 3B actual, ver reporting_time_logs_cache.py):
        `None` (default) preserva el comportamiento EXACTO de siempre —
        SIN caching, cada archivo se descarga fresco, MISMO contrato que
        `get_time_logs_facts` sigue usando (Batch B/Rentabilidad NO opta
        por esta fase). Un `cache_scope` no-`None` (una identidad estable
        del usuario Hub autenticado — NUNCA el `graph_token`) activa la
        cache versionada por eTag para TODOS los archivos — históricos
        (Fase 2) Y el actual (Fase 3B, `Time Logs Reports.csv`: MISMO tipo
        de recurso driveItem, MISMO contrato de versión — un eTag sin
        cambios sirve el resultado cacheado, un eTag distinto dispara
        descarga+parseo fresco, nunca una ventana de tiempo arbitraria).

        AÑO CERRADO DURABLE (Integral Cold Start Fase 1, ver HANDOFF —
        `reporting_time_logs_durable.py`): cuando el caller conoce sus
        años de antemano (`hot_years` explícito — Rentabilidad/Horas
        Vendidas), cada año YA cerrado (`is_closed_year`) con entrada
        durable válida se resuelve ACÁ, ANTES de listar la carpeta — ese
        año nunca vuelve a tocar Graph en absoluto para esta request. Los
        años restantes (abiertos, o cerrados sin entrada durable todavía)
        siguen el descubrimiento de siempre. En modo vida completa
        (`hot_years=None`, Cartera/Proyecto) no hay forma de saber de
        antemano qué años existen — la carpeta se sigue listando igual que
        siempre, pero cada ITEM descubierto cuyo año ya está cerrado se
        resuelve durable dentro de `_download_one_time_logs_file_cached`
        (nunca acá), evitando la descarga+parseo real — el costo dominante
        ya medido (~24-27s), aunque la llamada de listado en sí se
        preserve (es barata y además la única forma de descubrir un año
        cerrado nuevo que todavía no tiene entrada durable)."""
        durable_years: dict[str, list[dict]] = {}
        hot_years_to_discover = hot_years
        if cache_scope is not None:
            reporting_time_logs_durable.ensure_restored()
            if hot_years is not None:
                still_needed = []
                for year in hot_years:
                    if reporting_time_logs_durable.is_closed_year(year):
                        durable_rows = reporting_time_logs_durable.load_closed_year_entry(cache_scope, year)
                        if durable_rows is not None:
                            durable_years[year] = durable_rows
                            continue
                    still_needed.append(year)
                hot_years_to_discover = still_needed

        if hot_years_to_discover is not None and not hot_years_to_discover:
            # Todos los años pedidos ya se sirvieron durable — nunca se
            # lista la carpeta para esta request.
            items: list[dict] = []
        else:
            items = self._list_time_logs_csv_items(site_id, hot_years=hot_years_to_discover)
            if not items and not durable_years:
                raise ReportingSourceError("time_logs_files", "no se encontraron CSV en Template Dashboard")
            if hot_years_to_discover is not None:
                wanted = set(hot_years_to_discover)
                items = [it for it in items if _infer_time_logs_csv_year(it) in wanted]
                if not items and not durable_years:
                    return []

        results: list[dict | None] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=_TIME_LOGS_MAX_WORKERS) as pool:
            future_to_index = {
                pool.submit(self._download_one_time_logs_file_cached, site_id, it, cache_scope): idx
                for idx, it in enumerate(items)
            }
            try:
                for future in as_completed(future_to_index):
                    results[future_to_index[future]] = future.result()
            except Exception:
                # Ningún worker YA ARRANCADO puede interrumpirse (threads,
                # no procesos) — pero los que todavía están en cola sí se
                # cancelan, para no seguir pagando llamadas Graph después de
                # una falla real. La excepción original se re-lanza intacta
                # (mismo criterio que el loop secuencial: nada se swallowea).
                for f in future_to_index:
                    f.cancel()
                raise

        # Los años servidos durable ANTES de listar (§ arriba) nunca pasan
        # por el pool — se agregan acá directo, MISMO destino final
        # (`rows`) que cualquier otra fuente de filas.
        rows: list[dict] = []
        for year_rows in durable_years.values():
            rows.extend(year_rows)
        _tl_download_total_ms = 0.0
        _tl_parse_total_ms = 0.0
        _hist_hits = _hist_misses = _hist_downloads = 0
        # Cuenta TANTO los años resueltos antes de listar (Layer 1,
        # `durable_years`) COMO los items descubiertos que igual resolvieron
        # durable dentro del pool (Layer 2, modo vida completa — ver
        # `_download_one_time_logs_file_cached`) — un solo contador,
        # `durable_hits`, para la pregunta que el owner necesita responder
        # tras el restart: "¿cuántos años cerrados NUNCA tocaron Graph para
        # su contenido?".
        _hist_durable_hits = len(durable_years)
        _current_hit = _current_download = 0
        for outcome in results:
            if outcome is None:
                continue
            _tl_download_total_ms += outcome["download_ms"]
            _tl_parse_total_ms += outcome["parse_ms"]
            rows.extend(outcome["rows"])
            cache_outcome = outcome.get("cache_outcome", "n/a")
            if cache_outcome == "n/a":
                continue
            # Fase 3B: MISMO contrato hit/computed/waited para el archivo
            # actual que para los históricos — solo cambia a qué contador
            # (historical_*/current_*) aporta, ver docstring de
            # `_download_one_time_logs_file_cached`. `durable_closed_year`
            # (Integral Cold Start Fase 1) es un cuarto outcome posible
            # SOLO para históricos — año cerrado resuelto durable dentro
            # del pool, cero descarga/parseo real.
            if outcome.get("is_historical"):
                if cache_outcome == "durable_closed_year":
                    _hist_durable_hits += 1
                elif cache_outcome == "hit":
                    _hist_hits += 1
                elif cache_outcome == "computed":
                    _hist_misses += 1
                    _hist_downloads += 1
                elif cache_outcome == "waited":
                    _hist_misses += 1
            else:
                if cache_outcome == "hit":
                    _current_hit = 1
                else:  # "computed" o "waited" — ambos implicaron trabajo remoto real (propio o de otro thread)
                    _current_download = 1
        _perf_logger.info(
            "reporting.perf time_logs.download_total elapsed_ms=%s", round(_tl_download_total_ms),
        )
        _perf_logger.info(
            "reporting.perf time_logs.parse_total elapsed_ms=%s", round(_tl_parse_total_ms),
        )
        if cache_scope is not None:
            _perf_logger.info(
                "reporting.perf time_logs.cache historical_hits=%s historical_misses=%s "
                "historical_downloads=%s current_hit=%s current_download=%s",
                _hist_hits, _hist_misses, _hist_downloads, _current_hit, _current_download,
            )
            # DURABLE VERSIONED STATE FOR HISTORICAL TIME LOGS corrective
            # (Integral Cold Start Fase 1, ver HANDOFF) — SOLO agrega, nunca
            # reemplaza la línea de arriba (§13 del ticket original: "Keep
            # existing stage names... do not rename instrumentation").
            # NUNCA scope/drive_item_id/eTag/filas de negocio — sólo
            # conteos y un elapsed_ms de arranque, MISMO criterio de
            # seguridad que el resto de este archivo.
            _perf_logger.info(
                "reporting.perf time_logs.closed_year_durable durable_hits=%s restore_elapsed_ms=%s",
                _hist_durable_hits, reporting_time_logs_durable.restore_elapsed_ms(),
            )
        if not rows:
            raise ReportingSourceError("time_logs_rows", "ningún CSV produjo filas válidas")
        return rows

    def _download_one_time_logs_file(self, site_id: str, item: dict) -> dict | None:
        """Unidad de trabajo del pool de Fase 1A — descarga+parsea UN
        archivo Time Logs. Sin estado mutable compartido: `site_id`/`item`
        son locales al worker (cada llamada recibe su propia referencia,
        nunca una variable de loop reusada — el bug clásico de closures
        sobre `it`), `data`/`text`/`reader`/`file_rows` son todas variables
        locales de esta llamada. `self._token` se LEE (nunca se escribe
        acá) — ya fue fijado por el método público antes de lanzar el pool,
        ver AUDIT de thread-safety en HANDOFF.

        Devuelve `None` si el item no tiene id (mismo `continue` silencioso
        que la versión secuencial); si no, un dict `{rows, download_ms,
        parse_ms}` — incluso cuando `rows` queda vacío (archivo fail-soft:
        error de transporte, no decodifica, o headers requeridos
        faltantes), MISMO criterio exacto que el loop secuencial anterior,
        con logging equivalente al `status=failed`/`status=bad_headers` que
        tenía como líneas INFO por-archivo (retirado en Fase 1 por
        demasiado verboso para producción, ver HANDOFF §9 — el agregado
        `time_logs.download_total`/`time_logs.parse_total` alcanza como
        observabilidad permanente)."""
        item_id = _text(item.get("id"))
        if not item_id:
            return None
        _tl_dl_start = time.perf_counter()
        try:
            data = _call_graph("time_logs_download", lambda: microsoft365.graph_get_bytes(
                f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content", self._token,
            ))
            text = data.decode("utf-8-sig")
        except (ReportingGraphError, UnicodeDecodeError):
            return {"rows": [], "download_ms": (time.perf_counter() - _tl_dl_start) * 1000, "parse_ms": 0.0}
        _tl_dl_elapsed_ms = (time.perf_counter() - _tl_dl_start) * 1000
        _tl_parse_start = time.perf_counter()
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or not all(h in reader.fieldnames for h in TIME_LOGS_REQUIRED_HEADERS):
            return {
                "rows": [], "download_ms": _tl_dl_elapsed_ms,
                "parse_ms": (time.perf_counter() - _tl_parse_start) * 1000,
            }
        file_rows = list(reader)
        _tl_parse_elapsed_ms = (time.perf_counter() - _tl_parse_start) * 1000
        return {"rows": file_rows, "download_ms": _tl_dl_elapsed_ms, "parse_ms": _tl_parse_elapsed_ms}

    def _download_one_time_logs_file_cached(
        self, site_id: str, item: dict, cache_scope: str | None,
    ) -> dict | None:
        """Wrapper de `_download_one_time_logs_file` con la cache
        versionada (Fase 2 histórico + Fase 3B actual, ver HANDOFF —
        `EXTEND THE SAME VERSION-AWARE PARSED-RESULT CACHE TO THE CURRENT
        FILE`, ver reporting_time_logs_cache.py). `cache_scope=None`
        (default de siempre) salta la cache por completo — comportamiento
        IDÉNTICO a `_download_one_time_logs_file` directo, MISMO
        `download_ms`/`parse_ms` reales — MISMO camino que
        `get_time_logs_facts` (Batch B/Rentabilidad NUNCA optan por esta
        fase).

        Con `cache_scope` no-`None`, TODO archivo (histórico o actual —
        Fase 3B eliminó la distinción, ambos son el MISMO tipo de recurso
        driveItem y su metadata ya viene en la MISMA respuesta de listado
        de carpeta, ver AUDIT en HANDOFF) usa clave `(cache_scope,
        item_id, version)` — `version` = `eTag` (o `lastModifiedDateTime
        + size` si faltara, ver `_drive_item_version`), CERO llamada
        Graph extra para versión. `get_or_compute` es single-flight (ver
        `TimeLogsHistoricalCache` — dos requests concurrentes para la
        MISMA key nunca disparan dos descargas). El VALOR cacheado son
        las filas CSV crudas ya parseadas — la MISMA representación que
        `_download_one_time_logs_file` produce hoy, CERO fórmula de
        negocio agregada acá (eso sigue viviendo, sin cambios, en
        `get_time_logs_worked_hours`/`get_time_logs_facts`). El eTag del
        archivo actual cambia cuando su contenido cambia — MISMO contrato
        de versión ya aceptado para los históricos, nunca una ventana de
        tiempo arbitraria.

        `download_ms`/`parse_ms` reportados: el costo REAL de esta
        llamada — 0/0 en un `"hit"` (verdad literal: no bajó ni parseó
        nada), el split real de descarga/parseo en un `"computed"` (el
        único worker que efectivamente pagó el costo), 0/0 en un
        `"waited"` (esperó el resultado de otro thread, no un segundo
        download propio — el tiempo de espera real se ve igual en el
        `time_logs.total` exterior, no se duplica acá). `is_historical`
        viaja en el resultado SOLO para que `_download_time_logs_rows`
        pueda separar los contadores `historical_*`/`current_*` de
        observabilidad — nunca afecta la selección/parseo/agregación."""
        item_id = _text(item.get("id"))
        if not item_id:
            return None
        is_historical = _is_historical_time_logs_item(item)
        if cache_scope is None:
            outcome = self._download_one_time_logs_file(site_id, item)
            if outcome is not None:
                outcome["cache_outcome"] = "n/a"
                outcome["is_historical"] = is_historical
            return outcome

        # AÑO CERRADO DURABLE — Layer 2 (Integral Cold Start Fase 1, ver
        # HANDOFF/reporting_time_logs_durable.py): cubre el modo vida
        # completa (`hot_years=None`, Cartera/Proyecto — `_download_time_logs_rows`
        # no pudo filtrar este item ANTES de listar porque no conocía el
        # universo de años de antemano). `_infer_time_logs_csv_year`
        # resuelve el archivo "actual" (sin sufijo) al año de HOY siempre
        # — nunca puede clasificar como cerrado por construcción, así que
        # este short-circuit nunca alcanza al archivo en curso sin
        # necesitar un chequeo `is_historical` separado (igual se deja
        # como guarda explícita, ver docstring del módulo). CERO descarga,
        # CERO parseo — la garantía de frescura es el contrato de negocio
        # de año cerrado, no un eTag (a diferencia del camino de abajo).
        if is_historical:
            year = _infer_time_logs_csv_year(item)
            if reporting_time_logs_durable.is_closed_year(year):
                durable_rows = reporting_time_logs_durable.load_closed_year_entry(cache_scope, year)
                if durable_rows is not None:
                    return {
                        "rows": durable_rows, "download_ms": 0.0, "parse_ms": 0.0,
                        "cache_outcome": "durable_closed_year", "is_historical": True,
                    }

        key = (cache_scope, item_id, _drive_item_version(item))
        inner_outcome: dict | None = None

        def _compute() -> list[dict]:
            nonlocal inner_outcome
            inner_outcome = self._download_one_time_logs_file(site_id, item)
            return inner_outcome["rows"] if inner_outcome is not None else []

        rows, cache_outcome = _TIME_LOGS_HISTORICAL_CACHE.get_or_compute(key, _compute)
        if cache_outcome == "computed" and inner_outcome is not None:
            download_ms, parse_ms = inner_outcome["download_ms"], inner_outcome["parse_ms"]
            # Primera vez (o entrada durable vieja/inválida) que este año
            # cerrado se resuelve con éxito en este proceso — lo persiste
            # para que el PRÓXIMO restart nunca vuelva a tocar Graph por
            # él (ver `save_closed_year_entry`: best-effort, nunca rompe
            # esta request si la escritura falla).
            if is_historical and rows:
                year = _infer_time_logs_csv_year(item)
                if reporting_time_logs_durable.is_closed_year(year):
                    reporting_time_logs_durable.save_closed_year_entry(
                        cache_scope, year, item_id, _drive_item_version(item), rows,
                    )
        else:
            download_ms, parse_ms = 0.0, 0.0
        return {
            "rows": rows, "download_ms": download_ms, "parse_ms": parse_ms,
            "cache_outcome": cache_outcome, "is_historical": is_historical,
        }

    def get_time_logs_facts(self, access_token: str, *, hot_years: list[str] | None = None) -> list[dict]:
        """Hechos de Time Logs Reports normalizados y unidos a Cliente vía
        Proyectos — alimenta Horas Productivas (AMBAS perspectivas,
        Productivas Y No Asignadas) y Rentabilidad/Ejecutadas (Batch B).

        Join: `Project` (CSV, texto crudo) → clave normalizada (Upper/sin
        espacios, `_norm_key` — MISMA regla que `Key_proyect_desc` del PBIP
        en AMBAS tablas involucradas) → título de Proyectos normalizado →
        `EmpresaLookupId` (MISMO lookup ya CONFIRMADO contra Graph real por
        `staffing_repository.py`) → Cliente.id → Cliente.Title. Reusa las
        MISMAS listas Proyectos/Cliente que Cartera/Batch A (fetch propio
        acá — combinación de fuentes distinta, un solo fetch por request de
        Time Logs, sin fetches redundantes dentro de este método). Un
        Project sin match en Proyectos (o Proyecto sin Cliente resuelto)
        conserva el texto crudo de Client/Project del CSV como fallback —
        nunca descarta la fila silenciosamente.

        `hot_years` (HOT WINDOW / COLD HISTORY corrective, Horas
        Productivas — owner decision 2026-08-18): `None` (default)
        preserva el contrato PREVIO exacto — descarga TODOS los CSV
        encontrados (Rentabilidad y los wrappers standalone de Productivas
        siguen llamando así, sin cambios de comportamiento). Una lista
        explícita acota la SELECCIÓN DE ARCHIVOS antes de descargar (ver
        `_download_time_logs_rows`) — usado por `get_batch_b` para la
        ventana caliente/año frío, MISMO mecanismo que `hot_years` de
        "Horas por proyectos" (`_fetch_horas_proyectos`), pero acá aplicado
        a selección de archivo en vez de `$filter` OData."""
        self._token = access_token
        site_id = self._require_site_id()
        timer = _StageTimer("time_logs")
        operaciones_site_id = self._resolve_time_logs_site_id()
        timer.stage("operaciones_site")

        proyectos_items = self._items(site_id, self.proyectos_list_id, "proyectos")
        timer.stage("proyectos", len(proyectos_items))
        clientes_items = self._items(site_id, self.cliente_list_id, "cliente")
        timer.stage("cliente", len(clientes_items))
        porcentaje_part_field = self._resolve_porcentaje_part_field(site_id)
        timer.stage("cliente_columns_porcentaje_part")
        proy_by_key: dict[str, dict] = {}
        for it in proyectos_items:
            fields = it.get("fields") or {}
            key = _norm_key(_first(fields, PROY_TITLE))
            if key:
                proy_by_key[key] = fields
        cli_by_id = {_text(it.get("id")): (it.get("fields") or {}) for it in clientes_items}

        raw_rows = self._download_time_logs_rows(operaciones_site_id, hot_years=hot_years)
        timer.stage("csv_download_parse", len(raw_rows))
        facts = []
        for row in raw_rows:
            project_raw = _text(row.get("Project"))
            proy = proy_by_key.get(_norm_key(project_raw), {})
            cli = cli_by_id.get(_first(proy, *HP_EMPRESA_LOOKUP), {})
            facts.append({
                "cliente": _text(cli.get(CLI_NOMBRE)) or _text(row.get("Client")),
                "proyecto": _first(proy, PROY_TITLE) or project_raw,
                # Estado (Proyectos.Status) — owner corrective: Horas
                # Productivas necesita un filtro de Estado como el resto del
                # Hub. `proy` YA trae el item completo de Proyectos (mismo
                # join usado para cliente/porcentajePart arriba) — MISMO
                # campo que Cartera/Rentabilidad leen (`PROY_ESTADO`), nunca
                # un segundo fetch. Proyecto sin match ⇒ "" (fail-soft,
                # queda fuera de ambos checkboxes de Estado hasta que el
                # join se resuelva, nunca un valor inventado).
                "estado": _text(proy.get(PROY_ESTADO)),
                "task": _text(row.get("Task")),
                "email": _text(row.get("Email")),
                # FINAL RECONCILIATION CORRECTIVE (owner live golden case
                # 2026/Julio/Mantenimiento/En curso: PBI Trabajadas=4.568, Hub
                # daba 4,6 = 4568/1000 EXACTO). `Duration Decimal` YA es horas
                # decimales (estándar de export de time-tracking: p. ej. "8,5" =
                # 8h30m); `_num` la parsea correctamente y la SUMA ya coincidía
                # con el PBI (4.568). El `/1000` era una división ESPURIA —
                # asumía milli-horas, contradicho por la evidencia viva (una
                # sola división 1000×, no doble: si `_num` mis-parseara por
                # locale el resultado sería ~0,0046, no 4,6). PBIP: HORAS =
                # SUM(Time Logs Reports[HORAS]) = Duration Decimal, SIN escala.
                # (La nota `/1000` de real-data-lineage.md quedó desmentida por
                # el caso golden — ver HANDOFF.)
                "horas": _num(row.get("Duration Decimal")),
                "mes": _time_logs_mes(row.get("Date")),
                "anio": _time_logs_anio(row.get("Date")),
                # "Cliente + Part" (Horas Productivas, panel derecho — ver
                # _resolve_porcentaje_part_field arriba): fracción CRUDA
                # (0-1) tal cual SharePoint la guarda — el *100 vive en el
                # service (shaping de negocio), MISMO criterio que el resto
                # de este método. `porcentaje_part_field` DESCUBIERTO en
                # vivo, nunca un candidato codificado a mano — "" (columna no
                # encontrada) ⇒ 0.0, fail-soft.
                "porcentajePart": _num(cli.get(porcentaje_part_field)) if porcentaje_part_field else 0.0,
            })
        timer.stage("shaping", len(facts))
        timer.total()
        return facts

    def get_time_logs_worked_hours(
        self, access_token: str, *, hot_years: list[str] | None = None, cache_scope: str | None = None,
    ) -> list[dict]:
        """Proyección MÍNIMA de Time Logs — `{proyecto, anio, mes, horas,
        task}` por fila, SIN resolver Proyectos/Cliente/porcentaje_part
        (CALL BUDGET corrective, owner decision 2026-08-18 — Cartera
        reportó 4x más llamadas Graph tras la reconciliación: 2/3 de esa
        duplicación era releer Proyectos/Cliente/columnas que Cartera YA
        había leído para "Horas por proyectos", solo para enriquecer
        campos de Time Logs — `cliente`/`estado`/`porcentajePart` — que
        Cartera (y Rentabilidad) NUNCA consumen de acá; ambos hacen SU
        PROPIO join por `_norm_key(proyecto)` contra datos que ya tienen.

        `proyecto` acá es SIEMPRE el texto CRUDO del CSV (Project), nunca
        resuelto contra Proyectos — `_norm_key` (Upper/sin espacios) ya
        normaliza ambos lados del join sin necesitar el título resuelto
        (MISMA base que el fallback de `get_time_logs_facts` cuando el
        join falla — acá es la ÚNICA vía, nunca un fetch más liviano que
        cambie el resultado del join real del PBIP).

        Horas Productivas (Batch B) SIGUE usando `get_time_logs_facts`
        (el contrato completo) — necesita cliente/estado/porcentajePart
        para su propia UI (agrupar por Cliente, filtro de Estado, panel
        "Cliente + Part"). Este método es SOLO para consumidores que
        necesitan nada más que el agregado proyecto×año×mes×horas
        (Cartera/Mantenimiento — ver `get_cartera_mantenimiento`).

        `hot_years`: MISMO mecanismo de source-loading que
        `get_time_logs_facts` (selecciona archivos por nombre ANTES de
        descargar, ver `_download_time_logs_rows`).

        `cache_scope` (CACHE VERSIONED HISTORICAL TIME LOGS corrective,
        Fase 2): `None` (default) preserva el comportamiento EXACTO de
        siempre — sin caching. Un `cache_scope` no-`None` (identidad
        estable del usuario Hub — NUNCA el `graph_token`) activa la cache
        de archivos históricos en `_download_time_logs_rows`. Hoy SOLO
        `get_cartera_proyecto` opta por esto — Mantenimiento (ventana
        caliente ya acotada) y Rentabilidad/Batch B (`get_time_logs_facts`,
        contrato completo distinto) quedan sin tocar, Fase 2 es
        deliberadamente de una sola fuente/un solo consumidor."""
        # PERF instrumentation permanente (bajo costo, MISMO contrato
        # `reporting.perf` que `_StageTimer` — ver HANDOFF Fase 1 §9).
        _tl_total_start = time.perf_counter()
        self._token = access_token
        operaciones_site_id = self._resolve_time_logs_site_id()
        raw_rows = self._download_time_logs_rows(operaciones_site_id, hot_years=hot_years, cache_scope=cache_scope)
        result = [
            {
                "proyecto": _text(row.get("Project")),
                "mes": _time_logs_mes(row.get("Date")),
                "anio": _time_logs_anio(row.get("Date")),
                "horas": _num(row.get("Duration Decimal")),
                # `task` (owner ticket "URGENT REPORTING CORRECTNESS — HNP
                # TASK + SOLD HOURS YEAR ACCUMULATION"): Rentabilidad/"No
                # asignadas a servicios" necesita distinguir la tarea "01. No
                # Asignación a Servicio en Cliente" de otras tareas del MISMO
                # proyecto placeholder HNP (Vacaciones, etc.) — sin esto, el
                # concepto sumaba el proyecto ENTERO. CERO llamada Graph
                # extra: `row` ya es la fila CRUDA completa del CSV ya
                # descargado por `_download_time_logs_rows` arriba (MISMA
                # fuente que `get_time_logs_facts` lee `row.get("Task")`,
                # ver ahí) — el CALL BUDGET corrective (ver docstring) es
                # sobre evitar releer Proyectos/Cliente, nunca sobre qué
                # columnas de una fila YA en memoria se proyectan.
                "task": _text(row.get("Task")),
            }
            for row in raw_rows
        ]
        _perf_logger.info(
            "reporting.perf time_logs.total elapsed_ms=%s rows=%s",
            round((time.perf_counter() - _tl_total_start) * 1000), len(result),
        )
        return result

    def get_time_logs_years_available(self, access_token: str) -> list[str]:
        """Metadata LIVIANA de años disponibles de Time Logs (HOT WINDOW /
        COLD HISTORY corrective, Horas Productivas) — SOLO lista los
        archivos de `TIME_LOGS_FOLDER_PATH` (Graph metadata, nunca
        descarga/parsea ningún CSV) e infiere el año de cada uno por su
        nombre (`_infer_time_logs_csv_year`). Separada de
        `get_time_logs_facts` a propósito — MISMO criterio que
        `get_cartera_mantenimiento_anio_estado_pairs`: las OPCIONES del
        <select> de Año nunca deben confundirse con los años ya
        HIDRATADOS por la ventana caliente."""
        self._token = access_token
        operaciones_site_id = self._resolve_time_logs_site_id()
        items = self._list_time_logs_csv_items(operaciones_site_id)
        return sorted({_infer_time_logs_csv_year(it) for it in items})

    def get_colaboradores_email_map(self, access_token: str) -> dict[str, str]:
        """Email (lowercase) → nombre de Consultor. El person field
        `Colaborador` NO expone email en Graph (LIVE GRAPH VALIDATED:
        ninguna variante de `$select` sobre Colaboradores devuelve un
        campo con "@" — ni `Colaborador`, `Colaborador_x002e_` ni
        `Colaborador_x002e_LookupId`); el email real vive en la SharePoint
        User Information List (`config.SHAREPOINT_USER_INFORMATION_LIST_ID`,
        template `userInformation`, MISMO sitio Gestión), resuelta por:

            Colaboradores.Colaborador_x002e_LookupId → UserInfo.id → UserInfo.Name

        `Name` es un claims/login SharePoint (`i:0#.f|membership|user@dom`,
        LIVE GRAPH VALIDATED: 148/148 Colaboradores con LookupId resuelto
        tienen `|` en `Name`, 0 son email puro) — el email real es el
        último segmento tras el último `|`. Reproduce la relación TMDL del
        PBIP `Time Logs Reports.Email → Colaboradores.EMail_Colaborador`
        (esa columna del modelo es una expansión de Power Query del person
        field, no un internal name literal de Graph). Bulk: UNA lectura de
        Colaboradores + UNA lectura de User Information List, nunca N+1."""
        self._token = access_token
        site_id = self._require_site_id()
        timer = _StageTimer("colaboradores")
        items = self._items(site_id, self.colaboradores_list_id, "colaboradores")
        timer.stage("colaboradores", len(items))
        userinfo_items = self._items(site_id, self.user_information_list_id, "user_information")
        timer.stage("user_information", len(userinfo_items))
        name_by_id = {_text(it.get("id")): _text((it.get("fields") or {}).get(USER_INFO_NAME)) for it in userinfo_items}
        mapping: dict[str, str] = {}
        for it in items:
            fields = it.get("fields") or {}
            title = _first(fields, *COLAB_TITLE)
            lookup_id = _text(fields.get(COLAB_LOOKUP_ID))
            if not title or not lookup_id:
                continue
            name = name_by_id.get(lookup_id)
            if not name:
                continue
            email = (name.rsplit("|", 1)[-1] if "|" in name else name).strip().lower()
            if "@" not in email:
                continue
            mapping[email] = title
        timer.total()
        return mapping


def _quote(path: str) -> str:
    from urllib.parse import quote
    return quote(path)


def _read_facturadas_table(data: bytes, required: tuple[str, ...]) -> tuple[dict[str, int], list[tuple]]:
    """Extrae la tabla de Facturadas desde bytes de XLSX (openpyxl read-only/
    data-only, en memoria, sin persistir). Devuelve (header→índice, filas).

    NO asume que la tabla arranque en A1 ni en la primera hoja: `read_only` de
    openpyxl NO expone `ws.tables` (la metadata de Excel Table no se parsea en
    ese modo), así que se ancla al HEADER REAL — la primera fila, en cualquier
    hoja, que contenga TODAS las columnas requeridas (`Proyecto Base` +
    `Suma de Cantidad`). Robusto a filas/hojas previas. Si ninguna hoja tiene
    ese header ⇒ ReportingSourceError('table')."""
    import openpyxl
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — workbook inválido/corrupto
        raise ReportingSourceError("workbook", "no se pudo abrir el XLSX") from None
    try:
        for ws in workbook.worksheets:
            header_index: dict[str, int] | None = None
            rows: list[tuple] = []
            for row in ws.iter_rows(values_only=True):
                if header_index is None:
                    names = {_text(c): idx for idx, c in enumerate(row or ()) if _text(c)}
                    if all(req in names for req in required):
                        header_index = names
                    continue
                if row is not None and any(c is not None for c in row):
                    rows.append(row)
            if header_index is not None:
                return header_index, rows
        raise ReportingSourceError("table", f"no se encontró la tabla de Facturadas ({', '.join(required)})")
    finally:
        workbook.close()
