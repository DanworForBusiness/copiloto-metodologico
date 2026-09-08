"""
test_reporting_rentabilidad.py — Contrato de datos reales de Rentabilidad
(AMBAS perspectivas, Resumen Y Certificadas vs Ejecutadas — Reporting
Integral Batch B). Certificadas/Vendidas: backbone de Cartera (SharePoint,
`get_cartera_facts`). Trabajadas/Ejecutadas: adapter de Time Logs Reports
(CSV, MISMO que Horas Productivas — ver Correction 1/real-data-lineage.md
§8bis/§10). Determinístico, sin Graph real: monkeypatchea
`core.microsoft365` (mismo criterio que test_reporting_batch_b.py).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import openpyxl
from openpyxl.worksheet.table import Table

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService

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
    """XLSX mínimo con la tabla `Tabla24` vacía (mismo patrón que
    test_reporting_cartera.py) — Rentabilidad no ejercita Facturadas
    directamente, pero `get_cartera_facts` siempre la descarga."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facturado"
    ws.append(["Proyecto Base", "Suma de Cantidad"])
    ws.add_table(Table(displayName="Tabla24", ref="A1:B1"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _patch(monkeypatch, *, horas, proyectos, clientes, csv_files=None, file_rows=None):
    file_rows = file_rows or {}
    facturadas_bytes = _empty_facturadas_xlsx()

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return csv_files if csv_files is not None else []
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
        return facturadas_bytes  # descarga de la XLSX de Facturadas (get_cartera_facts)

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )


def _service():
    return ReportingService(_repo())


def test_get_rentabilidad_joins_certificadas_sharepoint_with_trabajadas_time_logs(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero",
             "HsVendidas": "10", "HsFacturadas": "8", "HsTrabajadas": "0"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Febrero",
             "HsVendidas": "5", "HsFacturadas": "4", "HsTrabajadas": "0"},
        ],
        proyectos=[{"id": "p1", "Title": "Implementación SADI v2", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "YPF", "ClientPartnerLookupId": "pm1"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            # `Duration Decimal` = horas decimales directas (SIN /1000, ver
            # FINAL RECONCILIATION CORRECTIVE): 3h en Enero, 2h en Febrero.
            {"Date": "2026-01-10", "Project": "Implementación SADI v2", "Duration Decimal": "3", "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
            {"Date": "2026-02-10", "Project": "Implementación SADI v2", "Duration Decimal": "2", "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
        ]},
    )
    result = _service().get_rentabilidad("tok")
    assert len(result["proyectos"]) == 1
    p = result["proyectos"][0]
    assert p["proyecto"] == "Implementación SADI v2" and p["cliente"] == "YPF" and p["tipo"] == "Proyecto"
    assert p["certificadas"] == 12  # 8 + 4, SharePoint
    assert p["trabajadas"] == 5  # 3 + 2, Time Logs (horas decimales, sin escala)
    assert p["certMonthly"][0] == 8 and p["certMonthly"][1] == 4
    assert p["trabMonthly"][0] == 3 and p["trabMonthly"][1] == 2
    assert p["fecha"] == "2026-01-01"


def test_get_rentabilidad_projects_estado_from_project_status(monkeypatch):
    # Paso 1 de la migración Año/Mes/Estado (HANDOFF §-16 #8): el shape de
    # Rentabilidad proyecta `estado` desde Proyectos.Status — insumo del futuro
    # facet Estado. Aditivo (no altera certificadas/trabajadas/monthly).
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero",
                "HsVendidas": "10", "HsFacturadas": "8", "HsTrabajadas": "0"}],
        proyectos=[{"id": "p1", "Title": "Implementación SADI v2", "TipodeProyecto": "Proyecto", "Status": "Finalizado"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
        # get_rentabilidad siempre lee Time Logs — el fixture debe proveerlo
        # (sin esto: ReportingSourceError stage=time_logs_files antes del assert).
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-01-10", "Project": "Implementación SADI v2", "Duration Decimal": "0",
             "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
        ]},
    )
    p = _service().get_rentabilidad("tok")["proyectos"][0]
    assert p["estado"] == "Finalizado"


