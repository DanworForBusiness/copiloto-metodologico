"""
test_profile_document_service.py — Capital Humano Phase 4.1: contrato de
`webapp.services.profile_document_service` — folder-ensure (§10), upload
contract con reintento único (§15), y los dos tokens de reintento firmados
(§16/§17). Sin HTTP real, sin Graph real: `core.microsoft365` se
monkeypatchea función por función (el service llama SIEMPRE vía
`microsoft365.graph_x(...)`, nunca un import directo del símbolo, para que
esto funcione)."""
from __future__ import annotations

import time
import urllib.error
from unittest.mock import patch

import pytest

import config
from webapp.services import profile_document_repository, profile_document_service as svc


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://graph.microsoft.com/v1.0/x", code, "err", None, None)


@pytest.fixture(autouse=True)
def _configured_graph(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "site-1")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", "root-1")


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _push_path_succeeds_by_default(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)


# ============================================================================
# require_graph_config
# ============================================================================

def test_require_graph_config_raises_when_site_id_missing(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "")
    with pytest.raises(svc.ProfileDocumentConfigIncompleteError):
        svc.require_graph_config()


def test_require_graph_config_passes_when_fully_configured():
    svc.require_graph_config()  # no debe levantar


# ============================================================================
# ensure_profile_folder (§10)
# ============================================================================

def test_ensure_profile_folder_returns_existing_folder_without_creating(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: {"id": "folder-existing"})
    called_post = {"n": 0}
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", lambda *a, **k: called_post.__setitem__("n", 1))
    item_id = svc.ensure_profile_folder("profile-1", "tok")
    assert item_id == "folder-existing"
    assert called_post["n"] == 0


def test_ensure_profile_folder_creates_once_when_missing(monkeypatch):
    def _get(url, token):
        raise _http_error(404)

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", lambda url, token, body: {"id": "folder-new"})
    item_id = svc.ensure_profile_folder("profile-1", "tok")
    assert item_id == "folder-new"


def test_ensure_profile_folder_concurrent_conflict_reuses_existing(monkeypatch):
    calls = {"get": 0}

    def _get(url, token):
        calls["get"] += 1
        if calls["get"] == 1:
            raise _http_error(404)
        return {"id": "folder-from-other-request"}

    def _post(url, token, body):
        raise _http_error(409)

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    item_id = svc.ensure_profile_folder("profile-1", "tok")
    assert item_id == "folder-from-other-request"
    assert calls["get"] == 2


def test_ensure_profile_folder_never_creates_a_second_differently_named_folder(monkeypatch):
    create_bodies = []

    def _get(url, token):
        raise _http_error(404)

    def _post(url, token, body):
        create_bodies.append(body)
        return {"id": "folder-new"}

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    svc.ensure_profile_folder("profile-1", "tok")
    assert len(create_bodies) == 1
    assert create_bodies[0]["name"] == "profile-1"
    assert create_bodies[0]["@microsoft.graph.conflictBehavior"] == "fail"


def test_ensure_profile_folder_403_on_lookup_is_permission_denied(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(403)))
    with pytest.raises(svc.ProfileDocumentPermissionDeniedError):
        svc.ensure_profile_folder("profile-1", "tok")


def test_ensure_profile_folder_403_on_create_is_permission_denied(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", lambda *a, **k: (_ for _ in ()).throw(_http_error(403)))
    with pytest.raises(svc.ProfileDocumentPermissionDeniedError):
        svc.ensure_profile_folder("profile-1", "tok")


def test_ensure_profile_folder_ambiguous_lookup_failure_is_graph_source_error(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(500)))
    with pytest.raises(svc.ProfileDocumentGraphSourceError):
        svc.ensure_profile_folder("profile-1", "tok")


