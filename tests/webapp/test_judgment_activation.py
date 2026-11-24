"""Activacion COORDINADA de judgment-rules/v5 (``ruleset-activation/v1``): despliegue con v5 inactivo (conducta identica), ACTIVATING invisible, commit atomico a ACTIVE para gobernado
y ad hoc a la vez, validacion previa, abortos sin exposicion inconsistente, finalizacion (una sola version viva + ``previous_judgment``) y rollback instantaneo. Sin red ni Spaces."""
from __future__ import annotations

import json

import pytest

from tests._judgment_cases import fresh
from tests.webapp.test_governed_assessment_durability import restart, world  # noqa: F401 (fixture reutilizada)
from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION, RULESET_V4, RULESET_V5, evaluate_judgment
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import judgment_activation as ja
from webapp.services import migration_approval as ma

MODELS = {("Biogenesis", "AM_SELLOUT_GP"): "biogenesis_sellout_gp", ("Kensing", "4AM_GL"): "kensing_gl_line_item_raw",
          ("Biogenesis", "AM_DescuentosComerciales"): "biogenesis_descuentos_comerciales"}


def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="alice", operator="bob")


def seed_v4(world_) -> None:
    for (client, am), alias in MODELS.items():
        p = fresh(alias)
        env = svc._envelope(client, am, status="CURRENT", profile=p, pool_signature="pool-x", versions={}, judgment=evaluate_judgment(p, ruleset=RULESET_V4, ratified_contracts=[]))
        svc._store_envelope(env)
    restart()


def visible(client, am) -> dict:
    restart()
    env = svc._load_envelope(client, am)
    return svc._persisted_judgment(env)


def activate(world_, **kw):
    return bf.secured_activate_ruleset(target_ruleset=RULESET_V5, authorizer=kw.pop("authorizer", _grant), backup_dir=kw.pop("backup_dir", world_.tmp / "bk"), operator="bob",
                                       allow_local_only=True, remote_enabled=lambda: False, **kw)


def test_without_marker_the_visible_ruleset_is_the_code_default_and_v5_is_inactive(world):
    assert ja.read_marker() is None and svc.active_ruleset() == CURRENT_RULESET_VERSION == RULESET_V4
    seed_v4(world)
    assert visible("Biogenesis", "AM_SELLOUT_GP")["ruleset_version"] == RULESET_V4
    assert svc.provisional_judgment(fresh("biogenesis_sellout_gp"))["ruleset_version"] == RULESET_V4        # ad hoc tambien v4


def test_an_invalid_or_unknown_marker_fails_safe_to_the_default(world):
    seed_v4(world)
    p = ja.marker_path()
    p.write_text("{not json", encoding="utf-8")
    assert svc.active_ruleset() == RULESET_V4
    p.write_text(json.dumps({"version": ja.ACTIVATION_VERSION, "state": "ACTIVE", "ruleset_version": "judgment-rules/v99", "previous_ruleset_version": RULESET_V4}), encoding="utf-8")
    assert svc.active_ruleset() == RULESET_V4


def test_activating_stages_candidates_that_are_invisible_for_governed_and_ad_hoc(world):
    seed_v4(world)
    ja.write_marker(ja.ACTIVATING, ruleset_version=RULESET_V5, previous_ruleset_version=RULESET_V4, plan_digest="d" * 64, operator="bob")
    env = svc._load_envelope("Biogenesis", "AM_SELLOUT_GP")
    svc.apply_judgment_candidate(env, evaluate_judgment(fresh("biogenesis_sellout_gp"), ruleset=RULESET_V5, ratified_contracts=[]))
    assert svc.active_ruleset() == RULESET_V4
    assert visible("Biogenesis", "AM_SELLOUT_GP")["ruleset_version"] == RULESET_V4
    assert svc.provisional_judgment(fresh("biogenesis_sellout_gp"))["ruleset_version"] == RULESET_V4


