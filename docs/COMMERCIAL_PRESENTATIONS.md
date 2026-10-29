# Gestión Comercial → Presentaciones (V1)

Presentaciones comerciales inmersivas gobernadas por el Hub: se **ven y presentan** por URL, se **copian** como instancias propias y se **editan** (campos de texto acotados) con control de revisiones.
Primer template: `vanture-bdc` ("La empresa autónoma comienza con los datos", Danwor × Vanture).

**Estrategia (extracción mínima).** El template conserva TODO el markup, CSS y JS del HTML original; los textos de las 12 slides (206 campos de texto plano; los diagramas son bloques gobernados por el template) son el contenido editable de la instancia, más la lista de slides eliminadas. No hay renderer universal,
bloques genéricos, SPA ni persistencia transversal nueva. Las composiciones especializadas (arquitecturas SAP BDC, flujo BDC Connect, pestañas por perfil, marquee) son markup del template.

## Rutas y autorización

| # | Ruta | Qué hace | Autorización |
|---|---|---|---|
| R1 | `GET /presentaciones/{id}` | Documento inmersivo (ver y presentar). `?embed=1` oculta la barra de co-branding. | Sesión + `auth_service.is_authorized` (`ALLOWED_USERS` ∪ `ACADEMY_USERS`). **No exige Comercial.** |
| R2 | `GET /comercial/presentaciones` | Catálogo (seed + instancias) | Comercial |
| R3 | `GET /comercial/presentaciones/{id}` | Detalle: shell del Hub + iframe de R1 | Comercial |
| R4 | `POST /comercial/presentaciones/{id}/copiar` | Copia como instancia propia | Comercial (cualquier presentación; sin requisito adicional) |
| R5 | `GET /comercial/presentaciones/{id}/editar` | Editor INLINE: el documento real en modo edición + barra Cancelar · «Sin guardar» · Guardar | Comercial + poder editar esa instancia |
| R6 | `POST /comercial/presentaciones/{id}` | Guarda campos, slides eliminadas y/o composición de componentes (JSON `{revision, fields?, removed_slides?, blocks?}`; al menos uno) | ídem R5 + `same_origin_guard` |
| R7 | `POST /comercial/presentaciones/{id}/eliminar` | Elimina la instancia (borrado real; el enlace compartido pasa a 404) | Comercial + poder eliminar esa instancia + `same_origin_guard` |
| R8 | `GET /presentaciones/{id}/logo` | PNG del logo propio de una copia (404 si no tiene) | MISMO contrato de lectura que R1: sesión + `is_authorized`, **sin** exigir Comercial |
| R9 | `POST /comercial/presentaciones/{id}/qr` | Reemplaza o quita (`remove=1`) el QR de la copia (multipart: `revision` + `file`) | ídem R6 (Comercial + poder editar esa instancia + `same_origin_guard`) |
| R10 | `GET /presentaciones/{id}/qr` | PNG del QR propio de una copia (404 si no tiene) | MISMO contrato de lectura que R1/R8 |
| R11 | `GET /comercial/componentes/vista?ref=` | Vista previa de un componente con el renderer real | Comercial + visibilidad del componente (404 si no lo ve) |
| R12 | `POST /comercial/componentes/duplicar` | Duplica un componente (institucional o propio visible) como PROPIO | Comercial + verlo + `same_origin_guard` |
| R13 | `POST /comercial/componentes/{id}` | Edita nombre, descripción y parámetros (textos y tonos) bajo revisión | responsable o `CONTENT_OWNER` + `same_origin_guard` |
| R14 | `POST /comercial/componentes/{id}/eliminar` | Elimina un componente propio | ídem R13 |

* **Copiar no implica editar.** Editar = Comercial **y** no-seed **y** (`owner_id` == usuario **o** `CONTENT_OWNER`). `CONTENT_OWNER` es la capacidad transversal de gobierno de edición de contenidos del Hub (no se creó ninguna variable, rol ni allowlist nueva).
  **`APP_OWNER` no edita contenido ajeno por ser administrador**: es el contrato que ya rige Estrategia (`commercial_access.can_edit_strategy_user`: «sin bypass de `APP_OWNER`; si el dueño de la app necesita editar, su identidad debe estar explícita en `CONTENT_OWNER`») y lo reitera `auth_service.is_content_owner_user`. Si el dueño de la app edita, lo hace porque también está en `CONTENT_OWNER`, o sobre sus propias copias (es su responsable).
