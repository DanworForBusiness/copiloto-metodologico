"""Presentaciones V1 — segunda colección «Componentes reutilizables»: ver un institucional → duplicarlo como propio → editar textos y tonos → usarlo en una presentación con SNAPSHOT independiente.

Tres cosas distintas: la DEFINICIÓN visual gobernada (renderer + contrato de parámetros, en código), el COMPONENTE catalogado (identidad, parámetros, responsable, procedencia, revisión) y el USO en una presentación
(snapshot de los parámetros dentro de `blocks`). Editar o eliminar el componente NO altera lo ya insertado. Los propios son privados para su responsable y `CONTENT_OWNER` (filtro en el servidor).
"""
from __future__ import annotations

import copy
import json
import re

import pytest
from bs4 import BeautifulSoup

import config
from core import remote_store
from tests.webapp.test_commercial_presentations_access import (
    A, APP_OWNER, B, HUB_ONLY, INTRUDER, ORIGIN, OWNER_C, SEED_ID, _copy, _save, world,
)
from tests.webapp.test_commercial_presentations_persistence import FakeSpaces
from webapp.services import auth_service
from webapp.services import commercial_presentations as cp

BDC_REF = "layer-stack:bdc-architecture"
KC_REF = "layer-stack:knowledge-core-stack"
FLOW_REF = "ecosystem-flow:open-ecosystem-flow"
MARQUEE_REF = "logo-marquee:partner-logo-marquee"


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _bdc_params():
    return {k: copy.deepcopy(v) for k, v in cp._addons()["bdc-architecture"].items() if k != "label"}


def _dup(world, email, ref=BDC_REF, name="Mi componente", description="", headers=ORIGIN):
    return world.client(email).post("/comercial/componentes/duplicar", json={"ref": ref, "name": name, "description": description}, headers=headers)


def _mk(world, email=A, ref=BDC_REF, name="Mi componente"):
    r = _dup(world, email, ref, name)
    assert r.status_code == 200, r.text
    return r.json()["component_id"]


def _edit(world, email, cid, revision, **body):
    return world.client(email).post(f"/comercial/componentes/{cid}", json={"revision": revision, **body}, headers=ORIGIN)


def _del(world, email, cid, headers=ORIGIN):
    return world.client(email).post(f"/comercial/componentes/{cid}/eliminar", json={}, headers=headers)


def _stored(world, cid):
    return json.loads((world.directory / "components" / f"{cid}.json").read_text(encoding="utf-8"))


def _pstored(world, pid):
    return json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))


def _tweak(params, fn):
    p = copy.deepcopy(params)
    fn(p)
    return p


def _blocks(world, pid, blocks, revision, email=A):
    return world.client(email).post(f"/comercial/presentaciones/{pid}", json={"revision": revision, "blocks": blocks}, headers=ORIGIN)


def _doc(world, pid, email=A, edit=False):
    r = world.client(email).get(f"/presentaciones/{pid}?embed=1" + ("&edit=1" if edit else ""))
    assert r.status_code == 200
    return _soup(r.text)


def _use(variant, component="layer-stack"):
    return {"component": component, "variant": variant}


def _catalog(world, email=A):
    r = world.client(email).get("/comercial/presentaciones")
    assert r.status_code == 200
    return _soup(r.text)


# ── Contrato de parámetros (el mismo para YAML institucional, componentes propios y snapshots) ───────────────────────────────────

def test_the_params_contract_accepts_the_institutional_variants_and_normalizes():
    for variant in cp.COMPONENTS["layer-stack"]["variants"]:
        params = {k: v for k, v in cp._addons()[variant].items() if k != "label"}
        assert cp.validate_layer_stack_params(params) == params


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=1), lambda p: p.pop("connectors"), lambda p: p.update(connectors="si"), lambda p: p.update(sides=["uno"]), lambda p: p.update(sides="x"),
    lambda p: p["layers"].clear(), lambda p: p["layers"].extend([copy.deepcopy(p["layers"][0]) for _ in range(3)]),
    lambda p: p["layers"][0].update(tone="rainbow"), lambda p: p["layers"][0].update(reveal=4), lambda p: p["layers"][0].update(reveal=61), lambda p: p["layers"][0].update(reveal=True),
    lambda p: p["layers"][0].update(html="<b>"), lambda p: p["layers"][0].pop("label"), lambda p: p["layers"][0].update(label="   "), lambda p: p["layers"][0].update(label="x" * 121),
    lambda p: p["layers"][0].update(label="uno\ndos"), lambda p: p["layers"][0].update(label="a‮b"), lambda p: p["layers"][0].update(sub=7),
    lambda p: p["layers"][0]["boxes"].clear(), lambda p: p["layers"][0]["boxes"].extend([{"text": "x", "tone": "gold"}] * 6),
    lambda p: p["layers"][0]["boxes"][0].update(tone="red"), lambda p: p["layers"][0]["boxes"][0].update(style="color:red"), lambda p: p["layers"][0]["boxes"][0].pop("text"),
    lambda p: p["layers"][0]["boxes"][0].update(note=""),
])
def test_the_params_contract_rejects_malformed_or_out_of_range_parameters(mutate):
    with pytest.raises(cp.PresentationValidationError) as err:
        cp.validate_layer_stack_params(_tweak(_bdc_params(), mutate))
    assert "<" not in str(err.value)


