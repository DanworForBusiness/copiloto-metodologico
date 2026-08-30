"""
test_remote_store_restore_coordination.py — RESTORE-COMPLETION coordination
added to core/remote_store.py (Integral Cold Start Fase 1, owner ticket, ver
HANDOFF — §6 "the smallest coordination primitive necessary so Reporting
knows whether its durable cache restore is not applicable / in progress /
completed / failed").

No existía cobertura de tests para remote_store.py antes de este corrective
— este archivo agrega la mínima necesaria para probar la MÁQUINA DE ESTADOS
nueva (`restore_status`/`await_restore`/`restore_elapsed_ms`) sin tocar/
duplicar la lógica de listado/descarga/subida real de S3 (mockeada acá en
el límite `_get_client`, el mismo punto de inyección que el módulo ya
expone). §12 REMOTE_STORE SAFETY: confirma que los directorios YA
persistidos (catalog_local/case_library/feedback_log/activity_log/reports)
siguen intactos y que `reporting_cache` ahora es parte del contrato por
default, sin romper nada existente."""
from __future__ import annotations

import threading
import time

import pytest

import config
from core import remote_store


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    monkeypatch.setattr(remote_store, "_restore_elapsed_ms", None)
    remote_store._restore_done.clear()
    monkeypatch.setattr(remote_store, "_signatures", {})


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeClient:
    """Doble mínimo del cliente boto3 real — sólo lo que `restore()` toca:
    `get_paginator("list_objects_v2").paginate(...)` y `download_file(...)`."""

    def __init__(self, objects_by_prefix, *, fail_download_for=None):
        self._objects_by_prefix = objects_by_prefix
        self._fail_download_for = fail_download_for or set()
        self.downloaded: list[str] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        contents = self._objects_by_prefix.get(Prefix, [])
        return [{"Contents": [{"Key": k} for k in contents]}]

    def download_file(self, bucket, key, dest):
        if key in self._fail_download_for:
            raise RuntimeError("simulated download failure")
        self.downloaded.append(key)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("x")


def _enable_persistence(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SPACES_ENDPOINT", "https://fake.digitaloceanspaces.com")
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_KEY", "fake-key")
    monkeypatch.setattr(config, "SPACES_SECRET", "fake-secret")
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["reporting_cache"])


# ============================================================
# Estado — not_applicable / completed / failed
# ============================================================

def test_disabled_stays_not_applicable_and_returns_zero(monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    assert remote_store.restore() == 0
    assert remote_store.restore_status() == "not_applicable"
    assert remote_store.restore_elapsed_ms() is None


def test_successful_restore_marks_completed_and_records_elapsed(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    fake_client = _FakeClient({"reporting_cache/": ["reporting_cache/a.json", "reporting_cache/b.json"]})
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    downloaded = remote_store.restore()

    assert downloaded == 2
    assert remote_store.restore_status() == "completed"
    assert remote_store.restore_elapsed_ms() is not None
    assert remote_store._restore_done.is_set()


def test_client_init_failure_marks_failed_not_hung(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)

    def _boom():
        raise RuntimeError("boto3 unavailable")

    monkeypatch.setattr(remote_store, "_get_client", _boom)

    assert remote_store.restore() == 0
    assert remote_store.restore_status() == "failed"
    assert remote_store._restore_done.is_set()


def test_partial_download_failure_still_completes(monkeypatch, tmp_path):
    # Un objeto individual fallando no debe tumbar el restore completo —
    # comportamiento YA existente (best-effort), sólo confirmado acá junto
    # con el nuevo tracking de estado.
    _enable_persistence(monkeypatch, tmp_path)
    fake_client = _FakeClient(
        {"reporting_cache/": ["reporting_cache/a.json", "reporting_cache/b.json"]},
        fail_download_for={"reporting_cache/b.json"},
    )
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    downloaded = remote_store.restore()

    assert downloaded == 1  # sólo "a.json" bajó con éxito
    assert remote_store.restore_status() == "completed"  # best-effort: sigue "completed", no "failed"


# ============================================================
# await_restore — no polling, join real, timeout acotado
# ============================================================

def test_await_restore_returns_immediately_when_not_applicable():
    started = time.perf_counter()
    result = remote_store.await_restore(timeout=5.0)
    assert result == "not_applicable"
    assert (time.perf_counter() - started) < 0.5


def test_await_restore_joins_a_real_background_restore(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    fake_client = _FakeClient({"reporting_cache/": ["reporting_cache/a.json"]})

    def _slow_get_client():
        time.sleep(0.15)
        return fake_client

    monkeypatch.setattr(remote_store, "_get_client", _slow_get_client)

    t = threading.Thread(target=remote_store.restore)
    t.start()
    time.sleep(0.02)  # deja que restore() marque "in_progress" antes de esperar
    started = time.perf_counter()
    result = remote_store.await_restore(timeout=5.0)
    elapsed = time.perf_counter() - started
    t.join(timeout=3)

    assert result == "completed"
    assert 0.05 <= elapsed < 3.0, "no se unió al restore real en curso"


def test_await_restore_gives_up_after_timeout(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    started = time.perf_counter()
    result = remote_store.await_restore(timeout=0.1)
    elapsed = time.perf_counter() - started
    assert result == "in_progress"  # sigue en curso, el caller degrada solo
    assert elapsed < 1.0


# ============================================================
# §12 — reporting_cache es parte del contrato PERSIST_PATHS por default,
# sin desplazar los directorios existentes
# ============================================================

def test_reporting_cache_is_part_of_default_persist_paths():
    assert "reporting_cache" in config.PERSIST_PATHS
    for existing in ("catalog_local", "case_library", "feedback_log", "activity_log", "reports"):
        assert existing in config.PERSIST_PATHS
