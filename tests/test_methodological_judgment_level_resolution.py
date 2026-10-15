"""LEVEL_RESOLUTION — variante EXPLORATORIA (opt-in) de resolución de nivel con estados explícitos por dimensión.

Fortaleza demostrada · responsabilidad cumplida (EVIDENCIA POSITIVA) · deficiencia demostrada · no evaluable (falta evidencia) · no aplicable (justificado por la arquitectura). «No evaluable» y «No aplicable» no son
equivalentes; la ausencia de fortaleza no es deficiencia; nunca se asigna «cumplida» ni «no aplicable» por mera ausencia. Solo reinterpreta los LVL-06: el resto de la decisión queda intacto."""
from __future__ import annotations

import glob
import gzip
import json
import os

import pytest

from tests._judgment_profiles import PB
from tests.test_methodological_judgment_rules import buena_model
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.rules import resolve_level_exploratory

LV = ("LEVEL_RESOLUTION",)
ALL = ("T4_HAR", "T5_LOCAL_PHYSICAL", "LEVEL_RESOLUTION")
S, M, D, NE, NA, UN = C.DS_STRENGTH, C.DS_MET, C.DS_DEFICIENCY, C.DS_NOT_EVALUABLE, C.DS_NOT_APPLICABLE, C.DS_UNRESOLVED


def _states(*xs):
    return [{"dimension": f"D{i}", "state": x} for i, x in enumerate(xs)]


def _replica_model(producers=("REPLICATION_FLOW",), state="ESTABLISHED", join=False):
    """Producto que lee UNA tabla local (réplica de un contrato gobernado), sin capa de armonización, con fortalezas de ingesta y publicado."""
    b = PB().view("V1", ["LT1"], joins=1 if join else 0).local_leaf("LT1", state=state).product("V1").measure("M").flow("RF", delta=True)
    b.trace("M", "LT1", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False).assoc("D1")
    p = b.build()
    for rec in p["canonical_evidence"]["leaf_provenance"]:
        if rec["leaf"] == "LT1":
            rec.update(basis="REPLICATION_FLOW", chain_complete=True, producer_flows=[{"kind": k, "name": f"F_{k}", "target_match": "EXACT", "load_types": []} for k in producers])
    return p


def states_of(j):
    return {d["dimension"]: d["state"] for d in j.get("dimension_states", [])}


# ───────── la hipótesis de nivel (función pura) ─────────
def test_the_level_hypothesis_never_promotes_unresolved_unevaluable_or_deficient_dimensions():
    assert resolve_level_exploratory(_states(S, S, S, M)) == (C.BUENA, "LVL-07")                         # fortalezas suficientes + responsabilidad cumplida
    assert resolve_level_exploratory(_states(S, S, S, NA)) == (C.BUENA, "LVL-07")
    assert resolve_level_exploratory(_states(S, NA, M, S)) == (C.ADECUADA, "LVL-08")                    # esenciales cumplidas, fortalezas insuficientes para Buena
    for bad in (NE, UN, D):
        assert resolve_level_exploratory(_states(S, S, S, bad)) is None and resolve_level_exploratory(_states(S, M, M, bad)) is None
    assert resolve_level_exploratory(_states(M, M, M, M)) is None and resolve_level_exploratory(_states(NA, NA, NA, NA)) is None      # sin ninguna fortaleza no se promueve; todo «no aplicable» tampoco
    assert NE != NA and resolve_level_exploratory(_states(S, S, S, NA)) != resolve_level_exploratory(_states(S, S, S, NE))                # no evaluable ≠ no aplicable


