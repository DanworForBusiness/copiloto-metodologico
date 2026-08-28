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

import pytest

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


# --- ANTI FAN-OUT (owner live proof, agosto 2026) -------------------------
# "Horas por proyectos" PUEDE traer N filas para el mismo proyecto x anio x
# mes (distintas entradas/correcciones del mismo mes). El bucle de emision
# le asignaba a CADA una el agregado mensual COMPLETO de Time Logs, asi que
# `trabajadas` quedaba multiplicada por N. Medido en vivo: 3 filas x 203,5 h
# = 610,5 h reportadas, y 2 x 9,0 = 18,0 -> 416 h inventadas en un mes.
#
# El grano de SALIDA es proyecto x anio x mes (ver el UNION GRAIN corrective
# del docstring de `_mantenimiento_rows`): las horas trabajadas canonicas
# entran UNA sola vez, sin importar cuantas filas tenga la fuente.
_FANOUT_H = 203.5          # el valor vivo del caso A
_FANOUT_DECOY = "999"      # si algun camino volviera a leer HsTrabajadas


def _fanout_fixture(monkeypatch, n_filas, horas_time_logs, mes="Agosto", dia="05"):
    """N filas de SharePoint para el MISMO proyecto x anio x mes, y UN solo
    agregado de Time Logs. `HsVendidas`/`HsFacturadas` distintas por fila para
    poder verificar que se SUMAN (no se descartan ni se toma "la primera")."""
    _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": mes,
            "HsTrabajadas": _FANOUT_DECOY,
            "HsVendidas": str(100 + i), "HsFacturadas": str(10 + i),
        } for i in range(n_filas)],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento",
                     "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [
            {"Date": f"2026-08-{dia}", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": str(horas_time_logs)},
        ]},
    )
    return ReportingService(_repo()).get_cartera_mantenimiento("tok", anios=["2026"])


@pytest.mark.parametrize("n_filas", [1, 2, 3])
def test_worked_hours_never_fan_out_no_matter_how_many_source_rows(monkeypatch, n_filas):
    rows = _fanout_fixture(monkeypatch, n_filas, _FANOUT_H)
    agosto = [r for r in rows if r["mes"] == "8"]
    total = round(sum(r["trabajadas"] for r in agosto), 2)
    # LA invariante: el canonico entra una vez, no N veces.
    assert total == _FANOUT_H, (n_filas, total)
    # Y explicitamente: NO es N x H para N > 1.
    if n_filas > 1:
        assert total != round(n_filas * _FANOUT_H, 2)
    # Grano de salida: UNA fila logica por proyecto x anio x mes.
    assert len(agosto) == 1, (n_filas, len(agosto))
    # El campo manual nunca alimenta trabajadas.
    assert total != float(_FANOUT_DECOY)


@pytest.mark.parametrize("n_filas", [1, 2, 3])
def test_certified_and_sold_hours_are_summed_not_discarded(monkeypatch, n_filas):
    # Las otras medidas SI son aditivas por fila de origen: cada fila de
    # SharePoint aporta sus propias horas. Colapsar el grano no puede
    # perderlas (la UI ya las sumaba por rollup, ver CARTERA_CONFIG).
    rows = _fanout_fixture(monkeypatch, n_filas, _FANOUT_H)
    agosto = [r for r in rows if r["mes"] == "8"]
    esperado_vend = float(sum(100 + i for i in range(n_filas)))
    esperado_cert = float(sum(10 + i for i in range(n_filas)))
    assert round(sum(r["vendidasMensuales"] for r in agosto), 2) == esperado_vend
    assert round(sum(r["certificadas"] for r in agosto), 2) == esperado_cert
    # `facturadas` es source-faithful en Mantenimiento (== certificadas).
    assert round(sum(r["facturadas"] for r in agosto), 2) == esperado_cert
    # `diff` se recalcula sobre los valores colapsados, no arrastra el fan-out.
    assert round(sum(r["diff"] for r in agosto), 2) == round(esperado_cert - _FANOUT_H, 2)


def test_live_case_a_three_source_rows_report_2035_not_6105(monkeypatch):
    # Equivalente al caso vivo A (Danone - Modern Data Stack SAP Fase I).
    rows = _fanout_fixture(monkeypatch, 3, 203.5)
    agosto = [r for r in rows if r["mes"] == "8"]
    assert round(sum(r["trabajadas"] for r in agosto), 2) == 203.5
    assert round(sum(r["trabajadas"] for r in agosto), 2) != 610.5


