# HANDOFF — Copiloto Metodológico Danwor

> **Documento de continuidad entre sesiones de Claude Code.** Si sos una sesión
> nueva y no tenés el historial de la conversación: **este archivo es tu única
> fuente de verdad del estado de la iniciativa**. Leelo COMPLETO, después
> verificá `git status` + branch, y ejecutá **únicamente** la fase que el
> usuario haya autorizado explícitamente. Ver **A.17 Regla de continuidad**.

---

# ARCHITECTURE MODERNIZATION / INTERNAL APPS INITIATIVE

> **⭐ Esta es la iniciativa ACTIVA.** El handoff del *Dashboard de Actividad*
> quedó como **ANEXO HISTÓRICO** al final (feature previa, ya en producción; se
> conserva por su detalle técnico, no es el trabajo en curso).
>
> **Estado en una línea (2026-08):** Fase 0 **CERRADA/GREEN, mergeada a `main`**.
> **Fase 1 — System Router Pilot: IMPLEMENTADA y pusheada** a
> `work/architecture-phase-1` (commit `a0bfd8d`); **CI PENDIENTE** (falta abrir
> el PR manualmente — sin `gh` en el entorno) → **NO cerrada todavía**. Detalle y
> gates en **A.13.1**. No avanzar a Fase 2 sin autorización explícita.

## A.1 Contexto de la iniciativa

Danwor está iniciando una **modernización progresiva de aplicativos internos**
históricamente implementados con Power BI, Power Apps, SharePoint Lists, Excel
en SharePoint y combinaciones de esas tecnologías.

**No** buscamos migrar tecnología 1:1. Para cada aplicativo se evalúa:
**KEEP / REFACTOR / RETIRE**. El objetivo es rescatar capacidades funcionales
válidas y llevarlas a una arquitectura gobernable, reutilizable y escalable —
evitando reemplazar *"un conjunto de Power BI desordenados"* por *"un conjunto
de aplicaciones FastAPI desordenadas"*.

El **Copiloto Metodológico** es hoy la base tecnológica más madura; por eso
preparamos **primero su arquitectura** antes de sumar verticales nuevos.

El primer vertical nuevo posterior al refactor será **STAFFING / ASIGNACIONES DE
CONSULTORES** (derivado de un Power BI legado ya reverse-engineered). Staffing
será además el **ARCHITECTURAL INTEGRATION TEST** de la arquitectura nueva: la
pregunta de diseño es *"¿podemos incorporar Staffing sin contaminar `main.py`,
sin copiar llamadas a Graph y sin acoplarlo al Copiloto?"*. Si la respuesta es
NO, el refactor todavía no logró su objetivo. **Staffing NO se implementa
todavía** — es solo el norte del diseño.

## A.2 Secuencia global de la iniciativa

1. Arquitectura actual del Copiloto (entender/validar). ✅ hecho (diagnóstico).
2. Refactor estructural **sin cambio funcional**. ⏳ en curso (Fases 0→3).
3. Validación / regresión.
4. Staffing como primer vertical.
5. SharePoint → Repository → Service → HTMX.
6. Paridad funcional con Power BI.
7. Mejoras funcionales / UX / IA.
8. Decommission controlado del legado.
9. Repetir el patrón para los siguientes aplicativos.

**Principios rectores:** *PARIDAD ANTES DE INNOVACIÓN. DECOMMISSION DESPUÉS DE
VALIDACIÓN.*

## A.3 Baseline arquitectónico confirmado (FACT — verificado contra el código)

- FastAPI · Uvicorn · Jinja2 SSR · HTMX · Alpine.js · Tailwind (CDN en
  `base.html`) + CSS Danwor.
- Auth: **Microsoft Entra ID OAuth2/PKCE, public client (token delegado, sin
  client_secret)**; sesión propia por **cookie HttpOnly firmada HS256**
  (`webapp/services/auth_service.py`; JWKS vía `httpx`).
- Microsoft Graph existente; SharePoint **Lists** y **Files/Drives**.
- DigitalOcean **App Platform** (deploy por `Procfile`, buildpack) + DigitalOcean
  **Spaces** para persistencia runtime (`core/remote_store.py`, boto3).
- **Sin PostgreSQL, sin Redis, sin ORM.** Persistencia = filesystem efímero +
  mirror a Spaces; formatos YAML/JSONL.
- `webapp/main.py`: **~4264 líneas, 75 endpoints, CERO `APIRouter`** (todo
  `@app.*`). `lifespan` maneja restore/sync/mirror de Spaces. Singletons
  instanciados a nivel de módulo (`store`, `section_analysis_service`,
  `assistant_chat_service`, `assistant_routing_service`, `assistant_store`,
  `assistant_feedback_repository`) + `templates` (Jinja).
- `webapp/services/` (13 módulos) ya es una separación parcial de responsabilidades.
- **Graph vive en exactamente 3 archivos**, todos con `urllib.request` (no httpx):
  `core/sharepoint_cases.py` (Lists), `tools/catalog/catalog_service.py` (Drive),
  `tools/catalog/generate_catalog.py` (shares/drives). Headers/paginación
  duplicados; error handling distinto por consumidor; sin retries/429.
- **No existe integración M365 común** todavía.
- **Dos clientes OpenAI duplicados** (`section_analysis.py`, `assistant_service.py`),
  sin gateway. Su consolidación **NO está en el critical path de Staffing**.
