"""
test_commercial_odoo_routes.py — Commercial Hub Odoo Batch 1: contrato HTTP
de `GET /comercial/data/summary|pipeline` + contratos de fuente (loader
canónico, ausencia de auth/JWT legacy) — CI NUNCA depende de Odoo real
(mockea `webapp.routers.commercial.CommercialService` en el boundary del
router, MISMO patrón que `test_staffing_routes.py` con `StaffingService`).
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import commercial as commercial_router
from webapp.services import auth_service
from webapp.services.commercial_service import CommercialConfigurationError, CommercialDataError

_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")
_COMMERCIAL_PY = (Path(__file__).resolve().parents[2] / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
_COMMERCIAL_SERVICE_PY = (
    Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_service.py"
).read_text(encoding="utf-8")
_ODOO_CLIENT_PY = (Path(__file__).resolve().parents[2] / "webapp" / "services" / "odoo_client.py").read_text(encoding="utf-8")

auth_service.ALLOWED_USERS = "ALL"
# CSV compartido con test_commercial_hub_foundation.py — ver su comentario
# (mismo riesgo de "asignación de módulo, la última que se ejecute gana"
# ya documentado en test_staffing_routes.py para OPERATIONS_ALLOWED_USERS).
auth_service.COMMERCIAL_ALLOWED_USERS = "commercial-test@danwor.com,commercial-odoo-test@danwor.com"

_SESSION = auth_service.create_session(
    {"email": "commercial-odoo-test@danwor.com", "name": "Odoo Test", "first_name": "Odoo", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


class _StubService:
    def __init__(self, summary=None, pipeline=None, raises=None):
        self._summary = summary or {"ok": True}
        self._pipeline = pipeline or {"ok": True}
        self._raises = raises

    def get_summary(self, tipo="todos"):
        if self._raises:
            raise self._raises
        return {**self._summary, "tipo": tipo}

    def get_pipeline(self, tipo="todos"):
        if self._raises:
            raise self._raises
        return {**self._pipeline, "tipo": tipo}


def _unlisted_client(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-odoo-test@danwor.com")
    session = auth_service.create_session(
        {"email": "unlisted-odoo@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


# ============================================================================
# 1 — Auth Hub obligatoria
# ============================================================================

def test_summary_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/summary", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/platform?next=")


def test_pipeline_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/pipeline", follow_redirects=False)
    assert response.status_code == 303


# ============================================================================
# 2 — Entitlement Comercial fail-closed a nivel de ruta
# ============================================================================

def test_summary_unlisted_authorized_app_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial/data/summary")
    assert response.status_code == 403


def test_pipeline_unlisted_authorized_app_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial/data/pipeline")
    assert response.status_code == 403


def test_commercial_allowed_users_blank_denies_previously_authorized_user(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    response = client.get("/comercial/data/summary")
    assert response.status_code == 403


# ============================================================================
# Camino autorizado — contrato de datos
# ============================================================================

def test_summary_authorized_returns_ok_state_and_payload(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService(summary={"pipeline": {"hours": 10}}))
    response = client.get("/comercial/data/summary?tipo=nn")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["summary"]["tipo"] == "nn"


def test_pipeline_authorized_returns_ok_state_and_payload(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService(pipeline={"stages": []}))
    response = client.get("/comercial/data/pipeline?tipo=bi")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["pipeline"]["tipo"] == "bi"


def test_invalid_tipo_query_param_falls_back_to_todos(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService())
    response = client.get("/comercial/data/summary?tipo=not-a-real-tipo")
    assert response.json()["summary"]["tipo"] == "todos"


# ============================================================================
# Fail-soft — config incompleta / error de Odoo / inesperado (SIEMPRE 200)
# ============================================================================

def test_config_incomplete_returns_200_with_config_incomplete_state(monkeypatch):
    monkeypatch.setattr(
        commercial_router, "CommercialService",
        lambda: _StubService(raises=CommercialConfigurationError("no config")),
    )
    response = client.get("/comercial/data/summary")
    assert response.status_code == 200
    assert response.json() == {"state": "config_incomplete"}


def test_odoo_data_error_returns_200_with_source_error_and_stage(monkeypatch):
    monkeypatch.setattr(
        commercial_router, "CommercialService",
        lambda: _StubService(raises=CommercialDataError("boom", stage="execute")),
    )
    response = client.get("/comercial/data/pipeline")
    assert response.status_code == 200
    assert response.json() == {"state": "source_error", "stage": "execute"}


def test_unexpected_failure_never_leaks_traceback_to_browser(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService(raises=RuntimeError("secret internal detail")))
    response = client.get("/comercial/data/summary")
    assert response.status_code == 200
    body = response.json()
    assert body == {"state": "source_error", "stage": "unexpected"}
    assert "secret internal detail" not in response.text


# ============================================================================
# 17/18 — Loader canónico del Hub para la espera foreground de Odoo, NUNCA
# un loader propio de Comercial
# ============================================================================

def test_commercial_js_notifies_the_canonical_hub_foreground_progress():
    assert 'HubForegroundProgress.begin("commercial"' in _COMMERCIAL_JS
    assert 'HubForegroundProgress.complete("commercial")' in _COMMERCIAL_JS
    assert 'HubForegroundProgress.fail("commercial")' in _COMMERCIAL_JS


def test_commercial_js_never_touches_the_progress_dom_or_defines_its_own_loader():
    assert "read-progress" not in _COMMERCIAL_JS
    assert "hub-progress" not in _COMMERCIAL_JS
    assert "querySelector" not in _COMMERCIAL_JS
    assert "setInterval" not in _COMMERCIAL_JS  # el timer del progress vive SOLO en HubForegroundProgress


def test_hub_foreground_progress_boundary_itself_is_untouched_by_this_batch():
    # El boundary compartido (app.js) no ganó una key "commercial" hardcodeada
    # NI ningún caso especial — recibe la key como cualquier otro caller
    # (Reporting/Asignaciones), sin bifurcar comportamiento por vertical.
    assert '"commercial"' not in _APP_JS


# ============================================================================
# 19 — Ningún auth/JWT/DB del CRM legacy
# ============================================================================

def test_no_legacy_crm_auth_primitives_anywhere_in_commercial_surface():
    haystacks = [_COMMERCIAL_PY, _COMMERCIAL_SERVICE_PY, _ODOO_CLIENT_PY, _COMMERCIAL_JS]
    banned = (
        "AUTH_SECRET_KEY", "jwt.encode", "jwt.decode", "users.db", "sqlite3",
        "bcrypt", "passlib", "SMTP_PASSWORD", "reset-password", "MICROSOFT_CLIENT_ID",
    )
    for text in haystacks:
        for term in banned:
            assert term not in text, term


def test_commercial_data_endpoints_never_accept_a_graph_token_or_odoo_credential_param():
    # Odoo es un service account server-side (config.py) — a diferencia de
    # Staffing/Reporting (Graph delegado), estos endpoints NUNCA reciben
    # token/credencial alguna del browser.
    for banned in ("graph_token", "api_key", "odoo_token", "password"):
        assert banned not in _COMMERCIAL_PY


# ============================================================================
# 20/21 — Copiloto y Operations intactos
# ============================================================================

def test_root_copilot_route_still_serves_copilot_unchanged():
    anon = TestClient(app)
    response = anon.get("/")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text


def test_operations_staffing_route_unaffected_by_odoo_batch(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "commercial-odoo-test@danwor.com")
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<p class="eyebrow">Staffing</p>' in response.text
