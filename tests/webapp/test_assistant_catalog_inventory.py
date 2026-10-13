"""Las preguntas amplias sobre el Catálogo («¿qué dominios hay?», «¿qué modelos tenemos?») se responden con un RESUMEN acotado, no con el inventario completo.

Principio: el asistente resume, ayuda a acotar y lleva al Catálogo (acción «Abrir Catálogo»); un listado solo si se pide, con tope (MAX_LIST), orden determinista y criterio explicado; los filtros
(cliente, línea de negocio) se aplican ANTES de listar. Fuentes = las del Catálogo: universo `browsable_flows`, clasificación EFECTIVA (`dcs.effective_lob_by_entry`); un modelo sin clasificación
efectiva jamás aparece bajo una línea de negocio. Fixtures SINTÉTICAS (corpus de 120 modelos para verificar que el tamaño de la respuesta y las acciones no crecen con el catálogo)."""
from __future__ import annotations

import html as _html
import re
import types

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, auth_service, catalog_reads
from webapp.services import domain_classification_service as dcs
from webapp.services.content_service import load_module

PACK = "datasphere_core"
HOST = "04_Modelado_y_Desarrollo"
N = 120
CLIENTS = ["Pampa", "Loma", "Kensing", "Danone", "Illolay", "Biogenesis", "Atlas", "Norte"]
LOB_BY_MOD5 = {0: "SALES", 1: "FINANCE", 2: "SUPPLY_CHAIN", 3: None, 4: "SALES"}      # None = sin asignación → sin clasificar
VERDICTS = ("parcial", "buena", "plena")


def _spec(i: int) -> dict:
    return {"cliente": CLIENTS[i % len(CLIENTS)], "lob": LOB_BY_MOD5[i % 5], "dom": f"Dominio {i:03d}", "verdict": VERDICTS[i % 3]}


def _write(root, i: int) -> None:
    s = _spec(i)
    d = root / s["cliente"] / f"D{i:03d}"
    d.mkdir(parents=True)
    lob = f"  linea_de_negocio: {s['lob']}\n" if s["lob"] else ""
    (d / f"flujo_D{i:03d}.md").write_text(
        f"---\nauto:\n  tipo: flujo\n  dominio: {s['dom']}\n  artefactos:\n  - 4AM_X_{i:03d}\nmanual:\n  cliente: {s['cliente']}\n  veredicto: {s['verdict']}\n{lob}---\nCuerpo.\n", encoding="utf-8")


def _world(tmp_path, monkeypatch, n: int):
    for i in range(n):
        _write(tmp_path, i)
    entries = cs.load_catalog(str(tmp_path))
    products = [types.SimpleNamespace(product_id="DP_X", title="X"), types.SimpleNamespace(product_id="DP_Y", title="Y")]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, products))
    return entries


@pytest.fixture()
def big(tmp_path, monkeypatch):
    return _world(tmp_path, monkeypatch, N)


@pytest.fixture()
def small(tmp_path, monkeypatch):
    return _world(tmp_path, monkeypatch, 9)


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def _bullets(route) -> list:
    """Filas (``ResultItem``) que el asistente muestra: el resultado ESTRUCTURADO, no el texto del mensaje (identidad real = ``entry_id``)."""
    return list(route.result.items) if route is not None and route.result is not None else []


def _i(item) -> int:
    """Índice sintético de la entrada, leído de su ``entry_id`` real (``<cliente>-d<NNN>-flujo-d<NNN>``)."""
    return int(re.search(r"-d(\d{3})-", item.ref).group(1))


def _count(pred, n=N) -> int:
    return sum(1 for i in range(n) if pred(_spec(i)))


