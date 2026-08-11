"""
test_staffing_vacation_routes.py — Contrato HTTP de
`POST /operaciones/staffing/asignaciones/vacaciones` (Staffing Vacation
Timeline Overlay, ver HANDOFF). MISMO patrón que
test_staffing_director_priority_v2.py::historial — mockea
`webapp.routers.staffing.StaffingService` en el boundary del router, sin
Graph real, sin tocar StaffingRepository.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import staffing as staffing_router
from webapp.services import auth_service

auth_service.ALLOWED_USERS = "ALL"
# Reusa la MISMA identidad compartida que el resto de tests/webapp/test_staffing_*.py
# (ver comentario en test_staffing_routes.py) — evita pisarse en el CSV de
# OPERATIONS_ALLOWED_USERS entre archivos.
auth_service.OPERATIONS_ALLOWED_USERS = "staffing-test@danwor.com,topbar-full@danwor.com,onedanwor-full@danwor.com"
_SESSION = auth_service.create_session(
    {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

_OVERLAY_PAYLOAD = {
    "vacations": [{
        "source_id": "1", "consultant_id": "c1", "consultant_name": "Matías Gómez",
        "start_date": "2026-08-15", "end_date": "2026-08-20",
        "on_timeline": True, "start_index": 1, "end_index": 1,
        "clipped_start": False, "clipped_end": False,
    }],
    "unmatched_count": 0, "ambiguous_count": 0,
}


class _RecordingVacationService:
    calls: list = []

    def __init__(self, overlay=None, error=None):
        self._overlay = overlay if overlay is not None else _OVERLAY_PAYLOAD
        self._error = error

    def get_vacation_overlay(self, access_token):
        _RecordingVacationService.calls.append(access_token)
        if self._error is not None:
            raise self._error
        return self._overlay


def test_vacaciones_endpoint_requires_session_same_as_data_endpoint():
    anon = TestClient(app)
    response = anon.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Foperaciones%2Fstaffing%2Fasignaciones"


def test_vacaciones_endpoint_calls_service_with_token_explicit(monkeypatch):
    _RecordingVacationService.calls = []
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingVacationService())
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok-vac"})
    assert response.status_code == 200
    assert _RecordingVacationService.calls == ["tok-vac"]


def test_vacaciones_endpoint_returns_json_not_html(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingVacationService())
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["state"] == "ok"
    assert data["vacations"][0]["consultant_id"] == "c1"


def test_vacaciones_endpoint_response_never_includes_unmatched_ambiguous_counts_leak(monkeypatch):
    # unmatched_count/ambiguous_count son diagnóstico interno (van al log
    # VERBOSE, ver test de abajo) — nunca se devuelven en la respuesta al
    # cliente (evita filtrar información de negocio no solicitada).
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingVacationService())
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert set(response.json().keys()) == {"state", "vacations"}


def test_vacaciones_endpoint_missing_token_never_calls_service(monkeypatch):
    class _Raising:
        def get_vacation_overlay(self, *a, **kw):
            raise AssertionError("no debería llamarse")
    monkeypatch.setattr(staffing_router, "StaffingService", _Raising)
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": ""})
    assert response.status_code == 200
    assert response.json() == {"state": "missing_token"}


def test_vacaciones_endpoint_config_incomplete_state():
    class _ConfigIncomplete:
        def get_vacation_overlay(self, *a, **kw):
            raise RuntimeError("Falta configurar el mapeo de campos de Vacaciones")
    orig = staffing_router.StaffingService
    staffing_router.StaffingService = _ConfigIncomplete
    try:
        response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
        assert response.json() == {"state": "config_incomplete"}
    finally:
        staffing_router.StaffingService = orig


def test_vacaciones_endpoint_graph_error_state_never_leaks_details():
    class _Boom:
        def get_vacation_overlay(self, *a, **kw):
            raise Exception("graph boom, sensitive detail")
    orig = staffing_router.StaffingService
    staffing_router.StaffingService = _Boom
    try:
        response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
        assert response.json() == {"state": "graph_error"}
        assert "sensitive detail" not in response.text
    finally:
        staffing_router.StaffingService = orig


def test_vacaciones_endpoint_token_never_echoed(monkeypatch):
    secret = "eyJ-super-secret-token"
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingVacationService())
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": secret})
    assert secret not in response.text


def test_vacaciones_endpoint_denied_when_not_operations_authorized(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "someone-else@danwor.com")
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert response.status_code == 403


class _RaisingIfCalledVacationService:
    def get_vacation_overlay(self, *args, **kwargs):
        raise AssertionError("StaffingService.get_vacation_overlay no debería llamarse")


def test_vacaciones_endpoint_a_real_looking_graph_token_never_bypasses_operations_authorization(monkeypatch):
    # Mismo contrato de seguridad que los otros 2 endpoints POST de Staffing
    # — un usuario full-tier NO listado en OPERATIONS_ALLOWED_USERS sigue
    # denegado aunque mande un graph_token con pinta real.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledVacationService)
    session = auth_service.create_session(
        {"email": "unlisted-full-vac@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "Full"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    response = ro.post(
        "/operaciones/staffing/asignaciones/vacaciones",
        data={"graph_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake"},
    )
    assert response.status_code == 403


def _fake_log_verbose_recorder(monkeypatch):
    calls = []

    def _fake_log_verbose(event_type, user=None, *, scope=None, area=None, module=None, **payload):
        calls.append((event_type, module, payload))

    monkeypatch.setattr(staffing_router, "log_verbose", _fake_log_verbose)
    return calls


def test_vacaciones_diagnostics_persist_canonical_data_load_diagnostics_operation_vacations(monkeypatch):
    calls = _fake_log_verbose_recorder(monkeypatch)
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingVacationService())

    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert response.status_code == 200

    diag = [c for c in calls if c[0] == "data_load_diagnostics"]
    assert len(diag) == 1
    _, module, payload = diag[0]
    assert module == "OP01"
    assert payload["operation"] == "vacations"
    assert payload["unmatched_count"] == 0
    assert payload["ambiguous_count"] == 0
    assert "tok" not in str(payload)


def test_vacaciones_config_incomplete_persists_canonical_configuration_incomplete_operation_vacations(monkeypatch):
    calls = _fake_log_verbose_recorder(monkeypatch)

    class _ConfigIncomplete:
        def get_vacation_overlay(self, *a, **kw):
            raise RuntimeError("Falta configurar el mapeo de campos de Vacaciones")
    monkeypatch.setattr(staffing_router, "StaffingService", _ConfigIncomplete)

    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert response.status_code == 200

    incomplete = [c for c in calls if c[0] == "configuration_incomplete"]
    assert len(incomplete) == 1
    _, module, payload = incomplete[0]
    assert module == "OP01"
    assert payload["operation"] == "vacations"


def test_vacaciones_graph_failure_persists_canonical_data_load_failed_operation_vacations(monkeypatch):
    calls = _fake_log_verbose_recorder(monkeypatch)

    class _Boom:
        def get_vacation_overlay(self, *a, **kw):
            raise ConnectionError("graph boom")
    monkeypatch.setattr(staffing_router, "StaffingService", _Boom)

    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert response.status_code == 200

    failed = [c for c in calls if c[0] == "data_load_failed"]
    assert len(failed) == 1
    _, module, payload = failed[0]
    assert module == "OP01"
    assert payload["operation"] == "vacations"
    assert payload["error_type"] == "ConnectionError"
