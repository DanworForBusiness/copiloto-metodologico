"""Paso 4A.0 — vocabulario gobernado y reconocimiento tipado (``assistant_query``). Módulo puro: estos tests no necesitan Catálogo ni HTTP.

Lo que fijan: el vocabulario de NIVELES sale de los niveles de cada indicador y el de ESTADOS de los estados reales del Assessment (con una guarda que falla si el contrato suma un estado sin
concepto); «sin assessment» (ausencia de EVALUACIÓN) NO es «no aplica / no disponible / no compatible» (ausencia de RESULTADO UTILIZABLE) y cada concepto se prueba por separado; los niveles solo
se interpretan junto a su indicador (colisión con el veredicto legado); y toda operación no soportada cae en una taxonomía cerrada.
"""
from __future__ import annotations

import re

import pytest

from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import assistant_indicators as ind
from webapp.services import assistant_query as q


# ── D-5: ausencia de EVALUACIÓN ≠ ausencia de RESULTADO UTILIZABLE ───────────────────────────────────────────────────────────────────────────
def test_every_real_assessment_state_has_exactly_one_single_state_concept():
    assert q.unmapped_states() == ()                                                                  # si el contrato suma un estado, esta correspondencia tiene que decirlo
    for state in svc.STATES:
        owners = [c.id for c in q.STATE_CONCEPTS if not c.group and not c.judgment and state in c.states]
        assert len(owners) == 1, (state, owners)
        assert q.concept_for_state(state).id == owners[0]


def test_absence_of_evaluation_is_exactly_the_not_yet_assessed_state():
    c = q.STATE_BY_ID[q.NOT_EVALUATED_ID]
    assert c.states == (svc.NOT_YET_ASSESSED,)
    assert c.label == presenter.STATE_LABELS[svc.NOT_YET_ASSESSED] == "Sin assessment"


def test_absence_of_a_usable_result_is_a_separate_group_of_the_other_three_states():
    g = q.STATE_BY_ID[q.NO_USABLE_RESULT_ID]
    assert g.group and set(g.states) == {svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA}
    assert svc.NOT_YET_ASSESSED not in g.states and svc.CURRENT not in g.states and svc.STALE not in g.states
    assert not set(g.states) & set(q.STATE_BY_ID[q.NOT_EVALUATED_ID].states)                         # los dos conceptos NO se solapan


@pytest.mark.parametrize("phrase,concept", [
    ("modelos sin assessment", q.NOT_EVALUATED_ID), ("modelos sin evaluar", q.NOT_EVALUATED_ID), ("modelos que no tienen assessment", q.NOT_EVALUATED_ID),
    ("modelos no evaluados", q.NOT_EVALUATED_ID),
    ("modelos que no aplica", q.NOT_APPLICABLE_ID), ("entradas con assessment no disponible", q.FAILED_ID), ("entradas con informe no compatible", q.INCOMPATIBLE_ID),
    ("modelos sin resultado utilizable", q.NO_USABLE_RESULT_ID),
    ("modelos sin assessment vigente", q.NOT_CURRENT_ID), ("modelos que no tienen assessment vigente", q.NOT_CURRENT_ID),
    ("modelos desactualizados", q.STALE_ID), ("modelos vigentes", q.CURRENT_ID), ("modelos con adopción no determinable", q.NOT_DETERMINABLE_ID),
])
def test_each_state_phrase_names_exactly_one_concept_and_never_a_neighbour(phrase, concept):
    assert q.parse(phrase).states == (concept,)


def test_no_state_term_belongs_to_two_concepts():
    seen: dict[str, str] = {}
    for c in q.STATE_CONCEPTS:
        for t in c.terms:
            assert t not in seen, (t, seen[t], c.id)
            seen[t] = c.id


@pytest.mark.parametrize("concept", [c.id for c in q.STATE_CONCEPTS])
def test_the_canonical_phrase_of_every_concept_round_trips_through_the_parser(concept):
    c = q.STATE_BY_ID[concept]
    assert q.parse(f"¿Qué entradas {c.canonical}?").states == (concept,)


