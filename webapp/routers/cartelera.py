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
    existing = {e["id"] for e in editions}
    current = svc.month_key()
    candidates = [m for m in (svc.shift_month(current, d) for d in (-1, 0, 1, 2)) if m not in existing]
    return templates.TemplateResponse(request=request, name="human_capital/cartelera.html", headers=_NO_STORE, context=_shell(
        request, user, "ediciones", editions=editions, candidates=candidates, default_month=svc.shift_month(current, 1) if svc.shift_month(current, 1) in candidates else (candidates[0] if candidates else "")))


@router.post(f"{BASE}/ediciones", include_in_schema=False)
def edition_create(request: Request, month: str = Form(""), copy_previous: str = Form("")):
    user = _admin_write(request)
    try:
        svc.create_edition(month, _actor(user), copy_previous=copy_previous == "1")
    except _ERRORS as exc:
        return _back(BASE, error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(f"{BASE}/ediciones/{month}", ok="Edición creada.")


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
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_edicion.html", headers=_NO_STORE, context=_shell(
        request, user, "ediciones", edition=edition, model_json=json.dumps(model, ensure_ascii=False), state=svc.effective_state(edition),
        links=svc.site_links()))


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
    return _render_portal(request, "novedades", edition=edition, preview=True)


@router.post(f"{BASE}/proyectos", include_in_schema=False)
def project_options(request: Request, graph_token: str = Form("")):
    _admin_write(request)
    try:
        return JSONResponse({"projects": svc.project_options(graph_token)}, headers=_NO_STORE)
    except svc.ProjectSourceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502, headers=_NO_STORE)


# ── gestión: cumpleaños ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get(f"{BASE}/cumpleanos", response_class=HTMLResponse, include_in_schema=False)
def birthdays_page(request: Request):
    user, redirect = _admin(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_cumpleanos.html", headers=_NO_STORE, context=_shell(
        request, user, "cumpleanos", birthdays=svc.list_birthdays(), upcoming=svc.upcoming_birthdays(7), months=svc.MONTHS))


@router.post(f"{BASE}/cumpleanos", include_in_schema=False)
def birthday_save(request: Request, birthday_id: str = Form(""), revision: str = Form(""), name: str = Form(""), day: str = Form(""),
                  month: str = Form(""), authorized: str = Form("")):
    user = _admin_write(request)
    try:
        svc.save_birthday(birthday_id or None, int(revision) if revision else None, name=name, day=day, month=month,
                          authorized=authorized == "1", actor=_actor(user))
    except _ERRORS as exc:
        return _back(f"{BASE}/cumpleanos", error=str(exc) if isinstance(exc, svc.CarteleraValidationError) else _storage_message(exc))
    return _back(f"{BASE}/cumpleanos", ok="Cumpleaños guardado.")


@router.post(f"{BASE}/cumpleanos/importar", include_in_schema=False)
def birthday_import(request: Request, bulk: str = Form(""), authorized: str = Form("")):
    user = _admin_write(request)
    try:
        created, rejected = svc.import_birthdays(bulk, _actor(user), authorized=authorized == "1")
    except _ERRORS as exc:
        return _back(f"{BASE}/cumpleanos", error=_storage_message(exc))
    message = f"{created} cumpleaños importados."
    if rejected:
        return _back(f"{BASE}/cumpleanos", ok=message, error=f"{len(rejected)} líneas no tienen el formato «Nombre;DD/MM»: " + " | ".join(rejected[:5]))
    return _back(f"{BASE}/cumpleanos", ok=message)


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


def _render_portal(request: Request, page: str, *, edition: dict | None = None, preview: bool = False, user: dict | None = None):
    user = user or auth_service.session_user(request)
    ctx = {"page": page, "svc": svc, "user": user, "preview": preview, "edition": edition, "links": svc.site_links(),
           "editions": svc.visible_editions(), "inst": svc.institutional(), "today_month": svc.month_key(),
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
    return _render_portal(request, "inicio", edition=svc.current_edition(), user=user)


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
    return _render_portal(request, "novedades", edition=edition, user=user)


@router.get("/cartelera/nosotros", response_class=HTMLResponse, include_in_schema=False)
def portal_about(request: Request):
    user, redirect = _reader(request)
    if redirect:
        return redirect
    return _render_portal(request, "nosotros", user=user)
