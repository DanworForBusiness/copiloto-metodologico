"""P0 (incidente Biogenesis 2026-11-06) — la publicación transaccional es el ÚNICO camino y la integridad no depende de configuración.

Lo que fija (exportador, generador, manifiestos y ``remote_store`` REALES; tenant de Datasphere y bucket simulados, todo bajo ``tmp_path``):
  * no hay interruptor ni pedido de espejo: ``DANWOR_PUBLICATION_SAFE``/``DANWOR_PUBLICATION_MIRROR_UP`` no existen ni cambian nada;
  * la confirmación remota la decide ``DANWOR_PERSISTENCE``: ``remote`` confirma o rechaza, ``local`` publica (también transaccional) en disco;
  * una publicación fallida no deja evidencia parcial ni local ni en Spaces, y el sincronizador periódico no sube estados intermedios;
  * un objeto del pool de OTRO Space con contenido distinto se rechaza antes de tocar nada; una colisión que solo revela un paquete legacy se deshace;
  * la regeneración es POR CLIENTE: un cliente inconsistente no bloquea ni contamina la publicación de otro.
Datos sintéticos.
"""
from __future__ import annotations

import json

import pytest

import config
from core import remote_store
from tests.publication_fakes import CLIENT, FakeS3, enable_remote, seed_pricing, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog import publication_txn as txn


def _dim(ctx, client=CLIENT):
    return json.loads((ctx.pool / client / "views" / "1VR_DIM_PROD.json").read_text(encoding="utf-8"))["definitions"]["1VR_DIM_PROD"]["elements"]


def _legacy_bundle(ctx, client=CLIENT):
    """Paquete legacy en la RAÍZ del cliente (como los de SharePoint): embebe la dependencia 1VR_DIM_PROD tal como era al exportarlo."""
    prod_dim = json.loads((ctx.pool / client / "views" / "1VR_DIM_PROD.json").read_text(encoding="utf-8"))["definitions"]["1VR_DIM_PROD"]
    bundle = view("LEGACY_AM", ["1VR_DIM_PROD"])
    bundle["definitions"]["1VR_DIM_PROD"] = prod_dim
    (ctx.pool / client / "LEGACY_AM.json").write_text(json.dumps(bundle), encoding="utf-8")


def _qa_changes_the_shared_dimension(ctx):
    seed_pricing(ctx.tenant, "QA")
    ctx.tenant.put("QA", "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "SOLO_QA")))


@pytest.fixture()
def remote(ctx, monkeypatch):                                                                                   # noqa: F811
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    from webapp.services import datasphere_publication_service  # noqa: F401 — registra la guarda REAL del sincronizador periódico
    return s3


# ═══════════════ T1–T3 · sin interruptor ni variables: un solo camino ═══════════════
def test_t1_the_retired_variables_change_nothing_and_publish_is_always_transactional(ctx, monkeypatch):        # noqa: F811
    monkeypatch.setenv("DANWOR_PUBLICATION_SAFE", "0")
    monkeypatch.setenv("DANWOR_PUBLICATION_MIRROR_UP", "0")
    res, lines = ctx.publish()
    assert res.status == txn.COMPLETED and res.operation_id and any("PUBLICACION TRANSACCIONAL" in ln for ln in lines)
    manifest = json.loads(next((ctx.catalog / ".lineage_runs").rglob("lineage-manifest.json")).read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 3 and "integrity" in manifest and all("sha256" in o for o in manifest["objects"])   # nunca el v2 sin integridad
    import dataclasses
    assert not {"safe", "mirror_up"} & {f.name for f in dataclasses.fields(dsp.ExportRequest)}


def test_t2_local_development_publishes_through_the_same_transactional_unit(ctx):                              # noqa: F811
    assert remote_store.local_only()
    res, _ = ctx.publish()
    assert (res.status, res.durability) == (txn.COMPLETED, txn.DURABILITY_LOCAL_BY_CONFIG)
    assert [j["state"] for j in ctx.journals()] == [txn.J_LOCAL_COMMITTED]                                      # journal + deshacer también en local


def test_t3_remote_persistence_always_confirms_in_spaces(ctx, remote):                                          # noqa: F811
    res, _ = ctx.publish()
    assert (res.status, res.durability) == (txn.COMPLETED, txn.DURABILITY_REMOTE)
    assert any(k.endswith("Acme.publication.json") for k in remote.objects)                                     # marcador de activación confirmado


def test_t3b_read_only_persistence_cannot_confirm_so_nothing_is_published(ctx, monkeypatch):                    # noqa: F811
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    before = ctx.digest()
    res, _ = ctx.publish()
    assert res.status == txn.FAILED_NO_CHANGE and ctx.digest() == before


# ═══════════════ T4–T5 · identidad entre Spaces y colisiones legacy ═══════════════
def test_t4_a_conflicting_object_from_another_space_is_refused_before_touching_anything(ctx, remote):           # noqa: F811
    assert ctx.publish(space="PROD")[0].status == txn.COMPLETED
    _legacy_bundle(ctx)
    pre, remote_pre = ctx.digest(), dict(remote.objects)
    _qa_changes_the_shared_dimension(ctx)
    res, _ = ctx.publish(space="QA")
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "POOL_IDENTITY_CONFLICT")
    assert ctx.digest() == pre and remote.objects == remote_pre and "SOLO_QA" not in _dim(ctx)


def test_t5_a_collision_only_a_legacy_bundle_reveals_is_rolled_back_locally_and_spaces_is_untouched(ctx, remote):   # noqa: F811
    assert ctx.publish(space="PROD")[0].status == txn.COMPLETED
    _legacy_bundle(ctx)
    for mp in (ctx.catalog / ".lineage_runs" / CLIENT / "PROD").rglob("lineage-manifest.json"):
        mp.unlink()                                                                                            # nadie reclama el objeto: solo el paquete legacy lo contradice
    remote_store.mirror_up()
    pre, remote_pre = ctx.digest(), dict(remote.objects)
    _qa_changes_the_shared_dimension(ctx)
    res, _ = ctx.publish(space="QA")
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "CATALOG_GENERATION_FAILED")
    assert ctx.digest() == pre and "SOLO_QA" not in _dim(ctx)                                                  # local: la versión previa, sin evidencia parcial
    assert remote.objects == remote_pre                                                                        # Spaces: intacto
    remote_store.mirror_up()
    assert remote.objects == remote_pre                                                                        # y el ciclo periódico no sube nada distinto de la versión previa


