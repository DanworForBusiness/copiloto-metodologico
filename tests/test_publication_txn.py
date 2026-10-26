"""Primitivas de la fase 0 (H5/H6): escritura atómica, manifiestos con integridad y carga tri-estado, candado de escritor único ENTRE PROCESOS, journal/undo/recuperación."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import atomic_io
from tools.catalog import publication_txn as txn

REPO = Path(__file__).resolve().parents[1]


# ─────────────── H6 · escritura atómica ───────────────
def test_atomic_write_replaces_the_whole_file_and_leaves_no_temporaries(tmp_path):
    target = tmp_path / "a" / "x.json"
    atomic_io.write_text(target, "uno")
    atomic_io.write_text(target, "dos")
    assert target.read_text(encoding="utf-8") == "dos" and [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_a_failed_atomic_write_keeps_the_previous_content_and_cleans_its_temporary(tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    atomic_io.write_text(target, "viejo-completo")
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("muere antes del replace")))
    with pytest.raises(OSError):
        atomic_io.write_text(target, "nuevo-que-no-llega")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "viejo-completo" and list(tmp_path.glob(".tmp-atomic-*")) == []


def test_orphan_temporaries_left_by_a_crash_are_removed(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / (atomic_io.TMP_PREFIX + "zz.part")).write_text("basura", encoding="utf-8")
    assert atomic_io.remove_orphan_temporaries(tmp_path) == 1 and not any(tmp_path.rglob(atomic_io.TMP_PREFIX + "*"))


# ─────────────── H6 · manifiestos: integridad y carga tri-estado ───────────────
def _manifest(**over):
    base = {"schemaVersion": 3, "client": "Acme", "space": "PROD", "rootObject": "AM", "objects": [{"object_type": "views", "technical_name": "V", "sha256": "x"}]}
    base.update(over)
    return base


def test_load_manifest_is_tri_state_and_a_failure_is_never_an_empty_manifest(tmp_path):
    p = tmp_path / "lineage-manifest.json"
    assert txn.load_manifest(p).state == txn.M_ABSENT
    p.write_text(json.dumps(txn.seal_manifest(_manifest())), encoding="utf-8")
    ok = txn.load_manifest(p)
    assert ok.state == txn.M_OK and ok.usable and ok.data["rootObject"] == "AM"
    p.write_text(p.read_text(encoding="utf-8")[:40], encoding="utf-8")                                 # truncado
    broken = txn.load_manifest(p)
    assert broken.state == txn.M_UNREADABLE and not broken.usable and broken.data == {}
    p.write_text(json.dumps({"objects": "no-es-una-lista"}), encoding="utf-8")
    assert txn.load_manifest(p).state == txn.M_INVALID_SCHEMA
    p.write_text(json.dumps({"objects": [{"object_type": 1, "technical_name": "V"}]}), encoding="utf-8")
    assert txn.load_manifest(p).state == txn.M_INVALID_SCHEMA


def test_a_tampered_v3_manifest_is_detected_and_a_legacy_v2_manifest_stays_valid(tmp_path):
    p = tmp_path / "m.json"
    sealed = txn.seal_manifest(_manifest())
    sealed["rootObject"] = "OTRO"                                                                        # alterado después de sellar
    p.write_text(json.dumps(sealed), encoding="utf-8")
    assert txn.load_manifest(p).state == txn.M_DIGEST_MISMATCH
    legacy = _manifest(schemaVersion=2)                                                                  # v2: sin ``integrity``
    p.write_text(json.dumps(legacy), encoding="utf-8")
    assert txn.load_manifest(p).state == txn.M_OK


def test_semantic_signature_ignores_volatile_fields_only():
    a = txn.seal_manifest(_manifest(createdAt="t1", operationId="o1", hostId="h1", cleanup={"deletedStaleObjects": 0}))
    b = txn.seal_manifest(_manifest(createdAt="t2", operationId="o2", hostId="h2", cleanup={"deletedStaleObjects": 3}))
    c = txn.seal_manifest(_manifest(objects=[{"object_type": "views", "technical_name": "W", "sha256": "x"}]))
    assert txn.semantic_signature(a) == txn.semantic_signature(b) != txn.semantic_signature(c)


# ─────────────── H5 · candado de escritor único ───────────────
@pytest.fixture()
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(txn.LOCK_DIR_ENV, str(tmp_path / "locks"))
    return tmp_path / "locks"


def test_the_writer_lock_is_reentrant_for_the_same_thread(lock_dir):
    with txn.writer_lock(0):
        with txn.writer_lock(0):
            pass


def test_another_thread_cannot_enter_and_gets_busy_without_side_effects(lock_dir):
    got: list = []
    with txn.writer_lock(1):
        def attempt():
            try:
                with txn.writer_lock(0):
                    got.append("entered")
            except txn.CatalogBusyError:
                got.append("busy")
        t = threading.Thread(target=attempt)
        t.start()
        t.join(5)
    assert got == ["busy"]
    with txn.writer_lock(0):                                                                              # liberado al salir
        pass


_HOLDER = """
import sys, time
sys.path.insert(0, {repo!r})
from tools.catalog import publication_txn as txn
with txn.writer_lock(5):
    print("locked", flush=True)
    time.sleep(120)
