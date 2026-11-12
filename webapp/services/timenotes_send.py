"""
timenotes_send.py — Adaptador de NOTIFICACIONES de Carga de Horas (Operaciones). Acción MANUAL del usuario: individual o lote (N individuales).

Reutiliza SIN duplicar el transporte y la auditoría transversales (`institutional_mail.send` / `institutional_mail.write_audit`) y la plantilla
institucional (`notifications/_layout.html`). NO toca el contrato de eventos de `notifications.py` ni el comportamiento del transporte compartido.

Comportamiento PRODUCTIVO (owner 2026-11-12):
  * «Notificar» envía un correo PERSONALIZADO al COLABORADOR seleccionado y elegible. El destinatario es su email corporativo del padrón, que el
    ROUTER ya recalculó y validó en servidor (`notificar`); acá se vuelve a validar el formato del email y se corta si no es válido.
  * Remitente = buzón institucional `NOTIFICATIONS_SENDER` (`institutional_mail.send`, token de app). Envío condicionado por `NOTIFICATIONS_ENABLED`.
  * Lote = ejecución controlada de N envíos individuales (una falla no aborta el resto).
  * Auditoría durable por DESTINATARIO y SEMANA, con `operation_id` DETERMINÍSTICO (semana+email) → anti-duplicados (intención create-only ANTES del
    transporte). `accepted` (202, NO prueba entrega) · `failed` (4xx≠429) · `uncertain` (429/5xx/timeout, NUNCA se reintenta solo) · `duplicate`.
  * SIN modo prueba en el flujo operativo (se retiró; las pruebas automatizadas usan `transport=` simulado). NO hay redirección de destinatarios.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import config
from webapp import workspaces
from webapp.services import institutional_mail, notifications
from webapp.templating import templates

_logger = logging.getLogger("danwor.operations.timenotes.send")

TEMPLATE = "notifications/timenotes_reminder.html"
TITLE = "Recordatorio de carga de horas"
_EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def audit_root() -> Path:
    return Path(config.BASE_DIR) / "operations" / "timenotes"


def _sha8(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:8]


def operation_id_for(week_start: str, week_end: str, email: str) -> str:
    """Determinístico por (semana, email): dos envíos a la misma persona y semana colisionan (duplicado), nunca se reenvía."""
    return f"tn-{week_start}-{week_end}-{_sha8(email)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_recipient(colaborador_email: str) -> tuple[str, str]:
    """Destinatario EFECTIVO + error. SIEMPRE el email corporativo del colaborador (sin redirección). El router ya lo tomó del padrón y validó
    elegibilidad; acá sólo se revalida el formato (defensa adicional)."""
    email = (colaborador_email or "").strip()
    if not email or not _EMAIL_OK.match(email):
        return "", "dirección de colaborador inválida"
    return email, ""


def build_message(*, colaborador_email: str, nombre: str, semana_label: str, exigibles, registradas, faltantes,
                  recipient: str, hub_url: str = "") -> dict:
    """Mensaje Graph `sendMail` con la plantilla institucional (vertical Operaciones). `recipient` ya resuelto (email del colaborador)."""
    html = templates.env.get_template(TEMPLATE).render(
        title=TITLE, brand=dict(notifications.INSTITUTIONAL), vertical=workspaces.brand_identity("operations"),
        d={"nombre": nombre or colaborador_email, "semana": semana_label,
           "exigibles": _hx(exigibles), "registradas": _hx(registradas), "faltantes": _hx(faltantes), "hub_url": hub_url})
    subject = f"Recordatorio de carga de horas | Operaciones | Semana {semana_label}"
    return {"message": {"subject": subject[:255], "body": {"contentType": "HTML", "content": html},
                        "toRecipients": [{"emailAddress": {"address": recipient}}]}}


def _hx(value) -> str:
    try:
        f = float(value)
        return f"{f:g} h"
    except (TypeError, ValueError):
        return str(value)


def _write_intent(path: Path, payload: dict) -> None:
    institutional_mail.write_audit(path, payload, create_only=True)


def send_one(*, colaborador_email: str, nombre: str, semana: dict, exigibles, registradas, faltantes,
             actor: str, hub_url: str = "",
             enabled=notifications.enabled, missing=notifications.missing_settings,
             transport=institutional_mail.send) -> dict:
    """Un envío individual, auditado y anti-duplicado. `semana` = {"inicio","fin","label"}. Devuelve un dict de estado para la UI/lote.
    Precondición ANTES de escribir la intención (sin registro huérfano): interruptor global y configuración; así un reintento tras habilitar no
    queda bloqueado como duplicado. `actor` = quién disparó el envío (sólo auditoría; NO es el destinatario)."""
    inicio, fin, label = semana.get("inicio", ""), semana.get("fin", ""), semana.get("label", "")
    base = {"colaborador": colaborador_email, "nombre": nombre, "semana": label}

    if not enabled():
        return {**base, "status": "disabled", "detail": "envío institucional deshabilitado (NOTIFICATIONS_ENABLED=false)"}
    if missing():
        return {**base, "status": "config_incomplete", "detail": "configuración de correo incompleta"}

    recipient, err = resolve_recipient(colaborador_email)
    if err:
        return {**base, "status": "error", "detail": err}

    op_id = operation_id_for(inicio, fin, colaborador_email)
    intent_path = audit_root() / op_id / "intent.json"
    try:
        _write_intent(intent_path, {
            "schema": 1, "operation_id": op_id, "kind": "timenotes_reminder", "started_at": _now_iso(),
            "actor": actor, "colaborador_email": colaborador_email, "recipient_actual": recipient,
            "semana_inicio": inicio, "semana_fin": fin,
            "horas_exigibles": exigibles, "horas_registradas": registradas, "horas_faltantes": faltantes,
        })
    except FileExistsError:
        prior = institutional_mail.read_json(audit_root() / op_id / "result.json") or {}
        return {**base, "status": "duplicate", "detail": "ya registrado para esta semana y destinatario; no se reenvió.",
                "prior_outcome": prior.get("outcome"), "recipient": recipient}
    except institutional_mail.AuditUnavailable:
        return {**base, "status": "error", "detail": "no se pudo registrar la operación de forma durable; no se envió nada."}

    state, detail = transport(build_message(
        colaborador_email=colaborador_email, nombre=nombre, semana_label=label, exigibles=exigibles,
        registradas=registradas, faltantes=faltantes, recipient=recipient, hub_url=hub_url))
    try:
        institutional_mail.write_audit(audit_root() / op_id / "result.json", {
            "schema": 1, "operation_id": op_id, "finished_at": _now_iso(), "outcome": state, "detail": detail,
            "recipient_actual": recipient})
    except institutional_mail.AuditUnavailable:
        _logger.warning("operation=timenotes result_not_persisted op=%s outcome=%s", op_id, state)
    return {**base, "status": state, "detail": detail, "recipient": recipient, "operation_id": op_id}


def send_batch(destinatarios: list[dict], *, semana: dict, actor: str, hub_url: str = "",
               enabled=notifications.enabled, missing=notifications.missing_settings,
               transport=institutional_mail.send) -> dict:
    """Lote = N envíos individuales controlados. Una falla NO aborta el resto. `destinatarios`: [{email,nombre,exigibles,registradas,faltantes}].
    Devuelve {"resultados":[...], "resumen":{accepted,duplicate,failed,uncertain,error,disabled,config_incomplete,total}}."""
    resultados = []
    for d in destinatarios:
        resultados.append(send_one(
            colaborador_email=str(d.get("email") or ""), nombre=str(d.get("nombre") or ""), semana=semana,
            exigibles=d.get("exigibles"), registradas=d.get("registradas"), faltantes=d.get("faltantes"),
            actor=actor, hub_url=hub_url, enabled=enabled, missing=missing, transport=transport))
    resumen: dict[str, int] = {"total": len(resultados)}
    for r in resultados:
        resumen[r["status"]] = resumen.get(r["status"], 0) + 1
    return {"resultados": resultados, "resumen": resumen}
