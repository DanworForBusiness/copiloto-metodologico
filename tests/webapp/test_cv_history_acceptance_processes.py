"""Entrega C — exclusión ENTRE PROCESOS entre la aceptación manual, las acciones manuales y la importación del mismo `content_key`: las tres usan el mismo candado del libro (`manifest.ledger_lock`). Aquí otro intérprete lo retiene y se
demuestra que cada camino lo respeta (la tanda difiere la unidad sin esperar; la aceptación y las acciones manuales no escriben nada y piden reintentar). No hay datos reales: SharePoint y Graph simulados."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import config
from tests.webapp.test_cv_history_acceptance import PABLO, draft, go, ready  # noqa: F401
from tests.webapp.test_cv_history_import_exec import OWNER, TOKEN, env, ex, mf, profile_document_repository, profile_repository  # noqa: F401
from tests.webapp.test_cv_history_pilot import home, sp  # noqa: F401
from webapp.services import cv_history_acceptance as acc

HOLDER = Path(__file__).resolve().parents[1] / "support_ledger_lock_holder.py"


def hold(tmp_path, rid, key):
    ready_file, release_file = tmp_path / "held", tmp_path / "release"
    proc = subprocess.Popen([sys.executable, str(HOLDER), json.dumps({"base_dir": str(config.BASE_DIR), "run_id": rid, "key": key, "ready_file": str(ready_file), "release_file": str(release_file)})],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 120
    while not ready_file.exists():
        assert time.monotonic() < deadline and proc.poll() is None, proc.stderr.read() if proc.poll() is not None else "el proceso no tomó el candado"
        time.sleep(0.02)
    return proc, release_file


def free(proc, release_file):
    release_file.write_text("soltar", encoding="utf-8")
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err[-1500:]


def test_another_process_holding_the_ledger_lock_blocks_the_acceptance_the_manual_actions_and_defers_the_batch_unit(env, tmp_path, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    proc, release = hold(tmp_path, rid, pablo)
    try:
        monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 0.4)
        puts, profiles = list(store.puts), len(profile_repository.list())
        with pytest.raises(mf.ManifestBusyError):
            go(rid, pablo, pre)                                                                                                  # la aceptación no entra
        with pytest.raises(mf.ManifestBusyError):
            ex.complete_identity(rid, pablo, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz", duplicate_confirmed=True)  # tampoco las acciones manuales viejas
        with pytest.raises(mf.ManifestBusyError):
            ex.dismiss(rid, pablo, actor=OWNER, reason="OTHER")
        assert store.puts == puts and len(profile_repository.list()) == profiles and mf.get_ledger(rid, pablo) is None          # no se escribió nada
        batch = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
        assert batch["deferred"] == 0                                                                                            # la unidad aún no existe en el libro: la tanda no la toma (solo corre lo planificado)
    finally:
        free(proc, release)
    monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 45.0)
    assert go(rid, pablo, pre)["ok"] and mf.get_ledger(rid, pablo)["state"] == ex.DONE                                           # liberado el candado, la aceptación funciona


def test_a_pending_manual_unit_is_deferred_by_the_batch_while_another_process_holds_the_lock_and_runs_later(env, tmp_path, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    store.deny_put = True
    assert go(rid, pablo, pre)["outcome"].startswith("STOP:")                                                                    # aceptación a medias: una unidad manual pendiente en el libro
    store.deny_put = False
    proc, release = hold(tmp_path, rid, pablo)
    try:
        puts = list(store.puts)
        batch = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
        assert batch["deferred"] == 1 and batch["processed"] == 0 and store.puts == puts and mf.get_ledger(rid, pablo)["state"] != ex.DONE
    finally:
        free(proc, release)
    later = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)                                                      # sin el candado ocupado, la tanda la completa (misma decisión aceptada)
    assert later["deferred"] == 0 and later["done"] == 1 and mf.get_ledger(rid, pablo)["state"] == ex.DONE and mf.get_ledger(rid, pablo)["accepted"]["by"] == OWNER
    assert len(profile_document_repository.list_for_profile(mf.get_ledger(rid, pablo)["profile_id"])) == 1


def test_the_lock_is_per_content_so_a_held_unit_does_not_block_another(env, tmp_path, monkeypatch):
    store, rid, pablo = ready(env)
    scanned = next(k for k, g in ((g["content_key"], g) for g in mf.list_groups(rid)) if g["representative"]["name"] == "escaneado.pdf")
    proc, release = hold(tmp_path, rid, pablo)
    try:
        monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 0.4)
        assert ex.dismiss(rid, scanned, actor=OWNER, reason="NOT_A_CV")["ok"]                                                    # otro contenido: sin espera
    finally:
        free(proc, release)
