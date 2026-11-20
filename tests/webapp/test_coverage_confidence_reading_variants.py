"""Alcance B — cierre: las lecturas de Cobertura y Confianza (capa de presentación) son correctas en TODAS las variantes, no solo al 100 %.
Usa las funciones reales del presenter con datos en memoria (no persiste ni regenera). Verifica: denominador = aspectos APLICABLES, que «parcial» nunca se
lea como «por completo», y que Confianza conserve su significado (≠ Cobertura, ≠ Calidad)."""
from __future__ import annotations

import re

from webapp.services import assessment_presenter as ap


def _rs(applicable, assessed, partially_assessed, not_assessed, not_applicable):
    return {"applicable": applicable, "assessed": assessed, "partially_assessed": partially_assessed,
            "not_assessed": not_assessed, "not_applicable": not_applicable}


def test_complete_coverage_all_assessed_no_fabricated_partials():
    t = ap._coverage_reading(_rs(40, 40, 0, 0, 1))
    assert "Se evaluaron por completo los 40 aspectos requeridos" in t
    assert "1 aspecto no aplica y queda fuera del cálculo" in t
    assert "en parte" not in t and "sin evaluar" not in t                     # no inventa parciales ni no-evaluados


def test_mixed_keeps_applicable_denominator_and_separates_partial_from_complete():
    t = ap._coverage_reading(_rs(10, 6, 3, 1, 2))
    assert "De los 10 aspectos requeridos para este modelo" in t             # denominador = aplicables (10), NUNCA 12 (no suma los no aplicables)
    assert "6 se evaluaron por completo" in t
    assert "3 en parte" in t and "1 sin evaluar" in t                         # parcial y no-evaluado explícitos, distintos de «por completo»
    assert "2 aspectos no aplican y quedan fuera del cálculo" in t


def test_only_partial_is_never_reported_as_complete():
    t = ap._coverage_reading(_rs(5, 0, 5, 0, 0))
    assert "0 se evaluaron por completo" in t and "5 en parte" in t
    assert "no aplica" not in t                                               # na = 0 → sin cláusula de exclusión


def test_only_not_assessed():
    t = ap._coverage_reading(_rs(4, 2, 0, 2, 0))
    assert "2 se evaluaron por completo" in t and "2 sin evaluar" in t
    assert "en parte" not in t


def test_singular_plural_of_not_applicable():
    assert "1 aspecto no aplica y queda fuera" in ap._coverage_reading(_rs(3, 3, 0, 0, 1))
    assert "2 aspectos no aplican y quedan fuera" in ap._coverage_reading(_rs(3, 3, 0, 0, 2))


def test_zero_applicable_is_contemplated():
    t = ap._coverage_reading(_rs(0, 0, 0, 0, 0))
    assert "no tiene aspectos metodológicos aplicables para evaluar" in t


def test_the_count_reported_as_complete_is_always_assessed_not_partial():
    # El número leído como «evaluado por completo» debe ser SIEMPRE ``assessed``, en las dos redacciones (todo evaluado / mezcla).
    for appl, done, part, no in [(10, 6, 3, 1), (8, 8, 0, 0), (7, 1, 5, 1), (4, 0, 0, 4)]:
        t = ap._coverage_reading(_rs(appl, done, part, no, 0))
        if done == appl:
            assert f"por completo los {done} aspectos" in t, (t, done)
        else:
            m = re.search(r"(\d+) se evaluaron por completo", t)
            assert m and int(m.group(1)) == done, (t, done)
        # los parciales, si existen, van en el paréntesis «en parte», nunca como «por completo»
        if part:
            assert f"{part} en parte" in t and f"{part} se evaluaron por completo" not in t


def test_confidence_reading_keeps_its_meaning_distinct_from_coverage_and_quality():
    r = ap._CONFIDENCE_READING
    assert "evidencia que sí se pudo evaluar" in r                            # confianza = fiabilidad de lo evaluado
    assert "No mide la calidad" in r                                          # ≠ calidad del modelo
    assert "independiente de la cobertura" in r                              # ≠ cobertura
