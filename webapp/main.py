from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode
import asyncio
import dataclasses
import html
import logging
import os
import re
import sys
import unicodedata
import zlib

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from core.content_registry import (
    DEFAULT_PACK,
    available_content_packs,
    content_pack_label,
    content_pack_short_label,
    resolve_pack,
)
from core import remote_store
from core.utils import slugify
from webapp.services import auth_service
from webapp.services import catalog_reads
from webapp.services import odoo_client  # pool de conexiones de proceso (cierre en lifespan)
from webapp.services.activity_service import log_activity, log_verbose
from webapp.services.activity_context import SCOPE_COPILOT, AREA_CATALOG
from webapp.services.content_service import (
    academy_asset_for,
    list_academy,
    list_appendices,
    list_modules,
)
from webapp.services import admin_tools
from webapp.services import activity_analytics
from webapp.services import guides_service as guides
from webapp.routers import system as system_router
from webapp.routers import telemetry as telemetry_router
from webapp.routers import auth as auth_router
from webapp.routers import whats_new as whats_new_router
from webapp.templating import templates  # env Jinja compartida (Fase 2C); misma identidad
from webapp import access  # access policy de la web (owner/readonly/diagnostic)
from webapp import nav     # navegación de la web (login redirect)
from webapp import workspaces  # PLATFORM: identidad/switcher entre workspaces
from webapp import operations_access  # access policy del workspace Operations
from webapp import hub_metrics_access  # access policy de Usage Metrics (owner ticket "HUB METRICS")
from webapp.routers import academy as academy_router
from webapp.routers import pack_assets as pack_assets_router
from webapp.routers import landing as landing_router
from webapp.routers import feedback as feedback_router
from webapp.routers import assistant as assistant_router
from webapp.routers import wizard as wizard_router
from webapp.routers import modules as modules_router
from webapp.routers import reporting as reporting_router
from webapp.routers import staffing as staffing_router
from webapp.routers import platform as platform_router
from webapp.routers import commercial as commercial_router
from webapp.routers import human_capital as human_capital_router
from webapp.routers import catalog_assessment as catalog_assessment_router
from webapp.routers import datasphere_assessment as datasphere_assessment_router
from webapp.routers import datasphere_publication as datasphere_publication_router
from webapp.routers import governed_decisions as governed_decisions_router
from webapp.routers import governed_contracts_ui as governed_contracts_ui_router
from webapp.services import assessment_presenter
from webapp.services import catalog_delta_evidence
from webapp.services import asset_evaluation
from webapp.services import assessment_service
from webapp.services import domain_classification_service as dcs
from webapp.services import catalog_navigation
from webapp.presentation import artifact_icons
from webapp.services import catalog_ingestion_orchestration
from webapp.services import catalog_cds_docs
from webapp.services import catalog_view_source
from webapp.routers import commercial_strategy as commercial_strategy_router

WEB_DIR = Path(__file__).parent

# REPORTING PERF LOGGER — ENABLE EXISTING PRODUCTION TIMING LOGGER (owner
# ticket, ver AUDIT "Cartera Proyecto Cold Penalty Source Attribution").
# TODA la instrumentación de timing de Reporting/Cartera YA EXISTE (ver
# reporting_repository.py/reporting_service.py/reporting_time_logs_durable.py
# — logger `danwor.reporting.perf`, ya safe por diseño: solo elapsed_ms/
# conteos, NUNCA token/scope crudo/filas/eTag) pero nunca llegaba a
# DigitalOcean Runtime Logs. CAUSA RAÍZ (ver AUDIT §4): ningún archivo de
# este repo configura logging en absoluto (cero `basicConfig`/`dictConfig`),
# así que el root logger queda en su default de Python (`WARNING`, sin
# handler) y `danwor.reporting.perf` HEREDA ese nivel — cada `.info(...)` de
# esas líneas se descarta ANTES de llegar a cualquier handler, sin importar
# qué capture DigitalOcean. Esto habilita ÚNICAMENTE ese logger, nunca root/
# uvicorn/`danwor.reporting` (el general, sin `.perf`) — visibilidad pura,
# cero cambio de comportamiento/caching/adquisición de Reporting.
_REPORTING_PERF_LOGGER_NAME = "danwor.reporting.perf"
# Nombre PROPIO del handler (Handler.set_name/get_name, stdlib) — guard de
# idempotencia preciso: identifica ESTE handler puntual en vez de asumir
# "cualquier handler presente ya es el nuestro" (`if logger.handlers`),
# que sería frágil si algo más (p. ej. el plugin de logging de pytest, que
# puede adjuntar sus propios handlers directo a un logger con
# `propagate=False` para poder seguir capturándolo) ya dejó otro handler
# ahí antes de que este bootstrap corra.
_REPORTING_PERF_HANDLER_NAME = "reporting_perf_stdout"