def test_the_distribution_partitions_the_universe_and_separates_both_absences():
    states = [svc.CURRENT] * 5 + [svc.STALE] * 2 + [svc.NOT_YET_ASSESSED] * 3 + [svc.NOT_APPLICABLE] * 2 + [svc.FAILED] + [svc.INCOMPATIBLE_SCHEMA] * 4
    lines, breakdown = q.distribution(states)
    assert sum(n for _id, _label, n in lines) == len(states) == 17                                    # las líneas PARTEN el universo
    by = {cid: n for cid, _label, n in lines}
    assert by == {q.CURRENT_ID: 5, q.STALE_ID: 2, q.NOT_EVALUATED_ID: 3, q.NO_USABLE_RESULT_ID: 7}   # «sin assessment» (3) y «sin resultado utilizable» (7) por separado
    assert dict(breakdown) == {"No aplica": 2, "Assessment no disponible": 1, "Informe no compatible": 4}


def test_the_distribution_lists_only_what_exists():
    lines, breakdown = q.distribution([svc.CURRENT] * 3)
    assert lines == ((q.CURRENT_ID, "Assessment vigente", 3),) and breakdown == ()


# ── Niveles: derivados de los indicadores ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_every_level_of_every_ordinal_indicator_has_a_label_and_a_term_and_belongs_to_one_indicator():
    for i in ind.INDICATORS:
        if i.kind != "ordinal":
            assert i.level_labels == () and i.levels == ()
            continue
        assert tuple(lv for lv, _ in i.level_labels) == i.levels                                      # mismos niveles y mismo orden que la fuente
        for lv, label in i.level_labels:
            owners = {q.LEVEL_TERMS[t] for t in q.LEVEL_TERMS if q.LEVEL_TERMS[t][1] == lv and q.LEVEL_TERMS[t][0] == i.id}
            assert owners == {(i.id, lv)}
            assert any(q.LEVEL_TERMS.get(f) == (i.id, lv) for f in q._forms(label))
    assert len({term for term in q.LEVEL_TERMS}) == len(q.LEVEL_TERMS)


