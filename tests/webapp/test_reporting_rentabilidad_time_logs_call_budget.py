"""
test_reporting_rentabilidad_time_logs_call_budget.py — CRITICAL STOP
corrective (owner decision 2026-08-18, "OWNER DECISION — AUTHORIZE TWO
MINIMAL CORRECTIVES").

Root cause PROBADA con un diagnóstico read-only (call-by-call inventory
contra 5 checkpoints de git, transporte mockeado — nunca wall-clock): el
commit `f7a9d9b` (recursión a `Times previos/`) cambió el contrato
IMPLÍCITO de `get_time_logs_facts(access_token)` sin `hot_years` — antes
de ese commit, "sin acotar" solo podía encontrar 1 archivo (el listado no
era recursivo); después, "sin acotar" pasó a significar SILENCIOSAMENTE
"todos los archivos/años conocidos" (6 CSV). `get_rentabilidad` nunca pasó
`hot_years` a esa llamada — heredó el cambio de contrato sin pedirlo,
explicando una regresión de performance real (~14s→~92s, owner live
measurement).

Este archivo fija el presupuesto de llamadas CORRECTO — Rentabilidad debe
declarar EXPLÍCITAMENTE su scope de Time Logs, MISMA ventana que ya usa
para "Horas por proyectos" (año actual + anterior por defecto, un año
frío puntual explícito) — para que esta regresión no pueda reintroducirse
en silencio.
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


class _FixedDatetime2026:
    @staticmethod
    def now():
        import datetime as _dt
        return _dt.datetime(2026, 8, 15)


def _patch(monkeypatch, *, horas_por_proyectos, proyectos, clientes, csv_files, subfolder_csv_files, file_rows):
    log_bytes = []
    log_paged = []

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        log_paged.append(url)
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
        log_bytes.append(url)
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_text(rows)
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return log_bytes, log_paged


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def _three_year_fixture(monkeypatch):
    return _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
            "HsTrabajadas": "999", "HsVendidas": "160", "HsFacturadas": "140",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[
            {"id": "current-1", "name": "Time Logs Reports.csv"},
            {"id": "folder-previos", "name": "Times previos", "folder": {"childCount": 4}},
        ],
        subfolder_csv_files={"folder-previos": [
            {"id": "hist-2025", "name": "Time Logs Reports 2025.csv"},
            {"id": "hist-2024", "name": "Time Logs Reports 2024.csv"},
            {"id": "hist-2023", "name": "Time Logs Reports 2023.csv"},
            {"id": "hist-2022", "name": "Time Logs Reports 2022.csv"},
        ]},
        file_rows={
            "current-1": [{"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                           "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682.4"}],
            "hist-2025": [{"Date": "2025-05-10", "Client": "Danone", "Project": "Soporte Danone",
                           "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "80"}],
            "hist-2024": [{"Date": "2024-05-10", "Client": "Danone", "Project": "Soporte Danone",
                           "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "80"}],
        },
    )


def test_rentabilidad_default_downloads_only_current_and_previous_year_csv(monkeypatch):
    monkeypatch.setattr(repo_mod, "datetime", _FixedDatetime2026)
    log_bytes, _log_paged = _three_year_fixture(monkeypatch)
    result = ReportingService(_repo()).get_rentabilidad("tok")
    proyecto = next(p for p in result["proyectos"] if p["proyecto"] == "Soporte Danone")
    assert proyecto["trabByYear"]["2026"][7] == 2682.4
    downloaded = [u for u in log_bytes if "/content" in u]
    # A: default request -> SOLO año actual + anterior (2026+2025), nunca
    # el historial completo (2024/2023/2022 NUNCA se descargan).
    assert len(downloaded) == 2
    assert any("current-1" in u for u in downloaded)
    assert any("hist-2025" in u for u in downloaded)
    assert not any("hist-2024" in u or "hist-2023" in u or "hist-2022" in u for u in downloaded)


def test_rentabilidad_explicit_cold_year_downloads_only_that_year(monkeypatch):
    log_bytes, _log_paged = _three_year_fixture(monkeypatch)
    result = ReportingService(_repo()).get_rentabilidad("tok", anios=["2024"])
    proyecto = next(p for p in result["proyectos"] if p["proyecto"] == "Soporte Danone")
    assert proyecto["trabByYear"]["2024"][4] == 80.0  # Mayo, índice 4
    downloaded = [u for u in log_bytes if "/content" in u]
    # B: año frío explícito -> SOLO ese archivo.
    assert len(downloaded) == 1
    assert "hist-2024" in downloaded[0]


def test_rentabilidad_anios_catalog_downloads_no_csv_content(monkeypatch):
    # D: el catálogo de años de Rentabilidad (`get_rentabilidad_anios`) es
    # sobre "Horas por proyectos" (SharePoint) — nunca toca Time Logs en
    # absoluto, cero descargas de CSV por construcción.
    log_bytes, _log_paged = _three_year_fixture(monkeypatch)
    ReportingService(_repo()).get_rentabilidad_anios("tok")
    downloaded = [u for u in log_bytes if "/content" in u]
    assert downloaded == []
