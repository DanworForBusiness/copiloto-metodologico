"""Paso 3 — continuidad conversacional: contexto ESTRUCTURADO de la última consulta, seguimientos con una gramática acotada, referencias posicionales y paginación.

Lo que fijan estos tests: el estado guarda solo la consulta, las identidades `(kind, ref)` mostradas y una huella (jamás URLs, valores ni copias del Catálogo) y pertenece a UN usuario y a ESTA conversación;
vence a los 15 minutos; los resultados se RECALCULAN con datos vigentes antes de paginar, filtrar o abrir una referencia; una referencia posicional solo se resuelve si el conjunto ORDENADO completo que originó
la lista (y los atributos que determinan su filtro y orden) sigue siendo el mismo; un seguimiento sin contexto vigente produce una aclaración determinista (nunca el LLM libre ni referencias inventadas); una
consulta nueva reemplaza el contexto y las demás rutas lo borran; la paginación no duplica ni omite entradas y no convierte un empate en un ranking. Fixtures SINTÉTICAS (las del paso 2)."""
from __future__ import annotations

import dataclasses
import html as _html
import re
import time
import types

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_context as qc
from webapp.services import assistant_indicators as ind
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, auth_service, catalog_reads
from webapp.services import domain_classification_service as dcs
from webapp.services.content_service import load_module
from tests.webapp.test_assistant_indicators import CLIENTS, HOST, PACK, _expected, _spec, _status, _write      # helpers sintéticos del paso 2

WORLD_N = 90                                                                                          # más entradas que en el paso 2: cada línea de negocio y cliente da varias páginas de diez


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for i in range(WORLD_N):
        _write(tmp_path, i)
    entries = cs.load_catalog(str(tmp_path))
    index = {}
    for e in entries:
        i = int(re.search(r"-d(\d{3})-", e.entry_id).group(1))
        index[e.entry_id] = i
        s = _spec(i)
        e.portabilidad_bdc = ({"score": s["score"], "version": s["version"], "clasificacion": "plena"} if s["score"] is not None else {"clasificacion": "no_evaluable"})
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, [types.SimpleNamespace(product_id="DP", title="X")]))

    def read(flows):
        statuses = {e.entry_id: _status(e, index[e.entry_id]) for e in flows}
        return caps.Governed(statuses, dcs.effective_lob_by_entry(flows, statuses))
    monkeypatch.setattr(caps, "governed_read", read)
    return types.SimpleNamespace(entries=entries, index=index, by_id={e.entry_id: e for e in entries})

TYPES_KEY = lambda s: (-s["score"],) if s["score"] is not None and s["version"] else None            # orden de «mejor consistencia de tipos» (independiente del código bajo prueba)
COVERAGE_KEY = lambda s: (-s["strict"],) if s["state"] == "CURRENT" else None


def _store():
    from webapp.routers.assistant import assistant_store
    return assistant_store


class Chat:
    """Una persona conversando por el chat REAL (HTTP): misma conversación, mismo usuario, estado en el almacén real."""

    def __init__(self, email="ana@d.com", cid=None):
        self.client = TestClient(main.app)
        self.client.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "A", "first_name": "A", "family_name": "U"}))
        self.email = email
        self.cid = cid or _store().start(HOST, PACK).id

    def ask(self, question: str) -> "Reply":
        raw = self.client.post(f"/modules/{HOST}/assistant/chat", data={"question": question, "conversation_id": self.cid, "pack": PACK}).text
        return Reply(raw)

    @property
    def state(self):
        return _store()._conversations[self.cid].query_state


class Reply:
    def __init__(self, raw: str):
        self.raw = raw
        body = re.search(r'section-analysis-output">(.*?)</div>', raw, re.S)
        self.text = _html.unescape(re.sub(r"<[^>]+>", " ", body.group(1))) if body else ""
        self.text = re.sub(r"\s+", " ", self.text).strip()
        self.rows = [{k: _html.unescape(v) for k, v in re.findall(r'([\w-]+)="([^"]*)"', tag)} for tag in re.findall(r'<a class="assistant-result-row"([^>]*)>', raw)]
        self.refs = [r["data-ref"] for r in self.rows]
        self.asks = [(_html.unescape(re.search(r'name="question" value="([^"]*)"', b).group(1)), re.search(r"<button[^>]*>(.*?)</button>", b, re.S).group(1).strip())
                     for b in re.findall(r'<form class="assistant-route-ask".*?</form>', raw, re.S)]
        self.note = _html.unescape(re.sub(r"<[^>]+>", "", (re.search(r'assistant-results-note">(.*?)</p>', raw, re.S) or [None, ""])[1]))
        self.llm = "Respuesta generada con IA" in raw


def _full_order(world, key, order="best_first", predicate=lambda s: True):
    return _expected(world, key, order, predicate=predicate)


def _fresh_titles(world, refs):
    by_id = {e.entry_id: e for e in world.entries}
    return [by_id[r] for r in refs]


