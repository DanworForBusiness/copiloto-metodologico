"""
test_commercial_strategy_repository_restore_race.py — Startup Restore /
Cache Race Gate (ver HANDOFF).

`webapp.main.lifespan()` arranca `remote_store.restore()` como background
task SIN esperarlo (la app queda lista para servir HTTP de inmediato, para
que /health no falle durante un deploy). `CommercialStrategyRepository`
puebla `self._cache` en el PRIMER `load()`/`save()` y nunca vuelve a leer
disco — sin coordinación, una request que llega ANTES de que el restore
termine cachearía el SEED committed (revision 1) para el resto de la vida
del proceso, ignorando para siempre el runtime durable que aparece
segundos después (y un Save posterior pisaría ese runtime con un
documento construido sobre el seed stale).

Fix: `_ensure_restored()` (ver commercial_strategy_repository.py) reusa
LITERAL `remote_store.await_restore()` — el MISMO primitivo que ya resolvía
esta clase de problema para `reporting_time_logs_durable.ensure_restored()`
(ver ese módulo) — antes de la primera lectura de disco en `load()`/`save()`.
Este archivo prueba la reproducción controlada del gate: sin el fix, el
test B fallaría (la primera lectura vería revision 1 para siempre); con el
fix, `load()`/`save()` BLOQUEAN hasta que el restore en curso termina."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
import yaml

import config
from core import remote_store
from webapp.services import commercial_strategy_repository as repo_module


@pytest.fixture(autouse=True)
def _isolate_remote_store_state(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    monkeypatch.setattr(remote_store, "_restore_elapsed_ms", None)
    remote_store._restore_done.clear()


def _seed_path() -> Path:
    return (
        Path(__file__).resolve().parents[2] / "webapp" / "services"
        / "commercial_strategy_data" / "strategy.yaml"
    )


def _write_runtime_doc(path: Path, *, revision: int, lead: str) -> None:
    doc = yaml.safe_load(_seed_path().read_text(encoding="utf-8"))
    doc["revision"] = revision
    doc["hero"]["lead"] = lead
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _complete_restore_after_delay(runtime_path: Path, *, delay: float, revision: int, lead: str):
    def _run():
        time.sleep(delay)
        _write_runtime_doc(runtime_path, revision=revision, lead=lead)
        with remote_store._restore_state_lock:
            remote_store._restore_state = "completed"
        remote_store._restore_done.set()

    t = threading.Thread(target=_run)
    t.start()
    return t


# ============================================================
# A — restore YA terminado (runtime presente) antes del primer load()
# ============================================================

def test_durable_runtime_already_restored_before_first_load_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    runtime_path = tmp_path / "strategy.yaml"
    _write_runtime_doc(runtime_path, revision=8, lead="Ya restaurado antes del primer load")
    monkeypatch.setattr(remote_store, "_restore_state", "completed")
    remote_store._restore_done.set()

    repo = repo_module.CommercialStrategyRepository(seed_path=_seed_path(), runtime_path=runtime_path)
    doc = repo.load()

    assert doc["revision"] == 8
    assert doc["hero"]["lead"] == "Ya restaurado antes del primer load"


# ============================================================
# B — reproducción controlada del race: load() llega DURANTE el restore
# ============================================================

def test_load_during_in_flight_restore_blocks_and_sees_the_restored_document(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    runtime_path = tmp_path / "strategy.yaml"
    repo = repo_module.CommercialStrategyRepository(seed_path=_seed_path(), runtime_path=runtime_path)

    # El runtime local TODAVÍA no existe (como en un container recién
    # arrancado) y el restore está "in_progress" — exactamente la ventana
    # que el gate señala como peligrosa.
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    t = _complete_restore_after_delay(runtime_path, delay=0.15, revision=8, lead="Restaurado durante el load")

    started = time.perf_counter()
    doc = repo.load()
    elapsed = time.perf_counter() - started
    t.join(timeout=3)

    assert elapsed >= 0.1, "load() no esperó al restore en curso — habría cacheado el seed para siempre"
    assert doc["revision"] == 8
    assert doc["hero"]["lead"] == "Restaurado durante el load"

    # La caché en memoria ya quedó con el contenido restaurado — ninguna
    # lectura posterior vuelve a caer al seed ni relee disco.
    assert repo.load()["revision"] == 8


def test_load_during_in_flight_restore_gives_up_after_timeout_without_hanging_forever(monkeypatch, tmp_path):
    # Si el restore NUNCA termina (falla silenciosa, cuelgue de red), el
    # timeout acotado de `_ensure_restored` evita una request colgada
    # indefinidamente — degrada al seed en vez de bloquear para siempre.
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(repo_module, "_RESTORE_WAIT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    runtime_path = tmp_path / "strategy.yaml"
    repo = repo_module.CommercialStrategyRepository(seed_path=_seed_path(), runtime_path=runtime_path)

    started = time.perf_counter()
    doc = repo.load()
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    assert doc["revision"] == 1  # cae al seed — sin runtime local, es el único documento disponible


# ============================================================
# C — persistencia deshabilitada o sin runtime durable: bootstrap normal,
# sin espera (ver `remote_store.await_restore` — "not_applicable" instantáneo)
# ============================================================

def test_persistence_disabled_seed_bootstrap_is_instant(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    repo = repo_module.CommercialStrategyRepository(seed_path=_seed_path(), runtime_path=tmp_path / "strategy.yaml")

    started = time.perf_counter()
    doc = repo.load()
    elapsed = time.perf_counter() - started

    assert doc["revision"] == 1
    assert elapsed < 0.5


def test_persistence_enabled_but_restore_already_completed_with_no_runtime_seed_bootstrap_is_instant(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(remote_store, "_restore_state", "completed")
    remote_store._restore_done.set()
    repo = repo_module.CommercialStrategyRepository(seed_path=_seed_path(), runtime_path=tmp_path / "strategy.yaml")

    started = time.perf_counter()
    doc = repo.load()
    elapsed = time.perf_counter() - started

    assert doc["revision"] == 1  # sin runtime durable, el seed sigue siendo bootstrap válido
    assert elapsed < 0.5


# ============================================================
# D — Save safety: el primer Save tras un restart usa la revision
# restaurada, nunca la del seed
# ============================================================

def test_first_save_after_restart_is_based_on_the_restored_revision_not_the_seed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(remote_store, "push_path", lambda path: True)  # push durable simulado OK
    runtime_path = tmp_path / "strategy.yaml"
    repo = repo_module.CommercialStrategyRepository(seed_path=_seed_path(), runtime_path=runtime_path)

    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    t = _complete_restore_after_delay(runtime_path, delay=0.1, revision=8, lead="Restaurado")

    # Un Save que llega ANTES de que termine el restore, con expected_revision=8
    # (lo que el cliente hubiera visto de haber podido leer el runtime restaurado
    # a tiempo) — si `save()` no esperara el restore, leería el seed (revision 1)
    # y este `expected_revision=8` fallaría como "stale" (o peor, pisaría el
    # runtime restaurado con un documento construido sobre el seed).
    saved = repo.save({}, expected_revision=8, updated_by_id="u1", updated_by_label="Test")
    t.join(timeout=3)

    assert saved["revision"] == 9
    assert saved["hero"]["lead"] == "Restaurado"  # el candidato partió del runtime restaurado, no del seed
