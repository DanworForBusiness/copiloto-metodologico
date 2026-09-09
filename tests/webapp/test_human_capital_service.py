"""
test_human_capital_service.py — Capital Humano Phase 3 (First Profile
Slice) + Phase 3.2 (Profile Search, Filters & Duplicate Advisory):
contrato PROPIO de `webapp.services.human_capital_service` — SOLO lo que
este service decide por sí mismo (delegación al repository + validación/
shaping de negocio + búsqueda/filtros/duplicados, Phase 3.2). NO duplica
los tests de `profile_repository` (persistencia, push_path, restore
coordination — ver test_profile_repository.py, ya cubiertos ahí)."""
from __future__ import annotations

import pytest

from webapp.services import human_capital_service, profile_repository
from webapp.services.human_capital_service import ProfileDuplicateAdvisory


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


# --- Delegación al repository -----------------------------------------------

def test_list_profiles_delegates_to_repository():
    human_capital_service.create_profile({"first_name": "A", "last_name": "B"})
    result = human_capital_service.list_profiles()
    assert len(result) == 1
    assert result[0]["first_name"] == "A"


def test_create_profile_delegates_to_repository_and_returns_the_saved_profile():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    assert saved["profile_id"]
    assert saved["first_name"] == "Juan"


# --- Validación de negocio (vive en el service, no en el repository) -------

def test_missing_first_name_raises_value_error():
    with pytest.raises(ValueError):
        human_capital_service.create_profile({"first_name": "", "last_name": "Gómez"})


def test_missing_last_name_raises_value_error():
    with pytest.raises(ValueError):
        human_capital_service.create_profile({"first_name": "Juan", "last_name": ""})


def test_whitespace_only_names_are_treated_as_missing():
    with pytest.raises(ValueError):
        human_capital_service.create_profile({"first_name": "   ", "last_name": "Gómez"})


def test_names_are_stripped_before_persisting():
    saved = human_capital_service.create_profile({"first_name": "  Juan  ", "last_name": "  Gómez  "})
    assert saved["first_name"] == "Juan"
    assert saved["last_name"] == "Gómez"


def test_email_is_optional_and_not_used_as_identity():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    assert saved["email"] is None
    assert saved["profile_id"]  # la identidad es el UUID, nunca el email


# --- source: mismo enum consistente en validación/persistencia (nunca el
# mismatch legacy UI-acepta/DB-rechaza "Flyer") -----------------------------

@pytest.mark.parametrize("source", human_capital_service.VALID_SOURCES)
def test_every_valid_source_is_accepted_including_flyer(source):
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B", "source": source})
    assert saved["source"] == source


def test_flyer_specifically_is_accepted_end_to_end():
    # Legacy Phase 2 §5/§6: "Flyer" era aceptado por la UI/tipo pero
    # rechazado por el CHECK de la base — este test prueba explícitamente
    # que acá NO se reproduce ese mismatch.
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B", "source": "Flyer"})
    assert saved["source"] == "Flyer"


def test_invalid_source_raises_value_error():
    with pytest.raises(ValueError):
        human_capital_service.create_profile({"first_name": "A", "last_name": "B", "source": "Portal de empleo"})


def test_empty_source_is_valid_and_stored_as_none():
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B", "source": ""})
    assert saved["source"] is None


# --- potential_rating: rango 1-5 (mismo CHECK del legacy) -------------------

@pytest.mark.parametrize("rating", ["1", "3", "5"])
def test_valid_potential_rating_is_accepted(rating):
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B", "potential_rating": rating})
    assert saved["potential_rating"] == int(rating)


@pytest.mark.parametrize("rating", ["0", "6", "-1"])
def test_out_of_range_potential_rating_raises_value_error(rating):
    with pytest.raises(ValueError):
        human_capital_service.create_profile({"first_name": "A", "last_name": "B", "potential_rating": rating})


def test_non_numeric_potential_rating_raises_value_error():
    with pytest.raises(ValueError):
        human_capital_service.create_profile({"first_name": "A", "last_name": "B", "potential_rating": "cinco"})


def test_empty_potential_rating_is_valid_and_stored_as_none():
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B", "potential_rating": ""})
    assert saved["potential_rating"] is None


# ============================================================================
# Phase 3.2 §19 — búsqueda textual (`filter_profiles`, `q`)
# ============================================================================

def _make(**overrides) -> dict:
    base = {
        "first_name": "Ana", "last_name": "Pérez", "email": None, "phone": None,
        "linkedin_url": None, "location": None, "profile_skills": None,
        "seniority": None, "source": None, "tags": None, "notes": None,
        "potential_rating": None, "potential_mark": None,
    }
    base.update(overrides)
    return base


