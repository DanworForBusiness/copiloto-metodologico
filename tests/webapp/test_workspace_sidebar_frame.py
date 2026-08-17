"""
test_workspace_sidebar_frame.py — Shared Workspace Sidebar Frame (discovery +
minimal consolidation): prueba REUSO semántico entre el nav izquierdo del
Copiloto (partials/copilot_nav.html) y el de Operaciones
(partials/operations_nav.html) — nunca snapshots de pixeles ni de HTML
completo.

Contexto (ver HANDOFF): el frame (grid `.app-shell`, `.module-nav`, el
controlador Alpine `copilotLayout()` en app.js) YA era compartido antes de
este batch — la sospecha de "copiado en vez de reusado" se DESCARTÓ. Lo que
faltaba en Operaciones eran dos piezas puntuales: el `<div data-resize>` del
handle de resize (omitido en el template) y `user` en el contexto del router
(por eso nunca renderizaba la bienvenida). La navegación en sí (Core/Apéndices/
Academy del Copiloto vs. Staffing/Asignaciones de Operaciones) sigue siendo
100% workspace-owned — eso no cambia.

Corrective posterior (mismo batch, antes de abrir PR): el `<div data-resize>`
en sí seguía copy-pasteado literalmente en 4 lugares (module.html x2,
academy.html, operations/staffing.html) — extraído a
`partials/workspace_shell.html::sidebar_resize_handle(side)`, la misma
partial que ya tenía `nav_welcome`. Estos tests verifican que los 4
consumidores reales usan el macro (no el markup inline) y que el handle
sigue renderizando exactamente una vez por lado.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_COPILOT_NAV = (_TEMPLATES / "partials" / "copilot_nav.html").read_text(encoding="utf-8")
_OPERATIONS_NAV = (_TEMPLATES / "partials" / "operations_nav.html").read_text(encoding="utf-8")
_COMMERCIAL_NAV = (_TEMPLATES / "partials" / "commercial_nav.html").read_text(encoding="utf-8")
_SHELL_PARTIAL = (_TEMPLATES / "partials" / "workspace_shell.html").read_text(encoding="utf-8")
_STAFFING_PAGE = (_TEMPLATES / "operations" / "staffing.html").read_text(encoding="utf-8")
_MODULE_PAGE = (_TEMPLATES / "module.html").read_text(encoding="utf-8")
_ACADEMY_PAGE = (_TEMPLATES / "academy.html").read_text(encoding="utf-8")
_LAYOUT_CSS = (_STATIC / "layout.css").read_text(encoding="utf-8")

_MODULE_URL = "/modules/01_Arquitectura_y_Conectividad"
_STAFFING_URL = "/operaciones/staffing/asignaciones"
_ACADEMY_URL = "/academy/ACAD01_Paradigmas"
_COMMERCIAL_URL = "/comercial"


def _copilot_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    session = auth_service.create_session(
        {"email": "sidebar-copilot@danwor.com", "name": "Sidebar Copilot", "first_name": "Sidebar", "family_name": "Copilot"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _operations_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "sidebar-ops@danwor.com")
    session = auth_service.create_session(
        {"email": "sidebar-ops@danwor.com", "name": "Sidebar Ops", "first_name": "Sidebar", "family_name": "Ops"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _commercial_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "sidebar-crm@danwor.com")
    session = auth_service.create_session(
        {"email": "sidebar-crm@danwor.com", "name": "Sidebar Crm", "first_name": "Sidebar", "family_name": "Crm"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# --- 1/2: ambos workspaces consumen el MISMO frame compartido (resize) -----

def test_copilot_renders_the_shared_resize_handle(monkeypatch):
    response = _copilot_client(monkeypatch).get(_MODULE_URL)
    assert response.status_code == 200
    assert '<div class="resize-handle resize-left" data-resize="left"' in response.text


def test_operations_now_renders_the_same_shared_resize_handle(monkeypatch):
    # Antes de este batch, Operations no tenía este <div> — el mecanismo de
    # drag (app.js::copilotLayout().init(), ver más abajo) ya buscaba
    # cualquier [data-resize] dentro del .app-shell, pero no tenía nada que
    # encontrar. La corrección es agregar el handle, no una segunda
    # implementación de resize.
    response = _operations_client(monkeypatch).get(_STAFFING_URL)
    assert response.status_code == 200
    assert '<div class="resize-handle resize-left" data-resize="left"' in response.text


def test_both_workspaces_use_the_same_layout_controller_and_shell_geometry():
    # MISMO controlador Alpine (app.js::copilotLayout) y MISMA clase de grid
    # (.app-shell, layout.css) — la geometría/el resize nunca fueron (ni son
    # ahora) dos implementaciones distintas.
    assert 'x-data="copilotLayout(' in _STAFFING_PAGE
    assert 'x-data="copilotLayout(' in _MODULE_PAGE
    assert 'class="app-shell' in _STAFFING_PAGE
    assert 'class="app-shell' in _MODULE_PAGE


def test_sidebar_geometry_sourced_from_shared_app_shell_not_workspace_local():
    # Ni los nav partials ni el template de Staffing declaran su propio
    # --nav-width/grid-template-columns — la única fuente es .app-shell
    # (webapp/static/layout.css), consumida por ambos vía la misma clase.
    for source in (_COPILOT_NAV, _OPERATIONS_NAV, _STAFFING_PAGE):
        assert "--nav-width" not in source
        assert "grid-template-columns" not in source


# --- 5: bienvenida de identidad — compartida, no reimplementada ------------

def test_copilot_and_operations_render_the_shared_welcome_for_their_own_user(monkeypatch):
    copilot_html = _copilot_client(monkeypatch).get(_MODULE_URL).text
    operations_html = _operations_client(monkeypatch).get(_STAFFING_URL).text
    assert '<p class="nav-welcome">Bienvenido <strong>Sidebar Copilot!</strong></p>' in copilot_html
    assert '<p class="nav-welcome">Bienvenido <strong>Sidebar Ops!</strong></p>' in operations_html


def test_welcome_markup_defined_once_and_reused_by_both_nav_partials():
    # Un solo macro fuente (mismo criterio que hub_icon() en
    # test_platform_hub_icon_reuse.py) — el markup real (`<p class=
    # "nav-welcome">`) no debe reaparecer copiado inline en ninguno de los
    # dos partials de nav (los comentarios explicativos SÍ pueden mencionar
    # la palabra "Bienvenido" en prosa, eso no es el markup duplicado).
    assert 'from "partials/workspace_shell.html" import nav_welcome' in _COPILOT_NAV
    assert 'from "partials/workspace_shell.html" import nav_welcome' in _OPERATIONS_NAV
    assert '<p class="nav-welcome">' not in _COPILOT_NAV
    assert '<p class="nav-welcome">' not in _OPERATIONS_NAV
    assert _SHELL_PARTIAL.count('<p class="nav-welcome">') == 1


# --- MICRO-CORRECTIVE: ritmo vertical del sidebar (Bienvenido → kicker →
# primer nav item) unificado entre Copiloto/Operations/Comercial -----------
#
# Auditoría (owner live QA): Copiloto ya tenía el ritmo correcto porque su
# kicker vive DENTRO de un <button class="nav-toggle"> (colapsable — Core),
# cuyo padding (4px 0 8px, layout.css) provee el espaciado. Operations/
# Comercial usan un <p class="nav-kicker"> plano (sin acordeón, nav flat) —
# ese <p> nunca tuvo padding/margin propio (el reset de Tailwind CDN
# zeroea el margin default de <p>, y ningún selector existente lo
# compensaba: `.module-nav > .nav-kicker` no matchea porque hay un <nav>
# de por medio en los TRES workspaces). Resultado: kicker pegado al
# "Bienvenido" y al primer nav item en Operations/Comercial.
#
# Fix: `.sidebar-workspace-kicker` (layout.css) — clase ADITIVA nueva,
# mismo padding que YA usaba `.nav-toggle` para Copiloto, agregada a los
# TRES kickers (el <button> de Copiloto Y los <p> de Operations/Comercial).
# CERO cambios a `.nav-toggle`/`.nav-toggle--appendix`/`.nav-toggle--academy`
# — Copiloto queda pixel-idéntico (mismo padding, ahora desde una clase
# adicional cuyo valor coincide con el que ya tenía).

def test_all_three_workspaces_share_the_sidebar_workspace_kicker_class():
    # La MISMA clase en los tres — nunca `.operations-sidebar-spacing`/
    # `.commercial-sidebar-spacing` ni un offset ad-hoc por vertical.
    assert 'class="nav-toggle sidebar-workspace-kicker"' in _COPILOT_NAV
    assert 'class="nav-kicker sidebar-workspace-kicker"' in _OPERATIONS_NAV
    assert 'class="nav-kicker sidebar-workspace-kicker"' in _COMMERCIAL_NAV


def test_sidebar_workspace_kicker_rhythm_defined_once_not_per_workspace():
    # Una sola declaración de la regla compartida — nunca duplicada ni
    # reescrita con números distintos por workspace.
    assert _LAYOUT_CSS.count(".sidebar-workspace-kicker {") == 1
    for banned in (".operations-sidebar-spacing", ".commercial-sidebar-spacing", ".copilot-sidebar-spacing"):
        assert banned not in _LAYOUT_CSS
        assert banned not in _OPERATIONS_NAV
        assert banned not in _COMMERCIAL_NAV
        assert banned not in _COPILOT_NAV


def test_sidebar_workspace_kicker_rule_is_additive_never_touched_nav_toggle_base():
    # El corrective es puramente ADITIVO — `.nav-toggle` (Copiloto) conserva
    # EXACTAMENTE su padding original, nunca se le quitó/movió nada para
    # "hacerlo coincidir" con Operations/Comercial.
    assert ".nav-toggle {" in _LAYOUT_CSS
    nav_toggle_rule = _LAYOUT_CSS[_LAYOUT_CSS.index(".nav-toggle {"):_LAYOUT_CSS.index("}", _LAYOUT_CSS.index(".nav-toggle {"))]
    assert "padding: 4px 0 8px;" in nav_toggle_rule


def test_appendix_and_academy_toggles_never_use_the_workspace_kicker_class():
    # Sección DISTINTA (separador con su propio margin-top/border-top) —
    # nunca confundir con el kicker de nivel workspace.
    for marker in ("nav-toggle--appendix", "nav-toggle--academy"):
        start = _COPILOT_NAV.index(marker)
        line_start = _COPILOT_NAV.rfind("\n", 0, start)
        line_end = _COPILOT_NAV.index("\n", start)
        line = _COPILOT_NAV[line_start:line_end]
        assert "sidebar-workspace-kicker" not in line


def test_kicker_renders_with_the_shared_class_on_all_three_live_pages(monkeypatch):
    copilot_html = _copilot_client(monkeypatch).get(_MODULE_URL).text
    operations_html = _operations_client(monkeypatch).get(_STAFFING_URL).text
    commercial_html = _commercial_client(monkeypatch).get(_COMMERCIAL_URL).text
    assert 'class="nav-toggle sidebar-workspace-kicker"' in copilot_html
    assert 'class="nav-kicker sidebar-workspace-kicker"' in operations_html
    assert 'class="nav-kicker sidebar-workspace-kicker"' in commercial_html


# --- corrective: el <div data-resize> ya no vive copy-pasteado -------------

def test_resize_handle_markup_defined_once_and_reused_by_all_page_templates():
    # Antes de la corrección, el mismo <div class="resize-handle ...> vivía
    # copiado literalmente en module.html (x2, left+right), academy.html
    # (left) y operations/staffing.html (left) — 4 copias del mismo markup.
    # Ahora las 4 páginas importan el macro; el <div> real solo existe una
    # vez, dentro de workspace_shell.html.
    for page in (_MODULE_PAGE, _ACADEMY_PAGE, _STAFFING_PAGE):
        assert 'from "partials/workspace_shell.html" import sidebar_resize_handle' in page
        assert '<div class="resize-handle' not in page
    assert _SHELL_PARTIAL.count('<div class="resize-handle') == 1


def test_canonical_resize_handle_renders_exactly_once_on_single_panel_pages(monkeypatch):
    # Operaciones y Academy no tienen panel derecho — un solo handle (left).
    operations_html = _operations_client(monkeypatch).get(_STAFFING_URL).text
    academy_html = _copilot_client(monkeypatch).get(_ACADEMY_URL).text
    assert operations_html.count('data-resize="left"') == 1
    assert operations_html.count('data-resize="right"') == 0
    assert academy_html.count('data-resize="left"') == 1
    assert academy_html.count('data-resize="right"') == 0


def test_canonical_resize_handle_renders_once_per_side_on_module_page(monkeypatch):
    # module.html SÍ tiene panel derecho (show_panel) — un handle por lado,
    # nunca duplicado.
    response = _copilot_client(monkeypatch).get(_MODULE_URL)
    assert response.text.count('data-resize="left"') == 1
    assert response.text.count('data-resize="right"') == 1


# --- 3/4: la navegación en sí sigue siendo 100% workspace-owned ------------

def test_copilot_still_renders_its_own_module_pack_navigation(monkeypatch):
    response = _copilot_client(monkeypatch).get(_MODULE_URL)
    assert 'class="nav-kicker">CORE' in response.text
    assert "Danwor Academy" in response.text


def test_operations_still_renders_its_own_staffing_navigation(monkeypatch):
    # Platform Entry / Workspace Resolution (ver HANDOFF): Operaciones dejó
    # de tener un kicker "STAFFING" propio de una sola vertical — ahora es
    # nav plana "OPERACIONES" (operations_nav.html) con dos áreas productivas
    # (Asignaciones/Reporting), sin acordeón. Sigue siendo 100% workspace-
    # owned (nunca el nav CORE del Copiloto) — solo cambió su rótulo/alcance.
    response = _operations_client(monkeypatch).get(_STAFFING_URL)
    # Sidebar Vertical Rhythm micro-corrective (ver HANDOFF): el kicker
    # ganó la clase compartida `sidebar-workspace-kicker` — mismo texto,
    # mismo rótulo, solo el ritmo vertical se unificó con Copiloto.
    assert '<p class="nav-kicker sidebar-workspace-kicker">OPERACIONES</p>' in response.text
    assert "Asignaciones" in response.text
    assert "Reporting" in response.text


# --- 6/7: sin fuga de contenido específico de un workspace al otro ---------

def test_no_copilot_specific_module_pack_markup_leaks_into_operations(monkeypatch):
    response = _operations_client(monkeypatch).get(_STAFFING_URL)
    assert "nav-toggle" not in response.text  # acordeón Core/Apéndices/Academy
    assert "Danwor Academy" not in response.text
    assert "CORE " not in response.text


def test_no_operations_specific_staffing_markup_leaks_into_copilot(monkeypatch):
    response = _copilot_client(monkeypatch).get(_MODULE_URL)
    assert "STAFFING" not in response.text
    assert "Asignaciones" not in response.text
