"""
test_reporting_rentabilidad_time_logs_duplicate_dimensions.py — DUPLICATE
DIMENSIONS corrective (owner decision 2026-08-19, "OWNER DECISION —
OPTIMIZE PROVEN REDUNDANCY FIRST").

Profiling real (post EXPLICIT SOURCE TOPOLOGY corrective) mostró
`rentabilidad.diag_time_logs_total`≈10.9s, de los cuales ~4.1s eran una
RELECTURA de Proyectos/Cliente/columnas (porcentaje_part) que
`get_cartera_facts` (usado por Rentabilidad para "Horas por proyectos", EN
EL MISMO REQUEST) ya había leído — `get_time_logs_facts` (el contrato
enriquecido de Time Logs, con join a Proyectos/Cliente para resolver
`cliente`/`estado`/`porcentajePart`) hacía esa relectura para campos que
`_trab_by_proyecto_by_year` NUNCA consume (solo lee proyecto/año/mes/
horas). Fix: `get_rentabilidad` pasó a usar `get_time_logs_worked_hours`
(la proyección mínima, MISMO método ya usado por Cartera desde el CALL
BUDGET corrective anterior — ver test_reporting_cartera_time_logs_call_
budget.py), nunca un loader nuevo.

Este archivo fija DOS presupuestos/garantías para que esta regresión no
pueda reintroducirse en silencio:

  1) CALL BUDGET — Proyectos/Cliente/columnas se leen COMO MÁXIMO una vez
     en todo el request de `get_rentabilidad` (la lectura que ya hace
     `get_cartera_facts`); Time Logs NUNCA los relee.
  2) EQUIVALENCIA — `_trab_by_proyecto_by_year` (la única función que
     consume Time Logs en `get_rentabilidad`, fuente de `trabByYear`)
     produce EXACTAMENTE el mismo resultado si se la alimenta con
     `get_time_logs_facts` (contrato completo, el que usaba ANTES) o con
     `get_time_logs_worked_hours` (contrato mínimo, el que usa AHORA) para
     el mismo fixture — la migración de contrato es mecánica, cero cambio
     de semántica.
"""
from __future__ import annotations

import csv
import io

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService, _trab_by_proyecto_by_year

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


_HORAS_POR_PROYECTOS = [{
    "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
    "HsTrabajadas": "999", "HsVendidas": "160", "HsFacturadas": "140",
}]
_PROYECTOS = [{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}]
_CLIENTES = [{"id": "c1", "Title": "Danone"}]
_CSV_FILES = [{"id": "current-1", "name": "Time Logs Reports.csv"}]
_FILE_ROWS = {
    "current-1": [{"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                   "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "2682.4"}],
}


def test_rentabilidad_reads_proyectos_cliente_columns_exactly_once(monkeypatch):
    monkeypatch.setattr(repo_mod, "datetime", _FixedDatetime2026)
    log = _patch(
        monkeypatch,
        horas_por_proyectos=_HORAS_POR_PROYECTOS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        csv_files=_CSV_FILES, subfolder_csv_files={}, file_rows=_FILE_ROWS,
    )
    ReportingService(_repo()).get_rentabilidad("tok")
    # CALL BUDGET (owner 2026-08-19): Proyectos/Cliente — MÁXIMO 1 lectura
    # cada uno en todo el request ("Horas por proyectos" vía
    # get_cartera_facts). `/columns` de Cliente tiene UN consumidor
    # legítimo dentro de ese mismo fetch (`_resolve_client_partners`,
    # Client Partner) — el presupuesto correcto es <=1, NO 0 (ver MISMO
    # criterio en test_reporting_cartera_time_logs_call_budget.py). Lo que
    # este test bloquea es la SEGUNDA lectura de `/columns` que
    # `get_time_logs_facts` agregaría (`_resolve_porcentaje_part_field`,
    # para `porcentajePart` — campo que `_trab_by_proyecto_by_year` nunca
    # consume) si Rentabilidad volviera a usar ese contrato enriquecido.
    proyectos_reads = [u for u in log.paged if _PROY in u and "/columns" not in u]
    cliente_reads = [u for u in log.paged if _CLI in u and "/columns" not in u]
    columns_reads = [u for u in log.paged if "/columns" in u]
    assert len(proyectos_reads) == 1, proyectos_reads
    assert len(cliente_reads) == 1, cliente_reads
    assert len(columns_reads) <= 1, columns_reads


def test_trab_by_proyecto_by_year_equivalent_between_facts_and_worked_hours_contracts(monkeypatch):
    """Mismo fixture, mismo `hot_years` — `_trab_by_proyecto_by_year`
    (única función que consume Time Logs para `trabByYear` en
    `get_rentabilidad`) debe producir el MISMO resultado exacto sea que se
    la alimente con el contrato ANTES (`get_time_logs_facts`, enriquecido)
    o el contrato AHORA (`get_time_logs_worked_hours`, mínimo) — prueba que
    el swap de loader es puramente mecánico, cero cambio de semántica."""
    monkeypatch.setattr(repo_mod, "datetime", _FixedDatetime2026)
    log = _patch(
        monkeypatch,
        horas_por_proyectos=_HORAS_POR_PROYECTOS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        csv_files=_CSV_FILES, subfolder_csv_files={}, file_rows=_FILE_ROWS,
    )
    repo = _repo()
    facts = repo.get_time_logs_facts("tok", hot_years=["2026"])
    worked_hours = repo.get_time_logs_worked_hours("tok", hot_years=["2026"])
    assert _trab_by_proyecto_by_year(facts) == _trab_by_proyecto_by_year(worked_hours)
    # sanity: el fixture realmente produjo horas no-triviales de ambos
    # lados (si esto estuviera vacío, la equivalencia sería trivial/vacía).
    assert _trab_by_proyecto_by_year(worked_hours)
    _ = log  # no assertions extra sobre call budget acá, ver test de arriba
