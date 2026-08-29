"""
test_reporting_facturadas_cache.py — CACHE VERSIONED HISTORICAL TIME LOGS
corrective, Fase 3 (Reporting Cartera/Proyecto FACTS REMOTE-COST
REDUCTION, owner ticket, ver HANDOFF). Cubre la ÚNICA fuente clasificada
VERSION-CACHE SAFE en esta fase: Facturadas XLSX (`_facturadas_by_project_
cached`, ver reporting_repository.py §2 del audit).

- call-budget: metadata SIEMPRE (1 GET barato, necesario para saber si hay
  HIT), download+parse del XLSX SOLO en un MISS;
- equivalencia de negocio frío == tibio;
- invalidación real por cambio de eTag;
- fail-soft si la metadata falla (nunca rompe, cae a "siempre fresco");
- aislamiento de autorización (scope A no filtra hacia scope B);
- `cache_scope=None` preserva el comportamiento EXACTO de siempre — CERO
  llamada de metadata extra, CERO caching (Mantenimiento/combo, que NUNCA
  pasan `cache_scope`, quedan sin tocar);
- `include_facturadas=False` (Mantenimiento) sigue sin tocar la XLSX en
  absoluto, con o sin `cache_scope`.

MISMO patrón de fixtures/mocking que test_reporting_cartera.py
(`_facturadas_xlsx`) y test_reporting_time_logs_historical_cache.py
(cache aislada vía monkeypatch del singleton de módulo).
"""
from __future__ import annotations

import io

import openpyxl
from openpyxl.worksheet.table import Table

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingGraphError, ReportingRepository
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

_SITE = "site-1"
_HP, _PROY, _CLI, _CP = "hp-list", "proy-list", "cli-list", "cp-list"
_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]
_XLSX_PATH_SUFFIX = "content"  # el path real vive en FACTURADAS_XLSX_PATH, no se hardcodea acá


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _facturadas_xlsx(pairs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facturado"
    ws.append(["Proyecto Base", "Suma de Cantidad"])
    for proyecto_base, cantidad in pairs:
        ws.append([proyecto_base, cantidad])
    ws.add_table(Table(displayName="Tabla24", ref=f"A1:B{1 + len(pairs)}"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _CallLog:
    def __init__(self):
        self.json = []
        self.bytes = []
        self.paged = []


def _patch(monkeypatch, *, xlsx_bytes, metadata=None, metadata_error=None):
    """`metadata`: dict devuelto por el GET de metadata (sin `:content`) —
    default `{"id": "fac-1", "eTag": "etag-v1", "lastModifiedDateTime":
    "2026-01-01T00:00:00Z", "size": 2048}`. `metadata_error`: si se pasa,
    el GET de metadata levanta esa excepción (simula falla de transporte,
    ver test de fail-soft)."""
    log = _CallLog()
    metadata = metadata if metadata is not None else {
        "id": "fac-1", "eTag": "etag-v1", "lastModifiedDateTime": "2026-01-01T00:00:00Z", "size": 2048,
    }

    def fake_json(url, access_token=None, **kw):
        log.json.append(url)
        if metadata_error is not None:
            raise metadata_error
        return metadata

    def fake_paged(url, access_token=None, **kw):
        log.paged.append(url)
        if "/columns" in url:
            return _CP_COLUMNS
        return []

    def fake_bytes(url, access_token=None, **kw):
        log.bytes.append(url)
        return xlsx_bytes

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return log


def _repo_with_token():
    repo = ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )
    repo._token = "tok"
    return repo


# ============================================================
# CALL BUDGET — metadata siempre, download+parse solo en MISS
# ============================================================

def test_cold_pays_metadata_plus_download_warm_pays_only_metadata(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    log = _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]))
    repo = _repo_with_token()

    by_key1, outcome1 = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert outcome1 == "computed"
    assert len(log.json) == 1     # 1 metadata GET
    assert len(log.bytes) == 1    # 1 download real (MISS)

    by_key2, outcome2 = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert outcome2 == "hit"
    assert len(log.json) == 2     # metadata SIEMPRE (necesaria para saber si hay HIT)
    assert len(log.bytes) == 1    # CERO download extra — vino de cache

    assert by_key1 == by_key2 == {"SOPORTEDANONE": 290.0}


def test_cache_scope_none_never_calls_metadata_never_caches(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    log = _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]))
    repo = _repo_with_token()

    repo._facturadas_by_project_cached(_SITE, cache_scope=None)
    repo._facturadas_by_project_cached(_SITE, cache_scope=None)

    assert log.json == []          # CERO llamadas de metadata — comportamiento de siempre
    assert len(log.bytes) == 2     # descarga fresca CADA VEZ, MISMO contrato que antes de Fase 3
    assert len(fresh_cache) == 0