def test_get_rentabilidad_project_with_no_time_logs_gets_zero_trabajadas(monkeypatch):
    # Un proyecto SharePoint sin ninguna hora en Time Logs (sin tracking
    # aún, pero Time Logs SÍ tiene datos de OTROS proyectos) aporta
    # trabajadas=0 en TODOS los meses — NUNCA se omite la fila.
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Marzo",
                "HsVendidas": "5", "HsFacturadas": "5", "HsTrabajadas": "0"}],
        proyectos=[{"id": "p1", "Title": "Mantenimiento X", "TipodeProyecto": "Tiempo & Materiales"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-03-01", "Project": "Otro Proyecto Sin Relacion", "Duration Decimal": "1000", "Email": "z@danwor.com", "Client": "Otro", "Task": "Dev"},
        ]},
    )
    result = _service().get_rentabilidad("tok")
    assert len(result["proyectos"]) == 1
    p = result["proyectos"][0]
    assert p["trabajadas"] == 0
    assert p["trabMonthly"] == [0] * 12
    assert p["certificadas"] == 5


def test_get_rentabilidad_portfolio_monthly_totals_real_grain(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero", "HsVendidas": "10", "HsFacturadas": "0"},
            {"ProyectoLookupId": "p2", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero", "HsVendidas": "7", "HsFacturadas": "0"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Febrero", "HsVendidas": "3", "HsFacturadas": "0"},
        ],
        proyectos=[
            {"id": "p1", "Title": "Proyecto Uno", "TipodeProyecto": "Proyecto"},
            {"id": "p2", "Title": "Proyecto Dos", "TipodeProyecto": "Proyecto"},
        ],
        clientes=[{"id": "c1", "Title": "AySA"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            # Proyecto placeholder de No Asignadas, tarea 01 — NUNCA aporta a
            # "proyectos", solo al portfolio noAsignadasMonthly.
            {"Date": "2026-01-05", "Project": "Danwor - Horas No Productivas (#1)", "Duration Decimal": "4", "Email": "b@danwor.com", "Client": "Danwor", "Task": "01. No Asignación a Servicio en Cliente"},
            # TASK CORRECTIVE (owner ticket "URGENT REPORTING CORRECTNESS —
            # HNP TASK + SOLD HOURS YEAR ACCUMULATION"): MISMO proyecto
            # placeholder, OTRA tarea (Vacaciones) — "No asignadas a
            # servicios" es el CONCEPTO tarea-01, nunca el proyecto HNP
            # entero. Esta fila NUNCA debe sumar a noAsignadasMonthly.
            {"Date": "2026-01-05", "Project": "Danwor - Horas No Productivas (#1)", "Duration Decimal": "6", "Email": "b@danwor.com", "Client": "Danwor", "Task": "02. Vacaciones"},
        ]},
    )
    result = _service().get_rentabilidad("tok")
    # SOURCE-PARITY corrective: Vendidas ya NO es un escalar de portfolio —
    # vive POR PROYECTO (vendMonthly, mismo shape que certMonthly/trabMonthly),
    # ver test_reporting_rentabilidad_source_parity.py para el porqué (PBIP
    # filtra Vendidas por Tipo Proyecto/Estado igual que Certificadas).
    by_proyecto = {p["proyecto"]: p for p in result["proyectos"]}
    assert by_proyecto["Proyecto Uno"]["vendMonthly"][0] == 10
    assert by_proyecto["Proyecto Uno"]["vendMonthly"][1] == 3
    assert by_proyecto["Proyecto Dos"]["vendMonthly"][0] == 7
    assert by_proyecto["Proyecto Uno"]["vendidas"] == 13
    assert by_proyecto["Proyecto Dos"]["vendidas"] == 7
    # No asignadas SIGUE siendo portfolio: Enero=4 (SOLO tarea 01 del
    # proyecto placeholder — las 6h de Vacaciones del MISMO proyecto NUNCA
    # entran, ver TASK CORRECTIVE arriba).
    assert result["noAsignadasMonthly"][0] == 4
    assert sum(result["noAsignadasMonthly"]) == 4
    # El placeholder NUNCA aparece como fila de proyecto real.
    proyecto_names = set(by_proyecto)
    assert "Danwor - Horas No Productivas (#1)" not in proyecto_names


