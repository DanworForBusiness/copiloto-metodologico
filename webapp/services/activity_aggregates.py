"""
activity_aggregates.py
======================
Agregados diarios de actividad (Métricas de Uso v2 — fase 1).

Problema que resuelve: `activity_analytics.build_summary` relee TODOS los JSONL
del rango en cada request. Con 13 meses de historia eso no escala, y además
cada lectura vuelve a tocar payloads v1 con texto libre. Acá:

- Un día CERRADO (anterior a hoy, UTC) se agrega UNA vez a
  `activity_log/agg/YYYYMMDD.json` vía `core.remote_store.write_through` (llega
  a Spaces igual que el JSONL). Idempotente: el archivo guarda la FIRMA de su
  fuente (tamaño + mtime del JSONL, versión del catálogo y del esquema); si la
  firma coincide se reusa, si el JSONL cambió (o cambió la taxonomía) se
  recalcula. Nada en memoria entre requests.
- HOY se calcula al vuelo desde el JSONL crudo con la MISMA función (nunca se
  persiste un día abierto).
- Todo pasa por `activity_context.normalize_record`: v1 y v2 llegan con la
  misma forma y con `attrs` ya filtrados por la whitelist del catálogo — el
  texto libre de v1 nunca entra a un agregado.

Filas: una por (vertical, area, module, section, kind, event, cls, status,
user) con `count`, `view_ids` (visitas v2 DISTINTAS), `visits_v1` (visitas
v1, por evento) y `ts` (segundos desde 00:00 UTC de cada evento). Guardar `ts`
por fila permite calcular sesiones EXACTAS bajo cualquier filtro/visibilidad
(una sesión se computa solo con los eventos que el viewer puede ver, nunca con
los de una vertical oculta) y unir sesiones que cruzan la medianoche.

Además, por usuario, `sessions`: los tramos del día (inactividad > 30 min corta
sesión) con inicio/fin ISO — resumen del día (lo pidió el diseño para poder
unir tramos entre días). `query` NO los usa: recalcula desde `ts` de las filas
filtradas, que da el mismo resultado sin filtros y el correcto con filtros.

Retención: no se borra nada automáticamente. `RETENTION_MONTHS` es solo un
valor para mostrar en la UI.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from config import ACTIVITY_LOG_DIR
from webapp.services import activity_catalog
from webapp.services.activity_context import (
    EVENT_CLASS_USAGE, area_label, is_visit, module_label, normalize_record, scope_label,
)

log = logging.getLogger(__name__)

RETENTION_MONTHS = 13
SESSION_GAP_S = 30 * 60
# Subir si cambia la FORMA del archivo de agregado: invalida los existentes.
AGG_SCHEMA = 1
_DIMENSIONS = ("vertical", "area", "module", "section", "kind", "event", "cls", "status", "user")
_FILTER_KEYS = ("vertical", "module", "section", "user", "kind", "event", "cls")
_MAX_PAGE_SIZE = 200


# ── fechas ───────────────────────────────────────────────────────────────────

def _day_key(value: Any) -> str:
    """'YYYYMMDD' desde date/datetime/'YYYY-MM-DD'/'YYYYMMDD'."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value or "").replace("-", "").strip()
    datetime.strptime(text, "%Y%m%d")  # valida (ValueError si no)
    return text


def _days(start: Any, end: Any) -> list[str]:
    low = datetime.strptime(_day_key(start), "%Y%m%d").date()
    high = datetime.strptime(_day_key(end), "%Y%m%d").date()
    out = []
    while low <= high:
        out.append(low.strftime("%Y%m%d"))
        low += timedelta(days=1)
    return out


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _day_epoch(day: str) -> int:
    return int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())


def _ts_seconds(ts_utc: str, day: str) -> int | None:
    try:
        stamp = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp()) - _day_epoch(day)


# ── archivos ─────────────────────────────────────────────────────────────────

def _log_dir() -> Path:
    return Path(ACTIVITY_LOG_DIR)


def _source_path(day: str) -> Path:
    return _log_dir() / f"activity_{day}.jsonl"


def _agg_path(day: str) -> Path:
    return _log_dir() / "agg" / f"{day}.json"


