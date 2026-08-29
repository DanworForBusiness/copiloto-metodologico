"""
test_reporting_horas_delta_sync.py — DELTA-SYNC CARTERA PROJECT HOURS
corrective, Fase 3A (Reporting Cartera/Proyecto, owner ticket, ver
HANDOFF). Cubre el reemplazo de la relectura completa de "Horas por
proyectos" (1786 filas) por Microsoft Graph `/items/delta`, SOLO en el
camino `hot_years=None` + `cache_scope` real de Cartera/Proyecto.

Matriz requerida (§10 del ticket):
  A. cold sync, 1 página -> estado completo, deltaLink guardado;
  B. cold sync, paginado -> cada nextLink seguido, deltaLink final
     adoptado SOLO al completar;
  C. warm, cero cambios -> 1 llamada delta, sin enumeración completa;
  D. warm, 1 update -> solo el item cambiado viaja, el resultado
     reconstruido lo refleja;
  E. insert -> el item aparece;
  F. delete -> el facet @removed lo quita;
  G. item duplicado en una secuencia delta -> gana la ÚLTIMA ocurrencia;
  H. 410/reset -> estado viejo descartado, UN resync completo, resultado
     nuevo completo;
  I. sync paginado fallido -> el estado previo válido NO se muta
     parcialmente;
  J. aislamiento scope A/B;
  K. hot_years != None -> camino NO-delta intacto (Mantenimiento).

Más: equivalencia de negocio (ref. full-list == delta cold-sync, y los
Cartera facts resultantes de ambos son idénticos) y presupuesto de
llamadas remotas (cold pagina lo necesario; warm sin cambios = SOLO el/los
checkpoint(s) delta, CERO full-list fetch).
"""
from __future__ import annotations

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingGraphError, ReportingRepository

_SITE, _HP = "site-1", "hp-list"


@pytest.fixture(autouse=True)
def _isolate_horas_delta_cache(monkeypatch):
    monkeypatch.setattr(repo_mod, "_HORAS_DELTA_CACHE", DeltaCache())


def _repo():
    repo = ReportingRepository(site_id=_SITE, horas_proyectos_list_id=_HP)
    repo._token = "tok"
    return repo


def _cold_url():
    return repo_mod._list_items_delta_url(_SITE, _HP, repo_mod.HORAS_PROYECTOS_SELECT_FIELDS)


def _row(item_id, *, proyecto="p1", empresa="c1", anio="2026", mes="Agosto", horas="1"):
    return {"id": item_id, "fields": {
        "ProyectoLookupId": proyecto, "EmpresaLookupId": empresa, "A_x00f1_o": anio, "Mes": mes,
        "HsTrabajadas": horas, "HsVendidas": horas, "HsFacturadas": horas,
    }}


def _removed(item_id):
    return {"id": item_id, "@removed": {"reason": "deleted"}}


