"""P1 · reimportación del catálogo y regeneración consistente del assessment — pruebas de la UNIDAD con el MOTOR REAL del assessment.

Frontera simulada: el tenant de Datasphere y el bucket. El exportador, los manifiestos, el generador del Catálogo, el almacén gobernado del assessment, ``remote_store`` y el motor de
assessment son los REALES. Todo bajo ``tmp_path``: sin red, sin datos reales, sin Spaces.

Criterio fijado: AFECTADO ⇒ se regenera ANTES de activar; NO afectado con independencia demostrada ⇒ conserva su vigencia; dependencias INCIERTAS ⇒ STALE explícito y trazable; una falla en
cualquier etapa (extracción, generación, escritura, activación, confirmación) deja la última versión válida; una versión nueva solo es vigente con Spaces confirmado; sin versiones
mezcladas ni diagnósticos obsoletos en la versión activa.
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from core import publication_gate as gate
from tests.publication_fakes import CLIENT, SPACE, FakeS3, SimulatedCrash, analytic_model, enable_remote, local_table, seed_pricing, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import catalog_service as cs
from tools.catalog import publication_ledger as ledger_mod
from tools.catalog import publication_txn as txn
from webapp.services import assessment_service as assess

AM_A, AM_B = "AM_Lista_Precios", "AM_Otro"


@pytest.fixture()
def world(ctx, monkeypatch, tmp_path):                                                                     # noqa: F811
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate.set_resolver(None)
    ctx.governed = tmp_path / "governed"
    yield ctx
    gate.clear(ctx.catalog)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()


def pub(w, root=AM_A, **kw):
    return w.publish(root, stage=True, **kw)


def entry_of(w, am):
    return next(e for e in cs.load_catalog(w.catalog / CLIENT) if am in {str(a) for a in (e.artefactos or [])})


def env_of(am):
    return assess._load_envelope(CLIENT, am)


def env_bytes(w, am):
    p = assess.governed_path(CLIENT, am)
    return p.read_bytes() if p is not None and p.is_file() else None


def governed_tree(w):
    return {p.relative_to(w.governed).as_posix(): p.read_bytes() for p in sorted(w.governed.rglob("*")) if p.is_file()} if w.governed.exists() else {}


def closure_of(am):
    return set(env_of(am)["profile_json"]["scope"]["closure_artifacts"])


def v2_exclusive(t):
    """Cambio realista que solo toca la cadena de AM_Lista_Precios: modifica una vista, quita una dependencia y agrega otra."""
    t.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    t.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    t.drop(SPACE, "views", "1VR_LP_EXTRA")


def both(w):
    assert pub(w, AM_A)[0].ok and pub(w, AM_B)[0].ok


# ═══════════════ primera publicación ═══════════════
def test_first_publication_creates_the_catalog_and_its_assessment_as_one_unit(world):
    res, _ = pub(world)
    assert (res.status, res.ok, res.outcome) == (txn.COMPLETED, True, "CHANGED") and res.stages["assessment"] == "OK"
    env = env_of(AM_A)
    assert env["status"] == "OK" and env["input_closure_signature"] and env["profile_json"]["assessment_state"] == "ASSESSED"
    assert assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT
    assert res.applied["assessment"]["regenerated"] == [AM_A]
    _s, data = ledger_mod.load(world.catalog, CLIENT)
    assert data["active"]["assessment"]["regenerated"] == [AM_A] and any(k.startswith("governed-assessments/") for k in data["active"]["files"])
    assert ledger_mod.verify(data, ledger_mod.unit_files(world.catalog, world.pool, CLIENT, assess.governed_client_dir(CLIENT))) == []


# ═══════════════ reimportación con cambios reales: se regenera y refleja el cambio ═══════════════
def test_a_real_change_regenerates_the_affected_assessment_and_drops_obsolete_references(world):
    pub(world)
    before = env_of(AM_A)
    assert "1VR_LP_EXTRA" in closure_of(AM_A) and "1VR_LP_EXTRA" in json.dumps(before["profile_json"])
    v2_exclusive(world.tenant)
    res, _ = pub(world)
    assert res.status == txn.COMPLETED and res.applied["assessment"]["regenerated"] == [AM_A]
    after = env_of(AM_A)
    assert after["assessment_artifact_signature"] != before["assessment_artifact_signature"] and after["input_pool_signature"] != before["input_pool_signature"]
    assert "1VR_LP_EXTRA" not in closure_of(AM_A) and "1VR_LP_EXTRA" not in json.dumps(after["profile_json"])      # sin referencias obsoletas en la versión activa
    assert assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT
    assert len(list(assess.governed_path(CLIENT, AM_A).parent.glob(f"{AM_A}__*.json"))) == 1                       # UNA sola versión activa: la anterior fue REEMPLAZADA, no sumada


def test_the_new_assessment_is_computed_only_from_the_new_inputs(world, monkeypatch):
    pub(world)
    v2_exclusive(world.tenant)
    seen = []
    real = assess._engine_assess

    def spy(root, client_dir, am, domain_dir, *a, **k):
        seen.append(sorted(p.name for p in (Path(root) / client_dir).rglob("*.json")))
        return real(root, client_dir, am, domain_dir, *a, **k)

    monkeypatch.setattr(assess, "_engine_assess", spy)
    assert pub(world)[0].ok
    assert seen and all("1VR_LP_EXTRA.json" not in names for names in seen)                                         # el motor corrió sobre el pool NUEVO (ya sin la vista retirada)


# ═══════════════ reimportación idéntica ═══════════════
def test_an_identical_reimport_rewrites_neither_the_catalog_nor_any_assessment(world):
    pub(world)
    tree, governed = {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()}, governed_tree(world)
    mtimes = {p: p.stat().st_mtime_ns for p in list(world.catalog.rglob("*")) + list(world.governed.rglob("*")) if p.is_file()}
    res, _ = pub(world)
    assert (res.outcome, res.applied["changed"], res.stages["assessment"]) == ("NO_CHANGE", False, "SKIPPED")
    assert {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()} == tree and governed_tree(world) == governed
    assert {p: p.stat().st_mtime_ns for p in mtimes} == mtimes


def test_a_missing_assessment_is_recreated_by_an_otherwise_identical_reimport(world):
    pub(world)
    assess.governed_path(CLIENT, AM_A).unlink()
    assess.reset_memory_cache()
    res, _ = pub(world)
    assert res.outcome == "CHANGED" and res.applied["assessment"]["regenerated"] == [AM_A] and assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT


# ═══════════════ regeneración selectiva: afectado / independiente / incierto ═══════════════
def test_an_unaffected_assessment_keeps_its_currentness_without_being_regenerated(world):
    both(world)
    b_bytes = env_bytes(world, AM_B)
    v2_exclusive(world.tenant)                                                                                      # toca SOLO la cadena exclusiva de AM_Lista_Precios
    res, _ = pub(world, AM_A)
    summary = res.applied["assessment"]
    assert summary["regenerated"] == [AM_A] and AM_B in summary["kept"] and not summary["uncertain"]
    assert env_bytes(world, AM_B) == b_bytes                                                                        # ni un byte distinto: NO se regeneró
    st = assess.status_for_entry(entry_of(world, AM_B))
    assert st.state == assess.CURRENT and st.pool_drift                                                             # vigente aunque la firma del pool completo cambió, y trazable


def test_a_shared_dependency_change_regenerates_every_assessment_that_reads_it(world):
    both(world)
    world.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "D_NUEVA")))
    res, _ = pub(world, AM_A)
    assert sorted(res.applied["assessment"]["regenerated"]) == [AM_A, AM_B]
    for am in (AM_A, AM_B):
        assert assess.status_for_entry(entry_of(world, am)).state == assess.CURRENT and not assess.status_for_entry(entry_of(world, am)).pool_drift


def test_an_unaffected_legacy_assessment_without_a_dependency_signature_is_rebased_not_regenerated(world):
    both(world)
    path = assess.governed_path(CLIENT, AM_B)
    legacy = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k not in ("input_closure_signature", "dependency_rule_version")}
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")                                      # sobre previo a esta firma (todos los de producción hoy)
    assess.reset_memory_cache()
    v2_exclusive(world.tenant)
    res, _ = pub(world, AM_A)
    assert AM_B in res.applied["assessment"]["rebased"] and AM_B not in res.applied["assessment"]["regenerated"]
    rebased = json.loads(path.read_text(encoding="utf-8"))
    assert rebased["input_closure_signature"] and rebased["profile_json"] == legacy["profile_json"] and rebased["generated_at"] == legacy["generated_at"]
    assert assess.status_for_entry(entry_of(world, AM_B)).state == assess.CURRENT


def test_uncertain_dependencies_leave_an_explicit_traceable_stale_instead_of_a_guess(world, monkeypatch):
    both(world)
    path = assess.governed_path(CLIENT, AM_B)
    legacy = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k not in ("input_closure_signature", "dependency_rule_version")}
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    assess.reset_memory_cache()
    v2_exclusive(world.tenant)
    real = assess.ma_dependencies.dependency_signatures
    monkeypatch.setattr(assess.ma_dependencies, "dependency_signatures", lambda *a, **k: {am: None for am in real(*a, **k)})   # las dependencias no pueden calcularse
    assess.invalidate_pool_index()
    res, _ = pub(world, AM_A)
    assert res.status == txn.COMPLETED and AM_B in res.applied["assessment"]["uncertain"]
    st = assess.status_for_entry(entry_of(world, AM_B))
    assert st.state == assess.STALE and st.stale_dependency == assess.DEP_UNCERTAIN
    _s, data = ledger_mod.load(world.catalog, CLIENT)
    assert AM_B in data["active"]["assessment"]["uncertain"]                                                        # trazable en la bitácora


# ═══════════════ modelos retirados: sin diagnósticos obsoletos ═══════════════
def test_the_governed_assessment_of_a_model_that_left_the_pool_is_removed_with_the_unit(world):
    both(world)
    for rel in ("analytic-models/AM_Otro", "views/3VF_OT", "local-tables/RAW_OT"):
        (world.pool / CLIENT / f"{rel}.json").unlink()
    assert assess.governed_path(CLIENT, AM_B).is_file()
    v2_exclusive(world.tenant)
    res, _ = pub(world, AM_A)
    assert res.status == txn.COMPLETED and res.applied["assessment"]["removed"] == [AM_B]
    assert not assess.governed_path(CLIENT, AM_B).exists() and assess.governed_path(CLIENT, AM_A).is_file()


def test_nothing_is_removed_when_the_pool_cannot_be_resolved_unambiguously(world):
    both(world)
    other = world.pool / CLIENT / "views" / "COLISION.json"
    other.write_text(json.dumps(view("3VF_LP", ["RAW_PRECIOS"], cols=("Z",))), encoding="utf-8")                  # misma entidad con contenido distinto: el pool no carga
    v2_exclusive(world.tenant)
    res, _ = pub(world, AM_A)
    assert res.status in (txn.ROLLED_BACK, txn.COMPLETED)
    assert assess.governed_path(CLIENT, AM_B).is_file()                                                              # jamás se borra un diagnóstico por una falla de carga


# ═══════════════ fallas por etapa: se conserva la última versión válida ═══════════════
def _fail_engine_for(monkeypatch, am):
    real = assess._engine_assess

    def boom(root, client_dir, target, *a, **k):
        if target == am:
            raise RuntimeError("fallo del motor (inyectado)")
        return real(root, client_dir, target, *a, **k)
    monkeypatch.setattr(assess, "_engine_assess", boom)
    return real


def test_an_assessment_generation_failure_rolls_back_everything_and_keeps_the_last_valid_version(world, monkeypatch):
    pub(world)
    pre_local, pre_governed = {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()}, governed_tree(world)
    v2_exclusive(world.tenant)
    real = _fail_engine_for(monkeypatch, AM_A)
    res, _ = pub(world)
    assert (res.status, res.error_code, res.ok) == (txn.ROLLED_BACK, "ASSESSMENT_FAILED", False)
    assert res.failures == [{"kind": "assessment", "object_type": "", "technical_name": AM_A, "code": "GENERATION_ERROR"}]
    assert {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()} == pre_local and governed_tree(world) == pre_governed   # pool, Catálogo, bitácora y assessments: la versión previa EXACTA
    assert assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT                                  # y sigue vigente la última versión válida
    assert gate.read_state(world.catalog) is None and not txn.has_pending(world.catalog)
    monkeypatch.setattr(assess, "_engine_assess", real)
    assert pub(world)[0].status == txn.COMPLETED                                                                    # el reintento converge


def test_an_assessment_write_failure_rolls_back_the_unit(world, monkeypatch):
    pub(world)
    pre_local, pre_governed = {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()}, governed_tree(world)
    v2_exclusive(world.tenant)
    monkeypatch.setattr(assess, "commit_prepared_local", lambda prep: assess.DURABLE_MEMORY)                       # el disco no es escribible
    res, _ = pub(world)
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "ASSESSMENT_WRITE_FAILED")
    assert {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()} == pre_local and governed_tree(world) == pre_governed


@pytest.mark.parametrize("point", ["after_assessment_prepare", "after_assessment_commit", "before_verify", "after_ledger"])
def test_an_exception_during_activation_restores_the_whole_unit(world, monkeypatch, point):
    pub(world)
    pre_local, pre_governed = {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()}, governed_tree(world)
    v2_exclusive(world.tenant)
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **kw: (_ for _ in ()).throw(RuntimeError("falla inyectada")) if p == point else None)
    res, _ = pub(world)
    assert res.status == txn.ROLLED_BACK
    assert {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()} == pre_local and governed_tree(world) == pre_governed
    assert assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT and gate.read_state(world.catalog) is None
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert pub(world)[0].status == txn.COMPLETED


@pytest.mark.parametrize("point", ["after_journal_planned", "after_undo_snapshot", "after_catalog", "after_assessment_prepare", "after_assessment_commit", "after_ledger"])
def test_a_process_crash_is_recovered_to_the_previous_version_of_catalog_and_assessments(world, monkeypatch, point):
    pub(world)
    pre_local, pre_governed = {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()}, governed_tree(world)
    v2_exclusive(world.tenant)
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **kw: (_ for _ in ()).throw(SimulatedCrash()) if p == point else None)
    with pytest.raises(SimulatedCrash):
        pub(world)
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    pending = [j for j in world.journals() if j["state"] in (txn.J_PLANNED, txn.J_APPLYING)]
    assert len(pending) == 1
    assert gate.read_state(world.catalog)["state"] == gate.ACTIVATING                                               # el proceso murió con la compuerta CERRADA: los lectores no ven la unidad a medias
    recovered = txn.recover_pending(world.catalog)
    assert recovered and gate.read_state(world.catalog) is None                                                     # la recuperación deshizo y abrió la compuerta
    assess.reset_memory_cache()
    assert {p: p.read_bytes() for p in world.catalog.rglob("*") if p.is_file()} == pre_local and governed_tree(world) == pre_governed
    assert assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT
    assert pub(world)[0].status == txn.COMPLETED and assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT


# ═══════════════ concurrencia ═══════════════
def test_two_republications_of_the_same_scope_never_interleave(world, monkeypatch):
    pub(world)
    v2_exclusive(world.tenant)
    entered, release = threading.Event(), threading.Event()
    results: dict = {}

    def hook(point, **kw):
        if point == "after_assessment_prepare" and not entered.is_set():
            entered.set()
            assert release.wait(20)

    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    first = threading.Thread(target=lambda: results.setdefault("a", pub(world)[0]))
    first.start()
    assert entered.wait(20)
    monkeypatch.setattr(txn, "_LOCK_WAIT_OVERRIDE", None, raising=False)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "_SAFE_LOCK_TIMEOUT_S", 0.2)
    second, _ = pub(world)                                                                                          # mientras la primera prepara, la segunda NO entra
    release.set()
    first.join(30)
    assert (second.status, second.error_code) == (txn.FAILED_NO_CHANGE, "CATALOG_BUSY") and results["a"].status == txn.COMPLETED
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    again, _ = pub(world)
    assert again.outcome == "NO_CHANGE" and assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT   # y la repetición queda idempotente: una sola versión final


# ═══════════════ aislamiento entre clientes y modelos ═══════════════
def test_publishing_one_client_never_touches_another_clients_catalog_assessments_or_ledger(world):
    pub(world)
    other = world.tenant.spaces[SPACE]
    assert other                                                                                                    # (el mismo tenant sirve a ambos clientes)
    assert pub(world, AM_A, client="Beta")[0].ok
    beta_before = {p.relative_to(world.governed).as_posix(): p.read_bytes() for p in (world.governed / "v1" / "Beta").rglob("*") if p.is_file()}
    acme_before = {p.relative_to(world.governed).as_posix(): p.read_bytes() for p in (world.governed / "v1" / CLIENT).rglob("*") if p.is_file()}
    ledgers_before = {c: ledger_mod.ledger_path(world.catalog, c).read_bytes() for c in ("Beta", CLIENT)}
    v2_exclusive(world.tenant)
    res, _ = pub(world, AM_A, client="Beta")
    assert res.status == txn.COMPLETED
    assert {p.relative_to(world.governed).as_posix(): p.read_bytes() for p in (world.governed / "v1" / CLIENT).rglob("*") if p.is_file()} == acme_before
    assert ledger_mod.ledger_path(world.catalog, CLIENT).read_bytes() == ledgers_before[CLIENT]
    assert {p.relative_to(world.governed).as_posix(): p.read_bytes() for p in (world.governed / "v1" / "Beta").rglob("*") if p.is_file()} != beta_before


# ═══════════════ confirmación durable de los assessments junto con el Catálogo ═══════════════
def test_the_governed_assessments_are_confirmed_in_spaces_together_with_the_catalog_before_the_marker(world, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3, governed=world.governed)
    res, _ = pub(world, mirror=True)
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_REMOTE
    env_keys = [k for k in s3.objects if k.startswith("audit/governed-assessments/")]
    assert len(env_keys) == 1 and env_keys[0].endswith(".json")
    puts = [k for o, k in s3.log if o == "put"]
    assert puts.index(env_keys[0]) < max(i for i, k in enumerate(puts) if k.endswith("Acme.publication.json")) and puts[-1].endswith(("Acme.publication.json", ".catalog_manifest.json"))
    assert s3.objects[env_keys[0]] == assess.governed_path(CLIENT, AM_A).read_bytes()


def test_a_remote_failure_restores_the_previous_assessment_in_local_and_in_spaces(world, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3, governed=world.governed)
    pub(world, mirror=True)
    pre_governed, pre_remote = governed_tree(world), dict(s3.objects)
    v2_exclusive(world.tenant)
    s3.fail_keys = ("governed-assessments/",)                                                                       # el assessment NO se puede subir
    res, _ = pub(world, mirror=True)
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "PERSISTENCE_FAILED")
    assert governed_tree(world) == pre_governed and s3.objects == pre_remote
    assert assess.status_for_entry(entry_of(world, AM_A)).state == assess.CURRENT


def test_a_retired_model_assessment_is_also_removed_from_spaces(world, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3, governed=world.governed)
    assert pub(world, AM_A, mirror=True)[0].ok and pub(world, AM_B, mirror=True)[0].ok
    b_key = next(k for k in s3.objects if k.startswith("audit/governed-assessments/") and AM_B in k)
    for rel in ("analytic-models/AM_Otro", "views/3VF_OT", "local-tables/RAW_OT"):
        (world.pool / CLIENT / f"{rel}.json").unlink()
    v2_exclusive(world.tenant)
    res, _ = pub(world, AM_A, mirror=True)
    assert res.status == txn.COMPLETED and b_key not in s3.objects                                                  # no resucita en un restore


# ═══════════════ lectores: nadie ve una versión a medias ═══════════════
def test_assessment_readers_wait_for_the_activation_and_then_see_only_the_new_version(world, monkeypatch):
    pub(world)
    entry = entry_of(world, AM_A)
    v2_exclusive(world.tenant)
    seen: dict = {}

    def probe():
        out: list = []
        t = threading.Thread(target=lambda: out.append(_catch(lambda: assess.status_for_entry(entry))))
        t.start()
        t.join(10)
        seen["reader"] = out[0] if out else None

    monkeypatch.setattr(gate, "DEFAULT_WAIT_S", 0.2)
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **kw: probe() if p == "after_assessment_commit" else None)
    res, _ = pub(world)
    assert res.status == txn.COMPLETED and isinstance(seen["reader"], gate.PublicationInProgress)                   # a mitad de la activación el lector NO obtiene un estado
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    st = assess.status_for_entry(entry_of(world, AM_A))
    assert st.state == assess.CURRENT and "1VR_LP_EXTRA" not in json.dumps(st.profile)


# ═══════════════ aceptación final: modificar un catálogo existente y verificar que el assessment nuevo representa ese cambio ═══════════════
def test_final_acceptance_a_modified_existing_catalog_yields_an_assessment_that_represents_the_change(world):
    first, _ = pub(world)
    entry = entry_of(world, AM_A)
    old = assess.status_for_entry(entry)
    assert old.state == assess.CURRENT and {"1VR_LP_BASE", "1VR_LP_EXTRA", "3VF_LP"} <= closure_of(AM_A) and "3VF_NUEVA" not in closure_of(AM_A)
    # el modelo cambia en el tenant: se agrega una capa nueva (3VF_NUEVA) entre el AM y su fuente, se retira una vista y se modifica otra
    world.tenant.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    world.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    world.tenant.put(SPACE, "views", "3VF_NUEVA", view("3VF_NUEVA", ["3VF_LP"]))
    world.tenant.put(SPACE, "analytic-models", AM_A, analytic_model(AM_A, "3VF_NUEVA", ["1VR_DIM_PROD"], label="Modelo Analítico - Precios"))
    world.tenant.drop(SPACE, "views", "1VR_LP_EXTRA")
    second, _ = pub(world)
    assert second.status == txn.COMPLETED and second.outcome == "CHANGED"
    new = assess.status_for_entry(entry_of(world, AM_A))
    assert new.state == assess.CURRENT and not new.pool_drift
    assert {"3VF_NUEVA", "1VR_LP_BASE", "3VF_LP"} <= closure_of(AM_A) and "1VR_LP_EXTRA" not in closure_of(AM_A)       # el assessment NUEVO representa el cambio
    assert set(new.profile["scope"]["closure_artifacts"]) != set(old.profile["scope"]["closure_artifacts"]) and new.profile["scope"]["closure_size"] == len(closure_of(AM_A))
    assert new.profile != old.profile and env_of(AM_A)["assessment_artifact_signature"] != assess._artifact_signature(old.profile)
    # el Catálogo materializado y el assessment son la MISMA versión (nada mezclado)
    flows = [e for e in cs.load_catalog(world.catalog / CLIENT) if AM_A in {str(a) for a in (e.artefactos or [])}]
    assert flows and all("3VF_NUEVA" in {str(a) for a in e.artefactos} and "1VR_LP_EXTRA" not in {str(a) for a in e.artefactos} for e in flows)
    _s, data = ledger_mod.load(world.catalog, CLIENT)
    assert data["active"]["operation_id"] == second.operation_id and data["active"]["previous_operation_id"] == first.operation_id
    assert ledger_mod.verify(data, ledger_mod.unit_files(world.catalog, world.pool, CLIENT, assess.governed_client_dir(CLIENT))) == []


def _catch(fn):
    try:
        return fn()
    except Exception as exc:                                                                                        # noqa: BLE001
        return exc
