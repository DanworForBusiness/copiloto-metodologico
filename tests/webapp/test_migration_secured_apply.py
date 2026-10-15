"""Endurecimiento del ``--apply`` de ``evidence_leaf_materialization_v4``: autorizacion FAIL-CLOSED (``migration_approval``), respaldo verificable + restauracion por objeto (``governed_backup``),
plan_digest, olas con deteccion de fallas/abstenciones/pendientes remotos y control de publicacion (bytes remotos, reinicio bloqueado, reejecucion).

Todo sobre copias aisladas (``tmp_path``: pool, almacen gobernado, catalogo y Spaces FALSO en memoria); nunca estado real."""
from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config
from core import remote_store
from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import (MIG, _digest, _env, _make_v3, _path, _rec, _snapshot, kworld)  # noqa: F401
from tests.webapp.test_governed_assessment_durability import FakeBucket, files, restart  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
_PRIV = Ed25519PrivateKey.generate()
_OTHER = Ed25519PrivateKey.generate()
KEYS = {"alice": _PRIV.public_key()}


def _approval(plan: dict, **over) -> dict:
    kw = dict(migration=MIG, plan_digest=plan["digest"], clients=plan["clients"], approver="alice", operator="bob", private_key=_PRIV, issued_at=NOW - timedelta(minutes=5), ttl=timedelta(hours=2))
    kw.update(over)
    return ma.issue_approval(**kw)


def _verify(approval, plan, *, operator="bob", now=NOW, keys=KEYS, migration=MIG):
    return ma.verify_approval(approval, migration=migration, plan_digest=plan["digest"], plan_clients=plan["clients"], operator=operator, now=now, approver_keys=keys)


PLAN = {"digest": "d" * 64, "clients": ["Kensing"]}


# ───────────────────────────── 1. autorizacion fail-closed ─────────────────────────────
def test_default_is_deny_without_approval_without_configured_approvers_and_app_owner_authorizes_nothing(monkeypatch):
    assert not ma.deny_all(MIG, PLAN).granted
    assert _verify(None, PLAN).reason == ma.NO_APPROVAL
    assert _verify(_approval(PLAN), PLAN, keys={}).reason == ma.NO_APPROVERS                   # sin claves configuradas nadie aprueba
    monkeypatch.delenv(ma.ENV_APPROVER_KEYS, raising=False)
    assert ma.load_approver_keys() == {}
    monkeypatch.setenv("APP_OWNER", "bob")                                                       # ser APP_OWNER no autoriza nada
    monkeypatch.setenv("APP_OWNERS", "bob,alice")
    assert not ma.authorizer_from_file(None, "bob")(MIG, PLAN).granted
    assert ma.verify_approval(_approval(PLAN), migration=MIG, plan_digest=PLAN["digest"], plan_clients=PLAN["clients"], operator="bob", now=NOW).reason == ma.NO_APPROVERS
    monkeypatch.setenv(ma.ENV_APPROVER_KEYS, "{no es json")
    assert ma.load_approver_keys() == {}


def test_a_valid_approval_is_granted_and_names_identity_scope_and_expiry():
    a = _verify(_approval(PLAN), PLAN)
    assert a.granted and a.approver == "alice" and a.operator == "bob" and a.expires_at == "2026-10-15T13:55:00Z"


def _tamper(field, value):
    def f(doc):
        doc[field] = value
        return doc
    return f


@pytest.mark.parametrize("make,operator,expected", [
    (lambda: {**_approval(PLAN), "approver": "mallory"}, "bob", ma.UNKNOWN_APPROVER),
    (lambda: {**_approval(PLAN), "plan_digest": "e" * 64}, "bob", ma.BAD_SIGNATURE),                                 # editar el documento invalida la firma
    (lambda: ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=["Kensing"], approver="alice", operator="bob", private_key=_OTHER, issued_at=NOW - timedelta(minutes=5)), "bob", ma.BAD_SIGNATURE),
    (lambda: _approval(PLAN, issued_at=NOW - timedelta(hours=5), ttl=timedelta(hours=1)), "bob", ma.EXPIRED),
    (lambda: _approval(PLAN, issued_at=NOW + timedelta(hours=1)), "bob", ma.ISSUED_IN_FUTURE),
    (lambda: _approval(PLAN, migration="evidence_confidence_v3"), "bob", ma.WRONG_MIGRATION),
    (lambda: _approval(PLAN, clients=["Otro"]), "bob", ma.SCOPE_NOT_COVERED),
    (lambda: _approval(PLAN), "alice", ma.OPERATOR_MISMATCH),
    (lambda: _approval(PLAN, operator="alice"), "alice", ma.SAME_PERSON),                                           # el aprobador no ejecuta
    (lambda: _approval(PLAN), "", ma.NO_OPERATOR),
    (lambda: _approval(PLAN, plan_digest="f" * 64), "bob", ma.PLAN_CHANGED),
    (lambda: {"schema": "otro/v9"}, "bob", ma.BAD_SCHEMA),
])
def test_every_unmet_condition_denies_with_a_stable_reason(make, operator, expected):
    a = _verify(make(), PLAN, operator=operator)
    assert not a.granted and a.reason == expected


