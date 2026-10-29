"""Presentaciones V1 — componentes institucionales CERRADOS (`ecosystem-flow`, `logo-marquee`): Duplicar ya no depende de que el renderer sea editable.

Una copia cerrada es un componente propio con `params: null`: identidad, nombre, descripción, responsable y procedencia propios, pero SIGUE la definición institucional (no congela el contenido visual ni guarda
HTML/CSS/JS). De ella solo se editan nombre y descripción; se inserta en una presentación como snapshot (`params: null`) y eliminarla del catálogo no rompe ningún uso. Autorizaciones sin cambios.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.webapp.test_commercial_presentations_access import (
    A, APP_OWNER, B, HUB_ONLY, INTRUDER, ORIGIN, OWNER_C, SEED_ID, _copy, _save, world,
)
from tests.webapp.test_commercial_presentations_components import (
    BDC_REF, FLOW_REF, KC_REF, MARQUEE_REF, _blocks, _bdc_params, _catalog, _del, _doc, _dup, _edit, _mk, _pstored, _stored, _tweak, _use,
)
from webapp.services import commercial_presentations as cp

ALL4 = [BDC_REF, KC_REF, FLOW_REF, MARQUEE_REF]
CLOSED = [FLOW_REF, MARQUEE_REF]
FLOW, MARQUEE = "ecosystem-flow", "logo-marquee"


def _files(world):
    return sorted(p.name for p in (world.directory / "components").glob("*.json")) if (world.directory / "components").exists() else []


# ── 1. Duplicar los cuatro institucionales con perfiles autorizados ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [A, B, OWNER_C, APP_OWNER])
def test_every_authorized_commercial_profile_duplicates_the_four_institutional_components(world, email):
    for ref in ALL4:
        r = _dup(world, email, ref, name=f"Copia de {ref.split(':')[1]}", description="descripción propia")
        assert r.status_code == 200, (email, ref, r.text)
        stored = _stored(world, r.json()["component_id"])
        closed = ref in CLOSED
        assert (stored["params"] is None) == closed                                                           # cerrado: `params: null`; con contrato: los parámetros institucionales
        assert stored["component"] == ref.split(":")[0] and stored["revision"] == 1
        assert stored["owner_id"] == email and stored["created_by_id"] == email                                # responsable = quien duplica, nunca el institucional
        assert stored["based_on"] == {"ref": ref, "name": cp.COMPONENTS[ref.split(":")[0]]["variants"][ref.split(":")[1]], "revision": None, "origin": "institutional"}
        assert set(stored) == {"schema_version", "component_id", "component", "name", "description", "params", "revision", "owner_id", "created_by_id", "created_by_label", "created_at", "updated_at",
                               "updated_by_id", "updated_by_label", "based_on"}                                # solo datos: nunca HTML/CSS/JS ni el contenido visual
        assert "<" not in json.dumps(stored)
    assert len(_files(world)) == 4


@pytest.mark.parametrize("email", [HUB_ONLY, INTRUDER])
def test_duplicating_still_requires_commercial_access(world, email):
    for ref in CLOSED:
        assert _dup(world, email, ref, name="x").status_code in (401, 403)
    assert _files(world) == []


def test_the_catalog_offers_duplicate_on_the_four_institutional_rows_and_keeps_edit_and_delete_read_only_for_every_profile(world):
    for email in (A, OWNER_C, APP_OWNER):
        soup = _catalog(world, email)
        rows = soup.select("#cc-catalog-table tbody tr")
        assert [r["data-cc-ref"] for r in rows] == ALL4
        for r in rows:
            view, dup, edit, delete = r.select(".dw-row-actions > *")
            assert not dup.has_attr("disabled") and dup["data-cc-dup"] == r["data-cc-ref"]
            assert edit.has_attr("disabled") and "solo lectura" in edit["aria-label"]                          # la definición institucional es inmutable desde la UI, también para APP_OWNER / CONTENT_OWNER
            assert delete.has_attr("disabled") and "no se eliminan" in delete["aria-label"]
        seed = soup.select_one(f'#cp-catalog-table tr[data-presentation-id="{SEED_ID}"]')
        acts = seed.select(".dw-row-actions > *")
        assert acts[4].has_attr("disabled") and acts[5].has_attr("disabled")                                  # y la presentación institucional también


# ── 2. Coherencia entre acciones de la UI y endpoints ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [A, OWNER_C, APP_OWNER])
def test_every_enabled_action_succeeds_on_the_server_and_no_enabled_control_ends_in_403_or_422(world, email):
    mine = {ref: _mk(world, A, ref, name=f"De A {ref.split(':')[1]}") for ref in ALL4}
    soup = _catalog(world, email)
    visible_own = {r["data-cc-ref"] for r in soup.select('#cc-catalog-table tr[data-cc-ref^="own:"]')}
    if email == APP_OWNER:
        assert visible_own == set()                                                                             # APP_OWNER no obtiene acceso editorial sobre lo ajeno
    else:
        assert visible_own == {f"own:{cid}" for cid in mine.values()}                                          # responsable o CONTENT_OWNER
    for tr in soup.select("#cc-catalog-table tbody tr"):
        ref = tr["data-cc-ref"]
        view, dup, edit, delete = tr.select(".dw-row-actions > *")
        if not view.has_attr("disabled"):
            assert world.client(email).get(f"/comercial/componentes/vista?ref={ref}").status_code == 200
        if not dup.has_attr("disabled"):
            r = _dup(world, email, ref, name="Duplicada desde la UI")
            assert r.status_code == 200, (email, ref, r.status_code, r.text)
        if not edit.has_attr("disabled"):
            params = json.loads(edit["data-cc-params"])
            body = {"name": "Renombrada desde la UI", "description": "ok"}
            if params is not None:                                                                              # exactamente lo que arma el diálogo: un cerrado NUNCA manda parámetros
                body["params"] = params
            r = _edit(world, email, edit["data-cc-edit"], int(edit["data-cc-rev"]), **body)
            assert r.status_code == 200, (email, ref, r.status_code, r.text)
        if not delete.has_attr("disabled"):
            assert _del(world, email, delete["data-cc-delete"]).status_code == 200
    if email != APP_OWNER:
        assert all(not _stored_exists(world, cid) for cid in mine.values())                                    # todas las acciones habilitadas corrieron hasta el final


def _stored_exists(world, cid):
    return (world.directory / "components" / f"{cid}.json").exists()


def test_the_endpoints_have_no_path_to_edit_or_delete_an_institutional_component(world):
    for ref in ALL4:
        for email in (A, OWNER_C, APP_OWNER):
            assert _edit(world, email, ref, 1, name="x").status_code in (404, 422)
            assert _del(world, email, ref).status_code == 404
    assert _files(world) == []


# ── 3 y 4. Edición de nombre/descripción de una copia cerrada y rechazo de parámetros ────────────────────────────────────────────

@pytest.mark.parametrize("ref", CLOSED)
def test_a_closed_copy_edits_only_name_and_description_and_keeps_params_null(world, ref):
    cid = _mk(world, A, ref, name="Flujo para Acme")
    r = _edit(world, A, cid, 1, name="Flujo renombrado", description="para la cuenta Acme")
    assert r.status_code == 200 and r.json()["revision"] == 2
    stored = _stored(world, cid)
    assert stored["name"] == "Flujo renombrado" and stored["description"] == "para la cuenta Acme" and stored["params"] is None and stored["revision"] == 2
    assert stored["owner_id"] == A and stored["based_on"]["ref"] == ref                                         # identidad, responsable y procedencia intactos
    soup = _catalog(world, A)
    row = soup.select_one(f'tr[data-cc-ref="own:{cid}"]')
    assert "Flujo renombrado" in row.get_text() and "para la cuenta Acme" in row.get_text()
    assert _edit(world, A, cid, 1, name="viejo").status_code == 409                                              # revisión optimista intacta
    assert _edit(world, A, cid, 2, name="x" * 121).status_code == 422 and _stored(world, cid)["name"] == "Flujo renombrado"


@pytest.mark.parametrize("junk", [{}, {"layers": []}, [], {"sides": ["x"], "connectors": True, "layers": [{"label": "x"}]}, "texto", 7, True, {"html": "<b>x</b>"}])
@pytest.mark.parametrize("ref", CLOSED)
def test_parameters_sent_to_a_closed_copy_are_rejected_without_changing_anything(world, ref, junk):
    cid = _mk(world, A, ref)
    before = (world.directory / "components" / f"{cid}.json").read_bytes()
    r = _edit(world, A, cid, 1, name="otro", params=junk)
    assert r.status_code == 422 and "<b>" not in r.text
    assert (world.directory / "components" / f"{cid}.json").read_bytes() == before                              # atómico: ni el nombre se aplicó


def test_an_explicit_null_for_params_is_the_same_as_not_sending_them_and_unknown_keys_still_fail(world):
    cid = _mk(world, A, FLOW_REF)
    assert _edit(world, A, cid, 1, name="solo nombre", params=None).status_code == 200 and _stored(world, cid)["params"] is None
    assert _edit(world, A, cid, 2, name="x", extra=1).status_code == 422
    assert _edit(world, A, cid, 2).status_code == 422                                                           # nada para guardar


def test_a_component_with_a_contract_keeps_its_texts_and_tones_editing_unchanged(world):
    cid = _mk(world, A, BDC_REF)
    params = _tweak(_bdc_params(), lambda p: p["layers"][0].update(label="OTRO RÓTULO"))
    assert _edit(world, A, cid, 1, params=params).status_code == 200 and _stored(world, cid)["params"]["layers"][0]["label"] == "OTRO RÓTULO"
    assert _edit(world, A, cid, 2, params=None, name="solo nombre").status_code == 200
    assert _edit(world, A, cid, 3, params=_tweak(params, lambda p: p["layers"].pop())).status_code == 422       # la estructura sigue sin editarse


# ── Validadores del snapshot y lectura del componente ──────────────────────────────────────────────────────────────────────────

def test_a_closed_snapshot_stores_null_params_and_never_accepts_parameters():
    cid = str(uuid.uuid4())
    frm = {"id": cid, "revision": 1, "name": "Copia"}
    ok = cp.validate_blocks({"s12.bleed": {"component": MARQUEE, "variant": f"own:{cid}@1", "params": None, "from": frm}})
    assert ok == {"s12.bleed": {"component": MARQUEE, "variant": f"own:{cid}@1", "params": None, "from": frm}}
    for params in ({}, {"layers": []}, [], "x", 0):
        with pytest.raises(cp.PresentationValidationError):
            cp.validate_blocks({"s12.bleed": {"component": MARQUEE, "variant": f"own:{cid}@1", "params": params, "from": frm}})
    with pytest.raises(cp.PresentationValidationError):                                                         # con contrato, el snapshot SIGUE exigiendo parámetros válidos
        cp.validate_blocks({"s2.body": {"component": "layer-stack", "variant": f"own:{cid}@1", "params": None, "from": frm}})
    with pytest.raises(cp.PresentationValidationError):                                                         # y un componente que no existe sigue sin ser válido
        cp.validate_blocks({"s2.body": {"component": "html", "variant": f"own:{cid}@1", "params": None, "from": frm}})


def test_a_stored_closed_component_with_parameters_is_unreadable_and_never_breaks_the_listing(world):
    cid = _mk(world, A, FLOW_REF)
    good = _mk(world, A, MARQUEE_REF, name="Sana")
    path = world.directory / "components" / f"{cid}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["params"] = {"layers": []}
    path.write_text(json.dumps(raw), encoding="utf-8")
    refs = [r["data-cc-ref"] for r in _catalog(world, A).select("#cc-catalog-table tbody tr")]
    assert f"own:{cid}" not in refs and f"own:{good}" in refs
    assert _edit(world, A, cid, 1, name="x").status_code == 404


# ── 5. Inserción de Flow y Marquee duplicados ───────────────────────────────────────────────────────────────────────────────

def test_duplicated_flow_and_marquee_are_inserted_as_null_param_snapshots_and_render_the_institutional_content(world):
    fid, mid = _mk(world, A, FLOW_REF, name="Mi flujo"), _mk(world, A, MARQUEE_REF, name="Mis logos")
    ref_doc = _doc(world, _copy(world, A))
    base_boxes, base_items = len(ref_doc.find(id="s5").select(".flow-box")), len(ref_doc.find(id="s12").select(".logo-marquee__item"))
    assert base_boxes > 0 and base_items > 0
    pid = _copy(world, A)
    r = _blocks(world, pid, {"s5.body": _use(f"own:{fid}@1", FLOW), "s12.bleed": _use(f"own:{mid}@1", MARQUEE), "s6.body": _use(f"own:{fid}@1", FLOW)}, 1)
    assert r.status_code == 200, r.text
    blocks = _pstored(world, pid)["blocks"]
    assert blocks["s5.body"] == {"component": FLOW, "variant": f"own:{fid}@1", "params": None, "from": {"id": fid, "revision": 1, "name": "Mi flujo"}}
    assert blocks["s12.bleed"] == {"component": MARQUEE, "variant": f"own:{mid}@1", "params": None, "from": {"id": mid, "revision": 1, "name": "Mis logos"}}
    assert "<" not in json.dumps(blocks)
    soup = _doc(world, pid)
    s5 = soup.find(id="s5").select_one("[data-cp-region] > [data-cp-block]")
    assert s5["data-cp-block"] == f"own:{fid}@1" and len(soup.find(id="s5").select(".flow-box")) == base_boxes          # el mismo contenido y renderer institucional
    s12 = soup.find(id="s12").select_one("[data-cp-region] > [data-cp-block]")
    assert s12["data-cp-block"] == f"own:{mid}@1" and len(soup.find(id="s12").select(".logo-marquee__item")) == base_items
    assert soup.find(id="s6").select_one("[data-cp-block]")["data-cp-block"] == f"own:{fid}@1"                          # y se agrega en una región vacía


def test_the_browser_cannot_supply_parameters_for_a_closed_copy_and_the_region_type_is_still_enforced(world):
    fid, mid = _mk(world, A, FLOW_REF), _mk(world, A, MARQUEE_REF)
    pid = _copy(world, A)
    for extra in ({"params": None}, {"params": {"layers": []}}, {"html": "<b>x</b>"}):
        assert _blocks(world, pid, {"s6.body": {**_use(f"own:{fid}@1", FLOW), **extra}}, 1).status_code == 422
    assert _blocks(world, pid, {"s12.bleed": _use(f"own:{fid}@1", FLOW)}, 1).status_code == 422                   # un flujo (body) no calza en una región bleed
    assert _blocks(world, pid, {"s6.body": _use(f"own:{mid}@1", MARQUEE)}, 1).status_code == 422                  # ni un marquee (bleed) en una región body
    assert _blocks(world, pid, {"s6.body": _use(f"own:{fid}@9", FLOW)}, 1).status_code == 422                     # revisión inexistente
    assert _pstored(world, pid).get("blocks") in (None, {})


def test_a_closed_copy_is_private_and_only_its_owner_or_the_content_owner_get_it(world):
    cid = _mk(world, A, FLOW_REF)
    for email in (B, APP_OWNER):
        assert f"own:{cid}" not in _catalog(world, email).text
        assert world.client(email).get(f"/comercial/componentes/vista?ref=own:{cid}").status_code == 404
        before = (world.directory / "components" / f"{cid}.json").read_bytes()
        assert _edit(world, email, cid, 1, name="ajeno").status_code in (403, 404) and _del(world, email, cid).status_code in (403, 404)
        assert (world.directory / "components" / f"{cid}.json").read_bytes() == before
    pid = _copy(world, B)
    assert _blocks(world, pid, {"s6.body": _use(f"own:{cid}@1", FLOW)}, 1, email=B).status_code == 422             # tampoco lo puede insertar
    assert world.client(OWNER_C).get(f"/comercial/componentes/vista?ref=own:{cid}").status_code == 200
    assert _edit(world, OWNER_C, cid, 1, name="gobierno editorial").status_code == 200                             # CONTENT_OWNER conserva su atribución


def test_the_preview_of_a_closed_copy_is_the_real_institutional_renderer(world):
    cid = _mk(world, A, MARQUEE_REF)
    inst = _catalog_preview(world, MARQUEE_REF)
    own = _catalog_preview(world, f"own:{cid}")
    assert own.select_one(".logo-marquee") and len(own.select(".logo-marquee__item")) == len(inst.select(".logo-marquee__item")) > 0


def _catalog_preview(world, ref):
    from bs4 import BeautifulSoup
    r = world.client(A).get(f"/comercial/componentes/vista?ref={ref}")
    assert r.status_code == 200
    return BeautifulSoup(r.text, "html.parser")


# ── 6. Las presentaciones sobreviven a la eliminación de sus componentes propios ───────────────────────────────────────────────────

def test_deleting_the_closed_copies_never_breaks_the_presentations_that_used_them_and_render_never_reads_the_component(world, monkeypatch):
    fid, mid = _mk(world, A, FLOW_REF, name="Flujo efímero"), _mk(world, A, MARQUEE_REF, name="Logos efímeros")
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s5.body": _use(f"own:{fid}@1", FLOW), "s12.bleed": _use(f"own:{mid}@1", MARQUEE)}, 1).status_code == 200
    before = _doc(world, pid)
    assert _del(world, A, fid).status_code == 200 and _del(world, A, mid).status_code == 200
    assert _files(world) == []

    def boom(*_a, **_k):
        raise AssertionError("el render de lectura NO debe leer el JSON del componente propio")
    original = cp.get_component
    monkeypatch.setattr(cp, "get_component", boom)
    after = _doc(world, pid)                                                                                       # lectura: solo el snapshot de la presentación
    for sid, sel in (("s5", ".flow-box"), ("s12", ".logo-marquee__item")):
        assert len(after.find(id=sid).select(sel)) == len(before.find(id=sid).select(sel)) > 0
    assert after.find(id="s5").select_one("[data-cp-block]")["data-cp-block"] == f"own:{fid}@1"
    monkeypatch.setattr(cp, "get_component", original)
    edit = _doc(world, pid, edit=True)                                                                             # en edición se sigue ofreciendo para conservarlo o reemplazarlo
    for cid, comp in ((fid, FLOW), (mid, MARQUEE)):
        tag = edit.select_one(f'#cp-addon-dialog input[value="{comp}:own:{cid}@1"]').find_parent("label").select_one(".cp-addon-tag")
        assert tag.text == "en uso · rev. 1"
    assert _save(world, A, pid, {"s3_title": "sigue editable"}, 2).status_code == 200
    assert _blocks(world, pid, {"s5.body": _use(f"own:{fid}@1", FLOW), "s12.bleed": _use(f"own:{mid}@1", MARQUEE)}, 3).status_code == 200      # volver a elegirlo conserva el snapshot
    copy_id = _copy(world, B, source=pid, title="Copia de B")                                                    # la copia hereda el uso sin acceso al componente
    assert _pstored(world, copy_id)["blocks"] == _pstored(world, pid)["blocks"]
    assert _doc(world, copy_id, email=B).find(id="s12").select_one("[data-cp-block]")["data-cp-block"] == f"own:{mid}@1"


# ── 7. Compatibilidad con snapshots anteriores y `removed_slides` ───────────────────────────────────────────────────────────────

def test_previous_snapshots_institutional_overrides_and_removed_slides_keep_working(world):
    cid = _mk(world, A, BDC_REF, name="Con parámetros")
    fid = _mk(world, A, FLOW_REF, name="Cerrado")
    pid = _copy(world, A)
    legacy = {"s3.body": {"component": "layer-stack", "variant": "knowledge-core-stack"}, "s5.body": None}                 # desvíos institucionales (formato anterior)
    r = world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "removed_slides": ["s7"], "blocks": legacy}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    r = world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 2, "removed_slides": ["s7", "s9"],
                             "blocks": {**legacy, "s2.body": _use(f"own:{cid}@1"), "s6.body": _use(f"own:{fid}@1", FLOW)}}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    stored = _pstored(world, pid)
    assert stored["blocks"]["s3.body"] == {"component": "layer-stack", "variant": "knowledge-core-stack"} and stored["blocks"]["s5.body"] is None
    assert stored["blocks"]["s2.body"]["params"] == _bdc_params() and stored["blocks"]["s2.body"]["from"]["id"] == cid         # el snapshot con parámetros conserva su forma exacta
    assert stored["blocks"]["s6.body"]["params"] is None and stored["removed_slides"] == ["s7", "s9"]
    soup = _doc(world, pid)
    assert [s["id"] for s in soup.select("section[id^=s]")] == ["s1", "s2", "s3", "s4", "s5", "s6", "s8", "s10", "s11", "s12"]      # numeración y slides eliminadas intactas
    assert soup.find(id="s2").select_one("[data-cp-block]")["data-cp-block"] == f"own:{cid}@1" and soup.find(id="s6").select_one("[data-cp-block]")["data-cp-block"] == f"own:{fid}@1"
    assert _del(world, A, cid).status_code == 200 and _del(world, A, fid).status_code == 200
    assert _doc(world, pid).find(id="s6").select_one("[data-cp-block]")                                              # y sobreviven al borrado de los componentes
    assert _save(world, A, pid, {"s3_title": "editable"}, 3).status_code == 200


def test_a_presentation_stored_before_this_change_loads_and_saves_without_migration(world):
    pid = _copy(world, A)
    path = world.directory / f"{pid}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["blocks"] = {"s3.body": {"component": "layer-stack", "variant": "knowledge-core-stack"}, "s12.bleed": None}
    raw["removed_slides"] = ["s7"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    soup = _doc(world, pid)
    assert "s7" not in [s["id"] for s in soup.select("section[id^=s]")] and not soup.find(id="s12").select(".logo-marquee")
    assert _save(world, A, pid, {"s3_title": "sigue"}, raw["revision"]).status_code == 200
    assert _pstored(world, pid)["blocks"] == raw["blocks"]


# ── Interfaz del diálogo y límites del cambio ──────────────────────────────────────────────────────────────────────────────────

def test_the_edit_dialog_and_script_handle_a_component_without_parameters():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    js = (root / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
    html = (root / "webapp" / "templates" / "partials" / "commercial_component_dialogs.html").read_text(encoding="utf-8")
    assert "if (!params) return;" in js and "if (ccEditing.params) payload.params" in js                            # un cerrado no arma el formulario de parámetros ni los manda
    assert "data-cc-edit-msg-fixed hidden" in html and "solo podés cambiar su nombre y su descripción" in html
    assert "podés editar sus textos y tonos sin afectarlo" not in html                                              # el mensaje de duplicar ya no promete edición interna a lo cerrado


def test_the_change_adds_no_environment_variables_roles_or_allowlists_and_the_limitation_is_documented():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    src = (root / "webapp" / "services" / "commercial_presentations.py").read_text(encoding="utf-8")
    assert "os.environ" not in src and "getenv" not in src
    docs = (root / "docs" / "COMMERCIAL_PRESENTATIONS.md").read_text(encoding="utf-8")
    assert "no congela" in docs and "copia cerrada" in docs and "params: null" in docs
