"""
governed_ratification_link.py — ENLACE puro entre las hojas de un perfil y las ratificaciones `governed-decision/v1` vigentes (v4.B / B5.1, SIMULACION).

El motor de Juicio es puro y NO lee decisiones (D8): este módulo está FUERA del motor y solo arma la entrada explícita `ratified_contracts` de
`evaluate_judgment(..., variants={"GOVERNED_CONTRACT_RATIFIED"}, ratified_contracts=...)`. Solo LECTURA: no escribe eventos, no toca Assessments ni
canonical-evidence/v4, no persiste. El historial se inyecta (`load_events`) para que la lógica sea determinista y testeable; por defecto lee el registro B2.

Una hoja se enlaza ÚNICAMENTE si cumple TODO:
  · hoja `REMOTE_IMPORTED` con `contract_class == EXTERNAL_CONTRACT_UNDETERMINED` y exactamente una `remote_entity` (identidad inequívoca);
  · existe una ratificación vigente del MISMO (client, remote_connection, remote_entity) — no revocada;
  · el digest per-CDS actual de la evidencia MATCH-ea el de la ratificación (STALE / UNKNOWN / evidencia contradictoria entre consumidores NO enlazan);
  · `REVIEW_DUE` enlaza (D7: revisión vencida no es caducidad) pero se informa.
El motor vuelve a validar cada ratificación contra el perfil (elegibilidad de la clase, identidad, exposición decision-crítica).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from webapp.services import governed_decisions as gd

UNDETERMINED_CLASS = "EXTERNAL_CONTRACT_UNDETERMINED"

OUT_LINKED, OUT_NOT_LINKED = "LINKED", "NOT_LINKED"


def _report(leaf: str, outcome: str, reason: str | None, **extra: Any) -> dict:
    out = {"leaf": leaf, "outcome": outcome, "reason": reason}
    out.update(extra)
    return out


def link_ratifications(profile: Mapping[str, Any], client: str, *, now: datetime,
                       load_events: Callable[[str, str], list[dict]] | None = None,
                       evidence_digests: Mapping[str, str | None] | None = None) -> dict:
    """Devuelve `{"ratified_contracts": [...], "report": [...]}`.

    `load_events(client, technical_id)` -> eventos del contrato (por defecto `governed_decisions_store.load_events`).
    `evidence_digests` (opcional): `{technical_id: digest | None}` calculado por el cross-scan del cliente (inventario B4); `None` = evidencia
    contradictoria/no verificable → no enlaza. Si falta la clave, se usa el digest per-CDS de la hoja de ESTE perfil."""
    if load_events is None:
        from webapp.services import governed_decisions_store as _store
        load_events = _store.load_events
    ce = (profile or {}).get("canonical_evidence") or {}
    leaves = (ce.get("source_contract_classes") or {}).get("leaves") or []
    linked: list[dict] = []
    report: list[dict] = []
    for lf in sorted((l for l in leaves if isinstance(l, dict)), key=lambda l: str(l.get("leaf"))):
        name = str(lf.get("leaf"))
        if lf.get("leaf_kind") != gd.LEAF_KIND_REMOTE_IMPORTED or lf.get("contract_class") != UNDETERMINED_CLASS:
            continue                                                  # solo la clase indeterminada es candidata (nunca cruda/gobernada/desconocida/plataforma)
        try:
            identity = gd.cds_identity(lf)
            current = gd.cds_contract_digest(lf)
        except gd.ValidationError as exc:
            report.append(_report(name, OUT_NOT_LINKED, "IDENTITY_NOT_UNEQUIVOCAL", detail=str(exc)))
            continue
        tid = gd.cds_technical_id(identity)
        if evidence_digests is not None and tid in evidence_digests:
            current = evidence_digests[tid]                           # None = contradictoria / no verificable
        events = [e for e in load_events(client, tid) if isinstance(e, dict)]
        foreign = [e for e in events if (e.get("subject") or {}).get("client") != client or (e.get("subject") or {}).get("technical_id") != tid]
        if foreign:
            report.append(_report(name, OUT_NOT_LINKED, "FOREIGN_SUBJECT_EVENTS", technical_id=tid))
            continue
        st = gd.derive_status(events, current, now)
        if st["decision"] == gd.DECISION_NONE:
            report.append(_report(name, OUT_NOT_LINKED, "NO_RATIFICATION", technical_id=tid))
        elif st["decision"] == gd.DECISION_REVOKED:
            report.append(_report(name, OUT_NOT_LINKED, "REVOKED", technical_id=tid))
        elif st["evidence"] == gd.EVIDENCE_STALE:
            report.append(_report(name, OUT_NOT_LINKED, "EVIDENCE_STALE", technical_id=tid))
        elif st["evidence"] != gd.EVIDENCE_MATCH:
            report.append(_report(name, OUT_NOT_LINKED, "EVIDENCE_UNKNOWN", technical_id=tid))
        else:
            event = next(e for e in events if e["event_id"] == st["ratified_event_id"])
            linked.append({"leaf": name, "identity": identity, "technical_id": tid, "event_id": event["event_id"], "digest": event["evidence_ref"]["digest"],
                           "evidence_ref": dict(event["evidence_ref"]), "decided_at": event["decided_at"]})
            report.append(_report(name, OUT_LINKED, None, technical_id=tid, event_id=event["event_id"], review=st["review"]))
    return {"ratified_contracts": linked, "report": report}
