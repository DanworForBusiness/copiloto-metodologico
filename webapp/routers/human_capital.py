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

from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import config
from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, workspaces
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_catalog_repository,
    human_capital_indicators, human_capital_service, onboarding_repository, pagination, profile_document_repository, profile_document_service, publicacion_repository,
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
    # Landing "01 Inicio" retirado POR AHORA (owner): el workspace arranca
    # directamente en Candidatos (Base de CVs). La comprobación de acceso corre
    # ANTES del redirect — un usuario no admitido sigue recibiendo 403 (mismo
    # contrato fail-closed que Commercial/Operations), nunca un redirect que
    # esconda la denegación. `landing.html` se conserva para reactivarlo luego.
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    return RedirectResponse(url="/cchh/candidatos", status_code=307)


def _profiles_panel_context(*, query: dict | None = None, page=1, page_size=None) -> dict:
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
    # Paginación estándar CCHH (HANDOFF «CCHH TABLE PAGINATION STANDARD»): dataset → filtros/búsqueda (arriba) → TOTAL filtrado → slice. El contador del panel es `total_profiles`.
    pager = pagination.paginate(profiles, page=page, page_size=page_size, base_url="/cchh/candidatos", params=query or {})
    return {
        "profiles": pager["items"],
        "total_profiles": pager["total"],
        "pager": pager,
        "dataset_has_any_profiles": bool(all_profiles),
        "source_error": source_error,
        "config_incomplete": False,
        "valid_sources": human_capital_catalog_repository.catalog_values("fuente"),
        # Filtro de Seniority gobernado (owner UX parity slice 2, B3): mismas
        # opciones activas del catálogo que ofrece el alta. El template preserva
        # un valor de query fuera de catálogo (legacy) como opción seleccionada
        # para que el filtro siga round-tripeando.
        "valid_seniority": human_capital_catalog_repository.catalog_values("seniority"),
        # Filtro de Marca de potencial gobernado (P2): MISMO criterio que
        # Seniority arriba -- el catálogo `potencial` reemplaza el input de
        # texto libre que tenía antes.
        "valid_potencial": human_capital_catalog_repository.catalog_values("potencial"),
        "query": query or {},
    }