def test_search_is_case_insensitive():
    profiles = [_make(first_name="Juan", last_name="Gómez")]
    result = human_capital_service.filter_profiles(profiles, {"q": "JUAN"})
    assert result == profiles


def test_search_trims_and_collapses_whitespace():
    profiles = [_make(first_name="Juan", last_name="Gómez")]
    result = human_capital_service.filter_profiles(profiles, {"q": "  juan  "})
    assert result == profiles


def test_search_matches_first_and_last_name_together():
    profiles = [_make(first_name="Juan", last_name="Gómez")]
    result = human_capital_service.filter_profiles(profiles, {"q": "juan gómez"})
    assert result == profiles


def test_search_matches_email():
    profiles = [_make(email="juan@example.com")]
    assert human_capital_service.filter_profiles(profiles, {"q": "juan@example.com"}) == profiles


def test_search_matches_profile_skills():
    profiles = [_make(profile_skills="SAP BI, Power BI")]
    assert human_capital_service.filter_profiles(profiles, {"q": "power bi"}) == profiles


def test_search_matches_tags():
    profiles = [_make(tags="urgente, senior-sap")]
    assert human_capital_service.filter_profiles(profiles, {"q": "senior-sap"}) == profiles


def test_search_matches_location():
    profiles = [_make(location="Buenos Aires")]
    assert human_capital_service.filter_profiles(profiles, {"q": "buenos aires"}) == profiles


def test_search_matches_notes():
    profiles = [_make(notes="Referido por RRHH interno")]
    assert human_capital_service.filter_profiles(profiles, {"q": "rrhh interno"}) == profiles


def test_search_linkedin_url_is_not_searched():
    # Phase 3.2 §3: decisión explícita de NO incluir linkedin_url en la
    # búsqueda textual (slug de URL opaco, sin señal propia sobre lo que
    # ya cubren first_name/last_name).
    profiles = [_make(linkedin_url="https://linkedin.com/in/juan-perez-83a2")]
    assert human_capital_service.filter_profiles(profiles, {"q": "83a2"}) == []


def test_search_no_match_returns_empty_list():
    profiles = [_make(first_name="Juan", last_name="Gómez")]
    assert human_capital_service.filter_profiles(profiles, {"q": "nadie-coincide"}) == []


def test_search_multiple_matches_returns_all():
    profiles = [_make(first_name="Juan", last_name="A"), _make(first_name="Juana", last_name="B")]
    result = human_capital_service.filter_profiles(profiles, {"q": "juan"})
    assert len(result) == 2


def test_search_combined_with_structured_filter_is_and():
    profiles = [
        _make(first_name="Juan", last_name="Gómez", seniority="Senior"),
        _make(first_name="Juan", last_name="Ruiz", seniority="Junior"),
    ]
    result = human_capital_service.filter_profiles(profiles, {"q": "juan", "seniority": "Senior"})
    assert len(result) == 1
    assert result[0]["last_name"] == "Gómez"


def test_clear_filters_is_an_empty_or_absent_query():
    profiles = [_make(first_name="Juan"), _make(first_name="Ana")]
    assert human_capital_service.filter_profiles(profiles, None) == profiles
    empty_query = {"q": "", "seniority": "", "source": "", "location": "", "potential_mark": ""}
    assert human_capital_service.filter_profiles(profiles, empty_query) == profiles


# ============================================================================
# Phase 3.2 §20 — filtros estructurados
# ============================================================================

def test_filter_by_seniority_exact_match():
    profiles = [_make(first_name="A", seniority="Senior"), _make(first_name="B", seniority="Junior")]
    result = human_capital_service.filter_profiles(profiles, {"seniority": "Senior"})
    assert [p["first_name"] for p in result] == ["A"]


def test_filter_by_seniority_is_case_insensitive():
    profiles = [_make(first_name="A", seniority="Senior")]
    assert human_capital_service.filter_profiles(profiles, {"seniority": "senior"}) == profiles


def test_filter_by_source_exact_match():
    profiles = [_make(first_name="A", source="LinkedIn"), _make(first_name="B", source="Referido")]
    result = human_capital_service.filter_profiles(profiles, {"source": "LinkedIn"})
    assert [p["first_name"] for p in result] == ["A"]


def test_filter_by_location_is_contains_not_exact():
    # Phase 3.2 §5, decisión documentada: location es CONTAINS -- texto
    # libre real, sin taxonomía fija ni en el legacy ni acá.
    profiles = [_make(first_name="A", location="Buenos Aires, Argentina")]
    assert human_capital_service.filter_profiles(profiles, {"location": "buenos aires"}) == profiles


