"""
test_human_capital_profile_documents_routes.py — Capital Humano Phase 4.1
(Profile Documents): contrato HTTP de
`POST /cchh/candidatos/{profile_id}/documentos` +
`.../documentos/retry-upload` + `.../documentos/retry-commit`. MISMO patrón
de autorización/aislamiento (fixture `autouse` con `monkeypatch`) que
`test_human_capital_profile_edit_routes.py`. Graph se monkeypatchea al
nivel de `core.microsoft365` vía `webapp.services.profile_document_service`
— nunca HTTP real."""
from __future__ import annotations

import hashlib
import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, profile_document_repository, profile_document_service, profile_repository,
)

_AUTHORIZED_EMAIL = "human-capital-docs-test@danwor.com"
_PDF_BYTES = b"%PDF-1.4\n%test content\n"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://graph.microsoft.com/v1.0/x", code, "err", None, None)

_FOLDER_NAMES: dict = {}


def _graph_get_folder(url, token, **kw):
    """Graph realista para el lookup de carpeta del uploader: por PATH devuelve una carpeta bajo la raíz (`root-1`) con el nombre pedido; por ID devuelve la misma carpeta (nombre recordado)."""
    tail = url.split("/items/", 1)[1] if "/items/" in url else ""
    if ":/" in tail:
        name = urllib.parse.unquote(tail.split(":/", 1)[1].split("?")[0])
        _FOLDER_NAMES["folder-1"] = name
    else:
        name = _FOLDER_NAMES.get("folder-1", "")
    return {"id": "folder-1", "name": name, "folder": {}, "parentReference": {"id": "root-1"}}



@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")     # estos tests simulan fallos/confirmaciones de Spaces: ejercitan el contrato REMOTE (el modo es explícito)
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "site-1")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", "root-1")
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)


@pytest.fixture(autouse=True)
def _graph_happy_path(monkeypatch):
    # Default determinístico: carpeta ya existe, PUT confirma al primer
    # intento -- los tests que necesitan un camino distinto lo
    # sobreescriben en su propio cuerpo.
    monkeypatch.setattr(profile_document_service.microsoft365, "graph_get_json", _graph_get_folder)
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: {"id": "item-1", "webUrl": "https://danwor.sharepoint.com/x"},
    )


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Docs Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _create_profile(client: TestClient, **overrides) -> dict:
    data = {"first_name": "Ana", "last_name": "Lopez", **overrides}
    client.post("/cchh/candidatos/nuevo", data=data)
    matches = [p for p in profile_repository.list() if p["first_name"] == data["first_name"]]
    return matches[0]


def _upload(client: TestClient, profile_id: str, *, graph_token="tok", filename="cv.pdf", data=_PDF_BYTES):
    form = {}
    if graph_token is not None:
        form["graph_token"] = graph_token
    files = {"file": (filename, data, "application/pdf")} if filename else None
    return client.post(
        f"/cchh/candidatos/{profile_id}/documentos", data=form, files=files, follow_redirects=False,
    )


# ============================================================================
# Authorization
# ============================================================================

def test_upload_anonymous_redirects_to_login():
    profile = _create_profile(_client_as(_AUTHORIZED_EMAIL))
    anon = TestClient(app)
    response = _upload(anon, profile["profile_id"])
    assert response.status_code in (302, 303)


def test_upload_unlisted_user_is_denied_with_403():
    profile = _create_profile(_client_as(_AUTHORIZED_EMAIL))
    response = _upload(_client_as("unlisted@danwor.com"), profile["profile_id"])
    assert response.status_code == 403


def test_upload_missing_profile_is_404():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _upload(client, "does-not-exist")
    assert response.status_code == 404


