"""Reemplazo GOBERNADO de la publicación de un modelo hecha desde otro Space — pruebas de la UNIDAD con el exportador, los manifiestos, el generador del Catálogo y el
motor de assessment REALES (frontera simulada: el tenant de Datasphere). Todo bajo ``tmp_path``.

Contrato:
  * la guarda NO cambia: sin autorización, el conflicto sigue siendo ``POOL_IDENTITY_CONFLICT`` sin cambios, ahora con su ALCANCE (exclusivos / compartidos);
  * el reemplazo solo es posible (``safe`` + ``token``) si todos los conflictos los reclama UNA publicación anterior del MISMO modelo y ninguna publicación de otro modelo los reclama (lo compartido con otras fichas/paquetes legacy se informa y se confirma)
    (otro manifiesto, otra ficha del Catálogo o un paquete legacy);
  * la autorización es la huella exacta del plan: si cambió algo entre la confirmación y la ejecución (la publicación anterior, el modelo, sus dependencias),
    ``REPLACEMENT_PLAN_CHANGED`` sin cambios; un token sin conflicto que reemplazar tampoco autoriza nada;
  * aplicado: el manifiesto desplazado se retira DENTRO de la unidad (rollback lo restaura), el ledger pasa al Space nuevo y el Assessment gobernado se
    regenera desde la evidencia publicada.
"""
from __future__ import annotations

import json

import pytest

from core import publication_gate as gate
from tests.publication_fakes import CLIENT, seed_pricing, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import publication_ledger as ledger_mod
from tools.catalog import publication_txn as txn
from webapp.services import assessment_service as assess

AM_A, AM_B = "AM_Lista_Precios", "AM_Otro"
QA = "QA"


@pytest.fixture()
def world(ctx, monkeypatch, tmp_path):                                                                     # noqa: F811
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate.set_resolver(None)
    seed_pricing(ctx.tenant, QA)
    yield ctx
    gate.clear(ctx.catalog)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()


def pub(w, root=AM_A, **kw):
    return w.publish(root, stage=True, **kw)[0]


def manifest(w, space, root=AM_A):
    return w.catalog / ".lineage_runs" / CLIENT / space / "analytic-model" / root / "lineage-manifest.json"