def test_filter_by_potential_mark_exact_match():
    profiles = [_make(first_name="A", potential_mark="Alto"), _make(first_name="B", potential_mark="Medio")]
    result = human_capital_service.filter_profiles(profiles, {"potential_mark": "Alto"})
    assert [p["first_name"] for p in result] == ["A"]


def test_multiple_structured_filters_use_and_semantics():
    profiles = [
        _make(first_name="A", seniority="Senior", source="LinkedIn"),
        _make(first_name="B", seniority="Senior", source="Referido"),
    ]
    result = human_capital_service.filter_profiles(profiles, {"seniority": "Senior", "source": "LinkedIn"})
    assert [p["first_name"] for p in result] == ["A"]


def test_unknown_filter_value_yields_no_matches_not_an_error():
    profiles = [_make(first_name="A", seniority="Senior")]
    assert human_capital_service.filter_profiles(profiles, {"seniority": "NoExiste"}) == []


# ============================================================================
# Phase 3.2 §21 — duplicate advisory (contrato legacy exacto, §9)
# ============================================================================

def test_same_normalized_email_triggers_advisory():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez", "email": "juan@example.com"})
    with pytest.raises(ProfileDuplicateAdvisory):
        human_capital_service.create_profile({"first_name": "Otro", "last_name": "Nombre", "email": "juan@example.com"})


def test_email_casing_differences_still_trigger_advisory():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez", "email": "Juan@Example.com"})
    with pytest.raises(ProfileDuplicateAdvisory):
        human_capital_service.create_profile({"first_name": "Otro", "last_name": "Nombre", "email": "juan@example.com"})


def test_same_first_and_last_name_with_no_email_triggers_advisory():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    with pytest.raises(ProfileDuplicateAdvisory):
        human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})


def test_same_name_but_different_explicit_emails_does_not_trigger_advisory():
    # Phase 3.2 §9/§21: si el Profile nuevo trae email, el algoritmo SOLO
    # matchea por email -- el nombre nunca participa en esa rama.
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez", "email": "juan.1@example.com"})
    saved = human_capital_service.create_profile(
        {"first_name": "Juan", "last_name": "Pérez", "email": "juan.2@example.com"}
    )
    assert saved["email"] == "juan.2@example.com"


def test_different_person_does_not_trigger_advisory():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    saved = human_capital_service.create_profile({"first_name": "Ana", "last_name": "Gómez"})
    assert saved["first_name"] == "Ana"


def test_advisory_does_not_create_the_profile():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    with pytest.raises(ProfileDuplicateAdvisory):
        human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    assert len(profile_repository.list()) == 1


def test_advisory_exception_carries_the_matched_profile():
    original = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    with pytest.raises(ProfileDuplicateAdvisory) as exc_info:
        human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    assert exc_info.value.matched_profile["profile_id"] == original["profile_id"]


def test_explicit_continue_creates_exactly_one_profile():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    with pytest.raises(ProfileDuplicateAdvisory):
        human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    # "Crear de todos modos" -- duplicate_confirmed=True salta el chequeo.
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"}, duplicate_confirmed=True)
    assert len(profile_repository.list()) == 2


def test_duplicate_confirmed_skips_the_check_entirely():
    # Ni siquiera con datos que matchearían -- confirmar de entrada nunca
    # dispara el advisory.
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    saved = human_capital_service.create_profile(
        {"first_name": "Juan", "last_name": "Pérez"}, duplicate_confirmed=True,
    )
    assert saved["first_name"] == "Juan"


def test_duplicate_advisory_does_not_change_phase_3_1_durability_semantics(monkeypatch):
    # Phase 3.1 sigue intacta: un create() que SÍ llega a intentar
    # persistencia (porque no hay duplicado, o porque ya se confirmó)
    # sigue limpiando el archivo local si push_path falla.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(profile_repository.ProfileStorageError):
        human_capital_service.create_profile({"first_name": "Nueva", "last_name": "Persona"})
    assert profile_repository.list() == []


# ============================================================================
# Phase 3.5 — update_profile (validación compartida, duplicate-on-update
# excluyendo el Profile propio, delegación de concurrencia/rollback)
# ============================================================================

def test_get_profile_delegates_to_repository():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    fetched = human_capital_service.get_profile(saved["profile_id"])
    assert fetched["first_name"] == "Juan"


def test_get_profile_returns_none_for_missing():
    assert human_capital_service.get_profile("does-not-exist") is None