def test_only_texts_and_tones_are_editable_so_the_structure_signature_ignores_them():
    base = _bdc_params()
    changed = _tweak(base, lambda p: (p["layers"][0].update(label="Otro", tone="neutral"), p["layers"][0]["boxes"][0].update(text="Nuevo", tone="neutral")))
    assert cp._stack_shape(changed) == cp._stack_shape(base)
    for mutate in (lambda p: p["layers"][0]["boxes"].pop(), lambda p: p["layers"][0].update(reveal=20), lambda p: p.update(connectors=True), lambda p: p.update(sides=[]),
                   lambda p: p["layers"][0].pop("sub"), lambda p: p["layers"][3]["boxes"][0].pop("note")):
        assert cp._stack_shape(_tweak(base, mutate)) != cp._stack_shape(base)


# ── Colección: institucionales + propios, con visibilidad en el servidor ─────────────────────────────────────────────────────────────

def test_the_catalog_has_two_collections_with_the_same_hub_patterns_and_the_institutional_components(world):
    soup = _catalog(world)
    sections = soup.select("details.dashboard-section")
    assert [s["data-section-id"] for s in sections] == ["presentaciones", "componentes"] and sections[0].has_attr("open") and not sections[1].has_attr("open")      # «Componentes reutilizables» entra colapsado
    comp = sections[1]
    assert [c["class"][0] for c in comp.select("summary.dashboard-section__head > *")] == ["dashboard-section__caret", "dashboard-section__title", "dashboard-section__count"]
    count = comp.select_one("[data-cp-count]")
    assert count.text.strip() == "4 componentes" and count["data-cp-singular"] == "componente" and count["data-cp-plural"] == "componentes"
    assert comp.select_one(".matrix-scroll > table.dashboard-table#cc-catalog-table") and comp.select_one(".dashboard-empty[data-cp-empty][hidden]")
    rows = comp.select("#cc-catalog-table tbody tr")
    assert [r["data-cc-ref"] for r in rows] == [BDC_REF, KC_REF, FLOW_REF, MARQUEE_REF]
    for r in rows:
        assert not r.select(".status-badge") and r.has_attr("data-cp-search")                              # sin badge «Institucional»: el tratamiento diferencial lo dan las acciones
        buttons = r.select(".dw-row-actions > *")
        assert len(buttons) == 4 and [b.name for b in buttons] == ["button"] * 4                         # Ver · Duplicar · Editar · Eliminar: siempre las cuatro
        assert not buttons[0].has_attr("disabled") and buttons[2].has_attr("disabled") and buttons[3].has_attr("disabled")       # institucional: se ve y se duplica; nunca se edita ni se elimina
        assert "solo lectura" in buttons[2]["aria-label"] and "no se eliminan" in buttons[3]["aria-label"]
    dup = {r["data-cc-ref"]: r.select(".dw-row-actions > *")[1] for r in rows}
    assert not dup[BDC_REF].has_attr("disabled") and not dup[KC_REF].has_attr("disabled")
    assert dup[FLOW_REF].has_attr("disabled") and "sin parámetros" in dup[FLOW_REF]["aria-label"] or "no tiene parámetros editables" in dup[FLOW_REF]["aria-label"]
    assert dup[MARQUEE_REF].has_attr("disabled")                                                          # sin contrato de parámetros: se ve y se usa, pero no se duplica ni se edita


def test_a_duplicated_component_shows_up_in_the_collection_with_identity_ownership_and_origin(world):
    cid = _mk(world, A, name="Arquitectura para Acme")
    soup = _catalog(world)
    count = soup.select("details.dashboard-section")[1].select_one("[data-cp-count]")
    assert count.text.strip() == "5 componentes"
    row = soup.select_one(f'#cc-catalog-table tr[data-cc-ref="own:{cid}"]')
    assert row and row.select_one("strong").text == "Arquitectura para Acme" and row.select_one(".status-badge--tentativo").text.strip() == "Propio"
    cells = [td.get_text(" ", strip=True) for td in row.select("td")]
    assert len(cells) == 5 and "Basado en «Arquitectura SAP BDC»" in cells[0] and "Cuerpo de la slide" in cells[1] and cells[2] and re.fullmatch(r"\d\d-\d\d-\d{4}", cells[3])
    buttons = row.select(".dw-row-actions > *")
    assert all(not b.has_attr("disabled") for b in buttons) and buttons[2]["data-cc-edit"] == cid and buttons[3]["data-cc-delete"] == cid
    assert json.loads(buttons[2]["data-cc-params"]) == _bdc_params() and buttons[2]["data-cc-rev"] == "1"
    assert row["data-cp-search"].lower().startswith("arquitectura para acme")


