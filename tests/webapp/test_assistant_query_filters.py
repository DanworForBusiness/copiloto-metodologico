"""Paso 4A — consultas con vocabulario GOBERNADO de nivel, estado y cantidad (destino `catalog_query`).

Principios que fijan estos tests: un calificador reconocido como gobernado (nivel de un indicador, estado del assessment, cantidad) JAMÁS se ignora en silencio: se consume, se aclara o se declara la
operación no soportada; la respuesta repite los filtros efectivos y el denominador; los conteos salen de un ORÁCULO escrito acá desde las reglas documentadas (no del código bajo prueba);
«sin assessment» (ausencia de evaluación) y «sin resultado utilizable» (no aplica / no disponible / no compatible) son conceptos distintos; las consultas que no traen vocabulario 4A, las metodológicas
y las documentales siguen exactamente como antes. Fixtures SINTÉTICAS: estados REALES de Assessment (`svc.AssessmentStatus`) con los seis estados.
"""
from __future__ import annotations

import re
import types

import pytest

import webapp.main as main
from tools.catalog import catalog_service as cs
from tests.webapp.test_assistant_indicators import CONF_POS, CLIENTS, GENERATED, HOST, LEVEL_POS, PACK, _spec, _write
from tests.webapp.test_assistant_query_context import Chat, Reply
from webapp.services import assessment_service as svc
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_indicators as ind
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_query as aq
from webapp.services import catalog_reads
from webapp.services import domain_classification_service as dcs

WORLD_N = 90
LOB_LABEL = {"SALES": "Ventas", "FINANCE": "Finanzas"}


# ── mundo sintético con LOS SEIS estados reales ───────────────────────────────────────────────────────────────────────────────────────────
def state4(i: int) -> str:
    """Estado de Assessment de la entrada i: los de `_spec` (vigente, desactualizado, sin assessment) más no aplica, no disponible y esquema incompatible."""
    base = _spec(i)["state"]
    if base != svc.CURRENT:
        return base
    if i % 17 == 5:
        return svc.NOT_APPLICABLE
    if i % 19 == 7:
        return svc.FAILED
    if i % 23 == 11:
        return svc.INCOMPATIBLE_SCHEMA
    return svc.CURRENT


def spec4(i: int) -> dict:
    return {**_spec(i), "state": state4(i)}


def _status4(entry, i: int) -> svc.AssessmentStatus:
    s = spec4(i)
    if s["state"] in (svc.NOT_YET_ASSESSED, svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA):
        return svc.AssessmentStatus(state=s["state"], entry_id=entry.entry_id, client=entry.cliente, terminal_am=None)
    profile = {"assessment_state": "ASSESSED", "summary": {"coverage": {"strict": s["strict"]}, "confidence": {"level": s["confidence"]}},
               "review_required": [{"occurrence_count": s["review"]}], "observations": [{"occurrence_count": 1}]}
    level = "BUENA" if s["state"] == svc.STALE else s["judgment"]                                           # un desactualizado CONSERVA el nivel de su último Juicio (adopción y vigencia son ejes separados)
    judgment = ({"status": "NOT_DETERMINABLE", "level": None, "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": []} if level == "ND"
                else {"status": "DETERMINED", "level": level, "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": []})
    return svc.AssessmentStatus(state=s["state"], entry_id=entry.entry_id, client=entry.cliente, terminal_am=f"4AM_X_{i:03d}", generated_at=GENERATED, profile=profile, judgment=judgment)


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
        statuses = {e.entry_id: _status4(e, index[e.entry_id]) for e in flows}
        return caps.Governed(statuses, dcs.effective_lob_by_entry(flows, statuses))
    monkeypatch.setattr(caps, "governed_read", read)
    return types.SimpleNamespace(entries=entries, index=index, by_id={e.entry_id: e for e in entries})


