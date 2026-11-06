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
- **Journal superviviente de una restauración con el catálogo reconstruido desde Spaces:** el script lo **detecta y se detiene sin escribir** (ver más abajo).

## Procedimiento
**0. Verificar el despliegue** (Console del componente web, en la raíz de la app):
```bash
ls ops/recovery/biogenesis_20261106 && python ops/recovery/biogenesis_20261106/restore_biogenesis.py --help
```
Si el directorio no existe en el contenedor, **no continuar**.

**1. Precondiciones:**
- publicaciones y «Generar catálogo» suspendidos;
- sin deploy en curso;
- réplica única;
- la Console muestra `remote True True` con `python -c "import config; print(config.PERSISTENCE_MODE, config.SPACES_CONFIGURED, config.PERSIST_ENABLED)"`.

**2. Cargar la evidencia** (equipo del operador, con las credenciales de Spaces; se ejecuta solo con autorización):
```bash
python ops/recovery/biogenesis_20261106/upload_evidence.py --evidence-dir <carpeta_evidencia>
```
```bash
python ops/recovery/biogenesis_20261106/upload_evidence.py --evidence-dir <carpeta_evidencia> --apply --confirm SUBIR-EVIDENCIA-BIOGENESIS-20261106
```
El primero es el dry-run y el segundo la carga. Cada objeto queda verificado por sha256 y **privado**: se comprueba que no tenga concesiones públicas.

**3. Dry-run** (Console):
```bash
python ops/recovery/biogenesis_20261106/restore_biogenesis.py
```
Tiene que mostrar `PREFLIGHT OK`, estado `OLD`, 7 restauraciones y 3 retiros a cuarentena.

**4. Ejecutar** (Console, solo con autorización):
```bash
python ops/recovery/biogenesis_20261106/restore_biogenesis.py --apply --confirm RESTAURAR-BIOGENESIS-20261106
```
Tiene que terminar en `RESULTADO: COMPLETED` y la `VERIFICACIÓN` con estado `NEW`.

**5. Verificar después:**
- dry-run con estado `NEW`;
- en una **copia** del pool: `generate_all(only_clients={"Biogenesis"})` sin colisión, catálogo idéntico al publicado y los 25 assessments evaluables sin cambio de nivel;
- ETags de los demás clientes iguales a los del inventario previo.

**6.** Reabrir publicaciones.

## Recuperación ante fallos

| Resultado | Significado | Acción |
|---|---|---|
| `ABORT: …` (rc=1) | Preflight fallido; nada modificado (como mucho la cuarentena, que es inocua) | Investigar; no forzar |
| `ROLLED_BACK` (rc=2) | Spaces rechazó una escritura; disco y Spaces volvieron al estado previo y se verificaron | Reintentar cuando Spaces responda |
| `RECOVERY_REQUIRED` (rc=2) | No se pudo verificar Spaces; la compuerta queda cerrada (`UNCERTAIN`) y los lectores ven 503 | Se resuelve solo (lector bloqueado, cada 30 s, o al arrancar). Para forzarlo: `python -c "from tools.catalog import publication_txn as t; print(t.recover_pending('<catalog_local>'))"`. Después, dry-run |
| Proceso muerto o reinicio | Journal o intención pendientes | `boot_recover` completa o deshace verificando Spaces. Después, dry-run: el estado tiene que ser `OLD` o `NEW` entero (o `MIXED`, que `--apply` o `--rollback` resuelven) |
| Revertir una restauración completada | — | `--rollback --confirm REVERTIR-BIOGENESIS-20261106` (desde la cuarentena remota, con verificación de hashes) |

**Journal superviviente.** Si se perdiera `catalog_local` (reconstruido desde Spaces) pero sobreviviera el journal de una restauración en `.publication_runtime/`, el script **aborta** y no escribe nada. En ese caso **no** hay que forzar `recover_pending` a ciegas: con el disco reconstruido, completar hacia adelante podría retirar objetos sin restaurar los demás. Hay que escalar:
1. Inspeccionar el journal: `mode` `biogenesis-restore-*` y su estado.
2. Mover el journal a un costado, como evidencia.
3. Correr el dry-run, que va a mostrar `OLD`, `NEW` o `MIXED`.
4. Converger con `--apply` o `--rollback`.

En DigitalOcean `catalog_local` y `.publication_runtime` son hermanos en el mismo disco efímero y se pierden juntos, así que este escenario es teórico.

## Ciclo de vida y retiro (después de verificar la recuperación)
1. **Registro operativo que se conserva** (en `docs/operations/`, sin datos del cliente): fecha y operador, `operation_id`, salida del dry-run y del `--apply`, verificación posterior, hashes de `PLAN` y `manifest.json` de la cuarentena.
2. **Retirar el código:** un PR que borre `ops/recovery/biogenesis_20261106/` y su test. Mientras exista no hace nada si no se lo invoca.
3. **Retirar la evidencia temporal de Spaces:** borrar `quarantine/biogenesis-20261106-evidence/` cuando la recuperación esté verificada.
4. **Retirar la cuarentena:** conservar `quarantine/biogenesis-20261106-calidad/` (versiones de CALIDAD) durante el período de retención que defina el owner; es lo único que permite `--rollback`. Después, borrarla.
5. **Limpiar en disco:** `.recovery_quarantine/` se pierde solo con cada redeploy; no hace falta acción.