@router.get("/cchh/candidatos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_page(
    request: Request,
    q: str = Query(""), seniority: str = Query(""), source: str = Query(""),
    location: str = Query(""), potential_mark: str = Query(""),
    page: str = Query(""), page_size: str = Query(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    # Plumbing puro (Phase 3.2 §6): arma el dict semántico que el service
    # sabe interpretar — el router no decide qué campos son buscables ni
    # cómo se comparan, eso vive en human_capital_service.filter_profiles.
    query = {"q": q, "seniority": seniority, "source": source, "location": location, "potential_mark": potential_mark}
    context = {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK,
        "user": user,
        "active_page": "perfiles",
        **_profiles_panel_context(query=query, page=page, page_size=page_size),
    }
    # BUG real encontrado con el Visual QA Harness (owner post-#107 audit):
    # este GET nunca distinguía HTMX de navegación directa -- el filtro
    # (`hx-get="/cchh/candidatos" hx-target="#hc-profiles-panel"
    # hx-swap="outerHTML"`) recibía SIEMPRE la página completa (con
    # topbar/nav/hero) de vuelta, y HTMX la insertaba entera dentro del
    # fragmento, anidando el shell del Hub dentro de sí mismo -- 100%
    # reproducible con un click real en "Buscar" desde cero, sin ningún
    # estado previo. MISMO patrón ya establecido en el resto del Hub
    # (`request.headers.get("HX-Request")`, ver p.ej. catalog_flow/
    # catalog_cds en webapp/main.py) -- nunca un mecanismo nuevo.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html", context=context,
        )
    return templates.TemplateResponse(
        request=request, name="human_capital/profiles.html", context=context,
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
        # (CCHH Catalog Foundation §6 / P2) -- MISMO criterio para los tres campos.
        "valid_sources": human_capital_catalog_repository.catalog_values("fuente"),
        "valid_seniority": human_capital_catalog_repository.catalog_values("seniority"),
        "valid_potencial": human_capital_catalog_repository.catalog_values("potencial"),
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
        # Edición migration-safe (§6 / P2): el catálogo activo + si el valor
        # actual quedó FUERA del catálogo (legacy), se preserva marcado y no
        # se agrega al catálogo automáticamente -- MISMO criterio para los
        # tres campos.
        "valid_seniority": _governed_edit_options("seniority", form_values.get("seniority")),
        "seniority_is_legacy": _is_legacy_governed_value("seniority", form_values.get("seniority")),
        "valid_sources": _governed_edit_options("fuente", form_values.get("source")),
        "source_is_legacy": _is_legacy_governed_value("fuente", form_values.get("source")),
        "valid_potencial": _governed_edit_options("potencial", form_values.get("potential_mark")),
        "potencial_is_legacy": _is_legacy_governed_value("potencial", form_values.get("potential_mark")),
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
    if profile is None or profile.get("deleted_at"):
        # Un candidato borrado (lógico) no es editable: se comporta como
        # inexistente (no aparece en listados, no se puede abrir su edición).
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
    # form limpio, pero acá la edición vive en su propia página. Vuelve al MISMO contexto de listado (filtros/página/tamaño) si vino de él (`?return_to=`).
    return _return_redirect(request, "/cchh/candidatos")


@router.post("/cchh/candidatos/{profile_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profile_delete(
    request: Request,
    profile_id: str,
    # Filtros vigentes (hx-include del form de filtros desde el listado): se
    # re-aplican al re-render para que el borrado NO pierda la búsqueda activa.
    q: str = Form(""), seniority: str = Form(""), source: str = Form(""),
    location: str = Form(""), potential_mark: str = Form(""),
    page: str = Form(""), page_size: str = Form(""),
):
    """Borrado LÓGICO del candidato — UNA sola operación de servicio
    (`soft_delete_profile`), compartida por los dos puntos de entrada de UI
    (ícono en el listado vía HTMX, botón en Editar vía POST full-page).
    Documentos preservados. Autorización: MISMA frontera que el resto de
    Capital Humano (`_require_human_capital_session`); APP_OWNER entra por el
    contrato global ya aprobado."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    actor = (user or {}).get("sub") or ""
    delete_error: str | None = None
    try:
        human_capital_service.soft_delete_profile(profile_id, actor)
    except ProfileNotFoundError:
        # Ya no existe (doble submit / borrado concurrente): idempotente, se
        # sigue al listado actualizado sin error -- no es una falla real.
        pass
    except (ProfileConfigIncompleteError, ProfileRemoteOutcomeUnknownError,
            ProfileStorageError, ProfileConcurrentModificationError) as exc:
        delete_error = str(exc)

    if request.headers.get("HX-Request"):
        # Desde el listado: re-render del panel filtrado -> el candidato
        # borrado desaparece, el conteo se recalcula, los filtros se conservan.
        # Si falló, el candidato SIGUE presente (no hay remoción parcial) y se
        # muestra el error.
        query = {"q": q, "seniority": seniority, "source": source, "location": location, "potential_mark": potential_mark}
        context = {"request": request, **_profiles_panel_context(query=query, page=page, page_size=page_size)}
        if delete_error:
            context["delete_error"] = delete_error
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_profiles_panel.html", context=context,
        )

    # Desde Editar (full-page). Éxito -> vuelve al listado. Falla -> re-render
    # de la página de edición con el error, el candidato intacto.
    if delete_error:
        profile = human_capital_service.get_profile(profile_id)
        if profile is not None:
            return templates.TemplateResponse(
                request=request, name="human_capital/profile_edit.html",
                context={
                    "current_workspace": workspaces.HUMAN_CAPITAL,
                    "available_workspaces": workspaces.available_workspaces(user),
                    "pack": DEFAULT_PACK, "user": user, "active_page": "perfiles",
                    **_edit_page_context(profile_id, form_values=profile, expected_revision=profile.get("revision", 1), create_error=delete_error),
                    "documents": profile_document_service.list_documents_for_profile(profile_id),
                    "document_state": None,
                },
            )
    return _return_redirect(request, "/cchh/candidatos")


def _import_page_context(**kwargs) -> dict:
    return {
        "max_upload_mb": profile_import_service.MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_import_rows": profile_import_service.MAX_IMPORT_ROWS,
        "bulk_import_template_url": config.HC_BULK_IMPORT_TEMPLATE_URL,
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
    return _edit_redirect(request, profile_id)


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
    return _edit_redirect(request, profile_id)


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
    return _edit_redirect(request, profile_id)


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
    return _edit_redirect(request, profile_id)


# ---------------------------------------------------------------------------
# Búsquedas (procesos de selección) — listado / alta / edición / detalle /
# pipeline. Router liviano (MISMO criterio que Candidatos): la validación y el
# shaping viven en human_capital_service; la relación con candidatos es por
# `profile_id` (el Banco de CVs sigue siendo el maestro de la persona).
# ---------------------------------------------------------------------------
_BUSQUEDA_SHELL_ERRORS = (
    busqueda_repository.BusquedaConfigIncompleteError,
    busqueda_repository.BusquedaRemoteOutcomeUnknownError,
    busqueda_repository.BusquedaStorageError,
)


_BUSQUEDA_ATENCION_FILTERS = ("todas", "estancadas", "sin_requerimiento", "onboarding_pendiente")


def _busquedas_list_context(atencion: str = "todas") -> dict:
    all_b: list[dict] = []
    source_error: str | None = None
    try:
        all_b = human_capital_service.list_busquedas()
    except busqueda_repository.BusquedaStorageError as exc:
        source_error = str(exc)
    if atencion not in _BUSQUEDA_ATENCION_FILTERS:
        atencion = "todas"

    def _sort(bs):
        return sorted(bs, key=lambda b: (b.get("fecha_apertura") or ""), reverse=True)

    # Cada proceso se enriquece UNA vez con sus señales derivadas (attention) —
    # nunca se duplica en otro contenedor. Activos: se cuentan las señales para
    # los chips y se filtra la grilla por la señal elegida. Cerrados: se enriquecen
    # igual (para el badge "sin requerimiento") pero no llevan filtro de atención.
    # `onboarding_pendiente`: Onboardings de ese proceso con completado == False (señal sobre el MISMO proceso, nunca otro contenedor). Aplica a activos Y
    # cerrados (un proceso cerrado puede tener ingresos con onboarding pendiente); el filtro, como los demás, sólo recorta la grilla de activos.
    pendientes = human_capital_service.onboarding_pending_by_busqueda()
    activas = [human_capital_service.busqueda_with_attention(b, pendientes.get(b["busqueda_id"], 0))
               for b in all_b if b.get("estado") == "activo"]
    cerradas = [human_capital_service.busqueda_with_attention(b, pendientes.get(b["busqueda_id"], 0))
                for b in all_b if b.get("estado") == "cerrado"]
    estancadas_n = sum(1 for b in activas if b["attention"]["estancada"])
    sin_req_n = sum(1 for b in activas if b["attention"]["sin_requerimiento"])
    onb_pend_n = sum(1 for b in activas if b["attention"]["onboarding_pendiente"])

    if atencion == "estancadas":
        activas_view = [b for b in activas if b["attention"]["estancada"]]
    elif atencion == "sin_requerimiento":
        activas_view = [b for b in activas if b["attention"]["sin_requerimiento"]]
    elif atencion == "onboarding_pendiente":
        activas_view = [b for b in activas if b["attention"]["onboarding_pendiente"]]
    else:
        activas_view = activas

    return {
        "activas": _sort(activas_view),
        "cerradas": _sort(cerradas),
        "total_busquedas": len(all_b),
        "total_activas": len(activas),
        "atencion": atencion,
        "atencion_counts": {"todas": len(activas), "estancadas": estancadas_n, "sin_requerimiento": sin_req_n,
                            "onboarding_pendiente": onb_pend_n},
        "source_error": source_error,
    }


def _busqueda_form_context(*, form_values, is_edit, error=None, concurrent_modification=False) -> dict:
    # Área/Departamento y Área de la consultora son GOBERNADAS por catálogo
    # (mismo contrato migration-safe que Seniority/Fuente en el alta de
    # candidatos): el <select> ofrece el catálogo activo, más el valor legacy
    # actual al frente si quedó fuera de catálogo, para que una edición no
    # relacionada siga round-tripeando. `modalidad` NO es catálogo: es un enum
    # de dominio fijo (Híbrido/Remoto), igual que en la app de referencia.
    return {
        "form_values": form_values,
        "is_edit": is_edit,
        "form_error": error,
        "concurrent_modification": concurrent_modification,
        "modalidades": busqueda_repository.MODALIDADES,
        "areas_consultora": _governed_edit_options("area_consultora", form_values.get("area_consultora")),
        "areas_departamento": _governed_edit_options("area", form_values.get("area_departamento")),
        "area_is_legacy": _is_legacy_governed_value("area", form_values.get("area_departamento")),
        "area_consultora_is_legacy": _is_legacy_governed_value("area_consultora", form_values.get("area_consultora")),
    }


def _busqueda_detail_context(busqueda: dict) -> dict:
    view = human_capital_service.busqueda_candidates_view(busqueda)
    board = {etapa: [] for etapa in busqueda_repository.ETAPAS}
    for app in view:
        board.setdefault(app.get("etapa"), []).append(app)
    return {
        "busqueda": busqueda,
        "board": board,
        "etapas": busqueda_repository.ETAPAS,
        "resultados": busqueda_repository.RESULTADOS_FINALES,
        "etapa_final": busqueda_repository.ETAPA_FINAL,
        "total_candidatos": len(view),
        "available_profiles": human_capital_service.profiles_available_for_busqueda(busqueda),
        # "Eliminar" (owner request, diálogo Cerrar proceso): sólo visible para
        # una búsqueda placeholder/dummy -- MISMA regla que valida
        # `delete_busqueda` server-side (única fuente de verdad, ver
        # `busqueda_is_placeholder`), acá sólo decide si el botón se muestra.
        "busqueda_is_deletable": human_capital_service.busqueda_is_placeholder(busqueda),
    }


def _busqueda_shell(request, extra: dict) -> dict:
    user = auth_service.session_user(request)
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "busquedas",
        **extra,
    }


def _busqueda_form_values(**kw) -> dict:
    return {k: v for k, v in kw.items()}


@router.get("/cchh/busquedas", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busquedas_page(request: Request, atencion: str = Query("todas")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    ctx = _busquedas_list_context(atencion)
    # Filtro de atención reactivo (HTMX): sólo se re-renderiza el panel interno de
    # Procesos activos (`#busquedas-activas-panel`), no la página entera — MISMO
    # criterio de fragmento que el listado de Candidatos.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_busquedas_activas_panel.html",
            context={"request": request, **ctx},
        )
    return templates.TemplateResponse(
        request=request, name="human_capital/busquedas.html",
        context=_busqueda_shell(request, ctx),
    )


@router.get("/cchh/busquedas/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_new_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    today = datetime.now().date().isoformat()
    return templates.TemplateResponse(
        request=request, name="human_capital/busqueda_form.html",
        context=_busqueda_shell(request, _busqueda_form_context(
            form_values={"fecha_apertura": today, "pedido_formal_recibido": False}, is_edit=False)),
    )


def _busqueda_form_from_request(nombre_puesto, fecha_apertura, descripcion, area_departamento,
                                modalidad, area_consultora, jd_texto, palabras_clave,
                                pedido_formal_recibido) -> dict:
    return {
        "nombre_puesto": nombre_puesto, "fecha_apertura": fecha_apertura,
        "descripcion": descripcion, "area_departamento": area_departamento,
        "modalidad": modalidad, "area_consultora": area_consultora,
        "jd_texto": jd_texto, "palabras_clave": palabras_clave,
        "pedido_formal_recibido": pedido_formal_recibido.strip().lower() == "on",
    }


@router.post("/cchh/busquedas/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_new_submit(
    request: Request,
    nombre_puesto: str = Form(""), fecha_apertura: str = Form(""), descripcion: str = Form(""),
    area_departamento: str = Form(""), modalidad: str = Form(""), area_consultora: str = Form(""),
    jd_texto: str = Form(""), palabras_clave: str = Form(""), pedido_formal_recibido: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _busqueda_form_from_request(
        nombre_puesto, fecha_apertura, descripcion, area_departamento, modalidad,
        area_consultora, jd_texto, palabras_clave, pedido_formal_recibido)
    try:
        busqueda = human_capital_service.create_busqueda(form_values)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request, name="human_capital/busqueda_form.html",
            context=_busqueda_shell(request, _busqueda_form_context(
                form_values=form_values, is_edit=False, error=str(exc))))
    except _BUSQUEDA_SHELL_ERRORS as exc:
        return templates.TemplateResponse(
            request=request, name="human_capital/busqueda_form.html",
            context=_busqueda_shell(request, _busqueda_form_context(
                form_values=form_values, is_edit=False, error=str(exc))))
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda['busqueda_id']}", status_code=303)


@router.get("/cchh/busquedas/{busqueda_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_edit_page(request: Request, busqueda_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    form_values = dict(busqueda)
    form_values["expected_revision"] = busqueda.get("revision", 1)
    return templates.TemplateResponse(
        request=request, name="human_capital/busqueda_form.html",
        context=_busqueda_shell(request, _busqueda_form_context(form_values=form_values, is_edit=True)),
    )


@router.post("/cchh/busquedas/{busqueda_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_edit_submit(
    request: Request, busqueda_id: str,
    nombre_puesto: str = Form(""), fecha_apertura: str = Form(""), descripcion: str = Form(""),
    area_departamento: str = Form(""), modalidad: str = Form(""), area_consultora: str = Form(""),
    jd_texto: str = Form(""), palabras_clave: str = Form(""), pedido_formal_recibido: str = Form(""),
    expected_revision: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _busqueda_form_from_request(
        nombre_puesto, fecha_apertura, descripcion, area_departamento, modalidad,
        area_consultora, jd_texto, palabras_clave, pedido_formal_recibido)
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    def _render(**extra):
        fv = dict(form_values); fv["expected_revision"] = revision_int
        return templates.TemplateResponse(
            request=request, name="human_capital/busqueda_form.html",
            context=_busqueda_shell(request, _busqueda_form_context(
                form_values=fv, is_edit=True, **extra)))

    try:
        human_capital_service.update_busqueda(busqueda_id, revision_int, form_values)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except busqueda_repository.BusquedaConcurrentModificationError:
        return _render(concurrent_modification=True)
    except _BUSQUEDA_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.get("/cchh/busquedas/{busqueda_id}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_detail_page(request: Request, busqueda_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    return templates.TemplateResponse(
        request=request, name="human_capital/busqueda_detail.html",
        context=_busqueda_shell(request, _busqueda_detail_context(busqueda)),
    )


def _busqueda_detail_body(request, busqueda_id: str):
    """Fragmento del cuerpo del detalle (pipeline + agregar candidato), usado
    para el swap HTMX tras cada mutación de pipeline."""
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_busqueda_detail_body.html",
        context={"request": request, **_busqueda_detail_context(busqueda)},
    )


@router.post("/cchh/busquedas/{busqueda_id}/candidatos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_add_candidate(request: Request, busqueda_id: str, profile_id: str = Form("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.add_candidate_to_busqueda(busqueda_id, profile_id)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except (ValueError, *_BUSQUEDA_SHELL_ERRORS):
        pass  # el body se re-renderiza igual; un fallo de durabilidad no rompe la vista
    if request.headers.get("HX-Request"):
        return _busqueda_detail_body(request, busqueda_id)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/etapa",
             response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_move_stage(
    request: Request, busqueda_id: str, application_id: str,
    etapa: str = Form(""), resultado_final: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.move_application_stage(busqueda_id, application_id, etapa, resultado_final)
    except (busqueda_repository.BusquedaNotFoundError, busqueda_repository.ApplicationNotFoundError):
        raise HTTPException(404, "No encontrado") from None
    except (ValueError, *_BUSQUEDA_SHELL_ERRORS):
        pass
    if request.headers.get("HX-Request"):
        return _busqueda_detail_body(request, busqueda_id)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/eliminar",
             response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_remove_candidate(request: Request, busqueda_id: str, application_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.remove_application(busqueda_id, application_id)
    except (busqueda_repository.BusquedaNotFoundError, busqueda_repository.ApplicationNotFoundError):
        pass
    except _BUSQUEDA_SHELL_ERRORS:
        pass
    if request.headers.get("HX-Request"):
        return _busqueda_detail_body(request, busqueda_id)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/cerrar", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_busqueda_close(request: Request, busqueda_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form = await request.form()
    finalize_ids = form.getlist("finalize_application_id")
    try:
        human_capital_service.close_busqueda(busqueda_id, finalize_ids)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except _BUSQUEDA_SHELL_ERRORS:
        pass
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_delete(request: Request, busqueda_id: str):
    # Hard delete (owner request, diálogo "Cerrar proceso": "para que ni
    # aparezca como cerrado") -- SÓLO permitido para una búsqueda placeholder
    # (`human_capital_service.busqueda_is_placeholder`, defensa en profundidad:
    # el template ya oculta este botón si no aplica). Un `ValueError` acá es
    # el caso de carrera (alguien agregó un candidato/entrevista entre que se
    # abrió el diálogo y este submit) -- se ignora igual que el resto de
    # fallos de esta ruta (`remove_candidate`, arriba): la búsqueda
    # simplemente sigue existiendo, nunca un 500.
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.delete_busqueda(busqueda_id)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except (ValueError, *_BUSQUEDA_SHELL_ERRORS):
        return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)
    return RedirectResponse(url="/cchh/busquedas", status_code=303)


# ---------------------------------------------------------------------------
# Entrevistas — listado / alta manual / edición / detalle / "no se concretó" /
# eliminar. Dominio verbatim de la referencia (app-seleccion,
# supabase/schema.sql tabla `entrevistas` + lib/entrevistaAutomatica.ts — ver
# entrevista_repository.py para las citas exactas): registro FLAT (no un
# lifecycle con múltiples estados), se autocompleta al mover una postulación
# de Búsquedas a una etapa de entrevista (ver
# human_capital_service.move_application_stage), o se carga manualmente para
# un candidato del Banco que todavía no está en ninguna Búsqueda. Router
# liviano, MISMO criterio que Búsquedas: sin filesystem/Spaces/UUID/JSON acá.
# ---------------------------------------------------------------------------
_ENTREVISTA_SHELL_ERRORS = (
    entrevista_repository.EntrevistaConfigIncompleteError,
    entrevista_repository.EntrevistaRemoteOutcomeUnknownError,
    entrevista_repository.EntrevistaStorageError,
)


def _entrevistas_shell(request, extra: dict) -> dict:
    user = auth_service.session_user(request)
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "entrevistas",
        **extra,
    }


def _entrevistas_list_context(origen: str = "", puesto: str = "", area: str = "", q: str = "", page=1, page_size=None) -> dict:
    origen, puesto, area, q = (origen or "").strip(), (puesto or "").strip(), (area or "").strip(), (q or "").strip()
    entrevistas: list[dict] = []
    source_error: str | None = None
    try:
        entrevistas = human_capital_service.list_entrevistas_view()
    except entrevista_repository.EntrevistaStorageError as exc:
        source_error = str(exc)
    dataset_has_any = bool(entrevistas)
    # Opciones de los tres desplegables sobre el dataset COMPLETO (no desaparecen al aplicar otros filtros). Origen = Profile.source (taxonomía gobernada `fuente`), Puesto = distintos presentes
    # (no es catálogo), Área = `area_consultora`.
    origen_options = _governed_filter_options("fuente", [e.get("origen") for e in entrevistas], origen, human_capital_service.ENTREVISTA_SIN_ORIGEN, "Sin origen")
    puesto_options = _governed_filter_options(None, [e.get("puesto_mostrado") for e in entrevistas], puesto, human_capital_service.ENTREVISTA_SIN_PUESTO, "Sin puesto")
    area_options = _governed_filter_options("area_consultora", [e.get("area") for e in entrevistas], area, human_capital_service.SIN_AREA, "Sin área")
    entrevistas = human_capital_service.filter_entrevistas(entrevistas, origen=origen, puesto=puesto, area=area, q=q)      # filtros + búsqueda ANTES de paginar
    entrevistas.sort(key=lambda e: e.get("created_at") or "", reverse=True)                                                # orden ANTES de paginar
    pager = pagination.paginate(entrevistas, page=page, page_size=page_size, base_url="/cchh/entrevistas",
                                params={"origen": origen, "puesto": puesto, "area": area, "q": q})
    return {
        "entrevistas": pager["items"],
        "total_entrevistas": pager["total"],                                        # TOTAL FILTRADO (contador del dashboard-section), nunca el tamaño de la página
        "dataset_has_any_entrevista": dataset_has_any,
        "pager": pager,
        "origen": origen, "puesto": puesto, "area": area, "q": q,
        "origen_options": origen_options, "puesto_options": puesto_options, "area_options": area_options,
        "source_error": source_error,
    }


def _entrevista_form_context(*, form_values, is_edit, entrevista=None, error=None,
                              concurrent_modification=False) -> dict:
    # `area`/`modalidad_contratacion` son GOBERNADOS por catálogo — MISMO
    # contrato migration-safe que Búsquedas/Candidatos (valor legacy fuera de
    # catálogo se preserva como opción marcada, nunca se agrega al catálogo).
    # BUG corregido (owner audit, observación B): el campo `area` de
    # Entrevista se valida contra el catálogo `area_consultora`
    # (`human_capital_service._validate_area_consultora_governed`, MISMO
    # criterio que ya prueba test_human_capital_entrevistas.py vía
    # `_seed_area_consultora` -- "qué área consultora del candidato
    # ingresaría", DERIVADO de `Busqueda.area_consultora` cuando la
    # entrevista viene de un proceso, ver
    # test_area_of_auto_created_entrevista_derives_from_busqueda_area_consultora).
    # El chequeo de legacy leía por error el catálogo `area` (8 valores,
    # de "área departamento" -- un campo/catálogo TOTALMENTE DISTINTO,
    # propio de Búsquedas: `area_departamento`) en vez de
    # `area_consultora` -- la mezcla podía marcar "(fuera de catálogo)" a
    # un valor de `area_consultora` genuinamente vigente (o al revés,
    # dejar pasar como "no-legacy" algo que sí estaba fuera de catálogo),
    # sin relación con las opciones reales que ofrece el `<select>`.
    # Ambas líneas usan `area_consultora` ahora, MISMO catálogo para
    # options + legacy-check -- consistente con la validación real del
    # service.
    return {
        "form_values": form_values,
        "is_edit": is_edit,
        "entrevista": entrevista,
        "form_error": error,
        "concurrent_modification": concurrent_modification,
        "areas": _governed_edit_options("area_consultora", form_values.get("area")),
        "area_is_legacy": _is_legacy_governed_value("area_consultora", form_values.get("area")),
        "modalidades_contratacion": _governed_edit_options(
            "modalidad_contratacion", form_values.get("modalidad_contratacion")),
        "modalidad_contratacion_is_legacy": _is_legacy_governed_value(
            "modalidad_contratacion", form_values.get("modalidad_contratacion")),
        "profiles": human_capital_service.list_profiles() if not is_edit else [],
    }


def _entrevista_form_from_request(fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
                                   remuneracion_pretendida, observaciones, resumen_entrevista_tecnica) -> dict:
    return {
        "fecha_entrevista": fecha_entrevista, "puesto_evaluado": puesto_evaluado, "area": area,
        "modalidad_contratacion": modalidad_contratacion,
        "remuneracion_pretendida": remuneracion_pretendida, "observaciones": observaciones,
        "resumen_entrevista_tecnica": resumen_entrevista_tecnica,
    }


@router.get("/cchh/entrevistas", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevistas_page(request: Request, origen: str = Query(""), puesto: str = Query(""), area: str = Query(""), q: str = Query(""),
                                   page: str = Query(""), page_size: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    ctx = _entrevistas_list_context(origen, puesto, area, q, page, page_size)
    # Paginación (HTMX): sólo se re-renderiza el panel (`#hc-entrevistas-panel`), MISMO criterio de fragmento que Candidatos/Onboarding.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_entrevistas_panel.html", context={"request": request, **ctx},
        )
    return templates.TemplateResponse(
        request=request, name="human_capital/entrevistas.html",
        context=_entrevistas_shell(request, ctx),
    )


@router.get("/cchh/entrevistas/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_new_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        request=request, name="human_capital/entrevista_form.html",
        context=_entrevistas_shell(request, _entrevista_form_context(form_values={}, is_edit=False)),
    )


@router.post("/cchh/entrevistas/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_new_submit(
    request: Request, profile_id: str = Form(""),
    fecha_entrevista: str = Form(""), puesto_evaluado: str = Form(""), area: str = Form(""),
    modalidad_contratacion: str = Form(""), remuneracion_pretendida: str = Form(""),
    observaciones: str = Form(""), resumen_entrevista_tecnica: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _entrevista_form_from_request(
        fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
        remuneracion_pretendida, observaciones, resumen_entrevista_tecnica)
    try:
        entrevista = human_capital_service.create_entrevista_manual(profile_id, form_values)
    except (ValueError, *_ENTREVISTA_SHELL_ERRORS) as exc:
        return templates.TemplateResponse(
            request=request, name="human_capital/entrevista_form.html",
            context=_entrevistas_shell(request, _entrevista_form_context(
                form_values=form_values, is_edit=False, error=str(exc))))
    # Sin vista de detalle standalone (owner UX pass 1: "la experiencia de
    # producto no la necesita" -- ver Editar mismo, que ya muestra todos los
    # campos): después de crear, se sigue editando directo.
    return RedirectResponse(url=f"/cchh/entrevistas/{entrevista['entrevista_id']}/editar", status_code=303)


@router.get("/cchh/entrevistas/{entrevista_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_edit_page(request: Request, entrevista_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    entrevista = human_capital_service.get_entrevista(entrevista_id)
    if entrevista is None:
        raise HTTPException(404, "Entrevista no encontrada")
    view = human_capital_service.entrevista_view(entrevista)
    form_values = dict(entrevista)
    form_values["expected_revision"] = entrevista.get("revision", 1)
    return templates.TemplateResponse(
        request=request, name="human_capital/entrevista_form.html",
        context=_entrevistas_shell(request, _entrevista_form_context(
            form_values=form_values, is_edit=True, entrevista=view)),
    )


@router.post("/cchh/entrevistas/{entrevista_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_edit_submit(
    request: Request, entrevista_id: str,
    fecha_entrevista: str = Form(""), puesto_evaluado: str = Form(""), area: str = Form(""),
    modalidad_contratacion: str = Form(""), remuneracion_pretendida: str = Form(""),
    observaciones: str = Form(""), resumen_entrevista_tecnica: str = Form(""),
    expected_revision: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _entrevista_form_from_request(
        fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
        remuneracion_pretendida, observaciones, resumen_entrevista_tecnica)
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    def _render(**extra):
        fv = dict(form_values); fv["expected_revision"] = revision_int
        entrevista = human_capital_service.get_entrevista(entrevista_id)
        view = human_capital_service.entrevista_view(entrevista) if entrevista else None
        return templates.TemplateResponse(
            request=request, name="human_capital/entrevista_form.html",
            context=_entrevistas_shell(request, _entrevista_form_context(
                form_values=fv, is_edit=True, entrevista=view, **extra)))

    try:
        human_capital_service.update_entrevista(entrevista_id, revision_int, form_values)
    except entrevista_repository.EntrevistaNotFoundError:
        raise HTTPException(404, "Entrevista no encontrada") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except entrevista_repository.EntrevistaConcurrentModificationError:
        return _render(concurrent_modification=True)
    except _ENTREVISTA_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return _return_redirect(request, "/cchh/entrevistas")


@router.post("/cchh/entrevistas/{entrevista_id}/no-concretada", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_no_concretada(
    request: Request, entrevista_id: str,
    fecha_entrevista: str = Form(""), puesto_evaluado: str = Form(""), area: str = Form(""),
    modalidad_contratacion: str = Form(""), remuneracion_pretendida: str = Form(""),
    observaciones: str = Form(""), resumen_entrevista_tecnica: str = Form(""),
    expected_revision: str = Form(""),
):
    # MISMO botón que la referencia (EntrevistaModal.tsx "No se concretó"):
    # guarda lo que esté cargado en el form Y marca concretada=False en la
    # misma acción, en vez de descartar ediciones pendientes.
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _entrevista_form_from_request(
        fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
        remuneracion_pretendida, observaciones, resumen_entrevista_tecnica)
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    def _render(**extra):
        fv = dict(form_values); fv["expected_revision"] = revision_int
        entrevista = human_capital_service.get_entrevista(entrevista_id)
        view = human_capital_service.entrevista_view(entrevista) if entrevista else None
        return templates.TemplateResponse(
            request=request, name="human_capital/entrevista_form.html",
            context=_entrevistas_shell(request, _entrevista_form_context(
                form_values=fv, is_edit=True, entrevista=view, **extra)))

    try:
        updated = human_capital_service.update_entrevista(entrevista_id, revision_int, form_values)
        human_capital_service.marcar_entrevista_no_concretada(entrevista_id, updated["revision"])
    except entrevista_repository.EntrevistaNotFoundError:
        raise HTTPException(404, "Entrevista no encontrada") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except entrevista_repository.EntrevistaConcurrentModificationError:
        return _render(concurrent_modification=True)
    except _ENTREVISTA_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return _return_redirect(request, "/cchh/entrevistas")


@router.post("/cchh/entrevistas/{entrevista_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_delete(request: Request, entrevista_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.delete_entrevista(entrevista_id)
    except entrevista_repository.EntrevistaNotFoundError:
        pass
    except _ENTREVISTA_SHELL_ERRORS:
        pass
    return _return_redirect(request, "/cchh/entrevistas")


# ---------------------------------------------------------------------------
# Onboarding — listado / edición (datos + checklist con guardado inmediato).
# Dominio verbatim de la referencia (app-seleccion, tabla `onboarding`; ver
# onboarding_repository.py): un registro por candidatura que queda Finalizado +
# Ingreso, creado SOLO automáticamente (human_capital_service.
# sync_onboardings_for_busqueda), sin alta manual ni delete. Router liviano,
# MISMO criterio que Entrevistas: sin filesystem/Spaces/UUID/JSON acá.
# ---------------------------------------------------------------------------
_ONBOARDING_SHELL_ERRORS = (
    onboarding_repository.OnboardingConfigIncompleteError,
    onboarding_repository.OnboardingRemoteOutcomeUnknownError,
    onboarding_repository.OnboardingStorageError,
)
_ONBOARDING_ESTADO_FILTERS = ("todos", "pendientes", "completos")


def _onboarding_shell(request, extra: dict) -> dict:
    user = auth_service.session_user(request)
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "onboarding",
        **extra,
    }


def _governed_filter_options(catalog_name: str | None, values: list, selected: str, sentinel: str, sin_label: str) -> list[dict]:
    """Opciones de un filtro de listado SOBRE EL DATASET COMPLETO (no desaparecen al filtrar): el catálogo GOBERNADO `catalog_name` (si lo hay; sin hardcodear), MÁS los valores presentes
    en las filas que ya no están en el catálogo (legacy, marcados «(fuera de catálogo)» SOLO cuando hay catálogo) o el valor seleccionado, MÁS «Sin …» si alguna fila no tiene valor.
    Sin catálogo (p. ej. Puesto) son los valores distintos presentes, en orden estable (case-insensitive)."""
    active = human_capital_catalog_repository.catalog_values(catalog_name) if catalog_name else []
    present = sorted({(v or "").strip() for v in values if (v or "").strip()}, key=lambda x: (x.casefold(), x))
    extra = [v for v in present if v not in active]
    if selected and selected != sentinel and selected not in active and selected not in extra:
        extra.append(selected)
    tag = " (fuera de catálogo)" if catalog_name else ""
    options = [{"value": a, "label": a} for a in active] + [{"value": a, "label": f"{a}{tag}"} for a in extra]
    if any(not (v or "").strip() for v in values) or selected == sentinel:
        options.append({"value": sentinel, "label": sin_label})
    return options


def _onboarding_area_filter_options(rows: list[dict], selected: str) -> list[dict]:
    """Filtro Área de Onboarding: catálogo gobernado `area_consultora` + legacy presentes + «Sin área» (ver `_governed_filter_options`)."""
    return _governed_filter_options("area_consultora", [o.get("area") for o in rows], selected, human_capital_service.SIN_AREA, "Sin área")


def _onboarding_list_context(estado: str = "todos", area: str = "", q: str = "", page=1, page_size=None) -> dict:
    if estado not in _ONBOARDING_ESTADO_FILTERS:
        estado = "todos"
    area, q = (area or "").strip(), (q or "").strip()
    rows: list[dict] = []
    source_error: str | None = None
    try:
        # GET de navegación = SOLO LECTURA: nunca crea/repone onboardings. El backfill de ingresos históricos es un paso operativo explícito y separado
        # (`human_capital_service.reconcile_onboardings`, DEFERRED: Historical onboarding backfill = deferred operational step).
        rows = human_capital_service.list_onboardings_view()
    except (onboarding_repository.OnboardingStorageError, busqueda_repository.BusquedaStorageError) as exc:
        source_error = str(exc)
    dataset_has_any = bool(rows)
    area_options = _onboarding_area_filter_options(rows, area)         # sobre el dataset COMPLETO: las opciones no desaparecen al filtrar
    rows = human_capital_service.filter_onboardings(rows, estado=estado, area=area, q=q)
    rows.sort(key=lambda o: (o.get("fecha_ingreso") or "", o.get("created_at") or ""), reverse=True)      # orden ANTES de paginar
    pager = pagination.paginate(rows, page=page, page_size=page_size, base_url="/cchh/onboarding",
                                params={"estado": estado if estado != "todos" else "", "area": area, "q": q})
    return {
        "onboardings": pager["items"],
        "total_onboardings": pager["total"],                                        # TOTAL FILTRADO (contador del dashboard-section), nunca el tamaño de la página
        "pager": pager,
        "dataset_has_any_onboarding": dataset_has_any,
        "estado": estado,
        "area": area,
        "q": q,
        "area_options": area_options,
        "source_error": source_error,
    }


def _onboarding_form_context(*, form_values, onboarding=None, error=None, concurrent_modification=False,
                             checklist_error=None) -> dict:
    # `area` = taxonomía `area_consultora` (la misma que Entrevista.area y Búsqueda.area_consultora, de donde se hereda): MISMO contrato migration-safe que
    # Entrevistas (opciones del catálogo activo + el valor legacy actual marcado "(fuera de catálogo)").
    return {
        "form_values": form_values,
        "onboarding": onboarding,
        "form_error": error,
        "concurrent_modification": concurrent_modification,
        "checklist_error": checklist_error,
        "areas": _governed_edit_options("area_consultora", form_values.get("area")),
        "area_is_legacy": _is_legacy_governed_value("area_consultora", form_values.get("area")),
    }


@router.get("/cchh/onboarding", response_class=HTMLResponse, include_in_schema=False)
def human_capital_onboarding_page(request: Request, estado: str = Query("todos"), area: str = Query(""), q: str = Query(""),
                                  page: str = Query(""), page_size: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    ctx = _onboarding_list_context(estado, area, q, page, page_size)
    # Filtro reactivo (HTMX): sólo se re-renderiza el panel (`#hc-onboarding-panel`) — MISMO criterio de fragmento que Candidatos.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_onboarding_panel.html",
            context={"request": request, **ctx},
        )
    return templates.TemplateResponse(
        request=request, name="human_capital/onboarding.html",
        context=_onboarding_shell(request, ctx),
    )


def _onboarding_edit_response(request: Request, application_id: str, form_values: dict, **extra):
    onboarding = human_capital_service.get_onboarding(application_id)
    view = human_capital_service.onboarding_view(onboarding) if onboarding else None
    return templates.TemplateResponse(
        request=request, name="human_capital/onboarding_form.html",
        context=_onboarding_shell(request, _onboarding_form_context(
            form_values=form_values, onboarding=view, **extra)),
    )


@router.get("/cchh/onboarding/{application_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_onboarding_edit_page(request: Request, application_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    onboarding = human_capital_service.get_onboarding(application_id)
    if onboarding is None:
        raise HTTPException(404, "Onboarding no encontrado")
    form_values = dict(onboarding)
    form_values["expected_revision"] = onboarding.get("revision", 1)
    return _onboarding_edit_response(request, application_id, form_values)


@router.post("/cchh/onboarding/{application_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_onboarding_edit_submit(
    request: Request, application_id: str,
    fecha_ingreso: str = Form(""), oferta_cerrada: str = Form(""), area: str = Form(""),
    expected_revision: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = {"fecha_ingreso": fecha_ingreso, "oferta_cerrada": oferta_cerrada, "area": area}
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None
    fv = {**form_values, "expected_revision": revision_int, "application_id": application_id}
    try:
        human_capital_service.update_onboarding(application_id, revision_int, form_values)
    except onboarding_repository.OnboardingNotFoundError:
        raise HTTPException(404, "Onboarding no encontrado") from None
    except ValueError as exc:
        return _onboarding_edit_response(request, application_id, fv, error=str(exc))
    except onboarding_repository.OnboardingConcurrentModificationError:
        return _onboarding_edit_response(request, application_id, fv, concurrent_modification=True)
    except _ONBOARDING_SHELL_ERRORS as exc:
        return _onboarding_edit_response(request, application_id, fv, error=str(exc))
    return _return_redirect(request, "/cchh/onboarding")


@router.post("/cchh/onboarding/{application_id}/checklist/{item_key}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_onboarding_toggle_item(request: Request, application_id: str, item_key: str, checked: str = Form("")):
    """Tildado con guardado inmediato (HTMX): devuelve el fragmento del checklist con el estado ya persistido. Un casillero destildado no envía el campo, así
    que `checked == "1"` ⇔ tildado."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    error = None
    try:
        human_capital_service.set_onboarding_item(application_id, item_key, checked == "1")
    except onboarding_repository.OnboardingNotFoundError:
        raise HTTPException(404, "Onboarding no encontrado") from None
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    except _ONBOARDING_SHELL_ERRORS as exc:
        error = str(exc)
    onboarding = human_capital_service.get_onboarding(application_id)
    if onboarding is None:
        raise HTTPException(404, "Onboarding no encontrado")
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_onboarding_checklist.html",
        context={"request": request, "onboarding": human_capital_service.onboarding_view(onboarding), "checklist_error": error},
    )


# ---------------------------------------------------------------------------
# Publicaciones / Avisos — listado (filtros Período · Canal · Buscar, paginado) / alta / edición / eliminar.
# Dominio verbatim de la referencia (app-seleccion, tabla `publicaciones`; ver publicacion_repository.py): registro histórico plano, SIN estados; `posicion` es un SNAPSHOT
# (se copia el puesto de la Búsqueda al elegirla y no se sincroniza después); canal = catálogo gobernado `canal_publicacion`. Router liviano, MISMO criterio que Entrevistas.
# ---------------------------------------------------------------------------
_PUBLICACION_SHELL_ERRORS = (
    publicacion_repository.PublicacionConfigIncompleteError,
    publicacion_repository.PublicacionRemoteOutcomeUnknownError,
    publicacion_repository.PublicacionStorageError,
)


def _publicaciones_shell(request, extra: dict) -> dict:
    user = auth_service.session_user(request)
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "publicaciones",
        **extra,
    }


