# Portabilidad BDC — Evaluación cross-runtime (HANA → HDLF/Object Store → Databricks)

Dimensión de evaluación del Arquitecto Danwor que estima el riesgo de portar un dominio
o un data product hacia SAP Business Data Cloud / HDLF / Delta Sharing / Databricks.
Es **SEPARADA e independiente** del cumplimiento de extracción (`evaluate_compliance`):
no lo reemplaza ni recalcula sus veredictos históricos.

> ⚠️ No son reglas oficiales/universales de SAP. Es una matriz basada en **evidencia
> interna Danwor** (incidentes reales), con estado de validación y confianza explícitos,
> ampliable con nuevos casos.

## Por qué existe (aprendizaje que la motiva)

Un tipo válido en HANA **no** constituye necesariamente un contrato portable de forma
confiable hacia HDLF/Databricks. Dos incidentes reales lo expusieron:

1. **DecimalFloat monetario** — campos monetarios de S/4HANA llegaron como DecimalFloat;
   durante la publicación a HDLF se tradujeron mal (incluso a `String(10)`) y la carga
   falló. Solución: normalizar a `DECIMAL(38,6)` en la capa contractual.
2. **`DECIMAL(37,19)` inferido en un porcentaje** — `ConfirmedQuantity/OrderQuantity*100`
   inferido con escala 19; el Transformation Flow HANA→HDLF fallaba antes de Spark. Un
   cast tardío en la publicación no alcanzó: hubo que **tipificar en la expresión de
   origen** (`CAST(... AS DECIMAL(15,6))`), en la misma capa donde nace el campo.

## Dominio vs Data Product (no se mezclan)

- **Dominio Datasphere** → diagnóstico **PREVENTIVO**: ¿este dominio será portable a BDC?
  Los hallazgos de tipo pueden ser `riesgo` (penalizan) o `buena_practica` (reconocen una
  normalización correcta).
- **Data Product BDC publicado** → análisis **RETROSPECTIVO**: ya está publicado, así que
  la compatibilidad mínima ya se resolvió. Dos controles separados:
  - **Safety check** — incompatibilidades **residuales** en el contrato → penalizan.
  - **Adaptaciones / workarounds** — tipos que parecen normalizados por portabilidad
    (p. ej. `DECIMAL(38,6)` monetario, `DECIMAL(15,6)` en un ratio) → `adaptacion`/`hint`,
    **no penalizan**; piden revisar/documentar antes de reutilizar.

El evaluador elige el modo por el generador (dominio = `flujo_*.md`; data product =
`data_products/*.md`); para el DP evalúa el **contrato publicado** (nodos Object Store),
no el upstream HANA de construcción.

## Cinco tipos de hallazgo (no un warning genérico)

| Tipo | Semáforo | Significado |
|---|---|---|
| **riesgo** | rojo (alto) / naranja (medio) / ámbar (nota) | Posible incompatibilidad o pérdida de fidelidad. Penaliza. |
| **adaptacion** | azul | Evidencia de que el campo se modificó a propósito para portabilidad. No penaliza. |
| **buena_practica** | verde | El tipo fue normalizado correctamente en la capa contractual. |
| **hint** | azul | Patrón a reconocer antes de replicar (posible workaround). No penaliza. |
| **no_evaluable** | ámbar | No hay metadata suficiente. **Nunca** se asume compatible. |

## Scoring y semáforo

- Dimensión **paralela**: cada activo recibe un **score 0-100** + clasificación
  (`plena`/`buena`/`condicionada`/`inspiracional`/`no_recomendable`) + semáforo, aparte
  del veredicto de extracción. `config.peso_global = 0` → NO altera el veredicto histórico.
- Modelo: `100 − Σ(penalizaciones por riesgo × severidad × confianza) + reconocimiento por
  normalización − penalización acotada por metadata faltante`.
- Salvaguardas (evitan que muchos warnings menores hundan el score):
  - **cap por regla** (`penalizacion_max_por_regla`): el aporte de una misma regla se topa.
  - **regla bloqueante** (p. ej. PK/FK): impide `buena`/`plena`.
- Cada hallazgo es explicable: artefacto, campo, tipo actual, regla, severidad, confianza,
  recomendación, capa donde corregir, y si afecta o no el scoring.

## La matriz configurable (crece sin tocar código)

Archivo: `content_packs/<pack>/portability_bdc.yaml` (fuente única). Si falta, el evaluador
cae a los seeds de `tools/catalog/portability_bdc.py`.

Cada regla: `id`, `nombre`, `descripcion`, `cuando` (condiciones), comportamiento por
contexto `dominio` / `data_product` (`tipo_hallazgo`, `severidad`, `peso`, `control`,
`bloqueante`), `recomendacion`, `capa_correccion`, `confianza`, `estado_validacion`,
`incidentes`, `habilitada`, `version`.

