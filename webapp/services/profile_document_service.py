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

IDENTIDAD DE CARPETA/ARCHIVO (§8, ACTUALIZADO — convención legible, ver HANDOFF «Carpetas legibles de candidatos»): las carpetas NUEVAS se llaman
`Apellido, Nombre [id8]` (`<CV root>/Apellido, Nombre [a1b2c3d4]`); el `[id8]` (primeros 8 caracteres del `profile_id`) es INFORMATIVO: NO es la identidad. La identidad
estable de la carpeta es su `driveItem.id`, que se persiste completo en el campo OPCIONAL `folder_item_id` de cada ProfileDocument. Las carpetas históricas `<CV root>/<profile_id>`
(UUID) siguen siendo válidas y NUNCA se renombran; una carpeta tampoco se renombra automáticamente cuando cambian el nombre o el apellido del candidato (los documentos nuevos siguen yendo
a la carpeta existente por id). Antes de reutilizar una carpeta se VERIFICA que corresponde al candidato: es una carpeta, cuelga directo de la raíz de CVs, su nombre es el legible actual,
el UUID heredado o termina con el marcador `[id8]` de ESE candidato, y ningún documento de OTRO candidato la referencia. Si no se verifica, no se reutiliza (se sigue la resolución por
ruta o se crea una carpeta nueva; nunca se escribe en una carpeta no verificada). El archivo remoto sigue siendo `<document_id>__<nombre-sanitizado>` — nunca el nombre original solo,
para que dos uploads del mismo nombre de archivo (p. ej. "CV.pdf" subido por dos personas distintas) nunca colisionen de path en la MISMA carpeta.

EXPOSICIÓN DE DATOS PERSONALES (decisión explícita): el nombre y apellido del candidato pasan a formar parte del NOMBRE DE CARPETA y, por lo tanto, de la ruta y de los `web_url` de SharePoint:
son visibles en la navegación de SharePoint (intencional, es el objetivo), en los links/URLs, en el historial del navegador, en el índice de búsqueda y en la auditoría del tenant, y se espejan en
el `web_url` de la metadata local/Spaces. NO se escriben en logs técnicos de la aplicación (`sharepoint_graph_diagnostics.sanitize_endpoint` enmascara los segmentos de nombre). Un cambio de
nombre del candidato NO se propaga a la carpeta (no hay renombrado automático): la rectificación del nombre en SharePoint es una acción manual.

FOLDER-ENSURE (§10): resolución por candidato, en este orden (sin renombrar nunca): (1) `folder_item_id` guardado en sus documentos (se verifica por id); (2) lookup por PATH relativo
al item raíz (`.../items/{root}:/{nombre}`, sintaxis estándar de Graph) con el nombre legible actual y con el UUID heredado (el UUID primero si el candidato ya tiene documentos sin
`folder_item_id`); (3) ausente → POST a `.../items/{root}/children` con `conflictBehavior=fail` (nunca `rename`/`replace`: un 409 de una carrera concurrente se RESUELVE re-leyendo la
carpeta por el MISMO nombre — que se calcula UNA sola vez por operación — y verificándola). Sin nombres de candidato (`first_name`/`last_name` ausentes) rige el contrato original:
carpeta `<CV root>/<profile_id>`. El `item_id` de la carpeta NO se guarda en el Profile: vive en cada ProfileDocument (`folder_item_id`, opcional).

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
import unicodedata
import urllib.error
import urllib.parse
from uuid import uuid4

from jose import JWTError, jwt

import config
from core import microsoft365
from webapp.services import profile_document_repository, profile_document_validation, sharepoint_graph_diagnostics

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_logger = logging.getLogger("danwor.cchh.profile_document")      # sin nombres de candidatos en ningún mensaje

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


class ProfileDocumentsBulkDeleteIncompleteError(Exception):
    """La eliminación de TODOS los documentos de un candidato no se completó: `deleted` documentos confirmados (físico + metadata), `remaining` sin confirmar. `kind` = causa del primer
    fallo (`permission` | `graph` | `physical_unknown` | `metadata_unknown`). El candidato NO se da de baja; el reintento es idempotente (solo procesa lo que queda)."""

    def __init__(self, message: str, *, kind: str, deleted: int, remaining: int):
        super().__init__(message)
        self.kind, self.deleted, self.remaining = kind, deleted, remaining


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


