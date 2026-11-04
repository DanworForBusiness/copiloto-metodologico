# CCHH: retiro de originales, avisos y acciones de tabla

## Diagnóstico comprobado

`core.microsoft365.graph_patch_json` estaba definido dos veces. La segunda
definición (Solicitudes, keyword `etag`) ocultaba la primera (CCHH, keywords
`extra_headers` y `timeout`). El retiro/movimiento desde carga-masiva invocaba
`extra_headers`, produciendo un TypeError antes del request de PATCH. El catch
general lo convertía en retiro pendiente y mensaje de timeout de SharePoint.
La carpeta destino podía haberse creado antes de fallar ese PATCH.

La versión actual reintenta retiros pendientes al cargar la bandeja (acotado por
AUTO_RETIRE_MAX). Esto explica que incorporados se recree al abrir la pantalla:
el intento crea la carpeta si falta. No se cambió esa política funcional.

## Corrección

Un único transporte admite ambos contratos, preservando If-Match y el timeout.
La combinación de etag y cabecera contradictorios se rechaza sin red. Solicitudes
mantiene su ETag obligatorio en el repositorio; no se cambian scopes ni permisos.
El DELETE sigue siendo graph_delete, compartido con los documentos gobernados.

Reintentar el retiro mueve al destino registrado; no equivale a pulsar Eliminar.
La papelera conserva el comportamiento y las confirmaciones existentes. No se
modificaron controles de identidad, contenido, ubicación, ETag ni baja del Banco.
Un TypeError local se clasifica como RETIRE_LOCAL, sin atribuirlo a SharePoint.

## UX

- Avisos CCHH de resultado/error y transitorios incorporan un cierre accesible ×,
  reutilizando panel-close. Funciona con HTMX y contenido generado dinámicamente.
- Conserva timers, fade y contenedores de los toasts para mensajes posteriores.
  No añade timers propios ni afecta otras verticales. Los campos de confirmación
  dentro de formularios no se convierten en avisos descartables.
- El conjunto de acciones lo define la página visible. Cada fila muestra los
  mismos SVG en el mismo orden; lo que no aplica está disabled. Si ninguna fila
  admite una acción, no se representa. Descartar/eliminar comparten una posición
  de papelera, con su acción y confirmación específicas.

## Verificación

Pruebas de transporte usan urlopen simulado y las funciones reales de core: PATCH
con extra_headers, PATCH con etag y DELETE con If-Match. Se prueba conciliación
de una eliminación cuya respuesta se pierde sin repetir el DELETE.
La regresión amplia de CCHH, documentos, autenticación y Solicitudes pasó con
1.372 pruebas; la prueba nueva de error local y los gates focalizados también
se ejecutaron sobre la corrección final.

Navegador: escritorio 1600px / móvil 390px, claro y oscuro; posiciones medidas,
disabled por fila, desaparición de acciones no aplicables a toda la página,
cierre por teclado, cierre manual y auto-dismiss original de seis segundos.

Revisión: http://localhost:8135/cchh/candidatos/importar (laboratorio aislado,
datos y drive simulados). Hub del worktree: http://localhost:8085/cchh/candidatos/importar
(sesión corporativa requerida). No se tocaron las instancias 8082/8083/8084.

No se movieron ni eliminaron archivos reales. La causa local está comprobada;
la confirmación de una operación física real sigue pendiente del reintento
autorizado del Product Owner. No se afirma que exista un 403, bloqueo de permisos
o timeout de Graph en producción sin evidencia del request real.
