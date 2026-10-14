"""4B.3 / 4B.4 — Consultas INDIVIDUALES gobernadas sobre un modelo por el chat REAL (destino `entry_query`) y seguimientos con modelo en foco.

Principios que fijan estos tests: un modelo se identifica por el TÍTULO que muestra el Catálogo (el cliente solo desambigua; nunca se elige por cliente solamente ni "por parecido"); la respuesta muestra
SIEMPRE la identidad (título · cliente · línea de negocio), el valor con su evidencia y el enlace al Assessment; los siete estados del Assessment se distinguen (ausente ≠ no aplica ≠ no disponible ≠ no
compatible ≠ desactualizado ≠ no determinable); un valor de un Assessment desactualizado solo aparece como histórico y no vigente; los fundamentos son los textos del producto y del NIVEL vigente; nada
se resuelve con el LLM; las consultas de conjunto, conceptuales y documentales siguen exactamente como antes. Fixtures: el mundo de `test_assistant_entry_facts` (siete estados, valores literales).
"""
from __future__ import annotations

import html as _html
import re

import pytest

from tests.webapp.test_assistant_entry_facts import CAST, CONF_LABEL, LEVEL_LABEL, PACK, world  # noqa: F401 — `world` es un fixture
from tests.webapp.test_assistant_indicators import HOST
from tests.webapp.test_assistant_query_context import Chat, Reply
from webapp.services import assessment_presenter as presenter
from webapp.services import assistant_navigation as nav

COLOR_EMOJIS = "🟢🟡🟠🔴⚪"


def ask(q: str) -> Reply:
    return Chat().ask(q)


def rows_of(r: Reply, world, *keys):
    return [world.key_of[ref] for ref in r.refs] == list(keys)


def one_entry(r: Reply, world, key):
    assert not r.llm, r.text
    assert [world.key_of[x] for x in r.refs] == [key], (r.refs, r.text)
    assert world.e[key].entry_id == r.rows[0]["data-ref"]
    return r


# ══ 1. un atributo explícito: SOLO el valor pedido, con identidad, evidencia y enlace ═══════════════════════════════════════════════════════
@pytest.mark.parametrize("key", ["dd", "ddi", "so", "stl", "stp"])
def test_each_attribute_of_a_current_entry_equals_the_oracle(world, key):
    c = CAST[key]
    title = c["title"]
    where = f" de {c['client']}" if title == "Stock" else ""
    cases = [
        (f"¿Qué casos a revisar tiene {title}{where}?", "Casos a revisar", f"{c['review']} caso"),
        (f"¿Cuál es la confianza de {title}{where}?", "Confianza de la evidencia", CONF_LABEL[c["conf"]]),
        (f"¿Cuál es la cobertura estricta de {title}{where}?", "Cobertura estricta", f"{c['strict'] * 100:.1f}%"),
        (f"¿Qué adopción metodológica tiene {title}{where}?", "Adopción metodológica", LEVEL_LABEL[c["level"]]),
        (f"¿Cuál es la consistencia de tipos de {title}{where}?", "Consistencia de tipos", f"{c['score']}/100"),
    ]
    for question, label, expected in cases:
        r = one_entry(ask(question), world, key)
        assert title in r.text and c["client"] in r.text and label in r.text and expected in r.text, (question, r.text)
        assert "Estado del Assessment" not in r.text                                              # un atributo explícito NO vuelca la ficha
        raw = _html.unescape(r.raw)
        assert presenter.assessment_url(world.e[key], PACK) in raw                                # enlace al Assessment construido desde el entry_id


def test_the_single_attribute_answer_shows_only_that_value_with_its_evidence_and_caveat(world):
    r = one_entry(ask("¿Qué casos a revisar tiene Delivery Document?"), world, "dd")
    assert "7 casos a revisar" in r.text and "Confianza" not in r.text and "Cobertura" not in r.text and "Adopción" not in r.text
    assert "no equivale necesariamente a peor calidad" in r.note                                  # la advertencia metodológica del indicador viaja con el valor
    assert "Assessment · casos a revisar" in _html.unescape(r.raw) and "09/10/2026" in _html.unescape(r.raw)        # fuente y fecha en la fila


def test_two_attributes_in_one_question_are_both_answered_and_nothing_else(world):
    r = one_entry(ask("¿Qué cobertura y qué confianza tiene Delivery Document?"), world, "dd")
    assert "95.0%" in r.text and "Alta" in r.text and "Casos a revisar" not in r.text and "Adopción" not in r.text


