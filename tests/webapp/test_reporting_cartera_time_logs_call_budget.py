"""
test_reporting_cartera_time_logs_call_budget.py — CALL BUDGET corrective
(owner decision 2026-08-18, "OWNER DECISION — REWORK C1, KEEP C2").

La primera versión de la reconciliación Cartera↔Time Logs (ver
test_reporting_cartera_rentabilidad_worked_hours_sources.py) fue
funcionalmente correcta pero mecánicamente cara: medía 4→12 llamadas Graph
(3x) para el caso por-defecto (Año=2026/Mes=Agosto), suficiente para
explicar una regresión de performance real (~4x reportada por el owner).
Root cause identificada con un diagnóstico read-only (call-by-call
inventory, no wall-clock — no hay acceso Graph real en este entorno):

  1) `get_time_logs_facts` releía Proyectos/Cliente/columnas — datos que
     `get_cartera_facts` YA había leído en el MISMO request, solo para
     enriquecer campos (cliente/estado/porcentajePart) que la
     reconciliación NUNCA usa (`_trab_by_proyecto_by_year` solo lee
     proyecto/año/mes/horas).
  2) Time Logs se pedía con la MISMA ventana especulativa de 2 años que
     SharePoint (año actual + anterior) aunque nadie hubiera pedido el año
     anterior todavía — "no aceptar una carga histórica que nadie pidió".

Este archivo fija el presupuesto de llamadas CORRECTO (no un número
arbitrario — CERO llamadas redundantes, CERO años no solicitados) usando
el repository REAL con `core.microsoft365` mockeado (mismo criterio que
test_reporting_batch_b.py), para que una futura regresión de este mismo
tipo falle acá, no se descubra en vivo.
"""
from __future__ import annotations

import csv
import io

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _COLAB, _USERINFO = "hp-list", "proy-list", "cli-list", "colab-list", "userinfo-list"
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


class _CallLog:
    def __init__(self):
        self.json = []
        self.paged = []
        self.bytes = []


def _patch(monkeypatch, *, horas_por_proyectos, proyectos, clientes, csv_files, subfolder_csv_files, file_rows):
    """`csv_files` = items de la RAÍZ de `Template Dashboard` (puede incluir
    una carpeta `Times previos`). `subfolder_csv_files` = dict folder_id ->
    items (SOLO se consulta si el código realmente baja a esa subcarpeta —
    ver test de FILE DISCOVERY abajo)."""
    log = _CallLog()

    def fake_json(url, access_token=None, **kw):
        log.json.append(url)
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        log.paged.append(url)
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return csv_files
        for folder_id, items in (subfolder_csv_files or {}).items():
            if f"/sites/{_OPS_SITE}/drive/items/{folder_id}/children" in url:
                return items
        if "/columns" in url:
            return []
        if _HP in url:
            return _items(horas_por_proyectos)
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        if _COLAB in url or _USERINFO in url:
            return []
        return []

    def fake_bytes(url, access_token=None, **kw):
        log.bytes.append(url)
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_text(rows)
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return log


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


class _FixedDatetime2026:
    @staticmethod
    def now():
        import datetime as _dt
        return _dt.datetime(2026, 8, 15)


def test_cartera_mantenimiento_default_downloads_only_current_year_csv(monkeypatch):
    monkeypatch.setattr(repo_mod, "datetime", _FixedDatetime2026)
    log = _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
            "HsTrabajadas": "999", "HsVendidas": "160", "HsFacturadas": "140",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[
            {"id": "current-1", "name": "Time Logs Reports.csv"},
            {"id": "folder-previos", "name": "Times previos", "folder": {"childCount": 2}},
        ],
        subfolder_csv_files={"folder-previos": [
            {"id": "hist-2025", "name": "Time Logs Reports 2025.csv"},
            {"id": "hist-2024", "name": "Time Logs Reports 2024.csv"},
        ]},
        file_rows={
            "current-1": [{"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                           "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682"}],
            "hist-2025": [{"Date": "2025-07-10", "Client": "Danone", "Project": "Soporte Danone",
                           "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "80"}],
        },
    )
    rows = ReportingService(_repo()).get_cartera_mantenimiento("tok")
    agosto = next(r for r in rows if r["mes"] == "8")
    assert agosto["trabajadas"] == 2682.0
    # NO se descarga NINGÚN archivo histórico — solo el actual (2026).
    downloaded = [u for u in log.bytes if "/content" in u]
    assert len(downloaded) == 1
    assert "current-1" in downloaded[0]
    # FILE DISCOVERY OPTIMIZADO: como el archivo actual alcanza para
    # resolver el año pedido, NUNCA se lista "Times previos/" — 0 llamadas
    # a esa subcarpeta.
    subfolder_listed = [u for u in log.paged if "folder-previos" in u]
    assert subfolder_listed == []


def test_cartera_mantenimiento_reads_proyectos_cliente_columns_exactly_once(monkeypatch):
    monkeypatch.setattr(repo_mod, "datetime", _FixedDatetime2026)
    log = _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
            "HsTrabajadas": "999", "HsVendidas": "160", "HsFacturadas": "140",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        subfolder_csv_files={},
        file_rows={"current-1": [{"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                                    "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682"}]},
    )
    ReportingService(_repo()).get_cartera_mantenimiento("tok")
    # CALL BUDGET (owner 2026-08-18): Proyectos/Cliente/columnas — MÁXIMO
    # 1 lectura cada uno en todo el request (la duplicación era la causa
    # raíz de la regresión ~4x — `get_time_logs_worked_hours` NUNCA debe
    # releerlos, a diferencia de `get_time_logs_facts`, que Batch B sigue
    # usando completo por una razón distinta).
    proyectos_reads = [u for u in log.paged if _PROY in u and "/columns" not in u]
    cliente_reads = [u for u in log.paged if _CLI in u and "/columns" not in u]
    columns_reads = [u for u in log.paged if "/columns" in u]
    assert len(proyectos_reads) == 1, proyectos_reads
    assert len(cliente_reads) == 1, cliente_reads
    assert len(columns_reads) <= 1, columns_reads


def test_cartera_mantenimiento_cold_year_downloads_only_that_year(monkeypatch):
    log = _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2024", "Mes": "Marzo",
            "HsTrabajadas": "999", "HsVendidas": "90", "HsFacturadas": "80",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[
            {"id": "current-1", "name": "Time Logs Reports.csv"},
            {"id": "folder-previos", "name": "Times previos", "folder": {"childCount": 2}},
        ],
        subfolder_csv_files={"folder-previos": [
            {"id": "hist-2025", "name": "Time Logs Reports 2025.csv"},
            {"id": "hist-2024", "name": "Time Logs Reports 2024.csv"},
        ]},
        file_rows={
            "hist-2024": [{"Date": "2024-03-15", "Client": "Danone", "Project": "Soporte Danone",
                           "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "80"}],
        },
    )
    rows = ReportingService(_repo()).get_cartera_mantenimiento("tok", anios=["2024"])
    marzo = next(r for r in rows if r["mes"] == "3")
    assert marzo["trabajadas"] == 80.0
    downloaded = [u for u in log.bytes if "/content" in u]
    assert len(downloaded) == 1
    assert "hist-2024" in downloaded[0]