# ── ORÁCULO independiente: reglas documentadas, escritas desde el contrato y no desde el código bajo prueba ─────────────────────────────────────────
def _current(s):
    return s["state"] == svc.CURRENT


def _adoption(s):
    return s["judgment"] if _current(s) and s["judgment"] in LEVEL_POS else None             # solo lo VIGENTE y determinado se compara


def _confidence(s):
    return s["confidence"] if _current(s) else None


_KEYS = {"adoption": (_adoption, LEVEL_POS), "confidence": (_confidence, CONF_POS)}


def _scope(world, client=None, lob=None, pred=lambda s: True):
    out = []
    for e in world.entries:
        s = spec4(world.index[e.entry_id])
        if (client is None or e.cliente == client) and (lob is None or s["lob"] == lob) and pred(s):
            out.append((e, s))
    return out


def levels_oracle(world, indicator, levels, *, client=None, lob=None, pred=lambda s: True):
    key, pos = _KEYS[indicator]
    scope = _scope(world, client, lob, pred)
    comparable = [(e, s) for e, s in scope if key(s) is not None]
    matched = sorted(((e, s) for e, s in comparable if key(s) in levels), key=lambda p: (pos[key(p[1])], p[0].cliente.lower(), p[0].dominio.lower(), p[0].entry_id))
    excluded = {}
    for e, s in scope:
        if key(s) is not None:
            continue
        code = "STALE" if s["state"] == svc.STALE else ("NOT_DETERMINABLE" if _current(s) and indicator == "adoption" and s["judgment"] == "ND" else "NOT_ASSESSED")
        excluded[code] = excluded.get(code, 0) + 1
    return types.SimpleNamespace(universe=len(scope), evaluated=len(comparable), ids=[e.entry_id for e, _ in matched], excluded=excluded)


def head_count(r: Reply) -> int:
    m = re.search(r"(?:Hay|Son) (\d+) entradas? navegables?", r.text)
    if m:
        return int(m.group(1))
    assert "Ninguna entrada" in r.text, r.text
    return 0


def walk(chat: Chat, first: Reply, limit: int = 30) -> list[str]:
    """Todas las filas de todas las páginas de una consulta, en orden."""
    refs, r = list(first.refs), first
    while "mostrame los siguientes" in r.note and len(refs) < 1000 and limit:
        r, limit = chat.ask("Mostrame los siguientes"), limit - 1
        refs += r.refs
    return refs


def _ask_query(question: str):
    return nav._catalog_query(question, PACK, HOST)


# ══ 1. NIVELES (4A.1) ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
LEVEL_CASES = [
    ("¿Qué modelos tienen adopción buena?", "adoption", {"BUENA"}, None, None),
    ("¿Qué modelos tienen adopción adecuada?", "adoption", {"ADECUADA"}, None, None),
    ("modelos con adopción parcial", "adoption", {"PARCIAL"}, None, None),
    ("modelos con adopción plena", "adoption", {"PLENA"}, None, None),
    ("Dame los modelos con adopción buena o adecuada", "adoption", {"BUENA", "ADECUADA"}, None, None),
    ("modelos con adopción buena, adecuada o parcial", "adoption", {"BUENA", "ADECUADA", "PARCIAL"}, None, None),
    ("modelos de Pampa con adopción buena", "adoption", {"BUENA"}, "Pampa", None),
    ("modelos de Kensing con adopción plena", "adoption", {"PLENA"}, "Kensing", None),
    ("modelos de Ventas con adopción buena", "adoption", {"BUENA"}, None, "SALES"),
    ("modelos de Kensing en Finanzas con adopción buena o adecuada", "adoption", {"BUENA", "ADECUADA"}, "Kensing", "FINANCE"),
    ("modelos con confianza alta", "confidence", {"HIGH"}, None, None),
    ("modelos con confianza media", "confidence", {"MEDIUM"}, None, None),
    ("modelos con confianza baja", "confidence", {"LOW"}, None, None),
    ("modelos con confianza alta o media", "confidence", {"HIGH", "MEDIUM"}, None, None),
    ("modelos de Loma con confianza baja", "confidence", {"LOW"}, "Loma", None),
    ("¿Qué entradas de Pampa tienen adopción parcial?", "adoption", {"PARCIAL"}, "Pampa", None),                    # cero
]


