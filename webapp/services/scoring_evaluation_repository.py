"""scoring_evaluation_repository.py — persistencia de las EVALUACIONES puntuadas (scoring) de un proceso.

Una evaluación = (postulación, fase, `evaluator`): un documento por clave, upsert idempotente (re-guardar lo mismo no escribe). Usa la MISMA mecánica de durabilidad y los MISMOS errores que `scoring_grid_repository`
(local/remote/read-only, escritura atómica, compensación) — no un mecanismo paralelo — y vive bajo `human-capital/scoring-evaluations/<busqueda_id>/`. Es independiente del registro administrativo de entrevista
(`entrevista_repository`), que no guarda ninguna respuesta puntuada.

El registro lo construye `scoring_engine.construir_evaluacion` (identidades verificadas, `grid_id`/`grid_version`); acá solo se persiste. Nada se crea al navegar: solo `save`."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import config
from webapp.services import scoring_grid_repository as _grid

# Mecánica compartida con la grilla (misma familia de errores y de persistencia).
GridStorageError = _grid.GridStorageError
GridConfigIncompleteError = _grid.GridConfigIncompleteError
GridRemoteOutcomeUnknownError = _grid.GridRemoteOutcomeUnknownError

_SUBDIR = "scoring-evaluations"
_SCHEMA_VERSION = 1
_write_lock = threading.Lock()
_COMPARABLE = ("evaluator", "recorded_by", "grid_id", "grid_version", "fase", "puntaje", "cumple_excluyentes", "comentario_general", "detalles")


class EvaluationConcurrentModificationError(Exception):
    """La evaluación cambió desde que se abrió el formulario (`revision` distinta)."""


def evaluation_id(evaluation_key: str) -> str:
    """Id determinístico por clave (postulación:fase:evaluator): la misma evaluación siempre cae en el mismo documento."""
    return str(uuid5(NAMESPACE_URL, f"danwor:scoring-evaluation:{evaluation_key}"))


def _dir(busqueda_id: str) -> Path:
    if not _grid._ID_RE.match(str(busqueda_id or "")):
        raise ValueError("Identificador de búsqueda inválido.")
    return config.BASE_DIR / _grid._PERSIST_DIR_NAME / _SUBDIR / busqueda_id


def _read(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GridStorageError(f"No se pudo leer la evaluación: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not payload.get("evaluation_key"):
        return None
    payload.setdefault("revision", 1)
    return payload


def get(busqueda_id: str, evaluation_key: str) -> dict | None:
    _grid._ensure_restored()
    return _read(_dir(busqueda_id) / f"{evaluation_id(evaluation_key)}.json")


def list_for_busqueda(busqueda_id: str) -> list[dict]:
    """Todas las evaluaciones del proceso (de cualquier postulación/fase/evaluador), ordenadas por fecha de registro. Un archivo ilegible se saltea sin romper el resto."""
    _grid._ensure_restored()
    directory = _dir(busqueda_id)
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise GridStorageError(f"No se pudo listar las evaluaciones: {exc}") from exc
    out = []
    for entry in entries:
        if entry.is_file() and entry.suffix == ".json" and not entry.name.startswith(".tmp-"):
            payload = _read(entry)
            if payload is not None:
                out.append(payload)
    return sorted(out, key=lambda e: str(e.get("recorded_at") or ""))


def delete_for_busqueda(busqueda_id: str) -> int:
    """Elimina TODAS las evaluaciones de UNA búsqueda (cascada de «Eliminar búsqueda»; cada archivo con el contrato de durabilidad de `scoring_grid_repository.delete_file_durable`) -> cuántas borró. Idempotente:
    sin carpeta no hace nada. Un archivo ilegible también se borra (es de esa búsqueda: no debe quedar residuo)."""
    _grid._ensure_restored()
    directory = _dir(busqueda_id)
    if not directory.exists():
        return 0
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise GridStorageError(f"No se pudo listar las evaluaciones: {exc}") from exc
    deleted = 0
    with _write_lock:
        for entry in entries:
            if entry.is_file() and entry.suffix == ".json" and not entry.name.startswith(".tmp-"):
                deleted += 1 if _grid.delete_file_durable(entry) else 0
        try:
            directory.rmdir()                            # carpeta vacía: orden local (en Spaces no existen carpetas, solo objetos)
        except OSError:
            pass
    return deleted


def save(record: dict, expected_revision: int) -> tuple[dict, bool]:
    """Upsert de la evaluación -> `(evaluación, cambió)`. `expected_revision` = 0 para crear. Si el contenido (notas, comentarios, versión de grilla, identidades) no cambia NO se escribe. Cualquier otra diferencia sube la `revision`."""
    _grid._ensure_restored()
    busqueda_id = record.get("busqueda_id")
    key = record.get("evaluation_key")
    if not key or not busqueda_id:
        raise ValueError("Evaluación sin clave o sin proceso.")
    path = _dir(busqueda_id) / f"{evaluation_id(key)}.json"
    with _write_lock:
        current = _read(path)
        current_revision = current["revision"] if current else 0
        if expected_revision != current_revision:
            raise EvaluationConcurrentModificationError("La evaluación cambió desde que abriste el formulario. Volvé a abrirla para no pisar el cambio.")
        if current is not None and all(current.get(k) == record.get(k) for k in _COMPARABLE):
            return current, False
        payload = {**record, "schema_version": _SCHEMA_VERSION, "evaluation_id": evaluation_id(key), "revision": current_revision + 1,
                   "created_at": (current or {}).get("created_at") or record.get("recorded_at"), "updated_at": record.get("recorded_at")}
        return _grid._persist(path, payload, previous=current), True