def test_the_old_need_scope_declaration_is_replaced_when_a_model_resolves(world):
    assert "necesito" not in ask("¿Qué casos a revisar tiene Delivery Document?").text.lower()


# ══ 2. «¿qué sabés de X?»: ficha breve ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_ficha_has_state_and_the_five_measures(world):
    r = one_entry(ask("¿Qué sabés de Delivery Document?"), world, "dd")
    for label in ("Estado del Assessment", "Assessment vigente", "Adopción metodológica", "Buena", "Confianza de la evidencia", "Alta", "Cobertura estricta", "95.0%",
                  "Consistencia de tipos", "90/100", "Casos a revisar", "7 casos"):
        assert label in r.text, (label, r.text)
    assert "no se combinan en un puntaje" in r.note                                                # sin puntaje global


@pytest.mark.parametrize("question", ["¿Qué sabés de Delivery Document?", "Contame sobre Delivery Document", "Decime cómo está Delivery Document", "Resumime Delivery Document"])
def test_ficha_phrasings(world, question):
    assert "Estado del Assessment" in one_entry(ask(question), world, "dd").text


# ══ 3. los siete estados se distinguen ══════════════════════════════════════════════════════════════════════════════════════════════════════
def test_not_determinable_says_it_and_still_answers_the_other_measures(world):
    r = one_entry(ask("¿Qué sabés de Reserva MP?"), world, "mp")
    assert "No determinable" in r.text and "Evidencia afirmativa insuficiente" in r.text and "85.0%" in r.text and "80/100" in r.text
    only = one_entry(ask("¿Qué adopción tiene Reserva MP?"), world, "mp")
    assert "No determinable" in only.text and "Buena" not in only.text and "Plena" not in only.text


def test_stale_shows_the_last_known_value_only_as_historical_and_not_current(world):
    r = one_entry(ask("¿Cuál es la confianza de Lista Precios?"), world, "lp")
    assert "Baja" in r.text and "último valor conocido" in r.text and "no es el vigente" in r.text and "desactualizado" in r.text
    ficha = one_entry(ask("¿Qué sabés de Lista Precios?"), world, "lp")
    assert "Assessment desactualizado" in ficha.text and "histórico" in ficha.text and "no son los vigentes" in ficha.text and "60/100" in ficha.text
    assert "Regenerarlo" in ficha.text                                                               # qué pasa al regenerar (texto del producto)
    types_line = re.search(r"Consistencia de tipos\s*:\s*60/100([^-]{0,20})", ficha.text)
    assert types_line and "histórico" not in types_line.group(1)                                     # la consistencia de tipos no depende del Assessment: no es histórica


@pytest.mark.parametrize("key,question,state_label,absent", [
    ("ped", "¿Qué sabés de Pedidos Entregas?", "Sin assessment", "todavía no se evaluó"),
    ("dan", "¿Qué sabés de Almacén Danone?", "No aplica", CAST["dan"]["reason"]),
    ("desp", "¿Qué sabés de Despachos?", "Assessment no disponible", CAST["desp"]["reason"]),
    ("fac", "¿Qué sabés de Facturas?", "Informe no compatible", CAST["fac"]["reason"]),
])
def test_missing_assessment_states_are_told_apart_and_keep_only_the_type_score(world, key, question, state_label, absent):
    r = one_entry(ask(question), world, key)
    assert state_label in r.text and absent in r.text
    assert "Buena" not in r.text and "Alta" not in r.text and "%" not in r.text                     # ningún valor del assessment
    if key != "fac":
        assert f"{CAST[key]['score']}/100" in r.text                                               # la consistencia de tipos es del Catálogo
    others = {"todavía no se evaluó": "No aplica", CAST["dan"]["reason"]: "todavía no se evaluó"}
    for needle, forbidden in others.items():
        if needle == absent:
            assert forbidden not in r.text


def test_state_question_answers_the_state_alone(world):
    r = one_entry(ask("¿Cuál es el estado del assessment de Despachos?"), world, "desp")
    assert "Assessment no disponible" in r.text and "Adopción" not in r.text and "Confianza" not in r.text
    r2 = one_entry(ask("¿Está desactualizado Lista Precios?"), world, "lp")
    assert "Assessment desactualizado" in r2.text


# ══ 4. identidad: título del Catálogo, cliente que desambigua, nunca por cliente solamente ═══════════════════════════════════════════════
def test_the_answer_always_shows_the_identity_title_client_and_line(world):
    r = one_entry(ask("¿Qué casos a revisar tiene Delivery Document?"), world, "dd")
    assert "Delivery Document" in r.text and "Kensing" in r.text and "Ventas" in r.text


