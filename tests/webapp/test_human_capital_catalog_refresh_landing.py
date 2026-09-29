"""test_human_capital_catalog_refresh_landing.py — "Actualizar catálogos" en UN
click desde el landing de Base de CVs (owner). Reusa el MISMO endpoint
`POST /cchh/catalogos/refresh` (sin API paralela) y el hook de token compartido
(`data-dw-graph-token` en hub_auth.js): un request HTMX devuelve SÓLO el
fragmento de feedback (para inyectarlo en /cchh/candidatos, sin navegar),
mientras que el submit full-page de catalogos.html sigue devolviendo la página
completa. El feedback usa el partial compartido
`human_capital_catalog_refresh_feedback.html`."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service

_ROOT = Path(__file__).resolve().parents[2]
_PROFILES_HTML = (_ROOT / "webapp" / "templates" / "human_capital" / "profiles.html").read_text(encoding="utf-8")
_AUTHORIZED_EMAIL = "hc-catalog-landing-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


def _client() -> TestClient:
    session = auth_service.create_session(
        {"email": _AUTHORIZED_EMAIL, "name": "C L", "first_name": "C", "family_name": "L"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ── Markup del landing ───────────────────────────────────────────────────────

def test_landing_button_is_one_click_htmx_post_with_shared_token_marker():
    # El botón dispara el POST real al MISMO endpoint, con el token Graph
    # auto-inyectado por el hook compartido (data-dw-graph-token), y apunta su
    # feedback al contenedor del listado (no navega).
    assert 'hx-post="/cchh/catalogos/refresh"' in _PROFILES_HTML
    assert "data-dw-graph-token" in _PROFILES_HTML
    assert 'hx-target="#hc-catalog-refresh-feedback"' in _PROFILES_HTML
    assert 'id="hc-catalog-refresh-feedback"' in _PROFILES_HTML
    assert "Actualizar catálogos" in _PROFILES_HTML


def test_landing_button_does_not_navigate_to_a_get_refresh():
    # No debe existir un link GET al endpoint POST-only.
    assert 'href="/cchh/catalogos/refresh"' not in _PROFILES_HTML


# ── Comportamiento del endpoint (HX vs full-page) ────────────────────────────

def test_htmx_refresh_returns_only_the_feedback_fragment():
    r = _client().post(
        "/cchh/catalogos/refresh", data={"graph_token": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # Fragmento: NADA del shell de página completa (topbar/app-shell/doctype).
    assert "app-shell" not in r.text
    assert "class=\"topbar\"" not in r.text
    assert "<!doctype" not in r.text.lower()
    # Token vacío -> auth_required, con LINK a /cchh/catalogos (no el botón
    # inerte de reauth, que sólo tiene handler en catalogos.html).
    assert 'data-error-kind="auth_required"' in r.text
    assert 'href="/cchh/catalogos"' in r.text
    assert "data-hc-catalog-reauth" not in r.text


def test_fullpage_refresh_still_returns_the_catalogos_page():
    # El submit full-page de catalogos.html (sin HX-Request) NO cambia:
    # devuelve la página completa con su form de refresh (reauth inline).
    r = _client().post("/cchh/catalogos/refresh", data={"graph_token": ""})
    assert r.status_code == 200
    assert "app-shell" in r.text
    assert "data-hc-graph-read-form" in r.text          # el form full-page sigue ahí
    assert 'data-error-kind="auth_required"' in r.text   # mismo feedback (partial)
    assert "data-hc-catalog-reauth" in r.text            # botón de reauth inline (sin reauth_href)