def test_own_components_are_private_to_their_owner_and_to_the_content_owner_filtered_on_the_server(world):
    cid = _mk(world, A)
    assert f'data-cc-ref="own:{cid}"' in world.client(A).get("/comercial/presentaciones").text
    assert f'data-cc-ref="own:{cid}"' in world.client(OWNER_C).get("/comercial/presentaciones").text      # CONTENT_OWNER autorizado en Comercial
    for other in (B, APP_OWNER):                                                                         # otro usuario Comercial / el administrador: no lo ven
        r = world.client(other).get("/comercial/presentaciones")
        assert r.status_code == 200 and f"own:{cid}" not in r.text and "Mi componente" not in r.text
    assert world.client(HUB_ONLY).get("/comercial/presentaciones").status_code == 403
    for other in (B, APP_OWNER):                                                                         # y tampoco por las rutas: un componente ajeno no se distingue de uno inexistente
        assert world.client(other).get(f"/comercial/componentes/vista?ref=own:{cid}").status_code == 404
        assert _edit(world, other, cid, 1, name="x").status_code == 404 and _del(world, other, cid).status_code == 404
        assert _dup(world, other, f"own:{cid}").status_code == 404
    assert (world.directory / "components" / f"{cid}.json").exists()


# ── Duplicar ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_duplicating_stores_an_independent_component_with_identity_owner_origin_revision_and_only_data(world):
    r = _dup(world, A, BDC_REF, "  Mi arquitectura  ", "para Acme")
    assert r.status_code == 200 and r.json()["revision"] == 1
    cid = r.json()["component_id"]
    stored = _stored(world, cid)
    assert set(stored) == {"schema_version", "component_id", "component", "name", "description", "params", "revision", "owner_id", "created_by_id", "created_by_label", "created_at", "updated_at",
                           "updated_by_id", "updated_by_label", "based_on"}
    assert stored["name"] == "Mi arquitectura" and stored["description"] == "para Acme" and stored["component"] == "layer-stack" and stored["owner_id"] == A and stored["created_by_id"] == A
    assert stored["params"] == _bdc_params() and "label" not in stored["params"]
    assert stored["based_on"] == {"ref": BDC_REF, "name": "Arquitectura SAP BDC", "revision": None, "origin": "institutional"}
    assert "<" not in json.dumps(stored["params"]) and not [k for k in stored if "html" in k.lower() or "css" in k.lower() or "js" in k.lower()]
    stored["params"]["layers"][0]["label"] = "tocado"
    assert cp._addons()["bdc-architecture"]["layers"][0]["label"] == "AI AGENTS"                         # independiente: tocar la copia no toca el institucional


def test_duplicating_an_own_component_keeps_the_provenance_chain(world):
    first = _mk(world, A, name="Primero")
    second = _dup(world, A, f"own:{first}", "Segundo")
    assert second.status_code == 200
    based = _stored(world, second.json()["component_id"])["based_on"]
    assert based == {"ref": f"layer-stack:own:{first}@1", "name": "Primero", "revision": 1, "origin": "own"}


@pytest.mark.parametrize("ref, status", [(FLOW_REF, 422), (MARQUEE_REF, 422), ("layer-stack:inventada", 404), ("html:x", 404), ("own:00000000-0000-4000-8000-000000000000", 404), ("", 404), ("../etc", 404)])
def test_only_components_with_a_parameter_contract_can_be_duplicated_and_the_ref_must_exist(world, ref, status):
    before = world.files()
    assert _dup(world, A, ref).status_code == status
    assert not (world.directory / "components").exists() or not list((world.directory / "components").glob("*.json")) and world.files() == before


@pytest.mark.parametrize("name, description", [("", ""), ("   ", ""), ("x" * 121, ""), ("uno\ndos", ""), ("ok", "x" * 201), ("ok", "a‮b")])
def test_duplicate_validates_name_and_description_without_reflecting_them(world, name, description):
    r = _dup(world, A, BDC_REF, name, description)
    assert r.status_code == 422 and "x" * 20 not in r.text and not (world.directory / "components").exists()


