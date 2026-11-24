"""Activacion COORDINADA de judgment-rules/v6 (migracion ``judgment_rules_v6``): MISMO contrato que v5 (plan firmado, respaldo, ACTIVATING invisible, validacion, commit atomico,
finalizacion y rollback) + prerrequisito de ORIGEN (v5 FINALIZED) + EXCLUSION frente al circuito de actualizacion gobernada (aprobaciones en vuelo, guardas en registro/arranque/
autorizador/vias aseguradas) + re-validacion posterior al commit. Sin red ni Spaces; fixtures seudonimizados."""
from __future__ import annotations

import json

import pytest

from tests._judgment_cases import fresh
from tests.webapp.test_governed_assessment_durability import restart, world  # noqa: F401 (fixture reutilizada)
from tools.catalog.methodological_judgment import RULESET_V4, RULESET_V5, evaluate_judgment
from tools.catalog.methodological_judgment.contract import RULESET_V6
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import governed_update_approval as gua
from webapp.services import governed_update_requests as gur
from webapp.services import judgment_activation as ja
from webapp.services import migration_approval as ma

MODELS = {("Biogenesis", "AM_SELLOUT_GP"): "biogenesis_sellout_gp", ("Kensing", "4AM_GL"): "kensing_gl_line_item_raw",
          ("Biogenesis", "AM_DescuentosComerciales"): "biogenesis_descuentos_comerciales"}


@pytest.fixture(autouse=True)
def _isolated_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("DANWOR_GOVERNED_UPDATE_REQUESTS_DIR", str(tmp_path / "requests"))
    yield


def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="alice", operator="bob")


def seed_v5_finalized(world_, *, marker_state=ja.FINALIZED) -> None:
    """Padron con Juicio v5 vivo y el marcador de la activacion de v5 en ``marker_state`` (por defecto FINALIZED: el estado de produccion)."""
    for (client, am), alias in MODELS.items():
        p = fresh(alias)
        env = svc._envelope(client, am, status="CURRENT", profile=p, pool_signature="pool-x", versions={},
                            judgment=evaluate_judgment(p, ruleset=RULESET_V5, ratified_contracts=[]))
        svc._store_envelope(env)
    if marker_state is not None:
        ja.write_marker(marker_state, ruleset_version=RULESET_V5, previous_ruleset_version=RULESET_V4, plan_digest="b" * 64, operator="owner")
    restart()


def visible(client, am) -> dict:
    restart()
    return svc._persisted_judgment(svc._load_envelope(client, am))


def activate(world_, **kw):
    return bf.secured_activate_ruleset(target_ruleset=RULESET_V6, authorizer=kw.pop("authorizer", _grant), backup_dir=kw.pop("backup_dir", world_.tmp / "bk6"), operator="bob",
                                       allow_local_only=True, remote_enabled=lambda: False, **kw)


def _in_flight_request(state="STARTED"):
    rec = {"schema": gur.SCHEMA, "request_id": "a" * 32, "client_dir": "Biogenesis", "terminal_am": "AM_SELLOUT_GP", "status": gur.OPEN,
           "approval": {"approval_id": "b" * 32, "state": state}}
    gur._write(rec)
    return rec


# ───────────── migracion y prerrequisito ─────────────
def test_v6_has_its_own_signed_activation_migration():
    assert bf._ACTIVATION_MIGRATIONS[RULESET_V6] == bf.JUDGMENT_V6_ACTIVATION_MIGRATION == "judgment_rules_v6"
    assert bf._ACTIVATION_MIGRATIONS[RULESET_V5] == bf.JUDGMENT_V5_ACTIVATION_MIGRATION       # v5 intacta


@pytest.mark.parametrize("marker_state", [None, ja.ROLLED_BACK])
def test_v6_activation_requires_v5_finalized_and_writes_nothing_otherwise(world, marker_state):
    seed_v5_finalized(world, marker_state=marker_state)
    before = ja.read_marker()
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATION_REFUSED and out["reason"] == bf.PREREQUISITE_NOT_MET
    assert out["prerequisite"]["met"] is False and out["prerequisite"]["required"].startswith(f"{RULESET_V5} FINALIZED")
    assert ja.read_marker() == before
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(c, a) for c, a in MODELS)


def test_v6_activation_is_refused_while_v5_is_still_in_transition(world):
    seed_v5_finalized(world, marker_state=ja.ACTIVE)
    assert activate(world)["reason"] == "ACTIVATION_IN_TRANSITION"


