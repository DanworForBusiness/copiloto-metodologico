# HANDOFF — Dashboard de Actividad y Métricas de Uso

> **Documento de traspaso entre asistentes.** Antes de tocar nada, leé este
> archivo entero y la "Bitácora" al final. El plan de diseño detallado de cada
> fase (con el razonamiento completo, no solo el resultado) vive en
> `C:\Users\Joe\.claude\plans\pure-mapping-coral.md` — leelo si necesitás el
> "por qué" de una decisión de arquitectura, no solo el "qué".

> **📍 Estado al 2026-08-05:** todo el trabajo previo de esta sesión (fixes de
> índices/roadmap, contenido, árbol de decisión) quedó resuelto y ya está en
> producción — el usuario lo confirmó y pusheó él mismo, **no es parte de este
> handoff**. El foco actual y único de este documento es el **dashboard de
> Actividad** (`/admin/activity`), nacido de logging que ya se había extendido
> para el lanzamiento reducido. Está funcional y probado por el usuario con
> datos reales, pero **queda un pedido de UX sin resolver** (ver "Pendientes").

## Qué hace

Vista interna (solo `APP_OWNER`) que lee `activity_log/*.jsonl` (eventos de
adopción: login, módulos vistos, preguntas al asistente, árboles, catálogo) +
`feedback_log/*.yaml` (👍/👎 del asistente) y arma:
- KPIs (eventos totales, usuarios únicos, promedio diario, preguntas al
  asistente, **consultas a IA con costo real**).
- Serie diaria (barras CSS), eventos por tipo, top módulos/usuarios.
- **Historia de preguntas al asistente**: reconstruye, por conversación, la
  secuencia completa — pregunta → turno(s) (router y/o "Explícame vos" → IA) →
  feedback 👍/👎 con comentario, correlacionando dos fuentes de log que antes
  eran independientes (ver "Arquitectura").

Filtros: rango de fechas, usuario, módulo — todo server-side (no cliente).
Grupos reordenables por drag-and-drop (persistido en `sessionStorage`, se
pierde al cerrar la pestaña, a propósito).

## Arquitectura

- **Ruta** `GET /admin/activity` (`webapp/main.py`, ~línea 1883): dual-mode,
  igual patrón que las rutas de catálogo (`catalog_cds`, etc.) — con header
  `HX-Request` devuelve solo el fragment (`partials/admin_activity_content.html`),
  si no devuelve la página completa (`admin_activity_page.html`, shell
  `app-shell no-copilot` con nav izquierdo, sin panel del asistente).
  Gating: `_require_diagnostic(request)` **+** `_is_owner(request)` explícito
  (más estricto que el resto del panel admin — expone mails/preguntas reales).
- **Punto de entrada**: link "Abrir dashboard de actividad →" dentro del
  acordeón "📊 Actividad y métricas de uso" en `partials/admin_panel.html`
  (panel derecho). El resto del acordeón (form de filtro viejo) ya no existe
  ahí — los filtros viven en la página del dashboard.
- **Servicio** `webapp/services/activity_analytics.py`:
  - `build_summary(date_from, date_to, user="", module="")`: única función
    pública. Un solo pase de lectura de `ACTIVITY_LOG_DIR` (JSONL) +
    `FEEDBACK_LOG_DIR` (YAML), sin pandas/duckdb/caché (volumen chico).
  - `_read_events`: parsea JSONL → lista de eventos normalizados.
  - `_read_feedback`: glob `*.yaml`, filtra `type=="assistant_response"`,
    indexa por `reply_id`.
  - Agrupa `assistant_query` por `payload.conversation_id` en
    `assistant_conversations` (cada "turno" = un evento; feedback pegado por
    `reply_id`). Eventos sin `conversation_id` (datos previos a la
    correlación) quedan como conversación de 1 turno, sin feedback vinculado
    — degradación esperada, no bug.
  - `_TONE_TAG_RE`: limpia `{dw-black}`/`{dw-gold}` (sintaxis de dos-tonos del
    contenido, ver `content_service.TONE_RE`) de `route_heading` y `answer`
    antes de mostrarlos — **bug real que apareció con datos reales y ya está
    arreglado**.
  - Devuelve, por story: `question`, `user`, `turns[]` (`ts_utc`, `routed`,
    `route_heading`, `bypass`, `llm`, `api_cost`, `answer`, `feedback`),
    `turn_count`, `has_api_cost`, `has_down_feedback`.