def test_duplicate_follows_the_enforcement_order_session_commercial_origin_and_payload(world):
    before = world.files()
    assert world.client(None).post("/comercial/componentes/duplicar", json={"ref": BDC_REF, "name": "x"}, headers=ORIGIN).status_code == 401
    assert _dup(world, HUB_ONLY).status_code == 403 and _dup(world, INTRUDER).status_code == 403
    assert _dup(world, A, headers={"Origin": "https://evil.example"}).status_code == 403
    assert world.client(A).post("/comercial/componentes/duplicar", json={"name": "x"}, headers=ORIGIN).status_code == 400
    assert world.client(A).post("/comercial/componentes/duplicar", content="no es json", headers=ORIGIN).status_code == 400
    assert world.files() == before and not (world.directory / "components").exists()


def test_the_collection_has_quotas_per_user_and_global(world, monkeypatch):
    monkeypatch.setattr(cp, "MAX_COMPONENTS_PER_USER", 2)
    _mk(world, A, name="Uno")
    _mk(world, A, name="Dos")
    r = _dup(world, A, BDC_REF, "Tres")
    assert r.status_code == 409 and r.json()["state"] == "quota"
    assert _dup(world, B, BDC_REF, "De B").status_code == 200                                              # el tope es por usuario
    monkeypatch.setattr(cp, "MAX_COMPONENTS", 3)
    assert _dup(world, OWNER_C, BDC_REF, "De C").status_code == 409                                       # y global


# ── Editar (textos y tonos; misma estructura) ───────────────────────────────────────────────────────────────────────────────

def test_editing_changes_texts_and_tones_bumps_the_revision_and_keeps_identity_and_provenance(world):
    cid = _mk(world, A)
    params = _tweak(_bdc_params(), lambda p: (p["layers"][0].update(label="AGENTES", tone="neutral"), p["layers"][0]["boxes"][0].update(text="Joule propio", tone="neutral"), p.update(sides=["Izq", "Der"])))
    r = _edit(world, A, cid, 1, name="Renombrado", description="Nueva", params=params)
    assert r.status_code == 200 and r.json()["revision"] == 2 and r.json()["ref"] == f"layer-stack:own:{cid}@2"
    stored = _stored(world, cid)
    assert stored["revision"] == 2 and stored["name"] == "Renombrado" and stored["description"] == "Nueva" and stored["params"] == params
    assert stored["owner_id"] == A and stored["created_by_id"] == A and stored["updated_by_id"] == A and stored["based_on"]["ref"] == BDC_REF
    assert _edit(world, A, cid, 2, name="Solo nombre").json()["revision"] == 3 and _stored(world, cid)["params"] == params     # editar solo metadata no toca los parámetros


@pytest.mark.parametrize("mutate", [
    lambda p: p["layers"][0]["boxes"].pop(), lambda p: p["layers"].pop(), lambda p: p["layers"][0].update(reveal=20), lambda p: p.update(connectors=True), lambda p: p.update(sides=[]),
    lambda p: p["layers"][0].pop("sub"), lambda p: p["layers"][3]["boxes"][0].pop("note"), lambda p: p["layers"].reverse(),
])
def test_structure_edits_are_rejected_in_this_delivery_and_nothing_is_saved(world, mutate):
    cid = _mk(world, A)
    before = _stored(world, cid)
    r = _edit(world, A, cid, 1, params=_tweak(_bdc_params(), mutate))
    assert r.status_code == 422 and ("estructura" in r.text or "forma" in r.text or "tono" in r.text)
    assert _stored(world, cid) == before


@pytest.mark.parametrize("mutate", [lambda p: p["layers"][0].update(tone="rainbow"), lambda p: p["layers"][0]["boxes"][0].update(text=""), lambda p: p["layers"][0]["boxes"][0].update(text="y" * 121),
                                    lambda p: p["layers"][0]["boxes"][0].update(html="<script>alert(1)</script>")])
def test_invalid_parameters_are_rejected_without_reflecting_the_value(world, mutate):
    cid = _mk(world, A)
    before = _stored(world, cid)
    r = _edit(world, A, cid, 1, params=_tweak(_bdc_params(), mutate))
    assert r.status_code == 422 and "alert(1)" not in r.text and "yyyy" not in r.text and _stored(world, cid) == before


def test_editing_is_checked_against_the_revision_and_the_payload_shape(world):
    cid = _mk(world, A)
    assert _edit(world, A, cid, 1, name="uno").status_code == 200
    stale = _edit(world, A, cid, 1, name="viejo")
    assert stale.status_code == 409 and stale.json()["state"] == "stale" and stale.json()["current_revision"] == 2 and _stored(world, cid)["name"] == "uno"
    assert world.client(A).post(f"/comercial/componentes/{cid}", json={"name": "x"}, headers=ORIGIN).status_code == 400
    assert world.client(A).post(f"/comercial/componentes/{cid}", json={"revision": 2}, headers=ORIGIN).status_code == 422                     # nada para guardar
    assert world.client(A).post(f"/comercial/componentes/{cid}", json={"revision": 2, "owner_id": "x@y.z"}, headers=ORIGIN).status_code == 422     # el responsable no se edita
    assert _edit(world, A, cid, 2, name="").status_code == 422
    assert _stored(world, cid)["revision"] == 2 and _stored(world, cid)["owner_id"] == A
    assert _edit(world, A, cid, 2, name="uno").status_code == 200 and _stored(world, cid)["revision"] == 2                                   # sin cambios reales no se quema una revisión


