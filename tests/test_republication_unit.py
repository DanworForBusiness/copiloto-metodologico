"""P1 · la publicación como UNIDAD con confirmación DURABLE: idempotencia, bitácora, compuerta de lectores, respuestas perdidas, listados que fallan, borrados remotos y recuperación.

Frontera simulada: el tenant de Datasphere y el bucket (``FakeS3`` con fallos inyectables). Exportador, manifiestos, generador del Catálogo, ``remote_store`` y la recuperación son los REALES.
Criterio del dueño que fija este archivo: una versión NUEVA no es vigente mientras Spaces no haya confirmado su contenido y el marcador de activación; si la confirmación falla o queda
incierta, o se mantiene la última versión confirmada o se bloquea la lectura hasta resolverlo; una escritura efectiva con respuesta perdida se VERIFICA antes de revertir o declarar fallo.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from core import publication_gate as gate
from core import remote_store
from tests.publication_fakes import CLIENT, SPACE, FakeS3, enable_remote, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import publication_ledger as ledger_mod
from tools.catalog import publication_txn as txn

MARKER = "Acme.publication.json"


def v2(t):
    t.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    t.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    t.drop(SPACE, "views", "1VR_LP_EXTRA")


def remote_keys(s3):
    return sorted(s3.objects)


def local_tree(ctx):
    return {p.relative_to(ctx.catalog).as_posix(): p.read_bytes() for p in sorted(ctx.catalog.rglob("*")) if p.is_file()}


@pytest.fixture()
def remote(ctx, monkeypatch):                                                                               # noqa: F811
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    gate.set_resolver(None)
    yield s3
    gate.clear(ctx.catalog)
    gate.set_resolver(None)


def crash_before_remote(monkeypatch, then=None):
    def hook(point, **kw):
        if point == "before_remote" and then is not None:
            then()
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)


# ═══════════════ idempotencia ═══════════════
def test_an_identical_reimport_writes_nothing_locally_nor_remotely(ctx, remote):                              # noqa: F811
    first, _ = ctx.publish(mirror=True)
    assert first.status == txn.COMPLETED and first.outcome == "CHANGED"
    before_tree, before_uploads, before_log = local_tree(ctx), remote.uploads, list(remote.log)
    mtimes = {p: p.stat().st_mtime_ns for p in ctx.catalog.rglob("*") if p.is_file()}
    again, lines = ctx.publish(mirror=True)
    assert (again.status, again.ok, again.outcome, again.applied["changed"]) == (txn.COMPLETED, True, "NO_CHANGE", False)
    assert local_tree(ctx) == before_tree and {p: p.stat().st_mtime_ns for p in ctx.catalog.rglob("*") if p.is_file()} == mtimes
    assert remote.uploads == before_uploads and remote.log == before_log                                    # Spaces: ni una escritura
    assert any("SIN CAMBIOS" in ln for ln in lines) and len(ctx.journals()) == 1                            # y ni siquiera un journal nuevo


def test_an_identical_reimport_does_not_grow_the_history(ctx):                                              # noqa: F811
    ctx.publish()
    ctx.publish()
    ctx.publish()
    state, data = ledger_mod.load(ctx.catalog, CLIENT)
    assert state == ledger_mod.L_OK and len(data["history"]) == 1


def test_an_identical_reimport_repairs_a_remote_that_was_behind(ctx, remote):                               # noqa: F811
    ctx.publish()                                                                                           # publicada en local, nunca confirmada en Spaces (p. ej. con el modo local)
    again, _ = ctx.publish(mirror=True)
    assert again.status == txn.COMPLETED and again.outcome == "NO_CHANGE" and again.durability == txn.DURABILITY_REMOTE
    assert any(k.endswith(MARKER) for k in remote.objects) and any(k.endswith("AM_Lista_Precios.json") for k in remote.objects)


def test_an_identical_reimport_with_an_unconfirmable_remote_is_refused_not_reported_as_fine(ctx, remote):    # noqa: F811
    ctx.publish()
    remote.fail_keys = ("RAW_PROD",)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "PERSISTENCE_UNCONFIRMED")


# ═══════════════ bitácora: versión anterior → versión nueva → ejecución ═══════════════
def test_the_ledger_traces_previous_new_and_the_execution(ctx):                                              # noqa: F811
    first, _ = ctx.publish()
    v2(ctx.tenant)
    second, _ = ctx.publish()
    state, data = ledger_mod.load(ctx.catalog, CLIENT)
    assert state == ledger_mod.L_OK
    active = data["active"]
    assert active["operation_id"] == second.operation_id and active["previous_operation_id"] == first.operation_id
    assert [h["operation_id"] for h in data["history"]] == [first.operation_id, second.operation_id]
    assert data["history"][1]["previous_operation_id"] == first.operation_id and active["root_object"] == "AM_Lista_Precios" and active["space"] == SPACE
    assert "views/1VR_LP_EXTRA" not in active["objects"] and "views/1VR_LP_BASE" in active["objects"]


def test_the_ledger_digests_describe_exactly_what_is_on_disk(ctx):                                           # noqa: F811
    ctx.publish()
    _s, data = ledger_mod.load(ctx.catalog, CLIENT)
    files = ledger_mod.unit_files(ctx.catalog, ctx.pool, CLIENT, None)
    assert ledger_mod.verify(data, files) == []
    (ctx.pool / CLIENT / "views" / "3VF_LP.json").write_text("{}", encoding="utf-8")                          # una mezcla / alteración se DETECTA
    assert any("3VF_LP" in p for p in ledger_mod.verify(data, ledger_mod.unit_files(ctx.catalog, ctx.pool, CLIENT, None)))


def test_a_missing_ledger_is_adopted_by_the_next_publication_not_treated_as_no_change(ctx):                  # noqa: F811
    ctx.publish()
    ledger_mod.ledger_path(ctx.catalog, CLIENT).unlink()
    res, _ = ctx.publish()
    assert res.status == txn.COMPLETED and res.outcome == "CHANGED" and ledger_mod.load(ctx.catalog, CLIENT)[0] == ledger_mod.L_OK


def test_an_unreadable_ledger_is_never_the_same_as_no_history(ctx):                                          # noqa: F811
    ctx.publish()
    ledger_mod.ledger_path(ctx.catalog, CLIENT).write_text("{truncado", encoding="utf-8")
    assert ledger_mod.load(ctx.catalog, CLIENT)[0] == ledger_mod.L_UNREADABLE
    res, _ = ctx.publish()
    assert res.status == txn.COMPLETED and res.outcome == "CHANGED"                                         # se reescribe de forma atómica; nunca se interpreta como «vacío»


def test_a_disk_that_no_longer_matches_the_ledger_is_republished_not_declared_unchanged(ctx):                 # noqa: F811
    ctx.publish()
    (ctx.catalog / CLIENT / "General" / "vistas" / "RAW_PROD.md").write_text("alterado", encoding="utf-8")
    res, _ = ctx.publish()
    assert res.outcome == "CHANGED"                                                                          # nunca «sin cambios» si el disco no es la versión del ledger


# ═══════════════ compuerta de lectores durante la activación ═══════════════
def test_readers_cannot_see_the_unit_while_it_is_being_activated_and_the_owner_can(ctx, monkeypatch):         # noqa: F811
    ctx.publish()
    v2(ctx.tenant)
    seen: dict = {}

    def probe():
        def reader():
            try:
                gate.check(ctx.catalog, wait=0)
                seen["reader"] = "paso"
            except gate.PublicationInProgress:
                seen["reader"] = "espera"
        t = threading.Thread(target=reader)
        t.start()
        t.join(5)
        gate.check(ctx.catalog, wait=0)                                                                      # el hilo dueño no se bloquea a sí mismo
        seen["owner"] = "paso"

    monkeypatch.setattr(txn, "FAULT_HOOK", lambda point, **kw: probe() if point == "after_pool" else None)
    res, _ = ctx.publish()
    assert res.status == txn.COMPLETED and seen == {"reader": "espera", "owner": "paso"}
    gate.check(ctx.catalog, wait=0)                                                                           # y al terminar la compuerta quedó abierta


def test_the_gate_opens_after_a_rollback_too(ctx, monkeypatch):                                              # noqa: F811
    ctx.publish()
    v2(ctx.tenant)
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda point, **kw: (_ for _ in ()).throw(RuntimeError("x")) if point == "after_catalog" else None)
    res, _ = ctx.publish()
    assert res.status == txn.ROLLED_BACK and gate.read_state(ctx.catalog) is None


# ═══════════════ confirmación durable: la versión nueva no es vigente sin Spaces ═══════════════
def test_the_new_version_is_not_visible_to_readers_until_spaces_confirms(ctx, remote, monkeypatch):           # noqa: F811
    ctx.publish(mirror=True)
    v2(ctx.tenant)
    states: dict = {}

    def at_remote():
        states["gate"] = (gate.read_state(ctx.catalog) or {}).get("state")
        states["marker_remote"] = any(k.endswith(MARKER) for k in remote.objects)

    monkeypatch.setattr(txn, "FAULT_HOOK", lambda point, **kw: at_remote() if point == "before_remote" else None)
    res, _ = ctx.publish(mirror=True)
    assert states["gate"] == gate.ACTIVATING                                                                  # durante la confirmación los lectores esperan
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_REMOTE and gate.read_state(ctx.catalog) is None


def test_the_activation_marker_is_uploaded_last_and_removals_are_deleted_before_uploading(ctx, remote):        # noqa: F811
    ctx.publish(mirror=True)
    assert any(k.endswith("1VR_LP_EXTRA.json") for k in remote.objects)
    v2(ctx.tenant)
    start = len(remote.log)
    res, _ = ctx.publish(mirror=True)
    ops = remote.log[start:]
    assert res.status == txn.COMPLETED and not any(k.endswith("1VR_LP_EXTRA.json") for k in remote.objects)    # el objeto retirado NO queda en Spaces (no resucita en un restore)
    assert {o for o, _k in ops[:2]} == {"delete"}                                                             # lo retirado se borra ANTES de subir lo nuevo
    assert {k.rsplit("/", 1)[-1] for o, k in ops[-2:]} == {MARKER, ".catalog_manifest.json"} and all(o == "put" for o, _k in ops[-2:])   # y los marcadores van AL FINAL


def test_a_lost_response_after_a_successful_write_is_verified_not_reverted(ctx, remote):                      # noqa: F811
    """La escritura del marcador SÍ ocurrió pero la respuesta se perdió: persist_changed lo ve como fallo; la publicación VERIFICA el remoto y la da por confirmada."""
    remote.lose_response_keys = (MARKER,)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.ok, res.durability) == (txn.COMPLETED, True, txn.DURABILITY_REMOTE)
    assert any(k.endswith(MARKER) for k in remote.objects) and [j["state"] for j in ctx.journals()] == [txn.J_REMOTE_CONFIRMED]
    assert gate.read_state(ctx.catalog) is None


def test_a_lost_response_on_a_body_file_is_also_verified_before_declaring_failure(ctx, remote):               # noqa: F811
    remote.lose_response_keys = ("RAW_PROD.json",)
    res, _ = ctx.publish(mirror=True)
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_REMOTE
    for key in ("audit/catalog_local/.csn_cache/Acme/local-tables/RAW_PROD.json", "audit/catalog_local/.publication_state/" + MARKER):
        assert key in remote.objects


def test_a_rejected_upload_restores_the_last_confirmed_version_in_local_and_in_spaces(ctx, remote):            # noqa: F811
    ctx.publish(mirror=True)
    confirmed_local, confirmed_remote = local_tree(ctx), dict(remote.objects)
    v2(ctx.tenant)
    remote.fail_keys = ("3VF_LP.json",)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code, res.ok) == (txn.ROLLED_BACK, "PERSISTENCE_FAILED", False)
    assert local_tree(ctx) == confirmed_local and remote.objects == confirmed_remote                          # NI UN byte distinto de la última versión confirmada, en ambos lados
    assert gate.read_state(ctx.catalog) is None and not txn.has_pending(ctx.catalog)
    remote.fail_keys = ()
    retry, _ = ctx.publish(mirror=True)
    assert retry.status == txn.COMPLETED and retry.durability == txn.DURABILITY_REMOTE and not any(k.endswith("1VR_LP_EXTRA.json") for k in remote.objects)


def test_when_spaces_cannot_be_read_the_publication_is_neither_confirmed_nor_undone_and_readers_are_blocked(ctx, remote, monkeypatch):   # noqa: F811
    ctx.publish(mirror=True)
    confirmed_remote = dict(remote.objects)
    v2(ctx.tenant)
    crash_before_remote(monkeypatch, lambda: setattr(remote, "down", True))                                  # Spaces cae justo al confirmar
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.RECOVERY_REQUIRED, "PERSISTENCE_UNCERTAIN")
    assert gate.read_state(ctx.catalog)["state"] == gate.UNCERTAIN                                           # lectura bloqueada: no se muestra como vigente
    with pytest.raises(gate.PublicationBlocked):
        t_result: list = []
        t = threading.Thread(target=lambda: t_result.append(_catch(lambda: gate.check(ctx.catalog, wait=0))))
        t.start()
        t.join(5)
        raise t_result[0]
    assert [j["state"] for j in ctx.journals() if j["operation_id"] == res.operation_id] == [txn.J_COMMIT_UNCERTAIN] and txn.has_pending(ctx.catalog)
    # una nueva publicación NO avanza sobre un estado sin resolver
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    blocked, _ = ctx.publish(mirror=True)
    assert (blocked.status, blocked.error_code) == (txn.RECOVERY_REQUIRED, "RECOVERY_REQUIRED")
    # Spaces vuelve y SÍ guarda todo: la recuperación completa hacia adelante, verificando, y abre la compuerta
    remote.down = False
    recovered = txn.recover_pending(ctx.catalog)
    assert [r["action"] for r in recovered] == ["CONFIRMED"] and gate.read_state(ctx.catalog) is None and not txn.has_pending(ctx.catalog)
    assert remote.objects != confirmed_remote and any(k.endswith(MARKER) for k in remote.objects)


def test_an_uncertain_publication_that_spaces_can_no_longer_store_is_undone_after_verifying(ctx, remote, monkeypatch):   # noqa: F811
    ctx.publish(mirror=True)
    confirmed_local, confirmed_remote = local_tree(ctx), dict(remote.objects)
    v2(ctx.tenant)
    crash_before_remote(monkeypatch, lambda: setattr(remote, "down", True))
    res, _ = ctx.publish(mirror=True)
    assert res.error_code == "PERSISTENCE_UNCERTAIN"
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    remote.down = False
    remote.fail_keys = ("3VF_LP.json",)                                                                       # vuelve, pero ya no acepta la versión nueva
    recovered = txn.recover_pending(ctx.catalog)
    assert [r["action"] for r in recovered] == ["ROLLED_BACK"]
    assert local_tree(ctx) == confirmed_local and remote.objects == confirmed_remote and gate.read_state(ctx.catalog) is None


def test_if_the_compensation_cannot_complete_the_previous_version_stays_active_and_new_publications_wait(ctx, remote, monkeypatch):   # noqa: F811
    ctx.publish(mirror=True)
    confirmed_local = local_tree(ctx)
    v2(ctx.tenant)
    crash_before_remote(monkeypatch, lambda: setattr(remote, "down", True))
    assert ctx.publish(mirror=True)[0].error_code == "PERSISTENCE_UNCERTAIN"
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    remote.down = False
    remote.fail_keys = ("3VF_LP.json", "1VR_LP_EXTRA.json")                                                   # ni la nueva ni la previa pueden escribirse para esas claves
    recovered = txn.recover_pending(ctx.catalog)
    assert [r["action"] for r in recovered] == ["DIVERGED"]
    assert local_tree(ctx) == confirmed_local and gate.read_state(ctx.catalog) is None                       # los lectores vuelven a la ÚLTIMA versión confirmada
    blocked, _ = ctx.publish(mirror=True)
    assert blocked.error_code == "RECOVERY_REQUIRED"
    remote.fail_keys = ()
    assert [r["action"] for r in txn.recover_pending(ctx.catalog)] == ["ROLLED_BACK"] and not txn.has_pending(ctx.catalog)
    assert ctx.publish(mirror=True)[0].status == txn.COMPLETED


def test_a_gate_resolver_lets_a_blocked_reader_recover_the_publication(ctx, remote, monkeypatch):             # noqa: F811
    ctx.publish(mirror=True)
    v2(ctx.tenant)
    crash_before_remote(monkeypatch, lambda: setattr(remote, "down", True))
    assert ctx.publish(mirror=True)[0].error_code == "PERSISTENCE_UNCERTAIN"
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    remote.down = False
    gate.set_resolver(lambda root: txn.recover_pending(root, timeout=0))
    monkeypatch.setattr(gate, "_last_resolve", 0.0)
    out: list = []
    t = threading.Thread(target=lambda: out.append(_catch(lambda: gate.check(ctx.catalog, wait=0))))
    t.start()
    t.join(10)
    assert out == [None] and not txn.has_pending(ctx.catalog)


# ═══════════════ listados y lecturas remotas que fallan: resultado EXPLÍCITO, nunca «no hay huérfanos» ═══════════════
def test_a_failing_remote_listing_refuses_the_publication_before_touching_anything(ctx, remote):               # noqa: F811
    remote.fail_list = True
    before = local_tree(ctx)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "REMOTE_LISTING_FAILED") and local_tree(ctx) == before
    assert not txn.has_pending(ctx.catalog) and ctx.journals() == [] and gate.read_state(ctx.catalog) is None


def test_an_unreadable_remote_probe_refuses_the_publication(ctx, remote):                                     # noqa: F811
    remote.fail_head_keys = (MARKER,)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "REMOTE_LISTING_FAILED")


def test_local_files_that_differ_from_the_remote_and_were_never_reconciled_refuse_the_publication(ctx, remote):  # noqa: F811
    ctx.publish(mirror=True)
    key = remote_store._key("catalog_local/.csn_cache/Acme/views/3VF_LP.json")
    remote_store._unreconciled.add(key)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "REMOTE_NOT_RECONCILED")


def test_a_read_only_persistence_mode_cannot_confirm_so_it_refuses(ctx, monkeypatch):                          # noqa: F811
    import config
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    res, _ = ctx.publish(mirror=True)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "PERSISTENCE_NOT_AVAILABLE")


# ═══════════════ H2: lo que se borra en local también se borra en Spaces ═══════════════
def test_remote_orphans_that_a_restore_would_resurrect_are_removed_only_inside_the_clients_own_prefixes(ctx, remote):   # noqa: F811
    ctx.publish(mirror=True)
    own_orphan = "audit/catalog_local/.csn_cache/Acme/views/VIEJA_BORRADA.json"
    other_client = "audit/catalog_local/.csn_cache/Otro/views/DE_OTRO.json"
    remote.objects[own_orphan] = b"{}"
    remote.objects[other_client] = b"{}"
    v2(ctx.tenant)
    res, _ = ctx.publish(mirror=True)
    assert res.status == txn.COMPLETED
    assert own_orphan not in remote.objects                                                                   # huérfano del cliente: retirado
    assert other_client in remote.objects                                                                     # el de otro cliente: INTACTO
    assert own_orphan.split("audit/")[1] in res.applied["remote_removed"]


def test_the_remote_deletion_cap_protects_the_bucket_from_a_calculation_error(ctx, remote, monkeypatch):        # noqa: F811
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    ctx.publish(mirror=True)
    for i in range(30):
        remote.objects[f"audit/catalog_local/.csn_cache/Acme/views/ORFANA_{i}.json"] = b"{}"
    monkeypatch.setattr(dsp, "_MAX_REMOTE_DELETES", 10)
    v2(ctx.tenant)
    res, _ = ctx.publish(mirror=True)
    assert res.status == txn.COMPLETED and sum(1 for k in remote.objects if "ORFANA_" in k) >= 20


def _catch(fn):
    try:
        fn()
    except Exception as exc:                                                                                    # noqa: BLE001
        return exc
    return None
