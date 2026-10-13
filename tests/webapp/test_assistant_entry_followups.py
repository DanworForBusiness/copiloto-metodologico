"""4B.4 — Seguimientos sobre el modelo EN FOCO: «¿y su confianza?», «¿por qué tiene Buena?», «abrí su Assessment», «la confianza del segundo».

Principios que fijan estos tests: el foco es una IDENTIDAD (`entry_id`) guardada junto a la consulta, del mismo usuario y conversación, que vence con el TTL y se pierde con cualquier consulta nueva u otra
respuesta; en cada seguimiento el modelo se REVALIDA contra el Catálogo vigente y los valores se vuelven a LEER (nada se reutiliza de la respuesta anterior); nombrar otro modelo o alcance hace una consulta
nueva; sin foco vigente la respuesta es una aclaración determinista (nunca el LLM ni un modelo inventado). Mismo mundo de siete estados que `test_assistant_entry_facts`.
"""
from __future__ import annotations

import dataclasses
import html as _html
import time

import pytest

from tests.webapp.test_assistant_entry_facts import CAST, CONF_LABEL, LEVEL_LABEL, PACK, world  # noqa: F401 — `world` es un fixture
from tests.webapp.test_assistant_indicators import CONF_POS, HOST
from tests.webapp.test_assistant_query_context import Chat, Reply, _store
from webapp.services import assessment_presenter as presenter
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_context as qc
from webapp.services import catalog_reads


def ref_of(r: Reply, world):
    assert not r.llm, r.text
    return [world.key_of[x] for x in r.refs]


def assert_about(r: Reply, world, key):
    assert ref_of(r, world) == [key], (r.refs, r.text)


def confidence_ranking(world):
    """Orden esperado de «mejor confianza» (oráculo escrito acá): solo vigentes, nivel de MAYOR a menor y desempate estable (cliente, nombre, entry_id)."""
    cur = [(k, c) for k, c in CAST.items() if c["state"] == "CURRENT" and c.get("level") and c["conf"] in CONF_POS]
    cur.sort(key=lambda kc: (CONF_POS[kc[1]["conf"]], kc[1]["client"].lower(), kc[1]["dominio"].lower()))
    return [k for k, _c in cur]


# ══ 1. el circuito pedido ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_requested_circuit_individual_then_its_confidence_then_why_then_its_assessment(world):
    chat = Chat()
    first = chat.ask("¿Qué sabés de Delivery Document?")
    assert_about(first, world, "dd")
    assert chat.state.focus == world.e["dd"].entry_id
    conf = chat.ask("¿Y su confianza?")
    assert_about(conf, world, "dd")
    assert "Confianza de la evidencia" in conf.text and "Alta" in conf.text and "Delivery Document" in conf.text and "Kensing" in conf.text         # identidad siempre presente
    assert "Cobertura" not in conf.text and "Estado del Assessment" not in conf.text                                                              # solo el atributo pedido
    why = chat.ask("¿Por qué tiene Buena?")
    assert_about(why, world, "dd")
    assert "Adopción metodológica: Buena" in why.text and "La evidencia observada muestra una adopción consistente" in why.text
    opened = chat.ask("Abrí su Assessment")
    assert not opened.llm and "Acá está el Assessment" in opened.text and "Delivery Document" in opened.text
    assert presenter.assessment_url(world.e["dd"], PACK) in _html.unescape(opened.raw)
    assert chat.state.focus == world.e["dd"].entry_id                                                                                             # sigue en foco


@pytest.mark.parametrize("question,label,expected", [
    ("¿Y su cobertura?", "Cobertura estricta", "95.0%"), ("¿Y sus casos a revisar?", "Casos a revisar", "7 casos"), ("¿Y su consistencia de tipos?", "Consistencia de tipos", "90/100"),
    ("¿Y la adopción?", "Adopción metodológica", "Buena"), ("¿Cuál es su confianza?", "Confianza de la evidencia", "Alta"), ("¿Y su estado?", "Estado del Assessment", "Assessment vigente"),
    ("¿Y qué fortalezas tiene?", "Evidencias favorables", "Adquisición de la fuente gobernada"), ("¿Por qué?", "Adopción metodológica: Buena", "Puntos de atención"),
])
def test_followup_phrasings_answer_about_the_focused_model(world, question, label, expected):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    r = chat.ask(question)
    assert_about(r, world, "dd")
    assert label in r.text and expected in r.text, r.text


