"""cartelera_comunidad.py — COMUNIDAD (portal de colaboradores) y API del MOTOR DE INTERACCIÓN de Cartelera Virtual.

Contratos (mismos que el portal, F11):
  - Consumo y participación (`/cartelera/comunidad`, `/cartelera/social/…`): sesión + admisión general al Hub; no exige ninguna vertical.
    Toda escritura exige además origen verificado (`same_origin_guard`). Identidad = la sesión de Entra ID (correo y nombre).
  - Moderación: pestaña «Comunidad» de Gestión de cartelera (`/cchh/cartelera/comunidad`) con admisión al Hub + `COMMUNITY_OWNER` (patrón de
    CONTENT_OWNER). NO depende de Capital Humano ni lo otorga: quien solo modera ve únicamente esa pestaña, sin la navegación de la vertical.
    La presentación (portal) no tiene controles de moderación.
Las reglas (visibilidad, propiedad, aprobación, integridad) viven en `cartelera_social`; acá solo se traducen a HTTP.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from webapp import same_origin_guard
from webapp.routers.cartelera import BASE, _NO_STORE, _back, _reader, _render_portal, _shell, portal_audience_ok, read_upload
from webapp.services import auth_service, cartelera_media as media, cartelera_repository as repo, cartelera_service as svc, cartelera_social as social
from webapp.templating import templates

router = APIRouter()


def _participant(request: Request) -> dict:
    """Escritura de un colaborador: sesión + admisión al Hub + origen verificado."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not auth_service.is_authorized(str(user.get("sub") or "")) or not portal_audience_ok(user):
        raise HTTPException(403, "No autorizado")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no verificado")
    return user


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Datos inválidos") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Datos inválidos")
    return data


@router.post("/cartelera/social/media", include_in_schema=False)
async def social_media_upload(request: Request):
    """Imagen o flyer de una publicación (cualquier colaborador admitido). Contenido nativo del Hub: el original queda en el almacenamiento
    privado del Hub (Spaces), gobernado por la publicación; no se escribe en SharePoint ni se pide acceso a carpetas de Capital Humano."""
    user = _participant(request)
    try:
        data, filename, _form = await read_upload(request)
        record = media.ingest(data, scope="comunidad", actor=social.uname(user), owner=social.uid(user), filename=filename)
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422, headers=_NO_STORE)
    return JSONResponse(media.public_view(record), headers=_NO_STORE)


MOD = f"{BASE}/comunidad"                                                             # pestaña «Comunidad» de Gestión de cartelera
LEGACY_MOD = "/cartelera/comunidad/moderacion"


def _moderator(request: Request):
    """(user, redirect) de la moderación: sesión + admisión al Hub + `COMMUNITY_OWNER`. Capital Humano no alcanza."""
    user, redirect = _reader(request)
    if redirect:
        return None, redirect
    if not social.is_moderator(user):
        raise HTTPException(403, "No autorizado para moderar Comunidad")
    return user, None


def _moderator_write(request: Request) -> dict:
    user = _participant(request)
    if not social.is_moderator(user):
        raise HTTPException(403, "No autorizado para moderar Comunidad")
    return user


def _reader_json(request: Request) -> dict:
    user = auth_service.session_user(request)
    if not user or not auth_service.is_authorized(str(user.get("sub") or "")):
        raise HTTPException(401, "Sin sesión")
    if not portal_audience_ok(user):
        raise HTTPException(403, "No autorizado")
    return user


def _run(fn):
    """Errores de dominio → JSON (mensaje apto para mostrar)."""
    try:
        return JSONResponse({"ok": True, **(fn() or {})}, headers=_NO_STORE)
    except social.SocialError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422, headers=_NO_STORE)
    except svc.CarteleraValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422, headers=_NO_STORE)
    except social.SocialForbidden as exc:
        return JSONResponse({"error": str(exc) or "No podés hacer esto."}, status_code=403, headers=_NO_STORE)
    except (social.SocialNotFound, repo.CarteleraNotFoundError):
        return JSONResponse({"error": "El contenido ya no está disponible."}, status_code=404, headers=_NO_STORE)
    except repo.CarteleraConcurrentModificationError:
        return JSONResponse({"error": "El contenido cambió mientras lo editabas. Recargá e intentá de nuevo."}, status_code=409, headers=_NO_STORE)
    except (repo.CarteleraStorageError, repo.CarteleraRemoteOutcomeUnknownError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503, headers=_NO_STORE)


