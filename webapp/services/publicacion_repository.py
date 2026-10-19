"""publicacion_repository.py — persistencia de PUBLICACIONES (registro de avisos publicados de las búsquedas).

MISMO patrón de storage que `entrevista_repository`/`onboarding_repository` (un JSON por entidad bajo `human-capital/`, write atómico crash-safe, durabilidad write-through vía
`core.remote_store`, lock global para el read-modify-write) — NO un mecanismo nuevo/paralelo.

SEMÁNTICA DE DOMINIO (verbatim de la referencia app-seleccion; `supabase/schema.sql` tabla `publicaciones`, `lib/actions/publicaciones.ts`, `PublicacionModal.tsx`):
  - Es un REGISTRO HISTÓRICO plano de avisos publicados (crear / editar / eliminar). SIN estados ni lifecycle (no hay borrador/publicada/cerrada/expirada, ni publicar/despublicar/
    cerrar/duplicar): la referencia no los tiene y acá no se inventan.
  - Campos: `posicion` (texto OBLIGATORIO), `canal` (taxonomía gobernada `canal_publicacion`), `cantidad_avisos` (entero >= 1: una fila puede agrupar varios avisos de una misma posición),
    `periodo` (trimestre "YYYY-Qn" elegido a mano), `fecha` (opcional), `observacion` (opcional), `busqueda_id` (opcional).
  - `busqueda_id` OPCIONAL (en la referencia `proceso_id`, FK `on delete set null`): Búsqueda 1 → 0..N Publicaciones; una Publicación pertenece a 0..1 Búsqueda; sin relación con
    CandidateApplication/Profile. `posicion` es un SNAPSHOT: al elegir una Búsqueda se COPIA su `nombre_puesto` y se persiste junto con el vínculo; después NO se re-deriva ni se sincroniza
    si la Búsqueda cambia de nombre (representa «qué posición decía el aviso cuando se publicó»). Esa regla vive en `human_capital_service`; este repositorio sólo persiste lo ya validado.
  - Delete = HARD DELETE (`eliminarPublicacion`: `.delete().eq("id", id)` incondicional, con confirmación genérica).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

_SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_PUBLICACIONES_SUBDIR = "publicaciones"
_RESTORE_WAIT_TIMEOUT_S = 15.0

_write_lock = threading.Lock()

EDITABLE_FIELDS = ("busqueda_id", "posicion", "canal", "cantidad_avisos", "periodo", "fecha", "observacion")


class PublicacionStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class PublicacionConfigIncompleteError(PublicacionStorageError):
    """`remote_store.enabled() is False` — Spaces sin configurar."""


class PublicacionNotFoundError(Exception):
    """Operación sobre una `publicacion_id` inexistente (→ 404 en el router)."""


class PublicacionConcurrentModificationError(Exception):
    """La publicación cambió desde que se leyó la `revision` esperada."""


class PublicacionRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


def _publicaciones_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _PUBLICACIONES_SUBDIR


def _publicacion_path(publicacion_id: str) -> Path:
    return _publicaciones_dir() / f"{publicacion_id}.json"


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
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
        raise PublicacionStorageError(f"No se pudo escribir la publicación en disco: {exc}") from exc
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
        _logger.warning("human_capital.publicacion_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("human_capital.publicacion_remote_cleanup_failed reason=%s", type(exc).__name__)


def _with_defaults(payload: dict) -> dict:
    payload.setdefault("revision", 1)
    payload.setdefault("busqueda_id", None)
    payload.setdefault("fecha", None)
    payload.setdefault("observacion", None)
    payload.setdefault("cantidad_avisos", 1)
    return payload


def get(publicacion_id: str) -> dict | None:
    _ensure_restored()
    path = _publicacion_path(publicacion_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PublicacionStorageError(f"No se pudo leer la publicación: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return _with_defaults(payload) if isinstance(payload, dict) else None


def list() -> list[dict]:  # noqa: A001 — nombre del contrato, no el builtin
    _ensure_restored()
    directory = _publicaciones_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise PublicacionStorageError(f"No se pudo listar las publicaciones: {exc}") from exc
    out: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # corrupto/ilegible → se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("publicacion_id"):
            out.append(_with_defaults(payload))
    return out


def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    _write_atomic(path, payload)
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        if previous is None:
            _cleanup_failed_write(path)
        else:
            _write_atomic(path, previous)
        raise PublicacionConfigIncompleteError("La sección de Publicaciones todavía no está configurada para este entorno.")
    if previous is None:
        if remote_store.push_path(path):  # reintento del create
            return payload
        _cleanup_failed_write(path)
        raise PublicacionRemoteOutcomeUnknownError("No pudimos confirmar si esta publicación se guardó. Revisá el listado antes de reintentar.")
    _write_atomic(path, previous)
    if remote_store.push_path(path):
        raise PublicacionStorageError("No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior de la publicación.")
    raise PublicacionRemoteOutcomeUnknownError("No pudimos confirmar el estado remoto de esta publicación tras un fallo de guardado.")


def create(fields: dict) -> dict:
    """Crea una publicación. `fields` ya viene validado/shapeado por el service (incluido el snapshot de `posicion`)."""
    _ensure_restored()
    publicacion_id = str(uuid4())
    now = _now_iso()
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "publicacion_id": publicacion_id,
        "revision": 1,
        **{k: fields.get(k) for k in EDITABLE_FIELDS},
        "created_at": now,
        "updated_at": now,
    }
    return _persist(_publicacion_path(publicacion_id), payload, previous=None)


def update(publicacion_id: str, expected_revision: int, fields: dict) -> dict:
    """Edita una publicación (concurrencia optimista por `revision`). `fields` ya validado/shapeado por el service."""
    with _write_lock:
        current = get(publicacion_id)
        if current is None:
            raise PublicacionNotFoundError(f"La publicación {publicacion_id} no existe.")
        if current.get("revision", 1) != expected_revision:
            raise PublicacionConcurrentModificationError("Esta publicación cambió desde que abriste el formulario.")
        candidate = json.loads(json.dumps(current))  # copia profunda
        for k in EDITABLE_FIELDS:
            if k in fields:
                candidate[k] = fields[k]
        candidate["publicacion_id"] = publicacion_id
        candidate["schema_version"] = _SCHEMA_VERSION
        candidate["created_at"] = current.get("created_at")
        candidate["revision"] = current.get("revision", 1) + 1
        candidate["updated_at"] = _now_iso()
        return _persist(_publicacion_path(publicacion_id), candidate, previous=current)


def delete(publicacion_id: str) -> None:
    """Hard delete (la referencia borra sin precondiciones). Si el borrado remoto no se confirma, ROLLBACK del borrado local (local/remoto nunca divergen)."""
    path = _publicacion_path(publicacion_id)
    previous = get(publicacion_id)
    if previous is None:
        raise PublicacionNotFoundError(f"La publicación {publicacion_id} no existe.")
    try:
        path.unlink()
    except FileNotFoundError:
        raise PublicacionNotFoundError(f"La publicación {publicacion_id} no existe.") from None
    except OSError as exc:
        raise PublicacionStorageError(f"No se pudo eliminar la publicación: {exc}") from exc
    if remote_store.delete_path(path):
        return
    if not remote_store.enabled():
        raise PublicacionConfigIncompleteError("La sección de Publicaciones todavía no está configurada para este entorno.")
    _write_atomic(path, previous)  # rollback: local vuelve a existir
    raise PublicacionRemoteOutcomeUnknownError("No pudimos confirmar si el borrado se sincronizó. Revisá el listado antes de reintentar.")
