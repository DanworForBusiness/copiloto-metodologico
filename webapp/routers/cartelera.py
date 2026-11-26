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
import re
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import human_capital_access, same_origin_guard, workspaces
from webapp.services import auth_service, cartelera_repository as repo, cartelera_service as svc, cartelera_social as social
from webapp.services import cartelera_birthdays_source as bd_source
from webapp.services.activity_catalog import AREA_CCHH, EVENT_PAGE_VIEW, SCOPE_HUMAN_CAPITAL
from webapp.services.activity_service import log_activity
from webapp.templating import templates

router = APIRouter()
_log = logging.getLogger("danwor.cartelera")
BASE = "/cchh/cartelera"
_NO_STORE = {"Cache-Control": "no-store"}
# Logo institucional del encabezado del portal: el MISMO activo que Danwor Academy y Comercial › Presentaciones muestran a pantalla completa
# (wordmark blanco para fondo oscuro), referenciado en su ubicación original vía `/pack-assets`. No se copia ni se genera otra variante.
BRAND_LOGO_URL = f"/pack-assets/{DEFAULT_PACK}/academy/assets/img/danwor-logo.png"
TABS = (("ediciones", "Ediciones", BASE), ("cumpleanos", "Cumpleaños", f"{BASE}/cumpleanos"),
        ("institucional", "Nosotros y galería", f"{BASE}/institucional"), ("accesos", "Accesos directos", f"{BASE}/accesos"),
        ("comunidad", "Comunidad", f"{BASE}/comunidad"))
# Cada pestaña con la autorización EXISTENTE que la gobierna: las editoriales, Capital Humano (HUMAN_CAPITAL_ALLOWED_USERS); Comunidad, la
# moderación (COMMUNITY_OWNER). Son independientes: quien solo modera ve únicamente Comunidad, y Capital Humano no ve Comunidad por serlo.
COMMUNITY_TAB = "comunidad"


def _tabs_for(user: dict | None) -> tuple:
    hc, mod = human_capital_access.is_human_capital_authorized_user(user), social.is_moderator(user)
    return tuple(t for t in TABS if (mod if t[0] == COMMUNITY_TAB else hc))


# ── guardas ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _admin(request: Request):
    """(user, redirect): sin sesión → login; sin Capital Humano → 403."""
    user = auth_service.session_user(request)
    if not user:
        return None, workspaces.platform_login_redirect(request)
    if request.url.path.rstrip("/") == BASE and not human_capital_access.is_human_capital_authorized_user(user) and social.is_moderator(user):
        return None, RedirectResponse(f"{BASE}/comunidad", status_code=303)                # «Gestión» de quien solo modera: su pestaña
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


# AUDIENCIA DEL PORTAL. F11 (definitivo): en el lanzamiento institucional lo usa cualquier persona admitida al Hub. Hasta entonces (Fase I
# y homologación en DigitalOcean) el portal y su acceso del topbar quedan limitados a quienes YA están admitidos al workspace de Capital
# Humano (`HUMAN_CAPITAL_ALLOWED_USERS` o `COMMUNITY_OWNER`): ninguna variable ni mecanismo nuevo. El lanzamiento (Fase II, paso 6) es
# cambiar esta constante a True en un PR propio.
PORTAL_LAUNCHED = False


def portal_audience_ok(user: dict | None) -> bool:
    return bool(user) and (PORTAL_LAUNCHED or human_capital_access.is_human_capital_workspace_member(user))


def portal_link_visible(available_workspaces) -> bool:
    """Decisión de PRESENTACIÓN del acceso a la Cartelera en el topbar compartido: la MISMA regla que `portal_audience_ok`, evaluada sobre los
    workspaces que el servidor ya resolvió para la persona (`workspaces.available_workspaces(user)` incluye Capital Humano exactamente cuando
    `is_human_capital_workspace_member(user)`). El topbar solo consume el resultado: no conoce verticales ni reglas de autorización."""
    return PORTAL_LAUNCHED or workspaces.HUMAN_CAPITAL in (available_workspaces or ())


