"""
test_human_capital_profile_new_cv_upload.py — Capital Humano, paridad
funcional con `CvUploader`/`BancoCvForm` de la app de referencia: el CV se
puede adjuntar durante el ALTA (`POST /cchh/candidatos/nuevo`), no sólo
después vía el panel de Documentos de edición.

CV OPCIONAL — confirmado LEYENDO la fuente de la referencia (nunca
asumido): `BancoCvForm.tsx` no marca el input `required`, y
`lib/actions/banco-cvs.ts::crearBancoCv` persiste `cv_url: cv_url || null`
sin exigirlo; la carga masiva además crea candidatos sin ningún CV. MISMO
criterio acá: un Profile puede existir sin ProfileDocument.

Orquestación (sin atomicidad fake entre Spaces y SharePoint, ver el POST
handler en webapp/routers/human_capital.py):
  1. validar el archivo (si vino uno) ANTES de crear el Profile — vía
     `profile_document_validation.validate_upload`, la MISMA función pura
     que `profile_document_service.upload_document` ya usa como primer
     paso — cero segunda implementación de validación.
  2. crear el Profile (`human_capital_service.create_profile`, sin cambios).
  3. si había CV, subirlo como el ProfileDocument inicial de ESE profile_id
     recién creado, reusando `profile_document_service.upload_document`
     tal cual (el MISMO call que usa la ruta de Documentos existente).
  4. si el upload falla en cualquier punto: el Profile YA persistido se
     PRESERVA (nunca se borra como compensación), y la respuesta reusa
     `_render_profile_edit_with_documents` — el MISMO helper/plantilla que
     la página de edición ya usa para sus propios fallos de documento — así
     que el reintento del usuario es literalmente el form de Documentos que
     esa página ya renderiza, nunca una segunda UI de reintento.

Graph se monkeypatchea al nivel de `core.microsoft365` vía
`profile_document_service` — MISMO patrón exacto que
test_human_capital_profile_documents_routes.py, nunca HTTP real."""
from __future__ import annotations

import urllib.error

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, profile_document_repository, profile_document_service, profile_repository,
)

_AUTHORIZED_EMAIL = "human-capital-new-cv-test@danwor.com"
_PDF_BYTES = b"%PDF-1.4\n%test content\n"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://graph.microsoft.com/v1.0/x", code, "err", None, None)


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
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
    monkeypatch.setattr(profile_document_service.microsoft365, "graph_get_json", lambda url, token: {"id": "folder-1"})
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: {"id": "item-1", "webUrl": "https://danwor.sharepoint.com/x"},
    )


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC New CV Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _submit_new(client: TestClient, *, graph_token="tok", filename=None, data=None, **overrides):
    form = {"first_name": "Ana", "last_name": "Lopez", **overrides}
    if graph_token is not None:
        form["graph_token"] = graph_token
    files = {"file": (filename, data, "application/pdf")} if filename else None
    return client.post(
        "/cchh/candidatos/nuevo", data=form, files=files, follow_redirects=False,
    )


# ============================================================================
# Campo CV presente en el form de alta, en el lugar de la referencia
# ============================================================================

def test_new_page_includes_a_cv_field_with_reference_copy():
    text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos/nuevo").text
    assert '<label for="hc-new-cv-file">CV</label>' in text
    assert 'accept=".pdf,.doc,.docx"' in text
    assert "Adjuntar archivo" in text
    assert "máx. 10 MB" in text
    assert 'enctype="multipart/form-data"' in text


# ============================================================================
# Alta CON CV → Profile + ProfileDocument, ambos creados
# ============================================================================

def test_create_with_cv_creates_both_profile_and_document():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code == 303
    assert response.headers["location"] == "/cchh/candidatos"
    profiles = profile_repository.list()
    assert len(profiles) == 1
    docs = profile_document_repository.list_for_profile(profiles[0]["profile_id"])
    assert len(docs) == 1
    assert docs[0]["filename"] == "cv.pdf"


# ============================================================================
# Alta SIN CV → sigue funcionando exactamente igual que antes (CV opcional,
# verificado en la referencia, nunca asumido)
# ============================================================================

def test_create_without_cv_still_creates_only_the_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/cchh/candidatos"
    profiles = profile_repository.list()
    assert len(profiles) == 1
    assert profile_document_repository.list_for_profile(profiles[0]["profile_id"]) == []


# ============================================================================
# Validación del archivo ANTES de crear el Profile — nada persiste si el
# archivo es inválido.
# ============================================================================

def test_invalid_extension_rejects_before_creating_the_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.txt", data=b"plain text")
    assert response.status_code == 200
    assert 'class="feedback-error"' in response.text
    assert profile_repository.list() == []