def test_an_approval_that_lives_longer_than_the_maximum_or_without_expiry_is_denied():
    with pytest.raises(ValueError):
        ma.issue_approval(migration=MIG, plan_digest="d" * 64, clients=["Kensing"], approver="alice", operator="bob", private_key=_PRIV, ttl=ma.MAX_TTL + timedelta(seconds=1))
    doc = {"schema": ma.APPROVAL_SCHEMA, "migration": MIG, "plan_digest": PLAN["digest"], "scope": {"clients": ["Kensing"]}, "approver": "alice", "authorized_operator": "bob",
           "issued_at": "2026-10-15T11:00:00Z", "expires_at": "2026-10-17T11:00:00Z"}
    import base64
    doc["signature"] = base64.b64encode(_PRIV.sign(ma.canonical(doc))).decode()
    assert _verify(doc, PLAN).reason == ma.TTL_TOO_LONG
    doc2 = {k: v for k, v in doc.items() if k not in ("signature", "expires_at")}
    doc2["signature"] = base64.b64encode(_PRIV.sign(ma.canonical(doc2))).decode()
    assert _verify(doc2, PLAN).reason == ma.NO_EXPIRY
    assert _verify(_approval({"digest": "d" * 64, "clients": []}), {"digest": "d" * 64, "clients": []}).reason == ma.EMPTY_PLAN


def test_an_unreadable_approval_file_denies(tmp_path):
    f = tmp_path / "a.json"
    assert ma.authorizer_from_file(f, "bob", approver_keys=KEYS)(MIG, PLAN).reason == ma.APPROVAL_UNREADABLE
    f.write_text("{roto", encoding="utf-8")
    assert ma.authorizer_from_file(f, "bob", approver_keys=KEYS)(MIG, PLAN).reason == ma.APPROVAL_UNREADABLE


# ───────────────────────────── 2. dry-run: contrato seguro + plan_digest ─────────────────────────────
def _grant(migration, plan):
    return ma.Authorization(True, "AUTHORIZED", approver="alice", operator="bob")


def _secured(w, *entries, authorizer=_grant, backup=None, **kw):
    return bf.secured_migrate(list(entries) or [w.k, w.k2], MIG, authorizer=authorizer, backup_dir=backup if backup is not None else w.tmp / "bk", allow_local_only=kw.pop("allow_local_only", True),
                              governed_state_present=ALWAYS, **kw)


def _plan(w, *entries):
    return bf.migrate_stale_evidence(list(entries) or [w.k, w.k2], MIG, apply=False, governed_state_present=ALWAYS)["plan"]


def test_dry_run_writes_nothing_and_publishes_a_stable_plan_digest(kworld):
    _make_v3(kworld, kworld.k, kworld.k2)
    snap = _snapshot(kworld)
    a = bf.migrate_stale_evidence([kworld.k, kworld.k2], MIG, apply=False, governed_state_present=ALWAYS)
    b = bf.migrate_stale_evidence([kworld.k2, kworld.k], MIG, apply=False, governed_state_present=ALWAYS)
    assert a["dry_run"] is True and a["apply"] is False and _snapshot(kworld) == snap
    assert a["plan"]["digest"] == b["plan"]["digest"] and a["plan"]["clients"] == ["Kensing", "Kensing2"] and len(a["plan"]["items"]) == 2          # independiente del orden
    item = a["plan"]["items"][0]
    assert set(item) >= {"previous_artifact_signature", "pool_signature", "candidate_artifact_signature", "judgment_action"} and all(item[k] for k in ("previous_artifact_signature", "pool_signature", "candidate_artifact_signature"))
    assert _plan(kworld, kworld.k)["digest"] != a["plan"]["digest"]                                                                                  # otro plan => otro digest