templates.env.globals["cartelera_portal_link"] = portal_link_visible


def _reader(request: Request):
    user = auth_service.session_user(request)
    if not user:
        return None, workspaces.platform_login_redirect(request)
    if not auth_service.is_authorized(str(user.get("sub") or "")):
        raise HTTPException(403, "No autorizado")
    if not portal_audience_ok(user):
        raise HTTPException(403, "La Cartelera Virtual todavía no está disponible")
    return user, None


def _actor(user: dict) -> str:
    return str(user.get("name") or user.get("sub") or "")


def _shell(request: Request, user: dict, tab: str, **extra) -> dict:
    # Mismo workspace para todos los admitidos; `hc_full` (Capital Humano) decide qué soluciones muestra la navegación.
    hc = human_capital_access.is_human_capital_authorized_user(user)
    return {"current_workspace": workspaces.HUMAN_CAPITAL, "hc_full": hc, "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK, "user": user, "active_page": "cartelera", "tabs": _tabs_for(user), "tab": tab, "svc": svc,
            "ok": request.query_params.get("ok", ""), "error": request.query_params.get("error", ""), "bd_source_on": bd_source.ENABLED, **extra}


def _back(path: str, *, ok: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode
    query = urlencode({k: v for k, v in (("ok", ok), ("error", error)) if v})
    return RedirectResponse(f"{path}{('&' if '?' in path else '?') + query if query else ''}", status_code=303)


def _storage_message(exc: Exception) -> str:
    if isinstance(exc, svc.CarteleraValidationError):
        return str(exc)
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
        before = social.item_targets(svc.get_edition(month))
        saved = svc.save_content(month, revision, title=body.get("title", ""), subtitle=body.get("subtitle", ""),
                                 sections=list(body.get("sections") or []), actor=_actor(user))
        social.purge_targets(sorted(before - social.item_targets(saved)))             # contenidos quitados: sin interacciones huérfanas
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
            targets = social.item_targets(svc.get_edition(month))
            svc.delete_edition(month, _actor(user))
            social.purge_targets(sorted(targets))
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
_BIRTHDAY_SORTS = {"fecha": lambda b: (int(b.get("month", 0)), int(b.get("day", 0)), b.get("name", "").lower()),
                   "colaborador": lambda b: svc_fold(b.get("name", "")),
                   "autorizacion": lambda b: (not svc.is_publishable(b), int(b.get("month", 0)), int(b.get("day", 0)))}


def svc_fold(value: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", value or "") if not unicodedata.combining(c)).lower()


def _birthday_sort_state(params: dict, orden: str, direction: str) -> dict:
    """MISMO contrato que el orden del Banco de CVs: la misma columna invierte la dirección, otra arranca ascendente; cada URL conserva
    búsqueda, filtros y tamaño de página y vuelve a la página 1."""
    from urllib.parse import urlencode
    urls, cols = {}, {}
    for col in _BIRTHDAY_SORTS:
        nxt = "desc" if (col == orden and direction == "asc") else "asc"
        urls[col] = f"{BASE}/cumpleanos?" + urlencode({**params, "orden": col, "dir": nxt})
        cols[col] = {"href": urls[col], "arrow": ("▲" if direction == "asc" else "▼") if col == orden else "",
                     "aria": ("ascending" if direction == "asc" else "descending") if col == orden else "none"}
    return cols


@router.get(f"{BASE}/cumpleanos", response_class=HTMLResponse, include_in_schema=False)
def birthdays_page(request: Request, q: str = "", mes: str = "", autorizacion: str = "", orden: str = "fecha", dir: str = "asc",
                   page: str = "1", page_size: str = ""):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    from webapp.services.pagination import paginate
    autorizacion = autorizacion if autorizacion in svc.AUTH_FILTERS else ""
    mes = mes if mes.isdigit() and 1 <= int(mes) <= 12 else ""
    orden = orden if orden in _BIRTHDAY_SORTS else "fecha"
    direction = "desc" if dir == "desc" else "asc"
    all_rows = svc.list_birthdays()
    # Contrato del Hub: dataset completo → filtros → búsqueda → orden → total filtrado → página (nunca paginar antes de filtrar).
    rows = sorted(svc.filter_birthdays(q=q, month=mes, authorized=autorizacion), key=_BIRTHDAY_SORTS[orden], reverse=direction == "desc")
    params = {k: v for k, v in (("q", q), ("mes", mes), ("autorizacion", autorizacion)) if v}
    sort_params = {**params, **({"orden": orden, "dir": direction} if (orden, direction) != ("fecha", "asc") else {})}
    pager = paginate(rows, page=page, page_size=page_size, base_url=f"{BASE}/cumpleanos", params=sort_params)
    sizing = {"page_size": pager["page_size"]} if page_size else {}
    greetings = {b["id"]: svc.greeting_status(b) for b in all_rows}                  # saludo del PRÓXIMO cumpleaños de cada persona
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", birthdays=pager["items"], pager=pager, filtered=pager["total"], total=len(all_rows),
        sort_cols=_birthday_sort_state({**params, **sizing}, orden, direction),
        greetings=greetings, upcoming=svc.upcoming_birthdays(30), months=svc.MONTHS, q=q, mes=mes, autorizacion=autorizacion,
        sp_status=bd_source.status() if bd_source.ENABLED else None,
        bd_emails=bd_source.emails() if bd_source.ENABLED else {}, email_required=svc._email_required()))


