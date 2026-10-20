"""
test_human_capital_hub_foundation.py — Capital Humano Phase 1 (Hub Walking
Skeleton): contrato HTTP + de plantillas del CUARTO workspace del Danwor
Hub (`/cchh`). Alcance de esta fase: VERTICAL BOUNDARY únicamente
— sin dato de negocio, sin Supabase, sin lógica legacy, sin roles
(hrbp/hiring_manager), sin endpoints de datos (ver
webapp/routers/human_capital.py). MISMO patrón exacto que
test_commercial_hub_foundation.py (Commercial Hub Workspace Foundation,
Batch 0), aplicado al cuarto workspace:

 1. Landing autenticado responde 200 en el namespace canónico.
 2. Sin sesión → redirect canónico a /platform.
 3. Autenticado pero NO en la allowlist de Capital Humano → 403, MISMO
    contrato que Commercial/Operations (nunca 404/redirect silencioso).
 4. Reutiliza shell/topbar/loader canónico del Hub (mismo DOM, no una
    copia) — sin CSS/JS propios de Capital Humano.
 5. Capital Humano no tiene auth propio (reusa auth_service/workspaces).
 6. Entitlement es fail-closed (unit tests exhaustivos, ver
    test_human_capital_access.py).
 7. Copiloto/Operations/Commercial intactos — sin fuga de markup entre
    workspaces.
 8. Route surface actualizado explícitamente (ver
    test_staffing_production_corrective.py::test_15_route_surface_still_120
    y test_route_contract.py).

A DIFERENCIA de test_commercial_hub_foundation.py: acá `HUMAN_CAPITAL_
ALLOWED_USERS`/`ALLOWED_USERS` se setean vía una fixture `autouse` con
`monkeypatch` (revertida automáticamente por test) en vez de una
asignación de módulo sin revertir — evita el riesgo de fuga entre
archivos de test ya documentado en ese archivo (COMMERCIAL_ALLOWED_USERS
"la última que se ejecute durante el collection gana").
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_ROUTERS = Path(__file__).resolve().parents[2] / "webapp" / "routers"
_LANDING_HTML = (_TEMPLATES / "human_capital" / "landing.html").read_text(encoding="utf-8")
_HUMAN_CAPITAL_NAV_HTML = (_TEMPLATES / "partials" / "human_capital_nav.html").read_text(encoding="utf-8")
_COMMERCIAL_NAV_HTML = (_TEMPLATES / "partials" / "commercial_nav.html").read_text(encoding="utf-8")
_OPERATIONS_NAV_HTML = (_TEMPLATES / "partials" / "operations_nav.html").read_text(encoding="utf-8")
_HUMAN_CAPITAL_PY = (_ROUTERS / "human_capital.py").read_text(encoding="utf-8")
_HUMAN_CAPITAL_ACCESS_PY = (
    Path(__file__).resolve().parents[2] / "webapp" / "human_capital_access.py"
).read_text(encoding="utf-8")

_AUTHORIZED_EMAIL = "human-capital-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_by_default(monkeypatch):
    # Admisión base + allowlist de Capital Humano para _AUTHORIZED_EMAIL,
    # SIN tocar Operations/Commercial (quedan en su default fail-closed ""
    # salvo que un test los sobreescriba explícitamente). Revertido
    # automáticamente al final de cada test.
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "Human Capital Test", "first_name": "Human Capital", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ============================================================================
# 1/2 — Namespace canónico /cchh, sin sesión -> redirect
# ============================================================================

def test_get_anonymous_redirects_to_login_preserving_canonical_next():
    anon = TestClient(app)
    response = anon.get("/cchh", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Fcchh"


def test_get_authorized_returns_200_at_canonical_namespace():
    response = _client_as(_AUTHORIZED_EMAIL).get("/cchh")
    assert response.status_code == 200


# ============================================================================
# 3 — Autenticado pero no listado -> 403 (fail-closed, MISMO contrato que
#     Commercial/Operations — nunca 404/redirect silencioso)
# ============================================================================

def test_authenticated_but_unlisted_user_is_denied_with_403():
    response = _client_as("unlisted@danwor.com").get("/cchh")
    assert response.status_code == 403


def test_human_capital_allowed_users_blank_denies_the_previously_authorized_user(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")
    response = _client_as(_AUTHORIZED_EMAIL).get("/cchh")
    assert response.status_code == 403


def test_operations_or_commercial_listing_alone_never_grants_human_capital_access(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    response = _client_as(_AUTHORIZED_EMAIL).get("/cchh")
    assert response.status_code == 403


# ============================================================================
# 4 — Reutiliza shell/topbar/loader canónico del Hub (MISMO DOM, no una copia)
# ============================================================================

def test_shell_reuses_the_canonical_app_shell_and_topbar_macro():
    response = _client_as(_AUTHORIZED_EMAIL).get("/cchh")
    text = response.text
    assert 'class="app-shell no-copilot"' in text
    assert '<header class="topbar">' in text
    assert 'class="module-content"' in text
    assert 'data-resize="left"' in text


def test_shell_reuses_the_exact_same_canonical_progress_dom():
    response = _client_as(_AUTHORIZED_EMAIL).get("/cchh")
    assert response.text.count(
        '<div class="read-progress" aria-hidden="true"><span class="read-progress__fill"></span></div>'
    ) == 1


def test_landing_template_never_embeds_its_own_progress_markup():
    assert "read-progress" not in _LANDING_HTML
    assert "hub-progress" not in _LANDING_HTML


def test_shell_shows_workspace_switcher_when_multiple_workspaces_available():
    # human-capital-test@danwor.com está admitido a Copiloto Y Capital
    # Humano (fixture autouse) -- el switcher debe aparecer (>1 workspace).
    response = _client_as(_AUTHORIZED_EMAIL).get("/cchh")
    assert '<details class="workspace-switch">' in response.text


def test_cchh_root_redirects_to_candidatos_entry():
    # Landing "01 Inicio" retirado por ahora (owner): /cchh arranca en Candidatos.
    r = _client_as(_AUTHORIZED_EMAIL).get("/cchh", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/cchh/candidatos"
    # El nav ya no ofrece "Inicio"; Candidatos es la primera entrada, activa.
    text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos").text
    assert "<strong>Inicio</strong>" not in text
    assert '<a href="/cchh/candidatos" class="module-link is-active">' in text


def test_landing_has_no_own_css_or_js_asset_link():
    # Phase 1 explícito: "NO agregar CSS. NO agregar JS." -- la landing no
    # debe linkear ningún stylesheet/script propio de Capital Humano (a
    # diferencia de commercial/crm.html, que sí carga commercial.css/js).
    assert "human_capital.css" not in _LANDING_HTML
    assert "human_capital.js" not in _LANDING_HTML
    assert "<link rel=\"stylesheet\"" not in _LANDING_HTML
    assert "<script src=" not in _LANDING_HTML


def test_content_shows_expected_eyebrow_title_without_banned_words():
    # Con la landing retirada, la entrada del workspace es Candidatos (Base de
    # CVs) — sigue con el eyebrow "Capital Humano" y sin palabras prohibidas.
    text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos").text
    assert '<p class="eyebrow">Capital Humano</p>' in text
    assert "<h1>Base de CVs</h1>" in text
    for banned in ("Preview", "PoC", "Prototype", "Coming Soon", "preview", "coming soon"):
        assert banned not in text


def test_content_never_lists_functional_capabilities_as_if_available():
    # "NO listar funcionalidades todavía como si estuvieran disponibles":
    # ninguna capability hipotética debe aparecer como TAB de nav. Se testea
    # sobre la entrada real del workspace (Candidatos), y contra el FORMATO de
    # link de nav (`<strong>…</strong>`) -- así "Indicadores" (rótulo de un
    # filtro real) o "Procesos" (dominio de Búsquedas) no dan falsos positivos:
    # lo que se prohíbe es que exista un TAB para una capability no construida.
    text = _client_as(_AUTHORIZED_EMAIL).get("/cchh/candidatos").text
    assert "<strong>Candidatos</strong>" in text  # nav real, no un placeholder
    assert "<strong>Búsquedas</strong>" in text   # nav real, no un placeholder
    assert "<strong>Entrevistas</strong>" in text  # nav real, no un placeholder (ver test_human_capital_entrevistas.py)
    assert "<strong>Onboarding</strong>" in text  # nav real, no un placeholder (ver test_human_capital_onboarding.py)
    assert "<strong>Avisos</strong>" in text  # nav real, no un placeholder (ver test_human_capital_publicaciones.py)
    assert "<strong>Indicadores</strong>" in text  # nav real, no un placeholder (ver test_human_capital_indicadores_view.py)
    for capability in (
        "Administración", "Inicio", "Perfiles",
    ):
        assert f"<strong>{capability}</strong>" not in text


# ============================================================================
# 5 — Capital Humano NO tiene auth propio
# ============================================================================

def test_human_capital_router_never_defines_its_own_session_primitives():
    for banned in ("COOKIE_NAME", "create_session(", "decode_session(", "def session_user", "jwt.encode", "jwt.decode"):
        assert banned not in _HUMAN_CAPITAL_PY


def test_human_capital_access_reuses_auth_service_predicate_not_a_new_one():
    assert "auth_service.is_authorized" in _HUMAN_CAPITAL_ACCESS_PY
    assert "auth_service.is_human_capital_user" in _HUMAN_CAPITAL_ACCESS_PY
    for banned in ("jwt.encode", "jwt.decode", "COOKIE_NAME ="):
        assert banned not in _HUMAN_CAPITAL_ACCESS_PY


def test_human_capital_router_reuses_the_shared_login_redirect():
    # Chequea la LLAMADA real (paréntesis de invocación), no prosa de
    # comentario que legítimamente puede nombrar `nav.login_redirect` al
    # explicar por qué NO se usa acá (MISMO criterio que
    # test_commercial_nav_never_leaks_operations_markup, que chequea
    # markup renderizado en vez de texto de comentario).
    assert "workspaces.platform_login_redirect(request" in _HUMAN_CAPITAL_PY
    assert "nav.login_redirect(" not in _HUMAN_CAPITAL_PY


# ============================================================================
# Scope guards adicionales (Phase 1 §15) -- Supabase / lógica legacy /
# roles / module id
# ============================================================================

def test_human_capital_surface_has_no_supabase_import():
    # Chequea IMPORTS/paquetes reales, no prosa de comentario que
    # legítimamente menciona "Supabase" al explicar qué NO se porta en esta
    # fase (MISMO criterio que los tests de markup renderizado arriba).
    for surface in (_HUMAN_CAPITAL_PY, _HUMAN_CAPITAL_ACCESS_PY):
        assert "import supabase" not in surface
        assert "from supabase" not in surface
    assert "@supabase/" not in _LANDING_HTML
    assert "@supabase/" not in _HUMAN_CAPITAL_NAV_HTML


def test_human_capital_surface_has_no_legacy_role_literal():
    # Chequea el LITERAL de rol tal como aparecería en código real
    # (comillas incluidas), no prosa de comentario que legítimamente
    # nombra hrbp/hiring_manager al explicar que NO se implementan todavía.
    for surface in (_HUMAN_CAPITAL_PY, _HUMAN_CAPITAL_ACCESS_PY):
        assert '"hrbp"' not in surface and "'hrbp'" not in surface
        assert '"hiring_manager"' not in surface and "'hiring_manager'" not in surface


def test_human_capital_router_never_calls_log_activity_with_a_module_id():
    # Deliberado (ver Phase 1 forensic): `log_activity(module=...)` es
    # OPCIONAL -- Capital Humano no lo llama en absoluto en esta fase, así
    # que nunca necesita un module id de CHxx. El propio comentario del
    # router SÍ nombra `CM01_MODULE_ID`/`module_registry.py` en prosa, al
    # explicar por qué Capital Humano no los usa -- por eso este test
    # chequea la ausencia de la LLAMADA real, no de esas palabras sueltas.
    assert "log_activity(" not in _HUMAN_CAPITAL_PY


def test_human_capital_router_has_zero_business_data_fetch():
    # Router liviano: ninguna consulta a datos de negocio -- ni Supabase, ni
    # ningún .service/.repository de Capital Humano (no existen todavía).
    for banned in (".from(", "select(", "CandidatoRepository", "ProcesoRepository", "HumanCapitalService"):
        assert banned not in _HUMAN_CAPITAL_PY


# ============================================================================
# 7 — Copiloto/Operations/Commercial intactos, sin fuga de markup
# ============================================================================

def test_operations_nav_never_leaks_human_capital_markup():
    assert "human-capital" not in _OPERATIONS_NAV_HTML
    assert "Capital Humano" not in _OPERATIONS_NAV_HTML


def test_commercial_nav_never_leaks_human_capital_markup():
    assert "human-capital" not in _COMMERCIAL_NAV_HTML
    assert "Capital Humano" not in _COMMERCIAL_NAV_HTML


def test_human_capital_nav_never_leaks_operations_or_commercial_markup():
    # Chequea el MARKUP renderizado real (nav-kicker + module-link), no
    # prosa de comentario que legítimamente puede nombrar Operations/
    # Comercial al explicar el patrón que este partial sigue (MISMO
    # criterio que test_commercial_nav_never_leaks_operations_markup).
    assert '<strong>Asignaciones</strong>' not in _HUMAN_CAPITAL_NAV_HTML
    assert '<strong>CRM</strong>' not in _HUMAN_CAPITAL_NAV_HTML
    assert '<p class="nav-kicker sidebar-workspace-kicker">OPERACIONES</p>' not in _HUMAN_CAPITAL_NAV_HTML
    assert '<p class="nav-kicker sidebar-workspace-kicker">COMERCIAL</p>' not in _HUMAN_CAPITAL_NAV_HTML


def test_root_copilot_route_still_serves_copilot_unchanged(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    response = _client_as("copilot-only@danwor.com").get("/")
    assert response.status_code == 200
    assert "Capital Humano" not in response.text


def test_operaciones_and_comercial_namespaces_unaffected_by_new_route():
    response = TestClient(app).get("/comercial", follow_redirects=False)
    assert response.status_code == 303  # sin sesión, MISMO contrato de siempre
    assert response.headers["location"] == "/platform?next=%2Fcomercial"
