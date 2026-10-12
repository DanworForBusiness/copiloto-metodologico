"""El asistente reconoce «ver informe de cliente para X» como NAVEGACIÓN y ofrece la acción real que abre el Informe de Cliente existente.

Contrato: reconoce la intención; resuelve X contra los clientes REALES del Catálogo (los del selector «Clientes»); la URL sale siempre de una entrada real (nunca del texto del usuario) y apunta a
la ruta existente `/catalog/client/assessment` (que conserva su propio control de sesión); ambigüedad, cliente inexistente o sin flujos publicados se resuelven de forma explícita; las consultas
informativas siguen su flujo normal. Mecanismo extensible (`assistant_navigation.TARGETS`), pero solo Informe de Cliente. Fixtures SINTÉTICAS."""
from __future__ import annotations

import html as _html
import re
import types
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import business_classification as BC
from tools.catalog import catalog_service as cs
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services.content_service import load_module
from tests.webapp.test_lob_surface_consistency import _bc, _status

PACK = "datasphere_core"
STEM = "01_Arquitectura_y_Conectividad"
HX = {"HX-Request": "true"}


def _flow(root, cliente, dom_dir, dominio, am):
    d = root / cliente / dom_dir
    d.mkdir(parents=True)
    (d / f"flujo_{dom_dir}.md").write_text(f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n  - {am}\nmanual:\n  cliente: {cliente}\n---\nCuerpo.\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    _flow(tmp_path, "Pampa", "Compras", "Compras", "4AM_PAMPA_COMPRAS")
    _flow(tmp_path, "Pampa", "Pagos", "Pagos", "4AM_PAMPA_PAGOS")
    _flow(tmp_path, "Loma", "Stock", "Stock / Inventario", "4MA_STOCK")
    entries = cs.load_catalog(str(tmp_path))
    by_client = {}
    for e in entries:
        by_client.setdefault(e.cliente, []).append(e)
    statuses = {e.entry_id: _status(e, _bc("SUPPLY_CHAIN"), e.artefactos[0]) for e in entries}
    conn = types.SimpleNamespace(cliente="Danone")                      # figura en el catálogo de conexiones, pero SIN flujos publicados
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_architecture_connections", lambda: (cs, [conn]))
    monkeypatch.setattr(svc, "statuses_for_entries", lambda batch: {x.entry_id: statuses[x.entry_id] for x in batch})
    return types.SimpleNamespace(entries=entries, by_client=by_client, ids={e.entry_id for e in entries})


def _route(question):
    return assistant_routing.AssistantRoutingService().route(load_module(STEM, PACK), question, PACK)


def _flow_id(url):
    return parse_qs(urlparse(url).query)["flow"][0]


def test_pampa_resolves_to_its_real_client_report_with_a_visible_action(world):
    r = _route("Ver informe de cliente para Pampa")
    assert r is not None and "Informe de Cliente de Pampa" in r.message and "2 modelos publicados" in r.message
    assert "CDS" not in r.message and "parcial" not in r.message.lower()                                      # no es una explicación de flujos
    (a,) = r.actions
    assert a.label == "Ver informe" and a.embed is True and urlparse(a.url).path == "/catalog/client/assessment"
    assert _flow_id(a.url) in {e.entry_id for e in world.by_client["Pampa"]} and parse_qs(urlparse(a.url).query)["pack"] == [PACK]


@pytest.mark.parametrize("question", ["ver informe de cliente para Pampa", "Mostrame el informe del cliente Pampa", "abrir reporte de cliente pampa", "informe de cliente Pampa", "quiero ver el informe de cliente de PAMPA"])
def test_the_navigation_intent_is_recognized_in_common_phrasings(world, question):
    r = _route(question)
    assert r is not None and r.actions and _flow_id(r.actions[0].url) in {e.entry_id for e in world.by_client["Pampa"]}


def test_any_other_client_with_a_report_works_without_hardcoding(world):
    r = _route("ver informe de cliente para Loma")
    assert "Informe de Cliente de Loma" in r.message and "1 modelo publicado" in r.message
    assert _flow_id(r.actions[0].url) == world.by_client["Loma"][0].entry_id


def test_a_nonexistent_client_is_explicit_and_offers_only_real_options(world):
    r = _route("ver informe de cliente para Acme")
    assert "No encontré un cliente «Acme»" in r.message
    assert {_flow_id(a.url) for a in r.actions} <= world.ids and {a.label for a in r.actions} == {"Informe de Cliente · Loma", "Informe de Cliente · Pampa"}
    assert not any("acme" in a.url.lower() for a in r.actions)                                              # nunca un enlace para lo que no existe


def test_a_client_known_elsewhere_but_without_published_models_has_no_report(world):
    r = _route("ver informe de cliente para Danone")
    assert "Danone" in r.message and "no hay Informe de Cliente" in r.message and r.actions == ()


def test_without_a_client_it_asks_which_one_and_with_two_it_offers_both(world):
    r = _route("ver informe de cliente")
    assert "¿De qué cliente" in r.message and {a.label for a in r.actions} == {"Informe de Cliente · Loma", "Informe de Cliente · Pampa"}
    two = _route("mostrame el informe de cliente de Pampa y de Loma")
    assert "más de un cliente" in two.message and len(two.actions) == 2


@pytest.mark.parametrize("question", ["¿qué flujos CDS implementó Pampa?", "contame sobre Pampa", "qué es el informe de cliente", "para qué sirve el informe del cliente", "cómo funciona el informe de cliente"])
def test_informational_questions_keep_their_normal_flow(world, question):
    assert nav.resolve(question, PACK) is None
    r = _route(question)
    assert r is None or not any(urlparse(a.url).path == "/catalog/client/assessment" for a in r.actions)


@pytest.mark.parametrize("question", ["ver informe de cliente para https://evil.example/x", "ver informe de cliente para <script>alert(1)</script>", "ver informe de cliente para ../../admin", "ver informe de cliente para javascript:alert(1)"])
def test_invented_urls_and_markup_never_reach_the_actions_or_the_message(world, question):
    r = _route(question)
    assert r is not None
    assert all(urlparse(a.url).path == "/catalog/client/assessment" and _flow_id(a.url) in world.ids for a in r.actions)
    assert "<" not in r.message and "http" not in r.message.lower() and "javascript" not in r.message.lower()


def _client(session=True):
    c = TestClient(main.app)
    if session:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def test_end_to_end_the_chat_returns_the_action_and_the_existing_report_opens(world):
    from webapp.routers.assistant import assistant_store
    c = _client()
    conv = assistant_store.start(STEM, PACK).id
    resp = c.post(f"/modules/{STEM}/assistant/chat", data={"question": "Ver informe de cliente para Pampa", "conversation_id": conv, "pack": PACK})
    page = _html.unescape(resp.text)
    m = re.search(r'<a class="assistant-route-action" href="([^"]+)"\s+hx-get="([^"]+)" hx-target="#catalog-detail-slot" hx-swap="innerHTML">Ver informe</a>', page)
    assert resp.status_code == 200 and m and m.group(1) == m.group(2) and "Informe de Cliente de Pampa" in page
    report = c.get(m.group(2), headers=HX)                                                                  # la acción abre la ruta EXISTENTE
    assert report.status_code == 200 and "Pampa" in _html.unescape(report.text)


def test_the_target_route_keeps_its_own_access_control_and_rejects_invented_flows(world):
    url = f"/catalog/client/assessment?flow={next(iter(world.ids))}&pack={PACK}"
    assert _client(session=False).get(url, headers=HX).status_code == 401                                   # sin sesión: la ruta destino lo rechaza
    assert _client().get(f"/catalog/client/assessment?flow=no-existe&pack={PACK}", headers=HX).status_code == 404


def test_the_mechanism_is_extensible_by_target_and_the_registered_targets_are_the_expected_ones():
    assert [t.key for t in nav.TARGETS] == ["client_report", "data_catalog", "catalog_inventory"]


# ── el flujo de la URL es solo una REFERENCIA técnica; el informe cubre todo el cliente ─────────────────────────────────────────────────────────
def test_the_reference_flow_is_deterministic_and_independent_of_load_order(world, monkeypatch):
    first = _route("ver informe de cliente para Pampa").actions[0].url
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, list(reversed(world.entries))))
    assert _route("ver informe de cliente para Pampa").actions[0].url == first                              # mismo flujo de referencia con otro orden de carga
    assert _flow_id(first) == min(world.by_client["Pampa"], key=lambda e: (e.dominio.lower(), e.entry_id)).entry_id


