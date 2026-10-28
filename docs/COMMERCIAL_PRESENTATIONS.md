# Gestión Comercial → Presentaciones (V1)

Presentaciones comerciales inmersivas gobernadas por el Hub: se **ven y presentan** por URL, se **copian** como instancias propias y se **editan** (campos de texto acotados) con control de revisiones.
Primer template: `vanture-bdc` ("La empresa autónoma comienza con los datos", Danwor × Vanture).

**Estrategia (extracción mínima).** El template conserva TODO el markup, CSS y JS del HTML original; solo 20 campos de texto plano pasan a contenido editable de la instancia. No hay renderer universal,
bloques genéricos, SPA ni persistencia transversal nueva. Las composiciones especializadas (arquitecturas SAP BDC, flujo BDC Connect, pestañas por perfil, marquee) son markup del template.

## Rutas y autorización

| # | Ruta | Qué hace | Autorización |
|---|---|---|---|
| R1 | `GET /presentaciones/{id}` | Documento inmersivo (ver y presentar). `?embed=1` oculta la barra de co-branding. | Sesión + `auth_service.is_authorized` (`ALLOWED_USERS` ∪ `ACADEMY_USERS`). **No exige Comercial.** |
| R2 | `GET /comercial/presentaciones` | Catálogo (seed + instancias) | Comercial |
| R3 | `GET /comercial/presentaciones/{id}` | Detalle: shell del Hub + iframe de R1 | Comercial |
| R4 | `POST /comercial/presentaciones/{id}/copiar` | Copia como instancia propia | Comercial (cualquier presentación; sin requisito adicional) |
| R5 | `GET /comercial/presentaciones/{id}/editar` | Editor INLINE: el documento real en modo edición + barra Cancelar · «Sin guardar» · Guardar | Comercial + poder editar esa instancia |
| R6 | `POST /comercial/presentaciones/{id}` | Guarda campos (JSON `{revision, fields}`) | ídem R5 + `same_origin_guard` |
| R7 | `POST /comercial/presentaciones/{id}/eliminar` | Elimina la instancia (borrado real; el enlace compartido pasa a 404) | Comercial + poder eliminar esa instancia + `same_origin_guard` |
| R8 | `GET /presentaciones/{id}/logo` | PNG del logo propio de una copia (404 si no tiene) | MISMO contrato de lectura que R1: sesión + `is_authorized`, **sin** exigir Comercial |

* **Copiar no implica editar.** Editar = Comercial **y** no-seed **y** (`owner_id` == usuario **o** `CONTENT_OWNER` **o** `APP_OWNER`). `CONTENT_OWNER` es la capacidad transversal de gobierno de edición de contenidos del Hub (no se creó ninguna variable, rol ni allowlist nueva).
* **Eliminar** = Comercial **y** no-seed **y** (`owner_id` == usuario **o** `APP_OWNER`). Es más estricto que editar a propósito: `CONTENT_OWNER` edita contenido ajeno pero no lo elimina. `APP_OWNER` (superusuario de los workspaces del Hub, `access.is_owner_user`) habilita **cualquier acción** sobre cualquier copia; la semilla institucional sigue siendo inmutable e imborrable para todos.
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

«Editar» ya no abre un formulario: abre **la presentación misma** (el documento real, en un iframe del mismo origen con `?embed=1&edit=1`) y los 20 textos habilitados son leaves `contenteditable` de **texto plano** en su lugar, con el contorno punteado discreto de Estrategia (`.dw-inline-edit`, content.css: mismo look que `.cs-inline`, que conserva su definición y su test; unificarlas queda como seguimiento). La barra vive en el Hub: **Cancelar** · badge «Sin guardar» (`.status-badge--tentativo`) · **Guardar** (deshabilitado sin cambios).