- **Correlación entre 2 sistemas de log independientes** (el hallazgo central
  de esta feature): el asistente ya reenviaba `conversation_id` en el botón
  "Explícame vos" (`partials/assistant_route_actions.html:22`) y cada
  respuesta ya tenía un `reply.id` único (`AssistantReply`, en memoria,
  `webapp/services/assistant_service.py`), pero NINGUNO de los dos llegaba a
  los logs. Se agregó:
  - `webapp/main.py` `assistant_chat()` (~línea 1505): el `log_verbose("assistant_query", ...)`
    ahora incluye `reply_id`, `conversation_id`, `answer` (texto de la
    respuesta, primeros 600 chars), y `api_cost` (bool: hubo llamada REAL a
    OpenAI, no router ni modo simulación — `llm=True` no alcanza para esto
    porque el modo simulación de `AssistantChatService.ask()` también da
    `llm=True` sin gastar tokens).
  - `webapp/services/assistant_service.py` `AssistantFeedbackRepository.save()`:
    ahora acepta `user: dict | None` y graba `reply_id`, `conversation_id`,
    `user`, `name` en el YAML de feedback (antes no tenía ningún ID).
  - `assistant_feedback()`/`assistant_feedback_submit()` en `main.py` pasan
    `user=auth_service.session_user(request)` a `.save()`.
  - Retrocompatible: YAML/JSONL viejos sin estos campos no correlacionan
    (se ven sueltos), no rompen nada (`analyze_feedback.py` sigue andando
    igual, lee con `.get()`).
- **Templates**:
  - `webapp/templates/admin_activity_page.html`: shell standalone (copia el
    esqueleto de `catalog_cds.html`).
  - `webapp/templates/partials/admin_activity_content.html`: contenido real.
    Estructura tipo "What's New" (reuso explícito, no reinventado): filtros
    en una fila arriba (`.whatsnew-filters`/`.whatsnew-filter`, clases
    reusadas) + grupos colapsables `<details class="dashboard-section" data-section-id="...">`
    (Resumen, Tendencia diaria, Eventos y catálogo, Módulos y usuarios,
    Historia de preguntas). `data-section-id` es estable aunque la sección
    esté condicionalmente ausente.
  - "← Volver" + filtros viven juntos en `.dashboard-sticky-header` (mismo
    truco `position:sticky; top:-48px` que ya usaba `.catalog-back` solo),
    de forma que quedan pegados arriba al scrollear. El hero (título grande)
    se OCULTA si `total_events==0` para que "Volver" no quede varado a mitad
    de una página casi vacía.
  - Historia de preguntas: cada conversación es un `<details class="assistant-story">`
    colapsado en una línea (fecha·hora, usuario, pregunta, íconos con
    tooltip: ↻N turnos, 🪙 costo, 👎 feedback negativo) que expande al click
    al detalle turno a turno (badge Router/Explícame vos/IA + texto de la
    respuesta + feedback).
- **JS** `webapp/static/app.js`: `initDashboardSections()` — reordenamiento
  drag-and-drop **genérico** (HTML5 DnD nativo, sin librería) de cualquier
  contenedor `[data-dashboard-key="x"]` con hijos `.dashboard-section`.
  Persistencia `sessionStorage["dw-dash-order:x"]`. Se reaplica en cada
  `htmx:load` (dispara en carga inicial Y después de cada swap). Línea de
  inserción gris (`.dashboard-section__drop-line`) muestra el punto exacto
  donde caería la sección al soltar — el `drop` inserta exactamente ahí (no
  recalcula, usa el mismo nodo que ya se mostró). Contrato pensado para que
  el PRÓXIMO dashboard que se arme lo herede gratis.
- **CSS** `webapp/static/tools.css`: todo nuevo bajo comentarios
  `/* Dashboard de Actividad... */` y `/* Historia de preguntas... */`,
  bloques `.activity-*`, `.dashboard-*`, `.assistant-story*`. **Importante**:
  los tokens `--card`/`--soft`/`--ink`/`--dim`/`--line` de la parte de arriba
  del archivo son GLOBALES (`:root` en `app.css`, dark-default) pero
  **`.content-theme-light` los redefine solo dentro de `.copilot-panel`**
  (panel derecho) — en el panel CENTRAL (`.module-content`, donde vive este
  dashboard) esos tokens NO cambian de tema fuera de ese scope. Por eso todo
  lo nuevo usa colores literales light-first + override `.content-theme-dark`
  (mismo patrón que `.whatsnew-item`/`.whatsnew-day`), NO los tokens de arriba
  para fondos/texto. Si agregás algo nuevo acá, seguí ese mismo patrón o vas a
  reproducir el bug que ya se pisó una vez (cards que quedaban oscuras en
  tema claro).

## Reglas de diseño (no romper)

- Sin librerías nuevas (charts, drag-and-drop, etc.) — todo CSS/HTML/JS
  vainilla, consistente con el resto del copiloto.
- Gating owner-only estricto en `/admin/activity` — no aflojarlo aunque el
  resto del panel admin sea más laxo (`DIAGNOSTIC_MODE` global).
- `sessionStorage` para el orden de drag-and-drop, nunca `localStorage`
  (pedido explícito: volátil por sesión, no preferencia permanente).
- No tocar `activity_service.py` (el logger en sí) más allá de los campos
  opcionales ya sumados — sigue siendo "best effort", nunca bloquea la app.

## Pendientes

