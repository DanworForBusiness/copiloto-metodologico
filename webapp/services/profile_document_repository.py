"""
profile_document_repository.py — persistencia de ProfileDocument (Capital
Humano Phase 4.1 — Profile Documents, primera rebanada SharePoint).

Contrato de disco (`human-capital/profile-documents/<document_id>.json`),
UN archivo por ProfileDocument, MISMO patrón exacto de escritura
CRASH-SAFE + write-through síncrono a Spaces que
`webapp/services/profile_repository.py` (tempfile→flush+fsync→
`os.replace` atómico, `remote_store.push_path()` síncrono, reintento único
ante push no confirmado, `ProfileDocumentRemoteOutcomeUnknownError`/
`ProfileDocumentConfigIncompleteError` si ni el intento inicial ni el
reintento confirman) — reusado tal cual, ver ese módulo para el
razonamiento completo de cada decisión de durabilidad (no repetido acá).

IDENTIDAD PREASIGNADA (ticket §7/§15): a diferencia de
`profile_repository.create()` (siempre mintea un `profile_id` nuevo), acá
el `document_id` SIEMPRE lo mintea el caller (`profile_document_service.py`,
`uuid4()` ANTES del PUT a Graph) — este repository solo conoce
`create_with_explicit_id`, MISMA forma que
`profile_repository.create_with_explicit_id` (Phase 3.8): idempotente si
los campos CANÓNICOS coinciden (ALREADY_COMMITTED, zero rewrite),
`ProfileDocumentIdConflictError` si no coinciden (zero write, ANTES de
tocar el disco).

CAMPOS CANÓNICOS (`_CANONICAL_FIELDS`): identidad de negocio de un intento
de commit — incluye `uploaded_at`/`uploaded_by` a propósito (un reintento
de commit legítimo, ver `profile_document_service.retry_commit_document`,
viaja con el MISMO `uploaded_at` que quedó fijado en el momento en que
Graph confirmó el PUT original, dentro del token firmado — dos intentos
con `uploaded_at` distinto NO son el mismo intento, son dos uploads
diferentes que casualmente reusarían un id, lo cual sería un conflicto
real). `web_url` queda AFUERA a propósito (ticket §18: "convenience
metadata only" — no es parte de la identidad, SharePoint podría
normalizar/cambiar su forma sin que cambie la identidad real del recurso,
que ya está fijada por `site_id`+`drive_id`+`item_id`).

SIN update/delete (§7/§24, scope guard explícito): un ProfileDocument, una
vez persistido, es inmutable — no hay "reemplazar" ni "borrar" en esta
rebanada. `list_for_profile()` filtra en memoria sobre TODOS los
documentos (SIN índice/cache, mismo criterio que `profile_repository.
list()` — sin evidencia todavía de que el volumen lo justifique).

LOCK GLOBAL de módulo (`_write_lock`, NUNCA un diccionario por
`document_id`) — MISMO criterio que `profile_repository._write_lock`:
serializa la sección crítica completa de `create_with_explicit_id`
(chequeo de existencia → escritura) dentro de ESTE proceso. Un lock
process-local no protege contra múltiples réplicas — MISMO gate de
producción que Profile (`App Platform replica count == 1`), sin resolver
acá tampoco.

Repository puro: NO conoce `Request`, NO habla Graph, NO genera
`document_id` — solo persiste lo que ya llega shapeado/identificado por el
caller (`webapp/services/profile_document_service.py`)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

_SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_DOCUMENTS_SUBDIR = "profile-documents"
# Acotado, MISMO valor/razonamiento que profile_repository._RESTORE_WAIT_TIMEOUT_S.
_RESTORE_WAIT_TIMEOUT_S = 15.0

_write_lock = threading.Lock()

_CANONICAL_FIELDS = (
    "profile_id", "provider", "site_id", "drive_id", "item_id",
    "filename", "content_type", "size_bytes", "uploaded_at", "uploaded_by",
)


class ProfileDocumentStorageError(Exception):
    """Fallo de PERSISTENCIA (filesystem local o remote_store/Spaces) —
    clasificación por defecto SOURCE_ERROR. Ver
    `ProfileDocumentConfigIncompleteError` para el caso específico de
    Spaces sin configurar."""


class ProfileDocumentConfigIncompleteError(ProfileDocumentStorageError):
    """`remote_store.enabled() is False` — CONFIG_INCOMPLETE, NUNCA un
    fallo transitorio (mismo criterio que
    `profile_repository.ProfileConfigIncompleteError`)."""


class ProfileDocumentRemoteOutcomeUnknownError(Exception):
    """Ni el push inicial ni un reintento explícito al MISMO
    `document_id`/path/contenido confirmaron — MISMO significado semántico
    que `profile_repository.ProfileRemoteOutcomeUnknownError`.
    Deliberadamente NO es un `ProfileDocumentStorageError`: un `except`
    genérico de esa clase nunca debe aplanar esto a SOURCE_ERROR."""


class ProfileDocumentIdConflictError(Exception):
    """`create_with_explicit_id()` encontró un `document_id` que YA existe
    pero con campos CANÓNICOS distintos — zero write, se levanta ANTES de
    tocar el disco. MISMO criterio que
    `profile_repository.ProfileIdConflictError`."""


class ProfileDocumentDeleteOutcomeUnknownError(Exception):
    """`delete()` confirmó el borrado remoto ni en el intento inicial ni en
    un único reintento — MISMO significado semántico que
    `ProfileDocumentRemoteOutcomeUnknownError` para `create()`, pero en
    sentido inverso: acá el archivo local todavía NO se tocó (a diferencia
    de create, que sí escribe y después revierte ante ambigüedad) — ver
    `delete()` para el orden exacto. El caller (`profile_document_service.
    delete_document`) debe tratar esto como "la metadata sigue intacta,
    reintentable" — nunca borra el archivo local sin la confirmación
    remota primero."""


def _document_delete_orphan_cleanup(path: Path) -> None:
    """Best-effort: intenta limpiar un objeto remoto huérfano (de un
    `delete()` previo que haya confirmado el borrado remoto pero se haya
    interrumpido ANTES de borrar el archivo local — no debería poder pasar
    con el orden actual de `delete()`, pero es gratis cubrirlo). Nunca
    lanza — `delete()` sobre un `document_id` que ya no existe es SIEMPRE
    un no-op exitoso."""
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca bloquea el no-op
        _logger.warning("human_capital.profile_document_delete_orphan_cleanup_failed reason=%s", type(exc).__name__)


def _canonical_fields_match(existing: dict, candidate: dict) -> bool:
    return all(existing.get(field) == candidate.get(field) for field in _CANONICAL_FIELDS)


def _documents_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _DOCUMENTS_SUBDIR


def _document_path(document_id: str) -> Path:
    return _documents_dir() / f"{document_id}.json"


def _ensure_restored() -> None:
    """Espera (acotado, nunca indefinido) a que el restore de arranque
    contra Spaces termine, SOLO si genuinamente sigue en curso — MISMO
    criterio que `profile_repository._ensure_restored`."""
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _write_atomic(path: Path, payload: dict) -> None:
    """Escritura CRASH-SAFE — MISMO patrón exacto que
    `profile_repository._write_atomic`."""
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
        raise ProfileDocumentStorageError(f"No se pudo escribir el documento en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _cleanup_failed_write(path: Path) -> None:
    """Revierte un write local cuya durabilidad remota nunca se confirmó —
    MISMO criterio/razonamiento que `profile_repository._cleanup_failed_write`."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("human_capital.profile_document_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca enmascara el fallo real
        _logger.warning("human_capital.profile_document_remote_cleanup_failed reason=%s", type(exc).__name__)


