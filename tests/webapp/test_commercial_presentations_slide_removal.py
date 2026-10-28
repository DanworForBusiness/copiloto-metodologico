"""Presentaciones V1 — eliminar slides de una copia («X» por slide en el modo edición) con numeración, riel y «Siguiente» dinámicos.

Contrato: la instancia guarda SOLO ids (`removed_slides`, opcional: sin slides eliminadas el archivo conserva la forma de siempre); la portada (s1) no se quita; el modo lectura omite por completo las slides
eliminadas (su texto no viaja en el HTML); el modo edición las muestra colapsadas con «Restaurar»; la copia hereda la composición del origen y es independiente después.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import (
    A, APP_OWNER, B, OWNER_C, SEED_ID, _copy, _save, world,
)
import config
from webapp.services import auth_service
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]
SEED = cp._seed_raw("vanture-bdc")["content"]
ALL = [f"s{i}" for i in range(1, 13)]


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _read(world, pid, email=A):
    r = world.client(email).get(f"/presentaciones/{pid}?embed=1")
    assert r.status_code == 200
    return r.text


def _remove(world, pid, ids, revision, email=A, fields=None):
    body = {"revision": revision, "removed_slides": ids}
    if fields is not None:
        body["fields"] = fields
    return world.client(email).post(f"/comercial/presentaciones/{pid}", json=body, headers={"Origin": "http://testserver"})


def _stored(world, pid):
    return json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))


# ── Contrato / validación ────────────────────────────────────────────────────────────────────────────────────────────

def test_validate_removed_slides_accepts_removable_ids_in_document_order_and_rejects_everything_else():
    assert cp.validate_removed_slides([]) == []
    assert cp.validate_removed_slides(["s9", "s4"]) == ["s4", "s9"]
    for bad in ("s4", None, ["s1"], ["s13"], ["s4", "s4"], [4], [["s4"]], ["S4"], ["s4; drop"], {"s4": True}):
        with pytest.raises(cp.PresentationValidationError):
            cp.validate_removed_slides(bad)


@pytest.mark.parametrize("bad", ["s4", ["s1"], ["s99"], ["s4", "s4"], [4], {"s4": True}])
def test_a_forged_removed_slides_payload_is_a_422_and_saves_nothing(world, bad):
    pid = _copy(world, A)
    before = world.files()
    r = _remove(world, pid, bad, 1, fields={"s3_title": "no debería guardarse"})
    assert r.status_code == 422, r.text
    assert world.files() == before                                                               # todo o nada: ni siquiera los campos válidos del mismo guardado


def test_removing_slides_needs_no_field_change_and_a_save_without_either_is_still_rejected(world):
    pid = _copy(world, A)
    assert world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 1}, headers={"Origin": "http://testserver"}).status_code == 422
    r = _remove(world, pid, ["s9", "s4"], 1)
    assert r.status_code == 200, r.text
    assert r.json()["removed_slides"] == ["s4", "s9"] and r.json()["revision"] == 2


def test_the_instance_stores_only_ids_and_keeps_its_old_shape_when_nothing_is_removed(world):
    pid = _copy(world, A)
    assert "removed_slides" not in _stored(world, pid)
    assert _remove(world, pid, ["s4", "s6"], 1, fields={"s3_title": "Nuevo"}).status_code == 200      # campos + slides en UNA revisión
    stored = _stored(world, pid)
    assert stored["removed_slides"] == ["s4", "s6"] and stored["revision"] == 2 and stored["content"]["s3_title"] == "Nuevo"
    assert not any("<" in str(v) for v in stored["removed_slides"])                              # ids, nunca markup
    assert _remove(world, pid, [], 2).status_code == 200
    restored = _stored(world, pid)
    assert "removed_slides" not in restored and restored["revision"] == 3                        # restaurar todo devuelve la forma de siempre


def test_a_stale_revision_never_applies_the_removal(world):
    pid = _copy(world, A)
    assert _save(world, A, pid, {"s3_title": "uno"}, 1).status_code == 200
    r = _remove(world, pid, ["s4"], 1)
    assert r.status_code == 409 and "removed_slides" not in _stored(world, pid)


# ── Lectura: numeración, riel y «Siguiente» dinámicos; el texto eliminado no viaja ───────────────────────────────────────

def test_the_read_document_omits_removed_slides_and_renumbers_rail_and_next_links(world):
    pid = _copy(world, A)
    assert _remove(world, pid, ["s4", "s6"], 1).status_code == 200
    html = _read(world, pid)
    soup = _soup(html)
    assert [s["id"] for s in soup.select("section[id^=s]")] == ["s1", "s2", "s3", "s5", "s7", "s8", "s9", "s10", "s11", "s12"]
    assert SEED["s4_title"] not in html and SEED["s6_title"] not in html and SEED["s6_cfo_uc_1"] not in html and "Capital de trabajo en tiempo real" not in html      # lo eliminado no viaja
    kickers = [k.get_text(strip=True) for k in soup.select(".topic-kicker")]
    assert [k.split(" · ")[0].replace(" ", "") for k in kickers] == [f"{i:02d}/09" for i in range(1, 10)]                       # 01/09 … 09/09 sobre las visibles
    assert [a["href"] for a in soup.select("nav.rail a")] == ["#s1", "#s2", "#s3", "#s5", "#s7", "#s8", "#s9", "#s10", "#s11", "#s12"]
    nxt = {a.find_parent("section")["id"]: (a["href"], a.get_text(strip=True)) for a in soup.select("a.next-link")}
    assert nxt["s2"] == ("#s3", f"Siguiente · {SEED['s2_next']}")                                 # el siguiente inmediato sigue siendo el original: conserva su texto
    assert nxt["s3"] == ("#s5", f"Siguiente · {SEED['s5_nav']}")                                  # el original (s4) ya no existe: apunta a la siguiente visible y nombra ESA slide
    assert nxt["s5"] == ("#s7", f"Siguiente · {SEED['s7_nav']}")
    assert nxt["s11"] == ("#s12", f"Siguiente · {SEED['s11_next']}") and "s12" not in nxt
    assert soup.select_one("#s1 a.scroll-cue")["href"] == "#s2"
    assert not soup.select("[data-cp-removed], .cp-slide-tools, [contenteditable]")               # el modo lectura no trae nada del editor


@pytest.mark.parametrize("removed, first, last_with_next", [(["s2"], "#s3", "s11"), (["s12"], "#s2", None), (["s11", "s12"], "#s2", None)])
def test_removing_the_edges_keeps_the_cue_and_the_last_slide_coherent(world, removed, first, last_with_next):
    pid = _copy(world, A)
    assert _remove(world, pid, removed, 1).status_code == 200
    soup = _soup(_read(world, pid))
    assert soup.select_one("#s1 a.scroll-cue")["href"] == first
    ids = [s["id"] for s in soup.select("section[id^=s]") if s["id"] != "s1"]
    assert ids and ids[-1] not in {r for r in removed}
    assert ids[-1] not in {s.find_parent("section")["id"] for s in soup.select("a.next-link")}   # la última visible no ofrece «Siguiente»
    kickers = [k.get_text(strip=True).split(" · ")[0].replace(" ", "") for k in soup.select(".topic-kicker")]
    assert kickers[0].startswith("01/") and kickers == [f"{i:02d}/{len(ids):02d}" for i in range(1, len(ids) + 1)]


def test_removing_every_slide_leaves_only_the_cover_without_errors(world):
    pid = _copy(world, A)
    assert _remove(world, pid, list(ALL[1:]), 1).status_code == 200
    soup = _soup(_read(world, pid))
    assert [s["id"] for s in soup.select("section[id^=s]")] == ["s1"] and not soup.select("a.next-link, nav.rail a[href^='#s2']") and soup.select_one("#s1 a.scroll-cue") is None


def test_the_seed_and_an_untouched_copy_still_render_all_twelve_slides_with_the_original_numbering(world):
    for pid in (SEED_ID, _copy(world, A)):
        soup = _soup(_read(world, pid))
        assert [s["id"] for s in soup.select("section[id^=s]")] == ALL
        assert [k.get_text(strip=True).split(" · ")[0].replace(" ", "") for k in soup.select(".topic-kicker")] == [f"{i:02d}/11" for i in range(1, 12)]


# ── Edición: X por slide, colapsada con «Restaurar», y el numerado ya calculado ─────────────────────────────────────────

def test_the_edit_document_offers_the_x_on_every_slide_but_the_cover_and_shows_removed_ones_collapsed(world):
    pid = _copy(world, A)
    assert _remove(world, pid, ["s4"], 1).status_code == 200
    soup = _soup(world.client(A).get(f"/presentaciones/{pid}?embed=1&edit=1").text)
    assert [s["id"] for s in soup.select("section[id^=s]")] == ALL                              # en edición están todas (para poder restaurar)
    assert not soup.select("#s1 .cp-slide-tools")
    for sid in ALL[1:]:
        tools = soup.select_one(f"#{sid} > .cp-slide-tools")
        assert tools is not None and tools["contenteditable"] == "false"
        assert tools.select_one(f'button.cp-slide-remove[data-cp-remove="{sid}"]')["aria-label"] == "Eliminar esta slide" and tools.select_one(f'button.cp-slide-restore[data-cp-restore="{sid}"]')
    assert [s["id"] for s in soup.select("section[data-cp-removed]")] == ["s4"]
    assert soup.select_one("#s4 [data-cp-field]") is not None                                    # sus textos siguen ahí: restaurar vuelve con lo editado
    assert [a.has_attr("hidden") for a in soup.select("nav.rail a")][1:] == [False, False, True] + [False] * 8
    assert soup.select_one("#s3 a.next-link")["href"] == "#s5"


def test_the_x_is_part_of_the_edit_document_only_and_never_reaches_a_reader(world):
    pid = _copy(world, A)
    assert "cp-slide-remove" not in _read(world, pid) and "cp-slide-remove" not in _read(world, SEED_ID)
    assert not _soup(world.client(A).get(f"/presentaciones/{SEED_ID}?embed=1&edit=1").text).select(".cp-slide-tools")      # la semilla no se edita: tampoco expone la X


# ── Copias ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_copy_inherits_the_composition_of_its_source_and_is_independent_afterwards(world):
    c1 = _copy(world, A, title="Primera")
    assert _remove(world, c1, ["s4", "s9"], 1).status_code == 200
    c2 = _copy(world, B, source=c1, title="Segunda")
    assert _stored(world, c2)["removed_slides"] == ["s4", "s9"] and cp.get(c2)["revision"] == 1
    assert "s4" not in [s["id"] for s in _soup(_read(world, c2, B)).select("section[id^=s]")]
    assert _remove(world, c2, ["s9"], 1, email=B).status_code == 200                              # c2 restaura s4
    assert cp.get(c1)["removed_slides"] == ["s4", "s9"] and cp.get(c2)["removed_slides"] == ["s9"]
    assert _remove(world, c1, [], 2).status_code == 200
    assert cp.get(c2)["removed_slides"] == ["s9"]                                                # y editar c1 no mueve a c2
    c3 = _copy(world, A, source=SEED_ID)
    assert "removed_slides" not in _stored(world, c3)                                            # copiar el seed siempre parte completo


# ── Autorización y persistencia ─────────────────────────────────────────────────────────────────────────────────────────

def test_only_who_can_edit_the_instance_can_remove_slides_and_the_seed_never(world, monkeypatch):
    pid = _copy(world, A)
    before = world.files()
    assert _remove(world, pid, ["s4"], 1, email=B).status_code == 403                             # otro usuario Comercial
    assert _remove(world, pid, ["s4"], 1, email=APP_OWNER).status_code == 403                     # administrador ≠ editor de contenido
    assert _remove(world, SEED_ID, ["s4"], 1).status_code == 403                                  # el seed es inmutable
    assert world.client(None).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "removed_slides": ["s4"]}, headers={"Origin": "http://testserver"}).status_code == 401
    assert world.files() == before
    assert _remove(world, pid, ["s4"], 1, email=OWNER_C).status_code == 200                       # CONTENT_OWNER (transversal)
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "owner-c,app-owner")
    assert _remove(world, pid, ["s4", "s5"], 2, email=APP_OWNER).status_code == 200               # …o quien lo sea de forma explícita


def test_a_persistence_failure_never_leaves_a_silent_half_state(world, monkeypatch):
    pid = _copy(world, A)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    before = world.files()
    r = _remove(world, pid, ["s4"], 1)
    assert r.status_code == 503 and world.files() == before
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    assert _remove(world, pid, ["s4"], 1).status_code == 200                                      # nada quedó a medias: se puede reintentar con la misma revisión


def test_an_instance_written_before_this_contract_has_no_removed_slides_and_can_remove_some(world):
    payload = json.loads((REPO / "tests" / "fixtures" / "presentations" / "vanture_bdc_v1_instance.json").read_text(encoding="utf-8"))
    payload["owner_id"] = A
    world.directory.mkdir(parents=True, exist_ok=True)
    (world.directory / f"{payload['presentation_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    assert "removed_slides" not in payload and cp.get(payload["presentation_id"])["removed_slides"] == []
    assert [s["id"] for s in _soup(_read(world, payload["presentation_id"])).select("section[id^=s]")] == ALL
    assert _remove(world, payload["presentation_id"], ["s7"], payload["revision"]).status_code == 200


def test_the_editor_and_the_document_scripts_wire_the_x_to_the_save_bar():
    js = (REPO / "content_packs" / "datasphere_core" / "assets" / "presentations" / "vanture-bdc" / "vanture.js").read_text(encoding="utf-8")
    assert "cp:slides-changed" in js and "data-cp-removed" in js and "renumber" in js
    hub = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
    assert "cp:slides-changed" in hub and "slidesChanged()" in hub and "removed_slides: removedNow()" in hub
