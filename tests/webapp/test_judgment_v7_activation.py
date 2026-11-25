"""Activacion COORDINADA de judgment-rules/v7 (migracion ``judgment_rules_v7``): MISMO mecanismo que v5/v6 (plan firmado, respaldo, ACTIVATING invisible, commit, finalizacion,
rollback y restauracion) + prerrequisito v6 FINALIZED + consumo SOLO de evidencia estructural verificada (sidecar ``structural-evidence/v1``). Sin red ni Spaces."""
from __future__ import annotations

import copy
import hashlib

import pytest

from tests._judgment_cases import fresh
from tests.test_methodological_judgment_v7_structural import _jkp, _profile
from tests.webapp.test_governed_assessment_durability import restart, world  # noqa: F401 (fixture reutilizada)
from tools.catalog.methodological_judgment import RULESET_V5, evaluate_judgment
from tools.catalog.methodological_judgment.contract import RULESET_V6, RULESET_V7
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import judgment_activation as ja
from webapp.services import migration_approval as ma
from webapp.services import structural_evidence as SE

MODELS = {("Biogenesis", "AM_SELLOUT_GP"): "biogenesis_sellout_gp", ("Kensing", "4AM_GL"): "kensing_gl_line_item_raw",
          ("Biogenesis", "AM_DescuentosComerciales"): "biogenesis_descuentos_comerciales"}
STRUCT = ("Synth", "AM")                                                    # modelo sintetico con sidecar estructural (E3/E5)


@pytest.fixture(autouse=True)
def _isolated_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("DANWOR_GOVERNED_UPDATE_REQUESTS_DIR", str(tmp_path / "requests"))
    yield


def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="alice", operator="bob")


def _struct_env(*, tamper=False):
    p = _profile()
    blk = _jkp(vf_keys=("K",))["join_key_profile"]
    meta = {"evidence_extensions": {"join_key_profile": blk, "profile_binding": {"method": SE.BINDING_METHOD, "digest": svc._artifact_signature(p)}}}
    sc = SE.build_sidecar(meta, p, "AM", "pool-x")
    if tamper:
        sc = copy.deepcopy(sc)
        sc["join_key_profile"]["keys"]["VF"]["declared"] = ["K", "ITEM"]         # alterado: el digest ya no coincide
    return svc._envelope(*STRUCT, status="CURRENT", profile=p, pool_signature="pool-x", versions={}, structural_evidence=sc,
                         judgment=evaluate_judgment(p, ruleset=RULESET_V6, ratified_contracts=[]))


def seed_v6_finalized(world_, *, marker_state=ja.FINALIZED, marker_ruleset=RULESET_V6, judgment_ruleset=RULESET_V6, tamper=False) -> None:
    for (client, am), alias in MODELS.items():
        p = fresh(alias)
        svc._store_envelope(svc._envelope(client, am, status="CURRENT", profile=p, pool_signature="pool-x", versions={},
                                          judgment=evaluate_judgment(p, ruleset=judgment_ruleset, ratified_contracts=[])))
    svc._store_envelope(_struct_env(tamper=tamper))
    if marker_state is not None:
        ja.write_marker(marker_state, ruleset_version=marker_ruleset, previous_ruleset_version=RULESET_V5, plan_digest="c" * 64, operator="owner")
    restart()


ALL = [*MODELS, STRUCT]


def visible(client, am) -> dict:
    restart()
    return svc._persisted_judgment(svc._load_envelope(client, am))


def activate(world_, **kw):
    return bf.secured_activate_ruleset(target_ruleset=RULESET_V7, authorizer=kw.pop("authorizer", _grant), backup_dir=kw.pop("backup_dir", world_.tmp / "bk7"), operator="bob",
                                       allow_local_only=True, remote_enabled=lambda: False, **kw)


def _bytes():
    return {k: hashlib.sha256(svc.governed_path(*k).read_bytes()).hexdigest() for k in ALL}


def _dims(j):
    return {d["dimension"]: d["state"] for d in j.get("dimension_states") or ()}


# ───────────── migracion y prerrequisito ─────────────
def test_v7_has_its_own_signed_activation_migration_and_v5_v6_are_intact():
    assert bf._ACTIVATION_MIGRATIONS[RULESET_V7] == bf.JUDGMENT_V7_ACTIVATION_MIGRATION == "judgment_rules_v7"
    assert bf._ACTIVATION_MIGRATIONS[RULESET_V6] == "judgment_rules_v6" and bf._ACTIVATION_MIGRATIONS[RULESET_V5] == "judgment_rules_v5"
    assert bf._ACTIVATION_PREREQUISITES == {RULESET_V6: RULESET_V5, RULESET_V7: RULESET_V6}


@pytest.mark.parametrize("marker_state,marker_ruleset,judgment_ruleset", [
    (None, RULESET_V6, RULESET_V6), (ja.FINALIZED, RULESET_V5, RULESET_V5), (ja.FINALIZED, RULESET_V6, RULESET_V5)])
def test_v7_requires_v6_finalized_with_a_consolidated_padron_and_writes_nothing_otherwise(world, marker_state, marker_ruleset, judgment_ruleset):
    seed_v6_finalized(world, marker_state=marker_state, marker_ruleset=marker_ruleset, judgment_ruleset=judgment_ruleset)
    before, marker = _bytes(), ja.read_marker()
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATION_REFUSED and out["reason"] == bf.PREREQUISITE_NOT_MET
    assert out["prerequisite"]["required"].startswith(f"{RULESET_V6} FINALIZED") and ja.read_marker() == marker and _bytes() == before