@pytest.mark.parametrize("question,key", [("¿Qué confianza tiene Delivery Document Item?", "ddi"), ("¿Qué confianza tiene Delivery Document?", "dd"),
                                          ("¿Qué confianza tiene Sell Out?", "so"), ("¿Qué confianza tiene Sell Out Hojobar?", "soh"),
                                          ("¿Qué confianza tiene Stock de Loma?", "stl"), ("¿Qué confianza tiene Stock de Pampa?", "stp"),
                                          ("¿Qué confianza tiene Reserva MP?", "mp"), ("¿Qué confianza tiene «Sell Out»?", "so")])
def test_nested_titles_and_client_disambiguation_select_the_right_model(world, question, key):
    one_entry(ask(question), world, key)


def test_a_title_shared_by_two_clients_asks_which_and_each_option_round_trips(world):
    r = ask("¿Qué sabés de Stock?")
    assert not r.llm and not r.rows and "más de un modelo" in r.text and [label for _q, label in r.asks] == ["Stock · Loma", "Stock · Pampa"]
    for (question, _label), key in zip(r.asks, ("stl", "stp")):
        one_entry(Chat().ask(question), world, key)                                                 # la pregunta canónica de cada opción lleva a ESE modelo


def test_the_options_keep_the_attribute_that_was_asked(world):
    r = ask("¿Qué casos a revisar tiene Stock?")
    answers = [Chat().ask(question) for question, _l in r.asks]
    assert [a.refs for a in answers] == [[world.e["stl"].entry_id], [world.e["stp"].entry_id]]
    assert "5 casos" in answers[0].text and "9 casos" in answers[1].text


def test_two_models_named_at_once_ask_for_one_at_a_time(world):
    r = ask("¿Qué sabés de Delivery Document y Reserva MP?")
    assert not r.llm and not r.rows and "uno por vez" in r.text and len(r.asks) == 2


def test_a_client_alone_never_selects_a_model(world):
    r = ask("¿Qué confianza tiene Kensing?")
    assert r.text.startswith("Entradas de Kensing ordenadas por")                                  # es la consulta de CONJUNTO de 4A, no una ficha de un modelo elegido por el cliente
    assert "Estado del Assessment" not in ask("¿Qué sabés de Kensing?").text


def test_a_partial_name_is_never_selected_but_candidates_are_offered_for_the_user_to_confirm(world):
    r = nav._entry_query("¿Qué casos a revisar tiene Delivery Doc?", PACK, HOST)
    assert r is not None and r.result is None and "No encontré un modelo con ese nombre exacto" in r.message                 # no se elige ni se responde un valor
    assert [label for label, _u, _m in [(a[0], a[1], a[2]) for a in r.actions]] == ["Delivery Document · Kensing", "Delivery Document Item · Kensing"]
    chat = ask("¿Qué sabés de Delivery Doc?")
    assert not chat.llm and not chat.rows and [label for _q, label in chat.asks] == ["Delivery Document · Kensing", "Delivery Document Item · Kensing"]
    for (question, _label), key in zip(chat.asks, ("dd", "ddi")):
        one_entry(Chat().ask(question), world, key)                                                                         # cada opción lleva EXACTAMENTE a ese modelo


def test_a_prefix_inside_a_named_client_only_offers_that_clients_models(world):
    r = ask("¿Qué sabés de Delivery Doc de Pampa?")
    assert not r.asks and not r.rows                                                                                         # Pampa no tiene ningún «Delivery Doc…»: no se ofrece el de Kensing
    r2 = ask("¿Qué sabés de Lista de Precios?")
    assert [label for _q, label in r2.asks] == ["Lista Precios · Biogenesis"] and not r2.llm


def test_a_name_with_no_candidates_is_not_claimed(world):
    for q in ("¿Qué casos a revisar tiene Sellout?", "¿Qué sabés de la arquitectura de datos?", "¿Cuál es el estado de la calidad de los datos de Kensing?"):
        assert nav._entry_query(q, PACK, HOST) is None, q


def test_a_quoted_name_that_does_not_exist_is_said_not_guessed(world):
    r = nav._entry_query("¿Qué sabés de «Delivery Docs»?", PACK, HOST)
    assert r is not None and r.result is None and "ese nombre exacto" in r.message and not r.actions
    r2 = nav._entry_query("¿Qué sabés de «Delivery Doc»?", PACK, HOST)
    assert r2 is not None and len(r2.actions) == 2                                                                           # entre comillas también se ofrecen candidatos (prefijo), nunca se selecciona


