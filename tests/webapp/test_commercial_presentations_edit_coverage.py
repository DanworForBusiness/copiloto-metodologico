"""Presentaciones V1 — cobertura de la edición textual de las 12 slides, bloques gobernados y compatibilidad de instancias.

Qué fija este archivo:
  * G1 — cada una de las 12 slides tiene campos editables, y TODO texto visible del documento es: un campo editable, el espejo de uno, texto de un bloque gobernado (diagrama / marquee),
    el pie legal o cromo declarado (numeración, íconos, «Ver la presentación»). Un texto nuevo en el template sin clasificar rompe este test: el inventario es ejecutable.
  * G2/G7 — los bloques gobernados (arquitectura BDC, stack Knowledge Core, flujo del ecosistema, marquee de clientes) están identificados con `data-cp-block` y no contienen campos.
  * G3 — guardar los campos de las 12 slides en un solo guardado y volver a abrir los conserva.
  * G4 — copias de copias independientes entre sí y del original.
  * G6 — una instancia creada con los 20 campos originales sigue renderizando y se puede editar.
  * Evolución — claves y límites del template solo pueden crecer sin subir `template_version`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup, Comment, Doctype

from tests.webapp.test_commercial_presentations_access import (
    A, B, OWNER_C, SEED_ID, _copy, _save, world,
)
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]
SPEC = cp.TEMPLATES["vanture-bdc"]
SEED = cp._seed_raw("vanture-bdc")["content"]
FIXTURES = REPO / "tests" / "fixtures" / "presentations"
ASSETS = REPO / "content_packs" / "datasphere_core" / "assets" / "presentations" / "vanture-bdc"
SLIDES = [f"s{i}" for i in range(1, 13)]
BLOCKS = {"bdc-architecture": "s3", "knowledge-core-stack": "s4", "open-ecosystem-flow": "s5", "partner-logo-marquee": "s12"}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _edit_doc(world, pid, email=A):
    r = world.client(email).get(f"/presentaciones/{pid}?embed=1&edit=1")
    assert r.status_code == 200
    return _soup(r.text)


def _write_instance(world, payload: dict) -> str:
    world.directory.mkdir(parents=True, exist_ok=True)
    (world.directory / f"{payload['presentation_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload["presentation_id"]


# ── G1 — cobertura ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_every_one_of_the_twelve_slides_has_editable_fields_in_the_contract_and_in_the_document(world):
    assert {f.section for f in SPEC.fields} == set(cp._SLIDE.values()) and len(cp._SLIDE) == 12
    soup = _edit_doc(world, _copy(world, A))
    for sid in SLIDES:
        assert soup.select(f"#{sid} [data-cp-field]"), f"la slide {sid} no tiene ningún texto editable"
    assert len(soup.select("[data-cp-field]")) == len(SPEC.fields) == len(SEED)


# Texto que NO es contenido editable y por qué: numeración y glifos (estructura que gobierna el template), cue de scroll y etiqueta del riel de la portada (cromo de la UI).
CHROME = {"Portada", "Ver la presentación"}
GLYPHS = {"◈", "✓", "★", "🏆", "∅", "⇄"}


def _is_declared_chrome(text: str) -> bool:
    return text in CHROME or text in GLYPHS or bool(re.fullmatch(r"\d\d", text)) or bool(re.fullmatch(r"\d\d / 11 ·", text)) or text == "Siguiente ·"


def test_every_visible_text_is_editable_a_mirror_a_governed_block_the_legal_footer_or_declared_chrome(world):
    soup = _edit_doc(world, _copy(world, A))
    unclassified = []
    for node in soup.find_all(string=True):
        if isinstance(node, (Comment, Doctype)) or node.parent.name in {"script", "style", "title"}:
            continue
        text = re.sub(r"\s+", " ", str(node)).strip()
        if not text:
            continue
        if node.find_parent(class_="cp-slide-tools") or node.find_parent(attrs={"data-cp-kicker-no": True}) or node.find_parent(attrs={"data-cp-derived": True}):
            continue                                                                             # herramientas de edición (X / Restaurar), numeración dinámica y texto derivado: cromo del editor
        if node.find_parent(attrs={"data-cp-field": True}) or node.find_parent(attrs={"data-cp-mirror": True}) or node.find_parent(attrs={"data-cp-block": True}) or node.find_parent("footer"):
            continue
        if _is_declared_chrome(text):
            continue
        unclassified.append(text[:80])
    assert not unclassified, f"texto visible sin clasificar (¿campo nuevo, bloque o cromo?): {unclassified}"


def test_the_governed_blocks_are_identified_in_their_slides_and_hold_no_editable_field(world):
    soup = _edit_doc(world, _copy(world, A))
    blocks = {b["data-cp-block"]: b for b in soup.select("[data-cp-block]")}
    assert set(blocks) == set(BLOCKS) and len(soup.select("[data-cp-block]")) == len(BLOCKS)
    for name, sid in BLOCKS.items():
        assert blocks[name].find_parent("section")["id"] == sid, name
        assert not blocks[name].select("[data-cp-field], [contenteditable]"), f"{name}: un bloque gobernado no expone sus cajas como campos"
    # el mismo bloque se ve igual en lectura: el atributo es solo identificación, no cambia el render
    plain = _soup(world.client(A).get(f"/presentaciones/{SEED_ID}?embed=1").text)
    assert {b["data-cp-block"] for b in plain.select("[data-cp-block]")} == set(BLOCKS)


def test_the_governed_block_boundaries_exclude_the_narrative_text_around_them(world):
    soup = _edit_doc(world, _copy(world, A))
    arch = soup.select_one('[data-cp-block="bdc-architecture"]')
    assert "arch" in arch["class"]
    for key in ("s3_title", "s3_lead", "s3_lead_highlight", "s3_callout_intro", "s3_callout_highlight", "s3_callout_tail"):       # título, subtítulo y cierre de la slide 3 son narrativa editable, fuera del diagrama
        leaf = soup.select_one(f'[data-cp-field="{key}"]')
        assert leaf is not None and leaf.find_parent(attrs={"data-cp-block": True}) is None, key
    assert arch.find_next("aside", class_="callout") is not None and arch.find_next("aside", class_="callout").select_one('[data-cp-field="s3_callout_intro"]')


# ── Clasificación de campos (línea vs párrafo) ────────────────────────────────────────────────────────────────────────

def test_fields_are_classified_by_nature_and_no_seed_text_needs_a_line_break():
    assert {f.kind for f in SPEC.fields} == {"line", "paragraph"}
    paragraphs = [f for f in SPEC.fields if f.kind == "paragraph"]
    assert len(paragraphs) >= 40 and all(f.max_len >= 200 for f in paragraphs)
    assert all(f.max_len <= 200 for f in SPEC.fields if f.kind == "line")
    assert len({(f.section, f.label) for f in SPEC.fields}) == len(SPEC.fields)                  # cada campo se distingue por (slide, etiqueta): es lo que ve el usuario en un error de validación
    for field in SPEC.fields:
        assert re.search(r"[\r\n  ]", SEED[field.key]) is None, f"{field.key}: el seed no necesita saltos de línea, por eso el contrato sigue siendo de una línea"


@pytest.mark.parametrize("field", SPEC.fields, ids=lambda f: f.key)
def test_each_field_accepts_its_limit_and_rejects_empty_overlong_and_line_breaks(field):
    ok = ("x" * field.max_len)
    assert cp.validate_fields("vanture-bdc", {field.key: ok}) == {field.key: ok}
    for bad in ("", "   ", "x" * (field.max_len + 1), "uno\ndos", "uno\r\ndos", "uno dos"):
        with pytest.raises(cp.PresentationValidationError):
            cp.validate_fields("vanture-bdc", {field.key: bad})


# ── G3 — persistencia de los campos de las 12 slides ──────────────────────────────────────────────────────────────────

def test_saving_a_text_in_every_field_of_the_twelve_slides_is_kept_after_reload_and_never_touches_the_seed(world):
    seed_before = world.client(A).get(f"/presentaciones/{SEED_ID}").text
    pid = _copy(world, A)
    edits = {f.key: f"E:{f.key}" for f in SPEC.fields}
    assert all(len(v) <= SPEC.field(k).max_len for k, v in edits.items())
    r = _save(world, A, pid, edits, 1)
    assert r.status_code == 200, r.text
    assert cp.get(pid)["revision"] == 2 and cp.get(pid)["content"] == edits                      # en disco: todo y solo lo editado
    soup = _edit_doc(world, pid)
    for sid in SLIDES:
        assert soup.select(f"#{sid} [data-cp-field]")
    for leaf in soup.select("[data-cp-field]"):
        assert leaf.get_text() == edits[leaf["data-cp-field"]] == leaf["data-original"]
    for mirror in soup.select("[data-cp-mirror]"):                                               # los espejos (riel, detalle del pilar, encabezado) reflejan lo guardado
        assert mirror.get_text() == edits[mirror["data-cp-mirror"]]
    plain = world.client(A).get(f"/presentaciones/{pid}").text
    assert all(re.sub(r"\s+", " ", v) in re.sub(r"\s+", " ", _soup(plain).get_text(" ")) for v in edits.values())
    assert world.client(A).get(f"/presentaciones/{SEED_ID}").text == seed_before                 # el seed institucional no cambió


# ── G4 — copias independientes ────────────────────────────────────────────────────────────────────────────────────────

def test_a_copy_of_a_copy_snapshots_the_edited_content_and_every_edit_stays_in_its_own_instance(world):
    c1 = _copy(world, A, title="Primera")
    assert _save(world, A, c1, {"s3_title": "Título de c1", "s9_path1_title": "Camino de c1", "s7_p2_claim_3": "Afirmación de c1"}, 1).status_code == 200
    c2 = _copy(world, B, source=c1, title="Copia de la copia")
    assert cp.get(c2)["revision"] == 1 and cp.get(c2)["copied_from"]["presentation_id"] == c1
    assert cp.get(c2)["content"]["s3_title"] == "Título de c1" and cp.get(c2)["content"]["s9_path1_title"] == "Camino de c1"      # snapshot completo del origen al copiar
    assert _save(world, B, c2, {"s3_title": "Título de c2", "s6_cfo_prod_1": "Producto de c2"}, 1).status_code == 200
    c1_after = cp.get(c1)["content"]
    assert c1_after["s3_title"] == "Título de c1" and c1_after["s6_cfo_prod_1"] == SEED["s6_cfo_prod_1"] and cp.get(c1)["revision"] == 2
    assert cp.get(SEED_ID)["content"]["s3_title"] == SEED["s3_title"]
    assert _save(world, A, c1, {"s3_title": "Otra vez c1"}, 2).status_code == 200
    assert cp.get(c2)["content"]["s3_title"] == "Título de c2"                                    # editar c1 no mueve a c2


# ── G6 — instancias creadas con el esquema anterior ───────────────────────────────────────────────────────────────────

def test_an_instance_created_with_the_original_twenty_fields_renders_everything_and_can_be_edited(world):
    payload = json.loads((FIXTURES / "vanture_bdc_v1_instance.json").read_text(encoding="utf-8"))
    assert set(payload["content"]) < set(SPEC.field_keys) and len(payload["content"]) == 20 and payload["template_version"] == SPEC.version == 1
    payload["owner_id"] = A
    pid = _write_instance(world, payload)
    soup = _edit_doc(world, pid)
    leaves = {l["data-cp-field"]: l.get_text() for l in soup.select("[data-cp-field]")}
    assert set(leaves) == set(SPEC.field_keys)
    for key, value in leaves.items():                                                            # lo propio de la instancia se conserva; lo nuevo cae al valor del seed
        assert value == payload["content"].get(key, SEED[key]), key
    r = _save(world, A, pid, {"s6_cfo_uc_1": "Caso editado", "s4_title": "Nuevo título"}, payload["revision"])
    assert r.status_code == 200, r.text
    view = cp.get(pid)
    assert view["content"]["s6_cfo_uc_1"] == "Caso editado" and view["content"]["hero_lead"] == payload["content"]["hero_lead"] and view["content"]["s5_title"] == SEED["s5_title"]
    assert view["revision"] == payload["revision"] + 1 and view["template_version"] == 1
    again = _save(world, A, pid, {"s8_title": "Otro"}, view["revision"])
    assert again.status_code == 200
    assert _save(world, A, pid, {"s8_title": "Viejo"}, payload["revision"]).status_code == 409    # el control de revisión sigue vigente


def test_an_unknown_or_forged_key_is_still_rejected_atomically_with_the_larger_contract(world):
    pid = _copy(world, A)
    before = world.files()
    r = _save(world, A, pid, {"s3_title": "ok", "s3_arquitectura_caja_1": "forjado"}, 1)
    assert r.status_code == 422 and "forjado" not in r.text
    assert world.files() == before                                                               # nada se guardó (todo o nada)


def test_a_validation_error_on_a_new_field_names_the_slide_and_the_field_without_echoing_the_value(world):
    pid = _copy(world, A)
    r = _save(world, A, pid, {"s6_cfo_prod_3": "x" * 500}, 1)
    assert r.status_code == 422 and "Slide 6" in r.text and "producto de datos 3" in r.text and "xxxxx" not in r.text


# ── Evolución del template: las claves y los límites solo pueden crecer ───────────────────────────────────────────────

def test_the_full_field_contract_can_only_grow_within_the_same_template_version():
    pinned = json.loads((FIXTURES / "vanture_bdc_v1_field_limits.json").read_text(encoding="utf-8"))
    assert len(pinned) == 206
    for key, limit in pinned.items():
        field = SPEC.field(key)
        assert field is not None, f"{key} desapareció sin subir template_version"
        assert field.max_len >= limit, f"{key} redujo su límite sin subir template_version"


# ── Espejos y comportamiento de edición ───────────────────────────────────────────────────────────────────────────────

def test_mirrors_are_read_only_copies_of_a_field_and_the_rail_mirrors_each_section_name(world):
    soup = _edit_doc(world, _copy(world, A))
    keys = {m["data-cp-mirror"] for m in soup.select("[data-cp-mirror]")}
    assert keys <= set(SPEC.field_keys)
    assert len(soup.select("nav.rail [data-cp-mirror]")) == 11 and {f"s{i}_nav" for i in range(2, 13)} <= keys
    for m in soup.select("[data-cp-mirror]"):
        assert not m.has_attr("contenteditable") and not m.find_parent(attrs={"contenteditable": True}) and not m.select("[data-cp-field]")
    for i in range(1, 5):
        assert f"s7_p{i}_title" in keys                                                           # el detalle del pilar refleja su título


def test_the_edit_mode_behaviours_for_links_mirrors_and_pillars_exist_in_the_template_assets():
    js = (ASSETS / "vanture.js").read_text(encoding="utf-8")
    assert "data-cp-mirror" in js and "closest('a')" in js and "preventDefault" in js
    css = (ASSETS / "vanture.css").read_text(encoding="utf-8")
    assert "html[data-cp-edit] .reveal-fade__detail" in css                                        # el detalle del pilar no tapa el título al editar