- **Catalog concentra ~1900 líneas** de lógica funcional + presentación (SVG por
  strings) dentro de `main.py` (L2051–3946).
- Templates mayormente **planos** (`templates/*.html` + `templates/partials/*.html`).
- **CI:** no existía antes de Fase 0; ahora sí (ver A.10).

## A.4 Decisiones arquitectónicas YA TOMADAS (no son propuestas)

- **Target inmediato:** `routers/` + `services/` + `integrations/`.
- **NO** introducir todavía `modules/<domain>/`. La eventual co-localización por
  dominio se decidirá por **cohesión / cantidad de archivos relacionados /
  dependencias / presión estructural real** — **NO** por un número arbitrario de
  verticales.
- **Arquitectura = MODULAR MONOLITH PRAGMÁTICO.** NO microservicios, NO
  React/Vue, NO PostgreSQL/Redis "por arquitectura", NO CQRS/event-bus/service-mesh,
  NO Clean Architecture ceremonial. FastAPI+Jinja+HTMX+Alpine es la base deliberada.
- **`main.py` permanece como COMPOSITION ROOT.** `create_app()` **NO** es
  prerrequisito; queda diferido hasta que exista beneficio concreto.
- **Routers:** HTTP/templates/HTMX/status/redirects. **No** reglas de negocio ni Graph.
- **Services:** casos de uso, reglas funcionales, orquestación.
- **Integrations:** infraestructura externa (Graph, SharePoint, etc.).
- **SharePoint es INFRAESTRUCTURA, no dominio.**

## A.5 Decisiones sobre runtime / singletons

En una fase **posterior** (Fase 2) probablemente haya que sacar de `main.py` los
`templates` y los singletons compartidos para que los routers no importen
`main.py`. La opción preferida **a evaluar** es un módulo chico `bootstrap.py` o
`runtime.py` (naming a decidir en Fase 2a) que contenga **únicamente**
construcción de runtime objects compartidos + `templates` + singletons actuales.
**NO** debe ser service-locator general, dumping ground, reglas de negocio,
Graph, helpers genéricos ni lógica HTTP. **NO** mover automáticamente a
`app.state`. **NO** introducir `Depends`/DI en el mismo movimiento salvo
necesidad comprobada. Antes de esa fase, **inspeccionar** monkeypatch/imports de
objetos desde `webapp.main` (tests). **No implementado todavía.**

## A.6 Decisiones Microsoft 365

Se implementará (Fase 3) `webapp/integrations/microsoft365/` como capacidad
**pequeña** reutilizable. Separación deliberada:
- **M365-A = CONSOLIDACIÓN ESTRUCTURAL.** Preserva `urllib`, comportamiento,
  **tipos de excepción observables**, timeouts, **política de error por
  consumidor**, y paginación actual. Equivalencia estricta.
- **M365-B = HARDENING POSTERIOR.** Ahí se evalúa 429, `Retry-After`,
  retries/backoff, `GraphError`, normalización de excepciones, observabilidad,
  `httpx`, timeouts normalizados.

**NO** mezclar M365-A y M365-B. **NO** solapar Fase 2 y Fase 3.

## A.7 Decisiones sobre el token de Graph

Hoy el access token de Graph fluye `browser → sessionStorage["dw-graph-token"] →
form-data (`graph_token`) → backend`, protegido por HTTPS. **La deuda NO es
"plaintext network transport"** (viaja cifrado). La deuda real es: (a) token
accesible a JavaScript/sessionStorage; (b) propagación manual a endpoints; (c)
conocimiento de `graph_token` filtrándose a los consumidores. **NO rediseñar
OAuth ahora.** Los dominios futuros no deberían conocer `graph_token` más de lo
estrictamente necesario (la capa M365 debería ser la única que lo trate como
bearer de Graph).

## A.8 Catalog

**Decisión:** NO trasladar las ~1900 líneas de Catalog desde `main.py` a
`routers/catalog.py` solo para reducir `main.py` — eso mudaría el God Module sin
resolverlo. Catalog tendrá **su propia fase**, separando: HTTP → `routers/catalog.py`;
business/compliance/data → `services/catalog_service.py`; SVG/presentation →
`presentation/catalog_svg.py` (o equivalente). Mientras tanto, Catalog puede
**permanecer como residual explícito en `main.py`**.

## A.9 Templates

Los verticales **nuevos** usan `templates/<domain>/` (ej.: `templates/staffing/index.html`
+ `templates/staffing/partials/`). **NO** reorganizar los templates existentes
solo por uniformidad.

## A.10 Resultado de FASE 0 (COMPLETADA / GREEN / mergeada a main)

