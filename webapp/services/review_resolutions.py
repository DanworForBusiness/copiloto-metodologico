"""
review_resolutions.py — Derivación PURA del estado de una revisión gobernada (Iteración II, kind `REVIEW_RESOLUTION`).

Plano SEPARADO del Juicio (D8): este módulo NO entra al motor de `judgment-rules/v4` ni modifica scoring/evidencia técnica. Solo
deriva, EN LECTURA y de forma determinista, el estado de UN subject de revisión a partir de sus eventos append-only
(`governed_decisions.build_review_resolution`/`build_review_revocation`), la huella material vigente (calculada por la capa de
evidencia/ops) y la presencia actual del hallazgo. Sin I/O: no lee archivos/red/Spaces, no usa reloj ni azar (todo por parámetro).

CINCO PLANOS ORTOGONALES (no se colapsan):
  * disposition  — decisión humana verbatim del evento vigente (o None).
  * management   — OPEN | RESOLVED_SETTLED | RESOLVED_PENDING_ACTION | CONTESTED (derivado; CORRECTION_REQUIRED/DEFERRED ⇒ PENDING).
  * evidence     — MATCH | STALE | UNKNOWN | NA (compatibilidad de la huella del evento con la huella vigente del Assessment).
  * review       — CURRENT | DUE | NA (vigencia temporal; DUE no es caducidad).
  * presence     — PRESENT | ABSENT | UNKNOWN (si el Assessment actual aún emite el hallazgo). ABSENT NUNCA implica implementación.

CONCURRENCIA (sin exclusión atómica en Spaces): dos resoluciones vigentes del mismo subject ⇒ `management=CONTESTED`; NUNCA se
elige una "vigente" por ordenamiento temporal. La reconciliación es un evento nuevo cuyo `supersedes_event_ids` referencia TODAS
las decisiones en conflicto (append-only, conserva historial). La bifurcación se explica con `base_event_id` (cabeza vista).
"""
from __future__ import annotations

from datetime import datetime

from webapp.services import governed_decisions as gd

# Compatibilidad de evidencia (reusa el vocabulario de gd para coherencia de presentación).
EVIDENCE_MATCH, EVIDENCE_STALE, EVIDENCE_UNKNOWN, EVIDENCE_NA = gd.EVIDENCE_MATCH, gd.EVIDENCE_STALE, gd.EVIDENCE_UNKNOWN, gd.EVIDENCE_NA
REVIEW_CURRENT, REVIEW_DUE, REVIEW_NA = gd.REVIEW_CURRENT, gd.REVIEW_DUE, gd.REVIEW_NA
PRESENCE_PRESENT, PRESENCE_ABSENT, PRESENCE_UNKNOWN = "PRESENT", "ABSENT", "UNKNOWN"

DECISION_NONE, DECISION_RATIFIED, DECISION_REVOKED = gd.DECISION_NONE, gd.DECISION_RATIFIED, gd.DECISION_REVOKED


def _sorted(events: list[dict]) -> list[dict]:
    return sorted(events, key=lambda e: (gd.parse_utc(e.get("decided_at")), str(e.get("event_id") or "")))


def _live_ratifications(events: list[dict]) -> tuple[list[dict], dict]:
    """Ratificaciones VIGENTES (no revocadas, no superadas) + trazas. Determinista.

    - superada  = su event_id figura en el `supersedes_event_ids` de alguna otra ratificación (reconciliación).
    - revocada  = existe un REVOKED que la referencia (`revokes_event_id`).
    Devuelve (lista de ratificaciones vigentes ordenada, {superseded:set, revoked:set})."""
    ordered = _sorted(events)
    ratified = {e["event_id"]: e for e in ordered if e.get("event") == gd.EVENT_RATIFIED}
    superseded: set[str] = set()
    for e in ordered:
        if e.get("event") == gd.EVENT_RATIFIED:
            for sid in (e.get("supersedes_event_ids") or ()):
                superseded.add(sid)
    revoked: set[str] = set()
    for e in ordered:
        if e.get("event") == gd.EVENT_REVOKED:
            revoked.add(e.get("revokes_event_id"))
    live = [e for eid, e in ratified.items() if eid not in superseded and eid not in revoked]
    live.sort(key=lambda e: (gd.parse_utc(e.get("decided_at")), str(e.get("event_id") or "")))
    return live, {"superseded": superseded, "revoked": revoked}


