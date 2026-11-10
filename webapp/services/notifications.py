"""
notifications.py — notificaciones automáticas por correo del Hub (capacidad transversal mínima).

Flujo:  evento de negocio CONFIRMADO → política (registro `EVENTS`) → destinatarios (configuración) → plantilla → Microsoft Graph `sendMail`
        (identidad de APLICACIÓN) → registro durable del resultado.

Quien produce el evento (p. ej. Operaciones → 03. Solicitudes) solo llama a `emit(evento, clave, datos)` con datos de negocio ya confirmados.
No conoce OAuth, Graph, endpoints, secretos, códigos HTTP ni reintentos: todo eso vive acá y en `core/microsoft365_app.py` (token de aplicación,
separado de los tokens MSAL delegados de los usuarios) y `core/microsoft365.py` (transporte Graph compartido).

Confiabilidad (distinguimos 4 cosas: entidad confirmada · intención registrada · envío ACEPTADO por Graph (202) · entrega efectiva):
  · La intención se registra DURABLE antes de enviar: un archivo por (evento, clave) bajo `NOTIFICATIONS_DIR`, escrito con
    `remote_store.write_through` (atómico local + Spaces en producción). La creación EXCLUSIVA del archivo es la deduplicación: la misma clave
    (p. ej. el id de SharePoint de la solicitud) nunca produce una segunda intención, aunque el evento se reprocese o el proceso se reinicie.
  · El envío corre en un hilo aparte: nunca bloquea ni revierte el workflow que originó el evento.
  · Estados: pending → sending → accepted | retry | failed | uncertain. `accepted` = Graph respondió 202 (no prueba la entrega final).
    `retry` (429/5xx de capacidad, red antes de enviar, token no disponible) se reintenta: unas pocas veces en el momento y luego en el
    siguiente evento o al arrancar (`resume_pending`). `failed` (configuración, 4xx definitivos) no se reintenta.
    `uncertain` (timeout después de enviar, 500, o un proceso que murió en `sending`) NO se reenvía automáticamente: Graph no ofrece
    `sendMail` exactamente-una-vez, y ante la duda preferimos no duplicar (garantía del MVP: como mucho una aceptación por clave salvo
    reenvío manual; ninguna promesa de entrega).
  · Precondición vigente del Hub: un solo proceso/réplica (el lock de hilos es por proceso, igual que los demás registros).

Agregar un evento nuevo (p. ej. `operations.architecture_request.closed`): una entrada en `EVENTS` (destinatarios, asunto, plantilla) + su
plantilla + un `emit(...)` en el punto de negocio donde se CONFIRMA el cierre. El token, Graph, el registro y los reintentos no cambian.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import threading
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from html.parser import HTMLParser
from urllib.parse import quote

import config
from core import microsoft365, microsoft365_app, remote_store

log = logging.getLogger("danwor.notifications")

GRAPH_SEND_MAIL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
INLINE_ATTEMPTS = 3                     # intentos en el momento (con espera corta entre ellos)
MAX_ATTEMPTS = 6                        # tope total, contando los reanudados en eventos/arranques posteriores
BACKOFF_S = (2.0, 6.0)
MAX_RETRY_AFTER_S = 30.0
TRANSIENT_HTTP = frozenset({429, 502, 503, 504})
UNCERTAIN_HTTP = frozenset({500})
RESUMABLE = frozenset({"pending", "retry"})
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_lock = threading.Lock()                # serializa las transiciones de estado de los registros (un proceso)

# Identidad INSTITUCIONAL (común a todas las verticales): la marca «Danwor Hub» del lockup canónico del Hub (`hub_brand_lockup()` /
# `hub_icon()` en partials/platform_topbar.html: ícono de 4 cuadrados + «Danwor Hub»; en el correo el ícono se dibuja con una tabla 2×2, porque
# los clientes de correo no muestran SVG en línea), negro, tinta y acento institucionales (tokens del Hub: `--content-accent` en content.css /
# `--gold` e `--ink` en brand-hero.css). La identidad de cada VERTICAL (título/acento) no vive acá: sale de `webapp.workspaces.brand_identity`.
INSTITUTIONAL = {
    "hub_name": "Danwor Hub",
    "accent": "#e7b008",
    "ink": "#e4e4ec",                    # texto principal sobre negro (`--ink` de brand-hero.css: la 1ª línea del título de las tarjetas del Hub)
    "dark": "#0c0c0d",
    "muted": "#8b8b96",                  # `--ink-soft` de brand-hero.css (separador «·»)
    # Pila tipográfica de la cabecera: Inter (la del Hub) donde el cliente cargue la hoja de fuentes del Hub (`hub_fonts_url`, en <head> como
    # mejora progresiva: Apple Mail, iOS, Outlook para Mac…). Outlook de Windows no carga fuentes web: cae en «Segoe UI Black», el peso de
    # sistema más cercano a Inter 800 (Segoe UI Bold es 700 y más angosta); Apple usa SF con su peso 800. Nada se incrusta.
    "font": "Inter,'Segoe UI Black','Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif",
}


@dataclass(frozen=True)
class EventSpec:
    """Política de un evento: vertical que lo origina (clave de `webapp.workspaces`), a quién (setting de destinatarios), qué asunto y qué
    plantilla. Inmutable. La plantilla del evento extiende la base compartida (`notifications/_layout.html`)."""
    workspace: str
    recipients_setting: str
    subject: Callable[[dict], str]
    template: str
    title: str


EVENTS: dict[str, EventSpec] = {
    "operations.architecture_request.created": EventSpec(
        workspace="operations",
        recipients_setting="NOTIFICATIONS_OPERATIONS_RECIPIENTS",
        subject=lambda d: f"Nueva solicitud de arquitectura | Operaciones | Solicitud #{d.get('id', '')}",
        template="notifications/architecture_request_created.html",
        title="Nueva solicitud de arquitectura",
    ),
}


# ── configuración ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def enabled() -> bool:
    return bool(getattr(config, "NOTIFICATIONS_ENABLED", False))


def missing_settings() -> list[str]:
    """Nombres (nunca valores) de la configuración obligatoria que falta o es inválida con las notificaciones encendidas."""
    missing = [name for name in ("NOTIFICATIONS_TENANT_ID", "NOTIFICATIONS_CLIENT_ID", "NOTIFICATIONS_CLIENT_SECRET", "NOTIFICATIONS_SENDER")
               if not str(getattr(config, name, "") or "").strip()]
    sender = str(getattr(config, "NOTIFICATIONS_SENDER", "") or "")
    if sender and not _EMAIL_RE.match(sender):
        missing.append("NOTIFICATIONS_SENDER (formato)")
    return missing


def recipients_for(spec: EventSpec) -> list[str]:
    """Destinatarios gobernados del evento: SOLO la variable de configuración declarada en su política (deduplicados, formato válido)."""
    raw = str(getattr(config, spec.recipients_setting, "") or "")
    out: list[str] = []
    for part in re.split(r"[;,]", raw):
        address = part.strip()
        if address and _EMAIL_RE.match(address) and address.lower() not in {a.lower() for a in out}:
            out.append(address)
    return out


def startup_check() -> None:
    """Al arrancar: con las notificaciones encendidas y configuración incompleta, un error CLARO en el log (solo nombres de variables).
    Nunca impide que el Hub arranque ni expone valores."""
    if not enabled():
        log.info("notificaciones por correo: deshabilitadas (NOTIFICATIONS_ENABLED=false)")
        return
    missing = missing_settings()
    if missing:
        log.error("notificaciones por correo: HABILITADAS pero con configuración incompleta (%s): no se enviará ningún correo", ", ".join(missing))
    else:
        log.info("notificaciones por correo: habilitadas")


# ── registro durable ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _dir() -> Path:
    return Path(getattr(config, "NOTIFICATIONS_DIR"))


def record_path(event: str, key: str) -> Path:
    digest = hashlib.sha256(f"{event}\n{key}".encode("utf-8")).hexdigest()[:32]
    return _dir() / event.replace(".", "_") / f"{digest}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, record: dict) -> None:
    remote_store.write_through(path, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _create_exclusive(path: Path, record: dict) -> bool:
    """Crea el registro SOLO si no existe (la deduplicación por clave). Devuelve False si ya había una intención para ese evento y clave."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    except FileExistsError:
        return False
    _write(path, record)                                         # misma escritura atómica + persistencia explícita (Spaces en producción)
    return True


