"""Fase 0 de la republicación del Catálogo (H1/H3/H5/H6) — pruebas de INTEGRACIÓN con el exportador, la limpieza, los manifiestos, el generador y ``remote_store`` REALES.

Frontera simulada: el tenant de Datasphere y el bucket (en memoria). Todo bajo ``tmp_path``. Cubre: completitud (abortar sin tocar nada), contrato de estados, cleanup diferido
no destructivo, revalidación bajo el candado, exclusión entre hilos, confirmación remota real, idempotencia, aislamiento de clientes y el BARRIDO de fallos inyectados con
recuperación (el estado es siempre el previo o el completo, nunca mixto).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from core import remote_store
from tests.publication_fakes import CLIENT, HOST, SPACE, FakeS3, SimulatedCrash, analytic_model, enable_remote, seed_pricing, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog import publication_txn as txn


def v2(t):
    """Cambio realista: se modifica una vista, se quita una dependencia y se actualiza la vista que la usaba (2 escrituras + 1 borrado + manifiesto + Catálogo)."""
    t.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    t.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    t.drop(SPACE, "views", "1VR_LP_EXTRA")


def _record(**row):
    """Opcional (``PUBLICATION_SWEEP_REPORT=<archivo>``): deja una línea JSON por caso del barrido, para armar la matriz de fallos inyectados del informe."""
    out = os.getenv("PUBLICATION_SWEEP_REPORT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _name(d, pre, post):
    return "PRE" if d == pre else "POST" if d == post else "MIXTO"


def pending_journals(ctx):
    return [j for j in ctx.journals() if j["state"] in (txn.J_PLANNED, txn.J_APPLYING)]


# ═══════════════ H1 · completitud: una extracción incompleta no altera el Catálogo válido ═══════════════
def test_a_transient_read_error_aborts_before_touching_anything(ctx):
    ctx.publish()
    before = ctx.digest()
    ctx.tenant.fail_reads.add(("views", "3VF_LP"))
    res, _ = ctx.publish()
    assert (res.ok, res.status, res.error_code) == (False, txn.FAILED_NO_CHANGE, "EXTRACTION_INCOMPLETE")
    assert {"kind": "read", "object_type": "views", "technical_name": "3VF_LP", "code": "READ_FAILED"} in res.failures
    assert ctx.digest() == before and pending_journals(ctx) == [] and res.deleted_stale == 0               # NADA cambió: ni pool, ni manifiesto, ni Catálogo
    ctx.tenant.fail_reads.clear()
    again, _ = ctx.publish()
    assert again.status == txn.COMPLETED and ctx.digest() == before                                         # y el reintento sin el error converge al mismo estado


@pytest.mark.parametrize("family", ["analytic-models", "views", "local-tables", "remote-tables", "replication-flows"])
def test_a_transient_listing_error_in_any_family_aborts_without_changes(ctx, family):
    ctx.publish()
    before = ctx.digest()
    ctx.tenant.fail_lists.add(family)
    res, _ = ctx.publish()
    assert res.status == txn.FAILED_NO_CHANGE and res.error_code == "EXTRACTION_INCOMPLETE", family
    assert any(f["kind"] == "inventory" and f["object_type"] == family for f in res.failures)
    assert ctx.digest() == before and res.deleted_stale == 0


def test_a_truncated_listing_marked_by_the_cli_is_an_incomplete_extraction(ctx):
    ctx.publish()
    before = ctx.digest()
    ctx.tenant.incomplete_lists.add("views")
    res, _ = ctx.publish()
    assert res.status == txn.FAILED_NO_CHANGE and any(f["code"] == "INVENTORY_INCOMPLETE" for f in res.failures) and ctx.digest() == before


def test_incomplete_extraction_never_regenerates_the_catalog_nor_uploads(ctx, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    ctx.publish(mirror=True)
    uploads, before = s3.uploads, ctx.digest()
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda *a, **k: pytest.fail("no se regenera con evidencia incompleta"))
    ctx.tenant.fail_reads.add(("views", "1VR_LP_BASE"))
    res, _ = ctx.publish(mirror=True)
    assert res.status == txn.FAILED_NO_CHANGE and s3.uploads == uploads and ctx.digest() == before


def test_the_legacy_path_is_untouched_when_the_switch_is_off(ctx):
    """Con ``safe=False`` rige el camino anterior (documentación de la compatibilidad): mismo pool y Catálogo que el seguro en el camino feliz, y su comportamiento previo ante errores."""
    ctx.publish(safe=False)
    legacy = {k: v for k, v in ctx.digest().items() if "lineage-manifest" not in k and not k.startswith(".")}
    assert ctx.publish(safe=False)[0].status == ""                                                         # el camino anterior no informa ``status``
    shutil.rmtree(ctx.catalog)
    ctx.catalog.mkdir()
    safe, _ = ctx.publish(safe=True)
    assert safe.status == txn.COMPLETED
    assert {k: v for k, v in ctx.digest().items() if "lineage-manifest" not in k and not k.startswith(".")} == legacy
    ctx.tenant.fail_reads.add(("views", "3VF_LP"))
    assert ctx.publish(safe=False)[0].ok is True                                                           # defecto conocido del camino anterior: por eso el interruptor existe


# ═══════════════ contrato de estados ═══════════════
def test_happy_path_is_completed_and_reports_what_was_applied(ctx):
    res, lines = ctx.publish()
    assert (res.ok, res.status, res.error_code, res.exit_code) == (True, txn.COMPLETED, None, 0)
    assert res.durability == txn.DURABILITY_NOT_REQUESTED and res.stages["extraction"] == "OK" and res.stages["apply"] == "OK" and res.stages["catalog"] == "OK"
    assert res.applied["changed"] is True and len(res.applied["added"]) == 8 and res.applied["modified"] == [] and res.catalog_generated is True
    assert any("ESTADO DE LA PUBLICACIÓN: COMPLETED" in ln for ln in lines) and not any("Persistencia canónica: OK" in ln for ln in lines)
    manifest = txn.load_manifest(next(ctx.catalog.rglob("lineage-manifest.json")))
    assert manifest.state == txn.M_OK and manifest.data["schemaVersion"] == 3 and manifest.data["tenantId"] == "t.eu10.hcs.cloud.sap"
    assert all(o["sha256"] for o in manifest.data["objects"]) and manifest.data["operationId"] == res.operation_id
    assert [j["state"] for j in ctx.journals()] == [txn.J_LOCAL_COMMITTED]


def test_a_republication_without_changes_is_completed_and_rewrites_nothing(ctx, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    first, _ = ctx.publish(mirror=True)
    assert first.status == txn.COMPLETED and first.durability == txn.DURABILITY_REMOTE
    stamps = {p: p.stat().st_mtime_ns for p in ctx.catalog.rglob("*") if p.is_file()}
    uploads, digest = s3.uploads, ctx.digest()
    again, _ = ctx.publish(mirror=True)
    assert again.status == txn.COMPLETED and again.applied["changed"] is False and again.applied["added"] == [] and again.applied["modified"] == []
    assert {p: p.stat().st_mtime_ns for p in ctx.catalog.rglob("*") if p.is_file()} == stamps                  # ni un archivo reescrito
    assert s3.uploads == uploads and ctx.digest() == digest                                                   # ni una subida


def test_modifications_additions_and_removals_are_reported_and_applied(ctx):
    ctx.publish()
    v2(ctx.tenant)
    res, _ = ctx.publish()
    assert res.status == txn.COMPLETED and res.applied["removed"] == ["views/1VR_LP_EXTRA"]
    assert sorted(res.applied["modified"]) == ["views/1VR_LP_BASE", "views/3VF_LP"] and "views/1VR_LP_EXTRA" not in ctx.pool_names()


def test_shared_dependencies_are_retained_and_removed_only_when_nobody_owns_them(ctx):
    ctx.publish("AM_Lista_Precios")
    ctx.publish("AM_Otro")
    ctx.tenant.put(SPACE, "analytic-models", "AM_Lista_Precios", analytic_model("AM_Lista_Precios", "3VF_LP", [], label="Modelo Analítico - Precios"))
    first, _ = ctx.publish("AM_Lista_Precios")
    assert first.retained_shared == 2 and "views/1VR_DIM_PROD" in ctx.pool_names()
    ctx.tenant.put(SPACE, "analytic-models", "AM_Otro", analytic_model("AM_Otro", "3VF_OT", [], label="Modelo Analítico - Otros"))
    second, _ = ctx.publish("AM_Otro")
    assert second.status == txn.COMPLETED and "views/1VR_DIM_PROD" not in ctx.pool_names()


def test_contraction_is_observed_but_never_blocks_a_publication(ctx):
    ctx.publish()
    ctx.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["RAW_PRECIOS"]))
    ctx.tenant.put(SPACE, "analytic-models", "AM_Lista_Precios", analytic_model("AM_Lista_Precios", "3VF_LP", [], label="Modelo Analítico - Precios"))
    res, lines = ctx.publish(features=["analytic-models", "views", "tables"])
    assert res.status == txn.COMPLETED and res.observations["contraction"]["previous"] == 8 and res.observations["contraction"]["current"] < 4 + 1
    assert any("OBSERVACION" in ln for ln in lines)


# ═══════════════ cleanup diferido: nunca destructivo, nunca COMPLETED ═══════════════
def _two_published_then_drop_shared(ctx):
    ctx.publish("AM_Lista_Precios")
    ctx.publish("AM_Otro")
    ctx.tenant.put(SPACE, "analytic-models", "AM_Lista_Precios", analytic_model("AM_Lista_Precios", "3VF_LP", [], label="Modelo Analítico - Precios"))


def _manifest_of(ctx, root):
    return next(p for p in ctx.catalog.rglob("lineage-manifest.json") if p.parent.name == root)


def test_a_corrupt_manifest_of_another_root_never_authorizes_deletions(ctx):
    _two_published_then_drop_shared(ctx)
    other = _manifest_of(ctx, "AM_Otro")
    other.write_text(other.read_text(encoding="utf-8")[:40], encoding="utf-8")
    corrupt = other.read_bytes()
    res, _ = ctx.publish("AM_Lista_Precios")
    assert res.status == txn.COMMITTED_CLEANUP_DEFERRED and res.ok is False and res.error_code == "CLEANUP_DEFERRED" and "other_manifest_unusable" in res.deferred_reasons
    assert "views/1VR_DIM_PROD" in ctx.pool_names() and "local-tables/RAW_PROD" in ctx.pool_names() and res.deleted_stale == 0     # 0 borrados
    assert other.read_bytes() == corrupt                                                                      # el archivo corrupto no se toca ni se sobrescribe
    pending = txn.load_manifest(_manifest_of(ctx, "AM_Lista_Precios")).data["pendingCleanup"]
    assert {"object_type": "views", "technical_name": "1VR_DIM_PROD"} in pending                              # la limpieza queda registrada para reintentarse


def test_deferred_cleanup_is_completed_once_the_ownership_is_known_again(ctx):
    _two_published_then_drop_shared(ctx)
    other = _manifest_of(ctx, "AM_Otro")
    other.write_text("{no-es-json", encoding="utf-8")
    assert ctx.publish("AM_Lista_Precios")[0].status == txn.COMMITTED_CLEANUP_DEFERRED
    ctx.tenant.put(SPACE, "analytic-models", "AM_Otro", analytic_model("AM_Otro", "3VF_OT", [], label="Modelo Analítico - Otros"))
    assert ctx.publish("AM_Otro")[0].status == txn.COMMITTED_CLEANUP_DEFERRED                                 # republicar B repara su manifiesto, pero su ownership previo era desconocido: sigue PARTIAL
    assert txn.load_manifest(_manifest_of(ctx, "AM_Otro")).usable and ctx.publish("AM_Otro")[0].status == txn.COMPLETED   # y la siguiente ya es normal
    final, _ = ctx.publish("AM_Lista_Precios")
    assert final.status == txn.COMPLETED and "views/1VR_DIM_PROD" not in ctx.pool_names()                     # lo pendiente se limpia, ya sin dueño
    assert "pendingCleanup" not in txn.load_manifest(_manifest_of(ctx, "AM_Lista_Precios")).data


@pytest.mark.parametrize("damage", ["truncated", "invalid_json", "tampered"])
def test_an_unusable_own_previous_manifest_defers_cleanup_instead_of_deleting(ctx, damage):
    ctx.publish()
    v2(ctx.tenant)
    own = _manifest_of(ctx, "AM_Lista_Precios")
    text = own.read_text(encoding="utf-8")
    own.write_text({"truncated": text[:40], "invalid_json": "{x", "tampered": text.replace('"AM_Lista_Precios"', '"OTRO"', 1)}[damage], encoding="utf-8")
    res, _ = ctx.publish()
    assert res.status == txn.COMMITTED_CLEANUP_DEFERRED and res.ok is False
    assert "views/1VR_LP_EXTRA" in ctx.pool_names() and res.deleted_stale == 0
    assert ctx.publish()[0].status == txn.COMPLETED                                                           # el manifiesto quedó reescrito y sano: la siguiente es normal


def test_an_incomplete_restore_defers_cleanup_and_never_deletes(ctx, monkeypatch):
    enable_remote(monkeypatch, FakeS3())
    ctx.publish()
    v2(ctx.tenant)
    monkeypatch.setattr(remote_store, "_restore_degraded", True, raising=False)
    res, _ = ctx.publish()
    assert res.status == txn.COMMITTED_CLEANUP_DEFERRED and "restore_incomplete" in res.deferred_reasons and "views/1VR_LP_EXTRA" in ctx.pool_names()


def test_revalidation_under_the_lock_sees_a_manifest_that_became_corrupt_after_extraction(ctx, monkeypatch):
    _two_published_then_drop_shared(ctx)
    other = _manifest_of(ctx, "AM_Otro")

    def hook(point, **kw):
        if point == "after_validate":                                                                          # entre la extracción y el candado
            other.write_text(other.read_text(encoding="utf-8")[:30], encoding="utf-8")
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    res, _ = ctx.publish("AM_Lista_Precios")
    assert res.status == txn.COMMITTED_CLEANUP_DEFERRED and "views/1VR_DIM_PROD" in ctx.pool_names()           # lo calculado antes del candado NO se dio por válido


def test_revalidation_under_the_lock_sees_a_manifest_adopted_after_extraction(ctx, monkeypatch):
    ctx.publish("AM_Lista_Precios")                                                                           # A es dueño de la dimensión compartida
    ctx.tenant.put(SPACE, "analytic-models", "AM_Lista_Precios", analytic_model("AM_Lista_Precios", "3VF_LP", [], label="Modelo Analítico - Precios"))
    state = {"nested": False}

    def hook(point, **kw):
        if point == "after_validate" and not state["nested"]:
            state["nested"] = True
            assert ctx.publish("AM_Otro")[0].status == txn.COMPLETED                                          # B adopta la dimensión justo antes de que A tome el candado
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    res, _ = ctx.publish("AM_Lista_Precios")
    assert res.status == txn.COMPLETED and res.retained_shared == 2 and "views/1VR_DIM_PROD" in ctx.pool_names() and "local-tables/RAW_PROD" in ctx.pool_names()


# ═══════════════ identidad: guardia transitoria ═══════════════
def test_a_conflicting_object_from_another_space_is_refused_without_changes(ctx):
    ctx.publish("AM_Lista_Precios", space="PROD")
    seed_pricing(ctx.tenant, "QA")
    ctx.tenant.put("QA", "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "SOLO_QA")))
    before = ctx.digest()
    res, _ = ctx.publish("AM_Otro", space="QA")
    assert res.status == txn.FAILED_NO_CHANGE and res.error_code == "POOL_IDENTITY_CONFLICT" and ctx.digest() == before


def test_the_same_object_from_another_space_with_identical_content_is_not_a_conflict(ctx):
    ctx.publish("AM_Lista_Precios", space="PROD")
    seed_pricing(ctx.tenant, "QA")
    assert ctx.publish("AM_Otro", space="QA")[0].status == txn.COMPLETED


def test_a_sibling_root_of_the_same_space_may_update_a_shared_object(ctx):
    ctx.publish("AM_Lista_Precios")
    ctx.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "C_SHARED")))
    assert ctx.publish("AM_Otro")[0].status == txn.COMPLETED and "C_SHARED" in (ctx.pool / CLIENT / "views" / "1VR_DIM_PROD.json").read_text(encoding="utf-8")


def test_the_same_space_name_on_a_different_tenant_with_different_content_is_a_conflict(ctx):
    ctx.publish("AM_Lista_Precios")
    ctx.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "OTRO_TENANT")))
    res, _ = ctx.publish("AM_Otro", host="https://otro.eu10.hcs.cloud.sap")
    assert res.status == txn.FAILED_NO_CHANGE and res.error_code == "POOL_IDENTITY_CONFLICT"


# ═══════════════ generación acotada: una publicación no toca a otros clientes (S14) ═══════════════
def test_publishing_never_prunes_or_touches_another_clients_catalog(ctx):
    ctx.publish("AM_Lista_Precios", client="Acme")
    ctx.publish("AM_Otro", client="Beta")
    beta = {k: v for k, v in ctx.digest().items() if k.startswith("Beta/")}
    assert beta
    shutil.rmtree(ctx.pool / "Beta")                                                                          # p. ej. un restore parcial que trajo el Catálogo pero no el pool de Beta
    ctx.publish("AM_Lista_Precios", client="Acme")
    assert {k: v for k, v in ctx.digest().items() if k.startswith("Beta/")} == beta


# ═══════════════ confirmación remota real (H3) ═══════════════
def test_remote_confirmation_requires_persist_changed_to_confirm_and_uploads_the_marker_last(ctx, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    res, lines = ctx.publish(mirror=True)
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_REMOTE and res.stages["remote"] == "OK"
    assert {k.rsplit("/", 1)[-1] for k in s3.order[-2:]} == {".catalog_manifest.json", "Acme.publication.json"} and any("Persistencia canónica: OK" in ln for ln in lines)   # los marcadores van AL FINAL
    assert [j["state"] for j in ctx.journals()] == [txn.J_REMOTE_CONFIRMED]


def test_a_failed_upload_never_leaves_a_new_version_active_and_restores_the_last_confirmed_one(ctx, monkeypatch):
    """P1: si Spaces no confirma, la versión nueva NO queda vigente (antes quedaba «publicada, no confirmada»): local y remoto vuelven a la última versión confirmada."""
    s3 = FakeS3(fail_after=3)
    enable_remote(monkeypatch, s3)
    res, lines = ctx.publish(mirror=True)
    assert (res.status, res.ok, res.error_code) == (txn.ROLLED_BACK, False, "PERSISTENCE_FAILED")
    assert not any("Persistencia canónica: OK" in ln for ln in lines)
    assert ctx.pool_names() == [] and not s3.objects                                                          # no había versión previa: local y remoto quedaron VACÍOS (compensación verificada)
    assert [j["state"] for j in ctx.journals()] == [txn.J_ROLLED_BACK] and not txn.has_pending(ctx.catalog)
    s3.fail_after = None                                                                                       # Spaces vuelve
    retry, _ = ctx.publish(mirror=True)
    assert retry.status == txn.COMPLETED and retry.durability == txn.DURABILITY_REMOTE
    assert sum(1 for k in s3.objects if k.startswith("audit/catalog_local/")) == sum(1 for p in ctx.catalog.rglob("*") if p.is_file())


def test_a_blocked_sync_refuses_the_publication_without_touching_anything(ctx, monkeypatch):
    enable_remote(monkeypatch, FakeS3())
    monkeypatch.setattr(remote_store, "_restore_degraded", True, raising=False)
    res, lines = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "REMOTE_NOT_READY") and not any("Persistencia canónica: OK" in ln for ln in lines)
    assert ctx.pool_names() == [] and not txn.has_pending(ctx.catalog)


def test_local_persistence_by_configuration_is_reported_as_such_not_as_remote(ctx):
    res, lines = ctx.publish(mirror=True)                                                                      # DANWOR_PERSISTENCE=local (desarrollo)
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_LOCAL_BY_CONFIG and not any("Persistencia canónica: OK" in ln for ln in lines)


def test_partial_status_and_pending_remote_leave_the_periodic_cycle_from_publishing_half_states(ctx, monkeypatch):
    s3 = FakeS3(fail_after=2)
    enable_remote(monkeypatch, s3)
    ctx.publish(mirror=True)
    uploaded = s3.uploads
    remote_store.mirror_up()                                                                                  # el ciclo periódico NO completa por su cuenta lo retenido
    assert s3.uploads == uploaded and not any(k.endswith(".catalog_manifest.json") for k in s3.objects)


# ═══════════════ H5 · exclusión y recuperación ═══════════════
def test_a_second_publication_is_refused_with_catalog_busy_while_another_is_applying(ctx, monkeypatch):
    ctx.publish("AM_Lista_Precios")
    ctx.publish("AM_Otro")
    ctx.tenant.put(SPACE, "views", "3VF_OT", view("3VF_OT", ["RAW_OT"], cols=("A", "B", "C_NUEVA")))
    entered, release = threading.Event(), threading.Event()

    def hook(point, **kw):
        if point == "after_undo_snapshot" and threading.current_thread().name == "publicador-B":
            entered.set()
            assert release.wait(30)
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    monkeypatch.setattr(dsp, "_SAFE_LOCK_TIMEOUT_S", 0.3)
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("b", ctx.publish("AM_Otro")[0]), name="publicador-B")
    thread.start()
    assert entered.wait(30)
    mid = ctx.digest()
    busy, _ = ctx.publish("AM_Lista_Precios")                                                                  # A llega con B a mitad de su apply
    assert (busy.status, busy.error_code) == (txn.FAILED_NO_CHANGE, "CATALOG_BUSY") and ctx.digest() == mid
    release.set()
    thread.join(30)
    assert out["b"].status == txn.COMPLETED
    manifest = txn.load_manifest(_manifest_of(ctx, "AM_Otro"))
    assert all((ctx.pool / CLIENT / o["object_type"] / f"{o['technical_name']}.json").is_file() for o in manifest.data["objects"])   # el manifiesto no reclama nada inexistente
    assert ctx.publish("AM_Lista_Precios")[0].status == txn.COMPLETED


def test_a_pending_journal_blocks_publication_until_recovered(ctx, monkeypatch):
    ctx.publish()
    v2(ctx.tenant)
    real_restore = txn.UndoLog.restore
    monkeypatch.setattr(txn.UndoLog, "restore", lambda self: ["algo"])
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **k: (_ for _ in ()).throw(RuntimeError("falla")) if p == "after_manifest" else None)
    res, _ = ctx.publish()
    assert res.status == txn.RECOVERY_REQUIRED and res.error_code == "RECOVERY_REQUIRED" and txn.has_pending(ctx.catalog)
    monkeypatch.setattr(txn.UndoLog, "restore", real_restore)
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    healed, _ = ctx.publish()                                                                                  # la recuperación corre primero y luego se publica
    assert healed.status == txn.COMPLETED and not txn.has_pending(ctx.catalog)


# ═══════════════ BARRIDO de fallos inyectados: siempre estado previo o completo, nunca mixto ═══════════════
BEFORE_COMMIT = ["after_lock", "after_journal_planned", "after_undo_snapshot", "mid_pool_write:0", "mid_pool_write:1", "after_pool", "after_cleanup",
                 "before_manifest", "after_manifest", "after_catalog", "before_verify"]
AFTER_COMMIT = ["after_local_commit", "before_remote"]


def _hook(point_spec, kind):
    point, _, idx = point_spec.partition(":")

    def hook(p, **kw):
        if p == point and (not idx or kw.get("index") == int(idx)):
            raise (RuntimeError("falla inyectada") if kind == "exception" else SimulatedCrash())
    return hook


def _scenario(ctx):
    """PRE (baseline publicado), POST (v2 aplicado limpio) y el estado de partida restaurado al PRE."""
    ctx.publish()
    pre = ctx.digest()
    snap = ctx.tmp / "pre_copy"
    shutil.copytree(ctx.catalog, snap)
    v2(ctx.tenant)
    ctx.publish()
    post = ctx.digest()
    shutil.rmtree(ctx.catalog)
    shutil.copytree(snap, ctx.catalog)
    shutil.rmtree(txn.runtime_dir(ctx.catalog), ignore_errors=True)
    assert ctx.digest() == pre and pre != post
    return pre, post


@pytest.mark.parametrize("point", BEFORE_COMMIT)
def test_an_exception_at_any_apply_point_rolls_back_to_the_previous_state(ctx, monkeypatch, point):
    pre, post = _scenario(ctx)
    monkeypatch.setattr(txn, "FAULT_HOOK", _hook(point, "exception"))
    try:
        res, _ = ctx.publish()
    except RuntimeError:                                                                                       # antes del journal la excepción propaga (nada se aplicó todavía)
        assert point == "after_lock" and ctx.digest() == pre and pending_journals(ctx) == []
        outcome = "propaga (sin journal)"
    else:
        assert (res.status, res.ok) == (txn.ROLLED_BACK, False), point
        outcome = res.status
        assert ctx.digest() == pre and pending_journals(ctx) == [] and [j["state"] for j in ctx.journals()] == [txn.J_ROLLED_BACK]
    state_after = _name(ctx.digest(), pre, post)
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert not list(ctx.catalog.rglob(".tmp-atomic-*"))
    again, _ = ctx.publish()
    assert again.status == txn.COMPLETED and ctx.digest() == post                                              # y reintentar converge al estado de una publicación limpia
    _record(kind="excepción", point=point, result=outcome, state_after=state_after, recovery="rollback en proceso", retry=again.status, retry_state=_name(ctx.digest(), pre, post))


@pytest.mark.parametrize("point", ["after_validate"] + BEFORE_COMMIT)
def test_a_process_crash_at_any_point_is_recovered_to_the_previous_state_and_a_retry_converges(ctx, monkeypatch, point):
    pre, post = _scenario(ctx)
    monkeypatch.setattr(txn, "FAULT_HOOK", _hook(point, "crash"))
    with pytest.raises(SimulatedCrash):
        ctx.publish()
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    crashed = _name(ctx.digest(), pre, post)
    recovered = txn.recover_pending(ctx.catalog)
    assert ctx.digest() == pre, (point, recovered)                                                             # estado previo EXACTO (nunca mixto)
    assert not txn.has_pending(ctx.catalog) and not list(ctx.catalog.rglob(".tmp-atomic-*"))
    again, _ = ctx.publish()
    assert again.status == txn.COMPLETED and ctx.digest() == post
    _record(kind="caída (kill)", point=point, result="el proceso muere", state_after=crashed, recovery=", ".join(r["action"] for r in recovered) or "sin pendientes", recovered_state="PRE", retry=again.status, retry_state="POST")


@pytest.mark.parametrize("point", AFTER_COMMIT)
def test_a_failure_after_the_local_commit_leaves_the_committed_state_and_a_retry_is_a_noop(ctx, monkeypatch, point):
    pre, post = _scenario(ctx)
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    monkeypatch.setattr(txn, "FAULT_HOOK", _hook(point, "crash"))
    with pytest.raises(SimulatedCrash):
        ctx.publish(mirror=True)
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert [j["state"] for j in ctx.journals()][-1] == txn.J_COMMIT_UNCERTAIN and not any(k.endswith("Acme.publication.json") for k in s3.objects)   # P1: comprometido en local, SIN confirmar en Spaces
    recovered = txn.recover_pending(ctx.catalog)
    assert [r["action"] for r in recovered] == ["CONFIRMED"] and ctx.digest() == post                         # la recuperación VERIFICA el remoto y completa hacia adelante
    assert any(k.endswith("Acme.publication.json") for k in s3.objects) and not txn.has_pending(ctx.catalog)
    again, _ = ctx.publish(mirror=True)
    assert again.status == txn.COMPLETED and again.applied["changed"] is False and again.outcome == "NO_CHANGE" and ctx.digest() == post
    _record(kind="caída (kill)", point=point, result="el proceso muere", state_after="POST local, sin confirmar", recovery="CONFIRMED (verificó el remoto y completó)", recovered_state="POST", retry=again.status + " (NO_CHANGE)", retry_state="POST")


def test_catalog_generator_failure_rolls_back_pool_manifest_and_catalog(ctx, monkeypatch):
    pre, post = _scenario(ctx)
    real_regenerate = dsp.regenerate_catalog_from_pool
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla del generador")))
    res, _ = ctx.publish()
    assert (res.status, res.error_code, res.stages["catalog"]) == (txn.ROLLED_BACK, "CATALOG_GENERATION_FAILED", "FAILED") and ctx.digest() == pre
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", real_regenerate)
    assert ctx.publish()[0].status == txn.COMPLETED and ctx.digest() == post


def test_a_failed_verification_rolls_back(ctx, monkeypatch):
    pre, post = _scenario(ctx)
    monkeypatch.setattr(dsp, "_verify_applied", lambda *a, **k: ["objeto views/3VF_LP"])
    res, _ = ctx.publish()
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "VERIFY_FAILED") and ctx.digest() == pre


def test_results_journals_and_undo_never_contain_secrets(ctx, monkeypatch):
    s3 = FakeS3(fail_after=1)
    enable_remote(monkeypatch, s3)
    res, lines = ctx.publish(mirror=True)
    blobs = [json.dumps(res.__dict__, default=str), "\n".join(lines)] + [p.read_text(encoding="utf-8", errors="ignore") for p in txn.runtime_dir(ctx.catalog).rglob("*.json")]
    blobs += [p.read_text(encoding="utf-8", errors="ignore") for p in ctx.catalog.rglob("lineage-manifest.json")]
    assert not any("not-a-real-secret" in b for b in blobs)
