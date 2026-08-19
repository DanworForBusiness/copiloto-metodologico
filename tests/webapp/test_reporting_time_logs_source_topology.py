"""
test_reporting_time_logs_source_topology.py — EXPLICIT SOURCE TOPOLOGY
corrective (owner decision 2026-08-19, "ROOT CAUSE CONFIRMED, AUTHORIZE
MINIMAL DISCOVERY CORRECTIVE").

`_list_time_logs_csv_items` dejó de ser un crawler recursivo genérico sobre
CUALQUIER subcarpeta de `Template Dashboard` — un diagnóstico real
(page-by-page timing, owner 2026-08-19) probó que esa recursión entraba a
subcarpetas NO relacionadas con Time Logs (la raíz real tiene ≥5
subcarpetas, al menos una con anidamiento propio), sumando ~55s de llamadas
Graph reales pero irrelevantes para esta fuente.

El contrato de origen ahora es EXPLÍCITO — 2 ubicaciones planas conocidas:

    Template Dashboard/                 → Time Logs Reports.csv (actual)
    Template Dashboard/Times previos/   → Time Logs Reports YYYY.csv (histórico)

Este archivo prueba, con un fixture que incluye carpetas no relacionadas
(algunas con su propio anidamiento), que el nuevo algoritmo:
  A. año actual: lista la raíz una vez, NUNCA lista "Times previos";
  B. año histórico: lista la raíz una vez + "Times previos" una vez,
     selecciona el CSV histórico correcto;
  C. NUNCA entra a ninguna carpeta no relacionada de la raíz;
  D. descubre correctamente los archivos de "Times previos" cuando hacen falta;
  E/F. NUNCA desciende a una subcarpeta anidada (ni de una carpeta no
     relacionada, ni dentro de "Times previos" mismo) — cero llamadas Graph
     a esos ids;
  G. el catálogo de años de Productivas sigue viendo 2021..2026 desde
     raíz + "Times previos" (sin recursión más profunda);
  H. el contrato hot/cold (source loading, file-discovery optimizado) sigue
     sin cambios de comportamiento observable.
"""
from __future__ import annotations

import csv
import io

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _COLAB, _USERINFO = "hp-list", "proy-list", "cli-list", "colab-list", "userinfo-list"
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]


