# CTA del Judgment (`cta-playbook/v1`) — matriz de las 32 claves (SOLO PROPUESTA)

Estado: **propuesta para aceptación del owner; nada de esto se muestra ni se implementa.** Todo lo "verificado" sale de `tools/catalog/methodological_judgment/{cta,rules,engine,contract}.py` (leído, no ejecutado en producción). Los textos orientativos son **redacciones propuestas**: el código solo define el identificador de cada clave y la regla que la activa; no define su redacción.

## 1. Qué decide el nivel (demostrado en `engine._decide`)
`_decide` recibe SOLO cuatro entradas: razones (`reasons`), fortalezas (`strengths`), contratos no evaluables y «decisión-crítica indeterminada». En ese orden:
1. alguna razón **MATERIAL_DEVIATION** viva → `PARCIAL` (LVL-02);
2. algún contrato no evaluable → `No determinable` (LVL-01);
3. fuente externa indeterminada y decisión-crítica → `No determinable` (LVL-05);
4. alguna razón **RELEVANT_WEAKNESS** viva → `ADECUADA` (LVL-03);
5. fortalezas en las cuatro dimensiones → `BUENA` (LVL-04); si no → `No determinable` (LVL-06).

Consecuencias verificables:
* Solo MATERIAL y RELEVANT son consultadas. **LOCAL_WEAKNESS, revisiones, calificadores y limitaciones no participan** del nivel (los calificadores `naming_governance` declaran `level_effect NONE`; el docstring de `rules.py` dice que los cortes de calificadores «no tienen efecto sobre el nivel»).
* Una razón sale de la determinación **solo** si su estado pasa a `GOVERNED_EXCEPTION` o `NOT_APPLICABLE` (`contract.REMOVING_STATES`); `ACCEPTED_AS_DESIGNED` no la retira. El resultado se recalcula con lo que queda (`engine._what_would_change`): al retirar la(s) razón(es) que sostienen el nivel, el motor re-decide con las demás (p. ej. PARCIAL→ADECUADA si queda una RELEVANT, o PARCIAL→No determinable si no queda ninguna ni fortalezas).
* **El motor no demuestra que ejecutar una acción mejore el nivel**: demuestra qué estado de la razón lo cambiaría. Por eso ningún texto propuesto promete una mejora.

## 2. Reglas causa (15) — condición que activa el CTA y efecto sobre el nivel
| Regla | Clase / estado | Condición (verificada en `rules.py` / `cta.py`) | Efecto sobre el nivel demostrado |
|---|---|---|---|
| PRO-MAT-01 | MATERIAL · razón → `APPLICABLE` | El nodo de PRODUCTO (proveedor directo del AM) re-ejecuta integración/armonización: universo mixto (consume un contrato armonizado de nivel hecho o alcanza una tabla física cruda y además lee hojas de frontera directamente) o integración multi-frontera sin armonización. Hojas de sistema y `EXTERNAL_CONTRACT_UNDETERMINED` excluidas. | Sostiene `PARCIAL`. Retirada vía excepción gobernada / «no aplica» → el motor re-decide con lo restante. |
| LEK-MAT-02 | MATERIAL · razón → `APPLICABLE` | Fuga de responsabilidad distribuida con consecuencia trazable en el contrato publicado (vocabulario de frontera sin resolver llega verbatim al consumidor, o semántica reutilizable creada en ≥2 nodos aguas abajo sobre una frontera técnica). | Igual que PRO-MAT-01. |
| SRC-REL-01 | RELEVANT · razón → `APPLICABLE` | Tabla física cruda en la espina cuyo camino al producto omite la frontera reutilizable y la responsabilidad es compensada por lógica de Modelado. | Sostiene `ADECUADA` **solo si no hay MATERIAL vivo**; con MATERIAL vivo su retirada no cambia el nivel. |
| HAR-REL-01 | RELEVANT · razón → `APPLICABLE` | Armonización técnica/no resuelta (candidatos que conservan el vocabulario de la fuente) MÁS consecuencia semántica aguas abajo (vocabulario técnico llega al consumidor, semántica reutilizable creada aguas abajo, o la propagación completa la alineación de ramas). | Igual que SRC-REL-01. |
| HAR-REL-02 | RELEVANT · razón → `APPLICABLE` | Responsabilidades no separables por rol declarado: nodos de composición sin rol declarado a ≥2 distancias distintas + reconvergencia + candidatos de armonización coexistiendo con integración aguas arriba. | Igual que SRC-REL-01. |
| SRC-LOC-01 | LOCAL · razón → `APPLICABLE` | Otra tabla física cruda (fuera de la espina, vía candidato o sin compensación). | **Ninguno**: `_decide` no consulta razones LOCAL. |
| PRO-LOC-01 | LOCAL · razón → `APPLICABLE` | Nodo de producto con uniones y columnas derivadas, no material, con proveedores efectivos solo de vista/armonización (alineación de ramas completada en el producto). | **Ninguno** (LOCAL). |
| SRC-REV-01 | revisión `OPEN` → `REVIEW` | Hay orígenes externos y alguna hoja remota con contrato `EXTERNAL_CONTRACT_UNDETERMINED`. | La limitación asociada solo `can_change_level` si es decisión-crítica (expone su vocabulario verbatim al contrato publicado): entonces el nivel es `No determinable` LVL-05 hasta clasificar la fuente. En otro caso, ninguno. |
| SRC-REV-02 | revisión `OPEN` → `REVIEW` | `source.replication_mode` está `NOT_ASSESSED` (el export estándar no expone replicación/federación). | **Ninguno** (`level_effect NONE`, `closure_effect NONE`). |
| MNT-QUAL-01 | revisión `OPEN` → `REVIEW` | Cadena gráfica profunda con lógica de integración/transformación dentro de vistas gráficas. | **Ninguno** (calificador). |
| PRO-REV-01 | revisión `NOT_EVALUATED/DISABLED_PENDING_EVIDENCE` → `DISABLED_PENDING_EVIDENCE` | Hay nodos de producto con predicados; la revisión de predicados fijos está deshabilitada en el motor («NO evaluados»). | **Ninguno** (la limitación `predicate_ownership_not_evaluated` tiene `can_change_level false`). |
| GOV-REV-01 | calificador `naming_governance` con `disposition NEEDS_CONTEXT` → `REVIEW` | Estado de naming distinto de `NONE`/`NOT_DETERMINABLE`. | **Ninguno** (`level_effect NONE`, `closure_effect NONE` declarados). |
| FOC-REV-01 | calificador `product_focus == GENERIC` → `REVIEW` | Sin derivaciones, sin predicados no triviales y sin uniones. | **Ninguno** (los calificadores no entran en `_decide`). |
| EVD-LIM-01 | limitación → `REVIEW` | `local_leaf_provenance_not_evaluated` o `harmonization_mapping_not_observable`. | `can_change_level false`. En modelos `No determinable` por evidencia insuficiente, `_what_would_change` indica «establecer evidencia…» sin predecir el resultado. |
| PUB-SUP-01 | soporte → `REVIEW` | `supporting.published_measures_without_business_identity` no vacío. | **Ninguno demostrado**: no es razón ni entrada de `_decide`. |

