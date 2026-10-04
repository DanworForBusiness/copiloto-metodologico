"""test_catalog_publication_guides.py — Guías del front de publicación del Catálogo
y el CONTRATO de enlace del renderer de guías (interno vs externo).

- las guías de Assessment (`como-assessment-modelado`) y de publicación de dominios
  (`como-publicar-dominios`) se cargan y renderizan por el mecanismo EXISTENTE
  (guides_service + /catalog/guide/<slug>);
- el how-to de Assessment ("¿Querés saber cómo funciona? Click aca →") aparece ANTES
  del form, reusando el primitivo del Catálogo (`tool-suggest__link`);
- CONTRATO DE ENLACE: un CTA INTERNO (navegación in-app) se renderiza en la MISMA
  pestaña (target="_parent") y resuelve a la ruta canónica /modules/<stem>?...&tool=<id>
  (sin hostname de producción en el YAML); un CTA EXTERNO (http/s, p. ej. SharePoint)
  conserva su apertura en pestaña nueva (target="_blank").
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from webapp import access
from webapp.services import auth_service, guides_service

STEM = "04_Modelado_y_Desarrollo"
PACK = "datasphere_core"
MODULE_URL = f"/modules/{STEM}?pack={PACK}"
ASSESS_EMBED = f"/catalog/guide/como-assessment-modelado?embed=1&pack={PACK}"
PUBLICAR_EMBED = f"/catalog/guide/como-publicar-dominios?embed=1&pack={PACK}"
# La guía de publicación "manual" conserva un recurso EXTERNO (carpeta SharePoint): sirve para probar el caso externo del contrato.
MANUAL_EMBED = f"/catalog/guide/como-publicar-dominios-manual?embed=1&pack={PACK}"


def _client(email="owner@danwor.com"):
    session = auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"})
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    return _client("owner@danwor.com")


def _anchor_with(html: str, needle: str) -> str:
    """La etiqueta <a …> que contiene `needle` (para inspeccionar su target)."""
    for m in re.finditer(r"<a\b[^>]*>", html):
        if needle in m.group(0):
            return m.group(0)
    return ""


# ───────────────────────── contextual link (Assessment) ─────────────────────────
def test_assessment_howto_link_copy_and_position(owner):
    html = owner.get(MODULE_URL).text
    link = f"/catalog/guide/como-assessment-modelado?pack={PACK}"
    assert link in html
    # Copia EXACTA pedida por el owner.
    assert "¿Querés saber cómo funciona?" in html and "Click aca" in html
    assert "Cómo podés usarlo" not in html            # la copia vieja ya no está
    # Reusa el primitivo del Catálogo (sin CSS propio) y va ANTES del form.
    assert "tool-suggest__link" in html
    assert html.index(link) < html.index('id="dsa-form"')


def test_catalog_howto_link_still_targets_publicar_guide():
    # El link how-to del Catálogo (pre-existente) sigue apuntando a `como-publicar-dominios`.
    from pathlib import Path
    tpl = Path(__file__).resolve().parents[2] / "webapp" / "templates" / "partials" / "catalog_tab.html"
    html = tpl.read_text(encoding="utf-8")
    assert "/catalog/guide/como-publicar-dominios?pack=" in html
    assert "¿Falta un Catálogo de Datos?" in html
    assert "Cómo publicarlo →" in html and "¿Falta un Catálogo de Datos?</strong> Cómo podés publicarlo" not in html


# ───────────────────────── carga / render de las guías ─────────────────────────
def test_both_guides_load_via_guides_service():
    for slug in ("como-assessment-modelado", "como-publicar-dominios"):
        g = guides_service.load(PACK, slug)
        assert g is not None, slug
        assert g.get("title"), slug
        assert g.get("steps"), slug


def test_assessment_and_publicar_are_distinct_guides():
    a = guides_service.load(PACK, "como-assessment-modelado")
    p = guides_service.load(PACK, "como-publicar-dominios")
    assert a["title"] != p["title"]
    assert re.sub(r"<[^>]+>", "", a["title"]).strip()
    assert re.sub(r"<[^>]+>", "", p["title"]).strip()


# ───────────────────────── contrato de enlace: interno vs externo ─────────────────────────
def test_assessment_step5_internal_cta_same_tab(owner):
    html = owner.get(ASSESS_EMBED).text
    anchor = _anchor_with(html, "tool=assessment")
    assert anchor, "falta el CTA interno del paso 5 (tool=assessment)"
    assert 'target="_parent"' in anchor          # misma pestaña (navega el frame del copiloto)
    assert 'target="_blank"' not in anchor       # NO pestaña nueva
    # Mecanismo canónico módulo/herramienta (no un path arbitrario, no un hostname).
    assert re.search(r'href="/modules/[^"]+\?pack=datasphere_core&(amp;)?tool=assessment"', anchor)


def test_publicar_step5_internal_cta_same_tab_to_catalog_publication_state(owner):
    html = owner.get(PUBLICAR_EMBED).text
    anchor = _anchor_with(html, "catalog_action=publish")
    assert anchor, "falta el CTA interno del paso 5 (Catálogo, estado de alta)"
    assert 'target="_parent"' in anchor
    assert 'target="_blank"' not in anchor
    assert "tool=assessment" not in anchor        # NO debe apuntar al assessment
    assert "tool=publicar" not in anchor          # ni a una pestaña/herramienta "Publicar" aparte
    assert re.search(r'href="/modules/[^"]+\?pack=datasphere_core&(amp;)?tab=catalogo&(amp;)?catalog_action=publish"', anchor)


def test_external_guide_links_keep_new_tab(owner):
    # Un recurso EXTERNO (carpeta SharePoint, en la guía manual) conserva su apertura en pestaña nueva.
    html = owner.get(MANUAL_EMBED).text
    anchor = _anchor_with(html, "sharepoint.com")
    assert anchor, "falta el link externo de SharePoint"
    assert 'target="_blank"' in anchor
    assert 'rel="noopener noreferrer"' in anchor


def test_internal_navigation_has_no_production_hostname(owner):
    for url in (ASSESS_EMBED, PUBLICAR_EMBED):
        html = owner.get(url).text
        assert "ondigitalocean" not in html, url    # el YAML no lleva hostnames de producción


def _back_href(html: str) -> str:
    m = re.search(r'<a class="catalog-back" href="([^"]+)"[^>]*>', html)
    return m.group(1) if m else ""


def test_assessment_guide_volver_returns_to_assessment_tool(owner):
    # "← Volver" de la guía de Assessment abre la herramienta Assessment (contrato canónico ?tool=assessment):
    # diagnóstico expandido + Assessment seleccionado; decisión y gestión colapsadas (lo resuelve copilotLayout).
    html = owner.get(ASSESS_EMBED).text
    href = _back_href(html)
    assert re.search(r"/modules/[^\"]+&(amp;)?tool=assessment$", href), href
    assert "tab=catalogo" not in href
    # No depende del historial del browser: navega el frame padre a una ruta explícita.
    anchor = _anchor_with(html, "catalog-back")
    assert 'target="_parent"' in anchor


def test_publicar_guide_volver_is_not_assessment(owner):
    # §4: la guía de publicación NO debe volver al Assessment; conserva su propio contexto (pestaña Catálogo).
    href = _back_href(owner.get(PUBLICAR_EMBED).text)
    assert "tool=assessment" not in href
    assert "tab=catalogo" in href


def test_back_tool_is_opt_in_per_guide():
    # El contrato es por-guía (opt-in): solo la guía de Assessment declara `back_tool`.
    assert guides_service.load(PACK, "como-assessment-modelado").get("back_tool") == "assessment"
    assert not guides_service.load(PACK, "como-publicar-dominios").get("back_tool")


def test_internal_cta_url_is_absent_in_raw_yaml():
    # El YAML declara `tool:` (no una URL): la ruta real la resuelve la app, no el contenido.
    from pathlib import Path
    guides = Path(__file__).resolve().parents[2] / "content_packs" / PACK / "guides"
    for slug in ("como-assessment-modelado", "como-publicar-dominios"):
        raw = (guides / f"{slug}.yaml").read_text(encoding="utf-8")
        assert "ondigitalocean" not in raw, slug
        assert re.search(r"\btool:\s*(assessment|catalogo)\b", raw), slug
