"""
test_profile_document_repository.py — Capital Humano Phase 4.1 (Profile
Documents): contrato de `webapp.services.profile_document_repository`, sin
HTTP, sin Graph, sin Spaces real (todo monkeypatcheado) — MISMO patrón de
aislamiento (`tmp_path` vía `config.BASE_DIR`) que
`test_profile_repository.py`."""
from __future__ import annotations

import json

import pytest

import config
from webapp.services import profile_document_repository
from webapp.services.profile_document_repository import (
    ProfileDocumentConfigIncompleteError,
    ProfileDocumentDeleteOutcomeUnknownError,
    ProfileDocumentIdConflictError,
    ProfileDocumentRemoteOutcomeUnknownError,
    ProfileDocumentStorageError,
)


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _push_path_succeeds_by_default(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)


def _doc(**overrides) -> dict:
    base = {
        "profile_id": "profile-1", "provider": "sharepoint", "site_id": "site-1",
        "drive_id": "drive-1", "item_id": "item-1", "web_url": "https://danwor.sharepoint.com/x",
        "filename": "CV.pdf", "content_type": "application/pdf", "size_bytes": 1234,
        "uploaded_at": "2026-09-14T10:00:00+00:00", "uploaded_by": "juan@danwor.com",
    }
    base.update(overrides)
    return base


# --- create_with_explicit_id(): CREATED ------------------------------------

def test_create_with_explicit_id_persists_and_returns_created_true():
    document, created = profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert created is True
    assert document["document_id"] == "doc-1"
    assert document["schema_version"] == 1
    assert document["filename"] == "CV.pdf"


def test_create_with_explicit_id_writes_exactly_one_json_file():
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    files = list((config.BASE_DIR / "human-capital" / "profile-documents").iterdir())
    assert len(files) == 1
    assert files[0].name == "doc-1.json"


def test_create_with_explicit_id_never_stamps_extra_business_timestamps():
    # El repository NO agrega created_at/updated_at propios -- uploaded_at
    # viaja completo desde el caller (ver docstring del módulo).
    document, _ = profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert document["uploaded_at"] == "2026-09-14T10:00:00+00:00"
    assert "created_at" not in document
    assert "updated_at" not in document


# --- get() / list_for_profile() --------------------------------------------

def test_get_returns_none_for_missing_document():
    assert profile_document_repository.get("does-not-exist") is None


def test_get_returns_persisted_document():
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    fetched = profile_document_repository.get("doc-1")
    assert fetched["document_id"] == "doc-1"


def test_list_for_profile_returns_only_matching_profile_documents():
    profile_document_repository.create_with_explicit_id("doc-1", _doc(profile_id="profile-1"))
    profile_document_repository.create_with_explicit_id("doc-2", _doc(profile_id="profile-2"))
    profile_document_repository.create_with_explicit_id("doc-3", _doc(profile_id="profile-1", filename="CV2.pdf"))
    docs = profile_document_repository.list_for_profile("profile-1")
    assert {d["document_id"] for d in docs} == {"doc-1", "doc-3"}


def test_list_for_profile_returns_empty_list_when_directory_missing():
    assert profile_document_repository.list_for_profile("profile-1") == []


def test_list_for_profile_supports_multiple_documents_per_profile():
    profile_document_repository.create_with_explicit_id("doc-1", _doc(filename="CV-es.pdf"))
    profile_document_repository.create_with_explicit_id("doc-2", _doc(filename="CV-en.pdf"))
    docs = profile_document_repository.list_for_profile("profile-1")
    assert len(docs) == 2


def test_list_for_profile_skips_corrupted_files(tmp_path):
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    corrupt = config.BASE_DIR / "human-capital" / "profile-documents" / "corrupt.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    docs = profile_document_repository.list_for_profile("profile-1")
    assert len(docs) == 1


# --- idempotent retry (ALREADY_COMMITTED) ----------------------------------

def test_create_with_explicit_id_same_document_id_same_fields_is_already_committed():
    first, created_first = profile_document_repository.create_with_explicit_id("doc-1", _doc())
    second, created_second = profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert created_first is True
    assert created_second is False
    assert first == second