def _signature(source: Path) -> dict:
    st = source.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "catalog": activity_catalog.version(), "schema": AGG_SCHEMA}


def _read_records(day: str) -> list[dict]:
    """Registros NORMALIZADOS (v1 y v2) del JSONL del día. Líneas corruptas se
    descartan en silencio, igual que el lector del dashboard."""
    path = _source_path(day)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        rec = normalize_record(raw)
        if rec is not None:
            out.append(rec)
    return out


# ── cómputo de un día ────────────────────────────────────────────────────────

def _status(rec: dict) -> str:
    """Estado de un `outcome` (éxito/falla) a partir de los attrs que los
    productores ya escriben (`state`, `ok`, `error`…). Vacío para no-outcomes."""
    if rec["kind"] != activity_catalog.KIND_OUTCOME:
        return ""
    attrs = rec["attrs"]
    state = str(attrs.get("state") or attrs.get("status") or "").strip().lower()
    if state in ("success", "succeeded", "ok", "done", "published", "completed"):
        return "success"
    if state in ("failed", "failure", "error", "errored"):
        return "failed"
    if state:
        return state
    if isinstance(attrs.get("ok"), bool):
        return "success" if attrs["ok"] else "failed"
    if attrs.get("error") or attrs.get("error_code"):
        return "failed"
    if rec["event"] == "tree_outcome":
        return "success"
    return "done"


def _spans(seconds: list[int], gap: int = SESSION_GAP_S) -> list[tuple[int, int]]:
    """Tramos de actividad: una pausa > `gap` corta la sesión."""
    spans: list[tuple[int, int]] = []
    for ts in sorted(seconds):
        if spans and ts - spans[-1][1] <= gap:
            spans[-1] = (spans[-1][0], ts)
        else:
            spans.append((ts, ts))
    return spans


def compute_day(day: str, records: list[dict] | None = None) -> dict:
    """Agregado de UN día (misma función para días cerrados y para hoy)."""
    records = _read_records(day) if records is None else records
    rows: dict[tuple, dict] = {}
    usage_seconds: dict[str, list[int]] = {}
    for rec in records:
        key = (rec["vertical"], rec["area"], rec["module"], rec["section"], rec["kind"],
               rec["event"], rec["event_class"], _status(rec), rec["user"])
        row = rows.get(key)
        if row is None:
            row = rows[key] = {**dict(zip(_DIMENSIONS, key)), "count": 0, "visits_v1": 0, "view_ids": set(), "ts": []}
        row["count"] += 1
        if is_visit(rec):
            if rec["v"] == 2:
                view_id = str(rec["attrs"].get("view_id") or "")
                if view_id:
                    row["view_ids"].add(view_id)
            else:
                row["visits_v1"] += 1
        sec = _ts_seconds(rec["ts_utc"], day)
        if sec is not None:
            row["ts"].append(sec)
            if rec["event_class"] == EVENT_CLASS_USAGE and rec["user"]:
                usage_seconds.setdefault(rec["user"], []).append(sec)
    epoch = _day_epoch(day)

    def _iso(sec: int) -> str:
        return datetime.fromtimestamp(epoch + sec, tz=timezone.utc).isoformat(timespec="seconds")

    return {
        "schema": AGG_SCHEMA,
        "day": day,
        "rows": [
            {**row, "view_ids": sorted(row["view_ids"]), "ts": sorted(row["ts"])}
            for row in rows.values()
        ],
        "sessions": {
            user: [[_iso(a), _iso(b)] for a, b in _spans(secs)]
            for user, secs in sorted(usage_seconds.items())
        },
    }