# ── API para los productores de eventos ──────────────────────────────────────────────────────────────────────────────────────────────────────
def emit(event: str, key: str, data: dict, *, origin: str | None = None) -> str:
    """Registra la intención de notificar un evento de negocio YA CONFIRMADO y dispara el envío en segundo plano.
    Devuelve: "disabled" | "duplicate" | "config_incomplete" | "no_recipients" | "queued". Nunca lanza: un fallo de la notificación
    jamás afecta al workflow que la originó."""
    try:
        return _emit(event, str(key), dict(data), origin)
    except Exception as exc:                                     # noqa: BLE001 — la notificación es accesoria al evento de negocio
        log.warning("notificación %s no registrada por un error interno (%s)", event, type(exc).__name__)
        return "error"


def _emit(event: str, key: str, data: dict, origin: str | None = None) -> str:
    spec = EVENTS.get(event)
    if spec is None:
        raise ValueError(f"evento no registrado: {event}")
    if not enabled():
        return "disabled"
    path = record_path(event, key)
    record = {"event": event, "key": key, "data": data, "hub_base": hub_base(origin), "state": "pending", "attempts": 0,
              "created_at": _now(), "updated_at": _now()}
    missing = missing_settings()
    recipients = recipients_for(spec)
    if missing or not recipients:
        record.update(state="failed", error="config_incomplete" if missing else "no_recipients")
        if _create_exclusive(path, record):
            log.error("notificación %s clave=%s no enviada: %s", event, _short(key),
                      ("configuración incompleta (" + ", ".join(missing) + ")") if missing else "sin destinatarios configurados")
        return "config_incomplete" if missing else "no_recipients"
    if not _create_exclusive(path, record):
        log.info("notificación %s clave=%s ya registrada: no se duplica", event, _short(key))
        return "duplicate"
    log.info("notificación %s clave=%s registrada (pendiente de envío)", event, _short(key))
    threading.Thread(target=_deliver_safely, args=(path,), name="hub-notification", daemon=True).start()
    threading.Thread(target=_resume_safely, kwargs={"exclude": path}, name="hub-notification-resume", daemon=True).start()
    return "queued"


