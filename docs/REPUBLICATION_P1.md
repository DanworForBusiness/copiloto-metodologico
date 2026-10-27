# P1 — Reimportación del catálogo y regeneración consistente del assessment

Estado: implementado en la rama `feat/republication-p1` (commits locales, sin push/PR/merge/deploy). Interruptor: **`DANWOR_PUBLICATION_SAFE`**, apagado por defecto (ver `docs/PUBLICATION_SAFE_RUNBOOK.md`
para activar, desactivar y operar). Diseño previo: `docs/audit/CATALOG_REPUBLICATION_AUDIT_20261026.md` y `docs/audit/CATALOG_REPUBLICATION_PHASE0_DESIGN_20261026.md`.

## 1. Contrato (qué garantiza una reimportación)

| Requisito | Cómo se cumple |
|---|---|
| Identidad canónica, no solo el nombre visible | El cliente es **una** identidad: `canonical_client_dir` reusa el directorio existente aunque cambie la capitalización (Linux distingue mayúsculas) y rechaza con `CLIENT_KEY_CONFLICT` si ya existen dos variantes. Raíz = `(Space, tipo, objeto)` + tenant en el manifiesto |
| Reemplazo lógico, nunca dos versiones activas | El assessment de un AM es **un** archivo (`governed-assessments/v1/<cliente>/<AM>__<tag>.json`); la versión nueva lo reemplaza. El Catálogo materializado del cliente se regenera. Lo retirado se borra **también en Spaces** |
| Assessment solo con las entradas nuevas | El motor corre **sobre el pool ya aplicado** de la misma unidad (prueba: el espía del motor nunca ve la vista retirada) |
| Sin mezcla histórico/nuevo | Un solo marcador de activación por cliente (`.publication_state/<cliente>.publication.json`) con digests de **toda** la unidad; `verify`/`verify_ledgers` detectan cualquier mezcla |
| Publicación consistente (sin estado híbrido) | La unidad (pool + linaje + Catálogo + assessments + bitácora) se aplica con journal/undo detrás de la **compuerta de lectores** y solo se abre con la versión **confirmada en Spaces** o deshecha |
| Si una etapa falla, queda la última versión válida | Cualquier falla (extracción, generación del Catálogo, generación o escritura del assessment, verificación, activación, confirmación) restaura la versión previa **en local y en Spaces** |
| Fallos de listado de Spaces, no solo de escritura | `preflight` exige restore completo, sin locales sin reconciliar, lectura de prueba e **inventario** remoto; un listado que falla ⇒ `REMOTE_LISTING_FAILED` sin tocar nada |
| Idempotencia | Una reimportación idéntica **no escribe nada** (ni local ni Spaces ni journal): `outcome=NO_CHANGE`. Si Spaces estaba atrasado, se repara; si no se puede confirmar, se rechaza |
| Trazabilidad | Bitácora: versión activa con operación **previa**, raíz/Space, objetos, resumen del assessment (regenerados / reconstruidos / conservados / inciertos / retirados) + historial de 50 activaciones; evento `done` hacia la UI con `change` y `assessment` |
| Metodología y calibración intactas | 0 líneas de reglas, señales, Juicio ni calibración de confianza; `semantic_universe` es una extracción sin cambios de `assess()` |
| Reuso | CLI/exportador, catálogo local, `remote_store` (hold/persist_changed/delete_path/read_object_bytes), almacén gobernado y Spaces existentes. Sin sistema de persistencia nuevo ni procesamiento asíncrono |
| ASSESS/EPHEMERAL vs PUBLISH/CANONICAL | Sin cambios: el assessment ad-hoc no usa nada de esto; solo `mode=publish` con el interruptor encendido |

## 2. Granularidad de la vigencia (precisión 1 del dueño)

