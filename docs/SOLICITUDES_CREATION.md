# Alta de Solicitudes

## Acceso y autorización Microsoft

Consulta, alta y edición usan la política existente de Operaciones. No hay
feature flag de escritura ni rol adicional. La lista y los catálogos se leen
con `dwGraphToken`. Crear y Guardar usan `dwSolicitudesWriteToken`, definido en
la autoridad institucional `hub_auth.js`, con consentimiento incremental para
`Sites.ReadWrite.All`. Los scopes ordinarios del Hub siguen siendo `User.Read`
y `Sites.Read.All`. CCHH conserva su scope configurado de documentos.

El envío intenta adquirir el token silenciosamente. Si hace falta interacción,
el formulario conserva el borrador y ofrece Autorizar escritura con Microsoft.
Ese gesto usa exclusivamente `dwGraphReauthenticate`. Graph aplica los permisos
efectivos del usuario sobre la lista. El Hub comunica 401, 403 y 429 y no guarda
tokens en archivos, almacenamiento del navegador o logs.

## Contrato del alta

`GET /operaciones/solicitudes/nueva` abre una página propia. La hidratación
`POST /nueva/datos` solamente lee metadatos y catálogos. El formulario es el mismo
partial que usa edición, con Title obligatorio, Pendiente como único estado
inicial y los selectores existentes de Proyecto y Colaborador.

`POST /operaciones/solicitudes/nueva` valida sesión, acceso a Operaciones,
mismo origen, cardinalidad de parámetros y una allowlist propia. Antes del POST
Graph vuelve a leer las definiciones de columnas, incluidos campos ocultos,
obligatoriedad, defaults, tipos, choices y campos calculados o de solo lectura.
Un esquema incompatible o un campo obligatorio no contemplado sin default
impide generar el payload. No se inventa Status funnel ni se envían campos de
creación administrados. Los defaults omitidos quedan a cargo de SharePoint.

El payload usa el sobre `fields`. Proyecto se envía como `ProyectoLookupId`.
Colaborador utiliza la colección LookupId de cero o un elemento y su anotación
`Collection(Edm.String)`. Los IDs se validan contra los catálogos. Fechas y
texto seguro reutilizan las validaciones y serialización de edición.

Tras obtener el ID, se relee el registro y se comparan los valores enviados.
La confirmación invalida el snapshot y navega al detalle. Edición mantiene
PATCH parcial, ETag/If-Match, protección de campos y auditoría existentes.

## Repeticiones y resultados inciertos

Cada alta recibe un ticket de un solo uso vinculado al actor. Una sección
crítica impide dos POST concurrentes. Los errores de validación y rechazos
HTTP definitivos permiten corregir el mismo borrador. Tras un timeout, error
5xx o confirmación fallida, el ticket queda consumido y no se reintenta el POST.
Un replay confirmado con el mismo contenido devuelve el ID original.

Los tickets viven en memoria durante una hora. Un ticket desconocido después
de un reinicio o recibido por otro proceso se rechaza antes de contactar Graph.
En instalaciones con varios workers sin afinidad, puede ser necesario reabrir
el alta en el proceso que la atenderá. No se garantiza idempotencia entre dos
formularios distintos ni se infiere por Title que dos solicitudes sean iguales.

## Validación real pendiente

El desarrollo y las capturas usan fixtures identificados y transportes dobles.
No se ejecutaron POST, PATCH ni DELETE sobre SharePoint real. Las definiciones
reales completas de creación no se recuperaron desde la sesión del PO durante
esta ejecución. El código las valida en cada alta; los fixtures no acreditan
el esquema productivo ni sus defaults o fórmulas de validación.

Antes de aceptar una escritura real faltan la autorización delegada de Entra,
consentimiento administrativo si el tenant lo exige, permisos efectivos del
usuario en la lista y un registro expresamente autorizado para la prueba.
La aceptación debe comprobar metadatos reales, defaults de Status funnel y
fecha de creación, lookup simple/múltiple, alta única, relectura, auditoría,
PATCH parcial y conflicto ETag sobre ese registro.

Referencias oficiales

- [Crear un listItem](https://learn.microsoft.com/en-us/graph/api/listitem-create?view=graph-rest-1.0)
- [columnDefinition](https://learn.microsoft.com/en-us/graph/api/resources/columndefinition?view=graph-rest-1.0)
