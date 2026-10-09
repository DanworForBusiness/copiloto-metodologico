"""
test_persistence_isolation.py — aislamiento localhost/producción de la persistencia remota y de la Case Library.

Contratos que fija (ninguna prueba toca un bucket real: el cliente S3 es un doble en memoria):
  * ``DANWOR_PERSISTENCE`` = local (default) | read-only | remote; inválido FALLA; nunca se infiere de credenciales;
  * ``local`` con credenciales productivas NO realiza operación remota alguna (ni construye el cliente S3);
  * ``read-only`` permite lectura remota explícita y bloquea toda escritura/borrado;
  * ``remote`` conserva el comportamiento productivo (restore, mirror_up, write-through);
  * ``restore`` NO sobrescribe archivos locales por defecto (``overwrite=True`` es el reemplazo explícito);
  * ``mirror_up`` no corre con restore en curso/fallido/incompleto ni sube archivos locales sin reconciliar;
  * el arranque/cierre de la app (lifespan) solo restaura/sincroniza en modo ``remote``;
  * la ``case_library`` real jamás recibe casos desde tests; navegar por GET no crea casos ni dispara escrituras remotas.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import remote_store

REPO = Path(__file__).resolve().parent.parent


# ── doble de S3 ─────────────────────────────────────────────────────────────
class FakeS3:
    """Cliente S3 mínimo en memoria: registra TODA operación (lecturas y escrituras)."""

    def __init__(self, objects: dict[str, bytes] | None = None, *, fail_download: set[str] | None = None):
        self.objects = dict(objects or {})
        self.fail_download = fail_download or set()
        self.calls: list[tuple] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        self.calls.append(("list", Prefix))
        contents = [{"Key": k, "Size": len(v), "ETag": '"%s"' % hashlib.md5(v).hexdigest()} for k, v in sorted(self.objects.items()) if k.startswith(Prefix)]
        return [{"Contents": contents}]

    def download_file(self, bucket, key, dest):
        self.calls.append(("download", key))
        if key in self.fail_download:
            raise RuntimeError("descarga simulada fallida")
        Path(dest).write_bytes(self.objects[key])

    def upload_file(self, path, bucket, key):
        self.calls.append(("upload", key))
        self.objects[key] = Path(path).read_bytes()

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Key))
        self.objects.pop(Key, None)

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Key))
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    @property
    def writes(self):
        return [c for c in self.calls if c[0] in ("upload", "delete")]


def set_mode(monkeypatch, tmp_path, mode, *, credentials=True, client=None):
    """Configura el modo como lo haría ``config.py`` (credenciales y modo independientes) y apunta ``case_library`` a un tmp."""
    monkeypatch.setattr(config, "SPACES_ENDPOINT", "https://fake.digitaloceanspaces.com" if credentials else "")
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket" if credentials else "")
    monkeypatch.setattr(config, "SPACES_KEY", "fake-key" if credentials else "")
    monkeypatch.setattr(config, "SPACES_SECRET", "fake-secret" if credentials else "")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "SPACES_CONFIGURED", credentials)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", mode)
    monkeypatch.setattr(config, "PERSIST_ENABLED", credentials and mode == "remote")
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", credentials and mode == "read-only")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["case_library"])
    monkeypatch.setattr(remote_store, "_signatures", {})
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    remote_store._restore_done.clear()
    monkeypatch.setattr(remote_store, "_restore_degraded", False)
    monkeypatch.setattr(remote_store, "_unreconciled", set())
    if client is None:
        def boom():
            raise AssertionError("NO debía construirse el cliente S3 (operación remota no autorizada)")
        monkeypatch.setattr(remote_store, "_get_client", boom)
    else:
        monkeypatch.setattr(remote_store, "_get_client", lambda: client)
    (tmp_path / "case_library").mkdir(exist_ok=True)
    return tmp_path / "case_library"


# ── contrato de configuración ───────────────────────────────────────────────
def test_parse_persistence_mode_default_normalisation_and_invalid_values():
    assert config.parse_persistence_mode(None) == config.parse_persistence_mode("") == config.parse_persistence_mode("   ") == "local"
    assert config.parse_persistence_mode(" Remote ") == "remote" and config.parse_persistence_mode("READ-ONLY") == "read-only" and config.parse_persistence_mode("local") == "local"
    for bad in ("remoto", "true", "1", "prod", "production", "readonly", "read_only", "on", "yes", "localhost"):
        with pytest.raises(ValueError, match="DANWOR_PERSISTENCE inválido"):
            config.parse_persistence_mode(bad)


def _config_under(env_extra: dict) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SPACES_") and k != "DANWOR_PERSISTENCE"}
    clean.update({"SPACES_ENDPOINT": "", "SPACES_BUCKET": "", "SPACES_KEY": "", "SPACES_SECRET": "", "DANWOR_PERSISTENCE": "", "PYTHONIOENCODING": "utf-8"})
    clean.update(env_extra)
    code = "import config; print(config.PERSISTENCE_MODE, config.SPACES_CONFIGURED, config.PERSIST_ENABLED, config.PERSIST_READ_ONLY)"
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=clean, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


CREDS = {"SPACES_ENDPOINT": "https://x", "SPACES_BUCKET": "b", "SPACES_KEY": "k", "SPACES_SECRET": "s"}


@pytest.mark.parametrize("extra,expected", [
    (CREDS, "local True False False"),                                          # credenciales productivas SIN modo ⇒ localhost no habilita nada
    ({**CREDS, "DANWOR_PERSISTENCE": "local"}, "local True False False"),
    ({**CREDS, "DANWOR_PERSISTENCE": "read-only"}, "read-only True False True"),
    ({**CREDS, "DANWOR_PERSISTENCE": "remote"}, "remote True True False"),
    ({"DANWOR_PERSISTENCE": "remote"}, "remote False False False"),             # modo remote SIN credenciales ⇒ sigue sin haber persistencia
    ({"DANWOR_PERSISTENCE": "read-only"}, "read-only False False False"),
    ({}, "local False False False"),
])
def test_config_modes_under_real_environments(extra, expected):
    r = _config_under(extra)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.strip() == expected


def test_invalid_mode_fails_loudly_at_config_import_and_never_enables_remote():
    r = _config_under({**CREDS, "DANWOR_PERSISTENCE": "remoto"})
    assert r.returncode != 0 and "DANWOR_PERSISTENCE inválido" in r.stderr and r.stdout.strip() == ""


# ── local: credenciales presentes, cero operaciones remotas ─────────────────
def test_local_mode_with_production_credentials_performs_no_remote_operation(monkeypatch, tmp_path):
    case_dir = set_mode(monkeypatch, tmp_path, "local")                          # `_get_client` revienta si se lo construye
    f = case_dir / "case_x.yaml"
    f.write_text("a: 1", encoding="utf-8")
    assert (remote_store.enabled(), remote_store.read_enabled(), remote_store.write_enabled()) == (False, False, False)
    assert remote_store.restore() == 0 and remote_store.restore(overwrite=True) == 0
    assert remote_store.mirror_up() == 0
    assert remote_store.push_path(f) is False and remote_store.delete_path(f) is False
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.read_object_bytes(f)
    assert remote_store.restore_status() == "not_applicable" and f.read_text(encoding="utf-8") == "a: 1"


# ── read-only ───────────────────────────────────────────────────────────────
def test_read_only_allows_explicit_reads_and_blocks_every_write(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_remote.yaml": b"remote: 1"})
    case_dir = set_mode(monkeypatch, tmp_path, "read-only", client=s3)
    assert (remote_store.enabled(), remote_store.read_enabled(), remote_store.write_enabled()) == (False, True, False)
    assert remote_store.restore() == 1 and (case_dir / "case_remote.yaml").read_bytes() == b"remote: 1"      # lectura explícita: OK
    assert remote_store.read_object_bytes(case_dir / "case_remote.yaml") == b"remote: 1"
    local = case_dir / "case_local.yaml"
    local.write_text("local: 1", encoding="utf-8")
    assert remote_store.mirror_up() == 0 and remote_store.push_path(local) is False and remote_store.delete_path(case_dir / "case_remote.yaml") is False
    assert s3.writes == [] and "case_local.yaml" not in " ".join(k for k in s3.objects) and "case_library/case_remote.yaml" in s3.objects


# ── remote: comportamiento productivo intacto ───────────────────────────────
def test_remote_mode_keeps_the_production_behaviour(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_a.yaml": b"a: 1"})
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    assert (remote_store.enabled(), remote_store.read_enabled(), remote_store.write_enabled()) == (True, True, True)
    assert remote_store.restore() == 1 and remote_store.restore_status() == "completed"           # FS vacío (producción): baja todo
    new = case_dir / "case_new.yaml"
    new.write_text("n: 1", encoding="utf-8")
    assert remote_store.mirror_up() == 1 and s3.objects["case_library/case_new.yaml"] == b"n: 1"      # sync sube lo nuevo
    other = case_dir / "case_wt.yaml"
    other.write_text("w: 1", encoding="utf-8")
    assert remote_store.push_path(other) is True and "case_library/case_wt.yaml" in s3.objects      # write-through
    assert remote_store.delete_path(other) is True and "case_library/case_wt.yaml" not in s3.objects


# ── restore: no sobrescribe por defecto ─────────────────────────────────────
def test_restore_does_not_overwrite_existing_local_files_by_default(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_a.yaml": b"REMOTE-OLD", "case_library/case_b.yaml": b"b: 1"})
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    mine = case_dir / "case_a.yaml"
    mine.write_bytes(b"LOCAL-NEWER-EDIT")
    os.utime(mine, (1_700_000_000, 1_700_000_000))
    before = mine.stat().st_mtime_ns
    assert remote_store.restore() == 1                                                          # solo bajó case_b (no existía)
    assert mine.read_bytes() == b"LOCAL-NEWER-EDIT" and mine.stat().st_mtime_ns == before       # intacto, ni contenido ni mtime
    assert (case_dir / "case_b.yaml").read_bytes() == b"b: 1"
    assert remote_store.last_restore_report["skipped_existing"] == 1 and "case_library/case_a.yaml" in remote_store._unreconciled
    assert ("download", "case_library/case_a.yaml") not in s3.calls


def test_restore_overwrite_is_the_explicit_controlled_replacement(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_a.yaml": b"REMOTE"})
    case_dir = set_mode(monkeypatch, tmp_path, "read-only", client=s3)
    (case_dir / "case_a.yaml").write_bytes(b"LOCAL")
    remote_store.restore()
    assert (case_dir / "case_a.yaml").read_bytes() == b"LOCAL"
    assert remote_store.restore(overwrite=True) == 1
    assert (case_dir / "case_a.yaml").read_bytes() == b"REMOTE" and not remote_store._unreconciled and remote_store.last_restore_report["overwrite"] is True


def test_restore_treats_an_identical_local_file_as_reconciled_without_downloading(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_a.yaml": b"same"})
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    (case_dir / "case_a.yaml").write_bytes(b"same")
    assert remote_store.restore() == 0 and remote_store.last_restore_report["reconciled"] == 1 and not remote_store._unreconciled
    assert ("download", "case_library/case_a.yaml") not in s3.calls and remote_store.mirror_up() == 0 and s3.writes == []


def test_unreconciled_local_files_are_never_uploaded_by_mirror_up(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_a.yaml": b"REMOTE-PROD"})
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    (case_dir / "case_a.yaml").write_bytes(b"LOCAL-STALE")
    remote_store.restore()
    (case_dir / "case_new.yaml").write_bytes(b"legit-new")
    assert remote_store.mirror_up() == 1                                                       # sube lo nuevo...
    assert s3.objects["case_library/case_a.yaml"] == b"REMOTE-PROD"                             # ...y JAMÁS pisa el objeto productivo con el local sin reconciliar


# ── mirror_up no corre sobre un restore incompleto ──────────────────────────
@pytest.mark.parametrize("state", ["in_progress", "failed"])
def test_mirror_up_is_blocked_while_restore_is_in_progress_or_failed(monkeypatch, tmp_path, state):
    s3 = FakeS3()
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    (case_dir / "case_x.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(remote_store, "_restore_state", state)
    assert remote_store.sync_blocked_reason() and remote_store.mirror_up() == 0 and s3.writes == []
    monkeypatch.setattr(remote_store, "_restore_state", "completed")
    assert remote_store.sync_blocked_reason() is None and remote_store.mirror_up() == 1


def test_a_partial_restore_failure_keeps_the_historic_completed_status_but_blocks_sync(monkeypatch, tmp_path):
    s3 = FakeS3({"case_library/case_a.yaml": b"a", "case_library/case_b.yaml": b"b"}, fail_download={"case_library/case_b.yaml"})
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    assert remote_store.restore() == 1
    assert remote_store.restore_status() == "completed" and remote_store._restore_degraded      # los lectores no se cuelgan (contrato histórico)
    (case_dir / "case_new.yaml").write_text("n", encoding="utf-8")
    assert remote_store.mirror_up() == 0 and s3.writes == []                                    # pero NO se sincroniza sobre un restore incompleto
    s3.fail_download.clear()
    assert remote_store.restore(overwrite=True) == 2 and not remote_store._restore_degraded      # un restore completo posterior lo habilita
    assert remote_store.mirror_up() == 1


def test_a_listing_failure_also_blocks_sync(monkeypatch, tmp_path):
    class BadList(FakeS3):
        def paginate(self, Bucket, Prefix):
            raise RuntimeError("listado caído")
    s3 = BadList()
    case_dir = set_mode(monkeypatch, tmp_path, "remote", client=s3)
    (case_dir / "case_x.yaml").write_text("x", encoding="utf-8")
    remote_store.restore()
    assert remote_store._restore_degraded and remote_store.mirror_up() == 0 and s3.writes == []


def test_client_init_failure_in_restore_blocks_sync(monkeypatch, tmp_path):
    set_mode(monkeypatch, tmp_path, "remote", client=FakeS3())
    monkeypatch.setattr(remote_store, "_get_client", lambda: (_ for _ in ()).throw(RuntimeError("sin boto3")))
    assert remote_store.restore() == 0 and remote_store.restore_status() == "failed" and remote_store.mirror_up() == 0


# ── lifespan (arranque/cierre) ──────────────────────────────────────────────
def _lifespan_spies(monkeypatch):
    calls = {"restore": 0, "mirror_up": 0, "sync_loop": 0}

    def restore(overwrite=False):
        calls["restore"] += 1
        return 0

    def mirror_up():
        calls["mirror_up"] += 1
        return 0

    async def sync_loop():
        calls["sync_loop"] += 1
        await asyncio.sleep(3600)
    monkeypatch.setattr(remote_store, "restore", restore)
    monkeypatch.setattr(remote_store, "mirror_up", mirror_up)
    monkeypatch.setattr(remote_store, "sync_loop", sync_loop)
    return calls


def _boot():
    import webapp.main as main
    with TestClient(main.app):
        time.sleep(0.2)                                                                         # deja correr las tareas de fondo del arranque


def test_app_lifespan_in_local_mode_with_credentials_touches_nothing_remote_and_warns(monkeypatch, tmp_path, caplog):
    set_mode(monkeypatch, tmp_path, "local")
    calls = _lifespan_spies(monkeypatch)
    with caplog.at_level(logging.INFO):
        _boot()
    assert calls == {"restore": 0, "mirror_up": 0, "sync_loop": 0}
    assert any("DANWOR_PERSISTENCE=local" in r.getMessage() and "DESACTIVADOS" in r.getMessage() for r in caplog.records)


def test_app_lifespan_in_read_only_mode_never_restores_syncs_or_flushes_automatically(monkeypatch, tmp_path):
    set_mode(monkeypatch, tmp_path, "read-only")
    calls = _lifespan_spies(monkeypatch)
    _boot()
    assert calls == {"restore": 0, "mirror_up": 0, "sync_loop": 0}


def test_app_lifespan_in_remote_mode_restores_syncs_and_flushes(monkeypatch, tmp_path, caplog):
    set_mode(monkeypatch, tmp_path, "remote")
    calls = _lifespan_spies(monkeypatch)
    with caplog.at_level(logging.WARNING):
        _boot()
    assert calls["restore"] == 1 and calls["sync_loop"] == 1 and calls["mirror_up"] == 1          # restore + sync + flush de cierre
    assert not any("DANWOR_PERSISTENCE=" in r.getMessage() and "DESACTIVADOS" in r.getMessage() for r in caplog.records)


def test_mode_warning_only_when_credentials_exist_without_full_remote(monkeypatch, tmp_path):
    set_mode(monkeypatch, tmp_path, "remote")
    assert remote_store.mode_warning() is None
    set_mode(monkeypatch, tmp_path, "local")
    assert "DANWOR_PERSISTENCE=local" in remote_store.mode_warning()
    set_mode(monkeypatch, tmp_path, "local", credentials=False)
    assert remote_store.mode_warning() is None                                                  # sin credenciales no hay nada que advertir


# ── CLI explícito ───────────────────────────────────────────────────────────
def test_explicit_cli_restore_requires_read_authorisation_and_is_non_destructive_by_default(monkeypatch, tmp_path, capsys):
    s3 = FakeS3({"case_library/case_a.yaml": b"REMOTE"})
    case_dir = set_mode(monkeypatch, tmp_path, "local", client=s3)
    assert remote_store._cli(["restore"]) == 2 and s3.calls == []                               # local: no hace nada
    set_mode(monkeypatch, tmp_path, "read-only", client=s3)
    (case_dir / "case_a.yaml").write_bytes(b"LOCAL")
    assert remote_store._cli(["restore"]) == 0 and (case_dir / "case_a.yaml").read_bytes() == b"LOCAL"
    assert remote_store._cli(["restore", "--overwrite"]) == 0 and (case_dir / "case_a.yaml").read_bytes() == b"REMOTE" and s3.writes == []
    assert remote_store._cli(["status"]) == 0 and "DANWOR_PERSISTENCE=read-only" in capsys.readouterr().out


# ── Case Library: ruta resuelta al usar, tests aislados ─────────────────────
def test_case_repository_resolves_the_directory_at_use_time_and_respects_explicit_roots(tmp_path):
    from webapp.services.case_library import CaseLibraryRepository
    repo = CaseLibraryRepository()
    assert repo.root == Path(config.CASE_LIBRARY_DIR)
    explicit = CaseLibraryRepository(tmp_path / "x")
    assert explicit.root == tmp_path / "x"
    repo.root = tmp_path / "y"                                                                   # el setter existente de los tests sigue funcionando
    assert repo.root == tmp_path / "y"


def test_the_shared_wizard_store_follows_the_redirected_case_library_and_never_touches_the_real_one():
    from webapp.copilot_runtime import store
    repo = store._case_repository
    assert repo.root == Path(config.CASE_LIBRARY_DIR)
    real = Path(__file__).resolve().parent.parent / "case_library"
    assert repo.root.resolve() != real.resolve() and "runtime-case-library" in str(repo.root)
    before = set(os.listdir(real)) if real.exists() else set()
    run = store.start(store.tree_by_id("extraction_options"))
    for a in ["ERP", "S4HANA", "Private", "Sí", "Sí", "Sí"]:
        store.answer(run.id, a)
    assert run.case_id and (repo.root / f"{run.case_id}.yaml").is_file()                          # el caso se crea (lógica funcional intacta) en el tmp
    assert (set(os.listdir(real)) if real.exists() else set()) == before                          # y la real no cambió


def test_explicit_case_library_operations_still_work(monkeypatch):
    from webapp.copilot_runtime import store
    run = store.start(store.tree_by_id("extraction_options"))
    for a in ["ERP", "S4HANA", "Private", "Sí", "Sí", "Sí"]:
        store.answer(run.id, a)
    case_id = store._case_repository.update_feedback(run, rating="up")
    assert (Path(config.CASE_LIBRARY_DIR) / f"{case_id}.yaml").is_file()


# ── navegar por GET no crea casos ni dispara escrituras remotas ─────────────
def test_get_navigation_creates_no_cases_and_triggers_no_remote_operation(monkeypatch, tmp_path):
    import webapp.main as main
    from webapp.services import auth_service
    from webapp.services.case_library import CaseLibraryRepository
    set_mode(monkeypatch, tmp_path, "remote", client=FakeS3())                                    # peor caso: modo remote con cliente
    spied = []
    for name in ("restore", "mirror_up", "push_path", "delete_path", "read_object_bytes"):
        monkeypatch.setattr(remote_store, name, lambda *a, _n=name, **k: spied.append(_n))
    writes = []
    real_write = CaseLibraryRepository._write
    monkeypatch.setattr(CaseLibraryRepository, "_write", lambda self, path, data: (writes.append(str(path)), real_write(self, path, data))[1])
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    for url in ("/", "/modules/04_Modelado_y_Desarrollo?pack=datasphere_core", "/modules/04_Modelado_y_Desarrollo?pack=datasphere_core&tab=catalogo",
                "/catalog/domain?dominio=Ventas", "/api/auth/me", "/whats-new"):
        assert c.get(url, follow_redirects=True).status_code in (200, 401, 404, 307), url
    assert spied == [] and writes == []
    assert [p for p in Path(config.CASE_LIBRARY_DIR).glob("case_*.yaml")] == []
