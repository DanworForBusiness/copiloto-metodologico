"""
test_commercial_presentations_logo.py — Presentaciones: logo del cliente al copiar (PNG con fondo transparente que reemplaza al logo de Vanture del header y el pie).

Contrato:
  * se sube en el diálogo «Copiar como nueva» (multipart: título + logo opcional); el JSON `{title}` sigue siendo válido;
  * el servidor valida TODO: firma PNG, CRC de cada bloque, IEND, ≤ 1 MB, ≤ 1,5 M de píxeles, no animado y transparencia real (canal alfa/`tRNS` y al menos un píxel transparente); nada se escribe si falla;
  * se guarda tal cual (`<uuid>.logo.png`, junto a la instancia, bajo el mismo directorio ya persistido); la copia de una copia hereda el logo; eliminar la presentación elimina su logo;
  * R8 `GET /presentaciones/{id}/logo`: mismo contrato de lectura que R1 (cualquier usuario admitido al Hub, sin exigir Comercial), PNG con `nosniff` y CSP restrictiva.
"""
from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import config
from core import remote_store
from tests.webapp.test_commercial_presentations_access import (  # noqa: F401  (fixtures y helpers compartidos)
    A, HUB_ONLY, INTRUDER, ORIGIN, SEED_ID, _copy, _delete, world,
)
from tests.webapp.test_commercial_presentations_persistence import FakeSpaces  # noqa: F401
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def make_png(width=6, height=4, color_type=6, depth=8, alpha=lambda x, y: 0 if x < 2 else 255, extra=b"", ihdr_extra=None, filters=(0,), palette=None, trns=None, interlace=0) -> bytes:
    """PNG sintético válido. `alpha(x, y)` define la opacidad por píxel (color_type 6 / 4)."""
    rows = []
    for y in range(height):
        px = bytearray()
        for x in range(width):
            if color_type == 6:
                px += bytes((200, 30, 30, alpha(x, y)))
            elif color_type == 4:
                px += bytes((120, alpha(x, y)))
            elif color_type == 2:
                px += bytes((200, 30, 30))
            elif color_type == 3:
                px += bytes((x % 2,))
        ftype = filters[y % len(filters)]
        if ftype == 0:
            rows.append(bytes([0]) + bytes(px))
        else:                                         # el filtro «Sub»/«Up» se codifica restando: se verifica que el decodificador del servidor lo deshaga
            bpp = {6: 4, 4: 2, 2: 3, 3: 1}[color_type]
            prev = rows_raw[-1] if (rows_raw := globals().setdefault("_rr", [])) and False else None
            line = bytearray(px)
            if ftype == 1:
                for i in range(len(px) - 1, bpp - 1, -1):
                    line[i] = (px[i] - px[i - bpp]) & 255
            rows.append(bytes([ftype]) + bytes(line))
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace) if ihdr_extra is None else ihdr_extra
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    if palette:
        out += _chunk(b"PLTE", palette)
    if trns is not None:
        out += _chunk(b"tRNS", trns)
    out += extra + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")
    return out


GOOD = make_png()


def _post_copy(world, email, *, title="Con logo", logo=None, filename="logo.png", source=SEED_ID):
    files = {"logo": (filename, logo, "image/png")} if logo is not None else {"logo": ("", b"")}          # sin archivo elegido el navegador igual envía multipart (campo vacío)
    return world.client(email).post(f"/comercial/presentaciones/{source}/copiar", data={"title": title}, files=files, headers=ORIGIN)


def _page(world, pid, email=HUB_ONLY):
    return world.client(email).get(f"/presentaciones/{pid}").text


# ── validación ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_valid_transparent_png_is_accepted_and_reported():
    meta = cp.validate_logo_png(GOOD)
    assert meta["width"] == 6 and meta["height"] == 4 and meta["bytes"] == len(GOOD) and len(meta["sha256"]) == 64


@pytest.mark.parametrize("filters", [(0,), (1,), (0, 1)], ids=["none", "sub", "mixed"])
def test_the_pixel_check_decodes_filtered_scanlines(filters):
    cp.validate_logo_png(make_png(width=9, height=5, alpha=lambda x, y: 0 if (x + y) % 3 == 0 else 255, filters=filters))


def test_grey_alpha_and_palette_with_trns_are_valid_transparent_pngs():
    cp.validate_logo_png(make_png(color_type=4))
    cp.validate_logo_png(make_png(color_type=3, palette=bytes((0, 0, 0, 255, 255, 255)), trns=bytes((0, 255))))