# ═══════════════ T6–T7 · el sincronizador nunca publica un estado intermedio ═══════════════
@pytest.mark.parametrize("point", ["after_pool", "after_manifest", "after_catalog"])
def test_t6_the_periodic_sync_running_inside_the_unit_uploads_nothing(ctx, remote, monkeypatch, point):         # noqa: F811
    assert ctx.publish()[0].status == txn.COMPLETED
    ctx.tenant.put("PROD", "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    uploaded: list = []
    real = remote_store.mirror_up

    def tick(p, **kw):
        if p == point:
            uploaded.append(real())                                                                            # el ciclo periódico dispara EN MEDIO de la unidad
    monkeypatch.setattr(txn, "FAULT_HOOK", tick)
    assert ctx.publish()[0].status == txn.COMPLETED
    assert uploaded == [0]                                                                                     # retenido: ni un archivo de la versión a medias


def test_t7_a_failed_publication_leaves_nothing_for_the_periodic_sync(ctx, remote, monkeypatch):                # noqa: F811
    assert ctx.publish()[0].status == txn.COMPLETED
    remote_pre = dict(remote.objects)
    ctx.tenant.put("PROD", "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla del generador")))
    res, _ = ctx.publish()
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "CATALOG_GENERATION_FAILED")
    remote_store.mirror_up()                                                                                   # el rollback reescribe (mtime nuevo) los MISMOS bytes: el ciclo puede re-subirlos…
    assert remote.objects == remote_pre                                                                        # …pero el contenido en Spaces es exactamente la versión previa


# ═══════════════ T8 · regeneración por cliente: un cliente inconsistente no bloquea a otro ═══════════════
def test_t8_an_inconsistent_client_neither_blocks_nor_is_touched_by_another_clients_publication(ctx, remote):   # noqa: F811
    assert ctx.publish(space="PROD")[0].status == txn.COMPLETED
    _legacy_bundle(ctx)
    contaminated = json.loads((ctx.pool / CLIENT / "views" / "1VR_DIM_PROD.json").read_text(encoding="utf-8"))
    contaminated["definitions"]["1VR_DIM_PROD"]["elements"]["SOLO_QA"] = {"type": "cds.String"}
    (ctx.pool / CLIENT / "views" / "1VR_DIM_PROD.json").write_text(json.dumps(contaminated), encoding="utf-8")   # el estado que dejó el camino legacy en producción
    acme = {k: v for k, v in ctx.digest().items() if f"/{CLIENT}/" in f"/{k}" or k.startswith(CLIENT)}
    res, _ = ctx.publish("AM_Otro", space="PROD", client="Zeta")
    assert res.status == txn.COMPLETED                                                                         # el otro cliente publica
    assert {k: v for k, v in ctx.digest().items() if f"/{CLIENT}/" in f"/{k}" or k.startswith(CLIENT)} == acme   # y el inconsistente quedó tal cual (no se regeneró)