# ───────────────────────────── 3. apply denegado: nunca un dry-run "exitoso" ─────────────────────────────
def test_an_unauthorized_apply_is_denied_explicitly_writes_nothing_and_does_not_look_like_a_dry_run(kworld):
    _make_v3(kworld, kworld.k, kworld.k2)
    snap = _snapshot(kworld)
    out = _secured(kworld, authorizer=ma.deny_all)
    assert out["outcome"] == bf.DENIED and out["applied"] is False and out["apply"] is True and out["dry_run"] is False and out["writes"] == 0
    assert out["authorization"]["granted"] is False and out["authorization"]["reason"] == ma.NO_APPROVAL and out["records"] == []
    assert out["plan"]["digest"] == _plan(kworld)["digest"] and out["restart_safe"] is False and out["complete"] is False                          # el plan_digest queda a la vista del aprobador
    assert _snapshot(kworld) == snap and not (kworld.tmp / "bk").exists()                                                                               # ni siquiera el respaldo


def test_the_cli_exit_code_of_a_denied_apply_is_4_and_a_dry_run_keeps_its_safe_contract(kworld, monkeypatch, capsys):
    _make_v3(kworld, kworld.k, kworld.k2)
    snap = _snapshot(kworld)
    monkeypatch.delenv(ma.ENV_APPROVER_KEYS, raising=False)
    assert bf.main(["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", MIG]) == bf.EXIT_DENIED
    assert json.loads(capsys.readouterr().out)["outcome"] == bf.DENIED
    assert bf.main(["--regenerate-stale-evidence", MIG, "--dry-run"]) in (0, 2)
    rep = json.loads(capsys.readouterr().out)
    assert rep["dry_run"] is True and rep["plan"]["digest"] and _snapshot(kworld) == snap
    with pytest.raises(SystemExit) as e:                                                                                                                # sin --dry-run ni --apply: usage
        bf.main(["--regenerate-stale-evidence", MIG])
    assert e.value.code == 2


def test_an_approval_signed_for_another_plan_is_denied_with_plan_changed_and_nothing_is_written(kworld, tmp_path):
    _make_v3(kworld, kworld.k, kworld.k2)
    snap = _snapshot(kworld)
    only_k = _plan(kworld, kworld.k)                                                                                                                      # el plan que el aprobador vio
    f = tmp_path / "approval.json"
    f.write_text(json.dumps(_approval(only_k, clients=["Kensing", "Kensing2"], issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))), encoding="utf-8")
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, "bob", approver_keys=KEYS))
    assert out["outcome"] == bf.DENIED and out["authorization"]["reason"] == ma.PLAN_CHANGED and _snapshot(kworld) == snap


def test_a_valid_signed_approval_of_the_exact_plan_applies_with_a_verified_backup_and_a_journal(kworld, tmp_path):
    originals = _make_v3(kworld, kworld.k, kworld.k2)
    plan = _plan(kworld)
    f = tmp_path / "approval.json"
    f.write_text(json.dumps(_approval(plan, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))), encoding="utf-8")
    pre = {p.name: p.read_bytes() for p in files(kworld)}
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, "bob", approver_keys=KEYS))
    assert out["outcome"] == bf.APPLIED and out["applied"] is True and out["counts"]["failed"] == 0 and out["counts"]["abstained"] == 0
    assert out["secured_apply"]["authorization"]["approver"] == "alice" and [w["status"] for w in out["secured_apply"]["waves"]] == ["CLEAN", "CLEAN"]
    assert svc.status_for_entry(kworld.k).state == svc.CURRENT and svc.status_for_entry(kworld.k2).state == svc.CURRENT
    bk = kworld.tmp / "bk"
    assert gb.verify_backup(bk) == [] and gb.load_manifest(bk)["plan_digest"] == plan["digest"]
    for o in gb.load_manifest(bk)["objects"]:                                                                                                             # bytes EXACTOS previos a la migracion
        assert (bk / "objects" / o["key"]).read_bytes() == pre[Path(o["key"]).name] and o["size"] == len(pre[Path(o["key"]).name])
    ev = gb.read_journal(bk)
    assert [e["event"] for e in ev].count("BACKED_UP") == 2 and [e["event"] for e in ev].count("COMMITTED") == 2
    for e in (e for e in ev if e["event"] == "COMMITTED"):
        assert e["sha256"] == _digest(kworld.governed / e["key"])                                                                                         # el hash de lo escrito queda en el diario
    assert originals["Kensing"]["profile_json"]["canonical_evidence"]["version"] != _env(_path(kworld, "Kensing"))["profile_json"]["canonical_evidence"]["version"]


