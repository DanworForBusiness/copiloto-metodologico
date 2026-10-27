# Publicación segura del Catálogo (fase 0 + P1) — activación, desactivación y rollback

Interruptor: **`DANWOR_PUBLICATION_SAFE`** — apagado por defecto. Alcance: solo el modo **PUBLISH** (el Assessment ad-hoc no cambia).
Diseño y causas: `docs/audit/CATALOG_REPUBLICATION_AUDIT_20261026.md` y `docs/audit/CATALOG_REPUBLICATION_PHASE0_DESIGN_20261026.md` (rama de auditoría). **P1** (assessment dentro de la unidad, confirmación durable antes de visibilidad, lectores protegidos, idempotencia, borrados remotos): `docs/REPUBLICATION_P1.md`.

## 1. Qué cambia cuando está ENCENDIDO

| Garantía | Comportamiento |
|---|---|
| **H1** extracción incompleta | Cualquier fallo de lectura o de listado (o una violación de los invariantes de completitud) **aborta antes de tocar nada**: `FAILED_NO_CHANGE` / `EXTRACTION_INCOMPLETE`. No se limpia, no se regenera, no se sube, no se registra el Assessment |
| **H3** persistencia | La confirmación remota se **verifica** (`publication_remote.confirm`: cuerpo, marcador de activación al final, bytes exactos; una respuesta perdida tras una escritura efectiva se reconcilia leyendo el remoto) bajo `hold_publication`; **solo `remote` confirma**. Modo `local` (desarrollo) ⇒ `local_only_by_config`. **P1:** si Spaces no confirma, la versión nueva **no queda vigente**: se restaura la última confirmada (`ROLLED_BACK`/`PERSISTENCE_FAILED`) o, si no se puede verificar, se bloquea la lectura (`RECOVERY_REQUIRED`/`PERSISTENCE_UNCERTAIN`) hasta resolverlo |
| **H5** concurrencia | Candado de **escritor único entre procesos** (`flock`/`msvcrt`) sobre `catalog_local`, tomado por la publicación, «Generar catálogo» y APIS_ONLY. Ocupado ⇒ `CATALOG_BUSY` sin efectos |
| **H6** manifiestos | Escrituras atómicas; manifiesto v3 con integridad y `sha256` por objeto; carga tri-estado: **un manifiesto ilegible nunca autoriza un borrado** (limpieza diferida ⇒ **PARTIAL**, nunca `COMPLETED`) |
| Transaccionalidad | journal + copia de deshacer: ante cualquier falla se restaura el estado previo (`ROLLED_BACK`); una caída del proceso se recupera al inicio de la siguiente operación |
| Revalidación | Los manifiestos y las precondiciones se calculan **bajo el candado**, no antes |
| Contracción | Una caída de más del 50 % de los objetos se **observa** (línea `OBSERVACION` y `observations.contraction`); no bloquea |

`SUCCESS` en la UI ⇔ `status == COMPLETED`. `PARTIAL` (solo `COMMITTED_CLEANUP_DEFERRED`) se muestra como «Publicación parcial» (aviso, botón rehabilitado para **reintentar**: es idempotente). Una reimportación idéntica termina `COMPLETED` con `outcome=NO_CHANGE` y no escribe nada.

Estados funcionales: `COMPLETED` · `FAILED_NO_CHANGE` · `ROLLED_BACK` · `COMMITTED_CLEANUP_DEFERRED` · `RECOVERY_REQUIRED`. `COMMITTED_UNCONFIRMED` es **solo** el estado interno `COMMIT_UNCERTAIN` del journal (no un resultado vigente).

## 2. Activar (sin estados mixtos)

Precondiciones (todas):
1. **Réplica única** de App Platform (`instance_count = 1`) — ver §5. El candado de SO **no** protege entre instancias.
2. `DANWOR_PERSISTENCE=remote` ya vigente en producción (gate de despliegue existente).
3. Ninguna publicación ni «Generar catálogo» en curso (el panel de Catálogo no muestra operación activa) y **sin journals pendientes** (§4).

Pasos:
1. Desplegar el código con el interruptor **apagado** (por defecto). Verificar que una publicación de prueba sigue comportándose igual.
2. Definir `DANWOR_PUBLICATION_SAFE=1` en el entorno y reiniciar la app. El interruptor se lee **una vez por operación**: la operación en curso nunca cambia de camino a mitad.
3. Publicar un modelo de prueba (entorno aislado primero) y comprobar: `status=COMPLETED`, `durability=remote_confirmed`, línea «Persistencia canónica: OK», un journal `REMOTE_CONFIRMED` en `<dirname(DANWOR_CATALOG_DIR)>/.publication_runtime/journal/`.
4. Republicar el mismo modelo: debe terminar `COMPLETED` sin reescribir ni subir archivos (`applied.changed=false`).

## 3. Desactivar / rollback (sin estados mixtos)