def qa_changes_exclusive(w):
    """En QA cambia SOLO la vista de hecho propia del modelo (exclusiva de AM_Lista_Precios)."""
    w.tenant.put(QA, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE", "1VR_LP_EXTRA"], cols=("A", "B", "SOLO_QA")))


def test_without_authorization_the_conflict_is_the_same_guard_now_with_its_scope(world):
    assert pub(world).status == txn.COMPLETED
    qa_changes_exclusive(world)
    before = world.digest()
    res = pub(world, space=QA)
    assert res.status == txn.FAILED_NO_CHANGE and res.error_code == "POOL_IDENTITY_CONFLICT" and world.digest() == before
    r = res.replacement
    assert r["safe"] and len(r["token"]) == 64 and r["displaced"]["space"] == "PROD" and not r["shared"]
    assert [c["technical_name"] for c in r["exclusive"]] == ["3VF_LP"]


def test_confirmed_replacement_moves_the_model_to_the_new_space_atomically(world):
    assert pub(world).status == txn.COMPLETED
    qa_changes_exclusive(world)
    token = pub(world, space=QA).replacement["token"]
    env_before = assess._load_envelope(CLIENT, AM_A)
    req = world.request(AM_A, space=QA, stage=True)
    req.replace_token = token
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    with dsp.capture_output():
        res = dsp.run_export(req)
    assert res.status == txn.COMPLETED, (res.error_code, res.message)
    assert not manifest(world, "PROD").exists() and manifest(world, QA).exists()                          # la publicación desplazada ya no reclama nada
    m = json.loads(manifest(world, QA).read_text(encoding="utf-8"))
    assert m["space"] == QA and m["replaces"]["space"] == "PROD"
    assert "SOLO_QA" in (world.pool / CLIENT / "views" / "3VF_LP.json").read_text(encoding="utf-8")
    _, led = ledger_mod.load(world.catalog, CLIENT)
    assert led["active"]["space"] == QA and led["active"]["replaced"]["space"] == "PROD"
    assess.reset_memory_cache()
    env = assess._load_envelope(CLIENT, AM_A)                                                              # assessment gobernado regenerado desde lo PUBLICADO (no se copia nada)
    assert env is not None and env["input_closure_signature"] != env_before["input_closure_signature"]
    entry = next(e for e in __import__("tools.catalog.catalog_service", fromlist=["x"]).load_catalog(world.catalog / CLIENT) if AM_A in e.artefactos)
    assert assess.status_for_entry(entry).state == "CURRENT"
    assert pub(world, space=QA).status == txn.COMPLETED                                                    # y desde ahora QA republica sin conflicto


def test_a_dependency_claimed_by_another_published_model_is_never_replaceable(world):
    assert pub(world).status == txn.COMPLETED and pub(world, AM_B).status == txn.COMPLETED                 # 1VR_DIM_PROD: AM_A y AM_B desde PROD
    world.tenant.put(QA, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "SOLO_QA")))
    before = world.digest()
    res = pub(world, space=QA)
    r = res.replacement
    assert res.error_code == "POOL_IDENTITY_CONFLICT" and not r["safe"] and r["token"] == ""
    shared = {c["technical_name"]: c for c in r["shared"]}
    assert "1VR_DIM_PROD" in shared and shared["1VR_DIM_PROD"]["used_by_models"]
    assert "CLAIMED_BY_OTHER_MODEL" in r["reasons"]                                                       # otra PUBLICACIÓN gobernada lo reclama: sigue bloqueando
    req = world.request(AM_A, space=QA, stage=True)
    req.replace_token = "f" * 64                                                                          # un token inventado no autoriza nada
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    with dsp.capture_output():
        forced = dsp.run_export(req)
    assert forced.error_code == "REPLACEMENT_PLAN_CHANGED" and world.digest() == before


def test_an_object_shared_only_with_a_legacy_package_is_listed_and_replaceable_with_confirmation(world):
    # decisión del owner (2026-11-09): el Space gobernado no es inmutable; lo compartido con fichas/paquetes legacy del cliente se informa y se reemplaza al confirmar
    assert pub(world).status == txn.COMPLETED
    qa_changes_exclusive(world)
    (world.pool / CLIENT / "PAQUETE_LEGACY.json").write_text(json.dumps({"definitions": {"3VF_LP": {"kind": "entity"}}}), encoding="utf-8")
    r = pub(world, space=QA).replacement
    assert r["safe"] and len(r["token"]) == 64 and r["shared"][0]["legacy_packages"] == ["PAQUETE_LEGACY"]