FOLDER_DELETED, FOLDER_ABSENT, FOLDER_NOT_REQUESTED = "deleted", "already_absent", "not_requested"
FOLDER_KEPT_PREFIX = "kept_"          # kept_has_content | kept_shared_metadata | kept_unverified | kept_permission | kept_error


def cleanup_empty_profile_folder(*, profile_id: str, folder_item_id: str | None, site_id: str | None, drive_id: str | None, access_token: str) -> str:
    """Elimina la carpeta SharePoint de un candidato SOLO si es seguro, e IDEMPOTENTE. NUNCA lanza ni toca la metadata (el llamador ya la resolvió): un resultado incierto = la carpeta se
    CONSERVA. Orden de verificación (cualquier duda corta sin borrar):
      1. identidad guardada: el site/drive del documento deben ser los de la config vigente (los helpers de lectura usan la config);
      2. ningún ProfileDocument (de este ni de otro candidato) referencia todavía la carpeta;
      3. por Graph: la carpeta existe (404 => `already_absent`), es carpeta, cuelga DIRECTO de la raíz de CVs y su nombre es el legible `[id8]` de ESTE candidato o el UUID heredado;
      4. por Graph: sus `children` están vacíos (sin archivos ni subcarpetas ni paginación pendiente);
      5. DELETE de la carpeta (404 => ya ausente; 401/403 => `kept_permission`; otro fallo/transporte => `kept_error`).
    Devuelve un estado: `deleted` | `already_absent` | `kept_*` | `not_requested`."""
    if not folder_item_id:
        return FOLDER_NOT_REQUESTED
    if site_id != config.HC_SHAREPOINT_SITE_ID or drive_id != config.HC_SHAREPOINT_DRIVE_ID:
        return FOLDER_KEPT_PREFIX + "unverified"
    try:
        if profile_document_repository.find_by_folder_item_id(folder_item_id):
            return FOLDER_KEPT_PREFIX + "shared_metadata"
    except profile_document_repository.ProfileDocumentStorageError:
        return FOLDER_KEPT_PREFIX + "unverified"
    try:
        item = _get_folder_by_id(folder_item_id, access_token)
    except ProfileDocumentPermissionDeniedError:
        return FOLDER_KEPT_PREFIX + "permission"
    except ProfileDocumentGraphSourceError:
        return FOLDER_KEPT_PREFIX + "error"
    if item is None:
        return FOLDER_ABSENT
    if not _folder_matches(item, profile_id, {str(profile_id)}):
        return FOLDER_KEPT_PREFIX + "unverified"
    children_url = f"{_drive_items_base()}/{urllib.parse.quote(folder_item_id, safe='')}/children?$select=id,name&$top=2"
    try:
        listing = microsoft365.graph_get_json(children_url, access_token)
    except urllib.error.HTTPError as exc:
        return FOLDER_KEPT_PREFIX + ("permission" if exc.code in (401, 403) else "error")
    except urllib.error.URLError:
        return FOLDER_KEPT_PREFIX + "error"
    if not isinstance(listing, dict) or not isinstance(listing.get("value"), list):
        return FOLDER_KEPT_PREFIX + "unverified"                       # respuesta irreconocible: incierto
    if listing["value"] or listing.get("@odata.nextLink"):
        return FOLDER_KEPT_PREFIX + "has_content"
    try:
        microsoft365.graph_delete(_document_item_url(site_id, drive_id, folder_item_id), access_token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return FOLDER_ABSENT
        return FOLDER_KEPT_PREFIX + ("permission" if exc.code in (401, 403) else "error")
    except urllib.error.URLError:
        return FOLDER_KEPT_PREFIX + "error"                            # ambiguo: puede haberse borrado; un reintento lo resuelve (404 => ya ausente)
    return FOLDER_DELETED


def delete_document(*, profile_id: str, document_id: str, access_token: str, remove_empty_folder: bool = False) -> dict:
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
    # La metadata ya está resuelta: la limpieza de la carpeta es un paso POSTERIOR y best-effort (nunca compromete la consistencia ni lanza). Si la carpeta no se puede borrar con certeza, se conserva.
    folder = FOLDER_NOT_REQUESTED
    if remove_empty_folder:
        folder = cleanup_empty_profile_folder(profile_id=profile_id, folder_item_id=document.get("folder_item_id"), site_id=document.get("site_id"),
                                              drive_id=document.get("drive_id"), access_token=access_token)
    return {"folder": folder}


def delete_profile_documents(*, profile_id: str, access_token: str) -> dict:
    """Elimina FÍSICAMENTE (SharePoint) y luego la metadata de TODOS los documentos activos de un candidato, y por último la carpeta si quedó realmente vacía. Cada documento sigue el MISMO camino
    estricto de `delete_document` (físico confirmado antes que metadata). Al primer documento que no se confirma se corta con `ProfileDocumentsBulkDeleteIncompleteError` (cuántos se borraron y
    cuántos quedan): el llamador NO da de baja al candidato. Idempotente: un reintento procesa solo lo que queda. Devuelve `{"deleted": n, "folders": {estado,...}}`."""
    require_graph_config()
    docs = profile_document_repository.list_for_profile(profile_id)
    total, deleted, folders = len(docs), 0, {}
    for doc in docs:
        try:
            delete_document(profile_id=profile_id, document_id=doc["document_id"], access_token=access_token)
        except ProfileDocumentDeletePermissionDeniedError as exc:
            raise ProfileDocumentsBulkDeleteIncompleteError(str(exc), kind="permission", deleted=deleted, remaining=total - deleted) from exc
        except ProfileDocumentDeleteGraphSourceError as exc:
            raise ProfileDocumentsBulkDeleteIncompleteError(str(exc), kind="graph", deleted=deleted, remaining=total - deleted) from exc
        except ProfileDocumentDeletePhysicalOutcomeUnknownError as exc:
            raise ProfileDocumentsBulkDeleteIncompleteError(str(exc), kind="physical_unknown", deleted=deleted, remaining=total - deleted) from exc
        except ProfileDocumentMetadataDeleteOutcomeUnknownError as exc:
            raise ProfileDocumentsBulkDeleteIncompleteError(str(exc), kind="metadata_unknown", deleted=deleted, remaining=total - deleted) from exc
        except ProfileDocumentNotFoundError:
            continue                                                    # ya borrado por otro camino (idempotente)
        deleted += 1
        folders[doc.get("folder_item_id")] = (doc.get("site_id"), doc.get("drive_id"))
    results = {}
    for folder_item_id, (site_id, drive_id) in folders.items():
        results[folder_item_id] = cleanup_empty_profile_folder(profile_id=profile_id, folder_item_id=folder_item_id, site_id=site_id, drive_id=drive_id, access_token=access_token)
    return {"deleted": deleted, "folders": results}


# ---------------------------------------------------------------------------
# URLs de Graph (identidad ESTABLE vía config, nunca una URL de sharing).
# ---------------------------------------------------------------------------

def _drive_items_base(site_id: str | None = None, drive_id: str | None = None) -> str:
    """Por defecto site/drive de la CONFIG; los parametros opcionales existen solo para la prueba de escritura de diagnostico (destino de control), nunca para el uploader."""
    return f"{GRAPH_BASE}/sites/{site_id or config.HC_SHAREPOINT_SITE_ID}/drives/{drive_id or config.HC_SHAREPOINT_DRIVE_ID}/items"


def _folder_by_path_url(folder_name: str) -> str:
    # el nombre (legible: espacios, coma, corchetes, acentos) se codifica como UN segmento de path; los UUID/ids no cambian
    return f"{_drive_items_base()}/{config.HC_CV_ROOT_ITEM_ID}:/{urllib.parse.quote(folder_name, safe='')}"


def _folder_by_id_url(folder_item_id: str) -> str:
    return f"{_drive_items_base()}/{urllib.parse.quote(folder_item_id, safe='')}?$select=id,name,folder,parentReference"


def _cv_root_children_url() -> str:
    return f"{_drive_items_base()}/{config.HC_CV_ROOT_ITEM_ID}/children"


def upload_content_url(folder_item_id: str, remote_filename: str, *, site_id: str | None = None, drive_id: str | None = None) -> str:
    return f"{_drive_items_base(site_id, drive_id)}/{folder_item_id}:/{remote_filename}:/content"


def _folder_create_body(folder_name: str) -> dict:
    # `conflictBehavior=fail` a propósito (§10): un 409 se maneja
    # explícitamente re-leyendo la carpeta existente — NUNCA se delega a
    # Graph un rename/replace automático que crearía una carpeta con
    # nombre distinto al determinístico esperado.
    return {"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}


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

# Convención legible `Apellido, Nombre [id8]`. Se eliminan los caracteres que SharePoint rechaza o que complican el direccionamiento por path (`" * : < > ? / \\ | # % ~ { }` y controles),
# se colapsan espacios, se quitan puntos/espacios finales y se acota cada parte. NO se cambia mayúsculas/acentos (el nombre se ve como se cargó).
_FOLDER_FORBIDDEN = re.compile(r'["*:<>?/\\|#%~{}\x00-\x1f\x7f]+')
_FOLDER_NAME_PART_MAX = 60


def _clean_name_part(value) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _FOLDER_FORBIDDEN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:_FOLDER_NAME_PART_MAX].strip(" .")


def folder_marker(profile_id: str) -> str:
    """`id8`: primeros 8 caracteres del profile_id (sin guiones). INFORMATIVO — la identidad de la carpeta es su `driveItem.id`, nunca este marcador."""
    return _FOLDER_FORBIDDEN.sub("", str(profile_id)).replace("-", "")[:8]


def build_folder_name(profile_id: str, first_name, last_name) -> str | None:
    """`Apellido, Nombre [id8]`; `None` si no hay ni nombre ni apellido (se usa entonces la carpeta UUID del contrato original)."""
    last, first = _clean_name_part(last_name), _clean_name_part(first_name)
    if not (last or first):
        return None
    base = f"{last}, {first}" if last and first else (last or first)
    return f"{base} [{folder_marker(profile_id)}]"


def _lookup_folder_by_path(folder_name: str, access_token: str) -> dict | None:
    """GET por path bajo la raíz. `None` = 404 CONFIRMADO (ausente). 403 → permission denied; cualquier otro fallo → GraphSourceError (un fallo de lectura NUNCA es ambiguo sobre «¿se creó algo?»:
    todavía no se intentó escribir nada)."""
    url = _folder_by_path_url(folder_name)
    try:
        return microsoft365.graph_get_json(url, access_token)
    except urllib.error.HTTPError as exc:
        # Diagnóstico (sólo observabilidad): el cuerpo real de Graph (code/message/innerError). Un 404 es el caso NORMAL «carpeta aún no existe» → INFO. `sanitize_endpoint` enmascara el nombre.
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.lookup", "GET", url, exc, level=logging.INFO if exc.code == 404 else logging.WARNING)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        if exc.code != 404:
            raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
        return None
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.lookup", "GET", url, exc)
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc


def _get_folder_by_id(folder_item_id: str, access_token: str) -> dict | None:
    """GET por id de una carpeta guardada en un documento. `None` = 404 (el id quedó obsoleto: se sigue por ruta). Otros fallos NO se interpretan como «no existe»: se propagan para no crear una
    carpeta duplicada ante un error transitorio."""
    url = _folder_by_id_url(folder_item_id)
    try:
        return microsoft365.graph_get_json(url, access_token)
    except urllib.error.HTTPError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.verify_by_id", "GET", url, exc, level=logging.INFO if exc.code == 404 else logging.WARNING)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        if exc.code != 404:
            raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
        return None
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.verify_by_id", "GET", url, exc)
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc


def _claimed_by_other_profile(folder_item_id: str, profile_id: str) -> bool:
    try:
        return any(d.get("profile_id") != profile_id for d in profile_document_repository.find_by_folder_item_id(folder_item_id))
    except profile_document_repository.ProfileDocumentStorageError:
        return True        # fail-closed: si no se puede comprobar, no se reutiliza


def _folder_matches(item, profile_id: str, accepted_names: set) -> bool:
    """¿La carpeta corresponde a ESTE candidato? Es carpeta, cuelga DIRECTO de la raíz de CVs, su nombre es uno de los esperados (legible actual / UUID heredado) o termina con el marcador `[id8]`
    de este candidato, y ningún documento de OTRO candidato la referencia. El marcador es una verificación de cordura, no la identidad."""
    if not isinstance(item, dict) or not item.get("id") or not isinstance(item.get("folder"), dict):
        return False
    if (item.get("parentReference") or {}).get("id") != config.HC_CV_ROOT_ITEM_ID:
        return False
    name = str(item.get("name") or "")
    marker = f"[{folder_marker(profile_id)}]".casefold()
    if name.casefold() not in {n.casefold() for n in accepted_names} and not name.casefold().endswith(marker):
        return False
    return not _claimed_by_other_profile(str(item["id"]), profile_id)


def _ensure_named_folder(profile_id: str, folder_name: str, access_token: str) -> str:
    try:
        docs = profile_document_repository.list_for_profile(profile_id)
    except profile_document_repository.ProfileDocumentStorageError:
        docs = []
    docs = sorted(docs, key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)
    accepted = {folder_name, profile_id}
    seen: set[str] = set()
    for doc in docs:                                                   # (1) id guardado en sus documentos
        folder_item_id = doc.get("folder_item_id")
        if not folder_item_id or folder_item_id in seen:
            continue
        seen.add(folder_item_id)
        item = _get_folder_by_id(folder_item_id, access_token)
        if _folder_matches(item, profile_id, accepted):
            return item["id"]
        _logger.info("human_capital.profile_folder_unverified via=stored_id")        # sin nombres: id obsoleto o que no corresponde -> ruta
    has_legacy_docs = any(not d.get("folder_item_id") for d in docs)
    for name in ((profile_id, folder_name) if has_legacy_docs else (folder_name, profile_id)):   # (2) por ruta: UUID heredado y nombre legible
        item = _lookup_folder_by_path(name, access_token)
        if _folder_matches(item, profile_id, accepted):
            return item["id"]
    return _create_folder_with_retry(folder_name, access_token, verify=lambda item: _folder_matches(item, profile_id, accepted))     # (3) crear


def ensure_profile_folder(profile_id: str, access_token: str, *, first_name=None, last_name=None) -> str:
    """Devuelve el `item_id` de la carpeta del candidato, creándola si hace falta (resolución en el docstring del módulo). Sin `first_name`/`last_name` (o ambos vacíos) rige el contrato
    ORIGINAL: carpeta `<CV root>/<profile_id>`, nombre determinístico, GET por path (200 existe / 404 ausente), POST con `conflictBehavior=fail`, un único reintento ante ambigüedad."""
    folder_name = build_folder_name(profile_id, first_name, last_name) if (first_name is not None or last_name is not None) else None
    if folder_name is not None:
        return _ensure_named_folder(profile_id, folder_name, access_token)
    existing = _lookup_folder_by_path(profile_id, access_token)
    if existing is not None:
        return existing["id"]
    return _create_folder_with_retry(profile_id, access_token)


def _attempt_create_folder(folder_name: str, access_token: str) -> tuple[str | None, bool]:
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
        created = microsoft365.graph_post_json(url, access_token, _folder_create_body(folder_name))
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


def _reread_folder_after_conflict(folder_name: str, access_token: str, verify=None) -> str:
    """Re-lee tras un 409 CONFIRMADO — en ese punto ya se sabe que la
    carpeta existe (la creó otro request), así que un fallo de transporte
    ACÁ es un problema de LECTURA pura, no de "¿se creó algo?" — se
    clasifica de inmediato (403 → permission denied, cualquier otra cosa →
    GRAPH_SOURCE_ERROR), sin un tercer intento de creación ni un retry
    anidado. Con `verify` (convención legible) la carpeta releída se VERIFICA: si no corresponde
    al candidato NO se usa (GRAPH_SOURCE_ERROR)."""
    url = _folder_by_path_url(folder_name)
    try:
        existing = microsoft365.graph_get_json(url, access_token)
    except urllib.error.HTTPError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.reread_after_conflict", "GET", url, exc)
        if exc.code == 403:
            raise ProfileDocumentPermissionDeniedError(_PERMISSION_DENIED_MESSAGE) from exc
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
    except urllib.error.URLError as exc:
        sharepoint_graph_diagnostics.log_graph_failure("ensure_folder.reread_after_conflict", "GET", url, exc)
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE) from exc
    if verify is not None and not verify(existing):
        _logger.warning("human_capital.profile_folder_conflict_unverified")
        raise ProfileDocumentGraphSourceError(_GRAPH_SOURCE_ERROR_MESSAGE)
    return existing["id"]


