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
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SEARCH_SAFE = re.compile(r'[\\"]')                                  # se quitan comillas/backslash del término $search
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

PROVIDER_DIRECTORY = "directory"
PROVIDER_ALLOWLIST = "allowlist"


def recipient_provider() -> str:
    """Proveedor de destinatarios EXPLÍCITO (`HC_SHARE_RECIPIENTS_PROVIDER`). Cualquier valor distinto de "allowlist" es el directorio: el
    catálogo local nunca se activa por un error de Graph ni por omisión."""
    value = str(getattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", "") or "").strip().lower()
    return PROVIDER_ALLOWLIST if value == PROVIDER_ALLOWLIST else PROVIDER_DIRECTORY


def allowlist_recipients() -> list[dict]:
    """Catálogo gobernado TRANSITORIO: los usuarios de `HUMAN_CAPITAL_ALLOWED_USERS` (mismo parser/normalización que la admisión a Capital
    Humano). Solo direcciones válidas del dominio corporativo (`AUTH_DEFAULT_DOMAIN`): nada externo. Sin nombres gobernados en el Hub → el
    nombre visible es el correo."""
    from webapp.services import auth_service
    domain = str(getattr(config, "AUTH_DEFAULT_DOMAIN", "") or "danwor.com").strip().lower().lstrip("@")
    # «ALL» es un valor especial del parser compartido (solo se rechaza cuando es la lista entera): mezclado en la lista se normalizaría a
    # `all@<dominio>`, que nunca es un destinatario legítimo → se excluye.
    emails = sorted(e for e in auth_service._human_capital_set()
                    if _EMAIL_RE.match(e) and e.rsplit("@", 1)[1] == domain and e.rsplit("@", 1)[0] != "all")
    return [{"name": e, "email": e} for e in emails]


def roster_names(token: str) -> dict[str, str]:
    """correo (minúsculas) → nombre del padrón de personal: lista Colaboradores + User Information List del sitio Gestión, leídas con el token
    DELEGADO base de lectura (`Sites.Read.All`, ya concedido). Reutiliza la resolución existente y validada en vivo de
    `ReportingRepository.get_colaboradores_email_map` (la capa de REPOSITORIO, no el servicio funcional de Reporting) sin duplicarla.
    Solo presentación: ante cualquier falla devuelve {} y el selector usa el correo. No persiste nada."""
    if not token:
        return {}
    try:
        from webapp.services.reporting_repository import ReportingRepository
        return dict(ReportingRepository().get_colaboradores_email_map(token) or {})
    except Exception as exc:                                         # noqa: BLE001 — presentación accesoria: nunca bloquea la selección
        _logger.info("operation=cv_share roster_names_unavailable error=%s", type(exc).__name__)
        return {}


def allowlist_catalog(token: str, *, names=roster_names) -> dict:
    """Catálogo del proveedor «allowlist» para UNA apertura del selector: SOLO las identidades de `HUMAN_CAPITAL_ALLOWED_USERS` (el padrón
    nunca agrega destinatarios), con su nombre del padrón cuando existe; si no, el correo. Se pide una vez por apertura y se filtra en memoria."""
    rows = allowlist_recipients()
    roster = names(token) if rows else {}
    out = [{"name": (roster.get(r["email"]) or "").strip() or r["email"], "email": r["email"]} for r in rows]
    return {"recipients": out, "names_resolved": sum(1 for r in out if r["name"] != r["email"])}


def recipients_enabled() -> bool:
    """¿Hay un proveedor de destinatarios utilizable? Directorio: scope delegado configurado. Catálogo: al menos una dirección corporativa."""
    if recipient_provider() == PROVIDER_ALLOWLIST:
        return bool(allowlist_recipients())
    return bool(directory_scope())


def needs_delegated_token() -> bool:
    """Solo el directorio consulta Microsoft con la identidad delegada; el catálogo gobernado se resuelve en el servidor."""
    return recipient_provider() == PROVIDER_DIRECTORY


def search_recipients(query: str, token: str) -> list[dict]:
    """Búsqueda del selector según el proveedor explícito. Misma forma de resultado ([{name, email}]) y mismo mínimo de caracteres."""
    if recipient_provider() == PROVIDER_ALLOWLIST:
        q = (query or "").strip().lower()
        if len(q) < MIN_QUERY:
            return []
        return [r for r in allowlist_recipients() if q in r["email"]][:MAX_RESULTS]
    return search_directory(query, token)


def directory_scope() -> str:
    """Scope delegado para buscar el directorio (`/users?$search`), leído en cada llamada. No es secreto; se expone vía la config de auth para
    que el frontend lo pida al token delegado. Vacío = búsqueda DESHABILITADA (prerequisito `User.ReadBasic.All` sin conceder)."""
    return str(getattr(config, "HC_DIRECTORY_SEARCH_SCOPE", "") or "").strip()


class ProfileSendError(RuntimeError):
    """Base (fail-closed)."""


class DirectorySearchUnavailable(ProfileSendError):
    """El directorio no se pudo consultar por permisos (403/401): prerequisito `User.ReadBasic.All` sin conceder, o token sin ese scope."""


class DirectoryError(ProfileSendError):
    """Error transitorio/indeterminado consultando el directorio."""


class SendAuditUnavailable(ProfileSendError):
    """La auditoría durable no se pudo confirmar: el envío no empieza (fail-closed)."""


# ───────────────────────── directorio (token DELEGADO) ─────────────────────────

def _dedup(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        key = r["email"].lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _row(u: dict) -> dict | None:
    email = str(u.get("mail") or u.get("userPrincipalName") or "").strip()
    if not email or not _EMAIL_RE.match(email):
        return None
    return {"name": str(u.get("displayName") or email).strip(), "email": email}


def search_directory(query: str, token: str, *, fetch=microsoft365.graph_get_json) -> list[dict]:
    """Usuarios corporativos que coinciden con `query` (nombre o correo), vía Graph `/users?$search` con el token DELEGADO. ≥3 caracteres.
    `DirectorySearchUnavailable` si falta permiso (403/401); `DirectoryError` ante fallo transitorio. Nunca lanza por query corta (devuelve [])."""
    if not directory_scope():
        raise DirectorySearchUnavailable("búsqueda de directorio deshabilitada: falta conceder el permiso delegado (HC_DIRECTORY_SEARCH_SCOPE vacío)")
    q = _SEARCH_SAFE.sub("", (query or "").strip())
    if len(q) < MIN_QUERY:
        return []
    url = GRAPH_USERS_SEARCH.format(q=quote(q), top=MAX_RESULTS)
    try:
        data = fetch(url, token, extra_headers={"ConsistencyLevel": "eventual"})
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise DirectorySearchUnavailable(f"sin permiso para consultar el directorio (HTTP {exc.code})") from exc
        raise DirectoryError(f"error consultando el directorio (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise DirectoryError(f"no se pudo consultar el directorio ({type(exc).__name__})") from exc
    rows = [r for r in (_row(u) for u in (data.get("value") or [])) if r is not None]
    return _dedup(rows)[:MAX_RESULTS]


def validate_recipients(emails: list[str], token: str, *, fetch=microsoft365.graph_get_json) -> tuple[list[dict], list[str]]:
    """Valida CADA destinatario contra el proveedor explícito (el backend no confía en el autocomplete). Directorio: resuelve `/users/{email}`
    con el token DELEGADO; un 403/401 → `DirectorySearchUnavailable` (no se puede validar ⇒ no se envía). Catálogo: la dirección tiene que estar
    en el catálogo gobernado. Devuelve (válidos [{name,email}], inválidos [email])."""
    if recipient_provider() == PROVIDER_ALLOWLIST:
        return _validate_allowlist(emails)
    valid: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in emails or []:
        email = str(raw or "").strip()
        key = email.lower()
        if not email or not _EMAIL_RE.match(email) or key in seen:
            if email and key not in seen:
                invalid.append(email)
            seen.add(key)
            continue
        seen.add(key)
        try:
            u = fetch(GRAPH_USER_GET.format(id=quote(email, safe="@")), token)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise DirectorySearchUnavailable(f"sin permiso para validar destinatarios (HTTP {exc.code})") from exc
            invalid.append(email)                                    # 404/400/… = no es una identidad válida
            continue
        except (URLError, TimeoutError, OSError, ValueError):
            invalid.append(email)
            continue
        row = _row(u)
        valid.append(row if row is not None else {"name": email, "email": email})
    return _dedup(valid), invalid


def _validate_allowlist(emails: list[str]) -> tuple[list[dict], list[str]]:
    catalog = {r["email"]: r for r in allowlist_recipients()}
    valid: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in emails or []:
        email = str(raw or "").strip()
        key = email.lower()
        if not email or key in seen:
            continue
        seen.add(key)
        if key in catalog:
            valid.append(catalog[key])
        else:
            invalid.append(email)
    return valid, invalid


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


def build_graph_message(included: list[dict], recipients: list[dict], subject: str, message: str) -> dict:
    """Mensaje Graph `sendMail` con la plantilla institucional (identidad Capital Humano). Valores escapados por Jinja; enlaces ya validados."""
    html = templates.env.get_template(TEMPLATE).render(
        title=TITLE, brand=dict(notifications.INSTITUTIONAL), vertical=workspaces.brand_identity("human-capital"),
        d={"candidates": included, "message": (message or "").strip(), "note": SHAREPOINT_NOTE})
    subj = (subject or DEFAULT_SUBJECT).strip()[:SUBJECT_MAX] or DEFAULT_SUBJECT
    return {"message": {"subject": subj, "body": {"contentType": "HTML", "content": html},
                        "toRecipients": [{"emailAddress": {"address": r["email"]}} for r in recipients]}}


# ───────────────────────── envío (token de APP) ─────────────────────────

def send_graph_message(message: dict, *, token_fn=microsoft365_app.acquire_app_token, post=microsoft365.graph_post_accepted) -> tuple[str, str]:
    """Envía por el buzón institucional (client credentials). Devuelve (estado, detalle). 202 = `accepted` (NO prueba entrega); 4xx≠429 = `failed`;
    429/5xx/timeout/red = `uncertain` (no se reenvía automáticamente)."""
    if notifications.missing_settings():
        return "config_incomplete", "configuración de correo incompleta"
    try:
        token = token_fn(config.NOTIFICATIONS_TENANT_ID, config.NOTIFICATIONS_CLIENT_ID, config.NOTIFICATIONS_CLIENT_SECRET)
    except Exception as exc:                                         # noqa: BLE001 — token de app no disponible
        return "uncertain", f"no se obtuvo el token de aplicación ({type(exc).__name__})"
    url = GRAPH_SEND_MAIL.format(sender=quote(config.NOTIFICATIONS_SENDER, safe="@"))
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

def send_profiles(*, operation_id: str, actor: str, profile_ids: list[str], recipient_emails: list[str], subject: str, message: str,
                  choice: dict | None, delegated_token: str, get_profile, list_documents) -> dict:
    """Flujo completo (lo invoca la ruta): resolver candidatos → resolver enlaces → validar destinatarios (directorio) → auditar intención
    (create-only) → armar + enviar → auditar resultado. Devuelve un dict de estado para la UI. Nunca lanza por errores esperados."""
    if not OPERATION_ID_RE.match(operation_id or ""):
        return {"status": "error", "reason": "bad_operation", "message": "La operación venció o es inválida. Reabrí el diálogo e intentá de nuevo."}
    selection = human_capital_share.resolve(profile_ids, get_profile, list_documents)
    if not selection.ok:
        return {"status": "error", "reason": selection.reason, "message": selection.message}
    included, omitted = resolve_links(selection, choice)
    if not included:
        return {"status": "error", "reason": "no_links", "message": "Ninguno de los candidatos seleccionados tiene un CV con enlace disponible para compartir."}
    recipients, invalid = validate_recipients(recipient_emails, delegated_token)
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
    state, detail = send_graph_message(build_graph_message(included, recipients, subject, message))
    elapsed = int((time.monotonic() - started) * 1000)
    try:
        write_result(operation_id, outcome=state, detail=detail, elapsed_ms=elapsed)
    except SendAuditUnavailable:
        _logger.warning("operation=cv_share result_not_persisted operation=%s outcome=%s", operation_id, state)
    return {"status": state, "recipients": [r["email"] for r in recipients], "invalid": invalid,
            "shared": [c["name"] for c in included], "omitted": omitted, "detail": detail}
