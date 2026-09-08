"""
test_reporting_hnp_task_semantics.py — owner ticket "URGENT REPORTING
CORRECTNESS — HNP TASK + SOLD HOURS YEAR ACCUMULATION" §1: "Horas No
Productivas" (proyecto placeholder) != "No asignadas a servicios" (una
TAREA puntual dentro de ese proyecto) — el proyecto agrupa varios conceptos
de negocio (No Asignación/Vacaciones/otros) y sumarlos todos mezcla
conceptos que la Dirección mide por separado.

Fixture ÚNICA reutilizada en las 3 capas de prueba (backend shape, Graph
end-to-end de Rentabilidad, y filtro real de reporting.js), EXACTA a la
pedida por el ticket: un consultor con 3 tareas bajo el mismo proyecto HNP
en un mismo mes — tarea 01 "No Asignación a Servicio en Cliente" = 8h,
tarea "02. Vacaciones" = 6h, tarea "03. Other" = 4h. Total HNP = 18h, pero
NINGUNA superficie debe reportar 18h para "No asignadas a servicios".
"""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
from pathlib import Path

import openpyxl
from openpyxl.worksheet.table import Table

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService

import pytest

_HNP_PROJECT = "Danwor - Horas No Productivas (#1)"
_TASK_01 = "01. No Asignación a Servicio en Cliente"
_TASK_VACACIONES = "02. Vacaciones"
_TASK_OTHER = "03. Other"


# --- A/B/C/D/F a nivel backend (shape puro, sin Graph) ----------------------

def _service():
    repo = ReportingRepository(site_id="s", horas_proyectos_list_id="h", proyectos_list_id="p", cliente_list_id="c")
    return ReportingService(repo)


def _facts_8_6_4():
    """MISMA fixture que pide el ticket: tarea 01 -> 8h, Vacaciones -> 6h,
    tarea 03 -> 4h, un solo consultor, un solo mes (2026-01)."""
    return [
        {"cliente": "Danwor", "proyecto": _HNP_PROJECT, "task": _TASK_01,
         "email": "a@danwor.com", "horas": 8.0, "mes": "2026-01", "anio": "2026"},
        {"cliente": "Danwor", "proyecto": _HNP_PROJECT, "task": _TASK_VACACIONES,
         "email": "a@danwor.com", "horas": 6.0, "mes": "2026-01", "anio": "2026"},
        {"cliente": "Danwor", "proyecto": _HNP_PROJECT, "task": _TASK_OTHER,
         "email": "a@danwor.com", "horas": 4.0, "mes": "2026-01", "anio": "2026"},
    ]


def test_shape_no_asignadas_default_task_is_8h_not_18h():
    # A + D: sin elegir Tarea, la UI usa el default (tarea 01) -- la fila
    # correspondiente suma 8h, JAMÁS 18h (suma de las 3 tareas del proyecto).
    rows = _service()._shape_horas_productivas_no_asignadas(_facts_8_6_4(), {"a@danwor.com": "Ana"})
    by_tarea = {r["tarea"]: r for r in rows}
    assert by_tarea[_TASK_01]["horasByYear"]["2026"][0] == 8.0


def test_shape_no_asignadas_selecting_vacaciones_is_6h():
    # B: seleccionar explícitamente Vacaciones -- 6h, nunca mezclado con
    # la tarea 01 ni con la tarea 03.
    rows = _service()._shape_horas_productivas_no_asignadas(_facts_8_6_4(), {"a@danwor.com": "Ana"})
    by_tarea = {r["tarea"]: r for r in rows}
    assert by_tarea[_TASK_VACACIONES]["horasByYear"]["2026"][0] == 6.0


def test_shape_no_asignadas_selecting_task_03_is_4h():
    # C: seleccionar explícitamente la tarea 03 -- 4h.
    rows = _service()._shape_horas_productivas_no_asignadas(_facts_8_6_4(), {"a@danwor.com": "Ana"})
    by_tarea = {r["tarea"]: r for r in rows}
    assert by_tarea[_TASK_OTHER]["horasByYear"]["2026"][0] == 4.0