def _create_folder_with_retry(folder_name: str, access_token: str, verify=None) -> str:
    """Contrato de creación con UN único reintento ante ambigüedad (ticket
    §9) — MISMO nombre/payload/parent en AMBOS intentos (el nombre se calcula UNA vez por operación),
    nunca una segunda carpeta con nombre distinto. Ambos intentos ambiguos (ninguna
    respuesta HTTP confirmada en ninguno) → `ProfileDocumentFolderOutcomeUnknownError`."""
    item_id, confirmed = _attempt_create_folder(folder_name, access_token)
    if confirmed:
        return item_id if item_id is not None else _reread_folder_after_conflict(folder_name, access_token, verify)

    item_id, confirmed = _attempt_create_folder(folder_name, access_token)
    if confirmed:
        return item_id if item_id is not None else _reread_folder_after_conflict(folder_name, access_token, verify)

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
    uploaded_by: str, initiated_by: str, folder_item_id: str | None = None,
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
    if folder_item_id:
        claims["folder_item_id"] = folder_item_id
    return jwt.encode(claims, config.SESSION_SECRET, algorithm="HS256")


def verify_commit_retry_token(token: str) -> dict:
    return _verify_token(token, _COMMIT_RETRY_PURPOSE)


# ---------------------------------------------------------------------------
# Orquestación de alto nivel — lo único que el router llama.
# ---------------------------------------------------------------------------