def _configure_reporting_perf_logging() -> None:
    """Idempotente: un segundo import/bootstrap de este módulo en el MISMO
    proceso (p. ej. un test que hace `from webapp.main import app` más de
    una vez) nunca duplica ESTE handler puntual — guard mínimo, sin
    abstracción de logging nueva, consistente con el resto de este archivo.
    `propagate = False` es a propósito: el registro nunca burbujea a root,
    así que root queda EXACTAMENTE como estaba, sin tocar."""
    logger = logging.getLogger(_REPORTING_PERF_LOGGER_NAME)
    if any(h.get_name() == _REPORTING_PERF_HANDLER_NAME for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_REPORTING_PERF_HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


_configure_reporting_perf_logging()


def _display_diagnostic_title(title: str) -> str:
    value = str(title or "")
    for prefix in ("Diagn\u00f3stico \u00b7 ", "Diagn\u00f3stico del ", "Diagn\u00f3stico de "):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value

templates.env.filters["diagnostic_title"] = _display_diagnostic_title


_CATALOG_VERDICT_TEXT = {
    "plena": "Referencia Danwor — reutilizable",
    "buena": "Reutilizable con notas",
    "parcial": "Inspiración conceptual con reservas — no copiar tal cual",
}


def _catalog_verdict_key(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if raw in {"plena", "green", "verde"}:
        return "plena"
    if raw in {"buena", "yellow", "amarillo"}:
        return "buena"
    if raw in {"parcial", "orange", "naranja"}:
        return "parcial"
    return raw or "sin-dictamen"


def _catalog_verdict_label(value: str) -> str:
    return _catalog_verdict_key(value).replace("-", " ").upper()


def _catalog_verdict_text(value: str) -> str:
    return _CATALOG_VERDICT_TEXT.get(_catalog_verdict_key(value), "Referencia Danwor")


def _catalog_verdict_tone(value: str) -> str:
    """TONO (color) del veredicto por el mapeo canónico ÚNICO (valencia), separado de la CLAVE del veredicto. Así 'Buena'
    es verde en TODO el Hub (igual que Adopción), sin que el color codifique rango ni reparos. La clave (`catalog_verdict_class`)
    se mantiene para `data-*` y filtros."""
    from webapp.services.status_tone import status_tone
    return status_tone(_catalog_verdict_key(value))


templates.env.filters["catalog_verdict_class"] = _catalog_verdict_key          # CLAVE semántica (data-verdict, filtros): plena/buena/parcial
templates.env.filters["catalog_verdict_tone"] = _catalog_verdict_tone          # TONO (color) canónico por valencia: buena→verde
templates.env.filters["catalog_verdict_label"] = _catalog_verdict_label
templates.env.filters["catalog_verdict_text"] = _catalog_verdict_text

# La env Jinja compartida y sus globals de plataforma (static_version,
# verbose_telemetry) se construyen en webapp/templating.py (Fase 2C); acá se
# re-exporta `templates` (misma identidad) y se le siguen registrando los
# filtros de workspace (diagnostic_title, catalog_verdict_*, arriba).
# store y assistant_store (webapp/copilot_runtime.py, Fase 2J-A) ya no tienen
# consumidores en main.py: module_page (su único consumidor acá) se movió a
# webapp/routers/modules.py (Fase 2J-D), que los consume directo de
# copilot_runtime. section_analysis_service, assistant_chat_service,
# assistant_routing_service y assistant_feedback_repository se movieron a
# webapp/routers/assistant.py (Fase 2J-B) junto con sus únicos consumidores —
# stateless, sin identidad compartida requerida (ver Fase 2J assessment).
@asynccontextmanager
async def lifespan(app: FastAPI):
    # El estado persistido en DO Spaces se restaura EN BACKGROUND, no bloqueando el
    # arranque: esperar la descarga completa antes de servir hacía que el health
    # check fallara ("Waiting for application startup" hasta el timeout → connection
    # refused). Ahora la app responde /health de inmediato y el restore corre detrás
    # (la memoria queda disponible a los pocos segundos). No-op si Spaces no está
    # configurado.
    log = logging.getLogger("danwor.remote_store")

    async def _restore_bg():
        try:
            restored = await asyncio.to_thread(remote_store.restore)
            # El restore corre DESPUÉS de que la app empezó a servir, así que los
            # @lru_cache del catálogo pueden haberse poblado vacíos en la ventana
            # inicial. Al terminar, se invalidan para que el catálogo restaurado se
            # sirva sin necesidad de regenerarlo a mano.
            if restored:
                _invalidate_catalog_cache()
                log.info("restore en background completo: %d archivos, cache de catálogo invalidado", restored)
        except Exception as exc:
            log.warning("restore en background falló: %s", exc)

    log.info("persistencia: %s", remote_store.describe_mode())
    warning = remote_store.mode_warning()
    if warning:
        log.warning(warning)
    tasks = []
    if remote_store.enabled():
        tasks.append(asyncio.create_task(_restore_bg()))
        tasks.append(asyncio.create_task(remote_store.sync_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Flush final best-effort (cubre deploys/recycles graceful). Solo con persistencia remota completa; `mirror_up` además se niega a correr si el restore
        # no terminó o quedó incompleto.
        if remote_store.enabled():
            try:
                await asyncio.to_thread(remote_store.mirror_up)
            except Exception:
                pass

        # Cierre del pool de conexiones a Odoo. Vive en el PROCESO, asi que
        # el unico lugar legitimo para cerrarlo es el shutdown de la app —
        # nunca el fin de una request. Idempotente.
        try:
            odoo_client.close_transport()
        except Exception:
            pass


app = FastAPI(title="Copiloto Metodológico Danwor Beta", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
app.include_router(system_router.router)
app.include_router(telemetry_router.router)
app.include_router(auth_router.router)
app.include_router(whats_new_router.router)
app.include_router(academy_router.router)
app.include_router(pack_assets_router.router)
app.include_router(landing_router.router)
app.include_router(feedback_router.router)
app.include_router(assistant_router.router)
app.include_router(wizard_router.router)
app.include_router(modules_router.router)
app.include_router(staffing_router.router)
app.include_router(reporting_router.router)
app.include_router(platform_router.router)
app.include_router(commercial_router.router)
app.include_router(human_capital_router.router)
app.include_router(commercial_strategy_router.router)
app.include_router(catalog_assessment_router.router)
app.include_router(datasphere_assessment_router.router)
app.include_router(datasphere_publication_router.router)
app.include_router(governed_decisions_router.router)
app.include_router(governed_contracts_ui_router.router)


# Aliases a las capacidades ya extraídas (gate distribuido pre-2D). Los handlers
# que todavía viven en este módulo usan estos nombres; el comportamiento reside
# ahora en core.content_registry (pack), webapp.access (access policy) y
# webapp.nav (navegación). Se conservan como alias para no refactorizar aún las
# decenas de call-sites que quedan en main.py (residual Catalog/Admin).
_pack = resolve_pack                    # core.content_registry
_login_redirect = nav.login_redirect    # webapp.nav
_is_owner = access.is_owner             # webapp.access
_readonly = access.is_readonly          # webapp.access
_diagnostic = access.is_diagnostic      # webapp.access
# Alias transitorio (Fase 2E): dueño real en nav; se conserva porque el
# catálogo residual (`/catalog/guide/{slug}`) todavía lo consume. _invite_src
# se removió (Fase 2J-D): su único consumidor era module_page, que ahora
# importa `invite.invite_src` directo en webapp/routers/modules.py.
_home_url = nav.home_url                                       # nav (module/catalog)


def _truthy(value: str) -> bool:
    """Checkbox HTML: manda su valor solo si está tildado."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


# /health y /favicon.ico se movieron a webapp/routers/system.py (Fase 1);
# se incluyen vía app.include_router(system_router.router) arriba.


# ── Autenticación Microsoft (Entra ID) ─────────────────────────────────────────
# /api/auth/microsoft/config|token, /api/auth/me, /api/diagnostic y /api/auth/logout
# se movieron a webapp/routers/auth.py (Fase 2B), incluido vía
# app.include_router(auth_router.router) arriba. _MsTokenBody se co-localizó allí
# (su único consumidor era el token endpoint).


# /brand/dancing/{pack_name}, /pack-assets/{pack_name}/{asset_path:path} y
# /pack-resources/{pack_name}/{kind}/{resource_path:path} se movieron a
# webapp/routers/pack_assets.py (Fase 2F), incluido vía
# app.include_router(pack_assets_router.router) arriba.


# GET /, /home, /login, /desafio y /desafio/cerrar se movieron a
# webapp/routers/landing.py (Fase 2G), incluido vía
# app.include_router(landing_router.router) arriba.


# GET /modules/{stem} (module_page) se movió a webapp/routers/modules.py
# (Fase 2J-D), incluido vía app.include_router(modules_router.router) arriba.
# _MODULE_TABS se co-localizó allí (único consumidor).


# POST /modules/{stem}/sections/analyze se movió a webapp/routers/assistant.py
# (Fase 2J-B), incluido vía app.include_router(assistant_router.router) arriba.



# Feedback de módulo, sección, catálogo y sección de catálogo (12 rutas) se
# movieron a webapp/routers/feedback.py (Fase 2H), incluido vía
# app.include_router(feedback_router.router) arriba. Academy feedback (2E)
# ya estaba movido; Assistant response feedback (2J-B) y Wizard/Trees
# feedback (2J-C) se movieron en fases posteriores — ver más abajo.


# La capacidad completa de Trees/Wizard (9 rutas: /trees/suggest,
# /modules/{stem}/trees/start, /modules/{stem}/diagnostics/orchestrator,
# /wizards/{run_id}/answer|back|restart|feedback(+submit,+cancel)) se movió a
# webapp/routers/wizard.py (Fase 2J-C), incluido vía
# app.include_router(wizard_router.router) arriba. _save_tree_suggestion,
# _norm_tokens, _resolve_tree, _follow_wizard_redirect, _fragment, _thanks y
# _feedback_form se co-localizaron allí (únicos consumidores).


# POST /modules/{stem}/assistant/chat y las 3 rutas de feedback sobre una
# respuesta (POST /assistant/responses/{reply_id}/feedback(+submit,+cancel))
# se movieron a webapp/routers/assistant.py (Fase 2J-B), incluido vía
# app.include_router(assistant_router.router) arriba. _assistant_feedback_template
# se co-localizó allí (su único consumidor).


# /telemetry/event se movió a webapp/routers/telemetry.py (Fase 2A), incluido
# vía app.include_router(telemetry_router.router) arriba. La constante
# _TELEMETRY_EVENTS se co-localizó allí (su único consumidor).


# =========================================================
# Herramientas de gestión (Analítica interna) — solo diagnóstico
# =========================================================

def _require_diagnostic(request: Request) -> None:
    if not access.is_diagnostic(request):
        raise HTTPException(403, "Solo disponible en modo diagnóstico")


def _require_hub_metrics(request: Request) -> None:
    """Frontera de seguridad REAL de Usage Metrics (owner ticket "HUB METRICS
    — CENTRALIZE ACCESS AND ENABLE BY VERTICAL") — reemplaza el histórico
    `_require_diagnostic` + owner-only de estas 4 rutas por la autorización
    CENTRALIZADA (`webapp.hub_metrics_access`), que YA decide sola el
    fallback a APP_OWNER cuando `HUB_METRICS_ALLOWED_USERS` no está
    configurado (ver ese módulo) — nunca un segundo chequeo de owner acá.
    Deliberadamente SIN `_require_diagnostic`: esta vista ahora también se
    sirve desde Operations (fuera del modo diagnóstico del Copiloto, un
    concepto de OTRO vertical) — MISMO mensaje/código 403 que el resto del
    Hub usa para "no autorizado"."""
    if not hub_metrics_access.can_access_hub_metrics_request(request):
        raise HTTPException(403, "No autorizado para Usage Metrics")


def _hub_metrics_visibility(request: Request) -> tuple[frozenset[str], frozenset[str]]:
    """(allowed_scopes, allowed_areas) del usuario actual (owner ticket "HUB
    METRICS — ENFORCE VERTICAL DATA VISIBILITY" §4) — SIEMPRE se calcula y
    pasa a `build_summary` en las 5 rutas de Metrics de acá abajo, nunca
    opcional: es la frontera real que evita que un usuario sin Comercial (p.
    ej.) reciba scope=commercial/area=crm aunque lo pida por query param a
    mano (`build_summary` intersecta la selección pedida contra lo
    AUTORIZADO, nunca contra "todo lo que existe" — ver ese módulo)."""
    return hub_metrics_access.metrics_visibility_for(auth_service.session_user(request))


def _admin_result(request: Request, title: str, output: str):
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_result.html",
        context={"title": title, "output": output},
    )


@app.get("/admin/activity", response_class=HTMLResponse, include_in_schema=False)
def admin_activity(
    request: Request, date_from: str = "", date_to: str = "",
    user_filter: str = Query("", alias="user"), module_filter: str = Query("", alias="module"),
    scope_filter: list[str] = Query([], alias="scope"), area_filter: list[str] = Query([], alias="area"),
):
    _require_hub_metrics(request)
    allowed_scopes, allowed_areas = _hub_metrics_visibility(request)
    summary = activity_analytics.build_summary(
        date_from, date_to, user_filter, module_filter,
        scopes=scope_filter, areas=area_filter,
        allowed_scopes=allowed_scopes, allowed_areas=allowed_areas,
    )
    ctx = {"summary": summary, "date_from": date_from, "date_to": date_to}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/admin_activity_content.html", context=ctx,
        )
    session_user = auth_service.session_user(request)
    selected = DEFAULT_PACK
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="admin_activity_page.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request),
            "pack_label": content_pack_short_label(selected), "pack": selected, "user": session_user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(session_user),
            **ctx,
        },
    )


@app.get("/admin/activity/trend", response_class=HTMLResponse, include_in_schema=False)
def admin_activity_trend(
    request: Request, date_from: str = "", date_to: str = "",
    user_filter: str = Query("", alias="user"), module_filter: str = Query("", alias="module"),
    scope_filter: list[str] = Query([], alias="scope"), area_filter: list[str] = Query([], alias="area"),
    trend_page: int = 1,
):
    """Fragment liviano de solo 'Tendencia diaria' (barras + paginador-
    guardrail) — MISMO patrón que admin_activity_stories/admin_activity_tree_runs:
    pasar de página no toca el resto del dashboard (orden de drag-and-drop,
    abierto/cerrado de las demás secciones)."""
    _require_hub_metrics(request)
    allowed_scopes, allowed_areas = _hub_metrics_visibility(request)
    summary = activity_analytics.build_summary(
        date_from, date_to, user_filter, module_filter,
        trend_page=trend_page,
        scopes=scope_filter, areas=area_filter,
        allowed_scopes=allowed_scopes, allowed_areas=allowed_areas,
    )
    return templates.TemplateResponse(
        request=request, name="partials/admin_activity_trend.html",
        context={"summary": summary, "date_from": date_from, "date_to": date_to},
    )


@app.get("/admin/activity/stories", response_class=HTMLResponse, include_in_schema=False)
def admin_activity_stories(
    request: Request, date_from: str = "", date_to: str = "",
    user_filter: str = Query("", alias="user"), module_filter: str = Query("", alias="module"),
    scope_filter: list[str] = Query([], alias="scope"), area_filter: list[str] = Query([], alias="area"),
    story_page: int = 1, story_page_size: int = 10,
):
    """Fragment liviano de solo la 'Historia de preguntas' (lista + paginador),
    para que pasar de página no reemplace el dashboard entero (perdería el
    orden de drag-and-drop y el abierto/cerrado de las demás secciones)."""
    _require_hub_metrics(request)
    allowed_scopes, allowed_areas = _hub_metrics_visibility(request)
    summary = activity_analytics.build_summary(
        date_from, date_to, user_filter, module_filter, story_page, story_page_size,
        scopes=scope_filter, areas=area_filter,
        allowed_scopes=allowed_scopes, allowed_areas=allowed_areas,
    )
    return templates.TemplateResponse(
        request=request, name="partials/admin_activity_stories.html",
        context={"summary": summary, "date_from": date_from, "date_to": date_to},
    )


@app.get("/admin/activity/tree-runs", response_class=HTMLResponse, include_in_schema=False)
def admin_activity_tree_runs(
    request: Request, date_from: str = "", date_to: str = "",
    user_filter: str = Query("", alias="user"), module_filter: str = Query("", alias="module"),
    scope_filter: list[str] = Query([], alias="scope"), area_filter: list[str] = Query([], alias="area"),
    run_page: int = 1, run_page_size: int = 10,
):
    """Fragment liviano de solo 'Recorridos' (árboles de decisión) — mismo
    patrón que admin_activity_stories: pasar de página no toca el resto del
    dashboard."""
    _require_hub_metrics(request)
    allowed_scopes, allowed_areas = _hub_metrics_visibility(request)
    summary = activity_analytics.build_summary(
        date_from, date_to, user_filter, module_filter,
        run_page=run_page, run_page_size=run_page_size,
        scopes=scope_filter, areas=area_filter,
        allowed_scopes=allowed_scopes, allowed_areas=allowed_areas,
    )
    return templates.TemplateResponse(
        request=request, name="partials/admin_activity_tree_runs.html",
        context={"summary": summary, "date_from": date_from, "date_to": date_to},
    )


@app.get("/operaciones/metricas", response_class=HTMLResponse, include_in_schema=False)
def operations_metrics(
    request: Request, date_from: str = "", date_to: str = "",
    user_filter: str = Query("", alias="user"), module_filter: str = Query("", alias="module"),
    scope_filter: list[str] = Query([], alias="scope"), area_filter: list[str] = Query([], alias="area"),
):
    """Usage Metrics expuesto DESDE Operations (owner ticket "HUB METRICS —
    CENTRALIZE ACCESS AND ENABLE BY VERTICAL" §6): el MISMO reporte que
    /admin/activity (activity_analytics.build_summary +
    partials/admin_activity_content.html) — nunca una copia/fork, solo el
    shell alrededor cambia (operations_nav.html en vez del nav de Copiloto).

    TRES chequeos independientes, ninguno sustituye a otro (§1/§G/§H del
    ticket): admisión a Operations (`operations_access` — MISMO patrón que
    reporting.py/staffing.py, sin un segundo parser), la superficie
    "operations" habilitada en HUB_METRICS_ENABLED_VERTICALS, y la
    autorización de Metrics en sí (`_require_hub_metrics`, ver ahí — YA
    resuelve el fallback a APP_OWNER cuando la lista no está configurada).
    Ser Metrics-usuario NUNCA otorga Operations por sí solo, y viceversa."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations")
    if not hub_metrics_access.is_vertical_metrics_enabled("operations"):
        raise HTTPException(403, "Usage Metrics no está habilitado para Operations")
    _require_hub_metrics(request)
    user = auth_service.session_user(request)
    allowed_scopes, allowed_areas = hub_metrics_access.metrics_visibility_for(user)
    summary = activity_analytics.build_summary(
        date_from, date_to, user_filter, module_filter,
        scopes=scope_filter, areas=area_filter,
        allowed_scopes=allowed_scopes, allowed_areas=allowed_areas,
    )
    return templates.TemplateResponse(
        request=request, name="operations/metrics.html",
        context={
            "current_workspace": workspaces.OPERATIONS,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            # Resalta "03 Métricas de uso" en operations_nav.html.
            "active_page": "metrics",
            # Ya pasamos los 3 chequeos arriba para llegar hasta acá -- True
            # a secas, MISMO campo que staffing.py/reporting.py calculan
            # para que el link se siga mostrando (activo) en esta página.
            "can_hub_metrics": True,
            "summary": summary, "date_from": date_from, "date_to": date_to,
        },
    )


@app.get("/admin/runtime/inventory", response_class=HTMLResponse)
def admin_runtime_inventory(request: Request):
    _require_diagnostic(request)
    rows, total_files, total_size, _latest = admin_tools.runtime_inventory()
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_runtime.html",
        context={
            "rows": rows,
            "total_files": total_files,
            "total_size": admin_tools._format_bytes(total_size),
        },
    )


@app.get("/admin/runtime/export.zip")
def admin_runtime_export(request: Request):
    _require_diagnostic(request)
    payload = admin_tools.runtime_export_zip()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{admin_tools.runtime_export_filename()}"'
        },
    )


@app.post("/admin/runtime/purge", response_class=HTMLResponse)
def admin_runtime_purge(
    request: Request,
    folders: list[str] = Form([]),
    date_from: str = Form(""),
    date_to: str = Form(""),
    mode: str = Form("preview"),
):
    _require_diagnostic(request)
    output = admin_tools.runtime_purge(folders, date_from, date_to, dry_run=(mode != "delete"))
    return _admin_result(request, "Borrado selectivo de runtime", output)


@app.post("/admin/cases/analyze", response_class=HTMLResponse)
def admin_cases_analyze(request: Request, llm: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_cases_analysis(use_llm=_truthy(llm))
    return _admin_result(request, "Case Library — análisis", output)


@app.post("/admin/reconciliation/analyze", response_class=HTMLResponse)
def admin_reconciliation_analyze(request: Request):
    _require_diagnostic(request)
    output = admin_tools.run_reconciliation_analysis()
    return _admin_result(request, "Reconciliación diagnóstico ⇄ automatización", output)


# Las 4 lecturas cacheadas (_catalog_entries, _catalog_architecture_connections,
# _catalog_data_products_entries, _catalog_security_entries) y sus 5 funciones
# delgadas de consulta se movieron a webapp/services/catalog_reads.py (Fase 2I).
# Aliases transicionales abajo: los consumidores que TODAVÍA viven en main
# (rutas de Catalog residuales) siguen usando estos nombres sin cambios.
# _catalog_domains, _catalog_connection_types, _catalog_security_families y
# _catalog_data_products_list se removieron (Fase 2J-D): su único consumidor
# era module_page, que ahora importa `catalog_reads` directo en
# webapp/routers/modules.py. _catalog_security_criteria no tiene consumidor
# en main.py desde antes de esta fase (deuda de Catalog preexistente, fuera
# de alcance — no se toca).
_catalog_entries = catalog_reads._catalog_entries
_catalog_architecture_connections = catalog_reads._catalog_architecture_connections
_catalog_data_products_entries = catalog_reads._catalog_data_products_entries
_catalog_security_entries = catalog_reads._catalog_security_entries
_catalog_security_criteria = catalog_reads._catalog_security_criteria


def _invalidate_catalog_cache() -> None:
    """Limpia los @lru_cache de lectura del catálogo (ahora en catalog_reads)
    tras una regeneración (self-service /catalog/regenerate o
    /admin/catalog/generate) — si no, la página de módulos seguiría sirviendo
    el catalog_local viejo hasta un restart del proceso."""
    _catalog_entries.cache_clear()
    _catalog_architecture_connections.cache_clear()
    _catalog_data_products_entries.cache_clear()
    _catalog_security_entries.cache_clear()
    assessment_service.invalidate_pool_index()   # la vigencia del assessment se recalcula contra el pool regenerado


def _catalog_data_product_artifact_name(name: str) -> str:
    tail = str(name or "").strip()
    tail = tail.split(":", 1)[1] if ":" in tail else tail
    tail = tail.split(".", 1)[1] if "." in tail else tail
    return tail.strip() or str(name or "").strip()


def _catalog_data_product_lineage_svg(product, show_lineage: bool = False) -> str:
    entities = []
    seen = set()
    for ent in getattr(product, "entities", []) or []:
        if not isinstance(ent, dict):
            continue
        if str(ent.get("kind") or "").strip().lower() != "entity":
            continue
        raw_name = str(ent.get("name") or "")
        display = _catalog_data_product_artifact_name(raw_name)
        key = display.lower()
        if not display or key in seen:
            continue
        seen.add(key)
        entities.append({"name": raw_name, "display": display, "label": str(ent.get("label") or "")})
    if not entities:
        return ""
    # Label por nodo de linaje (misma fuente que usa el heading de la sección) para que
    # el ancla de la vista SIMPLE también matchee el id de sección (Descripción · ID).
    _node_label = {
        str(n.get("display") or "").strip().lower(): str(n.get("label") or "")
        for n in (getattr(product, "lineage_nodes", []) or []) if isinstance(n, dict)
    }

    _node_icon = {
        str(n.get("display") or "").strip().lower(): artifact_icons.artifact_type(artefacto_tecnico=str(n.get("kind_label") or "") or None)
        for n in (getattr(product, "lineage_nodes", []) or []) if isinstance(n, dict)
    }

    def _dp_node_text(x: float, y: float, ytxt: str, num: str, label: str, icon_key: str | None, cut: int) -> str:
        """Texto del nodo SIMPLE. Sin ícono: la salida histórica (número + nombre en un solo <text>); con ícono: número, ícono del registro y nombre desplazado."""
        if not icon_key:
            return (f'<text x="{x + 16}" y="{ytxt}" class="ln-node-text"><tspan class="ln-num">{num}</tspan> '
                    f'{html.escape(label[:cut] + ("..." if len(label) > cut else ""), quote=True)}</text>')
        short = label[:cut - 7] + ("..." if len(label) > cut - 7 else "")
        return (f'<text x="{x + 16}" y="{ytxt}" class="ln-node-text"><tspan class="ln-num">{num}</tspan></text>'
                + artifact_icons.icon_group(icon_key, x=x + 40, y=y + 13.5, size=15)
                + f'<text x="{x + 62}" y="{ytxt}" class="ln-node-text">{html.escape(short, quote=True)}</text>')

    def _dp_anchor(display: str, ent_label: str = "") -> str:
        lbl = (_node_label.get(display.lower()) or ent_label or "").strip()
        desc = lbl if (lbl and lbl != display) else display
        return slugify(f"{desc} · {display}")

    if show_lineage and getattr(product, "lineage_nodes", None):
        published_by_display = {ent["display"].lower(): idx for idx, ent in enumerate(entities)}
        published_node_names: list[str] = []
        lineage_nodes = []
        extra_index = len(entities)
        used_names: set[str] = set()

        def _layer(node: dict, is_product: bool = False) -> int:
            if is_product:
                return 5
            if node.get("runtime") == "object_store":
                return 4
            if node.get("remote_entity"):
                return 1
            raw = (str(node.get("display") or node.get("technical_name") or node.get("name") or "")).upper()
            if raw.startswith(("0", "1", "SAPI", "ABAP")):
                return 1
            if raw.startswith("2"):
                return 2
            if raw.startswith("3"):
                return 3
            return 3

        for raw_node in getattr(product, "lineage_nodes", []) or []:
            if not isinstance(raw_node, dict):
                continue
            name = str(raw_node.get("name") or "").strip()
            display = str(raw_node.get("display") or _catalog_data_product_artifact_name(name)).strip()
            if not name or name in used_names:
                continue
            used_names.add(name)
            disp_key = display.lower()
            runtime = str(raw_node.get("runtime") or "")
            is_pub = bool(raw_node.get("published")) or disp_key in published_by_display
            # Ancla = MISMO texto plano que el heading de la sección (Descripción · ID).
            # Usa el label por-display (last-wins, igual que el generador arma la sección):
            # nodos HANA/HDLF homónimos comparten sección, así que comparten ancla.
            slug = _dp_anchor(display)
            if is_pub:
                idx = published_by_display.get(disp_key, len(published_node_names))
                num = idx + 2
                if runtime == "object_store":
                    published_node_names.append(name)
            else:
                extra_index += 1
                num = extra_index + 1
            icon_key = artifact_icons.artifact_type(artefacto_tecnico=str(raw_node.get("kind_label") or "") or None)        # misma fuente de verdad de tipos; sin metadata → sin ícono
            title_parts = [str(raw_node.get("label") or raw_node.get("kind_label") or display)]
            if runtime:
                title_parts.append("Object Store" if runtime == "object_store" else "HANA")
            if raw_node.get("delta_capable"):
                title_parts.append("delta capable")
            lineage_nodes.append({
                "name": name,
                "label": display,
                "title": " · ".join([p for p in title_parts if p]),
                "layer": _layer(raw_node),
                "upstreams": [str(x) for x in raw_node.get("upstreams") or []],
                "assoc_upstreams": [str(x) for x in raw_node.get("assoc_upstreams") or []],
                "slug": slug,
                "num": num,
                "is_factcube": False,
                "runtime": runtime,
                "subtext": " · ".join(filter(None, [
                    "delta capable" if raw_node.get("delta_capable") else "",
                    "compartido" if raw_node.get("shared") else "",
                ])),
                **({"icon": icon_key} if icon_key else {}),
            })
        if lineage_nodes:
            product_label = getattr(product, "business_name", "") or getattr(product, "title", "") or getattr(product, "product_id", "")
            root_name = "__data_product__"
            lineage_nodes.append({
                "name": root_name,
                "label": str(product_label),
                "title": str(product_label),
                "layer": 5,
                "upstreams": published_node_names,
                "assoc_upstreams": [],
                "slug": "resumen",
                "num": 1,
                "is_factcube": False,
                "runtime": "object_store",
                "subtext": "Data Product",
                "icon": "data_product",                    # tipo PROPIO: un Data Product no es un Modelo Analítico (no usa el cubo)
            })
            return _catalog_lineage_svg(lineage_nodes, True, {n["name"] for n in lineage_nodes})

    def esc(value: str) -> str:
        return html.escape(str(value or ""), quote=True)

    H, VGAP, HEAD = 42, 14, 28
    left_x, right_x = 22, 360
    left_w, right_w = 300, 250
    rows = max(1, len(entities))
    height = HEAD + rows * (H + VGAP) - VGAP + 8
    width = right_x + right_w + 24
    product_y = HEAD + max(0, (rows - 1) * (H + VGAP) / 2)
    product_label = getattr(product, "business_name", "") or getattr(product, "title", "") or getattr(product, "product_id", "")
    p = [
        f'<div class="catalog-lineage__scroll"><svg class="lineage-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="Linaje del Data Product">',
        '<defs><marker id="dp-ln-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" class="ln-arrowhead"/></marker></defs>',
        '<text x="22" y="12" class="ln-col-label">ENTIDADES PUBLICADAS</text>',
        '<text x="360" y="12" class="ln-col-label">DATA PRODUCT</text>',
    ]
    for i, ent in enumerate(entities):
        y = HEAD + i * (H + VGAP)
        slug = _dp_anchor(ent["display"], ent.get("label") or "")
        label = ent["display"]
        p.append(f'<path d="M{left_x + left_w},{y + H / 2:.1f} C{left_x + left_w + 45},{y + H / 2:.1f} {right_x - 55},{product_y + H / 2:.1f} {right_x - 4},{product_y + H / 2:.1f}" class="ln-edge" marker-end="url(#dp-ln-arrow)"/>')
        p.append(f'<a href="#{esc(slug)}" class="ln-node-a" data-catalog-anchor="#{esc(slug)}">')
        p.append(f'<rect x="{left_x}" y="{y}" width="{left_w}" height="{H}" rx="8" class="ln-node"/>')
        p.append(_dp_node_text(left_x, y, str(y + 26), f"{i + 2:02d}", label, _node_icon.get(label.lower()), 42))
        p.append('</a>')
    p += [
        '<a href="#resumen" class="ln-node-a" data-catalog-anchor="#resumen">',
        f'<rect x="{right_x}" y="{product_y:.1f}" width="{right_w}" height="{H}" rx="8" class="ln-node"/>',
        _dp_node_text(right_x, product_y, f"{product_y + 26:.1f}", "01", str(product_label), "data_product", 34),
        '</a>',
        '</svg></div>',
    ]
    return "".join(p)

# _catalog_security_entries/_catalog_security_families/_catalog_security_criteria
# se movieron a webapp/services/catalog_reads.py (Fase 2I); alias transicional
# arriba, junto al resto del cierre mínimo de dependencia.


def _catalog_conn_label(conn) -> str:
    return conn.business_name or conn.connection or conn.type_label


def _catalog_md_value(value: str, code: bool = False) -> str:
    value = str(value or "").strip()
    if not value:
        return "-"
    value = value.replace("`", "")
    value = value.replace("|", "\\|")
    if code:
        return f"`{value}`"
    return value


def _catalog_space_sort_key(space: str) -> tuple[int, str]:
    raw = str(space or "").strip()
    norm = unicodedata.normalize("NFKD", raw.upper())
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    order = {
        "DEV": 10, "DES": 10, "DESA": 10, "DESARROLLO": 10, "DEVELOPMENT": 10,
        "QA": 20, "QAS": 20, "CALIDAD": 20, "QUALITY": 20, "TEST": 20,
        "PRD": 30, "PROD": 30, "PRODUCTIVO": 30, "PRODUCCION": 30, "PRODUCTION": 30,
    }
    return (order.get(norm, 100), norm)


def _catalog_connections_svg(nodes: list[dict], spaces: list[str]) -> str:
    if not nodes:
        return ""
    H, VGAP, HEAD = 40, 16, 34
    left_x, ds_x = 28, 610
    NUM_W, CHAR, PAD_R, WMIN, WMAX = 30, 6.3, 14, 170, 360
    ds_w = 260
    spaces = sorted(spaces or ["Datasphere"], key=_catalog_space_sort_key)

    def esc(v: str) -> str:
        return html.escape(str(v or ""), quote=True)

    def _display_label(node: dict) -> str:
        label = str(node.get("label") or "").strip()
        meta = str(node.get("meta") or "").strip()
        return label

    def _node_dims(label: str) -> tuple[int, str]:
        disp = (label[:48] + "...") if len(label) > 51 else label
        w = max(WMIN, min(WMAX, int(NUM_W + len(disp) * CHAR + PAD_R)))
        return w, disp

    def _svg_key(value: str) -> str:
        raw = unicodedata.normalize("NFKD", str(value or ""))
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw.strip().lower()).strip("-")
        return raw or "space"

    space_keys = {space: _svg_key(space) for space in spaces}
    node_dims = [_node_dims(_display_label(n)) for n in nodes]
    row_h = H + VGAP
    top = HEAD + 30
    h = max(230, top + max(len(nodes), len(spaces)) * row_h + 22)
    ds_h = max(154, 64 + len(spaces) * 34)
    ds_y = max(36, (h - ds_h) // 2)
    first_space_y = ds_y + 62
    space_y = {space: first_space_y + i * 34 for i, space in enumerate(spaces)}
    width = max(920, ds_x + ds_w + 28)
    highlight_rules = []
    for key in sorted(set(space_keys.values())):
        hover_focus = f".conn-lineage-svg:has(.conn-space-link--{key}:focus)"
        highlight_rules.append(
            f"{hover_focus} .conn-edge:not(.conn-edge-to-{key}){{opacity:.12;}}"
            f"{hover_focus} .conn-edge-to-{key}{{opacity:1;stroke:#e7b008;stroke-width:2.8;}}"
            f"{hover_focus} .conn-edge-to-{key}.conn-edge--muted{{opacity:.58;}}"
            f"{hover_focus} .conn-space-link--{key} .conn-space{{stroke:#e7b008;stroke-width:1.6;}}"
        )
    highlight_style = "<style>" + "".join(highlight_rules) + "</style>" if highlight_rules else ""

    parts = [
        '<div class="catalog-lineage__scroll">',
        f'<svg class="lineage-svg conn-lineage-svg" viewBox="0 0 {width} {h}" width="{width}" height="{h}" role="img" aria-label="Arquitectura y conectividad del cliente">',
        '<defs><marker id="conn-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" fill="context-stroke"/></marker></defs>',
        highlight_style,
        f'<text x="{left_x}" y="18" class="ln-col-label">Fuentes de Datos</text>',
        f'<text x="{ds_x}" y="18" class="ln-col-label">SAP Datasphere</text>',
        f'<rect x="{ds_x}" y="{ds_y}" width="{ds_w}" height="{ds_h}" rx="8" class="ln-node conn-ds-box"/>',
        f'<text x="{ds_x + ds_w - 16}" y="{ds_y + 30}" text-anchor="end" class="ln-node-text conn-ds-title">Espacios</text>',
    ]
    for space in spaces:
        y = space_y[space]
        skey = space_keys.get(space, _svg_key(space))
        parts.append(f'<g class="conn-space-link conn-space-link--{skey}" tabindex="0" role="button" aria-label="Resaltar conexiones hacia {esc(space)}">')
        parts.append(f'<rect x="{ds_x + 16}" y="{y - 16}" width="{ds_w - 32}" height="28" rx="8" class="ln-node conn-space"/>')
        parts.append(f'<text x="{ds_x + 30}" y="{y + 2}" class="ln-node-text conn-space-text">{esc(space)}</text>')
        parts.append('</g>')
    for idx, (node, (node_w, label)) in enumerate(zip(nodes, node_dims), 1):
        y = top + (idx - 1) * row_h
        cy = y + H / 2
        klass = "conn-node-wrap" + (" conn-node-wrap--muted" if not node.get("selected") else "")
        edge_class = "ln-edge conn-edge" + (" conn-edge--muted" if not node.get("selected") else "")
        href = f'#{esc(node.get("slug", ""))}'
        title = esc(_display_label(node))
        sx = left_x + node_w
        dx = max(32, (ds_x - sx) / 2)
        node_spaces = sorted(node.get("spaces") or [node.get("space") or spaces[0]], key=_catalog_space_sort_key)
        for target_space in node_spaces:
            target_y = space_y.get(target_space, ds_y + ds_h / 2)
            target_key = space_keys.get(target_space, _svg_key(target_space))
            parts.append(f'<path class="{edge_class} conn-edge-to-{target_key}" marker-end="url(#conn-arrow)" d="M{sx},{cy:.0f} C{sx+dx:.0f},{cy:.0f} {ds_x-dx:.0f},{target_y} {ds_x + 16},{target_y}"/>')
        parts.append(f'<a class="ln-node-a conn-node-a" href="{href}"><title>{title}</title><g class="{klass}">')
        parts.append(f'<rect x="{left_x}" y="{y}" width="{node_w}" height="{H}" rx="8" class="ln-node conn-node"/>')
        parts.append(f'<text x="{left_x + 11}" y="{cy + 4:.0f}" class="ln-num">{idx:02d}</text>')
        parts.append(f'<text x="{left_x + 32}" y="{cy + 4:.0f}" class="ln-node-text conn-node-title">{esc(label)}</text>')
        parts.append('</g></a>')
    parts.append('</svg></div>')
    return "".join(parts)


def _catalog_connection_features_text(mod, conn) -> str:
    features = mod.connection_feature_states([conn]) if hasattr(mod, "connection_feature_states") else []
    return " · ".join(
        f"{f['label']} ({'disponible' if f.get('enabled') else 'posible / no disponible'})"
        for f in features
    ) or "-"


def _catalog_conn_norm_key(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def _catalog_conn_lookup(mapping: dict, *keys: str) -> str:
    if not isinstance(mapping, dict):
        return ""
    by_norm = {_catalog_conn_norm_key(k): str(v or "").strip() for k, v in mapping.items()}
    for key in keys:
        value = by_norm.get(_catalog_conn_norm_key(key), "")
        if value and value not in {"—", "-", "None", "none", "null", "?"}:
            return value
    return ""


def _catalog_bool_text(value, default: bool | None = None) -> str:
    if value is None or str(value).strip() == "":
        if default is None:
            return "-"
        return "Sí" if default else "No"
    norm = _catalog_conn_norm_key(str(value))
    if norm in {"si", "true", "1", "on", "yes", "connected", "dpagent"}:
        return "Sí"
    if norm in {"no", "false", "0", "off", "none", "null"}:
        return "No"
    return str(value).strip()


def _catalog_words(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _catalog_has_feature(mod, conn, label: str) -> bool:
    states = mod.connection_feature_states([conn]) if hasattr(mod, "connection_feature_states") else []
    wanted = _catalog_conn_norm_key(label)
    return any(_catalog_conn_norm_key(str(item.get("label") or "")) == wanted for item in states)


def _catalog_is_mssql(conn) -> bool:
    return _catalog_conn_norm_key(getattr(conn, "type_id", "")) == "mssql"


def _catalog_is_generic_jdbc(conn) -> bool:
    """Orígenes JDBC genéricos (GENERICJDBC): PostgreSQL, MySQL, etc. Se detectan por
    typeId o, como respaldo, por `driverClass` (marcador inequívoco de JDBC). NO se usa
    `url` como respaldo: OData también tiene url y caería acá por error."""
    if _catalog_conn_norm_key(getattr(conn, "type_id", "")) == "genericjdbc":
        return True
    info = getattr(conn, "info", {}) or {}
    return bool(_catalog_conn_lookup(info, "driverClass"))


def _catalog_is_odata(conn) -> bool:
    """Orígenes OData (typeId ODATA): conectividad por URL de servicio, no formato SAP."""
    return _catalog_conn_norm_key(getattr(conn, "type_id", "")) == "odata"


def _catalog_connection_rows(mod, conn, include_space: bool = False) -> list[tuple[str, str, str]]:
    info = getattr(conn, "info", {}) or {}
    gateway = getattr(conn, "gateway", {}) or {}
    auth = getattr(conn, "authentication", {}) or {}
    props = getattr(conn, "props", {}) or {}
    feats = getattr(conn, "feature_values", {}) or {}

    def lookup(*keys: str) -> str:
        return (
            _catalog_conn_lookup(info, *keys)
            or _catalog_conn_lookup(props, *keys)
            or _catalog_conn_lookup(feats, *keys)
        )

    rows = [
        ("group", "Identificaci\u00f3n", ""),
    ]
    if include_space:
        rows.append(("row", "Espacio", _catalog_md_value(getattr(conn, "space", ""), True)))
    rows.extend([
        ("row", "Business Name", _catalog_md_value(getattr(conn, "business_name", ""))),
        ("row", "Technical Name", _catalog_md_value(getattr(conn, "connection", ""), True)),
    ])

    if _catalog_is_mssql(conn):
        server = lookup("pds_server_name")
        port = lookup("pds_port_number")
        virtual_host = _catalog_conn_lookup(gateway, "virtualHost")
        virtual_port = _catalog_conn_lookup(gateway, "virtualPort")
        virtual_destination = _catalog_conn_lookup(gateway, "virtual_destination") or lookup("virtual_destination")
        if (
            _catalog_conn_lookup(gateway, "cloudConnectorLocation")
            and virtual_host
            and virtual_port
            and virtual_host == server
            and virtual_port == port
        ):
            virtual_destination_label = "Derivar de conectividad"
        else:
            virtual_destination_label = _catalog_bool_text(virtual_destination)
        rows.extend([
            ("group", "Conectividad", ""),
            ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
            ("row", "Servidor", _catalog_md_value(server, True)),
            ("row", "Puerto", _catalog_md_value(port, True)),
            ("row", "Nombre de base de datos", _catalog_md_value(lookup("pds_database_name"), True)),
            ("row", "Version", _catalog_md_value(lookup("version"))),
            ("group", "Cloud Connector", ""),
            ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
            ("row", "Location ID", _catalog_md_value(_catalog_conn_lookup(gateway, "cloudConnectorLocation"), True)),
            ("row", "Virtual Destination", _catalog_md_value(virtual_destination_label)),
            ("group", "Features", ""),
            ("row", "Remote tables", _catalog_bool_text(lookup("remoteTables"), _catalog_has_feature(mod, conn, "Remote Tables"))),
            ("row", "Data Provisioning Agent", _catalog_md_value(lookup("dpAgent") or getattr(conn, "agent", ""), True)),
            ("row", "Data flows", _catalog_bool_text(lookup("dataflows"), _catalog_has_feature(mod, conn, "Data Flows"))),
            ("row", "Replication Flows", _catalog_bool_text(lookup("replicationflows"), _catalog_has_feature(mod, conn, "Replication Flows"))),
        ])
        return rows

    if _catalog_is_generic_jdbc(conn):
        # Origen JDBC genérico (PostgreSQL, MySQL, …): la conectividad se describe con
        # driverClass + url (no con los campos SAP host/mandante/sysid). El acceso a
        # datos vive en adapterName (Data Provisioning) y kind (Data Access).
        cred_mode = _catalog_conn_lookup(props, "Modo credencial", "credentialMode")
        adapter_name = getattr(conn, "adapter", "") or lookup("adapterName", "adapter")
        kind = _catalog_conn_lookup(info, "kind")
        rows.extend([
            ("group", "Conectividad", ""),
            ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
            ("row", "JDBC driver class", _catalog_md_value(lookup("driverClass"), True)),
            ("row", "JDBC URL", _catalog_md_value(lookup("url"), True)),
            ("group", "Cloud Connector", ""),
            ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
            ("group", "Autenticación", ""),
            ("row", "Modo credencial", _catalog_md_value(cred_mode)),
            ("group", "Remote Tables", ""),
            ("row", "Data Provisioning", _catalog_md_value(adapter_name, True)),
            ("row", "Data Access", _catalog_md_value(kind, True)),
            ("row", "Data Provisioning Agent", _catalog_md_value(lookup("dpAgent") or getattr(conn, "agent", ""), True)),
            ("row", "Estado", _catalog_md_value(getattr(conn, "agent_status", ""))),
        ])
        # Replication Flows: solo si la conexión declara la capacidad. Cuando FEATURES
        # trae REPLICATION FLOW = No, el grupo (con Fast Serialization) se omite.
        if _catalog_has_feature(mod, conn, "Replication Flows"):
            rows.extend([
                ("group", "Replication Flows", ""),
                ("row", "Fast Serialization", _catalog_bool_text(_catalog_conn_lookup(feats, "useFastSerialization"))),
            ])
        rows.extend([
            ("group", "Features", ""),
            ("row", "Remote tables", "Sí" if _catalog_has_feature(mod, conn, "Remote Tables") else "No"),
            ("row", "Data flows", "Sí" if _catalog_has_feature(mod, conn, "Data Flows") else "No"),
            ("row", "Replication Flows", "Sí" if _catalog_has_feature(mod, conn, "Replication Flows") else "No"),
        ])
        return rows

    if _catalog_is_odata(conn):
        # Origen OData: la conectividad es la URL del servicio + versión (no los campos
        # SAP host/mandante/sysid). Cloud Connector con Virtual Destination condicional.
        # Sin grupos Remote Tables / Replication Flows (no aplican al patrón OData).
        auth_odata = _catalog_conn_lookup(info, "authentication") or _catalog_conn_lookup(auth, "auth_type")
        rows.extend([
            ("group", "Conectividad", ""),
            ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
            ("row", "URL", _catalog_md_value(_catalog_conn_lookup(info, "url"), True)),
            ("row", "Version", _catalog_md_value(_catalog_conn_lookup(info, "version"))),
            ("group", "Cloud Connector", ""),
            ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
            ("row", "Location ID", _catalog_md_value(_catalog_conn_lookup(gateway, "cloudConnectorLocation"), True)),
        ])
        vdest = _catalog_bool_text(_catalog_conn_lookup(gateway, "virtual_destination"))
        rows.append(("row", "Virtual Destination", vdest))
        if vdest == "Sí":   # solo si hay virtual destination tiene sentido host/puerto
            rows.extend([
                ("row", "Virtual Host", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualHost"), True)),
                ("row", "Virtual Port", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualPort"), True)),
            ])
        rows.extend([
            ("group", "Autenticación", ""),
            ("row", "Tipo", _catalog_md_value(auth_odata)),
            ("group", "Features", ""),
            ("row", "Remote tables", "Sí" if _catalog_has_feature(mod, conn, "Remote Tables") else "No"),
            ("row", "Data flows", "Sí" if _catalog_has_feature(mod, conn, "Data Flows") else "No"),
            ("row", "Replication Flows", "Sí" if _catalog_has_feature(mod, conn, "Replication Flows") else "No"),
        ])
        return rows

    auth_type = _catalog_conn_lookup(auth, "auth_type")
    data_provisioning = _catalog_conn_lookup(feats, "dataProvisioningOption") or getattr(conn, "data_provisioning", "")
    data_access = _catalog_words(_catalog_conn_lookup(feats, "dataAccess"))
    dp_agent = _catalog_conn_lookup(feats, "dpAgent") or getattr(conn, "agent", "")
    sac_tunnel = _catalog_conn_lookup(feats, "sac_tunnel_connection_name")

    rows.extend([
        ("group", "Conectividad", ""),
        ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
        ("row", "Modo de conexión", _catalog_md_value(_catalog_conn_lookup(info, "connectionType"))),
        ("row", "Application Server", _catalog_md_value(_catalog_conn_lookup(info, "host"), True)),
        ("row", "System Number", _catalog_md_value(_catalog_conn_lookup(info, "systemnumber"), True)),
        ("row", "Mandante", _catalog_md_value(_catalog_conn_lookup(info, "client"), True)),
        ("row", "ID", _catalog_md_value(_catalog_conn_lookup(info, "sysid"), True)),
        ("row", "Idioma", _catalog_md_value(_catalog_conn_lookup(info, "langcode"), True)),
        ("group", "Cloud Connector", ""),
        ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
        ("row", "Location ID", _catalog_md_value(_catalog_conn_lookup(gateway, "cloudConnectorLocation"), True)),
        ("row", "Virtual Destination", _catalog_bool_text(_catalog_conn_lookup(gateway, "virtual_destination"))),
        ("row", "Virtual Host", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualHost"), True)),
        ("row", "Virtual Port", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualPort"), True)),
        ("group", "Autenticaci\u00f3n", ""),
        ("row", "Tipo", _catalog_md_value(auth_type)),
        ("group", "Remote Tables", ""),
        ("row", "Data Provisioning", _catalog_md_value(data_provisioning)),
        ("row", "Data Access", _catalog_md_value(data_access)),
        ("row", "Data Provisioning Agent", _catalog_md_value(dp_agent, True)),
        ("row", "Estado", _catalog_md_value(getattr(conn, "agent_status", ""))),
        ("row", "Streaming Read", _catalog_bool_text(_catalog_conn_lookup(feats, "streaming"))),
        ("group", "Replication Flows", ""),
        ("row", "Fast Serialization", _catalog_bool_text(_catalog_conn_lookup(feats, "useFastSerialization"))),
        ("group", "Features", ""),
        ("row", "Remote tables", "S\u00ed" if _catalog_has_feature(mod, conn, "Remote Tables") else "No"),
        ("row", "Data flows", "S\u00ed" if _catalog_has_feature(mod, conn, "Data Flows") else "No"),
        ("row", "Replication Flows", "S\u00ed" if _catalog_has_feature(mod, conn, "Replication Flows") else "No"),
        ("row", "Model Import", "S\u00ed" if sac_tunnel else "No"),
    ])
    return rows


def _catalog_connection_table_md(mod, conns: list) -> list[str]:
    if not conns:
        return []
    if len(conns) == 1:
        lines = ["| Propiedad | Valor |", "|---|---|"]
        for kind, label, value in _catalog_connection_rows(mod, conns[0], include_space=True):
            if kind == "group":
                lines.append(f"| {{{{table_group: {label}}}}} | |")
            else:
                lines.append(f"| {label} | {value or '-'} |")
        lines.append("")
        return lines

    headers = [_catalog_md_value(conn.space, True) for conn in conns]
    rows_by_conn = [_catalog_connection_rows(mod, conn) for conn in conns]
    lines = ["| Propiedad | " + " | ".join(headers) + " |", "|---|" + "|".join("---" for _ in conns) + "|"]
    for idx, (kind, label, _value) in enumerate(rows_by_conn[0]):
        if kind == "group":
            lines.append("| {{table_group: " + label + "}} | " + " | ".join("" for _ in conns) + " |")
        else:
            values = [rows[idx][2] if idx < len(rows) else "-" for rows in rows_by_conn]
            lines.append("| " + label + " | " + " | ".join(value or "-" for value in values) + " |")
    lines.append("")
    return lines



def _catalog_compliance_intro_md(verdict: str, verdict_text: str, scope: str) -> str:
    label = _catalog_verdict_label(verdict)
    context = str(scope or "la evidencia disponible").strip()
    return (
        f"> Esta lectura consolida los hallazgos detectados sobre {context} y explica por qué "
        f"el catálogo lo clasifica como **{label} · {verdict_text}**."
    )


def _catalog_connection_compliance_html(mod, detail: dict) -> str:
    """Build a deterministic compliance table for Connections & Spaces detail."""
    connections = list(detail.get("connections") or [])
    selected_paths = set(detail.get("selected_paths") or set())
    selected = [conn for conn in connections if not selected_paths or conn.path in selected_paths] or connections
    if not selected:
        return ""

    verdict = str(detail.get("verdict") or "parcial").strip().lower()
    verdict_label = _catalog_verdict_label(verdict)
    verdict_text = _catalog_verdict_text(verdict)
    type_label = str(detail.get("type_label") or detail.get("type_id") or "tipo de conexión")
    count = int(detail.get("count") or len(selected))
    lines = [
        f"- {verdict} **Dictamen del catálogo** _Evidencia:_ {verdict_label} — {verdict_text}; {count} "
        f"{'conexión evaluada' if count == 1 else 'conexiones evaluadas'} para {type_label}."
    ]

    spaces = sorted({str(getattr(conn, "space", "") or "").strip() for conn in selected if getattr(conn, "space", "")}, key=_catalog_space_sort_key)
    business_names = {str(_catalog_conn_label(conn) or "").strip() for conn in selected}
    technical_names = {str(getattr(conn, "connection", "") or "").strip() for conn in selected}
    if len(selected) == 1:
        space_text = spaces[0] if spaces else "el espacio informado"
        lines.append(
            f"- nota **Cobertura por espacio** _Evidencia:_ La conexión está documentada para {space_text}; "
            "no requiere comparación entre ambientes."
        )
    elif len(business_names) == 1 and len(technical_names) == 1:
        lines.append(
            f"- cumple **Consistencia entre espacios** _Evidencia:_ Business Name y Technical Name se mantienen alineados en "
            f"{' | '.join(spaces)}."
        )
    else:
        details = []
        if len(business_names) > 1:
            details.append("Business Name no homogéneo")
        if len(technical_names) > 1:
            details.append("Technical Name no homogéneo")
        lines.append(
            f"- observar **Consistencia entre espacios** _Evidencia:_ {'; '.join(details) or 'hay diferencias de metadata'} "
            f"para {' | '.join(spaces) or 'los espacios evaluados'}."
        )

    try:
        feature_states = mod.connection_feature_states(selected) if mod else []
    except Exception:
        feature_states = []
    enabled = [str(item.get("label") or "") for item in feature_states if item.get("enabled")]
    unavailable = [str(item.get("label") or "") for item in feature_states if not item.get("enabled")]
    if enabled and not unavailable:
        lines.append(
            f"- cumple **Capacidades disponibles** _Evidencia:_ {' | '.join(enabled)} disponibles según configuración de agente, "
            "Cloud Connector y features declaradas."
        )
    elif enabled:
        lines.append(
            f"- observar **Capacidades disponibles** _Evidencia:_ Disponibles: {' | '.join(enabled)}. "
            f"No disponibles o pendientes: {' | '.join(unavailable) or 'sin detalle'}."
        )
    else:
        lines.append(
            "- atender **Capacidades disponibles** _Evidencia:_ No se detectaron capacidades operativas disponibles con la metadata evaluada."
        )

    additional = [conn for conn in connections if selected_paths and conn.path not in selected_paths]
    if additional:
        labels = sorted({f"{getattr(conn, 'space', '')} · {getattr(conn, 'connection', '')}" for conn in additional})
        lines.append(
            f"- observar **Cobertura fuera del origen seleccionado** _Evidencia:_ Existen conexiones adicionales atenuadas para comparación: "
            f"{' | '.join(labels)}."
        )

    return _catalog_compliance_table_html("\n".join(lines))


def _catalog_connection_detail_view(mod, detail: dict, pack: str = ""):
    from webapp.services.content_service import render_sections

    selected_paths = detail.get("selected_paths") or set()
    grouped: dict[tuple, dict] = {}
    for conn in detail.get("connections") or []:
        selected = conn.path in selected_paths
        key = (selected, conn.type_id, conn.connection, _catalog_conn_label(conn))
        group = grouped.setdefault(key, {
            "conns": [],
            "selected": selected,
            "label": f"{conn.connection} \u00b7 {_catalog_conn_label(conn)}",
            "meta": conn.type_label,
        })
        group["conns"].append(conn)

    groups = sorted(
        grouped.values(),
        key=lambda g: (
            not g.get("selected"),
            _catalog_space_sort_key((g.get("conns") or [None])[0].space if g.get("conns") else ""),
            str(g.get("label") or "").lower(),
        ),
    )

    parts: list[str] = []
    for group in groups:
        conns = sorted(group["conns"], key=lambda c: _catalog_space_sort_key(c.space))
        first = conns[0]
        origin_label = _catalog_conn_label(first)
        heading = f"## {{dw-black}}{first.connection} \u00b7 {{dw-gold}}{origin_label}"
        body_parts = [heading, ""]
        count = len(conns)
        if count > 1:
            spaces_word = "espacio distinto" if count == 1 else "espacios distintos"
            body_parts.append(
                f"> El origen {origin_label} ({first.connection}) conecta contra {count} {spaces_word} en Datasphere; "
                "a continuación desglosamos el detalle técnico en cada caso."
            )
            body_parts.append("")
        if not group["selected"]:
            body_parts.append(
                "> Conexión adicional fuera del origen seleccionado. Se muestra por tener activado _Incluir todas las conexiones_."
            )
            body_parts.append("")

        body_parts.extend(_catalog_connection_table_md(mod, conns))
        parts.append("\n".join(body_parts).strip())

    compliance_html = _catalog_connection_compliance_html(mod, detail)
    if compliance_html:
        verdict = str(detail.get("verdict") or "parcial").strip().lower()
        intro = _catalog_compliance_intro_md(
            verdict,
            _catalog_verdict_text(verdict),
            f"las conexiones y espacios de {detail.get('cliente') or 'este cliente'} para {detail.get('type_label') or detail.get('type_id') or 'este tipo de conexión'}",
        )
        parts.append("## Cumplimiento\n\n" + intro + "\n\n" + compliance_html)

    composed = "\n\n".join(parts)
    sections = render_sections(composed, pack_name="", stem="", base=0) if composed else []
    nodes = []
    for group, section in zip(groups, sections):
        node_spaces = []
        for conn in sorted(group["conns"], key=lambda c: _catalog_space_sort_key(c.space)):
            if conn.space and conn.space not in node_spaces:
                node_spaces.append(conn.space)
        nodes.append({
            "slug": section.slug,
            "spaces": node_spaces,
            "label": group["label"],
            "meta": group["meta"],
            "selected": group["selected"],
        })
    svg = _catalog_connections_svg(nodes, detail.get("spaces") or [])
    return sections, svg


@app.get("/catalog/connections", response_class=HTMLResponse, include_in_schema=False)
def catalog_connections(request: Request, type_id: str = "", pack: str = Query(DEFAULT_PACK)):
    # Catálogo operativo de arquitectura/conectividad: conexiones agrupadas por cliente
    # según typeId Datasphere. Determinístico y sin tokens.
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    mod, entries = _catalog_architecture_connections()
    hits = mod.connections_for_type(entries, type_id) if (mod and type_id) else []
    groups = mod.connection_client_groups(hits) if mod else []
    label = mod.connection_type_label(type_id) if (mod and type_id) else ""
    return templates.TemplateResponse(
        request=request, name="partials/catalog_connections.html",
        context={"groups": groups, "type_id": type_id, "type_label": label, "pack": _pack(pack)},
    )


@app.get("/catalog/connections/detail", response_class=HTMLResponse, include_in_schema=False)
def catalog_connection_detail(
    request: Request,
    cliente: str = "",
    type_id: str = "",
    pack: str = Query(DEFAULT_PACK),
    all: int = 0,
):
    user = auth_service.session_user(request)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_architecture_connections()
    detail = mod.connection_client_detail(entries, cliente, type_id, bool(all)) if mod else None
    if detail is None:
        raise HTTPException(404, "Detalle de conexion no encontrado en el catalogo")
    sections, lineage_svg = _catalog_connection_detail_view(mod, detail, selected)
    context = {
        "pack": selected,
        "cliente": detail["cliente"],
        "type_id": detail["type_id"],
        "type_label": detail["type_label"],
        "include_all": detail["include_all"],
        "count": detail["count"],
        "all_count": detail["all_count"],
        "verdict": detail["verdict"],
        "verdict_label": detail["verdict_label"],
        "lineage_svg": lineage_svg,
        "sections": sections,
        "catalog_kind": "connections",
        "catalog_ref": f"{detail['cliente']} · {detail['type_label']}",
    }
    # HTMX → solo el fragmento (dentro de la pestaña Catálogo del módulo 04, sin
    # perder el panel). Navegación directa (deep-link del asistente) → página
    # completa con shell + nav, igual que /catalog/flow.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/catalog_connection_content.html", context=context,
        )
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    context.update({
        "modules": list_modules(selected),
        "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
        "academy": academy,
        "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
        "active_group": "", "active_stem": "",
        "readonly": _readonly(request), "user": user,
        "current_workspace": workspaces.COPILOT,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack_label": content_pack_short_label(selected),
    })
    return templates.TemplateResponse(
        request=request, name="catalog_connection.html", context=context,
    )





def _catalog_security_lineage_svg(entry, show_lineage: bool = False) -> str:
    """SVG liviano para Seguridad/DAC: entidad de permisos -> DAC -> vistas consumidoras."""
    from core.utils import slugify

    def _section_slug_for(needle: str) -> str:
        wanted = str(needle or "").strip()
        if not wanted:
            return ""
        for line in (getattr(entry, "body", "") or "").splitlines():
            if not line.lstrip().startswith("##"):
                continue
            title = re.sub(r"^#+\s*", "", line).strip()
            title = re.sub(r"\{dw-[^}]+\}", "", title, flags=re.I)
            title = title.replace("\\_", "_").replace("**", "")
            if wanted in title:
                return slugify(title)
        return slugify(wanted)

    source_raw = entry.source_entity or "Entidad de permisos"
    source = html.escape(source_raw)
    source_slug = html.escape(_section_slug_for(source_raw))
    dac_raw = entry.dac or "DAC"
    dac = html.escape(dac_raw)
    dac_slug = html.escape(_section_slug_for(dac_raw))
    views_raw = getattr(entry, "lineage_views", []) or []
    views = []
    seen = set()
    for row in views_raw:
        view = str((row or {}).get("view") or "").strip()
        space = str((row or {}).get("space") or "").strip()
        if not view:
            continue
        key = (space, view)
        if key in seen:
            continue
        seen.add(key)
        views.append({"space": space, "view": view})
    visible_views = views[:8] if show_lineage else []
    w = 940
    left_x, mid_x, right_x = 36, 352, 668
    node_w, node_h = 260, 50
    top_y = 78
    rows = max(1, len(visible_views))
    h = max(210, top_y + rows * 62 + 42)
    source_y = top_y + max(0, rows - 1) * 31
    dac_y = source_y

    parts = [
        '<div class="catalog-lineage__scroll">',
        f'<svg class="lineage-svg security-lineage-svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="Linaje de seguridad DAC">',
        '<defs><marker id="sec-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" class="ln-arrowhead"/></marker></defs>',
        f'<text x="{left_x}" y="24" class="ln-col-label">Entidad de Permisos</text>',
        f'<text x="{mid_x}" y="24" class="ln-col-label">Objeto DAC</text>',
    ]
    if show_lineage:
        parts.append(f'<text x="{right_x}" y="24" class="ln-col-label">Linaje consumidor</text>')
    source_open = f'<a href="#{source_slug}" class="ln-node-link" aria-label="Abrir entidad de permisos {source}">' if source_slug else ''
    source_close = '</a>' if source_slug else ''
    dac_open = f'<a href="#{dac_slug}" class="ln-node-link" aria-label="Abrir objeto DAC {dac}">' if dac_slug else ''
    dac_close = '</a>' if dac_slug else ''
    parts.extend([
        source_open,
        f'<rect x="{left_x}" y="{source_y}" width="{node_w}" height="{node_h}" rx="8" class="ln-node"/>',
        f'<text class="ln-num" x="{left_x+14}" y="{source_y+28}">01</text>',
        f'<text class="ln-node-text" x="{left_x+38}" y="{source_y+28}">{source}</text>',
        source_close,
        dac_open,
        f'<rect x="{mid_x}" y="{dac_y}" width="{node_w}" height="{node_h}" rx="8" class="ln-node security-dac-node"/>',
        f'<text class="ln-num" x="{mid_x+14}" y="{dac_y+28}">02</text>',
        f'<text class="ln-node-text" x="{mid_x+38}" y="{dac_y+28}">{dac}</text>',
        dac_close,
        f'<path class="ln-edge" d="M{left_x+node_w},{source_y+node_h/2:.0f} C{left_x+node_w+70},{source_y+node_h/2:.0f} {mid_x-70},{dac_y+node_h/2:.0f} {mid_x},{dac_y+node_h/2:.0f}" marker-end="url(#sec-arrow)"/>',
    ])
    if show_lineage and visible_views:
        for idx, row in enumerate(visible_views, start=1):
            y = top_y + (idx - 1) * 62
            label = html.escape(row["view"][:40] + ("..." if len(row["view"]) > 40 else ""))
            space = html.escape(row["space"])
            parts.extend([
                f'<rect x="{right_x}" y="{y}" width="{node_w}" height="{node_h}" rx="8" class="ln-node security-view-node"/>',
                f'<text class="ln-num" x="{right_x+14}" y="{y+22}">{idx+2:02d}</text>',
                f'<text class="ln-node-text" x="{right_x+38}" y="{y+22}">{label}</text>',
                f'<text class="ln-node-subtext" x="{right_x+38}" y="{y+39}">{space}</text>',
                f'<path class="ln-edge ln-edge--assoc" d="M{mid_x+node_w},{dac_y+node_h/2:.0f} C{mid_x+node_w+72},{dac_y+node_h/2:.0f} {right_x-72},{y+node_h/2:.0f} {right_x},{y+node_h/2:.0f}" marker-end="url(#sec-arrow)"/>',
            ])
        if len(views) > len(visible_views):
            parts.append(f'<text x="{right_x}" y="{top_y + len(visible_views) * 62 + 18}" class="ln-node-subtext">+ {len(views) - len(visible_views)} vistas adicionales en el detalle</text>')
    elif show_lineage:
        parts.append(f'<text x="{right_x}" y="{dac_y+28}" class="ln-node-subtext">Sin vistas consumidoras informadas</text>')
    parts.append('</svg></div>')
    return ''.join(parts)


def _catalog_roles_svg(entry, sections) -> str:
    """SVG de COBERTURA de roles (Seguridad de Roles). Se arma DESDE las secciones
    renderizadas (el CONTENIDO dicta el SVG): un nodo por sección de rol, con el MISMO
    número [NN] y la MISMA ancla que la sección — nunca muestra un rol que no esté en el
    contenido. Bandas = capas de Datasphere (según la función del rol), columnas =
    entornos DEV/QA/PRD. Cada rol abarca los entornos de su alcance; los globales abarcan
    la ENVOLVENTE de su capa (unión de scopes de los de alcance) → Modelado/Integración
    no llegan a Producción. Capa/scope salen del frontmatter `roles` (solo lookup)."""
    meta = {str(r.get("id")): r for r in (getattr(entry, "roles", []) or []) if r.get("id")}
    if not meta:
        return ""
    ENVS = ["DEV", "QA", "PRD"]
    LAYERS = ["Integración", "Modelado", "Consumo", "Gobierno y Administración"]
    CLS = {"Integración": "int", "Modelado": "mod", "Consumo": "con", "Gobierno y Administración": "gov"}
    # Nodos = secciones de rol realmente renderizadas (título "role_id · Matriz…").
    nodes = []
    for s in sections:
        rid = str(getattr(s, "title", "")).split("·")[0].strip()
        m = meta.get(rid)
        if not m:
            continue
        nodes.append({**m, "num": getattr(s, "number", 0), "slug": getattr(s, "slug", "")})
    if not nodes:
        return ""
    by_layer = {l: [] for l in LAYERS}
    for n in nodes:
        lyr = n.get("layer") if n.get("layer") in by_layer else "Gobierno y Administración"
        by_layer[lyr].append(n)

    def _sc(n):
        return {e for e in ENVS if e in (n.get("scopes") or [])}
    layer_envs = {}
    for l in LAYERS:
        acc = set()
        for n in by_layer[l]:
            if n.get("category") != "global":
                acc |= _sc(n)
        layer_envs[l] = acc or set(ENVS)

    COLW, X0 = 208, 210
    W = X0 + len(ENVS) * COLW + 14
    RH, RGAP, BANDPAD, HEADER = 34, 10, 40, 52
    col_x = lambda i: X0 + i * COLW
    bands, content = [], []
    y = HEADER
    for l in LAYERS:
        rs = by_layer[l]
        if not rs:
            continue
        c = CLS[l]
        bh = BANDPAD + len(rs) * (RH + RGAP)
        bands.append(f'<rect x="8" y="{y}" width="{W-16}" height="{bh-10}" rx="10" class="rl-band rl-band--{c}"/>')
        content.append(f'<text x="18" y="{y+23}" class="rl-lyr rl-lyr--{c}">{html.escape(l)}</text>')
        ry = y + BANDPAD - 4
        for n in rs:
            glob = n.get("category") == "global"
            sc = layer_envs[l] if glob else _sc(n)
            idxs = sorted(ENVS.index(e) for e in sc) or [0, 1, 2]
            xa = col_x(idxs[0]) + 6
            xb = col_x(idxs[-1]) + COLW - 12
            rid = html.escape(str(n.get("id") or ""))
            badge = "GLOBAL" if glob else " · ".join(e for e in ENVS if e in sc)
            slug = html.escape(str(n.get("slug") or ""))
            num = int(n.get("num") or 0)
            cy = ry + RH / 2 + 4
            badge_cls = "ln-node-subtext rl-badge-global" if glob else "ln-node-subtext"
            open_a = f'<a href="#{slug}" class="ln-node-a rl-role">' if slug else '<g class="rl-role">'
            close_a = '</a>' if slug else '</g>'
            content.append(
                f'{open_a}'
                f'<rect class="ln-node" x="{xa}" y="{ry}" width="{max(80, xb-xa)}" height="{RH}" rx="8"/>'
                f'<text class="ln-num" x="{xa+12}" y="{cy:.0f}">{num:02d}</text>'
                f'<text class="ln-node-text" x="{xa+32}" y="{cy:.0f}">{rid}</text>'
                f'<text class="{badge_cls}" x="{xb-12}" y="{cy:.0f}" text-anchor="end">{html.escape(badge)}</text>'
                f'{close_a}')
            ry += RH + RGAP
        y += bh
    H = y + 12
    heads = ['<text x="18" y="34" class="rl-collabel">CAPA · ENTORNO</text>']
    guides = []
    for i, e in enumerate(ENVS):
        cx, cw = col_x(i), COLW - 12
        heads.append(f'<rect x="{cx+6}" y="15" width="{cw}" height="26" rx="6" class="rl-envhead"/>')
        heads.append(f'<text x="{cx+6+cw/2:.0f}" y="33" class="rl-env" text-anchor="middle">{e}</text>')
        guides.append(f'<line x1="{cx+6}" y1="47" x2="{cx+6}" y2="{H-8}" class="rl-guide"/>')
    guides.append(f'<line x1="{col_x(len(ENVS)-1)+COLW-6}" y1="47" x2="{col_x(len(ENVS)-1)+COLW-6}" y2="{H-8}" class="rl-guide"/>')
    svg = (f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="roles-svg lineage-svg" '
           f'role="img" aria-label="Cobertura de roles por capa y entorno">'
           + "".join(bands) + "".join(guides) + "".join(heads) + "".join(content) + "</svg>")
    return '<div class="catalog-lineage__scroll">' + svg + '</div>'


@app.get("/catalog/security", response_class=HTMLResponse, include_in_schema=False)
def catalog_security(request: Request, family: str = "", pack: str = Query(DEFAULT_PACK)):
    # Catálogo operativo de Seguridad: familias funcionales (roles / datos).
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    mod, entries = _catalog_security_entries()
    hits = mod.security_for_family(entries, family) if (mod and family) else []
    family_label = ""
    if mod and family:
        family_label = next((f["label"] for f in mod.security_families(entries) if f["value"] == family), family)
    return templates.TemplateResponse(
        request=request,
        name="partials/catalog_security.html",
        context={"items": hits, "family": family, "family_label": family_label, "pack": _pack(pack)},
    )


@app.get("/catalog/security/detail", response_class=HTMLResponse, include_in_schema=False)
def catalog_security_detail(
    request: Request,
    cliente: str = "",
    criterio: str = "",
    dac: str = "",
    pack: str = Query(DEFAULT_PACK),
    lineage: int = 0,
):
    user = auth_service.session_user(request)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_security_entries()
    entry = mod.find_security_entry(entries, cliente, criterio, dac) if mod else None
    if entry is None:
        raise HTTPException(404, "Detalle de seguridad no encontrado en el catálogo")
    from webapp.services.content_service import render_sections
    is_roles = getattr(entry, "family", "") == "roles"
    show_lineage = bool(lineage) and not is_roles
    sections = render_sections(entry.body, pack_name=selected, stem=f"catalog_security_{entry.cliente}_{entry.dac}")
    # Roles → SVG de cobertura armado DESDE las secciones (el contenido dicta el SVG);
    # Datos/DAC → linaje consumidor.
    lineage_svg = _catalog_roles_svg(entry, sections) if is_roles else _catalog_security_lineage_svg(entry, show_lineage)
    return templates.TemplateResponse(
        request=request,
        name="partials/catalog_security_content.html",
        context={
            "pack": selected,
            "entry": entry,
            "sections": sections,
            "lineage_svg": lineage_svg,
            "show_lineage": show_lineage,
            "catalog_kind": "security",
            "catalog_ref": f"{entry.cliente} · {entry.criterio}",
        },
    )


@app.get("/catalog/domain", response_class=HTMLResponse, include_in_schema=False)
def catalog_domain(request: Request, dominio: str = "", modulo: str = "", pack: str = Query(DEFAULT_PACK)):
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="domain", ref=(dominio or modulo))
    # Determinístico y sin tokens → disponible para cualquier usuario logueado
    # (incluido el perfil de solo lectura), como Árbol/Diagnóstico.
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    mod, entries = _catalog_entries()
    flows = mod.flows_for_domain(entries, dominio) if (mod and dominio) else []
    # Énfasis por módulo (mismo catálogo global; solo cambia qué se resalta):
    # 03 Integración → fuentes de extracción; 04 Modelado → capas.
    foco = {"03": "fuentes"}.get((modulo or "").strip())
    return templates.TemplateResponse(
        request=request, name="partials/catalog_flows.html",
        context={"flows": flows, "dominio": dominio, "foco": foco, "pack": _pack(pack),
                 "grades": assessment_presenter.tile_grades(flows),
                 "logical_counts": {f.entry_id: _catalog_logical_artifact_count(f) for f in flows}},   # cantidad LÓGICA (mismo conjunto normalizado del linaje)   # grado PRIMARIO de la tile = Adopción metodológica persistida
    )


_LOB_NOTES = {
    "NO_ANALYTIC_MODEL": "Sin modelo analítico terminal: no hay raíz que clasificar.",
    "NOT_CLASSIFIED": "Aún sin clasificación de línea de negocio.",
    "UNDETERMINED": "Evidencia insuficiente para determinar la línea de negocio.",
}


def _lob_note(eff: dict) -> str | None:
    """Texto discreto de la tarjeta en la vista por línea de negocio (SOLO presentación de lo persistido/efectivo; no decide nada)."""
    if not eff["available"]:
        return "Clasificación no disponible (error de lectura)."
    if eff["source"] == "MANUAL":
        extra = f" · la clasificación automática sugiere {eff['automatic_label']}" if eff.get("automatic_label") else ""
        return f"Asignación manual{extra}"
    if eff["source"] == "UNCLASSIFIED":
        sug = eff.get("suggestion")
        if sug:
            conf = {"MEDIUM": "media", "HIGH": "alta"}.get(sug.get("confidence") or "", "media")
            return f"Sugerencia: {sug['label']} (confianza {conf}) · requiere confirmación; no agrupa."
        return _LOB_NOTES.get(eff.get("reason") or "")
    return None


_LOB_EMPTY = {
    "UNCLASSIFIED": "No hay modelos pendientes de clasificar.",
    "UNAVAILABLE": "No hay modelos con la clasificación no disponible.",
}


@app.get("/catalog/lob", response_class=HTMLResponse, include_in_schema=False)
def catalog_lob(request: Request, lob: str = "", pack: str = Query(DEFAULT_PACK)):
    """Flujos de una LÍNEA DE NEGOCIO (tercera modalidad del Catálogo, junto a Dominios y Data Products). Solo LEE la clasificación v2 PERSISTIDA
    (``domain_classification_service.effective_lob_by_entry``): precedencia manual > automática HIGH > sin clasificar; la MEDIUM no agrupa. Jamás deriva,
    clasifica ni escribe. Reusa las tarjetas de ``catalog_flows.html`` y su navegación al detalle."""
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="lob", ref=lob)
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    wanted = (lob or "").strip().upper()
    valid = {o["id"] for o in dcs.lob_options()} | {dcs.UNAVAILABLE}
    if wanted and wanted not in valid:
        raise HTTPException(404, "Línea de negocio desconocida")
    selected = _pack(pack)
    ctx = {"flows": [], "dominio": "", "foco": None, "pack": selected, "grades": {}, "logical_counts": {}, "lob_info": {},
           "empty_kind": "prompt", "empty_title": "Elegí una línea de negocio.", "empty_message": None}
    if not wanted:
        return templates.TemplateResponse(request=request, name="partials/catalog_flows.html", context=ctx)
    mod, entries = _catalog_entries()
    universe = catalog_navigation.browsable_flows(mod, entries) if mod else []
    try:
        statuses = assessment_service.statuses_for_entries(universe)            # UNA lectura por lote; la reusan la clasificación y los grados de las tarjetas
        eff = dcs.effective_lob_by_entry(universe, statuses)
    except Exception:                                                           # noqa: BLE001 — error real de lectura: se informa, jamás una clasificación ficticia
        logging.getLogger("danwor.catalog").exception("línea de negocio: no se pudo leer la clasificación persistida")
        ctx.update({"empty_kind": "error", "empty_title": "Clasificación no disponible",
                    "empty_message": "No se pudo leer la clasificación de línea de negocio. No significa que no haya modelos: reintentá en unos segundos."})
        return templates.TemplateResponse(request=request, name="partials/catalog_flows.html", context=ctx)
    flows = catalog_navigation.sort_flows_by_verdict(dcs.partition_by_lob(universe, eff)[wanted])
    n_unavailable = sum(1 for v in eff.values() if not v["available"])
    ctx.update({"flows": flows, "grades": assessment_presenter.tile_grades(flows, statuses),
                "logical_counts": {f.entry_id: _catalog_logical_artifact_count(f) for f in flows},
                "lob_info": {f.entry_id: {"note": _lob_note(eff[f.entry_id])} for f in flows},
                "empty_kind": "empty", "empty_title": "Sin modelos disponibles", "empty_message": _LOB_EMPTY.get(wanted, "Todavía no hay modelos asociados a esta línea de negocio."),
                "lob_unavailable": n_unavailable if wanted != dcs.UNAVAILABLE else 0})
    return templates.TemplateResponse(request=request, name="partials/catalog_flows.html", context=ctx)


@app.get("/catalog/client", response_class=HTMLResponse, include_in_schema=False)
def catalog_client(request: Request, cliente: str = "", pack: str = Query(DEFAULT_PACK)):
    """Todos los modelos (flujos) de un CLIENTE: cuarta modalidad del Catálogo. Mismas tarjetas y navegación que Modelos; solo filtra por ``entry.cliente``
    (coincidencia exacta sin distinguir mayúsculas), sin reglas por cliente. Solo lectura."""
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="client", ref=cliente)
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    selected = _pack(pack)
    mod, entries = _catalog_entries()
    name = (cliente or "").strip()
    flows = catalog_navigation.flows_for_client(mod, entries, name) if (mod and name) else []
    return templates.TemplateResponse(
        request=request, name="partials/catalog_flows.html",
        context={"flows": flows, "dominio": "", "foco": None, "pack": selected,
                 "empty_kind": "empty" if name else "prompt",
                 "empty_title": "Sin modelos disponibles" if name else "Elegí un cliente.",
                 "empty_message": f"Todavía no hay modelos asociados al cliente «{name}»." if name else None,
                 "grades": assessment_presenter.tile_grades(flows),
                 "logical_counts": {f.entry_id: _catalog_logical_artifact_count(f) for f in flows}})


_CATALOG_LAYERS = ["Ingesta", "Armonización", "Propagación", "Consumo"]


def _catalog_layer_index(auto: dict, name: str) -> int:
    """Capa (0-3) de un artefacto para el diagrama de linaje. Confía en `auto.capa`
    (que el generador ya computa de forma robusta); si no fuese una capa canónica,
    espeja la misma prioridad: prefijo 1-4 → esa capa; remoto → Ingesta; patrón de
    modelado (dimensión→Armonización, hecho→Propagación, cubo→Consumo); AM/EM→Consumo."""
    capa = auto.get("capa")
    if capa in _CATALOG_LAYERS:
        return _CATALOG_LAYERS.index(capa)
    if name[:1] in "1234":
        return int(name[0]) - 1
    if auto.get("origen_remoto"):
        return 0
    sem = f"{auto.get('data_category') or ''} {auto.get('modeling_pattern') or ''}".upper()
    if "CUBE" in sem:
        return 3
    if "FACT" in sem:
        return 2
    if "DIMENSION" in sem:
        return 1
    pref = name.split("_", 1)[0]
    if pref in ("AM", "EM"):
        return 3
    if pref[:1] == "V" or pref in ("VF", "VD", "VR", "VS"):
        return 2
    return 0   # no clasificable → Ingesta (NO Consumo: evita falsas anclas de sistema)


def _catalog_flow_keep(nodes: list, show_assoc: bool) -> set:
    """Conjunto de artefactos VISIBLES del flujo = ancestros DIRIGIDOS del OUTPUT
    (nodos de la capa más profunda CON aristas directas). Padres directos siempre; +
    asociación si `show_assoc`. Excluye ramas laterales (comparten fuente pero no
    alimentan la salida) y artefactos de sistema aislados (SAP.TIME, DAC, …). Lo usan
    tanto el diagrama como el filtro de secciones, para que ambos coincidan."""
    from collections import defaultdict
    names = {n["name"] for n in nodes}
    adj = defaultdict(set)
    dparent, aparent = defaultdict(set), defaultdict(set)
    for n in nodes:
        for up in n["upstreams"]:
            if up in names:
                adj[up].add(n["name"]); adj[n["name"]].add(up); dparent[n["name"]].add(up)
        for up in n["assoc_upstreams"]:
            if up in names:
                aparent[n["name"]].add(up)
    connected = {n["name"] for n in nodes if adj[n["name"]]}
    if not connected:
        return names
    maxlayer = max(n["layer"] for n in nodes if n["name"] in connected)
    anchors = {n["name"] for n in nodes if n["layer"] == maxlayer and n["name"] in connected}
    keep, stack = set(anchors), list(anchors)
    while stack:
        t = stack.pop()
        for up in dparent[t] | (aparent[t] if show_assoc else set()):
            if up not in keep:
                keep.add(up)
                stack.append(up)
    # Defensivo (linaje completeness): el OUTPUT declarado del flow —el/los nodo(s)
    # de la capa más profunda, típicamente el modelo de consumo (cube)— debe estar
    # SIEMPRE visible, aun si su metadata no trae upstream DIRECTO (el ancla se elige
    # entre los nodos CON aristas directas, así que un output cuyo único vínculo es
    # asociación quedaba fuera de su propio diagrama de "linaje del output"). No se
    # inventan aristas: si no conecta, se dibuja aislado; su asociación real aparece
    # solo con el toggle "Incluir asociaciones".
    out_layer = max(n["layer"] for n in nodes)
    keep |= {n["name"] for n in nodes if n["layer"] == out_layer}
    return keep


def _catalog_should_default_associations(nodes: list) -> bool:
    """Fallback contextual: ¿conviene mostrar asociaciones POR DEFECTO en este flow?

    True solo cuando el OUTPUT declarado (nodo/s de la capa más profunda) NO tiene
    dependencia DIRECTA hacia el resto del flow pero SÍ una asociación que lo vincula
    con algún nodo del flow. En ese caso el linaje directo dejaría el output suelto y
    las asociaciones dan contexto al modelo de consumo. NO cambia el default global:
    un flow con output conectado directo (o sin ninguna asociación) sigue con
    asociaciones OFF por defecto. No se infiere por nombres — solo por el modelo."""
    if not nodes:
        return False
    names = {n["name"] for n in nodes}
    out_layer = max(n["layer"] for n in nodes)
    for out in (n for n in nodes if n["layer"] == out_layer):
        direct = [u for u in out["upstreams"] if u in names]
        assoc = [u for u in out["assoc_upstreams"] if u in names]
        if not direct and assoc:
            return True
    return False


def _wrap_label(label: str, max_chars: int = 20) -> list[str]:
    """Parte un nombre técnico en ≤2 líneas para que el nodo no fuerce ancho excesivo
    (overflow horizontal). Preserva el nombre COMPLETO (no trunca): corta en el '_'
    más cercano al medio; sin '_', corta al medio."""
    label = str(label)
    if len(label) <= max_chars:
        return [label]
    mid = len(label) // 2
    unders = [i for i, c in enumerate(label) if c == "_"]
    if unders:
        cut = min(unders, key=lambda i: abs(i - mid))
        return [label[:cut + 1], label[cut + 1:]]
    return [label[:mid], label[mid:]]


def _lineage_crossings(order: dict, edges) -> int:
    """Cruces de aristas de un orden vertical: cada nodo en (columna, fila) y cada arista como el segmento entre sus extremos; cuenta los pares que se cortan de
    verdad (las aristas que comparten un nodo no cuentan). Solo mide: no modifica nada."""
    where = {n: (c, r) for c, names in order.items() for r, n in enumerate(names)}
    segs = [(u, v, where[u], where[v]) for u, v in sorted(set(edges)) if u != v and u in where and v in where and where[u][0] != where[v][0]]

    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    total = 0
    for i in range(len(segs)):
        u1, v1, a1, b1 = segs[i]
        for j in range(i + 1, len(segs)):
            u2, v2, a2, b2 = segs[j]
            if {u1, v1} & {u2, v2}:
                continue
            if orient(a1, b1, a2) * orient(a1, b1, b2) < 0 and orient(a2, b2, a1) * orient(a2, b2, b1) < 0:
                total += 1
    return total


def _lineage_row_order(by_col: dict, cols: list, edges, sweeps: int = 8) -> dict:
    """Orden VERTICAL de los nodos dentro de cada columna del diagrama de linaje (las columnas, los nodos, las aristas y la numeración NO cambian). Criterio conservador:
    baricentro de los vecinos de las otras columnas (alinea cada nodo con sus predecesores/sucesores), barridos alternados y DETERMINISTAS (empates = orden actual); el
    resultado reemplaza al orden actual SOLO si reduce estrictamente los cruces. Si no hay mejora demostrable, devuelve el orden de entrada tal cual."""
    from collections import defaultdict
    cur = {d: [n["name"] for n in by_col[d]] for d in cols}
    best_x = _lineage_crossings(cur, edges)
    if best_x == 0 or len(cols) < 2:
        return by_col
    nbrs = defaultdict(set)
    for u, v in edges:
        if u != v:
            nbrs[u].add(v)
            nbrs[v].add(u)
    best = {d: list(v) for d, v in cur.items()}
    for k in range(sweeps):
        for d in (cols if k % 2 == 0 else list(reversed(cols))):
            rows = {n: r for names in cur.values() for r, n in enumerate(names)}
            own = set(cur[d])

            def key(n, rows=rows, own=own):
                ys = [rows[m] for m in nbrs.get(n, ()) if m in rows and m not in own]
                return ((sum(ys) / len(ys)) if ys else rows[n], rows[n])
            cur[d] = sorted(cur[d], key=key)
        x = _lineage_crossings(cur, edges)
        if x < best_x:
            best_x, best = x, {d: list(v) for d, v in cur.items()}
            if x == 0:
                break
    if best == {d: [n["name"] for n in by_col[d]] for d in cols}:
        return by_col
    by_name = {n["name"]: n for d in cols for n in by_col[d]}
    return {d: [by_name[name] for name in best[d]] for d in cols}


def _catalog_node_icon(auto: dict, name: str, view_types: dict) -> dict:
    """Clave de ícono del nodo del linaje según la MISMA fuente de verdad de tipos que el pipeline (`artifact_icons.artifact_type`): `artefacto_tecnico` del Catálogo y, para las vistas,
    su `viewType` real (misma función que «Abrir SQL»). Sin metadata suficiente → {} y el nodo sale EXACTAMENTE como siempre (solo número)."""
    key = artifact_icons.artifact_type(artefacto_tecnico=str(auto.get("artefacto_tecnico") or "") or None, view_type=view_types.get(name))
    return {"icon": key} if key else {}


def _catalog_lineage_svg(nodes: list, show_assoc: bool, keep: set, focus=None, *, edge_kind: dict | None = None, aria: str | None = None,
                         arrow_at: str = "end", edge_title: dict | None = None) -> str:
    """Diagrama SVG del linaje del OUTPUT del flujo (solo los nodos en `keep`).
    Aristas DIRECTAS (FROM/JOIN) siempre; por ASOCIACIÓN (dimensiones/textos) solo con
    `show_assoc` (punteadas). Cada nodo es un ancla a su sección; el `num` del nodo ya
    viene alineado con el nº de sección (posición en el orden visible). `nodes`:
    [{name,label,title,layer,upstreams,assoc_upstreams,slug,num,is_factcube}].
    `focus` (opcional): nombres técnicos a RESALTAR (clase `ln-node-focus` en el ancla;
    p. ej. desde el informe de assessment). Solo estilo: no altera nodos, aristas ni layout.

    Extensión OPCIONAL para diagramas de pipeline (p. ej. «Orquestación de ingesta»; sin estas claves la salida es BYTE A BYTE la de siempre): un nodo con `icon` (tipo del registro
    `presentation.artifact_icons`) dibuja el ícono en lugar del número; con `role` agrega una línea de rol bajo el nombre COMPLETO (envuelto en ≤2 líneas, nunca truncado); sin `slug`
    no es un ancla (no hay ruta a la que enlazar); `evidence="unverified"` marca el nodo como no verificado. `edge_kind` {(origen, destino): "orch"|"proj"|"unverified"} agrega la
    clase `ln-edge--<kind>` a esa arista; `aria` reemplaza la etiqueta accesible del SVG; `arrow_at="start"` pone la punta en el extremo IZQUIERDO (el mecanismo apunta al artefacto
    que alimenta); `edge_title` {(origen, destino): texto} agrega un `<title>` (tooltip accesible) a la arista. Un nodo numerado con `icon` suma el ícono de su tipo junto al número."""
    from collections import defaultdict
    focus_names = frozenset(focus or ())
    names = {n["name"] for n in nodes if n["name"] in keep}
    direct_edges = [(up, n["name"]) for n in nodes if n["name"] in keep
                    for up in n["upstreams"] if up in names]
    assoc_edges = [(up, n["name"]) for n in nodes if n["name"] in keep
                   for up in n["assoc_upstreams"] if up in names]
    shown = [n for n in nodes if n["name"] in keep]
    if len(shown) < 2:
        return ""

    # Layout por DEPENDENCIA, criterio ALAP ("as late as possible", pegado al
    # consumo) como Datasphere: la columna se define por la distancia al SINK
    # (consumo final), NO por la distancia a la fuente. Así las fuentes DIRECTAS de
    # un nodo caen en la vertical inmediatamente anterior a él (p. ej. las fuentes de
    # 2VR juntas justo antes de 2VR). La capa de responsabilidad se muestra en el
    # detalle de cada artefacto, no condiciona la posición.
    shown_names = {n["name"] for n in shown}
    edges_layout = direct_edges + (assoc_edges if show_assoc else [])
    down_of = defaultdict(set)   # nodo → sus consumidores (distancia al sink)
    for up, tgt in edges_layout:
        if up in shown_names and tgt in shown_names:
            down_of[up].add(tgt)
    tosink_cache = {}

    def _to_sink(n, seen):
        if n in tosink_cache:
            return tosink_cache[n]
        downs = [d for d in down_of.get(n, ()) if d not in seen]
        r = 0 if not downs else 1 + max(_to_sink(d, seen | {n}) for d in downs)
        tosink_cache[n] = r
        return r

    for n in shown:
        _to_sink(n["name"], frozenset())
    maxlevel = max(tosink_cache.values(), default=0)
    by_col = defaultdict(list)
    for n in shown:
        by_col[maxlevel - tosink_cache[n["name"]]].append(n)   # columna = distancia al sink invertida
    cols = sorted(by_col)
    # H mayor en flujos normales para alojar labels de ≤2 líneas (nombres largos se
    # envuelven en vez de forzar ancho → evita overflow horizontal). COLGAP más chico
    # comprime el grafo. Los diagramas de Data Product (con subtext) conservan su H.
    H, VGAP, COLGAP, HEAD = (58 if any(n.get("role") for n in shown) else 46 if any(n.get("subtext") for n in shown) else 44), 16, 30, 6
    # Ancho POR CONTENIDO: cada caja se ajusta a su texto (no ancho fijo), para no
    # desperdiciar espacio; el ancho de columna = el del nodo más ancho de la columna.
    NUM_W, CHAR, PAD_R, WMIN, WMAX = 30, 6.3, 14, 92, 260
    ICON_X = 18        # desplazamiento extra del texto cuando el nodo NUMERADO lleva ícono (sin ícono: 0, salida idéntica a la histórica)

    def _node_dims(n):
        role = str(n.get("role") or "")
        if role:
            lines = _wrap_label(n["label"])
            return max(WMIN, min(WMAX, int(max(NUM_W + max(len(ln) for ln in lines) * CHAR + PAD_R, NUM_W + len(role) * 5.4 + PAD_R)))), lines
        sub = str(n.get("subtext") or "")
        if sub:
            # Data Product: comportamiento actual (1 línea + subtext); el ancho toma el
            # MAYOR de nombre y subtexto (SUB_CHAR levemente sobreestimado).
            disp = (n["label"][:33] + "…") if len(n["label"]) > 34 else n["label"]
            ix = ICON_X if n.get("icon") else 0                  # sin ícono: 0 (ancho histórico idéntico)
            w = max(WMIN, min(WMAX, int(max(NUM_W + ix + len(disp) * CHAR + PAD_R,
                                            32 + ix + len(sub) * 5.2 + PAD_R))))
            return w, [disp]
        # Flujo normal: nombre COMPLETO envuelto en ≤2 líneas; ancho = la línea más larga.
        lines = _wrap_label(n["label"])
        w = max(WMIN, min(WMAX, int(NUM_W + (ICON_X if n.get("icon") else 0) + max(len(ln) for ln in lines) * CHAR + PAD_R)))
        return w, lines

    node_w, node_disp, col_width = {}, {}, {}
    for d in cols:
        wmax = 0
        for n in by_col[d]:
            w, lines = _node_dims(n)
            node_w[n["name"]], node_disp[n["name"]] = w, lines
            wmax = max(wmax, w)
        col_width[d] = wmax
    col_x, acc = {}, 0
    for d in cols:
        col_x[d] = acc
        acc += col_width[d] + COLGAP
    pos, max_rows = {}, 0
    is_data_product_lineage = any(str(n.get("runtime") or "") == "object_store" for n in shown) and any(str(n.get("subtext") or "") == "Data Product" for n in shown)
    _band_gap_y = None   # y del gap entre bandas HANA/HDLF (para la leyenda "vía TF")
    if is_data_product_lineage:
        hana_cols = [d for d in cols if any(str(n.get("runtime") or "") == "hana" for n in by_col[d])]
        object_cols = [d for d in cols if any(str(n.get("runtime") or "") != "hana" for n in by_col[d])]

        def _band_x(band_cols: list[int]) -> dict[int, int]:
            out: dict[int, int] = {}
            band_acc = 0
            for d in band_cols:
                out[d] = band_acc
                band_acc += col_width[d] + COLGAP
            return out

        hana_x = _band_x(hana_cols)
        object_x = _band_x(object_cols)
        band_pad_x = 16
        band_label_h = 26
        hana_rows = 0
        object_rows = 0
        for d in hana_cols:
            hana_idx = 0
            for n in by_col[d]:
                if str(n.get("runtime") or "") == "hana":
                    pos[n["name"]] = (band_pad_x + hana_x[d], band_label_h + hana_idx * (H + VGAP))
                    hana_idx += 1
            hana_rows = max(hana_rows, hana_idx)

        hana_height = band_label_h + max(1, hana_rows) * (H + VGAP) - VGAP + 18
        object_top = hana_height + 22
        _band_gap_y = hana_height + (object_top - hana_height) / 2 + 3   # centro del gap
        for d in object_cols:
            object_idx = 0
            for n in by_col[d]:
                if str(n.get("runtime") or "") != "hana":
                    pos[n["name"]] = (band_pad_x + object_x[d], object_top + band_label_h + object_idx * (H + VGAP))
                    object_idx += 1
            object_rows = max(object_rows, object_idx)

        hana_width = max((hana_x[d] + col_width[d] for d in hana_cols), default=0)
        object_width = max((object_x[d] + col_width[d] for d in object_cols), default=0)
        max_rows = max(hana_rows, object_rows)
        width = max(hana_width, object_width) + band_pad_x * 2
        height = object_top + band_label_h + max(1, object_rows) * (H + VGAP) - VGAP + 18
    else:
        rows_by_col = _lineage_row_order(by_col, cols, [(u, t) for u, t in edges_layout if u in shown_names and t in shown_names])   # solo el orden vertical, alineado con vecinos
        for d in cols:
            for r, n in enumerate(rows_by_col[d]):
                pos[n["name"]] = (col_x[d], HEAD + r * (H + VGAP))
            max_rows = max(max_rows, len(by_col[d]))
        width = (acc - COLGAP) if cols else 0
        height = HEAD + max_rows * (H + VGAP) - VGAP + 4
    # La punta al inicio lleva un id ÚNICO por diagrama: varios SVG conviven en la página y `url(#id)` resuelve al PRIMERO del documento; si ese vive en un <details> cerrado (display:none) no se dibuja.
    arrow_start_id = f"ln-arrow-start-{zlib.crc32('|'.join(n['name'] for n in nodes).encode()):x}" if arrow_at == "start" else "ln-arrow-start"
    p = [f'<svg class="lineage-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
         f'role="img" aria-label="{html.escape(aria or "Diagrama de trazabilidad del flujo", quote=True)}">',
         '<defs><marker id="ln-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
         '<path d="M0,0 L8,4 L0,8 z" class="ln-arrowhead"/></marker>'
         + (f'<marker id="{arrow_start_id}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse">'
            '<path d="M0,0 L8,4 L0,8 z" class="ln-arrowhead"/></marker>' if arrow_at == "start" else '') + '</defs>']

    runtime_labels = {"hana": "HANA Space", "object_store": "HDLF Object Store", "data_product": "Data Product"}
    runtime_groups = defaultdict(list)
    for n in shown:
        runtime = str(n.get("runtime") or "").strip()
        if runtime:
            runtime_groups[runtime].append(n)
    for runtime, items in runtime_groups.items():
        xs = [pos[n["name"]][0] for n in items if n["name"] in pos]
        ys = [pos[n["name"]][1] for n in items if n["name"] in pos]
        if not xs or not ys:
            continue
        x0 = max(0, min(xs) - 12)
        y0 = max(0, min(ys) - 18)
        x1 = max(pos[n["name"]][0] + node_w[n["name"]] for n in items if n["name"] in pos) + 12
        y1 = max(pos[n["name"]][1] + H for n in items if n["name"] in pos) + 12
        p.append(f'<rect class="ln-runtime-box ln-runtime-box--{html.escape(runtime)}" x="{x0:.0f}" y="{y0:.0f}" width="{x1-x0:.0f}" height="{y1-y0:.0f}" rx="10"/>')
        p.append(f'<text class="ln-runtime-label" x="{x0+10:.0f}" y="{y0+12:.0f}">{html.escape(runtime_labels.get(runtime, runtime))}</text>')

    runtime_by_name = {n["name"]: str(n.get("runtime") or "") for n in shown}
    lineage_key_by_name = {n["name"]: slugify(str(n.get("label") or n.get("title") or n.get("name") or "")) for n in shown}
    lineage_key_counts = defaultdict(int)
    for _key in lineage_key_by_name.values():
        if _key:
            lineage_key_counts[_key] += 1
    duplicate_lineage_keys = {k for k, v in lineage_key_counts.items() if v > 1}
    if duplicate_lineage_keys:
        p.append("<style>" + "".join(
            f'.lineage-svg:has(.ln-k-{html.escape(k)}:hover) .ln-k-{html.escape(k)} .ln-node{{stroke:var(--gold);stroke-width:1.8;}}'
            for k in sorted(duplicate_lineage_keys)
        ) + "</style>")

    _tf_label_x: list = []   # posiciones ya ocupadas por la leyenda "vía Transformation Flow"

    def _edge(up, tgt, cls):
        if up not in pos or tgt not in pos:
            return
        edge_cls = cls + (f" ln-edge--{html.escape(str((edge_kind or {}).get((up, tgt)) or ''))}" if (edge_kind or {}).get((up, tgt)) else "")
        cross_band = (is_data_product_lineage
                      and runtime_by_name.get(up) == "hana"
                      and runtime_by_name.get(tgt) == "object_store"
                      and "ln-edge--assoc" not in cls)
        if cross_band:
            edge_cls = cls + " ln-edge--hana"
        x1, y1 = pos[up]
        x2, y2 = pos[tgt]
        if cross_band:
            # Salto ENTRE bandas (HANA arriba → HDLF abajo): sale por el borde INFERIOR
            # del nodo superior y entra por el borde SUPERIOR del inferior (vertical),
            # en vez de lateral der→izq. La curva se controla en el eje Y y el marker
            # (orient="auto") rota la punta hacia abajo solo.
            sx, sy = x1 + node_w[up] / 2, y1 + H
            tx, ty = x2 + node_w[tgt] / 2, y2
            dy = max(18, abs(ty - sy) / 2)
            # Punteada cross-band = publicación HANA→HDLF vía Transformation Flow. Tooltip
            # on-hover (robusto) + leyenda inline sutil en el gap (dedupe de posición).
            p.append(f'<path class="{edge_cls}" d="M{sx:.0f},{sy:.0f} C{sx:.0f},{sy+dy:.0f} '
                     f'{tx:.0f},{ty-dy:.0f} {tx:.0f},{ty:.0f}" marker-end="url(#ln-arrow)">'
                     f'<title>vía Transformation Flow</title></path>')
            my = _band_gap_y if _band_gap_y is not None else (sy + ty) / 2   # en el gap
            # X = donde el edge CRUZA el gap (sobre la curva bezier), NO el midpoint: dos
            # punteadas que en el gap están separadas reciben cada una su leyenda.
            mx = (sx + tx) / 2
            if _band_gap_y is not None:
                _bt, _bd = 0.5, 1e9
                for _i in range(101):
                    _t = _i / 100.0
                    _mt = 1 - _t
                    _yy = (_mt**3 * sy + 3 * _mt**2 * _t * (sy + dy)
                           + 3 * _mt * _t**2 * (ty - dy) + _t**3 * ty)
                    if abs(_yy - my) < _bd:
                        _bd, _bt = abs(_yy - my), _t
                _mt = 1 - _bt
                mx = _mt**3 * sx + 3 * _mt**2 * _bt * sx + 3 * _mt * _bt**2 * tx + _bt**3 * tx
            if all(abs(mx - px) > 130 for px in _tf_label_x):   # ancho de la leyenda (evita solape)
                _tf_label_x.append(mx)
                p.append(f'<text class="ln-edge-legend" x="{mx:.0f}" y="{my:.0f}" '
                         f'text-anchor="middle">vía Transformation Flow</text>')
        else:
            sx, sy, tx, ty = x1 + node_w[up], y1 + H / 2, x2, y2 + H / 2
            dx = max(24, abs(tx - sx) / 2)
            # Una PROYECCIÓN (`X_Delta — X`, «no es una carga») es un vínculo sin dirección de transferencia: sin punta de flecha. El resto de las aristas conserva su marcador.
            marker = ("" if (edge_kind or {}).get((up, tgt)) == "proj"
                      else f' marker-start="url(#{arrow_start_id})"' if arrow_at == "start" else ' marker-end="url(#ln-arrow)"')
            ttl = (edge_title or {}).get((up, tgt))
            p.append(f'<path class="{edge_cls}" d="M{sx:.0f},{sy:.0f} C{sx+dx:.0f},{sy:.0f} '
                     f'{tx-dx:.0f},{ty:.0f} {tx:.0f},{ty:.0f}"{marker}' + (f'><title>{html.escape(ttl)}</title></path>' if ttl else '/>'))

    for up, tgt in direct_edges:
        _edge(up, tgt, "ln-edge")
    if show_assoc:
        for up, tgt in assoc_edges:
            _edge(up, tgt, "ln-edge ln-edge--assoc")
    for n in shown:
        x, y = pos[n["name"]]
        w, lines = node_w[n["name"]], node_disp[n["name"]]
        subtext = str(n.get("subtext") or "").strip()
        lineage_key = lineage_key_by_name.get(n["name"], "")
        lineage_key_class = f" ln-k-{lineage_key}" if lineage_key in duplicate_lineage_keys else ""
        if n["name"] in focus_names:
            lineage_key_class += " ln-node-focus"
        role = str(n.get("role") or "").strip()
        if role:
            # Pipeline: ícono del tipo (en lugar del número), nombre COMPLETO en ≤2 líneas y el rol debajo. Sin truncar: el nombre técnico largo se envuelve.
            num_y = y + H / 2 + 3
            y0 = y + (H / 2 - 12 if len(lines) == 1 else H / 2 - 18)
            tsp = "".join(f'<tspan x="{x+32}" dy="{0 if i == 0 else 12}">{html.escape(ln)}</tspan>' for i, ln in enumerate(lines))
            label_svg = f'<text class="ln-node-text" x="{x+32}" y="{y0 + 12:.0f}">{tsp}</text>'
            sub_html = f'<text class="ln-node-subtext" x="{x+32}" y="{y + H - 10:.0f}">{html.escape(role)}</text>'
        elif subtext:
            tx = x + 32 + (ICON_X if n.get("icon") else 0)
            main_y = y + 18
            num_y = main_y
            label_svg = f'<text class="ln-node-text" x="{tx}" y="{main_y:.0f}">{html.escape(lines[0])}</text>'
            sub_html = f'<text class="ln-node-subtext" x="{tx}" y="{y+32:.0f}">{html.escape(subtext)}</text>'
        elif len(lines) == 1:
            tx = x + 32 + (ICON_X if n.get("icon") else 0)
            main_y = y + H / 2 + 4
            num_y = main_y
            label_svg = f'<text class="ln-node-text" x="{tx}" y="{main_y:.0f}">{html.escape(lines[0])}</text>'
            sub_html = ''
        else:
            # Label en 2 líneas (nombre completo): tspans; el número se centra vertical.
            tx = x + 32 + (ICON_X if n.get("icon") else 0)
            num_y = y + H / 2 + 3
            y0 = y + H / 2 - 5
            label_svg = (f'<text class="ln-node-text" x="{tx}" y="{y0:.0f}">'
                         f'<tspan x="{tx}" dy="0">{html.escape(lines[0])}</tspan>'
                         f'<tspan x="{tx}" dy="12">{html.escape(lines[1])}</tspan></text>')
            sub_html = ''
        icon_key = n.get("icon")
        if icon_key and role:                      # pipeline: el ícono reemplaza al número
            mark = artifact_icons.icon_group(icon_key, variant=n.get("icon_variant"), x=x + 8, y=y + (H - 18) / 2, size=18)
        elif icon_key:                             # linaje numerado: se conserva el número y se suma el ícono del tipo
            mark = (f'<text class="ln-num" x="{x+11}" y="{num_y:.0f}">{n["num"]:02d}</text>'
                    + artifact_icons.icon_group(icon_key, variant=n.get("icon_variant"), x=x + 27, y=y + (H - 15) / 2, size=15))
        else:
            mark = f'<text class="ln-num" x="{x+11}" y="{num_y:.0f}">{n["num"]:02d}</text>'
        node_cls = f'ln-node ln-layer-{n["layer"]}' + (" ln-node--unverified" if n.get("evidence") == "unverified" else "")
        inner = (f'<title>{html.escape(n["title"])}</title><rect class="{node_cls}" x="{x}" y="{y}" width="{w}" height="{H}" rx="8"/>'
                 f'{mark}{label_svg}{sub_html}')
        if n.get("slug"):
            p.append(f'<a href="#{n["slug"]}" class="ln-node-a{lineage_key_class}">{inner}</a>')
        else:
            p.append(f'<g class="ln-node-a ln-node-a--static{lineage_key_class}">{inner}</g>')
    p.append("</svg>")
    return '<div class="catalog-lineage__scroll">' + "".join(p) + "</div>"


def _catalog_apis_dir() -> Path:
    """Carpeta de código de las CDS views (extractores corporativos), compartida a
    nivel raíz del catálogo (una copia por CDS, agnóstica de cliente). El generador
    la descarga desde SharePoint (CSN/APIs) a catalog_local/APIs al regenerar."""
    from config import BASE_DIR
    root = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
    return Path(root) / "APIs"


def _cds_name_from_origen(origen: str) -> str:
    """`ABAP_CDS.ZCDS_DSP_DIM_MCH1$E` → `ZCDS_DSP_DIM_MCH1`. "" si el origen no es
    una CDS view corporativa (ABAPTABLES / BW Bridge / vacío → sin código que abrir)."""
    if not origen or not str(origen).upper().startswith("ABAP_CDS."):
        return ""
    return str(origen).split(".", 1)[1].split("$", 1)[0].strip()


def _catalog_cds_link(origen: str) -> str:
    """Nombre del documento de la CDS view SOLO si su código existe en APIs/. "" si no hay origen ABAP_CDS o no hay correspondencia CONFIABLE (así el botón no se renderiza
    y no hay link muerto ni error de consola). El nombre del origen puede ser el de la entidad (archivo con ese nombre) o el de su vista SQL (``@AbapCatalog.sqlViewName`` declarado
    por UN solo documento): ver ``catalog_cds_docs.resolve``."""
    cds = _cds_name_from_origen(origen)
    return catalog_cds_docs.resolve(_catalog_apis_dir(), cds) if cds else ""


def _catalog_source_button_html(path: str, label: str, css: str, **params) -> str:
    """Botón de apoyo que carga el CÓDIGO FUENTE de un artefacto en el slot central por HTMX (``.support-link`` dentro de `heading-supports`,
    que el pipeline de contenido sube al header de la sección). Lo comparten "Abrir CDS View" y "Abrir SQL": un solo markup/target/estilo."""
    qs = urlencode({**params, "pack": params.get("pack") or DEFAULT_PACK})
    return (f'\n\n<div class="support-links heading-supports {css}-launch">'
            f'<a class="support-link {css}-link" href="#" role="button" '
            f'hx-get="{path}?{qs}" hx-target="#catalog-detail-slot" hx-swap="innerHTML" '
            f'@click.prevent>{label} <span>&#8249;/&#8250;</span></a></div>\n\n')


def _catalog_cds_button_html(cds: str, pack: str = "", cliente: str = "", dominio: str = "", flow: str = "") -> str:
    """Bloque HTML del botón "Abrir CDS View" (carga el código del extractor en el slot
    central por HTMX). COMPARTIDO por el detalle de flujo (Dominios) y de Data Product,
    para no duplicar markup/URL/target/estilos. `cliente`/`dominio` van vacíos cuando no
    hay flujo de retorno (Data Products) → el CDS usa su "Volver" genérico. `flow` es la
    identidad estable del flujo de origen, para que "Volver" reabra ESE flujo (y no el
    primero de un cliente/dominio con varios). Va marcado `heading-supports` para que el
    pipeline de contenido lo suba al header de la sección."""
    return _catalog_source_button_html("/catalog/cds", "Abrir CDS View", "catalog-cds",
                                       name=cds, cliente=cliente, dominio=dominio, flow=flow, pack=pack)


def _catalog_sql_button_html(name: str, pack: str, cliente: str, dominio: str, flow: str) -> str:
    """Botón "Abrir SQL" de una vista SQL de Datasphere con consulta (misma familia visual y de navegación que "Abrir CDS View")."""
    return _catalog_source_button_html("/catalog/sql", "Abrir SQL", "catalog-sql",
                                       name=name, cliente=cliente, dominio=dominio, flow=flow, pack=pack)


def _catalog_dp_inject_cds_buttons(body: str, lineage_nodes: list, pack: str) -> str:
    """Inyecta "Abrir CDS View" en la sección de cada artefacto de ingesta del Data
    Product con `origen_cds` (llave del Replication Flow) cuyo código exista en APIs/.
    Espeja _catalog_flow_view (Dominios) reutilizando el mismo botón; sin flujo de
    retorno pasa cliente/dominio vacíos → el CDS usa su "Volver" genérico."""
    apis = _catalog_apis_dir()
    by_display = {}
    for n in lineage_nodes or []:
        if not isinstance(n, dict):
            continue
        cds = catalog_cds_docs.resolve(apis, str(n.get("origen_cds") or "").strip())
        disp = str(n.get("display") or "").strip()
        if cds and disp:
            by_display[disp] = cds
    if not by_display:
        return body

    def _repl(m):
        head = m.group(0)
        gold = re.search(r"\{dw-gold\}(.+)$", head)
        disp = (gold.group(1) if gold else head).strip()
        cds = by_display.get(disp)
        return head + _catalog_cds_button_html(cds, pack) if cds else head

    return re.sub(r"(?m)^##\s+.*$", _repl, body)


def _catalog_dp_render_compliance(body: str, entry) -> str:
    """Cierra el Data Product con el MISMO cumplimiento que Dominios (driver de L&F):
    intro por veredicto (semáforo) + tabla de hallazgos coloreada. El generador emite los
    hallazgos como bullets; acá se convierten con los helpers compartidos
    (_catalog_compliance_intro_md + _catalog_compliance_table_html)."""
    m = re.search(r"(?ms)^##\s+Cumplimiento[^\n]*\n(.*?)(?=\n##\s|\Z)", body)
    if not m:
        return body
    verdict = str(getattr(entry, "veredicto", "") or "").strip().lower()
    label = getattr(entry, "business_name", "") or getattr(entry, "title", "") or getattr(entry, "product_id", "")
    intro = _catalog_compliance_intro_md(
        verdict, str(getattr(entry, "apto", "") or _catalog_verdict_text(verdict)),
        f"el Data Product {label}",
    )
    table = _catalog_compliance_table_html(m.group(1))
    return body[:m.start()] + f"## Cumplimiento\n\n{intro}\n\n{table}\n" + body[m.end():]


def _catalog_cds_view(name: str):
    """Localiza APIs/<name>.md y lo prepara para renderizar con el MISMO pipeline de
    contenido (hero + secciones). Devuelve (meta, title, intro, sections) o None si
    no existe (el endpoint responde 404; el botón nunca se muestra sin archivo)."""
    from webapp.services.content_service import render_sections, split_front_matter
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = _catalog_apis_dir() / f"{name}.md"
    if not path.exists():
        return None
    meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="ignore"))
    body = body.strip()
    if body.count("```") % 2:   # fence sin cerrar (olvido del autor) → cerrar, tolerante
        body += "\n```"
    mt = re.search(r"^#\s+(.+)$", body, re.M)
    title = mt.group(1).strip() if mt else name
    rest = body[mt.end():] if mt else body
    h2 = re.search(r"^##\s+", rest, re.M)
    intro = (rest[:h2.start()] if h2 else rest)
    intro = re.sub(r"^[-\s]*$", "", intro, flags=re.M).strip().strip("_").strip()
    sections_md = rest[h2.start():] if h2 else ""
    sections = render_sections(sections_md, pack_name="", stem="", base=0) if sections_md else ()
    return meta, title, intro, sections



# Gobierno/Portabilidad: tonos, badge, tablas y filas viven en `webapp/services/asset_evaluation.py` (compartidos con el informe de
# Assessment). La CLASIFICACIÓN legacy de hallazgos (marcador/palabras clave → semáforo) NO cambia para las páginas existentes del
# Catálogo (conexiones, seguridad, data products): se conserva tal cual acá; el clasificador por marcador del generador es propio de
# «Mirada Danwor» (asset_evaluation.compliance_status).
def _catalog_compliance_status(raw: str) -> tuple[str, str]:
    """Map generated compliance markers/text to a visual status class + label."""
    value = (raw or "").lower()
    if any(token in value for token in ("rojo", "crit", "grave", "bloque", "no cumple", "??")):
        return "red", "Critico"
    if any(token in value for token in ("naranja", "parcial", "riesgo", "atender", "??")):
        return "orange", "Atender"
    if any(token in value for token in ("amarillo", "advert", "warning", "observ", "buena", "reutilizable con notas", "??")):
        return "amber", "Observar"
    if any(token in value for token in ("verde", "cumple", "plena", "correcto", "?", "?", "??")):
        return "green", "Cumple"
    return "blue", "Nota"


_catalog_status_badge = asset_evaluation.status_badge_html
_catalog_inline_html = asset_evaluation.inline_html
_catalog_evidence_html = asset_evaluation.evidence_html
_catalog_compliance_table_html = lambda source: asset_evaluation.compliance_table_html(source, classify=_catalog_compliance_status)
_PORT_CLASIF_LABEL = asset_evaluation.PORT_CLASIF_LABEL
_PORT_HALLAZGO_LABEL = asset_evaluation.PORT_HALLAZGO_LABEL


def _catalog_portability_table_html(findings: list, asset_type: str) -> str:
    return asset_evaluation.portability_table_html(findings)


def _catalog_portability_section_md(port: dict, asset_type: str, acks: list | None = None) -> str:
    """Sección markdown '## Portabilidad BDC' (dimensión cross-runtime, SEPARADA del
    cumplimiento de extracción). "" si no hay datos. El asset_type distingue el diagnóstico
    preventivo (dominio) del retrospectivo (data product: safety check + adaptaciones)."""
    if not port or not isinstance(port, dict):
        return ""
    findings = port.get("findings") or []
    view = asset_evaluation.portability_view(port, acks, asset_type)      # UNA sola semántica (etiquetas, KPI por campos distintos, copy) para Catálogo, Assessment y Data Product
    label = view["classification"]
    semaforo = port.get("semaforo") or "amber"
    conf = str(port.get("confianza") or "media").title()
    score = port.get("score")
    tiles = {t["label"]: t["value"] for t in view["tiles"]}
    nrisk, nind = tiles.get("Riesgos", 0), tiles.get("Normalizaciones / indicios", 0)
    noeval = port.get("campos_no_evaluables", 0)
    pill = _catalog_status_badge(semaforo, label)
    score_txt = f" · Consistencia **{score}/100**" if isinstance(score, int) else ""
    intro = (f"> {pill}{score_txt} · Confianza **{conf}**. {view['lead']}\n>\n"
             f"> Revisados **{port.get('campos_revisados', 0)}** campos · **{nrisk}** riesgo(s) · **{nind}** normalización/indicio"
             + (f" · **{noeval}** no evaluable(s)" if noeval else "") + "."
             + (" Evaluado con reglas anteriores (v1): regenerá el Catálogo para actualizarlo." if view["legacy"] else ""))
    ack_note = ""
    if acks:
        ack_note = f"\n\n> _{len(acks)} acknowledgement(s) registrados por el equipo._"
    table = _catalog_portability_table_html(findings, asset_type)
    body = table if table else "> Sin observaciones estructurales en las reglas evaluadas."
    return f"## Portabilidad BDC\n\n{intro}\n\n{body}{ack_note}\n"


_catalog_clean_label = catalog_reads.catalog_clean_label   # helper compartido con el informe de assessment (mismo título de artefacto)


_DELTA_SUFFIX = "_Delta"


def _catalog_delta_pairs(order: list, autos: dict, capture=None) -> dict:
    """{D: X} — pares «ingesta local X + su compañera técnica D = X_Delta» que son UN SOLO objeto lógico de ingesta delta-capable.
    Regla (determinista; ante cualquier duda NO colapsa). D se pliega en X solo si TODO esto se cumple:
      1. D == X + «_Delta» (sufijo exacto) y AMBOS están en el flujo (D sin su base X NUNCA se colapsa);
      2. ambos son «Tabla local» de la capa Ingesta y sin origen remoto;
      3. D es hoja (sin upstreams ni asociaciones) y X no lee nada distinto de D;
      4. nadie más que X consume D (si otro artefacto usa D como fuente ordinaria, D es un objeto semántico propio);
      5. evidencia CANÓNICA de captura delta de X (``capture(X)``, definición CSN `@DataWarehouse.delta`; la MISMA que muestra «Delta capture» en el detalle):
         ON **y** ``deltaFromEntities`` identifica a D como compañera. Sin esa confirmación (ON sin identidad de la compañera, OFF o evidencia
         INDETERMINADA) NO se pliega: no existe un par plegado sin evidencia canónica (ninguna heurística de nombre/estructura decide lo visible).
    Un artefacto que solo «termina en Delta» pero no cumple el par canónico queda tal cual."""
    present = set(order)
    consumers: dict[str, set] = {}
    for n in order:
        for u in list(autos[n].get("upstreams") or []) + list(autos[n].get("upstreams_asociacion") or []):
            consumers.setdefault(u, set()).add(n)
    is_local_ingest = lambda a: (a.get("artefacto_tecnico") == "Tabla local" and a.get("capa") == "Ingesta" and not a.get("origen_remoto"))   # noqa: E731
    out: dict = {}
    for d in order:
        if not d.endswith(_DELTA_SUFFIX) or len(d) == len(_DELTA_SUFFIX):
            continue
        x = d[: -len(_DELTA_SUFFIX)]
        if x not in present:
            continue
        ad, ax = autos[d], autos[x]
        if not (is_local_ingest(ad) and is_local_ingest(ax)):
            continue
        if ad.get("upstreams") or ad.get("upstreams_asociacion") or ax.get("upstreams_asociacion"):
            continue
        if set(ax.get("upstreams") or []) - {d} or (consumers.get(d, set()) - {x}):
            continue
        ev = capture(x) if capture else {"state": "UNDETERMINED", "companions": []}
        if ev["state"] != "ON" or d not in ev["companions"]:
            continue
        out[d] = x
    return out


_logical_count_cache: dict = {}


def _catalog_logical_artifact_count(entry) -> int:
    """Cantidad de artefactos del flujo que ve el USUARIO en el Catálogo (hero y tile): objetos LÓGICOS = la lista física menos las compañeras técnicas
    `_Delta` plegadas por ``_catalog_delta_pairs`` (el MISMO conjunto normalizado del linaje; no hay otra regla de conteo). El conteo FÍSICO sigue
    siendo ``len(entry.artefactos)`` y es el que usan el Assessment (cobertura/evidencia) y el detalle técnico (que conserva `X_Delta`).
    Sin candidatos (ningún `X_Delta` con su base `X` en el flujo) no hace I/O; con candidatos lee solo las vistas de ese flujo y cachea por proceso."""
    names = list(getattr(entry, "artefactos", None) or [])
    present = set(names)
    if not any(n.endswith(_DELTA_SUFFIX) and n[: -len(_DELTA_SUFFIX)] in present for n in names):
        return len(names)
    key = (getattr(entry, "entry_id", None), tuple(names))
    if key in _logical_count_cache:
        return _logical_count_cache[key]
    from webapp.services.content_service import split_front_matter
    vistas_dir = Path(entry.path).parent / "vistas"
    autos = {}
    for name in names:
        vpath = vistas_dir / f"{name}.md"
        if vpath.exists():
            meta, _body = split_front_matter(vpath.read_text(encoding="utf-8"))
            autos[name] = meta.get("auto") or {}
    capture = lambda n: catalog_delta_evidence.delta_capture(entry.cliente, entry.dominio, n)   # noqa: E731
    count = len(names) - len(_catalog_delta_pairs([n for n in names if n in autos], autos, capture))
    _logical_count_cache[key] = count
    return count


_DELTA_CAPTURE_LABEL = {"ON": "ON", "OFF": "OFF", "UNDETERMINED": "No determinado"}


def _catalog_add_delta_rows(vbody: str, state: str, companions: list) -> str:
    """Filas «Delta capture» (ON | OFF | No determinado) y, si la compañera técnica es conocida, «Tabla técnica de cambios» (solo trazabilidad), en la tabla
    de propiedades del detalle, justo después de «Artefacto». OFF nunca fabrica una tabla delta; ND jamás se convierte en OFF."""
    rows = f"| Delta capture | {_DELTA_CAPTURE_LABEL.get(state, 'No determinado')} |\n"
    if companions:
        rows += f"| Tabla técnica de cambios | {', '.join(companions)} |\n"
    m = re.search(r"(?ms)^\| Propiedad \| Valor \|\n\|[-| ]+\|\n(?:\|[^\n]*\n)+", vbody)
    if m is None:
        return vbody
    block = m.group(0)
    lines = block.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(lines) if ln.startswith("| Artefacto |")), len(lines) - 1) + 1
    return vbody[:m.start()] + "".join(lines[:at]) + rows + "".join(lines[at:]) + vbody[m.end():]


def _catalog_flow_view(entry, show_assoc: bool | None = None, pack: str = "", focus=None, focus_out: dict | None = None):
    """Arma el detalle de un flujo: (a) un diagrama SVG de linaje por capas y (b)
    las secciones estilo-módulo (una por artefacto, con sus tablas), renderizadas
    con el MISMO pipeline que los módulos (content_service.render_sections) para
    heredar el look & feel — no replicamos la lógica visual. El índice de secciones
    ES el linaje textual; el diagrama lo hace visual. El título de sección es la
    DESCRIPCIÓN con el nombre técnico entre llaves — `Descripción {NOMBRE}` — o solo
    el nombre si no hay descripción. Cierra con una sección de cumplimiento (los
    hallazgos del flujo_*.md). Las referencias entre artefactos y los nodos del
    diagrama linkean al ancla de la sección (mismo slug que el título mostrado).
    Devuelve (sections, lineage_svg, show_assoc, assoc_context).
    `focus` (nombres técnicos) resalta nodos del diagrama; `focus_out` (dict opcional) recibe
    {"present": [{name, slug}], "missing": [name]} — presente = nodo visible en el diagrama actual. Si
    la apertura es fresca (show_assoc None) y un nodo enfocado solo existe como asociación,
    se activan las asociaciones para poder mostrarlo."""
    from webapp.services.content_service import collapse_field_tables, render_sections, split_front_matter
    from core.utils import slugify

    def _heading(name: str, label: str) -> tuple[str, str]:
        """(heading con tonos para la sección, texto plano para el slug). Formato
        consistente en dos partes `A · B` (A en negro, B en dorado): con descripción
        → `{dw-black}Descripción · {dw-gold}NOMBRE`; sin descripción → se repite el
        nombre técnico `{dw-black}NOMBRE · {dw-gold}NOMBRE` (misma estructura visual)."""
        desc = catalog_reads.catalog_display_title(name, label)
        return f"{{dw-black}}{desc} · {{dw-gold}}{name}", f"{desc} · {name}"

    vistas_dir = Path(entry.path).parent / "vistas"
    names = list(entry.artefactos or [])
    bodies, headings, plains, autos = {}, {}, {}, {}
    for name in names:
        vpath = vistas_dir / f"{name}.md"
        if not vpath.exists():
            continue
        meta, body = split_front_matter(vpath.read_text(encoding="utf-8"))
        auto = meta.get("auto") or {}
        label = _catalog_clean_label(auto.get("label"), name)
        bodies[name] = body.strip()
        headings[name], plains[name] = _heading(name, label)
        autos[name] = auto
    slug_of = {name: slugify(plains[name]) for name in bodies}   # ancla = slug del texto plano (sin tonos)

    # Reclasificación directo/asociación (idempotente con el generador nuevo): una
    # entidad analítica (hecho/dimensión/cubo) tiene como linaje DIRECTO solo su base
    # transformacional (vista relacional / hecho); dimensiones, textos y fuentes
    # crudas → asociación (como Datasphere). Se aplica en runtime para que valga sin
    # regenerar los .md viejos.
    def _is_transform_base(x: str) -> bool:
        a = autos.get(x)
        if not a:
            return False
        sem = f"{a.get('data_category') or ''} {a.get('modeling_pattern') or ''}".upper()
        return (not a.get("origen_remoto")) and ("DIMENSION" not in sem)

    def _split_up(auto: dict) -> tuple[list, list]:
        up = [u for u in (auto.get("upstreams") or []) if u in bodies]
        assoc = [u for u in (auto.get("upstreams_asociacion") or []) if u in bodies]
        sem = f"{auto.get('data_category') or ''} {auto.get('modeling_pattern') or ''}".upper()
        if any(k in sem for k in ("FACT", "DIMENSION", "CUBE")):
            demote = {x for x in up if not _is_transform_base(x)}   # solo degradar, nunca promover
            up = [x for x in up if x not in demote]
            assoc = sorted(set(assoc) | demote)
        return up, assoc

    # Ingesta local + su compañera técnica `_Delta` = UN objeto lógico: se pliega en el grafo/secciones y su verdad técnica queda en el detalle de la base.
    _capture = lambda n: catalog_delta_evidence.delta_capture(entry.cliente, entry.dominio, n)   # noqa: E731 — evidencia canónica (CSN), UNA para colapso y detalle
    delta_pairs = _catalog_delta_pairs([n for n in names if n in bodies], autos, _capture)
    view_types = catalog_view_source.view_type_map(entry.cliente, entry.dominio)       # {vista → GRAPHICAL|SQL}; {} sin pool (los nodos de vista quedan sin ícono)
    # Nodos del flujo (sin num todavía: se numera por posición en el ORDEN VISIBLE).
    nodes, order = [], [n for n in names if n in bodies and n not in delta_pairs]
    for name in order:
        auto = autos[name]
        label = _catalog_clean_label(auto.get("label"), name)
        up, assoc = _split_up(auto)
        up = [u for u in dict.fromkeys(delta_pairs.get(u, u) for u in up) if u != name]          # aristas hacia `_Delta` → su base lógica (sin auto-bucles)
        assoc = [u for u in dict.fromkeys(delta_pairs.get(u, u) for u in assoc) if u != name]
        sem = f"{auto.get('data_category') or ''} {auto.get('modeling_pattern') or ''}".upper()
        nodes.append({
            "name": name, "num": 0, "slug": slug_of[name],
            "title": label if label != name else name,   # tooltip = descripción (o nombre si no hay)
            "label": name,                                # nodo = nombre técnico
            "layer": _catalog_layer_index(auto, name),
            "is_factcube": ("FACT" in sem or "CUBE" in sem),
            "upstreams": up,
            "assoc_upstreams": assoc,
            **_catalog_node_icon(auto, name, view_types),
        })

    # keep = artefactos visibles según el toggle. Las secciones del detalle y el SVG
    # se filtran y NUMERAN por el mismo orden visible (shown_order) → los números del
    # nodo y de su sección coinciden y se adaptan dinámicamente al toggle.
    # Default contextual de asociaciones: si no vino una preferencia explícita
    # (show_assoc None), se prenden solo cuando el output quedaría desconectado del
    # linaje directo pero tiene asociación (ver _catalog_should_default_associations).
    assoc_context = _catalog_should_default_associations(nodes)
    explicit_assoc = show_assoc is not None
    if show_assoc is None:
        show_assoc = assoc_context
    focus_list = list(dict.fromkeys(delta_pairs.get(str(f), str(f)) for f in (focus or ())))          # el foco sobre la compañera `_Delta` apunta a su objeto lógico
    if focus_list and not explicit_assoc and not show_assoc:
        direct_keep = _catalog_flow_keep(nodes, False)
        if any(n["name"] in focus_list and n["name"] not in direct_keep for n in nodes):
            show_assoc = True
    keep = _catalog_flow_keep(nodes, show_assoc)
    shown_order = [name for name in order if name in keep] or order
    num_of = {name: i + 1 for i, name in enumerate(shown_order)}
    for n in nodes:
        n["num"] = num_of.get(n["name"], 0)
    lineage_svg = _catalog_lineage_svg(nodes, show_assoc, keep, focus=focus_list)
    if focus_out is not None:
        visible = {n["name"]: n["slug"] for n in nodes if n["name"] in keep} if lineage_svg else {}
        focus_out["present"] = [{"name": f, "slug": visible[f]} for f in focus_list if f in visible]
        focus_out["missing"] = [f for f in focus_list if f not in visible]
    sql_views = catalog_view_source.sql_view_names(entry.cliente, entry.dominio)     # vistas SQL CON consulta (índice por huella del pool; sin texto SQL)
    parts = []
    for name in shown_order:
        # H1 → H2 (lambda: la descripción con tonos puede traer caracteres que re
        # interpretaría como grupos de reemplazo).
        vbody = re.sub(r"^\s*#\s+.*$", lambda _m: f"## {headings[name]}", bodies[name], count=1, flags=re.M)
        if autos[name].get("artefacto_tecnico") == "Tabla local":
            ev = _capture(name)
            vbody = _catalog_add_delta_rows(vbody, ev["state"], ev["companions"] if ev["state"] == "ON" else [])     # la tabla técnica sale SOLO de la evidencia canónica
        # Botón "Abrir CDS View": auto-vínculo al código del extractor cuando el
        # origen es ABAP_CDS.<X> y existe APIs/<X>.md (si no, no se renderiza →
        # robusto, sin link muerto). Va como bloque HTML tras el heading (markdown
        # lo levanta al header por venir marcado como heading-supports). Al volver del código se re-abre este mismo flujo.
        cds = _catalog_cds_link(autos[name].get("origen_remoto") or "")
        if cds:
            btn = _catalog_cds_button_html(cds, pack, entry.cliente, entry.dominio, entry.entry_id)
            vbody = re.sub(r"(?m)^##\s+.*$", lambda m: m.group(0) + btn, vbody, count=1)
        # Botón "Abrir SQL": SOLO si la vista es SQL y su JSON trae `@DataWarehouse.sqlEditor.query` (GRAPHICAL / UNKNOWN / SQL sin consulta: nada).
        if name in sql_views:
            sql_btn = _catalog_sql_button_html(name, pack, entry.cliente, entry.dominio, entry.entry_id)
            vbody = re.sub(r"(?m)^##\s+.*$", lambda m: m.group(0) + sql_btn, vbody, count=1)
        parts.append(vbody)
    # Cumplimiento y Portabilidad ya NO son secciones del flujo: se resumen en «Mirada Danwor» (ver `asset_evaluation`).
    composed = "\n\n".join(parts)
    # Linkear en las tablas → ancla de su sección, solo para artefactos VISIBLES
    # (evita links muertos a secciones filtradas por el toggle).
    shown_set = set(shown_order)
    composed = re.sub(
        r"`([A-Za-z0-9_]+)`",
        lambda m: (f"[{m.group(1)}](#{slug_of[delta_pairs.get(m.group(1), m.group(1))]})" if delta_pairs.get(m.group(1), m.group(1)) in shown_set else m.group(0)),
        composed,
    )
    # «Orquestación de ingesta» (colapsable bajo «Campos») SOLO en tablas locales de capa Ingesta: relaciones del manifiesto del CLI, detalle de los JSON técnicos.
    orch_of = {slug_of[n]: n for n in shown_order if (autos.get(n) or {}).get("capa") == "Ingesta" and (autos.get(n) or {}).get("artefacto_tecnico") == "Tabla local"}

    def _orch_cds_link(obj: str) -> str:
        obj = catalog_cds_docs.resolve(_catalog_apis_dir(), obj)
        if not obj:
            return ""                                     # sin código de la CDS en APIs/ (o sin correspondencia confiable) no hay enlace (nunca un link muerto)
        qs = urlencode({"name": obj, "cliente": entry.cliente, "dominio": entry.dominio, "flow": entry.entry_id, "pack": pack or DEFAULT_PACK})
        return f'<a href="#" role="button" hx-get="/catalog/cds?{html.escape(qs)}" hx-target="#catalog-detail-slot" hx-swap="innerHTML" @click.prevent>Abrir CDS View</a>'

    def _fold(s):
        body = collapse_field_tables(s.html)
        name = orch_of.get(s.slug)
        if name is None:
            return dataclasses.replace(s, html=body)
        orch = catalog_ingestion_orchestration.orchestration_for(entry.cliente, name, _capture(name))
        block = catalog_ingestion_orchestration.render_html(orch, f"orquestacion-{s.slug}", _orch_cds_link, diagram=_catalog_lineage_svg)
        return dataclasses.replace(s, html=catalog_ingestion_orchestration.inject_after_fields(body, block))

    sections = tuple(_fold(s) for s in render_sections(composed, pack_name="", stem="", base=0))    # tablas de campos plegadas (todas las capas)
    return sections, lineage_svg, show_assoc, assoc_context


@app.get("/catalog/flow", response_class=HTMLResponse, include_in_schema=False)
def catalog_flow(request: Request, cliente: str = "", dominio: str = "", flow: str = "",
                 pack: str = Query(DEFAULT_PACK), assoc: int | None = None, frag: str = "",
                 focus: str = ""):
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="flow", ref=(flow or f"{cliente}/{dominio}"), assoc=bool(assoc))
    """Detalle de un flujo del catálogo, renderizado en el panel central con el
    MISMO pipeline de contenido de los módulos (hero + diagrama de linaje +
    artículo .methodology). El contenido vive en los .md del catálogo. `assoc=1`
    incluye las aristas por asociación (indirectas) en el diagrama; `frag=lineage`
    devuelve solo el bloque del diagrama (para el toggle vía HTMX). `focus=A,B`
    (nombres técnicos de artefacto) resalta esos nodos del linaje, p. ej. al llegar
    desde "Ver en linaje" del informe de assessment; un nombre ausente del diagrama
    actual no es un error (se informa como "No visible")."""
    user = auth_service.session_user(request)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_entries()
    # Routing por identidad estable: con flow=<entry_id> resolvemos EXACTAMENTE ese
    # registro. (cliente, dominio) NO es único — un mismo par puede tener varios
    # flujos legítimos (distinto modelo de consumo). Sin flow (links legacy):
    # 1 match → compatibilidad; >1 → NO devolver el primero silenciosamente, se
    # muestran las opciones para elegir.
    entry = None
    if mod and flow:
        entry = mod.find_flow_by_id(entries, flow)
    elif mod:
        matches = mod.flows_for_client_domain(entries, cliente, dominio)
        if len(matches) == 1:
            entry = matches[0]
        elif len(matches) > 1:
            return templates.TemplateResponse(
                request=request, name="partials/catalog_ambiguous.html",
                context={"flows": matches, "cliente": cliente, "dominio": dominio,
                         "foco": None, "pack": selected},
            )
    if entry is None:
        raise HTTPException(404, "Flujo no encontrado en el catálogo")
    # assoc no especificado (apertura del flow) → el view decide el default contextual;
    # assoc=0/1 (toggle del usuario) → respeta la preferencia explícita.
    req_assoc = None if assoc is None else bool(assoc)
    focus_names = assessment_presenter.parse_focus(focus)
    focus_state: dict = {}
    # Los kwargs de foco solo viajan cuando hay foco: la apertura normal queda idéntica a la previa.
    view_kwargs = {"focus": focus_names, "focus_out": focus_state} if focus_names else {}
    sections, lineage_svg, show_assoc, assoc_context = _catalog_flow_view(entry, req_assoc, selected, **view_kwargs)
    lineage_ctx = {"lineage_svg": lineage_svg, "show_assoc": show_assoc,
                   "assoc_context": assoc_context,
                   "cliente": entry.cliente, "dominio": entry.dominio,
                   "entry_id": entry.entry_id, "pack": selected,
                   "focus_csv": ",".join(focus_names),
                   "focus_present": focus_state.get("present", []),
                   "focus_missing": focus_state.get("missing", focus_names),
                   "assessment_url": assessment_presenter.assessment_url(entry, selected),
                   "clear_focus_url": assessment_presenter.flow_url(entry, selected)}
    if frag == "lineage":   # solo el bloque del diagrama (toggle Asociaciones)
        return templates.TemplateResponse(request=request, name="partials/catalog_lineage.html",
                                          context=lineage_ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    ah_view = assessment_presenter.header_for(entry, selected)          # grado + hallazgos de gobierno SOLO del Assessment gobernado (None si falla: sin fallback legacy)
    heading_title, _heading_tech = assessment_presenter.flow_heading(entry, ah_view)      # identidad del MODELO terminal (negocio → nombre técnico legible); el dominio legacy no nombra un modelo identificado
    # HTMX → solo el fragmento (actualiza #content sin salir del módulo ni perder
    # el panel derecho). Navegación directa → página completa con shell + nav.
    tmpl = "partials/catalog_flow_content.html" if request.headers.get("HX-Request") else "catalog_flow.html"
    return templates.TemplateResponse(
        request=request, name=tmpl,
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request), "pack": selected,
            "pack_label": content_pack_short_label(selected), "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            "cliente": entry.cliente, "dominio": entry.dominio, "icon": entry.icon,
            "heading_title": heading_title,
            "veredicto": entry.veredicto, "apto": entry.apto,
            "n_art": _catalog_logical_artifact_count(entry), "sections": sections,
            "assessment_header": ah_view,
            "lob_label": ((ah_view or {}).get("line_of_business") or assessment_presenter.lob_display(None))["label"],
            "evaluation": asset_evaluation.asset_evaluation(
                entry, findings=(ah_view or {}).get("governance_findings"), assessed=bool((ah_view or {}).get("has_profile"))),
            "catalog_kind": "domain",
            "catalog_ref": f"{entry.cliente} · {entry.dominio}",
            **lineage_ctx,
        },
    )


@app.get("/catalog/data-product", response_class=HTMLResponse, include_in_schema=False)
def catalog_data_product(request: Request, product_id: str = "", pack: str = Query(DEFAULT_PACK), lineage: int = 0):
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="data_product", ref=product_id, lineage=bool(lineage))
    """Detalle central de un Data Product del catálogo BDC."""
    user = auth_service.session_user(request)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_data_products_entries()
    entry = mod.find_data_product(entries, product_id) if mod else None
    if entry is None:
        raise HTTPException(404, "Data Product no encontrado en el catálogo")
    from webapp.services.content_service import render_sections
    body = _catalog_dp_inject_cds_buttons(entry.body, entry.lineage_nodes, selected)
    body = _catalog_dp_render_compliance(body, entry)
    _port_md = _catalog_portability_section_md(
        getattr(entry, "portabilidad_bdc", None), "data_product", getattr(entry, "portabilidad_ack", None))
    if _port_md:
        body = body + "\n\n" + _port_md
    sections = render_sections(body, pack_name="", stem="", base=0)
    show_lineage = bool(lineage)
    lineage_svg = _catalog_data_product_lineage_svg(entry, show_lineage)
    ctx = {
        "product": entry,
        "sections": sections,
        "lineage_svg": lineage_svg,
        "show_lineage": show_lineage,
        "pack": selected,
        "catalog_kind": "data_product",
        "catalog_ref": entry.product_id,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/catalog_data_product_content.html", context=ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_data_product.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request),
            "pack_label": content_pack_short_label(selected), "user": user,
            **ctx,
        },
    )


