# Assessment — lectura orientada a acciones y evidencia completa

Estado: **presentación implementada localmente** (guía de lectura, evidencia exhaustiva bajo demanda, registro de etiquetas). La sección de acciones (CTA) del Judgment está **solo propuesta** (contrato + simulación, §5); no se implementó ningún texto prescriptivo. Nada cambia en reglas, scoring, Judgment v4, confianza ni evidencia canónica; no se regeneró ningún Assessment.

## 1. Diagnóstico de `AM_ANALISIS_VENCIMIENTO` (Biogenesis)
Perfil gobernado local `governed-assessments/v1/Biogenesis/AM_ANALISIS_VENCIMIENTO__509fdb7beb.json` (generado 2026-10-08, `judgment-rules/v1`, nivel PARCIAL; **producción puede traer un Judgment más nuevo**: la estructura es la misma).

| Pregunta | Hallazgo |
|---|---|
| Evidencia canónica de nomenclatura | `canonical_evidence.naming_evidence.hygiene.artifact_name_populations`: 28 artefactos, 4 cumplen, **24 no cumplen, con la lista nominal de los 24** (`non_compliant_artifacts`) y de los 4 (`compliant_artifacts`). |
| ¿Están los 24 con su motivo? | **Por familia, sí, de forma demostrable**: el perfil conserva `by_identity[]` (procedencia del nombre) con los artefactos que no cumplen y el histograma de familias de **ese** grupo. Los dos grupos con incumplimientos tienen UNA sola familia cada uno (12 `MISSING_LAYER_PREFIX` en `PROJECT_ASSIGNED`; 12 `NO_RECOGNIZED_PATTERN` en `SOURCE_DERIVED_IMPORTED`), así que los 12 de cada familia quedan identificados. Si un grupo tuviera varias familias, la evidencia **no** permite repartirlos y la UI lo dice (solo agregado). No existe un motivo por artefacto más fino que la familia en el perfil persistido. |
| Etiquetas de negocio compartidas | `shared_label_groups` = **16** (agregado). El detalle por grupo (etiqueta, nombres técnicos, formas, dónde se observó) existe **solo para los 6** que `FIELD_VOCABULARY_MIX` marca para revisión (juntos en un artefacto o llegando al modelo terminal). Para las otras 10 solo existe el conteo. |
| ¿Por artefacto y campo o solo agregación? | Para los 6: por etiqueta, con `technical_names`, `forms_by_name`, `co_declared_in`, `terminal_model_fields`. No hay relación por artefacto→campo→etiqueta para las 10 restantes. |
| Qué se perdía / truncaba | **Presenter/plantilla:** artefactos del hallazgo `[:12]`, de cada ocurrencia `[:6]`, unidades de limitaciones `[:8]`, y en Gobierno la evidencia «a, b, c, d, e, f (+18)». **Formateador compartido con el Markdown (`render_md`):** `MAX_OCCURRENCES_SHOWN = 8`, `_names(technical_names, 5)` para etiquetas, `fields[:5]` en capa de paso, `lineage_consumers[:4]`. **Presenter del Judgment:** `judgment_view` descartaba `affected_artifacts`, `cta_candidates`, `what_would_change_the_judgment`, `review_candidates`. **Motor:** un hecho de ÁREA (`numeric_non_measure_fields`) llega al perfil con un tope «… (+N)» ya grabado: no es recuperable desde el perfil. |
| CTA / condiciones de cambio | **Existen y no se mostraban** (6 CTA, 2 condiciones de cambio, ver §5). |

## 2. Evidencia real de las dos observaciones de gobierno
1. **Oportunidades en la convención de nombres** → los 24 artefactos (12 + 12) y los 4 que cumplen, con la regla evaluada (texto persistido del motor).
2. **Etiqueta compartida entre vocabularios técnicos distintos** → 6 etiquetas con todos sus nombres técnicos (p. ej. «unidad medida base» → `/CWM/TARUM`, `MEINS`); el Assessment aclara que solo conserva 6 de 16.

