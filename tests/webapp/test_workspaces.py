"""
test_workspaces.py — Contrato de `webapp.workspaces` (batch de transición
multi-workspace, post primer hito real de Staffing).

Combinaciones testeadas: solo las REALMENTE representables bajo la política
actual (`auth_service.user_tier` ∈ {"full", "academy", None}, con el
invariante de que ninguna sesión real tiene tier None — ver
`auth_service.py`). "Operations-only" NO es representable: la
disponibilidad de Copiloto (`user is not None`) es un superconjunto de la
de Operations (tier=="full") — cualquier usuario con Operations también
tiene Copiloto. Este test NO inventa un tier falso para simular esa
combinación imposible; se documenta acá en vez de fabricarla.
"""
from __future__ import annotations

from webapp import workspaces


def _user(tier_marker: str) -> dict:
    return {"sub": f"{tier_marker}@danwor.com"}


def test_academy_tier_user_sees_only_copilot(monkeypatch):
    from webapp.services import auth_service
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")

    result = workspaces.available_workspaces(_user("academy-user"))

    assert result == [workspaces.COPILOT]


def test_full_tier_user_sees_both_workspaces(monkeypatch):
    from webapp.services import auth_service
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "")

    result = workspaces.available_workspaces(_user("full-user"))

    assert result == [workspaces.COPILOT, workspaces.OPERATIONS]


def test_no_session_sees_no_workspaces():
    # user=None representa "sin sesión" — no una combinación de tiers
    # inventada. Bajo la política actual, un usuario CON sesión siempre
    # tiene tier "full" o "academy" (nunca None — ver invariante en
    # auth_service.py), así que "0 workspaces autenticado" no es
    # representable; "sin sesión" sí lo es y es lo que se prueba acá.
    assert workspaces.available_workspaces(None) == []


def test_operations_only_is_not_representable_under_current_policy():
    # Documentación ejecutable: Copilot.is_authorized es `user is not None`
    # (superconjunto estricto de Operations, que exige tier=="full"). Para
    # CUALQUIER user real, si Operations lo autoriza, Copilot también.
    for tier_marker in ("full-user", "academy-user", "anything"):
        user = _user(tier_marker)
        if workspaces.OPERATIONS.is_authorized(user):
            assert workspaces.COPILOT.is_authorized(user)


def test_workspace_metadata_matches_expected_routing():
    assert workspaces.COPILOT.home_url == "/"
    assert workspaces.OPERATIONS.home_url == "/operaciones/staffing/asignaciones"
    assert {ws.key for ws in workspaces.available_workspaces(_user("x"))} <= {"copilot", "operations"}