def test_a_change_after_the_confirmation_invalidates_it_without_changes(world):
    assert pub(world).status == txn.COMPLETED
    qa_changes_exclusive(world)
    token = pub(world, space=QA).replacement["token"]
    world.tenant.put(QA, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE", "1VR_LP_EXTRA"], cols=("A", "B", "OTRO_CAMBIO")))   # el modelo cambió en QA
    before = world.digest()
    req = world.request(AM_A, space=QA, stage=True)
    req.replace_token = token
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    with dsp.capture_output():
        res = dsp.run_export(req)
    assert res.error_code == "REPLACEMENT_PLAN_CHANGED" and world.digest() == before and manifest(world, "PROD").exists()


def test_a_concurrent_republication_of_the_displaced_space_invalidates_the_confirmation(world, monkeypatch):
    assert pub(world).status == txn.COMPLETED
    qa_changes_exclusive(world)
    token = pub(world, space=QA).replacement["token"]
    world.tenant.put("PROD", "views", "1VR_LP_EXTRA", view("1VR_LP_EXTRA", ["RAW_PRECIOS"], cols=("A", "B", "PROD_NUEVO")))
    state = {"done": False}

    def hook(point, **kw):                                                                                 # PROD republica entre la extracción de QA y el candado
        if point == "after_validate" and not state["done"]:
            state["done"] = True
            assert pub(world).status == txn.COMPLETED
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    req = world.request(AM_A, space=QA, stage=True)
    req.replace_token = token
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    with dsp.capture_output():
        res = dsp.run_export(req)
    assert res.error_code == "REPLACEMENT_PLAN_CHANGED" and manifest(world, "PROD").exists() and not manifest(world, QA).exists()


def test_a_token_without_a_conflict_to_replace_authorizes_nothing(world):
    assert pub(world).status == txn.COMPLETED
    req = world.request(AM_A, stage=True)
    req.replace_token = "a" * 64
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    with dsp.capture_output():
        res = dsp.run_export(req)
    assert res.error_code == "REPLACEMENT_PLAN_CHANGED"


def test_a_failure_after_removing_the_displaced_publication_rolls_everything_back(world, monkeypatch):
    assert pub(world).status == txn.COMPLETED
    qa_changes_exclusive(world)
    token = pub(world, space=QA).replacement["token"]
    before = world.digest()

    def hook(point, **kw):
        if point == "after_displaced_removed":
            raise RuntimeError("falla simulada")
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    req = world.request(AM_A, space=QA, stage=True)
    req.replace_token = token
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    with dsp.capture_output():
        res = dsp.run_export(req)
    assert res.status == txn.ROLLED_BACK and world.digest() == before and manifest(world, "PROD").exists()


# ── frontera HTTP: el token llega VALIDADO al servicio (malformado = vacío) y el evento final lleva el alcance para el diálogo ──
@pytest.mark.parametrize("sent, received", [("b" * 64, "b" * 64), ("no-es-un-token", ""), ("", "")])
def test_the_run_endpoint_forwards_only_a_well_formed_token_and_returns_the_scope(monkeypatch, sent, received):
    from tests.webapp import test_datasphere_publication_safe as s
    from webapp import access
    from webapp.services import datasphere_publication_service as pubsvc
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    seen = {}
    scope = {"safe": False, "token": "", "reasons": ["SHARED_DEPENDENCY"], "exclusive": [], "shared": [{"technical_name": "VT_X"}]}

    def fake_run(values, secret, **kw):
        seen["token"] = kw.get("replace_token")
        return pubsvc.PublicationOutcome("FAILED", "POOL_IDENTITY_CONFLICT", "x", stage="publish", status=txn.FAILED_NO_CHANGE, replacement=scope)
    monkeypatch.setattr(pubsvc, "run", fake_run)
    r = s._client("owner@danwor.com").post(s.RUN_URL, data={**s.FORM, "replace_token": sent}, headers={"Origin": "http://testserver"})
    done = next(json.loads(ln) for ln in r.text.splitlines() if '"done"' in ln)
    assert seen["token"] == received and done["error_code"] == "POOL_IDENTITY_CONFLICT" and done["replacement"] == scope and done["result_url"] is None


def test_an_unexpected_failure_in_the_worker_still_ends_the_stream(monkeypatch):
    """Una excepción dentro del hilo (p. ej. una firma incompatible) jamás deja el stream sin evento terminal: antes colgaba al cliente (y a la suite) indefinidamente."""
    from tests.webapp import test_datasphere_publication_safe as s
    from webapp import access
    from webapp.services import datasphere_publication_service as pubsvc
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")

    def boom(values, secret, *, user_email="", on_line=None, on_phase=None):              # firma VIEJA: TypeError por replace_token
        raise AssertionError("no debería llegar acá")
    monkeypatch.setattr(pubsvc, "run", boom)
    r = s._client("owner@danwor.com").post(s.RUN_URL, data=s.FORM, headers={"Origin": "http://testserver"})
    done = next(json.loads(ln) for ln in r.text.splitlines() if '"done"' in ln)
    assert done["state"] == "FAILED" and done["error_code"] == "EXTRACTION_FAILED" and done["result_url"] is None