@app.get("/catalog/cds", response_class=HTMLResponse, include_in_schema=False)
def catalog_cds(request: Request, name: str = "", cliente: str = "", dominio: str = "",
                flow: str = "", pack: str = Query(DEFAULT_PACK)):
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="cds", ref=name)
    """Código de una CDS view (extractor corporativo), renderizado en el panel
    central con el mismo pipeline de contenido. Se abre desde el botón "Abrir CDS
    View" del detalle de un flujo; "Volver" re-abre ese flujo (cliente+dominio)."""
    user = auth_service.session_user(request)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    view = _catalog_cds_view(name)
    if view is None:
        raise HTTPException(404, "CDS view no encontrada en el catálogo")
    meta, title, intro, sections = view
    ctx = {
        "cds_name": name, "cds_title": title, "cds_intro": intro,
        "tabla_base": str(meta.get("tabla_base") or ""),
        "extraccion": str(meta.get("extraccion") or ""),
        "sections": sections, "cliente": cliente, "dominio": dominio, "flow": flow, "pack": selected,
        "catalog_kind": "cds",
        "catalog_ref": name,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request,
                                          name="partials/catalog_cds_content.html", context=ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_cds.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request),
            "pack_label": content_pack_short_label(selected), "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            **ctx,
        },
    )