def test_retry_upload_unlisted_user_is_denied_with_403():
    profile = _create_profile(_client_as(_AUTHORIZED_EMAIL))
    response = _client_as("unlisted@danwor.com").post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-upload",
        data={"token": "x", "graph_token": "tok"}, files={"file": ("cv.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 403


def test_retry_commit_unlisted_user_is_denied_with_403():
    profile = _create_profile(_client_as(_AUTHORIZED_EMAIL))
    response = _client_as("unlisted@danwor.com").post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-commit", data={"token": "x"},
    )
    assert response.status_code == 403


# ============================================================================
# Upload — camino feliz
# ============================================================================

def test_upload_success_redirects_to_edit_page():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"], filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code == 303
    assert response.headers["location"] == f"/cchh/candidatos/{profile['profile_id']}/editar"


def test_uploaded_document_is_persisted_for_the_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    docs = profile_document_repository.list_for_profile(profile["profile_id"])
    assert len(docs) == 1
    assert docs[0]["filename"] == "cv.pdf"
    assert docs[0]["uploaded_by"] == _AUTHORIZED_EMAIL


def test_multiple_documents_can_be_uploaded_for_the_same_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"], filename="cv-es.pdf")
    _upload(client, profile["profile_id"], filename="cv-en.pdf")
    docs = profile_document_repository.list_for_profile(profile["profile_id"])
    assert {d["filename"] for d in docs} == {"cv-es.pdf", "cv-en.pdf"}


# ============================================================================
# Upload — fronteras de error (VALIDATION_ERROR / CONFIG_INCOMPLETE / GRAPH_*)
# ============================================================================

def test_upload_missing_graph_token_shows_permission_denied_state():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"], graph_token=None)
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text


def test_upload_missing_file_shows_validation_error():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = client.post(f"/cchh/candidatos/{profile['profile_id']}/documentos", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert 'data-document-error-kind="VALIDATION_ERROR"' in response.text


def test_upload_invalid_file_format_shows_validation_error():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"], filename="cv.txt", data=b"plain text")
    assert response.status_code == 200
    assert 'data-document-error-kind="VALIDATION_ERROR"' in response.text
    assert profile_document_repository.list_for_profile(profile["profile_id"]) == []


def test_upload_config_incomplete_shows_config_incomplete_state(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "")
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"])
    assert response.status_code == 200
    assert 'data-error-kind="config_incomplete"' in response.text


def test_upload_graph_403_shows_permission_denied_state(monkeypatch):
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_get_json",
        lambda url, token: (_ for _ in ()).throw(_http_error(403)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"])
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text


def test_upload_graph_source_error_shows_no_disponible(monkeypatch):
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_get_json",
        lambda url, token: (_ for _ in ()).throw(_http_error(500)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"])
    assert response.status_code == 200
    assert 'data-error-kind="source_error"' in response.text


def test_upload_both_put_attempts_fail_shows_retry_form_with_token(monkeypatch):
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"])
    assert response.status_code == 200
    assert 'data-document-error-kind="REMOTE_OUTCOME_UNKNOWN"' in response.text
    assert 'action="/cchh/candidatos/' in response.text
    assert "retry-upload" in response.text
    assert profile_document_repository.list_for_profile(profile["profile_id"]) == []


def test_upload_folder_create_transport_ambiguity_shows_outcome_unknown_without_retry_token(monkeypatch):
    # Fase 4.1.1 corrective: ambigüedad de carpeta (sin document_id
    # todavía) -- estado REMOTE_OUTCOME_UNKNOWN, pero SIN form de
    # reintento con token (a diferencia de la ambigüedad de upload):
    # reintentar acá es simplemente reenviar la MISMA acción de upload.
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_get_json",
        lambda url, token: (_ for _ in ()).throw(_http_error(404)),
    )
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_post_json",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("timeout")),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _upload(client, profile["profile_id"])
    assert response.status_code == 200
    assert 'data-document-error-kind="REMOTE_OUTCOME_UNKNOWN"' in response.text
    assert "retry-upload" not in response.text
    assert "retry-commit" not in response.text
    assert profile_document_repository.list_for_profile(profile["profile_id"]) == []


def test_upload_confirmed_but_metadata_persistence_fails_shows_commit_retry_form(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    response = _upload(client, profile["profile_id"])
    assert response.status_code == 200
    assert 'data-document-error-kind="DOCUMENT_METADATA_PERSISTENCE_ERROR"' in response.text
    assert "retry-commit" in response.text


# ============================================================================
# retry-upload route
# ============================================================================

def test_retry_upload_succeeds_and_reuses_document_id(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    response = _upload(client, profile["profile_id"])
    token = response.text.split('name="token" value="')[1].split('"')[0]

    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: {"id": "item-final", "webUrl": "https://x"},
    )
    retry_response = client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-upload",
        data={"token": token, "graph_token": "tok"},
        files={"file": ("cv.pdf", _PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert retry_response.status_code == 303
    docs = profile_document_repository.list_for_profile(profile["profile_id"])
    assert len(docs) == 1


def test_retry_upload_with_different_file_shows_mismatch(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    response = _upload(client, profile["profile_id"])
    token = response.text.split('name="token" value="')[1].split('"')[0]

    retry_response = client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-upload",
        data={"token": token, "graph_token": "tok"},
        files={"file": ("cv.pdf", b"%PDF-1.4\ncompletely different bytes\n", "application/pdf")},
    )
    assert retry_response.status_code == 200
    assert 'data-document-error-kind="RETRY_FILE_MISMATCH"' in retry_response.text


def test_retry_upload_wrong_user_is_rejected(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    response = _upload(client, profile["profile_id"])
    token = response.text.split('name="token" value="')[1].split('"')[0]

    other_client = _client_as(_AUTHORIZED_EMAIL)  # misma sesión de auth, pero simulamos un token de otro usuario
    tampered_token = profile_document_service.sign_upload_retry_token(
        document_id="doc-x", profile_id=profile["profile_id"], folder_item_id="folder-1", filename="doc-x__cv.pdf",
        original_filename="cv.pdf", content_type="application/pdf", size_bytes=len(_PDF_BYTES),
        sha256=hashlib.sha256(_PDF_BYTES).hexdigest(), initiated_by="someone-else@danwor.com",
    )
    retry_response = other_client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-upload",
        data={"token": tampered_token, "graph_token": "tok"},
        files={"file": ("cv.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert retry_response.status_code == 200
    assert 'data-document-error-kind="VALIDATION_ERROR"' in retry_response.text


# ============================================================================
# retry-commit route
# ============================================================================

def test_retry_commit_succeeds_with_zero_graph_put(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    response = _upload(client, profile["profile_id"])
    token = response.text.split('name="token" value="')[1].split('"')[0]

    def _fail_if_called(*a, **k):
        raise AssertionError("retry-commit must never call graph_put_bytes")

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_put_bytes", _fail_if_called)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    retry_response = client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-commit", data={"token": token},
        follow_redirects=False,
    )
    assert retry_response.status_code == 303
    docs = profile_document_repository.list_for_profile(profile["profile_id"])
    assert len(docs) == 1


def test_retry_commit_twice_is_already_committed_idempotent(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    response = _upload(client, profile["profile_id"])
    token = response.text.split('name="token" value="')[1].split('"')[0]

    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-commit", data={"token": token},
        follow_redirects=False,
    )
    second = client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-commit", data={"token": token},
        follow_redirects=False,
    )
    assert second.status_code == 303
    docs = profile_document_repository.list_for_profile(profile["profile_id"])
    assert len(docs) == 1  # nunca un segundo documento por el reintento idempotente


# ============================================================================
# UI: listado / web_url / LinkedIn regression
# ============================================================================

def test_edit_page_lists_uploaded_documents_with_web_url_link():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    response = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert response.status_code == 200
    assert "cv.pdf" in response.text
    assert 'href="https://danwor.sharepoint.com/x"' in response.text
    assert "Abrir en SharePoint" in response.text


def test_edit_page_shows_empty_state_with_no_documents():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert response.status_code == 200
    assert "Todavía no hay documentos" in response.text


def test_edit_page_still_exposes_linkedin_field_no_new_cv_specific_field():
    # LinkedIn regression (ticket §13): el contrato de Profile YA expone
    # linkedin_url -- Phase 4.1 no debe agregar un campo separado.
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client, linkedin_url="https://linkedin.com/in/ana-lopez")
    response = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert response.status_code == 200
    assert 'name="linkedin_url"' in response.text
    assert 'value="https://linkedin.com/in/ana-lopez"' in response.text
    assert "linkedin_cv" not in response.text
    assert "cv_linkedin" not in response.text


# ============================================================================
# Config / security
# ============================================================================

def test_retry_commit_config_incomplete_reissues_available_state(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: False)
    response = _upload(client, profile["profile_id"])
    token = response.text.split('name="token" value="')[1].split('"')[0]

    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)
    retry_response = client.post(
        f"/cchh/candidatos/{profile['profile_id']}/documentos/retry-commit", data={"token": token},
    )
    assert retry_response.status_code == 200
    assert 'data-error-kind="config_incomplete"' in retry_response.text


# ============================================================================
# Delete (Fase 4.1.2)
# ============================================================================

def _delete(client: TestClient, profile_id: str, document_id: str, *, graph_token="tok"):
    form = {}
    if graph_token is not None:
        form["graph_token"] = graph_token
    return client.post(
        f"/cchh/candidatos/{profile_id}/documentos/{document_id}/delete", data=form, follow_redirects=False,
    )


def _uploaded_document_id(client: TestClient, profile_id: str) -> str:
    # Vía el repository directo -- MÁS robusto que scrapear el HTML
    # (evita depender de cuántas veces aparece "/documentos/" en la página).
    docs = profile_document_repository.list_for_profile(profile_id)
    assert docs, "no hay ningún documento subido para este profile_id"
    return docs[0]["document_id"]


def test_delete_anonymous_redirects_to_login():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])
    anon = TestClient(app)
    response = _delete(anon, profile["profile_id"], document_id)
    assert response.status_code in (302, 303)


def test_delete_unlisted_user_is_denied_with_403():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])
    response = _delete(_client_as("unlisted@danwor.com"), profile["profile_id"], document_id)
    assert response.status_code == 403


def test_delete_missing_document_is_404():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    response = _delete(client, profile["profile_id"], "does-not-exist")
    assert response.status_code == 404


def test_delete_document_belonging_to_a_different_profile_is_404():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile_a = _create_profile(client, first_name="Ana")
    profile_b = _create_profile(client, first_name="Bea")
    _upload(client, profile_a["profile_id"])
    document_id = _uploaded_document_id(client, profile_a["profile_id"])
    response = _delete(client, profile_b["profile_id"], document_id)
    assert response.status_code == 404


def test_delete_missing_graph_token_shows_permission_denied_state():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])
    response = _delete(client, profile["profile_id"], document_id, graph_token=None)
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text