def _publicaciones_list_context(periodo: str = "", canal: str = "", q: str = "", page=1, page_size=None) -> dict:
    periodo, canal, q = (periodo or "").strip(), (canal or "").strip(), (q or "").strip()
    rows: list[dict] = []
    source_error: str | None = None
    try:
        rows = human_capital_service.list_publicaciones_view()
    except publicacion_repository.PublicacionStorageError as exc:
        source_error = str(exc)
    dataset_has_any = bool(rows)
    # Opciones sobre el dataset COMPLETO (no desaparecen al filtrar). Período = los realmente presentes (cronológico, más reciente primero; NO la ventana del alta);
    # Canal = catálogo gobernado `canal_publicacion` + valores históricos presentes («(fuera de catálogo)»).
    periodos = sorted({r.get("periodo") for r in rows if r.get("periodo")} | ({periodo} if periodo else set()), reverse=True)
    canal_options = _governed_filter_options("canal_publicacion", [r.get("canal") for r in rows], canal, "__sin_canal__", "Sin canal")
    all_rows = rows
    rows = human_capital_service.filter_publicaciones(rows, periodo=periodo, canal=canal, q=q)      # filtros + búsqueda ANTES de paginar
    summary = human_capital_service.publicaciones_summary(rows, all_rows)                          # sobre el filtrado COMPLETO (no la página); «Este trimestre» sobre el dataset entero
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    rows.sort(key=lambda r: r.get("periodo") or "", reverse=True)                                    # período desc, luego alta desc (MISMO orden que la referencia); orden ANTES de paginar
    pager = pagination.paginate(rows, page=page, page_size=page_size, base_url="/cchh/publicaciones", params={"periodo": periodo, "canal": canal, "q": q})
    return {
        "publicaciones": pager["items"],
        "total_publicaciones": pager["total"],                                      # TOTAL FILTRADO (contador del dashboard-section), nunca el tamaño de la página
        "dataset_has_any_publicacion": dataset_has_any,
        "pager": pager,
        "periodo": periodo, "canal": canal, "q": q,
        "periodo_options": periodos, "canal_options": canal_options,
        "summary": summary,
        "source_error": source_error,
    }


