"""El asistente reconoce el Catálogo de datos del Hub («¿qué catálogos hay?», «abrí el Catálogo de datos») y ofrece una acción que ACTIVA la pestaña «Catálogo».

Mismo mecanismo que el Informe de Cliente (`assistant_navigation.TARGETS` + `AssistantRoute`): un destino más, `data_catalog`. La acción usa el mecanismo existente de la pestaña (estado Alpine `toolTab` si el
módulo actual ya muestra ese catálogo; si no, el link `?tab=catalogo` a un módulo que sí lo muestra). El texto lista solo las vistas REALMENTE disponibles (mismas fuentes que la pestaña, sin duplicar
inventarios). Las preguntas conceptuales/metodológicas y las ambiguas no se convierten en navegación. Fixtures SINTÉTICAS."""
from __future__ import annotations

import html as _html
import re
import types
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, auth_service, catalog_reads
from webapp.services.content_service import load_module

PACK = "datasphere_core"
HOST = "04_Modelado_y_Desarrollo"          # un módulo cuya pestaña «Catálogo» ES el Catálogo de datos
FIRST_HOST = "03_Integracion_y_Estrategia_de_Datos"
OTHER = "01_Arquitectura_y_Conectividad"   # su pestaña «Catálogo» muestra conexiones


def _flow(root, cliente, dom_dir, dominio, am):
    d = root / cliente / dom_dir
    d.mkdir(parents=True)
    (d / f"flujo_{dom_dir}.md").write_text(f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n  - {am}\nmanual:\n  cliente: {cliente}\n---\nCuerpo.\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    _flow(tmp_path, "Pampa", "Compras", "Compras", "4AM_PAMPA_COMPRAS")
    _flow(tmp_path, "Loma", "Stock", "Stock / Inventario", "4MA_STOCK")
    entries = cs.load_catalog(str(tmp_path))
    products = [types.SimpleNamespace(product_id="DP_X", title="X")]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, products))
    return types.SimpleNamespace(entries=entries, products=products, patch=monkeypatch)


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def test_the_informational_question_returns_a_short_summary_and_an_action_that_activates_the_catalog_tab(world):
    r = _route("¿Qué catálogos disponibles hay?")
    assert r is not None and "Catálogo de datos" in r.message and "4 vistas" in r.message
    for view in ("**Modelos** (2)", "**Data Products** (1)", "**Líneas de negocio**", "**Clientes** (2)"):
        assert view in r.message
    (a,) = r.actions
    assert a.label == "Abrir Catálogo" and a.activate_tab == "catalogo" and a.embed is False
    assert urlparse(a.url).path == f"/modules/{HOST}" and parse_qs(urlparse(a.url).query) == {"pack": [PACK], "tab": ["catalogo"]}


@pytest.mark.parametrize("question", ["Abrí el Catálogo de datos", "abrir el catálogo", "Mostrame el catálogo de datos", "quiero ver el catálogo", "ir al catálogo del Hub"])
def test_direct_requests_open_the_catalog(world, question):
    r = _route(question)
    assert r is not None and "Catálogo de datos" in r.message and r.actions[0].label == "Abrir Catálogo" and r.actions[0].activate_tab == "catalogo"


def test_from_a_module_whose_catalog_tab_shows_something_else_it_navigates_to_a_module_that_has_the_data_catalog(world):
    r = _route("¿Qué catálogos disponibles hay?", stem=OTHER)
    (a,) = r.actions
    assert a.activate_tab == "" and a.embed is False                                                        # no se activa una pestaña que muestra OTRO catálogo (conexiones)
    assert urlparse(a.url).path == f"/modules/{FIRST_HOST}" and parse_qs(urlparse(a.url).query)["tab"] == ["catalogo"]


def test_only_the_views_that_really_exist_are_listed(world):
    world.patch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, []))
    r = _route("¿Qué catálogos hay?")
    assert "Data Products" not in r.message and "3 vistas" in r.message
    world.patch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, []))
    r = _route("¿Qué catálogos disponibles hay?")
    assert "no tiene modelos ni Data Products" in r.message and r.actions == ()                             # catálogo vacío: explícito y sin acción