## 3. Matriz de las 32 claves
Estado = `applicability_state` que el motor asigna al CTA que contiene la clave (varias claves comparten CTA). «Evidencia» = tipos de `evidence_refs` que el CTA lleva (observados en los 34 perfiles locales con el ruleset vigente). La columna «Efecto demostrado» remite a §2.

| # | Clave técnica | Regla(s) causa | Texto orientativo propuesto | Evidencia asociada | Estado | Efecto en el nivel (§2) | Cuándo NO corresponde prometer mejora |
|---|---|---|---|---|---|---|---|
| 1 | `evaluate_governed_source_contract` | PRO-MAT-01 | Evaluar si lo que el producto lee directo de las fuentes puede consumirse mediante un contrato de fuente gobernado. | `ce:view_complexity`, `ce:assembly_topology`, `fact:source.contract`, `ce:harmonization_contract` | APPLICABLE | Sostiene PARCIAL; retirada de la razón re-decide | Si el modelo mantiene otra razón MATERIAL u otra RELEVANT: el nivel puede no subir |
| 2 | `extend_or_reuse_harmonization_contract` | PRO-MAT-01 | Evaluar extender o reutilizar un contrato de armonización para lo que hoy resuelve el nodo de producto. | ídem | APPLICABLE | ídem | ídem |
| 3 | `move_reusable_transformations_to_harmonization` | PRO-MAT-01 | Evaluar trasladar a armonización las transformaciones reutilizables que hoy están en el nodo de producto. | ídem | APPLICABLE | ídem | ídem |
| 4 | `keep_only_product_specific_composition_in_propagation` | PRO-MAT-01, PRO-LOC-01 | Dejar en Propagación solo la composición propia del producto. | ídem; `ce:view_complexity` | APPLICABLE | MATERIAL: ídem · LOCAL: ninguno | Cuando solo existe PRO-LOC-01 (LOCAL): sin efecto sobre el nivel |
| 5 | `resolve_responsibility_at_expected_boundary` | LEK-MAT-02 | Resolver la responsabilidad en la frontera esperada, en lugar de distribuirla en artefactos intermedios. | `ce:published_field_lineage`, `ce:source_contract_classes`, `fact:source.contract`, `fact:source.downstream_reach` | APPLICABLE | Sostiene PARCIAL | Igual que #1 |
| 6 | `normalize_vocabulary_toward_published_contract` | LEK-MAT-02 | Normalizar el vocabulario antes de que llegue al contrato publicado. | ídem | APPLICABLE | ídem | ídem |
| 7 | `reduce_distributed_logic_in_intermediate_artifacts` | LEK-MAT-02 | Reducir la lógica distribuida en artefactos intermedios. | ídem | APPLICABLE | ídem | ídem |
| 8 | `enrich_business_identity_of_published_measures` | LEK-MAT-02 | Enriquecer la identidad de negocio de las medidas publicadas. | ídem | APPLICABLE | ídem | ídem |
| 9 | `evaluate_governed_cds_or_odp_instead_of_physical_table` | SRC-REL-01, SRC-LOC-01 | Evaluar un CDS u ODP gobernado en lugar de la tabla física. | `fact:source.downstream_reach`, `ce:leaf_provenance`, `ce:physical_source_boundaries`, `ce:source_contract_classes` | APPLICABLE | REL: sostiene ADECUADA solo sin MATERIAL vivo · LOC: ninguno | Con MATERIAL vivo; o si solo hay SRC-LOC-01 |
| 10 | `record_governed_exception_if_legitimate` | SRC-REL-01 | Si el acceso directo es legítimo, registrarlo como excepción gobernada. | ídem | APPLICABLE | La excepción gobernada es uno de los dos estados que retiran la razón (`REMOVING_STATES`) | Con MATERIAL vivo la retirada no cambia el nivel; «aceptado como diseñado» NO retira |
| 11 | `normalize_or_enrich_vocabulary_in_harmonization` | HAR-REL-01 | Normalizar o enriquecer el vocabulario en la capa de armonización. | `ce:harmonization_contract`, `ce:published_field_lineage`, `ce:view_complexity` | APPLICABLE | Sostiene ADECUADA (sin MATERIAL vivo) | Con MATERIAL vivo |
| 12 | `publish_reusable_business_labels` | HAR-REL-01 | Publicar etiquetas de negocio reutilizables. | ídem | APPLICABLE | ídem | ídem |
| 13 | `remove_projections_without_semantic_responsibility` | HAR-REL-01 | Retirar proyecciones que no aportan responsabilidad semántica. | ídem | APPLICABLE | ídem | ídem |
| 14 | `consolidate_redundant_intermediate_views` | HAR-REL-02, MNT-QUAL-01 | Consolidar vistas intermedias redundantes. | `ce:assembly_topology`, `ce:branch_reconvergence`, `ce:view_complexity` | APPLICABLE (HAR-REL-02) / REVIEW (MNT) | HAR-REL-02: ADECUADA sin MATERIAL vivo · MNT: ninguno | Con MATERIAL vivo; o si solo corresponde a MNT-QUAL-01 |
| 15 | `make_layer_roles_explicit` | HAR-REL-02 | Hacer explícitos los roles de capa. | `ce:assembly_topology`, `ce:branch_reconvergence` | APPLICABLE | Sostiene ADECUADA (sin MATERIAL vivo) | Con MATERIAL vivo |
| 16 | `reduce_unnecessary_reconvergence` | HAR-REL-02 | Reducir la reconvergencia innecesaria. | ídem | APPLICABLE | ídem | ídem |
| 17 | `establish_class_of_external_object` | SRC-REV-01 | Establecer la clase del objeto externo (qué tipo de contrato de origen es). | `area:SOURCE_EXTRACTION` | REVIEW | Solo si la limitación es decisión-crítica (LVL-05) | Si no es decisión-crítica: ninguno |
| 18 | `declare_replication_or_federation_mode` | SRC-REV-02 | Declarar el modo de replicación o federación. | `area:SOURCE_EXTRACTION` | REVIEW | Ninguno | Siempre |
| 19 | `declare_load_strategy` | SRC-REV-02 | Declarar la estrategia de carga. | ídem | REVIEW | Ninguno | Siempre |
| 20 | `review_graph_decomposition` | MNT-QUAL-01 | Revisar la descomposición del grafo. | `ce:view_complexity` | REVIEW | Ninguno | Siempre |
| 21 | `evaluate_sql_where_it_improves_traceability` | MNT-QUAL-01 | Evaluar SQL donde mejore la trazabilidad. | ídem | REVIEW | Ninguno | Siempre |
| 22 | `preserve_graphical_where_clearer` | MNT-QUAL-01 | Conservar lo gráfico donde resulte más claro. | ídem | REVIEW | Ninguno | Siempre |
| 23 | `review_predicate_ownership` | PRO-REV-01 | Revisar a quién pertenece cada predicado fijo. | `ce:expression_occurrences` | DISABLED_PENDING_EVIDENCE | Ninguno | Siempre; además el motor no evalúa esta revisión |
| 24 | `evaluate_governed_access_control` | PRO-REV-01 | Evaluar si el predicado corresponde a un control de acceso gobernado. | ídem | DISABLED_PENDING_EVIDENCE | Ninguno | Siempre |
| 25 | `declare_local_convention_via_governed_exception` | GOV-REV-01 | Declarar la convención local de nombres mediante una excepción gobernada. | (sin refs; calificador) | REVIEW | Ninguno (`level_effect NONE`) | Siempre |
| 26 | `align_naming_where_role_ambiguity_justifies` | GOV-REV-01 | Alinear nombres donde la ambigüedad de rol lo justifique. | ídem | REVIEW | Ninguno | Siempre |
| 27 | `confirm_product_intent` | FOC-REV-01 | Confirmar la intención del producto. | (sin refs; calificador) | REVIEW | Ninguno | Siempre |
| 28 | `acquire_missing_processing_evidence` | EVD-LIM-01 | Adquirir la evidencia de procesamiento que falta. | `ce:leaf_provenance`, `ce:harmonization_contract` | REVIEW | `can_change_level false`; en `No determinable` por evidencia insuficiente puede ser lo que falta para afirmar | No prometer salir de «No determinable» |
| 29 | `complete_missing_mapping_evidence` | EVD-LIM-01 | Completar la evidencia de mapeos que falta. | ídem | REVIEW | ídem | ídem |
| 30 | `associate_reusable_business_dimensions` | PUB-SUP-01 | Asociar dimensiones de negocio reutilizables. | `ce:published_contract` | REVIEW | Ninguno demostrado | Siempre |
| 31 | `enrich_business_labels` | PUB-SUP-01 | Enriquecer las etiquetas de negocio. | ídem | REVIEW | Ninguno demostrado | Siempre |
| 32 | `reduce_source_vocabulary_leakage` | PUB-SUP-01 | Reducir la fuga de vocabulario de la fuente. | ídem | REVIEW | Ninguno demostrado | Siempre |

