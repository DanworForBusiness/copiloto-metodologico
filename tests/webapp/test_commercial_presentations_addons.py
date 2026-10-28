"""Presentaciones V1 — add-ons: componentes gobernados por región (Quitar / Restaurar / Reemplazar / Agregar) y QR intercambiable.

Contrato: la instancia guarda solo ids (`blocks` = desvíos del ocupante por defecto de cada región; `None` = quitado) y, aparte, el asset del QR; el renderer de `layer-stack` es único y compartido (parámetros
institucionales en `addons.yaml`); la compatibilidad es solo el TIPO de región (`body` / `bleed`); las regiones nuevas no emiten nada mientras estén vacías; el seed conserva paridad exacta.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import config
from core import remote_store
from tests.webapp.test_commercial_presentations_access import (
    A, APP_OWNER, B, HUB_ONLY, INTRUDER, ORIGIN, OWNER_C, SEED_ID, _copy, _delete, _save, world,
)
from tests.webapp.test_commercial_presentations_logo import _chunk, make_png
from tests.webapp.test_commercial_presentations_persistence import FakeSpaces
from webapp.services import auth_service
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]
SEED = cp._seed_raw("vanture-bdc")["content"]
BDC = {"component": "layer-stack", "variant": "bdc-architecture"}
KC = {"component": "layer-stack", "variant": "knowledge-core-stack"}
FLOW = {"component": "ecosystem-flow", "variant": "open-ecosystem-flow"}
MARQUEE = {"component": "logo-marquee", "variant": "partner-logo-marquee"}
OPAQUE_QR = make_png(width=40, height=40, color_type=2)                                # un QR real suele ser opaco y cuadrado
TRANSPARENT_QR = make_png(width=40, height=40, color_type=6, alpha=lambda x, y: 0 if x < 4 else 255)


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _read(world, pid, email=A, edit=False):
    r = world.client(email).get(f"/presentaciones/{pid}?embed=1" + ("&edit=1" if edit else ""))
    assert r.status_code == 200, r.text[:200]
    return _soup(r.text)


def _blocks(world, pid, blocks, revision, email=A, fields=None):
    body = {"revision": revision, "blocks": blocks}
    if fields is not None:
        body["fields"] = fields
    return world.client(email).post(f"/comercial/presentaciones/{pid}", json=body, headers=ORIGIN)


def _qr(world, pid, revision, data=OPAQUE_QR, email=A, remove=False, filename="qr.png", headers=ORIGIN):
    form = {"revision": str(revision)}
    if remove:
        form["remove"] = "1"
    files = {"file": (filename, data, "image/png")} if (data is not None and not remove) else {"_": ("", b"")}
    return world.client(email).post(f"/comercial/presentaciones/{pid}/qr", data=form, files=files, headers=headers)


def _stored(world, pid):
    return json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))


# ── Registro, regiones y validación ───────────────────────────────────────────────────────────────────────────────────

def test_the_registry_only_offers_real_components_and_each_variant_id_is_the_block_marker_already_in_the_document(world):
    lib = cp.component_library()
    assert [o["ref"] for o in lib] == ["layer-stack:bdc-architecture", "layer-stack:knowledge-core-stack", "ecosystem-flow:open-ecosystem-flow", "logo-marquee:partner-logo-marquee"]
    assert {o["region"] for o in lib} == {"body", "bleed"} and [o["ref"] for o in lib if o["region"] == "bleed"] == ["logo-marquee:partner-logo-marquee"]
    soup = _read(world, SEED_ID)
    assert {b["data-cp-block"] for b in soup.select("[data-cp-block]")} == {o["variant"] for o in lib}      # el registro no inventa nada: es lo que el template ya dibujaba
    assert set(cp._addons()) == set(cp.COMPONENTS["layer-stack"]["variants"])                                 # las dos variantes de layer-stack son datos institucionales


def test_there_are_nine_declared_regions_four_existing_and_five_empty_by_default():
    assert set(cp.REGIONS) == {"s2.body", "s3.body", "s4.body", "s5.body", "s6.body", "s7.body", "s8.body", "s9.body", "s12.bleed"}
    assert {r for r, (_, default) in cp.REGIONS.items() if default} == {"s3.body", "s4.body", "s5.body", "s12.bleed"}
    assert [t for t, _ in cp.REGIONS.values()].count("bleed") == 1                                          # ninguna región bleed nueva: el marquee solo se quita/restaura en s12


@pytest.mark.parametrize("value, expected", [
    ({}, {}),
    ({"s3.body": KC}, {"s3.body": KC}),
    ({"s4.body": KC}, {}),                                                                                   # coincide con el ocupante por defecto: no se guarda
    ({"s2.body": None}, {}),                                                                                 # «quitado» en una región que ya nace vacía: nada que guardar
    ({"s3.body": None, "s2.body": FLOW}, {"s2.body": FLOW, "s3.body": None}),                               # normalizado al orden de las regiones
    ({"s12.bleed": None}, {"s12.bleed": None}),
])
def test_validate_blocks_normalizes_to_the_deviations_from_the_default(value, expected):
    assert cp.validate_blocks(value) == expected


@pytest.mark.parametrize("bad", [
    {"s2.body": MARQUEE},                                                                                    # marquee es bleed
    {"s12.bleed": BDC}, {"s12.bleed": FLOW},                                                                 # layer-stack / flow son body
    {"s99.body": None}, {"s1.body": None}, {"s2.bleed": None},                                               # región inexistente
    {"s2.body": {"component": "layer-stack", "variant": "inventada"}}, {"s2.body": {"component": "html", "variant": "x"}},
    {"s2.body": {"component": "layer-stack"}}, {"s2.body": {"component": "layer-stack", "variant": "bdc-architecture", "html": "<b>"}},
    {"s2.body": "layer-stack:bdc-architecture"}, {"s2.body": [BDC]}, {"s2.body": 1},
    [], "s3.body", {3: None},
])
def test_validate_blocks_rejects_unknown_incompatible_or_malformed_compositions(bad):
    with pytest.raises(cp.PresentationValidationError) as err:
        cp.validate_blocks(bad)
    assert "<" not in str(err.value) and "inventada" not in str(err.value)                                   # el mensaje no refleja el valor recibido


def test_the_institutional_variants_file_is_validated_on_load(monkeypatch, tmp_path):
    good = (REPO / "webapp" / "services" / "commercial_presentations_data" / "addons.yaml").read_text(encoding="utf-8")
    original = cp._SEED_DIR
    for broken in (good.replace("tone: light", "tone: rainbow", 1), good.replace('label: "AI AGENTS"', 'label: "<b>x</b>\\n"', 1), good.replace("reveal: 34", "reveal: 900", 1),
                   good.replace("knowledge-core-stack:", "otra:", 1)):
        (tmp_path / "addons.yaml").write_text(broken, encoding="utf-8")
        monkeypatch.setattr(cp, "_SEED_DIR", tmp_path)
        cp._addons.cache_clear()
        with pytest.raises(cp.PresentationUnavailable):
            cp._addons()
    monkeypatch.setattr(cp, "_SEED_DIR", original)
    cp._addons.cache_clear()
    assert cp._addons()


# ── Guardado (R6) ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_composition_is_saved_without_field_changes_and_the_file_keeps_its_old_shape_when_nothing_deviates(world):
    pid = _copy(world, A)
    assert "blocks" not in _stored(world, pid)
    r = _blocks(world, pid, {"s3.body": KC, "s5.body": None, "s2.body": FLOW}, 1)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 2 and r.json()["blocks"] == {"s2.body": FLOW, "s3.body": KC, "s5.body": None}
    stored = _stored(world, pid)
    assert stored["blocks"] == {"s2.body": FLOW, "s3.body": KC, "s5.body": None} and stored["revision"] == 2
    assert not any("<" in json.dumps(v) for v in stored["blocks"].values())                                   # ids, nunca markup
    assert _blocks(world, pid, {}, 2).status_code == 200
    assert "blocks" not in _stored(world, pid)                                                               # restaurar todo devuelve la forma de siempre


def test_fields_slides_and_composition_are_saved_together_in_a_single_revision(world):
    pid = _copy(world, A)
    r = world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "fields": {"s3_title": "Otro título"}, "removed_slides": ["s9"], "blocks": {"s3.body": KC}}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["revision"] == 2
    stored = _stored(world, pid)
    assert stored["content"]["s3_title"] == "Otro título" and stored["removed_slides"] == ["s9"] and stored["blocks"] == {"s3.body": KC}


@pytest.mark.parametrize("bad", [{"s2.body": MARQUEE}, {"s12.bleed": KC}, {"s99.body": None}, {"s2.body": {"component": "layer-stack", "variant": "x"}}, "s3.body", [KC]])
def test_an_incompatible_or_forged_composition_is_a_422_that_saves_nothing_not_even_the_valid_fields(world, bad):
    pid = _copy(world, A)
    before = world.files()
    r = _blocks(world, pid, bad, 1, fields={"s3_title": "no debería guardarse"})
    assert r.status_code == 422, r.text
    assert world.files() == before
    assert "<" not in r.text


def test_a_stale_revision_never_applies_the_composition(world):
    pid = _copy(world, A)
    assert _save(world, A, pid, {"s3_title": "uno"}, 1).status_code == 200
    assert _blocks(world, pid, {"s3.body": KC}, 1).status_code == 409 and "blocks" not in _stored(world, pid)


def test_only_who_can_edit_the_instance_changes_its_composition_and_the_seed_never(world, monkeypatch):
    pid = _copy(world, A)
    before = world.files()
    assert _blocks(world, pid, {"s3.body": KC}, 1, email=B).status_code == 403                               # otro usuario Comercial
    assert _blocks(world, pid, {"s3.body": KC}, 1, email=APP_OWNER).status_code == 403                       # administrador ≠ editor de contenido ajeno
    assert _blocks(world, SEED_ID, {"s3.body": KC}, 1).status_code == 403                                    # el seed es inmutable
    assert _blocks(world, pid, {"s3.body": KC}, 1, email=HUB_ONLY).status_code == 403
    assert world.client(None).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "blocks": {}}, headers=ORIGIN).status_code == 401
    assert world.files() == before
    assert _blocks(world, pid, {"s3.body": KC}, 1, email=OWNER_C).status_code == 200                         # CONTENT_OWNER
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "owner-c,app-owner")
    assert _blocks(world, pid, {"s3.body": BDC}, 2, email=APP_OWNER).status_code == 200                      # solo de forma explícita


def test_a_persistence_failure_on_a_composition_change_leaves_no_half_state(world, monkeypatch):
    pid = _copy(world, A)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    before = world.files()
    assert _blocks(world, pid, {"s3.body": KC}, 1).status_code == 503 and world.files() == before
    spaces = FakeSpaces(monkeypatch, push_results=[False])
    r = _blocks(world, pid, {"s3.body": KC}, 1)
    assert r.status_code == 503 and world.files() == before                                                  # el push no se confirmó: el local vuelve a la versión anterior
    assert _stored(world, pid)["revision"] == 1 and spaces.pushed


# ── Render ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_untouched_instance_renders_the_four_existing_regions_and_emits_nothing_for_the_five_empty_ones(world):
    for pid in (SEED_ID, _copy(world, A)):
        soup = _read(world, pid)
        assert [r["data-cp-region"] for r in soup.select("[data-cp-region]")] == ["s3.body", "s4.body", "s5.body", "s12.bleed"]
        assert not soup.select(".cp-region-gap, .cp-region-tools, template")                                  # las regiones vacías no emiten markup ni herramientas al leer


def test_removing_a_component_removes_the_whole_region_including_its_label_and_spacer(world):
    pid = _copy(world, A)
    base = _read(world, pid)
    label_style = "letter-spacing:.24em"
    assert base.find(id="s4").select(f"[style*='{label_style}']") and base.find(id="s5").select("div[style='height:44px']")
    assert _blocks(world, pid, {"s3.body": None, "s4.body": None, "s5.body": None, "s12.bleed": None}, 1).status_code == 200
    soup = _read(world, pid)
    assert not soup.select("[data-cp-region], [data-cp-block], .arch, .layer-row, .flow-grid, .logo-marquee")
    assert not soup.find(id="s4").select(f"[style*='{label_style}']")                                          # el rótulo del diagrama se va con su región: no queda huérfano
    assert not soup.find(id="s5").select("div[style='height:44px']")                                          # ni el espaciador
    for sid, key in (("s3", "s3_title"), ("s4", "s4_title"), ("s5", "s5_card1_title")):                       # la narrativa de alrededor sigue intacta
        assert SEED[key] in soup.find(id=sid).get_text()
    assert _blocks(world, pid, {}, 2).status_code == 200                                                     # restaurar: vuelve exactamente a lo de antes
    assert [str(x) for x in _read(world, pid).select("[data-cp-region]")] == [str(x) for x in base.select("[data-cp-region]")]


def test_replacing_a_region_uses_the_shared_renderer_and_does_not_rewrite_the_narrative_text_around_it(world):
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s3.body": KC}, 1).status_code == 200
    soup = _read(world, pid)
    in_s3 = soup.find(id="s3").select_one("[data-cp-block]")
    in_s4 = soup.find(id="s4").select_one("[data-cp-block]")
    assert in_s3["data-cp-block"] == in_s4["data-cp-block"] == "knowledge-core-stack"
    assert str(in_s3) == str(in_s4)                                                                          # MISMO renderer y MISMOS parámetros: nada duplicado por instancia
    assert not soup.find(id="s3").select(".arch") and len(in_s3.select(".layer-row")) == 3
    assert soup.find(id="s3").select_one("h2").get_text(strip=True) == SEED["s3_title"]                      # el título de la slide no se tocó solo
    stored_text = json.dumps(_stored(world, pid))
    assert "SAP Datasphere" not in stored_text and "Grafo de conocimiento" not in stored_text                # la instancia no guarda contenido del componente


def test_adding_a_component_in_an_empty_region_places_it_last_in_the_container_with_the_standard_gap(world):
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s2.body": FLOW, "s8.body": BDC, "s6.body": KC}, 1).status_code == 200
    soup = _read(world, pid)
    assert [r["data-cp-region"] for r in soup.select("[data-cp-region]")] == ["s2.body", "s3.body", "s4.body", "s5.body", "s6.body", "s8.body", "s12.bleed"]
    for sid, variant in (("s2", "open-ecosystem-flow"), ("s6", "knowledge-core-stack"), ("s8", "bdc-architecture")):
        region = soup.find(id=sid).select_one("[data-cp-region]")
        assert "cp-region-gap" in region["class"] and region.select_one("[data-cp-block]")["data-cp-block"] == variant
        container = region.parent
        assert container.find_all(recursive=False)[-1] is region                                             # al final del contenedor de la slide
        assert region.find_next_sibling() is None and container.find_next_sibling("a") is not None             # y antes del «Siguiente»


def test_composition_never_changes_numbering_rail_anchors_or_next_links(world):
    pid = _copy(world, A)
    base = _read(world, pid)

    def nav(soup):
        return ([k.get_text(strip=True) for k in soup.select(".topic-kicker")], [a["href"] for a in soup.select("nav.rail a")],
                [(a.find_parent("section")["id"], a["href"], a.get_text(strip=True)) for a in soup.select("a.next-link")], [s["id"] for s in soup.select("section[id^=s]")])
    assert _blocks(world, pid, {"s3.body": None, "s4.body": FLOW, "s7.body": KC, "s12.bleed": None}, 1).status_code == 200
    assert nav(_read(world, pid)) == nav(base)


def test_composition_coexists_with_removed_slides_and_their_regions_disappear_with_the_slide(world):
    pid = _copy(world, A)
    r = world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "removed_slides": ["s4", "s7"], "blocks": {"s2.body": KC, "s7.body": FLOW}}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    soup = _read(world, pid)
    assert [s["id"] for s in soup.select("section[id^=s]")] == ["s1", "s2", "s3", "s5", "s6", "s8", "s9", "s10", "s11", "s12"]
    assert [x["data-cp-region"] for x in soup.select("[data-cp-region]")] == ["s2.body", "s3.body", "s5.body", "s12.bleed"]       # s4.body y s7.body se fueron con su slide
    assert [k.get_text(strip=True).split(" · ")[0].replace(" ", "") for k in soup.select(".topic-kicker")] == [f"{i:02d}/09" for i in range(1, 10)]
    edit = _read(world, pid, edit=True)                                                                      # y vuelven al restaurar la slide: el estado de sus regiones se conserva
    assert edit.find(id="s7").select_one("[data-cp-region]")["data-cp-occupant"] == "ecosystem-flow:open-ecosystem-flow"


def test_the_edit_document_offers_only_compatible_registered_components_per_region_and_keeps_the_library_inert(world):
    pid = _copy(world, A)
    soup = _read(world, pid, edit=True)
    regions = {r["data-cp-region"]: r for r in soup.select("[data-cp-region]")}
    assert list(regions) == list(cp.REGIONS) and all(r.select_one(":scope > .cp-region-tools[contenteditable=false]") for r in regions.values())
    body_options = {"layer-stack:bdc-architecture", "layer-stack:knowledge-core-stack", "ecosystem-flow:open-ecosystem-flow"}
    for rid, r in regions.items():
        offered = {b["data-cp-pick"] for b in r.select("[data-cp-pick]")}
        assert offered == (body_options if rid.endswith(".body") else {"logo-marquee:partner-logo-marquee"}), rid     # solo registrados y compatibles con el TIPO de región
        assert r["data-cp-region-type"] == ("body" if rid.endswith(".body") else "bleed")
    assert regions["s2.body"]["data-cp-occupant"] == "" and regions["s2.body"]["data-cp-default"] == "" and not regions["s2.body"].select("[data-cp-block]")
    assert regions["s3.body"]["data-cp-occupant"] == regions["s3.body"]["data-cp-default"] == "layer-stack:bdc-architecture"
    assert all(n.has_attr("hidden") for n in soup.select("[data-cp-note]"))                                  # el aviso de texto desalineado aparece solo tras agregar/reemplazar
    assert {t["data-cp-component"] for t in soup.select("template[data-cp-component]")} == {f"{o['component']}:{o['variant']}" for o in cp.component_library()}
    assert "cp-region-tools" not in str(_read(world, SEED_ID))                                               # nada de esto llega a la lectura
    assert not _read(world, SEED_ID, edit=True).select("[data-cp-region] .cp-region-tools, template")        # ni el seed expone herramientas


# ── Copias ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_copy_snapshots_the_composition_and_stays_independent_afterwards(world):
    c1 = _copy(world, A, title="Primera")
    assert _blocks(world, c1, {"s3.body": KC, "s2.body": FLOW}, 1).status_code == 200
    c2 = _copy(world, B, source=c1, title="Segunda")
    assert _stored(world, c2)["blocks"] == {"s2.body": FLOW, "s3.body": KC} and cp.get(c2)["revision"] == 1
    assert _blocks(world, c2, {"s3.body": None}, 1, email=B).status_code == 200
    assert cp.get(c1)["blocks"] == {"s2.body": FLOW, "s3.body": KC} and cp.get(c2)["blocks"] == {"s3.body": None}
    assert _blocks(world, c1, {}, 2).status_code == 200
    assert cp.get(c2)["blocks"] == {"s3.body": None}
    assert "blocks" not in _stored(world, _copy(world, A, source=SEED_ID))                                   # el seed siempre parte de la composición original


def test_an_instance_written_before_add_ons_renders_the_default_composition_and_can_change_it(world):
    payload = json.loads((REPO / "tests" / "fixtures" / "presentations" / "vanture_bdc_v1_instance.json").read_text(encoding="utf-8"))
    payload["owner_id"] = A
    world.directory.mkdir(parents=True, exist_ok=True)
    (world.directory / f"{payload['presentation_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    pid = payload["presentation_id"]
    assert cp.get(pid)["blocks"] == {} and cp.get(pid)["qr_sha"] is None
    assert [r["data-cp-region"] for r in _read(world, pid).select("[data-cp-region]")] == ["s3.body", "s4.body", "s5.body", "s12.bleed"]
    assert _blocks(world, pid, {"s5.body": KC}, payload["revision"]).status_code == 200


def test_a_damaged_composition_in_a_stored_instance_is_ignored_when_reading_never_breaks_the_document(world):
    pid = _copy(world, A)
    raw = _stored(world, pid)
    raw["blocks"] = {"s3.body": {"component": "layer-stack", "variant": "borrada"}, "s99.body": None, "s5.body": None, "s2.body": {"component": "logo-marquee", "variant": "partner-logo-marquee"}}
    (world.directory / f"{pid}.json").write_text(json.dumps(raw), encoding="utf-8")
    soup = _read(world, pid)
    assert [r["data-cp-region"] for r in soup.select("[data-cp-region]")] == ["s3.body", "s4.body", "s12.bleed"]     # lo inválido se ignora entrada por entrada; lo válido (s5 quitado) se respeta


# ── QR: validación (parametrización del validador PNG existente) ──────────────────────────────────────────────────────

def test_the_qr_validator_accepts_opaque_or_transparent_square_pngs_and_the_logo_validator_still_requires_transparency():
    assert cp.validate_qr_png(OPAQUE_QR)["width"] == 40 and cp.validate_qr_png(TRANSPARENT_QR)["height"] == 40
    with pytest.raises(cp.LogoValidationError, match="transparencia"):
        cp.validate_logo_png(OPAQUE_QR)                                                                      # el logo NO cambió: sigue exigiendo transparencia
    assert cp.validate_logo_png(TRANSPARENT_QR)["bytes"] == len(TRANSPARENT_QR)


@pytest.mark.parametrize("data, fragment", [
    (b"", "PNG"), (None, "PNG"), (b"GIF89a" + b"0" * 40, ".png"), (b"\xff\xd8\xff\xe0" + b"0" * 40, ".png"), (b"<svg xmlns='http://www.w3.org/2000/svg'/>", ".png"), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", ".png"),
    (make_png(width=40, height=20, color_type=2), "cuadrado"), (make_png(width=20, height=40, color_type=2), "cuadrado"),
    (make_png(width=40, height=40, color_type=2, extra=_chunk(b"acTL", struct.pack(">II", 2, 0))), "animados"),
    (OPAQUE_QR[:-20], "incompleto o dañado"),
    (make_png(width=1300, height=1300, color_type=2), "demasiado grande en píxeles"),
])
def test_every_invalid_qr_is_rejected_with_a_displayable_message(data, fragment):
    with pytest.raises(cp.LogoValidationError) as err:
        cp.validate_qr_png(data)
    assert fragment in str(err.value) and "logo" not in str(err.value).lower()                               # el mensaje habla del QR, no del logo


def test_a_corrupt_qr_checksum_and_an_oversized_qr_are_rejected():
    broken = bytearray(OPAQUE_QR)
    broken[-20] ^= 0xFF
    with pytest.raises(cp.LogoValidationError):
        cp.validate_qr_png(bytes(broken))
    with pytest.raises(cp.LogoValidationError, match="1 MB"):
        cp.validate_qr_png(OPAQUE_QR + b"0" * (cp.MAX_LOGO_BYTES + 1))


def test_a_nearly_square_qr_within_the_tolerance_is_accepted():
    assert cp.validate_qr_png(make_png(width=40, height=38, color_type=2))["width"] == 40


# ── QR: ciclo de vida ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_replacing_the_qr_stores_the_asset_next_to_the_instance_and_the_document_serves_it_from_a_private_url(world):
    pid = _copy(world, A)
    default_src = _read(world, pid).select_one("#s10 img[alt^='Código QR']")["src"]
    assert default_src.endswith("/img/38a9ed59b4.png") and "?v=" not in default_src
    r = _qr(world, pid, 1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 2 and body["qr_custom"] is True and re.fullmatch(rf"/presentaciones/{pid}/qr\?v=[0-9a-f]{{10}}", body["qr_url"])
    assert (world.directory / f"{pid}.qr.png").read_bytes() == OPAQUE_QR
    stored = _stored(world, pid)
    assert stored["qr"]["sha256"] and stored["qr"]["width"] == 40 and stored["revision"] == 2 and "<" not in json.dumps(stored["qr"])
    assert _read(world, pid).select_one("#s10 img[alt^='Código QR']")["src"] == body["qr_url"]
    served = world.client(HUB_ONLY).get(f"/presentaciones/{pid}/qr")                                         # contrato de lectura de R1: cualquier usuario admitido al Hub
    assert served.status_code == 200 and served.content == OPAQUE_QR and served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "private, no-store" and "sandbox" in served.headers["content-security-policy"] and served.headers["x-content-type-options"] == "nosniff"


def test_the_qr_never_touches_the_seed_or_other_copies_and_they_cannot_reach_it(world):
    mine, other = _copy(world, A), _copy(world, A, title="Otra")
    assert _qr(world, mine, 1).status_code == 200
    assert world.client(A).get(f"/presentaciones/{SEED_ID}/qr").status_code == 404
    assert world.client(A).get(f"/presentaciones/{other}/qr").status_code == 404                              # la otra copia no tiene QR propio
    assert _read(world, other).select_one("#s10 img[alt^='Código QR']")["src"].endswith("/img/38a9ed59b4.png")
    assert _read(world, SEED_ID).select_one("#s10 img[alt^='Código QR']")["src"].endswith("/img/38a9ed59b4.png")
    assert not (world.directory / f"{other}.qr.png").exists()
    for bad in ("..%2f..%2fpasswd", "../x", "no-es-un-uuid"):
        assert world.client(A).get(f"/presentaciones/{bad}/qr").status_code in (404, 422)                    # el id se valida: nunca se arma una ruta con lo recibido


def test_replacing_a_previous_qr_overwrites_the_same_file_and_removing_returns_to_the_institutional_one(world):
    pid = _copy(world, A)
    assert _qr(world, pid, 1, data=OPAQUE_QR).status_code == 200
    first_sha = _stored(world, pid)["qr"]["sha256"]
    assert _qr(world, pid, 2, data=TRANSPARENT_QR).status_code == 200
    assert (world.directory / f"{pid}.qr.png").read_bytes() == TRANSPARENT_QR and _stored(world, pid)["qr"]["sha256"] != first_sha and cp.get(pid)["revision"] == 3
    r = _qr(world, pid, 3, remove=True)
    assert r.status_code == 200 and r.json()["qr_custom"] is False and r.json()["revision"] == 4
    assert not (world.directory / f"{pid}.qr.png").exists() and "qr" not in _stored(world, pid)
    assert _read(world, pid).select_one("#s10 img[alt^='Código QR']")["src"].endswith("/img/38a9ed59b4.png")
    again = _qr(world, pid, 4, remove=True)
    assert again.status_code == 200 and cp.get(pid)["revision"] == 4                                          # ya usa el institucional: no se quema una revisión


def test_the_qr_change_is_checked_against_the_revision_and_invalid_files_change_nothing(world):
    pid = _copy(world, A)
    before = world.files()
    assert _qr(world, pid, 7).status_code == 409 and world.files() == before                                 # revisión desactualizada
    for data, name in ((b"GIF89a" + b"0" * 40, "qr.gif"), (make_png(width=40, height=20, color_type=2), "qr.png"), (OPAQUE_QR[:-20], "qr.png"), (b"x" * (cp.MAX_LOGO_BYTES + 100), "qr.png")):
        r = _qr(world, pid, 1, data=data, filename=name)
        assert r.status_code in (413, 422), (name, r.status_code)
        assert world.files() == before and not (world.directory / f"{pid}.qr.png").exists()
    r = world.client(A).post(f"/comercial/presentaciones/{pid}/qr", data={"revision": "1"}, files={"_": ("", b"")}, headers=ORIGIN)
    assert r.status_code == 422 and world.files() == before                                                  # sin archivo
    assert world.client(A).post(f"/comercial/presentaciones/{pid}/qr", json={"revision": 1}, headers=ORIGIN).status_code == 400
    assert world.client(A).post(f"/comercial/presentaciones/{pid}/qr", data={"file": "x"}, files={"_": ("", b"")}, headers=ORIGIN).status_code == 400   # sin revisión


def test_only_who_can_edit_the_instance_changes_its_qr_and_the_seed_never(world):
    pid = _copy(world, A)
    before = world.files()
    assert _qr(world, pid, 1, email=B).status_code == 403 and _qr(world, pid, 1, email=APP_OWNER).status_code == 403
    assert _qr(world, pid, 1, email=HUB_ONLY).status_code == 403 and _qr(world, SEED_ID, 1).status_code == 403
    assert _qr(world, pid, 1, headers={"Origin": "https://evil.example"}).status_code == 403
    assert world.client(None).post(f"/comercial/presentaciones/{pid}/qr", data={"revision": "1"}, files={"file": ("q.png", OPAQUE_QR, "image/png")}, headers=ORIGIN).status_code == 401
    assert world.files() == before
    assert world.client(INTRUDER).get(f"/presentaciones/{pid}/qr").status_code == 403 and world.client(None).get(f"/presentaciones/{pid}/qr", follow_redirects=False).status_code == 303
    assert _qr(world, pid, 1, email=OWNER_C).status_code == 200                                              # CONTENT_OWNER


def test_a_copy_inherits_the_qr_as_a_snapshot_and_deleting_removes_the_asset(world):
    c1 = _copy(world, A, title="Con QR")
    assert _qr(world, c1, 1).status_code == 200
    c2 = _copy(world, B, source=c1, title="Hereda")
    assert (world.directory / f"{c2}.qr.png").read_bytes() == OPAQUE_QR and _stored(world, c2)["qr"] == _stored(world, c1)["qr"]
    assert _qr(world, c2, 1, data=TRANSPARENT_QR, email=B).status_code == 200
    assert (world.directory / f"{c1}.qr.png").read_bytes() == OPAQUE_QR                                       # independiente
    assert _delete(world, A, c1).status_code == 200 and not (world.directory / f"{c1}.qr.png").exists() and (world.directory / f"{c2}.qr.png").exists()


def test_a_reference_whose_file_was_lost_falls_back_to_the_institutional_qr_instead_of_a_broken_image(world):
    pid = _copy(world, A)
    assert _qr(world, pid, 1).status_code == 200
    (world.directory / f"{pid}.qr.png").unlink()
    assert _read(world, pid).select_one("#s10 img[alt^='Código QR']")["src"].endswith("/img/38a9ed59b4.png")
    assert world.client(A).get(f"/presentaciones/{pid}/qr").status_code == 404


def test_the_edit_document_offers_the_qr_controls_only_to_editors_and_marks_the_image(world):
    pid = _copy(world, A)
    soup = _read(world, pid, edit=True)
    tools = soup.select_one("#s10 .cp-asset-tools[contenteditable=false]")
    assert tools and tools.select_one("[data-cp-qr-replace]") and tools.select_one("input[type=file][accept^='image/png'][hidden]") and tools.select_one("[data-cp-qr-reset]").has_attr("hidden")
    assert soup.select_one("#s10 img[data-cp-qr]")
    assert not _read(world, pid).select("[data-cp-qr], .cp-asset-tools")
    assert _qr(world, pid, 1).status_code == 200
    assert not _read(world, pid, edit=True).select_one("[data-cp-qr-reset]").has_attr("hidden")             # con QR propio se ofrece volver al institucional
    editor = _soup(world.client(A).get(f"/comercial/presentaciones/{pid}/editar").text).select_one("[data-cp-editor]")
    assert editor["data-qr-url"] == f"/comercial/presentaciones/{pid}/qr"


# ── QR: fallas de publicación (asset ↔ referencia) ─────────────────────────────────────────────────────────────────────

def test_a_failed_asset_push_reverts_everything_and_the_remote_is_re_synced(world, monkeypatch):
    pid = _copy(world, A)
    spaces = FakeSpaces(monkeypatch, push_results=[False])
    before = world.files()
    r = _qr(world, pid, 1)
    assert r.status_code == 503 and r.json()["reason"] == "durable_push_failed"
    assert world.files() == before and not (world.directory / f"{pid}.qr.png").exists() and "qr" not in _stored(world, pid) and _stored(world, pid)["revision"] == 1
    assert spaces.deleted == [world.directory / f"{pid}.qr.png"]                                              # sin QR previo: el remoto vuelve a «sin QR»
    assert spaces.pushed[0].name == f"{pid}.qr.png"                                                          # el asset se intentó ANTES que la instancia


def test_an_asset_published_but_not_the_reference_is_compensated_in_both_places(world, monkeypatch):
    pid = _copy(world, A)
    spaces = FakeSpaces(monkeypatch, push_results=[True, False])                                               # asset OK, instancia NO
    before = world.files()
    r = _qr(world, pid, 1)
    assert r.status_code == 503 and r.json()["reason"] == "durable_push_failed"
    assert world.files() == before and not (world.directory / f"{pid}.qr.png").exists()
    assert [p.name for p in spaces.pushed] == [f"{pid}.qr.png", f"{pid}.json", f"{pid}.json"]                  # asset, instancia (falla), instancia anterior reenviada
    assert spaces.deleted == [world.directory / f"{pid}.qr.png"]                                              # y el asset huérfano se retira del remoto: sin referencias rotas ni basura


def test_replacing_an_existing_qr_that_fails_restores_the_previous_bytes_locally_and_remotely(world, monkeypatch):
    pid = _copy(world, A)
    assert _qr(world, pid, 1, data=OPAQUE_QR).status_code == 200
    spaces = FakeSpaces(monkeypatch, push_results=[True, False])
    before = world.files()
    r = _qr(world, pid, 2, data=TRANSPARENT_QR)
    assert r.status_code == 503
    assert world.files() == before and (world.directory / f"{pid}.qr.png").read_bytes() == OPAQUE_QR           # el QR anterior sigue siendo el vigente
    assert _stored(world, pid)["revision"] == 2 and cp.get(pid)["qr_sha"] == _stored(world, pid)["qr"]["sha256"]
    assert [p.name for p in spaces.pushed][-2:] == [f"{pid}.qr.png", f"{pid}.json"] and not spaces.deleted    # el remoto recibe de nuevo el QR y la instancia anteriores


def test_when_even_the_compensation_cannot_be_confirmed_the_outcome_is_reported_as_uncertain_not_as_success(world, monkeypatch):
    pid = _copy(world, A)
    spaces = FakeSpaces(monkeypatch, push_results=[True, False, False])                                        # asset OK, instancia NO, y la compensación de la instancia tampoco
    r = _qr(world, pid, 1)
    assert r.status_code == 503 and r.json()["reason"] == "outcome_unknown" and "No pudimos confirmar" in r.json()["message"]
    assert not (world.directory / f"{pid}.qr.png").exists() and "qr" not in _stored(world, pid)                # el local nunca queda a medias
    assert spaces.pushed


def test_removing_a_qr_whose_remote_delete_fails_restores_it(world, monkeypatch):
    pid = _copy(world, A)
    assert _qr(world, pid, 1).status_code == 200
    spaces = FakeSpaces(monkeypatch)
    monkeypatch.setattr(remote_store, "delete_path", lambda p: spaces.deleted.append(p) or False)
    before = world.files()
    r = _qr(world, pid, 2, remove=True)
    assert r.status_code in (503,) and world.files() == before and (world.directory / f"{pid}.qr.png").read_bytes() == OPAQUE_QR
    assert _stored(world, pid)["revision"] == 2 and "qr" in _stored(world, pid)


def test_remote_mode_without_credentials_and_read_only_never_write_a_qr(world, monkeypatch):
    pid = _copy(world, A)
    before = world.files()
    FakeSpaces(monkeypatch, enabled=False)
    r = _qr(world, pid, 1)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete" and world.files() == before
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    assert _qr(world, pid, 1).status_code == 503 and world.files() == before


def test_a_local_disk_failure_while_writing_the_reference_removes_the_new_asset(world, monkeypatch):
    pid = _copy(world, A)
    assert _qr(world, pid, 1, data=OPAQUE_QR).status_code == 200
    before = world.files()

    def boom(path, payload):
        raise cp.PresentationStorageError("disco lleno")
    monkeypatch.setattr(cp, "_write_atomic", boom)
    r = _qr(world, pid, 2, data=TRANSPARENT_QR)
    assert r.status_code == 503 and world.files() == before and (world.directory / f"{pid}.qr.png").read_bytes() == OPAQUE_QR


def test_a_remote_copy_publishes_the_inherited_qr_before_the_instance(world, monkeypatch):
    src = _copy(world, A)
    assert _qr(world, src, 1).status_code == 200
    spaces = FakeSpaces(monkeypatch)
    new = _copy(world, A, source=src, title="Hereda remoto")
    assert [p.name for p in spaces.pushed] == [f"{new}.qr.png", f"{new}.json"]                                # el asset primero: la instancia nunca aparece remotamente sin él
    spaces2 = FakeSpaces(monkeypatch, push_results=[False, False])
    r = world.client(A).post(f"/comercial/presentaciones/{src}/copiar", json={"title": "Falla"}, headers=ORIGIN)
    assert r.status_code == 503 and not [p for p in world.directory.glob("*.qr.png") if p.stem.split(".")[0] not in {src, new}]
    assert spaces2.pushed


# ── Sin infraestructura nueva ───────────────────────────────────────────────────────────────────────────────────────────

def test_add_ons_introduce_no_environment_variables_roles_allowlists_or_dependencies():
    for name in ("webapp/services/commercial_presentations.py", "webapp/routers/commercial_presentations.py"):
        source = (REPO / name).read_text(encoding="utf-8")
        assert "os.environ" not in source and "os.getenv" not in source and "_env(" not in source, name
    assert "ADDON" not in (REPO / "config.py").read_text(encoding="utf-8").upper().replace("ADDONS_", "")
    service = (REPO / "webapp/services/commercial_presentations.py").read_text(encoding="utf-8")
    assert "is_content_owner_user" in service and "def can_edit_presentation_user" in service              # la autorización es la existente


def test_the_script_and_style_changes_for_add_ons_are_confined_to_edit_mode_and_wire_the_editor():
    js = (REPO / "content_packs/datasphere_core/assets/presentations/vanture-bdc/vanture.js").read_text(encoding="utf-8")
    assert "cp:composition-changed" in js and "data-cp-component" in js and "cloneNode(true)" in js and "innerHTML" not in js        # se clona lo que ya está: nunca se arma markup
    css = (REPO / "content_packs/datasphere_core/assets/presentations/vanture-bdc/vanture.css").read_text(encoding="utf-8")
    assert ".cp-region-gap{margin-top:56px}" in css and "html[data-cp-edit] .cp-region-tools" in css
    hub = (REPO / "webapp/static/commercial_presentations.js").read_text(encoding="utf-8")
    assert "cp:composition-changed" in hub and "blocks: blocksNow()" in hub and "data-qr-url" in hub and "QR_MAX" in hub
