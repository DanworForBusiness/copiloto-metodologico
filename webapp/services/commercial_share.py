"""
commercial_share.py — Gestión Comercial → «Compartir» (02. Estrategia, presentación individual y presentaciones seleccionadas) → «Enviar por correo».

Envío MANUAL por el buzón institucional a destinatarios CORPORATIVOS del contexto Comercial (`share_recipients`, contexto `commercial`:
`COMMERCIAL_ALLOWED_USERS`; el contexto lo fija este módulo, nunca el navegador). Los ENLACES los resuelve el servidor desde lo que se comparte
(`kind` + ids validados contra el almacén de presentaciones): son las MISMAS direcciones que «Copiar enlace» (`/comercial/estrategia`,
`/presentaciones/<id>`), sin enlaces nuevos ni permisos nuevos; quien lo recibe lo abre con su propia sesión del Hub.

Reutiliza sin duplicar: transporte + auditoría (`institutional_mail`), plantilla institucional (`notifications/_layout.html`, identidad
Gestión Comercial vía `workspaces.brand_identity("commercial")`) y el filtro `email_lines`. Auditoría durable fail-closed bajo
`<NOTIFICATIONS_DIR>/manual/commercial-share/<operation_id>/{intent,result}.json` (ya persistido): una intención create-only por `operation_id`
ANTES de Graph (un mismo `operation_id` nunca produce un segundo envío) y el resultado después. Sin el cuerpo del mensaje ni tokens en los registros.

El remitente queda detrás de `send=` (hoy `institutional_mail.send`, app + buzón institucional): pasar a `/me/sendMail` delegado es cambiar ese
punto, no este flujo.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from webapp import workspaces
from webapp.services import institutional_mail, notifications
from webapp.services import share_recipients as _sr
from webapp.templating import templates

_logger = logging.getLogger("danwor.commercial_share")

CONTEXT = _sr.CONTEXT_COMMERCIAL
TEMPLATE = "notifications/commercial_share.html"
KIND_STRATEGY, KIND_PRESENTATIONS = "strategy", "presentations"
STRATEGY_PATH = "/comercial/estrategia"
STRATEGY_TITLE = "Estrategia comercial"
MAX_PRESENTATIONS = 20
MAX_RECIPIENTS = 20
SUBJECT_MAX = 200
MESSAGE_MAX = 5000
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DISABLED_MESSAGE = "El envío por correo institucional está deshabilitado. Podés usar «Copiar enlace»."
NOTE = "Quien lo reciba lo abre con su propia sesión del Hub: no se crean enlaces nuevos ni se cambian permisos."


def presentation_path(presentation_id: str) -> str:
    return f"/presentaciones/{presentation_id}"


def default_subject(kind: str, items: list[dict]) -> str:
    if kind == KIND_STRATEGY:
        return STRATEGY_TITLE
    if len(items) == 1:
        return f"Presentación: {items[0]['title']}"
    return f"Presentaciones comerciales ({len(items)})"


def dialog_context() -> dict:
    """Datos del diálogo ÚNICO «Compartir» de Comercial (Estrategia, presentación, selección): proveedor de destinatarios del contexto Comercial,
    interruptor global del envío y límites. Las rutas del servidor (catálogo/directorio/envío) son fijas de la vertical."""
    return {"directory_enabled": _sr.recipients_enabled(CONTEXT), "recipient_provider": _sr.recipient_provider(),
            "send_enabled": notifications.enabled(), "send_disabled_message": DISABLED_MESSAGE, "max_recipients": MAX_RECIPIENTS,
            "max_items": MAX_PRESENTATIONS, "catalog_url": "/comercial/compartir/catalogo", "directory_url": "/comercial/compartir/destinatarios",
            "send_url": "/comercial/compartir/enviar"}


def resolve_items(kind: str, presentation_ids: list[str], *, get_presentation) -> tuple[list[dict], list[str]]:
    """Lo que se comparte → ([{title, path}], ids no disponibles). Estrategia: su página. Presentaciones: SOLO ids existentes en el almacén
    (`get_presentation` lanza si no existe / no está disponible); el título y la ruta salen del servidor, nunca del navegador."""
    if kind == KIND_STRATEGY:
        return [{"title": STRATEGY_TITLE, "path": STRATEGY_PATH}], []
    if kind != KIND_PRESENTATIONS:
        return [], []
    items: list[dict] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in presentation_ids:
        pid = str(raw or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        try:
            view = get_presentation(pid)
        except Exception:                                            # noqa: BLE001 — inexistente / no disponible / almacenamiento: se omite
            missing.append(pid)
            continue
        items.append({"title": str(view.get("title") or pid), "path": presentation_path(pid)})
    return items, missing


def build_message(items: list[dict], recipients: list[dict], subject: str, message: str, origin: str | None) -> dict:
    """Payload Graph con la identidad institucional de Gestión Comercial: mensaje del usuario → lista de lo compartido (título + «Abrir en el Hub»,
    enlace absoluto resuelto acá; la URL larga no se muestra) → nota de acceso. Valores escapados por Jinja."""
    links = [{"title": i["title"], "url": notifications.hub_link(i["path"], origin)} for i in items]
    html = templates.env.get_template(TEMPLATE).render(
        title=subject, brand=dict(notifications.INSTITUTIONAL), vertical=workspaces.brand_identity("commercial"),
        d={"message": message, "items": links, "note": NOTE})
    return {"message": {"subject": subject, "body": {"contentType": "HTML", "content": html},
                        "toRecipients": [{"emailAddress": {"address": r["email"]}} for r in recipients]}}


def audit_root() -> Path:
    return Path(config.NOTIFICATIONS_DIR) / "manual" / "commercial-share"


def read_audit(operation_id: str) -> dict:
    base = audit_root() / operation_id
    return {"intent": institutional_mail.read_json(base / "intent.json"), "result": institutional_mail.read_json(base / "result.json")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def send_share(*, operation_id: str, actor: str, kind: str, presentation_ids: list[str], recipient_emails: list[str], subject: str,
               message: str, delegated_token: str, origin: str | None, get_presentation, send=None) -> dict:
    """Valida → audita intención (create-only) → envía → audita resultado. Devuelve un dict de estado para la UI; nunca lanza por errores
    esperados (salvo `DirectorySearchUnavailable` del proveedor de directorio, que la ruta traduce)."""
    send = send or institutional_mail.send
    if not OPERATION_ID_RE.match(operation_id or ""):
        return {"status": "error", "reason": "bad_operation", "message": "La operación venció o es inválida. Reabrí el diálogo e intentá de nuevo."}
    if not notifications.enabled():
        return {"status": "disabled", "reason": "disabled", "message": DISABLED_MESSAGE}
    if kind not in (KIND_STRATEGY, KIND_PRESENTATIONS):
        return {"status": "error", "reason": "bad_request", "message": "No se reconoce qué se quiere compartir."}
    if kind == KIND_PRESENTATIONS and len({str(p).strip() for p in presentation_ids if str(p).strip()}) > MAX_PRESENTATIONS:
        return {"status": "error", "reason": "too_many_items", "message": f"Se pueden compartir hasta {MAX_PRESENTATIONS} presentaciones por vez."}
    items, missing = resolve_items(kind, presentation_ids, get_presentation=get_presentation)
    if not items:
        return {"status": "error", "reason": "no_items", "message": "Ninguna de las presentaciones seleccionadas está disponible."}
    recipients, invalid = _sr.validate_recipients(recipient_emails, delegated_token, context=CONTEXT)
    if not recipients:
        return {"status": "error", "reason": "no_recipients", "message": "No hay destinatarios corporativos válidos seleccionados.", "invalid": invalid}
    if len(recipients) > MAX_RECIPIENTS:
        return {"status": "error", "reason": "too_many_recipients", "message": f"Se pueden enviar hasta {MAX_RECIPIENTS} destinatarios por vez."}
    subject = (subject or "").strip() or default_subject(kind, items)
    message = (message or "").replace("\r\n", "\n").strip()
    if len(subject) > SUBJECT_MAX or len(message) > MESSAGE_MAX:
        return {"status": "error", "reason": "too_long", "message": f"El asunto admite hasta {SUBJECT_MAX} caracteres y el mensaje hasta {MESSAGE_MAX}."}
    if notifications.missing_settings():
        return {"status": "config_incomplete", "reason": "config_incomplete", "message": "El correo institucional no está configurado; no se envió nada."}
    base = audit_root() / operation_id
    try:
        institutional_mail.write_audit(base / "intent.json", {
            "schema": 1, "operation_id": operation_id, "kind": f"commercial_share:{kind}", "started_at": _now(), "actor": actor,
            "paths": [i["path"] for i in items], "omitted": missing, "recipients": [r["email"] for r in recipients],
        }, create_only=True)
    except FileExistsError:
        prior = (read_audit(operation_id).get("result") or {}).get("outcome")
        return {"status": "duplicate", "message": "Este envío ya se había registrado; no se reenvió.", "prior_outcome": prior}
    except institutional_mail.AuditUnavailable:
        return {"status": "error", "reason": "audit_unavailable", "message": "No se pudo registrar la operación de forma durable; no se envió nada."}
    started = time.monotonic()
    state, detail = send(build_message(items, recipients, subject, message, origin))
    try:
        institutional_mail.write_audit(base / "result.json", {"schema": 1, "operation_id": operation_id, "finished_at": _now(), "outcome": state,
                                                              "detail": detail, "elapsed_ms": int((time.monotonic() - started) * 1000)})
    except institutional_mail.AuditUnavailable:
        _logger.warning("operation=commercial_share result_not_persisted op=%s outcome=%s", operation_id[:12], state)
    return {"status": state, "recipients": [r["email"] for r in recipients], "invalid": invalid, "shared": [i["title"] for i in items],
            "omitted": missing, "detail": detail}
