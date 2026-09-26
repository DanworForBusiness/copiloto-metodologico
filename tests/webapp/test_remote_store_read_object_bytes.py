"""
test_remote_store_read_object_bytes.py — CCHH Catalog Foundation, primitivo
`remote_store.read_object_bytes()`.

Es el primitivo de verificación por CONTENIDO (bytes exactos) que la máquina
de estados del refresh de catálogos usa para desambiguar un `push_path()` que
devolvió `False`. Tres resultados deliberadamente distintos: bytes (existe),
None (ausente), RemoteReadError (irresoluble). Se mockea el límite
`_get_client` (mismo punto de inyección que el resto de remote_store)."""
from __future__ import annotations

from pathlib import Path

import pytest

import config
from core import remote_store


@pytest.fixture(autouse=True)
def _spaces_enabled(monkeypatch, tmp_path):
    # enabled() lee config.PERSIST_ENABLED (bool fijo al import) + SPACES_BUCKET/PREFIX
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "danwor-test")
    monkeypatch.setattr(config, "SPACES_PREFIX", "prod")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    # persist dir "catalog_local" cuelga de BASE_DIR salvo DANWOR_CATALOG_DIR
    monkeypatch.delenv("DANWOR_CATALOG_DIR", raising=False)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local"])


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _NoSuchKey(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class _FakeClient:
    def __init__(self, *, body=None, raise_exc=None):
        self._body = body
        self._raise = raise_exc
        self.calls = []

    def get_object(self, *, Bucket, Key):
        self.calls.append((Bucket, Key))
        if self._raise is not None:
            raise self._raise
        return {"Body": _FakeBody(self._body)}


def _catalog_path() -> Path:
    return config.BASE_DIR / "catalog_local" / "cchh" / "catalogos.json"


def test_returns_exact_bytes_when_object_present(monkeypatch):
    fake = _FakeClient(body=b'{"schema_version": 1}')
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake)
    out = remote_store.read_object_bytes(_catalog_path())
    assert out == b'{"schema_version": 1}'
    # key mapeado con prefijo de ambiente + directorio lógico
    assert fake.calls == [("danwor-test", "prod/catalog_local/cchh/catalogos.json")]


def test_returns_none_when_object_absent(monkeypatch):
    fake = _FakeClient(raise_exc=_NoSuchKey())
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake)
    assert remote_store.read_object_bytes(_catalog_path()) is None


def test_raises_remote_read_error_when_get_unresolved(monkeypatch):
    boom = RuntimeError("connection reset")
    fake = _FakeClient(raise_exc=boom)
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake)
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.read_object_bytes(_catalog_path())


def test_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)  # enabled() -> False
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.read_object_bytes(_catalog_path())


def test_raises_when_path_outside_persisted_dirs(monkeypatch):
    fake = _FakeClient(body=b"x")
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake)
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.read_object_bytes(config.BASE_DIR / "not_persisted" / "f.json")
