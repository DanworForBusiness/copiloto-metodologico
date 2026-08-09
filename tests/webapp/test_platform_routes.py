"""
test_platform_routes.py — Contrato HTTP de `GET /platform` (batch de
transición multi-workspace, post primer hito real de Staffing).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import platform as platform_router
from webapp.services import auth_service
from webapp import workspaces

auth_service.ALLOWED_USERS = "ALL"

_FULL_SESSION = auth_service.create_session(
    {"email": "platform-full@danwor.com", "name": "Full", "first_name": "Full", "family_name": "User"}
)


def _full_client():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _FULL_SESSION)
    return c


def _academy_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    session = auth_service.create_session(
        {"email": "platform-academy@danwor.com", "name": "Academy", "first_name": "Academy", "family_name": "User"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# --- Unauthenticated ----------------------------------------------------

def test_unauthenticated_renders_platform_neutral_login():
    anon = TestClient(app)
    response = anon.get("/platform")
    assert response.status_code == 200
    assert "Plataforma Danwor" in response.text
    assert 'id="microsoft-login-btn"' in response.text


def test_platform_identity_has_no_copilot_specific_branding():
    anon = TestClient(app)
    response = anon.get("/platform")
    assert "Copiloto Metodológico" not in response.text
    assert "Arquitecto Danwor" not in response.text
    assert "SAP Datasphere" not in response.text


def test_valid_next_survives_login_flow_via_existing_mechanism():
    anon = TestClient(app)
    response = anon.get("/platform?next=/operations/staffing")
    assert response.status_code == 200
    # Mismo mecanismo que `/`: el destino explícito queda en data-home, para
    # que auth.js navegue ahí DIRECTO tras el login (sin volver a /platform).
    assert 'data-home="/operations/staffing"' in response.text


def test_unsafe_next_falls_back_to_platform_default_home():
    anon = TestClient(app)
    response = anon.get("/platform?next=//evil.com")
    assert response.status_code == 200
    assert 'data-home="/platform"' in response.text


# --- Authenticated: workspace resolution ---------------------------------

def test_authenticated_with_one_workspace_redirects_directly(monkeypatch):
    academy = _academy_client(monkeypatch)
    response = academy.get("/platform", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == workspaces.COPILOT.home_url


def test_authenticated_with_two_workspaces_sees_selector():
    response = _full_client().get("/platform")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text
    assert "Operations" in response.text
    assert f'href="{workspaces.COPILOT.home_url}"' in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text


def test_authenticated_explicit_next_takes_precedence_over_selector():
    response = _full_client().get("/platform?next=/operations/staffing", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/operations/staffing"


def test_selector_renders_only_workspaces_returned_by_resolver(monkeypatch):
    # Prueba que el selector es puramente una vista de lo que devuelve
    # available_workspaces — no una lista hardcodeada.
    fake_ws = workspaces.Workspace(
        key="only-one", label="Solo Uno", subtitle="Test", home_url="/solo-uno",
        is_authorized=lambda user: True,
    )
    monkeypatch.setattr(platform_router, "available_workspaces", lambda user: [fake_ws, workspaces.COPILOT])
    response = _full_client().get("/platform")
    assert response.status_code == 200
    assert "Solo Uno" in response.text
    assert 'href="/solo-uno"' in response.text


def test_zero_workspaces_gets_controlled_no_access_state(monkeypatch):
    monkeypatch.setattr(platform_router, "available_workspaces", lambda user: [])
    response = _full_client().get("/platform")
    assert response.status_code == 200
    assert "no tiene acceso habilitado" in response.text.lower()


# --- Operations deep-link round trip --------------------------------------

def test_operations_deep_link_round_trip_via_platform_entry():
    # Cadena completa: /operations/staffing sin sesión → /platform?next=...
    # (verificado en test_staffing_routes.py) → /platform preserva ese next
    # en data-home mientras no hay sesión → una vez autenticado, /platform
    # con ese mismo next redirige DIRECTO a /operations/staffing (nunca al
    # selector). Ambos hops se prueban acá juntos.
    anon = TestClient(app)
    unauth = anon.get("/platform?next=/operations/staffing")
    assert 'data-home="/operations/staffing"' in unauth.text

    authenticated = _full_client().get("/platform?next=/operations/staffing", follow_redirects=False)
    assert authenticated.status_code == 307
    assert authenticated.headers["location"] == "/operations/staffing"