- **Python:** LOCAL `3.14.3` == PRODUCCIÓN DO `3.14.3` (build log: *"Using cached
  install of Python 3.14.3"*). DO advierte que a futuro requerirá `.python-version`
  → **DEFERRED** (ver A.16), NO crear todavía.
- **Testing:** baseline original **225 passed / 1 skipped / 226 collected** →
  con route-contract **226 / 1** → con el test explícito de case_library vacío
  **227 passed / 1 skipped / 228 collected**. `pytest==9.1.1`.
- **`requirements-dev.txt`** creado con `pytest==9.1.1` (separado de
  `requirements.txt`, que es solo runtime).
- **Route contract** (`tests/test_route_contract.py` + `tests/route_surface.json`):
  **75 rutas propias** protegidas por `MÉTODO + PATH`, comparadas como **set**,
  sin acople a handler/response_model; excluye `/static`, rutas del framework
  (`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`) y `HEAD/OPTIONS`.
  **Regla:** durante el refactor, un mismatch es **primero una REGRESIÓN**; NO
  regenerar `route_surface.json` para hacer pasar el test; `UPDATE_ROUTE_SURFACE=1`
  solo ante cambio de contrato HTTP deliberado y aprobado.
- **CI** (`.github/workflows/ci.yml`): Python 3.14; instala `requirements.txt` +
  `requirements-dev.txt`; corre `pytest -q`; triggers `pull_request` + `push` a
  `main`. **CI final: GREEN.**
- **Functional smoke manual (por el usuario): GREEN** — Auth, Landing/nav,
  Content/módulos, Wizard/árboles, Assistant/análisis, Academy, Catalog, Admin,
  Feedback, Graph/SharePoint.
- **Estado:** `LOCAL PYTEST GREEN · ROUTE CONTRACT GREEN · CI GREEN · FUNCTIONAL
  SMOKE GREEN` → **FASE 0 CLOSED / GREEN.** Mergeada a `main` vía PR #1 (merge
  commit `e007bb3`; commits `75cbf3e` route-contract, `5ddde40` CI+dev-deps,
  `9ae1c25` fix determinismo).

## A.11 Hallazgo de determinismo de tests (Fase 0)

`tests/webapp/test_admin_tools.py::test_cases_analysis_renders_summary` dependía
accidentalmente del `case_library/` runtime local: en local (con datos) tomaba
la rama de análisis y pasaba; en CI (checkout limpio, `case_library/`
gitignoreado) tomaba la rama *"No hay casos para analizar"* y fallaba. Se corrigió
**solo en tests** (fixture que redirige `analyze_cases.CASE_DIR` a un tmp con un
caso mínimo real + test explícito del empty-state). **No se modificó la
aplicación.** Este finding **justifica por qué se incorporó CI antes del refactor**.

## A.12 Estado Git (verificado al escribir este checkpoint)

