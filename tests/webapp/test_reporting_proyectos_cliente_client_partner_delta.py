"""
test_reporting_proyectos_cliente_client_partner_delta.py — DELTA-SYNC
corrective, Fase 3B (Reporting Cartera/Proyecto, owner ticket, ver
HANDOFF). Extiende el mecanismo delta ya probado por Horas (Fase 3A,
ver test_reporting_horas_delta_sync.py) a Proyectos/Cliente/Client
Partner.

El primitivo de transporte/estado (`DeltaCache`/`_delta_apply_pages`/
`_delta_cold_sync`/`_delta_warm_sync`/`_delta_sync_list`) es COMPARTIDO y
GENÉRICO — ya tiene su matriz completa (cold/paginado/warm-sin-cambios/
update/insert/delete/duplicado/410/paginado-fallido/aislamiento) probada
por Horas. Este archivo NO repite esa matriz por fuente (ver HANDOFF §10:
"Do not duplicate every low-level DeltaCache unit test") — cada fuente
recibe cobertura de INTEGRACIÓN/EQUIVALENCIA: cold, warm sin cambios,
insert+update+delete combinados, resync tras 410, un sync fallido no muta
el estado previo, aislamiento de scope, y equivalencia de negocio contra
`get_cartera_facts_with_project_dimension`.

Client Partner es un caso aparte: su lista TARGET se descubre
dinámicamente vía `/columns` — ese descubrimiento nunca se cachea (sigue
siendo un GET de metadata liviano en cada llamada), pero la LECTURA de
items del target sí usa delta, con la key de cache basada en el id
EFECTIVO/resuelto (nunca un id adivinado)."""
from __future__ import annotations

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingGraphError, ReportingRepository

_SITE = "site-1"
_HP, _PROY, _CLI, _CP = "hp-list", "proy-list", "cli-list", "cp-list"
_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]


@pytest.fixture(autouse=True)
def _isolate_delta_cache(monkeypatch):
    monkeypatch.setattr(repo_mod, "_DELTA_CACHE", DeltaCache())


def _repo():
    repo = ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )
    repo._token = "tok"
    return repo


