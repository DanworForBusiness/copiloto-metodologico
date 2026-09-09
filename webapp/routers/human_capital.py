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

Capital Humano Phase 3.2 — Profile Search, Filters & Duplicate Advisory:
MISMAS dos rutas (§23, delta de superficie 0 — search/filtros van por
GET query params, `?q=&seniority=&source=&location=&potential_mark=`,
combinables vía AND). El GET agrega esos cinco `Query(...)` opcionales y
arma un dict `query` — eso es plumbing, NO lógica de filtrado (§6): el
router nunca decide QUÉ campos son buscables ni CÓMO se comparan, eso
vive enteramente en `human_capital_service.filter_profiles`/
`_matches_search_text`/`_matches_structured_filters`. El POST agrega
`ProfileDuplicateAdvisory` como una CUARTA frontera de error — nunca
reutiliza `No disponible`/`feedback-error` (§22) — y un campo de form
`duplicate_confirmed` (§11, override explícito, validado acá mismo con
un simple `.strip().lower() == "true"`, sin estado global/sesión).

Capital Humano Phase 3.5 — Safe Profile Edit (Revision + Compensating
Overwrite): DOS rutas nuevas, MISMO estilo `{recurso}/{id}/acción` ya
establecido en el Hub (`wizard.py`'s `/wizards/{run_id}/answer`,
`assistant.py`'s `/assistant/responses/{reply_id}/feedback`) —
`GET/POST /human-capital/profiles/{profile_id}/edit`. Router liviano
otra vez (§13 del ticket): NUNCA compara `revision`, NUNCA loquea, NUNCA
decide rollback — solo pasa `expected_revision` (un campo hidden
server-issued del form) tal cual a `human_capital_service.update_profile`,
que a su vez delega la comparación/lock/compensación exclusivamente a
`profile_repository.update`. Seis fronteras de error para el form de
edición (validación, posible duplicado -- excluyendo el Profile propio,
config_incomplete, source_error, concurrent_modification,
remote_outcome_unknown — Phase 3.5 §17), cada una a su propio contrato
visual, NUNCA superpuestas. Un `profile_id` inexistente en la URL es un
404 HTTP genuino (`ProfileNotFoundError` → `HTTPException(404)`), NUNCA
uno de los seis estados renderizados dentro del form.

Capital Humano Phase 3.6 — Create In-Doubt Remote Durability: el POST de
ALTA gana una CUARTA frontera de error, `ProfileRemoteOutcomeUnknownError`
(MISMA clase que ya usaba `update()` desde Phase 3.5, mismo significado
semántico: "no podemos probar qué pasó del lado remoto") — nunca
aplanada a `No disponible`/SOURCE_ERROR, con copy que EXPLÍCITAMENTE
desalienta un reintento ciego (Phase 3.6 §13): reintentar el form
mintearía un `profile_id` NUEVO (a diferencia de `update()`, create no
tiene una key existente sobre la cual reintentar desde la UI). Delta de
superficie: 0 rutas nuevas — mismo POST existente.

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

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, workspaces
from webapp.services import auth_service, human_capital_service
from webapp.services.human_capital_service import (
    ProfileConcurrentModificationError,
    ProfileConfigIncompleteError,
    ProfileDuplicateAdvisory,
    ProfileNotFoundError,
    ProfileRemoteOutcomeUnknownError,
    ProfileStorageError,
)
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
    duplicate_advisory: dict | None = None, query: dict | None = None,
    remote_outcome_unknown: str | None = None,
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
    `Sin resultados` (Phase 3 §16).

    `query` (Phase 3.2 §6): dict semántico `{q, seniority, source,
    location, potential_mark}` armado por el caller a partir de los
    `Query(...)` del GET (nunca por el POST, que no tiene concepto de
    filtro propio) — se filtra en memoria vía
    `human_capital_service.filter_profiles`, SOBRE el mismo resultado de
    `list_profiles()` de arriba, nunca con una lectura de disco aparte
    (Phase 3.2 §14, sin índice/cache). `dataset_has_any_profiles`
    distingue "no hay NINGÚN Profile" (§7, `Sin resultados` con el copy
    original) de "hay Profiles pero ninguno matchea el filtro actual"
    (`Sin resultados` con copy distinto, NUNCA `No disponible`) — la
    plantilla decide cuál mostrar según cuál de las dos listas está vacía.

    `duplicate_advisory` (Phase 3.2 §9/§22): el Profile YA existente que
    matcheó como posible duplicado — CUARTA situación semántica, propia,
    nunca superpuesta con `source_error`/`config_incomplete`/`error`
    (un `create()` que dispara advisory nunca llegó a intentar
    persistencia, así que `source_error` queda en lo que sea que
    `list_profiles()` haya dado, independiente).

    `remote_outcome_unknown` (Phase 3.6 §12/§13): el push inicial Y un
    reintento explícito al MISMO `profile_id` tampoco confirmaron —
    QUINTA situación semántica, propia, NUNCA aplanada a `source_error`
    (`ProfileRemoteOutcomeUnknownError` no es un `ProfileStorageError`,
    ver profile_repository.py). El copy debe desalentar explícitamente un
    reintento ciego del form (§13): a diferencia de `update()`, un
    reintento acá mintearía un `profile_id` NUEVO, así que "reintentá" es
    la instrucción INCORRECTA."""
    source_error: str | None = None
    all_profiles: list[dict] = []
    try:
        all_profiles = human_capital_service.list_profiles()
    except ProfileStorageError as exc:
        source_error = str(exc)
    profiles = human_capital_service.filter_profiles(all_profiles, query) if query else all_profiles
    config_incomplete = False
    if create_error is not None:
        source_error = create_error
        config_incomplete = create_error_config_incomplete
    return {
        "profiles": profiles,
        "dataset_has_any_profiles": bool(all_profiles),
        "source_error": source_error,
        "config_incomplete": config_incomplete,
        "duplicate_advisory": duplicate_advisory,
        "remote_outcome_unknown": remote_outcome_unknown,
        "error": error,
        "form_values": form_values or {},
        "valid_sources": human_capital_service.VALID_SOURCES,
        "query": query or {},
    }


@router.get("/human-capital/profiles", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_page(
    request: Request,
    q: str = Query(""), seniority: str = Query(""), source: str = Query(""),
    location: str = Query(""), potential_mark: str = Query(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    # Plumbing puro (Phase 3.2 §6): arma el dict semántico que el service
    # sabe interpretar — el router no decide qué campos son buscables ni
    # cómo se comparan, eso vive en human_capital_service.filter_profiles.
    query = {"q": q, "seniority": seniority, "source": source, "location": location, "potential_mark": potential_mark}
    return templates.TemplateResponse(
        request=request, name="human_capital/profiles.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "perfiles",
            **_profiles_panel_context(request, query=query),
        },
    )


@router.post("/human-capital/profiles", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_create(
    request: Request,
    first_name: str = Form(""), last_name: str = Form(""), email: str = Form(""),
    phone: str = Form(""), linkedin_url: str = Form(""), location: str = Form(""),
    profile_skills: str = Form(""), seniority: str = Form(""), source: str = Form(""),
    tags: str = Form(""), notes: str = Form(""), potential_rating: str = Form(""),
    potential_mark: str = Form(""), duplicate_confirmed: str = Form(""),
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
    # Override explícito, server-side, sin estado global/sesión (Phase
    # 3.2 §11) — el propio botón "Crear de todos modos" del panel envía
    # este campo con valor literal "true"; cualquier otro valor (ausente,
    # "false", basura) se trata como NO confirmado.
    confirmed = duplicate_confirmed.strip().lower() == "true"
    try:
        human_capital_service.create_profile(form_values, duplicate_confirmed=confirmed)
    except ValueError as exc:
        # Validación: re-muestra el panel con el error y los valores ya
        # tipeados preservados — MISMO patrón exacto que
        # wizard.py::tree_feedback_submit con `store.submit_feedback`.
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html",
            context=_profiles_panel_context(request, error=str(exc), form_values=form_values),
        )
    except ProfileDuplicateAdvisory as exc:
        # Posible duplicado (Phase 3.2 §9/§22): CUARTA frontera de error,
        # nunca "No disponible"/"feedback-error" — la creación no ocurrió
        # todavía, el form queda con los valores tipeados intactos para
        # que "Crear de todos modos" pueda reenviarlos tal cual.
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html",
            context=_profiles_panel_context(
                request, form_values=form_values, duplicate_advisory=exc.matched_profile,
            ),
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
    except ProfileRemoteOutcomeUnknownError as exc:
        # Phase 3.6 §7/§12/§13: el push inicial Y un reintento explícito
        # al MISMO profile_id tampoco confirmaron -- QUINTA frontera de
        # error, NUNCA aplanada a "No disponible"/SOURCE_ERROR (afirmaría
        # una certeza de fallo que no existe). El copy debe desalentar un
        # reintento ciego: a diferencia de update(), reintentar acá
        # mintearía un profile_id NUEVO.
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html",
            context=_profiles_panel_context(request, form_values=form_values, remote_outcome_unknown=str(exc)),
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


def _edit_page_context(
    profile_id: str, *, form_values: dict, expected_revision: int, error: str | None = None,
    create_error: str | None = None, create_error_config_incomplete: bool = False,
    duplicate_advisory: dict | None = None, concurrent_modification: bool = False,
    remote_outcome_unknown: str | None = None,
) -> dict:
    """Contexto compartido de `human_capital_profile_edit_form.html` —
    usado TANTO por el GET (carga inicial desde el Profile actual) COMO
    por el POST (re-render con el intento fallido, MISMOS valores
    tipeados), MISMO idioma exacto que `_profiles_panel_context` para
    crear (Phase 3.5, seis fronteras de error, nunca superpuestas — ver
    docstring del módulo):
      - `error` (ValueError) -> `.feedback-error`, MISMO contrato que crear.
      - `duplicate_advisory` -> MISMO `.callout callout--punch`, MISMO
        contrato que crear (excluyendo este `profile_id`, ver
        `human_capital_service.update_profile`).
      - `create_error`/`config_incomplete` -> MISMO `.dashboard-empty`
        "No disponible", MISMO contrato que crear.
      - `concurrent_modification` -> estado PROPIO (Phase 3.5 §16): nunca
        "No disponible", nunca un error de validación genérico — un
        `.callout callout--warn` distinto, con su propio texto explicando
        que el Profile cambió desde que se abrió el form.
      - `remote_outcome_unknown` -> estado PROPIO (Phase 3.5 §10/§11):
        NUNCA se aplana a `source_error` — su propio `.dashboard-empty`
        con `data-error-kind="remote_outcome_unknown"` y un eyebrow
        DISTINTO de "No disponible", para que nunca se lea como el mismo
        fallo genérico de siempre."""
    return {
        "profile_id": profile_id,
        "form_values": form_values,
        "expected_revision": expected_revision,
        "error": error,
        "source_error": create_error,
        "config_incomplete": create_error_config_incomplete,
        "duplicate_advisory": duplicate_advisory,
        "concurrent_modification": concurrent_modification,
        "remote_outcome_unknown": remote_outcome_unknown,
        "valid_sources": human_capital_service.VALID_SOURCES,
    }


@router.get("/human-capital/profiles/{profile_id}/edit", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profile_edit_page(request: Request, profile_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = human_capital_service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile no encontrado")
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_edit.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "perfiles",
            **_edit_page_context(profile_id, form_values=profile, expected_revision=profile.get("revision", 1)),
        },
    )


@router.post("/human-capital/profiles/{profile_id}/edit", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profile_edit_submit(
    request: Request,
    profile_id: str,
    first_name: str = Form(""), last_name: str = Form(""), email: str = Form(""),
    phone: str = Form(""), linkedin_url: str = Form(""), location: str = Form(""),
    profile_skills: str = Form(""), seniority: str = Form(""), source: str = Form(""),
    tags: str = Form(""), notes: str = Form(""), potential_rating: str = Form(""),
    potential_mark: str = Form(""), expected_revision: str = Form(""), duplicate_confirmed: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    form_values = {
        "first_name": first_name, "last_name": last_name, "email": email, "phone": phone,
        "linkedin_url": linkedin_url, "location": location, "profile_skills": profile_skills,
        "seniority": seniority, "source": source, "tags": tags, "notes": notes,
        "potential_rating": potential_rating, "potential_mark": potential_mark,
    }
    try:
        expected_revision_int = int(expected_revision)
    except ValueError:
        # El campo es hidden y server-issued (§15) -- nunca debería llegar
        # ausente/corrupto salvo manipulación directa del form; tratarlo
        # como 400 es correcto acá (no es un estado de negocio, es un
        # request malformado).
        raise HTTPException(400, "expected_revision inválido") from None
    confirmed = duplicate_confirmed.strip().lower() == "true"

    def _render(**context_kwargs):
        return templates.TemplateResponse(
            request=request, name="human_capital/profile_edit.html",
            context={
                "current_workspace": workspaces.HUMAN_CAPITAL,
                "available_workspaces": workspaces.available_workspaces(user),
                "pack": DEFAULT_PACK,
                "user": user,
                "active_page": "perfiles",
                **_edit_page_context(
                    profile_id, form_values=form_values, expected_revision=expected_revision_int, **context_kwargs,
                ),
            },
        )

    try:
        human_capital_service.update_profile(
            profile_id, expected_revision_int, form_values, duplicate_confirmed=confirmed,
        )
    except ProfileNotFoundError:
        raise HTTPException(404, "Profile no encontrado") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except ProfileDuplicateAdvisory as exc:
        return _render(duplicate_advisory=exc.matched_profile)
    except ProfileConcurrentModificationError:
        # Phase 3.5 §16: nunca "No disponible", nunca un error de
        # validación genérico -- estado propio, sin overwrite (el
        # Profile en disco quedó completamente intacto, ver
        # profile_repository.update).
        return _render(concurrent_modification=True)
    except ProfileConfigIncompleteError as exc:
        return _render(create_error=str(exc), create_error_config_incomplete=True)
    except ProfileRemoteOutcomeUnknownError as exc:
        # Phase 3.5 §10: NUNCA se aplana a source_error -- estado propio,
        # local ya restaurado a la versión anterior (ver
        # profile_repository.update), remoto genuinamente desconocido.
        return _render(remote_outcome_unknown=str(exc))
    except ProfileStorageError as exc:
        return _render(create_error=str(exc))
    # Éxito: vuelve al listado -- "guardar" implica "terminé de editar
    # esto", MISMO criterio que un alta exitosa vuelve al panel con el
    # form limpio, pero acá la edición vive en su propia página.
    return RedirectResponse(url="/human-capital/profiles", status_code=303)
