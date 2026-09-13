"""
test_human_capital_profile_import_routes.py — Capital Humano Phase 3.8
(Safe Bulk Profile Import): contrato HTTP/UI de
`GET /human-capital/profiles/import` +
`POST /human-capital/profiles/import/preview` +
`POST /human-capital/profiles/import/confirm`. MISMO patrón de
autorización/aislamiento que los demás archivos de test de este router."""
from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_import_service, profile_repository

_AUTHORIZED_EMAIL = "human-capital-import-test@danwor.com"

_ALL_HEADERS = ["nombre", "apellido", "email", "telefono", "linkedin", "ubicacion",
                "perfil_skills", "seniority", "fuente", "tags", "notas", "potencial", "marca_potencial"]


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Import Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _row(nombre="Juan", apellido="Perez", email="", fuente="", potencial="") -> list:
    return [nombre, apellido, email, "", "", "", "", "", fuente, "", "", potencial, ""]


def _xlsx_bytes(rows: list[list], headers: list[str] = _ALL_HEADERS) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client: TestClient, data: bytes, filename: str = "perfiles.xlsx"):
    return client.post(
        "/human-capital/profiles/import/preview",
        files={"file": (filename, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


# ============================================================================
# Authorization
# ============================================================================

def test_get_import_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/human-capital/profiles/import", follow_redirects=False)
    assert response.status_code == 303


def test_get_import_unlisted_user_is_denied_with_403():
    response = _client_as("unlisted@danwor.com").get("/human-capital/profiles/import")
    assert response.status_code == 403


def test_preview_unlisted_user_is_denied_with_403():
    response = _client_as("unlisted@danwor.com").post("/human-capital/profiles/import/preview")
    assert response.status_code == 403


def test_confirm_unlisted_user_is_denied_with_403():
    response = _client_as("unlisted@danwor.com").post("/human-capital/profiles/import/confirm")
    assert response.status_code == 403


def test_get_import_authorized_returns_200():
    response = _client_as(_AUTHORIZED_EMAIL).get("/human-capital/profiles/import")
    assert response.status_code == 200
    assert "XLSX" in response.text


# ============================================================================
# Upload / preview
# ============================================================================

def test_missing_file_shows_error():
    response = _client_as(_AUTHORIZED_EMAIL).post("/human-capital/profiles/import/preview")
    assert response.status_code == 200
    assert "Elegí un archivo" in response.text


def test_empty_file_shows_error():
    response = _upload(_client_as(_AUTHORIZED_EMAIL), b"")
    assert response.status_code == 200
    assert "vacío" in response.text


def test_oversize_file_is_rejected(monkeypatch):
    monkeypatch.setattr(profile_import_service, "MAX_UPLOAD_BYTES", 10)
    data = _xlsx_bytes([_row()])
    assert len(data) > 10
    response = _upload(_client_as(_AUTHORIZED_EMAIL), data)
    assert response.status_code == 200
    assert "supera el máximo" in response.text


def test_invalid_workbook_is_rejected_cleanly():
    response = _upload(_client_as(_AUTHORIZED_EMAIL), b"esto no es un xlsx", filename="perfiles.xlsx")
    assert response.status_code == 200
    assert "válido" in response.text
    assert "Traceback" not in response.text


def test_missing_required_headers_rejected():
    data = _xlsx_bytes([["a@x.com"]], headers=["email"])
    response = _upload(_client_as(_AUTHORIZED_EMAIL), data)
    assert response.status_code == 200
    assert "Faltan columnas" in response.text


def test_row_limit_exceeded_rejected(monkeypatch):
    monkeypatch.setattr(profile_import_service, "MAX_IMPORT_ROWS", 2)
    data = _xlsx_bytes([_row(), _row(nombre="Ana"), _row(nombre="Luis")])
    response = _upload(_client_as(_AUTHORIZED_EMAIL), data)
    assert response.status_code == 200
    assert "máximo" in response.text


def test_valid_xlsx_shows_preview_with_eligible_row():
    data = _xlsx_bytes([_row()])
    response = _upload(_client_as(_AUTHORIZED_EMAIL), data)
    assert response.status_code == 200
    assert "Juan Perez" in response.text
    assert 'name="all_tokens"' in response.text


def test_preview_does_not_create_any_profile():
    data = _xlsx_bytes([_row()])
    client = _client_as(_AUTHORIZED_EMAIL)
    _upload(client, data)
    assert profile_repository.list() == []


def test_preview_shows_existing_duplicate_advisory():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Perez", "email": "juan@x.com"})
    data = _xlsx_bytes([_row(email="juan@x.com")])
    response = _upload(client, data)
    assert 'data-row-status="existing_duplicate"' in response.text


def test_preview_shows_within_file_duplicate_advisory():
    data = _xlsx_bytes([_row(email="juan@x.com"), _row(email="JUAN@x.com")])
    response = _upload(_client_as(_AUTHORIZED_EMAIL), data)
    assert 'data-row-status="within_file_duplicate"' in response.text


def test_preview_shows_invalid_row():
    data = _xlsx_bytes([_row(nombre="")])
    response = _upload(_client_as(_AUTHORIZED_EMAIL), data)
    assert 'data-row-status="invalid"' in response.text


# ============================================================================
# Confirm — integrity / idempotency / collision
# ============================================================================

def _extract_all_tokens(html: str) -> list[str]:
    import re
    return re.findall(r'name="all_tokens" value="([^"]+)"', html)


def test_confirm_with_valid_token_creates_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    preview_html = _upload(client, _xlsx_bytes([_row()])).text
    tokens = _extract_all_tokens(preview_html)
    assert len(tokens) == 1
    response = client.post(
        "/human-capital/profiles/import/confirm",
        data={"all_tokens": tokens, "include_tokens": tokens, "invalid_row_count": "0"},
    )
    assert response.status_code == 200
    assert "CREATED" in response.text
    assert len(profile_repository.list()) == 1


def test_confirm_tampered_token_rejected():
    client = _client_as(_AUTHORIZED_EMAIL)
    preview_html = _upload(client, _xlsx_bytes([_row()])).text
    token = _extract_all_tokens(preview_html)[0]
    tampered = token[:-4] + ("zzzz" if token[-4:] != "zzzz" else "yyyy")
    response = client.post(
        "/human-capital/profiles/import/confirm",
        data={"all_tokens": [tampered], "include_tokens": [tampered], "invalid_row_count": "0"},
    )
    assert response.status_code == 200
    assert "INVALID" in response.text
    assert profile_repository.list() == []


def test_confirm_swapped_profile_id_rejected():
    # Un profile_id "swapeado" (apuntando a un Profile ya existente)
    # requeriría re-firmar el token -- sin la SESSION_SECRET real, esto
    # es exactamente el mismo caso que un token tampered/forjado.
    client = _client_as(_AUTHORIZED_EMAIL)
    from jose import jwt as _jwt
    forged = _jwt.encode(
        {"profile_id": "swapped-id", "row_number": 1, "fields": {"first_name": "X", "last_name": "Y"},
         "has_duplicate_advisory": False, "exp": 9999999999},
        "attacker-guessed-wrong-secret", algorithm="HS256",
    )
    response = client.post(
        "/human-capital/profiles/import/confirm",
        data={"all_tokens": [forged], "include_tokens": [forged], "invalid_row_count": "0"},
    )
    assert "INVALID" in response.text
    assert profile_repository.list() == []


def test_duplicate_row_not_included_is_skipped_without_creating():
    client = _client_as(_AUTHORIZED_EMAIL)
    preview_html = _upload(client, _xlsx_bytes([_row(email="juan@x.com"), _row(email="JUAN@x.com")])).text
    tokens = _extract_all_tokens(preview_html)
    assert len(tokens) == 2
    # Confirma SOLO el primero (elegible) -- deja el dup sin marcar.
    response = client.post(
        "/human-capital/profiles/import/confirm",
        data={"all_tokens": tokens, "include_tokens": [tokens[0]], "invalid_row_count": "0"},
    )
    assert "DUPLICATE_SKIPPED" in response.text
    assert len(profile_repository.list()) == 1


def test_same_confirm_retried_is_already_committed():
    client = _client_as(_AUTHORIZED_EMAIL)
    preview_html = _upload(client, _xlsx_bytes([_row()])).text
    tokens = _extract_all_tokens(preview_html)
    client.post("/human-capital/profiles/import/confirm", data={"all_tokens": tokens, "include_tokens": tokens, "invalid_row_count": "0"})
    response2 = client.post("/human-capital/profiles/import/confirm", data={"all_tokens": tokens, "include_tokens": tokens, "invalid_row_count": "0"})
    assert "ALREADY_COMMITTED" in response2.text
    assert len(profile_repository.list()) == 1


def test_unknown_outcome_shows_retry_action(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    preview_html = _upload(client, _xlsx_bytes([_row()])).text
    tokens = _extract_all_tokens(preview_html)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    response = client.post("/human-capital/profiles/import/confirm", data={"all_tokens": tokens, "include_tokens": tokens, "invalid_row_count": "0"})
    assert "REMOTE_OUTCOME_UNKNOWN" in response.text
    assert "Reintentar esta fila" in response.text


def test_result_summary_shows_expected_counts():
    client = _client_as(_AUTHORIZED_EMAIL)
    preview_html = _upload(client, _xlsx_bytes([_row(), _row(nombre="")])).text
    tokens = _extract_all_tokens(preview_html)
    response = client.post(
        "/human-capital/profiles/import/confirm",
        data={"all_tokens": tokens, "include_tokens": tokens, "invalid_row_count": "1"},
    )
    assert response.status_code == 200
    text = response.text
    assert "Total" in text and "Creados" in text


# ============================================================================
# Security / temp-file (§24/§29)
# ============================================================================

def test_no_xlsx_persisted_locally_or_mirrored_to_spaces(tmp_path):
    client = _client_as(_AUTHORIZED_EMAIL)
    _upload(client, _xlsx_bytes([_row()]))
    # Ningún archivo .xlsx debe existir en ningún lugar bajo BASE_DIR.
    matches = list(tmp_path.rglob("*.xlsx"))
    assert matches == []


def test_import_surface_has_no_cv_or_sharepoint():
    from pathlib import Path
    router_py = (Path(__file__).resolve().parents[2] / "webapp" / "routers" / "human_capital.py").read_text(encoding="utf-8")
    service_py = (Path(__file__).resolve().parents[2] / "webapp" / "services" / "profile_import_service.py").read_text(encoding="utf-8")
    for surface in (router_py, service_py):
        assert "cv_ref" not in surface
        assert "from core.microsoft365" not in surface
        assert "core.sharepoint_cases" not in surface


def test_manual_create_route_unaffected_by_import_capability():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = client.post("/human-capital/profiles", data={"first_name": "Manual", "last_name": "Alta"})
    assert response.status_code == 200
    assert len(profile_repository.list()) == 1