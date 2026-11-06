# Publicación del Catálogo (fase 0 + P1) — precondiciones, despliegue y operación

**Único camino.** Desde el hotfix P0 (incidente Biogenesis, 2026-11-06) la publicación transaccional es el ÚNICO camino del modo **PUBLISH**: se retiraron el
interruptor `DANWOR_PUBLICATION_SAFE`, la variable `DANWOR_PUBLICATION_MIRROR_UP` y el flag `--mirror-up` de la CLI, junto con el camino legacy (escritura directa
sobre el pool sin deshacer, regeneración global, espejo sin guarda). La integridad no es configurable. El Assessment ad-hoc no cambia.
Diseño y causas: `docs/audit/CATALOG_REPUBLICATION_AUDIT_20261026.md` y `docs/audit/CATALOG_REPUBLICATION_PHASE0_DESIGN_20261026.md` (rama de auditoría). **P1** (assessment dentro de la unidad, confirmación durable antes de visibilidad, lectores protegidos, idempotencia, borrados remotos): `docs/REPUBLICATION_P1.md`.

## 1. Garantías (siempre activas)

| Garantía | Comportamiento |
|---|---|
| **H1** extracción incompleta | Cualquier fallo de lectura o de listado (o una violación de los invariantes de completitud) **aborta antes de tocar nada**: `FAILED_NO_CHANGE` / `EXTRACTION_INCOMPLETE`. No se limpia, no se regenera, no se sube, no se registra el Assessment |
| **H3** persistencia | La decide **`DANWOR_PERSISTENCE`** (no hay otra variable): `remote` ⇒ la confirmación en Spaces es **obligatoria** y se **verifica** (`publication_remote.confirm`: cuerpo, marcador de activación al final, bytes exactos; una respuesta perdida tras una escritura efectiva se reconcilia leyendo el remoto) bajo `hold_publication`; `local` (desarrollo) ⇒ la misma unidad transaccional en disco, `local_only_by_config`; `read-only` ⇒ no se puede confirmar: se rechaza sin tocar nada. **P1:** si Spaces no confirma, la versión nueva **no queda vigente**: se restaura la última confirmada (`ROLLED_BACK`/`PERSISTENCE_FAILED`) o, si no se puede verificar, se bloquea la lectura (`RECOVERY_REQUIRED`/`PERSISTENCE_UNCERTAIN`) hasta resolverlo |
| **H5** concurrencia | Candado de **escritor único entre procesos** (`flock`/`msvcrt`) sobre `catalog_local`, tomado por la publicación, «Generar catálogo» y APIS_ONLY. Ocupado ⇒ `CATALOG_BUSY` sin efectos |
| **H6** manifiestos | Escrituras atómicas; manifiesto v3 con integridad y `sha256` por objeto; carga tri-estado: **un manifiesto ilegible nunca autoriza un borrado** (limpieza diferida ⇒ **PARTIAL**, nunca `COMPLETED`) |
| Transaccionalidad | journal + copia de deshacer: ante cualquier falla se restaura el estado previo (`ROLLED_BACK`); una caída del proceso se recupera al inicio de la siguiente operación |
| Revalidación | Los manifiestos y las precondiciones se calculan **bajo el candado**, no antes |
| Contracción | Una caída de más del 50 % de los objetos se **observa** (línea `OBSERVACION` y `observations.contraction`); no bloquea |
| Sincronizador periódico | `mirror_up` consulta SIEMPRE la guarda (`datasphere_publication_service._mirror_guard`): no sube mientras haya una publicación activándose, una verificación de arranque o un journal sin resolver. La guarda lee el estado del DISCO (compuerta y journals), así que vale entre procesos y no deja bloqueos permanentes (la recuperación los resuelve) |
| Regeneración | Por **cliente** (`only_clients`): un cliente inconsistente no bloquea ni toca la publicación de otro. La regeneración global queda como operación explícita de mantenimiento («Generar catálogo» en el panel del dueño) |

`SUCCESS` en la UI ⇔ `status == COMPLETED`. `PARTIAL` (solo `COMMITTED_CLEANUP_DEFERRED`) se muestra como «Publicación parcial» (aviso, botón rehabilitado para **reintentar**: es idempotente). Una reimportación idéntica termina `COMPLETED` con `outcome=NO_CHANGE` y no escribe nada.

Estados funcionales: `COMPLETED` · `FAILED_NO_CHANGE` · `ROLLED_BACK` · `COMMITTED_CLEANUP_DEFERRED` · `RECOVERY_REQUIRED`. `COMMITTED_UNCONFIRMED` es **solo** el estado interno `COMMIT_UNCERTAIN` del journal (no un resultado vigente).