def test_broad_questions_return_a_short_aggregate_summary_with_the_real_counts(small):
    for q in ("¿Qué dominios hay disponibles?", "¿Qué modelos tenemos?"):
        r = _route(q)
        assert r is not None and "**9 entradas navegables**" in r.message and "de **8 clientes**" in r.message and "modelos de datos" not in r.message and "dominios" not in r.message
        assert "**Por línea de negocio** (clasificación efectiva)" in r.message
        assert "Ventas: " in r.message and "**Sin clasificar**: " in r.message                       # lo no clasificado se expresa como tal
        assert "**2 Data Products**" in r.message and "otra vista del Catálogo" in r.message         # Data Products no se mezcla con los modelos
        assert _bullets(r) == []                                                                       # sin enumeración de modelos
        (a,) = r.actions
        assert a.label == "Abrir Catálogo" and a.activate_tab == "catalogo" and not a.embed             # una sola acción principal; NO una botonera por modelo


def test_summary_counts_are_the_catalogs_own(small):
    r = _route("¿Qué modelos tenemos?")
    n = 9
    sales, fin, sc, none = (_count(lambda s, k=k: s["lob"] == k, n) for k in ("SALES", "FINANCE", "SUPPLY_CHAIN", None))
    for part in (f"Ventas: {sales}", f"Finanzas: {fin}", f"Cadena de suministro: {sc}", f"**Sin clasificar**: {none}"):
        assert part in r.message
    assert sales + fin + sc + none == n                                                             # la distribución es una partición completa: nada se pierde ni se duplica


def test_with_120_models_response_size_and_actions_stay_bounded(big):
    queries = ("¿Qué dominios hay disponibles?", "¿Qué modelos tenemos?", "Mostrame 10 modelos", "Mostrame 500 modelos", "Modelos de Ventas", "¿Qué tiene Pampa?", "modelos sin clasificar")
    sizes = {}
    for q in queries:
        r = _route(q)
        assert r is not None, q
        assert len(r.actions) <= 2 and not any(a.embed and a.label.startswith(tuple(CLIENTS[1:])) for a in r.actions), q
        assert len(_bullets(r)) <= nav.MAX_LIST, q
        sizes[q] = len(r.message)
    assert sizes["¿Qué dominios hay disponibles?"] < 800 and max(sizes.values()) < 1800                # el tamaño no crece con el catálogo
    assert "El Catálogo contiene **120 entradas navegables** de **8 clientes**" in _route("¿Qué modelos tenemos?").message


def test_an_explicit_list_is_limited_ordered_deterministically_and_explains_the_criterion(big, monkeypatch):
    r = _route("Mostrame 10 modelos")
    lines = _bullets(r)
    assert len(lines) == 10 and "Hay **120 entradas navegables**" in r.message and "Muestro 10," in r.message and "top" not in r.message.lower()
    assert "ordenados por mirada Danwor (plena → parcial) y luego por cliente y nombre" in r.message
    assert "Hay 110 más" in r.result.footer and "Hay 110 más" not in r.message                       # el pie va bajo las filas, no en el encabezado
    by_id = {e.entry_id: e for e in big}
    expected = sorted(big, key=lambda e: ({"plena": 0, "buena": 1, "parcial": 2}.get(e.veredicto, 9), e.cliente.lower(), e.dominio.lower(), [c.lower() for c in e.consumo], e.entry_id))[:10]
    assert [it.ref for it in lines] == [e.entry_id for e in expected]                                # orden por mirada Danwor (plena primero), luego cliente · nombre, con el MISMO orden de siempre
    assert all(by_id[it.ref].veredicto == "plena" for it in lines)                                   # primero lo de mejor mirada
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, list(reversed(big))))
    assert [it.ref for it in _bullets(_route("Mostrame 10 modelos"))] == [it.ref for it in lines]    # mismo resultado con otro orden de carga


def test_the_requested_number_is_honored_up_to_the_cap(big):
    assert len(_bullets(_route("Mostrame 3 modelos"))) == 3
    r = _route("Mostrame 500 modelos")
    assert len(_bullets(r)) == nav.MAX_LIST and f"Muestro una selección de {nav.MAX_LIST} (el máximo por respuesta)" in r.message and "abrí el Catálogo para explorar el resto" in r.result.footer