# ───────────────────────────── 4. cambio entre plan y escritura ─────────────────────────────
def test_a_candidate_that_differs_from_the_approved_item_at_commit_time_is_never_written(kworld):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    calls = []
    real = svc.regenerate_stale_evidence

    def shifting(entry, migration):
        cand = real(entry, migration)
        calls.append(1)
        if len(calls) > 1:                                                                                                                               # fase de apply: el motor ya produce otra cosa
            cand = dataclasses.replace(cand, envelope={**cand.envelope, "assessment_artifact_signature": "0" * 64})
        return cand
    out = _secured(kworld, kworld.k, regenerate=shifting)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and _rec(out)["action"] == bf.FAILED_ACTION and _rec(out)["reason"] == "PLAN_CHANGED" and out["counts"]["failed"] == 1
    assert _snapshot(kworld) == snap and not [e for e in gb.read_journal(kworld.tmp / "bk") if e["event"] == "COMMITTED"]


# ───────────────────────────── 5. respaldo: falla segura ─────────────────────────────
def test_when_the_backup_cannot_be_guaranteed_nothing_is_written(kworld):
    _make_v3(kworld, kworld.k)
    snap = _snapshot(kworld)
    busy = kworld.tmp / "busy"
    busy.mkdir()
    (busy / "otro.txt").write_text("x")
    assert _secured(kworld, kworld.k, backup=busy)["backup_error"] == "BACKUP_DIR_NOT_EMPTY"
    assert _secured(kworld, kworld.k, backup=kworld.governed / "bk")["backup_error"] == "BACKUP_DIR_INSIDE_GOVERNED_STORE"
    assert _secured(kworld, kworld.k, backup="")["backup_error"] == "BACKUP_DIR_REQUIRED"
    out = _secured(kworld, kworld.k, backup=kworld.tmp / "bk2", allow_local_only=False)                                                                  # sin remoto no se garantiza la publicacion
    assert out["outcome"] == bf.REMOTE_REQUIRED
    for o in (_secured(kworld, kworld.k, backup=busy),):
        assert o["outcome"] == bf.BACKUP_NOT_GUARANTEED and o["applied"] is False and o["writes"] == 0
    assert _snapshot(kworld) == snap


def test_backup_unit_manifest_sizes_hashes_seal_and_corruption_detection(kworld):
    _make_v3(kworld, kworld.k)
    p = _path(kworld, "Kensing")
    bk = kworld.tmp / "unit"
    m = gb.create_backup(bk, [{"client": "Kensing", "root": "x", "path": p}], governed_dir=kworld.governed, migration=MIG, plan_digest="p" * 64, environment="e")
    (o,) = m["objects"]
    assert o["size"] == p.stat().st_size and o["sha256"] == _digest(p) and gb.verify_backup(bk) == []
    (bk / "objects" / o["key"]).write_bytes(b"corrupto")
    assert gb.verify_backup(bk) == [f"OBJECT_CORRUPT:{o['key']}"]
    assert gb.restore_object(bk, o["key"], governed_dir=kworld.governed, apply=True)["status"] == "BACKUP_OBJECT_CORRUPT"                              # un respaldo corrupto no restaura
    with pytest.raises(gb.BackupError) as e:
        gb.create_backup(kworld.tmp / "empty", [], governed_dir=kworld.governed, migration=MIG, plan_digest="p", environment="e")
    assert e.value.code == "NOTHING_TO_BACK_UP"
    with pytest.raises(gb.BackupError) as e:
        gb.create_backup(kworld.tmp / "miss", [{"client": "K", "root": "r", "path": kworld.governed / "v1" / "no.json"}], governed_dir=kworld.governed, migration=MIG, plan_digest="p", environment="e")
    assert e.value.code == "TARGET_MISSING"


