"""
test_platform_topbar.py — Contrato del header transversal compartido
(`partials/platform_topbar.html`), batch Shared Workspace Shell +
Operaciones Structure — corrective pass "VISUAL CONTRACT CORRECTION".

Geometría final (recuperada de la versión histórica pre-Batch-B vía git,
ver HANDOFF): IZQUIERDA = identidad animada única (mark I+D + título +
subtítulo, config-driven, MISMO componente para cualquier workspace) — NUNCA
es el trigger del switcher. DERECHA (`.header-tools`) = pager → switcher de
workspace (botón-ícono, MISMA caja visual que theme/logout — `.theme-toggle`,
NO el logo completo) → selector interno (caller) → theme → logout →
mobile-menu.

Sucesor de test_workspace_switcher.py: el switcher standalone en
`header-tools` fue reemplazado por un botón-ícono más (mismo lenguaje
visual que theme/logout), nunca por la identidad animada — ver HANDOFF.
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
# Operations Workspace Access Gate (ver HANDOFF): tier "full" ya no basta
# por sí solo para ver/entrar a Operations — este archivo prueba
# renderizado del topbar/switcher, no la frontera de autorización en sí
# (ver test_operations_access.py), así que se abre explícito listando su
# email — "ALL" ya NO es un wildcard válido para Operations (rechazado
# deliberadamente, ver auth_service._operations_set). MISMO valor CSV que
# usan los otros archivos de test para esta variable (asignación plana de
# módulo: la última que se ejecute durante el collection de pytest "gana"
# para cualquier test sin su propio override).
auth_service.OPERATIONS_ALLOWED_USERS = (
    "staffing-test@danwor.com,topbar-full@danwor.com,onedanwor-full@danwor.com"
)

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
    # Sin Operations explícito para ESTE usuario — a diferencia del "ALL"
    # de módulo (pensado para el cliente full), el fixture academy sigue
    # probando el caso "single workspace" (solo Copilot). Ver
    # test_workspaces.py para el caso NUEVO "academy + Operations listado".
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
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


# --- Seguridad: title/subtitle son query params — deben ser inertes ---------
# (BATCH B — PARAMETERIZED BRAND SAFETY CHECK)

def test_dancing_logo_route_escapes_script_close_tag_breakout():
    # `</script>` en el valor NO debe cerrar el <script> real del asset — el
    # parser de HTML tokeniza esa secuencia de forma literal e insensible a
    # mayúsculas, ANTES de que exista noción de "string JS", así que escapar
    # solo para JS (comillas/backslashes) no alcanza.
    payload = "</script><script>window.__pwned=1</script>"
    response = _full_client().get(
        "/brand/dancing/datasphere_core", params={"title": payload, "subtitle": "x"},
    )
    assert response.status_code == 200
    # El valor crudo (sin escapar) nunca debe aparecer — de lo contrario
    # closea el <script> real.
    assert "</script><script>window.__pwned=1</script>" not in response.text
    # Debe sobrevivir escapado dentro del literal JS.
    assert "const TITLE    = '<\\/script><script>window.__pwned=1<\\/script>';" in response.text


def test_dancing_logo_route_escapes_quotes_and_backslashes():
    payload = r"';alert(document.cookie);//\\"
    response = _full_client().get(
        "/brand/dancing/datasphere_core", params={"title": payload, "subtitle": "x"},
    )
    assert response.status_code == 200
    # Ni la comilla simple ni el backslash pueden llegar crudos al literal —
    # de lo contrario cierran el string JS y el resto se ejecuta como código.
    # (comilla escapada \' + los 2 backslashes originales, cada uno duplicado a \\)
    assert r"const TITLE    = '\';alert(document.cookie);//\\\\';" in response.text


def test_dancing_logo_route_escapes_newlines_and_unicode_passes_through():
    payload = "Línea 1\nLínea 2 · ó"
    response = _full_client().get(
        "/brand/dancing/datasphere_core", params={"title": payload, "subtitle": "x"},
    )
    assert response.status_code == 200
    # Un salto de línea crudo dentro de un literal '...' de JS es inválido
    # (rompe el script) — debe llegar escapado como \n, nunca crudo.
    assert "const TITLE    = 'Línea 1\\nLínea 2 · ó';" in response.text
    assert "Línea 1\nLínea 2" not in response.text
    # Unicode normal (acentos, ·) sigue viajando sin alterarse.
    assert "·" in response.text and "ó" in response.text


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

def test_operations_right_zone_order_pager_switch_context_theme_logout():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    text = response.text
    tools_idx = text.index('class="header-tools"')
    pager_idx = text.index('class="module-pager"')
    switch_idx = text.index('class="workspace-switch"')
    context_idx = text.index('aria-label="Área operativa"')
    theme_idx = text.index('@click="toggleTheme()"')
    logout_idx = text.index('class="theme-toggle logout-btn"')
    assert tools_idx < pager_idx < switch_idx < context_idx < theme_idx < logout_idx


# --- E/F: workspace switching es un botón-ícono más (RIGHT), no la identidad ---

def test_multi_workspace_copilot_user_gets_switcher_as_icon_button():
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch">' in response.text
    # MISMA caja visual que theme/logout — no el logo completo.
    assert '<summary class="theme-toggle" aria-label="Cambiar de espacio de trabajo"' in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text
    assert workspaces.OPERATIONS.label in response.text
    # La identidad animada (LEFT) nunca es el trigger del switcher.
    assert '<summary class="animated-brand"' not in response.text
    # El switcher es un ícono, no el logo completo — sin <img> en el topbar.
    assert "danwor-logo.png" not in response.text


def test_multi_workspace_operations_user_gets_switcher_as_icon_button():
    response = _full_client().get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<details class="workspace-switch">' in response.text
    assert '<summary class="theme-toggle" aria-label="Cambiar de espacio de trabajo"' in response.text
    assert f'href="{workspaces.COPILOT.home_url}"' in response.text
    assert workspaces.COPILOT.label in response.text


def test_single_workspace_user_gets_no_dropdown_but_keeps_identity(monkeypatch):
    academy = _academy_client(monkeypatch)
    response = academy.get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch">' not in response.text
    assert '<a href="/?pack=datasphere_core" class="theme-toggle" aria-label="Danwor · Inicio"' in response.text
    # La identidad animada sigue presente aunque no haya switch posible.
    assert 'class="animated-brand"' in response.text


def test_full_tier_without_operations_listing_also_gets_no_switcher(monkeypatch):
    # Item N del batch: tier "full" NUNCA es la fuente de esta
    # autorización — sin OPERATIONS_ALLOWED_USERS explícito, un usuario
    # full ve el MISMO switcher ausente que uno academy (arriba).
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    response = _full_client().get(_MODULE_URL)
    assert response.status_code == 200
    assert '<details class="workspace-switch">' not in response.text
    assert "Operaciones" not in response.text


# --- G: link a /onedanwor -----------------------------------------------------

def test_brand_switcher_links_to_onedanwor():
    response = _full_client().get(_MODULE_URL)
    assert 'href="/onedanwor"' in response.text
    assert "Todos los espacios" in response.text


# --- H: sin switcher standalone duplicado ------------------------------------

def test_no_duplicate_standalone_switcher_remains():
    response = _full_client().get(_MODULE_URL)
    # Un solo switcher (el botón-ícono de RIGHT) — nada de un segundo
    # control de texto standalone (el viejo `workspace_switcher.html`,
    # eliminado hace dos batches).
    assert response.text.count('class="workspace-switch">') == 1
    assert "workspace_switcher" not in response.text


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
