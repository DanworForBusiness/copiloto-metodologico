"""T2 — explicabilidad de «No determinable»: causa real desde hechos/códigos del Juicio persistido; el valor oficial NO cambia; una causa, un texto."""
import copy

from webapp.services import assessment_presenter as P

DIM = {"dimension": "SOURCE_INGESTION", "state": "NO_STRENGTH", "cause": "SOURCE_CONTACT_NOT_ALL_GOVERNED"}


def _nd(code="INSUFFICIENT_AFFIRMATIVE_EVIDENCE", rule="LVL-06", missing=None, **extra):
    j = {"status": "NOT_DETERMINABLE", "level": None, "level_rule": rule, "not_determinable_reason": code, "reasons": [], "strengths": [], "review_candidates": []}
    if missing is not None:
        j["not_determinable_detail"] = {"level_rule": "LVL-06", "missing_strength_dimensions": missing, "evidence_sufficient": not any(d["state"] == "INSUFFICIENT_EVIDENCE" for d in missing)}
    j.update(extra)
    return j


def _d(dim, state="NO_STRENGTH", cause="NO_AFFIRMATIVE_EVIDENCE"):
    return {"dimension": dim, "state": state, "cause": cause}


def test_a_determined_judgment_or_no_judgment_has_no_explanation():
    assert P.nd_explanation(None) is None and P.nd_explanation({}) is None
    assert P.nd_explanation({"status": "DETERMINED", "level": "ADECUADA"}) is None


def test_sufficient_evidence_without_strengths_is_stated_as_such_and_never_as_insufficient():
    e = P.nd_explanation(_nd(missing=[DIM, _d("HARMONIZATION", cause="NO_HARMONIZATION_BOUNDARY_OBSERVED")]))
    assert e["cause"] == P.ND_SUFFICIENT_NO_STRENGTH and "Sin deficiencias determinables" in e["text"] and "Ingesta, Armonización" in e["text"]
    assert "insuficiente" not in e["text"].lower().replace("evidencia afirmativa", "") and [d["dimension"] for d in e["dimensions"]] == ["Ingesta", "Armonización"]


def test_a_local_weakness_is_counted_from_the_judgment_not_hidden_behind_no_weaknesses():
    reasons = [{"classification": "LOCAL_WEAKNESS", "state": "OPEN"}, {"classification": "LOCAL_WEAKNESS", "state": "NOT_APPLICABLE"}]
    e = P.nd_explanation(_nd(missing=[DIM], reasons=reasons))
    assert "1 debilidad local sin efecto" in e["text"] and "Sin deficiencias determinables" not in e["text"]       # la que una excepción/NA retiró no cuenta


def test_insufficient_and_mixed_evidence_are_distinct_causes_with_distinct_texts():
    ins = _d("PUBLISHED_CONTRACT", "INSUFFICIENT_EVIDENCE", "PUBLISHED_LINEAGE_INCOMPLETE")
    only = P.nd_explanation(_nd(missing=[ins]))
    mixed = P.nd_explanation(_nd(missing=[ins, DIM]))
    assert only["cause"] == P.ND_INSUFFICIENT_EVIDENCE and mixed["cause"] == P.ND_PARTIAL_EVIDENCE
    assert only["text"] != mixed["text"] and "Contrato publicado" in only["text"] and "Ingesta" in mixed["text"] and "evidencia suficiente" in mixed["text"]
    assert only["dimensions"][0]["cause_text"].startswith("el linaje de los campos publicados")


def test_undetailed_lvl06_does_not_claim_the_evidence_was_sufficient():
    e = P.nd_explanation(_nd())
    assert e["cause"] == P.ND_NO_STRENGTH_UNDETAILED and "no detalla" in e["text"] and e["dimensions"] == []


def test_other_supported_indeterminations_each_have_their_own_cause():
    ne = P.nd_explanation(_nd("CONTRACT_NOT_EVALUABLE", "LVL-01", contracts_not_evaluable=["HARMONIZATION", "PROPAGATION"]))
    assert ne["cause"] == P.ND_CONTRACT_NOT_EVALUABLE and "Armonización, Propagación" in ne["text"] and "los contratos" in ne["text"]
    rev = [{"rule_id": "SRC-REV-01", "affected_artifacts": ["T_A", "T_B"]}, {"rule_id": "OTHER", "affected_artifacts": ["T_C"]}]
    ext = P.nd_explanation(_nd("EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL", "LVL-05", review_candidates=rev))
    assert ext["cause"] == P.ND_SOURCE_CLASS_CRITICAL and "2 artefactos" in ext["text"]
    assert P.nd_explanation(_nd("ASSESSMENT_NOT_JUDGEABLE", None))["cause"] == P.ND_NOT_JUDGEABLE
    assert P.nd_explanation(_nd("CANONICAL_EVIDENCE_V3_REQUIRED", None))["cause"] == P.ND_EVIDENCE_V3_REQUIRED
    assert P.nd_explanation(_nd("SOMETHING_NEW", None))["cause"] == P.ND_UNSPECIFIED and "SOMETHING_NEW" in P.nd_explanation(_nd("SOMETHING_NEW", None))["text"]


def test_every_cause_has_a_different_label_and_text():
    cases = [_nd(missing=[DIM]), _nd(missing=[_d("PROPAGATION", "INSUFFICIENT_EVIDENCE", "NO_AFFIRMATIVE_EVIDENCE")]),
             _nd(missing=[_d("PROPAGATION", "INSUFFICIENT_EVIDENCE"), DIM]), _nd(), _nd("CONTRACT_NOT_EVALUABLE", "LVL-01", contracts_not_evaluable=["PROPAGATION"]),
             _nd("EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL", "LVL-05"), _nd("ASSESSMENT_NOT_JUDGEABLE", None), _nd("CANONICAL_EVIDENCE_V3_REQUIRED", None)]
    out = [P.nd_explanation(c) for c in cases]
    assert len({e["label"] for e in out}) == len(out) and len({e["text"] for e in out}) == len(out)


def test_the_official_value_and_the_judgment_are_untouched_and_the_view_carries_the_explanation():
    j = _nd(missing=[DIM])
    before = copy.deepcopy(j)
    v = P.judgment_view(j)
    assert j == before                                                            # solo lectura: no muta el Juicio persistido
    assert v["determined"] is False and v["level"] is None and v["level_label"] == "No determinable" and v["nd_reason_id"] == "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"
    assert v["nd"]["cause"] == P.ND_SUFFICIENT_NO_STRENGTH and v["headline"] == v["nd"]["text"] and v["nd_reason"] == v["nd"]["label"]
    assert P.judgment_view({"status": "DETERMINED", "level": "BUENA", "strengths": [], "reasons": []})["nd"] is None
