"""test_hub_base_static_shell.py — guarda transversal del shell del Hub: sin Tailwind Play CDN.

`base.html` cargaba `cdn.tailwindcss.com`: ese script genera CSS en el navegador y lo anexa al final del <head> DESPUÉS del primer render, así que en producción (latencia) se veía un flash
claro/gris al cambiar de sección (confirmado por el owner: con el CDN bloqueado el flash desaparece). Su preflight + las 6 utilities de <html>/<body> viven ahora en `app.css` como CSS estático.
Estas guardas impiden que vuelva una dependencia del runtime de Tailwind. NO comparan hojas completas: sólo contratos puntuales.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, human_capital_service  # noqa: F401  (human_capital_service: asegura el registro de rutas CCHH)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "webapp" / "templates"
STATIC = ROOT / "webapp" / "static"
BASE = (TEMPLATES / "base.html").read_text(encoding="utf-8")
APP_CSS = (STATIC / "app.css").read_text(encoding="utf-8")

# Las 6 utilities que base.html pedía a Tailwind (inventario auditado) + utilities de runtime que no deben reaparecer en las plantillas del shell.
_FORMER_UTILITIES = ("h-full", "scroll-smooth", "min-h-full", "antialiased")
_ARBITRARY_VALUE = re.compile(r"(?<![\w-])[a-z][\w-]*-\[[^\]\s]+\]")      # sintaxis de valor arbitrario de Tailwind: bg-[#0c0c0d], w-[200px]…


def _class_tokens(html: str) -> set[str]:
    html = re.sub(r"\{#.*?#\}", "", html, flags=re.S)
    tokens: set[str] = set()
    for m in re.finditer(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", html):
        value = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", m.group(1) or m.group(2) or "")
        tokens.update(value.split())
    return tokens


def test_base_html_does_not_load_the_tailwind_play_cdn_nor_any_tailwind_runtime():
    assert "tailwind" not in BASE.lower()
    assert "cdn.tailwindcss.com" not in BASE


def test_html_and_body_no_longer_carry_tailwind_utilities_and_the_other_scripts_are_untouched():
    root_tag = re.search(r"<html\b[^>]*>", BASE).group(0)
    body_tag = re.search(r"<body\b[^>]*>", BASE).group(0)
    for cls in (*_FORMER_UTILITIES, "bg-[#0c0c0d]", "text-[#e4e4ec]"):
        assert cls not in root_tag and cls not in body_tag, cls
    # Una variable por hotfix: HTMX, Alpine, Google Fonts, el tema inicial, el ancho persistido de las columnas y el gate de telemetría siguen donde estaban.
    for needle in ("cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js", "unpkg.com/htmx.org@2.0.4", "fonts.googleapis.com/css2?family=Inter", "dw-content-theme", "dw-nav-width",
                   "DW_VERBOSE", "skip-link"):
        assert needle in BASE, needle
    for css in ("app.css", "content.css", "layout.css", "tools.css", "feedback.css"):
        assert f"path='{css}'" in BASE, css


def test_no_shell_template_or_script_relies_on_tailwind_runtime_classes():
    """Las plantillas que renderiza `base.html` (y sus parciales) no usan utilities de Tailwind: ni las 6 de antes ni valores arbitrarios `x-[...]`. Las páginas standalone (landing, callback de auth,
    guía embebida) no cargaban base.html y traen sus propias hojas: fuera de este contrato."""
    standalone = {"landing.html", "auth_callback.html", "catalog_guide_slides.html"}
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        if path.name in standalone:
            continue
        for tok in _class_tokens(path.read_text(encoding="utf-8")):
            if tok in _FORMER_UTILITIES or _ARBITRARY_VALUE.search(tok):
                offenders.append(f"{path.relative_to(ROOT)}: {tok}")
    assert not offenders, offenders


def test_app_css_carries_the_static_replacement_of_the_former_preflight_and_utilities():
    assert "Reemplaza ESTÁTICAMENTE" in APP_CSS and "cdn.tailwindcss.com" in APP_CSS          # documentado en el CSS: de dónde viene y por qué
    must_have = (
        r"\*,::before,::after\{box-sizing:border-box;border-width:0;border-style:solid",       # pseudo-elementos en border-box + defaults de borde
        r"html\{height:100%;line-height:1\.5",                                                  # h-full + line-height base del preflight
        r"body\{min-height:100%;[^}]*line-height:inherit[^}]*-webkit-font-smoothing:antialiased",  # min-h-full + antialiased + body hereda el 1.5
        r"h1,h2,h3,h4,h5,h6\{font-size:inherit;font-weight:inherit\}",
        r"button,input,optgroup,select,textarea\{font-family:inherit",
        r"ol,ul\{list-style:none;margin:0;padding:0\}",
        r"canvas,iframe,img,svg\{display:block",
        r"\[hidden\]:where\(:not\(\[hidden=until-found\]\)\)\{display:none\}",
    )
    for pattern in must_have:
        assert re.search(pattern, APP_CSS), pattern
    assert "line-height:1.65" not in APP_CSS.split("html{scroll-behavior:smooth}body{", 1)[1].split("}", 1)[0]     # el 1.65 viejo perdía contra el preflight: no debe reaparecer en body
    assert "html{scroll-behavior:smooth}" in APP_CSS                                         # scroll-smooth ya estaba en el CSS propio


@pytest.fixture
def _client(monkeypatch):
    email = "hub-static-shell-test@danwor.com"
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", email)
    c = TestClient(app, follow_redirects=True)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "S T", "first_name": "S", "family_name": "T"}))
    return c


def test_rendered_hub_pages_keep_the_ssr_shell_and_never_reference_tailwind(_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    for url in ("/cchh/busquedas", "/cchh/publicaciones"):
        html = _client.get(url).text
        assert "cdn.tailwindcss.com" not in html and "tailwind" not in html.lower(), url
        assert '<html lang="es">' in html and "<body>" in html, url
        assert 'class="module-nav' in html or "module-nav" in html, url                      # el sidebar sigue en el HTML SSR
        assert "app-shell" in html and "skip-link" in html, url
        assert "path=app.css" in html or "/static/app.css" in html, url