# ───────── sobre perfiles ─────────
def test_a_pure_replica_without_integration_gets_adecuada_by_resolution_with_traceable_states():
    p = _replica_model()
    base, v = evaluate_judgment(p), evaluate_judgment(p, variants=LV)
    assert (base["status"], base["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06")
    assert (v["status"], v["level"], v["level_rule"]) == ("DETERMINED", "ADECUADA", "LVL-08") and "not_determinable_detail" not in v and v["ruleset_options"] == {"variants": ["LEVEL_RESOLUTION"]}
    assert states_of(v) == {"SOURCE_INGESTION": S, "HARMONIZATION": NA, "PROPAGATION": M, "PUBLISHED_CONTRACT": S}
    ds = {d["dimension"]: d for d in v["dimension_states"]}
    assert "ARCH.no_view_in_closure_integrates_several_inputs_joins_0_unions_0" in ds["HARMONIZATION"]["basis"]                       # no aplicable: justificado por la arquitectura observada
    assert "ce:leaf_provenance/LT1" in ds["PROPAGATION"]["basis"]                                                                     # cumplida: evidencia positiva con referencia
    assert v["strengths"] == base["strengths"] and v["reasons"] == base["reasons"]                                                    # el nivel cambia por la regla, no por fortalezas/razones nuevas


def test_responsibility_met_needs_positive_evidence_not_just_the_absence_of_weaknesses():
    for tag, p in (("replica escrita tambien por un Data Flow", _replica_model(("REPLICATION_FLOW", "DATA_FLOW"))), ("sin procedencia comprobada", _replica_model(state=None))):
        v = evaluate_judgment(p, variants=LV)
        assert v["status"] == "NOT_DETERMINABLE" and v["level_rule"] == "LVL-06" and states_of(v)["PROPAGATION"] == UN, tag


def test_not_applicable_needs_an_architectural_justification_not_just_a_missing_layer():
    v = evaluate_judgment(_replica_model(join=True), variants=LV)                                  # un join integra entradas: la ausencia de armonización NO es «no aplicable»
    assert v["status"] == "NOT_DETERMINABLE" and states_of(v)["HARMONIZATION"] == UN


def test_insufficient_evidence_is_not_evaluable_and_never_promoted():
    p = _replica_model()
    p2 = PB().view("V1", ["LT1"]).local_leaf("LT1").product("V1").measure("M").flow("RF", delta=True).trace("M", "LT1").incomplete("M").build()
    for rec in p2["canonical_evidence"]["leaf_provenance"]:
        rec.update(basis="REPLICATION_FLOW", chain_complete=True, producer_flows=[{"kind": "REPLICATION_FLOW", "name": "RF", "target_match": "EXACT", "load_types": []}])
    v = evaluate_judgment(p2, variants=LV)
    assert v["status"] == "NOT_DETERMINABLE" and NE in states_of(v).values() and v["not_determinable_detail"]["evidence_sufficient"] is False
    assert evaluate_judgment(p, variants=LV)["status"] == "DETERMINED"                                # control: con evidencia completa sí


def test_a_demonstrated_local_deficiency_blocks_the_promotion():
    p = buena_model().remote("RAW1", raw=True, spine=False).build()                                # tabla ABAP física cruda: SRC-LOC-01 y sin SRC-STR-01
    v = evaluate_judgment(p, variants=LV)
    assert "SRC-LOC-01" in {r["rule_id"] for r in v["reasons"]} and states_of(v)["SOURCE_INGESTION"] == D and v["status"] == "NOT_DETERMINABLE"


# ───────── lo que NO debe cambiar ─────────
_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "methodological_judgment")


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(_FIX, "*.json.gz"))), ids=lambda p: os.path.basename(p)[:-8])
def test_golden_levels_never_change_including_the_good_controls_and_the_critical_undetermined_source(path):
    prof = json.loads(gzip.open(path).read().decode("utf-8"))
    base, v = evaluate_judgment(prof), evaluate_judgment(prof, variants=ALL)
    assert (base["status"], base["level"], base["level_rule"]) == (v["status"], v["level"], v["level_rule"]), os.path.basename(path)          # LVL-05 (fuente crítica) y los determinados no se tocan


def test_controls_and_determined_models_are_untouched_by_the_level_variant():
    p = buena_model().build()
    base, v = evaluate_judgment(p), evaluate_judgment(p, variants=LV)
    assert (base["status"], base["level"], base["level_rule"]) == (v["status"], v["level"], v["level_rule"]) == ("DETERMINED", "BUENA", "LVL-04") and "dimension_states" not in v
