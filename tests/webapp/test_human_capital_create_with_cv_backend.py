"""
test_human_capital_create_with_cv_backend.py — contrato BACKEND del flujo
create-with-CV (CCHH): una vez que el POST llega, SIEMPRE hay un outcome
observable y el Profile queda persistido. Formaliza la reproducción del
forensic (el backend nunca fue el silent no-op) y cubre las fronteras que el
handler traduce a UI: partial-success con token vacío, permiso denegado,
outcome ambiguo, éxito, y NO duplicar el Profile en un retry."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_document_service, profile_repository

_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "hc-cv-test@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client():
    s = auth_service.create_session({"email": "hc-cv-test@danwor.com", "name": "R", "first_name": "R", "family_name": "X"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, s)
    return c


def _files():
    return {"file": ("cv.pdf", io.BytesIO(_PDF), "application/pdf")}


def test_create_without_file_redirects_and_persists():
    c = _client()
    r = c.post("/cchh/candidatos/nuevo", data={"first_name": "Ana", "last_name": "Perez"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cchh/candidatos"
    assert len(profile_repository.list()) == 1


def test_create_with_file_empty_token_is_partial_success_visible():
    c = _client()
    r = c.post("/cchh/candidatos/nuevo",
               data={"first_name": "Bob", "last_name": "Lopez", "graph_token": ""},
               files=_files(), follow_redirects=False)
    assert r.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in r.text
    assert len(profile_repository.list()) == 1  # Profile persistido pese al CV pendiente


def test_create_with_file_permission_denied_is_visible(monkeypatch):
    def _boom(**kwargs):
        raise profile_document_service.ProfileDocumentPermissionDeniedError("sin permiso")
    monkeypatch.setattr(profile_document_service, "upload_document", _boom)
    c = _client()
    r = c.post("/cchh/candidatos/nuevo",
               data={"first_name": "Cara", "last_name": "Diaz", "graph_token": "tok"},
               files=_files(), follow_redirects=False)
    assert r.status_code == 200
    assert 'data-error-kind="graph_permission_denied"' in r.text
    assert len(profile_repository.list()) == 1


def test_create_with_file_ambiguous_upload_is_visible(monkeypatch):
    def _ambiguous(**kwargs):
        raise profile_document_service.ProfileDocumentUploadRetryRequired("rt")
    monkeypatch.setattr(profile_document_service, "upload_document", _ambiguous)
    c = _client()
    r = c.post("/cchh/candidatos/nuevo",
               data={"first_name": "Eve", "last_name": "Mora", "graph_token": "tok"},
               files=_files(), follow_redirects=False)
    assert r.status_code == 200
    assert 'data-document-error-kind="REMOTE_OUTCOME_UNKNOWN"' in r.text
    assert len(profile_repository.list()) == 1


def test_create_with_file_success_redirects(monkeypatch):
    monkeypatch.setattr(profile_document_service, "upload_document", lambda **kwargs: {"ok": True})
    c = _client()
    r = c.post("/cchh/candidatos/nuevo",
               data={"first_name": "Gio", "last_name": "Ruiz", "graph_token": "tok"},
               files=_files(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cchh/candidatos"
    assert len(profile_repository.list()) == 1


def test_retry_after_partial_success_does_not_duplicate_profile():
    c = _client()
    data = {"first_name": "Ana", "last_name": "Perez", "graph_token": ""}
    first = c.post("/cchh/candidatos/nuevo", data=data, files=_files(), follow_redirects=False)
    assert first.status_code == 200  # partial success, profile creado
    assert len(profile_repository.list()) == 1
    # Retry idéntico SIN duplicate_confirmed -> advisory de duplicado, NO 2do Profile.
    again = c.post("/cchh/candidatos/nuevo", data=data, files=_files(), follow_redirects=False)
    assert again.status_code == 200
    assert len(profile_repository.list()) == 1
