"""
test_reporting_rentabilidad_route_wiring.py — REPORTING / RENTABILIDAD,
SHARED SOURCE REUSE CORRECTIVE, §11 del ticket (owner ticket, ver HANDOFF).

MISMO patrón que test_reporting_horas_vendidas_route_wiring.py: tests A
TRAVÉS del boundary HTTP real (FastAPI TestClient + sesión autenticada real
+ un `ReportingRepository` REAL inyectado en el router, no un
`ReportingService` mockeado) — sólo `core.microsoft365` (el transporte) se
mockea, para que la forma delta (`/items/delta`) vs. legacy paginada sea
evidencia DIRECTA de qué `cache_scope` llegó realmente al repositorio."""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service, reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache
from webapp.routers import reporting as reporting_router

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _CP = "hp-list", "proy-list", "cli-list", "cp-list"
_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]

_SESSION_A = auth_service.create_session(
    {"email": "rentabilidad-route-a@danwor.com", "name": "Rent A", "first_name": "A", "family_name": "Route"},
)
_SESSION_B = auth_service.create_session(
    {"email": "rentabilidad-route-b@danwor.com", "name": "Rent B", "first_name": "B", "family_name": "Route"},
)
_client = TestClient(app)


@pytest.fixture(autouse=True)
def _authorize_test_users(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(
        auth_service, "OPERATIONS_ALLOWED_USERS",
        "rentabilidad-route-a@danwor.com,rentabilidad-route-b@danwor.com",
    )


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch):
    monkeypatch.setattr(repo_mod, "_DELTA_CACHE", DeltaCache())
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", TimeLogsHistoricalCache())


def _install_real_repository(monkeypatch):
    test_repo = ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )
    monkeypatch.setattr(reporting_router, "ReportingService", lambda: ReportingService(test_repo))
    return test_repo


def _horas_row(item_id, *, anio="2026"):
    return {"id": item_id, "fields": {
        "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": anio, "Mes": "Agosto",
        "HsTrabajadas": "1", "HsVendidas": "1", "HsFacturadas": "1",
    }}


def _proy_row(item_id):
    return {"id": item_id, "fields": {
        "Title": "Soporte Danone", "TipodeProyecto": "Proyecto", "Status": "En curso",
        "FechadeInicio": "2026-01-15", "ID_x0020_Proyecto_x0020_Danwor": "DAN-001",
    }}


def _cli_row(item_id):
    return {"id": item_id, "fields": {"Title": "Danone", "ClientPartnerLookupId": "pm1"}}


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


class _FakeTransport:
    def __init__(self):
        self.json_calls: list[str] = []
        self.paged_calls: list[str] = []
        self.bytes_calls: list[str] = []
        self._delta_responses: dict[str, dict] = {}

    def seed_delta_source(self, list_id, select_fields, items):
        url = repo_mod._list_items_delta_url(_SITE, list_id, select_fields)
        delta_link = f"{url}&dl=v1"
        self._delta_responses[url] = {"value": items, "@odata.deltaLink": delta_link}
        self._delta_responses[delta_link] = {"value": [], "@odata.deltaLink": delta_link}

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.json_calls.append(url)
            if url in self._delta_responses:
                return self._delta_responses[url]
            if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
                return {"id": _OPS_SITE}
            raise AssertionError(f"URL no programada: {url}")

        def fake_paged(url, access_token=None, **kw):
            self.paged_calls.append(url)
            if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
                return [{"id": "tl1", "name": "Time Logs Reports.csv", "file": {}}]
            if "/columns" in url:
                return _CP_COLUMNS
            if _CP in url:
                return [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}]
            return []

        def fake_bytes(url, access_token=None, **kw):
            self.bytes_calls.append(url)
            return _csv_bytes([{
                "Date": "2026-08-10", "Client": "Danone", "Project": "Soporte Danone",
                "Task": "", "Email": "", "Duration Decimal": "4",
            }])

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _seeded_transport():
    t = _FakeTransport()
    t.seed_delta_source(_PROY, repo_mod.PROYECTOS_SELECT_FIELDS, [_proy_row("p1")])
    t.seed_delta_source(_CLI, repo_mod.CLIENTE_SELECT_FIELDS, [_cli_row("c1")])
    t.seed_delta_source(_HP, repo_mod.HORAS_PROYECTOS_SELECT_FIELDS, [_horas_row("h1")])
    t.seed_delta_source(_CP, None, [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}])
    return t


def _cold_url(list_id, select_fields):
    return repo_mod._list_items_delta_url(_SITE, list_id, select_fields)


_PROY_COLD = _cold_url(_PROY, repo_mod.PROYECTOS_SELECT_FIELDS)
_HP_COLD = _cold_url(_HP, repo_mod.HORAS_PROYECTOS_SELECT_FIELDS)


# --- A/C — cache_scope no vacío llega al repositorio; alcanza el camino delta

def test_a_rentabilidad_route_reaches_delta_path_not_legacy(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "ok"

    assert _PROY_COLD in transport.json_calls
    assert _HP_COLD in transport.json_calls
    # Ninguna lectura legacy paginada de Proyectos/Horas — sólo el
    # discovery `/columns` de Client Partner es legítimamente legacy.
    assert not any(_PROY in u for u in transport.paged_calls)
    assert not any(_HP in u for u in transport.paged_calls)


def test_c_anios_disponibles_route_reaches_delta_path_not_legacy(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad/anios-disponibles",
        data={"graph_token": "tok"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "ok"
    assert _PROY_COLD in transport.json_calls
    assert _HP_COLD in transport.json_calls
    assert not any(_PROY in u for u in transport.paged_calls)
    assert not any(_HP in u for u in transport.paged_calls)


# --- B — ambas rutas usan el MISMO scope: la segunda hereda el checkpoint --

def test_b_both_routes_share_the_exact_same_scope(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r1 = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r1.json()["state"] == "ok"
    assert transport.json_calls.count(_HP_COLD) == 1

    transport.json_calls.clear()
    r2 = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad/anios-disponibles",
        data={"graph_token": "tok"},
    )
    assert r2.json()["state"] == "ok"
    # anios-disponibles NUNCA repite el cold sync — heredó el MISMO
    # checkpoint tibio, prueba directa de que ambas resolvieron el MISMO
    # cache_scope.
    assert _HP_COLD not in transport.json_calls
    assert _PROY_COLD not in transport.json_calls


# --- D — Time Logs recibe el mismo cache_scope, warm = cero descargas -----

def test_d_time_logs_shares_scope_and_stays_warm_across_calls(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r1 = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r1.json()["state"] == "ok"
    assert len(transport.bytes_calls) == 1

    transport.bytes_calls.clear()
    r2 = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r2.json()["state"] == "ok"
    assert transport.bytes_calls == []  # versión sin cambios -> cero descargas de contenido


# --- E — aislamiento de scope A/B intacto -----------------------------------

def test_e_scope_a_and_scope_b_never_share_state(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)

    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)
    r_a = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r_a.json()["state"] == "ok"
    assert _HP_COLD in transport.json_calls

    transport.json_calls.clear()
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_B)
    r_b = _client.post(
        "/operaciones/reporting/integral/data/rentabilidad",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r_b.json()["state"] == "ok"
    # scope B paga SU PROPIO cold sync — nunca hereda el de A.
    assert _HP_COLD in transport.json_calls
