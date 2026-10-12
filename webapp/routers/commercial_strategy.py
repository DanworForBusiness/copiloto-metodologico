"""
commercial_strategy.py — Estrategia Comercial HTTP (Commercial Strategy).

Segunda capability top-level de Comercial (`02 · Estrategia`, junto a
`01 · CRM` en `commercial.py`) — router DEDICADO en vez de agregar a
`commercial.py` (owner decision explícita: ese archivo ya crece por trabajo
en curso no relacionado). MISMO gate de sesión que `commercial.py`
(`commercial_access.is_commercial_authorized`) — Estrategia es una vista más
del MISMO workspace Comercial, nunca un segundo criterio de autorización.

LECTURA (`GET /comercial/estrategia`): cualquier usuario autorizado a
Comercial (`can_view_strategy_user`).

ESCRITURA (`POST /comercial/estrategia`) — habilitada tras confirmación de
topología del owner (DigitalOcean: 1 contenedor / 1 proceso uvicorn para
esta release — ver `commercial_strategy_repository.py` para el contrato
exacto que esa confirmación habilita, y qué cambiaría si el número de
instancias sube). Capas de enforcement, EN ORDEN:

  1. Sesión válida (redirect si no).
  2. `commercial_access.can_edit_strategy_user(user)` — SOLO `CONTENT_OWNER`
     llega más allá de este punto. 403 si no. Autoritativo: el botón
     "Editar" oculto en el template NUNCA es la defensa real.
  3. `same_origin_guard.is_same_origin_write(request)` — protección mínima
     anti-CSRF (ver ese módulo). 403 si el request no declara el MISMO
     origen que sirvió la página.
  4. JSON parseable, con `revision` entero — 400 si no.
  5. `commercial_strategy_editing.merge_editable_fields(body)` — valida
     TODO el payload antes de tocar cualquier cosa; rechazo atómico
     (422) — nunca una persistencia parcial.
  6. `commercial_strategy_repository.save_strategy(...)` — revision
     optimista (409 si desactualizada, SIN escribir nada), escritura
     atómica local + push SÍNCRONO a Spaces (503 + rollback local si el
     push falla — ver ese módulo). 200 SOLO si terminó durable.

Fail-soft en lectura (MISMO contrato que `commercial.py`/`reporting.py`): un
documento ausente o corrupto (`CommercialStrategyUnavailable`) nunca produce
un 500 crudo — la página renderiza en un estado "no disponible" explícito.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

import yaml
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from config import FEEDBACK_LOG_DIR
from core import remote_store
from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, same_origin_guard, workspaces
from webapp.services import auth_service, commercial_strategy_editing, commercial_strategy_markup
from webapp.services.activity_context import AREA_STRATEGY, SCOPE_COMMERCIAL, EVENT_MODULE_VIEWED
from webapp.services.activity_service import log_activity
from webapp.services.commercial_strategy_editing import ValidationError
from webapp.services.commercial_strategy_repository import (
    CommercialStrategyUnavailable,
    DurablePushFailedError,
    StaleRevisionError,
    load_strategy,
    save_strategy,
)
from webapp.services.module_registry import CM02_MODULE_ID
from webapp.templating import templates

# Identidades TÉCNICAS estables de sección (ver el reporte del owner: "usar
# identificadores técnicos estables ya presentes en el modelo de Estrategia,
# nunca el título mutable") — son LITERAL las claves de nivel superior del
# documento (`webapp.services.commercial_strategy_data.strategy.yaml`), la
# MISMA fuente que ya usa el template para renderizar cada sección — nunca
# una segunda lista inventada aparte. `hero`/`revision`/`meta`/`nav`/
# `version` quedan fuera a propósito (no son "secciones" con feedback propio
# — la portada no tiene botonera, MISMO criterio que Copilot no le pone
# feedback al `#__module__`/hero de un módulo).
_FEEDBACK_SECTIONS = (
    "objectives", "demand_generation", "segments", "focus", "capacity",
    "cadence", "exclusions", "next_steps", "annex_map", "annex_roles",
    "annex_process", "annex_rules",
)

# Contextual Section Feedback gate (ver HANDOFF): fragmento gramatical
# OPCIONAL que reemplaza "esta sección" en la pregunta del primitivo
# compartido (`partials/section_feedback.html`, ver su docstring — el
# parcial solo interpola texto, nunca infiere nada). ÚNICA fuente de esta
# clasificación semántica (Single Source Cleanup, ver HANDOFF) — ya NO
# existe una segunda copia en strategy.html: tanto el render inicial
# completo como los 3 endpoints HTMX de feedback (que re-renderizan el
# parcial sin pasar por el documento entero) llaman a
# `feedback_subject_for_section()` más abajo. "exclusions"/"next_steps"
# (activación) quedan deliberadamente fuera del dict — el resolver
# devuelve `None`, y el parcial cae al fallback genérico "esta sección"
# (ver gate: ninguno de los dos es un pilar).
_FEEDBACK_SUBJECT_BY_SECTION = {
    "objectives": "este pilar", "demand_generation": "este pilar", "segments": "este pilar",
    "focus": "este pilar", "capacity": "este pilar", "cadence": "este pilar",
    "annex_map": "este anexo", "annex_roles": "este anexo",
    "annex_process": "este anexo", "annex_rules": "este anexo",
}


def feedback_subject_for_section(section_id: str) -> str | None:
    """Fragmento gramatical opcional para el prompt de feedback de esta
    sección (`subject` del primitivo compartido) — ÚNICO punto que
    cualquier caller (render inicial, HTMX) debe llamar; nunca leer
    `_FEEDBACK_SUBJECT_BY_SECTION` directamente desde otro módulo."""
    return _FEEDBACK_SUBJECT_BY_SECTION.get(section_id)


# Hub Transversal Module Section Navigation (ver HANDOFF) — Estrategia es el
# PRIMER consumidor, nunca el dueño de la forma. `_SECTION_NAV_MAP` es la
# ÚNICA fuente estructural (anchor_id -> family/código/content_key) —
# MOVIDA acá desde el `{% set cs_nav_map = {...} %}` que antes vivía
# hardcodeado dentro de `strategy.html` (Information Architecture pass, ver
# ese template) para que sea alcanzable TANTO por el índice in-page/eyebrows
# de sección COMO por el nav lateral persistente del Hub (`commercial_nav.
# html`), sin duplicar el mapeo estructural (Sidebar Section Nav Gate:
# "ONE canonical structural mapping, never a second Strategy section map").
# Familia/código/orden son metadata ESTRUCTURAL pura — código de template,
# nunca `strategy.nav[].group` ni ningún otro campo leído del documento
# (seed o runtime), MISMO principio que ya regía cuando vivía en el template.
_SECTION_NAV_MAP = {
    "p1": ("pilares", "01", "objectives"), "genda": ("pilares", "02", "demand_generation"),
    "p2": ("pilares", "03", "segments"), "p3": ("pilares", "04", "focus"),
    "p4": ("pilares", "05", "capacity"), "p5": ("pilares", "06", "cadence"),
    "p6": ("activacion", "E01", "exclusions"), "proximos": ("activacion", "E02", "next_steps"),
    "mapa": ("anexos", "A01", "annex_map"), "roles": ("anexos", "A02", "annex_roles"),
    "proceso": ("anexos", "A03", "annex_process"), "reglas": ("anexos", "A04", "annex_rules"),
}

# Etiquetas de grupo del SIDEBAR -- acortadas a propósito (owner feedback
# tras validación en browser: la columna angosta del nav lateral hacía
# envolver "PILARES ESTRATÉGICOS"/"DE LA ESTRATEGIA A LA ACCIÓN" a 2
# líneas). NUNCA toca las etiquetas del índice in-page "EN ESTE MÓDULO"
# (esas viven aparte, literales en strategy.html -- tienen 3 columnas
# anchas ahí, entran en una línea sin problema, mismo criterio "cada
# consumidor decide su propia presentación sobre el MISMO dato
# estructural").
_SECTION_NAV_GROUP_LABELS = {
    "pilares": "Pilares",
    "activacion": "Acciones",
    "anexos": "Anexos",
}

# Ancla ESTABLE de portada — ya existía (`<section id="hero" class=
# "module-hero">`, ver strategy.html), ya participa del scroll-spy
# compartido (`copilotLayout.activeSection`, ver app.js: su selector
# `.module-hero, .content-section` ya la incluye) — nunca una ancla nueva.
_SECTION_NAV_TOP_ID = "hero"
_SECTION_NAV_TOP_LABEL = "Portada"


def _build_section_navigation(strategy: dict) -> dict:
    """Contrato GENÉRICO opcional de navegación de secciones (Hub
    Transversal Module Section Navigation) — Estrategia es el primer
    consumidor, nunca el dueño de la forma; cualquier otro módulo futuro
    que quiera opt-in construye la MISMA forma `{top, groups}` desde su
    propio contenido, nunca importando nada Estrategia-específico de acá.

    Construido a partir de `_SECTION_NAV_MAP` (estructural: family/código/
    orden) + `strategy[content_key]["title"]` (negocio, MISMA fuente exacta
    que ya usa el índice in-page y los eyebrows de sección — nunca una
    copia). `None` si `strategy` es `None` (documento no disponible,
    MISMO fail-soft que el resto de esta página) — el caller entonces no
    agrega `section_navigation` al contexto, y `commercial_nav.html` no
    renderiza nada nuevo (opt-in por ausencia, nunca un flag booleano
    aparte)."""
    groups: dict[str, list[dict]] = {}
    for anchor_id, (family, code, content_key) in _SECTION_NAV_MAP.items():
        groups.setdefault(family, []).append({
            "id": anchor_id,
            "code": code,
            "label": strategy[content_key]["title"],
            "href": f"#{anchor_id}",
        })
    return {
        "top": {"id": _SECTION_NAV_TOP_ID, "label": _SECTION_NAV_TOP_LABEL, "href": f"#{_SECTION_NAV_TOP_ID}"},
        "groups": [
            # `links`, NUNCA `items` -- Jinja resuelve `group.items` contra
            # el método builtin `dict.items` de un dict plano ANTES de caer
            # a `group['items']` (footgun conocido: getattr gana sobre
            # __getitem__), lo que rompía el render con "'builtin_function_
            # or_method' object is not iterable". `links` no colisiona con
            # ningún método de dict.
            {"label": _SECTION_NAV_GROUP_LABELS[family], "links": links}
            for family, links in groups.items()
        ],
    }


router = APIRouter()
_logger = logging.getLogger("danwor.commercial_strategy")

# Filtro Jinja de la vertical (MISMO patrón que main.py registrando
# `catalog_verdict_*` sobre `templates.env` — este router no importa
# `webapp.main`, así que registra el suyo directamente, igual que
# `templating.py` anticipa para el "segundo workspace", ver su docstring).
templates.env.filters["strategy_markup"] = commercial_strategy_markup.render
templates.env.filters["strategy_status_class"] = commercial_strategy_markup.status_badge_class


def _require_commercial_session(request: Request, *, next_path: str | None = None):
    """MISMO patrón que `commercial._require_commercial_session` —
    deliberadamente duplicado acá (no importado desde `commercial.py`):
    este Hub ya trata este helper de 3 líneas como duplicación aceptada por
    router (ver `staffing._require_operations_session`/`commercial.
    _require_commercial_session`), nunca extraído a un módulo compartido."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not commercial_access.is_commercial_authorized(request):
        raise HTTPException(403, "No autorizado para Comercial")
    return None


