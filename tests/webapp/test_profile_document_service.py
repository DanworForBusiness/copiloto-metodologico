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
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")


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
# ensure_profile_folder — Fase 4.1.1 corrective (§8-§11): ambigüedad de
# TRANSPORTE (URLError, sin respuesta HTTP) en el POST de creación NO
# prueba que la carpeta no se haya creado — requiere un único reintento
# antes de rendirse, distinto de un 4xx/5xx CONFIRMADO (que nunca reintenta).
# ============================================================================

def test_ensure_profile_folder_transport_failure_on_create_retries_same_payload(monkeypatch):
    get_calls = {"n": 0}
    post_payloads = []

    def _get(url, token):
        get_calls["n"] += 1
        raise _http_error(404)

    def _post(url, token, body):
        post_payloads.append((url, token, body))
        if len(post_payloads) == 1:
            raise urllib.error.URLError("connection reset")
        return {"id": "folder-after-retry"}

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    item_id = svc.ensure_profile_folder("profile-1", "tok")
    assert item_id == "folder-after-retry"
    assert len(post_payloads) == 2
    assert post_payloads[0] == post_payloads[1]  # MISMO parent/name/payload en ambos intentos


def test_ensure_profile_folder_first_transport_fail_then_201_succeeds(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    calls = {"n": 0}

    def _post(url, token, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("timeout")
        return {"id": "folder-ok"}

    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    item_id = svc.ensure_profile_folder("profile-1", "tok")
    assert item_id == "folder-ok"
    assert calls["n"] == 2


def test_ensure_profile_folder_first_transport_fail_then_409_reread_found(monkeypatch):
    get_calls = {"n": 0}

    def _get(url, token):
        get_calls["n"] += 1
        if get_calls["n"] == 1:
            raise _http_error(404)  # chequeo inicial: ausente
        return {"id": "folder-from-conflict"}  # re-read tras el 409 del reintento

    calls = {"n": 0}

    def _post(url, token, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("timeout")
        raise _http_error(409)

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    item_id = svc.ensure_profile_folder("profile-1", "tok")
    assert item_id == "folder-from-conflict"
    assert calls["n"] == 2


def test_ensure_profile_folder_double_transport_ambiguity_is_outcome_unknown(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("timeout")))
    with pytest.raises(svc.ProfileDocumentFolderOutcomeUnknownError):
        svc.ensure_profile_folder("profile-1", "tok")


def test_ensure_profile_folder_403_on_create_never_retries(monkeypatch):
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    calls = {"n": 0}

    def _post(url, token, body):
        calls["n"] += 1
        raise _http_error(403)

    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    with pytest.raises(svc.ProfileDocumentPermissionDeniedError):
        svc.ensure_profile_folder("profile-1", "tok")
    assert calls["n"] == 1


def test_ensure_profile_folder_confirmed_4xx_never_retries(monkeypatch):
    # Un 4xx/5xx CONFIRMADO (código real, no 403/409) es un rechazo
    # definitivo -- reintentarlo solo amplificaría un error real.
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    calls = {"n": 0}

    def _post(url, token, body):
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr(svc.microsoft365, "graph_post_json", _post)
    with pytest.raises(svc.ProfileDocumentGraphSourceError):
        svc.ensure_profile_folder("profile-1", "tok")
    assert calls["n"] == 1


def test_ensure_profile_folder_transport_failure_on_initial_lookup_is_source_error_no_retry(monkeypatch):
    # El chequeo inicial es de solo LECTURA -- nada se intentó escribir
    # todavía, así que un fallo de transporte ahí es un SOURCE_ERROR
    # honesto (nunca ambiguo sobre "¿se creó algo?").
    calls = {"n": 0}

    def _get(url, token):
        calls["n"] += 1
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(svc.microsoft365, "graph_get_json", _get)
    with pytest.raises(svc.ProfileDocumentGraphSourceError):
        svc.ensure_profile_folder("profile-1", "tok")
    assert calls["n"] == 1


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


# ============================================================================
# filename sanitization (§8) — cobertura dedicada, ticket delta explícito
# ============================================================================

def test_sanitize_filename_keeps_alphanumerics_dots_dashes_underscores():
    assert svc._sanitize_filename("CV_final-v2.pdf") == "CV_final-v2.pdf"


def test_sanitize_filename_replaces_spaces_and_special_chars_with_dash():
    assert svc._sanitize_filename("My CV (final)!.pdf") == "My-CV-final-.pdf"


def test_sanitize_filename_collapses_repeated_dashes():
    assert svc._sanitize_filename("a   b***c.pdf") == "a-b-c.pdf"


def test_sanitize_filename_strips_leading_and_trailing_dashes():
    assert svc._sanitize_filename("***weird***.pdf").startswith("weird")
    assert not svc._sanitize_filename("***weird***.pdf").startswith("-")
    assert not svc._sanitize_filename("***weird***.pdf").endswith("-")


def test_sanitize_filename_handles_unicode_by_replacing_it():
    result = svc._sanitize_filename("currículum-vitae.pdf")
    assert "í" not in result
    assert result.endswith(".pdf")


def test_sanitize_filename_neutralizes_path_traversal_attempts():
    # "/" y ".." no son alfanuméricos/./-/_ -- se reemplazan por "-", nunca
    # sobreviven como separador de path en el nombre remoto resultante.
    result = svc._sanitize_filename("../../etc/passwd")
    assert "/" not in result
    assert "\\" not in result


def test_sanitize_filename_truncates_very_long_names():
    result = svc._sanitize_filename("a" * 500 + ".pdf")
    assert len(result) <= 150


def test_sanitize_filename_empty_after_stripping_falls_back_to_documento():
    assert svc._sanitize_filename("***") == "documento"


def test_build_remote_filename_never_uses_the_original_name_alone():
    # Dos uploads del mismo nombre de archivo nunca colisionan de path en
    # la misma carpeta (§8) -- el document_id SIEMPRE prefija.
    a = svc.build_remote_filename("doc-1", "CV.pdf")
    b = svc.build_remote_filename("doc-2", "CV.pdf")
    assert a != b
    assert a.startswith("doc-1__")
    assert b.startswith("doc-2__")


# ============================================================================
# boundary estructural: toda escritura ancla a HC_CV_ROOT_ITEM_ID / a la
# identidad canónica del documento — nunca un root arbitrario (ticket:
# "en unit tests sólo fijar que el código no permite elegir/arbitrar otro
# root"; la prueba negativa REAL contra Graph queda para el gate live).
# ============================================================================

def test_folder_url_is_always_anchored_under_the_configured_cv_root():
    url = svc._folder_by_path_url("profile-1")
    assert url.endswith(f"/items/{config.HC_CV_ROOT_ITEM_ID}:/profile-1")
    assert config.HC_SHAREPOINT_SITE_ID in url
    assert config.HC_SHAREPOINT_DRIVE_ID in url


def test_cv_root_children_url_is_always_anchored_under_the_configured_cv_root():
    url = svc._cv_root_children_url()
    assert url.endswith(f"/items/{config.HC_CV_ROOT_ITEM_ID}/children")


def test_ensure_profile_folder_never_accepts_a_caller_supplied_root_or_parent_id():
    # `ensure_profile_folder` sólo recibe `profile_id`/`access_token` -- no
    # existe ningún parámetro por el que un caller pueda pasar un root/
    # parent_id propio; el ÚNICO root posible es `config.HC_CV_ROOT_ITEM_ID`.
    import inspect

    params = list(inspect.signature(svc.ensure_profile_folder).parameters)
    assert params == ["profile_id", "access_token", "first_name", "last_name"]            # sólo identidad del candidato + nombres para el rótulo; ningún root/parent/site/drive
    assert not [p for p in params if "root" in p or "parent" in p or "site" in p or "drive" in p]


def test_delete_document_signature_never_accepts_a_caller_supplied_site_or_drive_id():
    # MISMO criterio: `delete_document` sólo recibe profile_id/document_id/
    # access_token -- site_id/drive_id/item_id SIEMPRE salen de la metadata
    # ya persistida de ESE documento, nunca de un parámetro que el router
    # (y por lo tanto un request) pudiera controlar.
    import inspect

    params = list(inspect.signature(svc.delete_document).parameters)
    assert params == ["profile_id", "document_id", "access_token"]


def test_delete_physical_document_url_is_built_purely_from_its_arguments():
    # `_delete_physical_document` nunca lee `config.HC_*` -- la identidad
    # física de un documento YA COMMITTED es la que quedó fijada en su
    # propia metadata, aunque la config vigente del Hub haya rotado desde
    # entonces (ver comentario de sección en profile_document_service.py).
    url = svc._document_item_url("site-X", "drive-Y", "item-Z")
    assert url == "https://graph.microsoft.com/v1.0/sites/site-X/drives/drive-Y/items/item-Z"


# ============================================================================
# delete_document (Fase 4.1.2) — orden estricto físico→metadata, 404=éxito,
# 401/403=metadata intacta, ambigüedad=metadata intacta, idempotencia.
# ============================================================================

def _seed_document(document_id: str = "doc-1", profile_id: str = "profile-1", **overrides) -> dict:
    fields = {
        "profile_id": profile_id, "provider": "sharepoint", "site_id": "site-1",
        "drive_id": "drive-1", "item_id": "item-1", "web_url": "https://danwor.sharepoint.com/x",
        "filename": "CV.pdf", "content_type": "application/pdf", "size_bytes": 1234,
        "uploaded_at": "2026-09-14T10:00:00+00:00", "uploaded_by": "juan@danwor.com",
    }
    fields.update(overrides)
    document, _created = profile_document_repository.create_with_explicit_id(document_id, fields)
    return document


def test_delete_document_not_found_raises_and_never_calls_graph(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(svc.ProfileDocumentNotFoundError):
        svc.delete_document(profile_id="profile-1", document_id="never-existed", access_token="tok")
    assert called["n"] == 0


def test_delete_document_belonging_to_another_profile_is_not_found(monkeypatch):
    # Least-privilege (§ router): un document_id real de OTRO profile_id se
    # ve exactamente igual que uno inexistente -- nunca revela que existe.
    _seed_document(document_id="doc-1", profile_id="profile-OTHER")
    called = {"n": 0}
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(svc.ProfileDocumentNotFoundError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert called["n"] == 0


def test_delete_document_missing_config_never_calls_graph(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "")
    _seed_document()
    called = {"n": 0}
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(svc.ProfileDocumentConfigIncompleteError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert called["n"] == 0


def test_delete_document_uses_the_documents_own_site_drive_item_id_never_current_config(monkeypatch):
    # El documento fue creado bajo site/drive distintos de la config VIGENTE
    # (simula una rotación de config desde el momento del commit) -- el
    # DELETE físico tiene que usar SIEMPRE la identidad de la metadata.
    _seed_document(site_id="site-OLD", drive_id="drive-OLD", item_id="item-OLD")
    seen = {}

    def _delete(url, token):
        seen["url"] = url

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert "site-OLD" in seen["url"]
    assert "drive-OLD" in seen["url"]
    assert "item-OLD" in seen["url"]
    assert config.HC_SHAREPOINT_SITE_ID not in seen["url"]


def test_delete_document_204_deletes_the_physical_item_then_the_metadata(monkeypatch):
    _seed_document()
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda url, token: None)
    svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is None


def test_delete_document_404_on_physical_is_treated_as_success_and_still_deletes_metadata(monkeypatch):
    # "Graph 404 = físico ya ausente -> continuar cleanup de metadata."
    _seed_document()

    def _delete(url, token):
        raise _http_error(404)

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is None


def test_delete_document_401_on_physical_raises_permission_denied_and_preserves_metadata(monkeypatch):
    _seed_document()

    def _delete(url, token):
        raise _http_error(401)

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    with pytest.raises(svc.ProfileDocumentDeletePermissionDeniedError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is not None


def test_delete_document_403_on_physical_raises_permission_denied_and_preserves_metadata(monkeypatch):
    _seed_document()

    def _delete(url, token):
        raise _http_error(403)

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    with pytest.raises(svc.ProfileDocumentDeletePermissionDeniedError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is not None


def test_delete_document_confirmed_5xx_on_physical_raises_graph_source_error_and_preserves_metadata(monkeypatch):
    _seed_document()

    def _delete(url, token):
        raise _http_error(500)

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    with pytest.raises(svc.ProfileDocumentDeleteGraphSourceError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is not None


def test_delete_document_confirmed_409_on_physical_raises_graph_source_error_and_preserves_metadata(monkeypatch):
    _seed_document()

    def _delete(url, token):
        raise _http_error(409)

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    with pytest.raises(svc.ProfileDocumentDeleteGraphSourceError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is not None


def test_delete_document_transport_failure_on_physical_preserves_metadata_and_is_retryable(monkeypatch):
    # "transporte/5xx ambiguo = preservar metadata y permitir retry."
    _seed_document()

    def _delete(url, token):
        raise urllib.error.URLError("network blip")

    monkeypatch.setattr(svc.microsoft365, "graph_delete", _delete)
    with pytest.raises(svc.ProfileDocumentDeletePhysicalOutcomeUnknownError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is not None
    # Retry idempotente: la segunda llamada, con Graph ahora confirmando
    # 404 (ya ausente -- coherente con "no sabemos si el primer intento
    # había llegado a borrarlo"), termina limpiando la metadata igual.
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda url, token: (_ for _ in ()).throw(_http_error(404)))
    svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is None


def test_delete_document_metadata_outcome_unknown_when_repository_delete_is_ambiguous(monkeypatch):
    _seed_document()
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda url, token: None)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "delete_path", lambda path: False)
    with pytest.raises(svc.ProfileDocumentMetadataDeleteOutcomeUnknownError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    # El físico YA se confirmó ausente -- la metadata podría seguir
    # LISTADA (borrado remoto sin confirmar), nunca perdida silenciosamente.
    assert profile_document_repository.get("doc-1") is not None


def test_delete_document_is_idempotent_calling_it_twice_both_succeed(monkeypatch):
    _seed_document()
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda url, token: None)
    svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is None
    # Segunda vez: ya no hay metadata (ProfileDocumentNotFoundError es el
    # comportamiento correcto y esperado -- "ya no existe", nunca un error
    # de servidor) -- el router lo traduce a 404 real, no a un
    # document_state renderizado (ver test de rutas).
    with pytest.raises(svc.ProfileDocumentNotFoundError):
        svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")


def test_delete_document_never_deletes_a_different_documents_metadata(monkeypatch):
    _seed_document(document_id="doc-1", item_id="item-1")
    _seed_document(document_id="doc-2", item_id="item-2")
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda url, token: None)
    svc.delete_document(profile_id="profile-1", document_id="doc-1", access_token="tok")
    assert profile_document_repository.get("doc-1") is None
    assert profile_document_repository.get("doc-2") is not None
