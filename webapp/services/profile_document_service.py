"""
profile_document_service.py — Capital Humano Phase 4.1 (Profile Documents
— SharePoint CV storage, primera rebanada). Orquesta TODO lo que el router
necesita para subir/reintentar un documento de Profile: validación de
formato (`profile_document_validation.py`), carpeta por Profile en
SharePoint, el contrato de upload con reintento único, los DOS tokens
firmados de reintento (upload-retry §16, commit-retry §17), y la
persistencia final (`profile_document_repository.py`). El router NUNCA
habla Graph ni conoce `document_id`/rutas de SharePoint directamente —
solo llama a este service y traduce sus excepciones a contratos HTTP,
MISMA división de responsabilidad que `human_capital_service.py` frente a
`profile_repository.py`.

GRAPH TRANSPORT: solo los tres primitivos de `core.microsoft365`
(`graph_get_json`/`graph_post_json`/`graph_put_bytes`) — sin SDK, sin
PATCH, sin DELETE (ticket §9). Se llama SIEMPRE vía el módulo
(`microsoft365.graph_x(...)`), nunca con `from core.microsoft365 import
graph_x`, para que los tests puedan monkeypatchear el símbolo del módulo.

IDENTIDAD DE CARPETA/ARCHIVO (§8): la carpeta de un Profile es
determinística (`<CV root>/<profile_id>` — nunca nombre/email de la
persona) y el archivo remoto es `<document_id>__<nombre-sanitizado>` —
nunca el nombre original solo, para que dos uploads del mismo nombre de
archivo (p. ej. "CV.pdf" subido por dos personas distintas) nunca
colisionen de path en la MISMA carpeta.

FOLDER-ENSURE (§10): lookup por PATH relativo al item raíz
(`.../items/{root}:/{profile_id}`, sintaxis estándar de Graph para
direccionar un hijo por path) — 200 existe, 404 ausente. Ausente → POST a
`.../items/{root}/children` con `conflictBehavior=fail` (nunca `rename`/
`replace`: un 409 de una carrera concurrente se RESUELVE re-leyendo la
carpeta ya creada por el otro request, nunca creando una segunda carpeta
con nombre distinto). Nunca persiste el `item_id` de la carpeta dentro del
Profile (§10, explícito) — se resuelve en cada upload.

UPLOAD CONTRACT (§15): intento 1, si NO confirma (`HTTPError`/`URLError`
que NO sea 403 — un 403 es una clasificación DEFINITIVA de permiso, nunca
"ambiguo") un ÚNICO reintento a la MISMA URL con los MISMOS bytes. Si
NINGUNO confirma: `ProfileDocumentUploadRetryRequired`, con un token
firmado (§16) que preserva el MISMO `document_id`/path — un reintento del
usuario NUNCA mintea un `document_id` nuevo. `document_id` se mintea ANTES
del primer PUT (§15, explícito) — así el MISMO id sobrevive a cualquier
cantidad de reintentos de ESTE intento de upload.

METADATA COMMIT (§17): si Graph confirma pero
`profile_document_repository.create_with_explicit_id` levanta
`ProfileDocumentRemoteOutcomeUnknownError` (push a Spaces no confirmado),
se emite un SEGUNDO token distinto (`purpose` distinto) que permite
reintentar SOLO la persistencia — CERO PUT nuevo a Graph en ese camino."""
from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.error
from uuid import uuid4

from jose import JWTError, jwt

import config
from core import microsoft365
from webapp.services import profile_document_repository, profile_document_validation, sharepoint_graph_diagnostics

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Acotado (nunca indefinido) — MISMO criterio que
# profile_import_service._ROW_TOKEN_MAX_AGE_S. 30 min (ticket §16,
# explícito para upload-retry; aplicado también a commit-retry por el
# MISMO criterio, el ticket no da un valor distinto para ese segundo token).
_RETRY_TOKEN_MAX_AGE_S = 1800

_UPLOAD_RETRY_PURPOSE = "human_capital_profile_document_upload_retry"
_COMMIT_RETRY_PURPOSE = "human_capital_profile_document_commit_retry"

_PERMISSION_DENIED_MESSAGE = "No tenés permiso para escribir documentos en SharePoint. Contactá al administrador."
_GRAPH_SOURCE_ERROR_MESSAGE = "No pudimos completar la operación con SharePoint. Probá de nuevo en unos minutos."