def test_delete_success_redirects_and_removes_the_document_from_the_list(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])
    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", lambda url, token: None)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 303
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "cv.pdf" not in edit_page.text
    assert "Todavía no hay documentos" in edit_page.text


def test_delete_graph_404_still_succeeds_and_removes_metadata(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])

    def _raise_404(url, token):
        raise _http_error(404)

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", _raise_404)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 303
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "Todavía no hay documentos" in edit_page.text


def test_delete_graph_401_shows_permission_denied_and_keeps_the_document_listed(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])

    def _raise_401(url, token):
        raise _http_error(401)

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", _raise_401)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "cv.pdf" in edit_page.text


def test_delete_graph_403_shows_permission_denied_and_keeps_the_document_listed(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])

    def _raise_403(url, token):
        raise _http_error(403)

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", _raise_403)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "cv.pdf" in edit_page.text


def test_delete_graph_source_error_shows_no_disponible_and_keeps_the_document_listed(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])

    def _raise_500(url, token):
        raise _http_error(500)

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", _raise_500)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 200
    assert 'data-error-kind="source_error"' in response.text
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "cv.pdf" in edit_page.text


def test_delete_transport_ambiguity_shows_outcome_unknown_and_keeps_the_document_listed(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])

    def _raise_transport(url, token):
        raise urllib.error.URLError("network blip")

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", _raise_transport)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 200
    assert 'data-document-error-kind="DELETE_OUTCOME_UNKNOWN"' in response.text
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "cv.pdf" in edit_page.text