## 2. Precondiciones de despliegue (configuración de infraestructura, sin variables nuevas)

Con `DANWOR_PERSISTENCE=remote` (producción) TODAS deben cumplirse; si alguna falta, la publicación **aborta antes de tocar nada** con un código explícito (fail-closed), el resto del Hub sigue funcionando:

| Precondición | Cómo se verifica | Si falta |
|---|---|---|
| `DANWOR_PERSISTENCE=remote` y credenciales `SPACES_*` completas | log de arranque `persistencia: DANWOR_PERSISTENCE=remote · credenciales Spaces presentes` | `PERSISTENCE_MODE_NOT_REMOTE` / `PERSISTENCE_NOT_WRITABLE` |
| `catalog_local` y `governed-assessments` en `SPACES_PERSIST_PATHS` (el **default del código ya los incluye**; solo hay que revisarlo si el entorno define la variable explícita) | App Platform → variables del componente | `CATALOG_NOT_PERSISTED` / `ASSESSMENT_NOT_PERSISTED` |
| **Réplica única** (`instance_count = 1`) | App Platform → componente web → Resources | sin detección automática: el candado de SO no protege entre instancias (§5) |
| Sin journals pendientes ni publicación en curso al desplegar | §4 (`recover_pending`) | la primera publicación responde `RECOVERY_REQUIRED` hasta resolverlo |

**Variables a RETIRAR del entorno** (ya no se leen; dejarlas no tiene efecto, pero confunden): `DANWOR_PUBLICATION_SAFE`, `DANWOR_PUBLICATION_MIRROR_UP`.

## 3. Orden de despliegue y rollback

**Despliegue**
1. Suspender publicaciones y «Generar catálogo» (aviso al equipo).
2. Verificar las precondiciones de §2 (sin cambiarlas salvo que falte algo).
3. Desplegar. Al arrancar con persistencia remota: la **compuerta de arranque** cierra la lectura del Catálogo hasta que el restore termine y la recuperación verifique lo
   restaurado (503 con `BOOT_VERIFICATION_PENDING` durante ese lapso); el sincronizador queda retenido mientras tanto. Es el comportamiento esperado.
4. Revisar el log del arranque: `verify_ledgers` no debe reportar clientes incoherentes (los que nunca publicaron por la unidad no tienen bitácora y no se reportan).
5. Publicar un modelo de prueba y comprobar `status=COMPLETED`, `durability=remote_confirmed` y un journal `REMOTE_CONFIRMED` en
   `<dirname(DANWOR_CATALOG_DIR)>/.publication_runtime/journal/`. Republicarlo: `COMPLETED` / `NO_CHANGE` sin subir nada.
6. Reabrir publicaciones. Un cliente con evidencia inconsistente en el pool (p. ej. Biogenesis) ya no bloquea a los demás; su propia publicación fallará con un
   código explícito hasta su recuperación (procedimiento aparte).

**Rollback**: revertir el commit y redeployar. El manifiesto v3 es un superconjunto del v2 y el directorio `.publication_runtime/` está fuera del árbol que lee el
código anterior: no hay migración. *Advertencia:* el código anterior vuelve al camino legacy (sin deshacer) mientras no se defina `DANWOR_PUBLICATION_SAFE=1`;
con el rollback hay que mantener suspendidas las publicaciones o definir esa variable.

## 4. Operación y diagnóstico

* Journals: `<dirname(DANWOR_CATALOG_DIR)>/.publication_runtime/journal/<operation_id>.json` (estado, objetos planificados, host). Undo: `…/undo/<operation_id>/` (solo mientras la operación no está comprometida).
* Pendiente = journal `PLANNED`/`APPLYING`, `COMMIT_UNCERTAIN` o `REMOTE_DIVERGED` (o ilegible). Se recupera solo en la siguiente publicación, al arrancar (tras el restore) y desde cualquier lector bloqueado (≤ cada 30 s); para forzarlo sin publicar:
  `python -c "from tools.catalog import publication_txn as t; print(t.recover_pending('<DANWOR_CATALOG_DIR>'))"`
