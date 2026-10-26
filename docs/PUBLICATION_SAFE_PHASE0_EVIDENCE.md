# Fase 0 de la republicación del Catálogo — evidencia de implementación local

Rama local `feat/publication-safe-phase0` (worktree `ArquitectoDanwor-p0`) sobre `origin/main` 1726497. **Sin push, PR, merge ni deploy.** Alcance: **solo** H1, H3, H5 y H6. Fuera: H2, aislamiento físico Tenant/Space, migración de rutas, diario de publicaciones, UX histórica.
Interruptor `DANWOR_PUBLICATION_SAFE`, **apagado por defecto** (camino anterior intacto). Activación/rollback: `docs/PUBLICATION_SAFE_RUNBOOK.md`.

## 1. Estados de publicación observados (todos producidos y verificados por pruebas)

| `status` | `Outcome.state` (UI) | `ok` / exit | Escenarios observados | Garantía comprobada |
|---|---|---|---|---|
| `COMPLETED` | `SUCCESS` | true / 0 | camino feliz; republicación idéntica (`changed=false`, **0** archivos reescritos, **0** subidas); modificación/altas/bajas; dependencia compartida retenida y luego borrada; contracción observada; persistencia local por configuración | Única vía a `SUCCESS` |
| `FAILED_NO_CHANGE` | `FAILED` | false / 1 | `EXTRACTION_INCOMPLETE` (lectura fallida; listado fallido en **cada** familia; listado truncado marcado por el CLI); `POOL_IDENTITY_CONFLICT` (otro Space/tenant con contenido distinto); `CATALOG_BUSY` (otra operación aplicando) | Estado **byte-idéntico** (digest) antes/después; sin regenerar ni subir ni registrar Assessment |
| `ROLLED_BACK` | `FAILED` | false / 1 | fallo del generador (`CATALOG_GENERATION_FAILED`); verificación fallida (`VERIFY_FAILED`); excepción inyectada en cada punto del apply (`APPLY_FAILED`) | Estado previo **restaurado y verificado** |
| `COMMITTED_UNCONFIRMED` | `PARTIAL` | false / 3 | subida a Spaces que falla a mitad; sincronización bloqueada (restore degradado) | Local válido y consistente; marcador `.catalog_manifest.json` **no** publicado; no se afirma «Persistencia canónica: OK»; el reintento (idempotente) completa |
| `COMMITTED_CLEANUP_DEFERRED` | `PARTIAL` | false / 3 | manifiesto de **otra raíz** ilegible; manifiesto **propio** previo truncado / JSON inválido / alterado; restore incompleto; manifiesto que se corrompe **después** de la extracción (revalidación bajo candado) | **0 borrados**; el archivo corrupto queda intacto; lo pendiente en `pendingCleanup` se limpia al recuperar el ownership |
| `RECOVERY_REQUIRED` | `FAILED` | false / 1 | restauración que no se puede verificar; journal ilegible | No se publica encima de un estado a medias (con el interruptor encendido **o** apagado) |

`durability`: `remote_confirmed` (solo si `persist_changed == remote`) · `local_only_by_config` (`DANWOR_PERSISTENCE=local`) · `unconfirmed` (⇒ PARTIAL) · `not_requested` (CLI sin `--mirror-up`).

## 2. Matriz de fallos inyectados

Escenario: `PRE` = baseline publicado; cambio real (2 escrituras + 1 borrado + manifiesto + Catálogo). **Caída (kill)** = `BaseException` que salta todo rollback en proceso (como `kill -9`); **excepción** = rollback en proceso. Estados comparados por digest de todo `catalog_local` (manifiestos normalizados). `MIXTO` = estado a medias observado tras la caída.

| Punto de inyección | Falla | Resultado de la operación | Estado tras el fallo | Recuperación | Estado tras recuperar | Reintento |
|---|---|---|---|---|---|---|
| `after_validate` | caída (kill) | el proceso muere | PRE | sin pendientes | PRE | COMPLETED → POST |
| `after_lock` | excepción | propaga (sin journal) | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_lock` | caída (kill) | el proceso muere | PRE | sin pendientes | PRE | COMPLETED → POST |
| `after_journal_planned` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_journal_planned` | caída (kill) | el proceso muere | PRE | DISCARDED | PRE | COMPLETED → POST |
| `after_undo_snapshot` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_undo_snapshot` | caída (kill) | el proceso muere | PRE | ROLLED_BACK | PRE | COMPLETED → POST |
| `mid_pool_write:0` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `mid_pool_write:0` | caída (kill) | el proceso muere | PRE | ROLLED_BACK | PRE | COMPLETED → POST |
| `mid_pool_write:1` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `mid_pool_write:1` | caída (kill) | el proceso muere | MIXTO | ROLLED_BACK | PRE | COMPLETED → POST |
| `after_pool` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_pool` | caída (kill) | el proceso muere | MIXTO | ROLLED_BACK | PRE | COMPLETED → POST |
| `after_cleanup` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_cleanup` | caída (kill) | el proceso muere | MIXTO | ROLLED_BACK | PRE | COMPLETED → POST |
| `before_manifest` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `before_manifest` | caída (kill) | el proceso muere | MIXTO | ROLLED_BACK | PRE | COMPLETED → POST |
| `after_manifest` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_manifest` | caída (kill) | el proceso muere | MIXTO | ROLLED_BACK | PRE | COMPLETED → POST |
| `after_catalog` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `after_catalog` | caída (kill) | el proceso muere | POST | ROLLED_BACK | PRE | COMPLETED → POST |
| `before_verify` | excepción | ROLLED_BACK | PRE | rollback en proceso | PRE | COMPLETED → POST |
| `before_verify` | caída (kill) | el proceso muere | POST | ROLLED_BACK | PRE | COMPLETED → POST |
| `after_local_commit` | caída (kill) | el proceso muere | POST | sin pendientes (ya comprometido) | POST | COMPLETED (NO_CHANGE) → POST |
| `before_remote` | caída (kill) | el proceso muere | POST | sin pendientes (ya comprometido) | POST | COMPLETED (NO_CHANGE) → POST |