def _editable_doc(strategy: dict) -> dict:
    """Estado mutable de EDIT que recibe el cliente — TODO valor visible de
    negocio ahora es editable (gate "COMPLETE VISIBLE BUSINESS VALUE
    EDITABILITY", ver HANDOFF), así que en vez de curar campo por campo
    (que quedaba obsoleto cada vez que se agregaba una sección editable),
    esto manda la sección COMPLETA tal cual vive en el canónico para cada
    clave de `EDITABLE_TOP_KEYS` — incluye algunos campos estructurales
    (eyebrow/title/ids/headers de tabla) que el cliente nunca edita
    (ninguna afordancia de UI los toca) pero que viajan igual porque no
    son sensibles: son el MISMO contenido que la página ya renderiza
    server-side. El validador server-side (`merge_editable_fields`) sigue
    siendo la ÚNICA frontera de verdad — nunca confía en lo que vuelva de
    acá, recalcula lo estructural desde el documento canónico de nuevo."""
    doc = {"revision": strategy["revision"]}
    for key in commercial_strategy_editing.EDITABLE_TOP_KEYS:
        doc[key] = strategy[key]
    return doc


@router.get("/comercial/estrategia", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_page(request: Request):
    redirect = _require_commercial_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_STRATEGY, module=CM02_MODULE_ID)

    try:
        strategy = load_strategy()
        error_state = ""
        editable_doc = _editable_doc(strategy)
        section_navigation = _build_section_navigation(strategy)
    except CommercialStrategyUnavailable as exc:
        _logger.warning("commercial_strategy: documento no disponible: %s", exc)
        strategy = None
        error_state = "unavailable"
        editable_doc = None
        section_navigation = None

    return templates.TemplateResponse(
        request=request, name="commercial/strategy.html",
        context={
            "current_workspace": workspaces.COMMERCIAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "estrategia",
            # Hub Telemetry Origin Attribution gate (ver HANDOFF): MISMO
            # `CM02_MODULE_ID` que ya se usa en el `log_activity` de arriba
            # -- expuesto también al cliente (ver base.html
            # window.DW_MODULE_ID) para que el beacon compartido de
            # nav_jump (`.section-index a`, ver app.js) se atribuya a
            # Estrategia en vez de caer en Copiloto/Metodología por default.
            "dw_module_id": CM02_MODULE_ID,
            "strategy": strategy,
            "error_state": error_state,
            # `nav_map` = `_SECTION_NAV_MAP` tal cual (compat): el template
            # sigue leyendo el MISMO identificador/forma que su antiguo
            # `{% set cs_nav_map %}` local para el índice in-page + los 12
            # eyebrows de sección, ahora resuelto en Python (fuente única).
            "nav_map": _SECTION_NAV_MAP,
            # `section_navigation`: contrato GENÉRICO transversal (Hub
            # Transversal Module Section Navigation, ver HANDOFF) — `None`
            # si el documento no está disponible (fail-soft, MISMO criterio
            # que `strategy`/`editable_doc` arriba). Genericity Gate:
            # `commercial_nav.html` renderiza el nav anidado con SOLO
            # `{% if section_navigation %}` — nunca chequea
            # `active_page == "estrategia"` (ese acoplamiento se retiró):
            # opt-in por AUSENCIA de esta variable, nunca un flag booleano
            # aparte, y la identidad de Estrategia no vive en la condición.
            "section_navigation": section_navigation,
            # `can_edit`: SOLO gobierna si el botón "Editar" se RENDERIZA —
            # nunca la defensa real (ver POST abajo, que repite el mismo
            # chequeo server-side de forma autoritativa).
            "can_edit": commercial_access.can_edit_strategy_user(user),
            "editable_doc": editable_doc,
            # Single Source Cleanup (ver HANDOFF): el template YA NO trae su
            # propia copia de la clasificación pilar/activación/anexo para
            # el `subject` de feedback — recibe el resultado YA RESUELTO acá,
            # desde el MISMO resolver que usan los 3 endpoints HTMX de
            # feedback más abajo (`feedback_subject_for_section`).
            "feedback_subjects": {sid: feedback_subject_for_section(sid) for sid in _FEEDBACK_SECTIONS},
        },
    )


