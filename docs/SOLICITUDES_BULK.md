# Solicitudes: selección y acciones en lote

## Implementación

Base: `2182ffc`, con alta, edición, admisión y bitácora existentes. La rama
`feat/solicitudes-bulk-v1` no modifica esos contratos ni amplía scopes globales.

La selección reutiliza el motor extraído de CCHH (`table_selection.js`) y la
misma macro de barra contextual. CCHH conserva sus claves, límites y conducta.
Solicitudes guarda únicamente IDs en sessionStorage, con namespace por usuario,
vertical y lista. El checkbox de encabezado selecciona solo la página visible.
El universo autorizado del snapshot permite reconciliar selecciones sin perderlas
por filtros, orden o paginación. Los errores Graph no se interpretan como universo vacío.

Compartir produce enlaces internos autenticados y mensajes copiables, sin tokens
ni enlaces públicos SharePoint. Consolidar requiere dos registros y solo compara
atributos y permite proponer un principal; no existe operación de consolidación efectiva.

## Contrato de actualización

Un plan permite un campo común: Status Arquitectura, Prioridad, Colaborador asignado,
Fecha de inicio planificada o Fecha de inicio real. Se validan catálogos y esquema;
la asignación individual conserva la colección Graph y protege los múltiples históricos.
Title, Proyecto y narrativas quedan fuera del contrato masivo.

Preparar y revisar no escriben. La revisión obtiene valores y ETags vigentes y
explica los efectos de admisión reutilizados de la lógica central de Funnel.
Una confirmación explícita habilita tandas de cinco, con identidad delegada de
escritura y autorización de Operaciones. Cada registro se relee, comprueba su ETag,
recibe PATCH parcial con If-Match y se relee para confirmar. Se invalida el snapshot
tras intentos de escritura. La auditoría registra IDs, campos y resultados, sin contenidos.

Los resultados distinguen confirmada, sin cambios, conflicto, rechazada e incierta.
Un doble envío no repite las filas confirmadas. Ante respuesta HTTP perdida se
consultan resultados del plan; no se repite automáticamente el PATCH. Reintentar
exige una revisión nueva de los fallidos y versiones actuales; excluye los éxitos.

Los planes viven en memoria del proceso durante 30 minutos, ligados al actor,
con límite de 512. No contienen tokens. Reiniciar o cambiar de worker puede hacer
que el plan deje de estar disponible: falla cerrado y nunca reconstruye un envío.
Antes de operar con múltiples workers debe resolverse afinidad o almacenamiento
compartido del plan; no se afirma durabilidad ni ejecución transaccional.

## Consolidación futura: decisión pendiente

Propuesta: lista separada de relaciones, conservando todas las solicitudes originales.
Cada relación tendría IDs estables de principal y origen, tipo de relación,
identidad del actor, fecha UTC y versión de la decisión. Una clave de relación
evitaría duplicados. Permite reversión sin borrar ni concatenar narrativas.
Alternativa: lookup múltiple a esta misma lista en la principal, con historial de
versiones. Requiere aprobar esquema, permisos, semántica y concurrencia antes de
implementarlo. Ninguna lista, columna ni relación fue creada por este desarrollo.

## Evidencia y límites

- Regresión local relevante: 932 pruebas aprobadas (Operaciones, Solicitudes,
  Búsquedas, autenticación, selección CCHH, tablas y navegación).
- Navegador real automatizado con 60 fixtures explícitamente simulados:
  escritorio/móvil, claro/oscuro, selección, persistencia HTMX, compartir,
  preview, admisión, protección de múltiples, fallos parciales y reintentos.
- Arrastre y resize nativos, persistencia al recargar, tandas de 50 seleccionados
  y recuperación de respuesta perdida verificados sin repetir escrituras simuladas.
- Interacciones sobre snapshot vigente verificadas sin lecturas Graph adicionales.
- Laboratorio seguro: `http://127.0.0.1:8133/operaciones/solicitudes`.
- Hub del worktree: `http://localhost:8084/operaciones/solicitudes`.
  8083 permanece independiente. El origen nuevo puede requerir login corporativo.

No se ejecutaron POST, PATCH ni DELETE reales sobre SharePoint. La integración
de escritura efectiva, permisos delegados del usuario y reconciliación remota
siguen pendientes de un registro expresamente autorizado. Los resultados con
fixtures no equivalen a aceptación productiva. Sin merge ni despliegue.