def wall_context(user: dict, mod: bool, request: Request) -> dict:
    qp = request.query_params
    f = {"cat": qp.get("cat", "all") if qp.get("cat", "all") in ("all", *social.CATEGORY_BY_ID) else "all",
         "tipo": qp.get("tipo", "all") if qp.get("tipo", "all") in dict(social.WALL_TYPES) else "all",
         "q": (qp.get("q") or "")[:120], "desde": (qp.get("desde") or "")[:10]}
    posts, counts = social.wall(user, mod, cat=f["cat"], kind=f["tipo"], q=f["q"], since=f["desde"])
    return {"posts": posts, "wall_counts": counts, "wall_filter": f}


# ── portal: Comunidad ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/cartelera/comunidad", response_class=HTMLResponse, include_in_schema=False)
def community_page(request: Request):
    user, redirect = _reader(request)
    if redirect:
        return redirect
    return _render_portal(request, "comunidad", user=user, **wall_context(user, False, request))


# ── publicaciones ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.post("/cartelera/comunidad/publicaciones", include_in_schema=False)
async def post_create(request: Request):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: {"post": {"id": (p := social.save_post(user, data))["id"], "status": p["status"]}})


@router.post("/cartelera/comunidad/publicaciones/{post_id}", include_in_schema=False)
async def post_update(request: Request, post_id: str):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: {"post": {"id": (p := social.save_post(user, data, post_id))["id"], "status": p["status"]}})


@router.post("/cartelera/comunidad/publicaciones/{post_id}/eliminar", include_in_schema=False)
def post_delete(request: Request, post_id: str):
    user = _participant(request)
    return _run(lambda: social.delete_post(user, post_id))


# ── interacciones (motor compartido) ──────────────────────────────────────────────────────────────────────────────────────────────
@router.post("/cartelera/social/reaccion", include_in_schema=False)
async def api_react(request: Request):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: {"mine": social.react(user, str(data.get("target") or ""), str(data.get("r") or ""))})


@router.post("/cartelera/social/comentarios", include_in_schema=False)
async def api_comment(request: Request):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: {"id": social.add_comment(user, str(data.get("target") or ""), data.get("body"), str(data.get("parent") or ""))["id"]})


@router.post("/cartelera/social/comentarios/{comment_id}", include_in_schema=False)
async def api_comment_edit(request: Request, comment_id: str):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: {"id": social.edit_comment(user, comment_id, data.get("body"))["id"]})


@router.post("/cartelera/social/comentarios/{comment_id}/eliminar", include_in_schema=False)
def api_comment_delete(request: Request, comment_id: str):
    user = _participant(request)
    return _run(lambda: social.delete_comment(user, comment_id))


@router.post("/cartelera/social/voto", include_in_schema=False)
async def api_vote(request: Request):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: social.vote(user, str(data.get("post") or ""), int(data.get("i", -1))))


@router.post("/cartelera/social/reportes", include_in_schema=False)
async def api_report(request: Request):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: {"id": social.report(user, str(data.get("kind") or ""), str(data.get("id") or ""), str(data.get("reason") or ""),
                                             data.get("note"))["id"]})


@router.get("/cartelera/social/bloque", response_class=HTMLResponse, include_in_schema=False)
def api_block(request: Request, target: str = "", mini: str = ""):
    """Vuelve a dibujar el bloque de interacción de UN contenido (mismo componente que la página) tras una acción."""
    user = _reader_json(request)
    if not social.target_visible(target, user, False):
        raise HTTPException(404, "No disponible")
    names = social.people()
    ctx = {"social": social.snapshot([target], user, False, names), "me": social.uid(user), "me_name": social.uname(user),
           "social_reactions": social.REACTIONS, "social_when": social.fmt_when, "target": target, "mini": mini == "1"}
    return templates.TemplateResponse(request=request, name="cartelera/_social_fragment.html", context=ctx, headers=_NO_STORE)


@router.get("/cartelera/social/personas", include_in_schema=False)
def api_people(request: Request, q: str = ""):
    _reader_json(request)
    return JSONResponse({"people": social.search_people(q)}, headers=_NO_STORE)


@router.get("/cartelera/social/notificaciones", include_in_schema=False)
def api_notifications(request: Request):
    user = _reader_json(request)
    return JSONResponse({"items": social.notifications(user), "prefs": social.prefs(user)["notif"]}, headers=_NO_STORE)


@router.post("/cartelera/social/notificaciones", include_in_schema=False)
async def api_notifications_update(request: Request):
    user = _participant(request)
    data = await _json(request)
    return _run(lambda: social.save_prefs(user, notif=data.get("notif"), seen=bool(data.get("seen"))))


# ── Gestión de cartelera › Comunidad (COMMUNITY_OWNER; sin requerir ni otorgar Capital Humano) ─────────────────────────────────────
@router.get(LEGACY_MOD, include_in_schema=False)
def legacy_moderation_page():
    """La moderación vive en Gestión de cartelera › Comunidad: se conserva el enlace anterior."""
    return RedirectResponse(MOD, status_code=303)