## 3. Qué se implementó (todo presentación)
* **Guía «¿Cómo leer este Assessment?»** al inicio del informe: tabla Pregunta · Ir a (enlace de contenido del Hub) con ayuda `(i)` por fila (tooltip del Hub) y la frase única «Las observaciones de gobierno no necesariamente cambian el nivel metodológico». Solo enlaza secciones que existen en ese informe (navegación `DwAssessmentNav`). El tooltip de «Puntos de atención» solo afirma que la gobernanza de nombres/etiquetas no modifica el nivel cuando el Judgment lo declara (`qualifiers.naming_governance.level_effect == NONE`) — y el código del Judgment confirma que no lee esos hallazgos (`test_the_judgment_engine_does_not_consume…`).
* **Evidencia exhaustiva bajo demanda:** familias de la convención de nombres con TODOS sus artefactos (disclosure del Hub por familia + «Cumplen la convención»); enlace desde la fila de Gobierno; `<details>` nativo «Ver todos los artefactos (N)» en lugar de «… (+N)» (artefactos del hallazgo y de cada ocurrencia, unidades de limitaciones, artefactos de cada «Punto de atención» del Judgment); «Ver la evidencia completa de esta ocurrencia» cuando una línea de texto recortó una lista; etiquetas compartidas con todos sus nombres técnicos y la **nota de alcance real** (6 de 16).
* **Registro centralizado de etiquetas** (`webapp/services/assessment_labels.py`): solo códigos con significado **documentado en el motor** (cada entrada cita su archivo fuente y un test verifica que el código figure ahí). Los identificadores técnicos siguen intactos en el JSON/Markdown y en el glosario del detalle técnico (y como `title` de la etiqueta). Las palabras genéricas (`UNKNOWN`, `SYSTEM`) solo se etiquetan en su contexto.
* Tipografía transversal: las listas dentro de celdas usan `<details>` nativo (heredan la fuente de la celda); los disclosures `dashboard-section` solo viven a nivel de sección. Medido con Playwright: celdas 12,8 px Inter / JetBrains Mono en todas las tablas nuevas, igual que las existentes.

## 4. Códigos del motor visibles: auditoría transversal (34 perfiles reales)
**Etiquetados (semántica documentada en el motor):** familias de incumplimiento de §28 (5 + 1), forma del nombre (3), procedencia del nombre (4), ancla del nombre del campo (7), forma del nombre técnico (5). Fuentes: `naming_rules.py`, `generate_catalog.parse_name`, `identity.py`, `facts_naming.py`.

**Ya traducidos por el motor, con el código como traza discreta (sin cambios):** códigos de razón de evaluabilidad (`NOT_OBSERVABLE_IN_STANDARD_EXPORT`, `NO_UNITS_IN_SCOPE`, `NO_REMOTE_SOURCE_IN_SCOPE`, `SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED`, `CONTEXT_REQUIRED`, `NAME_DOES_NOT_ENCODE_LAYER`, `EDGE_OUTSIDE_RULE_POPULATION`), ids de regla.

**Documentados en el motor pero NO registrados todavía (decisión metodológica de redacción):** `NAME_LAYER_ABOVE_BOUNDARY_POSITION` (`signals.py`: «el nombre dice capa ≥ 2 pero es una hoja del grafo»), `NAME_PREFIX` (`contract.py`: «último recurso: prefijo del nombre»).

**Sin semántica documentada → se dejan tal cual, NO se traducen:** `FAMILY_DIRECTORY`, `STRUCTURAL_MARKER` (solo documentado como base de pertenencia a vistas), `CDS_VIEW`, `OTHER_ORIGIN`, `SAPI_ODP`, `RAW_PHYSICAL_TABLE`, `CDS_EXTRACTION`, `ON_DELTA_INTERVAL`, `AT_SCHEDULED_TIME`, `SOURCE_FILTER_PRESENT`, `VIEW_DIMENSION_DAY`. Los valores como `S4_HANA`, `DATA_STRUCTURE`, `LANGUAGE_DEPENDENT_TEXT` son vocabulario de SAP copiado de la metadata, no códigos del motor: corresponde mostrarlos literales.

**Pendientes de decisión (casos ambiguos):**
1. ¿Se registran `NAME_LAYER_ABOVE_BOUNDARY_POSITION` y `NAME_PREFIX` (texto ya documentado) y con qué redacción?
2. Estados de fuente (`CDS_VIEW`, `SAPI_ODP`, `OTHER_ORIGIN`…): ¿quién define su significado? Hoy no hay documentación en el motor.
3. Los enums del Judgment (`classification`, `statement_key`) ya se traducen en `assessment_presenter`; ¿se migran a este registro para tener una única fuente? (sin cambio visible).
4. La sección «Casos a revisar» usa el formateador compartido con el Markdown; sus líneas de texto conservan ciertos truncamientos (`render_md`). No se tocó el Markdown (tiene pruebas de contrato); el detalle completo está en la UI.