def test_live_case_b_two_source_rows_report_90_not_180(monkeypatch):
    # Equivalente al caso vivo B (Danone - Mantenimiento Proyecto B-One).
    rows = _fanout_fixture(monkeypatch, 2, 9.0)
    agosto = [r for r in rows if r["mes"] == "8"]
    assert round(sum(r["trabajadas"] for r in agosto), 2) == 9.0
    assert round(sum(r["trabajadas"] for r in agosto), 2) != 18.0


def test_collapsing_the_grain_does_not_merge_different_months(monkeypatch):
    # El grano incluye el mes: agrupar no puede fusionar meses distintos.
    _patch(
        monkeypatch,
        horas_por_proyectos=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio",
             "HsTrabajadas": _FANOUT_DECOY, "HsVendidas": "10", "HsFacturadas": "1"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
             "HsTrabajadas": _FANOUT_DECOY, "HsVendidas": "20", "HsFacturadas": "2"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
             "HsTrabajadas": _FANOUT_DECOY, "HsVendidas": "30", "HsFacturadas": "3"},
        ],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento",
                     "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [
            {"Date": "2026-07-10", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "40"},
            {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "60"},
        ]},
    )
    rows = ReportingService(_repo()).get_cartera_mantenimiento("tok", anios=["2026"])
    por_mes = {r["mes"]: r for r in rows}
    assert sorted(por_mes) == ["7", "8"]                 # dos filas, no una ni tres
    assert por_mes["7"]["trabajadas"] == 40.0
    assert por_mes["8"]["trabajadas"] == 60.0            # NO 120.0
    assert por_mes["8"]["vendidasMensuales"] == 50.0     # las dos filas de agosto, sumadas


# --- UNIVERSE SYMMETRY: Rentabilidad ve el MISMO universo que Cartera -----
# Owner live proof (agosto 2026): "Tenaris - Servicio Estrategico SAP BDC"
# tenia 14,5 h en Cartera y 0 en Rentabilidad. Cartera descubre proyectos por
# Time Logs + `project_dim` (Proyectos) aunque no exista fila en "Horas por
# proyectos"; Rentabilidad iteraba SOLO `cert_by_proyecto`, o sea esa lista.
# Ahora el universo es la UNION, y las dimensiones salen de `project_dim`
# (nunca inventadas).
_LIVE_TENARIS_H = 14.5


