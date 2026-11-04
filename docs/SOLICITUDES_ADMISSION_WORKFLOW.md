# Solicitudes — admisión, observaciones e historial

## Estado y evidencia

Implementado en `feat/solicitudes-admission-workflow`, sobre el trabajo previo
`82dfa7d`. Main verificado: `20911db3d14b380700d490c2c404a3da5961a6c8`.
No hay merge ni deploy autorizado en esta iteración.

El Product Owner aportó capturas de SharePoint que confirman:

- Funnel: Choice con Solicitado, Aprobado para Arquitectura y Rechazado, Aplica Operaciones.
- Comentarios: texto multilínea enriquecido mejorado, opcional, sin append nativo.
- Seis líneas corresponde a la altura del formulario, no a capacidad de almacenamiento.

Las capturas no acreditan permisos Graph efectivos ni conservación del HTML al
escribir. Cada nueva operación valida los metadatos efectivos con el token
delegado. No se ejecutaron POST, PATCH ni DELETE reales en SharePoint.

## Admisión

Solo si Funnel vigente es Solicitado, una primera fecha planificada/real o un
cambio efectivo a Planificado, En curso o Finalizado propone aprobación.
El usuario confirma mediante el diálogo compartido del Hub. Backend valida
el evento contra el registro vigente y el catálogo editable. Campos operativos
y Funnel se envían en un único PATCH con If-Match; la relectura comprueba
explícitamente el valor derivado de Funnel además de los campos ingresados.

No se aprueba por comentarios, prioridad, referencias, Suspendido ni por cambiar
una fecha previamente informada. No hay reparación retroactiva al consultar ni
sincronización de estados. Decisiones previas no se revierten implícitamente.
No se reintrodujo una matriz de transiciones de Arquitectura.

Rechazar / Derivar a Operaciones solo aparece con Funnel Solicitado. El diálogo
solicita motivo obligatorio. Un borrador pendiente impide abrirlo sin guardar o
recargar expresamente. El endpoint admite únicamente ETag, motivo, confirmación,
ticket de observación y parámetros de token; rechaza campos operativos.
El único PATCH de rechazo contiene Funnel y Comentarios, con el motivo como
entrada de bitácora. Fechas, Arquitectura y asignaciones quedan intactas.

El alta conserva su contrato existente y no ofrece aprobar ni rechazar.
El default real de Funnel en la creación sigue siendo una comprobación de
integración pendiente; este cambio no agrega Funnel a su allowlist.

## Persistencia incremental

El formulario de edición utiliza `new_observation`, inicialmente vacío. El
nombre interno Comentarios no se acepta como campo de edición directa.
El alta mantiene su campo inicial de comentarios; al leerlo luego se considera
contenido heredado salvo que posea estructura verificable de bitácora.

El servidor anexa un div HTML al valor original sin analizarlo y volver a
serializarlo. El original permanece byte por byte en el payload. La nueva
entrada contiene cabecera legible con fecha ISO y offset, actor autenticado y
texto escapado con saltos de línea. No se interpreta el texto ingresado como HTML.

El div incorpora `data-dw-observation` con un documento firmado HS256: ID,
solicitud, tipo de entrada, UTC, actor y texto. Es metadata de integridad, NO un
token de acceso: ningún endpoint la admite como credencial o autorización.
Usa la clave de firma de sesión existente y un issuer específico. El ticket
previo al envío está firmado, vence en 24 horas y vincula ID, usuario y solicitud.
La hora de la entrada se genera al guardar. No procede de datos editables.

Una entrada ya presente con el mismo ID se rechaza antes de cualquier nuevo
PATCH. El ETag protege dos envíos concurrentes y los formularios obsoletos.
Un timeout provoca relectura, nunca replay automático. Si no puede confirmarse,
el borrador se conserva y se solicita revisar la versión vigente.
Solo al confirmarse se limpia el área temporal. Durante el envío se bloquea su
edición para no borrar texto nuevo escrito mientras esperaba la respuesta.

Se valida `readOnly=false`, texto multilínea, `textType=richText` y
`appendChangesToExistingText=false`. El máximo efectivo, cuando Graph lo expone,
se comprueba sobre el contenido completo serializado, incluida la metadata, en
unidades UTF-16. No se interpreta la altura de seis líneas como un límite.
Si el proveedor impone otra restricción y rechaza el PATCH, se preserva el borrador;
no se trunca ni se descarta historial.

## Proyección visual y contenido heredado

