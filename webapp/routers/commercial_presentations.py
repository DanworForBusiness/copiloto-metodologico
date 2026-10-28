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
    # Edición inline: `?edit=1` solo se honra si el usuario PUEDE editar esta instancia (Comercial + responsable / CONTENT_OWNER / APP_OWNER; nunca la semilla). Para cualquier otro es la vista de siempre: leer por URL no habilita nada.
    edit_mode = edit == "1" and embedded and presentations.can_edit_presentation_user(user, view)
    return templates.TemplateResponse(request=request, name=spec.jinja, context=presentations.render_context(view, embed=embedded, theme=theme, edit=edit_mode), headers=_NO_STORE)


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
    return templates.TemplateResponse(
        request=request, name="commercial/presentations.html",
        context=_shell_context(request, user, rows=rows, error_state=error_state, max_title=presentations.MAX_TITLE), headers=_NO_STORE,
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
    try:
        clean = presentations.validate_fields(view["template_id"], body.get("fields"))
    except presentations.PresentationValidationError as exc:
        raise HTTPException(422, str(exc))
    updated_by_id = str(user.get("sub") or "")
    updated_by_label = str(user.get("name") or user.get("username") or user.get("email") or updated_by_id)
    try:
        saved = presentations.save(presentation_id, clean, expected_revision=revision, updated_by_id=updated_by_id, updated_by_label=updated_by_label)
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
    return JSONResponse({"state": "ok", "revision": saved["revision"], "updated_at": saved["updated_at"], "content": saved["content"]})
