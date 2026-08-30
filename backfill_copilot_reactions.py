"""
backfill_copilot_reactions.py — HISTORICAL BACKFILL: Copiloto reactions
already stored in FEEDBACK_LOG_DIR → Activity Log (ACTIVITY_LOG_DIR).

Owner ticket "REACCIONES — COMPLETE COPILOT COVERAGE + HISTORY" §5/§6/§8/§9.

WHY THIS EXISTS: Copiloto's reaction producers (module/section/catalog/
catalog_section feedback, ver webapp/routers/feedback.py; Academy feedback,
ver webapp/routers/academy.py; Assistant response feedback, ver
webapp/routers/assistant.py) now emit an Activity Log event going forward
(commits ce82abf + follow-up), but every reaction recorded BEFORE that
change lives ONLY in FEEDBACK_LOG_DIR, which "Reacciones"
(activity_analytics.build_summary) never counts from directly (that store
supplies COMMENT TEXT only, cross-referenced against an existing Activity
event — never a second counting path, ver _read_reaction_comments). This
script is the explicit, one-time bridge: it reads the historically
sufficient YAML types and appends EQUIVALENT synthetic Activity Log events,
preserving the ORIGINAL reaction timestamp/user/sentiment/area/module —
never "now".

SCOPE (deliberately excludes Tree/Wizard outcome ratings): `case_library`
YAML (the only durable trace of a Tree rating) has no user field at all and
overwrites its `rating` in place on every resubmission (no append-only
trail of individual reactions) — historical Tree reactions cannot be
reconstructed with the same fidelity as the other six producers. Forward
coverage for Tree begins with this release (`tree.feedback`, ver
webapp/routers/wizard.py); this script never touches it, and never
fabricates a user or a count for it. See HANDOFF for the full audit.

EXECUTION MODEL (§8): explicit, operator-invoked, dry-run by default —
NEVER runs automatically at application startup. `plan()` is pure/read-only
and fully deterministic for a given pair of directories (sorted glob, no
wall-clock/randomness in its own logic); `apply()` is the only function
that writes. Safe to re-run: every synthetic event carries an opaque,
filename- (or reply_id-, for assistant) derived marker in
`payload.backfill_marker`, and `plan()` skips any source record whose
marker already exists anywhere in ACTIVITY_LOG_DIR — never a timestamp-
equality dedup.

CLI: `python backfill_copilot_reactions.py` (dry-run, prints counts only)
     `python backfill_copilot_reactions.py --apply` (writes)
Also reachable from the admin panel via
`webapp.services.admin_tools.run_copilot_reactions_backfill` (same
`_capture_main` pattern as `analyze_feedback.py`/`analyze_cases.py`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

import config

# type (FEEDBACK_LOG_DIR, feedback_store) -> (event_name, area, module_field).
# `module_field`: the key inside the YAML record that carries the module/
# module-equivalent identity to copy into the synthetic event's `module`
# field (None when that producer has no module dimension — catalog/
# catalog_section, same as their own catalog_view/catalog_regenerate
# siblings). scope is always "copilot" for every type here.
_BACKFILL_TYPES: dict[str, tuple[str, str, str | None]] = {
    "module_content": ("module.feedback", "methodology", "module"),
    "section_content": ("section.feedback", "methodology", "module"),
    "catalog": ("catalog.feedback", "catalog", None),
    "catalog_section": ("catalog_section.feedback", "catalog", None),
    "academy_content": ("academy.feedback", "academy", "academy_page"),
    "assistant_response": ("assistant.feedback", "assistant", "module"),
}


def _opaque_marker(source: str) -> str:
    """SHA-256 truncado, NUNCA reversible — MISMO criterio/longitud que
    `reporting_time_logs_durable._opaque_scope_key`. `source` es la KEY
    ESTABLE del registro origen (nombre de archivo YAML, o `reply_id` para
    Assistant — ver `_candidate_for`) — nunca datos de usuario/token."""
    return hashlib.sha256(f"copilot_reaction_backfill:{source}".encode("utf-8")).hexdigest()[:32]


def _iter_feedback_records(feedback_dir: Path):
    if not feedback_dir.exists():
        return
    for path in sorted(feedback_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        yield path, data


def _existing_markers(activity_dir: Path) -> set[str]:
    """Escanea TODO `activity_YYYYMMDD.jsonl` existente por
    `payload.backfill_marker` — la garantía de re-run seguro (§8/§9): un
    marker ya presente en CUALQUIER día nunca se vuelve a insertar. Fail-
    soft ante un archivo/línea corrupta (nunca bloquea el plan completo)."""
    markers: set[str] = set()
    if not activity_dir.exists():
        return markers
    for path in activity_dir.glob("activity_*.jsonl"):
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
            except json.JSONDecodeError:
                continue
            marker = (raw.get("payload") or {}).get("backfill_marker")
            if marker:
                markers.add(str(marker))
    return markers


def _day_key(ts_utc: str) -> str:
    """YYYYMMDD desde el `ts` ISO 8601 ORIGINAL del registro — nunca la
    fecha de HOY (§5: "Do not use now as event time"). Mismo formato que
    `activity_YYYYMMDD.jsonl` ya usa en todo el resto del Hub."""
    digits = "".join(ch for ch in ts_utc[:10] if ch.isdigit())
    return digits


def _candidate_for(path: Path, data: dict, existing_markers: set[str]) -> dict[str, Any] | None:
    """Un único registro YAML -> el evento sintético a insertar, o `None`
    si el registro no es candidato (tipo no cubierto, dato estructural
    faltante, o ya backfilleado). NUNCA fabrica un campo ausente — un
    registro sin `ts`/`rating` válido se SALTEA (contado como `skipped` por
    el caller), nunca se le inventa "ahora" ni un rating por default."""
    type_ = str(data.get("type") or "")
    mapping = _BACKFILL_TYPES.get(type_)
    if mapping is None:
        return None
    event_name, area, module_field = mapping
    rating = str(data.get("rating") or "")
    if rating not in {"up", "down"}:
        return None
    ts = str(data.get("ts") or "").strip()
    if len(ts) < 10:  # al menos "YYYY-MM-DD" -- fail-soft, nunca "now"
        return None
    # Assistant tiene una key MÁS FUERTE que el nombre de archivo (§9: "for
    # Assistant, prefer its existing stable reply/record id") — el MISMO
    # `reply_id` que `assistant_query` ya usa para correlacionar con
    # "Historia de preguntas" (ver assistant_service.py). Igual de opaco que
    # un hash de filename (uuid4 hex aleatorio), así que se hashea con el
    # MISMO formato — nunca dos formatos de marker distintos conviviendo.
    marker_source = str(data.get("reply_id") or "") if type_ == "assistant_response" else path.name
    if not marker_source:
        marker_source = path.name  # fallback fail-soft si un assistant_response viejo no trae reply_id
    marker = _opaque_marker(marker_source)
    if marker in existing_markers:
        return None
    module_value = str(data.get(module_field) or "") if module_field else ""
    event: dict[str, Any] = {
        "ts_utc": ts,
        "event": event_name,
        "user": str(data.get("user") or ""),
        "name": str(data.get("name") or ""),
        "scope": "copilot",
        "area": area,
        "payload": {
            "feedback_sentiment": "positive" if rating == "up" else "negative",
            "backfill_marker": marker,
            "backfill_source_type": type_,
        },
    }
    if module_value:
        event["module"] = module_value
    return event


def plan(feedback_dir: Path | None = None, activity_dir: Path | None = None) -> list[dict[str, Any]]:
    """Solo LECTURA — nunca escribe. Determinístico para un mismo par de
    directorios (glob ordenado, sin wall-clock/random propio)."""
    feedback_dir = feedback_dir or config.FEEDBACK_LOG_DIR
    activity_dir = activity_dir or config.ACTIVITY_LOG_DIR
    existing = _existing_markers(activity_dir)
    candidates: list[dict[str, Any]] = []
    for path, data in _iter_feedback_records(feedback_dir):
        candidate = _candidate_for(path, data, existing)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def apply(candidates: list[dict[str, Any]], activity_dir: Path | None = None) -> int:
    """Único punto de escritura. Agrupa por día (el `ts` ORIGINAL de cada
    candidato, nunca hoy) y ADJUNTA (nunca reescribe) a
    `activity_YYYYMMDD.jsonl` — MISMO formato de línea que `log_activity`
    ya produce (json compacto, ensure_ascii=False), así que `_read_events`
    los lee exactamente igual que un evento en vivo. Devuelve la cantidad
    insertada."""
    activity_dir = activity_dir or config.ACTIVITY_LOG_DIR
    activity_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for candidate in candidates:
        by_day.setdefault(_day_key(candidate["ts_utc"]), []).append(candidate)
    inserted = 0
    for day, events in sorted(by_day.items()):
        if not day:
            continue
        path = activity_dir / f"activity_{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                inserted += 1
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Ejecuta el backfill (default: dry-run, solo plan).")
    args = parser.parse_args(argv)

    candidates = plan()
    by_type: dict[str, int] = {}
    for candidate in candidates:
        t = candidate["payload"]["backfill_source_type"]
        by_type[t] = by_type.get(t, 0) + 1

    # NUNCA user/token crudo en la salida — solo conteos por tipo (§8: "no
    # raw user/token output in logs"), mismo criterio de seguridad que el
    # resto de este Hub.
    print(f"[PLAN] {len(candidates)} evento(s) candidato(s) para backfill:")
    for t in sorted(_BACKFILL_TYPES):
        print(f"  {t}: {by_type.get(t, 0)}")
    if not candidates:
        print("[PLAN] Nada para insertar (todo ya backfilleado, o no hay registros históricos elegibles).")

    if not args.apply:
        print("[DRY-RUN] Nada escrito. Ejecutá con --apply para confirmar.")
        return 0

    inserted = apply(candidates)
    print(f"[APPLY] {inserted} evento(s) insertado(s) en {config.ACTIVITY_LOG_DIR}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