def test_already_committed_retry_does_not_rewrite_the_file(monkeypatch):
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    write_calls = []
    original_write = profile_document_repository._write_atomic

    def _tracking_write(path, payload):
        write_calls.append(path)
        return original_write(path, payload)

    monkeypatch.setattr(profile_document_repository, "_write_atomic", _tracking_write)
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert write_calls == []


# --- DOCUMENT_ID_CONFLICT ---------------------------------------------------

def test_create_with_explicit_id_conflicting_fields_raises_conflict_error():
    profile_document_repository.create_with_explicit_id("doc-1", _doc(filename="CV.pdf"))
    with pytest.raises(ProfileDocumentIdConflictError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc(filename="OtherCV.pdf"))


def test_conflicting_create_does_not_write_anything(monkeypatch):
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    write_calls = []
    monkeypatch.setattr(profile_document_repository, "_write_atomic", lambda *a, **k: write_calls.append(a))
    with pytest.raises(ProfileDocumentIdConflictError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc(size_bytes=99999))
    assert write_calls == []


def test_conflict_on_differing_uploaded_at_is_a_real_conflict():
    # uploaded_at participa de los campos canónicos a propósito (ver
    # docstring del módulo) -- dos intentos con distinto uploaded_at NO
    # son el mismo intento de commit.
    profile_document_repository.create_with_explicit_id("doc-1", _doc(uploaded_at="2026-09-14T10:00:00+00:00"))
    with pytest.raises(ProfileDocumentIdConflictError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc(uploaded_at="2026-09-14T11:00:00+00:00"))


def test_web_url_difference_alone_does_not_cause_conflict():
    # web_url es "convenience metadata only" (ticket §18) -- afuera de
    # _CANONICAL_FIELDS a propósito.
    profile_document_repository.create_with_explicit_id("doc-1", _doc(web_url="https://a"))
    document, created = profile_document_repository.create_with_explicit_id("doc-1", _doc(web_url="https://b"))
    assert created is False


# --- durability: CONFIG_INCOMPLETE / REMOTE_OUTCOME_UNKNOWN ----------------

def test_create_raises_config_incomplete_when_spaces_disabled(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileDocumentConfigIncompleteError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc())