def _publicacion_form_context(*, form_values, is_edit, publicacion=None, error=None, concurrent_modification=False) -> dict:
    busquedas = sorted(human_capital_service.list_busquedas(), key=lambda b: b.get("fecha_apertura") or "", reverse=True)
    current_periodo = (form_values.get("periodo") or "").strip()
    window = human_capital_service.periodos_disponibles()
    historical = bool(current_periodo) and current_periodo not in window
    current_bid = (form_values.get("busqueda_id") or "").strip()
    return {
        "form_values": form_values, "is_edit": is_edit, "publicacion": publicacion, "form_error": error, "concurrent_modification": concurrent_modification,
        "busquedas_activas": [b for b in busquedas if b.get("estado") == "activo"],
        "busquedas_cerradas": [b for b in busquedas if b.get("estado") != "activo"],
        "busqueda_missing": bool(current_bid) and not any(b["busqueda_id"] == current_bid for b in busquedas),
        # Canal GOBERNADO (`canal_publicacion`): opciones del catálogo activo + el valor histórico actual (fuera de catálogo) marcado; sin fallback hardcodeado.
        "canales": _governed_edit_options("canal_publicacion", form_values.get("canal")),
        "canal_is_legacy": _is_legacy_governed_value("canal_publicacion", form_values.get("canal")),
        "canal_catalog_empty": not human_capital_catalog_repository.catalog_values("canal_publicacion"),
        # Período: ventana calculada del alta; una edición conserva SELECCIONADO un período histórico fuera de la ventana (history-safe).
        "periodos": ([current_periodo, *window] if historical else window),
        "periodo_is_historical": historical,
    }


