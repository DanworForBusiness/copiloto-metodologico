"""test_hub_global_selector.py — selector global del Hub (botón de mosaicos, esquina superior derecha).

Diagnóstico definitivo: landing (`/onedanwor`) y selector usan la MISMA fuente (`workspaces.available_workspaces(user)`); lo que parecía «Capital Humano no aparece» era que el menú OMITÍA la
vertical actual: dentro de Capital Humano, esa vertical no figuraba. Criterio vigente: el selector muestra TODAS las verticales autorizadas, incluida la actual, identificada como activa;
nunca una no autorizada; y coincide con el landing.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import config
from webapp import workspaces
from webapp.main import app
from webapp.services import auth_service

EMAIL = "selector-probe@danwor.com"
HOME = {"copilot": "/", "operations": "/operaciones/staffing/asignaciones", "commercial": "/comercial", "human-capital": "/cchh"}
LABEL = {"copilot": "Copiloto Metodológico", "operations": "Operaciones", "commercial": "Gestión Comercial", "human-capital": "Capital Humano"}
PAGES = {  # una página real por vertical (+ variantes de Capital Humano)
    "copilot": ["/modules/00_Intro?pack=datasphere_core"],
    "operations": ["/operaciones/staffing/asignaciones", "/operaciones/busquedas"],
    "commercial": ["/comercial", "/comercial/estrategia"],
    "human-capital": ["/cchh/candidatos", "/cchh/entrevistas", "/cchh/indicadores", "/cchh/onboarding"],
}
PROFILES = {  # perfil -> verticales autorizadas (Copiloto siempre: sesión válida)
    "solo-copiloto": ["copilot"],
    "capital-humano": ["copilot", "human-capital"],
    "operaciones": ["copilot", "operations"],
    "comercial": ["copilot", "commercial"],
    "ops+cchh": ["copilot", "operations", "human-capital"],
    "todas": ["copilot", "operations", "commercial", "human-capital"],
}
ALLOWLIST_ATTR = {"operations": "OPERATIONS_ALLOWED_USERS", "commercial": "COMMERCIAL_ALLOWED_USERS", "human-capital": "HUMAN_CAPITAL_ALLOWED_USERS"}


def _client(monkeypatch, tmp_path, authorized):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    for key, attr in ALLOWLIST_ATTR.items():
        monkeypatch.setattr(auth_service, attr, EMAIL if key in authorized else "")
    c = TestClient(app, follow_redirects=True)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def _menu(html):
    block = re.search(r'<nav class="workspace-switch-menu".*?</nav>', html, re.S)
    if not block:
        return None
    anchors = re.findall(r'<a ([^>]*)>([^<]+)</a>', block.group(0))
    items = []
    for attrs, label in anchors:
        href = re.search(r'href="([^"]+)"', attrs).group(1)
        items.append({"href": href, "label": label.strip(), "active": 'class="is-active"' in attrs and 'aria-current="page"' in attrs, "has_aria": "aria-current" in attrs})
    return items


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_the_selector_lists_every_authorized_vertical_including_the_current_one_on_every_page(monkeypatch, tmp_path, profile):
    authorized = PROFILES[profile]
    client = _client(monkeypatch, tmp_path, authorized)
    expected_hrefs = [HOME[k] for k in authorized]
    for vertical in authorized:                                                          # solo se visitan páginas de verticales a las que el perfil SÍ accede
        for page in PAGES[vertical]:
            response = client.get(page)
            assert response.status_code == 200, (profile, page, response.status_code)
            items = _menu(response.text)
            if len(authorized) == 1:
                assert items is None                                                      # con una sola vertical el selector no existe (comportamiento previo)
                continue
            hrefs = [i["href"] for i in items if i["href"] != "/onedanwor"]
            assert sorted(hrefs) == sorted(expected_hrefs), (profile, page, hrefs)        # exactamente las autorizadas: ni de más ni de menos
            assert [i["href"] for i in items][-1] == "/onedanwor"                         # «Todos los espacios» sigue al final
            active = [i for i in items if i["active"]]
            assert [a["href"] for a in active] == [HOME[vertical]] and sum(i["has_aria"] for i in items) == 1, (profile, page)   # la actual, identificada, y solo ella
            assert LABEL[vertical] in [i["label"] for i in items]


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_the_selector_never_shows_an_unauthorized_vertical_and_matches_the_landing(monkeypatch, tmp_path, profile):
    authorized = PROFILES[profile]
    client = _client(monkeypatch, tmp_path, authorized)
    forbidden = {HOME[k] for k in HOME if k not in authorized}
    landing = client.get("/onedanwor")
    tiles = set(re.findall(r'<a class="glass workspace-tile" href="([^"]+)"', landing.text))
    last_page = PAGES[authorized[-1]][0]
    items = _menu(client.get(last_page).text) or []
    shown = {i["href"] for i in items} - {"/onedanwor"}
    assert not (shown & forbidden) and not (tiles & forbidden)
    if len(authorized) > 1:
        assert tiles == shown == {HOME[k] for k in authorized}                          # MISMA fuente: landing == selector


def test_the_current_vertical_is_visible_inside_capital_humano_which_is_what_the_original_report_showed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, ["copilot", "operations", "commercial", "human-capital"])
    items = _menu(client.get("/cchh/entrevistas").text)
    assert [i["label"] for i in items][:4] == [LABEL["copilot"], LABEL["operations"], LABEL["commercial"], LABEL["human-capital"]]      # orden estable de `available_workspaces`
    assert [i["label"] for i in items if i["active"]] == ["Capital Humano"]


def test_the_selector_has_no_list_of_services_of_its_own():
    """No hay una lista paralela: el macro itera únicamente lo que recibe (`available_workspaces`) y las claves de vertical solo viven en `webapp/workspaces.py`."""
    from pathlib import Path
    topbar = Path("webapp/templates/partials/platform_topbar.html").read_text(encoding="utf-8")
    assert "{% for ws in available_workspaces %}" in topbar and "Capital Humano" not in topbar.split("{% macro platform_topbar")[1] and "/cchh" not in topbar.split("{% macro platform_topbar")[1]
    assert len(workspaces._ALL_WORKSPACES) == 4