@pytest.mark.parametrize("text,indicator,levels,client,lob", LEVEL_CASES)
def test_a_level_query_returns_exactly_the_entries_of_the_oracle_in_order(world, text, indicator, levels, client, lob):
    expected = levels_oracle(world, indicator, levels, client=client, lob=lob)
    chat = Chat()
    first = chat.ask(text)
    assert not first.llm, text
    assert head_count(first) == len(expected.ids), (text, first.text)
    assert first.refs == expected.ids[:10]                                                                  # primera página: mismo orden estable que el oráculo
    assert walk(chat, first) == expected.ids                                                                # todas las páginas: ni duplicados ni omisiones


@pytest.mark.parametrize("text,indicator,levels,client,lob", LEVEL_CASES)
def test_the_head_echoes_the_effective_filters_and_the_denominator(world, text, indicator, levels, client, lob):
    expected = levels_oracle(world, indicator, levels, client=client, lob=lob)
    r = Chat().ask(text)
    label = ind.BY_ID[indicator].label
    assert label in r.text and f"«{ind.level_text(ind.BY_ID[indicator], tuple(levels))}»" in r.text                       # indicador y niveles efectivos
    assert f"de {expected.universe}; {expected.evaluated} con valor comparable" in r.text                    # denominador: universo y comparables
    if client:
        assert client in r.text
    if lob:
        assert LOB_LABEL[lob] in r.text


def test_a_single_level_says_all_rows_share_it_and_never_presents_a_ranking(world):
    r = Chat().ask("modelos con adopción buena")
    assert "Todas comparten el nivel «Buena»" in r.note and "no implica un ranking" in r.note
    assert "Empatan" not in r.note and "El corte cae dentro de un empate" not in r.note
    assert "<ol" not in r.raw and not re.search(r"assistant-result-title\">\s*(?:#?\d+[.)°º])", r.raw)


def test_several_levels_group_rows_by_level_in_the_order_of_the_source(world):
    expected = levels_oracle(world, "adoption", {"PLENA", "BUENA"})
    r = Chat().ask("modelos con adopción plena o buena")
    assert r.refs == expected.ids[:10]
    levels_in_order = [_adoption(spec4(world.index[i])) for i in expected.ids]
    assert levels_in_order == sorted(levels_in_order, key=LEVEL_POS.get)                                    # primero todas las Plena, luego las Buena


def test_a_level_without_entries_is_an_explicit_none_with_its_denominator(world):
    expected = levels_oracle(world, "adoption", {"PARCIAL"}, client="Pampa")
    assert expected.ids == []                                                                               # Pampa no tiene ninguna parcial en este mundo
    r = Chat().ask("modelos de Pampa con adopción parcial")
    assert r.text.startswith("Ninguna entrada") and "Adopción metodológica" in r.text and "«Parcial»" in r.text
    assert f"de {expected.universe}; {expected.evaluated} con valor comparable" in r.text and not r.rows and not r.llm


def test_what_is_not_compared_is_counted_by_reason(world):
    expected = levels_oracle(world, "adoption", {"BUENA"})
    r = Chat().ask("modelos con adopción buena")
    assert expected.excluded["STALE"] and expected.excluded["NOT_DETERMINABLE"] and expected.excluded["NOT_ASSESSED"]
    assert f"{expected.excluded['STALE']} desactualizadas" in r.note
    assert f"{expected.excluded['NOT_DETERMINABLE']} no determinables" in r.note
    assert f"{expected.excluded['NOT_ASSESSED']} sin assessment vigente" in r.note                            # incluye no iniciado, no aplica, no disponible y no compatible: es el vocabulario del paso 2


