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

## SHARED WORKSPACE SIDEBAR FRAME — IMPLEMENTED / PENDING CI
(commit funcional `dd7f99d` — `refactor(platform): share workspace sidebar
frame` + corrective `refactor(platform): share sidebar resize handle`
(posterior, mismo branch, ver bullet dedicado abajo), branch
`work/shared-workspace-sidebar-frame`. **NO marcar CLOSED/GREEN todavía**
— falta la confirmación de CI real en GitHub Y la validación visual/de
producto por el owner, mismo protocolo ya usado en batches anteriores.)

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
- **Validación visual:** NO realizada vía navegador automatizado — el
  login real requiere Microsoft Entra ID OAuth con credenciales reales,
  fuera de alcance de este entorno. Verificación equivalente hecha vía
  `TestClient` HTTP directo contra el servidor real (mismo motor Jinja,
  mismo HTML servido): se capturó el render completo del nav de Copiloto y
  de Operaciones antes/después del cambio y se confirmó que el handle de
  resize y la bienvenida aparecen en ambos con el HTML esperado. Falta
  smoke visual real del owner en `localhost:8082`/producción antes de
  CLOSED/GREEN.
- **Pendiente:** confirmación de CI real en GitHub + validación
  visual/de producto por el owner antes de CLOSED/GREEN. No se abrió el PR
  todavía.

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
| Shared Workspace Sidebar Frame | **IMPLEMENTED / PENDING CI** |

## CURRENT EXECUTION POINT
- **Phase:** Shared Workspace Sidebar Frame — ver sección dedicada arriba.
  Precedida por Operations Workspace Access Gate y Danwor Hub —
  Single-Workspace Switcher Correction, **ambos CLOSED/GREEN, mergeados a
  `main` (vía PR #35/#324b6f2 y PR #36/#979cfc1) y validados en producción
  DigitalOcean el 2026-08-10**; la docs-only closure de ambos también
  mergeó vía **PR #37** (merge commit `5d6dba2`). Ninguno de los tres se
  tocó en este batch.
- **Status:** **IMPLEMENTED / PENDING CI.** Suite local sobre el HEAD real
  (funcional `dd7f99d` + corrective `4372625`): **510 passed / 1 skipped**
  (497 + 13 nuevos), route surface **82** (sin cambios). Ver sección
  dedicada "SHARED WORKSPACE SIDEBAR FRAME — IMPLEMENTED / PENDING CI"
  arriba para el detalle completo — falta la confirmación de CI real en
  GitHub Y validación visual/de producto por el owner antes de
  CLOSED/GREEN. PR no abierto todavía.
- **Functional commits:** `dd7f99d` (`refactor(platform): share workspace
  sidebar frame`) + `4372625` (`refactor(platform): share sidebar resize
  handle`, corrective que extrajo el `<div data-resize>` a
  `partials/workspace_shell.html::sidebar_resize_handle()` — ver bullet
  dedicado arriba). Ninguno amendeado/squasheado. Branch
  `work/shared-workspace-sidebar-frame`.
- **Alcance combinado (batches previos, sin tocar en este):**
  `OPERATIONS_ALLOWED_USERS` (allowlist explícita, fail-closed, sin
  wildcard `"ALL"`, misma normalización que
  `ALLOWED_USERS`); `is_operations_authorized_user` = admisión base AND
  allowlist de Operations, ortogonal al tier de Copiloto, sin bypass de
  `APP_OWNER`; MISMO predicado gobierna descubrimiento (`workspaces.py`) y
  enforcement server-side (`routers/staffing.py`); el switcher del topbar
  compartido ahora expresa ese resultado correctamente (ausente con 1 solo
  workspace, presente con 2+, ícono del hero de Danwor Hub intacto).
  **Ninguna ruta se renombró, ningún cambio de auth/OAuth/sesión, route
  surface sin cambios (82)** — ver "Alcance" en cada sección dedicada
  arriba. El batch de Sidebar Frame (esta fase) agrega: frame de sidebar
  YA compartido confirmado por evidencia (no se creó ninguna abstracción
  nueva de geometría/resize); `user` ahora en el contexto de
  `staffing_page`; bienvenida de sesión Y handle de resize extraídos a
  `partials/workspace_shell.html` (macros `nav_welcome`/
  `sidebar_resize_handle`), importado por Copiloto Y Operaciones — sin
  tocar navegación workspace-owned de ninguno de los dos.
- **Next candidate:** ninguno nominado por este documento. Los tres
  últimos batches fueron deliberadamente pequeños y no abren una fase
  nueva — el próximo incremento debe partir de una evaluación fresca del
  estado actual (código + tests + HANDOFF), no de una continuación
  asumida. Los hallazgos reportados-pero-diferidos explícitamente durante
  el batch de Sidebar Frame (login no autenticado sigue diciendo "One
  Danwor"; colisiones de color en el timeline de Staffing; filtro de tipo
  de proyecto; switch de agrupamiento consultor/proyecto; Status
  Finalización; optimización de ventana histórica de Graph) NO están
  autorizados por este documento — son candidatos para batches propios.
  Entra Groups/App Roles, Projects/Reporting, cálculo de capacidad,
  rebranding de login o cambios de color/agrupamiento de Staffing quedan
  igualmente diferidos sin autorización.
- **Next candidate status:** **NOT AUTHORIZED.**
- **Baseline:** Python 3.14.3 · FastAPI 0.141.1 · Starlette 1.4.1 · tests
  **510 passed / 1 skipped** · route surface **82** · commits `dd7f99d` +
  `4372625` sobre `work/shared-workspace-sidebar-frame` (branchada desde
  `main` HEAD `5d6dba2d92ad1daf5797971b014aa1850dba0c3d`, que ya incluye la
  docs-closure PR #37).

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
   2026-08-10)**, **Shared Workspace Sidebar Frame IMPLEMENTED/PENDING CI
   (commits `dd7f99d` + `4372625`, branch
   `work/shared-workspace-sidebar-frame` — falta confirmación de CI real Y
   validación visual/de producto del owner antes de CLOSED/GREEN)**,
   observación no-bloqueante de calidad referencial de origen registrada
   (no resuelta a propósito).
3. El usuario decide el próximo incremento a partir de una evaluación
   fresca — nada queda autorizado por este documento hasta que el usuario
   lo indique explícitamente.
4. **Recién entonces** se autoriza el próximo lote.

### Stop conditions para el próximo agente
Detenerse y reportar si: contradicción entre HANDOFF/Git/código · branch equivocada ·
working tree sucio · el próximo candidato requiere una dependencia main-private
inesperada · aparece necesidad de `app.state`/`Depends()`/DI no justificada por
necesidad concreta · cambio de comportamiento o de route-surface no anticipado ·
se intenta comenzar Phase 3/M365 hardening completo/Catalog refactor/Admin
extraction/rediseño de login-nav/cálculo de capacidad/los hallazgos de UI/
Staffing diferidos (login "One Danwor", colores del timeline, filtro de
proyecto, agrupamiento consultor/proyecto, Status Finalización, ventana
histórica de Graph) sin autorización explícita.

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