def test_get_rentabilidad_trabajadas_join_uses_normalized_key_not_exact_title(monkeypatch):
    """SOURCE-PARITY corrective (defecto #1, Trabajadas 4.6 vs 4,624.5 en el
    caso dorado del owner): el join Cartera<->Time Logs usa `_norm_key`
    (Upper+sin espacios) — MISMA normalización que el PBIP usa para unir
    estas 2 fuentes en `Comparativo` (Key_Proyect_desc/Proyecto_Key), NUNCA
    un match exacto de string.

    Fixture: un proyecto HISTÓRICO que ya no existe en la lista Proyectos
    actual (ProyectoLookupId/Project sin match) — AMBOS lados caen a su
    propio fallback de texto crudo con distinta capitalización/espaciado
    entre sí ("Proyecto  Legado X" en Horas por proyectos vs "PROYECTO
    LEGADO X" en el CSV de Time Logs). El join EXACTO viejo nunca los habría
    unido (son strings distintos); `_norm_key` sí, porque colapsan a la
    MISMA clave — prueba directa de que Trabajadas ya no desaparece por
    drift de nombres históricos entre las 2 fuentes físicas."""
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "missing-id", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero",
             "HsVendidas": "10", "HsFacturadas": "8", "HsTrabajadas": "0",
             "Proyecto": "Proyecto  Legado X"},
        ],
        proyectos=[],  # el proyecto NO existe en Proyectos (archivado/histórico)
        clientes=[{"id": "c1", "Title": "YPF"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-01-10", "Project": "PROYECTO LEGADO X", "Duration Decimal": "5",
             "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
        ]},
    )
    result = _service().get_rentabilidad("tok")
    assert len(result["proyectos"]) == 1
    p = result["proyectos"][0]
    assert p["trabajadas"] == 5, (
        "el join debe unir 'Proyecto  Legado X' (Cartera) con 'PROYECTO LEGADO X' "
        "(Time Logs) vía _norm_key pese a NO ser el mismo string exacto"
    )
    assert p["certificadas"] == 8  # Certificadas sin cambios — no forma parte de este defecto.


# ============================================================================
# Frontend — ciclo de vida real-data propio + monthly real (no flatMonthly)
# ============================================================================

_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


def test_rentabilidad_endpoint_and_lifecycle_exist():
    assert '"/operaciones/reporting/integral/data/rentabilidad"' in _REPORTING_JS
    assert "function loadRentabilidadData" in _REPORTING_JS
    assert "function renderRentabilidadDataState" in _REPORTING_JS
    assert "rentabilidadDataStatus: \"idle\"," in _REPORTING_JS


def test_rentabilidad_never_mixes_mock_and_real_data():
    load_fn = _REPORTING_JS[_REPORTING_JS.index("async function loadRentabilidadData"):_REPORTING_JS.index("async function loadRentabilidadData") + 1200]
    assert "replaceRows(RENTABILIDAD_PROYECTOS, data.rentabilidad.proyectos)" in load_fn
    assert "replaceMonthly(RENTABILIDAD_NOASIGNADAS_MENSUAL, data.rentabilidad.noAsignadasMonthly)" in load_fn
    # SOURCE-PARITY corrective: Vendidas dejó de ser un escalar de portfolio
    # (ver test_reporting_rentabilidad_source_parity.py) — ya NO se puebla acá,
    # llega dentro de cada fila de `data.rentabilidad.proyectos` (vendByYear).
    assert "vendidasMonthly" not in load_fn


