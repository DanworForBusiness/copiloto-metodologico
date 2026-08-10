"""
test_workspaces.py — Contrato de `webapp.workspaces` (Operations Workspace
Access Gate, post Staffing V1 + Danwor Hub CLOSED/GREEN).

Operations ya NO depende de `auth_service.user_tier` — su predicado
(`operations_access.is_operations_authorized_user`) exige DOS condiciones
independientes: admisión BASE a la app (`auth_service.is_authorized`, el
mismo tier full/academy de siempre) Y membresía explícita en
`OPERATIONS_ALLOWED_USERS`. Por eso las 4 combinaciones full/academy ×
con/sin Operations son TODAS representables (a diferencia del batch
anterior, donde Operations exigía tier=="full" y "academy + Operations"
era imposible por construcción)."""
from __future__ import annotations

from webapp import workspaces
from webapp.services import auth_service


def _user(email: str) -> dict:
    return {"sub": email}


def _admit_full(monkeypatch, email: str) -> None:
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", email)
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "")


def _admit_academy(monkeypatch, email: str) -> None:
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", email)


def test_full_tier_without_operations_listing_sees_only_copilot(monkeypatch):
    _admit_full(monkeypatch, "full-user@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    result = workspaces.available_workspaces(_user("full-user@danwor.com"))

    assert result == [workspaces.COPILOT]


def test_academy_tier_without_operations_listing_sees_only_copilot(monkeypatch):
    _admit_academy(monkeypatch, "academy-user@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    result = workspaces.available_workspaces(_user("academy-user@danwor.com"))

    assert result == [workspaces.COPILOT]


def test_full_tier_with_operations_listing_sees_both_workspaces(monkeypatch):
    _admit_full(monkeypatch, "full-user@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "full-user")

    result = workspaces.available_workspaces(_user("full-user@danwor.com"))

    assert result == [workspaces.COPILOT, workspaces.OPERATIONS]


def test_academy_tier_with_operations_listing_sees_both_workspaces(monkeypatch):
    # Combinación explícitamente NUEVA (ver HANDOFF, Operations Workspace
    # Access Gate): Operations ya NO es "solo tier full" — un usuario
    # Academy legítimamente admitido y listado en OPERATIONS_ALLOWED_USERS
    # entra a Operaciones igual que uno full. El tier de Copiloto nunca es
    # la fuente de esta autorización.
    _admit_academy(monkeypatch, "academy-user@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "academy-user")

    result = workspaces.available_workspaces(_user("academy-user@danwor.com"))

    assert result == [workspaces.COPILOT, workspaces.OPERATIONS]


def test_operations_allowed_users_missing_denies_operations_regardless_of_tier(monkeypatch):
    # "Ausente" y "vacío" llegan idénticos a auth_service (config._env
    # devuelve "" si la env var no está seteada) — se prueba acá el mismo
    # valor "" que representa ambos casos.
    _admit_full(monkeypatch, "full-user@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")

    result = workspaces.available_workspaces(_user("full-user@danwor.com"))

    assert result == [workspaces.COPILOT]


def test_operations_allowed_users_blank_denies_operations(monkeypatch):
    _admit_full(monkeypatch, "full-user@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "   ")

    result = workspaces.available_workspaces(_user("full-user@danwor.com"))

    assert result == [workspaces.COPILOT]


def test_no_session_sees_no_workspaces():
    # user=None representa "sin sesión" — no una combinación de tiers
    # inventada. Bajo la política actual, un usuario CON sesión siempre
    # tiene tier "full" o "academy" (nunca None — ver invariante en
    # auth_service.py), así que "0 workspaces autenticado" no es
    # representable; "sin sesión" sí lo es y es lo que se prueba acá.
    assert workspaces.available_workspaces(None) == []


def test_workspace_metadata_matches_expected_routing():
    assert workspaces.COPILOT.home_url == "/"
    assert workspaces.OPERATIONS.home_url == "/operaciones/staffing/asignaciones"
    assert {ws.key for ws in workspaces.available_workspaces(_user("x@danwor.com"))} <= {"copilot", "operations"}