def test_activation_commits_atomically_for_every_model_and_for_ad_hoc_at_once(world):
    seed_v4(world)
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATED and out["staged"] == len(MODELS) and ja.state() == ja.ACTIVE
    assert svc.active_ruleset() == RULESET_V5
    for (client, am) in MODELS:
        assert visible(client, am)["ruleset_version"] == RULESET_V5
    assert visible("Biogenesis", "AM_SELLOUT_GP")["level"] == "PARCIAL"                                           # PRO-MAT-02
    assert svc.provisional_judgment(fresh("biogenesis_sellout_gp"))["ruleset_version"] == RULESET_V5
    # el Juicio previo sigue intacto (rollback instantaneo posible) hasta la finalizacion
    restart()
    env = svc._load_envelope("Biogenesis", "AM_SELLOUT_GP")
    assert env["judgment"]["ruleset_version"] == RULESET_V4 and env["judgment_candidate"]["ruleset_version"] == RULESET_V5


def test_a_cached_web_process_sees_the_commit_without_restart(world):
    seed_v4(world)
    svc._load_envelope("Biogenesis", "AM_SELLOUT_GP")                     # el proceso web cachea el sobre en memoria (sin candidato)
    assert svc._persisted_judgment(svc._load_envelope("Biogenesis", "AM_SELLOUT_GP"))["ruleset_version"] == RULESET_V4
    activate(world)
    env = svc._load_envelope("Biogenesis", "AM_SELLOUT_GP")                 # SIN restart: el cambio de marcador invalida la cache
    assert svc._persisted_judgment(env)["ruleset_version"] == RULESET_V5


def test_any_failure_before_commit_rolls_back_without_ever_exposing_v5(world, monkeypatch):
    seed_v4(world)
    real = svc.apply_judgment_candidate
    calls = []

    def flaky(env, cand):
        calls.append(env["terminal_am"])
        if len(calls) == 2:
            raise OSError("disk")
        return real(env, cand)
    monkeypatch.setattr(svc, "apply_judgment_candidate", flaky)
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATION_ABORTED and out["reason"] == "STAGING_FAILED" and ja.state() == ja.ROLLED_BACK
    assert svc.active_ruleset() == RULESET_V4
    restart()
    for (client, am) in MODELS:
        env = svc._load_envelope(client, am)
        assert "judgment_candidate" not in env and svc._persisted_judgment(env)["ruleset_version"] == RULESET_V4


def test_validation_refuses_active_when_an_envelope_changed_during_staging(world, monkeypatch):
    seed_v4(world)
    real = svc.apply_judgment_candidate

    def regenerated_meanwhile(env, cand):                                  # otro proceso regenera el sobre (sin candidato) justo despues del staging
        r = real(env, cand)
        if env["terminal_am"] == "AM_DescuentosComerciales":
            e = dict(svc._load_envelope(env["client"], env["terminal_am"]))
            e.pop("judgment_candidate", None)
            svc._store_envelope(e)
        return r
    monkeypatch.setattr(svc, "apply_judgment_candidate", regenerated_meanwhile)
    out = activate(world)
    assert out["outcome"] == bf.ACTIVATION_ABORTED and out["reason"] == "VALIDATION_FAILED" and svc.active_ruleset() == RULESET_V4


def test_activation_is_refused_without_authorization_or_backup_and_writes_nothing(world):
    seed_v4(world)
    assert activate(world, authorizer=lambda m, p: ma.DENIED("NO_APPROVAL"))["reason"] == bf.DENIED
    assert activate(world, backup_dir=None)["reason"] == bf.BACKUP_NOT_GUARANTEED
    assert ja.read_marker() is None
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(c, a) for c, a in MODELS)