def test_focus_moves_with_each_new_individual_answer(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    chat.ask("¿Qué sabés de Reserva MP?")
    r = chat.ask("¿Y su cobertura?")
    assert_about(r, world, "mp")
    assert "85.0%" in r.text and "95.0%" not in r.text                                              # el valor del NUEVO foco, nunca el del anterior


def test_naming_another_model_is_a_new_query_not_a_followup_on_the_focus(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    r = chat.ask("¿Y la confianza de Sell Out?")
    assert_about(r, world, "so")
    assert chat.state.focus == world.e["so"].entry_id


def test_why_with_another_level_clarifies_on_the_focused_model(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    r = chat.ask("¿Por qué tiene Parcial?")
    assert_about(r, world, "dd")
    assert "no tiene hoy adopción" in r.text and "Buena" in r.text and "La adopción es parcial" not in r.text


def test_followup_after_a_stale_model_keeps_values_historical(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Lista Precios?")
    r = chat.ask("¿Y su confianza?")
    assert_about(r, world, "lp")
    assert "Baja" in r.text and "no es el vigente" in r.text


@pytest.mark.parametrize("key,title", [("ped", "Pedidos Entregas"), ("dan", "Almacén Danone"), ("desp", "Despachos"), ("fac", "Facturas")])
def test_followup_after_a_model_without_usable_assessment_says_so(world, key, title):
    chat = Chat()
    chat.ask(f"¿Qué sabés de {title}?")
    r = chat.ask("¿Y su confianza?")
    assert_about(r, world, key)
    assert "Alta" not in r.text and "Media" not in r.text and "Baja" not in r.text


# ══ 2. el foco se pierde ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_a_new_set_query_clears_the_focus(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    chat.ask("¿Qué modelos tienen adopción buena?")
    assert chat.state.focus is None
    r = chat.ask("¿Y su confianza?")
    assert not r.llm and "No tengo un modelo en foco" in r.text and not r.refs


def test_any_other_route_clears_the_focus(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    chat.ask("¿Qué es la cobertura estricta?")                                                       # ruta conceptual: borra el contexto estructurado
    assert chat.state is None or chat.state.focus is None
    assert "No tengo un modelo en foco" in chat.ask("¿Y su confianza?").text


def test_the_focus_expires_with_the_ttl(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    conv = _store()._conversations[chat.cid]
    conv.query_state = dataclasses.replace(conv.query_state, updated_at=time.time() - (qc.TTL_SECONDS + 5))
    r = chat.ask("¿Y su confianza?")
    assert not r.llm and "Pasaron más de 15 minutos" in r.text and not r.refs


def test_the_focus_belongs_to_one_user_and_one_conversation(world):
    ana = Chat("ana@d.com")
    ana.ask("¿Qué sabés de Delivery Document?")
    beto = Chat("beto@d.com", cid=ana.cid)                                                           # misma conversación, otro usuario
    assert "No tengo un modelo en foco" in beto.ask("¿Y su confianza?").text
    other = Chat("ana@d.com")                                                                        # mismo usuario, otra conversación
    assert "No tengo un modelo en foco" in other.ask("¿Y su confianza?").text
    assert_about(ana.ask("¿Y su confianza?"), world, "dd")                                           # y el de Ana no se tocó


@pytest.mark.parametrize("question", ["¿Y su confianza?", "Abrí su Assessment", "¿Por qué tiene Buena?", "¿Cuál es su cobertura?"])
def test_a_followup_without_focus_is_a_deterministic_clarification(world, question):
    r = Chat().ask(question)
    assert not r.llm and not r.rows
    assert "foco" in r.text or "No tengo" in r.text or "modelo" in r.text.lower()


def test_the_word_esta_without_accent_is_not_a_focus_reference(world):
    r = Chat().ask("¿Está desactualizado Lista Precios?")
    assert_about(r, world, "lp")                                                                     # «está» sin tilde = «esta»: no se confunde con deixis


# ══ 3. revalidación: datos y permisos vigentes en cada uso ══════════════════════════════════════════════════════════════════════════════════
def test_values_are_reread_on_every_followup_never_reused(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    status = world.statuses[world.e["dd"].entry_id]
    profile = dict(status.profile)
    profile["summary"] = {"coverage": {"strict": 0.95}, "confidence": {"level": "LOW"}}               # el dato cambió entre un turno y el siguiente
    world.statuses[world.e["dd"].entry_id] = dataclasses.replace(status, profile=profile)
    r = chat.ask("¿Y su confianza?")
    assert "Baja" in r.text and "Alta" not in r.text


def test_a_focused_model_that_left_the_catalog_is_not_answered_with_old_data(world, monkeypatch):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    kept = [e for e in world.entries if e.entry_id != world.e["dd"].entry_id]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (__import__("tools.catalog.catalog_service", fromlist=["x"]), kept))
    r = chat.ask("¿Y su confianza?")
    assert not r.llm and "ya no está disponible" in r.text and not r.refs and "Alta" not in r.text


def test_the_state_keeps_only_identities_and_parameters_never_values_or_urls(world):
    chat = Chat()
    chat.ask("¿Qué cobertura y qué confianza tiene Delivery Document?")
    st = chat.state
    assert st.spec.capability == "entry" and st.spec.entry == world.e["dd"].entry_id and st.spec.attrs == ("coverage", "confidence") and st.focus == st.spec.entry
    assert st.refs == ((caps.CATALOG_ENTRY, world.e["dd"].entry_id),)
    dumped = repr(st)
    for value in ("95.0%", "Alta", "Buena", "/catalog/", "http", "Delivery Document", "Kensing"):
        assert value not in dumped, value


# ══ 4. consultas de conjunto con foco individual ═══════════════════════════════════════════════════════════════════════════════════════════
def test_list_followups_do_not_apply_to_an_individual_context(world):
    chat = Chat()
    chat.ask("¿Qué sabés de Delivery Document?")
    for q in ("¿Cuántos son?", "Mostrame los siguientes", "Abrí el segundo"):
        r = chat.ask(q)
        assert not r.llm and "un solo modelo" in r.text or "lista" in r.text, (q, r.text)
    assert chat.state.focus == world.e["dd"].entry_id                                                  # y el foco se conserva


# ══ 5. desde una lista: «abrí el segundo» + «la confianza del segundo» ══════════════════════════════════════════════════════════════════════
def test_open_by_reference_puts_the_model_in_focus_without_losing_the_list(world):
    chat = Chat()
    first = chat.ask("¿Qué entradas tienen mejor confianza de la evidencia?")
    order = confidence_ranking(world)
    assert [world.key_of[r] for r in first.refs] == order
    opened = chat.ask("Abrí el segundo")
    assert [world.key_of[r] for r in opened.refs] == [order[1]] and chat.state.focus == world.e[order[1]].entry_id
    assert [world.key_of[r] for r in chat.ask("Abrí el tercero").refs] == [order[2]]                  # la lista sigue valiendo
    chat.ask("Abrí el segundo")
    r = chat.ask("¿Y su cobertura?")
    assert_about(r, world, order[1])
    assert f"{CAST[order[1]]['strict'] * 100:.1f}%" in r.text
    assert [world.key_of[x] for x in chat.ask("Abrí el primero").refs] == [order[0]]                    # y después de responder, la lista sigue


def test_a_list_context_needs_an_explicit_reference_for_the_focus(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor confianza de la evidencia?")
    chat.ask("Abrí el segundo")
    r = chat.ask("¿Y por cobertura?")                                                                   # sin deixis sigue siendo un seguimiento de la LISTA
    assert len(r.refs) > 1


def test_the_attribute_of_an_ordinal_reference(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor confianza de la evidencia?")
    order = confidence_ranking(world)
    for question, pos in (("La cobertura del segundo", 1), ("¿Y los casos a revisar del tercero?", 2), ("¿Cuál es la adopción del último?", len(order) - 1)):
        r = chat.ask(question)
        assert_about(r, world, order[pos])
    assert [world.key_of[x] for x in chat.ask("Abrí el primero").refs] == [order[0]]                    # preguntar por una referencia no pierde la lista
    assert chat.state.focus == world.e[order[0]].entry_id


def test_an_ordinal_attribute_is_refused_when_the_list_changed(world):
    chat = Chat()
    chat.ask("¿Qué entradas tienen mejor confianza de la evidencia?")
    key = confidence_ranking(world)[0]
    status = world.statuses[world.e[key].entry_id]
    profile = dict(status.profile)
    profile["summary"] = {"coverage": {"strict": 0.5}, "confidence": {"level": "LOW"}}                  # cambió el orden de la lista
    world.statuses[world.e[key].entry_id] = dataclasses.replace(status, profile=profile)
    r = chat.ask("La cobertura del segundo")
    assert not r.llm and "La lista cambió" in r.text and not r.refs


def test_an_ordinal_attribute_without_a_list_asks_for_one(world):
    r = Chat().ask("La confianza del segundo")
    assert not r.llm and not r.refs and "No tengo una consulta previa" in r.text