# ── 1. el circuito pedido, de punta a punta por el chat real ─────────────────────────────────────────────────────────────────────────────────────
def test_the_requested_circuit_rank_then_filter_then_next_page_then_open_the_second(world):
    chat = Chat()
    first = chat.ask("¿Qué modelos tienen mayor consistencia de tipos?")
    assert first.refs == _full_order(world, TYPES_KEY)[:10] and chat.state.spec.indicator == "types" and chat.state.spec.offset == 0
    ventas = chat.ask("¿Y los de Ventas?")
    full_sales = _full_order(world, TYPES_KEY, predicate=lambda s: s["lob"] == "SALES")
    assert ventas.refs == full_sales[:10] and "línea de negocio **Ventas**".replace("**", "") in ventas.text and "Consistencia de tipos" in ventas.text      # filtro nuevo, indicador conservado
    page2 = chat.ask("Mostrame los siguientes")
    assert page2.refs == full_sales[10:20] and "Página 2 de" in page2.text and chat.state.spec.offset == 10
    opened = chat.ask("Abrí el segundo")
    (row,) = opened.rows
    assert row["data-kind"] == caps.CATALOG_ENTRY and row["data-ref"] == page2.refs[1] and row["hx-trigger"] == "load, click"           # el segundo de la ÚLTIMA lista visible
    by_id = {e.entry_id: e for e in world.entries}
    assert row["href"] == presenter.flow_url(by_id[page2.refs[1]], PACK) == row["hx-get"] and "segundo" in opened.text and not opened.llm
    assert chat.state.spec.offset == 10                                                                                                  # abrir no cambia la lista: «el tercero» sigue valiendo
    assert chat.ask("Abrí el tercero").refs == [page2.refs[2]]


# ── 2. paginación ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_pagination_walks_the_whole_ordered_set_without_duplicates_or_omissions(world):
    chat = Chat()
    seen = chat.ask("¿Qué entradas tienen mejor consistencia de tipos?").refs
    while True:
        page = chat.ask("Mostrame los siguientes")
        if not page.refs:
            assert "Ya viste todas las entradas" in page.text
            break
        assert len(page.refs) <= 10
        seen += page.refs
    expected = _full_order(world, TYPES_KEY)
    assert seen == expected and len(set(seen)) == len(seen)                                                                              # sin duplicados ni omisiones, en el orden del indicador


def test_previous_pages_come_back_and_the_first_page_is_declared(world):
    chat = Chat()
    p1 = chat.ask("¿Qué entradas tienen mejor consistencia de tipos?")
    p2 = chat.ask("Mostrame los siguientes")
    assert chat.ask("Los anteriores").refs == p1.refs and chat.ask("Los anteriores").text == "Ya estás en la primera página."
    assert p2.refs != p1.refs and not set(p1.refs) & set(p2.refs)


def test_a_tie_wider_than_ten_is_a_tie_on_every_page_and_never_a_positional_ranking(world, monkeypatch):
    monkeypatch.setitem(ind.BY_ID, "coverage", dataclasses.replace(ind.BY_ID["coverage"], measure=lambda e, g, p: ind.Measurement(ind.OK, 1.0, "100.0%", caps.Evidence("Assessment · cobertura estricta"))))
    chat = Chat()
    n = len(world.entries)
    pages = [chat.ask("¿Qué entradas tienen mejor cobertura estricta?")]
    while len(pages) < n // 10:
        pages.append(chat.ask("Mostrame los siguientes"))
    assert f"Empatan: {n} entradas con «100.0%»" in pages[0].note and "continúan el empate" not in pages[0].note
    for page in pages[1:]:
        assert "continúan el empate de «100.0%»" in page.note and "no implica un ranking" in page.note
    assert all("El corte cae dentro de un empate" in page.note for page in pages[:-1]) and "El corte cae dentro de un empate" not in pages[-1].note        # la última página ya no corta nada
    assert all(len(page.refs) == 10 for page in pages) and len({r for page in pages for r in page.refs}) == n                              # todas las entradas, una sola vez
    for page in pages:
        assert "<ol" not in page.raw and not re.search(r"assistant-result-title\">\s*(?:#?\d+[.)°º])", page.raw) and "posición" not in page.raw.lower()


def test_page_size_follows_the_query_and_never_exceeds_ten(world):
    chat = Chat()
    first = chat.ask("Mostrame 3 modelos")
    assert len(first.refs) == 3 and chat.state.spec.limit == 3
    nxt = chat.ask("Mostrame los siguientes")
    assert len(nxt.refs) == 3 and not set(first.refs) & set(nxt.refs) and "Página 2 de" in nxt.text
    big = Chat().ask("Mostrame 500 modelos")
    assert len(big.refs) == 10


def test_summary_has_no_pages_and_asking_for_next_says_so(world):
    chat = Chat()
    chat.ask("¿Qué modelos tenemos?")
    assert "El resumen no tiene páginas" in chat.ask("Mostrame los siguientes").text and chat.state.spec.capability == "summary"