def test_oversized_file_rejects_before_creating_the_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    oversized = b"%PDF-1.4\n" + b"0" * (profile_document_service.profile_document_validation.MAX_CV_UPLOAD_BYTES + 1)
    response = _submit_new(client, filename="cv.pdf", data=oversized)
    assert response.status_code == 200
    assert 'class="feedback-error"' in response.text
    assert profile_repository.list() == []


# ============================================================================
# Profile creado + upload de CV fallido -> estado explícito de éxito
# parcial, el Profile se preserva, nunca se reporta éxito falso.
# ============================================================================

def test_missing_graph_token_creates_profile_but_shows_permission_denied_state():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, graph_token=None, filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text
    # Éxito parcial real: el Profile SÍ se creó (nunca deshecho), sólo el
    # documento quedó pendiente.
    profiles = profile_repository.list()
    assert len(profiles) == 1
    assert profile_document_repository.list_for_profile(profiles[0]["profile_id"]) == []


def test_graph_403_creates_profile_but_shows_permission_denied_state(monkeypatch):
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_get_json",
        lambda url, token: (_ for _ in ()).throw(_http_error(403)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in response.text
    assert len(profile_repository.list()) == 1


def test_graph_401_creates_profile_but_shows_source_error_state(monkeypatch):
    # MISMA clasificación ya establecida en profile_document_service.py
    # (folder-ensure): sólo 403 es "permiso definitivo" -- 401 cae en
    # GraphSourceError genérico ("No disponible"), MISMO criterio que la
    # ruta de Documentos existente (ver ensure_profile_folder). No se
    # reclasifica acá, sólo se reusa tal cual.
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_get_json",
        lambda url, token: (_ for _ in ()).throw(_http_error(401)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code == 200
    assert 'data-error-kind="source_error"' in response.text
    assert len(profile_repository.list()) == 1


def test_ambiguous_upload_outcome_creates_profile_shows_retry_form_never_deletes_metadata(monkeypatch):
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code == 200
    assert 'data-document-error-kind="REMOTE_OUTCOME_UNKNOWN"' in response.text
    assert "retry-upload" in response.text
    # El Profile se preserva -- nunca se borra como "compensación" de un
    # upload que en realidad podría haber llegado al remoto.
    profiles = profile_repository.list()
    assert len(profiles) == 1
    assert profile_document_repository.list_for_profile(profiles[0]["profile_id"]) == []


def test_partial_success_response_never_looks_like_a_plain_success_redirect(monkeypatch):
    # "no silently report full success" -- una falla de documento NUNCA
    # devuelve el 303 al listado que sí devuelve un alta 100% exitosa.
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.pdf", data=_PDF_BYTES)
    assert response.status_code != 303


# ============================================================================
# Reintento vía el contrato YA existente (retry-upload) -- nunca una
# segunda Profile, MISMO document_id preservado.
# ============================================================================

def test_retry_after_ambiguous_upload_succeeds_without_a_duplicate_profile(monkeypatch):
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    response = _submit_new(client, filename="cv.pdf", data=_PDF_BYTES)
    import re
    token_match = re.search(r'name="token" value="([^"]+)"', response.text)
    assert token_match, "el form de reintento debe traer el token firmado"
    retry_token = token_match.group(1)
    profile_id = profile_repository.list()[0]["profile_id"]

    # El reintento ahora SÍ confirma (misma ruta/contrato existente).
    monkeypatch.setattr(
        profile_document_service.microsoft365, "graph_put_bytes",
        lambda *a, **k: {"id": "item-1", "webUrl": "https://danwor.sharepoint.com/x"},
    )
    retry_response = client.post(
        f"/cchh/candidatos/{profile_id}/documentos/retry-upload",
        data={"token": retry_token, "graph_token": "tok"},
        files={"file": ("cv.pdf", _PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert retry_response.status_code == 303
    assert len(profile_repository.list()) == 1  # sigue siendo UN solo Profile
    assert len(profile_document_repository.list_for_profile(profile_id)) == 1


# ============================================================================
# Guardrail arquitectónico: cero segunda implementación de upload.
# ============================================================================

def test_no_second_upload_implementation_was_introduced():
    import inspect
    from webapp.routers import human_capital as human_capital_router
    source = inspect.getsource(human_capital_router)
    # DOS call sites esperados -- la ruta de Documentos existente Y el
    # nuevo POST de alta -- ambos llamando a la MISMA función del service,
    # nunca una tercera/paralela implementación de upload.
    assert source.count("profile_document_service.upload_document(") == 2
    # Y el POST de alta debe reusar el MISMO helper de render que ya usan
    # todas las rutas de documentos para sus propios estados de error
    # (nunca una plantilla nueva para "éxito parcial").
    new_submit_src = source[source.index("async def human_capital_profile_new_submit"):]
    new_submit_src = new_submit_src[:new_submit_src.index("\n\n\ndef _edit_page_context")]
    assert "_render_profile_edit_with_documents(" in new_submit_src
    assert new_submit_src.count("profile_document_service.upload_document(") == 1