def test_rentabilidad_resumen_and_cert_ejec_gate_on_rentabilidad_status():
    # MAIN-TAB HYDRATION CONTRACT (HANDOFF §-15/-17): la hidratación ya no
    # corre en cada sub-renderer — mismo patrón que Cartera/Horas Vendidas.
    # SHARED REPORTING INITIAL-LOAD CONTRACT (HANDOFF §-18): el trigger se
    # centralizó en el shell (`TAB_HYDRATION_TRIGGERS.rentabilidad`, ver
    # `render()`) — tampoco vive ya en el dispatcher `renderRentabilidad`.
    for fn in ("renderRentabilidadResumen", "renderRentabilidadCertEjecutadas"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 400]
        assert 'state.rentabilidadDataStatus !== "ready"' in body
        assert "loadRentabilidadData()" not in body
    dispatcher = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidad()"):_REPORTING_JS.index("function renderRentabilidad()") + 700]
    assert "hydrateRentabilidadTab();" not in dispatcher
    assert "rentabilidad: hydrateRentabilidadTab," in _REPORTING_JS


def test_rentabilidad_mensual_uses_real_monthly_grain_not_flat_spread():
    # Batch B: certMonthly/trabMonthly por proyecto son grano mensual REAL
    # (fuente proyecto×mes) — sumados directo, NUNCA flatMonthly de un
    # escalar (esa simplificación de V0 quedó retirada de esta función).
    # TEMPORAL DOMAIN CONTRACT: la suma ahora pasa por `_sumMonthlyArrays`
    # (NULL-AWARE — preserva "—" en vez de "0" para meses capados por el
    # current-year future-month cap), no un `+=` ingenuo.
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidadMensual"):_REPORTING_JS.index("function renderRentabilidadResumen")]
    assert "_sumMonthlyArrays(rows.map(p => p.certMonthly))" in fn
    assert "_sumMonthlyArrays(rows.map(p => p.trabMonthly))" in fn
    assert "_diffMonthly(certMonthly, trabMonthly)" in fn
    assert "flatMonthly(p.certificadas)" not in fn
    assert "flatMonthly(p.trabajadas)" not in fn


# --- Ranking Top 5 por default (owner feedback) --------------------------

import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

_RANKING_HARNESS = Path(__file__).resolve().parent / "reporting_rentabilidad_ranking.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_rentabilidad_ranking_top5_invariants_hold():
    result = subprocess.run(
        ["node", str(_RANKING_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Rentabilidad ranking invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL RENTABILIDAD RANKING INVARIANTS PASSED" in result.stdout


def test_mejor_performance_renders_left_of_peor_performance():
    body = _REPORTING_JS[_REPORTING_JS.index("function renderRentabilidadResumen"):_REPORTING_JS.index("function certEjecGroupTooltip")]
    mejor_idx = body.index("Mejor performance")
    peor_idx = body.index("Peor performance")
    assert mejor_idx < peor_idx, "Mejor performance debe aparecer ANTES que Peor en el markup (columna izquierda del grid de 2 columnas)"
    assert '"activity-grid-2"' in body, "reusa el primitivo Hub de 2 columnas compartido, no un grid propio de Rentabilidad"


def test_get_rentabilidad_keeps_year_grain_separate(monkeypatch):
    # Migración Año/Mes/Estado: Ene-2026 y Ene-2025 quedan direccionables por
    # separado (certByYear/trabByYear/vendidasByYear), nunca colapsados antes del
    # filtro. Estado proyectado.
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero",
             "HsVendidas": "10", "HsFacturadas": "8", "HsTrabajadas": "0"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2025", "Mes": "Enero",
             "HsVendidas": "4", "HsFacturadas": "3", "HsTrabajadas": "0"},
        ],
        proyectos=[{"id": "p1", "Title": "SADI", "TipodeProyecto": "Proyecto", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-01-10", "Project": "SADI", "Duration Decimal": "5",
             "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
        ]},
    )
    r = _service().get_rentabilidad("tok")
    p = r["proyectos"][0]
    assert p["certByYear"]["2026"][0] == 8 and p["certByYear"]["2025"][0] == 3   # Ene por año
    assert p["trabByYear"]["2026"][0] == 5
    assert p["vendByYear"]["2026"][0] == 10 and p["vendByYear"]["2025"][0] == 4
    assert p["estado"] == "En curso"