def test_the_v6_dry_run_is_the_signed_global_plan_and_writes_nothing(world):
    seed_v5_finalized(world)
    marker = ja.read_marker()
    rep = bf.backfill_judgment(apply=False, target_ruleset=RULESET_V6)
    assert rep["plan"]["migration"] == bf.JUDGMENT_V6_ACTIVATION_MIGRATION and rep["visible_ruleset"] == RULESET_V5
    assert rep["activation_prerequisite"]["met"] is True
    assert {i["operation"] for i in rep["plan"]["items"]} == {"REPLACE_JUDGMENT"} and len(rep["plan"]["items"]) == len(MODELS)
    assert rep["counts"]["level_changes"] == 0 and rep["level_changes"] == []                      # los casos de referencia no cambian de nivel en v6
    assert ja.read_marker() == marker
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(c, a) for c, a in MODELS)


# ───────────── ciclo completo ─────────────
def test_v6_activation_commits_finalizes_and_keeps_v5_as_previous(world):
    seed_v5_finalized(world)
    v5 = {k: visible(*k) for k in MODELS}
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATED and out["staged"] == len(MODELS) and ja.state() == ja.ACTIVE and svc.active_ruleset() == RULESET_V6
    m = ja.read_marker()
    assert (m["ruleset_version"], m["previous_ruleset_version"]) == (RULESET_V6, RULESET_V5)
    for k in MODELS:
        v6 = visible(*k)
        assert v6["ruleset_version"] == RULESET_V6
        assert (v6["status"], v6["level"], v6["level_rule"], v6["closure"]) == (v5[k]["status"], v5[k]["level"], v5[k]["level_rule"], v5[k]["closure"])
        assert [r["reason_id"] for r in v6["reasons"]] == [r["reason_id"] for r in v5[k]["reasons"]]
        assert [s["strength_id"] for s in v6["strengths"]] == [s["strength_id"] for s in v5[k]["strengths"]]
    assert bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16])["reason"] == "BACKUP_DIR_REQUIRED"
    fin = bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16], backup_dir=world.tmp / "bk6")
    assert fin["outcome"] == "FINALIZED" and ja.state() == ja.FINALIZED and svc.active_ruleset() == RULESET_V6
    assert ja.read_marker()["activation_backup"]["manifest_sha256"] == out["backup"]["manifest_sha256"]
    restart()
    for (client, am) in MODELS:
        env = svc._load_envelope(client, am)
        assert "judgment_candidate" not in env and env["judgment"]["ruleset_version"] == RULESET_V6
        prev = v5[(client, am)]
        assert env["previous_judgment"] == {"status": prev["status"], "level": prev["level"], "level_rule": prev["level_rule"], "ruleset_version": RULESET_V5}
        assert env["profile_json"] == fresh(MODELS[(client, am)])                                  # el perfil (y sus firmas) no se tocan


def test_finalize_refuses_a_backup_that_is_not_the_anchored_one(world, tmp_path):
    seed_v5_finalized(world)
    out = activate(world)
    other = world.tmp / "other-bk"
    other.mkdir()
    (other / "manifest.json").write_text(json.dumps({"migration": "x"}), encoding="utf-8")
    r = bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16], backup_dir=other)
    assert r["outcome"] == bf.ACTIVATION_REFUSED and r["reason"] in ("BACKUP_UNREADABLE", "BACKUP_MISMATCH") and ja.state() == ja.ACTIVE


def test_a_finalized_v6_activation_is_reversible_by_restoring_the_anchored_backup(world):
    """Reversion de una activacion FINALIZED: --restore-backup del respaldo anclado (gracias a los COMMITTED de la finalizacion) + rollback del marcador."""
    seed_v5_finalized(world)
    paths = {k: svc.governed_path(*k) for k in MODELS}
    original = {k: p.read_bytes() for k, p in paths.items()}
    out = activate(world)
    bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16], backup_dir=world.tmp / "bk6")
    seal = out["backup"]["manifest_sha256"][:16]
    rst = bf.restore_from_backup(world.tmp / "bk6", apply=True, confirm=seal, authorizer=_grant)
    assert rst["counts"].get("RESTORED") == len(MODELS) and rst["counts"]["failed"] == 0 and rst["counts"]["conflicts"] == 0
    rb = bf.rollback_ruleset_activation(operator="bob")
    assert rb["outcome"] == "ROLLED_BACK" and svc.active_ruleset() == RULESET_V5
    assert all(paths[k].read_bytes() == original[k] for k in MODELS)                                  # bytes originales v5
    # y el padron vuelve a cumplir el prerrequisito: la activacion se puede reintentar
    assert bf.activation_prerequisite(RULESET_V6)["met"] is True


