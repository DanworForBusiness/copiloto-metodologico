"""
Gate 8D.1 — CTA candidates (playbook gobernado), entrada ejecutiva (``executive-input/v1``) y validador del contrato de salida del futuro Asesor LLM.

No se llama a ningun LLM: el validador es puro y se prueba con salidas construidas a mano.
"""
from __future__ import annotations

import copy
import json

import pytest

from tests._judgment_cases import fresh
from tools.catalog.methodological_judgment import (CTA_PLAYBOOK_VERSION, EXECUTIVE_INPUT_VERSION, build_executive_input, evaluate_judgment, validate_executive_output)
from tools.catalog.methodological_judgment.cta import PLAYBOOK
from tools.catalog.methodological_judgment.contract import CTA_PRIORITIES

SELLOUT, PREF, KENSING, BASE = "biogenesis_sellout_gp", "biogenesis_prefacturacion", "kensing_gl_line_item_raw", "biogenesis_base_stocks"


def ctx(alias):
    p = fresh(alias)
    j = evaluate_judgment(p)
    return p, j, build_executive_input(j, p)


def good_output(ex: dict) -> dict:
    """Narrativa minima VALIDA construida solo con material de la entrada ejecutiva."""
    ij = ex["immutable_judgment"]
    reasons = (ex["material_deviations"] or ex["relevant_weaknesses"])[:1]
    ctas = ex["cta_candidates"][:1]
    out = {
        "executive_summary": {"level_echo": ij["level"], "status_echo": ij["status"], "closure_echo": ij["closure"], "text": "El modelo requiere atencion en un punto concreto."},
        "why_this_level": [{"reason_ids": [r["reason_id"] for r in reasons], "affected_artifacts": reasons[0]["affected_artifacts"][:1], "evidence_refs": reasons[0]["evidence_refs"][:1],
                            "text": "La responsabilidad se resuelve aguas abajo."}] if reasons else [],
        "strengths": [],
        "improvement_actions": [{"cta_id": c["cta_id"], "priority": c["priority_class"], "action_key": c.get("action_family"), "evidence_refs": c["evidence_refs"][:1],
                                 "text": "Resolver el punto en el modelo reutilizable."} for c in ctas if c["priority_class"] in CTA_PRIORITIES[:4]],
        "questions_for_project": [],
        "maintainability_opportunities": [],
        "what_would_improve_the_solution": [],
        "evidence_limitations": [],
    }
    return out


# ───────────────────────────── CTA ─────────────────────────────
def test_cta_candidates_come_only_from_the_governed_playbook_and_are_grounded():
    for alias in (SELLOUT, PREF, KENSING, BASE):
        p, j, _ = ctx(alias)
        allowed = {v[0] for v in PLAYBOOK.values()} | {c["cta_id"] for c in j["cta_candidates"] if c["cta_id"].startswith("CTA-")}
        for c in j["cta_candidates"]:
            assert c["cta_id"] in allowed
            assert c["priority_class"] in CTA_PRIORITIES and c["action_family"] and c["cause_rule_ids"] is not None and c["affected_artifacts"] is not None
            assert c["applicability_state"] in ("APPLICABLE", "REVIEW", "DISABLED_PENDING_EVIDENCE") and "evidence_refs" in c


def test_material_reason_yields_an_applicable_material_cta_and_playbook_version_is_exposed():
    _, j, ex = ctx(BASE)
    mat = [c for c in j["cta_candidates"] if c["priority_class"] == "MATERIAL"]
    assert mat and all(c["applicability_state"] == "APPLICABLE" for c in mat)
    assert ex["cta_playbook_version"] == CTA_PLAYBOOK_VERSION


def test_predicate_ownership_cta_is_disabled_pending_evidence():
    _, j, _ = ctx(BASE)
    c = [c for c in j["cta_candidates"] if c["cta_id"] == "CTA-PRO-PREDICATE-OWNERSHIP-REVIEW"]
    assert c and c[0]["applicability_state"] == "DISABLED_PENDING_EVIDENCE"


# ───────────────────────────── entrada ejecutiva ─────────────────────────────
def test_executive_input_exposes_the_immutable_judgment_and_only_grounded_material():
    p, j, ex = ctx("biogenesis_reserva_material")
    assert ex["executive_input_version"] == EXECUTIVE_INPUT_VERSION
    assert ex["immutable_judgment"] == {"status": j["status"], "level": j["level"], "closure": j["closure"], "ruleset_version": j["ruleset_version"], "plena_assignment": j["plena_assignment"]}
    assert ex["material_deviations"] and all(r["evidence_refs"] for r in ex["material_deviations"])
    assert set(ex["known_artifacts"]) >= {a for r in ex["material_deviations"] for a in r["affected_artifacts"]}
    assert ex["judgment_output_signature"] == j["output_signature"] and ex["input_signature"]


def test_executive_input_is_deterministic_json_and_does_not_mutate_inputs():
    p, j, _ = ctx(PREF)
    ps, js = copy.deepcopy(p), copy.deepcopy(j)
    a, b = build_executive_input(j, p), build_executive_input(copy.deepcopy(j), copy.deepcopy(p))
    assert a == b and p == ps and j == js and json.loads(json.dumps(a)) == a


def test_open_questions_are_exposed_as_not_defects_and_unobserved_load_properties_are_declared():
    p, _, ex = ctx(PREF)
    assert all(q["interpretation"] == "OPEN_QUESTION_NOT_A_DEFECT" and q["level_effect"] == "NONE" for q in ex["open_questions"])
    assert {"federation_state", "replication_state"} <= set(ex["observed_load_properties"]["unobserved_properties"])


