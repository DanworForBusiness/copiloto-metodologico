# Solicitudes Vigentes y Cerradas

Proyección de lectura, sin cambios de estados ni contratos de escritura.

- Funnel `Rechazado, Aplica Operaciones` tiene prioridad absoluta: Cerradas.
- Arquitectura `Finalizado` o `Suspendido`: Cerradas.
- `Pendiente`, `Planificado`, `En curso`: Vigentes si no hay rechazo.
- Estados vacíos o desconocidos no rechazados: Vigentes con aviso visible y
  contador `data-unknown-state-count`. No se descartan ni se normalizan en origen.

Orden de procesamiento: snapshot autorizado completo → filtros/búsqueda comunes →
clasificación exclusiva → orden independiente → paginación de 50 por panel.
Parámetros `page_vigentes`, `page_cerradas`, `sort_vigentes`, `sort_cerradas` y sus
direcciones conservan el otro panel. Cambiar filtros/búsqueda reinicia ambas páginas.
El paginador compartido admite `page_param` opcional; su default sigue siendo `page`.

Vigentes abre inicialmente; Cerradas queda colapsado salvo que no haya Vigentes,
siguiendo Búsquedas. Se reutiliza persistencia de colapso del Hub.
Ambas tablas comparten la preferencia de layout existente v3; no cambia el esquema
de columnas. El checkbox de página se limita a su tabla, con una selección global
por ID y una sola barra contextual. La reconciliación sigue usando todo el universo
autorizado, no solo los dos recortes paginados.

Base: rama de badges `987214a`, que incorpora main `8df68ac` (PR #267 integrado).
No se duplica esa implementación: esta rama conserva la macro de badges.

Validación local: 1.034 pruebas aprobadas. Navegador con 210 registros simulados:
escritorio 1600px / móvil 390px, claro y oscuro; páginas independientes, selección
por tabla y entre paneles, conservación del colapso, filtros reiniciando ambas
páginas, reutilización del snapshot y drag/resize/persistencia en ambas tablas.
Clasificación, rechazo con Pendiente, desconocidos, reubicación al releer, errores
y vacíos cubiertos por pruebas backend.

Revisión: http://localhost:8133/operaciones/solicitudes (laboratorio simulado) y
http://localhost:8084/operaciones/solicitudes (Hub, sesión corporativa requerida).
Sin escrituras reales SharePoint, merge ni despliegue. Aceptación con datos reales
y CI de GitHub pendientes de revisión/integración.
