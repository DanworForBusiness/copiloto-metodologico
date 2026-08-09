"""
academy.py — Danwor Academy (feature COPILOT).

Router del área de onboarding "Danwor Academy": la página (`GET /academy/{stem}`)
y sus 3 endpoints de feedback. Extraído de ``webapp/main.py`` (Fase 2E) como
relocación pura — sin rediseño de auth/pack/diagnostic/readonly/feedback.

Feature COPILOT que consume capacidades compartidas desde sus dueños explícitos
(templating/access/nav/content_registry/services + presentación) SIN importar
``webapp.main``. La presentación del cierre/feedback vive en
``webapp.presentation.academy_feedback`` (también la usa /pack-assets) y la
invitación en ``webapp.presentation.invite`` (también la usa la página de módulo).

Persistencia de feedback: `_save_academy_feedback` es feature-local y escribe a
``FEEDBACK_LOG_DIR`` (seam propio del router, patchable en tests). NO comparte el
seam con las otras familias de feedback que siguen en ``main``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import uuid4

import yaml

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from webapp.services import auth_service
from webapp.services.activity_service import log_activity
from webapp.services.content_service import (
    academy_asset_for,
    list_academy,
    list_appendices,
    list_modules,
)
from webapp import access, nav, workspaces
from webapp.presentation import academy_feedback, invite
from webapp.templating import templates
from core.content_registry import (
    DEFAULT_PACK,
    available_content_packs,
    content_pack_label,
    content_pack_short_label,
    resolve_pack,
)
from config import FEEDBACK_LOG_DIR

router = APIRouter()


@router.get("/academy/{stem}", response_class=HTMLResponse, include_in_schema=False)
def academy_page(request: Request, stem: str, pack: str = Query(DEFAULT_PACK)):
    """Danwor Academy: área de onboarding desconectada de la navegación.

    Embebe un HTML de storytelling autónomo (assets/academy/*.html) a pantalla
    completa, sin panel de IA (como el módulo 00). El theme del copiloto viaja al
    iframe por la infra de /pack-assets (data-dw-theme + postMessage)."""
    diagnostic = access.is_diagnostic(request)
    selected = resolve_pack(pack) if diagnostic else DEFAULT_PACK
    user = auth_service.session_user(request)
    if not user:
        return nav.login_redirect(request, selected)

    asset = academy_asset_for(stem, selected)
    if not asset:
        raise HTTPException(404, "Página de Academy no encontrada")
    current = next((item for item in list_academy(selected) if item.stem == stem), None)
    academy_nav = [item for item in list_academy(selected) if not item.nav_hidden]
    log_activity("academy_selected", user=user, page=stem, pack=selected)

    # branding=danwor → colores/marca Danwor; embed=1 → oculta el topbar propio del
    # HTML (el shell del copiloto ya da contexto). El theme lo inyecta pack_asset.
    iframe_src = (
        f"/pack-assets/{selected}/academy/{asset}?"
        f"{urlencode({'branding': 'danwor', 'embed': '1', 'academy_feedback': stem})}"
    )
    packs = [
        {"name": name, "label": content_pack_short_label(name), "description": content_pack_label(name), "home": nav.home_url(name)}
        for name in available_content_packs()
    ]
    return templates.TemplateResponse(
        request=request,
        name="academy.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy_nav,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy_nav},
            "active_group": "academy",
            "active_stem": stem,
            "readonly": access.is_readonly(request),
            "page_title": current.title if current else "Danwor Academy",
            "iframe_src": iframe_src,
            "asset": asset,
            "show_panel": False,
            "diagnostic": diagnostic,
            "is_owner": access.is_owner(request),
            "pack": selected,
            "packs": packs,
            "pack_label": content_pack_short_label(selected),
            "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            "invite_src": invite.invite_src(request, selected),
        },
    )


def _save_academy_feedback(request: Request, stem: str, pack: str, rating: str, comment: str = "") -> None:
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    selected = resolve_pack(pack)
    current = next((item for item in list_academy(selected) if item.stem == stem), None)
    if current is None:
        raise HTTPException(404, "Página de Academy no encontrada")
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "academy_content",
            "academy_page": current.stem,
            "academy_title": current.title,
            "pack": selected,
            "rating": rating,
            "comment": comment.strip(),
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
            "ts": now.isoformat(),
        }
        path = FEEDBACK_LOG_DIR / f"academy_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        # El feedback no debe romper la navegación ni el contenido de Academy.
        pass


@router.post("/academy/{stem}/feedback", response_class=HTMLResponse, include_in_schema=False)
def academy_feedback_react(request: Request, stem: str, rating: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    selected = resolve_pack(pack)
    if rating == "down":
        return HTMLResponse(academy_feedback.feedback_html(stem, selected, form=True))
    _save_academy_feedback(request, stem, selected, "up")
    return HTMLResponse(academy_feedback.feedback_html(stem, selected, submitted=True, rating="up"))


@router.post("/academy/{stem}/feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def academy_feedback_submit(
    request: Request,
    stem: str,
    rating: str = Form("down"),
    comment: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = resolve_pack(pack)
    if rating == "down" and not comment.strip():
        return HTMLResponse(academy_feedback.feedback_html(stem, selected, form=True, error="Contanos brevemente qué faltó."))
    _save_academy_feedback(request, stem, selected, rating, comment)
    return HTMLResponse(academy_feedback.feedback_html(stem, selected, submitted=True, rating=rating))


@router.post("/academy/{stem}/feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def academy_feedback_cancel(stem: str, pack: str = Form(DEFAULT_PACK)):
    return HTMLResponse(academy_feedback.feedback_html(stem, resolve_pack(pack)))