class ProfileDocumentConfigIncompleteError(Exception):
    """`HC_SHAREPOINT_SITE_ID`/`HC_SHAREPOINT_DRIVE_ID`/`HC_CV_ROOT_ITEM_ID`
    ausente — CONFIG_INCOMPLETE (§20), fail-closed, chequeado ANTES de
    cualquier llamada a Graph. Clase DISTINTA de
    `profile_document_repository.ProfileDocumentConfigIncompleteError`
    (esa es sobre Spaces, ésta sobre la config de Graph/SharePoint) —
    nunca se confunden aunque el router las mapee al mismo estado visual."""


class ProfileDocumentPermissionDeniedError(Exception):
    """403 de Graph — GRAPH_PERMISSION_DENIED (§14/§20), NUNCA tratado
    como ambiguo/reintentable: la autorización insuficiente es una
    clasificación DEFINITIVA (necesita consentimiento de administrador,
    no un reintento del usuario)."""


class ProfileDocumentGraphSourceError(Exception):
    """Fallo de Graph CONFIRMADO (una respuesta HTTP real, con código) que
    no es 403/404/409 — un rechazo definitivo de Graph, nunca ambiguo,
    nunca reintentado acá (reintentar un 4xx/5xx confirmado solo
    amplificaría un error real, p. ej. de validación). También el
    resultado de un fallo de TRANSPORTE en un paso de solo LECTURA (nada
    se intentó escribir todavía, así que no hay ambigüedad de "¿se creó
    algo?" que proteger — ver `ProfileDocumentFolderOutcomeUnknownError`
    para ESA distinción, específica de la escritura de creación de
    carpeta)."""


class ProfileDocumentFolderOutcomeUnknownError(Exception):
    """Fase 4.1.1, corrective (ticket §8): ni el intento de creación de
    carpeta NI su único reintento (MISMO `profile_id`/payload/parent en
    ambos) recibieron una respuesta HTTP confirmada — un fallo de
    TRANSPORTE (`URLError` sin `HTTPError`, o cualquier "no-ACK") tras un
    POST de escritura NO prueba que la carpeta no se haya creado del lado
    del servidor. Deliberadamente distinta de `ProfileDocumentGraphSourceError`
    (que solo cubre respuestas CONFIRMADAS): un `except
    ProfileDocumentGraphSourceError` genérico nunca debe aplanar esto a
    "seguro que falló". Sin token de reintento propio (a diferencia del
    upload) — el `document_id` todavía no existe en este punto del flujo
    (§15: se mintea DESPUÉS de `ensure_profile_folder`), así que un
    reintento seguro es simplemente que el usuario reenvíe la MISMA acción
    de upload: `ensure_profile_folder` es idempotente por diseño (lookup
    por path + 409→re-lee), así que una carpeta que sí se haya creado en
    el intento ambiguo aparecerá como "existente" en el próximo intento."""


class ProfileDocumentUploadRetryRequired(Exception):
    """Ni el PUT inicial ni el único reintento (MISMA URL/bytes)
    confirmaron — REMOTE_OUTCOME_UNKNOWN (§15/§20). Lleva el token firmado
    de upload-retry (§16) listo para que el router se lo devuelva al
    browser."""

    def __init__(self, retry_token: str):
        self.retry_token = retry_token
        super().__init__(
            "No pudimos confirmar si el archivo se subió a SharePoint. Podés reintentar con el mismo archivo."
        )


class ProfileDocumentCommitRetryRequired(Exception):
    """Graph SÍ confirmó el PUT pero la persistencia del ProfileDocument
    no — DOCUMENT_METADATA_PERSISTENCE_ERROR (§17/§20). Lleva el token
    firmado de commit-retry, que reintenta SOLO la persistencia (cero PUT
    nuevo)."""

    def __init__(self, retry_token: str):
        self.retry_token = retry_token
        super().__init__(
            "El archivo se subió a SharePoint pero no pudimos confirmar el registro. "
            "Podés reintentar el registro sin volver a subir el archivo."
        )


class ProfileDocumentRetryFileMismatchError(Exception):
    """El SHA-256 del archivo re-seleccionado en un upload-retry NO
    coincide con el original — RETRY_FILE_MISMATCH (§16/§20). Zero PUT a
    Graph, zero write."""