def test_v7_activation_is_refused_while_v6_is_in_transition(world):
    seed_v6_finalized(world, marker_state=ja.ACTIVE)
    assert activate(world)["reason"] == "ACTIVATION_IN_TRANSITION"


def test_the_v7_dry_run_is_the_signed_global_plan_and_writes_nothing(world):
    seed_v6_finalized(world)
    before, marker = _bytes(), ja.read_marker()
    rep = bf.backfill_judgment(apply=False, target_ruleset=RULESET_V7)
    assert rep["plan"]["migration"] == bf.JUDGMENT_V7_ACTIVATION_MIGRATION and rep["visible_ruleset"] == RULESET_V6 and rep["activation_prerequisite"]["met"] is True
    assert {i["operation"] for i in rep["plan"]["items"]} == {"REPLACE_JUDGMENT"} and len(rep["plan"]["items"]) == len(ALL)
    assert [c["terminal_am"] for c in rep["level_changes"]] in ([], ["AM"])                    # solo el modelo con evidencia estructural puede cambiar
    assert ja.read_marker() == marker and _bytes() == before


# ───────────── evidencia estructural: solo verificada ─────────────
def test_v7_candidate_consumes_the_verified_sidecar_and_abstains_on_a_tampered_one(world):
    seed_v6_finalized(world)
    ok = bf.backfill_judgment(apply=False, target_ruleset=RULESET_V7)
    restart()
    world_tampered = svc._load_envelope(*STRUCT)
    fresh_ok = svc.classify_judgment_decisions(world_tampered, ruleset=RULESET_V7)["fresh"]
    assert fresh_ok["structural_evidence"]["e3"]["state"] == "DEMONSTRATED" and _dims(fresh_ok)["HARMONIZATION"] == "NOT_APPLICABLE"
    env = copy.deepcopy(world_tampered)
    env["structural_evidence"]["join_key_profile"]["keys"]["VF"]["declared"] = ["K", "ITEM"]
    bad = svc.classify_judgment_decisions(env, ruleset=RULESET_V7)["fresh"]
    assert "structural_evidence" not in bad and bad == evaluate_judgment(env["profile_json"], ruleset=RULESET_V7, ratified_contracts=[])
    assert ok["counts"]["failed"] == 0


# ───────────── ciclo completo ─────────────
def test_v7_activation_commits_finalizes_keeps_v6_as_previous_and_never_touches_profiles(world):
    seed_v6_finalized(world)
    v6 = {k: visible(*k) for k in ALL}
    profiles = {k: copy.deepcopy(svc._load_envelope(*k)["profile_json"]) for k in ALL}
    sidecar = copy.deepcopy(svc._load_envelope(*STRUCT)["structural_evidence"])
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATED and out["staged"] == len(ALL) and ja.state() == ja.ACTIVE and svc.active_ruleset() == RULESET_V7
    assert (ja.read_marker()["ruleset_version"], ja.read_marker()["previous_ruleset_version"]) == (RULESET_V7, RULESET_V6)
    for k in MODELS:                                                            # sin evidencia nueva: mismo nivel, fundamentos y fortalezas que v6
        v7 = visible(*k)
        assert (v7["status"], v7["level"], v7["level_rule"], v7["closure"]) == (v6[k]["status"], v6[k]["level"], v6[k]["level_rule"], v6[k]["closure"])
        assert [r["reason_id"] for r in v7["reasons"]] == [r["reason_id"] for r in v6[k]["reasons"]]
        assert [s["strength_id"] for s in v7["strengths"]] == [s["strength_id"] for s in v6[k]["strengths"]]
    assert visible(*STRUCT)["structural_evidence"]["e5"]["state"] == "DEFICIENT"
    fin = bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16], backup_dir=world.tmp / "bk7")
    assert fin["outcome"] == "FINALIZED" and ja.state() == ja.FINALIZED and svc.active_ruleset() == RULESET_V7
    restart()
    for k in ALL:
        env = svc._load_envelope(*k)
        assert env["judgment"]["ruleset_version"] == RULESET_V7 and "judgment_candidate" not in env and env["profile_json"] == profiles[k]
        assert env["previous_judgment"]["ruleset_version"] == RULESET_V6
    assert svc._load_envelope(*STRUCT)["structural_evidence"] == sidecar                       # el sidecar se conserva intacto


def test_v7_rollback_from_active_restores_v6_byte_for_byte(world):
    seed_v6_finalized(world)
    before = _bytes()
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATED
    rb = bf.rollback_ruleset_activation(operator="bob")
    assert rb["outcome"] == "ROLLED_BACK" and svc.active_ruleset() == RULESET_V6
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(*k) for k in ALL) and _bytes() == before


def test_a_finalized_v7_activation_is_reversible_by_restoring_the_anchored_backup(world):
    seed_v6_finalized(world)
    before = _bytes()
    out = activate(world)
    bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16], backup_dir=world.tmp / "bk7")
    rst = bf.restore_from_backup(world.tmp / "bk7", apply=True, confirm=out["backup"]["manifest_sha256"][:16], authorizer=_grant)
    assert rst["counts"].get("RESTORED") == len(ALL) and rst["counts"]["failed"] == 0 and rst["counts"]["conflicts"] == 0
    assert bf.rollback_ruleset_activation(operator="bob")["outcome"] == "ROLLED_BACK" and svc.active_ruleset() == RULESET_V6
    restart()
    assert _bytes() == before
    assert bf.activation_prerequisite(RULESET_V7)["met"] is True                              # se puede reintentar