def _universe_fixture(monkeypatch, *, con_fila_sharepoint, tipo="Mantenimiento",
                      en_proyectos=True, horas=_LIVE_TENARIS_H):
    """Proyecto con horas de Time Logs en agosto 2026. `con_fila_sharepoint`
    controla si existe la fila de "Horas por proyectos"; `en_proyectos`, si el
    proyecto resuelve contra la dimension."""
    hp = []
    if con_fila_sharepoint:
        hp.append({"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026",
                    "Mes": "Agosto", "HsTrabajadas": "999", "HsVendidas": "40", "HsFacturadas": "30"})
    _patch(
        monkeypatch,
        horas_por_proyectos=hp,
        proyectos=([{"id": "p1", "Title": "Tenaris - SAP BDC", "TipodeProyecto": tipo,
                      "Status": "En curso"}] if en_proyectos else []),
        clientes=[{"id": "c1", "Title": "Tenaris"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [
            {"Date": "2026-08-12", "Client": "Tenaris", "Project": "Tenaris - SAP BDC",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": str(horas)},
        ]},
    )
    return ReportingService(_repo())


def _rent_row(payload, proyecto):
    return next((r for r in payload["proyectos"] if r["proyecto"] == proyecto), None)


def test_rentabilidad_includes_a_time_logs_only_project_with_valid_mapping(monkeypatch):
    # A + B: presente, y con las horas canonicas de Time Logs.
    svc = _universe_fixture(monkeypatch, con_fila_sharepoint=False)
    row = _rent_row(svc.get_rentabilidad("tok", anios=["2026"]), "Tenaris - SAP BDC")
    assert row is not None, "el proyecto desaparecia de Rentabilidad"
    assert row["trabByYear"]["2026"][7] == _LIVE_TENARIS_H
    assert row["trabajadas"] == _LIVE_TENARIS_H          # 14.5, nunca 0


def test_time_logs_only_project_inherits_its_dimensions_from_project_dim(monkeypatch):
    # C: Tipo Proyecto (y el resto de la dimension) vienen de `project_dim`,
    # nunca inventados — asi que el facet Tipo Proyecto sigue discriminando.
    for tipo in ("Mantenimiento", "Proyecto"):
        svc = _universe_fixture(monkeypatch, con_fila_sharepoint=False, tipo=tipo)
        row = _rent_row(svc.get_rentabilidad("tok", anios=["2026"]), "Tenaris - SAP BDC")
        assert row["tipo"] == tipo, tipo
        assert row["estado"] == "En curso"
        # `cliente`/`cp` viven en la fila de "Horas por proyectos", no en
        # Proyectos: sin esa fila, `project_dim` los deja en "" a proposito
        # (ver reporting_repository, project_dim se siembra vacio y solo se
        # completa desde un fact). NO se fabrican — y la clave es que
        # Rentabilidad muestre EXACTAMENTE lo mismo que Cartera para ese
        # proyecto, no que invente un nombre.
        assert row["cliente"] == "" and row["cp"] == ""
        if tipo == "Mantenimiento":
            cart = [r for r in svc.get_cartera_mantenimiento("tok", anios=["2026"])
                    if r["proyecto"] == "Tenaris - SAP BDC" and r["mes"] == "8"]
            assert len(cart) == 1
            assert cart[0]["cliente"] == row["cliente"]
            assert cart[0]["cp"] == row["cp"]
            assert cart[0]["status"] == row["estado"]
    # Y las medidas que no tienen fuente conservan su cero legitimo.
    assert row["certByYear"] == {} and row["vendByYear"] == {}
    assert row["certificadas"] == 0.0 and row["vendidas"] == 0.0


def test_project_present_in_both_universes_emits_exactly_one_row(monkeypatch):
    # D + E: sin duplicar cuando existe en las dos fuentes.
    svc = _universe_fixture(monkeypatch, con_fila_sharepoint=True)
    payload = svc.get_rentabilidad("tok", anios=["2026"])
    filas = [r for r in payload["proyectos"] if r["proyecto"] == "Tenaris - SAP BDC"]
    assert len(filas) == 1
    assert filas[0]["trabByYear"]["2026"][7] == _LIVE_TENARIS_H
    assert filas[0]["certByYear"]["2026"][7] == 30.0     # la fila SharePoint SI aporta
    assert filas[0]["trabajadas"] != 999.0               # el decoy manual nunca


def test_unmapped_time_logs_project_gets_no_fabricated_dimensions(monkeypatch):
    # F: sin mapping en Proyectos, no se inventa Cliente/Tipo/Estado.
    svc = _universe_fixture(monkeypatch, con_fila_sharepoint=False, en_proyectos=False)
    payload = svc.get_rentabilidad("tok", anios=["2026"])
    assert _rent_row(payload, "Tenaris - SAP BDC") is None
    for r in payload["proyectos"]:
        assert r["cliente"] != "" or r["tipo"] != "", r    # nada fabricado vacio
    assert not any(r["tipo"] == "" and r["trabajadas"] for r in payload["proyectos"])


def test_cross_view_worked_hours_reconcile_for_the_same_project_month(monkeypatch):
    # G — INVARIANTE INTEGRADO: mismo proyecto x anio x mes, tipo
    # Mantenimiento y mapping valido ⇒ MISMO numero en las dos vistas, con
    # 0, 1, 2 o 3 filas en "Horas por proyectos".
    for n_filas in (0, 1, 2, 3):
        _patch(
            monkeypatch,
            horas_por_proyectos=[{
                "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
                "HsTrabajadas": "999", "HsVendidas": str(10 + i), "HsFacturadas": str(5 + i),
            } for i in range(n_filas)],
            proyectos=[{"id": "p1", "Title": "Tenaris - SAP BDC",
                         "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
            clientes=[{"id": "c1", "Title": "Tenaris"}],
            csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
            file_rows={"current-1": [
                {"Date": "2026-08-12", "Client": "Tenaris", "Project": "Tenaris - SAP BDC",
                 "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": str(_LIVE_TENARIS_H)},
            ]},
        )
        svc = ReportingService(_repo())
        cartera = sum(r["trabajadas"] for r in svc.get_cartera_mantenimiento("tok", anios=["2026"])
                       if r["mes"] == "8")
        svc2 = ReportingService(_repo())
        rent = _rent_row(svc2.get_rentabilidad("tok", anios=["2026"]), "Tenaris - SAP BDC")
        rent_h = rent["trabByYear"]["2026"][7]
        assert round(cartera, 2) == round(rent_h, 2) == _LIVE_TENARIS_H, (n_filas, cartera, rent_h)


# --- CONSERVACION: ninguna hora canonica desaparece en silencio -----------
# Regla de nivel superior (owner): toda hora positiva de Time Logs esta
#   A. representada en un proyecto de negocio con mapping valido, o
#   B. clasificada EXPLICITAMENTE como no reconciliable.
# Nada puede evaporarse. En esta fase la clasificacion es interna/visible en
# test: la observabilidad en UI es trabajo aparte, deliberadamente diferido.
def _conservacion_fixture(monkeypatch):
    """Cuatro clases de hora de Time Logs en agosto 2026, todas positivas:
      100 h  proyecto Mantenimiento con mapping y fila SharePoint
       50 h  proyecto Mantenimiento con mapping y SIN fila SharePoint
       25 h  proyecto tipo Proyecto con mapping (fuera del universo de Mant.)
       10 h  proyecto SIN mapping en Proyectos
        7 h  placeholder de "No asignadas"
    """
    _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
            "HsTrabajadas": "999", "HsVendidas": "1", "HsFacturadas": "1",
        }],
        proyectos=[
            {"id": "p1", "Title": "Mant Con Fila", "TipodeProyecto": "Mantenimiento", "Status": "En curso"},
            {"id": "p2", "Title": "Mant Sin Fila", "TipodeProyecto": "Mantenimiento", "Status": "En curso"},
            {"id": "p3", "Title": "Tipo Proyecto", "TipodeProyecto": "Proyecto", "Status": "En curso"},
        ],
        clientes=[{"id": "c1", "Title": "ACME"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [
            {"Date": "2026-08-03", "Client": "ACME", "Project": "Mant Con Fila",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "100"},
            {"Date": "2026-08-04", "Client": "ACME", "Project": "Mant Sin Fila",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "50"},
            {"Date": "2026-08-05", "Client": "ACME", "Project": "Tipo Proyecto",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "25"},
            {"Date": "2026-08-06", "Client": "ACME", "Project": "Fantasma Sin Alta",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "10"},
            {"Date": "2026-08-07", "Client": "Danwor", "Project": "Danwor - Horas No Productivas (#1)",
             "Task": "Interno", "Email": "a@danwor.com", "Duration Decimal": "7"},
        ]},
    )
    return 100.0, 50.0, 25.0, 10.0, 7.0


def test_every_positive_time_logs_hour_is_represented_or_classified_in_rentabilidad(monkeypatch):
    con_fila, sin_fila, tipo_proy, fantasma, placeholder = _conservacion_fixture(monkeypatch)
    payload = ReportingService(_repo()).get_rentabilidad("tok", anios=["2026"])
    en_proyectos = round(sum(r["trabByYear"].get("2026", [0.0] * 12)[7]
                             for r in payload["proyectos"]), 2)
    no_asignadas = round(payload["noAsignadasByYear"]["2026"][7], 2)
    # A: Rentabilidad cubre TODOS los tipos con mapping valido.
    assert en_proyectos == round(con_fila + sin_fila + tipo_proy, 2)
    # A (bis): el placeholder tiene su propia fila de portfolio, no se pierde.
    assert no_asignadas == placeholder
    # B: lo unico fuera es el proyecto sin alta — clasificado, no evaporado.
    total_canonico = con_fila + sin_fila + tipo_proy + fantasma + placeholder
    assert round(total_canonico - en_proyectos - no_asignadas, 2) == fantasma
    # Y NUNCA aparece con dimensiones inventadas.
    assert not any(r["proyecto"] == "Fantasma Sin Alta" for r in payload["proyectos"])


def test_every_positive_time_logs_hour_is_represented_or_classified_in_cartera(monkeypatch):
    con_fila, sin_fila, tipo_proy, fantasma, placeholder = _conservacion_fixture(monkeypatch)
    repo = _repo()
    facts, project_dim = repo.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, hot_years=["2026"])
    trab = _trab_by_proyecto_by_year(
        repo.get_time_logs_worked_hours("tok", hot_years=["2026"]))
    rows, unmatched = ReportingService(repo)._mantenimiento_rows(facts, trab, project_dim)

    en_filas = round(sum(r["trabajadas"] for r in rows if r["mes"] == "8"), 2)
    no_reconciliable = round(sum(u["horas"] for u in unmatched if u["mes"] == "8"), 2)
    fuera_del_universo = round(
        sum(monthly["2026"][7] for key, monthly in trab.items()
            if (project_dim.get(key) or {}).get("tipoProyecto") not in (None, "Mantenimiento")), 2)

    # A: los dos proyectos Mantenimiento con mapping — con y sin fila.
    assert en_filas == round(con_fila + sin_fila, 2)
    # B: el proyecto sin alta queda clasificado, nunca descartado.
    assert no_reconciliable == fantasma
    # Exclusion DECLARADA de universo (no una perdida): tipo Proyecto.
    assert fuera_del_universo == tipo_proy
    # La particion cubre todo lo que Cartera podia ver (el placeholder ya lo
    # excluye `_trab_by_proyecto_by_year`, tiene su propia representacion).
    visible_para_cartera = con_fila + sin_fila + tipo_proy + fantasma
    assert round(visible_para_cartera - en_filas - no_reconciliable - fuera_del_universo, 2) == 0.0


# --- PERSPECTIVA PROYECTO: fuente canonica, grano de VIDA -----------------
# Owner decision OPTION A: Proyecto sigue siendo lifetime/project-level (una
# fila por proyecto, "Hs Trabajadas Acum", sin Anio/Mes de horas — `anioInicio`
# decide QUE proyectos entran, no que horas se suman), pero `trabajadas` deja
# de salir del campo manual `hsTrabajadas` y pasa a Time Logs, la MISMA
# autoridad y el MISMO join `_norm_key(proyecto)` que Mantenimiento y
# Rentabilidad. El campo manual sigue existiendo; solo dejo de ser la medida.
_PROY_DECOY = "999"        # HsTrabajadas manual: si volviera, se ve


def _tl(fecha, horas, proyecto="Rollout Reporting"):
    return {"Date": fecha, "Client": "Ternium", "Project": proyecto,
            "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": str(horas)}


def _proy_patch(monkeypatch, *, n_filas_sharepoint=1, time_logs_rows=(),
                proyectos=None, tipo="Proyecto"):
    """N filas de "Horas por proyectos" para el MISMO proyecto, mas Time Logs
    repartidos en DOS anios (archivo historico + archivo actual) para poder
    verificar el total de VIDA."""
    _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1",
            "A_x00f1_o": "2025" if i else "2026", "Mes": "Marzo",
            "HsTrabajadas": _PROY_DECOY,
            "HsVendidas": str(10 + i), "HsFacturadas": str(5 + i),
        } for i in range(n_filas_sharepoint)],
        proyectos=(proyectos if proyectos is not None else
                   [{"id": "p1", "Title": "Rollout Reporting",
                     "TipodeProyecto": tipo, "Status": "En curso",
                     "FechadeInicio": "2026-01-15"}]),
        clientes=[{"id": "c1", "Title": "Ternium"}],
        csv_files=[{"id": "y2024", "name": "Time Logs Reports 2024.csv"},
                   {"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"y2024": list(time_logs_rows[:1]), "current-1": list(time_logs_rows[1:])},
    )
    # La perspectiva Proyecto SI descarga/parsea la XLSX de Facturadas, que es
    # ortogonal a lo que prueban estos tests (horas trabajadas) — `_patch`
    # devuelve CSV para cualquier byte y eso no es un workbook valido. Se
    # stubbea a vacio: `facturadas` no participa de ninguna afirmacion de acá.
    monkeypatch.setattr(ReportingRepository, "_facturadas_by_project", lambda self, site_id: {})
    return ReportingService(_repo())


_TL_VIDA = [_tl("2024-05-02", 100), _tl("2026-03-10", 40)]      # 140 h de vida


def test_proyecto_worked_hours_come_from_time_logs_not_the_manual_field(monkeypatch):
    svc = _proy_patch(monkeypatch, time_logs_rows=_TL_VIDA)
    fila = next(r for r in svc.get_cartera_proyecto("tok") if r["proyecto"] == "Rollout Reporting")
    assert fila["trabajadas"] == 140.0            # 100 (2024) + 40 (2026)
    assert fila["trabajadas"] != float(_PROY_DECOY)


def test_proyecto_lifetime_total_sums_every_available_year_exactly_once(monkeypatch):
    svc = _proy_patch(monkeypatch, time_logs_rows=_TL_VIDA)
    fila = next(r for r in svc.get_cartera_proyecto("tok") if r["proyecto"] == "Rollout Reporting")
    assert fila["trabajadas"] == 140.0
    # Grano project-level: sin `anio`/`mes` en la salida.
    assert "anio" not in fila and "mes" not in fila
    assert fila["anioInicio"] == "2026"           # atributo del proyecto, no periodo


@pytest.mark.parametrize("n_filas", [1, 2, 3])
def test_proyecto_source_rows_never_multiply_the_canonical_total(monkeypatch, n_filas):
    # N filas de origen para el MISMO proyecto no pueden multiplicar Time Logs
    # (el mismo defecto que se corrigio en Mantenimiento).
    svc = _proy_patch(monkeypatch, n_filas_sharepoint=n_filas, time_logs_rows=_TL_VIDA)
    filas = [r for r in svc.get_cartera_proyecto("tok") if r["proyecto"] == "Rollout Reporting"]
    assert len(filas) == 1, n_filas               # una fila por proyecto, siempre
    assert filas[0]["trabajadas"] == 140.0, n_filas
    if n_filas > 1:
        assert filas[0]["trabajadas"] != 140.0 * n_filas
    # `vendidas`/`certificadas` SI son aditivas por fila de origen.
    assert filas[0]["vendidas"] == float(sum(10 + i for i in range(n_filas)))
    assert filas[0]["certificadas"] == float(sum(5 + i for i in range(n_filas)))


def test_proyecto_identity_is_the_normalized_project_name(monkeypatch):
    # Dos filas cuyo `idProyecto` difiere pero cuyo nombre normaliza IGUAL: UNA
    # fila, con el canonico UNA vez. Agrupar por id le daria el total completo a
    # cada una (fan-out), porque Time Logs solo trae el NOMBRE.
    _patch(
        monkeypatch,
        horas_por_proyectos=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026",
             "Mes": "Marzo", "HsTrabajadas": _PROY_DECOY, "HsVendidas": "10", "HsFacturadas": "5"},
            {"ProyectoLookupId": "p2", "EmpresaLookupId": "c1", "A_x00f1_o": "2026",
             "Mes": "Abril", "HsTrabajadas": _PROY_DECOY, "HsVendidas": "20", "HsFacturadas": "6"},
        ],
        proyectos=[
            {"id": "p1", "Title": "Rollout Reporting", "TipodeProyecto": "Proyecto", "Status": "En curso"},
            {"id": "p2", "Title": "ROLLOUT  REPORTING", "TipodeProyecto": "Proyecto", "Status": "En curso"},
        ],
        clientes=[{"id": "c1", "Title": "Ternium"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [_tl("2026-03-10", 40)]},
    )
    monkeypatch.setattr(ReportingRepository, "_facturadas_by_project", lambda self, site_id: {})
    rows = ReportingService(_repo()).get_cartera_proyecto("tok")
    assert len(rows) == 1
    assert rows[0]["trabajadas"] == 40.0          # NO 80.0
    assert rows[0]["vendidas"] == 30.0            # las dos filas de origen, sumadas