def get(document_id: str) -> dict | None:
    """`None` si el archivo no existe o está corrupto — mismo criterio
    fail-soft que `profile_repository.get`."""
    _ensure_restored()
    path = _document_path(document_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProfileDocumentStorageError(f"No se pudo leer el documento: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def list_for_profile(profile_id: str) -> list[dict]:
    """Lee y parsea CADA archivo del directorio en esta misma llamada —
    sin cache/índice, mismo criterio que `profile_repository.list()`. Un
    archivo individual corrupto se SALTEA (nunca rompe el listado
    completo)."""
    _ensure_restored()
    directory = _documents_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise ProfileDocumentStorageError(f"No se pudo listar los documentos: {exc}") from exc
    documents: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            raw = entry.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, ValueError):
            continue  # archivo corrupto/ilegible -- se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("profile_id") == profile_id:
            documents.append(payload)
    return documents


def _create_locked(document_id: str, document: dict) -> dict:
    """Cuerpo de `create_with_explicit_id` bajo `_write_lock` — MISMA
    secuencia de fallo/reintento que `profile_repository._create_locked`
    (push → si no confirma y Spaces sin configurar: CONFIG_INCOMPLETE
    determinístico, sin reintentar → si Spaces SÍ configurado: un único
    reintento → si tampoco confirma: limpieza + REMOTE_OUTCOME_UNKNOWN).
    NO agrega timestamps propios: `document` ya llega completo (incluye
    `uploaded_at`/`uploaded_by`, fijados por el caller en el momento en
    que Graph confirmó el upload, nunca por este repository) — solo
    agrega `schema_version`/`document_id`, campos de FORMATO, nunca de
    negocio."""
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "document_id": document_id,
        **document,
    }
    path = _document_path(document_id)
    _write_atomic(path, payload)
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        _cleanup_failed_write(path)
        raise ProfileDocumentConfigIncompleteError(
            "El almacenamiento de documentos todavía no está configurado para este entorno."
        )
    if remote_store.push_path(path):
        return payload
    _cleanup_failed_write(path)
    raise ProfileDocumentRemoteOutcomeUnknownError(
        "No pudimos confirmar si este documento se guardó. Antes de reintentar, revisá el listado."
    )