1. **[ACTIVO — lo último que pidió el usuario, sin resolver]** El detalle
   expandido de cada conversación muestra los turnos como badges sueltos
   (`Router 👍` / `Router → Tipos de Vistas CDS` / `Explícame vos → IA`) pero
   **no se lee como un relato secuencial**. Cita textual del usuario: *"sin
   darme señales claras de storytelling y relato secuencial de hechos no me
   sirve de mucho"*. Falta rediseñar `.assistant-story__detail` en
   `admin_activity_content.html` para que se lea como una línea de tiempo
   narrativa (p. ej. "1. Preguntó → 2. El router respondió con X (el usuario
   calificó 👎: 'motivo') → 3. Pidió 'Explícame vos' → la IA respondió con
   Y"), no una lista plana de badges. Es la continuación directa de todo el
   trabajo de correlación (`assistant_stories`, ya tiene toda la data
   necesaria: `turns[].answer`, `.feedback`, `.routed`, `.bypass` — es un
   problema de TEMPLATE/presentación, no de datos faltantes).
2. Datos anteriores a esta sesión (sin `reply_id`/`conversation_id`) nunca van
   a correlacionar retroactivamente — comportamiento esperado, no arreglar.
3. El contrato de drag-and-drop (`data-dashboard-key`) es genérico pero
   todavía no se retrofiteó a ningún otro dashboard (no existe otro hoy).
4. No hay test end-to-end real logueado (Azure AD, no automatizable desde el
   asistente) — toda la verificación de esta sesión fue: unit tests
   sintéticos de `build_summary()` (incluido el escenario real de 3 turnos
   reportado por el usuario), render Jinja con datos sintéticos, y arranque
   del preview sin errores de servidor/consola. La confirmación visual final
   la hace el usuario con su propia sesión.

## Protocolo de handoff

Igual que `tools/catalog/HANDOFF.md`: al terminar una sesión de trabajo sobre
este dashboard, agregá una entrada a la Bitácora (fecha, qué cambiaste,
estado, qué falta) y actualizá "Pendientes" si resolviste algo de la lista.

## Bitácora

### 2026-08-05 — Claude (sesión completa: de la idea a "Historia de preguntas")
**Estado: funcional, verificado por el usuario con datos reales en su sesión
propia. Queda 1 pendiente activo (ver Pendientes #1) sin resolver por límite
de contexto de esta sesión.**

Arco completo de la sesión (todo en el plan file, con el detalle de cada
decisión): (1) propuesta inicial de dashboard de actividad → owner preguntó
por bucket/storage (ya resuelto, DO Spaces en uso) → (2) primera versión
dentro del panel derecho (acordeón admin) → usuario pidió reubicarlo al panel
central con nivel visual "pionero en analítica" → (3) rediseño completo como
página standalone (`/admin/activity`, patrón dual-mode de catálogo) con KPIs,
gráfico de barras CSS, listas rankeadas → usuario pidió reusar la estructura
de "What's New" (filtros en fila + grupos colapsables) en vez de reinventar
→ (4) refactor a esa estructura + filtros de usuario/módulo server-side → (5)
drag-and-drop de los grupos (pedido explícito de "algo sólido y reusable",
sessionStorage) + fix de un bug real (dragover sin `preventDefault` hacía que
soltar revirtiera el orden) + línea de inserción visual → (6) varios ajustes
finos de CSS (margen del header fijo, padding vs. margin en sticky, formato
de fechas, hero oculto sin datos) → (7) el usuario reportó ver la misma
pregunta duplicada en el feed — investigación reveló que es el flujo real
router→"Explícame vos" (dos eventos legítimos, no bug) pero el feedback 👎
tampoco se veía (log completamente separado, sin ID de correlación) → (8)
se agregó `reply_id`/`conversation_id`/`api_cost`/`answer` a los logs +
correlación completa en `activity_analytics.py` + fix del bug de
`{dw-black}`/`{dw-gold}` filtrándose en `route_heading` (sintaxis de
contenido no renderizada) → (9) rediseño de la fila a `<details>` colapsado
con íconos+tooltips + KPI nuevo de costo real de IA → (10) usuario pidió
también ver el TEXTO de la respuesta (no solo metadata) → agregado → (11)
feedback final: el detalle no se lee como relato secuencial (pendiente #1).

Todo verificado con: `ast.parse` en cada `.py` tocado, unit tests sintéticos
de `build_summary()` (incluido el escenario exacto de 3 turnos + 👎 + costo
reportado por el usuario), render Jinja de cada template con datos
sintéticos y con casos vacíos, arranque de preview sin errores de
servidor/consola en cada fase. Nunca se pudo loguear con sesión real (Azure
AD) desde el asistente — toda confirmación visual la hizo el usuario.

Próximo asistente: arrancá por el Pendiente #1. Tenés toda la data ya armada
en `summary.assistant_stories[].turns[]` — es pura reescritura de
`admin_activity_content.html` (sección "Historia de preguntas al asistente")
para que el detalle expandido lea como una narrativa, no una lista de
badges. Dejá tu entrada acá abajo cuando termines.
