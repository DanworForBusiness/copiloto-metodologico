"""
test_human_capital_legacy_compat.py — Capital Humano UX Parity: contrato de
la CAPA DE COMPATIBILIDAD legacy `/human-capital/*` -> superficie canónica
`/cchh/*`.

Contrato (ver webapp/routers/human_capital.py, sección "Compatibilidad
legacy"):
  - GET legacy -> 308 permanente al equivalente /cchh, path params
    preservados; NUNCA sirve contenido propio (es solo puente hacia afuera).
  - POST legacy -> alias al MISMO handler canónico (segundo decorador), no
    redirect: preserva método, form/body/uploads, path params, actor/sesión
    y response semantics.
Las dos rutas genuinamente nuevas (GET /cchh/candidatos/nuevo, POST
.../documentos/{document_id}/delete) NO tienen par legacy y no se testean
acá. MISMO patrón de autorización/aislamiento que el resto del router."""
from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository

_AUTHORIZED_EMAIL = "human-capital-compat-test@danwor.com"

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


def _client_as(email: str = _AUTHORIZED_EMAIL) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Compat Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _one_row_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_ALL_HEADERS)
    ws.append(["Ada", "Lovelace", "ada@example.com", "", "", "", "Python", "Senior",
               "LinkedIn", "", "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------
# GET legacy -> 308 al equivalente /cchh (path params preservados)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "legacy, canonical",
    [
        ("/human-capital", "/cchh"),
        ("/human-capital/profiles", "/cchh/candidatos"),
        ("/human-capital/profiles/import", "/cchh/candidatos/importar"),
    ],
)
def test_get_legacy_redirects_308_to_canonical(legacy, canonical):
    client = _client_as()
    resp = client.get(legacy, follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == canonical


def test_get_legacy_edit_redirect_preserves_path_param():
    client = _client_as()
    resp = client.get("/human-capital/profiles/AB-42/edit", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/cchh/candidatos/AB-42/editar"


# --------------------------------------------------------------------------
# POST legacy -> MISMO handler (alias), no redirect: método/payload/actor
# preservados, misma response semantics que la canónica.
# --------------------------------------------------------------------------
def test_post_legacy_import_preview_reaches_same_handler_not_a_redirect():
    client = _client_as()
    xlsx = _one_row_xlsx()
    files = {"file": ("candidatos.xlsx",
                      io.BytesIO(xlsx),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    legacy = client.post("/human-capital/profiles/import/preview", files=files, follow_redirects=False)
    # NO es un redirect de compatibilidad: el handler procesó el upload.
    assert legacy.status_code == 200
    assert "location" not in {k.lower() for k in legacy.headers}
    # El payload se preservó: el candidato subido aparece en el preview.
    assert "Ada" in legacy.text and "Lovelace" in legacy.text

    # Paridad con la superficie canónica: mismo status y mismo candidato
    # renderizado (es literalmente el mismo handler).
    canonical = client.post(
        "/cchh/candidatos/importar/preview",
        files={"file": ("candidatos.xlsx", io.BytesIO(xlsx),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        follow_redirects=False,
    )
    assert canonical.status_code == legacy.status_code == 200
    assert ("Ada" in canonical.text) and ("Lovelace" in canonical.text)


def test_post_legacy_alias_enforces_same_auth_gate():
    # Sin sesión HC autorizada, el alias legacy NO procesa (mismo gate que la
    # canónica) — nunca 200 de contenido real.
    unauth = TestClient(app)
    resp = unauth.post("/human-capital/profiles/import/preview", files={
        "file": ("x.xlsx", io.BytesIO(_one_row_xlsx()),
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    }, follow_redirects=False)
    assert resp.status_code != 200


# --------------------------------------------------------------------------
# Control: la superficie canónica /cchh sigue operativa (con la landing "01
# Inicio" retirada por ahora, /cchh redirige a la entrada real, Candidatos).
# --------------------------------------------------------------------------
def test_canonical_cchh_root_redirects_to_candidatos_entry():
    client = _client_as()
    resp = client.get("/cchh", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/cchh/candidatos"
    assert client.get("/cchh/candidatos").status_code == 200