def test_only_the_owner_or_the_content_owner_edits_and_enforcement_order_holds(world):
    cid = _mk(world, A)
    before = _stored(world, cid)
    assert world.client(None).post(f"/comercial/componentes/{cid}", json={"revision": 1, "name": "x"}, headers=ORIGIN).status_code == 401
    assert _edit(world, HUB_ONLY, cid, 1, name="x").status_code == 403 and _edit(world, B, cid, 1, name="x").status_code == 404 and _edit(world, APP_OWNER, cid, 1, name="x").status_code == 404
    assert world.client(A).post(f"/comercial/componentes/{cid}", json={"revision": 1, "name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _stored(world, cid) == before
    assert _edit(world, OWNER_C, cid, 1, name="Editado por CONTENT_OWNER").status_code == 200
    assert _stored(world, cid)["owner_id"] == A and _stored(world, cid)["updated_by_id"] == OWNER_C                                        # el responsable no cambia: quedó trazado quién editó


# ── Eliminar ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_deleting_removes_the_component_for_its_owner_or_the_content_owner_only(world):
    cid = _mk(world, A)
    assert _del(world, B, cid).status_code == 404 and _del(world, APP_OWNER, cid).status_code == 404 and _del(world, HUB_ONLY, cid).status_code == 403
    assert world.client(None).post(f"/comercial/componentes/{cid}/eliminar", json={}, headers=ORIGIN).status_code == 401
    assert _del(world, A, cid, headers={"Origin": "https://evil.example"}).status_code == 403 and (world.directory / "components" / f"{cid}.json").exists()
    assert _del(world, A, cid).status_code == 200 and not (world.directory / "components" / f"{cid}.json").exists()
    assert _del(world, A, cid).status_code == 404
    other = _mk(world, A)
    assert _del(world, OWNER_C, other).status_code == 200
    assert 'data-cc-ref="own:' not in world.client(A).get("/comercial/presentaciones").text


# ── Persistencia ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_component_files_live_in_the_persisted_directory_and_never_mix_with_presentations(world):
    cid = _mk(world, A)
    pid = _copy(world, A)
    assert (world.directory / "components" / f"{cid}.json").exists() and not (world.directory / f"{cid}.json").exists()
    assert [p.name for p in world.directory.glob("*.json")] == [f"{pid}.json"]                           # el listado de presentaciones no ve componentes
    assert f'data-presentation-id="{cid}"' not in world.client(A).get("/comercial/presentaciones").text


def test_remote_mode_pushes_the_component_and_a_failed_create_is_retried_once_then_reported_as_uncertain(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    cid = _mk(world, A)
    assert [p.name for p in spaces.pushed] == [f"{cid}.json"]
    spaces2 = FakeSpaces(monkeypatch, push_results=[False, True])
    assert _dup(world, A, BDC_REF, "Reintento").status_code == 200 and len(spaces2.pushed) == 2
    spaces3 = FakeSpaces(monkeypatch, push_results=[False, False])
    before = sorted(p.name for p in (world.directory / "components").glob("*.json"))
    r = _dup(world, A, BDC_REF, "Falla")
    assert r.status_code == 503 and r.json()["reason"] == "outcome_unknown" and "No pudimos confirmar" in r.json()["message"]
    assert sorted(p.name for p in (world.directory / "components").glob("*.json")) == before and spaces3.deleted          # sin huérfanos locales ni remotos


def test_a_failed_push_on_edit_restores_the_previous_version_and_reports_503(world, monkeypatch):
    cid = _mk(world, A)
    before = _stored(world, cid)
    FakeSpaces(monkeypatch, push_results=[False])
    r = _edit(world, A, cid, 1, name="No se guarda")
    assert r.status_code == 503 and r.json()["reason"] == "durable_push_failed" and _stored(world, cid) == before


def test_a_failed_remote_delete_restores_the_component_after_one_retry(world, monkeypatch):
    cid = _mk(world, A)
    FakeSpaces(monkeypatch)
    calls = []
    monkeypatch.setattr(remote_store, "delete_path", lambda p: calls.append(p) or False)
    r = _del(world, A, cid)
    assert r.status_code == 503 and len(calls) == 2 and (world.directory / "components" / f"{cid}.json").exists()


def test_read_only_and_unconfigured_modes_never_write_components(world, monkeypatch):
    cid = _mk(world, A)
    before = _stored(world, cid)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    assert _dup(world, A, BDC_REF, "x").status_code == 503 and _edit(world, A, cid, 1, name="x").status_code == 503 and _del(world, A, cid).status_code == 503
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    FakeSpaces(monkeypatch, enabled=False, push_results=[False, False, False, False])
    assert _edit(world, A, cid, 1, name="x").json()["reason"] == "config_incomplete" and _stored(world, cid) == before
    assert _del(world, A, cid).json()["reason"] == "config_incomplete" and (world.directory / "components" / f"{cid}.json").exists()


# ── Vista previa con el renderer REAL ───────────────────────────────────────────────────────────────────────────────────────

def test_the_preview_is_the_real_renderer_of_the_presentation_for_institutional_and_own_components(world):
    cid = _mk(world, A)
    assert _edit(world, A, cid, 1, params=_tweak(_bdc_params(), lambda p: p["layers"][0]["boxes"][0].update(text="Caja de prueba"))).status_code == 200
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@2")}, 1).status_code == 200
    in_doc = str(_doc(world, pid).find(id="s2").select_one("[data-cp-region] > [data-cp-block]"))
    r = world.client(A).get(f"/comercial/componentes/vista?ref=own:{cid}")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store"
    preview = _soup(r.text)
    assert preview.body["class"] == ["cp-preview"] and not preview.select("section, nav.rail, footer, [data-cp-region]")                    # solo el componente, sin el resto de la presentación
    assert str(preview.select_one("[data-cp-block]")) == in_doc                                                                              # el MISMO macro y los MISMOS parámetros: ninguna representación paralela
    seed_block = str(_doc(world, SEED_ID).find(id="s3").select_one("[data-cp-block]"))
    assert str(_soup(world.client(A).get(f"/comercial/componentes/vista?ref={BDC_REF}").text).select_one("[data-cp-block]")) == seed_block      # y el institucional, idéntico al de la presentación semilla
    assert _soup(world.client(A).get(f"/comercial/componentes/vista?ref={FLOW_REF}").text).select_one("[data-cp-block='open-ecosystem-flow']")


def test_the_preview_follows_the_commercial_and_visibility_contracts(world):
    cid = _mk(world, A)
    for ref in ("", "layer-stack:inventada", "own:00000000-0000-4000-8000-000000000000", "x"):
        assert world.client(A).get(f"/comercial/componentes/vista?ref={ref}").status_code == 404
    assert world.client(None).get(f"/comercial/componentes/vista?ref={BDC_REF}", follow_redirects=False).status_code == 303
    assert world.client(HUB_ONLY).get(f"/comercial/componentes/vista?ref={BDC_REF}").status_code == 403
    assert world.client(B).get(f"/comercial/componentes/vista?ref=own:{cid}").status_code == 404 and world.client(OWNER_C).get(f"/comercial/componentes/vista?ref=own:{cid}").status_code == 200


# ── Uso en una presentación: snapshot independiente ─────────────────────────────────────────────────────────────────────────

def test_the_add_on_dialog_offers_the_own_components_of_the_editor_with_their_real_preview_and_never_to_readers_or_others(world):
    cid = _mk(world, A, name="Mi arquitectura")
    pid = _copy(world, A)
    soup = _doc(world, pid, edit=True)
    ref = f"layer-stack:own:{cid}@1"
    tpl = soup.select_one(f'template[data-cp-component="{ref}"]')
    assert tpl and tpl.select_one(f'[data-cp-block="own:{cid}@1"]') and len(tpl.select(".layer-row")) == 4                                    # el componente real, con sus parámetros
    opt = soup.select_one(f'#cp-addon-dialog input[value="{ref}"]').find_parent("label")
    assert opt["data-cp-opt-type"] == "body" and "Mi arquitectura" in opt.text and opt.select_one(".cp-addon-tag").text == "propio"
    assert not _doc(world, pid).select(f'[data-cp-component="{ref}"]') and ref not in world.client(A).get(f"/presentaciones/{pid}?embed=1").text
    other = _copy(world, B)
    assert ref not in world.client(B).get(f"/presentaciones/{other}?embed=1&edit=1").text                                                  # el componente es privado de A: no aparece en la biblioteca de B
    assert ref in world.client(OWNER_C).get(f"/presentaciones/{pid}?embed=1&edit=1").text                                                  # CONTENT_OWNER edita la presentación de A y lo ve


def test_using_a_component_stores_a_server_made_snapshot_never_html_and_renders_it_in_the_region(world):
    cid = _mk(world, A, name="Mi arquitectura")
    params = _tweak(_bdc_params(), lambda p: (p["layers"][0].update(label="AGENTES PROPIOS"), p["layers"][0]["boxes"][0].update(text="Joule propio", tone="neutral")))
    assert _edit(world, A, cid, 1, params=params).status_code == 200
    pid = _copy(world, A)
    r = _blocks(world, pid, {"s2.body": _use(f"own:{cid}@2"), "s5.body": _use(f"own:{cid}@2")}, 1)
    assert r.status_code == 200, r.text
    stored = _pstored(world, pid)["blocks"]["s2.body"]
    assert stored == {"component": "layer-stack", "variant": f"own:{cid}@2", "params": params, "from": {"id": cid, "revision": 2, "name": "Mi arquitectura"}}
    assert "<" not in json.dumps(stored)
    soup = _doc(world, pid)
    block = soup.find(id="s2").select_one("[data-cp-region] > [data-cp-block]")
    assert block["data-cp-block"] == f"own:{cid}@2" and "AGENTES PROPIOS" in block.text and "Joule propio" in block.text and len(block.select(".layer-row")) == 4
    assert soup.find(id="s5").select_one("[data-cp-block]")["data-cp-block"] == f"own:{cid}@2"                                              # reemplaza el flujo de s5 (región body)


def test_the_browser_can_never_supply_the_snapshot_and_unavailable_or_incompatible_uses_are_rejected(world):
    cid = _mk(world, A)
    pid = _copy(world, A)
    before = world.files()
    forged = {"component": "layer-stack", "variant": f"own:{cid}@1", "params": _tweak(_bdc_params(), lambda p: p["layers"][0].update(label="inyectado")), "from": {"id": cid, "revision": 1, "name": "x"}}
    assert _blocks(world, pid, {"s2.body": forged}, 1).status_code == 422                                  # el cliente no aporta parámetros: los pone el servidor
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@7")}, 1).status_code == 422                    # la revisión elegida ya no es la vigente
    assert _blocks(world, pid, {"s2.body": _use("own:00000000-0000-4000-8000-000000000000@1")}, 1).status_code == 422
    assert _blocks(world, pid, {"s12.bleed": _use(f"own:{cid}@1")}, 1).status_code == 422                  # compatibilidad por TIPO de región: es body, no bleed
    assert _blocks(world, pid, {"s2.body": {"component": "ecosystem-flow", "variant": f"own:{cid}@1"}}, 1).status_code == 422
    assert _blocks(world, pid, {"s2.body": _use("own:no-es-un-uuid@1")}, 1).status_code == 422
    foreign = _mk(world, B, name="Ajeno")
    assert _blocks(world, pid, {"s2.body": _use(f"own:{foreign}@1")}, 1).status_code == 422                # un componente que el editor no puede ver no se puede usar
    assert world.files() == before


def test_editing_the_original_afterwards_never_alters_a_use_already_inserted(world):
    cid = _mk(world, A, name="Original")
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@1")}, 1).status_code == 200
    snapshot = _pstored(world, pid)["blocks"]["s2.body"]
    assert _edit(world, A, cid, 1, params=_tweak(_bdc_params(), lambda p: p["layers"][1].update(label="CAMBIADO DESPUÉS"))).status_code == 200
    assert _edit(world, A, cid, 2, name="Original v3").status_code == 200
    assert _pstored(world, pid)["blocks"]["s2.body"] == snapshot                                             # el archivo de la presentación no se tocó
    assert "CAMBIADO DESPUÉS" not in _doc(world, pid).get_text() and "KNOWLEDGE CORE" in _doc(world, pid).find(id="s2").get_text()
    # volver a guardar la presentación con ese mismo componente conserva SU snapshot (no re-lee el catálogo)
    assert world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 2, "fields": {"s3_title": "otro"}, "blocks": {"s2.body": _use(f"own:{cid}@1")}}, headers=ORIGIN).status_code == 200
    assert _pstored(world, pid)["blocks"]["s2.body"] == snapshot
    # el editor ofrece la versión vigente Y la que está en uso (con su rótulo), sin ambigüedad de referencias
    soup = _doc(world, pid, edit=True)
    refs = {t["data-cp-component"]: t for t in soup.select("template[data-cp-component]")}
    assert f"layer-stack:own:{cid}@3" in refs and f"layer-stack:own:{cid}@1" in refs
    assert "CAMBIADO DESPUÉS" in refs[f"layer-stack:own:{cid}@3"].get_text() and "CAMBIADO DESPUÉS" not in refs[f"layer-stack:own:{cid}@1"].get_text()
    tags = {o.find_parent("label").select_one(".cp-addon-tag").text for o in soup.select(f'#cp-addon-dialog input[value^="layer-stack:own:{cid}@"]')}
    assert tags == {"propio", "en uso · rev. 1"}
    # recién al ELEGIR de nuevo la vigente, el uso se actualiza
    assert world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 3, "blocks": {"s2.body": _use(f"own:{cid}@3")}}, headers=ORIGIN).status_code == 200
    assert "CAMBIADO DESPUÉS" in _doc(world, pid).find(id="s2").get_text() and _pstored(world, pid)["blocks"]["s2.body"]["from"]["revision"] == 3