@pytest.mark.parametrize("question", ["¿Cómo diseñar un catálogo de datos?", "¿Qué es un catálogo de datos?", "para qué sirve un catálogo de datos", "explicame qué es un catálogo", "buenas prácticas de un catálogo de datos"])
def test_methodological_and_conceptual_questions_are_not_navigation(world, question):
    assert nav._data_catalog(question, PACK, HOST) is None
    r = _route(question)
    assert r is None or not any(a.activate_tab or "tab=catalogo" in a.url for a in r.actions)


@pytest.mark.parametrize("question", ["catálogo", "abrí", "mostrame algo", "ver catálogo de conexiones", "qué catálogos de seguridad hay", "abrí el catálogo del cliente Pampa", "ver el catálogo de APIs"])
def test_ambiguous_or_other_catalogs_never_invent_a_destination(world, question):
    assert nav._data_catalog(question, PACK, HOST) is None


def test_the_client_report_flow_is_preserved(world):
    r = _route("Ver informe de cliente para Pampa")
    assert r is not None and "Informe de Cliente de Pampa" in r.message
    assert r.actions[0].label == "Ver informe" and r.actions[0].embed is True and r.actions[0].activate_tab == ""
    assert nav.resolve("ver informe de cliente", PACK, HOST).message.startswith("¿De qué cliente")          # y su registro sigue primero: «catálogo» no lo captura


def test_the_offered_views_are_the_ones_the_product_has_today():
    src = (assistant_routing.__file__.rsplit("services", 1)[0] + "templates/partials/catalog_tab.html")
    template = open(src, encoding="utf-8").read()
    options = re.search(r'<select id="catalog-kind"[^>]*>(.*?)</select>', template, re.S).group(1)
    assert tuple(re.findall(r'<option value="[^"]+">([^<]+)</option>', options)) == nav.DATA_CATALOG_VIEWS      # si el producto cambia sus vistas, este destino lo detecta


def _client():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def test_the_hosting_rule_matches_the_modules_that_really_render_the_data_catalog(world):
    c = _client()
    for stem, expect_data_catalog in ((FIRST_HOST, True), (HOST, True), (OTHER, False)):
        page = c.get(f"/modules/{stem}?pack={PACK}&tab=catalogo").text
        assert ('id="catalog-kind"' in page) is expect_data_catalog, stem                                   # el selector «Catálogo de datos» solo vive en los módulos «host»
    hosts = {m.stem for m in nav._data_catalog_hosts(PACK)}
    assert HOST in hosts and FIRST_HOST in hosts and OTHER not in hosts


def test_end_to_end_the_chat_renders_an_action_that_activates_the_tab_in_page(world):
    from webapp.routers.assistant import assistant_store
    c = _client()
    conv = assistant_store.start(HOST, PACK).id
    page = _html.unescape(c.post(f"/modules/{HOST}/assistant/chat", data={"question": "¿Qué catálogos disponibles hay?", "conversation_id": conv, "pack": PACK}).text)
    assert re.search(r'<a class="assistant-route-action" href="/modules/04_Modelado_y_Desarrollo\?pack=datasphere_core&tab=catalogo" @click.prevent="toolTab=\'catalogo\'">Abrir Catálogo</a>', page)
    other = _html.unescape(c.post(f"/modules/{OTHER}/assistant/chat", data={"question": "Abrí el Catálogo de datos", "conversation_id": assistant_store.start(OTHER, PACK).id, "pack": PACK}).text)
    assert re.search(r'<a class="assistant-route-action" href="/modules/03_Integracion_y_Estrategia_de_Datos\?pack=datasphere_core&tab=catalogo">Abrir Catálogo</a>', other)   # navegación normal
    assert "toolTab" not in other.split("assistant-route-actions")[1]
