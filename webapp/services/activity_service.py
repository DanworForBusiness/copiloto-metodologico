"""
activity_service.py
===================
Logging liviano de adopción inicial (port del core/activity.py legacy, commit
861560c, sin Streamlit). Una línea JSON por evento, particionado por día, en
ACTIVITY_LOG_DIR. Controlado por LOG_ACTIVITY (OFF por defecto; ON en DO).

No reemplaza auditoría formal ni analytics persistente: sirve para observar
adopción temprana (logins, módulos visitados) mientras el flag esté habilitado.
Nunca bloquea la experiencia por un problema de logging.

Diferencia con el legacy: se retiró el filtro por ALLOWED_USERS — hoy el acceso
ya está restringido por la autorización (ALLOWED_USERS/ACADEMY_USERS), así que
todo usuario con sesión es un usuario autorizado y su actividad interesa
(especialmente el perfil Academy durante el Desafío).

ENVELOPE v2 (Métricas de Uso v2 — fase 1). Cada línea es:

    {v:2, id, ts_utc, kind, event, user, vertical, scope, area, module?,
     section?, surface?, attrs}

- `id` (uuid4 hex) identifica la línea; `kind`/`surface` salen del catálogo
  central (`activity_catalog`).
- `attrs` reemplaza a `payload`: SOLO las claves de la whitelist del evento
  (todo lo demás se descarta ANTES de escribir) y con los valores saneados
  (claves con forma de credencial fuera, valores con marcadores de
  credencial redactados, URLs sin query string).
- `name` ya no se escribe en cada línea: el display name viaja una sola vez,
  en el `login` (`attrs.display_name`), y el lector lo resuelve por usuario.
- Un evento NO catalogado se escribe igual (no se pierde que pasó), pero con
  `attrs={}`, y se avisa UNA vez por proceso en el logger del servidor.

Los registros v1 (sin `v`) quedan como están: la compatibilidad es de
LECTURA (`activity_context.normalize_record`), nunca se reescribe el JSONL.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from config import ACTIVITY_LOG_DIR, LOG_ACTIVITY
from webapp.services import activity_catalog

log = logging.getLogger(__name__)

# Niveles de logging. OFF=0 (nada), BASIC=1 (adopción: login/módulos/…),
# VERBOSE=2 (BASIC + interacción fina: preguntas al asistente, pasos de árbol,
# navegación de catálogo, errores). ON/TRUE/1 = BASIC por compatibilidad.
_LEVELS = {
    "": 0, "OFF": 0, "NO": 0, "FALSE": 0, "0": 0,
    "BASIC": 1, "ON": 1, "1": 1, "TRUE": 1, "YES": 1, "SI": 1, "SÍ": 1,
    "VERBOSE": 2, "FULL": 2, "2": 2,
}
_ENABLED_VALUES = {"1", "ON", "TRUE", "YES", "SI", "SÍ", "BASIC", "VERBOSE", "FULL", "2"}
_MAX_STRING_LEN = 4000
_MAX_LIST_LEN = 50

# Nombres de parámetro de `log_activity`/`log_verbose` + campos del envelope:
# un caller que reenvía un body de CLIENTE (beacons de telemetry/reporting)
# tiene que sacarlos ANTES de expandir `**payload`. Si no, un beacon con
# `{"user": ...}` o `{"module": ...}` choca con el keyword del mismo nombre y
# Python levanta TypeError en el call site (antes de entrar al sink, así que
# el try/except de acá no lo puede atajar) → 500 en el endpoint.
# (`kind`/`name` NO están: no chocan con ningún parámetro y `kind` es un
# atributo legítimo de algunos beacons, p. ej. disclosure_toggle.)
RESERVED_KEYS = frozenset({"event", "event_type", "user", "scope", "area", "module", "module_id", "section"})

# Saneamiento por VALOR (además de la whitelist por clave del catálogo). MISMA
# idea que `connectivity_catalog_update._strip`/`unsafe_properties`: una clave
# con forma de credencial se elimina; un valor de texto con un marcador de
# credencial se redacta. La whitelist ya hace casi todo el trabajo — esto es
# la segunda red por si un atributo permitido (p. ej. `href`, `error`) trae
# algo que no debería.
_SECRET_KEY = re.compile(r"(password|passwd|secret|token|credential|assertion|user(name|id)$)", re.I)
_SECRET_VALUE = re.compile(
    r"(password|passwd|secret|bearer\s+\S|access_token|refresh_token|id_token|client_secret|api[_-]?key"
    r"|assertion|[?&](sig|token|code|key)=|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})",
    re.I,
)
_URL = re.compile(r"^[A-Za-z][\w+.-]*://\S+$")
_REDACTED = "[redacted]"

# Eventos no catalogados ya avisados (dedupe del warning por proceso; no es un
# caché de datos, solo evita inundar el log del servidor).
_warned_unknown: set[str] = set()


def _level() -> int:
    return _LEVELS.get((LOG_ACTIVITY or "").strip().upper(), 0)


def is_activity_logging_enabled() -> bool:
    """True desde nivel BASIC (adopción). Los callsites existentes no cambian."""
    return _level() >= 1


def is_verbose() -> bool:
    """True solo en VERBOSE (interacción fina). Gatea los eventos nuevos."""
    return _level() >= 2


def _strip_query(text: str) -> str:
    base, sep, frag = text.partition("#")
    return base.split("?", 1)[0] + (sep + frag if sep else "")


def _clean_value(value: Any) -> Any:
    """Saneamiento por VALOR, recursivo. Nunca levanta."""
    if isinstance(value, str):
        text = value
        if _URL.match(text.strip()) or (text.startswith("/") and "?" in text):
            # URL/ruta: sin query string (puede traer firmas/tokens); el
            # fragmento (`#seccion`) se conserva: es la señal útil de nav_jump.
            text = _strip_query(text)
        if _SECRET_VALUE.search(text):
            return _REDACTED
        return text[:_MAX_STRING_LEN] + "…[truncated]" if len(text) > _MAX_STRING_LEN else text
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _clean_value(v) for k, v in value.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(value, (list, tuple, set)):
        return [_clean_value(item) for item in list(value)[:_MAX_LIST_LEN]]
    return _clean_value(str(value))


def build_attrs(event_type: str, payload: dict) -> dict:
    """Payload del productor → `attrs` del envelope v2: whitelist del catálogo
    (por clave) + saneamiento por valor. Evento no catalogado → {}."""
    filtered = activity_catalog.filter_attrs(event_type, payload)
    return {k: _clean_value(v) for k, v in filtered.items() if not _SECRET_KEY.search(k)}


def build_envelope(
    event_type: str,
    user: dict | None,
    *,
    scope: str | None = None,
    area: str | None = None,
    module: str | None = None,
    section: str | None = None,
    payload: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Envelope v2 (ver docstring del módulo). Separado de la escritura para
    poder testear la forma sin tocar disco."""
    user = user or {}
    now = now or datetime.now(timezone.utc)
    spec = activity_catalog.spec_of(event_type)
    if spec is None and event_type not in _warned_unknown:
        _warned_unknown.add(event_type)
        log.warning("activity_service: evento no catalogado %r — se escribe con attrs vacíos (sumarlo a activity_catalog)", event_type)
    # Sin contexto explícito, un evento CATALOGADO toma su default; uno no
    # catalogado queda sin scope/area (el lector lo clasifica `unknown`).
    if spec is not None:
        default_scope, default_area = activity_catalog.default_context(event_type)
        scope = scope or default_scope
        area = area or default_area
    attrs = build_attrs(event_type, payload or {})
    if event_type == "login" and user.get("name"):
        # El display name viaja UNA vez por sesión (ver docstring del módulo).
        attrs["display_name"] = _clean_value(str(user.get("name")))
    envelope: dict[str, Any] = {
        "v": 2,
        "id": uuid.uuid4().hex,
        "ts_utc": now.isoformat(timespec="seconds"),
        "kind": spec.kind if spec else activity_catalog.KIND_UNKNOWN,
        "event": event_type,
        "user": str(user.get("sub") or user.get("email") or ""),
    }
    if scope:
        envelope["vertical"] = activity_catalog.vertical_of(scope)
        envelope["scope"] = str(scope)
    if area:
        envelope["area"] = str(area)
    if module is not None:
        envelope["module"] = str(module)
    if section:
        envelope["section"] = _clean_value(str(section))
    if spec is not None:
        envelope["surface"] = spec.surface
    envelope["attrs"] = attrs
    return envelope


