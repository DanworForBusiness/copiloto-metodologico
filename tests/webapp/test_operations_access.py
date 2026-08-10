"""
test_operations_access.py — Operations Workspace Access Gate: contrato del
predicado de autorización (`webapp.operations_access.is_operations_authorized_user`
+ `auth_service.is_operations_user`), sin HTTP — ver test_staffing_routes.py
para el enforcement server-side de las rutas canónicas, y test_workspaces.py
para el descubrimiento (`available_workspaces`).

Dos condiciones AMBAS requeridas, deliberadamente ORTOGONALES:
1. Admisión BASE (`auth_service.is_authorized`, el tier full/academy de
   siempre — NO se modifica).
2. Allowlist explícita `OPERATIONS_ALLOWED_USERS` (`auth_service.is_operations_user`,
   MISMA normalización que ALLOWED_USERS — reusa `auth_service._csv_users`,
   nunca un segundo parser de identidad).
"""
from __future__ import annotations

from webapp import operations_access
from webapp.services import auth_service


def _user(email: str) -> dict:
    return {"sub": email}


# --- Normalización de username — MISMA que ALLOWED_USERS (reusada) --------

def test_compact_username_config_matches_authenticated_email(monkeypatch):
    # El input real SIEMPRE es el email completo de la sesión autenticada
    # (viene de Microsoft — user_from_claims exige "@", nunca un username
    # suelto). El contrato de "username compacto" es del lado de la
    # CONFIGURACIÓN: OPERATIONS_ALLOWED_USERS=joconnor (sin "@") debe
    # autorizar al mismo usuario que joconnor@danwor.com — exactamente
    # como ya funciona ALLOWED_USERS (auth_service._csv_users: sin "@" ->
    # se le agrega @AUTH_DEFAULT_DOMAIN al entry de la allowlist).
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "joconnor")

    assert auth_service.is_operations_user("joconnor@danwor.com") is True


def test_full_email_config_value_is_also_accepted(monkeypatch):
    # El mismo contrato acepta también la forma completa en la config, sin
    # requerir repetir @danwor.com sea obligatorio.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "joconnor@danwor.com")

    assert auth_service.is_operations_user("joconnor@danwor.com") is True


def test_compact_and_full_email_config_values_refer_to_the_same_user(monkeypatch):
    # "joconnor" y "joconnor@danwor.com" como valor de configuración deben
    # autorizar exactamente al mismo usuario autenticado — no dos
    # identidades distintas.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "joconnor")
    compact_result = auth_service.is_operations_user("joconnor@danwor.com")

    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "joconnor@danwor.com")
    full_result = auth_service.is_operations_user("joconnor@danwor.com")

    assert compact_result is True
    assert compact_result == full_result


def test_case_insensitive_like_allowed_users(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "JoConnor")

    assert auth_service.is_operations_user("joconnor@danwor.com") is True
    assert auth_service.is_operations_user("JOCONNOR@DANWOR.COM") is True


def test_multiple_users_same_separator_convention_as_allowed_users(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "joconnor, otro_usuario")

    assert auth_service.is_operations_user("joconnor@danwor.com") is True
    assert auth_service.is_operations_user("otro_usuario@danwor.com") is True
    assert auth_service.is_operations_user("nadie@danwor.com") is False


def test_no_second_identity_normalization_algorithm():
    # Reuso real, no una reimplementación paralela — `_operations_set` debe
    # llamar al MISMO `_csv_users` que ya usan `_allowed_set`/`_academy_set`.
    import inspect
    source = inspect.getsource(auth_service._operations_set)
    assert "_csv_users(" in source


# --- Fail-closed ------------------------------------------------------------

def test_missing_operations_allowed_users_denies_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    assert auth_service.is_operations_user("joconnor@danwor.com") is False
    assert auth_service.is_operations_user("anyone@danwor.com") is False


def test_blank_operations_allowed_users_denies_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "   ")

    assert auth_service.is_operations_user("joconnor@danwor.com") is False


def test_fail_closed_never_falls_back_to_allowed_users_or_academy_users(monkeypatch):
    # Alguien admitido full/academy en la app, pero SIN OPERATIONS_ALLOWED_USERS
    # configurado, sigue sin acceso a Operations — nunca hereda ALLOWED_USERS.
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    user = _user("anyone@danwor.com")
    assert auth_service.is_authorized("anyone@danwor.com") is True  # base: admitido
    assert operations_access.is_operations_authorized_user(user) is False  # Operations: NO


# --- APP_OWNER — sin bypass implícito --------------------------------------

def test_app_owner_not_explicitly_listed_has_no_operations_bypass(monkeypatch):
    import config
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    owner_email = f"{config.APP_OWNER}@danwor.com"
    assert operations_access.is_operations_authorized_user(_user(owner_email)) is False


def test_app_owner_gains_access_only_if_explicitly_listed(monkeypatch):
    import config
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", config.APP_OWNER)

    owner_email = f"{config.APP_OWNER}@danwor.com"
    assert operations_access.is_operations_authorized_user(_user(owner_email)) is True


# --- Ortogonalidad al tier de Copiloto --------------------------------------

def test_academy_tier_explicitly_listed_is_authorized_for_operations(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "academy-user")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "academy-user")

    assert auth_service.user_tier("academy-user@danwor.com") == "academy"
    assert operations_access.is_operations_authorized_user(_user("academy-user@danwor.com")) is True


def test_full_tier_not_listed_is_denied_despite_full_copilot_access(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    assert auth_service.user_tier("full-user@danwor.com") == "full"
    assert operations_access.is_operations_authorized_user(_user("full-user@danwor.com")) is False


# --- Item I: allowlist de Operations no otorga admisión base ---------------

def test_operations_listing_alone_never_grants_base_admission(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "not-admitted")

    assert auth_service.is_authorized("not-admitted@danwor.com") is False
    assert operations_access.is_operations_authorized_user(_user("not-admitted@danwor.com")) is False


def test_no_user_is_never_authorized():
    assert operations_access.is_operations_authorized_user(None) is False