class RetryTokenError(Exception):
    """Token de reintento (upload o commit) que no verifica — firma
    inválida/alterada/expirada/purpose incorrecto/usuario distinto.
    SIEMPRE mensaje seguro, nunca un `JWTError` crudo."""


# ---------------------------------------------------------------------------
# Delete (Fase 4.1.2, ver HANDOFF): borrado end-to-end de UN ProfileDocument.
# Orden ESTRICTO, no negociable (ticket): físico primero, metadata después.
# Nunca al revés — borrar la metadata antes de confirmar el físico dejaría
# un archivo huérfano en SharePoint sin registro, invisible para el Hub
# pero consumiendo espacio/permaneciendo accesible por `web_url` si alguien
# lo guardó. IDENTIDAD del documento a borrar: SIEMPRE `profile_id` +
# `document_id` (nunca filename/URL) — y el `site_id`/`drive_id`/`item_id`
# usados para el DELETE físico salen de la metadata YA PERSISTIDA de ESE
# documento, nunca de `config.HC_*` vigente (que podría haber rotado desde
# que el documento se creó — la identidad canónica de un documento es la
# que quedó fijada en el momento de su propio commit, ver
# `_commit_uploaded_document`). Sin retry token propio (a diferencia de
# upload/commit): `delete_document` es naturalmente idempotente por
# `document_id` — un DELETE de Graph sobre un item ya borrado responde 404,
# que este mismo código ya trata como éxito, así que un reintento EXTERNO
# (el usuario repitiendo la acción "Eliminar") es siempre seguro sin
# necesitar un token firmado que preserve estado entre requests.
# ---------------------------------------------------------------------------


class ProfileDocumentNotFoundError(Exception):
    """`document_id` no existe, o existe pero pertenece a otro `profile_id`
    — 404 desde la perspectiva del router. Nunca revela la existencia de un
    documento de OTRO Profile (least-privilege, MISMO criterio que
    `_profile_or_404` en el router: un id ajeno se ve exactamente igual que
    un id inexistente)."""


class ProfileDocumentDeletePermissionDeniedError(Exception):
    """401/403 de Graph al intentar el DELETE físico — clasificación
    DEFINITIVA (necesita consentimiento de administrador, no un
    reintento), metadata SIEMPRE intacta: nunca se borra si Graph rechazó
    por autenticación/autorización."""


class ProfileDocumentDeleteGraphSourceError(Exception):
    """Fallo CONFIRMADO de Graph (código de respuesta real, distinto de
    401/403/404) al intentar el DELETE físico — metadata SIEMPRE intacta."""


class ProfileDocumentDeletePhysicalOutcomeUnknownError(Exception):
    """Fallo de TRANSPORTE (`URLError` sin `HTTPError` — ningún código de
    respuesta confirmado) al intentar el DELETE físico — NO prueba que el
    archivo se haya borrado o no del lado de Graph. Metadata SIEMPRE
    intacta: nunca se borra ante ambigüedad. El usuario puede reintentar la
    MISMA acción "Eliminar" — idempotente por diseño (ver arriba)."""


class ProfileDocumentMetadataDeleteOutcomeUnknownError(Exception):
    """El físico SÍ se confirmó borrado (204 o 404) pero
    `profile_document_repository.delete()` no pudo confirmar el borrado
    remoto de la metadata en Spaces — el documento podría seguir apareciendo
    listado. El usuario puede reintentar la MISMA acción "Eliminar": el
    físico ya está borrado (un segundo DELETE a Graph sobre el mismo
    `item_id` responde 404, que este código ya trata como éxito), así que
    un reintento completo de `delete_document()` termina llamando de nuevo
    a `profile_document_repository.delete()` — que es en sí mismo
    idempotente — hasta confirmar."""


def _document_item_url(site_id: str, drive_id: str, item_id: str) -> str:
    return f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{item_id}"


