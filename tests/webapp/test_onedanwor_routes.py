"""
test_onedanwor_routes.py — Contrato HTTP de `GET /onedanwor`, la entrada
canónica user-facing de plataforma (batch One Danwor entry experience,
sucesor de `/platform` como entrada real — ver test_platform_routes.py
para el alias de compatibilidad).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import platform as platform_router
from webapp.services import auth_service
from webapp import workspaces

auth_service.ALLOWED_USERS = "ALL"
# Operations Workspace Access Gate (ver HANDOFF): tier "full" ya no basta
# por sí solo — este archivo prueba resolución/selector de plataforma, no
# la frontera de autorización en sí (ver test_operations_access.py), así
# que el cliente full de módulo se abre explícito listando su email —
# "ALL" ya NO es un wildcard válido para Operations (rechazado
# deliberadamente, ver auth_service._operations_set). MISMO valor CSV que
# usan los otros archivos de test para esta variable (asignación plana de
# módulo: la última que se ejecute durante el collection de pytest "gana"
# para cualquier test sin su propio override).
auth_service.OPERATIONS_ALLOWED_USERS = (
    "staffing-test@danwor.com,topbar-full@danwor.com,onedanwor-full@danwor.com"
)

_FULL_SESSION = auth_service.create_session(
    {"email": "onedanwor-full@danwor.com", "name": "Full", "first_name": "Full", "family_name": "User"}
)


def _full_client():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _FULL_SESSION)
    return c


def _academy_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    # Sin Operations explícito para ESTE usuario — a diferencia del "ALL"
    # de módulo (pensado para el cliente full), este fixture sigue
    # probando el caso "single workspace" (solo Copilot).
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    session = auth_service.create_session(
        {"email": "onedanwor-academy@danwor.com", "name": "Academy", "first_name": "Academy", "family_name": "User"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# --- A: unauthenticated ---------------------------------------------------

def test_unauthenticated_renders_onedanwor_login():
    anon = TestClient(app)
    response = anon.get("/onedanwor")
    assert response.status_code == 200
    # Danwor Hub Login Identity Alignment (ver HANDOFF): la landing
    # anónima ya no dice "One Danwor" — dice Danwor Hub, igual que el
    # resto de la plataforma autenticada.
    assert "Danwor Hub" in response.text
    assert "One Danwor" not in response.text
    assert 'id="microsoft-login-btn"' in response.text
    # Auth boundary intacto: mismo botón, mismo target, mismo flujo.
    assert 'onclick="loginWithMicrosoft(event)"' in response.text
    assert 'data-home="/onedanwor"' in response.text
    assert "Ingresar con tu cuenta Danwor" in response.text
    # Los 3 bullets truthful de Danwor Hub (sin cambios de contenido).
    assert "Un único acceso con tu cuenta Danwor" in response.text
    assert "Espacios de trabajo según tu perfil" in response.text
    assert "Navegación integrada entre servicios" in response.text


def test_onedanwor_login_reuses_the_canonical_hub_icon_not_a_second_svg():
    # Danwor Hub Login — Canonical Brand Lockup Corrective (ver HANDOFF):
    # el batch anterior solo alineó el COPY ("Danwor Hub" en vez de "One
    # Danwor") pero dejó el wordmark histórico de dos líneas sin ícono.
    # Ahora la landing anónima consume el MISMO `hub_brand_lockup()` que
    # el hero autenticado — el ícono canónico de 4 cuadrados está
    # presente, reusado (nunca un segundo <svg> recreado a mano).
    anon = TestClient(app)
    response = anon.get("/onedanwor")
    assert response.status_code == 200
    assert response.text.count('<rect x="3" y="3" width="7" height="7" rx="1.5"/>') == 1


def test_onedanwor_login_renders_canonical_kicker_and_one_line_title():
    anon = TestClient(app)
    response = anon.get("/onedanwor")
    assert response.status_code == 200
    # Shared Hub Copy Alignment (ver HANDOFF): "Todos nuestros servicios"
    # es el copy DEFINITIVO del eyebrow (era "Todos los servicios Danwor").
    assert "Una plataforma · Todos nuestros servicios" in response.text
    assert '<h1><span class="hub-hero__icon">' in response.text
    assert "</span>Danwor Hub</h1>" in response.text


def test_onedanwor_login_no_longer_uses_historical_split_wordmark_or_kicker():
    # El kicker histórico vivía hardcodeado en landing.html, INDEPENDIENTE
    # del dict de copy — por eso el batch anterior (que solo tocó
    # `_ONEDANWOR_LANDING_COPY`) no lo alineó. El logo de marca en la
    # pantalla de intro SÍ sigue diciendo "Data & AI for Business" en su
    # `alt` — eso es la tagline genérica de la compañía, comparte esa
    # pantalla con el Copiloto, y no es lo que este batch corrige.
    anon = TestClient(app)
    response = anon.get("/onedanwor")
    assert response.status_code == 200
    assert '<p class="kicker">Data &amp; AI for Business</p>' not in response.text
    assert "<h1>Danwor<span>Hub</span></h1>" not in response.text
    # El logo de marca de la pantalla de intro (compartida con el
    # Copiloto, no parte del bloque de identidad Hub) SÍ sigue con su
    # tagline genérica en el `alt` — no se tocó, y no debería.
    assert 'alt="Danwor — Data & AI for Business"' in response.text


def test_onedanwor_identity_has_no_copilot_specific_branding():
    anon = TestClient(app)
    response = anon.get("/onedanwor")
    assert "Copiloto Metodológico" not in response.text
    assert "Arquitecto Danwor" not in response.text
    assert "SAP Datasphere" not in response.text


def test_auth_popup_state_serves_neutral_document_not_rich_landing():
    # Corrective pass: un overlay sobre la landing no alcanza — el popup de
    # Microsoft (y su callback, fijo en "/") debe recibir un documento
    # separado, sin el copy/branding RICO de ninguna landing específica
    # (nunca "Una plataforma"/checklist/wordmark de página, nunca
    # Copiloto). Corrective posterior (Danwor Hub Login Identity
    # Alignment, ver HANDOFF): el <title> de ESE documento neutro SÍ dice
    # "Danwor Hub" — el paraguas de plataforma, no un producto específico
    # ni el histórico "One Danwor" — para que ningún paso de la app
    # durante el login se sienta desalineado con la identidad Hub.
    anon = TestClient(app)
    response = anon.get("/onedanwor?auth_popup=1")
    assert response.status_code == 200
    assert "Danwor Hub" in response.text
    assert "One Danwor" not in response.text
    assert "Copiloto Metodológico" not in response.text
    assert "Una plataforma" not in response.text
    assert 'class="auth-completing"' in response.text
    assert "Completando acceso" in response.text


def test_code_callback_state_serves_same_neutral_document_with_hub_identity():
    # El redirect_uri de Microsoft está fijo en el origen raíz ("/"), así
    # que el callback real casi siempre vuelve por ahí — pero
    # `render_landing` intercepta `code=` sin importar QUÉ ruta lo llame,
    # así que `/onedanwor?code=...` (navegación directa/futura) debe
    # comportarse igual: mismo documento neutro, misma identidad Danwor
    # Hub, cero copy rico de ninguna landing específica.
    anon = TestClient(app)
    response = anon.get("/onedanwor?code=fake-oauth-code&state=fake-state")
    assert response.status_code == 200
    assert "Danwor Hub" in response.text
    assert "One Danwor" not in response.text
    assert "Copiloto Metodológico" not in response.text
    assert "Una plataforma" not in response.text
    assert 'class="auth-completing"' in response.text


def test_valid_next_survives_login_flow_via_existing_mechanism():
    anon = TestClient(app)
    response = anon.get("/onedanwor?next=/operations/staffing")
    assert response.status_code == 200
    assert 'data-home="/operations/staffing"' in response.text


def test_unsafe_next_falls_back_to_onedanwor_default_home():
    anon = TestClient(app)
    response = anon.get("/onedanwor?next=//evil.com")
    assert response.status_code == 200
    assert 'data-home="/onedanwor"' in response.text


# --- D/E/F/G: authenticated workspace resolution --------------------------

def test_authenticated_with_one_workspace_redirects_directly(monkeypatch):
    academy = _academy_client(monkeypatch)
    response = academy.get("/onedanwor", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == workspaces.COPILOT.home_url


def test_full_tier_without_operations_listing_also_sees_only_copilot(monkeypatch):
    # Operations Workspace Access Gate (items N/O del batch): tier "full"
    # NUNCA es la fuente de esta autorización — un usuario full admitido
    # pero NO listado en OPERATIONS_ALLOWED_USERS ve exactamente lo mismo
    # que uno academy sin listar: Danwor Hub lo manda derecho a Copiloto,
    # Operaciones nunca aparece (ni en el selector, ni en el switcher).
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    response = _full_client().get("/onedanwor", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == workspaces.COPILOT.home_url


def test_authenticated_with_zero_workspaces_shows_no_access(monkeypatch):
    monkeypatch.setattr(platform_router, "available_workspaces", lambda user: [])
    response = _full_client().get("/onedanwor")
    assert response.status_code == 200
    assert "no tiene acceso habilitado" in response.text.lower()


def test_authenticated_with_two_workspaces_sees_rich_selector():
    response = _full_client().get("/onedanwor")
    assert response.status_code == 200
    # Tarjeta Copilot: eyebrow/título/acento/descripción/features reales.
    assert "Copiloto Metodológico" in response.text
    assert "Arquitecto Danwor" in response.text
    assert "Marco metodológico navegable" in response.text
    assert "Árboles de decisión y asistencia IA" in response.text
    assert "Criterios técnicos para proyectos SAP" in response.text
    # Tarjeta Operaciones: NO es solo "Staffing" — describe alcance real.
    assert "Gestión Inteligente" in response.text
    assert "Operaciones" in response.text
    assert "Staffing y asignaciones" in response.text
    assert "Gestión de proyectos" in response.text
    assert "Analítica operativa" in response.text
    # Los destinos siguen siendo Workspace.home_url — nunca hardcodeados.
    assert f'href="{workspaces.COPILOT.home_url}"' in response.text
    assert f'href="{workspaces.OPERATIONS.home_url}"' in response.text


def test_authenticated_selector_shows_danwor_hub_hero_not_repeated_card_eyebrow():
    # Revisión visual Danwor Hub (ver HANDOFF): el hero autenticado dice
    # "Danwor Hub" con el ícono canónico (`hub_icon()`) — un wordmark
    # distinto del de la landing anónima (que usa el mismo `<h1>+<span>`
    # de texto que el Copiloto, sin ícono; ver Danwor Hub Login Identity
    # Alignment y test_unauthenticated_renders_onedanwor_login, arriba —
    # ambas pantallas dicen "Danwor Hub" hoy, pero con presentación
    # distinta). Las cards NO repiten más el eyebrow "Data & AI for
    # Business" arriba de cada título — la jerarquía visual del hero ya lo
    # comunica.
    response = _full_client().get("/onedanwor")
    assert response.status_code == 200
    text = response.text
    assert "Danwor Hub" in text
    assert 'class="glass access-brand hub-hero"' in text
    # Shared Hub Copy Alignment (ver HANDOFF): "Todos nuestros servicios".
    assert "Una plataforma · Todos nuestros servicios" in text
    assert 'class="hub-hero__icon"' in text
    assert "Data & AI for Business" not in text


def test_authenticated_explicit_next_takes_precedence_over_selector():
    response = _full_client().get("/onedanwor?next=/operations/staffing", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/operations/staffing"


def test_selector_renders_only_workspaces_returned_by_resolver(monkeypatch):
    # La presentación rica NO reemplaza la autorización: un workspace sin
    # entrada en el mapa de copy (`_WORKSPACE_CARD_COPY`) todavía se
    # muestra (con fallback a su `label`) si el resolver lo devuelve — la
    # fuente de autorización/identidad sigue siendo `Workspace`, no el mapa
    # de presentación.
    fake_ws = workspaces.Workspace(
        key="only-one", label="Solo Uno", subtitle="Test", home_url="/solo-uno",
        is_authorized=lambda user: True,
    )
    monkeypatch.setattr(platform_router, "available_workspaces", lambda user: [fake_ws, workspaces.COPILOT])
    response = _full_client().get("/onedanwor")
    assert response.status_code == 200
    assert "Solo Uno" in response.text
    assert 'href="/solo-uno"' in response.text


def test_zero_workspaces_gets_controlled_no_access_state(monkeypatch):
    monkeypatch.setattr(platform_router, "available_workspaces", lambda user: [])
    response = _full_client().get("/onedanwor")
    assert response.status_code == 200
    assert "no tiene acceso habilitado" in response.text.lower()
    # Shared Hub Copy Alignment (ver HANDOFF): platform_no_access.html
    # hardcodea el mismo kicker INDEPENDIENTEMENTE del macro
    # `hub_brand_lockup()` — verificado acá para que una futura
    # consolidación no lo pierda de vista.
    assert "Una plataforma · Todos nuestros servicios" in response.text


# --- H/I: Copilot entries stay unchanged ----------------------------------

def test_root_still_serves_copilot_unchanged():
    anon = TestClient(app)
    response = anon.get("/")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text
    assert "One Danwor" not in response.text


def test_copilot_alias_still_serves_copilot_unchanged():
    anon = TestClient(app)
    response = anon.get("/copilot")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text
    assert "One Danwor" not in response.text


# --- Operations deep-link round trip, through BOTH hops -------------------

def test_operations_deep_link_round_trip_through_platform_and_onedanwor():
    # Cadena completa: /operations/staffing sin sesión → /platform?next=...
    # (verificado en test_staffing_routes.py, sin cambios en este batch) →
    # /platform redirige 307 a /onedanwor?next=... (verificado en
    # test_platform_routes.py) → /onedanwor preserva ese next en data-home
    # mientras no hay sesión → autenticado, /onedanwor con ese mismo next
    # redirige DIRECTO a /operations/staffing (nunca al selector).
    anon = TestClient(app)
    via_platform = anon.get("/platform?next=/operations/staffing", follow_redirects=False)
    assert via_platform.headers["location"] == "/onedanwor?next=%2Foperations%2Fstaffing"

    unauth = anon.get("/onedanwor?next=/operations/staffing")
    assert 'data-home="/operations/staffing"' in unauth.text

    authenticated = _full_client().get("/onedanwor?next=/operations/staffing", follow_redirects=False)
    assert authenticated.status_code == 307
    assert authenticated.headers["location"] == "/operations/staffing"