@router.post("/comercial/estrategia", include_in_schema=False)
async def commercial_strategy_save(request: Request):
    """Endpoint de escritura — ver docstring del módulo para las 6 capas de
    enforcement en orden. Autoritativo: nunca confía en que el botón
    "Editar" estuviera oculto en el cliente."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not commercial_access.can_edit_strategy_user(user):
        raise HTTPException(403, "No autorizado para editar Estrategia")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")

    raw_body = await request.body()
    try:
        body = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(body, dict) or not isinstance(body.get("revision"), int):
        raise HTTPException(400, "Falta 'revision' (entero) en el payload")
    expected_revision = body["revision"]

    try:
        current = load_strategy()
    except CommercialStrategyUnavailable:
        raise HTTPException(503, "Estrategia no disponible")

    try:
        # `current`: fuente de los campos ESTRUCTURALES que cada validador
        # recupera (headers de tabla, access.kind, ids de segmento/subtab —
        # ver commercial_strategy_editing.merge_editable_fields). Son
        # topología/seed, nunca se editan ellos mismos, así que usar la
        # versión recién leída (no necesariamente la misma `expected_revision`
        # que mandó el cliente) es seguro — el chequeo de concurrencia
        # optimista real sigue siendo 100% responsabilidad de `save_strategy`
        # más abajo, esto no lo reemplaza ni lo adelanta.
        updated_fields = commercial_strategy_editing.merge_editable_fields(body, current)
    except ValidationError as exc:
        raise HTTPException(422, str(exc))

    updated_by_id = str(user.get("sub") or "")
    updated_by_label = str(user.get("username") or user.get("email") or user.get("sub") or "")

    try:
        saved = save_strategy(
            updated_fields, expected_revision=expected_revision,
            updated_by_id=updated_by_id, updated_by_label=updated_by_label,
        )
    except StaleRevisionError as exc:
        return JSONResponse(
            {"state": "stale", "current_revision": exc.current_revision,
             "message": "La Estrategia cambió desde que la cargaste — recargá la página antes de "
                        "volver a guardar. Nada se sobrescribió."},
            status_code=409,
        )
    except DurablePushFailedError:
        _logger.error("commercial_strategy: push durable falló, save abortado (revision %s)", expected_revision)
        return JSONResponse(
            {"state": "unavailable", "reason": "durable_push_failed",
             "message": "No se pudo guardar de forma durable — el documento anterior no fue modificado. "
                        "Reintentá en unos minutos; tus cambios en pantalla no se perdieron."},
            status_code=503,
        )
    except CommercialStrategyUnavailable as exc:
        _logger.warning("commercial_strategy: save sin documento base: %s", exc)
        raise HTTPException(503, "Estrategia no disponible")

    log_activity(
        "strategy.saved", user=user, scope=SCOPE_COMMERCIAL, area=AREA_STRATEGY,
        module=CM02_MODULE_ID, revision=saved["revision"],
    )
    return JSONResponse({"state": "ok", "document": _editable_doc(saved)})


# ── Feedback por sección — REUSA el primitivo transversal de Copilot ───────
# (`partials/section_feedback.html`, `.section-feedback*` en tools.css,
# `feedback_store`/`type="section_content"`/evento `"section.feedback"` del
# pipeline de Activity — ver docstrings de webapp/routers/feedback.py y
# webapp/services/feedback_store.py) — CERO componente/endpoint/storage/
# esquema de evento nuevo. Lo único que cambia respecto de Copilot:
#   - el namespace de ruta (`/comercial/estrategia/sections/<id>` en vez de
#     `/modules/<stem>/sections/<slug>`, porque Estrategia no tiene `stem`)
#   - `scope=SCOPE_COMMERCIAL`/`area=AREA_STRATEGY` en vez de
#     SCOPE_COPILOT/AREA_METHODOLOGY (scope/area SIEMPRE reflejan el
#     workspace real — nunca se le pone la etiqueta de otro)
#   - la autorización: `can_view_strategy_user` (VER Estrategia), NUNCA
#     `can_edit_strategy_user` — dejar feedback de sección es una acción de
#     LECTURA/reacción del Hub, no una edición de contenido de negocio;
#     acoplarla a CONTENT_OWNER sería inventar un requisito que ni Copilot
#     ni Comercial exigen para reaccionar a contenido que ya podés ver.


def _require_can_view_strategy(request: Request):
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not commercial_access.can_view_strategy_user(user):
        raise HTTPException(403, "No autorizado para Comercial")
    return user


def _section_or_404(section_id: str) -> dict:
    if section_id not in _FEEDBACK_SECTIONS:
        raise HTTPException(404, "Sección no encontrada")
    try:
        strategy = load_strategy()
    except CommercialStrategyUnavailable as exc:
        raise HTTPException(503, "Estrategia no disponible") from exc
    return strategy[section_id]


def _strategy_section_feedback_ctx(section_id: str, section: dict, **extra) -> dict:
    return {
        "feedback_id": section_id,
        "feedback_url_base": f"/comercial/estrategia/sections/{section_id}",
        "pack": DEFAULT_PACK,
        "subject": feedback_subject_for_section(section_id),
        **extra,
    }


def _save_strategy_section_feedback(request, user, section_id: str, section_title: str, rating: str, comment: str = "") -> None:
    """MISMA implementación que `feedback._save_section_feedback` —
    `type="section_content"` (encaja tal cual: `module`/`section_slug`/
    `section_title` es exactamente la forma que ese tipo ya espera, ver su
    docstring — nunca una taxonomía nueva), mismo store, mismo formato de
    archivo. Deliberadamente duplicada acá en vez de importada de
    `feedback.py` — MISMO criterio que ese módulo documenta para sus propias
    4 implementaciones ("duplicadas a propósito, no una plataforma de
    feedback genérica")."""
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "section_content",
            "module": CM02_MODULE_ID,
            "module_title": "Estrategia Comercial",
            "section_slug": section_id,
            "section_title": section_title,
            "pack": DEFAULT_PACK,
            "rating": rating,
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
            "ts": now.isoformat(),
        }
        clean_comment = (comment or "").strip()
        if clean_comment:
            payload["comment"] = clean_comment
        path = FEEDBACK_LOG_DIR / f"section_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass
    try:
        log_activity(
            "section.feedback", user=user, scope=SCOPE_COMMERCIAL, area=AREA_STRATEGY,
            module=CM02_MODULE_ID, section=section_id, section_title=section_title,
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001
        pass


@router.post("/comercial/estrategia/sections/{section_id}/feedback", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_section_feedback(request: Request, section_id: str, rating: str = Form("")):
    user = _require_can_view_strategy(request)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    section = _section_or_404(section_id)
    if rating == "down":
        return templates.TemplateResponse(
            request=request, name="partials/section_feedback.html",
            context=_strategy_section_feedback_ctx(section_id, section, form=True),
        )
    _save_strategy_section_feedback(request, user, section_id, section["title"], "up")
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_strategy_section_feedback_ctx(section_id, section, submitted=True, rating="up"),
    )


@router.post("/comercial/estrategia/sections/{section_id}/feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_section_feedback_submit(request: Request, section_id: str, comment: str = Form("")):
    user = _require_can_view_strategy(request)
    section = _section_or_404(section_id)
    _save_strategy_section_feedback(request, user, section_id, section["title"], "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_strategy_section_feedback_ctx(section_id, section, submitted=True, rating="down"),
    )


@router.post("/comercial/estrategia/sections/{section_id}/feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def commercial_strategy_section_feedback_cancel(request: Request, section_id: str):
    _require_can_view_strategy(request)
    section = _section_or_404(section_id)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_strategy_section_feedback_ctx(section_id, section),
    )