Condiciones (`cuando`) soportadas: `semantica` / `semantica_en`, `tipo_base`,
`tipo_o_native_in`, `precision_min/max/eq`, `escala_min/max/eq`, `longitud_min/max`,
`derivado`, `sin_cast`, `pk_fk_tipo_distinto`, y `o:` (OR de sub-condiciones).

### Agregar / validar una regla nueva
1. Agregá un item a `reglas:` en el YAML (o desactivá uno con `habilitada: false`).
2. Estados de validación: `observado → reproducido → validado → recomendado`
   (`pendiente` / `deprecado`). Marcá `estado_validacion` según la evidencia real.
3. Referenciá el incidente/producto en `incidentes:` y la `fuente_evidencia`.
4. Regenerá el catálogo: la telemetría (`reports/portability_bdc.yaml`) muestra cuánto
   dispara la regla y en qué activos, para calibrar antes de darla por validada.

## Metadata que consume (y sus límites actuales)

Del CSN capturamos por campo: `tipo`, precisión/escala (en `longitud`), `native`
(`@DataWarehouse.native.dataType`), `derived` + `cast` (de las columnas del query), y
`assoc_col` (columna PK referenciada por una FK, del `on` de la asociación).

**Limitaciones**: la detección de campos derivados depende de que la expresión esté en el
CSN de la vista (no siempre para artefactos publicados que ya “aplanaron” el cálculo); la
semántica funcional se infiere por nombre/label (heurística, ajustable por keywords); la
confianza baja cuando falta lineage/expresión o hay muchos campos no evaluables.

## Acknowledgements (senior)

Se registran en el bloque `manual:` del `.md` del activo (preservado en regeneración):
`manual.portabilidad_ack: [{campo, artefacto, estado, nota}]`, con
`estado ∈ pendiente|reconocido|documentado|descartado|falsa_deteccion`. Sirven para que un
consultor que reutilice el producto no pierda decisiones técnicas importantes, y para
descartar falsos positivos sin tocar la matriz.

---

# Guía corta para consultores — Cómo interpretar la Portabilidad BDC

La sección **“Portabilidad BDC”** aparece al final del detalle de un dominio o data product.

- **El score y el semáforo** te dicen qué tan listo está el activo para el cruce de
  runtimes. En un **dominio** es preventivo (antes de publicar); en un **data product** es
  retrospectivo (ya publicado).
- **Colores de los hallazgos**:
  - 🔴/🟠 **Riesgo** — hay una posible incompatibilidad o pérdida de fidelidad. Resolver
    en la capa que indica la recomendación (casi siempre **la contractual o la expresión de
    origen**, no la publicación).
  - 🟡 **Ámbar / nota** — revisión recomendada, bajo impacto.
  - 🔵 **Azul (adaptación / hint)** — el campo **parece** haber sido normalizado por
    portabilidad (no es un error). Conservá el cast y **documentá la decisión** antes de
    reutilizar o modificar el contrato.
  - 🟢 **Verde** — tipo normalizado correctamente.
  - ⚪ **No evaluable** — falta metadata; no asumas que es compatible.
- **Antes de replicar un data product**: mirá las adaptaciones. Un `DECIMAL(38,6)` monetario
  o un `DECIMAL(15,6)` en un porcentaje casi siempre son normalizaciones deliberadas para
  portabilidad — no las “simplifiques” sin entender por qué están.
- **Regla de oro** (de los incidentes reales): el tipo de un campo derivado debe fijarse
  con `CAST` **en la misma capa/expresión donde nace**, no tarde durante la publicación.

---

# Compatibilidad BDC por restricciones (`compatibilidad_bdc/v1`)

Eje **aditivo e independiente** del score de arriba: no emite puntaje y no cambia ni el score, ni la
confianza, ni los hallazgos de portabilidad. Vive en el bloque *Portabilidad & preparación BDC* del
Assessment, como «Compatibilidad con destinos BDC».

- **Fuente única**: la sección `compatibilidad` de `content_packs/<pack>/portability_bdc.yaml`
  (escenarios = mecanismo × destino, restricciones `SAPC-*`, fuentes SAP). Motor puro:
  `tools/catalog/compat_bdc.py`; composición para el informe: `webapp/services/bdc_compatibility_service.py`.
  No hay seeds en código a propósito: duplicar las restricciones crearía dos fuentes de verdad.
- **Evidencia**: el `tipo`/`longitud` canónico de cada columna (el mismo que consume el evaluador de
  portabilidad). Nunca el nombre ni el label del campo.