Propiedad verificada en las 25 filas: el estado final tras recuperar es **PRE exacto** (o **POST** si ya estaba comprometido), el journal queda terminal, no quedan temporales `.tmp-atomic-*`, y el reintento converge exactamente al estado de una publicación limpia. Las filas `MIXTO` demuestran que la caída SÍ deja un estado a medias y que la recuperación lo deshace.

Otras inyecciones (todas verdes): fallo de lectura de una vista intermedia · fallo de listado en las 5 familias · listado truncado · generador que lanza · verificación que falla · restauración que falla (⇒ `RECOVERY_REQUIRED` hasta recuperar) · journal ilegible · manifiestos (truncado, JSON inválido, alterado, ajeno, ausente-por-restore) · exclusión entre hilos y entre **procesos reales** (el SO libera el candado si el titular muere) · subida parcial/bloqueada · bytes de ratificaciones/respaldos en Spaces intactos.

**Verificación por mutación** (cada una hace fallar las pruebas y se revirtió): compuerta de completitud ciega · borrado autorizado con manifiesto ilegible · rollback sin restaurar · confirmación remota falsa · sin candado de SO · apply sin candado.

## 3. Regresión

* **Suite completa** (sin `test_human_capital_indicadores_view.py`, que requiere `bs4` y queda para el CI): **10337 passed, 23 skipped, 0 failed** (34 min 27 s), sobre el árbol final.
* **104 pruebas nuevas** (`test_publication_txn.py` 15 · `test_publication_safe_phase0.py` 62 · `test_publication_safe_producers.py` 5 · `webapp/test_datasphere_publication_safe.py` 22) — incluido el barrido de 25 casos de fallo inyectado.
* **No regresión:** las suites existentes de publicación, Assessment, perfiles de propietario, generador/Catálogo, APIS_ONLY, regeneración y aislamiento del scoring legacy siguen verdes; el único test existente modificado es el guard de aislamiento del scoring (ampliada su lista de funciones de E/S exentas, con precedente).
* Nota: en este worktree los 6 fallos "conocidos" del checkout principal (datos locales: `test_modeling_assessment_invariants` ×4, `test_next_steps_has_sixteen_rows`, `test_form_fields_labels_placeholders_and_margins`) **no** ocurren.
* **CI pendiente** (no ejecutable localmente): Linux/`fcntl` (el test de contención entre procesos corre en `ubuntu-24.04`) y el test de Indicadores.

## 4. Assessment, Judgment y ratificaciones — sin regresión

* **Cero líneas modificadas** en `assessment_service`, el motor de Juicio, `judgment_decisions`, `governed_decisions*` y `governed_ratification_link` (prueba estática + `git diff`).
* Publicación **abortada** (`FAILED_NO_CHANGE`): el sobre gobernado y su Juicio quedan **byte-idénticos** y el Assessment sigue `CURRENT`; no se registra de nuevo (no se reutiliza evidencia incompleta).
* Republicación **idéntica**: Assessment `CURRENT`, mismo `generated_at`, sobre sin cambios. Cambio comprometido: `STALE` → regenerado `CURRENT` sobre el **mismo** registro (un solo archivo por modelo).
* Ratificaciones/respaldos/sobres gobernados sembrados en Spaces: **bytes idénticos** tras una publicación segura con persistencia; solo se suben claves bajo `catalog_local/`.
* Cambio de orden (único): el registro del Assessment ocurre **después** del commit local y solo en estados comprometidos.

## 5. Limitaciones de DigitalOcean
Réplica única obligatoria (el candado de SO no protege entre instancias); FS efímero (consistencia remota por orden de subida, no por journal); H2 pendiente (los borrados no se propagan a Spaces); identidad del pool aún sin Space/tenant físico (solo guardia); manifiestos corruptos ajenos no se copian a cuarentena; un manifiesto propio previo ilegible puede dejar huérfanos hasta la fase 0B. Detalle en el runbook §5.

## 6. Desvíos respecto del diseño aprobado (transparencia)
1. El Markdown generado **no** se escribe de forma atómica (el generador legacy queda intacto: lo protege el guard de aislamiento del scoring); lo cubre la copia de deshacer del directorio de Catálogo del cliente.
2. Los manifiestos corruptos ajenos se **dejan intactos e informados** (no se copian a cuarentena).
3. `PARTIAL` se presenta con el bloque de aviso existente (el JS ya trata todo lo que no es `SUCCESS` como aviso con el botón rehabilitado): sin cambios de JS.
4. Se amplió la excepción del guard `test_legacy_list_tile_template_and_scoring_modules_are_untouched…` (precedente de «tercera excepción») con 4 funciones de E/S/orquestación del generador.
5. Se agregó `pendingCleanup` al manifiesto (aditivo) para que el cleanup diferido sea reintentable.
6. Los otros productores de `catalog_local` (regeneración, APIS_ONLY) toman el candado **solo** con el interruptor encendido.
