"""cartelera.py — CARTELERA VIRTUAL: gestión dentro de Capital Humano y portal standalone de consumo.

DOS contratos, deliberadamente separados (mismo criterio que Presentaciones Comerciales, R1 vs. gestión):
  Gestión  `/cchh/cartelera/…`  — sesión + `human_capital_access.is_human_capital_authorized` (F1: MISMA frontera que Staffing; sin roles nuevos).
                                  Toda escritura exige además origen verificado (`same_origin_guard`).
  Consumo  `/cartelera/…`        — sesión + `auth_service.is_authorized` (admisión general al Hub, F11). NO exige ninguna vertical. Solo ve
                                  ediciones PUBLICADAS (la visibilidad la resuelve `cartelera_service`, nunca la plantilla).
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, same_origin_guard, workspaces
from webapp.services import auth_service, cartelera_repository as repo, cartelera_service as svc
from webapp.services.activity_catalog import AREA_CCHH, EVENT_PAGE_VIEW, SCOPE_HUMAN_CAPITAL
from webapp.services.activity_service import log_activity
from webapp.templating import templates

router = APIRouter()
_log = logging.getLogger("danwor.cartelera")
BASE = "/cchh/cartelera"
_NO_STORE = {"Cache-Control": "no-store"}
TABS = (("ediciones", "Ediciones", BASE), ("cumpleanos", "Cumpleaños", f"{BASE}/cumpleanos"),
        ("institucional", "Nosotros y galería", f"{BASE}/institucional"), ("accesos", "Accesos directos", f"{BASE}/accesos"))


# ── guardas ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _admin(request: Request):
    """(user, redirect): sin sesión → login; sin Capital Humano → 403."""
    user = auth_service.session_user(request)
    if not user:
        return None, workspaces.platform_login_redirect(request)
    if not human_capital_access.is_human_capital_authorized(request):
        raise HTTPException(403, "No autorizado para Capital Humano")
    return user, None


def _admin_write(request: Request) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not human_capital_access.is_human_capital_authorized(request):
        raise HTTPException(403, "No autorizado para Capital Humano")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no verificado")
    return user


def _reader(request: Request):
    user = auth_service.session_user(request)
    if not user:
        return None, workspaces.platform_login_redirect(request)
    if not auth_service.is_authorized(str(user.get("sub") or "")):
        raise HTTPException(403, "No autorizado")
    return user, None


def _actor(user: dict) -> str:
    return str(user.get("name") or user.get("sub") or "")


def _shell(request: Request, user: dict, tab: str, **extra) -> dict:
    return {"current_workspace": workspaces.HUMAN_CAPITAL, "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK, "user": user, "active_page": "cartelera", "tabs": TABS, "tab": tab, "svc": svc,
            "ok": request.query_params.get("ok", ""), "error": request.query_params.get("error", ""), **extra}


def _back(path: str, *, ok: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode
    query = urlencode({k: v for k, v in (("ok", ok), ("error", error)) if v})
    return RedirectResponse(f"{path}{'?' + query if query else ''}", status_code=303)


def _storage_message(exc: Exception) -> str:
    if isinstance(exc, repo.CarteleraConcurrentModificationError):
        return str(exc) or "El contenido cambió mientras editabas. Recargá antes de guardar."
    if isinstance(exc, (repo.CarteleraStorageError, repo.CarteleraRemoteOutcomeUnknownError)):
        return str(exc)
    return "No se pudo guardar."


_ERRORS = (svc.CarteleraValidationError, repo.CarteleraConcurrentModificationError, repo.CarteleraStorageError, repo.CarteleraRemoteOutcomeUnknownError)


# ── gestión: ediciones ────────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get(BASE, response_class=HTMLResponse, include_in_schema=False)
def editions_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    editions = svc.list_editions()
    current = svc.current_edition()
    current_id = current["id"] if current else None
    preparing = [e for e in editions if svc.effective_state(e) in ("draft", "scheduled")]
    history = [e for e in editions if (svc.effective_state(e) == "published" and e["id"] != current_id) or svc.effective_state(e) == "withdrawn"]
    return templates.TemplateResponse(request=request, name="human_capital/cartelera.html", headers=_NO_STORE, context=_shell(
        request, user, "ediciones", current=current, preparing=preparing, history=history, default_month=svc.next_free_month(),
        taken=[e["id"] for e in editions]))


@router.post(f"{BASE}/ediciones", include_in_schema=False)
def edition_create(request: Request, month: str = Form("")):
    user = _admin_write(request)
    try:
        svc.create_edition(month, _actor(user), copy_previous=False)
    except _ERRORS as exc:
        return _back(BASE, error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(f"{BASE}/ediciones/{month}", ok="Cartelera creada.")


@router.post(f"{BASE}/ediciones/{{source}}/copiar", include_in_schema=False)
def edition_copy(request: Request, source: str, month: str = Form("")):
    user = _admin_write(request)
    try:
        svc.copy_edition(source, month, _actor(user))
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "La edición no existe") from None
    except _ERRORS as exc:
        return _back(BASE, error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(f"{BASE}/ediciones/{month}", ok=f"Copia creada como borrador a partir de {svc.month_label(source)}.")


def _edition_or_404(month: str) -> dict:
    edition = svc.get_edition(month)
    if edition is None:
        raise HTTPException(404, "La edición no existe")
    return edition


@router.get(f"{BASE}/ediciones/{{month}}", response_class=HTMLResponse, include_in_schema=False)
def edition_editor(request: Request, month: str):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    edition = _edition_or_404(month)
    model = {"title": edition.get("title", ""), "subtitle": edition.get("subtitle", ""),
             "sections": [{**s, **{k: svc.SECTION_BY_KEY[s["key"]][k] for k in ("title", "kind")}} for s in edition["sections"]]}
    birthdays = sorted(svc.birthdays_of_month(int(month[5:7])), key=lambda b: (int(b["day"]), b["name"].lower()))
    # Desde Ediciones (Editar) arranca en Inicio; los botones contextuales de Novedades traen `pagina=novedades` y vuelven a esa cartelera.
    from_portal = request.query_params.get("origen") == "portal"
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_edicion.html", headers=_NO_STORE, context=_shell(
        request, user, "ediciones", edition=edition, model_json=json.dumps(model, ensure_ascii=False), state=svc.effective_state(edition),
        links=svc.site_links(), birthdays=birthdays, min_schedule=svc.to_local_input(svc.now_utc().isoformat()),
        canvas_page=_canvas_page(request, "inicio"), editor_mode=_editor_mode(request),
        back_to_portal=f"/cartelera/novedades/{month}" if from_portal and svc.is_visible(edition) else ""))


@router.post(f"{BASE}/ediciones/{{month}}/contenido", include_in_schema=False)
async def edition_save_content(request: Request, month: str):
    user = _admin_write(request)
    try:
        body = await request.json()
        revision = int(body.get("revision"))
        saved = svc.save_content(month, revision, title=body.get("title", ""), subtitle=body.get("subtitle", ""),
                                 sections=list(body.get("sections") or []), actor=_actor(user))
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "La edición no existe") from None
    except repo.CarteleraConcurrentModificationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409, headers=_NO_STORE)
    except (svc.CarteleraValidationError, ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc) or "Datos inválidos."}, status_code=422, headers=_NO_STORE)
    except (repo.CarteleraStorageError, repo.CarteleraRemoteOutcomeUnknownError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503, headers=_NO_STORE)
    return JSONResponse({"ok": True, "revision": saved["revision"], "updated": svc.fmt_local(saved["updated_at"])}, headers=_NO_STORE)


@router.post(f"{BASE}/ediciones/{{month}}/estado", include_in_schema=False)
def edition_state(request: Request, month: str, action: str = Form(""), revision: int = Form(0), when: str = Form("")):
    user = _admin_write(request)
    _edition_or_404(month)
    path = f"{BASE}/ediciones/{month}"
    try:
        if action == "publish":
            svc.publish_now(month, revision, _actor(user))
            return _back(path, ok="Edición publicada. Ya la ven los colaboradores.")
        if action == "schedule":
            saved = svc.schedule(month, revision, when, _actor(user))
            return _back(path, ok=f"Publicación programada para el {svc.fmt_local(saved['publish_at'])} (hora de Buenos Aires).")
        if action in ("unschedule", "withdraw"):
            svc.to_draft(month, revision, _actor(user), action=action)
            return _back(path, ok="Edición retirada del portal." if action == "withdraw" else "Programación cancelada: la edición vuelve a borrador.")
        if action == "delete":
            svc.delete_edition(month, _actor(user))
            return _back(BASE, ok=f"Edición de {svc.month_label(month)} eliminada.")
    except _ERRORS as exc:
        return _back(path, error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    raise HTTPException(400, "Acción desconocida")


@router.get(f"{BASE}/ediciones/{{month}}/vista-previa", response_class=HTMLResponse, include_in_schema=False)
def edition_preview(request: Request, month: str):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    edition = _edition_or_404(month)
    return _render_portal(request, _canvas_page(request, "inicio"), edition=edition, preview=True, context="preview")


@router.post(f"{BASE}/ediciones/{{month}}/vista-previa", response_class=HTMLResponse, include_in_schema=False)
async def edition_live_preview(request: Request, month: str):
    """Vista previa del contenido EN PANTALLA (sin guardar), renderizada con la MISMA plantilla del portal. No persiste nada ni cambia la
    visibilidad; es POST porque viaja el borrador completo (y exige el mismo origen verificado que cualquier escritura de gestión)."""
    _admin_write(request)
    edit = request.query_params.get("edit") == "1"
    try:
        body = await request.json()
        build = svc.draft_edition if edit else svc.preview_edition
        edition = build(month, title=body.get("title", ""), subtitle=body.get("subtitle", ""), sections=list(body.get("sections") or []))
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "La edición no existe") from None
    except (svc.CarteleraValidationError, ValueError, TypeError, AttributeError) as exc:
        return JSONResponse({"error": str(exc) or "Datos inválidos."}, status_code=422, headers=_NO_STORE)
    return _render_portal(request, _canvas_page(request, "novedades"), edition=edition, preview=True, edit_mode=edit, context="preview", clean_view=not edit,
                          preview_note="Vista del colaborador · incluye los cambios sin guardar")


def _canvas_page(request: Request, default: str) -> str:
    page = request.query_params.get("pagina", "")
    return page if page in ("inicio", "novedades", "nosotros") else default


@router.get(f"{BASE}/ediciones/{{month}}/lienzo", response_class=HTMLResponse, include_in_schema=False)
def edition_canvas(request: Request, month: str):
    """LIENZO de edición: la MISMA plantilla del portal (`cartelera/portal.html`) con `edit_mode`, que agrega la capa de edición (textos
    `contenteditable`, herramientas de sección y de contenido, arrastre) y dibuja todas sus páginas para recorrerlas sin recargar.
    Arranca en Inicio, como el portal. Solo gestión de Capital Humano; nada de esto llega al portal."""
    user, redirect = _admin(request)
    if redirect:
        return redirect
    return _render_portal(request, _canvas_page(request, "inicio"), edition=_edition_or_404(month), preview=True, edit_mode=True, user=user,
                          layout_mode=_editor_mode(request) == "ordenar")


def _editor_mode(request: Request) -> str:
    """«ordenar» (desde el botón Ordenar de Novedades): la MISMA capa de edición limitada a la composición. Cualquier otro valor: completo."""
    return "ordenar" if request.query_params.get("modo") == "ordenar" else "editar"


@router.post(f"{BASE}/proyectos", include_in_schema=False)
def project_options(request: Request, graph_token: str = Form("")):
    _admin_write(request)
    try:
        return JSONResponse({"projects": svc.project_options(graph_token)}, headers=_NO_STORE)
    except svc.ProjectSourceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502, headers=_NO_STORE)


# ── gestión: cumpleaños ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get(f"{BASE}/cumpleanos", response_class=HTMLResponse, include_in_schema=False)
def birthdays_page(request: Request, q: str = "", mes: str = "", visibilidad: str = ""):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    visibilidad = visibilidad if visibilidad in svc.VISIBILITY_LABELS else ""
    mes = mes if mes.isdigit() and 1 <= int(mes) <= 12 else ""
    all_rows = svc.list_birthdays()
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", birthdays=svc.filter_birthdays(q=q, month=mes, visibility=visibilidad), total=len(all_rows),
        upcoming=svc.upcoming_birthdays(30), months=svc.MONTHS, q=q, mes=mes, visibilidad=visibilidad))


@router.post(f"{BASE}/cumpleanos", include_in_schema=False)
def birthday_save(request: Request, birthday_id: str = Form(""), revision: str = Form(""), name: str = Form(""), day: str = Form(""),
                  month: str = Form(""), authorized: str = Form("")):
    user = _admin_write(request)
    try:
        svc.save_birthday(birthday_id or None, int(revision) if revision else None, name=name, day=day, month=month,
                          authorized=authorized == "1", actor=_actor(user))
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "No existe") from None
    except _ERRORS as exc:
        return _back(f"{BASE}/cumpleanos", error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(f"{BASE}/cumpleanos", ok="Cumpleaños actualizado." if birthday_id else "Cumpleaños agregado.")


@router.get(f"{BASE}/cumpleanos/importar", response_class=HTMLResponse, include_in_schema=False)
def birthday_import_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos_importar.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", rows=None, bulk=""))


@router.post(f"{BASE}/cumpleanos/importar/vista-previa", response_class=HTMLResponse, include_in_schema=False)
async def birthday_import_preview(request: Request):
    user = _admin_write(request)
    form = await request.form()
    text = str(form.get("bulk") or "")
    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        raw = await upload.read()
        if len(raw) > 512 * 1024:
            return _back(f"{BASE}/cumpleanos/importar", error="El archivo supera 512 KB.")
        text = raw.decode("utf-8-sig", errors="replace")
    rows = svc.preview_birthday_import(text)
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos_importar.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", rows=rows, bulk=text))


@router.post(f"{BASE}/cumpleanos/importar", include_in_schema=False)
async def birthday_import(request: Request):
    user = _admin_write(request)
    form = await request.form()
    lines = [str(v) for v in form.getlist("line")]
    try:
        created, rejected = svc.import_birthdays(chr(10).join(lines), _actor(user), authorized=form.get("authorized") == "1")
    except _ERRORS as exc:
        return _back(f"{BASE}/cumpleanos", error=_storage_message(exc))
    message = f"{created} cumpleaños importado{'' if created == 1 else 's'}."
    return _back(f"{BASE}/cumpleanos", ok=message, error=f"{len(rejected)} líneas no se pudieron importar." if rejected else "")


@router.post(f"{BASE}/cumpleanos/{{birthday_id}}/{{action}}", include_in_schema=False)
def birthday_action(request: Request, birthday_id: str, action: str, revision: int = Form(0)):
    user = _admin_write(request)
    try:
        if action in ("ocultar", "mostrar"):
            svc.set_birthday_hidden(birthday_id, revision, action == "ocultar", _actor(user))
        elif action == "eliminar":
            repo.delete("birthdays", birthday_id)
        else:
            raise HTTPException(400, "Acción desconocida")
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "No existe") from None
    except _ERRORS as exc:
        return _back(f"{BASE}/cumpleanos", error=_storage_message(exc))
    return _back(f"{BASE}/cumpleanos", ok="Cambio guardado.")


# ── gestión: institucional, galería y accesos ─────────────────────────────────────────────────────────────────────────────────────
@router.get(f"{BASE}/institucional", response_class=HTMLResponse, include_in_schema=False)
def institutional_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    data = svc.institutional()
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_institucional.html", headers=_NO_STORE, context=_shell(
        request, user, "institucional", data=data, model_json=json.dumps({k: data.get(k) for k in ("mission", "vision", "values", "albums")}, ensure_ascii=False)))


@router.post(f"{BASE}/institucional", include_in_schema=False)
async def institutional_save(request: Request):
    user = _admin_write(request)
    try:
        body = await request.json()
        revision = body.get("revision")
        saved = svc.save_institutional(int(revision) if revision not in (None, "") else None, mission=body.get("mission", ""),
                                       vision=body.get("vision", ""), values=list(body.get("values") or []),
                                       albums=list(body.get("albums") or []), actor=_actor(user))
    except repo.CarteleraConcurrentModificationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409, headers=_NO_STORE)
    except (svc.CarteleraValidationError, ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc) or "Datos inválidos."}, status_code=422, headers=_NO_STORE)
    except (repo.CarteleraStorageError, repo.CarteleraRemoteOutcomeUnknownError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503, headers=_NO_STORE)
    return JSONResponse({"ok": True, "revision": saved["revision"]}, headers=_NO_STORE)


@router.get(f"{BASE}/accesos", response_class=HTMLResponse, include_in_schema=False)
def links_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_accesos.html", headers=_NO_STORE, context=_shell(
        request, user, "accesos", links=svc.site_links(), links_revision=svc.site_revision()))


@router.post(f"{BASE}/accesos", include_in_schema=False)
async def links_save(request: Request):
    user = _admin_write(request)
    form = await request.form()
    titles, groups, urls = form.getlist("title"), form.getlist("group"), form.getlist("url")
    revision = form.get("revision")
    links = [{"title": t, "group": g, "url": u} for t, g, u in zip(titles, groups, urls)]
    try:
        svc.save_links(int(revision) if revision else None, links, _actor(user))
    except _ERRORS as exc:
        return _back(f"{BASE}/accesos", error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(f"{BASE}/accesos", ok="Accesos directos guardados.")


# ── portal de consumo (standalone) ────────────────────────────────────────────────────────────────────────────────────────────────
def _calendar(month_key: str) -> dict:
    """Grilla lunes→domingo del mes con los cumpleaños publicables (solo día y mes)."""
    import calendar
    year, month = int(month_key[:4]), int(month_key[5:7])
    by_day: dict[int, list[str]] = {}
    for b in svc.birthdays_of_month(month):
        by_day.setdefault(int(b["day"]), []).append(b["name"])
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    return {"weeks": weeks, "by_day": by_day, "count": sum(len(v) for v in by_day.values())}


def _portal_urls(edition: dict | None, context: str) -> dict:
    """Destinos de la navegación del portal según el CONTEXTO en que se abrió:
      - "general"  (acceso transversal / Ver portal): Inicio y Novedades siguen a la edición vigente;
      - "edition"  (Presentar, el selector de mes o un enlace directo a una edición publicada, vigente o histórica): toda la navegación
                   conserva ESA edición (`?edicion=AAAA-MM` / `/cartelera/novedades/AAAA-MM`);
      - "preview"  (Presentar/Vista previa administrativa de un borrador o programada): se queda dentro de la vista previa de gestión.
    El lienzo del editor usa siempre "general": su navegación la resuelve el propio lienzo sin salir del editor."""
    month = edition["id"] if edition else ""
    if context == "preview" and month:
        base = f"{BASE}/ediciones/{month}/vista-previa"
        return {"inicio": f"{base}?pagina=inicio", "novedades": f"{base}?pagina=novedades", "nosotros": f"{base}?pagina=nosotros",
                "edition": f"{base}?pagina=novedades"}
    if context == "edition" and month:
        return {"inicio": f"/cartelera?edicion={month}", "novedades": f"/cartelera/novedades/{month}",
                "nosotros": f"/cartelera/nosotros?edicion={month}", "edition": f"/cartelera/novedades/{month}"}
    return {"inicio": "/cartelera", "novedades": "/cartelera/novedades", "nosotros": "/cartelera/nosotros",
            "edition": f"/cartelera/novedades/{month}" if month else "/cartelera/novedades"}


def _selected_edition(request: Request) -> dict | None:
    """Edición elegida con `?edicion=AAAA-MM` (Presentar). Mismas reglas que un enlace directo: solo publicadas; si no, 404."""
    month = request.query_params.get("edicion", "")
    if not month:
        return None
    candidate = svc.get_edition(month)
    if candidate is None or not svc.is_visible(candidate):
        raise HTTPException(404, "Esta edición no está publicada")
    return candidate


def _render_portal(request: Request, page: str, *, edition: dict | None = None, preview: bool = False, user: dict | None = None,
                   preview_note: str = "", edit_mode: bool = False, context: str = "general", layout_mode: bool = False,
                   clean_view: bool = False):
    user = user or auth_service.session_user(request)
    ctx = {"page": page, "svc": svc, "user": user, "preview": preview, "preview_note": preview_note, "edit_mode": edit_mode,
           "layout_mode": bool(edit_mode and layout_mode),
           "urls": _portal_urls(edition, "general" if edit_mode else context),
           # «Gestionar» / «Volver al Hub» solo en el acceso general; nunca al presentar o mirar una edición elegida, ni en el editor.
           "hdr_tools": context == "general" and not edit_mode,
           # Vista del colaborador del editor: experiencia limpia, sin ningún control administrativo.
           "clean_view": clean_view,
           "edition": edition, "links": svc.site_links(),
           "editions": svc.visible_editions(), "inst": svc.institutional(), "today_month": svc.month_key(),
           "today_day": svc.now_utc().astimezone(svc.BUSINESS_TZ).day,
           "upcoming": svc.upcoming_birthdays(7), "can_manage": human_capital_access.is_human_capital_authorized_user(user)}
    if edition is not None:
        ctx["calendar"] = _calendar(edition["id"])
    if not preview:
        log_activity(EVENT_PAGE_VIEW, user=user, scope=SCOPE_HUMAN_CAPITAL, area=AREA_CCHH, module="cartelera",
                     view_id=uuid.uuid4().hex, nav_type="navigate", path=request.url.path)
    return templates.TemplateResponse(request=request, name="cartelera/portal.html", context=ctx, headers=_NO_STORE)


@router.get("/cartelera", response_class=HTMLResponse, include_in_schema=False)
def portal_home(request: Request):
    user, redirect = _reader(request)
    if redirect:
        return redirect
    selected = _selected_edition(request)
    return _render_portal(request, "inicio", edition=selected or svc.current_edition(), user=user,
                          context="edition" if selected else "general")


@router.get("/cartelera/novedades", response_class=HTMLResponse, include_in_schema=False)
@router.get("/cartelera/novedades/{month}", response_class=HTMLResponse, include_in_schema=False)
def portal_edition(request: Request, month: str = ""):
    user, redirect = _reader(request)
    if redirect:
        return redirect
    edition = svc.current_edition()
    if month:
        candidate = svc.get_edition(month)
        if candidate is None or not svc.is_visible(candidate):
            raise HTTPException(404, "Esta edición no está publicada")
        edition = candidate
    return _render_portal(request, "novedades", edition=edition, user=user, context="edition" if month else "general")


@router.get("/cartelera/nosotros", response_class=HTMLResponse, include_in_schema=False)
def portal_about(request: Request):
    user, redirect = _reader(request)
    if redirect:
        return redirect
    selected = _selected_edition(request)
    return _render_portal(request, "nosotros", edition=selected, user=user, context="edition" if selected else "general")