@router.post(f"{BASE}/cumpleanos/fuente", response_class=HTMLResponse, include_in_schema=False)
def birthdays_source_refresh(request: Request, graph_token: str = Form(""), force: str = Form(""), reload: str = Form("1")):
    """Lee el Excel de SharePoint con el token DELEGADO de quien abre Gestión (inyectado por `data-dw-graph-token`, MISMO hook que la
    actualización de Catálogos CCHH). Si el contenido cambió, recarga la página; si no, devuelve el estado de la fuente."""
    user = _admin_write(request)
    if not bd_source.ENABLED:
        raise HTTPException(404, "Fuente no habilitada")
    error, headers = None, dict(_NO_STORE)
    try:
        result = bd_source.refresh(graph_token, actor=_actor(user), force=force == "1")
        if result["changed"] and reload != "0":
            headers["HX-Refresh"] = "true"
    except bd_source.SourceError as exc:
        error = {"kind": exc.kind, "message": str(exc), "detail": exc.detail}
    except _ERRORS as exc:
        error = {"kind": "storage", "message": _storage_message(exc), "detail": ""}
    return templates.TemplateResponse(request=request, name="human_capital/_cartelera_bd_source.html", headers=headers, context={
        "request": request, "sp_status": bd_source.status(), "sp_error": error, "svc": svc})


def _volver_qs(volver: str) -> str:
    """El estado de Cumpleaños (`volver`) ya saneado por `_birthdays_return`, como query string ("" si no hay)."""
    return _birthdays_return(volver).partition("?")[2]


def _volver_param(volver: str, sep: str) -> str:
    from urllib.parse import quote
    qs = _volver_qs(volver)
    return f"{sep}volver={quote(qs, safe='')}" if qs else ""


def _birthdays_return(volver: str) -> str:
    """Vuelve a la vista de Cumpleaños con los MISMOS filtros, orden y página (`q`, `mes`, `autorizacion`, `orden`, `dir`, `page`,
    `page_size`); cualquier otra cosa se descarta. Si la página quedó fuera de rango (p. ej. tras eliminar), `paginate` la acota."""
    from urllib.parse import parse_qsl, urlencode
    keep = {k: v for k, v in parse_qsl((volver or "").lstrip("?"))
            if k in ("q", "mes", "autorizacion", "orden", "dir", "page", "page_size") and len(v) <= 120}
    return f"{BASE}/cumpleanos" + (f"?{urlencode(keep)}" if keep else "")



