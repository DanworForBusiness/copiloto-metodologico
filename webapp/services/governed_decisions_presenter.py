"""
governed_decisions_presenter.py — Presentación PURA de las 3 dimensiones de estado de un contrato gobernado (v4.B / B4).

Mapea los valores canónicos de `derive_status` (decisión/evidencia/revisión) a {label humano, tono}. El TONO reusa la taxonomía
canónica del Hub (`webapp.services.status_tone`: plena=verde/positivo, buena=ámbar, parcial=naranja, neutra=gris). Las tres
dimensiones son INDEPENDIENTES y se muestran por separado — NUNCA se combinan en un único semáforo (una ratificación no implica
cumplimiento metodológico/Judgment). Sin estado, sin I/O: solo lectura de valores ya derivados.
"""
from __future__ import annotations

from urllib.parse import urlencode

from webapp.services import governed_decisions as gd
from webapp.services.status_tone import ACCEPTABLE, ATTENTION, NEUTRAL, POSITIVE

# decisión: RATIFIED (positivo) · REVOKED (atención) · NONE (neutro)
_DECISION = {
    gd.DECISION_RATIFIED: ("Ratificada", POSITIVE),
    gd.DECISION_REVOKED: ("Revocada", ATTENTION),
    gd.DECISION_NONE: ("Sin decisión", NEUTRAL),
}
# evidencia: MATCH (positivo) · STALE (atención) · UNKNOWN/NA (neutro). STALE ≠ caducidad; UNKNOWN = no verificable ahora.
_EVIDENCE = {
    gd.EVIDENCE_MATCH: ("Coincide", POSITIVE),
    gd.EVIDENCE_STALE: ("Desactualizada", ATTENTION),
    gd.EVIDENCE_UNKNOWN: ("No verificable", NEUTRAL),
    gd.EVIDENCE_NA: ("N/A", NEUTRAL),
}
# revisión: CURRENT (positivo) · DUE (aceptable/ámbar — NO es caducidad, la ratificación sigue vigente) · NA (neutro)
_REVIEW = {
    gd.REVIEW_CURRENT: ("Al día", POSITIVE),
    gd.REVIEW_DUE: ("Revisión vencida", ACCEPTABLE),
    gd.REVIEW_NA: ("N/A", NEUTRAL),
}


def _chip(table: dict, value: str) -> dict:
    label, tone = table.get(value, (str(value or "—"), NEUTRAL))
    return {"value": value, "label": label, "tone": tone}


def dimension_chips(status: dict) -> dict:
    """{decision, evidence, review} → cada uno {value, label, tone}. Dimensiones independientes (no se combinan)."""
    return {
        "decision": _chip(_DECISION, status.get("decision")),
        "evidence": _chip(_EVIDENCE, status.get("evidence")),
        "review": _chip(_REVIEW, status.get("review")),
    }


def governed_contract_links(profile: dict | None, client: str, pack: str = "") -> list[dict]:
    """Navegación contextual (B4, Parte C): a partir de las hojas `REMOTE_IMPORTED` del perfil YA persistido de un Assessment,
    devuelve las CDS/API de origen con IDENTIDAD INEQUÍVOCA (exactamente una `remote_entity`) y su enlace a la ficha del contrato.
    SOLO LECTURA de `canonical_evidence.source_contract_classes.leaves`; NO recalcula ni toca el Assessment ni su Judgment.
    Fail-soft: una hoja sin identidad inequívoca se OMITE (no se ofrece enlace ambiguo). Dedupe por identidad."""
    if not isinstance(profile, dict) or not str(client or "").strip():
        return []
    leaves = (((profile.get("canonical_evidence") or {}).get("source_contract_classes") or {}).get("leaves") or [])
    out: dict[str, dict] = {}
    for lf in leaves:
        if not isinstance(lf, dict) or lf.get("leaf_kind") != gd.LEAF_KIND_REMOTE_IMPORTED:
            continue
        try:
            ident = gd.cds_identity(lf)                             # exige identidad inequívoca; omite lo ambiguo (fail-soft)
        except gd.ValidationError:
            continue
        tid = gd.cds_technical_id(ident)
        if tid in out:
            continue
        q = {"client": client, "remote_entity": ident["remote_entity"]}
        if ident.get("remote_connection"):
            q["remote_connection"] = ident["remote_connection"]
        if pack:
            q["pack"] = pack
        out[tid] = {"remote_connection": ident.get("remote_connection"), "remote_entity": ident["remote_entity"],
                    "url": "/governed-contracts/detail?" + urlencode(q)}
    return sorted(out.values(), key=lambda r: ((r["remote_connection"] or ""), r["remote_entity"]))