def test_update_profile_runs_the_same_validation_as_create():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    with pytest.raises(ValueError):
        human_capital_service.update_profile(saved["profile_id"], 1, {"first_name": "", "last_name": "Gómez"})


def test_update_profile_rejects_invalid_source():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    with pytest.raises(ValueError):
        human_capital_service.update_profile(
            saved["profile_id"], 1, {"first_name": "Juan", "last_name": "Gómez", "source": "Portal de empleo"},
        )


def test_update_profile_success_increments_revision():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    updated = human_capital_service.update_profile(
        saved["profile_id"], 1, {"first_name": "Juan", "last_name": "Ruiz"},
    )
    assert updated["last_name"] == "Ruiz"
    assert updated["revision"] == 2


def test_update_profile_stale_revision_raises_concurrent_modification():
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    human_capital_service.update_profile(saved["profile_id"], 1, {"first_name": "Juan", "last_name": "Uno"})
    with pytest.raises(profile_repository.ProfileConcurrentModificationError):
        human_capital_service.update_profile(saved["profile_id"], 1, {"first_name": "Juan", "last_name": "Dos"})


def test_update_profile_on_missing_profile_raises_not_found():
    with pytest.raises(profile_repository.ProfileNotFoundError):
        human_capital_service.update_profile("does-not-exist", 1, {"first_name": "A", "last_name": "B"})


def test_update_profile_editing_email_to_match_another_profile_triggers_advisory():
    other = human_capital_service.create_profile(
        {"first_name": "Otro", "last_name": "Perfil", "email": "otro@example.com"},
    )
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    with pytest.raises(human_capital_service.ProfileDuplicateAdvisory) as exc_info:
        human_capital_service.update_profile(
            saved["profile_id"], 1,
            {"first_name": "Juan", "last_name": "Gómez", "email": "otro@example.com"},
        )
    assert exc_info.value.matched_profile["profile_id"] == other["profile_id"]


def test_update_profile_never_flags_itself_as_a_duplicate():
    # Sin excluir el propio profile_id, CUALQUIER edición que no cambie
    # email/nombre se marcaría como "duplicado de sí misma" -- confirma
    # que eso NUNCA pasa.
    saved = human_capital_service.create_profile(
        {"first_name": "Juan", "last_name": "Gómez", "email": "juan@example.com"},
    )
    updated = human_capital_service.update_profile(
        saved["profile_id"], 1,
        {"first_name": "Juan", "last_name": "Gómez", "email": "juan@example.com", "location": "Rosario"},
    )
    assert updated["location"] == "Rosario"


def test_update_profile_duplicate_confirmed_skips_the_check():
    other = human_capital_service.create_profile(
        {"first_name": "Otro", "last_name": "Perfil", "email": "otro@example.com"},
    )
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    updated = human_capital_service.update_profile(
        saved["profile_id"], 1,
        {"first_name": "Juan", "last_name": "Gómez", "email": "otro@example.com"},
        duplicate_confirmed=True,
    )
    assert updated["email"] == "otro@example.com"
    assert other["profile_id"] != updated["profile_id"]


def test_update_profile_propagates_config_incomplete(monkeypatch):
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Gómez"})
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    with pytest.raises(profile_repository.ProfileConfigIncompleteError):
        human_capital_service.update_profile(saved["profile_id"], 1, {"first_name": "Juan", "last_name": "Ruiz"})


# ============================================================================
# Phase 3.6 — create_profile propaga ProfileRemoteOutcomeUnknownError, y un
# create() sin resolver nunca es tratado como Profile comprometido por la
# detección de duplicados de una creación POSTERIOR (§14/§19 del ticket)
# ============================================================================

def test_create_profile_propagates_remote_outcome_unknown(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(profile_repository.ProfileRemoteOutcomeUnknownError):
        human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})


def test_unresolved_create_does_not_trigger_a_duplicate_advisory_on_retry(monkeypatch):
    # Phase 3.6 §14: "user retries -> duplicate advisory sees local
    # ghost" NUNCA pasa, porque el create() sin resolver ya limpió su
    # archivo local antes de propagar la excepción (mismo invariante de
    # Phase 3.1) -- _find_duplicate lee list_profiles(), que NUNCA vio
    # ese intento. El retry con los MISMOS datos (nombre/apellido) debe
    # poder crearse sin ningún advisory de "duplicado de sí mismo".
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(profile_repository.ProfileRemoteOutcomeUnknownError):
        human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})

    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    saved = human_capital_service.create_profile({"first_name": "Juan", "last_name": "Pérez"})
    assert saved["first_name"] == "Juan"
    assert len(profile_repository.list()) == 1