def _commit_uploaded_document(
    *, document_id: str, profile_id: str, drive_item: dict, filename: str, content_type: str,
    size_bytes: int, uploaded_at: str, uploaded_by: str, folder_item_id: str | None = None,
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
    if folder_item_id:
        fields["folder_item_id"] = folder_item_id     # campo OPCIONAL (identidad estable de la carpeta, ver docstring del módulo); no es campo canónico
    try:
        document, _created = profile_document_repository.create_with_explicit_id(document_id, fields)
        return document
    except profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError:
        retry_token = sign_commit_retry_token(
            document_id=document_id, profile_id=profile_id, provider="sharepoint",
            site_id=config.HC_SHAREPOINT_SITE_ID, drive_id=config.HC_SHAREPOINT_DRIVE_ID,
            item_id=drive_item.get("id"), web_url=drive_item.get("webUrl"), filename=filename,
            content_type=content_type, size_bytes=size_bytes, uploaded_at=uploaded_at, uploaded_by=uploaded_by,
            initiated_by=uploaded_by, folder_item_id=folder_item_id,
        )
        raise ProfileDocumentCommitRetryRequired(retry_token) from None


def upload_document(*, profile_id: str, filename: str, data: bytes, access_token: str, uploaded_by: str, first_name=None, last_name=None) -> dict:
    """Camino de alta de UN documento nuevo (§6-§15/§20). Orden estricto:
    validación de formato → config de Graph → ensure-folder → mint
    `document_id` → upload con reintento → commit de metadata (con su
    propio camino de reintento, ver `_commit_uploaded_document`)."""
    _ext, content_type = profile_document_validation.validate_upload(filename, data)
    require_graph_config()
    folder_item_id = ensure_profile_folder(profile_id, access_token, first_name=first_name, last_name=last_name)
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
        folder_item_id=folder_item_id,
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
        uploaded_at=uploaded_at, uploaded_by=requesting_user_sub, folder_item_id=claims.get("folder_item_id"),
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
    if claims.get("folder_item_id"):
        fields["folder_item_id"] = claims["folder_item_id"]
    try:
        return profile_document_repository.create_with_explicit_id(claims["document_id"], fields)
    except profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError:
        retry_token = sign_commit_retry_token(
            document_id=claims["document_id"], profile_id=claims["profile_id"], provider=claims["provider"],
            site_id=claims["site_id"], drive_id=claims["drive_id"], item_id=claims["item_id"],
            web_url=claims["web_url"], filename=claims["filename"], content_type=claims["content_type"],
            size_bytes=claims["size_bytes"], uploaded_at=claims["uploaded_at"], uploaded_by=claims["uploaded_by"],
            initiated_by=requesting_user_sub, folder_item_id=claims.get("folder_item_id"),
        )
        raise ProfileDocumentCommitRetryRequired(retry_token) from None


def count_documents_by_profile() -> dict[str, int]:
    """Documentos activos por candidato (para el diálogo contextual de baja del listado). Fail-soft: si no se puede leer, `{}` (el diálogo cae al texto sin documentos solo cuando de verdad no hay)."""
    try:
        return profile_document_repository.count_by_profile()
    except profile_document_repository.ProfileDocumentStorageError:
        return {}


def list_documents_for_profile(profile_id: str) -> list[dict]:
    return profile_document_repository.list_for_profile(profile_id)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