def test_ensure_profile_folder_ambiguous_create_failure_is_graph_source_error(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", lambda *a, **k: (_ for _ in ()).throw(_http_error(500)))
    with pytest.raises(svc.ProfileDocumentGraphSourceError):
        svc.ensure_profile_folder("profile-1", "tok")


def test_ensure_profile_folder_conflict_reread_failure_is_graph_source_error(monkeypatch):
    def _get(url, token):
        raise _http_error(404)

    def _post(url, token, body):
        raise _http_error(409)

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    with pytest.raises(svc.ProfileDocumentGraphSourceError):
        svc.ensure_profile_folder("profile-1", "tok")


# ============================================================================
# upload_document_content (§15) — intento + único reintento
# ============================================================================

def test_upload_content_first_attempt_success_no_second_call(monkeypatch):
    calls = {"n": 0}

    def _put(url, token, data, content_type):
        calls["n"] += 1
        return {"id": "drive-item-1", "webUrl": "https://x"}

    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _put)
    result = svc.upload_document_content("https://url", "tok", b"data", "application/pdf")
    assert result["id"] == "drive-item-1"
    assert calls["n"] == 1


def test_upload_content_first_fails_second_succeeds(monkeypatch):
    calls = {"n": 0}

    def _put(url, token, data, content_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return {"id": "drive-item-2"}

    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _put)
    result = svc.upload_document_content("https://url", "tok", b"data", "application/pdf")
    assert result["id"] == "drive-item-2"
    assert calls["n"] == 2


def test_upload_content_both_attempts_fail_raises_unknown(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))
    with pytest.raises(svc.ProfileDocumentRemoteUploadUnknownError):
        svc.upload_document_content("https://url", "tok", b"data", "application/pdf")


def test_upload_content_retry_uses_same_url_and_bytes(monkeypatch):
    seen = []

    def _put(url, token, data, content_type):
        seen.append((url, data))
        if len(seen) == 1:
            raise _http_error(503)
        return {"id": "ok"}

    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _put)
    svc.upload_document_content("https://same-url", "tok", b"same-bytes", "application/pdf")
    assert seen[0] == seen[1] == ("https://same-url", b"same-bytes")


def test_upload_content_403_is_permission_denied_no_retry(monkeypatch):
    calls = {"n": 0}

    def _put(url, token, data, content_type):
        calls["n"] += 1
        raise _http_error(403)

    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _put)
    with pytest.raises(svc.ProfileDocumentPermissionDeniedError):
        svc.upload_document_content("https://url", "tok", b"data", "application/pdf")
    assert calls["n"] == 1


def test_upload_content_url_error_counts_as_unconfirmed(monkeypatch):
    def _put(url, token, data, content_type):
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _put)
    with pytest.raises(svc.ProfileDocumentRemoteUploadUnknownError):
        svc.upload_document_content("https://url", "tok", b"data", "application/pdf")


# ============================================================================
# retry tokens (§16/§17) — sign/verify
# ============================================================================

def _upload_claims(**overrides) -> dict:
    base = dict(
        document_id="doc-1", profile_id="profile-1", folder_item_id="folder-1", filename="doc-1__cv.pdf",
        original_filename="cv.pdf", content_type="application/pdf", size_bytes=10, sha256="abc123",
        initiated_by="juan@danwor.com",
    )
    base.update(overrides)
    return base


def test_upload_retry_token_roundtrip():
    token = svc.sign_upload_retry_token(**_upload_claims())
    claims = svc.verify_upload_retry_token(token)
    assert claims["document_id"] == "doc-1"
    assert claims["profile_id"] == "profile-1"
    assert claims["sha256"] == "abc123"


def test_upload_retry_token_wrong_purpose_is_rejected():
    commit_token = svc.sign_commit_retry_token(
        document_id="doc-1", profile_id="profile-1", provider="sharepoint", site_id="s", drive_id="d",
        item_id="i", web_url=None, filename="f", content_type="application/pdf", size_bytes=1,
        uploaded_at="2026-01-01T00:00:00+00:00", uploaded_by="juan@danwor.com", initiated_by="juan@danwor.com",
    )
    with pytest.raises(svc.RetryTokenError):
        svc.verify_upload_retry_token(commit_token)


def test_upload_retry_token_expired_is_rejected():
    with patch("time.time", return_value=1_000_000):
        token = svc.sign_upload_retry_token(**_upload_claims())
    with patch("time.time", return_value=1_000_000 + svc._RETRY_TOKEN_MAX_AGE_S + 1):
        with pytest.raises(svc.RetryTokenError):
            svc.verify_upload_retry_token(token)