def test_deleting_the_component_never_breaks_the_presentations_that_used_it(world):
    cid = _mk(world, A, name="Efímero")
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@1")}, 1).status_code == 200
    assert _del(world, A, cid).status_code == 200
    soup = _doc(world, pid)
    assert soup.find(id="s2").select_one("[data-cp-block]")["data-cp-block"] == f"own:{cid}@1" and len(soup.find(id="s2").select(".layer-row")) == 4          # sigue renderizando su snapshot
    edit = _doc(world, pid, edit=True)
    assert edit.select_one(f'#cp-addon-dialog input[value="layer-stack:own:{cid}@1"]').find_parent("label").select_one(".cp-addon-tag").text == "en uso · rev. 1"   # y se puede conservar o reemplazar
    assert _save(world, A, pid, {"s3_title": "sigue editable"}, 2).status_code == 200
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@1")}, 3).status_code == 200                      # volver a elegirlo conserva su snapshot aunque el original ya no exista
    assert _blocks(world, pid, {}, 4).status_code == 200 and not _doc(world, pid).find(id="s2").select("[data-cp-block]")


def test_a_copy_of_the_presentation_inherits_the_snapshot_and_needs_no_access_to_the_component(world):
    cid = _mk(world, A, name="Privado de A")
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@1")}, 1).status_code == 200
    copy_id = _copy(world, B, source=pid, title="Copia de B")
    assert _pstored(world, copy_id)["blocks"] == _pstored(world, pid)["blocks"]
    assert "Privado de A" not in world.client(B).get("/comercial/presentaciones").text                         # el componente sigue siendo privado de A
    soup = _doc(world, copy_id, email=B)
    assert soup.find(id="s2").select_one("[data-cp-block]")["data-cp-block"] == f"own:{cid}@1"              # pero el uso viaja en la copia, independiente
    assert _del(world, A, cid).status_code == 200 and _doc(world, copy_id, email=B).find(id="s2").select_one("[data-cp-block]")
    assert _blocks(world, copy_id, {"s2.body": None, "s3.body": None}, 1, email=B).status_code == 200