def _delete_physical_document(*, site_id: str, drive_id: str, item_id: str, access_token: str) -> None:
    """UN intento de DELETE físico — 204 y 404 son AMBOS éxito (404 = ya
    ausente, ticket explícito). 401/403 → permission denied. Cualquier otro
    código CONFIRMADO → GraphSourceError. Fallo de transporte (sin código)
    → PhysicalOutcomeUnknown. Sin reintento interno (a diferencia de
    `upload_document_content`) — ver comentario de sección arriba: un
    reintento de TODA la acción "Eliminar" ya es seguro porque un segundo
    DELETE sobre un item ausente es 404, que este mismo código trata como
    éxito, así que duplicar el reintento acá sería redundante."""
    url = _document_item_url(site_id, drive_id, item_id)
    try:
        microsoft365.graph_delete(url, access_token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        if exc.code in (401, 403):
            raise ProfileDocumentDeletePermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        raise ProfileDocumentDeleteGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
    except urllib.error.URLError as exc:
        raise ProfileDocumentDeletePhysicalOutcomeUnknownError(
            "No pudimos confirmar si el archivo se borró de SharePoint. Podés reintentar la eliminación."
        ) from exc


def delete_document(*, profile_id: str, document_id: str, access_token: str) -> None:
    """Camino de borrado de UN ProfileDocument. Orden ESTRICTO (ver
    comentario de sección):
      1. resuelve la metadata por `profile_id`+`document_id` — NUNCA
         filename/URL; ausente o de OTRO Profile → `ProfileDocumentNotFoundError`;
      2. DELETE físico PRIMERO, con `site_id`/`drive_id`/`item_id` de ESA
         metadata (nunca de `config.HC_*` vigente);
      3. sólo si el físico se confirma ausente (204 o 404) se borra la
         metadata (`profile_document_repository.delete`, en sí mismo
         idempotente);
      4. 401/403 o ambigüedad de transporte del físico → excepción propia,
         CERO llamada a `profile_document_repository.delete` — la metadata
         nunca se toca si el físico no se confirmó ausente.

    `require_graph_config()` primero (MISMO gate fail-closed que
    `upload_document` — el mismo scope de escritura que subió el archivo
    hace falta para borrarlo)."""
    require_graph_config()
    document = profile_document_repository.get(document_id)
    if document is None or document.get("profile_id") != profile_id:
        raise ProfileDocumentNotFoundError(f"El documento {document_id} no existe para este Profile.")
    _delete_physical_document(
        site_id=document["site_id"], drive_id=document["drive_id"], item_id=document["item_id"],
        access_token=access_token,
    )
    try:
        profile_document_repository.delete(document_id)
    except profile_document_repository.ProfileDocumentDeleteOutcomeUnknownError as exc:
        raise ProfileDocumentMetadataDeleteOutcomeUnknownError(
            "El archivo se borró de SharePoint pero no pudimos confirmar el borrado del registro. "
            "Podés reintentar la eliminación."
        ) from exc


# ---------------------------------------------------------------------------
# URLs de Graph (identidad ESTABLE vía config, nunca una URL de sharing).
# ---------------------------------------------------------------------------

def _drive_items_base() -> str:
    return f"{GRAPH_BASE}/sites/{config.HC_SHAREPOINT_SITE_ID}/drives/{config.HC_SHAREPOINT_DRIVE_ID}/items"


def _folder_by_path_url(profile_id: str) -> str:
    return f"{_drive_items_base()}/{config.HC_CV_ROOT_ITEM_ID}:/{profile_id}"


def _cv_root_children_url() -> str:
    return f"{_drive_items_base()}/{config.HC_CV_ROOT_ITEM_ID}/children"


def upload_content_url(folder_item_id: str, remote_filename: str) -> str:
    return f"{_drive_items_base()}/{folder_item_id}:/{remote_filename}:/content"


def _folder_create_body(profile_id: str) -> dict:
    # `conflictBehavior=fail` a propósito (§10): un 409 se maneja
    # explícitamente re-leyendo la carpeta existente — NUNCA se delega a
    # Graph un rename/replace automático que crearía una carpeta con
    # nombre distinto al determinístico esperado.
    return {"name": profile_id, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}


def require_graph_config() -> None:
    """CONFIG_INCOMPLETE fail-closed (§20; §5 de la Fase 4.1.1 corrective
    agregó `HC_DOCUMENT_WRITE_SCOPE`) — chequeado ANTES de cualquier
    llamada a Graph, para que un entorno sin configurar nunca intente un
    request que de todos modos va a fallar de forma menos clara. El
    browser también falla cerrado del lado de la adquisición de token
    (ver `hub_auth.js::dwHumanCapitalDocumentWriteToken`, que devuelve ""
    si el scope no está configurado en `/api/auth/microsoft/config`) — ESTE
    chequeo server-side es la clasificación explícita CONFIG_INCOMPLETE,
    independiente de si el browser ya mandó o no un `graph_token` vacío."""
    if not (
        config.HC_SHAREPOINT_SITE_ID and config.HC_SHAREPOINT_DRIVE_ID and config.HC_CV_ROOT_ITEM_ID
        and config.HC_DOCUMENT_WRITE_SCOPE
    ):
        raise ProfileDocumentConfigIncompleteError(
            "La integración de documentos con SharePoint todavía no está configurada para este entorno."
        )


# ---------------------------------------------------------------------------
# Nombre de archivo determinístico (§8).
# ---------------------------------------------------------------------------

def _sanitize_filename(original: str) -> str:
    """Conserva alfanuméricos/guion/guion bajo/punto del nombre original,
    reemplaza el resto por `-`, colapsa repeticiones, acota longitud —
    NUNCA usa el nombre/email de la persona como identidad de archivo
    (§8, explícito: eso es responsabilidad de `document_id`, no de este
    string, que es solo legibilidad humana en SharePoint)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", original.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:150] or "documento"


def build_remote_filename(document_id: str, original_filename: str) -> str:
    return f"{document_id}__{_sanitize_filename(original_filename)}"


# ---------------------------------------------------------------------------
# Folder-ensure (§10).
# ---------------------------------------------------------------------------

def ensure_profile_folder(profile_id: str, access_token: str) -> str:
    """Devuelve el `item_id` de la carpeta `<CV root>/<profile_id>`,
    creándola si hace falta. Contrato EXACTO del ticket §9 (Fase 4.1.1,
    corrective):
      1. nombre determinístico = `profile_id`;
      2. GET por path bajo la raíz configurada — 200 existe, 404 ausente
         confirmado (una respuesta HTTP real en ambos casos);
      3. ausente → intenta crear (`_create_folder_with_retry`, ver abajo):
         confirmado (201/409) resuelve de inmediato; AMBIGUO (fallo de
         transporte, sin respuesta HTTP) → UN único reintento antes de
         rendirse;
      4. devuelve el `item_id`.

    El GET inicial es de solo LECTURA — un fallo de transporte ahí NUNCA
    es ambiguo sobre "¿se creó algo?" (nada se intentó escribir todavía),
    así que se clasifica de inmediato como `ProfileDocumentGraphSourceError`,
    sin retry. La ambigüedad real (§8) vive exclusivamente en el POST de
    creación, ver `_create_folder_with_retry`."""
    url = _folder_by_path_url(profile_id)
    try:
        existing = microsoft365.graph_get_json(url, access_token)
        return existing["id"]
    except urllib.error.HTTPError as exc:
        # Diagnóstico (sólo observabilidad): el cuerpo real de Graph (code/message/innerError) se registraba nunca. Un 404 es el caso NORMAL «carpeta aún no existe» → INFO.
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.lookup", "GET", url, exc, level=logging.INFO if exc.code == 404 else logging.WARNING)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        if exc.code != 404:
            raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.lookup", "GET", url, exc)
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc

    return _create_folder_with_retry(profile_id, access_token)


def _attempt_create_folder(profile_id: str, access_token: str) -> tuple[str | None, bool]:
    """UN intento de POST de creación. Devuelve `(item_id, confirmed)`:
      - `(item_id, True)`: 201 confirmado, carpeta recién creada.
      - `(None, True)`: 409 CONFIRMADO — otro request ya la creó; el
        caller re-lee (nunca crea una segunda carpeta con nombre distinto).
      - `(None, False)`: fallo AMBIGUO — `URLError` sin `HTTPError`
        (ningún código de respuesta en absoluto). NO prueba que la carpeta
        no se haya creado (ticket §8) — el caller decide si reintenta.

    Un 403, o cualquier OTRO 4xx/5xx CONFIRMADO (código de respuesta real,
    distinto de 409), es una clasificación DEFINITIVA — se propaga como
    excepción de inmediato, nunca participa de la ambigüedad "reintentar
    o no"."""
    url = _cv_root_children_url()
    try:
        created = microsoft365.graph_post_json(url, access_token, _folder_create_body(profile_id))
        return created["id"], True
    except urllib.error.HTTPError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.create", "POST", url, exc, level=logging.INFO if exc.code == 409 else logging.WARNING)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        if exc.code == 409:
            return None, True
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.create", "POST", url, exc)
        return None, False


def _reread_folder_after_conflict(profile_id: str, access_token: str) -> str:
    """Re-lee tras un 409 CONFIRMADO — en ese punto ya se sabe que la
    carpeta existe (la creó otro request), así que un fallo de transporte
    ACÁ es un problema de LECTURA pura, no de "¿se creó algo?" — se
    clasifica de inmediato (403 → permission denied, cualquier otra cosa →
    GRAPH_SOURCE_ERROR), sin un tercer intento de creación ni un retry
    anidado."""
    url = _folder_by_path_url(profile_id)
    try:
        existing = microsoft365.graph_get_json(url, access_token)
        return existing["id"]
    except urllib.error.HTTPError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.reread_after_conflict", "GET", url, exc)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.reread_after_conflict", "GET", url, exc)
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc


def _create_folder_with_retry(profile_id: str, access_token: str) -> str:
    """Contrato de creación con UN único reintento ante ambigüedad (ticket
    §9) — MISMO `profile_id`/payload/parent en AMBOS intentos, nunca una
    segunda carpeta con nombre distinto. Ambos intentos ambiguos (ninguna
    respuesta HTTP confirmada en ninguno) → `ProfileDocumentFolderOutcomeUnknownError`."""
    item_id, confirmed = _attempt_create_folder(profile_id, access_token)
    if confirmed:
        return item_id if item_id is not None else _reread_folder_after_conflict(profile_id, access_token)

    item_id, confirmed = _attempt_create_folder(profile_id, access_token)
    if confirmed:
        return item_id if item_id is not None else _reread_folder_after_conflict(profile_id, access_token)

    raise ProfileDocumentFolderOutcomeUnknownError(
        "No pudimos confirmar si la carpeta de este candidato se creó en SharePoint. "
        "Podés reintentar la acción — es seguro repetirla."
    )


# ---------------------------------------------------------------------------
# Upload contract (§15).
# ---------------------------------------------------------------------------

def _put_once(url: str, access_token: str, data: bytes, content_type: str) -> dict | None:
    """`None` significa "no confirmado" (transporte/5xx/lo-que-sea que no
    sea 403) — nunca prueba ausencia, solo ausencia de confirmación. Un
    403 es DEFINITIVO, se propaga de inmediato (nunca participa del
    contrato "unconfirmed -> retry")."""
    try:
        return microsoft365.graph_put_bytes(url, access_token, data, content_type=content_type)
    except urllib.error.HTTPError as exc:
        # Diagnóstico: hoy un 4xx/5xx NO-403 acá se aplana a «no confirmado → reintento» y el motivo real de Graph se perdía.
        sharepoint_graph_diagnostics.log_graph_failure("upload.put", "PUT", url, exc)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        return None
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("upload.put", "PUT", url, exc)
        return None