def test_stale_assessments_never_count_as_a_level_even_if_they_keep_a_judgment(world):
    stale = [e.entry_id for e, s in _scope(world, pred=lambda s: s["state"] == svc.STALE)]
    assert stale                                                                                            # el mundo tiene desactualizados, todos CON nivel persistido (Buena)
    chat = Chat()
    shown = set(walk(chat, chat.ask("modelos con adopción buena")))
    assert not shown & set(stale) and shown == set(levels_oracle(world, "adoption", {"BUENA"}).ids)


def test_count_phrasing_answers_the_number_first(world):
    expected = levels_oracle(world, "adoption", {"BUENA"}, client="Pampa")
    counted = Chat().ask("¿Cuántos modelos de Pampa tienen adopción buena?")
    plain = Chat().ask("modelos de Pampa con adopción buena")
    assert counted.text.startswith(f"Son {len(expected.ids)} entradas navegables") and plain.text.startswith(f"Hay {len(expected.ids)} entradas navegables")
    assert counted.refs == plain.refs == expected.ids[:10]


def test_a_requested_page_size_is_respected(world):
    r = Chat().ask("mostrame 3 modelos con adopción buena")
    assert len(r.refs) == 3 and r.refs == levels_oracle(world, "adoption", {"BUENA"}).ids[:3]


# ── contexto: el filtro viaja con las páginas y las referencias ─────────────────────────────────────────────────────────────────────────────
def test_the_context_stores_the_levels_as_parameters_and_the_identities_in_order(world):
    chat = Chat()
    first = chat.ask("modelos con adopción buena o adecuada")
    st = chat.state
    assert st.spec.capability == "rank" and st.spec.indicator == "adoption" and st.spec.levels == ("BUENA", "ADECUADA")
    assert [r[1] for r in st.refs] == first.refs and all(r[0] == caps.CATALOG_ENTRY for r in st.refs)
    assert "Buena" not in repr(st) and "http" not in repr(st)                                               # ni rótulos ni URLs: solo identidades y parámetros


def test_pagination_keeps_the_level_filter_and_the_second_reference_opens_the_filtered_second(world):
    expected = levels_oracle(world, "adoption", {"BUENA"})
    chat = Chat()
    chat.ask("modelos con adopción buena")
    p2 = chat.ask("Mostrame los siguientes")
    assert p2.refs == expected.ids[10:20] and "Página 2 de" in p2.text
    opened = chat.ask("Abrí el segundo")
    assert opened.refs == [expected.ids[11]] and "Abro el segundo" in opened.text


def test_a_reference_is_refused_if_a_level_of_the_list_changed(world, monkeypatch):
    expected = levels_oracle(world, "adoption", {"BUENA"})
    chat = Chat()
    first = chat.ask("modelos con adopción buena")
    victim = first.refs[3]
    real = caps.governed_read

    def mutated(flows):
        g = real(flows)
        if g is not None and victim in g.statuses:
            g.statuses[victim].judgment["level"] = "PARCIAL"                                                # cambió el nivel de una entrada de la lista
        return g
    monkeypatch.setattr(caps, "governed_read", mutated)
    refused = chat.ask("Abrí el primero")
    assert "La lista cambió" in refused.text and not refused.rows and expected.ids[0] == first.refs[0]


# ── aclaraciones: un nivel que no se puede consumir nunca se ignora ─────────────────────────────────────────────────────────────────────────
def test_a_level_the_indicator_does_not_have_is_clarified_with_its_real_levels(world):
    r = Chat().ask("modelos con adopción alta")
    assert "No reconozco ese nivel para Adopción metodológica" in r.text and "Plena, Buena, Adecuada y Parcial" in r.text
    assert not r.rows and not r.llm and len(r.asks) == 4
    assert [label for _q, label in r.asks] == [f"Adopción metodológica: {lv}" for lv in ("Plena", "Buena", "Adecuada", "Parcial")]


