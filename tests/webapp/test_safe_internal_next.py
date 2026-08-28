"""
test_safe_internal_next.py — `next` INTERNO: autoridad única y contrato de
seguridad (owner-authorized security corrective).

La auditoría del logout preserve-location encontró un open redirect REAL y
PREEXISTENTE: la validación de `next` estaba copiada en tres lugares
(routers/landing.py, y dos veces en routers/platform.py) como

    next if next.startswith("/") and not next.startswith("//") else ""

y esa regla ACEPTABA destinos que el browser resuelve a un host ajeno. Medido
con `a.href` sobre un documento del Hub:

    "/\\evil.example"      ->  http://evil.example/     (el browser trata \\ como /)
    "/<TAB>/evil.example"  ->  http://evil.example/     (el browser borra el TAB)

Ahora hay UNA autoridad, `webapp.safe_redirect.safe_internal_next`, y estos
tests la prueban en aislamiento Y en las cuatro superficies HTTP que consumen
`next`, porque un helper correcto que alguien no llama no protege nada.

Los vectores externos de acá no son inventados: cada uno se verificó en el
browser resolviendo a un host ajeno antes de escribirlo.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.safe_redirect import safe_internal_next
from webapp.services import auth_service

_ROOT = Path(__file__).resolve().parents[2]

auth_service.ALLOWED_USERS = "ALL"
_SESSION = auth_service.create_session(
    {"email": "safe-next@danwor.com", "name": "Safe", "first_name": "Safe", "family_name": "Next"}
)

# Destinos internos legítimos del Hub.
VALID = [
    "/comercial",
    "/comercial?foo=bar",
    "/modules/x?pack=y",
    "/onedanwor",
    "/reporting?x=1",
    "/operaciones/staffing/asignaciones",
    "/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core",
]

# Destinos que NO deben sobrevivir. Cada uno con el motivo por el que el
# browser lo lleva afuera (o por el que no es un path).
HOSTILE = [
    ("https://evil.example", "esquema absoluto"),
    ("http://evil.example", "esquema absoluto"),
    ("https://evil.example/comercial", "esquema absoluto con path que parece nuestro"),
    ("//evil.example", "protocol-relative"),
    ("///evil.example", "protocol-relative con barra extra"),
    ("/\\evil.example", "el browser trata \\ como / -> //evil.example"),
    ("/\\\\evil.example", "idem, doble backslash"),
    ("\\evil.example", "arranca con backslash"),
    ("\\\\evil.example", "UNC -> file://evil.example/"),
    ("/\t/evil.example", "el browser BORRA el TAB -> //evil.example"),
    ("/\n/evil.example", "el browser BORRA el LF -> //evil.example"),
    ("/\r/evil.example", "el browser BORRA el CR -> //evil.example"),
    ("/\t\\evil.example", "TAB + backslash combinados"),
    (" //evil.example", "espacio inicial recortado por el browser"),
    ("javascript:alert(1)", "esquema no navegable"),
    ("JavaScript:alert(1)", "idem, otro casing"),
    ("data:text/html,<h1>x", "esquema data"),
    ("", "vacío"),
    ("comercial", "relativo sin barra"),
]


def _authed():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    return c


def _enc(value: str) -> str:
    """El mismo encoding que produce `URLSearchParams` en el browser."""
    return quote(value, safe="*-._")


def _data_home(body: str) -> str | None:
    m = re.search(r'data-home="([^"]*)"', body)
    return m.group(1) if m else None


def _is_external(target: str) -> bool:
    """¿Este destino saca al usuario del origen? Réplica de la semántica de
    parseo del browser que se midió en la auditoría."""
    probe = re.sub(r"^[\x00-\x20]+", "", re.sub(r"[\t\n\r]", "", target or "")).replace("\\", "/")
    return probe.startswith("//") or bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", probe))


# --- A: el helper en aislamiento -------------------------------------------

@pytest.mark.parametrize("value", VALID)
def test_helper_accepts_ordinary_hub_paths(value):
    assert safe_internal_next(value) == value


@pytest.mark.parametrize("value,motivo", HOSTILE)
def test_helper_rejects_every_measured_external_form(value, motivo):
    assert safe_internal_next(value) == "", motivo


def test_helper_returns_exactly_what_it_validated():
    # Nunca se valida una cadena y se emite otra: lo devuelto no puede ser
    # externo por construcción.
    for value, _ in HOSTILE:
        assert not _is_external(safe_internal_next(value))
    for value in VALID:
        assert not _is_external(safe_internal_next(value))


def test_helper_tolerates_none_like_the_empty_string():
    assert safe_internal_next(None) == ""


def test_helper_preserves_the_query_string_untouched():
    for value in ("/comercial?foo=bar", "/reporting?a=1&b=2", "/x?q=%2Fy", "/x?q=a+b"):
        assert safe_internal_next(value) == value


def test_backslash_regression_the_form_that_exposed_the_hole():
    # El caso puntual que la regla vieja aceptaba. Se deja explícito y aparte
    # de la tabla para que un futuro "simplifiquemos esto" lo rompa de frente.
    vieja_regla = lambda n: n if n.startswith("/") and not n.startswith("//") else ""
    assert vieja_regla("/\\evil.example") == "/\\evil.example"   # la aceptaba
    assert _is_external("/\\evil.example")                        # y era externa
    assert safe_internal_next("/\\evil.example") == ""            # ahora no
    # Y su gemelo, el que nadie había visto: tab dentro de la URL.
    assert vieja_regla("/\t/evil.example") == "/\t/evil.example"
    assert _is_external("/\t/evil.example")
    assert safe_internal_next("/\t/evil.example") == ""


# --- B: TODAS las superficies HTTP que consumen `next` ---------------------

# Las cuatro rutas que reciben `next` como query param. `_LANDING_SURFACES`
# renderizan la pantalla de acceso (se observa `data-home`); `/platform` es un
# alias que re-emite `next` (se observa el `Location`).
_LANDING_SURFACES = ["/", "/copilot", "/onedanwor"]
_ALL_SURFACES = _LANDING_SURFACES + ["/platform"]


def test_the_covered_surfaces_are_all_the_ones_that_consume_next():
    # Guardia de no-vacuidad: si mañana aparece una quinta ruta con `next`,
    # este test falla y obliga a cubrirla en vez de dejarla sin probar.
    found = set()
    for module in (_ROOT / "webapp" / "routers").glob("*.py"):
        src = module.read_text(encoding="utf-8")
        for chunk in src.split("@router.get(")[1:]:
            route = re.match(r'\s*"([^"]+)"', chunk)
            # Firma completa: el `)` de cierre es el que va seguido de `:`, no
            # el de un `Query(...)` interno.
            signature = re.search(r"\ndef \w+\((.*?)\)\s*(?:->[^:]+)?:", chunk, re.S)
            if route and signature and re.search(r"\bnext\b", signature.group(1)):
                found.add(route.group(1))
    assert found == set(_ALL_SURFACES), found


@pytest.mark.parametrize("surface", _LANDING_SURFACES)
@pytest.mark.parametrize("value,motivo", HOSTILE)
def test_landing_surfaces_never_emit_an_external_data_home(surface, value, motivo):
    anon = TestClient(app)
    response = anon.get(f"{surface}?next={_enc(value)}")
    assert response.status_code == 200
    home = _data_home(response.text)
    assert home is not None, (surface, value)
    assert not _is_external(home), (surface, value, motivo, home)
    assert "evil.example" not in response.text
    assert "javascript:alert" not in response.text


@pytest.mark.parametrize("surface", _LANDING_SURFACES)
@pytest.mark.parametrize("value,motivo", HOSTILE)
def test_authenticated_surfaces_never_redirect_outside(surface, value, motivo):
    response = _authed().get(f"{surface}?next={_enc(value)}", follow_redirects=False)
    location = response.headers.get("location", "")
    assert not _is_external(location), (surface, value, motivo, location)
    assert "evil.example" not in location


@pytest.mark.parametrize("value,motivo", HOSTILE)
def test_platform_alias_never_forwards_an_external_next(value, motivo):
    anon = TestClient(app)
    response = anon.get(f"/platform?next={_enc(value)}", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    # El alias descarta el destino y manda a la entrada pelada.
    assert location == "/onedanwor", (value, motivo, location)


@pytest.mark.parametrize("surface", _LANDING_SURFACES)
@pytest.mark.parametrize("value", VALID)
def test_valid_next_still_round_trips_on_every_surface(surface, value):
    anon = TestClient(app)
    response = anon.get(f"{surface}?next={_enc(value)}")
    assert response.status_code == 200
    assert _data_home(response.text) == value
    # Y autenticado resuelve al mismo destino por redirect.
    authed = _authed().get(f"{surface}?next={_enc(value)}", follow_redirects=False)
    assert authed.status_code == 307
    assert authed.headers["location"] == value


@pytest.mark.parametrize("value", VALID)
def test_platform_alias_preserves_a_valid_next(value):
    anon = TestClient(app)
    response = anon.get(f"/platform?next={_enc(value)}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == f"/onedanwor?next={quote(value, safe='')}"


def test_surfaces_without_next_keep_their_own_default():
    anon = TestClient(app)
    assert _data_home(anon.get("/onedanwor").text) == "/onedanwor"
    for surface in ("/", "/copilot"):
        home = _data_home(anon.get(surface).text)
        assert home is not None and home.startswith("/home?pack="), (surface, home)
    assert anon.get("/platform", follow_redirects=False).headers["location"] == "/onedanwor"


# --- C: sigue habiendo UNA sola autoridad ---------------------------------

def test_the_validation_lives_in_exactly_one_place():
    # Ninguna copia de la expresión suelta: si vuelve, vuelve el agujero.
    offenders = []
    for path in (_ROOT / "webapp").rglob("*.py"):
        if path.name == "safe_redirect.py":
            continue
        src = path.read_text(encoding="utf-8")
        code = re.sub(r'"""(?:.|\n)*?"""', "", src)
        code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
        if 'startswith("//")' in code or "startswith('//')" in code:
            offenders.append(str(path.relative_to(_ROOT)))
    assert not offenders, offenders


def test_every_consumer_goes_through_the_helper():
    for module in ("landing.py", "platform.py"):
        src = (_ROOT / "webapp" / "routers" / module).read_text(encoding="utf-8")
        assert "from webapp.safe_redirect import safe_internal_next" in src, module
        assert src.count("safe_next = safe_internal_next(next)") >= 1, module


def test_the_helper_stays_a_leaf_with_no_webapp_dependencies():
    # Es una primitive de seguridad: cualquiera tiene que poder importarla sin
    # arrastrar routers, servicios ni riesgo de ciclo.
    src = (_ROOT / "webapp" / "safe_redirect.py").read_text(encoding="utf-8")
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    assert "webapp" not in code
    assert "fastapi" not in code


def test_the_client_still_does_not_duplicate_the_server_rule():
    # El logout manda la ubicación tal cual; la autoridad sigue siendo el
    # server (ver test_hub_logout_next.py). Un chequeo propio en el cliente
    # serían dos reglas que pueden divergir.
    app_js = (_ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    fn = "\n".join(l for l in app_js.splitlines() if not l.strip().startswith("//")) \
        .split("window.dwLogout = function () {", 1)[1].split("\n};", 1)[0]
    assert "startsWith" not in fn
    assert "replace(" not in fn
