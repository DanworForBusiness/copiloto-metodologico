"""test_human_capital_catalog_refresh_landing.py — "Actualizar catálogos" en UN
click, promovido al margen superior derecho (owner corrective: antes vivía
SOLO en el hero de Base de CVs; ahora vive en el slot `caller()` compartido de
`platform_topbar.html` -- partials/human_capital_catalog_refresh_topbar.html
-- por eso aparece en CUALQUIER página de Capital Humano, no sólo el landing
de Candidatos). Reusa el MISMO endpoint `POST /cchh/catalogos/refresh` (sin
API paralela) y el hook de token compartido (`data-dw-graph-token` en
hub_auth.js): un request HTMX devuelve SÓLO el fragmento de feedback (para
inyectarlo sin navegar), mientras que el submit full-page de catalogos.html
sigue devolviendo la página completa. El feedback usa el partial compartido
`human_capital_catalog_refresh_feedback.html`. `catalogos.html` (la página
dedicada de administración) NO lo incluye -- ya tiene su propio refresh
full-page, un segundo botón ahí sería redundante."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, busqueda_repository, entrevista_repository, human_capital_service, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_TOPBAR_PARTIAL_HTML = (
    _ROOT / "webapp" / "templates" / "partials" / "human_capital_catalog_refresh_topbar.html"
).read_text(encoding="utf-8")
_CATALOGOS_HTML = (_ROOT / "webapp" / "templates" / "human_capital" / "catalogos.html").read_text(encoding="utf-8")
_AUTHORIZED_EMAIL = "hc-catalog-landing-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(busqueda_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(busqueda_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(entrevista_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(entrevista_repository.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    session = auth_service.create_session(
        {"email": _AUTHORIZED_EMAIL, "name": "C L", "first_name": "C", "family_name": "L"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ── Markup del partial compartido (topbar) ──────────────────────────────────

def test_topbar_button_is_one_click_htmx_post_with_shared_token_marker():
    # El botón dispara el POST real al MISMO endpoint, con el token Graph
    # auto-inyectado por el hook compartido (data-dw-graph-token), y apunta su
    # feedback a un contenedor propio (dropdown, no navega).
    assert 'hx-post="/cchh/catalogos/refresh"' in _TOPBAR_PARTIAL_HTML
    assert "data-dw-graph-token" in _TOPBAR_PARTIAL_HTML
    assert 'hx-target="#hc-catalog-refresh-feedback"' in _TOPBAR_PARTIAL_HTML
    assert 'id="hc-catalog-refresh-feedback"' in _TOPBAR_PARTIAL_HTML
    assert "Actualizar catálogos" in _TOPBAR_PARTIAL_HTML


def test_topbar_button_does_not_navigate_to_a_get_refresh():
    # No debe existir un link GET al endpoint POST-only.
    assert 'href="/cchh/catalogos/refresh"' not in _TOPBAR_PARTIAL_HTML


def test_catalogos_page_does_not_duplicate_the_topbar_button():
    # `catalogos.html` ya tiene su propio refresh full-page -- un segundo
    # botón (el del topbar) sería redundante en esa página específica.
    assert "partials/human_capital_catalog_refresh_topbar.html" not in _CATALOGOS_HTML


# ── Presencia real en las páginas de cada sección (owner: "que aparezca en
#    las demás secciones", no sólo Candidatos) ──────────────────────────────

def test_topbar_button_renders_on_candidatos_busquedas_and_entrevistas():
    c = _client()
    profile = profile_repository.create({"first_name": "A", "last_name": "B"})
    busqueda = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    entrevista = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    urls = [
        "/cchh/candidatos",
        "/cchh/candidatos/nuevo",
        f"/cchh/candidatos/{profile['profile_id']}/editar",
        "/cchh/busquedas",
        f"/cchh/busquedas/{busqueda['busqueda_id']}",
        "/cchh/busquedas/nueva",
        "/cchh/entrevistas",
        "/cchh/entrevistas/nueva",
        f"/cchh/entrevistas/{entrevista['entrevista_id']}/editar",
    ]
    for url in urls:
        html = c.get(url).text
        assert 'hx-post="/cchh/catalogos/refresh"' in html, f"falta el botón de refresh en {url}"


def test_topbar_button_does_not_render_on_the_dedicated_catalogos_page():
    # catalogos.html tiene su PROPIO form de refresh full-page -- nunca dos
    # botones de refresh en la misma página.
    html = _client().get("/cchh/catalogos").text
    assert html.count('hx-post="/cchh/catalogos/refresh"') == 0


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
