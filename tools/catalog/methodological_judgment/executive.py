"""Constructor PURO de la entrada ejecutiva para un futuro Asesor LLM (``executive-input/v1``). Sin LLM, sin red, sin narrativa, sin seleccion de modelo.

Solo expone informacion ESTRUCTURADA y FUNDAMENTADA del motor determinista; el nivel metodologico entra como ``immutable_judgment`` y el LLM no puede alterarlo."""
from __future__ import annotations

from typing import Any, Mapping

from tools.catalog.methodological_judgment.profile_access import ProfileEvidence

from . import contract as C

_REASON_KEYS = ("reason_id", "rule_id", "classification", "dimension", "secondary_dimension", "statement_key", "affected_artifacts", "evidence_refs", "causal_conditions", "state")


def _pick(d: Mapping[str, Any], keys) -> dict:
    return {k: d[k] for k in keys if k in d}


def _observed_load(ev: ProfileEvidence) -> dict:
    """Propiedades de carga EXPLICITAMENTE exportadas. Un termino de carga solo puede aparecer en una narrativa si esta aqui (nunca se infiere FULL/DELTA/federado/replicado)."""
    flows, terms = [], set()
    for f in ev.section("processing_flow_properties").get("flows") or ():
        obs = f.get("observed") or {}
        p = f.get("properties") or {}
        rec = {"flow_kind": f.get("flow_kind"), "flow_name": f.get("flow_name"), "observed": {k: v for k, v in sorted(obs.items()) if v not in (None, [], {})}}
        if p.get("delta_load_trigger"):
            rec["observed"]["delta_load_trigger"] = p["delta_load_trigger"]
            terms.add("delta")
        for t in p.get("tasks") or ():
            if t.get("change_capture_properties") or t.get("delta_check_interval") or t.get("delta_disabled") is not None:
                terms.add("delta")
            if t.get("load_type"):
                terms.add(str(t["load_type"]).lower())
        for vals in obs.values():
            for v in (vals if isinstance(vals, list) else [vals]):
                if isinstance(v, str):
                    terms.add(v.lower())
        flows.append(rec)
    return {"flows": flows, "allowed_terms": sorted(terms), "unobserved_properties": ["schedule", "federation_state", "replication_state", "execution_mode"]}


def build_executive_input(judgment: Mapping[str, Any], profile: Mapping[str, Any]) -> dict:
    ev = ProfileEvidence(profile)
    reasons = judgment.get("reasons") or []
    refs: set[str] = set()
    for group in (reasons, judgment.get("strengths") or [], judgment.get("review_candidates") or [], judgment.get("evidence_limitations") or [], judgment.get("cta_candidates") or []):
        for item in group:
            refs.update(item.get("evidence_refs") or ())
    refs = {r for r in refs if ev.ref_exists(r)}
    open_questions = [{"question_id": q.get("id"), "level_effect": "NONE", "interpretation": "OPEN_QUESTION_NOT_A_DEFECT"} for q in ev.open_questions if q.get("id")]
    live = [r for r in reasons if r["state"] not in C.REMOVING_STATES]
    ex = {
        "executive_input_version": C.EXECUTIVE_INPUT_VERSION, "cta_playbook_version": C.CTA_PLAYBOOK_VERSION,
        "immutable_judgment": {"status": judgment.get("status"), "level": judgment.get("level"), "closure": judgment.get("closure"), "ruleset_version": judgment.get("ruleset_version"),
                               "plena_assignment": judgment.get("plena_assignment")},
        "terminal_analytic_model": ev.am,
        "strengths": [_pick(s, ("strength_id", "rule_id", "dimension", "statement_key", "affected_artifacts", "evidence_refs")) for s in judgment.get("strengths") or ()],
        "material_deviations": [_pick(r, _REASON_KEYS) for r in live if r["classification"] == C.MATERIAL_DEVIATION],
        "relevant_weaknesses": [_pick(r, _REASON_KEYS) for r in live if r["classification"] == C.RELEVANT_WEAKNESS],
        "local_weaknesses": [_pick(r, _REASON_KEYS) for r in live if r["classification"] == C.LOCAL_WEAKNESS],
        "excepted_reasons": [_pick(r, _REASON_KEYS) for r in reasons if r["state"] in C.REMOVING_STATES],
        "qualifiers": judgment.get("qualifiers") or {},
        "open_questions": open_questions,
        "review_candidates": [_pick(r, ("review_id", "rule_id", "family", "state", "affected_artifacts", "evidence_refs", "level_effect")) for r in judgment.get("review_candidates") or ()],
        "cta_candidates": list(judgment.get("cta_candidates") or ()),
        "evidence_limitations": [_pick(l, ("limitation_id", "key", "affected_artifacts", "can_change_level")) for l in judgment.get("evidence_limitations") or ()],
        "what_would_change_the_judgment": list(judgment.get("what_would_change_the_judgment") or ()),
        "what_would_improve_the_solution": list(judgment.get("what_would_improve_the_solution") or ()),
        "supporting_evidence": dict(judgment.get("supporting_evidence") or {}),
        "observed_load_properties": _observed_load(ev),
        "evidence_refs": sorted(refs),
        "known_artifacts": sorted(ev.known_artifacts()),
        "known_reason_ids": sorted([r["reason_id"] for r in reasons] + [s["strength_id"] for s in judgment.get("strengths") or ()]),
        "known_review_ids": sorted(r["review_id"] for r in judgment.get("review_candidates") or ()),
        "known_cta_ids": sorted(c["cta_id"] for c in judgment.get("cta_candidates") or ()),
        "external_undetermined_artifacts": sorted({a for r in judgment.get("review_candidates") or () if r.get("rule_id") == "SRC-REV-01" for a in r.get("affected_artifacts") or ()}),
        "judgment_output_signature": judgment.get("output_signature"),
    }
    ex["input_signature"] = C.signature(ex)
    return ex
