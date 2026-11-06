# Operación excepcional: recuperación del pool de Biogenesis (incidente 2026-11-06)

**Qué es.** Una publicación de `AM_ANALISIS_VENCIMIENTO` desde el Space CALIDAD, por el camino legacy previo al #275, sobrescribió 7 CSN compartidos del pool de Biogenesis y agregó 2 objetos exclusivos de CALIDAD y su manifiesto de linaje. Desde entonces, la regeneración y el assessment de Biogenesis fallan por `CatalogCollisionError` / `POOL_LOAD_ERROR`.

Esta carpeta restaura **exactamente** esos objetos al estado de la **última regeneración exitosa**. Los hashes fijos de `PLAN` son los de la firma `catalog_local/.catalog_manifest.json`.

**Qué no es.** No forma parte de la aplicación:
- **no** se importa desde el Hub;
- **no** corre al arrancar ni en tareas programadas;
- **no** cambia publicación, regeneración ni assessment.

Solo se ejecuta **a mano**, en **dry-run por defecto**.

| Archivo | Uso |
|---|---|
| `restore_biogenesis.py` | Restauración y reversión (Console del componente web) |
| `upload_evidence.py` | Carga controlada de la evidencia a Spaces (equipo del operador) |
| `tests/test_ops_recovery_biogenesis_20261106.py` | 32 pruebas con datos sintéticos (sin datos del cliente) |

**Evidencia:** los 7 CSN autoritativos **no están en el repositorio**. Se suben a Spaces con `upload_evidence.py`, a `quarantine/biogenesis-20261106-evidence/`. Ese prefijo es **privado** y está **fuera de `SPACES_PERSIST_PATHS`**, así que ni el restore ni el sincronizador lo tocan. `restore_biogenesis.py` valida su sha256 contra los hashes fijos y contra la firma vigente **antes** de usarla.

## Garantías
- **Preflight** por hashes (estado local y remoto), antes y **otra vez bajo el candado**. Aborta ante cualquier hash o estado desconocido, disco distinto de Spaces, journals o intenciones pendientes, compuerta cerrada, restore incompleto o falta de persistencia remota.
- **Cuarentena verificable antes de sobrescribir:** bytes previos de los 10 objetos, manifiesto incluido, en `quarantine/biogenesis-20261106-calidad/` y en disco, con `manifest.json` y sha256.
- **Unidad transaccional:** reutiliza la de la publicación (`hold_publication`, candado de escritor entre procesos, compuerta de lectores, journal con deshacer, **registro de intención en Spaces**, `converge` verificado).
- **Caídas:** las resuelve la recuperación existente (`recover_pending`, al arrancar o desde un lector bloqueado), completando o deshaciendo según lo que verifica en Spaces.
- **Estado mixto** (cada objeto previo o restaurado, sin desconocidos): `--apply` o `--rollback` lo llevan a un estado entero.
- **Journal anómalo** (por ejemplo, uno que sobrevivió con el catálogo reconstruido desde Spaces): el script lo **detecta y se detiene sin escribir**, y el procedimiento exige un diagnóstico específico (ver «Journal anómalo: detenerse»).

## Hitos y autorizaciones (independientes)

Son tres hitos separados. **Cada uno exige su propia autorización expresa del owner.** Ninguna autorización se hereda de la anterior.

| Hito | Qué hace | Escrituras | Autorización |
|---|---|---|---|
| **H1. Despliegue del script** | El merge del PR deja el código en el contenedor | **Ninguna** en Spaces ni en el Catálogo | La del merge, que **no** habilita H2 ni H3 |
| **H2. Carga privada de la evidencia** | Sube los 7 CSN autoritativos a `quarantine/biogenesis-20261106-evidence/` | Solo ese prefijo privado. El Catálogo **no** se modifica | **Expresa**, aunque no toque el Catálogo |
| **H3. Restauración de los 10 objetos** | Restaura 7 objetos y pone 3 en cuarentena en `catalog_local` y Spaces | Pool de Biogenesis y prefijo de cuarentena | **Segunda autorización expresa**, solo después de revisar el dry-run productivo |

El dry-run de restauración necesita la evidencia en Spaces. Por eso solo se puede correr después de H2.

## Procedimiento

### H1. Despliegue del script (sin escrituras)
Verificarlo desde la Console del componente web, en la raíz de la app:
```bash
ls ops/recovery/biogenesis_20261106 && python ops/recovery/biogenesis_20261106/restore_biogenesis.py --help
```
Si el directorio no existe en el contenedor, **no continuar**. H1 termina acá.

### H2. Carga privada de la evidencia (requiere autorización expresa)
Se ejecuta desde el equipo del operador, con las credenciales de Spaces.

1. Dry-run, que no escribe nada:
   ```bash
   python ops/recovery/biogenesis_20261106/upload_evidence.py --evidence-dir <carpeta_evidencia>
   ```
2. Carga, **solo con la autorización de H2**:
   ```bash
   python ops/recovery/biogenesis_20261106/upload_evidence.py --evidence-dir <carpeta_evidencia> --apply --confirm SUBIR-EVIDENCIA-BIOGENESIS-20261106
   ```