@app.get("/catalog/sql", response_class=HTMLResponse, include_in_schema=False)
def catalog_sql(request: Request, name: str = "", cliente: str = "", dominio: str = "",
                flow: str = "", pack: str = Query(DEFAULT_PACK)):
    """Fuente SQL de una vista de Datasphere (`@DataWarehouse.sqlEditor.query` del JSON del pool), renderizada como CÓDIGO en el MISMO panel y con
    la MISMA plantilla que "Abrir CDS View". Mismo acceso que el Catálogo (sesión obligatoria); 404 si la vista no es SQL con consulta.
    "Volver" re-abre el flujo de origen. No ejecuta, reescribe ni infiere SQL; el texto no se registra en logs."""
    user = auth_service.session_user(request)
    log_verbose("catalog_view", user=(user or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="sql", ref=name)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    src = catalog_view_source.read_view_sql(cliente, dominio, name)
    if src is None:
        raise HTTPException(404, "Vista SQL no encontrada en el catálogo")
    from webapp.services.content_service import render_sections
    longest = max((len(r) for r in re.findall(r"`+", src.sql)), default=0)
    fence = "`" * max(3, longest + 1)          # un SQL con ``` no puede cerrar el bloque: la valla es más larga que cualquier racha de comillas
    sections = render_sections(f"## Código SQL\n\n{fence}sql\n{src.sql}\n{fence}\n", pack_name="", stem="", base=0)
    identity = catalog_view_source.artifact_identity(name, cliente, dominio, flow)      # nombre de negocio + capa + uso semántico (misma metadata del Catálogo)
    ctx = {
        "cds_name": name, "cds_title": identity.business_name,
        "cds_intro": "Código SQL de la vista en Datasphere",
        "meta_prefix": identity.meta_segments(name)[:-1],
        "source_label": "Vista SQL", "tabla_base": "", "extraccion": "",
        "sections": sections, "cliente": cliente, "dominio": dominio, "flow": flow, "pack": selected,
        "catalog_kind": "sql", "catalog_ref": name,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/catalog_cds_content.html", context=ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_cds.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request),
            "pack_label": content_pack_short_label(selected), "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            **ctx,
        },
    )