# ───────────────────────────── 6. restauracion por objeto ─────────────────────────────
def _applied(w):
    pre = _make_v3(w, w.k, w.k2)
    before = {p.name: p.read_bytes() for p in files(w)}
    out = _secured(w)
    assert out["outcome"] == bf.APPLIED
    return before, w.tmp / "bk", out


def test_restore_by_object_brings_back_the_exact_bytes_is_idempotent_and_touches_only_that_object(kworld):
    before, bk, _ = _applied(kworld)
    keys = {Path(o["key"]).parent.name: o["key"] for o in gb.load_manifest(bk)["objects"]}
    migrated2 = (kworld.governed / keys["Kensing2"]).read_bytes()
    dry = gb.restore_object(bk, keys["Kensing"], governed_dir=kworld.governed, apply=False)
    assert dry["status"] == "WOULD_RESTORE" and (kworld.governed / keys["Kensing"]).read_bytes() != before[Path(keys["Kensing"]).name]
    done = gb.restore_object(bk, keys["Kensing"], governed_dir=kworld.governed, apply=True)
    assert done["status"] == "RESTORED" and (kworld.governed / keys["Kensing"]).read_bytes() == before[Path(keys["Kensing"]).name]
    assert (kworld.governed / keys["Kensing2"]).read_bytes() == migrated2                                                                                # el otro objeto, intacto
    assert gb.restore_object(bk, keys["Kensing"], governed_dir=kworld.governed, apply=True)["status"] == "ALREADY_RESTORED"
    assert gb.restore_object(bk, "v1/nada.json", governed_dir=kworld.governed)["status"] == "UNKNOWN_OBJECT"
    restart()
    assert svc.status_for_entry(kworld.k).pending_migration == MIG and svc.status_for_entry(kworld.k2).pending_migration is None                                  # k volvio a v3, k2 sigue migrado


def test_restore_never_overwrites_a_concurrent_local_change(kworld):
    before, bk, _ = _applied(kworld)
    key = next(o["key"] for o in gb.load_manifest(bk)["objects"] if "Kensing2" not in o["key"])
    f = kworld.governed / key
    f.write_bytes(f.read_bytes() + b" ")                                                                                                                  # alguien lo toco despues de la migracion
    touched = f.read_bytes()
    res = gb.restore_object(bk, key, governed_dir=kworld.governed, apply=True)
    assert res["status"] == "CONFLICT_CONCURRENT_CHANGE" and res["where"] == "local" and f.read_bytes() == touched


def test_restore_of_an_object_the_migration_never_wrote_is_not_committed(kworld):
    _make_v3(kworld, kworld.k)
    p = _path(kworld, "Kensing")
    bk = kworld.tmp / "nc"
    gb.create_backup(bk, [{"client": "Kensing", "root": "x", "path": p}], governed_dir=kworld.governed, migration=MIG, plan_digest="p", environment="e")
    key = p.relative_to(kworld.governed).as_posix()
    p.write_bytes(p.read_bytes() + b"\n")
    assert gb.restore_object(bk, key, governed_dir=kworld.governed, apply=True)["status"] == "NOT_COMMITTED"


def _restore_approval(w, bk, *, operator="bob", clients=None, migration=None) -> Path:
    m = gb.load_manifest(bk)
    doc = ma.issue_approval(migration=migration or f"restore:{MIG}", plan_digest=m["manifest_sha256"], clients=clients or sorted({o["client"] for o in m["objects"]}), approver="alice", operator=operator,
                            private_key=_PRIV, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1), ttl=timedelta(hours=1))
    f = w.tmp / f"restore-approval-{len(list(w.tmp.glob('restore-approval-*')))}.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return f