## 4. Observado en los 34 perfiles locales (ruleset vigente `judgment-rules/v4`, en memoria)
| CTA | Perfiles | Estados observados |
|---|---|---|
| CTA-GOV-DECLARE-NAMING-CONVENTION | 34 | REVIEW |
| CTA-PRO-PREDICATE-OWNERSHIP-REVIEW | 33 | DISABLED_PENDING_EVIDENCE |
| CTA-SRC-DECLARE-REPLICATION-MODE | 27 | REVIEW |
| CTA-SRC-GOVERNED-SOURCE | 17 | APPLICABLE |
| CTA-PUB-ENRICH-SEMANTICS | 16 | REVIEW |
| CTA-PRO-RESOLVE-MIXED-UNIVERSE | 9 | APPLICABLE |
| CTA-SRC-DOCUMENT-EXTERNAL-CONTRACT | 9 | REVIEW |
| CTA-MNT-REVIEW-GRAPH-DECOMPOSITION | 9 | REVIEW |
| CTA-EVD-CLOSE-EVIDENCE-GAP | 8 | REVIEW |
| CTA-HAR-CONSOLIDATE-FRAGMENTED-LAYERS | 8 | APPLICABLE |
| CTA-FOC-CONFIRM-PRODUCT-INTENT | 6 | REVIEW |
| CTA-HAR-HARMONIZE-REUSABLE-SEMANTICS | 4 | APPLICABLE |
| CTA-LEK-RESTORE-RESPONSIBILITY | 1 | APPLICABLE |

## 5. Presentación propuesta (4 bloques, ya acordada en el informe anterior)
1. **Rectificación metodológica potencialmente relevante:** CTA `APPLICABLE` cuya regla causa es MATERIAL o RELEVANT (filas 1–16 salvo las LOCAL). Redacción siempre condicional y con el estado que cambiaría el nivel; nunca «mejorará».
2. **Mejora de gobierno independiente del nivel:** causa con `level_effect NONE` declarado (GOV-REV-01 → filas 25–26) y las demás revisiones/calificadores sin entrada en `_decide` (MNT-QUAL-01, SRC-REV-02, FOC-REV-01).
3. **Validación contextual pendiente:** CTA `REVIEW` (incluye SRC-REV-01, PUB-SUP-01, EVD-LIM-01).
4. **Evidencia insuficiente:** `DISABLED_PENDING_EVIDENCE` (PRO-REV-01).

**Decisiones del owner pendientes antes de implementar:** (a) aprobar o corregir los 32 textos; (b) si los 4 bloques usan esos nombres; (c) si PUB-SUP-01 se muestra como «validación contextual» (el motor no declara su efecto); (d) si SRC-LOC-01/PRO-LOC-01 (LOCAL) se muestran como CTA aunque el nivel no dependa de ellos.
