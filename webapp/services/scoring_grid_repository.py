"""scoring_grid_repository.py — persistencia de la GRILLA de evaluación (scoring) de un proceso.

MISMO patrón de storage que `entrevista_repository`/`busqueda_repository` (un JSON por entidad bajo `human-capital/`, escritura atómica, write-through vía `core.remote_store` según `DANWOR_PERSISTENCE`, lock global
para el read-modify-write, revisión optimista) — no un mecanismo paralelo. Vive en su PROPIO documento, separado de `busqueda`, para no mezclar su concurrencia con la del proceso.

Una grilla es UNA INSTANCIA por búsqueda, con identidad y versión propias:
  - `grid_id` (estable) y `version` (entera, +1 cada vez que cambia el CONTENIDO de los criterios; guardar sin cambios no escribe ni sube la versión): las evaluaciones guardan la versión contra la que se hicieron y quedan
    DESACTUALIZADAS cuando la grilla pasa a otra versión;
  - `revision` (concurrencia optimista del documento: dos personas editando la grilla a la vez no se pisan);
  - `origen`: hoy siempre «manual»; reserva `template_id`/`template_version` para instanciar futuras plantillas reutilizables sin migrar datos;
  - cada criterio tiene `criterio_id` estable, `fase` (`cchh` | `tecnica`; hoy solo `cchh` es guardable), `bloque`, `nombre`, `descripcion`, `peso`, anclas 1/3/5, `preguntas_guia` y `orden`."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

_PERSIST_DIR_NAME = "human-capital"
_SUBDIR = "scoring-grids"
_SCHEMA_VERSION = 1
_RESTORE_WAIT_TIMEOUT_S = 15.0
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_write_lock = threading.Lock()
_CONTENT_FIELDS = ("criterio_id", "fase", "bloque", "nombre", "descripcion", "peso", "ancla_1", "ancla_3", "ancla_5", "preguntas_guia")


class GridStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class GridConfigIncompleteError(GridStorageError):
    """`remote_store` sin configurar en modo remote, o modo read-only."""


class GridRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


class GridConcurrentModificationError(Exception):
    """La grilla cambió desde que se leyó la `revision` esperada."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _SUBDIR


def _path(busqueda_id: str) -> Path:
    if not _ID_RE.match(str(busqueda_id or "")):
        raise ValueError("Identificador de búsqueda inválido.")
    return _dir() / f"{busqueda_id}.json"


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


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
        raise GridStorageError(f"No se pudo escribir la grilla en disco: {exc}") from exc
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
        _logger.warning("human_capital.scoring_grid_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("human_capital.scoring_grid_remote_cleanup_failed reason=%s", type(exc).__name__)


def _require_writable() -> None:
    if remote_store.read_only_mode():
        raise GridConfigIncompleteError("El scoring está en modo de solo lectura en este entorno: no se pueden guardar cambios.")


def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    """Contrato de durabilidad de los demás repositorios: `local` escribe en disco sin Spaces; `remote` confirma el push o compensa (restaura la versión previa / limpia el alta) y distingue el resultado desconocido."""
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
        raise GridConfigIncompleteError("El scoring todavía no está configurado para este entorno.")
    if previous is None:
        if remote_store.push_path(path):
            return payload
        _cleanup_failed_write(path)
        raise GridRemoteOutcomeUnknownError("No pudimos confirmar si la grilla se guardó. Revisá la pantalla antes de reintentar.")
    _write_atomic(path, previous)
    if remote_store.push_path(path):
        raise GridStorageError("No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior de la grilla.")
    raise GridRemoteOutcomeUnknownError("No pudimos confirmar el estado remoto de la grilla tras un fallo de guardado.")


def get(busqueda_id: str) -> dict | None:
    _ensure_restored()
    path = _path(busqueda_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GridStorageError(f"No se pudo leer la grilla: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    payload.setdefault("revision", 1)
    payload.setdefault("version", 1)
    payload.setdefault("criterios", [])
    return payload


def _content(criterios) -> list:
    """Contenido comparable de la grilla (sin `orden` explícito: el orden es la posición)."""
    return [{k: c.get(k) for k in _CONTENT_FIELDS} for c in criterios]


def save(busqueda_id: str, expected_revision: int, criterios: list[dict], *, actor: str | None, origen: dict | None = None) -> tuple[dict, bool]:
    """Guarda la grilla de la búsqueda -> `(grilla, cambió)`. `criterios` ya viene normalizado/validado por el motor (sin ids: los criterios nuevos reciben `criterio_id`; los existentes conservan el suyo).
    `expected_revision` = 0 para crear la grilla; si no coincide con la vigente -> `GridConcurrentModificationError`. Si el contenido no cambia NO se escribe ni se sube la versión."""
    _ensure_restored()
    with _write_lock:
        current = get(busqueda_id)
        current_revision = current["revision"] if current else 0
        if expected_revision != current_revision:
            raise GridConcurrentModificationError("La grilla cambió desde que abriste el editor. Volvé a abrirla para no pisar el cambio.")
        known = {c["criterio_id"] for c in (current or {}).get("criterios", [])}
        now = _now_iso()
        built = []
        for i, c in enumerate(criterios):
            item = {k: c.get(k) for k in _CONTENT_FIELDS}
            item["criterio_id"] = item["criterio_id"] if item["criterio_id"] in known else str(uuid4())
            item["orden"] = i
            built.append(item)
        if current is not None and _content(current["criterios"]) == _content(built):
            return current, False
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "grid_id": (current or {}).get("grid_id") or str(uuid4()),
            "busqueda_id": busqueda_id,
            "version": (current["version"] + 1) if current else 1,
            "revision": current_revision + 1,
            "origen": (current or {}).get("origen") or origen or {"tipo": "manual", "template_id": None, "template_version": None},
            "criterios": built,
            "created_at": (current or {}).get("created_at") or now,
            "created_by": (current or {}).get("created_by") or (str(actor).strip().lower() or None if actor else None),
            "updated_at": now,
            "updated_by": (str(actor).strip().lower() or None) if actor else None,
        }
        return _persist(_path(busqueda_id), payload, previous=current), True
