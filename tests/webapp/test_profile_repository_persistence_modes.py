"""Modos de persistencia (`DANWOR_PERSISTENCE`) en `profile_repository`: alta, edición y baja lógica.

  local      escritura física local atómica; NO requiere ni intenta Spaces (aunque haya credenciales).
  remote     escritura local + confirmación remota obligatoria (compensación y errores intactos).
  read-only  ninguna escritura de negocio (ni siquiera local): rechazo ANTES de tocar el disco.
Sin red ni Spaces reales: `remote_store` se reemplaza por dobles."""
from __future__ import annotations

import json

import pytest

import config
from webapp.services import profile_repository as repo


class Remote:
    def __init__(self, push_ok=True):
        self.push_ok, self.pushes, self.deletes = push_ok, 0, 0

    def push_path(self, path):
        self.pushes += 1
        return self.push_ok

    def delete_path(self, path):
        self.deletes += 1
        return True

    def enabled(self):
        return True


@pytest.fixture()
def remote(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    r = Remote()
    monkeypatch.setattr(repo.remote_store, "push_path", r.push_path)
    monkeypatch.setattr(repo.remote_store, "delete_path", r.delete_path)
    monkeypatch.setattr(repo.remote_store, "enabled", r.enabled)
    return r


def _mode(monkeypatch, mode):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", mode)


def _profile(**o):
    return {"first_name": "Ana", "last_name": "Pérez", "email": None, **o}


def _files():
    d = config.BASE_DIR / "human-capital" / "profiles"
    return sorted(d.glob("*.json")) if d.exists() else []


# ── local ──────────────────────────────────────────────────────────────────────────────
def test_local_create_update_and_soft_delete_persist_on_disk_without_touching_spaces(remote, monkeypatch):
    _mode(monkeypatch, "local")
    p = repo.create(_profile())
    assert [f.stem for f in _files()] == [p["profile_id"]]
    u = repo.update(p["profile_id"], 1, _profile(first_name="Bruna"))
    assert u["revision"] == 2 and repo.get(p["profile_id"])["first_name"] == "Bruna"
    d = repo.soft_delete(p["profile_id"], "owner@danwor.com")
    assert d["deleted_at"] and d["deleted_by"] == "owner@danwor.com" and d["revision"] == 3
    on_disk = json.loads(_files()[0].read_text(encoding="utf-8"))
    assert on_disk["deleted_at"] and on_disk["first_name"] == "Bruna"            # baja LÓGICA: el registro sigue en disco
    assert (remote.pushes, remote.deletes) == (0, 0)                              # jamás una escritura/borrado remoto desde `local`


def test_local_never_pushes_even_when_spaces_looks_enabled_and_a_failing_push_cannot_roll_anything_back(remote, monkeypatch):
    _mode(monkeypatch, "local")
    remote.push_ok = False                                                        # si se intentara el push, el write se revertiría (el bug original)
    p = repo.create(_profile())
    repo.soft_delete(p["profile_id"], "x")
    assert repo.get(p["profile_id"])["deleted_at"] and remote.pushes == 0


def test_local_soft_delete_is_idempotent_and_preserves_the_candidate_documents(remote, monkeypatch, tmp_path):
    _mode(monkeypatch, "local")
    p = repo.create(_profile())
    docs = tmp_path / "human-capital" / "profile-documents"
    docs.mkdir(parents=True)
    doc = docs / "doc-1.json"
    doc.write_text(json.dumps({"document_id": "doc-1", "profile_id": p["profile_id"]}), encoding="utf-8")
    first = repo.soft_delete(p["profile_id"], "a")
    again = repo.soft_delete(p["profile_id"], "b")
    assert again == first and again["revision"] == 2                              # sin re-escritura ni bump
    assert json.loads(doc.read_text(encoding="utf-8"))["profile_id"] == p["profile_id"]     # documentos intactos


def test_local_still_enforces_optimistic_concurrency_and_not_found(remote, monkeypatch):
    _mode(monkeypatch, "local")
    p = repo.create(_profile())
    repo.update(p["profile_id"], 1, _profile(first_name="B"))
    with pytest.raises(repo.ProfileConcurrentModificationError):
        repo.update(p["profile_id"], 1, _profile(first_name="C"))
    with pytest.raises(repo.ProfileNotFoundError):
        repo.soft_delete("no-existe", "x")


# ── remote: contrato intacto ──────────────────────────────────────────────────────────────────────────────────────
def test_remote_requires_remote_confirmation_on_create_update_and_soft_delete(remote, monkeypatch):
    _mode(monkeypatch, "remote")
    p = repo.create(_profile())
    repo.update(p["profile_id"], 1, _profile(first_name="B"))
    repo.soft_delete(p["profile_id"], "x")
    assert remote.pushes == 3


def test_remote_failed_push_still_compensates_and_the_soft_delete_does_not_stick(remote, monkeypatch):
    _mode(monkeypatch, "remote")
    p = repo.create(_profile())
    remote.push_ok = False
    with pytest.raises((repo.ProfileStorageError, repo.ProfileRemoteOutcomeUnknownError)):
        repo.soft_delete(p["profile_id"], "x")
    assert not repo.get(p["profile_id"]).get("deleted_at") and repo.get(p["profile_id"])["revision"] == 1


def test_remote_without_credentials_stays_config_incomplete_and_restores_the_previous_version(remote, monkeypatch):
    _mode(monkeypatch, "remote")
    p = repo.create(_profile())
    remote.push_ok = False
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)             # remote sin credenciales: NUNCA cae a escritura solo local
    with pytest.raises(repo.ProfileConfigIncompleteError):
        repo.update(p["profile_id"], 1, _profile(first_name="B"))
    with pytest.raises(repo.ProfileConfigIncompleteError):
        repo.create(_profile(first_name="Nueva"))
    assert [repo.get(p["profile_id"])["first_name"]] == ["Ana"] and len(_files()) == 1


# ── read-only ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_read_only_rejects_business_writes_before_touching_disk_but_still_reads(remote, monkeypatch):
    _mode(monkeypatch, "local")
    p = repo.create(_profile())
    _mode(monkeypatch, "read-only")
    before = _files()[0].read_text(encoding="utf-8")
    with pytest.raises(repo.ProfileConfigIncompleteError):
        repo.create(_profile(first_name="Otra"))
    with pytest.raises(repo.ProfileConfigIncompleteError):
        repo.update(p["profile_id"], 1, _profile(first_name="B"))
    with pytest.raises(repo.ProfileConfigIncompleteError):
        repo.soft_delete(p["profile_id"], "x")
    assert len(_files()) == 1 and _files()[0].read_text(encoding="utf-8") == before
    assert repo.get(p["profile_id"])["first_name"] == "Ana" and len(repo.list()) == 1
    assert remote.pushes == 0 and not list((config.BASE_DIR / "human-capital" / "profiles").glob(".tmp-*"))


def test_an_already_deleted_profile_stays_a_noop_even_in_read_only(remote, monkeypatch):
    _mode(monkeypatch, "local")
    p = repo.create(_profile())
    deleted = repo.soft_delete(p["profile_id"], "x")
    _mode(monkeypatch, "read-only")
    assert repo.soft_delete(p["profile_id"], "y") == deleted


def test_the_default_mode_is_local():
    assert config.parse_persistence_mode(None) == "local" and config.parse_persistence_mode("") == "local"
