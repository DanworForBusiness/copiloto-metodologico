"""
timenotes_service.py — Cumplimiento de Carga de Horas (Operaciones → 01. Asignaciones → Carga de Horas, MVP).

Servicio PURO de cálculo, INDEPENDIENTE de la UI y del envío de correos. Reúne tres lecturas de solo lectura (todas con el token DELEGADO base
`Sites.Read.All` que ya usa Staffing/Reporting):
  1. CSV `Time Logs Reports.csv` del drive del sitio Gestión (MISMO mecanismo Graph que Ausencias: `root:/{path}:/content`, `graph_get_bytes`).
  2. Padrón de colaboradores con email, vía `ReportingRepository.get_colaboradores_email_map` (Email→Consultor, el MISMO join del PBIP
     `Time Logs Reports.Email → Colaboradores`). El universo es DINÁMICO: nunca se fija la cantidad de colaboradores.
  3. Excepciones (Ausencias) — BEST-EFFORT e INFORMATIVO. Ver `_attach_exceptions`: NO se descuentan horas automáticamente (ninguna excepción
     de Ausencias se traduce de forma INEQUÍVOCA a horas exigibles: la tabla no tiene campo de horas y un rango de fechas no define horas sin una
     política de negocio). Se presentan como «pendiente de revisión» y marcan al colaborador como NO auto-notificable.

Regla de negocio ratificada por el owner: TODOS los colaboradores del padrón deben registrar `TIMENOTES_WEEKLY_HOURS` (40) por semana, salvo
excepciones. Semana = lunes a viernes; la anterior por defecto.

FAIL-SAFE (requisito del owner): un error de ORIGEN (descarga/parse) NUNCA se interpreta como «0 horas registradas». Se distingue:
  * `ok` — cálculo válido.
  * `csv_incompleto` — faltan columnas requeridas (email/duración/fecha) → la UI bloquea el envío.
  * `error_lectura` — falla de descarga/Graph → la UI bloquea el envío.
  * `padron_no_disponible` — no se pudo resolver el padrón.
Los fetchers son inyectables para probar con datos controlados (sin Graph real).
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from core import microsoft365

_logger = logging.getLogger("danwor.operations.timenotes")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

STATUS_OK = "ok"
STATUS_CSV_INCOMPLETE = "csv_incompleto"
STATUS_READ_ERROR = "error_lectura"
STATUS_PADRON_UNAVAILABLE = "padron_no_disponible"

# ── Referencias FIJAS del contrato de origen (decisión del owner: SIN variables de entorno nuevas para esta funcionalidad) ──
# El sitio/biblioteca es el MISMO que Ausencias (sitio Operaciones): se resuelve en `resolve_site_id` reutilizando
# `staffing_repository.ABSENCE_SITE_PATH`, sin Site ID hardcodeado ni config duplicada. La ruta del CSV es relativa a la raíz del drive por
# defecto de ese sitio (igual que `ABSENCE_XLSX_PATH`). Columnas y horas quedan fijas por el contrato acordado.
CSV_PATH = "General/Template Dashboard/Time Logs Reports.csv"
EMAIL_COLUMN = "Email"
DURATION_COLUMN = "Duration Decimal"
DATE_COLUMN = "Date"
WEEKLY_HOURS = 40.0

# Personas que NO forman parte del grupo «colaboradores» de Carga de Horas (equipos administrativos, ejecutivos o comerciales que no cargan
# horas directamente). Decisión del owner 2026-11-11: se excluyen de TODO listado (padrón, «Registros sin colaborador», Excepciones y envío)
# antes de cualquier cálculo. Mantenimiento SOLO por código (sin config ni UI). Comparación en minúsculas.
NON_COLLABORATOR_EMAILS = frozenset({
    "administracion@danwor.com",
    "adicriscenzo@danwor.com",
    "asosa@danwor.com",
    "bjawor@danwor.com",
    "cbustos@danwor.com",
    "dmitilmon@danwor.com",
    "gjordan@danwor.com",
    "jmelgarejo@danwor.com",
    "rdiaz@danwor.com",
    "sapienza@danwor.com",
    "svines@danwor.com",
})


def is_non_collaborator(email) -> bool:
    return str(email or "").strip().lower() in NON_COLLABORATOR_EMAILS


# Estado de ASIGNACIÓN (P7, decisión del owner 2026-11-11) — NO es condición laboral. Valores = Status crudo de Asignaciones de Staffing
# (passthrough, mismos conocidos que el filtro de Asignaciones; blank → «Sin estado»). Un colaborador coincide con un estado si tiene AL MENOS
# una asignación de ese estado que se SOLAPA con la semana consultada (FechaInicio ≤ fin y FechaFin vacía o ≥ inicio; fechas vacías = abiertas).
# Sin ninguna asignación aplicable → «Sin estado». Identidad: id del ítem de Colaboradores (exacta, nunca por nombre).
ESTADO_SIN = "Sin estado"
ESTADOS_CONOCIDOS = ("Activo", "Tentativo", "Inactivo", ESTADO_SIN)
ESTADOS_DEFAULT = ("Activo",)
_ESTADO_INACTIVO = "Inactivo"


# Disponibilidad de asignación (Colaboradores, MISMO campo y semántica que Operaciones → Asignaciones; vacío → «Sin dato»). Criterio de
# Operaciones (2026-11-12): SOLO Full Time tiene obligación semanal definida (40 h). Part Time: mínimo no definido. Sin dato: no se determina.
# En esos dos casos NO se declara incumplimiento ni se notifica. Valor VIGENTE de la lista (no hay historial por semana).
DISPONIBILIDAD_FT = "Full Time"
DISPONIBILIDAD_SIN_DATO = "Sin dato"
DISPONIBILIDAD_DEFAULT = DISPONIBILIDAD_FT


# Feriados nacionales — contrato REAL del archivo de Operaciones (2026-11-12): columnas Fecha / Motivo / Tipo (sin «Aplica»). Tipo ∈ Inamovible /
# No laborable / Turístico. Hoja: «Feriados» si existe; si no, la ÚNICA hoja cuyo encabezado trae las tres columnas (mismo criterio que Ausencias,
# sin exigir un nombre). Full Time: exigibles = max(0, base × (5 − feriados) / 5), contando fechas de los TIPOS elegidos, lunes–viernes de la
# semana, sin duplicados (una fecha con dos tipos descuenta una vez). Regla operativa (notificaciones): 40 h + solo Inamovible; cualquier otra
# selección es simulación. Calendario no validable → cálculo provisional (nunca se asumen cero feriados) y no se notifica.
HOLIDAYS_PATH = "General/Template Dashboard/Feriados Nacionales.xlsx"
HOLIDAYS_SHEET = "Feriados"
HOLIDAYS_HEADERS = ("Fecha", "Motivo", "Tipo")
HOLIDAY_TYPES = ("Inamovible", "No laborable", "Turistico")
HOLIDAY_TYPES_DEFAULT = ("Inamovible",)
HOL_OK = "ok"
HOL_AUSENTE = "ausente"
HOL_ESTRUCTURA = "estructura_invalida"
HOL_FECHA = "fecha_invalida"
HOL_SIN_COBERTURA = "sin_cobertura"
HOL_LECTURA = "error_lectura"


def _fold(value) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(value if value is not None else "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.strip().lower().split())


_TIPO_BY_FOLD = {_fold(t): t for t in HOLIDAY_TYPES}          # «Turístico» / «turistico» / «NO LABORABLE» → valor canónico


def holiday_types_selection(values) -> tuple:
    """Tipos elegidos, canónicos y en el orden del contrato (desconocidos se descartan)."""
    sel = {_TIPO_BY_FOLD.get(_fold(v)) for v in (values or [])}
    return tuple(t for t in HOLIDAY_TYPES if t in sel)


def parse_holidays(data: bytes, inicio: date, fin: date) -> dict:
    """Calendario → {estado, feriados[{fecha, descripcion, tipos[]}] de la semana (L–V, una entrada por fecha), detalle}. Puro (bytes en memoria,
    openpyxl read-only). Cobertura: cada año de los días lunes–viernes de la semana debe tener AL MENOS una fila con fecha válida."""
    import openpyxl
    from webapp.services.staffing_repository import _cell_to_iso_date, _header_names
    out = {"estado": HOL_OK, "feriados": [], "detalle": ""}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                              # noqa: BLE001 — archivo no legible como Excel
        return {**out, "estado": HOL_ESTRUCTURA, "detalle": "El archivo no es un Excel válido."}
    if HOLIDAYS_SHEET in wb.sheetnames:
        sheet = HOLIDAYS_SHEET
    else:
        candidates = [n for n in wb.sheetnames if set(HOLIDAYS_HEADERS) <= _header_names(wb[n])]
        if len(candidates) != 1:
            why = "ninguna hoja tiene" if not candidates else "más de una hoja tiene"
            return {**out, "estado": HOL_ESTRUCTURA, "detalle": f"No se pudo identificar la hoja de feriados: {why} las columnas {', '.join(HOLIDAYS_HEADERS)}."}
        sheet = candidates[0]
    rows = wb[sheet].iter_rows(values_only=True)
    header = next(rows, None) or ()
    idx = {}
    for i, cell in enumerate(header):
        name = str(cell).strip() if cell is not None else ""
        if name and name not in idx:
            idx[name] = i
    missing = [h for h in HOLIDAYS_HEADERS if h not in idx]
    if missing:
        return {**out, "estado": HOL_ESTRUCTURA, "detalle": f"Hoja «{sheet}»: faltan columnas " + ", ".join(missing) + "."}
    week_days = {inicio + timedelta(days=i) for i in range((fin - inicio).days + 1) if (inicio + timedelta(days=i)).weekday() < 5}
    years_seen, found = set(), {}
    for n, row in enumerate(rows, start=2):
        if row is None or not any(c is not None and str(c).strip() for c in row):
            continue
        cell = lambda h: row[idx[h]] if idx[h] < len(row) else None   # noqa: E731
        iso = _cell_to_iso_date(cell("Fecha"))
        if not iso:
            return {**out, "estado": HOL_FECHA, "detalle": f"Hoja «{sheet}», fila {n}: fecha inválida."}
        tipo = _TIPO_BY_FOLD.get(_fold(cell("Tipo")))
        if not tipo:
            return {**out, "estado": HOL_ESTRUCTURA, "detalle": f"Hoja «{sheet}», fila {n}: «Tipo» debe ser Inamovible, No laborable o Turístico."}
        d = date.fromisoformat(iso)
        years_seen.add(d.year)
        if d in week_days:
            e = found.setdefault(d, {"descripcion": str(cell("Motivo") or "").strip(), "tipos": []})
            if tipo not in e["tipos"]:
                e["tipos"].append(tipo)
    missing_years = sorted({d.year for d in week_days} - years_seen)
    if missing_years:
        return {**out, "estado": HOL_SIN_COBERTURA, "detalle": "El calendario no tiene fechas cargadas para " + ", ".join(map(str, missing_years)) + "."}
    out["feriados"] = [{"fecha": d.isoformat(), "descripcion": found[d]["descripcion"], "tipos": found[d]["tipos"]} for d in sorted(found)]
    out["hoja"] = sheet
    return out


def load_holidays(token: str, inicio: date, fin: date, *, download=None) -> dict:
    """Descarga el calendario con la MISMA descarga Graph que el CSV y lo valida. 404 → ausente; otro error → error de lectura."""
    try:
        data = (download or download_csv_bytes)(token, path=HOLIDAYS_PATH)
    except HTTPError as exc:
        if exc.code == 404:
            return {"estado": HOL_AUSENTE, "feriados": [], "detalle": f"No se encontró «{HOLIDAYS_PATH}»."}
        return {"estado": HOL_LECTURA, "feriados": [], "detalle": f"No se pudo leer el calendario (HTTP {exc.code})."}
    except (URLError, TimeoutError, OSError, ValueError):
        return {"estado": HOL_LECTURA, "feriados": [], "detalle": "No se pudo leer el calendario de feriados."}
    return parse_holidays(data, inicio, fin)


def _load_availability(token, *, cache_scope=None):
    """id ítem Colaboradores → disponibilidad. None = no disponible (fail-safe: nadie queda con obligación)."""
    try:
        from webapp.services.staffing_repository import StaffingRepository
        return StaffingRepository().get_consultant_availability(token, cache_scope=cache_scope)
    except Exception:                               # noqa: BLE001 — se informa como disponibilidad no determinable
        return None


def estados_requieren_inactivos(estados) -> bool:
    """Inactivo se lee aparte (historial, igual que Asignaciones). También hace falta para «Sin estado»: sin el historial, quien solo tiene
    asignaciones Inactivas se clasificaría mal como «Sin estado»."""
    sel = set(estados or ())
    return _ESTADO_INACTIVO in sel or ESTADO_SIN in sel


def _applies_to_week(start, end, inicio: date, fin: date) -> bool:
    return (start is None or start <= fin) and (end is None or end >= inicio)


def estados_por_consultor(rows, inicio: date, fin: date) -> dict[str, set[str]]:
    """consultant_id → estados de sus asignaciones aplicables a la semana. Pura (filas con consultant_id/status/start_date/end_date)."""
    out: dict[str, set[str]] = {}
    for r in rows or []:
        cid = str(getattr(r, "consultant_id", "") or "")
        if not cid or not _applies_to_week(getattr(r, "start_date", None), getattr(r, "end_date", None), inicio, fin):
            continue
        out.setdefault(cid, set()).add((getattr(r, "status", "") or "").strip() or ESTADO_SIN)
    return out


def _load_assignments(token, *, include_inactive: bool, cache_scope=None):
    """Asignaciones de Staffing por el MISMO servicio que Asignaciones (operational = sin Inactivo; + historial Inactivo si se pide). None = no
    disponible (el llamador lo informa; nunca se asume un estado)."""
    try:
        from webapp.services.staffing_service import StaffingService
        service = StaffingService()
        rows = list(service.get_overview(token, cache_scope=cache_scope))
        if include_inactive:
            rows += list(service.get_overview(token, assignment_scope="historical_inactive", cache_scope=cache_scope))
        return rows
    except Exception:                               # noqa: BLE001 — se informa como estados no disponibles
        return None

# Estados de Ausencias que cuentan como excepción vigente (resto se ignora). Confirmado contra `_ABSENCE_REQUIRED_HEADERS` de
# staffing_repository (columna `Estado`); conservador: solo aprobadas/confirmadas.
_ABSENCE_ACTIVE_STATES = {"aprobada", "aprobado", "confirmada", "confirmado", "activa", "activo"}


# ───────────────────────── semana ─────────────────────────

def week_range(reference: date) -> tuple[date, date]:
    """Lunes–viernes de la semana que contiene `reference`."""
    monday = reference - timedelta(days=reference.weekday())
    return monday, monday + timedelta(days=4)


def previous_week(today: date | None = None) -> tuple[date, date]:
    """Semana anterior completa (lunes–viernes) respecto de `today` (default: hoy)."""
    today = today or date.today()
    monday_this = today - timedelta(days=today.weekday())
    prev_monday = monday_this - timedelta(days=7)
    return prev_monday, prev_monday + timedelta(days=4)


def _week_label(inicio: date, fin: date) -> str:
    return f"{inicio.strftime('%d/%m/%Y')} al {fin.strftime('%d/%m/%Y')}"


# ───────────────────────── lecturas SharePoint (token delegado) ─────────────────────────

def _content_url(site_id: str, path: str) -> str:
    return f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{quote(path)}:/content"


def _item_url(site_id: str, path: str) -> str:
    return f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{quote(path)}"


def resolve_site_id(token: str, *, site_path: str | None = None, fetch=microsoft365.graph_get_json) -> str:
    """Resuelve el `site_id` del sitio donde vive la carpeta `General/Template Dashboard`, REUTILIZANDO el contrato de ubicación de Ausencias
    (`staffing_repository.ABSENCE_SITE_PATH` = sitio **Operaciones**) — el MISMO sitio/carpeta donde está `Solicitudes.xlsx`, resuelto con la MISMA
    llamada Graph que usa Ausencias. Sin Site ID hardcodeado ni config duplicada. Propaga los errores nativos de urllib (el caller los clasifica)."""
    if site_path is None:
        from webapp.services import staffing_repository          # import diferido: evita ciclo y reusa el contrato existente
        site_path = staffing_repository.ABSENCE_SITE_PATH
    data = fetch(f"{GRAPH_BASE}/sites/{site_path}", token)
    return str((data or {}).get("id") or "")


def download_csv_bytes(token: str, *, site_id: str | None = None, path: str | None = None,
                       fetch=microsoft365.graph_get_bytes) -> bytes:
    """Descarga el CSV de Time Notes (bytes crudos) del sitio Operaciones (mismo que Ausencias). Propaga HTTPError/URLError/OSError al caller."""
    site_id = site_id or resolve_site_id(token)
    return fetch(_content_url(site_id, path or CSV_PATH), token)


def _folder_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _exc_code(exc) -> str:
    return f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__


def diagnose_source(token: str, *, site_id: str | None = None, folder: str | None = None,
                    fetch=microsoft365.graph_get_json) -> dict:
    """Diagnóstico READ-ONLY del origen del CSV (se usa SOLO cuando la descarga dio 404). Con el token DELEGADO del usuario: nombre del drive
    (biblioteca del sitio Gestión, para verificar «Documentos compartidos») + listado de LA carpeta esperada (`General/Template Dashboard`).
    Enumera UNA sola carpeta conocida — NUNCA una búsqueda indiscriminada por nombre en SharePoint. Nunca expone el token. Best-effort."""
    site_id = site_id or resolve_site_id(token)
    folder = CSV_PATH.rsplit("/", 1)[0] if folder is None else folder
    diag = {"site_id_configurado": bool(site_id), "carpeta": folder, "drive": None, "archivos": None, "detalle": ""}
    try:
        drv = fetch(f"{GRAPH_BASE}/sites/{site_id}/drive?$select=id,name", token)
        if isinstance(drv, dict):
            diag["drive"] = {"name": drv.get("name"), "id": drv.get("id")}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        diag["detalle"] = f"drive: {_exc_code(exc)}"
    try:
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{quote(folder)}:/children?$select=name,file,folder&$top=200"
        data = fetch(url, token)
        diag["archivos"] = [{"name": it.get("name"), "es_carpeta": bool(it.get("folder"))} for it in (data.get("value") or [])]
    except HTTPError as exc:
        diag["carpeta_http"] = exc.code
        diag["detalle"] = (diag["detalle"] + " · " if diag["detalle"] else "") + f"carpeta: HTTP {exc.code}"
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        diag["detalle"] = (diag["detalle"] + " · " if diag["detalle"] else "") + f"carpeta: {type(exc).__name__}"
    return diag


def csv_metadata(token: str, *, site_id: str | None = None, path: str | None = None, fetch=microsoft365.graph_get_json) -> dict:
    """{last_modified, web_url} del driveItem del CSV (MISMA lectura que la vigencia). `web_url` SOLO si Graph lo devuelve (nunca se arma)."""
    site_id = site_id or resolve_site_id(token)
    try:
        meta = fetch(_item_url(site_id, path or CSV_PATH), token)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return {}
    meta = meta if isinstance(meta, dict) else {}
    url = str(meta.get("webUrl") or "")
    return {"last_modified": meta.get("lastModifiedDateTime"), "web_url": url if url.startswith("https://") else None}


def csv_last_modified(token: str, *, site_id: str | None = None, path: str | None = None,
                      fetch=microsoft365.graph_get_json) -> str | None:
    """Vigencia del CSV (`lastModifiedDateTime` del driveItem) cuando se puede verificar; `None` si falla (nunca rompe el cálculo)."""
    site_id = site_id or resolve_site_id(token)
    try:
        meta = fetch(_item_url(site_id, path or CSV_PATH), token)
        value = meta.get("lastModifiedDateTime") if isinstance(meta, dict) else None
        return str(value) if value else None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


# ───────────────────────── parseo ─────────────────────────

def _parse_hours(raw) -> float | None:
    """`Duration Decimal` → float. Tolera coma decimal (es-AR) y separador de miles. `None` si no es un número."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace(" ", "")
    if "," in s and "." in s:                       # 1.234,5 → miles '.' + decimal ','
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                                   # 1,5 → decimal ','
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(raw) -> date | None:
    """Fecha de la fila. ISO (`YYYY-MM-DD`, con o sin hora) es inequívoco; para `D/M/Y` se asume día-primero (es-AR). `None` si no parsea."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(csv_bytes: bytes) -> tuple[list[dict], list[str]]:
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    # Autodetección de delimitador (coma / punto y coma) sobre la primera línea.
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    rows = []
    for row in reader:
        rows.append({(k.strip() if k else k): v for k, v in row.items()})
    return rows, headers


# ───────────────────────── cálculo ─────────────────────────

@dataclass
class Colaborador:
    email: str
    nombre: str
    horas_base: float
    horas_exigibles: float
    horas_registradas: float
    faltantes: float
    cumple: bool
    sin_registros: bool
    excepciones: list = field(default_factory=list)
    revisar_excepcion: bool = False
    estados: list = field(default_factory=list)
    disponibilidad: str = DISPONIBILIDAD_SIN_DATO
    obligacion: bool = True               # True solo con obligación semanal definida (Full Time)
    calculo_verificado: bool = True       # False si el calendario de feriados no se pudo validar: cálculo provisional, no notificable

    @property
    def notificable(self) -> bool:
        """Elegible para notificación automática en lote: tiene faltantes y NO tiene una excepción pendiente de revisión."""
        return self.obligacion and self.calculo_verificado and self.faltantes > 0 and not self.revisar_excepcion

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("email", "nombre", "horas_base", "horas_exigibles", "horas_registradas",
                                            "faltantes", "cumple", "sin_registros", "excepciones", "revisar_excepcion", "estados",
                                            "disponibilidad", "obligacion", "calculo_verificado")}
        d["notificable"] = self.notificable
        return d


def _resolve_padron(token, email_map_fn) -> tuple[dict[str, str] | None, dict[str, str]]:
    """(email→nombre, email→id del ítem de Colaboradores). Con `email_map_fn` inyectado (tests) no hay ids."""
    if email_map_fn is not None:
        try:
            return email_map_fn(token), {}
        except Exception:                           # noqa: BLE001 — el caller marca padron_no_disponible
            return None, {}
    try:
        from webapp.services.reporting_repository import ReportingRepository
        return ReportingRepository().get_colaboradores_padron(token)
    except Exception:                               # noqa: BLE001
        return None, {}


def build_compliance(*, token: str, today: date | None = None, week: tuple[date, date] | None = None,
                     weekly_hours: float | None = None,
                     download=download_csv_bytes, meta=csv_metadata, diagnose=diagnose_source,
                     email_map_fn=None, absences_fn=None, assignments_fn=None, include_inactive: bool = False,
                     cache_scope=None, availability_fn=None, holidays_fn=None, holiday_types=None) -> dict:
    """Contrato de cálculo. Devuelve un dict serializable para la vista. NUNCA lanza por errores esperados (los informa como `status`)."""
    inicio, fin = week if week is not None else previous_week(today)
    base = float(weekly_hours if weekly_hours is not None else WEEKLY_HOURS)
    out = {"status": STATUS_OK, "semana": {"inicio": inicio.isoformat(), "fin": fin.isoformat(), "label": _week_label(inicio, fin)},
           "horas_base": base, "csv_actualizado": None, "colaboradores": [], "no_asociables": [],
           "resumen": {}, "detalle": ""}

    padron, padron_ids = _resolve_padron(token, email_map_fn)       # email(lower) → nombre / → id ítem Colaboradores
    if padron:
        padron = {e: n for e, n in padron.items() if not is_non_collaborator(e)}
    if not padron:
        out["status"] = STATUS_PADRON_UNAVAILABLE
        out["detalle"] = "No se pudo resolver el padrón de colaboradores."
        return out

    try:
        raw = download(token)
    except HTTPError as exc:
        out["status"] = STATUS_READ_ERROR
        out["http_status"] = exc.code                               # diagnóstico explícito (sin tokens ni secretos)
        if exc.code == 404:
            out["detalle"] = (f"El archivo no existe en la ruta configurada (HTTP 404): «{CSV_PATH}». "
                              "Verificá el nombre y la ubicación reales del CSV en SharePoint (sitio Gestión).")
            try:
                out["diagnostico"] = diagnose(token)                 # enumera SOLO la carpeta esperada (read-only), para ver el nombre real
            except Exception:                                        # noqa: BLE001 — el diagnóstico es accesorio, nunca rompe el error principal
                pass
        elif exc.code == 403:
            out["detalle"] = "Sin permiso para leer el archivo (HTTP 403): el token delegado del usuario no tiene acceso a ese documento o ubicación."
        elif exc.code == 401:
            out["detalle"] = "Autorización de Microsoft no válida o vencida (HTTP 401). Reingresá y reintentá."
        else:
            out["detalle"] = f"Graph respondió HTTP {exc.code} al descargar el CSV. No se interpreta como ausencia de registros."
        return out
    except (URLError, TimeoutError, OSError) as exc:
        out["status"] = STATUS_READ_ERROR
        out["detalle"] = f"Falla de red o transporte al descargar el CSV ({type(exc).__name__}). No se interpreta como ausencia de registros."
        return out

    rows, headers = parse_csv(raw)
    missing = [c for c in (EMAIL_COLUMN, DURATION_COLUMN, DATE_COLUMN) if c not in headers]
    if missing:
        out["status"] = STATUS_CSV_INCOMPLETE
        out["detalle"] = f"El CSV no tiene las columnas requeridas: {', '.join(missing)}."
        out["columnas_encontradas"] = headers
        return out

    m = meta(token)
    if isinstance(m, dict):
        out["csv_actualizado"], out["csv_web_url"] = m.get("last_modified"), m.get("web_url")
    else:                                                          # compatibilidad: metadato como texto (vigencia sola)
        out["csv_actualizado"], out["csv_web_url"] = m, None

    # Agregación por email dentro de la semana. Se cuentan filas con fecha ILEGIBLE o duración ilegible como señal de calidad (no se inventan).
    horas_por_email: dict[str, float] = {}
    filas_fecha_ilegible = 0
    filas_duracion_ilegible = 0
    filas_en_semana = 0
    for r in rows:
        d = _parse_date(r.get(DATE_COLUMN))
        if d is None:
            filas_fecha_ilegible += 1
            continue
        if not (inicio <= d <= fin):
            continue
        filas_en_semana += 1
        email = str(r.get(EMAIL_COLUMN) or "").strip().lower()
        if not email:
            continue
        h = _parse_hours(r.get(DURATION_COLUMN))
        if h is None:
            filas_duracion_ilegible += 1
            continue
        horas_por_email[email] = round(horas_por_email.get(email, 0.0) + h, 2)

    exceptions_by_email = _attach_exceptions(token, inicio, fin, padron, absences_fn)

    colaboradores: list[Colaborador] = []
    for email, nombre in sorted(padron.items(), key=lambda kv: (kv[1] or kv[0]).lower()):
        key = email.strip().lower()
        registradas = round(horas_por_email.get(key, 0.0), 2)
        sin_registros = key not in horas_por_email
        exc = exceptions_by_email.get(key, [])
        faltantes = round(max(0.0, base - registradas), 2)
        colaboradores.append(Colaborador(
            email=key, nombre=nombre or key, horas_base=base, horas_exigibles=base,
            horas_registradas=registradas, faltantes=faltantes, cumple=registradas >= base,
            sin_registros=sin_registros, excepciones=exc, revisar_excepcion=bool(exc),
        ))

    # Registros que no se pueden asociar a un colaborador del padrón (section 2).
    padron_keys = {e.strip().lower() for e in padron}
    no_asociables = [{"email": e, "horas": round(h, 2)} for e, h in sorted(horas_por_email.items())
                     if e not in padron_keys and not is_non_collaborator(e)]

    # Estado de asignación (P7): por id del ítem de Colaboradores. Sin asignaciones disponibles → `estados_disponibles=False` (el filtro y el
    # envío se bloquean; nunca se asume un estado). Con `email_map_fn` inyectado sin `assignments_fn` (tests), no se consulta Staffing.
    if assignments_fn is None and email_map_fn is not None:
        assignments_fn = lambda *a, **k: None                          # noqa: E731
    loader = assignments_fn or _load_assignments
    rows_asig = loader(token, include_inactive=include_inactive, cache_scope=cache_scope)
    out["estados_disponibles"] = rows_asig is not None
    out["incluye_inactivos"] = bool(include_inactive)
    if rows_asig is not None:
        por_consultor = estados_por_consultor(rows_asig, inicio, fin)
        ids = {str(k).strip().lower(): v for k, v in (padron_ids or {}).items()}
        for c in colaboradores:
            c.estados = sorted(por_consultor.get(ids.get(c.email, ""), set()) or {ESTADO_SIN})

    # Disponibilidad y obligación (por id del ítem de Colaboradores). Con `email_map_fn` inyectado SIN `availability_fn` (tests de cálculo
    # previos) se asume Full Time para todos; en producción la disponibilidad SIEMPRE se lee de Staffing y, si no se puede, nadie queda con
    # obligación (no se declaran incumplimientos ni se notifica).
    if availability_fn is None and email_map_fn is not None:
        disp_by_email = {c.email: DISPONIBILIDAD_FT for c in colaboradores}
        out["disponibilidad_disponible"] = True
    else:
        avail = (availability_fn or _load_availability)(token, cache_scope=cache_scope)
        out["disponibilidad_disponible"] = avail is not None
        ids = {str(k).strip().lower(): v for k, v in (padron_ids or {}).items()}
        disp_by_email = {c.email: ((avail or {}).get(ids.get(c.email, "")) or DISPONIBILIDAD_SIN_DATO) for c in colaboradores}
    # Feriados (Full Time): con `email_map_fn` inyectado SIN `holidays_fn` (tests de cálculo previos) → calendario válido sin feriados.
    if holidays_fn is None and email_map_fn is not None:
        hol = {"estado": HOL_OK, "feriados": [], "detalle": ""}
    else:
        hol = (holidays_fn or load_holidays)(token, inicio, fin)
    verificado = hol.get("estado") == HOL_OK
    tipos_sel = holiday_types_selection(holiday_types) if holiday_types is not None else HOLIDAY_TYPES_DEFAULT
    aplicables = [f for f in (hol.get("feriados") or []) if set(f.get("tipos") or HOLIDAY_TYPES_DEFAULT) & set(tipos_sel)]
    n_fer = len(aplicables) if verificado else 0                   # una entrada por fecha: dos tipos en la misma fecha descuentan una vez
    exigibles_ft = round(max(0.0, base * (5 - n_fer) / 5), 2)
    out["feriados"] = {"estado": hol.get("estado"), "verificado": verificado, "lista": aplicables if verificado else [], "cantidad": n_fer,
                       "tipos": list(tipos_sel), "semana_todos": hol.get("feriados") or [],
                       "descuento_h": round(base - exigibles_ft, 2), "detalle": hol.get("detalle") or "", "ruta": HOLIDAYS_PATH}
    for c in colaboradores:
        c.disponibilidad = disp_by_email.get(c.email) or DISPONIBILIDAD_SIN_DATO
        c.obligacion = c.disponibilidad == DISPONIBILIDAD_FT
        if not c.obligacion:                      # sin mínimo definido: no hay faltantes ni cumplimiento contra 40 h
            c.faltantes = 0.0
            c.cumple = False
        elif not verificado:                      # calendario no validado: cálculo PROVISIONAL contra la base SIN descontar feriados
            c.calculo_verificado = False          # (nunca se asumen cero feriados como definitivo); visible pero no notificable
        else:                                     # exigibles ajustadas por feriados; las registradas NO cambian
            c.horas_exigibles = exigibles_ft
            c.faltantes = round(max(0.0, exigibles_ft - c.horas_registradas), 2)
            c.cumple = c.horas_registradas >= exigibles_ft

    out["colaboradores"] = [c.as_dict() for c in colaboradores]
    out["no_asociables"] = no_asociables
    out["calidad"] = {"filas_total": len(rows), "filas_en_semana": filas_en_semana,
                      "filas_fecha_ilegible": filas_fecha_ilegible, "filas_duracion_ilegible": filas_duracion_ilegible}
    out["resumen"] = {
        "esperados": len(colaboradores),
        "cumplen": sum(1 for c in colaboradores if c.cumple),
        "con_faltantes": sum(1 for c in colaboradores if c.faltantes > 0),
        "sin_registros": sum(1 for c in colaboradores if c.sin_registros),
        "con_excepcion": sum(1 for c in colaboradores if c.revisar_excepcion),
        "notificables": sum(1 for c in colaboradores if c.notificable),
        "no_asociables": len(no_asociables),
    }
    return out


def _attach_exceptions(token, inicio: date, fin: date, padron: dict[str, str], absences_fn) -> dict[str, list]:
    """Excepciones (Ausencias) BEST-EFFORT, por email, SOLO informativas (no descuentan horas). Asocia una ausencia a un colaborador solo cuando
    el nombre normalizado coincide de forma INEQUÍVOCA con exactamente un email del padrón. Si Ausencias no está disponible, devuelve {} (no bloquea)."""
    try:
        if absences_fn is not None:
            absences = absences_fn(token)
        else:
            from webapp.services import staffing_repository
            absences = staffing_repository.StaffingRepository().get_absences(token)
    except Exception:                               # noqa: BLE001 — Ausencias es accesorio; su falla NUNCA rompe el cumplimiento
        return {}

    # nombre normalizado → email (solo nombres únicos: si un nombre mapea a >1 email, se descarta por ambiguo).
    name_to_email: dict[str, str] = {}
    ambiguous: set[str] = set()
    for email, nombre in padron.items():
        norm = _normalize_name(nombre)
        if not norm:
            continue
        if norm in name_to_email and name_to_email[norm] != email.strip().lower():
            ambiguous.add(norm)
        name_to_email[norm] = email.strip().lower()
    for norm in ambiguous:
        name_to_email.pop(norm, None)

    result: dict[str, list] = {}
    for a in absences or []:
        estado = str(_absence_get(a, "Estado", "estado") or "").strip().lower()
        if estado and estado not in _ABSENCE_ACTIVE_STATES:
            continue
        desde = _parse_date(_absence_get(a, "Desde", "desde", "start", "inicio"))
        hasta = _parse_date(_absence_get(a, "Hasta", "hasta", "end", "fin")) or desde
        if desde is None or hasta < inicio or desde > fin:
            continue
        nombre = f"{_absence_get(a, 'Nombre', 'nombre') or ''} {_absence_get(a, 'Apellido', 'apellido') or ''}".strip()
        norm = _normalize_name(nombre)
        email = name_to_email.get(norm)
        entry = {"motivo": str(_absence_get(a, "Motivo", "motivo") or "").strip() or "Sin motivo",
                 "desde": desde.isoformat(), "hasta": hasta.isoformat(), "estado": estado or "—",
                 "nombre": nombre, "asociada": bool(email), "pendiente_revision": True}
        if email:
            result.setdefault(email, []).append(entry)
    return result


def _absence_get(a, *keys):
    """Acceso tolerante a un registro de Ausencia, sea dict plano, dict con `fields`, o dataclass."""
    for k in keys:
        if isinstance(a, dict):
            if k in a:
                return a[k]
            f = a.get("fields")
            if isinstance(f, dict) and k in f:
                return f[k]
        elif hasattr(a, k):
            return getattr(a, k)
    return None


def _normalize_name(value) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())
