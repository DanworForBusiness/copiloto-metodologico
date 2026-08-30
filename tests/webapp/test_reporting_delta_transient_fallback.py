"""
test_reporting_delta_transient_fallback.py — PHASE 3B RESILIENCY
CORRECTIVE (Reporting Cartera/Proyecto, owner ticket, ver HANDOFF).

Root cause real (owner warm smoke): un timeout de LECTURA
(`response.read()` dentro de `urlopen`, YA con la conexión establecida)
llega como `TimeoutError` CRUDO — `_call_graph` solo capturaba
`urllib.error.HTTPError`/`URLError`, así que ese `TimeoutError` se
propagaba SIN clasificar y tumbaba el Cartera response completo.

Fix en dos capas:
  1. `_call_graph` ahora también clasifica `TimeoutError` → mismo
     `ReportingGraphError(status_code=None)` que ya usa `URLError`
     ("sin respuesta HTTP recibida en absoluto").
  2. `_delta_sync_list` (el primitivo COMPARTIDO por Horas/Proyectos/
     Cliente/Client Partner) atrapa esa señal ESPECÍFICA
     (`status_code is None`, nunca un status code HTTP real
     reclasificado, nunca una excepción que no sea `ReportingGraphError`)
     y cae al fetch legacy pre-delta EXACTO de esa fuente — SIN tocar el
     estado delta cacheado (`DeltaCache.sync` ya garantiza esto: una
     excepción de `sync_fn` deja `self._data[key]` intacto).

Matriz requerida (§8 del ticket):
  A. warm success -> sin legacy full read;
  B. warm transient timeout -> exactamente un legacy full read, estado
     delta previo SIN CAMBIOS;
  C. próximo request tras B -> reintenta el MISMO checkpoint previo;
  D. warm 410 -> resync existente, NUNCA fallback;
  E. transient + fallback también falla -> se propaga (contrato Cartera);
  F. filas de fallback equivalentes de negocio al camino pre-delta;
  G. Client Partner: resolución dinámica de lista preservada en fallback;
  H. Horas hot_years!=None intacto;
  I. sin llamadas extra en un warm exitoso normal.

El primitivo compartido se prueba a fondo vía Horas (el caso real
reportado); Proyectos/Cliente/Client Partner reciben cobertura de
integración más liviana (mismo mecanismo, ya generalizado — ver HANDOFF
§8: "avoid duplicating identical low-level tests across all four
sources")."""
from __future__ import annotations

