"""
human_capital.py — Capital Humano HTTP.

Capital Humano Phase 1 — Hub Walking Skeleton: `GET /human-capital` — shell
autenticado, sin dato de negocio, sin Supabase, sin lógica legacy, sin
roles internos (hrbp/hiring_manager, NO implementados) — la MISMA
arquitectura Hub que ya demostraron Operations y Commercial (auth/sesión,
topbar, switcher, sidebar, theme, CSS/JS compartido, loader canónico,
permissions pattern), aplicada a un CUARTO workspace.

Capital Humano Phase 3 — First Profile Slice: `GET/POST /human-capital/
profiles` — Banco de Perfiles (metadata únicamente, SIN CV/SharePoint,
ver `webapp/services/human_capital_service.py`/
`webapp/services/profile_repository.py`). Router liviano por diseño (§14
del ticket): no conoce filesystem, no conoce Spaces, no genera UUID, no
serializa JSON — solo resuelve `Request`/valida admisión/llama al
service/renderiza. `ValueError` (validación de negocio),
`ProfileConfigIncompleteError` (CONFIG_INCOMPLETE — Spaces sin
configurar) y `ProfileStorageError` (SOURCE_ERROR — Spaces configurado
pero el push falló) son las TRES fronteras de error que el router traduce
a contratos DISTINTOS del Hub (Phase 3.1 §5/§11) — nunca la misma
superficie para dos de ellas. `ProfileConfigIncompleteError` es subclase
de `ProfileStorageError`, así que el `except` MÁS ESPECÍFICO va primero.

Consume `webapp.human_capital_access.is_human_capital_authorized`
(allowlist explícita `HUMAN_CAPITAL_ALLOWED_USERS` del workspace Capital
Humano, INDEPENDIENTE de `OPERATIONS_ALLOWED_USERS`/
`COMMERCIAL_ALLOWED_USERS`) — MISMO patrón que
`webapp/routers/commercial.py` con `webapp.commercial_access` y
`webapp/routers/staffing.py` con `webapp.operations_access` — para LAS TRES
rutas de este router (el shell Y el Banco de Perfiles).

El deep-link no autenticado entra por
`webapp.workspaces.platform_login_redirect` (apunta a `/platform`) — NO
por `webapp.nav.login_redirect` (Copilot-específico): Capital Humano es un
workspace distinto del Copiloto, mismo criterio que Operations/Commercial.

Deliberadamente SIN `log_activity`/module id: `activity_service.
log_activity`'s `module=` es OPCIONAL (ver su firma) — Commercial pasa
`CM01_MODULE_ID` por elección propia, no porque el contrato lo exija. El
namespace `CHxx` de `module_registry.py` queda RESERVADO, sin asignar,
tal como estaba antes de esta fase (ver Phase 1 forensic, "no forzar un
module ID solo por crear el workspace")."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, workspaces
from webapp.services import auth_service, human_capital_service
from webapp.services.human_capital_service import ProfileConfigIncompleteError, ProfileStorageError
from webapp.templating import templates

router = APIRouter()


def _require_human_capital_session(request: Request, *, next_path: str | None = None):
    """Redirect (sin sesión) o 403 (sesión sin autorización de Capital
    Humano) — o None si OK. MISMO patrón que
    `commercial._require_commercial_session`/
    `staffing._require_operations_session`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not human_capital_access.is_human_capital_authorized(request):
        raise HTTPException(403, "No autorizado para Capital Humano")
    return None


@router.get("/human-capital", response_class=HTMLResponse, include_in_schema=False)
def human_capital_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/landing.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            # Capital Humano no tiene concepto de "pack" propio — MISMO
            # criterio que Commercial/Operations: la identidad animada
            # compartida se resuelve contra el pack por defecto.
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "inicio",
        },
    )


