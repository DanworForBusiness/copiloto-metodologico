"""Centro de Notificaciones del Hub (capacidad de PLATAFORMA): bandeja personal unificada con estado de lectura propio.

Modelo híbrido (sin framework, sin colas ni schedulers):
  - PROVEEDORES: cada vertical registra una función que, para un usuario, calcula sus notificaciones a partir de su propio estado funcional
    (`register_provider`). La vertical decide qué es notificable y para quién (y respeta sus autorizaciones al calcular).
  - EVENTOS PERSISTIDOS: para lo que no puede derivarse del estado (`notify`), guardados por destinatario con deduplicación por clave.
El Hub gobierna: identidad estable de cada notificación (`<origen>:<id>`), estado de lectura (individual + «todas leídas»), DESCARTE por usuario
(«quitar de la lista»: leer ≠ descartar; el evento o el estado de origen no se tocan, solo deja de mostrarse en SU bandeja y no vuelve), contador de no
leídas compartido (campana del header y portal de Cartelera) y la navegación al contexto (`/notificaciones/abrir/<id>`, solo ítems de la propia
bandeja). El correo es un canal aparte (`notifications.py`): una notificación interna nunca implica un correo.
Persistencia: `notifications/inbox/<hash>.json` (estado) y `<hash>.events.json` (eventos) — `notifications/` ya es un directorio persistido
de plataforma; no se agrega infraestructura. Migración: al primer uso, el sello «visto hasta» que cada proveedor tenía (`legacy_seen`) se toma
como «todas leídas» hasta ese instante: nadie recibe de nuevo su historial.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import config
from core import remote_store

log = logging.getLogger("danwor.hub_inbox")
MAX_ITEMS = 50
_PROVIDERS: dict[str, dict] = {}
_lock = threading.Lock()


def register_provider(source: str, label: str, items: Callable[[dict], list[dict]], legacy_seen: Callable[[dict], str] | None = None) -> None:
    """`items(user)` → [{'id', 'at' (ISO UTC), 'text', 'url', 'feature'}] ya filtrado por las autorizaciones de la vertical."""
    _PROVIDERS[source] = {"label": label, "items": items, "legacy_seen": legacy_seen}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _email(user: dict | None) -> str:
    return str((user or {}).get("sub") or "").strip().lower()


def _base(email: str) -> Path:
    return config.BASE_DIR / "notifications" / "inbox" / hashlib.sha256(email.encode("utf-8")).hexdigest()[:32]


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


def _state(user: dict) -> dict:
    """Estado de lectura del usuario; en el primer uso de cada proveedor, migra su sello «visto hasta»."""
    email = _email(user)
    path = Path(f"{_base(email)}.json")
    state = _load(path, {}) or {}
    state.setdefault("read", {}); state.setdefault("all_read_at", ""); state.setdefault("migrated", []); state.setdefault("dismissed", {})
    pending = [s for s in _PROVIDERS if s not in state["migrated"]]
    if pending:
        for source in pending:
            fn = _PROVIDERS[source].get("legacy_seen")
            try:
                legacy = str(fn(user) or "") if fn else ""
            except Exception:                                                     # noqa: BLE001 — un proveedor caído no rompe la bandeja
                legacy = ""
            if legacy > state["all_read_at"]:
                state["all_read_at"] = legacy
            state["migrated"].append(source)
        _save_state(email, state)
    return state


def _save_state(email: str, state: dict) -> None:
    remote_store.write_through(Path(f"{_base(email)}.json"), json.dumps(state, ensure_ascii=False))


def items(user: dict | None) -> list[dict]:
    """Bandeja unificada del usuario (proveedores + eventos), más nueva primero, con `unread` resuelto por el estado del Hub."""
    email = _email(user)
    if not email:
        return []
    out: list[dict] = []
    for source, prov in _PROVIDERS.items():
        try:
            rows = prov["items"](user) or []
        except Exception as exc:                                                  # noqa: BLE001
            log.warning("hub_inbox: el proveedor %s falló: %s", source, exc)
            continue
        out += [{"id": f"{source}:{r['id']}", "source": source, "source_label": prov["label"], "feature": r.get("feature", ""),
                 "at": r.get("at") or "", "text": r.get("text", ""), "url": r.get("url", "")} for r in rows if r.get("id")]
    for ev in _load(Path(f"{_base(email)}.events.json"), []):
        out.append({**ev, "source_label": (_PROVIDERS.get(ev.get("source")) or {}).get("label", ev.get("source", ""))})
    state = _state(user)
    out = [n for n in out if n["id"] not in state["dismissed"]]                   # descartadas: fuera de la lista (y del contador) para siempre
    out.sort(key=lambda n: n["at"], reverse=True)
    out = out[:MAX_ITEMS]
    for n in out:
        n["unread"] = n["at"] > state["all_read_at"] and n["id"] not in state["read"]
    return out


def unread_count(user: dict | None) -> int:
    return sum(1 for n in items(user) if n["unread"])


def resolve(user: dict | None, notification_id: str) -> dict | None:
    """La notificación SOLO si está en la bandeja del propio usuario (navegación con sus autorizaciones)."""
    return next((n for n in items(user) if n["id"] == notification_id), None)


def mark_read(user: dict, notification_id: str) -> bool:
    if not resolve(user, notification_id):
        return False
    with _lock:
        state = _state(user)
        state["read"][notification_id] = _now()
        _save_state(_email(user), state)
    return True


def dismiss(user: dict, notification_id: str) -> bool:
    """Quita UNA notificación de la bandeja del usuario (no borra el evento ni el estado de origen). Solo ítems de la PROPIA bandeja; idempotente
    en efecto (una ya descartada deja de estar en la bandeja ⇒ ``False``). El descarte es PERMANENTE: se recuerda sin límite (un id por descarte,
    en el mismo archivo de estado), porque un proveedor puede volver a calcular una notificación vieja y no debe reaparecer."""
    if not resolve(user, notification_id):
        return False
    with _lock:
        state = _state(user)
        state["dismissed"][notification_id] = _now()
        state["read"].pop(notification_id, None)
        _save_state(_email(user), state)
    return True


def mark_all_read(user: dict) -> None:
    with _lock:
        state = _state(user)
        state["all_read_at"], state["read"] = _now(), {}                          # lo leído individualmente queda cubierto por el sello (los descartes se conservan)
        _save_state(_email(user), state)


def notify(email: str, *, source: str, feature: str, key: str, text: str, url: str, at: str | None = None) -> bool:
    """Evento PERSISTIDO para un destinatario (lo que no puede derivarse del estado). Idempotente por (origen, clave)."""
    email = str(email or "").strip().lower()
    if not email or not key:
        return False
    path = Path(f"{_base(email)}.events.json")
    nid = f"{source}:{key}"
    with _lock:
        events = _load(path, [])
        if any(e.get("id") == nid for e in events):
            return False
        events.append({"id": nid, "source": source, "feature": feature, "at": at or _now(), "text": str(text)[:300], "url": url})
        remote_store.write_through(path, json.dumps(events[-200:], ensure_ascii=False))
    return True
