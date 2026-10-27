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

import asyncio
import logging
import threading

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

import urllib.parse
import config
from core.content_registry import DEFAULT_PACK
from webapp import access as app_access
from webapp import human_capital_access, same_origin_guard, workspaces
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_catalog_repository,
    human_capital_indicators, human_capital_service, onboarding_repository, pagination, profile_document_repository, profile_document_service, publicacion_repository,
    profile_document_validation, profile_import_service, sharepoint_graph_diagnostics,
)
from webapp.services import cv_history_acceptance, cv_history_drafts, cv_history_import, cv_history_import_exec, cv_history_inventory, cv_history_manifest, cv_history_pilot, cv_history_review
from webapp.services import human_capital_contact, human_capital_export, human_capital_share, profile_consolidation_service
from webapp.services import scoring_engine, scoring_evaluation_repository, scoring_grid_repository, scoring_service
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


_PROFILE_SORT_COLUMNS = tuple(human_capital_service.PROFILE_SORT_KEYS)


def _profile_sort_state(query: dict, sort, direction, page_size) -> dict:
    """Estado del orden del Banco + la URL de cada cabecera: la MISMA columna invierte la dirección, otra vuelve a ascendente (contrato de las tablas ordenables del Hub). Cada URL conserva búsqueda, filtros y tamaño de
    página, y vuelve a la página 1 (ordenar cambia qué filas caen en cada página)."""
    sort = sort if sort in _PROFILE_SORT_COLUMNS else ""
    direction = "desc" if (sort and direction == "desc") else "asc"
    base = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    if page_size not in (None, ""):
        base["page_size"] = page_size
    urls = {}
    for column in _PROFILE_SORT_COLUMNS:
        next_dir = "desc" if (column == sort and direction == "asc") else "asc"
        urls[column] = "/cchh/candidatos?" + urllib.parse.urlencode({**base, "sort": column, "dir": next_dir})
    return {"key": sort, "dir": direction, "urls": urls, "arrow": {c: ("▲" if direction == "asc" else "▼") if c == sort else "" for c in _PROFILE_SORT_COLUMNS}}


def _profiles_panel_context(*, query: dict | None = None, page=1, page_size=None, sort="", direction="", owner: bool = False) -> dict:
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
    # Orden por columna: sobre el resultado COMPLETO (filtrado), ANTES de paginar — nunca solo la página visible.
    sort_state = _profile_sort_state(query or {}, sort, direction, page_size)
    profiles = human_capital_service.sort_profiles(profiles, sort_state["key"], sort_state["dir"])
    # Paginación estándar CCHH (HANDOFF «CCHH TABLE PAGINATION STANDARD»): dataset → filtros/búsqueda (arriba) → orden → TOTAL filtrado → slice. El contador del panel es `total_profiles`.
    pager = pagination.paginate(profiles, page=page, page_size=page_size, base_url="/cchh/candidatos", params={**(query or {}), "sort": sort_state["key"], "dir": sort_state["dir"] if sort_state["key"] else ""})
    return {
        "sort_state": sort_state,
        "profiles": pager["items"],
        "total_profiles": pager["total"],
        "pager": pager,
        "dataset_has_any_profiles": bool(all_profiles),
        "doc_counts": profile_document_service.count_documents_by_profile(),
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
        # «Consolidar perfiles»: acción EXCLUSIVA del dueño (APP_OWNER). El botón solo se dibuja para él; el servidor lo exige igual en cada ruta (`_require_consolidation_owner`).
        "can_consolidate": owner,
        "consolidation_pending": profile_consolidation_service.pending_count() if owner else 0,
    }