# ── 3. cambios de filtro e indicador (gramática derivada de los contratos) ──────────────────────────────────────────────────────────────────
def test_filter_changes_replace_only_their_own_kind_and_keep_the_indicator(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor consistencia de tipos?")
    r = chat.ask("Solo Pampa")
    assert chat.state.spec.client == "Pampa" and chat.state.spec.indicator == "types" and set(_spec_clients(world, r.refs)) == {"Pampa"}
    r = chat.ask("¿Y los de Finanzas?")
    assert chat.state.spec.client == "Pampa" and chat.state.spec.lob == "FINANCE"                                                          # cliente conservado, línea nueva
    r = chat.ask("Sin clasificar")
    assert chat.state.spec.lob == dcs.UNCLASSIFIED and chat.state.spec.client == "Pampa"
    assert r.refs == _full_order(world, TYPES_KEY, predicate=lambda s: s["client"] == "Pampa" and s["lob"] is None)[:10]


def _spec_clients(world, refs):
    by_id = {e.entry_id: e for e in world.entries}
    return [by_id[r].cliente for r in refs]


def test_a_filter_over_an_inventory_list_stays_an_inventory_list_and_over_a_summary_becomes_one(world):
    chat = Chat()
    chat.ask("Mostrame 10 modelos")
    chat.ask("¿Y los de Loma?")
    assert chat.state.spec.capability == "list" and chat.state.spec.client == "Loma" and chat.state.spec.indicator is None
    chat2 = Chat()
    chat2.ask("¿Qué modelos tenemos?")
    r = chat2.ask("¿Y los de Ventas?")
    assert chat2.state.spec.capability == "list" and chat2.state.spec.lob == "SALES" and r.rows


def test_indicator_changes_keep_the_filters_reset_the_page_and_follow_the_direction_words(world):
    chat = Chat()
    chat.ask("¿Qué entradas de Pampa tienen mejor consistencia de tipos?")
    r = chat.ask("¿Y por confianza?")
    assert chat.state.spec.indicator == "confidence" and chat.state.spec.client == "Pampa" and chat.state.spec.offset == 0 and chat.state.spec.order == "best_first"
    r = chat.ask("Ordenalos por cobertura")
    assert chat.state.spec.indicator == "coverage"
    r = chat.ask("¿Y por peor cobertura estricta?")
    assert chat.state.spec.order == "worst_first" and r.refs == _full_order(world, COVERAGE_KEY, "worst_first", predicate=lambda s: s["client"] == "Pampa")[:10]


def test_an_indicator_follow_up_on_an_inventory_list_becomes_a_ranking_of_the_same_scope(world):
    chat = Chat()
    chat.ask("Modelos de Ventas")
    r = chat.ask("¿Y por cobertura?")
    assert chat.state.spec.capability == "rank" and chat.state.spec.lob == "SALES" and r.refs == _full_order(world, COVERAGE_KEY, predicate=lambda s: s["lob"] == "SALES")[:10]


def test_filter_and_indicator_can_change_together_and_the_volume_indicator_still_asks_about_direction(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    chat.ask("¿Y los de Ventas por confianza?")
    assert (chat.state.spec.lob, chat.state.spec.indicator) == ("SALES", "confidence")
    r = chat.ask("¿Y por mejor casos a revisar?")
    assert "mide cantidad, no calidad" in r.text and {q for q, _label in r.asks} and not r.rows


def test_two_entities_of_the_same_kind_are_not_chosen_for_the_user_and_the_context_is_kept(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    before = chat.state
    r = chat.ask("¿Y los de Pampa y Loma?")
    assert "un solo cliente y una sola línea de negocio" in r.text and chat.state.spec == before.spec


def test_a_pending_metric_clarification_is_completed_by_naming_the_metric(world):
    chat = Chat()
    chat.ask("¿Cuál es el dominio con mejor scoring?")
    assert chat.state.spec.capability == "pending"
    r = chat.ask("Por confianza")
    assert chat.state.spec.capability == "rank" and chat.state.spec.indicator == "confidence" and r.rows
    chat2 = Chat()
    chat2.ask("¿Qué modelos de Ventas con mayores riesgos?")
    r2 = chat2.ask("Los siguientes")
    assert "Todavía no hay una lista para recorrer" in r2.text and r2.asks and not r2.rows


# ── 4. reinicio y reemplazo del contexto ─────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("phrase", ["Empezar de nuevo", "Nueva consulta", "Empezá de nuevo", "Desde cero", "Olvidá lo anterior"])
def test_an_explicit_restart_clears_the_context(world, phrase):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    assert chat.state is not None
    r = chat.ask(phrase)
    assert chat.state is None and "empezamos de nuevo" in r.text and r.asks
    assert "No tengo una consulta previa vigente" in chat.ask("Mostrame los siguientes").text


def test_a_new_standalone_query_replaces_the_context_instead_of_inheriting_it(world):
    chat = Chat()
    chat.ask("¿Qué entradas de Pampa tienen mejor consistencia de tipos?")
    for question, expect in (("¿Qué tiene Loma?", dict(capability="list", client="Loma", indicator=None)), ("Modelos de Ventas", dict(capability="list", client=None, lob="SALES", indicator=None)),
                             ("¿Qué entradas tienen mejor cobertura estricta?", dict(capability="rank", client=None, lob=None, indicator="coverage"))):
        chat.ask(question)
        for field, value in expect.items():
            assert getattr(chat.state.spec, field) == value, (question, field)
        chat.ask("¿Qué entradas de Pampa tienen mejor consistencia de tipos?")                                                           # vuelve a un contexto con filtros


@pytest.mark.parametrize("question", ["Ver informe de cliente para Pampa", "Abrí el Catálogo", "¿Qué documentación tenemos de CDS?", "¿Cómo se definen los dominios de negocio?"])
def test_other_destinations_clear_the_context_so_a_follow_up_never_inherits_foreign_filters(world, question):
    chat = Chat()
    chat.ask("¿Qué entradas de Pampa tienen mejor consistencia de tipos?")
    assert chat.state is not None
    chat.ask(question)
    assert chat.state is None
    assert "No tengo una consulta previa vigente" in chat.ask("¿Y los de Ventas?").text


def test_the_free_llm_turn_also_clears_the_context(world, monkeypatch):
    from webapp.routers.assistant import assistant_chat_service
    import webapp.services.assistant_service as svc_mod

    class Fake:
        def __init__(self, **_): self.chat = self
        completions = property(lambda self: self)
        def create(self, **_):
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]})()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(svc_mod, "_catalog_grounding", lambda _q: "")
    monkeypatch.setattr(assistant_chat_service, "client_factory", Fake)
    monkeypatch.setattr(assistant_chat_service, "execution_mode", "produccion")
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    reply = chat.ask("qwxz plugh frobnicate")
    assert reply.llm and chat.state is None