def test_include_facturadas_false_never_touches_xlsx_with_or_without_cache_scope(monkeypatch):
    log = _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("x", 0)]))
    repo = _repo_with_token()
    facts, _dim = repo.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, cache_scope="user-a@danwor.com",
    )
    assert log.json == []
    assert log.bytes == []


# ============================================================
# EQUIVALENCIA DE NEGOCIO — frío == tibio, invalidación real
# ============================================================

def test_cold_and_warm_results_are_business_identical(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290), ("Costeo Federal", 120)]))
    repo = _repo_with_token()

    cold, _ = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    warm, _ = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert cold == warm == {"SOPORTEDANONE": 290.0, "COSTEOFEDERAL": 120.0}


def test_etag_change_invalidates_and_new_data_appears(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    metadata_v1 = {"id": "fac-1", "eTag": "etag-v1", "lastModifiedDateTime": "2026-01-01T00:00:00Z", "size": 2048}
    _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]), metadata=metadata_v1)
    repo = _repo_with_token()

    first, outcome1 = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert first["SOPORTEDANONE"] == 290.0
    assert outcome1 == "computed"

    metadata_v2 = {"id": "fac-1", "eTag": "etag-v2", "lastModifiedDateTime": "2026-02-01T00:00:00Z", "size": 2100}
    _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 999)]), metadata=metadata_v2)
    second, outcome2 = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert second["SOPORTEDANONE"] == 999.0, "eTag cambió pero la cache siguió sirviendo el valor viejo"
    assert outcome2 == "computed"  # nueva key ⇒ nunca puede ser hit


def test_same_etag_keeps_serving_cached_value_even_if_source_bytes_changed(monkeypatch):
    """MISMO contrato de eTag-trust que Fase 2 (Time Logs) — documentado
    explícitamente, no oculto."""
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    metadata = {"id": "fac-1", "eTag": "etag-v1", "lastModifiedDateTime": "2026-01-01T00:00:00Z", "size": 2048}
    _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]), metadata=metadata)
    repo = _repo_with_token()
    repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")

    _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 999)]), metadata=metadata)  # mismo eTag
    second, outcome = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert second["SOPORTEDANONE"] == 290.0  # sigue sirviendo el valor cacheado
    assert outcome == "hit"


# ============================================================
# FAIL-SOFT — falla de metadata nunca rompe el fetch
# ============================================================

def test_metadata_failure_falls_back_to_always_fresh_never_breaks(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    log = _patch(
        monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]),
        metadata_error=ReportingGraphError(stage="facturadas_metadata", status_code=500),
    )
    repo = _repo_with_token()

    by_key, outcome = repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert by_key == {"SOPORTEDANONE": 290.0}  # el fetch SIGUE funcionando
    assert outcome == "n/a"
    assert len(log.bytes) == 1  # cayó al camino sin cache — descarga directa
    assert len(fresh_cache) == 0  # nunca escribió una entrada con versión inventada


# ============================================================
# AISLAMIENTO DE AUTORIZACIÓN
# ============================================================

def test_scope_b_never_inherits_scope_a_cache_entries(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    log = _patch(monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]))
    repo = _repo_with_token()

    repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert len(log.bytes) == 1

    repo._facturadas_by_project_cached(_SITE, cache_scope="user-a@danwor.com")
    assert len(log.bytes) == 1  # A tibio — sin nueva descarga

    repo._facturadas_by_project_cached(_SITE, cache_scope="user-b@danwor.com")
    assert len(log.bytes) == 2, "scope B heredó la entrada de scope A — fuga de datos cross-user"


# ============================================================
# End-to-end vía get_cartera_facts_with_project_dimension real
# ============================================================

def test_end_to_end_facts_call_uses_cached_facturadas_on_warm_request(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_FACTURADAS_CACHE", fresh_cache)
    log = _patch(
        monkeypatch, xlsx_bytes=_facturadas_xlsx([("Soporte Danone", 290)]),
    )
    repo = _repo_with_token()
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", lambda url, access_token=None, **kw: (
        _CP_COLUMNS if "/columns" in url else
        _items([{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Proyecto", "Status": "En curso"}])
        if _PROY in url else
        _items([{"id": "c1", "Title": "Danone"}]) if _CLI in url else
        _items([{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
                  "HsTrabajadas": "1", "HsVendidas": "1", "HsFacturadas": "1"}]) if _HP in url else []
    ))

    facts1, _dim1 = repo.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=True, cache_scope="user-a@danwor.com",
    )
    facts2, _dim2 = repo.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=True, cache_scope="user-a@danwor.com",
    )
    assert facts1[0]["facturadasProyecto"] == facts2[0]["facturadasProyecto"] == 290.0
    assert len(log.bytes) == 1  # UN SOLO download del XLSX en las 2 llamadas