def test_composition_with_a_component_coexists_with_removed_slides_numbering_and_the_other_add_ons(world):
    cid = _mk(world, A)
    pid = _copy(world, A)
    r = world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "removed_slides": ["s4"], "blocks": {"s6.body": _use(f"own:{cid}@1"), "s3.body": None, "s4.body": _use(f"own:{cid}@1")}}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    soup = _doc(world, pid)
    assert [s["id"] for s in soup.select("section[id^=s]")] == ["s1", "s2", "s3", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12"]
    assert [x["data-cp-region"] for x in soup.select("[data-cp-region]")] == ["s5.body", "s6.body", "s12.bleed"]
    assert [k.get_text(strip=True).split(" · ")[0].replace(" ", "") for k in soup.select(".topic-kicker")] == [f"{i:02d}/10" for i in range(1, 11)]


def test_a_stored_snapshot_that_is_damaged_is_ignored_when_reading(world):
    cid = _mk(world, A)
    pid = _copy(world, A)
    assert _blocks(world, pid, {"s2.body": _use(f"own:{cid}@1"), "s8.body": _use(f"own:{cid}@1")}, 1).status_code == 200
    raw = json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))
    raw["blocks"]["s2.body"]["params"]["layers"][0]["tone"] = "rainbow"
    raw["blocks"]["s8.body"]["from"]["revision"] = 99
    (world.directory / f"{pid}.json").write_text(json.dumps(raw), encoding="utf-8")
    soup = _doc(world, pid)
    assert [x["data-cp-region"] for x in soup.select("[data-cp-region]")] == ["s3.body", "s4.body", "s5.body", "s12.bleed"]


# ── Estáticos ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_collection_introduces_no_environment_variables_roles_or_allowlists():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ("webapp/services/commercial_presentations.py", "webapp/routers/commercial_presentations.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "os.environ" not in source and "os.getenv" not in source, name
    service = (root / "webapp/services/commercial_presentations.py").read_text(encoding="utf-8")
    assert "def can_view_component_user" in service and "is_content_owner_user" in service
    js = (root / "webapp/static/commercial_presentations.js").read_text(encoding="utf-8")
    assert "data-cc-view" in js and "data-cc-edit" in js and "/comercial/componentes/duplicar" in js
