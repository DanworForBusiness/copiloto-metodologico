# CCHH → Candidatos — Fase 2 (exportación de CVs): requisitos acordados

Estado: **NO implementada.** Este documento fija lo que la exportación (descarga de CVs en ZIP) deberá cumplir antes de construirse, tal como se acordó al aprobar el discovery de selección múltiple.
La Fase 1 (ficha de consulta + selección + «Compartir perfiles») no incluye ZIP, descarga de documentos, auditoría de exportación ni «seleccionar todos los resultados del filtro».

## Requisitos obligatorios

1. **Auditoría durable con identificadores internos.** Toda exportación deja un registro **durable** (que sobreviva a reinicios y a restore) con identificadores internos —`profile_id`/`document_id` u
   `operation_id`— y quién la hizo, cuándo, cuántos perfiles/archivos/bytes y el resultado. **Sin** datos personales en el registro: ni nombres, ni nombres de archivo (contienen nombres), ni el texto de
   búsqueda, ni URLs, ni tokens. Se registra la **intención** antes de leer cualquier documento y el cierre después.
2. **No depender de `LOG_ACTIVITY`.** El sink de actividad actual es *best-effort* (nunca bloquea y es no-op si `LOG_ACTIVITY=OFF`): no sirve como evidencia de una exportación. Si la auditoría durable no
   se puede escribir, la exportación **no se ejecuta** (fail-closed).
3. **Límites explícitos** (valores a confirmar con el owner; propuesta del discovery): ≤ 25 candidatos, ≤ 60 archivos, ≤ 100 MB en total, ≤ 25 MB por archivo, presupuesto de tiempo acotado,
   concurrencia limitada y una exportación en vuelo por usuario, con límite de tasa. Se verifican **antes** de llamar a Graph (con los metadatos) y de nuevo durante la descarga.
4. **Tratamiento explícito de errores parciales.** Por archivo se clasifica el fallo (sin permiso, movido/eliminado, límite de tasa de Graph con un reintento, tiempo agotado, tamaño excedido). Si al menos un
   archivo se obtuvo se entrega el ZIP con un `LEEME` de errores y conteos; si ninguno, no hay ZIP. Los candidatos sin CV se informan y nunca bloquean.
5. **Descarga temporal, sin persistir ni duplicar.** Archivos obtenidos en streaming con el token **delegado** del usuario (los permisos reales de SharePoint), ZIP en almacenamiento temporal fuera de las
   rutas persistidas, borrado garantizado, `Cache-Control: no-store`, nombre del ZIP sin datos personales. No se modifican los CVs originales ni se crean enlaces/permisos.
6. **Confirmación explícita** previa con resumen (candidatos, con/sin CV, archivos, tamaño) para evitar descargas masivas accidentales.
7. **Acceso:** decisión pendiente del owner sobre si exige el nivel CCHH habitual o el nivel de datos sensibles.

## Fuera de esta fase por decisión del owner
ZIP · descarga de documentos · auditoría de exportación · selección de todos los resultados del filtro.
