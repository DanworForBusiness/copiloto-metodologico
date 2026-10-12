"""
feedback.py — Feedback Family: módulo, sección, catálogo, sección de catálogo
(feature COPILOT).

Router de las 12 rutas de reacción/comentario (👍/👎 + comentario opcional +
cancelar) de 4 familias: feedback general de módulo, micro-feedback por
sección de módulo, feedback de cierre de catálogo y micro-feedback por
sección de catálogo. Extraído de ``webapp/main.py`` (Fase 2H) como relocación
pura — sin rediseño de auth/persistencia/presentación.

AUTH HARDENING (owner AUDIT "REACTIONS — TRACE MISSING USER IDENTITY" +
ticket "REACTIONS — FINAL AUTH HARDENING"): los 4 `_save_*` ya NO coercen
`auth_service.session_user(request) or {}` en silencio — sin sesión válida,
levantan `HTTPException(401, "Sin sesión")` ANTES de cualquier persistencia
(mismo código/mensaje que ya usan las páginas GET equivalentes, ver
webapp/routers/modules.py y webapp/main.py). Una reacción del Hub siempre
requiere usuario autenticado; las dos reacciones históricas ya backfilleadas
sin identidad (catálogo/metodología, PRE-existentes a este hardening) no se
tocan ni se re-fabrican — el evento de Activity Log persiste intacto, tal
cual (owner ticket "REACTIONS — EXCLUDE LEGACY UNATTRIBUTED FEEDBACK":
Reacciones ahora las excluye de la métrica por completo, en vez de
mostrarlas bajo un bucket "Sin usuario identificado").

Feature COPILOT que consume capacidades compartidas desde sus dueños explícitos
(`core.content_registry`, `webapp.services.auth_service`,
`webapp.services.content_service`, `webapp.templating`, `config`) SIN importar
``webapp.main``.

Las 4 implementaciones de persistencia (`_save_*`) se mantienen **deliberadamente
duplicadas** (mismo patrón mkdir + payload YAML + write-tmp + replace + best-effort):
esta fase establece ownership, no diseña una plataforma de feedback genérica.

NO cubre: Academy feedback (`webapp/routers/academy.py`, Fase 2E), Assistant
response feedback (stateful, `assistant_store`/`assistant_feedback_repository`),
Wizard/Trees feedback (stateful, `store`) ni `/trees/suggest` (mismo seam de
persistencia pero feature distinta — sigue en `main`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import yaml

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from config import FEEDBACK_LOG_DIR
from core import remote_store
from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp.services import auth_service
from webapp.services import module_registry
from webapp.services.activity_context import AREA_CATALOG, AREA_METHODOLOGY, SCOPE_COPILOT
from webapp.services.activity_service import log_activity
from webapp.services.content_service import load_module
from webapp.templating import templates

router = APIRouter()


def normalize_feedback_module(raw_module: str) -> tuple[str, str]:
    """(raw_module, module) para un valor de `module` de feedback — MISMO
    patrón que `activity_analytics._read_events`: `raw_module` preserva
    EXACTO el valor persistido (stem legacy o ya canónico); `module` es su
    `module_id` canónico si `module_registry` lo reconoce, o el mismo valor
    sin cambios si no (nunca se fabrica un ID). Solo lectura/cómputo — NUNCA
    reescribe YAML histórico. Se usa tanto para canonicalizar escrituras
    NUEVAS (ver `_save_module_feedback`/`_save_section_feedback`) como para
    normalizar en lectura cualquier registro histórico."""
    raw = (raw_module or "").strip()
    if not raw:
        return raw, raw
    resolved = module_registry.resolve(raw)
    return raw, (resolved.module_id if resolved else raw)


# ── Feedback general de módulo (cierre de módulo) ──────────────────────────

def _save_module_feedback(request: Request, document, selected: str, rating: str, comment: str = "") -> None:
    # AUTH HARDENING (owner: "una reacción válida del Hub requiere un usuario
    # autenticado") — MISMO criterio que la página GET del módulo
    # (webapp/routers/modules.py, `if not user: return nav.login_redirect`)
    # y que las páginas GET de catálogo (webapp/main.py, `raise
    # HTTPException(401, "Sin sesión")`), reusado tal cual acá. Chequea ANTES
    # de cualquier try/except de abajo a propósito: `HTTPException` es una
    # `Exception` como cualquier otra — levantarla DENTRO de esos bloques
    # fail-soft la tragaría silenciosamente y renderizaría un falso éxito
    # ("Gracias") sin haber persistido nada. Ya NUNCA se corrige a `{}`
    # silenciosamente — antes esa coerción era exactamente lo que dejaba
    # pasar una reacción anónima (ver AUDIT "REACTIONS — TRACE MISSING USER
    # IDENTITY").
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        # MODULE IDENTIFIER CONVENTION V2 — Fase 2: escrituras NUEVAS
        # persisten el `module_id` canónico (mismo criterio que Activity) —
        # el `stem` sigue siendo la key de TODO lo demás (ruta, `load_module`,
        # etc., sin cambios); YAML histórico existente nunca se reescribe.
        _, _canonical_module = normalize_feedback_module(document.summary.stem)
        payload = {
            "type": "module_content",
            "module": _canonical_module,
            "module_title": document.summary.title,
            "pack": selected,
            "rating": rating,
            # IDENTITY FIELD CORRECTIVE (found while auth-hardening this
            # router): `auth_service.create_session`/`decode_session` NEVER
            # populate `username`/`email` -- the ONLY identity key the
            # session dict ever carries is `sub` (ver auth_service.py:224-
            # 232). Sin `.get("sub")` acá, esta línea escribía SIEMPRE ""
            # -- incluso con sesión válida -- desde el origen de esta
            # feature; MISMA cadena de 3 keys que ya usan reporting.py/
            # staffing.py/commercial.py/academy.py (nunca inventada acá).
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
            "ts": now.isoformat(),
        }
        clean_comment = (comment or "").strip()
        if clean_comment:
            payload["comment"] = clean_comment
        path = FEEDBACK_LOG_DIR / f"module_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    except Exception:
        # El feedback no debe romper la navegacion ni el contenido del modulo.
        pass
    # USAGE METRICS — REACCIONES (owner: "el copiloto tiene un sinnúmero de
    # reacciones que debe ser tenido en cuenta"): evento del MISMO pipeline
    # de Activity que reporting.feedback/staffing.feedback/commercial.feedback
    # (ver activity_context._USAGE_EVENTS) — `feedback_sentiment` es la ÚNICA
    # señal que "Reacciones" necesita (activity_analytics.build_summary lee
    # CUALQUIER evento con ese campo, nunca una lista hardcodeada de nombres),
    # así que esto entra solo al agregado por área SIN tocar esa función.
    # `area=AREA_METHODOLOGY` (no un área "feedback" nueva): mismo criterio
    # que module_viewed/module_selected — el feedback de un módulo vive en la
    # MISMA área que su navegación. Best-effort, fuera del try/except de
    # arriba a propósito: una falla acá nunca debe reescribir/perder el YAML
    # de feedback que SÍ se guardó (misma disciplina fail-soft que ese try).
    try:
        _, _log_module = normalize_feedback_module(document.summary.stem)
        log_activity(
            "module.feedback", user=user, scope=SCOPE_COPILOT, area=AREA_METHODOLOGY,
            module=_log_module, feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


@router.post("/modules/{stem}/feedback", response_class=HTMLResponse)
def module_feedback(
    request: Request,
    stem: str,
    rating: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = resolve_pack(pack)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating invalido")
    document = load_module(stem, selected)
    if rating == "down":
        return templates.TemplateResponse(
            request=request,
            name="partials/module_feedback.html",
            context={"document": document, "pack": selected, "form": True, "error": ""},
        )
    _save_module_feedback(request, document, selected, "up")
    return templates.TemplateResponse(
        request=request,
        name="partials/module_feedback.html",
        context={"document": document, "pack": selected, "submitted": True, "rating": "up"},
    )


@router.post("/modules/{stem}/feedback/submit", response_class=HTMLResponse)
def module_feedback_submit(
    request: Request,
    stem: str,
    rating: str = Form("down"),
    comment: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = resolve_pack(pack)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating invalido")
    document = load_module(stem, selected)
    if rating == "down" and not comment.strip():
        return templates.TemplateResponse(
            request=request,
            name="partials/module_feedback.html",
            context={
                "document": document,
                "pack": selected,
                "form": True,
                "error": "Contanos brevemente que falto o que mejorarias.",
            },
        )
    _save_module_feedback(request, document, selected, rating, comment)
    return templates.TemplateResponse(
        request=request,
        name="partials/module_feedback.html",
        context={"document": document, "pack": selected, "submitted": True, "rating": rating},
    )


@router.post("/modules/{stem}/feedback/cancel", response_class=HTMLResponse)
def module_feedback_cancel(request: Request, stem: str, pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    document = load_module(stem, selected)
    return templates.TemplateResponse(
        request=request,
        name="partials/module_feedback.html",
        context={"document": document, "pack": selected},
    )


# ── Feedback por sección (micro-botonera al cierre de cada sección) ────────
# Granularidad fina: complementa el feedback general de módulo. Es contenido
# (sin IA/tokens), así que lo ve también el perfil de solo lectura. El comentario
# del 👎 es OPCIONAL. Escribe type=section_content (lo analiza analyze_feedback).

def _section_or_404(document, section_slug: str):
    section = next((s for s in document.sections if s.slug == section_slug), None)
    if section is None:
        raise HTTPException(404, "Sección no encontrada")
    return section


def _section_feedback_ctx(document, section, selected: str, **extra) -> dict:
    return {"stem": document.summary.stem, "section": section, "pack": selected, **extra}


def _save_section_feedback(request: Request, document, section, selected: str, rating: str, comment: str = "") -> None:
    # AUTH HARDENING — MISMO criterio que _save_module_feedback (ver ahí).
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        # MODULE IDENTIFIER CONVENTION V2 — Fase 2: mismo criterio que
        # _save_module_feedback (ver ahí) — persiste el `module_id` canónico.
        _, _canonical_module = normalize_feedback_module(document.summary.stem)
        payload = {
            "type": "section_content",
            "module": _canonical_module,
            "module_title": document.summary.title,
            "section_slug": section.slug,
            "section_title": section.title,
            "pack": selected,
            "rating": rating,
            # IDENTITY FIELD CORRECTIVE — MISMO hallazgo/fix que
            # _save_module_feedback (ver ahí): falta `.get("sub")`.
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
            "ts": now.isoformat(),
        }
        clean_comment = (comment or "").strip()
        if clean_comment:
            payload["comment"] = clean_comment
        path = FEEDBACK_LOG_DIR / f"section_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    except Exception:
        # El feedback no debe romper la navegación ni el contenido del módulo.
        pass
    # USAGE METRICS — REACCIONES: MISMO criterio que _save_module_feedback
    # (ver ahí) — área methodology (el feedback de una sección vive en la
    # MISMA área que la navegación de módulos), best-effort aparte.
    # `section`/`section_title` (owner ticket "REACCIONES — PER-USER
    # REACTED-OBJECT BREAKDOWN"): MISMOS valores `section.slug`/
    # `section.title` ya usados arriba para el YAML — cero lookup nuevo,
    # solo copiarlos también acá para que Reacciones pueda resolver A QUÉ
    # sección concreta reaccionó cada persona (antes solo sabía el módulo).
    try:
        _, _log_module = normalize_feedback_module(document.summary.stem)
        log_activity(
            "section.feedback", user=user, scope=SCOPE_COPILOT, area=AREA_METHODOLOGY,
            module=_log_module, section=section.slug, section_title=section.title,
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


@router.post("/modules/{stem}/sections/{section_slug}/feedback", response_class=HTMLResponse)
def section_feedback(request: Request, stem: str, section_slug: str,
                     rating: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    document = load_module(stem, selected)
    section = _section_or_404(document, section_slug)
    if rating == "down":
        # El 👎 despliega la línea de comentario opcional; todavía no persiste.
        return templates.TemplateResponse(
            request=request, name="partials/section_feedback.html",
            context=_section_feedback_ctx(document, section, selected, form=True),
        )
    _save_section_feedback(request, document, section, selected, "up")
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_section_feedback_ctx(document, section, selected, submitted=True, rating="up"),
    )


@router.post("/modules/{stem}/sections/{section_slug}/feedback/submit", response_class=HTMLResponse)
def section_feedback_submit(request: Request, stem: str, section_slug: str,
                            rating: str = Form("down"), comment: str = Form(""),
                            pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    document = load_module(stem, selected)
    section = _section_or_404(document, section_slug)
    # Comentario opcional: el 👎 se registra siempre (con o sin texto).
    _save_section_feedback(request, document, section, selected, "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_section_feedback_ctx(document, section, selected, submitted=True, rating="down"),
    )


@router.post("/modules/{stem}/sections/{section_slug}/feedback/cancel", response_class=HTMLResponse)
def section_feedback_cancel(request: Request, stem: str, section_slug: str, pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    document = load_module(stem, selected)
    section = _section_or_404(document, section_slug)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_section_feedback_ctx(document, section, selected),
    )


# ── Feedback de cierre de catálogo / Novedades (partial catalog_feedback.html) ──
def _save_catalog_feedback(request: Request, kind: str, ref: str, pack: str, rating: str, comment: str = "") -> None:
    # AUTH HARDENING — MISMO criterio que _save_module_feedback (ver ahí).
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "catalog", "catalog_kind": kind or "", "catalog_ref": ref or "",
            "pack": pack, "rating": rating,
            # IDENTITY FIELD CORRECTIVE — MISMO hallazgo/fix que
            # _save_module_feedback (ver ahí): falta `.get("sub")`.
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""), "ts": now.isoformat(),
        }
        c = (comment or "").strip()
        if c:
            payload["comment"] = c
        path = FEEDBACK_LOG_DIR / f"catalog_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass
    # USAGE METRICS — REACCIONES: MISMO criterio que _save_module_feedback
    # (ver ahí). area=AREA_CATALOG (MISMA área que catalog_view/
    # catalog_regenerate) — sin `module` a propósito, catálogo no tiene esa
    # dimensión (mismo criterio que esos dos eventos, ninguno la lleva).
    # `kind`/`ref` (owner ticket "REACCIONES — PER-USER REACTED-OBJECT
    # BREAKDOWN"): MISMOS nombres de campo que `catalog_view` YA usa
    # (webapp/main.py, log_verbose(..., kind=..., ref=...)) — reusa la
    # convención existente en vez de inventar una nueva, cero lookup nuevo
    # (son parámetros de esta misma función).
    try:
        log_activity(
            "catalog.feedback", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
            kind=kind or "", ref=ref or "",
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


def _catalog_feedback_ctx(kind: str, ref: str, pack: str, fb_eyebrow: str = "", fb_prompt: str = "", **extra) -> dict:
    return {"catalog_kind": kind, "catalog_ref": ref, "pack": pack,
            "fb_eyebrow": fb_eyebrow, "fb_prompt": fb_prompt, **extra}


@router.post("/catalog/feedback", response_class=HTMLResponse, include_in_schema=False)
def catalog_feedback(request: Request, rating: str = Form(""), catalog_kind: str = Form(""),
                     catalog_ref: str = Form(""), fb_eyebrow: str = Form(""), fb_prompt: str = Form(""),
                     pack: str = Form(DEFAULT_PACK)):
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    selected = resolve_pack(pack)
    if rating == "down":   # el 👎 despliega el comentario opcional; todavía no persiste
        return templates.TemplateResponse(
            request=request, name="partials/catalog_feedback.html",
            context=_catalog_feedback_ctx(catalog_kind, catalog_ref, selected, fb_eyebrow, fb_prompt, form=True))
    _save_catalog_feedback(request, catalog_kind, catalog_ref, selected, "up")
    return templates.TemplateResponse(
        request=request, name="partials/catalog_feedback.html",
        context=_catalog_feedback_ctx(catalog_kind, catalog_ref, selected, fb_eyebrow, fb_prompt, submitted=True, rating="up"))


@router.post("/catalog/feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def catalog_feedback_submit(request: Request, comment: str = Form(""), catalog_kind: str = Form(""),
                            catalog_ref: str = Form(""), fb_eyebrow: str = Form(""), fb_prompt: str = Form(""),
                            pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    _save_catalog_feedback(request, catalog_kind, catalog_ref, selected, "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/catalog_feedback.html",
        context=_catalog_feedback_ctx(catalog_kind, catalog_ref, selected, fb_eyebrow, fb_prompt, submitted=True, rating="down"))


@router.post("/catalog/feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def catalog_feedback_cancel(request: Request, catalog_kind: str = Form(""), catalog_ref: str = Form(""),
                            fb_eyebrow: str = Form(""), fb_prompt: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    return templates.TemplateResponse(
        request=request, name="partials/catalog_feedback.html",
        context=_catalog_feedback_ctx(catalog_kind, catalog_ref, resolve_pack(pack), fb_eyebrow, fb_prompt))


# ── Micro-feedback por sección del detalle de catálogo (simil módulos) ──────
def _save_catalog_section_feedback(request: Request, cliente: str, dominio: str, slug: str,
                                   pack: str, rating: str, comment: str = "") -> None:
    # AUTH HARDENING — MISMO criterio que _save_module_feedback (ver ahí).
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "catalog_section", "cliente": cliente, "dominio": dominio, "section_slug": slug,
            "pack": pack, "rating": rating,
            # IDENTITY FIELD CORRECTIVE — MISMO hallazgo/fix que
            # _save_module_feedback (ver ahí): falta `.get("sub")`.
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""), "ts": now.isoformat(),
        }
        c = (comment or "").strip()
        if c:
            payload["comment"] = c
        path = FEEDBACK_LOG_DIR / f"catalog_section_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    except Exception:  # noqa: BLE001
        pass
    # USAGE METRICS — REACCIONES: MISMO criterio que _save_catalog_feedback
    # (ver ahí) — MISMA área (catálogo), sin `module`.
    # `cliente`/`dominio`/`section_slug` (owner ticket "REACCIONES —
    # PER-USER REACTED-OBJECT BREAKDOWN"): MISMOS valores ya usados arriba
    # para el YAML — cero lookup nuevo, solo copiarlos también acá.
    try:
        log_activity(
            "catalog_section.feedback", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
            cliente=cliente or "", dominio=dominio or "", section_slug=slug or "",
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


def _catalog_section_fb_ctx(cliente: str, dominio: str, slug: str, pack: str, fb_prompt: str = "", **extra) -> dict:
    return {"cliente": cliente, "dominio": dominio, "slug": slug, "pack": pack, "fb_prompt": fb_prompt, **extra}


@router.post("/catalog/section-feedback", response_class=HTMLResponse, include_in_schema=False)
def catalog_section_feedback(request: Request, cliente: str = Form(""), dominio: str = Form(""),
                             slug: str = Form(""), rating: str = Form(""), fb_prompt: str = Form(""),
                             pack: str = Form(DEFAULT_PACK)):
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    selected = resolve_pack(pack)
    if rating == "down":
        return templates.TemplateResponse(
            request=request, name="partials/catalog_section_feedback.html",
            context=_catalog_section_fb_ctx(cliente, dominio, slug, selected, fb_prompt, form=True))
    _save_catalog_section_feedback(request, cliente, dominio, slug, selected, "up")
    return templates.TemplateResponse(
        request=request, name="partials/catalog_section_feedback.html",
        context=_catalog_section_fb_ctx(cliente, dominio, slug, selected, fb_prompt, submitted=True, rating="up"))


@router.post("/catalog/section-feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def catalog_section_feedback_submit(request: Request, cliente: str = Form(""), dominio: str = Form(""),
                                    slug: str = Form(""), comment: str = Form(""), fb_prompt: str = Form(""),
                                    pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    _save_catalog_section_feedback(request, cliente, dominio, slug, selected, "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/catalog_section_feedback.html",
        context=_catalog_section_fb_ctx(cliente, dominio, slug, selected, fb_prompt, submitted=True, rating="down"))


@router.post("/catalog/section-feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def catalog_section_feedback_cancel(request: Request, cliente: str = Form(""), dominio: str = Form(""),
                                    slug: str = Form(""), fb_prompt: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    return templates.TemplateResponse(
        request=request, name="partials/catalog_section_feedback.html",
        context=_catalog_section_fb_ctx(cliente, dominio, slug, resolve_pack(pack), fb_prompt))
