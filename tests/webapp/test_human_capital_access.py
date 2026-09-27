"""
test_human_capital_access.py — Capital Humano Phase 1 (Hub Walking
Skeleton): contrato del predicado de autorización
(`webapp.human_capital_access.is_human_capital_authorized_user` +
`auth_service.is_human_capital_user`), sin HTTP — ver
test_human_capital_hub_foundation.py para el enforcement server-side de la
ruta canónica, y test_workspaces.py para el descubrimiento
(`available_workspaces`).

MISMO contrato exacto que test_commercial_access.py/test_operations_access.py,
aplicado a Capital Humano — dos condiciones AMBAS requeridas,
deliberadamente ORTOGONALES:
1. Admisión BASE (`auth_service.is_authorized`, el tier full/academy de
   siempre — NO se modifica).
2. Allowlist explícita `HUMAN_CAPITAL_ALLOWED_USERS`
   (`auth_service.is_human_capital_user`, MISMA normalización que
   ALLOWED_USERS — reusa `auth_service._csv_users`, nunca un segundo
   parser de identidad). INDEPENDIENTE de `OPERATIONS_ALLOWED_USERS`/
   `COMMERCIAL_ALLOWED_USERS` — estar autorizado a un workspace no implica
   nada sobre los otros.

Roles legacy (hrbp/hiring_manager) y data scope NO son parte de esta fase
— este archivo cubre EXCLUSIVAMENTE la frontera A (workspace admission).
"""
from __future__ import annotations

from webapp import human_capital_access
from webapp.services import auth_service


def _user(email: str) -> dict:
    return {"sub": email}


# --- Normalización de username — MISMA que ALLOWED_USERS (reusada) --------

def test_compact_username_config_matches_authenticated_email(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "joconnor")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is True


def test_full_email_config_value_is_also_accepted(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "joconnor@danwor.com")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is True


def test_case_insensitive_like_allowed_users(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "JoConnor")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is True
    assert auth_service.is_human_capital_user("JOCONNOR@DANWOR.COM") is True


def test_multiple_users_same_separator_convention_as_allowed_users(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "joconnor, otro_usuario")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is True
    assert auth_service.is_human_capital_user("otro_usuario@danwor.com") is True
    assert auth_service.is_human_capital_user("nadie@danwor.com") is False


def test_no_second_identity_normalization_algorithm():
    # Reuso real, no una reimplementación paralela — `_human_capital_set`
    # debe llamar al MISMO `_csv_users` que ya usan `_allowed_set`/
    # `_academy_set`/`_operations_set`/`_commercial_set`.
    import inspect
    source = inspect.getsource(auth_service._human_capital_set)
    assert "_csv_users(" in source


# --- Fail-closed ------------------------------------------------------------

def test_missing_human_capital_allowed_users_denies_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is False
    assert auth_service.is_human_capital_user("anyone@danwor.com") is False


def test_blank_human_capital_allowed_users_denies_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "   ")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is False


# --- ALL nunca es un wildcard para Capital Humano (misma corrección de
# seguridad que Operations/Comercial) ---------------------------------------

def test_human_capital_allowed_users_all_uppercase_denies_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "ALL")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is False
    assert auth_service.is_human_capital_user("anyone@danwor.com") is False


def test_human_capital_allowed_users_all_lowercase_denies_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "all")

    assert auth_service.is_human_capital_user("joconnor@danwor.com") is False


def test_human_capital_authorized_user_denied_even_when_all_configured(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "ALL")

    assert human_capital_access.is_human_capital_authorized_user(_user("anyone@danwor.com")) is False


def test_fail_closed_never_falls_back_to_allowed_users_or_academy_users(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")

    user = _user("anyone@danwor.com")
    assert auth_service.is_authorized("anyone@danwor.com") is True  # base: admitido
    assert human_capital_access.is_human_capital_authorized_user(user) is False  # Capital Humano: NO


# --- APP_OWNER — superusuario de workspace (owner decision, corrective
#     post-#107: REEMPLAZA la política anterior de esta sección, que
#     probaba lo contrario -- "sin bypass implícito". Ver
#     `webapp/access.py::is_owner_user` y
#     `tests/webapp/test_app_owner_workspace_superuser.py` para el
#     contrato completo/centralizado; acá sólo se confirma que ESTE
#     vertical lo consume correctamente. -----------------------------------

def test_app_owner_has_human_capital_access_even_when_not_explicitly_listed(monkeypatch):
    from webapp import access
    monkeypatch.setattr(access, "APP_OWNER", "owner-hc-test")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")

    owner_email = "owner-hc-test@danwor.com"
    assert human_capital_access.is_human_capital_authorized_user(_user(owner_email)) is True


def test_app_owner_explicitly_listed_is_still_authorized_redundant_with_owner_bypass(monkeypatch):
    # El owner explícitamente listado en HUMAN_CAPITAL_ALLOWED_USERS sigue
    # entrando -- ya no es el ÚNICO camino (ver test de arriba), pero la
    # excepción del owner es aditiva: nunca RESTA autorización que ya existía.
    from webapp import access
    monkeypatch.setattr(access, "APP_OWNER", "owner-hc-test")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "owner-hc-test")

    owner_email = "owner-hc-test@danwor.com"
    assert human_capital_access.is_human_capital_authorized_user(_user(owner_email)) is True


def test_default_config_value_is_empty_string_not_a_hardcoded_user():
    # El default productivo de HUMAN_CAPITAL_ALLOWED_USERS debe ser ""
    # (fail-closed) — nunca un email hardcodeado.
    import config
    assert config.HUMAN_CAPITAL_ALLOWED_USERS == ""


# --- Ortogonalidad al tier de Copiloto Y a Operations/Comercial ------------

def test_academy_tier_explicitly_listed_is_authorized_for_human_capital(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "academy-user")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "academy-user")

    assert auth_service.user_tier("academy-user@danwor.com") == "academy"
    assert human_capital_access.is_human_capital_authorized_user(_user("academy-user@danwor.com")) is True


def test_full_tier_not_listed_is_denied_despite_full_copilot_access(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")

    assert auth_service.user_tier("full-user@danwor.com") == "full"
    assert human_capital_access.is_human_capital_authorized_user(_user("full-user@danwor.com")) is False


def test_operations_listing_alone_never_grants_human_capital_access(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "shared-user")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")

    user = _user("shared-user@danwor.com")
    assert auth_service.is_operations_user("shared-user@danwor.com") is True
    assert human_capital_access.is_human_capital_authorized_user(user) is False


def test_commercial_listing_alone_never_grants_human_capital_access(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "shared-user")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")

    user = _user("shared-user@danwor.com")
    assert auth_service.is_commercial_user("shared-user@danwor.com") is True
    assert human_capital_access.is_human_capital_authorized_user(user) is False


def test_human_capital_listing_alone_never_grants_operations_or_commercial_access(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "shared-user")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")

    assert auth_service.is_human_capital_user("shared-user@danwor.com") is True
    assert auth_service.is_operations_user("shared-user@danwor.com") is False
    assert auth_service.is_commercial_user("shared-user@danwor.com") is False


# --- allowlist de Capital Humano no otorga admisión base -------------------

def test_human_capital_listing_alone_never_grants_base_admission(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "not-admitted")

    assert auth_service.is_authorized("not-admitted@danwor.com") is False
    assert human_capital_access.is_human_capital_authorized_user(_user("not-admitted@danwor.com")) is False


def test_no_user_is_never_authorized():
    assert human_capital_access.is_human_capital_authorized_user(None) is False