def test_the_intro_count_is_the_universe_the_report_shows_and_the_report_covers_every_model_of_the_client(world):
    from webapp.services import client_assessment_portfolio as portfolio
    for client in ("Pampa", "Loma"):
        r = _route(f"ver informe de cliente para {client}")
        n = int(re.search(r"sus (\d+) modelo", r.message).group(1))
        snap = portfolio.build_snapshot(world.by_client[client][0], PACK)
        assert n == snap["catalog_assets"] == len(world.by_client[client])                                    # el conteo = lo que muestra el informe
        assert {a["asset_id"] for a in snap["assets"]} == {e.artefactos[0] for e in world.by_client[client]}  # TODOS los modelos, no solo el del flujo de referencia


def test_back_closes_the_detail_when_opened_from_the_assistant_and_returns_to_the_flow_otherwise(world):
    c = _client()
    url = _route("ver informe de cliente para Pampa").actions[0].url
    from_assistant = _html.unescape(c.get(url, headers=HX).text)
    assert 'onclick="return dwCatalogBack();"' in from_assistant and "← Volver</a>" in from_assistant
    assert not re.search(r'<a class="catalog-back"[^>]*hx-get', from_assistant)                             # no vuelve al flujo de referencia que el usuario no pidió
    plain = _html.unescape(c.get(url.replace("&origin=assistant", ""), headers=HX).text)                    # entrada desde el detalle del Catálogo: comportamiento de siempre
    assert re.search(r'<a class="catalog-back"[^>]*hx-get="/catalog/flow\?[^"]*"', plain) and "dwCatalogBack" not in plain.split("catalog-actionbar__actions")[0]
    forged = _html.unescape(c.get(url.replace("origin=assistant", "origin=otro"), headers=HX).text)         # solo un valor admitido
    assert re.search(r'<a class="catalog-back"[^>]*hx-get', forged)
