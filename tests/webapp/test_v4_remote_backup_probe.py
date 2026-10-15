"""CLI ``python -m tools.catalog.v4_remote_backup_probe``: prueba inocua del respaldo durable. Spaces FALSO en memoria sobre copias aisladas (nunca produccion): dry-run por defecto sin escrituras,
``--execute`` con confirmacion exacta, un unico run ``probe-*`` de 4 objetos, verificaciones por bytes/hashes/sello, sin tocar Assessments ni borrar, y falla cerrada ante la incertidumbre."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import config
from core import remote_store
from tests.webapp.test_assessment_leaf_materialization_migration import MIG, _make_v3, _snapshot, kworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import _publish_all, remote  # noqa: F401
from tools.catalog import v4_remote_backup_probe as probe
from webapp.services import durable_backup as dbk
from webapp.services import governed_backup as gb

NS = f"migration-backups/{MIG}/"
OK = ["--execute", "--confirm", probe.CONFIRM_PHRASE]


def _world(kworld, remote):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    return _snapshot(kworld), dict(remote.objects)


def _run(capsys, *argv):
    code = probe.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# ───────────────────────────── dry-run (por defecto): cero escrituras ─────────────────────────────
def test_dry_run_is_the_default_and_writes_nothing(kworld, remote, capsys, tmp_path, monkeypatch):
    snap, before = _world(kworld, remote)
    created = []
    real_mkdtemp = probe.tempfile.mkdtemp
    monkeypatch.setattr(probe.tempfile, "mkdtemp", lambda *a, **k: created.append(1) or real_mkdtemp(*a, **k))
    for argv in ([], ["--dry-run"]):
        code, rep = _run(capsys, *argv)
        assert code == 0 and rep["mode"] == "dry_run" and rep["writes_performed"] == 0 and rep["would_execute"] is True and rep["blockers"] == []
        assert rep["expected_object_count"] == 4 and rep["would_write_files"][1:] == ["manifest.json", "manifest.sha256", "sealed.json"] and rep["would_write_files"][0].startswith("objects/")
        assert rep["original_local_sha256"] == rep["original_remote_sha256"] and rep["prior_runs_in_namespace"] == [] and rep["confirm_phrase_required_for_execute"] == probe.CONFIRM_PHRASE
        assert rep["assessments_modified"] is False and rep["migration_executed"] is False and rep["deletes_performed"] == 0
    assert remote.objects == before and _snapshot(kworld) == snap and created == []                                       # ni remoto ni staging local


@pytest.mark.parametrize("breaker, blocker", [
    ("no_remote", "REMOTE_READ_NOT_ENABLED"), ("collision", "NAMESPACE_COLLISION"), ("differs", "REMOTE_DIFFERS_FROM_LOCAL"), ("absent", "REMOTE_ORIGINAL_ABSENT"), ("empty_plan", "EMPTY_PLAN"),
])
def test_dry_run_reports_blockers_and_exits_2_without_writing(kworld, remote, capsys, monkeypatch, breaker, blocker):
    snap, before = _world(kworld, remote)
    if breaker == "no_remote":
        monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    elif breaker == "collision":
        monkeypatch.setattr(config, "PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME, "migration-backups"])
    elif breaker == "differs":
        k = next(iter(remote.objects))
        remote.objects[k] += b" "
    elif breaker == "absent":
        remote.objects.clear()
    elif breaker == "empty_plan":
        from webapp.services import assessment_backfill as bf
        monkeypatch.setattr(bf, "migrate_stale_evidence", lambda entries, mig, **k: {"plan": {"items": [], "digest": "x", "environment": "e", "clients": []}})
    before = dict(remote.objects)
    code, rep = _run(capsys)
    assert code == probe.EXIT_BLOCKED and rep["would_execute"] is False and any(b.startswith(blocker) for b in rep["blockers"]) and rep["writes_performed"] == 0
    assert remote.objects == before and _snapshot(kworld) == snap


# ───────────────────────────── --execute: confirmacion explicita ─────────────────────────────
@pytest.mark.parametrize("argv", [["--execute"], ["--execute", "--confirm", "yes"], ["--execute", "--confirm", probe.CONFIRM_PHRASE.lower()]])
def test_execute_without_the_exact_confirmation_is_refused_with_zero_writes(kworld, remote, capsys, argv):
    snap, before = _world(kworld, remote)
    code, rep = _run(capsys, *argv)
    assert code == probe.EXIT_NOT_CONFIRMED and rep["error"] == "CONFIRMATION_REQUIRED" and rep["writes_performed"] == 0 and remote.objects == before and _snapshot(kworld) == snap


def test_confirm_without_execute_is_a_usage_error(kworld, remote):
    with pytest.raises(SystemExit) as e:
        probe.main(["--confirm", probe.CONFIRM_PHRASE])
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        probe.main(["--dry-run", "--execute"])                                                                              # excluyentes


# ───────────────────────────── --execute: camino feliz acotado ─────────────────────────────
def test_execute_writes_exactly_four_objects_in_one_probe_run_and_verifies_everything(kworld, remote, capsys):
    snap, before = _world(kworld, remote)
    code, rep = _run(capsys, *OK)
    assert code == 0 and rep["verdict_ok"] is True and rep["mode"] == "execute" and rep["run_id"].startswith("probe-")
    keys = rep["exact_keys_written"]
    assert len(keys) == 4 == rep["writes_performed"] and all(k.startswith(f"{NS}{rep['run_id']}/") for k in keys)
    assert sorted(k.rsplit("/", 1)[-1] for k in keys if "/objects/" not in k) == ["manifest.json", "manifest.sha256", "sealed.json"] and sum("/objects/" in k for k in keys) == 1
    new = {k for k in remote.objects if k not in before}
    assert new == set(keys) and {k: remote.objects[k] for k in before} == before                                           # solo 4 claves nuevas; nada preexistente cambia ni se borra
    v = rep["verifications"]
    assert v["key_count_is_4"] and v["keys_all_under_probe_run"] and v["sealed"] and v["seal_manifest_sha256_matches_local"] and v["hash_checks_all_true"] and v["fetched_objects"] == 1
    assert v["fetched_verify_problems"] == [] and v["original_envelope_unchanged_local"] and v["original_envelope_unchanged_remote"] and v["governed_store_tree_unchanged"]
    assert rep["assessments_modified"] is False and rep["migration_executed"] is False and rep["deletes_performed"] == 0 and _snapshot(kworld) == snap
    assert dbk.list_runs(MIG) == [{"run_id": rep["run_id"], "sealed": True}]


def test_the_report_and_logs_contain_no_secrets_endpoints_or_profile_content(kworld, remote, capsys, caplog):
    _world(kworld, remote)
    caplog.set_level("DEBUG")
    probe.main(list(OK))
    out = capsys.readouterr().out
    blob = out + "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("fake-key", "fake-secret", "fake.digitaloceanspaces.com", "fake-bucket"):
        assert secret not in blob
    assert "profile_json" not in out and '"canonical_evidence"' not in out


def test_a_second_run_in_the_same_second_fails_closed_without_touching_the_first(kworld, remote, capsys, monkeypatch):
    _world(kworld, remote)
    monkeypatch.setattr(probe, "new_probe_run_id", lambda now=None: "probe-20261015T130000Z")
    assert _run(capsys, *OK)[0] == 0
    frozen = {k: v for k, v in remote.objects.items() if k.startswith(NS)}
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and rep["error"] == "RUN_ALREADY_EXISTS" and {k: v for k, v in remote.objects.items() if k.startswith(NS)} == frozen


# ───────────────────────────── --execute: falla cerrada ─────────────────────────────
def test_execute_refuses_when_blocked_and_writes_nothing(kworld, remote, capsys, monkeypatch):
    snap, _ = _world(kworld, remote)
    monkeypatch.setattr(config, "PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME, "migration-backups"])
    before = dict(remote.objects)
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and rep["error"] == "BLOCKED_BEFORE_ANY_WRITE" and "NAMESPACE_COLLISION" in rep["blockers"] and rep["writes_performed"] == 0
    assert remote.objects == before and _snapshot(kworld) == snap


def test_a_remote_that_differs_from_local_aborts_before_any_remote_write(kworld, remote, capsys):
    _world(kworld, remote)
    remote.objects[next(iter(remote.objects))] += b" "
    before = dict(remote.objects)
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and "REMOTE_DIFFERS_FROM_LOCAL" in rep["blockers"] and remote.objects == before                  # evitaria un 5.o objeto (objects_remote/)


def test_an_unexpected_backup_file_set_aborts_before_any_remote_write(kworld, remote, capsys, monkeypatch):
    _world(kworld, remote)
    real = gb.create_backup

    def with_extra(dest, targets, **kw):
        m = real(dest, targets, **kw)
        (Path(dest) / "objects" / "extra.json").write_bytes(b"{}")
        return m
    monkeypatch.setattr(gb, "create_backup", with_extra)
    before = dict(remote.objects)
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and rep["error"] == "UNEXPECTED_BACKUP_FILE_SET" and remote.objects == before and rep["writes_performed"] == 0


def test_only_probe_runs_are_ever_created(kworld, remote, capsys, monkeypatch):
    _world(kworld, remote)
    monkeypatch.setattr(probe, "new_probe_run_id", lambda now=None: "run-real-1")
    before = dict(remote.objects)
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and rep["error"] == "BAD_RUN_ID" and remote.objects == before


def test_a_put_failure_is_reported_nothing_is_deleted_and_no_seal_exists(kworld, remote, capsys, monkeypatch):
    snap, before = _world(kworld, remote)
    real_put = remote.put_object
    sent = []

    def flaky(Bucket, Key, Body):
        if len(sent) >= 2:
            raise RuntimeError("simulated outage")
        sent.append(Key)
        real_put(Bucket, Key, Body)
    monkeypatch.setattr(remote, "put_object", flaky)
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and rep["error"] == "REMOTE_WRITE_FAILED" and len(rep["exact_keys_written"]) == 2 == rep["writes_performed"]
    assert not any(k.endswith("/sealed.json") for k in remote.objects) and {k: remote.objects[k] for k in before} == before and _snapshot(kworld) == snap   # lo parcial NO se borra; sin sello = inutilizable
    assert rep["deletes_performed"] == 0 and rep["assessments_modified"] is False


def test_bytes_that_do_not_match_what_was_sent_fail_the_probe(kworld, remote, capsys, monkeypatch):
    _world(kworld, remote)

    def lying(Bucket, Key, Body):
        remote.objects[Key] = bytes(Body)[:-1] if "/objects/" in Key else bytes(Body)
    monkeypatch.setattr(remote, "put_object", lying)
    code, rep = _run(capsys, *OK)
    assert code == probe.EXIT_FAILED and rep["error"] == "REMOTE_BYTES_MISMATCH" and not any(k.endswith("/sealed.json") for k in remote.objects)


def test_it_never_deletes_anything_remote_neither_on_success_nor_on_failure(kworld, remote, capsys, monkeypatch):
    _world(kworld, remote)
    deleted = []
    remote.delete_object = lambda **k: deleted.append(k)
    monkeypatch.setattr(remote_store, "delete_path", lambda *a, **k: deleted.append("delete_path"))
    assert _run(capsys, *OK)[0] == 0                                                                                        # exito
    monkeypatch.setattr(remote, "fail_puts", True)
    monkeypatch.setattr(probe, "new_probe_run_id", lambda now=None: "probe-20261015T140000Z")
    assert _run(capsys, *OK)[0] == probe.EXIT_FAILED                                                                        # falla
    assert deleted == []


def test_the_module_is_runnable_as_python_dash_m_and_shows_short_usage():
    out = subprocess.run([sys.executable, "-m", "tools.catalog.v4_remote_backup_probe", "--help"], capture_output=True, text=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0 and "--execute" in out.stdout and "--dry-run" in out.stdout and probe.CONFIRM_PHRASE in out.stdout