@app.get("/catalog/guide/{slug}", response_class=HTMLResponse, include_in_schema=False)
def catalog_guide(request: Request, slug: str, pack: str = Query(DEFAULT_PACK),
                  embed: int = 0, theme: str = ""):
    log_verbose("catalog_view", user=(auth_service.session_user(request) or {}), scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="guide", ref=slug)
    """Guía (autoservicio) para publicar un catálogo. GENÉRICA por `slug`: cada
    how-to es un `guides/<slug>.yaml` del pack (dominios, orígenes/conexiones, …) —
    agregar una guía nueva NO requiere código, solo el YAML + un link. Se cuenta como
    storytelling estilo Danwor Academy (slides + rail): `embed=1` devuelve las slides
    (contenido del iframe); sin embed, el wrapper con shell. El CONTENIDO vive en el
    pack; lo carga guides_service. Capturas desde assets/<assets_dir>/ del pack."""
    user = auth_service.session_user(request)
    if not user:
        return _login_redirect(request, _pack(pack))
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    guide = guides.load(selected, slug)
    if guide is None:
        raise HTTPException(404, "Guía no encontrada en el pack")

    theme_cookie = request.cookies.get("dw-content-theme", "light")
    resolved_theme = (theme or theme_cookie)
    if resolved_theme not in ("light", "dark"):
        resolved_theme = "light"

    # Destino del "← Volver" (dentro del contenido, como en los detalles de flujo):
    # el módulo/pestaña Catálogo desde donde se invoca la guía. Lo decide el CONTENIDO
    # (guide.back_module: dominios→04, orígenes→01); default 04 si no viene.
    back_num = (guide.get("back_module") or "04").zfill(2)
    back_mod = next((m for m in list_modules(selected)
                     if str(m.number).zfill(2) == back_num
                     or m.stem.startswith(f"{back_num}_")), None)
    # Destino del "← Volver": si la guía declara `back_tool`, volvemos a ESA herramienta con el
    # contrato canónico `?tool=<id>` (abre su grupo y colapsa los demás — mismo estado que el
    # informe de assessment). Si no, al comportamiento histórico (pestaña Catálogo del módulo).
    back_tool = guide.get("back_tool") or ""
    if back_mod and back_tool:
        back_url = f"/modules/{back_mod.stem}?pack={selected}&tool={back_tool}"
    elif back_mod:
        back_url = f"/modules/{back_mod.stem}?pack={selected}&tab=catalogo"
    else:
        back_url = f"/?pack={selected}"

    # CTAs INTERNOS de los pasos: el YAML declara `link: {tool: "<id>", module?}`
    # (sin hostname ni path); acá resolvemos la URL REAL de la herramienta, porque
    # necesitamos list_modules para mapear el nº de módulo → stem. El template navega
    # esos links en la MISMA pestaña (target="_parent"). Destino por defecto: el módulo
    # de la propia guía (back_module). Si el módulo no resuelve, el CTA se omite (no se
    # inventa una ruta). Los externos (http/s) quedan igual (pestaña nueva).
    for _st in guide["steps"]:
        _ln = _st.get("link")
        if not (_ln and _ln.get("internal")):
            continue
        _num = (_ln.get("module") or back_num).zfill(2)
        _mod = next((m for m in list_modules(selected)
                     if str(m.number).zfill(2) == _num or m.stem.startswith(f"{_num}_")), None)
        # `tool: catalogo` es la PESTAÑA Catálogo (no una herramienta): `?tab=catalogo[&catalog_action=<action>]`; el resto, `?tool=<id>`.
        if _ln["tool"] == "catalogo":
            _q = "tab=catalogo" + (f"&catalog_action={_ln['action']}" if _ln.get("action") else "")
        else:
            _q = f"tool={_ln['tool']}"
        _ln["url"] = (f"/modules/{_mod.stem}?pack={selected}&{_q}" if _mod else "")
        if not _ln["url"]:
            _st["link"] = None

    # embed=1 → contenido de las slides (lo monta el iframe del wrapper).
    if embed:
        return templates.TemplateResponse(
            request=request, name="catalog_guide_slides.html",
            context={"pack": selected, "guide": guide, "steps": guide["steps"],
                     "theme": resolved_theme, "back_url": back_url, "slug": slug},
        )

    # Wrapper (navegación directa desde el link "¿Falta …?"): shell + academy-stage
    # con las slides embebidas (mismo patrón que /academy/{stem}). Sin panel derecho.
    iframe_src = (
        f"/catalog/guide/{quote(slug, safe='')}?"
        f"{urlencode({'embed': '1', 'pack': selected, 'theme': resolved_theme, 'branding': 'danwor'})}"
    )
    # Topbar completo, igual que módulos/Academy: el selector de marco (diagnóstico)
    # y el toggle de diagnóstico se muestran según el mismo criterio, para que el
    # alcance de botones sea el mismo que en la vista desde la que se invocó.
    diagnostic = _diagnostic(request)
    packs = [
        {"name": name, "label": content_pack_short_label(name),
         "description": content_pack_label(name), "home": _home_url(name)}
        for name in available_content_packs()
    ]
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_guide.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            # Nav izquierdo con el grupo CORE expandido: la guía no es un módulo
            # (active_stem=""), pero así la lista de módulos queda visible para
            # continuar de un click (además del "← Volver" al módulo que la llamó).
            "active_group": "core", "active_stem": "",
            "diagnostic": diagnostic, "is_owner": _is_owner(request), "packs": packs,
            "readonly": _readonly(request), "pack": selected,
            "pack_label": content_pack_short_label(selected), "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            "iframe_src": iframe_src, "show_panel": False,
        },
    )


