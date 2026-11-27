"""Avatares transversales del Hub: prioridad personalizada → corporativa → iniciales; cada colaborador administra solo la suya; la foto
corporativa propia se sincroniza con `User.Read` (sin permisos nuevos) y como mucho una vez por día; Comunidad la usa sin llamar a Graph."""
from __future__ import annotations

import io
import urllib.error

from PIL import Image

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, env, remote  # noqa: F401
from tests.webapp.test_cartelera_access import ORIGIN, editions  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from webapp.services import cartelera_social as social, user_avatars as av


def _png(w=800, h=500, color=(30, 140, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_priority_custom_then_corporate_then_initials(monkeypatch, editions):  # noqa: F811
    email = "persona@danwor.com"
    assert av.effective(email)["source"] is None and av.avatar_url(email) == ""
    monkeypatch.setattr(av.microsoft365, "graph_get_bytes", lambda url, token, **k: _png(400, 400, (200, 0, 0)))
    assert av.sync_corporate(email, "token")["source"] == "corporate"
    calls = []
    monkeypatch.setattr(av.microsoft365, "graph_get_bytes", lambda url, token, **k: calls.append(url) or _png())
    av.sync_corporate(email, "token")
    assert calls == []                                                                    # como mucho una vez por día
    url_corp = av.avatar_url(email)
    assert av.set_custom(email, _png(1200, 600))["source"] == "custom" and av.avatar_url(email) != url_corp
    img = Image.open(io.BytesIO(av.image_bytes(av.key(email))))
    assert img.format == "WEBP" and img.size == (256, 256)                                # recorte cuadrado, sin metadatos
    assert av.remove_custom(email)["source"] == "corporate" and av.avatar_url(email) == url_corp


def test_account_without_corporate_photo_falls_back_to_initials(monkeypatch, editions):  # noqa: F811
    def no_photo(url, token, **k):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(av.microsoft365, "graph_get_bytes", no_photo)
    assert av.sync_corporate("sinfoto@danwor.com", "token", force=True)["source"] is None


def test_routes_only_manage_the_own_photo_and_community_uses_it(editions, monkeypatch):  # noqa: F811
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)
    ops, cchh = session_client(OPS_A), session_client(CCHH_EMAIL)
    assert ops.post("/perfil/foto", files={"file": ("a.png", _png(), "image/png")}).status_code == 403   # sin origen verificado
    r = ops.post("/perfil/foto", headers=ORIGIN, data={"email": CCHH_EMAIL}, files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["email"] == OPS_A.lower() and r.json()["has_custom"]        # la identidad sale de la sesión
    assert av.effective(CCHH_EMAIL)["source"] is None                                                    # nunca la de otro
    assert ops.post("/perfil/foto", headers=ORIGIN, files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}).status_code == 422
    url = av.avatar_url(OPS_A)
    assert cchh.get(url).status_code == 200 and cchh.get(url).headers["content-type"] == "image/webp"   # visible para otros usuarios del Hub
    anon = session_client(CCHH_EMAIL); anon.cookies.clear()
    assert anon.get(url).status_code == 401
    owner = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}
    social.save_post(owner, {"category": "reconocimientos", "title": "Con avatar"})
    assert url in cchh.get("/cartelera/comunidad").text                                                  # Comunidad sin llamadas a Graph
    me = ops.post("/perfil/foto/quitar", headers=ORIGIN).json()
    assert me["source"] is None and me["avatar"] == "" and me["initials"]


def test_portal_header_offers_the_same_profile_menu_and_every_community_avatar_is_the_effective_one(editions, monkeypatch):  # noqa: F811
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)
    ops, cchh = session_client(OPS_A), session_client(CCHH_EMAIL)
    av.set_custom(OPS_A, _png())
    url = av.avatar_url(OPS_A)
    author = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}
    post = social.save_post(author, {"category": "reconocimientos", "title": "Con avatar en todas partes"})
    target = f"post:{post['id']}"
    assert cchh.post("/cartelera/social/reaccion", headers=ORIGIN, json={"target": target, "r": "like"}).status_code == 200
    assert ops.post("/cartelera/social/reaccion", headers=ORIGIN, json={"target": target, "r": "like"}).status_code == 200
    html = ops.get("/cartelera/comunidad").text
    tools = html.split('class="hdr-tools"', 1)[1].split("</header>", 1)[0]
    assert tools.index('data-sx="notif"') < tools.index("data-dw-profile")                 # el avatar, a la derecha de la campana
    assert "hub_profile.js" in tools and "hub_topbar_menus.js" in tools                      # MISMO menú y MISMO controlador del Hub
    composer = html.split('class="card composer-cta"', 1)[1].split("</div>", 1)[0]
    assert url in composer                                                                    # publicador: la foto efectiva, no «OA»
    who = cchh.get("/cartelera/comunidad").text.split(f'data-social="{target}"', 1)[1].split('data-who="', 1)[1].split('"', 1)[0]
    assert url.replace("&", "&amp;") in who or url in who                                      # detalle de reacciones con la misma foto
    social.save_post({"sub": CCHH_EMAIL, "email": CCHH_EMAIL, "name": "CH QA"}, {"category": "oficial", "title": "Oficial", "official": True})
    assert 'class="avatar ch"' in cchh.get("/cartelera/comunidad").text                     # lo oficial conserva la identidad «CH»
