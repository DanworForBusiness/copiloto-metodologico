"""test_hub_tile_capital_humano.py — contenido aprobado del tile «Capital Humano» del selector del Hub (`platform.py::_WORKSPACE_CARD_COPY["human-capital"]`).

Valida el copy definitivo (título, subtítulo, descripción y las tres capacidades), que el selector lo renderiza tal cual con el render común y que NO se anuncia Indicadores mientras no esté publicado en main.
"""
from __future__ import annotations

import re
from html import unescape

from fastapi.testclient import TestClient

from webapp import workspaces
from webapp.main import app
from webapp.routers import platform as platform_router
from webapp.services import auth_service

TILE = platform_router._WORKSPACE_CARD_COPY["human-capital"]
DESCRIPTION = "Un espacio para gestionar el ciclo de selección: banco de CVs, búsquedas, entrevistas e incorporación."
FEATURES = ["Banco de CVs y candidatos", "Búsquedas y entrevistas", "Onboarding y avisos"]


def test_the_approved_copy_is_the_tile_definition():
    assert TILE["title"] == "Capital Humano" and TILE["accent"] == "Selección e Incorporación" and TILE["eyebrow"] is None
    assert TILE["description"] == DESCRIPTION
    assert TILE["features"] == FEATURES                                                       # exactamente tres capacidades, en este orden
    assert TILE["accent"] == workspaces.HUMAN_CAPITAL.subtitle                                # el subtítulo del workspace no cambia


def test_the_tile_follows_the_pattern_of_the_other_workspace_tiles():
    others = [v for k, v in platform_router._WORKSPACE_CARD_COPY.items() if k != "human-capital"]
    assert all(len(v["features"]) == 3 for v in others) and len(TILE["features"]) == 3
    words = len(TILE["description"].split())
    assert min(len(v["description"].split()) for v in others) - 4 <= words <= max(len(v["description"].split()) for v in others) + 2    # extensión comparable a los demás tiles


def test_the_tile_does_not_announce_unpublished_capabilities():
    text = " ".join([TILE["title"], TILE["accent"], TILE["description"], *TILE["features"]]).lower()
    assert "indicador" not in text and "métrica" not in text                                   # Indicadores vive solo en una branch: no se anuncia hasta publicarse
    assert "empezando por" not in text                                                        # ya no es «solo el banco de CVs»


def test_the_selector_renders_the_tile_with_the_common_markup(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(platform_router, "available_workspaces", lambda user: [workspaces.COPILOT, workspaces.HUMAN_CAPITAL])
    client = TestClient(app)
    client.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "tile-hc@danwor.com", "name": "T", "first_name": "T", "family_name": "T"}))
    response = client.get("/onedanwor")
    assert response.status_code == 200
    # Sin parser HTML externo (como el resto de los tests del landing): se aísla el <a> del tile por su href y se verifica el markup común de `platform_selector.html`.
    tiles = re.findall(r'<a class="glass workspace-tile" href="([^"]+)">(.*?)</a>', response.text, re.S)
    block = next(inner for href, inner in tiles if href == workspaces.HUMAN_CAPITAL.home_url)
    assert "<h2>Capital Humano<span>Selección e Incorporación</span></h2>" in block                    # título blanco + acento gold: MISMO componente que las otras cards
    assert re.findall(r"<p>(.*?)</p>", block, re.S) == [DESCRIPTION]
    items = re.findall(r'<li><span class="access-check">&#10003;</span>(.*?)</li>', block, re.S)
    assert [unescape(i).strip() for i in items] == FEATURES
    assert block.count("<li>") == 3 and "<ul class=\"access-list\">" in block
