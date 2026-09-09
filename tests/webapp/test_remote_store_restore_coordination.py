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


# ============================================================
# push_path() — Capital Humano Phase 3.3 (Remote Store Durability Outcome
# corrective): `True` debe significar EXACTAMENTE "el PUT remoto quedó
# establecido", `False` EXACTAMENTE "no se estableció durabilidad remota"
# — nunca "el PUT succeeded pero un bookkeeping local opcional falló".
# Sin cobertura propia antes de esta fase (los tests de Profile solo
# mockeaban `push_path` como caja negra, nunca probaron SU lógica interna).
# ============================================================

class _FakeUploadClient:
    """Doble mínimo para push_path()/mirror_up(): sólo `upload_file()`,
    lo único que estas rutas tocan del cliente boto3 real."""

    def __init__(self, *, fail_upload_for=None):
        self.fail_upload_for = fail_upload_for or set()
        self.uploaded: list[str] = []

    def upload_file(self, filename, bucket, key):
        if key in self.fail_upload_for:
            raise RuntimeError("simulated upload failure")
        self.uploaded.append(key)


def _write_persisted_file(tmp_path, dirname: str, relname: str, content: str = "x"):
    path = tmp_path / dirname / relname
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_push_path_disabled_returns_false_and_never_uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["reporting_cache"])
    fake_client = _FakeUploadClient()
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)
    path = _write_persisted_file(tmp_path, "reporting_cache", "a.json")

    assert remote_store.push_path(path) is False
    assert fake_client.uploaded == []


def test_push_path_real_upload_failure_returns_false(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    path = _write_persisted_file(tmp_path, "reporting_cache", "a.json")
    key = f"reporting_cache/{path.name}"
    fake_client = _FakeUploadClient(fail_upload_for={key})
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    assert remote_store.push_path(path) is False


def test_push_path_success_with_signature_bookkeeping_success_returns_true(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    path = _write_persisted_file(tmp_path, "reporting_cache", "a.json")
    key = f"reporting_cache/{path.name}"
    fake_client = _FakeUploadClient()
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    assert remote_store.push_path(path) is True
    assert fake_client.uploaded == [key]
    assert remote_store._signatures[key] == remote_store._sig(path)


def test_push_path_success_with_signature_bookkeeping_failure_still_returns_true(monkeypatch, tmp_path):
    # EL test crítico de esta fase (§11): el PUT remoto se completa, pero
    # el bookkeeping de firma POSTERIOR (_sig -> path.stat()) explota --
    # push_path() debe seguir devolviendo True, porque la durabilidad
    # remota YA está establecida en ese punto y `_signatures` es sólo una
    # optimización de `mirror_up()`, nunca la fuente de verdad.
    _enable_persistence(monkeypatch, tmp_path)
    path = _write_persisted_file(tmp_path, "reporting_cache", "a.json")
    fake_client = _FakeUploadClient()
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    def _boom_sig(_path):
        raise OSError("simulated stat failure right after a successful PUT")
    monkeypatch.setattr(remote_store, "_sig", _boom_sig)

    assert remote_store.push_path(path) is True
    assert fake_client.uploaded == [f"reporting_cache/{path.name}"]  # el PUT sí ocurrió


def test_push_path_bookkeeping_failure_leaves_no_stale_signature(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    path = _write_persisted_file(tmp_path, "reporting_cache", "a.json")
    key = f"reporting_cache/{path.name}"
    monkeypatch.setattr(remote_store, "_get_client", lambda: _FakeUploadClient())

    def _boom_sig(_path):
        raise OSError("simulated stat failure")
    monkeypatch.setattr(remote_store, "_sig", _boom_sig)

    remote_store.push_path(path)
    assert key not in remote_store._signatures


def test_push_path_outside_persisted_dirs_returns_false(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    monkeypatch.setattr(remote_store, "_get_client", lambda: _FakeUploadClient())
    outside = tmp_path / "not-a-persisted-dir" / "a.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("x", encoding="utf-8")

    assert remote_store.push_path(outside) is False


def test_mirror_up_reuploads_without_data_loss_when_signature_bookkeeping_was_missed(monkeypatch, tmp_path):
    # §12: si push_path() confirmó el PUT pero no pudo registrar la firma
    # (missing entry en `_signatures`), el próximo mirror_up() debe
    # simplemente re-subir ese archivo (costo: una subida redundante del
    # mismo contenido) -- nunca perder el archivo ni fallar.
    _enable_persistence(monkeypatch, tmp_path)
    path = _write_persisted_file(tmp_path, "reporting_cache", "a.json")
    key = f"reporting_cache/{path.name}"
    fake_client = _FakeUploadClient()
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    # push_path() ya subió el archivo, pero SIN registrar la firma (simula
    # exactamente el escenario del test anterior).
    fake_client.uploaded.append(key)
    assert key not in remote_store._signatures

    uploaded_count = remote_store.mirror_up()

    assert uploaded_count == 1  # re-subido, sin firma previa que lo salteara
    assert fake_client.uploaded.count(key) == 2  # la subida original + la redundante
    assert path.read_text(encoding="utf-8") == "x"  # el contenido local nunca se tocó
    assert remote_store._signatures[key] == remote_store._sig(path)  # ahora sí queda registrada


# ============================================================
# Capital Humano Phase 3.5 §12/§23 — mirror_up() debe RETOMAR (nunca
# saltear) un Profile restaurado por un rollback de update() cuya
# compensación tampoco pudo confirmarse — probado, no asumido, exactamente
# como pide el ticket ("Test it").
# ============================================================

def test_mirror_up_retries_a_rollback_restored_profile_after_unconfirmed_compensation(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["human-capital"])
    from webapp.services import profile_repository

    fake_client = _FakeUploadClient()
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake_client)

    minimal = {
        "first_name": "Ana", "last_name": "Pérez", "email": None, "phone": None,
        "linkedin_url": None, "location": None, "profile_skills": None,
        "seniority": None, "source": None, "tags": None, "notes": None,
        "potential_rating": None, "potential_mark": None,
    }
    saved = profile_repository.create(dict(minimal))
    path = profile_repository._profile_path(saved["profile_id"])
    key = f"human-capital/profiles/{path.name}"
    assert remote_store._signatures.get(key) == remote_store._sig(path)  # create() ya la registró

    # Fuerza que TANTO el push de B como la compensación de A fallen —
    # exactamente el escenario "rollback cannot be confirmed" (§2B).
    fake_client.fail_upload_for = {key}
    with pytest.raises(profile_repository.ProfileRemoteOutcomeUnknownError):
        profile_repository.update(saved["profile_id"], 1, dict(minimal, first_name="Bruno"))

    # El local YA fue restaurado a A (mtime NUEVO vía el atomic-write del
    # rollback) — su firma cacheada sigue siendo la de la subida ORIGINAL
    # (create()), que ya no coincide con el mtime post-rollback.
    assert remote_store._signatures.get(key) != remote_store._sig(path)

    # Deja de fallar (simula que la conectividad se restablece) y corre el
    # próximo ciclo de mirror_up() — debe RE-SUBIR A, nunca saltearlo por
    # una firma stale, llevando el sistema de vuelta al último estado
    # conocido (Phase 3.5 §12, "driving the system back toward the last
    # known committed state").
    fake_client.fail_upload_for = set()
    uploaded = remote_store.mirror_up()

    assert uploaded == 1
    assert key in fake_client.uploaded
    assert profile_repository.get(saved["profile_id"])["first_name"] == "Ana"  # local sigue en A
    assert remote_store._signatures[key] == remote_store._sig(path)  # firma ahora al día con A
