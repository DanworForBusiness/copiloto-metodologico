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

import io

import config
from core import microsoft365

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# XLSX de Facturadas — ubicación estable como module constants (mismo criterio
# que los ABSENCE_* de staffing_repository: ruta en el drive, no config nueva).
FACTURADAS_XLSX_PATH = "General/Reportes Horas por Proyectos/Horas Facturadas Proyectos-Período.xlsx"
FACTURADAS_TABLE_NAME = "Tabla24"

# Nombres de columna FUENTE (derivados del M/TMDL del PBIP). Únicos internal
# names de SharePoint en toda la app de Reporting — si el runtime de Graph
# entrega otro internal name para un lookup (p. ej. `ProyectoLookupId`), se
# ajusta ACÁ, nunca en el service/JS (ver gaps en real-data-lineage.md).
HP_PROYECTO_ID = "ProyectoId"       # Horas por proyectos → Proyectos.Id
HP_EMPRESA_ID = "EmpresaId"         # Horas por proyectos → Cliente.Id
HP_PROYECTO_TITLE = "Proyecto"      # título del proyecto (lookup expandido)
HP_ANIO = "Año"
HP_MES = "Mes"
HP_HS_TRABAJADAS = "HsTrabajadas"
HP_HS_VENDIDAS = "HsVendidas"
HP_HS_FACTURADAS = "HsFacturadas"   # M lo renombra a "Hs certificadas"

PROY_ID_DANWOR = "ID Proyecto Danwor"
PROY_TIPO = "TipodeProyecto"        # "Tiempo & Materiales" ⇒ Mantenimiento
PROY_FECHA_INICIO = "FechadeInicio"
PROY_ESTADO = "Estado"
PROY_EMPRESA_ID = "EmpresaId"

CLI_NOMBRE = "Cliente"
CLI_CLIENT_PARTNER = "Client Partner"

# XLSX Facturadas (Tabla24)
FAC_PROYECTO_BASE = "Proyecto Base"
FAC_CANTIDAD = "Suma de Cantidad"


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


def _list_items_url(site_id: str, list_id: str, select_fields: list[str] | None = None) -> str:
    """Items de una lista con `fields` expandido. Si se pasan `select_fields`,
    se limita el $select a esas columnas (nunca baja toda la lista). Mismo
    patrón que `staffing_repository._list_items_url`, replicado acá para no
    acoplar Reporting a un helper privado de Staffing."""
    url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items?$expand=fields"
    if select_fields:
        from urllib.parse import quote
        cols = ",".join(select_fields)
        url += f"($select={quote(cols)})"
    return url


def _norm_key(name) -> str:
    """Clave de proyecto normalizada (Upper + sin espacios) — reproduce la
    transformación del PBIP (`Text.Upper(Replace(' ',''))`) que une
    `Horas por proyectos.Proyecto_Key` con `Horas Facturadas.Key_proyect_desc`."""
    return "".join(str(name or "").split()).upper()


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
    ):
        self.site_id = site_id if site_id is not None else config.SHAREPOINT_GESTION_SITE_ID
        self.horas_proyectos_list_id = horas_proyectos_list_id or config.SHAREPOINT_HORAS_PROYECTOS_LIST_ID
        self.proyectos_list_id = proyectos_list_id or config.SHAREPOINT_PROYECTOS_LIST_ID
        self.cliente_list_id = cliente_list_id or config.SHAREPOINT_CLIENTE_LIST_ID

    def _require_site_id(self) -> str:
        if not self.site_id:
            raise ReportingConfigurationError("SHAREPOINT_GESTION_SITE_ID no configurado")
        return self.site_id

    # --- lecturas de lista (paginadas) --------------------------------------

    def _fields(self, site_id: str, list_id: str, stage: str, select: list[str] | None = None) -> list[dict]:
        items = _call_graph(stage, lambda: microsoft365.graph_get_paged_values(
            _list_items_url(site_id, list_id, select), access_token=self._token,
        ))
        return [it.get("fields") or {} for it in items]

    # --- XLSX Facturadas ----------------------------------------------------

    def _download_facturadas_workbook(self, site_id: str) -> bytes:
        return _call_graph("facturadas_download", lambda: microsoft365.graph_get_bytes(
            f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{_quote(FACTURADAS_XLSX_PATH)}:/content",
            self._token,
        ))

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

    # --- ensamble de hechos normalizados ------------------------------------

    def get_cartera_facts(self, access_token: str) -> list[dict]:
        """Hechos de Cartera normalizados a grano proyecto×mes, con la dimensión
        de proyecto/cliente ya unida y `facturadasProyecto` (SUM del XLSX por
        proyecto) adjunto. Nombres de negocio, NUNCA internal names SharePoint.
        El split Mantenimiento/Proyecto y las medidas derivadas viven en el
        service."""
        self._token = access_token
        site_id = self._require_site_id()

        proyectos = self._fields(site_id, self.proyectos_list_id, "proyectos")
        clientes = self._fields(site_id, self.cliente_list_id, "cliente")
        horas = self._fields(site_id, self.horas_proyectos_list_id, "horas_proyectos")
        facturadas_by_key = self._facturadas_by_project(site_id)

        proy_by_id = {_text(p.get("id") or p.get("Id")): p for p in proyectos}
        cli_by_id = {_text(c.get("id") or c.get("Id")): c for c in clientes}

        facts: list[dict] = []
        for h in horas:
            proy = proy_by_id.get(_text(h.get(HP_PROYECTO_ID)), {})
            cli = cli_by_id.get(_text(h.get(HP_EMPRESA_ID)), {})
            proyecto_title = _text(h.get(HP_PROYECTO_TITLE)) or _text(proy.get(PROY_ID_DANWOR))
            tipo_raw = _text(proy.get(PROY_TIPO))
            tipo = "Mantenimiento" if tipo_raw == "Tiempo & Materiales" else tipo_raw
            fecha_inicio = _text(proy.get(PROY_FECHA_INICIO))
            anio_inicio = fecha_inicio[:4] if fecha_inicio else ""
            facts.append({
                "cliente": _text(cli.get(CLI_NOMBRE)),
                "clientPartner": _text(cli.get(CLI_CLIENT_PARTNER)),
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
        return facts


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