def test_finalize_leaves_a_single_live_judgment_with_only_a_scalar_previous_level(world):
    seed_v4(world)
    out = activate(world)
    assert bf.finalize_ruleset_activation(operator="bob", confirm_plan="short")["reason"] == "CONFIRMATION_REQUIRED"
    assert bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16])["reason"] == "BACKUP_DIR_REQUIRED"   # ancla de reversibilidad
    fin = bf.finalize_ruleset_activation(operator="bob", confirm_plan=out["plan"]["digest"][:16], backup_dir=world.tmp / "bk")
    assert fin["outcome"] == "FINALIZED" and fin["finalized"] == len(MODELS) and ja.state() == ja.FINALIZED and fin["committed_journaled"] == len(MODELS)
    restart()
    env = svc._load_envelope("Biogenesis", "AM_SELLOUT_GP")
    assert "judgment_candidate" not in env and env["judgment"]["ruleset_version"] == RULESET_V5
    assert env["previous_judgment"] == {"status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-05", "ruleset_version": RULESET_V4}
    assert svc.active_ruleset() == RULESET_V5


def test_rollback_of_an_active_activation_is_instant_and_complete(world):
    seed_v4(world)
    activate(world)
    rb = bf.rollback_ruleset_activation(operator="bob")
    assert rb["outcome"] == "ROLLED_BACK" and rb["candidates_withdrawn"] == len(MODELS) and svc.active_ruleset() == RULESET_V4
    restart()
    for (client, am) in MODELS:
        env = svc._load_envelope(client, am)
        assert "judgment_candidate" not in env and svc._persisted_judgment(env)["ruleset_version"] == RULESET_V4
    assert bf.rollback_ruleset_activation(operator="bob")["outcome"] == "NOTHING_TO_DO"


def test_a_second_activation_is_refused_while_one_is_in_transition(world):
    seed_v4(world)
    activate(world)
    again = activate(world, backup_dir=world.tmp / "bk2")
    assert again["reason"] == "ACTIVATION_IN_TRANSITION"


def test_the_global_dry_run_is_the_ab_plan_and_writes_nothing(world):
    seed_v4(world)
    rep = bf.backfill_judgment(apply=False, target_ruleset=RULESET_V5)
    assert rep["plan"]["migration"] == bf.JUDGMENT_V5_ACTIVATION_MIGRATION and rep["target_ruleset"] == RULESET_V5 and rep["visible_ruleset"] == RULESET_V4
    changes = {(c["client"], c["terminal_am"]): (c["from"], c["to"]) for c in rep["level_changes"]}
    assert changes == {("Biogenesis", "AM_SELLOUT_GP"): (["NOT_DETERMINABLE", None, "LVL-05"], ["DETERMINED", "PARCIAL", "LVL-02"])}
    assert ja.read_marker() is None
    with pytest.raises(ValueError):
        bf.backfill_judgment(apply=True, target_ruleset=RULESET_V5)


# ─────────── Hotfix: respaldo DURABLE externo (Spaces) ANTES de ACTIVATING y de cualquier escritura (adopcion PRODUCTIVA) ───────────
def _force_local_backup(monkeypatch):
    """``create_backup`` real pero sin exigir remoto (el test no tiene Spaces real): el respaldo LOCAL se crea igual; aisla la prueba al orden del publish durable."""
    real_cb = bf.gb.create_backup
    monkeypatch.setattr(bf.gb, "create_backup", lambda dest, targets, **kw: real_cb(dest, targets, **{**kw, "require_remote": False, "remote_read": None}))


def _productive(world_, **kw):
    return bf.secured_activate_ruleset(target_ruleset=RULESET_V5, authorizer=_grant, backup_dir=world_.tmp / kw.pop("bk", "bkprod"),
                                       operator="bob", allow_local_only=False, remote_enabled=kw.pop("remote_enabled", lambda: True), **kw)


