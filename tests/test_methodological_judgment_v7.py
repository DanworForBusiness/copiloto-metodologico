"""judgment-rules/v7 — R-ACC estricta (contrato externo ACEPTADO por ratificacion): resuelve la Ingesta y el contrato publicado como RESPONSABILIDAD CUMPLIDA
cuando el unico bloqueo es una hoja externa aceptada para ESTE consumo; nunca concede fortalezas, nunca neutraliza deficiencias independientes; v2-v6 intactos.
Sin datos de clientes: perfiles sinteticos + casos de referencia seudonimizados."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tests._judgment_cases import FROZEN_LABELS, fresh
from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment

V6 = dict(ruleset=C.RULESET_V6)
V7 = dict(ruleset=C.RULESET_V7)
CONN = "SQL_SERVER_X"


def _model(*, exposure="verbatim", second_external=False, raw_leaf=False):
    """Producto sobre una hoja externa EXT (y opcionalmente otra externa EXT2 o una tabla fisica RAW). ``M`` hereda el vocabulario de EXT."""
    provs = ["EXT", "CDS1"] + (["EXT2"] if second_external else []) + (["RAW"] if raw_leaf else [])
    b = PB().view("V1", provs, joins=len(provs) - 1).remote("EXT", external=True).remote("CDS1").product("V1").measure("M").measure("Q")
    if second_external:
        b.remote("EXT2", external=True)
        b.trace("Q", "EXT2", character="IDENTITY_ONLY", label="EQUALS_LEAF_LABEL")
    else:
        b.trace("Q", "CDS1", character="IDENTITY_OR_RENAME", label="DIFFERENT_FROM_LEAF", name_equals=False)
    if raw_leaf:
        b.remote("RAW", raw=True)
    if exposure == "verbatim":
        b.trace("M", "EXT", character="IDENTITY_ONLY", label="EQUALS_LEAF_LABEL")
    else:
        b.trace("M", "EXT", character="IDENTITY_OR_RENAME", label="DIFFERENT_FROM_LEAF", name_equals=False)
    p = b.build()
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf"] in ("EXT", "EXT2"):
            r["remote_connection"], r["remote_entities"] = CONN, [f'"dbo"."{r["leaf"]}"']
    return p


def _ratify(leaf, entity=None, applies_to=("AM",)):
    ent = entity or f'"dbo"."{leaf}"'
    d = hashlib.sha256(leaf.encode()).hexdigest()
    return {"applies_to": list(applies_to), "leaf": leaf, "identity": {"remote_connection": CONN, "remote_entity": ent}, "technical_id": json.dumps({"remote_connection": CONN, "remote_entity": ent}),
            "event_id": f"gd-test-{leaf}", "digest": d, "evidence_ref": {"algo": "sha256", "digest": d, "fields": ["source_contract_classes.leaves[cds]"]}}


def _dims(j):
    return {d["dimension"]: d["state"] for d in j.get("dimension_states") or ()}


def _strengths(j):
    return sorted({s["rule_id"] for s in j["strengths"]})


def _ratif(j, leaf):
    return next(d for d in j["dispositions_applied"] if d.get("leaf") == leaf)


# ───────────── R-ACC: efecto positivo acotado ─────────────
def test_v7_an_accepted_external_source_resolves_source_and_published_contract_as_met_without_new_strengths():
    p, rc = _model(), [_ratify("EXT")]
    j6, j7 = evaluate_judgment(copy.deepcopy(p), ratified_contracts=rc, **V6), evaluate_judgment(copy.deepcopy(p), ratified_contracts=rc, **V7)
    assert _dims(j6)[C.SOURCE_INGESTION] == C.DS_NOT_EVALUABLE
    assert _dims(j7)[C.SOURCE_INGESTION] == C.DS_MET and _dims(j7)[C.PUBLISHED_CONTRACT] == C.DS_MET
    assert _strengths(j7) == _strengths(j6) and not {"SRC-STR-01", "PUB-STR-01"} & set(_strengths(j7))     # NUNCA fortaleza


def test_v7_a_valid_non_critical_ratification_is_an_applied_acceptance_while_v6_keeps_no_effect():
    p, rc = _model(exposure="authored"), [_ratify("EXT")]
    assert _ratif(evaluate_judgment(copy.deepcopy(p), ratified_contracts=rc, **V6), "EXT")["status"] == "NO_EFFECT"
    r7 = _ratif(evaluate_judgment(copy.deepcopy(p), ratified_contracts=rc, **V7), "EXT")
    assert (r7["status"], r7["effect"]) == ("APPLIED", C.RATIFICATION_EFFECT_ACCEPTED)


# ───────────── R-ACC: negativos ─────────────
def test_v7_without_ratification_nothing_changes():
    p = _model()
    j6, j7 = evaluate_judgment(copy.deepcopy(p), ratified_contracts=[], **V6), evaluate_judgment(copy.deepcopy(p), ratified_contracts=[], **V7)
    assert (j6["status"], j6["level_rule"], _dims(j6)) == (j7["status"], j7["level_rule"], _dims(j7))


def test_v7_another_unaccepted_external_source_keeps_source_without_evidence_and_blocks_published_contract():
    j = evaluate_judgment(_model(second_external=True), ratified_contracts=[_ratify("EXT")], **V7)
    if j.get("dimension_states"):
        assert _dims(j)[C.SOURCE_INGESTION] == C.DS_NOT_EVALUABLE and _dims(j)[C.PUBLISHED_CONTRACT] != C.DS_MET
    else:                                                                    # EXT2 sigue siendo decision-critica: LVL-05
        assert j["level_rule"] == "LVL-05"


def test_v7_an_independent_physical_raw_deficiency_is_never_neutralized():
    p = _model(raw_leaf=True)
    j6 = evaluate_judgment(copy.deepcopy(p), ratified_contracts=[_ratify("EXT")], **V6)
    j7 = evaluate_judgment(copy.deepcopy(p), ratified_contracts=[_ratify("EXT")], **V7)
    assert sorted(r["reason_id"] for r in j7["reasons"]) == sorted(r["reason_id"] for r in j6["reasons"])
    if j7.get("dimension_states"):
        assert _dims(j7)[C.SOURCE_INGESTION] != C.DS_MET                                            # la tabla cruda no es aceptada


def test_v7_a_ratification_with_another_identity_has_no_effect():
    j = evaluate_judgment(_model(), ratified_contracts=[_ratify("EXT", entity='"dbo"."OTRA"')], **V7)
    assert _ratif(j, "EXT")["status"] == "REJECTED" and j["level_rule"] == "LVL-05"


# ───────────── aislamiento: v6 intacto y v7 = v6 sin aceptaciones en los casos de referencia ─────────────
@pytest.mark.parametrize("alias", sorted(FROZEN_LABELS))
def test_v7_equals_v6_on_every_reference_case_without_acceptances(alias):
    j6, j7 = evaluate_judgment(fresh(alias), ratified_contracts=[], **V6), evaluate_judgment(fresh(alias), ratified_contracts=[], **V7)
    key = lambda j: (j["status"], j["level"], j["level_rule"], j["closure"], sorted(r["reason_id"] for r in j["reasons"]), _strengths(j))   # noqa: E731
    assert key(j6) == key(j7) and j7["ruleset_version"] == C.RULESET_V7


def test_v7_is_available_but_not_current_and_only_activatable_by_its_signed_migration_over_v6():
    from webapp.services import assessment_backfill as bf
    assert C.RULESET_V7 in C.RULESETS and C.CURRENT_RULESET_VERSION != C.RULESET_V7
    assert bf._ACTIVATION_MIGRATIONS[C.RULESET_V7] == "judgment_rules_v7" and bf._ACTIVATION_PREREQUISITES[C.RULESET_V7] == C.RULESET_V6


# ───────────── R-ACC: ALCANCE de consumo declarado ─────────────
def test_v7_a_ratification_without_declared_consumption_keeps_the_v6_semantics():
    p = _model()
    rc = [_ratify("EXT", applies_to=())]                                              # como TODAS las ratificaciones historicas
    j6, j7 = evaluate_judgment(copy.deepcopy(p), ratified_contracts=rc, **V6), evaluate_judgment(copy.deepcopy(p), ratified_contracts=rc, **V7)
    assert _dims(j7) == _dims(j6) and "effect" not in _ratif(j7, "EXT")


def test_v7_acceptance_declared_for_another_consumer_never_applies_here():
    p = _model(exposure="authored")
    j7 = evaluate_judgment(p, ratified_contracts=[_ratify("EXT", applies_to=("OTRO_AM",))], **V7)
    assert _ratif(j7, "EXT")["status"] == "NO_EFFECT"


def test_decision_inputs_of_historical_ratifications_are_byte_identical():
    from tools.catalog.methodological_judgment.engine import decision_inputs
    hist = _ratify("EXT", applies_to=())
    hist.pop("applies_to")
    assert "applies_to" not in decision_inputs([hist])["ratified_contracts"][0]