@router.post(f"{BASE}/cumpleanos", include_in_schema=False)
def birthday_save(request: Request, birthday_id: str = Form(""), revision: str = Form(""), first_name: str = Form(""), last_name: str = Form(""),
                  email: str = Form(""), day: str = Form(""), month: str = Form(""), authorized: str = Form(""), volver: str = Form(""),
                  write_token: str = Form("")):
    user = _admin_write(request)
    try:
        svc.save_birthday(birthday_id or None, int(revision) if revision else None, first_name=first_name, last_name=last_name, email=email,
                          day=day, month=month, authorized=authorized == "1", actor=_actor(user), write_token=write_token)
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "No existe") from None
    except _ERRORS as exc:
        return _back(_birthdays_return(volver), error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(_birthdays_return(volver), ok="Cumpleaños actualizado." if birthday_id else "Cumpleaños agregado.")


@router.get(f"{BASE}/cumpleanos/importar", response_class=HTMLResponse, include_in_schema=False)
def birthday_import_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos_importar.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", rows=None, bulk="", import_format=svc.import_format(), volver=_volver_qs(request.query_params.get("volver", "")),
        back_url=_birthdays_return(request.query_params.get("volver", ""))))


@router.post(f"{BASE}/cumpleanos/importar/vista-previa", response_class=HTMLResponse, include_in_schema=False)
async def birthday_import_preview(request: Request):
    user = _admin_write(request)
    form = await request.form()
    text = str(form.get("bulk") or "")
    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        raw = await upload.read()
        if len(raw) > 512 * 1024:
            return _back(f"{BASE}/cumpleanos/importar" + _volver_param(str(form.get("volver") or ""), "?"), error="El archivo supera 512 KB.")
        text = raw.decode("utf-8-sig", errors="replace")
    rows = svc.preview_birthday_import(text)
    volver = str(form.get("volver") or "")
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos_importar.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", rows=rows, bulk=text, import_format=svc.import_format(), volver=_volver_qs(volver), back_url=_birthdays_return(volver)))


@router.post(f"{BASE}/cumpleanos/importar", include_in_schema=False)
async def birthday_import(request: Request):
    user = _admin_write(request)
    form = await request.form()
    lines = [str(v) for v in form.getlist("line")]
    try:
        created, rejected = svc.import_birthdays(chr(10).join(lines), _actor(user), authorized=form.get("authorized") == "1",
                                                 write_token=str(form.get("write_token") or ""))
    except _ERRORS as exc:
        # Sin confirmación de la persistencia: se vuelve a la vista previa con las MISMAS líneas para reintentar sin recargarlas.
        volver = str(form.get("volver") or "")
        return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos_importar.html", headers=_NO_STORE, context=_shell(
            request, user, "cumpleanos", rows=svc.preview_birthday_import(chr(10).join(lines)), bulk=chr(10).join(lines),
            import_format=svc.import_format(), volver=_volver_qs(volver), back_url=_birthdays_return(volver), error=_storage_message(exc)))
    message = f"{created} cumpleaños importado{'' if created == 1 else 's'}."
    return _back(_birthdays_return(str(form.get("volver") or "")), ok=message, error=f"{len(rejected)} líneas no se pudieron importar." if rejected else "")


@router.post(f"{BASE}/cumpleanos/{{birthday_id}}/eliminar", include_in_schema=False)
def birthday_delete(request: Request, birthday_id: str, volver: str = Form(""), write_token: str = Form("")):
    user = _admin_write(request)
    try:
        greetings = [f"greeting:{g['id']}" for g in repo.list_all("greetings") if g.get("birthday_id") == birthday_id]
        svc.delete_birthday(birthday_id, write_token=write_token, actor=_actor(user))
        social.purge_targets(greetings)
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "No existe") from None
    except _ERRORS as exc:
        return _back(_birthdays_return(volver), error=_storage_message(exc))
    return _back(_birthdays_return(volver), ok="Cumpleaños eliminado.")


