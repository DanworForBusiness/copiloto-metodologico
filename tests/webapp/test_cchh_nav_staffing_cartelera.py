"""Navegación de Capital Humano: dos grupos colapsables con el patrón del Copiloto (`button.nav-toggle` + `.nav-kicker` + `.nav-toggle-sign`
y contenedor `x-show`) — STAFFING (las seis capacidades, sin cambios de rutas, numeración ni permisos) y CARTELERA VIRTUAL (una entrada
preparatoria «Gestión de cartelera», sin lógica ni permisos nuevos)."""
from __future__ import annotations

import re

import pytest

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401

STAFFING = [("01", "Candidatos", "/cchh/candidatos"), ("02", "Búsquedas", "/cchh/busquedas"), ("03", "Entrevistas", "/cchh/entrevistas"),
            ("04", "Onboarding", "/cchh/onboarding"), ("05", "Avisos", "/cchh/publicaciones"), ("06", "Indicadores", "/cchh/indicadores")]
STAFFING_PAGES = [path for _, _, path in STAFFING] + ["/cchh/catalogos", "/cchh/candidatos/nuevo"]


def _nav(html: str) -> str:
    return re.search(r'<nav aria-label="Capital Humano"(.*?)</nav>', html, re.S).group(1)


def _group(nav: str, name: str) -> tuple[str, str]:
    """(botón del grupo, contenedor del grupo)."""
    m = re.search(rf'(<button[^>]*data-nav-group="{name}".*?</button>)\s*(<div x-show="[^"]+".*?</div>)', nav, re.S)
    assert m, name
    return m.group(1), m.group(2)


def _links(container: str) -> list[tuple[str, str, str]]:
    return re.findall(r'<a href="([^"]+)" class="module-link[^"]*">\s*<span>(\d+)</span><strong>([^<]+)</strong>', container)


@pytest.mark.parametrize("path", STAFFING_PAGES)
def test_staffing_pages_open_the_staffing_group_and_keep_the_six_entries_unchanged(path):
    html = session_client(CCHH_EMAIL).get(path).text
    nav = _nav(html)
    button, container = _group(nav, "staffing")
    assert 'class="nav-toggle sidebar-workspace-kicker"' in button and '<span class="nav-kicker">STAFFING</span>' in button
    assert 'class="nav-toggle-sign"' in button and ':aria-expanded="navStaffing"' in button
    assert 'style="display:none"' not in container.split(">")[0]                                     # grupo de la página activa: abierto
    assert [(n, label, href) for href, n, label in _links(container)] == STAFFING                    # mismas 6 entradas, numeración y rutas
    cart_button, cart_container = _group(nav, "cartelera")
    assert '<span class="nav-kicker nav-kicker--appendix">CARTELERA VIRTUAL</span>' in cart_button and "nav-toggle--appendix" in cart_button
    assert 'style="display:none"' in cart_container.split(">")[0]                                    # el otro grupo: colapsado
    assert _links(cart_container) == [("/cchh/cartelera", "01", "Gestión de cartelera")]
    assert "CAPITAL HUMANO" not in nav


def test_the_active_staffing_entry_is_highlighted():
    nav = _nav(session_client(CCHH_EMAIL).get("/cchh/entrevistas").text)
    active = re.findall(r'<a href="([^"]+)" class="module-link is-active">', nav)
    assert active == ["/cchh/entrevistas"]


def test_the_cartelera_page_opens_its_group_and_is_only_preparatory():
    response = session_client(CCHH_EMAIL).get("/cchh/cartelera")
    assert response.status_code == 200
    html = response.text
    nav = _nav(html)
    _, staffing = _group(nav, "staffing")
    _, cartelera = _group(nav, "cartelera")
    assert 'style="display:none"' in staffing.split(">")[0] and 'style="display:none"' not in cartelera.split(">")[0]
    assert re.findall(r'<a href="([^"]+)" class="module-link is-active">', nav) == ["/cchh/cartelera"]
    main = re.search(r'<main id="content" class="module-content">(.*?)</main>', html, re.S).group(1)
    assert '<p class="eyebrow">Cartelera Virtual</p>' in main and "<h1>Gestión de cartelera</h1>" in main
    assert "data-cartelera-preparatory" in main and "En preparación" in main
    for forbidden in ("<form", "hx-post", "<button", "<input", "<textarea"):                       # sin acciones: no concede nada
        assert forbidden not in main, forbidden


def test_the_cartelera_page_uses_the_existing_human_capital_boundary():
    anonymous = session_client(CCHH_EMAIL)
    anonymous.cookies.clear()
    assert anonymous.get("/cchh/cartelera").status_code in (302, 303, 307)                           # sin sesión → login
    assert session_client(OPS_A).get("/cchh/cartelera").status_code == 403                           # admitido al Hub, sin Capital Humano → 403
    assert session_client(CCHH_EMAIL).get("/cchh/cartelera").status_code == 200


def test_staffing_replaces_the_gold_heading_on_every_existing_functional_page():
    for path in STAFFING_PAGES:
        html = session_client(CCHH_EMAIL).get(path).text
        assert '<p class="eyebrow">Capital Humano' not in html, path
        assert '<p class="eyebrow">Staffing' in html, path
