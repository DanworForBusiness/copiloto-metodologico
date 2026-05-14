from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from config import ACTIVITY_LOG_DIR, ALLOWED_USERS, AUTH_DEFAULT_DOMAIN, LOG_ACTIVITY

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


def _allowed_activity_users() -> set[str] | None:
    raw = (ALLOWED_USERS or "").strip()
    if not raw or raw.upper() == "ALL" or raw == "*":
        return None

    domain = (AUTH_DEFAULT_DOMAIN or "danwor.com").strip().lower().lstrip("@")
    users: set[str] = set()
    for item in raw.split(","):
        user = item.strip().lower()
        if not user:
            continue
        if "@" not in user:
            user = f"{user}@{domain}"
        users.add(user)
    return users


def _can_log_user(username: str) -> bool:
    allowed = _allowed_activity_users()
    if allowed is None:
        return True
    return (username or "").strip().lower() in allowed


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


def log_activity(event_type: str, **payload: Any) -> None:
    """
    Registra actividad operacional inicial en JSON Lines.

    El log es intencionalmente simple: una línea por evento, particionado por día.
    No reemplaza auditoría formal ni analytics persistente; sirve para observar
    adopción temprana mientras LOG_ACTIVITY esté habilitado.
    """
    if not is_activity_logging_enabled():
        return

    try:
        auth = st.session_state.get("_auth", {}) if hasattr(st, "session_state") else {}
        now = datetime.now(timezone.utc)
        username = payload.pop("username", None) or auth.get("username", "")
        if not _can_log_user(username):
            return
        event = {
            "ts_utc": now.isoformat(timespec="seconds"),
            "event": event_type,
            "user": username,
            "name": payload.pop("name", None) or auth.get("name", ""),
            "payload": _safe_value(payload),
        }
        ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        out_path = ACTIVITY_LOG_DIR / f"activity_{now:%Y%m%d}.jsonl"
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        # Nunca bloqueamos la experiencia por un problema de logging.
        return