# ============================================================================
# HOT WINDOW / COLD HISTORY (owner live QA — "hot window was applied to
# Horas Vendidas but not Rentabilidad")
# ============================================================================

def test_get_rentabilidad_defaults_to_hot_window(monkeypatch):
    from datetime import datetime
    captured = {}

    def fake_cartera_facts(self, access_token, *, include_facturadas=True, hot_years=None, cache_scope=None):
        captured["hot_years"] = hot_years
        captured["include_facturadas"] = include_facturadas
        return [], {}

    def fake_time_logs(self, access_token, *, hot_years=None, cache_scope=None):
        captured["time_logs_hot_years"] = hot_years
        return []

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)
    _service().get_rentabilidad("tok")
    y = datetime.now().year
    assert captured["hot_years"] == [str(y), str(y - 1)]
    # P5 (owner live QA de performance — mismo criterio que Cartera/Mantenimiento):
    # Rentabilidad NUNCA lee facturadasProyecto, la XLSX era un costo desperdiciado.
    assert captured["include_facturadas"] is False
    # TIME LOGS SCOPE corrective (owner 2026-08-18 — root cause de la
    # regresión ~14s→~92s: Time Logs quedaba SIN acotar acá, heredando
    # "todos los archivos" desde que el loader compartido se hizo
    # recursivo). MISMA ventana que "Horas por proyectos", nunca None.
    assert captured["time_logs_hot_years"] == [str(y), str(y - 1)]


def test_get_rentabilidad_cold_year_passes_explicit_scope(monkeypatch):
    captured = {}

    def fake_cartera_facts(self, access_token, *, include_facturadas=True, hot_years=None, cache_scope=None):
        captured["hot_years"] = hot_years
        return [], {}

    def fake_time_logs(self, access_token, *, hot_years=None, cache_scope=None):
        captured["time_logs_hot_years"] = hot_years
        return []

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)
    _service().get_rentabilidad("tok", anios=["2024"])
    assert captured["hot_years"] == ["2024"]
    assert captured["time_logs_hot_years"] == ["2024"]


def test_get_rentabilidad_anios_reuses_horas_vendidas_metadata(monkeypatch):
    # "Do not duplicate temporal runtime logic" — Rentabilidad reusa el MISMO
    # helper de metadata que Horas Vendidas (mismo dominio: todos los tipos).
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "A_x00f1_o": "2026"}, {"ProyectoLookupId": "p2", "A_x00f1_o": "2024"}],
        proyectos=[
            {"id": "p1", "TipodeProyecto": "Tiempo & Materiales", "Status": "En curso"},
            {"id": "p2", "TipodeProyecto": "Proyecto", "Status": "Finalizado"},
        ],
        clientes=[{"id": "c1", "Title": "YPF"}],
    )
    anios = {p["anio"] for p in _service().get_rentabilidad_anios("tok")}
    assert anios == {"2026", "2024"}


# ============================================================================
# TEMPORAL DOMAIN CONTRACT (owner live QA — "Septiembre" apareció como
# opción imposible del <select> de Año en Horas Vendidas)
# ============================================================================

def test_get_rentabilidad_rejects_non_year_anio_value(monkeypatch):
    # Un valor no-4-dígitos en A_x00f1_o (dato de origen corrupto/mal cargado)
    # NUNCA debe alcanzar certByYear/vendByYear — se descarta en la
    # fuente, nunca filtrado recién en el HTML.
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "Septiembre", "Mes": "Julio",
             "HsVendidas": "99", "HsFacturadas": "99", "HsTrabajadas": "0"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio",
             "HsVendidas": "10", "HsFacturadas": "8", "HsTrabajadas": "0"},
        ],
        proyectos=[{"id": "p1", "Title": "SADI", "TipodeProyecto": "Proyecto", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-07-10", "Project": "SADI", "Duration Decimal": "0",
             "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
        ]},
    )
    r = _service().get_rentabilidad("tok")
    p = r["proyectos"][0]
    assert "Septiembre" not in p["certByYear"]
    assert "Septiembre" not in p["vendByYear"]
    assert set(p["certByYear"].keys()) == {"2026"}