@pytest.mark.parametrize("data, fragment", [
    (b"", "Elegí un archivo PNG"),
    (b"\xff\xd8\xff\xe0" + b"0" * 100, "debe ser un archivo .png"),
    (b"GIF89a" + b"0" * 100, "debe ser un archivo .png"),
    (make_png(color_type=2), "no tiene transparencia"),                                                             # RGB sin alfa
    (make_png(alpha=lambda x, y: 255), "ningún píxel transparente"),                                                # RGBA completamente opaco
    (make_png(color_type=3, palette=bytes((0, 0, 0, 255, 255, 255)), trns=bytes((255, 255))), "ningún píxel transparente"),
    (GOOD[:-20], "incompleto o dañado"),                                                                            # truncado
    (GOOD[:40] + bytes([GOOD[40] ^ 0xFF]) + GOOD[41:], "dañado"),                                                   # CRC roto
    (make_png(extra=_chunk(b"acTL", struct.pack(">II", 2, 0))), "animados"),
    (make_png(width=2000, height=1000), "demasiado grande en píxeles"),
], ids=["empty", "jpeg", "gif", "rgb-no-alpha", "opaque-rgba", "palette-opaque", "truncated", "bad-crc", "animated", "too-many-pixels"])
def test_every_invalid_logo_is_rejected_with_a_displayable_message(data, fragment):
    with pytest.raises(cp.LogoValidationError) as exc:
        cp.validate_logo_png(data)
    assert fragment in str(exc.value)


def test_a_logo_over_one_megabyte_is_rejected():
    with pytest.raises(cp.LogoValidationError, match="1 MB"):
        cp.validate_logo_png(GOOD + b"0" * cp.MAX_LOGO_BYTES)


def test_a_corrupt_pixel_stream_is_a_clear_error_not_a_crash():
    broken = make_png().replace(b"IDAT", b"IDAT", 1)
    idat_at = broken.index(b"IDAT")
    body = broken[idat_at + 4: idat_at + 4 + 10]
    bad = broken[:idat_at + 4] + bytes(len(body)) + broken[idat_at + 4 + len(body):]                               # IDAT con basura: el CRC ya no coincide → «dañado»
    with pytest.raises(cp.LogoValidationError):
        cp.validate_logo_png(bad)


# ── copia con logo ──────────────────────────────────────────────────────────────────────────────────────

def test_copying_with_a_logo_stores_it_next_to_the_instance_and_the_document_uses_it_in_header_and_footer(world):
    r = _post_copy(world, A, logo=GOOD)
    assert r.status_code == 200, r.text
    pid = r.json()["presentation_id"]
    assert (world.directory / f"{pid}.logo.png").read_bytes() == GOOD                                             # guardado tal cual, sin re-codificar
    html = _page(world, pid)
    imgs = [i for i in BeautifulSoup(html, "html.parser").find_all("img") if i.get("alt") == "Logo del cliente"]
    assert len(imgs) == 2 and all(i["src"].startswith(f"/presentaciones/{pid}/logo?v=") for i in imgs)             # header y pie
    assert "718d5d28e4.png" not in html and "Vanture — a Knowmad Mood company" not in html
    assert "Danwor — Data &amp; AI for Business" in html                                                          # el logo institucional no se toca
    assert 'alt="Vanturess"' in html                                                                              # el logo de la sección de alianza (Vanture como sujeto) tampoco


def test_a_copy_without_a_logo_keeps_the_original_and_the_json_contract_still_works(world):
    pid = _copy(world, A)                                                                                         # JSON, como siempre
    assert not (world.directory / f"{pid}.logo.png").exists()
    html = _page(world, pid)
    assert html.count("718d5d28e4.png") == 2 and "/logo?v=" not in html
    r = _post_copy(world, A, title="Multipart sin logo")
    assert r.status_code == 200 and '"logo":' not in (world.directory / f"{r.json()['presentation_id']}.json").read_text(encoding="utf-8")


def test_the_seed_always_shows_the_original_logo(world):
    assert _page(world, SEED_ID).count("718d5d28e4.png") == 2


def test_a_copy_of_a_copy_inherits_the_logo_and_a_new_upload_replaces_it(world):
    first = _post_copy(world, A, logo=GOOD).json()["presentation_id"]
    inherited = _post_copy(world, A, title="Hereda", source=first).json()["presentation_id"]
    assert (world.directory / f"{inherited}.logo.png").read_bytes() == GOOD
    other = make_png(width=8, height=3)
    replaced = _post_copy(world, A, title="Reemplaza", source=first, logo=other).json()["presentation_id"]
    assert (world.directory / f"{replaced}.logo.png").read_bytes() == other
    assert (world.directory / f"{first}.logo.png").read_bytes() == GOOD                                           # el origen no cambia


@pytest.mark.parametrize("bad", [make_png(color_type=2), make_png(alpha=lambda x, y: 255), b"no-es-un-png"], ids=["rgb", "opaque", "garbage"])
def test_an_invalid_logo_is_a_422_and_nothing_is_written(world, bad):
    r = _post_copy(world, A, logo=bad)
    assert r.status_code == 422 and r.json().get("detail")
    assert world.files() == {} and not list(world.directory.glob("*")) if world.directory.exists() else True


def test_an_oversized_upload_is_rejected_before_creating_anything(world):
    r = _post_copy(world, A, logo=GOOD + b"0" * (cp.MAX_LOGO_BYTES + 200_000))
    assert r.status_code in (413, 422)
    assert world.files() == {}


def test_the_title_is_still_required_with_a_logo(world):
    r = _post_copy(world, A, title="  ", logo=GOOD)
    assert r.status_code == 422 and not list(world.directory.glob("*")) if world.directory.exists() else True