def test_restore_requires_same_environment_confirmation_and_a_signed_authorization(kworld, monkeypatch, capsys):
    before, bk, _ = _applied(kworld)
    rep = bf.restore_from_backup(bk, apply=True, confirm="")
    assert rep["outcome"] == "CONFIRMATION_REQUIRED" and not [e for e in gb.read_journal(bk) if e["event"] == "RESTORED"]
    seal = gb.load_manifest(bk)["manifest_sha256"]
    monkeypatch.setenv(ma.ENV_APPROVER_KEYS, json.dumps({"alice": ma.public_key_b64(_PRIV)}))
    snap = _snapshot(kworld)
    assert bf.main(["--restore-backup", str(bk), "--dry-run"]) == 0 and json.loads(capsys.readouterr().out)["counts"]["WOULD_RESTORE"] == 2
    # sin aprobacion / con aprobacion de otra migracion, de otro operador o de otros clientes => denegado, cero escrituras
    assert bf.main(["--restore-backup", str(bk), "--apply", "--confirm-restore", seal[:12], "--operator", "bob"]) == bf.EXIT_DENIED and json.loads(capsys.readouterr().out)["outcome"] == "RESTORE_DENIED"
    for bad in (_restore_approval(kworld, bk, migration=MIG), _restore_approval(kworld, bk, operator="eve"), _restore_approval(kworld, bk, clients=["Otro"])):
        assert bf.main(["--restore-backup", str(bk), "--apply", "--confirm-restore", seal[:12], "--operator", "bob", "--approval", str(bad)]) == bf.EXIT_DENIED
        assert json.loads(capsys.readouterr().out)["outcome"] == "RESTORE_DENIED" and _snapshot(kworld) == snap
    ok = _restore_approval(kworld, bk)
    assert bf.main(["--restore-backup", str(bk), "--apply", "--confirm-restore", seal[:12], "--operator", "bob", "--approval", str(ok)]) == 0
    assert {p.name: p.read_bytes() for p in files(kworld)} == before                                                                                      # todo restaurado, byte a byte
    other = kworld.tmp / "otro-governed"
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(other))
    assert bf.restore_from_backup(bk)["outcome"] == "ENVIRONMENT_MISMATCH"                                                                                # un respaldo de otro entorno no se aplica aqui


# ───────────────────────────── 7. olas: detener ante fallas y abstenciones ─────────────────────────────
def test_a_failure_in_the_first_wave_stops_the_next_waves(kworld, monkeypatch):
    _make_v3(kworld, kworld.k, kworld.k2)
    real = svc.commit_regenerated

    def flaky(candidate):
        if candidate.client == "Kensing":
            raise OSError("disco lleno")
        return real(candidate)
    monkeypatch.setattr(svc, "commit_regenerated", flaky)
    snap2 = {p.name: _digest(p) for p in files(kworld) if p.parent.name == "Kensing2"}
    out = _secured(kworld, waves=[["Kensing"], ["Kensing2"]])
    assert out["outcome"] == bf.STOPPED_AT_WAVE and [w["status"] for w in out["secured_apply"]["waves"]] == ["STOPPED"] and out["secured_apply"]["not_attempted_waves"] == [["Kensing2"]]
    assert {p.name: _digest(p) for p in files(kworld) if p.parent.name == "Kensing2"} == snap2 and out["complete"] is False and out["restart_safe"] is False
    assert [e["event"] for e in gb.read_journal(kworld.tmp / "bk") if e["event"] == "FAILED"] == ["FAILED"]
    assert any(m.startswith("STOPPED_AT_WAVE_1") for m in out["environment_warnings"])


def test_an_abstention_stops_the_next_waves_and_the_cli_reports_exit_5(kworld, monkeypatch, capsys):
    _make_v3(kworld, kworld.k, kworld.k2)
    real = svc.regenerate_stale_evidence
    state = {"plan": True}

    def abstaining(entry, migration):
        cand = real(entry, migration)
        if not state["plan"] and entry.cliente == "Kensing":
            raise svc.EnrichmentError("CLOSURE_DRIFT")
        return cand
    plan = _plan(kworld)
    f = kworld.tmp / "approval.json"
    f.write_text(json.dumps(_approval(plan, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))), encoding="utf-8")
    monkeypatch.setenv(ma.ENV_APPROVER_KEYS, json.dumps({"alice": ma.public_key_b64(_PRIV)}))

    orig_apply = bf.migrate_stale_evidence

    def phased(entries, migration, *, apply=False, **kw):
        state["plan"] = not apply
        return orig_apply(entries, migration, apply=apply, regenerate=abstaining, **kw)
    monkeypatch.setattr(bf, "migrate_stale_evidence", phased)
    snap2 = {p.name: _digest(p) for p in files(kworld) if p.parent.name == "Kensing2"}
    code = bf.main(["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", MIG, "--approval", str(f), "--operator", "bob", "--backup-dir", str(kworld.tmp / "bk"),
                    "--allow-local-only", "--waves", "Kensing;Kensing2"])
    rep = json.loads(capsys.readouterr().out)
    assert code == bf.EXIT_WAVE_STOPPED and rep["outcome"] == bf.STOPPED_AT_WAVE and rep["counts"]["abstained"] == 1
    assert {p.name: _digest(p) for p in files(kworld) if p.parent.name == "Kensing2"} == snap2


