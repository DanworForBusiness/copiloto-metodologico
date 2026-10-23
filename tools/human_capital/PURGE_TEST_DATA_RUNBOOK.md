# Runbook — purga controlada de datos de PRUEBA de Capital Humano (DigitalOcean)

**Estado: herramienta implementada y probada solo con datos sintéticos. NO se ejecutó ninguna purga real.** Cada paso real lo autoriza el owner después de revisar el resultado del anterior.

Herramienta: `python -m tools.human_capital.purge_test_data` (lógica y garantías en el docstring del módulo). Se ejecuta desde la raíz del repo, en la consola del servicio web de App Platform, con comandos cortos y sin heredoc.

## Alcance
- **Se purga** (lista blanca bajo `human-capital/`, disco **y** Spaces): `profiles/`, `profile-documents/` (solo metadatos), `busquedas/` (incluye solicitudes de Operaciones y postulaciones), `entrevistas/`, `onboarding/`, `scoring-grids/`, `scoring-evaluations/`, `publicaciones/` y los temporales de escritura de esas carpetas.
- **Se preserva siempre:** `catalogs/`, `import-history/` (política de retención propia), cualquier archivo desconocido y todo lo que esté fuera de `human-capital/` (configuración, bitácora general, otras verticales).
- **Sin Graph:** no se borra ni se toca ningún CV ni carpeta de SharePoint (los archivos de SharePoint quedan huérfanos por diseño).

## Parámetros fijos
Quietud de **10 min** · dos inventarios separados **20 s** · ventana de mantenimiento máxima **60 min** · verificación final tras **≥ 150 s** · retención recomendada del backup **14 días** (sin borrado automático; la eliminación es manual, desde la consola de Spaces).

## Antes de empezar
1. Acordar una **ventana de mantenimiento** (nadie usa CCHH/Operaciones→Búsquedas). La herramienta detecta deriva y escrituras concurrentes, pero **no puede impedirlas**.
2. El owner confirma que **no hay datos ni solicitudes reales** que conservar (queda como `--owner-ack`).

## Pasos
1. **Dry-run (solo lectura)** — inventario local/Spaces, diferencias, huérfanos, desconocidos y las frases de confirmación:
   ```
   python -m tools.human_capital.purge_test_data --dry-run > /tmp/cchh_purge_plan.json; echo exit=$?
   head -c 6000 /tmp/cchh_purge_plan.json
   ```
   Revisar con el owner: `environment` (bucket/prefijo/base_dir), `counts`, `inconsistencies`, `orphans`, `unknown_files`, `preserved`, `blockers`. Pegar el JSON de vuelta. **Detenerse acá.**
2. **Backup sellado** (después del GO): `python -m tools.human_capital.purge_test_data --backup --confirm BACKUP-CCHH-<env8>-<digest12>` — imprime el `run_id`; el respaldo (incluido contenido solo remoto y versiones remotas distintas) queda en `migration-backups/cchh_reset/<run_id>/`, sellado y re-descargado/verificado.
3. **Ejecución** (después de otro GO, dentro de la ventana):
   ```
   python -m tools.human_capital.purge_test_data --execute --confirm PURGE-CCHH-<env8>-<digest12> --owner-ack NO-REAL-DATA-<digest12> --backup-run <run_id> --window-until <AAAA-MM-DDTHH:MM:SSZ>
   ```
   Si se corta (código 5) se **reanuda con el mismo comando** (y una ventana nueva): el manifiesto sellado manda; lo ausente cuenta como completado y lo presente debe conservar exactamente los hashes aprobados. Código 6 = deriva/concurrencia: no se borra nada más; revisar antes de seguir.
4. **Verificación**: `python -m tools.human_capital.purge_test_data --verify --backup-run <run_id>` — la primera vez devuelve `PENDING_SYNC_WINDOW` (código 3) hasta pasados 150 s; repetir después. Con código 0 la vertical quedó vacía, operativa para nuevas altas y sin reaparición.
5. **Alta de prueba** a mano desde la UI (la herramienta no crea registros).

## Restauración (solo si hace falta; no hay rollback automático)
- Plan sin cambios: `python -m tools.human_capital.purge_test_data --restore --backup-run <run_id>` (informa y muestra la frase requerida).
- Ejecutar: `... --restore --backup-run <run_id> --confirm RESTORE-CCHH-<env8>-<digest12>`. Solo desde un respaldo sellado del MISMO entorno; **no sobrescribe** nada existente (los conflictos se informan). Cada lado se recupera en su propio almacenamiento con SU contenido original (si la versión remota difería de la local, se restaura la remota en Spaces y la local en disco), verificando el hash de cada uno contra el manifiesto sellado; ante cualquier falla se detiene con un error explícito y NO declara éxito parcial (lo ya restaurado no se revierte; reintentar es idempotente).

## Códigos de salida
0 ok · 2 bloqueado · 3 verificación pendiente · 4 confirmación faltante/inválida (cero cambios) · 5 falla (reanudable) · 6 deriva o escrituras concurrentes · 7 verificación fallida.
