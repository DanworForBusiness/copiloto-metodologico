"""
test_staffing_routes.py — Contrato HTTP de Operations/Staffing (Batch 3 de
aceleración post-Fase 2). Mockea `webapp.routers.staffing.StaffingService`
en el boundary del router — sin Graph real, sin tocar StaffingRepository.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import staffing as staffing_router
from webapp.services import auth_service
from webapp.services.staffing_service import StaffingOverviewRow

auth_service.ALLOWED_USERS = "ALL"

_SESSION = auth_service.create_session(
    {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


def _academy_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    session = auth_service.create_session(
        {"email": "academy@danwor.com", "name": "Academy", "first_name": "Academy", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


class _RaisingIfCalledService:
    def get_overview(self, access_token):
        raise AssertionError("StaffingService.get_overview no debería llamarse")


class _RecordingService:
    calls: list = []

    def __init__(self, rows=None, error=None):
        self._rows = rows if rows is not None else []
        self._error = error

    def get_overview(self, access_token):
        _RecordingService.calls.append(access_token)
        if self._error is not None:
            raise self._error
        return self._rows


def _row(**overrides):
    base = dict(
        assignment_id="1", consultant_name="Ana Pérez", project_name="Proyecto Alfa",
        allocation_value=50.0, allocation_unit="hours_per_month", capacity_percentage=None,
        status="Active", start_date=None, end_date=None,
    )
    base.update(overrides)
    return StaffingOverviewRow(**base)


# --- GET /operations/staffing ------------------------------------------------

def test_get_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/operations/staffing", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("/?")
    assert location.endswith("#acceso")
    assert "next=%2Foperations%2Fstaffing" in location


def test_get_authorized_full_tier_returns_200():
    response = client.get("/operations/staffing")
    assert response.status_code == 200


def test_get_academy_tier_is_denied(monkeypatch):
    ro = _academy_client(monkeypatch)
    response = ro.get("/operations/staffing")
    assert response.status_code == 403


def test_get_does_not_call_staffing_service(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    response = client.get("/operations/staffing")
    assert response.status_code == 200


def test_get_renders_operations_staffing_template():
    response = client.get("/operations/staffing")
    assert "Asignaciones de consultores" in response.text
    assert 'id="staffing-rows"' in response.text
    assert 'hx-post="/operations/staffing/data"' in response.text


def test_get_requires_no_graph_token():
    # El mismo GET de arriba nunca manda graph_token — confirma que no es
    # requerido (la request no lleva form/query graph_token en absoluto).
    response = client.get("/operations/staffing")
    assert response.status_code == 200
    assert "graph_token=" not in str(response.request.url)


# --- POST /operations/staffing/data -----------------------------------------

def test_post_authorized_with_token_calls_service_with_token_explicit(monkeypatch):
    _RecordingService.calls = []
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[_row()]))

    response = client.post("/operations/staffing/data", data={"graph_token": "tok-explicit-123"})

    assert response.status_code == 200
    assert _RecordingService.calls == ["tok-explicit-123"]


def test_post_successful_rows_render_fragment(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(consultant_name="Zulema Diaz", project_name="Proyecto Z")]),
    )
    response = client.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "Zulema Diaz" in response.text
    assert "Proyecto Z" in response.text


def test_post_renders_honest_hours_per_month_label_without_capacity_percentage(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(allocation_value=40.0, allocation_unit="hours_per_month")]),
    )
    response = client.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "40 h/mes" in response.text
    # Ninguna capacidad/porcentaje inventada — solo la unidad validada.
    assert "%" not in response.text
    assert "capacity" not in response.text.lower()


def test_post_empty_rows_render_valid_empty_state(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[]))
    response = client.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "No hay asignaciones" in response.text


def test_post_missing_config_renders_controlled_state(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(error=RuntimeError("Falta configurar el mapeo de campos de Staffing")),
    )
    response = client.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "configuration incomplete" in response.text.lower()
    assert "Traceback" not in response.text


def test_post_graph_failure_renders_controlled_error_state(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(error=Exception("graph boom, sensitive detail")),
    )
    response = client.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "sensitive detail" not in response.text
    assert "graph boom" not in response.text
    assert "Traceback" not in response.text


def test_post_token_never_echoed_in_response(monkeypatch):
    secret_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9-super-secret"
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[_row()]))
    response = client.post("/operations/staffing/data", data={"graph_token": secret_token})
    assert secret_token not in response.text

    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(error=RuntimeError("boom")),
    )
    error_response = client.post("/operations/staffing/data", data={"graph_token": secret_token})
    assert secret_token not in error_response.text


def test_post_missing_token_renders_controlled_state_without_calling_service(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    response = client.post("/operations/staffing/data", data={"graph_token": ""})
    assert response.status_code == 200
    assert "token" in response.text.lower()


def test_post_unauthenticated_cannot_use_endpoint_even_with_a_token(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    anon = TestClient(app)
    response = anon.post(
        "/operations/staffing/data", data={"graph_token": "tok"}, follow_redirects=False,
    )
    assert response.status_code == 307  # redirige a login — nunca llega al service


def test_post_academy_tier_cannot_use_endpoint_even_with_a_token(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    ro = _academy_client(monkeypatch)
    response = ro.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 403
