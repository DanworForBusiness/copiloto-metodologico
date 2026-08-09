"""
test_workspace_switcher.py — Contrato de presentación del selector de
workspace in-app (`partials/workspace_switcher.html`), batch de navegación
in-app post cierre de Platform Entry / Workspace Resolution.

El partial es PURA presentación: renderiza `current_workspace` +
`available_workspaces` tal como los pasa el router — no decide
autorización. Estos tests verifican SOLO lo observable en el HTML (no la
lógica de `available_workspaces`, ya cubierta en test_workspaces.py).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import modules as modules_router
from webapp.services import auth_service
from webapp import workspaces

auth_service.ALLOWED_USERS = "ALL"

_MODULE_URL = "/modules/01_Arquitectura_y_Conectividad"

_FULL_SESSION = auth_service.create_session(
    {"email": "switcher-full@danwor.com", "name": "Full", "first_name": "Full", "family_name": "User"}
)


def _full_client():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _FULL_SESSION)
    return c


def _academy_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    session = auth_service.create_session(
        {"email": "switcher-academy@danwor.com", "name": "Academy", "first_name": "Academy", "family_name": "User"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# --- A: usuario solo-Copilot (tier academy) ------------------------------

def test_copilot_only_user_sees_identity_without_dropdown(monkeypatch):
    response = _academy_client(monkeypatch).get(_MODULE_URL)
    assert response.status_code == 200
    assert workspaces.COPILOT.label in response.text
    assert '<details class="workspace-switch">' not in response.text
    assert "workspace-switch-menu" not in response.text
    assert "Todos los espacios" not in response.text


# --- B: usuario multi-workspace en Copilot -------------------------------

def test_multi_workspace_user_on_copilot_sees_operations_destination():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch">' in response.text
    assert workspaces.COPILOT.label in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text
    assert workspaces.OPERATIONS.label in response.text
    assert 'href="/onedanwor"' in response.text
    assert "Todos los espacios" in response.text


# --- C: usuario multi-workspace en Operations ----------------------------

def test_multi_workspace_user_on_operations_sees_copilot_destination():
    response = _full_client().get("/operations/staffing")
    assert response.status_code == 200
    assert '<details class="workspace-switch">' in response.text
    assert workspaces.OPERATIONS.label in response.text
    assert f'href="{workspaces.COPILOT.home_url}"' in response.text
    assert workspaces.COPILOT.label in response.text
    assert 'href="/onedanwor"' in response.text


# --- D: el partial renderiza EXACTAMENTE lo que devuelve el resolver -----

def test_switcher_never_shows_a_workspace_the_resolver_did_not_return(monkeypatch):
    # Usuario full ve normalmente ambos workspaces — si el resolver devuelve
    # solo Copilot, el switcher no debe inventar ni mostrar Operations.
    monkeypatch.setattr(modules_router.workspaces, "available_workspaces", lambda user: [workspaces.COPILOT])
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert "Operations" not in response.text
    assert '<details class="workspace-switch">' not in response.text


# --- G: identidad de Operations no se duplica ----------------------------

def test_operations_topbar_shows_workspace_identity_exactly_once():
    response = _full_client().get("/operations/staffing")
    assert response.status_code == 200
    # La identidad vieja hardcodeada ("Danwor · Operations") queda reemplazada
    # por el partial — no debe sobrevivir junto al selector nuevo.
    assert "Danwor · Operations" not in response.text
    assert response.text.count(f">{workspaces.OPERATIONS.label}<") == 1


# --- Regresión: pack no viaja al cambiar de workspace, comportamiento intacto --

def test_pack_query_param_unaffected_by_switcher():
    response = _full_client().get(f"{_MODULE_URL}?pack=datasphere_core")
    assert response.status_code == 200
    # El destino a Operations nunca debe cargar ?pack=... — pack es interno
    # del Copiloto y no tiene significado para otros workspaces.
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}?pack=' not in response.text
