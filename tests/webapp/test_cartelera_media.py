"""Servicio multimedia de Cartelera Virtual: validación real (Pillow), representaciones sin metadatos con proporción original y acceso
SOLO por referencia visible (borradores, pendientes, retiradas y ajenas no se exponen)."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, env, remote  # noqa: F401
from tests.webapp.test_cartelera_access import ORIGIN, editions  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from webapp.services import cartelera_birthdays_source as bd_source, cartelera_media as media, cartelera_repository as repo
from webapp.services import cartelera_service as svc, cartelera_social as social


def _png(w=1200, h=600, color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif(w=3000, h=1000) -> bytes:
    img = Image.new("RGB", (w, h), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "CamaraDePrueba"                                                # Make
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


@pytest.fixture
def no_sharepoint(monkeypatch):
    monkeypatch.setattr(bd_source, "ENABLED", False)                               # sin Graph: el original queda en el Hub (como el DEMO)


def test_processing_validates_real_format_strips_metadata_and_keeps_aspect():
    with pytest.raises(media.MediaError):
        media.process(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    with pytest.raises(media.MediaError):
        media.process(b"GIF89a" + b"\x00" * 64)
    frames = [Image.new("RGB", (64, 64), c) for c in ((255, 0, 0), (0, 255, 0))]
    gif = io.BytesIO(); frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(media.MediaError):
        media.process(gif.getvalue())
    out = media.process(_jpeg_with_exif())
    disp = Image.open(io.BytesIO(out["display"]))
    assert out["format"] == "JPEG" and disp.format == "WEBP" and disp.size == (1920, 640)       # proporción 3:1 conservada
    assert not disp.info.get("exif") and not disp.getexif()                                    # metadatos descartados
    assert max(Image.open(io.BytesIO(out["thumb"])).size) == 480


def test_media_access_follows_visible_references(editions, no_sharepoint, monkeypatch):  # noqa: F811
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)
    cur, nxt, _ = editions
    cchh, ops = session_client(CCHH_EMAIL), session_client(OPS_A)
    # Novedades: subida por Capital Humano (con origen verificado); en una edición NO publicada solo la ve Capital Humano
    r = cchh.post("/cchh/cartelera/media", headers=ORIGIN, data={"scope": "novedades", "context": nxt}, files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert ops.post("/cchh/cartelera/media", headers=ORIGIN, data={"scope": "novedades"}, files={"file": ("a.png", _png(), "image/png")}).status_code == 403
    svc.save_content(nxt, repo.get("editions", nxt)["revision"], title="F", subtitle="", actor="t",
                     sections=[{"key": "comunicados", "items": [{"title": "Con imagen", "image": mid}]}])
    assert cchh.get(f"/cartelera/media/{mid}/display").status_code == 200
    assert ops.get(f"/cartelera/media/{mid}/display").status_code == 404                     # borrador/programada: no
    svc.save_content(cur, repo.get("editions", cur)["revision"], title="P", subtitle="", actor="t",
                     sections=[{"key": "comunicados", "items": [{"title": "Publicado", "image": mid}]}])
    r = ops.get(f"/cartelera/media/{mid}/display")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and "sandbox" in r.headers["content-security-policy"]
    assert "Con imagen" not in ops.get("/cartelera").text
    assert ops.get(f"/cartelera/media/{mid}/original").status_code == 404                    # el original nunca se distribuye


def test_community_image_is_owned_and_hidden_when_pending_or_removed(editions, no_sharepoint, monkeypatch):  # noqa: F811
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)
    cchh, ops = session_client(CCHH_EMAIL), session_client(OPS_A)
    r = ops.post("/cartelera/social/media", headers=ORIGIN, files={"file": ("flyer.png", _png(600, 1200), "image/png")})
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    rec = media.meta(mid)
    assert rec["scope"] == "comunidad" and rec["original"]["store"] == "hub"                  # sin SharePoint ni permisos del colaborador
    assert ops.get(f"/cartelera/media/{mid}/display").status_code == 200                     # la autoría ve lo suyo recién subido
    assert cchh.get(f"/cartelera/media/{mid}/display").status_code == 404                    # nadie más, hasta que se publique
    owner = {"sub": OPS_A, "email": OPS_A, "name": "Ops"}
    with pytest.raises(social.SocialError):
        social.save_post({"sub": CCHH_EMAIL, "email": CCHH_EMAIL, "name": "CH"}, {"category": "reconocimientos", "title": "x", "image": mid})
    approval = next(c["id"] for c in social.CATEGORIES if c["approval"] and c["collab"])
    post = social.save_post(owner, {"category": approval, "title": "Con flyer", "image": mid, "consent": True})
    assert post["status"] == "pending" and cchh.get(f"/cartelera/media/{mid}/display").status_code == 404
    repo.save("posts", post["id"], {"status": "published"}, expected_revision=post["revision"])
    assert cchh.get(f"/cartelera/media/{mid}/display").status_code == 200                    # publicada: visible
    p = repo.get("posts", post["id"])
    repo.save("posts", post["id"], {"status": "removed"}, expected_revision=p["revision"])
    assert cchh.get(f"/cartelera/media/{mid}/display").status_code == 404                    # retirada: no


def test_album_photos_survive_editing_album_attributes(no_sharepoint):
    rec = media.ingest(_png(800, 800), scope="galeria", actor="t", context="Fiesta")
    svc.save_institutional(None, mission="M", vision="V", values=[], actor="t",
                           albums=[{"id": "al1", "title": "Fiesta", "photos": [{"id": rec["id"]}, {"id": "f" * 32}], "cover": rec["id"]}])
    alb = svc.institutional()["albums"][0]
    assert alb["photos"] == [{"id": rec["id"], "caption": ""}] and alb["cover"] == rec["id"]   # ids inexistentes descartados
    svc.save_institutional(svc.institutional()["revision"], mission="M", vision="V", values=[], actor="t",
                           albums=[{**alb, "title": "Fiesta 2026", "description": "Nueva descripción"}])
    assert svc.institutional()["albums"][0]["photos"] == alb["photos"]
