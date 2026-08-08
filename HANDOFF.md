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

## CURRENT EXECUTION POINT
- **Phase:** Phase 2 — formal closure
- **Status:** CLOSED / GREEN
- **Current next candidate:** **POST-PHASE-2 PLATFORM ACCELERATION DECISION**
  — decidir el trabajo mínimo de plataforma necesario antes de arrancar
  delivery real de Operations/Staffing. Candidatos: M365 shared capability /
  M365-A, y Operations workspace / Staffing enablement.
- **Next candidate status:** **NOT YET AUTHORIZED.** No asumir automáticamente
  una nueva fase larga de refactor. No elegir M365-A por default sin primero
  evaluar si Staffing puede arrancar en paralelo o inmediatamente después de
  una porción mínima de capacidad compartida.
- **Baseline:** Python 3.14.3 · FastAPI 0.141.1 · Starlette 1.4.1 · tests **274 passed / 1 skipped** · route surface **75**

### Expected next workflow
1. El agente siguiente lee **HANDOFF + Git** (no la conversación previa).
2. Reconstruye el estado: Fase 2 CLOSED/GREEN, ningún blocker mandatorio
   pendiente, Catálogo/Admin/M365/LLM/runtime-scalability explícitamente
   diferidos (ver bloque de cierre de Fase 2 arriba).
3. El usuario evalúa y decide la **POST-PHASE-2 PLATFORM ACCELERATION
   DECISION**: mínimo de plataforma antes de Operations/Staffing, vs.
   M365-A, vs. arrancar Staffing en paralelo con una porción mínima
   compartida.
4. **Recién entonces** se autoriza el próximo lote — no antes.

### Stop conditions para el próximo agente
Detenerse y reportar si: contradicción entre HANDOFF/Git/código · branch equivocada ·
working tree sucio · el próximo candidato requiere una dependencia main-private
inesperada · aparece necesidad de `app.state`/`Depends()`/DI no justificada por
necesidad concreta · cambio de comportamiento o de route-surface · se intenta
comenzar Phase 3/M365-A/Operations/Staffing/Catalog refactor/Admin extraction
sin autorización explícita de la POST-PHASE-2 PLATFORM ACCELERATION DECISION.

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