import urllib.error

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingGraphError, ReportingRepository

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
    """`graph_get_json` programable por URL exacta — igual que en
    test_reporting_horas_delta_sync.py, ACÁ además soporta simular un
    `TimeoutError` CRUDO (no envuelto en `URLError`, MISMO defecto real
    que reportó el owner) vía `raise_timeout_at`. `graph_get_paged_values`
    (usado por el fetch legacy pre-delta Y por el descubrimiento de
    columnas de Client Partner) se sirve desde `paged_responses`, una
    lista de funciones — cada llamada consume la SIGUIENTE (permite
    devolver contenido distinto en el legacy fetch vs. el discovery)."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.raise_on: dict[str, Exception] = {}
        self.calls: list[str] = []
        self.paged_calls: list[str] = []
        self.paged_by_marker: dict[str, list] = {}

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

    def raise_timeout_at(self, url):
        self.raise_on[url] = TimeoutError("The read operation timed out")

    def paged(self, marker, rows):
        """`marker`: substring que identifica la URL (list_id o
        `/columns`). `rows`: lista de items Graph a devolver para
        cualquier URL que contenga ese marker."""
        self.paged_by_marker[marker] = rows

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.calls.append(url)
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


def _horas_row(item_id, *, horas="1"):
    """MISMA forma Graph cruda (`{id, fields}`) tanto para las respuestas
    delta como para el legacy full-read — ambos caminos leen el MISMO
    `graph_get_json`/`graph_get_paged_values` `value[]`, sin transformación
    intermedia."""
    return {"id": item_id, "fields": {
        "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
        "HsTrabajadas": horas, "HsVendidas": horas, "HsFacturadas": horas,
    }}


_horas_paged_item = _horas_row


# ============================================================
# `_call_graph` — clasificación de TimeoutError
# ============================================================

def test_call_graph_classifies_bare_timeout_error_as_no_status_code():
    def raiser():
        raise TimeoutError("The read operation timed out")

    with pytest.raises(ReportingGraphError) as exc_info:
        repo_mod._call_graph("some_stage", raiser)
    assert exc_info.value.status_code is None
    assert exc_info.value.stage == "some_stage"


def test_call_graph_still_classifies_urlerror_and_httperror_unchanged():
    def raiser_urlerror():
        raise urllib.error.URLError("connection refused")

    with pytest.raises(ReportingGraphError) as exc_info:
        repo_mod._call_graph("s", raiser_urlerror)
    assert exc_info.value.status_code is None

    def raiser_httperror():
        raise urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)

    with pytest.raises(ReportingGraphError) as exc_info2:
        repo_mod._call_graph("s", raiser_httperror)
    assert exc_info2.value.status_code == 403


# ============================================================
# A/I — warm success: sin legacy full read, sin llamadas extra
# ============================================================

def test_a_i_warm_success_no_legacy_full_read_no_extra_calls(monkeypatch):
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")  # cold

    server.add_page(delta1, items=[], delta_link=f"{url}&dl=v2")
    server.calls.clear()
    server.paged_calls.clear()
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]      # SOLO el checkpoint
    assert server.paged_calls == []      # CERO legacy full read
    assert {it["id"] for it in items} == {"h1"}


# ============================================================
# B/C — timeout transitorio: fallback UNA vez, estado previo intacto,
# próximo request reintenta el MISMO checkpoint
# ============================================================

def test_b_warm_transient_timeout_falls_back_state_unchanged(monkeypatch):
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1", horas="100")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")  # cold, deja rows_at_T/deltaLink_T

    # Warm: el checkpoint (delta1) ahora tira timeout de lectura.
    server.raise_timeout_at(delta1)
    server.paged(_HP, [_horas_paged_item("h1", horas="100"), _horas_paged_item("h2", horas="200")])

    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h1", "h2"}  # filas FRESCAS del legacy fetch
    assert len(server.paged_calls) == 1  # exactamente 1 legacy full read

    # El estado cacheado sigue siendo EXACTAMENTE rows_at_T/deltaLink_T —
    # nunca se reemplazó con las filas de fallback ni se borró.
    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _HP)]
    assert state["delta_link"] == delta1
    assert set(state["rows"]) == {"h1"}
    assert state["rows"]["h1"]["fields"]["HsTrabajadas"] == "100"


def test_c_next_request_after_fallback_retries_same_checkpoint(monkeypatch):
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")  # cold

    server.raise_timeout_at(delta1)
    server.paged(_HP, [_horas_paged_item("h1")])
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")  # fallback

    # Ahora delta1 SÍ responde (el owner reintenta más tarde) — el
    # próximo request debe pedir ESE MISMO checkpoint, no uno nuevo.
    del server.raise_on[delta1]
    server.add_page(delta1, items=[_horas_row("h2", horas="50")], delta_link=f"{url}&dl=v2")
    server.calls.clear()
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]
    assert {it["id"] for it in items} == {"h1", "h2"}  # el delta aplicó el cambio sobre rows_at_T correctamente


# ============================================================
# D — 410 sigue yendo a resync, NUNCA se sustituye por fallback
# ============================================================

def test_d_warm_410_still_resyncs_never_fallback(monkeypatch):
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_at(delta1, ReportingGraphError(stage="horas_delta", status_code=410))
    resync_delta = f"{url}&dl=v-resync"
    server.add_page(url, items=[_horas_row("h9")], delta_link=resync_delta)
    server.paged_calls.clear()

    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h9"}
    assert server.paged_calls == [], "410 debe resincronizar vía delta — NUNCA caer a legacy fallback"


# ============================================================
# E — transitorio + fallback también falla: se propaga
# ============================================================

def test_e_transient_failure_and_fallback_failure_propagates(monkeypatch):
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_timeout_at(delta1)
    # SIN `server.paged(_HP, ...)` — el legacy fetch también falla (la
    # fixture levanta AssertionError, standing in por cualquier fallo real
    # de transporte del legacy path).

    with pytest.raises(AssertionError):
        repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    # Tampoco en este caso se tocó el estado cacheado.
    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _HP)]
    assert state["delta_link"] == delta1


def test_e_transient_failure_and_fallback_reporting_error_propagates_as_reporting_error(monkeypatch):
    """MISMO caso que arriba pero con el legacy fetch fallando con el
    error REAL de Cartera (`ReportingGraphError`, no un stand-in de
    test) — confirma que el contrato de error existente de Cartera se
    preserva intacto, nunca silenciado ni reemplazado."""
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_timeout_at(delta1)

    def failing_paged(url, access_token=None, **kw):
        raise urllib.error.HTTPError("http://x", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", failing_paged)
    with pytest.raises(ReportingGraphError) as exc_info:
        repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert exc_info.value.status_code == 503


# ============================================================
# F — filas de fallback equivalentes de negocio al camino pre-delta
# ============================================================

def test_f_fallback_rows_business_equivalent_to_pre_delta_path(monkeypatch):
    server = _FakeServer()
    url = _horas_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[_horas_row("h1", horas="10")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_timeout_at(delta1)
    legacy_rows = [_horas_paged_item("h1", horas="10"), _horas_paged_item("h2", horas="20")]
    server.paged(_HP, legacy_rows)
    fallback_items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    # Referencia: MISMOS datos leídos por el camino pre-delta puro
    # (cache_scope=None, _fetch_horas_proyectos directo).
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", lambda u, access_token=None, **kw: legacy_rows)
    reference_items, _ = repo._fetch_horas_proyectos(_SITE, hot_years=None)

    assert sorted(fallback_items, key=lambda it: it["id"]) == sorted(reference_items, key=lambda it: it["id"])


# ============================================================
# H — hot_years != None (Mantenimiento) nunca toca este camino
# ============================================================

def test_h_hot_years_not_none_never_reaches_delta_or_fallback(monkeypatch):
    def fake_json_raises(url, access_token=None, **kw):
        raise AssertionError("hot_years!=None NUNCA debe llamar al endpoint delta")

    def fake_paged(url, access_token=None, **kw):
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json_raises)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    repo = _repo()
    items, filter_used = repo._fetch_horas_proyectos(_SITE, hot_years=["2026"])
    assert items == []  # no explota — nunca tocó el endpoint delta


# ============================================================
# Proyectos / Cliente — mismo mecanismo, cobertura más liviana
# (el primitivo compartido ya se prueba a fondo arriba vía Horas)
# ============================================================

def test_proyectos_transient_timeout_falls_back_and_preserves_checkpoint(monkeypatch):
    server = _FakeServer()
    url = _proy_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[{"id": "p1", "fields": {"Title": "A"}}], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_timeout_at(delta1)
    server.paged(_PROY, [{"id": "p1", "fields": {"Title": "A"}}, {"id": "p2", "fields": {"Title": "B"}}])
    items = repo._fetch_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"p1", "p2"}

    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _PROY)]
    assert state["delta_link"] == delta1
    assert set(state["rows"]) == {"p1"}  # estado previo intacto, NUNCA reemplazado por las filas de fallback


def test_cliente_transient_timeout_falls_back_and_preserves_checkpoint(monkeypatch):
    server = _FakeServer()
    url = _cli_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[{"id": "c1", "fields": {"Title": "A"}}], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_timeout_at(delta1)
    server.paged(_CLI, [{"id": "c1", "fields": {"Title": "A"}}, {"id": "c2", "fields": {"Title": "B"}}])
    items = repo._fetch_cliente_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"c1", "c2"}

    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _CLI)]
    assert state["delta_link"] == delta1
    assert set(state["rows"]) == {"c1"}


# ============================================================
# G — Client Partner: resolución dinámica preservada en fallback
# ============================================================

def test_g_client_partner_transient_timeout_preserves_dynamic_resolution(monkeypatch):
    server = _FakeServer()
    url = _cp_url()
    delta1 = f"{url}&dl=v1"
    server.add_page(url, items=[{"id": "pm1", "fields": {"Title": "Diego Ramos"}}], delta_link=delta1)
    server.paged("/columns", _CP_COLUMNS)  # descubrimiento SIEMPRE fresco
    server.install(monkeypatch)
    repo = _repo()
    repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")

    server.raise_timeout_at(delta1)
    # El legacy fetch de Client Partner usa graph_get_paged_values contra
    # el TARGET ya resuelto (_CP) — se sirve por el MISMO marcador.
    server.paged(_CP, [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}, {"id": "pm2", "fields": {"Title": "Ana Gómez"}}])
    cp_by_id = repo._resolve_client_partners(_SITE, cache_scope="user-a@danwor.com")
    assert cp_by_id == {"pm1": "Diego Ramos", "pm2": "Ana Gómez"}

    # El descubrimiento (/columns) siguió corriendo — 2 llamadas totales
    # (cold + fallback), MISMO contrato de discovery de siempre.
    columns_calls = [u for u in server.paged_calls if "/columns" in u]
    assert len(columns_calls) == 2

    state = repo_mod._DELTA_CACHE._data[("user-a@danwor.com", _SITE, _CP)]
    assert state["delta_link"] == delta1
    assert set(state["rows"]) == {"pm1"}
