"""test_human_capital_soft_delete.py — borrado LÓGICO de candidatos (owner
closure): soft delete, documentos PRESERVADOS, candidato borrado EXCLUIDO de
listados/búsquedas normales. Cubre service + repository + las dos entradas de
ruta (listado vía HTMX / edición full-page), una sola operación de servicio
compartida. MISMO patrón autouse de aislamiento/autorización que
test_human_capital_profiles_routes.py (fixture con monkeypatch, push_path->True
para que el write remoto confirme en test, nunca una asignación sin revertir).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository, human_capital_service
from webapp.services import profile_document_repository

_AUTHORIZED_EMAIL = "hc-delete-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    # push_path->True: el write LOCAL cuenta como durabilidad confirmada en test
    # (update()/soft_delete devuelven éxito), MISMO criterio que el resto de los
    # tests de escritura de Capital Humano.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(profile_document_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_document_repository.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    session = auth_service.create_session(
        {"email": _AUTHORIZED_EMAIL, "name": "D T", "first_name": "D", "family_name": "T"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ── service / repository ───────────────────────────────────────────────────

def test_soft_delete_marks_deleted_and_excludes_from_list():
    p = profile_repository.create({"first_name": "Ana", "last_name": "Li"})
    human_capital_service.soft_delete_profile(p["profile_id"], "actor@danwor.com")
    stored = profile_repository.get(p["profile_id"])
    assert stored["deleted_at"]                        # marcado, no borrado físico
    assert stored["deleted_by"] == "actor@danwor.com"
    # excluido de listados/búsquedas normales...
    assert all(x["profile_id"] != p["profile_id"] for x in human_capital_service.list_profiles())
    # ...pero el registro sigue existiendo en disco (soft, no hard).
    assert profile_repository.get(p["profile_id"]) is not None


def test_soft_delete_is_idempotent():
    p = profile_repository.create({"first_name": "Ana", "last_name": "Li"})
    first = human_capital_service.soft_delete_profile(p["profile_id"], "a")
    second = human_capital_service.soft_delete_profile(p["profile_id"], "b")
    # un segundo borrado NO re-escribe (mismo deleted_at, no re-stampa el actor).
    assert second["deleted_at"] == first["deleted_at"]
    assert second["deleted_by"] == "a"


def test_soft_delete_preserves_other_profiles():
    keep = profile_repository.create({"first_name": "Keep", "last_name": "Me"})
    drop = profile_repository.create({"first_name": "Drop", "last_name": "Me"})
    human_capital_service.soft_delete_profile(drop["profile_id"], "a")
    ids = [x["profile_id"] for x in human_capital_service.list_profiles()]
    assert keep["profile_id"] in ids
    assert drop["profile_id"] not in ids


def test_soft_delete_preserves_documents():
    p = profile_repository.create({"first_name": "Doc", "last_name": "Keep"})
    profile_document_repository.create_with_explicit_id(
        "doc-1111",
        {
            "profile_id": p["profile_id"], "provider": "sharepoint",
            "site_id": "s", "drive_id": "d", "item_id": "i",
            "filename": "cv.pdf", "content_type": "application/pdf",
            "size_bytes": 1024, "uploaded_at": "2026-09-27T00:00:00Z",
            "uploaded_by": "a@danwor.com",
        },
    )
    human_capital_service.soft_delete_profile(p["profile_id"], "a")
    # El documento del candidato sigue existiendo tras el borrado lógico.
    docs = profile_document_repository.list_for_profile(p["profile_id"])
    assert any(d["document_id"] == "doc-1111" for d in docs)


# ── ruta: listado (HTMX) ────────────────────────────────────────────────────

def test_delete_route_htmx_returns_panel_without_candidate():
    p = profile_repository.create({"first_name": "Zoe", "last_name": "Unica"})
    r = _client().post(f"/cchh/candidatos/{p['profile_id']}/eliminar", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Zoe Unica" not in r.text
    assert p["profile_id"] not in [x["profile_id"] for x in human_capital_service.list_profiles()]


def test_delete_route_htmx_preserves_active_filter():
    profile_repository.create({"first_name": "SenKeep", "last_name": "Uno", "seniority": "Senior"})
    junior = profile_repository.create({"first_name": "JunDrop", "last_name": "Dos", "seniority": "Junior"})
    # borra un Junior mientras el filtro activo es seniority=Senior
    r = _client().post(
        f"/cchh/candidatos/{junior['profile_id']}/eliminar",
        data={"seniority": "Senior"}, headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "SenKeep Uno" in r.text     # el filtro Senior se conserva en el re-render
    assert "JunDrop Dos" not in r.text  # el Junior no aparece bajo ese filtro


# ── ruta: edición (full-page) ───────────────────────────────────────────────

def test_delete_route_fullpage_redirects_to_list():
    p = profile_repository.create({"first_name": "Full", "last_name": "Page"})
    r = _client().post(f"/cchh/candidatos/{p['profile_id']}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cchh/candidatos"
    assert p["profile_id"] not in [x["profile_id"] for x in human_capital_service.list_profiles()]


def test_deleted_candidate_edit_page_is_404():
    p = profile_repository.create({"first_name": "Gone", "last_name": "Now"})
    human_capital_service.soft_delete_profile(p["profile_id"], "a")
    r = _client().get(f"/cchh/candidatos/{p['profile_id']}/editar")
    assert r.status_code == 404


def test_delete_route_requires_authorization():
    p = profile_repository.create({"first_name": "Auth", "last_name": "Guard"})
    session = auth_service.create_session(
        {"email": "unlisted@danwor.com", "name": "X", "first_name": "X", "family_name": "Y"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    r = c.post(f"/cchh/candidatos/{p['profile_id']}/eliminar")
    assert r.status_code == 403
    # el candidato NO fue borrado por un no autorizado
    assert profile_repository.get(p["profile_id"]).get("deleted_at") is None
