"""Los OTROS productores de ``catalog_local`` (regeneración legacy y APIS_ONLY) respetan el candado de escritor único ENTRE PROCESOS — solo con el interruptor encendido —
y, con otro proceso aplicando, abortan SIN modificar nada. Con el interruptor apagado se comportan exactamente como antes (no esperan ni fallan por el candado)."""
from __future__ import annotations

import pytest

from tests.test_publication_txn import _spawn_holder
from tools.catalog import publication_txn as txn
from webapp.services import admin_tools, catalog_apis_sync


@pytest.fixture()
def holder(tmp_path, monkeypatch):
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv(txn.LOCK_DIR_ENV, str(lock_dir))
    proc = _spawn_holder(lock_dir)
    yield proc
    proc.kill()
    proc.wait(10)


def test_legacy_regeneration_aborts_without_writing_when_another_process_holds_the_catalog_lock(holder, monkeypatch):
    monkeypatch.setattr(admin_tools, "run_catalog_generation", lambda graph_token="": pytest.fail("no debe regenerar con otro proceso aplicando"))
    ran, message = admin_tools.run_catalog_generation_locked()
    assert ran is False and "otro proceso" in message
    assert admin_tools._CATALOG_REGEN_LOCK.acquire(blocking=False)                                              # el candado de hilo se liberó
    admin_tools._CATALOG_REGEN_LOCK.release()


def test_legacy_regeneration_runs_normally_when_nobody_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv(txn.LOCK_DIR_ENV, str(tmp_path / "locks"))
    monkeypatch.setattr(admin_tools, "run_catalog_generation", lambda graph_token="": "regenerado")
    assert admin_tools.run_catalog_generation_locked() == (True, "regenerado")


def test_apis_only_apply_aborts_without_touching_anything_when_another_process_holds_the_lock(holder, monkeypatch):
    monkeypatch.setattr(catalog_apis_sync, "_apply_locked", lambda *a, **k: pytest.fail("no debe aplicar con otro proceso aplicando"))
    out = catalog_apis_sync.apply("tok", "plan", catalog_apis_sync.CONFIRM_PHRASE)
    assert out.state == "aborted" and any("No se aplicó nada" in line for line in out.lines)


def test_apis_only_hold_and_guard_reports_busy_while_another_process_holds_the_lock(holder):
    """Sin interruptor: APIS_ONLY siempre consulta el candado entre procesos; con otro proceso aplicando no obtiene un hold exclusivo."""
    with catalog_apis_sync._hold_and_guard() as hold:
        assert isinstance(hold, catalog_apis_sync._BusyHold) and hold.exclusive is False
