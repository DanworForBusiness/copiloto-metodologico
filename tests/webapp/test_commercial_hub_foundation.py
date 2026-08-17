"""
test_commercial_hub_foundation.py — Commercial Hub Workspace Foundation
(Batch 0): contrato HTTP + de plantillas del TERCER workspace del Danwor
Hub (`/comercial`). Alcance del batch: PLATAFORMA/SHELL únicamente — sin
Odoo, sin Graph, sin dato real, sin endpoints de datos (ver
webapp/routers/commercial.py). Cubre los 11 puntos pedidos por el batch:

 1. Hub landing muestra 3 verticales para usuario autorizado.
 2. Grid de tiles conserva el mismo componente/regla responsive (sin
    transform:scale, sin override por cantidad de tiles).
 3. Comercial tiene namespace `/comercial` (no `/operaciones/crm`).
 4. Comercial reutiliza shell/topbar/loader canónico del Hub.
 5. Comercial no tiene auth propio (reusa auth_service/workspaces).
 6. Entitlement es fail-closed (unit tests exhaustivos: test_commercial_access.py).
 7. Usuario no autorizado no descubre ni accede a Comercial.
 8. Sin iframe/link al CRM legacy (Odoo/DigitalOcean).
 9. Copiloto intacto.
10. Operations intacto (sin fuga de markup entre workspaces).
11. Route surface actualizado explícitamente (ver test_staffing_production_corrective.py
    ::test_15_route_surface_still_99 y test_route_contract.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service
from webapp import workspaces

_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_ROUTERS = Path(__file__).resolve().parents[2] / "webapp" / "routers"
_BRAND_HERO_CSS = (_STATIC / "brand-hero.css").read_text(encoding="utf-8")
_CRM_HTML = (_TEMPLATES / "commercial" / "crm.html").read_text(encoding="utf-8")
_COMMERCIAL_NAV_HTML = (_TEMPLATES / "partials" / "commercial_nav.html").read_text(encoding="utf-8")
_OPERATIONS_NAV_HTML = (_TEMPLATES / "partials" / "operations_nav.html").read_text(encoding="utf-8")
_COMMERCIAL_PY = (_ROUTERS / "commercial.py").read_text(encoding="utf-8")
_COMMERCIAL_ACCESS_PY = (Path(__file__).resolve().parents[2] / "webapp" / "commercial_access.py").read_text(encoding="utf-8")

auth_service.ALLOWED_USERS = "ALL"
# COMMERCIAL_ALLOWED_USERS: asignación de MÓDULO (no monkeypatch, no se
# revierte entre archivos) — mismo riesgo ya documentado para
# OPERATIONS_ALLOWED_USERS en test_staffing_routes.py: "la última que se
# ejecute durante el collection de pytest gana". El CSV lista los emails
# default de AMBOS archivos de Commercial Odoo Batch 1 que asignan esta
# variable a nivel de módulo (este archivo + test_commercial_odoo_routes.py)
# para que ninguno pise al otro sin importar el orden de collection.
auth_service.COMMERCIAL_ALLOWED_USERS = "commercial-test@danwor.com,commercial-odoo-test@danwor.com"

_SESSION = auth_service.create_session(
    {"email": "commercial-test@danwor.com", "name": "Commercial Test", "first_name": "Commercial", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


def _unlisted_client(monkeypatch):
    # Admitido full a la app, pero SIN estar en COMMERCIAL_ALLOWED_USERS.
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-test@danwor.com")
    session = auth_service.create_session(
        {"email": "unlisted@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


# ============================================================================
# 3 — Namespace canónico /comercial (NO /operaciones/crm)
# ============================================================================

def test_get_anonymous_redirects_to_login_preserving_canonical_next():
    anon = TestClient(app)
    response = anon.get("/comercial", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Fcomercial"


def test_get_authorized_returns_200_at_canonical_namespace():
    response = client.get("/comercial")
    assert response.status_code == 200


def test_operaciones_crm_namespace_does_not_exist():
    response = client.get("/operaciones/crm")
    assert response.status_code == 404


# ============================================================================
# 4 — Reutiliza shell/topbar/loader canónico del Hub (MISMO DOM, no una copia)
# ============================================================================

def test_shell_reuses_the_canonical_app_shell_and_topbar_macro():
    response = client.get("/comercial")
    text = response.text
    assert 'class="app-shell no-copilot"' in text
    assert '<header class="topbar">' in text
    assert 'class="module-content"' in text
    assert 'data-resize="left"' in text


def test_shell_reuses_the_exact_same_canonical_progress_dom():
    # El ÚNICO nodo del loader del Hub — montado por platform_topbar.html,
    # nunca redefinido acá (ver Hub Foreground Progress corrective).
    response = client.get("/comercial")
    assert response.text.count('<div class="read-progress" aria-hidden="true"><span class="read-progress__fill"></span></div>') == 1


def test_crm_template_never_embeds_its_own_progress_markup():
    assert "read-progress" not in _CRM_HTML
    assert "hub-progress" not in _CRM_HTML


def test_shell_shows_workspace_switcher_when_multiple_workspaces_available():
    # commercial-test@danwor.com está admitido a Copiloto Y Comercial (ver
    # setup de módulo) — el switcher debe aparecer (>1 workspace).
    response = client.get("/comercial")
    assert 'class="workspace-switch"' in response.text
    assert "Copiloto Metodológico" in response.text  # el OTRO workspace disponible


def test_commercial_nav_highlights_the_single_crm_capability():
    response = client.get("/comercial")
    text = response.text
    assert '<p class="nav-kicker">COMERCIAL</p>' in text
    assert '<strong>CRM</strong>' in text
    assert 'href="/comercial" class="module-link is-active"' in text


def test_topbar_identity_uses_commercial_app_title_and_subtitle():
    import config
    from urllib.parse import quote
    response = client.get("/comercial")
    assert quote(config.COMMERCIAL_APP_TITLE) in response.text
    assert quote(config.COMMERCIAL_APP_SUBTITLE) in response.text


def test_content_shows_expected_eyebrow_title_and_copy_without_banned_words():
    response = client.get("/comercial")
    text = response.text
    assert '<p class="eyebrow">Comercial</p>' in text
    assert "<h1>CRM Danwor</h1>" in text
    assert "Gestión y analítica comercial integrada con nuestro CRM." in text
    for banned in ("Preview", "PoC", "Prototype", "Coming Soon", "preview", "coming soon"):
        assert banned not in text


# ============================================================================
# 5 — Comercial NO tiene auth propio
# ============================================================================

def test_commercial_router_never_defines_its_own_session_primitives():
    for banned in ("COOKIE_NAME", "create_session(", "decode_session(", "def session_user", "jwt.encode", "jwt.decode"):
        assert banned not in _COMMERCIAL_PY, banned
        assert banned not in _COMMERCIAL_ACCESS_PY, banned


def test_commercial_access_reuses_auth_service_predicate_not_a_new_one():
    assert "from webapp.services import auth_service" in _COMMERCIAL_ACCESS_PY
    assert "auth_service.is_authorized(" in _COMMERCIAL_ACCESS_PY
    assert "auth_service.is_commercial_user(" in _COMMERCIAL_ACCESS_PY


def test_commercial_router_reuses_the_shared_login_redirect():
    # MISMO mecanismo que Operations: workspaces.platform_login_redirect,
    # nunca webapp.nav.login_redirect (Copilot-específico) ni un redirect
    # propio hardcodeado. Se chequea la ausencia de IMPORT/uso en código
    # (no de la prosa del docstring, que sí lo menciona para explicar la
    # decisión).
    assert "workspaces.platform_login_redirect(" in _COMMERCIAL_PY
    assert "from webapp.nav" not in _COMMERCIAL_PY
    assert "import nav" not in _COMMERCIAL_PY
    assert "nav.login_redirect(" not in _COMMERCIAL_PY


# ============================================================================
# 6/7 — Fail-closed + no discoverable/accesible para usuario no autorizado
# ============================================================================

def test_unlisted_authorized_app_user_is_denied_with_403(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial")
    assert response.status_code == 403


def test_commercial_allowed_users_blank_denies_the_previously_authorized_user(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    response = client.get("/comercial")
    assert response.status_code == 403


def test_unauthorized_user_never_sees_the_commercial_tile_in_the_selector(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/onedanwor")
    assert response.status_code == 200
    assert "Gestión Comercial" not in response.text
    assert 'href="/comercial"' not in response.text


def test_graph_token_like_param_never_bypasses_commercial_authorization(monkeypatch):
    # Comercial no tiene endpoints de datos todavía, pero el gate de sesión
    # debe ser inmune a cualquier parámetro — nunca hay un bypass por query.
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial?graph_token=a-real-looking-token")
    assert response.status_code == 403


# ============================================================================
# 1/2 — Hub landing: 3 verticales, mismo componente de grid responsive
# ============================================================================

def test_authorized_user_for_all_three_workspaces_sees_three_tiles(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "commercial-test@danwor.com")
    response = client.get("/onedanwor")
    assert response.status_code == 200
    text = response.text
    assert "Copiloto Metodológico" in text
    assert "Gestión Inteligente" in text
    assert "Gestión Comercial" in text
    assert "CRM Danwor" in text
    assert "Pipeline y oportunidades" in text
    assert "Seguimiento de actividad comercial" in text
    assert "Indicadores y analítica CRM" in text
    assert f'href="{workspaces.COPILOT.home_url}"' in text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in text
    assert f'href="{workspaces.COMMERCIAL.home_url}"' in text
    # Jerarquía visual: título blanco (h2) + acento gold (span), MISMO
    # componente que las otras dos cards — nunca un <h2> propio distinto.
    assert "<h2>Gestión Comercial<span>CRM Danwor</span></h2>" in text


def test_tile_grid_is_a_single_shared_responsive_component_no_per_tile_hacks():
    # `.workspace-tiles`/`.workspace-tile` es la MISMA regla para 2 o 3
    # tiles — auto-fit/minmax ya es responsive por construcción (3 col
    # desktop ancho / 2 intermedio / 1 mobile, sin media queries a mano).
    # Ninguna transform:scale ni override por cantidad de tiles.
    assert "grid-template-columns:repeat(auto-fit,minmax(" in _BRAND_HERO_CSS
    assert re.search(r"\.workspace-tile\s*\{[^}]*transform:\s*scale", _BRAND_HERO_CSS) is None
    # Solo UNA declaración de `.workspace-tiles` en toda la hoja de estilos
    # (ningún override condicional por N de tiles).
    assert _BRAND_HERO_CSS.count(".workspace-tiles{") == 1


def test_workspace_tile_geometry_is_square_via_aspect_ratio_not_arbitrary_height():
    # CORRECTIVE VISUAL (owner): los tiles deben ser CUADRADOS — geometría
    # real (aspect-ratio), nunca height/min-height fijo ni altura derivada
    # del contenido. Verificado como contrato de fuente acá; la geometría
    # REAL (computed width==height en las 3 anchuras) se verificó en vivo
    # en browser (ver STOP report) — este test protege contra que alguien
    # borre la regla sin querer, no sustituye esa verificación visual.
    tile_rule = re.search(r"\.workspace-tile\s*\{([^}]*)\}", _BRAND_HERO_CSS)
    assert tile_rule is not None
    assert re.search(r"aspect-ratio\s*:\s*1\s*/\s*1", tile_rule.group(1))
    assert "height:" not in tile_rule.group(1)
    assert "min-height" not in _BRAND_HERO_CSS[_BRAND_HERO_CSS.index(".workspace-tile{"):_BRAND_HERO_CSS.index(".workspace-tile{") + 2000]


def test_workspace_tile_typography_scale_is_smaller_than_the_shared_access_brand_default():
    # CORRECTIVE VISUAL (owner): "no comprimir únicamente el ancho dejando
    # la tipografía intacta" — la escala tipográfica/espacial DENTRO de
    # `.workspace-tile .access-brand` debe ser explícitamente MENOR que el
    # default compartido de `.access-brand` (usado por la pantalla de
    # acceso/`.hub-hero`, que NO se toca). Nunca la misma escala solo con
    # un contenedor más angosto.
    shared_h1_match = re.search(r"\.access-brand h1\s*\{[^}]*font-size:\s*clamp\(([\d.]+)rem", _BRAND_HERO_CSS)
    tile_h2_match = re.search(r"\.workspace-tile \.access-brand h2\s*\{[^}]*font-size:\s*clamp\(([\d.]+)rem", _BRAND_HERO_CSS)
    assert shared_h1_match and tile_h2_match
    assert float(tile_h2_match.group(1)) < float(shared_h1_match.group(1))

    shared_p_match = re.search(r"\.access-brand p\s*\{[^}]*font-size:\s*([\d.]+)rem", _BRAND_HERO_CSS)
    tile_p_match = re.search(r"\.workspace-tile \.access-brand p\s*\{[^}]*font-size:\s*([\d.]+)rem", _BRAND_HERO_CSS)
    assert shared_p_match and tile_p_match
    assert float(tile_p_match.group(1)) < float(shared_p_match.group(1))

    # El padding scoped al tile también debe ser menor que el shared default.
    shared_padding_match = re.search(r"\.access-brand\s*\{padding:\s*(\d+)px", _BRAND_HERO_CSS)
    tile_padding_match = re.search(r"\.workspace-tile \.access-brand\s*\{[^}]*padding:\s*(\d+)px", _BRAND_HERO_CSS)
    assert shared_padding_match and tile_padding_match
    assert int(tile_padding_match.group(1)) < int(shared_padding_match.group(1))


def test_workspace_tile_rules_apply_identically_no_per_workspace_css_exception():
    # CORRECTIVE VISUAL (owner): "la tarjeta Comercial no puede introducir
    # una excepción" — ninguna clase con modificador por workspace
    # (--commercial/--copilot/--operations) en la hoja de estilos.
    for banned in ("workspace-tile--commercial", "workspace-tile--copilot", "workspace-tile--operations"):
        assert banned not in _BRAND_HERO_CSS


# ============================================================================
# 8 — Sin iframe/link al CRM legacy (Odoo/DigitalOcean)
# ============================================================================

def test_no_reference_to_legacy_crm_app_anywhere_in_commercial_surface():
    # Referencias al REPO/APP legacy específico (`crm-comercial`, el backend
    # DigitalOcean) — NO la palabra "Odoo"/"odoo_client" en general:
    # Commercial Hub Odoo Batch 1 (ver HANDOFF) integra Odoo de verdad y
    # tiene su PROPIO `webapp/services/odoo_client.py`, legítimamente
    # nombrado en docstrings/imports — nunca se copió el archivo legacy
    # (`crm-comercial/backend/odoo_client.py`), así que lo que se verifica
    # acá es la ausencia del path/dominio LEGACY específico, no del
    # concepto "Odoo" en sí (ver test_commercial_odoo_routes.py::
    # test_no_legacy_crm_auth_primitives_anywhere_in_commercial_surface
    # para la verificación complementaria de auth/JWT/DB legacy).
    haystacks = [_CRM_HTML, _COMMERCIAL_PY, _COMMERCIAL_ACCESS_PY, _COMMERCIAL_NAV_HTML]
    for banned in ("backend/odoo_client", "backend.odoo_client", "digitalocean.app", "ondigitalocean", "crm-comercial"):
        for text in haystacks:
            assert banned not in text.lower(), banned


def test_crm_template_has_exactly_one_iframe_the_shared_brand_identity():
    # El ÚNICO <iframe> es el mismo componente de identidad animada
    # compartido (`/brand/dancing/{pack}`) que usan Copiloto/Operations —
    # nunca un segundo iframe embebiendo la app vieja.
    iframes = re.findall(r"<iframe[^>]*>", _CRM_HTML)
    assert len(iframes) == 1
    assert "/brand/dancing/" in iframes[0]


# ============================================================================
# 9/10 — Copiloto y Operations intactos, sin fuga de markup entre workspaces
# ============================================================================

def test_operations_nav_never_leaks_commercial_markup():
    assert "COMERCIAL" not in _OPERATIONS_NAV_HTML
    assert ">CRM<" not in _OPERATIONS_NAV_HTML


def test_commercial_nav_never_leaks_operations_markup():
    # Chequea el MARKUP renderizado real (nav-kicker + module-link), no
    # prosa de comentario que legítimamente puede nombrar Operations al
    # explicar el patrón que este partial sigue.
    assert '<p class="nav-kicker">OPERACIONES</p>' not in _COMMERCIAL_NAV_HTML
    assert "<strong>Asignaciones</strong>" not in _COMMERCIAL_NAV_HTML
    assert "<strong>Reporting</strong>" not in _COMMERCIAL_NAV_HTML


def test_staffing_page_response_never_mentions_commercial(monkeypatch):
    # Usuario autorizado a Operations pero NO a Comercial: el switcher de
    # workspace legítimamente NO debe ofrecer Comercial como destino (ver
    # test_unauthorized_user_never_sees_the_commercial_tile_in_the_selector
    # para el mismo invariante en /onedanwor) — y el nav propio de
    # Operations tampoco debe traer markup de Comercial.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "ops-only@danwor.com")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    session = auth_service.create_session(
        {"email": "ops-only@danwor.com", "name": "Ops Only", "first_name": "Ops", "family_name": "Only"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    response = ro.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert "Gestión Comercial" not in response.text
    assert '<p class="nav-kicker">COMERCIAL</p>' not in response.text


def test_root_copilot_route_still_serves_copilot_unchanged():
    anon = TestClient(app)
    response = anon.get("/")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text