def test_an_aborted_v6_attempt_can_be_retried_without_touching_the_marker(world, monkeypatch):
    seed_v5_finalized(world)
    real, calls = svc.apply_judgment_candidate, []

    def flaky(env, cand):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk")
        return real(env, cand)
    monkeypatch.setattr(svc, "apply_judgment_candidate", flaky)
    assert activate(world)["reason"] == "STAGING_FAILED" and ja.state() == ja.ROLLED_BACK
    monkeypatch.setattr(svc, "apply_judgment_candidate", real)
    pre = bf.activation_prerequisite(RULESET_V6)
    assert pre["met"] is True and pre["marker_state"] == ja.ROLLED_BACK and pre["padron"]["not_consolidated"] == []
    assert activate(world, backup_dir=world.tmp / "bk6-retry")["outcome"] == bf.ACTIVATED


def test_the_prerequisite_refuses_a_mixed_padron_even_with_a_valid_marker(world):
    seed_v5_finalized(world)
    env = dict(svc._load_envelope("Kensing", "4AM_GL"))
    env["judgment"] = {**env["judgment"], "ruleset_version": RULESET_V4}                              # un sobre que no quedo consolidado en v5
    svc._store_envelope(env)
    restart()
    pre = bf.activation_prerequisite(RULESET_V6)
    assert pre["met"] is False and pre["padron"]["not_consolidated"] == [["Kensing", "4AM_GL"]]
    assert activate(world)["reason"] == bf.PREREQUISITE_NOT_MET


def test_v6_rollback_from_active_restores_v5_instantly(world):
    seed_v5_finalized(world)
    activate(world)
    rb = bf.rollback_ruleset_activation(operator="bob")
    assert rb["outcome"] == "ROLLED_BACK" and rb["candidates_withdrawn"] == len(MODELS) and svc.active_ruleset() == RULESET_V5
    for k in MODELS:
        assert visible(*k)["ruleset_version"] == RULESET_V5


def test_a_staging_failure_rolls_back_to_v5_without_exposing_v6(world, monkeypatch):
    seed_v5_finalized(world)
    real, calls = svc.apply_judgment_candidate, []

    def flaky(env, cand):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk")
        return real(env, cand)
    monkeypatch.setattr(svc, "apply_judgment_candidate", flaky)
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATION_ABORTED and out["reason"] == "STAGING_FAILED" and svc.active_ruleset() == RULESET_V5
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(c, a) for c, a in MODELS)


def test_a_concurrent_write_after_validation_is_caught_after_commit_and_rolled_back(world, monkeypatch):
    """Un escritor que paso sus guardas ANTES de ACTIVATING reescribe un sobre entre la validacion y el commit: la re-validacion post-commit lo detecta y revierte."""
    seed_v5_finalized(world)
    real = ja.write_marker

    def racing(state_, **kw):
        if state_ == ja.ACTIVE:
            e = dict(svc._load_envelope("Kensing", "4AM_GL"))
            e.pop("judgment_candidate", None)
            svc._store_envelope(e)
        return real(state_, **kw)
    monkeypatch.setattr(ja, "write_marker", racing)
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATION_ABORTED and out["reason"] == "POST_COMMIT_VALIDATION_FAILED"
    assert ja.state() == ja.ROLLED_BACK and svc.active_ruleset() == RULESET_V5
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(c, a) for c, a in MODELS)


# ───────────── exclusion con el circuito de actualizacion gobernada ─────────────
@pytest.mark.parametrize("state", ["REGISTERED", "VERIFYING", "STARTED", "RECONCILIATION_REQUIRED", "INTERRUPTED"])
def test_activation_is_refused_while_a_governed_update_approval_is_in_flight(world, state):
    seed_v5_finalized(world)
    _in_flight_request(state)
    out = activate(world)
    assert out["reason"] == bf.GOVERNED_UPDATES_IN_FLIGHT and out["approvals"][0]["approval_state"] == state
    assert ja.state() == ja.FINALIZED and svc.active_ruleset() == RULESET_V5