- **Estados**: `compatible` · `conversion_documentada` · `condicionada` · `adaptacion_requerida` ·
  `incompatible_explicita` (+ `no_evaluable`). Que un tipo no figure en una matriz SAP **no** es una
  prohibición: es `condicionada` + `pendiente_verificacion`.
- **Validación de cada restricción**: `sap_documentado` (doc. oficial, con `fuentes`), `danwor_empirico`
  (observación de proyectos) o `pendiente_verificacion`.
- **Contexto de publicación**: se **declara** en el flujo (`manual.publicacion_bdc`), por ejemplo
  `publicacion_bdc: [{ mecanismo: replication_flow, destino: local_table_file }]`. Nunca se infiere del
  linaje y no lo modifica. Sin declaración, el diagnóstico es «condicionado al destino» y lista las preguntas.
- **Metodología**: cada restricción referencia una sección del Copiloto por `key_topics`
  (`module_reference`), y las URLs SAP se registran una vez en `content/apendices/A04` y en los
  `{{support: …}}` del contenido; un test verifica que no diverjan.

---

# Portability Scoring v2 (`portability-scoring/v2`)

**Qué cambia respecto de la v1 descrita arriba** (la v1 sigue explicando el origen de las reglas y los incidentes):

- El score mide la **consistencia estructural de los tipos** del dominio, **independiente del destino**. Lo que depende del mecanismo o del destino (p. ej. `DecimalFloat`) se evalúa
  en `compatibilidad_bdc/v1` (sección `compatibilidad` del YAML) y aparece en la **misma tabla** como «condicionado por destino», sin sumar al score ni a los riesgos.
- **Bandas** (ids persistidos sin cambios; solo cambia la etiqueta visible): `plena` = «Sin hallazgos estructurales» (o «Sin elementos estructurales que evaluar» si ninguna regla
  estructural tuvo entrada), `buena` = «Observaciones menores», `condicionada` = «Observaciones relevantes», `inspiracional` = «Inconsistencias estructurales»,
  `no_recomendable` = «Inconsistencias críticas». «Condicionada/Plena/Buena» ya no se usan como etiqueta: colisionaban con la adopción metodológica y con la compatibilidad.
- **Reglas**: PB-001 solo como safety check en data product; PB-002 exige campo derivado sin CAST (los decimales con escala alta *declarada* son el indicio informativo PB-002H,
  sin coordenadas ni tipos de cambio); PB-003 es un **indicio** por nombre (nunca penaliza; texto: solo magnitudes con precisión validada, nunca «monetario»; descarta unidades,
  códigos, fechas, indicadores y entidades; longitud ≥ 2); PB-004 solo numéricos; PB-005 solo por **base de tipo distinta** (bloqueante) y PB-005B para «FK más ancha que su PK»
  (alineación potencialmente destructiva: con solo metadata no se afirma truncamiento ni pérdida; las ampliaciones compatibles no generan hallazgo).
- **Tipo de hallazgo → etiqueta**: `riesgo` = Riesgo (evidencia estructural), `hint` = Indicio (por nombre, sin penalización), `adaptacion` = **Normalización detectada**
  (ya aplicada; no confundir con «Adaptación requerida» de compatibilidad).
- **Confianza** (de la *evaluación*; no mide compatibilidad con un destino): *baja* si la cobertura de metadata < 0.7; *media* si < 0.9, si la banda cambia al retirar los hallazgos
  que dependen de nombres (`depende_de_nombre` en la matriz) o si las reglas estructurales no tuvieron evidencia que ejercer (campos numéricos derivados / relaciones FK resueltas);
  *alta* en otro caso. Ausencia de riesgos ≠ evidencia suficiente. La evidencia que respalda la confianza se muestra en el (i) del tile.
- **KPI** por **campos distintos** (`campos_distintos`); las ocurrencias por artefacto siguen en `findings` y en el detalle de cada fila.
- **Persistencia/migración**: `auto.portabilidad_bdc` (generado por el Catálogo) lleva `version`. Un resultado sin `version` es v1 y la UI lo marca; la migración es **regenerar el Catálogo**
  (la firma del generador ahora incluye `portability_bdc.py` y el YAML, así que un cambio de scoring invalida el cache incremental). No hay backfill de assessments.
- **Calibración**: PB-003 se validó con una muestra de calibración (362 tuplas) y una **muestra independiente** (195 tuplas de 292 entidades fuera de los 35 flujos): 0 falsos positivos
  en 191 negativos; límites y falsos negativos conocidos (duraciones en días, vocabulario) en `tests/fixtures/portability_v2/` y `tests/test_portability_scoring_v2.py`.
