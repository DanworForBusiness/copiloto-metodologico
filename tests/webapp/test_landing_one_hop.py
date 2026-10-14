"""P1: la raíz con sesión resuelve el destino post-login en UN salto (antes `/` → `/home` → módulo).

Todos los controles de `/home` se conservan: sesión, perfil de solo lectura → Academy, pack solo en modo diagnóstico, 503 sin módulos, `next` interno seguro."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.content_registry import DEFAULT_PACK
from webapp import access
from webapp.main import app
from webapp.nav import home_url
from webapp.routers import landing
from webapp.services import auth_service
from webapp.services.content_service import list_academy

FULL = "full@danwor.com"
RO = "ro@danwor.com"


def _client(email: str, *, diagnostic: bool = False) -> TestClient:
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "U", "first_name": "U", "family_name": "X"}))
    if diagnostic:
        c.cookies.set("dw-diagnostic", "on")
    return c


@pytest.fixture(autouse=True)
def profiles(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", FULL)                       # el dueño puede activar el modo diagnóstico
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", FULL)             # perfil full solo para FULL…
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")            # …y el resto entra como solo lectura (Academy)


def _expected_full(pack=DEFAULT_PACK):
    return home_url(pack)


def _expected_readonly(pack=DEFAULT_PACK):
    academy = [a for a in list_academy(pack) if not a.nav_hidden]
    return f"/academy/{academy[0].stem}?pack={pack}" if academy else home_url(pack)


# ══ P1 ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("surface", ["/", "/copilot"])
def test_authenticated_root_goes_straight_to_the_final_destination_in_one_hop(surface):
    r = _client(FULL).get(surface, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == _expected_full()
    assert "/home" not in r.headers["location"]
    chain = _client(FULL).get(surface, follow_redirects=True)
    assert [h.status_code for h in chain.history] == [307] and chain.status_code == 200          # un solo salto hasta la página (antes dos)


def test_the_destination_is_exactly_what_home_decides_for_every_profile():
    for email, expected in ((FULL, _expected_full()), (RO, _expected_readonly())):
        root = _client(email).get("/", follow_redirects=False).headers["location"]
        via_home = _client(email).get(f"/home?pack={DEFAULT_PACK}", follow_redirects=False).headers["location"]
        assert root == via_home == expected, (email, root, via_home, expected)


def test_readonly_profile_still_lands_on_academy_not_on_the_intro():
    r = _client(RO).get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == _expected_readonly() and "/academy/" in r.headers["location"]


def test_the_pack_is_honoured_only_in_diagnostic_mode_exactly_like_home():
    from core.content_registry import available_content_packs
    other = next((p for p in available_content_packs() if p != DEFAULT_PACK), None)
    plain = _client(FULL).get(f"/?pack={other or DEFAULT_PACK}", follow_redirects=False).headers["location"]
    assert f"pack={DEFAULT_PACK}" in plain                                                       # sin modo diagnóstico el marco es siempre el productivo
    diag_root = _client(FULL, diagnostic=True).get(f"/?pack={other or DEFAULT_PACK}", follow_redirects=False).headers["location"]
    diag_home = _client(FULL, diagnostic=True).get(f"/home?pack={other or DEFAULT_PACK}", follow_redirects=False).headers["location"]
    assert diag_root == diag_home


def test_unauthenticated_visitors_still_get_the_landing_and_home_still_requires_a_session():
    anon = TestClient(app)
    r = anon.get("/", follow_redirects=False)
    assert r.status_code == 200 and 'data-home="/home?pack=' in r.text                            # el botón de acceso conserva su destino post-login
    h = anon.get("/home", follow_redirects=False)
    assert h.status_code == 307 and h.headers["location"].startswith("/?") and "next=%2Fhome" in h.headers["location"] and h.headers["location"].endswith("#acceso")
    assert anon.get("/copilot", follow_redirects=False).status_code == 200


def test_a_valid_next_keeps_precedence_and_a_hostile_one_falls_back_to_the_destination():
    c = _client(FULL)
    assert c.get("/?next=/modules/01_Arquitectura_y_Conectividad%3Fpack%3Ddatasphere_core", follow_redirects=False).headers["location"].startswith("/modules/01_")
    for hostile in ("//evil.example/x", "https://evil.example", "javascript:alert(1)", "/\\evil.example"):
        loc = c.get("/", params={"next": hostile}, follow_redirects=False).headers["location"]
        assert loc == _expected_full() and "evil" not in loc, hostile


def test_session_with_auth_popup_state_behaves_as_before():
    r = _client(FULL).get("/?auth_popup=1", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == _expected_full()                     # con sesión la raíz siempre redirige (el popup sin sesión sirve el documento neutro)


def test_no_modules_is_the_same_503_on_both_entry_points(monkeypatch):
    def boom(_pack):
        raise HTTPException(503, "No hay módulos configurados")
    monkeypatch.setattr(landing, "home_url", boom)
    assert _client(FULL).get("/", follow_redirects=False).status_code == 503
    assert _client(FULL).get("/home", follow_redirects=False).status_code == 503


def test_there_is_a_single_authority_for_the_post_login_destination():
    import inspect
    assert "home_destination(" in inspect.getsource(landing.home) and "resolve_home" in inspect.getsource(landing.render_landing)
    assert inspect.getsource(landing.landing).count("home_destination(") == 1 and inspect.getsource(landing.copilot_entry).count("home_destination(") == 1