La vigencia comparaba la firma del pool **completo** del cliente: publicar un modelo dejaba `STALE` a todos los demás. Ahora cada sobre guarda la **firma de dependencias** de su AM
(`tools/catalog/modeling_assessment/dependency_signature.py`, regla `dep-closure/v1`): archivos de su clausura semántica (y la ausencia explícita de los que falten) + archivos que no son
entidades y **mencionan** a la clausura (flujos, tareas) + los que definen lo que esos mencionan + la **contención** (qué AM terminales contienen cada artefacto). Es una cota superior
conservadora: puede marcar de más, nunca de menos.

| Situación | Resultado |
|---|---|
| Afectado (contiene el modelo publicado, o cambió algo que lee, p. ej. una dimensión compartida) | **Regeneración obligatoria antes de publicar** |
| No afectado con independencia demostrada (firma antes == después) | **Conserva su vigencia** (`CURRENT`, `pool_drift`); su sobre no se toca byte a byte; si no traía la firma se le agrega (`rebased`) sin regenerar |
| Dependencias inciertas (sobre previo a esta firma, regla distinta, archivo ilegible, nombre de archivo no único) | **`STALE` explícito** (`stale_dependency=UNCERTAIN`) y trazable en la bitácora; nunca se afirma independencia |
| Cambio de **versión** (metodología/evidencia) | Siempre `STALE` por versión; la independencia no lo enmascara |

La independencia se prueba contra el **motor real**: el perfil de un AM no afectado es idéntico (salvo las anclas a la firma del pool) antes y después de cambios ajenos (`tests/test_dependency_signature.py`,
`tests/webapp/test_assessment_dependency_currentness.py`). Los sobres existentes no traen la firma: siguen leyéndose como antes hasta que se regeneren o se reconstruyan en la siguiente publicación.

## 3. Confirmación durable antes de visibilidad (precisión 2 del dueño)

```
lock → recuperación previa → preflight remoto (restore, locales sin reconciliar, lectura, inventario)
→ ¿algo que hacer? no ⇒ NO_CHANGE
→ journal PLANNED → COMPUERTA CERRADA → estado previo del assessment → undo (pool, manifiestos, Catálogo, bitácora, assessments del cliente) → APPLY
→ Catálogo del cliente → assessment: PREPARAR en memoria (motor, validación) → ESCRIBIR en local → VERIFICAR → bitácora (marcador de activación)
→ journal COMMIT_UNCERTAIN (intención) → CONFIRMAR en Spaces: borrar lo retirado → cuerpo → marcadores AL FINAL → verificar por bytes
   CONFIRMED  ⇒ REMOTE_CONFIRMED, undo descartado, cachés fuera, COMPUERTA ABIERTA
   FAILED     ⇒ (se leyó Spaces y NO refleja la versión) restaurar local desde el undo y reconciliar Spaces con la previa ⇒ ROLLED_BACK / PERSISTENCE_FAILED
   UNCERTAIN  ⇒ (no se pudo leer/verificar) NI se confirma NI se deshace: COMPUERTA CERRADA (UNCERTAIN), RECOVERY_REQUIRED / PERSISTENCE_UNCERTAIN
```

* `COMMITTED_UNCONFIRMED` ya **no** es un resultado funcional (no hay «publicado sin confirmar»): es solo el estado interno `COMMIT_UNCERTAIN` del journal.
* **Respuestas perdidas**: antes de revertir o declarar fallo se **lee** el remoto (bytes exactos). Una escritura efectiva con respuesta perdida cuenta como confirmada (`test_a_lost_response_*`).
* **Recuperación** (`recover_pending`, al inicio de cada publicación, al arrancar tras el restore y desde cada lector bloqueado, como mucho cada 30 s): hacia adelante (el local es la versión nueva); si Spaces no puede reflejarla,
  hacia atrás (restaura el undo y reconcilia Spaces con la previa). `REMOTE_DIVERGED`: el local ya es la versión previa (lectores liberados) pero Spaces todavía no; no se publica nada nuevo hasta resolverlo.