def test_shape_no_asignadas_task_dropdown_source_is_hnp_tasks_only():
    # F: las únicas 3 "tareas" que el backend puede producir para este
    # proyecto son EXACTAMENTE las 3 observadas -- nunca una 4ta fabricada,
    # nunca "Todas" como fila de datos.
    rows = _service()._shape_horas_productivas_no_asignadas(_facts_8_6_4(), {"a@danwor.com": "Ana"})
    assert {r["tarea"] for r in rows} == {_TASK_01, _TASK_VACACIONES, _TASK_OTHER}


def test_shape_no_asignadas_absent_task_01_yields_no_row_for_it():
    # E: si la tarea 01 nunca aparece en los hechos (ausente del todo), no
    # existe fila para ella -- el default de la UI (que filtra por esa
    # etiqueta) debe rendir cero, JAMÁS caer a sumar Vacaciones/Other.
    facts = [f for f in _facts_8_6_4() if f["task"] != _TASK_01]
    rows = _service()._shape_horas_productivas_no_asignadas(facts, {"a@danwor.com": "Ana"})
    assert _TASK_01 not in {r["tarea"] for r in rows}


# --- G/H a nivel Rentabilidad (Graph mockeado, end-to-end) ------------------

_SITE, _HP, _PROY, _CLI = "site-1", "hp-list", "proy-list", "cli-list"
_OPS_SITE = "ops-site-1"
_CSV_HEADERS = ["Date", "From", "To", "Description", "Duration", "Duration Decimal",
                "Client", "Project", "Task", "User", "Email", "Billable Amount", "Billable", "Cost"]


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _empty_facturadas_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facturado"
    ws.append(["Proyecto Base", "Suma de Cantidad"])
    ws.add_table(Table(displayName="Tabla24", ref="A1:B1"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _patch_rentabilidad(monkeypatch, *, horas, proyectos, clientes, csv_files, file_rows):
    facturadas_bytes = _empty_facturadas_xlsx()

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return csv_files
        if "/columns" in url:
            return []
        if _HP in url:
            return _items(horas)
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        return []

    def fake_bytes(url, access_token, **kw):
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_bytes(rows)
        if "/drive/items/" in url:
            return _csv_bytes([])
        return facturadas_bytes

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _rentabilidad_service():
    repo = ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )
    return ReportingService(repo)


def test_rentabilidad_no_asignadas_a_servicios_is_8h_and_excludes_other_hnp_tasks(monkeypatch):
    # G + H: "No asignadas a servicios" en Detalle Mensual por concepto
    # SIEMPRE usa proyecto HNP + tarea 01 (sin dropdown) -- con la MISMA
    # fixture del ticket, debe reportar 8h, jamás 18h, y Vacaciones/Other
    # nunca deben contribuir a este concepto.
    _patch_rentabilidad(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero",
                "HsVendidas": "0", "HsFacturadas": "0"}],
        proyectos=[{"id": "p1", "Title": "Proyecto Uno", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "AySA"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-01-05", "Project": _HNP_PROJECT, "Duration Decimal": "8", "Email": "a@danwor.com", "Client": "Danwor", "Task": _TASK_01},
            {"Date": "2026-01-05", "Project": _HNP_PROJECT, "Duration Decimal": "6", "Email": "a@danwor.com", "Client": "Danwor", "Task": _TASK_VACACIONES},
            {"Date": "2026-01-05", "Project": _HNP_PROJECT, "Duration Decimal": "4", "Email": "a@danwor.com", "Client": "Danwor", "Task": _TASK_OTHER},
        ]},
    )
    result = _rentabilidad_service().get_rentabilidad("tok")
    assert result["noAsignadasMonthly"][0] == 8
    assert sum(result["noAsignadasMonthly"]) == 8  # nunca 18 (8+6+4)


# --- A-F a nivel reporting.js real (Node harness) ---------------------------

_HARNESS = Path(__file__).resolve().parent / "reporting_capacidad_no_asignadas_tarea.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_capacidad_no_asignadas_tarea_js_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"No Asignadas Tarea JS invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL NO ASIGNADAS TAREA INVARIANTS PASSED" in result.stdout
