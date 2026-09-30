"""entrevista_repository.py — persistencia de ENTREVISTAS (registro de
entrevista a un candidato).

MISMO patrón de storage que `busqueda_repository`/`profile_repository` (un
JSON por entidad bajo `human-capital/`, write atómico crash-safe,
durabilidad write-through vía `core.remote_store`, lock global para el
read-modify-write) — NO un mecanismo nuevo/paralelo.

SEMÁNTICA DE DOMINIO (verbatim de la referencia app-seleccion, sin
inventar — ver supabase/schema.sql tabla `entrevistas` + lib/entrevistaAutomatica.ts):
  - Entrevista es un registro FLAT, no un lifecycle con múltiples estados:
    el único campo de estado es `concretada` (bool, default True). No hay
    "reprogramar"/"cancelar" como flujo separado -- "no se concretó" es
    simplemente poner concretada=False.
  - Se origina de UNA de dos formas:
    (a) automática, cuando una CandidateApplication (postulación embebida
        de una Búsqueda) entra a la etapa "Entrevista CCHH" / "Entrevista
        Líder" / "Decisión Final" -- UN SOLO registro por (profile_id,
        busqueda_id), se reutiliza/edita, nunca se duplica (ver
        unique index `entrevistas_candidato_proceso_unique_idx` +
        comentario "al pasar de Entrevista CCHH a Entrevista Líder se
        actualiza esta misma fila en vez de crear una nueva" en el schema
        de referencia);
    (b) manual, para un Profile del Banco que todavía no está en ninguna
        Búsqueda (sólo profile_id, sin busqueda_id/application_id).
  - `origen` NO se persiste como campo propio: la referencia misma
    prioriza mostrar el origen real del candidato/CV por sobre cualquier
    valor propio de la entrevista (`origenDeEntrevista`, lib/types.ts) --
    en el Hub el Profile YA tiene `source` (catálogo `fuente`) desde el
    alta, así que se deriva de ahí siempre (ver
    `human_capital_service.entrevista_view`), sin duplicar el dato.
  - `puesto_evaluado`: si hay `busqueda_id`, se deriva de
    `busqueda.nombre_puesto` (de sólo lectura en el form, igual que la
    referencia); si no, es texto libre editable.
  - `area` y `modalidad_contratacion` son catálogos GOBERNADOS
    (`area_consultora` y `modalidad_contratacion`,
    human_capital_catalog_repository) -- MISMO contrato migration-safe que
    Seniority/Fuente/Área de Búsquedas. `area_consultora` es la MISMA
    taxonomía que `entrevistas.area` en la referencia (confirmado por la
    migración `2026-09-24-fase2.sql`, que backfillea
    `procesos.area_consultora` DESDE `entrevistas.area`).
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
_ENTREVISTAS_SUBDIR = "entrevistas"
_RESTORE_WAIT_TIMEOUT_S = 15.0

_write_lock = threading.Lock()

# Campos EDITABLES de una entrevista (alta manual/edición). `concretada` tiene
# su propia operación (marcar_no_concretada) -- no se edita por esta vía para
# que quede explícito en el router/servicio cuándo se está tocando el único
# campo de "estado" real de la entidad.
EDITABLE_FIELDS = (
    "fecha_entrevista", "puesto_evaluado", "area", "modalidad_contratacion",
    "remuneracion_pretendida", "observaciones", "resumen_entrevista_tecnica",
)


class EntrevistaStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class EntrevistaConfigIncompleteError(EntrevistaStorageError):
    """`remote_store.enabled() is False` — Spaces sin configurar."""


class EntrevistaNotFoundError(Exception):
    """Operación sobre una `entrevista_id` inexistente (→ 404 en el router)."""


class EntrevistaConcurrentModificationError(Exception):
    """La entrevista cambió desde que se leyó la `revision` esperada."""


class EntrevistaRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


def _entrevistas_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _ENTREVISTAS_SUBDIR


def _entrevista_path(entrevista_id: str) -> Path:
    return _entrevistas_dir() / f"{entrevista_id}.json"


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
        raise EntrevistaStorageError(f"No se pudo escribir la entrevista en disco: {exc}") from exc
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
        _logger.warning("human_capital.entrevista_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("human_capital.entrevista_remote_cleanup_failed reason=%s", type(exc).__name__)


def _with_defaults(payload: dict) -> dict:
    payload.setdefault("revision", 1)
    payload.setdefault("concretada", True)
    payload.setdefault("busqueda_id", None)
    payload.setdefault("application_id", None)
    return payload


def get(entrevista_id: str) -> dict | None:
    _ensure_restored()
    path = _entrevista_path(entrevista_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EntrevistaStorageError(f"No se pudo leer la entrevista: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return _with_defaults(payload) if isinstance(payload, dict) else None


def list() -> list[dict]:  # noqa: A001 — nombre del contrato, no el builtin
    _ensure_restored()
    directory = _entrevistas_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise EntrevistaStorageError(f"No se pudo listar las entrevistas: {exc}") from exc
    out: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # corrupto/ilegible → se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("entrevista_id"):
            out.append(_with_defaults(payload))
    return out


def find_by_profile_and_busqueda(profile_id: str, busqueda_id: str) -> dict | None:
    """UN solo registro por (profile_id, busqueda_id) -- MISMO contrato que el
    unique index de la referencia. Usado por el auto-create idempotente al
    mover una postulación a una etapa de entrevista."""
    for entrevista in list():
        if entrevista.get("profile_id") == profile_id and entrevista.get("busqueda_id") == busqueda_id:
            return entrevista
    return None


def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    _write_atomic(path, payload)
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        if previous is None:
            _cleanup_failed_write(path)
        else:
            _write_atomic(path, previous)
        raise EntrevistaConfigIncompleteError(
            "La sección de Entrevistas todavía no está configurada para este entorno."
        )
    if previous is None:
        if remote_store.push_path(path):  # reintento del create
            return payload
        _cleanup_failed_write(path)
        raise EntrevistaRemoteOutcomeUnknownError(
            "No pudimos confirmar si esta entrevista se guardó. Revisá el listado antes de reintentar."
        )
    _write_atomic(path, previous)
    if remote_store.push_path(path):
        raise EntrevistaStorageError(
            "No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior de la entrevista."
        )
    raise EntrevistaRemoteOutcomeUnknownError(
        "No pudimos confirmar el estado remoto de esta entrevista tras un fallo de guardado."
    )


def create(fields: dict, *, profile_id: str, busqueda_id: str | None, application_id: str | None) -> dict:
    """Crea una entrevista nueva. `fields` ya viene validado/shapeado por el
    service. `concretada` arranca en True (MISMO default que la referencia)."""
    _ensure_restored()
    entrevista_id = str(uuid4())
    now = _now_iso()
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "entrevista_id": entrevista_id,
        "revision": 1,
        "profile_id": profile_id,
        "busqueda_id": busqueda_id,
        "application_id": application_id,
        **{k: fields.get(k) for k in EDITABLE_FIELDS},
        "concretada": True,
        "created_at": now,
        "updated_at": now,
    }
    return _persist(_entrevista_path(entrevista_id), payload, previous=None)


def _mutate(entrevista_id: str, mutator, *, expected_revision: int | None = None) -> dict:
    with _write_lock:
        current = get(entrevista_id)
        if current is None:
            raise EntrevistaNotFoundError(f"La entrevista {entrevista_id} no existe.")
        if expected_revision is not None and current.get("revision", 1) != expected_revision:
            raise EntrevistaConcurrentModificationError(
                "Esta entrevista cambió desde que abriste el formulario."
            )
        candidate = json.loads(json.dumps(current))  # copia profunda
        mutator(candidate)
        candidate["entrevista_id"] = entrevista_id
        candidate["schema_version"] = _SCHEMA_VERSION
        candidate["created_at"] = current.get("created_at")
        candidate["profile_id"] = current.get("profile_id")
        candidate["busqueda_id"] = current.get("busqueda_id")
        candidate["application_id"] = current.get("application_id")
        candidate["revision"] = current.get("revision", 1) + 1
        candidate["updated_at"] = _now_iso()
        return _persist(_entrevista_path(entrevista_id), candidate, previous=current)


def update(entrevista_id: str, expected_revision: int, fields: dict) -> dict:
    """Edita los campos de una entrevista (form de edición). Concurrencia
    optimista por `revision`. NO toca profile_id/busqueda_id/application_id/
    concretada (ver marcar_no_concretada)."""
    def _apply(doc: dict) -> None:
        for k in EDITABLE_FIELDS:
            if k in fields:
                doc[k] = fields[k]
    return _mutate(entrevista_id, _apply, expected_revision=expected_revision)


def marcar_no_concretada(entrevista_id: str, expected_revision: int) -> dict:
    """Única transición de 'estado' de una entrevista (MISMO botón/semántica
    que la referencia, "No se concretó" -- ver EntrevistaModal.tsx). No hay
    forma de revertir en la referencia; tampoco acá."""
    def _apply(doc: dict) -> None:
        doc["concretada"] = False
    return _mutate(entrevista_id, _apply, expected_revision=expected_revision)


def delete(entrevista_id: str) -> None:
    """Hard delete -- verificado contra la referencia, no por inferencia:
    `eliminarEntrevista` (lib/actions/entrevistas.ts) hace un
    `.from("entrevistas").delete().eq("id", id)` incondicional, sin
    precondición ni bloqueo por feedback/documentos/relaciones. La UI
    (EliminarEntrevistaButton.tsx) sólo pide una confirmación genérica, sin
    chequear nada antes. `entrevista_documentos` referencia a `entrevistas`
    con `on delete cascade` (migracion-2026-08-27.sql) -- el borrado NUNCA se
    bloquea por tener adjuntos, se cascadea. `feedback_tecnico` ni siquiera
    tiene FK a `entrevistas` (referencia `candidato_id`,
    migracion-2026-09-23-scoring-b.sql) -- borrar una entrevista no lo toca.
    Ninguna de las dos entidades existe todavía en el Hub (deferred, ver
    checkpoint); si se agregan después, su storage deberá limpiarse en su
    propia implementación, no acá. MISMO contrato de durabilidad que el
    resto del repositorio: si el borrado remoto no se confirma, se hace
    ROLLBACK del borrado local (se reescribe el contenido previo) para que
    local/remoto nunca diverjan (ver
    commercial_licensing_priority_repository.eliminar)."""
    path = _entrevista_path(entrevista_id)
    previous = get(entrevista_id)
    if previous is None:
        raise EntrevistaNotFoundError(f"La entrevista {entrevista_id} no existe.")
    try:
        path.unlink()
    except FileNotFoundError:
        raise EntrevistaNotFoundError(f"La entrevista {entrevista_id} no existe.") from None
    except OSError as exc:
        raise EntrevistaStorageError(f"No se pudo eliminar la entrevista: {exc}") from exc
    if remote_store.delete_path(path):
        return
    if not remote_store.enabled():
        raise EntrevistaConfigIncompleteError(
            "La sección de Entrevistas todavía no está configurada para este entorno."
        )
    _write_atomic(path, previous)  # rollback: local vuelve a existir
    raise EntrevistaRemoteOutcomeUnknownError(
        "No pudimos confirmar si el borrado se sincronizó. Revisá el listado antes de reintentar."
    )