Cada objeto queda verificado por sha256 y **privado**: se comprueba que no tenga concesiones públicas. H2 termina acá; el Catálogo sigue sin cambios.

### H3. Restauración (requiere una segunda autorización expresa)
1. **Precondiciones:**
   - publicaciones y «Generar catálogo» suspendidos;
   - sin deploy en curso;
   - réplica única;
   - la Console muestra `remote True True` con `python -c "import config; print(config.PERSISTENCE_MODE, config.SPACES_CONFIGURED, config.PERSIST_ENABLED)"`.
2. **Dry-run productivo** (Console; no escribe nada):
   ```bash
   python ops/recovery/biogenesis_20261106/restore_biogenesis.py
   ```
   Tiene que mostrar `PREFLIGHT OK`, estado `OLD`, 7 restauraciones y 3 retiros a cuarentena. **La salida se comparte con el owner**, y la autorización de H3 se da sobre ese resultado.
3. **Ejecución** (Console), **solo con la autorización de H3**:
   ```bash
   python ops/recovery/biogenesis_20261106/restore_biogenesis.py --apply --confirm RESTAURAR-BIOGENESIS-20261106
   ```
   Tiene que terminar en `RESULTADO: COMPLETED` y la `VERIFICACIÓN` con estado `NEW`.
4. **Verificación posterior:**
   - dry-run con estado `NEW`;
   - en una **copia** del pool: `generate_all(only_clients={"Biogenesis"})` sin colisión, catálogo idéntico al publicado y los 25 assessments evaluables sin cambio de nivel;
   - ETags de los demás clientes iguales a los del inventario previo.
5. Reabrir publicaciones, con autorización del owner.

## Recuperación ante fallos

| Resultado | Significado | Acción |
|---|---|---|
| `ABORT: …` (rc=1) | Preflight fallido; nada modificado (como mucho la cuarentena, que es inocua) | Investigar; no forzar |
| `ROLLED_BACK` (rc=2) | Spaces rechazó una escritura; disco y Spaces volvieron al estado previo y se verificaron | Reintentar cuando Spaces responda |
| `RECOVERY_REQUIRED` (rc=2) | No se pudo verificar Spaces; la compuerta queda cerrada (`UNCERTAIN`) y los lectores ven 503 | Lo resuelve solo el mecanismo existente (lector bloqueado cada 30 s, o al arrancar). Después, dry-run. Si no se resuelve o el dry-run no da `OLD`, `NEW` o `MIXED`: **detenerse** (ver «Journal anómalo») |
| Proceso muerto o reinicio | Journal o intención pendientes | `boot_recover` completa o deshace verificando Spaces. Después, dry-run: el estado tiene que ser `OLD` o `NEW` entero (o `MIXED`, que `--apply` o `--rollback` resuelven, con la autorización de H3). Cualquier otro resultado: **detenerse** |
| Revertir una restauración completada | — | `--rollback --confirm REVERTIR-BIOGENESIS-20261106` (desde la cuarentena remota, con verificación de hashes) |

### Journal anómalo: detenerse

Aplica ante **cualquier inconsistencia entre el journal, el estado local y Spaces**. Por ejemplo:
- un journal `biogenesis-restore-*` que sobrevivió con `catalog_local` reconstruido desde Spaces;
- un estado `OTHER`;
- un disco distinto de Spaces que la recuperación existente no resolvió.

En todos esos casos el procedimiento **se detiene**. El script aborta con `DETENIDO` y no escribe nada. A partir de ahí:
- **no** se modifica, mueve ni borra ningún journal, intención, objeto local ni objeto remoto;
- **no** se ejecuta `recover_pending`, `--apply` ni `--rollback`;
- **se conserva toda la evidencia tal como está:**
  - `.publication_runtime/` (journals, undo e intenciones locales);
  - `catalog_local`;
  - los registros de intención en Spaces;
  - `.recovery_quarantine/` y la cuarentena remota;
  - la salida completa del script;
- se exige un **diagnóstico específico** del caso. Cualquier modificación posterior necesita una autorización nueva del owner, basada en ese diagnóstico.

No hay una receta operativa general para este escenario.

En DigitalOcean, `catalog_local` y `.publication_runtime` comparten el mismo disco efímero y se pierden juntos, así que el caso del journal superviviente es teórico. El script igual lo detecta y se detiene.

## Ciclo de vida y retiro (después de verificar la recuperación)
1. **Registro operativo que se conserva** (en `docs/operations/`, sin datos del cliente): fecha y operador, `operation_id`, salida del dry-run y del `--apply`, verificación posterior, hashes de `PLAN` y `manifest.json` de la cuarentena.
2. **Retirar el código:** un PR que borre `ops/recovery/biogenesis_20261106/` y su test. Mientras exista no hace nada si no se lo invoca.
3. **Retirar la evidencia temporal de Spaces:** borrar `quarantine/biogenesis-20261106-evidence/` cuando la recuperación esté verificada.
4. **Retirar la cuarentena:** conservar `quarantine/biogenesis-20261106-calidad/` (versiones de CALIDAD) durante el período de retención que defina el owner; es lo único que permite `--rollback`. Después, borrarla.
5. **Limpiar en disco:** `.recovery_quarantine/` se pierde solo con cada redeploy; no hace falta acción.
