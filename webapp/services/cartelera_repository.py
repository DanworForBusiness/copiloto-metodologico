"""cartelera_repository.py — persistencia de CARTELERA VIRTUAL (Capital Humano).

MISMO patrón de storage que `publicacion_repository`/`entrevista_repository` (un JSON por entidad bajo `human-capital/`, write atómico
crash-safe, write-through síncrono vía `core.remote_store`, compensación ante push no confirmado, lock global para el read-modify-write,
concurrencia optimista por `revision`) — NO un mecanismo nuevo. `human-capital` ya figura en `config.PERSIST_PATHS`.

Colecciones (`human-capital/cartelera/<colección>/<id>.json`):
  - `editions`   — una por mes; id = «YYYY-MM».
  - `birthdays`  — una por persona (uuid).
  - `settings`   — documentos únicos: `institutional` (misión, visión, valores, galería, organigrama) y `site` (accesos directos).

Los RECURSOS documentales (imágenes, flyers, organigrama) NO viven acá: su fuente de verdad es la carpeta SharePoint de Cartelera Virtual
y su copia de distribución validada vive en Spaces (decisión F5). Esa integración queda pendiente de la verificación de acceso a SharePoint:
este módulo solo persiste el contenido editorial y sus metadatos.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

SCHEMA_VERSION = 1
COLLECTIONS = ("editions", "birthdays", "settings")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RESTORE_WAIT_TIMEOUT_S = 15.0
_write_lock = threading.RLock()


class CarteleraStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class CarteleraConfigIncompleteError(CarteleraStorageError):
    """Persistencia no habilitada para escribir en este entorno."""


class CarteleraNotFoundError(Exception):
    """Documento inexistente (→ 404)."""


class CarteleraConcurrentModificationError(Exception):
    """El documento cambió desde que se leyó la `revision` esperada (→ 409, sin escribir)."""


class CarteleraRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


def _dir(collection: str) -> Path:
    if collection not in COLLECTIONS:
        raise ValueError(f"colección desconocida: {collection}")
    return config.BASE_DIR / "human-capital" / "cartelera" / collection


def _path(collection: str, doc_id: str) -> Path:
    if not _ID.match(str(doc_id or "")):
        raise CarteleraNotFoundError(f"identificador inválido: {doc_id!r}")
    return _dir(collection) / f"{doc_id}.json"


def _ensure_restored() -> None:
    if remote_store.enabled():
        remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise CarteleraStorageError(f"No se pudo escribir en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _cleanup_failed_write(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("cartelera.cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("cartelera.remote_cleanup_failed reason=%s", type(exc).__name__)


def _require_writable() -> None:
    if remote_store.read_only_mode():
        raise CarteleraConfigIncompleteError("La Cartelera Virtual está en modo de solo lectura en este entorno: no se pueden guardar cambios.")


def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    _require_writable()
    _write_atomic(path, payload)
    if remote_store.local_only():
        return payload
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        if previous is None:
            _cleanup_failed_write(path)
        else:
            _write_atomic(path, previous)
        raise CarteleraConfigIncompleteError("La Cartelera Virtual todavía no está configurada para este entorno.")
    if previous is None:
        if remote_store.push_path(path):
            return payload
        _cleanup_failed_write(path)
        raise CarteleraRemoteOutcomeUnknownError("No pudimos confirmar si se guardó. Revisá el listado antes de reintentar.")
    _write_atomic(path, previous)
    if remote_store.push_path(path):
        raise CarteleraStorageError("No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior.")
    raise CarteleraRemoteOutcomeUnknownError("No pudimos confirmar el estado remoto tras un fallo de guardado.")


def get(collection: str, doc_id: str) -> dict | None:
    _ensure_restored()
    try:
        raw = _path(collection, doc_id).read_text(encoding="utf-8")
    except (FileNotFoundError, CarteleraNotFoundError):
        return None
    except OSError as exc:
        raise CarteleraStorageError(f"No se pudo leer: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def list_all(collection: str) -> list[dict]:
    _ensure_restored()
    directory = _dir(collection)
    if not directory.exists():
        return []
    out: list[dict] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix != ".json" or entry.name.startswith(".tmp-"):
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("id"):
            out.append(payload)
    return out


def save(collection: str, doc_id: str, fields: dict, *, expected_revision: int | None) -> dict:
    """Crea (`expected_revision=None`, falla si ya existe) o actualiza con concurrencia optimista. `fields` ya validado por el servicio."""
    with _write_lock:
        path = _path(collection, doc_id)
        current = get(collection, doc_id)
        if expected_revision is None and current is not None:
            raise CarteleraConcurrentModificationError("Ya existe un registro con ese identificador.")
        if expected_revision is not None:
            if current is None:
                raise CarteleraNotFoundError(f"{collection}/{doc_id} no existe.")
            if int(current.get("revision", 1)) != int(expected_revision):
                raise CarteleraConcurrentModificationError("El contenido cambió desde que abriste el formulario. Recargá antes de guardar.")
        now = now_iso()
        payload = {**(json.loads(json.dumps(current)) if current else {}), **fields,
                   "id": doc_id, "schema_version": SCHEMA_VERSION,
                   "revision": (int(current.get("revision", 1)) + 1) if current else 1,
                   "created_at": (current or {}).get("created_at") or now, "updated_at": now}
        return _persist(path, payload, previous=current)


def delete(collection: str, doc_id: str) -> None:
    with _write_lock:
        path = _path(collection, doc_id)
        previous = get(collection, doc_id)
        if previous is None:
            raise CarteleraNotFoundError(f"{collection}/{doc_id} no existe.")
        _require_writable()
        try:
            path.unlink()
        except OSError as exc:
            raise CarteleraStorageError(f"No se pudo eliminar: {exc}") from exc
        if remote_store.local_only() or remote_store.delete_path(path):
            return
        if not remote_store.enabled():
            raise CarteleraConfigIncompleteError("La Cartelera Virtual todavía no está configurada para este entorno.")
        _write_atomic(path, previous)
        raise CarteleraRemoteOutcomeUnknownError("No pudimos confirmar si el borrado se sincronizó. Revisá el listado antes de reintentar.")
