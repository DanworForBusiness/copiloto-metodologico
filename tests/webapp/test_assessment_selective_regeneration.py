"""test_assessment_selective_regeneration.py — ``assessment_backfill --regenerate-selected``: regeneracion SELECTIVA de evidencia de assessments CURRENT por AM terminal (B5.6, AM_DESPACHOS).

Caso real: una correccion del motor que NO cambia ``assessment_versions`` deja los 34 assessments CURRENT con evidencia vieja; el operador regenera SOLO los AM pedidos con las garantias de las migraciones
sensibles (aprobacion firmada del plan exacto + respaldo local y durable + diario + confirmacion remota por bytes) y verifica por BYTES que ningun otro sobre cambio.
Pool sintetico real (``kworld``: Kensing y Kensing2 con el mismo pool), almacen gobernado temporal y Spaces falso en memoria: sin red, sin Datasphere, sin credenciales.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import _env, _path, _rewrite, _snapshot, kworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import KEYS, _PRIV, remote  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import durable_backup as dbk
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma

SEL = bf.SELECTIVE_MIGRATION
OLD_NOTE = "evidencia previa a la correccion"


def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="alice", operator="bob")


def _old_evidence(env: dict) -> None:
    """Deja el sobre como lo dejo el motor ANTERIOR a la correccion: misma firma de pool y mismas versiones (sigue CURRENT) pero evidencia vieja y un Juicio previo distinto (PARCIAL/LVL-02)."""
    env["profile_json"]["canonical_evidence"]["naming_evidence"]["taxonomy"][0]["note"] = OLD_NOTE
    env["judgment"] = {**env["judgment"], "status": "DETERMINED", "level": "PARCIAL", "level_rule": "LVL-02", "output_signature": "sha256:juicio-previo-a-la-correccion"}


def seed(w) -> dict:
    """Genera los dos assessments con el motor real y deja SOLO el de Kensing con evidencia vieja. Devuelve rutas/bytes/AM terminal."""
    for e in (w.k, w.k2):
        assert svc.generate(e).state == svc.CURRENT
    target, other = _path(w, "Kensing"), _path(w, "Kensing2")
    _rewrite(target, _old_evidence)
    restart()
    assert svc.status_for_entry(w.k).state == svc.CURRENT                                  # la evidencia vieja NO la vuelve STALE: por eso no sirve una migracion por version
    return {"target": target, "other": other, "root": _env(target)["terminal_am"], "root2": _env(other)["terminal_am"], "before": target.read_bytes(), "before_other": other.read_bytes()}


def plan_of(w, roots, **kw):
    return bf.plan_selective_regeneration([w.k, w.k2], roots, client="Kensing", governed_state_present=ALWAYS, **kw)


def apply_(w, roots, *, authorizer=_grant, backup=None, **kw):
    return bf.secured_regenerate_selected([w.k, w.k2], roots, client="Kensing", authorizer=authorizer, backup_dir=backup if backup is not None else w.tmp / "bk", allow_local_only=kw.pop("allow_local_only", True),
                                          remote_enabled=kw.pop("remote_enabled", lambda: False), **kw)


# ───────────────────────────── servicio: regenerate_current_evidence ─────────────────────────────
def test_the_candidate_refreshes_evidence_rederives_judgment_and_keeps_the_sibling_classifications(kworld):
    s = seed(kworld)
    prev = _env(s["target"])
    cand = svc.regenerate_current_evidence(kworld.k)
    ce = cand.profile["canonical_evidence"]["naming_evidence"]["taxonomy"][0]
    assert ce["note"] != OLD_NOTE                                                          # la evidencia viene del motor vigente sobre el pool local
    assert cand.envelope["input_pool_signature"] == prev["input_pool_signature"] and cand.envelope["assessment_versions"] == prev["assessment_versions"]
    assert cand.envelope["domain_classification"] == prev["domain_classification"] and cand.envelope["business_classification"] == prev["business_classification"]
    assert cand.envelope["judgment"]["ruleset_version"] == prev["judgment"]["ruleset_version"] and cand.notes["judgment_action"] == "judgment_rederived"
    assert cand.notes["previous_judgment_level"] == "PARCIAL" and cand.previous_signature == prev["assessment_artifact_signature"]
    assert s["target"].read_bytes() == s["before"]                                          # no escribe


def test_only_a_current_record_with_the_current_judgment_contract_can_be_regenerated(kworld):
    s = seed(kworld)
    orig_sig = _env(s["target"])["input_pool_signature"]
    _rewrite(s["target"], lambda e: (e.update(input_pool_signature="0" * 64), e.pop("input_closure_signature", None)))                 # ahora es STALE (el pool cambio): otro camino
    restart()
    with pytest.raises(svc.EnrichmentError) as ei:
        svc.regenerate_current_evidence(kworld.k)
    assert ei.value.code == "PREVIOUS_NOT_CURRENT"
    _rewrite(s["target"], lambda e: e.update(input_pool_signature=orig_sig))                  # vuelve a CURRENT
    _rewrite(s["target"], lambda e: e["judgment"].update(ruleset_version="judgment-rules/v2"))
    restart()
    with pytest.raises(svc.EnrichmentError) as ei:
        svc.regenerate_current_evidence(kworld.k)
    assert ei.value.code == "PREVIOUS_JUDGMENT_NOT_CURRENT"                                # un Juicio de otro contrato es de --backfill-judgment, no de este operador


def test_a_changed_closure_is_a_hard_refusal_and_nothing_is_written(kworld, monkeypatch):
    s = seed(kworld)
    real = svc._engine_assess

    def drifted(*a, **k):
        doc, meta = real(*a, **k)
        doc["scope"]["closure_artifacts"] = list(doc["scope"]["closure_artifacts"]) + ["EXTRA_ARTIFACT"]
        return doc, meta
    monkeypatch.setattr(svc, "_engine_assess", drifted)
    with pytest.raises(svc.EnrichmentError) as ei:
        svc.regenerate_current_evidence(kworld.k)
    assert ei.value.code in ("CLOSURE_DRIFT", "INVALID_PROFILE") and s["target"].read_bytes() == s["before"]


# ───────────────────────────── dry-run: identidad exacta, informe viejo -> nuevo, plan firmable ─────────────────────────────
def test_the_dry_run_reports_what_changes_plans_only_the_requested_root_and_writes_nothing(kworld):
    s = seed(kworld)
    snap = _snapshot(kworld)
    out = plan_of(kworld, [s["root"]], expect_judgment="")
    assert out["dry_run"] is True and out["apply"] is False and _snapshot(kworld) == snap
    (rec,) = out["records"]                                                                  # el otro assessment ni aparece
    assert (rec["client"], rec["root"], rec["action"]) == ("Kensing", s["root"], bf.WOULD_REGENERATE)
    d = rec["delta"]
    assert d["evidence_sections_changed"] == ["naming_evidence"] and d["pool_signature_same"] and d["versions_same"] and d["closure_same"]
    assert d["judgment"]["old"] == "PARCIAL/LVL-02" and d["judgment"]["new"] != d["judgment"]["old"] and rec["projected"] == d["judgment"]["new"]
    assert [(i["client"], i["root"]) for i in out["plan"]["items"]] == [("Kensing", s["root"])] and out["counts"]["other_envelopes_in_plan"] == 0
    assert out["plan"]["items"][0]["judgment_action"].endswith(d["judgment"]["new"])        # el resultado PROYECTADO entra al digest que se firma
    assert plan_of(kworld, [s["root"]])["plan"]["digest"] == out["plan"]["digest"]           # estable


def test_the_root_identity_is_exact_unknown_or_miscased_roots_are_never_planned(kworld):
    s = seed(kworld)
    for bad in (s["root"].lower(), s["root"] + "_X", "AM_NO_EXISTE"):
        out = plan_of(kworld, [bad])
        assert out["plan"]["items"] == [] and out["records"][0]["reason"] == bf.SKIPPED_ROOT_NOT_FOUND and out["counts"]["would_regenerate"] == 0


def test_the_projected_judgment_is_a_precondition_not_a_wish(kworld):
    s = seed(kworld)
    projected = plan_of(kworld, [s["root"]])["records"][0]["projected"]
    assert plan_of(kworld, [s["root"]], expect_judgment=projected)["counts"]["would_regenerate"] == 1
    out = plan_of(kworld, [s["root"]], expect_judgment="BUENA/LVL-04")
    assert out["plan"]["items"] == [] and out["records"][0]["reason"] == bf.EXPECTATION_NOT_MET and out["records"][0]["projected"] == projected


def test_a_stale_record_is_not_eligible_for_the_selective_regeneration(kworld):
    s = seed(kworld)
    _rewrite(s["target"], lambda e: (e.update(input_pool_signature="0" * 64), e.pop("input_closure_signature", None)))
    restart()
    out = plan_of(kworld, [s["root"]])
    assert out["records"][0]["reason"] == bf.SKIPPED_NOT_CURRENT_SELECTED and out["plan"]["items"] == []


# ───────────────────────────── apply asegurado ─────────────────────────────
def test_the_secured_apply_backs_up_journals_and_writes_only_the_selected_envelope(kworld):
    s = seed(kworld)
    out = apply_(kworld, [s["root"]])
    assert out["outcome"] == bf.APPLIED and out["complete"] and out["counts"] == {"regenerated": 1, "failed": 0, "pending_remote": 0, "unexpected_envelopes_changed": 0}
    assert s["target"].read_bytes() != s["before"] and s["other"].read_bytes() == s["before_other"]                         # el OTRO sobre, byte a byte igual
    assert out["integrity"]["unexpected_changed"] == [] and len(out["integrity"]["changed"]) == 1 and out["integrity"]["governed_envelopes_checked"] == 2
    env = _env(s["target"])
    assert env["profile_json"]["canonical_evidence"]["naming_evidence"]["taxonomy"][0]["note"] != OLD_NOTE and env["judgment"]["level_rule"] != "LVL-02"
    restart()
    assert svc.status_for_entry(kworld.k).state == svc.CURRENT and svc.status_for_entry(kworld.k2).state == svc.CURRENT
    events = [j["event"] for j in gb.read_journal(kworld.tmp / "bk")]
    assert events[:3] == ["BACKED_UP", "AUTHORIZED", "COMMITTED"]                                                           # respaldo verificado ANTES de autorizar/escribir
    assert gb.verify_backup(kworld.tmp / "bk") == [] and out["secured_apply"]["backup"]["objects"] == 1
    again = plan_of(kworld, [s["root"]])                                                                                     # idempotente: ya no cambia la evidencia ni el Juicio
    assert again["records"][0]["delta"]["evidence_sections_changed"] == [] and again["records"][0]["delta"]["judgment"]["old"] == again["records"][0]["delta"]["judgment"]["new"]


def test_the_secured_apply_refuses_without_authorization_backup_or_remote_and_writes_nothing(kworld):
    s = seed(kworld)
    snap = _snapshot(kworld)
    assert apply_(kworld, [s["root"]], authorizer=lambda m, p: ma.DENIED("NO_APPROVAL"))["outcome"] == bf.DENIED
    out = bf.secured_regenerate_selected([kworld.k, kworld.k2], [s["root"]], client="Kensing", authorizer=_grant, backup_dir=None, allow_local_only=True, remote_enabled=lambda: False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["writes"] == 0
    out = bf.secured_regenerate_selected([kworld.k, kworld.k2], [s["root"]], client="Kensing", authorizer=_grant, backup_dir=kworld.tmp / "bk2", remote_enabled=lambda: False)   # sin remoto y sin --allow-local-only
    assert out["outcome"] == bf.REMOTE_REQUIRED and out["writes"] == 0
    assert apply_(kworld, ["AM_NO_EXISTE"])["outcome"] == "NOTHING_TO_DO"
    assert _snapshot(kworld) == snap and not (kworld.tmp / "bk2").exists()


def test_plan_drift_between_the_approved_plan_and_the_write_is_not_written(kworld):
    s = seed(kworld)

    def drifting(migration, plan):                                                         # el sobre cambia DESPUES de planificar y ANTES de escribir
        _rewrite(s["target"], lambda e: e["profile_json"]["canonical_evidence"]["naming_evidence"]["taxonomy"][0].update(note="otro cambio concurrente"))
        restart()
        return _grant(migration, plan)
    out = apply_(kworld, [s["root"]], authorizer=drifting)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["counts"]["failed"] == 1 and out["records"][0]["reason"] == "PLAN_CHANGED"
    assert _env(s["target"])["profile_json"]["canonical_evidence"]["naming_evidence"]["taxonomy"][0]["note"] == "otro cambio concurrente" and s["other"].read_bytes() == s["before_other"]


def test_a_write_that_touches_any_other_envelope_is_detected_and_reported(kworld):
    s = seed(kworld)

    def leaky_commit(candidate):
        durable = svc.commit_stale_migration(candidate)
        _rewrite(s["other"], lambda e: e.update(generated_at="2026-01-01T00:00:00Z"))      # un efecto colateral indebido sobre OTRO sobre
        return durable
    out = apply_(kworld, [s["root"]], commit=leaky_commit)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["complete"] is False and out["counts"]["unexpected_envelopes_changed"] == 1
    assert len(out["integrity"]["unexpected_changed"]) == 1 and out["environment_warnings"]


def test_the_backup_restores_the_previous_bytes_of_the_selected_envelope_only(kworld):
    s = seed(kworld)
    out = apply_(kworld, [s["root"]])
    bk = kworld.tmp / "bk"
    seal = out["secured_apply"]["backup"]["manifest_sha256"]
    assert s["target"].read_bytes() != s["before"]
    dry = bf.restore_from_backup(bk)
    assert dry["counts"]["WOULD_RESTORE"] == 1
    res = bf.restore_from_backup(bk, apply=True, confirm=seal[:12], authorizer=_grant)
    assert res["outcome"] == "DONE" and res["counts"]["RESTORED"] == 1 and res["counts"]["failed"] == 0
    assert s["target"].read_bytes() == s["before"] and s["other"].read_bytes() == s["before_other"]


# ───────────────────────────── remoto: respaldo durable, escritura confirmada por bytes ─────────────────────────────
def _signed(w, roots):
    plan = plan_of(w, roots)["plan"]
    doc = ma.issue_approval(migration=SEL, plan_digest=plan["digest"], clients=plan["clients"], approver="alice", operator="bob", private_key=_PRIV,
                            issued_at=datetime.now(timezone.utc) - timedelta(minutes=1), ttl=timedelta(hours=1))
    f = w.tmp / "approval-sel.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return ma.authorizer_from_file(f, "bob", approver_keys=KEYS)


def test_with_spaces_the_durable_backup_is_sealed_first_and_the_new_bytes_are_confirmed_remotely(kworld, remote):
    s = seed(kworld)
    for p in files(kworld):
        assert svc.remote_store.push_path(p)
    other_remote = {k: v for k, v in remote.objects.items() if s["other"].name in k}
    out = bf.secured_regenerate_selected([kworld.k, kworld.k2], [s["root"]], client="Kensing", authorizer=_signed(kworld, [s["root"]]), backup_dir=kworld.tmp / "bk", run_id="run-sel-1")
    assert out["outcome"] == bf.APPLIED and out["records"][0]["remote_state"] == svc.REMOTE_CONFIRMED and out["records"][0]["durable"] == svc.DURABLE_REMOTE
    assert svc.verify_remote_durability("Kensing", s["root"]) == svc.REMOTE_CONFIRMED and svc.remote_store.read_object_bytes(s["target"]) == s["target"].read_bytes()
    assert {k: v for k, v in remote.objects.items() if s["other"].name in k} == other_remote                                # el remoto del otro sobre, intacto
    runs = {r["run_id"]: r for r in dbk.list_runs(SEL)}
    assert runs["run-sel-1"]["sealed"] is True and out["secured_apply"]["backup"]["remote"] is not None
    kinds = [j["event"] for j in gb.read_journal(kworld.tmp / "bk")]
    assert kinds == ["BACKED_UP", "AUTHORIZED", "COMMITTED", "REMOTE_CONFIRMED"]


def test_a_signed_approval_for_another_plan_does_not_authorize_the_selective_apply(kworld, remote):
    s = seed(kworld)
    for p in files(kworld):
        assert svc.remote_store.push_path(p)
    auth = _signed(kworld, [s["root"]])
    _rewrite(s["target"], lambda e: e["profile_json"]["canonical_evidence"]["naming_evidence"]["taxonomy"][0].update(note="cambio posterior a la firma"))   # el plan de AHORA ya no es el firmado
    restart()
    snap = _snapshot(kworld)
    out = bf.secured_regenerate_selected([kworld.k, kworld.k2], [s["root"]], client="Kensing", authorizer=auth, backup_dir=kworld.tmp / "bk")
    assert out["outcome"] == bf.DENIED and out["writes"] == 0 and _snapshot(kworld) == snap


def test_a_remote_put_that_cannot_be_confirmed_stops_the_run_and_reports_pending(kworld, remote):
    s = seed(kworld)
    for p in files(kworld):
        assert svc.remote_store.push_path(p)
    remote.fail_uploads = True
    out = bf.secured_regenerate_selected([kworld.k, kworld.k2], [s["root"]], client="Kensing", authorizer=_grant, backup_dir=kworld.tmp / "bk", run_id="run-sel-2")
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["counts"]["pending_remote"] == 1 and out["complete"] is False and out["records"][0]["durable"] == svc.DURABLE_LOCAL
    assert any(e["event"] == "REMOTE_PENDING" for e in gb.read_journal(kworld.tmp / "bk"))


# ───────────────────────────── CLI ─────────────────────────────
@pytest.mark.parametrize("argv", [
    ["--regenerate-selected", "--client", "Kensing"],                                              # sin --root
    ["--regenerate-selected", "--root", "AM_X", "--dry-run"],                                      # sin --client: jamas un alcance amplio
    ["--regenerate-selected", "--client", "Kensing", "--root", "AM_X"],                            # ni --dry-run ni --apply
    ["--regenerate-selected", "--client", "Kensing", "--root", "AM_X", "--apply"],                 # sin --confirm-migration
    ["--client", "Kensing", "--root", "AM_X", "--dry-run"],                                        # --root sin el modo
    ["--reassess-current", "--client", "Kensing", "--root", "AM_X", "--dry-run"],
    ["--regenerate-selected", "--client", "Kensing", "--root", "AM_X", "--dry-run", "--reevaluate-decision-stale"],
    ["--regenerate-selected", "--client", "Kensing", "--root", "AM_X", "--apply", "--confirm-migration", "otra_migracion"],
])
def test_the_cli_contract_fails_safely_with_zero_writes(argv, kworld):
    s = seed(kworld)
    snap = _snapshot(kworld)
    with pytest.raises(SystemExit):
        bf.main(argv)
    assert _snapshot(kworld) == snap and s["target"].read_bytes() == s["before"]


def test_the_cli_dry_run_prints_the_plan_and_the_apply_is_denied_without_a_signed_approval(kworld, capsys):
    s = seed(kworld)
    snap = _snapshot(kworld)
    code = bf.main(["--regenerate-selected", "--client", "Kensing", "--root", s["root"], "--decisions", "none", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert code in (0, 2) and out["mode"] == "regenerate-selected" and out["counts"]["would_regenerate"] == 1 and out["plan"]["items"] and _snapshot(kworld) == snap
    code = bf.main(["--regenerate-selected", "--client", "Kensing", "--root", s["root"], "--decisions", "none", "--apply", "--confirm-migration", SEL, "--backup-dir", str(kworld.tmp / "bk"),
                    "--allow-local-only"])
    out = json.loads(capsys.readouterr().out)
    assert code == bf.EXIT_DENIED and out["outcome"] == bf.DENIED and out["writes"] == 0 and _snapshot(kworld) == snap


def test_the_existing_operator_modes_keep_their_contract(kworld):
    seed(kworld)
    for argv in (["--decisions", "none", "--dry-run"], ["--reevaluate-decision-stale", "--dry-run"], ["--expect-judgment", "ADECUADA/LVL-08", "--dry-run"]):
        with pytest.raises(SystemExit):
            bf.main(argv)                                                                          # los flags nuevos solo existen con su modo
