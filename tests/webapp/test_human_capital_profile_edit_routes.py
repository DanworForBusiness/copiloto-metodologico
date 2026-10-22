"""
test_human_capital_profile_edit_routes.py — Capital Humano Phase 3.5 (Safe
Profile Edit — Revision + Compensating Overwrite): contrato HTTP/UI de
`GET/POST /cchh/candidatos/{profile_id}/editar`. MISMO patrón de
autorización/aislamiento que test_human_capital_profiles_routes.py (fixture
`autouse` con `monkeypatch`, nunca una asignación de módulo sin revertir)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo

_AUTHORIZED_EMAIL = "human-capital-edit-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")     # estos tests simulan fallos de Spaces: ejercitan el contrato REMOTE (el modo es explícito, no se infiere)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    # MISMO seed que test_human_capital_profiles_routes.py: el alta exige que
    # Seniority pertenezca al catálogo GOBERNADO activo (CCHH Catalog
    # Foundation §6) -- sin esto, cualquier `_create(..., seniority="Junior")`
    # falla con ValueError silencioso (el POST responde 200 con `error`, no
    # crea el Profile). Sólo "Junior" (usado por los tests de `.hero-meta`)
    # necesita estar en catálogo acá.
    snap = catalog_repo.build_snapshot(
        {
            "seniority": [{"valor": "Junior", "orden": 1}, {"valor": "Senior", "orden": 2}],
            "fuente": [{"valor": "LinkedIn", "orden": 1}],
        },
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"},
    )
    snapshot_path = catalog_repo.snapshot_path()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(catalog_repo._serialize(snap))


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Edit Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _create(client: TestClient, **overrides) -> dict:
    data = {"first_name": "Juan", "last_name": "Gómez", **overrides}
    client.post("/cchh/candidatos/nuevo", data=data)
    matches = [p for p in profile_repository.list() if p["first_name"] == data["first_name"]]
    return matches[0]


# ============================================================================
# Authorization — MISMO contrato ya probado para /cchh/candidatos
# ============================================================================

def test_get_edit_anonymous_redirects_to_login():
    client = _create(_client_as(_AUTHORIZED_EMAIL))
    anon = TestClient(app)
    response = anon.get(f"/cchh/candidatos/{client['profile_id']}/editar", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/platform?next=%2Fcchh%2Fcandidatos%2F{client['profile_id']}%2Feditar"


def test_get_edit_unlisted_user_is_denied_with_403():
    saved = _create(_client_as(_AUTHORIZED_EMAIL))
    response = _client_as("unlisted@danwor.com").get(f"/cchh/candidatos/{saved['profile_id']}/editar")
    assert response.status_code == 403


def test_post_edit_unlisted_user_is_denied_with_403():
    saved = _create(_client_as(_AUTHORIZED_EMAIL))
    response = _client_as("unlisted@danwor.com").post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "A", "last_name": "B", "expected_revision": "1"},
    )
    assert response.status_code == 403


# ============================================================================
# GET — form poblado / 404
# ============================================================================

def test_authorized_get_edit_returns_200_with_populated_form():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, first_name="Juan", last_name="Gómez", location="Rosario")
    response = client.get(f"/cchh/candidatos/{saved['profile_id']}/editar")
    assert response.status_code == 200
    assert 'value="Juan"' in response.text
    assert 'value="Gómez"' in response.text
    assert 'value="Rosario"' in response.text
    assert 'name="expected_revision" value="1"' in response.text


# ============================================================================
# GET — contexto `.hero-meta` (Perfil/Skills · Seniority) bajo el header
# ============================================================================

def test_get_edit_hero_meta_shows_both_skills_and_seniority_joined():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, profile_skills="SAP BI", seniority="Junior")
    response = client.get(f"/cchh/candidatos/{saved['profile_id']}/editar")
    assert response.status_code == 200
    assert '<div class="hero-meta"><span>SAP BI</span><span>Junior</span></div>' in response.text


def test_get_edit_hero_meta_shows_only_skills_when_seniority_is_empty():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, profile_skills="SAP BI", seniority="")
    response = client.get(f"/cchh/candidatos/{saved['profile_id']}/editar")
    assert '<div class="hero-meta"><span>SAP BI</span></div>' in response.text
    assert "Sin especificar" not in response.text.split('<div class="hero-meta">')[1].split("</div>")[0]


def test_get_edit_hero_meta_shows_only_seniority_when_skills_is_empty():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, profile_skills="", seniority="Junior")
    response = client.get(f"/cchh/candidatos/{saved['profile_id']}/editar")
    assert '<div class="hero-meta"><span>Junior</span></div>' in response.text


def test_get_edit_hero_meta_is_absent_when_both_skills_and_seniority_are_empty():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, profile_skills="", seniority="")
    response = client.get(f"/cchh/candidatos/{saved['profile_id']}/editar")
    assert 'class="hero-meta"' not in response.text


def test_get_edit_missing_profile_is_404():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = client.get("/cchh/candidatos/does-not-exist/editar")
    assert response.status_code == 404


def test_post_edit_missing_profile_is_404():
    client = _client_as(_AUTHORIZED_EMAIL)
    response = client.post(
        "/cchh/candidatos/does-not-exist/editar",
        data={"first_name": "A", "last_name": "B", "expected_revision": "1"},
    )
    assert response.status_code == 404


# ============================================================================
# POST — edición válida
# ============================================================================

def test_valid_edit_redirects_to_the_profiles_list():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Ruiz", "expected_revision": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/cchh/candidatos"


def test_valid_edit_persists_the_change():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Ruiz", "expected_revision": "1"},
    )
    updated = profile_repository.get(saved["profile_id"])
    assert updated["last_name"] == "Ruiz"
    assert updated["revision"] == 2


def test_valid_edit_reflected_on_the_list_page():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Ruiz", "expected_revision": "1"},
    )
    response = client.get("/cchh/candidatos")
    assert "Juan Ruiz" in response.text
    assert "Juan Gómez" not in response.text


# ============================================================================
# POST — validación (nunca la misma superficie que otro estado)
# ============================================================================

def test_edit_validation_error_shows_feedback_error_and_preserves_values():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "", "last_name": "Gómez", "location": "Rosario", "expected_revision": "1"},
    )
    assert response.status_code == 200
    assert 'class="feedback-error"' in response.text
    assert 'value="Rosario"' in response.text


def test_edit_validation_error_does_not_change_the_stored_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "", "last_name": "Gómez", "expected_revision": "1"},
    )
    current = profile_repository.get(saved["profile_id"])
    assert current["revision"] == 1
    assert current["first_name"] == "Juan"


# ============================================================================
# POST — concurrent_modification (Phase 3.5 §16)
# ============================================================================

def test_stale_expected_revision_shows_concurrent_modification_state():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    # "user 2" edita primero -> revision pasa a 2.
    client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Uno", "expected_revision": "1"},
    )
    # "user 1" todavía tenía el form abierto con expected_revision=1.
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Dos", "expected_revision": "1"},
    )
    assert response.status_code == 200
    assert 'data-advisory-kind="concurrent_modification"' in response.text
    assert 'dashboard-empty__label">No disponible' not in response.text
    assert 'class="feedback-error"' not in response.text


def test_concurrent_modification_does_not_overwrite_the_winning_edit():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Uno", "expected_revision": "1"},
    )
    client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Dos", "expected_revision": "1"},
    )
    current = profile_repository.get(saved["profile_id"])
    assert current["last_name"] == "Uno"
    assert current["revision"] == 2


# ============================================================================
# POST — duplicate advisory en edición (Phase 3.5 §17)
# ============================================================================

def test_edit_that_would_create_a_duplicate_shows_advisory():
    client = _client_as(_AUTHORIZED_EMAIL)
    other = _create(client, first_name="Otro", last_name="Perfil", email="otro@example.com")
    saved = _create(client, first_name="Juan", last_name="Gómez")
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={
            "first_name": "Juan", "last_name": "Gómez", "email": "otro@example.com",
            "expected_revision": "1",
        },
    )
    assert response.status_code == 200
    assert 'data-advisory-kind="possible_duplicate"' in response.text
    assert "Otro Perfil" in response.text
    assert other["profile_id"] != saved["profile_id"]


def test_edit_does_not_flag_itself_as_duplicate():
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, first_name="Juan", last_name="Gómez", email="juan@example.com")
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={
            "first_name": "Juan", "last_name": "Gómez", "email": "juan@example.com",
            "location": "Rosario", "expected_revision": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303  # éxito, nunca un advisory de sí mismo


def test_edit_duplicate_confirmed_saves_anyway():
    client = _client_as(_AUTHORIZED_EMAIL)
    _create(client, first_name="Otro", last_name="Perfil", email="otro@example.com")
    saved = _create(client, first_name="Juan", last_name="Gómez")
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={
            "first_name": "Juan", "last_name": "Gómez", "email": "otro@example.com",
            "expected_revision": "1", "duplicate_confirmed": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert profile_repository.get(saved["profile_id"])["email"] == "otro@example.com"


# ============================================================================
# POST — source failure (A permanece visible, Phase 3.5 §21)
# ============================================================================

def test_source_failure_keeps_a_visible_and_shows_no_disponible(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, first_name="Juan", last_name="Gómez")
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return calls["n"] > 1  # B falla, la compensación A confirma

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Ruiz", "expected_revision": "1"},
    )
    assert response.status_code == 200
    assert 'dashboard-empty__label">No disponible' in response.text
    current = profile_repository.get(saved["profile_id"])
    assert current["first_name"] == "Juan"
    assert current["last_name"] == "Gómez"  # A sigue visible, nunca "Ruiz"


def test_config_incomplete_edit_shows_distinct_error_kind(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client)
    # El default de la fixture (`push_path` -> True) simula un entorno
    # donde el respaldo SIEMPRE confirma -- fuerza acá el fallo real,
    # con `enabled()` en False (ya el default), para ejercitar CONFIG_INCOMPLETE.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Ruiz", "expected_revision": "1"},
    )
    assert response.status_code == 200
    assert 'data-error-kind="config_incomplete"' in response.text


# ============================================================================
# POST — remote_outcome_unknown (Phase 3.5 §10/§11, nunca aplanado)
# ============================================================================

def test_remote_outcome_unknown_shows_its_own_distinct_state(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    saved = _create(client, first_name="Juan", last_name="Gómez")
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    response = client.post(
        f"/cchh/candidatos/{saved['profile_id']}/editar",
        data={"first_name": "Juan", "last_name": "Ruiz", "expected_revision": "1"},
    )
    assert response.status_code == 200
    assert 'data-error-kind="remote_outcome_unknown"' in response.text
    assert 'dashboard-empty__label">No disponible' not in response.text
    current = profile_repository.get(saved["profile_id"])
    assert current["first_name"] == "Juan"
    assert current["last_name"] == "Gómez"  # local se mantuvo en A, nunca "Ruiz"


# ============================================================================
# Scope guards (Phase 3.5 §26) — nada de delete/CV/SharePoint/DB en esta ruta
# ============================================================================

def test_edit_surface_has_no_cv_sharepoint_or_db():
    from pathlib import Path
    router_py = (Path(__file__).resolve().parents[2] / "webapp" / "routers" / "human_capital.py").read_text(encoding="utf-8")
    form_html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "partials" / "human_capital_profile_edit_form.html").read_text(encoding="utf-8")
    for surface in (router_py, form_html):
        assert "cv_ref" not in surface
        assert "from core.microsoft365" not in surface
        assert "core.sharepoint_cases" not in surface


def test_update_repository_never_calls_delete_path():
    # Phase 3.5 §4: "Absolutely no delete_path() as compensation for
    # update" -- chequea el sitio de LLAMADA real (`remote_store.
    # delete_path(`), no la prosa del docstring que legítimamente
    # menciona "delete_path()" al explicar por qué nunca se usa acá
    # (MISMO criterio ya establecido para otros scope-guard tests de
    # este proyecto -- ver test_human_capital_hub_foundation.py).
    from pathlib import Path
    repo_py = (Path(__file__).resolve().parents[2] / "webapp" / "services" / "profile_repository.py").read_text(encoding="utf-8")
    update_body = repo_py.split("\ndef update(", 1)[1]
    assert "remote_store.delete_path(" not in update_body