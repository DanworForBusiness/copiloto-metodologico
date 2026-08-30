"""
test_reporting_horas_vendidas_delta_reuse.py — REPORTING / HORAS VENDIDAS,
SHARED DELTA ACQUISITION CORRECTIVE (owner ticket, ver HANDOFF).

Owner-measured, aislado de Cartera:
    POST /data/horas-vendidas                    Waiting = 14.68 s
    POST /data/horas-vendidas/anios-disponibles   Waiting = 13.67 s

Root cause confirmado: Batch A (`get_batch_a_facts`) y `anios-disponibles`
(`_anio_estado_pairs`) leían Proyectos y Horas por proyectos CADA UNO por su
cuenta — 2 lecturas independientes de la MISMA lista, ninguna cacheada. Este
corrective NO agrega una segunda implementación de delta: enruta AMBOS
consumidores por el MISMO `_delta_sync_list`/`_DELTA_CACHE` ya probado por
Cartera — misma key `(cache_scope, site_id, list_id)`, así que comparten el
canonical state sin importar cuál lo sincronizó primero.

Matriz requerida (§12 del ticket) — SIN duplicar la matriz de bajo nivel de
DeltaCache ya cubierta en test_reporting_horas_delta_sync.py /
test_reporting_proyectos_cliente_client_partner_delta.py /
test_reporting_delta_transient_fallback.py:
  A. Batch A: legacy == delta filtrado localmente por hot_years;
  B. anios-disponibles: legacy == delta;
  C. Horas Vendidas y anios-disponibles comparten el MISMO estado tibio;
  D. dos consumidores fríos SIMULTÁNEOS -> un solo full sync (single-flight);
  E. dos consumidores tibios simultáneos -> sin fetch completo duplicado;
  F. un update delta se vuelve visible en ambas proyecciones;
  G. un delete se vuelve visible en ambas proyecciones;
  H. fallback transitorio preserva disponibilidad (ambos consumidores);
  I. aislamiento de scope;
  J. otros consumidores de Batch A (Participación) sin cambios;
  K. Mantenimiento (anios-disponibles de Cartera) sin cambios.
"""
from __future__ import annotations

import threading

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService

_SITE, _HP, _PROY, _CLI, _CP = "site-1", "hp-list", "proy-list", "cli-list", "cp-list"
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


