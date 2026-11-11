"""
institutional_mail.py — Transporte y auditoría TRANSVERSALES del correo institucional del Hub (buzón `NOTIFICATIONS_SENDER`, identidad de
aplicación `Hub-Danwor-Notifications`), para envíos MANUALES iniciados por un usuario (p. ej. Capital Humano → Contactar candidato).

Reutiliza sin duplicar: `notifications.enabled()`/`missing_settings()` (interruptor global y configuración), `microsoft365_app.acquire_app_token`
(client credentials) y `microsoft365.graph_post_accepted` (transporte Graph existente). La auditoría usa el mismo patrón fail-closed que el resto
del Hub: intención create-only por `operation_id` ANTES de Graph (anti doble envío) y resultado después, vía `remote_store.write_through`.
No conoce destinatarios ni plantillas: eso lo decide cada adaptación funcional.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import config
from core import microsoft365, microsoft365_app, remote_store
from webapp.services import notifications

GRAPH_SEND_MAIL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


class AuditUnavailable(RuntimeError):
    """La auditoría durable no se pudo confirmar: el envío no empieza (fail-closed)."""


def send(message: dict, *, token_fn=microsoft365_app.acquire_app_token, post=microsoft365.graph_post_accepted) -> tuple[str, str]:
    """Envía `message` (payload Graph `sendMail`) por el buzón institucional. Devuelve (estado, detalle sin secretos):
    `accepted` (202, NO prueba entrega) · `failed` (4xx≠429) · `uncertain` (429/5xx/timeout/red/≠202: nunca se reenvía solo) ·
    `disabled` (interruptor global apagado: Graph NO se llamó) · `config_incomplete`. El interruptor se verifica otra vez justo antes del POST."""
    if not notifications.enabled():
        return "disabled", "envío institucional deshabilitado (NOTIFICATIONS_ENABLED=false)"
    if notifications.missing_settings():
        return "config_incomplete", "configuración de correo incompleta"
    try:
        token = token_fn(config.NOTIFICATIONS_TENANT_ID, config.NOTIFICATIONS_CLIENT_ID, config.NOTIFICATIONS_CLIENT_SECRET)
    except Exception as exc:                                         # noqa: BLE001 — token de app no disponible
        return "uncertain", f"no se obtuvo el token de aplicación ({type(exc).__name__})"
    url = GRAPH_SEND_MAIL.format(sender=quote(config.NOTIFICATIONS_SENDER, safe="@"))
    if not notifications.enabled():
        return "disabled", "envío institucional deshabilitado antes de enviar (NOTIFICATIONS_ENABLED=false)"
    try:
        status = post(url, token, message)
    except HTTPError as exc:
        return ("failed" if 400 <= exc.code < 500 and exc.code != 429 else "uncertain"), f"Graph HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return "uncertain", f"error de red ({type(exc).__name__})"
    return ("accepted" if status == 202 else "uncertain"), f"HTTP {status}"


def write_audit(path: Path, payload: dict, *, create_only: bool = False) -> None:
    """Registro durable (local + Spaces cuando la persistencia remota está activa). `create_only` = `FileExistsError` si ya existe."""
    if remote_store.read_only_mode():
        raise AuditUnavailable("modo de solo lectura")
    if create_only and path.exists():
        raise FileExistsError(str(path))
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    try:
        outcome = remote_store.write_through(path, text)
    except OSError:
        raise AuditUnavailable("no se pudo escribir el registro") from None
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise AuditUnavailable("registro no confirmado en Spaces")


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