class _FakeDeltaServer:
    """Runs de páginas pre-programadas por URL EXACTA — `add_page` fija la
    respuesta que `graph_get_json` debe devolver para esa URL; `raise_at`
    hace que esa URL levante una excepción (simula 410/fallas de
    transporte)."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.raise_on: dict[str, Exception] = {}
        self.calls: list[str] = []

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

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.calls.append(url)
            if url in self.raise_on:
                raise self.raise_on[url]
            if url not in self.responses:
                raise AssertionError(f"URL de delta no programada en el fixture: {url}")
            return self.responses[url]

        def fake_paged(url, access_token=None, **kw):
            raise AssertionError(f"full-list fetch inesperado con cache_scope activo: {url}")

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)


# --- A/B — cold sync, 1 página y paginado -----------------------------------

def test_a_cold_sync_one_page_full_state_deltalink_stored(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    server.add_page(cold, items=[_row("h1"), _row("h2")], delta_link=f"{cold}&dl=v1")
    server.install(monkeypatch)

    repo = _repo()
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h1", "h2"}
    assert server.calls == [cold]
    state = repo_mod._HORAS_DELTA_CACHE._data[("user-a@danwor.com", _SITE, _HP)]
    assert state["delta_link"] == f"{cold}&dl=v1"


def test_b_cold_sync_paged_every_nextlink_followed_deltalink_only_at_end(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    page2 = f"{cold}&page=2"
    page3 = f"{cold}&page=3"
    server.add_page(cold, items=[_row("h1")], next_link=page2)
    server.add_page(page2, items=[_row("h2")], next_link=page3)
    server.add_page(page3, items=[_row("h3")], delta_link=f"{cold}&dl=v1")
    server.install(monkeypatch)

    repo = _repo()
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h1", "h2", "h3"}
    assert server.calls == [cold, page2, page3]  # las 3 páginas, en orden


# --- C/D/E/F — warm: cero cambios, update, insert, delete -------------------

def test_c_warm_zero_changes_one_delta_call_no_full_enumeration(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")  # cold

    delta2 = f"{cold}&dl=v2"
    server.add_page(delta1, items=[], delta_link=delta2)  # warm: sin cambios
    server.calls.clear()
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]  # SOLO el checkpoint — nunca una enumeración completa
    assert {it["id"] for it in items} == {"h1"}  # resultado completo sigue igual


def test_d_warm_one_update_only_changed_item_travels_full_result_reflects_it(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1", horas="1"), _row("h2", horas="2")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    delta2 = f"{cold}&dl=v2"
    server.add_page(delta1, items=[_row("h1", horas="99")], delta_link=delta2)  # SOLO h1 cambia
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    by_id = {it["id"]: it for it in items}
    assert by_id["h1"]["fields"]["HsTrabajadas"] == "99"
    assert by_id["h2"]["fields"]["HsTrabajadas"] == "2"  # sin tocar


def test_e_insert_item_appears(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    delta2 = f"{cold}&dl=v2"
    server.add_page(delta1, items=[_row("h2")], delta_link=delta2)  # item NUEVO
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h1", "h2"}


def test_f_delete_removed_facet_removes_item(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1"), _row("h2")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    delta2 = f"{cold}&dl=v2"
    server.add_page(delta1, items=[_removed("h1")], delta_link=delta2)
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h2"}


# --- G — duplicado en la misma secuencia: gana la última ocurrencia --------

def test_g_duplicate_item_in_one_delta_sequence_last_occurrence_wins(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1", horas="1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    # UNA sola página trae h1 dos veces con valores distintos — la SEGUNDA
    # debe ganar (orden de llegada dentro de la MISMA respuesta).
    delta2 = f"{cold}&dl=v2"
    server.add_page(delta1, items=[_row("h1", horas="10"), _row("h1", horas="20")], delta_link=delta2)
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert len(items) == 1
    assert items[0]["fields"]["HsTrabajadas"] == "20"


def test_g_duplicate_item_across_pages_last_page_wins(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1", horas="1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    page2 = f"{cold}&warm-page=2"
    delta2 = f"{cold}&dl=v2"
    server.add_page(delta1, items=[_row("h1", horas="10")], next_link=page2)
    server.add_page(page2, items=[_row("h1", horas="30")], delta_link=delta2)  # última página gana
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert len(items) == 1
    assert items[0]["fields"]["HsTrabajadas"] == "30"


# --- H — 410/reset: descarta estado, UN resync completo, nunca un loop ----

def test_h_410_discards_state_does_exactly_one_full_resync(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1"), _row("h2")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    # El checkpoint quedó inválido — Graph devuelve 410 Gone.
    server.raise_at(delta1, ReportingGraphError(stage="horas_delta", status_code=410))
    # El resync completo posterior arma un universo DISTINTO (prueba que
    # realmente se reconstruyó desde cero, no que reusó el estado viejo).
    resync_delta = f"{cold}&dl=v-resync"
    server.add_page(cold, items=[_row("h3"), _row("h4"), _row("h5")], delta_link=resync_delta)

    server.calls.clear()
    items = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert {it["id"] for it in items} == {"h3", "h4", "h5"}  # estado viejo (h1/h2) descartado
    assert server.calls == [delta1, cold]  # UN intento warm fallido + UN resync — nunca un loop

    # El próximo call ya usa el NUEVO deltaLink del resync, no re-resync.
    server.add_page(resync_delta, items=[], delta_link=f"{cold}&dl=v-resync-2")
    server.calls.clear()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [resync_delta]


def test_h_resync_failure_also_propagates_never_loops(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    server.raise_at(delta1, ReportingGraphError(stage="horas_delta", status_code=410))
    # El resync TAMBIÉN falla — `_horas_delta_cold_sync` reintenta UNA vez
    # sin `$select` ante CUALQUIER falla (MISMO criterio ya establecido
    # por `_items_narrowed_or_full`), así que ambas URLs (con y sin
    # `$select`) deben fallar para que la excepción final se propague.
    cold_no_select = repo_mod._list_items_delta_url(_SITE, _HP, None)
    server.raise_at(cold, ReportingGraphError(stage="horas_delta", status_code=503))
    server.raise_at(cold_no_select, ReportingGraphError(stage="horas_delta", status_code=503))

    server.calls.clear()
    with pytest.raises(ReportingGraphError) as exc_info:
        repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert exc_info.value.status_code == 503
    assert server.calls == [delta1, cold, cold_no_select]  # warm + resync(select) + resync(fallback) — nunca un loop


# --- I — sync paginado fallido: estado previo NO se muta parcialmente -----

def test_i_failed_paged_sync_does_not_partially_mutate_previous_state(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    delta1 = f"{cold}&dl=v1"
    server.add_page(cold, items=[_row("h1"), _row("h2")], delta_link=delta1)
    server.install(monkeypatch)
    repo = _repo()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    # Warm: la primera página trae un cambio real, pero la SEGUNDA página
    # (nextLink) falla con un error genérico (no 410) — el sync completo
    # debe fallar SIN adoptar el cambio parcial de la primera página.
    page2 = f"{cold}&warm-page=2"
    server.add_page(delta1, items=[_row("h1", horas="999")], next_link=page2)
    server.raise_at(page2, ReportingGraphError(stage="horas_delta", status_code=503))

    with pytest.raises(ReportingGraphError):
        repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    # El estado en cache sigue siendo el ORIGINAL (h1 con horas="1", NUNCA
    # "999") — la mutación parcial nunca se publicó.
    state = repo_mod._HORAS_DELTA_CACHE._data[("user-a@danwor.com", _SITE, _HP)]
    assert state["rows"]["h1"]["fields"]["HsTrabajadas"] == "1"
    assert state["delta_link"] == delta1  # deltaLink tampoco avanzó


# --- J — aislamiento scope A/B ----------------------------------------------

def test_j_scope_b_never_inherits_scope_a_state(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    server.add_page(cold, items=[_row("h1")], delta_link=f"{cold}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [cold]

    server.calls.clear()
    items_b = repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-b@danwor.com")
    assert server.calls == [cold], "scope B debió pagar SU PROPIO cold sync — no heredó el de A"
    assert {it["id"] for it in items_b} == {"h1"}


# --- K — hot_years != None: camino NO-delta intacto (Mantenimiento) -------

def test_k_hot_years_not_none_never_uses_delta_path(monkeypatch):
    calls = {"delta": 0, "paged": 0}

    def fake_json(url, access_token=None, **kw):
        calls["delta"] += 1
        raise AssertionError("hot_years!=None NUNCA debe llamar al endpoint delta")

    def fake_paged(url, access_token=None, **kw):
        calls["paged"] += 1
        assert "/items/delta" not in url
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    repo = _repo()
    items, filter_used = repo._fetch_horas_proyectos(_SITE, hot_years=["2026"])
    assert calls["delta"] == 0
    assert calls["paged"] >= 1


def test_k_cache_scope_none_never_uses_delta_path_even_with_hot_years_none(monkeypatch):
    """`hot_years=None` SOLO” dispara delta si TAMBIÉN hay `cache_scope`
    real — combo (`get_cartera()`)/tests que nunca pasan `cache_scope`
    deben seguir en el camino de siempre."""
    calls = {"delta": 0, "paged": 0}

    def fake_json(url, access_token=None, **kw):
        calls["delta"] += 1
        raise AssertionError("cache_scope=None NUNCA debe llamar al endpoint delta")

    def fake_paged(url, access_token=None, **kw):
        calls["paged"] += 1
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    repo = _repo()
    items, filter_used = repo._fetch_horas_proyectos(_SITE, hot_years=None)
    assert calls["delta"] == 0
    assert calls["paged"] == 1


# --- Equivalencia de negocio: full-list de referencia == delta cold-sync --

def test_business_equivalence_reference_full_list_vs_delta_cold_sync(monkeypatch):
    rows = [_row("h1", proyecto="p1", empresa="c1", anio="2026", mes="Agosto", horas="132"),
            _row("h2", proyecto="p2", empresa="c1", anio="2025", mes="Diciembre", horas="8")]

    # A. referencia — full-list existente (`_fetch_horas_proyectos`,
    # hot_years=None, camino NO-delta).
    def fake_paged_reference(url, access_token=None, **kw):
        return rows

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged_reference)
    repo_ref = _repo()
    reference_items, _ = repo_ref._fetch_horas_proyectos(_SITE, hot_years=None)

    # B. delta cold-sync — MISMAS filas, servidas vía /items/delta.
    server = _FakeDeltaServer()
    cold = _cold_url()
    server.add_page(cold, items=rows, delta_link=f"{cold}&dl=v1")
    server.install(monkeypatch)
    repo_delta = _repo()
    delta_items = repo_delta._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")

    def _sorted_by_id(items):
        return sorted(items, key=lambda it: it["id"])

    assert _sorted_by_id(reference_items) == _sorted_by_id(delta_items)


def test_business_equivalence_final_cartera_facts_identical(monkeypatch):
    from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

    proyectos = [{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Proyecto",
                  "FechadeInicio": "2026-01-15", "Status": "En curso",
                  "ID_x0020_Proyecto_x0020_Danwor": "DAN-001"}]
    clientes = [{"id": "c1", "Title": "Danone"}]
    horas_row = _row("h1", proyecto="p1", empresa="c1", anio="2026", mes="Agosto", horas="132")

    def _build_paged(hp_source):
        def fake_paged(url, access_token=None, **kw):
            if "/columns" in url:
                return []
            if _HP in url:
                return hp_source()
            if "proy" in url.lower():
                return [{"id": r.get("id", ""), "fields": r} for r in proyectos]
            if "cli" in url.lower():
                return [{"id": r.get("id", ""), "fields": r} for r in clientes]
            return []
        return fake_paged

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", _build_paged(lambda: [horas_row]))
    repo_ref = ReportingRepository(site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id="proy-list",
                                    cliente_list_id="cli-list")
    facts_ref, _dim_ref = repo_ref.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, cache_scope=None,
    )

    server = _FakeDeltaServer()
    cold = _cold_url()
    server.add_page(cold, items=[horas_row], delta_link=f"{cold}&dl=v1")

    def fake_json_and_paged(monkeypatch):
        def fake_json(url, access_token=None, **kw):
            if url in server.responses:
                server.calls.append(url)
                return server.responses[url]
            raise AssertionError(f"URL no programada: {url}")

        def fake_paged(url, access_token=None, **kw):
            if "/columns" in url:
                return []
            if "proy" in url.lower():
                return [{"id": r.get("id", ""), "fields": r} for r in proyectos]
            if "cli" in url.lower():
                return [{"id": r.get("id", ""), "fields": r} for r in clientes]
            raise AssertionError(f"full-list fetch inesperado: {url}")

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)

    fake_json_and_paged(monkeypatch)
    repo_delta = ReportingRepository(site_id=_SITE, horas_proyectos_list_id="hp-list-delta",
                                      proyectos_list_id="proy-list", cliente_list_id="cli-list")
    repo_delta.horas_proyectos_list_id = _HP  # MISMO list id que el cold_url calculado arriba
    facts_delta, _dim_delta = repo_delta.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, cache_scope="user-a@danwor.com",
    )

    assert facts_ref == facts_delta


# --- Presupuesto de llamadas remotas ----------------------------------------

def test_remote_call_budget_cold_pages_as_needed_warm_only_checkpoint(monkeypatch):
    server = _FakeDeltaServer()
    cold = _cold_url()
    page2 = f"{cold}&page=2"
    server.add_page(cold, items=[_row("h1")], next_link=page2)
    server.add_page(page2, items=[_row("h2")], delta_link=f"{cold}&dl=v1")
    server.install(monkeypatch)
    repo = _repo()

    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [cold, page2]  # cold: 2 páginas, ninguna llamada extra

    delta1 = f"{cold}&dl=v1"
    server.add_page(delta1, items=[], delta_link=f"{cold}&dl=v2")
    server.calls.clear()
    repo._fetch_horas_proyectos_delta(_SITE, cache_scope="user-a@danwor.com")
    assert server.calls == [delta1]  # warm sin cambios: SOLO el checkpoint, jamás cold ni paged
