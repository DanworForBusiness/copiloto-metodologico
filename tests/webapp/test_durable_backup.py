"""Respaldo DURABLE en Spaces de ``evidence_leaf_materialization_v4``: ``migration-backups/<migracion>/<run_id>/`` en el bucket existente, escrituras dirigidas (no mirror_up), bytes originales +
manifiesto + hashes + aprobacion + diario, confirmacion por bytes, sello final, sin sobrescribir, restauracion autorizada, y ANTES de la primera modificacion de un Assessment.
Todo con un Spaces falso en memoria sobre copias aisladas."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone

import pytest

import config
from core import remote_store
from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import MIG, _digest, _make_v3, _path, _snapshot, kworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import (KEYS, _PRIV, _approval, _plan, _publish_all, _restore_approval, _secured, remote)  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import durable_backup as dbk
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma

NS = f"{remote_store.BACKUP_NAMESPACE}/{MIG}/"


def _backup_keys(remote) -> list[str]:
    return sorted(k for k in remote.objects if k.startswith(NS))


def _authorizer(w, plan, name="approval.json"):
    f = w.tmp / name
    f.write_text(json.dumps(_approval(plan, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))), encoding="utf-8")
    return ma.authorizer_from_file(f, "bob", approver_keys=KEYS)


def _applied_remote(w, remote, run_id="run-1"):
    _make_v3(w, w.k, w.k2)
    _publish_all(w)
    before = {p.name: p.read_bytes() for p in files(w)}
    out = _secured(w, authorizer=_authorizer(w, _plan(w)), allow_local_only=False, run_id=run_id)
    assert out["outcome"] == bf.APPLIED
    return before, out


# ───────────────────────────── contenido, namespace y orden ─────────────────────────────
def test_the_durable_backup_is_sealed_and_confirmed_before_the_first_assessment_is_modified(kworld, remote, monkeypatch):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    seen = []
    real = svc.commit_regenerated

    def spy(candidate):
        seen.append({"sealed": any(k.endswith("/sealed.json") for k in _backup_keys(remote)), "objects": len([k for k in _backup_keys(remote) if "/objects/" in k])})
        return real(candidate)
    monkeypatch.setattr(svc, "commit_regenerated", spy)
    out = _secured(kworld, authorizer=_authorizer(kworld, _plan(kworld)), allow_local_only=False, run_id="run-1")
    assert out["outcome"] == bf.APPLIED and len(seen) == 2
    assert all(s == {"sealed": True, "objects": 2} for s in seen)                                                                                         # sellado + 2 objetos ANTES de cada escritura


def test_the_remote_run_holds_original_bytes_manifest_hashes_approval_and_the_journal(kworld, remote):
    before, out = _applied_remote(kworld, remote)
    base = NS + "run-1/"
    keys = _backup_keys(remote)
    assert out["secured_apply"]["backup"]["remote"] == {"run_id": "run-1", "prefix": base[:-1], "files": 5, "objects": 2, "manifest_sha256": out["secured_apply"]["backup"]["manifest_sha256"], "sealed": True}
    objs = [k for k in keys if "/objects/" in k]
    assert len(objs) == 2 and all(remote.objects[k] == before[k.rsplit("/", 1)[1]] for k in objs)                                                          # bytes ORIGINALES
    assert {k[len(base):] for k in keys if "/journal/" not in k and "/objects/" not in k} == {"manifest.json", "manifest.sha256", "approval.json", "sealed.json"}
    seal = json.loads(remote.objects[base + "sealed.json"])
    assert seal["manifest_sha256"] == gb.load_manifest(kworld.tmp / "bk")["manifest_sha256"] and all(
        __import__("hashlib").sha256(remote.objects[base + rel]).hexdigest() == sha for rel, sha in seal["files"].items())
    approval = json.loads(remote.objects[base + "approval.json"])
    assert approval["approver"] == "alice" and approval["authorized_operator"] == "bob" and approval["plan_digest"] == out["plan"]["digest"] and "signature" in approval
    ev = [json.loads(remote.objects[k]) for k in keys if "/journal/" in k]
    assert [e["event"] for e in sorted(ev, key=lambda e: e["seq"])].count("COMMITTED") == 2 and out["secured_apply"]["journal_remote_failures"] == []
    assert [e["sha256"] for e in ev if e["event"] == "COMMITTED"] and all(e["sha256"] == _digest(kworld.governed / e["key"]) for e in ev if e["event"] == "COMMITTED")


def test_the_backup_namespace_is_outside_persist_paths_and_unsafe_configs_are_refused(kworld, remote, monkeypatch):
    assert remote_store.backup_namespace_collision() is None and "migration-backups" not in config.PERSIST_PATHS
    for bad in ("migration-backups", "migration-backups/x", "migration-backups/"):
        monkeypatch.setattr(config, "PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME, bad])
        assert "colisiona" in remote_store.backup_namespace_collision()
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    snap = _snapshot(kworld)
    out = _secured(kworld, kworld.k, allow_local_only=False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "NAMESPACE_COLLISION" and _snapshot(kworld) == snap and _backup_keys(remote) == []


# ───────────────────────────── no sobrescribir / falla segura ─────────────────────────────
def test_a_used_run_id_and_an_existing_key_are_never_overwritten(kworld, remote):
    _applied_remote(kworld, remote)
    keys = _backup_keys(remote)
    frozen = {k: remote.objects[k] for k in keys}
    with pytest.raises(remote_store.RemoteBackupExists):
        remote_store.put_backup_object(keys[0], b"otro")
    with pytest.raises(dbk.DurableBackupError) as e:
        dbk.publish(kworld.tmp / "bk", MIG, "run-1")
    assert e.value.code == "RUN_ID_ALREADY_USED"
    again = bf.secured_migrate([kworld.k, kworld.k2], MIG, authorizer=lambda m, p: ma.Authorization(True, "AUTHORIZED"), backup_dir=kworld.tmp / "bk-b", allow_local_only=False, run_id="run-1", governed_state_present=ALWAYS)
    assert again["outcome"] == bf.RECOVERY_ONLY                                                                                                           # nada que migrar: ni siquiera intenta otro respaldo
    assert {k: remote.objects[k] for k in _backup_keys(remote)} == frozen


def test_a_second_migration_with_a_reused_run_id_is_refused_before_any_write(kworld, remote):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    remote.objects[NS + "run-x/sealed.json"] = b"{}"                                                                                                       # un run previo sellado con ese id
    snap = _snapshot(kworld)
    out = _secured(kworld, kworld.k, allow_local_only=False, run_id="run-x")
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "RUN_ID_ALREADY_USED" and _snapshot(kworld) == snap and remote.objects[NS + "run-x/sealed.json"] == b"{}"


def test_when_the_durable_backup_cannot_be_confirmed_no_assessment_is_modified(kworld, remote, monkeypatch):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    snap = _snapshot(kworld)
    remote.fail_puts = True
    out = _secured(kworld, kworld.k, allow_local_only=False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "REMOTE_WRITE_FAILED" and _snapshot(kworld) == snap and out["applied"] is False
    remote.fail_puts = False

    def lying(Bucket, Key, Body):                                                                                                                          # confirma pero guarda otros bytes
        remote.objects[Key] = bytes(Body)[:-1] if Key.endswith(".json") or "/objects/" in Key else bytes(Body)
    monkeypatch.setattr(remote, "put_object", lying)
    out = _secured(kworld, kworld.k, backup=kworld.tmp / "bk-2", allow_local_only=False, run_id="run-2")
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "REMOTE_BYTES_MISMATCH" and _snapshot(kworld) == snap
    assert not any(k.endswith("/sealed.json") for k in _backup_keys(remote))                                                                                # sin sello: no restaurable


def test_a_failure_publishing_the_remote_journal_stops_the_next_waves(kworld, remote, monkeypatch):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    real = dbk.publish

    def publish_then_break(*a, **k):
        r = real(*a, **k)
        remote.fail_puts = True                                                                                                                            # el diario remoto deja de poder escribirse
        return r
    monkeypatch.setattr(dbk, "publish", publish_then_break)
    out = _secured(kworld, allow_local_only=False, waves=[["Kensing"], ["Kensing2"]], run_id="run-j")
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["secured_apply"]["journal_remote_failures"] and out["secured_apply"]["not_attempted_waves"] == [["Kensing2"]]


# ───────────────────────────── permisos, borrados y limpieza ─────────────────────────────
def test_restore_mirror_up_and_delete_path_never_touch_the_backup_namespace(kworld, remote):
    _applied_remote(kworld, remote)
    frozen = {k: remote.objects[k] for k in _backup_keys(remote)}
    remote_store._restore_state = "completed"
    remote_store._restore_degraded = False
    remote_store.restore(overwrite=True)
    remote_store.mirror_up()
    for p in files(kworld):
        remote_store.delete_path(p)                                                                                                                         # borra SOLO el objeto de governed-assessments
    assert {k: remote.objects[k] for k in _backup_keys(remote)} == frozen and not (kworld.tmp / "migration-backups").exists()
    assert all(not k.startswith(NS) for k in remote.objects if "governed-assessments" in k)


def test_backup_keys_outside_the_namespace_are_rejected(kworld, remote):
    for bad in ("governed-assessments/v1/x.json", "migration-backups/../x", "migration-backups/", "other/migration-backups/x"):
        with pytest.raises(remote_store.RemoteReadError):
            remote_store.get_backup_object(bad)
    assert not [n for n in dir(remote_store) if "delete" in n and "backup" in n]                                                                              # no existe ninguna operacion de borrado de respaldos


def test_list_runs_marks_unsealed_runs_and_the_cli_lists_them_read_only(kworld, remote, capsys):
    _applied_remote(kworld, remote, run_id="run-ok")
    remote.objects[NS + "run-partial/objects/x.json"] = b"{}"                                                                                               # corrida interrumpida: sin sello
    assert dbk.list_runs(MIG) == [{"run_id": "run-ok", "sealed": True}, {"run_id": "run-partial", "sealed": False}]
    before = dict(remote.objects)
    assert bf.main(["--list-remote-backups", MIG]) == 0 and json.loads(capsys.readouterr().out)["runs"][0]["run_id"] == "run-ok" and remote.objects == before
    with pytest.raises(dbk.DurableBackupError) as e:
        dbk.fetch(MIG, "run-partial", kworld.tmp / "stg")
    assert e.value.code == "RUN_NOT_SEALED"


# ───────────────────────────── restauracion desde el respaldo durable ─────────────────────────────
def test_restore_from_the_durable_run_works_without_the_local_backup_and_needs_explicit_authorization(kworld, remote):
    before, out = _applied_remote(kworld, remote)
    shutil.rmtree(kworld.tmp / "bk")                                                                                                                      # el contenedor perdio el respaldo local
    seal = out["secured_apply"]["backup"]["manifest_sha256"]
    denied = bf.restore_from_remote_run(MIG, "run-1", kworld.tmp / "stg1", apply=True, confirm=seal[:12])
    assert denied["outcome"] == "RESTORE_DENIED"
    dry = bf.restore_from_remote_run(MIG, "run-1", kworld.tmp / "stg2")
    assert dry["counts"]["WOULD_RESTORE"] == 2 and dry["remote_run"]["run_id"] == "run-1"
    stg = kworld.tmp / "stg3"
    gb_dir = dbk.fetch(MIG, "run-1", stg)
    ok = _restore_approval(kworld, stg)
    shutil.rmtree(stg)
    monkey_keys = ma.authorizer_from_file(ok, "bob", approver_keys=KEYS)
    res = bf.restore_from_remote_run(MIG, "run-1", stg, apply=True, confirm=seal[:12], authorizer=monkey_keys)
    assert res["outcome"] == "DONE" and res["counts"]["RESTORED"] == 2 and res["counts"]["failed"] == 0 and gb_dir["manifest_sha256"] == seal
    assert {p.name: p.read_bytes() for p in files(kworld)} == before                                                                                      # originales de vuelta, local...
    for p in files(kworld):
        assert remote_store.read_object_bytes(p) == p.read_bytes()                                                                                         # ...y republicados y confirmados en remoto
    assert bf.restore_from_remote_run(MIG, "run-1", kworld.tmp / "stg4")["counts"]["ALREADY_RESTORED"] == 2


def test_a_tampered_or_unsealed_remote_run_is_unusable(kworld, remote):
    _applied_remote(kworld, remote)
    victim = next(k for k in _backup_keys(remote) if "/objects/" in k)
    remote.objects[victim] += b" "
    r = bf.restore_from_remote_run(MIG, "run-1", kworld.tmp / "stg-t")
    assert r["outcome"] == "REMOTE_BACKUP_UNUSABLE:REMOTE_OBJECT_CORRUPT"
    remote.objects[victim] = remote.objects[victim][:-1]
    del remote.objects[NS + "run-1/sealed.json"]
    assert bf.restore_from_remote_run(MIG, "run-1", kworld.tmp / "stg-u")["outcome"] == "REMOTE_BACKUP_UNUSABLE:RUN_NOT_SEALED"
    assert bf.restore_from_remote_run(MIG, "no-existe", kworld.tmp / "stg-v")["outcome"] == "REMOTE_BACKUP_UNUSABLE:RUN_NOT_SEALED"