def test_delete_retry_after_transport_ambiguity_succeeds(monkeypatch):
    # Idempotencia end-to-end: reintentar la MISMA acción "Eliminar" tras
    # una ambigüedad de transporte termina borrando el documento igual --
    # sin token propio, el usuario simplemente reenvía el mismo form.
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])

    def _raise_transport(url, token):
        raise urllib.error.URLError("network blip")

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", _raise_transport)
    _delete(client, profile["profile_id"], document_id)

    monkeypatch.setattr(profile_document_service.microsoft365, "graph_delete", lambda url, token: None)
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 303
    edit_page = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert "Todavía no hay documentos" in edit_page.text


def test_delete_config_incomplete_shows_no_disponible(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    document_id = _uploaded_document_id(client, profile["profile_id"])
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "")
    response = _delete(client, profile["profile_id"], document_id)
    assert response.status_code == 200
    assert 'data-error-kind="config_incomplete"' in response.text


def test_edit_page_lists_document_size_and_a_delete_button():
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create_profile(client)
    _upload(client, profile["profile_id"])
    response = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar")
    assert response.status_code == 200
    assert "data-hc-document-delete-form" in response.text
    # `_PDF_BYTES` tiene menos de 1 KiB -- filesizeformat lo expresa en Bytes.
    assert "Bytes" in response.text
