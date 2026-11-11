"""
human_capital_contact_send.py — Capital Humano → 02. Búsquedas → «Contactar candidato» → canal Mail → «Enviar».

Envío MANUAL a un destinatario EXTERNO: el correo vigente del candidato, resuelto SIEMPRE en el servidor desde la postulación de ESA búsqueda (el
navegador nunca aporta la dirección). No usa catálogos de destinatarios internos (`HUMAN_CAPITAL_ALLOWED_USERS`/`HC_SHARE_RECIPIENTS_PROVIDER`).
Reutiliza el transporte/auditoría transversal (`institutional_mail`), la plantilla institucional (identidad Capital Humano) y el filtro
`email_lines`. La auditoría durable (`human-capital/contact-mails/<operation_id>/{intent,result}.json`) es la fuente técnica de verdad: un mismo
`operation_id` nunca produce un segundo envío. Sin cuerpo del mensaje, tokens ni la dirección en claro en los registros técnicos.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from webapp import workspaces
from webapp.services import human_capital_contact as contact
from webapp.services import institutional_mail, notifications
from webapp.templating import templates

TEMPLATE = "notifications/candidate_contact.html"
SUBJECT_MAX = 200
BODY_MAX = 5000
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DISABLED_MESSAGE = "El envío por correo institucional está deshabilitado. Podés usar «Copiar mensaje»."
HISTORY_ACTION = {"accepted": "correo_aceptado", "failed": "correo_fallido", "uncertain": "correo_incierto"}


def audit_root() -> Path:
    return Path(config.BASE_DIR) / "human-capital" / "contact-mails"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_audit(operation_id: str) -> dict:
    base = audit_root() / operation_id
    return {"intent": institutional_mail.read_json(base / "intent.json"), "result": institutional_mail.read_json(base / "result.json")}


def build_message(recipient: str, subject: str, body: str) -> dict:
    """Payload Graph con la identidad institucional de Capital Humano. Solo el texto del usuario (escapado, saltos como `<br>`): sin adjuntos,
    sin enlaces agregados, sin intro redundante; pie adecuado a un destinatario externo."""
    html = templates.env.get_template(TEMPLATE).render(
        title=subject, brand=dict(notifications.INSTITUTIONAL), vertical=workspaces.brand_identity("human-capital"), d={"body": body})
    return {"message": {"subject": subject, "body": {"contentType": "HTML", "content": html},
                        "toRecipients": [{"emailAddress": {"address": recipient}}]}}


def send_contact(*, operation_id: str, actor: str, busqueda: dict | None, application_id: str, situacion: str, subject: str, body: str,
                 get_profile, send=None) -> dict:
    """Valida → audita intención (create-only) → envía → audita resultado. Devuelve un dict de estado para la UI; nunca lanza por errores
    esperados. `recipient` solo viaja en la respuesta (para el aviso al usuario), nunca a los registros técnicos."""
    send = send or institutional_mail.send
    if not OPERATION_ID_RE.match(operation_id or ""):
        return {"status": "error", "reason": "bad_operation", "message": "La operación venció o es inválida. Reabrí el diálogo e intentá de nuevo."}
    if not notifications.enabled():
        return {"status": "disabled", "reason": "disabled", "message": DISABLED_MESSAGE}
    if situacion not in dict(contact.TIPOS_MENSAJE):
        return {"status": "error", "reason": "bad_request", "message": "Situación inválida."}
    if busqueda is None:
        return {"status": "error", "reason": "not_found", "message": "Búsqueda no encontrada."}
    if busqueda.get("estado") != "activo":
        return {"status": "error", "reason": "inactive", "message": "El proceso no está activo."}
    app = next((a for a in busqueda.get("candidatos", []) if a.get("application_id") == application_id), None)
    profile = get_profile(app.get("profile_id")) if app and app.get("profile_id") else None
    if app is None or not profile or profile.get("deleted_at"):
        return {"status": "error", "reason": "not_found", "message": "El candidato no pertenece a esta búsqueda o ya no está disponible."}
    recipient = contact.valid_email(profile.get("email"))
    if not recipient:
        return {"status": "error", "reason": "no_email", "message": "El candidato no tiene un email válido cargado."}
    subject = (subject or "").strip()
    body = (body or "").replace("\r\n", "\n").strip()
    if not subject or not body:
        return {"status": "error", "reason": "empty", "message": "Completá el asunto y el mensaje."}
    if len(subject) > SUBJECT_MAX or len(body) > BODY_MAX:
        return {"status": "error", "reason": "too_long", "message": f"El asunto admite hasta {SUBJECT_MAX} caracteres y el mensaje hasta {BODY_MAX}."}
    if notifications.missing_settings():
        return {"status": "config_incomplete", "reason": "config_incomplete", "message": "El correo institucional no está configurado; no se envió nada."}
    base = audit_root() / operation_id
    try:
        institutional_mail.write_audit(base / "intent.json", {
            "schema": 1, "operation_id": operation_id, "kind": "candidate_contact", "started_at": _now(), "actor": actor,
            "busqueda_id": busqueda.get("busqueda_id") or busqueda.get("id"), "application_id": application_id, "profile_id": app.get("profile_id"),
            "situacion": situacion, "recipient_sha256": hashlib.sha256(recipient.lower().encode()).hexdigest(),
        }, create_only=True)
    except FileExistsError:
        prior = (read_audit(operation_id).get("result") or {}).get("outcome")
        return {"status": "duplicate", "message": "Este envío ya se había registrado; no se reenvió.", "prior_outcome": prior}
    except institutional_mail.AuditUnavailable:
        return {"status": "error", "reason": "audit_unavailable", "message": "No se pudo registrar la operación de forma durable; no se envió nada."}
    started = time.monotonic()
    state, detail = send(build_message(recipient, subject, body))
    try:
        institutional_mail.write_audit(base / "result.json", {"schema": 1, "operation_id": operation_id, "finished_at": _now(), "outcome": state,
                                                              "detail": detail, "elapsed_ms": int((time.monotonic() - started) * 1000)})
    except institutional_mail.AuditUnavailable:
        pass                                                         # la intención ya bloquea cualquier reenvío del mismo operation_id
    return {"status": state, "recipient": recipient, "detail": detail}
