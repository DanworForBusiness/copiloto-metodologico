"""
test_human_capital_transversal_back_navigation.py — Capital Humano, corrective
de navegación transversal: reemplaza el "Volver al listado"/"Volver a
Candidatos" inline (un <a> genérico dentro del module-hero) por el ÚNICO
primitivo de "volver" que el Hub ya tiene, `.catalog-back` (tools.css,
sticky/theme-aware, ver whats_new.html/catalog_guide_slides.html) — investigado
antes de tocar código: Commercial/Staffing/Reporting NO tienen ningún control
de back/close transversal propio (cero ocurrencias de "volver"/"cerrar"/
"back"/"close" en crm.html/strategy.html/staffing*.html/reporting_integral.html)
así que `.catalog-back` es el ÚNICO candidato real de reuso, no uno entre
varios. Cero CSS/JS nuevo: la clase viene de tools.css (ya cargado Hub-wide
vía base.html), nunca redeclarada acá.

Alcance: las TRES superficies que tenían el link inline (Cargar CV al banco,
Carga masiva de CVs, candidate detail/editar) — profile_import_result.html
("Ir a Candidatos") queda AFUERA a propósito: es un CTA de avance tras una
acción ya completada, no una navegación de "volver" durante una tarea en
curso, semánticamente distinto."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_HC_CSS = (_ROOT / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
_TOOLS_CSS = (_ROOT / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")

_AUTHORIZED_EMAIL = "human-capital-back-nav-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Back Nav Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ============================================================================
# Las tres superficies usan EXACTAMENTE `.catalog-back`, apuntando al
# listado -- MISMA URL que el "volver" inline que reemplazan.
# ============================================================================

def test_profile_new_page_uses_the_canonical_catalog_back_link():
    text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos/nuevo").text
    assert '<a class="catalog-back" href="/cchh/candidatos">' in text


def test_profile_import_page_uses_the_canonical_catalog_back_link():
    text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos/importar").text
    assert '<a class="catalog-back" href="/cchh/candidatos">' in text


def test_profile_edit_page_uses_the_canonical_catalog_back_link():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/cchh/candidatos/nuevo", data={"first_name": "Juan", "last_name": "Gómez"})
    profile_id = profile_repository.list()[0]["profile_id"]
    text = client.get(f"/cchh/candidatos/{profile_id}/editar").text
    assert '<a class="catalog-back" href="/cchh/candidatos">' in text


def test_catalog_back_link_resolves_to_a_real_200_surface():
    # "X vuelve a la superficie correcta" (§ validación del ticket) -- el
    # href no es sólo un string plausible, la ruta que apunta responde 200.
    client = _client_as(_AUTHORIZED_EMAIL)
    response = client.get("/cchh/candidatos/nuevo")
    assert client.get("/cchh/candidatos").status_code == 200
    assert response.status_code == 200


# ============================================================================
# Reuso genuino, no reimplementación: cero CSS propio de Capital Humano para
# esto, la clase viene únicamente de tools.css.
# ============================================================================

def test_catalog_back_class_is_never_redeclared_in_human_capital_css():
    assert ".catalog-back" not in _HC_CSS


def test_no_hc_specific_back_or_close_css_exists():
    for banned in (".hc-back", ".hc-close", ".hc-return", "hc-catalog-back"):
        assert banned not in _HC_CSS, banned


def test_catalog_back_primitive_still_sticky_and_theme_aware_in_tools_css():
    # Guardrail de que seguimos apuntando al primitivo REAL (no uno
    # renombrado/movido sin actualizar este test) -- sticky + ambos temas.
    # `\n.catalog-back {` (no `button.catalog-back {`, un reset de botón NO
    # relacionado que también vive en tools.css) es la regla base real.
    idx = _TOOLS_CSS.index("\n.catalog-back {")
    body = _TOOLS_CSS[idx: _TOOLS_CSS.index("}", idx) + 1]
    assert "position:sticky" in body.replace(" ", "")
    assert ".content-theme-light .catalog-back" in _TOOLS_CSS
    assert ".content-theme-dark .catalog-back" in _TOOLS_CSS


# ============================================================================
# Cero drift de wording/flujo funcional CCHH -- sólo cambió el afordance de
# navegación, el texto que ya existía se preserva.
# ============================================================================

def test_wording_is_preserved_not_replaced_with_generic_copy():
    new_text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos/nuevo").text
    assert "Volver a Candidatos" in new_text
    import_text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos/importar").text
    assert "Volver a Candidatos" in import_text
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/cchh/candidatos/nuevo", data={"first_name": "Ana", "last_name": "Ruiz"})
    profile_id = profile_repository.list()[0]["profile_id"]
    edit_text = client.get(f"/cchh/candidatos/{profile_id}/editar").text
    assert "Volver al listado" in edit_text
