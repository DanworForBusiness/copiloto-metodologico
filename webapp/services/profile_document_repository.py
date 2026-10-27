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


class ProfileDocumentLockedError(ProfileDocumentStorageError):
    """El candidato dueño del documento está en una consolidación en curso: no se agregan ni se borran documentos hasta que termine."""


class ProfileDocumentReassignError(Exception):
    """``reassign_for_consolidation`` rechazó el movimiento (documento inexistente, de otro candidato o fuera de lo autorizado por la consolidación): zero write."""


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


def _persistence_mode() -> str:
    """Modo de persistencia EXPLÍCITO (`DANWOR_PERSISTENCE`, ver config.py): nunca se infiere de credenciales ni de `remote_store.enabled()`. MISMO criterio que
    `profile_repository`."""
    return str(getattr(config, "PERSISTENCE_MODE", "local"))


def _local_only() -> bool:
    """`local`: la fuente de verdad es el disco (escritura atómica / unlink). NO se requiere ni se intenta ninguna operación contra Spaces (ni push, ni delete_path),
    aunque haya credenciales en el entorno. `remote` conserva íntegro su contrato de confirmación y compensación."""
    return _persistence_mode() == "local"


def _require_writable_mode() -> None:
    """`read-only` NO habilita escrituras de negocio: se rechaza ANTES de tocar el disco (sin efectos parciales) con el mismo error determinístico de "no configurado"."""
    if _persistence_mode() == "read-only":
        raise ProfileDocumentConfigIncompleteError(
            "El almacenamiento de documentos está en modo de solo lectura en este entorno: no se pueden guardar cambios."
        )


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


def _assert_owner_operable(profile_id: str | None) -> None:
    """Un documento nuevo (o un borrado) sobre un candidato bloqueado por una consolidación se rechaza ANTES de tocar el disco. Import diferido: profile_repository no depende de este módulo."""
    if not profile_id:
        return
    from webapp.services import profile_repository
    if profile_repository.is_locked(profile_repository.get(profile_id)):
        raise ProfileDocumentLockedError("Este candidato está en proceso de consolidación: no se pueden agregar ni borrar documentos hasta que termine.")


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


def count_by_profile() -> dict[str, int]:
    """Cantidad de documentos activos por `profile_id` (UNA pasada por el directorio, mismo criterio fail-soft que `list_for_profile`). Solo lectura."""
    _ensure_restored()
    directory = _documents_dir()
    counts: dict[str, int] = {}
    if not directory.exists():
        return counts
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise ProfileDocumentStorageError(f"No se pudo listar los documentos: {exc}") from exc
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("profile_id"):
            counts[payload["profile_id"]] = counts.get(payload["profile_id"], 0) + 1
    return counts


def find_by_folder_item_id(folder_item_id: str) -> list[dict]:
    """Documentos (de CUALQUIER candidato) que referencian esta carpeta de SharePoint por `folder_item_id`. Lo usa el uploader para no reutilizar una carpeta que ya pertenece a OTRO candidato.
    Mismo criterio fail-soft que `list_for_profile`: un archivo corrupto se saltea."""
    _ensure_restored()
    directory = _documents_dir()
    if not directory.exists() or not folder_item_id:
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise ProfileDocumentStorageError(f"No se pudo listar los documentos: {exc}") from exc
    found: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("folder_item_id") == folder_item_id:
            found.append(payload)
    return found


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
    _require_writable_mode()
    _assert_owner_operable(payload.get("profile_id"))
    _write_atomic(path, payload)
    if _local_only():
        return payload                                   # `local`: escritura física local atómica; sin sincronización con Spaces
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
            if not _local_only():                        # `local`: no hay objeto remoto que limpiar ni se contacta a Spaces
                _document_delete_orphan_cleanup(path)
            return False
        _require_writable_mode()                         # `read-only`: ni borrado local ni remoto (sin efectos parciales)
        existing = get(document_id)
        _assert_owner_operable((existing or {}).get("profile_id"))
        if not _local_only() and remote_store.enabled():
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


