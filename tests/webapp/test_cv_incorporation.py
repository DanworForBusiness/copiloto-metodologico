"""Motor común de incorporación (`cv_incorporation`) y área de la bandeja en el manifiesto: la tabla de errores conserva el orden del importador histórico y la bandeja no se mezcla con los lotes de Históricos."""
from __future__ import annotations

import urllib.error

from tests.webapp.test_cv_history_inventory import http_error
from tests.webapp.test_cv_history_pilot import home  # noqa: F401  (fixture)
from webapp.services import (cv_history_import_exec as ex, cv_history_manifest as mf, cv_incorporation as core, profile_document_repository, profile_document_service as docsvc, profile_document_validation,
                             profile_repository)


def test_classify_keeps_the_original_except_order():
    cases = [
        (docsvc.ProfileDocumentPermissionDeniedError("x"), (core.STOP, "PERMISSION_DENIED")),
        (profile_repository.ProfileConfigIncompleteError("x"), (core.STOP, "CONFIG_INCOMPLETE")),          # también es ProfileStorageError (reintentable): la configuración gana
        (profile_document_repository.ProfileDocumentConfigIncompleteError("x"), (core.STOP, "CONFIG_INCOMPLETE")),
        (mf.ManifestDurabilityError("x"), (core.STOP, "PERSISTENCE_ERROR")),
        (profile_document_validation.ProfileDocumentValidationError("x"), (core.FAILED, "INVALID_DOCUMENT")),
        (profile_repository.ProfileIdConflictError("x"), (core.BLOCKED, "PROFILE_ID_CONFLICT")),
        (profile_document_repository.ProfileDocumentIdConflictError("x"), (core.BLOCKED, "DOC_ID_CONFLICT")),
        (ValueError("x"), (core.FAILED, "INVALID_PROFILE_FIELDS")),
        (urllib.error.URLError("x"), (core.RETRY, "URLError")),
        (profile_repository.ProfileStorageError("x"), (core.RETRY, "ProfileStorageError")),
        (KeyError("x"), None),
    ]
    for exc, expected in cases:
        assert core.classify(exc) == expected, exc
    wrapped = docsvc.ProfileDocumentGraphSourceError("x")
    wrapped.__cause__ = http_error(401)
    assert core.classify(wrapped) == (core.TOKEN, "TOKEN_EXPIRED")


def test_historical_importer_uses_the_common_engine():
    assert ex._verify_remote is core.verify_remote and ex._is_401 is core.is_401 and ex._RETRYABLE is core.RETRYABLE


def test_inbox_area_is_separate_from_historical_runs(home):
    mf.put_group(mf.INBOX, "a" * 16, {"state": "ANALYZED"})
    mf.put_ledger(mf.INBOX, "a" * 16, {"state": "DONE"})
    mf.append_audit(mf.INBOX, "a" * 16, "ANALYZED")
    assert (home / "human-capital" / "cv-inbox" / "staging" / ("a" * 16 + ".json")).is_file()
    assert not (home / "human-capital" / "import-history").exists()
    assert mf.list_runs() == []
    assert [g["content_key"] for g in mf.list_groups(mf.INBOX)] == ["a" * 16] and set(mf.list_ledgers(mf.INBOX)) == {"a" * 16}