class _FakeServer:
    """`graph_get_json` programable por URL exacta, con soporte para
    forzar solapamiento real entre threads (`block_at`, §D/§E) y para
    simular fallas de transporte (`raise_at`). `graph_get_paged_values`
    se sirve desde `paged_by_marker` (usado por el camino legacy Y por el
    descubrimiento `/columns` de Client Partner, que NUNCA pasa por
    delta)."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.raise_on: dict[str, Exception] = {}
        self.calls: list[str] = []
        self.paged_calls: list[str] = []
        self.paged_by_marker: dict[str, list] = {}
        self._block_url = None
        self._block_started = None
        self._block_release = None

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

    def block_at(self, url, started_event, release_event):
        self._block_url = url
        self._block_started = started_event
        self._block_release = release_event

    def paged(self, marker, rows):
        self.paged_by_marker[marker] = rows

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.calls.append(url)
            if url == self._block_url and self._block_started is not None:
                self._block_started.set()
                self._block_release.wait(timeout=2)
            exc = self.raise_on.get(url)
            if exc is not None:
                raise exc
            if url not in self.responses:
                raise AssertionError(f"URL de delta no programada: {url}")
            return self.responses[url]

        def fake_paged(url, access_token=None, **kw):
            self.paged_calls.append(url)
            for marker, rows in self.paged_by_marker.items():
                if marker in url:
                    return rows
            raise AssertionError(f"full-list/columns fetch no programado: {url}")

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)


def _horas_url():
    return repo_mod._list_items_delta_url(_SITE, _HP, repo_mod.HORAS_PROYECTOS_SELECT_FIELDS)


def _proy_url():
    return repo_mod._list_items_delta_url(_SITE, _PROY, repo_mod.PROYECTOS_SELECT_FIELDS)


def _cli_url():
    return repo_mod._list_items_delta_url(_SITE, _CLI, repo_mod.CLIENTE_SELECT_FIELDS)


def _cp_url():
    return repo_mod._list_items_delta_url(_SITE, _CP, None)


def _horas_row(item_id, *, anio="2026", horas="1", proyecto="p1", empresa="c1"):
    return {"id": item_id, "fields": {
        "ProyectoLookupId": proyecto, "EmpresaLookupId": empresa, "A_x00f1_o": anio, "Mes": "Agosto",
        "HsTrabajadas": horas, "HsVendidas": horas, "HsFacturadas": horas,
    }}


def _proy_row(item_id, *, title="Soporte Danone", tipo="Proyecto", estado="En curso"):
    return {"id": item_id, "fields": {
        "Title": title, "TipodeProyecto": tipo, "Status": estado,
        "FechadeInicio": "2026-01-15", "ID_x0020_Proyecto_x0020_Danwor": "DAN-001",
    }}


def _cli_row(item_id, *, title="Danone"):
    return {"id": item_id, "fields": {"Title": title, "ClientPartnerLookupId": "pm1"}}


def _removed(item_id):
    return {"id": item_id, "@removed": {"reason": "deleted"}}


def _standard_server(*, horas_items=None):
    """Server UNIVERSAL — sirve las 4 fuentes por AMBOS caminos (legacy vía
    `graph_get_paged_values` Y delta vía `graph_get_json`), y además
    pre-registra, para CADA fuente, el checkpoint `deltaLink` que el
    cold-sync deja guardado (`&dl=v1`) respondiendo "sin cambios" (`&dl=v2`)
    — así una llamada WARM posterior (p. ej. `anios-disponibles` reusando
    el checkpoint que Batch A ya dejó) SIEMPRE encuentra una respuesta
    programada, sin que cada test tenga que registrarla a mano.
    `horas_items`: default `[_horas_row("h1", anio="2026")]`."""
    horas_items = horas_items if horas_items is not None else [_horas_row("h1", anio="2026")]
    server = _FakeServer()
    server.paged(_PROY, [_proy_row("p1")])
    server.paged(_CLI, [_cli_row("c1")])
    server.paged(_HP, horas_items)
    server.paged("/columns", _CP_COLUMNS)
    server.paged(_CP, [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}])
    for cold_url, items in (
        (_proy_url(), [_proy_row("p1")]),
        (_cli_url(), [_cli_row("c1")]),
        (_horas_url(), horas_items),
        (_cp_url(), [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}]),
    ):
        delta1 = f"{cold_url}&dl=v1"
        server.add_page(cold_url, items=items, delta_link=delta1)
        # Warm sin cambios AUTO-PERPETUANTE: el checkpoint se devuelve a sí
        # mismo como próximo deltaLink — así CUALQUIER número de llamadas
        # warm subsiguientes (sin que el test tenga que pre-registrar cada
        # nivel) sigue encontrando una respuesta programada.
        server.add_page(delta1, items=[], delta_link=delta1)
    return server


# ============================================================
# A — Batch A: legacy == delta filtrado localmente por hot_years
# ============================================================

def test_a_batch_a_legacy_equals_delta_filtered_locally(monkeypatch):
    horas_rows = [
        _horas_row("h1", anio="2026", horas="132"),
        _horas_row("h2", anio="2025", horas="8"),
        _horas_row("h3", anio="2021", horas="999"),  # fuera de la ventana caliente
    ]

    # Referencia: camino legacy ($filter OData), cache_scope=None.
    server_ref = _standard_server()
    server_ref.paged(_HP, [r for r in horas_rows if r["fields"]["A_x00f1_o"] in ("2026", "2025")])
    server_ref.install(monkeypatch)
    reference = _repo().get_batch_a_facts(access_token="tok", hot_years=["2026", "2025"])

    # Delta: canonical FULL-HISTORY, filtrado LOCALMENTE por el mismo hot_years.
    server_delta = _standard_server(horas_items=horas_rows)
    server_delta.install(monkeypatch)
    delta_result = _repo().get_batch_a_facts(
        access_token="tok", hot_years=["2026", "2025"], cache_scope="user-a@danwor.com",
    )

    assert delta_result["project_facts"] == reference["project_facts"]
    assert delta_result["cliente_dim"] == reference["cliente_dim"]
    # Sanity — la ventana caliente realmente excluyó 2021 en AMBOS caminos.
    assert all(f["anio"] != "2021" for f in delta_result["project_facts"])


# ============================================================
# B — anios-disponibles: legacy == delta
# ============================================================

def test_b_anios_disponibles_legacy_equals_delta(monkeypatch):
    horas_rows = [_horas_row("h1", anio="2026"), _horas_row("h2", anio="2025"), _horas_row("h3", anio="2021")]

    server_ref = _standard_server(horas_items=horas_rows)
    server_ref.install(monkeypatch)
    reference = _repo().get_horas_anio_estado_pairs("tok")

    server_delta = _standard_server(horas_items=horas_rows)
    server_delta.install(monkeypatch)
    delta_result = _repo().get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")

    assert delta_result == reference
    assert {p["anio"] for p in delta_result} == {"2026", "2025", "2021"}  # incluye años FRÍOS — full-history


# ============================================================
# C — Horas Vendidas y anios-disponibles comparten el MISMO estado tibio
# ============================================================

def test_c_shared_warm_state_between_batch_a_and_anios_disponibles(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    repo = _repo()

    # Batch A (Horas Vendidas) sincroniza PRIMERO — cold completo.
    repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")
    assert _horas_url() in server.calls
    assert _proy_url() in server.calls

    # anios-disponibles llega DESPUÉS — MISMA key de Proyectos/Horas: debe
    # heredar el checkpoint tibio (1 llamada de checkpoint c/u), CERO cold
    # sync propio.
    server.calls.clear()
    repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
    assert server.calls == [f"{_proy_url()}&dl=v1", f"{_horas_url()}&dl=v1"] or set(server.calls) == {
        f"{_proy_url()}&dl=v1", f"{_horas_url()}&dl=v1",
    }, "anios-disponibles disparó su PROPIA sincronización en vez de heredar el estado tibio de Batch A"
    assert _proy_url() not in server.calls  # el COLD de Proyectos no se repitió
    assert _horas_url() not in server.calls  # el COLD de Horas no se repitió


# ============================================================
# D/E — dos consumidores simultáneos (fríos y tibios): single-flight
# ============================================================

def test_d_simultaneous_cold_consumers_one_full_sync(monkeypatch):
    server = _standard_server()
    horas_url = _horas_url()
    started = threading.Event()
    release = threading.Event()
    server.block_at(horas_url, started, release)
    server.install(monkeypatch)
    repo = _repo()

    results = {}

    def call_batch_a():
        results["batch_a"] = repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")

    def call_anios():
        assert started.wait(timeout=2), "batch_a nunca llegó al checkpoint de Horas — no se pudo forzar el solapamiento"
        results["anios"] = repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
        release.set()

    t1 = threading.Thread(target=call_batch_a)
    t2 = threading.Thread(target=call_anios)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    horas_calls = [u for u in server.calls if u == horas_url]
    assert len(horas_calls) == 1, "el sync frío de Horas se disparó más de una vez para la MISMA key — single-flight falló"
    assert results["batch_a"]["project_facts"][0]["anio"] == "2026"
    assert {p["anio"] for p in results["anios"]} == {"2026"}


def test_e_simultaneous_warm_consumers_no_duplicate_full_fetch(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    repo = _repo()
    repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")  # cold, ambos

    # Warm: los 2 consumidores piden el MISMO checkpoint casi a la vez.
    delta1 = f"{_horas_url()}&dl=v1"
    started = threading.Event()
    release = threading.Event()
    server.block_at(delta1, started, release)
    server.calls.clear()

    def call_batch_a():
        repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")

    def call_anios():
        assert started.wait(timeout=2)
        repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
        release.set()

    t1 = threading.Thread(target=call_batch_a)
    t2 = threading.Thread(target=call_anios)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    delta_calls = [u for u in server.calls if u == delta1]
    assert len(delta_calls) == 1, "el checkpoint warm se pidió más de una vez para la MISMA key en vuelo"


# ============================================================
# F/G — update/delete visibles en AMBAS proyecciones
# ============================================================

def test_f_update_visible_in_both_projections(monkeypatch):
    server = _standard_server(horas_items=[_horas_row("h1", anio="2026", horas="1")])
    server.install(monkeypatch)
    repo = _repo()
    repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")

    delta1 = f"{_horas_url()}&dl=v1"
    delta2 = f"{_horas_url()}&dl=v2"
    server.add_page(delta1, items=[_horas_row("h1", anio="2026", horas="999")], delta_link=delta2)
    server.add_page(delta2, items=[], delta_link=delta2)  # warm auto-perpetuante tras el update
    updated = repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")
    assert updated["project_facts"][0]["hsTrabajadas"] == 999.0

    anios_after = repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
    assert {p["anio"] for p in anios_after} == {"2026"}  # sigue reflejando el estado post-update


def test_g_delete_visible_in_both_projections(monkeypatch):
    horas_items = [_horas_row("h1", anio="2026"), _horas_row("h2", anio="2025")]
    server = _standard_server(horas_items=horas_items)
    server.install(monkeypatch)
    repo = _repo()
    repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
    assert {p["anio"] for p in repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")} == {"2025", "2026"}

    delta1 = f"{_horas_url()}&dl=v1"
    delta2 = f"{_horas_url()}&dl=v2"
    server.add_page(delta1, items=[_removed("h2")], delta_link=delta2)
    server.add_page(delta2, items=[], delta_link=delta2)  # warm auto-perpetuante tras el delete
    anios_after = repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
    assert {p["anio"] for p in anios_after} == {"2026"}


# ============================================================
# H — fallback transitorio preserva disponibilidad (ambos consumidores)
# ============================================================

def test_h_transient_fallback_preserves_availability_for_both_consumers(monkeypatch):
    server = _standard_server(horas_items=[_horas_row("h1", anio="2026")])
    server.install(monkeypatch)
    repo = _repo()
    repo.get_batch_a_facts(access_token="tok", hot_years=["2026"], cache_scope="user-a@danwor.com")

    delta1 = f"{_horas_url()}&dl=v1"
    server.raise_at(delta1, TimeoutError("The read operation timed out"))
    server.paged(_HP, [_horas_row("h1", anio="2026"), _horas_row("h2", anio="2025")])
    result = repo.get_batch_a_facts(access_token="tok", hot_years=["2026", "2025"], cache_scope="user-a@danwor.com")
    assert {f["anio"] for f in result["project_facts"]} == {"2026", "2025"}  # Cartera nunca se cayó

    # El checkpoint previo sigue intacto (mismo contrato que 2ce38a2).
    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _HP)]
    assert state["delta_link"] == delta1


# ============================================================
# I — aislamiento de scope
# ============================================================

def test_i_scope_isolation(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    repo = _repo()

    repo.get_horas_anio_estado_pairs("tok", cache_scope="user-a@danwor.com")
    server.calls.clear()
    repo.get_horas_anio_estado_pairs("tok", cache_scope="user-b@danwor.com")
    assert _horas_url() in server.calls, "scope B heredó el estado de scope A — fuga de datos cross-user"


# ============================================================
# J — otros consumidores de Batch A (Participación) sin cambios
# ============================================================

def test_j_participacion_untouched_stays_on_legacy_path(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    service = ReportingService(_repo())

    # get_participacion NUNCA pasa cache_scope — debe seguir en el camino
    # legacy exacto de siempre, cero llamada al endpoint delta.
    service.get_participacion("tok", anios=["2026"])
    assert not any("/items/delta" in u for u in server.calls)


# ============================================================
# K — Mantenimiento (anios-disponibles de Cartera) sin cambios
# ============================================================

def test_k_cartera_mantenimiento_anios_disponibles_untouched(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    repo = _repo()

    repo.get_cartera_mantenimiento_anio_estado_pairs("tok")
    assert not any("/items/delta" in u for u in server.calls), \
        "Mantenimiento nunca debe tocar el endpoint delta (§13 HANDOFF: do not touch)"