def resume_pending() -> int:
    """Reanuda las intenciones que quedaron `pending`/`retry` (proceso reiniciado o fallas transitorias previas) y marca como `uncertain` las
    que un proceso anterior dejó en `sending` (pudo haberse enviado: no se reenvían). Devuelve cuántas se reintentaron."""
    return _resume(None)


def _resume_safely(exclude: Path | None = None) -> None:
    try:
        _resume(exclude)
    except Exception as exc:                                     # noqa: BLE001
        log.warning("reanudación de notificaciones falló (%s)", type(exc).__name__)


def _resume(exclude: Path | None) -> int:
    if not enabled() or not _dir().is_dir():
        return 0
    retried = 0
    for path in sorted(_dir().glob("*/*.json")):
        if exclude is not None and path == exclude:
            continue
        record = _read(path)
        if not record:
            continue
        if record.get("state") == "sending" and not _inflight(path):
            with _lock:
                record = _read(path) or record
                if record.get("state") == "sending":
                    record.update(state="uncertain", error="interrupted_while_sending", updated_at=_now())
                    _write(path, record)
                    log.warning("notificación %s clave=%s quedó en envío al reiniciar: resultado incierto, no se reenvía", record.get("event"), _short(record.get("key", "")))
            continue
        if record.get("state") in RESUMABLE and int(record.get("attempts", 0)) < MAX_ATTEMPTS:
            _deliver(path)
            retried += 1
    return retried


