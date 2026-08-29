"""
test_reporting_horas_vendidas_route_wiring.py — DELTA REUSE ACTIVATION
CORRECTIVE (owner ticket, ver HANDOFF). Owner smoke tras 741e891 mostró
`filter_used=True` y CERO líneas `*_delta` para
`/data/horas-vendidas`/`/data/horas-vendidas/anios-disponibles` — la
sospecha era un `cache_scope` perdido entre router→service→repository.

AUDIT DE FUENTE (ver HANDOFF §2/§3): el trace línea por línea de
`webapp/routers/reporting.py` → `ReportingService.get_horas_vendidas`/
`get_horas_vendidas_anios` → `ReportingRepository.get_batch_a_facts`/
`get_horas_anio_estado_pairs` NO encontró ningún punto donde `cache_scope`
se pierda — el router YA resuelve `user.get("username") or user.get("email")
or user.get("sub")` (MISMO criterio EXACTO que `reporting_cartera_data`,
carácter por carácter) y lo pasa explícito en cada boundary. No se
encontró ningún bug de wiring en este código fuente.

Este archivo agrega la cobertura que el ticket exige de todos modos (§6):
tests A TRAVÉS del boundary HTTP real (FastAPI TestClient + sesión
autenticada real), NO solo mockeando `ReportingService` (eso solo probaría
router→service, nunca service→repository) — acá se inyecta un
`ReportingRepository` REAL con site/list ids de test, y se mockea
ÚNICAMENTE `core.microsoft365` (el transporte), exactamente como el resto
de los tests de repository de esta serie — así una llamada Graph con FORMA
delta (`/items/delta`) vs. forma legacy (`/items` paginado) es evidencia
DIRECTA de qué `cache_scope` llegó realmente al repositorio, sin que el
test pueda "ocultar" el boundary real.

Si esta suite queda en verde pero el owner sigue viendo el camino legacy en
producción, la conclusión pasa a ser operacional (proceso Uvicorn no
recargado tras 741e891), no un defecto de este código — ver HANDOFF §1."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service, reporting_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService
from webapp.routers import reporting as reporting_router

_SITE = "site-1"
_HP, _PROY, _CLI, _CP = "hp-list", "proy-list", "cli-list", "cp-list"
_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]

_SESSION_A = auth_service.create_session(
    {"email": "hv-route-test-a@danwor.com", "name": "HV Test A", "first_name": "HV", "family_name": "A"},
)
_SESSION_B = auth_service.create_session(
    {"email": "hv-route-test-b@danwor.com", "name": "HV Test B", "first_name": "HV", "family_name": "B"},
)
_client = TestClient(app)


@pytest.fixture(autouse=True)
def _authorize_test_users(monkeypatch):
    """`ALLOWED_USERS`/`OPERATIONS_ALLOWED_USERS` son globals de MÓDULO
    mutados a nivel de import por VARIOS archivos de test (ver
    test_reporting_cartera.py) — asignarlos como código de módulo acá
    también sería frágil ante el orden de colección real de pytest (el
    último import "gana" y pisa la allowlist de los demás). `monkeypatch`
    en un fixture, en cambio, corre POR TEST y se revierte solo — nunca
    depende de qué otro archivo se haya importado antes o después."""
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(
        auth_service, "OPERATIONS_ALLOWED_USERS",
        "hv-route-test-a@danwor.com,hv-route-test-b@danwor.com",
    )


@pytest.fixture(autouse=True)
def _isolate_delta_cache(monkeypatch):
    monkeypatch.setattr(repo_mod, "_DELTA_CACHE", DeltaCache())


def _install_real_repository(monkeypatch):
    """Inyecta un `ReportingRepository` REAL (no un mock de servicio) con
    site/list ids de test — el router sigue llamando
    `ReportingService()` (su MISMA construcción de siempre), solo se
    intercepta la fábrica para que use ids de test en vez de `config.py`.
    TODO el resto de la cadena (service → repository → `_delta_sync_list`
    → `_DELTA_CACHE` → `_call_graph` → transporte) corre SIN mockear."""
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


class _FakeTransport:
    """MISMO patrón que el resto de la serie — distingue llamadas delta
    (`graph_get_json` a `/items/delta`) de llamadas legacy
    (`graph_get_paged_values` a `/items`/`/columns`), self-perpetuando el
    checkpoint warm para que cualquier número de requests subsiguientes
    encuentre respuesta sin que el test tenga que pre-registrar cada
    nivel."""

    def __init__(self):
        self.json_calls: list[str] = []
        self.paged_calls: list[str] = []
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
            raise AssertionError(f"URL delta no programada: {url}")

        def fake_paged(url, access_token=None, **kw):
            self.paged_calls.append(url)
            if "/columns" in url:
                return _CP_COLUMNS
            if _CP in url:
                return [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}]
            if _PROY in url:
                return [_proy_row("p1")]
            if _CLI in url:
                return [_cli_row("c1")]
            if _HP in url:
                return [_horas_row("h1")]
            return []

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)


def _seeded_transport():
    t = _FakeTransport()
    t.seed_delta_source(_PROY, repo_mod.PROYECTOS_SELECT_FIELDS, [_proy_row("p1")])
    t.seed_delta_source(_CLI, repo_mod.CLIENTE_SELECT_FIELDS, [_cli_row("c1")])
    t.seed_delta_source(_HP, repo_mod.HORAS_PROYECTOS_SELECT_FIELDS, [_horas_row("h1")])
    t.seed_delta_source(_CP, None, [{"id": "pm1", "fields": {"Title": "Diego Ramos"}}])
    return t


# --- A/D — cache_scope no vacío llega al repositorio; sin lecturas legacy --

def test_a_horas_vendidas_route_reaches_delta_path_not_legacy(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r = _client.post(
        "/operaciones/reporting/integral/data/horas-vendidas",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "ok"

    # Forma DELTA (`/items/delta`), NUNCA la lectura legacy paginada de
    # Proyectos/Cliente/Client Partner/Horas ($filter) — si `cache_scope`
    # hubiera llegado `None`, estas 4 URLs habrían aparecido en su lugar.
    assert any("/items/delta" in u for u in transport.json_calls)
    assert not any(_PROY in u for u in transport.paged_calls)
    # Cliente: SOLO el discovery `/columns` de Client Partner es legacy a
    # propósito (nunca cacheado, ver HANDOFF Fase 3B §6 — el discovery
    # VIVE en la lista Cliente, `.../lists/cli-list/columns`, así que
    # contiene `_CLI` legítimamente); su LECTURA de items sí debe ser delta.
    assert not any(_CLI in u and "/columns" not in u for u in transport.paged_calls)
    assert not any(_HP in u for u in transport.paged_calls)
    # Client Partner: SOLO el discovery `/columns` legacy — su LECTURA de
    # items sí debe ser delta.
    assert all("/columns" in u for u in transport.paged_calls)


def test_a2_anios_disponibles_route_reaches_delta_path_not_legacy(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r = _client.post(
        "/operaciones/reporting/integral/data/horas-vendidas/anios-disponibles",
        data={"graph_token": "tok"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "ok"
    assert any("/items/delta" in u for u in transport.json_calls)
    assert not any(_PROY in u for u in transport.paged_calls)
    assert not any(_HP in u for u in transport.paged_calls)


# --- B/C — ambas rutas usan el MISMO scope, MISMO contrato que Cartera ----

def test_bc_both_routes_share_the_exact_same_scope_as_cartera(monkeypatch):
    """Prueba `cache_scope` == `session_user(request)["sub"]` (MISMO
    contrato que `reporting_cartera_data`, ver HANDOFF §4) verificando el
    efecto observable: la SEGUNDA ruta hereda el checkpoint tibio que la
    PRIMERA dejó — si usaran scopes DISTINTOS, cada una haría su propio
    cold sync."""
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r1 = _client.post(
        "/operaciones/reporting/integral/data/horas-vendidas",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r1.json()["state"] == "ok"
    cold_proy_calls = [u for u in transport.json_calls if u == repo_mod._list_items_delta_url(
        _SITE, _PROY, repo_mod.PROYECTOS_SELECT_FIELDS,
    )]
    assert len(cold_proy_calls) == 1

    transport.json_calls.clear()
    r2 = _client.post(
        "/operaciones/reporting/integral/data/horas-vendidas/anios-disponibles",
        data={"graph_token": "tok"},
    )
    assert r2.json()["state"] == "ok"
    # anios-disponibles NUNCA repite el cold sync de Proyectos/Horas —
    # heredó el MISMO checkpoint tibio que la primera ruta dejó, prueba
    # directa de que ambas resolvieron el MISMO cache_scope.
    cold_url_proy = repo_mod._list_items_delta_url(_SITE, _PROY, repo_mod.PROYECTOS_SELECT_FIELDS)
    cold_url_horas = repo_mod._list_items_delta_url(_SITE, _HP, repo_mod.HORAS_PROYECTOS_SELECT_FIELDS)
    assert cold_url_proy not in transport.json_calls
    assert cold_url_horas not in transport.json_calls


# --- E — aislamiento de scope A/B intacto ----------------------------------

def test_e_scope_a_and_scope_b_never_share_state(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)

    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)
    r_a = _client.post(
        "/operaciones/reporting/integral/data/horas-vendidas",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r_a.json()["state"] == "ok"
    cold_url_proy = repo_mod._list_items_delta_url(_SITE, _PROY, repo_mod.PROYECTOS_SELECT_FIELDS)
    assert cold_url_proy in transport.json_calls

    transport.json_calls.clear()
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_B)
    r_b = _client.post(
        "/operaciones/reporting/integral/data/horas-vendidas",
        data={"graph_token": "tok", "anios": "2026"},
    )
    assert r_b.json()["state"] == "ok"
    # Scope B paga SU PROPIO cold sync — nunca hereda el de A.
    assert cold_url_proy in transport.json_calls