def test_a_line_of_business_name_is_scope_not_a_model(world):
    r = ask("¿Qué cobertura tiene Ventas?")
    assert not (len(r.refs) == 1 and "Estado del Assessment" in r.text)


# ══ 5. fundamentos del Juicio: los textos del producto, del NIVEL vigente ═════════════════════════════════════════════════════════════════
def test_why_explains_the_current_level_with_the_product_texts(world):
    r = one_entry(ask("¿Por qué Delivery Document tiene adopción buena?"), world, "dd")
    assert "Adopción metodológica: Buena" in r.text and "La evidencia observada muestra una adopción consistente de los cuatro contratos metodológicos." in r.text
    assert "Adquisición de la fuente gobernada en todo contacto con el origen" in r.text and "Alineación de ramas completada recién en el producto" in r.text
    assert "y 1 más en el informe" in r.text                                                         # topes: ≤ 3 con el resto en el informe
    assert "los fundamentos son los del informe" in r.note.lower()
    assert not re.search(r"\b(?:what_would|cta_candidates|review_candidates)\b", r.raw)


def test_why_with_another_level_clarifies_instead_of_justifying_it(world):
    r = one_entry(ask("¿Por qué Delivery Document tiene adopción parcial?"), world, "dd")
    assert "no tiene hoy adopción" in r.text and "Parcial" in r.text and "Buena" in r.text           # el nivel vigente es Buena
    assert "La adopción es parcial" not in r.text and "Adquisición de la fuente" not in r.text        # no se fabrican fundamentos del nivel equivocado


def test_why_for_each_level_uses_that_levels_headline(world):
    heads = {"so": "refleja la adopción metodológica en los cuatro contratos", "ddi": "adopción adecuada, con oportunidades puntuales de mejora",
             "soh": "La adopción es parcial: existen señales favorables"}
    for key, head in heads.items():
        r = one_entry(ask(f"¿Por qué {CAST[key]['title']} tiene esa adopción?"), world, key)
        assert head in r.text, (key, r.text)


def test_why_not_determinable_gives_the_reason_and_no_level(world):
    r = one_entry(ask("¿Por qué Reserva MP es no determinable?"), world, "mp")
    assert "No determinable" in r.text and "Evidencia afirmativa insuficiente" in r.text and "Buena" not in r.text and "Adecuada" not in r.text


def test_why_of_a_stale_assessment_is_labelled_historical(world):
    r = one_entry(ask("¿Por qué Lista Precios tiene adopción buena?"), world, "lp")
    assert "no vigente" in r.text and "Buena" in r.text


@pytest.mark.parametrize("key,question", [("ped", "¿Por qué Pedidos Entregas tiene esa adopción?"), ("dan", "¿Por qué Almacén Danone tiene esa adopción?"),
                                          ("desp", "¿Por qué Despachos tiene esa adopción?"), ("fac", "¿Por qué Facturas tiene esa adopción?")])
def test_no_fundamentals_are_fabricated_without_a_judgment(world, key, question):
    r = one_entry(ask(question), world, key)
    assert "Evidencias favorables" not in r.text and "Puntos de atención" not in r.text and "La evidencia observada" not in r.text


def test_strengths_and_limitations_aspects(world):
    s = one_entry(ask("¿Cuáles son las fortalezas de Sell Out?"), world, "so")
    assert "Evidencias favorables (2)" in s.text and "Puntos de atención" not in s.text
    lim = one_entry(ask("¿Qué limitaciones tiene Delivery Document?"), world, "dd")
    assert "No pude leer las limitaciones" in lim.text                                                 # el informe sintético no se arma: se dice, no se afirma «ninguna»


def test_limitations_come_from_the_report_view_capped_to_two(world, monkeypatch):
    rows = [{"title": f"Superficie {n}", "area": "Área", "state": "No evaluable", "reason": f"Motivo {n}"} for n in range(4)]
    monkeypatch.setattr(presenter, "report_view", lambda *a, **k: {"limitations": rows})
    r = one_entry(ask("¿Qué limitaciones tiene Delivery Document?"), world, "dd")
    assert "Limitaciones de la evidencia (4)" in r.text and "Superficie 0" in r.text and "Superficie 1" in r.text and "Superficie 2" not in r.text and "y 2 más en el informe" in r.text
    assert "Puntos de atención" not in r.text and "Evidencias favorables" not in r.text


