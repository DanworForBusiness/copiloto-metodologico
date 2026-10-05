"""Taxonomia de la evidencia de NOMBRES: higiene de convencion vs calidad del contrato semantico (S2, evidence release). Estatica y descriptiva.

Decision del owner (Gate 8A.2): "naming fuera del juicio" mezclaba dos cosas.
  HYGIENE            convencion cosmetica (prefijo, mayusculas, estilo de nombre tecnico, desvios de seccion 28): informativa, NUNCA direccional.
  SEMANTIC_CONTRACT  calidad del contrato semantico expuesto (nombre de negocio igual a un codigo opaco de la fuente, etiqueta de medida igual al identificador tecnico,
                     vocabulario fuente arrastrado por un contrato supuestamente armonizado, vocabularios incompatibles expuestos al consumo).
  MIXED              la misma regla alcanza ambas cosas.
Cada item lleva su ``status``: ACTIVE (existe y se evalua), REDESIGN_CANDIDATE (la regla actual es demasiado estrecha para el criterio y la evidencia descriptiva nueva
la reemplaza como insumo: no se retira todavia), CONTEXT_ONLY (pregunta de contexto, no hallazgo). Ningun item es direccional en esta entrega (``directional`` es siempre
False): la capa de juicio futura decide con esta separacion, sin heredar un "naming quality" generico.
"""
from __future__ import annotations

CAT_HYGIENE, CAT_SEMANTIC_CONTRACT, CAT_MIXED = "HYGIENE", "SEMANTIC_CONTRACT", "MIXED"
NAMING_CATEGORIES = (CAT_HYGIENE, CAT_SEMANTIC_CONTRACT, CAT_MIXED)
ST_ACTIVE, ST_REDESIGN, ST_CONTEXT = "ACTIVE", "REDESIGN_CANDIDATE", "CONTEXT_ONLY"
NAMING_STATUSES = (ST_ACTIVE, ST_REDESIGN, ST_CONTEXT)
KINDS = ("RULE", "FACT", "CONTEXT_QUESTION")


def _t(item: str, kind: str, category: str, status: str, facts: tuple[str, ...], note: str) -> dict:
    return {"item": item, "kind": kind, "category": category, "status": status, "directional": False, "evidence_facts": list(facts), "note": note}


NAMING_TAXONOMY: tuple[dict, ...] = (
    _t("section_28_artifact_name_conformity", "FACT", CAT_HYGIENE, ST_ACTIVE, ("naming.artifact_name_contract", "naming.artifact_name_populations"),
       "letra de artefacto/uso no homologada y prefijo de capa de la seccion 28: convencion, no contrato semantico"),
    _t("ROLE_LAYER_DIVERGENCE", "RULE", CAT_HYGIENE, ST_ACTIVE, ("ingestion.inferred_layer_vs_lineage_depth",), "capa codificada en el nombre frente a posicion/rol declarado: convencion de capa"),
    _t("field_name_form_and_case", "FACT", CAT_HYGIENE, ST_ACTIVE, ("naming.field_name_forms",), "forma mecanica y estilo de mayusculas del nombre tecnico: estilo"),
    _t("local_naming_convention_context", "CONTEXT_QUESTION", CAT_HYGIENE, ST_CONTEXT, ("naming.convention_context",), "convencion local historica que explica un desvio; nunca reescribe el cumplimiento"),
    _t("FIELD_VOCABULARY_MIX", "RULE", CAT_MIXED, ST_ACTIVE, ("naming.same_normalized_label_multiple_technical_names",),
       "una misma etiqueta con varios nombres tecnicos: alcanza el contrato expuesto (semantico) y capas internas (higiene)"),
    _t("LABEL_NOT_ENRICHING", "RULE", CAT_SEMANTIC_CONTRACT, ST_REDESIGN, ("naming.field_name_contract", "am.field_semantics"),
       "hoy exige que TODOS los campos repitan su nombre; la evidencia por campo (am.field_semantics) es un insumo mas fiel"),
    _t("OPAQUE_FIELD_NAME", "RULE", CAT_SEMANTIC_CONTRACT, ST_REDESIGN, ("naming.field_name_forms", "am.field_semantics"),
       "hoy solo dispara con prefijo de namespace propio; la coincidencia exacta con una columna remota (am.field_semantics) es un insumo verificable"),
    _t("LABEL_NAME_MISMATCH", "CONTEXT_QUESTION", CAT_SEMANTIC_CONTRACT, ST_CONTEXT, ("naming.field_name_contract",),
       "no existe como regla: solo la pregunta de contexto label_name_mismatch_intent (¿es intencional que el nombre tecnico difiera de su etiqueta?)"),
    _t("business_vocabulary_expected_by_project", "CONTEXT_QUESTION", CAT_SEMANTIC_CONTRACT, ST_CONTEXT, ("naming.field_name_contract",), "vocabulario de negocio que el proyecto espera"),
    _t("coexisting_name_vocabularies_decision", "CONTEXT_QUESTION", CAT_SEMANTIC_CONTRACT, ST_CONTEXT, ("naming.same_normalized_label_multiple_technical_names",),
       "si la coexistencia de vocabularios es deliberada"),
    _t("published_field_and_measure_semantics", "FACT", CAT_SEMANTIC_CONTRACT, ST_ACTIVE, ("am.field_semantics",),
       "etiqueta == nombre tecnico y coincidencia exacta con una columna remota por campo y medida publicados"),
    _t("harmonization_vocabulary_carry_through", "FACT", CAT_SEMANTIC_CONTRACT, ST_ACTIVE, ("harmonization.contract_character",),
       "arrastre del vocabulario fuente + origen de etiquetas + mapeos de cada artefacto candidato a armonizacion"),
)