- Branch actual: **`work/architecture-phase-1`**.
- `main` contiene Fase 0 (merge `e007bb3`, PR #1).
- Branch `work/architecture-phase-0`: **eliminada local** (ya no figura en
  `git branch`).
- Working tree: **limpio** antes de este checkpoint.
- Este checkpoint se commitea como **docs-only** sobre `work/architecture-phase-1`.

## A.13 FASE ACTIVA — PHASE 1: SYSTEM ROUTER PILOT

**Hipótesis a probar:** *"Podemos extraer una frontera HTTP pequeña y coherente
de `main.py` hacia `APIRouter` sin modificar contrato ni comportamiento."*

**Único alcance funcional:** `/health` y `/favicon.ico`.

```
ANTES:  main.py  →  /health, /favicon.ico
DESPUÉS: main.py → app.include_router(system.router)
         webapp/routers/__init__.py
         webapp/routers/system.py  →  /health, /favicon.ico
```

**NO forma parte de Fase 1:** bootstrap/runtime module, `create_app()`,
`app.state`, `Depends` nuevos, cualquier otro router (auth/academy/wizard/
assistant/content/admin/catalog), Graph, M365, httpx, LLM, templates,
persistence, `.python-version`, ADR.

**`system.py` NO puede importar `webapp.main`.** Si `/favicon.ico` depende de una
constante/path de `main.py`: primero buscar si ya existe en `config` u otro
módulo, preservar exactamente la resolución actual, **no** crear bootstrap/runtime
solo para esto, **no** refactorizar paths transversalmente. Si mover favicon
exige una decisión arquitectónica más amplia → **DETENERSE y explicar** antes de
ampliar scope.

## A.13.1 Fase 1 — Estado de implementación (IMPLEMENTED / CI PENDING / NOT CLOSED)

**STATUS: IMPLEMENTED LOCALLY / PUSHED · CI PENDING · NOT YET CLOSED.** No marcar
GREEN/CLOSED hasta que **GitHub CI = GREEN** en el PR.

- **Branch:** `work/architecture-phase-1`. **HEAD:** `a0bfd8d`.
- **Commits de esta fase:** `8c152b1` (checkpoint del HANDOFF, docs-only) →
  `a0bfd8d` (implementación) → *(este commit)* `docs(handoff): update architecture
  phase 1 status`.
- **Archivos creados:** `webapp/routers/__init__.py`, `webapp/routers/system.py`
  (`APIRouter` con `/health` y `/favicon.ico`; NO importa `webapp.main`; resuelve
  el path del favicon localmente = `webapp/`, igual que `WEB_DIR`).
- **Archivos modificados:** `webapp/main.py` (+`from webapp.routers import system as
  system_router`, +`app.include_router(system_router.router)`, −los 2 handlers,
  reemplazados por un comentario); `tests/webapp/test_routes.py` (+`test_favicon`).
- **Endpoints movidos:** `/health`, `/favicon.ico` (2). Superficie total sin
  cambios: **75 rutas** (73 `@app.*` directos + 2 vía router).
- **Route contract:** GREEN, **75 rutas**, `route_surface.json` **NO modificado**
  (surface antes == después). No se corrió `UPDATE_ROUTE_SURFACE`.
- **pytest:** **228 passed / 1 skipped / 0 failed** (227 → +1 por `test_favicon`).
- **Smoke `/health`:** 200 · `{"status":"ok","architecture":"fastapi-ssr-htmx"}`.
- **Smoke `/favicon.ico`:** 200 · `content-type: image/x-icon` · 19621 bytes.
- **CI:** PENDIENTE. Abrir PR (sin merge) desde
  `https://github.com/DanworForBusiness/copiloto-metodologico/pull/new/work/architecture-phase-1`
  (base `main` ← `work/architecture-phase-1`). El trigger `pull_request` corre la
  Action; esperado **228 passed / 1 skipped**.
- **Findings / desviaciones:** ninguna desviación del plan. Único agregado no
  estrictamente "mover código": un test mínimo de regresión para `/favicon.ico`
  (no existía; `/health` ya tenía `test_health`) — dentro del alcance autorizado.
- **Próximo paso:** confirmar CI GREEN en el PR → recién ahí Fase 1 = CLOSED/GREEN.
  **NO avanzar a Fase 2** sin review + autorización explícita del usuario.

## A.14 Gates de Fase 1

Fase 1 es GREEN **solo si**: `route contract GREEN` · `pytest completo GREEN` ·
`/health smoke GREEN` · `/favicon.ico smoke GREEN` · `GitHub CI GREEN`. No
requiere repetir el functional pack completo (cambia solo 2 endpoints
infraestructurales). **Al terminar Fase 1 NO avanzar a Fase 2** — requiere review
explícita del usuario.

## A.15 Siguientes fases planeadas (resumen)

- **Fase 2 — Router boundaries progresivas.** Antes: evaluar el runtime/bootstrap
  module (A.5). Extraer routers de menor a mayor acoplamiento. **Catalog NO se
  muda wholesale** (A.8).
- **Fase 3 — M365-A** (consolidación estructural, A.6). Después: **Staffing
  readiness review**; luego Staffing como primer vertical.

## A.16 Deferred debt (explícita)

- `.python-version` (DO lo requerirá; probable fijar `3.14.3`).
- Warnings de FastAPI/Starlette bajo Python 3.14 (baseline 197; no del código
  propio; no resolver ni ocultar).
- Fallback `.streamlit/secrets.toml` de `OPENAI_API_KEY` (`section_analysis.py:417`
  `_legacy_api_key`) → migrar a env/secret store.
- **Corrección documental pendiente:** el README (commit `df9d6ef`) llama a
  `.streamlit/` "legacy/de esa época" — impreciso, sigue siendo fallback vivo de
  la API key. Corregir cuando se autorice tocar docs de nuevo.
- `AssistantConversationStore` sin TTL (en memoria).
- Cliente LLM duplicado / LLM sync-bloqueante.
- M365-B hardening · `urllib→httpx`.
- `tools/catalog` (piloto) ejecutado desde prod vía `admin_tools`.
- Limpieza legacy (`ui/`, `engine/` vacíos, etc.) · consolidación de CSS.
- Eventual `create_app()` · eventual `app.state`/`Depends` (solo si aparece
  necesidad real).

## A.17 Regla de continuidad (LEER SIEMPRE)

Ante una sesión nueva de Claude Code:
1. Leer **todo** este HANDOFF.
2. Verificar `git branch --show-current` + `git status` (no asumir el estado).
3. **NO** inferir que una fase está autorizada solo porque figura como "next".
4. Ejecutar **únicamente** la fase que el usuario autorizó explícitamente.
5. Preservar los gates de cada fase.
6. **Nunca** avanzar automáticamente a la fase siguiente.
7. Actualizar este HANDOFF al terminar un checkpoint relevante.

---

# ANEXO HISTÓRICO — Dashboard de Actividad y Métricas de Uso

> **Feature previa, ya en producción.** Se conserva por su detalle técnico y sus
> decisiones; **no es la iniciativa activa** (esa es la de arquitectura, arriba).
> El plan de diseño detallado del dashboard vive en
> `C:\Users\Joe\.claude\plans\pure-mapping-coral.md`.

> **📍 Estado al 2026-08-05:** el foco de este anexo es el **dashboard de
> Actividad** (`/admin/activity`). Está funcional y probado por el usuario con
> datos reales. En su última ronda se cerró el Pendiente #1 (relato
> secuencial), una tanda de bugs con capturas, el truncado de respuestas largas,
> y se agregaron dos mejoras (gradiente de dorado en los gráficos + sección
> "Árboles de decisión"). **Todo esto YA ESTÁ PUSHEADO a prod.** **No quedan bugs
> abiertos**, pero quedan 2 pendientes de pulido (ver Pendientes #1 tonos y #2
> outcome de árboles — este último es un fix de origen que solo aplica a eventos
> NUEVOS).

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
  - `webapp/main.py` `assistant_chat()` (~línea 1505) **y `analyze_section()`
    (~línea 1526)**: el `log_verbose("assistant_query", ...)` ahora incluye
    `reply_id`, `conversation_id`, `answer` (texto COMPLETO de la respuesta —
    ya NO recortado a 600; el único tope que queda es el de `_safe_value` del
    logger, 4000 chars, ver Pendiente #1), y `api_cost` (bool: hubo llamada REAL a
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

> **Abiertos (pulido, no bugs):**
>
> - **[RESUELTO 2026-08-05 (6)] Tonos de los gráficos.** El usuario pidió "lo
>   mejor para UX/claridad, sutil, sin vender". Solución final: monocromo dorado
>   con **escalón por posición** (las listas van ordenadas desc), desaturando
>   hacia el claro — la barra #1 es la más intensa. Ya NO es inline por valor:
>   se movió a CSS (`tools.css`) vía `.activity-bar-row:nth-of-type(1..10)
>   .activity-bar__fill` (rampa de 10 pasos, fallback en `.activity-bar__fill`).
>   Se sacaron TODOS los `background:hsl(...)` inline del template. La **serie
>   diaria** quedó en dorado PLANO (`.activity-chart__bar`), porque es tiempo (no
>   ranking) y un gradiente ahí sugeriría una progresión falsa. Verificado:
>   7 barras → 7 tonos distintos (staircase), serie diaria 1 tono. `nth-of-type`
>   cuenta solo los `<div>`, el `<p>` subhead no desfasa. Si se quiere revisar de
>   nuevo: los 10 valores de la rampa están juntos en `tools.css` (buscar
>   "Rampa de dorado por POSICIÓN").
> - **[ABIERTO — parcialmente resuelto] Outcome de los árboles ("Resultado: —").**
>   El recorrido mostraba `Resultado: —` aunque el árbol llegara a una hoja.
>   Causa: en `main.py` `answer()` (~línea 1816) el log de `tree_outcome` tomaba
>   `leaf.get("id") or leaf.get("title")`, pero las hojas guardan el resultado en
>   **`leaf.decision`** (el `<h3>` "Decisión recomendada" de `wizard.html`). Ya
>   se cambió a `leaf.get("decision") or leaf.get("title") or leaf.get("id")`.
>   **OJO: solo aplica a eventos NUEVOS** — los `tree_outcome` ya logueados con
>   leaf vacío no se pueden backfillear; hay que completar un árbol NUEVO para
>   verlo. Mejoras posibles a futuro: (a) capturar también `leaf.name_pattern` /
>   `leaf.notes` / `post_actions.next_steps` para un outcome más rico; (b) en
>   árboles de assessment el resultado es el `score` (un dict) — hoy la
>   reconstrucción en `activity_analytics._tree_summary` solo guarda `score` si
>   es escalar, así que el score de assessment no se muestra (habría que
>   extraer `score`/`max_score` del dict). (c) opcional: loguear `stem` en
>   `tree_step`/`tree_outcome` (existe `run.module_stem`) para no depender del
>   mapa `tree_id`→módulo al atribuir recorridos por módulo.

1. **[RESUELTO 2026-08-05 (3)] Respuestas largas se cortaban con `…[truncated]`.**
   Se tomó la **opción (b)** de abajo (tope por-campo): en
   `activity_service.py` se agregó `_LONG_TEXT_KEYS = {"answer"}` +
   `_MAX_TEXT_LEN = 20000`, y `_safe_value(value, max_len=_MAX_STRING_LEN)` ahora
   recibe un tope; en la rama de dict, si la clave está en `_LONG_TEXT_KEYS` usa
   20000, si no sigue en 4000. Así SOLO `answer` crece (el log no se infla en el
   resto) y las guías/árboles largos se guardan completos. Verificado: `answer`
   de 6000 → completo, `question`/`route_heading` de 6000 → cortan a 4000,
   `answer` de 25000 → corta a 20000. `pytest -q` 225 passed / 1 skipped.
   Nota sobre la regla "no tocar el logger": este cambio NO agrega I/O ni nada
   que pueda bloquear (sigue best-effort), es solo un tope de tamaño por clave —
   por eso se consideró dentro del espíritu de la regla. El texto de abajo queda
   como registro histórico del diagnóstico y las alternativas evaluadas.
   **Causa raíz** (confirmada): NO era
   el viejo `[:600]` (ese se sacó y quedó resuelto — respuestas cortas ahora se
   ven enteras). Es `_safe_value()` en `webapp/services/activity_service.py:64`,
   que corta CUALQUIER string del payload a `_MAX_STRING_LEN = 4000` y le agrega
   `"…[truncated]"`. Esa guía supera los 4000 chars → se corta al persistir el
   evento (no en el render). **Restricción dura**: hay una regla explícita de NO
   tocar `activity_service.py` más allá de campos opcionales — sigue siendo
   "best effort", nunca bloquea la app. Subir `_MAX_STRING_LEN` a secas afecta
   TODOS los campos de TODOS los eventos (infla el log). Opciones para el
   próximo asistente (elegir con el usuario, no asumir):
   - (a) Subir `_MAX_STRING_LEN` (global, simple, pero infla todo el log).
   - (b) Tope por-campo más alto solo para `answer` (más quirúrgico; requiere
     tocar levemente `_safe_value`/el punto de log — ojo con la regla de arriba).
   - (c) **Recomendada arquitectónicamente**: NO guardar la respuesta completa
     en el log. Guardar solo `reply_id`/`conversation_id` (ya se guardan) y que
     el dashboard lea la respuesta canónica completa del `assistant_store`
     (`AssistantReply`, en memoria en `assistant_service.py`) por ese id. Deja
     el log liviano (coherente con la regla "best effort") y muestra el texto
     completo sin tope. Contra: el store es en memoria — respuestas de sesiones
     ya expiradas/reiniciadas no estarían (para eventos históricos habría que
     caer al `answer` truncado del log igual). Evaluar si vale la pena.
2. **[RESUELTO 2026-08-05]** El detalle expandido ahora se lee como **línea de
   tiempo narrativa**: `<ol class="story-timeline">` con un paso inicial
   "El usuario preguntó" + un paso por turno que describe en prosa qué pasó
   ("El router respondió con contenido curado — sección X" / "Pidió 'Explícame
   vos' y respondió la IA" / con 🪙 si tuvo costo), la respuesta indentada, y el
   feedback en la misma narrativa ("El usuario lo calificó 👎 no útil: 'motivo'").
   Markup en `admin_activity_content.html` (sección "Historia de preguntas") +
   CSS `.story-timeline*` en `tools.css` (light-first + override dark, línea
   conectora vertical). Reemplazó los badges sueltos (`.assistant-story__turn/
   __badge/__answer/__fb`, ya removidos del CSS).
3. Datos anteriores a esta sesión (sin `reply_id`/`conversation_id`) nunca van
   a correlacionar retroactivamente — comportamiento esperado, no arreglar.
4. El contrato de drag-and-drop (`data-dashboard-key`) es genérico pero
   todavía no se retrofiteó a ningún otro dashboard (no existe otro hoy).
5. No hay test end-to-end real logueado (Azure AD, no automatizable desde el
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

### 2026-08-05 (8) — Claude — Tooltips "qué mide cada evento". PUSHEADO.
**Estado: verificado (title/underline/cursor por estilos computados en browser +
`pytest -q` 225 passed / 1 skipped). Pusheado.**

- El usuario pidió claridad sobre qué cuenta cada evento (ej.: por qué
  `tree_step`=10 con `tree_started`=5). Se agregó un diccionario
  `_EVENT_DESCRIPTIONS` en `activity_analytics.py` (fuente única, alineado a los
  eventos reales de `main.py` + el beacon `/telemetry/event`), expuesto como
  `summary.event_descriptions`. El template pone ese texto como `title` (tooltip
  nativo) sobre el nombre del evento — tanto en la lista "Eventos por tipo" como
  en el header "Desglose de …". Afordancia visual: `.activity-event-name`
  (subrayado punteado + `cursor: help`).
- Aclaración clave que ahora da el tooltip: `tree_step` = respuestas a nodos
  INTERMEDIOS (varias por recorrido) vs `tree_started` = recorridos iniciados vs
  `tree_outcome` = recorridos que cerraron en resultado. Eso reconcilia lo que el
  usuario no entendía. Si se agregan eventos nuevos en `main.py`, sumar su
  descripción al dict (si falta, el tooltip cae en "Evento sin descripción").

### 2026-08-05 (7) — Claude — Pulidos cosméticos (tipografía de code + altura del botón). PUSHEADO.
**Estado: CSS puro (`tools.css`), verificado por estilos computados en browser +
`pytest -q` 225 passed / 1 skipped. Pusheado.**

Tres retoques que pidió el usuario:
- **`<code>` con tipografía distinta** (fechas del "Rango efectivo" y nombre de
  evento en "Desglose de …"): NO era tamaño (medían igual, 10.88/10.24px) sino
  la FAMILIA — el resto usa `JetBrains Mono` y los `<code>` caían en
  `ui-monospace` (default de Tailwind/navegador). Fix: `font: inherit` en
  `#activity-dashboard .hero-meta code` y `.dashboard-subhead code`.
- **Botón "Actualizar" más alto que la fila** (42px vs inputs de 35px): la base
  `.btn-primary` trae `min-height:42px; padding:10px`. Override scopeado a
  `.dashboard-sticky-header .whatsnew-filters .btn-primary` → `height:35px;
  padding:0 18px; inline-flex; align-items:center`. Ahora empareja inputs/selects.
- Todo scopeado al dashboard para no afectar otras páginas (otros heroes, la
  página de What's New que también usa `.whatsnew-filters`).

### 2026-08-05 (6) — Claude — Tonos de gráficos: monocromo con escalón por posición. PUSHEADO.
**Estado: verificado (estilos computados en browser: 7 barras → 7 tonos; serie
diaria plana; sin errores de consola; `pytest -q` 225 passed / 1 skipped) y
pusheado a prod.**

- Cierra el pendiente de tonos. El gradiente por valor inline anterior no
  convencía; el usuario pidió "lo mejor para UX/claridad, sutil, sin vender".
- Solución: **monocromo dorado, escalón por POSICIÓN** (listas ordenadas desc →
  #1 la más intensa, aclara hacia abajo), desaturando hacia el claro para que
  sea calmo. Movido de inline a CSS: `.activity-bar-row:nth-of-type(1..10)
  .activity-bar__fill` en `tools.css`. Se eliminaron todos los `hsl(...)` inline
  del template (más prolijo). **Serie diaria** → dorado plano
  (`.activity-chart__bar`), es tiempo, no ranking.
- Por qué por posición y no por valor: garantiza que barras vecinas siempre se
  diferencien (la cola de valores iguales —muchos "1"— con el modo por valor
  volvía a verse monótona). El largo sigue codificando la magnitud; el color es
  ordinal/decorativo.

### 2026-08-05 (5) — Claude — Panel de eventos interactivo (clic → desglose). PUSHEADO.
**Estado: verificado (backend reconcilia, toggle probado en browser sin errores
de consola, `pytest -q` 225 passed / 1 skipped) y pusheado a prod.**

- **"Eventos y catálogo" ahora es interactivo** (pedido del usuario). Antes el
  panel derecho mostraba SIEMPRE "Vistas de catálogo por tipo" (desglose fijo de
  `catalog_view`), lo que se veía inconsistente: catalog era el único evento
  desglosado. Ahora: las filas de "Eventos por tipo" (izquierda) son
  clickeables y el panel derecho muestra el desglose del evento elegido.
  - Regla de dimensión: `catalog_view` → por `kind` (tipo de vista, como antes);
    **cualquier otro evento** → por usuario (quién lo generó cuántas veces).
    Ej.: `module_selected = 5` → clic → Jose 3 / Ana 2.
  - Backend: `activity_analytics.build_summary` agrega `event_user_counts`
    (Counter por evento) y arma `summary.event_breakdowns` = {event: {dim, rows}}
    reutilizando `_top()`. Todos los desgloses se precalculan (volumen chico).
  - Front: `admin_activity_content.html` — izquierda con `data-event` +
    `role=button`/`tabindex`; derecha con un `.activity-breakdown__panel`
    `[data-breakdown]` por evento (todos `hidden`, JS muestra el activo).
    Toggle en `app.js` `initEventBreakdown()` (vanilla, se re-cablea en cada
    `htmx:load` igual que `initDashboardSections`; sin request extra). Por
    defecto muestra el evento más frecuente. CSS nuevo: `.activity-bar-row--clickable`
    (hover/active/focus) + `.dashboard-subhead__hint`.
  - Reconciliación garantizada: la suma del desglose == el count del evento
    (verificado: module_selected 5 = 3+2, catalog_view 3 = 2+1).
  - Nota: el desglose "por usuario" es universal; se podría enriquecer con
    dimensiones propias por evento (p. ej. `section_analyze` por sección/acción,
    `tree_started` por árbol) — quedó como mejora futura, no pedida.

### 2026-08-05 (4) — Claude — Mejoras: gradiente de gráficos + sección "Árboles de decisión" + fix outcome. PUSHEADO.
**Estado: todo verificado (`pytest -q` 225 passed / 1 skipped, render sintético
+ estilos computados en browser con Tailwind) y PUSHEADO a prod junto con la
ronda (3). Quedan 2 pendientes de pulido, ver "Abiertos" arriba.**

- **Gráficos con gradiente de dorado (pedido del usuario).** Antes todas las
  barras eran el mismo amarillo. Ahora cada barra toma un tono según su valor
  (más grande = dorado más intenso, va aclarando): inline
  `background:hsl(45,92%,{{ (44 + (100 - pct) * 0.28)|round|int }}%)` en serie
  diaria, eventos por tipo, top módulos, top usuarios y árboles iniciados. Se
  eligió "por valor" y no "por posición" para no engañar en la serie temporal.
  Verificado: barras horizontales pasaron de 1 tono a 4 (`rgb(215,164,9)` →
  `rgb(248,211,98)`). **El usuario dijo que no lo convence del todo — a repasar
  (ver Pendiente abierto de tonos).**
- **Nueva sección "Árboles de decisión"** (`data-section-id="arboles"`, entre
  "Módulos y usuarios" e "Historia de preguntas"). Dos partes: (1) *Árboles
  iniciados* — qué árbol se abrió (título real + chip del panel + count, barra
  con gradiente); (2) *Recorridos* — reconstrucción por `run_id` del camino de
  respuestas nodo a nodo (`tree_step`) + resultado (`tree_outcome`), colapsable
  con `story-timeline` (mismo patrón que la historia de preguntas). Data nueva
  en `activity_analytics._tree_summary(all_events, user, module)` →
  `summary.tree_started[]` / `summary.tree_runs[]`. CSS nuevo mínimo:
  `.tree-panel-chip`. **Atribución por módulo**: los eventos de recorrido NO
  traen módulo en el payload, así que se mapea `tree_id`→módulo desde
  `tree_started`; verificado que con filtro de módulo el recorrido sigue
  apareciendo (si no, se caía).
- **Fix "Resultado: —"** (lo marcó el usuario): el `tree_outcome` logueaba
  `leaf.get("id") or leaf.get("title")`, pero las hojas guardan el resultado en
  `leaf.decision`. Cambiado en `main.py` `answer()`. **Solo aplica a eventos
  nuevos** (ver Pendiente abierto). Ver ahí las mejoras futuras (name_pattern,
  score de assessment, loguear stem).

### 2026-08-05 (3) — Claude — Tanda de bugs post-relato + 1 bug abierto (truncado). SIN COMMITEAR.
**Estado: todos los ítems de abajo verificados salvo el último (bug abierto).
El batch NO está commiteado/pusheado — el usuario se quedó sin créditos
semanales y sigue con otro asistente. Ver "⚠️ Estado sin commitear" arriba
para la lista exacta de archivos y el flujo de commit/push.**

Bugs reportados por el usuario con capturas y resueltos en esta ronda:
- **Acciones de la tab "Sección" no aparecían en "Historia de preguntas".** La
  tab Sección logueaba un evento `section_analyze`, distinto de `assistant_query`
  (la fuente de la historia). Fix: `analyze_section()` en `webapp/main.py`
  (~línea 1065) ahora loguea `assistant_query` (con `section`, `action`,
  `question=(user_message or "{action} · {section_slug}")`, `answer`, `routed=False`,
  `bypass=False`, `llm=(reply is not None)`, `reply_id`, `conversation_id`,
  `api_cost`). Así todo lo que pasa por la tab asistente Y por la tab sección
  cae en la misma historia. `_MODULE_KEY_EVENTS` en `activity_analytics.py`
  incluye `assistant_query`.
- **La respuesta salía cortada (a 600) y con markdown crudo (`**`).** (1) Se
  sacó el `[:600]` del `answer` en los dos puntos de log (`assistant_chat()` y
  `analyze_section()`) → `answer=(reply.answer if reply else "")`. (2) Se agregó
  `answer_html` a cada turno en `build_summary` vía helper `_answer_html()`
  (import lazy de `render_llm_html`), y el template usa `turn.answer_html|safe`
  con fallback a texto plano. Ahora renderiza igual que el panel del asistente
  (negritas, listas). **OJO retro**: eventos viejos ya truncados a 600 siguen
  cortos (dato ya perdido en origen); solo eventos nuevos guardan completo.
- **Viñetas/números desaparecían tras renderizar el markdown.** Tailwind
  (preflight en `base.html`) resetea `ol, ul { list-style: none }` global. Fix:
  en `tools.css`, `.story-timeline__answer ul { list-style: disc }` /
  `ol { list-style: decimal }` (+ padding). Gana por especificidad
  (0,1,1 vs 0,0,1 de Tailwind). Verificado con `getComputedStyle` en una previa
  que carga Tailwind (raw `<ol>` = none; `.story-timeline__answer ol` = decimal).
- **Ingestas fake reaparecían (167/día).** No era el bucket (localhost usa
  carpetas locales). Causa: la suite pytest (TestClient como test@/junior@/
  senior@) + entorno con `LOG_ACTIVITY=VERBOSE` + sin aislar `ACTIVITY_LOG_DIR`
  escribía eventos reales en cada corrida. Fix: fixture autouse en
  `tests/conftest.py` (`_isolate_runtime_logs`) que fuerza `LOG_ACTIVITY="OFF"`
  y redirige `ACTIVITY_LOG_DIR` a un tmp. El `activity_log` local se limpió a
  mano (se quitaron las personas fake, backup `.bak`) — el `.bak` NO se
  commitea.
- **KPI "Consultas a IA (con costo)" en 0 mientras el relato decía "respondió
  la IA".** Se alineó el criterio: `api_cost = (reply is not None) and not
  execution_mode.startswith("simul")` (llamada REAL a OpenAI). El relato marca
  🪙 con costo / "· simulación, sin costo" en consecuencia.
- **Tiles de KPI todos con borde dorado.** Se les dio paleta propia
  (`--kpi-accent` + `.activity-kpi:nth-child(1..5)`: azul/verde/ámbar/naranja/
  dorado) alineada al stylesheet del copiloto.
- **Empty-state del catálogo distinto al global.** Se unificó al
  `.dashboard-empty` canónico (mismo que usa el dashboard cuando no hay datos).

- **Respuestas largas se cortaban con `…[truncated]` — RESUELTO en esta misma
  ronda.** El usuario reinició el server, repitió un prompt largo (árbol de
  diagnóstico de DAC) y seguía cortando. Se midió el evento real logueado:
  `len(answer) = 4012` = exactamente el tope `_MAX_STRING_LEN = 4000` de
  `_safe_value` en `activity_service.py` + `…[truncated]` (NO era el `[:600]`,
  ese ya estaba sacado). Fix: tope por-campo — `_LONG_TEXT_KEYS = {"answer"}` +
  `_MAX_TEXT_LEN = 20000`, `_safe_value` recibe `max_len`; solo `answer` sube a
  20000, el resto sigue en 4000 (log liviano). Ver Pendiente #1 para el detalle
  y la justificación frente a la regla de "no tocar el logger". Verificado por
  unit check de `_safe_value` + `pytest -q` (225 passed / 1 skipped). **Importa
  para la confirmación visual**: eventos viejos ya truncados a 4000 siguen
  cortos (dato perdido en origen); hay que reiniciar local + generar un prompt
  largo NUEVO para verlo completo.

Verificación de esta ronda: `getComputedStyle` con Tailwind cargado (viñetas),
render Jinja de la historia con `answer_html`, unit tests sintéticos de
`build_summary` (escenario real preservando answer de 1202 chars), `pytest -q`
= 225 passed / 1 skipped. Confirmación visual del usuario: el caso corto OK; el
largo pendiente por el bug de arriba.

### 2026-08-05 (2) — Claude — Pendiente #1 resuelto: relato secuencial + fix de test
**Estado: funcional, verificado por render Jinja + suite; falta confirmación
visual del usuario en su sesión real.**

- **Historia de preguntas → timeline narrativo** (Pendiente #1): el detalle
  expandido de cada conversación pasó de badges sueltos a un `<ol
  class="story-timeline">` que se lee de arriba a abajo (preguntó → cómo
  respondió cada turno en prosa → respuesta → cómo lo calificó). Data intacta
  (`assistant_stories[].turns[]`), solo TEMPLATE + CSS. CSS nuevo `.story-timeline*`
  siguiendo el patrón light-first + `.content-theme-dark` (los badges viejos
  `.assistant-story__turn/__badge/__answer/__fb` se removieron del CSS).
- **Fix de test frágil**: `test_activity_log_disabled_by_default` asumía
  `LOG_ACTIVITY` OFF por default y fallaba en entornos con la var en VERBOSE
  (dev/prod). Ahora forza `monkeypatch.setattr(activity_service, "LOG_ACTIVITY",
  "OFF")`. Suite 225 verdes con el entorno real (VERBOSE).
- Verificación: render Jinja del bloque con el escenario real (router 👎 →
  "Explícame vos" IA 🪙 👍) OK; `pytest` 225 passed. Confirmación visual: pendiente
  del usuario.

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
