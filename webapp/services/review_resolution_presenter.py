"""
review_resolution_presenter.py — Proyección de RESOLUCIONES de revisión sobre el view-model del informe (Iteración III).

Lee el registro gobernado (``review_resolution_ops`` → store append-only) + el sidecar ``review-evidence/v1`` ligado al perfil, y
adjunta a cada ocurrencia de revisión su estado de resolución (5 planos) para que la plantilla lo muestre con los MISMOS badges y
secciones del Hub. NO modifica el perfil, el scoring ni el Juicio (D8): es solo lectura + proyección. Sin huella verificable ⇒
evidencia ``UNKNOWN`` (nunca MATCH por defecto). Distingue revisiones metodológicas (MODEL_REVIEW) de oportunidades de
optimización (CONTEXTUAL_OPPORTUNITY, p. ej. Transformation Flow).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.catalog.modeling_assessment import review_evidence as rev
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store_mod
from webapp.services import review_resolutions as rr

# Etiquetas y tonos (fuente única del Hub: plena/buena/parcial/neutra).
KLASS_LABELS = {gd.KLASS_MODEL_REVIEW: "Revisión metodológica", gd.KLASS_CONTEXTUAL_OPPORTUNITY: "Oportunidad de optimización"}

DISPOSITION_LABELS = {
    gd.DISP_VALIDATED_DESIGN: "Diseño validado", gd.DISP_NOT_APPLICABLE: "No aplica",
    gd.DISP_CORRECTION_REQUIRED: "Requiere corrección", gd.DISP_DEFERRED: "Pospuesta",
    gd.DISP_ACCEPTED: "Aceptada", gd.DISP_DISCARDED: "Descartada",
}
MGMT_LABELS = {gd.MGMT_OPEN: "Sin resolver", gd.MGMT_RESOLVED_SETTLED: "Resuelta",
               gd.MGMT_RESOLVED_PENDING_ACTION: "Pendiente de remediación", gd.MGMT_CONTESTED: "En conflicto"}
MGMT_TONES = {gd.MGMT_OPEN: "neutra", gd.MGMT_RESOLVED_SETTLED: "plena",
              gd.MGMT_RESOLVED_PENDING_ACTION: "parcial", gd.MGMT_CONTESTED: "parcial"}
EVIDENCE_LABELS = {rr.EVIDENCE_MATCH: "Evidencia compatible", rr.EVIDENCE_STALE: "Evidencia cambiada",
                   rr.EVIDENCE_UNKNOWN: "Evidencia no verificable", rr.EVIDENCE_NA: "—"}
EVIDENCE_TONES = {rr.EVIDENCE_MATCH: "buena", rr.EVIDENCE_STALE: "parcial", rr.EVIDENCE_UNKNOWN: "neutra", rr.EVIDENCE_NA: "neutra"}
REVIEW_LABELS = {rr.REVIEW_CURRENT: "Vigente", rr.REVIEW_DUE: "Revisión vencida", rr.REVIEW_NA: "—"}
REVIEW_TONES = {rr.REVIEW_CURRENT: "buena", rr.REVIEW_DUE: "parcial", rr.REVIEW_NA: "neutra"}


# Estado de LECTURA (una sola etiqueta principal, derivada sin perder los planos). Una decisión registrada NO implica revisión cerrada:
# solo «Cerrada» cuando la disposición es de cierre, la evidencia coincide y la vigencia no venció. Prioridad (igual criterio que
# `governed_decisions.PRINCIPAL_*`): conflicto > evidencia desactualizada > evidencia no verificable > remediación/postergada > vencida > cerrada.
STATE_LABELS = {
    "OPEN": ("Sin resolver", "neutra"), "REOPENED": ("Reabierta", "neutra"), "CONTESTED": ("En conflicto", "parcial"),
    "EVIDENCE_STALE": ("Evidencia desactualizada", "parcial"), "EVIDENCE_UNKNOWN": ("Evidencia no verificable", "neutra"),
    "PENDING_REMEDIATION": ("Pendiente de remediación", "parcial"), "DEFERRED": ("Postergada", "parcial"),
    "REVIEW_DUE": ("Revisión vencida", "parcial"), "CLOSED": ("Cerrada", "plena"),
}
STATE_NOTES = {
    "OPEN": "Todavía no hay una decisión registrada para esta ocurrencia.",
    "REOPENED": "La decisión anterior fue revocada; la ocurrencia volvió a quedar sin resolver.",
    "CONTESTED": "Hay decisiones vigentes incompatibles. Ninguna prevalece hasta reconciliarlas.",
    "EVIDENCE_STALE": "La evidencia cambió desde que se registró la decisión: no se considera cerrada hasta revalidarla.",
    "EVIDENCE_UNKNOWN": "No se puede verificar que la evidencia actual sea la misma que se evaluó al decidir.",
    "PENDING_REMEDIATION": "La decisión exige una corrección en el modelo: la revisión sigue abierta hasta remediarla.",
    "DEFERRED": "La decisión se postergó: la revisión sigue abierta.",
    "REVIEW_DUE": "La decisión venció su fecha de revisión y debe revalidarse.",
    "CLOSED": "La revisión está cerrada con evidencia compatible y decisión vigente.",
}


def _state_key(st: dict) -> str:
    mgmt = st["management"]
    if mgmt == gd.MGMT_CONTESTED:
        return "CONTESTED"
    if mgmt == gd.MGMT_OPEN:
        return "REOPENED" if st.get("decision") == gd.DECISION_REVOKED else "OPEN"
    if st["evidence"] == rr.EVIDENCE_STALE:
        return "EVIDENCE_STALE"
    if st["evidence"] == rr.EVIDENCE_UNKNOWN:
        return "EVIDENCE_UNKNOWN"
    if st.get("disposition") == gd.DISP_CORRECTION_REQUIRED:
        return "PENDING_REMEDIATION"
    if st.get("disposition") == gd.DISP_DEFERRED:
        return "DEFERRED"
    if st["review"] == rr.REVIEW_DUE:
        return "REVIEW_DUE"
    return "CLOSED"


def _fmt_ts(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return gd.parse_utc(value).strftime("%d/%m/%Y %H:%M UTC")
    except Exception:  # noqa: BLE001 — presentación: si no parsea se muestra tal cual
        return value


def _fmt_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return gd.parse_utc(value).strftime("%d/%m/%Y")
    except Exception:  # noqa: BLE001
        return value


# Lectura CONTEXTUAL de las disposiciones por regla (misma familia MODEL_REVIEW y mismos códigos gobernados; cambia solo el texto).
RULE_DISPOSITION_LABELS = {
    "DIRECT_RAW_TABLE_ACCESS": {
        gd.DISP_VALIDATED_DESIGN: "Diseño validado · excepción metodológica justificada",
        gd.DISP_CORRECTION_REQUIRED: "Requiere corrección · desviación pendiente de remediación",
        gd.DISP_NOT_APPLICABLE: "No aplica · observación no aplicable al caso",
        gd.DISP_DEFERRED: "Pospuesta · decisión diferida",
    },
}
RULE_DISPOSITION_HINTS = {
    "DIRECT_RAW_TABLE_ACCESS": {
        gd.DISP_VALIDATED_DESIGN: "Cierra la observación: el acceso directo a la tabla física es una excepción deliberada y justificada. La observación sigue visible.",
        gd.DISP_CORRECTION_REQUIRED: "El acceso es una desviación: queda pendiente de remediación.",
        gd.DISP_NOT_APPLICABLE: "La observación no aplica a este caso. La observación sigue visible.",
        gd.DISP_DEFERRED: "La decisión se difiere: la observación queda abierta.",
    },
}
# Sustantivo del caso en el diálogo («Resolver observación» / «Resolver revisión»), según el área del informe.
NOUN_BY_SECTION = {"observations": "observación", "review_required": "revisión"}


def _disp_label(disposition, rule=None):
    return (RULE_DISPOSITION_LABELS.get(rule) or {}).get(disposition) or DISPOSITION_LABELS.get(disposition)


def _event_view(e: dict, rule=None) -> dict:
    return {"event": e.get("event"), "event_id": e.get("event_id"), "disposition": e.get("disposition"),
            "disposition_label": _disp_label(e.get("disposition"), rule), "actor": e.get("actor"),
            "decided_at": _fmt_ts(e.get("decided_at")), "rationale": e.get("rationale")}


def _resolution_view(events: list[dict], st: dict, ident: dict, tid: str, is_owner: bool, noun: str = "revisión") -> dict:
    mgmt = st["management"]
    ordered = sorted(events, key=lambda e: (e.get("decided_at") or "", e.get("event_id") or ""))
    last = ordered[-1] if ordered else None
    klass = ident["klass"]
    by_id = {e.get("event_id"): e for e in events}
    active = by_id.get(st.get("active_event_id"))
    state = _state_key(st)
    state_label, state_tone = STATE_LABELS[state]
    rule = ident.get("rule")
    contested = [_event_view(by_id[i], rule) for i in (st.get("contested_event_ids") or []) if i in by_id]
    return {
        "state": state, "state_label": state_label, "state_tone": state_tone, "state_note": STATE_NOTES[state],
        "closed": state == "CLOSED",
        "decision": _event_view(active, rule) if active else None,
        "noun": noun, "disposition_hints": RULE_DISPOSITION_HINTS.get(rule) or {},
        "review_by": _fmt_date(st.get("effective_review_by")),
        "contested_decisions": contested,
        "technical_id": tid, "klass": klass, "klass_label": KLASS_LABELS.get(klass, klass),
        "management": mgmt, "management_label": MGMT_LABELS[mgmt], "management_tone": MGMT_TONES[mgmt],
        "evidence": st["evidence"], "evidence_label": EVIDENCE_LABELS[st["evidence"]], "evidence_tone": EVIDENCE_TONES[st["evidence"]],
        "review": st["review"], "review_label": REVIEW_LABELS[st["review"]], "review_tone": REVIEW_TONES[st["review"]],
        "disposition": st.get("disposition"), "disposition_label": _disp_label(st.get("disposition"), rule),
        "contested": mgmt == gd.MGMT_CONTESTED, "contested_event_ids": st.get("contested_event_ids") or [],
        "active_event_id": st.get("active_event_id"), "dormant": bool(st.get("dormant")),
        "allowed_dispositions": [(d, _disp_label(d, rule)) for d in sorted(gd.DISPOSITIONS_BY_KLASS[klass])],
        "can_resolve": is_owner,
        "has_events": bool(ordered),
        "last": None if last is None else {"event": last["event"], "disposition": last.get("disposition"),
                                           "actor": last.get("actor"), "decided_at": last.get("decided_at"), "rationale": last.get("rationale")},
        "history": [{"event": e["event"], "disposition": e.get("disposition"), "actor": e.get("actor"),
                     "decided_at": e.get("decided_at"), "rationale": e.get("rationale")} for e in reversed(ordered)],
    }


def enrich(view: dict, status: Any, *, is_owner: bool, now: datetime | None = None, store=store_mod) -> dict:
    """Adjunta ``resolution`` a cada ocurrencia RESOLUBLE de ``view['review_required']`` y ``view['observations']`` y marca klass del grupo. Mutante;
    devuelve el mismo view. Falla-suave por ocurrencia: un error de lectura nunca rompe el informe (queda sin resolución)."""
    doc = getattr(status, "profile", None)
    if not doc or not isinstance(view.get("review_required"), list):
        return view
    now = now or datetime.now(timezone.utc)
    sidecar = getattr(status, "review_evidence", None)
    root_am = (sidecar or {}).get("root_am") or getattr(status, "terminal_am", None)
    client = getattr(status, "client", None)
    # (regla, occurrence_id) ⇒ ocurrencia cruda del perfil, en «Casos a revisar» y en «Observaciones» (la observación conserva su tipo).
    raw_by_id = {(g["id"], o["occurrence_id"]): o for key in NOUN_BY_SECTION for g in doc.get(key) or [] for o in g.get("occurrences") or []}
    any_resoluble = False
    for section, noun in NOUN_BY_SECTION.items():
        for vg in view.get(section) or []:
            klass = rev.KLASS_BY_RULE.get(vg.get("id"))
            vg["klass"] = klass
            # el badge de familia en la cabecera del agrupador es de «Casos a revisar»; una observación no se rotula como revisión
            vg["klass_label"] = KLASS_LABELS.get(klass) if section == "review_required" else None
            vg["resoluble"] = klass is not None
            if klass is None:
                continue
            any_resoluble = True
            for vo in vg.get("occurrences") or []:
                o = raw_by_id.get((vg.get("id"), vo.get("id")))
                if not o:
                    continue
                try:
                    ident = rev.review_identity(vg["id"], root_am, o.get("values") or {}, o.get("affected_artifacts") or [])
                    if ident is None:
                        continue
                    tid = rev.review_technical_id(ident)
                    events = store.load_events(client, tid)
                    digest = rev.current_footprint_digest(tid, sidecar, doc)
                    st = rr.derive_review_status(events, current_footprint_digest=digest, now=now, presence="PRESENT")
                    vo["resolution"] = _resolution_view(events, st, ident, tid, is_owner, noun)
                except Exception:  # noqa: BLE001 — proyección opcional: nunca rompe el informe
                    continue
    view["resolutions_enabled"] = any_resoluble
    view["is_owner"] = is_owner
    return view