def test_t8b_global_regeneration_remains_an_explicit_maintenance_operation(ctx):                                 # noqa: F811
    assert ctx.publish()[0].status == txn.COMPLETED
    summary = dsp.regenerate_catalog_from_pool(ctx.pool, ctx.catalog)                                          # sin only_clients: todos los clientes, a pedido
    assert "Catálogo regenerado" in summary


# ═══════════════ T9–T12 · caída, reinicio y concurrencia ═══════════════
@pytest.mark.parametrize("point", ["after_pool", "after_cleanup", "after_catalog"])
def test_t9_a_crash_inside_the_unit_is_recovered_to_one_whole_version_and_the_sync_stays_held(ctx, remote, monkeypatch, point):   # noqa: F811
    from webapp.services import datasphere_publication_service as svc
    from tests.publication_fakes import SimulatedCrash
    assert ctx.publish()[0].status == txn.COMPLETED
    pre, remote_pre = ctx.digest(), dict(remote.objects)
    ctx.tenant.put("PROD", "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **k: (_ for _ in ()).throw(SimulatedCrash()) if p == point else None)
    with pytest.raises(SimulatedCrash):
        ctx.publish()
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert txn.has_pending(ctx.catalog) and svc._mirror_guard() is not None                                   # tras la caída el sincronizador queda retenido
    assert remote_store.mirror_up() == 0 and remote.objects == remote_pre
    txn.recover_pending(ctx.catalog)                                                                           # el arranque (boot_recover) o la próxima publicación lo resuelven
    assert ctx.digest() == pre and not txn.has_pending(ctx.catalog)


def test_t10_boot_recovery_runs_without_any_switch(ctx, remote, monkeypatch):                                   # noqa: F811
    from webapp.services import datasphere_publication_service as svc
    called: list = []
    monkeypatch.setattr(svc, "resolve_pending", lambda *a, **k: called.append(1))
    svc.boot_recover()
    assert called == [1]
    monkeypatch.setattr(svc, "_canonical_roots", lambda: (ctx.catalog, ctx.pool))
    assert svc.begin_boot_gate() is True                                                                       # con persistencia remota la compuerta de arranque SIEMPRE se cierra
    from core import publication_gate as gate
    gate.clear(ctx.catalog)


def test_t11_a_second_publication_while_one_is_applying_is_refused_without_effects(ctx, remote, monkeypatch):   # noqa: F811
    import threading
    monkeypatch.setattr(dsp, "_SAFE_LOCK_TIMEOUT_S", 0.5)                                                      # la segunda no espera los 30 s del candado
    assert ctx.publish()[0].status == txn.COMPLETED
    inside, release, out = threading.Event(), threading.Event(), {}

    def hold(p, **kw):
        if p == "after_pool" and threading.current_thread().name == "first":
            inside.set()
            release.wait(10)
    monkeypatch.setattr(txn, "FAULT_HOOK", hold)
    ctx.tenant.put("PROD", "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    t = threading.Thread(target=lambda: out.setdefault("first", ctx.publish()[0]), name="first")
    t.start()
    assert inside.wait(10)
    second, _ = ctx.publish("AM_Otro")
    release.set()
    t.join(10)
    assert second.status == txn.FAILED_NO_CHANGE and second.error_code == "CATALOG_BUSY"
    assert out["first"].status == txn.COMPLETED


def test_t12_catalog_regeneration_cannot_run_while_a_publication_holds_the_writer_lock(ctx, monkeypatch):        # noqa: F811
    from webapp.services import admin_tools
    monkeypatch.setattr(admin_tools, "run_catalog_generation", lambda graph_token="": pytest.fail("no debe regenerar con una publicación aplicando"))
    with txn.writer_lock(1):                                                                                   # una publicación en curso (mismo proceso, otro hilo vería lo mismo)
        import threading
        res: dict = {}
        th = threading.Thread(target=lambda: res.setdefault("r", admin_tools.run_catalog_generation_locked()))
        th.start()
        th.join(10)
    ran, message = res["r"]
    assert ran is False and "otro proceso" in message