class ProfileDocumentRemoteUploadUnknownError(Exception):
    """Señal de transporte INTERNA (nunca cruza a `human_capital.py`):
    ninguno de los dos intentos de PUT confirmó. `upload_document`/
    `retry_upload_document` la capturan y la traducen a
    `ProfileDocumentUploadRetryRequired` CON el token firmado — esta clase
    separada existe para que `upload_document_content` (mecánica de
    transporte pura, reusada por ambos callers) no necesite conocer
    document_id/profile_id/firma."""


def upload_document_content(url: str, access_token: str, data: bytes, content_type: str) -> dict:
    """Intento + UN único reintento, MISMA URL/bytes en ambos (§15).
    Devuelve el `driveItem` del PUT confirmado. Si NINGUNO de los dos
    confirma: `ProfileDocumentRemoteUploadUnknownError`."""
    result = _put_once(url, access_token, data, content_type)
    if result is not None:
        return result
    result = _put_once(url, access_token, data, content_type)
    if result is not None:
        return result
    raise ProfileDocumentRemoteUploadUnknownError(
        "No pudimos confirmar si el archivo se subió a SharePoint tras un reintento."
    )


# ---------------------------------------------------------------------------
# Retry tokens (§16/§17) — MISMO signer/algoritmo que
# profile_import_service._sign_row_token (jose.jwt + config.SESSION_SECRET,
# HS256, claim `purpose` obligatorio).
# ---------------------------------------------------------------------------

