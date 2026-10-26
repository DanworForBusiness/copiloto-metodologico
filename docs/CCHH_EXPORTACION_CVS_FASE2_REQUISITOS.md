# CCHH → Candidatos — Fase 2 (exportación de CVs): requisitos acordados

Estado: **implementada (local, pendiente de revisión)** — «Descargar CVs» en la barra de selección de Candidatos. Este documento conserva los requisitos acordados y, al final, cómo se cumplieron y qué riesgos quedan.
La Fase 1 (ficha de consulta + selección + «Compartir perfiles») no incluía ZIP, descarga de documentos ni auditoría de exportación; la «selección de todos los resultados del filtro» sigue fuera de alcance.

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

## Fuera de alcance (sigue vigente)
Selección de todos los resultados del filtro · procesamiento por tandas · infraestructura adicional (workers, colas, almacenamiento temporal fuera del contenedor).

## Implementación (resumen técnico)

Código: `webapp/services/human_capital_export.py` (plan, auditoría, ejecución), `core/microsoft365.graph_download_capped` (lectura acotada), rutas `POST /cchh/candidatos/descargar/resumen` y `POST /cchh/candidatos/descargar`,
`webapp/static/human_capital_candidates_download.js` y `partials/human_capital_download_dialog_content.html`.

- **Una sola solicitud HTTP** con presupuesto de **60 s** (no se inicia ningún archivo nuevo pasados 45 s; cada archivo tiene su propio plazo de 30 s y un timeout de socket de 15 s). El proxy de App Platform corta hacia los ~100 s
  (no documentado oficialmente). La medición real previa en DigitalOcean (3 CVs, con una sonda temporal que no forma parte de este cambio) dio 452–832 ms por documento (≈ 1 s por archivo con margen) y 177 ms de ZIP; el ZIP es **almacenado** (los PDF/DOCX ya están comprimidos).
- **Límites en el servidor:** 25 candidatos (antes de leer ningún perfil), 60 archivos, 100 MB totales y 25 MB por archivo, con el tamaño declarado al planificar y **lectura acotada durante la transferencia** (tope de bytes, plazo total,
  cancelación). Los CVs que no se pueden bajar de antemano (más de 25 MB, sin ubicación válida) se informan como omitidos en el resumen y al confirmar el usuario acepta que quedan fuera.
- **Plan y confirmación:** el resumen (solo metadata local, sin Graph) trae un *digest* del plan; el servidor solo ejecuta lo que el usuario confirmó (si la selección o los CVs cambiaron, no se descarga nada).
- **Auditoría durable fail-closed** (`human-capital/cv-exports/<operation_id>/intent.json` y `result.json`, `remote_store.write_through` confirmado): la intención **antes** de la primera llamada a Graph; el resultado **antes** de entregar.
  Solo actor, ids internos, cantidades y códigos. Sin `LOG_ACTIVITY`.
- **Nunca un ZIP parcial en silencio:** con cualquier fallo (403/404/429/5xx/red/corte/tiempo) no se entrega nada; el diálogo lista los documentos fallidos y ofrece «Reintentar» o «Descargar sin esos N documentos» (segunda
  confirmación explícita con los ids reconocidos; el servidor solo la acepta si los fallos son un subconjunto de los reconocidos). El ZIP parcial lleva un `LEEME_OMITIDOS.txt`. Los reintentos son idempotentes (solo lecturas); cada intento es
  una operación auditada, enlazada con la anterior por `parent_operation_id`.
- **Reintentos:** un reintento ante 429/5xx/red/corte, con `Retry-After` acotado a 5 s y solo si cabe en el presupuesto; 401 (token vencido) corta la corrida; 403/404 no se reintentan.
- **Temporal:** `dwcvx-*` fuera de las rutas persistidas; cada CV baja a un parcial que se borra al entrar al ZIP; en Linux el ZIP se desvincula antes de entregarlo; `try/finally` + barrido de restos viejos. No se persiste ningún ZIP ni copia de CV.
- **Concurrencia (en proceso, réplica única):** una exportación en vuelo por usuario y como máximo dos en total (429 + `Retry-After`). **Cancelación:** si el navegador corta la solicitud (Cancelar, cerrar el diálogo, desconexión), el servidor cancela la lectura,
  limpia y registra `cancelled`.

## Riesgos residuales
- Réplica única como precondición: la concurrencia y el temporal son locales al proceso (igual que el resto de CCHH).
- El límite del proxy (~100 s) no está documentado oficialmente y el rendimiento de Graph varía: una selección grande puede terminar en parcial por tiempo (se informa, nunca en silencio). No hay procesamiento por tandas.
- La auditoría exige Spaces confirmado (`DANWOR_PERSISTENCE=remote`): si Spaces falla, no se puede exportar (diseño fail-closed).
- Acceso: hoy exige el nivel CCHH habitual (APP_OWNER o `HUMAN_CAPITAL_ALLOWED_USERS`); queda pendiente decidir si debe exigir el nivel de datos sensibles.
- El token delegado (`User.Read` + `Sites.Read.All`) viaja en el cuerpo del POST: no se guarda, no se registra y no figura en la auditoría.
- Un CV movido o borrado en SharePoint se informa como «no encontrado» (el modelo guarda `drive_id`/`item_id`, no se resuelve de nuevo por ruta).