def _publicacion_form_from_request(busqueda_id, posicion, canal, cantidad_avisos, periodo, fecha, observacion) -> dict:
    return {"busqueda_id": busqueda_id, "posicion": posicion, "canal": canal, "cantidad_avisos": cantidad_avisos, "periodo": periodo, "fecha": fecha, "observacion": observacion}


@router.get("/cchh/publicaciones", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicaciones_page(request: Request, periodo: str = Query(""), canal: str = Query(""), q: str = Query(""),
                                     page: str = Query(""), page_size: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    ctx = _publicaciones_list_context(periodo, canal, q, page, page_size)
    # Filtros/paginación reactivos (HTMX): sólo se re-renderiza el panel (`#hc-publicaciones-panel`), MISMO criterio de fragmento que Candidatos/Entrevistas/Onboarding.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_publicaciones_panel.html", context={"request": request, **ctx},
        )
    return templates.TemplateResponse(request=request, name="human_capital/publicaciones.html", context=_publicaciones_shell(request, ctx))


@router.get("/cchh/publicaciones/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicacion_new_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = {"periodo": human_capital_service.periodo_actual(), "cantidad_avisos": 1}
    return templates.TemplateResponse(
        request=request, name="human_capital/publicacion_form.html",
        context=_publicaciones_shell(request, _publicacion_form_context(form_values=form_values, is_edit=False)),
    )


@router.post("/cchh/publicaciones/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicacion_new_submit(
    request: Request, busqueda_id: str = Form(""), posicion: str = Form(""), canal: str = Form(""), cantidad_avisos: str = Form(""),
    periodo: str = Form(""), fecha: str = Form(""), observacion: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _publicacion_form_from_request(busqueda_id, posicion, canal, cantidad_avisos, periodo, fecha, observacion)

    def _render(**extra):
        return templates.TemplateResponse(
            request=request, name="human_capital/publicacion_form.html",
            context=_publicaciones_shell(request, _publicacion_form_context(form_values=form_values, is_edit=False, **extra)))
    try:
        human_capital_service.create_publicacion(form_values)
    except ValueError as exc:
        return _render(error=str(exc))
    except _PUBLICACION_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return _return_redirect(request, "/cchh/publicaciones")


@router.get("/cchh/publicaciones/{publicacion_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicacion_edit_page(request: Request, publicacion_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    publicacion = human_capital_service.get_publicacion(publicacion_id)
    if publicacion is None:
        raise HTTPException(404, "Publicación no encontrada")
    form_values = dict(publicacion)
    form_values["expected_revision"] = publicacion.get("revision", 1)
    return templates.TemplateResponse(
        request=request, name="human_capital/publicacion_form.html",
        context=_publicaciones_shell(request, _publicacion_form_context(
            form_values=form_values, is_edit=True, publicacion=human_capital_service.publicacion_view(publicacion))),
    )


@router.post("/cchh/publicaciones/{publicacion_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicacion_edit_submit(
    request: Request, publicacion_id: str, busqueda_id: str = Form(""), posicion: str = Form(""), canal: str = Form(""), cantidad_avisos: str = Form(""),
    periodo: str = Form(""), fecha: str = Form(""), observacion: str = Form(""), expected_revision: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _publicacion_form_from_request(busqueda_id, posicion, canal, cantidad_avisos, periodo, fecha, observacion)
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    def _render(**extra):
        fv = {**form_values, "publicacion_id": publicacion_id, "expected_revision": revision_int}
        current = human_capital_service.get_publicacion(publicacion_id)
        return templates.TemplateResponse(
            request=request, name="human_capital/publicacion_form.html",
            context=_publicaciones_shell(request, _publicacion_form_context(
                form_values=fv, is_edit=True, publicacion=human_capital_service.publicacion_view(current) if current else None, **extra)))
    try:
        human_capital_service.update_publicacion(publicacion_id, revision_int, form_values)
    except publicacion_repository.PublicacionNotFoundError:
        raise HTTPException(404, "Publicación no encontrada") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except publicacion_repository.PublicacionConcurrentModificationError:
        return _render(concurrent_modification=True)
    except _PUBLICACION_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return _return_redirect(request, "/cchh/publicaciones")


@router.post("/cchh/publicaciones/{publicacion_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicacion_delete(request: Request, publicacion_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.delete_publicacion(publicacion_id)
    except publicacion_repository.PublicacionNotFoundError:
        pass
    except _PUBLICACION_SHELL_ERRORS:
        pass
    return _return_redirect(request, "/cchh/publicaciones")


# ---------------------------------------------------------------------------
# Catálogos gobernados CCHH (read-model materializado + refresh token-carrying)
# ---------------------------------------------------------------------------
_MESES_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _friendly_refreshed_at(iso_value: str | None) -> str | None:
    """`meta.refreshed_at` es un ISO 8601 UTC crudo (`datetime.isoformat()`,
    ver human_capital_catalog_repository.build_snapshot) -- owner post-#104
    acceptance (item D: "friendly timestamp", "no raw schema v1 as primary
    user information"): formateado acá, en el límite de presentación, nunca
    en el repositorio (que sigue devolviendo el valor preciso/parseable)."""
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value)
    except ValueError:
        return iso_value
    return f"{dt.day} {_MESES_ES[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')} UTC"


def _fecha_dmy(value: str | None) -> str:
    """Presentación DD-MM-YYYY de una fecha almacenada en ISO (`YYYY-MM-DD`, o un
    datetime ISO del cual se toma la fecha) -- SÓLO presentación, en el límite de
    la vista: la persistencia y los `<input type=date>` siguen en ISO. Registrado
    como filtro Jinja compartido (`| fecha_dmy`) para toda fecha textual visible
    de Búsquedas (apertura/cierre/etc.). Si no parsea, devuelve el valor tal cual
    (nunca rompe el render por un dato inesperado)."""
    if not value:
        return ""
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # `YYYY-MM-DD` puro también lo parsea fromisoformat; este fallback cubre
        # sufijos raros (p.ej. 'Z') sin inventar formato.
        try:
            dt = datetime.fromisoformat(raw[:10])
        except ValueError:
            return raw
    return dt.strftime("%d-%m-%Y")


templates.env.filters["fecha_dmy"] = _fecha_dmy
# Contexto de listado para las páginas de edición (ver `pagination.list_return_context`): `{% set rc = list_return(request, "/cchh/…") %}` → `rc.back_url` / `rc.qs`.
templates.env.globals["list_return"] = lambda request, list_path: pagination.list_return_context(request.query_params, list_path)


def _edit_redirect(request: Request, profile_id: str) -> RedirectResponse:
    """303 a la página de edición del candidato conservando `?return_to=` (Cancelar/Guardar siguen volviendo al listado correcto tras una operación de documentos)."""
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar" + pagination.list_return_context(request.query_params, "/cchh/candidatos")["qs"], status_code=303)


def _return_redirect(request: Request, list_path: str) -> RedirectResponse:
    """303 al listado EN EL MISMO CONTEXTO (filtros/página/tamaño) si la acción vino de él (`?return_to=`); si no, al listado base."""
    return RedirectResponse(url=pagination.list_return_url(request.query_params.get("return_to"), list_path) or list_path, status_code=303)


def _catalogos_context(request: Request, *, refresh_result: dict | None, refresh_error: dict | None) -> dict:
    user = auth_service.session_user(request)
    meta = human_capital_catalog_repository.snapshot_meta()
    if meta:
        meta = {**meta, "refreshed_at": _friendly_refreshed_at(meta.get("refreshed_at"))}
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK,
        "user": user,
        "active_page": "catalogos",
        "meta": meta,
        "governed_catalog_labels": human_capital_catalog_repository.governed_catalog_labels(),
        "refresh_result": refresh_result,
        "refresh_error": refresh_error,
    }


# ── INDICADORES (06) ─────────────────────────────────────────────────────────────────────────────────────
# Paridad con `/metricas` de la app de referencia (spec `_handoffs/CCHH_06_INDICADORES_SPEC.md`). TODO el cálculo vive en `human_capital_indicators` (motor puro, Bloque A): el router solo arma UN snapshot por
# request (lectura de solo lectura), delega y entrega el resultado a las plantillas, que no calculan nada de negocio. Los selectores de período de C6 y C9 son independientes: cada uno recarga SOLO su tarjeta.
_INDICADORES_STORAGE_ERRORS = (
    busqueda_repository.BusquedaStorageError, entrevista_repository.EntrevistaStorageError, publicacion_repository.PublicacionStorageError, ProfileStorageError,
)
_INDICADORES_TARJETAS = {"perfiles": "build_perfiles", "canales": "build_canales"}


def _indicadores_shell(request, extra: dict) -> dict:
    user = auth_service.session_user(request)
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "indicadores",
        **extra,
    }


def _indicadores_facts() -> "human_capital_indicators.IndicatorFacts":
    """UN snapshot consistente por request: lectura única + hechos normalizados (sin consultas por candidatura)."""
    return human_capital_indicators.build_facts(human_capital_indicators.load_snapshot())


@router.get("/cchh/indicadores", response_class=HTMLResponse, include_in_schema=False)
def human_capital_indicadores_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    page: dict | None = None
    source_error: str | None = None
    try:
        page = human_capital_indicators.build_page(_indicadores_facts())
    except _INDICADORES_STORAGE_ERRORS as exc:
        source_error = str(exc)
    return templates.TemplateResponse(request=request, name="human_capital/indicadores.html", context=_indicadores_shell(request, {"page": page, "source_error": source_error}))


@router.get("/cchh/indicadores/tarjetas/{tarjeta}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_indicadores_card(request: Request, tarjeta: str, periodo: str = Query("")):
    """Fragmento HTMX de UNA tarjeta con filtro local (C6 `perfiles`, C9 `canales`). Un período desconocido degrada a «Histórico completo» (lo resuelve el motor). Sin HTMX → la página completa."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    builder = _INDICADORES_TARJETAS.get(tarjeta)
    if builder is None:
        raise HTTPException(404, "Tarjeta de indicadores inexistente")
    if not request.headers.get("HX-Request"):
        return RedirectResponse("/cchh/indicadores", status_code=303)
    data: dict | None = None
    source_error: str | None = None
    try:
        data = getattr(human_capital_indicators, builder)(_indicadores_facts(), periodo)
    except _INDICADORES_STORAGE_ERRORS as exc:
        source_error = str(exc)
    return templates.TemplateResponse(request=request, name=f"partials/human_capital_indicadores_{tarjeta}.html", context={"request": request, tarjeta: data, "source_error": source_error})


@router.get("/cchh/catalogos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_catalogos_page(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        request=request, name="human_capital/catalogos.html",
        context=_catalogos_context(request, refresh_result=None, refresh_error=None),
    )


@router.post("/cchh/catalogos/refresh", response_class=HTMLResponse, include_in_schema=False)
def human_capital_catalogos_refresh(request: Request, graph_token: str = Form("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    result = None
    error = None
    # Owner post-#104 acceptance (item E, taxonomía de Graph auth): un token
    # vacío NUNCA debe intentar la llamada a Graph -- es la MISMA acción que
    # un 401 (autorizar y reintentar), pero detectable sin round-trip.
    if not graph_token:
        error = {"kind": "auth_required", "message": "Necesitamos que autorices el acceso a Microsoft para leer el catálogo.", "errors": []}
    else:
        try:
            result = human_capital_catalog_repository.refresh_catalogs(
                graph_token, refreshed_by=(user or {}).get("email", "") if isinstance(user, dict) else "",
            )
            # Owner post-#104 acceptance (item D): "counts/status presented
            # structurally" -- las keys de `counts` son identificadores
            # internos (`GOVERNED_CATALOGS`, snake_case); acá se relabelan a
            # negocio ANTES de llegar al template, MISMO criterio que
            # `governed_catalog_labels`.
            result = {
                **result,
                "counts": {
                    human_capital_catalog_repository.CATALOG_LABELS.get(cat, cat): n
                    for cat, n in result["counts"].items()
                },
            }
        except human_capital_catalog_repository.CatalogWorkbookError as exc:
            error = {"kind": "validation", "message": "El workbook no pasó la validación.", "errors": exc.errors}
        except human_capital_catalog_repository.CatalogConfigIncompleteError as exc:
            error = {"kind": "config_incomplete", "message": str(exc), "errors": []}
        except human_capital_catalog_repository.CatalogAuthRequiredError as exc:
            error = {"kind": "auth_required", "message": str(exc), "errors": []}
        except human_capital_catalog_repository.CatalogForbiddenError as exc:
            error = {"kind": "forbidden", "message": str(exc), "errors": []}
        except human_capital_catalog_repository.CatalogNotFoundError as exc:
            error = {"kind": "not_found", "message": str(exc), "errors": []}
        except human_capital_catalog_repository.CatalogFetchError as exc:
            error = {"kind": "fetch", "message": str(exc), "errors": []}
        except human_capital_catalog_repository.CatalogRemoteOutcomeUnknownError as exc:
            error = {"kind": "remote_outcome_unknown", "message": str(exc), "errors": []}
    # Un click desde el landing de Base de CVs (botón icon-only, HTMX + token
    # auto-inyectado por el hook de hub_auth.js): NO navega -- devuelve SÓLO el
    # fragmento de feedback (MISMO partial que catalogos.html), que se inyecta
    # en /cchh/candidatos. `reauth_href` porque el landing no tiene el handler
    # de reautenticación inline. La página completa de catálogos sigue siendo
    # la respuesta para el submit full-page de catalogos.html.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/human_capital_catalog_refresh_feedback.html",
            context={"refresh_result": result, "refresh_error": error, "reauth_href": "/cchh/catalogos"},
        )
    return templates.TemplateResponse(
        request=request, name="human_capital/catalogos.html",
        context=_catalogos_context(request, refresh_result=result, refresh_error=error),
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
