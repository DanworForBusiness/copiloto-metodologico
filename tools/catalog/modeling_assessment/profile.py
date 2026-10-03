"""Contrato PUBLICO del assessment (M7): documento JSON versionado, estable y derivado de la composicion de M6.

M7 es el primer hito de producto: este modulo traduce la semantica ya compuesta (`AssessmentComposition`) a un documento que se serializa a JSON
y se renderiza a Markdown (`render_md`) SIN reinterpretar hechos crudos. Principios (decisiones del owner):

  * El motor sigue siendo A NIVEL DE OCURRENCIA; la PRESENTACION agrupa por `ObservationId`/`ReviewId` con `occurrence_count`, conservando cada
    ocurrencia, sus artefactos afectados, los ids de hechos de evidencia y valores normalizados.
  * `Finding.detail` es una representacion INTERNA: nunca se serializa a ciegas. Cada senal tiene un mapa EXPLICITO de claves publicas
    (`_VALUE_MAP`); lo que no esta mapeado no sale (test de fuga).
  * Las razones internas de M6 (`NO_REMOTE_SOURCE_IN_SCOPE`, causas, ...) NO son API: se traducen con un REGISTRO publico explicito, versionado y
    testeado (`PUBLIC_REASONS`, `reason_code` + `reason_summary`).
  * COBERTURA (completitud de evidencia requerida), CONFIANZA (fiabilidad epistemica de lo evaluado) y CONTEXTO REQUERIDO (preguntas humanas
    abiertas) son ejes INDEPENDIENTES y viajan en bloques separados. No hay score numerico, banda ni juicio de calidad.
  * Ausencia evaluada != ausencia no establecida: `rule_results` conserva ambas distinciones.

El documento es JSON puro con claves sin vocabulario prohibido; `canonical_assessment_json` es determinista (misma entrada -> mismos bytes).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Sequence

from . import model as M
from .assessability import (
    ASSESSABILITY_RULESET, CONFIDENCE_CUTS, CONFIDENCE_RULESET, SURFACE_REGISTRY, AssessmentComposition, ConfidenceAxis, SurfaceAssessment, SurfaceKind,
    SurfaceRole,
)
from .identity import IDENTITY_RULESET
from .model import Applicability, Assessability, Cause, Fact, SignalKind
from .signals import SIGNAL_REGISTRY, SIGNALS_RULESET, FactIndex, Finding

ASSESSMENT_SCHEMA_VERSION = "modeling_assessment.assessment/v1"
REASON_REGISTRY_VERSION = "reason-codes/v1"
FINDING_EVIDENCE_VERSION = "finding-evidence/v1"

ASSESSMENT_STATES = ("ASSESSED", "NOT_APPLICABLE", "INDETERMINATE")


# ═══════════════════════════ razones PUBLICAS (registro explicito) ═══════════════════════════
PUBLIC_REASONS: dict[str, str] = {
    # NOT_APPLICABLE
    "NO_TERMINAL_ANALYTIC_MODEL": "El cliente no tiene un modelo analítico terminal, por lo que no hay una unidad de evaluación.",
    "NO_REMOTE_SOURCE_IN_SCOPE": "No hay ningún artefacto con origen remoto en el alcance evaluado, por lo que esta evidencia no tiene nada que describir.",
    "NO_UNITS_IN_SCOPE": "No hay unidades de este tipo en el alcance evaluado.",
    "DECLARED_DIMENSION_ARTIFACT": "Las dimensiones declaradas quedan fuera de esta regla de composición.",
    "SYSTEM_PROVIDED_ARTIFACT": "Los artefactos provistos por la plataforma (sistema) quedan fuera del gobierno de nombres y de fuentes del proyecto.",
    "CONSUMER_IS_DECLARED_DIMENSION": "El artefacto consumidor se declara dimensión; el patrón de capa de paso no aplica a las dimensiones.",
    "NAME_DOES_NOT_ENCODE_LAYER": "El nombre del artefacto no codifica una capa, por lo que no puede evaluarse la divergencia entre nombre y posición.",
    "EDGE_OUTSIDE_RULE_POPULATION": "Esta arista no pertenece a la población de la regla: solo se evalúan transformaciones internas entre artefactos modelados.",
    "NO_PEER_ANALYTIC_MODEL": "No hay otro modelo analítico terminal en el universo con el cual comparar el reúso.",
    "INTERNAL_STATIC_OBJECT": "Objeto interno o estático conocido que no es un contrato de extracción externo.",
    # NOT_ASSESSED / PARTIALLY_ASSESSED (por causa)
    "REQUIRED_EVIDENCE_UNAVAILABLE": "La evidencia requerida para evaluar esto no está disponible.",
    "SOURCE_PROVENANCE_NOT_OBSERVABLE": "El origen de un artefacto de frontera local no puede observarse desde la exportación estándar de Modelado.",
    "PROCESSING_PLANE_BOUNDARY": "La evidencia vive en el plano de Procesamiento, fuera del contrato de Modelado.",
    "NOT_OBSERVABLE_IN_STANDARD_EXPORT": "La exportación estándar de Modelado no expone esta información.",
    "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED": "Los archivos de origen suplementarios necesarios para esta evidencia no formaban parte del conjunto evaluado.",
    "RULE_DOES_NOT_EVALUATE_UNIT": "La regla actual no evalúa este tipo de unidad.",
    "EVIDENCE_NOT_YET_PARSED": "La evidencia existe en la exportación, pero el proceso actual todavía no la interpreta.",
    "EXPERT_ONLY_EVIDENCE": "Esta evidencia solo puede aportarse mediante revisión manual o experta.",
    "CONTEXT_REQUIRED": "Se requiere una respuesta humana, de negocio o de arquitectura.",
    "POOL_LOAD_ERROR": "No se pudo cargar el conjunto de artefactos (colisión o error de carga).",
    "CONTRADICTORY_EVIDENCE": "La evidencia disponible es contradictoria o ambigua.",
}

_CAUSE_CODE: dict[Cause, str] = {
    Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT: "NOT_OBSERVABLE_IN_STANDARD_EXPORT",
    Cause.NOT_IN_SNAPSHOT: "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED",
    Cause.CLASSIFIER_RULE_GAP: "RULE_DOES_NOT_EVALUATE_UNIT",
    Cause.PLANE_BOUNDARY: "PROCESSING_PLANE_BOUNDARY",
    Cause.C1_NOT_YET_PARSED: "EVIDENCE_NOT_YET_PARSED",
    Cause.C2_EXPERT_ONLY: "EXPERT_ONLY_EVIDENCE",
    Cause.CONTEXT_REQUIRED: "CONTEXT_REQUIRED",
    Cause.PROVENANCE_UNKNOWN: "SOURCE_PROVENANCE_NOT_OBSERVABLE",
    Cause.COLLISION_OR_LOAD_ERROR: "POOL_LOAD_ERROR",
    Cause.CONTRADICTORY_EVIDENCE: "CONTRADICTORY_EVIDENCE",
}

# razon interna de NOT_APPLICABLE (cadena de M6 o valor de `ApplicabilityReason`) -> codigo publico
_NA_CODE: dict[str, str] = {
    "NO_TERMINAL_ANALYTIC_MODEL": "NO_TERMINAL_ANALYTIC_MODEL",
    "NO_REMOTE_SOURCE_IN_SCOPE": "NO_REMOTE_SOURCE_IN_SCOPE",
    "NO_UNITS_IN_SCOPE": "NO_UNITS_IN_SCOPE",
    "SYSTEM_ARTIFACT": "SYSTEM_PROVIDED_ARTIFACT",
    "CONSUMER_IS_DECLARED_DIMENSION": "CONSUMER_IS_DECLARED_DIMENSION",
    "NAME_DOES_NOT_ENCODE_LAYER": "NAME_DOES_NOT_ENCODE_LAYER",
    "EDGE_OUTSIDE_RULE_POPULATION": "EDGE_OUTSIDE_RULE_POPULATION",
    "DECLARED_DIMENSION": "DECLARED_DIMENSION_ARTIFACT",
    "NO_OTHER_TERMINAL_ANALYTIC_MODEL": "NO_PEER_ANALYTIC_MODEL",
    "INTERNAL_STATIC_OBJECT": "INTERNAL_STATIC_OBJECT",
}


class UnmappedReasonError(KeyError):
    """Una razon interna no tiene codigo publico: el registro esta incompleto (falla ruidosa, nunca se filtra la cadena interna)."""


def public_reason(state: Assessability, cause: Cause | None, na_reason: str | None) -> dict | None:
    """`{reason_code, reason_summary}` para NOT_APPLICABLE / NOT_ASSESSED / PARTIALLY_ASSESSED; None si ASSESSED."""
    if state is Assessability.ASSESSED:
        return None
    if state is Assessability.NOT_APPLICABLE:
        if na_reason is None:
            code = "NO_UNITS_IN_SCOPE"
        elif na_reason in _NA_CODE:
            code = _NA_CODE[na_reason]
        else:
            raise UnmappedReasonError(na_reason)
    elif cause is None:
        code = "REQUIRED_EVIDENCE_UNAVAILABLE"
    elif cause in _CAUSE_CODE:
        code = _CAUSE_CODE[cause]
    else:
        raise UnmappedReasonError(cause.value)
    return {"reason_code": code, "reason_summary": PUBLIC_REASONS[code]}


# ═══════════════════════════ evidencia NORMALIZADA de los hallazgos ═══════════════════════════
def _json_text(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# signal_id -> ((clave_publica, clave_interna_de_Finding.detail, transformacion|None), ...). Lo NO mapeado no se serializa.
_VALUE_MAP: dict[str, tuple[tuple[str, str, Callable[[Any], Any] | None], ...]] = {
    "DIRECT_RAW_TABLE_ACCESS": (
        ("remote_entity", "remote_entity", None), ("remote_connection", "remote_connection", None), ("artifact_label", "artifact_label", None),
        ("remote_stem", "remote_stem", None), ("identity_provenance", "identity", None),
        ("reaches_terminal_by_strict_lineage", "reaches_terminal_by_strict_lineage", None), ("lineage_distance", "lineage_distance", None),
        ("transitive_lineage_dependents", "transitive_lineage_dependents_in_scope", None), ("source_filter", "source_filter", None)),
    "SOURCE_FILTER_PRESENT": (("filter_expression_json", "where", _json_text),),
    "PASS_THROUGH_REVIEW": (
        ("edge", "edge", None), ("field_count", "field_count", None), ("consumer_declared_pattern", "consumer_declared_pattern", None),
        ("provider_layer_by_inference", "provider_inferred_layer", None), ("consumer_layer_by_inference", "consumer_inferred_layer", None)),
    "CROSS_LAYER_REDERIVATION": (
        ("provider", "provider", None), ("consumer", "consumer", None), ("fields", "fields", None), ("lineage_distance", "lineage_distance", None),
        ("adjacent", "adjacent", None)),
    "ROLE_LAYER_DIVERGENCE": (
        ("artifact", "artifact", None), ("layer_encoded_in_name", "name_encoded_layer", None), ("is_boundary_leaf", "is_boundary_leaf", None),
        ("layer_of_declared_role", "declared_role_layer", None), ("divergence_kinds", "divergence_kinds", None)),
    "DUPLICATED_REMOTE_IMPORT": (
        ("remote_entity", "remote_entity", None), ("local_artifacts", "local_artifacts", None), ("in_closure", "in_closure", None),
        ("spans_outside_closure", "spans_outside_closure", None), ("duplicated_inside_closure", "duplicated_inside_closure", None)),
    "LAYER_BOUNDARY_REVIEW": (
        ("source", "source", None), ("lineage_consumers", "lineage_consumers", None), ("association_consumer_count", "association_consumer_count", None)),
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": (
        ("artifact", "artifact", None), ("fan_in", "fan_in", None), ("boundary_providers", "boundary_providers", None),
        ("lineage_consumer_count", "lineage_consumer_count", None), ("declared_pattern", "declared_pattern", None),
        ("provisional_minimum_fan_in", "minimum_fan_in", None)),
    "FIELD_VOCABULARY_MIX": (
        ("label", "label", None), ("technical_names", "technical_names", None), ("distinct_forms", "distinct_forms", None),
        ("forms_by_name", "forms_by_name", None), ("evidence_tiers", "evidence_tiers", None), ("co_declared_in", "co_declared_in", None),
        ("terminal_model_fields", "terminal_am_fields", None), ("field_occurrences", "occurrence_count", None)),
    "OPAQUE_FIELD_NAME": (("field", "field", None), ("label", "label", None), ("exposed_in", "exposed_in", None)),
    "LABEL_NOT_ENRICHING": (("artifact", "artifact", None), ("field_count", "n_fields", None), ("fields_with_label_equal_to_name", "label_equals_field", None)),
    "AM_SEMANTIC_UNDERUSE_REVIEW": (
        ("association_count", "n_associations", None), ("measure_count", "n_measures", None), ("declared_dimension_count", "n_declared_dimensions", None),
        ("label_coverage", "label_coverage", None)),
}

PUBLIC_VALUE_KEYS: dict[str, tuple[str, ...]] = {sid: tuple(k for k, _, _ in spec) for sid, spec in _VALUE_MAP.items()}


def normalize_finding_values(signal_id: str, detail: dict) -> dict:
    """Valores publicos de UNA ocurrencia: solo las claves mapeadas de `Finding.detail` (nunca un passthrough)."""
    out: dict[str, Any] = {}
    for public_key, internal_key, transform in _VALUE_MAP[signal_id]:
        if internal_key in detail:
            v = detail[internal_key]
            out[public_key] = transform(v) if transform else v
    return out


# ═══════════════════════════ textos PUBLICOS (espanol) ═══════════════════════════
# Contrato: los IDENTIFICADORES (signal ids, claves JSON, reason_code, enums) son tecnicos y estables; todo texto de PRESENTACION es espanol.
# Los textos del registro M5 (`interpretation_limits`, `legitimate_reasons`, `missing_context`) estan en ingles y NO se reescriben alli (M5 esta
# cerrado y sus tests los fijan): la capa publica los traduce POSICION POR POSICION y un test exige que la cantidad coincida con el registro.
DISPLAY: dict[str, tuple[str, str]] = {
    "DIRECT_RAW_TABLE_ACCESS": ("Acceso directo a tabla física", "Una tabla física remota se importa directamente en el alcance evaluado."),
    "SOURCE_FILTER_PRESENT": ("Filtro de origen presente", "La definición de la fuente remota lleva un filtro (descriptivo; no es una virtud ni un defecto)."),
    "PASS_THROUGH_REVIEW": ("Capa de paso", "Una transformación interna modelada expone exactamente el esquema de su único proveedor y no introduce nada."),
    "CROSS_LAYER_REDERIVATION": ("Campo derivado de nuevo en otra capa", "El mismo campo se deriva en un artefacto y otra vez en un ancestro del linaje."),
    "ROLE_LAYER_DIVERGENCE": ("La capa del nombre difiere de la posición estructural o del rol declarado",
                              "La capa codificada en el nombre del artefacto no coincide con el lugar que ocupa ni con lo que declara."),
    "DUPLICATED_REMOTE_IMPORT": ("Entidad remota importada más de una vez", "La misma entidad remota existe bajo varias identidades locales."),
    "LAYER_BOUNDARY_REVIEW": ("Fuente remota consumida directamente por la capa 3 o superior",
                              "Todos los consumidores de una fuente remota ya están en la tercera capa o en una superior."),
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": ("Ensamble central plano", "Un ensamble con muchos proveedores no declara asociaciones, medidas ni dimensiones (detector provisional)."),
    "FIELD_VOCABULARY_MIX": ("Etiqueta compartida entre vocabularios técnicos distintos",
                             "Nombres técnicos de formas distintas comparten una misma etiqueta de negocio, declarados juntos o expuestos por el modelo analítico."),
    "OPAQUE_FIELD_NAME": ("Nombre con prefijo propio no legible desde su etiqueta",
                          "Un nombre técnico de espacio de nombres propio tiene una etiqueta que no comparte ninguna palabra con él."),
    "LABEL_NOT_ENRICHING": ("Etiquetas que repiten los nombres técnicos", "Todos los campos expuestos del artefacto tienen una etiqueta idéntica a su nombre técnico."),
    "AM_SEMANTIC_UNDERUSE_REVIEW": ("Modelo analítico sin asociaciones", "El modelo analítico terminal no declara asociaciones."),
}

# Orden de PRESENTACION (versionado; NO es puntaje, severidad ni penalidad: solo ordena el informe). Menor numero = antes. Registro unico, sin resultados de cliente:
# observaciones automaticas primero; luego revisiones de arquitectura/ingesta; luego revisiones de nombres/contexto; las senales descriptivas al final.
PRESENTATION_ORDER_VERSION = "presentation-order/v1"
PRESENTATION_PRIORITY: dict[str, int] = {
    "DIRECT_RAW_TABLE_ACCESS": 10,
    "CROSS_LAYER_REDERIVATION": 20, "FLAT_CENTRAL_ASSEMBLY_REVIEW": 30, "DUPLICATED_REMOTE_IMPORT": 40, "ROLE_LAYER_DIVERGENCE": 50,
    "LAYER_BOUNDARY_REVIEW": 60, "PASS_THROUGH_REVIEW": 70, "AM_SEMANTIC_UNDERUSE_REVIEW": 80,
    "FIELD_VOCABULARY_MIX": 110, "OPAQUE_FIELD_NAME": 120, "LABEL_NOT_ENRICHING": 130,
    "SOURCE_FILTER_PRESENT": 200,
}

# Deduplicacion SEMANTICA de preguntas de contexto: una pregunta generica de convencion NO se muestra si un grupo de hallazgos activo ya la formula
# (mapeo explicito clave -> senales; nunca por similitud de texto). Una clave sin senales es una pregunta independiente.
CONTEXT_QUESTION_COVERED_BY: dict[str, tuple[str, ...]] = {
    "local_convention_for_section_28_deviations": (),
    "business_vocabulary_expected_by_project": ("FIELD_VOCABULARY_MIX", "OPAQUE_FIELD_NAME"),
    "label_name_mismatch_intent": ("OPAQUE_FIELD_NAME",),
    "coexisting_name_vocabularies_decision": ("FIELD_VOCABULARY_MIX",),
}

# Que representa UNA ocurrencia de cada senal (la cantidad de ocurrencias y la de artefactos involucrados NO tienen por que coincidir).
OCCURRENCE_UNIT: dict[str, str] = {
    "DIRECT_RAW_TABLE_ACCESS": "artefacto con acceso directo a una tabla física",
    "SOURCE_FILTER_PRESENT": "fuente remota con filtro",
    "PASS_THROUGH_REVIEW": "relación proveedor→consumidor",
    "CROSS_LAYER_REDERIVATION": "par proveedor→consumidor con campos derivados de nuevo",
    "ROLE_LAYER_DIVERGENCE": "artefacto",
    "DUPLICATED_REMOTE_IMPORT": "entidad remota repetida",
    "LAYER_BOUNDARY_REVIEW": "relación fuente→consumidor",
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": "ensamble",
    "FIELD_VOCABULARY_MIX": "grupo de etiqueta compartida",
    "OPAQUE_FIELD_NAME": "campo con su etiqueta",
    "LABEL_NOT_ENRICHING": "artefacto",
    "AM_SEMANTIC_UNDERUSE_REVIEW": "modelo analítico",
}

WHY_SHOWN: dict[str, str] = {
    "DIRECT_RAW_TABLE_ACCESS": "Se muestra por cada tabla física remota importada directamente en el alcance evaluado, sea cual sea su alcance o filtro: el proyecto debe indicar si cada una es una excepción deliberada.",
    "SOURCE_FILTER_PRESENT": "Se muestra como contexto porque la definición de la fuente remota lleva un filtro; no implica revisión.",
    "PASS_THROUGH_REVIEW": "Se muestra porque una transformación interna modelada (de un artefacto modelado a otro) tiene un único proveedor, no es una dimensión declarada y expone exactamente el esquema de su proveedor sin introducir campos derivados, asociaciones ni medidas.",
    "CROSS_LAYER_REDERIVATION": "Se muestra porque el mismo campo se deriva en un artefacto y otra vez en un ancestro del linaje.",
    "ROLE_LAYER_DIVERGENCE": "Se muestra porque la capa codificada en el nombre del artefacto difiere de su posición estructural o de la capa de su rol declarado.",
    "DUPLICATED_REMOTE_IMPORT": "Se muestra porque una entidad remota se importa bajo más de una identidad local y al menos una de ellas está dentro del alcance evaluado.",
    "LAYER_BOUNDARY_REVIEW": "Se muestra porque todos los consumidores de linaje de esta fuente remota están en la capa inferida 3 o superior.",
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": "Se muestra porque un ensamble con muchos proveedores no declara asociaciones, medidas ni dimensiones (el corte de proveedores es un parámetro provisional del detector).",
    "FIELD_VOCABULARY_MIX": "Se muestra porque nombres técnicos de formas distintas comparten una misma etiqueta de negocio y aparecen juntos en un artefacto o llegan al modelo analítico terminal.",
    "OPAQUE_FIELD_NAME": "Se muestra porque un nombre técnico con prefijo propio tiene una etiqueta que no comparte ninguna palabra con él.",
    "LABEL_NOT_ENRICHING": "Se muestra porque todos los campos expuestos del artefacto tienen una etiqueta idéntica a su nombre técnico.",
    "AM_SEMANTIC_UNDERUSE_REVIEW": "Se muestra porque el modelo analítico terminal no declara asociaciones.",
}

SIGNAL_QUESTIONS: dict[str, str] = {
    "DIRECT_RAW_TABLE_ACCESS": "¿Es el acceso directo a una tabla física remota una excepción metodológica justificada para cada artefacto afectado, o una desviación?",
    "PASS_THROUGH_REVIEW": "¿Cuál es el rol funcional de la capa consumidora (frontera de servicio o de producto, contrato estable, descomposición técnica, compatibilidad)?",
    "CROSS_LAYER_REDERIVATION": "¿Se recalcula el campo intencionalmente en cada capa y qué capa es dueña de su definición?",
    "ROLE_LAYER_DIVERGENCE": "¿A qué capa debe servir cada uno de estos artefactos en este proyecto?",
    "DUPLICATED_REMOTE_IMPORT": "¿Por qué se importa la misma entidad remota bajo varias identidades locales?",
    "LAYER_BOUNDARY_REVIEW": "¿Está intencionalmente ausente la armonización en estas fuentes remotas?",
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": "¿Se declara intencionalmente el contrato semántico aguas abajo de este ensamble?",
    "FIELD_VOCABULARY_MIX": "¿Los nombres técnicos que comparten una etiqueta designan el mismo concepto de negocio y se espera unificar el vocabulario?",
    "OPAQUE_FIELD_NAME": "¿Debe el nombre técnico ser autoexplicativo, o es la etiqueta el término de negocio gobernado?",
    "LABEL_NOT_ENRICHING": "¿Deben estos artefactos exponer etiquetas de negocio?",
    "AM_SEMANTIC_UNDERUSE_REVIEW": "¿Es el modelo analítico plano a propósito, sin asociaciones?",
}

CONTEXT_NEEDED: dict[str, str] = {
    "DIRECT_RAW_TABLE_ACCESS": "Si el acceso directo a la tabla física es una excepción metodológica justificada para este artefacto o una desviación.",
    "PASS_THROUGH_REVIEW": "Rol funcional de la capa consumidora (frontera de servicio o de producto, contrato estable, descomposición técnica, compatibilidad).",
    "CROSS_LAYER_REDERIVATION": "Si el campo se recalcula intencionalmente en cada capa y qué capa es dueña de su definición.",
    "ROLE_LAYER_DIVERGENCE": "A qué capa debe servir el artefacto en este proyecto.",
    "DUPLICATED_REMOTE_IMPORT": "Motivo de importar la misma entidad remota bajo varias identidades locales.",
    "LAYER_BOUNDARY_REVIEW": "Si la armonización está ausente intencionalmente para esta fuente.",
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": "Si el contrato semántico se declara intencionalmente aguas abajo de este ensamble.",
    "FIELD_VOCABULARY_MIX": "Si los nombres técnicos designan el mismo concepto de negocio y si se espera unificar el vocabulario.",
    "OPAQUE_FIELD_NAME": "Si el nombre técnico debe ser autoexplicativo o si la etiqueta es el término gobernado.",
    "LABEL_NOT_ENRICHING": "Si este artefacto debe exponer etiquetas de negocio.",
    "AM_SEMANTIC_UNDERUSE_REVIEW": "Si el modelo analítico es plano intencionalmente.",
}

# Traduccion POSICION POR POSICION de `SignalSpec.interpretation_limits` (cantidad verificada contra el registro por un test).
INTERPRETATION_LIMITS: dict[str, tuple[str, ...]] = {
    "DIRECT_RAW_TABLE_ACCESS": (
        "un acceso directo a una tabla física no es, por sí solo, una arquitectura inválida",
        "no se asigna severidad: alcance, dependientes, filtro y etiqueta son contexto para la revisión, no factores de decisión",
        "la resolución queda abierta (requiere contexto) hasta conocer el contexto del proyecto"),
    "SOURCE_FILTER_PRESENT": (
        "un filtro de origen no es una virtud ni un defecto",
        "el cargador estándar no consume remote.query: la evidencia es una lectura suplementaria (clase C1)"),
    "PASS_THROUGH_REVIEW": (
        "una capa de paso no es un defecto por sí sola",
        "una capa de paso casi pura puede ser metodológicamente aceptable",
        "la lectura depende del rol funcional de la capa",
        "la ausencia de hallazgo NO significa que el patrón esté ausente: las transformaciones internas sin consulta visible o sin descriptores no son evaluables; las aristas desde la fuente hacia el primer consumidor y del último proveedor hacia el modelo analítico terminal quedan fuera de la población de esta regla"),
    "CROSS_LAYER_REDERIVATION": (
        "no es un defecto automático (es una revisión de estabilidad del contrato)",
        "no indica cuál de las derivaciones es la correcta",
        "la semántica de las fórmulas no es observable en la exportación estándar (clase C2)"),
    "ROLE_LAYER_DIVERGENCE": (
        "no es por sí solo un defecto de nombres",
        "la capa codificada en el nombre es una convención del proyecto, no una responsabilidad demostrada",
        "no se declara incorrecto ningún rol"),
    "DUPLICATED_REMOTE_IMPORT": (
        "no es un defecto automático",
        "varias identidades pueden ser legítimas",
        "el alcance (dentro del cierre o en el universo del cliente) es contexto, no severidad"),
    "LAYER_BOUNDARY_REVIEW": (
        "cada ocurrencia es un elemento a revisar, no un conteo de defectos",
        "la capa inferida no es una responsabilidad demostrada",
        "un atajo directo puede ser legítimo"),
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": (
        "un ensamble central plano no es necesariamente un mal diseño",
        "la cantidad de proveedores por sí sola no es evidencia de un defecto",
        "el mínimo de proveedores es un parámetro provisional del detector, pendiente de validación",
        "el mínimo de proveedores es lógica de detección provisional y debe validarse en la repetición de validación en paralelo antes de tratarse como una regla de revisión estable"),
    "FIELD_VOCABULARY_MIX": (
        "una etiqueta compartida es evidencia, no prueba, de que los nombres designan el mismo concepto de negocio",
        "no es un hallazgo de sinonimia, inconsistencia ni duplicación",
        "las series numeradas de columnas están excluidas",
        "las etiquetas genéricas pueden seguir generando falsos positivos de revisión: no hay evidencia semántica que separe un sinónimo de una etiqueta reutilizada"),
    "OPAQUE_FIELD_NAME": (
        "la legibilidad de un nombre no se evalúa sin un diccionario: solo se detecta este patrón estrecho y mecánico",
        "la etiqueta puede ser el término de negocio autorizado",
        "no es un juicio sobre la etiqueta",
        "limitado intencionalmente por la evidencia: no existe un diccionario de vocabulario empresarial, por lo que no se detectan nombres opacos sin prefijo de espacio de nombres propio"),
    "LABEL_NOT_ENRICHING": (
        "una etiqueta igual al nombre técnico no es un defecto por sí sola",
        "aplica solo cuando TODOS los campos expuestos repiten su nombre"),
    "AM_SEMANTIC_UNDERUSE_REVIEW": (
        "un modelo analítico sin asociaciones no es deficiente de manera universal",
        "los textos y jerarquías no son observables más allá de las asociaciones declaradas"),
}

# Traduccion POSICION POR POSICION de `SignalSpec.legitimate_reasons`.
LEGITIMATE_REASONS: dict[str, tuple[str, ...]] = {
    "DIRECT_RAW_TABLE_ACCESS": (),
    "SOURCE_FILTER_PRESENT": (),
    "PASS_THROUGH_REVIEW": ("frontera de servicio", "frontera de producto", "contrato semántico estable", "descomposición técnica", "capa de compatibilidad"),
    "CROSS_LAYER_REDERIVATION": (
        "una conversión de unidad, moneda o tipo reaplicada deliberadamente", "un nivel de agregación distinto por capa",
        "un recálculo requerido por una unión o filtro posterior", "un endurecimiento intencional del contrato en una frontera"),
    "ROLE_LAYER_DIVERGENCE": (
        "objeto intermedio materializado con el nombre de una capa posterior", "el dígito de capa responde a una convención del proyecto",
        "el patrón declarado refleja valores por defecto de la herramienta"),
    "DUPLICATED_REMOTE_IMPORT": (
        "fuentes ECC y S/4 en paralelo durante una migración", "filtros o proyecciones distintos por consumidor",
        "necesidades de seguridad o de actualización separadas", "copias mantenidas por dominio de negocio"),
    "LAYER_BOUNDARY_REVIEW": (
        "fuente ya armonizada (por ejemplo, una vista CDS gobernada)", "tabla de referencia o de textos consumida una sola vez",
        "armonización realizada intencionalmente en la vista consumidora"),
    "FLAT_CENTRAL_ASSEMBLY_REVIEW": ("vista técnica de integración cuya semántica vive aguas abajo", "contrato declarado en la capa consumidora"),
    "FIELD_VOCABULARY_MIX": (
        "vocabularios de origen distintos conservados tal como se entregan", "una etiqueta genérica reutilizada para conceptos distintos",
        "unificación de vocabulario planificada aguas abajo"),
    "OPAQUE_FIELD_NAME": ("nombre heredado del sistema de origen", "sigla interna establecida", "la etiqueta es el término de negocio gobernado"),
    "LABEL_NOT_ENRICHING": ("estructura técnica intermedia no expuesta a consumidores", "etiquetas diferidas intencionalmente a la capa consumidora"),
    "AM_SEMANTIC_UNDERUSE_REVIEW": (
        "consumido por una herramienta que aporta sus propias dimensiones", "extracto plano para reportes por diseño", "semántica modelada en una capa posterior"),
}

CONTEXT_QUESTION_TEXT: dict[str, str] = {
    "local_convention_for_section_28_deviations": "¿Es la convención local de nombres intencionalmente distinta de la convención vigente de la sección 28?",
    "business_vocabulary_expected_by_project": "¿Es el vocabulario de negocio propio (prefijo Z) el que espera el proyecto?",
    "label_name_mismatch_intent": "¿Es intencional que el nombre técnico difiera de su etiqueta?",
    "coexisting_name_vocabularies_decision": "¿Es deliberada la coexistencia de varios vocabularios técnicos para una misma etiqueta de negocio?",
}

# Titulo humano de cada superficie de evidencia (las de regla se titulan con el nombre de la senal).
SURFACE_TITLES: dict[str, str] = {
    "source.contract": "Contrato de los artefactos con origen remoto", "source.keys": "Claves declaradas de las fuentes remotas",
    "source.annotation_inventory": "Anotaciones de las tablas remotas", "source.query_filter": "Filtros de las fuentes remotas",
    "source.downstream_reach": "Alcance aguas abajo de las fuentes remotas", "source.identity_continuity": "Continuidad entre identidad local y origen remoto",
    "source.family_distribution": "Distribución por familia de fuente", "source.connection_consistency": "Consistencia de conexiones remotas",
    "source.replication_mode": "Modo de replicación (federado o replicado)", "source.leaf_contract_status": "Contrato de las hojas locales sin origen remoto",
    "ingestion.boundary_artifacts": "Artefactos de frontera", "ingestion.first_consumers": "Primeros consumidores de cada hoja",
    "ingestion.field_retention": "Retención de campos de la hoja al primer consumidor", "ingestion.duplicate_remote_imports": "Importaciones remotas repetidas",
    "ingestion.inferred_layer_vs_lineage_depth": "Capa inferida frente a profundidad de linaje",
    "ingestion.dataflow_boundary": "Referencias del plano de Procesamiento (frontera)", "ingestion.processing_context_inventory": "Inventario de contexto de Procesamiento",
    "ingestion.processing_reference": "Referencias a flujos de Procesamiento", "am.contract_inventory": "Contrato declarado del modelo analítico",
    "am.closure_composition": "Composición del cierre semántico (linaje estricto frente a asociación)",
    "am.association_targets": "Destinos de asociación del modelo analítico", "am.contract_delta": "Diferencias del modelo analítico frente a sus proveedores",
    "am.numeric_attributes_not_declared_as_measures": "Atributos numéricos no declarados como medidas", "propagation.derived_fields": "Campos derivados por artefacto",
    "propagation.contract_delta": "Diferencias de contrato por paso de linaje", "propagation.contract_occurrences": "Ocurrencias del contrato del modelo a lo largo del cierre",
    "propagation.rederivation": "Campos derivados de nuevo en ancestros", "propagation.derivation_semantics": "Semántica exacta de las fórmulas de derivación",
    "harmonization.dimension_artifacts": "Dimensiones declaradas del cierre", "harmonization.composition_artifacts": "Artefactos de composición",
    "harmonization.reuse_context": "Definición de los alcances de reúso", "harmonization.local_chain_reuse": "Reúso dentro de la cadena local",
    "harmonization.cross_am_reuse": "Reúso entre modelos analíticos", "naming.artifact_name_contract": "Cumplimiento de la sección 28 por artefacto",
    "naming.field_name_contract": "Etiquetas por campo", "naming.field_name_forms": "Formas mecánicas de los nombres de campo",
    "naming.field_name_continuity": "Continuidad de nombres de campo en el linaje", "naming.artifact_name_populations": "Poblaciones de nombres de artefactos",
    "naming.field_vocabulary_summary": "Resumen del vocabulario de campos",
    "naming.same_normalized_label_multiple_technical_names": "Etiquetas compartidas por varios nombres técnicos",
    "naming.convention_context": "Contexto de la convención de nombres",
}


def public_limits(spec) -> list[str]:
    """`interpretation_limits` de una senal en su redaccion publica (espanol)."""
    return list(INTERPRETATION_LIMITS[spec.signal_id])


def public_legitimate_reasons(spec) -> list[str]:
    return list(LEGITIMATE_REASONS[spec.signal_id])


# ═══════════════════════════ construccion del documento ═══════════════════════════
def _ratio(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def _surface_unit_rows(s: SurfaceAssessment) -> list[dict]:
    rows = []
    for u in s.units:
        if u.assessability in (Assessability.ASSESSED,):
            continue
        r = public_reason(u.assessability, u.cause, u.not_applicable_reason)
        rows.append({"subject": u.subject, "state": u.assessability.value, **r})
    return rows


def _surface_json(s: SurfaceAssessment) -> dict:
    r = public_reason(s.assessability, s.cause, s.not_applicable_reason)
    c = s.counts
    out = {"surface_id": s.surface_id, "area": s.area.value, "kind": s.kind.value, "role": s.role.value, "assessability": s.assessability.value,
           "reason": r, "unit_counts": {"assessed": c.assessed, "partially_assessed": c.partially_assessed, "not_assessed": c.not_assessed,
                                        "indeterminate": c.indeterminate, "not_applicable": c.not_applicable},
           "open_evidence_gap_classes": [g.value for g in s.open_gap_classes]}
    if s.kind is SurfaceKind.RULE_EVALUATION:
        out["rule_result"] = s.result.value
        out["finding_count"] = s.finding_count
    rows = _surface_unit_rows(s)
    if rows:
        out["units_not_fully_assessed"] = [x for x in rows if x["state"] != "NOT_APPLICABLE"]
        out["units_not_applicable_count"] = sum(1 for x in rows if x["state"] == "NOT_APPLICABLE")
    return out


def _group_findings(comp: AssessmentComposition) -> dict[str, list[Finding]]:
    by: dict[str, list[Finding]] = {}
    for f in comp.findings:
        by.setdefault(f.signal_id, []).append(f)
    return by


def _finding_groups(comp: AssessmentComposition) -> list[dict]:
    specs = {s.signal_id: s for s in SIGNAL_REGISTRY}
    rule_by_signal = {sp.signal_id: sp.surface_id for sp in SURFACE_REGISTRY if sp.signal_id}
    surfaces = {s.surface_id: s for s in comp.surfaces}
    groups = []
    for sid, findings in _group_findings(comp).items():
        spec = specs[sid]
        title, expl = DISPLAY[sid]
        occurrences = []
        for i, f in enumerate(findings, start=1):
            occurrences.append({"occurrence_id": f"{sid}-{i:04d}", "affected_artifacts": list(f.subjects), "evidence_facts": list(f.fact_refs),
                                "values": normalize_finding_values(sid, f.detail)})
        surf = surfaces.get(rule_by_signal.get(sid, ""))
        g = {"id": sid, "kind": spec.kind.value, "area": spec.area.value, "title": title, "explanation": expl, "why_shown": WHY_SHOWN[sid],
             "presentation_priority": PRESENTATION_PRIORITY[sid], "occurrence_unit": OCCURRENCE_UNIT[sid], "occurrence_count": len(occurrences),
             "affected_artifacts": sorted({a for o in occurrences for a in o["affected_artifacts"]}),
             "interpretation_limits": public_limits(spec), "occurrences": occurrences}
        if spec.kind is SignalKind.OBSERVATION:
            g["resolution"] = "OPEN_CONTEXT_REQUIRED"
        if spec.kind is not SignalKind.DESCRIPTIVE:
            g["context_required"] = {"context_key": spec.context_question_key or sid.lower(), "question": SIGNAL_QUESTIONS[sid], "context_needed": CONTEXT_NEEDED[sid]}
            g["legitimate_reasons"] = public_legitimate_reasons(spec)
        if surf is not None:
            g["evidence_state"] = {"assessability": surf.assessability.value, "reason": public_reason(surf.assessability, surf.cause, surf.not_applicable_reason)}
        groups.append(g)
    order = {s.signal_id: i for i, s in enumerate(SIGNAL_REGISTRY)}
    groups.sort(key=lambda g: (g["presentation_priority"], order[g["id"]]))
    return groups


def _rule_results(comp: AssessmentComposition) -> list[dict]:
    sig = {sp.surface_id: sp.signal_id for sp in SURFACE_REGISTRY if sp.signal_id}
    out = []
    for s in comp.surfaces:
        if s.kind is not SurfaceKind.RULE_EVALUATION:
            continue
        out.append({"rule": sig[s.surface_id], "title": DISPLAY[sig[s.surface_id]][0], "area": s.area.value, "result": s.result.value, "assessability": s.assessability.value,
                    "finding_count": s.finding_count, "reason": public_reason(s.assessability, s.cause, s.not_applicable_reason)})
    return out


_COMPONENT_LIMITATION = {
    "non_heuristic_identity": "parte de las identidades de artefacto se basa en una regla heurística de nombres y no en metadatos observados",
    "resolved_provenance": "la procedencia de parte de los artefactos evaluados no está resuelta",
    "complete_rule_inputs": "parte de las conclusiones de las reglas se alcanzó con insumos evaluados de forma incompleta",
}

_COMPONENT_TEXT = {
    "non_heuristic_identity": "Identidades de artefacto que no dependen de una regla heurística",
    "resolved_provenance": "Artefactos con procedencia resuelta",
    "complete_rule_inputs": "Conclusiones de reglas alcanzadas con insumos completamente evaluados",
}

# Por que cada diagnostico NO es insumo de la confianza (un diagnostico nuevo sin texto falla ruidosamente: obliga a decidir).
_EXCLUDED_WHY = {
    "directly_observed_surfaces": "diagnóstico: la clase B es una derivación determinista sobre metadatos observables, no una marca de fiabilidad",
    "strict_coverage": "pertenece a la COBERTURA; nunca es insumo de la confianza",
    "inclusive_coverage": "pertenece a la COBERTURA; nunca es insumo de la confianza",
    "open_evidence_gap_surfaces": "es completitud (cobertura), no fiabilidad",
    "processing_evidence_attached": "la evidencia complementaria de otro plano no premia ni castiga globalmente la confianza",
    "open_context_questions": "el contexto sin resolver no es evidencia poco fiable; se reporta como su propio eje",
}

CONFIDENCE_MEANING = ("Fiabilidad epistémica de la evidencia que SÍ se evaluó. No es una afirmación sobre la calidad del modelo y es independiente "
                      "de la cobertura y de las preguntas de contexto aún abiertas.")
COVERAGE_MEANING = "Completitud de la evidencia REQUERIDA que efectivamente pudo evaluarse. No es una afirmación sobre la calidad."
NOT_APPLICABLE_CONFIDENCE_MEANING = "No hay evidencia evaluada que calificar."


def _confidence_json(comp: AssessmentComposition) -> dict:
    live = [f for f in comp.confidence_factors if f.contributes_to_confidence and f.level is not None]
    components = [{"id": f.name, "label": _COMPONENT_TEXT[f.name], "value": _ratio(f.value), "level": f.level.value,
                   "summary": f"proporción confiable: {_ratio(f.value)}"} for f in live]
    limiting = None
    if live:
        order = [M.ConfidenceLevel.LOW, M.ConfidenceLevel.MEDIUM, M.ConfidenceLevel.HIGH]
        worst = min(live, key=lambda f: (order.index(f.level), f.value if f.value is not None else -1.0, f.name))      # empate de nivel: gana el valor MAS BAJO
        if worst.level is not M.ConfidenceLevel.HIGH:
            limiting = {"component": worst.name, "summary": _COMPONENT_LIMITATION[worst.name]}
    excluded = [{"id": f.name, "axis": f.axis.value, "value": (_ratio(f.value) if isinstance(f.value, float) else f.value),
                 "why_excluded": _EXCLUDED_WHY[f.name]} for f in comp.confidence_factors
                if not f.contributes_to_confidence and f.axis is not ConfidenceAxis.CONTEXTUALITY]
    excluded.append({"id": "open_context_questions", "axis": ConfidenceAxis.CONTEXTUALITY.value, "value": None, "reported_in": "summary.context_required",
                     "why_excluded": _EXCLUDED_WHY["open_context_questions"]})
    return {"level": comp.confidence.level.value, "driver": comp.confidence.drivers[0].name.value, "meaning": CONFIDENCE_MEANING,
            "main_limitation": limiting, "components": components,
            "provisional_cuts": {"reliable_share_high_medium": list(CONFIDENCE_CUTS["reliable_share"]), "status": "PROVISIONAL_VALIDATE_IN_M8"},
            "excluded_from_confidence": excluded}


def _context_required(comp: AssessmentComposition, groups: list[dict], idx: FactIndex) -> dict:
    questions = []
    for g in groups:
        if "context_required" in g:
            questions.append({"id": g["id"], "origin": g["kind"], "question": g["context_required"]["question"], "occurrence_count": g["occurrence_count"]})
    active = {g["id"] for g in groups if "context_required" in g}
    deduplicated = []
    conv = idx.get("naming.convention_context", "closure")
    if conv is not None and conv.value:
        for key in conv.value["open_context_questions"]:
            covered = [sid for sid in CONTEXT_QUESTION_COVERED_BY[key] if sid in active]
            if covered:
                deduplicated.append({"id": key, "covered_by": covered[0]})              # ya la formula un grupo de hallazgos activo
            else:
                questions.append({"id": key, "origin": "CONTEXT_ONLY", "question": CONTEXT_QUESTION_TEXT[key], "occurrence_count": None})
    # UNIDADES DISTINTAS: preguntas abiertas (tipos de pregunta) != ocurrencias de hallazgos que respaldan a algunas de ellas.
    backed = sum(1 for q in questions if q["occurrence_count"] is not None)
    return {"open_questions": len(questions), "questions_backed_by_findings": backed, "questions_without_findings": len(questions) - backed,
            "occurrences": len(comp.observations) + len(comp.review_required), "questions": questions, "deduplicated_questions": deduplicated}


def _kv(rows: Iterable[dict], key: str = "key", count: str = "count") -> list[dict]:
    return [{"name": r[key], "count": r[count]} for r in rows]


def _boundary_breakdown(artifacts: list[dict]) -> list[dict]:
    """Particion EXHAUSTIVA y excluyente de los artefactos de frontera (sistema -> origen remoto -> local): la suma siempre iguala al total."""
    system = sum(1 for a in artifacts if a["identity"] == "SYSTEM" or a["implementation"] == "SYSTEM_PROVIDED")
    remote = sum(1 for a in artifacts if not (a["identity"] == "SYSTEM" or a["implementation"] == "SYSTEM_PROVIDED") and a["has_remote_entity"])
    local = len(artifacts) - system - remote
    return [{"name": "con origen remoto observado", "count": remote}, {"name": "locales: origen no observable", "count": local},
            {"name": "provistos por la plataforma (sistema)", "count": system}]


def _highlights(idx: FactIndex, comp: AssessmentComposition, area: M.Area) -> list[dict]:
    """Datos normalizados y legibles por area: SOLO claves/valores elegidos explicitamente (no hechos crudos)."""
    out: list[dict] = []

    def add(hid: str, label: str, value: Any, breakdown: list[dict] | None = None) -> None:
        if value is not None:
            row = {"id": hid, "label": label, "value": value}
            if breakdown is not None:
                row["breakdown"] = breakdown
            out.append(row)

    am_name = comp.scope.terminal_am
    if area is M.Area.ANALYTIC_MODEL:
        f = idx.usable_get("am.contract_inventory", am_name or "")
        if f is not None:
            s = f.value["summary"]
            add("fields", "Campos expuestos", s["n_fields"])
            add("measures", "Medidas", s["n_measures"])
            add("associations", "Asociaciones declaradas", s["n_associations"])
            add("derived_fields", "Campos con derivación visible", s["n_derived"])
            add("keys", "Claves declaradas", s["n_keys"])
            add("labelled_fields", "Campos con etiqueta de negocio", f.value["label_coverage"]["with_label"])
            add("direct_lineage_providers", "Proveedores directos de linaje", f.value["direct_lineage_providers"])
        t = idx.usable_get("am.association_targets", am_name or "")
        if t is not None:
            add("association_targets", "Destinos de asociación", sorted({a["target"] for a in t.value["associations"] if a["target"]}))
        cc = idx.usable_get("am.closure_composition", am_name or "")
        if cc is not None:                                                                    # contexto de arquitectura: ni bueno ni malo
            v = cc.value
            add("semantic_closure", "Cierre semántico del modelo analítico (artefactos)", v["semantic_closure_size"],
                [{"name": "alcanzables por linaje estricto desde el modelo terminal (incluido el propio modelo)", "count": v["strict_lineage_size"]},
                 {"name": "mediados por asociación (fuera del linaje estricto)", "count": v["association_mediated_size"]}])
        nn = idx.usable_get("am.numeric_attributes_not_declared_as_measures", am_name or "")
        if nn is not None:                                                                    # evidencia descriptiva; no es defecto ni revision
            fields = nn.value["fields"]
            add("numeric_non_measure_attributes", "Atributos numéricos expuestos que no están declarados como medida", len(fields))
            if fields:
                sample = [f"{x['field']} ({x['type']}{' ' + x['length'] if x.get('length') else ''})" for x in fields[:10]]
                add("numeric_non_measure_fields", "Campos numéricos no declarados como medida (muestra)", sample + ([f"… (+{len(fields) - 10})"] if len(fields) > 10 else []))
    elif area is M.Area.PROPAGATION:
        add("lineage_steps_evaluated", "Pasos de linaje con delta de contrato", len(idx.usable("propagation.contract_delta")) + len(idx.usable("am.contract_delta")))
        add("artifacts_with_derived_fields", "Artefactos con campos derivados", sum(1 for f in idx.usable("propagation.derived_fields") if f.value["n_derived"] > 0))
        r = idx.usable_get("propagation.rederivation", "closure")
        if r is not None:
            add("rederived_field_occurrences", "Campos derivados de nuevo en un ancestro del linaje", len(r.value["occurrences"]))
    elif area is M.Area.HARMONIZATION:
        d = idx.usable_get("harmonization.dimension_artifacts", "closure")
        if d is not None:
            add("declared_dimensions", "Dimensiones declaradas en el cierre", d.value["count"])
            add("dimensions_with_own_associations", "Dimensiones con asociaciones propias", d.value["with_own_associations"])
        c = idx.usable_get("harmonization.composition_artifacts", "closure")
        if c is not None:
            add("composition_artifacts", "Artefactos de composición (dos o más proveedores de linaje)", c.value["count"])
        cross = [(f.subject, f.value["cross_am_reuse_count"]) for f in idx.usable("harmonization.cross_am_reuse")]
        if cross:
            add("cross_model_reuse_artifacts", "Artefactos contenidos también en otros modelos analíticos", sum(1 for _, n in cross if n > 0))
            add("most_reused_across_models", "Más reutilizados en otros modelos analíticos",
                [{"name": a, "count": n} for a, n in sorted(cross, key=lambda x: (-x[1], x[0]))[:5] if n > 0])
    elif area is M.Area.INGESTION:
        b = idx.usable_get("ingestion.boundary_artifacts", "closure")
        if b is not None:
            add("boundary_artifacts", "Artefactos de frontera (hojas del cierre)", b.value["count"], _boundary_breakdown(b.value["artifacts"]))
        dup = idx.usable_get("ingestion.duplicate_remote_imports", "closure")
        if dup is not None:
            add("duplicated_remote_entities_in_closure", "Entidades remotas importadas más de una vez dentro del cierre", len(dup.value["in_closure"]))
    elif area is M.Area.NAMING:
        p = idx.usable_get("naming.artifact_name_populations", "closure")
        if p is not None:
            s28 = p.value["section_28"]
            add("section_28_applicable", "Artefactos bajo la convención de nombres de la sección 28", s28["applicable"])
            add("section_28_compliant", "Cumplen la sección 28 vigente", s28["compliant"])
            add("section_28_non_compliant", "No cumplen la sección 28 vigente", s28["non_compliant"])
            add("violation_families", "Familias de incumplimiento (artefactos)", _kv(s28["violation_family_histogram"]))
            add("by_provenance", "Sección 28 según la procedencia del nombre",
                [{"name": r["identity"], "applicable": r["applicable"], "compliant": r.get("compliant"), "non_compliant": r.get("non_compliant")}
                 for r in p.value["by_identity"] if r["applicable"]])
        v = idx.usable_get("naming.field_vocabulary_summary", "closure")
        if v is not None:
            add("field_occurrences", "Ocurrencias de campo en el cierre", v.value["field_occurrences"])
            add("fields_with_label", "Ocurrencias de campo con etiqueta de negocio", v.value["label_present"])
            add("fields_with_label_equal_to_name", "Ocurrencias de campo cuya etiqueta es igual al nombre técnico", v.value["label_equals_field"])
            am_block = v.value["terminal_am"]
            add("terminal_model_name_anchors", "Campos del modelo terminal según su ancla de nombre observable",
                _kv([r for r in am_block["name_anchor_counts"] if r["count"]], "anchor"))
            add("terminal_model_remote_source_anchor", "Campos del modelo terminal con ancla de origen remoto observada", am_block["remote_source_anchor_counts"])
        same = idx.usable_get("naming.same_normalized_label_multiple_technical_names", "closure")
        if same is not None:
            add("shared_label_groups", "Etiquetas de negocio compartidas por varios nombres técnicos", same.value["group_count"])
            add("shared_label_numbered_series", "De ellas, series numeradas de columnas", sum(1 for g in same.value["groups"] if g["is_numbered_series"]))
    elif area is M.Area.SOURCE_EXTRACTION:
        fam = idx.usable_get("source.family_distribution", "closure")
        if fam is not None:
            add("remote_artifacts", "Artefactos con origen remoto", fam.value["remote_artifacts"])
            add("by_source_state", "Por estado de la fuente", _kv(fam.value["by_source_state"], "source_state", "artifacts"))
        con = idx.usable_get("source.connection_consistency", "closure")
        if con is not None:
            add("connections", "Conexiones remotas distintas", con.value["connection_count"])
        add("sources_with_filter", "Fuentes remotas con filtro", sum(1 for f in comp.findings if f.signal_id == "SOURCE_FILTER_PRESENT"))
    return out


def _areas_json(comp: AssessmentComposition, groups: list[dict], idx: FactIndex) -> list[dict]:
    out = []
    by_area_cov = {a.area: a.counts for a in comp.coverage.by_area}
    for card in comp.areas:
        if card.assessability is Assessability.NOT_APPLICABLE:
            reason = public_reason(card.assessability, None, card.applicability.reason.value if card.applicability.reason else None)
        else:
            reason = public_reason(card.assessability, card.cause, None)
        c = by_area_cov[card.area]
        out.append({
            "id": card.area.value, "macro_dimension": M.MACRO_DIMENSION_OF_AREA[card.area].value, "assessability": card.assessability.value, "reason": reason,
            "required_surfaces": {"assessed": c.assessed, "partially_assessed": c.partially_assessed, "not_assessed": c.not_assessed,
                                  "not_applicable": c.not_applicable},
            "limitations_not_observable": [public_reason(Assessability.NOT_ASSESSED, x, None)["reason_code"] for x in card.not_observable],
            "highlights": _highlights(idx, comp, card.area),
            "finding_groups": [g["id"] for g in groups if g["area"] == card.area.value]})
    return out


def _surface_title(surface_id: str) -> str:
    rule_signal = {sp.surface_id: sp.signal_id for sp in SURFACE_REGISTRY if sp.signal_id}
    return f"Regla: {DISPLAY[rule_signal[surface_id]][0]}" if surface_id in rule_signal else SURFACE_TITLES[surface_id]


def _not_applicable_json(comp: AssessmentComposition) -> list[dict]:
    """Superficies que NO APLICAN (trazabilidad). No son evidencia faltante: no se mezclan con las limitaciones."""
    return [{"surface_id": s.surface_id, "title": _surface_title(s.surface_id), "area": s.area.value, "role": s.role.value,
             "reason": public_reason(s.assessability, s.cause, s.not_applicable_reason)}
            for s in comp.surfaces if s.assessability is Assessability.NOT_APPLICABLE and s.role is not SurfaceRole.DERIVED_VIEW]


def _limitations_json(comp: AssessmentComposition) -> list[dict]:
    rule_signal = {sp.surface_id: sp.signal_id for sp in SURFACE_REGISTRY if sp.signal_id}
    out = []
    for s in comp.surfaces:
        if s.assessability in (Assessability.ASSESSED, Assessability.NOT_APPLICABLE):
            continue
        if s.role in (SurfaceRole.DERIVED_VIEW,):
            continue
        r = public_reason(s.assessability, s.cause, s.not_applicable_reason)
        counts = s.counts
        affected = sorted(u.subject for u in s.units if u.assessability not in (Assessability.ASSESSED, Assessability.NOT_APPLICABLE))
        title = f"Regla: {DISPLAY[rule_signal[s.surface_id]][0]}" if s.surface_id in rule_signal else SURFACE_TITLES[s.surface_id]
        out.append({"surface_id": s.surface_id, "title": title, "affected_units": affected, "area": s.area.value, "role": s.role.value, "kind": s.kind.value, "assessability": s.assessability.value,
                    "counts_toward_coverage": s.role is SurfaceRole.REQUIRED,
                    "reason": r, "units_not_assessed": counts.not_assessed + counts.indeterminate, "units_assessed": counts.assessed})
    return out


def build_assessment(comp: AssessmentComposition, facts: Sequence[Fact], *, client: str, terminal_label: str | None = None) -> dict:
    """Documento publico del assessment, derivado SOLO de la composicion de M6 (+ el indice de hechos para los highlights).
    ``terminal_label``: nombre de negocio del modelo analitico terminal TAL COMO lo declara la evidencia (@EndUserText.label ya resuelto);
    es identidad descriptiva (None si la evidencia no lo declara): ninguna regla, hecho ni medida lo consume."""
    idx = FactIndex(facts)
    scope = comp.scope
    applicable = scope.applicability.state is Applicability.APPLICABLE
    groups = _finding_groups(comp)
    overall = comp.coverage.overall
    doc: dict[str, Any] = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "assessment_state": "ASSESSED" if applicable else "NOT_APPLICABLE",
        "versions": {"signals": SIGNALS_RULESET, "assessability": ASSESSABILITY_RULESET, "confidence": CONFIDENCE_RULESET, "reason_codes": REASON_REGISTRY_VERSION,
                     "finding_evidence": FINDING_EVIDENCE_VERSION, "identity": IDENTITY_RULESET, "facts_profile": M.SCHEMA_VERSION, "presentation_order": PRESENTATION_ORDER_VERSION},
        "scope": {"client": client, "terminal_analytic_model": scope.terminal_am,
                  "terminal_analytic_model_label": terminal_label if applicable else None, "applicability": scope.applicability.state.value,
                  "reason": None if applicable else public_reason(Assessability.NOT_APPLICABLE, None,
                                                                    scope.applicability.reason.value if scope.applicability.reason else None),
                  "closure_size": scope.closure_size, "closure_artifacts": list(scope.closure_names), "pool_signature": scope.pool_signature},
    }
    doc["summary"] = {
        "coverage": {"meaning": COVERAGE_MEANING,
                     "strict": _ratio(overall.ratio_strict), "inclusive": _ratio(overall.ratio_inclusive),
                     "required_surfaces": {"applicable": overall.applicable, "assessed": overall.assessed, "partially_assessed": overall.partially_assessed,
                                           "not_assessed": overall.not_assessed + overall.indeterminate, "not_applicable": overall.not_applicable}},
        "confidence": _confidence_json(comp) if applicable else {
            "level": comp.confidence.level.value, "driver": comp.confidence.drivers[0].name.value, "meaning": NOT_APPLICABLE_CONFIDENCE_MEANING,
            "main_limitation": None, "components": [], "provisional_cuts": {"reliable_share_high_medium": list(CONFIDENCE_CUTS["reliable_share"]),
                                                                           "status": "PROVISIONAL_VALIDATE_IN_M8"}, "excluded_from_confidence": []},
        "context_required": _context_required(comp, groups, idx) if applicable else {"open_questions": 0, "questions_backed_by_findings": 0, "questions_without_findings": 0, "occurrences": 0, "questions": [], "deduplicated_questions": []},
        "macro_dimensions": [{"id": x.macro_dimension.value, "state": x.state.value} for x in comp.coverage.macro_dimensions]}
    doc["areas"] = _areas_json(comp, groups, idx)
    doc["observations"] = [g for g in groups if g["kind"] == "OBSERVATION"]
    doc["review_required"] = [g for g in groups if g["kind"] == "REVIEW"]
    doc["descriptive_signals"] = [g for g in groups if g["kind"] == "DESCRIPTIVE"]
    doc["rule_results"] = _rule_results(comp)
    doc["evidence_limitations"] = _limitations_json(comp)
    doc["not_applicable"] = _not_applicable_json(comp)
    doc["surfaces"] = [_surface_json(s) for s in comp.surfaces]
    doc["fact_index"] = [{"fact": f.key, "area": f.area.value, "evidence_class": f.evidence_class.value, "plane": f.plane.value,
                          "assessability": f.assessability.value} for f in sorted(facts, key=lambda f: (f.area.value, f.fact_id, f.subject))]
    return doc


def build_indeterminate_assessment(*, client: str, requested_model: str | None, message: str, pool_signature: str | None = None) -> dict:
    """Documento cuando el pool no pudo cargarse (colision/error de carga): estado INDETERMINATE con causa publica, sin areas inventadas."""
    r = public_reason(Assessability.INDETERMINATE, Cause.COLLISION_OR_LOAD_ERROR, None)
    return {"schema_version": ASSESSMENT_SCHEMA_VERSION, "assessment_state": "INDETERMINATE",
            "versions": {"signals": SIGNALS_RULESET, "assessability": ASSESSABILITY_RULESET, "confidence": CONFIDENCE_RULESET, "reason_codes": REASON_REGISTRY_VERSION,
                         "finding_evidence": FINDING_EVIDENCE_VERSION, "identity": IDENTITY_RULESET, "facts_profile": M.SCHEMA_VERSION, "presentation_order": PRESENTATION_ORDER_VERSION},
            "scope": {"client": client, "terminal_analytic_model": requested_model, "terminal_analytic_model_label": None,
                      "applicability": "INDETERMINATE", "reason": r, "closure_size": 0,
                      "closure_artifacts": [], "pool_signature": pool_signature},
            "summary": {"coverage": None, "confidence": None, "context_required": None, "macro_dimensions": [], "error": message},
            "areas": [], "observations": [], "review_required": [], "descriptive_signals": [], "rule_results": [], "evidence_limitations": [],
            "surfaces": [], "fact_index": [], "not_applicable": []}


# ═══════════════════════════ validacion del contrato y serializacion ═══════════════════════════
REQUIRED_TOP_LEVEL_KEYS = ("schema_version", "assessment_state", "versions", "scope", "summary", "areas", "observations", "review_required",
                           "descriptive_signals", "rule_results", "evidence_limitations", "not_applicable", "surfaces", "fact_index")


def _walk_keys(v: Any, out: list[str]) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            out.append(k)
            _walk_keys(x, out)
    elif isinstance(v, list):
        for x in v:
            _walk_keys(x, out)


def validate_assessment(doc: dict) -> None:
    """Contrato estructural del documento publico. Lanza `ValueError` si algo del contrato no se cumple."""
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in doc]
    if missing:
        raise ValueError(f"faltan claves de nivel superior: {missing}")
    if doc["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
        raise ValueError("schema_version inesperada")
    if doc["assessment_state"] not in ASSESSMENT_STATES:
        raise ValueError("assessment_state invalido")
    keys: list[str] = []
    _walk_keys(doc, keys)
    bad = sorted({k for k in keys if M.find_forbidden_tokens(k)})
    if bad:
        raise ValueError(f"vocabulario prohibido en claves publicas: {bad}")
    for group in (*doc["observations"], *doc["review_required"], *doc["descriptive_signals"]):
        if group["occurrence_count"] != len(group["occurrences"]) or not group["occurrences"]:
            raise ValueError(f"grupo {group['id']}: occurrence_count no coincide con las ocurrencias preservadas")
        unknown = set()
        for o in group["occurrences"]:
            unknown |= set(o["values"]) - set(PUBLIC_VALUE_KEYS[group["id"]])
        if unknown:
            raise ValueError(f"grupo {group['id']}: valores fuera del mapa publico: {sorted(unknown)}")
    codes = set()

    def collect(v: Any) -> None:
        if isinstance(v, dict):
            if "reason_code" in v:
                codes.add(v["reason_code"])
            for x in v.values():
                collect(x)
        elif isinstance(v, list):
            for x in v:
                collect(x)
    collect(doc)
    if not codes <= set(PUBLIC_REASONS):
        raise ValueError(f"codigos de razon fuera del registro publico: {sorted(codes - set(PUBLIC_REASONS))}")


def canonical_assessment_json(doc: dict) -> str:
    """JSON determinista: claves ordenadas, UTF-8, sin NaN, salto final."""
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "), allow_nan=False) + "\n"