def test_waves_must_cover_exactly_the_plan_clients(kworld):
    _make_v3(kworld, kworld.k, kworld.k2)
    plan = _plan(kworld)
    assert bf.parse_waves("Kensing;Kensing2", plan) == [["Kensing"], ["Kensing2"]] and bf.parse_waves("Kensing2,Kensing", plan) == [["Kensing2", "Kensing"]]
    for bad in ("Kensing", "Kensing;Kensing", "Kensing;Kensing2;Otro"):
        with pytest.raises(ValueError):
            bf.parse_waves(bad, plan)
    assert bf._default_waves(plan) == [["Kensing"], ["Kensing2"]]


# ───────────────────────────── 8. control de publicacion (Spaces falso) ─────────────────────────────
@pytest.fixture()
def remote(kworld, monkeypatch):
    bucket = FakeBucket()
    bucket.fail_puts = False

    def put_object(Bucket, Key, Body):                                                                 # PUT dirigido (respaldo durable)
        if bucket.fail_puts:
            raise RuntimeError("simulated put failure")
        bucket.objects[Key] = bytes(Body)
    bucket.put_object = put_object
    monkeypatch.setattr(config, "SPACES_ENDPOINT", "https://fake.digitaloceanspaces.com")
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_KEY", "fake-key")
    monkeypatch.setattr(config, "SPACES_SECRET", "fake-secret")
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "BASE_DIR", kworld.tmp)
    monkeypatch.setattr(config, "PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME])
    class NoSuchKey(Exception):
        response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}

    def get_object(Bucket, Key):
        if Key not in bucket.objects:
            raise NoSuchKey()
        return {"Body": SimpleNamespace(read=lambda: bucket.objects[Key])}
    bucket.get_object = get_object
    monkeypatch.setattr(remote_store, "_get_client", lambda: bucket)
    monkeypatch.setattr(remote_store, "_signatures", {})
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    return bucket


def _publish_all(w):
    for p in files(w):
        assert remote_store.push_path(p)


def test_a_clean_remote_run_confirms_every_object_by_bytes_and_the_restart_is_safe(kworld, remote):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    before = dict(remote.objects)
    out = _secured(kworld, allow_local_only=False)
    assert out["outcome"] == bf.APPLIED and out["counts"]["regenerated_remote"] == 2 and out["counts"]["pending_remote"] == 0
    assert all(r["remote_state"] == svc.REMOTE_CONFIRMED for r in out["records"]) and out["restart"] == bf.RESTART_SAFE and out["complete"] is True
    for p in files(kworld):                                                                                                                                # el remoto == local, byte a byte
        assert svc.verify_remote_durability(p.parent.name, _env(p)["terminal_am"]) == svc.REMOTE_CONFIRMED
    assert dict(remote.objects) != before


def test_the_backup_captures_a_remote_object_that_differs_from_the_local_one(kworld, remote):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    key = next(iter(remote.objects))
    remote.objects[key] = tampered = remote.objects[key] + b" "                                                                                              # el remoto difiere del local
    out = _secured(kworld, kworld.k, allow_local_only=False)
    (o,) = gb.load_manifest(kworld.tmp / "bk")["objects"]
    assert out["outcome"] == bf.APPLIED and o["remote_state"] == gb.R_DIFFERS and o["remote_sha256"] != o["sha256"]
    assert (kworld.tmp / "bk" / "objects_remote" / o["key"]).read_bytes() == tampered
    assert gb.verify_backup(kworld.tmp / "bk") == []