@router.get("/cchh/candidatos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_page(
    request: Request,
    q: str = Query(""), seniority: str = Query(""), source: str = Query(""),
    location: str = Query(""), potential_mark: str = Query(""),
    page: str = Query(""), page_size: str = Query(""), sort: str = Query(""), dir: str = Query(""),
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
        **_profiles_panel_context(query=query, page=page, page_size=page_size, sort=sort, direction=dir, owner=app_access.is_owner(request)),
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
            first_name=profile.get("first_name") or "", last_name=profile.get("last_name") or "",
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
    # Solo con documentos activos: el diálogo contextual ofrece (desmarcado) «Eliminar también los documentos de SharePoint». Sin la casilla, el contrato de la baja NO cambia.
    delete_documents: str = Form(""), graph_token: str = Form(""),
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
    folder_notice = ""
    folder_notice_policy_only = False
    docs_deleted = 0
    if (delete_documents or "").strip().lower() in ("1", "true", "on", "yes"):
        # Flujo CONTROLADO con eliminación física: (1) documentos en SharePoint (cada uno confirmado antes de su metadata) y carpeta solo si quedó vacía; (2) recién entonces la baja lógica.
        # Si algo no se confirma, el candidato NO se da de baja y nunca se informa una eliminación física no confirmada. El reintento es idempotente (solo procesa lo que queda).
        profile = human_capital_service.get_profile(profile_id)
        if profile is not None:
            if not graph_token:
                return _render_profile_edit_with_documents(request, profile_id, profile, document_state={
                    "kind": "GRAPH_PERMISSION_DENIED",
                    "message": "No pudimos obtener autorización de Microsoft 365 para borrar los documentos. El candidato NO fue dado de baja. Volvé a iniciar sesión."})
            try:
                summary = profile_document_service.delete_profile_documents(profile_id=profile_id, access_token=graph_token)
                docs_deleted = summary["deleted"]
                kept_msgs = [m for m in (profile_document_service.folder_kept_message(s) for s in summary["folders"].values()) if m]
                folder_notice = " ".join(dict.fromkeys(kept_msgs))
                folder_notice_policy_only = bool(kept_msgs) and all(s == profile_document_service.FOLDER_KEPT_POLICY for s in summary["folders"].values() if str(s).startswith(profile_document_service.FOLDER_KEPT_PREFIX))
            except profile_document_service.ProfileDocumentConfigIncompleteError as exc:
                return _render_profile_edit_with_documents(request, profile_id, profile, document_state={"kind": "CONFIG_INCOMPLETE", "message": f"{exc} El candidato NO fue dado de baja."})
            except profile_document_service.ProfileDocumentsBulkDeleteIncompleteError as exc:
                kind = {"permission": "GRAPH_PERMISSION_DENIED", "graph": "GRAPH_SOURCE_ERROR"}.get(exc.kind, "DELETE_OUTCOME_UNKNOWN")
                return _render_profile_edit_with_documents(request, profile_id, profile, document_state={
                    "kind": kind,
                    "message": f"{exc} Se eliminaron {exc.deleted} documento(s) y quedan {exc.remaining} sin confirmar. El candidato NO fue dado de baja: podés reintentar."})
            except profile_document_repository.ProfileDocumentStorageError:
                return _render_profile_edit_with_documents(request, profile_id, profile, document_state={
                    "kind": "GRAPH_SOURCE_ERROR", "message": "No pudimos leer los documentos del candidato. No se cambió nada: probá de nuevo en unos minutos."})
    try:
        human_capital_service.soft_delete_profile(profile_id, actor)
    except ProfileNotFoundError:
        # Ya no existe (doble submit / borrado concurrente): idempotente, se
        # sigue al listado actualizado sin error -- no es una falla real.
        pass
    except (ProfileConfigIncompleteError, ProfileRemoteOutcomeUnknownError,
            ProfileStorageError, ProfileConcurrentModificationError) as exc:
        delete_error = str(exc)
        if docs_deleted:
            # Resultado PARCIAL: los documentos ya se eliminaron de SharePoint pero la baja lógica no se confirmó. Se dice tal cual.
            delete_error += f" Atención: sus {docs_deleted} documento(s) YA fueron eliminados de SharePoint."

    if folder_notice and not delete_error:
        # Éxito PARCIAL: baja lógica + documentos eliminados, pero la carpeta se conservó. Se muestra el listado con el aviso (en vez de la redirección silenciosa).
        context = {
            "current_workspace": workspaces.HUMAN_CAPITAL, "available_workspaces": workspaces.available_workspaces(user), "pack": DEFAULT_PACK, "user": user, "active_page": "perfiles",
            **_profiles_panel_context(query={}, page="", page_size="", owner=app_access.is_owner(request)),
            "delete_notice": f"El candidato fue dado de baja y sus {docs_deleted} documento(s) se eliminaron de SharePoint. {folder_notice}",
            "delete_notice_neutral": folder_notice_policy_only,     # carpeta conservada solo por la política de seguridad: aviso informativo, no advertencia
        }
        return templates.TemplateResponse(request=request, name="human_capital/profiles.html", context=context)

    if request.headers.get("HX-Request"):
        # Desde el listado: re-render del panel filtrado -> el candidato
        # borrado desaparece, el conteo se recalcula, los filtros se conservan.
        # Si falló, el candidato SIGUE presente (no hay remoción parcial) y se
        # muestra el error.
        query = {"q": q, "seniority": seniority, "source": source, "location": location, "potential_mark": potential_mark}
        context = {"request": request, **_profiles_panel_context(query=query, page=page, page_size=page_size, owner=app_access.is_owner(request))}
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


@router.get("/cchh/candidatos/{profile_id}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profile_detail_page(request: Request, profile_id: str):
    """FICHA de consulta (solo lectura) de un candidato: la URL ESTABLE que se comparte (nunca `/editar`). Misma autorización que el resto de Capital Humano (sin sesión → login; sin
    autorización CCHH → 403); un candidato dado de baja se comporta como inexistente (404). Reusa el modelo y el listado de documentos existentes: no crea entidades ni lógica nueva,
    no escribe nada. Se declara DESPUÉS de `/cchh/candidatos/nuevo` y `/importar` (rutas fijas del mismo nivel) para no ensombrecerlas. Datos personales ⇒ `no-store`."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    profile = human_capital_service.get_profile(profile_id)
    if profile is None or profile.get("deleted_at"):
        raise HTTPException(404, "Candidato no encontrado")
    documents: list[dict] | None
    try:
        documents = profile_document_service.list_documents_for_profile(profile_id)
    except profile_document_repository.ProfileDocumentStorageError:
        documents = None                                           # la ficha se muestra igual; Documentos dice «No disponible»
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_detail.html",
        context={
            "current_workspace": workspaces.HUMAN_CAPITAL,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            "active_page": "perfiles",
            "profile": profile,
            "documents": documents,
        },
        headers=_NO_STORE,
    )


@router.post("/cchh/candidatos/compartir", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_share_dialog(request: Request, profile_ids: list[str] = Form([])):
    """Contenido del diálogo «Compartir perfiles» (se pide por demanda al accionar, como «Contactar al candidato» del Pipeline). POST con SOLO ids (no viajan por la URL ni quedan en el
    registro de accesos); mismo origen obligatorio (las cookies son SameSite=None). El servidor es la autoridad del límite (``MAX_SHARE_PROFILES``) y resuelve nombres y
    CVs asociados (URL de SharePoint tomada de la metadata ya guardada de cada documento: no se construye ni se infiere ninguna): el navegador no confía en nada que haya guardado. Es una AYUDA para
    redactar: no envía ni registra nada, no incluye datos de contacto, no crea enlaces ni toca permisos."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    try:
        selection = human_capital_share.resolve(profile_ids, human_capital_service.get_profile, profile_document_service.list_documents_for_profile)
    except (ProfileStorageError, profile_document_repository.ProfileDocumentStorageError):
        selection = human_capital_share.ShareSelection(reason=human_capital_share.REASON_SOURCE_ERROR)
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_share_dialog_content.html",
        context={"selection": selection, "subject": human_capital_share.DEFAULT_SUBJECT, "intro": human_capital_share.INTRO, "outro": human_capital_share.OUTRO,
                 "max_profiles": human_capital_share.MAX_SHARE_PROFILES},
        headers=_NO_STORE,
    )


# ───────────────────────── Consolidar perfiles (solo APP_OWNER) ─────────────────────────
def _require_consolidation_owner(request: Request):
    """Sesión de Capital Humano + dueño de la app (APP_OWNER): permiso EXCLUSIVO de la primera versión. Devuelve un redirect (sin sesión) o lanza 403; ``None`` si puede."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not app_access.is_owner(request):
        raise HTTPException(403, "La consolidación de perfiles está reservada al dueño de la aplicación")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    return None


def _consolidation_page(request: Request, **context) -> HTMLResponse:
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="human_capital/profile_consolidation.html",
        context={"current_workspace": workspaces.HUMAN_CAPITAL, "available_workspaces": workspaces.available_workspaces(user), "pack": DEFAULT_PACK, "user": user,
                 "active_page": "perfiles", "labels": profile_consolidation_service.FIELD_LABELS, "svc": profile_consolidation_service, **context},
        headers=_NO_STORE)


def _selected_pair(profile_ids: list[str], primary_id: str) -> tuple[str, str] | None:
    ids = [i for i in dict.fromkeys(p.strip() for p in profile_ids if p and p.strip())]
    if len(ids) != 2:
        return None
    primary = primary_id if primary_id in ids else ids[0]
    return primary, next(i for i in ids if i != primary)


@router.post("/cchh/candidatos/consolidar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_consolidate_plan(request: Request, profile_ids: list[str] = Form([]), primary_id: str = Form("")):
    """Paso 1: comparación, documentos de ambos perfiles, relaciones y bloqueos. Solo lectura. POST con SOLO ids (no viajan por la URL)."""
    gate = _require_consolidation_owner(request)
    if gate is not None:
        return gate
    pair = _selected_pair(profile_ids, primary_id)
    if pair is None:
        return _consolidation_page(request, state="invalid", message="Seleccioná exactamente dos perfiles para consolidar.")
    primary, duplicate = pair
    plan = profile_consolidation_service.build_plan(primary, duplicate)
    return _consolidation_page(request, state="plan", plan=plan, primary_id=primary, duplicate_id=duplicate, swap_id=duplicate)


@router.post("/cchh/candidatos/consolidar/confirmar", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_profiles_consolidate_confirm(request: Request):
    """Paso 2: confirmación EXPLÍCITA. Ejecuta la consolidación (recuperable). Un resultado parcial nunca se presenta como éxito."""
    gate = _require_consolidation_owner(request)
    if gate is not None:
        return gate
    form = await request.form()
    primary_id, duplicate_id = str(form.get("primary_id") or ""), str(form.get("duplicate_id") or "")
    resolutions = {k[len("res_"):]: str(v) for k, v in form.multi_items() if k.startswith("res_")}
    user = auth_service.session_user(request) or {}
    actor = str(user.get("sub") or user.get("email") or "")
    outcome = await run_in_threadpool(
        profile_consolidation_service.execute, primary_id, duplicate_id, resolutions=resolutions, plan_digest=str(form.get("plan_digest") or ""), actor=actor,
        confirmed=str(form.get("confirm") or "") == "1")
    if outcome.status == profile_consolidation_service.STALE_PLAN or outcome.code in ("UNRESOLVED_CONFLICT", "CONFIRMATION_REQUIRED"):
        plan = profile_consolidation_service.build_plan(primary_id, duplicate_id)
        return _consolidation_page(request, state="plan", plan=plan, primary_id=primary_id, duplicate_id=duplicate_id, swap_id=duplicate_id,
                                   message=outcome.message, resolutions=resolutions)
    return _consolidation_page(request, state="result", outcome=outcome, primary_id=primary_id, duplicate_id=duplicate_id)


@router.get("/cchh/consolidaciones", response_class=HTMLResponse, include_in_schema=False)
def human_capital_consolidations_page(request: Request):
    """Operaciones de consolidación (pendientes primero) con sus acciones de recuperación."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not app_access.is_owner(request):
        raise HTTPException(403, "La consolidación de perfiles está reservada al dueño de la aplicación")
    return _consolidation_page(request, state="operations", operations=profile_consolidation_service.list_operations())


@router.post("/cchh/consolidaciones/{operation_id}/{action}", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_consolidation_recover(request: Request, operation_id: str, action: str):
    """Reanudar (completa lo que falta) o revertir (restaura las imágenes previas) una consolidación PENDIENTE."""
    gate = _require_consolidation_owner(request)
    if gate is not None:
        return gate
    if action not in ("reanudar", "revertir"):
        raise HTTPException(404, "Acción desconocida")
    user = auth_service.session_user(request) or {}
    actor = str(user.get("sub") or user.get("email") or "")
    run = profile_consolidation_service.resume if action == "reanudar" else profile_consolidation_service.abort
    outcome = await run_in_threadpool(run, operation_id, actor=actor)
    return _consolidation_page(request, state="result", outcome=outcome, primary_id="", duplicate_id="")


_export_logger = logging.getLogger("danwor.human_capital")


def _export_actor(request: Request) -> str:
    """Quién exporta, para la auditoría durable: el claim `sub` de la sesión (identificador estable); sin él, el email. Nunca nombres de candidatos."""
    user = auth_service.session_user(request) or {}
    return str(user.get("sub") or user.get("email") or "").strip().lower()


def _download_dialog(request: Request, state: str, status_code: int = 200, **context) -> HTMLResponse:
    response = templates.TemplateResponse(request=request, name="partials/human_capital_download_dialog_content.html", context={"state": state, "limits": human_capital_export, **context},
                                          headers=_NO_STORE)
    response.status_code = status_code
    return response


@router.post("/cchh/candidatos/descargar/resumen", response_class=HTMLResponse, include_in_schema=False)
def human_capital_profiles_download_summary(request: Request, profile_ids: list[str] = Form([])):
    """Resumen PREVIO de «Descargar CVs»: candidatos, documentos disponibles y tamaño, con los límites aplicados en el servidor. Solo metadata local: NO habla con Graph, no lee ningún CV y no
    audita (la auditoría empieza con la confirmación). POST con solo ids; mismo origen obligatorio; `no-store`."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    try:
        plan = human_capital_export.build_plan(profile_ids)
    except human_capital_export.ExportSourceError:
        return _download_dialog(request, "source_error")
    return _download_dialog(request, "summary", plan=plan)


@router.post("/cchh/candidatos/descargar", include_in_schema=False)
async def human_capital_profiles_download(
    request: Request, profile_ids: list[str] = Form([]), graph_token: str = Form(""), plan_digest: str = Form(""), ack_omitted: str = Form(""), allow_partial: str = Form(""),
    ack_failed: list[str] = Form([]), parent_operation_id: str = Form(""),
):
    """«Descargar CVs»: UNA solicitud que audita (fail-closed), lee cada CV por Graph con el token DELEGADO del usuario (sus permisos reales), arma un ZIP temporal y lo entrega. Nunca entrega un
    ZIP parcial sin la segunda confirmación explícita (`allow_partial` + `ack_failed`). Si el cliente se desconecta, se cancela la lectura. Detalle y límites: `human_capital_export`."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    cancel = threading.Event()

    async def watch_disconnect() -> None:
        while not cancel.is_set():
            if await request.is_disconnected():
                cancel.set()
                return
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(watch_disconnect())
    try:
        outcome = await run_in_threadpool(
            human_capital_export.execute, actor=_export_actor(request), raw_ids=profile_ids, token=graph_token, expected_digest=plan_digest, ack_omitted=ack_omitted == "1",
            allow_partial=allow_partial == "1", ack_failed=ack_failed, parent_operation_id=parent_operation_id or None, cancel=cancel)
    except human_capital_export.ExportInputError:
        return _download_dialog(request, "no_token")
    except human_capital_export.ExportBusy:
        response = _download_dialog(request, "busy", 429)
        response.headers["Retry-After"] = "5"
        return response
    except human_capital_export.ExportAuditResultUnavailable:
        return _download_dialog(request, "audit_result_unavailable", 503)
    except human_capital_export.ExportAuditUnavailable:
        return _download_dialog(request, "audit_unavailable", 503)
    except human_capital_export.ExportSourceError:
        return _download_dialog(request, "source_error")
    except Exception as exc:  # noqa: BLE001 — sin detalle hacia el usuario ni en el log (solo la clase)
        _export_logger.error("cv_export: fallo inesperado (%s)", type(exc).__name__)
        return _download_dialog(request, "error", 500)
    finally:
        cancel.set()
        watcher.cancel()
    if outcome.status == "blocked":
        return _download_dialog(request, "summary", plan=outcome.plan)
    if outcome.status == "stale_plan":
        return _download_dialog(request, "stale")
    if outcome.status in ("withheld_partial", "failed", "cancelled"):
        return _download_dialog(request, "partial" if outcome.status == "withheld_partial" else outcome.status, outcome=outcome, plan=outcome.plan)

    def stream():
        try:
            while True:
                chunk = outcome.zip_stream.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            outcome.cleanup()                                                        # también si el cliente corta la descarga a medias

    headers = {**_NO_STORE, "Content-Disposition": f'attachment; filename="{outcome.zip_name}"', "Content-Length": str(outcome.zip_size), "X-Content-Type-Options": "nosniff",
               "X-Export-Operation": outcome.operation_id, "X-Export-Status": "partial" if outcome.status == "delivered_partial" else "full", "X-Export-Files": str(outcome.files_ok)}
    return StreamingResponse(stream(), media_type="application/zip", headers=headers)


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
            first_name=profile.get("first_name") or "", last_name=profile.get("last_name") or "",
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
        outcome = profile_document_service.delete_document(
            profile_id=profile_id, document_id=document_id, access_token=graph_token, remove_empty_folder=True,
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
    kept = profile_document_service.folder_kept_message(outcome["folder"]) if outcome.get("last_document") else ""
    if kept:
        # El documento SÍ se eliminó (físico + registro) pero la carpeta quedó: nunca un éxito completo silencioso. Si se conserva por la POLÍTICA de seguridad (limpieza automática deshabilitada) no es un fallo:
        # aviso informativo (`FOLDER_RETAINED_POLICY`), no una advertencia.
        policy = outcome["folder"] == profile_document_service.FOLDER_KEPT_POLICY
        return _render_profile_edit_with_documents(
            request, profile_id, profile, document_state={"kind": "FOLDER_RETAINED_POLICY" if policy else "FOLDER_KEPT", "message": "El documento se eliminó de SharePoint. " + kept},
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


# Errores de NEGOCIO esperables al actuar sobre una solicitud de Operaciones (transición inválida, conflicto de idempotencia, id inválido): nunca deben llegar como HTTP 500.
# Se distinguen de la concurrencia (`BusquedaConcurrentModificationError`) y de la persistencia (`_BUSQUEDA_SHELL_ERRORS`).
_BUSQUEDA_SOLICITUD_ERRORS = (
    busqueda_repository.BusquedaInvalidTransitionError,
    busqueda_repository.BusquedaSubmissionConflictError,
    busqueda_repository.BusquedaInvalidSubmissionIdError,
)


_BUSQUEDA_ATENCION_FILTERS = ("todas", "estancadas", "sin_requerimiento", "onboarding_pendiente")


def _busquedas_list_context(atencion: str = "todas") -> dict:
    all_b: list[dict] = []
    source_error: str | None = None
    try:
        # SOLO búsquedas que CCHH gestiona (históricas, altas directas de CCHH y solicitudes ya tomadas): las solicitudes pendientes, observadas o canceladas de Operaciones NO son procesos
        # de selección y no cuentan en activas/cerradas, señales ni contadores (viven en la bandeja «Solicitudes recibidas»).
        all_b = human_capital_service.list_busquedas_gestionadas()
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
        **_solicitudes_context(),
    }


# ---------------------------------------------------------------------------
# Solicitudes recibidas (I3): bandeja de CCHH con los requerimientos que Operaciones envía (REG-CH-01). Datos = la proyección segura de I1 (`solicitud_view`); las solicitudes ya tomadas
# NO se listan acá (pasaron a «Procesos activos»). Las canceladas se consultan con su filtro (trazabilidad), sin acciones y sin contarse como pendientes ni como procesos. Orden: última recepción desc.
# ---------------------------------------------------------------------------
_SOLICITUD_FILTROS = ("recibidas", "enviadas", "observadas", "canceladas")
_SOLICITUD_OK = {
    "tomada": "Solicitud tomada en gestión: la búsqueda ya figura entre los procesos activos.",
    "correccion": "Se solicitó la corrección a Operaciones. La solicitud queda «Observada» hasta que la reenvíen.",
}
_MSG_SOLICITUD_CONCURRENT = "La solicitud cambió desde que abriste la pantalla (por ejemplo, Operaciones la editó o la canceló). Revisá su estado actual antes de actuar."
_MSG_SOLICITUD_STORAGE = "No pudimos guardar el cambio en este momento. Reintentá en unos minutos."
_MSG_SOLICITUD_TAKEN = "Esa solicitud ya fue tomada por otra persona o cambió (por ejemplo, Operaciones la editó o la canceló). Actualizamos la lista de solicitudes disponibles."
_MSG_SOLICITUD_SOURCE = "No pudimos leer las solicitudes en este momento. Reintentá en unos minutos."
# Post/Redirect/Get: ante un conflicto/error de persistencia al tomar o pedir corrección, un navegador (Accept: text/html) recibe 303 hacia la pantalla con `?aviso=<clave>` (lista blanca; nunca texto libre
# en la URL) y el aviso se muestra en un GET; un consumidor NO HTML recibe el 409/503 con el mensaje (`HTTPException`, JSON).
_SOLICITUD_AVISOS = {"tomada": _MSG_SOLICITUD_TAKEN, "cambio": _MSG_SOLICITUD_CONCURRENT, "error": _MSG_SOLICITUD_STORAGE,
                     "no_elegible": "No se pudo agregar al candidato: no es elegible para nuevos procesos (marca «No reconsiderar»).",
                     "eliminacion_incompleta": "No se pudo completar la eliminación de la búsqueda: sigue existiendo y la operación quedó registrada. Reintentá en unos minutos (se retoma donde quedó).",
                     "eliminacion_no_permitida": "No hay un proceso para eliminar: la solicitud de Operaciones todavía no fue tomada en gestión por Capital Humano (o ya está cancelada)."}


def _wants_html(request: Request) -> bool:
    return "text/html" in (request.headers.get("accept") or "")


def _solicitud_badge(view: dict) -> tuple[str, str]:
    """(rótulo, tono del `.status-badge` del Hub). Derivado del contrato de I1; sin estados nuevos. Vocabulario de CAPITAL HUMANO (el de Operaciones es otro y no se toca: allí «Nueva/Reenviada»):
    el estado interno `enviada` es «enviada POR Operaciones», una solicitud RECIBIDA y pendiente de que CCHH la tome; se rotula «Pendiente» (la misma categoría `pendiente` de `solicitud_categoria`),
    nunca «Enviada», que en CCHH sugeriría una búsqueda enviada/gestionada por Capital Humano."""
    estado = view.get("estado_solicitud")
    if estado == busqueda_repository.SOLICITUD_CANCELADA:
        return "Cancelada", "inactivo"
    if estado == busqueda_repository.SOLICITUD_OBSERVADA:
        return "Observada", "tentativo"
    if estado == busqueda_repository.SOLICITUD_EN_GESTION:
        return "En gestión", "activo"
    return ("Reenviada" if view.get("reenviada") else "Pendiente"), "otro"


def _solicitud_recepcion_key(r: dict) -> tuple[str, str, str]:
    """Clave de orden de la bandeja: (última recepción, primer envío, id). Las canceladas ordenan por su fecha de cancelación."""
    enviada = r.get("enviada_en") or ""
    if r.get("estado_solicitud") == busqueda_repository.SOLICITUD_CANCELADA:
        ultima = r.get("cancelada_en") or r.get("ultimo_envio_en") or enviada
    else:
        ultima = r.get("ultimo_envio_en") or enviada
    return (ultima, enviada, r["busqueda_id"])


def _solicitudes_disponibles() -> list[dict]:
    """Solicitudes que CCHH puede TOMAR desde «+ Nuevo proceso»: solo `enviada` (incluye reenviadas), de Operaciones, todavía no tomadas ni observadas ni canceladas. Misma proyección segura de I1
    (con `revision` para la toma con control optimista); última recepción primero. Un error de lectura devuelve lista vacía: la creación manual no debe depender de esto."""
    try:
        rows = [r for r in human_capital_service.list_solicitudes_view() if r["estado_solicitud"] == busqueda_repository.SOLICITUD_ENVIADA and r.get("estado") == "activo"]
    except _BUSQUEDA_SHELL_ERRORS:
        return []
    return sorted(rows, key=_solicitud_recepcion_key, reverse=True)


def _solicitudes_context(filtro: str = "recibidas", page=1, page_size=None) -> dict:
    filtro = filtro if filtro in _SOLICITUD_FILTROS else "recibidas"
    rows: list[dict] = []
    source_error: str | None = None
    try:
        rows = [r for r in human_capital_service.list_solicitudes_view()
                if r["estado_solicitud"] in (busqueda_repository.SOLICITUD_ENVIADA, busqueda_repository.SOLICITUD_OBSERVADA, busqueda_repository.SOLICITUD_CANCELADA)]
    except _BUSQUEDA_SHELL_ERRORS:
        source_error = _MSG_SOLICITUD_SOURCE
    for r in rows:
        r["badge_label"], r["badge_tone"] = _solicitud_badge(r)
    # POBLACIÓN de cada indicador (todas son DEMANDA RECIBIDA, ninguna es un proceso gestionado por CCHH: al tomarla sale de esta bandeja y pasa a «Procesos activos»):
    #   «Pendientes» (clave `enviadas`) = estado `enviada` (nuevas + reenviadas): esperan que CCHH las tome u observe;  «Observadas» = esperan una acción de OPERACIONES (la corrección): ya no son trabajo de CCHH;
    #   «Por atender» (clave `recibidas`, la vista por defecto) = lo que CCHH tiene que accionar = estado `enviada` (incluye reenviadas), SIN observadas;  «Canceladas» = solo consulta.
    #   Una observada vuelve a «Por atender» / «Pendientes» cuando Operaciones la corrige y la reenvía (transición `reenviar`).
    by = {k: [r for r in rows if r["estado_solicitud"] == k] for k in ("enviada", "observada", "cancelada")}
    counts = {"enviadas": len(by["enviada"]), "observadas": len(by["observada"]), "canceladas": len(by["cancelada"])}
    counts["recibidas"] = counts["enviadas"]
    visible = {"recibidas": by["enviada"], "enviadas": by["enviada"], "observadas": by["observada"], "canceladas": by["cancelada"]}[filtro]
    # Orden: ÚLTIMA RECEPCIÓN descendente, ANTES de paginar (paginador compartido, desde la fila 50). Nuevas = `enviada_en`; reenviadas y observadas = `ultimo_envio_en` (la observación no cambia
    # cuándo CCHH recibió la solicitud); canceladas = `cancelada_en`. Desempate determinístico: primer envío y luego id.
    visible = sorted(visible, key=_solicitud_recepcion_key, reverse=True)
    pager = pagination.paginate(visible, page=page, page_size=page_size, base_url="/cchh/solicitudes", params={"solicitud": filtro})
    # Estado inicial del colapsable: ABIERTO solo si hay solicitudes por atender o pendientes (> 0) —o si hubo un error de lectura, para que se vea—; con 0 queda COLAPSADO. La preferencia manual de la persona (sessionStorage,
    # `data-persist-collapse`) sigue prevaleciendo sobre este valor por defecto.
    return {"solicitudes": pager["items"], "solicitudes_total": pager["total"], "solicitudes_pager": pager, "solicitud_filtro": filtro, "solicitud_counts": counts,
            "solicitudes_source_error": source_error, "solicitudes_open": bool(source_error) or counts["recibidas"] > 0 or counts["enviadas"] > 0}


@router.get("/cchh/solicitudes", response_class=HTMLResponse, include_in_schema=False)
def human_capital_solicitudes_panel(request: Request, solicitud: str = Query("recibidas"), page: str = Query(""), page_size: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not request.headers.get("HX-Request"):
        return RedirectResponse(url="/cchh/busquedas", status_code=303)       # el panel es un fragmento HTMX de la pantalla de Búsquedas
    # El fragmento HTMX lo pide un filtro/paginador DENTRO del panel ya abierto: se devuelve siempre abierto (colapsarlo por un filtro sin resultados escondería los propios chips).
    return templates.TemplateResponse(request=request, name="partials/human_capital_solicitudes_panel.html",
                                      context={"request": request, **_solicitudes_context(solicitud, page, page_size), "solicitudes_open": True})


def _busqueda_form_context(*, form_values, is_edit, error=None, concurrent_modification=False, solicitud_conflict=None) -> dict:
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
        # «+ Nuevo proceso»: solicitudes de Operaciones que se pueden tomar en vez de crear un proceso manual (solo en el alta).
        "solicitudes_disponibles": [] if is_edit else _solicitudes_disponibles(),
        "solicitud_conflict": solicitud_conflict,
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
        "blocked_profiles_count": human_capital_service.profiles_blocked_for_busqueda_count(busqueda),
        # "Eliminar" (owner request, diálogo Cerrar proceso): sólo visible para
        # una búsqueda placeholder/dummy -- MISMA regla que valida
        # `delete_busqueda` server-side (única fuente de verdad, ver
        # `busqueda_is_placeholder`), acá sólo decide si el botón se muestra.
        "busqueda_is_deletable": human_capital_service.busqueda_is_placeholder(busqueda),
        # Solicitud de Operaciones (None para históricas/altas directas). Mientras no esté `en_gestion` (enviada/observada/cancelada) el detalle muestra SOLO el requerimiento:
        # sin pipeline ni acciones de candidatos (el servidor ya las rechaza; acá además se ocultan).
        "solicitud": human_capital_service.solicitud_view(busqueda, include_cchh_actors=True),
        "solicitud_gestion": busqueda_repository.solicitud_estado(busqueda) in (None, busqueda_repository.SOLICITUD_EN_GESTION),
        "solicitud_badge": _solicitud_badge(human_capital_service.solicitud_view(busqueda)) if human_capital_service.solicitud_view(busqueda) else None,
        # Acceso directo a Entrevistas desde las tarjetas del pipeline (solo `Entrevista CCHH` / `Entrevista Líder`): ver `_entrevista_links`.
        "entrevista_links": _entrevista_links(busqueda),
        "onboarding_links": _onboarding_links(busqueda),
        # Historial UNIFICADO del proceso (solicitud + movimientos de candidatos): dentro del cuerpo que se re-renderiza tras cada mutación del pipeline, para que no quede desactualizado.
        "busqueda_historial": human_capital_service.solicitud_view(busqueda, include_cchh_actors=True) or human_capital_service.proceso_historial_view(busqueda),
    }


_ENTREVISTA_ETAPAS = {"Entrevista CCHH": "Evaluación CCHH", "Entrevista Líder": "Evaluación técnica"}
_ENTREVISTA_FOCO_POR_ETAPA = {"Entrevista CCHH": "cchh", "Entrevista Líder": "tecnica"}
# Contexto de acceso a la edición de Entrevistas → (campo con el foco, campo BLOQUEADO). Sin contexto (Entrevistas → Editar) no hay foco ni bloqueo: comportamiento general.
_ENTREVISTA_FOCOS = {"cchh": ("observaciones", "resumen_entrevista_tecnica"), "tecnica": ("resumen_entrevista_tecnica", "observaciones")}


def _normalize_foco(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in _ENTREVISTA_FOCOS else ""


def _locked_fields(foco) -> tuple:
    return (_ENTREVISTA_FOCOS[_normalize_foco(foco)][1],) if _normalize_foco(foco) else ()


def _append_foco(url: str, foco) -> str:
    foco = _normalize_foco(foco)
    return f"{url}{'&' if '?' in url else '?'}foco={foco}" if foco else url


def _registro_entrevista_url(app: dict, busqueda_id: str, *, return_to: str = "", foco: str = "") -> str:
    """URL del REGISTRO ADMINISTRATIVO de entrevista de un (candidato, proceso): su edición si ya existe; si no, el alta con candidato y puesto/área precargados (navegar no crea nada). Un fallo de lectura degrada al listado.
    `return_to`: contexto explícito de origen (Pipeline de ESA búsqueda o Evaluación CCHH del candidato) para que «Volver» regrese a él."""
    ctx = return_to
    try:
        existing = entrevista_repository.find_by_profile_and_busqueda(app["profile_id"], busqueda_id)
        return (pagination.with_return_to(_append_foco(f"/cchh/entrevistas/{existing['entrevista_id']}/editar", foco), ctx) if existing
                else pagination.with_return_to(_append_foco("/cchh/entrevistas/nueva?" + urllib.parse.urlencode({"profile_id": app["profile_id"], "busqueda_id": busqueda_id}), foco), ctx))
    except entrevista_repository.EntrevistaStorageError:
        return "/cchh/entrevistas"


def _entrevista_links(busqueda: dict) -> dict:
    """`application_id` -> `{url, label}` para las postulaciones en etapa de entrevista. Si YA existe la entrevista de ese (candidato, proceso) se va a SU edición (flujo vigente); si no, al alta de Entrevistas con el
    candidato y el puesto/área del proceso precargados (el alta sigue siendo manual: navegar NO crea nada). Un fallo de lectura degrada al listado de Entrevistas (nunca a un alta que pudiera duplicar)."""
    links: dict = {}
    bid = busqueda.get("busqueda_id")
    for app in busqueda.get("candidatos", []):
        label = _ENTREVISTA_ETAPAS.get(app.get("etapa"))
        if not label or not app.get("profile_id"):
            continue
        # Hasta que la pestaña Evaluación esté terminada, AMBOS íconos abren la edición de la entrevista de ese (candidato, proceso) —la misma pantalla de Entrevistas → Editar— con el contexto de origen (`foco`):
        # «Evaluación CCHH» pone el foco en el resumen CCHH y «Evaluación técnica» en el técnico. Navegar no crea nada ni duplica la entrevista.
        links[app["application_id"]] = {"url": _registro_entrevista_url(app, bid, return_to=pagination.pipeline_return_url(bid), foco=_ENTREVISTA_FOCO_POR_ETAPA[app["etapa"]]), "label": label}
    return links


def _onboarding_links(busqueda: dict) -> dict:
    """`application_id` -> URL del Onboarding EXISTENTE, solo para postulaciones Finalizado · Ingreso. Es una lectura: no crea ningún Onboarding (nace al finalizar con Ingreso, ver `ensure_onboarding_for_application`). Si no existe o
    la lectura falla, no se ofrece el acceso (nunca un enlace a algo que no está)."""
    links: dict = {}
    ctx = pagination.pipeline_return_url(busqueda.get("busqueda_id"))
    for app in busqueda.get("candidatos", []):
        if app.get("etapa") != busqueda_repository.ETAPA_FINAL or app.get("resultado_final") != "Ingreso" or not app.get("application_id"):
            continue
        try:
            if onboarding_repository.get(app["application_id"]) is not None:
                links[app["application_id"]] = pagination.with_return_to(f"/cchh/onboarding/{app['application_id']}/editar", ctx)
        except onboarding_repository.OnboardingStorageError:
            continue
    return links


def _pipeline_actor(request: Request) -> str | None:
    """Ejecutor de un movimiento del pipeline = claim `sub` de la sesión autenticada (normalizado), MISMA fuente que las transiciones de la solicitud. `None` si la sesión no lo trae."""
    return str((auth_service.session_user(request) or {}).get("sub") or "").strip().lower() or None


def _require_evaluation_owner(request: Request) -> None:
    """La pestaña «Evaluación» de Búsquedas (JD, grilla, ranking y evaluación CCHH por candidato) está en DESARROLLO: durante esa etapa solo la usa el dueño de la app (config `APP_OWNER`, predicado único
    `access.is_owner`; NO es `CONTENT_OWNER` ni la autorización de datos sensibles). Protección SERVER-SIDE de cada endpoint propio de la pestaña; el resto de las Búsquedas y Entrevistas no se ve afectado."""
    if not app_access.is_owner(request):
        raise HTTPException(403, "La pestaña Evaluación está en desarrollo y solo está disponible para el dueño de la app")


def _busqueda_shell(request, extra: dict) -> dict:
    user = auth_service.session_user(request)
    return {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "busquedas",
        "can_use_evaluation": app_access.is_owner_user(user),
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
def human_capital_busqueda_new_page(request: Request, aviso: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    today = datetime.now().date().isoformat()
    return templates.TemplateResponse(
        request=request, name="human_capital/busqueda_form.html",
        context=_busqueda_shell(request, _busqueda_form_context(
            form_values={"fecha_apertura": today, "pedido_formal_recibido": False}, is_edit=False, solicitud_conflict=_SOLICITUD_AVISOS.get(aviso))),
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
    actor = str((auth_service.session_user(request) or {}).get("sub") or "").strip().lower() or None   # autoría: el actor autenticado (claim `sub`; no se inventa uno)
    try:
        busqueda = human_capital_service.create_busqueda(form_values, actor=actor)
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
    if busqueda_repository.solicitud_estado(busqueda) not in (None, busqueda_repository.SOLICITUD_EN_GESTION):
        return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)   # solicitud aún no tomada: sin edición por CCHH
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
    except _BUSQUEDA_SOLICITUD_ERRORS as exc:
        return _render(error=str(exc))
    except _BUSQUEDA_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.get("/cchh/busquedas/{busqueda_id}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_detail_page(request: Request, busqueda_id: str, ok: str = Query(""), aviso: str = Query(""), vista: str = Query(""), paso: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    if vista == _VISTA_LEGACY_SCORING:
        # Compatibilidad: la pestaña «Scoring (JD)» pasó a llamarse «Evaluación»; los enlaces viejos conservan el resto de la consulta (paso, ok, …).
        query = urllib.parse.urlencode([(k, _VISTA_EVALUACION if (k == "vista") else v) for k, v in request.query_params.multi_items()])
        return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?{query}", status_code=307)
    ctx = {**_busqueda_detail_context(busqueda), "solicitud_ok": _SOLICITUD_OK.get(ok), "solicitud_error": _SOLICITUD_AVISOS.get(aviso)}
    if vista == _VISTA_EVALUACION and busqueda_repository.es_gestionada(busqueda) and app_access.is_owner(request):     # el resto de los usuarios sigue viendo el Pipeline (la pestaña no existe para ellos)
        ctx.update(_scoring_context(busqueda, paso, ok=ok, actor=_pipeline_actor(request)))
    return templates.TemplateResponse(request=request, name="human_capital/busqueda_detail.html", context=_busqueda_shell(request, ctx))


# ── Pestaña «Evaluación» (antes «Scoring (JD)») — scoring CCHH: Job Description + grilla de evaluación + evaluación por candidato y ranking ──
_VISTA_EVALUACION, _VISTA_LEGACY_SCORING = "evaluacion", "scoring"
_SCORING_OK = {"jd": "Job Description guardada.", "grilla": "Grilla guardada: se creó una nueva versión.", "grilla_sin_cambios": "La grilla no tenía cambios: se mantiene la misma versión.",
               "evaluacion": "Evaluación CCHH guardada.", "evaluacion_sin_cambios": "La evaluación no tenía cambios: no se modificó."}
_SCORING_FORM_FIELDS = ("criterio_id", "bloque", "nombre", "peso", "descripcion", "ancla_1", "ancla_3", "ancla_5", "preguntas_guia")


def _scoring_context(busqueda: dict, paso: str = "", *, ok: str = "", errors: list[str] | None = None, rows: list[dict] | None = None, jd: tuple[str, str] | None = None, actor: str | None = None) -> dict:
    """Contexto del panel «Scoring CCHH» (pestaña «Evaluación» del detalle del proceso). Ante un error de validación se vuelve a mostrar lo que la persona escribió (`rows`/`jd`), no lo guardado."""
    try:
        data = scoring_service.panel(busqueda)
    except (scoring_grid_repository.GridStorageError, ValueError) as exc:
        return {"vista": _VISTA_EVALUACION, "scoring": {"storage_error": str(exc), "paso": "jd", "editable": False, "bloques": scoring_engine.BLOQUES, "errors": []}}
    paso = paso if paso in ("jd", "grilla", "ranking") else ("ranking" if data["grilla_valida"] else ("grilla" if data["jd_cargada"] else "jd"))
    if paso == "ranking" and not data["grilla_valida"]:
        paso = "grilla"                                                                               # sin grilla válida no hay ranking: se vuelve al paso que falta
    if paso == "ranking":
        try:
            data["ranking"] = scoring_service.ranking_view(busqueda, actor)
        except (scoring_grid_repository.GridStorageError, ValueError) as exc:
            return {"vista": _VISTA_EVALUACION, "scoring": {"storage_error": str(exc), "paso": "jd", "editable": False, "bloques": scoring_engine.BLOQUES, "errors": []}}
    if rows is not None:
        data["criterios_por_bloque"] = {b["key"]: [r for r in rows if r.get("bloque") == b["key"]] for b in scoring_engine.BLOQUES}
        data["suma_pesos"] = scoring_engine.suma_pesos(scoring_engine.normalizar_criterios(rows)[0])
    if jd is not None:
        data["jd_texto"], data["palabras_clave"] = jd
    return {"vista": _VISTA_EVALUACION, "scoring": {**data, "paso": paso, "ok": _SCORING_OK.get(ok), "errors": errors or []}}


def _render_scoring(request: Request, busqueda_id: str, paso: str, *, errors: list[str], rows: list[dict] | None = None, jd: tuple[str, str] | None = None):
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    ctx = {**_busqueda_detail_context(busqueda), **_scoring_context(busqueda, paso, errors=errors, rows=rows, jd=jd, actor=_pipeline_actor(request))}
    return templates.TemplateResponse(request=request, name="human_capital/busqueda_detail.html", context=_busqueda_shell(request, ctx))


_SCORING_SHELL_ERRORS = (*_BUSQUEDA_SHELL_ERRORS, scoring_grid_repository.GridStorageError, scoring_grid_repository.GridRemoteOutcomeUnknownError)


@router.post("/cchh/busquedas/{busqueda_id}/scoring/jd", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_scoring_jd(request: Request, busqueda_id: str, jd_texto: str = Form(""), palabras_clave: str = Form("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    _require_evaluation_owner(request)
    try:
        scoring_service.save_jd(busqueda_id, jd_texto, palabras_clave)
    except LookupError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except (ValueError, busqueda_repository.BusquedaInvalidTransitionError, busqueda_repository.BusquedaNotFoundError, *_SCORING_SHELL_ERRORS) as exc:
        return _render_scoring(request, busqueda_id, "jd", errors=[str(exc)], jd=(jd_texto, palabras_clave))
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?vista=evaluacion&paso=grilla&ok=jd", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/scoring/grilla", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_busqueda_scoring_grid(request: Request, busqueda_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    _require_evaluation_owner(request)
    form = await request.form()
    columns = {k: [str(v) for v in form.getlist(k)] for k in _SCORING_FORM_FIELDS}
    count = len(columns["bloque"])
    rows = [{k: (columns[k][i] if i < len(columns[k]) else "") for k in _SCORING_FORM_FIELDS} for i in range(count)]
    try:
        _, changed = scoring_service.save_grid(busqueda_id, form.get("expected_revision"), rows, actor=_pipeline_actor(request))
    except LookupError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except scoring_engine.ScoringValidationError as exc:
        return _render_scoring(request, busqueda_id, "grilla", errors=exc.errors, rows=rows)
    except scoring_grid_repository.GridConcurrentModificationError as exc:
        return _render_scoring(request, busqueda_id, "grilla", errors=[str(exc)])
    except (ValueError, busqueda_repository.BusquedaNotFoundError, *_SCORING_SHELL_ERRORS) as exc:
        return _render_scoring(request, busqueda_id, "grilla", errors=[str(exc)], rows=rows)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?vista=evaluacion&paso=grilla&ok={'grilla' if changed else 'grilla_sin_cambios'}", status_code=303)


# ── Evaluación CCHH de un candidato (instrumento del scoring) ──
def _evaluation_values(form: dict, campos) -> dict:
    """Valores a mostrar por criterio: lo que la persona acaba de enviar (re-render por error) o, si no, su evaluación previa. Solo lectura."""
    values = {}
    for cid, prev in (form.get("previous") or {}).items():
        values[cid] = {"nota": str(prev.get("nota") or ""), "cumple": "si" if prev.get("cumple") is True else ("no" if prev.get("cumple") is False else ""), "comentario": prev.get("comentario") or ""}
    if campos is not None:
        for blq in form["criterios_por_bloque"].values():
            for c in blq:
                cid = c["criterio_id"]
                values[cid] = {"nota": str(campos.get(f"nota_{cid}") or ""), "cumple": str(campos.get(f"cumple_{cid}") or ""), "comentario": str(campos.get(f"comentario_{cid}") or "")}
    return values


def _render_evaluation(request: Request, busqueda_id: str, application_id: str, *, errors: list[str] | None = None, campos=None, ok: str = ""):
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None or not busqueda_repository.es_gestionada(busqueda):
        raise HTTPException(404, "Búsqueda no encontrada")
    try:
        form = scoring_service.evaluation_form(busqueda, application_id, _pipeline_actor(request))
    except LookupError:
        raise HTTPException(404, "Postulación no encontrada") from None
    except (scoring_grid_repository.GridStorageError, ValueError) as exc:
        form, errors = None, [str(exc)]
    ctx = {"busqueda": busqueda, "application_id": application_id, "form": form, "errors": errors or [], "ok": _SCORING_OK.get(ok),
           "values": _evaluation_values(form, campos) if form else {}, "comentario_general": (str(campos.get("comentario_general") or "") if campos is not None else (form or {}).get("comentario_general", "")),
           "registro_url": _registro_entrevista_url(form["application"], busqueda_id, return_to=pagination.evaluation_return_url(busqueda_id, application_id)) if form and form["application"].get("profile_id") else "/cchh/entrevistas",
           "back_url": f"/cchh/busquedas/{busqueda_id}?vista=evaluacion&paso=ranking", "grid_url": f"/cchh/busquedas/{busqueda_id}?vista=evaluacion&paso=grilla"}
    return templates.TemplateResponse(request=request, name="human_capital/busqueda_evaluacion_cchh.html", context=_busqueda_shell(request, ctx))


@router.get("/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/evaluacion-cchh", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_cchh_evaluation_page(request: Request, busqueda_id: str, application_id: str):
    """Formulario de evaluación CCHH del candidato. Solo lectura: abrirlo NO crea ningún registro (se persiste únicamente al guardar)."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    _require_evaluation_owner(request)
    return _render_evaluation(request, busqueda_id, application_id)


@router.post("/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/evaluacion-cchh", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_busqueda_cchh_evaluation_save(request: Request, busqueda_id: str, application_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    _require_evaluation_owner(request)
    form = await request.form()
    campos = {k: str(v) for k, v in form.items()}
    try:
        _, changed = scoring_service.save_evaluation(busqueda_id, application_id, recorded_by=_pipeline_actor(request), campos=campos, expected_revision=campos.get("expected_revision"))
    except LookupError:
        raise HTTPException(404, "No encontrado") from None
    except scoring_engine.ScoringValidationError as exc:
        return _render_evaluation(request, busqueda_id, application_id, errors=exc.errors, campos=campos)
    except scoring_engine.ScoringIdentityError as exc:
        return _render_evaluation(request, busqueda_id, application_id, errors=[str(exc)], campos=campos)
    except scoring_evaluation_repository.EvaluationConcurrentModificationError as exc:
        return _render_evaluation(request, busqueda_id, application_id, errors=[str(exc)])
    except (ValueError, busqueda_repository.BusquedaNotFoundError, *_SCORING_SHELL_ERRORS) as exc:
        return _render_evaluation(request, busqueda_id, application_id, errors=[str(exc)], campos=campos)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?vista=evaluacion&paso=ranking&ok={'evaluacion' if changed else 'evaluacion_sin_cambios'}", status_code=303)


def _busqueda_detail_body(request, busqueda_id: str, notice: str | None = None):
    """Fragmento del cuerpo del detalle (pipeline + agregar candidato), usado
    para el swap HTMX tras cada mutación de pipeline. `notice`: aviso visible cuando la acción pedida no se aplicó (p. ej. candidato no elegible)."""
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_busqueda_detail_body.html",
        context={"request": request, **_busqueda_detail_context(busqueda), "oob_historial": True, "pipeline_notice": notice},
    )


@router.post("/cchh/busquedas/{busqueda_id}/candidatos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_add_candidate(request: Request, busqueda_id: str, profile_id: str = Form("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    notice = None
    try:
        human_capital_service.add_candidate_to_busqueda(busqueda_id, profile_id, actor=_pipeline_actor(request))
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except human_capital_service.CandidateNotEligibleError as exc:
        notice = str(exc)                                              # regla de elegibilidad: no se incorpora y se AVISA (no es un fallo silencioso)
    except (ValueError, *_BUSQUEDA_SOLICITUD_ERRORS, *_BUSQUEDA_SHELL_ERRORS):
        pass  # el body se re-renderiza igual; un fallo de durabilidad no rompe la vista
    if request.headers.get("HX-Request"):
        return _busqueda_detail_body(request, busqueda_id, notice=notice)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}" + ("?aviso=no_elegible" if notice else ""), status_code=303)


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
        human_capital_service.move_application_stage(busqueda_id, application_id, etapa, resultado_final, actor=_pipeline_actor(request))
    except (busqueda_repository.BusquedaNotFoundError, busqueda_repository.ApplicationNotFoundError):
        raise HTTPException(404, "No encontrado") from None
    except (ValueError, *_BUSQUEDA_SOLICITUD_ERRORS, *_BUSQUEDA_SHELL_ERRORS):
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
        human_capital_service.remove_application(busqueda_id, application_id, actor=_pipeline_actor(request))
    except (busqueda_repository.BusquedaNotFoundError, busqueda_repository.ApplicationNotFoundError):
        pass
    except (*_BUSQUEDA_SOLICITUD_ERRORS, *_BUSQUEDA_SHELL_ERRORS):
        pass
    if request.headers.get("HX-Request"):
        return _busqueda_detail_body(request, busqueda_id)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)


@router.get("/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/contacto", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_candidate_contact(request: Request, busqueda_id: str, application_id: str):
    """Contenido del diálogo «Contactar» de UNA postulación del proceso: solo lectura (no cambia etapas, no crea entrevistas, no registra envíos). Misma autorización que el resto del pipeline de CCHH; el candidato se
    resuelve SIEMPRE por la postulación del proceso indicado, así que no hay forma de pedir los datos de contacto de un candidato ajeno a ese proceso."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    application = next((a for a in busqueda.get("candidatos", []) if a.get("application_id") == application_id), None)
    if application is None:
        raise HTTPException(404, "Postulación no encontrada")
    profile = human_capital_service.get_profile(application.get("profile_id")) if application.get("profile_id") else None
    view = human_capital_contact.contact_view(profile, busqueda, application) if profile and not profile.get("deleted_at") else None
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_contact_dialog_content.html",
        context={"request": request, "view": view, "event_url": f"/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/contacto/evento"}, headers={"Cache-Control": "no-store"},
    )


@router.post("/cchh/busquedas/{busqueda_id}/candidatos/{application_id}/contacto/evento", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_candidate_contact_event(
    request: Request, busqueda_id: str, application_id: str,
    accion: str = Form(""), situacion: str = Form(""), canal: str = Form(""), event_id: str = Form(""),
):
    """Trazabilidad de la comunicación (historial existente del proceso): el navegador avisa SOLO después de una copia exitosa o de ejecutar la apertura del canal. Registra situación, canal, acción, candidato/postulación,
    ejecutor autenticado y fecha; jamás el texto del mensaje ni datos de contacto, y jamás «enviado/entregado». Responde con la tabla de Historial actualizada (`X-Contact-Recorded`: 1 nuevo / 0 duplicado ignorado)."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        busqueda, recorded = human_capital_service.register_candidate_contact(
            busqueda_id, application_id, accion, situacion, canal, actor=_pipeline_actor(request), event_id=event_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409 if "no tiene cargado" in str(exc) or "no está activo" in str(exc) else 400, str(exc)) from None
    except (busqueda_repository.BusquedaNotFoundError, busqueda_repository.ApplicationNotFoundError):
        raise HTTPException(404, "No encontrado") from None
    except (busqueda_repository.BusquedaInvalidTransitionError, *_BUSQUEDA_SOLICITUD_ERRORS) as exc:
        raise HTTPException(409, str(exc)) from None
    except _BUSQUEDA_SHELL_ERRORS:
        raise HTTPException(503, "No se pudo registrar en el historial: reintentá en unos instantes.") from None
    ctx = _busqueda_detail_context(busqueda)
    return templates.TemplateResponse(
        request=request, name="partials/human_capital_historial_section.html",
        context={"request": request, "view": ctx["busqueda_historial"]}, headers={"X-Contact-Recorded": "1" if recorded else "0", "Cache-Control": "no-store"},
    )


def _solicitud_actor(request: Request) -> tuple[object | None, str]:
    """(redirect|None, actor). Sesión + autorización de CCHH (`_require_human_capital_session`), mismo origen en la escritura y actor = claim `sub` de la sesión."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect, ""
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    actor = str((auth_service.session_user(request) or {}).get("sub") or "").strip().lower()
    if not actor:
        raise HTTPException(403, "La sesión no identifica al usuario")
    return None, actor


def _solicitud_error_page(request: Request, busqueda_id: str, message: str, status_code: int):
    busqueda = human_capital_service.get_busqueda(busqueda_id)
    if busqueda is None:
        raise HTTPException(404, "Búsqueda no encontrada")
    return templates.TemplateResponse(
        request=request, name="human_capital/busqueda_detail.html",
        context=_busqueda_shell(request, {**_busqueda_detail_context(busqueda), "solicitud_error": message}), status_code=status_code)


async def _solicitud_transition(request: Request, busqueda_id: str, action: str):
    redirect, actor = _solicitud_actor(request)
    if redirect is not None:
        return redirect
    form = await request.form()
    desde_nuevo = action == "tomar" and form.get("desde") == "nuevo"          # tomada desde «+ Nuevo proceso»
    try:
        revision = int(str(form.get("expected_revision") or "").strip())
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    def _fail(aviso: str, status_code: int):
        if not _wants_html(request):                                           # consumidor no HTML: la respuesta de conflicto/error apropiada (409/503 + mensaje)
            raise HTTPException(status_code, _SOLICITUD_AVISOS[aviso])
        target = "/cchh/busquedas/nueva" if desde_nuevo else f"/cchh/busquedas/{busqueda_id}"
        return RedirectResponse(url=f"{target}?aviso={aviso}", status_code=303)    # PRG: el aviso y la lista actualizada salen de un GET

    try:
        if action == "tomar":
            human_capital_service.take_solicitud(busqueda_id, revision, actor)
        else:
            human_capital_service.observe_solicitud(busqueda_id, revision, form.get("motivo") if isinstance(form.get("motivo"), str) else "", actor)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except (busqueda_repository.BusquedaConcurrentModificationError, *_BUSQUEDA_SOLICITUD_ERRORS):
        return _fail("tomada" if desde_nuevo else "cambio", 409)
    except _BUSQUEDA_SHELL_ERRORS:
        return _fail("error", 503)
    except ValueError as exc:                                                  # motivo obligatorio al solicitar corrección
        return _solicitud_error_page(request, busqueda_id, str(exc), 422)
    return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?ok={'tomada' if action == 'tomar' else 'correccion'}", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/tomar", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_solicitud_take(request: Request, busqueda_id: str):
    return await _solicitud_transition(request, busqueda_id, "tomar")


@router.post("/cchh/busquedas/{busqueda_id}/solicitar-correccion", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_solicitud_request_correction(request: Request, busqueda_id: str):
    return await _solicitud_transition(request, busqueda_id, "correccion")


@router.post("/cchh/busquedas/{busqueda_id}/cerrar", response_class=HTMLResponse, include_in_schema=False)
async def human_capital_busqueda_close(request: Request, busqueda_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form = await request.form()
    finalize_ids = form.getlist("finalize_application_id")
    try:
        human_capital_service.close_busqueda(busqueda_id, finalize_ids, actor=_pipeline_actor(request))
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except (*_BUSQUEDA_SOLICITUD_ERRORS, *_BUSQUEDA_SHELL_ERRORS):
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
    except (ValueError, *_BUSQUEDA_SOLICITUD_ERRORS, *_BUSQUEDA_SHELL_ERRORS):
        return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}", status_code=303)
    return RedirectResponse(url="/cchh/busquedas", status_code=303)


@router.post("/cchh/busquedas/{busqueda_id}/eliminar-completa", response_class=HTMLResponse, include_in_schema=False)
def human_capital_busqueda_delete_full(request: Request, busqueda_id: str):
    """«Eliminar búsqueda» con TODO lo asociado (postulaciones, entrevistas, onboardings, grilla y evaluaciones; las publicaciones se desvinculan). Misma autorización que el resto de las acciones de la búsqueda (sesión de
    Capital Humano); la confirmación la pide el diálogo corporativo en el navegador y el servidor valida por su cuenta (solicitud de Operaciones → no se elimina). Un fallo parcial deja la búsqueda visible y reanudable."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.delete_busqueda_completa(busqueda_id, actor=_pipeline_actor(request))
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Búsqueda no encontrada") from None
    except ValueError:
        return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?aviso=eliminacion_no_permitida", status_code=303)
    except human_capital_service.BusquedaDeletionError:
        return RedirectResponse(url=f"/cchh/busquedas/{busqueda_id}?aviso=eliminacion_incompleta", status_code=303)
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
        "can_see_compensation": human_capital_access.can_view_sensitive_data(user),     # «Remuneración pretendida»: política central; jamás un dato del navegador
        **extra,
    }


def _form_without_protected_compensation(form_values: dict, allow: bool) -> dict:
    """Sin permiso, el importe (persistido o enviado) NO viaja a ninguna plantilla ni se reenvía: se descarta antes de renderizar o de llamar al servicio."""
    return form_values if allow else {**form_values, "remuneracion_pretendida": None}


def _entrevistas_list_context(origen: str = "", puesto: str = "", area: str = "", q: str = "", page=1, page_size=None, allow_compensation: bool = False) -> dict:
    origen, puesto, area, q = (origen or "").strip(), (puesto or "").strip(), (area or "").strip(), (q or "").strip()
    entrevistas: list[dict] = []
    source_error: str | None = None
    try:
        entrevistas = human_capital_service.list_entrevistas_view(allow_compensation)           # sin permiso: el importe sale enmascarado (`****`) desde el servicio
    except entrevista_repository.EntrevistaStorageError as exc:
        source_error = str(exc)
    dataset_has_any = bool(entrevistas)
    # Opciones de los tres desplegables sobre el dataset COMPLETO (no desaparecen al aplicar otros filtros). Origen = Profile.source (taxonomía gobernada `fuente`), Puesto = distintos presentes
    # (no es catálogo), Área = `area_consultora`.
    origen_options = _governed_filter_options("fuente", [e.get("origen") for e in entrevistas], origen, human_capital_service.ENTREVISTA_SIN_ORIGEN, "Sin origen")
    puesto_options = _governed_filter_options(None, [e.get("puesto_mostrado") for e in entrevistas], puesto, human_capital_service.ENTREVISTA_SIN_PUESTO, "Sin puesto")
    area_options = _governed_filter_options("area_consultora", [e.get("area") for e in entrevistas], area, human_capital_service.SIN_AREA, "Sin área")
    entrevistas = human_capital_service.filter_entrevistas(entrevistas, origen=origen, puesto=puesto, area=area, q=q, include_compensation=allow_compensation)      # filtros + búsqueda ANTES de paginar (sin permiso, la búsqueda NO indexa el importe)
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


def _entrevista_form_context_foco(ctx: dict, foco) -> dict:
    """Agrega el contexto de acceso (foco y campo bloqueado) al contexto del formulario de Entrevista."""
    foco = _normalize_foco(foco)
    ctx["foco"] = foco
    ctx["foco_field"], ctx["locked_field"] = _ENTREVISTA_FOCOS.get(foco, ("", ""))
    return ctx


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
    ctx = _entrevistas_list_context(origen, puesto, area, q, page, page_size, human_capital_access.can_view_sensitive_data_request(request))
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
def human_capital_entrevista_new_page(request: Request, profile_id: str = Query(""), busqueda_id: str = Query(""), foco: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    # Precarga SOLO si el contexto viene del pipeline y es verificable: el candidato existe y (si se indica el proceso) participa en él. MISMA derivación de puesto/área que la entrevista automática. No escribe nada.
    form_values: dict = {}
    profile = human_capital_service.get_profile(profile_id) if profile_id else None
    if profile is not None and not profile.get("deleted_at"):
        form_values["profile_id"] = profile_id
        busqueda = human_capital_service.get_busqueda(busqueda_id) if busqueda_id else None
        if busqueda is not None and any(a.get("profile_id") == profile_id for a in busqueda.get("candidatos", [])):
            # Una entrevista por (candidato, proceso): si ya existe (enlace viejo, otra pestaña) se va a ella en vez de ofrecer un alta que la duplicaría.
            existing = entrevista_repository.find_by_profile_and_busqueda(profile_id, busqueda_id)
            if existing is not None:
                return RedirectResponse(url=_append_foco(f"/cchh/entrevistas/{existing['entrevista_id']}/editar" + _return_context(request.query_params, "/cchh/entrevistas", existing)["qs"], foco), status_code=303)
            form_values["busqueda_id"] = busqueda_id                       # el alta queda ligada a la postulación (el servidor resuelve `application_id`)
            form_values["puesto_evaluado"] = busqueda.get("nombre_puesto")
            form_values["area"] = busqueda.get("area_consultora")
            form_values["modalidad_contratacion"] = human_capital_service.resolve_entrevista_modalidad(None, busqueda)[0]
    return templates.TemplateResponse(
        request=request, name="human_capital/entrevista_form.html",
        context=_entrevistas_shell(request, _entrevista_form_context_foco(_entrevista_form_context(form_values=form_values, is_edit=False), foco)),
    )


@router.post("/cchh/entrevistas/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_new_submit(
    request: Request, profile_id: str = Form(""), busqueda_id: str = Form(""),
    fecha_entrevista: str = Form(""), puesto_evaluado: str = Form(""), area: str = Form(""),
    modalidad_contratacion: str = Form(""), remuneracion_pretendida: str = Form(""),
    observaciones: str = Form(""), resumen_entrevista_tecnica: str = Form(""), foco: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    allow = human_capital_access.can_view_sensitive_data_request(request)
    form_values = _form_without_protected_compensation(_entrevista_form_from_request(
        fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
        remuneracion_pretendida, observaciones, resumen_entrevista_tecnica), allow)
    for locked in _locked_fields(foco):                                       # en un alta el campo bloqueado por el contexto no se escribe: lo que llegue se descarta
        form_values[locked] = ""
    try:
        if busqueda_id:          # origen = postulación del Pipeline: ligada a la Búsqueda/postulación y única por (candidato, proceso)
            entrevista, _created = human_capital_service.create_entrevista_for_application(profile_id, busqueda_id, form_values, allow_compensation=allow)
        else:
            entrevista = human_capital_service.create_entrevista_manual(profile_id, form_values, allow_compensation=allow)
    except (ValueError, *_ENTREVISTA_SHELL_ERRORS) as exc:
        return templates.TemplateResponse(
            request=request, name="human_capital/entrevista_form.html",
            context=_entrevistas_shell(request, _entrevista_form_context_foco(_entrevista_form_context(
                form_values={**form_values, **({"profile_id": profile_id, "busqueda_id": busqueda_id} if busqueda_id else {})}, is_edit=False, error=str(exc)), foco)))
    # Sin vista de detalle standalone (owner UX pass 1: "la experiencia de
    # producto no la necesita" -- ver Editar mismo, que ya muestra todos los
    # campos): después de crear, se sigue editando directo.
    return RedirectResponse(url=_append_foco(f"/cchh/entrevistas/{entrevista['entrevista_id']}/editar" + _return_context(request.query_params, "/cchh/entrevistas", entrevista)["qs"], foco), status_code=303)


@router.get("/cchh/entrevistas/{entrevista_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_edit_page(request: Request, entrevista_id: str, foco: str = Query("")):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    entrevista = human_capital_service.get_entrevista(entrevista_id)
    if entrevista is None:
        raise HTTPException(404, "Entrevista no encontrada")
    allow = human_capital_access.can_view_sensitive_data_request(request)
    view = human_capital_service.entrevista_view(entrevista, allow)
    form_values = _form_without_protected_compensation(dict(entrevista), allow)
    form_values["expected_revision"] = entrevista.get("revision", 1)
    # Modalidad: valor guardado > (candidato: no aplica) > requerimiento del proceso > sin especificar. SOLO precarga el formulario (no escribe): una entrevista ya editada (revision > 1) conserva su vacío tal cual.
    modalidad, origen = human_capital_service.resolve_entrevista_modalidad(
        entrevista.get("modalidad_contratacion"), view.get("busqueda"), edited=entrevista.get("revision", 1) > 1)
    form_values["modalidad_contratacion"] = modalidad
    ctx = _entrevista_form_context_foco(_entrevista_form_context(form_values=form_values, is_edit=True, entrevista=view), foco)
    ctx["modalidad_prefilled"] = origen == "busqueda"
    return templates.TemplateResponse(
        request=request, name="human_capital/entrevista_form.html",
        context=_entrevistas_shell(request, ctx),
    )


@router.post("/cchh/entrevistas/{entrevista_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_edit_submit(
    request: Request, entrevista_id: str,
    fecha_entrevista: str = Form(""), puesto_evaluado: str = Form(""), area: str = Form(""),
    modalidad_contratacion: str = Form(""), remuneracion_pretendida: str = Form(""),
    observaciones: str = Form(""), resumen_entrevista_tecnica: str = Form(""), foco: str = Form(""),
    expected_revision: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    allow = human_capital_access.can_view_sensitive_data_request(request)
    form_values = _form_without_protected_compensation(_entrevista_form_from_request(
        fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
        remuneracion_pretendida, observaciones, resumen_entrevista_tecnica), allow)
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    locked = _locked_fields(foco)

    def _render(**extra):
        fv = dict(form_values); fv["expected_revision"] = revision_int
        entrevista = human_capital_service.get_entrevista(entrevista_id)
        view = human_capital_service.entrevista_view(entrevista, allow) if entrevista else None
        for name in locked:                                                   # el campo bloqueado se muestra con lo PERSISTIDO (un campo deshabilitado no viaja en el formulario)
            fv[name] = (entrevista or {}).get(name)
        return templates.TemplateResponse(
            request=request, name="human_capital/entrevista_form.html",
            context=_entrevistas_shell(request, _entrevista_form_context_foco(_entrevista_form_context(
                form_values=fv, is_edit=True, entrevista=view, **extra), foco)))

    try:
        human_capital_service.update_entrevista(entrevista_id, revision_int, form_values, allow_compensation=allow, locked_fields=locked)
    except entrevista_repository.EntrevistaNotFoundError:
        raise HTTPException(404, "Entrevista no encontrada") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except entrevista_repository.EntrevistaConcurrentModificationError:
        return _render(concurrent_modification=True)
    except _ENTREVISTA_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return _return_redirect(request, "/cchh/entrevistas", subject=human_capital_service.get_entrevista(entrevista_id))


@router.post("/cchh/entrevistas/{entrevista_id}/no-concretada", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_no_concretada(
    request: Request, entrevista_id: str,
    fecha_entrevista: str = Form(""), puesto_evaluado: str = Form(""), area: str = Form(""),
    modalidad_contratacion: str = Form(""), remuneracion_pretendida: str = Form(""),
    observaciones: str = Form(""), resumen_entrevista_tecnica: str = Form(""), foco: str = Form(""),
    expected_revision: str = Form(""),
):
    # MISMO botón que la referencia (EntrevistaModal.tsx "No se concretó"):
    # guarda lo que esté cargado en el form Y marca concretada=False en la
    # misma acción, en vez de descartar ediciones pendientes.
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    allow = human_capital_access.can_view_sensitive_data_request(request)
    form_values = _form_without_protected_compensation(_entrevista_form_from_request(
        fecha_entrevista, puesto_evaluado, area, modalidad_contratacion,
        remuneracion_pretendida, observaciones, resumen_entrevista_tecnica), allow)
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None

    locked = _locked_fields(foco)

    def _render(**extra):
        fv = dict(form_values); fv["expected_revision"] = revision_int
        entrevista = human_capital_service.get_entrevista(entrevista_id)
        view = human_capital_service.entrevista_view(entrevista, allow) if entrevista else None
        for name in locked:                                                   # el campo bloqueado se muestra con lo PERSISTIDO (un campo deshabilitado no viaja en el formulario)
            fv[name] = (entrevista or {}).get(name)
        return templates.TemplateResponse(
            request=request, name="human_capital/entrevista_form.html",
            context=_entrevistas_shell(request, _entrevista_form_context_foco(_entrevista_form_context(
                form_values=fv, is_edit=True, entrevista=view, **extra), foco)))

    try:
        updated = human_capital_service.update_entrevista(entrevista_id, revision_int, form_values, allow_compensation=allow, locked_fields=locked)
        human_capital_service.marcar_entrevista_no_concretada(entrevista_id, updated["revision"])
    except entrevista_repository.EntrevistaNotFoundError:
        raise HTTPException(404, "Entrevista no encontrada") from None
    except ValueError as exc:
        return _render(error=str(exc))
    except entrevista_repository.EntrevistaConcurrentModificationError:
        return _render(concurrent_modification=True)
    except _ENTREVISTA_SHELL_ERRORS as exc:
        return _render(error=str(exc))
    return _return_redirect(request, "/cchh/entrevistas", subject=human_capital_service.get_entrevista(entrevista_id))


@router.post("/cchh/entrevistas/{entrevista_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_entrevista_delete(request: Request, entrevista_id: str):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    subject = human_capital_service.get_entrevista(entrevista_id)          # el contexto se valida contra el registro ANTES de borrarlo
    try:
        human_capital_service.delete_entrevista(entrevista_id)
    except entrevista_repository.EntrevistaNotFoundError:
        pass
    except _ENTREVISTA_SHELL_ERRORS:
        pass
    return _return_redirect(request, "/cchh/entrevistas", subject=subject)


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
        "can_see_offer": human_capital_access.can_view_sensitive_data(user),            # «Oferta cerrada»: MISMA política central que «Remuneración pretendida»; jamás un dato del navegador
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


def _onboarding_list_context(estado: str = "todos", area: str = "", q: str = "", page=1, page_size=None, allow_sensitive: bool = False) -> dict:
    if estado not in _ONBOARDING_ESTADO_FILTERS:
        estado = "todos"
    area, q = (area or "").strip(), (q or "").strip()
    rows: list[dict] = []
    source_error: str | None = None
    try:
        # GET de navegación = SOLO LECTURA: nunca crea/repone onboardings. El backfill de ingresos históricos es un paso operativo explícito y separado
        # (`human_capital_service.reconcile_onboardings`, DEFERRED: Historical onboarding backfill = deferred operational step).
        rows = human_capital_service.list_onboardings_view(allow_sensitive)
    except (onboarding_repository.OnboardingStorageError, busqueda_repository.BusquedaStorageError) as exc:
        source_error = str(exc)
    dataset_has_any = bool(rows)
    area_options = _onboarding_area_filter_options(rows, area)         # sobre el dataset COMPLETO: las opciones no desaparecen al filtrar
    rows = human_capital_service.filter_onboardings(rows, estado=estado, area=area, q=q, include_offer=allow_sensitive)
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
    ctx = _onboarding_list_context(estado, area, q, page, page_size, human_capital_access.can_view_sensitive_data_request(request))
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
    allow = human_capital_access.can_view_sensitive_data_request(request)
    view = human_capital_service.onboarding_view(onboarding, allow) if onboarding else None
    if not allow:
        form_values = {**form_values, "oferta_cerrada": None}                      # sin permiso, la oferta (persistida o enviada) no viaja a la plantilla
    return templates.TemplateResponse(
        request=request, name="human_capital/onboarding_form.html",
        context=_onboarding_shell(request, _onboarding_form_context(
            form_values=form_values, onboarding=view, **extra)),
    )


@router.post("/cchh/onboarding/{application_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_onboarding_delete(request: Request, application_id: str):
    """«Eliminar onboarding» (diálogo corporativo de confirmación en el navegador). El servicio resuelve el vínculo real (application_id + búsqueda) y retira la postulación Ingreso de su búsqueda por el contrato vigente."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        human_capital_service.delete_onboarding(application_id, actor=_pipeline_actor(request))
    except (busqueda_repository.BusquedaNotFoundError, busqueda_repository.ApplicationNotFoundError, *_BUSQUEDA_SOLICITUD_ERRORS,
            *_BUSQUEDA_SHELL_ERRORS, *_ONBOARDING_SHELL_ERRORS):
        pass                                                                # la fila sigue en el listado (nada a medias: ver el servicio); misma convención silenciosa que Quitar del proceso
    return _return_redirect(request, "/cchh/onboarding")


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
    allow = human_capital_access.can_view_sensitive_data_request(request)
    form_values = {"fecha_ingreso": fecha_ingreso, "oferta_cerrada": oferta_cerrada if allow else None, "area": area}      # sin permiso, lo enviado se descarta antes de todo
    try:
        revision_int = int(expected_revision)
    except ValueError:
        raise HTTPException(400, "expected_revision inválido") from None
    fv = {**form_values, "expected_revision": revision_int, "application_id": application_id}
    try:
        human_capital_service.update_onboarding(application_id, revision_int, form_values, allow_sensitive=allow)
    except onboarding_repository.OnboardingNotFoundError:
        raise HTTPException(404, "Onboarding no encontrado") from None
    except ValueError as exc:
        return _onboarding_edit_response(request, application_id, fv, error=str(exc))
    except onboarding_repository.OnboardingConcurrentModificationError:
        return _onboarding_edit_response(request, application_id, fv, concurrent_modification=True)
    except _ONBOARDING_SHELL_ERRORS as exc:
        return _onboarding_edit_response(request, application_id, fv, error=str(exc))
    return _return_redirect(request, "/cchh/onboarding", subject=human_capital_service.get_onboarding(application_id), kinds=(pagination.CONTEXT_PIPELINE,))


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
        context={"request": request, "onboarding": human_capital_service.onboarding_view(onboarding, human_capital_access.can_view_sensitive_data_request(request)), "checklist_error": error},
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
    busquedas = sorted(human_capital_service.list_busquedas_gestionadas(), key=lambda b: b.get("fecha_apertura") or "", reverse=True)
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
        # El período ya no se elige: se deriva de la fecha. Una publicación histórica SIN fecha muestra su período guardado (sin asignarle una fecha) y pide fecha para guardarse.
        "periodo_legado": (publicacion or {}).get("periodo") if is_edit and not _fecha_valida(form_values.get("fecha")) else None,
    }


def _fecha_valida(value) -> bool:
    return bool(value) and human_capital_service._parse_fecha_publicacion(value) is not None


def _publicacion_form_from_request(busqueda_id, posicion, canal, cantidad_avisos, fecha, observacion) -> dict:
    return {"busqueda_id": busqueda_id, "posicion": posicion, "canal": canal, "cantidad_avisos": cantidad_avisos, "fecha": fecha, "observacion": observacion}


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
    form_values = {"fecha": human_capital_service.hoy_negocio().isoformat(), "cantidad_avisos": 1}          # hoy en la zona de negocio (Argentina); editable
    return templates.TemplateResponse(
        request=request, name="human_capital/publicacion_form.html",
        context=_publicaciones_shell(request, _publicacion_form_context(form_values=form_values, is_edit=False)),
    )


@router.post("/cchh/publicaciones/nueva", response_class=HTMLResponse, include_in_schema=False)
def human_capital_publicacion_new_submit(
    request: Request, busqueda_id: str = Form(""), posicion: str = Form(""), canal: str = Form(""), cantidad_avisos: str = Form(""),
    fecha: str = Form(""), observacion: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _publicacion_form_from_request(busqueda_id, posicion, canal, cantidad_avisos, fecha, observacion)

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
    fecha: str = Form(""), observacion: str = Form(""), expected_revision: str = Form(""),
):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    form_values = _publicacion_form_from_request(busqueda_id, posicion, canal, cantidad_avisos, fecha, observacion)
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
templates.env.filters["hc_profile_search"] = human_capital_service.profile_search_haystack          # haystack del buscador de candidatos (diálogo «Agregar candidato desde el Banco»)
# Contexto de listado para las páginas de edición (ver `pagination.list_return_context`): `{% set rc = list_return(request, "/cchh/…") %}` → `rc.back_url` / `rc.qs`.
templates.env.globals["list_return"] = lambda request, list_path, subject=None, kinds=None: _return_context(request.query_params, list_path, subject, kinds)


def _edit_redirect(request: Request, profile_id: str) -> RedirectResponse:
    """303 a la página de edición del candidato conservando `?return_to=` (Cancelar/Guardar siguen volviendo al listado correcto tras una operación de documentos)."""
    return RedirectResponse(url=f"/cchh/candidatos/{profile_id}/editar" + pagination.list_return_context(request.query_params, "/cchh/candidatos")["qs"], status_code=303)


def _process_context_matches(parts: dict | None, subject: dict | None) -> bool:
    """El contexto de proceso (`return_to` = Pipeline de una búsqueda o Evaluación CCHH de un candidato) solo vale si CORRESPONDE al registro que se está editando: la búsqueda existe (y, para la Evaluación, está
    en gestión), el registro pertenece a ESA búsqueda y a una postulación que sigue en su pipeline (la de la Evaluación, si el contexto la nombra). `subject`: dict con `busqueda_id` y `application_id`/`profile_id`
    del registro (entrevista, onboarding o alta en curso). Sin `subject` o con cualquier inconsistencia → no vale (se cae al destino independiente)."""
    if not parts or not subject or subject.get("busqueda_id") != parts["busqueda_id"]:
        return False
    try:
        busqueda = busqueda_repository.get(parts["busqueda_id"])
    except busqueda_repository.BusquedaStorageError:
        return False
    if busqueda is None or (parts["kind"] == pagination.CONTEXT_EVALUACION and not busqueda_repository.es_gestionada(busqueda)):
        return False
    apps = busqueda.get("candidatos") or []
    profile = subject.get("profile_id")
    wanted = parts["application_id"]
    if wanted:                                                                    # la Evaluación nombra una postulación concreta: debe seguir en el pipeline y ser del MISMO candidato que el registro
        app = next((a for a in apps if a.get("application_id") == wanted), None)
        if app is None:
            return False
        return app.get("profile_id") == profile if profile else (not subject.get("application_id") or subject["application_id"] == wanted)
    sub_app = subject.get("application_id")
    app = next((a for a in apps if a.get("application_id") == sub_app), None) if sub_app else None
    if app is None and profile:                                                   # la entrevista es única por (candidato, proceso): si el candidato fue quitado y vuelto a agregar conserva el `application_id` anterior; vale por candidato
        app = next((a for a in apps if a.get("profile_id") == profile), None)
    if app is None:
        return False
    return not profile or app.get("profile_id") == profile


def _return_context(query_params, list_path: str, subject: dict | None = None, kinds=None) -> dict:
    """`pagination.list_return_context` + validación EN SERVIDOR del contexto de proceso contra el registro (`subject`). Las pantallas de edición independientes sin `subject` solo aceptan su listado.
    `kinds`: orígenes de proceso admitidos por la pantalla (default: Pipeline y Evaluación CCHH; el Onboarding solo se abre desde el Pipeline)."""
    ctx = pagination.list_return_context(query_params, list_path, allow_process=subject is not None)
    if ctx["kind"] and (ctx["kind"] not in (kinds or (pagination.CONTEXT_PIPELINE, pagination.CONTEXT_EVALUACION))
                        or not _process_context_matches(pagination.process_context(ctx["return_to"]), subject)):
        return pagination.list_return_context({}, list_path)
    return ctx


def _return_redirect(request: Request, list_path: str, *, subject: dict | None = None, kinds=None) -> RedirectResponse:
    """303 al listado EN EL MISMO CONTEXTO (filtros/página/tamaño) si la acción vino de él (`?return_to=`); si no, al listado base. Con `subject`, también vuelve al Pipeline/Evaluación de origen (validado)."""
    return RedirectResponse(url=_return_context(request.query_params, list_path, subject, kinds)["return_to"] or list_path, status_code=303)


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
    return templates.TemplateResponse(request=request, name=f"partials/human_capital_indicadores_{tarjeta}.html", context={"request": request, tarjeta: data, "source_error": source_error, "hx_oob": True})


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


# ── DIAGNÓSTICO SharePoint (CV upload) — sonda de SOLO LECTURA, sólo APP_OWNER ────────────────────────────────────────────────────────
# Instrumento temporal del troubleshooting «CV upload → SharePoint»: con el MISMO token delegado que usa el uploader (`dwHumanCapitalDocumentWriteToken`, scopes base + HC_DOCUMENT_WRITE_SCOPE)
# ejecuta SÓLO GET contra el destino actual (config) y, opcionalmente, contra un destino de CONTROL descubierto desde un sharing link. No escribe nada, no persiste ni loguea el token.
# Ver `webapp/services/sharepoint_graph_diagnostics.py`. NO está en el nav.

def _require_owner_for_diagnostics(request: Request):
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    if not app_access.is_owner(request):
        raise HTTPException(403, "Sólo el dueño de la app puede usar el diagnóstico de SharePoint")
    return None


@router.get("/cchh/diagnostico/sharepoint", response_class=HTMLResponse, include_in_schema=False)
def human_capital_sharepoint_diagnostics_page(request: Request):
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    ctx = {
        "current_workspace": workspaces.HUMAN_CAPITAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK, "user": user, "active_page": "diagnostico",
        "current_target": sharepoint_graph_diagnostics.current_target(),
        "write_scope": config.HC_DOCUMENT_WRITE_SCOPE, "client_id": config.AZURE_CLIENT_ID,
        "write_phrase": sharepoint_graph_diagnostics.WRITE_TEST_PHRASE,
    }
    return templates.TemplateResponse(request=request, name="human_capital/diagnostico_sharepoint.html", context=ctx)


@router.post("/cchh/diagnostico/sharepoint/sonda", include_in_schema=False)
def human_capital_sharepoint_diagnostics_probe(request: Request, graph_token: str = Form(""), control_url: str = Form("")):
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not graph_token:
        return JSONResponse({"ok": False, "error": "Sin token de Microsoft 365 (el navegador no pudo obtenerlo)."}, status_code=400, headers={"Cache-Control": "no-store"})
    result = sharepoint_graph_diagnostics.run_probe(graph_token, control_sharing_url=control_url)
    return JSONResponse({"ok": True, **result}, headers={"Cache-Control": "no-store"})


@router.post("/cchh/diagnostico/sharepoint/inventario", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_inventory(request: Request, graph_token: str = Form(""), share_url: str = Form("")):
    """INVENTARIO de SOLO LECTURA del histórico de CVs (ver `cv_history_inventory`): resuelve el link y recorre la carpeta con GET; muestra agregados (sin nombres de archivo ni datos personales),
    no descarga, no guarda y no crea nada. CCHH autorizado (APP_OWNER o HUMAN_CAPITAL_ALLOWED_USERS). El token de LECTURA lo inyecta el hook compartido (`data-dw-graph-token`): jamás viaja en la plantilla ni se registra."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect

    def _render(inv: dict):
        # SIEMPRE 200: HTMX no reemplaza el contenido ante 4xx/5xx, y el estado de error ES el contenido que la persona tiene que ver (el `data-error-kind` lo distingue).
        return templates.TemplateResponse(request=request, name="partials/cv_history_inventory_result.html", context={"request": request, "inv": inv},
                                          headers={"Cache-Control": "no-store"})

    if not graph_token:
        return _render({"error": {"kind": "missing_token", "message": "Sin token de Microsoft 365 (el navegador no pudo obtenerlo). Volvé a iniciar sesión."}})
    try:
        result = cv_history_inventory.run_inventory(graph_token, share_url)
    except cv_history_inventory.InventoryInputError as exc:
        return _render({"error": {"kind": "invalid_link", "message": str(exc)}})
    if not result.get("ok"):
        kind = (result.get("errors") or [{}])[0].get("kind", "unresolved")
        message = {"not_a_folder": "El link no es una carpeta: pegá el link de la carpeta histórica."}.get(kind, "No se pudo resolver el link a una carpeta de SharePoint con tus permisos actuales.")
        return _render({"error": {"kind": kind, "message": message}})
    return _render(result)


# ── PILOTO de extracción mínima y conciliación (incremento 2 de la importación histórica de CVs) — sólo APP_OWNER, fuera del nav ───────────────────────────────────────────────────────────────
# Descarga una muestra chica, extrae localmente nombre/apellido/email/celular y concilia contra el Banco. NO crea perfiles, NO copia a `CVs`, NO usa servicios externos. Los datos personales extraídos solo se
# muestran en la vista de detalle del owner (`no-store`); el resto de las respuestas son conteos. Ver `webapp/services/cv_history_pilot.py`.
_NO_STORE = {"Cache-Control": "no-store"}


def _pilot_context(request: Request, **extra) -> dict:
    user = auth_service.session_user(request)
    return {"current_workspace": workspaces.HUMAN_CAPITAL, "available_workspaces": workspaces.available_workspaces(user), "pack": DEFAULT_PACK, "user": user, "active_page": "diagnostico",
            "authorization_phrase": cv_history_pilot.AUTHORIZATION_PHRASE, "sample_min": cv_history_pilot.SAMPLE_MIN, "sample_max": cv_history_pilot.SAMPLE_MAX, **extra}


def _pilot_panel(request: Request, run_id: str, notice: dict | None = None):
    """Panel de un lote (conteos, sin datos personales). SIEMPRE 200: HTMX no pinta 4xx y el aviso ES el contenido."""
    try:
        report = cv_history_pilot.run_report(run_id)
    except cv_history_manifest.ManifestError:
        raise HTTPException(404, "Lote inexistente") from None
    execution = cv_history_import_exec.execution_report(run_id) if report.get("kind") == "import" else None
    discard_block = cv_history_import_exec.discard_blockers(run_id) if report.get("kind") == "import" else None
    return templates.TemplateResponse(request=request, name="partials/cv_history_run_panel.html", context={"request": request, "report": report, "notice": notice, "run_id": run_id, "execution": execution, "import_phrase": cv_history_import_exec.confirm_phrase(run_id),
                                                                                                            "discard_block": discard_block, "discard_phrase": cv_history_pilot.discard_phrase(run_id)}, headers=_NO_STORE)


def _pilot_actor(request: Request) -> str:
    """El actor autenticado es el claim `sub` de la sesión (no existe un claim `email`): queda en la autorización del lote y en la autoría de cada dictamen."""
    return str((auth_service.session_user(request) or {}).get("sub") or "").strip().lower()


@router.get("/cchh/diagnostico/cvs-historicos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_page(request: Request):
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    runs = [{"run_id": r["run_id"], "state": r.get("state"), "created_at": r.get("created_at"), "counters": r.get("counters") or {}, "kind": r.get("kind", "pilot"), "derived_from_run_id": (r.get("derived_from") or {}).get("run_id")} for r in cv_history_manifest.list_runs()]
    return templates.TemplateResponse(request=request, name="human_capital/cv_history_pilot.html", context=_pilot_context(request, runs=runs), headers=_NO_STORE)


@router.post("/cchh/diagnostico/cvs-historicos/piloto", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_start(request: Request, graph_token: str = Form(""), share_url: str = Form(""), sample_size: str = Form(""), authorization: str = Form("")):
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect

    def _notice(kind: str, message: str):
        return templates.TemplateResponse(request=request, name="partials/cv_history_run_panel.html", context={"request": request, "report": None, "run_id": None, "notice": {"kind": kind, "message": message}}, headers=_NO_STORE)

    if not graph_token:
        return _notice("missing_token", "Sin token de Microsoft 365 (el navegador no pudo obtenerlo). Volvé a iniciar sesión.")
    try:
        size = int(sample_size)
    except ValueError:
        return _notice("invalid_sample", f"La muestra inicial es de {cv_history_pilot.SAMPLE_MIN} a {cv_history_pilot.SAMPLE_MAX} documentos.")
    try:
        run = cv_history_pilot.start_run(graph_token, share_url, sample_size=size, authorization=authorization, actor=_pilot_actor(request))
    except (cv_history_pilot.PilotInputError, cv_history_inventory.InventoryInputError) as exc:
        return _notice("invalid_input", str(exc))
    except cv_history_manifest.ManifestError:
        return _notice("persistence_error", "No se pudo guardar el manifiesto del lote (persistencia no confirmada). No se descargó nada.")
    return _pilot_panel(request, run["run_id"], {"kind": "created", "message": "Lote creado. Todavía no se descargó ningún documento: usá «Procesar muestra»."})


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/procesar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_process(request: Request, run_id: str, graph_token: str = Form(""), batch: str = Form("")):
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    if not graph_token:
        return _pilot_panel(request, run_id, {"kind": "missing_token", "message": "Sin token de Microsoft 365 (el navegador no pudo obtenerlo). Volvé a iniciar sesión."})
    try:
        if cv_history_manifest.get_run(run_id).get("kind") == cv_history_pilot.KIND_IMPORT:      # lote de importación: tandas configurables (documentos por tanda)
            result = cv_history_import.analyze(run_id, graph_token, max_documents=int(batch) if batch.strip().isdigit() else None)
        else:
            result = cv_history_pilot.process_run(run_id, graph_token)
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestError:
        return _pilot_panel(request, run_id, {"kind": "persistence_error", "message": "No se pudo guardar el avance (persistencia no confirmada). Reintentá: el proceso es reanudable."})
    return _pilot_panel(request, run_id, {"kind": "processed", "result": result})


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/descartar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_discard(request: Request, run_id: str, confirm: str = Form("")):
    """Eliminación EXPLÍCITA del área temporal con datos personales del lote (política de retención): exige la frase `ELIMINAR-DATOS-<run_id>`; la auditoría se conserva."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    try:
        cv_history_import_exec.assert_can_discard(run_id)
        cv_history_pilot.discard_staging(run_id, confirm=confirm, actor=_pilot_actor(request))
    except cv_history_pilot.PilotInputError as exc:
        return _pilot_panel(request, run_id, {"kind": "invalid_confirmation", "message": str(exc)})
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestError:
        return _pilot_panel(request, run_id, {"kind": "persistence_error", "message": "No se pudo completar la eliminación (borrado no confirmado). Nada se perdió: reintentá, es reanudable."})
    return _pilot_panel(request, run_id, {"kind": "discarded", "message": "Datos personales del lote eliminados. La auditoría y el reporte final se conservan."})


@router.post("/cchh/diagnostico/cvs-historicos/importacion", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_import_start(request: Request, graph_token: str = Form(""), share_url: str = Form(""), authorization: str = Form("")):
    """Crea un lote de IMPORTACIÓN (todos los contenidos del origen): inventario de solo lectura e índice. No descarga ni escribe en el Banco ni en SharePoint; el análisis se ejecuta por tandas."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect

    def _notice(kind: str, message: str):
        return templates.TemplateResponse(request=request, name="partials/cv_history_run_panel.html", context={"request": request, "report": None, "run_id": None, "notice": {"kind": kind, "message": message}}, headers=_NO_STORE)

    if not graph_token:
        return _notice("missing_token", "Sin token de Microsoft 365 (el navegador no pudo obtenerlo). Volvé a iniciar sesión.")
    try:
        run = cv_history_import.start_import(graph_token, share_url, actor=_pilot_actor(request), authorization=authorization)
    except (cv_history_pilot.PilotInputError, cv_history_inventory.InventoryInputError) as exc:
        return _notice("invalid_input", str(exc))
    except cv_history_manifest.ManifestError:
        return _notice("persistence_error", "No se pudo guardar el manifiesto del lote (persistencia no confirmada). No se descargó nada.")
    return _pilot_panel(request, run["run_id"], {"kind": "created", "message": f"Lote de importación creado: {run['counters']['selected']} contenidos únicos. Todavía no se analizó ni se escribió nada: usá «Analizar»."})


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/plan", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_import_plan(request: Request, run_id: str):
    """Genera el PLAN de importación (decisión por contenido) con el estado actual del Banco. Solo escribe en el manifiesto: ni el Banco ni SharePoint."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    try:
        cv_history_import.generate_plan(run_id, actor=_pilot_actor(request))
    except cv_history_pilot.PilotInputError as exc:
        return _pilot_panel(request, run_id, {"kind": "invalid_input", "message": str(exc)})
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestError:
        return _pilot_panel(request, run_id, {"kind": "persistence_error", "message": "No se pudo guardar el plan (persistencia no confirmada). No se modificó nada más."})
    return _pilot_panel(request, run_id, {"kind": "planned", "message": "Plan generado. Es solo una previsión: todavía no se creó ni se vinculó ningún perfil ni se subió ningún documento."})


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/importar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_import_run(request: Request, run_id: str, graph_token: str = Form(""), confirm: str = Form(""), batch: str = Form(""), plan_revision: str = Form(""), expect_profiles: str = Form(""), expect_documents: str = Form("")):
    """EJECUTA el plan de importación: crea/vincula perfiles en el Banco y sube los CV a la carpeta administrada de CVs, por tandas. El inicio exige la frase `IMPORTAR-<run_id>` y valida el permiso efectivo de
    escritura; después cada llamada es una tanda reanudable. Token con alcance de escritura (lo inyecta `human_capital_import.js`); los errores son 200 con aviso (HTMX no pinta 4xx)."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    if not graph_token:
        return _pilot_panel(request, run_id, {"kind": "missing_token", "message": "Sin token con permiso de escritura (el navegador no pudo obtenerlo). Usá «Autorizar escritura en CVs» y reintentá."})
    try:
        cv_history_import_exec.start_execution(run_id, graph_token, actor=_pilot_actor(request), confirm=confirm, expected={"revision": plan_revision, "profiles": expect_profiles, "documents": expect_documents})
        result = cv_history_import_exec.execute_batch(run_id, graph_token, actor=_pilot_actor(request), max_units=int(batch) if batch.strip().isdigit() else None)
    except cv_history_import_exec.ExecutionError as exc:
        return _pilot_panel(request, run_id, {"kind": "invalid_input", "message": str(exc)})
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestError:
        return _pilot_panel(request, run_id, {"kind": "persistence_error", "message": "No se pudo guardar el avance (persistencia no confirmada). Reintentá: la importación es reanudable y no duplica nada."})
    return _pilot_panel(request, run_id, {"kind": "imported", "result": result})


@router.get("/cchh/historicos", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_entry(request: Request):
    """Entrada de «Históricos sin validación» desde el menú de Capital Humano (CCHH autorizado: el esquema de siempre). Lleva a la importación histórica vigente —la más reciente cuyos datos temporales siguen disponibles— o, si
    no hay ninguna, muestra un aviso. No lee datos personales: solo mira el estado de los lotes."""
    redirect = _require_human_capital_session(request)
    if redirect is not None:
        return redirect
    try:
        runs = [r for r in cv_history_manifest.list_runs() if r.get("kind") == cv_history_pilot.KIND_IMPORT and (r.get("staging") or {}).get("state") == "PRESENT"]
    except cv_history_manifest.ManifestError:
        runs = []
    if runs:
        return RedirectResponse(url=f"/cchh/diagnostico/cvs-historicos/{runs[0]['run_id']}/excepciones", status_code=307)
    return templates.TemplateResponse(request=request, name="human_capital/cv_history_empty.html", context=_pilot_context(request, active_page="historicos"), headers=_NO_STORE)


@router.get("/cchh/diagnostico/cvs-historicos/{run_id}/excepciones", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_exceptions(request: Request, run_id: str, estado: str = Query("open"), motivo: str = Query(""), page: str = Query(""), page_size: str = Query("")):
    """Excepciones de la importación (pendientes del plan y bloqueados al ejecutar): contiene datos personales, solo CCHH autorizado, `no-store`. Paginación CANÓNICA del Hub (`pagination.paginate` + `partials/pager.html`):
    filtros ANTES de paginar; un filtro o el paginador dentro del panel (HTMX) re-renderizan solo el fragmento."""
    redirect = _require_human_capital_session(request)                                                              # CCHH autorizado (APP_OWNER o HUMAN_CAPITAL_ALLOWED_USERS): el esquema de siempre; lo sensible de la importación sigue siendo solo del owner
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    try:
        overview = cv_history_import_exec.exceptions_overview(run_id, status=estado, reason=motivo or None)
        pager = pagination.paginate(overview["rows"], page=page, page_size=page_size, base_url=f"/cchh/diagnostico/cvs-historicos/{run_id}/excepciones",
                                    params={"estado": estado if estado != "open" else "", "motivo": motivo})
        rows = cv_history_import_exec.exception_details(run_id, pager["items"]) if overview["staging_present"] else [{**r, "available": False, "ledger": {}, "suggestions": [], "alternatives": [], "emails": [], "specialties": []} for r in pager["items"]]
        cv_history_review.annotate(run_id, rows)                                                          # categoría de recuperabilidad y evidencia legible: SOLO de las filas de esta página
        cv_history_drafts.attach(run_id, rows)                                                            # borrador guardado (si lo hay) de las filas de esta página
        drafts_count = cv_history_drafts.count(run_id)
        execution = cv_history_import_exec.execution_report(run_id)
    except (cv_history_manifest.ManifestNotFoundError, cv_history_import_exec.ExecutionError):
        raise HTTPException(404, "Lote inexistente") from None
    context = {"run_id": run_id, "overview": overview, "pager": pager, "rows": rows, "execution": execution, "estado": estado, "motivo": motivo, "drafts_count": drafts_count}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/cv_history_exceptions_panel.html", context={"request": request, **context}, headers=_NO_STORE)
    return templates.TemplateResponse(request=request, name="human_capital/cv_history_exceptions.html", context=_pilot_context(request, active_page="historicos", is_owner=app_access.is_owner(request), **context), headers=_NO_STORE)


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/excepciones/{content_key}/{accion}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_exception_action(
    request: Request, run_id: str, content_key: str, accion: str, graph_token: str = Form(""), first_name: str = Form(""), last_name: str = Form(""), email: str = Form(""), phone: str = Form(""),
    duplicate_confirmed: str = Form(""), profile_id: str = Form(""), reason: str = Form(""), notes: str = Form(""), revision: str = Form(""), op: str = Form(""), target_revision: str = Form(""), fingerprint: str = Form(""),
    ack: list[str] = Form([]),
):
    """Gestiona UNA excepción: guardar borrador (Entrega B: nombre, apellido, email, teléfono y observaciones con control de revisión; NO crea, vincula ni modifica nada del Banco), revisar para aceptar (Entrega C: SOLO LECTURA,
    devuelve el panel de revisión) y aceptar (Entrega C: confirma una operación elegida —crear o vincular— sobre la revisión que vio el owner, por el circuito de `complete_identity`/`link_existing`), completar identidad (alta con
    nombre y apellido reales), vincular a un perfil existente (sin modificarlo), descartar, reabrir o reintentar. Responde la fila; los errores son 200 con aviso."""
    redirect = _require_human_capital_session(request)                                                              # CCHH autorizado (APP_OWNER o HUMAN_CAPITAL_ALLOWED_USERS): el esquema de siempre; lo sensible de la importación sigue siendo solo del owner
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id) or not cv_history_manifest.KEY_RE.match(content_key or "") or accion not in ("borrador", "aceptar-revisar", "aceptar", "completar", "vincular", "descartar", "reabrir", "reintentar"):
        raise HTTPException(404, "Excepción inexistente")
    try:
        cv_history_manifest.get_run(run_id)
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    actor = _pilot_actor(request)
    if accion == "aceptar-revisar":                                                                                      # SOLO LECTURA: calcula la revisión contra el Banco de este momento y devuelve el panel (no escribe nada)
        return templates.TemplateResponse(request=request, name="partials/cv_history_accept_panel.html", headers=_NO_STORE, context={"request": request, "run_id": run_id, "content_key": content_key,
                                          "pre": cv_history_acceptance.preflight(run_id, content_key), "ack_labels": cv_history_acceptance.ACK_LABELS, "signal_label": cv_history_acceptance.SIGNAL_LABEL,
                                          "strength_label": cv_history_acceptance.STRENGTH_LABEL})
    result: dict = {}
    draft_form: dict | None = None
    draft_revision: int | None = None
    accept_preview: dict | None = None
    try:
        if accion in ("borrador", "aceptar", "completar", "vincular") and not graph_token:
            result = {"ok": False, "error": "Sin token con permiso de escritura (el navegador no pudo obtenerlo). Usá «Autorizar escritura en CVs» y reintentá."}
            if accion == "borrador":
                draft_form = cv_history_drafts.clean({"first_name": first_name, "last_name": last_name, "email": email, "phone": phone, "notes": notes})
        elif accion == "borrador":
            draft_form = cv_history_drafts.clean({"first_name": first_name, "last_name": last_name, "email": email, "phone": phone, "notes": notes})
            try:
                saved = cv_history_drafts.save(run_id, content_key, draft_form, actor=actor, expected_revision=revision)
                result = {"ok": True, "draft_saved": True, "revision": saved["doc"]["revision"], "changed": saved["changed"], "created": saved["created"]}
                draft_form = None                                                                                      # lo guardado manda: la fila vuelve a mostrar el borrador vigente
            except cv_history_drafts.DraftConflict as conflict:
                result = {"ok": False, "draft_conflict": cv_history_drafts.conflict_view(conflict.current, draft_form)}
                draft_revision = int((conflict.current or {}).get("revision") or 0)                                    # al reenviar, el owner ya vio lo vigente
            except cv_history_drafts.DraftError as exc:
                result = {"ok": False, "error": str(exc)}
            except cv_history_manifest.ManifestNotFoundError:
                raise
            except cv_history_manifest.ManifestError:
                result = {"ok": False, "error": "No se pudo guardar el borrador (persistencia no confirmada). No se guardó nada: tus datos siguen en el formulario. Reintentá."}
        elif accion == "aceptar":
            result = cv_history_acceptance.confirm(run_id, content_key, graph_token, actor=actor, op=op, target_profile_id=profile_id, target_revision=target_revision, fingerprint=fingerprint, draft_revision=revision, acks=ack)
        elif accion == "completar":
            result = cv_history_import_exec.complete_identity(run_id, content_key, graph_token, actor=actor, first_name=first_name, last_name=last_name, email=email, phone=phone, duplicate_confirmed=duplicate_confirmed.strip().lower() == "true")
        elif accion == "vincular":
            result = cv_history_import_exec.link_existing(run_id, content_key, graph_token, actor=actor, profile_id=profile_id)
        elif accion == "descartar":
            result = cv_history_import_exec.dismiss(run_id, content_key, actor=actor, reason=reason)
        elif accion == "reabrir":
            result = cv_history_import_exec.reopen(run_id, content_key, actor=actor)
        else:
            result = cv_history_import_exec.retry(run_id, content_key, actor=actor)
    except cv_history_import_exec.ExecutionError as exc:
        result = {"ok": False, "error": str(exc)}
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestBusyError:
        result = {"ok": False, "error": "Otro proceso está trabajando con este contenido en este momento. No se guardó nada: reintentá en unos segundos."}
    except cv_history_manifest.ManifestError:
        result = {"ok": False, "error": "No se pudo guardar el cambio (persistencia no confirmada). Reintentá: no se duplicó nada."}
    if accion == "aceptar" and not result.get("accepted"):                                                              # sin aceptación: la fila vuelve con la revisión VIGENTE para decidir de nuevo
        accept_preview = result.get("preflight") or cv_history_acceptance.preflight(run_id, content_key)
    row = cv_history_import_exec.exception_row(run_id, content_key)
    cv_history_review.annotate(run_id, [row])
    cv_history_drafts.attach(run_id, [row])
    headers = dict(_NO_STORE)
    if result.get("draft_saved"):
        headers["HX-Trigger"] = '{"cvexc-drafts": %d}' % cv_history_drafts.count(run_id)                      # el contador «Con borrador guardado» del panel se actualiza sin recargar (lo escucha `human_capital_import.js`)
    return templates.TemplateResponse(request=request, name="partials/cv_history_exception_row.html", context={"request": request, "r": row, "run_id": run_id, "result": result, "accion": accion, "form": {} if accion == "borrador" else {"first_name": first_name, "last_name": last_name, "email": email, "phone": phone},
                                                                                                                 "draft_form": draft_form, "draft_revision": draft_revision, "accept_preview": accept_preview, "ack_labels": cv_history_acceptance.ACK_LABELS,
                                                                                                                 "signal_label": cv_history_acceptance.SIGNAL_LABEL, "strength_label": cv_history_acceptance.STRENGTH_LABEL, "dismiss_reasons": cv_history_import_exec.DISMISS_REASONS, "content_key": content_key}, headers=headers)


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/cv-fuente/{content_key}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_original_cv(request: Request, run_id: str, content_key: str, graph_token: str = Form("")):
    """«Ver CV original»: resuelve con Microsoft Graph el enlace de SharePoint del CV de una excepción, con el token DELEGADO del usuario (sus propios permisos; lo inyecta `hub_auth.js` vía `data-dw-graph-token`).
    SOLO LECTURA: no descarga ni copia el archivo y no guarda la URL; la respuesta es `no-store`. POST para que el token viaje en el cuerpo y no en la URL; los errores de Graph son 200 con un aviso claro (HTMX no pinta 4xx)."""
    redirect = _require_human_capital_session(request)                                                              # CCHH autorizado (APP_OWNER o HUMAN_CAPITAL_ALLOWED_USERS): el esquema de siempre; lo sensible de la importación sigue siendo solo del owner
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id) or not cv_history_manifest.KEY_RE.match(content_key or ""):
        raise HTTPException(404, "Excepción inexistente")
    try:
        result = cv_history_review.resolve_original(run_id, content_key, graph_token)
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    return templates.TemplateResponse(request=request, name="partials/cv_history_original_link.html", context={"request": request, "result": result, "content_key": content_key}, headers=_NO_STORE)


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/reevaluar", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_rearbitrate(request: Request, run_id: str):
    """Crea un lote DERIVADO que reevalúa la identidad con las reglas vigentes SOLO desde lo ya guardado: no descarga ni lee documentos (sin token de Graph) y no modifica el lote original."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    try:
        derived = cv_history_pilot.start_rearbitration(run_id, actor=_pilot_actor(request))
    except cv_history_pilot.PilotInputError as exc:
        return _pilot_panel(request, run_id, {"kind": "invalid_input", "message": str(exc)})
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestError:
        return _pilot_panel(request, run_id, {"kind": "persistence_error", "message": "No se pudo guardar el lote derivado (persistencia no confirmada). El lote original no se modificó."})
    return _pilot_panel(request, derived["run_id"], {"kind": "created", "message": "Lote derivado creado desde los datos ya guardados (no se leyó ningún documento). El lote original no se modificó."})


@router.post("/cchh/diagnostico/cvs-historicos/{run_id}/veredicto", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_verdict(request: Request, run_id: str, content_key: str = Form(""), status: str = Form(""), first_name: str = Form(""), last_name: str = Form("")):
    """Dictamen del owner sobre un documento del lote (propuesta correcta / corrección manual / identidad no determinada). Responde la celda del documento y refresca el panel de métricas por OOB; los errores
    son 200 con aviso en la celda (HTMX no pinta 4xx)."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id) or not cv_history_manifest.KEY_RE.match(content_key or ""):
        raise HTTPException(404, "Lote inexistente")
    error = None
    try:
        cv_history_pilot.record_verdict(run_id, content_key, status, actor=_pilot_actor(request), first_name=first_name, last_name=last_name)
    except cv_history_pilot.PilotInputError as exc:
        error = str(exc)
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    except cv_history_manifest.ManifestError:
        error = "No se pudo guardar el dictamen (persistencia no confirmada). Reintentá."
    row = next((r for r in cv_history_pilot.review_view(run_id) if r["content_key"] == content_key), None)
    if row is None:
        raise HTTPException(404, "Documento inexistente")
    return templates.TemplateResponse(request=request, name="partials/cv_history_verdict_cell.html", context={"request": request, "r": row, "run_id": run_id, "verdict_error": error, "oob_report": cv_history_pilot.run_report(run_id), "discard_phrase": cv_history_pilot.discard_phrase(run_id)}, headers=_NO_STORE)


@router.get("/cchh/diagnostico/cvs-historicos/{run_id}", response_class=HTMLResponse, include_in_schema=False)
def human_capital_cv_history_detail(request: Request, run_id: str):
    """Vista del owner con los resultados INDIVIDUALES (contiene datos personales: solo APP_OWNER, `no-store`, fuera del nav)."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    if not cv_history_manifest.RUN_ID_RE.match(run_id):
        raise HTTPException(404, "Lote inexistente")
    try:
        report = cv_history_pilot.run_report(run_id)
        rows = cv_history_pilot.review_view(run_id)
    except cv_history_manifest.ManifestNotFoundError:
        raise HTTPException(404, "Lote inexistente") from None
    execution = cv_history_import_exec.execution_report(run_id) if report.get("kind") == "import" else None
    discard_block = cv_history_import_exec.discard_blockers(run_id) if report.get("kind") == "import" else None
    return templates.TemplateResponse(request=request, name="human_capital/cv_history_run.html", context=_pilot_context(request, report=report, rows=rows, run_id=run_id, notice=None, execution=execution, import_phrase=cv_history_import_exec.confirm_phrase(run_id),
                                                                                                                       discard_block=discard_block, discard_phrase=cv_history_pilot.discard_phrase(run_id)), headers=_NO_STORE)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_local_request(request: Request) -> bool:
    """Exclusivamente localhost: el Host Y la direccion del cliente deben ser loopback (detras del proxy de DigitalOcean ninguna de las dos lo es)."""
    return (request.url.hostname or "").lower() in _LOCAL_HOSTS and (request.client.host if request.client else "") in _LOCAL_HOSTS


@router.post("/cchh/diagnostico/sharepoint/escritura", include_in_schema=False)
def human_capital_sharepoint_diagnostics_write_test(request: Request, graph_token: str = Form(""), control_url: str = Form(""), confirm: str = Form("")):
    """Prueba de escritura CONTROLADA: un PUT de un archivo de texto inocuo y de nombre unico en la carpeta de CONTROL. Solo APP_OWNER, solo localhost y con la frase de confirmacion exacta."""
    redirect = _require_owner_for_diagnostics(request)
    if redirect is not None:
        return redirect
    no_store = {"Cache-Control": "no-store"}
    if not _is_local_request(request):
        return JSONResponse({"ok": False, "error": "La prueba de escritura solo está disponible en localhost."}, status_code=403, headers=no_store)
    if (confirm or "").strip() != sharepoint_graph_diagnostics.WRITE_TEST_PHRASE:
        return JSONResponse({"ok": False, "error": f"Falta la confirmación explícita: escribí exactamente «{sharepoint_graph_diagnostics.WRITE_TEST_PHRASE}»."}, status_code=400, headers=no_store)
    if not graph_token:
        return JSONResponse({"ok": False, "error": "Sin token de Microsoft 365 (el navegador no pudo obtenerlo)."}, status_code=400, headers=no_store)
    return JSONResponse(sharepoint_graph_diagnostics.write_test(graph_token, control_url), headers=no_store)