def _profiles_panel_context(
    request: Request, *, error: str | None = None, form_values: dict | None = None,
    create_error: str | None = None, create_error_config_incomplete: bool = False,
) -> dict:
    """Contexto compartido del fragmento `human_capital_profiles_panel.html`
    — usado TANTO por el GET (embebido en la página completa) COMO por el
    POST (fragmento solo, para el swap de HTMX) — nunca dos plantillas con
    la misma estructura mantenidas por separado.

    `error`: mensaje de VALIDACIÓN (ValueError) para re-mostrar junto al
    form con los valores ya tipeados preservados — NUNCA se usa para un
    fallo de persistencia (eso es `create_error`/`source_error`, distinto).

    `list_profiles()` SIEMPRE se intenta acá, incluso cuando el caller ya
    tiene un `create_error` propio — son dos fuentes de fallo
    INDEPENDIENTES (un `create()` fallido no implica que `list()` también
    vaya a fallar: `list()`/`get()` solo leen filesystem local, nunca
    dependen de Spaces, ver profile_repository.py) — así el listado sigue
    reflejando el estado real cuando puede. `create_error`, si viene
    dado, tiene prioridad de DISPLAY sobre un eventual fallo propio de
    `list_profiles()` (un solo banner "No disponible" a la vez, nunca dos
    superpuestos).

    `config_incomplete` (Phase 3.1 §5/§11): SOLO puede venir `True` desde
    un `create_error` (`ProfileConfigIncompleteError`, ver el POST
    handler) — un fallo de `list_profiles()` en sí SIEMPRE es SOURCE_ERROR,
    nunca CONFIG_INCOMPLETE. Ambas clasificaciones comparten el MISMO
    eyebrow "No disponible" (MISMO criterio ya usado por
    `commercialForecastPanelBodyHtml`/`commercialActividadesHtml` — un
    solo primitivo visual, el texto/la clasificación en tests es lo que
    distingue, nunca un componente nuevo) pero NUNCA se confunden con
    `Sin resultados` (Phase 3 §16)."""
    source_error: str | None = None
    profiles: list[dict] = []
    try:
        profiles = human_capital_service.list_profiles()
    except ProfileStorageError as exc:
        source_error = str(exc)
    config_incomplete = False
    if create_error is not None:
        source_error = create_error
        config_incomplete = create_error_config_incomplete
    return {
        "profiles": profiles,
        "source_error": source_error,
        "config_incomplete": config_incomplete,
        "error": error,
        "form_values": form_values or {},
        "valid_sources": human_capital_service.VALID_SOURCES,
    }


@router.get("/human-capital/profiles", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/profiles.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "perfiles",
            **_profiles_panel_context(request),
        },
    )


@router.post("/human-capital/profiles", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_create(
    request: Request,
    first_name: str = Form(""), last_name: str = Form(""), email: str = Form(""),
    phone: str = Form(""), linkedin_url: str = Form(""), location: str = Form(""),
    profile_skills: str = Form(""), seniority: str = Form(""), source: str = Form(""),
    tags: str = Form(""), notes: str = Form(""), potential_rating: str = Form(""),
    potential_mark: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = {
        "first_name": first_name, "last_name": last_name, "email": email, "phone": phone,
        "linkedin_url": linkedin_url, "location": location, "profile_skills": profile_skills,
        "seniority": seniority, "source": source, "tags": tags, "notes": notes,
        "potential_rating": potential_rating, "potential_mark": potential_mark,
    }
    try:
        human_capital_service.create_profile(form_values)
    except ValueError as exc:
        # Validación: re-muestra el panel con el error y los valores ya
        # tipeados preservados — MISMO patrón exacto que
        # wizard.py::tree_feedback_submit con `store.submit_feedback`.
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html",
            context=_profiles_panel_context(request, error=str(exc), form_values=form_values),
        )
    except ProfileConfigIncompleteError as exc:
        # CONFIG_INCOMPLETE (Phase 3.1 §5A): Spaces sin configurar — NUNCA
        # se trata como un fallo transitorio/reintentable. El repository
        # ya garantiza que el archivo local NO sobrevivió este intento
        # (ver profile_repository._cleanup_failed_write) — el form NO se
        # limpia, para no perder lo tipeado, pero el Profile en sí nunca
        # llegó a existir de forma observable.
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html",
            context=_profiles_panel_context(
                request, form_values=form_values, create_error=str(exc), create_error_config_incomplete=True,
            ),
        )
    except ProfileStorageError as exc:
        # SOURCE_ERROR (Phase 3.1 §5B): Spaces configurado pero el push
        # falló — fallo externo/transitorio. MISMA garantía de limpieza
        # que el caso de arriba (ver profile_repository._cleanup_failed_write).
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html",
            context=_profiles_panel_context(request, form_values=form_values, create_error=str(exc)),
        )
    # Éxito confirmado (push_path() ya devolvió True) — vuelve al listado
    # con el form limpio (MISMO patrón que devolver el fragmento sin
    # `form_values`, así el <input> vacío queda como default del template).
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_profiles_panel.html",
        context=_profiles_panel_context(request),
    )