# ───────────────────────── reasignación EXCEPCIONAL por consolidación de perfiles ─────────────────────────
# Un ProfileDocument es inmutable (arriba: sin update). La consolidación de dos perfiles duplicados necesita UNA excepción acotada: cambiar a qué candidato pertenece un documento, sin tocar
# NADA más (ni `site_id`/`drive_id`/`item_id`/`web_url`/`folder_item_id`: el archivo de SharePoint no se mueve ni se modifica). No es un `update` genérico: solo la invoca
# ``profile_consolidation_service`` con la AUTORIZACIÓN de una operación de consolidación (``authorized``: el mapa ``document_id -> {from, to}`` de su intención auditada) y deja la procedencia
# en el propio documento (``consolidation``).
_CONSOLIDATION_KEY = "consolidation"


def _write_with_compensation(path: Path, candidate: dict, previous: dict) -> None:
    """Escritura atómica + push; ante un push no confirmado se vuelve a escribir (local y remoto) la versión anterior: MISMO contrato de compensación que ``profile_repository.update``."""
    _write_atomic(path, candidate)
    if _local_only():
        return
    if remote_store.push_path(path):
        return
    _write_atomic(path, previous)
    if not remote_store.enabled():
        raise ProfileDocumentConfigIncompleteError("El almacenamiento de documentos todavía no está configurado para este entorno.")
    if remote_store.push_path(path):
        raise ProfileDocumentStorageError("No pudimos confirmar el respaldo del cambio; el documento quedó como estaba.")
    raise ProfileDocumentRemoteOutcomeUnknownError("No pudimos confirmar el estado remoto de este documento tras un fallo de guardado. La versión local quedó como estaba.")


def reassign_for_consolidation(document_id: str, *, operation_id: str, authorized: dict, restore: dict | None = None) -> tuple[dict, bool]:
    """Mueve UN documento al candidato principal de una consolidación, o lo devuelve a su estado original (``restore`` = su imagen previa registrada en la intención). Devuelve ``(documento, cambió)``.

    Acotado y verificado, bajo ``_write_lock``: ``authorized`` debe nombrar ``document_id`` con ``{"from": <perfil actual>, "to": <perfil principal>}``; solo cambian ``profile_id`` y la marca
    ``consolidation`` (operación, origen, destino); todo lo demás se conserva byte a byte. IDEMPOTENTE: si ya está en el destino con la marca de ESTA operación (o, en ``restore``, ya está
    en su imagen previa) es un no-op. Cualquier otro caso (documento inexistente, de otro candidato, no autorizado) → ``ProfileDocumentReassignError`` sin escribir."""
    grant = (authorized or {}).get(document_id)
    if not operation_id or not isinstance(grant, dict) or not grant.get("from") or not grant.get("to"):
        raise ProfileDocumentReassignError("El documento no está autorizado por esta consolidación.")
    with _write_lock:
        current = get(document_id)
        if current is None:
            raise ProfileDocumentReassignError(f"El documento {document_id} no existe.")
        mark = current.get(_CONSOLIDATION_KEY) if isinstance(current.get(_CONSOLIDATION_KEY), dict) else {}
        path = _document_path(document_id)
        if restore is not None:
            if current == restore:
                return current, False
            if current.get("profile_id") != grant["to"] or mark.get("operation_id") != operation_id:
                raise ProfileDocumentReassignError("El documento no está en el estado que dejó esta consolidación: no se revierte.")
            if restore.get("document_id") != document_id or restore.get("profile_id") != grant["from"]:
                raise ProfileDocumentReassignError("La imagen previa no corresponde a este documento.")
            _require_writable_mode()
            _write_with_compensation(path, restore, current)
            return restore, True
        if current.get("profile_id") == grant["to"] and mark.get("operation_id") == operation_id:
            return current, False
        if current.get("profile_id") != grant["from"]:
            raise ProfileDocumentReassignError("El documento no pertenece al candidato que la consolidación espera: no se mueve.")
        _require_writable_mode()
        candidate = {**current, "profile_id": grant["to"], _CONSOLIDATION_KEY: {"operation_id": operation_id, "from_profile_id": grant["from"], "to_profile_id": grant["to"]}}
        _write_with_compensation(path, candidate, current)
        return candidate, True