def test_proyecto_time_logs_only_project_with_valid_mapping_appears(monkeypatch):
    # Sin fila en "Horas por proyectos", pero con mapping valido en Proyectos.
    svc = _proy_patch(monkeypatch, n_filas_sharepoint=0,
                     time_logs_rows=[_tl("2024-05-02", 12), _tl("2026-03-10", 8)])
    fila = next((r for r in svc.get_cartera_proyecto("tok")
                 if r["proyecto"] == "Rollout Reporting"), None)
    assert fila is not None, "un proyecto solo-Time-Logs con mapping no puede desaparecer"
    assert fila["trabajadas"] == 20.0
    # Medidas sin fuente conservan su cero legitimo, nunca un valor inventado.
    assert fila["certificadas"] == 0.0 and fila["vendidas"] == 0.0 and fila["facturadas"] == 0.0
    assert fila["status"] == "En curso"           # dimension heredada, no fabricada


def test_proyecto_never_fabricates_dimensions_for_unmapped_time_logs(monkeypatch):
    svc = _proy_patch(monkeypatch, n_filas_sharepoint=0, proyectos=[],
                     time_logs_rows=[_tl("2024-05-02", 12), _tl("2026-03-10", 8)])
    assert svc.get_cartera_proyecto("tok") == []


def test_proyecto_and_mantenimiento_universes_do_not_overlap(monkeypatch):
    svc = _proy_patch(monkeypatch, tipo="Mantenimiento", time_logs_rows=_TL_VIDA)
    assert svc.get_cartera_proyecto("tok") == []
    svc2 = _proy_patch(monkeypatch, tipo="Proyecto", time_logs_rows=_TL_VIDA)
    assert svc2.get_cartera_mantenimiento("tok", anios=["2026"]) == []