# ── envío ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
_inflight_paths: set[str] = set()


def _inflight(path: Path) -> bool:
    with _lock:
        return str(path) in _inflight_paths


def _deliver_safely(path: Path) -> None:
    try:
        _deliver(path)
    except Exception as exc:                                     # noqa: BLE001 — nunca propaga al hilo/proceso
        log.warning("envío de notificación falló por un error interno (%s)", type(exc).__name__)


def _claim(path: Path) -> dict | None:
    with _lock:
        if str(path) in _inflight_paths:
            return None
        record = _read(path)
        if not record or record.get("state") not in RESUMABLE:
            return None
        record.update(state="sending", updated_at=_now())
        _write(path, record)
        _inflight_paths.add(str(path))
        return record


def _finish(path: Path, record: dict, **changes) -> None:
    with _lock:
        record.update(updated_at=_now(), **changes)
        _write(path, record)
        _inflight_paths.discard(str(path))


def _deliver(path: Path) -> None:
    record = _claim(path)
    if record is None:
        return
    spec = EVENTS.get(record.get("event", ""))
    if spec is None:
        _finish(path, record, state="failed", error="unknown_event")
        return
    recipients = recipients_for(spec)
    missing = missing_settings()
    if missing or not recipients:
        _finish(path, record, state="failed", error="config_incomplete" if missing else "no_recipients")
        log.error("notificación %s clave=%s no enviada: %s", record["event"], _short(record["key"]),
                  "configuración incompleta" if missing else "sin destinatarios configurados")
        return
    message = build_message(record["event"], record.get("data") or {}, recipients, hub_base=record.get("hub_base") or "")
    attempts = int(record.get("attempts", 0))
    for inline in range(INLINE_ATTEMPTS):
        attempts += 1
        outcome, detail, retry_after = _send_once(message)
        if outcome == "accepted":
            _finish(path, record, state="accepted", attempts=attempts, error=None, accepted_at=_now())
            log.info("notificación %s clave=%s aceptada por Graph (HTTP 202) para %d destinatario(s)", record["event"], _short(record["key"]), len(recipients))
            return
        if outcome != "transient" or attempts >= MAX_ATTEMPTS:
            state = outcome if outcome != "transient" else "failed"             # failed/uncertain son terminales; un transitorio agotado también
            if outcome == "transient":
                detail = f"{detail}; reintentos agotados"
            _finish(path, record, state=state, attempts=attempts, error=detail)
            log.warning("notificación %s clave=%s %s (%s)", record["event"], _short(record["key"]),
                        "con resultado incierto: no se reenvía" if state == "uncertain" else "no enviada", detail)
            return
        if inline < INLINE_ATTEMPTS - 1:
            time.sleep(min(retry_after if retry_after is not None else BACKOFF_S[min(inline, len(BACKOFF_S) - 1)], MAX_RETRY_AFTER_S))
    _finish(path, record, state="retry", attempts=attempts, error=detail)
    log.warning("notificación %s clave=%s pendiente de reintento (%s)", record["event"], _short(record["key"]), detail)


