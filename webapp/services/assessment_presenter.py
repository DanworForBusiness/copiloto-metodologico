"""
assessment_presenter.py — vista estructurada (view-model) del assessment para Jinja.

Toma el JSON CANÓNICO del assessment (``modeling_assessment.assessment/v1``) y arma los datos que
consume ``partials/catalog_assessment_report.html`` / ``catalog_assessment_tile.html``. NO emite HTML,
NO relee ni reinterpreta hechos, NO decide reglas: solo ordena el documento en secciones y traduce
ENUMS a español con la API PÚBLICA de presentación del paquete (``modeling_assessment.presentation``), la
misma fuente de vocabulario y formato que usa el Markdown, de modo que UI y exportación no puedan divergir.
Solo se importan nombres públicos del paquete (invariante R-I2b).

Prohibido aquí (y verificado por tests): renderizar Markdown, scrapear el Markdown exportado, importar símbolos
privados del paquete o duplicar lógica de negocio del assessment. El texto de cada ocurrencia sale de
``presentation.occurrence_text(..., plain=True)`` (el MISMO formateador de una línea por ocurrencia que usa la
exportación, sin delimitadores de código inline): es TEXTO PLANO que Jinja escapa (autoescape), nunca HTML
marcado como seguro.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from tools.catalog.modeling_assessment import presentation as P

from webapp.services import assessment_service as svc

# ── etiquetas de ESTADO DE VIGENCIA (producto del Catálogo, no del motor) ──
STATE_LABELS = {
    svc.CURRENT: "Assessment vigente",
    svc.STALE: "Assessment desactualizado",
    svc.NOT_YET_ASSESSED: "Sin assessment",
    svc.NOT_APPLICABLE: "No aplica",
    svc.FAILED: "No se pudo generar",
    svc.INCOMPATIBLE_SCHEMA: "Informe no compatible",
}
STATE_NOTES = {
    svc.STALE: "El modelo cambió desde la última evaluación.",
}
# Acción de la TARJETA (tile) por estado: (clave, método, rótulo). En STALE la tarjeta ofrece "Ver informe";
# la acción de regenerar vive dentro del informe (REPORT_ACTIONS).
ACTIONS = {
    svc.CURRENT: ("view", "get", "Ver informe"),
    svc.STALE: ("view", "get", "Ver informe"),
    svc.NOT_YET_ASSESSED: ("generate", "post", "Generar informe"),
    svc.FAILED: ("retry", "post", "Reintentar"),
    svc.INCOMPATIBLE_SCHEMA: ("regenerate", "post", "Regenerar assessment"),
}
# Acción de generación DENTRO del informe: (clave, rótulo). CURRENT no ofrece ninguna (nada que regenerar).
REPORT_ACTIONS = {
    svc.STALE: ("regenerate", "Regenerar assessment"),
    svc.NOT_YET_ASSESSED: ("generate", "Generar informe"),
    svc.FAILED: ("retry", "Reintentar"),
    svc.INCOMPATIBLE_SCHEMA: ("regenerate", "Regenerar assessment"),
}
MAX_FOCUS_NAMES = 200


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return P.plural(n, singular, plural)


# ═══════════════════════════ URLs ═══════════════════════════
def assessment_url(entry: Any, pack: str, **extra: str) -> str:
    q = {"flow": entry.entry_id, "pack": pack, **extra}
    return "/catalog/flow/assessment?" + urlencode(q)


def flow_url(entry: Any, pack: str, focus: list[str] | None = None) -> str:
    """Detalle del flujo (con foco opcional en el linaje) — contrato ``/catalog/flow?flow=<id>&focus=A,B``."""
    qs = urlencode({"cliente": entry.cliente, "dominio": entry.dominio, "flow": entry.entry_id, "pack": pack})
    names = [n for n in dict.fromkeys(focus or []) if n][:MAX_FOCUS_NAMES]
    return f"/catalog/flow?{qs}" + (f"&focus={quote(','.join(names), safe=',')}" if names else "")


def parse_focus(raw: str) -> list[str]:
    """``focus=A,B`` → lista de nombres técnicos únicos y acotados (cualquier otra cosa se descarta)."""
    out: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if name and len(name) <= 200 and name not in out:
            out.append(name)
        if len(out) >= MAX_FOCUS_NAMES:
            break
    return out


# ═══════════════════════════ tile ═══════════════════════════
def tile_view(status: svc.AssessmentStatus, entry: Any, pack: str) -> dict:
    action = ACTIONS.get(status.state)
    summary = status.summary
    view: dict[str, Any] = {
        "state": status.state,
        "label": STATE_LABELS[status.state],
        "reason": status.reason if status.state in (svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA) else None,
        "url": assessment_url(entry, pack),
        "tile_url": assessment_url(entry, pack, frag="tile"),
        "entry_id": entry.entry_id,
        "action": None,
        "coverage": None, "confidence": None, "reviews": None, "reviews_title": None, "observations": None,
    }
    if action:
        view["action"] = {"key": action[0], "method": action[1], "label": action[2]}
    if summary:
        view["coverage"] = P.percent_text(summary["strict"])
        view["confidence"] = P.confidence_level_text(summary["confidence"]).capitalize()
        n = summary["review_occurrences"]
        view["reviews"] = _plural(n, "revisión", "revisiones") if n else "Sin revisiones"
        view["reviews_title"] = (f"{_plural(n, 'ocurrencia')} en {_plural(summary['review_groups'], 'grupo')} de revisión requerida"
                                 if n else "Ningún grupo de revisión requerida")
        if summary["observation_occurrences"]:
            view["observations"] = _plural(summary["observation_occurrences"], "observación", "observaciones")
    return view


def tiles_for(entries: list[Any], pack: str) -> dict[str, dict]:
    """``entry_id`` → vista del tile. Fail-soft: si el assessment falla, el Catálogo sigue mostrando sus tiles
    (sin el bloque); jamás rompe el listado legacy."""
    try:
        statuses = svc.statuses_for_entries(entries)
    except Exception:                                   # noqa: BLE001 — el listado del Catálogo no debe depender del assessment
        svc.log.exception("assessment: no se pudo calcular el estado de los tiles")
        return {}
    return {e.entry_id: tile_view(statuses[e.entry_id], e, pack) for e in entries if e.entry_id in statuses}


# ═══════════════════════════ informe ═══════════════════════════
def _focus_link(entry: Any, pack: str, names: list[str]) -> str | None:
    names = [n for n in dict.fromkeys(names) if n and P.is_artifact_name(n)]
    return flow_url(entry, pack, names) if names else None


def _group_view(g: dict, entry: Any, pack: str) -> dict:
    occurrences = []
    for o in g["occurrences"]:
        try:
            text = P.occurrence_text(g["id"], o, plain=True)
        except Exception:                               # noqa: BLE001 — formateo defensivo: nunca rompe el informe
            text = ", ".join(f"{k}={o['values'][k]}" for k in sorted(o.get("values") or {}))
        occurrences.append({"id": o["occurrence_id"], "text": text, "artifacts": list(o["affected_artifacts"]),
                            "lineage_url": _focus_link(entry, pack, o["affected_artifacts"])})
    state = g.get("evidence_state")
    evidence_limit = None
    if state and state["assessability"] != "ASSESSED":
        evidence_limit = (f"los insumos de la regla están {P.assessability_plural_text(state['assessability'])} "
                          f"({state['reason']['reason_summary']}); pueden existir más ocurrencias fuera de la evidencia que pudo evaluarse.")
    return {
        "dom_id": f"asr-g-{g['id']}", "id": g["id"], "title": g["title"], "kind": g["kind"], "kind_text": P.finding_kind_text(g["kind"]),
        "area_title": P.area_title(g["area"]), "explanation": g["explanation"], "why_shown": g["why_shown"],
        "question": (g.get("context_required") or {}).get("question") if g["kind"] != "DESCRIPTIVE" else None,
        "legitimate_reasons": g.get("legitimate_reasons") or [], "interpretation_limits": g.get("interpretation_limits") or [],
        "occurrence_count": g["occurrence_count"], "occurrence_unit": g["occurrence_unit"],
        "artifacts": list(g["affected_artifacts"]), "evidence_limit": evidence_limit,
        "lineage_url": _focus_link(entry, pack, g["affected_artifacts"]),
        "occurrences": occurrences,
    }


def _area_view(a: dict, groups_by_id: dict[str, dict], rules_by_area: dict[str, list[dict]]) -> dict:
    rs = a["required_surfaces"]
    highlights = []
    for h in a["highlights"]:
        highlights.append({"label": h["label"], "text": P.value_text(h["value"]),
                           "breakdown": [{"name": b["name"], "count": b["count"]} for b in (h.get("breakdown") or [])]})
    return {
        "id": a["id"], "dom_id": f"asr-a-{a['id']}", "title": P.area_title(a["id"]), "macro": P.macro_dimension_title(a["macro_dimension"]),
        "state": a["assessability"], "state_text": P.assessability_text(a["assessability"]),
        "reason": (f"{a['reason']['reason_summary']} ({a['reason']['reason_code']})" if a["reason"] else None),
        "surfaces": (f"{_plural(rs['assessed'], 'evaluada')}, {_plural(rs['partially_assessed'], 'parcialmente evaluada')}, "
                     f"{_plural(rs['not_assessed'], 'no evaluada')}, {rs['not_applicable']} no aplican"),
        "highlights": highlights,
        "findings": [{"title": groups_by_id[gid]["title"], "dom_id": f"asr-g-{gid}", "count": groups_by_id[gid]["occurrence_count"],
                      "kind_text": P.finding_kind_text(groups_by_id[gid]["kind"])} for gid in a["finding_groups"]],
        "rules": [{"title": r["title"], "result": r["result"], "result_text": P.rule_result_text(r["result"]),
                   "reason": r["reason"]["reason_summary"] if r["reason"] else None} for r in rules_by_area.get(a["id"], [])],
        "limitations_not_observable": bool(a["limitations_not_observable"]),
    }


def report_view(status: svc.AssessmentStatus, entry: Any, pack: str) -> dict:
    """View-model del informe. ``status.profile`` es el JSON canónico validado; nada se deriva de otra fuente."""
    doc = status.profile
    view: dict[str, Any] = {
        "state": status.state, "state_label": STATE_LABELS[status.state], "state_note": STATE_NOTES.get(status.state),
        "reason": status.reason, "error_code": status.error_code,
        "client": status.client, "entry_id": entry.entry_id, "dominio": entry.dominio,
        "terminal_am": status.terminal_am, "generated_at": status.generated_at,
        "action": ({"key": REPORT_ACTIONS[status.state][0], "label": REPORT_ACTIONS[status.state][1]} if status.state in REPORT_ACTIONS else None),
        "previous_profile_retained": status.previous_profile_retained,
        "report_url": assessment_url(entry, pack), "flow_url": flow_url(entry, pack),
        "json_url": "/catalog/flow/assessment.json?" + urlencode({"flow": entry.entry_id}),
        "md_url": "/catalog/flow/assessment.md?" + urlencode({"flow": entry.entry_id}),
        "has_profile": doc is not None,
    }
    if doc is None:
        return view
    scope, summary = doc["scope"], doc["summary"]
    cov, conf, ctx = summary["coverage"], summary["confidence"], summary["context_required"]
    rs = cov["required_surfaces"]
    groups_by_id = {g["id"]: g for g in (*doc["observations"], *doc["review_required"], *doc["descriptive_signals"])}
    rules_by_area: dict[str, list[dict]] = {}
    for r in doc["rule_results"]:
        rules_by_area.setdefault(r["area"], []).append(r)
    lim = conf["main_limitation"]
    view.update({
        "execution_text": P.execution_state_text(doc["assessment_state"]),
        "scope_text": f"{_plural(scope['closure_size'], 'artefacto')} en el cierre semántico del modelo analítico terminal",
        "schema_version": doc["schema_version"],
        "versions": [("Señales", doc["versions"]["signals"]), ("Evaluabilidad", doc["versions"]["assessability"]),
                     ("Confianza", doc["versions"]["confidence"])],
        "summary": {
            "coverage_strict": P.percent_text(cov["strict"]), "coverage_inclusive": P.percent_text(cov["inclusive"]), "coverage_meaning": cov["meaning"],
            "surfaces": (f"{rs['applicable']} aplicables — {_plural(rs['assessed'], 'evaluada')}, "
                         f"{_plural(rs['partially_assessed'], 'parcialmente evaluada')}, {_plural(rs['not_assessed'], 'no evaluada')} "
                         f"({rs['not_applicable']} que no aplican quedan excluidas)"),
            "confidence": P.confidence_level_text(conf["level"]), "confidence_level": conf["level"], "confidence_meaning": conf["meaning"],
            "confidence_limitation": lim["summary"] if lim else None,
            "context_open": _plural(ctx["open_questions"], "pregunta de contexto abierta", "preguntas de contexto abiertas"),
            "context_questions": [{"text": q["question"], "occurrences": q["occurrence_count"]} for q in ctx["questions"]],
            "no_evidence_dimensions": [P.macro_dimension_title(m["id"]) for m in summary["macro_dimensions"] if m["state"] == "COMPLETELY_NOT_ASSESSED"],
            "observation_groups": len(doc["observations"]), "review_groups": len(doc["review_required"]),
            "observation_occurrences": sum(g["occurrence_count"] for g in doc["observations"]),
            "review_occurrences": sum(g["occurrence_count"] for g in doc["review_required"]),
        },
        "areas": [_area_view(a, groups_by_id, rules_by_area) for a in doc["areas"]],
        "observations": [_group_view(g, entry, pack) for g in doc["observations"]],
        "review_required": [_group_view(g, entry, pack) for g in doc["review_required"]],
        "descriptive": [_group_view(g, entry, pack) for g in doc["descriptive_signals"]],
        "limitations": [{"title": s["title"], "surface_id": s["surface_id"], "area": P.area_title(s["area"]),
                         "state": P.assessability_text(s["assessability"]), "reason": s["reason"]["reason_summary"] if s["reason"] else "",
                         "reason_code": s["reason"]["reason_code"] if s["reason"] else "",
                         "counts": s["counts_toward_coverage"],
                         "units": [P.unit_label(u) for u in s["affected_units"]]} for s in doc["evidence_limitations"]],
        "absence_not_established": [r["title"] for r in doc["rule_results"] if r["result"] == "ABSENCE_NOT_ESTABLISHED"],
        "confidence_components": [{"label": c["label"], "value": c["value"], "level": P.confidence_level_text(c["level"])} for c in conf["components"]],
        "not_applicable": [{"title": s["title"], "surface_id": s["surface_id"], "area": P.area_title(s["area"]),
                            "reason": s["reason"]["reason_summary"], "reason_code": s["reason"]["reason_code"]} for s in doc["not_applicable"]],
        "deduplicated_questions": [{"id": q["id"], "covered_by": q["covered_by"]} for q in ctx["deduplicated_questions"]],
        "rule_results": [{"title": r["title"], "rule": r["rule"], "result_text": P.rule_result_text(r["result"]), "count": r["finding_count"]}
                         for r in doc["rule_results"]],
        "area_coverage": [{"title": P.area_title(a["id"]), **a["required_surfaces"]} for a in doc["areas"]],
        "technical": {"pool_signature": scope.get("pool_signature"),
                      "fact_families": len({f["fact"].split("@")[0] for f in doc["fact_index"]}), "fact_instances": len(doc["fact_index"]),
                      "versions_all": sorted(doc["versions"].items())},
    })
    return view