@pytest.mark.parametrize("state", ["APPLIED", "DENIED", "ABSTAINED", "FAILED", "NOT_APPLIED"])
def test_terminal_approvals_do_not_block_the_activation(world, state):
    seed_v5_finalized(world)
    _in_flight_request(state)
    assert activate(world)["outcome"] == bf.ACTIVATED


def test_an_approval_that_appears_after_the_first_check_still_blocks_before_activating(world):
    """Segunda lectura inmediatamente antes de publicar ACTIVATING: el autorizador de la activacion corre entre ambas."""
    seed_v5_finalized(world)

    def grant_then_someone_registers(migration, plan):
        _in_flight_request("REGISTERED")
        return _grant(migration, plan)
    out = activate(world, authorizer=grant_then_someone_registers)
    assert out["reason"] == bf.GOVERNED_UPDATES_IN_FLIGHT and ja.state() == ja.FINALIZED


@pytest.fixture
def activating(world):
    seed_v5_finalized(world)
    ja.write_marker(ja.ACTIVATING, ruleset_version=RULESET_V6, previous_ruleset_version=RULESET_V5, plan_digest="c" * 64, operator="bob")
    before = gua.readiness()
    gua._set_readiness(gua.READINESS_READY)
    yield world
    gua._set_readiness(before["state"], before.get("reason"))


def test_no_approval_is_registered_during_a_global_activation(activating):
    rec = {"request_id": "d" * 32, "client_dir": "Biogenesis", "terminal_am": "AM_SELLOUT_GP"}
    with pytest.raises(gua.ApprovalRefused) as e:
        gua.register(rec, {"email": "x@danwor.com"}, "OWNER", ["S"], idempotency_key="k")
    assert e.value.code == gua.RULESET_ACTIVATION_IN_PROGRESS


def test_no_registered_approval_starts_during_a_global_activation_and_it_is_not_consumed(activating):
    rec = _in_flight_request("REGISTERED")
    out = gua.start(rec, rec["approval"], compute=lambda *a, **k: pytest.fail("no debe recalcular"), execute=lambda *a, **k: pytest.fail("no debe ejecutar"))
    assert out == gua.RULESET_ACTIVATION_IN_PROGRESS
    assert gur.load(rec["request_id"])["approval"]["state"] == "REGISTERED"


def test_the_hub_authorizer_denies_right_before_writing_during_a_global_activation(activating):
    rec = _in_flight_request("STARTED")
    gua._running.add(rec["approval"]["approval_id"])
    try:
        auth = gua.hub_authorizer(rec["request_id"], rec["approval"]["approval_id"], "e" * 64)("regenerate_selected", {"digest": "e" * 64})
    finally:
        gua._running.discard(rec["approval"]["approval_id"])
    assert auth.granted is False and auth.reason == gua.RULESET_ACTIVATION_IN_PROGRESS


def test_a_refused_execution_settles_as_abstained_not_failed():
    assert gua._outcome_state({"outcome": bf.RULESET_ACTIVATION_IN_PROGRESS, "applied": False}) == (gua.ABSTAINED, bf.RULESET_ACTIVATION_IN_PROGRESS)


def test_individual_secured_paths_refuse_after_authorization_during_a_global_activation(activating, monkeypatch):
    seen = []
    monkeypatch.setattr(bf, "plan_selective_regeneration", lambda *a, **k: {"plan": {"digest": "f" * 64, "clients": ["Biogenesis"], "items": [{"client": "Biogenesis", "root": "AM_SELLOUT_GP"}]},
                                                                             "decision_snapshot": {}, "records": []})
    out = bf.secured_regenerate_selected([], ["AM_SELLOUT_GP"], authorizer=lambda m, p: seen.append(m) or _grant(m, p), backup_dir=activating.tmp / "bk-ind",
                                         allow_local_only=True, remote_enabled=lambda: False)
    assert seen and out["outcome"] == bf.RULESET_ACTIVATION_IN_PROGRESS and out["writes"] == 0
    assert not (activating.tmp / "bk-ind").exists()                                                # ni siquiera el respaldo


def test_guards_release_after_finalize_or_rollback(world):
    seed_v5_finalized(world)
    activate(world)
    assert gua._activation_in_transition() is True
    bf.rollback_ruleset_activation(operator="bob")
    assert gua._activation_in_transition() is False and bf._activation_in_transition() is False
