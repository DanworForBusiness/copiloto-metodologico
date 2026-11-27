"""D-LC-01 · LC-1 + LC-2 — retiro lógico gobernado de modelos y sus Assessments, con el MOTOR REAL y la publicación transaccional REAL (tenant simulado, todo bajo ``tmp_path``).

Escenarios exigidos por el owner: (1) retiro con y sin borrado de archivos; (2) republicación de OTRO modelo del cliente; (3) republicación del PROPIO modelo retirado
(reactivación trazable); (4) homónimos en otro cliente; (5) dependencias compartidas; (6) inventario activo vs evidencia histórica; (7) vista previa del padrón de una
activación futura con exclusión registrada; (8) preservación íntegra del marcador FINALIZED, sus respaldos y el Juicio histórico.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import publication_gate as gate
from tests.publication_fakes import CLIENT, SPACE, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import catalog_service as cs
from tools.catalog import catalog_retirement as gc
from tools.catalog import publication_ledger as ledger_mod
from webapp.services import assessment_service as assess
from webapp.services import catalog_governance as gov
from webapp.services import model_lifecycle as ml
from webapp.services import republication_stage as stage

AM_A, AM_B = "AM_Lista_Precios", "AM_Otro"
OWNER = "owner@danwor.com"


@pytest.fixture()
def world(ctx, monkeypatch, tmp_path):                                                                     # noqa: F811
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate.set_resolver(None)
    ctx.governed = tmp_path / "governed"
    assert ctx.publish(AM_A, stage=True)[0].ok and ctx.publish(AM_B, stage=True)[0].ok
    yield ctx
    gate.clear(ctx.catalog)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()


def pub(w, root=AM_A):
    res, _ = w.publish(root, stage=True)
    assert res.ok, (res.status, res.error_code, res.message)
    return res


def all_entries(w):
    """Como el Hub: desde la RAÍZ del Catálogo (el entry_id lleva el cliente)."""
    return [e for e in cs.load_catalog(w.catalog) if e.cliente == CLIENT]


def entry_of(w, am):
    return next(e for e in all_entries(w) if assess.resolve_entries([e])[e.entry_id].terminal_am == am)


def env_bytes(am, client=CLIENT):
    p = assess.governed_path(client, am)
    return p.read_bytes() if p is not None and p.is_file() else None


def flow_dir(w, am):
    return Path(entry_of(w, am).path).parent


def retire(w, am, *, purge=False, anchor=True):
    e = entry_of(w, am)
    folder = flow_dir(w, am)
    rec = gov.retire(e, OWNER, terminal_am=am if anchor else None)
    if purge:
        gov.purge_files(e)
    assess.reset_memory_cache()
    return e.entry_id, folder, rec


def summary(w):
    _s, data = ledger_mod.load(w.catalog, CLIENT)
    return data["active"]["assessment"]


# ═══════════════ (1)(2) retiro sin y con borrado; publicar OTRO modelo ═══════════════
def test_retire_without_purge_keeps_everything_and_the_stage_reports_it_as_retired(world):
    eid, folder, rec = retire(world, AM_B)
    assert rec["terminal_am"] == AM_B and rec["catalog_dir"] == f"{CLIENT}/{folder.name}"
    before = env_bytes(AM_B)
    world.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))                # cambio SOLO de la cadena de A
    res = pub(world, AM_A)
    assert res.applied["assessment"]["retired"] == [AM_B] and res.applied["assessment"]["removed"] == []
    assert AM_B not in res.applied["assessment"]["regenerated"] + res.applied["assessment"]["rebased"] + res.applied["assessment"]["left_as_is"]
    assert env_bytes(AM_B) == before                                                              # sobre intacto, byte a byte
    assert folder.is_dir()                                                                        # sin borrado: su carpeta se regenera como siempre (tile oculto)
    assert eid not in {e.entry_id for e in gov.visible_entries(all_entries(world))}
    assert summary(world)["retired"] == [AM_B]                                                    # trazable en la bitácora


def test_retire_with_purge_is_durable_publishing_another_model_does_not_resurrect_the_folder(world):
    eid, folder, rec = retire(world, AM_B, purge=True)
    assert not folder.exists() and gov.retirement(eid)["files_deleted"]["count"] > 0
    before = env_bytes(AM_B)
    pool_csn = world.pool / CLIENT / "analytic-models" / f"{AM_B}.json"
    assert pool_csn.is_file()
    world.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    res = pub(world, AM_A)
    assert not folder.exists()                                                                    # LC-2: no se regenera
    assert res.applied["assessment"]["retired"] == [AM_B]
    assert env_bytes(AM_B) == before and pool_csn.is_file()                                       # ni el sobre ni el pool se tocan
    assert gov.is_retired(eid)


def test_a_legacy_purged_record_without_anchor_is_resolved_by_its_exact_entry_id(world):
    eid, folder, _rec = retire(world, AM_B, purge=True, anchor=False)                             # como AM_TEST_VENTA_2 (registro anterior a D-LC-01)
    sup = gc.purged_retirements(world.catalog)[CLIENT.casefold()]
    assert sup.by_id == {eid: None} and sup.applies(CLIENT, folder.name, AM_B)
    assert not sup.applies(CLIENT, folder.name, AM_B, frozenset({AM_B}))                          # reactivación explícita
    assert not sup.applies(CLIENT, "Otra_Carpeta", AM_B)                                           # sin ancla, solo el entry_id exacto
    world.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    pub(world, AM_A)
    assert not folder.exists()


def test_a_record_anchored_to_another_model_does_not_suppress_a_homonym_folder(world):
    folder = flow_dir(world, AM_B)
    sup = gc.RetiredSuppression({gc._entry_id_for(CLIENT, folder.name): "AM_Distinto"}, frozenset({"AM_Distinto"}))
    assert not sup.applies(CLIENT, folder.name, AM_B)


# ═══════════════ (3) reactivación del propio modelo ═══════════════
def test_republishing_the_retired_model_itself_reactivates_it_with_a_trace(world):
    eid, folder, rec = retire(world, AM_B, purge=True)
    world.tenant.put(SPACE, "views", "3VF_OT", view("3VF_OT", ["RAW_OT"], cols=("A", "B", "C")))
    res = pub(world, AM_B)
    assert folder.is_dir() and AM_B in res.applied["assessment"]["regenerated"] and res.applied["assessment"]["retired"] == []
    from webapp.services import datasphere_publication_service as dps
    ids = dps.record_provenance(SimpleNamespace(client=CLIENT, root_object=AM_B), OWNER, "op-reactiva")
    assert eid in ids and not gov.is_retired(eid)
    hist = gov.retirement_history(eid)
    assert len(hist) == 1 and hist[0]["retired_at"] == rec["retired_at"] and hist[0]["lifted_by"] == OWNER
    assert hist[0]["lift_operation_id"] == "op-reactiva" and hist[0]["lift_cause"] == "REPUBLISHED" and hist[0]["files_deleted"]
    assert assess.status_for_entry(entry_of(world, AM_B)).state == assess.CURRENT
    assert ml.lifecycle_state(CLIENT, AM_B) == ml.ACTIVE


def test_lift_is_not_performed_without_a_durable_trace(world, monkeypatch):
    eid, _folder, _rec = retire(world, AM_B)
    real = gov._write
    monkeypatch.setattr(gov, "_write", lambda p, payload: (_ for _ in ()).throw(gov.GovernanceStorageError("x")) if gov.RETIREMENT_HISTORY_DIR in str(p) else real(p, payload))
    assert gov.lift_retirement([eid], actor=OWNER) == [] and gov.is_retired(eid)


# ═══════════════ (4) homónimos en otro cliente ═══════════════
def test_a_homonym_retired_in_another_client_does_not_affect_this_client(world):
    (world.catalog / gov.RETIRED_DIR).mkdir(exist_ok=True)
    (world.catalog / gov.RETIRED_DIR / "otro-cliente-x-flujo-x.json").write_text(json.dumps(
        {"schema": 1, "entry_id": "otro-cliente-x-flujo-x", "cliente": "Otro Cliente", "terminal_am": AM_B, "retired_at": "2026-11-27T00:00:00+00:00",
         "files_deleted": {"count": 3}}), encoding="utf-8")
    assert ml.retirements(CLIENT).models == {} and ml.retirements("Otro Cliente").is_retired(AM_B)
    assert ml.retirements("otro_cliente").is_retired(AM_B)                                          # mismo cliente normalizado
    folder = flow_dir(world, AM_B)
    world.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "D")))
    res = pub(world, AM_A)
    assert res.applied["assessment"]["retired"] == [] and AM_B in res.applied["assessment"]["regenerated"]
    assert folder.is_dir()


# ═══════════════ (5) dependencias compartidas ═══════════════
def test_a_change_in_a_shared_dependency_regenerates_the_active_model_and_keeps_the_retired_one(world):
    eid, folder, _rec = retire(world, AM_B, purge=True)
    before = env_bytes(AM_B)
    world.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "D")))
    res = pub(world, AM_A)
    assert AM_A in res.applied["assessment"]["regenerated"] and res.applied["assessment"]["retired"] == [AM_B]
    assert env_bytes(AM_B) == before and (world.pool / CLIENT / "views" / "1VR_DIM_PROD.json").is_file()
    from webapp.services import datasphere_publication_service as dps                             # publicar un objeto COMPARTIDO no reactiva al retirado
    dps.record_provenance(SimpleNamespace(client=CLIENT, root_object="1VR_DIM_PROD"), OWNER, "op-compartido")
    assert gov.is_retired(eid) and gov.retirement_history(eid) == []


def test_a_retired_model_that_leaves_the_pool_is_never_physically_removed(world):
    retire(world, AM_B)
    before = env_bytes(AM_B)
    for rel in ("analytic-models/AM_Otro", "views/3VF_OT", "local-tables/RAW_OT"):
        (world.pool / CLIENT / f"{rel}.json").unlink()
    world.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    res = pub(world, AM_A)
    assert res.applied["assessment"]["removed"] == [] and res.applied["assessment"]["retired"] == [AM_B]
    assert env_bytes(AM_B) == before


# ═══════════════ (6) inventario activo vs evidencia histórica ═══════════════
def test_inventory_counts_only_active_consumers_and_keeps_the_full_trace(world):
    retire(world, AM_B)
    active, retired = ml.split_consumers(CLIENT, [AM_A, AM_B, AM_A])
    assert active == [AM_A] and retired == [AM_B]
    assert ml.lifecycle_state(CLIENT, AM_B) == ml.RETIRED and ml.lifecycle_state(CLIENT, AM_A) == ml.ACTIVE
    visible = {e.entry_id for e in gov.visible_entries(all_entries(world))}
    assert entry_of(world, AM_A).entry_id in visible and entry_of(world, AM_B).entry_id not in visible
    assert env_bytes(AM_B) is not None                                                            # la evidencia histórica sigue legible
    assert assess._load_envelope(CLIENT, AM_B)["terminal_am"] == AM_B


def test_retired_is_a_distinct_state_from_stale_uncertain_and_removed():
    assert stage.RETIRED not in (stage.KEPT, stage.REBASED, stage.REGENERATED, stage.UNCERTAIN, stage.LEFT, stage.REMOVED)
    assert ml.RETIRED not in assess.STATES


# ═══════════════ (7) padrón de una activación futura ═══════════════
def test_future_padron_preview_excludes_the_retired_model_with_a_registered_cause(world):
    eid, _folder, rec = retire(world, AM_B)
    p = ml.padron_preview()
    assert [r["terminal_am"] for r in p["included"]] == [AM_A]
    ex = p["excluded"]
    assert len(ex) == 1 and ex[0]["terminal_am"] == AM_B and ex[0]["cause"] == ml.CAUSE_RETIRED and ex[0]["entry_id"] == eid
    assert ex[0]["retired_at"] == rec["retired_at"] and ex[0]["identity_source"] == ml.SOURCE_RECORD
    assert p["counts"] == {"envelopes": 2, "included": 1, "excluded": 1, "unresolved": 0}


def test_an_unresolvable_record_is_reported_and_excludes_nothing(world):
    (world.catalog / gov.RETIRED_DIR).mkdir(exist_ok=True)
    (world.catalog / gov.RETIRED_DIR / "acme-ya-no-existe-flujo-x.json").write_text(json.dumps(
        {"schema": 1, "entry_id": "acme-ya-no-existe-flujo-x", "cliente": CLIENT, "retired_at": "2026-10-01T00:00:00+00:00"}), encoding="utf-8")
    p = ml.padron_preview(client=CLIENT)
    assert p["counts"]["excluded"] == 0 and p["counts"]["included"] == 2
    assert p["unresolved"] == [{"entry_id": "acme-ya-no-existe-flujo-x", "client": CLIENT, "reason": "TILE_NOT_RESOLVABLE"}]


def test_a_legacy_record_is_resolved_through_the_catalog_with_the_canonical_resolver(world):
    eid, _folder, _rec = retire(world, AM_B, anchor=False)
    r = ml.retirements(CLIENT)
    assert r.is_retired(AM_B) and r.models[AM_B].identity_source == ml.SOURCE_CATALOG and r.models[AM_B].entry_id == eid


# ═══════════════ (8) v7 FINALIZED y respaldos intactos ═══════════════
def test_finalized_marker_backups_and_historical_judgment_stay_byte_identical(world):
    from webapp.services import judgment_activation as ja
    ja.write_marker(ja.FINALIZED, ruleset_version="judgment-rules/v7", previous_ruleset_version="judgment-rules/v6", plan_digest="e87784de" * 8, operator=OWNER)
    backup = world.governed / "migration-backups" / "judgment_rules_v7" / "20261127T114756Z-e87784de8bd1" / f"{AM_B}.json"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(env_bytes(AM_B))
    marker = ja.marker_path().read_bytes()
    env_b = env_bytes(AM_B)
    judgment_b = json.dumps(assess._load_envelope(CLIENT, AM_B).get("judgment"), sort_keys=True)
    eid, folder, _rec = retire(world, AM_B, purge=True)
    world.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "D")))
    pub(world, AM_A)
    ml.padron_preview()
    assert ja.marker_path().read_bytes() == marker and ja.read_marker()["state"] == ja.FINALIZED
    assert backup.read_bytes() == env_b and env_bytes(AM_B) == env_b
    assess.reset_memory_cache()
    assert json.dumps(assess._load_envelope(CLIENT, AM_B).get("judgment"), sort_keys=True) == judgment_b


# ═══════════════ ruta HTTP de retiro: persiste la identidad del modelo ═══════════════
@pytest.fixture()
def hub(world, monkeypatch):
    import config
    import webapp.main as main
    from webapp import access
    from webapp.services import auth_service
    monkeypatch.setattr(config, "BASE_DIR", Path(__file__).resolve().parents[1])
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", OWNER)
    main._invalidate_catalog_cache()

    def client():
        from fastapi.testclient import TestClient
        c = TestClient(main.app)
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": OWNER, "name": "T", "first_name": "T", "family_name": "U"}))
        return c
    yield SimpleNamespace(world=world, client=client, main=main)
    main._invalidate_catalog_cache()


def test_the_http_retire_route_persists_the_model_identity_and_the_purge_is_durable(hub):
    w = hub.world
    e = entry_of(w, AM_B)
    assert e.entry_id in {x.entry_id for x in hub.main._catalog_entries()[1]}                      # el Hub ve el mismo entry_id (cargado desde la raíz)
    folder = flow_dir(w, AM_B)
    r = hub.client().post("/catalog/flow/retire", data={"flow": e.entry_id, "delete_files": "on"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and 'data-catalog-retired="error"' not in r.text
    rec = gov.retirement(e.entry_id)
    assert rec["terminal_am"] == AM_B and rec["catalog_dir"] == f"{CLIENT}/{folder.name}" and rec["retired_by"] == OWNER and rec["files_deleted"]["count"] > 0
    assert not folder.exists()
    r2 = ml.retirements(CLIENT)
    assert r2.models[AM_B].identity_source == ml.SOURCE_RECORD and r2.models[AM_B].entry_id == e.entry_id
    env_before = env_bytes(AM_B)
    w.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    res = pub(w, AM_A)
    assert not folder.exists() and res.applied["assessment"]["retired"] == [AM_B] and env_bytes(AM_B) == env_before
    assert [x["terminal_am"] for x in ml.padron_preview()["excluded"]] == [AM_B]


def test_the_http_retire_route_still_retires_when_the_identity_cannot_be_resolved(hub, monkeypatch):
    e = entry_of(hub.world, AM_B)
    monkeypatch.setattr(assess, "resolve_entries", lambda entries: (_ for _ in ()).throw(RuntimeError("pool caído")))
    r = hub.client().post("/catalog/flow/retire", data={"flow": e.entry_id}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and gov.is_retired(e.entry_id)
    assert "terminal_am" not in gov.retirement(e.entry_id)                                         # sin ancla: se resolverá después por entry_id (registro anterior)
