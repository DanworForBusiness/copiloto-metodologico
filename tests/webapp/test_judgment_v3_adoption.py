"""
B5.3 — adopcion controlada de ``judgment-rules/v3``: instantanea verificable de decisiones de gobierno inyectada por un OPERADOR EXPLICITO, trazabilidad por evento en el Juicio derivado, firma de
entrada determinista, invalidacion (revocacion / evidencia STALE => PENDIENTE DE REEVALUACION sin reescritura silenciosa) y apply asegurado (aprobacion firmada + respaldo + diario).

Sin red ni Spaces: el registro se inyecta en memoria (``judgment_decisions.Snapshot``).
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from tests._judgment_cases import fresh
from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401 (fixture reutilizada)
from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION, RULESET_V3, evaluate_judgment
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import governed_decisions as gd
from webapp.services import judgment_decisions as jd
from webapp.services import migration_approval as ma

CLIENT, AM = "Biogenesis", "AM_SELLOUT_GP"
CRITICAL = ["ART_0012", "ART_0013", "ART_0014", "ART_0015"]
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def gp_profile() -> dict:
    p = fresh("biogenesis_sellout_gp")
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf_kind"] == "REMOTE_IMPORTED":
            r["remote_connection"], r["remote_entities"] = "SQL_CONN", [f'"dbo"."{r["leaf"]}"']
    return p


def leaf_rec(p: dict, leaf: str) -> dict:
    return next(r for r in p["canonical_evidence"]["source_contract_classes"]["leaves"] if r["leaf"] == leaf)


def tid_of(p: dict, leaf: str) -> str:
    return gd.cds_technical_id(gd.cds_identity(leaf_rec(p, leaf)))


def ratified(p: dict, leaf: str, *, event_id: str | None = None, client: str = CLIENT) -> dict:
    return gd.build_ratification(event_id=event_id or f"gd-{leaf}", subject={"client": client, "technical_id": tid_of(p, leaf)}, rationale="contrato gobernado", actor="owner@danwor.com",
                                 decided_at="2026-10-01T00:00:00Z", evidence_ref_override=gd.cds_evidence_ref(leaf_rec(p, leaf)))


def revoked(p: dict, leaf: str) -> dict:
    return gd.build_revocation(event_id=f"gd-rev-{leaf}", subject={"client": CLIENT, "technical_id": tid_of(p, leaf)}, revokes_event_id=f"gd-{leaf}", rationale="error",
                               actor="owner@danwor.com", decided_at="2026-10-02T00:00:00Z")


def snap(*events: dict, mode: str = jd.MODE_REGISTRY) -> jd.Snapshot:
    return jd.Snapshot(mode, NOW, {CLIENT: tuple(events)} if events else {})


def seed(world_, profile: dict | None = None, *, judgment: dict | None = None) -> dict:
    """Siembra un sobre gobernado con el estado de PRODUCCION previo a la adopcion: perfil real (fixture) + Juicio v2 persistido."""
    p = profile or gp_profile()
    j = judgment if judgment is not None else evaluate_judgment(p)                          # judgment-rules/v2 (reproducible: el motor por defecto no cambia)
    env = svc._envelope(CLIENT, AM, status="CURRENT", profile=p, pool_signature="pool-x", versions={}, judgment=j)
    svc._store_envelope(env)
    restart()
    return env


def stored() -> dict:
    return next(e for e, _p in svc.iter_governed_envelopes() if e["terminal_am"] == AM)


def raw_bytes(world_) -> bytes:
    return files(world_)[0].read_bytes()


ALL4 = lambda p: [ratified(p, l) for l in CRITICAL]                                         # noqa: E731


# ───────────── motor: v2 reproducible, v3 inyectado, entrada firmada ─────────────
def test_v2_stays_the_engine_default_byte_identical_and_unknown_rulesets_or_v3_variants_are_rejected():
    p = gp_profile()
    a, b = evaluate_judgment(p), evaluate_judgment(copy.deepcopy(p), ruleset="judgment-rules/v2")
    assert a["output_signature"] == b["output_signature"] and a["ruleset_version"] == "judgment-rules/v2" and "decision_inputs" not in a
    with pytest.raises(ValueError, match="ruleset"):
        evaluate_judgment(p, ruleset="judgment-rules/v9")
    with pytest.raises(ValueError, match="variantes"):
        evaluate_judgment(p, ruleset=RULESET_V3, variants=["T4_HAR"])


def test_v3_resolves_only_lvl05_records_the_exact_events_and_signs_its_decision_inputs():
    p = gp_profile()
    s = snap(*ALL4(p))
    v3, rep = jd.derive(p, CLIENT, s)
    v2 = evaluate_judgment(p)
    assert (v2["status"], v2["level_rule"]) == ("NOT_DETERMINABLE", "LVL-05")
    assert (v3["ruleset_version"], v3["status"], v3["level"], v3["level_rule"]) == (CURRENT_RULESET_VERSION, "DETERMINED", "ADECUADA", "LVL-03")
    assert {x["rule_id"] for x in v3["strengths"]} == {x["rule_id"] for x in v2["strengths"]}               # sin fortalezas nuevas
    assert [(r["rule_id"], r["classification"]) for r in v3["reasons"]] == [(r["rule_id"], r["classification"]) for r in v2["reasons"]]
    inputs = v3["decision_inputs"]["ratified_contracts"]
    assert [i["event_id"] for i in inputs] == [f"gd-{l}" for l in CRITICAL]
    assert all(i["technical_id"] and i["evidence_ref"]["algo"] == "sha256" and i["evidence_ref"]["digest"] == i["digest"] for i in inputs)
    disp = [d for d in v3["dispositions_applied"] if d.get("decision") == "GOVERNED_CONTRACT_RATIFIED"]
    assert [d["status"] for d in disp] == ["APPLIED"] * 4 and [d["event_id"] for d in disp] == [i["event_id"] for i in inputs]


def test_a_different_event_or_no_event_changes_the_signed_input_and_the_output_signature_and_replay_reproduces_it():
    p = gp_profile()
    a, _ = jd.derive(p, CLIENT, snap(*ALL4(p)))
    b, _ = jd.derive(p, CLIENT, snap(*[ratified(p, l, event_id=f"gd-other-{l}") for l in CRITICAL]))
    none, _ = jd.derive(p, CLIENT, jd.empty_snapshot(NOW))
    assert a["decision_inputs"]["signature"] != b["decision_inputs"]["signature"] != none["decision_inputs"]["signature"]
    assert len({a["output_signature"], b["output_signature"], none["output_signature"]}) == 3
    replay = evaluate_judgment(p, ruleset=CURRENT_RULESET_VERSION, ratified_contracts=jd.consumed(a))                   # replay de la MISMA entrada => MISMO Juicio (revalidar no relee el registro)
    assert replay["output_signature"] == a["output_signature"]


def test_events_of_other_clients_or_unrelated_contracts_do_not_change_the_judgment_or_its_signature():
    p = gp_profile()
    base, _ = jd.derive(p, CLIENT, jd.empty_snapshot(NOW))
    other = jd.Snapshot(jd.MODE_REGISTRY, NOW, {"Kensing": (ratified(p, "ART_0012", client="Kensing"),), CLIENT: (ratified(p, "ART_0007"),)})   # otro cliente + contrato fisico no candidato
    again, rep = jd.derive(p, CLIENT, other)
    assert again["output_signature"] == base["output_signature"] and rep["ratified_contracts"] == []


def test_a_valid_ratification_that_resolves_nothing_for_this_model_is_not_part_of_its_judgment_input_or_signature():
    p = gp_profile()
    base, _ = jd.derive(p, CLIENT, jd.empty_snapshot(NOW))
    noncritical = snap(ratified(p, "ART_0011"), ratified(p, "ART_0016"))                      # indeterminadas SIN exposicion decision-critica en este modelo (NO_EFFECT)
    j, rep = jd.derive(p, CLIENT, noncritical)
    assert [x["leaf"] for x in rep["ratified_contracts"]] == ["ART_0011", "ART_0016"]         # el enlace las ve y son validas...
    assert j["decision_inputs"]["ratified_contracts"] == [] and j["output_signature"] == base["output_signature"]   # ...pero el Juicio ni su firma cambian (sin churn)
    mixed, _ = jd.derive(p, CLIENT, snap(*ALL4(p), ratified(p, "ART_0011")))
    assert [i["leaf"] for i in mixed["decision_inputs"]["ratified_contracts"]] == CRITICAL and mixed["level"] == "ADECUADA"
    revoked_noneffect = jd.derive(p, CLIENT, snap(*ALL4(p), ratified(p, "ART_0011"), revoked(p, "ART_0011")))[0]
    assert revoked_noneffect["output_signature"] == mixed["output_signature"]                 # revocar una decision que no resolvia nada no invalida el Juicio


def test_the_snapshot_digest_is_verifiable_and_changes_with_the_registry_content():
    p = gp_profile()
    e = ALL4(p)
    assert snap(*e).digest == snap(*reversed(e)).digest != snap(*e[:3]).digest != jd.empty_snapshot(NOW).digest
    assert snap(*e).summary()["event_count"] == 4 and snap(*e).summary()["clients"] == {CLIENT: 4}


def test_an_unreadable_registry_is_never_the_same_as_no_decisions(monkeypatch):
    monkeypatch.setattr(svc.remote_store, "read_enabled", lambda: False)
    with pytest.raises(jd.DecisionSnapshotError):
        jd.take_snapshot([CLIENT])


# ───────────── contrato vigente y clasificacion ─────────────
def test_the_current_persisted_contract_is_v4_and_a_persisted_v2_judgment_is_version_stale(world):
    assert svc.JUDGMENT_CONTRACT_VERSIONS == ("judgment/v1", "judgment-rules/v4")
    env = seed(world)
    assert env["judgment"]["ruleset_version"] == "judgment-rules/v2"
    r = svc.classify_judgment_decisions(stored(), snap(*ALL4(gp_profile())))
    assert r["action"] == svc.JDG_VERSION_STALE and r["fresh"]["level"] == "ADECUADA" and r["lost"] == []


def test_generation_and_reads_never_consult_the_registry_so_a_fresh_derivation_has_no_decisions(world):
    p = gp_profile()
    j = svc._derive_judgment(p)                                                        # lo que usan generate/reassess/lectura: sin instantanea
    assert j["ruleset_version"] == "judgment-rules/v4" and j["decision_inputs"]["ratified_contracts"] == [] and (j["status"], j["level_rule"]) == ("NOT_DETERMINABLE", "LVL-05")


# ───────────── dry-run sin escrituras con conteos exactos ─────────────
def test_dry_run_reports_exact_counts_and_writes_nothing(world):
    seed(world)
    before = raw_bytes(world)
    rep = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=snap(*ALL4(gp_profile())))
    c = rep["counts"]
    assert (c["eligible"], c["judgment_version_stale"], c["would_update"], c["level_changes"], c["decision_resolutions"], c["updated"], c["failed"]) == (1, 1, 1, 1, 1, 0, 0)
    assert rep["level_changes"] == [{"client": CLIENT, "terminal_am": AM, "from": ["NOT_DETERMINABLE", None, "LVL-05"], "to": ["DETERMINED", "ADECUADA", "LVL-03"],
                                     "event_ids": [f"gd-{l}" for l in CRITICAL]}]
    assert rep["decision_snapshot"]["event_count"] == 4 and rep["decision_snapshot"]["digest"].startswith("sha256:") and rep["plan"]["digest"]
    assert raw_bytes(world) == before and len(files(world)) == 1


def test_without_decisions_the_v3_migration_changes_the_contract_version_but_no_level(world):
    seed(world)
    rep = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=jd.empty_snapshot(NOW))
    c = rep["counts"]
    assert (c["judgment_version_stale"], c["would_update"], c["level_changes"], c["decision_resolutions"]) == (1, 1, 0, 0)


def test_the_plan_digest_binds_the_snapshot_so_a_different_registry_state_needs_a_new_approval(world):
    seed(world)
    p = gp_profile()
    a = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=snap(*ALL4(p)))["plan"]["digest"]
    b = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=snap(*ALL4(p)[:3]))["plan"]["digest"]
    c = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=snap(*ALL4(p)))["plan"]["digest"]
    assert a == c != b


# ───────────── apply (biblioteca), idempotencia, trazabilidad ─────────────
def test_apply_persists_the_v3_judgment_with_event_traceability_and_touches_nothing_else_then_is_idempotent(world):
    env0 = seed(world)
    s = snap(*ALL4(gp_profile()))
    rep = bf.backfill_judgment(apply=True, client=CLIENT, snapshot=s)
    assert rep["counts"]["updated"] == 1 and rep["counts"]["failed"] == 0
    env = stored()
    assert (env["judgment"]["ruleset_version"], env["judgment"]["level"], env["judgment"]["level_rule"]) == (CURRENT_RULESET_VERSION, "ADECUADA", "LVL-03")
    assert [i["event_id"] for i in env["judgment"]["decision_inputs"]["ratified_contracts"]] == [f"gd-{l}" for l in CRITICAL]
    assert all(env[k] == env0[k] for k in ("profile_json", "assessment_artifact_signature", "input_pool_signature", "assessment_versions", "domain_classification", "business_classification", "generated_at"))
    assert "judgment_review" not in env and len(files(world)) == 1
    again = bf.backfill_judgment(apply=True, client=CLIENT, snapshot=s)
    assert again["counts"]["updated"] == 0 and again["counts"]["already_current"] == 1 and again["counts"]["complete"]


# ───────────── invalidacion: revocacion / evidencia STALE ─────────────
def adopted(world):
    seed(world)
    p = gp_profile()
    bf.backfill_judgment(apply=True, client=CLIENT, snapshot=snap(*ALL4(p)))
    restart()
    return p


def test_a_revocation_marks_the_judgment_pending_reevaluation_without_rewriting_it(world):
    p = adopted(world)
    before = stored()["judgment"]
    s2 = snap(*ALL4(p), revoked(p, "ART_0012"))
    r = svc.classify_judgment_decisions(stored(), s2)
    assert r["action"] == svc.JDG_DECISION_STALE and r["lost"][0]["event_id"] == "gd-ART_0012" and r["lost"][0]["reason"] == "REVOKED"
    dry = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=s2)
    assert dry["counts"]["decision_stale"] == 1 and dry["counts"]["would_mark_pending_reevaluation"] == 1 and dry["counts"]["would_update"] == 0
    app = bf.backfill_judgment(apply=True, client=CLIENT, snapshot=s2)
    assert app["counts"]["marked_pending_reevaluation"] == 1 and app["counts"]["updated"] == 0
    env = stored()
    assert env["judgment"] == before                                                       # el Juicio historico NO se reescribe
    m = env["judgment_review"]
    assert m["state"] == "PENDING_REEVALUATION" and m["cause"] == "GOVERNED_DECISION_NO_LONGER_VALID" and m["judgment_output_signature"] == before["output_signature"]
    assert m["reasons"] == [{"event_id": "gd-ART_0012", "leaf": "ART_0012", "technical_id": tid_of(p, "ART_0012"), "reason": "REVOKED"}] and m["snapshot"]["digest"] == s2.digest
    again = bf.backfill_judgment(apply=False, client=CLIENT, snapshot=s2)
    assert again["counts"]["would_mark_pending_reevaluation"] == 1                         # sigue pendiente hasta una reevaluacion EXPLICITA


def test_explicit_reevaluation_replaces_the_judgment_and_clears_the_pending_mark(world):
    p = adopted(world)
    s2 = snap(*ALL4(p), revoked(p, "ART_0012"))
    bf.backfill_judgment(apply=True, client=CLIENT, snapshot=s2)
    rep = bf.backfill_judgment(apply=True, client=CLIENT, snapshot=s2, reevaluate_decision_stale=True)
    assert rep["counts"]["updated"] == 1
    env = stored()
    assert "judgment_review" not in env and (env["judgment"]["status"], env["judgment"]["level_rule"]) == ("NOT_DETERMINABLE", "LVL-05")           # revocada una de las 4 => LVL-05 vuelve
    assert [i["event_id"] for i in env["judgment"]["decision_inputs"]["ratified_contracts"]] == [f"gd-{l}" for l in CRITICAL[1:]]


def test_stale_evidence_after_the_ratification_marks_the_judgment_pending_reevaluation(world):
    p = adopted(world)
    s = snap(*ALL4(p))
    env = stored()
    changed = copy.deepcopy(env["profile_json"])
    leaf_rec(changed, "ART_0013")["classification_bases"] = ["REMOTE_ENTITY_NAMESPACE"]    # la evidencia observada de la CDS cambio DESPUES de ratificar
    svc._store_envelope({**env, "profile_json": changed})
    restart()
    r = svc.classify_judgment_decisions(stored(), s)
    assert r["action"] == svc.JDG_DECISION_STALE and [(l["leaf"], l["reason"]) for l in r["lost"]] == [("ART_0013", "EVIDENCE_STALE")]


def test_a_new_ratification_after_adoption_is_a_plain_content_stale_not_an_invalidation(world):
    seed(world)
    p = gp_profile()
    bf.backfill_judgment(apply=True, client=CLIENT, snapshot=snap(*ALL4(p)[:3]))            # 3 de 4: sigue ND/LVL-05
    restart()
    r = svc.classify_judgment_decisions(stored(), snap(*ALL4(p)))                           # aparece la 4a: el Juicio podria rectificarse
    assert r["action"] == svc.JDG_CONTENT_STALE and r["lost"] == [] and r["fresh"]["level"] == "ADECUADA"


# ───────────── CLI / apply asegurado ─────────────
def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="alice", operator="bob")


def test_the_cli_apply_is_fail_closed_without_confirmation_and_without_a_signed_approval(world, capsys):
    seed(world)
    before = raw_bytes(world)
    with pytest.raises(SystemExit):
        bf.main(["--backfill-judgment", "--apply", "--decisions", "none"])                  # sin --confirm-migration
    code = bf.main(["--backfill-judgment", "--apply", "--decisions", "none", "--confirm-migration", bf.JUDGMENT_ADOPTION_MIGRATION])
    out = json.loads(capsys.readouterr().out)
    assert code == bf.EXIT_DENIED and out["outcome"] == bf.DENIED and out["writes"] == 0 and raw_bytes(world) == before


def test_the_cli_dry_run_fails_closed_when_the_registry_cannot_be_read_and_writes_nothing(world, monkeypatch, capsys):
    seed(world)
    before = raw_bytes(world)
    monkeypatch.setattr(svc.remote_store, "read_enabled", lambda: False)
    code = bf.main(["--backfill-judgment", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["outcome"] == "DECISION_SNAPSHOT_FAILED" and out["writes"] == 0 and raw_bytes(world) == before


def test_decision_flags_only_exist_with_the_judgment_operator(world):
    for argv in (["--decisions", "none", "--dry-run"], ["--reevaluate-decision-stale", "--dry-run"]):
        with pytest.raises(SystemExit):
            bf.main(argv)


def secured(world, **kw):
    return bf.secured_adopt_judgment(authorizer=kw.pop("authorizer", _grant), backup_dir=kw.pop("backup_dir", world.tmp / "bk"), client=CLIENT, allow_local_only=True,
                                     remote_enabled=lambda: False, **kw)


def test_the_secured_apply_backs_up_journals_and_applies_only_the_planned_judgment(world):
    env0 = seed(world)
    s = snap(*ALL4(gp_profile()))
    out = secured(world, snapshot=s)
    assert out["outcome"] == bf.APPLIED and out["counts"] == {"applied": 1, "failed": 0, "pending_remote": 0} and out["complete"]
    assert out["secured_apply"]["backup"]["objects"] == 1 and out["decision_snapshot"]["digest"] == s.digest
    from webapp.services import governed_backup as gb
    journal = gb.read_journal(world.tmp / "bk")
    env = stored()
    assert env["judgment"]["level"] == "ADECUADA" and env["profile_json"] == env0["profile_json"]
    events = [j["event"] for j in journal]
    assert events[:3] == ["BACKED_UP", "AUTHORIZED", "COMMITTED"]                            # respaldo verificado ANTES de autorizar/escribir; luego el commit por objeto
    auth = next(j for j in journal if j["event"] == "AUTHORIZED")
    commit = next(j for j in journal if j["event"] == "COMMITTED")
    assert auth["decision_snapshot_digest"] == s.digest and auth["plan_digest"] == out["plan"]["digest"] and commit["sha256"] and commit["key"].endswith(".json")


def test_the_secured_apply_refuses_without_authorization_or_backup_and_writes_nothing(world):
    seed(world)
    before = raw_bytes(world)
    s = snap(*ALL4(gp_profile()))
    assert secured(world, snapshot=s, authorizer=lambda m, p: ma.DENIED("NO_APPROVAL"))["outcome"] == bf.DENIED
    out = bf.secured_adopt_judgment(authorizer=_grant, backup_dir=None, client=CLIENT, snapshot=s, allow_local_only=True, remote_enabled=lambda: False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["writes"] == 0
    out = bf.secured_adopt_judgment(authorizer=_grant, backup_dir=world.tmp / "bk3", client=CLIENT, snapshot=s, remote_enabled=lambda: False)   # sin remoto y sin --allow-local-only
    assert out["outcome"] == bf.REMOTE_REQUIRED and out["writes"] == 0
    assert raw_bytes(world) == before


def test_plan_drift_between_the_approved_plan_and_the_write_is_not_written(world):
    seed(world)
    p = gp_profile()
    s = snap(*ALL4(p))
    def drifting(migration, plan):                                                         # el perfil cambia DESPUES de planificar y ANTES de escribir
        env = stored()
        ch = copy.deepcopy(env["profile_json"])
        leaf_rec(ch, "ART_0013")["classification_bases"] = ["REMOTE_ENTITY_NAMESPACE"]
        svc._store_envelope({**env, "profile_json": ch})
        restart()
        return _grant(migration, plan)
    out = secured(world, snapshot=s, authorizer=drifting)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["counts"]["failed"] == 1 and out["records"][0]["error"] == "PLAN_DRIFT"
    assert stored()["judgment"]["ruleset_version"] == "judgment-rules/v2" and "judgment_review" not in stored()      # solo cambio el perfil de la deriva provocada; el Juicio NO se escribio


def test_the_backup_restores_the_previous_v2_judgment_by_object(world):
    seed(world)
    before = raw_bytes(world)
    s = snap(*ALL4(gp_profile()))
    out = secured(world, snapshot=s)
    assert out["outcome"] == bf.APPLIED and raw_bytes(world) != before
    from webapp.services import governed_backup as gb
    manifest = gb.load_manifest(world.tmp / "bk")
    rep = bf.restore_from_backup(world.tmp / "bk", apply=True, confirm=manifest["manifest_sha256"][:16], authorizer=_grant)
    assert rep["outcome"] == "DONE" and rep["counts"]["failed"] == 0 and rep["counts"]["conflicts"] == 0
    restart()
    assert stored()["judgment"]["ruleset_version"] == "judgment-rules/v2" and raw_bytes(world) == before