class _FakeDeltaServer:
    """MISMO helper que test_reporting_horas_delta_sync.py — runs de
    páginas pre-programadas por URL exacta, con soporte para simular
    fallas de transporte (410/genéricas) en una URL puntual."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.raise_on: dict[str, Exception] = {}
        self.calls: list[str] = []
        self.paged_calls: list[str] = []
        self.paged_response = None

    def add_page(self, url, *, items=None, next_link=None, delta_link=None):
        payload = {"value": items or []}
        if next_link:
            payload["@odata.nextLink"] = next_link
        if delta_link:
            payload["@odata.deltaLink"] = delta_link
        self.responses[url] = payload
        return url

    def raise_at(self, url, exc):
        self.raise_on[url] = exc

    def install(self, monkeypatch, *, paged_response=None):
        self.paged_response = paged_response if paged_response is not None else _CP_COLUMNS

        def fake_json(url, access_token=None, **kw):
            self.calls.append(url)
            if url in self.raise_on:
                raise self.raise_on[url]
            if url not in self.responses:
                raise AssertionError(f"URL de delta no programada en el fixture: {url}")
            return self.responses[url]

        def fake_paged(url, access_token=None, **kw):
            self.paged_calls.append(url)
            if "/columns" in url:
                return self.paged_response
            raise AssertionError(f"full-list fetch inesperado con cache_scope activo: {url}")

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)


def _proy_url(select=None):
    return repo_mod._list_items_delta_url(_SITE, _PROY, select if select is not None else repo_mod.PROYECTOS_SELECT_FIELDS)


def _cli_url(select=None):
    return repo_mod._list_items_delta_url(_SITE, _CLI, select if select is not None else repo_mod.CLIENTE_SELECT_FIELDS)


def _cp_url():
    return repo_mod._list_items_delta_url(_SITE, _CP, None)


def _proy_row(item_id, *, title="Soporte Danone", tipo="Proyecto", estado="En curso"):
    return {"id": item_id, "fields": {
        "Title": title, "TipodeProyecto": tipo, "Status": estado,
        "FechadeInicio": "2026-01-15", "ID_x0020_Proyecto_x0020_Danwor": "DAN-001",
    }}


def _cli_row(item_id, *, title="Danone"):
    return {"id": item_id, "fields": {"Title": title, "ClientPartnerLookupId": "pm1"}}


def _cp_row(item_id, *, title="Diego Ramos"):
    return {"id": item_id, "fields": {"Title": title}}


def _removed(item_id):
    return {"id": item_id, "@removed": {"reason": "deleted"}}


# ============================================================
# PROYECTOS
# ============================================================

def test_proyectos_cold_full_state_deltalink_stored(monkeypatch):
    server = _FakeDeltaServer()
    url = _proy_url()
    server.add_page(url, items=[_proy_row("p1"), _proy_row("p2")], delta_link=f"{url}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    items = repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"p1", "p2"}
    assert server.calls == [url]
    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _PROY)]
    assert state["delta_link"] == f"{url}&dl=v1"


def test_proyectos_warm_zero_changes_only_checkpoint_call(monkeypatch):
    server = _FakeDeltaServer()
    url = _proy_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_proy_row("p1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.add_page(delta1, items=[], delta_link=f"{url}&dl=v2")
    server.calls.clear()
    items = repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]
    assert {it["id"] for it in items} == {"p1"}


def test_proyectos_insert_update_delete_all_reflected(monkeypatch):
    server = _FakeDeltaServer()
    url = _proy_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_proy_row("p1", title="A"), _proy_row("p2", title="B")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.add_page(delta1, items=[
        _proy_row("p1", title="A-updated"),  # update
        _proy_row("p3", title="C"),          # insert
        _removed("p2"),                       # delete
    ], delta_link=f"{url}&dl=v2")
    items = repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    by_id = {it["id"]: it for it in items}
    assert set(by_id) == {"p1", "p3"}
    assert by_id["p1"]["fields"]["Title"] == "A-updated"
    assert by_id["p3"]["fields"]["Title"] == "C"


def test_proyectos_410_discards_state_does_one_resync(monkeypatch):
    server = _FakeDeltaServer()
    url = _proy_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_proy_row("p1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_at(delta1, ReportingGraphError(stage="proyectos_delta", status_code=410))
    resync_delta = f"{url}&dl=v-resync"
    server.add_page(url, items=[_proy_row("p9")], delta_link=resync_delta)

    server.calls.clear()
    items = repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"p9"}
    assert server.calls == [delta1, url]


def test_proyectos_failed_paged_sync_does_not_mutate_previous_state(monkeypatch):
    server = _FakeDeltaServer()
    url = _proy_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_proy_row("p1", title="original")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    page2 = f"{url}&warm-page=2"
    server.add_page(delta1, items=[_proy_row("p1", title="mutated")], next_link=page2)
    server.raise_at(page2, ReportingGraphError(stage="proyectos_delta", status_code=503))
    with pytest.raises(ReportingGraphError):
        repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _PROY)]
    assert state["rows"]["p1"]["fields"]["Title"] == "original"
    assert state["delta_link"] == delta1


def test_proyectos_scope_isolation(monkeypatch):
    server = _FakeDeltaServer()
    url = _proy_url()
    server.add_page(url, items=[_proy_row("p1")], delta_link=f"{url}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    server.calls.clear()
    repo._fetch_proyectos_delta(_SITE, cache_scope="user-b@danwor.com")
    assert server.calls == [url], "scope B debió pagar su propio cold sync"


# ============================================================
# CLIENTE
# ============================================================

def test_cliente_cold_full_state_deltalink_stored(monkeypatch):
    server = _FakeDeltaServer()
    url = _cli_url()
    server.add_page(url, items=[_cli_row("c1"), _cli_row("c2")], delta_link=f"{url}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    items = repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"c1", "c2"}
    assert server.calls == [url]


def test_cliente_warm_zero_changes_only_checkpoint_call(monkeypatch):
    server = _FakeDeltaServer()
    url = _cli_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cli_row("c1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")

    server.add_page(delta1, items=[], delta_link=f"{url}&dl=v2")
    server.calls.clear()
    items = repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]
    assert {it["id"] for it in items} == {"c1"}


def test_cliente_insert_update_delete_all_reflected(monkeypatch):
    server = _FakeDeltaServer()
    url = _cli_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cli_row("c1", title="A"), _cli_row("c2", title="B")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")

    server.add_page(delta1, items=[
        _cli_row("c1", title="A-updated"), _cli_row("c3", title="C"), _removed("c2"),
    ], delta_link=f"{url}&dl=v2")
    items = repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")
    by_id = {it["id"]: it for it in items}
    assert set(by_id) == {"c1", "c3"}
    assert by_id["c1"]["fields"]["Title"] == "A-updated"


def test_cliente_410_discards_state_does_one_resync(monkeypatch):
    server = _FakeDeltaServer()
    url = _cli_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cli_row("c1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_at(delta1, ReportingGraphError(stage="cliente_delta", status_code=410))
    resync_delta = f"{url}&dl=v-resync"
    server.add_page(url, items=[_cli_row("c9")], delta_link=resync_delta)
    server.calls.clear()
    items = repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"c9"}
    assert server.calls == [delta1, url]


def test_cliente_failed_paged_sync_does_not_mutate_previous_state(monkeypatch):
    server = _FakeDeltaServer()
    url = _cli_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cli_row("c1", title="original")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")

    page2 = f"{url}&warm-page=2"
    server.add_page(delta1, items=[_cli_row("c1", title="mutated")], next_link=page2)
    server.raise_at(page2, ReportingGraphError(stage="cliente_delta", status_code=503))
    with pytest.raises(ReportingGraphError):
        repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")

    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _CLI)]
    assert state["rows"]["c1"]["fields"]["Title"] == "original"


def test_cliente_scope_isolation(monkeypatch):
    server = _FakeDeltaServer()
    url = _cli_url()
    server.add_page(url, items=[_cli_row("c1")], delta_link=f"{url}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")
    server.calls.clear()
    repo._fetch_cliente_delta(_SITE, cache_scope="user-b@danwor.com")
    assert server.calls == [url]


# ============================================================
# CLIENT PARTNER — target descubierto dinámicamente
# ============================================================

def test_client_partner_cold_uses_discovered_target_as_delta_list_id(monkeypatch):
    server = _FakeDeltaServer()
    url = _cp_url()
    server.add_page(url, items=[_cp_row("pm1")], delta_link=f"{url}&dl=v1")
    server.install(monkeypatch)  # paged_response default = _CP_COLUMNS (descubre target=_CP)
    repo = _repo()

    cp_by_id = repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    assert cp_by_id == {"pm1": "Diego Ramos"}
    assert server.calls == [url]
    assert server.paged_calls == [f"{repo_mod.GRAPH_BASE}/sites/{_SITE}/lists/{_CLI}/columns"]
    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _CP)]
    assert state["delta_link"] == f"{url}&dl=v1"


def test_client_partner_discovery_never_cached_always_fresh(monkeypatch):
    """El `/columns` de descubrimiento corre en CADA llamada — nunca se
    cachea (preserva el discovery contract tal cual, ver HANDOFF §6) — a
    diferencia de la LECTURA de items del target, que sí es delta/tibia."""
    server = _FakeDeltaServer()
    url = _cp_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cp_row("pm1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()

    repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    server.add_page(delta1, items=[], delta_link=f"{url}&dl=v2")
    server.calls.clear()
    server.paged_calls.clear()
    repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]  # solo el checkpoint delta — sin re-enumeración
    assert len(server.paged_calls) == 1  # pero el descubrimiento SÍ vuelve a correr


def test_client_partner_insert_update_delete_all_reflected(monkeypatch):
    server = _FakeDeltaServer()
    url = _cp_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cp_row("pm1", title="A"), _cp_row("pm2", title="B")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")

    server.add_page(delta1, items=[
        _cp_row("pm1", title="A-updated"), _cp_row("pm3", title="C"), _removed("pm2"),
    ], delta_link=f"{url}&dl=v2")
    cp_by_id = repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    assert cp_by_id == {"pm1": "A-updated", "pm3": "C"}


def test_client_partner_410_discards_state_does_one_resync(monkeypatch):
    server = _FakeDeltaServer()
    url = _cp_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_cp_row("pm1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")

    server.raise_at(delta1, ReportingGraphError(stage="client_partner_delta", status_code=410))
    resync_delta = f"{url}&dl=v-resync"
    server.add_page(url, items=[_cp_row("pm9")], delta_link=resync_delta)
    server.calls.clear()
    cp_by_id = repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    assert cp_by_id == {"pm9": "Diego Ramos"}
    assert server.calls == [delta1, url]


def test_client_partner_no_target_discoverable_returns_empty_never_caches(monkeypatch):
    server = _FakeDeltaServer()
    server.install(monkeypatch, paged_response=[{"name": "Title"}])  # sin columna lookup Client Partner
    repo = _repo()
    cp_by_id = repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    assert cp_by_id == {}
    assert server.calls == []  # nunca intenta delta sin target


def test_client_partner_scope_isolation(monkeypatch):
    server = _FakeDeltaServer()
    url = _cp_url()
    server.add_page(url, items=[_cp_row("pm1")], delta_link=f"{url}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    server.calls.clear()
    repo._resolve_client_partners(_SITE, cache_scope="user-b@danwor.com")
    assert server.calls == [url]


# ============================================================
# EQUIVALENCIA DE NEGOCIO END-TO-END (las 3 fuentes juntas)
# ============================================================

def test_business_equivalence_full_cartera_facts_with_all_three_deltas_active(monkeypatch):
    """Referencia (sin cache_scope, lecturas completas de siempre) vs.
    camino con Proyectos+Cliente+Client Partner+Horas TODOS vía delta —
    mismo resultado de negocio exacto."""
    proyecto_row = _proy_row("p1")
    cliente_row = {"id": "c1", "fields": {"Title": "Danone", "ClientPartnerLookupId": "pm1"}}
    cp_row = _cp_row("pm1", title="Diego Ramos")
    horas_row = {"id": "h1", "fields": {
        "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
        "HsTrabajadas": "132", "HsVendidas": "160", "HsFacturadas": "140",
    }}

    def fake_paged_reference(url, access_token=None, **kw):
        if "/columns" in url:
            return _CP_COLUMNS
        if _CP in url:
            return [cp_row]
        if _HP in url:
            return [horas_row]
        if _PROY in url:
            return [proyecto_row]
        if _CLI in url:
            return [cliente_row]
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged_reference)
    repo_ref = _repo()
    facts_ref, dim_ref = repo_ref.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, cache_scope=None,
    )

    delta_rows_by_list = {_HP: [horas_row], _PROY: [proyecto_row], _CLI: [cliente_row], _CP: [cp_row]}

    def fake_json_delta(url, access_token=None, **kw):
        for list_id, rows in delta_rows_by_list.items():
            if f"/lists/{list_id}/items/delta" in url:
                return {"value": rows, "@odata.deltaLink": f"{url}&token=v1"}
        raise AssertionError(f"URL de delta no programada: {url}")

    def fake_paged_delta(url, access_token=None, **kw):
        if "/columns" in url:
            return _CP_COLUMNS
        raise AssertionError(f"full-list fetch inesperado: {url}")

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json_delta)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged_delta)
    repo_delta = _repo()
    facts_delta, dim_delta = repo_delta.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, cache_scope="user-a@danwor.com",
    )

    assert facts_ref == facts_delta
    assert dim_ref == dim_delta
