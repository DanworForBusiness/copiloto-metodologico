"""
test_reporting_rentabilidad_delta_reuse.py — REPORTING / RENTABILIDAD, SHARED
SOURCE REUSE CORRECTIVE (owner ticket, ver HANDOFF).

Owner-measured (contaminado por Cartera simultáneo, pero la duplicación de
adquisición es independiente de eso, ver ticket):
    cartera.total (rama Facts de Rentabilidad)   ≈ 14.538 s
    time_logs.total (rama Time Logs)             ≈  8.612 s
    /rentabilidad/anios-disponibles              ≈ 15.443 s (Proyectos
        2.460 s + Horas full-history SIN acotar 12.980 s)

Root cause confirmado por lectura de fuente (ANTES de este corrective):
`get_rentabilidad` nunca pasaba `cache_scope` a
`get_cartera_facts_with_project_dimension`/`get_time_logs_worked_hours`
— y aunque lo hubiera pasado, `_cartera_facts_and_dimension` sólo enrutaba
Horas por proyectos al Delta primitivo cuando `hot_years is None`
(Cartera/Proyecto) — Rentabilidad SIEMPRE pasa `hot_years` explícito
(ventana caliente por defecto), así que habría seguido cayendo al `$filter`
legacy incluso con `cache_scope` real. `get_rentabilidad_anios` releía
Proyectos+Horas COMPLETOS por su cuenta, sin relación con lo que la rama
Facts de Rentabilidad (o Cartera/Proyecto, en la MISMA sesión) ya hubiera
sincronizado.

Corrective: MISMO primitivo ya probado (`_delta_sync_list`/`_DELTA_CACHE`
para Proyectos/Cliente/Client Partner/Horas, `TimeLogsHistoricalCache`/
`_TIME_LOGS_HISTORICAL_CACHE` para Time Logs) — CERO segunda
implementación. Horas por proyectos: el canonical Delta state SIEMPRE trae
la historia completa (nunca `hot_years is None` como gate); `hot_years` se
aplica LOCALMENTE (`_filter_horas_items_by_years`, MISMA función que ya usa
`get_batch_a_facts` para Horas Vendidas).

Matriz requerida (§11/§12 del ticket) — SIN duplicar la matriz de bajo
nivel de DeltaCache/TimeLogsHistoricalCache ya cubierta en
test_reporting_horas_delta_sync.py/
test_reporting_proyectos_cliente_client_partner_delta.py/
test_reporting_delta_transient_fallback.py/
test_reporting_time_logs_historical_cache.py, ni la matriz de Batch A ya
cubierta en test_reporting_horas_vendidas_delta_reuse.py:
  A. Rentabilidad: legacy == delta filtrado localmente por hot_years
     (Facts + Time Logs combinados).
  B. anios-disponibles: legacy == delta.
  C. Rentabilidad y anios-disponibles comparten el MISMO estado tibio de
     Proyectos/Horas.
  D. dos consumidores fríos SIMULTÁNEOS de Horas -> un solo full sync.
  F. hot_years se aplica LOCALMENTE — CERO `$filter` remoto bajo
     `cache_scope` real, sin importar cuántos años se pidan.
  G. Time Logs recibe el MISMO `cache_scope` — version-cache activo.
  H. aislamiento de scope.
  §14: Cartera/Proyecto y Horas Vendidas (otros consumidores de las mismas
     fuentes) sin cambios de comportamiento.
"""
from __future__ import annotations

import csv
import io
import threading

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _CP = "hp-list", "proy-list", "cli-list", "cp-list"
_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch):
    monkeypatch.setattr(repo_mod, "_DELTA_CACHE", DeltaCache())
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", TimeLogsHistoricalCache())


def _repo():
    repo = ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )
    repo._token = "tok"
    return repo


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


