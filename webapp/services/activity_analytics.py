"""
activity_analytics.py
======================
Resumen de adopción/uso a partir de los JSONL diarios que escribe
activity_service.log_activity/log_verbose en ACTIVITY_LOG_DIR. Un solo pase de
lectura de archivos, sin dependencias externas (stdlib) — el volumen esperado
en el lanzamiento reducido es chico, no hace falta pandas/duckdb ni caché.

No reemplaza auditoría formal: es una vista de lectura sobre el mismo log
liviano de adopción (ver activity_service.py), acotada por rango de fechas y,
opcionalmente, por usuario y/o módulo.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from config import ACTIVITY_LOG_DIR, FEEDBACK_LOG_DIR

# Misma convención de nombre/rango que admin_tools._file_date /
# runtime_purge: activity_YYYYMMDD.jsonl, bounds YYYYMMDD, vacío = sin límite.
_FILE_DATE_RE = re.compile(r"activity_(\d{8})\.jsonl$")

# route_heading llega con el título crudo de la sección, que puede traer la sintaxis
# de dos tonos del contenido (p. ej. "{dw-black}Tipos de {dw-gold}Vistas CDS") — la
# misma que content_service.TONE_RE sabe convertir a <span> al renderizar Markdown.
# Acá no renderizamos nada, así que directamente se descarta para mostrar texto plano.
_TONE_TAG_RE = re.compile(r"\{dw-(black|gold|white|green|red)\}", re.I)

# Eventos que llevan una referencia de módulo/stem en el payload (module_selected
# usa "module", el resto usa "stem"). El resto de los eventos (login, catalog_view,
# tree_*) no son atribuibles a un módulo puntual.
_MODULE_KEY_EVENTS = {"module_selected", "section_analyze", "assistant_query", "tree_started"}
_TOP_N = 10
_RECENT_QUESTIONS_N = 20


def _bounds(date_from: str, date_to: str) -> tuple[str, str]:
    low = (date_from or "").replace("-", "").strip() or "00000000"
    high = (date_to or "").replace("-", "").strip() or "99999999"
    return low, high


def _log_files(low: str, high: str) -> list[tuple[str, Path]]:
    if not ACTIVITY_LOG_DIR.exists():
        return []
    hits = []
    for path in ACTIVITY_LOG_DIR.glob("activity_*.jsonl"):
        match = _FILE_DATE_RE.search(path.name)
        if not match:
            continue
        day = match.group(1)
        if low <= day <= high:
            hits.append((day, path))
    return sorted(hits)


def _module_key(event_type: str, payload: dict[str, Any]) -> str:
    if event_type not in _MODULE_KEY_EVENTS:
        return ""
    return str(payload.get("module") or payload.get("stem") or "").strip()


def _read_feedback(date_from: str, date_to: str) -> dict[str, dict]:
    """Feedback (👍/👎 + comentario) de respuestas del asistente, indexado por
    reply_id — ver AssistantFeedbackRepository.save (assistant_service.py), que
    ahora graba reply_id/conversation_id para poder correlacionar acá. YAML
    previos a ese cambio no tienen reply_id: se ignoran para la historia (se
    siguen viendo igual en los reportes de analyze_feedback.py, sin cambios)."""
    low, high = _bounds(date_from, date_to)
    by_reply: dict[str, dict] = {}
    if not FEEDBACK_LOG_DIR.exists():
        return by_reply
    for path in FEEDBACK_LOG_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or data.get("type") != "assistant_response":
            continue
        reply_id = str(data.get("reply_id") or "").strip()
        if not reply_id:
            continue
        day = str(data.get("ts") or "")[:10].replace("-", "")
        if day and not (low <= day <= high):
            continue
        by_reply[reply_id] = {
            "rating": str(data.get("rating") or ""),
            "comment": str(data.get("comment") or ""),
        }
    return by_reply


def _read_events(date_from: str, date_to: str) -> list[dict]:
    """Parsea los JSONL del rango a una lista de eventos normalizados (día,
    tipo, usuario, nombre, ts, payload, module_key). Líneas corruptas se
    descartan silenciosamente, igual que log_activity nunca rompe la app."""
    low, high = _bounds(date_from, date_to)
    events: list[dict] = []
    for day, path in _log_files(low, high):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            event_type = str(raw.get("event") or "").strip()
            if not event_type:
                continue
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            user = str(raw.get("user") or "").strip()
            name = str(raw.get("name") or "").strip()
            events.append({
                "day": day,
                "event": event_type,
                "user": user,
                "name": name,
                "ts_utc": str(raw.get("ts_utc") or ""),
                "payload": payload,
                "module": _module_key(event_type, payload),
            })
    return events


def _facets(events: list[dict]) -> tuple[list[dict], list[str]]:
    """Usuarios/módulos distintos en el rango de fechas (ANTES de aplicar el
    filtro de usuario/módulo), para que los <select> no se vacíen al filtrar."""
    users: dict[str, str] = {}
    modules: set[str] = set()
    for ev in events:
        if ev["user"]:
            users.setdefault(ev["user"], ev["name"] or ev["user"])
        if ev["module"]:
            modules.add(ev["module"])
    available_users = [
        {"value": user, "label": label}
        for user, label in sorted(users.items(), key=lambda kv: kv[1].lower())
    ]
    available_modules = sorted(modules)
    return available_users, available_modules


def _top(counter: Counter, n: int = _TOP_N) -> list[dict]:
    total_max = counter.most_common(1)[0][1] if counter else 0
    rows = []
    for key, count in counter.most_common(n):
        rows.append({
            "label": key,
            "count": count,
            "pct": round(100 * count / total_max) if total_max else 0,
        })
    return rows


def build_summary(date_from: str, date_to: str, user: str = "", module: str = "") -> dict:
    all_events = _read_events(date_from, date_to)
    available_users, available_modules = _facets(all_events)
    feedback_by_reply = _read_feedback(date_from, date_to)

    user = (user or "").strip()
    module = (module or "").strip()
    events = [
        ev for ev in all_events
        if (not user or ev["user"] == user) and (not module or ev["module"] == module)
    ]

    total_events = 0
    users_seen: set[str] = set()
    event_counts: Counter = Counter()
    daily_counts: Counter = Counter()
    module_counts: Counter = Counter()
    user_counts: Counter = Counter()
    catalog_kind_counts: Counter = Counter()
    # Conversaciones del asistente agrupadas por conversation_id: cada "turno" es un
    # assistant_query (router, o el bypass de "Explícame vos"), con su feedback si
    # hubo. Eventos viejos sin conversation_id quedan como conversación de un solo
    # turno (mismo comportamiento que antes de esta correlación).
    assistant_conversations: dict[str, dict] = {}
    assistant_order: list[str] = []
    ts_min: str | None = None
    ts_max: str | None = None
    files_scanned = len({ev["day"] for ev in all_events})

    for ev in events:
        total_events += 1
        daily_counts[ev["day"]] += 1
        event_counts[ev["event"]] += 1

        if ev["user"]:
            users_seen.add(ev["user"])
            user_counts[ev["name"] or ev["user"]] += 1

        if ev["ts_utc"]:
            ts_min = ev["ts_utc"] if ts_min is None else min(ts_min, ev["ts_utc"])
            ts_max = ev["ts_utc"] if ts_max is None else max(ts_max, ev["ts_utc"])

        if ev["module"]:
            module_counts[ev["module"]] += 1

        if ev["event"] == "catalog_view":
            kind = str(ev["payload"].get("kind") or "").strip()
            if kind:
                catalog_kind_counts[kind] += 1
        elif ev["event"] == "assistant_query":
            question = str(ev["payload"].get("question") or "").strip()
            if question:
                conv_id = str(ev["payload"].get("conversation_id") or "").strip()
                reply_id = str(ev["payload"].get("reply_id") or "").strip()
                key = conv_id or f"_solo:{len(assistant_order)}"
                if key not in assistant_conversations:
                    assistant_conversations[key] = {
                        "question": question[:200],
                        "user": ev["name"] or ev["user"],
                        "turns": [],
                    }
                    assistant_order.append(key)
                assistant_conversations[key]["turns"].append({
                    "ts_utc": ev["ts_utc"],
                    "routed": bool(ev["payload"].get("routed")),
                    "route_heading": _TONE_TAG_RE.sub("", str(ev["payload"].get("route_heading") or "")).strip(),
                    "bypass": bool(ev["payload"].get("bypass")),
                    "llm": bool(ev["payload"].get("llm")),
                    "api_cost": bool(ev["payload"].get("api_cost")),
                    "answer": _TONE_TAG_RE.sub("", str(ev["payload"].get("answer") or "")).strip(),
                    "feedback": feedback_by_reply.get(reply_id),
                })

    daily_series = [
        {"day": day, "count": count, "pct": round(100 * count / max(daily_counts.values())) if daily_counts else 0}
        for day, count in sorted(daily_counts.items())
    ]
    total_days = len(daily_counts)

    # Resumen precalculado por conversación (fila colapsada del dashboard) + KPI de
    # costo: cuántas conversaciones tuvieron AL MENOS un turno con llamada real a
    # OpenAI (no todas las "preguntas al asistente" cuestan — router/simulación no).
    assistant_api_cost_count = 0
    for conv in assistant_conversations.values():
        turns = conv["turns"]
        conv["turn_count"] = len(turns)
        conv["has_down_feedback"] = any(t["feedback"] and t["feedback"]["rating"] == "down" for t in turns)
        conv["has_api_cost"] = any(t["api_cost"] for t in turns)
        if conv["has_api_cost"]:
            assistant_api_cost_count += 1

    recent_keys = assistant_order[-_RECENT_QUESTIONS_N:]
    assistant_stories = [assistant_conversations[k] for k in reversed(recent_keys)]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "selected_user": user,
        "selected_module": module,
        "available_users": available_users,
        "available_modules": available_modules,
        "files_scanned": files_scanned,
        "total_events": total_events,
        "unique_users": len(users_seen),
        "total_days": total_days,
        "avg_per_day": round(total_events / total_days) if total_days else 0,
        "assistant_query_count": event_counts.get("assistant_query", 0),
        "assistant_api_cost_count": assistant_api_cost_count,
        "ts_min": ts_min,
        "ts_max": ts_max,
        "event_counts": [
            {"event": event, "count": count}
            for event, count in event_counts.most_common()
        ],
        "top_modules": _top(module_counts),
        "top_users": _top(user_counts),
        "catalog_kind_counts": [
            {"kind": kind, "count": count}
            for kind, count in catalog_kind_counts.most_common()
        ],
        "daily_series": daily_series,
        "assistant_stories": assistant_stories,
    }
