"""
test_platform_topbar.py — Contrato del header transversal compartido
(`partials/platform_topbar.html`), batch Shared Workspace Shell +
Operaciones Structure — corrective pass "VISUAL CONTRACT CORRECTION".

Geometría final (recuperada de la versión histórica pre-Batch-B vía git,
ver HANDOFF): IZQUIERDA = identidad animada única (mark I+D + título +
subtítulo, config-driven, MISMO componente para cualquier workspace) — NUNCA
es el trigger del switcher. DERECHA (`.header-tools`) = pager → marca
compacta con switcher de workspace → selector interno (caller) → theme →
logout → mobile-menu.

Sucesor de test_workspace_switcher.py: el switcher standalone en
`header-tools` fue reemplazado por el switcher attached a la marca
compacta (ya no a la identidad animada) — ver HANDOFF.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import config
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


def _identity_query(html: str) -> dict:
    """Extrae title/subtitle del <iframe src="/brand/dancing/...?title=...&subtitle=..."> — decodificado, sin importar el estilo de encoding que use el filtro `urlencode` de Jinja."""
    match = re.search(r'<a href="[^"]*" class="animated-brand"[^>]*><iframe src="([^"]+)"', html)
    assert match, "identidad animada (LEFT) no encontrada en el topbar"
    query = parse_qs(urlparse(match.group(1)).query)
    return {"title": query.get("title", [""])[0], "subtitle": query.get("subtitle", [""])[0]}


# --- A/B: LEFT — identidad animada única, config-driven ---------------------

def test_copilot_left_identity_is_animated_and_config_driven():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<header class="topbar">' in response.text
    left_idx = response.text.index('class="animated-brand"')
    header_idx = response.text.index('<header class="topbar">')
    tools_idx = response.text.index('class="header-tools"')
    assert header_idx < left_idx < tools_idx  # LEFT precede a RIGHT
    identity = _identity_query(response.text)
    assert identity["title"] == config.APP_TITLE
    assert identity["subtitle"] == config.APP_SUBTITLE


def test_operations_left_identity_is_same_component_with_operations_config():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<header class="topbar">' in response.text
    identity = _identity_query(response.text)
    assert identity["title"] == config.OPERATIONS_APP_TITLE
    assert identity["subtitle"] == config.OPERATIONS_APP_SUBTITLE
    # MISMO componente (asset /brand/dancing/, no una imagen estática distinta).
    assert "/brand/dancing/" in response.text


def test_dancing_logo_route_substitutes_configured_title_and_subtitle():
    response = _full_client().get("/brand/dancing/datasphere_core?title=Hola+Mundo&subtitle=Sub+Test")
    assert response.status_code == 200
    assert "const TITLE    = 'Hola Mundo';" in response.text
    assert "const SUBTITLE = 'Sub Test';" in response.text
    # El texto original hardcodeado ya no debe sobrevivir.
    assert "Copiloto Metodológico · Arquitecto Danwor" not in response.text


def test_dancing_logo_route_without_params_keeps_original_asset_text():
    # Sin title/subtitle explícitos, el asset conserva su contenido (no-op) —
    # ningún consumidor real omite estos params hoy, pero la ruta no debe
    # romper si alguien la pide "pelada".
    response = _full_client().get("/brand/dancing/datasphere_core")
    assert response.status_code == 200
    assert "const TITLE    = 'Copiloto Metodológico · Arquitecto Danwor';" in response.text


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


# --- D: orden interno de RIGHT (.header-tools) -------------------------------

def test_operations_right_zone_order_pager_brand_context_theme_logout():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    text = response.text
    tools_idx = text.index('class="header-tools"')
    pager_idx = text.index('class="module-pager"')
    brand_idx = text.index('class="workspace-switch topbar-brand-compact"')
    context_idx = text.index('aria-label="Área operativa"')
    theme_idx = text.index('class="theme-toggle"')
    logout_idx = text.index('class="theme-toggle logout-btn"')
    assert tools_idx < pager_idx < brand_idx < context_idx < theme_idx < logout_idx


# --- E/F: workspace switching desde la marca compacta (RIGHT), no la identidad ---

def test_multi_workspace_copilot_user_gets_switcher_on_compact_brand():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch topbar-brand-compact">' in response.text
    assert '<summary class="brand-compact"' in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text
    assert workspaces.OPERATIONS.label in response.text
    # La identidad animada (LEFT) nunca es el trigger del switcher.
    assert '<summary class="animated-brand"' not in response.text


def test_multi_workspace_operations_user_gets_switcher_on_compact_brand():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<details class="workspace-switch topbar-brand-compact">' in response.text
    assert f'href="{workspaces.COPILOT.home_url}"' in response.text
    assert workspaces.COPILOT.label in response.text


def test_single_workspace_user_gets_no_dropdown_but_keeps_identity(monkeypatch):
    academy = _academy_client(monkeypatch)
    response = academy.get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch topbar-brand-compact">' not in response.text
    assert 'class="brand-compact-link"' in response.text
    # La identidad animada sigue presente aunque no haya switch posible.
    assert 'class="animated-brand"' in response.text


# --- G: link a /onedanwor -----------------------------------------------------

def test_brand_switcher_links_to_onedanwor():
    response = _full_client().get(_MODULE_URL)
    assert 'href="/onedanwor"' in response.text
    assert "Todos los espacios" in response.text


# --- H: sin switcher standalone duplicado ------------------------------------

def test_no_duplicate_standalone_switcher_remains():
    response = _full_client().get(_MODULE_URL)
    # Solo un switcher attached a la marca compacta; nada más se llama
    # "workspace-switch" como control independiente en header-tools.
    assert response.text.count('class="workspace-switch topbar-brand-compact"') == 1
    assert response.text.count('class="workspace-switch"') == 0


# --- I: selector interno (Copilot pack / Operaciones área) ------------------

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