# ── R8: servir el logo ───────────────────────────────────────────────────────────────────────────────────

def test_any_hub_user_reads_the_logo_with_the_safe_headers_and_the_commercial_contract_is_not_required(world):
    pid = _post_copy(world, A, logo=GOOD).json()["presentation_id"]
    for email in (A, HUB_ONLY):
        r = world.client(email).get(f"/presentaciones/{pid}/logo")
        assert r.status_code == 200 and r.content == GOOD
        assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "private, no-store" and "sandbox" in r.headers["content-security-policy"]


def test_the_logo_route_denies_anonymous_and_not_admitted_users_and_404s_without_a_logo(world):
    pid = _post_copy(world, A, logo=GOOD).json()["presentation_id"]
    plain = _copy(world, A)
    assert world.client(None).get(f"/presentaciones/{pid}/logo", follow_redirects=False).status_code == 303
    assert world.client(INTRUDER).get(f"/presentaciones/{pid}/logo").status_code == 403
    assert world.client(A).get(f"/presentaciones/{plain}/logo").status_code == 404
    assert world.client(A).get(f"/presentaciones/{SEED_ID}/logo").status_code == 404
    assert world.client(A).get("/presentaciones/00000000-0000-4000-8000-000000000000/logo").status_code == 404


# ── eliminar y persistencia remota ───────────────────────────────────────────────────────────────────────

def test_deleting_a_presentation_deletes_its_logo(world):
    pid = _post_copy(world, A, logo=GOOD).json()["presentation_id"]
    assert _delete(world, A, pid).status_code == 200
    assert world.files() == {} and not (world.directory / f"{pid}.logo.png").exists()
    assert world.client(A).get(f"/presentaciones/{pid}/logo").status_code == 404


def test_remote_copy_pushes_the_logo_before_the_instance_and_remote_delete_removes_both(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _post_copy(world, A, logo=GOOD).json()["presentation_id"]
    assert [p.name for p in spaces.pushed] == [f"{pid}.logo.png", f"{pid}.json"]                                  # el logo primero: nunca hay instancia remota sin su logo
    assert _delete(world, A, pid).status_code == 200
    assert sorted(p.name for p in spaces.deleted) == sorted([f"{pid}.json", f"{pid}.logo.png"])


def test_a_failed_remote_copy_discards_the_logo_too(world, monkeypatch):
    FakeSpaces(monkeypatch, push_results=[False, False, False, False])
    r = _post_copy(world, A, logo=GOOD)
    assert r.status_code == 503 and not list(world.directory.glob("*.png")) and world.files() == {}


def test_a_failed_remote_delete_restores_the_instance_and_its_logo(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _post_copy(world, A, logo=GOOD).json()["presentation_id"]
    monkeypatch.setattr(remote_store, "delete_path", lambda p: False)
    assert _delete(world, A, pid).status_code == 503
    assert (world.directory / f"{pid}.logo.png").read_bytes() == GOOD and (world.directory / f"{pid}.json").exists()


def test_the_logo_lives_under_the_already_persisted_directory(monkeypatch):
    monkeypatch.setattr(config, "COMMERCIAL_STRATEGY_RUNTIME_DIR", config.BASE_DIR / "commercial_strategy_runtime")
    assert remote_store._is_persisted_path(cp._logo_path("x"))                                                   # restore / push_path lo cubren: sin variable ni ruta nueva


# ── UI ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_copy_dialog_offers_an_optional_png_upload_and_sends_multipart(world):
    html = world.client(A).get("/comercial/presentaciones").text
    dialog = BeautifulSoup(html, "html.parser").select_one("dialog#cp-copy-dialog")
    field = dialog.select_one("input[type=file][data-cp-logo]")
    assert field is not None and field.get("accept") == "image/png,.png" and not field.has_attr("required")
    assert dialog.select_one("label[for=cp-copy-logo]").text.strip() == "Logo del cliente (opcional)"
    assert "PNG con fondo transparente, hasta 1 MB" in dialog.get_text(" ", strip=True)
    js = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
    assert "new FormData()" in js and 'form.append("logo"' in js and "JSON.stringify({ title: title })" not in js
    assert re.search(r'headers: \{ "Content-Type"', js.split("// ── Copiar como nueva", 1)[1].split("// ── Editor", 1)[0]) is None, "sin Content-Type propio: el navegador arma el boundary"


def test_the_file_picker_uses_the_hub_file_input_look_defined_once_in_the_shared_css():
    tools = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    hc = (REPO / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
    shared = '.feedback-field input[type="file"] { padding: 7px 10px; border: 1px solid rgba(128,128,128,.35); border-radius: 8px; background: transparent; color: #1f1f24; font: .8rem var(--sans); }'
    assert shared in tools and '.content-theme-dark .feedback-field input[type="file"] { color: #e4e4ec; }' in tools
    assert "padding: 7px 10px; border: 1px solid rgba(128,128,128,.35); border-radius: 8px;" in hc            # los mismos valores que Capital Humano ya usaba por formulario
    commercial = (REPO / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert 'type="file"' not in commercial and "file-selector" not in commercial, "sin CSS propio de Presentaciones para el selector de archivo"