1. Quitar `DANWOR_PUBLICATION_SAFE` (o `=0`) y reiniciar. **No hay que migrar nada**: el manifiesto v3 es un superconjunto del v2 y el código anterior lo lee; el directorio `.publication_runtime/` está fuera del árbol que lee el código anterior.
2. **La recuperación corre SIEMPRE** (con el interruptor encendido o apagado) al inicio de cada publicación: si el proceso murió a mitad de un apply, el journal `APPLYING` se restaura desde la copia de deshacer **antes** de publicar por el camino anterior. Apagar el interruptor después de una caída no deja estados mixtos (prueba: `test_turning_the_switch_off_after_a_crash_never_leaves_a_mixed_state`).
3. Si una recuperación no puede verificarse, la publicación responde `RECOVERY_REQUIRED` con el interruptor encendido **o** apagado: no se publica encima de un estado a medias. Resolución: §4.
4. Revertir el commit equivale a (1); no deja datos huérfanos del lado de la app (los journals son inertes).
5. Lo que **no** se revierte solo: objetos que una publicación segura ya borró legítimamente (limpieza por manifiesto OK) — es el comportamiento correcto de una republicación; el respaldo está en el undo solo **durante** la operación.

## 4. Operación y diagnóstico

* Journals: `<dirname(DANWOR_CATALOG_DIR)>/.publication_runtime/journal/<operation_id>.json` (estado, objetos planificados, host). Undo: `…/undo/<operation_id>/` (solo mientras la operación no está comprometida).
* Pendiente = journal `PLANNED`/`APPLYING`, `COMMIT_UNCERTAIN` o `REMOTE_DIVERGED` (o ilegible). Se recupera solo en la siguiente publicación, al arrancar (tras el restore) y desde cualquier lector bloqueado (≤ cada 30 s); para forzarlo sin publicar:
  `python -c "from tools.catalog import publication_txn as t; print(t.recover_pending('<DANWOR_CATALOG_DIR>'))"`
* `RECOVERY_REQUIRED` persistente ⇒ la restauración no pudo verificarse (permiso, disco). **No borrar el journal a mano**; revisar el undo del `operation_id` y restaurar manualmente, luego recuperar de nuevo.
* `COMMITTED_CLEANUP_DEFERRED`: hay un manifiesto no utilizable (el propio previo, el de otra raíz del cliente) o el restore está incompleto. No se borró nada. Reparación: republicar la raíz cuyo manifiesto está dañado (la extracción lo reconstruye) y volver a publicar; lo pendiente figura en `pendingCleanup` del manifiesto y se limpia cuando el ownership vuelve a ser conocido. *Limitación:* si el manifiesto **propio** previo estaba ilegible, no se sabe qué quedó obsoleto: esos objetos pueden quedar huérfanos hasta la fase 0B.
* `PERSISTENCE_FAILED` (`ROLLED_BACK`): Spaces no aceptó la versión nueva; local y Spaces quedaron en la última versión confirmada. Reintentar cuando Spaces responda.
* `PERSISTENCE_UNCERTAIN` (`RECOVERY_REQUIRED`): no se pudo verificar Spaces; los lectores del pool/Catálogo en frío reciben **503** hasta que la recuperación confirme o deshaga. Compuerta: `<dirname(DANWOR_CATALOG_DIR)>/.publication_runtime/gate.json`. No borrar el archivo a mano.
* `PERSISTENCE_DIVERGED`: el local volvió a la versión previa (lectores liberados) pero Spaces todavía no; no se publica hasta que la compensación se complete (se reintenta sola).
* Lock: archivo `danwor-catalog-locks/catalog-writer.lock` en el directorio temporal del SO (`DANWOR_CATALOG_LOCK_DIR` lo redefine). Lo libera el SO si el proceso muere.

## 5. Limitaciones de DigitalOcean y de esta fase

* **Réplica única obligatoria (temporal).** `flock` protege procesos que comparten el disco; **no** instancias con discos distintos. Riesgos residuales: (i) solapamiento instancia vieja/nueva durante un deploy; (ii) un futuro escalado horizontal. Mitigación parcial: `hostId`/`operationId` quedan en el manifiesto y el journal.
* **FS efímero:** un redeploy pierde journal y undo **y** el estado local; el estado se reconstruye con `restore()` desde Spaces. Por eso la consistencia remota depende del **orden de subida** (marcador `.catalog_manifest.json` al final) y no del journal.
* **H2 (P1):** la publicación segura borra en Spaces lo que retira y los huérfanos del propio cliente (tope 500) antes de subir lo nuevo. `mirror_up`/`persist_changed` siguen sin propagar borrados por sí solos: los otros productores (APIS_ONLY, «Generar catálogo») no cambian.
* **Identidad:** el pool sigue siendo `(cliente, tipo, nombre)`. La guardia `POOL_IDENTITY_CONFLICT` evita pisar un objeto de otro Space/tenant con contenido distinto; **no** es el aislamiento físico (fase posterior).
* Los manifiestos corruptos de otras raíces **no** se copian a cuarentena en esta fase: se dejan intactos y se informan.
* Spaces no documenta escritura condicional: el create-only de otros espacios es check-then-PUT; esta fase no depende de ello.
* La exclusión entre procesos usa el temporal del SO: si `TMPDIR` difiere entre procesos que comparten el catálogo, usar `DANWOR_CATALOG_LOCK_DIR` común.