@router.get(MOD, response_class=HTMLResponse, include_in_schema=False)
def moderation_page(request: Request):
    user, redirect = _moderator(request)
    if redirect:
        return redirect
    posts = social.list_posts()
    published = [p for p in social.wall_posts()]                                        # mismo orden que el muro (fijadas primero)
    names = social.people()
    pending = sorted([p for p in posts if social.post_state(p) == "pending"], key=social.post_when)
    scheduled = sorted([p for p in posts if social.post_state(p) == "scheduled"], key=social.post_when)
    removed = sorted([p for p in posts if social.post_state(p) == "removed"], key=lambda p: p.get("removed_at") or "", reverse=True)[:15]
    reports = [{**r, "content": social.report_target(r)} for r in social.open_reports()]
    since = (svc.now_utc().timestamp() - 30 * 86400)
    recent = lambda v: (svc.parse_utc(v) or svc.now_utc()).timestamp() >= since                     # noqa: E731
    comments = [c for c in social.list_comments() if c.get("status") != "removed"]
    recent_comments = [{**c, "where": social.target_label(c["target"])} for c in sorted(comments, key=lambda c: c.get("created_at", ""), reverse=True)
                       if recent(c.get("created_at"))][:30]
    reactions = sum(len(d.get("by") or {}) for d in repo.list_all("reactions"))
    kpis = {"posts": sum(1 for p in posts if social.post_state(p) == "published" and recent(social.post_when(p))),
            "comments": sum(1 for c in comments if recent(c.get("created_at"))), "reactions": reactions,
            "people": len(names), "pending": len(pending), "reports": len(reports)}
    return templates.TemplateResponse(request=request, name="human_capital/cartelera_comunidad.html", headers=_NO_STORE, context=_shell(
        request, user, "comunidad", pending=pending, scheduled=scheduled, removed=removed, reports=reports, log=social.modlog(),
        published=published, recent_comments=recent_comments, me=social.uid(user),
        names=names, kpis=kpis, categories=social.CATEGORY_BY_ID, social=social, reasons=social.MODERATION_REASONS))


@router.post(f"{MOD}/publicaciones/{{post_id}}/{{action}}", include_in_schema=False)
def moderation_post(request: Request, post_id: str, action: str, motivo: str = Form(""), nota: str = Form("")):
    user = _moderator_write(request)
    reason = " · ".join(x for x in (motivo.strip(), nota.strip()) if x)
    try:
        if action == "eliminar":                                                       # mismo servicio que la autoría (registra lo ajeno)
            social.delete_post(user, post_id)
        else:
            social.moderate_post(user, post_id, action, reason)
    except (social.SocialError, social.SocialForbidden, social.SocialNotFound) as exc:
        return _back(MOD, error=str(exc) or "La publicación ya no está disponible.")
    return _back(MOD, ok={"aprobar": "Publicación aprobada.", "rechazar": "Publicación rechazada.", "retirar": "Publicación retirada.",
                          "fijar": "Fijación actualizada.", "eliminar": "Publicación eliminada."}.get(action, "Hecho."))


@router.post(f"{MOD}/comentarios/{{comment_id}}/retirar", include_in_schema=False)
def moderation_comment(request: Request, comment_id: str, motivo: str = Form(""), nota: str = Form("")):
    user = _moderator_write(request)
    try:
        social.remove_comment(user, comment_id, " · ".join(x for x in (motivo.strip(), nota.strip()) if x))
    except (social.SocialError, social.SocialForbidden, social.SocialNotFound) as exc:
        return _back(MOD, error=str(exc) or "El comentario ya no está disponible.")
    return _back(MOD, ok="Comentario retirado.")


@router.post(f"{MOD}/reportes/{{report_id}}/{{action}}", include_in_schema=False)
def moderation_report(request: Request, report_id: str, action: str):
    """resolver · retirar (retira el contenido reportado y resuelve el reporte)."""
    user = _moderator_write(request)
    r = repo.get("reports", report_id) if report_id.isalnum() else None
    if not r:
        raise HTTPException(404, "El reporte no existe")
    try:
        if action == "retirar":
            kind, ident = r["target"].split(":", 1)
            if kind == "post":
                if (social.get_post(ident) or {}).get("status") != "removed":
                    social.moderate_post(user, ident, "retirar", r.get("reason", ""))
            else:
                c = repo.get("comments", ident)
                if c and c.get("status") != "removed":
                    social.remove_comment(user, ident, r.get("reason", ""))
        social.resolve_report(user, report_id)
    except (social.SocialError, social.SocialNotFound) as exc:
        return _back(MOD, error=str(exc) or "El contenido ya no está disponible.")
    return _back(MOD, ok="Contenido retirado y reporte resuelto." if action == "retirar" else "Reporte resuelto.")
