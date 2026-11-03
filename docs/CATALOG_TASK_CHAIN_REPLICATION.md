# Task Chains de Remote Tables: estrategia de replicación declarada (fase 1)

## Qué se reconoce
- Desde la fase 1, el extractor (`tools/catalog/DSP_CLI_Export_Lineage.py`, `discover_task_chains`) lee las Task Chains del Space cuando el linaje tiene Replication/Data/Transformation Flows **o** Remote Tables. Antes las salteaba si no había flows.
- **Remote Tables:** cuenta solo un paso estructural `nodes[].taskIdentifier` con `applicationId = REMOTE_TABLES`, `activity = REPLICATE` y `objectId` igual exactamente al nombre técnico de una Remote Table del linaje.
  - El par se verificó en una definición real: Contreras DEV, `TC_SSF_Snapshot`, 23 pasos.
  - Un nombre que aparece en etiquetas u otros campos no crea la relación.
  - Otros pares, como `REMOVE_REPLICATED_DATA`, se registran como no reconocidos.
- **Replication/Data/Transformation Flows:** siguen con el criterio histórico, la coincidencia exacta del nombre en la definición, sin cambios.
- Se lee una vez por extracción: el inventario de Task Chains se lista una vez y cada cadena se lee una vez, con la caché del exportador.

## Separación de evidencias (decisión de diseño)
| | Evidencia del pool (assessment) | Evidencia descriptiva del Catálogo |
|---|---|---|
| Qué | Nodo `task-chains/<TC>.json` + arista `ORCHESTRATES` TC → flujo | Pasos `REMOTE_TABLES / REPLICATE` en `taskChainScan.remoteTableSteps` |
| Dónde vive | Pool (`.csn_cache/<cliente>/`) y aristas del manifiesto | Solo en el manifiesto (`.lineage_runs/**/lineage-manifest.json`) |
| Compuerta de completitud de la publicación (H1) | Aplica: una lectura fallida bloquea | No aplica: una falla queda en `taskChainScan` (`readFailures`, `inventoryComplete`, `nonBlockingFailures`) y el Catálogo la muestra como NOT_ASSESSED |
| Firma de dependencias del assessment | La afecta (la cadena menciona objetos de la clausura) | No la afecta |
| Consumidor | Assessment y panel de tablas locales | Solo el panel «Orquestación de ingesta» de Remote Tables |

**Motivo:** el JSON de una cadena que replica Remote Tables menciona objetos de la clausura. Si entrara al pool, la firma de dependencias (`dependency_signature.py`, D1) dejaría vencidos todos los assessments que la comparten, aunque no cambie ninguna de sus entradas. Lo comprobamos con los cinco modelos de Contreras. Además, una arista hacia un objeto fuera del pool haría fallar la compuerta H1 (`EDGE_TARGET_NOT_IN_PLAN`).

Con flows en el linaje rige el comportamiento histórico, incluido que una falla de lectura bloquee.

## Cobertura efectiva y estados del panel
- **`taskChainScan`** (`task-chain-scan/v1`) registra:
  - el estado (`DISABLED`, `NOT_RUN`, `NO_CANDIDATES` o `SCANNED`);
  - `inventoryComplete`;
  - `chainsListed` y `chainsRead`;
  - `readFailures`;
  - las Remote Tables candidatas y sus pasos.
- **De dónde sale el estado:** de la extracción más reciente que cubre la tabla, según el `createdAt` del manifiesto. Nunca de la unión con exportes anteriores. Si una cadena se elimina o se modifica, la próxima publicación lo refleja.
- **Estados:**
  - `OBSERVED`: hay un paso verificado en esa extracción.
  - `NOT_OBSERVED`: el escaneo fue completo y la tabla era candidata, pero ninguna cadena la replica. La réplica programada directamente sobre la Remote Table no se evalúa, así que de esto no se concluye que la tabla se consuma por federación.
  - `NOT_ASSESSED`: no hay manifiesto, el manifiesto es anterior a la fase 1, el escaneo no corrió o fue incompleto.
- Nunca se afirma el estado operativo («Replicated (Snapshot)») ni el resultado de una ejecución.

## Persistencia
- El manifiesto, clásico o transaccional (`schemaVersion` 3, sellado), lleva `taskChainScan` como un dato más.
- El espejo a Spaces sube el archivo completo y el Catálogo lo lee igual. Lo cubre `tests/test_publication_task_chain_replication.py`.

## Fase 2 (pendiente): integración al assessment sin invalidaciones innecesarias
1. Sección propia de evidencia canónica, por ejemplo `acquisition_strategy`, derivada **solo** de `taskChainScan` y versionada. Sin leer el JSON de la cadena desde el pool.
2. La estrategia declarada es un hecho descriptivo de Ingesta: no aporta a SRC-STR-01 ni crea debilidades.
3. **Vigencia:**
   - la sección entra en una migración de versión de evidencia junto con la clasificación SAPSF, para hacer una sola migración;
   - un cambio en `taskChainScan` no invalida la firma de dependencias de la clausura, sino solo esa sección. Una firma propia permite recalcularla sin regenerar el perfil;
   - los cambios operativos (ejecuciones, estado `Replicated (Snapshot)`) nunca entran al perfil.
4. Las fallas de lectura de Task Chains siguen sin bloquear: el hecho queda como NOT_ASSESSED.
5. Antes de adoptarla: dry-run sobre todos los sobres productivos, mostrando cuáles cambian solo de sección descriptiva y confirmando que no cambia ningún nivel.

## Pendiente fuera de esta fase
La validación de alcance de features exige al menos una familia de flows para pedir `task-chains` (`INVALID_FEATURE_SCOPE`). Hoy no afecta, porque las publicaciones reales piden todas las familias. Si se quisiera pedir solo tablas y task chains, hay que revisar esa regla.
