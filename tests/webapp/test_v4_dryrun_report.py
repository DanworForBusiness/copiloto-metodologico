"""CLI ``python -m tools.catalog.v4_dryrun_report``: dry-run de ``evidence_leaf_materialization_v4`` con informe por cliente, digests y verificacion de ausencia de escrituras. Entornos aislados
(``tmp_path``) con Spaces FALSO en memoria: nunca produccion. Cubre el informe, la equivalencia con el CLI real ``--dry-run``, la ausencia de opciones de escritura, el cliente S3 de solo lectura,
la deteccion de escrituras (hash local, listado remoto, audit hook) y la falla cerrada."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import remote_store
from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import MIG, _make_v3, _path, _rewrite, _snapshot, kworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import _publish_all, remote  # noqa: F401
from tools.catalog import v4_dryrun_report as rpt
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc


@pytest.fixture()
def etag_remote(remote):
    """El Spaces falso del repo + ETag/tamaño en el listado (como el real), para detectar modificaciones remotas."""
    def paginate(Bucket, Prefix):
        return [{"Contents": [{"Key": k, "ETag": '"' + hashlib.md5(v).hexdigest() + '"', "Size": len(v)} for k, v in remote.objects.items() if k.startswith(Prefix)]}]
    remote.paginate = paginate
    return remote


def _world(kworld, remote):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    return _snapshot(kworld), dict(remote.objects)


def _run(capsys, *argv):
    code = rpt.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# ───────────────────────────── informe ─────────────────────────────
def test_the_report_covers_eligibility_signatures_digests_and_proves_no_writes(kworld, etag_remote, capsys):
    snap, before = _world(kworld, etag_remote)
    original_client = remote_store._get_client
    code, rep = _run(capsys)
    assert code == 0 and rep["dry_run_executed"] is True and rep["blocked"] is False and rep["apply_flag_used"] is False and rep["writes_options_available"] is False
    assert rep["counts"]["eligible"] == 2 and rep["counts"]["pending_migration_compat"] == 2 and rep["counts"]["would_regenerate"] == 2 and rep["envelopes_total"] == 2
    pc = rep["per_client"]["Kensing"]
    assert pc["eligible"] == 1 and pc["pending_migration_compat"] == 1 and pc["states"] == {"CURRENT|None": 1} and pc["pool_signature"] == {"coincide": 1} and pc["abstained"] == {} and pc["failed"] == {}
    assert pc["judgment_actions"] == {"judgment_signature_rebound": 1}
    js = pc["judgment_signatures"][0]
    assert js["ruleset_old"] == js["ruleset_new"] == svc.JUDGMENT_CONTRACT_VERSIONS[1] and js["output_signature_old"] != js["output_signature_new"] and js["source_profile_signature_old"] != js["source_profile_signature_new"]
    plan = rep["plan"]
    assert plan["items"] == 2 and plan["clients"] == ["Kensing", "Kensing2"] and plan["plan_digest"] and plan["items_digest_env_independent"] and set(plan["by_client_items_digest"]) == {"Kensing", "Kensing2"}
    assert rep["divergences"] == []
    z = rep["zero_writes_evidence"]
    assert z["no_writes_verified"] is True and z["governed_local_tree_identical_before_after"] and z["remote_listing_identical_before_after"] and z["blocked_s3_write_attempts"] == {}
    assert z["governed_local_files"] == 2 and z["remote_listing_entries"] == 2 and z["audit_events_during_dry_run"] == {} and z["read_only_s3_calls"] == {}      # un dry-run de pendientes no necesita leer el bucket durante la corrida
    assert _snapshot(kworld) == snap and etag_remote.objects == before and remote_store._get_client is original_client                    # nada cambia; el cliente real queda restaurado


def test_it_runs_the_same_dry_run_as_the_real_cli_and_reuses_its_plan_and_digest_function(kworld, etag_remote, capsys):
    _world(kworld, etag_remote)
    code, rep = _run(capsys)
    assert bf.main(["--regenerate-stale-evidence", MIG, "--dry-run"]) in (0, 2)
    cli = json.loads(capsys.readouterr().out)
    assert rep["plan"]["plan_digest"] == cli["plan"]["digest"] and rep["plan"]["items"] == len(cli["plan"]["items"]) and rep["plan"]["clients"] == cli["plan"]["clients"]
    assert {k: v for k, v in cli["counts"].items() if v} == rep["counts"] and rep["cli_equivalent"].endswith("--dry-run")
    from webapp.services import migration_approval as ma
    assert rep["plan"]["items_digest_env_independent"] == ma.compute_plan_digest(MIG, "", cli["plan"]["items"])


def test_brief_drops_only_the_per_object_judgment_signatures(kworld, etag_remote, capsys):
    _world(kworld, etag_remote)
    _, full = _run(capsys)
    code, brief = _run(capsys, "--brief")
    assert code == 0 and "judgment_signatures" not in brief["per_client"]["Kensing"] and full["per_client"]["Kensing"]["judgment_signatures"]
    assert brief["plan"] == full["plan"] and brief["counts"] == full["counts"]


def test_divergences_pool_difference_and_other_stale_causes_are_reported(kworld, etag_remote, capsys):
    _world(kworld, etag_remote)
    _rewrite(_path(kworld, "Kensing2"), lambda e: (e.update(input_pool_signature="0" * 64), e.pop("input_closure_signature", None)))
    restart()
    code, rep = _run(capsys)
    assert code == 0 and rep["counts"]["eligible"] == 1 and rep["per_client"]["Kensing2"]["pool_signature"] == {"DIFIERE": 1}
    assert rep["per_client"]["Kensing2"]["other_reasons"] == {"version_and_input_changed": 1} and {"client": "Kensing2", "kind": "POOL_SIGNATURE_DIFFERS", "count": 1} in rep["divergences"]


def test_abstentions_are_reported_and_exit_3(kworld, etag_remote, capsys, monkeypatch):
    _world(kworld, etag_remote)
    real = svc.regenerate_stale_evidence

    def abstain(entry, migration):
        if entry.cliente == "Kensing2":
            raise svc.EnrichmentError("CLOSURE_DRIFT")
        return real(entry, migration)
    monkeypatch.setattr(svc, "regenerate_stale_evidence", abstain)
    code, rep = _run(capsys)
    assert code == rpt.EXIT_ABSTENTIONS and rep["per_client"]["Kensing2"]["abstained"] == {"CLOSURE_DRIFT": 1} and rep["zero_writes_evidence"]["no_writes_verified"] is True
    assert {"client": "Kensing2", "kind": "ABSTENTIONS", "causes": {"CLOSURE_DRIFT": 1}} in rep["divergences"]


def test_the_report_has_no_secrets_endpoints_or_profile_content(kworld, etag_remote, capsys):
    _world(kworld, etag_remote)
    rpt.main([])
    out = capsys.readouterr().out
    for secret in ("fake-key", "fake-secret", "fake.digitaloceanspaces.com", "fake-bucket"):
        assert secret not in out
    assert "profile_json" not in out and '"summary"' not in out


# ───────────────────────────── sin opciones de escritura ─────────────────────────────
def test_there_is_no_apply_or_write_option_and_the_source_never_applies(capsys):
    for bad in (["--apply"], ["--execute"], ["--confirm", "x"], ["--out", "x.json"]):
        with pytest.raises(SystemExit) as e:
            rpt.main(bad)
        assert e.value.code == 2
    src = Path(rpt.__file__).read_text(encoding="utf-8")
    for forbidden in ("apply=True", "secured_migrate", "commit_stale_migration", "restore_from", "put_backup_object", "durable_backup", ".put_object(", ".upload_file(", ".delete_object("):
        assert forbidden not in src, forbidden


def test_the_module_runs_as_python_dash_m_and_help_lists_only_brief():
    out = subprocess.run([sys.executable, "-m", "tools.catalog.v4_dryrun_report", "--help"], capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0 and "--brief" in out.stdout and "--apply" not in out.stdout and "--execute" not in out.stdout


# ───────────────────────────── cliente S3 de solo lectura ─────────────────────────────
def test_the_read_only_client_allows_list_get_head_and_blocks_everything_else(etag_remote):
    guard = rpt.ReadOnlyClient(etag_remote)
    etag_remote.objects["k"] = b"v"
    assert guard.get_object(Bucket="b", Key="k")["Body"].read() == b"v" and guard.get_paginator("list_objects_v2") is not None
    for name in ("put_object", "upload_file", "delete_object", "copy_object", "delete_objects", "upload_fileobj"):
        with pytest.raises(PermissionError):
            getattr(guard, name)(Bucket="b", Key="k", Body=b"x")
    assert sum(guard.blocked.values()) == 6 and etag_remote.objects == {"k": b"v"}


# ───────────────────────────── falla cerrada ─────────────────────────────
def test_blockers_stop_before_running_anything(kworld, etag_remote, capsys, monkeypatch):
    _world(kworld, etag_remote)
    called = []
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda *a, **k: called.append(1))
    from webapp.services import catalog_reads
    import config
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)                                                                    # sin lectura remota
    code, rep = _run(capsys)
    assert code == rpt.EXIT_BLOCKED and rep["blocked"] is True and "REMOTE_READ_NOT_ENABLED" in rep["blockers"] and rep["dry_run_executed"] is False and called == []
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (None, []))
    code, rep = _run(capsys)
    assert code == rpt.EXIT_BLOCKED and "NO_CATALOG_ENTRIES" in rep["blockers"] and called == []


def test_no_governed_state_blocks(kworld, etag_remote, capsys):
    code, rep = _run(capsys)                                                                                                 # almacen vacio: nada que reportar
    assert code == rpt.EXIT_BLOCKED and "NO_GOVERNED_STATE" in rep["blockers"] and rep["dry_run_executed"] is False


def test_an_attempted_remote_write_is_blocked_and_fails_the_verdict(kworld, etag_remote, capsys, monkeypatch):
    snap, before = _world(kworld, etag_remote)
    real = bf.migrate_stale_evidence

    def sneaky(entries, mig, **kw):
        out = real(entries, mig, **kw)
        assert remote_store.push_path(files(kworld)[0]) is False                                                             # push_path atrapa el PermissionError: el bloqueo no se pierde
        return out
    monkeypatch.setattr(bf, "migrate_stale_evidence", sneaky)
    code, rep = _run(capsys)
    z = rep["zero_writes_evidence"]
    assert code == rpt.EXIT_WRITES_DETECTED and z["no_writes_verified"] is False and z["blocked_s3_write_attempts"] and etag_remote.objects == before and _snapshot(kworld) == snap


def test_a_local_modification_of_a_governed_envelope_fails_the_verdict(kworld, etag_remote, capsys, monkeypatch):
    _world(kworld, etag_remote)
    real = bf.migrate_stale_evidence

    def writes_locally(entries, mig, **kw):
        out = real(entries, mig, **kw)
        p = files(kworld)[0]
        p.write_bytes(p.read_bytes() + b" ")
        return out
    monkeypatch.setattr(bf, "migrate_stale_evidence", writes_locally)
    code, rep = _run(capsys)
    assert code == rpt.EXIT_WRITES_DETECTED and rep["zero_writes_evidence"]["governed_local_tree_identical_before_after"] is False and rep["zero_writes_evidence"]["no_writes_verified"] is False


def test_the_audit_hook_flags_file_writes_outside_the_temp_dir(kworld, etag_remote, capsys, monkeypatch, tmp_path):
    _world(kworld, etag_remote)
    real = bf.migrate_stale_evidence
    monkeypatch.setattr(rpt.WriteWatch, "tmp", str(tmp_path / "elsewhere"))                                                    # el "temporal" pasa a otro lado: todo lo del mundo aislado cuenta como externo

    def writes_a_file(entries, mig, **kw):
        out = real(entries, mig, **kw)
        (kworld.tmp / "stray.txt").write_text("x")
        return out
    monkeypatch.setattr(bf, "migrate_stale_evidence", writes_a_file)
    code, rep = _run(capsys)
    z = rep["zero_writes_evidence"]
    assert code == rpt.EXIT_WRITES_DETECTED and z["audit_events_during_dry_run"].get("file_write_outside_tmp") and any("stray.txt" in p for p in z["write_paths_outside_tmp"]) and z["no_writes_verified"] is False


def test_an_exception_during_the_run_fails_closed_without_detail_and_restores_the_client(kworld, etag_remote, capsys, monkeypatch):
    _world(kworld, etag_remote)
    original_client = remote_store._get_client
    monkeypatch.setattr(bf, "migrate_stale_evidence", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secreto-en-el-mensaje https://x")))
    code, rep = _run(capsys)
    assert code == rpt.EXIT_ERROR and rep["error"] == "RuntimeError" and rep["dry_run_executed"] is False and "secreto" not in json.dumps(rep) and remote_store._get_client is original_client
