"""
test_reporting_cartera_rentabilidad_worked_hours_sources.py — RECONCILIACIÓN
Cartera↔Rentabilidad "Horas Trabajadas" (owner decision 2026-08-18, "OWNER
DECISION — CONTINUE HOTFIX").

HISTORIA (contexto, no una regla eterna): el incidente original (owner
report — "Cartera/Proyectos deja de mostrar Horas Trabajadas desde ~Julio
2026; Rentabilidad Agosto 2026 muestra ~2.682 hs, Cartera no muestra ese
mismo volumen para el mismo período") fue auditado contra evidencia PBIP
PRIMARIA (TMDL/DAX citado textual, ver real-data-lineage.md/HANDOFF.md) y
confirmó que eran dos medidas DELIBERADAMENTE distintas del modelo PBIP
original — Cartera leía `Horas por proyectos[HsTrabajadas]` (campo MANUAL de
SharePoint); Rentabilidad, `SUM('Time Logs Reports'[HORAS])` — coincidentes
solo en el rótulo visible "Horas Trabajadas". Esa fidelidad al PBIP quedó
demostrada (Fase de review anterior de este mismo incidente): NO era un
defecto de código reconciliable contra el modelo original.

NUEVA DECISIÓN DE PRODUCTO (owner, 2026-08-18 — no una corrección de bug):
para el mismo contexto Año/Mes/filtros, "Cartera / Horas Trabajadas" DEBE
reconciliar con "Rentabilidad / Horas Trabajadas". Ambas ahora derivan del
MISMO canonical Time Logs facts (`_trab_by_proyecto_by_year`, MISMO join
`_norm_key(proyecto)`×año×mes) — ver `ReportingService._mantenimiento_rows`/
`get_cartera_mantenimiento`. El campo SharePoint `HsTrabajadas` sigue
existiendo (todavía consumido por `_proyecto_rows`, perspectiva Proyecto —
fuera de alcance de esta decisión) pero YA NO alimenta "trabajadas" de
Mantenimiento.

Este archivo YA NO bloquea la divergencia (la versión anterior aseguraba
`agosto["trabajadas"] != agosto_trab_rentabilidad` como contrato deliberado)
— ahora bloquea la RECONCILIACIÓN: mismo proyecto/año/mes/filtros ⇒ MISMO
número en ambas páginas. Si algún día alguien reintroduce una fuente
distinta para uno de los dos lados, este test lo marca como regresión.
Cubre: Agosto 2026, Julio 2026, un mes histórico (2025, ejercitando el
scope `anios=` de ambos lados), filtro por Proyecto y filtro por Cliente.
Los valores del fixture (2682/150/80/500) reproducen la ESCALA del reporte
real del owner, sin hardcodear esos números como regla de producción — son
datos de fixture, no literales del código bajo prueba.
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

# Decoy deliberado: si algún camino todavía leyera `HsTrabajadas` en vez de
# Time Logs, este valor (bien distinto de 2682/150/80/500) lo delataría.
_DECOY_HSTRABAJADAS = "999"


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _patch(monkeypatch, *, horas_por_proyectos, proyectos, clientes, csv_files, file_rows):
    """`csv_files`/`file_rows`: MISMA estructura que test_reporting_batch_b.py
    — necesario acá porque el mes histórico (2025) requiere `anios=["2025"]`
    ⇒ SOLO el archivo 2025 se descarga (HOT WINDOW/COLD HISTORY corrective,
    source loading por nombre de archivo, ver reporting_repository.py)."""
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


def _full_fixture(monkeypatch):
    """2 proyectos Mantenimiento (Soporte Danone/Danone, Implementación
    YPF/YPF) × 2026 (Julio+Agosto, archivo actual) + 1 mes histórico 2025
    (Marzo, archivo `Times previos`-style separado) — cubre reconciliación
    por mes, por proyecto y por cliente en una sola fuente compartida."""
    _patch(
        monkeypatch,
        horas_por_proyectos=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio",
             "HsTrabajadas": _DECOY_HSTRABAJADAS, "HsVendidas": "160", "HsFacturadas": "140"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
             "HsTrabajadas": _DECOY_HSTRABAJADAS, "HsVendidas": "160", "HsFacturadas": "140"},
            {"ProyectoLookupId": "p2", "EmpresaLookupId": "c2", "A_x00f1_o": "2026", "Mes": "Agosto",
             "HsTrabajadas": _DECOY_HSTRABAJADAS, "HsVendidas": "520", "HsFacturadas": "500"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2025", "Mes": "Marzo",
             "HsTrabajadas": _DECOY_HSTRABAJADAS, "HsVendidas": "90", "HsFacturadas": "80"},
        ],
        proyectos=[
            {"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"},
            {"id": "p2", "Title": "Implementación YPF", "TipodeProyecto": "Mantenimiento", "Status": "En curso"},
        ],
        clientes=[{"id": "c1", "Title": "Danone"}, {"id": "c2", "Title": "YPF"}],
        csv_files=[
            {"id": "csv-2026", "name": "Time Logs Reports.csv"},
            {"id": "csv-2025", "name": "Time Logs Reports 2025.csv"},
        ],
        file_rows={
            "csv-2026": [
                {"Date": "2026-07-10", "Client": "Danone", "Project": "Soporte Danone",
                 "Task": "Soporte", "Email": "a@danwor.com", "Duration Decimal": "150"},
                {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                 "Task": "Soporte", "Email": "a@danwor.com", "Duration Decimal": "2682"},
                {"Date": "2026-08-12", "Client": "YPF", "Project": "Implementación YPF",
                 "Task": "Dev", "Email": "b@danwor.com", "Duration Decimal": "500"},
            ],
            "csv-2025": [
                {"Date": "2025-03-15", "Client": "Danone", "Project": "Soporte Danone",
                 "Task": "Soporte", "Email": "a@danwor.com", "Duration Decimal": "80"},
            ],
        },
    )


def _cartera_row(cartera_rows, proyecto, mes):
    return next(r for r in cartera_rows if r["proyecto"] == proyecto and r["mes"] == mes)


def _rentabilidad_trab(rentabilidad, proyecto, anio, mes_idx):
    row = next(p for p in rentabilidad["proyectos"] if p["proyecto"] == proyecto)
    return row["trabByYear"][anio][mes_idx]


def test_cartera_and_rentabilidad_reconcile_agosto_2026(monkeypatch):
    _full_fixture(monkeypatch)
    service = ReportingService(_repo())
    cartera_rows = service.get_cartera_mantenimiento("tok", anios=["2026"])
    rentabilidad = service.get_rentabilidad("tok", anios=["2026"])

    agosto = _cartera_row(cartera_rows, "Soporte Danone", "8")
    agosto_rent = _rentabilidad_trab(rentabilidad, "Soporte Danone", "2026", 7)  # índice 7 = Agosto
    assert agosto["trabajadas"] == 2682.0
    assert agosto["trabajadas"] == agosto_rent
    assert agosto["trabajadas"] != float(_DECOY_HSTRABAJADAS)


def test_cartera_and_rentabilidad_reconcile_julio_2026(monkeypatch):
    _full_fixture(monkeypatch)
    service = ReportingService(_repo())
    cartera_rows = service.get_cartera_mantenimiento("tok", anios=["2026"])
    rentabilidad = service.get_rentabilidad("tok", anios=["2026"])

    julio = _cartera_row(cartera_rows, "Soporte Danone", "7")
    julio_rent = _rentabilidad_trab(rentabilidad, "Soporte Danone", "2026", 6)  # índice 6 = Julio
    assert julio["trabajadas"] == 150.0
    assert julio["trabajadas"] == julio_rent


def test_cartera_and_rentabilidad_reconcile_historical_month_2025(monkeypatch):
    # Mes histórico — ejercita el scope `anios=["2025"]` en AMBOS lados
    # (Cartera vía `hot_years`, Rentabilidad vía su propio `anios`): el
    # HOT WINDOW/COLD HISTORY corrective de Time Logs (source loading por
    # archivo) no debe romper la reconciliación fuera de la ventana caliente
    # por defecto.
    _full_fixture(monkeypatch)
    service = ReportingService(_repo())
    cartera_rows = service.get_cartera_mantenimiento("tok", anios=["2025"])
    rentabilidad = service.get_rentabilidad("tok", anios=["2025"])

    marzo = _cartera_row(cartera_rows, "Soporte Danone", "3")
    marzo_rent = _rentabilidad_trab(rentabilidad, "Soporte Danone", "2025", 2)  # índice 2 = Marzo
    assert marzo["trabajadas"] == 80.0
    assert marzo["trabajadas"] == marzo_rent


def test_cartera_and_rentabilidad_reconcile_per_proyecto_filter(monkeypatch):
    # Filtro por Proyecto: los DOS proyectos de Agosto 2026 reconcilian
    # INDEPENDIENTEMENTE — la agregación es por proyecto×año×mes, nunca un
    # escalar de portfolio que mezclaría ambos.
    _full_fixture(monkeypatch)
    service = ReportingService(_repo())
    cartera_rows = service.get_cartera_mantenimiento("tok", anios=["2026"])
    rentabilidad = service.get_rentabilidad("tok", anios=["2026"])

    danone = _cartera_row(cartera_rows, "Soporte Danone", "8")
    ypf = _cartera_row(cartera_rows, "Implementación YPF", "8")
    assert danone["trabajadas"] == 2682.0
    assert ypf["trabajadas"] == 500.0
    assert danone["trabajadas"] == _rentabilidad_trab(rentabilidad, "Soporte Danone", "2026", 7)
    assert ypf["trabajadas"] == _rentabilidad_trab(rentabilidad, "Implementación YPF", "2026", 7)


def test_cartera_and_rentabilidad_reconcile_per_cliente_filter(monkeypatch):
    # Filtro por Cliente: Cartera/Mantenimiento y Rentabilidad exponen
    # `cliente` en cada fila — filtrar por YPF aísla el mismo proyecto en
    # ambas páginas y sigue reconciliando.
    _full_fixture(monkeypatch)
    service = ReportingService(_repo())
    cartera_rows = [r for r in service.get_cartera_mantenimiento("tok", anios=["2026"]) if r["cliente"] == "YPF"]
    rentabilidad_rows = [p for p in service.get_rentabilidad("tok", anios=["2026"])["proyectos"] if p["cliente"] == "YPF"]

    assert len(cartera_rows) == 1 and len(rentabilidad_rows) == 1
    agosto = _cartera_row(cartera_rows, "Implementación YPF", "8")
    assert agosto["trabajadas"] == 500.0
    assert agosto["trabajadas"] == rentabilidad_rows[0]["trabByYear"]["2026"][7]
