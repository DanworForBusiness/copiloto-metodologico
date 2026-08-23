"""
test_reporting_cartera_union_grain.py — CRITICAL STOP corrective B (owner
decision 2026-08-18, "OWNER DECISION — AUTHORIZE TWO MINIMAL CORRECTIVES").

Defecto estructural PROBADO (repro determinístico, sin datos reales) antes
de este corrective: `_mantenimiento_rows` construía su universo iterando
ÚNICAMENTE las filas mensuales de "Horas por proyectos" (SharePoint) — una
combinación (proyecto, año, mes) que existe en Time Logs pero NO en
SharePoint (p. ej. el registro manual de Agosto todavía no fue cargado)
ni siquiera producía una fila en Cartera, aunque "Horas Trabajadas" ya
había sido rediseñada para leer Time Logs directamente. Esto es una fuente
plausible (no confirmada con datos productivos — pendiente de validación
owner) de la diferencia real reportada (Rentabilidad 2682.4 vs Cartera
2577.9 para Agosto 2026).

Fix: el universo de filas de Mantenimiento es ahora la UNIÓN de keys
(proyecto normalizado, año, mes) presentes en SharePoint O en Time Logs.
Una key Time-Logs-only resuelve Proyecto/Cliente/Client Partner/Estado
contra `project_dim` (`get_cartera_facts_with_project_dimension` — MISMO
snapshot de Proyectos/Cliente ya cargado por "Horas por proyectos, CERO
llamadas Graph nuevas), preservando la semántica de ausencia YA existente
para Vendidas/Certificadas (0.0, fail-soft, nunca inventado). Un proyecto
de Time Logs que no resuelve contra Proyectos en absoluto (unmatched)
nunca fabrica su dimensión — se reporta aparte, nunca se descarta en
silencio.
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


def _patch(monkeypatch, *, horas_por_proyectos, proyectos, clientes, csv_files, file_rows, subfolder_csv_files=None):
    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
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
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_text(rows)
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def test_time_logs_only_month_still_emits_a_cartera_row(monkeypatch):
    # A/B/D: SharePoint SOLO tiene Julio (Agosto todavía no fue cargado
    # manualmente); Time Logs tiene Julio Y Agosto. Agosto debe seguir
    # produciendo una fila — Vendidas/Certificadas preservan la semántica
    # de ausencia YA existente (0.0), Trabajadas es el valor REAL.
    _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio",
            "HsTrabajadas": "999", "HsVendidas": "100", "HsFacturadas": "90",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [
            {"Date": "2026-07-10", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "150"},
            {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682.4"},
        ]},
    )
    rows = ReportingService(_repo()).get_cartera_mantenimiento("tok", anios=["2026"])
    julio = next(r for r in rows if r["mes"] == "7")
    agosto = next((r for r in rows if r["mes"] == "8"), None)
    assert julio["trabajadas"] == 150.0 and julio["certificadas"] == 90.0
    assert agosto is not None, "Agosto debe existir como fila aunque SharePoint no lo tenga todavía"
    assert agosto["trabajadas"] == 2682.4
    # D: Vendidas/Certificadas preservan la semántica de ausencia (0.0,
    # NUNCA inventado) — nunca se copia un valor de otro mes/fuente.
    assert agosto["certificadas"] == 0.0
    assert agosto["vendidasMensuales"] == 0.0
    assert agosto["facturadas"] == 0.0
    # E: metadata resuelta contra el project_dim (Proyectos/Cliente YA
    # cargados) — nunca vacía cuando el proyecto SÍ resuelve.
    assert agosto["cliente"] == "Danone"
    assert agosto["status"] == "En curso"
    assert agosto["anio"] == "2026"
    assert agosto["fecha"] == "2026-08-01"


def test_existing_sharepoint_and_time_logs_month_produces_one_row_only(monkeypatch):
    # C: (proyecto, año, mes) presente en AMBAS fuentes -> UNA sola fila,
    # nunca duplicada.
    _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
            "HsTrabajadas": "999", "HsVendidas": "160", "HsFacturadas": "140",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [
            {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682.4"},
        ]},
    )
    rows = ReportingService(_repo()).get_cartera_mantenimiento("tok", anios=["2026"])
    agosto_rows = [r for r in rows if r["mes"] == "8"]
    assert len(agosto_rows) == 1
    assert agosto_rows[0]["trabajadas"] == 2682.4
    assert agosto_rows[0]["certificadas"] == 140.0  # viene de SharePoint, no de la rama union-only


def test_call_budget_unaffected_by_union_grain(monkeypatch):
    # E (call-budget side): el union grain NO agrega llamadas Graph nuevas
    # — Proyectos/Cliente se leen UNA sola vez, reutilizados para
    # project_dim (MISMO fetch, ver get_cartera_facts_with_project_dimension).
    log = []

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        log.append(url)
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return [{"id": "current-1", "name": "Time Logs Reports.csv"}]
        if "/columns" in url:
            return []
        if _HP in url:
            return [{"id": "p1", "fields": {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1",
                                              "A_x00f1_o": "2026", "Mes": "Julio",
                                              "HsTrabajadas": "999", "HsVendidas": "100", "HsFacturadas": "90"}}]
        if _PROY in url:
            return [{"id": "p1", "fields": {"id": "p1", "Title": "Soporte Danone",
                                              "TipodeProyecto": "Mantenimiento", "Status": "En curso"}}]
        if _CLI in url:
            return [{"id": "c1", "fields": {"Title": "Danone"}}]
        return []

    def fake_bytes(url, access_token=None, **kw):
        if "current-1" in url:
            return _csv_text([
                {"Date": "2026-07-10", "Client": "Danone", "Project": "Soporte Danone",
                 "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "150"},
                {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                 "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682.4"},
            ])
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)

    ReportingService(_repo()).get_cartera_mantenimiento("tok", anios=["2026"])
    proyectos_reads = [u for u in log if _PROY in u and "/columns" not in u]
    cliente_reads = [u for u in log if _CLI in u and "/columns" not in u]
    assert len(proyectos_reads) == 1, proyectos_reads
    assert len(cliente_reads) == 1, cliente_reads


def test_unmatched_time_logs_project_is_tracked_not_fabricated():
    # I: un proyecto de Time Logs cuyo título NO resuelve contra Proyectos
    # en absoluto (no existe en project_dim) — NUNCA fabrica Cliente/CP/
    # Estado, se reporta aparte (`unmatched`), nunca se pierde en silencio.
    service = ReportingService.__new__(ReportingService)  # sin repo — solo shaping puro
    facts = [{
        "tipoProyecto": "Mantenimiento", "cliente": "Danone", "clientPartner": "CP",
        "proyecto": "Soporte Danone", "idProyecto": "", "estado": "En curso",
        "anio": "2026", "mes": "Julio", "anioInicio": "2026",
        "hsTrabajadas": 999.0, "hsVendidas": 100.0, "hsCertificadas": 90.0, "facturadasProyecto": 0.0,
    }]
    trab_by_proyecto = {
        "SOPORTEDANONE": {"2026": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 150.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
        # Proyecto fantasma — no existe en Proyectos (project_dim no lo conoce).
        "PROYECTOFANTASMA": {"2026": [0.0] * 7 + [500.0] + [0.0] * 4},
    }
    project_dim = {
        "SOPORTEDANONE": {"proyecto": "Soporte Danone", "idProyecto": "", "tipoProyecto": "Mantenimiento",
                           "estado": "En curso", "anioInicio": "2026", "cliente": "Danone", "clientPartner": "CP"},
    }
    rows, unmatched = service._mantenimiento_rows(facts, trab_by_proyecto, project_dim)
    assert all(r["proyecto"] != "" for r in rows)  # ninguna fila con proyecto vacío/fabricado
    assert not any(r.get("cliente") and "PROYECTOFANTASMA" in r["proyecto"] for r in rows)
    assert len(unmatched) == 1
    assert unmatched[0]["proyectoKey"] == "PROYECTOFANTASMA"
    assert unmatched[0]["horas"] == 500.0
    assert unmatched[0]["mes"] == "8"