* **Lectores**: `core/publication_gate.py` (archivo hermano de `catalog_local`, visible para todos los procesos que comparten disco). `pool_root()` del assessment y las lecturas **en frío** del Catálogo esperan (≤15 s) o responden **503 + Retry-After**;
  con la caché caliente se sirve la última versión **confirmada** (la publicación invalida la caché recién cuando la nueva está confirmada). `UNCERTAIN` bloquea sin esperar. El hilo que publica no se bloquea a sí mismo.

### 3.1 Recuperación con Spaces parcialmente actualizado y el disco perdido (B1)

El journal y el undo viven en el disco local: un redeploy en mitad de la confirmación los pierde, y Spaces queda con parte de la versión nueva y parte de la previa. **Evidencia mínima que se agregó, dentro de lo que ya se persiste**
(`catalog_local/.publication_state/<cliente>.intent/`, sin otro sistema de persistencia; `tools/catalog/publication_intent.py`):

* `intent.json`: por cada archivo que **cambia** (o se retira) su digest PREVIO y su digest OBJETIVO, qué archivos son marcadores de activación, operación y operación previa.
* `pre/<sha256>`: los bytes PREVIOS de lo que cambia o se retira (la imagen previa: sin ella no hay forma de volver atrás una vez sobrescrito el objeto).
* `post/<sha256>`: los bytes OBJETIVO de los marcadores (para completar hacia adelante cuando el cuerpo quedó completo y solo faltó el marcador).

**Orden**: imágenes previas → verificadas → `intent.json` → verificado → recién entonces se escribe el cuerpo → marcadores al final → al confirmar, `intent.json` se retira primero. Sin `intent.json` el cuerpo remoto no se tocó. Una reimportación idéntica no registra nada.

**Cómo se resuelve** (`publication_intent.resolve`, nunca asume: lee Spaces objeto por objeto y clasifica cada uno como *previo*, *objetivo* u *otro*):

| Estado verificado de Spaces | Resultado |
|---|---|
| Todo el cuerpo es la versión **nueva** (el marcador puede ser antiguo, nuevo, ausente o ilegible) | se completa hacia adelante: se reescriben los marcadores desde `post/…` y la versión nueva es la vigente |
| Cualquier otra mezcla | se vuelve a la versión **previa**, en local y en Spaces, desde `pre/…` (un objeto que ya es el previo es su propia imagen); lo que la versión nueva creó se borra |
| No se puede leer/listar Spaces, o el restore quedó incompleto | **bloqueo de lectura** (`UNCERTAIN`) con código explícito (`REMOTE_UNREADABLE`, `RESTORE_INCOMPLETE`); no se modifica nada |
| Un objeto no es ni el previo ni el nuevo · falta una imagen previa necesaria · el registro está ilegible | **bloqueo de lectura** (`BLOCKED`) con código explícito (`REMOTE_UNEXPECTED_STATE`, `PRE_IMAGE_MISSING`, `INTENT_UNREADABLE`): no se adivina |

* **Disco conservado**: manda el journal (el local es la versión nueva completa): se completa hacia adelante verificando, y si Spaces no puede reflejarla se vuelve atrás desde el undo; el registro se cierra al terminar.
* **Disco perdido**: manda el registro, que `restore()` ya trajo junto con el resto. Idempotente: cada paso lee antes de escribir, de modo que otra interrupción (incluida otra pérdida de disco) converge en la pasada siguiente.
* **Compuerta de arranque** (`DANWOR_PUBLICATION_SAFE` + persistencia remota): `begin_boot_gate` cierra la lectura **antes de servir** y solo se abre cuando el restore terminó completo y la recuperación verificó que lo restaurado es UNA versión publicada. Un restore fallido se reintenta en segundo plano sin bloquear al lector.
* **Sync periódico**: `remote_store.set_mirror_guard` impide que `mirror_up` (incluido el flush final) suba el resto de una versión a medias tras un reinicio y la dé por completa sin verificar.
* **Defecto corregido de paso**: el marcador de activación figuraba como «huérfano» remoto y se borraba de Spaces justo antes de volver a subirlo (ventana sin marcador); ahora es parte de la unidad.