def test_config_incomplete_cleans_up_local_file(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileDocumentConfigIncompleteError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert profile_document_repository.get("doc-1") is None


def test_create_retries_once_then_raises_remote_outcome_unknown(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return False

    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", _push)
    with pytest.raises(ProfileDocumentRemoteOutcomeUnknownError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert calls["n"] == 2


def test_remote_outcome_unknown_cleans_up_local_file(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileDocumentRemoteOutcomeUnknownError):
        profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert profile_document_repository.get("doc-1") is None


def test_create_confirms_on_the_retry_attempt(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return calls["n"] == 2

    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", _push)
    document, created = profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert created is True
    assert calls["n"] == 2


def test_remote_outcome_unknown_is_not_a_profile_document_storage_error(monkeypatch):
    # Deliberadamente NO subclase de ProfileDocumentStorageError -- un
    # `except ProfileDocumentStorageError` genérico nunca debe atraparlo.
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileDocumentRemoteOutcomeUnknownError):
        try:
            profile_document_repository.create_with_explicit_id("doc-1", _doc())
        except ProfileDocumentStorageError:
            pytest.fail("ProfileDocumentRemoteOutcomeUnknownError should not be caught by ProfileDocumentStorageError")


def test_config_incomplete_is_a_profile_document_storage_error():
    assert issubclass(ProfileDocumentConfigIncompleteError, ProfileDocumentStorageError)


# --- delete() (Fase 4.1.2) ---------------------------------------------------

def test_delete_removes_an_existing_document_and_returns_true():
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert profile_document_repository.delete("doc-1") is True
    assert profile_document_repository.get("doc-1") is None


def test_delete_writes_zero_json_files_left_behind():
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    profile_document_repository.delete("doc-1")
    directory = config.BASE_DIR / "human-capital" / "profile-documents"
    assert list(directory.iterdir()) == []


def test_delete_is_idempotent_second_call_on_same_id_returns_false_never_raises():
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert profile_document_repository.delete("doc-1") is True
    assert profile_document_repository.delete("doc-1") is False  # no-op, NUNCA una excepción


def test_delete_on_a_document_id_that_never_existed_returns_false():
    assert profile_document_repository.delete("never-existed") is False


def test_delete_does_not_disturb_other_profiles_documents():
    profile_document_repository.create_with_explicit_id("doc-1", _doc(profile_id="profile-1"))
    profile_document_repository.create_with_explicit_id("doc-2", _doc(profile_id="profile-2"))
    profile_document_repository.delete("doc-1")
    assert profile_document_repository.get("doc-1") is None
    assert profile_document_repository.get("doc-2") is not None


def test_delete_confirms_remote_before_touching_the_local_file(monkeypatch):
    # Orden: remoto ANTES que local (ver docstring de delete()) -- si el
    # remoto nunca confirma, el archivo local queda INTACTO.
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", lambda path: False)
    with pytest.raises(ProfileDocumentDeleteOutcomeUnknownError):
        profile_document_repository.delete("doc-1")
    # El archivo local sigue ahí -- nunca se borra sin confirmación remota.
    assert profile_document_repository.get("doc-1") is not None


def test_delete_retries_once_then_raises_outcome_unknown(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    calls = {"n": 0}

    def _delete_path(path):
        calls["n"] += 1
        return False

    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", _delete_path)
    with pytest.raises(ProfileDocumentDeleteOutcomeUnknownError):
        profile_document_repository.delete("doc-1")
    assert calls["n"] == 2


def test_delete_confirms_on_the_retry_attempt(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    calls = {"n": 0}

    def _delete_path(path):
        calls["n"] += 1
        return calls["n"] == 2

    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", _delete_path)
    assert profile_document_repository.delete("doc-1") is True
    assert calls["n"] == 2
    assert profile_document_repository.get("doc-1") is None


def test_delete_outcome_unknown_is_not_a_profile_document_storage_error(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", lambda path: False)
    with pytest.raises(ProfileDocumentDeleteOutcomeUnknownError):
        try:
            profile_document_repository.delete("doc-1")
        except ProfileDocumentStorageError:
            pytest.fail("ProfileDocumentDeleteOutcomeUnknownError should not be caught by ProfileDocumentStorageError")


def test_delete_with_spaces_disabled_deletes_local_only_and_returns_true(monkeypatch):
    # A diferencia de create() (donde Spaces deshabilitado bloquea la
    # operación entera con CONFIG_INCOMPLETE), acá el documento ya es
    # local-only -- borrarlo localmente basta, nunca CONFIG_INCOMPLETE.
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)
    assert profile_document_repository.delete("doc-1") is True
    assert profile_document_repository.get("doc-1") is None


def test_delete_on_missing_document_still_attempts_orphan_remote_cleanup(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", lambda path: calls.__setitem__("n", calls["n"] + 1))
    assert profile_document_repository.delete("never-existed") is False
    assert calls["n"] == 1


def test_delete_on_missing_document_never_raises_even_if_orphan_cleanup_fails(monkeypatch):
    def _boom(path):
        raise RuntimeError("network blip")

    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", _boom)
    assert profile_document_repository.delete("never-existed") is False  # nunca propaga la excepción best-effort


# --- nunca persistir downloadUrl (ticket, cobertura explícita) --------------

def test_created_document_never_contains_a_download_url_field():
    document, _ = profile_document_repository.create_with_explicit_id("doc-1", _doc())
    assert "downloadUrl" not in document
    assert "@microsoft.graph.downloadUrl" not in document


def test_get_of_a_persisted_document_never_contains_a_download_url_field():
    profile_document_repository.create_with_explicit_id("doc-1", _doc())
    document = profile_document_repository.get("doc-1")
    assert "downloadUrl" not in document
    assert "@microsoft.graph.downloadUrl" not in document