def test_upload_retry_token_garbage_string_is_rejected():
    with pytest.raises(svc.RetryTokenError):
        svc.verify_upload_retry_token("not-a-real-jwt")


def _commit_claims(**overrides) -> dict:
    base = dict(
        document_id="doc-1", profile_id="profile-1", provider="sharepoint", site_id="site-1",
        drive_id="drive-1", item_id="item-1", web_url="https://x", filename="cv.pdf",
        content_type="application/pdf", size_bytes=10, uploaded_at="2026-01-01T00:00:00+00:00",
        uploaded_by="juan@danwor.com", initiated_by="juan@danwor.com",
    )
    base.update(overrides)
    return base


def test_commit_retry_token_roundtrip():
    token = svc.sign_commit_retry_token(**_commit_claims())
    claims = svc.verify_commit_retry_token(token)
    assert claims["document_id"] == "doc-1"
    assert claims["item_id"] == "item-1"


def test_commit_retry_token_wrong_purpose_is_rejected():
    upload_token = svc.sign_upload_retry_token(**_upload_claims())
    with pytest.raises(svc.RetryTokenError):
        svc.verify_commit_retry_token(upload_token)


# ============================================================================
# retry_upload_document (§16) — usuario / SHA / mismo document_id
# ============================================================================

def test_retry_upload_document_wrong_user_is_rejected(monkeypatch):
    token = svc.sign_upload_retry_token(**_upload_claims(sha256=svc.hashlib.sha256(b"%PDF-data").hexdigest()))
    with pytest.raises(svc.RetryTokenError):
        svc.retry_upload_document(
            token=token, data=b"%PDF-data", filename="cv.pdf", access_token="tok",
            requesting_user_sub="someone-else@danwor.com",
        )


def test_retry_upload_document_sha_mismatch_is_rejected():
    token = svc.sign_upload_retry_token(**_upload_claims(sha256="deadbeef"))
    with pytest.raises(svc.ProfileDocumentRetryFileMismatchError):
        svc.retry_upload_document(
            token=token, data=b"%PDF-different-content", filename="cv.pdf", access_token="tok",
            requesting_user_sub="juan@danwor.com",
        )


def test_retry_upload_document_same_document_id_reused(monkeypatch):
    data = b"%PDF-data"
    sha = svc.hashlib.sha256(data).hexdigest()
    token = svc.sign_upload_retry_token(**_upload_claims(sha256=sha))
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", lambda *a, **k: {"id": "item-final", "webUrl": "https://x"})
    document = svc.retry_upload_document(
        token=token, data=data, filename="cv.pdf", access_token="tok", requesting_user_sub="juan@danwor.com",
    )
    assert document["document_id"] == "doc-1"


def test_retry_upload_document_invalid_file_is_rejected():
    data = b"%PDF-data"
    sha = svc.hashlib.sha256(data).hexdigest()
    token = svc.sign_upload_retry_token(**_upload_claims(sha256=sha))
    with pytest.raises(svc.profile_document_validation.ProfileDocumentValidationError):
        svc.retry_upload_document(
            token=token, data=b"not a pdf but same sha would differ", filename="cv.exe", access_token="tok",
            requesting_user_sub="juan@danwor.com",
        )


# ============================================================================
# retry_commit_document (§17) — ZERO Graph PUT
# ============================================================================

def test_retry_commit_document_never_calls_graph_put(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("retry_commit_document must never call graph_put_bytes")

    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _fail_if_called)
    token = svc.sign_commit_retry_token(**_commit_claims())
    document, created = svc.retry_commit_document(token=token, requesting_user_sub="juan@danwor.com")
    assert created is True
    assert document["document_id"] == "doc-1"


def test_retry_commit_document_already_committed_on_second_call():
    token = svc.sign_commit_retry_token(**_commit_claims())
    svc.retry_commit_document(token=token, requesting_user_sub="juan@danwor.com")
    document, created = svc.retry_commit_document(token=token, requesting_user_sub="juan@danwor.com")
    assert created is False
    assert document["document_id"] == "doc-1"


def test_retry_commit_document_wrong_user_is_rejected():
    token = svc.sign_commit_retry_token(**_commit_claims())
    with pytest.raises(svc.RetryTokenError):
        svc.retry_commit_document(token=token, requesting_user_sub="someone-else@danwor.com")


