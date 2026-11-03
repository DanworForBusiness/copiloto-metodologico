"""cv_incorporation.py — MOTOR COMÚN de incorporación de CVs al Banco de CCHH: lo que comparten la importación histórica (`cv_history_import_exec`) y la bandeja operativa de Carga masiva (`cv_inbox`).

Extraído TAL CUAL de `cv_history_import_exec` (mismo comportamiento, mismos tests): no es un framework ni agrega lógica nueva. Contiene SOLO piezas sin estado de lote:
  * `RETRYABLE` / `classify(exc)`: la tabla ÚNICA que traduce los errores de los servicios actuales (perfil, documento, Graph, manifiesto) a una decisión — DETENER la tanda (permiso, configuración, persistencia
    no confirmada, token vencido), dejar el contenido FALLIDO o BLOQUEADO con un código, o REINTENTAR. El orden de la tabla es el del `except` original y importa (p. ej. la configuración incompleta de perfiles es
    también un `ProfileStorageError`, que en otro caso sería reintentable).
  * `is_401(exc)`: un token vencido no es un intento fallido del documento.
  * `verify_remote(remote, group, token)`: ¿el archivo que ya está en SharePoint ES este contenido? Por tamaño y por contenido (hash comparable o sha256), nunca solo por nombre.
  * `content_type(filename)`.

El ESTADO (lote, libro, plan, bandeja) vive en cada origen: este módulo no lee ni escribe ningún libro."""
from __future__ import annotations

import hashlib
import urllib.error

from webapp.services import (cv_history_manifest as manifest, profile_document_repository, profile_document_service as docsvc, profile_document_validation, profile_repository)

STOP, TOKEN, FAILED, BLOCKED, RETRY = "STOP", "TOKEN", "FAILED", "BLOCKED", "RETRY"

RETRYABLE = (docsvc.ProfileDocumentGraphSourceError, docsvc.ProfileDocumentFolderOutcomeUnknownError, docsvc.ProfileDocumentUploadRetryRequired, docsvc.ProfileDocumentCommitRetryRequired,
             profile_repository.ProfileStorageError, profile_repository.ProfileRemoteOutcomeUnknownError, profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError,
             urllib.error.URLError, OSError)

_CONFIG_INCOMPLETE = (docsvc.ProfileDocumentConfigIncompleteError, profile_repository.ProfileConfigIncompleteError, profile_document_repository.ProfileDocumentConfigIncompleteError)


def is_401(exc: BaseException) -> bool:
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def classify(exc: BaseException) -> tuple[str, str | None] | None:
    """`(tipo, código)` para un error de una unidad de incorporación, o None si no es un error conocido (el llamador lo relanza). Tipos: STOP (cortar la tanda), TOKEN (token vencido), FAILED, BLOCKED, RETRY."""
    if isinstance(exc, docsvc.ProfileDocumentPermissionDeniedError):
        return STOP, "PERMISSION_DENIED"
    if isinstance(exc, _CONFIG_INCOMPLETE):
        return STOP, "CONFIG_INCOMPLETE"
    if isinstance(exc, manifest.ManifestDurabilityError):
        return STOP, "PERSISTENCE_ERROR"
    if isinstance(exc, profile_document_validation.ProfileDocumentValidationError):
        return FAILED, "INVALID_DOCUMENT"
    if isinstance(exc, profile_repository.ProfileIdConflictError):
        return BLOCKED, "PROFILE_ID_CONFLICT"
    if isinstance(exc, profile_document_repository.ProfileDocumentIdConflictError):
        return BLOCKED, "DOC_ID_CONFLICT"
    if isinstance(exc, ValueError):
        return FAILED, "INVALID_PROFILE_FIELDS"
    if isinstance(exc, RETRYABLE):
        return (TOKEN, "TOKEN_EXPIRED") if is_401(exc) else (RETRY, type(exc).__name__)
    return None


def verify_remote(remote: dict, group: dict, token: str) -> bool:
    """¿El archivo que ya está en SharePoint ES este contenido? Por TAMAÑO y por CONTENIDO: el hash comparable del origen (mismo algoritmo) o, si Graph no informa uno comparable, el sha256 del archivo remoto descargado."""
    if int(remote.get("size") or -1) != int(group["size"]):
        return False
    source_hash = group.get("hash") or {}
    remote_hashes = (remote.get("file") or {}).get("hashes") or {}
    if source_hash.get("algo") and source_hash.get("value") and remote_hashes.get(source_hash["algo"]):
        return remote_hashes[source_hash["algo"]] == source_hash["value"]
    return hashlib.sha256(docsvc.read_remote_document_bytes(remote["id"], token)).hexdigest() == group["sha256"]


def content_type(filename: str) -> str:
    return profile_document_validation.ACCEPTED_CONTENT_TYPES[filename.rsplit(".", 1)[-1].lower() if "." in filename else ""]
