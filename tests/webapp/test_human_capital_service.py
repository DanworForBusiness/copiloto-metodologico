"""
test_human_capital_service.py — Capital Humano Phase 3 (First Profile
Slice): contrato PROPIO de `webapp.services.human_capital_service` —
SOLO lo que este service decide por sí mismo (delegación al repository +
validación/shaping de negocio). NO duplica los tests de
`profile_repository` (persistencia, push_path, restore coordination — ver
test_profile_repository.py, ya cubiertos ahí)."""
from __future__ import annotations

import pytest

from webapp.services import human_capital_service, profile_repository


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
