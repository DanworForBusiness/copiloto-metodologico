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

_ENABLED_VALUES = {"1", "ON", "TRUE", "YES", "SI", "SÍ"}
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


def is_activity_logging_enabled() -> bool:
    return (LOG_ACTIVITY or "").strip().upper() in _ENABLED_VALUES


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_MAX_STRING_LEN] + "…[truncated]" if len(value) > _MAX_STRING_LEN else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in _SENSITIVE_KEYS:
                clean[key_str] = "[redacted]"
            else:
                clean[key_str] = _safe_value(item)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in list(value)[:50]]
    return str(value)


def log_activity(event_type: str, user: dict | None = None, **payload: Any) -> None:
    """Registra un evento de actividad. `user` es el dict de la sesión FastAPI
    (auth_service.session_user): se toman sub/email y name."""
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
            "payload": _safe_value(payload),
        }
        ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        out_path = ACTIVITY_LOG_DIR / f"activity_{now:%Y%m%d}.jsonl"
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        # Nunca bloqueamos la experiencia por un problema de logging.
        return
