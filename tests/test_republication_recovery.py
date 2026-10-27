"""P1 · B1 — recuperación de una publicación interrumpida con Spaces PARCIALMENTE actualizado, incluso con el filesystem local perdido.

Frontera simulada: el tenant de Datasphere y el bucket (``FakeS3``: fallos, respuestas perdidas y MUERTES del proceso inyectables). Exportador, ``remote_store`` (incluido su ``restore`` real),
el registro de intención, la compuerta de lectores y el motor de assessment son los REALES. Todo bajo ``tmp_path``: jamás Spaces de producción.

Contrato que fija este archivo (criterio del dueño):
  * la última versión confirmada se identifica sin ambigüedad (marcador de activación + registro de intención + bitácora);
  * jamás se sirve un Catálogo o un assessment HÍBRIDO: el disco coincide byte a byte con Spaces y con los digests de la versión activa;
  * versión nueva COMPLETA en Spaces → se recupera como vigente; no confirmada → se recupera la anterior;
  * estado remoto imposible de establecer → la lectura queda BLOQUEADA con un código explícito, sin modificar nada;
  * la recuperación es idempotente, también ante otra interrupción (incluso otro reinicio con pérdida de disco en mitad de la recuperación).
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from core import publication_gate as gate
from core import remote_store
from tests.publication_fakes import CLIENT, SPACE, FakeS3, SimulatedCrash, enable_remote, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import catalog_service as cs
from tools.catalog import publication_intent as pi
from tools.catalog import publication_ledger as ledger_mod
from tools.catalog import publication_txn as txn
from webapp.services import assessment_service as assess
from webapp.services import datasphere_publication_service as svc

AM_A = "AM_Lista_Precios"
LEDGER_NAME = "Acme.publication.json"
LEDGER_KEY = "catalog_local/.publication_state/" + LEDGER_NAME
UNIT_PREFIXES = ("catalog_local/.csn_cache/Acme/", "catalog_local/.lineage_runs/Acme/", "catalog_local/Acme/", "governed-assessments/v1/Acme/")
UNIT_EXACT = ("catalog_local/.catalog_manifest.json", LEDGER_KEY)


@pytest.fixture()
def world(ctx, monkeypatch, tmp_path):                                                                     # noqa: F811
    s3 = FakeS3()
    governed = tmp_path / "governed"
    enable_remote(monkeypatch, s3, governed=governed)
    monkeypatch.setenv(txn.SAFE_ENV, "1")
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate.set_resolver(None)
    ctx.s3, ctx.governed = s3, governed
    yield ctx
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    gate.clear(ctx.catalog)
    gate.set_resolver(None)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()


def pub(w, root=AM_A, **kw):
    kw.setdefault("mirror", True)
    return w.publish(root, stage=True, **kw)


def v2(t):
    """Cambio realista de la cadena de AM_Lista_Precios: modifica una vista, quita una dependencia y agrega otra."""
    t.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    t.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    t.drop(SPACE, "views", "1VR_LP_EXTRA")


def entry_of(w, am=AM_A):
    return next(e for e in cs.load_catalog(w.catalog / CLIENT) if am in {str(a) for a in (e.artefactos or [])})


# ═══════════════ estado: lo que hay en disco y lo que hay en Spaces, solo de la unidad del cliente ═══════════════
def governed_client(w) -> Path:
    """La carpeta gobernada del cliente SIN pasar por la compuerta de lectores (el test inspecciona el disco aunque la lectura esté bloqueada)."""
    return w.governed / assess.GOVERNED_LAYOUT_VERSION / CLIENT


def local_unit(w) -> dict[str, bytes]:
    files = ledger_mod.unit_files(w.catalog, w.pool, CLIENT, governed_client(w))
    ledger = ledger_mod.ledger_path(w.catalog, CLIENT)
    if ledger.exists():
        files[LEDGER_KEY] = ledger
    return {k: p.read_bytes() for k, p in files.items() if ".intent/" not in k}


def remote_unit(w) -> dict[str, bytes]:
    out = {}
    for key, data in w.s3.objects.items():
        rel = key[len("audit/"):]
        if rel.startswith(UNIT_PREFIXES) or rel in UNIT_EXACT:
            out[rel] = data
    return out


def intents_anywhere(w) -> list[str]:
    return [k for k in w.s3.objects if ".intent/" in k] + [p.as_posix() for p in w.catalog.rglob("*") if ".intent" in p.as_posix() and p.is_file()]


def assert_not_hybrid(w) -> str:
    """El disco ES UNA versión publicada y Spaces tiene exactamente lo mismo; la compuerta está abierta y no queda nada a medias. Devuelve la operación activa."""
    local, remote = local_unit(w), remote_unit(w)
    assert local == remote, sorted(set(local) ^ set(remote)) or [k for k in local if local[k] != remote[k]]          # byte a byte, mismo conjunto de claves
    state, ledger = ledger_mod.load(w.catalog, CLIENT)
    assert state == ledger_mod.L_OK
    files = ledger_mod.unit_files(w.catalog, w.pool, CLIENT, governed_client(w))
    assert ledger_mod.verify(ledger, files) == []                                                                       # los digests de la versión activa coinciden con TODO el disco
    assert intents_anywhere(w) == [] and gate.read_state(w.catalog) is None and not txn.has_pending(w.catalog)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    assert assess.status_for_entry(entry_of(w)).state == assess.CURRENT                                                 # el assessment corresponde al Catálogo vigente (ni uno nuevo con otro viejo)
    return ledger["active"]["operation_id"]


# ═══════════════ procesos: caída, reinicio y pérdida del filesystem ═══════════════
def wipe_fs(w) -> None:
    """Redeploy de App Platform: filesystem efímero nuevo (sin catálogo, sin assessments, sin journal ni undo ni compuerta)."""
    shutil.rmtree(w.catalog, ignore_errors=True)
    shutil.rmtree(w.governed, ignore_errors=True)
    shutil.rmtree(txn.runtime_dir(w.catalog), ignore_errors=True)
    w.catalog.mkdir(parents=True, exist_ok=True)


def new_process(monkeypatch) -> None:
    for name, empty in (("_signatures", {}), ("_digests", {}), ("_unreconciled", set())):
        monkeypatch.setattr(remote_store, name, empty, raising=False)
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable", raising=False)
    monkeypatch.setattr(remote_store, "_restore_degraded", False, raising=False)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate._owner.op = None                                                                                               # un proceso nuevo no hereda al «dueño» de la operación que murió


def restart(w, monkeypatch, *, lose_fs: bool) -> None:
    """Lo que hace el arranque real: proceso nuevo → compuerta de arranque cerrada → restore desde Spaces → recuperación."""
    if lose_fs:
        wipe_fs(w)
    new_process(monkeypatch)
    assert svc.begin_boot_gate() is True
    remote_store.restore()
    svc.boot_recover()


def arm_fault(monkeypatch, point):
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **kw: (_ for _ in ()).throw(SimulatedCrash(p)) if p == point else None)


def run_until_crash(w):
    with pytest.raises(SimulatedCrash):
        pub(w)
    gate._owner.op = None                                                                                               # el proceso murió: el hilo de la prueba no sigue siendo el «dueño» de esa operación


def blocked_reason(w) -> str:
    with pytest.raises(gate.PublicationBlocked) as exc:
        gate.check(w.catalog, wait=0)
    return exc.value.reason


# ═══════════════ matriz: interrupción antes y después de cada etapa crítica × disco conservado / perdido ═══════════════
# (id, tipo, argumento, versión esperada con el disco conservado, versión esperada con el disco perdido)
#   disco conservado: el journal y el undo siguen ahí, el local es la versión nueva completa → se completa hacia adelante (verificando).
#   disco perdido:    solo queda Spaces → cuerpo nuevo COMPLETO = versión nueva; cualquier otra mezcla = la versión anterior.
CASES = [
    ("fault-after_local_commit", "fault", "after_local_commit", "V2", "V1"),            # aplicado en local, ni siquiera se registró la intención: Spaces intacta
    ("fault-before_remote", "fault", "before_remote", "V2", "V1"),
    ("fault-after_intent", "fault", "after_intent", "V2", "V1"),                        # intención registrada, ningún objeto del cuerpo escrito todavía
    ("s3-before-a-body-object", "before", "3VF_LP.json", "V2", "V1"),                   # el cuerpo quedó A MEDIAS: mezcla de versión nueva y previa
    ("s3-before-the-activation-marker", "before", LEDGER_NAME, "V2", "V2"),             # cuerpo completo, falta el marcador de activación
    ("s3-after-the-activation-marker", "after", LEDGER_NAME, "V2", "V2"),               # marcador escrito, la respuesta nunca llegó
    ("fault-after_remote", "fault", "after_remote", "V2", "V2"),                        # Spaces confirmó todo, el journal aún no lo sabe
    ("fault-after_confirm_journal", "fault", "after_confirm_journal", "V2", "V2"),
    ("fault-before_intent_close", "fault", "before_intent_close", "V2", "V2"),
    ("fault-after_intent_close", "fault", "after_intent_close", "V2", "V2"),            # todo hecho; solo la compuerta quedó cerrada por el proceso muerto
]


def arm(w, monkeypatch, kind, arg):
    if kind == "fault":
        arm_fault(monkeypatch, arg)
    elif kind == "before":
        w.s3.crash_before_keys = (arg,)
    else:
        w.s3.crash_after_keys = (arg,)


@pytest.mark.parametrize("lose_fs", [False, True], ids=["disco-conservado", "disco-perdido"])
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_an_interrupted_confirmation_always_recovers_one_whole_version_never_a_hybrid(world, monkeypatch, case, lose_fs):
    _id, kind, arg, expect_kept, expect_lost = case
    first, _ = pub(world)
    assert first.status == txn.COMPLETED
    op1 = first.operation_id
    v2(world.tenant)
    arm(world, monkeypatch, kind, arg)
    run_until_crash(world)
    world.s3.crash_before_keys = world.s3.crash_after_keys = ()
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert gate.read_state(world.catalog) is not None                                                                   # el proceso murió con la compuerta CERRADA: ningún lector ve la unidad a medias

    restart(world, monkeypatch, lose_fs=lose_fs)

    op = assert_not_hybrid(world)
    assert (op == op1) == ((expect_lost if lose_fs else expect_kept) == "V1")
    after = (local_unit(world), remote_unit(world))
    restart(world, monkeypatch, lose_fs=False)                                                                          # otro reinicio: idempotente, no cambia nada
    assert (local_unit(world), remote_unit(world)) == after and assert_not_hybrid(world) == op


def test_readers_are_blocked_with_an_explicit_code_from_the_start_of_the_boot_until_the_state_is_verified(world, monkeypatch):
    pub(world)
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    wipe_fs(world)
    new_process(monkeypatch)
    assert svc.begin_boot_gate()
    assert blocked_reason(world) == "BOOT_VERIFICATION_PENDING"                                                         # el restore aún no empezó: nada del Catálogo es visible
    with pytest.raises(gate.PublicationBlocked):
        assess.pool_root()
    remote_store.restore()
    assert blocked_reason(world) == "BOOT_VERIFICATION_PENDING"                                                         # restaurado, pero todavía sin verificar: mezcla posible, no se sirve
    svc.boot_recover()
    gate.check(world.catalog, wait=0)                                                                                   # verificado y resuelto: se abre


def test_a_complete_new_body_with_only_the_old_marker_is_recovered_as_the_new_version_and_the_marker_is_completed(world, monkeypatch):
    op1 = pub(world)[0].operation_id
    v2(world.tenant)
    world.s3.crash_before_keys = (LEDGER_NAME,)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    old_marker = world.s3.objects["audit/" + LEDGER_KEY]
    restart(world, monkeypatch, lose_fs=True)
    assert assert_not_hybrid(world) != op1 and world.s3.objects["audit/" + LEDGER_KEY] != old_marker
    state, ledger = ledger_mod.load(world.catalog, CLIENT)
    assert ledger["active"]["previous_operation_id"] == op1                                                              # trazabilidad: versión previa → nueva


def test_a_partially_uploaded_body_is_rolled_back_to_the_previous_version_in_spaces_and_locally(world, monkeypatch):
    op1 = pub(world)[0].operation_id
    confirmed_remote, confirmed_local = remote_unit(world), local_unit(world)
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    assert remote_unit(world) != confirmed_remote                                                                        # Spaces SÍ quedó mezclada
    restart(world, monkeypatch, lose_fs=True)
    assert assert_not_hybrid(world) == op1
    assert remote_unit(world) == confirmed_remote and local_unit(world) == confirmed_local                              # NI UN byte distinto de la última versión confirmada, en ambos lados


# ═══════════════ marcador de activación: antiguo, nuevo, ausente, ilegible ═══════════════
def test_an_absent_marker_with_a_partial_first_publication_recovers_to_nothing_published_and_can_be_republished(world, monkeypatch):
    world.s3.crash_before_keys = ("3VF_LP.json",)
    with pytest.raises(SimulatedCrash):
        pub(world)
    world.s3.crash_before_keys = ()
    assert "audit/" + LEDGER_KEY not in world.s3.objects                                                                  # nunca hubo marcador
    restart(world, monkeypatch, lose_fs=True)
    assert remote_unit(world) == {} and local_unit(world) == {} and intents_anywhere(world) == []                        # no quedó ni un objeto a medias del cliente
    assert gate.read_state(world.catalog) is None and not txn.has_pending(world.catalog)
    again, _ = pub(world)
    assert again.status == txn.COMPLETED
    assert_not_hybrid(world)


def test_an_absent_marker_with_a_complete_first_body_completes_the_first_publication(world, monkeypatch):
    world.s3.crash_before_keys = (LEDGER_NAME,)
    with pytest.raises(SimulatedCrash):
        pub(world)
    world.s3.crash_before_keys = ()
    restart(world, monkeypatch, lose_fs=True)
    assert_not_hybrid(world)
    assert "audit/" + LEDGER_KEY in world.s3.objects


@pytest.mark.parametrize("crash_key,expect_new", [("3VF_LP.json", False), (LEDGER_NAME, True)], ids=["cuerpo-mezclado→previa", "cuerpo-completo→nueva"])
def test_an_unreadable_marker_never_decides_by_itself_the_body_does_and_the_marker_is_rewritten(world, monkeypatch, crash_key, expect_new):
    op1 = pub(world)[0].operation_id
    v2(world.tenant)
    world.s3.crash_before_keys = (crash_key,)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    world.s3.objects["audit/" + LEDGER_KEY] = b'{"schema": "publication-ledger/v1", "active": {truncado'                    # el marcador remoto quedó ilegible
    restart(world, monkeypatch, lose_fs=True)
    op = assert_not_hybrid(world)
    assert (op != op1) == expect_new


def test_a_marker_that_spaces_cannot_return_blocks_reads_with_an_explicit_code_and_changes_nothing_until_it_can(world, monkeypatch):
    op1 = pub(world)[0].operation_id
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    wipe_fs(world)
    new_process(monkeypatch)
    svc.begin_boot_gate()
    remote_store.restore()
    local_before, remote_before = local_unit(world), dict(world.s3.objects)
    world.s3.fail_get_keys = (LEDGER_NAME,)                                                                              # el GET del marcador falla de forma irresoluble
    svc.boot_recover()
    assert blocked_reason(world) == pi.REMOTE_UNREADABLE                                                                 # no se pudo establecer el estado remoto: lectura bloqueada, con causa
    assert local_unit(world) == local_before and world.s3.objects == remote_before                                       # y no se tocó NADA mientras no se pueda verificar
    world.s3.fail_get_keys = ()
    svc.resolve_pending()
    assert assert_not_hybrid(world) == op1


# ═══════════════ fallos de listado y de lectura de Spaces durante la recuperación ═══════════════
def test_a_failing_listing_during_the_boot_restore_keeps_reads_blocked_and_recovers_once_spaces_can_be_listed(world, monkeypatch):
    op1 = pub(world)[0].operation_id
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    world.s3.fail_list = True
    restart(world, monkeypatch, lose_fs=True)
    assert blocked_reason(world) == "RESTORE_INCOMPLETE"                                                                 # restore incompleto: no se puede verificar lo restaurado
    assert local_unit(world) == {}                                                                                       # y no se inventó nada
    world.s3.fail_list = False
    remote_store.restore()                                                                                                # el reintento del restore (lo hace el resolvedor en segundo plano)
    svc.resolve_pending()
    assert assert_not_hybrid(world) == op1


def test_the_resolver_retries_a_failed_boot_restore_without_blocking_the_reader(world, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(remote_store, "restore", lambda overwrite=False: calls.append(1) or 0)
    new_process(monkeypatch)
    monkeypatch.setattr(remote_store, "_restore_state", "failed", raising=False)
    svc.begin_boot_gate()
    t0 = time.monotonic()
    svc.resolve_pending()
    assert time.monotonic() - t0 < 5
    deadline = time.monotonic() + 5
    while svc._RESTORE_RETRY["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == [1]


def test_a_read_failure_on_a_body_object_during_recovery_is_uncertain_not_a_guess(world, monkeypatch):
    op1 = pub(world)[0].operation_id
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    world.s3.fail_get_keys = ("1VR_LP_BASE.json",)
    restart(world, monkeypatch, lose_fs=True)
    assert blocked_reason(world) == pi.REMOTE_UNREADABLE and txn.has_pending(world.catalog)
    world.s3.fail_get_keys = ()
    svc.resolve_pending()
    assert assert_not_hybrid(world) == op1


def test_when_the_remote_is_gone_during_recovery_the_local_is_never_changed_and_reads_stay_blocked(world, monkeypatch):
    pub(world)
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    wipe_fs(world)
    new_process(monkeypatch)
    svc.begin_boot_gate()
    remote_store.restore()
    before = local_unit(world)
    world.s3.down = True
    svc.boot_recover()
    assert blocked_reason(world) == pi.REMOTE_UNREADABLE and local_unit(world) == before
    world.s3.down = False
    svc.resolve_pending()
    assert_not_hybrid(world)


# ═══════════════ limitaciones explícitas: sin evidencia suficiente NO se adivina ═══════════════
def mixed_world_with_lost_fs(w, monkeypatch):
    pub(w)
    v2(w.tenant)
    w.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(w)
    w.s3.crash_before_keys = ()
    wipe_fs(w)
    new_process(monkeypatch)
    svc.begin_boot_gate()
    remote_store.restore()


def test_without_the_previous_image_of_an_overwritten_object_recovery_blocks_explicitly_instead_of_guessing(world, monkeypatch):
    mixed_world_with_lost_fs(world, monkeypatch)
    for key in [k for k in world.s3.objects if "/pre/" in k]:
        del world.s3.objects[key]                                                                                         # la imagen previa se perdió en Spaces…
    for blob in list(world.catalog.rglob("pre/*")):
        blob.unlink()                                                                                                     # …y en el disco restaurado
    local_before, remote_before = local_unit(world), dict(world.s3.objects)
    svc.boot_recover()
    assert blocked_reason(world) == pi.PRE_IMAGE_MISSING
    assert local_unit(world) == local_before and world.s3.objects == remote_before


def test_an_object_that_is_neither_the_previous_nor_the_new_version_blocks_explicitly(world, monkeypatch):
    mixed_world_with_lost_fs(world, monkeypatch)
    victim = next(k for k in world.s3.objects if k.endswith("/1VR_LP_BASE.json") and ".csn_cache" in k)
    world.s3.objects[victim] = b'{"alterado": "por otro escritor"}'
    remote_before = dict(world.s3.objects)
    svc.boot_recover()
    assert blocked_reason(world) == pi.REMOTE_UNEXPECTED_STATE and world.s3.objects == remote_before


def test_an_unreadable_intent_blocks_explicitly(world, monkeypatch):
    mixed_world_with_lost_fs(world, monkeypatch)
    intent = next(world.catalog.rglob("intent.json"))
    intent.write_text("{ no es json", encoding="utf-8")
    svc.boot_recover()
    assert blocked_reason(world) == pi.INTENT_UNREADABLE


# ═══════════════ otra interrupción DURANTE la recuperación ═══════════════
@pytest.mark.parametrize("point", ["intent_classified", "intent_local_applied", "intent_before_close"])
@pytest.mark.parametrize("scenario,expect", [("mixed", "V1"), ("complete", "V2")], ids=["cuerpo-mezclado", "cuerpo-completo"])
@pytest.mark.parametrize("lose_fs_again", [False, True], ids=["mismo-disco", "disco-perdido-otra-vez"])
def test_a_second_interruption_during_recovery_converges_on_the_next_pass(world, monkeypatch, point, scenario, expect, lose_fs_again):
    op1 = pub(world)[0].operation_id
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",) if scenario == "mixed" else (LEDGER_NAME,)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    wipe_fs(world)
    new_process(monkeypatch)
    svc.begin_boot_gate()
    remote_store.restore()
    arm_fault(monkeypatch, point)
    with pytest.raises(SimulatedCrash):
        svc.boot_recover()                                                                                                # el proceso muere mientras se recupera
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert gate.read_state(world.catalog) is not None                                                                    # y la lectura sigue bloqueada
    restart(world, monkeypatch, lose_fs=lose_fs_again)
    op = assert_not_hybrid(world)
    assert (op == op1) == (expect == "V1")


def test_a_process_death_while_rolling_spaces_back_leaves_it_recoverable(world, monkeypatch):
    op1 = pub(world)[0].operation_id
    confirmed = remote_unit(world)
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    wipe_fs(world)
    new_process(monkeypatch)
    svc.begin_boot_gate()
    remote_store.restore()
    world.s3.crash_before_keys = ("1VR_LP_BASE.json",)                                                                    # muere justo al restaurar la imagen previa de una vista
    with pytest.raises(SimulatedCrash):
        svc.boot_recover()
    world.s3.crash_before_keys = ()
    restart(world, monkeypatch, lose_fs=True)
    assert assert_not_hybrid(world) == op1 and remote_unit(world) == confirmed


# ═══════════════ la evidencia se retira y no se acumula ═══════════════
def test_the_intent_is_registered_before_any_body_object_and_removed_when_the_version_is_confirmed(world):
    pub(world)
    v2(world.tenant)
    start = len(world.s3.log)
    res, _ = pub(world)
    assert res.status == txn.COMPLETED
    ops = world.s3.log[start:]
    first_body = next(i for i, (o, k) in enumerate(ops) if ".intent/" not in k)
    intent_json = next(i for i, (o, k) in enumerate(ops) if o == "put" and k.endswith("/intent.json"))
    assert intent_json < first_body                                                                                       # intent.json está confirmado ANTES de tocar el cuerpo
    assert all(i < intent_json for i, (o, k) in enumerate(ops) if o == "put" and "/pre/" in k)                             # y después de sus imágenes previas
    assert intents_anywhere(world) == []                                                                                  # confirmada: sin rastro de la evidencia


def test_an_identical_reimport_registers_no_intent_and_writes_nothing(world):
    pub(world)
    before_uploads, before_log = world.s3.uploads, list(world.s3.log)
    again, _ = pub(world)
    assert again.outcome == "NO_CHANGE" and world.s3.uploads == before_uploads and world.s3.log == before_log


def test_a_failure_to_register_the_intent_leaves_spaces_untouched_and_keeps_the_last_version(world):
    pub(world)
    confirmed_remote, confirmed_local = dict(world.s3.objects), local_unit(world)
    v2(world.tenant)
    world.s3.fail_keys = ("/pre/",)                                                                                       # Spaces no acepta la imagen previa
    res, _ = pub(world)
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "PERSISTENCE_FAILED")
    assert {k: v for k, v in world.s3.objects.items() if ".intent/" not in k} == {k: v for k, v in confirmed_remote.items() if ".intent/" not in k}   # el cuerpo remoto ni se tocó
    assert local_unit(world) == confirmed_local and gate.read_state(world.catalog) is None
    world.s3.fail_keys = ()
    retry, _ = pub(world)                                                                                                 # el reintento resuelve cualquier resto y publica
    assert retry.status == txn.COMPLETED
    assert_not_hybrid(world)


def test_when_spaces_is_down_at_the_very_start_the_publication_is_cleanly_rolled_back(world, monkeypatch):
    pub(world)
    confirmed_local = local_unit(world)
    v2(world.tenant)
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **kw: setattr(world.s3, "down", True) if p == "before_remote" else None)
    res, _ = pub(world)
    assert (res.status, res.error_code) == (txn.ROLLED_BACK, "PERSISTENCE_FAILED") and local_unit(world) == confirmed_local
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    world.s3.down = False
    retry, _ = pub(world)                                                                                                 # la evidencia residual se resuelve sola al reintentar
    assert retry.status == txn.COMPLETED
    assert_not_hybrid(world)


# ═══════════════ el sync periódico no completa a ciegas una versión a medias ═══════════════
def test_the_periodic_sync_does_not_push_the_rest_of_an_unverified_version(world, monkeypatch):
    pub(world)
    v2(world.tenant)
    world.s3.crash_before_keys = ("3VF_LP.json",)
    run_until_crash(world)
    world.s3.crash_before_keys = ()
    new_process(monkeypatch)
    before = (world.s3.uploads, dict(world.s3.objects))
    assert remote_store.mirror_up() == 0 and (world.s3.uploads, world.s3.objects) == before                                # el disco conservado tiene la versión nueva completa: NO se sube en silencio
    restart(world, monkeypatch, lose_fs=False)
    assert_not_hybrid(world)


def test_the_periodic_sync_resumes_once_everything_is_resolved(world, monkeypatch):
    pub(world)
    assert remote_store.mirror_up() >= 0
    assert svc._mirror_guard() is None
