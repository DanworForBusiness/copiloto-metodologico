"""
test_hub_logout_next.py — HUB: PRESERVE CURRENT LOCATION ACROSS LOGOUT / LOGIN
(owner contract).

Antes, el logout navegaba a "/" a secas: se perdía desde dónde se había salido
y, al volver a autenticarse, el usuario aterrizaba siempre en el destino por
defecto de su perfil (típicamente el Copiloto) aunque hubiera salido de otro
workspace. El fix es una sola línea en el chrome COMPARTIDO (`dwLogout`, en
app.js): el destino se deriva de la ubicación actual y viaja en `next`.

`next` NO es un mecanismo nuevo — es el mismo del camino inverso (entrar sin
sesión a una página protegida, `workspaces.platform_login_redirect`), y su
VALIDACIÓN vive en el server. Esa validación ya tiene su propia cobertura
(test_onedanwor_routes.py, test_platform_routes.py) y acá NO se re-implementa.

Reparto: las invariantes de construcción del `next` (encoding, relatividad,
transversalidad, ausencia de estado) viven en hub_logout_next.node.js, que
ejecuta el `dwLogout` REAL. Acá quedan (a) el viaje de ida y vuelta contra el
server real con la URL EXACTA que produce el cliente, y (b) las de ARCHIVO:
que el fix es del chrome compartido y que no cambió la semántica de auth.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "hub_logout_next.node.js"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")

auth_service.ALLOWED_USERS = "ALL"
_SESSION = auth_service.create_session(
    {"email": "logout-next@danwor.com", "name": "Logout", "first_name": "Logout", "family_name": "User"}
)


def _js_code(raw: str) -> str:
    """JS sin comentarios: el bloque que documenta este contrato cita a
    propósito los literales que estos tests prohíben (patrón ya conocido en
    este repo)."""
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


def _logout_fn(raw: str) -> str:
    return _js_code(raw).split("window.dwLogout = function () {", 1)[1].split("\n};", 1)[0]


# Rutas reales del Hub, de tres verticales distintas. NINGUNA está nombrada en
# el código del logout: es la lista de este test, no una rama del producto.
_HUB_LOCATIONS = [
    "/comercial",
    "/reporting",
    "/copilot",
    "/operaciones/staffing/asignaciones",
    "/modules/01_Arquitectura_y_Conectividad",
    "/comercial?tipo=nn",
    "/reporting?view=foo",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_logout_next_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Hub logout-next invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB LOGOUT NEXT INVARIANTS PASSED" in result.stdout


# --- A: el viaje completo, con la URL exacta que arma el cliente -----------

@pytest.mark.parametrize("location", _HUB_LOCATIONS)
def test_logout_target_brings_the_user_back_to_where_they_were(location):
    # La URL que produce `dwLogout` para esa ubicación (mismo encoding que
    # `URLSearchParams`: todo percent-encoded salvo alfanuméricos y `*-._`).
    target = f"/onedanwor?next={quote(location, safe='*-._')}"
    anon = TestClient(app)
    response = anon.get(target)
    assert response.status_code == 200
    # El login que ve el usuario ya apunta de vuelta a donde estaba: `data-home`
    # es lo que `auth.js::homeUrl()` navega tras autenticarse.
    assert f'data-home="{location}"' in response.text, target
    # Y autenticado, el mismo destino resuelve por redirect en vez de landing.
    authed = TestClient(app)
    authed.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    redirect = authed.get(target, follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == location


def test_logout_without_next_keeps_the_current_default_destination():
    # Entrar directo a /onedanwor (sin `next`) no cambió: el default sigue
    # siendo el propio endpoint, que recién autenticado resuelve workspaces.
    anon = TestClient(app)
    response = anon.get("/onedanwor")
    assert response.status_code == 200
    assert 'data-home="/onedanwor"' in response.text


# --- B: no se abrió un open redirect --------------------------------------

@pytest.mark.parametrize("hostile", [
    "https://evil.example",
    "//evil.example",
    "//evil.example/x",
    "javascript:alert(1)",
    "http://evil.example/comercial",
])
def test_hostile_next_never_produces_an_external_destination(hostile):
    target = f"/onedanwor?next={quote(hostile, safe='*-._')}"
    anon = TestClient(app)
    response = anon.get(target)
    assert response.status_code == 200
    # Cae en el default del propio endpoint; el destino hostil no sobrevive.
    assert 'data-home="/onedanwor"' in response.text
    assert "evil.example" not in response.text
    assert "javascript:alert" not in response.text
    # Y autenticado tampoco redirige afuera.
    authed = TestClient(app)
    authed.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    redirect = authed.get(target, follow_redirects=False)
    location = redirect.headers.get("location", "")
    assert not location.startswith(("http://", "https://", "//", "javascript:")), location


def test_the_next_validation_stays_a_server_side_decision():
    # El cliente manda la ubicación tal cual y no duplica la regla: si acá
    # apareciera una lista blanca / un `startsWith("/")` propio, habría DOS
    # autoridades sobre el mismo contrato y podrían divergir.
    fn = _logout_fn(_APP_JS)
    for duplicated in ("startsWith", "indexOf(\"//\")", "http", "origin", "hostname", "host"):
        assert duplicated not in fn, duplicated


# --- C: el fix es del chrome compartido, no de una capability -------------

def test_logout_is_resolved_once_in_the_shared_chrome():
    # Un solo `dwLogout` y un solo botón que lo invoca, en el topbar
    # transversal — no hay una versión por workspace.
    assert _APP_JS.count("window.dwLogout = function () {") == 1
    topbar = (_ROOT / "webapp" / "templates" / "partials" / "platform_topbar.html").read_text(encoding="utf-8")
    assert topbar.count("dwLogout()") == 1
    # Ninguna vertical trae su propio logout.
    for vertical in ("commercial.js", "reporting.js", "staffing.js", "tools.js"):
        path = _STATIC / vertical
        if path.exists():
            assert "dwLogout" not in path.read_text(encoding="utf-8"), vertical


def test_logout_destination_is_derived_never_hardcoded_per_capability():
    fn = _logout_fn(_APP_JS)
    # El destino sale de la ubicación actual...
    assert "window.location.pathname" in fn
    assert "window.location.search" in fn
    # ...y la única ruta nombrada es la entrada del Hub.
    assert "/onedanwor" in fn
    for capability in ("/comercial", "/reporting", "/copilot", "/operaciones", "/modules", "/academy"):
        assert capability not in fn, capability
    # El "/" pelado de antes ya no está: era justo lo que perdía la ubicación.
    assert 'window.location.href = "/"' not in fn
    # Sin hash: el fragmento no viaja al server, incluirlo sería ruido.
    assert "location.hash" not in fn


def test_logout_adds_no_new_client_state_or_confirmation():
    fn = _logout_fn(_APP_JS)
    for banned in ("localStorage", "sessionStorage", "document.cookie",
                   "confirm(", "dialog", "showModal"):
        assert banned not in fn, banned


# --- D: auth/session sin cambios ------------------------------------------

def test_logout_endpoint_semantics_are_unchanged():
    # Sigue siendo el mismo POST que borra la cookie: el fix es puramente de
    # NAVEGACIÓN post-logout, del lado del cliente.
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    response = c.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_service.COOKIE_NAME in response.headers.get("set-cookie", "")
    # El endpoint no aprendió nada de `next` — no le corresponde.
    router = (_ROOT / "webapp" / "routers" / "auth.py").read_text(encoding="utf-8")
    logout = router.split("def auth_logout(", 1)[1].split("\n@", 1)[0]
    assert "next" not in logout
    assert "Redirect" not in logout


def test_logout_still_only_calls_the_logout_endpoint():
    fn = _logout_fn(_APP_JS)
    assert fn.count("fetch(") == 1
    assert '"/api/auth/logout"' in fn
    assert '"POST"' in fn
