"""Contrato EDITORIAL del Assessment (Alcance B): la simplificación del lenguaje NO cambia el razonamiento metodológico.

No toca motor ni scoring: fija que los textos de presentación (`assessment_presenter`) conserven la condición/alcance/implicación que representan, con lenguaje
directo para un consultor SAP. Cada aserción comprueba los *tokens de significado* (no solo el literal), para que un reemplazo que cambie el sentido rompa el test."""
from __future__ import annotations

from webapp.services import assessment_presenter as ap


def test_level_headlines_are_conclusion_first():
    # Conclusión directa (sujeto = el modelo), sin el preámbulo «La evidencia observada muestra…».
    for lvl in ("BUENA", "ADECUADA", "PARCIAL"):
        assert "La evidencia observada muestra" not in ap._JDG_LEVEL_HEADLINE[lvl]
    assert ap._JDG_LEVEL_HEADLINE["BUENA"].startswith("El modelo")
    assert ap._JDG_LEVEL_HEADLINE["ADECUADA"].startswith("El modelo") and "oportunidades puntuales de mejora" in ap._JDG_LEVEL_HEADLINE["ADECUADA"]
    assert ap._JDG_LEVEL_HEADLINE["PARCIAL"].startswith("La adopción es parcial")
    assert "La evidencia observada" not in ap._JDG_GENERIC_HEADLINE


def test_jdg_statements_preserve_meaning_without_jargon():
    s = ap._JDG_STATEMENT
    # 3.1 (RELEVANT_WEAKNESS): crudo llega al contrato publicado sin un contrato que lo centralice (sin «centralidad de contrato» jerga).
    k31 = "lek.unrestored_raw_vocabulary_reaches_published_without_contract_centrality"
    assert "centralidad de contrato" not in s[k31]
    assert "contrato publicado" in s[k31] and "centralice" in s[k31] and ("sin transformar" in s[k31] or "sin armonizar" in s[k31])
    # 3.2 (MATERIAL vía testigo publicado): responsabilidad no resuelta que alcanza al consumidor.
    k32 = "lek.unrestored_responsibility_leakage"
    assert "responsabilidad" in s[k32].lower() and "consumidor" in s[k32]
    # 3.4 (PRO-MAT-02): tabla física cruda resuelta en vistas, con filtrado e integración, que produce publicado — NO reducido a «existe una vista».
    k34 = "pro.source_responsibility_displaced_with_produced_consequence"
    assert "tabla física cruda" in s[k34] and "filtrado e integración" in s[k34] and "publica" in s[k34]
    # Consistencia terminológica: «aguas abajo» no debe sobrevivir en los hallazgos.
    assert not any("aguas abajo" in v for v in s.values())


def test_material_failed_texts_keep_each_condition():
    m = ap._MATERIAL_FAILED_TEXT
    assert "una sola fuente" in m["M2"] and "integrar" in m["M2"]              # M2: resolución single-source antes de integrar
    assert "vista de modelado" in m["M3"] and "crudo" in m["M3"]              # M3: existe (o no) una vista que compensa
    assert "contrato publicado" in m["M4"]                                     # M4: produce medidas/claves del publicado
    assert "filtra registros" in m["M5a"]                                      # M5a: filtrado de registros de la fuente
    assert "otras fuentes" in m["M5b"] and "normalizar" in m["M5b"]           # M5b: integración con otras fuentes antes de normalizar
    assert "expresiones" in m["COVERAGE"] and "linaje" in m["COVERAGE"]        # COVERAGE: falta evidencia de expresiones / linaje


def test_source_nd_causes_stay_uncertain_not_categorical():
    c = ap._JDG_ND_CAUSE
    # ND/SOURCE: incertidumbre, nunca un incumplimiento afirmado; y SIN reducir la clase a un binario (hay 3 SOURCE_CLASSES: gobernado / crudo físico / externo indeterminado).
    assert "no se pudo determinar" in c["SOURCE_CONTRACT_CLASS_UNDETERMINED"] and "tipo de contrato" in c["SOURCE_CONTRACT_CLASS_UNDETERMINED"]
    assert "no todo contacto" in c["SOURCE_CONTACT_NOT_ALL_GOVERNED"]
    assert ap._JDG_ND_STATE_TEXT["NO_STRENGTH"].startswith("Evaluado") and "fortaleza" in ap._JDG_ND_STATE_TEXT["NO_STRENGTH"]


def test_hypotheses_do_not_leak_internal_judgment_jargon():
    # Juicio ≠ Assessment: las hipótesis no afirman nivel y no usan el término interno «el Juicio».
    for v in ap._WWC_HYPOTHESIS.values():
        assert "todavía no" in v                     # no afirma un nivel: queda explícito que todavía no se determina/calcula
        assert "el Juicio" not in v                 # Juicio ≠ Assessment: no se filtra el término interno