def test_productive_activation_publishes_durable_backup_before_marker_and_before_any_envelope_write(world, monkeypatch):
    seed_v4(world)
    events, pub = [], []
    real_cb = bf.gb.create_backup
    monkeypatch.setattr(bf.gb, "create_backup", lambda dest, targets, **kw: (events.append("create_backup"), real_cb(dest, targets, **{**kw, "require_remote": False, "remote_read": None}))[1])

    def fake_publish(backup_dir, migration, run_id, approval=None):
        events.append("publish"); pub.append((migration, run_id))
        return {"run_id": run_id, "sealed": True, "prefix": f"migration-backups/{migration}/{run_id}"}
    monkeypatch.setattr(bf.dbk, "publish", fake_publish)
    real_marker = ja.write_marker
    monkeypatch.setattr(ja, "write_marker", lambda state, **k: (events.append(f"marker:{state}"), real_marker(state, **k))[1])
    real_stage = svc.apply_judgment_candidate
    monkeypatch.setattr(svc, "apply_judgment_candidate", lambda env, cand: (events.append("stage"), real_stage(env, cand))[1])

    out = _productive(world)
    assert out["outcome"] == bf.ACTIVATED and ja.state() == ja.ACTIVE
    mig, run_id = pub[0]
    # 1) publicacion durable DESPUES del respaldo local y ANTES del marcador y de cualquier escritura de sobre
    assert events.index("create_backup") < events.index("publish") < events.index(f"marker:{ja.ACTIVATING}")
    assert events.index("publish") < events.index("stage")
    # 2) sello/manifiesto durable atados al digest del plan correcto + trazabilidad en el resultado
    assert mig == bf.JUDGMENT_V5_ACTIVATION_MIGRATION and run_id.split("-")[-1] == out["plan"]["digest"][:12]
    assert out["backup"]["remote"] == {"run_id": run_id, "sealed": True, "prefix": f"migration-backups/{mig}/{run_id}"}
    # 3) el comportamiento de scoring no cambia (mismo veredicto v5 que el camino local)
    assert visible("Biogenesis", "AM_SELLOUT_GP")["level"] == "PARCIAL"


def test_productive_publish_failure_aborts_with_zero_governed_modifications(world, monkeypatch):
    seed_v4(world)
    _force_local_backup(monkeypatch)
    marker_calls, stage_calls = [], []
    monkeypatch.setattr(bf.dbk, "publish", lambda *a, **k: (_ for _ in ()).throw(bf.dbk.DurableBackupError("REMOTE_WRITE_FAILED", "simulado")))
    monkeypatch.setattr(ja, "write_marker", lambda *a, **k: marker_calls.append(a))
    monkeypatch.setattr(svc, "apply_judgment_candidate", lambda e, c: stage_calls.append(1))
    out = _productive(world, bk="bkfail")
    assert out["outcome"] == bf.ACTIVATION_REFUSED and out["reason"] == bf.BACKUP_NOT_GUARANTEED
    assert marker_calls == [] and stage_calls == []                                   # ni marcador ni staging
    assert ja.read_marker() is None
    restart()
    for (client, am) in MODELS:                                                        # cero modificaciones gobernadas
        env = svc._load_envelope(client, am)
        assert "judgment_candidate" not in env and svc._persisted_judgment(env)["ruleset_version"] == RULESET_V4


def test_productive_activation_without_remote_is_refused_and_never_publishes(world, monkeypatch):
    seed_v4(world)
    pub = []
    monkeypatch.setattr(bf.dbk, "publish", lambda *a, **k: pub.append(1))
    out = _productive(world, remote_enabled=lambda: False)
    assert out["outcome"] == bf.ACTIVATION_REFUSED and out["reason"] == bf.REMOTE_REQUIRED          # productivo sin remoto durable => jamas activa
    assert pub == [] and ja.read_marker() is None
    restart()
    assert all("judgment_candidate" not in svc._load_envelope(c, a) for c, a in MODELS)


def test_local_only_test_mode_stays_separate_and_activates_without_durable_publish(world, monkeypatch):
    seed_v4(world)
    pub = []
    monkeypatch.setattr(bf.dbk, "publish", lambda *a, **k: pub.append(1))
    out = activate(world)                                                              # allow_local_only=True, remote_enabled=False
    assert out["outcome"] == bf.ACTIVATED and pub == [] and ja.state() == ja.ACTIVE    # modo local: respaldo solo local, sin publicacion durable