def test_proyecto_reads_time_logs_once_never_per_project(monkeypatch):
    # Sin N+1: una sola lectura de Time Logs para toda la perspectiva, aunque
    # haya varios proyectos.
    _patch(
        monkeypatch,
        horas_por_proyectos=[
            {"ProyectoLookupId": f"p{i}", "EmpresaLookupId": "c1", "A_x00f1_o": "2026",
             "Mes": "Marzo", "HsTrabajadas": _PROY_DECOY, "HsVendidas": "10", "HsFacturadas": "5"}
            for i in range(1, 4)
        ],
        proyectos=[{"id": f"p{i}", "Title": f"Proyecto {i}", "TipodeProyecto": "Proyecto",
                    "Status": "En curso"} for i in range(1, 4)],
        clientes=[{"id": "c1", "Title": "Ternium"}],
        csv_files=[{"id": "current-1", "name": "Time Logs Reports.csv"}],
        file_rows={"current-1": [_tl("2026-03-10", 40, "Proyecto 1")]},
    )
    monkeypatch.setattr(ReportingRepository, "_facturadas_by_project", lambda self, site_id: {})
    repo = _repo()
    llamadas = {"n": 0}
    real = repo.get_time_logs_worked_hours

    def contando(*a, **kw):
        llamadas["n"] += 1
        return real(*a, **kw)

    repo.get_time_logs_worked_hours = contando
    rows = ReportingService(repo).get_cartera_proyecto("tok")
    assert len(rows) == 3
    assert llamadas["n"] == 1, llamadas          # NUNCA una por proyecto
    assert sum(r["trabajadas"] for r in rows) == 40.0