## 5. CTA del Judgment — contrato y simulación (NO implementado)
**Contrato real (`cta-playbook/v1`, `tools/catalog/methodological_judgment/cta.py`):** cada candidato nace de una razón/revisión/limitación del motor; campos `cta_id`, `cause_rule_ids`, `affected_artifacts`, `priority_class` (MATERIAL · RELEVANT · LOCAL · REVIEW · MAINTAINABILITY · EVIDENCE), `action_family`, `action_keys` (claves del playbook, p. ej. `extend_or_reuse_harmonization_contract`), `evidence_refs`, `linked_reason_ids`, `applicability_state` (APPLICABLE · REVIEW · DISABLED_PENDING_EVIDENCE). Las condiciones de cambio (`what_would_change_the_judgment`) son: `ALL_SUSTAINING_REASONS_REMOVED_BY_VALID_EXCEPTION_OR_NOT_APPLICABLE → ADECUADA` y `ACCEPTED_AS_DESIGNED_DOES_NOT_CHANGE_LEVEL → PARCIAL`: el nivel solo cambia al **retirar las razones** con una excepción gobernada o una justificación de «no aplica», «nunca por la sola intención». **El motor no demuestra que ejecutar una acción mejore el nivel**; solo qué estado de la razón lo cambiaría.

**Propuesta de presentación (4 bloques, sin segundo motor ni heurísticas):**

| Bloque | Criterio (de datos del motor) | Para ANALISIS_VENCIMIENTO |
|---|---|---|
| Rectificación metodológica potencialmente relevante | `applicability_state == APPLICABLE` (vinculada a una razón que sostiene el nivel) | `CTA-PRO-RESOLVE-MIXED-UNIVERSE` (MATERIAL; 4 artefactos), `CTA-SRC-GOVERNED-SOURCE` (RELEVANT; 4 artefactos) |
| Mejora de gobierno independiente del nivel | CTA cuya causa el motor declara con `level_effect NONE` | `CTA-GOV-DECLARE-NAMING-CONVENTION` (nombres) |
| Validación contextual pendiente | `applicability_state == REVIEW` (no gobierno) | `CTA-PUB-ENRICH-SEMANTICS` (10 medidas), `CTA-SRC-DECLARE-REPLICATION-MODE` |
| Evidencia insuficiente | `DISABLED_PENDING_EVIDENCE` | `CTA-PRO-PREDICATE-OWNERSHIP-REVIEW` |

Simulación (texto condicional, sin prometer mejora): «Para este modelo, el nivel PARCIAL lo sostiene 1 desvío material (Propagación). Retirar esa razón mediante una excepción gobernada o una justificación de “no aplica” resultaría en ADECUADA según el Judgment; aceptarla “como diseñada” no cambia el nivel.» Cada fila listaría sus artefactos (los mismos `affected_artifacts`) y la familia de acción.

**Decisión pendiente antes de implementar:** las `action_keys` son identificadores en inglés (32 claves); mostrarlas como instrucciones exige que el owner apruebe una redacción por clave (¿la «redacción» es parte del playbook o del presentador?). Hasta entonces la sección no se implementa. «Mejora de gobierno independiente del nivel» solo se puede afirmar para la gobernanza de nombres; para `PUB-SUP-01` (semántica publicada) el motor no declara `level_effect`, por lo que va como «validación contextual».

## 6. Limitaciones de evidencia y notas
* Los 34 perfiles reales locales traen `judgment-rules/v1` (uno v2); el informe de producción puede mostrar un Judgment v4. La presentación no depende de la versión.
* Perfiles anteriores sin `canonical_evidence.naming_evidence` mantienen la lectura previa (`naming_view` devuelve `None`).
* El tope «… (+N)» que el motor graba en hechos de ÁREA (p. ej. `numeric_non_measure_fields`) no se puede completar sin regenerar el Assessment (fuera de alcance).
* `tests/webapp/test_datasphere_assessment.py::test_form_fields_labels_placeholders_and_margins` falla **también en `origin/main`** (campo «Nombre de Cliente» del formulario de publicación): preexistente, no relacionado.