def test_each_level_option_of_a_clarification_resolves_to_exactly_its_oracle(world):
    chat = Chat()
    clar = chat.ask("modelos de Pampa con adopción alta")
    for question, label in clar.asks:
        level = {"Plena": "PLENA", "Buena": "BUENA", "Adecuada": "ADECUADA", "Parcial": "PARCIAL"}[label.split(": ")[1]]
        expected = levels_oracle(world, "adoption", {level}, client="Pampa")
        r = Chat().ask(question)
        assert head_count(r) == len(expected.ids) and r.refs == expected.ids[:10], (question, r.text)


@pytest.mark.parametrize("text,indicator", [("modelos con cobertura buena", "Cobertura estricta"), ("modelos con consistencia de tipos alta", "Consistencia de tipos"),
                                            ("modelos con casos a revisar altos", "Casos a revisar")])
def test_a_level_asked_of_an_indicator_without_levels_is_clarified_never_ranked(world, text, indicator):
    r = Chat().ask(text)
    assert f"**{indicator}**" in r.raw or indicator in r.text
    assert "no tiene niveles gobernados" in r.text and not r.rows and not r.llm and "ordenadas por" not in r.text
    assert r.asks and indicator in r.asks[0][1]                                                             # la opción ordena por esa métrica, con los filtros ya aplicados


def test_a_negated_level_is_not_silently_turned_into_the_level(world):
    r = Chat().ask("modelos sin adopción plena")
    assert "Todavía no filtro por la negación de un nivel" in r.text and not r.rows and not r.llm and len(r.asks) == 4


def test_two_indicators_with_levels_are_clarified_and_each_option_keeps_only_its_levels(world):
    chat = Chat()
    r = chat.ask("modelos con adopción buena y confianza alta")
    assert "más de una métrica" in r.text and not r.rows and not r.llm
    assert [label for _q, label in r.asks] == ["Adopción metodológica: Buena", "Confianza de la evidencia: Alta"]
    for question, label in r.asks:
        indicator, level = ("adoption", {"BUENA"}) if label.startswith("Adopción") else ("confidence", {"HIGH"})
        expected = levels_oracle(world, indicator, level)
        again = Chat().ask(question)
        assert head_count(again) == len(expected.ids) and again.refs == expected.ids[:10]


def test_several_clients_with_a_level_are_clarified_not_guessed(world):
    r = Chat().ask("modelos de Pampa y Kensing con adopción buena")
    assert "más de un cliente" in r.text and not r.rows and not r.llm


# ── los canónicos del servidor dan ida y vuelta ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("indicator,levels,client,lob", [("adoption", ("BUENA",), None, None), ("adoption", ("BUENA", "ADECUADA"), "Pampa", None), ("confidence", ("HIGH", "MEDIUM"), None, "SALES"),
                                                         ("adoption", ("PLENA", "BUENA", "PARCIAL"), "Kensing", "FINANCE")])
def test_the_canonical_question_with_levels_round_trips(world, indicator, levels, client, lob):
    question = ind.canonical_question(indicator, ind.BEST_FIRST, client=client, lob_label=LOB_LABEL.get(lob), levels=levels)
    frame = aq.parse(question)
    assert frame.indicators == (indicator,) and sorted(lv for _o, lv in frame.levels) == sorted(levels)
    expected = levels_oracle(world, indicator, set(levels), client=client, lob=lob)
    r = Chat().ask(question)
    assert head_count(r) == len(expected.ids) and r.refs == expected.ids[:10]