def test_proyecto_stays_lifetime_and_introduces_no_period_filters():
    # Contrato de ARCHIVO: grano project-level y cero filtros de periodo nuevos.
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    js = (root / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")
    cfg = js.split('"Proyecto": {', 1)[1].split("\n  },", 1)[0]
    assert "Hs Trabajadas Acum" in cfg
    # Anio/Mes de horas siguen siendo exclusivos de Mantenimiento.
    facet = js.split("FACET_CONFIG = {", 1)[1].split("rentabilidad: () =>", 1)[0]
    assert 'persp !== "Proyecto" || state.carteraAnioInicio.has(r.anioInicio)' in facet
    assert facet.count("carteraAnios") == 2 and facet.count("carteraMeses") == 2
    svc_src = (root / "webapp" / "services" / "reporting_service.py").read_text(encoding="utf-8")
    proy = svc_src.split("def _proyecto_rows(", 1)[1].split("# --- Batch B", 1)[0]
    fila = proy.split("rows.append({", 1)[1].split("})", 1)[0]
    assert '"anio"' not in fila and '"mes"' not in fila
    assert '"anioInicio"' in fila
    # Y el campo manual ya no alimenta la medida (solo puede quedar citado en
    # la prosa del docstring).
    codigo = "\n".join(l for l in proy.splitlines() if not l.strip().startswith("#"))
    codigo = codigo.split('"""', 2)[-1] if '"""' in codigo else codigo
    assert "hsTrabajadas" not in codigo
