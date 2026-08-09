"""
test_platform_topbar.py — Contrato del header transversal compartido
(`partials/platform_topbar.html`), batch Shared Workspace Shell +
Operaciones Structure. Sucesor de test_workspace_switcher.py: el switcher
standalone en `header-tools` fue reemplazado por el switcher attached a la
marca/logo — ver HANDOFF.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service
from webapp import workspaces

auth_service.ALLOWED_USERS = "ALL"

_MODULE_URL = "/modules/01_Arquitectura_y_Conectividad"
_INTRO_URL = "/modules/00_Intro"  # sin previous_module real

_FULL_SESSION = auth_service.create_session(
    {"email": "topbar-full@danwor.com", "name": "Full", "first_name": "Full", "family_name": "User"}
)


def _full_client():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _FULL_SESSION)
    return c


def _academy_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    session = auth_service.create_session(
        {"email": "topbar-academy@danwor.com", "name": "Academy", "first_name": "Academy", "family_name": "User"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# --- A/D: shared topbar en páginas standalone de Copilot ---------------------

def test_module_page_uses_shared_topbar_with_animated_brand():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<header class="topbar">' in response.text
    assert 'class="animated-brand"' in response.text
    assert "/brand/dancing/" in response.text  # marca animada real, sin regresión


# --- B: shared topbar en Operaciones ------------------------------------------

def test_operations_page_uses_shared_topbar():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<header class="topbar">' in response.text
    assert 'class="animated-brand"' in response.text


# --- C: back/forward reales de módulo, preservados --------------------------

def test_module_page_pager_links_to_real_previous_and_next_module():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert 'aria-label="Módulo anterior"' in response.text
    assert 'aria-label="Módulo siguiente"' in response.text
    assert 'href="/modules/00_Intro?pack=datasphere_core"' in response.text


def test_intro_module_pager_disables_previous_when_no_earlier_module():
    response = _full_client().get(_INTRO_URL)
    assert response.status_code == 200
    # Sin módulo anterior real: NUNCA se fabrica un destino — el control
    # queda deshabilitado exactamente como module.html ya hacía.
    assert 'aria-label="Módulo anterior"' not in response.text
    assert '<span aria-hidden="true">&larr;</span>' in response.text


def test_non_module_copilot_pages_render_pager_disabled_both_sides():
    # Academy/Catalog/Admin/What's New nunca tuvieron secuencia real de
    # páginas — el topbar compartido NO debe inventar una.
    response = _full_client().get("/whats-new")
    assert response.status_code == 200
    assert '<span aria-hidden="true">&larr;</span>' in response.text
    assert '<span aria-hidden="true">&rarr;</span>' in response.text
    assert "aria-label=\"Módulo anterior\"" not in response.text
    assert "aria-label=\"Módulo siguiente\"" not in response.text


def test_operations_page_renders_pager_disabled_both_sides():
    # Staffing/Asignaciones es la única página real hoy — ambos controles
    # deshabilitados, sin fabricar orden.
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<span aria-hidden="true">&larr;</span>' in response.text
    assert '<span aria-hidden="true">&rarr;</span>' in response.text


# --- E/F: workspace switching desde la marca ---------------------------------

def test_multi_workspace_copilot_user_gets_switcher_on_brand():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch topbar-brand">' in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text
    assert workspaces.OPERATIONS.label in response.text


def test_multi_workspace_operations_user_gets_switcher_on_brand():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<details class="workspace-switch topbar-brand">' in response.text
    assert f'href="{workspaces.COPILOT.home_url}"' in response.text
    assert workspaces.COPILOT.label in response.text


def test_single_workspace_user_gets_no_dropdown_on_brand(monkeypatch):
    academy = _academy_client(monkeypatch)
    response = academy.get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch topbar-brand">' not in response.text
    assert 'class="animated-brand"' in response.text


# --- G: link a /onedanwor -----------------------------------------------------

def test_brand_switcher_links_to_onedanwor():
    response = _full_client().get(_MODULE_URL)
    assert 'href="/onedanwor"' in response.text
    assert "Todos los espacios" in response.text


# --- H: sin switcher standalone duplicado ------------------------------------

def test_no_duplicate_standalone_switcher_remains():
    response = _full_client().get(_MODULE_URL)
    # El viejo control de texto separado (`.eyebrow` + `.workspace-switch`
    # como control independiente en header-tools) ya no existe — solo el
    # attached a la marca. `.count` con el atributo completo evita el falso
    # positivo de `workspace-switch-menu` (que también empieza con el mismo
    # prefijo de clase).
    assert response.text.count('class="workspace-switch topbar-brand"') == 1
    assert 'class="workspace-switch"' not in response.text


# --- Section 27: selector interno (Copilot pack / Operaciones área) ---------

def test_copilot_pack_selector_still_renders_in_diagnostic_mode(monkeypatch):
    from webapp import access
    monkeypatch.setattr(access, "APP_OWNER", "topbar-full@danwor.com")
    diag_client = _full_client()
    diag_client.cookies.set("dw-diagnostic", "on")
    response = diag_client.get(_MODULE_URL)
    assert response.status_code == 200
    assert 'class="pack-select"' in response.text
    assert 'class="diag-toggle"' in response.text


def test_operations_internal_selector_shows_staffing_identity_disabled():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert 'aria-label="Área operativa" disabled' in response.text
    assert "Staffing" in response.text
    # No opciones futuras inventadas.
    assert "Projects" not in response.text
    assert "Reporting" not in response.text