def test_an_unreadable_remote_makes_the_backup_not_guaranteed_and_nothing_is_written(kworld, remote, monkeypatch):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    snap, remote_before = _snapshot(kworld), dict(remote.objects)

    def boom(path):
        raise remote_store.RemoteReadError("sin red")
    monkeypatch.setattr(remote_store, "read_object_bytes", boom)
    out = _secured(kworld, kworld.k, allow_local_only=False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "REMOTE_READ_FAILED" and _snapshot(kworld) == snap and remote.objects == remote_before


def test_pending_remote_stops_the_waves_blocks_the_restart_and_a_rerun_recovers_without_regenerating(kworld, remote):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    remote.fail_uploads = True                                                                                                                            # PUT remoto roto
    out = _secured(kworld, allow_local_only=False, waves=[["Kensing"], ["Kensing2"]])
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["counts"]["pending_remote"] == 1 and out["restart"] == bf.RESTART_BLOCKED and out["restart_safe"] is False
    assert out["secured_apply"]["not_attempted_waves"] == [["Kensing2"]] and _rec(out)["durable"] == svc.DURABLE_LOCAL
    assert any(e["event"] == "REMOTE_PENDING" for e in gb.read_journal(kworld.tmp / "bk"))
    remote.fail_uploads = False                                                                                                                           # se arregla Spaces: reejecucion
    again = bf.secured_migrate([kworld.k], MIG, authorizer=_grant, backup_dir=kworld.tmp / "bk-2", governed_state_present=ALWAYS)                          # plan vacio para k: solo recupera
    assert again["outcome"] == bf.RECOVERY_ONLY and _rec(again)["remote_state"] == svc.REMOTE_CONFIRMED and _rec(again)["remote_retry"] == "CONFIRMED" and again["restart"] == bf.RESTART_SAFE
    assert not (kworld.tmp / "bk-2").exists()                                                                                                             # la recuperacion no regenera ni respalda: no hay reemplazos
    rest = _secured(kworld, kworld.k2, backup=kworld.tmp / "bk-3", allow_local_only=False)                                                                # Kensing2 sigue pendiente de migrar
    assert rest["outcome"] == bf.APPLIED and rest["counts"]["regenerated_remote"] == 1


def test_a_put_that_reports_success_but_stores_other_bytes_is_not_confirmed(kworld, remote, monkeypatch):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)

    def lying_upload(filename, bucket, key):
        remote.objects[key] = Path(filename).read_bytes()[:-1]                                                                                            # confirma pero guarda otra cosa
    monkeypatch.setattr(remote, "upload_file", lying_upload)
    out = _secured(kworld, kworld.k, allow_local_only=False)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and _rec(out)["durable"] == svc.DURABLE_LOCAL and _rec(out)["remote_state"] == svc.REMOTE_PENDING and out["restart"] == bf.RESTART_BLOCKED


def test_rerunning_a_completed_migration_is_a_safe_no_op(kworld, remote):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    assert _secured(kworld, allow_local_only=False)["outcome"] == bf.APPLIED
    snap, remote_before = _snapshot(kworld), dict(remote.objects)
    again = _secured(kworld, backup=kworld.tmp / "bk-again", allow_local_only=False)
    assert again["outcome"] == bf.RECOVERY_ONLY and again["counts"]["regenerated_remote"] == 0 and again["counts"]["failed"] == 0 and again["restart"] == bf.RESTART_SAFE
    assert _snapshot(kworld) == snap and remote.objects == remote_before and not (kworld.tmp / "bk-again").exists()


def test_a_restore_republishes_and_confirms_the_original_bytes_remotely(kworld, remote):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    before = {p.name: p.read_bytes() for p in files(kworld)}
    assert _secured(kworld, kworld.k, allow_local_only=False)["outcome"] == bf.APPLIED
    bk = kworld.tmp / "bk"
    (o,) = gb.load_manifest(bk)["objects"]
    res = gb.restore_object(bk, o["key"], governed_dir=kworld.governed, apply=True, push_remote=remote_store.push_path, read_remote=remote_store.read_object_bytes)
    assert res["status"] == "RESTORED" and res["remote"] == "CONFIRMED"
    (p,) = files(kworld)
    assert p.read_bytes() == before[p.name] == remote_store.read_object_bytes(p)
    assert gb.restore_object(bk, o["key"], governed_dir=kworld.governed, apply=True, push_remote=remote_store.push_path, read_remote=remote_store.read_object_bytes)["status"] == "ALREADY_RESTORED"
    remote.objects[next(iter(remote.objects))] += b" "                                                                                                      # el remoto cambia por fuera
    res = gb.restore_object(bk, o["key"], governed_dir=kworld.governed, apply=True, push_remote=remote_store.push_path, read_remote=remote_store.read_object_bytes)
    assert res["status"] == "CONFLICT_CONCURRENT_CHANGE" and res["where"] == "remote" and remote.objects[next(iter(remote.objects))] == before[p.name] + b" "      # no se pisa