def _evidence_of(active: dict, current_footprint_digest: str | None) -> str:
    """Compatibilidad de la huella del evento vigente con la huella vigente del Assessment. `None` ⇒ UNKNOWN (nunca MATCH por defecto)."""
    if current_footprint_digest is None:
        return EVIDENCE_UNKNOWN
    return EVIDENCE_MATCH if current_footprint_digest == active["evidence_ref"]["digest"] else EVIDENCE_STALE


def derive_review_status(events: list[dict], *, current_footprint_digest: str | None, now: datetime,
                         presence: str | None = None) -> dict:
    """Estado derivado de UN subject de revisión (eventos ya filtrados por subject). Calculado en lectura, NUNCA se almacena.

    `current_footprint_digest` = huella material vigente (hex) o None si no es verificable (sidecar ausente/desfasado, evidencia
    no disponible) ⇒ evidence=UNKNOWN. `presence` = PRESENT/ABSENT/None (None ⇒ UNKNOWN). `now` debe ser datetime aware (UTC)."""
    for e in events:
        gd.validate_event(e)
        if e.get("kind") != gd.KIND_REVIEW_RESOLUTION:
            raise gd.ValidationError(f"derive_review_status solo acepta kind {gd.KIND_REVIEW_RESOLUTION}")
    if now.tzinfo is None:
        raise gd.ValidationError("now debe ser datetime aware (UTC)")

    presence_norm = presence if presence in (PRESENCE_PRESENT, PRESENCE_ABSENT) else PRESENCE_UNKNOWN
    live, traces = _live_ratifications(events)
    any_ratified = any(e.get("event") == gd.EVENT_RATIFIED for e in events)

    base = {
        "decision": DECISION_NONE, "management": gd.MGMT_OPEN, "disposition": None,
        "evidence": EVIDENCE_NA, "review": REVIEW_NA, "presence": presence_norm,
        "active_event_id": None, "contested_event_ids": [], "effective_review_by": None, "dormant": False,
        "events_considered": len(events),
    }

    if len(live) >= 2:
        # Bifurcación: decisiones vigentes incompatibles. NO se elige una por tiempo.
        contested = sorted(e["event_id"] for e in live)
        base.update({
            "decision": DECISION_RATIFIED, "management": gd.MGMT_CONTESTED, "evidence": EVIDENCE_UNKNOWN,
            "contested_event_ids": contested,
            "contested_detail": [
                {"event_id": e["event_id"], "disposition": e.get("disposition"), "actor": e.get("actor"),
                 "decided_at": e.get("decided_at"), "base_event_id": e.get("base_event_id"),
                 "evidence": _evidence_of(e, current_footprint_digest)}
                for e in live
            ],
        })
        return base

    if len(live) == 1:
        active = live[0]
        evidence = _evidence_of(active, current_footprint_digest)
        review = REVIEW_DUE if now > gd.parse_utc(active["review_by"]) else REVIEW_CURRENT
        base.update({
            "decision": DECISION_RATIFIED, "management": gd.management_state_of(active["disposition"]),
            "disposition": active["disposition"], "evidence": evidence, "review": review,
            "active_event_id": active["event_id"], "effective_review_by": active["review_by"],
            # dormant: resolución vigente cuyo hallazgo YA NO se detecta (historial, no fantasma; no es implementación).
            "dormant": presence_norm == PRESENCE_ABSENT,
            "klass": active.get("klass"),
        })
        return base

    # 0 vigentes
    if any_ratified:                       # hubo ratificación(es), todas revocadas/superadas sin reemplazo vigente ⇒ reabierto
        base["decision"] = DECISION_REVOKED
    return base
