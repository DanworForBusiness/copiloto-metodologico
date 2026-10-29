"""
commercial_presentations.py — Gestión Comercial → Presentaciones HTTP (V1).

DOS contratos de lectura/escritura, DELIBERADAMENTE separados (ver `webapp.services.commercial_presentations`):

  R1  GET  /presentaciones/{id}                      ver / presentar por URL — sesión + `auth_service.is_authorized` (ALLOWED_USERS ∪ ACADEMY_USERS). NO exige Comercial.
  R2  GET  /comercial/presentaciones                 catálogo                              — Comercial
  R3  GET  /comercial/presentaciones/{id}            detalle (shell del Hub + iframe de R1) — Comercial
  R4  POST /comercial/presentaciones/{id}/copiar     copia como instancia propia            — Comercial (NO requiere nada más; cualquier presentación se puede copiar)
  R5  GET  /comercial/presentaciones/{id}/editar     formulario de edición                  — Comercial + poder editar ESA instancia
  R6  POST /comercial/presentaciones/{id}            guardar campos                         — ídem R5
  R7  POST /comercial/presentaciones/{id}/eliminar   eliminar la instancia                  — Comercial + (responsable O APP_OWNER); nunca la semilla
  R8  GET  /presentaciones/{id}/logo                 logo propio de una copia               — contrato de lectura de R1
  R9  POST /comercial/presentaciones/{id}/qr         reemplazar / quitar el QR de la copia  — ídem R6 (multipart: revision + file | remove)
  R10 GET  /presentaciones/{id}/qr                   QR propio de una copia                 — contrato de lectura de R1
  R11 GET  /comercial/componentes/vista              vista previa de un componente (renderer real) — Comercial + visibilidad del componente
  R12 POST /comercial/componentes/duplicar           duplicar un componente como propio     — Comercial + verlo
  R13 POST /comercial/componentes/{id}               editar nombre, descripción y parámetros — Comercial + (responsable O CONTENT_OWNER)
  R14 POST /comercial/componentes/{id}/eliminar      eliminar un componente propio          — ídem R13

La URL de R1 es una DIRECCIÓN de acceso autenticado: no es una credencial ni un permiso. No hay acceso anónimo, tokens ni permisos por destinatario. No existe ninguna ruta para administrar templates o seeds.

Orden de enforcement de R6 (autoritativo; el botón oculto NUNCA es la defensa): 401 sin sesión → 403 no Comercial → 404 no existe → 403 no puede editarla (incluye seeds) → 403 origen no verificado
→ 400 JSON/revision → 422 validación → 409 revisión desactualizada (sin escribir) → 503 persistencia (con compensación). R4 sigue el mismo orden sin el paso de "poder editar".
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from core.content_registry import DEFAULT_PACK
from webapp import commercial_access, same_origin_guard, workspaces
from webapp.services import auth_service
from webapp.services import commercial_presentations as presentations
from webapp.services.activity_context import AREA_PRESENTATIONS, EVENT_MODULE_VIEWED, SCOPE_COMMERCIAL
from webapp.services.activity_service import log_activity
from webapp.services.module_registry import CM03_MODULE_ID
from webapp.templating import templates

router = APIRouter()
_logger = logging.getLogger("danwor.commercial_presentations")

_NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


# ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _require_commercial_page(request: Request):
    """Páginas HTML de Comercial: sin sesión → login (303); sin Comercial → 403. Mismo patrón que `commercial_strategy._require_commercial_session`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not commercial_access.is_commercial_authorized(request):
        raise HTTPException(403, "No autorizado para Comercial")
    return None