* **Eliminar** = Comercial **y** no-seed **y** (`owner_id` == usuario **o** `APP_OWNER`). Es más estricto que editar para `CONTENT_OWNER` (edita contenido ajeno pero no lo elimina). Para `APP_OWNER` la capacidad se apoya en el contrato vigente de `commercial_access` («CORRECTIVE post-#107: `APP_OWNER` SÍ es superusuario de este workspace»): eliminar una copia es **administración del workspace** (limpiar instancias), no gobierno de contenido; la semilla institucional sigue siendo inmutable e imborrable para todos.

| Actor | Editar (texto / slides) | Eliminar la copia |
|---|---|---|
| Responsable de la copia (Comercial) | sí | sí |
| Otro usuario Comercial | no (403) | no (403) |
| `CONTENT_OWNER` (Comercial) | sí, cualquier copia | no, lo ajeno |
| `APP_OWNER` (sin `CONTENT_OWNER`) | solo las suyas | sí, cualquier copia (superusuario del workspace) |
| `APP_OWNER` que además está en `CONTENT_OWNER` | sí, cualquier copia | sí |
| Usuario del Hub sin Comercial | no | no |
| Semilla institucional | nadie | nadie |
* Eliminar es un **borrado real** (no hay papelera): el archivo local se borra y, en `remote`, también el objeto en Spaces. Si el borrado remoto no se confirma tras un reintento, el archivo local se **restaura** y se informa 503 (nunca queda borrada en un lado y viva en el otro). Una instancia cuyo template no está disponible igual se puede eliminar (así se limpia). Evento de actividad: `presentation.deleted`.
* **Botones siempre presentes:** el catálogo (6 acciones por fila: Ver, Presentar, Copiar enlace, Copiar como nueva, Editar, Eliminar) y el detalle (segmentado nativo `.dashboard-view-switch`: Presentar, Copiar enlace, Copiar como nueva, Editar, Eliminar) muestran siempre todas; la que no corresponde queda **inhabilitada** (grisada, con el motivo en el tooltip y en `aria-label`) para que la experiencia visual sea homogénea. Ocultar un botón nunca es la defensa: cada ruta valida en el servidor. La confirmación de Eliminar usa el diálogo nativo del Hub (`dwConfirm`).
* Los **seeds son inmutables** para todos (también `CONTENT_OWNER`): se cambian solo por código/deploy. No existe ninguna ruta para administrar templates o seeds.
* **La URL de R1 es una dirección de acceso autenticado**, no una credencial ni un permiso: no hay acceso anónimo, tokens ni permisos por destinatario. Cualquier usuario admitido al Hub con el enlace ve la presentación
  (el editor lo avisa). No habilita catálogo, copia ni edición.
* Orden de enforcement de R6: 401 → 403 (no Comercial) → 404 → 403 (no puede editarla / seed) → 403 (origen) → 400 → 422 → 409 (revisión, sin escribir) → 503 (persistencia, con compensación).

## Modelo

* **Seed** (`webapp/services/commercial_presentations_data/<template_id>.yaml`): solo lectura. **Instancia** (`commercial_strategy_runtime/presentations/<uuid4>.json`): contenido, identidad, `owner_id` y `revision` propios.
* `created_by_*` (procedencia histórica, inmutable) ≠ `owner_id` (responsabilidad editable; inicialmente igual al creador; sin UI para transferirlo en V1). `copied_from` = `{presentation_id, revision, template_version, at}`.
* La copia es un **snapshot**: guarda las 20 claves con sus valores en ese momento; no referencia al origen. Una clave que falte (campo agregado después) cae al valor del seed.
* CSS, JS, fuentes e imágenes son archivos **compartidos** (`content_packs/datasphere_core/assets/presentations/vanture-bdc/`, servidos por `/pack-assets`); la instancia no los duplica ni los referencia (~3 KB por instancia).
* **Campos editables (todos texto plano, obligatorios):** hero (3), dato de s2 (2), CTA de s10 (4), s11: 4 propuestas × (título, texto) + reconocimientos en 3 partes. Lista y límites en `VANTURE_FIELDS`. El QR queda estático.

## Homologación con el Hub (diferencias intencionales respecto del HTML original)

El criterio de aceptación es **paridad narrativa y funcional + homologación institucional**. Se preserva la experiencia inmersiva de Vanture (composición, animaciones, tabs, scrollspy, marquee) y se homologa solo lo siguiente:

| Elemento | Clasificación | Implementación |
|---|---|---|
| Botón **Volver** en detalle y editor | REUSE | `a.catalog-back` (tools.css), primer hijo de `main`, como Capital Humano/Catálogo. Detalle → catálogo; editor → detalle. La URL compartida (R1) **no** lleva Volver ni navegación de Comercial. |
| **Aviso** de visibilidad del enlace | REUSE | `callout callout--note` (variante informativa, con su regla de tema claro) + escala compacta `.cp-notice`; sin estilos inline. En el editor, `callout--punch` con la misma escala. |
| **Confirmación transitoria** («Enlace copiado», «Guardado», «Sin cambios») | REUSE | Contenedor `.cp-toast[data-dw-auto-dismiss="6000"]`: primitivo Hub-wide `initAutoDismissFeedback` (app.js), el mismo del refresh de catálogos de CCHH (se vacía solo; un contenido nuevo reinicia el temporizador; clic afuera lo retira). El JS arma el markup `.callout` con `textContent` y **reemplaza** el aviso anterior: nunca hay una pila. |
| **Logo Danwor** (barra superior y pie) | REUSE | El asset de Danwor Academy a pantalla completa, `academy/assets/img/danwor-logo.png` (wordmark **blanco**, para fondo oscuro), referenciado por `/pack-assets` (sin copiar binarios). Reemplaza la variante de wordmark gris oscuro del HTML original, casi invisible sobre la barra negra. El co-branding con el logo de Vanture se conserva. |
| **Numeración** de los 10 encabezados narrativos (s2–s9, s11, s12) | ADAPT | El número dorado pasa de la izquierda a **encima** del título (convención de Academy `.stop-num` y del instructivo «Paso NN de MM»): misma fila, en columna; se conservan tipografía, color, secuencia y la animación `wipeIn`. |
| Números dentro de tarjetas, pasos, pilares y el encabezado de s10 (ya iba encima) | KEEP | Son indicadores de composición/diagrama, no encabezados narrativos. |

### Documento inmersivo (R1): theme y estilos institucionales

El documento deja de ser una «isla oscura» con diseño propio: consume la fuente de verdad visual del Hub y cambia de tema de verdad (no solo el ícono).

| Capacidad | Cómo se consume (no se duplica) |
|---|---|
| **Theme toggle** (pestaña independiente, extremo derecho del header) | `.theme-toggle` del Hub con los SVG sol/luna institucionales, definidos **una sola vez** en `partials/theme_icons.html` (el topbar del Hub los consume desde ahí). Preferencia `dw-content-theme` y cookie existentes; sin variables, cookies ni claves nuevas. |
| **Contrato de theme único** | `static/hub_theme.js` (`window.dwTheme`: leer/aplicar/guardar/escuchar). `toggleTheme()` de app.js **delega** ahí en vez de reimplementarlo (extensión mínima: el Hub y la presentación comparten la misma implementación). |
| **Viewer embebido** | No renderiza header ni toggle (sin controles duplicados): sigue el tema del Hub por `postMessage` `danwor-theme` (mecanismo existente de `iframe.content-embed`). Una pestaña independiente también se sincroniza con un cambio hecho en el Hub (evento `storage`). |
| **Sin flash** | El servidor lee la cookie `dw-content-theme` y emite `class="content-theme-*"` en `<html>` y el ícono correcto; un usuario Hub **sin** Comercial ve exactamente lo mismo (R1 no exige Comercial). |
| **Superficies, tinta, bordes, acento** | Tokens semánticos de tema en `content.css` (`--content-surface/canvas/tile/ink/ink-soft/ink-dim/line/line-strong/accent`; claro/oscuro). Los consumen el tile del Hub, Danwor Academy (`dw-academy.css`, vía `@import`) y la presentación. Un cambio ahí llega a los tres. `--gold` del Hub y de Academy apunta a `--content-accent`. |
| **Fondo** | En **oscuro**, el canvas del Hub (`--content-canvas`, el tono de la landing y de Academy; la superficie del tile, `#181819`, se leía demasiado gris para una presentación). En **claro**, la superficie blanca (`--content-surface`). |
| **Tipografía** | `var(--sans)`/`var(--mono)` del Hub (Inter, JetBrains Mono) con el `<link>` de fuentes del Hub (`hub_fonts_url`); se eliminaron Barlow/IBM Plex y las 33 fuentes `woff2` propias. |
| **Número / sección** | El **kicker de Academy** (`.topic-kicker`, el «09 / 10 · Donde todo esto converge» de paradigmas): mono, mayúsculas, en oro, encima del título, con el formato `NN / 11 · sección` (ej. «01 / 11 · El límite no es la IA»). Promovido de `dw-academy.css` a `content.css` (Academy lo sigue consumiendo por `@import`). `.topic-num` (numeración mínima) sigue existiendo para la guía del Copiloto, pero el documento ya no la usa. Los números internos de tarjetas, pasos y diagramas quedan como están. |
| **Bloques / tarjetas** | `.dw-card` (content.css): borde fino, radio 14px, fondo de tarjeta y el hover de Academy (borde oro y 2px hacia arriba), **sin glow ni inclinación 3D**. Comparte una sola definición con `.finale-card` de Academy (mismo fondo/borde/radio y mismo `:hover`). Las tarjetas ya no son un bloque unido por una línea de 1px: van separadas (gap 16px). Se conserva el revelado al hover de «Por qué BDC» y el realce de las tarjetas destacadas. |
| **Acento del título** | «datos» (y los demás acentos) en `var(--gold)` pleno (#E7B008, el del helper `{dw-gold}`); se retiró el degradé con brillo animado. |
| **Nitidez de los títulos** | Las animaciones de entrada ya no usan `animation-fill-mode: both` sino `backwards`: con `both` el contenedor queda con un `transform` identidad (capa compuesta) y el texto se rasteriza más borroso que en Academy; con `backwards` el elemento vuelve a su estilo base al terminar (ningún ancestro del título retiene un `transform`). |
| **Punches / callouts** | `aside.callout.callout--punch` del Hub (s2, s3, s4, s9, s11), sin bloques paralelos con borde propio. |
| **Entrar y saltar de sección** | «Ver la presentación» y los diez «Siguiente · título» usan el **cue de scroll** de la landing del Hub (`.scroll-cue`: etiqueta mono en mayúsculas + línea vertical animada, sin caja de botón), promovido a `content.css` con los valores de `brand-hero.css`. Sin desplazamiento magnético (se retiró el JS); el cue suma foco visible y realce al hover (la landing no los define). |
| **Cue de entrada anclado al fin de pantalla** | La portada mide exactamente la pantalla (`box-sizing:border-box; min-height:100svh`, como `.screen` de la landing; con `content-box` el padding se sumaba y la portada medía 100vh + 200px) y «Ver la presentación» cuelga de ella a 2,5rem del borde inferior (`position:absolute`, igual que `.hero-scroll`). Los «Siguiente · título» van **en el flujo** al final de cada sección: si caen fuera de la pantalla se cortan, pero nunca se superponen al contenido ni se arrastran al hacer scroll (se probó un `position: sticky` y se descartó a pedido: flotaba sobre el contenido). |
| **Glow del puntero de la portada** | El de la landing/login del Hub (`brand-hero.css .hero-gradient`): círculo de 1300px, oro 5% → 2% → nada, siempre visible y siguiendo al puntero (con puntero fino), en lugar del foco de 360px que se encendía y apagaba. |
| **Rail de secciones (puntos de la derecha)** | El `.rail` de Danwor Academy, promovido de `dw-academy.css` a `content.css` (Academy lo sigue consumiendo por `@import` y conserva solo su política de ocultar los textos bajo 1600px): puntos de 9px **sin glow**, activo en oro, y el texto de cada sección como **tooltip al pasar el mouse** (en la presentación el label de la sección activa no queda fijo). Se oculta bajo 900px, como en Academy. |

**Evidencia de propagación** (`_handoffs/PRESENTACIONES_V1_20261028/homolog/propagation.json`): se editaron de forma temporal `content.css`, `app.css` y `tools.css` (restaurados por hash) y el cambio llegó, sin tocar el CSS de la presentación, a fondo, oro, fuente, `.callout--punch`, `.theme-toggle`, al cue de scroll, al kicker, a las tarjetas (también las `.finale-card` de Academy), al rail (también el de Academy) y al viewer embebido.

**Diferencias intencionales respecto del HTML original:** tipografía y superficies del Hub; dorado `--content-accent` (`#e7b008`) en lugar de `#EBBF08`; numeración con `.topic-num` (encima del título); cue de scroll de la landing en lugar de botones; rail de Academy en lugar de los puntos con glow; fondo oscuro = canvas del Hub; kicker de Academy en lugar de la numeración mínima; tarjetas separadas con el hover de Academy (sin inclinación ni glow); «datos» en oro pleno; punches como callouts; logo de Danwor de Academy; tema claro disponible. Se preservan composición, storytelling, arquitectura BDC, tabs, diagramas, marquee, scrollspy y las animaciones narrativas.

**Excepciones y límites documentados (no resueltos aquí a propósito):**
* `.btn-secondary` del Hub no tiene variante para tema claro (cada vertical lo parchea; p. ej. `human_capital.css`): dentro de las secciones del documento `--line`/`--soft` se remapean una vez a sus equivalentes por tema.
* Danwor Academy sigue declarando su propia tipografía (`--sans` y un `<link>` propio): su fuente no se centraliza en este ticket.
* Los valores oscuros de `content.css` coinciden con la paleta de `app.css :root` por diseño, no por derivación (esos tokens son invariantes al tema).
* El dorado de acento sobre fondo blanco es la convención del propio Hub (eyebrows, `.topic-num` aparte); se usa para énfasis, nunca como único portador de información.
* `brand-hero.css` (la landing, una hoja autónoma que no carga el CSS del Hub) conserva su copia `.hero-scroll` del cue: mismos valores, pero no es el mismo selector. Unificarla exige que la landing cargue `content.css`.
* El toast `.cp-toast` repite el posicionamiento del aviso de CCHH: promoverlo a `tools.css` es la corrección real y queda como decisión pendiente.
* Con el cue y el rail el documento ya no contiene `.btn-primary`/`.btn-secondary`; los botones institucionales que consume son el toggle de tema (`.theme-toggle`) y, en las pantallas del Hub, los de catálogo, detalle y editor.

**Paridad.** Narrativa y funcional (contenido, secciones, tabs, scrollspy, marquee, animaciones, modo presentar), **no** igualdad de píxeles en lo homologado a propósito (tipografía, superficies, acento, numeración, botones, callouts, logo, tema). La comparación píxel a píxel contra el HTML original de la primera entrega (0,0 en las 12 secciones, con solo logo y numeración adaptados) quedó superada por esta homologación; la sustituyen los gates de estilo, theme y narrativa descritos arriba.

## Edición inline (como «02. Estrategia»)

«Editar» ya no abre un formulario: abre **la presentación misma** (el documento real, en un iframe del mismo origen con `?embed=1&edit=1`) y los textos habilitados (206, ver «Edición de las 12 slides») son leaves `contenteditable` de **texto plano** en su lugar, con el contorno punteado discreto de Estrategia (`.dw-inline-edit`, content.css: mismo look que `.cs-inline`, que conserva su definición y su test; unificarlas queda como seguimiento). La barra vive en el Hub: **Cancelar** · badge «Sin guardar» (`.status-badge--tentativo`) · **Guardar** (deshabilitado sin cambios).

* **Autorización:** `edit=1` solo se honra en R1 si además viene `embed=1` y el usuario **puede editar esa instancia** (Comercial + responsable / `CONTENT_OWNER`; nunca la semilla). Para cualquier otro la URL con `edit=1` es la vista de siempre: leer por URL no habilita nada. El guardado sigue siendo R6, con las mismas validaciones y órdenes de enforcement.
* **Guardado:** manda solo los campos cambiados bajo la revisión que cargó la página; el servidor valida obligatorios, longitudes (muestra el mensaje y se puede reintentar), caracteres y propiedad; `409` por revisión desactualizada.
* **Texto plano de una sola línea:** el servidor no admite saltos de línea en ningún campo; por eso Enter no hace nada y pegar siempre inserta texto plano sin saltos (no entra HTML ni formato). `text-transform: none !important` evita que un texto en mayúsculas por CSS se guarde en mayúsculas.
* **Descartar:** «Cancelar» con cambios pide confirmación con el diálogo del Hub (`dwConfirm`); también avisa el `beforeunload` nativo si se cierra la pestaña con cambios.
* **El documento sigue siendo el mismo:** las diferencias en modo edición son solo el envoltorio de cada texto (`<span data-cp-field>`); el HTML de lectura no cambia.

## Edición de las 12 slides (contenido textual completo)

«Editar» personaliza el **contenido textual y los valores comerciales de las 12 slides**, no el DOM: cada texto editable es un campo de la spec (`VANTURE_FIELDS`, 206 en total: 20 de la v1 original + 186 agregados de forma aditiva, **sin subir `template_version`**).
Clasificación por naturaleza (`FieldSpec.kind`): **`line`** (títulos, etiquetas, nombres: 161) y **`paragraph`** (texto corrido: 45). Hoy ambas son de una sola línea: ningún texto del seed contiene un salto (lo verifica un test), el servidor sigue prohibiendo los saltos y habilitar «saltos de línea controlados»
sería un cambio local al validador y al render de los `paragraph` el día que haya un caso real (no se abrió la restricción de forma indiscriminada).

| Slide | Campos (existentes + nuevos) | Textos gobernados por el template (no son campos) |
|---|---|---|
| 1 Portada | 4 (3 + **1**: etiqueta dorada «SAP Business Data Cloud») | cue «Ver la presentación» |
| 2 El límite no es la IA | 13 (2 + 11): nombre, título, subtítulo, rótulo, 3 tarjetas (título + texto), dato, «Siguiente» | numeración 01–03 de las tarjetas |
| 3 Qué es SAP BDC | 8 (0 + 8): nombre, título, subtítulo + frase destacada, cierre (3 tramos), «Siguiente» | **bloque `bdc-architecture`** (23 textos de la arquitectura) |
| 4 Knowledge Core | 11 (0 + 11): nombre, título, subtítulo (5 tramos con 2 énfasis), rótulo, cierre (2 tramos), «Siguiente» | **bloque `knowledge-core-stack`** (16 textos) |
| 5 Ecosistema abierto | 11 (0 + 11): nombre, título, subtítulo, rótulo, 3 tarjetas, «Siguiente» | **bloque `open-ecosystem-flow`** (12 textos) · íconos de las tarjetas |
| 6 Casos de uso | 74 (0 + 74): nombre, título, subtítulo, rótulo, 5 solapas (nombre, cabecera, rótulos, casos de uso, insignia, productos de datos), 3 tarjetas, «Siguiente» | la estructura de solapas (cantidad de items por persona) |
| 7 Por qué BDC | 26 (0 + 26): nombre, título, subtítulo, 2 rótulos, 4 pilares (título + 4 afirmaciones del detalle), «Siguiente» | numeración 01–04 · el detalle refleja el título del pilar (espejo) |
| 8 Cómo empezar | 13 (0 + 13): nombre, título, subtítulo, rótulo, 4 pasos (título + texto), «Siguiente» | numeración 01–04 |
| 9 Tu punto de partida | 23 (0 + 23): nombre, título, subtítulo, rótulo, 3 caminos (rótulo, título, texto, «cuándo conviene» + su rótulo), cierre (3 tramos), «Siguiente» | — |
| 10 Conclusión y demo | 6 (4 + 2): nombre, título (2 tramos), bajada, texto del QR, «Siguiente» | imagen del QR |
| 11 Por qué nuestra alianza | 15 (11 + 4): nombre, título, 4 propuestas, reconocimientos (rótulo + 3 tramos), «Siguiente» | íconos · imagen de sellos |
| 12 Clientes y casos | 2 (0 + 2): nombre, título | **bloque `partner-logo-marquee`** (84 logos) |
| Transversal | — | riel (espejo del nombre de cada sección), pie legal (3 textos), numeración «NN / total» |

Ningún texto visible queda sin clasificar: `test_every_visible_text_is_editable_a_mirror_a_governed_block_the_legal_footer_or_declared_chrome` falla si aparece un texto nuevo que no sea campo, espejo, bloque, pie legal o cromo declarado.
**Espejos:** el nombre de cada sección aparece en la etiqueta superior y en el riel (se edita en la etiqueta y el riel lo refleja en vivo); el título de cada pilar, también en su detalle; la etiqueta dorada de la portada, en el encabezado de la pestaña propia.
**Edición:** los textos dentro de un enlace («Siguiente · …») se editan sin navegar; el de una solapa se edita sin que el `<label>` robe el foco (y activa la solapa); en el modo edición el detalle de cada pilar se apila bajo su título (al leer sigue siendo el revelado por hover).

### Bloques gobernados

Las visualizaciones especializadas son **bloques gobernados**: el template los dibuja, sus cajas y conexiones **no son campos**, y quedan identificados con el atributo inerte `data-cp-block` (no cambia el render ni agrega comportamiento):

| Bloque (variante) | Slide | Elemento | Textos del bloque / narrativa editable alrededor |
|---|---|---|---|
| `bdc-architecture` | 3 | `div.arch` (cuatro capas + los dos laterales) | 23 / título, subtítulo y cierre de la slide |
| `knowledge-core-stack` | 4 | contenedor del stack de 3 capas con sus conectores | 16 / título, subtítulo, cierre; el rótulo «KNOWLEDGE CORE» (campo editable) vive en la región, fuera del elemento |
| `open-ecosystem-flow` | 5 | `div.flow-grid` (plataformas → BDC Connect → lo que se construye) | 12 / tarjetas y subtítulo; el espaciador de 44 px vive en la región |
| `partner-logo-marquee` | 12 | `div.logo-marquee` | solo imágenes |

*Precedentes en el Hub:* no existe ninguno de selección/sustitución de componentes **por instancia**. Los más cercanos son preferencias por usuario en el navegador (anchos de columna, versionadas por `layout.version`) y la **referencia por id** (`template_id` + `template_version` de esta misma entidad; packs gobernados en Contratos Gobernados): se reutiliza ese patrón, nunca HTML guardado.
*Reutilización real hoy:* la arquitectura BDC y el stack de Knowledge Core son el **mismo componente** (`layer-stack`, un renderer con parámetros distintos); el flujo y el marquee son componentes propios. Quitar, restaurar, reemplazar y agregar sobre estas regiones están implementados: ver «Add-ons» más abajo. Ocultar un bloque se verificó en navegador antes de implementarlo (ancla, riel y «Siguiente» siguen resolviendo); el límite de la región incluye el rótulo de la slide 4 y el espaciador de la slide 5 para no dejar huérfanos.

### Eliminar slides de una copia

En el modo edición cada slide (menos la portada) muestra una **✕ arriba a la derecha**. Al pulsarla la slide queda colapsada en una barra «Slide eliminada · Restaurar» (reversible hasta que se guarda y también después: al reabrir el editor siguen ahí para restaurarlas).
* **Numeración dinámica:** el kicker «NN / total», el riel de puntos, los enlaces «Siguiente» y el cue de la portada se recalculan sobre las slides **visibles** (`01/09 … 09/09` si se quitan dos). Si el siguiente original ya no existe, «Siguiente» apunta a la siguiente visible y nombra **esa** slide; la última visible no ofrece «Siguiente». El servidor aplica la misma regla al renderizar la lectura (y el editor, en vivo).
* **Persistencia:** `removed_slides` (lista de ids, opcional) dentro de la misma instancia; sin slides eliminadas el archivo conserva la forma de siempre. Se guarda con el mismo R6 (`removed_slides` en el payload, aun sin campos cambiados), con la misma revisión, validación atómica (lista, ids de slide quitable, sin repetidos; `s1` no) y autorización. «Sin guardar» también se activa por quitar/restaurar una slide.
* **Lectura:** las slides eliminadas **no se renderizan**: su texto no viaja en el HTML. La copia de una copia hereda la composición del origen y es independiente después.

## Add-ons: componentes gobernados, regiones y QR

**Qué es:** se reutilizan composiciones visuales ya existentes (no hay editor drag-and-drop ni HTML/CSS/JS aportado por usuarios). Cuatro conceptos, todos en el repo:

* **Componente** (código compartido: macro Jinja + CSS de `vanture.css`): `layer-stack` (un único renderer parametrizado), `ecosystem-flow`, `logo-marquee`. Los conectores animados son parte interna de `layer-stack` y de `ecosystem-flow`.
* **Variante** (set de parámetros institucional): `layer-stack` tiene `bdc-architecture` y `knowledge-core-stack` (`webapp/services/commercial_presentations_data/addons.yaml`, validado al cargar: tonos, rangos y textos cerrados); `ecosystem-flow` y `logo-marquee` tienen su única variante (`open-ecosystem-flow`, `partner-logo-marquee`). El id de variante es el `data-cp-block` que el documento ya llevaba.
* **Región** (lugar declarado del template, con **un solo ocupante**): tipo `body` (dentro del contenedor de la slide) o `bleed` (ancho completo). Hay nueve: las cuatro existentes (`s3.body` arquitectura BDC, `s4.body` stack Knowledge Core con su rótulo, `s5.body` flujo con su espaciador, `s12.bleed` marquee) y cinco `body` que **nacen vacías** (`s2`, `s6`, `s7`, `s8`, `s9`): mientras estén vacías no emiten markup ni alteran el layout; al ocuparse llevan la separación estándar de 56 px al final del contenedor.
* **Uso en la instancia:** `blocks` = solo los **desvíos** del ocupante por defecto de cada región (`null` = quitado); ids, nunca markup. Sin desvíos la clave no existe.

**Compatibilidad = solo el tipo de región** (`body` admite `layer-stack` y `ecosystem-flow`; `bleed` admite `logo-marquee`); no hay reglas semánticas por slide. El servidor valida región, componente, variante y tipo (422 atómico, sin reflejar el valor). La biblioteca ofrece únicamente composiciones reales y registradas.

| Componente | Quitar / Restaurar | Reemplazar | Agregar |
|---|---|---|---|
| `layer-stack` (2 variantes) | sí | por cualquier componente de región `body` | en regiones `body` |
| `ecosystem-flow` | sí | por cualquier componente de región `body` | en regiones `body` |
| `logo-marquee` | sí (solo `s12.bleed`) | sin alternativa en `bleed`: no se ofrece | solo en `bleed` |

**Experiencia (solo modo edición):** cada bloque queda dentro de un **borde punteado discreto** (`outline`: no toca el layout ni las animaciones) con una **✕ contextual en su esquina superior derecha** que **abre un diálogo y no elimina nada** (la ✕ de eliminar la slide es otra, arriba a la derecha de la slide, y se conserva). El diálogo es el `<dialog class="dw-confirm busqueda-dialog">` del Hub, el mismo patrón que «Agregar candidato desde el Banco» de CCHH (las reglas `.busqueda-dialog` y `.busqueda-picker` se promovieron de `human_capital.css` a `tools.css`; estilos computados de CCHH idénticos): lista las **alternativas compatibles con el tipo de región** que existen en el registro (la original marcada), con la **vista previa del componente real** (se clona la misma `<template>` que se insertaría, reducida para entrar; no hay miniaturas independientes). Seleccionar solo cambia la vista previa; **Cancelar / Escape no modifican nada ni marcan «Sin guardar»**; «Reemplazar» (o «Agregar» si la región está vacía) aplica solo a esa región; «Quitar componente» la deja vacía con un affordance compacto «+ Agregar componente» (que abre el mismo diálogo) y «Restaurar original» (reversible). El diálogo avisa, sin bloquear, que *los títulos y textos de la slide no se actualizan automáticamente*. Todo se integra con «Sin guardar», Cancelar y Guardar de la edición inline; el editor nunca arma markup (clona) y el servidor revalida todo al guardar. Quien no puede editar nunca recibe la ✕, el diálogo ni las plantillas.

**Integridad:** el seed conserva paridad exacta (DOM idéntico y capturas idénticas byte a byte en desktop/mobile, claro/oscuro, para s3, s4, s5 y s12); `removed_slides`, numeración, riel, anclas y «Siguiente» no cambian con la composición (las regiones de una slide eliminada se van con ella y vuelven al restaurarla); la copia hereda `blocks` como snapshot y queda independiente; las instancias anteriores renderizan la composición por defecto; la lectura de `blocks` es tolerante (lo inválido se ignora entrada por entrada) y el guardado es estricto.

**QR intercambiable (asset, no arquitectura):** reutiliza el uploader del logo del cliente, sin duplicarlo. El validador PNG se factorizó en `validate_png(data, *, noun, require_alpha, square)`: el **logo** sigue exigiendo transparencia; el **QR** (`validate_qr_png`) pide PNG íntegro (firma, CRC, IEND), no animado, ≤ 1 MB, ≤ 1,5 M de px, **cuadrado (±10 %)** y **no** exige transparencia (un QR suele ser opaco y el documento lo apoya sobre un tile blanco). Se reutilizan la escritura atómica, el orden «asset primero, instancia después», la herencia en la copia (snapshot), la limpieza al eliminar, el servido con `Content-Security-Policy: default-src 'none'; sandbox` y el contrato de lectura de R8. El asset es `<uuid>.qr.png` junto a la instancia (mismo directorio ya persistido: **sin repositorio de medios ni variables nuevas**) y la instancia guarda `qr: {sha256, bytes, width, height}`. La ruta la deriva el servidor del uuid validado: no hay rutas arbitrarias ni acceso al asset de otra instancia. El QR se aplica aparte de «Guardar» (R9, con la revisión vigente); los textos sin guardar siguen en pantalla.
*Fallas entre publicar el asset y guardar la referencia:* disco y Spaces vuelven a la versión anterior (se reenvía el QR/instancia previos o se retira el asset huérfano); si ni la compensación se puede confirmar, el resultado es **incierto** (503 `outcome_unknown` con aviso) y nunca se presenta un éxito parcial como éxito. Una referencia cuyo archivo se perdió vuelve al QR institucional (nunca una imagen rota).

**Autorización:** la de siempre. Usar componentes y cambiar el QR = poder editar esa instancia (responsable o `CONTENT_OWNER`); `APP_OWNER` no obtiene edición de contenido ajeno; el seed y las definiciones institucionales no se editan (cambian por código y deploy). Sin variables, roles ni allowlists nuevos.

**Guardar como:** se materializa en la colección «Componentes reutilizables» (siguiente sección): «Duplicar como propio» guarda un componente con nombre, identidad, responsable, procedencia y parámetros independientes, con alcance privado inicial y **nunca HTML/CSS/JS**.

## Componentes reutilizables (segunda colección)

`/comercial/presentaciones` administra **dos colecciones** con los mismos patrones (filtro compartido, agrupador `.dashboard-section` con contador, tabla `.matrix-scroll`, acciones siempre presentes y grisadas con motivo, estado vacío del Hub y tema): **Presentaciones** y **Componentes reutilizables**.
El modelo distingue tres cosas:

1. **Definición visual gobernada** (código y deploy): el renderer compartido y su **contrato de parámetros**. Para `layer-stack` (`validate_layer_stack_params`, el MISMO validador del YAML institucional, de los componentes propios y de los snapshots): tonos y rangos cerrados, textos de una línea (≤ 120), colecciones **ordenadas** de capas (≤ 6) y cajas (≤ 6). `ecosystem-flow` y `logo-marquee` no tienen parámetros: se ven y se usan, no se duplican ni se editan.
2. **Componente catalogado**: identidad (uuid), nombre, descripción, parámetros, responsable (`owner_id`/`created_by_*`), procedencia (`based_on`: de qué institucional o propio salió), revisión y fechas. Los institucionales **no se guardan**: salen del registro (`addons.yaml`). Los propios viven en `commercial_strategy_runtime/presentations/components/<uuid>.json` (mismo directorio persistido, subdirectorio aparte; escritura atómica, push y compensación del contrato de siempre; **sin repositorio nuevo, variables ni roles**). Topes: 30 por usuario y 200 en total.
3. **Uso en una presentación**: `blocks[region]` guarda `{component, variant: "own:<uuid>@<revisión>", params, from: {id, revision, name}}`: un **snapshot independiente** de los parámetros. Las instancias anteriores y los usos institucionales (`{component, variant}`) no cambian.

**Autorización (D1/D2, sin roles ni variables):** los propios son **privados para su responsable y para `CONTENT_OWNER`** autorizado en Comercial; el filtro se aplica en el SERVIDOR (lista, vista previa, uso, edición y baja: un componente ajeno es 404, no se distingue de uno inexistente; `APP_OWNER` tampoco lo ve por ser administrador). Ver/usar/editar/eliminar un propio = el mismo conjunto (responsable o `CONTENT_OWNER`). Los institucionales se ven y se duplican; **solo el código los cambia** (los propios almacenan parámetros validados, nunca HTML/CSS/JS).

**Flujo:** Ver (vista previa con el renderer real: el mismo documento y el mismo macro de la presentación, sin el resto de la slide, en un iframe) → Duplicar como propio (nombre «Copia de …», descripción opcional) → Editar textos y tonos (formulario generado desde el contrato; **la estructura —capas, cajas, laterales, conectores, rangos— no se edita en esta entrega**: ni reordenar ni eliminar elementos internos) → Guardar (revisión optimista, 409 si cambió) → aparece en el agrupador → el diálogo contextual de add-ons lo ofrece (etiqueta «propio») con su vista previa real → Aplicar y Guardar.

**Snapshot, no referencia viva:** al elegir un componente propio el navegador manda solo `{component, variant}`; el **servidor** adjunta el snapshot de los parámetros vigentes (si el cliente intenta aportar `params`/`from`, 422; si la revisión elegida ya no es la vigente, 422; si no puede verlo, 422). Si la región ya tenía ESA misma variante se conserva SU snapshot guardado. Modificar después el original **no altera** un uso ya insertado: el diálogo ofrece la versión vigente («propio») y la que está en uso («en uso · rev. N»), y recién al elegir la vigente el uso se actualiza. **Eliminar** el componente tampoco rompe nada: las presentaciones (y sus copias, que heredan el snapshot) siguen renderizando; su uso queda como «en uso» y puede conservarse o reemplazarse.

**Fuera de esta entrega (modelado, no implementado):** reordenar o eliminar capas/cajas internas, y compartir un propio con otros usuarios de Comercial.

## Logo del cliente al copiar

El diálogo «Copiar como nueva» incluye un **upload opcional** de logo: reemplaza al logo de **Vanture** (el co-branding del header y del pie del documento; el logo institucional de Danwor y el de la sección de alianza, donde Vanture es el sujeto, no se tocan). Selector nativo de archivo con el look de Capital Humano (promovido a `tools.css`, ver más abajo).

* **Contrato del archivo:** PNG **con fondo transparente**, hasta 1 MB y 1,5 millones de píxeles. El servidor valida *todo* (el cliente solo hace un chequeo rápido): firma PNG, CRC de cada bloque, `IEND`, no animado y **transparencia real** (canal alfa o `tRNS` y al menos un píxel transparente, decodificando hasta encontrar el primero). Si falla, la copia **no se crea** y el diálogo muestra el motivo. No se re-codifica nada: se guarda tal cual se subió.
* **Transporte:** multipart (`título` + `logo`) a `POST …/copiar`; el JSON `{title}` sigue siendo válido (sin logo).
* **Almacenamiento:** `commercial_strategy_runtime/presentations/<uuid>.logo.png`, junto al JSON y bajo el mismo directorio ya persistido (sin variable ni ruta nuevas); el JSON guarda `logo: {sha256, bytes, width, height}`. En `remote` se sube primero el logo y después la instancia (nunca hay instancia remota sin su logo); si algo falla, se descartan ambos archivos. Eliminar la presentación elimina su logo (y lo restaura si el borrado remoto no se confirma).
* **Herencia:** la copia de una copia hereda el logo del origen salvo que se suba uno nuevo. La semilla conserva siempre el original.
* **Lectura:** R8 (`/presentaciones/{id}/logo?v=<sha>`), PNG con `nosniff`, `Cache-Control: private, no-store` y CSP restrictiva (`default-src 'none'; sandbox`).
* **Fuera de alcance (V1):** cambiar el logo de una copia ya creada (editor) y recortar/redimensionar.

## Catálogo (`/comercial/presentaciones`): homologación con el Hub

Cada elemento se resolvió como *elemento actual → precedente del Hub → clase reutilizada → ajuste mínimo*. No hay primitivos nuevos: lo único propio es el filtro en el cliente.

| Elemento | Precedente | Reutiliza | Ajuste |
|---|---|---|---|
| Cabecera / subtítulo | Dashboard CRM (`crm.html`) | `.module-hero` > `.hero-lead-row` > `p.hero-lead.full-page-subtitle` | Solo markup (antes el `<p>` era hijo directo del hero y lo tomaba `.module-hero > p:not(.eyebrow)`, más chico y apagado). Sin CSS. |
| Filtro | CCHH → Base de CVs | `form.hc-profile-filters.filter-toolbar-card` + `.feedback-field--wide`, *antes* del agrupador | Solo «Buscar» (`Título, descripción, autor...`). El *look* de los controles pasó de `human_capital.css` a `tools.css`, junto a su layout, **sin cambiar reglas** (226 controles de CCHH comparados antes/después: 0 diferencias). Cargar `human_capital.css` no era opción: redefine el `h1` del hero y rompe la paridad con el CRM. |
| Agrupador y contador | CCHH → Candidatos | `details.dashboard-section` (+ `__head`, `__caret`, `__title`, `__count`, `__body`) con la tabla adentro | Ninguno. El contador usa el primitivo de `tools.css` tal cual (como Staffing); Candidatos lo afina en su propia hoja (peso 600 y tono más claro en oscuro): diferencia residual mínima. |
| Sin resultados | CCHH → Candidatos (`no_matches`) | `.dashboard-empty` | Oculto de entrada; reemplaza a la tabla dentro del agrupador. |
| Selector de archivo (logo) | CCHH → documentos del candidato / importación | `.feedback-field input[type="file"]` | El look (padding, borde fino, radio 8px, `.8rem`) pasó de `human_capital.css` (donde se repetía por formulario) al campo genérico de `tools.css`, **con los mismos valores** (los `input[type=file]` de CCHH: 0 diferencias computadas). Aclaración con `.dashboard-empty__hint`. |
| Acciones del detalle | CRM → Actividades › Período | `.dashboard-view-switch` (+ `--scroll`) con `.is-active` en «Presentar» | `tools.css`: los ítems `<a>` del primitivo (antes solo `button`; CCHH lo replicaba solo para `.busqueda-views a`). Verificado ítem a ítem contra el segmentado del CRM, en claro y oscuro. |
| Respiro hero → filtro y filtro → agrupador | `.module-hero + #hc-…-panel` (24px) y `#hc-profiles-panel > * + *` (26px) | Misma convención por-panel | `#cp-catalog-panel` + 2 reglas en `commercial.css`. |

**Búsqueda.** 100 % cliente (`commercial_presentations.js`): sin endpoint, parámetros ni preferencias persistidas. Normaliza con el helper de búsqueda libre del Hub, `dwSearchFold` (app.js: sin mayúsculas ni tildes, `ñ`≡`n`, espacios recortados y colapsados), y compara contra `data-cp-search` de cada fila (**título + responsable**, el mismo texto de la columna «Responsable»); nunca contra el HTML ni la narrativa del documento. El modelo no tiene descripción, por eso el placeholder acordado la nombra pero hoy solo matchean título y responsable. El contador muestra el total (semilla + copias) con la búsqueda vacía y los coincidentes con búsqueda activa («1 presentación» / «N presentaciones»).

**Propagación.** Un cambio en `.hero-lead`, `.dashboard-section__*` o `.hc-profile-filters` de `tools.css` llega a CRM/CCHH **y** a Presentaciones sin tocar `commercial.css` (verificado editando `tools.css` de forma temporal y restaurándolo por hash).

## Pulido final (catálogo, componentes, portada, pie y reveal)

* **Fechas sin valor:** «Última edición» de lo que no tiene fecha propia (la semilla y los componentes institucionales) muestra `RELEASE_DATE_LABEL` (`29-10-2026`, fecha de liberación; se ajusta si hay nuevos cambios). Solo se muestra: no se escribe nada.
* **Dos tablas, un contrato:** Presentaciones y Componentes reutilizables comparten `.dashboard-table.cp-catalog` (anchos 34/22/16/12 % + Acciones 252 px, alineación y tipografía) y las mismas cinco columnas; Componentes ya no tiene «Procedencia» (el «Basado en …» va como rótulo bajo el nombre). «Componentes reutilizables» arranca **colapsado**; «Presentaciones» abierto (`<details class="dashboard-section">` nativo).
* **Etiquetas:** responsable de la presentación institucional = «Juan Manuel Melgarejo»; de los componentes institucionales = «Danwor». Son solo rótulos (ownership y autorización intactos; los componentes propios conservan su metadata). Sin badge «Institucional» en ninguna colección; la inmutabilidad se ve en las acciones deshabilitadas.
* **Vista previa de componentes a escala:** el documento de vista previa se maqueta al ancho de referencia de la slide (1260 px) dentro de un iframe del mismo ancho y se escala proporcionalmente al ancho disponible (`fitPreview`). El responsive de la presentación real no se toca; el renderer es el mismo.
* **Scrollbar del documento embebido:** lo que scrollea en el editor es el viewport del iframe; `html.content-doc` se suma al selector de la regla de scrollbar del Hub (`tools.css`), sin colores propios.
* **Portada:** en tema claro la órbita del hero usa el gris suave del token de los indicadores laterales (`.content-theme-light .hero-orbit`, solo el color); punto dorado, geometría, grosor y movimiento intactos; el tema oscuro no cambia. El punch «7 de 10» baja ×0,83 (`clamp(46px,6.64vw,86px)`).
* **Pie:** sin la frase «Data & AI for Business.» bajo los logos; el aviso ocupa el resto de la fila (`flex:1 1 560px`, sin alto mínimo) y los logos quedan centrados respecto de su caja; en móvil la caja baja debajo de los logos.
* **Reveal por scroll (sin segundo motor):** la presentación ya revela por scroll con `animation-timeline:view()` inline en títulos, párrafos, bloques y add-ons (el hero conserva su entrada inmediata). Se reutiliza tal cual y solo se cierran dos brechas, ambas en `vanture.css`: con `prefers-reduced-motion` y en modo edición esos elementos quedan en su estado natural (`animation:none`), de modo que nada se oculta ni se desplaza y las acciones/selección no se ven afectadas. Las vistas previas del catálogo ya lo desactivaban; las animaciones internas (órbitas, conectores, marquee) y la barra de progreso (`scroll(root)`) no se tocan. El reveal es ligado al scroll (reversible), el mismo contrato que el Copiloto; las guías prácticas animan una sola vez, pero eso exigiría JS propio.

## Seguridad de salida

* Todo campo es un `str` validado (strip, largo máximo, sin caracteres de control, U+2028/2029 ni controles bidi); las claves desconocidas dan 422 (sin reflejar su nombre).
* Se emite SOLO como **texto de elemento** con el autoescape de Jinja: sin `|safe`, `Markup`, markup, enlaces, atributos ni URLs editables. Un campo en otro contexto exigiría un tipo de contexto nuevo con su propio validador y su propio test.
* Lo garantizan los tests: centinela por campo con BeautifulSoup (solo en nodos de texto), payloads hostiles, grep del template (sin construcciones inseguras; usa exactamente las claves de la spec), autoescape activo.

## Evolución del template (no rompe instancias en silencio)

| Cambio | Qué se hace |
|---|---|
| Aditivo: campo nuevo con default en el seed; cambios de markup, CSS o JS | No sube `version`. Las instancias viejas siguen funcionando (campo faltante → default del seed). |
| Roto: renombrar, quitar, cambiar tipo o **reducir un límite** | Sube `TemplateSpec.version` y se escribe `upgrade(content, from_version)` (en memoria al leer; se persiste en el próximo guardado). |
| Instancia con `template_version` mayor que la soportada | 409 claro (R1/R3); el catálogo la marca «Template no disponible». |
| `template_id` desconocido | 404; no rompe el catálogo. |

Candados en CI: fixture dorado `tests/fixtures/presentations/vanture_bdc_v1_instance.json` (debe validar y renderizar siempre), `PINNED_V1` y `vanture_bdc_v1_field_limits.json` (las 206 claves y sus límites solo pueden crecer), y el test que exige un `upgrade()` explícito cuando `version > 1`. La fixture dorada conserva sus 20 campos a propósito: es la prueba de una instancia creada con el esquema anterior (renderiza las 12 slides y se puede editar).

## Persistencia

Mismo contrato que `profile_repository`: un JSON por entidad, escritura atómica, push síncrono, compensación. `read-only` rechaza antes de tocar el disco; `local` no sincroniza; `remote` exige push (copia: un reintento y, si no confirma,
retira el archivo y avisa resultado incierto; guardado: restaura la versión anterior y responde 503). El restore de arranque se espera antes de leer. Réplica única (`docs/PUBLICATION_SAFE_RUNBOOK.md`): el lock es por proceso.

**Ubicación (decisión de infraestructura, no de pertenencia funcional a Estrategia):** `commercial_strategy_runtime/presentations/`. Ese directorio ya está en `config.PERSIST_PATHS` y en el valor documentado de `SPACES_PERSIST_PATHS` (`.env.example`),
y `restore`/`mirror_up`/`push_path` cubren los subdirectorios; **no se agregó ninguna variable ni ruta de persistencia**.

> **Verificación previa al deploy (no resoluble desde el repo):** si en DigitalOcean `SPACES_PERSIST_PATHS` está definida explícitamente, debe incluir `commercial_strategy_runtime`. Estrategia ya depende de esa entrada para ser durable, así que se espera que esté;
> confirmarlo en el panel de DO antes de desplegar y con `DANWOR_PERSISTENCE=remote`.

## Limitaciones conocidas (heredadas del original o de V1)

* El revelado por hover de «¿Por qué SAP BDC?» depende del foco en pantallas táctiles (limitación del original).
* Usa `animation-timeline` (animaciones ligadas al scroll): sin soporte fuera de navegadores Chromium actuales el contenido se muestra sin esa animación (heredado del original). Con `prefers-reduced-motion` y en modo edición se muestra siempre sin ella.
* Barra superior y pie siguen siendo oscuros en ambos temas (co-branding con logos pensados para fondo oscuro); el cuerpo del documento sí cambia de tema.
* QR estático; marca Vanture y logos de clientes heredados por todas las copias (sin neutralidad de marca ni personalización irrestricta).
* «Copiar enlace» es el único sharing de V1 (sin permisos individuales, publicación anónima ni envío externo).
* Tope de 200 instancias (`MAX_INSTANCES`).
* En pantallas muy angostas el texto «SAP BUSINESS DATA CLOUD» de la barra superior se superpone al logo de Vanture: ya ocurre en el HTML original (paridad exacta con él) y V1 no lo modifica.

## Cómo agregar un campo o un template

* **Campo aditivo:** agregarlo a `VANTURE_FIELDS` (con `kind`), al seed YAML (valor no vacío) y al template (`{{ f('<clave>') }}` en posición de texto; el test de cobertura exige clasificar todo texto visible). Sin subir `version`; las instancias existentes toman el valor del seed. Para fijar su límite, regenerar `tests/fixtures/presentations/vanture_bdc_v1_field_limits.json`.
* **Template nuevo:** entrada en `TEMPLATES`, seed, Jinja standalone (sin `extends`), assets bajo `assets/presentations/<id>/`, y su fixture dorado. Las superficies del Hub (catálogo, detalle, editor) no cambian.
