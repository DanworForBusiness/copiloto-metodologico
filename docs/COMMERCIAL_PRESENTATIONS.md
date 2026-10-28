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
| R5 | `GET /comercial/presentaciones/{id}/editar` | Formulario de edición | Comercial + poder editar esa instancia |
| R6 | `POST /comercial/presentaciones/{id}` | Guarda campos (JSON `{revision, fields}`) | ídem R5 + `same_origin_guard` |

* **Copiar no implica editar.** Editar = Comercial **y** no-seed **y** (`owner_id` == usuario **o** `CONTENT_OWNER`). `CONTENT_OWNER` es la capacidad transversal de gobierno de edición de contenidos del Hub (no se creó ninguna variable, rol ni allowlist nueva).
* `APP_OWNER` entra a Comercial y copia, pero no edita instancias ajenas salvo que esté en `CONTENT_OWNER` (mismo criterio que Estrategia).
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

**Referencia visual de paridad** = HTML original + estas dos diferencias aprobadas (logo y numeración). El gate compara el seed renderizado contra esa referencia: 0,0 de diferencia en las 12 secciones, en escritorio y móvil.

## Catálogo (`/comercial/presentaciones`): homologación con el Hub

Cada elemento se resolvió como *elemento actual → precedente del Hub → clase reutilizada → ajuste mínimo*. No hay primitivos nuevos: lo único propio es el filtro en el cliente.

| Elemento | Precedente | Reutiliza | Ajuste |
|---|---|---|---|
| Cabecera / subtítulo | Dashboard CRM (`crm.html`) | `.module-hero` > `.hero-lead-row` > `p.hero-lead.full-page-subtitle` | Solo markup (antes el `<p>` era hijo directo del hero y lo tomaba `.module-hero > p:not(.eyebrow)`, más chico y apagado). Sin CSS. |
| Filtro | CCHH → Base de CVs | `form.hc-profile-filters.filter-toolbar-card` + `.feedback-field--wide`, *antes* del agrupador | Solo «Buscar» (`Título, descripción, autor...`). El *look* de los controles pasó de `human_capital.css` a `tools.css`, junto a su layout, **sin cambiar reglas** (226 controles de CCHH comparados antes/después: 0 diferencias). Cargar `human_capital.css` no era opción: redefine el `h1` del hero y rompe la paridad con el CRM. |
| Agrupador y contador | CCHH → Candidatos | `details.dashboard-section` (+ `__head`, `__caret`, `__title`, `__count`, `__body`) con la tabla adentro | Ninguno. El contador usa el primitivo de `tools.css` tal cual (como Staffing); Candidatos lo afina en su propia hoja (peso 600 y tono más claro en oscuro): diferencia residual mínima. |
| Sin resultados | CCHH → Candidatos (`no_matches`) | `.dashboard-empty` | Oculto de entrada; reemplaza a la tabla dentro del agrupador. |
| Respiro hero → filtro y filtro → agrupador | `.module-hero + #hc-…-panel` (24px) y `#hc-profiles-panel > * + *` (26px) | Misma convención por-panel | `#cp-catalog-panel` + 2 reglas en `commercial.css`. |

**Búsqueda.** 100 % cliente (`commercial_presentations.js`): sin endpoint, parámetros ni preferencias persistidas. Compara en minúsculas, con espacios recortados y colapsados, contra `data-cp-search` de cada fila (**título + responsable**, el mismo texto de la columna «Responsable»); nunca contra el HTML ni la narrativa del documento. El modelo no tiene descripción, por eso el placeholder acordado la nombra pero hoy solo matchean título y responsable. El contador muestra el total (semilla + copias) con la búsqueda vacía y los coincidentes con búsqueda activa («1 presentación» / «N presentaciones»).

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
* Experiencia solo oscura (isla oscura dentro de un Hub claro); tipografía propia (Barlow, IBM Plex Mono) y dorado `#EBBF08` del original.
* QR estático; marca Vanture y logos de clientes heredados por todas las copias (sin neutralidad de marca ni personalización irrestricta).
* «Copiar enlace» es el único sharing de V1 (sin permisos individuales, publicación anónima ni envío externo).
* Tope de 200 instancias (`MAX_INSTANCES`).
* En pantallas muy angostas el texto «SAP BUSINESS DATA CLOUD» de la barra superior se superpone al logo de Vanture: ya ocurre en el HTML original (paridad exacta con él) y V1 no lo modifica.

## Cómo agregar un campo o un template

* **Campo aditivo:** agregarlo a `VANTURE_FIELDS`, al seed YAML (valor no vacío) y al template (`{{ c.<clave> }}` en posición de texto). Sin subir `version`.
* **Template nuevo:** entrada en `TEMPLATES`, seed, Jinja standalone (sin `extends`), assets bajo `assets/presentations/<id>/`, y su fixture dorado. Las superficies del Hub (catálogo, detalle, editor) no cambian.
