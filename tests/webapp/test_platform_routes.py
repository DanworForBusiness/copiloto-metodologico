"""
test_platform_routes.py — Contrato HTTP de `GET /platform` (batch One
Danwor entry experience): SOLO alias de compatibilidad, SIEMPRE 307 a
`/onedanwor`. La resolución real (landing/no-access/redirect/selector) vive
en `/onedanwor` — ver `tests/webapp/test_onedanwor_routes.py`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

auth_service.ALLOWED_USERS = "ALL"

_FULL_SESSION = auth_service.create_session(
    {"email": "platform-compat-full@danwor.com", "name": "Full", "first_name": "Full", "family_name": "User"}
)


def test_unauthenticated_platform_redirects_to_onedanwor():
    anon = TestClient(app)
    response = anon.get("/platform", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/onedanwor"


def test_platform_with_valid_next_preserves_it_on_redirect():
    anon = TestClient(app)
    response = anon.get("/platform?next=/operations/staffing", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/onedanwor?next=%2Foperations%2Fstaffing"


def test_platform_with_unsafe_next_drops_it_on_redirect():
    anon = TestClient(app)
    response = anon.get("/platform?next=//evil.com", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/onedanwor"


def test_authenticated_platform_also_redirects_to_onedanwor():
    # /platform NUNCA vuelve a renderizar landing/selector directamente —
    # ni siquiera autenticado. Un solo punto de entrada real.
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _FULL_SESSION)
    response = c.get("/platform", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/onedanwor"


def test_platform_redirect_chain_actually_lands_on_onedanwor():
    anon = TestClient(app)
    response = anon.get("/platform")  # follow_redirects=True (default)
    assert response.status_code == 200
    assert "One Danwor" in response.text