def test_executive_input_never_contains_a_narrative_or_a_model_selection():
    _, _, ex = ctx(KENSING)
    assert not {"model", "llm", "prompt", "narrative", "temperature"} & set(ex)


# ───────────────────────────── validador ─────────────────────────────
def test_a_well_formed_grounded_output_is_valid():
    _, _, ex = ctx(SELLOUT)
    res = validate_executive_output(good_output(ex), ex)
    assert res.valid, res.errors


def mutate(alias, fn):
    _, _, ex = ctx(alias)
    out = good_output(ex)
    fn(out, ex)
    return validate_executive_output(out, ex)


@pytest.mark.parametrize("name,fn,code", [
    ("level_mismatch", lambda o, e: o["executive_summary"].update(level_echo="BUENA"), "LEVEL_MISMATCH"),
    ("status_mismatch", lambda o, e: o["executive_summary"].update(status_echo="DETERMINED"), "STATUS_MISMATCH"),
    ("closure_mismatch", lambda o, e: o["executive_summary"].update(closure_echo="FINAL"), "CLOSURE_MISMATCH"),
    ("own_level_field", lambda o, e: o.update(level="BUENA"), "UNAUTHORIZED_LEVEL_FIELD"),
    ("missing_key", lambda o, e: o.pop("strengths"), "SCHEMA"),
    ("unknown_artifact", lambda o, e: o["why_this_level"][0].update(affected_artifacts=["INVENTED_VIEW"]), "UNKNOWN_ARTIFACT"),
    ("unknown_ref", lambda o, e: o["why_this_level"][0].update(evidence_refs=["ce:made_up/x"]), "UNKNOWN_EVIDENCE_REF"),
    ("unknown_reason", lambda o, e: o["why_this_level"][0].update(reason_ids=["NOPE:1"]), "UNKNOWN_REASON_ID"),
    ("unsupported_fact", lambda o, e: o["why_this_level"][0].update(evidence_refs=[]), "UNSUPPORTED_FACT"),
    ("cta_outside_playbook", lambda o, e: o["improvement_actions"][0].update(cta_id="CTA-MADE-UP"), "CTA_OUTSIDE_PLAYBOOK"),
    ("priority_invalid", lambda o, e: o["improvement_actions"][0].update(priority="URGENT"), "PRIORITY_INVALID"),
    ("full_load_inference", lambda o, e: o["executive_summary"].update(text="La carga es full y se replica a diario."), "UNSUPPORTED_LOAD_STATE"),
    ("federated_inference", lambda o, e: o["executive_summary"].update(text="El modelo esta federado."), "UNSUPPORTED_LOAD_STATE"),
    ("sql_dogma", lambda o, e: o["executive_summary"].update(text="SQL es mejor que lo grafico."), "SQL_GRAPHICAL_DOGMA"),
    ("graphical_dogma", lambda o, e: o["executive_summary"].update(text="Las vistas graficas son malas."), "SQL_GRAPHICAL_DOGMA"),
    ("plena_invented", lambda o, e: o["executive_summary"].update(text="El modelo alcanza el nivel PLENA."), "PLENA_INVENTED"),
    ("unknown_backtick_artifact", lambda o, e: o["executive_summary"].update(text="Revisar `VISTA_FANTASMA`."), "UNKNOWN_ARTIFACT"),
    ("invented_number", lambda o, e: o["executive_summary"].update(text="Afecta a 987654 campos."), "UNSUPPORTED_NUMBER"),
])
def test_validator_rejects_each_violation_class(name, fn, code):
    res = mutate(SELLOUT, fn)
    assert not res.valid and code in res.codes(), (name, res.errors)


def test_validator_rejects_a_review_treated_as_a_confirmed_defect():
    _, _, ex = ctx(SELLOUT)
    rv = ex["review_candidates"][0]["review_id"]
    out = good_output(ex)
    out["why_this_level"][0]["reason_ids"] = [rv]
    res = validate_executive_output(out, ex)
    assert "OPEN_QUESTION_AS_DEFECT" in res.codes()


def test_validator_accepts_load_terms_only_when_the_export_observed_them():
    p = fresh(SELLOUT)
    j = evaluate_judgment(p)
    ex = build_executive_input(j, p)
    ex = copy.deepcopy(ex)
    ex["observed_load_properties"]["allowed_terms"] = ["delta"]
    out = good_output(ex)
    out["executive_summary"]["text"] = "El flujo exporta una propiedad delta explicita."
    assert validate_executive_output(out, ex).valid
    out["executive_summary"]["text"] = "El flujo es full."
    assert "UNSUPPORTED_LOAD_STATE" in validate_executive_output(out, ex).codes()


def test_validator_does_not_accept_governed_or_raw_claims_for_undetermined_external_sources():
    _, _, ex = ctx(SELLOUT)
    ex = copy.deepcopy(ex)
    ex["external_undetermined_artifacts"] = ["EXT_X"]
    ex["known_artifacts"] = sorted({*ex["known_artifacts"], "EXT_X"})
    out = good_output(ex)
    out["executive_summary"]["text"] = "EXT_X es una fuente cruda."
    assert "UNSUPPORTED_SOURCE_STATE" in validate_executive_output(out, ex).codes()


def test_validator_is_pure_and_non_mutating():
    _, _, ex = ctx(SELLOUT)
    out = good_output(ex)
    exs, outs = copy.deepcopy(ex), copy.deepcopy(out)
    a, b = validate_executive_output(out, ex), validate_executive_output(out, ex)
    assert a == b and ex == exs and out == outs


def test_non_mapping_output_is_a_schema_error():
    _, _, ex = ctx(SELLOUT)
    assert validate_executive_output("texto libre", ex).codes() == ("SCHEMA",)
