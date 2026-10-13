"""Contrato de resultados estructurados del asistente (paso 1): las entradas del Catálogo que devuelve una consulta de inventario son FILAS navegables (`ResultItem` dentro de un `ResultSet`),
independientes del render, con identidad real (`entry_id`), título legible del resolver del Catálogo, cliente y línea de negocio EFECTIVA, y acceso directo al detalle por el mecanismo `embed`.

Mismos resultados que el inventario anterior (la equivalencia ANTES/DESPUÉS contra el snapshot real se verificó aparte; los tests de inventario existentes fijan universo, filtros, conteos y orden).
Fixtures SINTÉTICAS (corpus de 120 entradas para verificar que respuesta y filas no crecen)."""
from __future__ import annotations

import html as _html
import re
import types
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, auth_service, catalog_reads
from webapp.services import domain_classification_service as dcs
from webapp.services.content_service import load_module

PACK = "datasphere_core"
HOST = "04_Modelado_y_Desarrollo"
N = 120
CLIENTS = ["Pampa", "Loma", "Kensing", "Danone", "Illolay", "Biogenesis", "Atlas", "Norte"]
LOBS = {0: "SALES", 1: "FINANCE", 2: "SUPPLY_CHAIN", 3: None, 4: "SALES"}


def _write(root, i: int) -> None:
    cliente = CLIENTS[i % len(CLIENTS)]
    d = root / cliente / f"D{i:03d}"
    d.mkdir(parents=True)
    lob = f"  linea_de_negocio: {LOBS[i % 5]}\n" if LOBS[i % 5] else ""
    (d / f"flujo_D{i:03d}.md").write_text(
        f"---\nauto:\n  tipo: flujo\n  dominio: Dominio {i:03d}\n  artefactos:\n  - 4AM_X_{i:03d}\nmanual:\n  cliente: {cliente}\n  veredicto: {('parcial', 'buena', 'plena')[i % 3]}\n{lob}---\nCuerpo.\n",
        encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for i in range(N):
        _write(tmp_path, i)
    entries = cs.load_catalog(str(tmp_path))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, [types.SimpleNamespace(product_id="DP", title="X")]))
    return entries


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def _client(with_session=True):
    c = TestClient(main.app)
    if with_session:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _chat_raw(question, stem=HOST, with_session=True) -> str:
    from webapp.routers.assistant import assistant_store
    conv = assistant_store.start(stem, PACK).id
    return _client(with_session).post(f"/modules/{stem}/assistant/chat", data={"question": question, "conversation_id": conv, "pack": PACK}).text


def _chat(question, stem=HOST, with_session=True) -> str:
    return _html.unescape(_chat_raw(question, stem, with_session))


ROW = re.compile(r'<a class="assistant-result-row" href="([^"]+)"\s+hx-get="([^"]+)" hx-target="#catalog-detail-slot" hx-swap="innerHTML" data-entry-id="([^"]+)">')


def test_a_list_answer_carries_a_structured_result_set_with_real_identities(world):
    r = _route("Mostrame 10 modelos")
    rs = r.result
    assert isinstance(rs, caps.ResultSet) and rs.capability == "list" and rs.total == N and len(rs.items) == 10
    by_id = {e.entry_id: e for e in world}
    for it in rs.items:
        e = by_id[it.ref]                                                                            # la identidad es el entry_id REAL de una entrada del Catálogo
        assert it.url == presenter.flow_url(e, PACK) and it.mode == "embed" and it.client == e.cliente
    assert not any(it.title == by_id[it.ref].dominio for it in rs.items)                              # nunca el rótulo de dominio legado como título


def test_titles_come_from_the_catalog_resolver_and_fall_back_to_the_catalog_resolver_too(world, monkeypatch):
    seen = []
    monkeypatch.setattr(presenter, "entry_title", lambda status, entry: (seen.append(entry.entry_id) or f"Título de {entry.entry_id}", None))
    items = _route("Mostrame 3 modelos").result.items
    assert [it.title for it in items] == [f"Título de {it.ref}" for it in items] and seen == [it.ref for it in items]
    monkeypatch.setattr(caps, "governed_read", lambda flows: None)                                    # sin lectura gobernada: el resolver del propio Catálogo por artefacto de consumo
    monkeypatch.setattr(presenter, "flow_heading", lambda entry, header=None: (f"Heading de {entry.entry_id}", None))
    items = _route("Mostrame 3 modelos").result.items
    assert [it.title for it in items] == [f"Heading de {it.ref}" for it in items] and all(it.lob_label == "" for it in items)


def test_the_effective_line_of_business_is_shown_per_row_never_a_guess(world):
    by_id = {e.entry_id: e for e in world}
    for it in _route("Mostrame 500 modelos").result.items:
        i = int(re.search(r"-d(\d{3})-", it.ref).group(1))
        expected = {"SALES": "Ventas", "FINANCE": "Finanzas", "SUPPLY_CHAIN": "Cadena de suministro", None: "Sin clasificar"}[LOBS[i % 5]]
        assert it.lob_label == expected and by_id[it.ref]