**Limitación explícita**: la imagen previa se guarda *desde* esta versión del código; una interrupción de una publicación hecha con una versión anterior a B1 no tiene registro (en ese caso solo hay detección: `verify_ledgers`). Costo: los archivos que cambian se escriben dos veces (imagen previa + cuerpo) y se borran al confirmar.

### 3.2 Persistencia obligatoria del assessment (B2)

Con `DANWOR_PUBLICATION_SAFE` encendido y un almacén durable exigido por la etapa (los assessments gobernados), la publicación **aborta antes de conectar con Datasphere y antes de tocar nada** (`FAILED_NO_CHANGE`, `stages={"persistence":"FAILED"}`) si la
persistencia **efectiva** no lo cubre (`publication_remote.persistence_gap`, revalidado bajo el candado):

| Código | Causa |
|---|---|
| `PERSISTENCE_MODE_NOT_REMOTE` | hay credenciales de Spaces pero `DANWOR_PERSISTENCE` no es `remote` (lo publicado se perdería en el próximo reinicio) |
| `PERSISTENCE_NOT_WRITABLE` | modo `remote` sin escritura habilitada (credenciales incompletas) |
| `PERSISTENCE_NOT_REQUESTED` | la confirmación remota (`DANWOR_PUBLICATION_MIRROR_UP`) no está activada |
| `CATALOG_NOT_PERSISTED` | el directorio del Catálogo no está entre los persistidos |
| `ASSESSMENT_NOT_PERSISTED` | `governed-assessments` no figura en `SPACES_PERSIST_PATHS`, o el directorio al que el almacén escribe de verdad no está dentro del persistido (el nombre en la lista no alcanza) |
| `ASSESSMENT_STORE_UNAVAILABLE` | el almacén no tiene directorio válido (p. ej. cae bajo el Catálogo) |

Sin credenciales (desarrollo local) el disco es la fuente de verdad y no se exige nada. Una publicación sin etapa de assessment (CLI) no tiene este requisito.

## 4. H2 / 0B — borrados en Spaces

* Lo que la operación retiró (`UndoLog.removed_paths`) y los **huérfanos remotos del propio cliente** (clave remota bajo sus prefijos que ya no existe en local, solo con restore completo y sin locales sin reconciliar) se borran de Spaces **antes** de subir lo nuevo.
  Tope de seguridad: 500 borrados por publicación. Nunca se tocan prefijos de otro cliente ni `governed-decisions`, `migration-backups`, etc.
* `remote_store.delete_path` también olvida el digest confirmado (si no, un `persist_changed` posterior daba por subido lo que ya no estaba).

## 5. Mapa de pruebas (resultado en `docs/REPUBLICATION_P1_EVIDENCE.md`)

| Familia pedida por el dueño | Archivo |
|---|---|
| Primera publicación · cambio real con dependencias · identica · regeneración y retiro de referencias obsoletas · aceptación final | `tests/test_republication_assessment.py` |
| Error de extracción / generación / listado / escritura / activación / confirmación | `tests/test_publication_safe_phase0.py` (barrido de 25 fallos), `tests/test_republication_assessment.py`, `tests/test_republication_unit.py` |
| Concurrencia de dos republicaciones del mismo alcance | `tests/test_republication_assessment.py`, `tests/test_publication_safe_phase0.py` |
| Recuperación tras interrupción | `tests/test_republication_assessment.py` (6 puntos), `tests/test_publication_safe_phase0.py`, `tests/test_republication_unit.py` |
| Aislamiento entre clientes y modelos | `tests/test_republication_assessment.py`, `tests/test_dependency_signature.py`, `tests/test_republication_identity.py` |
| Respuestas perdidas · listados que fallan · operaciones remotas ambiguas | `tests/test_republication_unit.py` (+ `FakeS3` con `lose_response_keys`, `fail_get_keys`, `fail_head_keys`, `fail_list`, `down`) |
| Lectores durante activación/recuperación | `tests/test_publication_gate.py`, `tests/test_republication_readers.py`, `tests/test_republication_assessment.py` |
| **B1** interrupción antes/después de cada etapa de la confirmación × disco conservado/perdido · marcador antiguo/nuevo/ausente/ilegible · segunda interrupción durante la recuperación · fallos de listado y lectura · límites sin evidencia | `tests/test_republication_recovery.py` (`FakeS3` con `crash_before_keys` / `crash_after_keys`) |
| **B2** persistencia obligatoria del assessment (configuración efectiva) | `tests/test_publication_persistence_coverage.py` |

