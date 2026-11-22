"""
governed_updates.py — Solicitudes de actualización del Assessment gobernado (PR-1: solicitud, notificación y revisión con dry-run de SOLO LECTURA).

  POST /copilot/governed-updates/request                   desde un Assessment AD HOC propio: recalcula la oportunidad en el servidor, registra (o
                                                           reutiliza) la solicitud y notifica a Arquitectura. 303 → la solicitud.
  GET  /copilot/governed-updates/{request_id}              revisión autenticada (quien la pidió o APP_OWNER / CONTENT_OWNER).
  POST /copilot/governed-updates/{request_id}/preview      dry-run gobernado de un modelo (solo APP_OWNER / CONTENT_OWNER), en un hilo. 303.
  GET  /copilot/governed-updates/{request_id}/preview      fragmento del estado del dry-run (HTMX, mientras corre).

Seguridad: sesión obligatoria (401); los POST exigen mismo origen; la solicitud ajena es indistinguible de una inexistente (404). PR-1 NO ofrece
aprobar ni ejecutar: nada de esta superficie escribe Assessments gobernados ni Spaces de cliente. La aprobación y la ejecución son de PR-2.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK, content_pack_short_label, resolve_pack
from webapp import same_origin_guard, workspaces
from webapp.services import assessment_presenter as presenter
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as dsa
from webapp.services import governed_update_requests as gur
from webapp.services import notifications
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity
from webapp.services.assessment_acquisition import CONTRACT_CLASS_TEXT
from webapp.services.content_service import academy_asset_for, list_academy, list_appendices, list_modules
from webapp.services.status_tone import status_tone
from webapp.templating import templates

router = APIRouter()
log = logging.getLogger(__name__)
BASE = gur.BASE_PATH


def registry_unavailable_response(exc: gur.RegistryUnavailable):
    """Respuesta CONTROLADA mientras el registro no está restaurado (tras un redeploy): 503 + Retry-After; el usuario reintenta en unos segundos."""
    from fastapi.responses import PlainTextResponse
    log.warning("governed_update registry unavailable reason=%s", exc.reason)
    return PlainTextResponse("El registro de solicitudes se está restaurando después de una actualización del Hub. Reintentá en unos segundos.",
                             status_code=503, headers={"Retry-After": str(gur.RETRY_AFTER_S)})


def _user(request: Request) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return user


def _write_guard(request: Request) -> None:
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")


def _visible(request: Request, request_id: str) -> tuple[dict, dict]:
    user = _user(request)
    rec = gur.load(request_id)
    if rec is None or not gur.can_view(user, rec):
        raise HTTPException(404, "La solicitud no existe.")
    return user, rec


def _activity(event: str, user: dict, **attrs) -> None:
    try:
        log_activity(event, user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, **attrs)
    except Exception as exc:                                                       # noqa: BLE001 — la telemetría nunca rompe el circuito
        log.warning("governed_update activity %s failed exc=%s", event, type(exc).__name__)


def _level_badge(j: dict | None) -> dict:
    level = (j or {}).get("level")
    label = presenter._JDG_LEVEL_LABEL.get(level or "", "No determinable")
    return {"label": label, "tone": status_tone(label)}


def _contracts_text(rec: dict) -> list[str]:
    gov, cand = (rec.get("governed") or {}).get("source_contracts") or {}, (rec.get("source") or {}).get("source_contracts") or {}
    return [CONTRACT_CLASS_TEXT.get(k, k) for k in sorted(k for k in cand if cand.get(k, 0) != gov.get(k, 0))]


# ── solicitar ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.post(f"{BASE}/request", include_in_schema=False)
def request_update(request: Request, result_id: str = Form(""), stem: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    user = _user(request)
    _write_guard(request)
    if not dsa.tool_enabled(stem) or not dsa.can_execute(request):
        raise HTTPException(403, "No autorizado")
    record = dsa.load_result(result_id)
    if record is None or str(record.get("owner") or "") != dsa.owner_email(request):
        raise HTTPException(404, "El resultado no existe o venció (los assessments ad-hoc duran 24 horas).")
    status = dsa.result_status(record)
    profile = status.profile if isinstance(status.profile, dict) else None
    try:
        rec, created = gur.create(client_dir=str(record.get("client_dir") or status.client), client_display=str(record.get("client_display") or status.client),
                                  terminal_am=str(status.terminal_am), space=record.get("space"), result_id=result_id,
                                  candidate_judgment=status.judgment, candidate_profile=profile, requested_by=dsa.owner_email(request))
    except gur.NoOpportunity:
        raise HTTPException(409, "Ya no hay una diferencia relevante con el Assessment gobernado: no se registró ninguna solicitud.")
    _activity("governed_update.requested", user, request_id=rec["request_id"], client=rec["client_dir"], model=rec["terminal_am"],
              result_id=result_id, space=(rec.get("source") or {}).get("space"), created=created,
              vigente_level=((rec.get("governed") or {}).get("judgment") or {}).get("level") or "ND",
              candidato_level=((rec.get("source") or {}).get("judgment") or {}).get("level") or "ND",
              provenance=(rec.get("provenance") or {}).get("status"))
    if created:
        _notify(rec, request, user)
    return RedirectResponse(f"{BASE}/{rec['request_id']}?" + urlencode({"pack": resolve_pack(pack)}), status_code=303)


def _notify(rec: dict, request: Request, user: dict) -> None:
    """Correo a Arquitectura (`NOTIFICATIONS_ARQ_RECIPIENTS`) por el servicio de notificaciones del Hub: registro durable, deduplicación por
    `request_id`, reintentos y estados son de ese servicio. Apagadas: la solicitud queda registrada igual (`dispatch=disabled`)."""
    dispatch = "disabled"
    if notifications.enabled():
        try:
            origin = request.headers.get("origin") or f"{request.url.scheme}://{request.url.netloc}"   # mismo origen ya validado
            gov, cand = _level_badge((rec.get("governed") or {}).get("judgment")), _level_badge((rec.get("source") or {}).get("judgment"))
            dispatch = notifications.emit(gur.NOTIFY_EVENT, rec["request_id"], {
                "id": rec["request_id"], "client": rec.get("client"), "model": rec.get("terminal_am"),
                "vigente": gov["label"], "candidato": cand["label"], "contracts": ", ".join(_contracts_text(rec)),
                "provenance": (rec.get("provenance") or {}).get("text"), "requester": rec.get("requested_by"),
                "requested_at": (rec.get("requested_at") or "").replace("T", " ").replace("+00:00", " UTC"),
                "url": notifications.hub_link(f"{BASE}/{rec['request_id']}", origin),
            }, origin=origin)
        except Exception as exc:                                                   # noqa: BLE001 — accesoria: la solicitud ya está registrada
            log.warning("governed_update notify failed request=%s exc=%s", rec["request_id"], type(exc).__name__)
            dispatch = "error"
    gur.record_notification(rec, dispatch)
    _activity("governed_update.notified", user, request_id=rec["request_id"], client=rec["client_dir"], model=rec["terminal_am"], dispatch=dispatch)


# ── presentación de la revisión (solo lectura; no cambia validaciones ni elegibilidad) ──
# Estado del correo: «aceptada» = el servidor de correo (Graph) la ACEPTÓ (202); NUNCA se presenta como entrega confirmada.
NOTIFICATION_STATES = {
    "accepted": ("Aceptada por el servidor de correo", "plena", True, "Graph aceptó el envío; la entrega final al buzón no se confirma."),
    "pending": ("En cola", "neutra", False, ""),
    "sending": ("En envío", "neutra", False, ""),
    "retry": ("Reintentando", "buena", False, "El servicio reintenta solo; no hace falta reenviar."),
    "uncertain": ("Incierta", "parcial", True, "No se sabe si salió; no se reenvía automáticamente para no duplicar."),
    "failed": ("Fallida", "parcial", True, "No se pudo enviar. La solicitud sigue registrada."),
    "disabled": ("Notificaciones apagadas", "neutra", True, "En este entorno no se envían correos. La solicitud sigue registrada."),
    "error": ("No se pudo emitir", "parcial", True, "La solicitud sigue registrada."),
    "unknown": ("Sin dato", "neutra", True, ""),
}
PREVIEW_STAGE = {
    gur.PREVIEW_READY: ("Plan listo", "plena"), gur.PREVIEW_NO_CHANGE: ("Sin cambios", "neutra"), gur.PREVIEW_ABSTAINED: ("Se abstendría", "parcial"),
    gur.PREVIEW_FAILED: ("No se pudo calcular", "parcial"), gur.PREVIEW_OBSOLETE: ("Obsoleta", "parcial"), gur.PREVIEW_NOT_FOUND: ("Modelo no encontrado", "parcial"),
}


NOTIFICATION_POLL_MAX_S = 600            # el refresco del estado del correo se detiene en un estado terminal o a los 10 min de la solicitud


def _age_s(iso: str | None) -> float:
    from datetime import datetime, timezone
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _notification_view(rec: dict, delivery: dict | None) -> dict:
    n = rec.get("notification") or {}
    if not n:
        state = "pending"
    elif n.get("dispatch") in ("disabled", "error"):
        state = n["dispatch"]
    else:
        state = (delivery or {}).get("state") or "pending"
    label, tone, terminal, detail = NOTIFICATION_STATES.get(state, NOTIFICATION_STATES["unknown"])
    return {"state": state, "label": label, "tone": tone, "terminal": terminal, "detail": detail}


def _stages(rec: dict, role: str | None, state: str, prev: dict | None, preview_running: bool, can_preview: bool) -> list[dict]:
    """Etapas del circuito, SEPARADAS (autorización ≠ procedencia histórica ≠ origen autorizado ≠ dry-run ≠ aplicación). Solo presenta datos ya
    calculados; la elegibilidad real sigue siendo ``gur.eligibility`` (sin cambios)."""
    prov = rec.get("provenance") or {}
    stages = [{"key": "authorization", "label": "Autorización",
               "badge": (f"Autorizado · {role}" if role else "Solo lectura"), "tone": "plena" if role else "neutra",
               "detail": ("Podés calcular el dry-run gobernado. La aprobación llega en una próxima versión." if role
                          else "Revisa y aprueba Arquitectura (APP_OWNER o CONTENT_OWNER).")}]
    if prov.get("verified"):
        stages.append({"key": "historical_provenance", "label": "Procedencia del Assessment vigente", "badge": f"Space «{prov.get('governed_space')}»",
                       "tone": "plena", "detail": "Registrada en el Assessment gobernado."})
    else:
        stages.append({"key": "historical_provenance", "label": "Procedencia del Assessment vigente", "badge": "Desconocida", "tone": "neutra",
                       "detail": "El Assessment gobernado no registra el Space del que proviene; no se infiere."
                                 + (f" El registro de publicación indica «{prov['published_space']}» (evidencia auxiliar)." if prov.get("published_space") else "")})
    stages.append({"key": "authorized_origin", "label": "Origen autorizado para la reconstrucción", "badge": "Pendiente de declaración", "tone": "buena",
                   "detail": "Se declara al aprobar (próxima versión)."
                             + (f" La evaluación ad hoc se hizo en el Space «{prov['candidate_space']}»." if prov.get("candidate_space") else "")})
    if preview_running:
        dry = {"badge": "Calculando…", "tone": "neutra", "detail": "Solo lectura: no modifica el Assessment gobernado."}
    elif prev:
        badge, tone = PREVIEW_STAGE.get(prev.get("state"), ("Sin dato", "neutra"))
        dry = {"badge": badge, "tone": tone, "detail": ""}
    else:
        dry = {"badge": "Pendiente", "tone": "neutra", "detail": "Todavía no se calculó." if can_preview else "Lo calcula Arquitectura."}
    stages.append({"key": "dry_run", "label": "Dry-run gobernado", **dry})
    stages.append({"key": "application", "label": "Aplicación", "badge": "No disponible en esta versión", "tone": "neutra",
                   "detail": "La actualización real seguirá el procedimiento gobernado supervisado (aprobación explícita y aplicación asegurada con respaldo)."})
    return stages


def _next_step(state: str, role: str | None, prev: dict | None, preview_running: bool) -> str:
    if state == gur.OBSOLETE:
        return "El Assessment gobernado cambió: si la mejora sigue vigente, generá una evaluación ad hoc nueva y volvé a solicitarla."
    if not role:
        return "Arquitectura revisa la solicitud y calcula el dry-run gobernado."
    if preview_running:
        return "Esperá el resultado del dry-run gobernado."
    if not prev:
        return "Calcular el dry-run gobernado (solo lectura)."
    if prev.get("state") == gur.PREVIEW_READY:
        return "Declarar el origen autorizado y aprobar la actualización: disponible en una próxima versión."
    return "Revisar la causa del dry-run antes de continuar."


# ── revisar ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _view(rec: dict, user: dict, pack: str) -> dict:
    role = gur.approver_role(user)
    state = gur.current_status(rec)
    delivery = None
    if rec.get("notification"):
        try:
            delivery = notifications.delivery_status(gur.NOTIFY_EVENT, rec["request_id"])
        except Exception:                                                          # noqa: BLE001
            delivery = None
    prev = rec.get("preview") or None
    running = gur.preview_running(rec["request_id"])
    can_preview = bool(role) and state == gur.OPEN
    notification = _notification_view(rec, delivery)
    return {
        "notification": notification, "stages": _stages(rec, role, state, prev, running, can_preview),
        "next_step": _next_step(state, role, prev, running), "role_label": role or "Solo lectura",
        "poll": running or (not notification["terminal"] and _age_s(rec.get("requested_at")) < NOTIFICATION_POLL_MAX_S),
        "rec": rec, "state": state, "obsolete": state == gur.OBSOLETE, "role": role, "can_preview": can_preview,
        "is_requester": gur.user_email(user) == rec.get("requested_by"),
        "vigente": _level_badge((rec.get("governed") or {}).get("judgment")), "candidato": _level_badge((rec.get("source") or {}).get("judgment")),
        "contracts": _contracts_text(rec), "provenance": rec.get("provenance") or {}, "delivery": delivery,
        "preview": prev, "preview_running": running,
        "preview_to": _level_badge({"level": ((prev or {}).get("judgment") or {}).get("to")}) if (prev or {}).get("judgment") else None,
        "eligibility": gur.eligibility(rec), "base": f"{BASE}/{rec['request_id']}", "pack": pack,
    }


def _shell(request: Request, user: dict, pack: str) -> dict:
    academy = [a for a in list_academy(pack) if not a.nav_hidden]
    return {"modules": list_modules(pack), "appendices": [a for a in list_appendices(pack) if not a.nav_hidden], "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, pack) for a in academy}, "active_group": "core", "active_stem": "",
            "readonly": False, "pack": pack, "pack_label": content_pack_short_label(pack), "user": user,
            "current_workspace": workspaces.COPILOT, "available_workspaces": workspaces.available_workspaces(user)}


@router.get(f"{BASE}/{{request_id}}", response_class=HTMLResponse, include_in_schema=False)
def review(request: Request, request_id: str, pack: str = Query(DEFAULT_PACK)):
    user, rec = _visible(request, request_id)
    selected = resolve_pack(pack)
    role = gur.approver_role(user)
    if role and gur.record_review(rec, user, role):                                # revisión = la abre alguien que puede aprobar (la recarga no cuenta)
        _activity("governed_update.reviewed", user, request_id=request_id, client=rec["client_dir"], model=rec["terminal_am"], role=role,
                  status=gur.current_status(rec), provenance=(rec.get("provenance") or {}).get("status"))
    return templates.TemplateResponse(request=request, name="governed_update_request.html",
                                      context={"g": _view(rec, user, selected), **_shell(request, user, selected)})


@router.post(f"{BASE}/{{request_id}}/preview", include_in_schema=False)
def preview(request: Request, request_id: str, pack: str = Form(DEFAULT_PACK)):
    user, rec = _visible(request, request_id)
    _write_guard(request)
    role = gur.approver_role(user)
    if not role:
        raise HTTPException(403, "Solo APP_OWNER o CONTENT_OWNER pueden calcular el dry-run.")

    def done(fresh: dict, result: dict) -> None:
        _activity("governed_update.dry_run", user, request_id=request_id, client=fresh["client_dir"], model=fresh["terminal_am"], role=role,
                  state=result.get("state"), path=result.get("path"), plan_digest=(result.get("plan_digest") or "")[:12] or None,
                  projected_level=((result.get("judgment") or {}).get("to") or "ND") if result.get("judgment") else None,
                  abstentions=len(result.get("abstentions") or []), zero_writes=result.get("zero_writes"), duration_s=result.get("duration_s"))

    outcome = gur.start_preview(rec, user, role, on_done=done)
    if outcome == "OBSOLETE":
        _activity("governed_update.dry_run", user, request_id=request_id, client=rec["client_dir"], model=rec["terminal_am"], role=role,
                  state=gur.PREVIEW_OBSOLETE)
    return RedirectResponse(f"{BASE}/{request_id}?" + urlencode({"pack": resolve_pack(pack)}), status_code=303)


@router.get(f"{BASE}/{{request_id}}/preview", response_class=HTMLResponse, include_in_schema=False)
def preview_fragment(request: Request, request_id: str, pack: str = Query(DEFAULT_PACK)):
    user, rec = _visible(request, request_id)
    return templates.TemplateResponse(request=request, name="partials/governed_update_status.html",      # estado (dry-run + correo): solo lectura, sin reenvíos
                                      context={"g": _view(gur.load(request_id) or rec, user, resolve_pack(pack))})
