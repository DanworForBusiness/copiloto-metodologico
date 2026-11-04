# Admisión de Solicitudes — preparación, no integrada

## Estado

Las reglas puras de `solicitudes_admission.py` no están conectadas a rutas,
transportes ni plantillas. No cambian el guardado, la creación ni la admisión
actual. No se realizaron operaciones Graph reales.

La especificación del Product Owner exige confirmar los metadatos efectivos
antes de implementar escrituras de Funnel. Esa evidencia sigue pendiente.
Los fixtures prueban el comportamiento, no acreditan el contrato productivo.

Base verificada: main `20911db3d14b380700d490c2c404a3da5961a6c8`.
La rama deriva del avance publicado `4955834`, que incluye la corrección del
catálogo de Arquitectura y de los mensajes de guardado aún no integradas.
La consulta GitHub de PR abiertos de Solicitudes no devolvió resultados.

## Evidencia requerida

Columna `Status_x0020_funnel`, lista
`5d054bcc-8516-4fd5-b538-2d4cc5d754fb`, sitio Gestión:

- Tipo choice; opciones que incluyan exactamente los textos aprobados:
  Solicitado; Aprobado para Arquitectura; Rechazado, Aplica Operaciones.
- Editable, no calculada, default, obligatoriedad y restricciones.
- Para el alta, confirmar default Solicitado y Arquitectura Pendiente.

Una captura de configuración o una lectura autenticada de metadatos son
evidencias válidas. No se necesitan credenciales, tokens ni datos de registros.
`readOnly: false` debe estar comprobado; la ausencia del atributo no se interpreta
como confirmación. Los permisos efectivos de usuario se siguen verificando
mediante Microsoft, independientemente de la editabilidad del campo.

## Reglas implementadas, sin escritura

- Solo con Funnel Solicitado: primera fecha planificada/real o cambio efectivo
  a Planificado, En curso o Finalizado propone aprobación.
- Cambiar una fecha existente, comentarios, prioridad, proyecto, colaborador
  o pasar a Suspendido no dispara aprobación por sí solo.
- No se reparan retroactivamente compromisos preexistentes al abrir o guardar.
- Requiere confirmación explícita y catálogo compatible para construir el
  único payload conjunto operativo + Funnel.
- Aprobaciones, rechazos y valores desconocidos nunca se reemplazan implícitamente.
- Rechazo exige Solicitado, confirmación y motivo no vacío; su payload contiene
  solo Funnel y Comentarios. Conserva el HTML anterior y agrega el motivo escapado.
- Funnel continúa fuera de la allowlist de edición libre y del alta.

## Integración pendiente tras validar el contrato real

1. Mostrar confirmación contextual vinculada al borrador y su ETag antes de
   enviar el PATCH conjunto; revalidar los disparadores con valores vigentes.
2. Agregar diálogo compartido de rechazo, con motivo obligatorio y protección
   explícita frente a un borrador pendiente: no guardarlo ni descartarlo.
3. Reutilizar token delegado, acceso de Operaciones, same-origin e If-Match.
4. Proteger doble envío, resultado incierto y aprobación/rechazo concurrentes;
   nunca repetir automáticamente un PATCH incierto.
5. Releer y comprobar todos los campos enviados, incluido Funnel; invalidar
   snapshot, auditar sin motivos/contenido sensible y refrescar badges.
6. Validar el default real del alta sin agregar aprobación o rechazo a su UI.
7. Probar endpoints HTMX, ETag/412, 401/403/429, relectura, auditoría, snapshot,
   borradores y diálogos en escritorio/móvil y ambos temas con transportes dobles.

No hay evidencia visual del workflow porque aún no se incorporó al formulario.
No está listo para aceptación, merge o despliegue.

## Validación realizada

117 pruebas focalizadas aprobadas: 30 casos de admisión y 87 pruebas de
Solicitudes, alta, eliminación, catálogo y confirmación ya existentes.
Sin PATCH, POST ni DELETE reales sobre SharePoint.