## 6. Riesgos residuales

1. **Réplica única** (precondición vigente de la fase 0): el candado y la compuerta no protegen entre instancias con discos distintos.
2. **FS efímero + caída del proceso durante la confirmación remota** (B1, cerrado): el registro de intención en Spaces permite completar la versión nueva (si su cuerpo quedó completo) o volver a la previa; el arranque bloquea la lectura hasta verificarlo. Quedan **tres casos sin evidencia** que NO se resuelven solos y bloquean con código explícito: una imagen previa perdida (`PRE_IMAGE_MISSING`), un objeto modificado por un tercero durante la ventana (`REMOTE_UNEXPECTED_STATE`) y un registro ilegible (`INTENT_UNREADABLE`); y las publicaciones hechas con una versión del código anterior a B1 no tienen registro (solo detección por `verify_ledgers`).
3. **Bloqueo de lectura por Spaces caído justo al confirmar o al arrancar**: `UNCERTAIN` bloquea los lectores del pool/Catálogo en frío hasta que la recuperación pueda verificar Spaces (reintenta cada 30 s; un restore fallido se reintenta en segundo plano). Es la elección explícita del dueño (bloquear antes que mostrar una versión sin confirmar). Con el interruptor encendido, **cada arranque** mantiene la lectura cerrada hasta terminar el restore (puede ser de decenas de segundos).
4. **Duración de la solicitud**: preparar (motor por AM afectado) + confirmar en una sola solicitud puede durar más que la publicación anterior; no se agregó cola asíncrona (no hay evidencia de que haga falta). El tope de espera de lectores es 15 s (cada lector bloqueado ocupa un hilo del servidor).
5. **Vigencia por dependencias activa al desplegar** (no depende del interruptor): vale para los sobres escritos después; los previos siguen como antes (`UNCERTAIN` ⇒ `STALE`) hasta regenerarse o reconstruirse en una publicación.
6. **Cota conservadora de dependencias**: una mención textual cuenta aunque no sea referencia estructural (puede regenerar de más, nunca de menos). Una regla futura del motor que lea algo fuera de la clausura exige subir `DEPENDENCY_RULE_VERSION` (los sobres con otra versión pasan a «incierto»).
7. **Diagnóstico en tiempo de ejecución**: el generador del Catálogo es incremental; un archivo del Catálogo alterado a mano se detecta (la republicación se trata como cambio) pero el generador no necesariamente lo reescribe.
8. **Annex A (aislamiento físico por tenant/space) y migración de rutas**: fuera de alcance, sin cambios.
9. **Costo de la evidencia (B1)**: cada publicación con cambios escribe y luego borra en Spaces las imágenes previas de lo que cambia (doble escritura de los archivos modificados); un borrado de limpieza que falla deja el registro abierto (inocuo: se resuelve y cierra en la siguiente recuperación, pero bloquea nuevas publicaciones hasta entonces).
10. **Configuración obligatoria (B2)**: con el interruptor encendido hay que definir `DANWOR_PUBLICATION_MIRROR_UP=1` y tener `governed-assessments` en `SPACES_PERSIST_PATHS`; si no, **toda** publicación con assessment aborta con un código explícito.