def load_day(day: str, *, today: str | None = None) -> dict | None:
    """Agregado del día: al vuelo si es HOY (o futuro); para un día cerrado,
    el archivo persistido si su firma coincide con la fuente, si no se
    recalcula y se reescribe (write-through a Spaces). None si no hubo
    actividad ese día (ni JSONL ni agregado)."""
    today = today or _today()
    source = _source_path(day)
    if day >= today:
        return compute_day(day) if source.is_file() else None
    agg = _agg_path(day)
    stored = None
    if agg.is_file():
        try:
            stored = json.loads(agg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = None
    if not source.is_file():
        # Sin JSONL (p. ej. todavía no restaurado) el agregado persistido es
        # la mejor fuente disponible — nunca se borra ni se recalcula a vacío.
        return stored
    signature = _signature(source)
    if isinstance(stored, dict) and stored.get("signature") == signature:
        return stored
    result = {**compute_day(day), "signature": signature}
    try:
        from core import remote_store
        remote_store.write_through(agg, json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:  # noqa: BLE001 — el agregado es derivable: se devuelve igual
        log.warning("activity_aggregates: no se pudo persistir el agregado de %s (%s)", day, type(exc).__name__)
    return result


# ── consultas ────────────────────────────────────────────────────────────────

def _as_set(value) -> set[str] | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return {value}
    return {str(v) for v in value}


def _normalized_filters(filters: dict | None) -> dict[str, set[str]]:
    filters = dict(filters or {})
    filters.setdefault("cls", EVENT_CLASS_USAGE)
    out = {}
    for key in _FILTER_KEYS:
        wanted = _as_set(filters.get(key))
        if wanted is not None:
            out[key] = wanted
    return out


def _visible(vertical: str, visible: set[str] | None) -> bool:
    return visible is None or vertical in visible


def _matches(item: dict, wanted: dict[str, set[str]]) -> bool:
    return all(str(item.get(key) or "") in values for key, values in wanted.items())


def query(start, end, filters: dict | None = None, *, visible_verticals: Iterable[str] | None,
          today: str | None = None) -> dict:
    """KPIs + desglose + tendencia del rango [start, end] (días UTC inclusive).

    `visible_verticals` es OBLIGATORIO (keyword): el set de verticales que el
    viewer puede ver (`hub_metrics_access.metrics_visibility_for` → scopes).
    Se aplica ANTES de cualquier cómputo — usuarios únicos, sesiones y
    visitas se calculan solo con filas visibles. `None` = sin restricción
    (solo para callers internos/tests que no representan a un viewer).

    `filters`: vertical/module/section/user/kind/event/cls (str o lista).
    `cls` default `usage` (el dashboard de uso nunca mezcla diagnóstico).

    - visits: `view_id` distintos (v2, sin reloads) + visitas v1 por evento.
    - unique_users: UNIÓN de usuarios del rango (un usuario activo 2 días = 1).
    - sessions: tramos con pausa ≤ 30 min sobre los timestamps de las filas
      filtradas de TODO el rango — una sesión que cruza la medianoche es una.
    - actions: eventos `kind=action`; outcomes: `kind=outcome` por estado.
    """
    visible = None if visible_verticals is None else {str(v) for v in visible_verticals}
    wanted = _normalized_filters(filters)
    days = _days(start, end)
    visit_ids: set[str] = set()
    visits_v1 = 0
    users: set[str] = set()
    user_seconds: dict[str, list[int]] = {}
    actions = 0
    events_total = 0
    outcomes: Counter = Counter()
    tree: dict[str, dict] = {}
    trend: list[dict] = []
    for day in days:
        data = load_day(day, today=today)
        day_ids: set[str] = set()
        day_v1 = 0
        day_users: set[str] = set()
        day_actions = 0
        day_events = 0
        epoch = _day_epoch(day)
        for row in (data or {}).get("rows", []):
            if not _visible(row["vertical"], visible) or not _matches(row, wanted):
                continue
            count = int(row.get("count") or 0)
            ids = set(row.get("view_ids") or [])
            v1 = int(row.get("visits_v1") or 0)
            events_total += count
            day_events += count
            day_ids |= ids
            day_v1 += v1
            if row["kind"] == activity_catalog.KIND_ACTION:
                actions += count
                day_actions += count
            if row["kind"] == activity_catalog.KIND_OUTCOME:
                outcomes[row.get("status") or "done"] += count
            if row["user"]:
                day_users.add(row["user"])
                user_seconds.setdefault(row["user"], []).extend(epoch + s for s in row.get("ts") or [])
            node_v = tree.setdefault(row["vertical"], {"ids": set(), "v1": 0, "events": 0, "users": set(), "modules": {}})
            node_m = node_v["modules"].setdefault(row["module"], {"ids": set(), "v1": 0, "events": 0, "users": set(), "sections": {}})
            node_s = node_m["sections"].setdefault(row["section"], {"ids": set(), "v1": 0, "events": 0, "users": set()})
            for node in (node_v, node_m, node_s):
                node["ids"] |= ids
                node["v1"] += v1
                node["events"] += count
                if row["user"]:
                    node["users"].add(row["user"])
        visit_ids |= day_ids
        visits_v1 += day_v1
        users |= day_users
        trend.append({"day": day, "visits": len(day_ids) + day_v1, "users": len(day_users),
                      "actions": day_actions, "events": day_events, "sessions": 0})
    # Sesiones sobre TODO el rango (une tramos entre días); cada una se
    # imputa al día en que empieza para la tendencia.
    by_day = {t["day"]: t for t in trend}
    sessions = 0
    for seconds in user_seconds.values():
        for begin, _end in _spans(seconds):
            sessions += 1
            bucket = by_day.get(datetime.fromtimestamp(begin, tz=timezone.utc).strftime("%Y%m%d"))
            if bucket is not None:
                bucket["sessions"] += 1

    def _node(n: dict) -> dict:
        return {"visits": len(n["ids"]) + n["v1"], "events": n["events"], "users": len(n["users"])}

    breakdown = []
    for vertical, nv in sorted(tree.items(), key=lambda kv: -kv[1]["events"]):
        modules = []
        for module, nm in sorted(nv["modules"].items(), key=lambda kv: -kv[1]["events"]):
            sections = [
                {"section": section, **_node(ns)}
                for section, ns in sorted(nm["sections"].items(), key=lambda kv: -kv[1]["events"])
            ]
            modules.append({"module": module, "label": module_label(module) if module else "", **_node(nm), "sections": sections})
        breakdown.append({"vertical": vertical, "label": scope_label(vertical), **_node(nv), "modules": modules})
    return {
        "start": days[0] if days else "",
        "end": days[-1] if days else "",
        "retention_months": RETENTION_MONTHS,
        "kpis": {
            "visits": len(visit_ids) + visits_v1,
            "unique_users": len(users),
            "sessions": sessions,
            "actions": actions,
            "events": events_total,
            "outcomes": dict(outcomes),
        },
        "breakdown": breakdown,
        "trend": trend,
    }


def events(start, end, filters: dict | None = None, *, visible_verticals: Iterable[str] | None,
           page: int = 1, page_size: int = 50) -> dict:
    """Detalle (drill-down) leyendo el JSONL crudo del rango: registros
    normalizados, más nuevo primero, paginados. MISMA regla de visibilidad
    que `query` (antes de filtrar/paginar) y solo `attrs` filtrados — nunca el
    payload crudo v1."""
    visible = None if visible_verticals is None else {str(v) for v in visible_verticals}
    wanted = _normalized_filters(filters)
    items = []
    for day in _days(start, end):
        for rec in _read_records(day):
            if not _visible(rec["vertical"], visible):
                continue
            probe = {**rec, "cls": rec["event_class"]}
            if not _matches(probe, wanted):
                continue
            items.append({
                "id": rec["id"], "v": rec["v"], "ts_utc": rec["ts_utc"], "kind": rec["kind"],
                "event": rec["event"], "label": activity_catalog.label_of(rec["event"]),
                "user": rec["user"], "vertical": rec["vertical"], "area": rec["area"],
                "area_label": area_label(rec["area"]), "module": rec["module"], "section": rec["section"],
                "status": _status(rec), "attrs": rec["attrs"],
            })
    items.sort(key=lambda r: r["ts_utc"], reverse=True)
    page_size = max(1, min(int(page_size or 50), _MAX_PAGE_SIZE))
    total = len(items)
    total_pages = max(1, -(-total // page_size))
    page = max(1, min(int(page or 1), total_pages))
    begin = (page - 1) * page_size
    return {"items": items[begin:begin + page_size], "total": total, "page": page,
            "page_size": page_size, "total_pages": total_pages}