* `RECOVERY_REQUIRED` persistente ⇒ la restauración no pudo verificarse (permiso, disco). **No borrar el journal a mano**; revisar el undo del `operation_id` y restaurar manualmente, luego recuperar de nuevo.
* `COMMITTED_CLEANUP_DEFERRED`: hay un manifiesto no utilizable (el propio previo, el de otra raíz del cliente) o el restore está incompleto. No se borró nada. Reparación: republicar la raíz cuyo manifiesto está dañado (la extracción lo reconstruye) y volver a publicar; lo pendiente figura en `pendingCleanup` del manifiesto y se limpia cuando el ownership vuelve a ser conocido. *Limitación:* si el manifiesto **propio** previo estaba ilegible, no se sabe qué quedó obsoleto: esos objetos pueden quedar huérfanos hasta la fase 0B.
* `PERSISTENCE_FAILED` (`ROLLED_BACK`): Spaces no aceptó la versión nueva; local y Spaces quedaron en la última versión confirmada. Reintentar cuando Spaces responda.
* `PERSISTENCE_UNCERTAIN` (`RECOVERY_REQUIRED`): no se pudo verificar Spaces; los lectores del pool/Catálogo en frío reciben **503** hasta que la recuperación confirme o deshaga. Compuerta: `<dirname(DANWOR_CATALOG_DIR)>/.publication_runtime/gate.json`. No borrar el archivo a mano.
* `PERSISTENCE_DIVERGED`: el local volvió a la versión previa (lectores liberados) pero Spaces todavía no; no se publica hasta que la compensación se complete (se reintenta sola).
* **Registro de intención (B1)**: `catalog_local/.publication_state/<cliente>.intent/` (local **y** en Spaces) existe solo mientras una confirmación no se resolvió. Si persiste, la publicación nueva responde `RECOVERY_REQUIRED`. Se resuelve solo (arranque, lector bloqueado, siguiente publicación); a mano: el mismo `recover_pending` de arriba. **No borrar sus archivos ni los de Spaces a mano.**
* **Lectura bloqueada tras un arranque** (503 con `código X`; el log del arranque lo registra con severidad de error): `BOOT_VERIFICATION_PENDING` (restore o verificación en curso, esperar) · `RESTORE_INCOMPLETE` (el restore falló o quedó incompleto: se reintenta solo; si persiste, revisar credenciales/Spaces) · `REMOTE_UNREADABLE` (Spaces no se puede leer: se reintenta cada 30 s) · `PRE_IMAGE_MISSING` / `REMOTE_UNEXPECTED_STATE` / `INTENT_UNREADABLE` (**no se resuelven solos**: falta evidencia o un tercero modificó Spaces; el estado de Spaces y del disco no se tocó; escalar al equipo técnico con el `operation_id` del log).
* Lock: archivo `danwor-catalog-locks/catalog-writer.lock` en el directorio temporal del SO (`DANWOR_CATALOG_LOCK_DIR` lo redefine). Lo libera el SO si el proceso muere.

## 5. Limitaciones de DigitalOcean y de esta fase

* **Réplica única obligatoria (temporal).** `flock` protege procesos que comparten el disco; **no** instancias con discos distintos. Riesgos residuales: (i) solapamiento instancia vieja/nueva durante un deploy; (ii) un futuro escalado horizontal. Mitigación parcial: `hostId`/`operationId` quedan en el manifiesto y el journal.
* **FS efímero:** un redeploy pierde journal y undo **y** el estado local; el estado se reconstruye con `restore()` desde Spaces. La consistencia remota depende del **orden de subida** (registro de intención antes del cuerpo, marcadores al final) y, **si el redeploy ocurrió a mitad de la confirmación, del registro de intención** (B1): con él se completa la versión nueva o se vuelve a la previa de forma verificada, y la lectura queda cerrada mientras tanto.
* **H2 (P1):** la publicación segura borra en Spaces lo que retira y los huérfanos del propio cliente (tope 500) antes de subir lo nuevo. `mirror_up`/`persist_changed` siguen sin propagar borrados por sí solos: los otros productores (APIS_ONLY, «Generar catálogo») no cambian.
* **Identidad:** el pool sigue siendo `(cliente, tipo, nombre)`. La guardia `POOL_IDENTITY_CONFLICT` evita pisar un objeto de otro Space/tenant con contenido distinto; **no** es el aislamiento físico (fase posterior).
* Los manifiestos corruptos de otras raíces **no** se copian a cuarentena en esta fase: se dejan intactos y se informan.
* Spaces no documenta escritura condicional: el create-only de otros espacios es check-then-PUT; esta fase no depende de ello.
* La exclusión entre procesos usa el temporal del SO: si `TMPDIR` difiere entre procesos que comparten el catálogo, usar `DANWOR_CATALOG_LOCK_DIR` común.