def _send_once(message: dict) -> tuple[str, str, float | None]:
    """Un intento de envío. Devuelve (accepted|transient|failed|uncertain, detalle sin secretos, Retry-After en segundos o None)."""
    try:
        token = microsoft365_app.acquire_app_token(config.NOTIFICATIONS_TENANT_ID, config.NOTIFICATIONS_CLIENT_ID, config.NOTIFICATIONS_CLIENT_SECRET)
    except microsoft365_app.AppTokenError as exc:
        return ("transient" if exc.transient else "failed"), f"token: {exc}", None
    url = GRAPH_SEND_MAIL.format(sender=quote(config.NOTIFICATIONS_SENDER, safe="@"))
    try:
        status = microsoft365.graph_post_accepted(url, token, message)
    except urllib.error.HTTPError as exc:
        retry_after = _retry_after(exc)
        if exc.code in TRANSIENT_HTTP:
            return "transient", f"Graph HTTP {exc.code}", retry_after
        if exc.code in UNCERTAIN_HTTP:
            return "uncertain", f"Graph HTTP {exc.code}", None
        return "failed", f"Graph HTTP {exc.code}", None
    except (socket.timeout, TimeoutError):
        return "uncertain", "Graph sin respuesta (timeout)", None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
            return "uncertain", "Graph sin respuesta (timeout)", None
        return "transient", f"sin conexión con Graph ({type(getattr(exc, 'reason', exc)).__name__})", None
    finally:
        token = ""                                               # el token no sobrevive al intento
    if status == 202:
        return "accepted", "HTTP 202", None
    return "uncertain", f"Graph HTTP {status}", None


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    try:
        value = exc.headers.get("Retry-After") if exc.headers else None
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _short(key: str) -> str:
    return str(key)[:40]


# ── plantilla ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def build_message(event: str, data: dict, recipients: list[str], *, hub_base: str = "") -> dict:
    """Mensaje Graph (`sendMail`): asunto y cuerpo HTML generados SOLO desde la política y la plantilla del evento; todos los valores dinámicos
    se escapan (la plantilla no acepta HTML del usuario). El remitente lo fija la URL (buzón de configuración), nunca el mensaje.
    Capas: identidad institucional (`INSTITUTIONAL`) → identidad de la vertical (`workspaces.brand_identity(spec.workspace)`) → plantilla del
    evento (extiende `_layout.html`) → datos de negocio (`d`)."""
    from webapp import workspaces                             # imports diferidos: el servicio no depende de la app web para importarse
    from webapp.templating import templates
    spec = EVENTS[event]
    brand = dict(INSTITUTIONAL)
    html = templates.env.get_template(spec.template).render(title=spec.title, d=data, brand=brand,
                                                             vertical=workspaces.brand_identity(spec.workspace))
    return {"message": {"subject": spec.subject(data)[:255],
                        "body": {"contentType": "HTML", "content": html},
                        "toRecipients": [{"emailAddress": {"address": address}} for address in recipients]}}


def hub_base(origin: str | None = None) -> str:
    """Base absoluta del Hub para los correos: `NOTIFICATIONS_HUB_BASE_URL` si está configurada; si no, el origen ya validado del request que
    originó el evento. Vacía si ninguna es una URL http(s) (el enlace del correo queda entonces relativo)."""
    base = str(getattr(config, "NOTIFICATIONS_HUB_BASE_URL", "") or "").rstrip("/") or (origin or "").rstrip("/")
    return base if base.startswith(("https://", "http://")) else ""


def hub_link(path: str, origin: str | None = None) -> str:
    """URL absoluta a una ruta EXISTENTE del Hub (ver `hub_base`). Sin tokens ni accesos privilegiados: el destinatario se autentica como siempre."""
    return f"{hub_base(origin)}{path}"


class _ReadableText(HTMLParser):
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK and tag != "li":                    # viñetas consecutivas sin línea en blanco entre ellas
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


_TAG_LIKE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(\s[^<>]*)?/?>")


def readable_text(value: str | None, limit: int | None = None) -> str:
    """Texto LEGIBLE para un correo desde un campo que puede traer HTML enriquecido: conserva párrafos, saltos de línea y viñetas, decodifica
    entidades y descarta las etiquetas (también las que llegaron escapadas como texto). El resultado es texto plano: la plantilla lo ESCAPA al
    renderizar (nunca se inserta HTML)."""
    text = str(value or "")
    for _ in range(2):                                           # 2ª pasada: etiquetas que el campo guardó escapadas (&lt;b&gt;) y quedaron como texto
        parser = _ReadableText()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
        if not _TAG_LIKE.search(text):
            break
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    text = "\n".join(out).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text