# ── gestión: saludos de cumpleaños («Preparar saludo») ─────────────────────────────────────────────────────────────────────────────
def _greeting_target(request: Request, birthday_id: str) -> tuple[dict, int]:
    b = svc.get_birthday(birthday_id)
    if b is None:
        raise HTTPException(404, "El cumpleaños no existe")
    raw = request.query_params.get("anio", "")
    year = int(raw) if raw.isdigit() and 2000 <= int(raw) <= 2100 else svc.next_birthday(b).year
    return b, year


@router.get(f"{BASE}/cumpleanos/{{birthday_id}}/saludo", response_class=HTMLResponse, include_in_schema=False)
def greeting_editor(request: Request, birthday_id: str):
    """Editor del saludo: MISMO workspace y MISMA capa de edición inline que el editor de carteleras, sobre la tarjeta real del portal."""
    user, redirect = _admin(request)
    if redirect:
        return redirect
    b, year = _greeting_target(request, birthday_id)
    if not svc.is_publishable(b):
        return _back(_birthdays_return(request.query_params.get("volver", "")), error="Para preparar el saludo, " + b["name"] + " tiene que tener la autorización de publicación.")
    g = svc.get_greeting(svc.greeting_id(birthday_id, year))
    volver = request.query_params.get("volver", "")
    when = svc.birthday_date(b, year)
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_saludo.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", birthday=b, year=year, greeting=g, greet_date=when, state=svc.effective_state(g) if g else "none",
        gid=svc.greeting_id(birthday_id, year), suggested=f"{when.isoformat()}T09:00", volver=_volver_qs(volver), back_url=_birthdays_return(volver),
        min_schedule=svc.to_local_input(svc.now_utc().isoformat())))


@router.get(f"{BASE}/cumpleanos/{{birthday_id}}/saludo/lienzo", response_class=HTMLResponse, include_in_schema=False)
def greeting_canvas(request: Request, birthday_id: str):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    b, year = _greeting_target(request, birthday_id)
    return _render_portal(request, "saludo", user=user, preview=True, edit_mode=True, greeting=svc.draft_greeting(b, year), birthday=b,
                          greet_date=svc.birthday_date(b, year))


@router.post(f"{BASE}/cumpleanos/{{birthday_id}}/saludo/vista-previa", response_class=HTMLResponse, include_in_schema=False)
async def greeting_live_preview(request: Request, birthday_id: str):
    """Vista del colaborador del saludo con lo que está en pantalla (sin persistir)."""
    _admin_write(request)
    b, year = _greeting_target(request, birthday_id)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return _render_portal(request, "saludo", preview=True, clean_view=True, context="preview", greeting=svc.draft_greeting(b, year, body),
                          birthday=b, greet_date=svc.birthday_date(b, year), preview_note="Vista del colaborador · incluye los cambios sin guardar")


@router.post(f"{BASE}/cumpleanos/{{birthday_id}}/saludo/contenido", include_in_schema=False)
async def greeting_save(request: Request, birthday_id: str):
    user = _admin_write(request)
    b, year = _greeting_target(request, birthday_id)
    try:
        body = await request.json()
        rev = body.get("revision")
        saved = svc.save_greeting(birthday_id, year, int(rev) if rev not in (None, "") else None, body, _actor(user))
    except repo.CarteleraConcurrentModificationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409, headers=_NO_STORE)
    except (svc.CarteleraValidationError, ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc) or "Datos inválidos."}, status_code=422, headers=_NO_STORE)
    except (repo.CarteleraStorageError, repo.CarteleraRemoteOutcomeUnknownError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503, headers=_NO_STORE)
    return JSONResponse({"ok": True, "revision": saved["revision"], "updated": svc.fmt_local(saved["updated_at"])}, headers=_NO_STORE)


