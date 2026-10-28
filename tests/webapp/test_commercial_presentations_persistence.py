"""
test_commercial_presentations_persistence.py — Presentaciones V1: CONTRATO DE PERSISTENCIA del Hub (mismo que `profile_repository`).

  read-only → rechaza ANTES de tocar el disco · local → escritura atómica local, sin push · remote → push obligatorio; si no se confirma se compensa y se informa 503 (nunca éxito presumido).
  Revisión desactualizada → 409 sin escribir. El restore de arranque se espera antes de leer. El directorio cae bajo un directorio YA persistido (sin variable ni ruta nueva).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

import config
from core import remote_store
from tests.webapp.test_commercial_presentations_access import (  # noqa: F401  (fixtures y helpers compartidos)
    A, ORIGIN, SEED_ID, _copy, _delete, _save, world,
)
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]


class FakeSpaces:
    """Spaces simulado: registra los push/delete y permite forzar fallos."""

    def __init__(self, monkeypatch, *, enabled=True, push_results=None):
        self.pushed: list[Path] = []
        self.deleted: list[Path] = []
        self.restore_waits = 0
        self.push_results = list(push_results or [])
        self.enabled = enabled
        monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
        monkeypatch.setattr(remote_store, "enabled", lambda: self.enabled)
        monkeypatch.setattr(remote_store, "push_path", self._push)
        monkeypatch.setattr(remote_store, "delete_path", lambda p: self.deleted.append(Path(p)) or True)
        monkeypatch.setattr(remote_store, "await_restore", self._await)

    def _push(self, path):
        self.pushed.append(Path(path))
        return self.push_results.pop(0) if self.push_results else True

    def _await(self, timeout):
        self.restore_waits += 1
        return "completed"


# ── Cobertura de la persistencia de producción (sin variables ni rutas nuevas) ────────────────────────────────────────

def test_the_instances_live_under_a_directory_that_is_already_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COMMERCIAL_STRATEGY_RUNTIME_DIR", config.BASE_DIR / "commercial_strategy_runtime")
    path = cp._dir() / "x.json"
    assert path.parent.name == "presentations" and path.parent.parent.name == "commercial_strategy_runtime"
    assert remote_store._is_persisted_path(path)                                                # restore / mirror_up / push_path la cubren por `relative_to`
    assert "commercial_strategy_runtime" in config.PERSIST_PATHS or "SPACES_PERSIST_PATHS" in __import__("os").environ


def test_the_default_persist_paths_and_the_documented_production_value_include_the_runtime_directory():
    default_line = re.search(r'"SPACES_PERSIST_PATHS",\s*"([^"]+)"\s*"([^"]*)"', (REPO / "config.py").read_text(encoding="utf-8"))
    assert default_line and "commercial_strategy_runtime" in default_line.group(1) + default_line.group(2)
    documented = re.search(r"^SPACES_PERSIST_PATHS=(.+)$", (REPO / ".env.example").read_text(encoding="utf-8"), re.M)
    assert documented and "commercial_strategy_runtime" in documented.group(1).split(",")
    assert not re.search(r"PRESENTATION", (REPO / "config.py").read_text(encoding="utf-8")), "V1 no introduce configuración propia de Presentaciones"


def test_v1_introduces_no_new_environment_variables_or_allowlists():
    for name in ("webapp/services/commercial_presentations.py", "webapp/routers/commercial_presentations.py"):
        source = (REPO / name).read_text(encoding="utf-8")
        assert "os.environ" not in source and "os.getenv" not in source and "_env(" not in source, name


# ── Modos de persistencia ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_local_mode_writes_atomically_to_disk_and_never_touches_spaces(world, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(remote_store, "push_path", lambda p: (_ for _ in ()).throw(AssertionError("local no sincroniza")))
    pid = _copy(world, A)
    assert _save(world, A, pid, {"hero_lead": "local"}, 1).status_code == 200
    assert json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))["revision"] == 2
    assert not list(world.directory.glob(".tmp-*")), "no quedan temporales"


def test_read_only_mode_rejects_every_write_before_touching_the_disk(world, monkeypatch):
    pid = _copy(world, A)
    before = {p.name: p.read_bytes() for p in world.directory.iterdir()}
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete"
    r = _save(world, A, pid, {"hero_lead": "x"}, 1)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete"
    assert {p.name: p.read_bytes() for p in world.directory.iterdir()} == before                 # ni un archivo nuevo ni temporal
    assert world.client(A).get(f"/presentaciones/{pid}").status_code == 200                      # la LECTURA sigue funcionando


def test_remote_mode_pushes_the_exact_file_it_wrote(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    assert spaces.pushed == [world.directory / f"{pid}.json"]
    assert _save(world, A, pid, {"hero_lead": "remoto"}, 1).status_code == 200
    assert spaces.pushed[-1] == world.directory / f"{pid}.json" and len(spaces.pushed) == 2


# ── Fallos de persistencia: compensación y 503 ──────────────────────────────────────────────────────────────────────────────

def test_a_failed_push_on_save_restores_the_previous_version_and_reports_503(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    path = world.directory / f"{pid}.json"
    before = path.read_bytes()
    spaces.push_results = [False]
    r = _save(world, A, pid, {"hero_lead": "no persistido"}, 1)
    assert r.status_code == 503 and r.json()["reason"] == "durable_push_failed"
    assert path.read_bytes() == before                                                           # nunca queda una N+1 local sin confirmar
    assert "no persistido" not in world.client(A).get(f"/presentaciones/{pid}").text
    assert _save(world, A, pid, {"hero_lead": "ahora sí"}, 1).status_code == 200                 # la revisión no avanzó: se puede reintentar


def test_a_failed_push_on_copy_retries_once_then_removes_the_file_and_reports_an_unknown_outcome(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch, push_results=[False, False])
    r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 503 and r.json()["reason"] == "outcome_unknown"
    assert len(spaces.pushed) == 2 and len(spaces.deleted) == 1
    assert not list(world.directory.glob("*.json"))                                              # ni list() ni un mirror_up posterior pueden completar una alta fallida
    assert "x" not in [row for row in re.findall(r'data-presentation-id="([^"]+)"', world.client(A).get("/comercial/presentaciones").text) if row != SEED_ID]


def test_a_copy_whose_retry_succeeds_is_a_success(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch, push_results=[False, True])
    r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 200 and len(spaces.pushed) == 2 and len(list(world.directory.glob("*.json"))) == 1


def test_remote_mode_without_credentials_is_a_deterministic_config_error_and_is_not_retried(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch, enabled=False, push_results=[False, False])
    r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete"
    assert len(spaces.pushed) == 1 and not list(world.directory.glob("*.json"))


def test_remote_mode_without_credentials_does_not_leave_a_modified_instance_on_save(world, monkeypatch):
    pid = _copy(world, A)
    path = world.directory / f"{pid}.json"
    before = path.read_bytes()
    FakeSpaces(monkeypatch, enabled=False, push_results=[False])
    r = _save(world, A, pid, {"hero_lead": "x"}, 1)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete" and path.read_bytes() == before


def test_a_local_disk_failure_is_a_503_and_never_a_presumed_success(world, monkeypatch):
    def broken(path, payload):
        raise cp.PresentationStorageError("disco lleno")
    monkeypatch.setattr(cp, "_write_atomic", broken)
    r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 503 and r.json()["reason"] == "storage_error" and "disco lleno" not in r.text


# ── Restore de arranque, listado y archivos dañados ───────────────────────────────────────────────────────────────────────

def test_reads_and_writes_wait_for_the_startup_restore_when_spaces_is_enabled(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    waits = spaces.restore_waits
    cp.get(pid)
    cp.list_presentations()
    assert spaces.restore_waits >= waits + 2


def test_an_instance_restored_from_spaces_onto_an_empty_disk_is_visible(world):
    pid = _copy(world, A)
    raw = (world.directory / f"{pid}.json").read_bytes()
    for p in world.directory.iterdir():
        p.unlink()
    assert cp.list_presentations()[0]["presentation_id"] == SEED_ID and len(cp.list_presentations()) == 1
    (world.directory / f"{pid}.json").write_bytes(raw)                                           # lo que haría `remote_store.restore()`
    assert pid in [v["presentation_id"] for v in cp.list_presentations()]
    assert world.client(A).get(f"/presentaciones/{pid}").status_code == 200


def test_corrupt_files_and_temp_files_do_not_break_the_catalog(world):
    pid = _copy(world, A, title="Sana")
    world.directory.mkdir(parents=True, exist_ok=True)
    (world.directory / "11111111-1111-4111-8111-111111111111.json").write_text("{no json", encoding="utf-8")
    (world.directory / "22222222-2222-4222-8222-222222222222.json").write_text(json.dumps({"presentation_id": "otro"}), encoding="utf-8")
    (world.directory / ".tmp-abc.json").write_text("{}", encoding="utf-8")
    (world.directory / "notas.txt").write_text("x", encoding="utf-8")
    ids = [v["presentation_id"] for v in cp.list_presentations()]
    assert ids == [SEED_ID, pid]
    assert world.client(A).get("/comercial/presentaciones").status_code == 200
    assert world.client(A).get("/presentaciones/11111111-1111-4111-8111-111111111111").status_code == 404


# ── Concurrencia: la revisión optimista bajo el lock ─────────────────────────────────────────────────────────────────────

def test_two_concurrent_saves_with_the_same_revision_yield_exactly_one_winner(world):
    pid = _copy(world, A)
    results, barrier = [], threading.Barrier(2)

    def attempt(text):
        barrier.wait()
        try:
            cp.save(pid, {"hero_lead": text}, expected_revision=1, updated_by_id=A, updated_by_label="A")
            results.append("ok")
        except cp.StaleRevision as exc:
            results.append(("stale", exc.current_revision))

    threads = [threading.Thread(target=attempt, args=(t,)) for t in ("uno", "dos")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(map(str, results)) == sorted(["ok", str(("stale", 2))])
    assert json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))["revision"] == 2


def test_the_audit_trail_records_who_changed_what_without_changing_the_creator(world):
    pid = _copy(world, A, title="Auditada")
    assert _save(world, A, pid, {"hero_lead": "x"}, 1).status_code == 200
    stored = json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))
    assert stored["created_by_id"] == A and stored["owner_id"] == A and stored["updated_by_id"] == A
    assert stored["copied_from"]["presentation_id"] == SEED_ID and stored["created_at"] <= stored["updated_at"]


# ── Eliminar: el mismo contrato (read-only · local · remote con compensación) ───────────────────────────────────────────────────

def test_delete_in_local_mode_removes_the_file_and_never_touches_spaces(world, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(remote_store, "delete_path", lambda p: (_ for _ in ()).throw(AssertionError("local no sincroniza")))
    pid = _copy(world, A)
    assert _delete(world, A, pid).status_code == 200 and world.files() == {}


def test_delete_in_read_only_mode_is_rejected_before_touching_the_disk(world, monkeypatch):
    pid = _copy(world, A)
    before = world.files()
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    r = _delete(world, A, pid)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete"
    assert world.files() == before


def test_delete_in_remote_mode_removes_the_same_object_in_spaces(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    assert _delete(world, A, pid).status_code == 200
    assert spaces.deleted == [world.directory / f"{pid}.json"] and world.files() == {}


def test_a_failed_remote_delete_is_retried_once_then_the_local_file_is_restored_and_it_reports_503(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    before = world.files()
    calls = []
    monkeypatch.setattr(remote_store, "delete_path", lambda p: calls.append(Path(p)) or False)
    r = _delete(world, A, pid)
    assert r.status_code == 503 and r.json()["reason"] == "durable_push_failed" and "sigue en el catálogo" in r.json()["message"]
    assert len(calls) == 2 and world.files() == before                                         # un reintento; el archivo vuelve idéntico (nunca borrada en un lado y viva en el otro)
    assert pid in world.client(A).get("/comercial/presentaciones").text


def test_a_remote_delete_whose_retry_succeeds_is_a_success(world, monkeypatch):
    FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    results = iter([False, True])
    monkeypatch.setattr(remote_store, "delete_path", lambda p: next(results))
    assert _delete(world, A, pid).status_code == 200 and world.files() == {}


def test_remote_mode_without_credentials_does_not_delete_anything(world, monkeypatch):
    spaces = FakeSpaces(monkeypatch)
    pid = _copy(world, A)
    before = world.files()
    spaces.enabled = False
    r = _delete(world, A, pid)
    assert r.status_code == 503 and r.json()["reason"] == "config_incomplete" and world.files() == before and spaces.deleted == []