def create_with_explicit_id(document_id: str, document: dict) -> tuple[dict, bool]:
    """CREATE-ONLY con identidad PREASIGNADA — invariante no negociable:
    esto JAMÁS sobrescribe un ProfileDocument committed existente.

    Bajo `_write_lock` (sección crítica completa: chequeo de existencia →
    write local → durabilidad remota):
      1. `get(document_id)` — si YA existe:
         - campos CANÓNICOS coinciden EXACTO (`_canonical_fields_match`):
           retry idempotente (upload-retry o commit-retry, ver
           `profile_document_service.py`) → `(existing, False)`,
           ALREADY_COMMITTED, cero rewrite.
         - si NO coinciden: `ProfileDocumentIdConflictError`, zero write.
      2. Si NO existe: procede como una creación nueva (vía
         `_create_locked`) y devuelve `(payload, True)`, CREATED."""
    with _write_lock:
        existing = get(document_id)
        if existing is not None:
            if _canonical_fields_match(existing, document):
                return existing, False
            raise ProfileDocumentIdConflictError(
                f"El documento {document_id} ya existe con datos distintos a los de este intento."
            )
        return _create_locked(document_id, document), True


def delete(document_id: str) -> bool:
    """Elimina la metadata persistida de un ProfileDocument. IDEMPOTENTE
    por diseño — invariante no negociable, simétrica a `create_with_
    explicit_id`: un segundo `delete()` del MISMO `document_id` (o un
    `delete()` sobre un `document_id` que nunca existió) es SIEMPRE un
    no-op exitoso, `False`, NUNCA una excepción — a diferencia de create,
    acá "ya no está" nunca es un conflicto.

    Bajo `_write_lock` (misma sección crítica que create: leer estado
    actual → mutar disco → confirmar remoto):
      1. Si el archivo local NO existe: no-op — se intenta igual un borrado
         remoto best-effort (cubre un objeto huérfano de un `delete()`
         previo interrumpido tras confirmar lo remoto pero antes de borrar
         el archivo local) y devuelve `False`.
      2. Si existe: PRIMERO confirma el borrado remoto (intento + un único
         reintento, MISMO contrato de durabilidad que `create()` pero
         INVERTIDO en el orden — remoto antes que local, nunca al revés,
         así que ante ambigüedad remota el archivo local queda INTACTO,
         nunca a medio borrar). Si Spaces está deshabilitado no
         hay nada remoto que confirmar (a diferencia de create, donde
         deshabilitado bloquea la operación entera vía CONFIG_INCOMPLETE —
         acá el archivo YA es local-only, borrarlo localmente basta). Sólo
         tras la confirmación remota (o Spaces deshabilitado) se borra el
         archivo local, y devuelve `True`."""
    with _write_lock:
        path = _document_path(document_id)
        if not path.exists():
            _document_delete_orphan_cleanup(path)
            return False
        if remote_store.enabled():
            if not remote_store.delete_path(path):
                if not remote_store.delete_path(path):
                    raise ProfileDocumentDeleteOutcomeUnknownError(
                        "No pudimos confirmar el borrado remoto de este documento. Podés reintentar la eliminación."
                    )
        try:
            path.unlink()
        except OSError as exc:
            raise ProfileDocumentStorageError(f"No se pudo borrar el documento: {exc}") from exc
        return True