@router.post(f"{BASE}/cumpleanos/{{birthday_id}}/saludo/estado", include_in_schema=False)
def greeting_state(request: Request, birthday_id: str, action: str = Form(""), revision: int = Form(0), when: str = Form("")):
    user = _admin_write(request)
    b, year = _greeting_target(request, birthday_id)
    volver = request.query_params.get("volver", "")
    path = f"{BASE}/cumpleanos/{birthday_id}/saludo?anio={year}" + _volver_param(volver, "&")
    try:
        saved = svc.greeting_state(birthday_id, year, revision, action, when, _actor(user))
        if action == "delete":
            social.purge_targets([f"greeting:{svc.greeting_id(birthday_id, year)}"])
    except repo.CarteleraNotFoundError:
        raise HTTPException(404, "El saludo no existe") from None
    except _ERRORS as exc:
        return _back(path, error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    messages = {"publish": "Saludo publicado: ya lo ven los colaboradores.", "unschedule": "Programación cancelada: el saludo vuelve a borrador.",
                "withdraw": "Saludo retirado de la Cartelera.", "delete": "Saludo eliminado."}
    if action == "delete":
        return _back(_birthdays_return(volver), ok=messages["delete"])
    if action == "schedule":
        return _back(path, ok="Saludo programado para el " + svc.fmt_local(saved["publish_at"]) + " (hora de Buenos Aires).")
    return _back(path, ok=messages.get(action, "Cambio guardado."))


# ── gestión: institucional, galería y accesos ─────────────────────────────────────────────────────────────────────────────────────
@router.get(f"{BASE}/institucional", response_class=HTMLResponse, include_in_schema=False)
def institutional_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    data = svc.institutional()
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_institucional.html", headers=_NO_STORE, context=_shell(
        request, user, "institucional", data=data, model_json=json.dumps({k: data.get(k) for k in ("intro", "mission", "vision", "values_intro", "values", "links", "org", "albums")}, ensure_ascii=False),
        org_kinds=svc.ORG_KINDS))


@router.post(f"{BASE}/institucional", include_in_schema=False)
async def institutional_save(request: Request):
    user = _admin_write(request)
    try:
        body = await request.json()
        revision = body.get("revision")
        saved = svc.save_institutional(int(revision) if revision not in (None, "") else None, mission=body.get("mission", ""),
                                       vision=body.get("vision", ""), values=list(body.get("values") or []),
                                       albums=list(body.get("albums") or []), actor=_actor(user), intro=body.get("intro", ""),
                                       values_intro=body.get("values_intro", ""), links=list(body.get("links") or []),
                                       org=list(body["org"]) if isinstance(body.get("org"), list) else None)
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
    by_day: dict[int, list[dict]] = {}
    live = svc.live_greetings(year)
    for b in svc.birthdays_of_month(month):
        gid = svc.greeting_id(b["id"], year)
        by_day.setdefault(int(b["day"]), []).append({"name": b["name"], "greeting": gid if gid in live else ""})
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


