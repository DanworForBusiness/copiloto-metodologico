"""
test_commercial_licensing_priority_repository.py — Commercial Licensing,
Director Requests §5-§8: contrato de
`webapp.services.commercial_licensing_priority_repository`, sin HTTP, sin
Spaces real (todo monkeypatcheado, MISMO patrón que
test_profile_repository.py). Aísla cada test en su propio `tmp_path` (vía
`config.BASE_DIR`)."""
from __future__ import annotations

import config
import pytest

from webapp.services import commercial_licensing_priority_repository as repo


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _remote_store_noop(monkeypatch):
    # Default determinístico: Spaces deshabilitado, push_path/delete_path
    # devuelven False sin lanzar — los tests que prueban explícitamente el
    # camino de durabilidad remota sobreescriben esto en su propio cuerpo.
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: False)


# ============================================================================
# P/Q/R — mover a secundario
# ============================================================================

def test_p_new_account_starts_absent_ie_in_main_by_default():
    assert repo.list_deferred_account_ids() == set()


def test_q_and_r_defer_removes_from_main_appears_in_secondary(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    assert repo.list_deferred_account_ids() == {42}


def test_defer_is_idempotent_re_deferring_does_not_duplicate(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="a@danwor.com")
    repo.defer(42, by="b@danwor.com")
    assert repo.list_deferred_account_ids() == {42}


def test_defer_without_durable_backend_configured_raises():
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.defer(42, by="director@danwor.com")


def test_defer_when_remote_push_fails_but_enabled_raises_and_still_written_locally(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.defer(42, by="director@danwor.com")
    # El archivo local SE escribió (write-then-confirm) -- un reintento
    # posterior con Spaces funcionando de nuevo alcanza con reintentar
    # `defer()`, nunca pierde el registro local ya escrito.
    path = repo._record_path(42)
    assert path.exists()


# ============================================================================
# T/U — restaurar
# ============================================================================

def test_t_and_u_restore_removes_from_secondary_account_returns_to_main(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    assert repo.list_deferred_account_ids() == {42}
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: True)
    repo.restore(42)
    assert repo.list_deferred_account_ids() == set()


def test_restore_is_idempotent_restoring_an_already_main_account_is_a_noop():
    repo.restore(999)  # nunca fue diferida -- no debe tirar
    assert repo.list_deferred_account_ids() == set()


def test_restore_does_not_raise_even_if_remote_delete_is_best_effort_and_fails(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: False)
    repo.restore(42)  # best-effort -- no debe tirar aunque el delete remoto falle
    assert repo.list_deferred_account_ids() == set()


# ============================================================================
# V — identidad por id CANÓNICO, nunca nombre
# ============================================================================

def test_v_persistence_key_is_account_id_never_a_name(monkeypatch):
    # El schema del registro guardado no tiene ningún campo de "nombre" de
    # cuenta -- sólo `account_id` + metadata de auditoría (quién/cuándo).
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(555, by="director@danwor.com")
    import json
    saved = json.loads(repo._record_path(555).read_text(encoding="utf-8"))
    assert saved["account_id"] == 555
    assert "name" not in saved and "account_name" not in saved


# ============================================================================
# Múltiples cuentas independientes
# ============================================================================

def test_multiple_accounts_deferred_independently(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(1, by="a@danwor.com")
    repo.defer(2, by="a@danwor.com")
    repo.defer(3, by="a@danwor.com")
    assert repo.list_deferred_account_ids() == {1, 2, 3}
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: True)
    repo.restore(2)
    assert repo.list_deferred_account_ids() == {1, 3}


def test_corrupt_record_is_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(1, by="a@danwor.com")
    corrupt_path = repo._base_dir() / "corrupt.json"
    corrupt_path.write_text("not json at all", encoding="utf-8")
    assert repo.list_deferred_account_ids() == {1}