Se reutilizan dashboard-section, contador, matrix-scroll, dashboard-table,
field-hint, formularios y diálogos de Búsquedas/CCHH. Solo se agrega el ancho mínimo
de la tabla de historial y wrap del detalle para permitir scroll interno móvil.
No hay librería UI nueva ni refactorización de otras verticales.

Entradas firmadas válidas se muestran individualmente, de más nueva a más antigua,
en America/Argentina/Buenos_Aires usando BUSINESS_TZ compartida con Búsquedas.
El cuerpo visible debe concordar con el documento firmado. Lo no verificable se
muestra agrupado como una observación heredada, sin inventar autor ni fecha.
La representación es texto legible autoescapado, nunca HTML remoto ejecutable;
el HTML original continúa intacto en SharePoint y en el payload de append.

## Límites de integración real

La prueba autorizada de un registro deberá verificar que SharePoint conserva
`data-dw-observation` y el acumulado original. La confirmación compara las entradas firmadas y el contenido anterior, no la
serialización literal del HTML. Admite envoltorios neutros del proveedor,
entidades equivalentes y tbody implícito. Exige conservar entradas y su orden,
texto heredado, estructura significativa, enlaces, imágenes y atributos de
formato preexistentes. Si pierde metadata o contenido, informa resultado
incierto, conserva el borrador y no repite el PATCH. No se declara compatibilidad
productiva completa basándose en fixtures.

Una rotación de SESSION_SECRET invalidaría la verificación de entradas anteriores:
seguirán visibles como heredadas, sin atribución verificada. Antes de producción
se debe evaluar si la gobernanza de claves requiere un llavero de verificación
durable; no se agregó una nueva variable ni dependencia en este cambio.

## Movimientos operativos y auditoría durable

La tabla actual acredita observaciones y motivos de rechazo firmados. No atribuye
creación, aprobación ni cambios operativos basándose en logs de Python o en la
fecha de modificación del item. El log técnico conserva solo actor, item, nombres
de campos, resultado y correlación; no almacena el contenido de comentarios.

Microsoft Graph ofrece versiones con fields, lastModifiedDateTime y lastModifiedBy.
La disponibilidad y retención reales de esta lista no están comprobadas aquí.
Propuesta para la siguiente integración:

1. Leer todas las páginas de /items/{id}/versions y los campos necesarios.
2. Comparar únicamente versiones sucesivas verificables y completas; si faltan
   campos o hay huecos, indicar cobertura parcial sin inferir movimientos.
3. Atribuir fecha/actor solo desde metadata efectiva. No asumir que la primera
   versión retenida es la creación del registro.
4. No convertir cada acumulado de Comentarios en una observación nueva: el ID
   de entrada permite distinguir append de contenido repetido.
5. Acordar retención/versionado antes de presentarlo como auditoría completa.

Si las versiones son insuficientes, la alternativa es una fuente durable de eventos
con ID de operación, item, versión y resultado, con outbox/reconciliación para
manejar la falta de transacción entre Graph y el almacenamiento de eventos.
No se creó una lista, columna ni almacén adicional sin aprobación.

Referencias Microsoft:
- https://learn.microsoft.com/en-us/graph/api/listitem-list-versions?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/resources/listitemversion?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/resources/textcolumn?view=graph-rest-1.0

## Verificación de esta entrega

- 891 pruebas de regresión relevantes aprobadas en Operaciones, Búsquedas,
  Solicitudes, autenticación y selección/tablas compartidas de CCHH.
- Navegador real de pruebas con transportes Graph en memoria: claro/oscuro,
  escritorio 1440 px y móvil 390 px; historial, confirmación, cancelación,
  protección de borrador, aprobación, rechazo y relectura satisfactorios.
- Sin escrituras reales de SharePoint. La prueba controlada de persistencia
  productiva sigue pendiente de un registro expresamente autorizado.
- El barrido general de toda la web fue interrumpido para acotar la regresión
  a las superficies afectadas. No se declara aprobado el CI completo.
- CI de GitHub se dispara en PR o push a main, no en el push de esta rama.

## Corrección de confirmación tras normalización

El PO observó una entrada persistida y visible tras recargar pese al aviso
de resultado incierto. Se reprodujo en laboratorio la comparación literal de
HTML con envoltorios y entidades normalizadas. Guardar y Rechazar utilizan
ahora la misma confirmación semántica conservadora. No se realizaron nuevas
escrituras reales para probar esta corrección; queda pendiente su aceptación real.
El aviso usa callout--punch y field-hint compartidos en claro y oscuro, sin CSS nuevo.