class _FakeServer:
    """MISMO patrón que test_reporting_horas_vendidas_delta_reuse.py —
    `graph_get_json` programable por URL exacta (delta + resolución de
    sitio Time Logs), `graph_get_paged_values` servido por marcador
    (legacy/columns/carpeta CSV), `graph_get_bytes` para el contenido de
    los CSV de Time Logs."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.raise_on: dict[str, Exception] = {}
        self.calls: list[str] = []
        self.paged_calls: list[str] = []
        self.bytes_calls: list[str] = []
        self.paged_by_marker: dict[str, list] = {}
        self.csv_root_files: list[dict] = []
        self.csv_file_rows: dict[str, list] = {}
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

    def csv_file(self, item_id, *, name, rows):
        self.csv_root_files.append({"id": item_id, "name": name, "file": {}})
        self.csv_file_rows[item_id] = rows

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.calls.append(url)
            if url == self._block_url and self._block_started is not None:
                self._block_started.set()
                self._block_release.wait(timeout=2)
            exc = self.raise_on.get(url)
            if exc is not None:
                raise exc
            if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
                return {"id": _OPS_SITE}
            if url not in self.responses:
                raise AssertionError(f"URL de delta no programada: {url}")
            return self.responses[url]

        def fake_paged(url, access_token=None, **kw):
            self.paged_calls.append(url)
            if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
                return self.csv_root_files
            for marker, rows in self.paged_by_marker.items():
                if marker in url:
                    return rows
            raise AssertionError(f"full-list/columns/carpeta fetch no programado: {url}")

        def fake_bytes(url, access_token=None, **kw):
            self.bytes_calls.append(url)
            for item_id, rows in self.csv_file_rows.items():
                if f"/drive/items/{item_id}/content" in url:
                    return _csv_bytes(rows)
            raise AssertionError(f"descarga de CSV no programada: {url}")

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


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


def _standard_server(*, horas_items=None, time_logs_rows=None):
    """MISMO server universal que Horas Vendidas, más el CSV único de Time
    Logs (archivo "actual", sin sufijo de año — vive en la raíz de
    `Template Dashboard`, cubre cualquier `hot_years` pedido)."""
    horas_items = horas_items if horas_items is not None else [_horas_row("h1", anio="2026")]
    time_logs_rows = time_logs_rows if time_logs_rows is not None else [
        {"Date": "2026-08-10", "Client": "Danone", "Project": "Soporte Danone", "Task": "", "Email": "", "Duration Decimal": "4"},
    ]
    server = _FakeServer()
    server.paged(_PROY, [_proy_row("p1")])
    server.paged(_CLI, [_cli_row("c1")])
    server.paged(_HP, horas_items)
    server.paged("/columns", _CP_COLUMNS)
    server.paged(_CP, [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}])
    server.csv_file("tl1", name="Time Logs Reports.csv", rows=time_logs_rows)
    for cold_url, items in (
        (_proy_url(), [_proy_row("p1")]),
        (_cli_url(), [_cli_row("c1")]),
        (_horas_url(), horas_items),
        (_cp_url(), [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}]),
    ):
        delta1 = f"{cold_url}&dl=v1"
        server.add_page(cold_url, items=items, delta_link=delta1)
        server.add_page(delta1, items=[], delta_link=delta1)  # warm auto-perpetuante
    return server


# ============================================================
# A — Rentabilidad: legacy == delta filtrado localmente
# ============================================================

def test_a_rentabilidad_legacy_equals_delta_filtered_locally(monkeypatch):
    horas_rows = [
        _horas_row("h1", anio="2026", horas="132"),
        _horas_row("h2", anio="2025", horas="8"),
        _horas_row("h3", anio="2021", horas="999"),  # fuera de la ventana caliente
    ]

    server_ref = _standard_server()
    server_ref.paged(_HP, [r for r in horas_rows if r["fields"]["A_x00f1_o"] in ("2026", "2025")])
    server_ref.install(monkeypatch)
    reference = ReportingService(_repo()).get_rentabilidad("tok", anios=["2026", "2025"])

    server_delta = _standard_server(horas_items=horas_rows)
    server_delta.install(monkeypatch)
    delta_result = ReportingService(_repo()).get_rentabilidad(
        "tok", anios=["2026", "2025"], cache_scope="user-a@danwor.com",
    )

    assert delta_result == reference
    all_years = {a for row in delta_result["proyectos"] for a in row["certByYear"]} | \
        {a for row in delta_result["proyectos"] for a in row["trabByYear"]}
    assert "2021" not in all_years  # la ventana caliente realmente excluyó 2021 en AMBOS caminos


# ============================================================
# B — anios-disponibles: legacy == delta
# ============================================================

def test_b_anios_disponibles_legacy_equals_delta(monkeypatch):
    horas_rows = [_horas_row("h1", anio="2026"), _horas_row("h2", anio="2025"), _horas_row("h3", anio="2021")]

    server_ref = _standard_server(horas_items=horas_rows)
    server_ref.install(monkeypatch)
    reference = ReportingService(_repo()).get_rentabilidad_anios("tok")

    server_delta = _standard_server(horas_items=horas_rows)
    server_delta.install(monkeypatch)
    delta_result = ReportingService(_repo()).get_rentabilidad_anios("tok", cache_scope="user-a@danwor.com")

    assert delta_result == reference
    assert {p["anio"] for p in delta_result} == {"2026", "2025", "2021"}  # incluye años FRÍOS — full-history


# ============================================================
# C — Rentabilidad y anios-disponibles comparten el estado tibio
# ============================================================

def test_c_shared_warm_state_between_rentabilidad_and_anios_disponibles(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    service = ReportingService(_repo())

    service.get_rentabilidad("tok", anios=["2026"], cache_scope="user-a@danwor.com")
    assert _horas_url() in server.calls
    assert _proy_url() in server.calls

    server.calls.clear()
    service.get_rentabilidad_anios("tok", cache_scope="user-a@danwor.com")
    assert _proy_url() not in server.calls  # el COLD de Proyectos no se repitió
    assert _horas_url() not in server.calls  # el COLD de Horas no se repitió
    assert f"{_proy_url()}&dl=v1" in server.calls  # heredó el checkpoint tibio
    assert f"{_horas_url()}&dl=v1" in server.calls


# ============================================================
# D — dos consumidores fríos SIMULTÁNEOS de Horas: single-flight
# ============================================================

def test_d_simultaneous_cold_consumers_one_full_horas_sync(monkeypatch):
    server = _standard_server()
    horas_url = _horas_url()
    started = threading.Event()
    release = threading.Event()
    server.block_at(horas_url, started, release)
    server.install(monkeypatch)
    service = ReportingService(_repo())

    results = {}

    def call_rentabilidad():
        results["rentabilidad"] = service.get_rentabilidad("tok", anios=["2026"], cache_scope="user-a@danwor.com")

    def call_anios():
        assert started.wait(timeout=2), "Rentabilidad nunca llegó al checkpoint de Horas"
        results["anios"] = service.get_rentabilidad_anios("tok", cache_scope="user-a@danwor.com")
        release.set()

    t1 = threading.Thread(target=call_rentabilidad)
    t2 = threading.Thread(target=call_anios)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    horas_calls = [u for u in server.calls if u == horas_url]
    assert len(horas_calls) == 1, "el sync frío de Horas se disparó más de una vez para la MISMA key"
    assert {p["anio"] for p in results["anios"]} == {"2026"}


# ============================================================
# F — hot_years se aplica LOCALMENTE, CERO $filter remoto
# ============================================================

def test_f_hot_years_filtered_locally_never_a_remote_filter(monkeypatch):
    horas_rows = [_horas_row("h1", anio="2026"), _horas_row("h2", anio="2024")]
    server = _standard_server(horas_items=horas_rows)
    server.install(monkeypatch)
    service = ReportingService(_repo())

    result = service.get_rentabilidad("tok", anios=["2024"], cache_scope="user-a@danwor.com")
    # Ningún $filter OData contra Horas — la única URL de Horas tocada es la
    # delta (cold + checkpoint), nunca `_list_items_url(..., filter_expr=...)`.
    assert not any("$filter" in u for u in server.calls)
    assert not any(_HP in u and "/items?" in u for u in server.paged_calls)
    all_years = {a for row in result["proyectos"] for a in row["certByYear"]}
    assert all_years <= {"2024"}


# ============================================================
# G — Time Logs recibe el MISMO cache_scope
# ============================================================

def test_g_time_logs_receives_same_cache_scope_warm_zero_downloads(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    service = ReportingService(_repo())

    service.get_rentabilidad("tok", anios=["2026"], cache_scope="user-a@danwor.com")
    assert len(server.bytes_calls) == 1  # primera vez: 1 descarga de CSV

    server.bytes_calls.clear()
    service.get_rentabilidad("tok", anios=["2026"], cache_scope="user-a@danwor.com")
    assert server.bytes_calls == []  # versión sin cambios (mismo eTag/tamaño) -> cero descargas


# ============================================================
# H — aislamiento de scope
# ============================================================

def test_h_scope_isolation(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    service = ReportingService(_repo())

    service.get_rentabilidad_anios("tok", cache_scope="user-a@danwor.com")
    server.calls.clear()
    service.get_rentabilidad_anios("tok", cache_scope="user-b@danwor.com")
    assert _horas_url() in server.calls, "scope B heredó el estado de scope A — fuga de datos cross-user"


# ============================================================
# §14 — otros consumidores de las mismas fuentes sin cambios
# ============================================================

def test_cartera_proyecto_untouched_by_rentabilidad_change(monkeypatch):
    # Repositorio directo con `include_facturadas=False` (a diferencia del
    # service `get_cartera_proyecto`, que SIEMPRE pide Facturadas XLSX —
    # una fuente sin relación con este corrective, fuera de alcance acá):
    # prueba EXACTAMENTE el código que este corrective tocó
    # (`_cartera_facts_and_dimension`'s gate de Horas) para el caller
    # `hot_years=None` (vida completa del proyecto), sin ruido de Facturadas.
    server = _standard_server()
    server.install(monkeypatch)
    repo = _repo()

    # get_cartera_proyecto NUNCA pasa hot_years (vida completa) — el gate
    # `cache_scope is not None` (sin condición de hot_years) sigue
    # sirviéndole el MISMO canonical state sin filtro local (hot_years=None
    # nunca dispara `_filter_horas_items_by_years`).
    facts, project_dim = repo.get_cartera_facts_with_project_dimension(
        "tok", include_facturadas=False, hot_years=None, cache_scope="user-a@danwor.com",
    )
    assert facts and project_dim  # sanity: la vida completa del proyecto sigue resolviendo
    assert _horas_url() in server.calls  # sigue siendo el cold-sync canonical, sin filtro local


def test_horas_vendidas_untouched_by_rentabilidad_change(monkeypatch):
    server = _standard_server()
    server.install(monkeypatch)
    service = ReportingService(_repo())

    horas_vendidas = service.get_horas_vendidas("tok", anios=["2026"], cache_scope="user-a@danwor.com")
    assert horas_vendidas  # sanity: Batch A sigue funcionando exactamente igual