def test_level_labels_are_the_visible_labels_of_the_existing_presenters():
    assert dict(ind.BY_ID["adoption"].level_labels) == presenter._JDG_LEVEL_LABEL
    assert dict(ind.BY_ID["confidence"].level_labels) == {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Baja"}


@pytest.mark.parametrize("label,expected", [("Buena", {"buena", "buenas", "bueno", "buenos"}), ("Parcial", {"parcial", "parciales"}), ("Alta", {"alta", "altas", "alto", "altos"}),
                                            ("Adecuada", {"adecuada", "adecuadas", "adecuado", "adecuados"})])
def test_gender_and_number_inflections(label, expected):
    assert set(q._forms(label)) == expected


@pytest.mark.parametrize("text,indicator,levels", [
    ("¿Qué modelos tienen adopción buena?", "adoption", ["BUENA"]),
    ("modelos con adopción buena o adecuada", "adoption", ["ADECUADA", "BUENA"]),
    ("entradas con adopción parcial", "adoption", ["PARCIAL"]),
    ("modelos con adopción plena", "adoption", ["PLENA"]),
    ("modelos con confianza alta", "confidence", ["HIGH"]),
    ("modelos con confianza media", "confidence", ["MEDIUM"]),
    ("¿cuántas entradas tienen confianza baja?", "confidence", ["LOW"]),
])
def test_levels_are_recognised_next_to_their_indicator(text, indicator, levels):
    f = q.parse(text)
    assert f.indicators == (indicator,) and sorted(lv for _o, lv in f.levels) == sorted(levels)
    assert f.vocabulary and f.ops == ()


@pytest.mark.parametrize("text", ["modelos buenos", "modelos parciales", "¿qué flujos tienen mirada Danwor parcial?", "los modelos plenos", "buenas prácticas de nombres", "modelos con nivel alto"])
def test_a_level_word_without_its_indicator_is_not_governed_vocabulary(text):
    """Colisiona con el veredicto legado («mirada Danwor parcial»): se queda como está hoy."""
    f = q.parse(text)
    assert f.indicators == () and f.vocabulary is False


def test_a_level_term_next_to_the_wrong_indicator_is_still_reported_so_the_caller_can_clarify():
    f = q.parse("modelos con cobertura buena")
    assert f.indicators == ("coverage",) and f.levels == (("adoption", "BUENA"),) and f.vocabulary


def test_media_is_a_level_after_confidence_and_an_aggregate_only_as_the_media_of_something():
    assert q.parse("modelos con confianza media").ops == ()
    assert q.parse("la media de cobertura de Kensing").ops == (q.AGGREGATE,)
    assert q.parse("modelos con confianza media de la evidencia").levels == (("confidence", "MEDIUM"),)


# ── cantidad, agrupación, dimensión «estado» ────────────────────────────────────────────────────────────────────────────────────────────────
def test_count_group_by_and_state_dimension():
    f = q.parse("¿Cuántos modelos hay por cliente?")
    assert f.count and f.group_by == "client" and f.vocabulary
    assert q.parse("cantidad de modelos por línea de negocio").group_by == "lob"
    f = q.parse("Resumime el estado de Biogenesis")
    assert f.dimension_state and f.states == () and f.vocabulary
    assert q.parse("¿Cuántos modelos de Ventas hay?").vocabulary is False                             # el conteo SOLO no es 4A: lo sigue resolviendo el inventario


def test_a_state_term_is_reported_even_when_the_bare_word_estado_is_there_too():
    """Quien ejecuta da prioridad al concepto de estado sobre la dimensión «estado» (ambos quedan registrados: nada se ignora)."""
    f = q.parse("estado desactualizado de los modelos")
    assert f.states == (q.STALE_ID,) and f.dimension_state


# ── operaciones NO soportadas: taxonomía cerrada ────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,op", [
    ("Promedio de cobertura de Kensing", q.AGGREGATE), ("la mediana de casos a revisar", q.AGGREGATE), ("¿qué porcentaje de modelos tiene adopción buena?", q.AGGREGATE),
    ("la suma de los casos a revisar", q.AGGREGATE), ("cobertura 80%", q.AGGREGATE),
    ("Compará Pampa con Kensing", q.COMPARE), ("Pampa vs Kensing", q.COMPARE), ("comparación de cobertura entre clientes", q.COMPARE),
    ("modelos con cobertura mayor a 80", q.THRESHOLD), ("modelos con casos a revisar mas de 10", q.THRESHOLD), ("cobertura al menos 90", q.THRESHOLD),
    ("modelos sin adopción plena", q.NEGATED_LEVEL), ("modelos con adopción no buena", q.NEGATED_LEVEL),
])
def test_unsupported_operations_are_recognised_in_a_closed_taxonomy(text, op):
    assert op in q.parse(text).ops


@pytest.mark.parametrize("text", ["¿Qué modelos tienen adopción buena?", "modelos con adopción no determinable", "modelos de Kensing con confianza alta",
                                  "¿Qué entradas tienen mejor cobertura estricta?", "sumá Pampa", "modelos desactualizados", "mostrame 10 modelos"])
def test_supported_queries_name_no_unsupported_operation(text):
    assert q.parse(text).ops == ()


def test_no_determinable_is_a_state_not_a_negated_level():
    f = q.parse("modelos con adopción no determinable")
    assert f.states == (q.NOT_DETERMINABLE_ID,) and f.ops == () and f.levels == ()


def test_total_is_flagged_but_only_the_executor_decides_whether_it_aggregates():
    assert q.parse("casos a revisar de Kensing en total").total_word
    assert not q.parse("casos a revisar de Kensing").total_word


def test_complete_is_flagged_as_a_numeric_qualifier():
    assert q.parse("modelos con cobertura completa").threshold_word


# ── forma del resultado ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_masked_text_has_no_governed_term_left():
    f = q.parse("¿Cuántos modelos de Kensing tienen adopción buena o adecuada?")
    assert "buena" not in f.masked and "adecuada" not in f.masked and "adopcion" not in f.masked and "cuantos" not in f.masked
    assert re.search(r"\bkensing\b", f.masked)                                                         # lo que NO es vocabulario gobernado queda


def test_level_phrase_uses_the_source_order():
    assert q.level_phrase("adoption", ["ADECUADA", "BUENA"]) == "Buena o Adecuada"
    assert q.level_phrase("confidence", ["LOW"]) == "Baja"
    assert q.level_phrase("adoption", ["PLENA", "BUENA", "PARCIAL"]) == "Plena, Buena o Parcial"
