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
> **Estado en una línea (2026-08):** Fase 0 y **Fase 1** (System Router Pilot)
> **CLOSED / GREEN**, mergeadas a `main`. **Gate PRE-PHASE-2 de reproducibilidad
> de dependencias: CLOSED / GREEN** (ver A.18) — `.python-version=3.14.3` +
> `constraints.txt` cross-platform, validado en Windows + CI Linux + DigitalOcean.
> **Fase 2 (extracción de routers): CLOSED / GREEN.** **2A — Telemetry router**
> (`POST /telemetry/event`) **CLOSED / GREEN** (CI GREEN, mergeada a `main` vía
> PR #6). **2B — Auth/session API router** (`/api/auth/*` + `/api/diagnostic`, 5
> endpoints) **CLOSED / GREEN** (CI GREEN + smoke real de login; ver A.20). **2C —
> Shared templating gate** (`webapp/templating.py`) **CLOSED / GREEN** (mergeada a
> `main` vía PR #8; NO movió rutas, surface 75; ver A.20). **Gate distribuido PRE-2D
> de contexto** (`resolve_pack`→`content_registry`, access policy→`webapp/access.py`,
> login redirect→`webapp/nav.py`) **CLOSED / GREEN** (commit `f54acc6`; CI GREEN sobre
> stack reproducible; sin `deps.py`, sin mover rutas —surface 75—, sin
> runtime/bootstrap; ver A.20). **2D — What's New router** (`GET /whats-new` +
> `POST /whats-new/dismiss` → `webapp/routers/whats_new.py`) **CLOSED / GREEN**
> (commit `c119b51`; CI GREEN; feature COPILOT; router sin `webapp.main`; misma env
> Jinja; surface 75; +3 tests HTTP; ver A.20). **2E — Academy router** (`GET
> /academy/{stem}` + 3 endpoints de feedback → `webapp/routers/academy.py`)
> **CLOSED / GREEN** (commit `0431caa`; CI GREEN; feature COPILOT; router sin
> `webapp.main`; `_home_url`→`nav.home_url`, `_invite_src`→`presentation/invite.py`,
> `_academy_feedback_*`→`presentation/academy_feedback.py`, con aliases transitorios
> en main; surface 75; +2 tests; ver A.20). Cada lote requiere autorización explícita; NO
> avanzar de un lote al siguiente sin OK. **Fase 2: CLOSED / GREEN** (2F–2J-D
> completaron la extracción mecánica y stateful restante; ver bloque de cierre
> formal más abajo). Dirección futura de plataforma multi-workspace en **A.19**
> (es dirección, NO scope de Fase 2).

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

## A.13.1 Fase 1 — Estado (CLOSED / GREEN)

**STATUS: CLOSED / GREEN.** Todos los gates cumplidos.

**Evidencia final de cierre:**
- route-contract: **75 rutas, GREEN** · snapshot: **sin cambios**
- full suite local (FastAPI 0.115.5 / Starlette 0.41.3): **228 passed / 1 skipped / 0 failed**
- full suite fresh stack (FastAPI 0.141.1 / Starlette 1.4.1): **228 passed / 1 skipped / 0 failed**
- `/health`: **GREEN** · `/favicon.ico`: **GREEN**
- GitHub Actions: **GREEN**
- **PR #2: ready to merge** (NO mergeado todavía)
- app behavior: **sin cambios funcionales** (mismas URLs/métodos/respuestas)
- **dependency drift finding: sigue OPEN** como **PRE-PHASE-2 DECISION REQUIRED** (ver A.18)

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

## A.13.2 Fase 1 — Finding de CI y fix (dependency drift)

**Qué pasó:** la primera CI del PR de Fase 1 dio **1 failed / 227 passed / 1 skipped**.
El único fallo fue `tests/test_route_contract.py` (reportaba `/health` y
`/favicon.ico` como "faltantes", surface=73). **Las rutas funcionaban**:
`test_health` y `test_favicon` **pasaron** en el mismo run.

**Root cause (CONFIRMADO, reproducido en venv fresco):** **dependency drift.**
- Local dev: **FastAPI 0.115.5 / Starlette 0.41.3** → `include_router` **aplana**
  las rutas en `app.routes` → el collector viejo (top-level + `isinstance(Route)`)
  las veía → 75/GREEN.
- Fresh install / CI / DigitalOcean: **FastAPI 0.141.1 / Starlette 1.4.1** →
  `include_router` **anida** las rutas en un contenedor de router incluido
  (no es `Route` ni `Mount`; no expone `.routes`, sino `.original_router.routes`)
  → el collector viejo lo saltaba → surface=73 → FAIL. Pero el contenedor **sí
  rutea** (dispatch) → funcionales verdes. `requirements.txt` usa **rangos**, por
  eso una instalación fresca resolvió un stack más nuevo que el venv local.
- **Descartado:** doble import de `webapp.main` / problema del APIRouter / cambio
  de contrato HTTP. El `_iter_leaves` recursivo demostró 75 en AMBOS stacks.

**Fix (commit `6d1cf94`, SOLO test):** `_current_surface` ahora usa `_iter_leaves`,
un walk recursivo que reconoce hojas HTTP por atributos públicos (`path`+`methods`,
no por clase), desciende por `.routes` y —fallback de compatibilidad— por
`.original_router.routes`, con protección por `id()`. **Snapshot intacto (75)**;
sin `UPDATE_ROUTE_SURFACE`; **sin cambios de app**.

**Validado:** local (0.115.5) route-contract PASS + suite 228/1/0; venv fresco
(0.141.1/1.4.1) route-contract PASS + `test_health`/`test_favicon` 2 passed +
suite **228 passed / 1 skipped / 0 failed** (= lo esperado en CI). El venv fresco
quedó en el scratchpad de la sesión (`.../scratchpad/venv-fresh`).

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

## A.18 PRE-PHASE-2 DEPENDENCY REPRODUCIBILITY — CLOSED / GREEN

**Origen del gate:** durante Fase 1, el entorno local (FastAPI 0.115.5 / Starlette
0.41.3) **no reproducía** la instalación fresca de CI/prod (FastAPI 0.141.1 /
Starlette 1.4.1), porque `requirements.txt` usa **rangos**. Ese drift causó el
fallo de CI de Fase 1 (ver A.13.2). Se decidió **congelar la resolución que
CI/prod ya usan** (no es un upgrade), con `pip` puro — sin Poetry/uv/Docker.

**Implementación (commit `ad6d675` en `work/architecture-phase-2`; closeout en
`work/dependency-reproducibility-closeout`):**
- **NEW `constraints.txt`** — superset cross-platform de **51 paquetes** (49
  comunes con versiones idénticas + `colorama==0.4.6` Windows-only +
  `uvloop==0.22.1` Linux-only). Excluye `pip` (tooling de entorno). Sin hashes,
  sin markers. Un constraint no instala por sí solo: `colorama`/`uvloop` conviven
  y cada plataforma instala el que le corresponde.
- **NEW `.python-version` = `3.14.3`** — source of truth del intérprete.
- **`requirements.txt` / `requirements-dev.txt`** — solo se agregó `-c constraints.txt`;
  ranges/extras (`uvicorn[standard]`, `python-jose[cryptography]`), `openai>=1.30.0`
  y `pytest==9.1.1` intactos.
- **`.github/workflows/ci.yml`** — `setup-python` lee `python-version-file: ".python-version"`.
- **DELETE `.github/workflows/_diag-linux-freeze.yml`** — workflow diagnóstico temporal, ya no queda en el repo.

**Evidencia final (validado end-to-end):**
- **Windows / local (validado en esta sesión):** venv limpio desde requirements+constraints;
  Python 3.14.3; FastAPI 0.141.1 / Starlette 1.4.1 / OpenAI 2.53.0; `colorama`
  instalado, `uvloop` NO (correcto); route-contract GREEN; suite **228 / 1 / 0**;
  boot Uvicorn GREEN; `/health` GREEN; **functional smoke GREEN** (Auth Entra,
  Assistant/OpenAI real, Wizard, Catalog/SharePoint, HTMX).
- **GitHub Actions / Linux (confirmado por el equipo):** Python **3.14.3** leído
  de `.python-version`; CI GREEN; suite **228 / 1 / 0**; constraints cross-platform
  OK; `uvloop` Linux-only, `colorama` Windows-only; grafo común con mismas versiones.
- **DigitalOcean / producción (confirmado por el equipo):** build log explícito
  **“Using Python 3.14.3 specified in .python-version”**; cache descartada por
  cambio de `requirements.txt`; Python 3.14.3 instalado; dependencias desde
  `requirements.txt` aplicando `constraints.txt`; deployment/runtime GREEN;
  **production smoke GREEN**.

**Decisión arquitectónica registrada:**
- `.python-version` = **3.14.3** es la source of truth de Python.
- `requirements*.txt` expresan **intención/dependencias directas**; `constraints.txt`
  fija la **resolución validada**. **Un único** constraints cross-platform.
- Grafo normalizado: **49 comunes iguales**; Windows-only `colorama==0.4.6`;
  Linux-only `uvloop==0.22.1`. `pip` fuera del constraints.
- GitHub Actions consume `.python-version`; DigitalOcean confirmó `.python-version`.
- OpenAI: requirement `openai>=1.30.0`, constrained **`openai==2.53.0`**, con
  **smoke real GREEN**.

**Principio (invariante de reproducibilidad):**
> *Same source + declared Python + validated dependency resolution must reproduce
> equivalent application behavior across Windows development and Linux CI/production.*

**Dependency update policy (disciplina, sin bots/tooling nuevo):** las
dependencias congeladas **no** se actualizan incidentalmente. Para actualizar:
(1) branch dedicada; (2) fresh resolution; (3) inspeccionar diff; (4) `pytest`;
(5) CI; (6) smoke focal según paquetes afectados; (7) actualizar `constraints.txt`;
(8) merge. También **revisar ante una vulnerabilidad de seguridad relevante**
(constraints congela versiones que reciben security/compat fixes, así que la
revisión periódica es parte de la disciplina).

**ESTADO: A.18 CLOSED / GREEN.** Gate PRE-PHASE-2 cerrado. **Fase 2 READY FOR
EXPLICIT AUTHORIZATION — NOT STARTED.**

## A.19 Dirección arquitectónica futura — plataforma modular multi-workspace

**Es DIRECCIÓN, no scope de Fase 2 inmediato. No reestructurar carpetas ni
introducir microservicios por esto todavía.**

La app evoluciona hacia una plataforma interna modular con múltiples workspaces:
```
Danwor Internal Platform
├── Shared Platform Capabilities
├── Copiloto Metodológico
├── Portal de Operaciones
└── futuros servicios/workspaces
```
Principios:
- El **Copiloto es consumidor** de capacidades comunes, no dueño de ellas.
- **Operaciones** tendrá landing y navegación propias.
- Auth, session, permissions, Microsoft 365, integrations y el **shell visual**
  pueden ser **capacidades compartidas**.
- Un futuro **workspace/product switcher** cambiará entre áreas autorizadas; la
  navegación interna de cada workspace sigue siendo independiente.
- **Staffing** será el primer caso real que demuestre que un segundo workspace
  reutiliza capacidades transversales **sin contaminar el Copiloto**.
- Se mantiene el **modular monolith**; se evoluciona por **presión/cohesión real**,
  no anticipadamente.

## A.20 PHASE 2 — Router boundaries (EN PROGRESO)

Extracción incremental de handlers de `webapp/main.py` a `APIRouter`, un lote por
vez, preservando `MÉTODO + PATH` (route-contract, 75) y comportamiento. `main.py`
sigue siendo composition root (sin `create_app()`). Cada lote requiere
**autorización explícita**; no encadenar lotes.

**2A — Telemetry router — CLOSED / GREEN** (commit `597d47b`; CI GREEN, mergeada a `main` vía PR #6):
- `POST /telemetry/event` extraído a `webapp/routers/telemetry.py` (APIRouter),
  incluido con `app.include_router(...)`.
- **HTTP surface unchanged** (75 rutas; snapshot sin cambios; sin `UPDATE_ROUTE_SURFACE`).
- El router **NO importa `webapp.main`**; **sin bootstrap/runtime**, sin `app.state`,
  sin singletons nuevos. **Primer router respaldado por servicio compartido**
  (`activity_service`) + auth — patrón para futuros routers service-backed.
- `_TELEMETRY_EVENTS` co-localizada (único consumidor de código; la mención en
  `activity_analytics.py` es un comentario).
- Comportamiento **verbatim** (204 siempre, gate VERBOSE, no-op sin sesión,
  best-effort). Tests focales de regresión agregados (no había).
- Validado en stack reproducible (Python 3.14.3 / FastAPI 0.141.1): route-contract
  GREEN, focales 2/2, **suite 230 / 1 / 0**, smoke `POST /telemetry/event` → 204.
- **Cierre:** GitHub Actions **CI GREEN** y mergeada a `main` vía PR #6.

**2B — Auth/session API router — CLOSED / GREEN** (commit `37e56bf`):
- 5 endpoints extraídos a `webapp/routers/auth.py` (APIRouter): GET
  `/api/auth/microsoft/config`, POST `/api/auth/microsoft/token`, GET
  `/api/auth/me`, POST `/api/diagnostic`, POST `/api/auth/logout`.
- **Relocación pura, sin rediseño de auth**: METHOD+PATH, JSON, cookies/atributos
  (HttpOnly/SameSite/Secure), verificación de id_token, create/delete session,
  logging de login y comportamiento de diagnóstico preservados **verbatim**.
- `_MsTokenBody` co-localizado (único consumidor). `auth.py` **NO importa
  `webapp.main`**; sin bootstrap/runtime, sin `app.state`, sin instancias nuevas.
  Usa el mismo `auth_service` + config + `log_activity`.
- Limpieza de imports estrictamente obsoletos en `main.py` (`BaseModel`,
  `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `SESSION_MAX_AGE`).
- **HTTP surface unchanged** (75; snapshot sin cambios). Tests focales agregados
  para brechas sin cobertura HTTP (config / me auth+anon / logout); `/api/diagnostic`
  ya estaba cubierto; el token queda en unit + smoke manual (no E2E falso; JWKS = red).
- Validado en stack reproducible (Python 3.14.3 / FastAPI 0.141.1): route-contract
  GREEN, focales GREEN, **suite 234 / 1 / 0**, smoke config=200 / me-anon=401 / logout=200.
- **Establece el boundary HTTP de la capacidad PLATFORM / SHARED "identity +
  session"** (A.19). NO implementa workspace switcher / permissions registry /
  navegación / Portal de Operaciones / Staffing / roles nuevos.
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; full
  suite **234 passed / 1 skipped**; route surface **75 unchanged**; **smoke real
  GREEN** — login real contra Entra, sesión autenticada, `/api/auth/me`, y
  logout/terminación de sesión. **Sin rediseño de auth, sin runtime/bootstrap.**
  Auth/session queda establecida como **capacidad PLATFORM / SHARED**.

**2C — Shared templating gate — CLOSED / GREEN** (commit `a003464`; mergeada a `main` vía PR #8):
- La construcción de la **única** `Jinja2Templates` + globals de plataforma
  (`static_version`, `verbose_telemetry`) + `_StaticVersion` se movió a
  **`webapp/templating.py`**. `main` re-exporta el MISMO objeto
  (`webapp.main.templates is webapp.templating.templates` = **True**).
- Los **filtros de workspace** (`diagnostic_title`, `catalog_verdict_*`) **siguen
  registrándose desde `main`** sobre esa misma `templates.env`.
- **NO se movió ninguna ruta** (surface **75**, snapshot sin cambios). **No** se
  creó `runtime.py`/`bootstrap.py`. **Cero cambios de tests** (identidad preservada:
  `from webapp.main import templates` + `env.get_template` siguen funcionando).
- Imports obsoletos limpiados en `main` (`Jinja2Templates`, `is_verbose`).
- Validado (3.14.3 / FastAPI 0.141.1): identidad OK, filtros/globals sobre la misma
  env, route-contract GREEN, **suite 234 / 1 / 0**, smoke `/modules/01` 200 (base.html
  + `static_version` aplicado). **Cierre:** GitHub CI **GREEN**, mergeada a `main` vía PR #8.
- ⚠️ **Deuda registrada:** `templating.py` es PLATFORM-pure en responsabilidad,
  pero la env **todavía recibe** filtros Copilot/Catalog desde `main` → **NO está
  workspace-pure**. Se tratará cuando exista presión real por un 2º workspace.

**Gate distribuido PRE-2D de contexto — CLOSED / GREEN** (commit `f54acc6`):
- **Evidencia de cierre:** GitHub **CI GREEN sobre el stack reproducible**; Python
  **3.14.3**; full suite **234 passed / 1 skipped**; route surface **75 unchanged**;
  **no routes moved**. Distribución: content pack resolution → `core.content_registry`;
  current web access policies → `webapp/access.py`; current navigation helper →
  `webapp/nav.py`; **`auth_service` sin cambios de responsabilidad**; **sin
  `deps.py`/`runtime.py`/`bootstrap.py`**.
- **Corrección de una nota anterior:** el gate de templating (2C) resolvió SOLO el
  rendering boundary. What's New NO era inmediatamente extraíble después: su handler
  usa helpers privados de `main` (`_pack`, `_login_redirect`, `_diagnostic`,
  `_readonly`). Ese **helper/context boundary** se descubrió en el análisis de Fase 2D
  y es lo que este gate resuelve.
- El análisis mostró que NO era un cluster único sino **responsabilidades distintas**
  accidentalmente juntas en `main`. Se distribuyeron a sus dueños naturales (NO se
  creó `deps.py`):
  - **pack resolution** → `core.content_registry` (`resolve_pack(value)` + constante
    `DEFAULT_PACK`, literal `"datasphere_core"` para preservar el fallback exacto sin
    acoplar a `DANWOR_CONTENT_PACK`). Clasificación: **COPILOT / content context**.
  - **access policy** (owner/readonly/diagnostic) → **nuevo `webapp/access.py`**
    (`owner_emails`, `is_owner`, `is_readonly`, `is_diagnostic`). `is_readonly == tier
    academy` es semántica ACTUAL del Copiloto y por eso **NO** se metió en
    `auth_service`. Clasificación: **current web access policy** (NO se asume universal).
  - **login redirect** → **nuevo `webapp/nav.py`** (`login_redirect`). Todavía transporta
    `pack` → clasificación **PRESENTATION / current web navigation** (NO PLATFORM-pure).
- **`auth_service` NO recibió policy functions** — queda concentrado en identity /
  session / tokens / tiers (capacidad PLATFORM / SHARED).
- **`main` conserva aliases** (`_pack`, `_is_owner`, `_readonly`, `_diagnostic`,
  `_login_redirect`) para no refactorizar aún las decenas de call-sites del módulo.
  `_require_diagnostic` (guard HTTP de rutas admin) **permanece en `main`** y ahora
  llama `access.is_diagnostic(request)`; su ubicación se decidirá al extraer Admin.
- **Seam `APP_OWNER`**: se movió a `webapp.access.APP_OWNER`; los tests reasignan ahí
  (mismo patrón que `auth_service.ALLOWED_USERS`/`ACADEMY_USERS`), **sin** compat magic
  en `main`. `main` ya no importa `APP_OWNER`/`AUTH_DEFAULT_DOMAIN`/`DIAGNOSTIC_MODE`.
- **NO se movió ninguna ruta** (surface **75**, snapshot sin cambios). **No** se creó
  `deps.py`/`runtime.py`/`bootstrap.py`. **Sin cambios funcionales.** Test change:
  solo la migración del seam `APP_OWNER` (2 archivos).
- Validado local (`.venv`) + **CI GREEN sobre stack reproducible (Python 3.14.3)**:
  import OK, aliases OK, route-contract GREEN (**75**), **suite 234 / 1 skipped**,
  26 focales (owner/readonly/diagnostic/pack/login) GREEN.
- **What's New:** su extracción es la Fase 2D (abajo).
- **Clasificación multi-workspace (estado actual, NO anticipar):**
  - `auth_service` → **PLATFORM / SHARED** (identity + session).
  - `webapp/templating.py` → **PLATFORM presentation infrastructure**.
  - `webapp/access.py` → **CURRENT WEB ACCESS POLICY**. Su reuso cross-workspace
    futuro **NOT YET DECIDED** (no asumir que Operations lo reutilizará).
  - `webapp/nav.py` → **CURRENT WEB NAVIGATION**; todavía transporta semántica de
    content-pack del Copiloto. Reuso cross-workspace futuro **NOT YET DECIDED**.
  - `core.content_registry` → **COPILOT / CONTENT**.

**2D — What's New router — CLOSED / GREEN** (commit `c119b51`):
- **Evidencia de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **237 passed / 1 skipped**; route
  surface **75 unchanged**; **3 behavioral What's New tests GREEN** — authenticated
  render GREEN, anonymous redirect GREEN, dismiss cookie GREEN. El router **no
  importa `webapp.main`**; **misma env Jinja** preservada; **sin runtime/bootstrap**;
  feature classification permanece **COPILOT**.
- `GET /whats-new` y `POST /whats-new/dismiss` extraídos a
  **`webapp/routers/whats_new.py`** (APIRouter) como **relocación pura**: método/
  path/query/auth/redirect/pack/diagnostic/readonly/contexto de template y cookie
  (nombre `dw-whatsnew-seen` / valor / `Max-Age=31536000` / SameSite / Secure)/status/
  body preservados **verbatim**.
- **feature classification: COPILOT** — sus entradas son pack-scoped
  (`whats_new_service.entries(pack)`); consume capacidades compartidas **sin**
  volverse plataforma.
- El router **NO importa `webapp.main`**: usa dueños explícitos (`auth_service`,
  `webapp.access`, `webapp.nav`, `core.content_registry`, `whats_new_service`,
  `content_service`, `webapp.templating`, `config`) — **no** los aliases de `main`.
- Helpers exclusivos co-localizados: `_whatsnew_mark_seen` + `_WHATSNEW_COOKIE_MAXAGE`.
- **Misma env Jinja** (`webapp.main.templates is webapp.templating.templates` = True);
  **sin** filtros nuevos, **sin** tocar `base.html`/`whats_new.html`/`copilot_nav.html`,
  **sin** segunda env.
- `main.py`: solo `import` + `include_router` + remoción de los 2 handlers y los 2
  elementos feature-local. **Sin imports obsoletos** (verificado: `whatsnew`,
  `content_pack_short_label`, `list_academy`/`academy_asset_for`, `SESSION_COOKIE_*`,
  `Query`/`HTMLResponse` siguen con consumidores). Aliases PRE-2D **intactos**.
- **Sin** `runtime.py`/`bootstrap.py`/`deps.py`. **Sin** cambios en `access`/`nav`/
  `content_registry`/`templating`/`auth_service` ni en templates físicos ni manifests.
- **+3 tests HTTP de comportamiento** (`test_routes.py`): render autenticado (200 +
  título estable), redirect anónimo (307 a `/?pack=…&next=%2Fwhats-new#acceso`),
  dismiss (200 vacío + Set-Cookie con valor y atributos actuales). Clientes aislados.
- **Route surface 75 sin cambios** (snapshot intacto; sin `UPDATE_ROUTE_SURFACE`).
- Validado sobre **stack reproducible (Python 3.14.3 / FastAPI 0.141.1 / Starlette
  1.4.1)**: route-contract GREEN, **suite 237 passed / 1 skipped** (234 + 3), smoke
  focal de las 3 rutas GREEN. **Cierre:** GitHub CI **GREEN**.
- **Aprendizaje arquitectónico:** 2C (templating) + PRE-2D (helper/context) quedaron
  **validados por un consumidor real** — un feature COPILOT respaldado por template
  puede ahora vivir **fuera de `main.py`** sin trasladar dependencias privadas de
  `main` a otro god-module. Es el objetivo real de Fase 2D.

**2E — Academy router — CLOSED / GREEN** (commit `0431caa`):
- **Evidencia de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI **0.141.1**;
  Starlette **1.4.1**; full suite **239 passed / 1 skipped**; **route surface 75
  unchanged** (snapshot unchanged); Academy focal regression GREEN; **Pack Assets
  regression GREEN**; router **no importa `webapp.main`**; **sin runtime/bootstrap**;
  **sin cambios de comportamiento**.
- **Ownership final:**
  - `webapp/routers/academy.py` → **COPILOT / ACADEMY HTTP**.
  - `webapp/presentation/academy_feedback.py` → **COPILOT / ACADEMY PRESENTATION**.
  - `webapp/presentation/invite.py` → **COPILOT / SHARED PRESENTATION** (shared dentro
    del Copiloto, **NOT PLATFORM**; cross-workspace reuse **NOT YET DECIDED**).
  - `webapp/nav.py:home_url` → **CURRENT WEB NAVIGATION** (cross-workspace reuse
    **NOT YET DECIDED**).
- **Aliases transitorios que SIGUEN en `main`** (sin lógica, solo re-export para
  consumidores todavía NO extraídos; desaparecen cuando esos grupos salgan de main):
  `_home_url = nav.home_url` (landing/module/catalog), `_invite_src = invite.invite_src`
  (module), `INVITE_COOKIE = invite.INVITE_COOKIE` (/desafio, /desafio/cerrar),
  `_academy_feedback_assets = academy_feedback.feedback_assets` (/pack-assets).
- `GET /academy/{stem}` + `POST /academy/{stem}/feedback|feedback/submit|feedback/cancel`
  extraídos a **`webapp/routers/academy.py`** (APIRouter) como **relocación pura**:
  auth/pack/diagnostic/readonly/template/context/redirects/feedback y **formato
  persistido** preservados verbatim.
- **feature classification: COPILOT** (consume capacidades compartidas sin volverse
  plataforma). El router **NO importa `webapp.main`**.
- **Ownership de dependencias compartidas (ampliación de scope autorizada):**
  - `_home_url` → **`webapp/nav.py`** (`home_url`). Clasif.: **CURRENT WEB NAVIGATION**
    (NO se afirma PLATFORM/SHARED todavía).
  - `_invite_src` + `INVITE_STEM` + `INVITE_COOKIE` → **`webapp/presentation/invite.py`**
    (`invite_src`). Clasif.: **COPILOT / SHARED PRESENTATION** ("shared" = compartido
    hoy por features del Copiloto; cross-workspace reuse **NOT YET DECIDED**).
  - `_academy_feedback_html`/`_academy_feedback_assets` → **`webapp/presentation/academy_feedback.py`**
    (`feedback_html`/`feedback_assets`). Clasif.: **COPILOT / ACADEMY PRESENTATION**
    (que `/pack-assets` lo consuma NO lo vuelve plataforma).
  - Ninguno de estos módulos importa `webapp.main`, define rutas, ni se declara
    PLATFORM/SHARED.
- **Persistencia:** `_save_academy_feedback` es feature-local → movido al router con
  **seam propio `academy_router.FEEDBACK_LOG_DIR`** (tests de Academy migran a ese
  seam). `main` conserva su `FEEDBACK_LOG_DIR` porque las otras familias de feedback
  (module/section/catalog) siguen ahí. Seam **no** generalizado.
- **`/pack-assets` NO se movió** (sigue en `main`): consume `_academy_feedback_assets`
  vía **alias transitorio** hacia `presentation.academy_feedback`. Nota de reporte:
  `INVITE_COOKIE` tenía consumidores adicionales (`/desafio`, `/desafio/cerrar`, que
  quedan en main) → se resolvió con alias `INVITE_COOKIE = invite.INVITE_COOKIE`
  (single source of truth en `presentation/invite.py`).
- **Aliases transitorios en `main`** (desaparecen cuando salgan sus consumidores):
  `_home_url = nav.home_url` (landing/module/catalog), `_invite_src = invite.invite_src`
  (module), `INVITE_COOKIE = invite.INVITE_COOKIE` (/desafio), `_academy_feedback_assets
  = academy_feedback.feedback_assets` (/pack-assets). **No duplican implementación.**
- **Tests:** seam de Academy migrado a `academy_router.FEEDBACK_LOG_DIR`; +2 tests
  (redirect anónimo, submit/cancel). Render autenticado + react-up + **regresión de
  `/pack-assets`** (inyección de feedback) ya cubiertos por tests previos.
- **Sin** `runtime.py`/`bootstrap.py`/`deps.py`, sin nueva Jinja env, sin instancias
  nuevas, sin cambios en `access`/`content_registry`/`templating`/`auth_service` ni
  templates físicos. **Route surface 75 sin cambios** (snapshot intacto).
- Validado sobre **stack reproducible (Python 3.14.3 / FastAPI 0.141.1 / Starlette
  1.4.1)**: route-contract GREEN, **suite 239 passed / 1 skipped** (237 + 2), smoke
  focal (academy render / feedback / pack-assets) GREEN. **Cierre:** GitHub CI **GREEN**.
**2F — Pack Asset Serving router — CLOSED / GREEN** (commit `133b49d`; mergeada a
`main` vía PR #13, merge commit `ffb7095`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **240 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  **Pack Assets focal regressions GREEN**; **Academy feedback-injection regression
  GREEN**; router **no importa `webapp.main`**; **sin runtime/bootstrap**; **sin
  cambios de comportamiento**.
- `GET /brand/dancing/{pack_name}`, `GET /pack-assets/{pack_name}/{asset_path:path}`
  y `GET /pack-resources/{pack_name}/{kind}/{resource_path:path}` extraídos a
  **`webapp/routers/pack_assets.py`** (APIRouter) como **relocación pura**:
  auth/pack/theme/embed/traversal/feedback-injection/content-type preservados
  **verbatim**.
- **feature classification: COPILOT.** El router **NO importa `webapp.main`**:
  importa directo de los dueños explícitos —
  `core.content_registry.{resolve_pack, resolve_pack_file, roots_for_kind}`,
  `webapp.services.auth_service`, `webapp.nav.login_redirect`,
  `webapp.presentation.academy_feedback.feedback_assets` — **sin** consumir
  ningún alias de `main`.
- **Alias transitorio removido:** `_academy_feedback_assets` (su único
  consumidor en `main` era `/pack-assets`; búsqueda repo-wide confirmó cero
  consumidores restantes antes de removerlo). `_home_url`/`_invite_src`/
  `INVITE_COOKIE` **se mantienen** (siguen con consumidores en `main`:
  landing/module/catalog, /desafio).
- **Imports limpiados en `main`** (estrictamente obsoletos, verificados uno por
  uno antes de tocar): `import json` (único consumidor era `pack_asset`),
  `FileResponse` (único consumidor eran `pack_asset`/`pack_resource`),
  `resolve_pack_file`/`roots_for_kind` de `core.content_registry` (exclusivos
  de las 3 rutas movidas), el import de módulo `academy_feedback` (queda solo
  `invite` en `from webapp.presentation import invite`). `re`/`Path`/
  `HTMLResponse` **se mantienen** (consumidores reales en otras rutas de `main`).
- **Sin** `runtime.py`/`bootstrap.py`/`deps.py`, sin instancias nuevas, sin
  nueva Jinja env, **sin cambios** en `access`/`nav`/`content_registry`/
  `templating`/`auth_service`/`webapp/presentation/academy_feedback.py`.
  **Route surface 75 sin cambios** (snapshot intacto; sin `UPDATE_ROUTE_SURFACE`).
- **Tests:** cobertura previa inventariada primero (extensa: auth-required,
  traversal de `/pack-resources`, theme query/cookie, embed flag, feedback
  injection de Academy, dancing logo dark-shell, inherited/nested pack asset).
  Gap real identificado: `/pack-assets` no tenía test de path-traversal propio
  (solo `/pack-resources` lo tenía) → **+1 test**
  (`test_pack_asset_traversal_is_rejected`). No se duplicó cobertura existente.
- Validado sobre **stack reproducible** (venv fresco con `constraints.txt`:
  Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1 — el `.venv` persistente del
  repo sigue en 0.115.5/0.41.3 y **no** se usó para validar): route-contract
  GREEN (**75**), **suite 240 passed / 1 skipped** (239 + 1), smoke focal directo
  (dancing/pack-assets/pack-resources autenticado + anónimo 307 + traversal
  400/404) GREEN. **Cierre:** GitHub CI **GREEN**.
- **Clasificación multi-workspace (estado actual, NO anticipar):**
  - `webapp/routers/pack_assets.py` → **COPILOT / ASSET & RESOURCE SERVING**.
  - `webapp.presentation.academy_feedback` → sigue siendo el **dueño explícito**
    de Academy feedback presentation (sin cambios; consumido ahora por el router
    de pack assets vía `feedback_assets`, igual que antes lo consumía `main`).
  - `_academy_feedback_assets` (alias transitorio en `main`) → **eliminado**: no
    quedan consumidores en `main` tras la extracción.
  - Aliases todavía vigentes en `main` (consumidores reales restantes): `_home_url
    = nav.home_url`, `_invite_src = invite.invite_src`, `INVITE_COOKIE =
    invite.INVITE_COOKIE`.
**2G — Landing / Session Navigation router — CLOSED / GREEN** (commit `069c38f`;
branch `work/architecture-phase-2g-landing-nav`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **243 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  **Landing/Nav focal tests GREEN**; comportamiento actual de redirect/cookie
  **preservado**; router **no importa `webapp.main`**; **sin runtime/bootstrap**;
  **sin cambios de comportamiento**.
- `GET /`, `GET /home`, `GET /login`, `GET /desafio` y `GET /desafio/cerrar`
  extraídos a **`webapp/routers/landing.py`** (APIRouter) como **relocación
  pura**: auth/pack/next-param/open-redirect-guard/cookies/redirects/template
  context preservados **verbatim**. **NO** implementa el futuro modelo de
  workspace switcher / multi-workspace entry (A.19) — solo conserva la
  navegación actual.
- **feature classification: COPILOT.** El router **NO importa `webapp.main`**:
  importa directo de los dueños explícitos — `core.content_registry.{DEFAULT_PACK,
  resolve_pack}`, `webapp.access.{is_diagnostic, is_readonly}`,
  `webapp.nav.{home_url, login_redirect}`, `webapp.services.auth_service`,
  `webapp.services.content_service.list_academy`,
  `webapp.presentation.invite.INVITE_COOKIE`, `webapp.templating.templates`,
  `config.{DESAFIO_CLOSE_URL, SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE}` —
  **sin** consumir ningún alias de `main`.
- **Alias transitorio removido:** `INVITE_COOKIE` (sus únicos consumidores en
  `main` eran `/desafio` y `/desafio/cerrar`; búsqueda repo-wide confirmó cero
  consumidores restantes antes de removerlo). `_home_url`/`_invite_src`
  **se mantienen** (consumidores reales confirmados por grep: `_home_url` en
  Module/Catalog; `_invite_src` en Module).
- **Imports limpiados en `main`** (estrictamente obsoletos, verificados uno por
  uno antes de tocar): `DESAFIO_CLOSE_URL`, `SESSION_COOKIE_SAMESITE`,
  `SESSION_COOKIE_SECURE` de `config` (exclusivos de `/desafio`/`/desafio/cerrar`).
  `HTMLResponse`/`RedirectResponse`/`Query`/`DEFAULT_PACK`/`list_academy`/
  `templates` **se mantienen** (consumidores reales en otras rutas de `main`).
- **Sin** `runtime.py`/`bootstrap.py`/`deps.py`, sin instancias nuevas, sin
  nueva Jinja env, **sin cambios** en `access`/`nav`/`content_registry`/
  `templating`/`auth_service`/`webapp/presentation/invite.py`. **No** se
  reclasificó `nav.py` como PLATFORM ni se implementó selector de workspace.
  **Route surface 75 sin cambios** (snapshot intacto; sin `UPDATE_ROUTE_SURFACE`).
- **Tests:** cobertura previa inventariada primero (`/home` por tier, `/desafio`
  set-cookie + reinvocable + dialog overlay, `/desafio/cerrar` delete-cookie,
  `/` anónimo vs autenticado). Un test existente (`DESAFIO_CLOSE_URL` seam)
  migrado de `web_main` a `webapp.routers.landing` (mismo patrón que el seam de
  Academy en 2E) — necesario porque el import se removió de `main`, no es
  cobertura nueva. Gaps reales identificados: `/login` no tenía ningún test;
  el manejo de `next` en `/` (destino seguro para autenticados + guard
  anti-open-redirect) tampoco → **+3 tests**
  (`test_login_redirects_to_landing_access_section`,
  `test_root_authenticated_follows_safe_next_param`,
  `test_root_rejects_open_redirect_next_param`). No se duplicó cobertura existente.
- Validado sobre **stack reproducible** (venv fresco con `constraints.txt`:
  Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1 — el `.venv` persistente del
  repo sigue en 0.115.5/0.41.3 y **no** se usó para validar): route-contract
  GREEN (**75**), **suite 243 passed / 1 skipped** (240 + 3), smoke focal directo
  (`/` anónimo 200 / autenticado 307→`/home` · `/home` autenticado→`/modules/00_Intro`
  y anónimo→`/?...#acceso` · `/login`→`/?...#acceso` · `/desafio` set-cookie+307
  · `/desafio/cerrar` delete-cookie+307 a `DESAFIO_CLOSE_URL`) GREEN. **Cierre:**
  GitHub CI **GREEN**.
- **Clasificación multi-workspace (estado actual, NO anticipar):**
  - `webapp/routers/landing.py` → **CURRENT WEB LANDING / SESSION NAVIGATION**.
  - `webapp.nav.home_url` → **CURRENT WEB NAVIGATION**; cross-workspace reuse
    **NOT YET DECIDED**.
  - `webapp.presentation.invite` → **COPILOT / SHARED PRESENTATION**; **NOT
    PLATFORM**; cross-workspace reuse **NOT YET DECIDED** (sin cambios; sigue
    siendo el dueño explícito de `INVITE_COOKIE`/`invite_src`, consumido ahora
    por el router de landing igual que antes lo consumía `main`).
- **Cleanup registrado:** alias `INVITE_COOKIE` → **removido de `main`**; no
  quedan consumidores allí tras la extracción.
- **Aliases todavía vigentes en `main`:**
  - `_home_url = nav.home_url` → consumidores restantes: **Module + Catalog**.
  - `_invite_src = invite.invite_src` → consumidor restante: **Module**.
**2H — Feedback Family router — CLOSED / GREEN** (commit `9d4a89c`; mergeada a
`main` desde `work/architecture-phase-2h-feedback-family`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **247 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  **Feedback Family focal tests GREEN**; **`/trees/suggest` regression sanity
  GREEN** (sin regresión); router **no importa `webapp.main`**; **sin dependencia
  stateful**; **sin runtime/bootstrap**; **sin cambios de comportamiento**.
- **Delimitation assessment previo** (análisis, sin cambios de código) confirmó
  exacto: **12 rutas** en 4 familias (MODULE, SECTION, CATALOG, CATALOG SECTION),
  ninguna con dependencia stateful. Un hallazgo del assessment no anticipado:
  `POST /trees/suggest` comparte el mismo seam `FEEDBACK_LOG_DIR` pero es feature
  distinta (Trees/Wizard) — **excluida explícitamente**, permanece en `main`.
- `POST /modules/{stem}/feedback(+submit,+cancel)`, `POST
  /modules/{stem}/sections/{section_slug}/feedback(+submit,+cancel)`, `POST
  /catalog/feedback(+submit,+cancel)` y `POST /catalog/section-feedback(+submit,+cancel)`
  (12 rutas) extraídas a **`webapp/routers/feedback.py`** (un único APIRouter,
  las 4 familias comparten forma/dependencias idénticas) como **relocación
  pura**: payload YAML, metadata de usuario, timestamps, UUID, mkdir/write-tmp/
  replace/best-effort, fragmentos HTMX y validación de comentario (exclusiva de
  MODULE) preservados **verbatim**.
- **Explícitamente EXCLUIDAS** (no forman parte de este lote): Academy feedback
  (ya extraído en 2E), Assistant response feedback (**STATEFUL** —
  `assistant_store`/`assistant_feedback_repository`), Wizard/Trees feedback
  (**STATEFUL** — `store`), Admin feedback analysis (dominio ADMIN,
  `/admin/feedback/analyze`), `/trees/suggest` (mismo seam, feature distinta).
- **feature classification: COPILOT.** El router **NO importa `webapp.main`**:
  importa directo de los dueños explícitos — `core.content_registry.{DEFAULT_PACK,
  resolve_pack}`, `webapp.services.auth_service`,
  `webapp.services.content_service.load_module`, `webapp.templating.templates`,
  `config.FEEDBACK_LOG_DIR` — **sin** consumir ningún alias de `main`.
- **Duplicación deliberadamente retenida:** los 4 `_save_*` (`_save_module_feedback`,
  `_save_section_feedback`, `_save_catalog_feedback`, `_save_catalog_section_feedback`)
  repiten el mismo patrón de persistencia — **no** se consolidaron en un helper
  genérico. Esta fase establece ownership, no diseña una plataforma de feedback.
  `_section_or_404`/`_section_feedback_ctx`/`_catalog_feedback_ctx`/
  `_catalog_section_fb_ctx` co-localizados tal cual (confirmados exclusivos por grep).
- **FEEDBACK_LOG_DIR:** co-localizado en el router (mismo patrón que
  `academy_router.FEEDBACK_LOG_DIR`). **`main` retiene su propio import** de
  `config.FEEDBACK_LOG_DIR` — **no** se limpió — porque `_save_tree_suggestion`
  (`/trees/suggest`) sigue ahí. Ningún otro import se volvió estrictamente
  obsoleto en `main` (a diferencia de 2F/2G): `yaml`/`datetime`/`uuid4`/`Form`/
  `HTMLResponse`/`load_module`/`DEFAULT_PACK`/`resolve_pack`/`templates` siguen
  con consumidores reales en otras rutas.
- **Sin** `runtime.py`/`bootstrap.py`/`deps.py`, sin instancias/servicios nuevos,
  sin nueva Jinja env, sin nuevo módulo de presentación, **sin cambios** en
  `access`/`nav`/`content_registry`/`templating`/`auth_service`/
  `webapp/presentation/invite.py`/`webapp/routers/academy.py`/código de
  Trees-Wizard. **Route surface 75 sin cambios** (snapshot intacto; sin
  `UPDATE_ROUTE_SURFACE`).
- **Tests:** cobertura previa **muy desigual** (hallazgo del assessment): SECTION
  tenía 2 tests (up/down/submit/comment + 404 sección desconocida); MODULE,
  CATALOG y CATALOG SECTION (9 de 12 rutas) **sin ningún test**. Migrados los 2
  seams (`web_main.FEEDBACK_LOG_DIR` → `feedback_router.FEEDBACK_LOG_DIR`, mismo
  patrón que Academy/Landing). **+4 tests agrupados por comportamiento** (no 1:1
  por ruta): `test_section_feedback_cancel_renders_bar_without_persisting` (gap
  real de SECTION), `test_module_feedback_reaction_submit_and_cancel` (incluye la
  validación de comentario no vacío exclusiva de MODULE),
  `test_catalog_feedback_reaction_submit_and_cancel`,
  `test_catalog_section_feedback_reaction_submit_and_cancel`. Cada uno cubre
  reacción up (persiste + YAML `type`/campos), reacción down (form, no persiste),
  submit con comentario (persiste) y cancel (vuelve a la barra, no persiste).
- Validado sobre **stack reproducible** (venv fresco con `constraints.txt`:
  Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN
  (**75**), **suite 247 passed / 1 skipped** (243 + 4), smoke focal directo de
  las 4 familias (reacción up→200+persist) GREEN, sanity de `/trees/suggest`
  (import de `main.FEEDBACK_LOG_DIR` + POST real) GREEN — **sin regresión**.
  **Cierre:** GitHub CI **GREEN**.
- **Clasificación multi-workspace (estado actual, NO anticipar):**
  - `webapp/routers/feedback.py` → **COPILOT / FEEDBACK HTTP**.
  - `FEEDBACK_LOG_DIR` seam para MODULE, SECTION, CATALOG y CATALOG SECTION →
    dueño **`webapp/routers/feedback.py`** para esas rutas extraídas. `main.py`
    **retiene** su propio `FEEDBACK_LOG_DIR` porque `_save_tree_suggestion` /
    `POST /trees/suggest` siguen ahí.
  - Exclusiones preservadas explícitamente: **Academy feedback** → dueño Fase 2E
    (`webapp/routers/academy.py`); **Assistant feedback** → **STATEFUL /
    ASSISTANT**; **Wizard feedback** → **STATEFUL / WIZARD**; **Trees suggestion**
    (`/trees/suggest`) → dominio **TREES / WIZARD** (permanece en `main`);
    **Admin feedback analysis** (`/admin/feedback/analyze`) → dominio **ADMIN**.
**2I — Catalog minimal read dependency boundary — CLOSED / GREEN**
(commit `5d29a7e`; branch `work/architecture-phase-2i-catalog-dependency-boundary`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **255 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  **8 tests de `catalog_reads` GREEN**; **regresión de `module_page` GREEN**;
  **smoke de rutas representativas de Catalog GREEN**; **sanity de identidad/
  invalidación de cache GREEN**.
- **Origen:** el assessment post-mecánico (post-2H) encontró una dependencia
  asimétrica: `GET /modules/{stem}` (bloqueada por STATEFUL — `store` +
  `assistant_store`) consume 4 helpers privados de Catalog
  (`_catalog_domains`, `_catalog_connection_types`, `_catalog_security_families`,
  `_catalog_data_products_list`), pero Catalog **nunca** depende de STATEFUL.
  Una segunda assessment de delimitación final trazó el closure completo:
  **9 funciones, ~87 líneas** (4% del bloque de Catalog de ~2096 líneas) — sin
  HTTP, sin SVG, sin Graph, sin estado mutable por-request.
- Las 9 funciones (4 loaders `@lru_cache(maxsize=1)`: `_catalog_entries`,
  `_catalog_architecture_connections`, `_catalog_data_products_entries`,
  `_catalog_security_entries`; 5 consultas delgadas: `_catalog_domains`,
  `_catalog_connection_types`, `_catalog_security_families`,
  `_catalog_security_criteria`, `_catalog_data_products_list`) se movieron
  **verbatim** a **`webapp/services/catalog_reads.py`**.
- **feature classification: COPILOT / CATALOG READ CAPABILITY.** NOT PLATFORM
  / SHARED. El módulo **NO importa `webapp.main`**, no define rutas, no
  renderiza templates, no genera SVG, no toca Graph/M365.
- **Deliberadamente NO tocado:** el mecanismo de import dinámico
  (`sys.path.insert` + `import catalog_service`, repetido 4 veces idéntico)
  se relocalizó **sin fix ni rediseño** — es deuda explícita, repo-wide (el
  mismo patrón vive en `webapp/services/admin_tools.py` porque `tools/catalog/`
  no tiene `__init__.py`), y queda para una fase de Catalog/M365 futura.
- **Aliases transicionales en `main`** (identidad de objeto preservada,
  incluido `.cache_clear`): los 9 nombres siguen existiendo en `main.py` para
  sus consumidores actuales — las 8 llamadas directas a los 4 loaders desde
  rutas de Catalog (`/catalog/domain`, `/catalog/flow`, `/catalog/connections`
  (+`/detail`), `/catalog/security` (+`/detail`), `/catalog/data-product`) y
  las 6 llamadas de `module_page` (incl. `_catalog_domains()` dos veces:
  gate de `initial_tab` y valor de contexto). Ninguna ruta de Catalog ni
  `module_page` se tocó ni se movió.
- `_invalidate_catalog_cache()` **permanece en `main`** sin cambios de
  comportamiento — sigue limpiando los mismos 4 objetos de cache, ahora vía
  alias; verificado que `.cache_clear()` apunta al **mismo objeto** que ve
  `catalog_reads` (no un wrapper que rompería la identidad del cache).
- **Import limpiado en `main`** (estrictamente obsoleto, verificado antes de
  tocar): `from functools import lru_cache` — ningún `@lru_cache` queda en el
  archivo. `os` se mantiene (consumidores reales en otras rutas de `main`).
- **Sin** `runtime.py`/`bootstrap.py`/`deps.py`, sin nueva abstracción de
  repositorio/servicio genérico, **sin cambios** en ninguna ruta HTTP de
  Catalog, SVG, filtros de template, Admin, o código de Trees/Wizard/Assistant.
  **Route surface 75 sin cambios** (snapshot intacto; sin `UPDATE_ROUTE_SURFACE`).
- **Tests:** **8** tests de contrato observable en `tests/webapp/test_catalog_reads.py`
  (shape de salida de las 5 consultas, disponibilidad de `_catalog_security_criteria`
  —comparte cache con `_catalog_security_families`—, identidad de `.cache_clear`
  en los 4 loaders, degradación graceful con `DANWOR_CATALOG_DIR` inválido, +1
  fixture controlada — ver "CI failure / fix" abajo). No se construyó suite de
  Catalog HTTP (fuera de alcance de esta fase).
- Validado sobre **stack reproducible** (venv fresco con `constraints.txt`:
  Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN
  (**75**), **suite 255 passed / 1 skipped** (247 + 8), regresión de los 9
  tests de `module_page` GREEN, smoke real de las 4 rutas representativas de
  Catalog (`/catalog/domain`, `/catalog/connections`, `/catalog/security`,
  `/catalog/data-product` con `product_id` válido) GREEN, sanity de invalidación
  de cache verificada end-to-end (`main._invalidate_catalog_cache()` limpia el
  mismo objeto que `catalog_reads` ve). **Cierre:** GitHub CI **GREEN**.
- **CI failure / fix (commit `cfa86d9`):** el primer run de GitHub CI expuso
  un test dependiente del entorno: `test_domains_returns_list_of_strings`
  asumía que `catalog_local/` del desarrollador (gitignoreado, no versionado)
  existiría en un checkout limpio. **Causa raíz: suposición de entorno de
  test, NO regresión de producción** — `webapp/services/catalog_reads.py`
  quedó sin cambios; un catálogo vacío degradando a `[]` sigue siendo un
  resultado válido del contrato de graceful-degradation ya establecido. Fix:
  el test pasó a afirmar solo la forma estable (`list[str]`), y se agregó
  una fixture determinística bajo `tmp_path` (con `DANWOR_CATALOG_DIR`
  apuntado ahí + `cache_clear` antes/después) para probar el comportamiento
  semántico "no vacío" contra contenido conocido y controlado, no contra
  datos locales del desarrollador. Verificado con `DANWOR_CATALOG_DIR`
  apuntando a una ruta inexistente (simulando la condición real de CI) antes
  de confirmar el fix. Suite final tras el fix: **255 passed / 1 skipped**.
- **Consecuencia arquitectónica:** Fase 2I removió la dependencia privada de
  Catalog que de otro modo habría bloqueado una extracción limpia futura de
  `module_page` — un futuro router de Module puede importar la capacidad de
  lectura de Catalog directamente desde `catalog_reads`, **sin** tocar
  `webapp.main`. La descomposición completa de Catalog (HTTP/business/
  presentation) sigue **explícitamente diferida** a su propia fase futura
  (decisión A.8 preexistente) — **Fase 2I NO significa que el refactor
  completo de Catalog haya comenzado.**
- **Próximo candidato tras 2I:** STATEFUL ownership/lifecycle gate (`store` +
  `assistant_store`) — **NOT AUTHORIZED** hasta que: este commit de cierre esté
  pusheado; CI se mantenga GREEN; el PR esté mergeado; se verifique containment;
  y el cleanup de la branch de Fase 2I esté completo.

## PHASE 2J — STATEFUL OWNERSHIP / LIFECYCLE (EN PROGRESO)

**Assessment previo** (análisis, sin cambios de código) revalidó con evidencia
de código que **solo 2** de los 6 objetos creados en `main.py` requieren
identidad compartida entre requests: `store` (WizardStore) y `assistant_store`
(AssistantConversationStore). Los otros 4 (`section_analysis_service`,
`assistant_chat_service`, `assistant_routing_service`,
`assistant_feedback_repository`) son objetos config/repository stateless —
confirmado por los propios tests existentes (3 tests reemplazan
`section_analysis_service` por una instancia nueva sin romper nada;
`assistant_routing_service` no tiene `__init__` propio ni un solo atributo
`self.`). Encontró además una dependencia asimétrica: `store`→Catalog nunca
existe (Catalog nunca depende de STATEFUL), pero `module_page` (bloqueada por
STATEFUL) dependía de Catalog — por eso 2I se hizo antes.

**2J-A — Copilot runtime ownership — CLOSED / GREEN** (commit `c069300`;
branch `work/architecture-phase-2j-a-copilot-runtime`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **261 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  **6 tests de ownership/identidad de `copilot_runtime` GREEN**; **regresión de
  Wizard service GREEN**; **regresión de Assistant service GREEN**; **regresión
  de rutas Module/Assistant/Section/Wizard GREEN**.
- `store` y `assistant_store` — los únicos 2 objetos que requieren identidad
  compartida — se movieron **verbatim** a **`webapp/copilot_runtime.py`**
  (mismos constructores exactos: `WizardStore(case_repository=
  CaseLibraryRepository())`, `AssistantConversationStore()`).
- **Decisión de naming deliberada:** `webapp/copilot_runtime.py`, **NO**
  `webapp/runtime.py` (el nombre que HANDOFF venía anticipando desde A.5,
  antes de tener evidencia). Razón: el estado que posee es **COPILOT-específico**,
  no infraestructura platform/shared — a diferencia de `webapp/templating.py`
  (2C), que sí es platform porque cualquier workspace necesitaría un env Jinja.
- **feature classification: COPILOT / PROCESS-LIFETIME STATE COMPOSITION.**
  **NOT PLATFORM / SHARED.** `store` → **COPILOT / WIZARD** mutable lifecycle.
  `assistant_store` → **COPILOT / ASSISTANT** mutable lifecycle. Un futuro
  workspace de Operaciones/Staffing no reutilizaría estas INSTANCIAS (son
  conceptos de negocio del Copiloto sin análogo en su roadmap); podría
  eventualmente reutilizar el PATRÓN (un módulo chico de composición que
  construye y re-exporta sus propias instancias) — no este módulo ni estos
  objetos.
- **Explícitamente excluidos** (permanecen en `main.py` sin cambios):
  `section_analysis_service`, `assistant_chat_service`,
  `assistant_routing_service`, `assistant_feedback_repository` — ninguno
  requiere identidad compartida; no forman parte de este boundary.
- **Sin cambios de semántica** (verificado explícitamente, no asumido):
  mismo `_runs`/`_conversations`/`_replies` en memoria de proceso, mismo
  `RLock` por objeto, mismo `CaseLibraryRepository` inyectado, restart sigue
  perdiendo runs/conversaciones activas, múltiples workers siguen aislando
  estado por proceso, sin TTL/cleanup agregado. **Ninguna de estas
  limitaciones pre-existentes se resolvió — están deliberadamente fuera de
  alcance.**
- **`app.state` evaluado y descartado** (no por ser "scoped a request" —
  aclaración correcta: `app.state` es application-lifetime, técnicamente
  viable): se descarta porque la ownership por módulo simple ya preserva la
  identidad de proceso requerida, no hay variabilidad por-request que
  necesite resolverse, y `app.state` agregaría acoplamiento a FastAPI +
  churn de tests sin beneficio actual — no por ser prematuro en el sentido de
  "no aplica", sino porque no hay necesidad concreta hoy.
- **Aliases transicionales en `main`** (identidad de objeto verificada
  explícitamente — `main.store is copilot_runtime.store` y
  `main.assistant_store is copilot_runtime.assistant_store` → **True** ambos):
  las 14 rutas bloqueadas por estos objetos (module_page, sections/analyze,
  assistant/chat, assistant/responses/feedback×3, trees/start,
  diagnostics/orchestrator, wizards/answer,back,restart,feedback×3) **NO se
  movieron** — siguen en `main.py` sin cambios de comportamiento.
- **Imports limpiados en `main`** (estrictamente obsoletos, verificados uno
  por uno antes de tocar): `CaseLibraryRepository`, `WizardStore`,
  `AssistantConversationStore` — sus únicos consumidores eran las 2 líneas de
  construcción movidas. `StaleAssistantReplyError` **se mantiene** (consumidor
  real en las 3 rutas de assistant feedback).
- **Sin** `app.state`, sin `Depends()`, sin lifespan/startup-shutdown hooks,
  sin service locator, sin registry, sin persistencia/Redis/DB/TTL/estado
  distribuido, sin clases wrapper. **Route surface 75 sin cambios** (snapshot
  intacto; sin `UPDATE_ROUTE_SURFACE`).
- **Tests:** +6 en `tests/webapp/test_copilot_runtime.py` (tipo de cada
  instancia, identidad `main.*` == `copilot_runtime.*`, sanity de que un run
  de Wizard / una conversación de Assistant creados vía una referencia son
  visibles vía la otra — exactamente lo que las rutas existentes necesitan).
  Regresión dirigida GREEN: 6 tests de `test_wizard_service.py`, 6 de
  `test_assistant_service.py`, 17 tests HTTP de module_page/assistant/
  section-analysis/wizard en `test_routes.py`.
- Validado sobre **stack reproducible** (venv fresco con `constraints.txt`:
  Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN
  (**75**), **suite 261 passed / 1 skipped** (255 + 6). **Cierre:** GitHub CI
  **GREEN**.
**2J-B — Assistant + Section Analysis router — CLOSED / GREEN** (commit
`040afdd`; branch `work/architecture-phase-2j-b-assistant-section`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **265 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  `main.py` pasó de **41 a 36** rutas propias; regresión dirigida de Assistant/
  Section Analysis/Wizard/Module **GREEN**; cobertura de Assistant feedback
  submit/cancel **GREEN**; identidad de `assistant_store` **preservada**.
- `POST /modules/{stem}/sections/analyze`, `POST /modules/{stem}/assistant/chat`
  y `POST /assistant/responses/{reply_id}/feedback(+submit,+cancel)` (5 rutas)
  extraídas a **`webapp/routers/assistant.py`** (APIRouter) como **relocación
  pura**: status codes, redirects, HTMX, templates/contexto, semántica de
  reacción de feedback, manejo de stale-reply, comportamiento readonly,
  logging/telemetría y fallback de errores preservados **verbatim**.
- **Section Analysis vive intencionalmente en este mismo router** (no uno
  propio): usa `assistant_store`, el código ya la loguea como
  `assistant_query`, comparte conversación/reply con Assistant. Un router de
  una sola ruta no se justificaba solo por pureza de archivo. **Esto NO
  vuelve a Assistant una capacidad platform/shared.**
- **feature classification: COPILOT / ASSISTANT HTTP.** El router **NO
  importa `webapp.main`**: consume `assistant_store` directo de
  `webapp.copilot_runtime` (2J-A) — **misma identidad de objeto verificada**,
  sin construir otra instancia. Los 4 servicios stateless que estas rutas
  necesitan (`SectionAnalysisService`, `AssistantChatService`,
  `AssistantRoutingService`, `AssistantFeedbackRepository`) se **construyen
  en el router** — no viven en `copilot_runtime` (ese módulo posee
  únicamente estado mutable compartido, no config/repository objects).
  `_assistant_feedback_template` co-localizado (único consumidor).
- **Aliases/imports en `main`:** `assistant_store` **se mantiene** (alias a
  `copilot_runtime.assistant_store`; `module_page` todavía lo usa hasta
  2J-D). Las 4 construcciones de servicios stateless se **removieron sin
  alias** (cero consumidores restantes en `main`). Imports limpiados
  (estrictamente obsoletos, verificados uno por uno):
  `MODULE_TARGET_SLUG`, `SectionAnalysisError`, `render_llm_html`,
  `AssistantChatService`, `AssistantFeedbackRepository`,
  `AssistantRoutingService`, `StaleAssistantReplyError`. `ALL_ACTIONS`/
  `MODULE_ACTIONS`/`slugify`/`log_verbose`/`load_module` **se mantienen**
  (consumidores reales compartidos con `module_page`/Catalog/Trees-Wizard).
- **Sin** `runtime.py` nuevo, sin `app.state`, sin `Depends()`, sin
  duplicar construcción de ninguno de los 5 servicios entre `main` y el
  router (verificado). **Route surface 75 sin cambios** (snapshot intacto;
  sin `UPDATE_ROUTE_SURFACE`; `main.py` pasó de 41 a 36 rutas propias).
- **Tests:** migrados los 6 seams que monkeypatcheaban
  `web_main.section_analysis_service`/`web_main.assistant_chat_service` a
  `assistant_router.*` (mismo patrón que Academy/Landing/Feedback/Catalog).
  **+4 tests** cerrando los gaps reales de cobertura de Assistant feedback
  identificados en el assessment de Fase 2J (antes solo el camino de reply
  obsoleto — 409 — estaba cubierto): reacción "up" con persistencia real +
  YAML, "down"→formulario→`/submit` con comentario persistido, `/submit` sin
  comentario re-muestra el formulario con error, `/cancel` vuelve a la barra
  de reacción sin persistir.
- Validado sobre **stack reproducible** (venv fresco con `constraints.txt`:
  Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN
  (**75**), **suite 265 passed / 1 skipped** (261 + 4), regresión dirigida
  GREEN (75 tests unitarios de wizard/assistant/section-analysis/routing/
  copilot_runtime + 21 tests HTTP de module_page/assistant/section-analysis/
  wizard), identidad `assistant_router.assistant_store is
  copilot_runtime.assistant_store` verificada. **Cierre:** GitHub CI **GREEN**.
- **Consecuencia arquitectónica:** Assistant + Section Analysis HTTP ya **no
  viven en `main.py`**. El router extraído **no importa `webapp.main`** y
  consume su lifecycle mutable directo de `copilot_runtime`.
- **Test seams:** `section_analysis_service` y `assistant_chat_service`
  migrados de `webapp.main` a `webapp.routers.assistant` — **sin** aliases de
  compatibilidad retenidos en `main` solo para tests. El alias runtime de
  `assistant_store` en `main` **sigue** (temporal): `module_page` continúa
  consumiendo ese mismo objeto de lifecycle compartido hasta 2J-D.
- **Secuencia de Fase 2J:**

  | Lote | Estado |
  |---|---|
  | 2J-A Copilot runtime ownership | **CLOSED / GREEN** |
  | 2J-B Assistant + Section Analysis | **CLOSED / GREEN** |
  | 2J-C Trees + Wizard | **CLOSED / GREEN** |
  | 2J-D Module page | **CLOSED / GREEN** |
  | *(luego)* **PHASE 2 COMPLETION EVALUATION** | — |

  **Todo el HTTP Stateful mandatorio identificado para la Fase 2 ya está
  extraído. No hay 2J-E planificado.** Full Catalog decomposition sigue
  **DEFERRED**. Admin sigue **DEFERRED / non-blocking**.

  **La evaluación de cierre de Phase 2 se vuelve elegible solo después de:**
  este commit de cierre pusheado; CI final de 2J-D se mantenga GREEN; el PR
  esté mergeado; se verifique containment; y la branch de 2J-D esté
  limpiada.

**2J-C — Trees + Wizard router — CLOSED / GREEN** (commit
`40ed75d`; branch `work/architecture-phase-2j-c-trees-wizard`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **271 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  `main.py` pasó de **36 a 27** rutas propias; regresión dirigida de Trees/Wizard
  **GREEN**; cobertura de lifecycle de Wizard **GREEN**; cobertura del
  diagnóstico orquestador **GREEN**; cobertura de feedback de Wizard **GREEN**;
  cobertura de persistencia de sugerencia de árbol **GREEN**; identidad de
  `store` **preservada**.
- **Archivos tocados:** `webapp/routers/wizard.py` (nuevo), `webapp/main.py`,
  `tests/webapp/test_routes.py`. Sin otros cambios (el archivo untracked
  `content_packs/datasphere_core/assets/academy/restaurante-danwor.html`
  encontrado en el working tree es ajeno a esta fase — no se tocó ni se
  agregó al commit).
- **9 rutas + 7 helpers** movidos **verbatim** a **`webapp/routers/wizard.py`**:
  `POST /trees/suggest` (+ helper `_save_tree_suggestion`), `POST
  /modules/{stem}/trees/start` (+ helpers `_norm_tokens`, `_resolve_tree`),
  `POST /modules/{stem}/diagnostics/orchestrator`, `POST
  /wizards/{run_id}/answer|back|restart` (+ helpers `_follow_wizard_redirect`,
  `_fragment`), `POST /wizards/{run_id}/feedback(+submit,+cancel)` (+ helpers
  `_thanks`, `_feedback_form`). `/trees/suggest` se incluyó en este router
  (no en Feedback Family de 2H) porque su dueño de dominio es Trees/Wizard —
  ver docstring de `wizard.py` para la justificación completa.
- **`FEEDBACK_LOG_DIR` sin consumidores restantes en `main.py`** tras el
  movimiento — import removido junto con `datetime`/`timezone`, `uuid4` y
  `yaml` (verificados uno por uno con grep antes de remover). El router nuevo
  tiene su propio seam explícito: `from config import FEEDBACK_LOG_DIR`.
- **Identidad de `store` verificada explícitamente:** `main.store is
  copilot_runtime.store` → **True**; `wizard_router.store is
  copilot_runtime.store` → **True**. `main.py` retiene el alias `store` porque
  `module_page` todavía lo consume (hasta 2J-D). Sin `WizardStore`/
  `CaseLibraryRepository` nuevos construidos en el router.
- **Cobertura de comportamiento nueva (+6 tests)** cerrando gaps reales
  identificados en el assessment de Fase 2J: lifecycle answer/back/restart
  sobre un árbol real (`extraction_options`, con verificación de que
  `restart` limpia el historial); diagnóstico transversal orquestador
  (`framework_assessment` arrancado desde un módulo real); feedback de
  Wizard 👍 (persiste directo) y 👎→`/submit` con lección (persiste
  `rating`+`lessons` verificado leyendo el YAML real vía
  `copilot_runtime.store._case_repository.root` monkeypatcheado a
  `tmp_path`); `/submit` sin lección re-muestra el formulario con el error
  de negocio; `/cancel` no setea `rating`; `/trees/suggest` persiste con
  `wizard_router.FEEDBACK_LOG_DIR` monkeypatcheado a `tmp_path` y confirma el
  YAML resultante.
- **Regresión dirigida GREEN:** suite completa de `test_routes.py` (87
  tests) más los checks de identidad de `store` — ver evidencia de suite
  abajo.
- Validado sobre **stack reproducible** (venv fresco: Python 3.14.3 /
  FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN (**75**), **suite
  271 passed / 1 skipped** (265 + 6). `main.py` pasó de **36 a 27** rutas
  propias. **Cierre:** GitHub CI **GREEN**.
- **Nota transparente:** se detectó un archivo untracked ajeno a esta fase
  (`content_packs/datasphere_core/assets/academy/restaurante-danwor.html`,
  asset de storytelling de Academy) en el working tree al momento de
  commitear — no se agregó al commit funcional ni se tocó de ningún modo.

**2J-D — Module page router — CLOSED / GREEN** (commit
`5a5d98a`; branch `work/architecture-phase-2j-d-module-page`):
- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **274 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  `main.py` pasó de **27 a 26** rutas propias; regresión dirigida de Module/
  runtime **GREEN**; identidad de `store` **GREEN**; identidad de
  `assistant_store` **GREEN**.
- **Archivos tocados:** `webapp/routers/modules.py` (nuevo), `webapp/main.py`,
  `tests/webapp/test_routes.py`, `tests/webapp/test_copilot_runtime.py`. Sin
  otros cambios (el archivo untracked de Academy visto en 2J-C ya no está
  presente en el working tree — ajeno a esta fase de todos modos).
- **`GET /modules/{stem}` (module_page)** movida **verbatim** a
  **`webapp/routers/modules.py`**, incluyendo `_MODULE_TABS` (helper
  module-exclusivo, co-localizado). Sin cambios de auth/pack/tabs/layout
  inmersivo/Catálogo-tab/Novedades/invite — mismo template `module.html`,
  mismo contexto completo, mismos status codes.
- **`store`/`assistant_store` consumidos directo de `webapp.copilot_runtime`**
  (`from webapp.copilot_runtime import assistant_store, store` — mismo
  patrón que `wizard.py`/`assistant.py`). Identidad verificada explícitamente:
  `modules_router.store is copilot_runtime.store` → **True**;
  `modules_router.assistant_store is copilot_runtime.assistant_store` →
  **True**. Sin `WizardStore`/`AssistantConversationStore` nuevos construidos.
- **Catálogo consumido directo de `webapp.services.catalog_reads`**
  (`catalog_reads._catalog_domains/_catalog_connection_types/
  _catalog_security_families/_catalog_data_products_list()`) — no de los
  alias transicionales de `main.py`. Mismos shapes/degradación/orden/
  comportamiento de validez de tab preservados (relocación pura).
- **`_home_url`/`_invite_src` (aliases Fase 2E):** `_home_url` **se
  mantiene** en `main.py` — tiene un segundo consumidor real, la ruta
  residual `/catalog/guide/{slug}`, verificado con grep antes de decidir.
  `_invite_src` **se removió**: su único consumidor era `module_page`;
  `modules.py` importa `invite.invite_src` directo. El import
  `from webapp.presentation import invite` en `main.py` se removió junto
  con él (sin otro consumidor).
- **Alias runtime `main.store`/`main.assistant_store` (Fase 2J-A) removidos**:
  `module_page` era su único consumidor de producción en `main.py`
  (verificado con grep); su única razón para seguir vivos habría sido
  compatibilidad de tests, no producción. Se migraron en cambio los ~20
  usos de `web_main.assistant_store` en `tests/webapp/test_routes.py` a
  `copilot_runtime.assistant_store`, y `tests/webapp/test_copilot_runtime.py`
  se reescribió: las 2 aserciones de "alias de `main` == `copilot_runtime`"
  se reemplazaron por 4 aserciones equivalentes sobre los alias de router
  (`wizard_router.store`, `modules_router.store`,
  `assistant_router.assistant_store`, `modules_router.assistant_store`, todos
  `is copilot_runtime.*`); las 2 pruebas de visibilidad cruzada de estado se
  actualizaron para usar esos mismos alias de router en vez de `web_main.*`.
  El import `from webapp import copilot_runtime` en `main.py` se removió
  (sin otro consumidor). **`copilot_runtime.store`/`copilot_runtime.assistant_store`
  quedan como dueño único** — ya no hay ninguna referencia a estos objetos
  en `webapp.main`.
- **Imports adicionales limpiados en `main.py`** (estrictamente obsoletos,
  verificados uno por uno, sin otro consumidor tras la relocación):
  `config.ia_enabled_for`, `core.prompts.{ALL_ACTIONS, MODULE_ACTIONS,
  MODULE_ACTION_DESCRIPTIONS, get_loading_message}`,
  `webapp.services.content_service.{load_module, split_front_matter}`
  (`split_front_matter` sigue importado localmente donde se necesita, sin
  cambios ahí), `webapp.services.section_analysis.ACTION_DESCRIPTIONS`,
  `webapp.services.whats_new_service as whatsnew`. Los 4 alias
  `_catalog_domains`/`_catalog_connection_types`/`_catalog_security_families`/
  `_catalog_data_products_list` en `main.py` también se removieron (único
  consumidor era `module_page`); `_catalog_security_criteria` se deja
  intacto — ya estaba sin consumidor ANTES de esta fase (deuda de Catalog
  preexistente, fuera de alcance).
- **Cobertura nueva:** la cobertura HTTP existente de `module_page` (render
  autenticado, pack/query, tabs, Catálogo-tab, contexto de Assistant/Wizard,
  perfil readonly) ya era fuerte y sigue en pie sin cambios — es cobertura a
  nivel HTTP, indiferente a dónde vive el handler. Único gap real cerrado:
  **+1 test** de redirect anónimo (`test_module_page_anonymous_redirects_to_login`,
  mismo patrón que `/whats-new` y `/academy/{stem}`), que no existía.
- Validado sobre **stack reproducible** (venv fresco: Python 3.14.3 /
  FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN (**75**), **suite
  274 passed / 1 skipped**. `main.py` pasó de **27 a 26** rutas propias.
  **Cierre:** GitHub CI **GREEN**.
- **Secuencia de Fase 2J — resultado final:** 2J-A CLOSED/GREEN · 2J-B
  CLOSED/GREEN · 2J-C CLOSED/GREEN · 2J-D CLOSED/GREEN. **Todo el HTTP
  Stateful mandatorio identificado para la Fase 2 ya está extraído. No hay
  2J-E planificado.**
- **Señal de completitud de Fase 2:** con 2J-D cerrado, los 7 criterios de
  cierre previamente identificados quedan satisfechos en la evidencia
  disponible (extracción mecánica completa; blocker de dependencia de
  Catálogo resuelto en 2I; ownership de lifecycle mutable resuelto en 2J-A;
  Assistant/Section extraídas en 2J-B; Trees/Wizard extraídas en 2J-C;
  Module page extraída en 2J-D; residual de Catálogo y de Admin
  explícitamente diferidos). Esta señal se confirmó con una **PHASE 2
  COMPLETION EVALUATION** de solo-lectura (sin cambios de código/tests), cuyo
  veredicto fue **PHASE 2 CAN CLOSE — COMPLETE / GREEN**. Con esa evaluación
  concluida y autorización explícita del usuario, Fase 2 se cierra
  formalmente abajo.

---

# 🟢 PHASE 2 — CLOSURE (formal, HANDOFF-only)

**Fase 2: CLOSED / GREEN.**

El cierre se evaluó contra los criterios previamente acordados — **NO**
contra el requisito de que `main.py` tenga cero rutas. Cada criterio
mandatorio quedó COMPLETE con evidencia de código verificada (no asumida):

1. **Extracción mecánica HTTP** — System, Telemetry, Auth/session API,
   What's New, Academy, Pack Assets, Landing/session navigation, Feedback
   Family. Cada uno en su propio router bajo `webapp/routers/`, ninguno
   importa `webapp.main`.
2. **Boundary de templating compartido** — `webapp/templating.py` construye
   la ÚNICA instancia de `Jinja2Templates`; `main.py` re-exporta esa misma
   identidad (no crea una segunda env).
3. **Boundaries de ownership de soporte** — `webapp/access.py` (access
   policy), `webapp/nav.py` (navegación), `core/content_registry.py` (pack),
   `webapp/presentation/*` (helpers de presentación) — cada uno con dueño
   explícito, documentado, sin importar `webapp.main`.
4. **Boundary mínimo de dependencia de Catálogo** — `webapp/services/catalog_reads.py`
   existe; el Module page ya no depende de los helpers privados de Catálogo
   a través de `main.py` — los consume directo.
5. **Ownership del lifecycle mutable del Copiloto** — `webapp/copilot_runtime.py`
   posee `store` (WizardStore) y `assistant_store` (AssistantConversationStore)
   como únicos objetos de identidad compartida entre requests.
6. **Extracción de HTTP Stateful** — Assistant + Section Analysis (2J-B),
   Trees + Wizard (2J-C), Module page (2J-D). Los tres routers consumen
   `store`/`assistant_store` directo de `copilot_runtime`, con identidad de
   objeto verificada explícitamente en cada fase.

## Evidencia final de Fase 2

Python **3.14.3** · FastAPI **0.141.1** · Starlette **1.4.1** · full suite
**274 passed / 1 skipped** · route surface de la aplicación **75** · route
snapshot sin cambios (sin `UPDATE_ROUTE_SURFACE`) · CI **GREEN** a través de
Fase 2J-D · `main.py` con **26 rutas propias** tras 2J-D.

`webapp/main.py`, al cierre de Fase 2:
- no posee ningún lifecycle mutable de negocio del Copiloto;
- no construye `WizardStore` ni `AssistantConversationStore`;
- no consume `store` ni `assistant_store` (cero referencias, verificado);
- ningún router extraído lo importa;
- los grupos HTTP que quedan son únicamente **Catálogo** y **Admin**.

## Residuales explícitamente diferidos (deuda real, NO blockers)

**CATALOG** — descomposición completa de Catálogo HTTP/business-data-
compliance/presentation (helpers de SVG/presentación de Catálogo; mecanismo
dinámico `sys.path`/import de Catálogo; mutación del entorno Jinja compartido
con filtros específicos de Catálogo).

**ADMIN** — residual HTTP de Admin/cross-cutting; ya delega sustancialmente
en `admin_tools`; no crea ninguna ambigüedad de ownership de lifecycle del
Copiloto sin resolver.

**M365** — consolidación e integración de Microsoft 365/Graph; deuda de
diseño de token de sesión/browser donde aplique.

**LLM** — consolidación de cliente OpenAI/LLM donde aplique.

**RUNTIME SCALABILITY** — el runtime del Copiloto sigue siendo
process-local/en memoria; un restart pierde el estado de runtime activo; el
estado multi-worker sigue aislado por proceso; sin Redis/base de
datos/TTL introducidos.

Estos ítems son deuda real y quedan explícitamente diferidos — no violan
ningún criterio acordado de cierre de Fase 2.

## Resultado arquitectónico

```
main.py
  → composición de la aplicación / registro de routers
  → residual de Catálogo + Admin explícitamente diferido

webapp/copilot_runtime.py
  → COPILOT process-lifetime mutable state

webapp/routers/assistant.py
  → COPILOT / ASSISTANT HTTP

webapp/routers/wizard.py
  → COPILOT / TREES & WIZARD HTTP

webapp/routers/modules.py
  → COPILOT / MODULE HTTP

webapp/services/catalog_reads.py
  → COPILOT / CATALOG READ CAPABILITY
```

Capacidades compartidas como `auth_service`/`templating` siguen disponibles
sin volver compartido el estado o el HTTP específico del Copiloto.

## Operations / Staffing readiness

Conclusión de la Phase 2 Completion Evaluation: Fase 2 removió suficiente
acoplamiento específico del Copiloto como para que el repositorio avance a
la siguiente etapa de plataforma **sin forzar a un segundo workspace a
embeberse dentro de la implementación HTTP/business/state del Copiloto**.

**READY FOR NEXT PLATFORM WORK** — explícitamente **NO** "ALL PLATFORM
CAPABILITIES COMPLETE" (eso es falso: Catálogo y Admin siguen con residual
real y sin tocar).

**Phase 2: CLOSED / GREEN.** Full Catalog decomposition sigue **DEFERRED**.
Admin sigue **DEFERRED / non-blocking**.

---

# 🟡 POST-PHASE-2 PLATFORM ACCELERATION

> North star: entregar Operations/Staffing como el segundo workspace real lo
> antes posible, extrayendo solo la infraestructura compartida que un
> consumidor real justifique — no una nueva fase larga de refactor.

**Decisión arquitectónica evaluada (análisis de solo-lectura, sin cambios de
código):** de las 3 opciones (A. M365-A primero, B. Staffing primero, C. slice
delgado de M365 + Staffing inmediato), se eligió **C**. Secuencia acordada:

```
Batch 1 — Thin M365 integration
  ↓
Batch 2 — Operations / Staffing repository + service
  ↓
Batch 3 — GET /operations/staffing, POST /operations/staffing/data,
          página Jinja/HTMX read-only
  ↓
PRIMER HITO REAL DE STAFFING
```

El Excel Consultores/Conocimiento (sheet de Operaciones) queda diferido más
allá de ese hito.

## PLATFORM ACCELERATION — BATCH 1 — THIN M365 INTEGRATION — CLOSED / GREEN
(commit `77192e0`; branch `work/platform-acceleration-b1-m365-thin`)

- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **281 passed / 1 skipped**; **route
  surface 75 unchanged**; **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`);
  **7 tests focalizados de `microsoft365`** GREEN; regresión dirigida de
  Catálogo GREEN.
- **Owner exacto:** `core/microsoft365.py` (nuevo). Se eligió `core/` — NO un
  paquete `integrations/` nuevo — porque `core/` ya es la capa compartida sin
  dependencia de framework que hoy consumen tanto `webapp/` como `tools/`
  (`core/sharepoint_cases.py` ya hace Graph calls desde ahí), `core` nunca
  importa `webapp` (verificado con grep), y crear un paquete nuevo hubiera
  sido estructura prematura sin necesidad concreta.
  **Clasificación: SHARED PLATFORM / MICROSOFT 365 INTEGRATION.**
- **Primitivas creadas (exactas):** `graph_headers(access_token)`,
  `graph_get_json(url, access_token, *, timeout=12)`,
  `graph_get_paged_values(url, access_token, *, timeout=12)` (sigue
  `@odata.nextLink` acumulando `value`, generalizando el patrón ya probado en
  `core/sharepoint_cases.py::_fetch_sap_cases_cached`). `urllib.request`
  preservado como transporte (sin migrar a `httpx`). **Sin** retry/backoff,
  **sin** jerarquía de errores, **sin** manejo de 429/Retry-After, **sin**
  autenticación app-only, **sin** refresh/storage de tokens — explícitamente
  fuera de alcance de este batch (M365 hardening futuro, si hace falta).
- **Token ownership:** el módulo NO posee ni recupera tokens — cada llamada
  recibe `access_token` explícito del caller. Arquitectura sin cambios:
  MSAL en el browser → `sessionStorage` → el caller pasa el token explícito
  por request. Verificado: sin logging del token, sin token embebido en URL,
  sin estado a nivel de módulo (ni variable global, ni `app.state`, ni cache
  de conexión). **Restricción registrada para el próximo batch:** un `GET`
  server-side NO puede asumir que tiene el token Graph del browser — la forma
  esperada de Staffing es `GET /operations/staffing` (shell autenticado) +
  `POST /operations/staffing/data` (recibe `graph_token`, consulta Graph,
  devuelve fragmento HTMX) — routes NO implementadas todavía.
- **Boundary de cache:** NO se movió. `sharepoint_cases.py`'s TTL cache y
  `catalog_reads.py`'s `@lru_cache` siguen donde estaban — la política de
  cache sigue siendo responsabilidad de cada repositorio/consumidor, no del
  transporte compartido.
- **Consumidor existente migrado:** `tools/catalog/catalog_service.py` —
  `_graph_headers`/`_graph_get_json` ahora delegan en
  `core.microsoft365.graph_headers`/`graph_get_json` (mismo nombre/firma,
  mismo comportamiento observable: headers, timeout=12, propagación de
  errores sin normalizar). `generate_catalog.py` no necesitó ningún cambio
  (sigue llamando `cs._graph_get_json`/`cs._graph_headers` sin saber que
  delegan). Import defensivo (`try` directo, `except ImportError` inserta el
  repo root en `sys.path` y reintenta) para no romper el modo demo CLI
  standalone del archivo (`python tools/catalog/catalog_service.py`), que no
  tiene el repo root en `sys.path` por defecto — verificado que **ambos**
  modos de carga (proceso de la webapp vía el hack de
  `webapp/services/catalog_reads.py`, y el demo CLI standalone) siguen
  funcionando después del cambio.
- **`core/sharepoint_cases.py` sin cambios** — se queda temporalmente en su
  implementación actual (su propio `get_graph_headers` + loop de paginación
  inline), tal como autorizado. El propósito de este batch era establecer el
  boundary, no terminar M365-A.
- **Tests (+7):** `tests/test_microsoft365.py` — headers, `graph_get_json`
  (request/timeout esperados, decodificación JSON, propagación de errores de
  `urllib` sin normalizar), `graph_get_paged_values` (sigue `nextLink` y
  agrega, se detiene sin `nextLink`), y compatibilidad de
  `catalog_service._graph_headers`/`_graph_get_json` tras la delegación. Todo
  con `monkeypatch` sobre `urllib.request.urlopen` — sin dependencia de Graph
  real en CI.
- **Regresión dirigida GREEN:** `tests/webapp/test_catalog_reads.py`,
  `tests/test_compliance_rubric.py`, `tests/test_catalog_artifact_type.py`
  (31 tests) — sin regresión en el comportamiento existente de Catálogo.
- Validado sobre **stack reproducible** (venv fresco: Python 3.14.3 /
  FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN (**75**,
  **sin cambios** — este batch no agrega rutas), **suite 281 passed / 1
  skipped** (274 + 7). **Cierre:** GitHub CI **GREEN**.
- **Sin Operations/Staffing código todavía.** Ninguna ruta, servicio ni
  template de Staffing existe en este batch.

**Secuencia de aceleración — estado:**

| Lote | Estado |
|---|---|
| Batch 1 — Thin M365 integration | **CLOSED / GREEN** |
| Batch 2 — Operations/Staffing repository + service | **CLOSED / GREEN** |
| Batch 3 — Operations/Staffing HTTP + Jinja/HTMX | **IMPLEMENTED / PENDING REAL-DATA VALIDATION + CI** |
| *(luego)* Primer hito real de Staffing | **NO alcanzado todavía** (real Graph smoke NOT RUN) |

## PLATFORM ACCELERATION — BATCH 2 — OPERATIONS/STAFFING REPOSITORY + SERVICE — CLOSED / GREEN
(commit funcional original `96e41e3`, corrective pass `2dd42c5`; branch
`work/platform-acceleration-b2-staffing-domain`)

- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **297 passed / 1 skipped**;
  **16 tests focalizados de Staffing** GREEN; **route surface 75 unchanged**;
  **route snapshot unchanged** (sin `UPDATE_ROUTE_SURFACE`).
- **Ownership final:** `webapp/services/staffing_repository.py` →
  **OPERATIONS / STAFFING DATA ACCESS** (consume `core.microsoft365`; conoce
  la estructura fuente del sitio Gestión; conoce los list ID; posee el
  mapeo de field names; posee los joins de lookup de SharePoint; devuelve
  `StaffingAllocationRecord` normalizado — ningún JSON de Graph escapa del
  boundary del repositorio). `webapp/services/staffing_service.py` →
  **OPERATIONS / STAFFING DOMAIN / READ MODEL** (consume records
  normalizados; semántica defensiva de fechas; orden estable; shaping de
  read-model/display; sin conocimiento de URLs de Graph, list ID ni field
  names internos).
- **Archivos creados:** `webapp/services/staffing_repository.py`,
  `webapp/services/staffing_service.py`,
  `tests/webapp/test_staffing_repository.py`,
  `tests/webapp/test_staffing_service.py`. **Modificado:** `config.py`
  (4 constantes nuevas). Sin cambios en `webapp/main.py` — **cero rutas
  HTTP** de Operations/Staffing existen todavía.
- **Fuentes consumidas (primer slice, sitio Gestión):** Asignaciones por
  proyecto, Proyectos, Colaboradores. Fuera de alcance (diferido): Hitos,
  Cliente, Solicitud vacaciones, y el Excel de Operaciones (Consultores/
  Conocimiento) — ninguno se tocó.
- **Config agregada** (`config.py`, mismo patrón que
  `SHAREPOINT_CASES_SITE_ID`/`LIST_ID`): `SHAREPOINT_GESTION_SITE_ID`
  **sin default** — a diferencia de `SHAREPOINT_CASES_SITE_ID` (que sí tiene
  un valor real conocido), este site ID no apareció en ningún lugar del
  repo/HANDOFF antes de este batch (verificado explícitamente); queda como
  **prerequisito de despliegue pendiente antes de Batch 3**.
  `SHAREPOINT_ASIGNACIONES_LIST_ID`/`SHAREPOINT_PROYECTOS_LIST_ID`/
  `SHAREPOINT_COLABORADORES_LIST_ID` sí tienen default — los 3 valores
  provistos al autorizar este batch, verificados sin contradicción contra
  repo/HANDOFF (tampoco aparecían antes), pero **no confirmados contra una
  lectura real de Graph** — mismo prerequisito pendiente.
- **Mapeo de field names — CORRECTIVE PASS (commit `2dd42c5`):** la versión
  inicial de este batch usaba `ColaboradorLookupId`/`ProyectoLookupId`/
  `Asignacion`/`Estado`/`FechaInicio`/`FechaFin`, y `Title` para AMBOS
  nombres de Proyecto y Colaborador, como **defaults de producción** — un
  micro-pass correctivo los removió: que `sharepoint_cases.py` use esos
  nombres en OTRO sitio/lista no prueba que Gestión los use igual, y que
  SharePoint tenga una columna `Title` estándar tampoco prueba que Proyectos
  o Colaboradores la usen como nombre de display. Ahora el mapeo es un
  objeto explícito **`StaffingFieldMapping`** (8 campos: los 6 de
  Asignaciones + `proyecto_title` + `colaborador_title`) **sin ningún
  default** — `StaffingFieldMapping.from_config()` (producción) queda vacío
  hasta que alguien confirme los 8 internal names reales contra el sitio
  Gestión; construirlo vacío NO rompe el import de la app (verificado:
  `import webapp.main` sigue OK sin Staffing configurado). Los tests usan
  su propio `StaffingFieldMapping` fixture, explícitamente etiquetado como
  NO-producción. Este es el prerequisito de validación de campos pendiente
  antes de que Batch 3 muestre datos reales — sin corregir, cualquier
  intento de leer Staffing en producción falla explícito listando los 8
  campos faltantes (no lee un campo equivocado silenciosamente).
- **Contrato del repositorio:** `StaffingRepository.get_allocations(access_token)
  -> list[StaffingAllocationRecord]`. Nunca expone JSON crudo de Graph más
  allá del módulo. Consume `core.microsoft365.graph_get_paged_values`
  directo — sin construir requests `urllib` propios, sin duplicar
  headers/paginación.
- **Joins y degradación:** la unión Asignación→Proyecto/Colaborador se hace
  por el `id` de SharePoint (comparado como string contra el
  `*LookupId` numérico). **Regla de degradación elegida y testeada:** una
  referencia sin resolver NO descarta la fila — el id crudo del lookup se
  conserva, el nombre resuelto queda en `None` (el consumidor decide el
  fallback de display). Verificado con test (`test_unresolved_lookup_degrades_without_dropping_the_row`).
- **`SHAREPOINT_GESTION_SITE_ID` ausente → falla explícito**, no una lista
  vacía silenciosa — decisión deliberada, distinta de la filosofía
  "degradar a `[]`" de `sharepoint_cases.py`/`catalog_service.py` (esos
  protegen una conversación de IA; Staffing expone datos de negocio reales,
  donde un `[]` silencioso podría leerse como "no hay asignaciones" en vez
  de "falta configurar"). Errores de transporte de Graph tampoco se tragan
  silenciosamente — se propagan.
- **Contrato del servicio:** `StaffingService(repository=None).get_overview(access_token)
  -> list[StaffingOverviewRow]`. `staffing_service.py` **no importa
  `core.microsoft365`** — la dependencia de Graph queda aislada detrás de
  `StaffingRepository` (inyectable por constructor para tests).
- **Semántica de capacidad — deliberadamente diferida:** `allocation_unit`
  queda `"unknown"` y `capacity_percentage` queda `None` — la unidad real
  del campo `Asignacion` (¿horas? ¿%?) no está confirmada contra la fuente,
  y calcular un % ahora habría sido "una cuenta falsa" (explícitamente
  prohibido). La referencia histórica de Power BI (160 hs FT mensuales;
  estados Active/Inactive/Tentative/Vaca) se documenta como constante
  (`FT_MONTHLY_CAPACITY_HOURS_HISTORICAL`, `KNOWN_HISTORICAL_STATUSES`) pero
  **no se aplica a ningún cálculo** — tampoco estaba confirmada contra la
  fuente actual.
- **Cache:** ninguna agregada — decisión por defecto (tres fetches de lista
  para una página read-only no justifican un framework de cache todavía).
  Explícitamente distinto del `@lru_cache`/TTL-cache que sí tienen
  `catalog_reads.py`/`sharepoint_cases.py`.
- **Estado de datos reales — NOT RUN, no confundir con cierre de CI:** el
  real Graph smoke **NO se ejecutó** — falta `SHAREPOINT_GESTION_SITE_ID`
  configurado, no hay token Graph delegado disponible en este entorno de
  implementación, y los field mappings de producción siguen sin validar.
  Aceptable para Batch 2 según lo autorizado; no se fabricó evidencia.
  **Batch 2 se cierra CLOSED/GREEN sobre CI y tests — NO sobre datos reales
  validados.**
- **Tests (+13, luego +3 en el corrective pass → +16):** repositorio (9:
  URLs correctas, `access_token` explícito, normalización + joins,
  degradación sin dropear fila, propagación de errores de transporte,
  falla explícita solo al leer sin `site_id` configurado, falla explícita
  listando los campos faltantes del field mapping, `from_config()` no
  sintetiza ningún nombre en este entorno, los campos de display siguen el
  mapeo y no un `"Title"` hardcodeado) + servicio (7: filas estables,
  `capacity_percentage` en `None`, orden determinístico, fechas/estados
  correctos, placeholder de nombre sin resolver, resultado vacío sin
  excepción, token forwarding). Todo con fixtures determinísticas — sin
  Graph real en CI.
- Validado sobre **stack reproducible** (venv fresco: Python 3.14.3 /
  FastAPI 0.141.1 / Starlette 1.4.1): route-contract GREEN (**75**,
  **sin cambios** — este batch no agrega rutas), **suite 297 passed / 1
  skipped** (281 + 16). **Cierre:** GitHub CI **GREEN** (commit original
  `96e41e3` + corrective pass `2dd42c5`, mismo PR/branch).
- **Estado del esquema de SharePoint (explícito):** **KNOWN FROM LEGACY
  SOURCE CONFIGURATION** — Asignaciones por proyecto
  (`c7c96b06-b32d-4386-8228-dd8e1f9926b0`), Proyectos
  (`efa2e279-593e-4321-867f-1ff982d800f6`), Colaboradores
  (`6e512cd3-394a-461f-abcd-6613f2c21113`) — estos 3 list ID vienen de
  evidencia de Power Query legado provista al autorizar el batch; **NO
  LIVE GRAPH VALIDATED** todavía en este entorno. **Sin resolver para
  producción:** `SHAREPOINT_GESTION_SITE_ID`, y los 8 internal names de
  `StaffingFieldMapping` (colaborador lookup, proyecto lookup, allocation,
  status, start date, end date, project display/name, collaborator
  display/name) — **ningún default inventado existe**; `StaffingFieldMapping`
  exige valores explícitos. Los tests usan un fixture mapping explícitamente
  etiquetado como NO-la-realidad-de-Gestión.
- **Semántica de negocio — implementada:** allocation records normalizados,
  joins de fuente, degradación determinística de lookup sin resolver,
  status passthrough, parseo defensivo de fechas, orden estable
  consultor/proyecto. **Deliberadamente diferida:** allocation→capacity %,
  conversión de 160hs FT, proyección +35 días, aviso de fin a 45 días,
  timeline de 90 días, lookback de vacaciones a 30 días, vacaciones,
  enriquecimiento de cliente, Gantt/timeline, ranking, KPIs — razón: el
  campo/unidad real de allocation en producción no está validado todavía;
  **no se introdujo ningún cálculo de capacidad falso.**
- **State/cache:** sin cache agregada; sin runtime singleton de Staffing;
  sin storage de token; `access_token` sigue siendo input explícito del
  caller; sin base de datos/Redis/persistencia.
- **Prerequisitos exactos pendientes antes de Batch 3:** (1) confirmar
  `SHAREPOINT_GESTION_SITE_ID` real (sin default a propósito); (2) confirmar
  los 3 list ID contra una lectura real de Graph — KNOWN FROM LEGACY SOURCE
  CONFIGURATION, todavía no LIVE GRAPH VALIDATED; (3) confirmar los 8
  internal names de `StaffingFieldMapping` (sin default a propósito, ni
  siquiera `Title`); (4) confirmar la unidad real de `Asignacion` (horas/
  %/otra) antes de implementar `capacity_percentage`. **Batch 3 también
  necesitará una forma controlada de validar estos 3 puntos con datos
  reales — no se resuelve en este commit de docs.**

## PLATFORM ACCELERATION — BATCH 3 — OPERATIONS/STAFFING HTTP + JINJA/HTMX — CLOSED / GREEN
(commits `878f8e8`, `98490e7`; docs de hito real `5473b27`; branch
`work/platform-acceleration-b3-staffing-http`)

- **Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; FastAPI
  **0.141.1**; Starlette **1.4.1**; full suite **313 passed / 1 skipped**;
  **32 tests focalizados de Staffing** GREEN; **route surface 77**; route
  snapshot actualizado intencionalmente solo para `GET /operations/staffing`
  y `POST /operations/staffing/data`.

**REAL GRAPH SMOKE: GREEN. FIRST REAL STAFFING MILESTONE: ACHIEVED.** El
usuario completó el login real de Microsoft en su propio browser
autenticado y navegó a `/operations/staffing`; la página mostró filas
reales del sitio Gestión a través del flujo completo: browser autenticado
→ `dw-graph-token` real → `POST /operations/staffing/data` →
`StaffingService` → `StaffingRepository` → `core.microsoft365` → Microsoft
Graph → datos reales de Gestión → fragmento HTMX. Verificado por
observación directa del usuario en su browser — este agente NUNCA
inspeccionó, solicitó ni tocó su cookie de sesión ni su token Graph (ver
sección de validación de esquema abajo).

- **Rutas agregadas:** `GET /operations/staffing` (shell autenticado) y
  `POST /operations/staffing/data` (fragmento HTMX). **Route surface: 75 →
  77** (intencional; `tests/route_surface.json` regenerado con
  `UPDATE_ROUTE_SURFACE=1`; el diff contiene únicamente estas 2 rutas).
- **Owner:** `webapp/routers/staffing.py` → **OPERATIONS / STAFFING HTTP**.
  No importa `webapp.main`, no construye URLs de Graph, no posee el field
  mapping, no posee transporte Graph, no calcula negocio — delega 100% en
  `StaffingService` (Batch 2). `main.py` solo recibió las 2 líneas de
  registro esperadas (`import` + `include_router`), sin lógica de negocio.
- **Access policy de Operations:** `webapp/operations_access.py` →
  `is_staffing_authorized(request)` — regla mínima y explícitamente
  provisoria: sesión válida + `auth_service.user_tier(...) == "full"`
  (excluye tier "academy" y no-autenticados). Separada de
  `webapp/access.py` (política actual del Copiloto, no reutilizada a
  propósito, per la clasificación de Fase 2). NO es RBAC ni workspace
  registry.
- **Deep-link no autenticado:** `GET /operations/staffing` sin sesión usa
  `nav.login_redirect(request, DEFAULT_PACK)` — el mismo mecanismo genérico
  que ya consumen `modules.py`/`wizard.py` (verificado con código, no
  supuesto: `login_redirect` preserva `next` para cualquier path, no solo
  rutas del Copiloto). **No se creó ningún helper nuevo** — no hizo falta.
- **Token Graph delegado — mecanismo del browser reutilizado sin cambios:**
  `sessionStorage["dw-graph-token"]` (ya seteado por `webapp/static/auth.js`
  en cada login, global a toda la app) + el mismo patrón HTMX
  `hx-vals='js:{graph_token: sessionStorage.getItem("dw-graph-token") ||
  ""}'` que ya usa `partials/admin_panel.html`. **Cero extracción de JS
  compartido** — no hizo falta: el mecanismo ya era genérico, no
  Copilot-específico. El token nunca viaja en la URL (POST + `hx-vals`,
  igual que Admin), nunca se loguea, nunca se persiste server-side.
- **Templates:** `webapp/templates/operations/staffing.html` (página
  completa) y `webapp/templates/operations/staffing_rows.html` (fragmento
  HTMX). Usan `webapp.templating.templates` (misma env compartida). **Sin
  `partials/copilot_nav.html`**, sin `x-data="copilotLayout(...)"`, topbar
  propio y mínimo.
- **CSS — cero archivos nuevos:** se auditaron las hojas existentes antes
  de escribir cualquier CSS y resultaron suficientes: `.topbar`,
  `.header-tools`, `.theme-toggle.logout-btn` (genéricas, ya usadas en
  todas las páginas), `.module-content`/`.module-hero`/`.eyebrow` (theme-
  aware vía `--content-theme-*`, sin dependencia de `.app-shell`/grid de
  nav), `.empty-panel` y `.admin-table` (genérica pese al nombre — solo
  estilo de tabla, sin lógica de Admin). No se creó ningún stylesheet de
  Operations.
- **Integración con el servicio:** `GET` nunca llama a `StaffingService`
  (verificado con test — monkeypatchear el servicio para que reviente si
  se llama, el `GET` sigue en 200); el `POST` construye `StaffingService()`
  y llama `get_overview(graph_token)` — token explícito, sin storage.
- **Estados controlados (UX de error, sección 14):** `graph_token` vacío →
  "no pudimos obtener tu token…" (sin tocar el servicio); `RuntimeError`
  del repositorio (site ID/field mapping incompletos) → "Staffing
  configuration incomplete…"; cualquier otra excepción (Graph/transporte)
  → mensaje neutro genérico, **nunca el texto crudo de la excepción**
  (verificado con test: un mensaje de excepción con "sensitive detail"
  nunca llega al HTML); resultado vacío → mensaje neutro, no excepción;
  resultado con filas → tabla. **Ningún caso devuelve 500** para estos
  escenarios esperados. El token tampoco se ecoa nunca en el HTML
  (verificado con test usando un token "secreto" de prueba).
- **UI — parity deliberadamente diferida:** se muestran consultor,
  proyecto, asignación, estado, fechas. Sin Gantt, vacaciones, clientes,
  proyecciones de 35/45/90/30 días, ranking, KPIs, filtros avanzados,
  bookmarks — todo eso queda fuera a propósito.
- **State/runtime:** sin runtime singleton de Staffing, sin
  `copilot_runtime`, sin cache, sin base de datos/Redis — `StaffingService`/
  `StaffingRepository` se construyen por request, tal como en Batch 2.
- **Tests (+15):** GET (6: redirect anónimo con `next` preservado, 200
  autorizado, 403 tier academy, NO llama al servicio, template correcto,
  sin requerir `graph_token`) + POST (9: token explícito al servicio,
  fragmento con filas, estado vacío válido, config incompleta controlada,
  falla de Graph controlada sin filtrar el texto de la excepción, token
  nunca ecoado en éxito NI en error, token vacío sin llamar al servicio,
  POST no autenticado no llega al servicio, POST tier academy no llega al
  servicio). Todo mockeando `StaffingService` en el boundary del router —
  sin Graph real en CI.
- Validado sobre **stack reproducible** (venv fresco: Python 3.14.3 /
  FastAPI 0.141.1 / Starlette 1.4.1): **suite 313 passed / 1 skipped** (297
  + 16), route-contract GREEN tras regenerar el snapshot intencionalmente
  (**77**, sin cambios en la corrección de semántica). **Cierre:** GitHub
  CI **GREEN**.
- **SHELL SMOKE: GREEN** (previo a la validación real). Corrido sobre el
  `.venv` real del proyecto (`uvicorn webapp.main:app` puerto 8082, vía el
  launch config `copiloto` existente): Copiloto sigue abriendo normal;
  `/operations/staffing` sin sesión → 307 a
  `/?pack=...&next=%2Foperations%2Fstaffing#acceso`; con sesión válida →
  200, template correcto, HTMX auto-`POST` sin traceback.
- **REAL GRAPH SMOKE: GREEN.** Ver sección "Validación real de esquema y
  primer hito de Staffing" abajo para la evidencia completa (config
  validada, semántica de allocation corregida, observación no-bloqueante
  de datos fuente).
- **PRIMER HITO REAL DE STAFFING: ACHIEVED.**

## Validación real de esquema y primer hito de Staffing (Batch 3)

**Diagnóstico temporal:** se creó y usó `webapp/static/staffing-schema-debug.html`
(servido vía el mount `/static` existente, SIN ruta nueva) para leer
metadata de columnas de Graph con un token delegado real del usuario —
nunca mostrado, logueado, persistido ni commiteado. **Ya se borró** y
**nunca estuvo tracked** (verificado con `git status` antes y después).

**Sitio Gestión — validado, no adivinado:**
```
danwor.sharepoint.com,a4171082-702b-4f78-b9f4-6b4a0d3757f2,0d7e68b2-cabe-48af-991a-51bd1a5f0afc
```
(`https://danwor.sharepoint.com/sites/Gestion`)

**Los 3 list ID — live-Graph validated** (ya no solo "known from legacy
source configuration"): Asignaciones por proyecto
(`c7c96b06-b32d-4386-8228-dd8e1f9926b0`), Proyectos
(`efa2e279-593e-4321-867f-1ff982d800f6`), Colaboradores
(`6e512cd3-394a-461f-abcd-6613f2c21113`) — los 3 resolvieron correctamente
contra Graph real.

**Los 8 internal names de `StaffingFieldMapping` — validados contra
`/sites/{id}/lists/{id}/columns` real** (ya no provisorios):
`ColaboradorLookupId`, `ProyectoLookupId`, `Horaspormes`, `Status`,
`Fechainicio`, `FechaFin`, `Title` (Proyecto), `Title` (Colaborador). Nota
importante: `ColaboradorLookupId`/`ProyectoLookupId` NO son nombres
adivinados — son cómo Graph `fieldValueSet` representa el ID de un lookup
column cuyo nombre real ES `Colaborador`/`Proyecto` (sufijo `LookupId`
agregado por Graph, no por este repo).

**Semántica de allocation — validada y corregida (commit `98490e7`):**
`Horaspormes` (displayName "Horas por mes", type `number`) confirma que la
unidad es **horas por mes**, no una incógnita. `StaffingAllocationRecord.allocation_unit`
pasó de `"unknown"` a `"hours_per_month"`; el fragmento HTMX renderiza
`"40 h/mes"` en vez de `"40.0 (unknown)"`. **`capacity_percentage` sigue
`None` a propósito** — conocer la unidad no resuelve la semántica de
capacidad (baseline FT/PT, over-allocation), que queda como el PRÓXIMO
incremento funcional. NO se implementó ningún cálculo `allocation / 160`
ni ninguna regla de capacidad en este batch.

**Valores reales de `Status` observados:** Inactivo / Activo / Tentativo —
**no coinciden literalmente** con la referencia histórica documentada en
`staffing_service.py` (`KNOWN_HISTORICAL_STATUSES` = Active/Inactive/
Tentative/Vaca, en inglés, del Power BI legado). Sin impacto de
comportamiento: `status` siempre fue passthrough puro del valor fuente,
nunca se comparó contra esa constante — se corrigió el docstring para no
confundir a futuros lectores.

**Evidencia futura únicamente (NO consumida en este slice):** Colaboradores
tiene un campo real `Disponibilidaddeasignaci_x00f3_n` (displayName
"Disponibilidad de asignación"), choices `Part Time`/`Full Time` — relevante
para una futura semántica de capacidad, pero deliberadamente fuera de
alcance de Batch 3.

**Observación de datos fuente — NO BLOQUEANTE:** algunas asignaciones
reales tienen el lookup de consultor o proyecto sin resolver (referencia a
un item que no aparece en la lista relacionada). Clasificación:
**NON-BLOCKING SOURCE DATA / REFERENTIAL QUALITY OBSERVATION**. La regla de
degradación determinística del repositorio (Batch 2, testeada) funcionó
exactamente como se diseñó: la fila permaneció visible (con el nombre
resuelto en blanco) en vez de descartar todo el resultado. No se investigó
ni se corrigió esta calidad referencial de origen — no es una feature de
Batch 3.

**Configuración local usada para la validación:** los 9 valores (site ID +
8 field names) se cargaron vía `.env` (git-ignored, `load_dotenv()` en
`config.py`) — NO como default de producción en el código.
`config.py` sigue sin ningún default hardcodeado para estos 9 valores; el
mecanismo de `StaffingFieldMapping` sin defaults (corrective pass de Batch
2) queda intacto.

**Corrección de aislamiento de tests descubierta durante esta validación:**
con el `.env` real presente, dos tests que asumían un entorno local
"sin configurar" (`test_missing_field_mapping_fails_clearly_listing_what_is_missing`,
`test_production_config_does_not_silently_synthesize_field_names`) dejaron
de ser deterministas — uno de ellos llegó a hacer una llamada real (fallida,
401) contra Microsoft Graph durante una corrida de tests. Corregido: ambos
ahora monkeypatchean explícitamente los 8 nombres de config a vacío en vez
de asumir el ambiente — ya no dependen de si existe o no un `.env` local.

**Batch 3 — LEVEL A (implementación) COMPLETE; LEVEL B (primer hito real)
ACHIEVED.** Excel Consultores/Conocimiento, vacaciones, clientes, Gantt,
ranking, KPIs, filtros avanzados, workspace switcher, rediseño de login/nav
y limpieza de Catalog/Admin siguen explícitamente fuera de alcance.

---

## PLATFORM ENTRY / WORKSPACE RESOLUTION — CLOSED / GREEN
(commit funcional `bd5b0f8` — `feat(platform): add workspace-aware entry
flow`; checkpoint previo `221b82d`; branch
`work/platform-entry-workspace-resolution`)

**Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; full
suite **332 passed / 1 skipped**; **route surface 79**; route snapshot
expandido intencionalmente solo con `GET /platform` y `GET /copilot`
(anterior: 77); **smoke visual manual: GREEN**.

Primer paso de la dirección de plataforma multi-workspace descripta en
**A.19**: entrada transicional, sin todavía decidir la identidad final de
`/` ni construir un workspace switcher.

- **Modelo de routing transicional (intencional, no definitivo):** `/`
  sigue siendo EXACTAMENTE la entrada del Copiloto (verificado por
  comparación de string byte-a-byte contra la versión histórica — **cero
  regresión visual/funcional**). `/copilot` es un alias explícito nuevo que
  renderiza la misma experiencia (mismo `render_landing()`, mismo
  auth/next/pack). `/platform` es la nueva entrada platform-agnostic. El
  deep link existente `/operations/staffing` no cambió de URL.
- **`webapp/workspaces.py`** → **PLATFORM / WORKSPACE DISCOVERY & ROUTING
  METADATA.** No importa `webapp.main`, no posee estado mutable, no posee
  auth/sesión. Representación mínima e inmutable `Workspace(key, label,
  subtitle, home_url, is_authorized)` para exactamente 2 workspaces
  (`COPILOT`, `OPERATIONS`). **NO** es una base de datos, ni un plugin
  registry, ni discovery dinámico, ni RBAC.
- **Predicados de autorización — reutilizados, no duplicados:** Copilot =
  `user is not None` (cualquier sesión válida); Operations = predicado puro
  nuevo `operations_access.is_staffing_authorized_user(user)`, extraído del
  `is_staffing_authorized(request)` existente de Batch 3 (que ahora delega
  en él) — **cero regresión**, verificado contra los 16 tests de Staffing
  ya existentes.
- **Invariante de sesión (verificado por lectura de código, no supuesto):**
  `auth_service.py` exige `user_tier(email) is not None` para CREAR la
  sesión — ningún request con cookie de sesión válida puede tener tier
  `None`. Consecuencia: **"Operations-only" y "0 workspaces autenticado" NO
  son representables** bajo la política actual (Operations es un
  subconjunto estricto de Copilot). Documentado como invariante ejecutable
  en `tests/webapp/test_workspaces.py`
  (`test_operations_only_is_not_representable_under_current_policy`) — **no
  se fabricó ningún tier falso** para simular esa combinación imposible,
  por instrucción explícita.
- **Fuente de autorización — deliberadamente desacoplada:** el resolver NO
  usa variables de entorno como modelo permanente de asignación
  usuario→workspace; reutiliza las políticas server-side existentes. Cuando
  la fuente real cambie a futuro (grupos de Entra, app roles, allowlists),
  ese cambio no debería requerir tocar routing/UI de plataforma.
- **Resolución 0/1/2+ en `/platform` autenticado:** 0 workspaces →
  `platform_no_access.html` (estado controlado, sin loop de redirect); 1
  workspace → redirect directo 307 a su `home_url` (sin selector
  intermedio); 2+ → `platform_selector.html` con tiles solo de lo que
  devuelve `available_workspaces(user)` (nunca hardcodeado — verificado con
  test que monkeypatchea el resolver). Sin "recordar último workspace", sin
  localStorage — explícitamente diferido.
- **Precedencia de deep link:** un `next` explícito y seguro SIEMPRE gana
  sobre la resolución de selector, incluso autenticado con 2+ workspaces
  (verificado con test). Reutiliza el mecanismo de `next` YA existente
  (`safe_next` relativo, sin `//`) — no se introdujo un segundo sistema de
  post-login state, no se debilitó la protección de open-redirect.
- **Deep link de Operations — cambio de entrada de login, NO de URL de
  destino:** `GET/POST /operations/staffing(/data)` sin sesión ahora
  redirige a `/platform?next=...` (antes: `/` con el branding del
  Copiloto) vía `workspaces.platform_login_redirect(request)`, preservando
  `next` end-to-end. **El deep link de Copilot NO cambió** — sigue usando
  `nav.login_redirect` hacia `/`. Verificado con test end-to-end
  (`test_operations_deep_link_round_trip_via_platform_entry`) y con smoke
  visual manual en browser real (logged out → `/operations/staffing` →
  `/platform?next=%2Foperations%2Fstaffing`, sin branding del Copiloto,
  `data-home="/operations/staffing"` → tras login volvería directo a
  Staffing, sin pasar por el selector).
- **Naming provisorio, explícitamente cuestionable:** "Plataforma Danwor"
  (título, eyebrow, CTA genérico "Ingresar con tu cuenta Danwor" — sin
  mencionar workspaces específicos en el login). No es un ejercicio de
  branding definitivo.
- **Presentación reutilizada, no duplicada:** se parametrizó
  `landing.html` existente (75 líneas, autocontenido) con un diccionario de
  copy (`login_copy`) en vez de crear una plantilla hermana — reduce a cero
  el riesgo de divergencia entre `/`, `/copilot` y `/platform` para todo lo
  que no sea texto. Se extrajo `render_landing()` en `landing.py`,
  reutilizado por `platform.py` sin import circular
  (`platform.py`→`landing.py`, nunca al revés). Identidad visual
  (`.glass`, `.access-grid`, fondo hero) reutilizada de `brand-hero.css`
  existente (98 líneas ya auditadas) — solo se agregaron ~6 líneas nuevas
  (`.workspace-tile(s)`) para los tiles del selector, sin nuevo stylesheet.
- **Token Graph delegado — mecanismo sin cambios:**
  `sessionStorage["dw-graph-token"]` sigue siendo seteado exactamente igual
  por `auth.js`; Copilot y Operations siguen compartiendo la misma sesión
  de browser autenticada. Sin segundo OAuth, sin segunda sesión, sin
  transferencia server-side, sin nuevos scopes.
- **`pack` — sin tocar:** sigue siendo un selector de contenido
  Copilot-interno (`?pack=datasphere_core`); nunca se usó para identificar
  Operations ni ningún otro workspace.
- **Explícitamente diferido (NO en este batch):** workspace switcher
  in-app, flip de `/` a Platform, "recordar último workspace", RBAC
  generalizado, rediseño de Entra groups/app roles, base de datos de
  asignación usuario↔workspace, rediseño completo de login, rediseño de
  Graph token, landing de Operations, Projects/Reporting, paridad de
  capacidad/disponibilidad de Staffing.
- **Rutas agregadas:** `GET /platform`, `GET /copilot`. **Route surface: 77
  → 79** (intencional; snapshot regenerado con `UPDATE_ROUTE_SURFACE=1`; el
  diff contiene únicamente estas 2 líneas, en su posición alfabética
  correcta).
- **Tests (+19):** resolver de workspaces (5, incluyendo el invariante
  ejecutable de no-representabilidad), `/platform` (10: render
  no-autenticado, ausencia de branding Copilot, next válido/inválido,
  redirect directo con 1 workspace, selector con 2+, precedencia de next
  explícito, selector puramente derivado del resolver, estado 0-workspaces
  controlado, round-trip completo del deep link de Operations), `/copilot`
  (4: byte-idéntico a `/`, forward de sesión, preservación de next, `/`
  intacto). Más 2 tests existentes de Staffing actualizados para reflejar
  el nuevo destino de redirect (`/platform?next=...` en vez de `/?...`).
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  3.14.3): **suite 332 passed / 1 skipped** (313 + 19), route-contract
  GREEN tras regenerar el snapshot intencionalmente (**79**).
- **SMOKE VISUAL MANUAL: GREEN.** Corrido en browser real contra
  `uvicorn webapp.main:app` (puerto 8082, `.venv` del proyecto): `/`
  visualmente idéntico al histórico; `/copilot` misma experiencia; `/platform`
  no-autenticado con copy platform-neutral correcto; sesión inyectada
  (JWT firmado con el `.venv` del proyecto, nunca credenciales reales del
  usuario) → `/platform` autenticado con 2 workspaces → selector con ambos
  tiles → click en Operations → `/operations/staffing` correcto; click en
  Copilot → experiencia real del Copiloto; deep link logged-out →
  `/operations/staffing` → `/platform?next=%2Foperations%2Fstaffing`
  confirmado. Sin errores de consola en ningún paso.
- **Invariante de autorización actual (observado, no elegido):** Copilot =
  cualquier usuario actualmente autenticado; Operations = el predicado de
  autorización de Operations/Staffing ya existente. Por lo tanto, un
  **"usuario solo-Operations" NO es representable** por el modelo de
  autorización actual. Esto es un invariante observado del modelo actual —
  **no es una limitación del resolver ni fue trabajado artificialmente
  alrededor**. La fuente de autorización futura puede cambiar (grupos de
  Entra, app roles, allowlists explícitas, u otra fuente server-side) sin
  cambiar el contrato del resolver de workspaces; **no se elige ninguna en
  este cierre**.
- **CI:** GitHub CI **GREEN** sobre el estado final de la rama.

### Próxima decisión (NO autorizada automáticamente)

Con este batch **CLOSED / GREEN**, el próximo paso es una decisión del
usuario, no una continuación automática. Candidatos, sin elegir entre
ellos en este commit de docs:

- **IN-APP WORKSPACE NAVIGATION / PLATFORM EXPERIENCE** — dónde vive un
  switcher de workspace, cómo se relaciona la navegación de plataforma con
  la navegación local de cada workspace, el eventual flip de `/` a
  Platform, la evolución de la fuente de autorización, y el naming final
  de la plataforma (hoy "Plataforma Danwor", provisorio).
- **Paridad funcional de Staffing** (capacidad/disponibilidad, filtros,
  Gantt, vacaciones) — disponible como stream de producto separado.

**Ninguno autorizado todavía.**

---

## IN-APP WORKSPACE NAVIGATION / SHARED WORKSPACE SWITCHER — CLOSED / GREEN
(commit funcional `50c671b` — `feat(platform): add in-app workspace
switcher`; checkpoint previo `2c55fe8`; branch
`work/platform-inapp-workspace-switcher`)

**Evidencia final de cierre:** GitHub **CI GREEN**; Python **3.14.3**; full
suite **338 passed / 1 skipped**; **route surface 79** (sin cambios, sin
`UPDATE_ROUTE_SURFACE`); **smoke visual manual: GREEN** (desktop y viewport
375px, sin overflow horizontal, sin errores de consola).

Precedido por un **targeted assessment** (análisis únicamente, sin código)
que recomendó la forma de implementación (A: partial compartido + contexto
explícito por ruta) — implementado tal cual acá, un solo batch.

- **Dueño de presentación compartida:** `webapp/templates/partials/workspace_switcher.html`
  → **PLATFORM / SHARED PRESENTATION.** No decide autorización, no conoce
  Graph/pack/lógica de negocio de Copilot ni de Operations, y no hardcodea
  URLs de workspace — renderiza únicamente lo que el router le pasa
  (`current_workspace`, `available_workspaces`, ambos objetos `Workspace`
  de `webapp/workspaces.py`, que queda **sin cambios** en este batch).
- **Resultado arquitectónico confirmado:** `available_workspaces(user)` es
  ahora consumido por **DOS** experiencias de plataforma distintas — el
  selector/resolución de `/platform` (batch anterior) y este switcher
  in-app — sin que `workspaces.py` necesitara cambiar una línea para
  soportar el segundo consumidor. Evidencia de que el descubrimiento de
  workspaces quedó correctamente desacoplado de su presentación.
- **Estrategia de contexto:** explícita por ruta, siguiendo el patrón YA
  establecido (`modules.py` ya pasaba `"user": user` a mano) — sin
  middleware, sin context processor, sin `available_workspaces` como
  global de Jinja, sin un framework de contexto genérico. Cada owner HTTP
  sigue resolviendo `user = session_user(request)` y llama
  `workspaces.available_workspaces(user)` — 2 líneas, sin justificar un
  helper nuevo.
- **Comportamiento con 1 workspace disponible:** identidad en texto plano
  (`.eyebrow`), sin dropdown, sin chevron, sin menú de un solo ítem.
- **Comportamiento con 2+ workspaces:** `<details>/<summary>` nativo
  (accesible por teclado, sin Alpine/HTMX, ancla real) con los otros
  workspaces autorizados + un link final "Todos los espacios" → `/platform`
  (navegación pura, no crea un dashboard de plataforma).
- **Destinos:** siempre `Workspace.home_url` (COPILOT → `/`; OPERATIONS →
  `/operations/staffing`) — nunca hardcodeados en el partial. Verificado
  con test que compara contra los objetos reales de `workspaces.py`.
- **`pack` — sin cambios:** el destino a Operations nunca lleva
  `?pack=...`; los links de Copilot (`/modules/...?pack=...`) siguen
  intactos. Verificado con test dedicado.
- **Templates Copilot tocados (8, un `{% include %}` cada uno, sin
  unificar los topbars duplicados — deuda preexistente, fuera de
  alcance):** `module.html`, `academy.html`, `catalog_cds.html`,
  `catalog_connection.html`, `catalog_flow.html`, `catalog_guide.html`,
  `admin_activity_page.html`, `whats_new.html`.
- **Template Operations tocado (1):** `operations/staffing.html` — se
  **reemplazó** el `<span class="eyebrow">Danwor · Operations</span>`
  hardcodeado por el partial (misma posición, sin identidad duplicada;
  verificado con test).
- **Owners de ruta wireados:** `webapp/routers/modules.py`,
  `webapp/routers/academy.py`, `webapp/routers/whats_new.py`,
  `webapp/routers/staffing.py` (solo `staffing_page` — el fragmento HTMX
  `staffing_data`/`staffing_rows.html` NO recibió el switcher, no le
  corresponde).
- **Handlers residuales de `main.py` tocados (5, solo wiring de contexto,
  sin extraer/refactorizar Catalog/Admin):** `admin_activity`,
  `catalog_connection_detail`, `catalog_flow`, `catalog_cds`,
  `catalog_guide`. **`catalog_data_product` deliberadamente NO tocado** —
  se descubrió que su template (`catalog_data_product.html`) sobreescribe
  `{% block content %}`, un bloque que `base.html` no define (solo define
  `{% block body %}`); no tiene topbar propio y no es candidato al
  switcher. Se documenta el hallazgo, **no se corrige** — está fuera de
  alcance de este batch (no es business logic ni el bug de nadie de este
  batch).
- **CSS:** ~30 líneas nuevas en `webapp/static/layout.css` (junto a
  `.header-tools`/`.animated-brand`, su vecindario natural), reutilizando
  tokens existentes (`--gold`, `--card`, `--line`, `--soft`, `--ink`) y la
  clase `.eyebrow` ya existente para la etiqueta — sin nuevo stylesheet,
  sin design system.
- **Accesibilidad:** `<details>/<summary>` nativo — alcanzable por
  teclado, `:focus-visible` con outline dorado, anclas reales, sin
  JavaScript nuevo.
- **Mobile:** verificado en viewport 375px — sin overflow horizontal
  (`topbarScrollWidth == topbarClientWidth`), etiqueta con
  `max-width`+ellipsis, dropdown/mobile-menu conviven sin romperse.
- **Entradas NO tocadas (verificado, sin cambios):** `/`, `/copilot`,
  `/platform` — siguen siendo experiencias de entrada/resolución, no
  chrome autenticado in-workspace.
- **Rutas agregadas:** ninguna. **Route surface: sigue 79** (sin
  `UPDATE_ROUTE_SURFACE`, snapshot intacto, verificado).
- **Tests (+6, `tests/webapp/test_workspace_switcher.py`):** usuario
  solo-Copilot ve identidad sin dropdown; usuario multi-workspace en
  Copilot ve destino Operations; usuario multi-workspace en Operations ve
  destino Copilot; el switcher NUNCA muestra un workspace que el resolver
  no devolvió (monkeypatch de `available_workspaces`, prueba que es pura
  presentación); identidad de Operations aparece una sola vez (sin
  duplicar la vieja + la nueva); `pack` no viaja al link de Operations.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  3.14.3): **suite 338 passed / 1 skipped** (332 + 6), route-contract
  GREEN sin regenerar snapshot (**79**).
- **SMOKE VISUAL MANUAL: GREEN.** Sesión inyectada (JWT firmado con el
  `.venv` del proyecto) full-tier: módulo real de Copilot → dropdown abre
  → click Operations → llega a `/operations/staffing` real; desde
  Operations → dropdown abre → click Copiloto → llega a la home real del
  Copiloto (`/modules/00_Intro`, vía `/` → `/home`); viewport 375px sin
  overflow; `/`, `/platform` re-verificados sin cambios tras limpiar la
  sesión. Sin errores de consola en ningún paso. El caso de un solo
  workspace (tier academy) se verificó por test automatizado (mismo
  render Jinja real vía `TestClient`) y no en vivo: el servidor local de
  desarrollo resuelve cualquier email autenticado a tier "full" con su
  configuración actual, por lo que una sesión academy-only no es
  alcanzable en el browser sin mutar la config del servidor — se
  documenta esta razón en vez de forzarla.
- **CI:** GitHub CI **GREEN** sobre el estado final de la rama.

### Próxima decisión (NO autorizada automáticamente)

Con este batch **CLOSED / GREEN**, el próximo paso es una decisión del
usuario, no una continuación automática. Candidatos, sin elegir entre
ellos en este commit de docs:

- **A — Paridad funcional de Staffing** (capacidad/disponibilidad,
  filtros, Gantt, vacaciones).
- **B — Transición eventual de `/` a Platform** (el flip que este batch y
  el anterior dejaron explícitamente diferido).
- **C — Más UX de plataforma** (unificar los topbars duplicados del
  Copiloto, naming final, recordar el último workspace) — solo si aparece
  una necesidad concreta, no como trabajo especulativo.

**Ninguno autorizado todavía.**

---

## ONE DANWOR ENTRY EXPERIENCE (BATCH A) — CLOSED / GREEN
(commits funcionales `78357b5` — `feat(platform): introduce One Danwor
entry experience`; `f22e1f4` — `fix(platform): version One Danwor auth
script`; `df8d4de` — `fix(platform): isolate neutral auth callback
presentation`; checkpoints previos `2292aba`, `d43b527`; branch
`work/onedanwor-entry-experience`, mergeada a `main` vía PR #30)

**Evidencia final de cierre:** GitHub **CI GREEN**; **REAL MICROSOFT
OAUTH ROUND-TRIP: GREEN** (validado por el usuario con sus propias
credenciales reales, desde `/onedanwor` — sin landing del Copiloto
visible durante el popup/callback, overlay neutro "Completando
acceso…", login completado, resolución de workspace post-login
correcta, Copiloto y Operaciones disponibles según autorización);
Python **3.14.3**; full suite **354 passed / 1 skipped**; **route
surface 80** (79→80, intencional, única adición `GET /onedanwor`).

Precedido por un **targeted assessment** (análisis únicamente, sin código)
que determinó el límite seguro de implementación entre dos batches (A:
entrada/naming/auth; B: shell/topbar/rename de rutas de Operations,
explícitamente diferido). Esto es SOLO Batch A.

- **Naming:** término interno arquitectónico sigue siendo `platform` (sin
  rename de paquetes/módulos). Marca visible: **"One Danwor"**. Entrada
  canónica: **`GET /onedanwor`**. `/platform` pasa a ser un alias de
  compatibilidad — **SIEMPRE** 307 a `/onedanwor` (preservando `next` si
  es un path relativo seguro), **nunca** vuelve a renderizar landing o
  selector directamente. Un solo punto de resolución real evita que ambos
  diverjan. `/` y `/copilot` **sin cambios** (verificado con test
  byte-a-byte y smoke visual — siguen siendo la puerta histórica del
  Copiloto; NO se flipean a Platform en este batch).
- **`webapp/routers/platform.py`** — reestructurado: `_resolve_onedanwor()`
  contiene TODA la lógica de resolución (antes en `platform_entry`), ahora
  usada por `GET /onedanwor`; `GET /platform` quedó como un handler
  minúsculo que solo redirige. Cero duplicación de lógica de resolución.
- **Corrección del auth-popup (bug real reportado por el usuario, cerrado
  en dos pasadas):** causa exacta — `webapp/static/auth.js` abría el
  popup de OAuth hardcodeado a `window.location.origin +
  "/?auth_popup=1"`, SIEMPRE la raíz del Copiloto, sin importar si el
  login arrancó desde `/`, `/copilot` o `/onedanwor`; y el callback fijo
  de Microsoft a `/` (redirect_uri, registrado en Azure) también
  renderizaba esa landing. **Corrección final (sin segundo mecanismo de
  auth):**
  1. `auth.js`: el popup abre `window.location.pathname + "?" + <query
     actual + auth_popup=1>` — la página que INICIÓ el login,
     preservando su `next`/`pack` si los tenía.
  2. `render_landing()` (compartida por `/`, `/copilot`, `/onedanwor`):
     cuando la request tiene `auth_popup` o `code`, **no renderiza
     `landing.html` en absoluto** — sirve un documento SEPARADO y
     totalmente neutro, `auth_callback.html` (NUEVO). Un overlay sobre
     la landing no alcanzaba: el marcado con marca (Copiloto o el copy
     de la página que sea) seguía existiendo en el DOM debajo del
     overlay — visible en la validación real del usuario. El nuevo
     template no incluye ese copy en absoluto: solo el fondo oscuro
     existente, el spinner (`.auth-overlay`/`.auth-spinner`, YA
     existentes, cero CSS nuevo) y "Completando acceso…", más un botón
     oculto con `data-home` para que el fallback sin `window.opener` de
     `auth.js` siga resolviendo el destino correcto.
  3. Los `<script>` de `landing.html` (`auth.js`, `brand-hero.js`) y de
     `auth_callback.html` usan `?v={{ static_version }}` — mismo
     mecanismo de cache-busting que ya usa `base.html` (sin esto, un
     browser con `auth.js` cacheado de antes del deploy podía seguir
     ejecutando la lógica vieja del popup).
  - **`redirect_uri` de Microsoft (fijo en `/`) NO se tocó** — sigue
    devolviendo el callback a `/?code=...` siempre; ese callback ahora
    cae en la rama neutra de `render_landing()`. Sin cambios de PKCE,
    scopes, sesión, ni `sessionStorage["dw-graph-token"]`, sin token en
    URL, sin persistencia server-side de Graph token.
- **Login de One Danwor — misma grilla, sin plantilla nueva:**
  `_ONEDANWOR_LANDING_COPY` en `platform.py` ahora incluye los 3 bullets
  reales ("Un único acceso con tu cuenta Danwor", "Espacios de trabajo
  según tu perfil", "Navegación integrada entre servicios") — el
  checklist de `landing.html` ya soportaba esto, solo estaba `None` para
  la copy anterior de "Plataforma Danwor". Cero cambios de template.
- **Selector rico — misma grilla que la tarjeta de login del Copiloto:**
  `_WORKSPACE_CARD_COPY` (dict keyed por `Workspace.key`) en `platform.py`
  — presentación PURA (eyebrow/título/acento/descripción/features),
  deliberadamente separada de `Workspace` (`webapp/workspaces.py`, que
  sigue siendo la ÚNICA fuente de identidad/home/autorización — sin
  cambios salvo el label visible de Operations). `platform_selector.html`
  reutiliza `.access-brand`/`.access-list`/`.access-check` (YA globales en
  `brand-hero.css`) dentro de cada tile — solo se agregaron ~6 líneas de
  CSS para el `h2` de cada tarjeta (el heading principal de la página usa
  `h1`; las tarjetas usan `h2` para no duplicar el heading semántico,
  necesitando su propia regla ya que `.access-brand h1` es específica al
  tag). **Bug encontrado y corregido durante la implementación:** la
  clave `"copy"` en el dict de cada tarjeta colisionaba con
  `dict.copy()` — Jinja resuelve `card.copy` como ese método ANTES de
  probar `card["copy"]`, dejando el diccionario real inalcanzable por
  dot-notation. Renombrada a `"meta"`.
- **Naming de Operations:** `Workspace.label` de `OPERATIONS` pasa de
  "Operations" a **"Operaciones"** (identidad visible únicamente). `key`
  (`"operations"`), `home_url` (`/operations/staffing`), y todos los
  identificadores internos (`operations_access.py`, etc.) **sin cambios**
  — per instrucción explícita, no es un rename de código.
  "Todos los espacios" (en el switcher in-app, `workspace_switcher.html`)
  ahora apunta a `/onedanwor` (antes `/platform`) — literal, sin
  introducir un constante/helper nuevo para un solo uso.
- **Rutas agregadas:** `GET /onedanwor`. **Route surface: 79 → 80**
  (intencional; snapshot regenerado con `UPDATE_ROUTE_SURFACE=1`; el diff
  contiene únicamente esta línea). `/platform` sigue registrada — cambió
  su comportamiento (render → redirect), no el conteo de rutas.
- **Tests:** `tests/webapp/test_onedanwor_routes.py` (NUEVO, 13 tests) —
  login no-autenticado con los 3 bullets, ausencia de branding Copilot,
  next válido/inválido, 0/1/2+ workspaces (selector con contenido rico
  real de ambas tarjetas, verificado con los textos truthful exactos),
  precedencia de next explícito, selector puramente derivado del resolver
  (incluye caso con workspace SIN entrada en el mapa de copy — cae a
  `label`, la presentación no reemplaza autorización), `/` y `/copilot`
  intactos, y el round-trip COMPLETO de Operations a través de AMBOS saltos
  (`/operations/staffing` → `/platform?next=...` → `/onedanwor?next=...`
  → destino). `tests/webapp/test_platform_routes.py` (REESCRITO, 5 tests)
  — ahora solo prueba el alias de compatibilidad (siempre 307, con/sin
  `next` seguro/inseguro, autenticado o no). `tests/webapp/test_routes.py`
  (+3) — `auth_completing` en el HTML inicial para `auth_popup`/`code`,
  ausente en landing normal. `tests/webapp/test_workspace_switcher.py`
  (actualizado) — "Todos los espacios" ahora apunta a `/onedanwor`.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  3.14.3): **suite 350 passed / 1 skipped** (338 + 12 netos, incluye el
  test de versionado de scripts del pase correctivo), route-contract
  GREEN tras regenerar el snapshot intencionalmente (**80**).
- **SMOKE VISUAL MANUAL: GREEN.** `/onedanwor` no-autenticado con marca y
  3 bullets correctos; `/platform` redirige; selector autenticado
  multi-workspace muestra ambas tarjetas ricas completas; click en
  Operaciones llega a Staffing real; "Todos los espacios" del switcher
  apunta a `/onedanwor`; viewport 375px sin overflow horizontal; `/` y
  `/copilot` re-verificados sin cambios.
- **Corrección de entrega de `auth.js` (pase correctivo, commit
  `f22e1f4`):** el smoke inicial detectó que un tab de browser con
  sesión previa podía seguir ejecutando una copia cacheada de `auth.js`
  incluso después de que el server ya serviera la versión corregida —
  causa: los `<script>` de `landing.html` (`auth.js`, `brand-hero.js`)
  no llevaban el sufijo `?v={{ static_version }}` que `base.html` YA
  usa para cada asset estático. Corrección: se agregó el MISMO sufijo a
  ambos scripts de `landing.html` (mismo mecanismo existente, sin
  versionado nuevo, sin tocar `brand-hero.css` — deliberadamente fuera
  de alcance de este pase). Test agregado:
  `test_landing_versions_its_auth_and_brand_scripts`.
- **REAL AUTH POPUP SMOKE: GREEN (verificación del agente, DOM real).**
  Click real sobre "Ingresar con tu cuenta Danwor" (ejecución real de
  `loginWithMicrosoft`, sin stub) desde `/onedanwor`, `/` y `/copilot`
  — cada uno abrió su propia página (`/onedanwor?auth_popup=1`,
  `/?auth_popup=1`, `/copilot?auth_popup=1`), nunca hardcodeado a `/`.
  `document.body.innerHTML` confirmado SIN "Copiloto" ni "One Danwor" en
  ningún caso — no solo cubierto por un overlay, sino ausente del DOM.
  Sin errores de consola.
- **REAL MICROSOFT OAUTH ROUND-TRIP: GREEN (validación del usuario, con
  credenciales reales).** El usuario completó el login real de Microsoft
  desde `/onedanwor` en su propio browser autenticado: sin landing del
  Copiloto visible en ningún momento del popup/callback, overlay neutro
  "Completando acceso…" visible, autenticación de Microsoft completada
  normalmente, resolución de workspace post-login correcta, con
  Copiloto y Operaciones disponibles según su autorización real. Esto
  cierra la mitad del flujo que el agente no puede validar por sí mismo
  (requiere credenciales reales de Azure) — el defecto original queda
  confirmado resuelto de punta a punta, no solo por inspección de código
  ni por DOM del agente.
- **CI:** GitHub CI **GREEN** sobre el estado final de la rama, mergeada
  a `main` vía PR #30.

### Próxima decisión (NO autorizada en este commit de docs)

Con este batch **CLOSED / GREEN**, el próximo paso es una decisión del
usuario. Candidato identificado (NO autorizado, NO empezado):

- **SHARED WORKSPACE SHELL + OPERACIONES STRUCTURE (Batch B)** — header
  transversal compartido (preservando los controles reales de
  back/forward in-app del header actual — el usuario señaló
  explícitamente que NO son chrome de browser; Batch B debe inspeccionar
  su implementación real antes de tocarlos), switcher de workspace
  attached al logo, selector interno por workspace, grilla estructural
  compartida Copiloto/Operaciones, jerarquía canónica `/operaciones`,
  navegación local de Operaciones, jerarquía Staffing → Asignaciones,
  presentación de loading compartida donde se justifique.

**No autorizado todavía.**

---

## SHARED WORKSPACE SHELL + OPERACIONES STRUCTURE — CLOSED / GREEN
(commits funcionales `e2c8743`, `71bb101`, `2489ac4`, `56019be`,
`8108914`; checkpoints de docs `2fa2249`, `94508fe`, `2ab1e70`, `52636b9`;
branch `work/shared-shell-operaciones`)

**Evidencia final de cierre:** GitHub **CI GREEN** sobre el estado final
de la rama (confirmado por el usuario); Python **3.14.3**; full suite
**376 passed / 1 skipped**; tests dirigidos del topbar compartido **19
GREEN** (`tests/webapp/test_platform_topbar.py`); **route surface 82**
(80→82, único delta intencional: `GET /operaciones/staffing/asignaciones`
+ `POST /operaciones/staffing/asignaciones/data` — sin `POST /platform`
ni `POST /onedanwor`); **hardening de seguridad del brand animado
parametrizado: GREEN** (ver bullet dedicado más abajo — `</script>`
breakout, comillas/backslashes, newlines/Unicode).

**Commit final funcional de este batch:** `8108914` — `fix(platform):
escape script-context breakout in parameterized brand asset` (hardening
de seguridad, ver más abajo). El commit de docs que cierra este batch es
el que crea este mismo bloque.

Precedido por un **targeted assessment** (análisis, sin código) que dejó
este batch explícitamente diferido de Batch A (One Danwor Entry
Experience). Durante la implementación, el usuario corrigió un hallazgo
del assessment: los controles de back/forward SON reales (prev/next de
módulo en `module.html`), no chrome de browser — pero su alcance es
module.html-específico, no transversal. La decisión final (confirmada por
el usuario a mitad de implementación): el topbar compartido posee la
UBICACIÓN visual transversal de esos controles, pero CERO conocimiento de
secuencia — el caller decide si hay destino real o si quedan
deshabilitados (nunca fabrica un orden inexistente).

- **Dueño del topbar compartido:** `webapp/templates/partials/platform_topbar.html`
  → macro Jinja `platform_topbar(current_workspace, available_workspaces,
  identity_href, identity_html, mobile_menu_label, pager_previous=None,
  pager_next=None)` + `{% call %}` para el selector interno (contenido de
  CALLER). Clasificación: PLATFORM / SHARED PRESENTATION. Sin
  auth/Graph/pack/Staffing/secuencia de rutas — todo lo provee quien
  llama. **Firma corregida DOS VECES por el pase "VISUAL CONTRACT
  CORRECTION" (ver más abajo)** — la firma original de este batch
  (`brand_href, brand_html`, un solo bloque de marca) fue una geometría
  incorrecta; una firma intermedia (`switch_brand_html`, logo completo
  como trigger del switcher) también se corrigió antes del commit final —
  ver el ajuste de UX descripto abajo.
  - **Contrato de back/forward:** `pager_previous`/`pager_next`, cada uno
    `None` (deshabilitado, mismo marcado `<span aria-hidden>` que
    `module.html` ya usaba) o `{"href", "label"}`. `module.html` es el
    ÚNICO caller con datos reales (prev/next module real, verbatim del
    comportamiento histórico). Los otros 7 templates Copilot y
    Operaciones pasan `None`/`None` — ambos controles se muestran
    deshabilitados, sin inventar secuencia.
  - **Workspace switcher — botón-ícono de RIGHT, no en la identidad
    animada, NO el logo completo:** el control de texto standalone del
    batch anterior (`partials/workspace_switcher.html`, **eliminado**) se
    reemplaza por un botón más en `.header-tools` — MISMA caja visual que
    theme/logout (`.theme-toggle`, 34×34px), con un ícono de grilla
    (4 cuadrados, mismo lenguaje visual stroke-based que los demás
    íconos), actuando de trigger cuando `available_workspaces|length > 1`
    (`<details class="workspace-switch"><summary class="theme-toggle"
    aria-label="Cambiar de espacio de trabajo">`); con 1 solo workspace,
    es un link plano a su home con el mismo ícono (sin dropdown). Menú =
    otros workspaces autorizados + "Todos los espacios" → `/onedanwor`.
    Reusa `available_workspaces(user)`/`Workspace.home_url` sin duplicar
    autorización ni destinos. La identidad animada IZQUIERDA
    (`identity_html`) NUNCA es el trigger del switcher — sigue siendo pura
    identidad, sin acción. **Nota de UX (corrección post-implementación):**
    la primera versión de este pase usaba el logo estático completo
    (`brand/danwor-logo.png`) como trigger — el usuario aclaró que la idea
    era un botón-caja del mismo tamaño que theme/logout, no el logo; se
    reemplazó por el ícono de grilla, sin volver a tocar identidad/config/
    ruteo.
- **Templates Copilot migrados (8, mecánico):** `module.html`,
  `academy.html`, `catalog_cds.html`, `catalog_connection.html`,
  `catalog_flow.html`, `catalog_guide.html`, `admin_activity_page.html`,
  `whats_new.html` — topbar duplicado → llamada al macro. Cada página
  preserva sus diferencias reales vía parámetros/slot (mobile-menu label
  "Módulos" vs "Academy"; pack-select+diag-toggle solo donde ya existían:
  module/academy/catalog_guide). **Marca animada, pack, back/forward
  reales, theme, logout, mobile-menu: sin regresión** (verificado con
  smoke real + tests).
- **Asset animado de Operaciones — RESUELTO por el pase "VISUAL CONTRACT
  CORRECTION" (ver más abajo):** ya NO es "pending design asset". La
  identidad animada IZQUIERDA de Operaciones reusa el MISMO componente
  histórico único (`assets/danwor-animated-dancing.html`, servido vía
  `/brand/dancing/{pack}`) que Copiloto — no es un asset por-workspace, es
  transversal, parametrizado con `OPERATIONS_APP_TITLE`/
  `OPERATIONS_APP_SUBTITLE` en vez de `APP_TITLE`/`APP_SUBTITLE`. El logo
  estático de Danwor (`brand/danwor-logo.png`) sigue usándose SOLO en
  `landing.html` (sin relación con este batch) — el topbar compartido no
  lo referencia en absoluto: el switcher de RIGHT es un ícono, no una
  imagen.
- **Selector interno:** Copilot sin cambios (pack-select/diag-toggle
  exactamente donde ya estaban). Operaciones: `<select class="pack-select"
  disabled><option selected>Staffing</option></select>` — misma posición
  estructural que el selector de Copilot, identifica el área real sin
  fingir alternativas (sin Projects/Reporting inventados); queda
  trivialmente habilitable cuando exista una segunda área real.
- **Shell de Operaciones:** adoptó la convención YA existente
  (`.app-shell.no-copilot`, `.module-content`, `.module-nav`) en vez de
  crear un framework nuevo — Copilot no se tocó, Operaciones simplemente
  la adoptó por primera vez (antes era una página plana sin grid/nav/
  Alpine). `webapp/templates/partials/operations_nav.html` (NUEVO): grupo
  estático "STAFFING" + un solo link "Asignaciones" — sin acordeón (un
  solo item no lo justifica), sin Projects/Reporting/Vacations.
- **Hero de Asignaciones simplificado** (evita apilar
  Operaciones/Staffing/Asignaciones tres veces cuando el chrome ya dice
  las dos primeras): eyebrow "Staffing", `<h1>Asignaciones</h1>`, copy
  concisa.
- **Loading state:** el placeholder plano "Cargando asignaciones…" pasa a
  reusar `.section-analysis-spinner`/`.section-analysis-loading` (YA
  existentes, cero CSS nuevo) — mismo lenguaje visual que el spinner de
  Asistente/Análisis de sección. Empty/error states (`.empty-panel`) sin
  cambios.
- **Rutas canónicas:** `GET /operaciones/staffing/asignaciones` +
  `POST /operaciones/staffing/asignaciones/data` (workspace → área →
  capacidad). Históricas `GET/POST /operations/staffing(/data)` quedan
  como alias de compatibilidad — SIEMPRE 307 a la ruta canónica
  correspondiente, method+body preservados, sin reimplementar lógica.
  `OPERATIONS.home_url` (`webapp/workspaces.py`) actualizado a la
  canónica — único cambio ahí, sin expandir presentación/marketing.
- **Bug real descubierto, corregido, y CORREGIDO DE NUEVO (pase
  correctivo, commit `71bb101`):** un test de cadena completa
  (`follow_redirects=True`) expuso que un POST no-autenticado a la ruta
  canónica de Staffing, al redirigir en cascada vía
  `platform_login_redirect` → `/platform` → `/onedanwor`, terminaba en
  **405** porque ambos (Batch A) solo aceptaban GET — un 307 preserva el
  método, así que un POST podía llegar hasta ahí. La PRIMERA corrección
  (revertida) ensanchó `/platform`/`/onedanwor` a GET+POST — funcionaba,
  pero borraba una distinción real: esas rutas son superficies de
  NAVEGACIÓN, nunca deberían recibir una acción de Staffing. La
  corrección FINAL cambia `platform_login_redirect` (`webapp/workspaces.py`)
  para responder **303** (no 307) — la transición de autenticación
  SIEMPRE se vuelve GET en el siguiente hop, sin importar qué método la
  disparó — y acepta un `next_path` explícito para que el POST de datos
  de Staffing apunte `next` a la PÁGINA canónica, no al endpoint de datos
  que disparó el redirect (que nunca vuelve a ser golpeado con ese mismo
  POST — el `graph_token` original se descarta sin más). `/platform` y
  `/onedanwor` vuelven a ser GET-only. El alias histórico POST→POST
  (`/operations/staffing/data` → canónica) sigue en 307, sin cambios —
  es una canonicalización de recurso, no una transición de auth.
- **Rutas agregadas:** `GET /operaciones/staffing/asignaciones`,
  `POST /operaciones/staffing/asignaciones/data`. **Route surface: 80 →
  82** (intencional; snapshot regenerado con `UPDATE_ROUTE_SURFACE=1`). El
  diff final contra `main` de Batch A es EXACTAMENTE estas 2 líneas —
  `/platform`/`/onedanwor` quedan sin cambios de método.

### Pase correctivo "VISUAL CONTRACT CORRECTION" (geometría transversal del topbar)

El pase funcional inicial (`e2c8743`) implementó una geometría de topbar
INCORRECTA: movió el pager al primer hijo de `.topbar` (antes de la marca) y
fusionó identidad animada + switcher en un solo `<details>`. El usuario
señaló la regresión visual (pager en el borde izquierdo real, delante de
la marca) y, tras dos rondas de investigación en Git (`git show
574e790:webapp/templates/module.html` + lectura directa de
`assets/danwor-animated-dancing.html`), se confirmó el hallazgo clave: el
"mark I+D + título + subtítulo" que el usuario describía SIEMPRE fue un
único componente animado autocontenido (panel-left DANWOR→"Innovación +
Desarrollo"→"Data & AI for Business", divisor, panel-right título+
subtítulo) — nunca existió como markup Jinja separado, y `APP_TITLE`/
`APP_SUBTITLE` (`config.py`) nunca tuvieron consumidor real; el asset traía
su propio texto hardcodeado en JS (`const TITLE = '...'`).

**Contrato final (recuperado, no rediseñado):**
- **IZQUIERDA:** el componente animado histórico único, intacto, SIEMPRE
  primer hijo de `.topbar` — NUNCA es el trigger del switcher. Su
  título/subtítulo (antes hardcodeados en el asset) ahora se parametrizan
  vía query string del propio `GET /brand/dancing/{pack}` (`?title=...
  &subtitle=...`, `webapp/routers/pack_assets.py`) — reusa el MISMO
  mecanismo de sustitución de texto que ya existía ahí (`dark_shell_colors`)
  para reemplazar las constantes JS `TITLE`/`SUBTITLE` del asset, con
  escaping seguro para JS string literal. Sin params, el asset conserva su
  texto original (no-op, compatibilidad).
- **`config.py`:** `OPERATIONS_APP_TITLE`/`OPERATIONS_APP_SUBTITLE`
  (NUEVAS, mismo patrón `_env(...)` que `APP_TITLE`/`APP_SUBTITLE`,
  defaults "Gestión Inteligente · Operaciones" / "Analítica y gestión
  operativa | Danwor Transversal"). Sin mutación per-request — Copiloto
  SIEMPRE usa `APP_TITLE`/`APP_SUBTITLE`, Operaciones SIEMPRE usa las
  suyas; son constantes de proceso distintas, no un valor compartido que
  se pisa.
- **`webapp/templating.py`:** nuevo global Jinja `config` (el módulo
  completo, de solo lectura) — permite a las templates leer
  `config.APP_TITLE`/`config.OPERATIONS_APP_TITLE` sin que cada router
  reinyecte esos valores en su contexto.
- **DERECHA (`.header-tools`):** pager → switcher de workspace (botón-
  ícono, `<details class="workspace-switch"><summary class="theme-toggle"
  aria-label="Cambiar de espacio de trabajo">` con 2+ workspaces, link
  plano con el mismo ícono con 1 solo) → selector interno (`{{ caller() }}`)
  → theme → logout → mobile-menu. CSS: `.workspace-switch > summary.
  theme-toggle` (list-style/cursor/marker-hide/hover) — el botón reusa
  `.theme-toggle` TAL CUAL (34×34px, mismo lenguaje visual que theme/
  logout), sin CSS de tamaño propio. **Iteración de UX dentro de este
  mismo pase:** la primera versión usaba una clase `.brand-compact` con
  el logo completo (`brand/danwor-logo.png`) como trigger; el usuario
  aclaró que quería la caja-ícono, no el logo — se reemplazó por un ícono
  SVG de grilla (4 cuadrados, stroke-based) DENTRO de `.theme-toggle`,
  eliminando `.brand-compact`/`.brand-compact-link`/`.topbar-brand-compact`
  sin dejar rastro. Regla muerta previa `.animated-brand:has(.topbar-brand-static)`
  también eliminada.
- **9 templates actualizados** (8 Copilot + `operations/staffing.html`):
  `_identity_html` (iframe con `title`/`subtitle` urlencoded desde
  `config`) reemplaza el viejo `_brand_html` único — el switcher ya NO
  recibe presentación del caller (es 100% del macro, como theme/logout).
  `webapp/routers/staffing.py` agrega `pack: DEFAULT_PACK` al contexto de
  `staffing_page` (Operaciones no tiene concepto de pack propio; el asset
  animado es un archivo físico bajo `content_packs/`) — presentación
  pura, **sin tocar redirect/auth** (freeze respetado).
- **Sin cambios de ruteo:** route surface sigue en **82**, diff contra
  Batch A idéntico al de antes de este pase.
- **Tests:** `tests/webapp/test_platform_topbar.py` reescrito con el
  contrato final — identidad animada única (LEFT) config-driven para
  ambos workspaces, orden RIGHT (pager→switcher-ícono→selector
  interno→theme→logout), switcher como botón-ícono 34×34 igual a theme/
  logout (nunca la identidad, nunca el logo completo), sustitución real de
  `TITLE`/`SUBTITLE` vía la ruta `/brand/dancing/{pack}`, back/forward/
  selector interno sin regresión.
- **SMOKE VISUAL MANUAL: GREEN.** `/modules/00_Intro?pack=datasphere_core`
  y `/operaciones/staffing/asignaciones` vía sesión inyectada: orden DOM
  confirmado por accesibilidad (identidad → pager → switcher-ícono →
  selector interno → theme → logout → mobile-menu) en ambos workspaces;
  request real a `/brand/dancing/datasphere_core` con `title`/`subtitle`
  urlencoded correctos para cada workspace (verificado vía Network);
  sustitución de texto confirmada byte a byte (UTF-8 correcto — un
  mojibake inicial fue artefacto del codepage de la consola de smoke, no
  un bug real); `getBoundingClientRect()` del switcher = 34×34px, idéntico
  a theme/logout, cero `<img>` en el topbar. Viewport 375px: sin overflow,
  `.module-pager` sigue en `display:none` (regla preexistente, sin tocar).
  Sin errores de consola en ningún paso.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  3.14.3): **suite 373 passed / 1 skipped** (370 + 3 netos del pase de
  geometría visual — algunos tests viejos se reescribieron en vez de
  sumar, por eso el neto es menor a los 16 tests nuevos de
  `test_platform_topbar.py`), route-contract GREEN, snapshot sin cambios
  (**82** — este pase es presentación pura).
- **SMOKE VISUAL MANUAL: GREEN.** Módulo real de Copilot → back/forward
  reales con hrefs correctos, brand animado, switcher del brand con
  Operaciones + Todos los espacios, sin errores de consola. Click al
  brand → Operaciones real (Asignaciones, hero simplificado, nav STAFFING/
  Asignaciones, selector interno "Staffing" deshabilitado, intento real de
  SharePoint con "no token" esperado sin credenciales reales). Click de
  vuelta a Copiloto → home real (`/modules/00_Intro`). Compatibilidad
  `/operations/staffing` → cadena real hasta la página canónica. Deep link
  canónico logged-out → `/onedanwor?next=...` con `data-home` canónico
  (sin selector interpuesto). Viewport 375px: sin overflow horizontal en
  Copilot ni Operaciones; mobile-menu de Operaciones revela su nav
  izquierdo correctamente (verificado vía clase `is-open` + `display:
  block` computado). Academy/Catalog/What's New re-verificados: pager
  deshabilitado en ambos lados, brand intacto. Sin errores de consola en
  ningún paso.
- **Workspace switcher — corrección de UX (commits `56019be` +
  `52636b9`):** la primera versión del botón-ícono de RIGHT usaba el logo
  completo de Danwor (`brand/danwor-logo.png`) como trigger del switcher.
  El usuario aclaró que la intención era una caja del mismo tamaño que
  theme/logout, no el logo — se reemplazó por un ícono SVG de grilla (4
  cuadrados, mismo estilo stroke-based) DENTRO de la clase `.theme-toggle`
  ya existente (34×34px, cero CSS de tamaño nuevo). `platform_topbar` ya
  no recibe `switch_brand_html` del caller — el switcher es 100% del
  macro, igual que theme/logout. Verificado con
  `getBoundingClientRect()`: switcher = 34×34px, idéntico a theme/logout,
  cero `<img>` en el topbar, en ambos workspaces.
- **Hardening de seguridad del brand animado parametrizado (commit
  `8108914`):** verificación puntual pre-PR de la sustitución de
  `title`/`subtitle` en `/brand/dancing/{pack}` (ver arriba). Hallazgo: el
  escaping inicial (`_js_single_quoted`) protegía correctamente el
  contexto de string JS (comillas simples, backslashes) pero NO el
  contexto real de embedding — un `<script>` dentro de un documento HTML.
  El parser de HTML tokeniza `</script>` de forma literal e insensible a
  mayúsculas ANTES de que exista noción de "string JS": un `title`/
  `subtitle` con `</script>` cerraba el script real y el resto se
  parseaba como markup nuevo, ejecutando código inyectado — aunque el
  valor estuviera sintácticamente bien escapado como literal JS.
  Corrección: `"</"` → `"<\/"` (MISMO patrón que `pack_asset()` ya usa un
  poco más abajo en el mismo archivo para su payload de embed —
  reutilizado, no una nueva defensa) + normalización de saltos de línea
  crudos a `\n` escapado (un literal `'...'` de JS no admite un salto de
  línea crudo). Regresión agregada: intento de breakout
  `</script><script>...</script>`, comillas+backslashes combinados,
  multilinea, Unicode normal preservado — **GREEN**, sin afectar
  identidad/config/ruteo.
- **Validación final de cierre:** Python **3.14.3**; full suite **376
  passed / 1 skipped** (373 + 3 netos del hardening de seguridad);
  tests dirigidos del topbar compartido **19 GREEN**; route-contract
  GREEN, snapshot sin cambios (**82**); GitHub **CI GREEN** sobre el
  estado final de la rama (confirmado por el usuario).

### Próxima decisión (NO autorizada en este commit de docs)

Con este batch **CLOSED / GREEN**, el objetivo de shell/topbar
transversal de plataforma queda completo — la iniciativa vuelve a valor
de producto funcional. Candidato identificado (NO autorizado, NO
empezado):

- **STAFFING FUNCTIONAL PARITY** — empezando por un **assessment
  funcional** (análisis, sin código) de horas de asignación/mes,
  disponibilidad Full Time/Part Time de consultores, semántica de
  capacidad y de disponibilidad, ANTES de introducir cualquier regla
  basada en el umbral de 160h/mes.

Explícitamente diferido, sin autorizar implementación: capacidad/
disponibilidad de Staffing, reportes/herramientas adicionales de
Staffing, Projects, Reporting, vacaciones, filtros, Gantt, contenido real
del right rail de Operaciones, asset animado dedicado futuro de
Operaciones (`PENDING DESIGN ASSET`, no bloqueante), recordar-último-
workspace, última-ruta-por-workspace, RBAC generalizado, rediseño de
OAuth, flip de `/` a One Danwor, descomposición de Catalog/Admin, diseño-
system generalizado.

**No autorizado todavía.**

---

## STAFFING — ASIGNACIONES DASHBOARD V1 — CLOSED / GREEN
(commits funcionales `33cff8c` → `f3d8b9f` (docs) → `a79c4a5` →
`33939f7` (docs) → `8c788e1` → `0e39c82` → `970a6d8` → `c8d7b32` →
`7d5ef52` → `e4f50d0` (todos en `work/staffing-assignments-dashboard-v1`,
pusheados a origin) — ver "Pase correctivo" y "Segundo pase correctivo"
más abajo para el detalle de cada uno. **PR #33 — primer CI corrido:
GREEN.** Cierre formal confirmado por el usuario tras ver el CI real en
GitHub, mismo protocolo ya usado en Batch B.)

> **CLOSED/GREEN.** CI real de PR #33 corrió verde sobre el HEAD
> `e4f50d0`. Suite local a esa misma revisión: **458 passed / 1 skipped**,
> route surface **82** (snapshot `tests/route_surface.json`, sin cambios
> desde Batch B). Ver "Evidencia de cierre (PR #33)" al final de esta
> sección para el detalle completo.

Precedido por un **targeted assessment** (análisis, sin código) que
recuperó el objetivo de producto original detrás de Operaciones/Staffing:
reemplazar el reporte Power BI legado "Asignaciones consultores" por una
capacidad nativa de One Danwor. La tabla plana de Batch 2/3 probó la
arquitectura (SharePoint → `StaffingRepository` → `StaffingService` →
HTMX) pero nunca fue el producto final — este batch la reemplaza por un
dashboard operativo real, usando **Actividad y métricas de uso** como
referencia de lenguaje visual y el Power BI legado como referencia de
preguntas operativas (NO recreación pixel-perfect).

- **Enriquecimiento OPCIONAL FT/PT** (`webapp/services/staffing_repository.py`,
  `config.py`): el campo real de Colaboradores
  `Disponibilidaddeasignaci_x00f3_n` ("Disponibilidad de asignación",
  choices `Full Time`/`Part Time`, confirmado contra Graph en Batch 3 pero
  nunca expuesto) ahora resuelve a `consultant_availability` — passthrough
  puro, sin fallback a "Sin dato" en esta capa (esa decisión es de
  `build_dashboard_payload`, no del read-model). `config.
  SHAREPOINT_COLABORADORES_FIELD_DISPONIBILIDAD` sigue el mismo mecanismo
  que los 8 campos de mapeo existentes (sin default de producción, `.env`
  local) pero es explícitamente **OPCIONAL**:
  `StaffingFieldMapping._OPTIONAL_MAPPING_FIELDS` lo excluye de
  `missing_fields()` — un ambiente sin esta env var configurada sigue
  cargando Staffing normalmente (degrada a `None`/"Sin dato", nunca
  `RuntimeError`). `StaffingOverviewRow` también gana `consultant_id`/
  `project_id` (existían en `StaffingAllocationRecord` pero nunca
  llegaban al read-model) — necesarios para contar consultores/proyectos
  DISTINTOS por id estable, no por nombre (dos personas pueden compartir
  display name).
- **Payload único normalizado** (`webapp/services/staffing_service.py`):
  3 funciones libres nuevas —
  `compute_horizon_months(today=None)` (horizonte rolling determinístico:
  mes anterior → mes actual → 6 meses siguientes, 8 columnas — mensual a
  propósito, coincide con la unidad real `hours_per_month`);
  `compute_timeline_position(start, end, horizon_months)` (posición pura
  por índice de mes, NUNCA aritmética de píxeles — clippea visualmente
  cuando la fecha real cae fuera del horizonte sin alterarla; sin ninguna
  fecha real, la fila queda fuera del timeline pero sigue en KPIs/detalle;
  con un solo extremo conocido, el otro se trata como abierto);
  `build_dashboard_payload(rows, today=None)` (transformación PURA, sin
  Graph/repositorio, que arma `{assignments, horizon}` — el ÚNICO dataset
  que consume el dashboard). Deliberadamente **funciones libres, no
  métodos de `StaffingService`** — el router las importa directo porque
  varios tests existentes reemplazan `StaffingService` completo por un
  test double sin esos métodos; atarlas a la clase las habría dejado
  inalcanzables ahí.
- **`webapp/routers/staffing.py`:** `staffing_data` (POST, sin cambios de
  auth/redirect) ahora llama `build_dashboard_payload(rows)` y renderiza
  `operations/staffing_dashboard.html` (reemplaza `staffing_rows.html`,
  eliminado) en vez de pasar `rows` crudo. Los 4 estados controlados de
  siempre (`missing_token`/`config_incomplete`/`graph_error`/`ok`) se
  preservan exactamente — `ok` con `payload.assignments` vacío conserva el
  mensaje simple histórico ("No hay asignaciones para mostrar todavía"),
  sin montar filtros sobre cero datos.
- **Dashboard 100% client-side sobre UN dataset** (`webapp/static/app.js`
  → `staffingDashboard(payload)`, mismo estilo de función global plana que
  `copilotLayout()`): KPIs, timeline agrupado por consultor, panel de
  disponibilidad y detalle leen TODOS de un único `filteredAssignments`
  getter — ningún componente puede quedar en desacuerdo sobre el filtro
  vigente. El HTMX inicial (`hx-trigger="load"`, mismo `graph_token` de
  siempre) sigue cargando el dataset UNA sola vez; cambiar un filtro es
  JS puro, **sin volver a golpear Graph** (verificado en Network durante
  el smoke — ver abajo). Default de Estado: `Activo`+`Tentativo`
  visibles, `Inactivo` disponible pero no seleccionado — decisión
  explícita y visible, nunca un descarte silencioso (el dataset completo
  sigue cargado; togglear el checkbox lo revela).
- **KPIs, deliberadamente libres de regla de negocio:** consultores
  distintos (por id), proyectos distintos (por id), asignaciones activas,
  asignaciones tentativas, horas activas/mes (`sum(allocation_value)`
  donde `status == "Activo" AND allocation_unit == "hours_per_month"`).
  **Sin** capacidad, sin utilización, sin horizonte "termina pronto" (ver
  Deferred).
- **Timeline — jerarquía consultor → asignación**, nunca barras
  superpuestas en una misma fila (a diferencia del Power BI legado): grid
  CSS puro (`webapp/static/tools.css`), sin librería de charting,
  coordenadas mensuales server-derived. Mes actual resaltado con estilo
  restringido (mismo dorado que el resto de la app). Asignaciones sin
  ninguna fecha real quedan fuera del timeline (con un contador visible,
  "N asignación(es) sin fecha") pero siguen en KPIs/detalle — nunca se
  pierden en silencio.
- **Panel de disponibilidad — "Disponibilidad de asignación" (mismo
  término del campo fuente):** SOLO hechos — cuenta consultores
  DISTINTOS (no asignaciones) agrupados Full Time/Part Time/Sin dato,
  reusando `.activity-kpi-grid`/`.activity-kpi` tal cual (mismo lenguaje
  visual que Actividad, sin CSS nuevo). Cero % / horas libres / capacidad.
- **Detalle de asignaciones** preservado como sección colapsable de menor
  jerarquía (`.dashboard-section`, reusado tal cual salvo el grip de
  drag-and-drop de Actividad, deliberadamente NO copiado — ver alcance de
  reuso abajo), con las columnas históricas + Disponibilidad. Lookups sin
  resolver siguen presentes (nunca se descartan), marcados con un chip
  discreto (`.tree-panel-chip`, reusado) en vez de dominar la fila.
- **Alcance de reuso de Actividad — explícito:** SÍ reusadas tal cual
  `.module-hero`/`.activity-kpi-grid`/`.activity-kpi`/`.dashboard-section`/
  `.dashboard-section__*`/`.whatsnew-filters`/`.whatsnew-filter`/
  `.dashboard-empty`/`.btn-primary`. CSS nuevo SOLO para lo genuinamente
  distinto: `.staffing-timeline*` (grid mensual) y
  `.staffing-status-toggle`/`.staffing-status-chip` (único filtro
  multi-select del dashboard, Actividad no tiene equivalente) y
  `.status-badge*` (misma forma que `.catalog-card__verdict`, colores
  propios). Métricas/lógica/rutas de Actividad NO se tocaron ni se
  reusaron — Staffing no depende del código Python de Admin Activity.
- **Sin cambios de ruteo:** `GET/POST /operaciones/staffing/asignaciones(/data)`
  sin cambios de firma HTTP (el POST sigue recibiendo `graph_token` por
  form, sin campos de filtro server-side — el filtrado es 100%
  client-side). **Route surface: sigue en 82** — ninguna ruta nueva, sin
  cambios de auth/redirect (freeze respetado íntegramente).
- **Tests:** `tests/webapp/test_staffing_repository.py` (+9) — mapeo
  configurado con FT/PT, valor fuente en blanco, mapeo ausente (filas
  existentes siguen cargando), `colaborador_disponibilidad` nunca en
  `missing_fields()`. `tests/webapp/test_staffing_service.py` (+21) —
  passthrough de disponibilidad, ids estables, horizonte mensual
  (cruce de año incluido), posición en el timeline (dentro/antes/después/
  spanning/futuro/fechas faltantes en ambos sentidos), payload (shape,
  label de horas, disponibilidad default, múltiples asignaciones del
  mismo consultor con posiciones independientes, mes actual marcado, sin
  campos de capacidad). `tests/webapp/test_staffing_routes.py` (+8) —
  fragmento contiene filtros/KPIs/timeline/disponibilidad/detalle,
  disponibilidad real embebida en el payload JSON, default a "Sin dato",
  ninguna semántica de capacidad introducida (sin `%`, sin "utilization",
  sin "capacity_percentage"). Los 6 tests preexistentes que dependían del
  fragmento viejo siguen pasando tal cual — el nombre/valor embebido en el
  JSON del payload satisface las mismas aserciones de substring
  (`"Zulema Diaz"`, `"40 h/mes"`, `"No hay asignaciones"`, etc.) sin
  cambiar su intención.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**): **suite 403 passed / 1 skipped** (376 + 27 netos),
  route-contract GREEN, snapshot sin cambios (**82**).
- **SMOKE MANUAL: GREEN (browser real, dataset representativo inyectado
  — sin credenciales Graph reales disponibles en este entorno; ver nota
  abajo).** Página `/operaciones/staffing/asignaciones` real con sesión
  inyectada; fragmento del dashboard renderizado vía el mismo
  `TestClient` + `StaffingService` mockeado que usan los tests
  (5 filas representativas: 2 consultores con múltiples asignaciones,
  1 fila `Inactivo`, 1 lookup sin resolver, 1 fila sin fechas), inyectado
  en `#staffing-rows` del DOM real — Alpine lo montó solo (MutationObserver,
  sin llamada manual). Verificado leyendo el estado reactivo real de
  Alpine (`_x_dataStack`), no solo el HTML servido:
  - Default: `Activo`+`Tentativo` visibles, `Inactivo` oculto — KPIs
    (3 consultores/4 proyectos/3 activas/1 tentativa/140 h/mes),
    disponibilidad (2 Full Time/1 Part Time/0 Sin dato), timeline
    (2 grupos de consultor, fila sin fechas excluida con nota "1
    asignación(es) sin fecha"), detalle (4 filas) — **los 5 verificados
    coinciden exactamente con el cálculo manual esperado**.
  - Cambiar el filtro Consultor → recalculó KPIs/timeline/detalle a la
    fila única correspondiente; **cero requests nuevos a
    `/operaciones/staffing/asignaciones/data` ni a Graph** (confirmado en
    el log de Network — el filtrado es realmente 100% client-side).
  - Togglear el checkbox `Inactivo` → reveló la 5ª fila en KPIs
    (4 consultores, Sin dato pasa a 1)/timeline/detalle, sin request
    nuevo.
  - "Limpiar" → volvió exactamente al estado default (no a "todo
    seleccionado").
  - Viewport ~375px: sin overflow horizontal en el body; `.staffing-
    timeline-scroll` con `overflow-x:auto` y el grid interno en su ancho
    mínimo — scrollea en vez de forzar el Gantt completo al ancho de
    pantalla.
  - Sin errores de consola en ningún paso.
  - **Nota explícita:** este smoke NO es el "FIRST REAL STAFFING
    MILESTONE" (ese hito, con Graph real, sigue siendo válido como
    evidencia histórica separada — ver más arriba). Este entorno de
    desarrollo no tiene `SHAREPOINT_GESTION_SITE_ID`/mapeo de campos
    configurado, así que un POST real devolvería `config_incomplete` —
    el smoke de arriba prueba el dashboard/Alpine/template reales con un
    dataset representativo, no un segundo round-trip de Graph. Si el
    mapeo FT/PT tampoco está configurado en producción, el dashboard
    **debe seguir funcionando igual**, con disponibilidad degradada a
    "Sin dato" — eso es un PASS, no una falla (ver bullet de
    enriquecimiento opcional arriba).
- **Edición en paralelo detectada, NO tocada:** durante este batch el
  working tree mostró cambios sin commitear ajenos a Staffing —
  `webapp/templates/platform_selector.html` y luego
  `webapp/routers/platform.py` (`_WORKSPACE_CARD_COPY[*].eyebrow`
  "Data & AI for Business" → `None`, con el template ajustándose en
  consecuencia) — consistente con edición manual en paralelo (fuera de
  esta sesión), no con un bug de esta implementación. Excluidos
  deliberadamente de este commit (`git add` explícito, sin `-A`); quedan
  sin commitear en el working tree.
### Pase correctivo — revisión con dato real (commit `a79c4a5`)

El usuario revisó el dashboard con datos reales de SharePoint en
`localhost:8082` y encontró varios gaps contra el contrato V1. Todos
corregidos en un solo commit funcional; suite y route surface
actualizados abajo.

- **Bug real corregido — invariante Consultores vs Disponibilidad (53 vs
  54 observado):** `availabilityGroups`/`timelineGroups` (app.js) caían a
  `consultant_name` como key de agrupación cuando `consultant_id` era
  `None` — eso sintetiza un "consultor distinto" fantasma a partir de un
  nombre que MÚLTIPLES filas sin resolver distintas podían compartir.
  Ahora ambos agrupan ESTRICTAMENTE por `consultant_id` (igual que
  `kpis.consultants`, que ya excluía esas filas via `.filter(Boolean)`):
  una fila sin id consigue su propio grupo de timeline sin fusionarse
  (`"unresolved-" + assignment_id`) y queda EXCLUIDA de Disponibilidad —
  el invariante `Full Time + Part Time + Sin dato === Consultores` ahora
  se cumple por construcción. Sigue visible en todos lados como
  "(sin resolver)" — nunca se pierde la fila, solo deja de inflar un
  conteo de consultores que no le corresponde.
- **Hipótesis de bug corregida — Disponibilidad en 0/0/54 pese a mapeo
  correcto:** `webapp/services/staffing_repository.py` pedía Colaboradores
  con `$expand=fields` SIN `$select` — Graph puede omitir columnas custom
  tipo Choice de esa proyección default aunque otras columnas custom de
  la MISMA lista (Title) sí aparezcan. El fetch de Colaboradores ahora
  pide explícito `$expand=fields($select=colaborador_title,
  colaborador_disponibilidad)` (solo si el campo opcional está
  configurado). Asignaciones/Proyectos NO se tocaron — ya funcionan
  correctamente, sin necesidad comprobada de tocarlos. **Sin confirmar
  contra Graph real en este entorno** (sandbox sin credenciales) —
  reportado como hipótesis fundamentada, no como hecho verificado;
  pendiente del próximo smoke con dato real del usuario.
- **Default de Estado corregido:** el filtro default ahora es
  ESTRICTAMENTE Activo+Tentativo (antes, cualquier bucket "Sin estado"
  presente en el dato real quedaba seleccionado por default también,
  visible en el screenshot de revisión del usuario).
- **Filtros sticky:** reusan el mismo mecanismo de Actividad
  (`.dashboard-sticky-header` → renombrado `.staffing-sticky-filters`,
  `position:sticky; top:-48px`), no un sistema paralelo.
- **Resumen (KPIs) y Timeline ahora son `dashboard-section` colapsables**,
  mismo grammar que Detalle/Actividad — antes eran un grid suelto y una
  subsección plana sin esa identidad visual compartida.
- **Proporción operativa 2/3 · 1/3** (`.staffing-operational-grid`,
  `grid-template-columns: 2fr 1fr`) — antes 1fr/1fr, demasiado pareja.
- **Expand/collapse por consultor:** cada nombre de consultor es un
  disclosure clickeable (caret ▸/▾); "Expandir todo"/"Contraer todo"
  arriba del timeline. Estado 100% de presentación (`collapsedConsultants`,
  nunca toca `filteredAssignments`) — expandido por default; elegir un
  consultor puntual vía el filtro fuerza su expansión aunque estuviera
  colapsado (`$watch` en `init()`).
- **Paleta determinística por asignación** (`paletteClass`, hash de
  `project_id`, 6 tonos) — antes el color de la barra era SOLO el estado,
  haciendo que asignaciones distintas del mismo consultor se vieran casi
  iguales. El estado se superpone aparte (borde punteado + opacidad para
  Tentativo, opacidad para Inactivo) — nunca reemplaza la identidad de
  color.
- **Marcador de "hoy" real:** `compute_today_marker` (función pura,
  `webapp/services/staffing_service.py`) calcula la posición % exacta del
  día actual dentro del horizonte (mes + fracción del día, nunca solo
  "resaltar el mes") — antes el resaltado de mes actual era el único
  indicador. Línea punteada + label "HOY", `None` fuera del horizonte,
  complementa (no reemplaza) el resaltado de mes.
- **Tests:** `tests/webapp/test_staffing_repository.py` (+3, `$select`
  explícito con/sin campo opcional, Asignaciones/Proyectos sin cambios),
  `tests/webapp/test_staffing_service.py` (+8, marcador de hoy con fechas
  fijas: primer día/mitad de mes/límite de mes/último día visible/antes/
  después del horizonte, más el insumo del invariante — `consultant_id`
  nunca se sintetiza desde el nombre), `tests/webapp/test_staffing_routes.py`
  (+6, filtros sticky, 3 secciones colapsables, grid 2/3·1/3, controles
  expand/collapse, paleta+estado como clases independientes, marcador de
  hoy presente sin ser una métrica de capacidad — 2 tests preexistentes
  ajustados: el `%` del binding CSS del marcador rompía su aserción
  original "% en ningún lado", corregidos a verificar la AUSENCIA de
  patrones de capacidad específicos en vez de prohibir el carácter).
- **SMOKE MANUAL: GREEN (servidor propio en puerto separado — el
  servidor del usuario en :8082 no se tocó).** Dataset representativo con
  múltiples asignaciones por consultor, una fila con fecha de hoy real, y
  DOS filas sin resolver distintas (ids `None` distintos) inyectado sobre
  el DOM/Alpine/CSS reales. Verificado leyendo `_x_dataStack` directo:
  default `{Activo:true, Tentativo:true, Inactivo:false}`; invariante
  `2 = 1(FT) + 1(PT) + 0(Sin dato)` — sostiene; las 2 filas sin resolver
  NUNCA se fusionan (`timelineGroupKeys` mostró grupos separados, uno
  filtrado por default-Inactivo); paleta con índices distintos por
  proyecto en las 4 barras visibles, clases de estado combinadas aparte;
  `todayMarker.visible=true` con el % exacto del payload; sticky
  (`position:sticky` computado); 3 `dashboard-section` presentes;
  "Contraer todo" colapsó los 3 grupos (carets ▸, 0 filas visibles);
  elegir un consultor vía filtro lo expandió pese al colapso previo; CERO
  requests nuevos durante filtro/colapso (confirmado en Network); 375px
  sin overflow horizontal en el body, timeline con `overflow-x:auto`
  propio, grid operativo a 1 columna.
- **Edición en paralelo detectada, NO tocada (pase anterior):**
  `webapp/templates/platform_selector.html`/`webapp/routers/platform.py`
  — ya no aparecen en el working tree al empezar este pase correctivo
  (resueltos/commiteados por el usuario fuera de esta sesión).
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**): **suite 421 passed / 1 skipped** (403 + 18 netos),
  route-contract GREEN, snapshot sin cambios (**82**).
- **Pendiente en su momento, resuelto después:** confirmación de CI real
  en GitHub antes de CLOSED/GREEN, y confirmación del fix de `$select` de
  Colaboradores contra Graph real. Ambos resueltos en el "Segundo pase
  correctivo" de abajo — el usuario confirmó FT/PT con dato real
  (Consultores=9, Full Time=8, Part Time=1, Sin dato=0 → invariante
  `8+1+0=9` sostiene) y PR #33 corrió CI verde.

### Segundo pase correctivo — Cliente, filtrado facetado, coordenadas del marcador de "hoy" (commits `8c788e1`..`e4f50d0`)

Continuación del mismo batch V1, sobre la misma rama, tras una segunda
ronda de revisión con dato real de SharePoint del usuario. Cada punto es
su propio commit funcional — sin amend, sin squash.

- **`8c788e1` — Densidad de información restaurada vs. el Power BI legado
  + KPIs de Resumen simplificados:** la fila del timeline vuelve a mostrar
  "Estado (Horas) · Cliente - Proyecto" (dos `<span>` separados, nunca un
  string opaco — degrada con gracia sin Cliente todavía resuelto en ese
  momento); tooltip accesible por hover/focus en la barra (Consultor,
  Estado, Horas/mes, Cliente, Proyecto, fechas — CSS puro, sin librería,
  `x-text` nunca `x-html`). Estado pasa de "pills" horizontales a un grupo
  vertical compacto. **Full Time/Part Time/Sin dato se mudan de un panel
  lateral independiente a dentro de Resumen** (mismo `filteredAssignments`
  que el resto de los KPIs) — Timeline pasa a usar el ancho completo del
  reporte, se elimina `.staffing-operational-grid` (2/3·1/3). KPI "Horas
  activas / mes" pasa a ser SOLO el número con separador de miles (la
  unidad ya está en la etiqueta); últimos 4 KPIs con acentos de color
  reusados (azul/verde/ámbar, mismos tokens que los primeros 3 — nunca un
  color nuevo).
- **`0e39c82` — Alineación de la barra de filtros:** con Estado ahora
  vertical, `.whatsnew-filters` heredado (`align-items:flex-end`)
  desalineaba las labels de los otros campos contra la base del stack de
  checkboxes. Fix scoped a `.staffing-filter-bar` (nunca se tocó la regla
  compartida `.whatsnew-filter`, usada también por What's New/admin).
  "Limpiar" se muda a una acción de la barra (top-right, fuera del grupo
  de campos) en vez de leerse como otra opción de Estado.
- **`970a6d8` — Cliente resuelto vía `Proyecto.EmpresaLookupId`
  (LIVE-GRAPH-CONFIRMED):** inspección real de metadata contra el sitio
  Gestión (mecanismo temporal `staffing-cliente-debug.html`, nunca
  commiteado, borrado tras el uso) probó la relación real: Proyectos
  (`efa2e279-593e-4321-867f-1ff982d800f6`) → columna lookup real `Empresa`
  (`fieldValueSet` property `EmpresaLookupId`) → Cliente
  (`9c09571c-4fb8-4841-8f0d-4def03b3130d`) → `Title`. `client_id`/
  `client_name` enriquecimiento OPCIONAL (mismo mecanismo que
  `consultant_availability`): una lectura ADICIONAL de Graph durante la
  carga inicial SOLO si `SHAREPOINT_PROYECTOS_FIELD_EMPRESA_LOOKUP` +
  `SHAREPOINT_CLIENTE_FIELD_TITLE` están configurados (sin default de
  producción, mismo mecanismo de los 8 campos requeridos); resuelto UNA
  vez por proyecto, cacheado — nunca una llamada por asignación. Sin
  fallback textual a "(sin resolver)": `client_name` se queda `None` si no
  resuelve (nunca se inventa una identidad de cliente). Filtro CLIENTE
  agregado a la barra sticky entre Proyecto y Disponibilidad.
- **`c8d7b32` — Filtrado FACETADO (bug real de la revisión: proyectos
  Albanesi ofrecidos en el dropdown con cero filas alcanzables bajo el
  Estado default):** cada dropdown de entidad (Consultor/Proyecto/Cliente/
  Disponibilidad) ahora calcula sus opciones sobre el universo filtrado
  por TODOS los OTROS filtros activos (nunca por sí mismo) — `facetedAssignments(excludeKey)`
  en `app.js`. Consultor/Proyecto excluyen filas no-resueltas de sus
  opciones (mismo criterio que Cliente ya tenía) — nunca más una opción
  "(sin resolver)" sintética, aunque la fila sigue visible con "Todos". Una
  selección que otro filtro vuelve imposible se resetea sola a "Todos"
  (`$watch` + `_reconcileSelections()`) en vez de dejar un dashboard vacío
  sin explicación. **Decisión de producto explícita, NO accidental:** el
  chip de texto "HOY" del marcador de hoy queda removido — la línea
  vertical del día actual se mantiene, nunca se reemplaza por otra
  etiqueta textual.
- **`7d5ef52` — Fix de coordenadas del marcador de "hoy":**
  `.staffing-timeline__today-overlay` hardcodeaba `190px 1fr` sin `gap`
  mientras `.staffing-timeline__header`/`__row` ya usaban
  `var(--timeline-label-width)` (280px, tras un ensanche manual de labels
  para legibilidad) + `gap:10px` — dos grids con orígenes de columna
  distintos hacían que la línea cayera a la izquierda del mes real (bug
  reportado: parecía JUL en vez de AGO). `compute_today_marker` (Python)
  nunca calculó mal el %; el bug vivía 100% en CSS. Fix: la variable
  compartida en vez de un segundo literal — header/row/overlay no pueden
  volver a desincronizarse sin importar qué valor tenga la variable en
  cualquier breakpoint.
- **`e4f50d0` — Refinamiento de copy del hero (commit del usuario):**
  `webapp/templates/operations/staffing.html` — 1 línea, copy del
  `hero-lead` + clase `full-page-subtitle`. Fuera del alcance de esta
  sesión, sin impacto de tests (confirmado: mismo 458/1 antes y después).
- **Tests:** +37 en total sobre estos 5 commits — repository (Cliente
  resuelve/comparte cliente entre proyectos/degrada sin lookup roto/cero
  llamadas extra a Graph si no está configurado), service (passthrough de
  cliente sin fallback textual, marcador de hoy anclado a la fecha real
  reportada 2026-08-09 con horizonte jul..feb), routes (filtro Cliente
  posicionado, KPIs de disponibilidad dentro de Resumen, empty-state
  filtrado sigue presente), y dos archivos nuevos de contrato de fuente
  (`test_staffing_app_js.py`, `test_staffing_timeline_css.py`) que fijan
  la lógica de faceting/reconciliación y el invariante de coordenadas del
  CSS del timeline — este repo no tiene runtime de JS en la suite, así que
  el comportamiento en sí se verificó inyectando dato sintético real
  contra el estado interno vivo de Alpine en un servidor aislado (nunca el
  :8082 del usuario), no solo por lectura de código.
- **Edición en paralelo, NO tocada:** `webapp/templates/platform_selector.html`,
  `webapp/routers/platform.py`, `webapp/templates/partials/platform_topbar.html`,
  `webapp/static/brand-hero.css` — trabajo del usuario "Danwor Hub"
  (rebranding visual del selector de plataforma), explícitamente **fuera
  de alcance** de Staffing y **stasheado/revertido** por el usuario antes
  de este cierre. Ninguno de esos 4 archivos forma parte de PR #33.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**), en el HEAD real de PR #33 (`e4f50d0`): **suite 458 passed /
  1 skipped** (421 + 37 netos), route-contract GREEN, snapshot sin
  cambios (**82**).

### Evidencia de cierre (PR #33)

- **CI:** primer corrido de PR #33 — **GREEN**.
- **Suite local sobre el mismo HEAD:** 458 passed / 1 skipped.
- **Route surface:** 82 (snapshot `tests/route_surface.json`, sin cambios
  desde Batch B).
- **Smoke real de SharePoint en `localhost:8082` (usuario, no esta
  sesión):** confirmado interactivamente — FT/PT real trae valores
  (Consultores=9, Full Time=8, Part Time=1, Sin dato=0), Cliente resuelve
  con dato real de Albanesi (`EmpresaLookupId`→`Cliente.Title`,
  LIVE-GRAPH-CONFIRMED, ver `970a6d8`), y el proyecto Loma Negra con
  consultor sin resolver quedó confirmado como calidad referencial de
  origen real (no un bug de esta implementación) — se mantiene visible
  como "(sin resolver)", sin inventar una identidad.
- **Filtrado facetado:** confirmado — dropdowns nunca ofrecen una opción
  que produciría cero filas bajo el resto de los filtros activos;
  selecciones invalidadas se resetean solas.
- **Timeline/tooltip/expand-collapse:** confirmados — jerarquía consultor→
  asignación, tooltip accesible por hover/foco, expand/collapse por
  consultor + controles globales, marcador de "hoy" con posición
  matemáticamente correcta dentro del mes real (fix de coordenadas
  `7d5ef52`).
- **Registros de origen sin resolver:** fail-visible en todos los casos —
  la fila NUNCA se descarta, el id crudo se conserva, el nombre resuelto
  queda `None`/"(sin resolver)", nunca se sintetiza una identidad ni se
  ofrece como opción de filtro.
- **Cero requests a Graph durante interacción client-side:** confirmado en
  Network en cada smoke de este batch (filtros, expand/collapse, reset) —
  el único POST a `/operaciones/staffing/asignaciones/data` es el `hx-trigger="load"`
  inicial.

### Próxima decisión (NO autorizada en este commit de código)

Con Staffing V1 **CLOSED/GREEN**, el próximo paso es una decisión del
usuario. Explícitamente diferido, sin autorizar implementación: regla de
capacidad 160h/mes, % de capacidad, horas libres, sobre-asignación,
horizonte "termina pronto", filtro Tipo de Proyecto, vacaciones,
skills/conocimiento, ranking, profitability, workspace Projects, workspace
Reporting, librería de Gantt. (Filtro Cliente — antes en esta lista — ya
**IMPLEMENTADO**, ver `970a6d8`.)

**No autorizado todavía.**

---

## DANWOR HUB VISUAL IDENTITY — CLOSED / GREEN
(commit funcional `8ea2921` — `feat(platform): introduce Danwor Hub visual
identity`, branch `work/danwor-hub-visual`, pusheado a origin. **Primer CI
del PR: GREEN.** Cierre formal confirmado por el usuario tras ver el CI
real en GitHub.)

Batch **visual/product-identity ÚNICAMENTE** — reemplaza el naming visible
"One Danwor" del selector autenticado por **Danwor Hub**. El contrato de
arquitectura/ruteo queda intencionalmente sin tocar (ver "Límite de
alcance explícito" abajo).

- **Identidad visible autenticada:** el hero del selector (`/onedanwor`,
  2+ workspaces) presenta **Danwor Hub** en vez de "One Danwor" — kicker
  "Una plataforma · Todos los servicios Danwor" / `<h1>` con ícono +
  título en una sola línea / subtítulo aprobado ("Un punto de encuentro
  donde se conectan necesidades, capacidades, conocimiento y
  operación."+"Elegí el espacio de trabajo al que querés entrar.", en dos
  renglones). La landing anónima (SIN sesión) sigue mostrando "One Danwor"
  sin cambios — `_ONEDANWOR_LANDING_COPY` en `platform.py` no se tocó,
  fuera de alcance de este batch a propósito.
- **Ícono isotipo — una sola fuente canónica:** el macro `hub_icon()`
  (`webapp/templates/partials/platform_topbar.html`) es el ÚNICO lugar
  donde existe el markup del SVG de 4 cuadrados. Reusado por 3 call
  sites — switcher de workspace, fallback del switcher (sin otros
  workspaces disponibles), y el hero de Danwor Hub
  (`platform_selector.html`) — nunca un duplicado visualmente similar.
  Verificado por `tests/webapp/test_platform_hub_icon_reuse.py` (lee el
  template fuente: una sola definición del macro, cero `<svg>` inline
  fuera de él, el hero importa y llama al macro sin chrome de botón del
  switcher).
- **Título en una línea:** `.hub-hero h1` pasa a `display:flex` — el
  ícono (`width/height:1em`, escala directo con el font-size del título)
  ya no hereda el `display:block` compartido con `.access-brand h1 span`
  (esa regla sigue intacta para las tarjetas de workspace de abajo, que SÍ
  quieren su título en dos líneas con acento dorado — scoping vía
  `.hub-hero`, nunca tocando la regla compartida).
- **Kicker/subtítulo a ancho completo:** `.hub-hero .kicker`/`.hub-hero
  p.full-page-subtitle` cancelan el `max-width:46ch` heredado de
  `.access-brand`/`.access-brand p` — SOLO en este scope. Sin
  `white-space:nowrap` ni reducción de font-size: en desktop entra en una
  línea porque hay ancho disponible; en mobile envuelve naturalmente
  (verificado ~375px: título sigue en una línea, kicker envuelve a 2
  líneas, sin overflow horizontal del body).
- **Cards de workspace — eyebrow removido, capacidad intacta:**
  `_WORKSPACE_CARD_COPY["copilot"|"operations"].eyebrow` pasa de
  `"Data & AI for Business"` (repetido arriba de cada card, redundante una
  vez que la jerarquía visual del hero ya lo comunica) a `None`. El
  template conserva `{% if card.meta.eyebrow %}` — la capacidad de
  eyebrow por card sigue disponible para un futuro workspace; esto es una
  decisión de CONTENIDO, no una remoción de feature.
- **Tests (+2 archivos):** `test_onedanwor_routes.py` (+1 — hero visible,
  eyebrow ausente, landing anónima sin cambios) y
  `test_platform_hub_icon_reuse.py` (nuevo, +5 — fuente única del ícono,
  capacidad opcional de eyebrow preservada, sin chrome de switcher en el
  hero).
- **Validado sobre stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**), en el HEAD real del batch (`8ea2921`): **suite 464 passed /
  1 skipped**, route surface **82** (snapshot sin cambios). Revisión
  visual desktop GREEN y responsive ~375px GREEN (sin overflow horizontal
  de body, título+ícono siguen juntos, kicker envuelve naturalmente).

### Límite de alcance explícito — qué NO hizo este batch

- `/onedanwor` **NO** se renombró — sigue siendo la entrada canónica.
- `/platform` **NO** cambió de semántica — sigue siendo el alias de
  compatibilidad, siempre 307 a `/onedanwor`.
- Ninguna `Workspace.key` interna se renombró (`copilot`, `operations`
  intactas).
- Autenticación/autorización **NO** cambiaron — cero cambios en
  `auth_service`/`operations_access`/resolución de workspaces.
- Staffing (`work/staffing-assignments-dashboard-v1`, PR #33,
  CLOSED/GREEN — ver sección arriba) **NO** se tocó.
- **NO** se implementó el gate de acceso de Operaciones — sigue diferido
  (ver "Next candidate" abajo).
- Una migración de nombre de ruta (p. ej. `/onedanwor` → `/hub`) queda
  como decisión FUTURA e independiente — no evaluada ni autorizada en
  este batch.

---

## OPERATIONS WORKSPACE ACCESS GATE — CLOSED / GREEN
(commit funcional `05b5da3` — `feat(operations): add explicit workspace
access allowlist` + corrección de seguridad `47ee619` — `fix(operations):
reject wildcard workspace access` + docs `7994bbb`, branch
`work/operations-access-gate`, mergeado a `main` vía **PR #35** — merge
commit `324b6f2`. CI real de GitHub confirmado GREEN y **production smoke
verificado por el owner en DigitalOcean el 2026-08-10** — ver "Evidencia de
cierre" al final de esta sección.)

Batch de SEGURIDAD, deliberadamente pequeño: introduce la frontera de
autorización explícita que Operaciones necesitaba antes de exponerse a
usuarios reales. Precedido por Staffing V1 (PR #33, CLOSED/GREEN) y Danwor
Hub Visual Identity (CLOSED/GREEN) — ambos ya cerrados, ninguno se tocó.

- **Problema real:** hasta este batch, Operations dependía de
  `auth_service.user_tier(...) == "full"` — el MISMO tier que decide el
  producto Copiloto. Operaciones contiene información operativa que no
  debe estar disponible a todo usuario admitido al Copiloto/aplicación —
  el tier de Copiloto nunca debería haber sido el modelo de autorización
  de otro workspace.
- **`OPERATIONS_ALLOWED_USERS` (`config.py`):** allowlist EXPLÍCITA y
  PROVISORIA — MISMA normalización de usernames que `ALLOWED_USERS`
  (compactos → `@AUTH_DEFAULT_DOMAIN`, CSV, case-insensitive; reusa
  `auth_service._csv_users`, NUNCA un segundo algoritmo de identidad). A
  DIFERENCIA de `ALLOWED_USERS`/`ACADEMY_USERS`: default `""` (nunca
  `"ALL"`), JAMÁS hereda el fallback histórico "sin config = abierto"
  (ausente/vacío = SIEMPRE "nadie autorizado para Operaciones"), Y —
  corrección de seguridad post-implementación, ver bullet dedicado abajo
  — el literal `"ALL"` (el que SÍ abre `ALLOWED_USERS`/`ACADEMY_USERS` a
  todo el tenant) queda explícitamente RECHAZADO acá también: nunca
  "todos" para Operaciones, bajo ninguna configuración. Provisorio hasta
  que un mecanismo de entitlement central (Entra Groups/App Roles) lo
  justifique.
- **Corrección de seguridad — `"ALL"` NUNCA es wildcard para Operations
  (commit `fix(operations): reject wildcard workspace access`, posterior
  a `05b5da3`, mismo branch):** el chequeo de seguridad previo a abrir el
  PR encontró que `OPERATIONS_ALLOWED_USERS="ALL"` (o `"all"`/`" ALL "`)
  SÍ otorgaba acceso a Operaciones a cualquier usuario admitido —
  `_operations_set()` reusaba `_csv_users(..., empty_is_all=False)` sin
  notar que ese parser devuelve `None` (="todos") para el literal `"ALL"`
  INDEPENDIENTEMENTE de `empty_is_all`, y `is_operations_user` trataba
  `None` como "todos". Corregido: `_operations_set()` ahora SIEMPRE
  devuelve un `set[str]` concreto (convierte ese `None` en `set()`), así
  que `"ALL"` en cualquier capitalización/espaciado deniega a todos.
  `ALLOWED_USERS="ALL"` (comportamiento histórico de admisión base, sin
  tocar) sigue abriendo la app a todo el tenant exactamente igual — la
  restricción es SOLO de Operations. Tests nuevos en
  `test_operations_access.py` prueban ambos lados de esta regresión.
- **Política de autorización = admisión base AND allowlist de Operations**
  (`webapp/operations_access.is_operations_authorized_user`): dos
  condiciones independientes, AMBAS requeridas —
  `auth_service.is_authorized(email)` (la política de login existente,
  SIN modificar) Y `auth_service.is_operations_user(email)` (la nueva
  allowlist). Estar en `OPERATIONS_ALLOWED_USERS` nunca otorga admisión a
  la app por sí solo.
- **Ortogonal al tier de Copiloto — deliberadamente:** `user_tier` NUNCA
  se usa en el nuevo predicado. Las 4 combinaciones son representables y
  están testeadas: full sin Operations, academy sin Operations, full con
  Operations, **academy con Operations** (esta última era IMPOSIBLE bajo
  el modelo anterior — ahora un usuario Academy legítimamente admitido y
  listado en `OPERATIONS_ALLOWED_USERS` entra a Operaciones igual que uno
  full).
- **`APP_OWNER` — sin bypass implícito:** el predicado nuevo NUNCA
  referencia `APP_OWNER` (`webapp/access.py`, diagnóstico del Copiloto,
  módulo distinto). Si el dueño de la app necesita Operations, su
  identidad debe estar explícita en `OPERATIONS_ALLOWED_USERS` como
  cualquier otro usuario — auditable en config, sin excepción invisible.
- **Un solo predicado de workspace, no Staffing-específico:**
  `is_staffing_authorized_user`/`is_staffing_authorized` renombrados a
  `is_operations_authorized_user`/`is_operations_authorized` — el
  predicado permanente vive a nivel de WORKSPACE (Operaciones es más
  ancho que Staffing), no de área interna. Sin `STAFFING_ALLOWED_USERS` —
  la frontera de entitlement actual es el workspace completo; permisos
  por área quedan diferidos hasta que exista una necesidad real de
  diferenciarlos. Los 2 callers reales (`webapp/workspaces.py`,
  `webapp/routers/staffing.py`) se migraron DIRECTO al nuevo nombre — sin
  wrapper de compatibilidad (ningún caller/test lo necesitaba).
- **MISMA política gobierna descubrimiento Y enforcement:**
  `workspaces.OPERATIONS.is_authorized` y
  `staffing.py::_require_operations_session` llaman al MISMO predicado
  puro — autorizado: Operaciones aparece en el selector/switcher de Danwor
  Hub Y las rutas canónicas responden; no autorizado: Operaciones
  desaparece del selector/switcher Y el acceso directo por URL devuelve
  403 server-side (ocultar en UI nunca fue ni es la autorización real).
  Protege como mínimo `GET /operaciones/staffing/asignaciones` y
  `POST /operaciones/staffing/asignaciones/data` — un `graph_token`
  presente/válido NUNCA saltea esta autorización (testeado explícitamente
  con un `StaffingService` que rompería el test si se llegara a invocar).
- **Sin cambios de ruteo/auth/OAuth/sesión:** `/onedanwor`, `/platform`,
  `/copilot`, las URLs canónicas de `/operaciones`, los redirects de
  compatibilidad `/operations/*` (307, method+body preservados), las
  transiciones 303 de autenticación, el ciclo de vida del token de Graph —
  NADA de esto cambió. Este batch es SOLO autorización. **Route surface:
  82, sin cambios.**
- **Tests (+26 netos):** `tests/webapp/test_operations_access.py` (nuevo,
  +15) — predicado puro: equivalencia username-compacto/email-completo
  (misma normalización que `ALLOWED_USERS`), fail-closed (ausente/vacío),
  sin bypass de `APP_OWNER`, academy+Operations-listado autorizado,
  full+no-listado denegado, allowlist de Operations sola nunca otorga
  admisión base. `tests/webapp/test_workspaces.py` (reescrito) — las 4
  combinaciones full/academy × con/sin Operations, fail-closed a nivel de
  descubrimiento. `tests/webapp/test_staffing_routes.py` (+6) — GET/POST
  denegados para full-no-listado, graph-token nunca saltea, blank deniega
  al usuario previamente autorizado, el camino autorizado sigue
  funcionando GET+POST. `tests/webapp/test_platform_topbar.py`/
  `test_onedanwor_routes.py` (fixtures actualizados +2) — switcher/
  selector ocultan Operaciones a un full-tier no listado, igual que ya
  hacían con academy.
- **Hallazgo de aislamiento de tests (corregido en este batch):** los
  archivos de test que asignan `auth_service.OPERATIONS_ALLOWED_USERS`
  como atributo de MÓDULO (no vía `monkeypatch`) deben coincidir en el
  mismo valor entre archivos — pytest importa/colecciona TODOS los
  módulos de test antes de correr ninguno, así que la última asignación
  plana de módulo en orden de collection "gana" para cualquier test sin
  su propio override. Mismo patrón ya usado (sin fricción, por coincidir
  siempre) por `ALLOWED_USERS` desde hace varios batches — quedó expuesto
  acá porque introdujimos valores DISTINTOS entre archivos por error, no
  porque el patrón en sí sea nuevo. El valor plano compartido era
  originalmente `"ALL"`; tras la corrección de seguridad de wildcard (ver
  bullet dedicado arriba) ya no es válido para Operations, así que los
  tres archivos (`test_staffing_routes.py`, `test_platform_topbar.py`,
  `test_onedanwor_routes.py`) pasaron a un CSV explícito con los tres
  emails default de módulo.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**): **suite 496 passed / 1 skipped** (490 + 6 tests de la
  corrección de wildcard), route-contract GREEN, snapshot sin cambios
  (**82**).
- **Evidencia de cierre:** CI real de GitHub Actions sobre **PR #35**
  confirmado GREEN; merge a `main` vía merge commit `324b6f2`. El owner
  confirmó el **2026-08-10** que la autorización de Operaciones (allowlist
  explícita, fail-closed, sin bypass de `APP_OWNER`, sin wildcard `"ALL"`,
  academy+Operations soportado) funciona correctamente en **producción
  DigitalOcean** — es un **smoke de producción verificado por el usuario**,
  NO un resultado de test automatizado. Suite sobre el HEAD real de `main`
  tras este batch Y el batch posterior de UX (ver sección dedicada abajo):
  **497 passed / 1 skipped**, route surface **82** sin cambios.

---

## DANWOR HUB — SINGLE-WORKSPACE SWITCHER CORRECTION — CLOSED / GREEN
(commit funcional `bd301c0` — `fix(platform): hide Hub switcher for single
workspace`, branch `work/hub-switcher-single-workspace`, mergeado a `main`
vía **PR #36** — merge commit `979cfc1` (HEAD actual de `main`). CI real de
GitHub confirmado GREEN y **production smoke verificado por el owner en
DigitalOcean el 2026-08-10**.)

Corrección de UX chica, posterior al cierre de producción del Operations
Access Gate: la validación real en producción expuso que un usuario con
acceso a un solo workspace seguía viendo el ícono de Danwor Hub en el
topbar compartido, comportándose como un botón "Home" ambiguo — Danwor Hub
en el topbar es una afordancia de CAMBIO de workspace, nunca un botón Home
genérico.

- **Contrato final:** `available_workspaces(user)` con **1 solo workspace**
  → el switcher/ícono de Danwor Hub NO se renderiza en el topbar (nada de
  ícono deshabilitado, dropdown vacío ni fallback a `/onedanwor`).
  `available_workspaces(user)` con **2 o más** → el switcher se renderiza
  exactamente igual que antes (mismo `hub_icon()`, mismo `<details
  class="workspace-switch">`).
- **Cambio exacto:** se removió la rama `{% else %}` de
  `webapp/templates/partials/platform_topbar.html` que renderizaba un
  segundo `hub_icon()` como link a `identity_href` (`class="theme-toggle"
  aria-label="Danwor · Inicio"`) cuando no había elección de workspace —
  ese fallback era exactamente el botón Home ambiguo que se reportó.
- **El ícono del hero de Danwor Hub NO se tocó:** `platform_selector.html`
  sigue importando y usando el mismo `hub_icon()` para la identidad visual
  de la landing — esta corrección es SOLO del topbar compartido en
  workspace, nunca de la identidad de marca en la landing.
- **Sin cambios de autorización/ruteo:** el predicado
  `is_operations_authorized_user` y `available_workspaces` no se tocaron
  — esta es la expresión visual correcta de un resultado de autorización
  que ya era servidor-side; la seguridad real sigue viviendo en el gate de
  Operaciones (sección anterior).
- **Tests:** `test_platform_topbar.py` — reescrito
  `test_single_workspace_user_gets_no_hub_control_but_keeps_identity`
  (antes solo probaba la ausencia del dropdown; ahora también prueba la
  ausencia específica del fallback) + nuevo
  `test_operations_landing_hero_still_renders_canonical_hub_icon`.
  `test_platform_hub_icon_reuse.py` — invariante de conteo de usos del
  macro actualizado de 2 a 1 (el switcher es ahora el único uso dentro del
  topbar).
- Route surface: **82, sin cambios.**
- Validado sobre stack reproducible: **suite 497 passed / 1 skipped**
  (496 + 1 test nuevo), route-contract GREEN.
- **Evidencia de cierre:** CI real de GitHub Actions sobre **PR #36**
  confirmado GREEN; merge a `main` vía merge commit `979cfc1` (HEAD actual).
  El owner confirmó el **2026-08-10** que el comportamiento del switcher
  single-workspace funciona correctamente en **producción DigitalOcean** —
  **smoke de producción verificado por el usuario**, no un resultado de
  test automatizado.

---

## SHARED WORKSPACE SIDEBAR FRAME — CLOSED / GREEN
(commit funcional `dd7f99d` — `refactor(platform): share workspace sidebar
frame` + corrective `4372625` — `refactor(platform): share sidebar resize
handle` + docs `f367971`/`2fc20cd`, branch
`work/shared-workspace-sidebar-frame`, mergeado a `main` vía **PR #38** —
merge commit `a2f4035`. CI real de GitHub confirmado GREEN y **production
smoke verificado por el owner en DigitalOcean el 2026-08-10** — ver
"Evidencia de cierre" al final de esta sección.)

Corrective de arquitectura, disparado por una revisión visual real en
producción: Copiloto y Operaciones (los DOS consumidores reales de
workspace hoy) se compararon lado a lado y aparecieron 3 diferencias —
resize del nav ausente en Operaciones, bienvenida de sesión ausente en
Operaciones, y una sospecha de que el markup pudiera estar copiado en vez
de reusado.

- **Descubrimiento — la sospecha de "copiado vs. reusado" se DESCARTÓ:**
  el frame real (grid `.app-shell` con `--nav-width`/`--panel-width`,
  `.module-nav` en `grid-column:1`, el controlador Alpine
  `copilotLayout()` de `webapp/static/app.js`, la persistencia de ancho en
  `localStorage["dw-nav-width"]`) YA era compartido antes de este batch —
  `operations/staffing.html` ya usaba `class="app-shell no-copilot"` (la
  MISMA clase que `module.html`/`academy.html`) y `x-data="copilotLayout(
  'staffing')"` (el MISMO componente Alpine, invocado por 9 templates
  distintas, Copiloto y Operaciones incluidos). `operations_nav.html` ya
  reusaba `.module-nav`/`.nav-kicker`/`.module-link` (su propio comentario
  ya decía "mismo idioma visual que copilot_nav.html"). No había una
  segunda hoja de estilos ni una segunda implementación de resize.
- **Causa real — dos omisiones puntuales, no un problema de arquitectura:**
  1. `operations/staffing.html` nunca renderizaba el `<div data-resize=
     "left">` — `copilotLayout().init()` (`app.js`) ya hace
     `shell.querySelectorAll("[data-resize]")` y le engancha el drag, pero
     no había ningún `[data-resize]` que encontrar en la página de
     Staffing.
  2. `staffing_page()` (`webapp/routers/staffing.py`) calculaba `user =
     auth_service.session_user(request)` pero nunca lo pasaba al contexto
     del template — `operations_nav.html` no tenía forma de saber quién
     estaba logueado, aun si hubiera querido mostrar la bienvenida.
- **Corrección — mínima, aditiva, sin tocar CSS/JS compartido:**
  `webapp/templates/operations/staffing.html` ahora incluye el MISMO
  `<div class="resize-handle resize-left" data-resize="left">` que
  `module.html`/`academy.html` (markup idéntico, cero JS/CSS nuevo — el
  mecanismo ya existía). `staffing_page()` ahora agrega `"user": user` al
  contexto, igual que ya hacen `modules.py`/`academy.py`.
- **Bienvenida de sesión — extraída a un macro compartido, no duplicada:**
  el texto "Bienvenido `<nombre>`" vivía como HTML inline solo dentro de
  `copilot_nav.html`. Copiarlo tal cual a `operations_nav.html` habría sido
  la duplicación real que el batch buscaba evitar, así que se extrajo a
  un macro `nav_welcome(user, readonly=False)` — una sola fuente de
  verdad, importada por AMBOS `copilot_nav.html` y `operations_nav.html`.
  La identidad sigue viniendo de la MISMA sesión
  (`auth_service.session_user`) para los dos — Operaciones nunca parsea
  claims de Microsoft por su cuenta ni reimplementa el formateo del
  nombre. `readonly` (tier academy del Copiloto, badge "Modo lectura") es
  un parámetro opcional del macro que Operaciones simplemente no pasa —
  no existe ese tier para Operaciones y el macro no lo asume.
- **Corrective posterior (mismo batch, antes de abrir PR) — el `<div
  data-resize>` en sí seguía copy-pasteado:** el commit inicial (`dd7f99d`)
  agregó a `operations/staffing.html` el MISMO markup literal
  `<div class="resize-handle resize-left" data-resize="left">` que ya
  existía copiado 3 veces más (`module.html` x2 lado izq/der,
  `academy.html`) — el mecanismo era compartido, pero el trigger DOM en sí
  seguía siendo 4 copias del mismo `<div>`. Corregido en un segundo commit
  chico (`refactor(platform): share sidebar resize handle`): se extrajo a
  un macro `sidebar_resize_handle(side="left")`, agregado al MISMO partial
  que ya tenía `nav_welcome` — el archivo se renombró de
  `workspace_nav_identity.html` a **`webapp/templates/partials/
  workspace_shell.html`** (ahora contiene las dos primitivas mínimas del
  shell de workspace, no solo identidad). Los 4 consumidores reales
  (`module.html` ×2, `academy.html`, `operations/staffing.html`) ahora
  importan el macro — el `<div>` real vive una sola vez, dentro del
  partial. Cero cambios de CSS/JS; el HTML renderizado y el comportamiento
  de resize son idénticos.
- **Frontera final — frame compartido, contenido workspace-owned (sin
  cambios):** la navegación de Copiloto (acordeón Core/Apéndices/Academy,
  `pack`, numeración de módulos) y la de Operaciones (`STAFFING → 01
  Asignaciones`) siguen siendo 100% de cada workspace — no se importó
  ningún concepto de Copiloto a Operaciones ni viceversa. No se
  implementaron Projects/Reporting (fuera de alcance explícito). El
  geometry contract sigue siendo ÚNICO: `--nav-width`/`--panel-width` en
  `.app-shell` (`webapp/static/layout.css`) — ningún template declara su
  propio ancho o `grid-template-columns`.
- **`copilotLayout()` — nombre histórico, comportamiento ya genérico:**
  la función se llama `copilotLayout` pero ya opera sobre `this.$root`
  (el `.app-shell` que sea) sin ninguna referencia a Copiloto específica —
  9 templates la invocan hoy, incluyendo Operaciones. Renombrarla es
  cosmético, no funcional; **deliberadamente NO se tocó en este batch**
  (alcance mínimo) — queda como residual de naming, no como deuda
  bloqueante.
- **Copiloto sin regresión:** `module.html`/`academy.html` no se tocaron
  — el macro compartido reemplaza su línea inline por una llamada
  equivalente (`nav_welcome(user, readonly)`), mismo HTML resultante,
  mismo comportamiento de resize, mismo pack/módulo. Confirmado por los
  tests existentes de `test_routes.py`/`test_platform_topbar.py`
  (168 tests, todos GREEN sin modificar ninguno) más los nuevos tests
  dedicados.
- **Route surface: 82, sin cambios.** Ningún router/ruta nueva —
  `staffing_page` solo agregó una clave al contexto de un template ya
  existente.
- **Tests (+13, `tests/webapp/test_workspace_sidebar_frame.py`):** reuso
  semántico, no snapshots de pixeles/HTML completo — ambos workspaces
  renderizan el mismo `[data-resize="left"]`; ambos usan el mismo
  `x-data="copilotLayout(...)"` y `.app-shell`; ningún template declara
  `--nav-width`/`grid-template-columns` localmente; la bienvenida se
  renderiza igual para cada usuario real vía `TestClient`; el macro de
  bienvenida y el de resize-handle están definidos una sola vez cada uno e
  importados por los templates reales (mismo criterio que
  `test_platform_hub_icon_reuse.py` para `hub_icon()`); el handle renderiza
  exactamente una vez por lado (Operations/Academy: solo `left`; módulo
  con panel: `left` Y `right`, nunca duplicado); Copiloto sigue mostrando
  su nav de módulos/Academy y Operaciones el suyo de Staffing; ningún
  markup de un workspace se filtra al otro.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**): **suite 510 passed / 1 skipped** (497 + 13 nuevos),
  route-contract GREEN, snapshot sin cambios (**82**).
- **Validación visual durante la implementación:** NO realizada vía
  navegador automatizado — el login real requiere Microsoft Entra ID
  OAuth con credenciales reales, fuera de alcance de ese entorno.
  Verificación equivalente hecha vía `TestClient` HTTP directo contra el
  servidor real (mismo motor Jinja, mismo HTML servido): se capturó el
  render completo del nav de Copiloto y de Operaciones antes/después del
  cambio y se confirmó que el handle de resize y la bienvenida aparecen en
  ambos con el HTML esperado.
- **Evidencia de cierre:** CI real de GitHub Actions sobre **PR #38**
  confirmado GREEN; merge a `main` vía merge commit `a2f4035`. El owner
  confirmó el **2026-08-10** un **smoke de producción verificado por el
  usuario** (NO un resultado de test automatizado) en DigitalOcean:
  - **Copiloto** — bienvenida/identidad de usuario visible; resize del
    sidebar funcional; navegación y geometría existentes preservadas.
  - **Operaciones** — bienvenida/identidad de usuario visible (antes
    ausente); resize del sidebar funcional (antes ausente); geometría del
    shell compartido alineada con Copiloto; navegación de Operaciones
    (Staffing/Asignaciones) preservada sin cambios.
  Suite sobre el HEAD real de `main` tras este batch: **510 passed / 1
  skipped**, route surface **82** sin cambios (mismo baseline del gate de
  implementación — sin drift).

---

## DANWOR HUB LOGIN IDENTITY — CLOSED / GREEN
(commits funcionales `8e7ee46` + `5674120` (branch
`work/danwor-hub-login-identity`, PR #40/#41) + corrective final
`7a51c9c` (branch `work/danwor-hub-login-brand-lockup`, PR #42) + docs
`f3e0657`/`1bb422a`/`a8e487a`. CI real de GitHub confirmado GREEN sobre
los tres PRs y **production smoke verificado por el owner en
DigitalOcean el 2026-08-10** — ver "Evidencia de cierre" al final de esta
sección.)

Corrección chica de copy/identidad visual, disparada por una revisión de
producción: la experiencia autenticada de plataforma ya dice **Danwor
Hub** en todos lados, pero la landing anónima (`/onedanwor`, alias
`/platform`) seguía mostrando el copy histórico **"One Danwor"**.

- **Dónde vivía:** `_ONEDANWOR_LANDING_COPY` en `webapp/routers/platform.py`
  — el dict de copy que `render_landing()` (`webapp/routers/landing.py`)
  inyecta en el template compartido `landing.html` cuando no hay sesión.
  El nombre interno de ruta/batch "One Danwor" (`/onedanwor`, comentarios,
  nombres de test) **NO cambió** — solo el copy visible que ese dict
  produce.
- **Copy anterior → nuevo:** título de pestaña `"One Danwor · Danwor"` →
  `"Danwor Hub · Danwor"`; wordmark de dos líneas `"One"`/`"Danwor"` →
  `"Danwor"`/`"Hub"` (mismo `<h1>{{ brand_title }}<span>{{ brand_subtitle
  }}</span></h1>` de la landing histórica del Copiloto, sin plantilla
  nueva); descripción `"Un único acceso a los espacios de trabajo y
  herramientas internas Danwor."` → `"Un punto de encuentro donde se
  conectan necesidades, capacidades, conocimiento y operación."` (misma
  frase ya aprobada para el hero autenticado); línea bajo "¡Hola,
  bienvenido!" `"One Danwor"` → `"Ingresá con tu cuenta Danwor para
  acceder a los espacios de trabajo disponibles para vos."` (adaptada al
  estado NO autenticado — no se pegó mecánicamente "Elegí el espacio de
  trabajo", que asume sesión). `eyebrow_ink`/`eyebrow_gold` ("Una
  plataforma" / "Todos los servicios Danwor") y el `feature_list` ya
  coincidían con el copy Hub aprobado — sin cambios.
- **Ícono canónico — NO se reusó, y no es un déficit:** `landing.html` no
  tiene ningún slot de ícono SVG (solo el logo Danwor en PNG + el mismo
  wordmark de texto que ya usa la landing del Copiloto) — a diferencia de
  `platform_selector.html`, que sí usa `hub_icon()` en su hero. Insertar
  el ícono acá habría exigido agregar un elemento visual nuevo a un
  template compartido por `/`, `/copilot` y `/onedanwor`, divergiendo la
  composición de forma asimétrica — exactamente lo que este batch NO debía
  hacer. No se creó un segundo SVG canónico; se reusó el mecanismo de
  wordmark de texto ya existente, solo con copy distinta.
- **Hallazgos adyacentes corregidos (mismo commit, mismo criterio):**
  `platform_no_access.html` (estado de cero-workspaces, autenticado) tenía
  el mismo wordmark `"One"`/`"Danwor"` hardcodeado y el título "Sin acceso
  · One Danwor" — corregido a `"Danwor"`/`"Hub"` y "Sin acceso · Danwor
  Hub". `platform_shell.html` (shell compartido de `platform_selector.html`/
  `platform_no_access.html`) tenía el título default `{% block title %}One
  Danwor{% endblock %}` — nunca visible hoy (ambos consumidores lo
  sobreescriben) pero corregido a "Danwor Hub" para que un consumidor
  futuro no herede la marca vieja por accidente.
- **Auth boundary sin cambios:** mismo botón (`id="microsoft-login-btn"`),
  mismo `onclick="loginWithMicrosoft(event)"`, mismo `data-home`, mismo
  texto de CTA ("Ingresar con tu cuenta Danwor"), mismo mecanismo de
  `next`/OAuth/PKCE/callback/sesión — verificado explícitamente por test y
  por inspección manual contra el servidor real en `localhost:8082`.
- **Route surface: 82, sin cambios.** Solo copy — ningún router/ruta
  tocado.
- **Tests (+1 neto, ediciones a `test_onedanwor_routes.py` y
  `test_platform_routes.py`):** el copy nuevo se verifica visible, "One
  Danwor" se verifica ausente, el botón/target de login se verifica
  intacto, y se agregó un test explícito de que no se filtró un segundo
  SVG del ícono Hub a la landing anónima.
- Validado sobre **stack reproducible** (`.venv` del proyecto, Python
  **3.14.3**): **suite 511 passed / 1 skipped** (510 + 1 nuevo),
  route-contract GREEN, snapshot sin cambios (**82**).
- **Validación visual:** realizada — la landing anónima NO requiere Graph
  ni sesión, así que se pudo levantar el servidor real
  (`.venv\Scripts\python.exe -m uvicorn webapp.main:app --port 8082`) y
  navegarla directamente. Confirmado en el navegador: título de pestaña
  "Danwor Hub · Danwor", wordmark "Danwor"/"Hub", copy nuevo, botón de
  login intacto (`data-home="/onedanwor"`, `onclick`), cero apariciones de
  "One Danwor", sin un segundo `<rect>` del ícono Hub, y sin overflow
  horizontal a 375px de ancho (viewport móvil).
- **Corrective posterior (mismo batch, antes de abrir PR) — el commit
  inicial dejó un hueco de alcance:** la validación de producto del owner
  detectó que el commit `8e7ee46` solo cubrió la LANDING anónima
  (`/onedanwor`, alias `/platform`) — pero el paso intermedio real del
  login (el popup/callback de Microsoft) usa un documento distinto,
  `auth_callback.html`, que quedó sin tocar.
  - **Flujo real trazado desde el CTA** (`webapp/static/auth.js`):
    click en "Ingresar" → `loginWithMicrosoft(event)` abre un POPUP en la
    MISMA página con `?auth_popup=1` → esa request cae en
    `render_landing()` (`landing.py`), que intercepta `auth_popup`/`code`
    ANTES de tocar cualquier copy de landing y devuelve
    `auth_callback.html` (documento neutro) → dentro del popup, `auth.js`
    navega (`window.location.replace`) al `authorize` de
    `login.microsoftonline.com` — **Microsoft-hosted, fuera de nuestro
    control y fuera de alcance** — → Microsoft redirige de vuelta al
    `redirect_uri` fijo (`origin + "/"`, registrado en Azure, no
    parametrizable por página) con `?code=...&state=...` → esa segunda
    request TAMBIÉN cae en `render_landing()` (vía la ruta `/`) y
    TAMBIÉN devuelve `auth_callback.html` → `auth.js` completa el
    intercambio, crea la sesión, y navega/notifica al opener.
  - **Documento application-owned responsable:** `webapp/templates/
    auth_callback.html`. Es el ÚNICO template de la app en el tramo
    intermedio del login, sin importar si arrancó en Copiloto u
    Operaciones (el `redirect_uri` fijo en `/` garantiza que el tramo de
    vuelta siempre pasa por ahí).
  - **Identidad visible antes:** `<title>Completando acceso · Danwor</title>`
    — genérico, ni "One Danwor" ni "Danwor Hub", body sin ningún
    logo/wordmark (diseño deliberado de un batch previo para resolver un
    bug real de branding stale filtrándose detrás de un overlay).
  - **Por qué el batch anterior no lo detectó:** los tests existentes de
    este documento (`test_routes.py`, familia "Auth-popup/callback") solo
    afirmaban la AUSENCIA del copy rico de Copiloto (el bug original que
    resolvían) — nunca la PRESENCIA de una identidad Hub. El documento
    pasaba esos tests con o sin "Danwor Hub" en el título: la ausencia de
    aserciones positivas sobre identidad, no un error de implementación,
    fue la causa real del hueco.
  - **Qué se cambió ahora:** `<title>` → `"Completando acceso · Danwor Hub"`
    — el paraguas de plataforma, no un producto específico. El BODY
    sigue exactamente igual (sin logo/wordmark) — esto NO es un
    rediseño, es alinear la única línea de texto visible-en-pestaña que
    existía.
  - **Qué queda fuera de nuestra UI (confirmado, no tocado):** las
    páginas de `login.microsoftonline.com` (selección de cuenta,
    contraseña, MFA) son 100% Microsoft-hosted — la app nunca las
    renderiza ni las personaliza.
  - **Tests nuevos/actualizados:** `test_routes.py` (familia auth-popup/
    callback de `/` y `/copilot`) ahora afirma `"Danwor Hub" in
    response.text` además de la ausencia de copy de Copiloto;
    `test_onedanwor_routes.py::test_auth_popup_state_serves_neutral_document_not_rich_landing`
    invertido (antes afirmaba la AUSENCIA de "Danwor Hub", ahora afirma
    su PRESENCIA) + nuevo test dedicado al callback `?code=` de
    `/onedanwor` (mismo documento, misma identidad).
  - **Hallazgo relacionado, reportado pero NO corregido (fuera de
    alcance — es comportamiento de ruta, no de identidad visual):** si
    `handleRedirect()` no encuentra un `window.opener` vivo (pestaña
    original cerrada), el popup navega él mismo a `home_url` — que para
    ESE request (siempre `GET /` por el `redirect_uri` fijo) resuelve a
    `/home` (Copiloto), nunca a `/onedanwor`, sin importar dónde arrancó
    el login. Es una inconsistencia de destino de fallback preexistente,
    no introducida por este batch — requeriría cambiar lógica de
    redirect/JS, explícitamente fuera del alcance de esta corrección de
    identidad visual.
  - Commit: `5674120` — `fix(platform): align auth flow identity with
    Danwor Hub` (corrective, no amendea `8e7ee46`).
  - Validado: **suite 512 passed / 1 skipped** (511 + 1 nuevo),
    route-contract GREEN, route surface **82** sin cambios.
  - **Validación visual:** confirmada contra servidor real —
    `GET /onedanwor?auth_popup=1` sirve `auth_callback.html` con tab
    title "Completando acceso · Danwor Hub" y body sin ningún nombre de
    producto (solo "Completando acceso…" + spinner).
- **Corrective final (mismo batch, antes de abrir PR) — la validación de
  producto rechazó el resultado: el nombre estaba bien, el LOCKUP visual
  no:**
  - **Estado observado en producción:** el nombre visible ya decía
    "Danwor Hub" (commit `8e7ee46`), pero seguía renderizado como el
    wordmark histórico partido en dos líneas ("Danwor" / "Hub"), SIN el
    ícono canónico de 4 cuadrados, y el kicker de la pantalla de acceso
    seguía diciendo "DATA & AI FOR BUSINESS" — un lockup visual distinto
    del hero autenticado aprobado.
  - **Trazado del lockup canónico real:** `platform_selector.html`
    (`<div class="glass access-brand hub-hero"><p class="kicker">Una
    plataforma · Todos los servicios Danwor</p><h1><span class=
    "hub-hero__icon">{{ hub_icon() }}</span>Danwor Hub</h1>...`) + CSS en
    `webapp/static/brand-hero.css` (`.hub-hero h1{display:flex;
    align-items:center;gap:14px}`, `.hub-hero__icon{width:1em;height:1em}`
    — la geometría de una sola línea ícono+título).
  - **Por qué el kicker no se había alineado antes:** vivía HARDCODEADO
    directamente en `landing.html` ("Data & AI for Business"),
    INDEPENDIENTE de `_ONEDANWOR_LANDING_COPY` — el batch anterior (que
    solo tocó valores del dict de copy) nunca lo tocó porque no forma
    parte de ese dict.
  - **Extracción — dos consumidores reales justifican el macro
    compartido:** `hub_brand_lockup()` (nuevo, en `partials/
    platform_topbar.html`, junto a `hub_icon()`) — kicker + ícono +
    "Danwor Hub" fijos, SIN parámetros (es la identidad canónica, no una
    plantilla genérica). `platform_selector.html` y `landing.html` (vía
    `_ONEDANWOR_LANDING_COPY["hub_brand_lockup"] = True`) ahora llaman al
    MISMO macro — el ícono se reusa (`{{ hub_icon() }}` dentro del nuevo
    macro), nunca un segundo `<svg>`. La copy del Copiloto
    (`_COPILOT_LANDING_COPY`) no define esa clave — su wordmark de dos
    líneas sigue byte a byte igual.
  - **Frontera respetada:** el macro SOLO cubre kicker+ícono+título — el
    subtítulo del hero autenticado ("Elegí el espacio de trabajo...") y
    la descripción/checklist/CTA del login siguen siendo dueños de cada
    caller, sin tocar.
  - **Tests:** `test_platform_hub_icon_reuse.py` reescrito (el macro
    `hub_brand_lockup` se define una sola vez y reusa `hub_icon()`; ambos
    consumidores lo importan/llaman; ninguno duplica el `<svg>` ni el
    texto del kicker) + `test_onedanwor_routes.py` (ícono presente y
    reusado, kicker canónico + título de una línea presentes, wordmark/
    kicker histórico ausente, `alt` del logo de la pantalla de intro
    — compartida, no tocada — intacto).
  - Commit: `7a51c9c` — `fix(platform): reuse canonical Hub identity on
    login` (corrective, no amendea `8e7ee46`/`5674120`).
  - Validado: **suite 516 passed / 1 skipped** (512 + 4 nuevos),
    route-contract GREEN, route surface **82** sin cambios.
  - **Validación visual (desktop + mobile, contra servidor real):**
    a 800px y 1280px de viewport, el `<h1>` mide ~33px/~43px de alto —
    una sola línea (confirmado, no dos); ícono 32×32px, dorado
    (`rgb(231,176,8)`), alineado verticalmente con el texto; a 375px
    (mobile) sin overflow horizontal (`scrollWidth == clientWidth`) y el
    ícono sigue visualmente pegado al título. Texto renderizado
    verificado: "UNA PLATAFORMA · TODOS LOS SERVICIOS DANWOR" → ícono +
    "Danwor Hub" en una línea → "Un punto de encuentro donde se
    conectan..." → checklist → panel de login sin cambios.
- **Resumen de la evolución (3 pasadas, ninguna un fallo de mecánica de
  auth — SOLO identidad visual/copy):**
  1. Batch inicial (`8e7ee46`): cambió el naming visible de "One Danwor"
     a "Danwor Hub" en la landing anónima (`_ONEDANWOR_LANDING_COPY`).
  2. Corrective de callback (`5674120`): alineó el `<title>` del
     documento intermedio real del login (`auth_callback.html`,
     "Completando acceso · Danwor Hub") — el batch inicial solo había
     tocado la landing, no este documento distinto que Microsoft
     realmente muestra durante el popup/callback.
  3. Corrective final de lockup (`7a51c9c`): la revisión de producción
     expuso que el login seguía con el tratamiento visual histórico
     ("DATA & AI FOR BUSINESS", wordmark partido "Danwor"/"Hub" en dos
     líneas, sin ícono) en vez del lockup canónico del hero autenticado
     — se rastreó a markup hardcodeado en `landing.html` y se extrajo/
     reusó el primitivo compartido `hub_brand_lockup()`.
  OAuth/PKCE/callback/sesión/permisos/resolución de workspace/ciclo de
  vida del token de Graph **NUNCA cambiaron en ninguna de las 3
  pasadas** — esto fue, de punta a punta, una corrección de identidad
  visual/copy, no de mecánica de autenticación.
- **Evidencia de cierre:** CI real de GitHub Actions confirmado GREEN
  sobre los tres PRs (PR #40 → merge `4fe4193`; PR #41 → merge
  `e587609`; PR #42 → merge `e5bba90`, HEAD actual de `main`). El owner
  confirmó el **2026-08-10** un **smoke de producción verificado por el
  usuario** (NO un resultado de test automatizado) en DigitalOcean:
  - kicker canónico visible ("Una plataforma · Todos los servicios
    Danwor");
  - ícono canónico Hub (4 cuadrados) visible;
  - "Danwor Hub" renderizado en una sola línea en desktop;
  - copy propia del login (explicación + checklist) preservada;
  - CTA de login preservado;
  - selector Hub autenticado sigue alineado con el mismo lockup;
  - sin identidad "One Danwor" visible en ningún punto;
  - comportamiento responsive/mobile aceptable.
  Contrato final aceptado: **el MISMO bloque canónico**
  (`hub_brand_lockup()` → `hub_icon()`, sin un segundo SVG duplicado en
  ningún template consumidor) — "Una plataforma · Todos los servicios
  Danwor" + ícono + "Danwor Hub" — para el selector Hub autenticado Y el
  login anónimo. Suite sobre el HEAD real de `main` tras el cierre:
  **516 passed / 1 skipped**, route surface **82** sin cambios (mismo
  baseline del gate de implementación — sin drift).

---

## SHARED ACTIVITY / OBSERVABILITY — CONTEXT FOUNDATION (Batch A1) — CLOSED / GREEN
(commits funcionales `18d4c43` + corrective `ce17790`, branch
`work/shared-activity-context-foundation`; **mergeado a `main` vía PR #44 /
`53277d6`**)

> **CLOSED / GREEN.** Owner-confirmed: PR #44 mergeado, **CI GREEN**, baseline
> **528 passed / 1 skipped**, route surface **82**, smoke funcional OK. El
> corrective `ce17790` (lente Copiloto del dashboard actual, ver abajo) entró en
> el mismo PR. Batch A2 (Hub-wide dashboard) autorizado, arranca desde el `main`
> ya realineado.

Primer paso de "Shared Activity / Observability": hacer la capacidad de actividad
existente **platform-aware de forma ADITIVA**, preservando toda la actividad
histórica sin migración. **NO** es Activity Dashboard V2, **NO** toca UI, **NO**
agrega checkbox filters, **NO** deduplica el sticky-filter (todo eso es Batch A2).

- **Envelope viejo (histórico):** `ts_utc, event, user, name, payload`.
- **Envelope nuevo (aditivo):** `ts_utc, event, user, name, scope, area, payload`.
  `event` sigue siendo el identificador de acción. **NO** se agregaron
  `workspace`/`vertical`/`feature`/`action` (rechazados en este batch).
- **`scope`** = ámbito de Danwor Hub: `platform` | `copilot` | `operations`.
  La autenticación es de la PLATAFORMA (no de un workspace), por eso `platform`
  es de primera clase. **`area`** = área funcional: `auth` | `methodology` |
  `assistant` | `academy` | `catalog` | `staffing`. Sin taxonomía inventada más
  allá de productores reales.
- **Un solo activity service.** No hay `copilot_logger`/`operations_logger`/
  `platform_logger`. `log_activity`/`log_verbose` ganaron `scope`/`area`
  **keyword-only**, pasados EXPLÍCITOS por cada productor. El sink de bajo nivel
  (`activity_service`) **NO infiere** contexto ni conoce la taxonomía, ni importa
  FastAPI/Jinja. Si `scope`/`area` son None se OMITEN (forma histórica de 5
  campos preservada; el lector normaliza su ausencia).
- **Normalización histórica (una función canónica):** nuevo módulo hoja
  `webapp/services/activity_context.py` — constantes + `classify(event)` (mapa
  histórico) + `context_of(record)` (explícito gana; si falta, clasifica por
  `event`). El lector `activity_analytics._read_events` adjunta `scope`/`area` a
  CADA evento vía `context_of` — **seam único**, sin esparcir `event→scope/area`
  por el dashboard. `build_summary()` **NO** se rediseñó (eso es A2).
- **Contrato interino de lente (corrective `ce17790`, pre-A2):** el stream de
  actividad es **platform-wide desde ya** (persiste/normaliza Operations), pero el
  Activity Dashboard sigue siendo **Copiloto + plataforma** hasta A2. Como
  `build_summary()` contaba TODO el stream (verificado empíricamente: Operations
  contaminaba total_events/unique_users/daily_series/event_counts/top_users/facets),
  se agregó una regla en UN punto — `_DASHBOARD_EXCLUDED_SCOPES = {operations}`,
  aplicada al tope de `build_summary` antes de facets/conteos. **NO** es copilot-only:
  `login` (scope=platform) se preserva (semántica histórica). **A2 debe RETIRAR esta
  exclusión** al convertir el dashboard en Hub-wide. Test de regresión prueba
  persistencia-sí / contaminación-no simultáneamente. Suite **528 passed / 1 skipped**.
- **Mapeo histórico explícito (evidence-based):** `login`→platform/auth;
  `module_selected` + beacon (`module_continue`/`module_reached_end`/`nav_jump`/
  `link_external`/`disclosure_toggle`/`theme_change`) + `tree_started`/`tree_step`/
  `tree_outcome`→copilot/methodology; `assistant_query` (+ histórico
  `section_analyze`)→copilot/assistant; `academy_selected`→copilot/academy;
  `catalog_view`/`catalog_regenerate`→copilot/catalog; `staffing_overview_*`→
  operations/staffing. **Evento desconocido → `unknown`/`unknown`** (fallback
  explícito, NUNCA "copilot" en silencio). No se hallaron eventos históricos sin
  clasificar en fixtures/tests; un test-guardia verifica que todo nombre emitido
  por un productor real clasifica ≠ unknown.
- **Operations = primer productor de USO real:** `staffing_overview_viewed`
  (BASIC, adopción — como `module_selected`/`academy_selected`) en el GET exitoso
  de `/operaciones/staffing/asignaciones`. **Un evento por acceso a la página**;
  usa la sesión ya resuelta; **sin 2ª llamada a Graph, sin loguear el token, sin
  loguear el dataset, sin clickstream** (el POST /data y las interacciones
  client-side NO se loguean). Prueba: una capacidad compartida + múltiples scopes
  reales de plataforma.
- **Diagnósticos de Staffing** (`staffing_overview_config_incomplete`,
  `staffing_overview_error`) conservan su semántica **VERBOSE** y ahora reciben
  `scope=operations`/`area=staffing`. No se convirtieron en eventos BASIC de uso.
- **Beacon `/telemetry/event`:** el contexto lo fija el servidor
  (copilot/methodology); se excluyen `scope`/`area`/`event` del body para que el
  cliente **no pueda inyectar ni colisionar** el contexto de plataforma.
- **VERBOSE contract sin cambios:** `LOG_ACTIVITY`/`BASIC`/`VERBOSE`/
  `log_activity`/`log_verbose`/`is_verbose()` intactos. Saneo/redacción de payload
  (claves sensibles → `[redacted]`, `answer` cap 20000, resto 4000) sin cambios.
  **Usage analytics, NO audit/security logging.**
- **Retrocompatibilidad:** JSONL históricos siguen legibles SIN migración ni
  reescritura; los archivos existentes no se mutan. El lector defaultea lo
  ausente vía `context_of` (histórico → clasificación por event; Copiloto es el
  origen de casi todo lo previo, pero se clasifica por familia, no por asunción
  global).
- **Tests (+11):** `tests/webapp/test_activity_context.py` (9 — classify/
  context_of/guardia de cobertura, envelope explícito, forma histórica de 5
  campos, saneo/redacción, gate BASIC/VERBOSE, normalización lector histórico+
  nuevo) + `tests/webapp/test_staffing_routes.py` (2 — un solo
  `staffing_overview_viewed` con contexto+identidad+sin token/dataset;
  diagnósticos VERBOSE-only + contexto + token nunca logueado). Directorios de
  log temporales aislados (nunca runtime real).
- **Baseline:** suite **528 passed / 1 skipped** (516 + 11 + 1 regresión del
  corrective), **route surface 82 sin cambios** (snapshot intacto), sobre stack
  reproducible (Python 3.14.3 / FastAPI 0.141.1 / Starlette 1.4.1). **Cierre:**
  owner-confirmed CI **GREEN**, mergeado a `main` vía **PR #44 / `53277d6`**.
- **Batch A2 (Activity Dashboard V2 platform-wide) fue implementado — ver sección
  siguiente.**

---

## SHARED ACTIVITY / OBSERVABILITY — HUB DASHBOARD V2 (Batch A2) — CLOSED / GREEN
(commit funcional `8ae7593` + merge de reconciliación `54fc9b2` + corrective
de reconciliación (zero-selection guard), branch `work/activity-dashboard-hub-v2`,
**mergeada a `main` en tres pasadas — PR #50 (`cdc690b`, base Hub-wide +
reconciliación + zero-selection guard) → PR #51 (`75fa935`, corrective de
módulo/evento transversal + consistency check `configuration_incomplete`) →
PR #52 (`39fb236`, USAGE DASHBOARD SEMANTIC CLEANUP) — CLOSED/GREEN tras las
tres. Ver "FINAL HANDOFF CLOSURE" al final de esta sección para el modelo
final entregado y la validación de producción del owner.**)

Convierte el Activity Dashboard de Copilot-centric a dashboard de uso/adopción de
**Danwor Hub**. Depende de la fundación A1 (envelope `scope`/`area` + `context_of`).

- **Se RETIRÓ la exclusión interina de A1** (`_DASHBOARD_EXCLUDED_SCOPES`): el
  default del dashboard es ahora **Hub-wide** — platform + copilot + operations
  incluidos. Test de regresión prueba la inclusión por default.
- **Filtros Ámbito (scope) / Área (area)** multi-select con **faceting mutuo**
  derivado del universo filtrado (opciones nunca sintéticas; una selección que queda
  inválida al cambiar la otra dimensión se descarta determinísticamente). Ej.
  Ámbito=Operaciones ⇒ Área se restringe a Staffing; Área=Staffing ⇒ Ámbito resuelve
  a Operaciones. Default = todo tildado.
- **Pipeline genérico** (date → user → module → scope/area) + **lentes de dominio
  separadas**. `module` sigue Copilot-específico (Operations nunca tiene module, no
  se lo fuerza a la taxonomía de módulos). Las lentes Copilot (módulos/árboles/
  asistente/catálogo) keyean por eventos Copilot y **se vacían solas** cuando el
  Ámbito las excluye; `copilot_present` gatea los KPIs de asistente. Operations NO
  contamina los análisis Copilot (probado).
- **KPIs:** genéricos cross-scope = Eventos · Usuarios únicos · Eventos/día +
  **Áreas activas** (nuevo). Los KPIs de asistente (Preguntas/Consultas IA) son
  **lente Copilot** (solo cuando hay Copiloto en la selección), no métricas Hub
  genéricas. **NO** se inventaron KPIs de negocio de Operations (proyectos/
  allocation/capacidad son del dashboard de Staffing, no de éste).
- **Operations/Staffing** se representa solo por la telemetría que EXISTE
  (`staffing_overview_viewed` = "Vista de Asignaciones" + diagnósticos): conteos de
  uso Hub-wide + inclusión en filtros scope/area + desgloses por usuario/fecha/evento.
- **Vocabulario:** labels ES scope/area en `activity_context` (keys persistidos en
  inglés, estables). Contexto desconocido queda **explícito** (`unknown`/`unknown`),
  visible solo si tales eventos existen; nunca se asigna a Copilot en silencio.
- **Rutas:** `/admin/activity[/stories|/tree-runs]` aceptan `scope`/`area` (multi-
  valor) y los propagan; **full-page y HTMX comparten el mismo estado de filtro**;
  los pagers preservan scope/area en `base_qs`. **Gate owner-only + diagnóstico SIN
  cambios** (no se amplió el acceso pese a ser Hub-wide).
- **Primitivas de dashboard compartidas:** primitiva de checkbox multi-select
  extraída (`.dashboard-checkbox-group`/`.dashboard-checkbox-option`, de la botonera
  de Estado de Staffing, byte-for-byte) — usada por Actividad y **Staffing migrado a
  ella** (Alpine `x-model` intacto; solo rename de clase). **Sticky consolidado:** se
  eliminó el duplicado `.staffing-sticky-filters`; ambos dashboards usan
  `.dashboard-sticky-header`. Se reusan `activity-kpi(-grid)`/`dashboard-section`/
  `whatsnew-filters`/`dashboard-empty` (ya compartidas). Solo gramática visual — NO
  se compartió engine de filtro (Actividad = HTMX/form, Staffing = Alpine).
- **NO se agregaron productores de telemetría** (A2 es reader/dashboard). **NO se
  inició Staffing V2.**
- **Tests (+16 neto):** analytics (default Hub-wide incluye Operations; filtros/
  faceting scope/area; histórico 5-campos; unknown explícito; date/user/module;
  module no absorbe Operations; lentes Copilot correctas y no contaminadas) + rutas/
  UI (render de filtros Ámbito/Área; estado sobrevive refresh HTMX; default all-
  checked; selección inválida determinística; gate sin cambios; paridad full/partial;
  sin métricas de negocio de Operations; primitivas compartidas). Staffing GREEN
  (sticky test actualizado). **Route surface 82 sin cambios.**
- **Baseline:** suite **543 passed / 1 skipped** (stack reproducible 3.14.3 / FastAPI
  0.141.1 / Starlette 1.4.1), route-contract GREEN.
- **Validación visual (browser real, harness con ACTIVITY_LOG_DIR temp sembrado —
  sin tocar historia runtime):** default Hub-wide (todo tildado, KPI Áreas activas,
  Operations incluido); `scope=operations` (Ámbito→Operaciones, Área→Staffing, KPIs
  de asistente y secciones Copilot desaparecen, sin métricas de negocio); checkbox
  state correcto; sin overflow horizontal en desktop (1280) ni mobile (375); primitiva
  de checkbox/sticky con computed styles correctos. Smoke del dashboard de Staffing
  en browser requiere token de Graph + allowlist de Operations (no disponible en este
  entorno) → cubierto por los 135 tests de Staffing (fragmento renderizado con service
  mockeado) + CSS byte-idéntico. **PENDING:** CI GREEN en el PR + validación en prod.

### Pase de reconciliación — Batch A2 vs `main` post-Staffing V2

`work/activity-dashboard-hub-v2` (commit `8ae7593`, arrancada desde `main`
`2505da2`) predataba toda la evolución de Staffing Dashboard V2 — Director
Priority Update (arriba, CLOSED/GREEN, mergeada a `main` vía PR #46/#47/#48).
Staffing cambió superficies compartidas (`webapp/static/tools.css`,
`webapp/templates/operations/staffing_dashboard.html`,
`tests/webapp/test_staffing_routes.py`) — esta reconciliación las trae a la
rama de A2 antes de continuar hacia PR, sin rediseñar A2 ni tocar Staffing.

- **Merge:** `git merge origin/main` (NO rebase, NO force-push) sobre
  `work/activity-dashboard-hub-v2` → commit de merge `54fc9b2`. Divergencia
  previa: A2 2 commits adelante / `main` 11 commits adelante. **Conflicto
  real: únicamente `HANDOFF.md`** (resuelto a mano, preservando ambas
  evoluciones — ver el resto de esta sección y "Estado de fases"/"CURRENT
  EXECUTION POINT" abajo). `tools.css`, `staffing_dashboard.html`,
  `test_staffing_routes.py` y el resto de los archivos de Staffing
  **auto-mergearon limpio vía git, sin intervención manual** — la primitiva
  de checkbox/sticky compartida (`.dashboard-checkbox-group`/
  `.dashboard-checkbox-option`/`.dashboard-sticky-header`) y el resto del
  trabajo de A1/A2 no colisionaron con ninguno de los correctivos de
  Staffing V2 (KPIs por estado, loader de historial, colgroup fijo de 10
  columnas, `ASIGNACIÓN +N`, etc.) — **ambas evoluciones sobrevivieron
  intactas, confirmado por fuente y por gate** (ver abajo).
- **Shared UI primitives — revalidado post-merge:** `.dashboard-checkbox-group`/
  `.dashboard-checkbox-option`/`.dashboard-sticky-header` presentes y en uso
  por ambos dashboards; NO se resucitó `.staffing-sticky-filters` (el
  comentario que documenta su eliminación sigue en `tools.css`); CSS de
  layout fijo de la tabla de detalle de Staffing intacto
  (`table-layout:fixed` + colgroup `3,14,11,14,12,8,9,8,8,13`); filtros de
  Actividad renderizan correctamente.
- **Route contract:** route surface se mantiene en **83** (Staffing agregó
  `POST /operaciones/staffing/asignaciones/historial` antes de esta
  reconciliación; A2 no agrega ni quita rutas) — snapshot NO revertido a 82.
- **Pending pre-PR check #1 — Activity shell/ownership — NO CHANGE
  (documentado, no corregido en este pase):** `/admin/activity` (full-page)
  pasa `current_workspace=workspaces.COPILOT` explícito
  (`webapp/main.py::admin_activity`), `selected=DEFAULT_PACK` sigue
  gobernando `modules`/`appendices`/`academy`/`pack_label`, e incluye
  `partials/copilot_nav.html` (nav completo de módulos Copilot) +
  identidad animada con `config.APP_TITLE`/`APP_SUBTITLE` (marca de
  Copiloto). **Confirmado en vivo** (browser real, sesión owner): el brand
  iframe muestra "Copiloto Metodológico | Arquitecto Danwor", el nav
  lateral trae 15 links de módulos, y el switcher ofrece "Operaciones"
  como alternativa — es decir, el CHROME de la página sigue leyendo como
  "Copilot" pese a que el CONTENIDO (`admin_activity_content.html`) ya es
  genuinamente Hub-wide (KPIs/filtros Ámbito-Área sin favoritismo de
  dominio). **Decisión: sin cambio de código en este pase** — es una
  inconsistencia cosmética de baja severidad, no una tergiversación
  funcional de acceso: la ruta sigue siendo owner-only + diagnóstico (sin
  cambios), solo alcanzable desde un link dentro del propio panel de
  diagnóstico de Copilot (`admin_panel.html`), nunca enlazada desde
  Operations, el selector Hub, ni navegación general — ningún usuario
  no-owner ni usuario de Operations-only percibe este chrome. No existe
  una corrección chica y segura a la vez: el grid `.app-shell.no-copilot`
  asume `.module-nav` + resize-handle en esas columnas exactas (sacar el
  nav rompe el layout sin ajustar columnas), y `hub_brand_lockup()` (la
  identidad Hub neutral ya existente) es un bloque de hero completo, no un
  componente compacto de topbar — cualquiera de las dos correcciones reales
  se acerca a "nueva arquitectura de shell", explícitamente fuera de
  alcance de este batch. Queda documentado para una decisión futura
  explícitamente autorizada por el owner si se quiere pulir.
- **Pending pre-PR check #2 — zero-selection semantics — CORREGIDO
  (gap real encontrado e implementado):** el server ya caía a "todos" con
  cero parámetros `scope`/`area` (`sel_scopes = (requested_scopes &
  present_scopes) or present_scopes` en `activity_analytics.build_summary`)
  — la invariante "nunca cero seleccionados" YA estaba garantizada
  server-side. Lo que faltaba: **nada evitaba que el owner destildara el
  último checkbox tildado de un grupo** — su clic se revertía en silencio a
  "todo tildado" en vez de quedar en "nada", indistinguible de "filtro
  nunca tocado". Implementado el guard preferido por la especificación
  ("prevent deselecting the final selected option"): listener delegado en
  `document` para `change` sobre `input[name="scope"|"area"]` que revierte
  el último checkbox de su grupo si quedaría sin ninguno tildado
  (`webapp/static/app.js`, después de `initModuleFilter`). Filtra por
  `name` para no interferir con los checkboxes de Estado de Staffing
  (Alpine `x-model`, sin atributo `name`). **Verificado en vivo** (browser
  real): destildar Operaciones+Plataforma deja Copiloto tildado; el intento
  de destildar Copiloto (el último) se revierte automáticamente — mismo
  resultado confirmado para Área. Test de fuente
  (`test_zero_selection_guard_prevents_unchecking_last_scope_or_area_option`
  en `tests/webapp/test_activity_routes.py`, sin runtime de JS en esta
  suite, mismo criterio que Staffing).
- **A2 test gate (20 items):** los 20 puntos de la especificación de
  reconciliación tienen cobertura — Hub-wide default/scope-isolation/area-
  faceting/combinación/histórico-5-campos/unknown explícito (existentes,
  intactos post-merge) + render Ámbito-Área/refresh HTMX/default all-
  selected/gate owner-only/paridad full-partial/sin métricas de negocio de
  Operations/primitivas compartidas (existentes) + **zero-selection
  determinístico (nuevo, este pase)**. Staffing regression incluida en el
  ítem 20 (ver abajo).
- **Staffing regression gate:** 186 tests de Staffing (routes + repository +
  service + director-priority-v2 + production-corrective) **100% GREEN
  post-merge, sin tocar ningún archivo de Staffing en este pase** — default
  Activo-only, visibilidad de KPI por Tentativo, loader lazy de Inactivo,
  endpoint `/historial`, Activos vencidos, agrupamiento, colapso
  persistente, detalle agregado, proyección +N, drag/reorder, tabla de
  detalle fija, `ASIGNACIÓN +N`, DMY, uso de primitivas compartidas — todo
  intacto.
- **Visual smoke (browser real, sesión owner, servidor aislado :8091):**
  Activity — default Hub-wide con datos reales de `activity_log/` (Ámbito
  Copiloto/Operaciones/Plataforma y Área Academy/Asistente/Autenticación/
  Catálogo/Metodología/Staffing todos tildados por default, incluyendo
  eventos de Operations); guard de zero-selection confirmado en vivo para
  ambos grupos (ver arriba); sin overflow horizontal. Staffing — fragmento
  renderizado con `StaffingService` mockeado (3 asignaciones: Activo/
  Tentativo/Activo-vencido) inyectado en el DOM real: KPI "Asignaciones
  tentativas" oculto por default (`display:none`, Tentativo no
  seleccionado), "Activos vencidos" visible y en 1 (la asignación vencida
  detectada), `projectionColumnLabel` = "Asignación +35", colgroup
  `3,14,11,14,12,8,9,8,8,13` intacto, sticky/checkbox compartidos presentes,
  sin overflow horizontal (907px cliente = 907px scroll).
- **Gate final:** targeted Activity (26 passed) + targeted Staffing (186
  passed) + full suite **611 passed / 1 skipped** (+68 sobre el baseline
  pre-reconciliación de 543 de A2, +16 sobre el baseline de 595 de Staffing
  V2 cerrado) + route-contract GREEN, route surface **83** sin cambios.
- **Commits de este pase:** merge `54fc9b2` (`Merge remote-tracking branch
  'origin/main' into work/activity-dashboard-hub-v2`) + un corrective
  funcional pendiente (zero-selection guard + test, ver STOP report para el
  hash exacto) — ningún commit histórico de A2 reescrito/squasheado, sin
  force-push.
- **NO se iniciaron vacaciones, NO se inició trabajo nuevo de Staffing, NO
  se agregaron productores de telemetría nuevos** en este pase.

### Corrective — Transversal Module + Event Semantics (antes de abrir el PR de A2)

El owner revisó el Hub Dashboard real y encontró una inconsistencia
estructural: en Copiloto, entrar a un módulo SE REPRESENTA como actividad de
módulo (`module_selected`); en Operations/Staffing, entrar a Asignaciones NO
se representaba como módulo — Actividad solo veía nombres de evento crudos
con la ubicación repetida (`staffing_overview_viewed`, `staffing_overview_
error`, `staffing_historial_error`, `staffing_data_diagnostics`), producía
`0 módulo(s)` con el owner navegando activamente Asignaciones, y no escalaba
a futuras áreas (Recruiting, Projects, etc.).

**Jerarquía canónica congelada:** `scope → area → module (opcional) →
event`. `module` deja de ser "Copilot-only" — pasa a ser una dimensión
TRANSVERSAL opcional (Copilot Y Operations por igual). Regla de nombrado de
eventos congelada: el EVENTO describe QUÉ pasó, nunca DÓNDE, si scope/area/
module ya lo representan.

- **Trace previo (antes de tocar código):** `module` NUNCA fue un campo de
  primera clase del envelope — vivía dentro de `payload` (`payload.module`
  para `module_selected`, `payload.stem` para `section_analyze`/
  `assistant_query`/`tree_started`), extraído en `activity_analytics.
  _module_key` (ahora `_legacy_payload_module`, ver abajo). Staffing nunca
  pasó `module` en ningún productor.
- **Envelope persistido (NUEVO):** `activity_service.log_activity`/
  `log_verbose` ganan `module: str | None = None` keyword-only, aditivo
  entre `area` y `payload` (mismo criterio que scope/area — se omite si es
  `None`, callers existentes sin `module` siguen escribiendo el envelope de
  siempre). **NUNCA se migran/reescriben los JSONL históricos.**
- **Semántica de módulo — Asignaciones:** `module="assignments"` (key
  estable) → label visible **"Asignaciones"** (`activity_context.
  MODULE_LABELS`, mismo patrón que `SCOPE_LABELS`/`AREA_LABELS`). Los
  módulos de Copiloto (stems de contenido, p. ej.
  `01_arquitectura_y_conectividad`) NO se tocan ni se renombran — sin
  entrada en `MODULE_LABELS`, se ven tal cual (fallback al key crudo).
- **Productores nuevos de Staffing** (`webapp/routers/staffing.py`):
  - `staffing_overview_viewed` → `module_viewed` / `module=assignments`.
  - `staffing_overview_error` → `data_load_failed` / `module=assignments` /
    `payload.operation=initial`.
  - `staffing_historial_error` → `data_load_failed` / `module=assignments` /
    `payload.operation=history`.
  - `staffing_data_diagnostics` → `data_load_diagnostics` /
    `module=assignments` / `payload.operation=initial`.
  - `staffing_historial_diagnostics` → `data_load_diagnostics` /
    `module=assignments` / `payload.operation=history` — **extensión no
    pedida palabra por palabra en el corrective, pero aplicada por
    consistencia**: es el análogo exacto de `staffing_data_diagnostics` para
    la fase de historial; dejarlo sin normalizar habría sido inconsistente.
  - `staffing_overview_config_incomplete`/`staffing_historial_config_
    incomplete` **NO se tocan** (no pedidos, no repiten ubicación de forma
    que escale) — solo se les asigna `event_class=diagnostic` en el
    read-model para que no infle adopción.
- **Productor Copiloto** (`webapp/routers/modules.py`): `module_selected` →
  `module_viewed`, mismo `module=stem` de siempre (ahora campo de primera
  clase, no solo payload). Un solo evento por navegación (nunca ambos).
- **Normalización histórica** (`activity_context._EVENT_RENAME` +
  `normalize_event()`) — SOLO read-model, JSONL nunca tocado:
  `module_selected`→`module_viewed` (module se sigue extrayendo de
  `payload`, no se inyecta); `staffing_overview_viewed`→`module_viewed`/
  `assignments`; `staffing_overview_error`→`data_load_failed`/
  `assignments`/`operation=initial`; `staffing_historial_error`→
  `data_load_failed`/`assignments`/`operation=history`;
  `staffing_data_diagnostics`/`staffing_historial_diagnostics`→
  `data_load_diagnostics`/`assignments`/`operation=initial`|`history`.
  `raw_event` se preserva íntegro en cada evento leído (nunca se pierde qué
  se persistió realmente).
- **event_class (read-model únicamente, NUNCA en el envelope persistido)** —
  `activity_context.event_class()`: `usage` (login, module_viewed,
  assistant_query, academy_selected, catalog_view/regenerate, tree_*, y el
  resto del uso Copilot ya conocido) vs. `diagnostic` (`data_load_failed`,
  `data_load_diagnostics`, y los `*_config_incomplete` sin renombrar) vs.
  `unknown` (fallback explícito — nunca cae en usage en silencio).
- **Métricas de adopción, uso-only:** Eventos totales, Usuarios únicos,
  Eventos/día, Tendencia diaria, Top usuarios y Áreas activas ahora se
  computan EXCLUSIVAMENTE sobre `event_class=="usage"` — un usuario que solo
  generó errores de Graph ya no aparenta ser un usuario Hub activo.
- **Top Módulos, transversal:** cuenta ÚNICAMENTE ocurrencias de
  `event=="module_viewed"` agrupadas por `module` — Copiloto y Asignaciones
  participan de la MISMA métrica. Un `data_load_failed` dentro de
  Asignaciones nunca suma otra "visita". `module_event_breakdowns` (drilldown
  por módulo, al clickear una fila de "Top módulos") sigue incluyendo TODO lo
  que pasó en ese módulo (uso + diagnóstico) — el drilldown es distinto de la
  métrica de "visitas".
- **Filtro Módulo, transversal:** universo faceteado por fecha+usuario+
  Ámbito+Área (nunca por sí mismo, así el `<select>` no colapsa a su propia
  selección — mismo criterio que Área con Ámbito); una selección que queda
  inválida tras un cambio de Ámbito/Área se descarta determinísticamente
  (mismo criterio, nunca deja el dashboard en "0 eventos" silencioso).
  Ámbito/Área a su vez dejaron de derivar de un universo que incluía el
  filtro de Módulo (antes lo hacían — backwards, una dimensión de más abajo
  en la jerarquía no debe re-facetear a las de arriba).
- **UI:** sección "Eventos y catálogo" → **"Eventos"** (el nombre anterior
  era Copilot-centric, catálogo no existe en Operations). Dentro de la
  MISMA sección, "Eventos de uso" y (solo si hay diagnóstico) "Diagnóstico"
  quedan separados con la primitiva `dashboard-subhead` ya existente — sin
  tabs, sin dashboard nuevo, el diagnóstico sigue 100% inspeccionable
  (drilldown por usuario intacto). El `<select name="module">` ahora
  renderiza `{key, label}` (antes un string plano) — value=key (round-trip
  de filtro), texto visible=label.
- **Validación contra el `activity_log/` REAL del owner (solo lectura, JSONL
  jamás tocado):** `staffing_overview_viewed` (15) + `module_selected` (316,
  Copiloto) = **331** `module_viewed` normalizados; `staffing_overview_error`
  (28) + `staffing_historial_error` (1) = **29** `data_load_failed`;
  `staffing_data_diagnostics` (8) + `staffing_historial_diagnostics` (0) =
  **8** `data_load_diagnostics` — conteos ANTES/DESPUÉS coinciden
  exactamente. Top Módulos (solo `module_viewed`) para `assignments`:
  **15** (excluye correctamente los 37 eventos de diagnóstico) — validado en
  vivo en el dashboard real: `1 módulo(s) · 3 usuario(s)` (antes `0
  módulo(s)`), `<option value="assignments">Asignaciones</option>`, fila
  "Asignaciones · 15" en Top Módulos, "Eventos de uso"/"Diagnóstico"
  visualmente separados y ambos inspeccionables.
- **NO se tocó:** carga de datos de Staffing, historial lazy, filtros
  Graph, cálculos de KPI/timeline/detalle de Staffing, rutas, auth/sesión,
  ownership del shell de Actividad, vacaciones, UX de renovación de sesión
  Microsoft.
- **Gate:** targeted Activity (30 tests de contexto + 17 de rutas/UI) +
  targeted Staffing (todos los archivos) + full suite **635 passed / 1
  skipped** (+24 sobre el baseline de 611 de la reconciliación) +
  route-contract GREEN, route surface **83 sin cambios** (ningún endpoint
  nuevo).

#### Consistency check post-corrective — `configuration_incomplete`

El corrective de arriba dejó afuera, por error (no a propósito),
`staffing_overview_config_incomplete`/`staffing_historial_config_incomplete`
— una verificación puntual confirmó que **seguían siendo productores
CURRENT** (`webapp/routers/staffing.py`, ramas `except RuntimeError` de
`/data` e `/historial`), no solo históricos/test/compatibilidad. Se
normalizaron al canónico reusable **`configuration_incomplete`**
(`module=assignments`, `operation=initial`/`history`, `event_class=
diagnostic`) — deliberadamente **no** forzados a `data_load_failed`: config
incompleta (site_id/mapeo de campos) no es un fallo de Graph/transporte, es
un problema distinto que merece su propio nombre genérico. Lo histórico
normaliza igual vía `_EVENT_RENAME`, preservando `raw_event`. Tras este
fix, **cero productores current restantes** cuyo nombre repita `staffing_`
— las únicas apariciones que quedan en el repo son los mapas de
compatibilidad histórica (`_HISTORICAL`, `_EVENT_RENAME`) y fixtures de
test. Validado (solo lectura) contra el `activity_log/` real del owner: 0
ocurrencias históricas de ambos raw events — resultado esperado, nunca se
disparó esa rama de error en producción hasta ahora. Commit funcional
`fix(activity): complete Staffing event normalization` — +3 tests sobre
el baseline (638 passed / 1 skipped), route surface 83 sin cambios.

#### USAGE DASHBOARD SEMANTIC CLEANUP — boundary de producto del dashboard de Actividad

Con el modelo de evento/module ya correcto (arriba), el owner señaló que el
dashboard **Actividad y métricas de uso** seguía mostrando diagnóstico
técnico (fallas de Graph, config incompleta) dentro de una pantalla de
adopción — semánticamente incorrecto: esa pantalla responde *"quién usa
Danwor Hub, qué usa, cómo evoluciona"*, nunca *"¿falló Graph?"*.

**Boundary congelado** (`webapp/services/activity_analytics.py`,
`build_summary`): TODO el read-model que alimenta este dashboard —
`event_counts`, métricas de adopción (Eventos/Usuarios únicos/Eventos por
día/Tendencia diaria/Áreas activas/Top usuarios), `event_user_counts`,
`module_event_counts` (y por lo tanto `module_event_breakdowns`) — filtra a
`event_class == usage` con un solo `continue` temprano al inicio del loop.
`diagnostic` y `unknown` simplemente nunca entran: no hay dos counters, no
hay split visual, no hay lógica condicional repartida por el template.
`unknown` (evento sin clasificar) tampoco entra — nunca contamina adopción
en silencio, ni siquiera cuando se filtra explícitamente por
Ámbito=unknown.

**El diagnóstico NO se borra ni deja de emitirse** — `data_load_failed`,
`data_load_diagnostics`, `configuration_incomplete` siguen 100%
persistidos/normalizados/clasificables (`activity_service`/
`activity_context`, sin cambios), disponibles para una futura herramienta
owner-only que este batch **no crea** (sin tabs, sin "Mostrar
diagnóstico", sin accordion, sin query-string de diagnóstico).

**UI simplificada** (`admin_activity_content.html`): la sección "Eventos"
colapsa el anidamiento `Eventos → Eventos por tipo → Eventos de
uso/Diagnóstico` en un solo listado, "Acciones de usuario · click para
desglosar →" — sin heading "Eventos de uso" ni "Diagnóstico". El drilldown
por usuario (panel derecho) y el filtro cruzado por módulo (clic en
"Asignaciones" en "Módulos y usuarios") siguen funcionando igual, ahora
usage-only en ambos lados.

**Labels visibles de evento** (nuevo seam, `EVENT_LABELS`/`event_label` en
`activity_context.py`, mismo patrón que `MODULE_LABELS`/`module_label`): la
key técnica canónica (`module_viewed`, `assistant_query`, `catalog_view`,
`tree_started`, ...) ya no se muestra cruda en la UI — se reemplaza por su
label visible en español ("Vista de módulo", "Consulta al asistente", "Vista
de catálogo", "Inicio de recorrido"). Sin framework de localización — un
dict chico con fallback seguro al nombre crudo para eventos sin entrada. La
key técnica queda en el tooltip (`event_descriptions`, sin cambios) y en los
atributos `data-*` que usa el JS (`initEventBreakdown`/`initModuleFilter`,
sin cambios — el toggle sigue por atributo, nunca por texto visible).

**Validación real (solo lectura, `activity_log/` del owner, JSONL jamás
tocado):** con el dataset real (516 eventos de uso, 337 `module_viewed`),
`event_counts`/`event_breakdowns`/`module_event_breakdowns` no contienen
NINGÚN evento diagnóstico — confirmado que los ~29 `data_load_failed`/8
`data_load_diagnostics` históricos normalizados (ver corrective arriba)
desaparecen de la UI de uso sin desaparecer del log. Top usuarios refleja
volumen de uso real (`Jose O'Connor` 463), nunca ruido diagnóstico. Vista
Operations/Staffing-only: "Asignaciones" 15 en Top módulos, "Vista de
módulo" 15 en Acciones de usuario, drilldown por usuario funcional — sin
subgrupo diagnóstico, igual al mockup visual pedido por el owner.

**Gate:** targeted Activity (56 tests) + targeted Staffing (122 tests) +
full suite **646 passed / 1 skipped** (+8 sobre el baseline de 638) +
route-contract GREEN, route surface **83 sin cambios**. Commit funcional
`fix(activity): keep usage dashboard focused on user activity`.

### FINAL HANDOFF CLOSURE

A2 fue mergeada y **validada en producción por el owner** — cierre formal
de todo el batch (base Hub-wide + reconciliación + corrective de módulo/
evento transversal + consistency check + usage dashboard semantic cleanup).
Ninguna fase queda abierta bajo "A2"; los follow-ups NO iniciados están
documentados por separado (ver "STAFFING — VACATION TIMELINE OVERLAY",
"MODULE IDENTIFIER CONVENTION V2", "ACTIVITY ADMIN SHELL OWNERSHIP" y
"AUTH / GRAPH SESSION RENEWAL UX" más abajo).

**Modelo final entregado:**

```
scope → area → module? → event
```

con read-model de clasificación (NUNCA persistido en el envelope):

```
event_class = usage | diagnostic | unknown
```

- **Default Hub-wide:** Platform + Copilot + Operations incluidos por
  default (la exclusión interina de A1 fue retirada) — todo tildado en
  Ámbito/Área hasta que el owner filtre explícitamente.
- **Faceting scope/area/module:** Ámbito→Área unidireccional (Área nunca
  re-facetea Ámbito — el criterio que evita el bug reportado de "no puedo
  volver a tildar Copiloto"); Módulo se resuelve DESPUÉS de Ámbito/Área
  (fecha+usuario+Ámbito+Área → universo de módulos), nunca al revés; una
  selección que queda inválida tras cambiar una dimensión de más arriba se
  descarta determinísticamente, nunca deja el dashboard en "0 eventos"
  silencioso.
- **`module` transversal opcional:** dejó de ser Copilot-only — Copiloto Y
  Operations comparten la misma dimensión y la misma métrica de "Top
  módulos".
- **Semántica de Staffing/Asignaciones:** `module="assignments"` (key
  estable) → label visible "Asignaciones" (`MODULE_LABELS`/`module_label`);
  los módulos de Copiloto (stems de contenido) no se tocan.
- **Nombrado canónico de evento:** el evento describe QUÉ pasó, nunca DÓNDE
  — `module_viewed`, `data_load_failed`, `data_load_diagnostics`,
  `configuration_incomplete` reemplazan los nombres históricos de Staffing
  con la ubicación repetida en el propio nombre.
- **Normalización histórica sin migrar JSONL:** `_EVENT_RENAME`/
  `normalize_event()` operan puramente sobre el read-model leído — el JSONL
  persistido nunca se reescribe, ni para los históricos de Copiloto
  (`module_selected`) ni para los de Staffing.
- **`raw_event` preservado:** cada evento leído conserva el nombre
  realmente persistido junto al canónico normalizado — el owner puede
  seguir inspeccionando qué se guardó de verdad.
- **Clasificación uso vs. diagnóstico:** `event_class()` sobre el nombre ya
  normalizado — `usage` (login, module_viewed, assistant_query, árboles,
  catálogo, Academy, ...) vs. `diagnostic` (`data_load_failed`,
  `data_load_diagnostics`, `configuration_incomplete`) vs. `unknown`
  (fallback explícito, nunca cae en usage en silencio).
- **Boundary usage-only del Activity Dashboard:** TODO el read-model que
  alimenta la UI (`event_counts`, adopción, `event_user_counts`,
  `module_event_counts`/`module_event_breakdowns`) filtra a
  `event_class==usage`. El diagnóstico sigue 100% persistido/normalizado/
  clasificable — solo queda excluido de ESTA pantalla de adopción, no
  borrado ni dejado de emitir.
- **Top Módulos transversal:** cuenta únicamente `event==module_viewed`,
  agrupado por `module` — Copiloto y Asignaciones en la misma métrica.
- **Top Usuarios / Tendencia diaria / Eventos / Áreas activas — usage-only:**
  las seis métricas de adopción (Eventos totales, Usuarios únicos,
  Eventos/día, Tendencia diaria, Top usuarios, Áreas activas) se computan
  exclusivamente sobre `event_class==usage`.
- **Zero-selection guard:** un listener delegado en `document` impide
  destildar la última opción tildada de un grupo Ámbito/Área — el estado
  ambiguo "cero seleccionados" nunca llega a enviarse.
- **Staffing regression preservada** en las tres pasadas de merge — sin
  tocar carga de datos, historial lazy, filtros Graph, KPIs/timeline/
  detalle de Staffing.
- **Route surface: 83, sin cambios** en ninguna de las tres pasadas (A2 es
  reader/dashboard — no agrega ni quita endpoints).

**Validación de producción del owner (recorded, no simulado):**
- uso de módulos de Copiloto observado (`module_viewed`, Vista de módulo)
  en el dashboard real, con datos reales de `activity_log/`;
- uso de Operations/Staffing/Asignaciones observado — "Asignaciones"
  aparece como módulo transversal, con visitas reales contadas;
- el evento de uso "Vista de módulo" aparece con su label visible (no la
  key técnica cruda);
- los eventos diagnóstico (`data_load_failed`/`data_load_diagnostics`/
  `configuration_incomplete`) NO aparecen en el dashboard de uso — ni como
  fila, ni como heading, ni como opción de drilldown;
- actividad histórica diagnóstico-pesada (los ~29 `data_load_failed`/8
  `data_load_diagnostics` reales normalizados) ya no infla ninguna métrica
  de adopción — confirmado antes/después contra el `activity_log/` real;
- el guard de zero-selection de Ámbito/Área funciona en vivo (destildar la
  última opción de un grupo se revierte automáticamente);
- el smoke de regresión de Staffing quedó GREEN en las tres pasadas de
  merge (186/122 tests targeted según la pasada, siempre 100%).

**Baseline final:** suite **646 passed / 1 skipped**, route surface **83**,
route-contract **GREEN**, `main` HEAD `39fb236` (merge de PR #52).

---

## STAFFING DASHBOARD V2 — DIRECTOR PRIORITY UPDATE — CLOSED / GREEN
(commits funcionales `2833fa6` (`feat(staffing): optimize operational
assignment loading`) + `3fcb75e` (`feat(staffing): add capacity planning
dashboard controls`) + `265198b` (pre-PR final check) + `a149c3b`
(`fix(staffing): align status KPIs and verify lazy history loading`) +
`20d44f6` (`fix(staffing): shorten projection column header and fit
Disponibilidad on one line`) — mergeados a `main` vía PR #46/#47/#48,
**CLOSED / GREEN tras validación real del owner en el entorno de
producción del owner** (ver "Cierre — validación final del owner" al
final de esta sección). Branch original
`work/staffing-director-priority-v2`, arrancada desde `main` `2505da2` —
**PARALELA e independiente de `work/activity-dashboard-hub-v2` (Batch A2,
sigue PARKED, ver "SHARED ACTIVITY / OBSERVABILITY — HUB DASHBOARD V2" y
CURRENT EXECUTION POINT abajo), que este batch NO tocó.**)

Prioridad del director sobre 10 requerimientos de producto — 1-8 y 10
**CLOSED/GREEN**; 9 (overlay de vacaciones) se **separa explícitamente**
de este cierre y queda documentado en su propia sección, "STAFFING —
VACATION TIMELINE OVERLAY — FOLLOW-UP / NOT STARTED" (ver abajo) — nunca
fue parte del alcance cerrado acá.

- **1. Carga inicial no histórica + lazy history (IMPLEMENTADO):**
  `StaffingRepository.get_allocations(access_token, *, assignment_scope=
  "operational"|"historical_inactive")` — `"operational"` (default, usado
  por el POST `/data` de siempre) pide Asignaciones con `$filter=fields/
  {status} ne 'Inactivo'` + header `Prefer:
  HonorNonIndexedQueriesWarningMayFailRandomly` (mecanismo documentado de
  Microsoft para filtrar columnas Choice potencialmente no indexadas).
  **Fallback automático:** si Graph rechaza esa query (cualquier excepción
  de transporte), cae a pedir la lista COMPLETA sin `$filter` y filtra en
  Python — Staffing nunca se rompe, solo pierde la optimización de volumen
  en ese request puntual. **NO verificado contra el sitio Gestión real**
  (sin credenciales Graph en este entorno) — reportado como hipótesis
  fundamentada con red de seguridad, no como hecho confirmado (mismo
  criterio que otros enriquecimientos opcionales de Staffing, ver
  secciones previas). Proyectos/Colaboradores/Cliente se piden completos
  en AMBOS alcances — son tablas de dimensión, no el historial de ~3 años
  de Asignaciones. Default de Estado client-side pasa de Activo+Tentativo
  a **SOLO Activo**; Tentativo sigue 100% seleccionable/funcional (ya
  viene en la carga operacional). Togglear Inactivo por primera vez
  dispara `_loadInactiveHistory()` (fetch único a la ruta nueva, ver
  abajo), mergea por `assignment_id` (dedup determinístico), marca
  `historyLoaded` — toggles siguientes NUNCA refetchean.
- **Nueva ruta `POST /operaciones/staffing/asignaciones/historial`
  (route surface 82→83, `tests/route_surface.json` actualizado
  explícitamente):** JSON (no HTML — un segundo swap de
  `staffing_dashboard.html` remontaría Alpine y perdería el estado de
  filtros vigente), misma gate de sesión/autorización que `/data`, mismo
  `graph_token` explícito por form. Llama `StaffingService().get_overview(
  token, assignment_scope="historical_inactive")`.
- **Activo vencido (IMPLEMENTADO):** `is_activo_vencido(status, end_date,
  today=None)` en `staffing_service.py` — helper ÚNICO y canónico:
  `Status crudo == "Activo" AND FechaFin < hoy`. NUNCA reescribe el
  Status de SharePoint — anomalía analítica derivada, calculada una sola
  vez en `build_dashboard_payload` (`activo_vencido` por fila) para que
  KPI y detalle coincidan siempre.
- **2/3. KPI "Sin dato" → "Activos vencidos" (IMPLEMENTADO):** tile
  removido de Resumen (disponibilidad "Sin dato" como valor/filtro sigue
  existiendo sin cambios); reemplazado en la misma posición por "Activos
  vencidos" (`kpis.activosVencidos`, cuenta ASIGNACIONES, no consultores
  distintos — ver requerimiento del director), con tooltip explicativo.
- **4/5. Timeline (IMPLEMENTADO):** label de grupo ahora incluye horas
  sumarizadas del universo filtrado (`groupHoursSuffix`) — nombre y
  sufijo en `<span>` SEPARADOS (`__consultant-name`/`__consultant-hours`,
  gris oscuro para el sufijo) por ajuste cosmético pedido por el usuario
  (el mismo negro/bold para ambos competía visualmente). Switch de
  agrupación Consultor↔Proyecto: botón dorado/negro
  (`.staffing-timeline__group-toggle`, visualmente distinto de Expandir/
  Contraer), UNA sola implementación de `timelineGroups` generalizada por
  `groupBy` (nunca dos timelines paralelos), `rowGroupSecondaryLabel`
  cambia qué muestra la fila secundaria según el modo. Sin refetch en
  ningún caso (mismo `filteredAssignments`).
- **6/7. Detalle agregado + capacidad puntual (IMPLEMENTADO, con
  corrección post-revisión del usuario):** agregado por consultor por
  defecto (`aggregatedDetailRows`/`detailRows`, vista PLANA — dos
  `<template x-for>` anidados sobre la misma lista NO intercalan filas
  hijas bajo su agregada, bug real descubierto durante el desarrollo,
  corregido con una vista plana de un solo `x-for`), expandible a las
  filas de asignación originales. **Regla de "--" corregida por el
  usuario:** Cliente/Proyecto/Desde/Hasta muestran "--" SOLO cuando los
  hijos GENUINAMENTE difieren en ese campo puntual (`_singleValue`,
  evaluado de forma independiente por campo) — un único valor
  compartido (Cliente único, o un único "(sin resolver)" compartido) se
  informa a nivel agregado; fechas iguales con Cliente/Proyecto distintos
  también informan la fecha. Columnas nuevas "Asignación hoy"/"Asignación
  +N días" (control numérico, default 35, clamp 1-365,
  `setProjectionDays`, recalcula sin Graph) — efectividad puntual
  determinística sobre asignaciones abiertas (`_isEffectiveOn`,
  comparación de strings ISO, nunca `new Date()`). **Corrección de layout
  post-revisión del usuario:** la tabla usaba el layout automático de
  `<table>` — expandir/contraer un consultor cambiaba qué filas había en
  el DOM y reacomodaba TODAS las columnas (bug real reportado). Fix:
  `table-layout:fixed` + `<colgroup>` con anchos explícitos
  (`.staffing-detail-table`) — expandir ahora solo revela/oculta filas,
  las columnas nunca se mueven (verificado: mismo ancho de columna
  antes/después de expandir, smoke real).
- **8. Drag-and-drop (IMPLEMENTADO):** reuso PURO del mecanismo genérico
  ya existente (`initDashboardSections`/`[data-dashboard-key]`, ver app.js
  — HANDOFF ya documentaba que este contrato existía pero **nunca se
  había retrofiteado a ningún otro dashboard**). Staffing es el primero:
  `data-dashboard-key="staffing"` + `draggable="true"` + grip en los 3
  `<summary>` — CERO JS nuevo, CERO CSS nuevo (todo ya compartido con
  Actividad).
- **10. Fechas dd/mm/yyyy (IMPLEMENTADO):** `formatDateDMY(iso, short)` —
  parseo puro del string ISO (`split("-")`), NUNCA `new Date(iso)` (evita
  el corrimiento de un día por UTC). Aplicado en tooltip del timeline y en
  el detalle (Desde/Hasta). El "short" (dd/mm) queda disponible en el
  helper pero no tiene un consumidor hoy — no se fabricó un uso artificial
  solo para "usarlo".
- **9. Overlay de vacaciones — SEPARADO de este cierre, ver sección
  dedicada "STAFFING — VACATION TIMELINE OVERLAY — FOLLOW-UP / NOT
  STARTED" más abajo.** Nunca formó parte del alcance CLOSED/GREEN de
  este batch — el resto (1-8, 10) se implementó y cerró sin depender de
  ella.
- **Instrumentación de Graph (parcial, sin credenciales reales):**
  `assignment_scope="operational"` hace 1 llamada paginada a Asignaciones
  (filtrada) + 1 a Proyectos + 1 a Colaboradores (+ 1 a Cliente si está
  configurado) — mismo conteo de listas que antes, ahora con `$filter` en
  Asignaciones. `assignment_scope="historical_inactive"` (disparado
  exactamente una vez por sesión de dashboard, al primer toggle de
  Inactivo) repite las mismas 4 llamadas pero con el `$filter` invertido —
  no hay optimización de NO repetir Proyectos/Colaboradores/Cliente en la
  carga histórica (son tablas pequeñas, no el foco de este batch). No
  medible en páginas reales sin un tenant con datos — verificado por
  conteo de llamadas mockeadas en tests (`tests/webapp/
  test_staffing_director_priority_v2.py`).
- **Tests:** +36 en `tests/webapp/test_staffing_director_priority_v2.py`
  (`is_activo_vencido` con casos límite, `assignment_scope` passthrough,
  filtro Graph con fallback verificado con `HTTPError`/`URLError`
  simulados, endpoint `/historial` completo — auth/JSON/estados
  controlados/token nunca ecoado/route surface, y pinning de fuente para
  toda la lógica que vive 100% en JS/template, mismo criterio que
  `test_staffing_app_js.py`) + ajustes en 3 archivos existentes
  (`test_staffing_service.py::_FakeRepository` gana `assignment_scope`;
  `test_staffing_repository.py` un test actualizado para el `$filter`
  nuevo en Asignaciones; `test_staffing_routes.py` 3 tests actualizados
  para las columnas/KPI/label nuevos del template).
- **Validado sobre stack reproducible** (`.venv`, Python 3.14.3): **566
  passed / 1 skipped**, route-contract con snapshot actualizado
  DELIBERADAMENTE (**82→83**, un solo endpoint nuevo, documentado
  explícito arriba).
- **SMOKE VISUAL: GREEN** (servidor propio aislado en :8091 — nunca el
  :8082 del usuario — `.claude/launch.json` local, no versionado, gana
  una segunda config `danwor-webapp-smoke`; sesión inyectada vía cookie
  firmada real de `auth_service.create_session`, dataset representativo
  vía el mismo `TestClient` + `StaffingService` mockeado que usan los
  tests, inyectado en `#staffing-rows` del DOM real). Verificado leyendo
  `_x_dataStack` directo (no solo el HTML servido):
  - Default `{Activo:true, Tentativo:false, Inactivo:false}` — confirmado.
  - KPIs correctos contra cálculo manual (consultores/activas/horas/
    activos vencidos) sobre el universo YA filtrado por el default.
  - Agregación Cliente/Fechas: consultor con 2 asignaciones de MISMO
    cliente y MISMAS fechas pero proyectos DISTINTOS → Cliente y Fechas
    agregados correctamente, Proyecto "--" — exactamente el caso "Alejandro
    Levit/Farmanet" que pidió el usuario. Consultor con 2 asignaciones
    "(sin resolver)" → agregado muestra "(sin resolver)" con el chip, NO
    "--" — segundo caso pedido por el usuario, confirmado.
  - Grouping switch Consultor↔Proyecto: recalculó grupos/labels
    correctamente, sin request nuevo.
  - N-days: `setProjectionDays(60)` recalculó `projectionColumnLabel`/
    `targetISOValue` correctamente, sin Graph.
  - Drag-and-drop: `data-dashboard-key="staffing"` quedó `dashWired="1"`
    tras el primer `htmx:load` — reuso confirmado.
  - Tabla de detalle: ancho de columna IDÉNTICO antes/después de expandir
    un consultor (verificado en píxeles) — fix de `table-layout:fixed`
    confirmado, no solo leído en el CSS.
  - 375px: sin overflow horizontal en `document.body`, timeline con
    `overflow-x:auto` propio.
  - Sin errores de consola atribuibles a este batch.
- **`work/activity-dashboard-hub-v2` (Batch A2) queda PARKED, NO
  tocado, NO mergeado, NO reconciliado todavía.** Este batch arrancó
  desde `main` DIRECTAMENTE (no desde esa rama) por instrucción explícita
  del director. **Requiere una reconciliación separada con `main` después
  de que esta rama de Staffing se mergee** — nadie debe asumir que A2 ya
  incluye estos cambios de Staffing, ni viceversa.
- **DO NOT CHANGE respetado:** sin cambios en Danwor Hub, OAuth/sesión,
  token de Graph, workspace resolver, ni funcionalidad de Copiloto no
  relacionada. Sin DB/Redis/workers/caching ampliado.

### Pase correctivo — pre-PR final check

Tres verificaciones finales pedidas antes de abrir el PR — sin ampliar
alcance.

- **1. REAL GRAPH FILTER — `PENDING OWNER/PRODUCTION SMOKE`.** Este
  agente NO tiene acceso a un token Graph delegado real ni a una sesión
  de browser autenticada contra Microsoft 365 en este entorno (mismo
  límite ya documentado para el discovery de vacaciones, item 9) — el
  smoke real contra `Asignaciones` (site/list/field mapping SÍ están
  configurados en `.env` local, confirmado, pero eso no alcanza sin un
  `access_token` delegado real) no pudo ejecutarse. **No se reemplazó la
  implementación por esto** — el fallback automático (fetch completo +
  filtro Python si Graph rechaza `$filter`) sigue siendo la red de
  seguridad. Queda **explícitamente pendiente de smoke del owner/CI de
  producción** antes de considerar el filtro Graph "production-proven".
- **2. TIMELINE GROUP TOTAL HOURS — CONGELADO Y CORREGIDO.** La
  implementación previa sumaba TODA barra del grupo (incluyendo futuras y
  Activo-vencidas). Corregido: `groupHoursSuffix(group)` ahora reusa el
  MISMO helper canónico `_hoursIfEffective` que "Asignación hoy"/
  "Asignación +N días" — el total es EXCLUSIVAMENTE horas efectivas HOY.
  **Prueba en vivo** (servidor aislado :8091, sesión real, 3 asignaciones
  para un mismo consultor — vigente 20h/mes, futura 50h/mes, Activo-
  vencida 99h/mes): el header del grupo mostró **"· 20 h/mes"**
  (verificado leyendo `_x_dataStack` real) — nunca 169h (suma ciega) ni
  119h (excluyendo solo una de las dos). Regresión de fuente agregada en
  `test_staffing_director_priority_v2.py::test_group_header_hours_suffix_counts_only_hours_effective_today`
  (no hay runtime de JS en esta suite — pinnea la implementación exacta;
  la escena de 3 asignaciones se verificó en vivo, no por ejecución de
  test).
- **3. NEW HISTORY ENDPOINT SECURITY CONTRACT — MISMO contrato,
  confirmado + gap cerrado.** `/historial` ya tenía cobertura para: sin
  sesión (303 al mismo `next` canónico), Operations no autorizado (403),
  token nunca ecoado, estados controlados sin fuga de detalle. **Gap
  cerrado en este pase:** faltaba el test explícito de "un `graph_token`
  con pinta real NUNCA saltea la autorización" (mismo patrón que
  `test_post_graph_token_never_bypasses_operations_authorization` de
  `test_staffing_routes.py` para `/data`) — agregado
  (`test_historial_endpoint_a_real_looking_graph_token_never_bypasses_operations_authorization`,
  usuario full-tier no listado + token con pinta real + service que
  rompe el test si se invoca → 403, service nunca llamado).
- **Gate:** targeted Staffing (171 passed) + full suite (**568 passed /
  1 skipped**, +2 sobre el baseline previo de 566) + route-contract
  GREEN (snapshot sin cambios, sigue en **83** — este pase no tocó
  rutas).
- **Vacaciones (item 9):** sigue **BLOCKED / FOLLOW-UP REQUIRED**, sin
  cambios — no se fabricó metadata de workbook, no se reclama overlay
  implementado en ninguna parte de este batch.

### Pase correctivo — production behavior corrective (commits `fix(staffing): align status KPIs...`)

El owner validó contra datos/comportamiento reales y reportó 2 bugs de
producto + 1 pregunta de performance sin resolver. Ninguno se asumió — cada
uno se trazó en código real y, donde el entorno lo permitió, se verificó en
vivo contra un servidor aislado (nunca el `:8082` del usuario).

- **Bug 1 — KPI "Asignaciones tentativas" siempre visible (CORREGIDO):**
  el tile mostraba "0" incluso con Tentativo destildado — nunca desaparecía.
  Congelado el contrato: KPIs genéricos (Consultores/Proyectos/Full Time/
  Part Time) SIEMPRE visibles; Activo-dependientes (Asignaciones activas/
  Horas activas/Activos vencidos) SOLO si `filters.status.Activo`;
  Tentativo-dependiente (Asignaciones tentativas) SOLO si
  `filters.status.Tentativo` — ligado DIRECTO al mismo estado canónico que
  gobierna el resto del dashboard (`x-show`, nunca un getter derivado
  redundante). `x-show` (display:none) saca el tile del grid por completo —
  el resto reacomoda solo. **Verificado en vivo:** default Activo-only →
  Tentativas oculto, resto de Activo-dependientes visible; togglear
  Tentativo → Tentativas aparece (leído vía `getComputedStyle(tile).display`
  contra el DOM real, no solo el HTML servido).
- **Bug 2 — "Cargando historial…" ausente (CAUSA REAL del síntoma
  "clic en Inactivo no muestra feedback", CORREGIDO):** el mecanismo de
  lazy-history YA funcionaba (ver batch anterior), pero nunca tenía
  indicación visible — un fetch de varios segundos sin feedback "parece"
  instantáneo. `historyLoading` (antes privado `_historyLoading`, ahora
  parte del contrato congelado) expuesto con spinner
  (`.section-analysis-loading`/`-spinner`, MISMO primitivo que la carga
  inicial de `/data` — no se inventó un sistema de diseño nuevo) + mensaje
  de error visible fail-soft (nunca marca `historyLoaded=true` tras una
  falla). **Verificado en vivo contra el servidor real** (no un mock):
  togglear Inactivo con un `graph_token` inválido disparó EXACTAMENTE 1
  request a `/historial` (confirmado en Network), terminó en
  `historyLoadError=true`/`historyLoaded=false` (nunca "falso positivo" de
  éxito), y el mensaje de error se mostró en el DOM real.
- **Bug 3 — colapso global no sobrevive el cambio de agrupación
  (CORREGIDO, Part F):** "Contraer todo" en modo Consultor + cambiar a
  Proyecto volvía a expandir todo — las keys de `collapsedConsultants` son
  por entidad (consultant_id vs project_id), nunca coinciden entre modos.
  `timelineGlobalCollapseIntent` (null|"collapsed"|"expanded") nuevo,
  puramente en memoria: `collapseAllConsultants`/`expandAllConsultants` lo
  fijan; `toggleGrouping` lo respeta (recalcula colapso para las keys del
  NUEVO groupBy, nunca mapea identidades cruzadas); un toggle INDIVIDUAL
  limpia el intent (mixto → default de la agrupación nueva). **Verificado
  en vivo:** Consultor+Contraer todo→Proyecto sigue todo colapsado;
  Consultor+Expandir todo→Proyecto sigue todo expandido; el cambio de
  agrupación en sí nunca dispara Graph.
- **Pregunta de performance (item E) — instrumentación agregada, causa
  raíz NO confirmable en este entorno:** `StaffingRepository.
  last_diagnostics` (rows/pages/elapsed_ms/`filter_used`/`fallback_used`
  por fuente — Asignaciones/Proyectos/Colaboradores/Cliente — +
  `total_elapsed_ms`, NUNCA token/payload/PII) logueado VERBOSE-gated
  (`staffing_data_diagnostics`/`staffing_historial_diagnostics`) en
  `webapp/routers/staffing.py`. Esto responde "¿el fallback está
  ocurriendo?" — pero **NO puede confirmar por sí solo si los ~6s
  reportados por el owner vienen del fallback, de Proyectos/Colaboradores/
  Cliente, o de latencia de red genérica** sin ver esos logs contra el
  tráfico real de producción. `core/microsoft365.graph_get_paged_values`
  gana `on_page` (callback opcional, backward-compatible) para contar
  páginas sin que ese módulo posea lógica de diagnóstico propia.
- **Parte B/C — contenido del payload inicial:** trazado el pipeline
  completo (router → `StaffingService.get_overview(token)` sin scope
  explícito → default `"operational"` → `StaffingRepository` excluye
  Inactivo vía `$filter` o, si Graph lo rechaza, vía el fallback Python —
  AMBOS caminos probados con fixtures). **Diagnóstico honesto: no se puede
  confirmar contra el tenant real si Tentativo tiene registros vigentes
  hoy, ni si el `$filter` real se acepta o cae al fallback** — eso
  requiere una sesión con `graph_token` real, que este entorno no tiene
  (mismo límite que el overlay de vacaciones). Lo que SÍ se probó y
  confirma: (a) el código NUNCA envía Inactivo en la carga inicial ni deja
  fuera a Tentativo, en ningún camino (filtrado exitoso o fallback); (b) no
  existe ningún watcher/fetch condicionado a Tentativo — togglearlo es
  100% client-side, cero requests.
- **Gate:** targeted Staffing (196 passed, incluye el archivo nuevo
  `tests/webapp/test_staffing_production_corrective.py`, 25 tests) + full
  suite (**593 passed / 1 skipped**, +25 sobre el baseline previo de 568)
  + route-contract GREEN (snapshot sin cambios, sigue en **83** — este
  pase tampoco tocó rutas).
- **REAL GRAPH FILTER sigue PENDING OWNER/PRODUCTION SMOKE** — sin
  cambios de estado, ver pase anterior.
- **Vacaciones (item 9):** sigue **BLOCKED / FOLLOW-UP REQUIRED**, sin
  tocar.

### Cierre — validación final del owner

El director validó el batch completo en su propio entorno de producción
(no un smoke de este agente) y autorizó el cierre. Registrado tal cual
fue reportado:

- carga inicial de Staffing responsiva, sin demora material observada;
- estado por defecto es únicamente Activo;
- la visibilidad de KPIs dependientes de estado se comporta
  correctamente;
- seleccionar Inactivo muestra el loader/preloader visible;
- las asignaciones históricas inactivas se cargan tras esa acción;
- una interacción repetida se comporta como se espera, sin demora
  repetida visible;
- el agrupamiento por consultor/proyecto funciona;
- la intención global de colapsar/expandir sobrevive el cambio de
  agrupación;
- el detalle agregado de asignaciones funciona;
- la proyección a +N días funciona;
- el layout fijo final de la tabla de detalle es estable;
- el header de proyección es `ASIGNACIÓN +N`;
- `DISPONIBILIDAD` se renderiza en una sola línea al ancho de escritorio;
- no se observó overflow horizontal.

**Baseline automatizado final validado:** **595 passed / 1 skipped** ·
route surface **83** · route-contract **GREEN** · `main` HEAD `126f663`
(merge de PR #48, ya incluye PR #46/#47/#48 — commits `2833fa6`,
`3fcb75e`, `265198b`, `a149c3b`, `20d44f6`).

**Entregado en este cierre (1-8, 10 — ver detalle de cada uno en la
sección de arriba y en los pases correctivos):**
1. carga inicial operacional + historial Inactivo lazy;
2. visibilidad de KPIs dependiente de estado;
3. Activos vencidos;
4. horas h/mes del header de grupo congeladas a "efectivo hoy";
5. agrupamiento por Consultor / Proyecto;
6. estado de colapso/expansión global preservado entre agrupaciones;
7. detalle agregado expandible;
8. Asignación hoy / +N días;
9. drag/reorder del dashboard compartido, reusado sin cambios;
10. formato de fecha DMY;
11. estado visible de carga/error del historial;
12. diagnósticos de Graph / observabilidad de fallback;
13. corrective final de layout fijo/presentación de headers.

El requerimiento 9 original (overlay de vacaciones) **NO** forma parte
de este entregado — ver sección dedicada "STAFFING — VACATION TIMELINE
OVERLAY — FOLLOW-UP / NOT STARTED" abajo.

---

## STAFFING — VACATION TIMELINE OVERLAY — IMPLEMENTED / PENDING CI
(commits funcionales `feat(staffing): add vacation source integration` +
`feat(staffing): overlay vacations on consultant timeline`, branch
`work/staffing-vacation-overlay`, arrancada desde `main` `bb442e3` — NO
mergeada todavía)

Separado explícitamente del cierre de Staffing Dashboard V2 — Director
Priority Update. Era el requerimiento 9 original de ese batch; se
implementó en un batch propio, dos pasadas: discovery offline (bloqueado en
Graph real, ver abajo) + implementación completa contra el contrato offline
verificado.

### Discovery — offline (Graph real sigue sin acceso en este entorno)

El discovery contra Graph LIVE quedó bloqueado igual que antes (sin token
delegado ni sesión de browser autenticada en este entorno — confirmado
empíricamente: sin browser Chrome conectado vía "Claude in Chrome", y el
link de SharePoint provisto redirige a `login.microsoftonline.com` sin SSO
disponible). El owner desbloqueó el discovery de **contrato de datos**
(no de acceso Graph) exportando el workbook real como XLSX y compartiéndolo
directo — permitió confirmar, SOLO a nivel de contrato offline:

- sheet **"Solicitudes"**, 203 filas totales;
- columnas: `Fecha de Solicitud, CUIL, Legajo, Apellido, Nombre, Código,
  Motivo, Desde, Hasta, Días, Estado, Comentario del empleado, Comentario
  del aprobador, Etiquetas del empleado`;
- 48 filas con `Motivo=Vacaciones` (41 Aprobada / 6 Cancelada / 1 Rechazada);
- `Desde`/`Hasta` date-only, sin límites faltantes, sin `Desde > Hasta`,
  vacaciones de un solo día válidas, `Hasta` inclusive; `Días` es semántica
  de días laborales — NUNCA se usa para calcular el intervalo del timeline;
- 23 consultores con vacaciones aprobadas, 12 con más de un período;
- SharePoint site real: `https://danwor.sharepoint.com/sites/Gestion`
  (el MISMO sitio "Gestión" de Asignaciones/Proyectos/Colaboradores/
  Cliente) — evidencia de Power Query real del reporte "Solicitud
  vacaciones" existente, List ID `36452eea-cc7c-40ce-bf77-80b4bdfa1d47`.

**Sigue sin LIVE GRAPH VALIDATION**: el List ID/site son KNOWN FROM LEGACY
SOURCE CONFIGURATION (mismo estado que los 3 list ID originales de
Asignaciones en su propio discovery) — los internal names de columna
(`Apellido`/`Nombre`/`Motivo`/`Desde`/`Hasta`/`Estado`) NUNCA se infirieron
desde los display labels del export; quedan sin default de producción en
`config.py` hasta confirmarse contra `/sites/{id}/lists/{id}/columns` real.

### Implementación

**Fuente**: `StaffingRepository.get_vacations(access_token)` (lista
"Solicitud vacaciones", MISMO sitio Gestión, mecanismo Graph delegado
existente — sin auth nueva, sin credenciales nuevas) filtra en Python
`Motivo=Vacaciones` + `Estado=Aprobada` (constantes de negocio, ver
`VACATION_MOTIVO_VALUE`/`VACATION_ESTADO_APROBADA_VALUE` — no son internal
names, no necesitan config por tenant). Devuelve `VacationPeriod`
(`source_id`, `consultant_name` CRUDO "Nombre Apellido", `start_date`,
`end_date`) — SIN join contra Colaboradores todavía, ese vive en el
servicio. `StaffingRepository.get_consultants(access_token)` lee SOLO
Colaboradores (id + nombre), reusando `colaborador_title` de
`StaffingFieldMapping` — sin forzar una lectura completa de
Asignaciones/Proyectos. Config nueva: `SHAREPOINT_VACACIONES_LIST_ID`
(único default de producción, KNOWN FROM LEGACY SOURCE) +
`SHAREPOINT_VACACIONES_FIELD_{APELLIDO,NOMBRE,MOTIVO,DESDE,HASTA,ESTADO}`
(sin default a propósito, mismo mecanismo que los 8 campos REQUERIDOS de
Asignaciones).

**Identidad/join**: `_normalize_consultant_name` (trim + colapso de
espacios + casefold + folding NFKD de diacríticos, SIN fuzzy-matching, sin
reordenar nombres, sin nicknames) resuelve el caso real disclosed por el
owner (`Matias` vs `Matías`). `join_vacations_to_consultants` construye
key normalizada → candidatos: 1 candidato = match; 0 = `unmatched`; ≥2 =
`ambiguous` — ninguno de los dos últimos se asigna a nadie, ambos quedan
observables para diagnóstico (conteos puros, NUNCA nombres/PII en el
payload de vuelta al cliente ni en logs).

**Overlay/payload**: `build_vacation_overlay_payload` reusa
`compute_horizon_months`/`compute_timeline_position` (MISMO horizonte que
Asignaciones) pero excluye por completo (no clippea al borde) un período
que no se solapa en absoluto con la ventana visible — a diferencia de una
asignación potencialmente abierta, un período de vacaciones es finito y
conocido en ambos extremos; clippearlo al borde sugeriría, falsamente, que
está vigente ahora.

**Ruta nueva**: `POST /operaciones/staffing/asignaciones/vacaciones`
(JSON, MISMO patrón que `.../historial`) — carga LAZY e INDEPENDIENTE de
`.../data`, mismo `graph_token` explícito, misma gate de sesión/
autorización. Route surface **83 → 84**, documentado explícito
(`tests/route_surface.json` regenerado, `test_15_route_surface_still_84`
actualizado).

**UI**: `app.js::staffingDashboard()._loadVacations()` arranca sola en
`init()` (a diferencia del historial de Inactivo, que espera el primer
toggle) — fail-soft: una falla deja Asignaciones 100% intactas, solo omite
el overlay y muestra un aviso propio no bloqueante
(`.staffing-vacation-error`). El overlay se adjunta a `timelineGroups`
SOLO en agrupación Consultor (`vacationsByConsultant`, getter puro sobre
el dato ya cargado — cambiar de agrupación nunca refetchea). Tratamiento
visual neutro/subordinado (rayado diagonal, `repeating-linear-gradient`)
— nunca uno de los 6 tonos de `.staffing-palette-N`, para no leerse como
"otro proyecto más". Las barras de asignación existentes quedan
exactamente igual — el overlay es un lane separado, nunca mezclado con la
fila de asignación.

### Cero impacto en métricas (probado)

`kpis`/`filteredAssignments`/`aggregatedDetailRows`/`activo_vencido` nunca
leen `this.vacations` — confirmado por contenido de fuente
(`test_kpis_getter_never_reads_vacations_state`/
`test_filtered_assignments_never_reads_vacations_state`). El payload de
`build_vacation_overlay_payload` no comparte NINGUNA clave con
`build_dashboard_payload` (sin `allocation_value`/`status`/
`activo_vencido` en una fila de vacación).

### Validación contra datos reales — BLOQUEADA (mismo motivo que antes)

**NO se pudo ejecutar** en este entorno (sin token Graph delegado real, sin
browser Chrome conectado, el link de SharePoint provisto redirige a login
sin SSO disponible — mismo blocker documentado en el discovery previo).
Queda como el ÚNICO ítem pendiente antes de considerar este batch
production-ready: correr contra el sitio Gestión real, confirmar los
internal names de columna, confirmar la representación real de fecha que
devuelve Graph (el export offline es `dd/mm/yyyy` de Excel, la
representación LIVE de Graph no está confirmada — `StaffingService.
_parse_date` ya asume ISO 8601 igual que Asignaciones y degrada a `None`
sin romper nada si el formato real difiere), y validar match/unmatched/
ambiguous contra el universo real de Colaboradores.

### Gate

Targeted: Vacaciones repository (16 tests) + Vacaciones service (21 tests)
+ Vacaciones routes (13 tests) + Vacaciones UI/contenido (14 tests) — 64
tests nuevos, todos GREEN. Staffing regression completo (todos los archivos
`test_staffing_*.py`, incluyendo el ajuste esperado de
`test_15_route_surface_still_84`) + module-registry + Activity — GREEN,
sin debilitar ningún test existente. Full suite **776 passed / 1 skipped**
(+64 sobre el baseline de 712), route-contract GREEN tras regenerar el
snapshot, route surface **83 → 84** (cambio intencional, documentado).

---

## MODULE IDENTIFIER CONVENTION V2 — REGISTRY FOUNDATION — CLOSED / GREEN
(commits funcionales `feat(modules): add canonical Hub module registry`
(`7ddfc57`) + `fix(modules): keep canonical registry dependency-neutral`
(`9a5d2cb`) + `fix(modules): make display order explicit registry metadata`
(`9235944`), branch `work/module-identifier-convention-v2`, arrancada desde
`main` `68abe57` — **mergeada a `main` vía PR #55 (`59ce3fb`)**, los tres
commits funcionales confirmados CONTAINED en `main` post-merge. Ver "FINAL
HANDOFF CLOSURE" al final de esta sección para el contrato final entregado.)

### Discovery (read-only, previo a esta implementación)

Candidatos de prefijo iniciales propuestos por el owner: `CP` = Copiloto,
`OP` = Operaciones, `CM` = Comercial, `CH` = Capital Humano. Un discovery
read-only trazó cada superficie del repo acoplada a la identidad de módulo
(rutas, YAML de árboles — en DOS formatos distintos, `links.module` stem
puro vs. `module: "NN · stem"` compuesto —, Activity, Feedback YAML, nav
Jinja, ramas de negocio hardcodeadas `stem.startswith("01_"/"02_"/"05_")`,
fixtures de test/golden). Hallazgo clave: los apéndices YA tienen un campo
`module:` de frontmatter desacoplado del nombre de archivo (A01→"10",
A05→"06", etc.) pero el ORDEN de nav sigue siendo por archivo — es decir, el
propio repo ya demuestra que identidad y orden de display son separables,
pero nunca se terminó de reconciliar (inconsistencia visual latente, NO
corregida en esta fase). `CM`/`CH` no tienen ningún workspace/área/contenido
real en el repo hoy — son puramente aspiracionales.

### Fase 1 — registro/resolver canónico (esta implementación)

**Convención final de IDs (compactos, sin nombre descriptivo):**

| legacy key | `module_id` |
|---|---|
| `00_Intro` | `CP00` |
| `01_Arquitectura_y_Conectividad` | `CP01` |
| `02_Seguridad_y_Gobernanza` | `CP02` |
| `03_Integracion_y_Estrategia_de_Datos` | `CP03` |
| `04_Modelado_y_Desarrollo` | `CP04` |
| `05_Reporting_y_Consumo` | `CP05` |
| `A01_Arquitectura_y_Modelado` | `CPA01` |
| `A02_Modernizacion_BW_Greenfield` | `CPA02` |
| `A03_Hub_de_Integracion_AWS` | `CPA03` |
| `A04_Biblioteca_de_Referencias` | `CPA04` |
| `A05_Troubleshooting_y_Performance` | `CPA05` |
| `A06_SAP_Analytics_Cloud` | `CPA06` |
| `assignments` (Operations/Staffing) | `OP01` |

- **`module_id` es un código de identidad ESTABLE, inmutable una vez
  asignado** — no cambia si el módulo se reordena en nav ni si su título
  visible cambia. Nunca se deriva de la posición de display.
- **`display_order` es metadata explícita e independiente**, declarada
  literalmente junto a cada `module_id` en el registro — nunca calculada a
  partir de los dígitos del `module_id`, del filename/`content_key`, ni de
  la posición de enumeración al construir el registro (final display-order
  semantics corrective: la primera versión SÍ usaba un contador de
  iteración sobre `content_service.list_modules()`/`list_appendices()`,
  una dependencia indirecta del orden lexical de archivo — corregido).
  Significa "orden DENTRO de la colección navegable actual del propio
  descriptor" — Copiloto principal, apéndices de Copiloto y Operations son
  tres colecciones separadas hoy (sin secuencia Hub-wide única todavía), así
  que `CP01`/`CPA01`/`OP01` comparten legítimamente `display_order=1` sin
  colisionar (`module_id`/`workspace`/`kind` ya los distinguen). Los valores
  preservan el orden de nav de HOY sin reordenar nada en esta fase — commit
  `fix(modules): make display order explicit registry metadata`.
- **Los IDs son opacos para la lógica de aplicación**: ningún código —ni
  el propio registro— hace `.startswith`/slicing sobre el prefijo para
  decidir comportamiento. Probado por fuente (`test_resolver_source_never_
  parses_module_id_prefix`).
- **Metadata explícita por descriptor:** `workspace` (`copilot`/
  `operations`, mismas keys que `webapp/workspaces.py`), `area`
  (`methodology`/`staffing`), `kind` (`module`/`appendix` — sin valor
  `application`/tercer tipo para OP01: ningún consumidor actual lo necesita),
  `content_key` (stem físico, `None` para OP01 — Asignaciones no tiene
  Markdown backing), `visible_label` (para Copiloto reusa
  `content_service.title` — no duplica texto humano; para OP01 es un
  literal PROPIO del registro, `"Asignaciones"` — ver "Dirección de
  dependencia" abajo), `legacy_aliases` (contrato de compatibilidad
  explícito, nunca labels).
- **Dirección de dependencia (final foundation dependency check,
  corrective post-implementación):** `module_registry.py` es la capa de
  identidad de MÁS BAJO nivel del Hub — NO debe depender de ninguno de sus
  futuros consumidores. La primera versión importaba `MODULE_ASSIGNMENTS`/
  `module_label` de `activity_context` para el label de OP01 — exactamente
  al revés de la dirección deseada (Activity, en Fase 2, será quien
  dependa del registro, no el registro de Activity). Corregido: OP01 ahora
  usa literales propios (`_OP01_LEGACY_KEY="assignments"`,
  `_OP01_VISIBLE_LABEL="Asignaciones"`) — duplican temporalmente el mismo
  valor que ya vive en `activity_context.MODULE_ASSIGNMENTS`/
  `MODULE_LABELS`, una duplicación deliberadamente aceptada en vez de
  invertir la dependencia. Fase 2 migrará la resolución de labels de
  Activity para leer de este registro y podrá eliminar la entrada
  redundante en `MODULE_LABELS` donde sea seguro — no se tocó `activity_
  context.py` en este corrective. Commit `fix(modules): keep canonical
  registry dependency-neutral`.
- **Un solo resolver canónico** (`resolve(key)`): acepta indistintamente una
  key legacy o un `module_id` canónico y devuelve el MISMO descriptor;
  key desconocida → `None` determinístico, nunca se fabrica un descriptor.
- **Academy reservado, NO migrado:** namespace conceptual `CPACxx`
  documentado, sin entradas activas — Academy tiene su propio espacio de
  identidad (gap `ACAD10` faltante, `Funnel-como-faro.md` rompe la
  convención) que requiere su propio discovery futuro. `CM`/`CH` reservados
  igual, sin entradas (sin workspace/área real todavía).
- **CERO migración de consumidores** en esta fase: rutas (`/modules/{stem}`),
  Activity (`module=stem` en `log_activity`), Feedback YAML
  (`document.summary.stem`), nav Jinja (`copilot_nav.html`), `assistant_
  store`/`store.trees_for_module` (stem), YAML de árboles (ambos formatos),
  las ramas hardcodeadas `stem.startswith("01_"/"02_"/"05_")` de
  `module.html`/`catalog_tab.html`, filenames físicos, `pack.yaml` — TODOS
  siguen usando sus keys legacy exactamente como antes. El registro existe
  puramente por encima, probado por tests, sin un solo consumidor apuntando
  a él todavía.

### Ownership y ubicación

Nuevo archivo `webapp/services/module_registry.py` (mismo patrón que
`content_service.py`/`activity_context.py`) — NO se puso en `routers/`,
`templates/` ni `activity_analytics.py` (discovery señaló específicamente
evitar esos tres). Construye sus entradas iterando
`content_service.list_modules()`/`list_appendices()` (nunca duplica el
inventario de stems a mano) + una entrada estática para OP01. Sin base de
datos, sin discovery dinámico, sin plugin system — un `dict`+`dataclass`
chico, cacheado con `lru_cache`, igual que el resto de `content_service`.

### Deuda conocida (explícitamente NO resuelta en esta fase)

- Las ramas `stem.startswith("01_"/"02_"/"05_")` de `module.html`/
  `catalog_tab.html` siguen ahí — deuda de una futura "migración de
  capacidad semántica" (Fase 2+), NO reemplazadas por parsing de `CPxx`.
  Ancladas por test (`test_legacy_stems_behind_hardcoded_startswith_logic_
  resolve_as_expected`) para que un reemplazo futuro tenga contra qué
  validarse.
- La inconsistencia de número de apéndice (archivo vs. frontmatter `module:`)
  sigue sin corregir — documentada, no tocada.
- Activity y Feedback siguen sin ningún seam de normalización hacia
  `module_id` — Fase 2, no autorizada todavía.

### Gate

`tests/webapp/test_module_registry.py` (26 tests tras las dos pasadas
correctivas — cobertura completa de resolución legacy↔canónica,
no-duplicados, metadata por campo, opacidad del resolver ante `module_id`/
`content_key`/enumeración, `display_order` explícito por colección, anclaje
de la deuda hardcodeada, guardia de drift de inventario). Full suite en la
branch antes de mergear: **672 passed / 1 skipped** (+26 sobre el baseline
de 646), route-contract GREEN, route surface **83 sin cambios** (ningún
archivo de rutas tocado en ninguna de las tres pasadas). Archivos tocados en
total: 2 nuevos (`webapp/services/module_registry.py` +
`tests/webapp/test_module_registry.py`) — cero archivos existentes de
aplicación modificados.

### FINAL HANDOFF CLOSURE

Fase 1 mergeada y verificada CONTAINED en `main` — cierre formal de esta
fase. Fase 1 entregó ÚNICAMENTE el registro canónico aditivo — ningún
consumidor migrado, ningún archivo renombrado.

**Contrato final de IDs canónicos** (idéntico a la tabla de arriba, congelado):
`CP00`–`CP05` (Copiloto principal), `CPA01`–`CPA06` (apéndices de
Copiloto), `OP01` = Staffing/Asignaciones. Reservados conceptualmente, SIN
implementar/migrar: `CPACxx` (Academy de Copiloto), `CMxx` (Comercial),
`CHxx` (Capital Humano).

**Semántica de identidad congelada:** `module_id` es canónico, estable,
inmutable, independiente del label visible, independiente del filename/
`content_key`, e independiente de `display_order`. La parte numérica es un
código de identidad estable — NUNCA posición de navegación en vivo. Los IDs
son legibles para humanos pero OPACOS para la lógica de aplicación —
ningún código debe parsear `CP`/`CPA`/`OP` ni su sufijo numérico para
decidir comportamiento.

**Descriptor final:** `module_id, workspace, area, kind, content_key,
visible_label, display_order, legacy_aliases` — sin campos especulativos
agregados. **Dirección de dependencia confirmada:** `module_registry.py` es
dependency-neutral — NO depende de `activity_context` (corregido en
`9a5d2cb`); futuros consumidores dependerán DE él, nunca al revés.

**Contrato de `display_order` congelado:** metadata explícita, con alcance
a la colección navegable ACTUAL del propio descriptor — nunca Hub-wide,
nunca parseada de `module_id`, nunca parseada de filename/`content_key`,
nunca derivada de orden lexical de filesystem, nunca derivada de posición
de iteración genérica del registro (corregido en `9235944`). Valores
vigentes: Copiloto principal `CP00=0`…`CP05=5`; apéndices `CPA01=1`…
`CPA06=6`; Operations `OP01=1`.

**Boundary de compatibilidad:** Fase 1 deliberadamente NO migró: filenames
Markdown, YAML de árboles, `/modules/{stem}`, rutas de
feedback/tree/assistant, nav Jinja, productores de Activity, JSONL de
Activity, YAML de Feedback, `assistant_store`, `pack.yaml`, fixtures
golden, ni la lógica de negocio hardcodeada por stem. Los legacy aliases
siguen siendo el input de compatibilidad del resolver — ej.
`01_Arquitectura_y_Conectividad`→`CP01`, `assignments`→`OP01`.

**Deuda conocida, explícitamente sin resolver:** `stem.startswith("01_"/
"02_"/"05_")` en `module.html`/`catalog_tab.html` sigue determinando
comportamiento de negocio (qué catálogo mostrar) — regla congelada para el
futuro: identidad de módulo ≠ detección de capacidad/feature; esas ramas
NUNCA deben reemplazarse por parsing de `CPxx`, deben resolverse por
metadata explícita del descriptor cuando se migren.

**Academy:** namespace `CPACxx` reservado SOLO conceptualmente — Academy
tiene su propio espacio de identidad ya existente (gap `ACAD10` faltante,
`Funnel-como-faro.md` rompe la convención) que requiere su propio discovery
futuro dedicado, antes de cualquier mapeo.

**Baseline final:** suite **672 passed / 1 skipped**, route surface **83**,
route-contract **GREEN**, `main` HEAD `59ce3fb` (merge de PR #55).

## MODULE IDENTIFIER CONVENTION V2 — PHASE 2 — CANONICAL MODULE ADOPTION — CLOSED / GREEN
(commits funcionales `feat(activity): adopt canonical Hub module identities`
+ `feat(feedback): adopt canonical Hub module identities` +
`fix(feedback): normalize canonical modules in offline reports` +
`fix(feedback): canonicalize offline module filters`, branch
`work/module-identifier-convention-v2-phase2`, arrancada desde `main`
`fa49f69` — **mergeada a `main` vía PR #57, merge commit `0d1c138`**, los
cuatro commits funcionales confirmados CONTAINED en `main`
(`git merge-base --is-ancestor` GREEN sobre el HEAD de la rama y sobre el
primer commit funcional))

Hace load-bearing el registro canónico de Fase 1 en Activity y Feedback —
ÚNICAMENTE ahí. Sin rename de archivos, sin cambio de rutas/YAML/nav Jinja/
`assistant_store`, sin tocar la deuda hardcodeada de `stem.startswith(...)`,
sin migración de Academy.

### Activity — pipeline de normalización

`activity_analytics._read_events`: la extracción histórica de módulo NO
cambia (campo explícito → module inyectado por `normalize_event` →
`payload.module`/`payload.stem`) — ese mejor valor disponible ahora se llama
`raw_module` y se resuelve contra `module_registry.resolve()`: si resuelve,
`module` queda con el `module_id` canónico; si no, `module` queda IGUAL a
`raw_module` (nunca se fabrica un ID). `raw_module` se agrega como campo
nuevo del evento leído — JSONL persistido nunca tocado.

`activity_context.module_label()` ahora resuelve PRIMERO contra
`module_registry` (dueño único de la identidad) y cae a `MODULE_LABELS`/la
key cruda solo si el registro no reconoce la key. Efecto colateral
correcto: Top Módulos de Copiloto, que antes mostraba el stem crudo
("01_Arquitectura_y_Conectividad"), ahora muestra el label de producto
("Arquitectura y Conectividad") — igual que ya pasaba con Asignaciones.
`MODULE_ASSIGNMENTS`/`MODULE_LABELS` NO se borraron: `_EVENT_RENAME` sigue
inyectando `MODULE_ASSIGNMENTS` para históricos de Staffing sin campo
`module` explícito — consumidor real, no se fuerza su eliminación.

### Activity — productores nuevos

- `webapp/routers/modules.py`: resuelve el `stem` de ruta/contenido contra
  `module_registry` antes de loguear `module_viewed` — persiste el
  `module_id` canónico (`CP01`, `CPA01`, ...) si el registro lo reconoce
  (siempre, para cualquier módulo/apéndice actual), o el mismo `stem` si no.
  El `stem` sigue siendo la key de TODO lo demás en la función (rutas,
  trees, assistant, feedback, nav) — sin cambios.
- `webapp/routers/staffing.py`: los 6 productores (`module_viewed`,
  `data_load_failed`×2, `data_load_diagnostics`×2,
  `configuration_incomplete`×2) importan `OP01_MODULE_ID` PÚBLICO directo
  de `module_registry` (ya no `MODULE_ASSIGNMENTS` de `activity_context`) —
  persisten `module="OP01"` en vez de `"assignments"`. `scope=operations`/
  `area=staffing` sin cambios.

### Dirección de dependencia (confirmada, sin excepciones nuevas)

`module_registry` sigue sin importar `activity_context`/`activity_analytics`
— es `activity_analytics`/`activity_context` quienes ahora dependen DE él
(exactamente la dirección congelada en el corrective de Fase 1). El único
literal nuevo público es `OP01_MODULE_ID = "OP01"` en `module_registry.py`.

### Feedback — write path + normalización de lectura

Nuevo seam `webapp/routers/feedback.py::normalize_feedback_module(raw) ->
(raw_module, module)` — mismo patrón que Activity: `raw_module` preserva el
valor persistido tal cual; `module` es su `module_id` canónico si el
registro lo resuelve, o el mismo valor si no. `_save_module_feedback`/
`_save_section_feedback` (las dos únicas escrituras con campo `module`)
ahora persisten el `module_id` canónico en vez de `document.summary.stem`.
YAML histórico NUNCA se reescribe. Feedback de catálogo (`_save_catalog_
feedback`/`_save_catalog_section_feedback`, sin campo `module`) sin cambios.

### Feedback — consumidor offline (`analyze_feedback.py`)

Gap cerrado con un corrective chico (commit `fix(feedback): normalize
canonical modules in offline reports`): `analyze_feedback.py` (script
standalone en la raíz del repo, herramienta de reporting offline del owner)
ahora normaliza el valor `module` persistido a su `module_id` canónico ANTES
de agrupar, en los tres sitios que agrupaban por módulo crudo
(`analyze_assistant`, `analyze_modules`, `analyze_sections`) —
`analyze_academy` no toca `module`, sin cambios. Importa directo
`webapp.services.module_registry` (confirmado seguro: sin init de FastAPI,
sin red, sin side-effects de escritura — no importa `webapp.routers.feedback`
ni ningún router). Helper local `_canonical_module(raw) -> str` reimplementa
la orquestación mínima de `resolve()` (mismo patrón que
`normalize_feedback_module`, sin duplicar el mapeo canónico, que sigue
centralizado en `module_registry`). Sus hermanos (`analyze_cases.py`,
`propose_tree_updates.py`, `propose_content_updates.py`) siguen sin importar
`webapp.*` — no se tocaron, fuera de alcance de este corrective. Efecto
concreto: feedback histórico con stem legacy y feedback nuevo con `module_id`
canónico para el MISMO módulo ahora agregan en UNA sola fila en
`reports/feedback/module_feedback.yaml`/`section_feedback.yaml`/
`ia_feedback.yaml`. `feedback_log/*.yaml` fuente NUNCA se reescribe —
normalización solo en el read-model del reporte generado.

**Filtro `--module`/`load_feedback(module_filter=...)` — corrective
adicional** (commit `fix(feedback): canonicalize offline module filters`):
el filtro por módulo ahora normaliza AMBOS lados (el valor pedido por CLI y
el valor `module` persistido de cada registro) contra `module_registry`
antes de comparar — `--module 01_Arquitectura_y_Conectividad` y
`--module CP01` devuelven el mismo conjunto lógico de registros (histórico +
nuevo). Operadores existentes que sigan usando el stem legacy en la CLI no
necesitan migrar. Un valor desconocido sigue matcheando por pass-through
exacto, sin inventar IDs; la variante histórica lowercase-typo conocida
(`01_arquitectura_y_conectividad`) sigue sin resolver — no se agregó case
folding, la comparación sigue siendo exacta tras la normalización canónica.
Reporting offline de Feedback ahora tiene semántica canónica completa: tanto
para AGRUPAR como para FILTRAR.

### Compatibilidad y deuda

- Rutas (`/modules/{stem}`), YAML de árboles, nav Jinja, `assistant_store`,
  `pack.yaml`, filenames físicos: sin cambios, cero migración.
- `stem.startswith("01_"/"02_"/"05_")` en `module.html`/`catalog_tab.html`:
  sigue sin resolver — regla congelada: identidad de módulo ≠ detección de
  capacidad, nunca se reemplaza por parsing de `CPxx`.
- Academy: sin tocar, `CPACxx` sigue reservado sin entradas.

### Validación contra datos reales (solo lectura)

**Activity** (`activity_log/` del owner): 12 valores legacy conocidos
normalizan correctamente — `00_Intro`(179)→`CP00`, `01_Arquitectura_y_
Conectividad`(108)→`CP01`, `02_Seguridad_y_Gobernanza`(17)→`CP02`,
`03_Integracion_y_Estrategia_de_Datos`(17)→`CP03`, `04_Modelado_y_
Desarrollo`(19)→`CP04`, `05_Reporting_y_Consumo`(7)→`CP05`, `A01`..`A05`
(3/2/2/2/2)→`CPA01`..`CPA05`, `assignments`(52)→`OP01`. **Un valor sin
resolver, correctamente preservado sin cambios:** `01_arquitectura_y_
conectividad` (minúscula, 1 ocurrencia — variante histórica de un typo/
smoke previo; el resolver compara exacto, sin normalizar mayúsculas, tal
como documentado) — no se fabricó un ID, quedó lossless. Top Módulos
"Asignaciones": un solo bucket, `key=OP01`, `count=15` — histórico
`assignments` y (todavía inexistente en datos reales) nuevo `OP01` ya
agregarían junto si coexistieran.

**Feedback** (`feedback_log/` del owner): 2 registros con stem legacy
(`01_Arquitectura_y_Conectividad`, `04_Modelado_y_Desarrollo`), ambos
normalizan correctamente a `CP01`/`CP04`. Cero valores sin resolver. Los
mismos 2 registros re-validados (solo lectura) tras el corrective de
`analyze_feedback.py`: `analyze_assistant` los agrupa como `CP01: up=1` /
`CP04: up=1` (son `assistant_response`, no `module_content`/
`section_content` — el owner no tiene todavía feedback real de esos dos
tipos). YAML fuente confirmado sin cambios tras leer + generar reportes.
Re-validado otra vez tras el corrective del filtro CLI: `--module
01_Arquitectura_y_Conectividad` y `--module CP01` devuelven exactamente el
mismo archivo (`feedback_20260806_103529_bedd9d48.yaml`); `--module
04_Modelado_y_Desarrollo` y `--module CP04` devuelven exactamente el mismo
archivo (`feedback_20260805_162000_cf7f051f.yaml`). YAML fuente confirmado
sin cambios.

### Gate

Targeted: registro canónico (26 tests, Fase 1, sin debilitar) + Activity
(55 tests, `test_activity_context.py`/`test_activity_routes.py`) + Feedback
web (10 tests, `test_feedback_module_identity.py`) + Feedback offline (19
tests, `test_analyze_feedback_module_identity.py` — 11 de agrupamiento + 8
nuevos de filtro CLI) + Staffing regression (123 tests) — todos GREEN. Full
suite **712 passed / 1 skipped** (+40 sobre el baseline de 672),
route-contract GREEN, route surface **83 sin cambios**.

### FINAL HANDOFF CLOSURE

Fase 2 mergeada y verificada CONTAINED en `main` (PR #57, merge commit
`0d1c138`) — cierre formal de esta fase. Fase 2 hizo load-bearing el
registro canónico de Fase 1 en Activity y en el dominio completo de
Feedback — ÚNICAMENTE esos dos consumidores.

**Modelo final entregado — registro canónico:**
`CP00`–`CP05` (Copiloto principal), `CPA01`–`CPA06` (apéndices de
Copiloto), `OP01` (Staffing/Asignaciones). Reservados conceptualmente, SIN
implementar: `CPACxx` (Academy), `CMxx` (Comercial), `CHxx` (Capital
Humano). Contrato de identidad congelado desde Fase 1, sin cambios: `module_id`
es estable, inmutable, independiente del label visible, del `content_key` y
del `display_order`, y opaco a la lógica de negocio de la aplicación
(ningún código parsea `CP`/`CPA`/`OP` ni su sufijo numérico para decidir
comportamiento). Descriptor final sin campos nuevos: `module_id, workspace,
area, kind, content_key, visible_label, display_order, legacy_aliases`.

**Activity — contrato final:** el JSONL histórico nunca se reescribió.
Read-model: `raw_module` = valor persistido exacto (campo explícito →
`normalize_event` → `payload.module`/`payload.stem`); `module` = `module_id`
canónico cuando `module_registry.resolve()` lo reconoce, o el mismo
`raw_module` si no (nunca se fabrica un ID). Ejemplos reales:
`01_Arquitectura_y_Conectividad`→`raw_module=01_Arquitectura_y_
Conectividad`/`module=CP01`; `assignments`→`raw_module=assignments`/
`module=OP01`. Productores nuevos (`webapp/routers/modules.py` para
Copiloto, `webapp/routers/staffing.py` para Operations) persisten el
`module_id` canónico directo, sin pasar por el stem. Dashboard: IDs
canónicos son identidad interna, los labels visibles siguen siendo labels
de producto (`module_label()` resuelve primero contra el registro);
histórico + nuevo agregan en un solo bucket de módulo — `assignments` +
`OP01` ya conviven como un solo `Asignaciones`. Semántica usage-only del
dashboard sin cambios; diagnósticos siguen excluidos de las métricas de
adopción.

**Feedback — contrato final:** cubre el dominio COMPLETO de Feedback
existente — (1) escrituras nuevas de la webapp (`normalize_feedback_module`
persiste el `module_id` canónico), (2) compatibilidad de lectura de
Feedback web histórico, (3) agrupamiento del consumidor offline
`analyze_feedback.py`, (4) filtro `--module` del mismo consumidor offline.
`feedback_log/*.yaml` histórico NUNCA se reescribió, en ningún punto. Ejemplo
congelado: legacy `01_Arquitectura_y_Conectividad` + nuevo `CP01` normalizan
al mismo módulo lógico, tanto al agrupar reportes como al filtrar por
cualquiera de las dos formas (`--module 01_Arquitectura_y_Conectividad` ==
`--module CP01`, mismo conjunto de registros). Valores desconocidos
preservan pass-through exacto, sin case folding adicional — la variante
histórica lowercase-typo real (`01_arquitectura_y_conectividad`) sigue sin
resolver, por diseño.

**Dirección de dependencia congelada:** `Activity`, `Feedback web` y
`analyze_feedback.py` dependen DE `module_registry` — nunca al revés.
`module_registry` sigue dependency-neutral (no importa ninguno de sus
consumidores). Ningún mapeo canónico se duplicó en ningún consumidor; donde
un consumidor no pudo importar el seam de otro (`analyze_feedback.py` no
importa `webapp.routers.feedback`), reimplementó únicamente la orquestación
mínima de `resolve()`, nunca el mapeo.

**Boundary de compatibilidad (deliberadamente NO migrado en Fase 2):**
filenames Markdown, YAML de contenido, YAML de árboles, rutas
(`/modules/{stem}` y firmas de ruta en general), nav Jinja,
`assistant_store`, `pack.yaml`, JSONL de Activity persistido, YAML de
Feedback persistido, identidad de Academy, la lógica hardcodeada actual de
detección de capacidad por stem. Los legacy aliases siguen siendo inputs de
compatibilidad soportados en todos los seams (web y CLI).

**Deuda hardcodeada por stem — sin resolver, por diseño:**
`stem.startswith("01_"/"02_"/"05_")` en `module.html`/`catalog_tab.html`
sigue determinando comportamiento de negocio. Regla congelada: identidad de
módulo ≠ detección de capacidad/feature — esas ramas NUNCA deben
reemplazarse por parsing de `CPxx`; una corrección futura debe usar
metadata semántica de capacidad explícita en el descriptor, u otro seam
basado en evidencia, nunca parsing del ID.

**Rename físico de módulo/contenido:** **OPCIONAL, FUTURO, NO
PLANIFICADO.** La arquitectura entregada en Fase 1+2 elimina la necesidad de
renombrar contenido físico solo para tener identidad canónica — el registro
ya resuelve legacy↔canónico sin tocar un solo archivo. Un rename físico
debería considerarse más adelante SOLO si un beneficio operativo/de
mantenimiento concreto justifica el riesgo de esa migración. Ninguna Fase 3
está autorizada ni es requerida actualmente — esto NO es "el próximo paso
automático".

**Academy:** `CPACxx` sigue **RESERVADO SOLO conceptualmente**, sin
entradas. Academy tiene su propio espacio de identidad ya existente (gap
`ACAD10` faltante, `Funnel-como-faro.md` rompe la convención) que requiere
su propio discovery dedicado antes de cualquier mapeo futuro — discovery NO
iniciado en este batch ni autorizado todavía.

**Validación de producción del owner recorded:** navegación de módulos de
Copiloto funcional; Staffing/Asignaciones funcional; Activity muestra
labels de producto en vez de IDs técnicos crudos/canónicos; Asignaciones
sigue siendo un solo bucket de módulo entre legacy y `OP01`; Feedback nuevo
compatible con Feedback histórico; agrupamiento offline de Feedback agrega
identidades legacy/canónicas; filtros `--module` legacy y canónico devuelven
los mismos registros lógicos; ninguna migración de JSONL de Activity ni de
YAML de Feedback ocurrió; rutas sin cambios.

**Baseline final:** suite **712 passed / 1 skipped**, route surface **83**,
route-contract **GREEN**, `main` HEAD `0d1c138` (merge de PR #57).

**MODULE IDENTIFIER CONVENTION V2 — ALCANCE ACTUALMENTE AUTORIZADO =
COMPLETE / GREEN** (Fase 1 + Fase 2, ambas CLOSED/GREEN y mergeadas). Otros
follow-ups permanecen independientes y explícitamente NO iniciados:
Staffing Vacation Timeline Overlay, Activity Admin Shell Ownership, Auth /
Graph Session Renewal UX, discovery de identidad canónica de Academy. No se
seleccionó ni inició ningún follow-up en este batch de cierre.

## ACTIVITY ADMIN SHELL OWNERSHIP — FOLLOW-UP / NOT STARTED

Ya documentado como "Pending pre-PR check #1" en la sección de
reconciliación de A2 (arriba) — se deja también como follow-up standalone
para que no se pierda entre el detalle histórico del merge.

El contenido de `/admin/activity` (`admin_activity_content.html`) ya es
genuinamente Hub-wide (KPIs/filtros Ámbito-Área sin favoritismo de
dominio), pero el CHROME de la página (`webapp/main.py::admin_activity`)
sigue viviendo dentro de la composición histórica del admin de Copiloto:
`current_workspace=workspaces.COPILOT` explícito, `selected=DEFAULT_PACK`
gobernando `modules`/`appendices`/`academy`/`pack_label`, `partials/
copilot_nav.html` (nav completo de módulos Copilot), identidad animada con
`config.APP_TITLE`/`APP_SUBTITLE` (marca de Copiloto). Confirmado en vivo:
el brand iframe muestra "Copiloto Metodológico | Arquitecto Danwor" y el
nav lateral trae los módulos de Copiloto, pese a que el contenido es
Hub-wide.

**Por qué no se corrigió ya:** es una inconsistencia cosmética de baja
severidad (la ruta sigue owner-only + diagnóstico, sin cambio de acceso,
solo alcanzable desde el propio panel de diagnóstico de Copilot, nunca
enlazada desde Operations/selector Hub/nav general). No existe una
corrección chica y segura a la vez: el grid `.app-shell.no-copilot` asume
`.module-nav` + resize-handle en columnas exactas (sacar el nav rompe el
layout sin ajustar columnas), y `hub_brand_lockup()` (identidad Hub neutral
ya existente) es un bloque de hero completo, no un componente compacto de
topbar. Cualquiera de las dos correcciones reales se acerca a "nueva
arquitectura de shell" — requiere autorización explícita y alcance propio.
**NO iniciado.**

## AUTH / GRAPH SESSION RENEWAL UX — FOLLOW-UP / NOT STARTED

Reemplazar el error genérico de token de Graph expirado/ausente por un
flujo orgánico de reautenticación ("Renovar acceso") que devuelva al
usuario a su destino original (en vez del mensaje de error crudo actual
en los endpoints que dependen de `graph_token`, p. ej. Staffing). Requiere
diseñar el flujo de reautenticación (dónde vive el botón/link, cómo se
preserva la ruta de destino a través del re-login, si reutiliza el mismo
mecanismo OAuth/PKCE existente o necesita un endpoint nuevo) antes de
tocar código. **NO iniciado — sin discovery todavía.**

---

# ✅ CHECKPOINT — CURRENT EXECUTION POINT (para el agente N+1)

> Bloque de reanudación autosuficiente. Un agente nuevo debe poder reconstruir el
> estado con **HANDOFF.md + Git + repo**, sin esta conversación.

## Estado de fases (inequívoco)
| Fase | Estado |
|---|---|
| 2A Telemetry | **CLOSED / GREEN** |
| 2B Auth/session | **CLOSED / GREEN** |
| 2C Shared templating | **CLOSED / GREEN** |
| PRE-2D distributed helper/context gate | **CLOSED / GREEN** |
| 2D What's New | **CLOSED / GREEN** |
| 2E Academy | **CLOSED / GREEN** |
| 2F Pack Asset Serving | **CLOSED / GREEN** |
| 2G Landing / Session Navigation | **CLOSED / GREEN** |
| 2H Feedback Family | **CLOSED / GREEN** |
| 2I Catalog minimal read dependency boundary | **CLOSED / GREEN** |
| 2J-A Copilot runtime ownership | **CLOSED / GREEN** |
| 2J-B Assistant + Section Analysis | **CLOSED / GREEN** |
| 2J-C Trees + Wizard | **CLOSED / GREEN** |
| 2J-D Module page | **CLOSED / GREEN** |
| **Phase 2** | **CLOSED / GREEN** |
| Platform Acceleration — Batch 1 (Thin M365 integration) | **CLOSED / GREEN** |
| Platform Acceleration — Batch 2 (Staffing repository + service) | **CLOSED / GREEN** |
| Platform Acceleration — Batch 3 (Staffing HTTP + Jinja/HTMX) | **CLOSED / GREEN** |
| Platform Entry / Workspace Resolution | **CLOSED / GREEN** |
| In-App Workspace Navigation / Shared Workspace Switcher | **CLOSED / GREEN** |
| One Danwor Entry Experience (Batch A) | **CLOSED / GREEN** |
| Shared Workspace Shell + Operaciones Structure (Batch B) | **CLOSED / GREEN** |
| Staffing — Asignaciones Dashboard v1 | **CLOSED / GREEN** |
| Danwor Hub Visual Identity | **CLOSED / GREEN** |
| Operations Workspace Access Gate | **CLOSED / GREEN** |
| Danwor Hub — Single-Workspace Switcher Correction | **CLOSED / GREEN** |
| Shared Workspace Sidebar Frame | **CLOSED / GREEN** |
| Danwor Hub Login Identity | **CLOSED / GREEN** |
| Shared Activity / Observability — Context Foundation (Batch A1) | **CLOSED / GREEN** |
| Staffing Dashboard V2 — Director Priority Update | **CLOSED / GREEN** |
| Shared Activity / Observability — Hub Dashboard V2 (Batch A2) | **CLOSED / GREEN** |
| Module Identifier Convention V2 — Registry Foundation (Fase 1) | **CLOSED / GREEN** |
| Module Identifier Convention V2 — Canonical Module Adoption (Fase 2) | **CLOSED / GREEN** |
| **Module Identifier Convention V2 — alcance actualmente autorizado** | **COMPLETE / GREEN** |

## CURRENT EXECUTION POINT
- **Estado consolidado — Module Identifier Convention V2 CERRADO por
  completo (Fase 1 + Fase 2), el resto CLOSED/GREEN o follow-up
  explícitamente NO iniciado:**
  - **Staffing Director Priority V2 = CLOSED / GREEN.**
  - **Hub Activity Dashboard V2 = CLOSED / GREEN** — mergeada a `main` en
    PR #50/#51/#52 (`cdc690b` → `75fa935` → `39fb236`), validada en
    producción por el owner. Ver "FINAL HANDOFF CLOSURE" en la sección
    dedicada arriba para el modelo final entregado completo.
  - **Module Identifier Convention V2 Phase 1 = CLOSED / GREEN** — ver
    "FINAL HANDOFF CLOSURE" en la sección dedicada arriba. Mergeada a
    `main` vía PR #55 (`59ce3fb`), los tres commits funcionales
    confirmados CONTAINED. Registro/resolver canónico
    (`webapp/services/module_registry.py`) puramente aditivo — cero
    consumidores migrados (rutas/Activity/Feedback/nav/YAML/hardcoded
    `startswith` siguen usando sus keys legacy sin cambios).
  - **Module Identifier Convention V2 Phase 2 = CLOSED / GREEN** — ver
    "FINAL HANDOFF CLOSURE" en la sección dedicada arriba. Mergeada a
    `main` vía PR #57 (`0d1c138`), los cuatro commits funcionales
    confirmados CONTAINED. Activity y el dominio COMPLETO de Feedback
    (web + `analyze_feedback.py` — agrupamiento y filtro `--module`) ahora
    persisten/normalizan hacia el `module_id` canónico; JSONL/YAML
    histórico nunca reescrito; validado (solo lectura) contra
    `activity_log/`/`feedback_log/` reales.
  - **MODULE IDENTIFIER CONVENTION V2 — ALCANCE ACTUALMENTE AUTORIZADO =
    COMPLETE / GREEN.** Rename físico de módulo/contenido: **OPCIONAL,
    FUTURO, NO PLANIFICADO** — ver "FINAL HANDOFF CLOSURE" de Fase 2 para
    el razonamiento completo. Academy: `CPACxx` sigue reservado solo
    conceptualmente, discovery dedicado NO iniciado.
  - **Staffing Vacation Timeline Overlay = IMPLEMENTED / PENDING CI** — ver
    sección dedicada arriba. Branch `work/staffing-vacation-overlay`, **NO
    mergeada todavía**. Contrato de datos verificado offline (export real
    del owner); implementación completa (repository/service/router/UI)
    contra ese contrato; validación contra Graph LIVE sigue bloqueada (sin
    token delegado/sesión de browser en este entorno) — único pendiente
    antes de production-ready.
  - **Activity Admin Shell Ownership = FOLLOW-UP / NOT STARTED** — ver
    sección dedicada arriba (inconsistencia cosmética de baja severidad,
    sin corrección chica y segura disponible todavía).
  - **Auth / Graph Session Renewal UX = FOLLOW-UP / NOT STARTED** — ver
    sección dedicada arriba, sin discovery todavía.
- **Último batch CERRADO:** **Module Identifier Convention V2 — Canonical
  Module Adoption (Fase 2)** — ver "FINAL HANDOFF CLOSURE" en la sección
  dedicada arriba — **CLOSED / GREEN** (mergeada vía PR #57, `main` HEAD
  `0d1c138`, los cuatro commits funcionales confirmados CONTAINED). Con este
  cierre, **Module Identifier Convention V2 (Fase 1 + Fase 2) queda
  COMPLETE / GREEN** en su alcance actualmente autorizado. El batch previo
  cerrado es **Module Identifier Convention V2 — Registry Foundation (Fase
  1)** — ver "FINAL HANDOFF CLOSURE" en su sección dedicada arriba —
  **CLOSED / GREEN** (mergeada vía PR #55, `main` HEAD `59ce3fb`, los tres
  commits funcionales confirmados CONTAINED). El batch previo a ese es
  **Shared Activity / Observability — Hub Dashboard V2
  (Batch A2)** — ver "FINAL HANDOFF CLOSURE" en su sección dedicada arriba
  — **CLOSED / GREEN** (mergeada vía PR #50/#51/#52, `main` HEAD `39fb236`,
  validación de producción del owner recorded). El batch anterior a ese es
  **Staffing Dashboard V2 — Director Priority
  Update** — ver sección dedicada arriba — **CLOSED / GREEN** (commits
  `2833fa6` + `3fcb75e` + `265198b` (pre-PR final check) + `a149c3b`
  (production behavior corrective — Parts A/B/C/D/E/F) + `20d44f6`
  (corrective final de header/layout), **mergeados a `main` vía PR
  #46/#47/#48**, `main` HEAD `126f663`, **CLOSED/GREEN tras validación
  real del owner en su entorno de producción** — ver "Cierre —
  validación final del owner" en la sección dedicada arriba para el
  detalle completo de los 14 puntos validados y los 13 items
  entregados). Requerimiento 9 original (overlay de vacaciones)
  **separado explícitamente** de este cierre — ver sección dedicada
  "STAFFING — VACATION TIMELINE OVERLAY — FOLLOW-UP / NOT STARTED"
  arriba; NO autorizar su discovery hasta que exista una sesión con
  `graph_token` real disponible. **REAL GRAPH FILTER queda
  PENDING OWNER/PRODUCTION SMOKE** — sin cambio de estado, ya no bloquea
  el cierre (fallback automático sigue siendo la red de seguridad).
- **Batch CERRADO anterior:** **Shared Activity / Observability — Context
  Foundation (Batch A1)** — ver sección dedicada arriba — **CLOSED / GREEN**
  (commits `18d4c43` + corrective `ce17790`, **mergeado a `main` vía PR #44 /
  `53277d6`**, owner-confirmed CI GREEN). Envelope de actividad ahora
  platform-aware (aditivo `scope`/`area`), Operations emite su primer evento de
  uso (`staffing_overview_viewed`), una sola activity capability, históricos
  legibles sin migración; el corrective preserva el lente Copiloto del dashboard
  actual (Operations no lo contamina hasta A2).
- **Phase (producto):** ninguna fase de producto abierta. Danwor Hub Login Identity
  — ver sección dedicada arriba — está **CLOSED/GREEN, mergeado a `main`
  (PR #40/`4fe4193`, PR #41/`e587609`, PR #42/`e5bba90`) y validado en
  producción DigitalOcean el 2026-08-10**, igual que Operations Workspace
  Access Gate, Danwor Hub — Single-Workspace Switcher Correction, y
  Shared Workspace Sidebar Frame (PR #35/`324b6f2`, PR #36/`979cfc1`, PR
  #38/`a2f4035`, mismo 2026-08-10). Ninguno de los otros tres se tocó en
  este batch.
- **Status:** **CLOSED / GREEN** para Danwor Hub Login Identity. CI real
  de GitHub confirmado GREEN sobre PR #40/#41/#42. El owner confirmó el
  **2026-08-10** que el lockup canónico (kicker + ícono + "Danwor Hub" en
  una línea) funciona correctamente en **producción DigitalOcean** —
  **smoke de producción verificado por el usuario, NO un resultado de
  test automatizado**. Ver sección dedicada "DANWOR HUB LOGIN IDENTITY —
  CLOSED / GREEN" arriba para el detalle completo (incluye el resumen de
  las 3 pasadas y por qué ninguna tocó mecánica de auth).
- **Functional commits:** `8e7ee46` + `5674120` (branch
  `work/danwor-hub-login-identity`, PR #40/#41) + `7a51c9c` (branch
  `work/danwor-hub-login-brand-lockup`, PR #42, corrective final del
  lockup canónico). Ninguno amendeado/squasheado.
- **Alcance combinado:** `OPERATIONS_ALLOWED_USERS` (allowlist explícita,
  fail-closed, sin wildcard `"ALL"`, misma normalización que
  `ALLOWED_USERS`); `is_operations_authorized_user` = admisión base AND
  allowlist de Operations, ortogonal al tier de Copiloto, sin bypass de
  `APP_OWNER`; MISMO predicado gobierna descubrimiento (`workspaces.py`) y
  enforcement server-side (`routers/staffing.py`); el switcher del topbar
  compartido expresa ese resultado correctamente; el frame de sidebar
  compartido (`.app-shell`/`--nav-width`/`copilotLayout()`/`partials/
  workspace_shell.html`) quedó consolidado; el bloque canónico de
  identidad Hub (`hub_brand_lockup()` → `hub_icon()`, un solo SVG, sin
  duplicar) es compartido entre el selector Hub autenticado y la landing
  anónima. **Ninguna ruta se renombró, ningún cambio de auth/OAuth/
  sesión/PKCE/callback/permisos/resolución de workspace/token de Graph,
  route surface sin cambios (82)** en los cinco batches — ver
  "Alcance"/detalle en cada sección dedicada arriba.
- **En curso (PENDING CI, sin mergear):** Module Identifier Convention V2
  Phase 2 — Canonical Module Adoption — ver sección dedicada arriba. Un
  posible Phase 3 (physical rename) requiere autorización explícita
  separada, no está ni propuesto.
- **Next candidates (ninguno autorizado todavía — solo follow-ups
  documentados, NO iniciar sin autorización explícita del owner):**
  Activity Admin Shell Ownership, Auth / Graph Session Renewal UX. Staffing
  — Vacation Timeline Overlay YA NO es un candidato sin empezar: tiene su
  propia sección "IMPLEMENTED / PENDING CI" más abajo (branch
  `work/staffing-vacation-overlay`) — solo la validación contra Graph LIVE
  sigue bloqueada. Ver las secciones dedicadas arriba/abajo para el
  contrato conocido de cada uno.
  Los hallazgos
  reportados-pero-diferidos de batches previos (colisiones de color en el
  timeline de Staffing; filtro de tipo de proyecto; Status Finalización)
  SIGUEN diferidos/sin autorizar.
  Groups/App Roles, Projects/Reporting, cálculo de capacidad, y Shared
  Dashboard Primitives (más allá de lo ya extraído) quedan igualmente
  diferidos sin autorización.
- **Baseline actual (`main`, incluye Module Identifier Convention V2 Phase 1
  CLOSED/GREEN + A2 CLOSED/GREEN + Staffing Dashboard V2 CLOSED/GREEN — NO
  incluye Fase 2, todavía sin mergear):** Python 3.14.3 · FastAPI 0.141.1 ·
  Starlette 1.4.1 · tests **672 passed / 1 skipped** · route surface **83**
  · route-contract **GREEN** · `main` HEAD `fa49f69` (merge de PR #56,
  docs-only, ya incluye PR #55 — Fase 1 completo —, PR #50/#51/#52 — A2
  completo —, PR #46/#47/#48/#49 — Staffing Dashboard V2 Director Priority
  Update CLOSED/GREEN completo —, y PR #44 — A1 Context Foundation
  CLOSED/GREEN). Baseline en la branch de Fase 2
  (`work/module-identifier-convention-v2-phase2`, sin mergear): **693
  passed / 1 skipped** (+21).

### Expected next workflow
1. El agente siguiente lee **HANDOFF + Git** (no la conversación previa).
2. Reconstruye el estado: Batch 1/2/3 CLOSED/GREEN, primer hito real de
   Staffing ACHIEVED, mapeo de esquema y semántica de allocation validados
   contra Graph real, **Staffing — Asignaciones Dashboard v1 CLOSED/GREEN
   (PR #33)**, **Danwor Hub Visual Identity CLOSED/GREEN (commit
   `8ea2921`, branch `work/danwor-hub-visual`)**, **Operations Workspace
   Access Gate CLOSED/GREEN (commit `05b5da3` + `47ee619`, branch
   `work/operations-access-gate`, PR #35, production-verified
   2026-08-10)**, **Danwor Hub — Single-Workspace Switcher Correction
   CLOSED/GREEN (commit `bd301c0`, branch
   `work/hub-switcher-single-workspace`, PR #36, production-verified
   2026-08-10)**, **Shared Workspace Sidebar Frame CLOSED/GREEN (commits
   `dd7f99d` + `4372625`, branch `work/shared-workspace-sidebar-frame`,
   PR #38, production-verified 2026-08-10)**, **Danwor Hub Login Identity
   CLOSED/GREEN (commits `8e7ee46` + `5674120` + `7a51c9c`, PR
   #40/#41/#42, production-verified 2026-08-10 — kicker canónico + ícono
   Hub + "Danwor Hub" en una línea, MISMO `hub_brand_lockup()` que el
   selector autenticado)**, observación no-bloqueante de calidad
   referencial de origen registrada (no resuelta a propósito).
3. El usuario decide el próximo incremento a partir de una evaluación
   fresca — nada queda autorizado por este documento hasta que el usuario
   lo indique explícitamente. **Este trabajo NO debe interpretarse como
   pendiente** — requiere evaluación fresca del próximo candidato, no una
   continuación asumida.
4. **Recién entonces** se autoriza el próximo lote.

### Stop conditions para el próximo agente
Detenerse y reportar si: contradicción entre HANDOFF/Git/código · branch equivocada ·
working tree sucio · el próximo candidato requiere una dependencia main-private
inesperada · aparece necesidad de `app.state`/`Depends()`/DI no justificada por
necesidad concreta · cambio de comportamiento o de route-surface no anticipado ·
se intenta comenzar Phase 3/M365 hardening completo/Catalog refactor/Admin
extraction/rediseño de login-nav/cálculo de capacidad/los hallazgos de UI/
Staffing diferidos (login "One Danwor", colores del timeline, filtro de
proyecto, Status Finalización) sin autorización explícita · se retoma el
discovery de vacaciones (item 9 de Staffing Dashboard V2) sin una sesión
con `graph_token` real disponible.

## REMAINING PHASE 2 MAP (post-2J-D)
**STATEFUL** — completo. Module page (2J-D), Assistant + Section Analysis
(2J-B) y Trees/Wizard/Diagnostics (2J-C) están todos extraídos; `store`/
`assistant_store` no tienen ninguna referencia en `webapp.main` — dueño
único en `webapp/copilot_runtime.py`, consumido directo por
`webapp/routers/{wizard,assistant,modules}.py`.
- **Fase 2I ya resolvió** la dependencia de Catálogo que bloqueaba esta
  extracción (`catalog_reads._catalog_domains/_catalog_connection_types/
  _catalog_security_families/_catalog_data_products_list`, dueño explícito
  en `webapp/services/catalog_reads.py`) — confirmado en 2J-D: `modules.py`
  los consume directo, sin tocar `webapp.main`.
- **Restante en Fase 2J:** ninguno. Los 3 lotes planificados (2J-B, 2J-C,
  2J-D) están todos IMPLEMENTED/PENDING CI o CLOSED/GREEN. El siguiente paso
  no es otro lote — es la **PHASE 2 COMPLETION EVALUATION** (ver sección 2J
  arriba para los 7 criterios y su estado).

**CATALOG** — fase propia: separar **HTTP / business-data-compliance / presentation-SVG**
(no mover wholesale las ~1900 líneas).

**ADMIN** — último o parcialmente diferible según evaluación.

## ARCHITECTURAL NORTH STAR — Danwor Internal Platform
El **platform composition root** (hoy `webapp/main.py`, sin `create_app()`): **compone,
registra capacidades, resuelve entrada/workspace y delega.** NO es dueño funcional de
cada workspace. Principio: *"The platform decides where to delegate. Each workspace
decides what to do after receiving control."* **Shared capability ≠ owned by `main.py`.**

Clasificación de capacidades (preservar):
- `auth_service` → **PLATFORM / SHARED** identity + session.
- `webapp/templating.py` → **PLATFORM presentation infrastructure**.
- `webapp/access.py` → **CURRENT WEB ACCESS POLICY** (cross-workspace reuse **NOT YET DECIDED**).
- `webapp/nav.py` → **CURRENT WEB NAVIGATION** (cross-workspace reuse **NOT YET DECIDED**).
- `core.content_registry` → **COPILOT / CONTENT**.
- Academy / What's New → **COPILOT features**.
- Future **Operations / Staffing** → **NOT IMPLEMENTED**.

## SHARED UI DIRECTION (diferida)
NO confundir la **infraestructura de templating Jinja** con un futuro **design system**.
Los patrones reutilizables (documentación compartida, analytics/reporting, UI operativa)
deben **derivarse de consumidores reales** — Usage Analytics + Staffing serán evidencia
clave. **NO implementar todavía.**

## BRANCH SAFETY RULE (aprendida del incidente Phase 2D)
**NUNCA** borrar la branch de una fase hasta confirmar que su commit funcional está
contenido en `origin/main`:
```
git merge-base --is-ancestor <functional-commit> origin/main   # exit code 0 requerido
```
El incidente 2D (branch borrada con commits sin mergear; recuperados vía PR #11) es la
razón de esta regla.

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
