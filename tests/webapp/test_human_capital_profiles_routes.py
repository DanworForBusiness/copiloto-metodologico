"""
test_human_capital_profiles_routes.py — Capital Humano Phase 3 (First
Profile Slice): contrato HTTP/UI de `GET/POST /human-capital/profiles`.
MISMO patrón de autorización/aislamiento que
test_human_capital_hub_foundation.py (fixture `autouse` con `monkeypatch`,
nunca una asignación de módulo sin revertir — ver ese archivo para el
razonamiento completo). Sin snapshots completos de HTML (§21)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_ROUTERS = Path(__file__).resolve().parents[2] / "webapp" / "routers"
_LANDING_HTML = (_TEMPLATES / "human_capital" / "landing.html").read_text(encoding="utf-8")
_PROFILES_HTML = (_TEMPLATES / "human_capital" / "profiles.html").read_text(encoding="utf-8")
_PANEL_HTML = (_TEMPLATES / "partials" / "human_capital_profiles_panel.html").read_text(encoding="utf-8")
_HUMAN_CAPITAL_PY = (_ROUTERS / "human_capital.py").read_text(encoding="utf-8")

_AUTHORIZED_EMAIL = "human-capital-profiles-test@danwor.com"


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
        {"email": email, "name": "HC Profiles Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ============================================================================
# Authorization — MISMO contrato ya probado en test_human_capital_access.py/
# test_human_capital_hub_foundation.py, verificado acá para la ruta nueva.
# ============================================================================

def test_get_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/human-capital/profiles", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Fhuman-capital%2Fprofiles"


def test_get_unlisted_user_is_denied_with_403():
    response = _client_as("unlisted@danwor.com").get("/human-capital/profiles")
    assert response.status_code == 403


def test_post_unlisted_user_is_denied_with_403():
    response = _client_as("unlisted@danwor.com").post(
        "/human-capital/profiles", data={"first_name": "A", "last_name": "B"}
    )
    assert response.status_code == 403


def test_get_authorized_returns_200():
    response = _client_as(_AUTHORIZED_EMAIL).get("/human-capital/profiles")
    assert response.status_code == 200


# ============================================================================
# GET — empty / listado
# ============================================================================

def test_get_with_zero_profiles_shows_sin_resultados():
    response = _client_as(_AUTHORIZED_EMAIL).get("/human-capital/profiles")
    assert 'dashboard-empty__label">Sin resultados' in response.text


def test_created_profile_is_rendered_in_the_list():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Ana", "last_name": "Pérez"})
    response = client.get("/human-capital/profiles")
    assert "Ana Pérez" in response.text
    assert 'dashboard-empty__label">Sin resultados' not in response.text


def test_list_shows_the_minimum_required_columns():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={
        "first_name": "Ana", "last_name": "Pérez", "profile_skills": "SAP BI",
        "seniority": "Senior", "location": "Buenos Aires", "source": "LinkedIn",
    })
    text = client.get("/human-capital/profiles").text
    assert "SAP BI" in text
    assert "Senior" in text
    assert "Buenos Aires" in text
    assert "LinkedIn" in text


# ============================================================================
# POST — alta válida
# ============================================================================

def test_valid_post_persists_and_returns_the_updated_panel():
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "Carla", "last_name": "Ruiz"}
    )
    assert response.status_code == 200
    assert "Carla Ruiz" in response.text
    assert 'id="hc-profiles-panel"' in response.text


def test_valid_post_clears_the_form_on_success():
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "Carla", "last_name": "Ruiz"}
    )
    # El <input name="first_name"> del form vuelve a estar vacío tras un
    # alta exitosa -- no debe seguir mostrando "Carla" como value del campo
    # (solo debe aparecer como fila de la tabla).
    assert 'name="first_name" type="text" value=""' in response.text


# ============================================================================
# POST — validación (nunca la misma superficie que un fallo de persistencia)
# ============================================================================

def test_post_missing_required_fields_shows_validation_error_not_source_error():
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "", "last_name": ""}
    )
    assert response.status_code == 200
    assert 'class="feedback-error"' in response.text
    assert 'dashboard-empty__label">No disponible' not in response.text


def test_post_invalid_source_shows_validation_error():
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles",
        data={"first_name": "A", "last_name": "B", "source": "Portal de empleo"},
    )
    assert 'class="feedback-error"' in response.text


def test_post_validation_failure_preserves_typed_values():
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "", "last_name": "Gómez", "location": "Rosario"}
    )
    assert 'value="Rosario"' in response.text


def test_post_validation_failure_does_not_persist_anything():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "", "last_name": "Gómez"})
    assert profile_repository.list() == []


# ============================================================================
# POST — fallo de persistencia (source-error, nunca "Sin resultados")
# ============================================================================

def test_post_push_path_failure_shows_source_error_not_validation_error(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "A", "last_name": "B"}
    )
    assert response.status_code == 200
    assert 'dashboard-empty__label">No disponible' in response.text
    assert 'class="feedback-error"' not in response.text


def test_post_push_path_failure_preserves_typed_values(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "A", "last_name": "B", "location": "Córdoba"}
    )
    assert 'value="Córdoba"' in response.text


def test_get_list_failure_shows_source_error_never_sin_resultados(monkeypatch):
    def _boom():
        raise profile_repository.ProfileStorageError("boom")
    monkeypatch.setattr(profile_repository, "list", _boom)
    response = _client_as(_AUTHORIZED_EMAIL).get("/human-capital/profiles")
    assert response.status_code == 200
    assert 'dashboard-empty__label">No disponible' in response.text
    assert 'dashboard-empty__label">Sin resultados' not in response.text


# ============================================================================
# Phase 3.1 — clasificación explícita CONFIG_INCOMPLETE vs SOURCE_ERROR,
# y la garantía de que un create() fallido nunca reaparece (root cause del
# bug de data correctness de Phase 3).
# ============================================================================

def test_spaces_disabled_post_is_classified_as_config_incomplete(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "A", "last_name": "B"}
    )
    assert response.status_code == 200
    assert 'data-error-kind="config_incomplete"' in response.text


def test_enabled_but_push_failure_after_retry_post_is_classified_as_remote_outcome_unknown(monkeypatch):
    # Phase 3.6 reclasificación (ver profile_repository.create()): un push
    # que falla dos veces seguidas (inicial + reintento explícito) con
    # Spaces habilitado ya NO se muestra como "source_error" -- no
    # podemos probar ausencia remota, así que la UI debe reflejar
    # honestamente "Estado desconocido", nunca "No disponible".
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "A", "last_name": "B"}
    )
    assert response.status_code == 200
    assert 'data-error-kind="remote_outcome_unknown"' in response.text
    assert 'data-error-kind="source_error"' not in response.text


def test_remote_outcome_unknown_never_reuses_no_disponible_text(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    response = _client_as(_AUTHORIZED_EMAIL).post(
        "/human-capital/profiles", data={"first_name": "A", "last_name": "B"}
    )
    assert 'dashboard-empty__label">No disponible' not in response.text
    assert 'dashboard-empty__label">Estado desconocido' in response.text


def test_unresolved_create_never_reappears_on_a_subsequent_get(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Fantasma", "last_name": "Dos"})
    response = client.get("/human-capital/profiles")
    assert "Fantasma Dos" not in response.text
    assert 'dashboard-empty__label">Sin resultados' in response.text


def test_failed_create_never_reappears_on_a_subsequent_get(monkeypatch):
    # El fixture `_authorized_and_isolated` deja `push_path` en éxito por
    # default -- forzamos el fallo explícitamente para este POST.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Fantasma", "last_name": "Uno"})
    response = client.get("/human-capital/profiles")
    assert "Fantasma Uno" not in response.text
    assert 'dashboard-empty__label">Sin resultados' in response.text


def test_failed_create_then_retry_produces_exactly_one_profile(monkeypatch):
    client = _client_as(_AUTHORIZED_EMAIL)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    client.post("/human-capital/profiles", data={"first_name": "Reintento", "last_name": "Uno"})

    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    client.post("/human-capital/profiles", data={"first_name": "Reintento", "last_name": "Uno"})

    assert len(profile_repository.list()) == 1


# ============================================================================
# Scope guards (Phase 3 §25) — nada de CV/SharePoint/roles en esta ruta
# ============================================================================

def test_profiles_surface_has_no_cv_or_sharepoint_import_or_field():
    # Chequea IMPORTS/campos reales, no prosa de comentario que
    # legítimamente menciona "SharePoint"/"CV" al explicar qué queda fuera
    # de esta fase (MISMO criterio ya establecido en
    # test_human_capital_hub_foundation.py — ver ese archivo).
    for surface in (_HUMAN_CAPITAL_PY, _PANEL_HTML, _PROFILES_HTML):
        assert "cv_ref" not in surface
        assert "import microsoft365" not in surface
        assert "from core.microsoft365" not in surface
        assert "core.sharepoint_cases" not in surface


def test_profiles_surface_has_no_legacy_role_literal():
    for surface in (_HUMAN_CAPITAL_PY, _PANEL_HTML, _PROFILES_HTML):
        assert '"hrbp"' not in surface and "'hrbp'" not in surface
        assert '"hiring_manager"' not in surface and "'hiring_manager'" not in surface


def test_profiles_templates_add_no_own_css_or_js():
    for surface in (_PROFILES_HTML, _PANEL_HTML):
        assert "<link rel=\"stylesheet\"" not in surface
        assert "<script src=" not in surface


def test_landing_now_links_to_profiles():
    assert "/human-capital/profiles" in _LANDING_HTML


# ============================================================================
# Copiloto/Operations/Commercial intactos
# ============================================================================

def test_operaciones_and_comercial_namespaces_unaffected():
    response = TestClient(app).get("/comercial", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Fcomercial"


# ============================================================================
# Phase 3.2 §19 — búsqueda textual vía GET ?q=
# ============================================================================

def test_search_q_filters_the_list():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Gómez"})
    client.post("/human-capital/profiles", data={"first_name": "Ana", "last_name": "Ruiz"})
    response = client.get("/human-capital/profiles", params={"q": "juan"})
    assert "Juan Gómez" in response.text
    assert "Ana Ruiz" not in response.text


def test_search_q_no_match_shows_sin_resultados_never_no_disponible():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Gómez"})
    response = client.get("/human-capital/profiles", params={"q": "nadie-coincide"})
    assert 'data-empty-kind="no_matches"' in response.text
    assert 'dashboard-empty__label">No disponible' not in response.text


def test_zero_profiles_dataset_shows_dataset_empty_kind_not_no_matches():
    response = _client_as(_AUTHORIZED_EMAIL).get("/human-capital/profiles", params={"q": "cualquier-cosa"})
    assert 'data-empty-kind="dataset_empty"' in response.text


def test_combined_q_and_structured_filter():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Gómez", "seniority": "Senior"})
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Ruiz", "seniority": "Junior"})
    response = client.get("/human-capital/profiles", params={"q": "juan", "seniority": "Senior"})
    assert "Juan Gómez" in response.text
    assert "Juan Ruiz" not in response.text


def test_clear_filters_link_present_only_when_a_filter_is_active():
    client = _client_as(_AUTHORIZED_EMAIL)
    plain = client.get("/human-capital/profiles")
    assert "Limpiar filtros" not in plain.text
    filtered = client.get("/human-capital/profiles", params={"q": "juan"})
    assert "Limpiar filtros" in filtered.text


# ============================================================================
# Phase 3.2 §20 — filtros estructurados vía GET
# ============================================================================

def test_filter_by_seniority():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Uno", "last_name": "A", "seniority": "Senior"})
    client.post("/human-capital/profiles", data={"first_name": "Dos", "last_name": "B", "seniority": "Junior"})
    response = client.get("/human-capital/profiles", params={"seniority": "Senior"})
    assert "Uno A" in response.text
    assert "Dos B" not in response.text


def test_filter_by_source():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Uno", "last_name": "A", "source": "LinkedIn"})
    client.post("/human-capital/profiles", data={"first_name": "Dos", "last_name": "B", "source": "Referido"})
    response = client.get("/human-capital/profiles", params={"source": "LinkedIn"})
    assert "Uno A" in response.text
    assert "Dos B" not in response.text


def test_filter_by_location_contains():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Uno", "last_name": "A", "location": "Buenos Aires, Argentina"})
    response = client.get("/human-capital/profiles", params={"location": "buenos aires"})
    assert "Uno A" in response.text


def test_filter_by_potential_mark():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Uno", "last_name": "A", "potential_mark": "Alto"})
    client.post("/human-capital/profiles", data={"first_name": "Dos", "last_name": "B", "potential_mark": "Medio"})
    response = client.get("/human-capital/profiles", params={"potential_mark": "Alto"})
    assert "Uno A" in response.text
    assert "Dos B" not in response.text


def test_multiple_filters_use_and_semantics():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={
        "first_name": "Uno", "last_name": "A", "seniority": "Senior", "source": "LinkedIn",
    })
    client.post("/human-capital/profiles", data={
        "first_name": "Dos", "last_name": "B", "seniority": "Senior", "source": "Referido",
    })
    response = client.get("/human-capital/profiles", params={"seniority": "Senior", "source": "LinkedIn"})
    assert "Uno A" in response.text
    assert "Dos B" not in response.text


def test_unknown_filter_value_shows_no_matches_not_an_error():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Uno", "last_name": "A", "seniority": "Senior"})
    response = client.get("/human-capital/profiles", params={"seniority": "NoExiste"})
    assert response.status_code == 200
    assert 'data-empty-kind="no_matches"' in response.text


# ============================================================================
# Phase 3.2 §21/§22 — duplicate advisory end-to-end
# ============================================================================

def test_duplicate_advisory_is_shown_on_matching_email():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez", "email": "juan@example.com"})
    response = client.post(
        "/human-capital/profiles", data={"first_name": "Otro", "last_name": "Nombre", "email": "juan@example.com"}
    )
    assert response.status_code == 200
    assert 'data-advisory-kind="possible_duplicate"' in response.text
    assert "Juan Pérez" in response.text


def test_duplicate_advisory_never_reuses_no_disponible_surface():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})
    response = client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})
    assert 'dashboard-empty__label">No disponible' not in response.text
    assert 'class="feedback-error"' not in response.text


def test_duplicate_advisory_does_not_create_the_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})
    assert len(profile_repository.list()) == 1


def test_duplicate_confirmed_creates_exactly_one_additional_profile():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})  # advisory, no crea
    response = client.post(
        "/human-capital/profiles",
        data={"first_name": "Juan", "last_name": "Pérez", "duplicate_confirmed": "true"},
    )
    assert response.status_code == 200
    assert len(profile_repository.list()) == 2


def test_different_person_does_not_trigger_advisory_over_http():
    client = _client_as(_AUTHORIZED_EMAIL)
    client.post("/human-capital/profiles", data={"first_name": "Juan", "last_name": "Pérez"})
    response = client.post("/human-capital/profiles", data={"first_name": "Ana", "last_name": "Gómez"})
    assert 'data-advisory-kind="possible_duplicate"' not in response.text
    assert "Ana Gómez" in response.text