# ══ 2. LO QUE NO CAMBIA: falsos positivos y falsos negativos del ruteo ═════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text", [
    "modelos buenos", "modelos parciales", "¿Qué flujos tienen mirada Danwor parcial?", "los modelos plenos", "Buenas prácticas de nombres",         # nivel SIN su indicador: veredicto legado
    "¿Qué entradas tienen mejor cobertura estricta?", "¿Qué entradas tienen mejor adopción metodológica?", "Mostrame los modelos de Ventas",        # consultas del paso 2 y del inventario
    "¿Qué modelos tenemos?", "¿Cuántos modelos de Ventas hay?",
])
def test_queries_without_4a_vocabulary_are_not_claimed_by_the_new_target(world, text):
    assert _ask_query(text) is None


@pytest.mark.parametrize("text", [
    "¿Qué documentación hay sobre adopción metodológica buena?", "Mostrame la guía de confianza alta", "¿Qué recursos tenemos sobre adopción plena?",                  # documentales con vocabulario del Catálogo
    "¿Qué es la adopción metodológica buena?", "¿Cómo se calcula la confianza alta?", "Buenas prácticas para una adopción metodológica plena",           # metodológicas
    "¿Qué es la confianza de la evidencia?", "Ejemplos de adopción parcial",
])
def test_documentary_and_methodological_questions_sharing_the_vocabulary_are_not_claimed(world, text):
    assert _ask_query(text) is None


@pytest.mark.parametrize("text,expected_levels", [
    ("Pampa: modelos con adopción buena", {"BUENA"}), ("adopción buena, modelos de Pampa", {"BUENA"}), ("entradas con adopción buena de Ventas", {"BUENA"}),
    ("¿Qué entradas de Kensing tienen una adopción plena?", {"PLENA"}), ("flujos con confianza alta", None),
])
def test_data_questions_phrased_differently_are_still_recognised(world, text, expected_levels):
    r = _ask_query(text)
    assert r is not None and r.result is not None and r.result.ranking is not None and r.result.ranking.matched is not None, text


def test_a_query_that_fails_in_execution_says_so_instead_of_falling_to_the_free_llm(world, monkeypatch):
    monkeypatch.setattr(nav, "_ranking_result", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla simulada")))
    r = Chat().ask("modelos con adopción buena")
    assert "No pude completar la consulta en este momento" in r.text and not r.llm and not r.rows


# ══ 3. MUTACIONES: si el código ignora el calificador, estos tests lo detectan ════════════════════════════════════════════════════════════════
def _check_level_counts(world):
    for text, indicator, levels, client, lob in LEVEL_CASES:
        expected = levels_oracle(world, indicator, levels, client=client, lob=lob)
        r = Chat().ask(text)
        assert head_count(r) == len(expected.ids), text
        assert r.refs == expected.ids[:10], text


def test_mutation_a_level_ignored_is_detected(world, monkeypatch):
    _check_level_counts(world)                                                                              # sin mutar, pasa
    real = ind.rank_scope
    monkeypatch.setattr(ind, "rank_scope", lambda *a, **k: real(*a, **{**k, "levels": ()}) if len(a) < 9 else real(*a[:8]))
    with pytest.raises(AssertionError):
        _check_level_counts(world)


def test_mutation_a_union_of_levels_treated_as_one_level_is_detected(world, monkeypatch):
    real = ind.rank_scope

    def only_first_level(ind_, order, scope, governed, pack, limit=10, offset=0, scope_key=(), levels=()):
        return real(ind_, order, scope, governed, pack, limit, offset, scope_key, tuple(levels)[:1])
    monkeypatch.setattr(ind, "rank_scope", only_first_level)
    with pytest.raises(AssertionError):
        _check_level_counts(world)


def test_mutation_stale_counted_as_current_is_detected(world, monkeypatch):
    real_gate = ind._assessment_gate

    def lenient(entry, g):
        status = g.statuses[entry.entry_id] if g is not None else None
        if status is not None and status.state == svc.STALE:
            return status, None                                                                             # un desactualizado pasa como vigente
        return real_gate(entry, g)
    monkeypatch.setattr(ind, "_assessment_gate", lenient)
    with pytest.raises(AssertionError):
        _check_level_counts(world)
