"""
human_capital_profile_send.py — Envío MANUAL de perfiles por correo (CCHH → 01. Candidatos → Compartir perfiles → «Enviar por correo»).

Es una COMUNICACIÓN manual explícita del usuario (no un evento automático del workflow). Reutiliza:
  * `human_capital_share` — resolución segura de candidatos + enlaces de CV (`web_url` guardado, `safe_cv_url`); privacidad: solo nombre + enlace.
  * Transporte Microsoft Graph institucional de las notificaciones — token de APP (client credentials, `Mail.Send`) + `graph_post_accepted`, remitente `NOTIFICATIONS_SENDER`. NO se usa la sesión delegada para ENVIAR.
  * Plantilla HTML compartida (`notifications/_layout.html` + `_blocks.html`), identidad de la vertical Capital Humano desde `workspaces.brand_identity`.
  * Auditoría durable (mismo patrón fail-closed que `human_capital_export`): `human-capital/cv-shares/<operation_id>/{intent,result}.json`.

DIRECTORIO (selector de destinatarios): se consulta Microsoft Entra con el token DELEGADO del usuario autenticado (`/users?$search`). Permiso mínimo del endpoint: `User.ReadBasic.All` (sujeto a verificación real). Si el consentimiento no está concedido, Graph responde 403 → `DirectorySearchUnavailable` (la búsqueda queda bloqueada con mensaje claro; el resto del formulario sigue funcionando). NO se agregan permisos a la identidad técnica de notificaciones ni se consultan ACLs de SharePoint: SharePoint gobierna el acceso a cada CV de forma independiente; el correo solo lleva los enlaces existentes y cada destinatario los abre con SUS permisos.

GOBIERNO: sin endpoint genérico de correo; el backend VALIDA candidatos (existentes + enlaces del sistema) y destinatarios (identidades corporativas resueltas en el directorio); sin adjuntos, sin enlaces nuevos, sin datos personales innecesarios; auditoría sin contenido de CV; anti doble-envío por `operation_id` (create-only).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import config
from core import microsoft365, microsoft365_app, remote_store
from webapp import workspaces
from webapp.services import human_capital_share, notifications
from webapp.templating import templates

_logger = logging.getLogger("danwor.cchh.profile_send")

GRAPH_USERS_SEARCH = ("https://graph.microsoft.com/v1.0/users?$search=%22displayName:{q}%22%20OR%20%22mail:{q}%22"
                      "&$select=id,displayName,mail,userPrincipalName&$top={top}&$count=true")
GRAPH_USER_GET = "https://graph.microsoft.com/v1.0/users/{id}?$select=id,displayName,mail,userPrincipalName"
GRAPH_SEND_MAIL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

MIN_QUERY = 3
MAX_RESULTS = 15
MAX_RECIPIENTS = 20
SUBJECT_MAX = 200
MESSAGE_MAX = 4000
TEMPLATE = "notifications/profiles_shared.html"
TITLE = "Perfiles de candidatos"
SHAREPOINT_NOTE = "El acceso a los CVs está sujeto a los permisos de SharePoint del destinatario."
DEFAULT_SUBJECT = human_capital_share.DEFAULT_SUBJECT
DISABLED_MESSAGE = "El envío por correo institucional está deshabilitado. Podés usar «Copiar mensaje»."
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SEARCH_SAFE = re.compile(r'[\\"]')                                  # se quitan comillas/backslash del término $search
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# ── Destinatarios: capacidad TRANSVERSAL (`share_recipients`); Compartir perfiles es el contexto Capital Humano ──
from webapp.services import share_recipients as _sr
from webapp.services.share_recipients import (  # noqa: E402,F401 — reexportados (API y costuras de tests existentes)
    PROVIDER_ALLOWLIST, PROVIDER_DIRECTORY, DirectoryError, DirectorySearchUnavailable, directory_scope, needs_delegated_token,
    recipient_provider, roster_names, search_directory, validate_recipients,
)

CONTEXT = _sr.CONTEXT_HUMAN_CAPITAL


def allowlist_recipients() -> list[dict]:
    return _sr.allowlist_recipients(CONTEXT)


def allowlist_catalog(token: str) -> dict:
    return _sr.allowlist_catalog(CONTEXT, token)


def recipients_enabled() -> bool:
    return _sr.recipients_enabled(CONTEXT)


def search_recipients(query: str, token: str) -> list[dict]:
    return _sr.search_recipients(CONTEXT, query, token)


class ProfileSendError(RuntimeError):
    """Base (fail-closed)."""


class SendAuditUnavailable(ProfileSendError):
    """La auditoría durable no se pudo confirmar: el envío no empieza (fail-closed)."""


# ───────────────────────── armado del correo ─────────────────────────

def resolve_links(selection: human_capital_share.ShareSelection, choice: dict | None) -> tuple[list[dict], list[str]]:
    """Por candidato, el CV a compartir (el elegido, o el único si no hay ambigüedad) → [{name, url}] incluidos + [name] omitidos (sin enlace).
    El enlace SIEMPRE sale del modelo (`selection`), nunca de lo que mande el navegador."""
    included: list[dict] = []
    omitted: list[str] = []
    for item in selection.items:
        docs = item.get("docs") or []
        chosen_id = (choice or {}).get(item["id"])
        doc = next((d for d in docs if d["id"] == chosen_id), None) if chosen_id else (docs[0] if len(docs) == 1 else None)
        url = doc.get("url") if doc else None
        if url:
            included.append({"name": item["name"], "url": url})
        else:
            omitted.append(item["name"])
    return included, omitted


def build_graph_message(included: list[dict], recipients: list[dict], subject: str, message: str, closing: str = "") -> dict:
    """Mensaje Graph `sendMail` con la plantilla institucional (identidad Capital Humano): cuerpo del usuario (saludo + texto) → lista ÚNICA de
    candidatos (nombre + «Abrir CV en SharePoint», desde el modelo ya validado: nunca desde lo que escribió el usuario) → cierre del usuario →
    nota de permisos. Valores escapados por Jinja; enlaces ya validados; sin URLs técnicas en el texto visible."""
    html = templates.env.get_template(TEMPLATE).render(
        title=TITLE, brand=dict(notifications.INSTITUTIONAL), vertical=workspaces.brand_identity("human-capital"),
        d={"candidates": included, "message": (message or "").strip()[:MESSAGE_MAX], "closing": (closing or "").strip()[:MESSAGE_MAX],
           "note": SHAREPOINT_NOTE})
    subj = (subject or DEFAULT_SUBJECT).strip()[:SUBJECT_MAX] or DEFAULT_SUBJECT
    return {"message": {"subject": subj, "body": {"contentType": "HTML", "content": html},
                        "toRecipients": [{"emailAddress": {"address": r["email"]}} for r in recipients]}}


# ───────────────────────── envío (token de APP) ─────────────────────────

def send_graph_message(message: dict, *, token_fn=microsoft365_app.acquire_app_token, post=microsoft365.graph_post_accepted) -> tuple[str, str]:
    """Envía por el buzón institucional (client credentials). Devuelve (estado, detalle). 202 = `accepted` (NO prueba entrega); 4xx≠429 = `failed`;
    429/5xx/timeout/red = `uncertain` (no se reenvía automáticamente); interruptor global apagado = `disabled` (Graph NO se llamó)."""
    if not notifications.enabled():
        return "disabled", "envío institucional deshabilitado (NOTIFICATIONS_ENABLED=false)"
    if notifications.missing_settings():
        return "config_incomplete", "configuración de correo incompleta"
    try:
        token = token_fn(config.NOTIFICATIONS_TENANT_ID, config.NOTIFICATIONS_CLIENT_ID, config.NOTIFICATIONS_CLIENT_SECRET)
    except Exception as exc:                                         # noqa: BLE001 — token de app no disponible
        return "uncertain", f"no se obtuvo el token de aplicación ({type(exc).__name__})"
    url = GRAPH_SEND_MAIL.format(sender=quote(config.NOTIFICATIONS_SENDER, safe="@"))
    if not notifications.enabled():                                  # segunda comprobación INMEDIATAMENTE antes de la llamada a Graph
        return "disabled", "envío institucional deshabilitado antes de enviar (NOTIFICATIONS_ENABLED=false)"
    try:
        status = post(url, token, message)
    except HTTPError as exc:
        return ("failed" if 400 <= exc.code < 500 and exc.code != 429 else "uncertain"), f"Graph HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return "uncertain", f"error de red ({type(exc).__name__})"
    return ("accepted" if status == 202 else "uncertain"), f"HTTP {status}"


# ───────────────────────── auditoría durable (fail-closed, sin contenido de CV) ─────────────────────────

def audit_root() -> Path:
    return Path(config.BASE_DIR) / "human-capital" / "cv-shares"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_audit(path: Path, payload: dict, *, create_only: bool = False) -> None:
    if remote_store.read_only_mode():
        raise SendAuditUnavailable("modo de solo lectura")
    if create_only and path.exists():
        raise FileExistsError(str(path))
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    try:
        outcome = remote_store.write_through(path, text)
    except OSError:
        raise SendAuditUnavailable("no se pudo escribir el registro") from None
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise SendAuditUnavailable("registro no confirmado en Spaces")


def write_intent(operation_id: str, *, actor: str, profile_ids: list[str], recipients: list[dict], included: list[dict], omitted: list[str]) -> None:
    """Intención ANTES del primer Graph (fail-closed). Create-only = anti doble-envío (una intención por `operation_id`). Sin contenido de CV: solo ids/emails/nombres."""
    _write_audit(audit_root() / operation_id / "intent.json", {
        "schema": 1, "operation_id": operation_id, "kind": "cv_share", "started_at": _now_iso(), "actor": actor,
        "profile_ids": sorted(profile_ids), "recipients": [r["email"] for r in recipients],
        "shared_count": len(included), "omitted_count": len(omitted),
    }, create_only=True)


def write_result(operation_id: str, *, outcome: str, detail: str, elapsed_ms: int) -> None:
    _write_audit(audit_root() / operation_id / "result.json", {
        "schema": 1, "operation_id": operation_id, "finished_at": _now_iso(), "outcome": outcome, "detail": detail, "elapsed_ms": elapsed_ms,
    })


def read_audit(operation_id: str) -> dict:
    """Lectura para pruebas/soporte."""
    base = audit_root() / operation_id
    def _load(name):
        p = base / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return {"intent": _load("intent.json"), "result": _load("result.json")}


# ───────────────────────── orquestación ─────────────────────────

def send_profiles(*, operation_id: str, actor: str, profile_ids: list[str], recipient_emails: list[str], subject: str, message: str, closing: str = "",
                  choice: dict | None, delegated_token: str, get_profile, list_documents) -> dict:
    """Flujo completo (lo invoca la ruta): resolver candidatos → resolver enlaces → validar destinatarios (directorio) → auditar intención
    (create-only) → armar + enviar → auditar resultado. Devuelve un dict de estado para la UI. Nunca lanza por errores esperados."""
    if not OPERATION_ID_RE.match(operation_id or ""):
        return {"status": "error", "reason": "bad_operation", "message": "La operación venció o es inválida. Reabrí el diálogo e intentá de nuevo."}
    if not notifications.enabled():
        # Interruptor GLOBAL del envío institucional: rechazo explícito ANTES de validar, auditar o tocar Graph (sin intención nueva).
        return {"status": "disabled", "reason": "disabled", "message": DISABLED_MESSAGE}
    selection = human_capital_share.resolve(profile_ids, get_profile, list_documents)
    if not selection.ok:
        return {"status": "error", "reason": selection.reason, "message": selection.message}
    included, omitted = resolve_links(selection, choice)
    if not included:
        return {"status": "error", "reason": "no_links", "message": "Ninguno de los candidatos seleccionados tiene un CV con enlace disponible para compartir."}
    recipients, invalid = validate_recipients(recipient_emails, delegated_token, context=CONTEXT)
    if not recipients:
        return {"status": "error", "reason": "no_recipients", "message": "No hay destinatarios corporativos válidos seleccionados.",
                "invalid": invalid}
    if len(recipients) > MAX_RECIPIENTS:
        return {"status": "error", "reason": "too_many_recipients", "message": f"Se pueden enviar hasta {MAX_RECIPIENTS} destinatarios por vez."}
    if notifications.missing_settings():
        # Precondición de configuración (no es un intento de envío): se corta ANTES de escribir la intención, para no dejar un registro huérfano
        # y para que el mismo `operation_id` sirva cuando se reintente ya configurado.
        return {"status": "config_incomplete", "reason": "config_incomplete", "message": "El correo institucional no está configurado; no se envió nada."}
    started = time.monotonic()
    try:
        write_intent(operation_id, actor=actor, profile_ids=[i["id"] for i in selection.items], recipients=recipients, included=included, omitted=omitted)
    except FileExistsError:
        prior = read_audit(operation_id).get("result") or {}
        return {"status": "duplicate", "message": "Este envío ya se había registrado.", "prior_outcome": prior.get("outcome")}
    except SendAuditUnavailable as exc:
        return {"status": "error", "reason": "audit_unavailable", "message": "No se pudo registrar la operación de forma durable; no se envió nada."}
    state, detail = send_graph_message(build_graph_message(included, recipients, subject, message, closing))
    elapsed = int((time.monotonic() - started) * 1000)
    try:
        write_result(operation_id, outcome=state, detail=detail, elapsed_ms=elapsed)
    except SendAuditUnavailable:
        _logger.warning("operation=cv_share result_not_persisted operation=%s outcome=%s", operation_id, state)
    return {"status": state, "recipients": [r["email"] for r in recipients], "invalid": invalid,
            "shared": [c["name"] for c in included], "omitted": omitted, "detail": detail}
