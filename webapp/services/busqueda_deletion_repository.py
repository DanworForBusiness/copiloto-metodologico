"""busqueda_deletion_repository.py — registro de AUDITORÍA y REANUDACIÓN de «Eliminar búsqueda» (Capital Humano).

NO es un mecanismo de borrado: el borrado de cada entidad sigue en SU repositorio (entrevistas, onboarding, scoring, publicaciones, búsquedas). Esto solo deja un documento por búsqueda con QUIÉN eliminó, CUÁNDO, el PLAN (conteos)
y qué pasos se completaron, para que (a) la operación quede auditada aun después de que la búsqueda ya no exista y (b) un fallo parcial sea visible y reanudable (los pasos son idempotentes y la búsqueda se borra AL FINAL).

NO guarda datos personales de candidatos: solo el título del puesto, el ejecutor, fechas, conteos y la clase del error. Un JSON por búsqueda bajo `human-capital/busqueda-deletions/` (MISMA durabilidad write-through que los demás
repositorios: `local` escribe en disco; `remote` confirma el push o compensa).
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

_PERSIST_DIR_NAME = "human-capital"
_SUBDIR = "busqueda-deletions"
_SCHEMA_VERSION = 1
_RESTORE_WAIT_TIMEOUT_S = 15.0
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_write_lock = threading.Lock()

EN_CURSO, COMPLETADA, FALLIDA = "en_curso", "completada", "fallida"


class DeletionJournalError(Exception):
    """Fallo de PERSISTENCIA del registro (disco o remote_store), nunca de validación."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(busqueda_id: str) -> Path:
    if not _ID_RE.match(str(busqueda_id or "")):
        raise ValueError("Identificador de búsqueda inválido.")
    return config.BASE_DIR / _PERSIST_DIR_NAME / _SUBDIR / f"{busqueda_id}.json"


def _ensure_restored() -> None:
    if remote_store.enabled():
        remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def get(busqueda_id: str) -> dict | None:
    _ensure_restored()
    try:
        payload = json.loads(_path(busqueda_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeletionJournalError(f"No se pudo leer el registro de eliminación: {exc}") from exc
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


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
        raise DeletionJournalError(f"No se pudo escribir el registro de eliminación: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _save(busqueda_id: str, payload: dict, *, previous: dict | None) -> dict:
    """Escribe el registro; en `remote` confirma el push o compensa (restaura el anterior / limpia el alta) para que local y remoto no diverjan."""
    if remote_store.read_only_mode():
        raise DeletionJournalError("El entorno está en modo de solo lectura: no se puede registrar la eliminación.")
    path = _path(busqueda_id)
    _write_atomic(path, payload)
    if remote_store.local_only() or remote_store.push_path(path):
        return payload
    if previous is None:
        try:
            path.unlink(missing_ok=True)
            remote_store.delete_path(path)
        except Exception as exc:  # noqa: BLE001 — best-effort
            _logger.warning("human_capital.busqueda_deletion_journal_cleanup_failed reason=%s", type(exc).__name__)
    else:
        _write_atomic(path, previous)
    raise DeletionJournalError("No pudimos confirmar el registro de la eliminación en el respaldo; no se eliminó nada.")


def begin(busqueda_id: str, *, puesto: str | None, actor: str | None, plan: dict) -> dict:
    """Abre (o REANUDA) el registro: conserva `iniciada_en`/`iniciada_por` del primer intento, cuenta `intentos` y deja el PLAN vigente (los conteos de lo que falta). Estado `en_curso`."""
    with _write_lock:
        previous = get(busqueda_id)
        now = _now_iso()
        who = (str(actor).strip().lower() or None) if actor else None
        payload = {
            "schema_version": _SCHEMA_VERSION, "busqueda_id": busqueda_id, "puesto": puesto, "estado": EN_CURSO,
            "iniciada_en": (previous or {}).get("iniciada_en") or now, "iniciada_por": (previous or {}).get("iniciada_por") or who,
            "reanudada_por": who if previous else None, "intentos": int((previous or {}).get("intentos") or 0) + 1,
            "actualizada_en": now, "finalizada_en": None, "plan": plan, "hechos": {}, "falla": None,
        }
        return _save(busqueda_id, payload, previous=previous)


def record_step(busqueda_id: str, step: str, count: int) -> dict:
    """Marca un paso completado (`hechos[step] = count`)."""
    with _write_lock:
        previous = get(busqueda_id) or {}
        payload = {**previous, "hechos": {**(previous.get("hechos") or {}), step: count}, "actualizada_en": _now_iso()}
        return _save(busqueda_id, payload, previous=previous or None)


def finish(busqueda_id: str) -> dict:
    with _write_lock:
        previous = get(busqueda_id) or {}
        now = _now_iso()
        return _save(busqueda_id, {**previous, "estado": COMPLETADA, "finalizada_en": now, "actualizada_en": now, "falla": None}, previous=previous or None)


def fail(busqueda_id: str, *, step: str, error: str) -> None:
    """Marca el registro `fallida` en el paso `step` (clase del error, sin texto libre). Best-effort: el fallo original es el que importa y no se enmascara."""
    try:
        with _write_lock:
            previous = get(busqueda_id) or {}
            _save(busqueda_id, {**previous, "estado": FALLIDA, "actualizada_en": _now_iso(), "falla": {"paso": step, "error": error}}, previous=previous or None)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("human_capital.busqueda_deletion_journal_fail_not_recorded step=%s reason=%s", step, type(exc).__name__)
