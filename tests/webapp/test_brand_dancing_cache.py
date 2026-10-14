"""El logo animado (`/brand/dancing/{pack}`) es público y determinista: ETag por contenido + vigencia corta, 304 condicional, cuerpo y semántica intactos."""
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import pack_assets

PATH = "/brand/dancing/datasphere_core"
Q = {"title": "Copiloto", "subtitle": "SAP Datasphere"}


def _anon() -> TestClient:
    return TestClient(app)          # sin cookie de sesión: la ruta es pública hoy y debe seguir siéndolo


def test_response_carries_content_etag_and_short_public_freshness():
    r = _anon().get(PATH, params=Q)
    assert r.status_code == 200
    assert r.headers["etag"].startswith('"') and r.headers["etag"].endswith('"')
    assert r.headers["cache-control"] == f"public, max-age={pack_assets.DANCING_LOGO_MAX_AGE}"
    assert "immutable" not in r.headers["cache-control"]          # no es un asset versionado por URL: nunca immutable
    assert "set-cookie" not in r.headers and "vary" not in r.headers


def test_body_is_unchanged_by_the_caching_headers():
    r = _anon().get(PATH, params=Q)
    assert "Copiloto" in r.text and "SAP Datasphere" in r.text
    assert "#e4e4ec" in r.text and "#363636" not in r.text        # misma variante de paleta oscura que antes


def test_conditional_request_gets_304_without_body():
    c = _anon()
    first = c.get(PATH, params=Q)
    again = c.get(PATH, params=Q, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == first.headers["etag"]
    assert again.headers["cache-control"] == first.headers["cache-control"]


def test_stale_or_foreign_validator_gets_the_full_body():
    r = _anon().get(PATH, params=Q, headers={"If-None-Match": '"otro"'})
    assert r.status_code == 200 and len(r.content) > 1000
    multi = _anon().get(PATH, params=Q, headers={"If-None-Match": f'"x", {_anon().get(PATH, params=Q).headers["etag"]}'})
    assert multi.status_code == 304                               # lista de validadores: alcanza con que uno coincida


def test_etag_follows_the_rendered_content_not_the_url():
    a = _anon().get(PATH, params=Q).headers["etag"]
    b = _anon().get(PATH, params={"title": "Operaciones", "subtitle": "SAP Datasphere"}).headers["etag"]
    assert a != b                                                 # otro título => otro contenido => otro validador
    assert a == _anon().get(PATH, params=Q).headers["etag"]       # determinista


def test_unknown_pack_still_resolves_like_before():
    r = _anon().get("/brand/dancing/no_existe", params=Q)
    assert r.status_code in (200, 404) and (r.status_code == 404 or r.headers.get("etag"))