def test_retry_commit_document_conflicting_id_raises_conflict():
    svc.retry_commit_document(token=svc.sign_commit_retry_token(**_commit_claims()), requesting_user_sub="juan@danwor.com")
    conflicting_token = svc.sign_commit_retry_token(**_commit_claims(item_id="a-different-item"))
    with pytest.raises(profile_document_repository.ProfileDocumentIdConflictError):
        svc.retry_commit_document(token=conflicting_token, requesting_user_sub="juan@danwor.com")


def test_retry_commit_document_still_unconfirmed_reissues_commit_retry_token(monkeypatch):
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    token = svc.sign_commit_retry_token(**_commit_claims())
    with pytest.raises(svc.ProfileDocumentCommitRetryRequired) as exc_info:
        svc.retry_commit_document(token=token, requesting_user_sub="juan@danwor.com")
    assert exc_info.value.retry_token


# ============================================================================
# upload_document (§6-§15) — orquestación end-to-end con Graph fakeado
# ============================================================================

def test_upload_document_end_to_end_success(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", lambda url, token, body: {"id": "folder-1"})
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", lambda *a, **k: {"id": "item-1", "webUrl": "https://x"})
    document = svc.upload_document(
        profile_id="profile-1", filename="cv.pdf", data=b"%PDF-data", access_token="tok",
        uploaded_by="juan@danwor.com",
    )
    assert document["profile_id"] == "profile-1"
    assert document["item_id"] == "item-1"
    assert document["filename"] == "cv.pdf"


def test_upload_document_invalid_file_never_calls_graph(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("must not call Graph before validation passes")

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _fail_if_called)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _fail_if_called)
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", _fail_if_called)
    with pytest.raises(svc.profile_document_validation.ProfileDocumentValidationError):
        svc.upload_document(
            profile_id="profile-1", filename="cv.txt", data=b"plain text", access_token="tok",
            uploaded_by="juan@danwor.com",
        )


def test_upload_document_missing_config_never_calls_graph(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "")

    def _fail_if_called(*a, **k):
        raise AssertionError("must not call Graph when config is incomplete")

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _fail_if_called)
    with pytest.raises(svc.ProfileDocumentConfigIncompleteError):
        svc.upload_document(
            profile_id="profile-1", filename="cv.pdf", data=b"%PDF-data", access_token="tok",
            uploaded_by="juan@danwor.com",
        )


def test_upload_document_unconfirmed_upload_raises_retry_with_token(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: {"id": "folder-1"})
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))
    with pytest.raises(svc.ProfileDocumentUploadRetryRequired) as exc_info:
        svc.upload_document(
            profile_id="profile-1", filename="cv.pdf", data=b"%PDF-data", access_token="tok",
            uploaded_by="juan@danwor.com",
        )
    claims = svc.verify_upload_retry_token(exc_info.value.retry_token)
    assert claims["profile_id"] == "profile-1"
    assert claims["original_filename"] == "cv.pdf"


def test_upload_document_graph_confirmed_but_persistence_unconfirmed_raises_commit_retry(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: {"id": "folder-1"})
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", lambda *a, **k: {"id": "item-1", "webUrl": "https://x"})
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(svc.ProfileDocumentCommitRetryRequired) as exc_info:
        svc.upload_document(
            profile_id="profile-1", filename="cv.pdf", data=b"%PDF-data", access_token="tok",
            uploaded_by="juan@danwor.com",
        )
    claims = svc.verify_commit_retry_token(exc_info.value.retry_token)
    assert claims["item_id"] == "item-1"


def test_build_remote_filename_is_deterministic_for_same_inputs():
    a = svc.build_remote_filename("doc-1", "My CV (final).pdf")
    b = svc.build_remote_filename("doc-1", "My CV (final).pdf")
    assert a == b
    assert a.startswith("doc-1__")


def test_list_documents_for_profile_delegates_to_repository(monkeypatch):
    monkeypatch.setattr(profile_document_repository, "list_for_profile", lambda profile_id: [{"document_id": "x", "profile_id": profile_id}])
    docs = svc.list_documents_for_profile("profile-1")
    assert docs == [{"document_id": "x", "profile_id": "profile-1"}]