@app.get("/catalog/como-publicar", response_class=HTMLResponse, include_in_schema=False)
def catalog_guide_legacy(request: Request, pack: str = Query(DEFAULT_PACK)):
    """Alias histórico: la ruta se generalizó a /catalog/guide/{slug}. Redirige a la
    guía de dominios para no romper links viejos."""
    return RedirectResponse(
        f"/catalog/guide/como-publicar-dominios?pack={_pack(pack)}", status_code=307)


@app.post("/catalog/regenerate", include_in_schema=False)
def catalog_regenerate(request: Request, graph_token: str = Form("")):
    """Self-service: regenera el catálogo desde la guía (botón del paso 4), sin
    pasar por el panel de gestión (owner-only). Gate = estar logueado; la lectura
    de SharePoint usa el token DELEGADO del propio colaborador (Graph), así que la
    frontera de seguridad es SharePoint (solo ve el CSN al que ya tiene acceso).
    Serializado por lock (no corre dos veces en paralelo) y auditado."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    ran, message = admin_tools.run_catalog_generation_locked(graph_token=graph_token)
    ok = ran
    if ran:
        _invalidate_catalog_cache()
    # Token de Graph vencido → SharePoint responde 401. Lo traducimos a una acción
    # clara para el colaborador (el token no se puede refrescar sin re-login).
    if ran and "401" in message and "unauthorized" in message.lower():
        ok = False
        message = ("Tu token SharePoint expiró, necesitamos que reinicies tu sesión al "
                   "copiloto (logout/login) y vuelvas a repetir esta acción con un token nuevo.")
    log_activity("catalog_regenerate", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, ran=ran, ok=ok)
    return JSONResponse({"ok": ok, "message": message})


def _admin_apis_only(request: Request, graph_token: str, step: str, plan_id: str, confirm: str, journal_id: str):
    """Modo «solo APIs/CDS» del panel (preflight de solo lectura → apply confirmado → rollback desde el journal). El token Graph llega en el request y no se guarda."""
    from webapp.services import catalog_apis_sync as apis
    context = {"plan": None, "outcome": None, "error": "", "confirm_phrase": apis.CONFIRM_PHRASE, "rollback_phrase": apis.ROLLBACK_PHRASE,
               "statement": apis.untouched_statement(), "status_text": apis.STATUS_TEXT}
    try:
        if step == "apply":
            context["outcome"] = apis.apply(graph_token, plan_id, confirm)
        elif step == "rollback":
            context["outcome"] = apis.rollback(journal_id, confirm)
        else:
            context["plan"] = apis.build_plan(graph_token)
    except apis.ApisSyncError as exc:
        context["error"] = str(exc)
    except Exception:  # noqa: BLE001 — el panel nunca devuelve un 500 opaco
        logging.getLogger("danwor.catalog_apis_sync").exception("sincronización solo-APIs: falló")
        context["error"] = "Falló la operación por un error interno; no se modificó nada que no esté informado arriba. Revisá el log."
    outcome = context["outcome"]
    if outcome is not None and outcome.state in ("ok", "local_only", "rolled_back", "incomplete"):
        _invalidate_catalog_cache()
    user = auth_service.session_user(request) or {}
    log_activity("catalog_apis_sync", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, step=step or "preflight", state=(outcome.state if outcome else "preflight"))
    return templates.TemplateResponse(request=request, name="partials/admin_apis_sync.html", context=context)


@app.post("/admin/catalog/generate", response_class=HTMLResponse)
def admin_catalog_generate(request: Request, graph_token: str = Form(""), scope: str = Form(""), step: str = Form(""), plan: str = Form(""),
                           confirm: str = Form(""), journal: str = Form("")):
    _require_diagnostic(request)
    if scope == "apis_only":
        return _admin_apis_only(request, graph_token, step, plan, confirm, journal)
    output = admin_tools.run_catalog_generation(graph_token=graph_token)
    _invalidate_catalog_cache()
    return _admin_result(request, "Catálogo CSN — regeneración", output)


@app.post("/admin/feedback/analyze", response_class=HTMLResponse)
def admin_feedback_analyze(request: Request, kind: str = Form("all")):
    _require_diagnostic(request)
    output = admin_tools.run_feedback_analysis(kind=kind)
    return _admin_result(request, "Feedback — análisis", output)


@app.post("/admin/trees/propose", response_class=HTMLResponse)
def admin_trees_propose(request: Request, apply: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_tree_proposals(apply=_truthy(apply))
    return _admin_result(request, "Propuestas de árbol", output)


@app.post("/admin/content/propose", response_class=HTMLResponse)
def admin_content_propose(request: Request, llm: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_content_proposals(use_llm=_truthy(llm))
    return _admin_result(request, "Propuestas de contenido", output)


def _owner_key(request: Request) -> str:
    user = auth_service.session_user(request) or {}
    return str(user.get("sub") or "anon").strip().lower()


@app.post("/admin/sharepoint/refresh-cache", response_class=HTMLResponse)
def admin_sharepoint_refresh(request: Request, graph_token: str = Form("")):
    _require_diagnostic(request)
    message = admin_tools.sharepoint_refresh_cache(graph_token)
    return _admin_result(request, "SharePoint — cache", message)


@app.post("/admin/sharepoint/seed", response_class=HTMLResponse)
def admin_sharepoint_seed(request: Request, graph_token: str = Form("")):
    _require_diagnostic(request)
    preview, count = admin_tools.run_sharepoint_seed(graph_token, _owner_key(request))
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_sharepoint.html",
        context={"preview": preview, "count": count},
    )


@app.get("/admin/sharepoint/seed.json")
def admin_sharepoint_seed_download(request: Request):
    _require_diagnostic(request)
    payload = admin_tools.last_seed(_owner_key(request))
    if not payload:
        raise HTTPException(404, "No hay seed generado en esta sesión")
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sharepoint_cases_semantic_seed.json"'},
    )


if __name__ == "__main__":
    # `python -m webapp.main` levanta en 8501, el puerto cuyo redirect URI
    # (http://localhost:8501/) ya está registrado en Azure para el login MSAL.
    import uvicorn

    uvicorn.run("webapp.main:app", host="127.0.0.1", port=8501, reload=True)
