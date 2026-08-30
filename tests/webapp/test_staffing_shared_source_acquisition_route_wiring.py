"""
test_staffing_shared_source_acquisition_route_wiring.py — STAFFING
PERFORMANCE / SHARED SOURCE ACQUISITION CORRECTIVE, §12.L (owner ticket, ver
HANDOFF).

Cobertura de ruta HTTP REAL — MISMA lección explícita del ticket ("so the
optimization cannot silently remain inactive in production as happened
during the Horas Vendidas smoke investigation"): tests A TRAVÉS del boundary
HTTP real (FastAPI TestClient + sesión autenticada real + un
`StaffingRepository` REAL inyectado en el router, no un `StaffingService`
mockeado) — solo `core.microsoft365` (el transporte) se mockea. Prueba que
`initial`/`historial`/`ausencias` resuelven un `cache_scope` NO vacío y
CONSISTENTE entre sí, y que efectivamente alcanzan la forma delta
(`/items/delta`) en vez de la lectura legacy paginada."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service, staffing_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache
from webapp.services.staffing_repository import StaffingFieldMapping, StaffingRepository
from webapp.services.staffing_service import StaffingService
from webapp.routers import staffing as staffing_router

_SITE = "gestion-site-1"
_ASIG, _PROY, _COLAB, _CLI = "asig-list", "proy-list", "colab-list", "cli-list"
_OPS_SITE_ID = "ops-site-1"

_MAPPING = StaffingFieldMapping(
    colaborador_lookup_id="ColaboradorLookupId", proyecto_lookup_id="ProyectoLookupId",
    allocation="Horaspormes", status="Status", start_date="Fechainicio", end_date="FechaFin",
    proyecto_title="Title", colaborador_title="Title",
    colaborador_disponibilidad="Disponibilidad", proyecto_empresa_lookup="EmpresaLookupId",
    cliente_title="ClienteNombre",
)

_SESSION_A = auth_service.create_session(
    {"email": "staffing-route-a@danwor.com", "name": "Staffing Route A", "first_name": "A", "family_name": "Route"},
)
_SESSION_B = auth_service.create_session(
    {"email": "staffing-route-b@danwor.com", "name": "Staffing Route B", "first_name": "B", "family_name": "Route"},
)
_client = TestClient(app)


@pytest.fixture(autouse=True)
def _authorize_test_users(monkeypatch):
    """MISMO criterio robusto ya adoptado por
    `test_reporting_horas_vendidas_route_wiring.py` — `monkeypatch` en un
    fixture autouse, nunca una asignación de módulo top-level (esas son
    globals compartidas mutadas por VARIOS archivos de test, y "gana" la
    última que importe pytest — ver `test_staffing_routes.py`, que sigue el
    patrón frágil histórico sin tocar acá, fuera de alcance)."""
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(
        auth_service, "OPERATIONS_ALLOWED_USERS",
        "staffing-route-a@danwor.com,staffing-route-b@danwor.com",
    )


@pytest.fixture(autouse=True)
def _isolate_staffing_caches(monkeypatch):
    monkeypatch.setattr(repo_mod, "_STAFFING_DELTA_CACHE", DeltaCache())
    monkeypatch.setattr(repo_mod, "_STAFFING_XLSX_CACHE", TimeLogsHistoricalCache())


def _install_real_repository(monkeypatch):
    """Inyecta un `StaffingRepository` REAL con site/list ids de test — el
    router sigue llamando `StaffingService()` (su MISMA construcción de
    siempre), solo se intercepta la fábrica para que use ids de test en vez
    de `config.py`. TODO el resto de la cadena (service → repository →
    `_delta_sync_list`/eTag cache → `_call_staffing_graph` → transporte)
    corre SIN mockear."""
    test_repo = StaffingRepository(
        site_id=_SITE, asignaciones_list_id=_ASIG, proyectos_list_id=_PROY,
        colaboradores_list_id=_COLAB, cliente_list_id=_CLI, field_mapping=_MAPPING,
    )
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: StaffingService(test_repo))
    return test_repo


def _asig_row(item_id):
    return {"id": item_id, "fields": {
        "ColaboradorLookupId": "col1", "ProyectoLookupId": "p1", "Horaspormes": 40,
        "Status": "Activo", "Fechainicio": "2026-01-01", "FechaFin": "2026-12-31",
    }}


def _proy_row(item_id):
    return {"id": item_id, "fields": {"Title": "Proyecto Alfa", "EmpresaLookupId": "cli1"}}


def _colab_row(item_id):
    return {"id": item_id, "fields": {"Title": "Ana Pérez", "Disponibilidad": "Full Time"}}


def _cli_row(item_id):
    return {"id": item_id, "fields": {"ClienteNombre": "Cliente Uno"}}


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
            if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.ABSENCE_SITE_PATH}":
                return {"id": _OPS_SITE_ID}
            if "/drive/root:/" in url and ":/content" not in url:
                return {"id": "xlsx-1", "eTag": "etag-v1"}
            raise AssertionError(f"URL JSON no programada: {url}")

        def fake_paged(url, access_token=None, extra_headers=None, on_page=None, **kw):
            self.paged_calls.append(url)
            items = [_asig_row("a1")] if _ASIG in url else []
            if on_page:
                on_page(len(items))
            return items

        def fake_bytes(url, access_token=None, **kw):
            self.bytes_calls.append(url)
            import io as _io
            import openpyxl as _openpyxl
            wb = _openpyxl.Workbook()
            ws = wb.active
            ws.title = repo_mod.ABSENCE_WORKSHEET_NAME
            ws.append(["Apellido", "Nombre", "Motivo", "Desde", "Hasta", "Estado", "Días"])
            # Fechas cercanas a "hoy" real (no una constante fija) — el
            # overlay excluye períodos fuera del horizonte rolling
            # (mes anterior..+6 meses, ver `_overlaps_horizon`), así que el
            # fixture debe caer siempre DENTRO de esa ventana sin importar
            # cuándo corra la suite.
            import datetime as _dt
            hoy = _dt.date.today()
            desde = hoy.strftime("%d/%m/%Y")
            hasta = (hoy + _dt.timedelta(days=4)).strftime("%d/%m/%Y")
            ws.append(["Pérez", "Ana", "Vacaciones", desde, hasta, "Aprobada", 5])
            buf = _io.BytesIO()
            wb.save(buf)
            return buf.getvalue()

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _seeded_transport():
    t = _FakeTransport()
    t.seed_delta_source(_PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup], [_proy_row("p1")])
    t.seed_delta_source(_COLAB, [_MAPPING.colaborador_title, _MAPPING.colaborador_disponibilidad], [_colab_row("col1")])
    t.seed_delta_source(_CLI, [_MAPPING.cliente_title], [_cli_row("cli1")])
    return t


def _cold_url(list_id, select_fields):
    return repo_mod._list_items_delta_url(_SITE, list_id, select_fields)


_PROY_COLD = _cold_url(_PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
_COLAB_COLD = _cold_url(_COLAB, [_MAPPING.colaborador_title, _MAPPING.colaborador_disponibilidad])
_CLI_COLD = _cold_url(_CLI, [_MAPPING.cliente_title])


# --- initial/historial/ausencias alcanzan la forma delta, nunca legacy -----

def test_initial_route_reaches_delta_path_not_legacy(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r = _client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert r.status_code == 200
    assert "Proyecto Alfa" in r.text

    assert _PROY_COLD in transport.json_calls
    assert _COLAB_COLD in transport.json_calls
    assert _CLI_COLD in transport.json_calls
    assert not any(_PROY in u for u in transport.paged_calls)
    assert not any(_COLAB in u for u in transport.paged_calls)
    assert not any(_CLI in u for u in transport.paged_calls)


def test_historial_route_inherits_the_warm_checkpoint_from_initial(monkeypatch):
    """`cache_scope` == `session_user(request)["sub"]` (MISMO contrato que
    Reporting) probado por el efecto observable: Historial NO repite el
    cold sync que Initial ya pagó — si usaran scopes distintos, cada uno
    haría su propio cold sync."""
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r1 = _client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert r1.status_code == 200
    assert transport.json_calls.count(_PROY_COLD) == 1

    transport.json_calls.clear()
    r2 = _client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
    assert r2.status_code == 200
    assert r2.json()["state"] == "ok"
    assert _PROY_COLD not in transport.json_calls
    assert _COLAB_COLD not in transport.json_calls
    assert _CLI_COLD not in transport.json_calls


def test_ausencias_route_inherits_colaboradores_warm_state_from_initial(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    r1 = _client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert r1.status_code == 200

    transport.json_calls.clear()
    transport.bytes_calls.clear()
    r2 = _client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["state"] == "ok"
    assert len(body["absences"]) == 1

    assert _COLAB_COLD not in transport.json_calls  # Colaboradores reusado de Initial
    assert len(transport.bytes_calls) == 1  # primera vez que se pide el XLSX: 1 descarga


def test_ausencias_route_second_call_same_session_hits_xlsx_cache(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)

    _client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    transport.bytes_calls.clear()

    r = _client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert r.status_code == 200
    assert r.json()["state"] == "ok"
    assert transport.bytes_calls == []  # versión sin cambios ⇒ cero descargas de contenido


# --- scope isolation --------------------------------------------------------

def test_scope_a_and_scope_b_never_share_delta_state(monkeypatch):
    _install_real_repository(monkeypatch)
    transport = _seeded_transport()
    transport.install(monkeypatch)

    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_A)
    r_a = _client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert r_a.status_code == 200
    assert _PROY_COLD in transport.json_calls

    transport.json_calls.clear()
    _client.cookies.set(auth_service.COOKIE_NAME, _SESSION_B)
    r_b = _client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert r_b.status_code == 200
    # scope B paga SU PROPIO cold sync — nunca hereda el de A.
    assert _PROY_COLD in transport.json_calls