def _verify_token(token: str, expected_purpose: str) -> dict:
    try:
        claims = jwt.decode(token, config.SESSION_SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise RetryTokenError("El estado de este reintento expiró o no es válido.") from exc
    if claims.get("purpose") != expected_purpose:
        raise RetryTokenError("El estado de este reintento no es válido.")
    return claims


def sign_upload_retry_token(
    *, document_id: str, profile_id: str, folder_item_id: str, filename: str,
    original_filename: str, content_type: str, size_bytes: int, sha256: str, initiated_by: str,
) -> str:
    claims = {
        "purpose": _UPLOAD_RETRY_PURPOSE,
        "document_id": document_id,
        "profile_id": profile_id,
        "folder_item_id": folder_item_id,
        "filename": filename,
        "original_filename": original_filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "initiated_by": initiated_by,
        "exp": int(time.time()) + _RETRY_TOKEN_MAX_AGE_S,
    }
    return jwt.encode(claims, config.SESSION_SECRET, algorithm="HS256")


def verify_upload_retry_token(token: str) -> dict:
    return _verify_token(token, _UPLOAD_RETRY_PURPOSE)


def sign_commit_retry_token(
    *, document_id: str, profile_id: str, provider: str, site_id: str, drive_id: str, item_id: str,
    web_url: str | None, filename: str, content_type: str, size_bytes: int, uploaded_at: str,
    uploaded_by: str, initiated_by: str,
) -> str:
    claims = {
        "purpose": _COMMIT_RETRY_PURPOSE,
        "document_id": document_id,
        "profile_id": profile_id,
        "provider": provider,
        "site_id": site_id,
        "drive_id": drive_id,
        "item_id": item_id,
        "web_url": web_url,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "uploaded_at": uploaded_at,
        "uploaded_by": uploaded_by,
        "initiated_by": initiated_by,
        "exp": int(time.time()) + _RETRY_TOKEN_MAX_AGE_S,
    }
    return jwt.encode(claims, config.SESSION_SECRET, algorithm="HS256")


def verify_commit_retry_token(token: str) -> dict:
    return _verify_token(token, _COMMIT_RETRY_PURPOSE)


# ---------------------------------------------------------------------------
# Orquestación de alto nivel — lo único que el router llama.
# ---------------------------------------------------------------------------

def _commit_uploaded_document(
    *, document_id: str, profile_id: str, drive_item: dict, filename: str, content_type: str,
    size_bytes: int, uploaded_at: str, uploaded_by: str,
) -> dict:
    fields = {
        "profile_id": profile_id,
        "provider": "sharepoint",
        "site_id": config.HC_SHAREPOINT_SITE_ID,
        "drive_id": config.HC_SHAREPOINT_DRIVE_ID,
        "item_id": drive_item.get("id"),
        "web_url": drive_item.get("webUrl"),
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "uploaded_at": uploaded_at,
        "uploaded_by": uploaded_by,
    }
    try:
        document, _created = profile_document_repository.create_with_explicit_id(document_id, fields)
        return document
    except profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError:
        retry_token = sign_commit_retry_token(
            document_id=document_id, profile_id=profile_id, provider="sharepoint",
            site_id=config.HC_SHAREPOINT_SITE_ID, drive_id=config.HC_SHAREPOINT_DRIVE_ID,
            item_id=drive_item.get("id"), web_url=drive_item.get("webUrl"), filename=filename,
            content_type=content_type, size_bytes=size_bytes, uploaded_at=uploaded_at, uploaded_by=uploaded_by,
            initiated_by=uploaded_by,
        )
        raise ProfileDocumentCommitRetryRequired(retry_token) from None


def upload_document(*, profile_id: str, filename: str, data: bytes, access_token: str, uploaded_by: str) -> dict:
    """Camino de alta de UN documento nuevo (§6-§15/§20). Orden estricto:
    validación de formato → config de Graph → ensure-folder → mint
    `document_id` → upload con reintento → commit de metadata (con su
    propio camino de reintento, ver `_commit_uploaded_document`)."""
    _ext, content_type = profile_document_validation.validate_upload(filename, data)
    require_graph_config()
    folder_item_id = ensure_profile_folder(profile_id, access_token)
    document_id = str(uuid4())
    remote_filename = build_remote_filename(document_id, filename)
    sha256 = hashlib.sha256(data).hexdigest()
    uploaded_at = _now_iso()
    try:
        drive_item = upload_document_content(
            upload_content_url(folder_item_id, remote_filename), access_token, data, content_type,
        )
    except ProfileDocumentRemoteUploadUnknownError:
        retry_token = sign_upload_retry_token(
            document_id=document_id, profile_id=profile_id, folder_item_id=folder_item_id,
            filename=remote_filename, original_filename=filename, content_type=content_type,
            size_bytes=len(data), sha256=sha256, initiated_by=uploaded_by,
        )
        raise ProfileDocumentUploadRetryRequired(retry_token) from None
    return _commit_uploaded_document(
        document_id=document_id, profile_id=profile_id, drive_item=drive_item, filename=filename,
        content_type=content_type, size_bytes=len(data), uploaded_at=uploaded_at, uploaded_by=uploaded_by,
    )


def retry_upload_document(
    *, token: str, data: bytes, filename: str, access_token: str, requesting_user_sub: str,
) -> dict:
    """Camino de reintento de upload (§16): el usuario re-selecciona el
    archivo, se revalida TODO de nuevo, y el SHA-256 tiene que coincidir
    EXACTO con el original antes de reintentar el MISMO PUT (mismo
    `document_id`/path — nunca un id nuevo)."""
    claims = verify_upload_retry_token(token)
    if claims.get("initiated_by") != requesting_user_sub:
        raise RetryTokenError("Este reintento pertenece a otro usuario.")
    _ext, content_type = profile_document_validation.validate_upload(filename, data)
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != claims.get("sha256"):
        raise ProfileDocumentRetryFileMismatchError(
            "El archivo elegido no coincide con el original. Elegí el mismo archivo para reintentar."
        )
    uploaded_at = _now_iso()
    try:
        drive_item = upload_document_content(
            upload_content_url(claims["folder_item_id"], claims["filename"]), access_token, data, content_type,
        )
    except ProfileDocumentRemoteUploadUnknownError:
        retry_token = sign_upload_retry_token(
            document_id=claims["document_id"], profile_id=claims["profile_id"],
            folder_item_id=claims["folder_item_id"], filename=claims["filename"],
            original_filename=claims["original_filename"], content_type=content_type,
            size_bytes=len(data), sha256=sha256, initiated_by=requesting_user_sub,
        )
        raise ProfileDocumentUploadRetryRequired(retry_token) from None
    return _commit_uploaded_document(
        document_id=claims["document_id"], profile_id=claims["profile_id"], drive_item=drive_item,
        filename=claims["original_filename"], content_type=content_type, size_bytes=len(data),
        uploaded_at=uploaded_at, uploaded_by=requesting_user_sub,
    )


def retry_commit_document(*, token: str, requesting_user_sub: str) -> tuple[dict, bool]:
    """Camino de reintento de commit de metadata (§17) — ZERO PUT nuevo a
    Graph, solo reintenta la persistencia con los MISMOS campos canónicos
    que ya viajan en el token. Devuelve `(document, created)` —
    `created=True` CREATED, `created=False` ALREADY_COMMITTED, MISMO
    contrato que `profile_document_repository.create_with_explicit_id`."""
    claims = verify_commit_retry_token(token)
    if claims.get("initiated_by") != requesting_user_sub:
        raise RetryTokenError("Este reintento pertenece a otro usuario.")
    fields = {
        "profile_id": claims["profile_id"],
        "provider": claims["provider"],
        "site_id": claims["site_id"],
        "drive_id": claims["drive_id"],
        "item_id": claims["item_id"],
        "web_url": claims["web_url"],
        "filename": claims["filename"],
        "content_type": claims["content_type"],
        "size_bytes": claims["size_bytes"],
        "uploaded_at": claims["uploaded_at"],
        "uploaded_by": claims["uploaded_by"],
    }
    try:
        return profile_document_repository.create_with_explicit_id(claims["document_id"], fields)
    except profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError:
        retry_token = sign_commit_retry_token(
            document_id=claims["document_id"], profile_id=claims["profile_id"], provider=claims["provider"],
            site_id=claims["site_id"], drive_id=claims["drive_id"], item_id=claims["item_id"],
            web_url=claims["web_url"], filename=claims["filename"], content_type=claims["content_type"],
            size_bytes=claims["size_bytes"], uploaded_at=claims["uploaded_at"], uploaded_by=claims["uploaded_by"],
            initiated_by=requesting_user_sub,
        )
        raise ProfileDocumentCommitRetryRequired(retry_token) from None


def list_documents_for_profile(profile_id: str) -> list[dict]:
    return profile_document_repository.list_for_profile(profile_id)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