def _require_commercial_write(request: Request) -> dict:
    """POST de Comercial: 401 sin sesión, 403 sin Comercial."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not commercial_access.is_commercial_authorized_user(user):
        raise HTTPException(403, "No autorizado para Comercial")
    return user


def _load(presentation_id: str) -> dict:
    """Vista de la presentación o el HTTPException que corresponde. 404 = no existe; 409 = existe pero su template es de una versión más nueva que la soportada."""
    try:
        return presentations.get(presentation_id)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Presentación no encontrada")
    except presentations.PresentationUnavailable as exc:
        raise HTTPException(409 if exc.reason == "newer_version" else 404, "Presentación no disponible")
    except presentations.PresentationStorageError:
        raise HTTPException(503, "Presentaciones no disponibles")


def _date_dmy(value) -> str:
    """Fecha ISO → DD-MM-YYYY (solo presentación; la persistencia sigue en ISO)."""
    parts = str(value or "")[:10].split("-")
    return "-".join(reversed(parts)) if len(parts) == 3 and all(parts) else ""


def _share_path(presentation_id: str) -> str:
    return f"/presentaciones/{presentation_id}"


def _shell_context(request: Request, user: dict, **extra) -> dict:
    return {
        "current_workspace": workspaces.COMMERCIAL,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK,
        "user": user,
        "active_page": "presentaciones",
        "dw_module_id": CM03_MODULE_ID,
        **extra,
    }


async def _json_body(request: Request) -> dict:
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON inválido")
    return body


def _storage_response(exc: Exception) -> JSONResponse:
    """Contrato de errores de persistencia del Hub (mismo vocabulario que Estrategia): 503, nunca un éxito presumido."""
    if isinstance(exc, presentations.PresentationConfigIncomplete):
        return JSONResponse({"state": "unavailable", "reason": "config_incomplete", "message": str(exc)}, status_code=503)
    if isinstance(exc, presentations.RemoteOutcomeUnknown):
        return JSONResponse({"state": "unavailable", "reason": "outcome_unknown", "message": str(exc)}, status_code=503)
    if isinstance(exc, presentations.DurablePushFailed):
        return JSONResponse({"state": "unavailable", "reason": "durable_push_failed",
                             "message": "No se pudo guardar de forma durable — la versión anterior no fue modificada. Reintentá en unos minutos; tus cambios en pantalla no se perdieron."},
                            status_code=503)
    return JSONResponse({"state": "unavailable", "reason": "storage_error", "message": "No se pudo acceder al almacenamiento de presentaciones."}, status_code=503)


# ── R1 — ver / presentar por URL (NO exige Comercial) ─────────────────────────────────────────────────────────────────────

@router.get("/presentaciones/{presentation_id}", response_class=HTMLResponse, include_in_schema=False)
def presentation_shared_view(request: Request, presentation_id: str, embed: str = "", edit: str = ""):
    user = auth_service.session_user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    if not presentations.can_read_shared_user(user):
        raise HTTPException(403, "No autorizado")
    view = _load(presentation_id)
    embedded = embed == "1"
    if not embedded:                                                             # el detalle (iframe embed=1) ya registró la vista
        log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID)
    spec = presentations.template_spec(view["template_id"])
    theme = "dark" if request.cookies.get("dw-content-theme") == "dark" else "light"            # preferencia del Hub (cookie `dw-content-theme`), como /pack-assets: sin flash
    # Edición inline: `?edit=1` solo se honra si el usuario PUEDE editar esta instancia (Comercial + responsable / CONTENT_OWNER; nunca la semilla). Para cualquier otro es la vista de siempre: leer por URL no habilita nada.
    edit_mode = edit == "1" and embedded and presentations.can_edit_presentation_user(user, view)
    library = presentations.library_for(user, view.get("blocks")) if edit_mode else None      # institucionales + SUS componentes propios + los snapshots en uso: el filtro de visibilidad es del servidor
    return templates.TemplateResponse(request=request, name=spec.jinja, context=presentations.render_context(view, embed=embedded, theme=theme, edit=edit_mode, library=library), headers=_NO_STORE)


# ── R8 — logo propio de una instancia (mismo contrato de lectura que R1) ───────────────────────────────────────────────

@router.get("/presentaciones/{presentation_id}/logo", include_in_schema=False)
def presentation_logo(request: Request, presentation_id: str):
    user = auth_service.session_user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    if not presentations.can_read_shared_user(user):
        raise HTTPException(403, "No autorizado")
    _load(presentation_id)
    try:
        data = presentations.read_logo(presentation_id)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Esta presentación no tiene logo propio")
    except presentations.PresentationStorageError:
        raise HTTPException(503, "Presentaciones no disponibles")
    return Response(content=data, media_type="image/png", headers={**_NO_STORE, "Content-Security-Policy": "default-src 'none'; sandbox"})


# ── R10 — QR propio de una instancia (mismo contrato de lectura que R1/R8) ──────────────────────────────────────────────

@router.get("/presentaciones/{presentation_id}/qr", include_in_schema=False)
def presentation_qr(request: Request, presentation_id: str):
    user = auth_service.session_user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    if not presentations.can_read_shared_user(user):
        raise HTTPException(403, "No autorizado")
    _load(presentation_id)
    try:
        data = presentations.read_qr(presentation_id)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Esta presentación no tiene QR propio")
    except presentations.PresentationStorageError:
        raise HTTPException(503, "Presentaciones no disponibles")
    return Response(content=data, media_type="image/png", headers={**_NO_STORE, "Content-Security-Policy": "default-src 'none'; sandbox"})


# ── R2 — catálogo ─────────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/comercial/presentaciones", response_class=HTMLResponse, include_in_schema=False)
def presentations_catalog(request: Request):
    redirect = _require_commercial_page(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID)
    try:
        items = presentations.list_presentations()
        error_state = ""
    except presentations.PresentationStorageError:
        items, error_state = [], "unavailable"
    titles = {item["presentation_id"]: item["title"] for item in items}
    rows = [{**item, "can_edit": presentations.can_edit_presentation_user(user, item), "can_delete": presentations.can_delete_presentation_user(user, item), "share_path": _share_path(item["presentation_id"]),
             "updated_label": _date_dmy(item.get("updated_at")),
             "copied_from_title": titles.get((item.get("copied_from") or {}).get("presentation_id"), "")} for item in items]
    try:
        components = [{**c, "updated_label": _date_dmy(c.get("updated_at"))} for c in presentations.catalog_components(user)]
    except presentations.PresentationStorageError:
        components = []
    return templates.TemplateResponse(
        request=request, name="commercial/presentations.html",
        context=_shell_context(request, user, rows=rows, components=components, error_state=error_state, max_title=presentations.MAX_TITLE, max_desc=presentations.MAX_COMPONENT_DESC), headers=_NO_STORE,
    )


# ── R3 — detalle ──────────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/comercial/presentaciones/{presentation_id}", response_class=HTMLResponse, include_in_schema=False)
def presentation_detail(request: Request, presentation_id: str):
    redirect = _require_commercial_page(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    view = _load(presentation_id)
    log_activity(EVENT_MODULE_VIEWED, user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID)
    return templates.TemplateResponse(
        request=request, name="commercial/presentation_detail.html",
        context=_shell_context(request, user, presentation={**view, "updated_label": _date_dmy(view.get("updated_at"))}, share_path=_share_path(presentation_id),
                               can_edit=presentations.can_edit_presentation_user(user, view), can_delete=presentations.can_delete_presentation_user(user, view),
                               max_title=presentations.MAX_TITLE),
        headers=_NO_STORE,
    )


# ── R5 — formulario de edición ────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/comercial/presentaciones/{presentation_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def presentation_edit_form(request: Request, presentation_id: str):
    redirect = _require_commercial_page(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    view = _load(presentation_id)
    if not presentations.can_edit_presentation_user(user, view):
        raise HTTPException(403, "No autorizado para editar esta presentación")
    return templates.TemplateResponse(
        request=request, name="commercial/presentation_edit.html",
        context=_shell_context(request, user, presentation=view, share_path=_share_path(presentation_id)), headers=_NO_STORE,
    )


# ── R4 — copiar como instancia propia ────────────────────────────────────────────────────────────────────────────────────

@router.post("/comercial/presentaciones/{presentation_id}/copiar", include_in_schema=False)
async def presentation_copy(request: Request, presentation_id: str):
    user = _require_commercial_write(request)
    _load(presentation_id)                                                       # 404 / 409 antes de cualquier otra cosa
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    logo = None
    if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):      # el diálogo envía título + logo opcional; el JSON `{title}` sigue siendo válido
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > presentations.MAX_LOGO_BYTES + 64 * 1024:
            raise HTTPException(413, "El logo supera 1 MB: exportalo más liviano.")
        form = await request.form()
        body = {"title": form.get("title")}
        upload = form.get("logo")
        if upload is not None and getattr(upload, "filename", "") and hasattr(upload, "read"):
            logo = await upload.read(presentations.MAX_LOGO_BYTES + 1)
            if not logo:
                raise HTTPException(422, "El archivo de logo está vacío.")
    else:
        body = await _json_body(request)
    owner_id = str(user.get("sub") or "")
    owner_label = str(user.get("name") or user.get("username") or user.get("email") or owner_id)
    try:
        created = presentations.copy_presentation(presentation_id, title=body.get("title"), owner_id=owner_id, owner_label=owner_label, logo=logo)
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    except presentations.QuotaExceeded:
        return JSONResponse({"state": "quota", "message": "Se alcanzó el máximo de presentaciones. Pedí que se retire alguna antes de crear otra."}, status_code=409)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Presentación no encontrada")
    except presentations.PresentationUnavailable:
        raise HTTPException(409, "Presentación no disponible")
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.copy failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("presentation.copied", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID, revision=created["revision"])
    return JSONResponse({"state": "ok", "presentation_id": created["presentation_id"], "url": f"/comercial/presentaciones/{created['presentation_id']}"})


# ── R9 — reemplazar / quitar el QR de la copia ───────────────────────────────────────────────────────────────────────────

@router.post("/comercial/presentaciones/{presentation_id}/qr", include_in_schema=False)
async def presentation_qr_set(request: Request, presentation_id: str):
    user = _require_commercial_write(request)
    view = _load(presentation_id)
    if not presentations.can_edit_presentation_user(user, view):                 # incluye seeds (inmutables para TODOS)
        raise HTTPException(403, "No autorizado para editar esta presentación")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > presentations.MAX_LOGO_BYTES + 64 * 1024:
        raise HTTPException(413, "El QR supera 1 MB: exportalo más liviano.")
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise HTTPException(400, "Se esperaba un formulario con la revisión y el archivo del QR")
    form = await request.form()
    revision = str(form.get("revision") or "")
    if not revision.isdigit():
        raise HTTPException(400, "Falta 'revision' (entero) en el formulario")
    data = None
    if str(form.get("remove") or "") != "1":
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", "") or not hasattr(upload, "read"):
            raise HTTPException(422, "Elegí un archivo PNG para el QR.")
        data = await upload.read(presentations.MAX_LOGO_BYTES + 1)
        if not data:
            raise HTTPException(422, "El archivo del QR está vacío.")
    updated_by_id = str(user.get("sub") or "")
    updated_by_label = str(user.get("name") or user.get("username") or user.get("email") or updated_by_id)
    try:
        saved = presentations.set_qr(presentation_id, data, expected_revision=int(revision), updated_by_id=updated_by_id, updated_by_label=updated_by_label)
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    except presentations.StaleRevision as exc:
        return JSONResponse({"state": "stale", "current_revision": exc.current_revision,
                             "message": "La presentación cambió desde que la cargaste — recargá la página antes de volver a guardar. Nada se sobrescribió."}, status_code=409)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Presentación no encontrada")
    except presentations.PresentationUnavailable:
        raise HTTPException(409, "Presentación no disponible")
    except presentations.SeedIsReadOnly:
        raise HTTPException(403, "Las presentaciones institucionales no se editan")
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.qr failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("presentation.saved", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID, revision=saved["revision"])
    ctx = presentations.render_context(saved, edit=True)
    return JSONResponse({"state": "ok", "revision": saved["revision"], "qr_url": ctx["qr_url"], "qr_custom": ctx["qr_custom"]})


# ── Colección «Componentes reutilizables» (R11–R14) ──────────────────────────────────────────────────────────────────────

def _load_component(user: dict, component_id: str) -> dict:
    """404 si no existe, no está disponible o NO es visible para `user` (un componente ajeno no se distingue de uno inexistente)."""
    try:
        view = presentations.get_component(component_id)
    except (presentations.PresentationNotFound, presentations.PresentationUnavailable):
        raise HTTPException(404, "Componente no encontrado")
    except presentations.PresentationStorageError:
        raise HTTPException(503, "Presentaciones no disponibles")
    if not presentations.can_view_component_user(user, view):
        raise HTTPException(404, "Componente no encontrado")
    return view


@router.get("/comercial/componentes/vista", response_class=HTMLResponse, include_in_schema=False)
def component_preview(request: Request, ref: str = ""):
    redirect = _require_commercial_page(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    try:
        resolved = presentations.resolve_component_ref(ref, user)
    except (presentations.PresentationNotFound, presentations.PresentationUnavailable):
        raise HTTPException(404, "Componente no encontrado")
    except presentations.PresentationStorageError:
        raise HTTPException(503, "Presentaciones no disponibles")
    theme = "dark" if request.cookies.get("dw-content-theme") == "dark" else "light"
    template_id = next(iter(presentations.TEMPLATES))
    ctx = presentations.render_context(presentations.get(template_id), embed=True, theme=theme)
    ctx["preview_component"] = {"component": resolved["component"], "variant": resolved["variant"], "params": resolved["params"]}      # EL renderer real de la presentación, sin el resto del documento
    return templates.TemplateResponse(request=request, name=presentations.template_spec(template_id).jinja, context=ctx, headers=_NO_STORE)


@router.post("/comercial/componentes/duplicar", include_in_schema=False)
async def component_duplicate(request: Request):
    user = _require_commercial_write(request)
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    body = await _json_body(request)
    ref = body.get("ref")
    if not isinstance(ref, str):
        raise HTTPException(400, "Falta 'ref' en el payload")
    try:
        created = presentations.duplicate_component(ref, name=body.get("name"), description=body.get("description"), user=user)
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    except presentations.QuotaExceeded:
        return JSONResponse({"state": "quota", "message": "Se alcanzó el máximo de componentes propios. Eliminá alguno antes de crear otro."}, status_code=409)
    except (presentations.PresentationNotFound, presentations.PresentationUnavailable):
        raise HTTPException(404, "Componente no encontrado")
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.component.duplicate failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("component.created", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID, revision=created["revision"])
    return JSONResponse({"state": "ok", "component_id": created["component_id"], "revision": created["revision"]})


@router.post("/comercial/componentes/{component_id}", include_in_schema=False)
async def component_save(request: Request, component_id: str):
    user = _require_commercial_write(request)
    view = _load_component(user, component_id)
    if not presentations.can_edit_component_user(user, view):
        raise HTTPException(403, "No autorizado para editar este componente")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    body = await _json_body(request)
    revision = body.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise HTTPException(400, "Falta 'revision' (entero) en el payload")
    if not any(k in body for k in ("name", "description", "params")) or set(body) - {"revision", "name", "description", "params"}:
        raise HTTPException(422, "No hay nada para guardar")
    updated_by_id = str(user.get("sub") or "")
    updated_by_label = str(user.get("name") or user.get("username") or user.get("email") or updated_by_id)
    try:
        saved = presentations.update_component(component_id, expected_revision=revision, name=body.get("name"), description=body.get("description"), params=body.get("params"),
                                               updated_by_id=updated_by_id, updated_by_label=updated_by_label)
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    except presentations.StaleRevision as exc:
        return JSONResponse({"state": "stale", "current_revision": exc.current_revision,
                             "message": "El componente cambió desde que lo cargaste — recargá la página antes de volver a guardar. Nada se sobrescribió."}, status_code=409)
    except (presentations.PresentationNotFound, presentations.PresentationUnavailable):
        raise HTTPException(404, "Componente no encontrado")
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.component.save failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("component.saved", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID, revision=saved["revision"])
    return JSONResponse({"state": "ok", "revision": saved["revision"], "ref": saved["ref"]})


@router.post("/comercial/componentes/{component_id}/eliminar", include_in_schema=False)
async def component_delete(request: Request, component_id: str):
    user = _require_commercial_write(request)
    view = _load_component(user, component_id)
    if not presentations.can_delete_component_user(user, view):
        raise HTTPException(403, "No autorizado para eliminar este componente")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    try:
        presentations.delete_component(component_id)
    except (presentations.PresentationNotFound, presentations.PresentationUnavailable):
        raise HTTPException(404, "Componente no encontrado")
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.component.delete failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("component.deleted", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID)
    return JSONResponse({"state": "ok"})


# ── R7 — eliminar ────────────────────────────────────────────────────────────

@router.post("/comercial/presentaciones/{presentation_id}/eliminar", include_in_schema=False)
async def presentation_delete(request: Request, presentation_id: str):
    user = _require_commercial_write(request)
    try:
        view = presentations.authorization_view(presentation_id)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Presentación no encontrada")
    except presentations.PresentationStorageError:
        raise HTTPException(503, "Presentaciones no disponibles")
    if not presentations.can_delete_presentation_user(user, view):               # incluye seeds (nunca se eliminan, tampoco `APP_OWNER`)
        raise HTTPException(403, "No autorizado para eliminar esta presentación")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    try:
        presentations.delete_presentation(presentation_id)
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Presentación no encontrada")
    except presentations.SeedIsReadOnly:
        raise HTTPException(403, "Las presentaciones institucionales no se eliminan")
    except presentations.DurablePushFailed:
        return JSONResponse({"state": "unavailable", "reason": "durable_push_failed",
                             "message": "No se pudo confirmar la eliminación de forma durable — la presentación sigue en el catálogo. Reintentá en unos minutos."}, status_code=503)
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.delete failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("presentation.deleted", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID)
    return JSONResponse({"state": "ok", "url": "/comercial/presentaciones"})


# ── R6 — guardar campos ──────────────────────────────────────────────────────────────────────────────────────────────────

@router.post("/comercial/presentaciones/{presentation_id}", include_in_schema=False)
async def presentation_save(request: Request, presentation_id: str):
    user = _require_commercial_write(request)
    view = _load(presentation_id)
    if not presentations.can_edit_presentation_user(user, view):                 # incluye seeds (inmutables para TODOS, también `CONTENT_OWNER`)
        raise HTTPException(403, "No autorizado para editar esta presentación")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    body = await _json_body(request)
    revision = body.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise HTTPException(400, "Falta 'revision' (entero) en el payload")
    removed = body.get("removed_slides")                                         # opcional: la lista COMPLETA de slides eliminadas de la copia (ids; nunca markup)
    blocks = body.get("blocks")                                                  # opcional: los desvíos COMPLETOS de composición por región (ids de componente/variante; nunca markup)
    try:
        removed = presentations.validate_removed_slides(removed) if removed is not None else None
        blocks = presentations.validate_blocks(presentations.attach_block_snapshots(user, view, blocks)) if blocks is not None else None
        fields = body.get("fields")
        clean = presentations.validate_fields(view["template_id"], fields) if (fields or (removed is None and blocks is None)) else {}
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    updated_by_id = str(user.get("sub") or "")
    updated_by_label = str(user.get("name") or user.get("username") or user.get("email") or updated_by_id)
    try:
        saved = presentations.save(presentation_id, clean, expected_revision=revision, updated_by_id=updated_by_id, updated_by_label=updated_by_label, removed_slides=removed, blocks=blocks)
    except presentations.StaleRevision as exc:
        return JSONResponse({"state": "stale", "current_revision": exc.current_revision,
                             "message": "La presentación cambió desde que la cargaste — recargá la página antes de volver a guardar. Nada se sobrescribió."}, status_code=409)
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    except presentations.PresentationNotFound:
        raise HTTPException(404, "Presentación no encontrada")
    except presentations.PresentationUnavailable:
        raise HTTPException(409, "Presentación no disponible")
    except presentations.SeedIsReadOnly:
        raise HTTPException(403, "Las presentaciones institucionales no se editan")
    except presentations.PresentationStorageError as exc:
        _logger.error("commercial_presentations.save failed: %s", type(exc).__name__)
        return _storage_response(exc)
    log_activity("presentation.saved", user=user, scope=SCOPE_COMMERCIAL, area=AREA_PRESENTATIONS, module=CM03_MODULE_ID, revision=saved["revision"])
    return JSONResponse({"state": "ok", "revision": saved["revision"], "updated_at": saved["updated_at"], "content": saved["content"], "removed_slides": saved["removed_slides"], "blocks": saved["blocks"]})