def test_filtering_by_line_of_business_uses_only_the_effective_classification(big):
    r = _route("Modelos de Ventas")
    sales = _count(lambda s: s["lob"] == "SALES")
    assert f"Hay **{sales} entradas navegables** en la línea de negocio **Ventas** (clasificación efectiva)" in r.message
    listed = nav._catalog_inventory("Mostrame 500 modelos de Ventas", PACK, HOST)
    rows = listed.result.items
    assert rows and all(_spec(_i(it))["lob"] == "SALES" and it.catalog.lob_label == "Ventas" for it in rows)       # ninguno sin clasificar (ni de otra línea) bajo Ventas
    assert not any(_spec(_i(it))["lob"] is None for it in rows)


def test_unclassified_models_are_expressed_as_such_and_never_under_a_line(big):
    r = _route("¿Qué modelos sin clasificar hay?")
    none = _count(lambda s: s["lob"] is None)
    assert f"Hay **{none} entradas navegables** **sin clasificar** (sin línea de negocio efectiva)" in r.message
    lines = _bullets(r)
    assert lines and all(_spec(_i(it))["lob"] is None and it.catalog.lob_label == "Sin clasificar" for it in lines)
    for it in nav._catalog_inventory("Mostrame 500 modelos", PACK, HOST).result.items:
        assert (it.catalog.lob_label == "Sin clasificar") == (_spec(_i(it))["lob"] is None)                    # cada entrada muestra su clasificación efectiva real, no una supuesta


def test_a_client_filter_is_applied_first_and_offers_the_client_report(big):
    r = _route("¿Qué tiene Pampa?")
    pampa = _count(lambda s: s["cliente"] == "Pampa")
    assert f"Hay **{pampa} entradas navegables** de **Pampa**" in r.message
    assert all(it.catalog.client == "Pampa" for it in _bullets(r)) and len(_bullets(r)) == min(pampa, nav.MAX_LIST)
    assert [a.label for a in r.actions] == ["Abrir Catálogo", "Informe de Cliente · Pampa"] and r.actions[1].embed
    both = _route("Modelos de Ventas de Pampa")
    n = _count(lambda s: s["cliente"] == "Pampa" and s["lob"] == "SALES")
    assert f"Hay **{n} entradas navegables** de **Pampa** en la línea de negocio **Ventas**" in both.message     # cliente primero, después la línea de negocio


def test_the_client_report_flow_and_the_data_catalog_navigation_are_unchanged(big):
    r = _route("Ver informe de cliente para Pampa")
    assert r.actions[0].label == "Ver informe" and r.actions[0].embed and "Informe de Cliente de Pampa" in r.message
    r = _route("¿Qué catálogos disponibles hay?")
    assert "vistas" in r.message and r.actions[0].label == "Abrir Catálogo"
    r = _route("Abrí el Catálogo")
    assert r.message.startswith("Acá tenés el **Catálogo de datos**")
    r = _route("Abrí el catálogo de modelos")
    assert r.message.startswith("Acá tenés el **Catálogo de datos**") and len(r.actions) == 1       # abrir el catálogo ≠ listar modelos


@pytest.mark.parametrize("question", [
    "¿Cómo se definen los dominios de negocio?", "¿Cómo diseñar los dominios de negocio de un modelo analítico?", "¿Qué es un dominio de datos?", "buenas prácticas para modelos de datos",
    "¿Qué dominios de negocio conviene definir primero?", "¿Cuáles son los dominios de negocio típicos de una empresa?", "diferencia entre dominio y modelo",
    "para qué sirven los modelos de datos"])
def test_methodological_questions_are_not_catalog_inventory(big, question):
    assert nav._catalog_inventory(question, PACK, HOST) is None
    r = _route(question)
    assert r is None or "El Catálogo contiene" not in r.message and not any(a.label.startswith(("Informe de Cliente", "Abrir Catálogo")) for a in r.actions)


@pytest.mark.parametrize("question", [
    "¿Qué data products tenemos?", "¿Qué conexiones hay disponibles?", "qué CDS views hay", "¿Qué tiene Atlantis?", "¿Qué tiene?", "modelos de Pampa y Loma", "modelos de Ventas y Finanzas", "mostrame algo", "modelos"])
def test_other_inventories_unknown_names_and_ambiguity_never_invent_a_destination(big, question):
    assert nav._catalog_inventory(question, PACK, HOST) is None