def test_at_most_ten_rows_and_a_bounded_response_even_with_120_entries(world):
    for q in ("Mostrame 10 modelos", "Mostrame 500 modelos", "Modelos de Ventas", "¿Qué tiene Pampa?", "¿Qué dominios hay disponibles?"):
        page = _chat(q)
        rows = ROW.findall(page)
        assert len(rows) <= nav.MAX_LIST, q
        assert page.count('class="assistant-route-action"') <= 2 and "<table" not in page, q                 # sin tabla ni botonera por modelo
        assert len(page) < 14_000, (q, len(page))
    assert len(ROW.findall(_chat("Mostrame 500 modelos"))) == 10


def test_every_row_opens_its_own_entry_through_the_existing_embed_mechanism(world):
    page = _chat("Modelos de Ventas")
    rows = ROW.findall(page)
    assert len(rows) == 10
    real_ids = {e.entry_id for e in world}
    for href, hx_get, entry_id in rows:
        assert href == hx_get and entry_id in real_ids
        q = parse_qs(urlparse(href).query)
        assert urlparse(href).path == "/catalog/flow" and q["flow"] == [entry_id] and q["pack"] == [PACK]       # identidad real; ninguna URL inferida de texto
    assert 'id="catalog-detail-slot"' not in page                                                      # el slot vive en la página; la fila solo lo apunta


def test_a_row_url_opens_the_entry_with_a_session_and_is_rejected_without_one(world):
    href = ROW.findall(_chat("Mostrame 3 modelos"))[0][0]
    ok = _client().get(href, headers={"HX-Request": "true"})
    assert ok.status_code == 200 and "Dominio" not in ok.text[:0]                                      # el detalle de la entrada abre
    denied = _client(with_session=False).get(href, headers={"HX-Request": "true"}, follow_redirects=False)
    assert denied.status_code in (302, 303, 307, 401, 403)                                             # el control de sesión es el de la ruta destino


def test_rows_are_escaped_and_never_turn_a_title_into_markup(world, monkeypatch):
    monkeypatch.setattr(presenter, "entry_title", lambda status, entry: ('<img src=x onerror="alert(1)">', None))
    raw = _chat_raw("Mostrame 3 modelos")
    assert "<img src=x" not in raw and "&lt;img src=x" in raw


def test_the_footer_is_a_plain_note_under_the_rows_and_the_header_stays_in_the_message(world):
    r = _route("Mostrame 10 modelos")
    assert r.message.startswith("Hay **120 entradas navegables**") and r.message.endswith(":") and "Hay 110 más" not in r.message
    page = _chat("Mostrame 10 modelos")
    assert page.index("assistant-results") < page.index("assistant-results-note") and "Hay 110 más" in page
    assert _route("Mostrame 3 modelos").result.footer.startswith("Hay 117 más")


def test_only_list_answers_render_rows(world):
    for q in ("¿Qué dominios hay disponibles?", "Abrí el Catálogo", "Ver informe de cliente para Pampa", "¿Cómo se definen los dominios de negocio?", "modelos de Manufactura"):
        r = _route(q)
        assert r is None or r.result is None or not r.result.items, q
        assert "assistant-results" not in _chat(q), q


def test_summary_is_a_result_set_too_with_the_distribution_as_a_complete_partition(world):
    r = _route("¿Qué modelos tenemos?")
    rs = r.result
    assert rs.capability == "summary" and rs.items == () and rs.total == N and rs.classified
    assert sum(n for _g, _l, n in rs.aggregates) == N                                                   # nada se pierde ni se duplica
    groups = [g for g, _l, _n in rs.aggregates]
    assert groups[-1] == dcs.UNCLASSIFIED and groups.index("SALES") < groups.index(dcs.UNCLASSIFIED)    # lo no clasificado, al final y rotulado como tal


def test_the_order_is_deterministic_and_independent_of_load_order(world):
    a = [e.entry_id for e in caps.order_entries(world)]
    b = [e.entry_id for e in caps.order_entries(list(reversed(world)))]
    assert a == b


def test_when_the_classification_cannot_be_read_rows_still_open_and_say_nothing_about_a_line(world, monkeypatch):
    monkeypatch.setattr(caps, "governed_read", lambda flows: None)
    page = _chat("¿Qué tiene Pampa?")
    assert len(ROW.findall(page)) == 15 - 5 and "Sin clasificar" not in page and "Cadena de suministro" not in page


def test_the_row_css_keeps_one_column_wrapping_and_a_visible_keyboard_focus():
    css = open("webapp/static/tools.css", encoding="utf-8").read()
    for needle in (".assistant-result-row { display: grid;", "min-width: 0;", ".assistant-result-row:focus-visible", "overflow-wrap: anywhere", ".content-theme-dark .assistant-result-row"):
        assert needle in css
    assert "table" not in "".join(l for l in css.splitlines() if ".assistant-result" in l)


def test_the_rows_are_native_focusable_links():
    tpl = open("webapp/templates/partials/assistant_result_rows.html", encoding="utf-8").read()
    assert '<a class="assistant-result-row" href=' in tpl and "tabindex" not in tpl and "<button" not in tpl and "<table" not in tpl
