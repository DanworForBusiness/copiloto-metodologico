"""
human_capital.py — Capital Humano HTTP.

Capital Humano Phase 1 — Hub Walking Skeleton: `GET /cchh` — shell
autenticado, sin dato de negocio, sin Supabase, sin lógica legacy, sin
roles internos (hrbp/hiring_manager, NO implementados) — la MISMA
arquitectura Hub que ya demostraron Operations y Commercial (auth/sesión,
topbar, switcher, sidebar, theme, CSS/JS compartido, loader canónico,
permissions pattern), aplicada a un CUARTO workspace.

Capital Humano Phase 3 — First Profile Slice: `GET/POST /cchh/
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
`GET/POST /cchh/candidatos/{profile_id}/editar`. Router liviano
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

Capital Humano Phase 3.8 — Safe Bulk Profile Import (Preview + Confirm):
TRES rutas nuevas, `GET /cchh/candidatos/importar` +
`POST .../import/preview` + `POST .../import/confirm`. Router liviano
otra vez: parsea el upload (acotado en bytes ANTES de leerlo entero, ver
`profile_import_service.MAX_UPLOAD_BYTES`), delega TODO el parseo/
preview/confirm a `webapp/services/profile_import_service.py`, y solo
traduce sus resultados a contexto de plantilla — nunca decide qué es un
duplicado, nunca firma/verifica tokens, nunca llama a
`profile_repository` directamente. El archivo subido NUNCA se persiste
(ni disco ni Spaces) — vive en memoria durante la única request que lo
procesa. `preview` es de solo lectura (cero writes, ver
`profile_import_service.build_preview`); `confirm` es la ÚNICA de las
tres rutas que efectivamente persiste, y lo hace exclusivamente vía
`human_capital_service.create_profile_for_import` — el MISMO camino de
persistencia que el alta manual.

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

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, workspaces
from webapp.services import (
    auth_service, human_capital_catalog_repository, human_capital_service,
    profile_document_repository, profile_document_service,
    profile_document_validation, profile_import_service,
)
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


@router.get("/cchh", response_class=HTMLResponse, include_in_schema=False)
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


def _profiles_panel_context(*, query: dict | None = None) -> dict:
    """Contexto del fragmento `human_capital_profiles_panel.html` —
    listado + filtros ÚNICAMENTE (Fase de paridad funcional: el alta se
    mudó a su propia página/ruta, `GET/POST /cchh/candidatos/nuevo`,
    MISMO criterio que la app de referencia — `/banco-cvs` es listado,
    `/banco-cvs/nuevo` es alta, nunca la misma superficie). Este contexto
    ya no conoce `error`/`form_values`/`duplicate_advisory`/
    `remote_outcome_unknown`: esas son responsabilidad exclusiva de
    `_new_page_context` ahora.

    `query` (Phase 3.2 §6): dict semántico `{q, seniority, source,
    location, potential_mark}` armado por el caller a partir de los
    `Query(...)` del GET — se filtra en memoria vía
    `human_capital_service.filter_profiles`, SOBRE el mismo resultado de
    `list_profiles()`, nunca con una lectura de disco aparte (Phase 3.2
    §14, sin índice/cache). `dataset_has_any_profiles` distingue "no hay
    NINGÚN Profile" (§7, `Sin resultados` con el copy original) de "hay
    Profiles pero ninguno matchea el filtro actual" (`Sin resultados` con
    copy distinto, NUNCA `No disponible`) — la plantilla decide cuál
    mostrar según cuál de las dos listas está vacía.

    `config_incomplete` queda SIEMPRE False acá: esa clasificación sólo
    puede originarse en un intento de alta fallido (`ProfileConfigIncompleteError`,
    ver `_new_page_context`/el POST de `/new`) — un fallo de
    `list_profiles()` en sí SIEMPRE es SOURCE_ERROR, nunca CONFIG_INCOMPLETE
    (esto ya era cierto antes de esta fase, ver Phase 3.1 §5/§11 — acá
    sólo se vuelve explícito porque ya no hay un `create_error` que
    pudiera sobreescribirlo)."""
    source_error: str | None = None
    all_profiles: list[dict] = []
    try:
        all_profiles = human_capital_service.list_profiles()
    except ProfileStorageError as exc:
        source_error = str(exc)
    profiles = human_capital_service.filter_profiles(all_profiles, query) if query else all_profiles
    return {
        "profiles": profiles,
        "dataset_has_any_profiles": bool(all_profiles),
        "source_error": source_error,
        "config_incomplete": False,
        "valid_sources": human_capital_catalog_repository.catalog_values("fuente"),
        "query": query or {},
    }


@router.get("/cchh/candidatos", response_class=HTMLResponse, include_in_schema=False)
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
            **_profiles_panel_context(query=query),
        },
    )


def _new_page_context(
    *, form_values: dict, error: str | None = None,
    create_error: str | None = None, create_error_config_incomplete: bool = False,
    duplicate_advisory: dict | None = None, remote_outcome_unknown: str | None = None,
) -> dict:
    """Contexto de `human_capital/profile_new.html` (alta, página propia)
    — MISMO idioma de seis fronteras de error que ya usaba el panel
    combinado (Phase 3/3.1/3.2/3.6), sólo que ahora vive en su propia
    plantilla en vez de un fragmento HTMX-swapped: `error` (validación),
    `duplicate_advisory`, `source_error`+`config_incomplete` (Spaces sin
    configurar), `source_error` sin `config_incomplete` (push falló),
    `remote_outcome_unknown` (ni el push inicial ni un reintento explícito
    confirmaron). Nunca dos de estos superpuestos en el mismo render —
    MISMO contrato exacto, ver el POST handler de abajo."""
    return {
        "form_values": form_values,
        "error": error,
        "source_error": create_error,
        "config_incomplete": create_error_config_incomplete,
        "duplicate_advisory": duplicate_advisory,
        "remote_outcome_unknown": remote_outcome_unknown,
        # Alta: SOLO valores del catálogo activo, sin fallback a texto libre
        # (CCHH Catalog Foundation §6) -- MISMO criterio para ambos campos.
        "valid_sources": human_capital_catalog_repository.catalog_values("fuente"),
        "valid_seniority": human_capital_catalog_repository.catalog_values("seniority"),
    }


@router.get("/cchh/candidatos/nuevo", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profile_new_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_new.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "perfiles",
            **_new_page_context(form_values={}),
        },
    )


@router.post("/cchh/candidatos/nuevo", response_class=HTMLResponse, include_in_schema=False)
# Legacy compat (superficie /human-capital/*): alias al MISMO handler, no
# redirect -- preserva método/form/uploads/path params/response semantics.
@router.post("/human-capital/profiles", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_profile_new_submit(
    request: Request,
    first_name: str = Form(""), last_name: str = Form(""), email: str = Form(""),
    phone: str = Form(""), linkedin_url: str = Form(""), location: str = Form(""),
    profile_skills: str = Form(""), seniority: str = Form(""), source: str = Form(""),
    tags: str = Form(""), notes: str = Form(""), potential_rating: str = Form(""),
    potential_mark: str = Form(""), duplicate_confirmed: str = Form(""),
    file: UploadFile | None = File(None), graph_token: str = Form(""),
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
    # Override explícito, server-side, sin estado global/sesión (Phase
    # 3.2 §11) — el propio botón "Cargar de todos modos" de la página
    # envía este campo con valor literal "true"; cualquier otro valor
    # (ausente, "false", basura) se trata como NO confirmado.
    confirmed = duplicate_confirmed.strip().lower() == "true"

    def _render(**context_kwargs):
        return templates.TemplateResponse(
            request=request, name="human_capital/profile_new.html",
            context={
                "current_workspace": workspaces.HUMAN_CAPITAL,
                "available_workspaces": workspaces.available_workspaces(user),
                "pack": DEFAULT_PACK,
                "user": user,
                "active_page": "perfiles",
                **_new_page_context(form_values=form_values, **context_kwargs),
            },
        )

    # Fase de paridad funcional CV-en-alta (ticket "CvUploader"): validar
    # el ARCHIVO antes de cualquier escritura, cuando sea posible (§1 del
    # ticket) — MISMA función pura que `profile_document_service.
    # upload_document` usa como primer paso (`profile_document_validation.
    # validate_upload`), invocada acá TEMPRANO para poder rechazar un
    # archivo inválido sin haber creado el Profile todavía. Confirmado en
    # la app de referencia (BancoCvForm.tsx/lib/actions/banco-cvs.ts:
    # `cv_url: cv_url || null`, sin `required`) que el CV es OPCIONAL en
    # el alta -- nunca se asume, se lee el código fuente.
    file_bytes = b""
    if file is not None and file.filename:
        file_bytes = await file.read(profile_document_validation.MAX_CV_UPLOAD_BYTES + 1)
        try:
            profile_document_validation.validate_upload(file.filename, file_bytes)
        except profile_document_validation.ProfileDocumentValidationError as exc:
            return _render(error=str(exc))

    try:
        profile = human_capital_service.create_profile(form_values, duplicate_confirmed=confirmed)
    except ValueError as exc:
        # Validación: re-muestra la página con el error y los valores ya
        # tipeados preservados — MISMO patrón exacto que
        # wizard.py::tree_feedback_submit con `store.submit_feedback`.
        return _render(error=str(exc))
    except ProfileDuplicateAdvisory as exc:
        # Posible duplicado (Phase 3.2 §9/§22): CUARTA frontera de error,
        # nunca "No disponible"/"feedback-error" — la creación no ocurrió
        # todavía, el form queda con los valores tipeados intactos para
        # que "Cargar de todos modos" pueda reenviarlos tal cual.
        return _render(duplicate_advisory=exc.matched_profile)
    except ProfileConfigIncompleteError as exc:
        # CONFIG_INCOMPLETE (Phase 3.1 §5A): Spaces sin configurar — NUNCA
        # se trata como un fallo transitorio/reintentable. El repository
        # ya garantiza que el archivo local NO sobrevivió este intento
        # (ver profile_repository._cleanup_failed_write) — el form NO se
        # limpia, para no perder lo tipeado, pero el Profile en sí nunca
        # llegó a existir de forma observable.
        return _render(create_error=str(exc), create_error_config_incomplete=True)
    except ProfileRemoteOutcomeUnknownError as exc:
        # Phase 3.6 §7/§12/§13: el push inicial Y un reintento explícito
        # al MISMO profile_id tampoco confirmaron -- QUINTA frontera de
        # error, NUNCA aplanada a "No disponible"/SOURCE_ERROR (afirmaría
        # una certeza de fallo que no existe). El copy debe desalentar un
        # reintento ciego: a diferencia de update(), reintentar acá
        # mintearía un profile_id NUEVO.
        return _render(remote_outcome_unknown=str(exc))
    except ProfileStorageError as exc:
        # SOURCE_ERROR (Phase 3.1 §5B): Spaces configurado pero el push
        # falló — fallo externo/transitorio. MISMA garantía de limpieza
        # que el caso de arriba (ver profile_repository._cleanup_failed_write).
        return _render(create_error=str(exc))

    if not file_bytes:
        # Sin CV seleccionado: MISMO éxito de siempre, vuelve al listado.
        return RedirectResponse(url="/cchh/candidatos", status_code=303)

    # El Profile YA existe de acá en más -- nunca se "deshace" por un
    # fallo del upload (ticket, explícito: "do not delete metadata
    # blindly as compensation"). Cero atomicidad fake entre Spaces y
    # SharePoint: si el documento falla, el estado observable pasa a ser
    # EXACTAMENTE el mismo que si el usuario hubiese subido el archivo
    # desde el panel de Documentos de la página de edición y hubiese
    # fallado ahí -- reuso DIRECTO de `_render_profile_edit_with_documents`
    # (el MISMO helper que usan todas las rutas de documentos), nunca una
    # segunda implementación de upload/estado. El reintento del usuario
    # es, literalmente, el form de Documentos que esa página ya
    # renderiza -- "allow retry using the existing document-upload/
    # reconciliation contract" del ticket, tal cual.
    if not graph_token:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={
                "kind": "GRAPH_PERMISSION_DENIED",
                "message": "El candidato se guardó, pero no pudimos obtener autorización de Microsoft 365 para subir el CV. Volvé a iniciar sesión y subilo desde acá.",
            },
        )
    uploaded_by = _document_uploader_identity(request)
    try:
        profile_document_service.upload_document(
            profile_id=profile["profile_id"], filename=file.filename, data=file_bytes,
            access_token=graph_token, uploaded_by=uploaded_by,
        )
    except profile_document_validation.ProfileDocumentValidationError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "VALIDATION_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentPermissionDeniedError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "GRAPH_PERMISSION_DENIED", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentGraphSourceError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "GRAPH_SOURCE_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentFolderOutcomeUnknownError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "REMOTE_OUTCOME_UNKNOWN", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentUploadRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={
                "kind": "REMOTE_OUTCOME_UNKNOWN", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "upload",
            },
        )
    except profile_document_service.ProfileDocumentCommitRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={
                "kind": "DOCUMENT_METADATA_PERSISTENCE_ERROR", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "commit",
            },
        )
    except profile_document_repository.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_repository.ProfileDocumentIdConflictError as exc:
        return _render_profile_edit_with_documents(
            request, profile["profile_id"], profile,
            document_state={"kind": "DOCUMENT_ID_CONFLICT", "message": str(exc)},
        )
    # Éxito confirmado: Profile Y ProfileDocument ambos persistidos.
    # MISMO criterio que el alta sin CV — vuelve al listado.
    return RedirectResponse(url="/cchh/candidatos", status_code=303)


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
        # Edición migration-safe (§6): el catálogo activo + si el valor actual
        # quedó FUERA del catálogo (legacy), se preserva marcado y no se
        # agrega al catálogo automáticamente -- MISMO criterio para ambos campos.
        "valid_seniority": _governed_edit_options("seniority", form_values.get("seniority")),
        "seniority_is_legacy": _is_legacy_governed_value("seniority", form_values.get("seniority")),
        "valid_sources": _governed_edit_options("fuente", form_values.get("source")),
        "source_is_legacy": _is_legacy_governed_value("fuente", form_values.get("source")),
    }


def _is_legacy_governed_value(catalog_name: str, value: str | None) -> bool:
    v = (value or "").strip()
    return bool(v) and v not in human_capital_catalog_repository.catalog_values(catalog_name)


def _governed_edit_options(catalog_name: str, value: str | None) -> list[str]:
    """Catálogo activo, más el valor legacy actual (si está fuera de catálogo)
    al frente, para que una edición no relacionada pueda re-seleccionarlo. NUNCA
    se persiste esa inclusión en el catálogo — es solo la opción del <select>.
    MISMA función para cualquier catálogo gobernado (Seniority, Fuente)."""
    active = human_capital_catalog_repository.catalog_values(catalog_name)
    v = (value or "").strip()
    if v and v not in active:
        return [v, *active]
    return active


@router.get("/cchh/candidatos/{profile_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profile_edit_page(request: Request, profile_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = human_capital_service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Candidato no encontrado")
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
            "documents": profile_document_service.list_documents_for_profile(profile_id),
            "document_state": None,
        },
    )


@router.post("/cchh/candidatos/{profile_id}/editar", response_class=HTMLResponse, include_in_schema=False)
@router.post("/human-capital/profiles/{profile_id}/edit", response_class=HTMLResponse, include_in_schema=False)  # legacy compat alias
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
        raise HTTPException(404, "Candidato no encontrado") from None
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
    return RedirectResponse(url="/cchh/candidatos", status_code=303)


def _import_page_context(**kwargs) -> dict:
    return {
        "max_upload_mb": profile_import_service.MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_import_rows": profile_import_service.MAX_IMPORT_ROWS,
        **kwargs,
    }


@router.get("/cchh/candidatos/importar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_import_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_import.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "importar",
            **_import_page_context(),
        },
    )


@router.post("/cchh/candidatos/importar/preview", response_class=HTMLResponse, include_in_schema=False)
@router.post("/human-capital/profiles/import/preview", response_class=HTMLResponse, include_in_schema=False)  # legacy compat alias
async def human_capital_profiles_import_preview(request: Request, file: UploadFile | None = File(None)):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    base_context = {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK,
        "user": user,
        "active_page": "importar",
    }
    if file is None or not file.filename:
        return templates.TemplateResponse(
            request=request, name="human_capital/profile_import.html",
            context={**base_context, **_import_page_context(file_error="Elegí un archivo XLSX para continuar.")},
        )
    # Lectura ACOTADA (Phase 3.8 §5): nunca `await file.read()` a secas,
    # que leería un tamaño arbitrario a memoria antes de poder rechazarlo
    # -- se lee como máximo un byte más del límite, lo justo para
    # distinguir "cabe" de "no cabe" sin cargar un upload gigante entero.
    data = await file.read(profile_import_service.MAX_UPLOAD_BYTES + 1)
    if len(data) > profile_import_service.MAX_UPLOAD_BYTES:
        return templates.TemplateResponse(
            request=request, name="human_capital/profile_import.html",
            context={**base_context, **_import_page_context(
                file_error=f"El archivo supera el máximo de {profile_import_service.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )},
        )
    if not data:
        return templates.TemplateResponse(
            request=request, name="human_capital/profile_import.html",
            context={**base_context, **_import_page_context(file_error="El archivo está vacío.")},
        )
    try:
        rows = profile_import_service.parse_workbook(data)
    except profile_import_service.ImportFileError as exc:
        return templates.TemplateResponse(
            request=request, name="human_capital/profile_import.html",
            context={**base_context, **_import_page_context(file_error=str(exc))},
        )
    preview_rows = profile_import_service.build_preview(rows)
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_import_preview.html",
        context={**base_context, "preview_rows": preview_rows},
    )


@router.post("/cchh/candidatos/importar/confirm", response_class=HTMLResponse, include_in_schema=False)
@router.post("/human-capital/profiles/import/confirm", response_class=HTMLResponse, include_in_schema=False)  # legacy compat alias
def human_capital_profiles_import_confirm(
    request: Request,
    all_tokens: list[str] = Form(default=[]),
    include_tokens: list[str] = Form(default=[]),
    invalid_row_count: int = Form(0),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    results = profile_import_service.confirm_rows(all_tokens, include_tokens)
    # `invalid_row_count` viaja como hidden field desde el preview (Phase
    # 3.8 §22) -- las filas inválidas nunca reciben token, así que esta
    # request no tiene otra forma de saber cuántas hubo en el archivo
    # original; el propio conteo no es sensible (un número, no PII) y
    # viaja sin firmar a propósito (es solo un total de resumen, nunca
    # una decisión de persistencia -- alterarlo en el peor caso desvía un
    # número en el resumen, nunca crea/omite un Profile).
    summary = profile_import_service.summarize(
        results, total_rows=len(all_tokens) + invalid_row_count, invalid_count=invalid_row_count,
    )
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_import_result.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "importar",
            "results": results,
            "summary": summary,
        },
    )


# ---------------------------------------------------------------------------
# Capital Humano Phase 4.1 — Profile Documents (SharePoint, primera
# rebanada CV). Router liviano otra vez (MISMO criterio que el resto de
# este archivo): nunca habla Graph, nunca conoce `document_id`/rutas de
# SharePoint, nunca firma/verifica tokens -- todo eso vive en
# `webapp/services/profile_document_service.py`. Este router solo resuelve
# `Request`/valida admisión/traduce las excepciones del service a
# contratos de render, MISMO idioma que el resto de las rutas de este
# archivo (ValueError -> feedback-error, *ConfigIncomplete -> "No
# disponible" CONFIG_INCOMPLETE, etc. -- ver `_render_profile_edit_with_documents`).
#
# Reuso explícito del GET de edición existente (ticket §19): sin páginas
# nuevas -- `human_capital/profile_edit.html` ahora también renderiza la
# lista de documentos + el form de upload (partials/
# human_capital_profile_documents_panel.html). Sin ruta de descarga (§19,
# §18): "Abrir en SharePoint" es un link plano al `web_url` de Graph,
# nunca streameado por el Hub.
# ---------------------------------------------------------------------------

def _render_profile_edit_with_documents(request: Request, profile_id: str, profile: dict, *, document_state: dict) -> HTMLResponse:
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
            "documents": profile_document_service.list_documents_for_profile(profile_id),
            "document_state": document_state,
        },
    )


def _document_uploader_identity(request: Request) -> str:
    # MISMA precedencia que `cache_scope` en `staffing.py::staffing_data`
    # (username -> email -> sub) -- identidad estable de sesión, usada
    # tanto como `uploaded_by` (metadata mostrada al usuario) como
    # `initiated_by`/comparación de reintento (nunca `graph_token`, que es
    # efímero y ajeno a la identidad del Hub).
    user = auth_service.session_user(request) or {}
    return str(user.get("username") or user.get("email") or user.get("sub") or "")


def _profile_or_404(profile_id: str) -> dict:
    profile = human_capital_service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Candidato no encontrado")
    return profile


@router.post("/cchh/candidatos/{profile_id}/documentos", response_class=HTMLResponse, include_in_schema=False)
@router.post("/human-capital/profiles/{profile_id}/documents", response_class=HTMLResponse, include_in_schema=False)  # legacy compat alias
async def human_capital_profile_documents_upload(
    request: Request, profile_id: str,
    file: UploadFile | None = File(None), graph_token: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = _profile_or_404(profile_id)
    if not graph_token:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "GRAPH_PERMISSION_DENIED",
                "message": "No pudimos obtener autorización de Microsoft 365 para subir el documento. Volvé a iniciar sesión.",
            },
        )
    if file is None or not file.filename:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={"kind": "VALIDATION_ERROR", "message": "Elegí un archivo para continuar."},
        )
    data = await file.read(profile_document_validation.MAX_CV_UPLOAD_BYTES + 1)
    uploaded_by = _document_uploader_identity(request)
    try:
        profile_document_service.upload_document(
            profile_id=profile_id, filename=file.filename, data=data, access_token=graph_token,
            uploaded_by=uploaded_by,
        )
    except profile_document_validation.ProfileDocumentValidationError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "VALIDATION_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentPermissionDeniedError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "GRAPH_PERMISSION_DENIED", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentGraphSourceError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "GRAPH_SOURCE_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentFolderOutcomeUnknownError as exc:
        # Fase 4.1.1 corrective: ambigüedad en la CREACIÓN de carpeta, no en
        # el upload -- sin document_id todavía, así que sin token de
        # reintento propio. Reintentar es simplemente reenviar la MISMA
        # acción de upload (ensure_profile_folder es idempotente).
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "REMOTE_OUTCOME_UNKNOWN", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentUploadRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "REMOTE_OUTCOME_UNKNOWN", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "upload",
            },
        )
    except profile_document_service.ProfileDocumentCommitRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "DOCUMENT_METADATA_PERSISTENCE_ERROR", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "commit",
            },
        )
    except profile_document_repository.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_repository.ProfileDocumentIdConflictError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "DOCUMENT_ID_CONFLICT", "message": str(exc)},
        )
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar", status_code=303)


@router.post("/cchh/candidatos/{profile_id}/documentos/retry-upload", response_class=HTMLResponse, include_in_schema=False)
@router.post("/human-capital/profiles/{profile_id}/documents/retry-upload", response_class=HTMLResponse, include_in_schema=False)  # legacy compat alias
async def human_capital_profile_documents_retry_upload(
    request: Request, profile_id: str,
    token: str = Form(""), file: UploadFile | None = File(None), graph_token: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = _profile_or_404(profile_id)
    requesting_user = _document_uploader_identity(request)
    if not graph_token:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "GRAPH_PERMISSION_DENIED",
                "message": "No pudimos obtener autorización de Microsoft 365 para reintentar. Volvé a iniciar sesión.",
            },
        )
    if file is None or not file.filename:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={"kind": "VALIDATION_ERROR", "message": "Elegí el mismo archivo para reintentar."},
        )
    data = await file.read(profile_document_validation.MAX_CV_UPLOAD_BYTES + 1)
    try:
        profile_document_service.retry_upload_document(
            token=token, data=data, filename=file.filename, access_token=graph_token,
            requesting_user_sub=requesting_user,
        )
    except profile_document_service.RetryTokenError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "VALIDATION_ERROR", "message": str(exc)},
        )
    except profile_document_validation.ProfileDocumentValidationError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "VALIDATION_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentRetryFileMismatchError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "RETRY_FILE_MISMATCH", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentPermissionDeniedError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "GRAPH_PERMISSION_DENIED", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentUploadRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "REMOTE_OUTCOME_UNKNOWN", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "upload",
            },
        )
    except profile_document_service.ProfileDocumentCommitRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "DOCUMENT_METADATA_PERSISTENCE_ERROR", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "commit",
            },
        )
    except profile_document_repository.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_repository.ProfileDocumentIdConflictError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "DOCUMENT_ID_CONFLICT", "message": str(exc)},
        )
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar", status_code=303)


@router.post("/cchh/candidatos/{profile_id}/documentos/retry-commit", response_class=HTMLResponse, include_in_schema=False)
@router.post("/human-capital/profiles/{profile_id}/documents/retry-commit", response_class=HTMLResponse, include_in_schema=False)  # legacy compat alias
def human_capital_profile_documents_retry_commit(request: Request, profile_id: str, token: str = Form("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = _profile_or_404(profile_id)
    requesting_user = _document_uploader_identity(request)
    try:
        profile_document_service.retry_commit_document(token=token, requesting_user_sub=requesting_user)
    except profile_document_service.RetryTokenError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "VALIDATION_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentCommitRetryRequired as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "DOCUMENT_METADATA_PERSISTENCE_ERROR", "message": str(exc),
                "retry_token": exc.retry_token, "retry_kind": "commit",
            },
        )
    except profile_document_repository.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_repository.ProfileDocumentIdConflictError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "DOCUMENT_ID_CONFLICT", "message": str(exc)},
        )
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar", status_code=303)


# Delete (Fase 4.1.2, ver HANDOFF): MISMO idioma que upload/retry -- router
# liviano, nunca conoce Graph/site_id/drive_id/item_id, sólo resuelve
# Request/valida admisión/traduce excepciones. `document_id` es un segmento
# de PATH propio (nunca un campo de form) -- la identidad de QUÉ borrar es
# de la URL, nunca de un valor que el body pudiera falsificar para otro
# documento. `ProfileDocumentNotFoundError` -> 404 real (nunca un
# document_state renderizado: no hay nada que reintentar sobre un id que no
# existe o no es de este Profile).
@router.post(
    "/cchh/candidatos/{profile_id}/documentos/{document_id}/delete",
    response_class=HTMLResponse, include_in_schema=False,
)
async def human_capital_profile_documents_delete(
    request: Request, profile_id: str, document_id: str, graph_token: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = _profile_or_404(profile_id)
    if not graph_token:
        return _render_profile_edit_with_documents(
            request, profile_id, profile,
            document_state={
                "kind": "GRAPH_PERMISSION_DENIED",
                "message": "No pudimos obtener autorización de Microsoft 365 para borrar el documento. Volvé a iniciar sesión.",
            },
        )
    try:
        profile_document_service.delete_document(
            profile_id=profile_id, document_id=document_id, access_token=graph_token,
        )
    except profile_document_service.ProfileDocumentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except profile_document_service.ProfileDocumentConfigIncompleteError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "CONFIG_INCOMPLETE", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentDeletePermissionDeniedError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "GRAPH_PERMISSION_DENIED", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentDeleteGraphSourceError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "GRAPH_SOURCE_ERROR", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentDeletePhysicalOutcomeUnknownError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "DELETE_OUTCOME_UNKNOWN", "message": str(exc)},
        )
    except profile_document_service.ProfileDocumentMetadataDeleteOutcomeUnknownError as exc:
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "DELETE_OUTCOME_UNKNOWN", "message": str(exc)},
        )
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar", status_code=303)


# ---------------------------------------------------------------------------
# Catálogos gobernados CCHH (read-model materializado + refresh token-carrying)
# ---------------------------------------------------------------------------
@router.get("/cchh/catalogos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_catalogos_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/catalogos.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "catalogos",
            "meta": human_capital_catalog_repository.snapshot_meta(),
            "governed_catalogs": human_capital_catalog_repository.GOVERNED_CATALOGS,
            "refresh_result": None,
            "refresh_error": None,
        },
    )


@router.post("/cchh/catalogos/refresh", response_class=HTMLResponse, include_in_schema=False)
def human_capital_catalogos_refresh(request: Request, graph_token: str = Form("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    result = None
    error = None
    try:
        result = human_capital_catalog_repository.refresh_catalogs(
            graph_token, refreshed_by=(user or {}).get("email", "") if isinstance(user, dict) else "",
        )
    except human_capital_catalog_repository.CatalogWorkbookError as exc:
        error = {"kind": "validation", "message": "El workbook no pasó la validación.", "errors": exc.errors}
    except human_capital_catalog_repository.CatalogConfigIncompleteError as exc:
        error = {"kind": "config_incomplete", "message": str(exc), "errors": []}
    except human_capital_catalog_repository.CatalogFetchError as exc:
        error = {"kind": "fetch", "message": str(exc), "errors": []}
    except human_capital_catalog_repository.CatalogRemoteOutcomeUnknownError as exc:
        error = {"kind": "remote_outcome_unknown", "message": str(exc), "errors": []}
    return templates.TemplateResponse(
        request=request, name="human_capital/catalogos.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "catalogos",
            "meta": human_capital_catalog_repository.snapshot_meta(),
            "governed_catalogs": human_capital_catalog_repository.GOVERNED_CATALOGS,
            "refresh_result": result,
            "refresh_error": error,
        },
    )


# ---------------------------------------------------------------------------
# Compatibilidad legacy /human-capital/* -> superficie canónica /cchh/*
# ---------------------------------------------------------------------------
# La superficie canónica del workspace es /cchh (ver workspaces.py home_url).
# Estas rutas existen SOLO como compatibility surface para deep-links,
# bookmarks e integraciones que todavía apuntan a /human-capital/*. Los POST
# son aliases al MISMO handler (arriba, segundo decorador). Los GET son
# redirects 308 permanentes al equivalente /cchh, preservando path params.
# Nada interno debe apuntar a estas URLs: son solo puente hacia afuera.


@router.get("/human-capital", include_in_schema=False)
def human_capital_legacy_landing_redirect() -> RedirectResponse:
    return RedirectResponse(url="/cchh", status_code=308)


@router.get("/human-capital/profiles", include_in_schema=False)
def human_capital_legacy_profiles_redirect() -> RedirectResponse:
    return RedirectResponse(url="/cchh/candidatos", status_code=308)


@router.get("/human-capital/profiles/import", include_in_schema=False)
def human_capital_legacy_import_redirect() -> RedirectResponse:
    return RedirectResponse(url="/cchh/candidatos/importar", status_code=308)


@router.get("/human-capital/profiles/{profile_id}/edit", include_in_schema=False)
def human_capital_legacy_edit_redirect(profile_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar", status_code=308)