* **Autorización:** `edit=1` solo se honra en R1 si además viene `embed=1` y el usuario **puede editar esa instancia** (Comercial + responsable / `CONTENT_OWNER` / `APP_OWNER`; nunca la semilla). Para cualquier otro la URL con `edit=1` es la vista de siempre: leer por URL no habilita nada. El guardado sigue siendo R6, con las mismas validaciones y órdenes de enforcement.
* **Guardado:** manda solo los campos cambiados bajo la revisión que cargó la página; el servidor valida obligatorios, longitudes (muestra el mensaje y se puede reintentar), caracteres y propiedad; `409` por revisión desactualizada.
* **Texto plano de una sola línea:** el servidor no admite saltos de línea en ningún campo; por eso Enter no hace nada y pegar siempre inserta texto plano sin saltos (no entra HTML ni formato). `text-transform: none !important` evita que un texto en mayúsculas por CSS se guarde en mayúsculas.
* **Descartar:** «Cancelar» con cambios pide confirmación con el diálogo del Hub (`dwConfirm`); también avisa el `beforeunload` nativo si se cierra la pestaña con cambios.
* **El documento sigue siendo el mismo:** las diferencias en modo edición son solo el envoltorio de cada texto (`<span data-cp-field>`); el HTML de lectura no cambia.

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

Candados en CI: fixture dorado `tests/fixtures/presentations/vanture_bdc_v1_instance.json` (debe validar y renderizar siempre), `PINNED_V1` (claves y límites de la v1 solo pueden crecer), y el test que exige un `upgrade()` explícito cuando `version > 1`.

## Persistencia

Mismo contrato que `profile_repository`: un JSON por entidad, escritura atómica, push síncrono, compensación. `read-only` rechaza antes de tocar el disco; `local` no sincroniza; `remote` exige push (copia: un reintento y, si no confirma,
retira el archivo y avisa resultado incierto; guardado: restaura la versión anterior y responde 503). El restore de arranque se espera antes de leer. Réplica única (`docs/PUBLICATION_SAFE_RUNBOOK.md`): el lock es por proceso.

**Ubicación (decisión de infraestructura, no de pertenencia funcional a Estrategia):** `commercial_strategy_runtime/presentations/`. Ese directorio ya está en `config.PERSIST_PATHS` y en el valor documentado de `SPACES_PERSIST_PATHS` (`.env.example`),
y `restore`/`mirror_up`/`push_path` cubren los subdirectorios; **no se agregó ninguna variable ni ruta de persistencia**.

> **Verificación previa al deploy (no resoluble desde el repo):** si en DigitalOcean `SPACES_PERSIST_PATHS` está definida explícitamente, debe incluir `commercial_strategy_runtime`. Estrategia ya depende de esa entrada para ser durable, así que se espera que esté;
> confirmarlo en el panel de DO antes de desplegar y con `DANWOR_PERSISTENCE=remote`.

## Limitaciones conocidas (heredadas del original o de V1)

* El revelado por hover de «¿Por qué SAP BDC?» depende del foco en pantallas táctiles (limitación del original).
* Usa `animation-timeline` (animaciones ligadas al scroll): sin soporte fuera de navegadores Chromium actuales el contenido se muestra sin esa animación (heredado del original).
* Barra superior y pie siguen siendo oscuros en ambos temas (co-branding con logos pensados para fondo oscuro); el cuerpo del documento sí cambia de tema.
* QR estático; marca Vanture y logos de clientes heredados por todas las copias (sin neutralidad de marca ni personalización irrestricta).
* «Copiar enlace» es el único sharing de V1 (sin permisos individuales, publicación anónima ni envío externo).
* Tope de 200 instancias (`MAX_INSTANCES`).
* En pantallas muy angostas el texto «SAP BUSINESS DATA CLOUD» de la barra superior se superpone al logo de Vanture: ya ocurre en el HTML original (paridad exacta con él) y V1 no lo modifica.

## Cómo agregar un campo o un template

* **Campo aditivo:** agregarlo a `VANTURE_FIELDS`, al seed YAML (valor no vacío) y al template (`{{ c.<clave> }}` en posición de texto). Sin subir `version`.
* **Template nuevo:** entrada en `TEMPLATES`, seed, Jinja standalone (sin `extends`), assets bajo `assets/presentations/<id>/`, y su fixture dorado. Las superficies del Hub (catálogo, detalle, editor) no cambian.
