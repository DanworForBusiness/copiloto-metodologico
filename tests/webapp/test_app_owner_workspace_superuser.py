"""
test_app_owner_workspace_superuser.py — APP_OWNER como superusuario de TODOS
los workspaces registrados del Hub (owner decision, corrective post-#107).

Contrato exigido (ver `webapp/access.py::is_owner_user` para el razonamiento
completo):

    sesión autenticada + is_owner_user(user)  =>  autorizado en TODO
    workspace registrado (Copiloto/Operations/Commercial/Capital Humano),
    SIN necesitar estar en la allowlist específica de ese workspace.

    sesión autenticada, NO owner  =>  las allowlists base/por-workspace
    siguen exactamente igual que antes (fail-closed sin cambios).

    sin sesión  =>  sin acceso a nada, `APP_OWNER` nunca es un atajo de
    autenticación (sólo de autorización posterior a una sesión válida).

Este archivo prueba la excepción UNA vez, en el predicado compartido
(`webapp.access.is_owner_user`) y en cómo cada workspace lo consume — nunca
reimplementa la comparación contra `APP_OWNER` por su cuenta (si algún
vertical lo hiciera, sería la señal de que la excepción se duplicó en vez de
reusarse, justo lo que este corrective evita).

Capa pura (predicados, sin HTTP) + capa HTTP end-to-end (rutas reales de
cada vertical) + `available_workspaces` (el switcher/discovery, misma
fuente que las rutas)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp import access, commercial_access, human_capital_access, operations_access, workspaces
from webapp.main import app
from webapp.services import auth_service

_OWNER_EMAIL = "owner-superuser-test@danwor.com"
_NON_OWNER_EMAIL = "non-owner-superuser-test@danwor.com"


def _isolate_allowlists(monkeypatch, *, empty=True):
    """Fuerza las allowlists de los tres workspaces (y la admisión BASE) a
    un estado conocido -- por defecto TODAS vacías, para probar que el
    owner entra SIN depender de ninguna de ellas (nunca "el owner además
    resulta estar listado", que no probaría nada)."""
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "" if empty else _NON_OWNER_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "" if empty else _NON_OWNER_EMAIL)
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "" if empty else _NON_OWNER_EMAIL)
    monkeypatch.setattr(access, "APP_OWNER", _OWNER_EMAIL)


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "Superuser Test", "first_name": "Super", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ============================================================================
# Capa pura — un solo lugar decide la excepción (is_owner_user), los tres
# verticales sólo la CONSUMEN, nunca la reimplementan.
# ============================================================================

def test_is_owner_user_true_for_owner_false_for_others_and_none(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", _OWNER_EMAIL)
    assert access.is_owner_user({"sub": _OWNER_EMAIL}) is True
    assert access.is_owner_user({"sub": _NON_OWNER_EMAIL}) is False
    assert access.is_owner_user(None) is False


def test_owner_authorized_in_all_three_vertical_predicates_with_allowlists_empty(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    owner = {"sub": _OWNER_EMAIL}
    assert human_capital_access.is_human_capital_authorized_user(owner) is True
    assert operations_access.is_operations_authorized_user(owner) is True
    assert commercial_access.is_commercial_authorized_user(owner) is True


def test_non_owner_still_denied_by_each_vertical_when_not_in_its_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    non_owner = {"sub": _NON_OWNER_EMAIL}
    assert human_capital_access.is_human_capital_authorized_user(non_owner) is False
    assert operations_access.is_operations_authorized_user(non_owner) is False
    assert commercial_access.is_commercial_authorized_user(non_owner) is False


def test_non_owner_in_its_own_allowlist_is_unaffected_by_the_owner_exception(monkeypatch):
    # La excepción del owner es ADITIVA -- un no-owner correctamente listado
    # en SU allowlist sigue entrando exactamente igual que antes.
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _NON_OWNER_EMAIL)
    monkeypatch.setattr(access, "APP_OWNER", _OWNER_EMAIL)
    assert human_capital_access.is_human_capital_authorized_user({"sub": _NON_OWNER_EMAIL}) is True


def test_owner_without_session_is_never_authorized_app_owner_is_not_a_login_bypass(monkeypatch):
    # APP_OWNER autoriza DESPUÉS de una sesión válida -- nunca la reemplaza.
    _isolate_allowlists(monkeypatch, empty=True)
    assert access.is_owner_user(None) is False
    assert human_capital_access.is_human_capital_authorized_user(None) is False
    assert operations_access.is_operations_authorized_user(None) is False
    assert commercial_access.is_commercial_authorized_user(None) is False


# ============================================================================
# available_workspaces — MISMA fuente que el switcher/discovery y las rutas.
# ============================================================================

def test_available_workspaces_lists_every_registered_workspace_for_owner(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    owner = {"sub": _OWNER_EMAIL}
    keys = {ws.key for ws in workspaces.available_workspaces(owner)}
    assert keys == {"copilot", "operations", "commercial", "human-capital"}


def test_available_workspaces_stays_restricted_for_non_owner(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    non_owner = {"sub": _NON_OWNER_EMAIL}
    keys = {ws.key for ws in workspaces.available_workspaces(non_owner)}
    # Copiloto sigue abierto a cualquier sesión válida (sin allowlist propia);
    # los otros tres exigen estar listados, y acá ninguno lo está.
    assert keys == {"copilot"}


# ============================================================================
# Capa HTTP end-to-end — la MISMA excepción, ejercitada a través de las
# rutas reales de cada vertical (no sólo el predicado en aislamiento).
# ============================================================================

def test_owner_reaches_human_capital_route_with_empty_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_OWNER_EMAIL).get("/cchh")
    assert response.status_code == 200


def test_owner_reaches_operations_route_with_empty_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_OWNER_EMAIL).get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200


def test_owner_reaches_commercial_route_with_empty_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_OWNER_EMAIL).get("/comercial")
    assert response.status_code == 200


def test_owner_reaches_copilot_route(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_OWNER_EMAIL).get("/")
    assert response.status_code == 200


def test_non_owner_still_gets_403_on_human_capital_route_with_empty_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_NON_OWNER_EMAIL).get("/cchh")
    assert response.status_code == 403


def test_non_owner_still_gets_403_on_operations_route_with_empty_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_NON_OWNER_EMAIL).get("/operaciones/staffing/asignaciones")
    assert response.status_code == 403


def test_non_owner_still_gets_403_on_commercial_route_with_empty_allowlist(monkeypatch):
    _isolate_allowlists(monkeypatch, empty=True)
    response = _client_as(_NON_OWNER_EMAIL).get("/comercial")
    assert response.status_code == 403


def test_anonymous_owner_email_without_a_session_is_redirected_not_authorized(monkeypatch):
    # Nadie puede "presentarse" como el owner sin una sesión válida primero
    # -- sin cookie, cualquier ruta de workspace redirige a /platform, nunca
    # autoriza por el mero hecho de que APP_OWNER exista configurado.
    _isolate_allowlists(monkeypatch, empty=True)
    anon = TestClient(app)
    response = anon.get("/cchh", follow_redirects=False)
    assert response.status_code == 303