def _csv_bytes(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    w.writeheader()
    for r in rows:
        w.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


# Fixture de topología REAL (owner 2026-08-19): la raíz tiene el CSV actual
# + "Times previos" + 4 carpetas NO relacionadas, una de las cuales
# ("Archive") tiene a su vez una subcarpeta anidada — exactamente el patrón
# que causó los ~55s reales. "Times previos" también tiene su propia
# carpeta anidada no relacionada, para probar E/F simétricamente.
_ROOT_CHILDREN = [
    {"id": "current-1", "name": "Time Logs Reports.csv"},
    {"id": "folder-times-previos", "name": "Times previos", "folder": {"childCount": 3}},
    {"id": "folder-other", "name": "OtherFolder", "folder": {"childCount": 0}},
    {"id": "folder-archive", "name": "Archive", "folder": {"childCount": 1}},
    {"id": "folder-images", "name": "Images", "folder": {"childCount": 0}},
    {"id": "folder-whatever", "name": "Whatever", "folder": {"childCount": 0}},
]
_TIMES_PREVIOS_CHILDREN = [
    {"id": "hist-2025", "name": "Time Logs Reports 2025.csv"},
    {"id": "hist-2024", "name": "Time Logs Reports 2024.csv"},
    {"id": "folder-nested-junk", "name": "NestedJunk", "folder": {"childCount": 1}},
]
_ARCHIVE_CHILDREN = [
    {"id": "folder-archive-nested", "name": "ReallyOld", "folder": {"childCount": 5}},
]

# IDs cuyo `/children` NUNCA debe pedirse bajo ningún escenario de este
# archivo — cualquier llamada a uno de estos es, por definición, una
# regresión a la recursión genérica vieja.
_FORBIDDEN_CHILDREN_IDS = {
    "folder-other", "folder-archive", "folder-images", "folder-whatever",
    "folder-archive-nested", "folder-nested-junk",
}


def _patch(monkeypatch, *, file_rows, extra_paged=None):
    calls: list[str] = []

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        calls.append(url)
        for forbidden_id in _FORBIDDEN_CHILDREN_IDS:
            assert f"/drive/items/{forbidden_id}/children" not in url, (
                f"REGRESIÓN a recursión genérica: se listó una carpeta no relacionada ({forbidden_id})"
            )
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return _ROOT_CHILDREN
        if "/drive/items/folder-times-previos/children" in url:
            return _TIMES_PREVIOS_CHILDREN
        if extra_paged:
            for pattern, result in extra_paged.items():
                if pattern in url:
                    return result
        if "/columns" in url:
            return []
        if _HP in url or _PROY in url or _CLI in url or _COLAB in url or _USERINFO in url:
            return []
        return []

    def fake_bytes(url, access_token=None, **kw):
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_bytes(rows)
        return _csv_bytes([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return calls


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def test_current_year_only_lists_root_once_never_times_previos(monkeypatch):
    # A + C: año actual ⇒ la raíz ya lo resuelve — "Times previos" NUNCA se
    # lista, y ninguna carpeta no relacionada tampoco (ver `_FORBIDDEN_
    # CHILDREN_IDS`, chequeado en cada llamada de `fake_paged`).
    calls = _patch(monkeypatch, file_rows={
        "current-1": [{"Date": "2026-08-05", "Project": "X", "Client": "Y",
                       "Task": "T", "Email": "a@d.com", "Duration Decimal": "5"}],
    })
    repo = _repo()
    repo._token = "tok"
    items = repo._list_time_logs_csv_items(_OPS_SITE, hot_years=["2026"])
    assert [it["name"] for it in items] == ["Time Logs Reports.csv"]
    root_listings = [c for c in calls if ":/children" in c and "/items/" not in c]
    times_previos_listings = [c for c in calls if "folder-times-previos/children" in c]
    assert len(root_listings) == 1
    assert times_previos_listings == []


def test_historical_year_lists_root_once_and_times_previos_once(monkeypatch):
    # B + D: año frío ⇒ se abre "Times previos" (una sola vez, sin
    # recursión adentro). `_list_time_logs_csv_items` devuelve el universo
    # CANDIDATO completo (raíz + Times previos) — el filtro al año
    # EXACTO pedido vive en `_download_time_logs_rows`, sin cambios acá
    # (MISMO contrato de siempre, ver ese método).
    calls = _patch(monkeypatch, file_rows={
        "hist-2025": [{"Date": "2025-07-10", "Project": "X", "Client": "Y",
                       "Task": "T", "Email": "a@d.com", "Duration Decimal": "80"}],
    })
    repo = _repo()
    repo._token = "tok"
    items = repo._list_time_logs_csv_items(_OPS_SITE, hot_years=["2025"])
    names = {it["name"] for it in items}
    assert "Time Logs Reports 2025.csv" in names
    root_listings = [c for c in calls if ":/children" in c and "/items/" not in c]
    times_previos_listings = [c for c in calls if "folder-times-previos/children" in c]
    assert len(root_listings) == 1
    assert len(times_previos_listings) == 1


def test_unrelated_root_folders_never_produce_a_single_graph_call(monkeypatch):
    # C + E: OtherFolder/Archive/Images/Whatever nunca generan NINGUNA
    # llamada — ni siquiera para descartarlas (la aserción vive dentro de
    # `fake_paged` en `_patch`, así que cualquier violación hace fallar
    # el test inmediatamente, no solo al final).
    _patch(monkeypatch, file_rows={
        "hist-2024": [{"Date": "2024-05-20", "Project": "X", "Client": "Y",
                       "Task": "T", "Email": "a@d.com", "Duration Decimal": "2"}],
    })
    repo = _repo()
    repo._token = "tok"
    repo._list_time_logs_csv_items(_OPS_SITE, hot_years=["2024"])  # no debe lanzar AssertionError


def test_nested_folder_inside_times_previos_is_never_descended(monkeypatch):
    # F: "NestedJunk" (dentro de "Times previos") tampoco se recorre — el
    # algoritmo lista SOLO los children directos de "Times previos", nunca
    # desciende un nivel más.
    _patch(monkeypatch, file_rows={
        "hist-2025": [{"Date": "2025-07-10", "Project": "X", "Client": "Y",
                       "Task": "T", "Email": "a@d.com", "Duration Decimal": "80"}],
    })
    repo = _repo()
    repo._token = "tok"
    items = repo._list_time_logs_csv_items(_OPS_SITE, hot_years=["2025", "2024"])
    names = {it["name"] for it in items}
    assert names == {"Time Logs Reports.csv", "Time Logs Reports 2025.csv", "Time Logs Reports 2024.csv"}
    assert "NestedJunk" not in names


def test_years_available_catalog_sees_full_range_without_deep_recursion(monkeypatch):
    # G: el catálogo de Productivas (raíz + "Times previos" únicamente)
    # sigue viendo 2021..2026 con el fixture completo — MISMO catálogo que
    # antes de este corrective, ahora sin la recursión genérica.
    root_with_full_history = _ROOT_CHILDREN
    times_previos_full = _TIMES_PREVIOS_CHILDREN + [
        {"id": "hist-2023", "name": "Time Logs Reports 2023.csv"},
        {"id": "hist-2022", "name": "Time Logs Reports 2022.csv"},
        {"id": "hist-2021", "name": "Time Logs Reports 2021.csv"},
    ]

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        for forbidden_id in _FORBIDDEN_CHILDREN_IDS:
            assert f"/drive/items/{forbidden_id}/children" not in url
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return root_with_full_history
        if "/drive/items/folder-times-previos/children" in url:
            return times_previos_full
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    repo = _repo()
    years = repo.get_time_logs_years_available("tok")
    assert years == ["2021", "2022", "2023", "2024", "2025", "2026"]


def test_hot_cold_source_loading_contract_unchanged(monkeypatch):
    # H: el contrato de source-loading (seleccionar archivos ANTES de
    # descargar, en `_download_time_logs_rows` — `_list_time_logs_csv_
    # items` sigue devolviendo el universo CANDIDATO sin cambios, nunca
    # filtrado al año exacto) sigue funcionando igual con la topología
    # explícita — pedir un año sin archivo correspondiente sigue siendo un
    # dataset vacío legítimo, nunca un error.
    _patch(monkeypatch, file_rows={})
    repo = _repo()
    repo._token = "tok"
    rows = repo._download_time_logs_rows(_OPS_SITE, hot_years=["2099"])
    assert rows == []
