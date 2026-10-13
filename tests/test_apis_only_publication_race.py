"""Hotfix de coordinación `mirror_up` ⇄ `hold_publication` ⇄ APIS_ONLY (carrera confirmada en producción el 2026-10-13).

Contrato: un hold activo impide cualquier publicación concurrente del directorio retenido, INCLUIDOS los ciclos de `mirror_up` ya iniciados: el hold espera (acotado) a los ciclos en vuelo y,
como defensa adicional, `mirror_up` re-consulta el hold por archivo. APIS_ONLY no toca archivos locales hasta que el hold es exclusivo. Una clave «nueva» que aparece en Spaces con EXACTAMENTE el
contenido esperado se reconoce como preexistente (ni PUT ni rollback propio); con otro contenido se aborta sin sobrescribir. Las comunicaciones distinguen escrituras propias, ausencia de escrituras
propias y objetos ajenos al journal. La sincronización es DETERMINISTA: eventos y estado observable (nunca `sleep` como mecanismo), con los fakes de Graph y Spaces del test de APIS_ONLY.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from core import remote_store as rs
from tests.test_catalog_apis_only_sync import (  # noqa: F401 — `world` es un fixture
    FOLDER, PREFIX, TOKEN, _assert_fully_restored, _assert_no_pending_publication, key_of, remote_snapshot, tree_hash, world,
)
from webapp.services import catalog_apis_sync as apis

TIMEOUT = 10.0


def wait_until(cond, timeout=TIMEOUT):
    """Sondeo ACOTADO de un estado observable (no hay supuestos de duración: o el estado se alcanza o el test falla)."""
    gate, deadline = threading.Event(), time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "el estado esperado no se alcanzó"
        gate.wait(0.005)


def make_dirty(w, n):
    """n archivos locales SIN firma conocida: un ciclo de mirror_up los subiría."""
    for i in range(n):
        path = w.out / "zz_dirty" / f"x{i}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"sucio {i}\n", encoding="utf-8")
    return n


class BlockedMirror:
    """Corre `mirror_up` en un hilo y lo deja BLOQUEADO dentro de su primer upload (ciclo en vuelo, a mitad de un barrido), hasta `release`."""

    def __init__(self, w, blocked_threads=1):
        self.w, self.entered, self.release = w, threading.Event(), threading.Event()
        self.uploads: list[tuple[str, str]] = []
        self.results: list[int] = []
        self.threads: list[threading.Thread] = []
        self._mirror_names: set[str] = set()
        self._blocked = 0
        self._want = blocked_threads
        self._lock = threading.Lock()
        w.s3.after = self._hook

    def _hook(self, op, key):
        if op != "upload_file":
            return
        name = threading.current_thread().name
        self.uploads.append((name, key))
        if name in self._mirror_names:
            with self._lock:
                first = name not in getattr(self, "_seen", set())
                self._seen = getattr(self, "_seen", set()) | {name}
                if first:
                    self._blocked += 1
                    if self._blocked >= self._want:
                        self.entered.set()
            if first:
                assert self.release.wait(TIMEOUT), "el ciclo de mirror quedó bloqueado"

    def start(self, count=1):
        for i in range(count):
            t = threading.Thread(target=lambda: self.results.append(rs.mirror_up()), name=f"mirror-{i}")
            self._mirror_names.add(t.name)
            self.threads.append(t)
            t.start()
        assert self.entered.wait(TIMEOUT), "ningún ciclo de mirror llegó a publicar"
        return self

    def finish(self):
        self.release.set()
        for t in self.threads:
            t.join(TIMEOUT)
            assert not t.is_alive()

    def mirror_uploads(self):
        return [k for n, k in self.uploads if n in self._mirror_names]


# ══ 1. el hold espera a los ciclos en vuelo y los detiene por archivo ═══════════════════════════════════════════════════════════════════════
def test_a_mirror_cycle_started_before_the_hold_makes_the_hold_wait_and_stops_at_the_next_file(world):
    make_dirty(world, 3)
    mirror = BlockedMirror(world).start()
    assert rs._inflight == {"catalog_local": 1}
    got = {}

    def take():
        with rs.hold_publication(("catalog_local",)) as hold:
            got["exclusive"] = hold.exclusive
            got["mirror_uploads_at_acquire"] = len(mirror.mirror_uploads())
    holder = threading.Thread(target=take)
    holder.start()
    wait_until(lambda: rs._held.get("catalog_local") == 1)             # el hold ya es visible para los ciclos nuevos…
    assert "exclusive" not in got                                       # …pero NO se completó: espera al ciclo en vuelo (barrera)
    mirror.finish()
    holder.join(TIMEOUT)
    assert got == {"exclusive": True, "mirror_uploads_at_acquire": 1}   # el ciclo publicó UN archivo y se detuvo en el siguiente (control por archivo)
    assert len(mirror.mirror_uploads()) == 1 and rs._inflight == {} and rs._held == {}


def test_a_hold_acquired_during_a_sweep_publishes_nothing_more_until_it_is_released(world):
    make_dirty(world, 4)
    mirror = BlockedMirror(world).start()
    release_hold, in_hold = threading.Event(), threading.Event()

    def hold_for_a_while():
        with rs.hold_publication(("catalog_local",)):
            in_hold.set()
            assert release_hold.wait(TIMEOUT)
    holder = threading.Thread(target=hold_for_a_while)
    holder.start()
    wait_until(lambda: rs._held.get("catalog_local") == 1)
    mirror.finish()
    assert in_hold.wait(TIMEOUT)
    uploaded_before = len(mirror.mirror_uploads())
    assert uploaded_before == 1
    assert rs.mirror_up() == 0 and len(mirror.mirror_uploads()) == uploaded_before          # con el hold activo, ningún ciclo publica nada del árbol
    release_hold.set()
    holder.join(TIMEOUT)
    world.s3.after = lambda op, key: None
    assert rs.mirror_up() == 3                                                              # liberado el hold, lo restante se publica con normalidad (nada se perdió)


def test_the_per_file_control_alone_stops_a_cycle_even_if_the_barrier_times_out(world):
    make_dirty(world, 3)
    mirror = BlockedMirror(world).start()
    with rs.hold_publication(("catalog_local",), wait_timeout=0.05) as hold:                # vence la espera: el hold NO es exclusivo…
        assert hold.exclusive is False
        mirror.finish()                                                                     # …el ciclo en vuelo se reanuda CON el hold activo
        assert len(mirror.mirror_uploads()) == 1                                            # …y aun así se detuvo en su siguiente archivo (defensa adicional)


def test_two_cycles_in_flight_are_both_awaited(world):
    make_dirty(world, 4)
    mirror = BlockedMirror(world, blocked_threads=2).start(count=2)
    assert rs._inflight == {"catalog_local": 2}
    got = {}
    holder = threading.Thread(target=lambda: got.update(ex=rs.hold_publication(("catalog_local",)).__enter__().exclusive))
    holder.start()
    wait_until(lambda: rs._held.get("catalog_local") == 1)
    assert "ex" not in got
    mirror.finish()
    holder.join(TIMEOUT)
    assert got["ex"] is True
    with rs._hold_cv:                                                                       # limpieza del hold que el hilo dejó tomado a propósito
        rs._held.clear()


# ══ 2. APIS_ONLY no modifica nada local hasta que el hold es exclusivo ═════════════════════════════════════════════════════════════════
def test_apis_only_waits_for_an_in_flight_mirror_before_touching_any_local_file(world):
    plan = apis.build_plan(TOKEN)
    make_dirty(world, 3)
    before, listed = tree_hash(world.root), len(world.graph.listed)
    mirror = BlockedMirror(world).start()
    box = {}
    applier = threading.Thread(target=lambda: box.update(outcome=apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)))
    applier.start()
    wait_until(lambda: rs._held.get("catalog_local") == 1)
    assert "outcome" not in box                                                             # sigue esperando: no entró a APIS_ONLY
    assert tree_hash(world.root) == before and len(world.graph.listed) == listed and world.graph.downloaded == []     # ningún archivo local tocado, ni Graph consultado, ni plan rearmado
    mirror.finish()
    applier.join(TIMEOUT)
    assert box["outcome"].state == "ok", box["outcome"].lines
    scope_uploads = [k for k in mirror.mirror_uploads() if "/APIs/" in k or "01. APIs" in k or "download_manifest" in k]
    assert scope_uploads == [] and len(mirror.mirror_uploads()) == 1                         # el barrido nunca publicó nada del subárbol de APIs


def test_a_mirror_cycle_that_starts_during_the_import_publishes_nothing(world, monkeypatch):
    import catalog_service as cs
    plan = apis.build_plan(TOKEN)
    make_dirty(world, 2)
    seen = {}
    original = cs._download_text

    def download_and_sweep(url):
        text = original(url)
        if "mid" not in seen:
            world.s3.ops.clear()
            seen["mid"] = rs.mirror_up()                                                   # el ciclo periódico dispara A MITAD de la importación
            seen["mutations"] = world.s3.mutations()
        return text
    monkeypatch.setattr(cs, "_download_text", download_and_sweep)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok" and seen["mid"] == 0 and seen["mutations"] == []           # retenido: ni una publicación intermedia
    published = {k for op, k in world.s3.ops if op == "upload_file"}
    assert published and all(("/APIs/" in k or "01. APIs" in k or k.endswith("download_manifest")) for k in published)    # lo único publicado es lo del plan


def test_apis_only_aborts_without_touching_anything_if_the_hold_is_not_exclusive(world, monkeypatch):
    plan = apis.build_plan(TOKEN)
    make_dirty(world, 2)
    monkeypatch.setattr(rs, "HOLD_WAIT_TIMEOUT", 0.05)
    mirror = BlockedMirror(world).start()
    before = tree_hash(world.root)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)                          # la espera vence: no hay deadlock
    assert outcome.state == "aborted" and "publicación periódica en curso" in outcome.lines[0] and "No se modificó ningún archivo" in outcome.lines[0]
    assert tree_hash(world.root) == before and world.graph.downloaded == [] and rs._held == {}      # y el hold se liberó
    mirror.finish()
    world.s3.after = lambda op, key: None
    monkeypatch.setattr(rs, "HOLD_WAIT_TIMEOUT", 300.0)
    assert apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE).state in ("ok", "aborted")   # reintento sin ciclo en vuelo: sigue su curso


# ══ 3. ausencia de deadlocks y compatibilidad con los productores actuales ═══════════════════════════════════════════════════════════════
def test_a_hold_on_another_directory_never_waits_for_a_cycle_on_catalog_local(world):
    make_dirty(world, 2)
    mirror = BlockedMirror(world).start()
    started = time.monotonic()
    with rs.hold_publication(("case_library",), wait_timeout=0.2) as hold:                  # otro directorio: ninguna espera (la exclusión está acotada al árbol protegido)
        assert hold.exclusive is True and time.monotonic() - started < 0.2
    mirror.finish()


def test_holds_are_reentrant_and_sequential_without_a_cycle_in_flight(world):
    with rs.hold_publication(("catalog_local",)) as outer:
        with rs.hold_publication(("catalog_local",)) as inner:
            assert outer.exclusive and inner.exclusive and rs._held == {"catalog_local": 2}
        assert rs._held == {"catalog_local": 1}
    assert rs._held == {} and rs._inflight == {}
    with rs.hold_publication(("catalog_local",)):
        pass
    assert rs._held == {}


def test_a_cycle_that_raises_always_releases_its_in_flight_registration(world, monkeypatch):
    monkeypatch.setattr(rs, "_mirror_dir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla del barrido")))
    with pytest.raises(RuntimeError):
        rs.mirror_up()
    assert rs._inflight == {}
    with rs.hold_publication(("catalog_local",), wait_timeout=1.0) as hold:                 # y un hold posterior NO queda esperando a un ciclo muerto
        assert hold.exclusive is True


def test_an_interrupted_wait_does_not_leave_the_directory_held(world, monkeypatch):
    rs._inflight["catalog_local"] = 1                                                       # un ciclo «en vuelo» simulado
    monkeypatch.setattr(rs._hold_cv, "wait", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rs.hold_publication(("catalog_local",)).__enter__()
    assert rs._held == {}
    rs._inflight.clear()


def test_existing_producers_keep_their_semantics_with_an_in_flight_cycle(world):
    """El productor de siempre (`with hold_publication(...)`, p. ej. «Generar catálogo») sigue funcionando: espera al ciclo en vuelo y luego retiene."""
    make_dirty(world, 2)
    mirror = BlockedMirror(world).start()
    done = threading.Event()

    def legacy_producer():
        with rs.hold_publication(("catalog_local",)) as hold:
            hold.fail()                                                                     # una regeneración fallida retiene lo no confirmado, como siempre
        done.set()
    t = threading.Thread(target=legacy_producer)
    t.start()
    wait_until(lambda: rs._held.get("catalog_local") == 1)
    assert not done.is_set()
    mirror.finish()
    assert done.wait(TIMEOUT)
    t.join(TIMEOUT)
    assert rs._held == {} and rs._withheld                                                  # la retención posterior a un fallo se conserva


# ══ 4. clave nueva que aparece en Spaces ══════════════════════════════════════════════════════════════════════════════════════════════════
def _journal(world, outcome):
    return json.loads(world.s3.objects[f"{PREFIX}/backups/apis-sync-{outcome.journal_id}/journal.json"])


def _appear_on_first_copy(world, key, data_for):
    state = {"done": False}

    def hook(op, k):
        if op == "copy_object" and not state["done"]:
            state["done"] = True
            world.s3.objects[key] = data_for()
    world.s3.after = hook


def test_a_new_key_that_appears_with_identical_content_is_recognised_as_preexisting(world):
    plan = apis.build_plan(TOKEN)
    intruder = key_of(f".csn_cache/{FOLDER}/D.md")
    _appear_on_first_copy(world, intruder, lambda: (world.cache / FOLDER / "D.md").read_bytes())
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok", outcome.lines
    assert intruder not in {k for op, k in world.s3.ops if op == "upload_file"}               # sin PUT innecesario
    entry = next(e for e in _journal(world, outcome)["keys"] if e["key"] == intruder)
    assert entry["state"] == "preexisting" and entry["before_sha"] == entry["after_sha"]        # preexistente, no creación propia
    assert any("IDÉNTICO" in l and "otro productor" in l and "ningún rollback las toca" in l for l in outcome.lines)
    assert any("publicadas por esta operación" in l for l in outcome.lines) and outcome.published == 4


def test_a_new_key_that_appears_with_different_content_aborts_without_overwriting(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    intruder = key_of(f".csn_cache/{FOLDER}/D.md")
    _appear_on_first_copy(world, intruder, lambda: b"contenido distinto de otro productor")
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and "contenido DISTINTO" in " ".join(outcome.lines)
    assert world.s3.objects[intruder] == b"contenido distinto de otro productor"                  # ni sobrescrita ni borrada
    assert intruder not in {k for op, k in world.s3.ops if op in ("upload_file", "delete_object")}
    assert any("no escribió nada en Spaces" in l and "no hay escrituras propias que revertir" in l for l in outcome.lines)
    assert tree_hash(world.root) == local_before                                                # el local quedó como antes
    remote_after = remote_snapshot(world)
    assert {k: v for k, v in remote_after.items() if k != intruder} == remote_before


def test_an_identical_preexisting_key_is_kept_by_a_rollback_while_own_creations_are_undone(world):
    plan = apis.build_plan(TOKEN)
    intruder = key_of(f".csn_cache/{FOLDER}/D.md")
    _appear_on_first_copy(world, intruder, lambda: (world.cache / FOLDER / "D.md").read_bytes())
    own_new = key_of("APIs/D.md")
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and key == key_of("APIs/B.md")      # falla un PUT posterior
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and outcome.rollback_verified is True
    assert intruder in world.s3.objects                                                         # NO creada por la operación: el rollback la conserva
    assert own_new not in world.s3.objects                                                      # las creaciones propias sí se deshacen
    assert any("1 clave(s) del plan ya estaban en Spaces con contenido IDÉNTICO" in l for l in outcome.lines)
    states = {e["rel"].split("/")[-1] + ":" + e["kind"]: e["state"] for e in _journal(world, outcome)["keys"]}
    assert states[f"D.md:cache"] == "preexisting"


def test_an_existing_key_that_changed_unexpectedly_is_still_rejected(world):
    plan = apis.build_plan(TOKEN)
    victim = key_of("APIs/B.md")                                                                 # existía en el preflight
    world.s3.after = lambda op, key: world.s3.objects.__setitem__(victim, b"otro proceso") if op == "copy_object" and key.endswith("/APIs/B.md") else None
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state in ("rolled_back", "incomplete") and "otro proceso" in " ".join(outcome.lines)
    assert world.s3.objects[victim] == b"otro proceso"                                          # sin flexibilizar el control de claves ya existentes


def test_a_key_that_appears_identical_between_the_check_and_the_put_is_also_recognised(world):
    plan = apis.build_plan(TOKEN)
    target = key_of("APIs/D.md")                                                                 # clave nueva que se publica DESPUÉS de varios PUT propios

    def hook(op, key):
        if op == "upload_file" and key.endswith(f"/{FOLDER}/D.md") and target not in world.s3.objects:   # tras publicar el crudo de D, «otro productor» publica APIs/D.md idéntico
            world.s3.objects[target] = (world.out / "APIs" / "D.md").read_bytes()
    world.s3.after = hook
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok", outcome.lines
    assert target not in {k for op, k in world.s3.ops if op == "upload_file"}
    assert next(e for e in _journal(world, outcome)["keys"] if e["key"] == target)["state"] == "preexisting"


# ══ 5. comunicación: propias, ninguna propia, ajenas ════════════════════════════════════════════════════════════════════════════════════════
def test_messages_distinguish_own_writes_from_no_own_writes_and_never_claim_a_global_restore(world):
    plan = apis.build_plan(TOKEN)
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and n == 3
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    text = "\n".join(outcome.lines)
    assert outcome.state == "rolled_back" and "escritura(s) propia(s) de esta operación verificado por hash" in text and "no es una verificación global de Spaces" in text
    assert "Spaces y el catálogo local quedaron como antes" not in text and "Rollback VERIFICADO" not in text


def test_objects_outside_the_journal_are_reported_and_left_alone(world):
    plan = apis.build_plan(TOKEN)
    stranger = key_of(f".csn_cache/{FOLDER}/ZZ_OTRO_PRODUCTOR.md")
    _appear_on_first_copy(world, stranger, lambda: b"publicado por otro productor")
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    text = "\n".join(outcome.lines)
    assert outcome.state == "ok" and "NO figuran en el journal" in text and "ZZ_OTRO_PRODUCTOR.md" in text and "posible publicación de otro productor" in text
    assert world.s3.objects[stranger] == b"publicado por otro productor"                           # informado, jamás tocado
    assert stranger not in {e["key"] for e in _journal(world, outcome)["keys"]}


def test_a_clean_run_reports_no_foreign_objects_and_only_own_writes(world):
    plan = apis.build_plan(TOKEN)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    text = "\n".join(outcome.lines)
    assert outcome.state == "ok" and "NO figuran en el journal" not in text and "IDÉNTICO" not in text and "publicadas por esta operación" in text


def test_the_apply_after_a_foreign_cycle_published_identical_cache_files_still_succeeds(world):
    """El escenario de producción: el ciclo periódico publicó una foto parcial de la caché (identica a lo esperado) antes del apply; el preflight lo ve como «existe» y se respalda y publica."""
    for name in ("D",):
        world.s3.objects[key_of(f".csn_cache/{FOLDER}/{name}.md")] = b"# D\nnueva\n"
    plan = apis.build_plan(TOKEN)
    assert next(k for k in plan.keys if k.key.endswith(f"{FOLDER}/D.md")).remote == "existe"
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok", outcome.lines
    _assert_no_pending_publication(world)


def test_a_preexisting_key_that_changes_before_the_operation_closes_aborts_and_is_never_deleted(world):
    plan = apis.build_plan(TOKEN)
    intruder = key_of(f".csn_cache/{FOLDER}/D.md")
    state = {"copied": False}

    def hook(op, key):
        if op == "copy_object" and not state["copied"]:
            state["copied"] = True
            world.s3.objects[intruder] = (world.cache / FOLDER / "D.md").read_bytes()              # aparece idéntica y se reconoce como preexistente…
        elif op == "upload_file" and key.endswith("/APIs/B.md"):
            world.s3.objects[intruder] = b"cambiada por otro productor despues"                    # …pero otro productor la cambia antes de que cierre la operación
    world.s3.after = hook
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state in ("rolled_back", "incomplete") and "reconocida como ya publicada, cambió" in " ".join(outcome.lines)
    assert world.s3.objects[intruder] == b"cambiada por otro productor despues"                      # no es nuestra: ni se sobrescribe ni se borra
    assert intruder not in {k for op, k in world.s3.ops if op in ("delete_object",)}
