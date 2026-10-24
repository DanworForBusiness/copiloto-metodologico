"""El banner de Capital Humano (mismo componente `/brand/dancing/` que Operaciones y Comercial) lleva el título «Capital Humano · Selección e Incorporación» y el subtítulo «Talento en acción | Danwor Transversal» en TODAS las pantallas de la vertical."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import config
from webapp import workspaces
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401

TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
PAGES = ("/cchh/candidatos", "/cchh/busquedas", "/cchh/entrevistas", "/cchh/onboarding", "/cchh/publicaciones", "/cchh/indicadores", "/cchh/catalogos",
         "/cchh/candidatos/nuevo", "/cchh/entrevistas/nueva", "/cchh/publicaciones/nueva", "/cchh/busquedas/nueva")


def _identity(html: str) -> dict:
    src = re.search(r'<iframe src="(/brand/dancing/[^"]+)"', html).group(1).replace("&amp;", "&")
    q = parse_qs(urlparse(src).query)
    return {"title": q["title"][0], "subtitle": q["subtitle"][0]}


def test_the_identity_constants_have_the_requested_texts_and_the_workspace_tile_is_unchanged():
    assert config.HUMAN_CAPITAL_APP_TITLE == "Capital Humano · Selección e Incorporación"
    assert config.HUMAN_CAPITAL_APP_SUBTITLE == "Talento en acción | Danwor Transversal"
    assert workspaces.HUMAN_CAPITAL.label == "Capital Humano" and workspaces.HUMAN_CAPITAL.subtitle == "Selección e Incorporación"        # tile del Hub y selector de workspaces: sin cambios


@pytest.mark.parametrize("path", PAGES)
def test_every_cchh_screen_renders_the_banner_with_the_requested_title_and_subtitle(path):
    response = session_client(CCHH_EMAIL).get(path)
    assert response.status_code == 200, path
    assert _identity(response.text) == {"title": "Capital Humano · Selección e Incorporación", "subtitle": "Talento en acción | Danwor Transversal"}


def test_no_cchh_template_builds_its_banner_from_the_workspace_label_anymore():
    for template in sorted((TEMPLATES / "human_capital").glob("*.html")):
        text = template.read_text(encoding="utf-8")
        if "/brand/dancing/" in text:
            assert "config.HUMAN_CAPITAL_APP_TITLE" in text and "current_workspace.subtitle | urlencode" not in text and "current_workspace.label | urlencode" not in text, template.name


def test_other_verticals_keep_their_own_banners():
    ops = session_client(CCHH_EMAIL)
    assert config.OPERATIONS_APP_TITLE != config.HUMAN_CAPITAL_APP_TITLE and config.COMMERCIAL_APP_TITLE != config.HUMAN_CAPITAL_APP_TITLE
    assert (config.OPERATIONS_APP_TITLE, config.OPERATIONS_APP_SUBTITLE) == ("Gestión Inteligente · Operaciones", "Analítica y gestión operativa | Danwor Transversal")
    assert (config.COMMERCIAL_APP_TITLE, config.COMMERCIAL_APP_SUBTITLE) == ("Gestión Comercial · CRM Danwor", "Gestión y analítica comercial | Danwor Transversal")