"""


def _spawn_holder(lock_dir: Path) -> subprocess.Popen:
    env = {**os.environ, txn.LOCK_DIR_ENV: str(lock_dir), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER.format(repo=str(REPO))], stdout=subprocess.PIPE, text=True, env=env, cwd=str(REPO))
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_the_lock_excludes_another_PROCESS_and_the_os_releases_it_when_the_holder_dies(lock_dir):
    holder = _spawn_holder(lock_dir)
    try:
        t0 = time.monotonic()
        with pytest.raises(txn.CatalogBusyError):
            with txn.writer_lock(0.3):
                pytest.fail("no debe entrar mientras otro proceso lo tiene")
        assert time.monotonic() - t0 >= 0.25                                                              # esperó el timeout y NO escribió nada
    finally:
        holder.kill()                                                                                     # muerte abrupta del titular (equivale a kill -9)
        holder.wait(10)
    with txn.writer_lock(5):                                                                              # el SO liberó el candado: se obtiene sin intervención
        pass


def test_external_producers_only_take_the_lock_when_the_switch_is_on(lock_dir, monkeypatch):
    monkeypatch.delenv(txn.SAFE_ENV, raising=False)
    holder = _spawn_holder(lock_dir)
    try:
        with txn.external_writer_guard(0):                                                                # interruptor apagado: comportamiento de siempre (no espera ni falla)
            pass
        monkeypatch.setenv(txn.SAFE_ENV, "1")
        with pytest.raises(txn.CatalogBusyError):
            with txn.external_writer_guard(0):
                pass
    finally:
        holder.kill()
        holder.wait(10)


def test_the_switch_is_off_by_default_and_parses_truthy_values(monkeypatch):
    monkeypatch.delenv(txn.SAFE_ENV, raising=False)
    assert txn.safe_mode_enabled() is False
    for value, expected in (("1", True), ("true", True), ("ON", True), ("yes", True), ("0", False), ("off", False), ("", False), ("no", False)):
        monkeypatch.setenv(txn.SAFE_ENV, value)
        assert txn.safe_mode_enabled() is expected, value


# ─────────────── journal, undo y recuperación ───────────────
def test_undo_restores_modified_deleted_and_created_paths_and_whole_directories(tmp_path):
    root = tmp_path / "catalog_local"
    (root / "Acme").mkdir(parents=True)
    keep, gone, created, dir_file = root / "keep.json", root / "gone.json", root / "created.json", root / "Acme" / "f.md"
    keep.write_text("v1", encoding="utf-8")
    gone.write_text("g1", encoding="utf-8")
    dir_file.write_text("md1", encoding="utf-8")
    undo = txn.UndoLog(txn.undo_dir(root, "op1"))
    for p in (keep, gone, created, root / "Acme"):
        undo.capture(p)
    undo.save()
    keep.write_text("v2", encoding="utf-8")
    gone.unlink()
    created.write_text("nuevo", encoding="utf-8")
    dir_file.write_text("md2", encoding="utf-8")
    (root / "Acme" / "extra.md").write_text("x", encoding="utf-8")
    assert txn.UndoLog.load(txn.undo_dir(root, "op1")).restore() == []
    assert keep.read_text(encoding="utf-8") == "v1" and gone.read_text(encoding="utf-8") == "g1" and not created.exists()
    assert dir_file.read_text(encoding="utf-8") == "md1" and not (root / "Acme" / "extra.md").exists()


def _journal(root: Path, op: str, state: str, **extra):
    j = txn.Journal(root, op, {"operation_id": op, **extra})
    j.update(state)
    return j


def test_recovery_rolls_back_an_applying_operation_and_discards_a_planned_one(tmp_path):
    root = tmp_path / "catalog_local"
    root.mkdir()
    victim = root / "v.json"
    victim.write_text("antes", encoding="utf-8")
    undo = txn.UndoLog(txn.undo_dir(root, "opA"))
    undo.capture(victim)
    undo.save()
    victim.write_text("a-medias", encoding="utf-8")
    _journal(root, "opA", txn.J_APPLYING, touched_dirs=[str(root)])
    _journal(root, "opP", txn.J_PLANNED)
    assert txn.has_pending(root)
    results = {r["operation_id"]: r["action"] for r in txn.recover_pending(root)}
    assert results == {"opA": "ROLLED_BACK", "opP": "DISCARDED"}
    assert victim.read_text(encoding="utf-8") == "antes" and not txn.has_pending(root) and txn.recover_pending(root) == []


def test_recovery_failure_keeps_the_journal_pending_and_raises(tmp_path, monkeypatch):
    root = tmp_path / "catalog_local"
    root.mkdir()
    f = root / "v.json"
    f.write_text("antes", encoding="utf-8")
    undo = txn.UndoLog(txn.undo_dir(root, "opX"))
    undo.capture(f)
    undo.save()
    _journal(root, "opX", txn.J_APPLYING)
    monkeypatch.setattr(txn.UndoLog, "restore", lambda self: ["v.json"])
    with pytest.raises(txn.RecoveryError):
        txn.recover_pending(root)
    assert txn.has_pending(root)


def test_a_committed_journal_is_not_touched_by_recovery_and_runtime_lives_outside_the_catalog(tmp_path):
    root = tmp_path / "catalog_local"
    root.mkdir()
    _journal(root, "opC", txn.J_LOCAL_COMMITTED)
    assert txn.recover_pending(root) == [] and not txn.has_pending(root)
    assert txn.runtime_dir(root).parent == root.parent and root not in txn.runtime_dir(root).parents                # FUERA de catalog_local: mirror_up no lo ve