# ══ 6. lo que NO cambia ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("question", [
    "¿Qué modelos tienen adopción buena?", "¿Cuántos modelos hay por cliente?", "¿Qué entradas tienen mejor confianza de la evidencia?", "Mostrame los modelos de Kensing",
    "¿Qué modelos están desactualizados?", "¿Qué es la cobertura estricta?", "¿Cómo se define la adopción metodológica?", "¿Qué modelos tenemos?", "¿Cuál es el promedio de cobertura?",
    "modelos con adopción buena o adecuada", "¿Qué es un Assessment?", "casos a revisar de los modelos de Kensing",
])
def test_set_conceptual_and_documentary_questions_are_not_individual_queries(world, question):
    assert nav._entry_query(question, PACK, HOST) is None, question


def test_viewing_the_assessment_still_navigates(world):
    r = ask("Ver el assessment de Delivery Document")
    assert "Acá está el Assessment" in r.text and not r.llm
    assert presenter.assessment_url(world.e["dd"], PACK) in _html.unescape(r.raw)


def test_no_llm_no_legacy_verdict_no_emojis_in_any_individual_answer(world):
    for q in ("¿Qué sabés de Delivery Document?", "¿Por qué Delivery Document tiene adopción buena?", "¿Qué sabés de Lista Precios?", "¿Qué sabés de Facturas?",
              "¿Qué casos a revisar tiene Sell Out?"):
        r = ask(q)
        assert not r.llm and not any(ch in r.text for ch in COLOR_EMOJIS) and not any(w in r.text.lower() for w in ("mirada danwor", "referencia danwor", "inspiración")), q


def test_the_targets_order_puts_entry_query_before_catalog_query():
    keys = [t.key for t in nav.TARGETS]
    assert keys.index("entry_query") == keys.index("catalog_query") - 1 and keys.index("data_catalog") < keys.index("entry_query")


def test_an_individual_question_never_reads_a_value_from_another_entry(world):
    seen = {}
    for key in ("dd", "ddi", "so", "soh", "mp", "lp", "stl", "stp"):
        where = f" de {CAST[key]['client']}" if CAST[key]["title"] == "Stock" else ""
        r = one_entry(ask(f"¿Qué sabés de {CAST[key]['title']}{where}?"), world, key)
        seen[key] = r.text
    assert len(set(seen.values())) == len(seen)
    assert "95.0%" in seen["dd"] and "95.0%" not in "".join(t for k, t in seen.items() if k != "dd")


@pytest.mark.parametrize("question", [
    "¿Cuántos modelos como Delivery Document tienen casos a revisar?", "¿Qué modelos se parecen a Sell Out en confianza de la evidencia?",
    "Mostrame las entradas con cobertura mayor que la de Delivery Document", "¿Qué assessments tienen adopción buena, como Delivery Document?",
])
def test_a_set_question_that_mentions_a_model_title_is_not_an_individual_query(world, question):
    assert nav._entry_query(question, PACK, HOST) is None, question


def test_why_not_determinable_on_a_model_that_has_a_level_clarifies_the_false_premise(world):
    r = one_entry(ask("¿Por qué Delivery Document es no determinable?"), world, "dd")
    assert "no tiene hoy adopción no determinable" in r.text and "Buena" in r.text and "Adquisición de la fuente" not in r.text


def test_why_with_a_false_level_premise_on_a_stale_assessment_clarifies_with_the_historical_label(world):
    import dataclasses
    key = world.e["lp"].entry_id                                                                         # desactualizado; su último informe decía BUENA
    world.statuses[key] = dataclasses.replace(world.statuses[key], judgment={**world.statuses[key].judgment, "level": "ADECUADA"})
    r = one_entry(ask("¿Por qué Lista Precios tiene adopción buena?"), world, "lp")
    assert "no tenía adopción" in r.text and "último informe conocido" in r.text and "no es el vigente" in r.text and "Adecuada" in r.text
    assert "Adquisición de la fuente" not in r.text                                                      # no se justifica un nivel que el informe no tenía


def test_the_stale_ficha_describes_a_not_determinable_last_judgment_instead_of_claiming_no_readable_value(world):
    import dataclasses
    key = world.e["lp"].entry_id
    nd = {"status": "NOT_DETERMINABLE", "level": None, "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": [], "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"}
    world.statuses[key] = dataclasses.replace(world.statuses[key], judgment=nd)
    r = one_entry(ask("¿Qué sabés de Lista Precios?"), world, "lp")
    assert "Último informe conocido (Assessment desactualizado, no vigente): no determinable" in r.text and "no hay un último valor legible" not in r.text
