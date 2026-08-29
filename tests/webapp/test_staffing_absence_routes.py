"""
test_staffing_absence_routes.py — Contrato HTTP de
`POST /operaciones/staffing/asignaciones/ausencias` (Staffing Approved
Absence Overlay — generalización del Vacation Timeline Overlay original,
ver HANDOFF: endpoint interno sin contrato externo, reemplazado one-for-one
desde `.../vacaciones`, route surface se mantiene 84). MISMO patrón que
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
    "absences": [{
        "source_id": "1", "consultant_id": "c1", "consultant_name": "Matías Gómez",
        "start_date": "2026-08-15", "end_date": "2026-08-20", "concept": "Vacaciones",
        "on_timeline": True, "start_pct": 12.5, "end_pct": 15.625,
        "clipped_start": False, "clipped_end": False,
    }],
    "unmatched_count": 0, "ambiguous_count": 0,
    # Dirección corrective — Timeline/ventana de ausencias dinámica (ver
    # HANDOFF): el fixture de test double también los trae, MISMO campo que
    # produce `build_absence_overlay_payload` real.
    "absence_history_start": "2026-07-01", "absence_lookback_max_days": 58,
}


class _RecordingAbsenceService:
    calls: list = []

    def __init__(self, overlay=None, error=None):
        self._overlay = overlay if overlay is not None else _OVERLAY_PAYLOAD
        self._error = error

    def get_absence_overlay(self, access_token):
        _RecordingAbsenceService.calls.append(access_token)
        if self._error is not None:
            raise self._error
        return self._overlay


def test_ausencias_endpoint_requires_session_same_as_data_endpoint():
    anon = TestClient(app)
    response = anon.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Foperaciones%2Fstaffing%2Fasignaciones"


def test_ausencias_endpoint_calls_service_with_token_explicit(monkeypatch):
    _RecordingAbsenceService.calls = []
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingAbsenceService())
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok-vac"})
    assert response.status_code == 200
    assert _RecordingAbsenceService.calls == ["tok-vac"]


def test_ausencias_endpoint_returns_json_not_html(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingAbsenceService())
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["state"] == "ok"
    assert data["absences"][0]["consultant_id"] == "c1"
    assert data["absences"][0]["concept"] == "Vacaciones"


def test_ausencias_endpoint_response_never_includes_unmatched_ambiguous_counts_leak(monkeypatch):
    # unmatched_count/ambiguous_count son diagnóstico interno (van al log
    # VERBOSE, ver test de abajo) — nunca se devuelven en la respuesta al
    # cliente (evita filtrar información de negocio no solicitada).
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingAbsenceService())
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert set(response.json().keys()) == {"state", "absences", "absence_history_start", "absence_lookback_max_days"}


def test_ausencias_endpoint_missing_token_never_calls_service(monkeypatch):
    class _Raising:
        def get_absence_overlay(self, *a, **kw):
            raise AssertionError("no debería llamarse")
    monkeypatch.setattr(staffing_router, "StaffingService", _Raising)
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": ""})
    assert response.status_code == 200
    assert response.json() == {"state": "missing_token"}


def test_ausencias_endpoint_config_incomplete_state():
    class _ConfigIncomplete:
        def get_absence_overlay(self, *a, **kw):
            raise RuntimeError("Falta configurar el mapeo de campos de Ausencias")
    orig = staffing_router.StaffingService
    staffing_router.StaffingService = _ConfigIncomplete
    try:
        response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
        assert response.json() == {"state": "config_incomplete"}
    finally:
        staffing_router.StaffingService = orig


def test_ausencias_endpoint_graph_error_state_never_leaks_details():
    class _Boom:
        def get_absence_overlay(self, *a, **kw):
            raise Exception("graph boom, sensitive detail")
    orig = staffing_router.StaffingService
    staffing_router.StaffingService = _Boom
    try:
        response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
        assert response.json() == {"state": "graph_error"}
        assert "sensitive detail" not in response.text
    finally:
        staffing_router.StaffingService = orig


def test_ausencias_endpoint_schema_mismatch_is_fail_soft_own_state():
    # AbsenceSchemaError NO es RuntimeError, nunca cae en config_incomplete.
    # Safe Graph Failure Diagnostics: tiene su PROPIO state ("schema_error",
    # distinto de "graph_error") con stage/missing_fields seguros — nunca el
    # mensaje crudo de la excepción.
    from webapp.services.staffing_repository import AbsenceSchemaError

    class _SchemaMismatch:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceSchemaError(stage="columns", missing_fields=["Colaborador"], ambiguous_fields=[])
    orig = staffing_router.StaffingService
    staffing_router.StaffingService = _SchemaMismatch
    try:
        response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
        assert response.status_code == 200
        assert response.json() == {
            "state": "schema_error", "stage": "columns",
            "missing_fields": ["Colaborador"], "ambiguous_fields": [],
        }
    finally:
        staffing_router.StaffingService = orig


def test_ausencias_endpoint_token_never_echoed(monkeypatch):
    secret = "eyJ-super-secret-token"
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingAbsenceService())
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": secret})
    assert secret not in response.text


def test_ausencias_endpoint_denied_when_not_operations_authorized(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "someone-else@danwor.com")
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 403


class _RaisingIfCalledAbsenceService:
    def get_absence_overlay(self, *args, **kwargs):
        raise AssertionError("StaffingService.get_absence_overlay no debería llamarse")


def test_ausencias_endpoint_a_real_looking_graph_token_never_bypasses_operations_authorization(monkeypatch):
    # Mismo contrato de seguridad que los otros 2 endpoints POST de Staffing
    # — un usuario full-tier NO listado en OPERATIONS_ALLOWED_USERS sigue
    # denegado aunque mande un graph_token con pinta real.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledAbsenceService)
    session = auth_service.create_session(
        {"email": "unlisted-full-vac@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "Full"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    response = ro.post(
        "/operaciones/staffing/asignaciones/ausencias",
        data={"graph_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake"},
    )
    assert response.status_code == 403


def _fake_log_verbose_recorder(monkeypatch):
    calls = []

    def _fake_log_verbose(event_type, user=None, *, scope=None, area=None, module=None, **payload):
        calls.append((event_type, module, payload))

    monkeypatch.setattr(staffing_router, "log_verbose", _fake_log_verbose)
    return calls


def test_ausencias_diagnostics_persist_canonical_data_load_diagnostics_operation_absences(monkeypatch):
    calls = _fake_log_verbose_recorder(monkeypatch)
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingAbsenceService())

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200

    diag = [c for c in calls if c[0] == "data_load_diagnostics"]
    assert len(diag) == 1
    _, module, payload = diag[0]
    assert module == "OP01"
    assert payload["operation"] == "absences"
    assert payload["unmatched_count"] == 0
    assert payload["ambiguous_count"] == 0
    assert "tok" not in str(payload)


def test_ausencias_config_incomplete_persists_canonical_configuration_incomplete_operation_absences(monkeypatch):
    calls = _fake_log_verbose_recorder(monkeypatch)

    class _ConfigIncomplete:
        def get_absence_overlay(self, *a, **kw):
            raise RuntimeError("Falta configurar el mapeo de campos de Ausencias")
    monkeypatch.setattr(staffing_router, "StaffingService", _ConfigIncomplete)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200

    incomplete = [c for c in calls if c[0] == "configuration_incomplete"]
    assert len(incomplete) == 1
    _, module, payload = incomplete[0]
    assert module == "OP01"
    assert payload["operation"] == "absences"


def test_ausencias_graph_failure_persists_canonical_data_load_failed_operation_absences(monkeypatch):
    calls = _fake_log_verbose_recorder(monkeypatch)

    class _Boom:
        def get_absence_overlay(self, *a, **kw):
            raise ConnectionError("graph boom")
    monkeypatch.setattr(staffing_router, "StaffingService", _Boom)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200

    failed = [c for c in calls if c[0] == "data_load_failed"]
    assert len(failed) == 1
    _, module, payload = failed[0]
    assert module == "OP01"
    assert payload["operation"] == "absences"
    assert payload["error_type"] == "ConnectionError"


# ============================================================================
# Safe Graph Failure Diagnostics — stage/status expuestos con seguridad,
# endpoint sigue HTTP 200 fail-soft, sin PII/headers/token/body.
# ============================================================================

def test_ausencias_endpoint_graph_error_exposes_safe_stage_and_status(monkeypatch):
    from webapp.services.staffing_repository import AbsenceGraphError

    class _GraphBoom:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceGraphError(stage="items", status_code=403)
    monkeypatch.setattr(staffing_router, "StaffingService", _GraphBoom)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert response.json() == {"state": "graph_error", "stage": "items", "http_status": 403}


def test_ausencias_endpoint_diagnostics_include_stage_and_http_status(monkeypatch):
    from webapp.services.staffing_repository import AbsenceGraphError
    calls = _fake_log_verbose_recorder(monkeypatch)

    class _GraphBoom:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceGraphError(stage="columns", status_code=404)
    monkeypatch.setattr(staffing_router, "StaffingService", _GraphBoom)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200

    failed = [c for c in calls if c[0] == "data_load_failed"]
    assert len(failed) == 1
    _, module, payload = failed[0]
    assert module == "OP01"
    assert payload["operation"] == "absences"
    assert payload["stage"] == "columns"
    assert payload["http_status"] == 404
    assert payload["reason"] == "http_error"


def test_ausencias_endpoint_graph_error_response_never_leaks_sensitive_material(monkeypatch):
    from webapp.services.staffing_repository import AbsenceGraphError
    secret_token = "eyJ-super-secret-graph-token"

    class _GraphBoom:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceGraphError(stage="items", status_code=401)
    monkeypatch.setattr(staffing_router, "StaffingService", _GraphBoom)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": secret_token})
    text = response.text
    assert secret_token not in text
    assert "Authorization" not in text and "Bearer" not in text
    assert "@odata" not in text and "graph.microsoft.com" not in text
    for pii in ("CUIL", "Legajo", "Comentario"):
        assert pii not in text


def test_ausencias_endpoint_schema_error_response_never_leaks_sensitive_material(monkeypatch):
    from webapp.services.staffing_repository import AbsenceSchemaError
    secret_token = "eyJ-super-secret-graph-token-2"

    class _SchemaBoom:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceSchemaError(stage="columns", missing_fields=["Desde"], ambiguous_fields=[])
    monkeypatch.setattr(staffing_router, "StaffingService", _SchemaBoom)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": secret_token})
    assert response.status_code == 200
    text = response.text
    assert secret_token not in text
    for pii in ("CUIL", "Legajo", "Comentario"):
        assert pii not in text
    assert response.json()["missing_fields"] == ["Desde"]


def test_ausencias_endpoint_successful_flow_unchanged(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingAbsenceService())
    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert set(response.json().keys()) == {"state", "absences", "absence_history_start", "absence_lookback_max_days"}


def test_ausencias_endpoint_schema_error_response_contract_is_the_final_small_shape(monkeypatch):
    from webapp.services.staffing_repository import AbsenceSchemaError

    class _SchemaMismatch:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceSchemaError(stage="columns", missing_fields=["Colaborador"], ambiguous_fields=[])
    monkeypatch.setattr(staffing_router, "StaffingService", _SchemaMismatch)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert set(response.json().keys()) == {"state", "stage", "missing_fields", "ambiguous_fields"}


def test_ausencias_endpoint_graph_error_response_contract_is_the_final_small_shape(monkeypatch):
    from webapp.services.staffing_repository import AbsenceGraphError

    class _GraphBoom:
        def get_absence_overlay(self, *a, **kw):
            raise AbsenceGraphError(stage="items", status_code=403)
    monkeypatch.setattr(staffing_router, "StaffingService", _GraphBoom)

    response = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert set(response.json().keys()) == {"state", "stage", "http_status"}


# ============================================================================
# Assignments-first UX — el endpoint de ASIGNACIONES (`/data`) NO llama ni
# depende de las ausencias: nunca descarga el XLSX, y una falla de ausencias
# NO puede bloquear las asignaciones.
# ============================================================================

def test_assignments_data_endpoint_never_touches_absences(monkeypatch):
    from webapp.services.staffing_service import StaffingOverviewRow

    class _AssignmentsOnly:
        def get_overview(self, access_token):
            return [StaffingOverviewRow(
                assignment_id="1", consultant_id="1", consultant_name="Ana Pérez",
                project_id="10", project_name="Proyecto Alfa",
                allocation_value=50.0, allocation_unit="hours_per_month", capacity_percentage=None,
                status="Active", start_date=None, end_date=None,
            )]

        def get_absence_overlay(self, *a, **kw):
            raise AssertionError("/data (asignaciones) NUNCA debe cargar ausencias/XLSX")

        def get_absences(self, *a, **kw):
            raise AssertionError("/data (asignaciones) NUNCA debe descargar el XLSX")

    monkeypatch.setattr(staffing_router, "StaffingService", _AssignmentsOnly)
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "Proyecto Alfa" in response.text  # asignaciones renderizadas, sin esperar ausencias


# ============================================================================
# Route rename (ver HANDOFF): endpoint interno sin contrato externo, sin
# alias de compatibilidad — reemplazo one-for-one, ruta vieja no responde.
# ============================================================================

def test_old_vacaciones_route_no_longer_exists():
    response = client.post("/operaciones/staffing/asignaciones/vacaciones", data={"graph_token": "tok"})
    assert response.status_code == 404
