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
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from config import ACTIVITY_LOG_DIR, LOG_ACTIVITY

# Niveles de logging. OFF=0 (nada), BASIC=1 (adopción: login/módulos/…),
# VERBOSE=2 (BASIC + interacción fina: preguntas al asistente, pasos de árbol,
# navegación de catálogo, errores). ON/TRUE/1 = BASIC por compatibilidad.
_LEVELS = {
    "": 0, "OFF": 0, "NO": 0, "FALSE": 0, "0": 0,
    "BASIC": 1, "ON": 1, "1": 1, "TRUE": 1, "YES": 1, "SI": 1, "SÍ": 1,
    "VERBOSE": 2, "FULL": 2, "2": 2,
}
_ENABLED_VALUES = {"1", "ON", "TRUE", "YES", "SI", "SÍ", "BASIC", "VERBOSE", "FULL", "2"}
_SENSITIVE_KEYS = {
    "token",
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
}
_MAX_STRING_LEN = 4000
# Campos de texto "largo": la respuesta del asistente (`answer`) se muestra
# COMPLETA en el dashboard de actividad (historia de preguntas), así que se le
# da un tope mucho más alto que al resto de los campos para no cortar guías
# operativas / árboles de diagnóstico largos. El resto de los campos sigue en
# _MAX_STRING_LEN (el log se mantiene liviano: solo este campo crece).
_LONG_TEXT_KEYS = {"answer"}
_MAX_TEXT_LEN = 20000


def _level() -> int:
    return _LEVELS.get((LOG_ACTIVITY or "").strip().upper(), 0)


def is_activity_logging_enabled() -> bool:
    """True desde nivel BASIC (adopción). Los callsites existentes no cambian."""
    return _level() >= 1


def is_verbose() -> bool:
    """True solo en VERBOSE (interacción fina). Gatea los eventos nuevos."""
    return _level() >= 2


def _safe_value(value: Any, max_len: int = _MAX_STRING_LEN) -> Any:
    if isinstance(value, str):
        return value[:max_len] + "…[truncated]" if len(value) > max_len else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in _SENSITIVE_KEYS:
                clean[key_str] = "[redacted]"
            else:
                limit = _MAX_TEXT_LEN if key_str.lower() in _LONG_TEXT_KEYS else _MAX_STRING_LEN
                clean[key_str] = _safe_value(item, limit)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in list(value)[:50]]
    return str(value)


def log_activity(
    event_type: str,
    user: dict | None = None,
    *,
    scope: str | None = None,
    area: str | None = None,
    module: str | None = None,
    **payload: Any,
) -> None:
    """Registra un evento de actividad. `user` es el dict de la sesión FastAPI
    (auth_service.session_user): se toman sub/email y name.

    `scope`/`area` son el CONTEXTO de plataforma (Batch A1), pasados EXPLÍCITOS
    por el productor — este sink NO los infiere ni conoce la taxonomía (esa vive
    en `activity_context`). `module` (Batch A2 corrective) es la dimensión
    OPCIONAL y TRANSVERSAL de navegación (Copilot Y Operations) — antes solo
    viajaba dentro de `payload` (`payload.module`/`payload.stem`); ahora es un
    campo de primera clase del envelope, igual de opcional. Ninguno de los tres
    se infiere ni se conoce acá la taxonomía real (vive en `activity_context`/
    `activity_analytics`) — este sink sigue siendo genérico. Keyword-only para
    no colisionar con `**payload` ni poder ser inyectados por un caller que
    reenvía un body arbitrario. Se escriben aditivamente entre `name` y
    `payload`; si son None se omiten (envelope histórico de 5 campos preservado
    para callers que no los pasen — el lector normaliza su ausencia)."""
    if not is_activity_logging_enabled():
        return
    try:
        user = user or {}
        now = datetime.now(timezone.utc)
        event = {
            "ts_utc": now.isoformat(timespec="seconds"),
            "event": event_type,
            "user": str(user.get("sub") or user.get("email") or ""),
            "name": str(user.get("name") or ""),
        }
        if scope is not None:
            event["scope"] = str(scope)
        if area is not None:
            event["area"] = str(area)
        if module is not None:
            event["module"] = str(module)
        event["payload"] = _safe_value(payload)
        ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        out_path = ACTIVITY_LOG_DIR / f"activity_{now:%Y%m%d}.jsonl"
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        # Nunca bloqueamos la experiencia por un problema de logging.
        return


def log_verbose(
    event_type: str,
    user: dict | None = None,
    *,
    scope: str | None = None,
    area: str | None = None,
    module: str | None = None,
    **payload: Any,
) -> None:
    """Evento de interacción fina (pregunta al asistente, paso de árbol, vista de
    catálogo, error). Solo registra en nivel VERBOSE; en BASIC/OFF es no-op.
    `scope`/`area`/`module` (contexto de plataforma) se propagan tal cual a
    log_activity."""
    if not is_verbose():
        return
    log_activity(event_type, user=user, scope=scope, area=area, module=module, **payload)