def test_when_the_classification_cannot_be_read_it_says_so_instead_of_guessing(big, monkeypatch):
    monkeypatch.setattr(nav.caps, "governed_read", lambda flows: None)
    r = _route("¿Qué modelos tenemos?")
    assert "**120 entradas navegables**" in r.message and "no está disponible en este momento" in r.message and "Ventas: " not in r.message and "Sin clasificar" not in r.message
    r = _route("Modelos de Ventas")
    assert "no filtro por línea de negocio" in r.message and _bullets(r) == [] and len(r.actions) == 1
    r = _route("¿Qué tiene Pampa?")                                                                    # sin pedir línea de negocio, el filtro por cliente no depende de la clasificación
    assert "de **Pampa**" in r.message and _bullets(r) and all(it.catalog.lob_label == "" and it.catalog.lob_source == "" for it in _bullets(r))     # sin clasificación legible, las filas no muestran línea de negocio (ni una supuesta)
    assert all(it.title for it in _bullets(r))                                                                                      # y los títulos salen del resolver del Catálogo por artefacto de consumo


def test_one_unreadable_entry_is_reported_as_unavailable_not_as_unclassified(small, monkeypatch):
    real = dcs._effective_for
    target = small[0].entry_id

    def flaky(entry, status):
        if entry.entry_id == target:
            raise RuntimeError("lectura")
        return real(entry, status)

    monkeypatch.setattr(dcs, "_effective_for", flaky)
    r = _route("¿Qué modelos tenemos?")
    assert "**Clasificación no disponible**: 1" in r.message
    none = _count(lambda s: s["lob"] is None, 9) - (1 if _spec(0)["lob"] is None else 0)
    assert (f"**Sin clasificar**: {none}" in r.message) if none else ("Sin clasificar" not in r.message)


def test_an_empty_catalog_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, []))
    r = _route("¿Qué dominios hay disponibles?")
    assert r is not None and "todavía no tiene entradas navegables" in r.message and r.actions == ()


def test_the_older_inventory_classifier_now_gets_the_same_bounded_summary(big):
    from webapp.services.assistant_routing import _is_domain_inventory_query
    q = "cuales flujos hay implementados?"
    assert _is_domain_inventory_query(q) and nav._catalog_inventory(q, PACK, HOST) is None            # lo reconoce solo el clasificador anterior…
    r = _route(q)
    assert r is not None and r.message.startswith("El Catálogo contiene **120 entradas navegables**") and len(r.actions) == 1 and not r.actions[0].embed      # …y ya no vuelca nada


def test_registry_order_keeps_client_report_first():
    assert [t.key for t in nav.TARGETS] == ["catalog_followup", "client_report", "data_catalog", "catalog_query", "entry_assessment", "catalog_ranking", "catalog_inventory"]


def test_end_to_end_the_chat_renders_one_action_and_no_model_buttons(big):
    from webapp.routers.assistant import assistant_store
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    page = _html.unescape(c.post(f"/modules/{HOST}/assistant/chat", data={"question": "¿Qué dominios hay disponibles?", "conversation_id": assistant_store.start(HOST, PACK).id, "pack": PACK}).text)
    actions = page.split("assistant-route-actions")[1]
    assert actions.count("assistant-route-action") == 1 and "hx-get" not in actions and "toolTab='catalogo'" in actions and "Abrir Catálogo" in actions


@pytest.mark.parametrize("question", ["¿Qué dominios hay disponibles?", "¿Qué modelos tenemos?", "Mostrame 10 modelos", "Modelos de Ventas", "¿Qué tiene Pampa?", "modelos sin clasificar", "¿Qué catálogos disponibles hay?"])
def test_the_chat_renderer_never_turns_a_response_into_numbered_lists(big, question):
    """Regresión visual: ``render_llm_html`` interpreta «n)» como inicio de lista numerada y partía «Ventas (18) · …» en trozos."""
    from webapp.services.section_analysis import render_llm_html
    assert "<ol" not in render_llm_html(_route(question).message)