def log_activity(
    event_type: str,
    user: dict | None = None,
    *,
    scope: str | None = None,
    area: str | None = None,
    module: str | None = None,
    section: str | None = None,
    **payload: Any,
) -> None:
    """Registra un evento de actividad. `user` es el dict de la sesión FastAPI
    (auth_service.session_user): se toma sub/email como identidad.

    `scope`/`area`/`module` son el CONTEXTO de plataforma, pasados EXPLÍCITOS
    por el productor (si faltan, se completan con el default del catálogo).
    `section` (v2, opcional) es la sección dentro del módulo — campo de primera
    clase del envelope para poder desglosar vertical → módulo → sección.
    Keyword-only para que un body arbitrario reenviado no pueda inyectarlos
    (ver `RESERVED_KEYS`). `**payload` pasa por la whitelist del catálogo:
    lo que no esté permitido para ese evento NO se escribe.

    Nunca levanta hacia el caller: una falla de escritura se informa en el
    logger del servidor (sin contenido del evento) y la experiencia sigue."""
    if not is_activity_logging_enabled():
        return
    try:
        envelope = build_envelope(
            event_type, user, scope=scope, area=area, module=module, section=section, payload=payload,
        )
        ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        day = envelope["ts_utc"][:10].replace("-", "")
        out_path = ACTIVITY_LOG_DIR / f"activity_{day}.jsonl"
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:  # noqa: BLE001 — nunca bloqueamos la experiencia por logging
        try:
            log.warning("activity_service: no se pudo registrar un evento de actividad (%s)", type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass


def log_verbose(
    event_type: str,
    user: dict | None = None,
    *,
    scope: str | None = None,
    area: str | None = None,
    module: str | None = None,
    section: str | None = None,
    **payload: Any,
) -> None:
    """Evento de interacción fina (pregunta al asistente, paso de árbol, vista de
    catálogo, error). Solo registra en nivel VERBOSE; en BASIC/OFF es no-op.
    El contexto se propaga tal cual a log_activity."""
    if not is_verbose():
        return
    log_activity(event_type, user=user, scope=scope, area=area, module=module, section=section, **payload)