# ── 5. validez: usuario, conversación, TTL ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_state_belongs_to_one_user_and_one_conversation(world):
    ana = Chat("ana@d.com")
    ana.ask("¿Qué entradas tienen mejor cobertura estricta?")
    beto = Chat("beto@d.com", cid=ana.cid)                                                                                               # mismo id de conversación, otra persona
    r = beto.ask("Mostrame los siguientes")
    assert "No tengo una consulta previa vigente" in r.text and not r.rows
    assert ana.state is not None and ana.state.user_sub == "ana@d.com"                                                                   # y el estado de Ana ni se leyó ni se borró
    assert ana.ask("Mostrame los siguientes").rows
    other_conversation = Chat("ana@d.com")                                                                                               # otra conversación de la misma persona
    assert "No tengo una consulta previa vigente" in other_conversation.ask("Mostrame los siguientes").text


def test_the_context_expires_after_fifteen_minutes_and_says_so(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    store = _store()
    conv = store._conversations[chat.cid]
    conv.query_state = dataclasses.replace(conv.query_state, updated_at=time.time() - (qc.TTL_SECONDS + 5))
    r = chat.ask("Mostrame los siguientes")
    assert "Pasaron más de 15 minutos" in r.text and r.asks and not r.rows and chat.state is None
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    store._conversations[chat.cid].query_state = dataclasses.replace(chat.state, updated_at=time.time() - (qc.TTL_SECONDS - 30))        # dentro del TTL
    assert chat.ask("Mostrame los siguientes").rows


def test_the_ttl_runs_from_the_last_activity(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    store = _store()
    conv = store._conversations[chat.cid]
    conv.query_state = dataclasses.replace(conv.query_state, updated_at=time.time() - (qc.TTL_SECONDS - 5))
    chat.ask("Abrí el primero")                                                                                                          # actividad: renueva el instante
    assert time.time() - chat.state.updated_at < 30


def test_context_status_boundaries_are_pure_and_explicit():
    st = qc.QueryState(qc.QuerySpec("rank"), (), "fp", "u1", 100.0)
    assert qc.QueryContext(st, "u1", 100.0 + qc.TTL_SECONDS).status() == qc.LIVE
    assert qc.QueryContext(st, "u1", 100.0 + qc.TTL_SECONDS + 0.1).status() == qc.EXPIRED
    assert qc.QueryContext(st, "u2", 100.0).status() == qc.MISSING and qc.QueryContext(None, "u1", 100.0).status() == qc.MISSING and qc.QueryContext(st, "", 100.0).status() == qc.MISSING
    assert qc.QueryContext(st, "u1", 50.0).status() == qc.EXPIRED                                                                        # un reloj hacia atrás tampoco da un estado vigente


def test_the_state_stores_only_the_query_identities_and_a_fingerprint_never_urls_or_values(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor consistencia de tipos?")
    st = chat.state
    assert {f.name for f in dataclasses.fields(qc.QueryState)} == {"spec", "refs", "fingerprint", "user_sub", "updated_at"}
    # 4A: se sumaron SOLO parámetros de la consulta (niveles pedidos, concepto de estado, cantidad, agrupación); jamás un valor medido
    assert {f.name for f in dataclasses.fields(qc.QuerySpec)} == {"capability", "client", "lob", "indicator", "order", "limit", "offset", "levels", "state", "count", "group_by"}
    allowed = {"capability": str, "client": (str, type(None)), "lob": (str, type(None)), "indicator": (str, type(None)), "order": str, "limit": int, "offset": int,
               "levels": tuple, "state": (str, type(None)), "count": bool, "group_by": (str, type(None))}
    assert all(isinstance(getattr(st.spec, name), kind) for name, kind in allowed.items())
    assert all(isinstance(level, str) for level in qc.QuerySpec("rank", levels=("BUENA", "ADECUADA")).levels)
    assert len(st.refs) == 10 and all(isinstance(r, tuple) and len(r) == 2 and r[0] == caps.CATALOG_ENTRY for r in st.refs)
    dumped = repr(st)
    assert "/catalog/" not in dumped and "100/100" not in dumped and "http" not in dumped


def test_store_operations_are_scoped_by_user(world):
    store = _store()
    cid = store.start(HOST, PACK).id
    state = qc.QueryState(qc.QuerySpec("summary"), (), "", "ana", 1.0)
    store.set_query_state(cid, HOST, PACK, state)
    assert store.query_state(cid, HOST, PACK, "ana") == state and store.query_state(cid, HOST, PACK, "beto") is None and store.query_state(cid, HOST, PACK, "") is None
    store.clear_query_state(cid, HOST, PACK, "beto")
    assert store.query_state(cid, HOST, PACK, "ana") == state                                                                            # otro usuario no borra
    store.touch_query_state(cid, HOST, PACK, "beto", 99.0)
    assert store.query_state(cid, HOST, PACK, "ana").updated_at == 1.0
    store.touch_query_state(cid, HOST, PACK, "ana", 99.0)
    assert store.query_state(cid, HOST, PACK, "ana").updated_at == 99.0
    store.clear_query_state(cid, HOST, PACK, "ana")
    assert store.query_state(cid, HOST, PACK, "ana") is None
    with pytest.raises(KeyError):
        store.query_state("inexistente", HOST, PACK, "ana")


# ── 6. referencias posicionales ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("phrase,position", [("Abrí el primero", 1), ("Mostrame el tercero", 3), ("abrí el 4", 4), ("el quinto", 5), ("Abrime el décimo", 10), ("Abrí el último", 10), ("Ver el segundo modelo", 2)])
def test_valid_positional_references_open_the_entry_at_that_visible_position(world, phrase, position):
    chat = Chat()
    page = chat.ask("¿Qué entradas tienen mejor consistencia de tipos?")
    r = chat.ask(phrase)
    assert r.refs == [page.refs[position - 1]] and r.rows[0]["hx-trigger"] == "load, click" and not r.llm


def test_a_position_outside_the_visible_list_is_declared_not_guessed(world):
    chat = Chat()
    chat.ask("¿Qué entradas de Pampa tienen más casos a revisar?")
    n = len(chat.state.refs)
    r = chat.ask("Abrí el décimo")
    assert (n >= 10 and r.refs) or (n < 10 and f"tiene {n} entrada" in r.text and not r.rows)
    chat3 = Chat()
    chat3.ask("Mostrame 3 modelos")
    out = chat3.ask("Abrí el octavo")
    assert "La lista visible tiene 3 entradas" in out.text and not out.rows


@pytest.mark.parametrize("phrase", ["Abrí el undécimo", "Abrí el duodécimo", "Abrime el vigésimo", "Abrí el 11", "Mostrame el 15"])
def test_a_position_past_the_page_is_declared_and_never_falls_to_the_free_llm(world, phrase):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    r = chat.ask(phrase)
    assert "La lista visible tiene 10 entradas" in r.text and not r.rows and not r.llm and "Simulación" not in r.text
    assert chat.state is not None                                                                                                        # el contexto se conserva


def test_this_one_opens_only_when_it_is_unambiguous(world):
    chat = Chat()
    chat.ask("Mostrame 1 modelos")
    only = chat.ask("Abrí ese")
    assert len(only.rows) == 1 and only.refs == [chat.state.refs[0][1]]
    chat2 = Chat()
    chat2.ask("¿Qué entradas tienen mejor consistencia de tipos?")
    many = chat2.ask("Abrí ese")
    assert "Hay 10 entradas visibles" in many.text and not many.rows and chat2.state is not None


def test_the_reference_resolves_against_the_last_page_actually_shown(world):
    chat = Chat()
    p1 = chat.ask("¿Qué entradas tienen mejor consistencia de tipos?")
    p2 = chat.ask("Mostrame los siguientes")
    assert chat.ask("Abrí el primero").refs == [p2.refs[0]] != [p1.refs[0]]


def _mutating_reader(monkeypatch, mutate):
    original = caps.governed_read

    def read(flows):
        g = original(flows)
        mutate(g)
        return g
    monkeypatch.setattr(caps, "governed_read", read)


def _set_strict(entry_id, value):
    """Mutación de la cobertura de UNA entrada, tolerante a lecturas parciales (la apertura de una referencia lee solo esa entrada)."""
    def mutate(g):
        st = g.statuses.get(entry_id)
        if st is not None and st.profile:
            st.profile["summary"]["coverage"]["strict"] = value
    return mutate


def _someone_in_sales_not_on_the_first_page(world, shown_refs):
    return next(e for e in world.entries if e.entry_id not in shown_refs and _spec(world.index[e.entry_id])["state"] == "CURRENT" and _spec(world.index[e.entry_id])["lob"] == "SALES")


@pytest.mark.parametrize("what", ["metric_of_a_shown_entry", "metric_of_an_entry_not_shown", "classification", "universe"])
def test_if_the_ordered_set_or_its_determinants_changed_the_reference_is_refused_instead_of_opening_another_entry(world, monkeypatch, what):
    chat = Chat()
    page = chat.ask("¿Qué entradas de Ventas tienen mejor cobertura estricta?")
    shown = set(page.refs)
    if what == "metric_of_a_shown_entry":
        _mutating_reader(monkeypatch, _set_strict(page.refs[0], 0.5))
    elif what == "metric_of_an_entry_not_shown":
        _mutating_reader(monkeypatch, _set_strict(_someone_in_sales_not_on_the_first_page(world, shown).entry_id, 0.5))                  # está en el conjunto ordenado aunque no se vea
    elif what == "classification":
        victim = page.refs[3]
        _mutating_reader(monkeypatch, lambda g: g.eff[victim].update({"group": "FINANCE"}) if victim in g.eff else None)                 # el filtro por línea de negocio cambió su pertenencia
    else:
        gone = page.refs[2]
        remaining = [e for e in world.entries if e.entry_id != gone]
        monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (world_cs(), remaining))
    r = chat.ask("Abrí el segundo")
    assert "La lista cambió desde que te la mostré" in r.text and not r.rows and r.asks and chat.state is None
    assert r.asks[0][1] == "Volver a consultar" and r.asks[0][0].startswith("¿Qué entradas en Ventas tienen mejor cobertura estricta?")


def world_cs():
    from tools.catalog import catalog_service as cs
    return cs


def test_the_refused_reference_offers_the_same_query_again_and_that_renews_the_list(world, monkeypatch):
    chat = Chat()
    page = chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    _mutating_reader(monkeypatch, _set_strict(page.refs[0], 0.5))
    refused = chat.ask("Abrí el primero")
    renewed = chat.ask(refused.asks[0][0])
    assert renewed.refs != page.refs and chat.state is not None and chat.ask("Abrí el primero").refs == [renewed.refs[0]]                # con la lista vigente, la referencia vuelve a valer


def test_an_entry_that_disappears_is_revalidated_against_the_current_catalog(world, monkeypatch):
    chat = Chat()
    page = chat.ask("Mostrame 3 modelos")
    gone = page.refs[0]
    monkeypatch.setattr(caps, "find_entry", lambda kind, ref: None if ref == gone else next((e for e in world.entries if e.entry_id == ref), None))
    r = chat.ask("Abrí el primero")
    assert "ya no está disponible" in r.text and not r.rows


def test_when_the_list_cannot_be_revalidated_nothing_is_opened(world, monkeypatch):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    monkeypatch.setattr(caps, "governed_read", lambda flows: None)
    r = chat.ask("Abrí el primero")
    assert not r.rows and ("No pude" in r.text) and chat.state is not None


def test_pagination_over_changed_data_restarts_from_the_first_page_to_avoid_duplicates_and_omissions(world, monkeypatch):
    chat = Chat()
    p1 = chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    target = p1.refs[0]
    _mutating_reader(monkeypatch, _set_strict(target, 0.1))
    r = chat.ask("Mostrame los siguientes")
    assert "Los datos cambiaron desde que te mostré la lista" in r.text and "primera página" in r.text and chat.state.spec.offset == 0
    assert r.refs == _expected_after_change(world, target)[:10]


def _expected_after_change(world, target):
    rows = []
    for e in world.entries:
        s = _spec(world.index[e.entry_id])
        if s["state"] == "CURRENT":
            strict = 0.1 if e.entry_id == target else s["strict"]
            rows.append((-strict, e.cliente.lower(), e.dominio.lower(), e.entry_id))
    return [r[3] for r in sorted(rows)]


def test_filters_requery_current_data_and_never_reuse_the_old_values(world, monkeypatch):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    _mutating_reader(monkeypatch, lambda g: [st.profile["summary"]["coverage"].__setitem__("strict", 0.5) for st in g.statuses.values() if st.profile])
    r = chat.ask("¿Y los de Ventas?")
    assert {it for it in re.findall(r"assistant-result-metric\"><strong>([^<]+)</strong>", r.raw)} == {"50.0%"}                              # valores vigentes, no los de la lista anterior


# ── 7. contexto ausente: aclaración determinista, sin LLM ni referencias inventadas ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("phrase", ["Mostrame los siguientes", "Más", "Los anteriores", "Abrí el segundo", "Abrí ese", "¿Y los de Finanzas?", "Solo Pampa", "¿Y por confianza?", "Ordenalos por cobertura", "Sin clasificar"])
def test_a_recognizable_follow_up_without_context_is_a_deterministic_clarification(world, monkeypatch, phrase):
    from webapp.routers.assistant import assistant_chat_service

    def boom(**_kw):
        raise AssertionError("un seguimiento reconocible sin contexto no debe invocar al LLM")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(assistant_chat_service, "client_factory", boom)
    monkeypatch.setattr(assistant_chat_service, "execution_mode", "produccion")
    r = Chat().ask(phrase)
    assert "No tengo una consulta previa vigente para completar con eso" in r.text and not r.rows and not r.llm
    assert [label for _q, label in r.asks] == ["Ver el resumen del Catálogo", "Elegir una métrica"]


def test_a_follow_up_that_carries_its_own_full_query_is_answered_directly_without_context(world):
    r = Chat().ask("¿Y los modelos de Ventas con mejor confianza?")
    assert r.rows and "Confianza de la evidencia" in r.text


def test_a_bare_entity_without_a_continuation_mark_is_not_a_follow_up_unless_it_is_unclassified(world):
    for phrase in ("Pampa", "Ventas", "Finanzas"):
        assert nav._catalog_followup(phrase, PACK, HOST, None) is None
    assert nav._catalog_followup("Sin clasificar", PACK, HOST, None) is not None


# ── 8. la gramática se deriva de los contratos y no secuestra consultas con contenido propio ──────────────────────────────────────────────────
def _live(spec=None):
    return qc.QueryContext(qc.QueryState(spec or qc.QuerySpec("rank", indicator="types"), (), "", "u1", 1000.0), "u1", 1000.0)


def test_every_client_line_of_business_and_indicator_alias_works_as_a_follow_up_without_per_phrase_intents(world):
    for client in CLIENTS:
        assert _follow_spec(f"¿Y los de {client}?").client == client and _follow_spec(f"Solo {client}").client == client
    for option in dcs.lob_options():
        expected = option["id"]
        q = "Sin clasificar" if expected == dcs.UNCLASSIFIED else f"¿Y los de {option['label']}?"
        assert _follow_spec(q).lob == expected, q
    for indicator in ind.INDICATORS:
        for alias in indicator.aliases:
            spec = _follow_spec(f"¿Y por {alias}?")
            if spec is not None:
                assert spec.indicator == indicator.id, alias


def _follow_spec(question):
    clients = nav.catalog_navigation.clients(*catalog_reads._catalog_entries())
    f = nav._followup_intent(question, clients, True)
    if f is None:
        return None
    return types_namespace(client=f.clients[0] if f.clients else None, lob=f.lobs[0] if f.lobs else None, indicator=f.inds[0] if f.inds else None)


def types_namespace(**kw):
    import types
    return types.SimpleNamespace(**kw)


@pytest.mark.parametrize("question", ["¿Qué tiene Pampa?", "Modelos de Ventas", "Mostrame 10 modelos", "¿Qué dominios hay disponibles?", "¿Cómo se calcula la adopción metodológica?", "Más información sobre CDS",
                                       "¿Qué documentación tenemos de CDS?", "¿Cuál es el dominio con mejor scoring?", "Ver informe de cliente para Pampa", "y la seguridad de los datos cómo se gobierna"])
def test_questions_with_their_own_content_are_never_taken_as_follow_ups(world, question):
    clients = nav.catalog_navigation.clients(*catalog_reads._catalog_entries())
    assert nav._followup_intent(question, clients, True) is None


def test_the_follow_up_target_comes_first_but_only_for_short_elliptical_messages():
    assert nav.TARGETS[0].key == "catalog_followup"
    assert nav._catalog_followup("¿Qué es un dominio de datos y cómo se relaciona con el modelo de seguridad de Datasphere en la práctica?", PACK, HOST, _live()) is None


def test_the_grammar_recognizes_the_requested_pagination_reference_and_restart_phrases():
    clients = []
    for phrase, op in [("Mostrame los siguientes", "next"), ("Más", "next"), ("los próximos", "next"), ("Los anteriores", "prev"), ("Abrí el segundo", "ref"), ("Mostrame el tercero", "ref"),
                       ("Abrí ese", "ref"), ("Empezar de nuevo", "reset"), ("Nueva consulta", "reset")]:
        f = nav._followup_intent(phrase, clients, True)
        assert f is not None and f.op == op, phrase
    assert nav._followup_intent("Abrí ese", clients, True).ordinal == 0 and nav._followup_intent("Abrí el último", clients, True).ordinal == -1


# ── 9. regresiones ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_methodological_questions_curated_resources_and_inventory_are_unchanged_by_the_context(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    res = chat.ask("¿Qué documentación tenemos de CDS?")
    assert res.rows and all(r["data-kind"] == caps.CURATED_RESOURCE for r in res.rows) and chat.state is None
    inv = Chat().ask("¿Qué dominios hay disponibles?")
    assert "El Catálogo contiene" in inv.text and not inv.rows
    assert Chat().ask("Ver informe de cliente para Pampa").text.startswith("Acá está el Informe de Cliente de Pampa")


def test_an_autoloading_row_can_only_be_an_embed_destination():
    with pytest.raises(ValueError):
        caps.ResultItem(kind="k", ref="r", title="t", url="https://example.com/x", mode=caps.MODE_EXTERNAL, autoload=True)
    assert caps.ResultItem(kind="k", ref="r", title="t", url="/catalog/flow?flow=r", mode=caps.MODE_EMBED, autoload=True).autoload


def test_the_fingerprint_changes_with_the_query_the_order_and_the_determinant_attributes_not_only_the_ids():
    rows = [("catalog_entry", "a", "1"), ("catalog_entry", "b", "2")]
    base = qc.fingerprint(("rank", "coverage", "best_first", None, None), rows)
    assert base == qc.fingerprint(("rank", "coverage", "best_first", None, None), list(rows))
    assert base != qc.fingerprint(("rank", "coverage", "best_first", None, None), list(reversed(rows)))                                  # mismo conjunto, otro orden
    assert base != qc.fingerprint(("rank", "coverage", "best_first", None, None), [("catalog_entry", "a", "1"), ("catalog_entry", "b", "3")])        # mismos ids, otro valor determinante
    assert base != qc.fingerprint(("rank", "coverage", "worst_first", None, None), rows) and base != qc.fingerprint(("rank", "coverage", "best_first", "Pampa", None), rows)


# ── 10. una consulta reconocida que falla en ejecución no cae en silencio al LLM libre ───────────────────────────────────────────────────────
def test_a_recognized_follow_up_that_fails_says_so_instead_of_falling_to_the_free_llm(world, monkeypatch):
    from webapp.routers.assistant import assistant_chat_service

    def boom(**_kw):
        raise AssertionError("no debe invocar al LLM")
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(assistant_chat_service, "client_factory", boom)
    monkeypatch.setattr(assistant_chat_service, "execution_mode", "produccion")
    monkeypatch.setattr(nav, "_run_spec", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla simulada")))
    for phrase in ("Mostrame los siguientes", "Abrí el primero", "¿Y los de Ventas?"):
        r = chat.ask(phrase)
        assert "No pude completar la consulta en este momento" in r.text and not r.llm and not r.rows, phrase
    assert chat.state is not None                                                                                                        # el contexto sigue ahí para reintentar


def test_a_recognized_analytic_query_that_fails_says_so_instead_of_falling_to_the_free_llm(world, monkeypatch):
    monkeypatch.setattr(nav, "_ranking_result", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla simulada")))
    r = Chat().ask("¿Qué entradas tienen mejor cobertura estricta?")
    assert "No pude completar la consulta en este momento" in r.text and not r.llm and not r.rows


# ── 11. varias instancias / reinicio: el estado es por proceso, y falla de forma segura ───────────────────────────────────────────────────────
def test_context_is_per_process_so_another_worker_never_sees_or_invents_it(world, monkeypatch):
    """Límite DOCUMENTADO (process-local, igual que la conversación y su historial): otra instancia o un reinicio no tiene la conversación,
    así que un seguimiento no se resuelve con estado ajeno ni inventado: o falla la conversación desconocida (error visible) o, en una conversación
    nueva, pide una consulta nueva. En ningún caso abre entradas ni cae al LLM libre."""
    from webapp.routers import assistant as router
    from webapp.services.assistant_service import AssistantConversationStore

    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor cobertura estricta?")
    assert chat.state is not None
    monkeypatch.setattr(router, "assistant_store", AssistantConversationStore())                                                         # «otro worker»: almacén propio y vacío

    lost = chat.ask("Abrí el primero")                                                                                                   # misma conversación, otro proceso
    assert not lost.rows and not lost.llm and "Simulación" not in lost.raw and "Abro el primero" not in lost.raw

    fresh = Chat()                                                                                                                       # conversación nueva en ese proceso
    reply = fresh.ask("Mostrame los siguientes")
    assert "No tengo una consulta previa vigente" in reply.text and not reply.rows and not reply.llm