def _social_context(user: dict | None, ctx: dict, *, social_on: bool) -> dict:
    """Motor de interacción en el portal: solo en consumo real (nunca en el lienzo, vistas previas ni la vista del colaborador).
    Targets de la página: contenidos de la edición mostrada, el saludo y las publicaciones de Comunidad."""
    # Roles de Comunidad resueltos en el servidor y ORTOGONALES a Capital Humano (`can_manage` sigue siendo solo la gestión editorial).
    # Consumo IGUAL para cualquier permiso administrativo: el portal se dibuja siempre como colaborador (sin vista de moderación: nada retirado
    # a la vista ni resultados anticipados). Única capacidad de PARTICIPACIÓN que varía: comunicación oficial (Capital Humano), validada otra
    # vez en el servidor. La aprobación previa depende solo de la categoría.
    out = {"social_on": social_on, "social": {}, "can_publish_official": social.can_publish_official(user), "social_reactions": social.REACTIONS, "social_when": social.fmt_when,
           "me": social.uid(user or {}), "me_name": social.uname(user or {}), "notif_unseen": 0, "social_names": {},
           "community_count": sum(1 for p in social.list_posts() if social.post_state(p) == "published"),
           "social_categories": social.CATEGORIES, "social_categories_by_id": social.CATEGORY_BY_ID, "social_post_state": social.post_state,
           "social_post_json": social.post_json, "social_render": social.render_text, "social_rules": social.RULES,
           "social_wall_types": social.WALL_TYPES, "social_poll": lambda p: social.poll_state(p, user, False)}
    if not social_on:
        return out
    social.touch_person(user)
    names = social.people()
    targets = sorted(social.item_targets(ctx.get("edition"))) if ctx.get("page") == "novedades" else []
    if ctx.get("greeting"):
        targets.append(f"greeting:{ctx['greeting']['id']}")
    targets += [f"post:{p['id']}" for p in ctx.get("posts") or []]
    out["social"] = social.snapshot(targets, user, False, names)
    out["notif_unseen"] = sum(1 for n in social.notifications(user, names) if n["new"])
    out["social_names"] = names
    return out


def _render_portal(request: Request, page: str, *, edition: dict | None = None, preview: bool = False, user: dict | None = None,
                   preview_note: str = "", edit_mode: bool = False, context: str = "general", layout_mode: bool = False,
                   clean_view: bool = False, **extra):
    user = user or auth_service.session_user(request)
    ctx = {"page": page, "svc": svc, "user": user, "preview": preview, "preview_note": preview_note, "edit_mode": edit_mode,
           "layout_mode": bool(edit_mode and layout_mode),
           "urls": _portal_urls(edition, "general" if edit_mode else context),
           # Vista del colaborador del editor: experiencia limpia, sin ningún control administrativo.
           "clean_view": clean_view,
           "edition": edition, "links": svc.site_links(), "brand_logo_url": BRAND_LOGO_URL,
           "editions": svc.visible_editions(), "inst": svc.institutional(), "org_tree": svc.org_tree, "today_month": svc.month_key(),
           "today_day": svc.now_utc().astimezone(svc.BUSINESS_TZ).day,
           "upcoming": svc.upcoming_birthdays(7), "can_manage": human_capital_access.is_human_capital_authorized_user(user),
           "live_greetings": svc.live_greetings(svc.now_utc().astimezone(svc.BUSINESS_TZ).year),
           "this_year": svc.now_utc().astimezone(svc.BUSINESS_TZ).year, **extra}
    if edition is not None:
        ctx["calendar"] = _calendar(edition["id"])
    ctx.update(_social_context(user, ctx, social_on=bool(user) and not preview and not edit_mode and not clean_view))
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


@router.get("/cartelera/saludos/{gid}", response_class=HTMLResponse, include_in_schema=False)
def portal_greeting(request: Request, gid: str):
    """Saludo de cumpleaños publicado. Solo si está publicado (o su programación venció) y la persona sigue autorizada y visible."""
    user, redirect = _reader(request)
    if redirect:
        return redirect
    g = svc.get_greeting(gid) if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", gid or "") else None
    if not svc.greeting_visible(g):
        raise HTTPException(404, "Este saludo no está publicado")
    b = svc.get_birthday(g["birthday_id"])
    return _render_portal(request, "saludo", user=user, greeting=g, birthday=b, greet_date=svc.birthday_date(b, g["year"]))


@router.get("/cartelera/nosotros", response_class=HTMLResponse, include_in_schema=False)
def portal_about(request: Request):
    user, redirect = _reader(request)
    if redirect:
        return redirect
    selected = _selected_edition(request)
    return _render_portal(request, "nosotros", edition=selected, user=user, context="edition" if selected else "general")
